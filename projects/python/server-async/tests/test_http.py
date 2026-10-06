from collections.abc import AsyncGenerator

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def test_http_account_flow(client: AsyncClient) -> None:
    """Task 1: the same account flow, seen through real HTTP requests."""
    assert (await client.get("/ping")).json() == {"data": "pong"}
    account = {"username": "alice", "password": "password1"}
    assert (await client.post("/users", json=account)).status_code == 201
    assert (await client.post("/users", json=account)).status_code == 409
    assert (
        await client.post("/sessions", json={**account, "password": "wrongpassword"})
    ).status_code == 401

    token = (await client.post("/sessions", json=account)).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/texts", headers=headers)).json() == {"data": []}

    # A token without the Bearer prefix carries no identity.
    assert (await client.get("/texts", headers={"Authorization": token})).status_code == 401
    assert (await client.get("/texts")).status_code == 401

    assert (await client.delete("/sessions/current", headers=headers)).status_code == 200
    assert (await client.get("/texts", headers=headers)).status_code == 401


async def test_http_echo(client: AsyncClient) -> None:
    """Task 2: echo over real HTTP, including Unicode and the empty string."""
    response = await client.post("/echo", json={"text": "你好\nRM"})
    assert response.status_code == 200
    assert response.json() == {"data": "你好\nRM"}
    assert (await client.post("/echo", json={"text": ""})).json() == {"data": ""}
    assert (await client.post("/echo", json={"text": "a" * 65_536})).status_code == 200
    assert (await client.post("/echo", json={"text": "a" * 65_537})).status_code == 413


async def test_http_echo_rejects_bad_payloads(client: AsyncClient) -> None:
    assert (await client.post("/echo", json={"text": 42})).status_code == 400
    assert (await client.post("/echo", json={})).status_code == 400
    assert (await client.post("/echo", json={"text": "a", "extra": "b"})).status_code == 400
    assert (await client.post("/echo", content=b"not JSON")).status_code == 400


async def test_http_text_round_trip(client: AsyncClient) -> None:
    """Task 3: upload and read a named text over real HTTP."""
    account = {"username": "alice", "password": "password1"}
    await client.post("/users", json=account)
    token = (await client.post("/sessions", json=account)).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.put("/texts/note", headers=headers, json={"text": "hello"})
    assert response.status_code == 200
    assert (await client.get("/texts/note", headers=headers)).json() == {"data": "hello"}
    assert (await client.get("/texts/note")).status_code == 401

    # An ill-formed name is rejected before the token is ever inspected.
    assert (await client.get("/texts/n@me")).status_code == 400
    assert (await client.get("/texts/a/b")).status_code == 404
    assert (await client.get("/texts/missing", headers=headers)).status_code == 404
    assert (await client.put("/texts/note", headers=headers, json={"text": 42})).status_code == 400


async def test_http_text_deletion(client: AsyncClient) -> None:
    """Task 4: deleting a text removes it from the list over real HTTP."""
    account = {"username": "alice", "password": "password1"}
    await client.post("/users", json=account)
    token = (await client.post("/sessions", json=account)).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.put("/texts/alpha", headers=headers, json={"text": "a"})
    await client.put("/texts/beta", headers=headers, json={"text": "b"})
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["alpha", "beta"]}

    response = await client.delete("/texts/alpha", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": None}
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["beta"]}
    assert (await client.delete("/texts/alpha", headers=headers)).status_code == 404
    assert (await client.delete("/texts/missing", headers=headers)).status_code == 404
    assert (await client.delete("/texts/beta")).status_code == 401


async def test_http_account_deletion(client: AsyncClient) -> None:
    """任务 5：通过真实 HTTP 注销账号，旧令牌失效，同名重新注册后是干净账号。"""
    account = {"username": "alice", "password": "password1"}
    await client.post("/users", json=account)
    token = (await client.post("/sessions", json=account)).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.put("/texts/note", headers=headers, json={"text": "hello"})
    response = await client.delete("/users/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": None}

    # 旧令牌不能再用；没有令牌也不能注销
    assert (await client.get("/texts", headers=headers)).status_code == 401
    assert (await client.delete("/users/me")).status_code == 401

    # 同名重新注册成功，而且看不到旧账号的任何文本
    assert (await client.post("/users", json=account)).status_code == 201
    new_token = (await client.post("/sessions", json=account)).json()["data"]["token"]
    new_headers = {"Authorization": f"Bearer {new_token}"}
    assert (await client.get("/texts", headers=new_headers)).json() == {"data": []}
    assert (await client.get("/texts/note", headers=new_headers)).status_code == 404


async def test_http_routes(client: AsyncClient) -> None:
    assert (await client.get("/ping")).status_code == 200
    response = await client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    token = response.json()["data"]["token"]
    assert (
        await client.get("/texts", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (await client.get("/texts")).status_code == 401
    assert (
        await client.post(
            "/users", content=b"not JSON", headers={"Content-Type": "application/json"}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/users", content=b"x" * 524289, headers={"Content-Type": "application/json"}
        )
    ).status_code == 413


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
async def test_invalid_json(client: AsyncClient, body: bytes) -> None:
    assert (await client.post("/users", content=body)).status_code == 400


async def test_body_limit_and_routing(client: AsyncClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert (await client.post("/users", content=exact)).status_code == 400
    assert (await client.post("/users", content=exact + b" ")).status_code == 413
    assert (await client.get("/missing")).status_code == 404
    assert (await client.get("/echo")).status_code == 405
    assert (await client.patch("/ping")).status_code == 405
    assert (await client.get("/ping?test=1")).json() == {"data": "pong"}


async def test_unknown_path_is_not_found(client: AsyncClient) -> None:
    """未知路径仍是 404。

    起始代码里那条「未实现的路由应返回 404」的断言，已随路由逐条实现而移除：
    /users/me 在任务 5 实现后，不带令牌请求它是 401（地方在，但你没资格），
    不再是 404。所以这里改用真正不存在的路径来保留 404 的覆盖。
    """
    assert (await client.get("/missing")).status_code == 404
    assert (await client.delete("/users/other")).status_code == 404


@pytest.mark.parametrize("path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts"])
async def test_wrong_method_precedes_authentication(client: AsyncClient, path: str) -> None:
    assert (await client.patch(path)).status_code == 405
