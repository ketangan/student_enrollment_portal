"""
YAML config integrity — parametrized across every school config.

Each test runs once per YAML file in configs/schools/*.yaml.  Any new school
config added to that directory is automatically included.

Checks that:
  - The file parses as valid YAML
  - default_submission_status is a string, not a list
  - default_submission_status is in submission_statuses
  - All submission_workflow transition sources exist in submission_statuses
  - All submission_workflow transition targets exist in submission_statuses
  - All submission_workflow filter statuses exist in submission_statuses
  - All lead_workflow transition targets are valid LEAD_STATUS_CHOICES
  - All lead_workflow filter statuses are valid LEAD_STATUS_CHOICES
  - program_field_key (if set) exists as a field key in the enrollment form
  - application_fee.amount_from_field.field (if set) exists in the form
  - leads.redirect_url_field (if set) exists in leads.fields
  - leads.name_field_key (if set) exists in leads.fields
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

_CONFIGS_DIR = pathlib.Path("configs/schools")
_YAML_FILES = sorted(_CONFIGS_DIR.glob("*.yaml"))
_YAML_SLUGS = [p.stem for p in _YAML_FILES]

# Default statuses used when no admin block is present.
_DEFAULT_SUBMISSION_STATUSES = {"New", "In Review", "Contacted", "Archived"}


def _load(slug: str) -> dict:
    return yaml.safe_load((_CONFIGS_DIR / f"{slug}.yaml").read_text())


def _form_field_keys(raw: dict) -> set[str]:
    """All field keys defined in the enrollment form (single-form schema)."""
    form = raw.get("form") or {}
    return {
        f["key"]
        for section in form.get("sections", [])
        for f in section.get("fields", [])
    }


def _lead_field_keys(raw: dict) -> set[str]:
    return {f["key"] for f in (raw.get("leads") or {}).get("fields", [])}


# ---------------------------------------------------------------------------
# YAML parses without error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_yaml_parses(slug):
    """Every school config must parse as valid YAML without raising."""
    raw = _load(slug)
    assert isinstance(raw, dict), f"{slug}: expected dict at top level"
    assert raw.get("school", {}).get("slug") == slug, (
        f"{slug}: school.slug in YAML must match filename"
    )


# ---------------------------------------------------------------------------
# default_submission_status must be a string scalar, not a list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_default_submission_status_is_string(slug):
    """
    default_submission_status must be a plain string, not a YAML block-sequence list.
    Using the list form produces str(['New']) = \"['New']\" which is not in any
    submission_statuses list, causing the status to silently fall back.
    """
    raw = _load(slug)
    ds = raw.get("admin", {}).get("default_submission_status")
    if ds is None:
        return  # not set — defaults apply, nothing to check
    assert isinstance(ds, str), (
        f"{slug}: default_submission_status must be a scalar string, got {type(ds).__name__}: {ds!r}. "
        "Use 'default_submission_status: New' not a block list."
    )


# ---------------------------------------------------------------------------
# default_submission_status must be in submission_statuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_default_submission_status_in_statuses(slug):
    """default_submission_status (if set) must be present in submission_statuses."""
    raw = _load(slug)
    admin = raw.get("admin", {})
    ds = admin.get("default_submission_status")
    if not ds or not isinstance(ds, str):
        return
    statuses = set(admin.get("submission_statuses") or _DEFAULT_SUBMISSION_STATUSES)
    assert ds in statuses, (
        f"{slug}: default_submission_status '{ds}' not in submission_statuses {sorted(statuses)}"
    )


# ---------------------------------------------------------------------------
# Submission workflow transition integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_submission_transition_sources_in_statuses(slug):
    """Every 'from' status in submission_workflow.transitions must be in submission_statuses."""
    raw = _load(slug)
    admin = raw.get("admin", {})
    statuses = set(admin.get("submission_statuses") or _DEFAULT_SUBMISSION_STATUSES)
    transitions = (admin.get("submission_workflow") or {}).get("transitions", {})
    for from_s in transitions:
        assert from_s in statuses, (
            f"{slug}: transition source '{from_s}' not in submission_statuses {sorted(statuses)}"
        )


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_submission_transition_targets_in_statuses(slug):
    """Every 'to' status in submission_workflow.transitions must be in submission_statuses."""
    raw = _load(slug)
    admin = raw.get("admin", {})
    statuses = set(admin.get("submission_statuses") or _DEFAULT_SUBMISSION_STATUSES)
    transitions = (admin.get("submission_workflow") or {}).get("transitions", {})
    for from_s, actions in transitions.items():
        for action in actions:
            to_s = action["status"]
            assert to_s in statuses, (
                f"{slug}: transition target '{to_s}' (from '{from_s}') not in "
                f"submission_statuses {sorted(statuses)}"
            )


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_submission_filter_statuses_in_statuses(slug):
    """All statuses referenced in submission_workflow.filters must be in submission_statuses."""
    raw = _load(slug)
    admin = raw.get("admin", {})
    statuses = set(admin.get("submission_statuses") or _DEFAULT_SUBMISSION_STATUSES)
    filters = (admin.get("submission_workflow") or {}).get("filters", {})
    for filter_key, fconf in filters.items():
        for s in fconf.get("statuses", []):
            assert s in statuses, (
                f"{slug}: filter '{filter_key}' references status '{s}' not in "
                f"submission_statuses {sorted(statuses)}"
            )


# ---------------------------------------------------------------------------
# Lead workflow transition integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_lead_transition_targets_are_valid_choices(slug):
    """All lead_workflow.transitions targets must be in LEAD_STATUS_CHOICES."""
    import django
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from core.models import LEAD_STATUS_CHOICES
    valid = {c[0] for c in LEAD_STATUS_CHOICES}

    raw = _load(slug)
    transitions = (raw.get("admin", {}).get("lead_workflow") or {}).get("transitions", {})
    for from_s, actions in transitions.items():
        for action in actions:
            to_s = action["status"]
            assert to_s in valid, (
                f"{slug}: lead transition target '{to_s}' (from '{from_s}') is not a valid "
                f"LEAD_STATUS_CHOICES value. Valid: {sorted(valid)}"
            )


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_lead_filter_statuses_are_valid_choices(slug):
    """All statuses in lead_workflow.filters must be valid LEAD_STATUS_CHOICES."""
    import django
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from core.models import LEAD_STATUS_CHOICES
    valid = {c[0] for c in LEAD_STATUS_CHOICES}

    raw = _load(slug)
    filters = (raw.get("admin", {}).get("lead_workflow") or {}).get("filters", {})
    for filter_key, fconf in filters.items():
        for s in fconf.get("statuses", []):
            assert s in valid, (
                f"{slug}: lead filter '{filter_key}' references '{s}' which is not in "
                f"LEAD_STATUS_CHOICES {sorted(valid)}"
            )


# ---------------------------------------------------------------------------
# program_field_key must exist as a form field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_program_field_key_exists_in_form(slug):
    """If program_field_key is set, that key must be defined in the enrollment form sections."""
    raw = _load(slug)
    prog_key = raw.get("program_field_key", "")
    if not prog_key:
        return
    field_keys = _form_field_keys(raw)
    # If the form has no sections (e.g. multi-form-demo) there's nothing to check
    if not field_keys:
        return
    assert prog_key in field_keys, (
        f"{slug}: program_field_key '{prog_key}' not found in any form section field. "
        f"Defined fields: {sorted(field_keys)}"
    )


# ---------------------------------------------------------------------------
# application_fee.amount_from_field.field must exist in the form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_fee_amount_from_field_exists_in_form(slug):
    """
    If application_fee.amount_from_field.field is set, that field key must
    exist in the enrollment form so the fee amount is resolved correctly.
    """
    raw = _load(slug)
    fee = raw.get("application_fee") or {}
    if not fee.get("enabled"):
        return
    amt_field = (fee.get("amount_from_field") or {}).get("field", "")
    if not amt_field:
        return  # flat fee, no field reference
    field_keys = _form_field_keys(raw)
    if not field_keys:
        return
    assert amt_field in field_keys, (
        f"{slug}: application_fee.amount_from_field.field='{amt_field}' not found in form fields. "
        f"Defined fields: {sorted(field_keys)}"
    )


# ---------------------------------------------------------------------------
# leads.redirect_url_field must exist in leads.fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_lead_redirect_url_field_exists_in_lead_fields(slug):
    """
    If leads.redirect_url_field is set, that key must be defined in leads.fields.
    A typo here silently breaks the post-submit redirect for the trial form.
    """
    raw = _load(slug)
    leads = raw.get("leads") or {}
    ruf = leads.get("redirect_url_field", "")
    if not ruf:
        return
    lead_keys = _lead_field_keys(raw)
    assert ruf in lead_keys, (
        f"{slug}: leads.redirect_url_field='{ruf}' not found in leads.fields. "
        f"Defined: {sorted(lead_keys)}"
    )


# ---------------------------------------------------------------------------
# leads.name_field_key must exist in leads.fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_lead_name_field_key_exists_in_lead_fields(slug):
    """
    If leads.name_field_key is set, that key must be defined in leads.fields.
    A mismatch means leads are created with an empty name.
    """
    raw = _load(slug)
    leads = raw.get("leads") or {}
    nfk = leads.get("name_field_key", "")
    if not nfk:
        return
    lead_keys = _lead_field_keys(raw)
    assert nfk in lead_keys, (
        f"{slug}: leads.name_field_key='{nfk}' not found in leads.fields. "
        f"Defined: {sorted(lead_keys)}"
    )


# ---------------------------------------------------------------------------
# School slug consistency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_school_slug_matches_filename(slug):
    """school.slug in the YAML must match the filename (without .yaml extension)."""
    raw = _load(slug)
    yaml_slug = raw.get("school", {}).get("slug", "")
    assert yaml_slug == slug, (
        f"Filename '{slug}.yaml' but school.slug='{yaml_slug}' — these must match"
    )


# ---------------------------------------------------------------------------
# No orphan submission statuses (in statuses list but unreachable via transitions)
# This is a WARNING test — marked xfail for known intentional cases
# ---------------------------------------------------------------------------


_KNOWN_ORPHAN_STATUSES = {
    # Waitlisted is set by capacity auto-placement, not via admin transitions
    ("south-bay-music", "Waitlisted"),
    # Archived in young-minds-la is reachable only via free-form dropdown
    ("young-minds-la", "Archived"),
    # BHG: these are set manually via dropdown; "In Review" has no outgoing
    # transitions; "Needs Follow Up" has outgoing but no incoming transitions
    ("beverly-hills-gymnastics", "In Review"),
    ("beverly-hills-gymnastics", "Needs Follow Up"),
}


@pytest.mark.parametrize("slug", _YAML_SLUGS)
def test_no_unintentional_orphan_submission_statuses(slug):
    """
    Statuses that appear in submission_statuses but are never a target of any
    transition are 'orphan' statuses — the only way to reach them is via the
    free-form admin dropdown.  Known intentional orphans are whitelisted above.
    """
    raw = _load(slug)
    admin = raw.get("admin", {})
    statuses = admin.get("submission_statuses")
    if not statuses:
        return  # uses defaults — no custom transitions to check
    transitions = (admin.get("submission_workflow") or {}).get("transitions", {})
    if not transitions:
        return  # no transitions defined — all statuses are orphans by definition

    all_targets = {a["status"] for actions in transitions.values() for a in actions}
    initial_status = admin.get("default_submission_status", statuses[0])

    orphans = []
    for s in statuses:
        if s == initial_status:
            continue  # initial status is always reachable (it's the starting state)
        if s not in all_targets and (slug, s) not in _KNOWN_ORPHAN_STATUSES:
            orphans.append(s)

    assert not orphans, (
        f"{slug}: statuses unreachable via workflow transitions: {orphans}. "
        "Add an incoming transition or add to _KNOWN_ORPHAN_STATUSES if intentional."
    )
