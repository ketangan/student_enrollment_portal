"""Local documentation viewer tests; no application or external services required."""

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from scripts import project_status as viewer


@pytest.fixture
def source():
    return '''# Tracker

`venv/bin/python scripts/project_status.py`

## Current Status

- Read the [design](scheduling-module-design.md).

## Working Agreement

Keep one milestone active.

## Milestones

| ID | Deliverable | Acceptance check | Dependencies | Status |
| --- | --- | --- | --- | --- |
| M1a | Isolated module, default-off access | Access is isolated | Baseline tests | Ready for your test |
| M9 | Optional enrollment links | Existing schools unchanged | M1a | Planned |

## Effort and Major Checkpoints

| Milestone | Difficulty | Focused effort | Major checkpoint |
| --- | --- | --- | --- |
| M1a | Medium-high: security | 6-10 hours | Standalone access works |
| M9 | High: shared-code and release risk | 12-24 hours | Pilot verified |

[Verification](scheduling-m1a-verification.md)
'''


def test_tracker_and_estimates_are_joined_by_id(source):
    records = viewer.milestone_records(source)
    assert len(records) == 2
    assert records[0]["ID"] == "M1a"
    assert records[0]["Status"] == "Ready for your test"
    assert records[0]["Focused effort"] == "6-10 hours"
    assert records[-1]["ID"] == "M9"
    assert records[-1]["Difficulty"] == "High: shared-code and release risk"


@pytest.mark.parametrize("old,new,message", [
    ("## Milestones", "## Removed", "Missing tracker section"),
    ("| M9 | Optional", "| M8 | Optional", "match exactly once"),
    ("| M9 | High: shared", "| M8 | High: shared", "match exactly once"),
    ("| M9 | High: shared", "| M10 | High: shared", "match exactly once"),
    ("| Status |", "| State |", "columns have changed"),
    ("| Difficulty |", "| Complexity |", "columns have changed"),
    ("| Ready for your test |", "| Done-ish |", "Unknown milestone status"),
])
def test_tracker_schema_changes_fail_visibly(source, old, new, message):
    assert old in source
    with pytest.raises(ValueError, match=message):
        viewer.milestone_records(source.replace(old, new))


def test_table_parser_handles_escaped_pipes_and_formatting():
    tokens = viewer.parser().parse("| ID | Name |\n| --- | --- |\n| M1 | **one** \\| two |")
    assert viewer.table_rows(tokens) == [{"ID": "M1", "Name": "**one** | two"}]


@pytest.mark.parametrize("markdown", ["", "| ID |\n| --- |", "| ID | ID |\n| --- | --- |\n| A | B |"])
def test_missing_or_duplicate_table_headers_rejected(markdown):
    with pytest.raises(ValueError):
        viewer.table_rows(viewer.parser().parse(markdown))


def test_full_documents_render_tables_code_and_local_links(source):
    html = viewer.render_document(source, viewer.DOCUMENTS["/tracker"][1])
    assert '<a href="/design">' in html
    assert '<a href="/verification">' in html
    assert '<table>' in html and 'class="table-scroll"' in html
    assert '<code>venv/bin/python scripts/project_status.py</code>' in html
    assert 'id="effort-and-major-checkpoints"' in html
    assert 'href="#effort-and-major-checkpoints"' in html
    html = viewer.render_document("[Tracker](docs/scheduling-implementation-tracker.md#milestones)", "PROJECT.md")
    assert 'href="/tracker#milestones"' in html
    html = viewer.render_document("[M1b verification](scheduling-m1b-verification.md)", "docs/scheduling-implementation-tracker.md")
    assert 'href="/verification-m1b"' in html


def test_rendered_document_cannot_execute_html_or_fetch_remote_images():
    source = '''<script>alert(1)</script>

[Unsafe](javascript:alert(1))
[Secret](.env)
[Secret](file:///tmp/secret)
[Protocol relative](//evil.example/PROJECT.md)
![Remote](https://example.invalid/tracking.png)
[Web](https://example.com)
[Email](mailto:test@example.com)
[Section](#same)
## Same
## Same
## !!!
'''
    html = viewer.render_document(source, "PROJECT.md")
    assert "<script>" not in html and "<img" not in html
    assert 'href="javascript:' not in html and 'href="file:' not in html
    assert 'href=".env"' not in html and 'href="//evil' not in html
    assert 'href="https://example.com"' in html
    assert 'href="mailto:test@example.com"' in html
    assert 'href="#same"' in html
    assert 'id="same-2"' in html and 'id="section"' in html


def test_alignment_review_is_linked_and_keeps_release_findings_visible():
    html = viewer.render_document(
        "[Review](scheduling-alignment-review.md#confirmed-findings)",
        "docs/scheduling-implementation-tracker.md",
    )
    assert 'href="/review#confirmed-findings"' in html
    status, _, body = viewer.response("/review")
    assert status == 200
    assert b"product is not ready for a client rollout" in body
    assert b"A1 P1" in body and b"A2 P2" in body


def test_inventory_verification_and_manual_steps_are_browsable():
    html = viewer.render_document("[Inventory](scheduling-p3-verification.md)", "docs/scheduling-implementation-tracker.md")
    assert 'href="/verification-p3"' in html
    status, _, body = viewer.response("/verification-p3")
    assert status == 200
    assert b"Wix/Notion stock ownership" in body
    source = (viewer.ROOT / "docs/scheduling-implementation-tracker.md").read_text()
    assert "Studio stock" in viewer.manual_steps(source)["P3"]
    assert any(row["ID"] == "P3" for row in viewer.milestone_records(source))


def test_stripe_followup_is_planned_and_browsable_with_acceptance_checks():
    source = (viewer.ROOT / "docs/scheduling-implementation-tracker.md").read_text()
    row = next(row for row in viewer.milestone_records(source) if row["ID"] == "B1")
    assert row["Status"] == "Planned"
    steps = viewer.manual_steps(source)["B1"]
    for requirement in ("invoice.paid", "invoice.payment_failed", "send_payment_notifications",
                        "Parent payment failures", "card", "seven-day", "SBMC"):
        assert requirement in steps
    assert 'href="/payment-recovery"' in viewer.overview(source)
    status, _, body = viewer.response("/payment-recovery")
    assert status == 200 and b"NOT complete" in body


def test_m9a_verification_is_browsable_without_claiming_m9_complete():
    html = viewer.render_document("[Tours](scheduling-m9a-verification.md)", "docs/scheduling-implementation-tracker.md")
    assert 'href="/verification-m9a"' in html
    status, _, body = viewer.response("/verification-m9a")
    assert status == 200
    assert b"M9b" in body and b"SBMC and Kid Works" in body


def test_m9b_verification_is_browsable_and_keeps_rollout_limits_visible():
    html = viewer.render_document("[Admissions](scheduling-m9b-verification.md)", "docs/scheduling-implementation-tracker.md")
    assert 'href="/verification-m9b"' in html
    status, _, body = viewer.response("/verification-m9b")
    assert status == 200
    assert b"SBMC and Kid Works" in body and b"not production readiness" in body


def test_overview_is_derived_from_statuses_and_escapes_cells(source):
    changed = source.replace("| Ready for your test |", "| Accepted |")
    changed = changed.replace("Isolated module,", "<script>bad()</script>,")
    html = viewer.overview(changed)
    assert '<strong>1</strong><span>Accepted</span>' in html
    assert '<strong>0</strong><span>Ready for your test</span>' in html
    assert '<span class="badge accepted">Accepted</span>' in html
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert '<a href="/design">' in html
    assert 'What must pass' in html and '6-10 hours' in html


def test_empty_current_status_fails_visibly(source):
    start = source.index("## Current Status")
    end = source.index("## Working Agreement")
    source = source[:start] + "## Current Status\n\n" + source[end:]
    with pytest.raises(ValueError, match="Current Status is empty"):
        viewer.overview(source)


def test_expandable_manual_steps_and_open_questions_are_canonical_and_safe(source):
    source += '''
## Open Questions

Awaiting sample options for M2; not blocking.

## Manual Testing

### M1a

Account: `bookings-owner` / `admin` (local only).

1. Open [Bookings](http://127.0.0.1:8002/bookings/).
2. Verify no enrollment banner.

<script>unsafe()</script>
'''
    html = viewer.overview(source)
    assert 'Manual testing' in html and 'bookings-owner' in html and '<code>admin</code>' in html
    assert 'Awaiting sample options for M2' in html
    assert '<script>' not in html and '<ol>' in html
    assert 'href="http://127.0.0.1:8002/bookings/"' in html
    assert 'bookings-owner' in html.split('<details class="milestone">')[1]
    assert 'Testing steps will be added' in html
    assert 'Updated account' in viewer.overview(source.replace('Account:', 'Updated account:'))


@pytest.mark.parametrize('tail,message', [
    ('### M1a\n', 'steps missing'),
    ('### M1a\nHello\n### M1a\nAgain', 'Duplicate manual'),
    ('### Unknown\nHello', 'unknown milestone'),
])
def test_invalid_manual_testing_sections_are_not_silently_hidden(source, tail, message):
    with pytest.raises(ValueError, match=message):
        viewer.overview(source + '\n## Manual Testing\n\n' + tail)


@pytest.mark.parametrize("route", ["/", *viewer.DOCUMENTS, "/style.css", "/wordmark.png"])
def test_allowlisted_pages_and_assets(route):
    status, kind, body = viewer.response(route)
    assert status == 200
    assert body
    if kind.startswith("text/html"):
        assert b'aria-current=page' in body
        assert b'Source updated' in body
    if route == "/wordmark.png":
        assert body.startswith(b"\x89PNG")


@pytest.mark.parametrize("path", ["/.env", "/.git/config", "/../PROJECT.md", "/%2e%2e/.env", "/PROJECT.md", "/static/secrets", "/unknown"])
def test_other_files_never_served(path):
    assert viewer.response(path) == (404, "text/plain; charset=utf-8", b"Not found")


def test_source_is_reread_and_missing_files_have_clear_errors(tmp_path):
    status, _, body = viewer.response("/project", tmp_path)
    assert status == 503 and b'Could not load this document' in body
    project = tmp_path / "PROJECT.md"
    project.write_text("# First version")
    assert b'First version' in viewer.response("/project", tmp_path)[2]
    project.write_text("# Updated version")
    assert b'Updated version' in viewer.response("/project", tmp_path)[2]
    (tmp_path / "docs").mkdir()
    (tmp_path / viewer.DOCUMENTS["/tracker"][1]).write_text("Broken tracker")
    assert viewer.response("/", tmp_path)[0] == 503


@pytest.fixture
def server():
    with ThreadingHTTPServer(("127.0.0.1", 0), viewer.StatusHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def request(server, method="GET", path="/", headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_real_http_routes_headers_head_and_no_write_methods(server):
    status, headers, body = request(server, path="/tracker?refresh=1")
    assert status == 200 and b'Pontora Bookings Implementation Tracker' in body
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer"
    status, headers, body = request(server, "HEAD", "/project")
    assert status == 200 and body == b"" and int(headers["Content-Length"]) > 0
    assert request(server, "POST")[0] == 501
    assert request(server, "GET", "/.env")[0] == 404


def test_rebinding_host_rejected_but_localhost_allowed(server):
    assert request(server, headers={"Host": f"localhost:{server.server_port}"})[0] == 200
    for host in ["evil.example", f"evil.example:{server.server_port}", "", "localhost:1"]:
        assert request(server, headers={"Host": host})[0] == 403


def test_entrypoint_binds_only_loopback(monkeypatch):
    seen = []

    class FakeServer:
        server_port = 9876

        def __init__(self, address, handler):
            seen.append((address, handler))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def serve_forever(self):
            seen.append("served")

    monkeypatch.setattr(viewer, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr("sys.argv", ["project_status.py", "--port", "9876"])
    viewer.main()
    assert seen == [(("127.0.0.1", 9876), viewer.StatusHandler), "served"]
