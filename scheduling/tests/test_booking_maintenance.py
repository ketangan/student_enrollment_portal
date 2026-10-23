from io import StringIO
from unittest.mock import Mock

import pytest
from django.core.management import call_command, CommandError

from scheduling.management.commands import process_booking_maintenance as maintenance


@pytest.mark.parametrize("live", [False, True])
def test_maintenance_reuses_jobs_and_explicit_live_scope(monkeypatch, live):
    run = Mock()
    monkeypatch.setattr(maintenance, "call_command", run)
    output = StringIO()
    call_command("process_booking_maintenance", include_live_recovery=live, limit=12, stdout=output)
    assert [call.args[0] for call in run.call_args_list] == [
        "process_party_notifications", "refresh_square_connections", "reconcile_booking_payments"
    ] + (["reconcile_booking_payments"] if live else [])
    assert all(call.kwargs["limit"] == 12 for call in run.call_args_list)
    assert run.call_args_list[0].kwargs["all_schools"] is True
    assert run.call_args_list[2].kwargs["environment"] == "test"
    if live:
        assert run.call_args_list[3].kwargs["environment"] == "live"
    assert "completed" in output.getvalue()


@pytest.mark.parametrize("failed_index", range(4))
def test_maintenance_continues_after_failure_and_exits_nonzero(monkeypatch, failed_index):
    effects = [None] * 4
    effects[failed_index] = RuntimeError("private-provider-secret")
    run = Mock(side_effect=effects)
    monkeypatch.setattr(maintenance, "call_command", run)
    error = StringIO()
    with pytest.raises(CommandError, match="1 booking maintenance task"):
        call_command("process_booking_maintenance", include_live_recovery=True, stderr=error)
    assert run.call_count == 4
    assert "private-provider-secret" not in error.getvalue()


@pytest.mark.parametrize("limit", [0, 21])
def test_maintenance_rejects_unbounded_work(monkeypatch, limit):
    run = Mock()
    monkeypatch.setattr(maintenance, "call_command", run)
    with pytest.raises(CommandError, match="Limit"):
        call_command("process_booking_maintenance", limit=limit)
    run.assert_not_called()
