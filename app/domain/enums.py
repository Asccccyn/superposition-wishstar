"""领域枚举：所有状态取值集中在这里，供仓储层与数据库 CHECK 约束共用。"""
from enum import Enum


class Visibility(str, Enum):
    VISIBLE = "visible"    # 写入即进入“我们的瓶子”（§5）
    HIDDEN = "hidden"      # 先留在作者私人瓶子（§6）


class StarState(str, Enum):
    """星星状态机（§29）。REQUEST_PENDING / REQUEST_APPROVED 等请求侧状态
    记录在 star_requests.status 上：请求在批准前不绑定具体星（随机在批准时发生），
    因此候选星在批准前保持 SEALED，仍可被作者编辑 / 递出 / 进入纪念日。"""
    CREATED = "CREATED"
    SHARED = "SHARED"
    SEALED = "SEALED"
    ALLOCATED_RANDOMLY = "ALLOCATED_RANDOMLY"
    OFFERED = "OFFERED"
    DELIVERED = "DELIVERED"
    SESSION_LOCKED = "SESSION_LOCKED"
    OPENED = "OPENED"


class OpenMode(str, Enum):
    VISIBLE_FROM_START = "visible_from_start"
    REQUEST_RANDOM = "request_random"
    AUTHOR_OFFER = "author_offer"
    ANNIVERSARY = "anniversary"
    SPECIAL_DAY = "special_day"


class SharedOrigin(str, Enum):
    """在我们的瓶子里区分来源（§17）。"""
    VISIBLE_FROM_START = "visible_from_start"
    REVEALED_BY_REQUEST = "revealed_by_request"
    REVEALED_BY_OFFER = "revealed_by_offer"
    REVEALED_BY_ANNIVERSARY = "revealed_by_anniversary"
    REVEALED_BY_SPECIAL_DAY = "revealed_by_special_day"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class OfferStatus(str, Enum):
    OFFERED = "offered"
    DELIVERED = "delivered"   # 接住瞬间经过，随同一事务到达最终状态
    OPENED = "opened"
    COMPLETED = "completed"


class SessionStatus(str, Enum):
    WAITING_CONFIRMATION = "waiting_confirmation"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SessionType(str, Enum):
    ANNIVERSARY = "anniversary"
    SPECIAL_DAY = "special_day"


class ResponseType(str, Enum):
    TEXT = "text"
    AUDIO = "audio"


class EventType(str, Enum):
    """审计事件（§54）。审计只记事件与 ID，绝不复制正文（§53）。"""
    STAR_CREATED_VISIBLE = "STAR_CREATED_VISIBLE"
    STAR_CREATED_HIDDEN = "STAR_CREATED_HIDDEN"
    STAR_EDITED = "STAR_EDITED"
    STAR_DELETED = "STAR_DELETED"
    STAR_REQUEST_CREATED = "STAR_REQUEST_CREATED"
    STAR_REQUEST_APPROVED = "STAR_REQUEST_APPROVED"
    STAR_REQUEST_REJECTED = "STAR_REQUEST_REJECTED"
    STAR_RANDOM_ALLOCATED = "STAR_RANDOM_ALLOCATED"
    STAR_OFFERED = "STAR_OFFERED"
    STAR_ACCEPTED = "STAR_ACCEPTED"
    STAR_OPENED = "STAR_OPENED"
    STAR_MOVED_TO_SHARED = "STAR_MOVED_TO_SHARED"
    STAR_RESPONSE_ADDED = "STAR_RESPONSE_ADDED"
    SPECIAL_SESSION_STARTED = "SPECIAL_SESSION_STARTED"
    SPECIAL_SESSION_CONFIRMED = "SPECIAL_SESSION_CONFIRMED"
    SPECIAL_SESSION_COMPLETED = "SPECIAL_SESSION_COMPLETED"
    SPECIAL_SESSION_CANCELLED = "SPECIAL_SESSION_CANCELLED"    # 发起方在确认前撤回邀请
    SPECIAL_SESSION_DECLINED = "SPECIAL_SESSION_DECLINED"      # 被邀请方在确认前拒绝


class NotificationType(str, Enum):
    NEW_VISIBLE_STAR = "NEW_VISIBLE_STAR"        # §38.1
    HIDDEN_STAR_ADDED = "HIDDEN_STAR_ADDED"      # §38.2 只提示数量变化
    STAR_REQUEST_RECEIVED = "STAR_REQUEST_RECEIVED"
    STAR_REQUEST_GIVEN = "STAR_REQUEST_GIVEN"
    STAR_REQUEST_DECLINED = "STAR_REQUEST_DECLINED"
    STAR_OFFERED = "STAR_OFFERED"
    STAR_RESPONSE_ADDED = "STAR_RESPONSE_ADDED"
    SESSION_INVITE = "SESSION_INVITE"
    SESSION_CONFIRMED = "SESSION_CONFIRMED"
    SESSION_CANCELLED = "SESSION_CANCELLED"    # 发起方撤回了拆星邀请
    SESSION_DECLINED = "SESSION_DECLINED"      # 被邀请方这次先不想一起拆
