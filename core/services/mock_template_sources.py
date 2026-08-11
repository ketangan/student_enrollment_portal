from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings


class MockTemplateSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MockTemplateSource:
    id: str
    label: str
    path: str
    description: str

    @property
    def github_url(self) -> str:
        owner, repo, branch, _token = _repo_context()
        return f"https://github.com/{owner}/{repo}/blob/{branch}/{self.path}"


MOCK_TEMPLATE_SOURCES = (
    MockTemplateSource(
        id="mock-page-generator",
        label="Website mock page generator",
        path="scripts/generate_website_mocks.py",
        description="HTML, CSS, layout, and section copy used to generate the public mock pages.",
    ),
    MockTemplateSource(
        id="mock-variants-addendum",
        label="Mock variants and follow-up addendum",
        path="src/website_mocks.py",
        description="Variant labels, colors, mock payload helpers, and the fallback follow-up addendum.",
    ),
)


def list_mock_template_sources() -> list[MockTemplateSource]:
    return list(MOCK_TEMPLATE_SOURCES)


def get_mock_template_source(source_id: str) -> MockTemplateSource:
    for source in MOCK_TEMPLATE_SOURCES:
        if source.id == source_id:
            return source
    raise MockTemplateSourceError("Unknown mock template source.")


def fetch_mock_template_source(source: MockTemplateSource) -> dict:
    owner, repo, branch, token = _repo_context()
    path = quote(source.path)
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={quote(branch)}"
    data = _github_request("GET", url, token=token)
    encoded = str(data.get("content", "")).replace("\n", "")
    if data.get("encoding") != "base64" or not encoded:
        raise MockTemplateSourceError("GitHub returned an unreadable source file.")
    try:
        content = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise MockTemplateSourceError("Could not decode the source file from GitHub.") from exc
    return {
        "content": content,
        "sha": data.get("sha", ""),
        "html_url": data.get("html_url") or source.github_url,
    }


def _repo_context() -> tuple[str, str, str, str]:
    owner = getattr(settings, "MOCK_TEMPLATE_GITHUB_OWNER", "ketangan")
    repo = getattr(settings, "MOCK_TEMPLATE_GITHUB_REPO", "enrollify-outreach")
    branch = getattr(settings, "MOCK_TEMPLATE_GITHUB_BRANCH", "main")
    token = getattr(settings, "MOCK_TEMPLATE_GITHUB_TOKEN", "")
    return (
        str(owner).strip() or "ketangan",
        str(repo).strip() or "enrollify-outreach",
        str(branch).strip() or "main",
        str(token).strip(),
    )


def _github_request(method: str, url: str, payload: dict | None = None, token: str = "") -> dict:
    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Pontora-Ops-Portal",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("message") or detail
        except json.JSONDecodeError:
            message = detail
        raise MockTemplateSourceError(f"GitHub {exc.code}: {message}") from exc
    except (TimeoutError, URLError) as exc:
        raise MockTemplateSourceError(f"Could not reach GitHub: {exc}") from exc
