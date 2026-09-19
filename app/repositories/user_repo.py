"""users 表数据访问。"""


def insert(conn, user: dict) -> None:
    conn.execute(
        "INSERT INTO users (id, name, partner_id, created_at) "
        "VALUES (:id, :name, :partner_id, :created_at)",
        user,
    )


def get(conn, user_id: str):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_all(conn):
    rows = conn.execute("SELECT * FROM users ORDER BY created_at, id").fetchall()
    return [dict(r) for r in rows]


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
