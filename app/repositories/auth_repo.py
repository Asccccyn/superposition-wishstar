"""auth_sessions 表数据访问（F08：浏览器会话的服务端撤销记录）。"""

from datetime import datetime


def insert(conn, sid: str, actor_id: str, expires_ts: int) -> None:
    conn.execute(
        """INSERT INTO auth_sessions (sid, actor_id, created_at, expires_ts, revoked_at)
           VALUES (?, ?, ?, ?, NULL)""",
        (sid, actor_id, datetime.now().isoformat(timespec="seconds"), expires_ts),
    )


def get(conn, sid: str):
    row = conn.execute("SELECT * FROM auth_sessions WHERE sid = ?", (sid,)).fetchone()
    return dict(row) if row else None


def revoke(conn, sid: str) -> int:
    """撤销一个会话；已撤销/不存在时 rowcount 为 0，幂等。"""
    return conn.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE sid = ? AND revoked_at IS NULL",
        (datetime.now().isoformat(timespec="seconds"), sid),
    ).rowcount
