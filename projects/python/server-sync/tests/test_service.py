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
