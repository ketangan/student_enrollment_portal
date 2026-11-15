from types import SimpleNamespace

import pytest
from django.urls import reverse

from scheduling import customer_experience, guest_lists, workshop_followup
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_workshop_selections import pending_booking

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("base_only", [True, False, None])
@pytest.mark.parametrize("surface", ["party_start", "guest_list", "workshop_selections"])
def test_selection_pages_share_school_image_policy(stock, booking, client, monkeypatch, base_only, surface):
    copy = {} if base_only is None else {"base_images_only": base_only}
    def configuration(slug):
        assert slug == stock.school.slug
        return SimpleNamespace(raw={"scheduling": {"customer_experience": copy}})
    monkeypatch.setattr(customer_experience, "load_school_config", configuration)
    stock.cat.image_url = "https://example.invalid/cat.jpg"
    stock.cat.save()
    stock.item.image_url = "https://example.invalid/blue-cat.jpg"
    stock.item.save()
    kwargs = {"school_slug": stock.school.slug}
    if surface == "guest_list":
        kwargs["token"] = guest_lists.token_for(booking)
    elif surface == "workshop_selections":
        item, _ = pending_booking(stock)
        kwargs["token"] = workshop_followup.token_for(item)
    response = client.get(reverse(f"scheduling:{surface}", kwargs=kwargs),
                          {"offering": stock.offering.pk} if surface == "party_start" else {})
    assert response.status_code == 200
    catalog = response.context["party_catalog"]
    assert catalog["base_images_only"] is (base_only is True)
    assert catalog["designs"][0]["image_url"] == stock.cat.image_url
    assert catalog["variants"][0]["image_url"] == stock.item.image_url
    assert catalog["variants"][0]["options"] == stock.item.option_ids


@pytest.mark.parametrize("photo", ["", "https://example.invalid/blue-cat.jpg"])
def test_photo_fallback_is_shared_by_review_and_selection_data(stock, monkeypatch, photo):
    from scheduling.customer_experience import image_policy
    from scheduling.experience_demo.service import selection_previews
    from scheduling.inventory_forms import party_catalog_data
    from scheduling.tests.test_inventory import selections
    monkeypatch.setattr(customer_experience, "load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"customer_experience": {"base_images_only": False, "tint_fallback": False}}}))
    stock.cat.image_url = "https://example.invalid/cat.jpg"
    stock.cat.save()
    stock.item.image_url = photo
    stock.item.save()
    choices = selections(stock)
    choices[0]["image_url"] = stock.cat.image_url
    rows = [{"selections": choices}]
    selection_previews(rows, stock.offering, {"design_group": "Design", "color_group": "Color", "designs": [], "colors": []})
    assert rows[0]["preview"]["image"] == (photo or stock.cat.image_url)
    assert not rows[0]["preview"]["tint_fallback"]
    assert rows[0]["preview"]["exact"] is bool(photo)
    assert party_catalog_data(stock.offering)["tint_fallback"] is False
    assert image_policy(stock.school) == {"base_images_only": False, "tint_fallback": False}
