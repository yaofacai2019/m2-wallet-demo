"""Small in-memory session and role boundary for the internal trial."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie


@dataclass(frozen=True)
class Principal:
    username: str
    display_name: str
    role: str
    scopes: tuple[str, ...] = ()


class SessionAuth:
    def __init__(self, api_key: str, ttl_hours: int = 8):
        self.api_key = api_key
        self.ttl = timedelta(hours=ttl_hours)
        self._sessions: dict[str, tuple[Principal, datetime]] = {}
        self._lock = threading.RLock()
        self.users = self._load_users()

    @staticmethod
    def _load_users() -> dict[str, tuple[bytes, Principal]]:
        definitions = [
            ("admin", "M2_ADMIN_PASSWORD", "System Administrator", "ADMIN"),
            ("finance", "M2_FINANCE_PASSWORD", "Finance Administrator", "FINANCE"),
            ("operator", "M2_OPERATOR_PASSWORD", "Operations", "OPERATOR"),
            ("viewer", "M2_VIEWER_PASSWORD", "Read-only User", "VIEWER"),
        ]
        result = {}
        for username, env_name, display_name, role in definitions:
            password = os.environ.get(env_name)
            if not password:
                continue
            result[username] = (SessionAuth._password_hash(username, password), Principal(username, display_name, role))
        return result

    @staticmethod
    def _password_hash(username: str, password: str) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode(), f"m2-wallet:{username}".encode(), 200_000
        )

    def login(self, username: str, password: str) -> tuple[str, Principal] | None:
        record = self.users.get(username)
        supplied_hash = self._password_hash(username, password)
        if not record or not hmac.compare_digest(supplied_hash, record[0]):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (record[1], datetime.now(timezone.utc) + self.ttl)
        return token, record[1]

    def resolve(self, cookie_header: str, api_key: str) -> Principal | None:
        if api_key and hmac.compare_digest(api_key, self.api_key):
            return Principal("system-api", "System API", "ADMIN")
        cookie = SimpleCookie()
        cookie.load(cookie_header or "")
        morsel = cookie.get("m2_session")
        if not morsel:
            return None
        now = datetime.now(timezone.utc)
        with self._lock:
            record = self._sessions.get(morsel.value)
            if not record:
                return None
            if record[1] <= now:
                self._sessions.pop(morsel.value, None)
                return None
            return record[0]

    def logout(self, cookie_header: str) -> None:
        cookie = SimpleCookie()
        cookie.load(cookie_header or "")
        morsel = cookie.get("m2_session")
        if morsel:
            with self._lock:
                self._sessions.pop(morsel.value, None)
