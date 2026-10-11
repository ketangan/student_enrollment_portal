from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from core.models import AdminAuditLog, School
from scheduling import inventory
from scheduling.models import CatalogVariant, ChoiceOption, InventoryCatalog, Material, MaterialRequirement, SchedulingMembership, SchedulingProfile
from scheduling.tests.test_inventory import stock, reserve, url, post_form
from scheduling.tests.test_material_recipes import recipe_post

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("value", ["", "#123", "#gggggg", "123456"])
def test_catalog_color_requires_a_valid_hex(stock, value):
    target = url(stock, "inventory_option_new", catalog_pk=stock.catalog.pk, group_pk=stock.color.pk)
    response = stock.client.post(target, {"label": "New color", "swatch": value, "position": 0})
    assert response.status_code == 400
    assert "swatch" in response.context["form"].errors
    assert not stock.color.options.filter(label="New color").exists()


def test_catalog_color_picker_create_edit_and_legacy_photo_preservation(stock):
    s = stock
    s.color.name = "Fabric shades"
    s.color.save(update_fields=["name"])
    target = url(s, "inventory_option_new", catalog_pk=s.catalog.pk, group_pk=s.color.pk)
    page = s.client.get(target)
    assert page.context["title"] == "Add color"
    assert page.context["form"].fields["swatch"].required
    assert b'type="color"' in page.content and b'color-picker.js' in page.content
    assert b'name="image_url"' not in page.content
    assert b'Color hex (optional)' not in page.content
    assert s.client.post(target, {"label": "Coral", "swatch": "#aAbBcC", "position": 2, "is_active": "on"}).status_code == 302
    option = s.color.options.get(label="Coral")
    option.image_url = "https://example.com/legacy.jpg"
    option.save(update_fields=["image_url"])
    edit = url(s, "inventory_option_edit", catalog_pk=s.catalog.pk, group_pk=s.color.pk, pk=option.pk)
    assert s.client.get(edit).context["title"] == "Edit color"
    assert post_form(s.client, edit, {"label": "Coral", "swatch": "#112233", "position": 2,
        "is_active": "on", "image_url": "https://example.com/overwrite.jpg"}).status_code == 302
    option.refresh_from_db()
    assert option.swatch == "#112233"
    assert option.image_url == "https://example.com/legacy.jpg"
    assert AdminAuditLog.objects.filter(object_id=str(option.pk), changes__has_key="swatch").exists()


def test_non_color_options_keep_existing_optional_fields(stock):
    from scheduling.models import ChoiceGroup
    group = ChoiceGroup.objects.create(catalog=stock.catalog, name="Age", position=3)
    target = url(stock, "inventory_option_new", catalog_pk=stock.catalog.pk, group_pk=group.pk)
    page = stock.client.get(target)
    assert not page.context["form"].fields["swatch"].required
    assert b'color-picker.js' not in page.content
    assert stock.client.post(target, {"label": "Adult", "position": 0, "is_active": "on"}).status_code == 302


@pytest.mark.parametrize("editing", [False, True])
def test_compact_design_form_preserves_labels_fields_and_invalid_input(stock, editing):
    s = stock
    target = url(s, "catalog_design_edit", catalog_pk=s.catalog.pk, pk=s.cat.pk) if editing else url(
        s, "catalog_design_new", catalog_pk=s.catalog.pk
    )
    page = s.client.get(target)
    assert b'class="setup-form design-form"' in page.content
    assert b'class="design-main-fields"' in page.content
    assert b'class="design-color-head"' in page.content
    assert f'<label class="sr-only" for="id_image_{s.blue.pk}">Blue photo URL</label>'.encode() in page.content
    assert f'id="color-{s.blue.pk}"'.encode() in page.content

    before = s.catalog.items.count()
    response = post_form(s.client, target, {
        "label": "", "colors": [s.blue.pk], "is_active": "on",
        f"image_{s.blue.pk}": "javascript:alert(1)",
    })
    assert response.status_code == 400
    form = response.context["form"]
    assert "label" in form.errors and f"image_{s.blue.pk}" in form.errors
    assert form.color_rows[0]["selected"]
    assert form[f"image_{s.blue.pk}"].value() == "javascript:alert(1)"
    assert b'class="design-color-head"' in response.content
    assert b'aria-invalid="true"' in response.content
    assert s.catalog.items.count() == before


def test_design_with_no_active_colors_keeps_the_same_editor_layout(stock):
    stock.blue.is_active = False
    stock.blue.save(update_fields=["is_active"])
    response = stock.client.get(url(stock, "catalog_design_new", catalog_pk=stock.catalog.pk))
    assert b'class="setup-form design-form"' in response.content
    assert b"No active colors." in response.content


def test_design_color_images_are_independent_validated_and_audited(stock):
    s = stock
    red = ChoiceOption.objects.create(group=s.color, label="Red", swatch="#ff0000")
    target = url(s, "catalog_design_new", catalog_pk=s.catalog.pk)
    data = {"label": "Bear", "colors": [s.blue.pk, red.pk], "is_active": "on",
            f"image_{s.blue.pk}": "https://example.com/blue-bear.jpg", f"image_{red.pk}": "/static/red-bear.png"}
    assert s.client.post(target, data).status_code == 302
    design = s.design.options.get(label="Bear")
    variants = {next(i for i in v.option_ids if i != design.pk): v for v in s.catalog.items.all() if design.pk in v.option_ids}
    assert variants[red.pk].image_url == "/static/red-bear.png"
    assert variants[s.blue.pk].image_url == data[f"image_{s.blue.pk}"]
    assert not CatalogVariant.objects.get(pk=s.item.pk).image_url  # The blue cat is not changed.
    assert AdminAuditLog.objects.filter(model_label="scheduling.catalogvariant", changes__has_key="image_url").count() == 2
    edit = url(s, "catalog_design_edit", catalog_pk=s.catalog.pk, pk=design.pk)
    page = s.client.get(edit)
    assert b'design-color-grid' in page.content and b'#ff0000' in page.content
    assert page.context["form"][f"image_{red.pk}"].value() == "/static/red-bear.png"
    hidden = {f.name: f.value() for f in page.context["form"].hidden_fields()}
    variants[red.pk].image_url = "https://example.com/new.jpg"; variants[red.pk].save()
    assert s.client.post(edit, {**data, **hidden}).status_code == 400
    assert post_form(s.client, edit, {**data, f"image_{red.pk}": "javascript:alert(1)"}).status_code == 400
    assert post_form(s.client, edit, {**data, "colors": [s.blue.pk]}).status_code == 302
    variants[red.pk].refresh_from_db()
    assert not variants[red.pk].is_active and variants[red.pk].image_url == "https://example.com/new.jpg"


@pytest.mark.parametrize("photo", ["javascript:alert(1)", "http://example.com/bear.jpg", "/static/../secret.png", "/static/a.svg"])
def test_variant_model_rejects_unsafe_image_urls(stock, photo):
    stock.item.image_url = photo
    with pytest.raises(ValidationError): stock.item.full_clean()


def test_customer_payload_includes_exact_photo_and_recipe(stock):
    s = stock; s.item.image_url = "https://example.com/cat-blue.jpg"; s.item.save()
    payload = inventory.availability_payload(s.offering)
    assert payload["variants"][0]["image_url"] == s.item.image_url
    assert payload["variants"][0]["requirements"] == {str(s.material.pk): 1000}
    reserve(s)
    assert inventory.available(s.level) == 4


def test_long_color_photo_urls_fit_revision_and_saving_returns_to_settings(stock):
    s = stock
    ids = [s.blue.pk]
    for index in range(6):
        color = ChoiceOption.objects.create(group=s.color, label=f"Color {index}")
        ids.append(color.pk)
        variant = CatalogVariant(catalog=s.catalog, name=f"Cat {index}", option_ids=[s.cat.pk, color.pk],
            image_url=f"https://example.com/{'a' * 400}-{index}.jpg")
        variant.full_clean(); variant.save()
    target = url(s, "catalog_design_edit", catalog_pk=s.catalog.pk, pk=s.cat.pk)
    page = s.client.get(target)
    assert len(page.context["form"]["colors_revision"].value()) < 2048
    data = {"label": "Cat", "colors": ids, "is_active": "on"}
    for row in page.context["form"].color_rows:
        data[row["image"].name] = row["image"].value()
    assert post_form(s.client, target, data).status_code == 302
    edit_color = url(s, "inventory_option_edit", catalog_pk=s.catalog.pk, group_pk=s.color.pk, pk=s.blue.pk)
    result = post_form(s.client, edit_color, {"label": "Blue", "swatch": "#123456", "position": 0, "is_active": "on"})
    assert result.url == url(s, "catalog_settings", catalog_pk=s.catalog.pk)


def test_catalog_settings_and_color_posts_are_tenant_and_role_scoped(stock):
    s = stock
    foreign = SchedulingProfile.objects.create(school=School.objects.create(slug="foreign-catalog"))
    catalog = InventoryCatalog.objects.create(profile=foreign, name="Private")
    assert s.client.get(url(s, "catalog_settings", catalog_pk=catalog.pk)).status_code == 404
    assert s.client.post(url(s, "catalog_settings", catalog_pk=s.catalog.pk), {}).status_code == 405
    target = url(s, "catalog_design_new", catalog_pk=s.catalog.pk)
    another = InventoryCatalog.objects.create(profile=s.profile, name="Another")
    from scheduling.models import ChoiceGroup
    group = ChoiceGroup.objects.create(catalog=another, name="Color")
    color = ChoiceOption.objects.create(group=group, label="Private color")
    assert s.client.post(target, {"label": "Dog", "colors": [color.pk]}).status_code == 400
    SchedulingMembership.objects.filter(user=s.user).update(role="viewer")
    assert s.client.post(target, {"label": "Dog", "colors": [s.blue.pk]}).status_code == 404


@pytest.mark.parametrize("quantity,available,threshold,active,status", [
    (5,5,0,True,"available"), (5,2,2,True,"low"), (5,3,2,True,"available"),
    (5,0,2,True,"reserved"), (0,0,2,True,"empty"), (5,5,0,False,"inactive"),
])
def test_stock_status_uses_available_quantity_and_configured_threshold(stock, quantity, available, threshold, active, status):
    s = stock; s.level.on_hand = quantity; s.level.low_stock_threshold = threshold; s.level.material.is_active = active
    assert inventory.stock_status(s.level, available)[0] == status


def test_threshold_change_is_audited_stale_safe_and_does_not_consume_stock(stock):
    s = stock; target = url(s, "inventory_stock", pk=s.level.pk)
    data = {"mode": "receive", "on_hand": 0, "previous": 5, "reason": "Set reorder alert",
            "low_stock_threshold": 4, "previous_threshold": 0}
    assert s.client.post(target, data).status_code == 302
    s.level.refresh_from_db()
    assert s.level.low_stock_threshold == 4 and s.level.on_hand == 5
    assert not s.level.adjustments.exists()
    assert AdminAuditLog.objects.filter(model_label="scheduling.stocklevel", changes__has_key="low_stock_threshold").exists()
    assert s.client.post(target, {**data, "low_stock_threshold": 3}).status_code == 400
    assert s.client.post(target, {**data, "low_stock_threshold": ".5", "previous_threshold": 4}).status_code == 400
    reserve(s)
    page = s.client.get(url(s, "settings"))
    assert page.context["stock"][0]["status"] == "low"
    assert b"Low stock" in page.content and b"Alert at 4 pieces" in page.content
    assert inventory.available(s.level) == 4
    assert s.client.post(target, {**data, "low_stock_threshold": 0, "previous_threshold": 4}).status_code == 302
    assert s.client.post(target, {**data, "low_stock_threshold": 0}).status_code == 302


def test_fractional_threshold_uses_material_units(stock):
    s = stock; material = Material.objects.create(profile=s.profile, name="Fleece", sku="FL", unit="m")
    from scheduling.models import StockLevel
    level = StockLevel.objects.create(material=material, location=s.location, on_hand=3)
    inventory.adjust(s.user, s.school.slug, level.pk, on_hand=3, previous=3, reason="Alert",
        low_stock_threshold=Decimal(".125"), previous_threshold=0)
    level.refresh_from_db(); assert level.low_stock_threshold == Decimal(".125")
    level.full_clean()
    level.low_stock_threshold = -1
    with pytest.raises(ValidationError): level.full_clean()


def test_delete_recipe_row_preserves_other_rows_and_saved_reservation(stock):
    s = stock; booking = reserve(s); saved_materials = booking.snapshot["materials"]
    other = Material.objects.create(profile=s.profile, name="Eyes", sku="EYE")
    added = MaterialRequirement.objects.create(variant=s.item, material=other, quantity=2)
    target, data = recipe_post(s)
    page = s.client.get(target)
    assert b'class="icon-button recipe-delete"' in page.content
    assert b'type="checkbox"' not in page.content
    for i in range(data["recipe-TOTAL_FORMS"]):
        if data[f"recipe-{i}-id"] == added.pk: data[f"recipe-{i}-DELETE"] = "on"
    assert s.client.post(target, data).status_code == 302
    assert list(s.item.requirements.values_list("material_id", flat=True)) == [s.material.pk]
    booking.refresh_from_db()
    assert booking.snapshot["materials"] == saved_materials
    assert booking.stock_allocations.get().quantity == 1


def test_scheduling_calendar_assets_do_not_change_enrollment_base(stock):
    from pathlib import Path
    s = stock
    page = s.client.get(url(s, "workshop_detail", pk=s.offering.pk))
    assert b'scheduling/date-picker.js' in page.content
    assert 'date-picker' not in Path('templates/school_admin/base.html').read_text()
