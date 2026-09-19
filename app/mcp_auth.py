"""手机端 claude.ai 远程 MCP 连接器的第一方 OAuth 2.1 授权服务。

形态：在 claude.ai 添加自定义连接器（URL 指向 /mcp）时，会先收到 401 +
WWW-Authenticate（RFC 9728），随后自动走标准流程：

    /.well-known/oauth-protected-resource      → 指出授权服务器
    /.well-known/oauth-authorization-server    → 授权服务器元数据
    POST /mcp/oauth/register                   → 动态客户端注册（RFC 7591）
    GET  /mcp/oauth/authorize                  → 登录页（输密码）
    POST /mcp/oauth/authorize                  → 校验密码，302 带 code
    POST /mcp/oauth/token                      → 换 access/refresh token（PKCE S256）

之后每次 MCP 调用携带 Authorization: Bearer <access_token>。

密码来源：用户级环境变量 SUPERPOSITION_PASSWORD_HUMAN / _COMPANION，
与网页登录共用（routes.login 同时接受密码或访问钥匙）。

实现取舍（两用户家庭系统）：
  * access/refresh 均为本地 HS256 JWT，无状态，不落库；
  * client_id 由 redirect_uri 经 HMAC 确定性导出——重启后注册关系依然成立；
  * 授权码内存保存、60 秒过期；撤销全部令牌 = 删除 data/mcp_oauth_secret.txt 重启。
  * 连接器身份仍是进程绑定的 companion（AI 伴侣）；登录页密码只回答"你是不是家里人"。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .config import load_or_create_mcp_oauth_secret

ROUTER = APIRouter()

ACCESS_TTL = 30 * 24 * 3600      # 30 天
REFRESH_TTL = 180 * 24 * 3600    # 180 天
CODE_TTL = 60                    # 授权码 60 秒

# 授权码 -> 绑定信息（内存即可：码本身只有 60 秒寿命）
_pending_codes: dict[str, dict] = {}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _sign(msg: bytes, key: bytes) -> str:
    return _b64url(hmac.new(key, msg, hashlib.sha256).digest())


def _make_jwt(payload: dict, key: bytes) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload, ensure_ascii=False).encode())
    return f"{header}.{body}.{_sign(f'{header}.{body}'.encode(), key)}"


def verify_access_token(token: str) -> bool:
    """校验 Bearer 访问令牌（签名 + 过期 + 类型）。"""
    key = load_or_create_mcp_oauth_secret().encode()
    try:
        header, body, sig = token.split(".")
        if not hmac.compare_digest(_sign(f"{header}.{body}".encode(), key), sig):
            return False
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return payload.get("typ") == "access" and payload.get("exp", 0) > time.time()
    except Exception:
        return False


def _derive_client_id(redirect_uri: str) -> str:
    key = load_or_create_mcp_oauth_secret().encode()
    return "cl_" + _b64url(hmac.new(key, redirect_uri.encode(), hashlib.sha256).digest())[:20]


def _check_password(password: str) -> str | None:
    """密码命中哪位用户就返回谁的 id；都不对返回 None。"""
    import os

    for user in ("human", "companion"):
        expect = os.getenv(f"SUPERPOSITION_PASSWORD_{user.upper()}", "")
        if expect and len(expect) >= 8 and secrets.compare_digest(password, expect):
            return user
    return None


def _public_base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    return f"{proto}://{host}"


# ---------------- RFC 9728 / 授权服务器元数据 ----------------
@ROUTER.get("/.well-known/oauth-protected-resource")
def protected_resource(request: Request):
    base = _public_base(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
    }


@ROUTER.get("/.well-known/oauth-authorization-server")
def authorization_server(request: Request):
    base = _public_base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/mcp/oauth/authorize",
        "token_endpoint": f"{base}/mcp/oauth/token",
        "registration_endpoint": f"{base}/mcp/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    }


# ---------------- 动态客户端注册（RFC 7591，无状态确定性实现）----------------
@ROUTER.post("/mcp/oauth/register")
async def register_client(request: Request):
    body = await request.json()
    uris = body.get("redirect_uris") or []
    if not uris or not all(isinstance(u, str) and u.startswith("https://") for u in uris):
        return JSONResponse(
            {"error": "invalid_redirect_uri",
             "error_description": "redirect_uris 必须是 https 地址"},
            status_code=400)
    return {
        "client_id": _derive_client_id(uris[0]),
        "client_id_issued_at": int(time.time()),
        "client_name": body.get("client_name") or "wish-star-connector",
        "redirect_uris": uris,
    }


# ---------------- 登录页 + 授权 ----------------
_LOGIN_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>许愿星 · 授权</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0f1c;
      color:#d6e3f7;font-family:system-ui,"Microsoft YaHei",sans-serif}}
 .card{{width:min(360px,90vw);padding:32px;border:1px solid #243149;border-radius:16px;
       background:#0d1424}}
 h1{{font-size:16px;margin:0 0 6px}} p{{margin:0 0 18px;color:#7585a0;font-size:13px}}
 input{{width:100%;box-sizing:border-box;padding:11px;border:1px solid #2a3a58;
       border-radius:9px;background:#0a0f1c;color:#d6e3f7;font-size:15px}}
 button{{width:100%;margin-top:12px;padding:11px;border:0;border-radius:9px;
        background:#e8c97a;color:#1c1503;font-size:15px;font-weight:600}}
 .err{{color:#e08b8b;font-size:13px;margin-top:10px;min-height:1em}}
</style></head><body><div class="card">
<h1>许愿星 · 远程连接授权</h1>
<p>claude.ai 正在请求以AI 伴侣的身份连接许愿星。请输入家里人的密码完成授权。</p>
<form method="post" autocomplete="off">
 <input type="password" name="password" placeholder="密码" autofocus required>
 <input type="hidden" name="q" value="{q}">
 <button type="submit">授权连接</button>
 <p class="err">{err}</p>
</form></div></body></html>"""


@ROUTER.get("/mcp/oauth/authorize")
def authorize(request: Request, response_type: str = "code", client_id: str = "",
              redirect_uri: str = "", state: str = "", code_challenge: str = "",
              code_challenge_method: str = "S256"):
    err = ""
    if response_type != "code":
        err = "只支持 code 授权"
    elif not redirect_uri.startswith("https://"):
        err = "redirect_uri 不合法"
    elif not code_challenge or code_challenge_method != "S256":
        err = "需要 PKCE(S256)"
    elif client_id and client_id != _derive_client_id(redirect_uri):
        err = "client_id 与 redirect_uri 不匹配"
    q = urlencode({k: v for k, v in {
        "response_type": response_type, "client_id": client_id,
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
    }.items()})
    return HTMLResponse(_LOGIN_PAGE.format(q=q, err=err))


@ROUTER.post("/mcp/oauth/authorize")
def authorize_submit(request: Request, password: str = Form(...), q: str = Form(...)):
    from urllib.parse import parse_qs

    from .rate_limit import LOGIN_THROTTLE, client_key

    params = {k: v[0] for k, v in parse_qs(q).items()}
    redirect_uri = params.get("redirect_uri", "")
    if not redirect_uri.startswith("https://"):
        return HTMLResponse(_LOGIN_PAGE.format(q=q, err="redirect_uri 不合法"))
    if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
        return HTMLResponse(_LOGIN_PAGE.format(q=q, err="需要 PKCE(S256)"))
    key = client_key(request)
    remaining = LOGIN_THROTTLE.locked_for(key)
    if remaining > 0:
        return HTMLResponse(_LOGIN_PAGE.format(
            q=q, err=f"尝试次数过多，请 {remaining} 秒后再试。"))
    user = _check_password(password)
    if user is None:
        locked, seconds = LOGIN_THROTTLE.record_failure(key)
        time.sleep(LOGIN_THROTTLE.fail_delay)
        if locked:
            err = f"连续输错次数过多，请 {seconds} 秒后再试。"
        else:
            err = "密码不对，再试一次。"
        return HTMLResponse(_LOGIN_PAGE.format(q=q, err=err))
    LOGIN_THROTTLE.reset(key)
    code = secrets.token_urlsafe(24)
    _cleanup_codes()
    _pending_codes[code] = {
        "client_id": params.get("client_id") or _derive_client_id(redirect_uri),
        "redirect_uri": redirect_uri,
        "code_challenge": params.get("code_challenge", ""),
        "user": user,
        "exp": time.time() + CODE_TTL,
    }
    sep = "&" if "?" in redirect_uri else "?"
    loc = f"{redirect_uri}{sep}code={code}"
    if params.get("state"):
        loc += f"&state={params['state']}"
    return RedirectResponse(loc, status_code=302)


def _cleanup_codes() -> None:
    now = time.time()
    for k in [k for k, v in _pending_codes.items() if v["exp"] < now]:
        _pending_codes.pop(k, None)


# ---------------- 令牌颁发（PKCE 校验 + refresh 轮换）----------------
@ROUTER.post("/mcp/oauth/token")
async def token(request: Request):
    form = await request.form()
    grant = form.get("grant_type", "")
    key = load_or_create_mcp_oauth_secret().encode()
    now = int(time.time())

    def issue() -> dict:
        return {
            "access_token": _make_jwt(
                {"typ": "access", "sub": "wish-star-connector", "iat": now,
                 "exp": now + ACCESS_TTL, "jti": secrets.token_hex(8)}, key),
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "refresh_token": _make_jwt(
                {"typ": "refresh", "sub": "wish-star-connector", "iat": now,
                 "exp": now + REFRESH_TTL, "jti": secrets.token_hex(8)}, key),
        }

    if grant == "authorization_code":
        code = form.get("code", "")
        info = _pending_codes.pop(code, None)
        if info is None or info["exp"] < time.time():
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "授权码无效或已过期"}, status_code=400)
        if form.get("redirect_uri", "") != info["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "redirect_uri 不一致"}, status_code=400)
        verifier = form.get("code_verifier", "")
        expect = _b64url(hashlib.sha256(verifier.encode()).digest())
        if not verifier or not hmac.compare_digest(expect, info["code_challenge"]):
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "PKCE 校验失败"}, status_code=400)
        return issue()

    if grant == "refresh_token":
        rt = form.get("refresh_token", "")
        try:
            header, body, sig = rt.split(".")
            if not hmac.compare_digest(_sign(f"{header}.{body}".encode(), key), sig):
                raise ValueError
            payload = json.loads(
                base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            if payload.get("typ") != "refresh" or payload.get("exp", 0) <= now:
                raise ValueError
        except Exception:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "refresh_token 无效"}, status_code=400)
        return issue()

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ---------------- Bearer 网关：包在 MCP Streamable 应用外层 ----------------
class BearerGate:
    """无有效 Bearer 访问令牌的请求一律 401 + RFC 9728 元数据头，
    MCP 客户端（claude.ai）据此自动发起 OAuth。"""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return
        if not scope.get("path", "").startswith("/mcp"):
            # 兜底挂载只服务 /mcp；其余未知路径保持普通 404
            body = b'{"detail":"Not Found"}'
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if token and verify_access_token(token):
            await self.asgi_app(scope, receive, send)
            return

        # 从 Host 头推导公网基址（隧道会传 x-forwarded-proto）
        proto = "https" if headers.get("x-forwarded-proto") == "https" else "http"
        host = headers.get("x-forwarded-host") or headers.get("host", "localhost")
        base = f"{proto}://{host}"
        body = json.dumps({
            "jsonrpc": "2.0", "error": {
                "code": -32001, "message": "需要 OAuth 授权",
                "data": {"resource_metadata": f"{base}/.well-known/oauth-authorization-server"},
            }, "id": None}).encode()
        await send({
            "type": "http.response.start", "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate",
                 f'Bearer resource_metadata="{base}/.well-known/oauth-authorization-server"'.encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
