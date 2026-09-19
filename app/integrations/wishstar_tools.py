"""Thin tool adapter for AI/MCP clients.

The adapter is deliberately actor-bound at construction time.  Tool callers never
get to provide or override an actor id, so the service-layer permission model stays
the single source of truth.
"""
from __future__ import annotations

from ..services.star_service import StarService


class WishStarTools:
    def __init__(self, service: StarService, actor_id: str):
        self.service = service
        self.actor_id = actor_id

    def who_am_i(self) -> dict:
        return self.service.get_identity(self.actor_id)

    def bottle_counts(self) -> dict:
        return self.service.count_bottles(self.actor_id)

    def write_star(self, content: str, visibility: str,
                   mood_type: str | None = None, mood_text: str | None = None,
                   note: str | None = None) -> dict:
        return self.service.create_star(
            self.actor_id, content, visibility,
            mood_type=mood_type, mood_text=mood_text, note=note,
        )

    def my_hidden_stars(self) -> dict:
        return self.service.list_my_hidden_stars(self.actor_id)

    def my_hidden_star(self, star_id: str) -> dict:
        """私人星详情：显式查看计 1 次足迹并初始化 first_view；不改状态。"""
        return self.service.get_my_hidden_star(self.actor_id, star_id)

    def edit_star(self, star_id: str, content: str | None = None,
                  mood_type: str | None = None, mood_text: str | None = None,
                  note: str | None = None) -> dict:
        """作者本人可编辑自己的星（封存中/公共池均可）；产品不提供删除。"""
        return self.service.edit_star(
            self.actor_id, star_id, content=content,
            mood_type=mood_type, mood_text=mood_text, note=note,
        )

    def request_star(self) -> dict:
        return self.service.request_hidden_star(self.actor_id)

    def requests(self) -> dict:
        return self.service.list_requests(self.actor_id)

    def respond_to_request(self, request_id: str, decision: str,
                           star_id: str | None = None) -> dict:
        """decision=give 时可用 star_id 指定给哪一颗；缺省由服务端随机。"""
        return self.service.respond_hidden_request(
            self.actor_id, request_id, decision, star_id=star_id
        )

    def open_requested_star(self, request_id: str) -> dict:
        return self.service.open_allocated_star(self.actor_id, request_id)

    def offer_star(self, star_id: str, message: str | None = None) -> dict:
        return self.service.offer_hidden_star(self.actor_id, star_id, message=message)

    def offers(self) -> dict:
        return self.service.list_offers(self.actor_id)

    def accept_offered_star(self, offer_id: str) -> dict:
        return self.service.accept_offered_star(self.actor_id, offer_id)

    def shared_stars(self, author_id: str | None = None,
                     written_date: str | None = None,
                     opened_date: str | None = None,
                     shared_origin: str | None = None,
                     session_id: str | None = None) -> dict:
        return self.service.list_shared_stars(
            self.actor_id,
            author_id=author_id,
            written_date=written_date,
            opened_date=opened_date,
            shared_origin=shared_origin,
            session_id=session_id,
        )

    def shared_star(self, star_id: str) -> dict:
        return self.service.get_shared_star(self.actor_id, star_id)

    def respond_to_star(self, star_id: str, text: str) -> dict:
        return self.service.respond_to_star(
            self.actor_id, star_id, "text", text=text
        )

    def notifications(self, unread_only: bool = False, limit: int = 50) -> dict:
        return self.service.list_notifications(
            self.actor_id, unread_only=unread_only, limit=limit
        )

    def mark_notification_read(self, notification_id: str) -> dict:
        return self.service.mark_notification_read(self.actor_id, notification_id)

    def special_dates(self) -> dict:
        return self.service.list_special_dates(self.actor_id)

    def add_special_date(self, name: str, month: int, day: int,
                         year: int | None = None,
                         date_type: str = "custom") -> dict:
        return self.service.add_special_date(
            self.actor_id, name, month, day, year=year, date_type=date_type
        )

    def start_session(self, session_type: str | None = None,
                      special_date_id: str | None = None) -> dict:
        """必须绑定具体 special_date_id；类型默认由服务器按该日期的数据派生，
        只有显式传 session_type 时才做一致性校验（缺省不得偷偷补 anniversary）。"""
        return self.service.start_special_session(
            self.actor_id, session_type=session_type,
            special_date_id=special_date_id,
        )

    def current_session(self) -> dict:
        return self.service.get_current_session(self.actor_id)

    def confirm_session(self, session_id: str) -> dict:
        return self.service.confirm_special_session(self.actor_id, session_id)

    def take_session_star(self, session_id: str) -> dict:
        return self.service.take_next_session_star(self.actor_id, session_id)

    def finish_session(self, session_id: str) -> dict:
        return self.service.finish_special_session(self.actor_id, session_id)

    def cancel_session(self, session_id: str) -> dict:
        """发起方在对方确认前撤回邀请（F05）。"""
        return self.service.cancel_special_session(self.actor_id, session_id)

    def decline_session(self, session_id: str) -> dict:
        """被邀请方在确认前拒绝邀请，不必先同意（F05）。"""
        return self.service.decline_special_session(self.actor_id, session_id)

    def sessions(self) -> dict:
        """历史拆星批次列表，用于按批次回看共同瓶（F11）。"""
        return self.service.list_sessions(self.actor_id)
