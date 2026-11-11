from unittest.mock import patch

import pytest
from django.db import DatabaseError, OperationalError
from django.urls import reverse


@pytest.mark.django_db
def test_health_checks_database_without_authentication_or_writes(client, django_assert_num_queries):
    with django_assert_num_queries(1) as queries:
        response = client.get(reverse("healthz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert queries.captured_queries[0]["sql"] == "SELECT 1"
    assert "no-store" in response.headers["Cache-Control"]
    assert "sessionid" not in response.cookies


@pytest.mark.parametrize("error", [DatabaseError, OperationalError])
@pytest.mark.parametrize("failure_point", ["connect", "query"])
def test_database_failure_returns_generic_uncached_503(client, error, failure_point):
    with patch("core.views_health.connection.cursor") as cursor:
        failure = error("sensitive database connection details")
        if failure_point == "connect":
            cursor.side_effect = failure
        else:
            cursor.return_value.__enter__.return_value.execute.side_effect = failure
        response = client.get(reverse("healthz"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "sensitive" not in response.content.decode()
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.django_db
def test_head_checks_readiness_without_response_body(client, django_assert_num_queries):
    with django_assert_num_queries(1):
        response = client.head(reverse("healthz"))
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_health_rejects_unsafe_methods_without_database_access(client, method):
    with patch("core.views_health.connection.cursor") as cursor:
        response = getattr(client, method)(reverse("healthz"))
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET, HEAD"
    cursor.assert_not_called()
