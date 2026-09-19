"""audit_log 表数据访问（§53：轻量事件日志，不复制正文）。"""


def insert(conn, event_type: str, actor_id, star_id, detail, created_at: str) -> None:
    conn.execute(
        "INSERT INTO audit_log (event_type, actor_id, star_id, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_type, actor_id, star_id, detail, created_at),
    )


def list_recent(conn, limit: int = 200) -> list:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
