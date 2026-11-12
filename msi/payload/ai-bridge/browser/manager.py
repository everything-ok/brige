import asyncio
import os
import random
from typing import Dict, Optional
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page, Browser
from rich.console import Console

from .profile import profile_manager
from .stealth import STEALTH_JS

console = Console()


def fix_playwright_browsers_path() -> str:
    """修正 PLAYWRIGHT_BROWSERS_PATH。

    Cursor / Claude Code 等 IDE 的沙箱终端会把 PLAYWRIGHT_BROWSERS_PATH 指向
    形如 %TEMP%\\cursor-sandbox-cache\\<hash>\\playwright 的临时目录，那里并没有
    安装 Chromium，导致启动时报：
        Executable doesn't exist at ...\\playwright\\chromium-XXXX\\chrome-win64\\chrome.exe

    这里改回系统真实路径（%LOCALAPPDATA%\\ms-playwright），若其中确实存在浏览器。
    返回最终生效的路径（未设置时返回空串）。
    """
    current = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "") or ""
    default = Path(os.getenv("LOCALAPPDATA", "")) / "ms-playwright"

    sandboxed = "cursor-sandbox-cache" in current or "sandbox" in current.lower()
    missing = bool(current) and not Path(current).exists()

    if (sandboxed or missing) and default.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(default)
        console.print(
            f"[yellow]⚠️ 检测到沙箱 Playwright 路径，已重定向到: {default}[/yellow]"
        )
        return str(default)

    return current


class BrowserManager:
    """浏览器实例管理

    支持两种模式（由环境变量 BROWSER_MODE 控制）：
    - cdp:   通过 CDP 连接系统 Chrome（需 Chrome 以 --remote-debugging-port 启动），
             复用用户已登录的会话状态，不锁 profile、不污染用户 Chrome。
    - profile (默认): 用 launch_persistent_context 为每个站点维护独立持久化 profile。
    """

    def __init__(self):
        self._playwright = None
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}
        self._lock = asyncio.Lock()
        self._cdp_browser: Optional[Browser] = None
        # 手动登录临时可见集合：/login/{site} 把站点加入后强制弹出可见窗口，
        # 登录完成后移除，恢复 BROWSER_HEADLESS 的默认可见性
        self.visible_sites: set = set()
        # BROWSER_MODE=cdp 启用 CDP 连接；CDP_ENDPOINT 可覆盖默认端口
        self._mode = os.getenv("BROWSER_MODE", "profile").lower()
        self._cdp_endpoint = os.getenv(
            "CDP_ENDPOINT", "http://127.0.0.1:9222"
        )

    @staticmethod
    def _pick_debug_port(preferred: int) -> int:
        """优先用固定调试端口；被占用时自动找一个空闲端口"""
        import socket

        def _free(p: int) -> bool:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", p))
                    return True
                except OSError:
                    return False

        if _free(preferred):
            return preferred
        for p in range(preferred + 1, preferred + 50):
            if _free(p):
                console.print(
                    f"[yellow]⚠️ 调试端口 {preferred} 被占用，改用 {p}[/yellow]"
                )
                return p
        return preferred

    async def start(self):
        """启动 Playwright；CDP 模式下连接系统 Chrome"""
        fix_playwright_browsers_path()
        if self._mode == "cdp":
            console.print(f"[cyan]🔌 CDP 模式：连接 {self._cdp_endpoint} ...[/cyan]")
            self._playwright = await async_playwright().start()
            try:
                self._cdp_browser = await self._playwright.chromium.connect_over_cdp(
                    self._cdp_endpoint
                )
                console.print(f"[green]✅ 已连接系统 Chrome (CDP)[/green]")
            except Exception as e:
                console.print(
                    f"[red]❌ CDP 连接失败: {e}\n"
                    f"   请用调试端口启动 Chrome：\n"
                    f'   chrome.exe --remote-debugging-port=9222\n'
                    f"   或设 BROWSER_MODE=profile 回退独立 profile 模式[/red]"
                )
                raise
        else:
            console.print("[cyan]🌐 启动浏览器管理器 (profile 模式)...[/cyan]")
            self._playwright = await async_playwright().start()

    async def stop(self):
        """停止所有浏览器"""
        for page in self._pages.values():
            try:
                await page.close()
            except Exception:
                pass

        # CDP 模式不关闭用户的浏览器上下文，仅断开连接
        if self._mode != "cdp":
            for ctx in self._contexts.values():
                try:
                    await ctx.close()
                except Exception:
                    pass

        # CDP 连接断开（不退出 Chrome）
        if self._cdp_browser:
            try:
                await self._cdp_browser.close()
            except Exception:
                pass
            self._cdp_browser = None

        if self._playwright:
            await self._playwright.stop()

        self._contexts.clear()
        self._pages.clear()
        console.print("[yellow]👋 浏览器已关闭[/yellow]")

    async def get_page(self, site_name: str) -> Page:
        """获取站点专用页面

        - CDP 模式：复用系统 Chrome 已有标签页（URL 命中站点）或新建标签页，
          不创建独立 context，直接使用 default context。
        - profile 模式：用持久化 profile，自动重建已关闭的 context。
        """
        # Import here to avoid circular import
        from config import config

        async with self._lock:
            if self._mode == "cdp":
                return await self._get_or_create_cdp_page(site_name)

            # 检查已有 page 是否仍然有效
            if site_name in self._pages:
                page = self._pages[site_name]
                try:
                    if not page.is_closed():
                        # 进一步验证：尝试访问 URL 确保 page 真正可用
                        _ = page.url
                        return page
                except Exception:
                    pass
                # page 无效，清除引用
                self._pages.pop(site_name, None)

            # 检查 context 是否仍然有效，无效则重建
            if site_name in self._contexts:
                ctx = self._contexts[site_name]
                try:
                    # 尝试访问 pages 属性来验证 context 是否存活
                    _ = ctx.pages
                except Exception:
                    self._contexts.pop(site_name, None)

            context = await self._get_or_create_context(site_name)
            try:
                page = await context.new_page()
            except Exception as e:
                # context 已关闭/损坏，清缓存重建一次
                console.print(f"[yellow]⚠️ {site_name} new_page 失败，重建 context: {e}[/yellow]")
                self._contexts.pop(site_name, None)
                self._pages.pop(site_name, None)
                context = await self._get_or_create_context(site_name)
                page = await context.new_page()

            await page.add_init_script(STEALTH_JS)

            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            })

            self._pages[site_name] = page
            profile_manager.mark_used(site_name)

            return page

    async def _get_or_create_cdp_page(self, site_name: str) -> Page:
        """CDP 模式：复用已有标签页或新建，复用系统 Chrome 登录态"""
        from config import config

        # 检查已缓存 page 是否仍然有效
        if site_name in self._pages:
            try:
                if not self._pages[site_name].is_closed():
                    return self._pages[site_name]
            except Exception:
                pass
            self._pages.pop(site_name, None)

        site = config.get_site(site_name)
        site_url = site.url if site else None

        # 获取 default browser context（CDP 连接后自动存在）
        ctx = self._cdp_browser.contexts[0] if self._cdp_browser.contexts else None
        if ctx is None:
            ctx = await self._cdp_browser.new_context()

        # 复用已打开该站点的标签页，避免重复打开
        page = None
        if site_url:
            for p in ctx.pages:
                try:
                    if p.url.startswith(site_url) or site_url.startswith(p.url):
                        if not p.is_closed():
                            page = p
                            break
                except Exception:
                    continue

        if page is None:
            page = await ctx.new_page()
            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })

        self._pages[site_name] = page
        return page

    async def _get_or_create_context(self, site_name: str) -> BrowserContext:
        """获取或创建浏览器上下文"""
        from config import config

        if site_name in self._contexts:
            ctx = self._contexts[site_name]
            # 验证 context 仍存活：尝试访问 pages，失败则重建
            try:
                _ = ctx.pages
                return ctx
            except Exception:
                self._contexts.pop(site_name, None)
                self._pages.pop(site_name, None)

        profile_path = profile_manager.get_profile_path(site_name)

        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
            "--mute-audio",
            "--no-first-run",
            # 隐藏窗口时不降速：禁用遮挡节流，保证页面定时器/流式渲染正常
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            f"--window-size={1280 + random.randint(-50, 50)},{800 + random.randint(-50, 50)}",
        ]
        # 浏览器可见性（BROWSER_HEADLESS）：
        #   1/headless (默认) = 真无头，**完全不弹窗口**，不占桌面
        #   hidden/2          = 有头但窗口移到屏幕外（部分站点反爬更严时用）
        #   0/visible         = 正常显示窗口（仅人工登录/过验证码时用）
        # 手动登录中的站点强制可见（覆盖全局设置），
        # 登录完成后自动恢复默认的真无头。
        vis_mode = os.getenv("BROWSER_HEADLESS", "1").lower()
        if site_name in self.visible_sites:
            vis_mode = "0"
        if vis_mode in ("1", "true", "headless"):
            headless = True
        else:
            headless = False
            if vis_mode in ("hidden", "2", "offscreen"):
                launch_args.append("--window-position=-32000,-32000")

        # 可选：给 profile 浏览器开调试端口（如 BRIDGE_PROFILE_DEBUG_PORT=9223），
        # 便于人工/脚本用 CDP 检查页面 DOM、排查选择器问题
        dbg_port = os.getenv("BRIDGE_PROFILE_DEBUG_PORT")
        if dbg_port:
            # 固定端口在多站点并存时会冲突（Chrome 报 bind() 失败，DevTools 起不来），
            # 这里冲突时自动改用空闲端口，并把实际端口打印出来
            port = self._pick_debug_port(int(dbg_port))
            launch_args.append(f"--remote-debugging-port={port}")
            console.print(f"[cyan]🔌 profile 调试端口已开启: {port}[/cyan]")

        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=headless,
            viewport={
                "width": 1280 + random.randint(-50, 50),
                "height": 800 + random.randint(-50, 50),
            },
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            geolocation={"latitude": 31.2304, "longitude": 121.4737},
            permissions=["geolocation"],
            color_scheme="light",
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )

        # 真无头时 Chromium 的 HTTP 头仍带 "HeadlessChrome"，
        # 站点服务端据此判定为自动化 → 统一替换成正常 Chrome UA
        if headless:
            try:
                real_ua = await context.new_page()
                ua = await real_ua.evaluate("() => navigator.userAgent")
                await real_ua.close()
                if "HeadlessChrome" in ua:
                    ua = ua.replace("HeadlessChrome", "Chrome")
                    await context.set_extra_http_headers({"User-Agent": ua})
                    console.print("[dim]🕶️  已伪装 HeadlessChrome UA[/dim]")
            except Exception:
                pass

        self._contexts[site_name] = context
        console.print(f"[green]✅ {site_name} 浏览器上下文已创建[/green]")
        return context

    async def probe_headless(self, url: str, timeout: int = 15000) -> dict:
        """用临时 stealth headless 上下文探测 URL，探测后自动清理"""
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp(prefix="bridge_probe_")
        context = None
        try:
            context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                headless=True,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--mute-audio",
                    "--no-first-run",
                ],
                ignore_default_args=["--enable-automation"],
            )
            page = await context.new_page()
            await page.add_init_script(STEALTH_JS)
            await page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            })

            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # 只要 HTTP 状态 < 500 就算可访问
            if resp is None or resp.status >= 500:
                return {"accessible": False}

            await asyncio.sleep(2)
            title = await page.title()
            current_url = page.url.lower()

            # 检测是否需要登录
            login_kws = ["login", "signin", "sign-in", "auth", "register", "signup"]
            needs_login = any(kw in current_url for kw in login_kws)
            if not needs_login:
                try:
                    pw = await page.query_selector('input[type="password"]')
                    if pw and await pw.is_visible():
                        needs_login = True
                except Exception:
                    pass

            # 尝试抓取模型列表
            models = []
            try:
                els = await page.query_selector_all(
                    '[role="option"], [class*="model" i][class*="name" i], [data-testid*="model" i]'
                )
                seen: set = set()
                for el in els[:20]:
                    txt = (await el.inner_text()).strip().split("\n")[0]
                    if 2 < len(txt) < 50 and txt not in seen:
                        seen.add(txt)
                        models.append(txt)
            except Exception:
                pass

            return {
                "accessible": True,
                "title": title or url,
                "needs_login": needs_login,
                "models": models[:8],
            }
        except Exception as e:
            console.print(f"[dim]probe {url}: {e}[/dim]")
            return {"accessible": False}
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    async def check_site_alive(self, url: str, timeout: int = 8000) -> bool:
        """快速检测站点是否仍然可访问"""
        import tempfile, shutil
        tmp_dir = tempfile.mkdtemp(prefix="bridge_check_")
        context = None
        try:
            context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await context.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return resp is not None and resp.status < 500
        except Exception:
            return False
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    async def clear_site(self, site_name: str):
        """清除站点的浏览器状态"""
        if site_name in self._pages:
            try:
                await self._pages[site_name].close()
            except Exception:
                pass
            self._pages.pop(site_name, None)

        # CDP 模式不关用户 context、不删 profile（避免清掉用户登录态）
        if self._mode != "cdp" and site_name in self._contexts:
            try:
                await self._contexts[site_name].close()
            except Exception:
                pass
            self._contexts.pop(site_name, None)

        if self._mode != "cdp":
            profile_manager.clear_profile(site_name)


browser_manager = BrowserManager()
