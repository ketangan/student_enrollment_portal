"""The faster CI must retain the required check and fail closed on either partition."""
from pathlib import Path

import yaml


def workflow():
    return yaml.safe_load((Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text())


def test_python_partitions_are_complements_with_isolated_databases():
    job = workflow()["jobs"]["python-tests"]
    assert job["strategy"]["matrix"]["include"] == [
        {"suite": "migrations", "selection": "migration"},
        {"suite": "application", "selection": "not migration"},
    ]
    assert job["services"]["postgres"]["image"] == "postgres:18"
    assert job["strategy"]["fail-fast"] is False
    command = next(s for s in job["steps"] if s.get("name") == "Test with local-only providers")
    assert '-m pytest -k "$TEST_SELECTION"' in command["run"]
    assert "--data-file=coverage-part" in command["run"]


def test_required_gate_cannot_pass_on_failed_partitions_or_partial_coverage():
    job = workflow()["jobs"]["test"]
    assert job["needs"] == "python-tests" and job["if"] == "always()"
    assert job["steps"][0]["run"] == 'test "$PYTHON_RESULT" = success'
    steps = {s.get("name"): s for s in job["steps"]}
    combine = steps["Combine every partition"]["run"]
    for suite in ("migrations", "application"):
        assert f"coverage-parts/coverage-{suite}/coverage-part" in combine
    assert steps["Enforce coverage floor"]["run"].endswith("--fail-under=90")
    assert "testing.coverage_gate" in steps["Check every measured module"]["run"]
    assert "--test-coverage-branches=90" in steps["Test scheduling browser controls"]["run"]


def test_only_superseded_pull_request_runs_are_cancelled():
    assert workflow()["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
