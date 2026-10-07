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


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    respond: Any,
    answers: list[str],
) -> tuple[list[httpx.Request], str]:
    """小工具：用假服务端跑一遍 main()，返回（收到的请求，屏幕输出）。"""
    import text_service.client as module

    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)

    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(record)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", fake_client)
    # main() 会读 sys.argv，pytest 自己的参数会让它报错
    monkeypatch.setattr(sys, "argv", ["rm-client"])
    # 按顺序喂给 input()；密码走 getpass，单独顶掉
    queue = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *args: next(queue))
    monkeypatch.setattr(module.getpass, "getpass", lambda *args: "password1")

    module.main()
    return seen, capsys.readouterr().out


def test_main_text_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 3：put 上传、同名覆盖，get 读回的必须是最新内容；退出后不能再读。"""
    # 假服务端：真的把文本存起来，这样才验得到"覆盖后读回新内容"
    store: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users":
            return httpx.Response(201, json={"data": {"username": "alice"}})
        if path == "/sessions":
            return httpx.Response(200, json={"data": {"token": "tok123"}})
        if path == "/sessions/current":
            return httpx.Response(200, json={"data": None})
        if path.startswith("/texts/"):
            # 受保护接口：没有令牌一律 401
            if "Authorization" not in request.headers:
                return httpx.Response(401, json={"message": "Login required"})
            name = path.removeprefix("/texts/")
            if request.method == "PUT":
                store[name] = json.loads(request.content.decode("utf-8"))["text"]
                return httpx.Response(200, json={"data": None})
            if name in store:
                return httpx.Response(200, json={"data": store[name]})
            return httpx.Response(404, json={"message": "Text not found"})
        return httpx.Response(404, json={"message": "Not found"})

    seen, output = _run_main(
        monkeypatch,
        capsys,
        respond,
        [
            "register",
            "alice",  # 注册
            "login",
            "alice",  # 登录拿令牌
            "put",
            "note",
            "第一版",
            ".",  # 上传第一版
            "put",
            "note",
            "第二版",
            ".",  # 同名覆盖
            "get",
            "note",  # 读回
            "logout",  # 退出
            "get",
            "note",  # 退出后再读
            "q",
        ],
    )

    # 同名上传是覆盖，不是追加：最后只剩第二版
    assert store == {"note": "第二版"}
    # 读回的内容显示在屏幕上
    assert "200 {'data': '第二版'}" in output

    # 四次文本请求：两次 PUT、两次 GET，都打在 /texts/note 上
    text_requests = [request for request in seen if request.url.path == "/texts/note"]
    assert [request.method for request in text_requests] == ["PUT", "PUT", "GET", "GET"]
    # 前三次带着令牌（登录后），最后一次不带（退出后）
    assert [request.headers.get("Authorization") for request in text_requests] == [
        "Bearer tok123",
        "Bearer tok123",
        "Bearer tok123",
        None,
    ]
    # 退出后读取是 401，并提示重新登录
    assert "401 {'message': 'Login required'}" in output
    assert "Please log in again." in output


def test_main_put_without_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 3：没登录就 put，服务端返回 401，什么也没存下。"""
    store: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/texts/"):
            if "Authorization" not in request.headers:
                return httpx.Response(401, json={"message": "Login required"})
            store["note"] = json.loads(request.content.decode("utf-8"))["text"]
            return httpx.Response(200, json={"data": None})
        return httpx.Response(404, json={"message": "Not found"})

    seen, output = _run_main(monkeypatch, capsys, respond, ["put", "note", "内容", ".", "q"])

    # 请求确实发出去了：名字拼进了路径，文本放进请求体
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/texts/note"
    assert json.loads(seen[0].content.decode("utf-8")) == {"text": "内容"}
    # 但没有令牌，服务端什么都没存下
    assert "Authorization" not in seen[0].headers
    assert store == {}
    assert "401 {'message': 'Login required'}" in output


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})


def test_main_delete_updates_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 4：删除后列表里不再有该名称；重复删除同一个不存在的名称返回 404。"""
    store: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users":
            return httpx.Response(201, json={"data": {"username": "alice"}})
        if path == "/sessions":
            return httpx.Response(200, json={"data": {"token": "tok123"}})
        if path == "/sessions/current":
            return httpx.Response(200, json={"data": None})
        if path == "/texts":
            # 受保护接口：没有令牌一律 401；有令牌则返回按名称升序的列表
            if "Authorization" not in request.headers:
                return httpx.Response(401, json={"message": "Login required"})
            return httpx.Response(200, json={"data": sorted(store)})
        if path.startswith("/texts/"):
            # 文本接口同样要先验令牌
            if "Authorization" not in request.headers:
                return httpx.Response(401, json={"message": "Login required"})
            name = path.removeprefix("/texts/")
            if request.method == "PUT":
                # 上传：把文本存进 store（同名直接覆盖）
                store[name] = json.loads(request.content.decode("utf-8"))["text"]
                return httpx.Response(200, json={"data": None})
            if request.method == "DELETE":
                # 删得掉才删，删不掉（不存在）就 404
                if name in store:
                    del store[name]
                    return httpx.Response(200, json={"data": None})
                return httpx.Response(404, json={"message": "Text not found"})
            if name in store:
                return httpx.Response(200, json={"data": store[name]})
            return httpx.Response(404, json={"message": "Text not found"})
        return httpx.Response(404, json={"message": "Not found"})

    seen, output = _run_main(
        monkeypatch,
        capsys,
        respond,
        [
            "register",
            "alice",  # 注册
            "login",
            "alice",  # 登录拿令牌
            "put",
            "note",
            "内容",
            ".",  # 上传一个文本
            "list",  # 列表里应有 note
            "delete",
            "note",  # 删除它
            "list",  # 列表应变空
            "delete",
            "note",  # 再删一次 → 404
            "q",
        ],
    )

    # 删成功了：store 里不再有 note
    assert store == {}
    # 删除前列表含 note，删除后列表为空
    assert "200 {'data': ['note']}" in output
    assert "200 {'data': []}" in output
    # 重复删除不存在的名称返回 404
    assert "404 {'message': 'Text not found'}" in output

    # 确认真的发了两条 DELETE 请求到 /texts/note
    delete_requests = [
        req for req in seen if req.method == "DELETE" and req.url.path == "/texts/note"
    ]
    assert len(delete_requests) == 2


def test_main_get_missing_text_shows_404(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 4：获取不存在的文本时，屏幕要显示 404。"""

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users":
            return httpx.Response(201, json={"data": {"username": "alice"}})
        if path == "/sessions":
            return httpx.Response(200, json={"data": {"token": "tok123"}})
        if path.startswith("/texts"):
            # 任何文本请求都先验令牌；这里故意让文本不存在，统一返回 404
            if "Authorization" not in request.headers:
                return httpx.Response(401, json={"message": "Login required"})
            return httpx.Response(404, json={"message": "Text not found"})
        return httpx.Response(404, json={"message": "Not found"})

    _, output = _run_main(
        monkeypatch,
        capsys,
        respond,
        ["register", "alice", "login", "alice", "get", "ghost", "q"],
    )
    # 读到不存在的文本，屏幕要显示 404
    assert "404 {'message': 'Text not found'}" in output


def test_main_delete_user_clears_local_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 5：注销成功后本地令牌被清除，后续受保护请求因没令牌而 401。"""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/users":
            return httpx.Response(201, json={"data": {"username": "alice"}})
        if path == "/sessions":
            return httpx.Response(200, json={"data": {"token": "tok123"}})
        if path == "/users/me":
            # 注销成功：账号与令牌在服务端被删
            return httpx.Response(200, json={"data": None})
        if path == "/texts":
            # 没有令牌（或令牌无效）一律 401
            if "Authorization" not in request.headers:
                return httpx.Response(401, json={"message": "Login required"})
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={"message": "Not found"})

    seen, output = _run_main(
        monkeypatch,
        capsys,
        respond,
        ["register", "alice", "login", "alice", "delete-user", "list", "q"],
    )

    # 注销请求确实是 DELETE /users/me
    delete_user = [req for req in seen if req.method == "DELETE" and req.url.path == "/users/me"]
    assert len(delete_user) == 1
    # 注销后发出的 list 不再带令牌（本地 token 已被清空）
    text_requests = [req for req in seen if req.url.path == "/texts"]
    assert text_requests[-1].headers.get("Authorization") is None
    # 屏幕提示重新登录
    assert "401 {'message': 'Login required'}" in output
    assert "Please log in again." in output


def test_main_reregister_same_name_has_empty_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 5：注销会删掉账号与文本；同名重新注册、登录后列表应为空。"""
    # 模拟服务端状态：账号 -> 文本；token -> 账号
    accounts: dict[str, dict[str, str]] = {}
    token_for: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users":
            username = json.loads(request.content.decode("utf-8"))["username"]
            accounts[username] = {}  # 新账号，文本先为空
            return httpx.Response(201, json={"data": {"username": username}})
        if path == "/sessions":
            username = json.loads(request.content.decode("utf-8"))["username"]
            token = "tok_" + username
            token_for[token] = username
            return httpx.Response(200, json={"data": {"token": token}})
        if path == "/users/me":
            # 注销：删账号（含文本）+ 作废令牌
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            user = token_for.pop(token, None)
            if user is not None:
                accounts.pop(user, None)
            return httpx.Response(200, json={"data": None})
        if path == "/texts":
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            user = token_for.get(token)
            if user is None:
                return httpx.Response(401, json={"message": "Login required"})
            return httpx.Response(200, json={"data": sorted(accounts[user])})
        if path.startswith("/texts/"):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            user = token_for.get(token)
            if user is None:
                return httpx.Response(401, json={"message": "Login required"})
            name = path.removeprefix("/texts/")
            if request.method == "PUT":
                accounts[user][name] = json.loads(request.content.decode("utf-8"))["text"]
                return httpx.Response(200, json={"data": None})
            if name in accounts[user]:
                return httpx.Response(200, json={"data": accounts[user][name]})
            return httpx.Response(404, json={"message": "Text not found"})
        return httpx.Response(404, json={"message": "Not found"})

    _seen, output = _run_main(
        monkeypatch,
        capsys,
        respond,
        [
            "register",
            "alice",  # 第一次注册
            "login",
            "alice",  # 登录
            "put",
            "note",
            "内容",
            ".",  # 上传一个文本
            "list",  # 注销前：列表应有 note
            "delete-user",  # 注销（删账号 + 文本）
            "register",
            "alice",  # 同名重新注册（新账号）
            "login",
            "alice",  # 重新登录
            "list",  # 注销后重注册：列表应为空
            "q",
        ],
    )

    # 注销前列表含 note
    assert "200 {'data': ['note']}" in output
    # 同名重注册、重新登录后，列表应为空（旧数据已被注销清除）
    assert "200 {'data': []}" in output


def test_exchange_reports_status_for_empty_body() -> None:
    """任务 6：响应体缺失（如 204 无内容）时，状态码仍然要能拿到。"""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)  # 没有响应体

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "DELETE", "/users/me", "example")
        assert status == 204
        # 空 body 不是 JSON，回退成 message=空字符串，而不是崩溃
        assert result == {"message": ""}


def test_main_network_errors_continue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 6：连接错误与超时都属于网络错误，都应被捕获且命令循环继续。"""

    # 前两次请求分别模拟"连不上"和"超时"，之后恢复正常，用来证明一次失败不会让程序退出
    errors = [httpx.ConnectError("connection refused"), httpx.TimeoutException("timed out")]
    calls = {"n": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        i = calls["n"]
        calls["n"] += 1
        if i < len(errors):
            raise errors[i]
        return httpx.Response(200, json={"data": "pong"})

    _seen, output = _run_main(
        monkeypatch,
        capsys,
        respond,
        ["ping", "ping", "ping", "q"],
    )
    # 两次网络错误都被捕获并提示，而不是让程序崩掉
    assert output.count("Request failed") == 2
    # 错误之后还能继续：最后一次 ping 拿到了 200
    assert "200 {'data': 'pong'}" in output


def test_main_ctrl_c_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """任务 6：常规 Ctrl-C（input 抛 KeyboardInterrupt）应当干净退出，不把异常冒泡出来。"""
    import text_service.client as module

    # 第一次读取命令就模拟用户按了 Ctrl-C
    def raise_keyboard_interrupt(*args: Any) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_keyboard_interrupt)
    monkeypatch.setattr(sys, "argv", ["rm-client"])

    # 顶掉 httpx.Client，避免真的去连网（Ctrl-C 发生在发请求之前）
    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", fake_client)

    # 关键：main() 必须正常返回（KeyboardInterrupt 被内部 except 接住），不能冒泡出来
    module.main()
    # 退出时打印了一个换行（对应 except 分支里的 print()）
    assert capsys.readouterr().out == "\n"


def test_main_sets_finite_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 6：客户端使用有限超时（timeout 不是 None），避免无限等待。"""
    import text_service.client as module

    captured: dict[str, Any] = {}
    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "Client", fake_client)
    monkeypatch.setattr(sys, "argv", ["rm-client"])

    # 立刻 Ctrl-C，避免真的发请求
    def raise_keyboard_interrupt(*args: Any) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_keyboard_interrupt)

    module.main()
    # timeout 不是 None 即代表设定了上限（None 在 httpx 里表示不限制，会无限等）
    assert captured.get("timeout") is not None
