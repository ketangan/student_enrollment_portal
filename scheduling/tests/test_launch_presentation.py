from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from scheduling.camp_forms import CampForm
from scheduling.reservation_forms import QuoteForm


def test_payment_fields_are_provider_neutral():
    assert QuoteForm().fields["online"].label == "Collect payment online"
    assert CampForm._meta.labels["online"] == "Collect payment online"


@pytest.mark.parametrize("slug", ["first-studio", "second-studio"])
@pytest.mark.parametrize("party", [False, True])
def test_customer_footer_stays_in_its_own_business(slug, party):
    page = render_to_string("scheduling/experience/base.html", {
        "business": SimpleNamespace(slug=slug), "demo": {"name": "Studio"}, "party_experience": party,
    })
    target = reverse("scheduling:party_start" if party else "scheduling:experience_start",
                     kwargs={"school_slug": slug})
    footer = page.split("<footer>", 1)[1]
    assert f'href="{target}"' in footer
    assert "/demo/" not in footer and "plushumi-demo" not in footer


def test_payment_review_return_keeps_signed_destination():
    target = "/bookings/first-studio/pay/signed-customer-link/"
    page = render_to_string("scheduling/experience/base.html", {
        "business": SimpleNamespace(slug="first-studio"), "demo": {"name": "Studio"},
        "payment_review_url": target,
    })
    assert f'<a href="{target}">Your booking</a>' in page
