"""star_offers 表数据访问（§32）。"""


def insert(conn, offer: dict) -> None:
    conn.execute(
        """INSERT INTO star_offers (id, star_id, sender_id, recipient_id, status,
           offered_at, opened_at, message)
           VALUES (:id, :star_id, :sender_id, :recipient_id, :status,
           :offered_at, :opened_at, :message)""",
        offer,
    )


def get(conn, offer_id: str):
    row = conn.execute("SELECT * FROM star_offers WHERE id = ?", (offer_id,)).fetchone()
    return dict(row) if row else None


def update_fields(conn, offer_id: str, **fields) -> int:
    if not fields:
        return 0
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "oid": offer_id}
    return conn.execute(
        f"UPDATE star_offers SET {cols} WHERE id = :oid", params
    ).rowcount


def list_incoming(conn, user_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_offers WHERE recipient_id = ? ORDER BY offered_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_outgoing(conn, user_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM star_offers WHERE sender_id = ? ORDER BY offered_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]
