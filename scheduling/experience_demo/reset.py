"""Explicit, guarded reset of the local Plushumi rehearsal, never customer data."""
from django.core.management.base import CommandError
from django.db import transaction
from core.models import AdminAuditLog
from core.payments.models import PaymentAccount, PaymentOrder, PaymentAttempt, LedgerEntry
from scheduling import models as m
from .config import local_database


@transaction.atomic
def reset_demo(profile):
    school = profile.school
    if not (local_database() and school.slug == "plushumi-demo" and school.is_demo
            and school.feature_flags.get("booking_experience_demo") is True):
        raise CommandError("Only the isolated local Plushumi demo can be reset.")
    m.SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    orders = PaymentOrder.objects.filter(school=school)
    if (PaymentAccount.objects.filter(school=school).exists()
            or PaymentAttempt.objects.filter(order__in=orders).exists()
            or LedgerEntry.objects.filter(order__in=orders, channel="terminal").exists()
            or orders.exclude(environment="test").exists()
            or school.stripe_customer_id or school.smtp_host or school.app_fee_stripe_secret_key
            or profile.connections.exists()
            or m.VisitProgram.objects.filter(offering__location__profile=profile).exists()
            or m.LessonService.objects.filter(profile=profile).exists()):
        raise CommandError("Refusing to reset connected payments, Terminal receipts, integrations, visits or lesson data.")
    bookings = m.Reservation.objects.filter(profile=profile)
    m.StockAllocation.objects.filter(reservation__in=bookings).delete()
    m.ReservationOccurrence.objects.filter(reservation__in=bookings).delete()
    bookings.delete()
    m.CampRegistration.objects.filter(camp__offering__location__profile=profile).delete()
    m.WorkshopRequest.objects.filter(session__offering__location__profile=profile).delete()
    m.PartyRequest.objects.filter(offering__location__profile=profile).delete()
    m.Camp.objects.filter(offering__location__profile=profile).delete()
    m.Session.objects.filter(offering__location__profile=profile).delete()
    m.StockAdjustment.objects.filter(level__location__profile=profile).delete()
    m.StockLevel.objects.filter(location__profile=profile).delete()
    m.CatalogVariant.objects.filter(catalog__profile=profile).delete()
    m.Material.objects.filter(profile=profile).delete()
    m.ChoiceOption.objects.filter(group__offering__location__profile=profile).delete()
    m.ChoiceGroup.objects.filter(offering__location__profile=profile).delete()
    m.Offering.objects.filter(location__profile=profile).delete()
    m.ChoiceOption.objects.filter(group__catalog__profile=profile).delete()
    m.ChoiceGroup.objects.filter(catalog__profile=profile).delete()
    m.InventoryCatalog.objects.filter(profile=profile).delete()
    m.Room.objects.filter(location__profile=profile).delete()
    m.Location.objects.filter(profile=profile).delete()
    LedgerEntry.objects.filter(order__in=orders).delete()
    orders.delete()
    AdminAuditLog.objects.filter(extra__scheduling_profile_id=profile.pk).delete()
    school.feature_flags = {k: v for k, v in school.feature_flags.items()
                            if k not in {"booking_demo_inventory", "material_demo_seeded"}}
    school.save(update_fields=["feature_flags"])
