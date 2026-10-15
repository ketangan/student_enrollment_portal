"""Opt-in negotiated platform billing. Parent/school-merchant payments are not involved."""
import logging
import re
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import School, SchoolBillingCheckout, SchoolBillingOffer, SchoolBillingLocationChange
from core.services import feature_flags

logger = logging.getLogger(__name__)


def uses_school_pricing(school):
    # Presence in either mode opts out of legacy prices, including when this mode is missing.
    return school.billing_offers.exists()


def current_offer(school):
    return school.billing_offers.filter(mode=settings.STRIPE_MODE, is_current=True).first()


def accepted_location_change(checkout):
    return checkout.location_changes.filter(state="applied").select_related("offer", "previous_offer").order_by("-created_at").first()


def _matches_items(subscription, offer):
    items = subscription.get("items") or {}
    actual = [{"price": item.get("price", {}).get("id"), "quantity": item.get("quantity")}
              for item in items.get("data", [])]
    return not items.get("has_more") and sorted(actual, key=lambda x: x["price"] or "") == sorted(offer.line_items, key=lambda x: x["price"])


def _stripe():
    from core.services.billing_stripe import _get_stripe
    sdk = _get_stripe()
    key = settings.STRIPE_SECRET_KEY.strip()
    prefix = "sk_live_" if settings.STRIPE_MODE == "live" else "sk_test_"
    restricted = prefix.replace("sk_", "rk_", 1)
    if sdk is None or settings.STRIPE_MODE not in ("test", "live") or not key.startswith((prefix, restricted)):
        raise ValidationError("Stripe credentials do not match this billing environment.")
    return sdk, key


def _data(value):
    from core.services.billing_stripe import _stripe_object_to_dict
    return _stripe_object_to_dict(value)


def _read_price(sdk, key, price_id):
    if not re.fullmatch(r"price_[A-Za-z0-9_]+", price_id):
        raise ValidationError("Enter a Stripe Price ID starting with price_, not a product or payment link.")
    price = _data(sdk.Price.retrieve(price_id, expand=["product"], api_key=key))
    recurring = price.get("recurring") or {}
    product = price.get("product") or {}
    amount = price.get("unit_amount")
    if (price.get("id") != price_id or price.get("livemode") is not (settings.STRIPE_MODE == "live")
            or not price.get("active") or not isinstance(product, dict) or not product.get("active")
            or price.get("type") != "recurring" or recurring.get("usage_type") != "licensed"
            or recurring.get("interval") not in ("month", "year") or recurring.get("interval_count") != 1
            or price.get("billing_scheme") != "per_unit" or price.get("transform_quantity")
            or price.get("currency") != "usd" or type(amount) is not int or amount <= 0
            or price.get("currency_options")):
        raise ValidationError("Use an active USD recurring flat-rate price, billed monthly or annually, in this Stripe environment.")
    return {"id": price_id, "amount": amount, "currency": "usd", "interval": recurring["interval"],
            "display": f"${amount / 100:,.2f} / {recurring['interval']}"}


def validate_prices(base_id, location_id="", upgrade_id=""):
    sdk, key = _stripe()
    try:
        account = _data(sdk.Account.retrieve(api_key=key)).get("id")
        if not account:
            raise ValidationError("Stripe account could not be verified.")
        base = _read_price(sdk, key, base_id)
        location = _read_price(sdk, key, location_id) if location_id else {}
        upgrade = _read_price(sdk, key, upgrade_id) if upgrade_id else {}
        if any(p and p["interval"] != base["interval"] for p in (location, upgrade)):
            raise ValidationError("Base, location and upgrade prices must use the same billing period.")
        if location and location["id"] == base["id"]:
            raise ValidationError("Use a separate price for additional locations.")
        if upgrade and upgrade["amount"] <= base["amount"]:
            raise ValidationError("The future upgrade price must be higher than the base price.")
        return dict(account_id=account, base_price=base, location_price=location, upgrade_price=upgrade)
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("School price validation failed (%s)", type(exc).__name__)
        raise ValidationError("Could not verify these prices with Stripe. Nothing was saved; no default price will be substituted.") from exc


@transaction.atomic
def publish_offer(*, school, actor, label, included_locations, additional_locations, verified, expected_offer_id):
    School.objects.select_for_update().get(pk=school.pk)
    previous = current_offer(school)
    if str(previous.pk if previous else "") != expected_offer_id:
        raise ValidationError("Pricing changed in another window. Reload before saving.")
    if SchoolBillingLocationChange.objects.filter(checkout__offer__school=school, offer__mode=settings.STRIPE_MODE, state="pending").exists():
        raise ValidationError("Confirm the pending billing update before replacing this agreement. Use Retry billing update below.")
    pending = SchoolBillingCheckout.objects.filter(offer__school=school, offer__mode=settings.STRIPE_MODE,
                                                   state="pending").first()
    if pending:
        if (previous and previous.label == label and previous.included_locations == included_locations
                and previous.additional_locations == additional_locations
                and all(getattr(previous, field) == verified[field]
                        for field in ("account_id", "base_price", "location_price"))):
            return previous
        sdk, key = _stripe()
        try:
            session = _data(sdk.checkout.Session.retrieve(pending.session_id, api_key=key)) if pending.session_id else {}
        except Exception as exc:
            raise ValidationError("Could not verify the existing checkout. No agreement was changed.") from exc
        if session.get("status") == "expired":
            pending.state = "expired"
            pending.save(update_fields=["state"])
        else:
            created = timezone.localtime(pending.created_at).strftime("%b %d, %I:%M %p %Z")
            expires = timezone.localtime(pending.expires_at).strftime("%b %d, %I:%M %p %Z")
            raise ValidationError(
                f"Checkout {pending.pk} for {pending.offer.total_display}, started {created}, "
                f"is still awaiting completion or confirmation. Its scheduled expiry is {expires}. "
                "Closing the browser does not expire it. Finish or expire this checkout in Stripe before changing pricing."
            )
    if additional_locations and not verified["location_price"]:
        raise ValidationError("Additional locations require a location price.")
    if previous:
        previous.is_current = False
        previous.save(update_fields=["is_current"])
    offer = SchoolBillingOffer(school=school, mode=settings.STRIPE_MODE, created_by=actor,
                              label=label, included_locations=included_locations,
                              additional_locations=additional_locations, **verified)
    offer.full_clean()
    offer.save()
    return offer


def _verify_offer(offer):
    verified = validate_prices(offer.base_price["id"], offer.location_price.get("id", ""))
    if any(verified[field] != getattr(offer, field) for field in ("account_id", "base_price", "location_price")):
        raise ValidationError("The saved agreement no longer matches Stripe. Contact Pontora before paying.")


@transaction.atomic
def recover_checkout(*, school, checkout_id, expire=False):
    """Resolve one known checkout without replacing its price or creating a payment."""
    School.objects.select_for_update().get(pk=school.pk)
    try:
        attempt = SchoolBillingCheckout.objects.select_related("offer").get(
            pk=checkout_id, offer__school=school, offer__mode=settings.STRIPE_MODE)
    except (SchoolBillingCheckout.DoesNotExist, ValueError, ValidationError):
        raise ValidationError("Checkout not found for this school and billing environment.")
    if attempt.state != "pending":
        return "This checkout has already been resolved. Reload Billing to see its current status."
    if not attempt.session_id:
        raise ValidationError("Stripe has not returned a checkout reference. Nothing was canceled. Contact support to verify the original attempt.")
    sdk, key = _stripe()
    try:
        if _data(sdk.Account.retrieve(api_key=key)).get("id") != attempt.offer.account_id:
            raise ValidationError("The Stripe account has changed. Nothing was canceled.")
        session = _data(sdk.checkout.Session.retrieve(attempt.session_id, api_key=key))
        if (session.get("id") != attempt.session_id
                or session.get("livemode") is not (settings.STRIPE_MODE == "live")
                or session.get("mode") != "subscription"
                or (session.get("metadata") or {}).get("billing_checkout") != str(attempt.pk)):
            raise ValidationError("Stripe checkout does not match this record. Nothing was changed.")
        if expire and session.get("status") == "open" and session.get("payment_status") == "unpaid":
            try:
                sdk.checkout.Session.expire(attempt.session_id, api_key=key)
            except Exception:
                # Payment may finish while the operator clicks expire. Re-read, never assume cancellation.
                pass
            session = _data(sdk.checkout.Session.retrieve(attempt.session_id, api_key=key))
        if session.get("status") == "expired":
            attempt.state = "expired"
            attempt.save(update_fields=["state"])
            return "Checkout expired. You can now change the offer; the customer can start a new checkout from Billing."
        if session.get("status") == "complete":
            reconcile_checkout(school, session)
            attempt.refresh_from_db()
            if attempt.state == "complete":
                return "Subscription confirmed with Stripe. The customer can reload Billing; no second payment is needed."
            raise ValidationError("Checkout completed, but subscription confirmation is still pending. Do not ask the customer to pay again.")
        if session.get("status") == "open":
            if expire:
                raise ValidationError("Checkout could not be safely expired. It remains protected; check its status again before changing pricing.")
            return "Checkout is still open. The customer can continue from Billing, or you can expire the unpaid checkout below."
        raise ValidationError("Stripe returned an unrecognized checkout status. Nothing was changed.")
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("Checkout recovery failed for school %s (%s)", school.pk, type(exc).__name__)
        raise ValidationError("Could not confirm checkout with Stripe. Nothing was cleared; please check again.") from exc


def start_checkout(*, school, offer_id, success_url, cancel_url, customer_email=None):
    """Persist the request before calling Stripe; uncertain outcomes retry the same identity."""
    sdk, key = _stripe()
    with transaction.atomic():
        school = School.objects.select_for_update().get(pk=school.pk)
        if (school.subscription_checkout_paused or school.is_payment_overdue
                or school.has_active_stripe_subscription
                or (school.stripe_subscription_id and school.stripe_subscription_status not in ("canceled", "incomplete_expired"))):
            raise ValidationError("An existing subscription or trial must be resolved before starting checkout.")
        offer = current_offer(school)
        if not offer or str(offer.pk) != str(offer_id):
            raise ValidationError("This offer is unavailable or has changed. Reload Billing; no default price will be charged.")
        _verify_offer(offer)
        attempt = SchoolBillingCheckout.objects.filter(
            offer__school=school, offer__mode=settings.STRIPE_MODE, state="pending",
        ).order_by("created_at").first()
        if attempt and attempt.session_id:
            session = _data(sdk.checkout.Session.retrieve(attempt.session_id, api_key=key))
            if session.get("status") == "expired":
                attempt.state = "expired"
                attempt.save(update_fields=["state"])
                attempt = None
            elif session.get("status") == "complete":
                recover_checkout(school=school, checkout_id=attempt.pk)
                return success_url
            elif session.get("status") == "open":
                url = str(session.get("url", ""))
                if attempt.offer_id == offer.pk and url.startswith("https://checkout.stripe.com/"):
                    return url
        if attempt and attempt.offer_id != offer.pk:
            raise ValidationError("An earlier checkout is still open. Contact Pontora to reconcile it.")
        if not attempt:
            attempt = SchoolBillingCheckout.objects.create(offer=offer, expires_at=timezone.now() + timedelta(hours=1))
            metadata = {"school_slug": school.slug, "school_id": str(school.pk), "billing_checkout": str(attempt.pk)}
            attempt.parameters = {
                "mode": "subscription", "line_items": offer.line_items, "currency": "usd",
                "success_url": success_url, "cancel_url": cancel_url,
                "client_reference_id": str(school.pk), "metadata": metadata,
                "subscription_data": {"metadata": metadata},
                "expires_at": int(attempt.expires_at.timestamp()),
                "payment_method_types": ["card"], "allow_promotion_codes": False,
                "adaptive_pricing": {"enabled": False},
            }
            if customer_email:
                attempt.parameters["customer_email"] = customer_email
            attempt.save(update_fields=["parameters"])
    # No database transaction spans this write API call: a timeout must retain the retry key.
    if timezone.now() >= attempt.expires_at:
        raise ValidationError("Checkout could not be confirmed. Contact Pontora to reconcile it before retrying.")
    session = _data(sdk.checkout.Session.create(
        **attempt.parameters, api_key=key, idempotency_key=f"school-billing-{attempt.pk}",
    ))
    if not session.get("id") or not str(session.get("url", "")).startswith("https://checkout.stripe.com/"):
        raise ValidationError("Stripe did not return a valid checkout. No alternative price was used.")
    SchoolBillingCheckout.objects.filter(pk=attempt.pk).update(session_id=session["id"])
    return session["url"]


def validate_renewal_change(previous, offer):
    if offer.base_price["interval"] != previous.base_price["interval"]:
        raise ValidationError("Keep the same billing frequency. Monthly-to-annual and annual-to-monthly changes are not supported here.")
    if (offer.included_locations != previous.included_locations
            or (previous.location_price and offer.location_price != previous.location_price)
            or offer.additional_locations < previous.additional_locations):
        raise ValidationError("Keep included locations and the existing location rate unchanged. Location removals are not supported here.")
    if offer.line_items == previous.line_items:
        raise ValidationError("There is no price or location-count change to apply.")


def apply_billing_at_renewal(*, school, offer_id, actor, reviewed_offer_id=None, reviewed_renewal=None):
    """Replace base price and/or increase locations without proration or a new billing date."""
    sdk, key = _stripe()
    try:
        with transaction.atomic():
            school = School.objects.select_for_update().get(pk=school.pk)
            offer = current_offer(school)
            if not offer or str(offer.pk) != str(offer_id):
                raise ValidationError("The saved agreement changed. Reload before applying billing changes.")
            checkout = SchoolBillingCheckout.objects.select_related("offer").filter(
                offer__school=school, offer__mode=settings.STRIPE_MODE,
                subscription_id=school.stripe_subscription_id, state="complete").first()
            if not school.stripe_subscription_id or not checkout:
                raise ValidationError("This action requires an existing negotiated subscription. Legacy subscriptions are not changed.")
            _verify_offer(offer)
            if offer.account_id != checkout.offer.account_id:
                raise ValidationError("The subscription belongs to a different Stripe account.")
            sub = _data(sdk.Subscription.retrieve(school.stripe_subscription_id, api_key=key))
            if (sub.get("id") != school.stripe_subscription_id or sub.get("customer") != school.stripe_customer_id
                    or sub.get("livemode") is not (settings.STRIPE_MODE == "live")
                    or (sub.get("metadata") or {}).get("billing_checkout") != str(checkout.pk)):
                raise ValidationError("Stripe subscription does not match this school and environment.")
            pending = checkout.location_changes.filter(state="pending").first()
            latest = accepted_location_change(checkout)
            previous = latest.offer if latest else checkout.offer
            if pending and pending.offer_id != offer.pk:
                raise ValidationError("A previous billing update needs confirmation. Review that saved offer before retrying.")
            if latest and latest.offer_id == offer.pk and not pending:
                if not _matches_items(sub, latest.offer):
                    raise ValidationError("Stripe prices or quantities differ from the confirmed billing update. Reconcile billing before making another change.")
                return latest
            if reviewed_offer_id is not None and str(previous.pk) != str(reviewed_offer_id):
                raise ValidationError("The accepted subscription changed since your review. Reload and review the new totals.")
            if not pending:
                if (sub.get("status") != "active" or school.is_payment_overdue
                        or sub.get("cancel_at_period_end") or sub.get("cancel_at") or sub.get("schedule")
                        or sub.get("pause_collection") or sub.get("pending_update") or sub.get("billing_thresholds")
                        or sub.get("collection_method") != "charge_automatically"):
                    raise ValidationError("Resolve the subscription's payment, cancellation or scheduled changes before changing billing.")
                validate_renewal_change(previous, offer)
                if not _matches_items(sub, previous):
                    raise ValidationError("Stripe quantities differ from the accepted agreement. Reconcile billing before making another change.")
                items = sub["items"]["data"]
                if any(item.get("billing_thresholds") for item in items):
                    raise ValidationError("Subscriptions with billing thresholds need a separate review.")
                ends = {item.get("current_period_end") or sub.get("current_period_end") for item in items}
                if len(ends) != 1 or not next(iter(ends)):
                    raise ValidationError("Could not confirm one renewal date for this subscription.")
                effective_at = datetime.fromtimestamp(int(next(iter(ends))), tz=datetime_timezone.utc)
                if reviewed_renewal is not None and int(effective_at.timestamp()) != reviewed_renewal:
                    raise ValidationError("The renewal date changed since your review. Refresh Billing status and review again.")
                if effective_at <= timezone.now():
                    raise ValidationError("Renewal is in progress. Reload after it finishes before changing billing.")
                updates = []
                if offer.base_price["id"] != previous.base_price["id"]:
                    base_item = next(item for item in items if item["price"]["id"] == previous.base_price["id"])
                    updates.append({"id": base_item["id"], "price": offer.base_price["id"], "quantity": 1})
                if offer.additional_locations != previous.additional_locations:
                    location_item = next((item for item in items if item["price"]["id"] == offer.location_price["id"]), None)
                    item_update = {"quantity": offer.additional_locations}
                    item_update.update({"id": location_item["id"]} if location_item else {"price": offer.location_price["id"]})
                    updates.append(item_update)
                pending = SchoolBillingLocationChange(
                    checkout=checkout, offer=offer, previous_offer=previous,
                    effective_at=effective_at, created_by=actor)
                # Keep the original metadata/key format so in-flight location updates remain retryable.
                pending.parameters = {"items": updates, "proration_behavior": "none",
                                      "metadata": {"billing_location_change": str(pending.pk)}}
                pending.save()
        # Persist the retry identity before the Stripe write, including for timeouts.
        if (sub.get("metadata") or {}).get("billing_location_change") != str(pending.pk):
            if (sub.get("status") != "active" or school.is_payment_overdue
                    or sub.get("cancel_at_period_end") or sub.get("cancel_at") or sub.get("schedule")
                    or sub.get("pause_collection") or sub.get("pending_update") or sub.get("billing_thresholds")
                    or sub.get("collection_method") != "charge_automatically"
                    or any(item.get("billing_thresholds") for item in sub["items"]["data"])):
                raise ValidationError("The subscription changed while the update was pending. Review it in Stripe before retrying.")
            if not _matches_items(sub, pending.previous_offer):
                raise ValidationError("Stripe quantities changed while the update was pending. Review them before retrying.")
            if timezone.now() >= pending.effective_at:
                raise ValidationError("The renewal date passed while this update was unconfirmed. Check Stripe before retrying; no new adjustment was sent.")
            if {item.get("current_period_end") or sub.get("current_period_end") for item in sub["items"]["data"]} != {int(pending.effective_at.timestamp())}:
                raise ValidationError("The renewal date changed while the update was pending. Review it in Stripe before retrying.")
            sdk.Subscription.modify(checkout.subscription_id, **pending.parameters, api_key=key,
                                    idempotency_key=f"school-locations-{pending.pk}")
        from core.services.billing_stripe import handle_subscription_updated
        handle_subscription_updated({"id": checkout.subscription_id})
        pending.refresh_from_db()
        if pending.state != "applied":
            raise ValidationError("Stripe has not confirmed the billing update. Retry this same action; do not create another subscription.")
        return pending
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("Subscription billing update failed for school %s (%s)", school.pk, type(exc).__name__)
        raise ValidationError("The billing update could not be confirmed. Retry this same action to check safely; no second subscription will be created.") from exc


def apply_subscription(school, subscription):
    """Accept only subscriptions traceable to an immutable, school-owned checkout."""
    token = (subscription.get("metadata") or {}).get("billing_checkout")
    try:
        attempt = SchoolBillingCheckout.objects.select_related("offer").get(
            pk=token, offer__school=school, offer__mode=settings.STRIPE_MODE,
        )
    except (SchoolBillingCheckout.DoesNotExist, ValueError, ValidationError):
        return False
    sub_id = subscription.get("id")
    if (not sub_id or not subscription.get("customer")
            or subscription.get("livemode") is not (settings.STRIPE_MODE == "live")
            or (attempt.subscription_id and attempt.subscription_id != sub_id)
            or (school.stripe_subscription_id and school.stripe_subscription_id != sub_id
                and (attempt.subscription_id or school.stripe_subscription_status not in ("canceled", "incomplete_expired")))):
        return False
    change = None
    change_id = (subscription.get("metadata") or {}).get("billing_location_change")
    if change_id:
        try:
            change = attempt.location_changes.select_related("offer").get(pk=change_id)
        except (SchoolBillingLocationChange.DoesNotExist, ValueError, ValidationError):
            return False
    expected_offer = change.offer if change else attempt.offer
    if not _matches_items(subscription, expected_offer):
        logger.error("Negotiated subscription items do not match school %s's agreement", school.pk)
        return False
    if subscription.get("status") not in ("active", "trialing", "past_due", "unpaid", "canceled", "incomplete", "incomplete_expired", "paused"):
        return False
    if school.plan == feature_flags.PLAN_TRIAL and subscription["status"] in ("active", "trialing"):
        # Preserve effective access, including explicit exclusions, instead of inferring it from dollars.
        school.feature_flags = {**school.feature_flags, **feature_flags.merge_flags(plan=school.plan, overrides=school.feature_flags)}
        school.plan = feature_flags.PLAN_CUSTOM
    school.stripe_subscription_id = sub_id
    school.stripe_customer_id = subscription["customer"]
    attempt.subscription_id = sub_id
    attempt.state = "complete"
    attempt.save(update_fields=["subscription_id", "state"])
    if change and change.state != "applied":
        change.state = "applied"
        change.save(update_fields=["state"])
    return True


def reconcile_checkout(school, session):
    from core.services.billing_stripe import handle_subscription_updated, retrieve_billing_subscription
    token = (session.get("metadata") or {}).get("billing_checkout")
    try:
        attempt = SchoolBillingCheckout.objects.get(pk=token, offer__school=school, offer__mode=settings.STRIPE_MODE)
    except (SchoolBillingCheckout.DoesNotExist, ValueError, ValidationError):
        return
    if (session.get("livemode") is not (settings.STRIPE_MODE == "live")
            or session.get("mode") != "subscription" or not session.get("subscription")
            or (attempt.session_id and attempt.session_id != session.get("id"))):
        return
    sub = retrieve_billing_subscription(session["subscription"])
    if (sub.get("metadata") or {}).get("billing_checkout") != str(attempt.pk) or sub.get("customer") != session.get("customer"):
        return
    handle_subscription_updated(sub)


def portal_configuration(school):
    """Dedicated configuration never changes the existing schools' default Stripe portal."""
    offer = current_offer(school)
    if not offer:
        raise ValidationError("No agreement exists for this billing environment.")
    sdk, key = _stripe()
    if _data(sdk.Account.retrieve(api_key=key)).get("id") != offer.account_id:
        raise ValidationError("The billing account has changed. Contact Pontora.")
    if not offer.portal_configuration_id:
        config = _data(sdk.billing_portal.Configuration.create(
            features={"payment_method_update": {"enabled": True}, "invoice_history": {"enabled": True},
                      "subscription_update": {"enabled": False},
                      "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
                      "customer_update": {"enabled": False}},
            api_key=key, idempotency_key=f"school-billing-portal-{offer.pk}",
        ))
        if not config.get("id"):
            raise ValidationError("Billing portal configuration could not be created.")
        offer.portal_configuration_id = config["id"]
        offer.save(update_fields=["portal_configuration_id"])
    return offer.portal_configuration_id
