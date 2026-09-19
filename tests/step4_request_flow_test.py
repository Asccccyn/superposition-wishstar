"""Step4 验证：请求 -> 审批 -> 服务端随机 -> 打开。
运行：python tests/step4_request_flow_test.py"""
from _util import *  # noqa: F401,F403
from app.domain.errors import (
    BottleEmptyError,
    InvalidStateError,
    PermissionDeniedError,
    ValidationError,
)
from app.services.star_service import StarService

svc = StarService(fresh_db("step4"))

# ---- §49：对方瓶子为空时，诚实报错，不制造虚假星星 ----
try:
    svc.request_hidden_star("companion")   # 对方的私人瓶子是空的
    raise AssertionError("空瓶请求应被拒绝")
except BottleEmptyError as e:
    assert "空的" in e.message

# ---- 伴侣写 3 颗隐藏星 ----
for i in range(3):
    svc.create_star("companion", f"伴侣的隐藏星{i}", "hidden")

# ---- 发起请求：只创建请求，不返回正文，不暴露内部 star_id（§10 / §31）----
req = svc.request_hidden_star("human")
assert req["status"] == "pending"
assert "allocated_star_id" not in req and "opened_star_id" not in req
assert "content" not in req

# ---- 非主人不能审批（§11）----
try:
    svc.respond_hidden_request("human", req["id"], "give")
    raise AssertionError("请求人自己审批应被拒绝")
except PermissionDeniedError:
    pass

try:
    svc.respond_hidden_request("companion", req["id"], "全都要")
    raise AssertionError("非法 decision 应被拒绝")
except ValidationError:
    pass

# ---- not_now：拒绝，星保持 SEALED，可再次请求 ----
r = svc.respond_hidden_request("companion", req["id"], "not_now")
assert r["request"]["status"] == "rejected"
assert "star_id" not in r and "allocated_star_id" not in r["request"]
mine = svc.list_my_hidden_stars("companion")
assert all(s["state"] == "SEALED" for s in mine["items"])

# ---- give：服务端随机分配（§47）----
req2 = svc.request_hidden_star("human")
r2 = svc.respond_hidden_request("companion", req2["id"], "give")
assert r2["request"]["status"] == "approved"
assert "star_id" not in r2 and "allocated_star_id" not in r2["request"]
listed_before_open = svc.list_requests("human")["outgoing"]
pending_view = next(r for r in listed_before_open if r["id"] == req2["id"])
assert "allocated_star_id" not in pending_view and "opened_star_id" not in pending_view

# ---- 打开：只有请求人能打开；打开即进入我们的瓶子 ----
opened = svc.open_allocated_star("human", req2["id"])
assert opened["author_id"] == "companion", "随机抽中的星一定来自对方的瓶子"
assert opened["state"] == "SHARED"
assert opened["shared_origin"] == "revealed_by_request"
assert opened["open_mode"] == "request_random"
assert opened["opened_by"] == "human"
assert opened["shared_at"] == opened["opened_at"]
listed_after_open = svc.list_requests("human")["outgoing"]
opened_view = next(r for r in listed_after_open if r["id"] == req2["id"])
assert opened_view["opened_star_id"] == opened["id"]

# ---- §13：一次一颗，拿了不能换、不能重抽、不能重复打开 ----
try:
    svc.open_allocated_star("human", req2["id"])
    raise AssertionError("重复打开应被拒绝")
except InvalidStateError:
    pass

counts = svc.count_bottles("human")
assert counts["partner_private_count"] == 2 and counts["shared_count"] == 1

# ---- 请求得到的星必须回应；未回应前不能继续申请下一颗 ----
try:
    svc.request_hidden_star("human")
    raise AssertionError("未回应上一颗请求星时，不应允许继续申请")
except InvalidStateError:
    pass
svc.respond_to_star("human", opened["id"], "text", "我看到了，也记住了。")

# ---- 别人（主人）不能冒充请求人打开：再造一次请求并批准 ----
req3 = svc.request_hidden_star("human")
r3 = svc.respond_hidden_request("companion", req3["id"], "give")
try:
    svc.open_allocated_star("companion", req3["id"])
    raise AssertionError("非请求人打开应被拒绝")
except PermissionDeniedError:
    pass
svc.open_allocated_star("human", req3["id"])

# ---- §50 场景（批准时候选为空）在 Step5 中结合“主动递星”验证 ----

# ---- F03：一次只能有一个未走完的请求周期 ----
def _respond_opened(request_id):
    star_id = next(r for r in svc.list_requests("human")["outgoing"]
                   if r["id"] == request_id)["opened_star_id"]
    svc.respond_to_star("human", star_id, "text", "看到了，记住了。")

_respond_opened(req3["id"])

# 有 pending 请求时不能再发起第二条
req_pending = svc.request_hidden_star("human")
try:
    svc.request_hidden_star("human")
    raise AssertionError("已有 pending 请求时不应允许再发起")
except InvalidStateError:
    pass
svc.respond_hidden_request("companion", req_pending["id"], "not_now")

# 有 approved 且未打开的请求时同样不能再发起
req4 = svc.request_hidden_star("human")
svc.respond_hidden_request("companion", req4["id"], "give")
try:
    svc.request_hidden_star("human")
    raise AssertionError("已有 approved 未打开的请求时不应允许再发起")
except InvalidStateError:
    pass
opened4 = svc.open_allocated_star("human", req4["id"])

# 打开后未回应时，占位音频回应不能解除门槛
try:
    svc.respond_to_star("human", opened4["id"], "audio", audio_url="   ")
    raise AssertionError("空白音频 URL 不应被当作有效回应保存")
except ValidationError:
    pass
_respond_opened(req4["id"])

# 历史积压：服务层修复后正常流程已造不出“一条已打开未回应 + 一条已批准”，
# 直接写库模拟修复前积累的旧数据，open 必须仍然拒绝越序打开。
from app.common import new_id, now_iso  # noqa: E402
from app.repositories import request_repo, star_repo  # noqa: E402

star_a = svc.create_star("companion", "历史积压A", "hidden")
star_b = svc.create_star("companion", "历史积压B", "hidden")
now = now_iso()
with svc.db.transaction(immediate=True) as conn:
    # A：已批准、已打开（未回应）——先按合法流程走一半再补库更直观，这里直接造行
    request_repo.insert(conn, {
        "id": new_id("req"), "requester_id": "human", "owner_id": "companion",
        "status": "approved", "allocated_star_id": star_a["id"],
        "requested_at": now, "responded_at": now, "allocated_at": now, "opened_at": now,
    })
    star_repo.update_state(
        conn, star_a["id"], "SHARED", from_state="SEALED",
        opened_at=now, opened_by="human", shared_at=now,
        open_mode="request_random", shared_origin="revealed_by_request",
    )
    # B：已批准、未打开
    backlog_req_id = new_id("req")
    request_repo.insert(conn, {
        "id": backlog_req_id, "requester_id": "human", "owner_id": "companion",
        "status": "approved", "allocated_star_id": star_b["id"],
        "requested_at": now, "responded_at": now, "allocated_at": now, "opened_at": None,
    })
    star_repo.update_state(conn, star_b["id"], "ALLOCATED_RANDOMLY", from_state="SEALED")

try:
    svc.open_allocated_star("human", backlog_req_id)
    raise AssertionError("上一颗还没回应时不应允许打开积压的下一条")
except InvalidStateError:
    pass
# 回应了上一颗之后，积压请求才能打开
svc.respond_to_star("human", star_a["id"], "text", "补上的回应。")
opened_backlog = svc.open_allocated_star("human", backlog_req_id)
assert opened_backlog["state"] == "SHARED"
svc.respond_to_star("human", opened_backlog["id"], "text", "积压的也回应了。")

# ---- 用户规则（第二轮修订明确保留）：给的时候可以指定具体哪一颗 ----
# 主人指定自己的某颗 SEALED 隐藏星；不指定就随机。两种能力都必须可用。
spec_a = svc.create_star("companion", "指定要给的那颗", "hidden")
spec_b = svc.create_star("companion", "先递出去的那颗", "hidden")
req5 = svc.request_hidden_star("human")
r5 = svc.respond_hidden_request("companion", req5["id"], "give", star_id=spec_a["id"])
assert r5["request"]["status"] == "approved"
# 打开前响应仍不泄漏内部 star_id
assert "allocated_star_id" not in r5["request"] and "star_id" not in r5
opened5 = svc.open_allocated_star("human", req5["id"])
assert opened5["id"] == spec_a["id"] and opened5["content"] == "指定要给的那颗", \
    "指定 give 必须给到主人选中的那一颗"
svc.respond_to_star("human", spec_a["id"], "text", "指定的这颗收到了。")

# 非法指定一律拒绝：别人的星 / 可见星 / 已递出（非 SEALED）
q_owned = svc.create_star("human", "对方自己的星", "hidden")
vis_star = svc.create_star("companion", "可见星不能指定", "visible")
svc.offer_hidden_star("companion", spec_b["id"])   # -> OFFERED
fin_star = svc.create_star("companion", "随机兜底", "hidden")
req6 = svc.request_hidden_star("human")
for bad_id, why in ((q_owned["id"], "别人的星"), (vis_star["id"], "可见星"),
                    (spec_b["id"], "非 SEALED 状态")):
    try:
        svc.respond_hidden_request("companion", req6["id"], "give", star_id=bad_id)
        raise AssertionError(f"非法指定（{why}）应被拒绝")
    except ValidationError:
        pass
# 拒绝不消耗请求：随机 give 仍可完成本轮（候选只剩兜底这颗）
r6 = svc.respond_hidden_request("companion", req6["id"], "give")
assert r6["request"]["status"] == "approved"
opened6 = svc.open_allocated_star("human", req6["id"])
assert opened6["id"] == fin_star["id"], "随机模式仍由服务端挑选，候选只剩兜底这颗"
svc.respond_to_star("human", fin_star["id"], "text", "随机的也收到了。")

print("STEP 4 PASS —— 请求/审批/随机+指定双能力/打开/不可重抽/空瓶/一次一颗周期 全部符合文档")
