"""Signed frame tokens: the only credential the template origin accepts (spec 10, binding (c)).

The website signs one token per frame it renders; the template origin accepts it only on a GET
of that one instance's raw document, never as a session. HS256 with a key of its own (never the
session, CSRF or MCP signing key), audience equal to the template origin, claims `user_id` and
`instance_id`, at most 5 minutes of life, and single use within this process (`jti`). With
several server processes a token could be replayed once per process inside its 5 minutes; the
deployment runs one.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from uuid import UUID

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

ALGORITHM = "HS256"
FRAME_TOKEN_SECONDS = 300  # the binding cap: 5 minutes or less
MAX_TOKEN_CHARS = 1024  # a real token is about 300; anything longer is refused unparsed
MIN_KEY_CHARS = 32
RAW_PATH = "/app/instances/{instance_id}/raw"

FrameUrl = Callable[[UUID, UUID], str]  # (user_id, instance_id) -> the frame's src


class FrameTokens:
    """Signs and verifies frame tokens for one template origin (the audience)."""

    def __init__(
        self,
        key: str,
        audience: str,
        *,
        clock: Callable[[], float] = time.time,
        ttl_s: int = FRAME_TOKEN_SECONDS,
    ) -> None:
        if len(key) < MIN_KEY_CHARS:
            raise ValueError(f"the frame token key must be at least {MIN_KEY_CHARS} characters")
        if not 0 < ttl_s <= FRAME_TOKEN_SECONDS:
            raise ValueError(f"frame tokens live at most {FRAME_TOKEN_SECONDS} seconds")
        self._key = OctKey.import_key(key)
        self.audience = audience
        self._clock = clock
        self._ttl_s = ttl_s
        self._used: dict[str, int] = {}  # jti -> exp, purged as tokens expire
        self._lock = threading.Lock()

    def sign(self, user_id: UUID, instance_id: UUID) -> str:
        now = int(self._clock())
        claims = {
            "aud": self.audience,
            "user_id": str(user_id),
            "instance_id": str(instance_id),
            "iat": now,
            "exp": now + self._ttl_s,
            "jti": secrets.token_urlsafe(16),
        }
        return jwt.encode({"alg": ALGORITHM}, claims, self._key, algorithms=[ALGORITHM])

    def verify(self, token: str, instance_id: UUID) -> UUID | None:
        """The token's user when it is valid for `instance_id` and unused; else None.

        Never raises on bad input and never says why: every refusal is the same 404."""
        if not token or len(token) > MAX_TOKEN_CHARS:
            return None
        now = int(self._clock())
        try:
            decoded = jwt.decode(token, self._key, algorithms=[ALGORITHM])
            registry = jwt.JWTClaimsRegistry(
                now=now,
                aud={"essential": True, "value": self.audience},
                exp={"essential": True},
                iat={"essential": True},
                jti={"essential": True},
                user_id={"essential": True},
                instance_id={"essential": True, "value": str(instance_id)},
            )
            registry.validate(decoded.claims)
        except (JoseError, ValueError, TypeError):
            return None
        claims = decoded.claims
        exp, iat, jti = claims["exp"], claims["iat"], claims["jti"]
        if not (isinstance(exp, int) and isinstance(iat, int) and isinstance(jti, str)):
            return None
        if exp - iat > FRAME_TOKEN_SECONDS or iat > now + 5:  # no long-lived or future tokens
            return None
        try:
            user_id = UUID(str(claims["user_id"]))
        except ValueError:
            return None
        with self._lock:
            self._used = {k: v for k, v in self._used.items() if v >= now}
            if jti in self._used:
                return None
            self._used[jti] = exp
        return user_id


def make_frame_url(tokens: FrameTokens, origin: str) -> FrameUrl:
    """The callable the website installs: a fresh token per frame, on the template origin."""

    def frame_url(user_id: UUID, instance_id: UUID) -> str:
        token = tokens.sign(user_id, instance_id)
        return f"{origin}{RAW_PATH.format(instance_id=instance_id)}?t={token}"

    return frame_url
