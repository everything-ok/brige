"""
测试浏览器管理模块（mock Playwright）
"""
import sys
import json
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestProfileManager:

    def test_get_profile_path_creates_directory(self, tmp_path):
        from browser.profile import ProfileManager
        import browser.profile as profile_module

        original = profile_module.PROFILES_DIR
        profile_module.PROFILES_DIR = tmp_path

        try:
            manager = ProfileManager()
            manager.profiles_dir = tmp_path
            manager.metadata_file = tmp_path / "metadata.json"
            manager._metadata = {"profiles": {}}

            path = manager.get_profile_path("testsite")
            assert path.exists()
            assert path.is_dir()
        finally:
            profile_module.PROFILES_DIR = original

    def test_get_profile_path_records_metadata(self, tmp_path):
        from browser.profile import ProfileManager
        import browser.profile as profile_module

        original = profile_module.PROFILES_DIR
        profile_module.PROFILES_DIR = tmp_path

        try:
            manager = ProfileManager()
            manager.profiles_dir = tmp_path
            manager.metadata_file = tmp_path / "metadata.json"
            manager._metadata = {"profiles": {}}

            manager.get_profile_path("newsite")
            assert "newsite" in manager._metadata["profiles"]
            assert "created" in manager._metadata["profiles"]["newsite"]
        finally:
            profile_module.PROFILES_DIR = original

    def test_mark_used_increments_count(self, tmp_path):
        from browser.profile import ProfileManager
        import browser.profile as profile_module

        original = profile_module.PROFILES_DIR
        profile_module.PROFILES_DIR = tmp_path

        try:
            manager = ProfileManager()
            manager.profiles_dir = tmp_path
            manager.metadata_file = tmp_path / "metadata.json"
            manager._metadata = {"profiles": {"mysite": {"use_count": 0, "last_used": None, "created": "2024"}}}

            manager.mark_used("mysite")
            assert manager._metadata["profiles"]["mysite"]["use_count"] == 1
            assert manager._metadata["profiles"]["mysite"]["last_used"] is not None
        finally:
            profile_module.PROFILES_DIR = original

    def test_clear_profile_removes_directory(self, tmp_path):
        from browser.profile import ProfileManager
        import browser.profile as profile_module

        original = profile_module.PROFILES_DIR
        profile_module.PROFILES_DIR = tmp_path

        try:
            manager = ProfileManager()
            manager.profiles_dir = tmp_path
            manager.metadata_file = tmp_path / "metadata.json"
            manager._metadata = {"profiles": {"mysite": {}}}

            # 创建目录
            site_dir = tmp_path / "mysite"
            site_dir.mkdir()

            manager.clear_profile("mysite")
            assert not site_dir.exists()
            assert "mysite" not in manager._metadata["profiles"]
        finally:
            profile_module.PROFILES_DIR = original

    def test_list_profiles(self, tmp_path):
        from browser.profile import ProfileManager
        import browser.profile as profile_module

        original = profile_module.PROFILES_DIR
        profile_module.PROFILES_DIR = tmp_path

        try:
            manager = ProfileManager()
            manager.profiles_dir = tmp_path
            manager.metadata_file = tmp_path / "metadata.json"
            manager._metadata = {"profiles": {"site1": {}, "site2": {}}}

            profiles = manager.list_profiles()
            assert "site1" in profiles
            assert "site2" in profiles
        finally:
            profile_module.PROFILES_DIR = original

    def test_metadata_persisted_to_disk(self, tmp_path):
        from browser.profile import ProfileManager
        import browser.profile as profile_module

        original = profile_module.PROFILES_DIR
        profile_module.PROFILES_DIR = tmp_path

        try:
            manager = ProfileManager()
            manager.profiles_dir = tmp_path
            manager.metadata_file = tmp_path / "metadata.json"
            manager._metadata = {"profiles": {}}

            manager.get_profile_path("persistedsite")

            # 重新加载
            with open(tmp_path / "metadata.json") as f:
                saved = json.load(f)

            assert "persistedsite" in saved["profiles"]
        finally:
            profile_module.PROFILES_DIR = original


class TestStealthJS:

    def test_stealth_js_is_string(self):
        from browser.stealth import STEALTH_JS
        assert isinstance(STEALTH_JS, str)
        assert len(STEALTH_JS) > 100

    def test_stealth_js_hides_webdriver(self):
        from browser.stealth import STEALTH_JS
        assert "webdriver" in STEALTH_JS

    def test_stealth_js_sets_chrome_runtime(self):
        from browser.stealth import STEALTH_JS
        assert "window.chrome" in STEALTH_JS

    def test_stealth_js_sets_plugins(self):
        from browser.stealth import STEALTH_JS
        assert "plugins" in STEALTH_JS

    def test_stealth_js_sets_languages(self):
        from browser.stealth import STEALTH_JS
        assert "zh-CN" in STEALTH_JS

    def test_stealth_js_sets_webgl(self):
        from browser.stealth import STEALTH_JS
        assert "WebGLRenderingContext" in STEALTH_JS


class TestBrowserManager:

    @pytest.mark.asyncio
    async def test_start_initializes_playwright(self):
        from browser.manager import BrowserManager

        manager = BrowserManager()

        mock_playwright = MagicMock()
        mock_playwright_instance = MagicMock()
        mock_playwright.__aenter__ = AsyncMock(return_value=mock_playwright_instance)
        mock_playwright.__aexit__ = AsyncMock()

        with patch("browser.manager.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright_instance)
            await manager.start()

        assert manager._playwright is not None

    @pytest.mark.asyncio
    async def test_stop_closes_all_resources(self):
        from browser.manager import BrowserManager

        manager = BrowserManager()

        mock_page = MagicMock()
        mock_page.close = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        mock_ctx = MagicMock()
        mock_ctx.close = AsyncMock()

        mock_playwright = MagicMock()
        mock_playwright.stop = AsyncMock()

        manager._pages = {"poe": mock_page}
        manager._contexts = {"poe": mock_ctx}
        manager._playwright = mock_playwright

        await manager.stop()

        mock_page.close.assert_called_once()
        mock_ctx.close.assert_called_once()
        mock_playwright.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_page_returns_existing_page(self):
        from browser.manager import BrowserManager

        manager = BrowserManager()

        mock_page = MagicMock()
        mock_page.is_closed = MagicMock(return_value=False)
        manager._pages = {"poe": mock_page}

        page = await manager.get_page("poe")
        assert page is mock_page

    @pytest.mark.asyncio
    async def test_get_page_creates_new_when_closed(self, mock_config):
        from browser.manager import BrowserManager

        manager = BrowserManager()

        closed_page = MagicMock()
        closed_page.is_closed = MagicMock(return_value=True)
        manager._pages = {"poe": closed_page}

        new_page = MagicMock()
        new_page.add_init_script = AsyncMock()
        new_page.set_extra_http_headers = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.new_page = AsyncMock(return_value=new_page)
        manager._contexts = {"poe": mock_ctx}

        # config 是在函数内部 import 的，patch 模块级别的 config 对象
        with patch("config.config", mock_config):
            page = await manager.get_page("poe")

        assert page is new_page
        new_page.add_init_script.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_site_removes_page_and_context(self):
        from browser.manager import BrowserManager

        manager = BrowserManager()

        mock_page = MagicMock()
        mock_page.close = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.close = AsyncMock()

        manager._pages = {"poe": mock_page}
        manager._contexts = {"poe": mock_ctx}
        manager._profile_manager = MagicMock()
        manager._profile_manager.clear_profile = MagicMock()

        await manager.clear_site("poe")

        assert "poe" not in manager._pages
        assert "poe" not in manager._contexts
        manager._profile_manager.clear_profile.assert_called_once_with("poe")
