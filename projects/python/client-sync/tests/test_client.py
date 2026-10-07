import json
import sys
from typing import Any

import httpx
import pytest

from text_service.client import exchange, read_text


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


def test_read_text_joins_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 2：多行输入按换行拼接，末尾不自动补换行。"""
    # 输入：第一行、第二行，然后单独一个 "." 结束
    answers = iter(["第一行", "第二行", "."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == "第一行\n第二行"


def test_read_text_escapes_leading_dot(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 2：正文里以 "." 开头的行用 ".." 转义，能原样还原。"""
    # ".." → "."，"..hidden" → ".hidden"；单独的 "." 仍然是结束标记
    answers = iter(["..", "..hidden", "."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == ".\n.hidden"


def test_read_text_supports_empty_and_blank_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 2：空字符串和"末尾换行"都要能表达。"""
    # 直接结束 → 空文本
    answers = iter(["."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == ""

    # 内容后面再敲一个空行 → 文本以换行结尾（这就是"末尾换行"的表达方式）
    answers = iter(["a", "", "."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == "a\n"

    # 中间夹空行也要保留
    answers = iter(["a", "", "b", "."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == "a\n\nb"

    # 两个空行 → 文本本身就是一个换行符
    answers = iter(["", "", "."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == "\n"


def test_read_text_keeps_unicode(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 2：中文、emoji 都要能原样读完。"""
    answers = iter(["你好 RM 😀", "."])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert read_text() == "你好 RM 😀"


def test_main_echo_sends_text_and_prints_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 2：echo 命令把输入放进 text 字段发出去，并显示服务器回显的内容。"""
    import text_service.client as module

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # 模拟服务端原样回显：收到什么就返回什么
        payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"data": payload["text"]})

    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", fake_client)
    monkeypatch.setattr(sys, "argv", ["rm-client"])
    # echo 后进入多行输入：两行内容 + 一个转义行，然后 "." 结束；最后 q 退出
    answers = iter(["echo", "第一行", "..以点开头", ".", "q"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))

    module.main()
    output = capsys.readouterr().out

    # 请求确实是 POST /echo，正文就是那三行拼起来的内容
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/echo"
    assert json.loads(seen[0].content.decode("utf-8")) == {"text": "第一行\n.以点开头"}
    # 回显结果打印出来了（状态码 200 + 内容）
    assert "200" in output
    assert "第一行" in output
    # 输入提示也要出现，用户才知道怎么结束输入
    assert '"."' in output


def test_main_echo_sends_empty_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 2：空文本也要能发出去（直接敲 "." 结束）。"""
    import text_service.client as module

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": ""})

    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", fake_client)
    monkeypatch.setattr(sys, "argv", ["rm-client"])
    answers = iter(["echo", ".", "q"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))

    module.main()
    assert json.loads(seen[0].content.decode("utf-8")) == {"text": ""}
    assert "200 {'data': ''}" in capsys.readouterr().out


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})
