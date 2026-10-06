from text_service.service import MAX_TEXT_BYTES, Service


def test_account_lifecycle() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("GET", "/ping", None, "") == (200, {"data": "pong"})
    assert service.handle("POST", "/users", account, "")[0] == 201
    assert service.handle("POST", "/users", account, "")[0] == 409
    assert service.handle("POST", "/sessions", {**account, "password": "incorrect"}, "")[0] == 401
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    next_token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert token != next_token
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}") == (200, {"data": []})
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {next_token}")[0] == 200
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}")[0] == 401


def test_existing_account_flow() -> None:
    """Task 1: verify the account flow the starter code already provides."""
    service = Service()
    assert service.handle("GET", "/ping", None, "") == (200, {"data": "pong"})
    account = {"username": "alice", "password": "password1"}

    # Registering twice is a conflict; the second attempt must not overwrite.
    assert service.handle("POST", "/users", account, "") == (201, {"data": {"username": "alice"}})
    assert service.handle("POST", "/users", account, "")[0] == 409

    # A wrong password must be rejected before any token is issued.
    wrong = {**account, "password": "wrongpassword"}
    assert service.handle("POST", "/sessions", wrong, "")[0] == 401

    # Logging in again replaces the previous token instead of adding a second one.
    first = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    second = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert first != second
    assert service.handle("GET", "/texts", None, f"Bearer {first}")[0] == 401
    assert service.handle("GET", "/texts", None, f"Bearer {second}") == (200, {"data": []})

    # Logging out invalidates the current token.
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {second}") == (
        200,
        {"data": None},
    )
    assert service.handle("GET", "/texts", None, f"Bearer {second}")[0] == 401

    # A missing or unprefixed token is never accepted.
    assert service.handle("GET", "/texts", None, "")[0] == 401
    assert service.handle("GET", "/texts", None, second)[0] == 401


def test_echo_returns_text_unchanged() -> None:
    """Task 2: echo preserves Unicode, newlines and the empty string."""
    service = Service()
    for text in ("", "hello", "你好", "line1\nline2\n", "😀", "a" * MAX_TEXT_BYTES):
        assert service.handle("POST", "/echo", {"text": text}, "") == (200, {"data": text})


def test_echo_rejects_bad_payloads() -> None:
    """Task 2: echo accepts only a single string ``text`` field."""
    service = Service()
    for body in (
        None,
        [],
        {},
        {"text": 42},
        {"text": None},
        {"text": "a", "extra": "b"},
        {"body": "a"},
    ):
        assert service.handle("POST", "/echo", body, "")[0] == 400
    # A lone surrogate survives JSON decoding but is not valid Unicode text.
    assert service.handle("POST", "/echo", {"text": "\ud800"}, "")[0] == 400


def test_echo_size_limit_counts_utf8_bytes() -> None:
    """Task 2: the limit is 65536 UTF-8 bytes, not 65536 characters."""
    service = Service()
    assert service.handle("POST", "/echo", {"text": "a" * 65_536}, "")[0] == 200
    assert service.handle("POST", "/echo", {"text": "a" * 65_537}, "")[0] == 413
    # One emoji costs four bytes, so the limit arrives a quarter as soon.
    assert service.handle("POST", "/echo", {"text": "😀" * 16_384}, "")[0] == 200
    assert service.handle("POST", "/echo", {"text": "😀" * 16_385}, "")[0] == 413


def test_text_round_trip() -> None:
    """Task 3: PUT stores a text, GET returns it unchanged."""
    service = Service()
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"

    assert service.handle("PUT", "/texts/note", {"text": "你好\nRM"}, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": "你好\nRM"})
    # Uploading the same name replaces the content.
    assert service.handle("PUT", "/texts/note", {"text": "second"}, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": "second"})
    # The empty string is a real text, not a missing one.
    assert service.handle("PUT", "/texts/empty", {"text": ""}, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts/empty", None, auth) == (200, {"data": ""})


def test_text_requires_authentication() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]

    for method, body in (("PUT", {"text": "a"}), ("GET", None)):
        assert service.handle(method, "/texts/note", body, "")[0] == 401
        assert service.handle(method, "/texts/note", body, f"Bearer {token}x")[0] == 401


def test_text_name_is_checked_before_authentication() -> None:
    """Task 3: an ill-formed name is 400 even when no token is sent."""
    service = Service()
    for name in ("", "n@me", "x" * 65, "你好"):
        assert service.handle("GET", f"/texts/{name}", None, "")[0] == 400
    # A well-formed name with no token reaches authentication instead.
    assert service.handle("GET", "/texts/note", None, "")[0] == 401
    # A name containing a slash never matches the route shape at all.
    assert service.handle("GET", "/texts/a/b", None, "")[0] == 404


def test_text_missing_and_payload_errors() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"

    assert service.handle("GET", "/texts/missing", None, auth)[0] == 404
    assert service.handle("PUT", "/texts/note", {"text": 42}, auth)[0] == 400
    assert service.handle("PUT", "/texts/note", {"text": "a" * 65_537}, auth)[0] == 413


def test_validation() -> None:
    service = Service()
    for body in (
        None,
        [],
        {},
        {"username": True, "password": "password1"},
        {"username": "a/b", "password": "password1"},
    ):
        assert service.handle("POST", "/users", body, "")[0] == 400


def test_concurrent_registration() -> None:
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    body = {"username": "alice", "password": "password1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: service.handle("POST", "/users", body, "")[0], range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]
