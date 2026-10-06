from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from text_service.server import create_app
from text_service.service import Service


@pytest.fixture
def client() -> Generator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


def test_app_uses_supplied_service() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    with TestClient(create_app(service)) as configured, TestClient(create_app()) as fresh:
        assert configured.post("/sessions", json=account).status_code == 200
        assert fresh.post("/sessions", json=account).status_code == 401


def test_http_account_flow(client: TestClient) -> None:
    """Task 1: the same account flow, seen through real HTTP requests."""
    assert client.get("/ping").json() == {"data": "pong"}
    account = {"username": "alice", "password": "password1"}
    assert client.post("/users", json=account).status_code == 201
    assert client.post("/users", json=account).status_code == 409
    assert (
        client.post("/sessions", json={**account, "password": "wrongpassword"}).status_code == 401
    )

    token = client.post("/sessions", json=account).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/texts", headers=headers).json() == {"data": []}

    # A token without the Bearer prefix carries no identity.
    assert client.get("/texts", headers={"Authorization": token}).status_code == 401
    assert client.get("/texts").status_code == 401

    assert client.delete("/sessions/current", headers=headers).status_code == 200
    assert client.get("/texts", headers=headers).status_code == 401


def test_http_echo(client: TestClient) -> None:
    """Task 2: echo over real HTTP, including Unicode and the empty string."""
    response = client.post("/echo", json={"text": "你好\nRM"})
    assert response.status_code == 200
    assert response.json() == {"data": "你好\nRM"}
    assert client.post("/echo", json={"text": ""}).json() == {"data": ""}
    assert client.post("/echo", json={"text": "a" * 65_536}).status_code == 200
    assert client.post("/echo", json={"text": "a" * 65_537}).status_code == 413


def test_http_echo_rejects_bad_payloads(client: TestClient) -> None:
    assert client.post("/echo", json={"text": 42}).status_code == 400
    assert client.post("/echo", json={}).status_code == 400
    assert client.post("/echo", json={"text": "a", "extra": "b"}).status_code == 400
    assert client.post("/echo", content=b"not JSON").status_code == 400


def test_http_text_round_trip(client: TestClient) -> None:
    """Task 3: upload and read a named text over real HTTP."""
    account = {"username": "alice", "password": "password1"}
    client.post("/users", json=account)
    token = client.post("/sessions", json=account).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.put("/texts/note", headers=headers, json={"text": "hello"}).status_code == 200
    assert client.get("/texts/note", headers=headers).json() == {"data": "hello"}
    assert client.get("/texts/note").status_code == 401

    # An ill-formed name is rejected before the token is ever inspected.
    assert client.get("/texts/n@me").status_code == 400
    assert client.get("/texts/a/b").status_code == 404
    assert client.get("/texts/missing", headers=headers).status_code == 404
    assert client.put("/texts/note", headers=headers, json={"text": 42}).status_code == 400

    # DELETE is still pending (task 4): the route is known, so this is 405,
    # not the 404 an unknown path would give. Flip to 200 once implemented.
    assert client.delete("/texts/note", headers=headers).status_code == 405


def test_http_routes(client: TestClient) -> None:
    assert client.get("/ping").status_code == 200
    response = client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = client.post("/sessions", json={"username": "alice", "password": "password1"})
    token = response.json()["data"]["token"]
    assert (client.get("/texts", headers={"Authorization": f"Bearer {token}"})).status_code == 200
    assert client.get("/texts").status_code == 401
    assert (
        client.post("/users", content=b"not JSON", headers={"Content-Type": "application/json"})
    ).status_code == 400
    assert (
        client.post("/users", content=b"x" * 524289, headers={"Content-Type": "application/json"})
    ).status_code == 413


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
def test_invalid_json(client: TestClient, body: bytes) -> None:
    assert client.post("/users", content=body).status_code == 400


def test_body_limit_and_routing(client: TestClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert client.post("/users", content=exact).status_code == 400
    assert client.post("/users", content=exact + b" ").status_code == 413
    assert client.get("/missing").status_code == 404
    assert client.get("/echo").status_code == 405
    assert client.patch("/ping").status_code == 405
    assert client.get("/ping?test=1").json() == {"data": "pong"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/users/me"),
    ],
)
def test_unimplemented_routes_are_absent(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 404


@pytest.mark.parametrize("path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts"])
def test_wrong_method_precedes_authentication(client: TestClient, path: str) -> None:
    assert client.patch(path).status_code == 405
