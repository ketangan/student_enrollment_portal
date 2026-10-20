import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import School


@pytest.mark.django_db(transaction=True)
def test_square_and_deposit_upgrade_preserves_existing_stripe_payments():
    import uuid
    from django.utils import timezone

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("payments", "0004_paymentaccount_scope_alter_paymentaccount_reference_and_more"),
           ("scheduling", "0023_catalogvariant_image_url_and_more")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="payment-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Studio")
        offering = apps.get_model("scheduling", "Offering").objects.create(location=location, name="Workshop")
        from datetime import timedelta
        party = apps.get_model("scheduling", "PartyRequest").objects.create(
            offering=offering, organizer_name="Existing party", organizer_email="existing@example.invalid",
            starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=2), group_size=6,
            space="shared", proposed_total=450, proposed_deposit=150, submission_key=uuid.uuid4(),
            offering_snapshot={"workshop": "Workshop", "location": "Studio", "timezone": "America/Los_Angeles"})
        account = apps.get_model("payments", "PaymentAccount").objects.create(
            school=school, reference="acct_migration_test", enabled=True)
        order = apps.get_model("payments", "PaymentOrder").objects.create(
            school=school, account=account, purpose="booking", source_key="existing",
            merchant_reference=account.reference, total_cents=6500, deposit_cents=2500,
            description="Existing reservation")
        entry = apps.get_model("payments", "LedgerEntry").objects.create(
            order=order, school=school, environment="test", key=uuid.uuid4(), kind="payment",
            channel="stripe", amount_cents=2500, reference="pi_migration_test")
        attempt = apps.get_model("payments", "PaymentAttempt").objects.create(
            order=order, account=account, amount_cents=2500, state="paid", entry=entry,
            provider_session="cs_test_migration", provider_intent=entry.reference,
            expires_at=timezone.now(), success_url="https://example.invalid/return",
            cancel_url="https://example.invalid/return")
        MigrationExecutor(connection).migrate(latest)
        from core.payments.models import PaymentAccount, PaymentAttempt, PaymentOrder, LedgerEntry
        from scheduling.models import SchedulingProfile, Offering
        saved_account = PaymentAccount.objects.get(pk=account.pk)
        saved_order = PaymentOrder.objects.get(pk=order.pk)
        saved_attempt = PaymentAttempt.objects.get(pk=attempt.pk)
        saved_entry = LedgerEntry.objects.get(pk=entry.pk)
        assert (saved_account.provider, saved_account.reference, saved_account.location_reference,
                saved_account.enabled) == ("stripe", account.reference, "", True)
        assert (saved_order.account_id, saved_order.total_cents, saved_order.deposit_cents) == (account.pk, 6500, 2500)
        assert (saved_attempt.state, saved_attempt.entry_id, saved_attempt.submitted_at) == ("paid", entry.pk, None)
        assert (saved_entry.channel, saved_entry.amount_cents, saved_entry.reference) == ("stripe", 2500, entry.reference)
        assert (saved_entry.external_reference, saved_entry.paid_on, saved_entry.note, saved_entry.reverses_id) == ("", None, "", None)
        assert saved_entry.extra_charge_cents == 0
        assert SchedulingProfile.objects.get(pk=profile.pk).payment_provider == "stripe"
        assert Offering.objects.get(pk=offering.pk).deposit_per_participant == 0
        from scheduling.models import Location, PartyRequest
        saved_location = Location.objects.get(pk=location.pk)
        assert saved_location.name == "Studio"
        saved_party = PartyRequest.objects.get(pk=party.pk)
        assert saved_party.package_id is None and saved_party.package_snapshot == {}
        assert saved_party.proposed_total == 450 and saved_party.proposed_deposit == 150
        assert School.objects.get(pk=school.pk).plan == "custom"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_message_migration_does_not_queue_historical_confirmations():
    from datetime import date, time
    import uuid
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0012_lesson_offer_makeup_eligibility")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        def model(name):
            return apps.get_model("scheduling", name)
        school = apps.get_model("core", "School").objects.create(slug="message-upgrade", plan="custom")
        profile = model("SchedulingProfile").objects.create(school=school, enabled=True, lessons_enabled=True)
        location = model("Location").objects.create(profile=profile, name="Studio")
        service = model("LessonService").objects.create(profile=profile, name="Piano", duration_minutes=30)
        teacher = model("LessonTeacher").objects.create(profile=profile, name="Teacher")
        household = model("LessonHousehold").objects.create(profile=profile, name="Family", email="family@example.invalid")
        student = model("LessonStudent").objects.create(household=household, name="Student")
        offer = model("LessonBookingOffer").objects.create(profile=profile, title="Trial", location=location,
            service=service, teacher=teacher, published=True, public_trials=True)
        series = model("LessonSeries").objects.create(profile=profile,service=service,teacher=teacher,student=student,
            location=location,first_date=date(2030,1,7),last_date=date(2030,1,7),start_time=time(10),
            submission_key=uuid.uuid4(),submission_digest="prior",snapshot={"service":"Piano"})
        booking = model("LessonBooking").objects.create(offer=offer,series=series,contact_name="Guardian",
            contact_email="guardian@example.invalid",browser_hash="a"*64)
        notice = model("LessonBookingNotice").objects.create(booking=booking,event_key="confirmed",
            recipient="guardian@example.invalid",subject="Historical confirmation",body="Already saved")
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import SchedulingProfile, LessonBookingNotice, BookingMessage
        assert not SchedulingProfile.objects.get(pk=profile.pk).message_preview_enabled
        saved = LessonBookingNotice.objects.get(pk=notice.pk)
        assert saved.delivery_id is None and saved.body == "Already saved"
        assert not BookingMessage.objects.exists() and School.objects.get(pk=school.pk).plan=="custom"
        MigrationExecutor(connection).migrate(old)
        assert model("LessonBookingNotice").objects.get(pk=notice.pk).subject=="Historical confirmation"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_offer_policy_migration_preserves_existing_offer_with_credits_disabled():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0011_lesson_self_booking")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="offer-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, lessons_enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Studio")
        service = apps.get_model("scheduling", "LessonService").objects.create(profile=profile, name="Piano", duration_minutes=30)
        teacher = apps.get_model("scheduling", "LessonTeacher").objects.create(profile=profile, name="Teacher")
        offer = apps.get_model("scheduling", "LessonBookingOffer").objects.create(profile=profile, title="Trial",
            location=location, service=service, teacher=teacher, fee_cents=3000, public_trials=True, published=True)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.appointment_models import LessonBookingOffer
        upgraded = LessonBookingOffer.objects.get(pk=offer.pk)
        assert upgraded.published and upgraded.fee_cents == 3000 and upgraded.makeup_eligible is False
        assert School.objects.get(pk=school.pk).plan == "custom"
        MigrationExecutor(connection).migrate(old)
        assert apps.get_model("scheduling", "LessonBookingOffer").objects.get(pk=offer.pk).title == "Trial"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_booking_migration_leaves_old_family_grants_read_only():
    from datetime import timedelta
    from django.utils import timezone
    import uuid
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0010_family_schedule_access")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="booking-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, lessons_enabled=True)
        household = apps.get_model("scheduling", "LessonHousehold").objects.create(profile=profile, name="Family", email="family@example.invalid")
        now = timezone.now()
        grant = apps.get_model("scheduling", "FamilyScheduleGrant").objects.create(household=household,recipient_name="Guardian",
            recipient_email="guardian@example.invalid",created_at=now,expires_at=now+timedelta(days=7),
            token_expires_at=now+timedelta(days=1),token_hash="a"*64,submission_key=uuid.uuid4(),submission_digest="b"*64)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import FamilyScheduleGrant, LessonBookingOffer, LessonBooking, LessonBookingNotice
        upgraded = FamilyScheduleGrant.objects.get(pk=grant.pk)
        assert not upgraded.allow_booking and upgraded.token_hash == "a"*64
        assert not LessonBookingOffer.objects.exists() and not LessonBooking.objects.exists() and not LessonBookingNotice.objects.exists()
        assert School.objects.get(pk=school.pk).plan == "custom"
        MigrationExecutor(connection).migrate(old)
        assert apps.get_model("scheduling", "FamilyScheduleGrant").objects.get(pk=grant.pk).recipient_name == "Guardian"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_family_access_migration_preserves_existing_households_without_grants():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0009_lesson_credits")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="family-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, lessons_enabled=True)
        household = apps.get_model("scheduling", "LessonHousehold").objects.create(profile=profile, name="Family", email="private@example.invalid")
        student = apps.get_model("scheduling", "LessonStudent").objects.create(household=household, name="Original student")
        policy = apps.get_model("scheduling", "LessonCreditPolicy").objects.create(profile=profile, enabled=True, notice_hours=48, expiry_days=60)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import FamilyScheduleGrant, LessonHousehold, LessonStudent, LessonCreditPolicy
        assert not FamilyScheduleGrant.objects.exists()
        assert LessonHousehold.objects.get(pk=household.pk).email == "private@example.invalid"
        assert LessonStudent.objects.get(pk=student.pk).name == "Original student"
        saved = LessonCreditPolicy.objects.get(pk=policy.pk)
        assert (saved.enabled, saved.notice_hours, saved.expiry_days) == (True, 48, 60)
        assert School.objects.get(pk=school.pk).plan == "custom"
        MigrationExecutor(connection).migrate(old)
        assert "scheduling_familyschedulegrant" not in connection.introspection.table_names()
        assert apps.get_model("scheduling", "LessonStudent").objects.get(pk=student.pk).name == "Original student"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_credit_migration_preserves_lesson_history_and_is_opt_in():
    from datetime import date, time, timedelta
    from django.utils import timezone
    import uuid

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0008_lesson_changes")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="credit-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, lessons_enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Original studio")
        service = apps.get_model("scheduling", "LessonService").objects.create(profile=profile, name="Piano", duration_minutes=30)
        teacher = apps.get_model("scheduling", "LessonTeacher").objects.create(profile=profile, name="Teacher")
        family = apps.get_model("scheduling", "LessonHousehold").objects.create(profile=profile, name="Family", email="family@example.invalid")
        student = apps.get_model("scheduling", "LessonStudent").objects.create(household=family, name="Student")
        series = apps.get_model("scheduling", "LessonSeries").objects.create(profile=profile, service=service, teacher=teacher,
            student=student, location=location, first_date=date(2030,1,7), last_date=date(2030,1,7), start_time=time(10),
            submission_key=uuid.uuid4(), submission_digest="original", snapshot={"service":"Piano"})
        start = timezone.now()+timedelta(days=2)
        occurrence = apps.get_model("scheduling", "LessonOccurrence").objects.create(series=series, starts_at=start,
            ends_at=start+timedelta(minutes=30), state="canceled", reason="Existing cancellation", version=3)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import LessonOccurrence, LessonCredit, LessonCreditEvent, LessonCancellation, LessonCreditPolicy
        upgraded = LessonOccurrence.objects.get(pk=occurrence.pk)
        assert upgraded.state == "canceled" and upgraded.version == 3 and upgraded.reason == "Existing cancellation"
        for model in [LessonCredit, LessonCreditEvent, LessonCancellation, LessonCreditPolicy]:
            assert not model.objects.exists()
        assert School.objects.get(pk=school.pk).plan == "custom"
        assert LessonCreditPolicy(profile_id=profile.pk).enabled is False
        MigrationExecutor(connection).migrate(old)
        assert apps.get_model("scheduling", "LessonOccurrence").objects.get(pk=occurrence.pk).version == 3
        assert "scheduling_lessoncredit" not in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_lesson_changes_migration_preserves_schedules_and_defaults():
    from datetime import date, time, timedelta
    from django.utils import timezone
    import uuid

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0007_lesson_scheduling")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="lesson-change-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, lessons_enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Original studio")
        service = apps.get_model("scheduling", "LessonService").objects.create(profile=profile, name="Piano", duration_minutes=30)
        teacher = apps.get_model("scheduling", "LessonTeacher").objects.create(profile=profile, name="Original teacher")
        family = apps.get_model("scheduling", "LessonHousehold").objects.create(profile=profile, name="Family", email="family@example.com")
        student = apps.get_model("scheduling", "LessonStudent").objects.create(household=family, name="Student")
        series = apps.get_model("scheduling", "LessonSeries").objects.create(profile=profile, service=service,
            teacher=teacher, student=student, location=location, first_date=date(2030, 1, 7), last_date=date(2030, 1, 21),
            start_time=time(10), submission_key=uuid.uuid4(), submission_digest="original",
            snapshot={"duration_minutes": 30, "timezone": "America/Los_Angeles"})
        start = timezone.now() + timedelta(days=7)
        states = ["scheduled", "canceled", "skipped"]
        for i, state in enumerate(states):
            apps.get_model("scheduling", "LessonOccurrence").objects.create(series=series,
                starts_at=start+timedelta(weeks=i), ends_at=start+timedelta(weeks=i, minutes=30), state=state, reason="Original reason")
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import LessonOccurrence, LessonSeries, SchedulingProfile
        upgraded = LessonSeries.objects.get(pk=series.pk)
        assert upgraded.predecessor is None and upgraded.snapshot == series.snapshot
        assert upgraded.submission_digest == "original" and upgraded.teacher_id == teacher.pk
        assert list(upgraded.occurrences.values_list("state", flat=True)) == states
        assert all(item.version == 0 and item.attendance == "unmarked" and not item.attendance_note
                   and item.replacement is None and item.reason == "Original reason" for item in upgraded.occurrences.all())
        assert SchedulingProfile.objects.get(pk=profile.pk).lessons_enabled
        assert School.objects.get(pk=school.pk).plan == "custom"
        # Rollback is safe before recording new history; populated changes require a forward fix.
        MigrationExecutor(connection).migrate(old)
        historical = apps.get_model("scheduling", "LessonOccurrence")
        assert list(historical.objects.filter(series=series).values_list("state", flat=True)) == states
        MigrationExecutor(connection).migrate(latest)
        assert LessonOccurrence.objects.filter(series_id=series.pk).count() == 3
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_lesson_migration_preserves_camps_and_defaults_off():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0006_camp_campregistration_reservationoccurrence_and_more")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="lesson-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, native_reservations_enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Existing location")
        offering = apps.get_model("scheduling", "Offering").objects.create(location=location, name="Existing offering")
        camp = apps.get_model("scheduling", "Camp").objects.create(offering=offering, name="Existing camp", price=150, deposit=50, is_open=True)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import Camp, LessonSeries, SchedulingProfile
        upgraded = SchedulingProfile.objects.get(pk=profile.pk)
        assert upgraded.enabled and upgraded.native_reservations_enabled and not upgraded.lessons_enabled
        assert Camp.objects.get(pk=camp.pk).price == 150 and Camp.objects.get(pk=camp.pk).is_open
        assert School.objects.get(pk=school.pk).plan == "custom" and not LessonSeries.objects.exists()
        MigrationExecutor(connection).migrate(old)
        assert "scheduling_lessonseries" not in connection.introspection.table_names()
        assert apps.get_model("scheduling", "Camp").objects.get(pk=camp.pk).deposit == 50
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_camp_migration_preserves_existing_reservation_and_ledger():
    from datetime import timedelta
    from django.utils import timezone
    import uuid
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0005_schedulingprofile_native_reservations_enabled_and_more"),
           ("payments", "0003_payment_recovery_refunds")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        actor = apps.get_model(settings.AUTH_USER_MODEL).objects.create(username="camp-migration-owner")
        school = apps.get_model("core", "School").objects.create(slug="camp-upgrade", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True, native_reservations_enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Original studio")
        offering = apps.get_model("scheduling", "Offering").objects.create(location=location, name="Workshop")
        start = timezone.now()+timedelta(days=3)
        session = apps.get_model("scheduling", "Session").objects.create(offering=offering, starts_at=start, ends_at=start+timedelta(hours=1), capacity=4)
        request = apps.get_model("scheduling", "WorkshopRequest").objects.create(session=session, purchaser_name="Existing", purchaser_email="existing@example.com", submission_key=uuid.uuid4())
        order = apps.get_model("payments", "PaymentOrder").objects.create(school=school, purpose="booking", source_key="old-workshop", environment="test", currency="usd", merchant_reference=f"offline:{school.pk}", total_cents=10000, deposit_cents=2500, description="Original quote")
        reservation = apps.get_model("scheduling", "Reservation").objects.create(profile=profile, order=order, workshop_request=request, session=session, starts_at=start, ends_at=session.ends_at, quantity=2, hold_until=start, state="confirmed")
        entry = apps.get_model("payments", "LedgerEntry").objects.create(order=order, school=school, environment="test", key=uuid.uuid4(), kind="payment", amount_cents=2500, reference="original-receipt", actor=actor)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import Camp, Reservation
        from core.payments.models import LedgerEntry
        item = Reservation.objects.get(pk=reservation.pk)
        assert item.state == "confirmed" and item.workshop_request_id == request.pk and item.camp_registration_id is None
        assert item.order.total_cents == 10000 and LedgerEntry.objects.get(pk=entry.pk).amount_cents == 2500
        assert not Camp.objects.exists() and School.objects.get(pk=school.pk).plan == "custom"
        # Empty new tables can be rolled back. Populated camps require a forward fix.
        MigrationExecutor(connection).migrate(old)
        assert "scheduling_camp" not in connection.introspection.table_names()
        assert apps.get_model("scheduling", "Reservation").objects.get(pk=reservation.pk).state == "confirmed"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_party_migration_preserves_workshop_requests_and_defaults_off():
    from datetime import timedelta
    from django.utils import timezone
    import uuid
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("scheduling", "0003_offering_accepts_requests_choicegroup_choiceoption_and_more")]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        school = apps.get_model("core", "School").objects.create(slug="party-upgrade", display_name="Existing", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Original")
        offering = apps.get_model("scheduling", "Offering").objects.create(location=location, name="Workshop", accepts_requests=True)
        start = timezone.now() + timedelta(days=10)
        session = apps.get_model("scheduling", "Session").objects.create(offering=offering, starts_at=start, ends_at=start+timedelta(hours=1), capacity=10)
        request = apps.get_model("scheduling", "WorkshopRequest").objects.create(session=session, purchaser_name="Existing parent", purchaser_email="parent@example.com", submission_key=uuid.uuid4())
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import Offering, PartyRequest, WorkshopRequest
        upgraded = Offering.objects.get(pk=offering.pk)
        assert upgraded.accepts_requests and not upgraded.accepts_party_requests
        assert WorkshopRequest.objects.get(pk=request.pk).purchaser_name == "Existing parent"
        assert not PartyRequest.objects.exists() and School.objects.get(pk=school.pk).plan == "custom"
        MigrationExecutor(connection).migrate(previous)
        assert "scheduling_partyrequest" not in connection.introspection.table_names()
        assert apps.get_model("scheduling", "WorkshopRequest").objects.get(pk=request.pk).purchaser_name == "Existing parent"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_initial_migration_is_additive_and_reversible():
    school = School.objects.create(slug="preserve-school", display_name="Preserve", plan="custom")
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("scheduling", None)])
        assert "scheduling_schedulingprofile" not in connection.introspection.table_names()
        assert School.objects.get(pk=school.pk).plan == "custom"
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        profile_model = apps.get_model("scheduling", "SchedulingProfile")
        assert not profile_model.objects.exists()
        profile = profile_model.objects.create(school_id=school.pk)
        assert profile.enabled is False
        assert School.objects.get(pk=school.pk).display_name == "Preserve"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_request_migration_preserves_setup_and_defaults_collection_off():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_target = [("scheduling", "0002_location_offering_room_session_and_more")]
    try:
        executor.migrate(old_target)
        apps = executor.loader.project_state(old_target).apps
        school = apps.get_model("core", "School").objects.create(slug="request-migration", display_name="Original")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Original location")
        offering = apps.get_model("scheduling", "Offering").objects.create(location=location, name="Existing workshop", is_published=True)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import Offering
        migrated = Offering.objects.get(pk=offering.pk)
        assert migrated.is_published and not migrated.accepts_requests
        assert migrated.location.name == "Original location"
        MigrationExecutor(connection).migrate(old_target)
        assert "scheduling_workshoprequest" not in connection.introspection.table_names()
        assert apps.get_model("scheduling", "Offering").objects.get(pk=offering.pk).name == "Existing workshop"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_setup_migration_preserves_access_and_school_records():
    from scheduling.models import Location, SchedulingProfile
    school = School.objects.create(slug="preserve-m1b", display_name="Original", plan="custom")
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    Location.objects.create(profile=profile, name="Temporary setup")
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("scheduling", "0001_initial")])
        assert "scheduling_location" not in connection.introspection.table_names()
        historical = executor.loader.project_state([("scheduling", "0001_initial")]).apps
        assert historical.get_model("scheduling", "SchedulingProfile").objects.get(pk=profile.pk).enabled
        assert School.objects.get(pk=school.pk).plan == "custom"
        MigrationExecutor(connection).migrate(latest)
        assert not Location.objects.exists()
        assert SchedulingProfile.objects.get(pk=profile.pk).enabled
        assert School.objects.get(pk=school.pk).display_name == "Original"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_native_reservation_and_shared_ledger_migrations_are_additive():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0004_offering_accepts_party_requests_partyrequest")]
    try:
        executor.migrate([("payments", None)])
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="ledger-migration", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Original room location")
        offering = apps.get_model("scheduling", "Offering").objects.create(location=location, name="Party", accepts_party_requests=True)
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import Offering, SchedulingProfile
        from core.payments.models import PaymentOrder
        upgraded = SchedulingProfile.objects.get(pk=profile.pk)
        assert upgraded.enabled and not upgraded.native_reservations_enabled
        assert Offering.objects.get(pk=offering.pk).accepts_party_requests
        assert not PaymentOrder.objects.exists()
        assert School.objects.get(pk=school.pk).plan == "custom"
        MigrationExecutor(connection).migrate([("payments", None)])
        tables = connection.introspection.table_names()
        assert "payments_paymentorder" not in tables and "scheduling_reservation" not in tables
        assert apps.get_model("scheduling", "Offering").objects.get(pk=offering.pk).name == "Party"
    finally:
        MigrationExecutor(connection).migrate(latest)
