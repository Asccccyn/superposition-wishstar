"""notifications 表数据访问（§38：轻提示，不含隐藏星正文）。"""


def insert(conn, nid: str, recipient_id: str, ntype: str, title: str,
           body, star_id, created_at: str) -> None:
    conn.execute(
        """INSERT INTO notifications (id, recipient_id, type, title, body, star_id, is_read, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
        (nid, recipient_id, ntype, title, body, star_id, created_at),
    )


def get(conn, notification_id: str):
    row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return dict(row) if row else None


def list_for(conn, recipient_id: str, unread_only: bool = False, limit: int = 50) -> list:
    sql = "SELECT * FROM notifications WHERE recipient_id = ?"
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    rows = conn.execute(sql, (recipient_id, limit)).fetchall()
    return [dict(r) for r in rows]


def count_unread(conn, recipient_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE recipient_id = ? AND is_read = 0",
        (recipient_id,),
    ).fetchone()[0]


def count_unread_by_type(conn, recipient_id: str, ntype: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE recipient_id = ? AND is_read = 0 AND type = ?",
        (recipient_id, ntype),
    ).fetchone()[0]


def find_any_by_type(conn, recipient_id: str, ntype: str):
    """找该接收人此类型的任意一条通知（F02 合并数量提示用）。"""
    row = conn.execute(
        "SELECT * FROM notifications WHERE recipient_id = ? AND type = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (recipient_id, ntype),
    ).fetchone()
    return dict(row) if row else None


def mark_unread(conn, notification_id: str) -> int:
    """重新置为未读；不改 created_at，避免落盘新的逐颗时间信息。"""
    return conn.execute(
        "UPDATE notifications SET is_read = 0 WHERE id = ?", (notification_id,)
    ).rowcount


def mark_all_type_read(conn, recipient_id: str, ntype: str) -> int:
    """合并通知被读时，把该类型的所有行一起置为已读。"""
    return conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE recipient_id = ? AND type = ?",
        (recipient_id, ntype),
    ).rowcount


def mark_read(conn, notification_id: str, recipient_id: str) -> int:
    return conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ? AND recipient_id = ?",
        (notification_id, recipient_id),
    ).rowcount
