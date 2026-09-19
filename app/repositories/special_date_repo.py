"""special_dates 表数据访问（§34：纪念日/特殊日是数据，不是代码）。"""


def insert(conn, d: dict) -> None:
    conn.execute(
        """INSERT INTO special_dates (id, name, month, day, year, type, active, created_by, created_at)
           VALUES (:id, :name, :month, :day, :year, :type, :active, :created_by, :created_at)""",
        d,
    )


def get(conn, date_id: str):
    row = conn.execute("SELECT * FROM special_dates WHERE id = ?", (date_id,)).fetchone()
    return dict(row) if row else None


def list_active(conn) -> list:
    rows = conn.execute(
        "SELECT * FROM special_dates WHERE active = 1 ORDER BY month, day"
    ).fetchall()
    return [dict(r) for r in rows]


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM special_dates").fetchone()[0]
