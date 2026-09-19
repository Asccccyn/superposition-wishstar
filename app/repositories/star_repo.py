"""stars 表数据访问。

update_state 用 `WHERE state = from_state` 做条件更新：
并发下同一颗星只会被一个流程成功迁移，这是架构文档 §48 的原子性要求。
"""

_STATES_TABLE = "stars"


def _row(row):
    return dict(row) if row else None


def insert(conn, star: dict) -> None:
    conn.execute(
        """INSERT INTO stars (id, author_id, recipient_id, content, mood_type, mood_text, note,
           visibility, state, written_at, shared_at, opened_at, opened_by, open_mode,
           shared_origin, cycle_id, created_at, updated_at)
           VALUES (:id, :author_id, :recipient_id, :content, :mood_type, :mood_text, :note,
           :visibility, :state, :written_at, :shared_at, :opened_at, :opened_by, :open_mode,
           :shared_origin, :cycle_id, :created_at, :updated_at)""",
        star,
    )


def get(conn, star_id: str):
    return _row(conn.execute("SELECT * FROM stars WHERE id = ?", (star_id,)).fetchone())


def update_state(conn, star_id: str, to_state: str, from_state: str | None = None, **fields) -> int:
    sets = {"state": to_state, **fields}
    cols = ", ".join(f"{k} = :{k}" for k in sets)
    sql = f"UPDATE {_STATES_TABLE} SET {cols} WHERE id = :star_id"
    if from_state is not None:
        sql += " AND state = :from_state"
    params = {**sets, "star_id": star_id, "from_state": from_state}
    return conn.execute(sql, params).rowcount


def update_fields(conn, star_id: str, **fields) -> int:
    if not fields:
        return 0
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "sid": star_id}
    return conn.execute(f"UPDATE {_STATES_TABLE} SET {cols} WHERE id = :sid", params).rowcount


def delete(conn, star_id: str) -> int:
    return conn.execute("DELETE FROM stars WHERE id = ?", (star_id,)).rowcount


def list_by_author_states(conn, author_id: str, states) -> list:
    marks = ", ".join(f":s{i}" for i in range(len(states)))
    params = {f"s{i}": s for i, s in enumerate(states)}
    params["aid"] = author_id
    rows = conn.execute(
        f"SELECT * FROM stars WHERE author_id = :aid AND state IN ({marks}) "
        "ORDER BY written_at DESC, rowid DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def list_sealed_by_author(conn, author_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM stars WHERE author_id = ? AND visibility = 'hidden' AND state = 'SEALED' "
        "ORDER BY written_at, rowid",
        (author_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_hidden_unopened(conn, author_id: str) -> int:
    """私人瓶子数量 = 尚未被打开的隐藏星（§3.1 / §39：尚未公开）。"""
    return conn.execute(
        "SELECT COUNT(*) FROM stars WHERE author_id = ? AND visibility = 'hidden' "
        "AND state IN ('SEALED','OFFERED','ALLOCATED_RANDOMLY','SESSION_LOCKED')",
        (author_id,),
    ).fetchone()[0]


def count_sealed(conn, author_id: str) -> int:
    """可被随机分配 / 主动递出的候选星（§47）。"""
    return conn.execute(
        "SELECT COUNT(*) FROM stars WHERE author_id = ? AND visibility = 'hidden' "
        "AND state = 'SEALED'",
        (author_id,),
    ).fetchone()[0]


def pick_random_sealed(conn, author_id: str):
    """服务端随机抽一颗候选星（§47）：请求人与批准人都无法指定结果。"""
    return _row(conn.execute(
        "SELECT * FROM stars WHERE author_id = ? AND visibility = 'hidden' "
        "AND state = 'SEALED' ORDER BY RANDOM() LIMIT 1",
        (author_id,),
    ).fetchone())


def count_hidden_asof(conn, author_id: str, cutoff: str) -> int:
    """纪念日 0 点快照口径：cutoff 前已写入、且在 cutoff 时刻仍属于
    作者私人未公开瓶的隐藏星数量（产品不删星，hidden 合法出瓶必写
    shared_at，因此可用历史时间恢复 0 点状态，无需复制快照表）。"""
    return conn.execute(
        """SELECT COUNT(*) FROM stars
           WHERE author_id = :aid AND visibility = 'hidden'
             AND written_at < :cutoff
             AND (shared_at IS NULL OR shared_at >= :cutoff)""",
        {"aid": author_id, "cutoff": cutoff},
    ).fetchone()[0]


def count_anniversary_candidates(conn, author_id: str, cutoff: str) -> int:
    """本轮纪念日当前仍可被拆的候选数：0 点口径 + 当前仍是 SEALED。"""
    return conn.execute(
        """SELECT COUNT(*) FROM stars
           WHERE author_id = :aid AND visibility = 'hidden' AND state = 'SEALED'
             AND written_at < :cutoff
             AND (shared_at IS NULL OR shared_at >= :cutoff)""",
        {"aid": author_id, "cutoff": cutoff},
    ).fetchone()[0]


def pick_anniversary_candidate(conn, author_id: str, cutoff: str):
    """纪念日 take-next 候选：0 点时在对方瓶里 + 当前仍 SEALED（被请求/
    递出锁住的星不能被抢走），随机挑一颗。0 点后新写的星天然不在其中。"""
    return _row(conn.execute(
        """SELECT * FROM stars
           WHERE author_id = :aid AND visibility = 'hidden' AND state = 'SEALED'
             AND written_at < :cutoff
             AND (shared_at IS NULL OR shared_at >= :cutoff)
           ORDER BY RANDOM() LIMIT 1""",
        {"aid": author_id, "cutoff": cutoff},
    ).fetchone())


def count_shared(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM stars WHERE state = 'SHARED'"
    ).fetchone()[0]


def count_session_viewed_by(conn, session_id: str, viewer_id: str) -> int:
    """本轮里 actor 已经看了对方多少颗（按 cycle_id + opened_by 统计）。"""
    return conn.execute(
        "SELECT COUNT(*) FROM stars WHERE cycle_id = ? AND opened_by = ?",
        (session_id, viewer_id),
    ).fetchone()[0]


def find_unresponded_revealed(conn, viewer_id: str):
    """用户规则：私人瓶进入公共池的星，看了必须留一句话。

    找出 viewer 打开过、至今没有留下有效文字回应的私转公星星；
    存在这样一颗时，viewer 不能再打开/接住/互看任何新的星。
    """
    row = conn.execute(
        """SELECT s.* FROM stars s
           WHERE s.opened_by = ?
             AND s.state = 'SHARED'
             AND s.shared_origin != 'visible_from_start'
             AND NOT EXISTS (
                 SELECT 1 FROM star_responses r
                 WHERE r.star_id = s.id
                   AND r.responder_id = ?
                   AND r.type = 'text'
                   AND TRIM(COALESCE(r.text, '')) != ''
             )
           ORDER BY s.opened_at, s.rowid
           LIMIT 1""",
        (viewer_id, viewer_id),
    ).fetchone()
    return dict(row) if row else None


def upsert_view_count(conn, star_id: str, viewer_id: str) -> None:
    """查看足迹：按人累计 +1。只存次数，不保存每一次的查看时间——
    第一次的时间与人在 stars.first_view_at/first_view_by（CAS 只写一次）。"""
    conn.execute(
        """INSERT INTO star_view_counts (star_id, viewer_id, view_count)
           VALUES (:sid, :vid, 1)
           ON CONFLICT (star_id, viewer_id)
           DO UPDATE SET view_count = view_count + 1""",
        {"sid": star_id, "vid": viewer_id},
    )


def set_first_view_if_empty(conn, star_id: str, first_view_at: str,
                            first_view_by: str) -> int:
    """CAS 写入第一次真正查看的时间与人：只有 first_view_at 为空才写，
    永不覆盖。rowcount = 0 表示之前已有人看过第一次。"""
    return conn.execute(
        """UPDATE stars SET first_view_at = :fv_at, first_view_by = :fv_by
           WHERE id = :sid AND first_view_at IS NULL""",
        {"fv_at": first_view_at, "fv_by": first_view_by, "sid": star_id},
    ).rowcount


def view_stats(conn, star_id: str) -> list:
    """按人累计：[{viewer_id, count}]。不保存/不返回每次查看的时间。"""
    rows = conn.execute(
        "SELECT viewer_id, view_count AS count FROM star_view_counts "
        "WHERE star_id = ? ORDER BY view_count DESC, viewer_id",
        (star_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_shared(conn, *, author_id=None, written_date=None, opened_date=None,
                shared_origin=None, cycle_id=None) -> list:
    clauses = ["state = 'SHARED'"]
    params: list = []
    if author_id:
        clauses.append("author_id = ?")
        params.append(author_id)
    if written_date:
        clauses.append("substr(written_at, 1, 10) = ?")
        params.append(written_date)
    if opened_date:
        clauses.append("substr(opened_at, 1, 10) = ?")
        params.append(opened_date)
    if shared_origin:
        clauses.append("shared_origin = ?")
        params.append(shared_origin)
    if cycle_id:
        clauses.append("cycle_id = ?")
        params.append(cycle_id)

    sql = (
        "SELECT * FROM stars WHERE " + " AND ".join(clauses) +
        " ORDER BY COALESCE(shared_at, created_at) DESC, rowid DESC"
    )
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
