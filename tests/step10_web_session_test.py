"""Step10：真实 HTTP 验证 Web 静态页与 HttpOnly 浏览器会话。

F08/F13 修复后的回归：随机端口 + 唯一临时库；退出在服务端撤销会话
（复制走的旧 cookie 重放 401）；cookie 写操作校验精确同源（跨源 403）。
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from _util import ROOT  # noqa: F401


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
Q_TOKEN = "test-human-token-00000000000000000001"
J_TOKEN = "test-companion-token-0000000000000000000001"


def open_json(opener, method: str, path: str, body=None, headers=None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    resp = opener.open(req, timeout=5)
    return resp, json.loads(resp.read().decode("utf-8"))


db_dir = pathlib.Path(tempfile.mkdtemp(prefix="superposition_step10_web_"))
db_path = db_dir / "web.db"

env = os.environ.copy()
env.update({
    "SUPERPOSITION_TOKEN_HUMAN": Q_TOKEN,
    "SUPERPOSITION_TOKEN_COMPANION": J_TOKEN,
    "SUPERPOSITION_DB_PATH": str(db_path),
    "SUPERPOSITION_TEST_INSTANCE": "1",
})

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=ROOT,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

try:
    for _ in range(40):
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.15)
    else:
        raise AssertionError("隔离 Web 服务没有启动成功")

    with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
        assert resp.status == 200 and "三只瓶子" in html and "/static/app.js" in html
    with urllib.request.urlopen(BASE + "/static/app.js", timeout=5) as resp:
        assert resp.status == 200 and b"refreshAll" in resp.read()

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_resp, identity = open_json(opener, "POST", "/api/auth/login", {"token": Q_TOKEN})
    cookie_header = login_resp.headers.get("Set-Cookie", "")
    assert identity["id"] == "human"
    assert "HttpOnly" in cookie_header and "SameSite=strict" in cookie_header
    assert any(c.name == "superposition_session" for c in jar)

    # ---- 界面分面：网页登录只属于人类；AI 伴侣（AI）必须走 MCP ----
    companion_jar = http.cookiejar.CookieJar()
    companion_opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(companion_jar))
    try:
        open_json(companion_opener, "POST", "/api/auth/login", {"token": J_TOKEN})
        raise AssertionError("AI 伴侣的凭证不应当能登录网页")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403 and "MCP" in exc.read().decode("utf-8")
    assert not any(c.name == "superposition_session" for c in companion_jar)

    _, me = open_json(opener, "GET", "/api/auth/me")
    _, counts = open_json(opener, "GET", "/api/bottles/counts")
    assert me["id"] == "human" and "shared_count" in counts

    no_cookie = urllib.request.build_opener()
    secure_resp, _ = open_json(
        no_cookie,
        "POST",
        "/api/auth/login",
        {"token": Q_TOKEN},
        {"X-Forwarded-Proto": "https"},
    )
    assert "Secure" in secure_resp.headers.get("Set-Cookie", "")

    # ---- F13：cookie 身份的写操作校验精确同源 ----
    # 同源 Origin（与 Host 完全一致）放行
    _, star_ok = open_json(opener, "POST", "/api/stars",
                           {"content": "同源写入的一颗", "visibility": "hidden"},
                           {"Origin": BASE})
    assert star_ok["state"] == "SEALED"
    # 同站不同源（端口不同）的 Origin 被拒绝，且不产生写入
    try:
        open_json(opener, "POST", "/api/stars",
                  {"content": "跨源注入", "visibility": "hidden"},
                  {"Origin": f"http://127.0.0.1:{free_port()}"})
        raise AssertionError("跨源 cookie 写请求应当被拒绝")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    _, counts_now = open_json(opener, "GET", "/api/bottles/counts")
    assert counts_now["my_private_count"] == 1, "被拒绝的跨源请求不得产生写入"

    # ---- F08：退出撤销服务端会话，复制走的旧 cookie 也不再生效 ----
    saved_cookie = next(c for c in jar if c.name == "superposition_session").value
    open_json(opener, "POST", "/api/auth/logout")
    try:
        open_json(opener, "GET", "/api/auth/me")
        raise AssertionError("退出后旧浏览器会话不应继续可用")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401

    replay = urllib.request.build_opener()
    replay.addheaders = [("Cookie", f"superposition_session={saved_cookie}")]
    try:
        replay.open(BASE + "/api/auth/me", timeout=5)
        raise AssertionError("被复制走的旧 cookie 在退出后必须失效（F08）")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401

    print("STEP 10 PASS —— Web 静态页 / HttpOnly 会话 / Secure HTTPS cookie / "
          "退出撤销 / 同源校验 全部通过")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    import shutil

    shutil.rmtree(db_dir, ignore_errors=True)
