"""Step5 验证：主动递星 -> 接住（含 §50 场景）。
运行：python tests/step5_offer_flow_test.py"""
from _util import *  # noqa: F401,F403
from app.domain.errors import InvalidStateError, NotFoundError, PermissionDeniedError, ValidationError
from app.services.star_service import StarService

svc = StarService(fresh_db("step5"))

h1 = svc.create_star("human", "想现在就给你的那颗", "hidden")["id"]
h2 = svc.create_star("human", "留给以后的", "hidden")["id"]
v = svc.create_star("human", "公开星", "visible")["id"]

# ---- §14：只能递自己的隐藏星 ----
try:
    svc.offer_hidden_star("companion", h1)
    raise AssertionError("递别人的星应被拒绝")
except NotFoundError:
    pass

# 可见星已在我们的瓶子里，不能递
try:
    svc.offer_hidden_star("human", v)
    raise AssertionError("递可见星应被拒绝")
except ValidationError:
    pass

# ---- 正常递出：作者自己挑具体哪一颗（§8.2 关键区别）----
offer = svc.offer_hidden_star("human", h1)
assert offer["status"] == "offered" and offer["recipient_id"] == "companion"

flags = {s["id"]: s for s in svc.list_my_hidden_stars("human")["items"]}
assert flags[h1]["being_offered"] is True, "私人瓶子里能看到“递送中”状态（§7）"

# ---- §15：只有接收者能接住 ----
try:
    svc.accept_offered_star("human", offer["id"])
    raise AssertionError("作者自己接自己的星应被拒绝")
except PermissionDeniedError:
    pass

# ---- 接住：正文展开，星进入我们的瓶子 ----
opened = svc.accept_offered_star("companion", offer["id"])
assert opened["content"] == "想现在就给你的那颗"
assert opened["state"] == "SHARED"
assert opened["shared_origin"] == "revealed_by_offer"
assert opened["open_mode"] == "author_offer"
assert opened["opened_by"] == "companion"

# ---- 不能接两次（§48 并发保护也覆盖这里）----
try:
    svc.accept_offered_star("companion", offer["id"])
    raise AssertionError("重复接住应被拒绝")
except InvalidStateError:
    pass

counts = svc.count_bottles("companion")
assert counts["partner_private_count"] == 1 and counts["shared_count"] == 2

# ---- 已接住的星不能再递；作者仍可编辑（新规则：公共池的星作者随时可改）----
try:
    svc.offer_hidden_star("human", h1)
    raise AssertionError("公开星不能再次递出")
except InvalidStateError:
    pass
edited_shared = svc.edit_star("human", h1, content="接住之后作者还可以改")
assert edited_shared["content"] == "接住之后作者还可以改"
try:
    svc.edit_star("companion", h1, content="对方不能改")
    raise AssertionError("只有作者本人能编辑公共池的星")
except NotFoundError:
    pass

# ---- 递星可以捎一句话（可不填）----
msg_star = svc.create_star("companion", "想捎话的一颗", "hidden")["id"]
with_msg = svc.offer_hidden_star("companion", msg_star, message="吵架了也想让你看这颗")
assert with_msg["message"] == "吵架了也想让你看这颗"
qiao_notes = svc.list_notifications("human")["items"]
assert any(n.get("body") == "吵架了也想让你看这颗" for n in qiao_notes), "捎的话应随通知到达"

# ---- 接住门槛（用户规则）：上一颗打开没留话时不能接新的 ----
# h1 是伴侣接开的且还没留话 → 伴侣不能接住新的递星
second = svc.create_star("human", "等伴侣接的第二颗", "hidden")["id"]
svc.offer_hidden_star("human", second)
incoming = [o for o in svc.list_offers("companion")["incoming"] if o["status"] == "offered"]
try:
    svc.accept_offered_star("companion", incoming[-1]["id"])
    raise AssertionError("还有没留话的星时不应允许接住新的")
except InvalidStateError:
    pass
svc.respond_to_star("companion", h1, "text", "补上回应。")
accepted_second = svc.accept_offered_star("companion", incoming[-1]["id"])
assert accepted_second["state"] == "SHARED"
# 门槛按"打开人"计：对方自己没有未留话的星，可以接住 msg_star
accepted_msg = svc.accept_offered_star("human", with_msg["id"])
assert accepted_msg["state"] == "SHARED" and accepted_msg["content"] == "想捎话的一颗"
# 对方接开 msg_star 后也要先留话，才能进入 §50 的请求场景
svc.respond_to_star("human", accepted_msg["id"], "text", "看到了，捎的话也收到了。")

# ---- §50：请求创建后、批准前，唯一的星被递出 → 批准时候选为空 ----
only = svc.create_star("companion", "唯一的一颗", "hidden")["id"]
rq = svc.request_hidden_star("human")
svc.offer_hidden_star("companion", only)
r = svc.respond_hidden_request("companion", rq["id"], "give")
assert r["request"]["status"] == "rejected"
assert r["note"] == "现在没有可以递出的隐藏星了"
# 那颗星仍在 OFFERED（等接住），没有被偷偷分配走
flags = {s["id"]: s for s in svc.list_my_hidden_stars("companion")["items"]}
assert flags[only]["being_offered"] is True

print("STEP 5 PASS —— 主动递星/接住/来源标记/§50空候选 全部符合文档")
