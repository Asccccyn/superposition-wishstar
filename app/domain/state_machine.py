"""星星状态机（架构文档 §29）。所有状态变更必须经 require_transition 校验。"""
from .enums import StarState
from .errors import InvalidStateError

TRANSITIONS: dict[StarState, set] = {
    # 可见星：写入瞬间定型（§29.1）
    StarState.CREATED: {StarState.SHARED, StarState.SEALED},
    # 隐藏星的合法出瓶起点（§8 / §29.2~29.4）
    StarState.SEALED: {
        StarState.ALLOCATED_RANDOMLY,   # 请求批准后随机分配
        StarState.OFFERED,              # 作者主动递出
        StarState.SESSION_LOCKED,       # 旧版纪念日快照锁定（兼容历史数据，新逻辑不再产生）
        StarState.OPENED,               # 纪念日配额互看：直接从瓶子里看开（不预锁定）
    },
    StarState.ALLOCATED_RANDOMLY: {StarState.OPENED},
    StarState.OFFERED: {StarState.DELIVERED},
    StarState.DELIVERED: {StarState.OPENED},
    StarState.SESSION_LOCKED: {
        StarState.OPENED,       # 本轮拆开
        StarState.SEALED,       # session 结束时未拆的星解锁退回私人瓶子（§28：不会过期）
    },
    StarState.OPENED: {StarState.SHARED},
    # 星星不会因为被看过而消失（§16 / §58 第十）
    StarState.SHARED: set(),
}


def can_transition(frm: StarState, to: StarState) -> bool:
    return to in TRANSITIONS.get(frm, set())


def require_transition(frm: StarState, to: StarState) -> None:
    if not can_transition(frm, to):
        raise InvalidStateError(f"星星状态不允许从 {frm.value} 变为 {to.value}")
