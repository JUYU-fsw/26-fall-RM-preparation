import argparse
import getpass
from typing import Any

import httpx

# 多行文本的输入约定（协议允许"输入约定自行选择"，这是我们定的）：
#   1. 单独一行 "." 表示输入结束
#   2. 正文里某一行如果本身要以 "." 开头，就写成 ".." 开头（第一个点只当转义符）
#   3. 行与行之间用换行符拼接，末尾不自动补换行 ——
#      想让文本以换行结尾，就自己敲一个空行，再敲 "." 结束
TEXT_HINT = '输入文本，单独一行 "." 结束；正文行首是 "." 时写成 ".."'
END_MARK = "."


def read_text() -> str:
    """从终端读一段多行文本，按上面的约定把输入还原成原始内容。"""
    print(TEXT_HINT)
    lines: list[str] = []
    while True:
        line = input("| ")
        # 单独一个点就是结束标记，不作为内容
        if line == END_MARK:
            break
        # ".." 开头表示这一行的真实内容以 "." 开头，去掉第一个点
        lines.append(line[1:] if line.startswith("..") else line)
    # 用换行拼接，末尾不补换行：用户没敲就不加
    return "\n".join(lines)


def exchange(
    client: httpx.Client, method: str, path: str, token: str = "", body: object = None
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, json=body, headers=headers)
    try:
        result = response.json()
    except ValueError:
        result = {"message": response.text}
    return response.status_code, result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7878")
    args = parser.parse_args()
    token = ""
    with httpx.Client(
        base_url=args.url, timeout=12, follow_redirects=False, trust_env=False
    ) as client:
        try:
            while True:
                command = input(
                    "ping / register / login / logout / list / echo / "
                    "delete-user / put / get / delete / q > "
                ).strip()
                body = None
                if command == "q":
                    break
                if command in ("register", "login"):
                    body = {
                        "username": input("username: "),
                        "password": getpass.getpass("password: "),
                    }
                    method, path = "POST", "/users" if command == "register" else "/sessions"
                elif command in ("ping", "logout", "list"):
                    method, path = {
                        "ping": ("GET", "/ping"),
                        "logout": ("DELETE", "/sessions/current"),
                        "list": ("GET", "/texts"),
                    }[command]
                elif command == "echo":
                    body = {"text": read_text()}
                    method, path = "POST", "/echo"
                elif command == "put":
                    # 先问名字，再读多行文本；名字拼进路径，文本放进请求体
                    name = input("name: ")
                    body = {"text": read_text()}
                    method, path = "PUT", f"/texts/{name}"
                elif command == "get":
                    # 读取只需要名字，请求体为空，令牌由 exchange 自动带上
                    method, path = "GET", f"/texts/{input('name: ')}"
                elif command == "delete":
                    # 删除只需要名字：拼进路径，不需要请求体；令牌由 exchange 自动带上
                    method, path = "DELETE", f"/texts/{input('name: ')}"
                elif command == "delete-user":
                    print("This task is not implemented in the starting code yet.")
                    continue
                else:
                    print("Unknown command.")
                    continue
                try:
                    status, result = exchange(client, method, path, token, body)
                    print(status, result)
                    if command == "login" and status == 200:
                        token = result["data"]["token"]
                    if status == 401:
                        print("Please log in again.")
                    if status == 401 or (command == "logout" and status == 200):
                        token = ""
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f"Request failed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()
