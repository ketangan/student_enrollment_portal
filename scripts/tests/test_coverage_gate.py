import json
import pytest

from testing.coverage_gate import failures, main


def report(covered=9):
    return {"files": {"new.py": {"summary": {
        "num_statements": 8, "num_branches": 2, "covered_lines": covered-2, "covered_branches": 2,
    }}}}


def test_per_file_gate_cannot_be_hidden_by_total():
    assert failures(report()) == []
    assert failures(report(8)) == ["new.py: 80.00% (minimum 90%)"]
    empty_module = {"summary": {"num_statements": 0, "num_branches": 0, "covered_lines": 0, "covered_branches": 0}}
    assert failures({"files": {"__init__.py": empty_module}}) == []
    with pytest.raises(ValueError):
        failures({"files": {}})


def test_command_fails_and_succeeds(tmp_path, capsys):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report(8)))
    with pytest.raises(SystemExit) as exc:
        main([str(path)])
    assert exc.value.code == 1
    assert "new.py" in capsys.readouterr().err
    path.write_text(json.dumps(report()))
    main([str(path)])
    assert "Every measured module" in capsys.readouterr().out
