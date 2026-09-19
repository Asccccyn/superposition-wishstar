"""star_requests 表数据访问（§31）。"""


def insert(conn, req: dict) -> None:
    conn.execute(
        """INSERT INTO star_requests (id, requester_id, owner_id, status, allocated_star_id,
           requested_at, responded_at, allocated_at, opened_at)
           VALUES (:id, :requester_id, :owner_id, :status, :allocated_star_id,
           :requested_at, :responded_at, :allocated_at, :opened_at)""",
        req,
    )


def get(conn, request_id: str):
    row = conn.execute("SELECT * FROM star_requests WHERE id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def update_fields(conn, request_id: str, **fields) -> int:
    if not fields:
        return 0
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "rid": request_id}
    return conn.execute(
        f"UPDATE star_requests SET {cols} WHERE id = :rid", params
    ).rowcount


def list_outgoing(conn, user_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_requests WHERE requester_id = ? "
        "ORDER BY requested_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_incoming(conn, user_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_requests WHERE owner_id = ? "
        "ORDER BY requested_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def find_unacknowledged_opened(conn, requester_id: str):
    """找一条已打开、但请求者尚未留下有效文字回应的请求星（F03）。

    只有 type=text 且文本非空的回应才解除门槛：V1 没有服务端音频资产校验，
    任何占位音频行都不能算作完成回应。
    """
    row = conn.execute(
        """SELECT r.*
           FROM star_requests r
           WHERE r.requester_id = ?
             AND r.opened_at IS NOT NULL
             AND r.allocated_star_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1 FROM star_responses sr
                 WHERE sr.star_id = r.allocated_star_id
                   AND sr.responder_id = r.requester_id
                   AND sr.type = 'text'
                   AND TRIM(COALESCE(sr.text, '')) != ''
             )
           ORDER BY r.opened_at, r.rowid
           LIMIT 1""",
        (requester_id,),
    ).fetchone()
    return dict(row) if row else None


def find_unfinished(conn, requester_id: str):
    """请求者仍有一个未走完的请求周期（F03）：

    status=pending（等对方决定）或 status=approved 且尚未打开（等请求人接住）。
    打开后未回应的情况由 find_unacknowledged_opened 单独处理。
    """
    row = conn.execute(
        """SELECT * FROM star_requests
           WHERE requester_id = ?
             AND (status = 'pending' OR (status = 'approved' AND opened_at IS NULL))
           ORDER BY requested_at, rowid
           LIMIT 1""",
        (requester_id,),
    ).fetchone()
    return dict(row) if row else None
