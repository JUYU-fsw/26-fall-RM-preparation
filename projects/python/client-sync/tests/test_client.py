import sys
from typing import Any

import httpx
import pytest

from text_service.client import exchange


def test_exchange_omits_header_without_token() -> None:
    """任务 1：没有令牌时，请求里不应该出现 Authorization 头。"""

    def respond(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": "pong"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/ping") == (200, {"data": "pong"})


def test_exchange_reports_status_for_non_json_body() -> None:
    """任务 1：响应体不是 JSON 时，状态码仍然要能拿到。"""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server exploded")

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/texts", "example")
        assert status == 500
        assert result == {"message": "server exploded"}


def test_main_account_flow(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """任务 1：命令循环里登录后保存令牌、受保护请求带上它、退出后清除。"""
    import text_service.client as module

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/ping":
            return httpx.Response(200, json={"data": "pong"})
        if path == "/users":
            return httpx.Response(201, json={"data": {"username": "alice"}})
        if path == "/sessions":
            return httpx.Response(200, json={"data": {"token": "tok123"}})
        if path == "/texts":
            # 带令牌才放行：这样能看出客户端到底有没有把令牌带上
            if "Authorization" in request.headers:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(401, json={"message": "Login required"})
        if path == "/sessions/current":
            return httpx.Response(200, json={"data": None})
        return httpx.Response(404, json={"message": "Not found"})

    # 让 main() 里的 httpx.Client 走 MockTransport，不发真实网络请求
    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", fake_client)
    # main() 会读 sys.argv，pytest 自己的参数会让它报错
    monkeypatch.setattr(sys, "argv", ["rm-client"])
    # 依次喂给 input()：命令，以及注册/登录时要输入的用户名
    answers = iter(["ping", "register", "alice", "login", "alice", "list", "logout", "list", "q"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    monkeypatch.setattr(module.getpass, "getpass", lambda *args: "password1")

    module.main()
    output = capsys.readouterr().out

    # 每个命令都打印了状态码和结果
    assert "200 {'data': 'pong'}" in output
    assert "201 {'data': {'username': 'alice'}}" in output
    assert "200 {'data': {'token': 'tok123'}}" in output
    assert "200 {'data': []}" in output
    assert "401 {'message': 'Login required'}" in output
    # 401 会提示重新登录
    assert "Please log in again." in output

    # 关键：/texts 被请求了两次，一次带令牌、一次不带
    texts = [
        request.headers.get("Authorization") for request in seen if request.url.path == "/texts"
    ]
    assert texts == ["Bearer tok123", None]


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})
