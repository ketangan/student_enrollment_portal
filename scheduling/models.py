from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator, URLValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit
from pathlib import PurePosixPath
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
import uuid

from .capabilities import validate_capabilities
from .guide_storage import guide_storage


class SchedulingProfile(models.Model):
    school = models.OneToOneField(
        "core.School", on_delete=models.CASCADE, related_name="scheduling_profile"
    )
    enabled = models.BooleanField(default=False)
    capabilities = models.JSONField(default=list, blank=True, validators=[validate_capabilities])
    native_reservations_enabled = models.BooleanField(default=False)
    payment_provider = models.CharField(max_length=8, default="stripe", choices=[("stripe", "Stripe"), ("square", "Square")])
    lessons_enabled = models.BooleanField(default=False)
    message_preview_enabled = models.BooleanField(default=False)
    party_notifications_enabled = models.BooleanField(default=False)
    calendar_enabled = models.BooleanField(default=False)
    default_dashboard_view = models.CharField(max_length=8, default="overview",
        choices=[("overview", "Overview"), ("calendar", "Calendar")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(payment_provider__in=["stripe", "square"]), name="scheduling_payment_provider_valid")]

    def __str__(self):
        return str(self.school)


def design_guide_path(instance, filename):
    return f"booking-guides/{instance.profile_id}/{uuid.uuid4().hex}.pdf"


class DesignGuide(models.Model):
    profile = models.ForeignKey(SchedulingProfile, on_delete=models.PROTECT, related_name="design_guides")
    file = models.FileField(upload_to=design_guide_path, storage=guide_storage)
    original_name = models.CharField(max_length=200)
    checksum = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]


class SchedulingMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        SCHEDULER = "scheduler", "Scheduler"
        VIEWER = "viewer", "Viewer"

    profile = models.ForeignKey(
        SchedulingProfile, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="scheduling_memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    all_locations = models.BooleanField(default=True)
    locations = models.ManyToManyField("Location", blank=True, related_name="staff_memberships")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_scheduling_memberships")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "user"], name="unique_scheduling_membership"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["owner", "scheduler", "viewer"]),
                name="valid_scheduling_role",
            ),
        ]

    def __str__(self):
        return f"{self.user} -> {self.profile} ({self.role})"


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Choose a valid time zone.")


CAPACITY_VALIDATORS = [MinValueValidator(1), MaxValueValidator(10000)]


class Location(models.Model):
    profile = models.ForeignKey(SchedulingProfile, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=300, blank=True)
    timezone_name = models.CharField(max_length=64, default="America/Los_Angeles", validators=[validate_timezone])
    inventory_buffer_days = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(365)])

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(Lower("name"), "profile", name="scheduling_location_name_unique")]

    def __str__(self):
        return self.name

    def clean(self):
        if self.pk:
            previous = Location.objects.get(pk=self.pk)
            if previous.timezone_name != self.timezone_name and (Session.objects.filter(offering__location=self).exists() or PartyRequest.objects.filter(offering__location=self).exists()):
                raise ValidationError({"timezone_name": "The time zone cannot change after sessions or party requests have been created."})
            if previous.timezone_name != self.timezone_name and (LessonSeries.objects.filter(location=self).exists() or LessonAvailability.objects.filter(location=self).exists()):
                raise ValidationError({"timezone_name": "The time zone cannot change after lesson availability or schedules exist."})


class Room(models.Model):
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="rooms")
    name = models.CharField(max_length=100)
    capacity = models.PositiveIntegerField(validators=CAPACITY_VALIDATORS)

    class Meta:
        ordering = ["location__name", "name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "location", name="scheduling_room_name_unique"),
            models.CheckConstraint(condition=models.Q(capacity__gte=1, capacity__lte=10000), name="scheduling_room_capacity_valid"),
        ]

    def __str__(self):
        return f"{self.name} ({self.location.name})"

    def clean(self):
        if self.pk:
            previous = Room.objects.get(pk=self.pk)
            if previous.location_id != self.location_id:
                raise ValidationError({"location": "A saved room cannot move to another location."})
            if self.capacity is not None and self.sessions.filter(capacity__gt=self.capacity).exists():
                raise ValidationError({"capacity": "Capacity cannot be lower than an existing session's capacity."})
            if self.capacity is not None and self.party_requests.filter(status__in=["pending", "reviewed"], group_size__gt=self.capacity).exists():
                raise ValidationError({"capacity": "Capacity cannot be lower than an active party request's group size."})
            if self.capacity is not None and self.party_packages.annotate(
                    maximum=models.F("participant_limit") + models.F("additional_participant_limit")).filter(maximum__gt=self.capacity).exists():
                raise ValidationError({"capacity": "Update this space's party packages before reducing its participant capacity."})
            from .reservations import validate_room_capacity
            validate_room_capacity(self)


class Offering(models.Model):
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="offerings")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, max_length=3000)
    deposit_per_participant = models.DecimalField(max_digits=8, decimal_places=2, default=0,
        validators=[MinValueValidator(0)])
    is_published = models.BooleanField(default=False)
    accepts_requests = models.BooleanField(default=False)
    accepts_party_requests = models.BooleanField(default=False)
    inventory_catalog = models.ForeignKey("InventoryCatalog", null=True, blank=True,
        on_delete=models.PROTECT, related_name="offerings")

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.CheckConstraint(
            condition=models.Q(deposit_per_participant=0) | models.Q(deposit_per_participant__gte=0.50),
            name="workshop_deposit_valid")]

    def __str__(self):
        return self.name

    def clean(self):
        if self.inventory_catalog_id and self.location_id and self.inventory_catalog.profile_id != self.location.profile_id:
            raise ValidationError({"inventory_catalog": "Choose a catalog belonging to this business."})
        if self.pk:
            previous = Offering.objects.get(pk=self.pk)
            if previous.location_id != self.location_id:
                raise ValidationError({"location": "A saved workshop cannot move to another location."})
            if previous.inventory_catalog_id != self.inventory_catalog_id and (
                WorkshopRequest.objects.filter(session__offering=self).exists()
                or self.party_requests.exists()
                or CampRegistration.objects.filter(camp__offering=self).exists()
            ):
                raise ValidationError({"inventory_catalog": "This offering already has requests or bookings. Create a separate offering to use a different inventory catalog."})


class Session(models.Model):
    offering = models.ForeignKey(Offering, on_delete=models.PROTECT, related_name="sessions")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="sessions", blank=True, null=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    capacity = models.PositiveIntegerField(validators=CAPACITY_VALIDATORS)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="scheduling_session_time_valid"),
            models.CheckConstraint(condition=models.Q(capacity__gte=1, capacity__lte=10000), name="scheduling_session_capacity_valid"),
        ]
        indexes = [models.Index(fields=["room", "starts_at", "ends_at"], name="scheduling_room_times")]

    def __str__(self):
        return f"{self.offering.name}: {self.starts_at}"

    def clean(self):
        errors = {}
        from .reservations import validate_session_edit
        validate_session_edit(self)
        if self.room_id and self.offering_id:
            if self.room.location_id != self.offering.location_id:
                errors["room"] = "Choose a room at this workshop's location."
            if self.capacity is not None and self.capacity > self.room.capacity:
                errors["capacity"] = f"This room holds at most {self.room.capacity} participants."
        if self.starts_at and self.ends_at:
            if timezone.is_naive(self.starts_at) or timezone.is_naive(self.ends_at):
                errors["starts_at"] = "Session times must include their location's time zone."
            elif self.ends_at <= self.starts_at:
                errors["ends_at"] = "End time must be after start time."
            else:
                previous = Session.objects.filter(pk=self.pk).first() if self.pk else None
                if previous and self.requests.filter(status="pending").exists():
                    if (previous.starts_at, previous.ends_at, previous.room_id) != (self.starts_at, self.ends_at, self.room_id):
                        errors["starts_at"] = "Withdraw pending requests before changing this session's time or room."
                    if self.capacity is not None and self.requests.filter(status="pending").annotate(total=models.Count("participants")).filter(total__gt=self.capacity).exists():
                        errors["capacity"] = "Capacity cannot be smaller than a pending request's group size."
                if (not previous or previous.starts_at != self.starts_at) and self.starts_at <= timezone.now():
                    errors["starts_at"] = "Choose a future start time."
                if self.is_published and self.room_id and Session.objects.filter(
                    room_id=self.room_id, is_published=True,
                    starts_at__lt=self.ends_at, ends_at__gt=self.starts_at,
                ).exclude(pk=self.pk).exists():
                    errors["room"] = "This room already has a published session at that time."
        if errors:
            raise ValidationError(errors)


def validate_choice_image(value):
    parsed = urlsplit(value)
    if value.startswith("/static/"):
        if parsed.query or parsed.fragment or "\\" in value or "%" in value or ".." in PurePosixPath(parsed.path).parts or PurePosixPath(parsed.path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValidationError("Choose a local PNG/JPEG/WebP asset or an HTTPS image URL.")
    else:
        URLValidator(schemes=["https"])(value)


class ChoiceGroup(models.Model):
    offering = models.ForeignKey(Offering, null=True, blank=True, on_delete=models.PROTECT, related_name="choice_groups")
    catalog = models.ForeignKey("InventoryCatalog", null=True, blank=True, on_delete=models.PROTECT, related_name="choice_groups")
    tracks_stock = models.BooleanField(default=False)
    name = models.CharField(max_length=80)
    reference_image = models.CharField(max_length=500, blank=True, validators=[validate_choice_image])
    position = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(1000)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [models.UniqueConstraint(Lower("name"), "offering", name="scheduling_choice_group_name_unique"),
            models.UniqueConstraint(Lower("name"), "catalog", name="inventory_group_name_unique"),
            models.CheckConstraint(condition=(models.Q(offering__isnull=False, catalog__isnull=True) |
                models.Q(offering__isnull=True, catalog__isnull=False)), name="choice_group_owner_valid")]

    def __str__(self):
        return self.name

    def clean(self):
        if self.pk and self.catalog_id and self.catalog.items.exists():
            previous = ChoiceGroup.objects.get(pk=self.pk)
            if previous.tracks_stock != self.tracks_stock or (previous.tracks_stock and not self.is_active):
                raise ValidationError("Stock dimensions cannot change after items exist. Disable individual options instead.")
        if self._state.adding and self.catalog_id and self.tracks_stock and self.catalog.items.exists():
            raise ValidationError("Create a new catalog to add another stock dimension after items exist.")


class ChoiceOption(models.Model):
    group = models.ForeignKey(ChoiceGroup, on_delete=models.PROTECT, related_name="options")
    label = models.CharField(max_length=80)
    swatch = models.CharField(max_length=7, blank=True, validators=[RegexValidator(r"\A#[0-9a-fA-F]{6}\Z", "Use a six-digit hex color, such as #80B8DB.")])
    image_url = models.CharField(max_length=500, blank=True, validators=[validate_choice_image])
    position = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(1000)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [models.UniqueConstraint(Lower("label"), "group", name="scheduling_choice_option_label_unique")]

    def __str__(self):
        return self.label


class WorkshopRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        WITHDRAWN = "withdrawn", "Withdrawn"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session = models.ForeignKey(Session, on_delete=models.PROTECT, related_name="requests")
    purchaser_name = models.CharField(max_length=100)
    purchaser_email = models.EmailField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    submission_key = models.UUIDField(unique=True, editable=False)
    submission_digest = models.CharField(max_length=64, editable=False)
    browser_digest = models.CharField(max_length=64, editable=False)
    session_snapshot = models.JSONField(default=dict, editable=False)

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(status__in=["pending", "withdrawn"]), name="scheduling_request_status_valid")]

    def __str__(self):
        return str(self.public_id)

    @property
    def requested_start(self):
        return parse_datetime(self.session_snapshot.get("starts_at", ""))

    @property
    def requested_end(self):
        return parse_datetime(self.session_snapshot.get("ends_at", ""))


class RequestParticipant(models.Model):
    request = models.ForeignKey(WorkshopRequest, on_delete=models.CASCADE, related_name="participants")
    name = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField()
    selections = models.JSONField(default=list, editable=False)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [models.UniqueConstraint(fields=["request", "position"], name="scheduling_request_participant_position")]


class PartyPackage(models.Model):
    """Location-owned offers; submitted inquiries retain their own snapshots."""

    offering = models.ForeignKey(Offering, on_delete=models.PROTECT, related_name="party_packages")
    name = models.CharField(max_length=150)
    participant_limit = models.PositiveIntegerField(validators=CAPACITY_VALIDATORS)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(15), MaxValueValidator(720)])
    total = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    deposit = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    space = models.CharField(max_length=16, choices=[("shared", "Shared space"), ("exclusive", "Private room / table"), ("studio", "Full studio")], default="shared")
    room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.PROTECT, related_name="party_packages")
    included_observers = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(10000)])
    additional_participant_limit = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(10000)])
    additional_participant_price = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    tax_rate = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(Decimal("99.999"))])
    booking_mode = models.CharField(max_length=8, default="review", choices=[("review", "Staff approval"), ("direct", "Direct booking")])
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "offering", name="party_package_name_unique"),
            models.CheckConstraint(condition=models.Q(participant_limit__gte=1, participant_limit__lte=10000), name="party_package_size_valid"),
            models.CheckConstraint(condition=models.Q(deposit__gte=0) & (models.Q(total__isnull=True) | models.Q(total__gte=models.F("deposit"))), name="party_package_price_valid"),
            models.CheckConstraint(condition=models.Q(is_published=False) | models.Q(total__isnull=False, duration_minutes__isnull=False), name="party_package_publish_complete"),
            models.CheckConstraint(condition=models.Q(additional_participant_price__gte=0, additional_participant_limit__lte=10000, included_observers__lte=10000), name="party_package_extras_valid"),
            models.CheckConstraint(condition=models.Q(tax_rate__isnull=True) | models.Q(tax_rate__gte=0, tax_rate__lte=Decimal("99.999")), name="party_package_tax_valid"),
            models.CheckConstraint(condition=models.Q(space__in=["shared", "exclusive", "studio"], booking_mode__in=["review", "direct"]), name="party_package_modes_valid"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.room_id and self.offering_id and self.room.location_id != self.offering.location_id:
            errors["room"] = "Choose a space at this package's location."
        if self.room_id and self.participant_limit and self.additional_participant_limit is not None:
            if self.participant_limit + self.additional_participant_limit > self.room.capacity:
                errors["room"] = "The included and additional participants exceed this space's participant capacity."
        if self.participant_limit and self.additional_participant_limit is not None and self.participant_limit + self.additional_participant_limit > 10000:
            errors["additional_participant_limit"] = "The maximum group size is 10,000 participants."
        if self.pk and PartyPackage.objects.filter(pk=self.pk).exclude(offering_id=self.offering_id).exists():
            errors["offering"] = "A saved package cannot move to another workshop or location."
        if self.is_published:
            for field in ("total", "duration_minutes"):
                if getattr(self, field) is None:
                    errors[field] = "Required before publishing this package."
        if self.total is not None and self.deposit is not None:
            if 0 < self.total < Decimal("0.50"):
                errors["total"] = "Enter at least $0.50, or 0 for a free package."
            if self.deposit > self.total:
                errors["deposit"] = "The deposit cannot exceed the total."
            elif self.deposit and (self.deposit < Decimal("0.50") or self.total - self.deposit < Decimal("0.50")):
                errors["deposit"] = "Leave at least $0.50 for both the deposit and balance, or enter 0 for full payment."
        if errors:
            raise ValidationError(errors)

    def quote(self, quantity):
        if self.total is None or not 1 <= quantity <= self.participant_limit + self.additional_participant_limit:
            raise ValidationError("Choose a participant count within this package's limit.")
        extra = max(0, quantity - self.participant_limit)
        subtotal = self.total + extra * self.additional_participant_price
        tax = (subtotal * (self.tax_rate or Decimal(0)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if subtotal + tax > Decimal("999999.99"):
            raise ValidationError("This group's price exceeds the supported reservation amount.")
        return {"name": self.name, "participant_limit": self.participant_limit,
            "duration_minutes": self.duration_minutes, "base_price": str(self.total),
            "additional_participants": extra, "additional_participant_limit": self.additional_participant_limit,
            "additional_participant_price": str(self.additional_participant_price),
            "subtotal": str(subtotal), "tax_rate": str(self.tax_rate) if self.tax_rate is not None else None,
            "tax": str(tax), "total": str(subtotal + tax), "deposit": str(self.deposit),
            "included_observers": self.included_observers, "space": self.space,
            "room_id": self.room_id, "booking_mode": self.booking_mode}


class PartySlot(models.Model):
    """An offered start time, not a room or inventory claim."""

    package = models.ForeignKey(PartyPackage, on_delete=models.PROTECT, related_name="slots")
    starts_at = models.DateTimeField()
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["package", "starts_at"], name="party_slot_start_unique")]

    def __str__(self):
        return str(self.starts_at)

    def clean(self):
        if self.is_published and self.package_id:
            if self.starts_at and not timezone.now() + timedelta(minutes=40) < self.starts_at <= timezone.now() + timedelta(days=366):
                raise ValidationError("Choose a time more than 40 minutes ahead and within the next year.")
            if self.package.booking_mode != "direct" or not self.package.room_id or self.package.tax_rate is None:
                raise ValidationError("Set direct booking, a room/table and an explicit tax rate before publishing party times.")
            if not self.package.duration_minutes or self.package.total is None:
                raise ValidationError("Set the party price and duration before publishing party times.")


class PartyRequest(models.Model):
    """An inquiry and internal preparation proposal, never an inventory claim or order."""

    class Space(models.TextChoices):
        SHARED = "shared", "Shared space"
        EXCLUSIVE = "exclusive", "Private room / table"
        STUDIO = "studio", "Full studio"

    class Status(models.TextChoices):
        PENDING = "pending", "Needs review"
        REVIEWED = "reviewed", "Reviewed (not booked)"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    offering = models.ForeignKey(Offering, on_delete=models.PROTECT, related_name="party_requests")
    package = models.ForeignKey(PartyPackage, null=True, blank=True, on_delete=models.PROTECT, related_name="inquiries")
    party_slot = models.ForeignKey(PartySlot, null=True, blank=True, on_delete=models.PROTECT, related_name="requests")
    package_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    occasion = models.CharField(max_length=150, blank=True)
    age_range = models.CharField(max_length=100, blank=True)
    organizer_name = models.CharField(max_length=100)
    organizer_email = models.EmailField()
    organizer_phone = models.CharField(max_length=40, blank=True)
    birthday_name = models.CharField(max_length=100, blank=True)
    birthday_age = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(130)])
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    group_size = models.PositiveIntegerField(validators=CAPACITY_VALIDATORS)
    space = models.CharField(max_length=16, choices=Space.choices)
    customer_notes = models.TextField(blank=True, max_length=2000)
    offering_snapshot = models.JSONField(default=dict, editable=False)
    room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.PROTECT, related_name="party_requests")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    proposed_total = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    proposed_deposit = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    preparation_notes = models.TextField(blank=True, max_length=3000)
    materials = models.JSONField(default=list, blank=True, editable=False)
    revision = models.PositiveIntegerField(default=0, editable=False)
    submission_key = models.UUIDField(unique=True, editable=False)
    submission_digest = models.CharField(max_length=64, editable=False)
    browser_digest = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=["pending", "reviewed", "declined", "withdrawn"]), name="scheduling_party_status_valid"),
            models.CheckConstraint(condition=models.Q(space__in=["shared", "exclusive", "studio"]), name="scheduling_party_space_valid"),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="scheduling_party_time_valid"),
            models.CheckConstraint(condition=models.Q(group_size__gte=1, group_size__lte=10000), name="scheduling_party_size_valid"),
            models.CheckConstraint(condition=(
                models.Q(proposed_total__isnull=True, proposed_deposit__isnull=True) |
                models.Q(proposed_total__isnull=False, proposed_deposit__isnull=False,
                         proposed_total__gte=0, proposed_deposit__gte=0,
                         proposed_deposit__lte=models.F("proposed_total"))
            ), name="scheduling_party_proposal_valid"),
        ]

    def __str__(self):
        return str(self.public_id)

    @property
    def proposed_balance(self):
        return None if self.proposed_total is None or self.proposed_deposit is None else (self.proposed_total - self.proposed_deposit if self.proposed_deposit else 0)

    def clean(self):
        if self.package_id and self.package.offering_id != self.offering_id:
            raise ValidationError({"package": "Choose a package for this workshop."})
        if self.party_slot_id and self.party_slot.package_id != self.package_id:
            raise ValidationError({"party_slot": "Choose a time for this party package."})
        if self.room_id:
            if self.room.location_id != self.offering.location_id:
                raise ValidationError({"room": "Choose a room at this workshop's location."})
            if self.group_size > self.room.capacity:
                raise ValidationError({"room": "This room cannot hold the requested group size."})


class Camp(models.Model):
    offering = models.ForeignKey(Offering, on_delete=models.PROTECT, related_name="camps")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, max_length=3000)
    sessions = models.ManyToManyField(Session, related_name="camps")
    price = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(0)])
    deposit = models.DecimalField(max_digits=7, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    online = models.BooleanField(default=False)
    is_open = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(price__gte=0, deposit__gte=0, deposit__lte=models.F("price")), name="camp_price_valid")]

    def __str__(self):
        return self.name

    def clean(self):
        if self.price is not None and self.deposit is not None and self.deposit > self.price:
            raise ValidationError({"deposit": "The deposit cannot exceed the full camp price."})
        if self.pk and self.registrations.exists():
            old = Camp.objects.get(pk=self.pk)
            if any(getattr(old, field) != getattr(self, field) for field in ("offering_id", "price", "deposit", "online")):
                raise ValidationError("Create another camp to change the workshop, price or payment method after registration.")


class CampRegistration(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    camp = models.ForeignKey(Camp, on_delete=models.PROTECT, related_name="registrations")
    purchaser_name = models.CharField(max_length=100)
    purchaser_email = models.EmailField()
    participants = models.JSONField(default=list, editable=False)
    submission_key = models.UUIDField(unique=True, editable=False)
    submission_digest = models.CharField(max_length=64, editable=False)
    browser_digest = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "pk"]


class Reservation(models.Model):
    class State(models.TextChoices):
        HELD = "held", "Held - awaiting payment"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        REVIEW = "review", "Payment recorded - needs attention"
        CANCELED = "canceled", "Canceled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    profile = models.ForeignKey(SchedulingProfile, on_delete=models.PROTECT, related_name="reservations")
    order = models.OneToOneField("payments.PaymentOrder", on_delete=models.PROTECT)
    workshop_request = models.OneToOneField(WorkshopRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="reservation")
    party_request = models.OneToOneField(PartyRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="reservation")
    camp_registration = models.OneToOneField(CampRegistration, null=True, blank=True, on_delete=models.PROTECT, related_name="reservation")
    visit_booking = models.OneToOneField("scheduling.VisitBooking", null=True, blank=True, on_delete=models.PROTECT, related_name="reservation")
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    quantity = models.PositiveIntegerField(validators=CAPACITY_VALIDATORS)
    exclusive = models.BooleanField(default=False)
    snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=12, choices=State.choices, default=State.HELD)
    hold_until = models.DateTimeField()
    prepared_at = models.DateTimeField(null=True, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="prepared_reservations")
    preparation_version = models.PositiveIntegerField(default=0)
    internal_notes = models.TextField(blank=True, max_length=3000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=(
                models.Q(workshop_request__isnull=False, party_request__isnull=True, camp_registration__isnull=True, visit_booking__isnull=True, session__isnull=False)
                | models.Q(workshop_request__isnull=True, party_request__isnull=False, camp_registration__isnull=True, visit_booking__isnull=True, session__isnull=True, room__isnull=False)
                | models.Q(workshop_request__isnull=True, party_request__isnull=True, camp_registration__isnull=False, visit_booking__isnull=True, session__isnull=True, room__isnull=True)
                | models.Q(workshop_request__isnull=True, party_request__isnull=True, camp_registration__isnull=True, visit_booking__isnull=False, session__isnull=False, room__isnull=False)
            ), name="reservation_source_valid"),
            models.CheckConstraint(condition=models.Q(state__in=["held", "confirmed", "completed", "review", "canceled"]), name="reservation_state_valid"),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="reservation_time_valid"),
            models.CheckConstraint(condition=models.Q(quantity__gte=1, quantity__lte=10000), name="reservation_quantity_valid"),
        ]
        indexes = [models.Index(fields=["room", "starts_at", "ends_at"], name="reservation_room_times")]

    @property
    def state_label(self):
        if self.state == self.State.HELD and self.hold_until <= timezone.now():
            from core.payments.models import PaymentAttempt
            if self.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists():
                return "Held - checkout needs verification"
            return "Hold expired - not reserved"
        return self.get_state_display()


class ReservationOccurrence(models.Model):
    """A camp's seat claim. State/quantity/payment hold come from its one reservation."""
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="occurrences")
    session = models.ForeignKey(Session, on_delete=models.PROTECT, related_name="camp_occurrences")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.PROTECT)
    room_name = models.CharField(max_length=100, blank=True)
    total_cents = models.PositiveIntegerField()
    deposit_cents = models.PositiveIntegerField()

    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["reservation", "session"], name="camp_occurrence_unique"),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="camp_occurrence_time_valid"),
            models.CheckConstraint(condition=models.Q(deposit_cents__lte=models.F("total_cents")), name="camp_occurrence_amount_valid"),
        ]

    @property
    def total(self):
        from decimal import Decimal
        return Decimal(self.total_cents) / 100


from .lesson_models import (LessonService, LessonTeacher, LessonAvailability, LessonClosure,
                            LessonHousehold, LessonStudent, LessonSeries, LessonOccurrence)
from .credit_models import LessonCreditPolicy, LessonCancellation, LessonCredit, LessonCreditEvent
from .family_models import FamilyScheduleGrant
from .appointment_models import LessonBookingOffer, LessonBooking, LessonBookingNotice
from .message_models import BookingMessage, BookingMessageAttempt
from .integration_models import IntegrationConnection
from .square_models import SquareConnection, SquareAuthorization, SquareLocationMapping
from .visit_models import VisitProgram, VisitSlot, VisitBooking
from .visit_pilot_models import VisitPilotSettings, VisitAccessGrant, VisitScheduleBatch
from .admission_models import AdmissionPolicy, AdmissionCase
from .inventory_models import InventoryCatalog, CatalogVariant, Material, MaterialRequirement, StockLevel, StockAllocation, StockAdjustment, IncomingDelivery
