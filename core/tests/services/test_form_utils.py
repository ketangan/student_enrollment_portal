import pytest

from core.services import form_utils


def sample_form():
    return {
        "sections": [
            {
                "fields": [
                    {
                        "key": "color",
                        "type": "select",
                        "options": [
                            {"value": "r", "label": "Red"},
                            {"value": "g", "label": "Green"},
                            {"value": 10, "label": "Ten"},
                        ],
                    },
                    {
                        "key": "extras",
                        "type": "multiselect",
                        "options": [
                            {"value": "a", "label": "A"},
                            {"value": "b", "label": "B"},
                        ],
                    },
                    {"key": "notes", "type": "text"},
                ]
            }
        ]
    }


def test_build_option_label_map_includes_only_selects_and_multiselects():
    fm = sample_form()
    mapping = form_utils.build_option_label_map(fm)

    assert "color" in mapping
    assert "extras" in mapping
    assert "notes" not in mapping

    # numeric option values become string keys
    assert mapping["color"]["10"] == "Ten"
    assert mapping["color"]["r"] == "Red"


def test_resolve_label_single_found_and_missing():
    fm = sample_form()
    mapping = form_utils.build_option_label_map(fm)

    # found value
    assert form_utils.resolve_label("color", "g", mapping) == "Green"

    # missing label falls back to stringified value
    assert form_utils.resolve_label("color", "z", mapping) == "z"


def test_resolve_label_multiselect_all_found_and_some_missing():
    fm = sample_form()
    mapping = form_utils.build_option_label_map(fm)

    # all found
    assert form_utils.resolve_label("extras", ["a", "b"], mapping) == "A, B"

    # some missing - missing rendered as raw value
    assert form_utils.resolve_label("extras", ["a", "x", 123], mapping) == "A, x, 123"


def test_resolve_label_empty_and_none_return_none():
    fm = sample_form()
    mapping = form_utils.build_option_label_map(fm)

    assert form_utils.resolve_label("color", "", mapping) is None
    assert form_utils.resolve_label("color", None, mapping) is None
