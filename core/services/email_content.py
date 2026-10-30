"""Restricted HTML for staff-authored emails; never execute template code."""
import re
from html import unescape

import nh3
from django.core.exceptions import ValidationError
from django.utils.html import escape, strip_tags


TOKENS = ("full_name", "first_name", "email", "program", "status", "school_name",
          "booking_date", "booking_time", "location", "participant_count")
TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def clean_html(value, *, links=True):
    tags = {"p", "br", "div", "b", "strong", "i", "em", "u", "ul", "ol", "li"}
    if links:
        tags.add("a")
    return nh3.clean(value, tags=tags,
        attributes={"a": {"href", "class"}}, url_schemes={"https", "http", "mailto"},
        attribute_filter=lambda tag, attr, value: value if (attr == "href" and value.lower().startswith(("https://", "http://", "mailto:")))
            or (attr == "class" and value == "booking-email-action") else None)


def transport_html(value):
    """Only the system action link receives fixed, email-compatible button styling."""
    return clean_html(value).replace('class="booking-email-action"',
        'style="display:inline-block;padding:12px 20px;background:#2563eb;color:#ffffff;'
        'text-decoration:none;border-radius:6px;font-weight:600"')


def validate_body(value):
    if len(value) > 20000:
        raise ValidationError("Keep the message under 20,000 characters.")
    value = clean_html(value)
    if not unescape(strip_tags(value)).strip():
        raise ValidationError("Enter a message.")
    return value


def render_tokens(value, values, *, html=False):
    return TOKEN.sub(lambda match: str(escape(values[match[1]])) if html and match[1] in values
        else str(values.get(match[1], match[0])), value)
