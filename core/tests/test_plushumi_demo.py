import re

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse


@pytest.fixture(autouse=True)
def public_demo_settings(settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver", "demo.mypontora.com"]


@pytest.mark.parametrize("screen", range(6))
def test_plushumi_public_storyboard_without_database(client, screen):
    response = client.get(
        reverse("demo_index", kwargs={"demo_slug": "plushumi"}),
        {"screen": screen},
        HTTP_HOST="demo.mypontora.com",
    )
    assert response.status_code == 200
    assert "demo/plushumi/index.html" in [t.name for t in response.templates]
    content = response.content.decode()
    assert 'content="noindex, nofollow"' in content
    assert "CUSTOMER SCREEN" in content
    assert "PLUSHIE ADMIN SCREEN" in content
    assert "Purple cat plushie preview" in content
    assert "file:///" not in content
    assert "/schools/" not in content
    assert "{%" not in content


def test_plushumi_assets_are_deployable(client):
    content = client.get("/demo/plushumi/").content.decode()
    assets = set(re.findall(r"/static/(demo/plushumi/assets/[\w.-]+)", content))
    assert assets == {
        "demo/plushumi/assets/regular.ttf",
        "demo/plushumi/assets/bold.ttf",
        "demo/plushumi/assets/logo.png",
        "demo/plushumi/assets/designs.jpg",
        "demo/plushumi/assets/workshop.jpg",
        "demo/plushumi/assets/lucide.js",
    }
    for asset in assets:
        assert finders.find(asset), asset


@pytest.mark.parametrize("path", ["/demo/plushumi/checkout/", "/demo/not-a-demo/"])
def test_unknown_demo_pages_remain_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "slug,school",
    [("sbmc-demo", "south-bay-music"), ("kidworks-demo", "kidworks-childrens-center")],
)
def test_existing_school_demos_keep_their_enrollment_links(client, settings, slug, school):
    settings.APP_BASE_URL = "https://app.mypontora.com"
    assert client.get(f"/demo/{slug}/").status_code == 200
    response = client.get(f"/demo/{slug}/standalone-form/")
    assert response.status_code == 200
    assert response.context["form_url"] == f"https://app.mypontora.com/schools/{school}/apply/"
    assert "plushumi" not in response.content.decode().lower()
