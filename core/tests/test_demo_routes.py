import pytest


@pytest.fixture(autouse=True)
def demo_settings(settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.APP_BASE_URL = "https://app.mypontora.com"


@pytest.mark.parametrize("page", ["", "conservatory/", "studio/", "studio-light/", "studio-navy/"])
def test_retired_sbmc_homepage_concepts_are_unavailable(client, page):
    assert client.get(f"/demo/sbmc-homepage/{page}").status_code == 404


@pytest.mark.parametrize("page", ["", "dedicated-page/", "modal/", "bottom-section/", "link-out/", "standalone-form/"])
def test_sbmc_enrollment_integration_demos_are_preserved(client, page):
    response = client.get(f"/demo/sbmc-demo/{page}")
    assert response.status_code == 200
    assert "demo/sbmc-homepage" not in response.content.decode()
    if page:
        assert response.context["form_url"] == "https://app.mypontora.com/schools/south-bay-music/apply/"
        assert response.context["embed_form_url"] == "https://app.mypontora.com/schools/south-bay-music/apply/?embed=1"
