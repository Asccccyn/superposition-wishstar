"""Step12 验证：迁移版本表与"重启不改变业务状态"（第二轮修订 P0）。

覆盖审计矩阵 T1 / T2 / T8：
  T1  建 waiting session -> 重新 init Database -> session 仍 waiting；
  T2  确认 active -> 再 init（API 与 MCP 同一条启动路径）-> 仍 active，
      quota / cutoff 保持 DB 已存值；
  T8  active 后跨重启不重算。
另外验证：
  * 退休的破坏性迁移（unlock_session_stars / close_legacy_sessions）不再执行，
    即使库里残留旧版脏数据（SESSION_LOCKED 星 / waiting session）也保持原样；
  * 每个迁移按 name 在 schema_migrations 只登记一次，二次启动零 SQL 重放。

运行：python tests/step12_migration_restart_test.py"""
from datetime import datetime
from zoneinfo import ZoneInfo

from _util import *  # noqa: F401,F403
from app.common import now_iso, set_business_clock
from app.db.connection import Database
from app.db.schema import DDL, MIGRATIONS, RETIRED_MIGRATIONS
from app.db.seed import seed_if_empty
from app.integrations.wishstar_tools import WishStarTools
from app.repositories import migration_repo, star_repo
from app.services.star_service import StarService

SH = ZoneInfo("Asia/Shanghai")


def boot(path):
    """与 app.main.create_app / mcp_server.build_tools 完全相同的三行启动路径。"""
    db = Database(path, migrations=MIGRATIONS)
    db.init(DDL, seed_if_empty)
    return db


db = fresh_db("step12")
path = db.path
svc = StarService(db)
svc.add_special_date("human", "3.14", 3, 14, None, "anniversary")  # 种子无日期，自建示例

try:
    set_business_clock(datetime(2026, 3, 13, 20, 0, 0, tzinfo=SH))
    svc.create_star("human", "重启前写的甲", "hidden")
    svc.create_star("companion", "重启前写的乙", "hidden")
    set_business_clock(datetime(2026, 3, 14, 9, 0, 0, tzinfo=SH))
    dates = svc.list_special_dates("human")["items"]
    d314 = next(d for d in dates if (d["month"], d["day"]) == (3, 14))

    # ---- T1：waiting session 跨重启保持 waiting ----
    sess = svc.start_special_session("human", "anniversary", d314["id"])
    assert sess["status"] == "waiting_confirmation"

    db2 = boot(path)                      # 模拟 API 停止后再次启动
    svc2 = StarService(db2)
    again = svc2.get_current_session("human")["session"]
    assert again is not None and again["status"] == "waiting_confirmation", \
        "T1 失败：重启不得结束 waiting session"
    assert again["actor_a_count"] == 1 and again["actor_b_count"] == 1

    # ---- T2/T8：active session 跨重启保持 active，quota/cutoff 不重算 ----
    set_business_clock(datetime(2026, 3, 14, 21, 0, 0, tzinfo=SH))
    # 0 点后新写一颗：不影响已冻结的额度；重启后也不能被算进去
    svc2.create_star("companion", "0点后才写的J", "hidden")
    svc2.confirm_special_session("companion", again["id"])

    db3 = boot(path)                      # 再一次重启
    svc3 = StarService(db3)
    ov = svc3.get_current_session("human")
    assert ov["session"]["status"] == "active", "T2 失败：重启不得结束 active session"
    assert ov["session"]["quota_cutoff_at"] == "2026-03-14T00:00:00"
    assert ov["session"]["actor_a_count"] == 1 and ov["session"]["actor_b_count"] == 1, \
        "T8 失败：重启不得按当前数量重算 quota"
    assert ov["my_quota"] == 1

    # MCP 同一条启动路径（WishStarTools 与 mcp_server.build_tools 等价）
    tools = WishStarTools(StarService(boot(path)), "companion")
    ov_mcp = tools.current_session()
    assert ov_mcp["session"]["status"] == "active", "MCP 启动同样不得改变 session"
    assert ov_mcp["session"]["quota_cutoff_at"] == "2026-03-14T00:00:00"

    # ---- 退休迁移永不执行：旧版脏数据保持原样 ----
    with db3.transaction(immediate=True) as conn:
        # 手工造旧版遗留：SESSION_LOCKED 星 + 另一场 waiting session
        conn.execute(
            "UPDATE stars SET state = 'SESSION_LOCKED' "
            "WHERE content = '0点后才写的J'")
        conn.execute(
            "INSERT INTO star_opening_sessions (id, type, special_date_id, initiated_by, "
            "confirmed_by, status, started_at, ended_at, actor_a_count, actor_b_count, "
            "quota_cutoff_at) VALUES ('sess_legacy_dirty', 'special_day', NULL, 'human', "
            "NULL, 'waiting_confirmation', NULL, NULL, NULL, NULL, NULL)")
    db4 = boot(path)                      # 含脏数据的重启
    with db4.transaction() as conn:
        locked = conn.execute(
            "SELECT state FROM stars WHERE content = '0点后才写的J'").fetchone()[0]
        dirty = conn.execute(
            "SELECT status FROM star_opening_sessions "
            "WHERE id = 'sess_legacy_dirty'").fetchone()[0]
        applied = {r["name"] for r in conn.execute(
            "SELECT name FROM schema_migrations")}
    assert locked == "SESSION_LOCKED", "退休的 unlock_session_stars 不得再自动执行"
    assert dirty == "waiting_confirmation", "退休的 close_legacy_sessions 不得再自动执行"
    assert set(RETIRED_MIGRATIONS) <= applied, "退休迁移应登记进版本表防止任何重放"

    # ---- 迁移只执行一次：登记表稳定，二次启动不重放任何迁移 ----
    with db.transaction() as conn:
        before = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    boot(path)
    boot(path)
    with db.transaction() as conn:
        after = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        # applied_at 不因再次启动而变化 -> 没有重放
        stamps = {r["name"]: r["applied_at"] for r in conn.execute(
            "SELECT name, applied_at FROM schema_migrations")}
    boot(path)
    with db.transaction() as conn:
        stamps2 = {r["name"]: r["applied_at"] for r in conn.execute(
            "SELECT name, applied_at FROM schema_migrations")}
    assert before == after
    assert stamps == stamps2, "同一迁移不得在二次启动时重新登记/执行"
    for name, _ in MIGRATIONS:
        assert name in after, f"迁移 {name} 应已登记"

    # ---- 一次性数据迁移：旧 star_views 回填聚合表与 first_view ----
    # 模拟旧版库：一颗已被看过多次的星（逐行 star_views、first_view 为空）
    legacy_star = "star_legacy_views01"
    with db4.transaction(immediate=True) as conn:
        conn.execute(
            "INSERT INTO stars (id, author_id, recipient_id, content, visibility, state, "
            "written_at, shared_at, created_at, updated_at) VALUES "
            "(:id, 'human', 'companion', '旧版被看过的星', 'hidden', 'SHARED', "
            "'2026-01-01T10:00:00', '2026-01-02T10:00:00', '2026-01-01T10:00:00', "
            "'2026-01-02T10:00:00')", {"id": legacy_star})
        # 旧版逐行足迹：对方看了 1 次（更早），伴侣看了 2 次
        conn.executemany(
            "INSERT INTO star_views (star_id, viewer_id, viewed_at) VALUES (?, ?, ?)",
            [(legacy_star, "human", "2026-01-02T11:00:00"),
             (legacy_star, "companion", "2026-01-03T09:00:00"),
             (legacy_star, "companion", "2026-01-04T09:00:00")])
        # 模拟"旧库带数据首次升级"：撤销 backfill/purge 登记，
        # 让下一次启动按顺序真正执行它们
        conn.execute(
            "DELETE FROM schema_migrations WHERE name LIKE 'backfill%' "
            "OR name = 'purge_legacy_star_views'")
    db5 = boot(path)                      # 启动即执行 backfill x2 + purge
    with db5.transaction() as conn:
        fv = dict(conn.execute(
            "SELECT first_view_at, first_view_by FROM stars WHERE id = ?",
            (legacy_star,)).fetchone())
        agg = {r["viewer_id"]: r["view_count"] for r in conn.execute(
            "SELECT viewer_id, view_count FROM star_view_counts WHERE star_id = ?",
            (legacy_star,))}
        applied = {r["name"] for r in conn.execute(
            "SELECT name FROM schema_migrations")}
        legacy_rows = conn.execute(
            "SELECT COUNT(*) FROM star_views WHERE star_id = ?",
            (legacy_star,)).fetchone()[0]
        legacy_total = conn.execute("SELECT COUNT(*) FROM star_views").fetchone()[0]
    assert fv["first_view_at"] == "2026-01-02T11:00:00" \
        and fv["first_view_by"] == "human", \
        f"first_view 应回填旧表最早一条：{fv}"
    assert agg == {"human": 1, "companion": 2}, f"聚合表应等于旧表按人计数：{agg}"
    assert legacy_rows == 0 and legacy_total == 0, \
        "回填完成后必须清空旧逐次时间数据（star_views 仅留空 tombstone）"
    assert {"backfill_star_view_counts", "backfill_first_view",
            "purge_legacy_star_views"} <= applied
    boot(path)                            # 再次启动：迁移不重放，聚合不翻倍
    with db5.transaction() as conn:
        agg2 = {r["viewer_id"]: r["view_count"] for r in conn.execute(
            "SELECT viewer_id, view_count FROM star_view_counts WHERE star_id = ?",
            (legacy_star,))}
        legacy_total2 = conn.execute("SELECT COUNT(*) FROM star_views").fetchone()[0]
    assert agg2 == agg, "backfill 迁移只执行一次，重启不得把计数翻倍"
    assert legacy_total2 == 0

    # ---- 旧基线缺口：hidden+SHARED 但 shared_at=NULL 会污染纪念日 0 点额度 ----
    # as-of 判定依赖 shared_at IS NULL OR shared_at >= cutoff；shared_at 为空的
    # 旧公共池星会被误认为"从未离开私人瓶"。迁移必须把它回填为 opened_at。
    legacy_shared = "star_legacy_shared01"
    sealed_star = "star_legacy_sealed01"
    with db5.transaction(immediate=True) as conn:
        base = ("INSERT INTO stars (id, author_id, recipient_id, content, visibility, "
                "state, written_at, opened_at, opened_by, open_mode, shared_origin, "
                "created_at, updated_at) VALUES ")
        # 旧公共池星：hidden / SHARED / opened_at 有值 / shared_at=NULL（旧库缺陷形态）
        conn.execute(base + "(:id, 'human', 'companion', '旧基线缺shared_at的星', "
                     "'hidden', 'SHARED', '2026-01-01T10:00:00', '2026-01-05T12:00:00', "
                     "'companion', 'anniversary', 'revealed_by_anniversary', "
                     "'2026-01-01T10:00:00', '2026-01-05T12:00:00')", {"id": legacy_shared})
        # 对照组：真正还封存在私人瓶的星，迁移后必须仍计入额度
        conn.execute(base + "(:id, 'human', 'companion', '仍封存的对照星', "
                     "'hidden', 'SEALED', '2026-01-01T10:00:00', NULL, NULL, NULL, NULL, "
                     "'2026-01-01T10:00:00', '2026-01-01T10:00:00')", {"id": sealed_star})
        # 迁移前基线：缺陷星确实会被 as-of 误计（证明问题存在）。
        # 对方此时计入 3 颗 = "重启前写的甲"(合法 SEALED) + 缺陷星 + 对照星
        before = star_repo.count_hidden_asof(conn, "human", "2026-03-14T00:00:00")
        assert before == 3, f"迁移前缺陷星被误计入私人瓶额度：{before}"
        # 模拟旧库首次升级：撤销登记让下一次启动执行
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = 'backfill_legacy_shared_at'")
    db6 = boot(path)
    with db6.transaction() as conn:
        fixed = dict(conn.execute(
            "SELECT shared_at, opened_at FROM stars WHERE id = ?", (legacy_shared,)).fetchone())
        after = star_repo.count_hidden_asof(conn, "human", "2026-03-14T00:00:00")
        stamp = conn.execute(
            "SELECT applied_at FROM schema_migrations WHERE name = 'backfill_legacy_shared_at'"
        ).fetchone()["applied_at"]
    assert fixed["shared_at"] == fixed["opened_at"] == "2026-01-05T12:00:00", \
        f"shared_at 必须回填为 opened_at：{fixed}"
    assert after == before - 1 == 2, \
        "迁移后缺陷星退出 0 点额度，合法 SEALED（Q 星 + 对照星）仍计入"
    boot(path)                            # 再次启动：迁移不重跑
    with db6.transaction() as conn:
        stamp2 = conn.execute(
            "SELECT applied_at FROM schema_migrations WHERE name = 'backfill_legacy_shared_at'"
        ).fetchone()["applied_at"]
    assert stamp == stamp2, "backfill_legacy_shared_at 只执行一次"

    print("STEP 12 PASS —— 重启保持 waiting/active、quota/cutoff 不重算、"
          "退休破坏性迁移永不自动执行、迁移按 name 只跑一次、"
          "旧 star_views 一次性回填聚合表与 first_view 并清空、"
          "legacy shared_at 回填修复 0 点额度误判")
finally:
    set_business_clock(None)
