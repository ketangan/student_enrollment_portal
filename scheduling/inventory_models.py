"""Customer catalog variants and raw-material recipes, separate from seat capacity."""
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower


def validate_catalog_image(value):
    from .models import validate_choice_image
    validate_choice_image(value)


class InventoryCatalog(models.Model):
    profile = models.ForeignKey("SchedulingProfile", on_delete=models.PROTECT, related_name="inventory_catalogs")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(Lower("name"), "profile", name="inventory_catalog_name_unique")]

    def __str__(self):
        return self.name


class CatalogVariant(models.Model):
    catalog = models.ForeignKey(InventoryCatalog, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=160)
    option_ids = models.JSONField(default=list)
    choice_key = models.CharField(max_length=250, editable=False, blank=True)
    is_active = models.BooleanField(default=True)
    image_url = models.CharField(max_length=500, blank=True, validators=[validate_catalog_image])

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(fields=["catalog", "choice_key"], name="inventory_item_choices_unique")]

    def __str__(self):
        return self.name

    def clean(self):
        from .models import ChoiceOption
        if not isinstance(self.option_ids, list) or not self.option_ids or any(type(i) is not int for i in self.option_ids):
            raise ValidationError("Select this design/color combination.")
        groups = set(self.catalog.choice_groups.filter(tracks_stock=True, is_active=True).values_list("pk", flat=True))
        options = list(ChoiceOption.objects.filter(pk__in=self.option_ids, group__catalog=self.catalog,
            group__tracks_stock=True, group__is_active=True))
        if not groups or len(options) != len(groups) or len(self.option_ids) != len(groups) or {o.group_id for o in options} != groups:
            raise ValidationError("Select exactly one option from each stock dimension in this catalog.")
        self.option_ids = sorted(self.option_ids)
        self.choice_key = ":".join(map(str, self.option_ids))
        if self.pk and CatalogVariant.objects.get(pk=self.pk).option_ids != self.option_ids:
            raise ValidationError("An existing variant's combination cannot change. Create another variant.")


class Material(models.Model):
    class Unit(models.TextChoices):
        PIECES = "pieces", "Pieces"
        METERS = "m", "Meters"
        GRAMS = "g", "Grams"
        MILLILITERS = "ml", "Milliliters"

    profile = models.ForeignKey("SchedulingProfile", on_delete=models.PROTECT, related_name="materials")
    name = models.CharField(max_length=160)
    sku = models.CharField(max_length=64)
    unit = models.CharField(max_length=8, choices=Unit.choices, default=Unit.PIECES)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(Lower("sku"), "profile", name="material_sku_unique")]

    def __str__(self):
        return self.name

    def clean(self):
        if self.pk and Material.objects.get(pk=self.pk).unit != self.unit:
            raise ValidationError({"unit": "A material's stock unit cannot change. Create a new material instead."})


def validate_amount(value, unit, *, allow_zero=False):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError("Enter a valid material amount.") from exc
    if (not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0)
            or amount > 1000000 or amount != amount.quantize(Decimal(".001"))
            or (unit == Material.Unit.PIECES and amount != amount.to_integral_value())):
        raise ValidationError("Use up to three decimal places. Pieces must be whole numbers; amounts cannot be negative.")
    return amount


class MaterialRequirement(models.Model):
    variant = models.ForeignKey(CatalogVariant, on_delete=models.CASCADE, related_name="requirements")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="recipes")
    quantity = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal(".001")), MaxValueValidator(1000000)])

    class Meta:
        ordering = ["material__name", "pk"]
        constraints = [models.UniqueConstraint(fields=["variant", "material"], name="recipe_material_unique"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0, quantity__lte=1000000), name="recipe_quantity_valid")]

    def clean(self):
        if self.material_id and self.variant_id and self.material.profile_id != self.variant.catalog.profile_id:
            raise ValidationError("Recipe materials must belong to this business.")
        if self.material_id and self.quantity is not None:
            validate_amount(self.quantity, self.material.unit)


class StockLevel(models.Model):
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="levels")
    location = models.ForeignKey("Location", on_delete=models.PROTECT, related_name="stock_levels")
    on_hand = models.DecimalField(max_digits=10, decimal_places=3, default=0, validators=[MinValueValidator(0), MaxValueValidator(1000000)])
    low_stock_threshold = models.DecimalField(max_digits=10, decimal_places=3, default=0, validators=[MinValueValidator(0), MaxValueValidator(1000000)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["material", "location"], name="material_stock_location_unique"),
            models.CheckConstraint(condition=models.Q(on_hand__gte=0, on_hand__lte=1000000), name="inventory_stock_count_valid"),
            models.CheckConstraint(condition=models.Q(low_stock_threshold__gte=0, low_stock_threshold__lte=1000000), name="inventory_low_threshold_valid")]

    def clean(self):
        if self.material.profile_id != self.location.profile_id:
            raise ValidationError("Stock must belong to a location in the same business.")
        validate_amount(self.on_hand, self.material.unit, allow_zero=True)
        validate_amount(self.low_stock_threshold, self.material.unit, allow_zero=True)


class StockAllocation(models.Model):
    reservation = models.ForeignKey("Reservation", on_delete=models.PROTECT, related_name="stock_allocations")
    level = models.ForeignKey(StockLevel, on_delete=models.PROTECT, related_name="allocations")
    quantity = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal(".001"))])
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["reservation", "level"], name="inventory_allocation_unique"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="inventory_allocation_positive")]


class StockAdjustment(models.Model):
    level = models.ForeignKey(StockLevel, on_delete=models.PROTECT, related_name="adjustments")
    delta = models.DecimalField(max_digits=10, decimal_places=3)
    reason = models.CharField(max_length=250)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
