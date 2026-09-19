"""Step2 验证：数据层（建库 / 种子 / 仓储 CRUD / 条件状态更新）。
运行：python tests/step2_db_test.py"""
from _util import *  # noqa: F401,F403
from app.common import now_iso
from app.repositories import special_date_repo, star_repo, user_repo
from app.db.connection import Database
from app.db.schema import DDL
from app.db.seed import seed_if_empty

db = fresh_db("step2")

with db.transaction() as conn:
    # 种子：两位用户，伙伴绑定互相指向
    users = user_repo.list_all(conn)
    assert len(users) == 2, "应有两个用户"
    by_id = {u["id"]: u for u in users}
    assert by_id["human"]["partner_id"] == "companion"
    assert by_id["companion"]["partner_id"] == "human"

    # 特殊日期作为数据存在（§34），不写死在业务逻辑里
    dates = special_date_repo.list_active(conn)
    assert dates == [], "种子不应预置特殊日期（日期属于使用者的私人数据）"

    # 插入一颗隐藏星
    now = now_iso()
    star = {
        "id": "star_test000001", "author_id": "companion", "recipient_id": "human",
        "content": "测试星", "mood_type": None, "mood_text": "开心", "note": None,
        "visibility": "hidden", "state": "CREATED", "written_at": now,
        "shared_at": None, "opened_at": None, "opened_by": None,
        "open_mode": None, "shared_origin": None, "cycle_id": None,
        "created_at": now, "updated_at": now,
    }
    star_repo.insert(conn, star)
    assert star_repo.get(conn, star["id"])["content"] == "测试星"

    # CREATED -> SEALED（条件更新成功）
    assert star_repo.update_state(conn, star["id"], "SEALED", from_state="CREATED") == 1

    # 并发保护（§48）：用错误的前置状态更新 → rowcount = 0，不会误改
    assert star_repo.update_state(conn, star["id"], "SHARED", from_state="CREATED") == 0
    assert star_repo.get(conn, star["id"])["state"] == "SEALED"

    # 计数查询
    assert star_repo.count_sealed(conn, "companion") == 1
    assert star_repo.count_hidden_unopened(conn, "companion") == 1
    assert star_repo.count_shared(conn) == 0
    assert star_repo.pick_random_sealed(conn, "companion")["id"] == star["id"]

print("STEP 2 PASS —— 建库 / 种子 / 仓储 / 条件状态更新全部正常")
