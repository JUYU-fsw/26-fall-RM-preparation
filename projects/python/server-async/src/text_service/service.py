"""In-memory baseline. Implement the task routes in handle()."""

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

# Texts are measured in UTF-8 bytes, not characters. Echo shares this limit.
MAX_TEXT_BYTES = 65_536

ROUTES = (
    ("GET", "/ping"),
    ("POST", "/echo"),
    ("POST", "/users"),
    ("POST", "/sessions"),
    ("DELETE", "/sessions/current"),
    ("GET", "/texts"),
)


def route_error(method: str, path: str) -> int | None:
    allowed = next((verb for verb, route in ROUTES if route == path), None)
    if allowed is None:
        return 404
    return None if method == allowed else 405


BAD_TEXT_FIELD: tuple[int, dict[str, Any]] = (
    400,
    {"message": "Expected only the string field text"},
)
TEXT_TOO_LARGE: tuple[int, dict[str, Any]] = (
    413,
    {"message": "Text exceeds 65536 UTF-8 bytes"},
)


def validate_text_body(body: Any) -> tuple[int, dict[str, Any]] | None:
    """Validate a ``{"text": str}`` payload.

    Returns ``None`` when the payload is acceptable, otherwise the error
    response to send. Shared by echo and PUT /texts/{name}.
    """
    if not isinstance(body, dict) or set(body) != {"text"} or not isinstance(body["text"], str):
        return BAD_TEXT_FIELD
    try:
        encoded = body["text"].encode("utf-8")
    except UnicodeEncodeError:
        # Lone surrogates survive JSON decoding but are not valid text.
        return BAD_TEXT_FIELD
    if len(encoded) > MAX_TEXT_BYTES:
        return TEXT_TOO_LARGE
    return None


@dataclass
class User:
    salt: bytes
    digest: bytes
    token: str | None = None
    texts: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.lock = threading.Lock()

    def handle(
        self, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if status := route_error(method, path):
            return status, {"message": "Not found" if status == 404 else "Method not allowed"}
        if method == "GET" and path == "/ping":
            return 200, {"data": "pong"}
        if method == "POST" and path == "/echo":
            if error := validate_text_body(body):
                return error
            return 200, {"data": body["text"]}
        if path in ("/users", "/sessions") and method == "POST":
            if not isinstance(body, dict) or set(body) != {"username", "password"}:
                return 400, {"message": "Expected username and password"}
            name, password = body["username"], body["password"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name)
                or not isinstance(password, str)
                or not 8 <= len(password) <= 128
            ):
                return 400, {"message": "Invalid username or password length"}
            try:
                password.encode("utf-8")
            except UnicodeError:
                return 400, {"message": "Password must be valid Unicode"}
            # Hashing is outside the state lock; commit/check against current state under lock.
            if path == "/users":
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
                with self.lock:
                    if name in self.users:
                        return 409, {"message": "Username exists"}
                    self.users[name] = User(salt, digest)
                return 201, {"data": {"username": name}}
            with self.lock:
                user = self.users.get(name)
                if user is None:
                    return 401, {"message": "Invalid username or password"}
                salt, expected = user.salt, user.digest
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
            with self.lock:
                if self.users.get(name) is not user or not hmac.compare_digest(digest, expected):
                    return 401, {"message": "Invalid username or password"}
                user.token = secrets.token_urlsafe(32)
                # Later server task: record a deadline and return expires_in.
                return 200, {"data": {"token": user.token}}
        protected = path in ("/texts", "/sessions/current")
        if protected:
            token = (
                authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            )
            with self.lock:
                user = next((u for u in self.users.values() if token and u.token == token), None)
                if user is None:
                    return 401, {"message": "Login required"}
                # Later server task: check token expiry here, before reading or modifying state.
                if path == "/sessions/current" and method == "DELETE":
                    user.token = None
                    return 200, {"data": None}
                if path == "/texts" and method == "GET":
                    return 200, {"data": sorted(user.texts)}
        return 404, {"message": "Not found"}
