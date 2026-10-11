"""Fail unexpected provider traffic before Django starts during pytest runs."""
import socket
import pytest

_patch = pytest.MonkeyPatch()


def check_host(host):
    if host not in {"localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}:
        raise RuntimeError("External network access is disabled in automated tests. Mock the provider.")


def checked_connect(original):
    def connect(sock, address):
        if sock.family not in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("Only loopback IP sockets are allowed in automated tests.")
        check_host(address[0])
        return original(sock, address)
    return connect


def checked_lookup(original):
    def lookup(host, *args, **kwargs):
        check_host(host)
        return original(host, *args, **kwargs)
    return lookup


def deny_datagram(*args, **kwargs):
    raise RuntimeError("Datagram sends are disabled in automated tests.")


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    for name in ("connect", "connect_ex"):
        _patch.setattr(socket.socket, name, checked_connect(getattr(socket.socket, name)))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        _patch.setattr(socket, name, checked_lookup(getattr(socket, name)))
    _patch.setattr(socket.socket, "sendto", deny_datagram)
    if hasattr(socket.socket, "sendmsg"):
        _patch.setattr(socket.socket, "sendmsg", deny_datagram)


def pytest_unconfigure(config):
    _patch.undo()
