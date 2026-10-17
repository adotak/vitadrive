import io
import json
import logging
import re

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from vitadrive import __version__, observability
from vitadrive.api import create_app
from vitadrive.ratelimit import RateLimited


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


def _client(tmp_path, name="observability", *, raise_server_exceptions=True):
    app = create_app(str(tmp_path / f"{name}.db"), auth=HeaderAuth())
    return app, TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_access_log_is_json_with_request_fields_and_no_query_string(tmp_path):
    app, client = _client(tmp_path)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(observability.JsonFormatter())
    access_logger = logging.getLogger("vitadrive.access")
    access_logger.addHandler(handler)
    try:
        response = client.get("/no-such-route?search=private-query", headers={"X-Request-ID": "request-123"})
    finally:
        access_logger.removeHandler(handler)
        app.state.vitadrive.store.close()

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "request-123"
    entry = json.loads(stream.getvalue().strip())
    assert entry["request_id"] == "request-123"
    assert entry["method"] == "GET"
    assert entry["path"] == "/no-such-route"
    assert "private-query" not in stream.getvalue()
    assert entry["status"] == 404
    assert isinstance(entry["duration_ms"], (int, float))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+Z", entry["ts"])


def test_request_id_validation_falls_back_to_vercel_id_or_uuid(tmp_path):
    app, client = _client(tmp_path)
    try:
        invalid = client.get("/api/health", headers={"X-Request-ID": "x" * 300})
        fallback = client.get("/api/health", headers={"X-Vercel-ID": "sfo1.abc-123"})
    finally:
        app.state.vitadrive.store.close()

    generated = invalid.headers["X-Request-ID"]
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", generated)
    assert generated != "x" * 300
    assert fallback.headers["X-Request-ID"] == "sfo1.abc-123"


def test_invalid_vercel_id_is_replaced_and_static_requests_skip_access_log(tmp_path):
    app, client = _client(tmp_path, "static")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(observability.JsonFormatter())
    access_logger = logging.getLogger("vitadrive.access")
    access_logger.addHandler(handler)
    try:
        invalid = client.get("/api/health", headers={"X-Vercel-ID": "bad request id"})
        static = client.get("/static/index.html")
    finally:
        access_logger.removeHandler(handler)
        app.state.vitadrive.store.close()

    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", invalid.headers["X-Request-ID"])
    assert static.status_code == 200
    assert static.headers.get("X-Request-ID")
    assert stream.getvalue().count("\n") == 1


def test_configure_logging_is_idempotent_and_does_not_change_root_logger(monkeypatch):
    logger = logging.getLogger("vitadrive")
    root_handlers = list(logging.getLogger().handlers)
    uvicorn_handlers = list(logging.getLogger("uvicorn").handlers)
    previous_level = logger.level
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    observability.configure_logging()
    observability.configure_logging()

    try:
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], logging.StreamHandler)
        assert isinstance(logger.handlers[0].formatter, observability.JsonFormatter)
        assert logger.handlers[0].stream is not None
        assert logger.level == logging.DEBUG
        assert logging.getLogger().handlers == root_handlers
        assert logging.getLogger("uvicorn").handlers == uvicorn_handlers
    finally:
        logger.setLevel(previous_level)


def test_health_reports_database_revision_and_no_store(tmp_path):
    app, client = _client(tmp_path, "health")
    try:
        response = client.get("/api/health")
    finally:
        app.state.vitadrive.store.close()

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "version": app.version,
        "database": "ok",
        "db_revision": "0003",
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_health_returns_generic_error_when_database_is_unavailable(tmp_path):
    app, client = _client(tmp_path, "broken-health")
    store = app.state.vitadrive.store
    original_engine = store.engine
    broken_engine = create_engine("sqlite:////not-a-real-vitadrive-directory/db.sqlite")
    store.engine = broken_engine
    try:
        response = client.get("/api/health")
    finally:
        store.engine = original_engine
        broken_engine.dispose()
        store.close()

    assert response.status_code == 503
    assert response.json() == {"ok": False, "version": app.version, "database": "error"}
    assert response.headers["Cache-Control"] == "no-store"


def test_error_handlers_return_uniform_json_and_keep_exception_headers(tmp_path):
    app, client = _client(tmp_path, "errors", raise_server_exceptions=False)

    def not_found():
        raise HTTPException(404, "Missing resource")

    def unauthorized():
        raise HTTPException(401, "Sign in", headers={"WWW-Authenticate": "Bearer"})

    def typed(value: int):
        return {"value": value}

    def limited():
        raise RateLimited(17, "Please slow down")

    def explode():
        raise RuntimeError("sensitive exception text")

    app.add_api_route("/_test/not-found", not_found)
    app.add_api_route("/_test/unauthorized", unauthorized)
    app.add_api_route("/_test/typed/{value}", typed)
    app.add_api_route("/_test/limited", limited)
    app.add_api_route("/_test/explode", explode)

    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setFormatter(observability.JsonFormatter())
    package_logger = logging.getLogger("vitadrive")
    package_logger.addHandler(log_handler)
    try:
        unknown = client.get("/unknown", headers={"X-Request-ID": "unknown-1"})
        custom_404 = client.get("/_test/not-found", headers={"X-Request-ID": "http-404"})
        unauthorized_response = client.get("/_test/unauthorized", headers={"X-Request-ID": "http-401"})
        validation = client.get("/_test/typed/nope", headers={"X-Request-ID": "http-422"})
        rate_limited = client.get("/_test/limited", headers={"X-Request-ID": "http-429"})
        internal = client.get("/_test/explode", headers={"X-Request-ID": "http-500"})
    finally:
        package_logger.removeHandler(log_handler)
        app.state.vitadrive.store.close()

    unknown_body = unknown.json()
    assert unknown.status_code == 404
    assert unknown_body["detail"] == "Not Found"
    assert unknown_body["error"]["code"] == "not_found"
    assert unknown_body["error"]["request_id"] == unknown.headers["X-Request-ID"]

    custom_body = custom_404.json()
    assert custom_404.status_code == 404
    assert custom_body["detail"] == "Missing resource"
    assert custom_body["error"]["message"] == "Missing resource"
    assert custom_body["error"]["request_id"] == "http-404"

    unauthorized_body = unauthorized_response.json()
    assert unauthorized_response.status_code == 401
    assert unauthorized_response.headers["WWW-Authenticate"] == "Bearer"
    assert unauthorized_body["error"]["code"] == "unauthorized"
    assert unauthorized_body["error"]["request_id"] == "http-401"

    validation_body = validation.json()
    assert validation.status_code == 422
    assert isinstance(validation_body["detail"], list)
    assert validation_body["error"]["code"] == "validation_error"
    assert validation_body["error"]["message"].startswith("path.value:")
    assert validation_body["error"]["request_id"] == "http-422"

    limited_body = rate_limited.json()
    assert rate_limited.status_code == 429
    assert rate_limited.headers["Retry-After"] == "17"
    assert limited_body["detail"] == "Please slow down"
    assert limited_body["error"]["code"] == "rate_limited"
    assert limited_body["error"]["request_id"] == "http-429"

    internal_body = internal.json()
    assert internal.status_code == 500
    assert internal_body["detail"] == "Internal server error"
    assert "sensitive exception text" not in internal.text
    assert internal_body["error"]["code"] == "internal_error"
    assert internal_body["error"]["request_id"] == internal.headers["X-Request-ID"] == "http-500"
    entries = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    exception_entry = next(entry for entry in entries if entry["message"] == "Unhandled request exception")
    assert exception_entry["request_id"] == "http-500"
    assert "RuntimeError: sensitive exception text" in exception_entry["exc_info"]


def test_init_sentry_is_optional_and_disables_default_pii(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observability.init_sentry() is False

    import sentry_sdk

    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("VERCEL_ENV", "preview")

    assert observability.init_sentry() is True
    assert calls == [{
        "dsn": "https://public@example.invalid/1",
        "send_default_pii": False,
        "traces_sample_rate": 0.25,
        "environment": "preview",
        "release": __version__,
    }]
