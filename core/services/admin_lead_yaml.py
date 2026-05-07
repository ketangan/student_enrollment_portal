# core/services/admin_lead_yaml.py
from __future__ import annotations

from core.models import LEAD_STATUS_CHOICES

# Frozen set of valid Lead model status values — used to reject bad YAML.
_VALID_LEAD_STATUSES: frozenset[str] = frozenset(c[0] for c in LEAD_STATUS_CHOICES)


def get_lead_workflow_filters(config_raw: dict) -> dict:
    """Returns {key: {"label": str, "statuses": list[str]}} or {} if not configured.

    Parses admin.lead_workflow.filters from school YAML.
    Status values are validated against Lead model choices (new, contacted,
    trial_scheduled, enrolled, lost). Invalid statuses are stripped silently;
    filter entries whose statuses list becomes empty after validation are skipped.
    Schools without this block fall back to the generic status dropdown.
    """
    admin_block = (config_raw or {}).get("admin") if isinstance(config_raw, dict) else None
    if not isinstance(admin_block, dict):
        return {}
    workflow = admin_block.get("lead_workflow")
    if not isinstance(workflow, dict):
        return {}
    filters = workflow.get("filters")
    if not isinstance(filters, dict):
        return {}
    result: dict = {}
    for key, val in filters.items():
        if not isinstance(val, dict):
            continue
        label = val.get("label")
        statuses = val.get("statuses")
        if not isinstance(label, str) or not label.strip():
            continue
        if not isinstance(statuses, list) or not statuses:
            continue
        # Strip invalid Lead model statuses to prevent impossible filter tabs.
        valid_statuses = [str(s) for s in statuses if s and str(s) in _VALID_LEAD_STATUSES]
        if not valid_statuses:
            continue
        result[str(key)] = {
            "label": label.strip(),
            "statuses": valid_statuses,
        }
    return result


def get_lead_workflow_transitions(config_raw: dict) -> dict:
    """Returns {from_status: [{"label": str, "status": str}, ...]} or {} if not configured.

    Parses admin.lead_workflow.transitions from school YAML.
    Both from-statuses and target statuses are validated against Lead model
    choices. Invalid from-statuses and invalid target statuses are skipped
    silently. Returns empty dict when workflow block is absent — callers must
    not show inline transition buttons when this is empty.
    """
    admin_block = (config_raw or {}).get("admin") if isinstance(config_raw, dict) else None
    if not isinstance(admin_block, dict):
        return {}
    workflow = admin_block.get("lead_workflow")
    if not isinstance(workflow, dict):
        return {}
    transitions = workflow.get("transitions")
    if not isinstance(transitions, dict):
        return {}
    result: dict = {}
    for from_status, actions in transitions.items():
        # Skip transition entries whose from-status is not a valid Lead status.
        if str(from_status) not in _VALID_LEAD_STATUSES:
            continue
        # enrolled is a terminal status — set only by auto-conversion, never via
        # manual workflow toggle. Skip it as a from-state so no buttons appear on
        # already-enrolled leads.
        if str(from_status) == "enrolled":
            continue
        if not isinstance(actions, list):
            continue
        valid_actions = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            label = action.get("label")
            status = action.get("status")
            if not (isinstance(label, str) and label.strip()):
                continue
            if not (isinstance(status, str) and status.strip()):
                continue
            # Skip actions whose target status is not a valid Lead status.
            if status.strip() not in _VALID_LEAD_STATUSES:
                continue
            # enrolled is terminal — never settable via manual YAML transition button.
            if status.strip() == "enrolled":
                continue
            valid_actions.append({"label": label.strip(), "status": status.strip()})
        if valid_actions:
            result[str(from_status)] = valid_actions
    return result
