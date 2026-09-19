"""idempotency_keys 表数据访问（F10：网络重试不产生重复写入）。"""


def get(conn, actor_id: str, operation_id: str, endpoint: str):
    """同一 actor + 同一操作 ID + 同一端点才命中缓存结果。"""
    row = conn.execute(
        "SELECT result_json FROM idempotency_keys "
        "WHERE actor_id = ? AND operation_id = ? AND endpoint = ?",
        (actor_id, operation_id, endpoint),
    ).fetchone()
    return row["result_json"] if row else None


def insert(conn, actor_id: str, operation_id: str, endpoint: str,
           result_json: str, created_at: str) -> None:
    conn.execute(
        """INSERT INTO idempotency_keys (actor_id, operation_id, endpoint, result_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (actor_id, operation_id, endpoint, result_json, created_at),
    )


def purge_expired(conn, before_iso: str) -> int:
    """清理超过保留期的幂等记录（随写事务顺带执行，失败不影响业务）。"""
    return conn.execute(
        "DELETE FROM idempotency_keys WHERE created_at < ?", (before_iso,)
    ).rowcount
