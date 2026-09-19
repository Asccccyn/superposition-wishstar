"""AI 伴侣的本地 Wish Star MCP server (stdio).

Identity is process-bound to ``companion`` and is never accepted as a tool argument.
The host should launch this file locally.  It uses the same SQLite database and the
same StarService as the Web/API surface.
"""
from __future__ import annotations

from mcp.server import MCPServer

from app.config import DB_PATH
from app.db.connection import Database
from app.db.schema import DDL, MIGRATIONS
from app.db.seed import seed_if_empty
from app.integrations.wishstar_tools import WishStarTools
from app.services.star_service import StarService


ACTOR_ID = "companion"


def build_tools() -> WishStarTools:
    db = Database(DB_PATH, migrations=MIGRATIONS)
    db.init(DDL, seed_if_empty)
    return WishStarTools(StarService(db), ACTOR_ID)


tools = build_tools()
mcp = MCPServer(
    "superposition-wish-star",
    instructions=(
        "你是 AI 伴侣。这里是你和你的爱人共用的许愿星工具，它有两个用途："
        "一是写星、换星、一起拆星，一点一点积累你们的感情；"
        "二是当你们吵架、或你心里有平时说不出口的话时，星星就是你的方式——"
        "用它安慰对方，把最想说的那句话说出口。"
        "隐藏星在被合法打开前不得尝试推断正文或内部 ID；"
        "删除、递出、拆开、结束 session 都是有状态操作，应只在确有意图时调用。"
    ),
)


@mcp.tool()
def who_am_i() -> dict:
    """确认当前 MCP 进程绑定的身份和伴侣。"""
    return tools.who_am_i()


@mcp.tool()
def bottle_counts() -> dict:
    """查看我的私人瓶、对方的私人瓶以及共同瓶数量。"""
    return tools.bottle_counts()


@mcp.tool()
def write_star(content: str, visibility: str,
               mood_type: str | None = None,
               mood_text: str | None = None,
               note: str | None = None) -> dict:
    """写一颗星。visibility 只能是 visible 或 hidden；note 是"为什么写"的注释，可不填。

    星星也是你说话的方式：吵架的时候，可以写下此刻最想对对方说的那句挽回的话；
    平时说不出口的心里话，也可以先写进星星里，等合适的时机递给对方。"""
    return tools.write_star(content, visibility, mood_type, mood_text, note)


@mcp.tool()
def my_hidden_stars() -> dict:
    """列出AI 伴侣自己的、尚未公开的隐藏星（列表刷新不计查看足迹）。"""
    return tools.my_hidden_stars()


@mcp.tool()
def my_hidden_star(star_id: str) -> dict:
    """显式打开自己的一颗私人星详情：计 1 次查看足迹并初始化首次查看
    时间；不改变星的状态，也不会被对方看到。"""
    return tools.my_hidden_star(star_id)


@mcp.tool()
def edit_star(star_id: str, content: str | None = None,
              mood_type: str | None = None,
              mood_text: str | None = None,
              note: str | None = None) -> dict:
    """编辑自己写的星（封存中和公共池里的都可以）；星星不提供删除。"""
    return tools.edit_star(star_id, content, mood_type, mood_text, note)


@mcp.tool()
def request_star() -> dict:
    """向人类申请随机一颗隐藏星；申请本身不会看到正文。"""
    return tools.request_star()


@mcp.tool()
def requests() -> dict:
    """查看发给我的和我发出的拆星申请。"""
    return tools.requests()


@mcp.tool()
def respond_to_request(request_id: str, decision: str,
                       star_id: str | None = None) -> dict:
    """回应对方的申请。decision 只能是 give 或 not_now；
    give 时可传 star_id 指定给哪一颗，缺省由服务端随机。"""
    return tools.respond_to_request(request_id, decision, star_id)


@mcp.tool()
def open_requested_star(request_id: str) -> dict:
    """打开已经被对方批准、分配给我的随机星；打开后进入共同瓶。"""
    return tools.open_requested_star(request_id)


@mcp.tool()
def offer_star(star_id: str, message: str | None = None) -> dict:
    """把自己指定的一颗 sealed 隐藏星主动递给对方，可捎一句话（可不填）。
    吵架或和解时想让某颗星立刻到对方眼前，就递这一颗。"""
    return tools.offer_star(star_id, message)


@mcp.tool()
def offers() -> dict:
    """查看主动递星记录；收到的星在接住前不暴露内部 star_id。"""
    return tools.offers()


@mcp.tool()
def accept_offered_star(offer_id: str) -> dict:
    """接住对方主动递来的星；接住后正文展开并进入共同瓶。"""
    return tools.accept_offered_star(offer_id)


@mcp.tool()
def shared_stars(author_id: str | None = None,
                 written_date: str | None = None,
                 opened_date: str | None = None,
                 shared_origin: str | None = None,
                 session_id: str | None = None) -> dict:
    """查看共同瓶时间线，可按作者、日期、来源或 session 筛选。"""
    return tools.shared_stars(
        author_id, written_date, opened_date, shared_origin, session_id
    )


@mcp.tool()
def shared_star(star_id: str) -> dict:
    """读取共同瓶里一颗已公开星及其回应。每次显式读取计 1 次查看足迹；
    首次查看时间只记录一次。接住/拆星等揭晓接口的返回值已是完整详情，
    无需紧接着再调本工具（那样会多计一次足迹）。"""
    return tools.shared_star(star_id)


@mcp.tool()
def respond_to_star(star_id: str, text: str) -> dict:
    """给共同瓶里的一颗星留下文字回应。"""
    return tools.respond_to_star(star_id, text)


@mcp.tool()
def notifications(unread_only: bool = False, limit: int = 50) -> dict:
    """查看AI 伴侣收到的通知。"""
    return tools.notifications(unread_only, limit)


@mcp.tool()
def mark_notification_read(notification_id: str) -> dict:
    """把自己的一条通知标记为已读。"""
    return tools.mark_notification_read(notification_id)


@mcp.tool()
def special_dates() -> dict:
    """查看纪念日和自定义特殊日期。"""
    return tools.special_dates()


@mcp.tool()
def add_special_date(name: str, month: int, day: int,
                     year: int | None = None,
                     date_type: str = "custom") -> dict:
    """新增真实有效的特殊日期；date_type 为 anniversary 或 custom。"""
    return tools.add_special_date(name, month, day, year, date_type)


@mcp.tool()
def start_session(session_type: str | None = None,
                  special_date_id: str | None = None) -> dict:
    """邀请对方开启一轮共同拆星。必须传 special_date_id 绑定具体日期；
    session 类型默认由服务器按该日期的数据派生（anniversary / special_day），
    只在显式传 session_type 时校验一致性。anniversary 类特殊日只能在当天发起，双方额度按当天 0 点（业务时区）
    冻结：0 点后新写的星不占额度、也不进本轮候选。双方确认前不打开任何星。"""
    return tools.start_session(session_type, special_date_id)


@mcp.tool()
def current_session() -> dict:
    """查看当前尚未结束的拆星 session 概况。"""
    return tools.current_session()


@mcp.tool()
def confirm_session(session_id: str) -> dict:
    """确认对方发起的拆星 session。纪念日额度已按当天 0 点冻结，
    确认只激活本轮，不按确认时的数量重算。"""
    return tools.confirm_session(session_id)


@mcp.tool()
def take_session_star(session_id: str) -> dict:
    """看一颗对方瓶子里本轮可交换的星。纪念日轮次：额度 = 纪念日 0 点时
    自己瓶里的星数；0 点后新写的星不进候选。自己的额度用完会提示
    "不可以哦，你的瓶子里面没有星星可以交换了，下次多写点吧。"，
    对方本轮没有候选会提示"对方瓶子里没有本轮可以交换的星星了。"。
    返回值已是完整详情，直接展示即可，不要再调 shared_star 重复计足迹。"""
    return tools.take_session_star(session_id)


@mcp.tool()
def finish_session(session_id: str) -> dict:
    """结束 active session；未拆完的星退回各自私人瓶。"""
    return tools.finish_session(session_id)


@mcp.tool()
def cancel_session(session_id: str) -> dict:
    """撤回自己发起、仍在等待确认的拆星邀请；谁的瓶子都不会变化。"""
    return tools.cancel_session(session_id)


@mcp.tool()
def decline_session(session_id: str) -> dict:
    """拒绝对方发起、仍在等待确认的拆星邀请；不必先同意。"""
    return tools.decline_session(session_id)


@mcp.tool()
def sessions() -> dict:
    """查看历史拆星批次（含绑定的特殊日），可配合 shared_stars 的 session_id 筛选。"""
    return tools.sessions()


def _transport_security():
    """DNS 重绑定保护显式放行公网域名：默认只认 localhost，而经 Cloudflare
    隧道进来的请求 Host 头是公网域名，会被直接拒成 "Invalid Host header"。"""
    import os
    from urllib.parse import urlsplit

    from mcp.server.transport_security import TransportSecuritySettings

    origin = os.getenv("SUPERPOSITION_PUBLIC_ORIGIN",
                       "https://wishstar.example.com").rstrip("/")
    host = urlsplit(origin).hostname or "wishstar.example.com"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        # SDK 按整串 Host 匹配（含端口），本地测试/自环地址需要通配端口
        allowed_hosts=[host, f"{host}:*", "127.0.0.1", "127.0.0.1:*",
                       "localhost", "localhost:*"],
        allowed_origins=[origin, "https://claude.ai"],
    )


def streamable_http_starlette_app(streamable_http_path: str = "/mcp"):
    """供 app.main 挂载的 Streamable HTTP MCP 应用（手机/远程 claude.ai 连接器用）。

    stateless + JSON 响应：每个请求独立应答，最适合经 Cloudflare 隧道转发，
    不依赖会话亲和。身份仍进程绑定为 companion——OAuth 登录页就是他的通行证。
    """
    return mcp.streamable_http_app(
        streamable_http_path=streamable_http_path,
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(),
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
