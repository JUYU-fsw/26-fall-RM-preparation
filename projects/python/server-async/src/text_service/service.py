"""In-memory baseline. Implement the task routes in handle()."""

import hashlib
import hmac
import re
import secrets
import threading
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

# 令牌默认有效期（秒），可用 --token-ttl-seconds 覆盖。
DEFAULT_TOKEN_TTL_SECONDS = 300.0

# Texts are measured in UTF-8 bytes, not characters. Echo shares this limit.
MAX_TEXT_BYTES = 65_536

# Text names are 1-64 ASCII letters, digits, underscores or hyphens.
TEXT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

ROUTES = (
    ("GET", "/ping"),
    ("POST", "/echo"),
    ("POST", "/users"),
    ("POST", "/sessions"),
    ("DELETE", "/sessions/current"),
    ("DELETE", "/users/me"),
    ("GET", "/texts"),
    ("PUT", "/texts/{name}"),
    ("GET", "/texts/{name}"),
    ("DELETE", "/texts/{name}"),
)

TEXT_PREFIX = "/texts/"
INVALID_NAME: tuple[int, dict[str, Any]] = (400, {"message": "Invalid text name"})
TEXT_MISSING: tuple[int, dict[str, Any]] = (404, {"message": "Text not found"})


def match_route(method: str, path: str) -> tuple[int | None, str | None]:
    """Match a request against ROUTES.

    Returns ``(error_status, path_parameter)``. ``error_status`` is ``None``
    when the route matched, and ``path_parameter`` holds ``{name}`` if present.
    """
    known = False
    for verb, route in ROUTES:
        if route == path:
            if verb == method:
                return None, None
            known = True
            continue
        if route.endswith("/{name}") and path.startswith(TEXT_PREFIX):
            candidate = path[len(TEXT_PREFIX) :]
            if "/" not in candidate:
                if verb == method:
                    return None, candidate
                # Keep scanning: a later route may still accept this method.
                known = True
    return (405 if known else 404), None


def route_error(method: str, path: str) -> int | None:
    """Return the status for an unmatched route, or ``None`` when matched."""
    status, _ = match_route(method, path)
    return status


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
    # 令牌的到期时刻，用 monotonic() 的读数表示；None 表示当前没有有效令牌。
    expires_at: float | None = None
    texts: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(self, token_ttl_seconds: float = DEFAULT_TOKEN_TTL_SECONDS) -> None:
        self.users: dict[str, User] = {}
        self.lock = threading.Lock()
        # 令牌有效期：登录后固定，后续任何操作都不会把它往后推。
        self.token_ttl_seconds = token_ttl_seconds

    def handle(
        self, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        status, name = match_route(method, path)
        if status is not None:
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
                # 到期时刻 = 现在 + 有效期。用 monotonic 而不是墙上时钟，
                # 这样系统时间被调整也不会让令牌提前或延后失效。
                user.expires_at = monotonic() + self.token_ttl_seconds
                # expires_in 告诉客户端这次令牌能用多久（整数秒）。
                return 200, {
                    "data": {
                        "token": user.token,
                        "expires_in": int(self.token_ttl_seconds),
                    }
                }
        # The text name is checked before authentication, matching the
        # reference program: an ill-formed name is 400 even without a token.
        if name is not None and not TEXT_NAME.fullmatch(name):
            return INVALID_NAME
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        with self.lock:
            # 连同用户名一起取出来：注销需要把整个账号从字典里删掉，
            # 光拿到 user 对象不知道它挂在哪个名字下。
            found = next(
                ((name, u) for name, u in self.users.items() if token and u.token == token),
                None,
            )
            if found is None:
                return 401, {"message": "Login required"}
            username, user = found
            # 到期即失效，而且这一路只是「检查」：读一次时钟，不会延长有效期。
            if user.expires_at is None or monotonic() >= user.expires_at:
                return 401, {"message": "Login required"}
            if path == "/sessions/current" and method == "DELETE":
                # 退出：令牌和到期时刻一起清掉。
                user.token = None
                user.expires_at = None
                return 200, {"data": None}
            if path == "/users/me" and method == "DELETE":
                # 注销：把账号整个从表里删掉，它的文本和令牌随之一起消失。
                # 这里必须是「删除这个账号」而不是「只把 token 清空」：
                # 只清 token 的话，这个对象还留在 users 里，一个注销前发出、
                # 此刻才算完密码的旧登录请求，仍能匹配到它并重新发令牌。
                del self.users[username]
                return 200, {"data": None}
            if path == "/texts" and method == "GET":
                return 200, {"data": sorted(user.texts)}
            if name is not None:
                if method == "PUT":
                    if error := validate_text_body(body):
                        return error
                    user.texts[name] = body["text"]
                    return 200, {"data": None}
                if name not in user.texts:
                    return TEXT_MISSING
                if method == "GET":
                    return 200, {"data": user.texts[name]}
                # 走到这里只剩 DELETE：文本确认存在，删除并返回 200。
                del user.texts[name]
                return 200, {"data": None}
        return 404, {"message": "Not found"}
