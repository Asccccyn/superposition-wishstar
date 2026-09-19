"""star_responses 表数据访问（§33）。回应独立成表，不修改原始星星。"""


def insert(conn, resp: dict) -> None:
    conn.execute(
        """INSERT INTO star_responses (id, star_id, responder_id, type, text,
           audio_url, audio_duration, created_at)
           VALUES (:id, :star_id, :responder_id, :type, :text,
           :audio_url, :audio_duration, :created_at)""",
        resp,
    )


def list_by_star(conn, star_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_responses WHERE star_id = ? ORDER BY created_at, rowid",
        (star_id,),
    ).fetchall()
    return [dict(r) for r in rows]
