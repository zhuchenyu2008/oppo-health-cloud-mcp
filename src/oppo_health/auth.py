"""Single-user browser session and password hashing."""
from __future__ import annotations

import secrets
import time
from collections import deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from starlette.requests import Request

PASSWORD_HASHER = PasswordHasher()


def verify_password(password: str, encoded: str) -> bool:
    if not encoded:
        return False
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


@dataclass
class Session:
    token: str
    username: str
    csrf: str
    expires_at: float




class LoginRateLimiter:
    """Per-client in-memory limiter: at most five failed attempts per 15 minutes."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 15 * 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.failures: dict[str, deque[float]] = {}

    def _recent(self, client_key: str) -> deque[float]:
        now = time.time()
        recent = self.failures.setdefault(client_key, deque())
        while recent and now - recent[0] >= self.window_seconds:
            recent.popleft()
        return recent

    def allowed(self, client_key: str) -> bool:
        return len(self._recent(client_key)) < self.max_attempts

    def retry_after(self, client_key: str) -> int:
        recent = self._recent(client_key)
        if len(recent) < self.max_attempts:
            return 0
        return max(1, int(self.window_seconds - (time.time() - recent[0])))

    def failed(self, client_key: str) -> None:
        self._recent(client_key).append(time.time())

    def succeeded(self, client_key: str) -> None:
        self.failures.pop(client_key, None)


class SessionStore:
    def __init__(self, ttl_seconds: int = 12 * 3600):
        self.ttl_seconds = ttl_seconds
        self.sessions: dict[str, Session] = {}

    def create(self, username: str) -> Session:
        now = time.time()
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires_at >= now}
        token = secrets.token_urlsafe(32)
        session = Session(token, username, secrets.token_urlsafe(24), time.time() + self.ttl_seconds)
        self.sessions[token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        session = self.sessions.get(token)
        if not session or session.expires_at < time.time():
            self.sessions.pop(token, None)
            return None
        return session

    def revoke(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)


def session_from_request(request: Request, sessions: SessionStore) -> Session | None:
    return sessions.get(request.cookies.get("oppo_session"))
