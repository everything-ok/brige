"""
测试 GenericProvider 核心逻辑（mock 浏览器）
"""
import sys
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _make_provider(mock_config, site_name="poe"):
    from providers.generic import GenericProvider
    from limiter.rate import RateLimiter
    from security import SecurityManager

    site = mock_config.get_site(site_name)
    limiter = RateLimiter(mock_config)
    security = SecurityManager(mock_config)
    return GenericProvider(site, limiter, security)


class TestBuildPrompt:

    def test_single_user_message(self, mock_config):
        from providers.base import Message
        provider = _make_provider(mock_config)
        msgs = [Message(role="user", content="Hello")]
        result = provider._build_prompt(msgs)
        assert result == "Hello"

    def test_multi_turn_conversation(self, mock_config):
        from providers.base import Message
        provider = _make_provider(mock_config)
        msgs = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hi"),
            Message(role="assistant", content="Hello!"),
            Message(role="user", content="How are you?"),
        ]
        result = provider._build_prompt(msgs)
        assert "[System]: You are helpful" in result
        assert "Human: Hi" in result
        assert "Assistant: Hello!" in result
        assert "Human: How are you?" in result

    def test_system_only_message(self, mock_config):
        from providers.base import Message
        provider = _make_provider(mock_config)
        msgs = [Message(role="system", content="System prompt")]
        result = provider._build_prompt(msgs)
        assert "[System]: System prompt" in result

    def test_empty_messages(self, mock_config):
        from providers.base import Message
        provider = _make_provider(mock_config)
        result = provider._build_prompt([])
        assert result == ""

    def test_messages_joined_with_double_newline(self, mock_config):
        from providers.base import Message
        provider = _make_provider(mock_config)
        msgs = [
            Message(role="user", content="First"),
            Message(role="assistant", content="Second"),
        ]
        result = provider._build_prompt(msgs)
        assert "\n\n" in result


class TestFindSelector:

    @pytest.mark.asyncio
    async def test_uses_configured_selector_first(self, mock_config, mock_page, mock_element):
        provider = _make_provider(mock_config)
        mock_page.wait_for_selector = AsyncMock(return_value=mock_element)

        result = await provider._find_selector(mock_page, "input", ["textarea"])
        assert result == mock_config.get_site("poe").selectors["input"]

    @pytest.mark.asyncio
    async def test_falls_back_to_common_selectors(self, mock_config, mock_page, mock_element):
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        provider = _make_provider(mock_config)

        call_count = 0

        async def wait_side_effect(selector, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PlaywrightTimeout("configured selector not found")
            return mock_element

        mock_page.wait_for_selector = wait_side_effect
        mock_element.is_visible = AsyncMock(return_value=True)

        result = await provider._find_selector(mock_page, "input", ["textarea"])
        assert result == "textarea"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_selector_found(self, mock_config, mock_page):
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        provider = _make_provider(mock_config)
        mock_page.wait_for_selector = AsyncMock(side_effect=PlaywrightTimeout("not found"))

        result = await provider._find_selector(mock_page, "input", ["textarea", "input"])
        assert result is None

    @pytest.mark.asyncio
    async def test_caches_detected_selector(self, mock_config, mock_page, mock_element):
        provider = _make_provider(mock_config)

        # 清空配置的选择器，强制走 fallback 路径
        provider.site = MagicMock()
        provider.site.selectors = {}

        call_count = 0

        async def wait_side_effect(selector, timeout=None):
            nonlocal call_count
            call_count += 1
            return mock_element

        mock_page.wait_for_selector = wait_side_effect
        mock_element.is_visible = AsyncMock(return_value=True)

        # 第一次调用 — 走 fallback，检测并缓存
        result1 = await provider._find_selector(mock_page, "input", ["textarea"])
        calls_after_first = call_count

        # 第二次调用 — 命中缓存，只需 1 次 wait_for_selector
        result2 = await provider._find_selector(mock_page, "input", ["textarea"])

        assert result1 == result2 == "textarea"
        assert "input" in provider._detected_selectors
        assert call_count == calls_after_first + 1


class TestChatFlow:

    @pytest.mark.asyncio
    async def test_chat_returns_error_on_daily_limit(self, mock_config):
        from providers.generic import GenericProvider
        from limiter.rate import RateLimiter
        from security import SecurityManager
        from config import RateLimitConfig
        from providers.base import Message

        mock_config.global_rate_limit = RateLimitConfig(
            requests_per_minute=0,
            requests_per_hour=0,
            requests_per_day=0,
            min_interval_seconds=0,
            max_interval_seconds=0,
        )
        site = mock_config.get_site("poe")
        site.rate_limit = None

        limiter = RateLimiter(mock_config)
        security = SecurityManager(mock_config)
        provider = GenericProvider(site, limiter, security)

        chunks = []
        async for chunk in provider.chat([Message(role="user", content="test")]):
            chunks.append(chunk)

        assert any("[ERROR]" in c for c in chunks)
        assert any("每日" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_chat_navigates_to_site_when_not_on_page(self, mock_config, mock_page):
        from providers.generic import GenericProvider
        from limiter.rate import RateLimiter
        from security import SecurityManager
        from providers.base import Message
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        site = mock_config.get_site("poe")
        limiter = RateLimiter(mock_config)
        security = SecurityManager(mock_config)
        provider = GenericProvider(site, limiter, security)

        mock_page.url = "about:blank"
        mock_page.goto = AsyncMock()
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.query_selector_all = AsyncMock(return_value=[])
        mock_page.wait_for_selector = AsyncMock(side_effect=PlaywrightTimeout("not found"))

        # config 在 generic.py 里是函数内部 import，patch config 模块里的单例
        with patch("providers.generic.browser_manager") as mock_bm, \
             patch("providers.generic.asyncio.sleep", new_callable=AsyncMock), \
             patch("config.config", mock_config):
            mock_bm.get_page = AsyncMock(return_value=mock_page)

            chunks = []
            async for chunk in provider.chat([Message(role="user", content="test")]):
                chunks.append(chunk)

        # goto 现在带 timeout 参数（导航失败会重试），断言时需匹配
        mock_page.goto.assert_called_once_with(site.url,
                                               wait_until="domcontentloaded",
                                               timeout=30000)

    @pytest.mark.asyncio
    async def test_chat_sends_message_and_streams_response(self, mock_config, mock_page, mock_element):
        from providers.generic import GenericProvider
        from limiter.rate import RateLimiter
        from security import SecurityManager
        from providers.base import Message

        site = mock_config.get_site("poe")
        limiter = RateLimiter(mock_config)
        security = SecurityManager(mock_config)
        provider = GenericProvider(site, limiter, security)

        mock_page.url = "https://poe.com/chat"

        response_el = MagicMock()
        call_count = 0

        async def inner_text_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return "Hello"
            return "Hello, how can I help you?"

        response_el.inner_text = inner_text_side_effect
        response_el.is_visible = AsyncMock(return_value=False)

        mock_page.wait_for_selector = AsyncMock(return_value=mock_element)
        mock_element.is_visible = AsyncMock(return_value=True)
        mock_element.bounding_box = AsyncMock(return_value={"x": 100, "y": 200, "width": 400, "height": 50})
        mock_page.query_selector_all = AsyncMock(return_value=[response_el])
        mock_page.query_selector = AsyncMock(return_value=None)

        # config 在 generic.py 里是函数内部 import，patch config 模块里的单例
        with patch("providers.generic.browser_manager") as mock_bm, \
             patch("providers.generic.asyncio.sleep", new_callable=AsyncMock), \
             patch("config.config", mock_config), \
             patch("behavior.human.asyncio.sleep", new_callable=AsyncMock), \
             patch("behavior.mouse.asyncio.sleep", new_callable=AsyncMock):
            mock_bm.get_page = AsyncMock(return_value=mock_page)

            chunks = []
            async for chunk in provider.chat([Message(role="user", content="Hi")]):
                chunks.append(chunk)

        full_response = "".join(chunks)
        assert len(full_response) > 0
        assert "[ERROR]" not in full_response

    @pytest.mark.asyncio
    async def test_chat_handles_playwright_timeout(self, mock_config, mock_page):
        from providers.generic import GenericProvider
        from limiter.rate import RateLimiter
        from security import SecurityManager
        from providers.base import Message
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        site = mock_config.get_site("poe")
        limiter = RateLimiter(mock_config)
        security = SecurityManager(mock_config)
        provider = GenericProvider(site, limiter, security)

        mock_page.url = "https://poe.com/chat"
        mock_page.wait_for_selector = AsyncMock(side_effect=PlaywrightTimeout("timeout"))

        # config 在 generic.py 里是函数内部 import，patch config 模块里的单例
        with patch("providers.generic.browser_manager") as mock_bm, \
             patch("providers.generic.asyncio.sleep", new_callable=AsyncMock), \
             patch("config.config", mock_config):
            mock_bm.get_page = AsyncMock(return_value=mock_page)

            chunks = []
            async for chunk in provider.chat([Message(role="user", content="test")]):
                chunks.append(chunk)

        assert any("[ERROR]" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_get_current_model_returns_text(self, mock_config, mock_page, mock_element):
        provider = _make_provider(mock_config)
        # model_indicator 选择器 key 必须在 site.selectors 里才会被查询
        provider.site.selectors["model_indicator"] = '[class*="ModelName"]'
        mock_element.inner_text = AsyncMock(return_value="  GPT-4o  ")
        mock_page.query_selector = AsyncMock(return_value=mock_element)

        result = await provider.get_current_model(mock_page)
        assert result == "GPT-4o"

    @pytest.mark.asyncio
    async def test_get_current_model_returns_unknown_when_no_selector(self, mock_config, mock_page):
        provider = _make_provider(mock_config)
        # 没有配置 model_indicator，直接返回 unknown
        provider.site.selectors.pop("model_indicator", None)

        result = await provider.get_current_model(mock_page)
        assert result == "unknown"

    @pytest.mark.asyncio
    async def test_get_current_model_returns_unknown_on_error(self, mock_config, mock_page):
        provider = _make_provider(mock_config)
        provider.site.selectors["model_indicator"] = '[class*="ModelName"]'
        mock_page.query_selector = AsyncMock(side_effect=Exception("error"))

        result = await provider.get_current_model(mock_page)
        assert result == "unknown"
