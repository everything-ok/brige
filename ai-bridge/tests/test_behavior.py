"""
测试行为模拟模块（timing / mouse / keyboard）
不依赖浏览器，纯逻辑测试
"""
import sys
import math
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ─── timing ──────────────────────────────────────────────────

class TestTiming:

    def test_gaussian_delay_positive(self):
        from behavior.timing import gaussian_delay
        for _ in range(50):
            d = gaussian_delay(0.1, 0.02)
            assert d >= 0.01, "延迟不能小于 min_val"

    def test_gaussian_delay_custom_min(self):
        from behavior.timing import gaussian_delay
        for _ in range(50):
            d = gaussian_delay(0.0, 0.001, min_val=0.5)
            assert d >= 0.5

    def test_exponential_delay_positive(self):
        from behavior.timing import exponential_delay
        for _ in range(50):
            d = exponential_delay(1.0)
            assert d > 0

    def test_human_reading_time_scales_with_length(self):
        from behavior.timing import human_reading_time
        short = human_reading_time(100)
        long_ = human_reading_time(1000)
        # 长文本阅读时间应该更长（考虑随机因子，用均值比较）
        # 运行多次取均值
        shorts = [human_reading_time(100) for _ in range(20)]
        longs = [human_reading_time(1000) for _ in range(20)]
        assert sum(longs) / len(longs) > sum(shorts) / len(shorts)

    def test_human_reading_time_zero_length(self):
        from behavior.timing import human_reading_time
        t = human_reading_time(0)
        assert t >= 0

    def test_typing_delay_range(self, mock_config):
        from behavior.timing import typing_delay
        for char in "abcdefghijklmnopqrstuvwxyz .,!?":
            d = typing_delay(char, mock_config)
            assert d > 0, f"字符 '{char}' 的延迟应大于 0"

    def test_typing_delay_punctuation_slower(self, mock_config):
        from behavior.timing import typing_delay
        import statistics
        letter_delays = [typing_delay("a", mock_config) for _ in range(500)]
        punct_delays = [typing_delay(".", mock_config) for _ in range(500)]
        assert statistics.median(punct_delays) > statistics.median(letter_delays)

    def test_typing_delay_newline_slowest(self, mock_config):
        from behavior.timing import typing_delay
        # 直接验证 \n 的延迟乘数范围（2.0~4.0），比普通字母（无乘数）更大
        # 用足够大的样本，排除偶发长停顿的干扰
        import statistics
        letter_delays = [typing_delay("a", mock_config) for _ in range(500)]
        newline_delays = [typing_delay("\n", mock_config) for _ in range(500)]
        # 用中位数而非均值，避免偶发长停顿（0.03概率）拉偏结果
        assert statistics.median(newline_delays) > statistics.median(letter_delays)

    def test_should_make_typo_zero_rate(self):
        from behavior.timing import should_make_typo
        cfg = MagicMock()
        cfg.behavior.typing_mistake_rate = 0.0
        for _ in range(100):
            assert should_make_typo(cfg) is False

    def test_should_make_typo_full_rate(self):
        from behavior.timing import should_make_typo
        cfg = MagicMock()
        cfg.behavior.typing_mistake_rate = 1.0
        for _ in range(20):
            assert should_make_typo(cfg) is True

    def test_get_typo_char_returns_neighbor(self):
        from behavior.timing import get_typo_char
        # 'a' 的邻居是 qwsz
        results = set(get_typo_char("a") for _ in range(50))
        assert results.issubset(set("qwsz"))

    def test_get_typo_char_uppercase_preserved(self):
        from behavior.timing import get_typo_char
        result = get_typo_char("A")
        assert result.isupper(), "大写字母的错误字符也应该是大写"

    def test_get_typo_char_unknown_returns_original(self):
        from behavior.timing import get_typo_char
        # 数字没有邻居映射，应返回原字符
        assert get_typo_char("1") == "1"
        assert get_typo_char("@") == "@"


# ─── mouse ───────────────────────────────────────────────────

class TestMouse:

    def test_bezier_curve_start_end(self):
        from behavior.mouse import bezier_curve
        start = (0.0, 0.0)
        end = (100.0, 100.0)
        points = bezier_curve(start, end, num_points=20)

        assert len(points) == 20
        # 第一个点应接近起点
        assert abs(points[0][0] - start[0]) < 1e-6
        assert abs(points[0][1] - start[1]) < 1e-6
        # 最后一个点应接近终点
        assert abs(points[-1][0] - end[0]) < 1e-6
        assert abs(points[-1][1] - end[1]) < 1e-6

    def test_bezier_curve_num_points(self):
        from behavior.mouse import bezier_curve
        for n in [10, 25, 50, 100]:
            pts = bezier_curve((0, 0), (200, 200), num_points=n)
            assert len(pts) == n

    def test_bezier_curve_returns_floats(self):
        from behavior.mouse import bezier_curve
        pts = bezier_curve((0, 0), (100, 100))
        for x, y in pts:
            assert isinstance(x, float)
            assert isinstance(y, float)

    def test_add_noise_preserves_length(self):
        from behavior.mouse import add_noise_to_path
        original = [(float(i), float(i)) for i in range(20)]
        noisy = add_noise_to_path(original, noise_level=1.0)
        assert len(noisy) == len(original)

    def test_add_noise_changes_values(self):
        from behavior.mouse import add_noise_to_path
        original = [(50.0, 50.0)] * 100
        noisy = add_noise_to_path(original, noise_level=5.0)
        # 噪声应该改变至少一些点
        changed = sum(1 for (ox, oy), (nx, ny) in zip(original, noisy)
                      if ox != nx or oy != ny)
        assert changed > 0

    @pytest.mark.asyncio
    async def test_human_mouse_move_calls_page(self, mock_page, mock_config):
        from behavior.mouse import human_mouse_move
        await human_mouse_move(mock_page, 500.0, 400.0, mock_config)
        # 应该调用了 page.mouse.move
        assert mock_page.mouse.move.called

    @pytest.mark.asyncio
    async def test_human_mouse_move_fallback_on_error(self, mock_config):
        from behavior.mouse import human_mouse_move
        page = MagicMock()
        page.viewport_size = None
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock(side_effect=Exception("move failed"))

        # 不应该抛出异常（有 fallback）
        try:
            await human_mouse_move(page, 100.0, 100.0, mock_config)
        except Exception:
            pytest.fail("human_mouse_move 不应该抛出异常")


# ─── HumanBehavior ───────────────────────────────────────────

class TestHumanBehavior:

    @pytest.mark.asyncio
    async def test_random_scroll_skipped_when_probability_zero(self, mock_page, mock_config):
        from behavior.human import HumanBehavior
        mock_config.behavior.scroll_probability = 0.0
        human = HumanBehavior(mock_page, mock_config)
        await human.random_scroll()
        mock_page.mouse.wheel.assert_not_called()

    @pytest.mark.asyncio
    async def test_random_scroll_called_when_probability_one(self, mock_page, mock_config):
        from behavior.human import HumanBehavior
        mock_config.behavior.scroll_probability = 1.0
        human = HumanBehavior(mock_page, mock_config)
        await human.random_scroll()
        mock_page.mouse.wheel.assert_called_once()

    @pytest.mark.asyncio
    async def test_random_pause_skipped_when_probability_zero(self, mock_page, mock_config):
        from behavior.human import HumanBehavior
        import time
        mock_config.behavior.pause_probability = 0.0
        human = HumanBehavior(mock_page, mock_config)
        start = time.time()
        await human.random_pause()
        elapsed = time.time() - start
        assert elapsed < 0.1, "概率为0时不应该有暂停"

    @pytest.mark.asyncio
    async def test_read_response_caps_at_5s(self, mock_page, mock_config):
        from behavior.human import HumanBehavior
        import time
        human = HumanBehavior(mock_page, mock_config)
        # 超长文本，阅读时间会被 cap 到 5s，但测试中我们 mock sleep
        with patch("behavior.human.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await human.read_response(10000)
            # 确保 sleep 被调用，且时间 <= 5.0
            assert mock_sleep.called
            sleep_time = mock_sleep.call_args[0][0]
            assert sleep_time <= 5.0

    @pytest.mark.asyncio
    async def test_move_to_element_handles_missing_element(self, mock_page, mock_config):
        from behavior.human import HumanBehavior
        mock_page.wait_for_selector = AsyncMock(side_effect=Exception("not found"))
        human = HumanBehavior(mock_page, mock_config)
        # 不应该抛出异常
        await human.move_to_element("nonexistent-selector")

    @pytest.mark.asyncio
    async def test_before_action_runs_without_error(self, mock_page, mock_config):
        from behavior.human import HumanBehavior
        mock_config.behavior.scroll_probability = 0.0
        mock_config.behavior.pause_probability = 0.0
        human = HumanBehavior(mock_page, mock_config)
        await human.before_action()  # 不应该抛出异常
