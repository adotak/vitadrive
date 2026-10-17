"""Structured logging, request IDs, and optional error reporting."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_STANDARD_LOG_RECORD_ATTRIBUTES = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
}
_sentry_sdk = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id,
        }
        if record.exc_info:
            payload["exc_info"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_ATTRIBUTES and not key.startswith("_")
        }
        if request_id is None and extra.get("request_id") is not None:
            payload["request_id"] = extra["request_id"]
        payload.update({key: value for key, value in extra.items() if key != "request_id"})
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    logger = logging.getLogger("vitadrive")
    configured_level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO").upper())
    logger.setLevel(configured_level if isinstance(configured_level, int) else logging.INFO)
    logger.propagate = False

    handler = next(
        (candidate for candidate in logger.handlers if getattr(candidate, "_vitadrive_json_handler", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler._vitadrive_json_handler = True
        logger.addHandler(handler)
    else:
        handler.stream = sys.stdout
    for candidate in list(logger.handlers):
        if candidate is not handler:
            logger.removeHandler(candidate)
    handler.setLevel(logging.NOTSET)
    handler.setFormatter(JsonFormatter())


def init_sentry() -> bool:
    global _sentry_sdk
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False

    import sentry_sdk

    from . import __version__

    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        environment=os.environ.get("VERCEL_ENV") or "local",
        release=__version__,
    )
    _sentry_sdk = sentry_sdk
    return True


def _valid_request_id(value: str | None) -> bool:
    return bool(value and _REQUEST_ID_PATTERN.fullmatch(value))


class RequestIDMiddleware:
    def __init__(self, app, sentry_active: bool = False):
        self.app = app
        self.sentry_active = sentry_active

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        incoming_id = headers.get(b"x-request-id", b"").decode("latin-1")
        vercel_id = headers.get(b"x-vercel-id", b"").decode("latin-1")
        request_id = (
            incoming_id if _valid_request_id(incoming_id)
            else vercel_id if _valid_request_id(vercel_id)
            else uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        if self.sentry_active and _sentry_sdk is not None:
            _sentry_sdk.get_isolation_scope().set_tag("request_id", request_id)

        status = 500
        started = time.perf_counter()
        path = scope.get("path", "")

        async def send_with_request_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = [
                    (key, value) for key, value in message.get("headers", ())
                    if key.lower() != b"x-request-id"
                ]
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            status = 500
            raise
        finally:
            if not path.startswith("/static/"):
                logging.getLogger("vitadrive.access").info(
                    "request",
                    extra={
                        "method": scope.get("method", ""),
                        "path": path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            request_id_var.reset(token)
