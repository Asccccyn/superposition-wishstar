"""通用小工具：ID 生成与业务时间戳。

第二轮修订：所有业务时间统一走业务时区（config.BUSINESS_TIMEZONE，
默认 Asia/Shanghai），不依赖 Windows 当前系统时区 / VPN / 服务器迁移。
数据库里存的是无 offset 的业务时区本地时间串，与既有历史数据口径一致；
"纪念日当天 00:00" 的 cutoff 也按业务时区计算。
"""
import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .config import BUSINESS_TIMEZONE

TZ = ZoneInfo(BUSINESS_TIMEZONE)

# 测试注入口：置为业务时区的 aware datetime 后，business_now/now_iso 都以它为准；
# 置回 None 恢复真实时钟。生产代码不得设置。
_clock_override: datetime | None = None


def set_business_clock(when: datetime | None) -> None:
    """仅供测试注入时钟；传入 None 恢复真实时间。"""
    global _clock_override
    _clock_override = when


def business_now() -> datetime:
    """业务时区的当前 aware 时间。"""
    if _clock_override is not None:
        return _clock_override
    return datetime.now(TZ)


def business_today() -> date:
    return business_now().date()


def now_iso() -> str:
    """无 offset 的业务时区时间串（与库内历史数据口径一致）。"""
    return business_now().replace(tzinfo=None).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def anniversary_cutoff(target_date: date) -> str:
    """某自然日在业务时区的 00:00，作为纪念日交换额度的冻结时刻。"""
    return datetime.combine(target_date, time.min).replace(tzinfo=None).isoformat(timespec="seconds")
