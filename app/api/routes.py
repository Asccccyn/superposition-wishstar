"""REST 路由：只做参数搬运与调用 StarService，不含任何业务规则（§45）。"""
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

from ..rate_limit import LOGIN_THROTTLE, client_key

from ..config import APP_VERSION, BUILD_COMMIT, WEB_LOGIN_ACTOR
from ..services.star_service import StarService
from .deps import (
    Actor,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    authenticate_token_or_password,
    create_session_cookie,
    get_actor,
    get_service,
    revoke_session_cookie,
)

router = APIRouter()


class CreateStarBody(BaseModel):
    content: str
    visibility: str
    mood_type: Optional[str] = None
    mood_text: Optional[str] = None
    note: Optional[str] = None


class EditStarBody(BaseModel):
    content: Optional[str] = None
    mood_type: Optional[str] = None
    mood_text: Optional[str] = None
    note: Optional[str] = None


class RespondRequestBody(BaseModel):
    decision: str  # give | not_now
    star_id: Optional[str] = None  # give 时可指定给哪一颗；缺省由服务端随机


class OfferBody(BaseModel):
    star_id: str
    message: Optional[str] = None  # 递星时捎的一句话（可不填）


class StarResponseBody(BaseModel):
    type: str  # text | audio
    text: Optional[str] = None
    audio_url: Optional[str] = None
    audio_duration: Optional[float] = None


class StartSessionBody(BaseModel):
    # 服务器以 special_dates.type 派生 session 类型；显式传 type 时必须与
    # 派生结果一致。special_date_id 必填的语义校验在服务层完成
    # （与 MCP 同一条规则、同一个中文报错）。
    type: Optional[str] = None  # anniversary | special_day
    special_date_id: Optional[str] = None


class SpecialDateBody(BaseModel):
    name: str
    month: int
    day: int
    year: Optional[int] = None
    type: str = "custom"  # anniversary | custom


class LoginBody(BaseModel):
    token: str


@router.get("/health")
def health(request: Request):
    """F20：health 不再是静态 ok——附带数据库可用性与版本标识。

    test_instance 仅在显式设置 SUPERPOSITION_TEST_INSTANCE 时为真，
    供 Step8 这类测试确认自己连的是隔离实例而不是生产服务。
    """
    db = request.app.state.db
    db_ok = False
    try:
        with db.transaction() as conn:
            conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    payload = {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "version": APP_VERSION,
        "build_commit": BUILD_COMMIT,
        "test_instance": os.getenv("SUPERPOSITION_TEST_INSTANCE", "").lower()
        in {"1", "true", "yes"},
    }
    if not db_ok:
        # 健康检查本身保持 200，通过 body 表达降级，便于监控区分
        payload["status"] = "degraded"
    return payload


# ---------------- 浏览器身份会话 ----------------
@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response,
          svc: StarService = Depends(get_service)):
    # 同一输入框接受访问钥匙或密码（密码经 SUPERPOSITION_PASSWORD_* 配置）。
    # 阶梯锁定：连续 5 次错 → 锁 60s；10 次 → 300s；15 次+ → 900s。
    # 锁定期间连正确密码也拒绝（429），成功登录立即清零。
    key = client_key(request)
    remaining = LOGIN_THROTTLE.locked_for(key)
    if remaining > 0:
        raise HTTPException(status_code=429,
                            detail=f"尝试次数过多，请 {remaining} 秒后再试")
    try:
        actor = authenticate_token_or_password(body.token)
    except HTTPException:
        time.sleep(LOGIN_THROTTLE.fail_delay)
        locked, _seconds = LOGIN_THROTTLE.record_failure(key)
        if locked:
            raise HTTPException(status_code=429,
                                detail="连续输错次数过多，账号已被临时锁定")
        raise
    if actor != WEB_LOGIN_ACTOR:
        # 界面分面：网页是人类的界面；AI 伴侣只能通过 MCP（stdio / OAuth）接入。
        # 凭证本身有效，不计入登录锁定。
        raise HTTPException(
            status_code=403,
            detail="网页登录只属于人类；AI 伴侣请通过 MCP 连接",
        )
    LOGIN_THROTTLE.reset(key)
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    secure = request.url.scheme == "https" or forwarded_proto == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_cookie(actor, request.app.state.db),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return svc.get_identity(actor)


@router.get("/auth/me")
def auth_me(actor: Actor = Depends(get_actor), svc: StarService = Depends(get_service)):
    return svc.get_identity(actor)


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    """F08：除了删浏览器 cookie，还要撤销服务端会话；
    复制走的旧 cookie 从这一刻起不再有效。"""
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    if cookie_value:
        revoke_session_cookie(cookie_value, request.app.state.db)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="strict")
    return {"logged_out": True}


# ---------------- 写星 / 编辑 ----------------
@router.post("/stars")
def create_star(body: CreateStarBody, actor: Actor = Depends(get_actor),
                svc: StarService = Depends(get_service),
                idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    return svc.create_star(actor, body.content, body.visibility,
                           body.mood_type, body.mood_text, body.note,
                           operation_id=idempotency_key)


@router.patch("/stars/{star_id}")
def edit_star(star_id: str, body: EditStarBody, actor: Actor = Depends(get_actor),
              svc: StarService = Depends(get_service)):
    """作者本人编辑自己的星（封存中 / 公共池均可）；产品不提供删除。"""
    return svc.edit_star(actor, star_id, body.content, body.mood_type,
                         body.mood_text, body.note)


# ---------------- 三个瓶子 ----------------
@router.get("/bottles/counts")
def bottle_counts(actor: Actor = Depends(get_actor),
                  svc: StarService = Depends(get_service)):
    return svc.count_bottles(actor)


@router.get("/stars/hidden/mine")
def my_hidden_stars(actor: Actor = Depends(get_actor),
                    svc: StarService = Depends(get_service)):
    return svc.list_my_hidden_stars(actor)


@router.get("/stars/hidden/mine/{star_id}")
def my_hidden_star_detail(star_id: str, actor: Actor = Depends(get_actor),
                          svc: StarService = Depends(get_service)):
    """作者私人星详情：显式打开计 1 次 view、初始化 first_view；
    绝不改变 state，也不对对方可见。列表刷新不计 view。"""
    return svc.get_my_hidden_star(actor, star_id)


@router.get("/stars/shared")
def shared_stars(author_id: Optional[str] = None,
                 written_date: Optional[str] = None,
                 opened_date: Optional[str] = None,
                 shared_origin: Optional[str] = None,
                 session_id: Optional[str] = None,
                 actor: Actor = Depends(get_actor),
                 svc: StarService = Depends(get_service)):
    return svc.list_shared_stars(
        actor,
        author_id=author_id,
        written_date=written_date,
        opened_date=opened_date,
        shared_origin=shared_origin,
        session_id=session_id,
    )


@router.get("/stars/shared/{star_id}")
def shared_star_detail(star_id: str, actor: Actor = Depends(get_actor),
                       svc: StarService = Depends(get_service)):
    return svc.get_shared_star(actor, star_id)


# ---------------- 方式一：请求一颗隐藏星 ----------------
@router.post("/requests")
def create_request(actor: Actor = Depends(get_actor),
                   svc: StarService = Depends(get_service)):
    return svc.request_hidden_star(actor)


@router.get("/requests")
def list_requests(actor: Actor = Depends(get_actor),
                  svc: StarService = Depends(get_service)):
    return svc.list_requests(actor)


@router.post("/requests/{request_id}/respond")
def respond_request(request_id: str, body: RespondRequestBody,
                    actor: Actor = Depends(get_actor),
                    svc: StarService = Depends(get_service)):
    return svc.respond_hidden_request(actor, request_id, body.decision,
                                      star_id=body.star_id)


@router.post("/requests/{request_id}/open")
def open_allocated_star(request_id: str, actor: Actor = Depends(get_actor),
                        svc: StarService = Depends(get_service)):
    return svc.open_allocated_star(actor, request_id)


# ---------------- 方式二：作者主动递星 ----------------
@router.post("/offers")
def create_offer(body: OfferBody, actor: Actor = Depends(get_actor),
                 svc: StarService = Depends(get_service)):
    return svc.offer_hidden_star(actor, body.star_id, message=body.message)


@router.get("/offers")
def list_offers(actor: Actor = Depends(get_actor),
                svc: StarService = Depends(get_service)):
    return svc.list_offers(actor)


@router.post("/offers/{offer_id}/accept")
def accept_offer(offer_id: str, actor: Actor = Depends(get_actor),
                 svc: StarService = Depends(get_service)):
    return svc.accept_offered_star(actor, offer_id)


# ---------------- 回应 ----------------
@router.post("/stars/{star_id}/responses")
def respond_to_star(star_id: str, body: StarResponseBody,
                    actor: Actor = Depends(get_actor),
                    svc: StarService = Depends(get_service),
                    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    return svc.respond_to_star(actor, star_id, body.type, body.text,
                               body.audio_url, body.audio_duration,
                               operation_id=idempotency_key)


# ---------------- 特殊日期 / 纪念日 session ----------------
@router.get("/special-dates")
def list_special_dates(actor: Actor = Depends(get_actor),
                       svc: StarService = Depends(get_service)):
    return svc.list_special_dates(actor)


@router.post("/special-dates")
def add_special_date(body: SpecialDateBody, actor: Actor = Depends(get_actor),
                     svc: StarService = Depends(get_service)):
    return svc.add_special_date(actor, body.name, body.month, body.day,
                                body.year, body.type)


@router.post("/sessions")
def start_session(body: StartSessionBody, actor: Actor = Depends(get_actor),
                  svc: StarService = Depends(get_service)):
    return svc.start_special_session(actor, body.type, body.special_date_id)


@router.get("/sessions")
def list_sessions(actor: Actor = Depends(get_actor),
                  svc: StarService = Depends(get_service)):
    return svc.list_sessions(actor)


@router.get("/sessions/current")
def current_session(actor: Actor = Depends(get_actor),
                    svc: StarService = Depends(get_service)):
    return svc.get_current_session(actor)


@router.post("/sessions/{session_id}/confirm")
def confirm_session(session_id: str, actor: Actor = Depends(get_actor),
                    svc: StarService = Depends(get_service)):
    return svc.confirm_special_session(actor, session_id)


@router.post("/sessions/{session_id}/take-next")
def take_next_session_star(session_id: str, actor: Actor = Depends(get_actor),
                           svc: StarService = Depends(get_service),
                           idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    return svc.take_next_session_star(actor, session_id, operation_id=idempotency_key)


@router.post("/sessions/{session_id}/finish")
def finish_session(session_id: str, actor: Actor = Depends(get_actor),
                   svc: StarService = Depends(get_service)):
    return svc.finish_special_session(actor, session_id)


@router.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: str, actor: Actor = Depends(get_actor),
                   svc: StarService = Depends(get_service)):
    return svc.cancel_special_session(actor, session_id)


@router.post("/sessions/{session_id}/decline")
def decline_session(session_id: str, actor: Actor = Depends(get_actor),
                    svc: StarService = Depends(get_service)):
    return svc.decline_special_session(actor, session_id)


# ---------------- 通知 ----------------
@router.get("/notifications")
def list_notifications(unread_only: bool = False, limit: int = 50,
                       actor: Actor = Depends(get_actor),
                       svc: StarService = Depends(get_service)):
    return svc.list_notifications(actor, unread_only, limit)


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str, actor: Actor = Depends(get_actor),
                      svc: StarService = Depends(get_service)):
    return svc.mark_notification_read(actor, notification_id)
