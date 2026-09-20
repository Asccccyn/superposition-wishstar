"""MCP adapter regression: actor identity is bound outside tool arguments."""
import os
import tempfile

from _util import ROOT  # noqa: F401

from app.db.connection import Database
from app.db.schema import DDL
from app.db.seed import seed_if_empty
from app.integrations.wishstar_tools import WishStarTools
from app.services.star_service import StarService


def main() -> None:
    fd, path = tempfile.mkstemp(prefix="superposition_mcp_", suffix=".db")
    os.close(fd)
    try:
        db = Database(path)
        db.init(DDL, seed_if_empty)
        q = WishStarTools(StarService(db), "human")
        j = WishStarTools(StarService(db), "companion")

        assert q.who_am_i()["id"] == "human"
        assert j.who_am_i()["id"] == "companion"

        hidden = q.write_star("只允许对方自己在打开前看见", "hidden", "quiet")
        # 写操作最小回执：只回 id/state，不回显 content/author 等（省 AI 侧上下文）
        assert set(hidden) == {"id", "state"} and hidden["id"].startswith("star")
        assert q.bottle_counts()["my_private_count"] == 1
        assert j.bottle_counts()["partner_private_count"] == 1

        # 聚合总览（MCP 表面已用 my_overview 取代 5 个单项只读工具）
        ov = j.my_overview()
        assert ov["identity"]["id"] == "companion"
        assert ov["bottles"]["partner_private_count"] == 1
        assert isinstance(ov["requests"], dict) and isinstance(ov["offers"], dict)
        assert "count" in ov["unread_notifications"] and "latest" in ov["unread_notifications"]
        assert ov["current_session"]["session"] is None

        req = j.request_star()
        request_id = req["id"]
        approved = q.respond_to_request(request_id, "give")
        assert "allocated_star_id" not in approved["request"]
        opened = j.open_requested_star(request_id)
        assert opened["content"] == "只允许对方自己在打开前看见"
        assert j.bottle_counts()["shared_count"] == 1
        j.respond_to_star(opened["id"], "看到了。")

        # 用户规则（第二轮修订明确保留）：MCP give 同样支持指定 star_id
        pick = q.write_star("指定给伴侣的这颗", "hidden")
        req2 = j.request_star()
        given = q.respond_to_request(req2["id"], "give", star_id=pick["id"])
        assert given["request"]["status"] == "approved"
        opened2 = j.open_requested_star(req2["id"])
        assert opened2["id"] == pick["id"], "指定 give 必须给到选中的那一颗"
        assert opened2["content"] == "指定给伴侣的这颗"
        j.respond_to_star(pick["id"], "看到了，指定的这颗。")

        # 非法指定（不是主人自己的星）被拒绝，且不消耗请求
        from app.domain.errors import ValidationError
        j_star = j.write_star("伴侣写的星", "hidden")
        q.write_star("对方的瓶底星", "hidden")   # 让请求有空瓶之外的候选
        req3 = j.request_star()
        try:
            q.respond_to_request(req3["id"], "give", star_id=j_star["id"])
            raise AssertionError("指定别人的星应被拒绝")
        except ValidationError:
            pass
        declined = q.respond_to_request(req3["id"], "not_now")
        assert declined["request"]["status"] == "rejected"

        # 复审回归：MCP 发起自定义特殊日 session 不传 session_type 必须成功——
        # 默认值不得偷偷补 anniversary 再报"类型不一致"，类型由日期数据派生。
        custom = q.add_special_date("第一次看海", 4, 27, None, "custom")
        cs = j.start_session(special_date_id=custom["id"])   # 只传日期，不传 type
        # 类型派生/quota 规则由 service 层测试覆盖；MCP 回执只含 id/status
        assert set(cs) == {"id", "status"} and cs["status"] == "waiting_confirmation"
        active = q.confirm_session(cs["id"])
        assert active["status"] == "active"
        q.finish_session(cs["id"])

        visible = j.write_star("一开始就公开", "visible", "warm")
        assert set(visible) == {"id", "state"} and visible["id"].startswith("star")
        shared = q.shared_stars(author_id="companion")["items"]
        # 列表页瘦身：每项只回 id 与 note，内容点开详情才有
        assert shared and all(set(s) == {"id", "note"} for s in shared)
        assert any(s["id"] == visible["id"] for s in shared)
        hidden_list = q.my_hidden_stars()["items"]
        assert all(set(s) == {"id", "note"} for s in hidden_list)

        print("STEP 11 PASS —— actor-bound tool adapter / request / shared flow 全部通过")
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
