"""Web Push notifications to the owner's phone/computer when a reading turns critical.

Turned on by setting ``VAPID_PUBLIC_KEY`` and ``VAPID_PRIVATE_KEY`` (make a pair with ``vitadrive vapid-keys``).
"""

from __future__ import annotations

import base64
import json
import logging
import os

from pydantic import BaseModel, Field, field_validator

from .models import Alert, Severity

log = logging.getLogger(__name__)
DEFAULT_SUBJECT = "mailto:alerts@vitadrive.app"
GONE = (404, 410)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=10, max_length=100)


class PushSubscription(BaseModel):
    """What the browser's ``PushSubscription.toJSON()`` gives us."""

    endpoint: str = Field(max_length=2000)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("endpoint must be an https:// URL")
        return v


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keys() -> tuple[str, str]:
    """A new VAPID key pair as (public, private), both base64url (the format browsers and pywebpush expect)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(public), _b64(key.private_numbers().private_value.to_bytes(32, "big"))


def _env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip().strip("'\"") or None


def public_key() -> str | None:
    return _env("VAPID_PUBLIC_KEY") if enabled() else None


def enabled() -> bool:
    return bool(_env("VAPID_PUBLIC_KEY") and _env("VAPID_PRIVATE_KEY"))


def _deliver(sub: dict, data: str, ttl: int) -> int:
    """Send one push message; returns the push service's HTTP status (0 if it couldn't be reached)."""
    from pywebpush import WebPushException, webpush

    try:
        return webpush(subscription_info=sub, data=data, vapid_private_key=_env("VAPID_PRIVATE_KEY"),
                       vapid_claims={"sub": _env("VAPID_SUBJECT") or DEFAULT_SUBJECT}, ttl=ttl, timeout=5).status_code
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else 0
    except Exception as exc:  # network errors must never break reading ingestion
        log.warning("push delivery failed: %s", type(exc).__name__)
        return 0


def send_to_user(store, owner_id: str, payload: dict, ttl: int = 86_400) -> int:
    """Push ``payload`` to every device the user turned alerts on for. Returns how many accepted it."""
    if not enabled():
        return 0
    sent, data = 0, json.dumps(payload)
    for sub in store.push_subscriptions(owner_id):
        status = _deliver(sub, data, ttl)
        if status in GONE:  # the browser unsubscribed or the user cleared site data
            store.delete_push_subscription(sub["endpoint"])
        elif 200 <= status < 300:
            sent += 1
    return sent


def alert_payload(vehicle, alerts: list[Alert]) -> dict:
    n = len(alerts)
    return {"title": f"{vehicle.year} {vehicle.make} {vehicle.model}: {n} critical {'issue' if n == 1 else 'issues'}",
            "body": " · ".join(a.message for a in alerts[:3]) + (f" (+{n - 3} more)" if n > 3 else ""),
            "url": f"/?vin={vehicle.vin}", "tag": f"vitadrive-{vehicle.vin}"}


def notify_critical(store, vin: str, alerts: list[Alert]) -> int:
    """Alert the owner about newly raised critical alerts. Never raises."""
    critical = [a for a in alerts if a.severity is Severity.CRITICAL]
    if not critical or not enabled():
        return 0
    try:
        owner, vehicle = store.vehicle_owner(vin), store.get_vehicle(vin)
        return send_to_user(store, owner, alert_payload(vehicle, critical)) if owner and vehicle else 0
    except Exception as exc:
        log.warning("push notify failed: %s", type(exc).__name__)
        return 0
