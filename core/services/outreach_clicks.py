"""
Read Pontora outreach click activity from Google Sheets.

The outreach click logger writes to the Outreach spreadsheet, not this app's
database. This service keeps the ops portal integration isolated so the portal
can show those clicks without importing code from the outreach repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

CLICK_LOG_VIEW_TAB = "Click_Log_View"
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
LIMIT_OPTIONS = (100, 200, 500, 1000)
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)


class OutreachClickLogError(Exception):
    """Base class for outreach click-log loading errors."""


class OutreachClickLogConfigError(OutreachClickLogError):
    """Raised when the ops portal is missing Google Sheets configuration."""


class OutreachClickLogLoadError(OutreachClickLogError):
    """Raised when Google Sheets is configured but cannot be read."""


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


def _clean(value) -> str:
    return str(value or "").strip()


def _first(row: dict, *keys: str) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


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
    if isinstance(source, Path):
        credentials = Credentials.from_service_account_file(str(source), scopes=GOOGLE_SCOPES)
    else:
        credentials = Credentials.from_service_account_info(source, scopes=GOOGLE_SCOPES)
    return gspread.authorize(credentials)


def _read_click_log_records(tab_name: str = CLICK_LOG_VIEW_TAB) -> list[dict]:
    sheet_id = _clean(getattr(settings, "GOOGLE_SHEET_ID", ""))
    if not sheet_id:
        raise OutreachClickLogConfigError("GOOGLE_SHEET_ID is not configured.")

    try:
        worksheet = _google_client().open_by_key(sheet_id).worksheet(tab_name)
        return worksheet.get_all_records(numericise_ignore=["all"])
    except OutreachClickLogConfigError:
        raise
    except Exception as exc:
        raise OutreachClickLogLoadError(
            f"Could not read {tab_name} from the outreach Google Sheet: {exc}"
        ) from exc


def _decorate_row(row: dict) -> OutreachClickRow:
    timestamp = _first(row, "timestamp", "Timestamp")
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
    rows = [_decorate_row(record) for record in records]
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
