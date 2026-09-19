"""Step3 验证：写星 / 三瓶计数 / 列表 / 编辑 / 删除。
运行：python tests/step3_core_service_test.py"""
from _util import *  # noqa: F401,F403
from app.domain.errors import InvalidStateError, NotFoundError, ValidationError
from app.services.star_service import StarService

svc = StarService(fresh_db("step3"))

# ---- 写可见星：写入即进入我们的瓶子（§5）----
v = svc.create_star("human", "刚才你笑的时候我特别喜欢。", "visible", mood_type="开心")
assert v["state"] == "SHARED", "可见星写入后应直接 SHARED"
assert v["shared_origin"] == "visible_from_start"
assert v["shared_at"] == v["written_at"], "可见星 shared_at = written_at（§18）"
assert v["author_id"] == "human" and v["recipient_id"] == "companion"

counts = svc.count_bottles("companion")
assert counts["shared_count"] == 1
assert counts["partner_private_count"] == 0

# ---- 写隐藏星：进入作者私人瓶子（§6）----
h = svc.create_star("companion", "今天你说那句话的时候，我偷偷开心了很久。", "hidden", mood_text="很开心")
assert h["state"] == "SEALED"

counts = svc.count_bottles("human")
assert counts["partner_private_count"] == 1, "对方只能看到数量"
assert counts["partner_bottle"] == "AI 伴侣的瓶子"

# ---- 对方无法浏览隐藏星 metadata（§7）：服务层没有这个入口 ----
mine = svc.list_my_hidden_stars("companion")
assert len(mine["items"]) == 1 and mine["items"][0]["content"].startswith("今天")
assert mine["items"][0]["mood_text"] == "很开心"

# ---- 我们的瓶子：必须显示作者（§5.2）----
shared = svc.list_shared_stars("companion")
assert shared["items"][0]["author_name"] == "人类"
assert shared["items"][0]["source_label"] == "一开始就放进我们的瓶子"

# ---- 编辑规则（用户规则：写错就要能改；只有作者本人；公共池同样可编辑）----
edited = svc.edit_star("companion", h["id"], content="改完的这句话。")
assert edited["content"] == "改完的这句话。"

try:
    svc.edit_star("human", h["id"], content="偷改")
    raise AssertionError("非作者编辑应被拒绝")
except NotFoundError:
    pass

# 作者可以编辑自己已进公共池的星；对方不能
try:
    svc.edit_star("companion", v["id"], content="偷改对方的公开星")
    raise AssertionError("对方编辑公共星应被拒绝")
except NotFoundError:
    pass
edited_visible = svc.edit_star("human", v["id"], content="公开星也能改",
                               note="改的时候补的注释")
assert edited_visible["content"] == "公开星也能改"
assert edited_visible["note"] == "改的时候补的注释"

# ---- 产品规则：星星不提供删除（写错可以改，抹掉不行）----
assert not hasattr(svc, "delete_hidden_star"), "产品规则：不开放删除功能"

# ---- 注释（为什么写）：可选、独立于心情、可清空 ----
with_note = svc.create_star("human", "带注释的一颗", "hidden",
                            mood_type="想念", note="因为今天路过我们常去的那家店")
assert with_note["note"].startswith("因为今天")
cleared = svc.edit_star("human", with_note["id"], note="")
assert cleared["note"] is None, "空字符串应清空注释"

# ---- 心情字段语义（F16）：空字符串清空；不传不改 ----
h3 = svc.create_star("human", "带心情的一颗", "hidden",
                     mood_type="开心", mood_text="想你")
cleared_mood = svc.edit_star("human", h3["id"], mood_type="", mood_text="")
assert cleared_mood["mood_type"] is None and cleared_mood["mood_text"] is None
kept = svc.edit_star("human", h3["id"], content="只改正文")
assert kept["mood_type"] is None, "不传的字段保持不变"
no_mood = svc.create_star("human", "没写心情的一颗", "hidden",
                          mood_type="", mood_text="")
assert no_mood["mood_type"] is None and no_mood["mood_text"] is None

# 正在流程中（被递出）的星短暂冻结，等流程落定后再改
offered_star = svc.create_star("companion", "递出去就不能中途改", "hidden")["id"]
svc.offer_hidden_star("companion", offered_star)
try:
    svc.edit_star("companion", offered_star, content="中途偷改")
    raise AssertionError("递送中的星应冻结")
except InvalidStateError:
    pass

# ---- 写入校验 ----
for bad in [("  ", "visible"), ("", "hidden")]:
    try:
        svc.create_star("human", bad[0], bad[1])
        raise AssertionError("空内容应被拒绝")
    except ValidationError:
        pass
try:
    svc.create_star("human", "x" * 2001, "visible")
    raise AssertionError("超长内容应被拒绝")
except ValidationError:
    pass
try:
    svc.create_star("human", "合法内容", "半可见")
    raise AssertionError("非法 visibility 应被拒绝")
except ValidationError:
    pass

print("STEP 3 PASS —— 写星 / 三瓶 / 注释 / 编辑(含公共池) / 不提供删除 全部符合用户规则")
