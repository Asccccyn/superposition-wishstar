"""Step1 验证：领域层（枚举 / 异常 / 状态机）。运行：python tests/step1_domain_test.py"""
from _util import *  # noqa: F401,F403  先插入项目根路径
from app.domain.enums import StarState, Visibility
from app.domain.errors import InvalidStateError
from app.domain.state_machine import can_transition, require_transition

# §29.1 可见星：CREATED -> SHARED
assert can_transition(StarState.CREATED, StarState.SHARED)

# §29.2 隐藏星·普通请求：SEALED -> ALLOCATED_RANDOMLY -> OPENED -> SHARED
for frm, to in [
    (StarState.SEALED, StarState.ALLOCATED_RANDOMLY),
    (StarState.ALLOCATED_RANDOMLY, StarState.OPENED),
    (StarState.OPENED, StarState.SHARED),
]:
    assert can_transition(frm, to), f"{frm} -> {to} 应当合法"

# §29.3 隐藏星·主动递：SEALED -> OFFERED -> DELIVERED -> OPENED -> SHARED
for frm, to in [
    (StarState.SEALED, StarState.OFFERED),
    (StarState.OFFERED, StarState.DELIVERED),
    (StarState.DELIVERED, StarState.OPENED),
]:
    assert can_transition(frm, to), f"{frm} -> {to} 应当合法"

# §29.4 纪念日：SEALED -> SESSION_LOCKED -> OPENED -> SHARED
for frm, to in [
    (StarState.SEALED, StarState.SESSION_LOCKED),
    (StarState.SESSION_LOCKED, StarState.OPENED),
]:
    assert can_transition(frm, to), f"{frm} -> {to} 应当合法"

# 非法迁移必须被拒绝
for frm, to in [
    (StarState.SEALED, StarState.SHARED),        # 隐藏星不能直接跳进我们的瓶子
    (StarState.SHARED, StarState.SEALED),        # 打开后不回私人瓶子（§16）
    (StarState.OFFERED, StarState.OPENED),       # 递出的星必须经过“接住”
    (StarState.CREATED, StarState.OPENED),
]:
    try:
        require_transition(frm, to)
        raise AssertionError(f"{frm} -> {to} 应当被拒绝")
    except InvalidStateError:
        pass

# 可见性只有两种取值
assert {v.value for v in Visibility} == {"visible", "hidden"}

print("STEP 1 PASS —— 领域层状态机与枚举全部符合架构文档 §29")
