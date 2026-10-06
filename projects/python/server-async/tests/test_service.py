import pytest

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


def test_text_deletion_and_list() -> None:
    """Task 4: deleting removes the name, a repeat is 404, the list stays sorted."""
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"

    assert service.handle("GET", "/texts", None, auth) == (200, {"data": []})
    for name in ("delta", "alpha", "charlie"):
        assert service.handle("PUT", f"/texts/{name}", {"text": name}, auth)[0] == 200
    # The list is ordered by name, not by upload order.
    assert service.handle("GET", "/texts", None, auth) == (
        200,
        {"data": ["alpha", "charlie", "delta"]},
    )

    assert service.handle("DELETE", "/texts/charlie", None, auth) == (200, {"data": None})
    assert service.handle("GET", "/texts", None, auth) == (200, {"data": ["alpha", "delta"]})
    assert service.handle("GET", "/texts/charlie", None, auth)[0] == 404
    # Deleting again, or a name never uploaded, is 404 rather than 200.
    assert service.handle("DELETE", "/texts/charlie", None, auth)[0] == 404
    assert service.handle("DELETE", "/texts/never", None, auth)[0] == 404
    # A valid token is still required, and the name is still checked first.
    assert service.handle("DELETE", "/texts/alpha", None, "")[0] == 401
    assert service.handle("DELETE", "/texts/n@me", None, auth)[0] == 400


def test_text_deletion_is_isolated() -> None:
    """Task 4: removing one account's copy leaves another account's copy intact."""
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
    for owner, text in (("alice", "alice text"), ("bob", "bob text")):
        body = {"text": text}
        assert service.handle("PUT", "/texts/note", body, f"Bearer {tokens[owner]}")[0] == 200

    assert service.handle("DELETE", "/texts/note", None, f"Bearer {tokens['alice']}") == (
        200,
        {"data": None},
    )
    assert service.handle("GET", "/texts", None, f"Bearer {tokens['alice']}") == (200, {"data": []})
    assert service.handle("GET", "/texts/note", None, f"Bearer {tokens['bob']}") == (
        200,
        {"data": "bob text"},
    )
    assert service.handle("GET", "/texts", None, f"Bearer {tokens['bob']}") == (
        200,
        {"data": ["note"]},
    )


def test_account_deletion_clears_texts_and_token() -> None:
    """任务 5：注销清掉账号本身、它的全部文本和令牌，同名可重新注册且是干净的。"""
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"

    assert service.handle("PUT", "/texts/note", {"text": "hello"}, auth)[0] == 200
    assert service.handle("DELETE", "/users/me", None, auth) == (200, {"data": None})
    # 旧令牌立刻失效：读列表、读文本、写文本全都不行
    assert service.handle("GET", "/texts", None, auth)[0] == 401
    assert service.handle("GET", "/texts/note", None, auth)[0] == 401
    assert service.handle("PUT", "/texts/note", {"text": "again"}, auth)[0] == 401
    # 账号已不存在，所以同名重新注册是 201 而不是 409
    assert service.handle("POST", "/users", account, "")[0] == 201
    new_token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    new_auth = f"Bearer {new_token}"
    assert service.handle("GET", "/texts", None, new_auth) == (200, {"data": []})
    assert service.handle("GET", "/texts/note", None, new_auth)[0] == 404


def test_stale_login_cannot_touch_recreated_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 5：注销前发出、注销后才算完密码的登录，不能作用到同名的新账号上。"""
    import text_service.service as module

    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"
    real_pbkdf2 = module.hashlib.pbkdf2_hmac
    triggered: list[bool] = []

    def slow_pbkdf2(*args: object, **kwargs: object) -> bytes:
        # 只在第一次进入时插桩：制造「旧登录还在算密码」这个时间窗口。
        # 这个窗口里账号被注销、并且有人用同名重新注册了一个新账号。
        if not triggered:
            triggered.append(True)
            assert service.handle("DELETE", "/users/me", None, auth) == (200, {"data": None})
            assert service.handle("POST", "/users", account, "")[0] == 201
        # 注册时也会走到这里，靠 triggered 保证只插桩一次，避免递归。
        return real_pbkdf2(*args, **kwargs)

    monkeypatch.setattr(module.hashlib, "pbkdf2_hmac", slow_pbkdf2)
    # 这次登录拿到的是注销前的旧账号对象，所以即使密码正确也必须失败，
    # 否则它就会给「同名的新账号」发一张令牌。
    assert service.handle("POST", "/sessions", account, "")[0] == 401


def test_concurrent_deletion_and_text_write() -> None:
    """任务 5：注销与写文本并发时，写操作不会把已注销的账号「复活」。"""
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"
    assert service.handle("PUT", "/texts/note", {"text": "hello"}, auth)[0] == 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_job = pool.submit(service.handle, "DELETE", "/users/me", None, auth)
        write_job = pool.submit(service.handle, "PUT", "/texts/note", {"text": "again"}, auth)
        statuses = [delete_job.result()[0], write_job.result()[0]]
    # 注销一定成功；写操作可能 200（抢在注销前）也可能 401（抢在注销后），
    # 但无论先后顺序，账号都不会被重新建立起来。
    assert statuses[0] == 200
    assert statuses[1] in (200, 401)
    assert "alice" not in service.users


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
