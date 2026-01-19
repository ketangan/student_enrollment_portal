import yaml
from pathlib import Path

from core.services import validation


FIX = Path(__file__).resolve().parents[1] / "fixtures" / "forms"


class DummyPost:
    def __init__(self, data):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def getlist(self, key):
        v = self._data.get(key)
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [v]


def load_form(name: str):
    path = FIX / f"{name}.yaml"
    return yaml.safe_load(path.read_text())


def test_required_field_missing_returns_error():
    form = load_form("required")
    post = DummyPost({})

    cleaned, errors = validation.validate_submission(form, post)

    assert "name" in errors
    assert errors["name"] == "This field is required."
    # optional checkbox missing -> cleaned stores empty string per implementation
    assert "opt_in" in cleaned and cleaned["opt_in"] == ""


def test_type_validations_email_date_number_and_checkbox():
    form = load_form("types")

    # invalid email and invalid date and invalid number
    post = DummyPost({"email": "no-at", "dob": "2020-99-99", "amount": "abc", "agree": "on"})
    cleaned, errors = validation.validate_submission(form, post)

    assert errors["email"] == "Enter a valid email address."
    assert errors["dob"].startswith("Enter a valid date")
    assert errors["amount"] == "Enter a valid number."
    # checkbox validated into cleaned even when others invalid
    assert cleaned.get("agree") is True

    # now with valid inputs
    post2 = DummyPost({"email": "x@y.com", "dob": "2020-01-02", "amount": "12.5", "agree": "off"})
    cleaned2, errors2 = validation.validate_submission(form, post2)

    assert not errors2
    assert cleaned2["email"] == "x@y.com"
    assert isinstance(cleaned2["amount"], float) and cleaned2["amount"] == 12.5
    assert cleaned2["agree"] is False


def test_select_and_multiselect_are_accepted_and_multiselect_returns_list():
    form = load_form("selects")

    # select single value
    post = DummyPost({"choice": "b", "picks": [1, 2]})
    cleaned, errors = validation.validate_submission(form, post)

    assert not errors
    assert cleaned["choice"] == "b"
    # multiselect returns the list as provided
    assert cleaned["picks"] == [1, 2]


def test_empty_optional_fields_result_in_empty_cleaned_values():
    form = load_form("selects")
    post = DummyPost({})
    cleaned, errors = validation.validate_submission(form, post)

    assert not errors
    assert cleaned["choice"] == ""
    assert cleaned["picks"] == []
