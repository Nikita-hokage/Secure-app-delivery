"""WSGI entry point for Gunicorn and the local recovery drill dashboard."""

import json
import sqlite3
from pathlib import Path
from wsgiref.simple_server import make_server

from restorecheck.contract import DrillError
from restorecheck.demo import SCENARIOS, rehearse


STATIC = Path(__file__).with_name("static")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}
SECURITY = [("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"), ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
            ("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")]


def json_body(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def app(environ, start_response):
    path = environ.get("PATH_INFO", "")
    method = environ["REQUEST_METHOD"]
    status, mime, extra = "200 OK", "application/json; charset=utf-8", []
    if method not in ("GET", "HEAD"):
        status, body = "405 Method Not Allowed", json_body({"error": "method not allowed"})
        extra.append(("Allow", "GET, HEAD"))
    elif path in ASSETS:
        name, mime = ASSETS[path]
        body = (STATIC / name).read_bytes()
    elif path == "/health":
        body = json_body({"status": "ok"})
    elif path == "/api/scenarios":
        body = json_body([{"id": k, "name": v[0], "description": v[1]} for k, v in SCENARIOS.items()])
    elif path.startswith("/api/drills/") and path.removeprefix("/api/drills/") in SCENARIOS:
        try:
            body = json_body(rehearse(path.removeprefix("/api/drills/")))
        except (DrillError, OSError, sqlite3.Error):
            status, body = "503 Service Unavailable", json_body({"error": "drill unavailable"})
    else:
        status, body = "404 Not Found", json_body({"error": "not found"})
    start_response(status, [("Content-Type", mime), ("Content-Length", str(len(body))), *SECURITY, *extra])
    return [] if method == "HEAD" else [body]


if __name__ == "__main__":
    with make_server("127.0.0.1", 5000, app) as server:
        print("RestoreCheck: http://127.0.0.1:5000", flush=True)
        server.serve_forever()
