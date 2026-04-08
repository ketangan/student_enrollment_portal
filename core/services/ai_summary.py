# core/services/ai_summary.py
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _build_submission_text(submission_data: dict, form_cfg: dict) -> str:
    """Format submission data as labelled key-value pairs for Claude."""
    label_map: dict[str, str] = {}
    for section in (form_cfg.get("sections") or []):
        for field in (section.get("fields") or []):
            key = field.get("key")
            if key:
                label_map[key] = field.get("label", key)

    lines = []
    for key, value in (submission_data or {}).items():
        # Skip waiver audit metadata keys
        if any(key.endswith(suffix) for suffix in ("__at", "__ip", "__text", "__link_url")):
            continue
        label = label_map.get(key, key.replace("_", " ").title())
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "Yes" if value else "No"
        lines.append(f"{label}: {value}")

    return "\n".join(lines)


def _build_prompt(submission_text: str, school_name: str, criteria: list[str]) -> str:
    criteria_section = ""
    if criteria:
        criteria_list = "\n".join(f"- {c}" for c in criteria)
        criteria_section = (
            f"\n\nAlso assess the applicant against these criteria. "
            f"Include a brief rating and note for each in criteria_scores:\n{criteria_list}"
        )
    return (
        f"School: {school_name}\n\n"
        f"Application:\n{submission_text}"
        f"{criteria_section}"
    )


def generate_ai_summary(
    *,
    submission_data: dict,
    school_name: str,
    form_cfg: dict,
    criteria: list[str] | None = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Calls Claude API to generate a summary of the submission.

    Returns (result, error_message):
      - On success: ({"summary": str, "criteria_scores": [...]}, None)
      - On failure: (None, human-readable error string)

    YAML config (optional, read by caller and passed via criteria):
      ai_summary:
        criteria:
          - "Prior dance experience"
          - "Medical or allergy concerns"
    """
    from django.conf import settings

    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured; skipping AI summary generation")
        return None, "ANTHROPIC_API_KEY is not configured on this server."

    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed; run: pip install anthropic")
        return None, "The anthropic Python package is not installed on this server."

    submission_text = _build_submission_text(submission_data, form_cfg)
    if not submission_text.strip():
        logger.warning("No submission data to summarize")
        return None, "This submission has no data to summarize."

    # Safety truncation — avoid very large prompts
    if len(submission_text) > 3000:
        submission_text = submission_text[:3000] + "\n[truncated]"

    prompt = _build_prompt(submission_text, school_name, criteria or [])

    system = (
        "You are reviewing a student enrollment application. "
        "Respond with valid JSON only — no markdown fences, no explanation, just the JSON object.\n"
        'Schema: {"summary": "<3-sentence summary of the applicant>", '
        '"criteria_scores": [{"criterion": "...", "assessment": "...", "note": "..."}]}\n'
        "criteria_scores should be an empty array [] if no criteria were provided. "
        "Be concise, factual, and professional."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (message.content[0].text or "").strip()
        # Strip markdown fences if model adds them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        if "summary" not in result:
            return {"summary": raw, "criteria_scores": []}, None
        return result, None
    except json.JSONDecodeError:
        # Model returned non-JSON; wrap as plain summary
        return {"summary": raw, "criteria_scores": []}, None
    except Exception as exc:
        logger.exception("Failed to generate AI summary via Claude API")
        # Anthropic SDK errors carry a structured body; extract just the message field.
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            nested = body.get("error", {})
            if isinstance(nested, dict) and nested.get("message"):
                return None, nested["message"]
        return None, str(exc) or "Unexpected error calling the Claude API."
