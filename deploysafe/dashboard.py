"""A small, read-only dashboard using only loopback and persisted data."""

import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import socket
import sqlite3

from .config import validate_dashboard_port
from .manager import pointer
from .models import DeployError
from .security import LOOPBACK
from .storage import Store
from .supervisor import observed

STYLE = """
:root{color-scheme:light;--paper:#f4f1e9;--ink:#20292b;--muted:#526061;--line:#cbc9bc;
--green:#17674d;--red:#9e3d28;--panel:#fdfbf6}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:1250px;margin:auto;padding:44px clamp(18px,4vw,60px)}
header{display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font:600 clamp(34px,6vw,58px)/1 Georgia,serif;letter-spacing:-.05em;margin:0 0 12px}
h2{font-size:16px;margin:0 0 12px} p{margin:0}
.meta,.muted{color:var(--muted)} .mono,time,td:first-child{font-family:ui-monospace,monospace}
.status{background:#213537;color:#fdfbf6;margin:28px 0;padding:25px 28px;
display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:20px;border-radius:8px}
.status small{display:block;color:#d2dbd4} .status strong{font-size:21px}
.grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:20px}
section{background:var(--panel);border:1px solid var(--line);padding:19px;border-radius:8px}
.table{overflow:auto} table{border-collapse:collapse;width:100%;text-align:left;font-size:13px}
td,th{padding:9px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600} tr:last-child td{border:0}
.event{padding:10px 0;border-top:1px solid var(--line)} .event time{font-size:12px}
.fail{color:var(--red)} .pass{color:var(--green)}
footer{margin-top:24px;color:var(--muted);font-size:13px}
@media(max-width:830px){.grid{grid-template-columns:1fr}}
"""

MARK = """<svg aria-hidden="true" focusable="false" viewBox="0 0 38 18" width="38" height="18">
<path d="M1 10h8l4-8 6 14 6-10 4 4h8" fill="none" stroke="currentColor" stroke-width="2"/></svg>"""


def esc(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def table(headers, rows):
    if not rows:
        return '<p class="muted">No records yet.</p>'
    head = "".join(f"<th scope='col'>{esc(heading)}</th>" for heading in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render(policy):
    with Store(policy.workspace, readonly=True) as store, store.snapshot():
        active = pointer(store.active())
        history = store.history(20)
        last_check = store.one("SELECT timestamp FROM health_checks WHERE release_id=? ORDER BY id DESC LIMIT 1",
                               (active["release_id"],)) if active else None
    state = observed(policy, active)
    deployments = [(row["to_release"], row["action"], row["state"], row["error_code"])
                   for row in history["deployments"]]
    checks = [(row["release_id"], row["phase"], row["endpoint"], row["code"], row["latency_ms"])
              for row in history["health_checks"]]
    events = []
    for row in history["events"]:
        color = {"PASS": "pass", "FAIL": "fail"}.get(row["outcome"], "muted")
        events.append(f'''<article class="event"><time class="muted">{esc(row["timestamp"])}</time>
<p><strong class="{color}">{esc(row["stage"])}</strong> · {esc(row["code"])} · {esc(row["release_id"])}</p>
<p>{esc(row["message"])}</p></article>''')
    timeline = "".join(events) or '<p class="muted">No events yet.</p>'
    address = f'{LOOPBACK}:{active["port"]}' if active else None
    incidents = [(row["timestamp"], row["code"], row["message"]) for row in history["incidents"]]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeploySafe · Local deployment history</title><style>{STYLE}</style></head>
<body><main><header><div><h1>DeploySafe</h1><p class="meta">Local deployment and rollback history</p></div>
<p class="mono">{MARK} Read-only · 127.0.0.1</p></header>
<div class="status">
<div><small>Committed release</small><strong>{esc(active["release_id"] if active else "None")}</strong></div>
<div><small>Service health</small><strong>{esc(state["health"])}</strong></div>
<div><small>Fixture address</small><strong class="mono">{esc(address)}</strong></div>
<div><small>Last recorded check (UTC)</small><time>{esc(last_check["timestamp"] if last_check else None)}</time></div>
</div><div class="grid"><div>
<section><h2>Recent deployments</h2>{table(("Release", "Action", "State", "Failure"), deployments)}</section>
<section><h2>Health checks</h2>{table(("Release", "Phase", "Endpoint", "Result", "ms"), checks)}</section>
<section><h2>Incidents</h2>{table(("Time (UTC)", "Code", "Details"), incidents)}</section>
</div><section><h2>Event timeline</h2>{timeline}</section></div>
<footer>Release behavior comes from bundled fixtures. Checks and events come from actual local runs.
Reload this page to refresh the snapshot.</footer></main></body></html>"""


class LocalDashboard(HTTPServer):
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


def server(policy, port):
    selected = validate_dashboard_port(policy, port)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            if self.headers.get("Host") != f"{LOOPBACK}:{selected}":
                self.send_error(400)
                return
            try:
                data = render(policy).encode("utf-8")
            except (OSError, sqlite3.Error, DeployError):
                self.send_error(503, "Local audit snapshot unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def do_POST(self):
            raw_length = self.headers.get("Content-Length", "")
            if len(raw_length) <= 4 and raw_length.isascii() and raw_length.isdigit():
                length = int(raw_length)
                if length <= 2048:
                    try:
                        self.rfile.read(length)
                    except OSError:
                        return
            self.send_error(405, "The dashboard is read-only")

        def log_message(self, *_):
            pass

    try:
        return LocalDashboard((LOOPBACK, selected), Handler)
    except OSError as exc:
        raise DeployError("PORT_IN_USE", f"Could not bind dashboard to {LOOPBACK}:{selected}.") from exc
