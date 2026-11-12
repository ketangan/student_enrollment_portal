from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from django.core.management import CommandError
from django.db import OperationalError, ProgrammingError

from core.management.commands import check_portal_deployment as module


@pytest.fixture
def probe(monkeypatch):
    options = {"sslmode": "require", "connect_timeout": 90}
    database = SimpleNamespace(vendor="postgresql", settings_dict={"OPTIONS": options},
        ensure_connection=Mock(), close=Mock())
    monkeypatch.setattr(module, "connection", database)
    pause = Mock()
    monkeypatch.setattr(module, "sleep", pause)
    output, errors = StringIO(), StringIO()
    command = module.Command(stdout=output, stderr=errors)
    return SimpleNamespace(command=command, db=database, options=options, pause=pause,
        output=output, errors=errors)


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_connection_retries_are_bounded_and_options_restored(probe, failures):
    observed = []
    def connect():
        observed.append(dict(probe.db.settings_dict["OPTIONS"]))
        if len(observed) <= failures:
            raise OperationalError("private connection details")
    probe.db.ensure_connection.side_effect = connect
    probe.command.connect_database()
    assert observed == [{"sslmode": "require", "connect_timeout": 10}] * (failures + 1)
    assert probe.db.close.call_count == failures
    assert probe.pause.call_args_list == [call(5)] * failures
    assert probe.db.settings_dict["OPTIONS"] is probe.options
    assert "private connection details" not in probe.errors.getvalue()


def test_exhaustion_preserves_original_error_and_does_not_sleep_again(probe):
    failure = OperationalError("unreachable")
    probe.db.ensure_connection.side_effect = failure
    with pytest.raises(OperationalError) as caught:
        probe.command.connect_database()
    assert caught.value is failure
    assert probe.db.ensure_connection.call_count == probe.db.close.call_count == 3
    assert probe.pause.call_count == 2
    assert probe.db.settings_dict["OPTIONS"] is probe.options


@pytest.mark.parametrize("code,retry", [("08001", True), ("57P03", True), ("28P01", False), ("3D000", False)])
def test_only_connection_sqlstates_are_retried(probe, code, retry):
    cause = Exception("database failure")
    cause.pgcode = code
    failure = OperationalError("database failure")
    failure.__cause__ = cause
    probe.db.ensure_connection.side_effect = [failure, None]
    if retry:
        probe.command.connect_database()
        assert probe.db.ensure_connection.call_count == 2
    else:
        with pytest.raises(OperationalError):
            probe.command.connect_database()
        assert probe.db.ensure_connection.call_count == 1
        probe.pause.assert_not_called()
    assert probe.db.settings_dict["OPTIONS"] is probe.options


def test_non_connection_errors_are_not_retried(probe):
    probe.db.ensure_connection.side_effect = ProgrammingError("bad configuration")
    with pytest.raises(ProgrammingError):
        probe.command.connect_database()
    probe.pause.assert_not_called()
    probe.db.close.assert_not_called()
    assert probe.db.settings_dict["OPTIONS"] is probe.options


def test_other_database_backends_do_not_receive_postgres_options(probe):
    probe.db.vendor = "sqlite"
    def connect():
        assert probe.db.settings_dict["OPTIONS"] is probe.options
    probe.db.ensure_connection.side_effect = connect
    probe.command.connect_database()


@pytest.mark.parametrize("environment", ["production", "shared"])
def test_existing_non_demo_command_behavior_is_unchanged(probe, settings, environment):
    settings.PORTAL_DEPLOYMENT_ENV = environment
    probe.command.handle()
    probe.db.ensure_connection.assert_not_called()


def test_demo_safety_failure_precedes_connection_attempt(probe, settings):
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    settings.STRIPE_MODE = "live"
    with pytest.raises(CommandError, match="sandbox"):
        probe.command.handle()
    probe.db.ensure_connection.assert_not_called()


def test_no_safety_queries_or_success_after_connection_failure(probe, settings, monkeypatch):
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    settings.STRIPE_MODE = "test"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED = False
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_SECRET = ""
    school = Mock()
    monkeypatch.setattr(module, "School", school)
    probe.db.ensure_connection.side_effect = OperationalError("unreachable")
    with pytest.raises(OperationalError):
        probe.command.handle()
    school.objects.filter.assert_not_called()
    assert probe.output.getvalue() == ""


@pytest.mark.parametrize("query_failure", [False, True])
def test_recovered_connection_still_checks_isolation_without_replaying_queries(probe, settings, monkeypatch, query_failure):
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    settings.STRIPE_MODE = "test"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED = False
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_SECRET = ""
    school = Mock()
    monkeypatch.setattr(module, "School", school)
    probe.db.ensure_connection.side_effect = [OperationalError("timeout"), None]
    if query_failure:
        school.objects.filter.side_effect = OperationalError("query failed")
    else:
        school.objects.filter.return_value.exists.return_value = True
    with pytest.raises(OperationalError if query_failure else CommandError):
        probe.command.handle()
    school.objects.filter.assert_called_once_with(is_demo=False)
    assert probe.db.ensure_connection.call_count == 2
    assert probe.output.getvalue() == ""
