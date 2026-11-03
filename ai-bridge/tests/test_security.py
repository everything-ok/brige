"""
测试安全验证检测模块
"""
import sys
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestCaptchaDetector:

    def _make_detector(self, mock_config):
        from security.detector import CaptchaDetector
        return CaptchaDetector(mock_config)

    @pytest.mark.asyncio
    async def test_no_captcha_on_clean_page(self, mock_page, mock_config):
        detector = self._make_detector(mock_config)
        mock_page.url = "https://poe.com/chat"
        mock_page.query_selector = AsyncMock(return_value=None)

        has_captcha, name = await detector.check(mock_page)
        assert has_captcha is False
        assert name is None

    @pytest.mark.asyncio
    async def test_detects_visible_captcha_element(self, mock_page, mock_config, mock_element):
        detector = self._make_detector(mock_config)
        mock_page.url = "https://poe.com/chat"

        # 第一个 indicator (#challenge-running) 返回可见元素
        mock_page.query_selector = AsyncMock(return_value=mock_element)
        mock_element.is_visible = AsyncMock(return_value=True)

        has_captcha, name = await detector.check(mock_page)
        assert has_captcha is True
        assert name == "Cloudflare"

    @pytest.mark.asyncio
    async def test_ignores_invisible_captcha_element(self, mock_page, mock_config, mock_element):
        detector = self._make_detector(mock_config)
        mock_page.url = "https://poe.com/chat"

        mock_page.query_selector = AsyncMock(return_value=mock_element)
        mock_element.is_visible = AsyncMock(return_value=False)  # 不可见

        has_captcha, _ = await detector.check(mock_page)
        assert has_captcha is False

    @pytest.mark.asyncio
    async def test_detects_captcha_in_url(self, mock_page, mock_config):
        detector = self._make_detector(mock_config)
        mock_page.url = "https://example.com/challenge/verify"
        mock_page.query_selector = AsyncMock(return_value=None)

        has_captcha, name = await detector.check(mock_page)
        assert has_captcha is True
        assert name == "URL Challenge"

    @pytest.mark.asyncio
    async def test_url_keywords_detected(self, mock_page, mock_config):
        detector = self._make_detector(mock_config)
        mock_page.query_selector = AsyncMock(return_value=None)

        for keyword in ["challenge", "captcha", "verify", "check"]:
            mock_page.url = f"https://example.com/{keyword}/page"
            has_captcha, _ = await detector.check(mock_page)
            assert has_captcha is True, f"URL 包含 '{keyword}' 应该被检测到"

    @pytest.mark.asyncio
    async def test_selector_exception_continues(self, mock_page, mock_config):
        """选择器查询异常时应该继续检查其他指示器"""
        detector = self._make_detector(mock_config)
        mock_page.url = "https://poe.com/chat"
        mock_page.query_selector = AsyncMock(side_effect=Exception("selector error"))

        # 不应该抛出异常
        has_captcha, _ = await detector.check(mock_page)
        assert has_captcha is False

    @pytest.mark.asyncio
    async def test_wait_for_resolution_resolves_when_captcha_gone(self, mock_page, mock_config):
        detector = self._make_detector(mock_config)
        mock_page.url = "https://poe.com/chat"

        call_count = 0

        async def query_side_effect(selector):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                el = MagicMock()
                el.is_visible = AsyncMock(return_value=True)
                return el
            return None  # 第3次调用后验证消失

        mock_page.query_selector = query_side_effect

        with patch("security.detector.asyncio.sleep", new_callable=AsyncMock):
            resolved = await detector.wait_for_resolution(mock_page, timeout_minutes=1, check_interval=1)

        assert resolved is True

    @pytest.mark.asyncio
    async def test_wait_for_resolution_times_out(self, mock_page, mock_config, mock_element):
        detector = self._make_detector(mock_config)
        mock_page.url = "https://poe.com/challenge"
        mock_page.query_selector = AsyncMock(return_value=mock_element)
        mock_element.is_visible = AsyncMock(return_value=True)

        with patch("security.detector.asyncio.sleep", new_callable=AsyncMock):
            resolved = await detector.wait_for_resolution(
                mock_page, timeout_minutes=0, check_interval=1
            )

        assert resolved is False


class TestSecurityManager:

    @pytest.mark.asyncio
    async def test_check_and_handle_passes_clean_page(self, mock_page, mock_config):
        from security import SecurityManager
        mock_config.security.auto_pause = False
        manager = SecurityManager(mock_config)

        mock_page.url = "https://poe.com/chat"
        mock_page.query_selector = AsyncMock(return_value=None)

        result = await manager.check_and_handle(mock_page, "poe")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_and_handle_returns_false_on_unresolved_captcha(
        self, mock_page, mock_config, mock_element
    ):
        from security import SecurityManager
        mock_config.security.auto_pause = True
        mock_config.security.screenshot_on_captcha = False
        mock_config.security.notification_sound = False
        mock_config.security.desktop_notification = False
        mock_config.security.max_wait_minutes = 0

        manager = SecurityManager(mock_config)

        mock_page.url = "https://poe.com/challenge"
        mock_page.query_selector = AsyncMock(return_value=mock_element)
        mock_element.is_visible = AsyncMock(return_value=True)

        with patch("security.detector.asyncio.sleep", new_callable=AsyncMock):
            result = await manager.check_and_handle(mock_page, "poe")

        assert result is False
