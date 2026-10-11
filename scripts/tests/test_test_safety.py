import socket
from unittest.mock import Mock

import pytest

from testing import network
from testing.safety import test_database as database_config, test_environment as safe_environment


@pytest.mark.parametrize("url", ["postgres://localhost/prod", "postgres://remote.example/test_pontora_ci", "sqlite:///:memory:",
    "postgres://localhost/test_pontora_ci?host=remote", "postgres://localhost/test_pontora_ci#x", "postgres://localhost/test_pontora_%20"])
def test_rejects_unsafe_database_targets(url):
    with pytest.raises(ValueError):
        database_config(url)


def test_disposable_database_and_environment():
    config = database_config("postgres://user:secret@127.0.0.1:5432/test_pontora_ci")
    assert config["TEST"]["NAME"] == "test_pontora_ci"
    original = {"STRIPE_SECRET_KEY_LIVE": "real", "ANTHROPIC_API_KEY": "real", "BOOKINGS_INTEGRATION_KEYS": "real",
        "PATH": "/bin", "DJANGO_ENV": "production"}
    clean = safe_environment(original)
    assert original["STRIPE_SECRET_KEY_LIVE"] == "real"
    assert clean["STRIPE_SECRET_KEY_LIVE"] == clean["ANTHROPIC_API_KEY"] == clean["BOOKINGS_INTEGRATION_KEYS"] == ""
    assert clean["PATH"] == "/bin" and clean["DJANGO_ENV"] == "local"


@pytest.mark.parametrize("host", ["api.stripe.com", "8.8.8.8", "127.0.0.1.evil.example", "localhost.", None])
def test_external_dns_and_connections_denied_without_calling_network(host):
    original = Mock()
    with pytest.raises(RuntimeError):
        network.checked_lookup(original)(host, 443)
    with pytest.raises(RuntimeError):
        network.checked_connect(original)(Mock(family=socket.AF_INET), (host, 443))
    original.assert_not_called()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", b"localhost"])
def test_loopback_delegates_to_original(host):
    original = Mock(return_value="local")
    assert network.checked_lookup(original)(host, 5432) == "local"
    assert network.checked_connect(original)(Mock(family=socket.AF_INET6), (host, 5432)) == "local"


def test_no_datagrams_or_unix_socket_bypass():
    with pytest.raises(RuntimeError):
        network.deny_datagram()
    with pytest.raises(RuntimeError):
        network.checked_connect(Mock())(Mock(family=socket.AF_UNIX), "/tmp/socket")


def test_guard_is_installed_for_real_socket_methods():
    with pytest.raises(RuntimeError):
        socket.getaddrinfo("api.stripe.com", 443)
    with socket.socket() as sock:
        with pytest.raises(RuntimeError):
            sock.connect_ex(("8.8.8.8", 443))
        with pytest.raises(RuntimeError):
            sock.connect(("8.8.8.8", 443))
        with pytest.raises(RuntimeError):
            sock.sendto(b"blocked", ("8.8.8.8", 443))
