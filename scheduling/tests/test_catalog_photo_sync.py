from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404

from core.models import School
from scheduling.management.commands import sync_booking_design_images as sync
from scheduling.models import ChoiceGroup, ChoiceOption, InventoryCatalog, SchedulingProfile
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


def test_photo_sync_is_scoped_preserves_custom_photos_and_handles_renames(stock, monkeypatch):
    retired = "/static/old-sheet.jpg"
    photo = "/static/designs/cat.jpg"
    config = {"design_group": "Design", "retired_design_images": [retired],
              "designs": [{"name": "Cat", "x": "50%", "y": "0%", "image_url": photo}]}
    monkeypatch.setattr(sync, "configuration", lambda slug: config)
    stock.design.reference_image = retired
    stock.design.save()
    stock.cat.label = "Renamed design"
    stock.cat.save()
    stock.school.feature_flags = {"unrelated": True, "booking_demo_inventory": {
        "groups": {"design_group": stock.design.pk}, "design_previews": {
            str(stock.cat.pk): {"x": "50%", "y": "0%"}}}}
    stock.school.save()
    custom = ChoiceOption.objects.create(group=stock.design, label="Cat", image_url="https://school.test/custom.jpg")
    unknown = ChoiceOption.objects.create(group=stock.design, label="Unmapped")
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other-school"))
    group = ChoiceGroup.objects.create(catalog=InventoryCatalog.objects.create(profile=other, name="Other"), name="Design", reference_image=retired)
    foreign = ChoiceOption.objects.create(group=group, label="Cat")
    out = StringIO()
    call_command("sync_booking_design_images", school=stock.school.slug, stdout=out)
    stock.cat.refresh_from_db()
    assert stock.cat.image_url == ""
    assert "Dry run" in out.getvalue()
    call_command("sync_booking_design_images", school=stock.school.slug, apply=True, stdout=StringIO())
    stock.cat.refresh_from_db(); stock.design.refresh_from_db(); stock.school.refresh_from_db()
    custom.refresh_from_db(); unknown.refresh_from_db(); foreign.refresh_from_db(); group.refresh_from_db()
    assert stock.cat.image_url == photo
    assert stock.design.reference_image == ""
    assert stock.school.feature_flags["unrelated"] is True
    assert stock.school.feature_flags["booking_demo_inventory"]["design_previews"][str(stock.cat.pk)]["image_url"] == photo
    assert custom.image_url == "https://school.test/custom.jpg"
    assert unknown.image_url == foreign.image_url == ""
    assert group.reference_image == retired
    out = StringIO()
    call_command("sync_booking_design_images", school=stock.school.slug, apply=True, stdout=out)
    assert out.getvalue().startswith("0 design photos;")


def test_photo_sync_without_binding_uses_configured_name(stock, monkeypatch):
    monkeypatch.setattr(sync, "configuration", lambda slug: {"design_group": "Design",
        "designs": [{"name": "Cat", "image_url": "/static/cat.jpg"}]})
    call_command("sync_booking_design_images", school=stock.school.slug, apply=True, stdout=StringIO())
    stock.cat.refresh_from_db()
    assert stock.cat.image_url == "/static/cat.jpg"


def test_photo_sync_rejects_unknown_school_or_configuration(stock, monkeypatch):
    with pytest.raises(CommandError):
        call_command("sync_booking_design_images", school="missing")
    def missing(slug):
        raise Http404
    monkeypatch.setattr(sync, "configuration", missing)
    with pytest.raises(CommandError):
        call_command("sync_booking_design_images", school=stock.school.slug)
