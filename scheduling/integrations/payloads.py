"""A provider-neutral booking snapshot and an offline Notion projection."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
import json
import uuid

from django.core.exceptions import ValidationError


# Types are explicit: a destination's arbitrary fields never become executable code.
MAPPING_FIELDS = {
    "title": ("Booking title", "title", True),
    "booking_id": ("Booking reference", "rich_text", True),
    "contact_name": ("Customer name", "rich_text", False),
    "contact_email": ("Customer email", "email", False),
    "schedule": ("Start and end time", "date", False),
    "status": ("Booking status", "rich_text", False),
    "participants": ("Participants and their selections", "rich_text", True),
}
NOTION_VERSION = "2026-03-11"


@dataclass(frozen=True)
class Participant:
    name: str
    selections: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class BookingSnapshot:
    reference: str
    title: str
    contact_name: str
    contact_email: str
    starts_at: datetime
    ends_at: datetime
    status: str
    participants: tuple[Participant, ...]


@dataclass(frozen=True)
class Projection:
    payload: dict
    rows: tuple[dict, ...]
    api_version: str


class DestinationAdapter(Protocol):
    def project(self, booking: BookingSnapshot, configuration: dict, mapping: dict) -> Projection: ...


def validate_mapping(mapping):
    if not isinstance(mapping, dict) or set(mapping) - set(MAPPING_FIELDS):
        raise ValidationError("Choose supported booking fields.")
    for key, (_, _, required) in MAPPING_FIELDS.items():
        value = mapping.get(key, "")
        if not isinstance(value, str) or len(value) > 100 or (required and not value.strip()):
            raise ValidationError("Map the booking title, booking reference and participant selections.")
    targets = [value.strip().casefold() for value in mapping.values() if value.strip()]
    if len(targets) != len(set(targets)):
        raise ValidationError("Use a different destination property for each booking field.")


def rich_text(value):
    if len(value) > 200_000:
        raise ValidationError("This booking exceeds the destination text limit; no data was truncated.")
    return [{"type": "text", "text": {"content": value[i:i + 2000]}} for i in range(0, len(value), 2000)]


class NotionDestination:
    def project(self, booking, configuration, mapping):
        validate_mapping(mapping)
        try:
            data_source_id = str(uuid.UUID(configuration["data_source_id"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValidationError("Enter a valid Notion data source ID.") from None
        status_type = configuration.get("status_type", "rich_text")
        if status_type not in {"rich_text", "select", "status"}:
            raise ValidationError("Choose a supported booking-status field type.")
        if (booking.starts_at.utcoffset() is None or booking.ends_at.utcoffset() is None
                or booking.ends_at <= booking.starts_at or len(booking.contact_email) > 200):
            raise ValidationError("The booking has an invalid time range or email for this destination.")
        values = {
            "title": booking.title, "booking_id": booking.reference,
            "contact_name": booking.contact_name, "contact_email": booking.contact_email or None,
            "schedule": {"start": booking.starts_at.isoformat(), "end": booking.ends_at.isoformat()},
            "status": booking.status,
            "participants": "\n".join(f"{p.name}: " + "; ".join(f"{key}: {value}" for key, value in p.selections)
                for p in booking.participants),
        }
        properties, rows = {}, []
        for key in MAPPING_FIELDS:
            target = mapping.get(key, "")
            if target.strip():
                kind = status_type if key == "status" else MAPPING_FIELDS[key][1]
                value = rich_text(values[key]) if kind in {"title", "rich_text"} else values[key]
                if kind in {"select", "status"}:
                    value = {"name": value}
                properties[target.strip()] = {kind: value}
                display = (f"{booking.starts_at:%b %d, %Y, %I:%M %p %Z} to {booking.ends_at:%b %d, %Y, %I:%M %p %Z}"
                    if kind == "date" else values[key])
                rows.append({"property": target.strip(), "kind": kind, "value": display})
        payload = {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": properties}
        if len(json.dumps(payload).encode()) > 450_000:
            raise ValidationError("This booking exceeds the destination payload limit; no data was truncated.")
        return Projection(payload, tuple(rows), NOTION_VERSION)


DESTINATIONS: dict[str, DestinationAdapter] = {"notion": NotionDestination()}


def example_booking():
    return BookingSnapshot("example-booking-001", "Design Your Plushie", "Example Customer", "customer@example.invalid",
        datetime(2026, 10, 6, 17, tzinfo=timezone.utc), datetime(2026, 10, 6, 18, 30, tzinfo=timezone.utc), "Confirmed",
        (Participant("Avery", (("Design", "Cat"), ("Color", "Blue"))),
         Participant("Casey", (("Design", "Bunny"), ("Color", "Pink")))))
