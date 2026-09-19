"""种子数据：两位用户与三个特殊日期。只在空库时写入。"""
from ..common import now_iso
from ..config import DEFAULT_SPECIAL_DATES, DEFAULT_USERS
from ..repositories import special_date_repo, user_repo


def seed_if_empty(conn) -> None:
    if user_repo.count(conn) == 0:
        for u in DEFAULT_USERS:
            user_repo.insert(conn, {**u, "created_at": now_iso()})

    if special_date_repo.count(conn) == 0:
        for d in DEFAULT_SPECIAL_DATES:
            special_date_repo.insert(conn, {
                "id": f"sd_{d['month']:02d}_{d['day']:02d}",
                "name": d["name"],
                "month": d["month"],
                "day": d["day"],
                "year": d["year"],
                "type": d["type"],
                "active": 1,
                "created_by": None,
                "created_at": now_iso(),
            })
