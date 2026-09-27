"""Offline HTTP adapter for the Pocketful engine. Run with `python server.py`."""

from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit, unquote

from engine import ApiError, Engine


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are not supported")


def parse_body(raw, path):
    if not raw and (re.fullmatch(r"/requests/[^/]+/(decline|cancel)", path)
                    or re.fullmatch(r"/authorizations/[^/]+/void", path)):
        return {}
    try:
        body = json.loads(raw.decode("utf-8"), parse_float=Decimal,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError, ArithmeticError):
        raise ApiError(400, "malformed_request", "The body must be valid UTF-8 JSON.") from None
    if not isinstance(body, dict):
        raise ApiError(400, "malformed_request", "A JSON object is required.")
    return body


class Service(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, engine=None):
        self.engine = engine if engine is not None else Engine()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Pocketful"
    sys_version = ""

    def serve_browser(self, path, accept):
        """Fixed allowlist keeps HTML/static delivery independent of API auth."""
        routes = {"/", "/requests", "/split", "/signup", "/login", "/authorizations"}
        assets = {"/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/static/styles.css": ("styles.css", "text/css; charset=utf-8")}
        if path in routes and "text/html" in accept.lower():
            filename, mime = "index.html", "text/html; charset=utf-8"
        elif path in assets:
            filename, mime = assets[path]
        else:
            return False
        data = (Path(__file__).parent / "static" / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        return True

    def log_message(self, *args):
        # URLs, authentication headers and test-state bodies must not enter logs.
        pass

    def send_json(self, status, body):
        data = b"" if body is None else json.dumps(
            body, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        if code == 501:
            code = 404
        error_code = "not_found" if code == 404 else "malformed_request"
        self.send_json(code, {"error": {"code": error_code, "message": "Invalid HTTP request."}})

    def dispatch(self):
        try:
            try:
                target = urlsplit(self.path)
            except ValueError:
                raise ApiError(400, "malformed_request", "Invalid request target.") from None
            path = unquote(target.path)
            query = parse_qs(target.query, keep_blank_values=True)
            headers = {key.lower(): value for key, value in self.headers.items()}
            if "transfer-encoding" in headers:
                self.close_connection = True
                raise ApiError(400, "malformed_request", "Use a Content-Length body.")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]+", lengths[0])):
                self.close_connection = True
                raise ApiError(400, "malformed_request", "Invalid Content-Length.")
            length = int(lengths[0]) if lengths else 0
            raw = self.rfile.read(length) if length else b""
            if self.command in {"GET", "HEAD"} and self.serve_browser(path, headers.get("accept", "")):
                return
            body = parse_body(raw, path) if self.command in {"POST", "PUT", "PATCH"} else None
            status, response = self.server.engine.handle(self.command, path, query, body, headers)
            self.send_json(status, response)
        except ApiError as error:
            self.send_json(error.status, {"error": {"code": error.code, "message": error.message}})
        except (BrokenPipeError, ConnectionResetError):
            # A dropped response does not undo a committed transaction. Retry uses its key.
            self.close_connection = True
        except Exception as error:
            print(f"Unhandled service error: {type(error).__name__}", file=sys.stderr, flush=True)
            self.close_connection = True
            self.send_json(500, {"error": {"code": "internal_error", "message": "Internal service error."}})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = dispatch


def main():
    port = int(os.environ.get("PORT", "8080"))
    service = Service(("0.0.0.0", port))
    print(f"Pocketful listening on port {port}", flush=True)
    try:
        service.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        service.server_close()


if __name__ == "__main__":
    main()
