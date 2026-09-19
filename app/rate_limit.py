"""登录失败阶梯锁定（常见商业产品的形态）。

规则（默认，可用环境变量 SUPERPOSITION_LOGIN_LOCKOUT 覆盖用于测试）：
  * 连续输错不足 5 次：正常拒绝，仅固定 0.5s 小延迟钝化在线猜测；
  * 第 5 次错起：锁定 60 秒——锁定期间**连正确密码也拒绝**；
  * 第 10 次错起：锁定 300 秒（5 分钟）；
  * 第 15 次及以上：锁定 900 秒（15 分钟，封顶）；
  * 登录成功立即清零；锁定到期后计数也归零（重新给满 5 次机会）。

来源按 CF-Connecting-IP / 客户端 IP 区分；网页登录与远程 MCP 的
OAuth 登录页共用。进程内存态，重启即清零。
"""
from __future__ import annotations

import os
import threading
import time

DEFAULT_CONFIG = "5:60:300:900"   # 阈值:一级锁:二级锁:封顶锁


def _load_config() -> tuple[int, list[int]]:
    raw = os.getenv("SUPERPOSITION_LOGIN_LOCKOUT", DEFAULT_CONFIG)
    parts = [int(x) for x in raw.split(":")]
    if len(parts) != 4 or parts[0] < 1 or any(p <= 0 for p in parts[1:]):
        parts = [int(x) for x in DEFAULT_CONFIG.split(":")]
    return parts[0], parts[1:]


class LoginThrottle:
    def __init__(self, threshold: int | None = None, tiers: list[int] | None = None):
        cfg_threshold, cfg_tiers = _load_config()
        self.threshold = threshold or cfg_threshold
        self.tiers = tiers or cfg_tiers
        self.fail_delay = 0.5          # 未达阈值时每次错密码的固定小延迟
        self._lock = threading.Lock()
        self._state: dict[str, dict] = {}

    def locked_for(self, key: str) -> int:
        """该来源当前剩余锁定秒数（0 = 未锁定）。"""
        with self._lock:
            st = self._state.get(key)
            if not st:
                return 0
            remaining = st["lock_until"] - time.time()
            if remaining <= 0:
                if st["failures"] >= self.threshold:
                    # 锁定到期：计数归零，重新给满机会
                    self._state.pop(key, None)
                return 0
            return int(remaining) + 1

    def record_failure(self, key: str) -> tuple[bool, int]:
        """记一次失败。返回 (是否触发锁定, 锁定秒数)；未触发锁定时秒数为 0。"""
        with self._lock:
            now = time.time()
            st = self._state.get(key)
            if not st:
                st = {"failures": 0, "lock_until": 0.0}
                self._state[key] = st
            st["failures"] += 1
            n = st["failures"]
            if n >= self.threshold:
                seconds = self.tiers[min((n - self.threshold) // 5, len(self.tiers) - 1)]
                st["lock_until"] = now + seconds
                return True, seconds
            return False, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


LOGIN_THROTTLE = LoginThrottle()


def client_key(request) -> str:
    """隧道后面真实来源在 CF-Connecting-IP；直连时退回客户端地址。"""
    ip = request.headers.get("cf-connecting-ip") or \
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if ip:
        return ip
    client = request.client
    return client.host if client else "unknown"
