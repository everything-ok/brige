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

    def _get_stats(self, site_name: str) -> SiteStats:
        if site_name not in self.site_stats:
            self.site_stats[site_name] = SiteStats(name=site_name)
        return self.site_stats[site_name]

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

    async def acquire(self, site_name: str) -> float:
        """
        获取请求许可，返回需要等待的秒数
        返回 0 表示可以立即请求
        返回 -1 表示达到每日限制，无法请求
        """
        async with self._lock:
            stats = self._get_stats(site_name)
            limit = self._get_limit(site_name)
            now = time.time()

            if stats.cooldown_until and now < stats.cooldown_until:
                wait = stats.cooldown_until - now
                console.print(f"[yellow]⏳ {site_name} 冷却中，还需 {wait:.0f}s[/yellow]")
                return wait

            day_count = self._count_recent(stats.requests_day, 86400)
            if day_count >= limit.requests_per_day:
                console.print(
                    f"[red]🚫 {site_name} 达到每日限制 ({day_count}/{limit.requests_per_day})[/red]"
                )
                return -1

            hour_count = self._count_recent(stats.requests_hour, 3600)
            if hour_count >= limit.requests_per_hour:
                wait = 3600 - (now - min(stats.requests_hour))
                console.print(f"[yellow]⏳ {site_name} 达到每小时限制，等待 {wait:.0f}s[/yellow]")
                return min(wait, 600)

            minute_count = self._count_recent(stats.requests_minute, 60)
            if minute_count >= limit.requests_per_minute:
                wait = 60 - (now - min(stats.requests_minute))
                console.print(f"[yellow]⏳ {site_name} 达到每分钟限制，等待 {wait:.0f}s[/yellow]")
                return wait

            if stats.last_request:
                elapsed = now - stats.last_request
                if elapsed < limit.min_interval_seconds:
                    wait = limit.min_interval_seconds - elapsed
                    wait += (limit.max_interval_seconds - limit.min_interval_seconds) * (time.time() % 1)
                    return wait

            return 0

    async def record_request(self, site_name: str):
        """记录一次请求"""
        async with self._lock:
            stats = self._get_stats(site_name)
            now = time.time()
            stats.requests_minute.append(now)
            stats.requests_hour.append(now)
            stats.requests_day.append(now)
            stats.last_request = now

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
