"""Step9 回归：认证、hidden metadata、shared_at/排序、编辑字段。"""
import os

from _util import *  # noqa: F401,F403
from fastapi import HTTPException

from app.api.deps import authenticate_token
from app.domain.errors import NotFoundError, ValidationError
from app.repositories import notification_repo, star_repo
from app.services.star_service import StarService


# ---- Bearer token 才能证明 actor；不再接受客户端自报身份 ----
old_q = os.environ.get("SUPERPOSITION_TOKEN_HUMAN")
old_j = os.environ.get("SUPERPOSITION_TOKEN_COMPANION")
try:
    q_token = "q" * 40
    j_token = "j" * 40
    os.environ["SUPERPOSITION_TOKEN_HUMAN"] = q_token
    os.environ["SUPERPOSITION_TOKEN_COMPANION"] = j_token
    assert authenticate_token(q_token) == "human"
    assert authenticate_token(j_token) == "companion"
    for bad in ("", "wrong-token-that-is-not-valid-at-all"):
        try:
            authenticate_token(bad)
            raise AssertionError("缺失/错误 token 必须被拒绝")
        except HTTPException as exc:
            assert exc.status_code == 401
finally:
    if old_q is None:
        os.environ.pop("SUPERPOSITION_TOKEN_HUMAN", None)
    else:
        os.environ["SUPERPOSITION_TOKEN_HUMAN"] = old_q
    if old_j is None:
        os.environ.pop("SUPERPOSITION_TOKEN_COMPANION", None)
    else:
        os.environ["SUPERPOSITION_TOKEN_COMPANION"] = old_j


svc = StarService(fresh_db("step9_security"))

# ---- 新旧 hidden notification 都不能向对方暴露 star_id 或逐颗时间（F02）----
hidden = svc.create_star("companion", "旧时间写下、以后才拆的一颗", "hidden", "安静")["id"]
notes = svc.list_notifications("human")["items"]
hidden_notes = [n for n in notes if n["type"] == "HIDDEN_STAR_ADDED"]
assert len(hidden_notes) == 1 and hidden_notes[0]["star_id"] is None
assert hidden_notes[0]["created_at"] is None, "数量提示不得暴露写入时间"

with svc.db.transaction(immediate=True) as conn:
    notification_repo.insert(
        conn, "ntf_legacy_leak", "human", "HIDDEN_STAR_ADDED",
        "旧版本遗留通知", None, hidden, "2020-01-01T00:00:00",
    )
legacy_view = [n for n in svc.list_notifications("human")["items"]
               if n["type"] == "HIDDEN_STAR_ADDED"]
assert len(legacy_view) == 1, "历史多行通知也必须收敛成一条数量提示"
assert legacy_view[0]["star_id"] is None and legacy_view[0]["created_at"] is None, \
    "历史库里的泄漏字段也必须在 DTO 层被清掉"

# ---- request 批准后、真正 open 前，requester 看不到随机抽中的内部 ID ----
req = svc.request_hidden_star("human")
approved = svc.respond_hidden_request("companion", req["id"], "give")
assert "star_id" not in approved
assert "allocated_star_id" not in approved["request"]
listed = next(r for r in svc.list_requests("human")["outgoing"] if r["id"] == req["id"])
assert "allocated_star_id" not in listed and "opened_star_id" not in listed

# ---- F17：非规范 ISO 日期（如 20260914）直接拒绝，不再“接受但筛出空” ----
for bad_date in ("20260914", "2026-9-14"):
    try:
        svc.list_shared_stars("human", written_date=bad_date)
        raise AssertionError("非规范日期应被拒绝")
    except ValidationError:
        pass

# ---- 用旧 written_at 制造排序回归：后来拆开的旧星必须按 shared_at 排到新位置 ----
with svc.db.transaction(immediate=True) as conn:
    star_repo.update_fields(
        conn, hidden,
        written_at="2020-01-01T00:00:00",
        created_at="2020-01-01T00:00:00",
    )
visible = svc.create_star("human", "较早进入共同瓶子的可见星", "visible")["id"]
with svc.db.transaction(immediate=True) as conn:
    star_repo.update_fields(conn, visible, shared_at="2025-01-01T00:00:00")

opened = svc.open_allocated_star("human", req["id"])
assert opened["id"] == hidden
assert opened["shared_at"] == opened["opened_at"] and opened["shared_at"]
shared = svc.list_shared_stars("human")["items"]
assert shared[0]["id"] == hidden, "共同瓶子必须按真正进入 shared 的时间排序"

# open 以后流程历史才可以用 shared star id 建立跳转关系。
listed_after = next(r for r in svc.list_requests("human")["outgoing"] if r["id"] == req["id"])
assert listed_after["opened_star_id"] == hidden

# ---- incoming offer 在接住前不泄漏 star_id；sender 自己仍可识别自己递出的星 ----
offered_star = svc.create_star("companion", "主动递给你的", "hidden")["id"]
offer = svc.offer_hidden_star("companion", offered_star)
incoming = next(o for o in svc.list_offers("human")["incoming"] if o["id"] == offer["id"])
outgoing = next(o for o in svc.list_offers("companion")["outgoing"] if o["id"] == offer["id"])
assert "star_id" not in incoming
assert outgoing["star_id"] == offered_star

# ---- SEALED hidden 星的 mood_type 也可编辑 ----
editable = svc.create_star("human", "可以编辑的", "hidden", "旧心情")["id"]
edited = svc.edit_star("human", editable, mood_type="新心情")
assert edited["mood_type"] == "新心情"

# ---- 猜中对方 hidden ID 也只能得到“不存在”，不能做存在性探测 ----
try:
    svc.edit_star("companion", editable, content="探测")
    raise AssertionError("非作者不应通过错误类型确认 hidden star 存在")
except NotFoundError:
    pass

print("STEP 9 PASS —— Bearer认证/metadata脱敏/shared_at排序/mood_type 回归全部通过")
