"""Step13 验证：查看足迹与首次查看语义（第二轮修订 P1）。

覆盖审计矩阵 T13 / T14 / T15 / T16：
  T13  visible 星创建后首次打开详情 -> first_view_at 初始化一次，views +1；
  T14  同一 actor 再显式打开 9 次 -> first_view_at/by 永不覆盖，累计 count=10；
  T15  request / offer / session 揭晓 + UI 展示 -> 该次动作总共只增加 1 次 view；
  T16  作者显式打开私人星详情 -> first_view 可写、state 仍 SEALED、
       对方任何路径都读不到（404，不泄露存在性）。

另验证：列表刷新 / 数量查询不计 view（避免页面自动刷新虚增足迹）；
足迹按人聚合（star_view_counts 只存次数），旧逐行表 star_views 停止写入。
运行：python tests/step13_view_tracking_test.py"""
from datetime import datetime
from zoneinfo import ZoneInfo

from _util import *  # noqa: F401,F403
from app.common import set_business_clock
from app.domain.errors import NotFoundError
from app.repositories import star_repo
from app.services.star_service import StarService

SH = ZoneInfo("Asia/Shanghai")
svc = StarService(fresh_db("step13"))
svc.add_special_date("human", "3.14", 3, 14, None, "anniversary")  # 种子无日期，自建示例


def view_rows(star_id):
    with svc.db.transaction() as conn:
        return star_repo.view_stats(conn, star_id)


try:
    # ---- T13：visible 星首次显式打开 ----
    v = svc.create_star("human", "一开始就公开的星", "visible")
    svc.list_shared_stars("companion")            # 列表刷新：不计 view
    d1 = svc.get_shared_star("companion", v["id"])
    assert d1["first_view_at"] is not None and d1["first_view_by"] == "companion", \
        "第一次显式打开应初始化 first_view"
    assert d1["first_view_by_name"] == "AI 伴侣"
    rows = view_rows(v["id"])
    assert rows == [{"viewer_id": "companion", "count": 1}], rows
    # 复审后口径：足迹只存每人次数，不保存逐次查看时间
    assert all(set(r.keys()) == {"viewer_id", "count"} for r in rows)
    with svc.db.transaction() as conn:
        legacy_rows = conn.execute(
            "SELECT COUNT(*) FROM star_views WHERE star_id = ?", (v["id"],)).fetchone()[0]
    assert legacy_rows == 0, "旧逐行表 star_views 已停写，新查看不再落行"

    # ---- T14：再看 9 次，first_view 永不覆盖，累计 10 ----
    first_at, first_by = d1["first_view_at"], d1["first_view_by"]
    for _ in range(9):
        # 列表刷新夹在中间也不产生额外 view
        svc.list_shared_stars("human")
        d = svc.get_shared_star("companion", v["id"])
        assert d["first_view_at"] == first_at and d["first_view_by"] == first_by, \
            "首次查看时间/人绝不能被后续查看覆盖"
    # 作者本人也看一次：按人累计互不影响
    d_author = svc.get_shared_star("human", v["id"])
    assert d_author["first_view_at"] == first_at, "作者看同一颗星不再改 first_view"
    counts = {r["viewer_id"]: r["count"] for r in view_rows(v["id"])}
    assert counts == {"companion": 10, "human": 1}, counts
    # 多次查看也只留两条聚合计数，没有任何逐次时间被保存
    with svc.db.transaction() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM star_view_counts WHERE star_id = ?", (v["id"],)).fetchone()[0]
    assert total == 2, "只按人聚合计数，不逐次落行"

    # ---- T15a：请求揭晓 = 恰好 1 次 view（含 UI 展示）----
    h1 = svc.create_star("companion", "请求路径揭晓的星", "hidden")
    req = svc.request_hidden_star("human")
    svc.respond_hidden_request("companion", req["id"], "give")
    opened = svc.open_allocated_star("human", req["id"])
    # 前端等价行为：直接用揭晓返回的 DTO 渲染（不再补发详情请求）。
    # 若前端错误地再 GET 详情，会多出第 2 条——这里用服务层内部装配
    # （record_view=False）模拟"只为渲染补数据"的路径，断言不产生额外足迹。
    rows = view_rows(h1["id"])
    assert [r["count"] for r in rows if r["viewer_id"] == "human"] == [1], rows
    assert opened["first_view_at"] == opened["opened_at"], "揭晓即首次查看"
    # 内部装配不计数
    with svc.db.transaction() as conn:
        star = star_repo.get(conn, h1["id"])
        svc._assemble_shared_detail(conn, star)
    assert [r["count"] for r in view_rows(h1["id"])
            if r["viewer_id"] == "human"] == [1], "内部装配不得累计足迹"
    svc.respond_to_star("human", h1["id"], "text", "收到了。")

    # ---- T15b：递星接住 = 恰好 1 次 view ----
    h2 = svc.create_star("companion", "递星路径揭晓的星", "hidden")
    offer = svc.offer_hidden_star("companion", h2["id"], message="看看")
    accepted = svc.accept_offered_star("human", offer["id"])
    rows = view_rows(h2["id"])
    assert [r["count"] for r in rows if r["viewer_id"] == "human"] == [1], rows
    assert accepted["first_view_by"] == "human"
    svc.respond_to_star("human", h2["id"], "text", "接住了。")

    # ---- T15c：纪念日拆星 = 恰好 1 次 view ----
    set_business_clock(datetime(2026, 3, 13, 20, 0, 0, tzinfo=SH))
    h3 = svc.create_star("human", "纪念日路径揭晓的星", "hidden")
    svc.create_star("companion", "伴侣在cutoff前写的额度星", "hidden")  # 发起者自己的额度
    set_business_clock(datetime(2026, 3, 14, 9, 0, 0, tzinfo=SH))
    dates = svc.list_special_dates("human")["items"]
    d314 = next(d for d in dates if (d["month"], d["day"]) == (3, 14))
    sess = svc.start_special_session("companion", "anniversary", d314["id"])
    svc.confirm_special_session("human", sess["id"])
    taken = svc.take_next_session_star("companion", sess["id"])
    assert taken["content"] == "纪念日路径揭晓的星"
    rows = view_rows(h3["id"])
    assert [r["count"] for r in rows if r["viewer_id"] == "companion"] == [1], rows
    assert taken["first_view_by"] == "companion" and taken["first_view_at"] == taken["opened_at"]
    svc.respond_to_star("companion", h3["id"], "text", "纪念日看到了。")
    svc.finish_special_session("companion", sess["id"])

    # ---- T16：私人星显式查看留足迹，但不公开、不改状态 ----
    priv = svc.create_star("human", "只属于对方的私人星", "hidden")
    # 列表刷新不计
    svc.list_my_hidden_stars("human")
    assert view_rows(priv["id"]) == []
    pd1 = svc.get_my_hidden_star("human", priv["id"])
    assert pd1["state"] == "SEALED" and pd1["visibility"] == "hidden", \
        "私人详情绝不改变 state / visibility"
    assert pd1["first_view_at"] is not None and pd1["first_view_by"] == "human"
    pd1_at = pd1["first_view_at"]
    svc.get_my_hidden_star("human", priv["id"])   # 再看一次只累计
    pd2 = svc.get_my_hidden_star("human", priv["id"])
    assert pd2["first_view_at"] == pd1_at
    counts = {r["viewer_id"]: r["count"] for r in view_rows(priv["id"])}
    assert counts == {"human": 3}, counts  # 显式打开 3 次

    # 对方任何路径都读不到这颗私人星
    for attempt in (
        lambda: svc.get_my_hidden_star("companion", priv["id"]),
        lambda: svc.get_shared_star("companion", priv["id"]),
    ):
        try:
            attempt()
            raise AssertionError("对方不应能读到私人星")
        except NotFoundError:
            pass
    # 服务层没有"列他人隐藏星"的入口；对方的列表里也不出现
    assert all(s["id"] != priv["id"]
               for s in svc.list_my_hidden_stars("companion")["items"])

    # 编辑私人星只是改内容：不公开、不改 first_view
    edited = svc.edit_star("human", priv["id"], content="改过内容的私人星")
    assert edited["state"] == "SEALED" and edited["visibility"] == "hidden"
    assert edited["shared_at"] is None
    assert edited["first_view_at"] == pd1_at, "编辑不得触碰 first_view"
    after = svc.get_my_hidden_star("human", priv["id"])
    assert after["content"] == "改过内容的私人星" and after["state"] == "SEALED"

    print("STEP 13 PASS —— 首次查看只写一次/重复查看只累计/揭晓恰好 1 次/"
          "列表刷新不计足迹/私人详情留痕不公开 全部符合第二轮修订语义")
finally:
    set_business_clock(None)
