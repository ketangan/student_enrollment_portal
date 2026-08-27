"""
Read Pontora outreach click activity from Google Sheets.

The outreach click logger writes to the Outreach spreadsheet, not this app's
database. This service keeps the ops portal integration isolated so the portal
can show those clicks without importing code from the outreach repo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

CLICK_LOG_VIEW_TAB = "Click_Log_View"
CLICK_LOG_TAB = "Click_Log"
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
LIMIT_OPTIONS = (100, 200, 500, 1000)
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
)
DELETE_TOKEN_SEPARATOR = ":"
KEY_FIELDS = (
    "timestamp",
    "lead_id",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "path",
    "gesture_type",
    "tracking_kind",
    "user_agent",
    "referer",
)


class OutreachClickLogError(Exception):
    """Base class for outreach click-log loading errors."""


class OutreachClickLogConfigError(OutreachClickLogError):
    """Raised when the ops portal is missing Google Sheets configuration."""


class OutreachClickLogLoadError(OutreachClickLogError):
    """Raised when Google Sheets is configured but cannot be read."""


@dataclass(frozen=True)
class OutreachClickDeleteResult:
    deleted: int
    skipped: int
    requested: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutreachClickRow:
    timestamp_raw: str
    timestamp_pacific: str
    school_name: str
    website: str
    path: str
    gesture_type: str
    lead_id: str
    utm_source: str
    utm_medium: str
    utm_campaign: str
    tracking_kind: str
    referer: str
    sort_timestamp: datetime
    raw_sheet_row: int | None = None
    delete_token: str = ""


def _clean(value) -> str:
    return str(value or "").strip()


def _first(row: dict, *keys: str) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _row_dict(headers: list[str], values: list[str]) -> dict:
    return {
        header: values[idx] if idx < len(values) else ""
        for idx, header in enumerate(headers)
    }


def _safe_limit(raw_limit) -> int:
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _parse_timestamp(raw_value) -> datetime | None:
    if isinstance(raw_value, datetime):
        return raw_value
    if isinstance(raw_value, date):
        return datetime.combine(raw_value, time.min)

    raw = _clean(raw_value)
    if not raw:
        return None

    iso_candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%y %I:%M %p",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _pacific_timezone() -> ZoneInfo:
    return ZoneInfo(getattr(settings, "TIME_ZONE", "America/Los_Angeles"))


def _to_pacific(dt: datetime) -> datetime:
    tz = _pacific_timezone()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _format_pacific_timestamp(raw_value) -> str:
    parsed = _parse_timestamp(raw_value)
    if not parsed:
        return _clean(raw_value)
    return _to_pacific(parsed).strftime("%Y-%m-%d %I:%M %p %Z")


def _timestamp_sort_value(raw_value) -> datetime:
    parsed = _parse_timestamp(raw_value)
    if not parsed:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _to_pacific(parsed).astimezone(timezone.utc)


def _credential_source():
    path = _clean(getattr(settings, "GOOGLE_SHEETS_CREDENTIALS_PATH", ""))
    if path:
        credential_path = Path(path)
        if credential_path.exists():
            return credential_path

    raise OutreachClickLogConfigError(
        "Google Sheets credentials are not configured for the ops portal. "
        "Set GOOGLE_SHEETS_CREDENTIALS_PATH."
    )


@lru_cache(maxsize=1)
def _google_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise OutreachClickLogConfigError(
            "Google Sheets dependencies are not installed. "
            "Install gspread and google-auth."
        ) from exc

    source = _credential_source()
    credentials = Credentials.from_service_account_file(str(source), scopes=GOOGLE_SCOPES)
    return gspread.authorize(credentials)


def _worksheet(tab_name: str):
    sheet_id = _clean(getattr(settings, "GOOGLE_SHEET_ID", ""))
    if not sheet_id:
        raise OutreachClickLogConfigError("GOOGLE_SHEET_ID is not configured.")
    return _google_client().open_by_key(sheet_id).worksheet(tab_name)


def _read_click_log_records(tab_name: str = CLICK_LOG_VIEW_TAB) -> list[dict]:
    try:
        return _worksheet(tab_name).get_all_records(numericise_ignore=["all"])
    except OutreachClickLogConfigError:
        raise
    except Exception as exc:
        raise OutreachClickLogLoadError(
            f"Could not read {tab_name} from the outreach Google Sheet: {exc}"
        ) from exc


def _read_raw_click_log_records() -> list[tuple[int, dict]]:
    try:
        values = _worksheet(CLICK_LOG_TAB).get_all_values()
    except OutreachClickLogConfigError:
        raise
    except Exception as exc:
        raise OutreachClickLogLoadError(
            f"Could not read {CLICK_LOG_TAB} from the outreach Google Sheet: {exc}"
        ) from exc

    if not values:
        return []

    headers = values[0]
    return [
        (sheet_row, _row_dict(headers, row_values))
        for sheet_row, row_values in enumerate(values[1:], start=2)
    ]


def _key_for_record(row: dict) -> tuple[str, ...]:
    return tuple(_first(row, field, field.title().replace("_", " ")) for field in KEY_FIELDS)


def _delete_token(raw_sheet_row: int, raw_record: dict) -> str:
    payload = {
        "row": int(raw_sheet_row),
        "key": _key_for_record(raw_record),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _delete_value(raw_sheet_row: int, raw_record: dict) -> str:
    return f"{int(raw_sheet_row)}{DELETE_TOKEN_SEPARATOR}{_delete_token(raw_sheet_row, raw_record)}"


def _parse_delete_value(value: str) -> tuple[int, str] | None:
    raw = _clean(value)
    if DELETE_TOKEN_SEPARATOR not in raw:
        return None
    row_number, token = raw.split(DELETE_TOKEN_SEPARATOR, 1)
    try:
        parsed_row_number = int(row_number)
    except ValueError:
        return None
    token = token.strip()
    if parsed_row_number < 2 or not token:
        return None
    return parsed_row_number, token


def _raw_row_delete_values(raw_rows: list[tuple[int, dict]]) -> dict[tuple[str, ...], list[tuple[int, str]]]:
    by_key: dict[tuple[str, ...], list[tuple[int, str]]] = {}
    for sheet_row, raw_record in raw_rows:
        key = _key_for_record(raw_record)
        by_key.setdefault(key, []).append((sheet_row, _delete_value(sheet_row, raw_record)))
    return by_key


def _matched_raw_row(
    raw_rows_by_key: dict[tuple[str, ...], list[tuple[int, str]]],
    view_record: dict,
) -> tuple[int, str] | None:
    matches = raw_rows_by_key.get(_key_for_record(view_record), [])
    if not matches:
        return None
    return matches.pop(0)


def _decorate_row(row: dict, raw_row: tuple[int, str] | None = None) -> OutreachClickRow:
    timestamp = _first(row, "timestamp", "Timestamp")
    raw_sheet_row = raw_row[0] if raw_row else None
    delete_token = raw_row[1] if raw_row else ""
    return OutreachClickRow(
        timestamp_raw=timestamp,
        timestamp_pacific=_format_pacific_timestamp(timestamp),
        school_name=_first(row, "school_name", "school", "School", "School Name"),
        website=_first(row, "website", "Website"),
        path=_first(row, "path", "Path"),
        gesture_type=_first(row, "gesture_type", "gesture", "Gesture"),
        lead_id=_first(row, "lead_id", "utm_content", "lead", "Lead ID"),
        utm_source=_first(row, "utm_source", "source"),
        utm_medium=_first(row, "utm_medium", "medium"),
        utm_campaign=_first(row, "utm_campaign", "campaign"),
        tracking_kind=_first(row, "tracking_kind", "kind"),
        referer=_first(row, "referer", "referrer"),
        sort_timestamp=_timestamp_sort_value(timestamp),
        raw_sheet_row=raw_sheet_row,
        delete_token=delete_token,
    )


def _row_matches(row: OutreachClickRow, query: str, gesture_type: str) -> bool:
    if gesture_type and row.gesture_type.lower() != gesture_type.lower():
        return False

    needle = query.strip().lower()
    if not needle:
        return True

    haystack = " ".join(
        (
            row.school_name,
            row.website,
            row.path,
            row.gesture_type,
            row.lead_id,
            row.utm_source,
            row.utm_medium,
            row.utm_campaign,
            row.tracking_kind,
            row.referer,
        )
    ).lower()
    return needle in haystack


def build_click_log_context(*, q: str = "", gesture_type: str = "", limit=DEFAULT_LIMIT) -> dict:
    records = _read_click_log_records(CLICK_LOG_VIEW_TAB)
    raw_rows_by_key = _raw_row_delete_values(_read_raw_click_log_records())
    rows = [
        _decorate_row(record, _matched_raw_row(raw_rows_by_key, record))
        for record in records
    ]
    rows.sort(key=lambda row: row.sort_timestamp, reverse=True)

    gesture_choices = sorted({row.gesture_type for row in rows if row.gesture_type})
    filtered = [row for row in rows if _row_matches(row, q, gesture_type)]
    safe_limit = _safe_limit(limit)

    return {
        "rows": filtered[:safe_limit],
        "total_count": len(filtered),
        "sheet_total_count": len(rows),
        "q": q,
        "gesture_filter": gesture_type,
        "gesture_choices": gesture_choices,
        "limit": safe_limit,
        "limit_options": LIMIT_OPTIONS,
        "source_tab": CLICK_LOG_VIEW_TAB,
    }


def delete_click_log_rows(delete_values: list[str]) -> OutreachClickDeleteResult:
    requested = len(delete_values)
    parsed_values = [_parse_delete_value(value) for value in delete_values]
    parsed = [value for value in parsed_values if value is not None]
    skipped = requested - len(parsed)
    errors: list[str] = []

    if not parsed:
        return OutreachClickDeleteResult(
            deleted=0,
            skipped=skipped,
            requested=requested,
            errors=tuple(errors),
        )

    try:
        worksheet = _worksheet(CLICK_LOG_TAB)
        values = worksheet.get_all_values()
    except OutreachClickLogConfigError:
        raise
    except Exception as exc:
        raise OutreachClickLogLoadError(
            f"Could not load {CLICK_LOG_TAB} before deleting rows: {exc}"
        ) from exc

    if not values:
        return OutreachClickDeleteResult(
            deleted=0,
            skipped=requested,
            requested=requested,
            errors=("Click_Log is empty.",),
        )

    headers = values[0]
    valid_rows: list[int] = []
    seen_rows: set[int] = set()
    for row_number, expected_token in parsed:
        if row_number in seen_rows:
            skipped += 1
            continue
        seen_rows.add(row_number)

        if row_number > len(values):
            skipped += 1
            errors.append(f"Row {row_number} no longer exists.")
            continue

        raw_record = _row_dict(headers, values[row_number - 1])
        current_token = _delete_token(row_number, raw_record)
        if current_token != expected_token:
            skipped += 1
            errors.append(f"Row {row_number} changed before deletion; refresh and try again.")
            continue

        valid_rows.append(row_number)

    deleted = 0
    for row_number in sorted(valid_rows, reverse=True):
        try:
            worksheet.delete_rows(row_number)
            deleted += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"Could not delete row {row_number}: {exc}")

    return OutreachClickDeleteResult(
        deleted=deleted,
        skipped=skipped,
        requested=requested,
        errors=tuple(errors),
    )
