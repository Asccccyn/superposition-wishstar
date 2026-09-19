"""Step7 验证：纪念日 0 点快照互看 + 自定义特殊日（第二轮修订语义）。

覆盖审计矩阵 T3/T4/T5/T6/T7/T8/T17/T18：
  * 配额 = 纪念日当天 00:00（业务时区）冻结快照，不按确认时数量；
    额度完全按各自 cutoff 时刻的真实瓶中数量动态计算（10/15 仅为验收示例，
    另用 0/8 与 3/27 两种形态回归）；冻结的是数量额度，不锁定具体星。
  * 0 点后新写的星可以存在，但不占额度、不进本轮候选；
  * "我的额度耗尽"与"对方本轮候选为空"返回不同文案（文案逐字对齐）；
  * 确认只把 waiting -> active，晚上确认不改 0 点算好的额度；
  * 重启（同一 DB 再次 init）不改 session、不重算 quota/cutoff；
  * anniversary session 必须绑定具体纪念日且类型由日期数据派生；
  * 用户新增 anniversary 日期参与相同规则；custom 日期走 special_day 且不被误标。

运行：python tests/step7_session_flow_test.py"""
from datetime import datetime
from zoneinfo import ZoneInfo

from _util import *  # noqa: F401,F403
from app.common import set_business_clock
from app.db.connection import Database
from app.db.schema import DDL, MIGRATIONS
from app.db.seed import seed_if_empty
from app.domain.errors import (
    BottleEmptyError,
    DuplicateError,
    InvalidStateError,
    PermissionDeniedError,
    ValidationError,
)
from app.services.star_service import (
    ANNIVERSARY_PARTNER_EMPTY,
    ANNIVERSARY_QUOTA_EXHAUSTED,
    SPECIAL_DAY_EXHAUSTED,
    StarService,
)

SH = ZoneInfo("Asia/Shanghai")
svc = StarService(fresh_db("step7"))
# 开源版种子不含纪念日：测试自建三个示例日期（与任何真实日期无关）
for _n, _m, _d in (("3.14", 3, 14), ("6.18", 6, 18), ("11.11", 11, 11)):
    svc.add_special_date("human", _n, _m, _d, None, "anniversary")

try:
    # ---- 4.26（cutoff 前一天）写星：对方 10 颗，伴侣 15 颗（审计标准例 10/15）----
    set_business_clock(datetime(2026, 3, 13, 20, 0, 0, tzinfo=SH))
    for i in range(10):
        svc.create_star("human", f"对方的隐藏星{i}", "hidden")
    for i in range(15):
        svc.create_star("companion", f"伴侣的隐藏星{i}", "hidden")

    dates = svc.list_special_dates("human")["items"]
    assert {(d["month"], d["day"]) for d in dates} == {(3, 14), (6, 18), (11, 11)}
    d314 = next(d for d in dates if (d["month"], d["day"]) == (3, 14))
    assert d314["type"] == "anniversary", "3.14 应为 anniversary（测试自建）"

    # ---- 必须绑定具体日期（T17 反例）----
    try:
        svc.start_special_session("human", "anniversary", None)
        raise AssertionError("不绑定具体日期应被拒绝")
    except ValidationError:
        pass

    # ---- 非纪念日当天不能发起 anniversary ----
    set_business_clock(datetime(2026, 3, 15, 9, 0, 0, tzinfo=SH))
    try:
        svc.start_special_session("human", "anniversary", d314["id"])
        raise AssertionError("非纪念日当天发起应被拒绝")
    except ValidationError as e:
        assert "只能在纪念日当天" in e.message

    # ---- 纪念日当天：0 点后新写的星可存在，但不进本轮（T4 前置）----
    set_business_clock(datetime(2026, 3, 14, 9, 0, 0, tzinfo=SH))
    late_q = svc.create_star("human", "对方纪念日当天才写的", "hidden")
    late_j = [svc.create_star("companion", f"伴侣当天才写的{k}", "hidden") for k in range(2)]

    # ---- 类型错配被拒：不能"选了纪念日却按 special_day 发起"（T17）----
    try:
        svc.start_special_session("human", "special_day", d314["id"])
        raise AssertionError("类型错配应被拒绝")
    except ValidationError:
        pass

    # ---- 发起 anniversary：0 点快照额度随 session 持久化（T3）----
    sess = svc.start_special_session("human", "anniversary", d314["id"])
    assert sess["status"] == "waiting_confirmation" and sess["confirmed_by"] is None
    assert sess["type"] == "anniversary" and sess["special_date_id"] == d314["id"]
    assert sess["quota_cutoff_at"] == "2026-03-14T00:00:00", sess["quota_cutoff_at"]
    assert sess["actor_a_count"] == 10 and sess["actor_b_count"] == 15, \
        "额度 = 0 点快照（对方 10 / 伴侣 15），当天新星不计入"

    # 未确认不能看
    try:
        svc.take_next_session_star("human", sess["id"])
        raise AssertionError("未确认就看星应被拒绝")
    except InvalidStateError:
        pass

    # 发起者自己不能确认
    try:
        svc.confirm_special_session("human", sess["id"])
        raise AssertionError("发起者自己确认应被拒绝")
    except PermissionDeniedError:
        pass

    # 同一时刻不能有两场
    try:
        svc.start_special_session("companion", "anniversary", d314["id"])
        raise AssertionError("重复发起应被拒绝")
    except DuplicateError:
        pass

    # ---- 晚上才确认：额度仍是 0 点快照，不按确认时数量（T7）----
    set_business_clock(datetime(2026, 3, 14, 22, 30, 0, tzinfo=SH))
    active = svc.confirm_special_session("companion", sess["id"])
    assert active["status"] == "active"
    assert active["actor_a_count"] == 10 and active["actor_b_count"] == 15, \
        "确认不得重新按当前数量覆盖 0 点额度"
    assert active["quota_cutoff_at"] == "2026-03-14T00:00:00"

    # 双方瓶子里的星都保持 SEALED（不预锁定）
    mine_q = svc.list_my_hidden_stars("human")["items"]
    assert all(s["state"] == "SEALED" for s in mine_q) and len(mine_q) == 11

    # ---- 互看：对方额度 10，看伴侣 cutoff 前的星；看了必须留话 ----
    overview = svc.get_current_session("human")
    assert overview["my_quota"] == 10 and overview["my_viewed"] == 0
    assert overview["partner_remaining"] == 15, "本轮候选不含当天新星"

    seen_by_q = []
    for _ in range(10):
        star = svc.take_next_session_star("human", sess["id"])
        seen_by_q.append(star)
        assert star["author_id"] == "companion", "对方只能看伴侣写的星"
        assert star["opened_by"] == "human"
        assert star["state"] == "SHARED" and star["shared_at"] == star["opened_at"]
        assert star["cycle_id"] == sess["id"], "应记录所属批次（§37）"
        assert not star["content"].startswith("伴侣当天才写的"), "0 点后新星不得进入本轮候选"
        assert star["first_view_at"] == star["opened_at"], "揭晓即首次查看"
        try:
            svc.take_next_session_star("human", sess["id"])
            raise AssertionError("没留话不应允许连续看两颗")
        except InvalidStateError:
            pass
        svc.respond_to_star("human", star["id"], "text", "看到了。")

    # T5：对方额度 10 用完 -> 精确文案（逐字对齐用户指定文案）
    try:
        svc.take_next_session_star("human", sess["id"])
        raise AssertionError("额度耗尽应提示不可交换")
    except BottleEmptyError as e:
        assert e.message == ANNIVERSARY_QUOTA_EXHAUSTED, e.message
    assert ANNIVERSARY_QUOTA_EXHAUSTED == "不可以哦，你的瓶子里面没有星星可以交换了，下次多写点吧。"

    # ---- 伴侣额度 15，但对方本轮候选只有 0 点前的 10 颗 ----
    ov_j = svc.get_current_session("companion")
    assert ov_j["my_quota"] == 15 and ov_j["partner_remaining"] == 10
    for _ in range(10):
        star = svc.take_next_session_star("companion", sess["id"])
        assert star["author_id"] == "human"
        assert star["content"] != late_q["content"], "0 点后新星不得进入本轮候选"
        svc.respond_to_star("companion", star["id"], "text", "我也看到了。")
    # T6：伴侣额度还有（15>10），但对方本轮候选已空 -> 另一句文案
    try:
        svc.take_next_session_star("companion", sess["id"])
        raise AssertionError("对方候选耗尽应有专门提示")
    except BottleEmptyError as e:
        assert e.message == ANNIVERSARY_PARTNER_EMPTY, e.message
    assert ANNIVERSARY_PARTNER_EMPTY == "对方瓶子里没有本轮可以交换的星星了。"

    # T4 收尾：0 点后新写的星全部还在各自瓶里，从未被本轮碰过
    flags = {s["id"]: s for s in svc.list_my_hidden_stars("human")["items"]}
    assert flags[late_q["id"]]["state"] == "SEALED"
    flags_j = {s["id"]: s for s in svc.list_my_hidden_stars("companion")["items"]}
    for s in late_j:
        assert flags_j[s["id"]]["state"] == "SEALED"

    # ---- T8：重启（同一 DB 第二次 init，API/MCP 启动路径）不重算、不改状态 ----
    db2 = Database(svc.db.path, migrations=MIGRATIONS)
    db2.init(DDL, seed_if_empty)
    svc2 = StarService(db2)
    ov2 = svc2.get_current_session("human")
    assert ov2["session"]["id"] == sess["id"]
    assert ov2["session"]["status"] == "active", "重启不得结束 active session"
    assert ov2["session"]["quota_cutoff_at"] == "2026-03-14T00:00:00"
    assert ov2["session"]["actor_a_count"] == 10 \
        and ov2["session"]["actor_b_count"] == 15, "重启不得重算 quota"
    assert ov2["my_quota"] == 10

    # ---- 结束本轮：没看的自然留在瓶子里 ----
    fin = svc2.finish_special_session("companion", sess["id"])
    assert fin["status"] == "completed" and fin["ended_at"]
    try:
        svc2.take_next_session_star("human", sess["id"])
        raise AssertionError("结束后不能继续看")
    except InvalidStateError:
        pass
    assert svc2.get_current_session("human")["session"] is None

    # ---- T17 后半：用户新增 anniversary 日期参与相同规则 ----
    set_business_clock(datetime(2026, 3, 13, 21, 0, 0, tzinfo=SH))
    svc.create_star("human", "为新增纪念日写的", "hidden")
    svc.create_star("companion", "为新增纪念日写的", "hidden")
    new_ann = svc.add_special_date("human", "我们的纪念日", 3, 14, None, "anniversary")
    assert new_ann["type"] == "anniversary"
    set_business_clock(datetime(2026, 3, 14, 10, 0, 0, tzinfo=SH))
    s_new = svc.start_special_session("companion", "anniversary", new_ann["id"])
    assert s_new["type"] == "anniversary" and s_new["special_date_id"] == new_ann["id"]
    # 同一天的第二个纪念日：0 点快照按历史状态恢复（当时在瓶里的都算额度，
    # 即使这一天稍后已被上一轮看走 shared_at >= cutoff）；当前候选仍会排除
    # 非 SEALED 的星——额度与候选是两个口径（审计 §4.3）。
    # （对方：10+1；伴侣：15+1——都含 4.26 晚补写的那颗；当天 09:00 写的不算）
    assert s_new["actor_a_count"] == 16 and s_new["actor_b_count"] == 11, \
        f"新增纪念日同样按 0 点快照 {s_new['actor_a_count']}/{s_new['actor_b_count']}"
    svc.confirm_special_session("human", s_new["id"])
    st = svc.take_next_session_star("companion", s_new["id"])
    assert st["content"] == "为新增纪念日写的", "本轮只能拿到 0 点在瓶且当前仍 SEALED 的星"
    svc.respond_to_star("companion", st["id"], "text", "新纪念日也看到了。")
    try:
        svc.take_next_session_star("companion", s_new["id"])
        raise AssertionError("当前候选耗尽应提示对方瓶空")
    except BottleEmptyError as e:
        assert e.message == ANNIVERSARY_PARTNER_EMPTY
    svc.finish_special_session("companion", s_new["id"])

    # ---- T18：custom 日期走 special_day，不被误标 anniversary ----
    custom = svc.add_special_date("human", "第一次看海", 3, 14, None, "custom")
    s_cust = svc.start_special_session("human", "special_day", custom["id"])
    assert s_cust["type"] == "special_day" and s_cust["special_date_id"] == custom["id"]
    assert s_cust["quota_cutoff_at"] is None, "特殊日不按 0 点冻结"
    act = svc.confirm_special_session("companion", s_cust["id"])
    # special_day 保留既有口径：确认那一刻瓶里的当前数量
    # （对方：late_q 1 颗；伴侣：15-10 被看 +2 当天新写 +1 新增纪念日 = 8 颗）
    assert act["actor_a_count"] == 1 and act["actor_b_count"] == 8, \
        f"特殊日配额=确认时数量 {act['actor_a_count']}/{act['actor_b_count']}"
    cs = svc.take_next_session_star("companion", s_cust["id"])
    assert cs["shared_origin"] == "revealed_by_special_day"
    svc.respond_to_star("companion", cs["id"], "text", "特殊日的星。")
    try:
        svc.take_next_session_star("companion", s_cust["id"])
        raise AssertionError("特殊日配额用完应提示看完")
    except BottleEmptyError as e:
        assert e.message == SPECIAL_DAY_EXHAUSTED
    svc.finish_special_session("companion", s_cust["id"])

    # ---- F05：等待确认的邀请——发起方可取消，被邀请方可拒绝 ----
    s3 = svc.start_special_session("human", "anniversary", d314["id"])
    cancelled = svc.cancel_special_session("human", s3["id"])
    assert cancelled["status"] == "cancelled"
    assert svc.get_current_session("human")["session"] is None

    s4 = svc.start_special_session("human", "anniversary", d314["id"])
    try:
        svc.decline_special_session("human", s4["id"])
        raise AssertionError("发起方应使用取消而不是拒绝")
    except PermissionDeniedError:
        pass
    declined = svc.decline_special_session("companion", s4["id"])
    assert declined["status"] == "cancelled"

    # 拒绝之后可以立刻重新发起
    s5 = svc.start_special_session("companion", "special_day", custom["id"])
    svc.confirm_special_session("human", s5["id"])
    svc.finish_special_session("companion", s5["id"])

    # ---- 特殊日期：非法日期仍被拒绝 ----
    try:
        svc.add_special_date("human", "不存在的二月日期", 2, 31, None, "custom")
        raise AssertionError("非法日期应被拒绝")
    except ValidationError:
        pass

    # shared bottle 能按作者 / 来源 / session 筛选
    session_by_companion = svc.list_shared_stars(
        "human", author_id="companion", shared_origin="revealed_by_anniversary",
        session_id=sess["id"],
    )["items"]
    assert len(session_by_companion) == 10 \
        and all(s["author_id"] == "companion" for s in session_by_companion)
    opened_date = seen_by_q[0]["opened_at"][:10]
    assert len(svc.list_shared_stars("human", opened_date=opened_date)["items"]) >= 20

    # F11：历史批次列表可用于按批次回看
    history = svc.list_sessions("human")["items"]
    assert {s["id"] for s in history} >= {sess["id"], s5["id"]}
    assert all("special_date_name" in s for s in history)

    # ---- 10/15 仅为验收示例：额度必须按 cutoff 真实历史状态动态计算 ----
    # 用两套完全不同的数字回归：0/8 与 3/27；并证明"冻结的是数量额度，
    # 不是锁死具体哪几颗星"——session 进行中被递出的星实时退出候选。
    svc_d = StarService(fresh_db("step7_dynamic"))
    svc_d.add_special_date("human", "6.18", 6, 18, None, "anniversary")
    svc_d.add_special_date("human", "3.14", 3, 14, None, "anniversary")

    # 场景一（0/8，6.18）：双方额度彼此独立；0 额度不能拆；
    # 对方理论额度尚在但本轮无候选时，实际也拿不到。
    set_business_clock(datetime(2026, 6, 17, 20, 0, 0, tzinfo=SH))
    for i in range(8):
        svc_d.create_star("companion", f"伴侣6.18前的星{i}", "hidden")  # 对方 0 颗
    set_business_clock(datetime(2026, 6, 18, 9, 0, 0, tzinfo=SH))
    d618 = next(d for d in svc_d.list_special_dates("human")["items"]
               if (d["month"], d["day"]) == (6, 18))
    s79 = svc_d.start_special_session("human", "anniversary", d618["id"])
    assert s79["actor_a_count"] == 0 and s79["actor_b_count"] == 8, \
        "0/8：额度完全动态，不得出现任何示例数字"
    svc_d.confirm_special_session("companion", s79["id"])
    try:
        svc_d.take_next_session_star("human", s79["id"])
        raise AssertionError("额度 0 不能拆")
    except BottleEmptyError as e:
        assert e.message == ANNIVERSARY_QUOTA_EXHAUSTED
    try:
        svc_d.take_next_session_star("companion", s79["id"])
        raise AssertionError("对方本轮无候选应提示拿不到")
    except BottleEmptyError as e:
        assert e.message == ANNIVERSARY_PARTNER_EMPTY
    svc_d.finish_special_session("human", s79["id"])

    # 场景二（3/27，3.14）：数字与 10/15 示例不同同样正确；
    # 发起后没有一颗星被锁定（仍 SEALED、可正常递出）；
    # 被递出的那颗在 take 时实时退出候选——候选不预锁定。
    set_business_clock(datetime(2026, 3, 13, 20, 0, 0, tzinfo=SH))
    for i in range(3):
        svc_d.create_star("human", f"对方3.14前的星{i}", "hidden")
    for i in range(27):
        svc_d.create_star("companion", f"伴侣3.14前的星{i}", "hidden")
    set_business_clock(datetime(2026, 3, 14, 10, 0, 0, tzinfo=SH))
    d314d = next(d for d in svc_d.list_special_dates("human")["items"]
                 if (d["month"], d["day"]) == (3, 14))
    s42 = svc_d.start_special_session("human", "anniversary", d314d["id"])
    assert s42["actor_a_count"] == 3 and s42["actor_b_count"] == 27, \
        "3/27：额度按各自 cutoff 时刻数量分别动态冻结"
    svc_d.confirm_special_session("companion", s42["id"])
    mine = svc_d.list_my_hidden_stars("human")["items"]
    assert all(s["state"] == "SEALED" for s in mine) and len(mine) == 3, \
        "冻结的是数量，不是具体星：发起后无一颗被锁定"
    offered = next(s for s in mine if s["content"] == "对方3.14前的星0")
    svc_d.offer_hidden_star("human", offered["id"])   # session 进行中递出一颗
    star = svc_d.take_next_session_star("companion", s42["id"])
    assert star["id"] != offered["id"], "被递出的星实时退出本轮候选"
    assert star["content"] in ("对方3.14前的星1", "对方3.14前的星2")
    svc_d.respond_to_star("companion", star["id"], "text", "动态额度的星。")
    svc_d.finish_special_session("companion", s42["id"])

    print("STEP 7 PASS —— 0点快照(动态额度,10/15仅为示例)/当天新星排除/两种耗尽文案/"
          "确认不改额度/重启不变/纪念日类型绑定/自定义特殊日/冻结数量而非锁星 全部符合语义")
finally:
    set_business_clock(None)
