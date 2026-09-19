"""schema_migrations 版本表数据访问：迁移按 name 只执行一次。"""


def is_applied(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def mark_applied(conn, name: str, applied_at: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (name, applied_at),
    )
