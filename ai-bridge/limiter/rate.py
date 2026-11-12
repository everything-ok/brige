import asyncio
import time
from collections import deque
from typing import Dict, Optional
from dataclasses import dataclass, field
from rich.console import Console

console = Console()


@dataclass
class SiteStats:
    """站点统计"""
    name: str
    requests_minute: deque = field(default_factory=lambda: deque(maxlen=100))
    requests_hour: deque = field(default_factory=lambda: deque(maxlen=1000))
    requests_day: deque = field(default_factory=lambda: deque(maxlen=10000))
    last_request: Optional[float] = None
    consecutive_success: int = 0
    consecutive_failure: int = 0
    cooldown_until: Optional[float] = None
    cooldown_seconds: float = 0


class RateLimiter:
    """智能速率限制器"""

    def __init__(self, config):
        self.config = config
        self.global_limit = config.global_rate_limit
        self.site_stats: Dict[str, SiteStats] = {}
        self._lock = asyncio.Lock()
        # 已"预定"但还没真正发出的请求。
        # acquire() 只检查不占用时，并发请求会同时通过检查 → 实际请求数
        # 超过限额（TOCTOU 竞态）。用预留把检查与占用合并成一步。
        #
        # 预留必须按"请求"而不是按"站点"记账：否则并发下 A 请求失败时的
        # release 会把 B 请求还预留在途的槽位一起释放掉，导致实际请求数
        # 超限。因此每个预留带一个唯一 token：
        #   - record_request(token)   消费自己的预留
        #   - release_reservation(token) 归还自己的预留
        # 未提供 token 时才退回"按站点释放任意一个"的兼容行为。
        self._reserved: Dict[str, set] = {}
        self._token_seq = 0

    def _get_stats(self, site_name: str) -> SiteStats:
        if site_name not in self.site_stats:
            self.site_stats[site_name] = SiteStats(name=site_name)
        return self.site_stats[site_name]

    def _reserved_count(self, site_name: str) -> int:
        return len(self._reserved.get(site_name, ()))

    def _get_limit(self, site_name: str):
        """获取站点限制（优先站点独立配置，否则用全局）"""
        site = self.config.get_site(site_name)
        if site and site.rate_limit:
            return site.rate_limit
        return self.global_limit

    def _count_recent(self, timestamps: deque, seconds: int) -> int:
        """统计最近 N 秒内的请求数"""
        cutoff = time.time() - seconds
        return sum(1 for ts in timestamps if ts > cutoff)

    async def acquire(self, site_name: str, reserve: bool = False) -> float:
        """
        获取请求许可，返回需要等待的秒数
        返回 0 表示可以立即请求
        返回 -1 表示达到每日限制，无法请求

        reserve=True 时会**占用**一个配额（配合 release_reservation /
        record_request 使用），把「检查」和「占用」合并成原子操作。
        否则并发请求会同时通过检查 → 实际请求数超过限额（TOCTOU 竞态）。

        注意：reserve=True 是"按站点"记账的兼容模式 —— 并发下某个请求失败
        时的 release 可能误释放另一个并发请求的槽位。需要精确配对时，
        请使用 acquire_with_token()。
        """
        # acquire_with_token 返回 (token, wait)，这里只需要 wait
        _, wait = await self.acquire_with_token(site_name, reserve=reserve)
        return wait

    async def acquire_with_token(self, site_name: str, reserve: bool = False):
        """
        同 acquire()，但额外返回一个预留 token。

        返回 (token, wait)：
          - token: str，预留成功时的唯一标识；未预留或未拿到配额时为 None
          - wait:  需要等待的秒数（0 = 可立即请求，-1 = 达到每日限制）

        调用方必须把 token 传给 record_request(token) 或
        release_reservation(token)，保证"谁预留谁消费/归还"，
        避免并发下互相偷走配额槽位。
        """
        async with self._lock:
            stats = self._get_stats(site_name)
            limit = self._get_limit(site_name)
            now = time.time()
            reserved = self._reserved_count(site_name)

            if stats.cooldown_until and now < stats.cooldown_until:
                wait = stats.cooldown_until - now
                console.print(f"[yellow]⏳ {site_name} 冷却中，还需 {wait:.0f}s[/yellow]")
                return None, wait

            day_count = self._count_recent(stats.requests_day, 86400) + reserved
            if day_count >= limit.requests_per_day:
                console.print(
                    f"[red]🚫 {site_name} 达到每日限制 ({day_count}/{limit.requests_per_day})[/red]"
                )
                return None, -1

            hour_count = self._count_recent(stats.requests_hour, 3600) + reserved
            if hour_count >= limit.requests_per_hour:
                wait = (3600 - (now - min(stats.requests_hour))
                        if stats.requests_hour else 60)
                console.print(f"[yellow]⏳ {site_name} 达到每小时限制，等待 {wait:.0f}s[/yellow]")
                return None, min(wait, 600)

            minute_count = self._count_recent(stats.requests_minute, 60) + reserved
            if minute_count >= limit.requests_per_minute:
                wait = (60 - (now - min(stats.requests_minute))
                        if stats.requests_minute else 60)
                console.print(f"[yellow]⏳ {site_name} 达到每分钟限制，等待 {wait:.0f}s[/yellow]")
                return None, wait

            if stats.last_request:
                elapsed = now - stats.last_request
                if elapsed < limit.min_interval_seconds:
                    wait = limit.min_interval_seconds - elapsed
                    wait += (limit.max_interval_seconds - limit.min_interval_seconds) * (time.time() % 1)
                    return None, wait

            token = None
            if reserve:
                self._token_seq += 1
                token = f"{site_name}#{self._token_seq}"
                self._reserved.setdefault(site_name, set()).add(token)
            return token, 0

    async def release_reservation(self, site_name: str, token: Optional[str] = None):
        """释放一个预留配额（请求失败 / 根本没发出时调用）

        传入 token 时只归还该 token 自己的预留（精确配对）；
        不传 token 时退回"按站点归还任意一个"的兼容行为。
        """
        async with self._lock:
            bucket = self._reserved.get(site_name)
            if not bucket:
                return
            if token is not None:
                bucket.discard(token)
            elif bucket:
                bucket.pop()   # 兼容模式：取任意一个（set 无序，语义上是"一个"）

    async def record_request(self, site_name: str, token: Optional[str] = None):
        """记录一次请求（并消费掉对应的预留配额）"""
        async with self._lock:
            stats = self._get_stats(site_name)
            now = time.time()
            stats.requests_minute.append(now)
            stats.requests_hour.append(now)
            stats.requests_day.append(now)
            stats.last_request = now
            bucket = self._reserved.get(site_name)
            if bucket:
                if token is not None:
                    bucket.discard(token)
                elif bucket:
                    bucket.pop()   # 兼容模式

    async def record_success(self, site_name: str):
        """记录成功"""
        async with self._lock:
            stats = self._get_stats(site_name)
            stats.consecutive_success += 1
            stats.consecutive_failure = 0

            if stats.consecutive_success >= self.config.cooldown.reset_after_success:
                stats.cooldown_seconds = 0
                stats.cooldown_until = None

    def reset_site(self, site_name: str):
        """清除站点冷却与失败计数（登录成功后调用）"""
        stats = self._get_stats(site_name)
        stats.cooldown_until = None
        stats.cooldown_seconds = 0
        stats.consecutive_failure = 0

    def cooldown_remaining(self, site_name: str) -> float:
        """该站点冷却剩余秒数（0 = 未冷却）。同步方法，仅读状态。"""
        stats = self._get_stats(site_name)
        if stats.cooldown_until:
            return max(0.0, stats.cooldown_until - time.time())
        return 0.0

    async def record_failure(self, site_name: str, trigger_cooldown: bool = False):
        """记录失败"""
        async with self._lock:
            stats = self._get_stats(site_name)
            stats.consecutive_failure += 1
            stats.consecutive_success = 0

            if trigger_cooldown:
                if stats.cooldown_seconds == 0:
                    stats.cooldown_seconds = self.config.cooldown.initial_seconds
                else:
                    stats.cooldown_seconds = min(
                        stats.cooldown_seconds * self.config.cooldown.multiplier,
                        self.config.cooldown.max_seconds,
                    )
                stats.cooldown_until = time.time() + stats.cooldown_seconds
                console.print(
                    f"[yellow]⚠️  {site_name} 进入冷却 {stats.cooldown_seconds:.0f}s[/yellow]"
                )

    def get_status(self) -> Dict:
        """获取所有站点状态"""
        status = {}
        now = time.time()
        for name, stats in self.site_stats.items():
            limit = self._get_limit(name)
            status[name] = {
                "requests_last_minute": self._count_recent(stats.requests_minute, 60),
                "requests_last_hour": self._count_recent(stats.requests_hour, 3600),
                "requests_today": self._count_recent(stats.requests_day, 86400),
                "limit_per_minute": limit.requests_per_minute,
                "limit_per_hour": limit.requests_per_hour,
                "limit_per_day": limit.requests_per_day,
                "cooldown_remaining": max(0, (stats.cooldown_until or 0) - now),
                "consecutive_success": stats.consecutive_success,
                "consecutive_failure": stats.consecutive_failure,
            }
        return status
