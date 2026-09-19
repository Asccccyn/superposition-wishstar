"""Step15 验证：远程 MCP 连接器的 OAuth 2.1 全流程 + 网页密码登录。

模拟 claude.ai 添加自定义连接器时发生的完整握手：
  1. 无凭证 POST /mcp → 401 + WWW-Authenticate(resource_metadata, RFC 9728)
  2. /.well-known/oauth-protected-server / authorization-server 元数据
  3. 动态客户端注册（RFC 7591，client_id 确定性可复现）
  4. /mcp/oauth/authorize 登录页；错密码不放行，阶梯锁定，对密码 302 带 code
  5. /mcp/oauth/token：PKCE S256 换 access/refresh；错 verifier 拒绝
  6. Bearer 调 /mcp initialize 成功；伪造 token 仍 401；refresh 可轮换
  7. 网页 /api/auth/login 同框接受密码（错密码 401）
  8. SUPERPOSITION_MCP_HTTP=0 时 /mcp 整体关闭

运行：python tests/step15_remote_mcp_oauth_test.py"""
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from _util import ROOT  # noqa: F401


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
Q_TOKEN = "step15-human-token-0000000000001"
J_TOKEN = "step15-companion-token-000000000000001"
Q_PW = "step15-qiao-pass-4270"
J_PW = "step15-jia-pass-8190"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"

db_dir = tempfile.mkdtemp(prefix="superposition_step15_")
env = os.environ.copy()
env.update({
    "SUPERPOSITION_TOKEN_HUMAN": Q_TOKEN,
    "SUPERPOSITION_TOKEN_COMPANION": J_TOKEN,
    "SUPERPOSITION_PASSWORD_HUMAN": Q_PW,
    "SUPERPOSITION_PASSWORD_COMPANION": J_PW,
    "SUPERPOSITION_DB_PATH": os.path.join(db_dir, "step15.db"),
    "SUPERPOSITION_TEST_INSTANCE": "1",
    # 阶梯锁定加速档：阈值 2，两级锁 3s/6s（默认是 5 次→60s/300s/900s）
    "SUPERPOSITION_LOGIN_LOCKOUT": "2:3:6:9",
})

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app",
     "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=ROOT, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirect)
_urlopen = urllib.request.urlopen


def call(method, path, body=None, headers=None, form=None, follow=True):
    data = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    open_it = _urlopen if follow else _no_redirect_opener.open
    try:
        with open_it(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    line = f"  [{tag}] {name}"
    if not cond and extra:
        line += f"  -> {extra[:200]}"
    print(line)
    assert cond, name


try:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            s, _, _ = call("GET", "/api/health")
            if s == 200:
                break
        except Exception:
            pass
        time.sleep(0.15)
    else:
        raise AssertionError("隔离服务未启动")

    # ---- 1. 无凭证访问 /mcp：401 + RFC 9728 元数据头 ----
    s, h, b = call("POST", "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                         "params": {"protocolVersion": "2025-03-26",
                                                    "capabilities": {},
                                                    "clientInfo": {"name": "t", "version": "0"}}})
    check("无凭证 POST /mcp -> 401", s == 401)
    check("WWW-Authenticate 指向授权服务器元数据",
          "resource_metadata" in h.get("www-authenticate", "")
          and "/.well-known/oauth-authorization-server" in h.get("www-authenticate", ""),
          h.get("www-authenticate", ""))

    # ---- 2. 发现端点 ----
    s, _, b = call("GET", "/.well-known/oauth-protected-resource")
    meta_pr = json.loads(b)
    check("protected-resource 元数据", s == 200 and meta_pr["resource"].endswith("/mcp")
          and meta_pr["authorization_servers"])
    s, _, b = call("GET", "/.well-known/oauth-authorization-server")
    meta_as = json.loads(b)
    check("authorization-server 元数据",
          s == 200 and meta_as["response_types_supported"] == ["code"]
          and "S256" in meta_as["code_challenge_methods_supported"]
          and meta_as["registration_endpoint"].endswith("/mcp/oauth/register"))

    # ---- 3. 动态注册：client_id 确定性 ----
    s, _, b = call("POST", "/mcp/oauth/register",
                   body={"client_name": "claude.ai", "redirect_uris": [REDIRECT]})
    reg = json.loads(b)
    check("动态客户端注册", s == 200 and reg["client_id"].startswith("cl_"))
    s2, _, b2 = call("POST", "/mcp/oauth/register",
                     body={"client_name": "again", "redirect_uris": [REDIRECT]})
    check("注册无状态：同一 redirect_uri 得到同一 client_id",
          json.loads(b2)["client_id"] == reg["client_id"])

    # ---- 4. 登录页 + 密码 ----
    verifier = "step15-verifier-" + "x" * 32
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": reg["client_id"],
        "redirect_uri": REDIRECT, "state": "st-123",
        "code_challenge": challenge, "code_challenge_method": "S256"})
    s, _, b = call("GET", f"/mcp/oauth/authorize?{q}")
    check("授权页是登录表单", s == 200 and "密码" in b and "授权连接" in b)
    s, _, b = call("POST", "/mcp/oauth/authorize", form={"password": "wrong-password-8",
                                                         "q": q})
    check("错密码不放行（无 302）", s == 200 and "密码不对" in b)
    # 阶梯锁定（测试档阈值2/锁3s）：第2次错触发锁定
    s, _, b = call("POST", "/mcp/oauth/authorize", form={"password": "wrong-password-8",
                                                         "q": q})
    check("达到阈值触发锁定提示", s == 200 and "次数过多" in b and "秒" in b)
    # 锁定期间连正确密码也拒绝
    s, _, b = call("POST", "/mcp/oauth/authorize", form={"password": J_PW, "q": q},
                   follow=False)
    check("锁定期间正确密码也被拒（无 302）", s == 200 and "次数过多" in b)
    # 等锁过期后，正确密码恢复放行（计数已归零）
    time.sleep(3.2)
    s, h, b = call("POST", "/mcp/oauth/authorize", form={"password": J_PW, "q": q},
                   follow=False)
    loc = h.get("location", "")
    check("正确密码 302 到回调并携带 code+state",
          s == 302 and loc.startswith(REDIRECT) and "code=" in loc and "state=st-123" in loc,
          loc)
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)["code"][0]

    # ---- 5. 换令牌：PKCE ----
    s, _, b = call("POST", "/mcp/oauth/token", form={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "client_id": reg["client_id"],
        "code_verifier": "wrong-verifier-" + "y" * 32})
    check("错 PKCE verifier 被拒绝", s == 400)
    # 授权码一次性：上面的失败已消费掉 code？——失败不应消费，重取一枚
    s, h, b = call("POST", "/mcp/oauth/authorize", form={"password": Q_PW, "q": q},
                   follow=False)
    code = urllib.parse.parse_qs(
        urllib.parse.urlsplit(h.get("location", "")).query)["code"][0]
    s, _, b = call("POST", "/mcp/oauth/token", form={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT, "code_verifier": verifier})
    tokens = json.loads(b)
    check("正确 PKCE 换得 Bearer 令牌",
          s == 200 and tokens.get("token_type") == "Bearer"
          and tokens.get("access_token") and tokens.get("refresh_token"))

    # ---- 6. 带 Bearer 调 MCP ----
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "step15", "version": "0"}}}
    s, _, b = call("POST", "/mcp", body=init,
                   headers={"Authorization": f"Bearer {tokens['access_token']}",
                            "Accept": "application/json, text/event-stream"})
    result = json.loads(b)
    check("Bearer initialize 成功",
          s == 200 and result.get("result", {}).get("serverInfo", {}).get("name")
          == "superposition-wish-star", b)
    # 工具调用也走通一道：列工具
    s, _, b = call("POST", "/mcp", body={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                   headers={"Authorization": f"Bearer {tokens['access_token']}",
                            "Accept": "application/json, text/event-stream"})
    tools = json.loads(b).get("result", {}).get("tools", [])
    check("tools/list 可见（含 write_star / take_session_star）",
          s == 200 and any(t["name"] == "write_star" for t in tools)
          and any(t["name"] == "take_session_star" for t in tools))
    s, _, b = call("POST", "/mcp", body=init,
                   headers={"Authorization": "Bearer forged.token.here"})
    check("伪造 token 仍 401", s == 401)

    # ---- 7. refresh 轮换 ----
    s, _, b = call("POST", "/mcp/oauth/token",
                   form={"grant_type": "refresh_token",
                         "refresh_token": tokens["refresh_token"]})
    t2 = json.loads(b)
    check("refresh_token 可换新对", s == 200 and t2.get("access_token")
          and t2["access_token"] != tokens["access_token"])
    s, _, b = call("POST", "/mcp", body=init,
                   headers={"Authorization": f"Bearer {t2['access_token']}",
                            "Accept": "application/json, text/event-stream"})
    check("轮换后的 access 同样可用", s == 200)

    # ---- 8. 网页登录同框接受密码（界面分面：网页只属于对方） ----
    s, _, b = call("POST", "/api/auth/login", body={"token": Q_PW})
    check("网页用密码登录对方", s == 200 and json.loads(b)["name"] == "人类")
    s, _, b = call("POST", "/api/auth/login", body={"token": J_TOKEN})
    check("伴侣的钥匙在网页被拒并提示走 MCP", s == 403 and "MCP" in b)
    s, _, b = call("POST", "/api/auth/login", body={"token": "not-a-password-8"})
    check("错密码 401", s == 401)
    s, _, b = call("POST", "/api/auth/login", body={"token": "not-a-password-8"})
    check("达到阈值后网页登录返回 429 锁定", s == 429)
    s, _, b = call("POST", "/api/auth/login", body={"token": J_PW})
    check("网页登录锁定期间正确密码也 429", s == 429)
    time.sleep(3.2)
    s, _, b = call("POST", "/api/auth/login", body={"token": Q_PW})
    check("锁过期后对方密码恢复登录", s == 200 and json.loads(b)["name"] == "人类")
    s, _, b = call("POST", "/api/auth/login", body={"token": J_PW})
    check("锁过期后伴侣密码在网页仍是 403", s == 403 and "MCP" in b)

    # ---- 9. 未知路径仍是普通 404（不被网关吞成 401） ----
    s, _, b = call("GET", "/favicon.ico")
    check("未知路径普通 404", s == 404)

    print("\nSTEP 15 PASS —— 远程 MCP OAuth 2.1 全流程 / 密码登录 / 令牌轮换 全部通过")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(db_dir, ignore_errors=True)


# ---- 关闭开关的独立小节 ----
def test_disabled_by_env() -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    d = tempfile.mkdtemp(prefix="superposition_step15_off_")
    env2 = os.environ.copy()
    env2.update({
        "SUPERPOSITION_TOKEN_HUMAN": Q_TOKEN,
        "SUPERPOSITION_TOKEN_COMPANION": J_TOKEN,
        "SUPERPOSITION_DB_PATH": os.path.join(d, "off.db"),
        "SUPERPOSITION_TEST_INSTANCE": "1",
        "SUPERPOSITION_MCP_HTTP": "0",
    })
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env2,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=5) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.15)
        req = urllib.request.Request(base + "/mcp", data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404, f"SUPERPOSITION_MCP_HTTP=0 时 /mcp 应关闭(404)，得到 {status}"
        print("  [PASS] SUPERPOSITION_MCP_HTTP=0 时 /mcp 关闭（404）")
        print("STEP 15b PASS —— 关闭开关生效")
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        shutil.rmtree(d, ignore_errors=True)


test_disabled_by_env()
