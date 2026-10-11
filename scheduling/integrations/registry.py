"""Provider capabilities, not a universal user-programmable HTTP client."""

from dataclasses import dataclass
from types import MappingProxyType

from django.http import Http404


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    role: str
    credential_label: str
    documentation: str


PROVIDERS = MappingProxyType({
    "wix": Provider("wix", "Wix Bookings", "source", "Wix API key",
        "https://dev.wix.com/docs/api-reference/articles/rest-authentication/rest-api-authentication"),
    "notion": Provider("notion", "Notion", "destination", "Notion internal connection token",
        "https://developers.notion.com/guides/get-started/authorization"),
})


def provider(key):
    try:
        return PROVIDERS[key]
    except KeyError:
        raise Http404("Provider not available") from None
