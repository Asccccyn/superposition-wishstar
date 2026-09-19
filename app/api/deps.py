"""依赖注入：服务实例与 actor 认证。

公网 API 不接受客户端自报 actor。双方身份由独立 Bearer token 证明；
token 只从进程环境读取，不写入源码、数据库、日志或响应。

浏览器会话 cookie 在签名之外还携带服务端登记的会话 ID（F08）：
退出时撤销该会话，被复制走的旧 cookie 立即失效。
cookie 身份的写操作同时校验精确同源（F13），SameSite=Strict 不是 CSRF 防线。
"""
import os
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from ..config import DEFAULT_USERS
from ..repositories import auth_repo
from ..services.star_service import StarService

Actor = str

TOKEN_ENV_BY_ACTOR = {
    user["id"]: f"SUPERPOSITION_TOKEN_{user['id'].upper()}"
    for user in DEFAULT_USERS
}
MIN_TOKEN_LENGTH = 32
SESSION_COOKIE_NAME = "superposition_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30

# F13：cookie 身份的写方法必须来自精确同源；不信任任何“同站不同源”
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_service(request: Request) -> StarService:
    return request.app.state.service


def _configured_tokens() -> dict[str, str]:
    tokens = {actor: os.getenv(env_name, "") for actor, env_name in TOKEN_ENV_BY_ACTOR.items()}
    if any(len(token) < MIN_TOKEN_LENGTH for token in tokens.values()):
        raise HTTPException(status_code=503, detail="服务认证凭证未安全配置")
    if len(set(tokens.values())) != len(tokens):
        raise HTTPException(status_code=503, detail="服务认证凭证配置冲突")
    return tokens


def authenticate_token(presented: str) -> Actor:
    if not presented:
        raise HTTPException(status_code=401, detail="身份凭证无效")
    presented_bytes = presented.encode("utf-8")
    for actor_id, expected in _configured_tokens().items():
        if secrets.compare_digest(presented_bytes, expected.encode("utf-8")):
            return actor_id
    raise HTTPException(
        status_code=401,
        detail="身份凭证无效",
        headers={"WWW-Authenticate": "Bearer"},
    )


PASSWORD_ENV_BY_ACTOR = {
    user["id"]: f"SUPERPOSITION_PASSWORD_{user['id'].upper()}"
    for user in DEFAULT_USERS
}
MIN_PASSWORD_LENGTH = 8


def authenticate_token_or_password(presented: str) -> Actor:
    """登录专用：同一输入框既接受访问钥匙（Bearer token），也接受密码。

    密码经用户级环境变量 SUPERPOSITION_PASSWORD_HUMAN / _COMPANION 配置，
    与远程 MCP 的 OAuth 登录页共用（app.mcp_auth._check_password）。
    两者都未配置密码时退回纯 token 语义，行为与旧版完全一致。
    """
    try:
        return authenticate_token(presented)
    except HTTPException:
        if presented:
            for actor_id, env_name in PASSWORD_ENV_BY_ACTOR.items():
                expected = os.getenv(env_name, "")
                if expected and len(expected) >= MIN_PASSWORD_LENGTH and \
                        secrets.compare_digest(presented.encode(), expected.encode()):
                    return actor_id
        raise


def _session_key() -> bytes:
    tokens = _configured_tokens()
    material = (
        "superposition-session-v1\0"
        + tokens["human"]
        + "\0"
        + tokens["companion"]
    ).encode("utf-8")
    return hashlib.sha256(material).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign(payload: str) -> str:
    return _b64(hmac.new(_session_key(), payload.encode("utf-8"), hashlib.sha256).digest())


def create_session_cookie(actor_id: str, db) -> str:
    """签发会话：签名 cookie + 服务端 auth_sessions 登记行（F08）。"""
    if actor_id not in TOKEN_ENV_BY_ACTOR:
        raise HTTPException(status_code=401, detail="身份凭证无效")
    sid = secrets.token_urlsafe(24)
    expires = int(time.time()) + SESSION_MAX_AGE
    with db.transaction(immediate=True) as conn:
        auth_repo.insert(conn, sid, actor_id, expires)
    payload = f"{actor_id}.{sid}.{expires}"
    return f"{payload}.{_sign(payload)}"


def verify_session_cookie(value: str, db) -> Actor:
    try:
        actor_id, sid, expires_text, signature = value.split(".", 3)
        expires = int(expires_text)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="会话已失效")
    if actor_id not in TOKEN_ENV_BY_ACTOR or expires < int(time.time()):
        raise HTTPException(status_code=401, detail="会话已失效")
    payload = f"{actor_id}.{sid}.{expires}"
    expected = _sign(payload)
    if not secrets.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="会话已失效")
    # F08：会话必须在服务端仍然有效（未撤销、未过期）
    with db.transaction() as conn:
        row = auth_repo.get(conn, sid)
    if row is None or row["revoked_at"] is not None or row["expires_ts"] < int(time.time()) \
            or row["actor_id"] != actor_id:
        raise HTTPException(status_code=401, detail="会话已失效")
    return actor_id


def revoke_session_cookie(value: str, db) -> bool:
    """撤销 cookie 对应的服务端会话；无效 cookie 直接忽略（幂等）。"""
    try:
        actor_id, sid, expires_text, signature = value.split(".", 3)
        expires = int(expires_text)
    except (ValueError, AttributeError):
        return False
    payload = f"{actor_id}.{sid}.{expires}"
    if actor_id not in TOKEN_ENV_BY_ACTOR or \
            not secrets.compare_digest(signature, _sign(payload)):
        return False
    with db.transaction(immediate=True) as conn:
        return auth_repo.revoke(conn, sid) > 0


def _expected_origin(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-proto", "")
    proto = forwarded.split(",")[0].strip().lower() or request.url.scheme
    host = request.headers.get("host", "")
    return f"{proto}://{host}".lower()


def enforce_same_origin(request: Request) -> None:
    """F13：cookie 认证的非幂等请求必须来自精确同源。

    校验优先级：Origin > Referer > Sec-Fetch-Site。三者都缺失时放行
    （非浏览器客户端没有跨站伪造面，且 SameSite=Strict 仍生效）。
    只信任与 Host 完全一致的 origin——不把任意“同站”来源自动当可信。
    """
    if request.method not in _MUTATING_METHODS:
        return
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    sec_fetch_site = request.headers.get("sec-fetch-site", "")
    if not origin and not referer and not sec_fetch_site:
        return
    expected = _expected_origin(request)
    if origin:
        if origin.strip().lower() != expected:
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")
        return
    if referer:
        try:
            parts = urlsplit(referer)
            actual = f"{parts.scheme}://{parts.netloc}".lower()
        except ValueError:
            actual = ""
        if actual != expected:
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")
        return
    if sec_fetch_site and sec_fetch_site.strip() not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def get_actor(request: Request) -> Actor:
    authorization = request.headers.get("authorization", "")
    session_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    if authorization:
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            raise HTTPException(
                status_code=401,
                detail="需要 Bearer 身份凭证",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return authenticate_token(presented)
    if session_cookie:
        actor = verify_session_cookie(session_cookie, request.app.state.db)
        enforce_same_origin(request)
        return actor
    raise HTTPException(
        status_code=401,
        detail="需要登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
