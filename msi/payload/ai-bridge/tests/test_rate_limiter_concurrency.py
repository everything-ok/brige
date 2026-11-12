"""
限流器并发与状态回归测试。

重点验证 acquire() 与 record_request() 之间的 TOCTOU 竞争：
两个协程同时 acquire 都拿到 0（都认为可以发），随后各自 record_request，
实际发出的请求数就超过了限额 —— 这是真实存在的竞态。
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _limiter(minute=1000, hour=1000, day=1000, min_int=0, max_int=0):
    from unittest.mock import MagicMock
    from config import RateLimitConfig, CooldownConfig
    from limiter.rate import RateLimiter

    cfg = MagicMock()
    cfg.global_rate_limit = RateLimitConfig(
        requests_per_minute=minute, requests_per_hour=hour, requests_per_day=day,
        min_interval_seconds=min_int, max_interval_seconds=max_int)
    cfg.cooldown = CooldownConfig(initial_seconds=10, max_seconds=60,
                                  multiplier=2.0, reset_after_success=3)
    cfg.get_site = lambda name: None
    return RateLimiter(cfg)


@pytest.mark.asyncio
async def test_concurrent_acquire_does_not_exceed_limit():
    """并发 acquire 时，实际记录数不得超过限额

    acquire() 只做检查，record_request() 单独调用 —— 中间存在窗口。
    这里用 10 个并发任务模拟真实 IDE 并发请求。
    """
    lim = _limiter(minute=3)

    async def one():
        wait = await lim.acquire("site")
        if wait == 0:
            await lim.record_request("site")
            return 1
        return 0

    results = await asyncio.gather(*[one() for _ in range(10)])
    sent = sum(results)
    assert sent <= 3, f"并发下发出 {sent} 次，超过限额 3"


@pytest.mark.asyncio
async def test_reservation_blocks_concurrent_over_limit():
    """reserve=True 时并发请求不能突破限额（TOCTOU 修复）"""
    lim = _limiter(minute=3)

    async def one():
        wait = await lim.acquire("site", reserve=True)
        if wait == 0:
            await asyncio.sleep(0)      # 模拟真实流程里的 await
            await lim.record_request("site")
            return 1
        return 0

    results = await asyncio.gather(*[one() for _ in range(10)])
    sent = sum(results)
    assert sent <= 3, f"并发下发出 {sent} 次，超过限额 3"


@pytest.mark.asyncio
async def test_release_reservation_frees_quota():
    """预留后释放，配额必须还回去（否则会泄漏成"假性限流"）"""
    lim = _limiter(minute=1)
    assert await lim.acquire("s", reserve=True) == 0
    # 配额已被预留 → 第二个请求应被限流
    assert await lim.acquire("s", reserve=True) > 0
    await lim.release_reservation("s")
    # 释放后应恢复可用
    assert await lim.acquire("s", reserve=True) == 0


@pytest.mark.asyncio
async def test_record_request_consumes_reservation():
    """record_request 会消费掉预留，不会重复计数"""
    lim = _limiter(minute=10)
    await lim.acquire("s", reserve=True)
    await lim.record_request("s")
    assert lim._reserved_count("s") == 0
    assert len(lim.site_stats["s"].requests_minute) == 1


@pytest.mark.asyncio
async def test_release_reservation_never_goes_negative():
    """多余的释放不能把计数弄成负数"""
    lim = _limiter(minute=10)
    await lim.release_reservation("s")
    await lim.release_reservation("s")
    assert lim._reserved_count("s") == 0


@pytest.mark.asyncio
async def test_token_is_precisely_paired():
    """token 模式：record_request(token) 只消费自己的预留"""
    lim = _limiter(minute=10)
    token_a, wait_a = await lim.acquire_with_token("s", reserve=True)
    token_b, wait_b = await lim.acquire_with_token("s", reserve=True)
    assert token_a and token_b and token_a != token_b
    assert wait_a == 0 and wait_b == 0
    assert lim._reserved_count("s") == 2

    await lim.record_request("s", token_a)
    assert lim._reserved_count("s") == 1     # B 的预留还在
    await lim.record_request("s", token_b)
    assert lim._reserved_count("s") == 0


@pytest.mark.asyncio
async def test_release_with_token_does_not_steal_other_reservation():
    """关键回归：A 失败释放自己的预留，不能把 B 的槽位偷走"""
    lim = _limiter(minute=2)
    token_a, _ = await lim.acquire_with_token("s", reserve=True)
    token_b, _ = await lim.acquire_with_token("s", reserve=True)
    assert lim._reserved_count("s") == 2

    # A 请求失败 → 只归还 A 自己的
    await lim.release_reservation("s", token_a)
    assert lim._reserved_count("s") == 1, "B 的预留被偷走了"

    # C 占用释放出来的那个空槽
    token_c, wait_c = await lim.acquire_with_token("s", reserve=True)
    assert wait_c == 0
    assert lim._reserved_count("s") == 2

    # B 正常完成 → 只能消费掉 B 自己的预留，C 的必须还在
    await lim.record_request("s", token_b)
    assert lim._reserved_count("s") == 1, "C 的预留被 B 偷走了"
    assert token_c is not None


@pytest.mark.asyncio
async def test_release_unknown_token_is_noop():
    """释放一个不存在的 token 不能影响其它预留"""
    lim = _limiter(minute=10)
    token_a, _ = await lim.acquire_with_token("s", reserve=True)
    await lim.release_reservation("s", "s#999999")
    assert lim._reserved_count("s") == 1
    await lim.record_request("s", token_a)
    assert lim._reserved_count("s") == 0


@pytest.mark.asyncio
async def test_concurrent_mixed_success_failure_keeps_quota_consistent():
    """并发混合成功/失败：配额账目必须守恒，不能凭空多出或泄漏"""
    lim = _limiter(minute=100)
    results = []

    async def one(ok: bool):
        token, wait = await lim.acquire_with_token("s", reserve=True)
        if wait != 0:
            results.append("blocked")
            return
        await asyncio.sleep(0)
        if ok:
            await lim.record_request("s", token)
        else:
            await lim.release_reservation("s", token)

    await asyncio.gather(*[one(i % 2 == 0) for i in range(20)])
    assert lim._reserved_count("s") == 0, "有预留泄漏"
    assert len(lim.site_stats["s"].requests_minute) == 10


@pytest.mark.asyncio
async def test_daily_limit_blocks_after_reaching_cap():
    """达到每日限额后必须返回 -1（不可请求）"""
    lim = _limiter(day=2)
    await lim.record_request("s")
    await lim.record_request("s")
    assert await lim.acquire("s") == -1


@pytest.mark.asyncio
async def test_cooldown_remaining_is_zero_when_not_cooling():
    lim = _limiter()
    assert lim.cooldown_remaining("s") == 0.0


@pytest.mark.asyncio
async def test_cooldown_remaining_counts_down():
    lim = _limiter()
    await lim.record_failure("s", trigger_cooldown=True)
    rem = lim.cooldown_remaining("s")
    assert 0 < rem <= 10


@pytest.mark.asyncio
async def test_reset_site_clears_cooldown():
    lim = _limiter()
    await lim.record_failure("s", trigger_cooldown=True)
    assert lim.cooldown_remaining("s") > 0
    lim.reset_site("s")
    assert lim.cooldown_remaining("s") == 0.0


@pytest.mark.asyncio
async def test_success_resets_cooldown_after_threshold():
    """连续成功达到阈值后应清零冷却"""
    lim = _limiter()
    await lim.record_failure("s", trigger_cooldown=True)
    for _ in range(3):
        await lim.record_success("s")
    assert lim.site_stats["s"].cooldown_seconds == 0
    assert lim.cooldown_remaining("s") == 0.0


@pytest.mark.asyncio
async def test_status_reports_all_sites():
    lim = _limiter()
    await lim.record_request("a")
    st = lim.get_status()
    assert "a" in st
    for k in ("requests_last_minute", "requests_last_hour", "requests_today",
              "limit_per_minute", "limit_per_hour", "limit_per_day",
              "cooldown_remaining", "consecutive_success", "consecutive_failure"):
        assert k in st["a"], f"状态缺少字段 {k}"


@pytest.mark.asyncio
async def test_unknown_site_falls_back_to_global_limit():
    """站点没有独立限流配置时用全局的"""
    lim = _limiter(minute=7)
    st = lim.get_status()
    lim.site_stats["unknown"] = type(lim.site_stats.get("x", object())) \
        if False else lim._get_stats("unknown")
    st = lim.get_status()
    assert st["unknown"]["limit_per_minute"] == 7
