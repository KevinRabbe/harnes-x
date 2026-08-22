"""Dependency-light loopback HTTP/SSE transport for the personal Harness X App Server."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError

from .protocol import AppServerError, CodingSessionRequest
from .service import AppServerService

_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _atomic_text(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def _load_or_create_token(path: Path) -> str:
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError("existing app-server token is unexpectedly short")
        return token
    token = secrets.token_urlsafe(48)
    _atomic_text(path, token + "\n", mode=0o600)
    return token


class _LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalAppHTTPServer:
    """Authenticated loopback transport over an AppServerService."""

    def __init__(
        self,
        service: AppServerService,
        root: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("M34 app server binds only to literal 127.0.0.1")
        if port < 0 or port > 65535:
            raise ValueError("app-server port must be between 0 and 65535")
        self.service = service
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.token_path = self.root / "access-token"
        self.token = _load_or_create_token(self.token_path)
        handler = self._handler_type()
        self.httpd = _LoopbackHTTPServer((host, port), handler)
        self.host = host
        self.port = int(self.httpd.server_address[1])
        self.base_url = f"http://{host}:{self.port}"
        self.info_path = self.root / "server-info.json"
        _atomic_text(
            self.info_path,
            json.dumps(
                {
                    "schema_version": "app-server-info-v1",
                    "base_url": self.base_url,
                    "token_path": str(self.token_path),
                    "pid": os.getpid(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            mode=0o600,
        )
        self._thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.2)

    def start_in_thread(self) -> threading.Thread:
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(
            target=self.serve_forever,
            name="harness-x-app-server-http",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _handler_type(self):
        service = self.service
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "HarnessXAppServer/34"
            sys_version = ""

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

            def version_string(self) -> str:
                return self.server_version

            def do_GET(self) -> None:  # noqa: N802
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                parsed = urlsplit(self.path)
                if parsed.path == "/v1/health":
                    self._json(HTTPStatus.OK, service.health().model_dump(mode="json"))
                    return
                if not self._authorized(token):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return
                if parsed.path == "/v1/sessions":
                    self._json(
                        HTTPStatus.OK,
                        {
                            "schema_version": "app-session-list-v1",
                            "sessions": [
                                item.model_dump(mode="json") for item in service.sessions()
                            ],
                        },
                    )
                    return
                pieces = self._session_path(parsed.path)
                if pieces is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found")
                    return
                session_id, suffix = pieces
                try:
                    if suffix == "":
                        self._json(
                            HTTPStatus.OK,
                            service.session(session_id).model_dump(mode="json"),
                        )
                        return
                    if suffix == "/events":
                        query = parse_qs(parsed.query, keep_blank_values=False)
                        after = self._after_sequence(query)
                        events = service.store.events(session_id, after_sequence=after)
                        self._json(
                            HTTPStatus.OK,
                            {
                                "schema_version": "app-event-page-v1",
                                "session_id": session_id,
                                "after": after,
                                "events": [item.model_dump(mode="json") for item in events],
                            },
                        )
                        return
                    if suffix == "/events/stream":
                        query = parse_qs(parsed.query, keep_blank_values=False)
                        after = self._after_sequence(query)
                        self._stream_events(session_id, after)
                        return
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                    return
                except ValueError as exc:
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found")

            def do_POST(self) -> None:  # noqa: N802
                if not self._valid_host():
                    self._error(HTTPStatus.BAD_REQUEST, "invalid_host")
                    return
                if not self._authorized(token):
                    self._error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return
                parsed = urlsplit(self.path)
                if parsed.path == "/v1/sessions":
                    try:
                        raw = self._read_json()
                        request = CodingSessionRequest.model_validate(raw)
                        snapshot = service.create_session(request)
                    except ValidationError as exc:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_session_request",
                            str(exc)[:4000],
                        )
                        return
                    except ValueError as exc:
                        self._error(
                            HTTPStatus.BAD_REQUEST,
                            "invalid_session_request",
                            str(exc)[:4000],
                        )
                        return
                    self._json(HTTPStatus.ACCEPTED, snapshot.model_dump(mode="json"))
                    return
                pieces = self._session_path(parsed.path)
                if pieces is not None and pieces[1] == "/cancel":
                    try:
                        snapshot = service.cancel(pieces[0])
                    except KeyError:
                        self._error(HTTPStatus.NOT_FOUND, "unknown_session")
                        return
                    except ValueError as exc:
                        self._error(HTTPStatus.CONFLICT, "cannot_cancel", str(exc))
                        return
                    self._json(HTTPStatus.OK, snapshot.model_dump(mode="json"))
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found")

            def do_OPTIONS(self) -> None:  # noqa: N802
                # M34 intentionally does not enable cross-origin browser access. A future UI
                # served from this origin can use the API without CORS.
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Allow", "GET, POST, OPTIONS")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _valid_host(self) -> bool:
                host = self.headers.get("Host", "")
                hostname = host.rsplit(":", 1)[0].strip("[]").casefold()
                return hostname in _ALLOWED_HOSTS

            def _authorized(self, expected: str) -> bool:
                value = self.headers.get("Authorization", "")
                prefix = "Bearer "
                if not value.startswith(prefix):
                    return False
                return hmac.compare_digest(value[len(prefix) :], expected)

            def _read_json(self) -> object:
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise ValueError("Content-Length is required")
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("invalid Content-Length") from exc
                if length < 0 or length > _MAX_REQUEST_BYTES:
                    raise ValueError("request body exceeds app-server limit")
                content_type = self.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().casefold() != "application/json":
                    raise ValueError("Content-Type must be application/json")
                payload = self.rfile.read(length)
                try:
                    return json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("request body is not valid UTF-8 JSON") from exc

            def _after_sequence(self, query: dict[str, list[str]]) -> int:
                values = query.get("after", ["0"])
                if len(values) != 1:
                    raise ValueError("after must be specified once")
                try:
                    after = int(values[0])
                except ValueError as exc:
                    raise ValueError("after must be an integer") from exc
                if after < 0:
                    raise ValueError("after cannot be negative")
                return after

            def _session_path(self, path: str) -> tuple[str, str] | None:
                prefix = "/v1/sessions/"
                if not path.startswith(prefix):
                    return None
                remainder = path[len(prefix) :]
                if "/" in remainder:
                    session_id, suffix = remainder.split("/", 1)
                    suffix = "/" + suffix
                else:
                    session_id, suffix = remainder, ""
                if not session_id.startswith("app_") or len(session_id) != 36:
                    return None
                return session_id, suffix

            def _stream_events(self, session_id: str, after: int) -> None:
                service.session(session_id)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                cursor = after
                idle_deadline = time.monotonic() + 30.0 * 60.0
                try:
                    while time.monotonic() < idle_deadline:
                        events = service.store.events(session_id, after_sequence=cursor)
                        for event in events:
                            data = event.model_dump_json()
                            message = (
                                f"id: {event.sequence}\n"
                                f"event: {event.kind.value}\n"
                                f"data: {data}\n\n"
                            ).encode("utf-8")
                            self.wfile.write(message)
                            self.wfile.flush()
                            cursor = event.sequence
                            idle_deadline = time.monotonic() + 30.0 * 60.0
                        snapshot = service.session(session_id)
                        if snapshot.status.terminal and cursor >= snapshot.event_count:
                            return
                        if not events:
                            time.sleep(0.2)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def _json(self, status: HTTPStatus, payload: object) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def _error(
                self,
                status: HTTPStatus,
                error: str,
                detail: str | None = None,
            ) -> None:
                payload = AppServerError(error=error, detail=detail)
                self._json(status, payload.model_dump(mode="json"))

        return Handler
