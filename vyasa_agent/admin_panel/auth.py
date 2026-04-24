"""Auth primitives for the admin panel (design-06 §3).

Two planes, never mixed:

* :class:`GatewayBearer` — opaque ``vya_live_…`` tokens stored under the
  ``channels.gateway.tokens`` setting. Validated on every inbound message /
  dispatch / handoff. CSRF-exempt: no cookies, no ambient auth.
* :class:`SessionAuth`  — signed session cookie (``vya_sid``) paired with a
  double-submit CSRF token (``X-CSRF-Token``). Required on every non-gateway
  write.

The cookie is signed with ``itsdangerous`` if installed; otherwise we fall
back to a stdlib HMAC stamp so the admin panel boots on a minimal install.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, Request, status


SESSION_COOKIE = "vya_sid"
CSRF_COOKIE = "vya_csrf"
CSRF_HEADER = "X-CSRF-Token"
GATEWAY_TOKEN_PREFIX = "vya_live_"


class SettingsReader(Protocol):
    """Minimal view of :class:`SettingsStore` this module depends on."""

    def get(self, key: str) -> Any | None: ...


# --------------------------------------------------------------------------- #
# Gateway bearer                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class GatewayBearer:
    """Verifies the ``Authorization: Bearer vya_live_…`` header.

    Tokens live under ``channels.gateway.tokens`` as a list of
    ``{token, scope, label}`` objects. Comparison uses
    :func:`hmac.compare_digest` to avoid timing leaks.
    """

    settings: SettingsReader
    settings_key: str = "channels.gateway.tokens"

    def verify(self, request: Request) -> dict[str, str]:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
            )
        token = header.split(" ", 1)[1].strip()
        if not token.startswith(GATEWAY_TOKEN_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
            )

        configured = self.settings.get(self.settings_key) or []
        if not isinstance(configured, list):
            configured = []
        for row in configured:
            if not isinstance(row, dict):
                continue
            candidate = row.get("token", "")
            if not candidate:
                continue
            if hmac.compare_digest(str(candidate), token):
                return {
                    "token_label": str(row.get("label", "")),
                    "scope": str(row.get("scope", "adapter")),
                }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )


# --------------------------------------------------------------------------- #
# Session cookie + CSRF                                                       #
# --------------------------------------------------------------------------- #


class SessionAuth:
    """Stateless signed-cookie session with double-submit CSRF.

    The cookie payload is ``<subject>.<issued_at>.<hmac>``. We intentionally
    avoid ``itsdangerous`` as a hard dependency — the extra ``admin`` group
    pulls it in, but the base install works without.

    TODO(Dharma HIGH): bind the CSRF token to the session subject via HMAC
    (``csrf = HMAC(secret, subject||issued_at)``) so a cookie-tossing or
    XSS-on-a-sibling-subdomain attacker cannot forge a valid pair. The
    current double-submit only compares cookie to header, which is the
    weak variant of the pattern.
    """

    def __init__(
        self,
        secret_key: bytes,
        *,
        ttl_seconds: int = 60 * 60 * 8,
    ) -> None:
        if not secret_key:
            raise ValueError("secret_key must not be empty")
        self._secret = secret_key
        self._ttl = ttl_seconds

    # ---- issuing --------------------------------------------------------

    def issue_session(self, subject: str) -> tuple[str, str]:
        """Return a ``(session_cookie, csrf_token)`` pair for a fresh login."""
        issued_at = str(int(time.time()))
        payload = f"{subject}.{issued_at}"
        sig = self._sign(payload)
        session_cookie = f"{payload}.{sig}"
        csrf_token = secrets.token_urlsafe(32)
        return session_cookie, csrf_token

    # ---- verifying ------------------------------------------------------

    def verify(self, request: Request, *, require_csrf: bool) -> dict[str, str]:
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing session cookie",
            )
        try:
            subject, issued_at, sig = cookie.rsplit(".", 2)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="malformed session cookie",
            ) from exc

        expected = self._sign(f"{subject}.{issued_at}")
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid session signature",
            )
        try:
            issued = int(issued_at)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid session timestamp",
            ) from exc
        if issued + self._ttl < int(time.time()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session expired",
            )

        if require_csrf:
            csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
            csrf_header = request.headers.get(CSRF_HEADER, "")
            if not csrf_cookie or not csrf_header:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="csrf token required",
                )
            if not hmac.compare_digest(csrf_cookie, csrf_header):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="csrf token mismatch",
                )

        return {"subject": subject, "issued_at": issued_at}

    # ---- helpers --------------------------------------------------------

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).digest()
        return digest.hex()


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "GATEWAY_TOKEN_PREFIX",
    "GatewayBearer",
    "SESSION_COOKIE",
    "SessionAuth",
]
