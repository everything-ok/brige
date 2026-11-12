"""
pytest 共享 fixtures
"""
import sys
import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# 确保 ai-bridge 目录在 sys.path 中
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ─── 最小化 Config fixture（不依赖 sites.yaml / .env）────────

@pytest.fixture
def mock_config():
    """返回一个最小化的 mock config，不读取任何文件"""
    from config import (
        RateLimitConfig, BehaviorConfig, CooldownConfig,
        SecurityConfig, SiteConfig, CaptchaIndicator,
    )

    cfg = MagicMock()
    cfg.global_rate_limit = RateLimitConfig(
        requests_per_minute=10,
        requests_per_hour=100,
        requests_per_day=500,
        min_interval_seconds=1,
        max_interval_seconds=5,
    )
    cfg.behavior = BehaviorConfig(
        typing_speed_min_ms=10,
        typing_speed_max_ms=30,
        typing_mistake_rate=0.0,   # 测试时关闭错误
        scroll_probability=0.0,    # 测试时关闭随机滚动
        pause_probability=0.0,     # 测试时关闭随机暂停
        pause_duration_min=0.0,
        pause_duration_max=0.0,
        mouse_speed_min=1,
        mouse_speed_max=5,
    )
    cfg.cooldown = CooldownConfig(
        initial_seconds=10,
        max_seconds=60,
        multiplier=2.0,
        reset_after_success=3,
    )
    cfg.security = SecurityConfig(
        auto_pause=False,          # 测试时不等待人工
        notification_sound=False,
        desktop_notification=False,
        max_wait_minutes=0,
        screenshot_on_captcha=False,
    )
    cfg.captcha_indicators = [
        CaptchaIndicator(selector="#challenge-running", name="Cloudflare"),
        CaptchaIndicator(selector='iframe[src*="hcaptcha.com"]', name="hCaptcha"),
    ]

    # 两个测试站点
    site_poe = SiteConfig(
        name="poe",
        url="https://poe.com",
        enabled=True,
        priority=1,
        selectors={
            "input": 'textarea[class*="GrowingTextArea"]',
            "send_button": 'button[class*="SendButton"]',
            "response": '[class*="Message_botMessageBubble"]',
        },
        models={"gpt-4o": "GPT-4o", "claude-3-5-sonnet": "Claude-3.5-Sonnet"},
        rate_limit=RateLimitConfig(requests_per_hour=40),
    )
    site_perplexity = SiteConfig(
        name="perplexity",
        url="https://www.perplexity.ai",
        enabled=True,
        priority=2,
        selectors={"input": 'textarea[placeholder*="Ask"]'},
        models={"pplx-online": "default"},
        rate_limit=None,
    )

    cfg.sites = [site_poe, site_perplexity]
    cfg.get_site = lambda name: next((s for s in cfg.sites if s.name == name), None)
    cfg.get_enabled_sites = lambda: [s for s in cfg.sites if s.enabled]

    # 必须显式置空：MagicMock 的属性默认可调用且为真值，
    # 若不显式声明，config.API_KEY 会被当成"已配置密钥"，
    # 导致所有需要鉴权的接口（含管理页 /）在测试里返回 401。
    cfg.API_KEY = ""
    cfg.HOST = "127.0.0.1"

    return cfg


@pytest.fixture
def mock_page():
    """模拟 Playwright Page 对象"""
    page = MagicMock()
    page.url = "https://poe.com/chat"
    page.is_closed = MagicMock(return_value=False)
    page.viewport_size = {"width": 1280, "height": 800}

    # 所有 async 方法
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.screenshot = AsyncMock()
    page.add_init_script = AsyncMock()
    page.set_extra_http_headers = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.close = AsyncMock()

    return page


@pytest.fixture
def mock_element():
    """模拟 Playwright ElementHandle"""
    el = MagicMock()
    el.is_visible = AsyncMock(return_value=True)
    el.click = AsyncMock()
    el.fill = AsyncMock()
    el.type = AsyncMock()
    el.inner_text = AsyncMock(return_value="Hello from AI")
    el.bounding_box = AsyncMock(return_value={"x": 100, "y": 200, "width": 400, "height": 50})
    return el
