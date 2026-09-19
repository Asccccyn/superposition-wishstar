"""star_opening_sessions / star_session_items 表数据访问（§35 / §36）。"""


def insert_session(conn, sess: dict) -> None:
    conn.execute(
        """INSERT INTO star_opening_sessions (id, type, special_date_id, initiated_by,
           confirmed_by, status, started_at, ended_at, actor_a_count, actor_b_count,
           quota_cutoff_at)
           VALUES (:id, :type, :special_date_id, :initiated_by,
           :confirmed_by, :status, :started_at, :ended_at, :actor_a_count, :actor_b_count,
           :quota_cutoff_at)""",
        sess,
    )


def get(conn, session_id: str):
    row = conn.execute(
        "SELECT * FROM star_opening_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def get_unfinished(conn):
    """等待确认或进行中的 session（同一时刻最多一场）。"""
    row = conn.execute(
        "SELECT * FROM star_opening_sessions WHERE status IN ('waiting_confirmation','active') "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def update_fields(conn, session_id: str, **fields) -> int:
    if not fields:
        return 0
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "sid": session_id}
    return conn.execute(
        f"UPDATE star_opening_sessions SET {cols} WHERE id = :sid", params
    ).rowcount


def insert_item(conn, item: dict) -> None:
    conn.execute(
        """INSERT INTO star_session_items (id, session_id, star_id, author_id, opened, opened_at)
           VALUES (:id, :session_id, :star_id, :author_id, :opened, :opened_at)""",
        item,
    )


def list_items(conn, session_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_session_items WHERE session_id = ? ORDER BY rowid",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_unopened_items(conn, session_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_session_items WHERE session_id = ? AND opened = 0 ORDER BY rowid",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_unopened_items_by_author(conn, session_id: str, author_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_session_items "
        "WHERE session_id = ? AND author_id = ? AND opened = 0 ORDER BY rowid",
        (session_id, author_id),
    ).fetchall()
    return [dict(r) for r in rows]


def update_item(conn, item_id: str, **fields) -> int:
    if not fields:
        return 0
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "iid": item_id}
    return conn.execute(
        f"UPDATE star_session_items SET {cols} WHERE id = :iid", params
    ).rowcount


def delete_unopened_items_by_star(conn, star_id: str) -> int:
    """删除引用某颗星、且从未拆开过的历史批次条目（F06）。

    仅在作者删除退回（SEALED）星时调用：opened 条目保留历史，不允许删除。
    """
    return conn.execute(
        "DELETE FROM star_session_items WHERE star_id = ? AND opened = 0",
        (star_id,),
    ).rowcount


def list_all(conn) -> list:
    rows = conn.execute(
        "SELECT * FROM star_opening_sessions ORDER BY rowid DESC"
    ).fetchall()
    return [dict(r) for r in rows]
