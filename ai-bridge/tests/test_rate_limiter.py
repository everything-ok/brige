"""
测试速率限制器和负载均衡器
"""
import sys
import time
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestRateLimiter:

    def _make_limiter(self, mock_config):
        from limiter.rate import RateLimiter
        return RateLimiter(mock_config)

    @pytest.mark.asyncio
    async def test_first_request_allowed_immediately(self, mock_config):
        limiter = self._make_limiter(mock_config)
        wait = await limiter.acquire("poe")
        assert wait == 0, "第一次请求应该立即允许"

    @pytest.mark.asyncio
    async def test_per_minute_limit_enforced(self, mock_config):
        from config import RateLimitConfig
        mock_config.global_rate_limit = RateLimitConfig(
            requests_per_minute=2,
            requests_per_hour=100,
            requests_per_day=1000,
            min_interval_seconds=0,
            max_interval_seconds=0,
        )
        mock_config.get_site = lambda name: None  # 使用全局限制

        limiter = self._make_limiter(mock_config)

        # 记录 2 次请求
        await limiter.record_request("testsite")
        await limiter.record_request("testsite")

        # 第 3 次应该被限流
        wait = await limiter.acquire("testsite")
        assert wait > 0, "超过每分钟限制后应该返回等待时间"

    @pytest.mark.asyncio
    async def test_daily_limit_returns_minus_one(self, mock_config):
        from config import RateLimitConfig
        mock_config.global_rate_limit = RateLimitConfig(
            requests_per_minute=1000,
            requests_per_hour=1000,
            requests_per_day=2,
            min_interval_seconds=0,
            max_interval_seconds=0,
        )
        mock_config.get_site = lambda name: None

        limiter = self._make_limiter(mock_config)
        await limiter.record_request("testsite")
        await limiter.record_request("testsite")

        result = await limiter.acquire("testsite")
        assert result == -1, "达到每日限制应返回 -1"

    @pytest.mark.asyncio
    async def test_cooldown_blocks_requests(self, mock_config):
        limiter = self._make_limiter(mock_config)

        # 触发冷却
        await limiter.record_failure("poe", trigger_cooldown=True)

        wait = await limiter.acquire("poe")
        assert wait > 0, "冷却期间应该返回等待时间"

    @pytest.mark.asyncio
    async def test_cooldown_doubles_on_repeated_failure(self, mock_config):
        limiter = self._make_limiter(mock_config)

        await limiter.record_failure("poe", trigger_cooldown=True)
        stats1 = limiter.site_stats["poe"].cooldown_seconds

        # 重置冷却时间以便再次触发
        limiter.site_stats["poe"].cooldown_until = None

        await limiter.record_failure("poe", trigger_cooldown=True)
        stats2 = limiter.site_stats["poe"].cooldown_seconds

        assert stats2 == stats1 * mock_config.cooldown.multiplier

    @pytest.mark.asyncio
    async def test_cooldown_capped_at_max(self, mock_config):
        from config import CooldownConfig
        mock_config.cooldown = CooldownConfig(
            initial_seconds=30,
            max_seconds=60,
            multiplier=10.0,
            reset_after_success=3,
        )
        limiter = self._make_limiter(mock_config)

        # 多次失败
        for _ in range(5):
            limiter.site_stats.pop("poe", None)  # 重置以便重新触发
            await limiter.record_failure("poe", trigger_cooldown=True)

        # 冷却时间不应超过 max_seconds
        assert limiter.site_stats["poe"].cooldown_seconds <= 60

    @pytest.mark.asyncio
    async def test_success_resets_cooldown_after_threshold(self, mock_config):
        limiter = self._make_limiter(mock_config)

        # 先触发冷却
        await limiter.record_failure("poe", trigger_cooldown=True)
        assert limiter.site_stats["poe"].cooldown_until is not None

        # 连续成功 reset_after_success 次
        for _ in range(mock_config.cooldown.reset_after_success):
            await limiter.record_success("poe")

        assert limiter.site_stats["poe"].cooldown_until is None
        assert limiter.site_stats["poe"].cooldown_seconds == 0

    @pytest.mark.asyncio
    async def test_record_request_increments_counters(self, mock_config):
        limiter = self._make_limiter(mock_config)
        await limiter.record_request("poe")
        await limiter.record_request("poe")

        stats = limiter.site_stats["poe"]
        assert len(stats.requests_minute) == 2
        assert len(stats.requests_hour) == 2
        assert len(stats.requests_day) == 2

    @pytest.mark.asyncio
    async def test_get_status_returns_all_sites(self, mock_config):
        limiter = self._make_limiter(mock_config)
        await limiter.record_request("poe")
        await limiter.record_request("perplexity")

        status = limiter.get_status()
        assert "poe" in status
        assert "perplexity" in status

    @pytest.mark.asyncio
    async def test_get_status_fields(self, mock_config):
        limiter = self._make_limiter(mock_config)
        await limiter.record_request("poe")

        status = limiter.get_status()
        poe_status = status["poe"]

        required_fields = [
            "requests_last_minute", "requests_last_hour", "requests_today",
            "limit_per_minute", "limit_per_hour", "limit_per_day",
            "cooldown_remaining", "consecutive_success", "consecutive_failure",
        ]
        for field in required_fields:
            assert field in poe_status, f"缺少字段: {field}"

    @pytest.mark.asyncio
    async def test_site_specific_limit_overrides_global(self, mock_config):
        from config import RateLimitConfig
        # poe 有自定义限制 requests_per_hour=40
        limiter = self._make_limiter(mock_config)
        limit = limiter._get_limit("poe")
        assert limit.requests_per_hour == 40

    @pytest.mark.asyncio
    async def test_unknown_site_uses_global_limit(self, mock_config):
        limiter = self._make_limiter(mock_config)
        limit = limiter._get_limit("unknown_site")
        assert limit == mock_config.global_rate_limit

    @pytest.mark.asyncio
    async def test_count_recent_filters_old_timestamps(self, mock_config):
        from collections import deque
        limiter = self._make_limiter(mock_config)
        stats = limiter._get_stats("poe")

        now = time.time()
        # 添加一个旧时间戳（2分钟前）和一个新时间戳
        stats.requests_minute.append(now - 120)
        stats.requests_minute.append(now - 10)

        count = limiter._count_recent(stats.requests_minute, 60)
        assert count == 1, "只有最近60秒内的请求应该被计数"

    @pytest.mark.asyncio
    async def test_min_interval_enforced(self, mock_config):
        from config import RateLimitConfig
        mock_config.global_rate_limit = RateLimitConfig(
            requests_per_minute=100,
            requests_per_hour=1000,
            requests_per_day=10000,
            min_interval_seconds=30,
            max_interval_seconds=30,
        )
        mock_config.get_site = lambda name: None

        limiter = self._make_limiter(mock_config)
        await limiter.record_request("testsite")

        wait = await limiter.acquire("testsite")
        assert wait > 0, "最小间隔未到应该等待"


class TestLoadBalancer:

    def _make_balancer(self, mock_config):
        from limiter.rate import RateLimiter
        from limiter.balancer import LoadBalancer
        limiter = RateLimiter(mock_config)
        return LoadBalancer(mock_config, limiter), limiter

    @pytest.mark.asyncio
    async def test_select_site_returns_valid_site(self, mock_config):
        balancer, _ = self._make_balancer(mock_config)
        site = await balancer.select_site("gpt-4o")
        assert site == "poe", "gpt-4o 只在 poe 上，应该选择 poe"

    @pytest.mark.asyncio
    async def test_select_preferred_site(self, mock_config):
        balancer, _ = self._make_balancer(mock_config)
        site = await balancer.select_site("pplx-online", preferred_site="perplexity")
        assert site == "perplexity"

    @pytest.mark.asyncio
    async def test_select_site_unknown_model_returns_none(self, mock_config):
        balancer, _ = self._make_balancer(mock_config)
        # 没有任何站点支持这个模型
        site = await balancer.select_site("nonexistent-model-xyz")
        assert site is None

    @pytest.mark.asyncio
    async def test_get_available_models(self, mock_config):
        balancer, _ = self._make_balancer(mock_config)
        models = balancer.get_available_models()
        assert isinstance(models, list)
        assert "gpt-4o" in models
        assert "claude-3-5-sonnet" in models
        assert "pplx-online" in models
        # 应该是排序的
        assert models == sorted(models)

    @pytest.mark.asyncio
    async def test_select_site_with_all_sites_in_cooldown(self, mock_config):
        from config import RateLimitConfig
        # 设置每日限制为 0，让所有站点都不可用
        mock_config.global_rate_limit = RateLimitConfig(
            requests_per_minute=0,
            requests_per_hour=0,
            requests_per_day=0,
            min_interval_seconds=0,
            max_interval_seconds=0,
        )
        # 覆盖站点的 rate_limit
        for site in mock_config.sites:
            site.rate_limit = None

        balancer, _ = self._make_balancer(mock_config)
        site = await balancer.select_site("gpt-4o")
        # 所有站点都达到每日限制，应该返回 None
        assert site is None
