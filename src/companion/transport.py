"""Single lightweight loopback HTTP worker; no remote service or AI SDK."""
import hmac
import json
import os
import secrets
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from .bridge import BridgeBusy
from .events import CompanionEvent
from .notices import TaskNotice
from .paths import ensure_state_dir

MAX_BODY = 64 * 1024
MAX_NOTICE_BODY = 12 * 1024 * 1024


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BridgeServer:
    def __init__(self, bridge, registry, descriptor_path=None, port=0):
        self.bridge = bridge
        self.registry = registry
        self.token = secrets.token_urlsafe(32)
        self.descriptor_path = Path(descriptor_path) if descriptor_path else ensure_state_dir() / "bridge.json"
        self._thread = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            # Keep the server single-threaded and bound stalled client reads.
            def setup(self):
                self.request.settimeout(0.5)
                super().setup()

            def log_message(self, *_args):
                pass

            def respond(self, code, data):
                body = json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authenticated(self):
                # Browser scripts are not consumers of this local IPC endpoint.
                supplied = self.headers.get("Authorization", "")
                return (not self.headers.get("Origin") and
                        hmac.compare_digest(supplied.encode(), ("Bearer " + owner.token).encode()))

            def do_GET(self):
                if not self.authenticated():
                    return self.respond(401, {"error": "Unauthorized"})
                if self.path != "/v1/health":
                    return self.respond(404, {"error": "Unknown endpoint"})
                self.respond(200, {"schema_version": 1, "notice_schema_version": 2, "adapters": owner.registry.ids})

            def do_POST(self):
                if not self.authenticated():
                    return self.respond(401, {"error": "Unauthorized"})
                if self.headers.get_content_type() != "application/json":
                    return self.respond(415, {"error": "Expected application/json"})
                if self.headers.get("Transfer-Encoding"):
                    return self.respond(400, {"error": "Chunked bodies are not supported"})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.respond(400, {"error": "Invalid Content-Length"})
                limit = MAX_NOTICE_BODY if self.path == "/v2/notices" else MAX_BODY
                if not 0 < size <= limit:
                    return self.respond(413, {"error": "Invalid body size"})
                try:
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        return self.respond(400, {"error": "Incomplete body"})
                    payload = json.loads(raw)
                    if self.path == "/v1/events":
                        event = CompanionEvent.from_dict(payload)
                    elif self.path == "/v2/notices":
                        event = TaskNotice.from_dict(payload)
                    elif self.path.startswith("/v1/adapters/"):
                        identifier = self.path.removeprefix("/v1/adapters/")
                        event = owner.registry.normalize(identifier, payload)
                    else:
                        return self.respond(404, {"error": "Unknown endpoint"})
                    if event is not None:
                        owner.bridge.publish(event)
                    self.respond(202, {"accepted": event is not None})
                except KeyError:
                    self.respond(404, {"error": "Adapter is not enabled"})
                except BridgeBusy:
                    self.respond(429, {"error": "Event queue is full"})
                except (ValueError, TypeError, UnicodeError):
                    self.respond(400, {"error": "Invalid event"})
                except (socket.timeout, OSError):
                    self.respond(408, {"error": "Read timeout"})
                except Exception:
                    # A broken third-party adapter must not kill the server.
                    self.respond(500, {"error": "Adapter failed"})

        self._server = HTTPServer(("127.0.0.1", port), Handler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self):
        if self._thread is not None:
            return
        try:
            atomic_json(self.descriptor_path, {
                "schema_version": 1, "notice_schema_version": 2, "url": self.url, "token": self.token,
                "pid": os.getpid(), "adapters": self.registry.ids,
            })
        except Exception:
            self._server.server_close()
            raise
        self._thread = Thread(target=self._server.serve_forever,
                              kwargs={"poll_interval": 0.25}, daemon=True,
                              name="companion-event-bridge")
        self._thread.start()

    def stop(self):
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()
        try:
            descriptor = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
            if descriptor.get("token") == self.token:
                self.descriptor_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
