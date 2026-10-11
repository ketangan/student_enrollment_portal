"""Read-only, loopback-only viewer for the project's canonical planning documents."""

import argparse
from collections import Counter
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = {
    "/verification-p3": ("Shared inventory and staff settings", "docs/scheduling-p3-verification.md"),
    "/plushumi-demo": ("Plushumi working demo", "docs/scheduling-plushumi-demo.md"),
    "/integrated-demo": ("School demo walkthrough", "docs/scheduling-integrated-demo.md"),
    "/verification-m9c": ("M9c usable preschool demo", "docs/scheduling-m9c-verification.md"),
    "/verification-m9b": ("M9b admissions verification", "docs/scheduling-m9b-verification.md"),
    "/verification-m9a": ("M9a tour verification", "docs/scheduling-m9a-verification.md"),
    "/verification-m4a": ("Configurable integration foundation", "docs/scheduling-m4a-verification.md"),
    "/verification-p1": ("Pilot foundation verification", "docs/scheduling-p1-verification.md"),
    "/tracker": ("Implementation tracker", "docs/scheduling-implementation-tracker.md"),
    "/project": ("Project reference", "PROJECT.md"),
    "/design": ("Architecture", "docs/scheduling-module-design.md"),
    "/review": ("Requirements and rollout review", "docs/scheduling-alignment-review.md"),
    "/verification": ("M1a verification", "docs/scheduling-m1a-verification.md"),
    "/verification-m1b": ("M1b verification", "docs/scheduling-m1b-verification.md"),
    "/verification-m2": ("M2 verification", "docs/scheduling-m2-verification.md"),
    "/verification-m3": ("M3 verification", "docs/scheduling-m3-verification.md"),
    "/verification-m5a": ("M5a verification", "docs/scheduling-m5a-verification.md"),
    "/verification-m5b": ("M5b verification", "docs/scheduling-m5b-verification.md"),
    "/verification-m5c": ("M5c verification", "docs/scheduling-m5c-verification.md"),
    "/verification-m6": ("M6 verification", "docs/scheduling-m6-verification.md"),
    "/verification-m7a": ("M7a verification", "docs/scheduling-m7a-verification.md"),
    "/verification-m7b1": ("M7b1 verification", "docs/scheduling-m7b1-verification.md"),
    "/verification-m7b2": ("M7b2 verification", "docs/scheduling-m7b2-verification.md"),
    "/verification-m7c1": ("M7c1 verification", "docs/scheduling-m7c1-verification.md"),
    "/verification-m7c2": ("M7c2 verification", "docs/scheduling-m7c2-verification.md"),
    "/verification-m7c2-messages": ("M7c2 message verification", "docs/scheduling-m7c2-messages-verification.md"),
}
STATUSES = {
    "Planned": "planned",
    "In progress": "progress",
    "Ready for your test": "review",
    "Accepted": "accepted",
    "Blocked": "blocked",
}
ASSETS = Path(__file__).with_name("project_status_assets")


def parser():
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def section(tokens, title):
    for start, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h2" and tokens[start + 1].content == title:
            end = start + 3
            while end < len(tokens) and not (tokens[end].type == "heading_open" and tokens[end].tag in {"h1", "h2"}):
                end += 1
            return tokens[start + 3:end]
    raise ValueError(f"Missing tracker section: {title}")


def table_rows(tokens):
    """Read the first Markdown table as records, not delimiter-split strings."""
    rows, row = [], []
    for token in tokens:
        if token.type == "tr_open":
            row = []
        elif token.type == "inline":
            row.append(token.content)
        elif token.type == "tr_close":
            rows.append(row)
        elif token.type == "table_close":
            break
    if len(rows) < 2 or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("Missing or incomplete milestone table")
    if len(set(rows[0])) != len(rows[0]):
        raise ValueError("Duplicate table headings")
    return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


def milestone_records(source):
    tokens = parser().parse(source)
    milestones = table_rows(section(tokens, "Milestones"))
    estimates = table_rows(section(tokens, "Effort and Major Checkpoints"))
    required = {"ID", "Deliverable", "Acceptance check", "Dependencies", "Status"}
    estimate_fields = {"Milestone", "Difficulty", "Focused effort", "Major checkpoint"}
    if not all(required <= row.keys() for row in milestones) or not all(estimate_fields <= row.keys() for row in estimates):
        raise ValueError("Tracker table columns have changed; update the viewer")
    ids = [row["ID"] for row in milestones]
    estimate_ids = [row["Milestone"] for row in estimates]
    if len(set(ids)) != len(ids) or len(set(estimate_ids)) != len(estimate_ids) or set(ids) != set(estimate_ids):
        raise ValueError("Milestone IDs and estimates must match exactly once")
    by_id = {row["Milestone"]: row for row in estimates}
    if any(row["Status"] not in STATUSES for row in milestones):
        raise ValueError("Unknown milestone status; update the viewer")
    return [{**row, **by_id[row["ID"]]} for row in milestones]


def excerpt(source, title):
    tokens = section(parser().parse(source), title)
    ranges = [token.map for token in tokens if token.map]
    if not ranges:
        raise ValueError(f"{title} is empty")
    return "\n".join(source.splitlines()[min(start for start, _ in ranges):max(end for _, end in ranges)])


def manual_steps(source):
    """Milestone IDs are headings in the canonical tracker, not a second status file."""
    tokens = parser().parse(source)
    if not any(t.type == "inline" and t.content == "Manual Testing" for t in tokens):
        return {}
    steps = section(tokens, "Manual Testing")
    result = {}
    for i, token in enumerate(steps):
        if token.type != "heading_open" or token.tag != "h3":
            continue
        key = steps[i + 1].content
        if key in result:
            raise ValueError(f"Duplicate manual testing section: {key}")
        end = i + 3
        while end < len(steps) and steps[end].type != "heading_open":
            end += 1
        ranges = [t.map for t in steps[i + 3:end] if t.map]
        if not ranges:
            raise ValueError(f"Manual testing steps missing: {key}")
        result[key] = "\n".join(source.splitlines()[min(a for a, _ in ranges):max(b for _, b in ranges)])
    return result


def render_document(source, filename):
    md = parser()
    tokens = md.parse(source)
    toc, used_ids = [], Counter()
    routes = {str(Path(path)): route for route, (_, path) in DOCUMENTS.items()}
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            label = tokens[index + 1].content
            slug = re.sub(r"[^\w-]+", "-", label.lower()).strip("-") or "section"
            used_ids[slug] += 1
            anchor = slug if used_ids[slug] == 1 else f"{slug}-{used_ids[slug]}"
            token.attrSet("id", anchor)
            if token.tag == "h2":
                toc.append(f'<a href="#{escape(anchor)}">{escape(label)}</a>')
        for child in token.children or []:
            if child.type != "link_open":
                continue
            href = child.attrGet("href") or ""
            url = urlsplit(href)
            if href.startswith("#") or url.scheme in {"https", "http", "mailto"}:
                continue
            path = str(Path(filename).parent / unquote(url.path))
            if path in routes and not url.netloc:
                child.attrSet("href", routes[path] + ("#" + url.fragment if url.fragment else ""))
            else:
                child.attrs.pop("href", None)
    # Internal documentation must not execute HTML or fetch embedded remote images.
    md.add_render_rule("image", lambda self, ts, i, options, env: escape(ts[i].content))
    md.add_render_rule("table_open", lambda self, ts, i, options, env: '<div class="table-scroll" tabindex="0" role="region" aria-label="Document table"><table>')
    md.add_render_rule("table_close", lambda self, ts, i, options, env: "</table></div>")
    contents = '<details class="toc"><summary>On this page</summary>' + "".join(toc) + "</details>"
    return contents + '<article class="document">' + md.renderer.render(tokens, md.options, {}) + "</article>"


def overview(source):
    records = milestone_records(source)
    tests = manual_steps(source)
    if set(tests) - {r["ID"] for r in records}:
        raise ValueError("Manual testing section refers to an unknown milestone")
    counts = Counter(row["Status"] for row in records)
    metrics = "".join(
        f'<div class="metric"><strong>{counts[status]}</strong><span>{escape(status)}</span></div>'
        for status in STATUSES
    )
    # Render the excerpt through the same safe link rewriting as the full document.
    current = render_document(excerpt(source, "Current Status"), DOCUMENTS["/tracker"][1])
    questions = ""
    if any(t.type == "inline" and t.content == "Open Questions" for t in parser().parse(source)):
        questions = '<section class="questions"><h2>Open questions &amp; blockers</h2>' + render_document(excerpt(source, "Open Questions"), DOCUMENTS["/tracker"][1]) + '</section>'
    rows = []
    for row in records:
        status = STATUSES[row["Status"]]
        detail = "".join(f'<div><h3>{label}</h3><p>{escape(row[key])}</p></div>' for label, key in (
            ("Difficulty", "Difficulty"), ("Completion checkpoint", "Major checkpoint"),
            ("What must pass", "Acceptance check"), ("Dependencies", "Dependencies"),
        ))
        steps = render_document(tests[row["ID"]], DOCUMENTS["/tracker"][1]) if row["ID"] in tests else '<p>Testing steps will be added with the implementation.</p>'
        detail += '<section class="manual-tests"><h3>Manual testing</h3>' + steps + '</section>'
        rows.append(f'''<details class="milestone">
          <summary><span class="milestone-id">{escape(row['ID'])}</span>
            <span class="deliverable">{escape(row['Deliverable'])}</span>
            <span class="badge {status}">{escape(row['Status'])}</span>
            <span class="effort">{escape(row['Focused effort'])}</span><span class="expand" aria-hidden="true">+</span></summary>
          <div class="milestone-detail">{detail}</div></details>''')
    return f'''<section class="status-band"><h2>Current status</h2>{current}</section>
        {questions}<section class="metrics" aria-label="Milestone counts">{metrics}</section>
        <section class="roadmap"><div class="section-heading"><h2>Milestones</h2><span>{len(records)} total</span></div>
        <div class="milestone-head" aria-hidden="true"><span>ID</span><span>Deliverable</span><span>Status</span><span>Est. effort</span><span></span></div>
        {''.join(rows)}</section>
        <p class="footnote">Effort includes implementation, tests, review and browser checks. It is not elapsed time or remaining time. Acceptance is separate from engineering completion.</p>
        <a class="text-link" href="/tracker#effort-and-major-checkpoints">Full estimates and release gates</a>'''


def page(title, content, active, modified=None):
    navigation = {"/": ("Status overview", ""), **DOCUMENTS}
    links = "".join(f'<a href="{route}" {"aria-current=page" if route == active else ""}>{escape(label)}</a>' for route, (label, _) in navigation.items())
    stamp = f"Source updated {datetime.fromtimestamp(modified).astimezone().strftime('%b %d, %Y at %I:%M %p')}" if modified else ""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{escape(title)} | Pontora project</title><link rel="stylesheet" href="/style.css"><link rel="icon" href="data:,"></head>
        <body><aside class="sidebar"><a class="brand" href="/"><img src="/wordmark.png" alt="Pontora"></a>
        <p class="workspace-label">PROJECT WORKSPACE</p><nav aria-label="Project documents">{links}</nav><p class="local-label">Internal · Local only</p></aside>
        <main><header class="page-heading"><div><p class="eyebrow">ENROLLMENT + BOOKINGS</p><h1>{escape(title)}</h1></div><a class="refresh" href="{active}" aria-label="Refresh current document">Refresh</a></header>
        <p class="source-stamp">{stamp}</p>{content}</main></body></html>'''


def response(path, root=ROOT):
    if path == "/style.css":
        return 200, "text/css; charset=utf-8", (ASSETS / "style.css").read_bytes()
    if path == "/wordmark.png":
        return 200, "image/png", (root / "static/pontora-wordmark-nav.png").read_bytes()
    if path != "/" and path not in DOCUMENTS:
        return 404, "text/plain; charset=utf-8", b"Not found"
    title, filename = DOCUMENTS.get(path, ("Status overview", DOCUMENTS["/tracker"][1]))
    try:
        file = root / filename
        source = file.read_text(encoding="utf-8")
        content = overview(source) if path == "/" else render_document(source, filename)
        return 200, "text/html; charset=utf-8", page(title, content, path, file.stat().st_mtime).encode()
    except (OSError, ValueError) as exc:
        content = f'<section role="alert"><h2>Could not load this document</h2><p>{escape(str(exc))}</p><a href="/tracker">Open the tracker</a></section>'
        return 503, "text/html; charset=utf-8", page(title, content, path).encode()


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve(head=True)

    def serve(self, head=False):
        # Reject other hostnames to limit DNS rebinding against this local-only server.
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self.send_error(403)
            return
        status, kind, body = response(urlsplit(self.path).path)
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head:
            self.wfile.write(body)


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--port", type=int, default=8003)
    args = arguments.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), StatusHandler) as server:
        print(f"Project status: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
