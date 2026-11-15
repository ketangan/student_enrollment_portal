"""Calendar exports contain public event details, never private booking links."""
from datetime import timezone as utc_timezone
from urllib.parse import urlencode

from django.http import Http404


def calendar_url(*, provider, start, end, title, location):
    start, end = start.astimezone(utc_timezone.utc), end.astimezone(utc_timezone.utc)
    if provider == "google":
        return "https://calendar.google.com/calendar/render?" + urlencode({"action": "TEMPLATE",
            "text": title, "location": location, "dates": f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}"})
    if provider == "outlook":
        return "https://outlook.live.com/calendar/0/deeplink/compose?" + urlencode({
            "path": "/calendar/action/compose", "rru": "addevent", "subject": title,
            "location": location, "startdt": start.isoformat(), "enddt": end.isoformat()})
    raise Http404


def calendar_file(*, public_id, created_at, start, end, title, location, demo=False):
    def escape(value):
        return str(value).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

    def stamp(value):
        return value.astimezone(utc_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pontora//Workshop booking//EN", "BEGIN:VEVENT",
        f"UID:{public_id}@{'demo.pontora' if demo else 'pontora'}", f"DTSTAMP:{stamp(created_at)}",
        f"DTSTART:{stamp(start)}", f"DTEND:{stamp(end)}", f"SUMMARY:{escape(title)}", f"LOCATION:{escape(location)}",
        "DESCRIPTION:" + ("Local demonstration booking. Not a real reservation." if demo else "Workshop reservation."),
        "END:VEVENT", "END:VCALENDAR"]
    folded = []
    # RFC 5545 counts UTF-8 octets, not characters.
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode()) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"
