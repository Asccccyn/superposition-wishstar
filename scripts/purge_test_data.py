"""正式启用前的一次性维护脚本：清除全部业务测试数据。

背景：data/superposition.db 中的业务数据全部来自 2026-09-13 的一次
step8 smoke 误连（5 颗星、1 请求、1 递星、1 回应、1 session、11 通知、
22 审计），由林石见与 GLM 共同确认无真实用户数据混入后决定清零。

保留：users（两位用户）、special_dates（使用者自建的特殊日期）、
schema_migrations（迁移登记）。清除其余全部业务表。

安全设计：
  * 不挂在 app startup——按第二轮修订审计要求，破坏性清理必须显式执行；
  * 必须传 --confirm 才会执行；
  * 全程单事务，任何异常整体回滚；
  * 执行前打印全表盘点，执行后打印核验结果。

用法：
  .venv/Scripts/python scripts/purge_test_data.py --confirm
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "superposition.db"

# 保留表：种子身份与迁移登记，绝不清除
KEEP_TABLES = ("users", "special_dates", "schema_migrations", "sqlite_sequence")

# 业务表：正式启用前全部清零
PURGE_TABLES = (
    "star_session_items",
    "star_views",            # 旧 tombstone（本应为空，防御性清理）
    "star_view_counts",
    "star_responses",
    "star_requests",
    "star_offers",
    "notifications",
    "star_opening_sessions",
    "stars",
    "audit_log",             # 全部为该次 smoke 的审计行
    "idempotency_keys",
    "auth_sessions",         # 正式启用前统一失效旧会话（当前为空，防御性清理）
)


def main() -> None:
    if "--confirm" not in sys.argv:
        print("拒绝执行：需要 --confirm 显式确认。这是删除生产业务数据的维护脚本。")
        sys.exit(2)

    if not DB_PATH.exists():
        print(f"数据库不存在：{DB_PATH}")
        sys.exit(2)

    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        print("== 清理前盘点 ==")
        for table in KEEP_TABLES[1:3] + PURGE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:24s} = {n}")

        conn.execute("BEGIN IMMEDIATE")
        for table in PURGE_TABLES:
            n = conn.execute(f"DELETE FROM {table}").rowcount
            print(f"  DELETE {table:24s} -> {n} 行")
        conn.execute("COMMIT")

        print("== 清理后核验 ==")
        ok = True
        for table in PURGE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:24s} = {n}")
            ok = ok and n == 0
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        dates = conn.execute("SELECT COUNT(*) FROM special_dates").fetchone()[0]
        migrations = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        print(f"  users={users} special_dates={dates} schema_migrations={migrations}")
        if not (ok and users == 2 and dates == 3 and migrations >= 11):
            print("核验失败：有残留或种子被误伤！")
            sys.exit(1)
        print("PURGE OK —— 业务测试数据已全部清零，种子身份与迁移登记完好。")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
