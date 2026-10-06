from text_service.service import Service


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


def test_token_header_forms() -> None:
    """Task 1: only a ``Bearer`` token identifies a user."""
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert service.handle("GET", "/texts", None, f"Bearer {token}") == (200, {"data": []})
    # A bare token, an empty header and other schemes all carry no identity.
    for header in (token, "", "Bearer ", f"Basic {token}", "bearer " + token):
        assert service.handle("GET", "/texts", None, header)[0] == 401


def test_echo_round_trip() -> None:
    """Task 2: echo returns the submitted text unchanged."""
    service = Service()
    for text in ("", "hello", "你好\nRM", "😀" * 100, "a" * 65_536):
        assert service.handle("POST", "/echo", {"text": text}, "") == (200, {"data": text})


def test_echo_rejects_bad_payloads() -> None:
    """Task 2: only a single ``text`` string field is accepted."""
    service = Service()
    for body in (None, [], {}, {"text": 42}, {"text": "a", "extra": "b"}, {"text": "\ud800"}):
        assert service.handle("POST", "/echo", body, "")[0] == 400


def test_echo_limit_counts_bytes() -> None:
    """Task 2: the cap is 65536 UTF-8 bytes, so fewer emoji than letters fit."""
    service = Service()
    assert service.handle("POST", "/echo", {"text": "😀" * 16_384}, "")[0] == 200
    assert service.handle("POST", "/echo", {"text": "😀" * 16_385}, "")[0] == 413


def test_text_round_trip() -> None:
    """Task 3: upload, overwrite and read a named text."""
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"

    assert service.handle("PUT", "/texts/note", {"text": "hello"}, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": "hello"})
    assert service.handle("PUT", "/texts/note", {"text": "again"}, auth)[0] == 200
    assert service.handle("GET", "/texts/note", None, auth) == (200, {"data": "again"})
    assert service.handle("GET", "/texts", None, auth) == (200, {"data": ["note"]})


def test_text_name_is_checked_before_authentication() -> None:
    """Task 3: an ill-formed name is 400 even with no token at all."""
    service = Service()
    # Ill-formed name: rejected before the token is ever inspected.
    assert service.handle("GET", "/texts/n@me", None, "")[0] == 400
    # Well-formed but unknown name: the missing token is what matters.
    assert service.handle("GET", "/texts/missing", None, "")[0] == 401
    # A name containing a slash does not match the route shape at all.
    assert service.handle("GET", "/texts/a/b", None, "")[0] == 404


def test_text_isolation_between_accounts() -> None:
    """Task 3: each account only sees its own texts."""
    service = Service()
    for name in ("alice", "bob"):
        account = {"username": name, "password": "password1"}
        assert service.handle("POST", "/users", account, "")[0] == 201
    tokens = {
        name: service.handle("POST", "/sessions", {"username": name, "password": "password1"}, "")[
            1
        ]["data"]["token"]
        for name in ("alice", "bob")
    }
    assert (
        service.handle("PUT", "/texts/note", {"text": "alice text"}, f"Bearer {tokens['alice']}")[0]
        == 200
    )
    assert service.handle("GET", "/texts", None, f"Bearer {tokens['bob']}") == (200, {"data": []})
    assert service.handle("GET", "/texts/note", None, f"Bearer {tokens['bob']}")[0] == 404


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
