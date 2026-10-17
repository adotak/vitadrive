"""HTTP security headers and content security policy construction."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def build_csp(clerk_frontend_api: str | None) -> str:
    fapi = f"https://{clerk_frontend_api.strip().rstrip('/')}" if clerk_frontend_api else None
    scripts = ["'self'", "'unsafe-inline'", "https://unpkg.com", "https://challenges.cloudflare.com"]
    connections = ["'self'"]
    frames = ["https://challenges.cloudflare.com"]
    if fapi:
        scripts.extend((fapi, "https://*.protect.clerk.com"))
        connections.extend((
            fapi,
            "https://clerk-telemetry.com",
            "https://*.clerk-telemetry.com",
            "https://*.protect.clerk.com:*",
        ))
        frames.extend((fapi, "https://*.protect.clerk.com"))

    # 'unsafe-inline' remains necessary until PR 7 moves the inline script into modules.
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        f"script-src {' '.join(scripts)}",
        "style-src 'self' 'unsafe-inline' https://unpkg.com",
        ("img-src 'self' data: blob: https://unpkg.com https://tile.openstreetmap.org "
         "https://*.tile.openstreetmap.org https://upload.wikimedia.org "
         "https://thumb.wikimedia.org https://img.clerk.com"),
        "font-src 'self' data:",
        "media-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self' blob:",
        f"connect-src {' '.join(connections)}",
        f"frame-src {' '.join(frames)}",
    ]
    return "; ".join(directives)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that adds security headers to HTTP responses."""

    def __init__(self, app: ASGIApp, csp: str) -> None:
        self.app = app
        self.csp = csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "").rstrip("/")
        forwarded_proto = next(
            (
                value.split(b",", 1)[0].strip().lower()
                for name, value in scope.get("headers", ())
                if name.lower() == b"x-forwarded-proto"
            ),
            b"",
        )
        is_https = scope.get("scheme") == "https" or forwarded_proto == b"https"

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                existing = {name.lower() for name, _ in headers}
                additions = []
                if path not in {"/docs", "/redoc"} and b"content-security-policy" not in existing:
                    additions.append((b"content-security-policy", self.csp.encode("latin-1")))
                for name, value in (
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"x-frame-options", b"DENY"),
                    (b"permissions-policy", b"geolocation=(self), camera=(), microphone=(), payment=(), usb=()"),
                    (b"cross-origin-opener-policy", b"same-origin-allow-popups"),
                ):
                    if name not in existing:
                        additions.append((name, value))
                if is_https and b"strict-transport-security" not in existing:
                    additions.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
                if additions:
                    message = {**message, "headers": [*headers, *additions]}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
