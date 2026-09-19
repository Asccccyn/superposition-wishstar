"""Step6 验证：回应 / 通知 / 审计日志。
运行：python tests/step6_response_audit_test.py"""
from _util import *  # noqa: F401,F403
from app.domain.errors import NotFoundError, ValidationError
from app.repositories import audit_repo
from app.services.star_service import StarService

svc = StarService(fresh_db("step6"))

v = svc.create_star("human", "公开的一颗星", "visible")["id"]

# ---- 文字回应（§20.1：让写星的人知道这颗星被接住了）----
resp = svc.respond_to_star("companion", v, "text", "我看到啦，原来你那天是这样想的。")
assert resp["type"] == "text"

detail = svc.get_shared_star("companion", v)
assert detail["responses"][0]["text"].startswith("我看到啦")
assert detail["responses"][0]["responder_name"] == "AI 伴侣"

# ---- 查看足迹（用户规则）：每看一次记一笔，按人累计 ----
first_view = {x["viewer_id"]: x["count"] for x in detail["views"]}
assert first_view.get("companion") == 1, "详情每次打开都应记一次查看"
svc.get_shared_star("companion", v)          # 伴侣再看一次
detail2 = svc.get_shared_star("human", v)  # 对方也看
counts2 = {x["viewer_id"]: x["count"] for x in detail2["views"]}
assert counts2.get("companion") == 2 and counts2.get("human") == 1, counts2
names2 = {x["viewer_name"] for x in detail2["views"]}
assert names2 == {"人类", "AI 伴侣"}

# 空文字回应应被拒绝
try:
    svc.respond_to_star("companion", v, "text", "   ")
    raise AssertionError("空回应应被拒绝")
except ValidationError:
    pass

# ---- 隐藏星不能被回应（也绝不因此暴露内容）----
h = svc.create_star("companion", "藏着的一颗", "hidden")["id"]
hidden_notes = [
    n for n in svc.list_notifications("human")["items"]
    if n["type"] == "HIDDEN_STAR_ADDED"
]
assert hidden_notes and all(n["star_id"] is None for n in hidden_notes), \
    "隐藏星数量通知不得暴露 star_id"
# F02：再写一颗也只合并成一条数量提示，且不暴露逐颗时间
svc.create_star("companion", "又一颗隐藏星", "hidden")
merged = [n for n in svc.list_notifications("human")["items"]
          if n["type"] == "HIDDEN_STAR_ADDED"]
assert len(merged) == 1, "隐藏星通知必须是合并后的数量提示，不保留逐颗事件"
assert merged[0]["created_at"] is None, "合并提示不得携带任何写入时间"
try:
    svc.respond_to_star("human", h, "text", "偷看后的回应")
    raise AssertionError("隐藏星不应能被回应")
except NotFoundError:
    pass

# ---- 语音回应：字段已预留（§21），可保存引用 ----
r2 = svc.respond_to_star("human", v, "audio",
                         audio_url="https://example.com/a.webm", audio_duration=23.5)
assert r2["type"] == "audio" and r2["audio_duration"] == 23.5

# ---- 通知（§38）----
notes = svc.list_notifications("companion")
types = [n["type"] for n in notes["items"]]
assert "NEW_VISIBLE_STAR" in types, "可见星写入应通知对方"
assert "STAR_RESPONSE_ADDED" in types, "回应应通知作者"
assert notes["unread_count"] >= 2

first_unread = next(n for n in notes["items"] if not n["is_read"])
svc.mark_notification_read("companion", first_unread["id"])
after = svc.list_notifications("companion")
assert after["unread_count"] == notes["unread_count"] - 1

# 别人的和不存在的一律 404：不通过错误差异泄露通知 ID 的存在性（F15）
try:
    svc.mark_notification_read("human", first_unread["id"])
    raise AssertionError("标记他人通知应被拒绝")
except NotFoundError:
    pass
try:
    svc.mark_notification_read("companion", "ntf_missing_000")
    raise AssertionError("不存在的通知应被拒绝")
except NotFoundError:
    pass

# ---- 审计日志（§53/§54）：仅内部维护层读取，不再作为普通 actor API ----
assert not hasattr(svc, "list_audit_log"), "普通业务 service 不应暴露全局 audit 读取入口"
with svc.db.transaction() as conn:
    logs = audit_repo.list_recent(conn)
event_types = [l["event_type"] for l in logs]
assert "STAR_CREATED_VISIBLE" in event_types
assert "STAR_CREATED_HIDDEN" in event_types
assert "STAR_RESPONSE_ADDED" in event_types
for l in logs:
    blob = (l["detail"] or "") + (l["star_id"] or "")
    assert "我看到啦" not in blob and "藏着的一颗" not in blob and "公开的一颗星" not in blob, \
        "审计日志不得包含正文"

print("STEP 6 PASS —— 回应/通知脱敏/内部审计边界 全部符合文档")
