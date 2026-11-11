import asyncio
import random
from typing import AsyncGenerator, List, Optional
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
from rich.console import Console

from .base import Message, ChatResult
from browser.manager import browser_manager
from browser.profile import profile_manager
from behavior.human import HumanBehavior
from security import SecurityManager
from limiter.rate import RateLimiter

console = Console()


async def match_login_state(page: Page, selector_expr: str) -> bool:
    """登录态选择器组合匹配（逗号分隔）：
      - 普通选择器：正面特征，任一存在且可见即可
      - '!' 前缀：反面特征，必须全部不存在
    返回 True = 登录态成立。
    例: 'textarea.input-scroll, !a:has-text("登录"), !button:has-text("登录")'
      = 有聊天输入框 且 页面上没有"登录"入口 ⇒ 已登录
    """
    positives, negatives = [], []
    for part in selector_expr.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("!"):
            negatives.append(part[1:].strip())
        else:
            positives.append(part)
    try:
        for n in negatives:
            el = await page.query_selector(n)
            if el and await el.is_visible():
                return False
        if positives:
            for p in positives:
                el = await page.query_selector(p)
                if el and await el.is_visible():
                    return True
            return False
        return True   # 只有反面条件且全部通过
    except Exception:
        return False


class GenericProvider:
    """通用网站 Provider（配置驱动）"""

    COMMON_INPUT_SELECTORS = [
        'textarea[class*="input" i]',
        'textarea[class*="chat" i]',
        'textarea[class*="message" i]',
        'textarea[placeholder*="message" i]',
        'textarea[placeholder*="ask" i]',
        'textarea[placeholder*="type" i]',
        'textarea',
        '[contenteditable="true"]',
        'input[type="text"][class*="chat" i]',
    ]

    COMMON_SEND_SELECTORS = [
        'button[type="submit"]',
        'button[class*="send" i]',
        'button[aria-label*="send" i]',
        'button[class*="submit" i]',
        '[role="button"][class*="send" i]',
    ]

    COMMON_RESPONSE_SELECTORS = [
        '[class*="message" i][class*="bot" i]',
        '[class*="message" i][class*="assistant" i]',
        '[class*="response" i]',
        '[class*="answer" i]',
        '[class*="markdown" i]',
        '[class*="prose" i]',
        '[data-testid*="message" i]',
        '[data-testid*="response" i]',
        '[data-testid*="answer" i]',
    ]

    COMMON_STOP_SELECTORS = [
        'button[class*="stop" i]',
        'button[aria-label*="stop" i]',
        '[class*="generating" i]',
        '[class*="loading" i]',
    ]

    # 登录页面特征：URL 关键词 + 页面元素
    LOGIN_URL_KEYWORDS = ["login", "signin", "sign-in", "auth", "account/login", "session"]
    LOGIN_PAGE_SELECTORS = [
        'input[type="password"]',
        'button[class*="login" i]',
        'button[class*="signin" i]',
        'a[href*="login" i]',
        '[class*="login-form" i]',
        '[data-testid*="login" i]',
    ]

    def __init__(self, site_config, rate_limiter: RateLimiter, security_manager: SecurityManager):
        self.site = site_config
        self.limiter = rate_limiter
        self.security = security_manager
        self._detected_selectors = {}

    async def chat(
        self,
        messages: List[Message],
        model: str = "default",
        stream: bool = True,
        captcha_wait_minutes: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """执行对话

        captcha_wait_minutes: 遇到验证码时等待人工处理的分钟数。
        API/IDE 调用应传 0~1（挂住 30 分钟会让 IDE 侧请求超时）；
        交互式 CLI 可传 None 用配置默认值。
        """
        from config import config

        wait_time = await self.limiter.acquire(self.site.name)
        if wait_time < 0:
            yield "[ERROR] 达到每日请求限制"
            return
        if wait_time > 0:
            console.print(f"[cyan]⏳ 等待 {wait_time:.0f}s...[/cyan]")
            await asyncio.sleep(wait_time)

        try:
            page = await browser_manager.get_page(self.site.name)
            human = HumanBehavior(page, config)

            if not page.url.startswith(self.site.url):
                console.print(f"[cyan]🌐 导航到 {self.site.url}[/cyan]")
                try:
                    await page.goto(self.site.url, wait_until="domcontentloaded", timeout=30000)
                except Exception as nav_err:
                    console.print(f"[yellow]⚠️ 导航失败，重试: {nav_err}[/yellow]")
                    # 浏览器可能已关闭，刷新页面
                    await asyncio.sleep(2)
                    page = await browser_manager.get_page(self.site.name)
                    try:
                        await page.goto(self.site.url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as retry_err:
                        yield f"[ERROR] 导航失败: {retry_err}"
                        return
                await asyncio.sleep(random.uniform(2, 4))

                # 关闭可能存在的遮罩层/弹窗
                await self._close_overlays(page)

                ok = await self.security.check_and_handle(page, self.site.name, captcha_wait_minutes)
                if not ok:
                    await self.limiter.record_failure(self.site.name, trigger_cooldown=True)
                    yield "[ERROR] 安全验证未通过"
                    return

            # 检测是否需要登录，如果需要则等待用户完成登录
            if await self._check_login_required(page):
                console.print(f"[yellow]🔐 {self.site.name} 检测到需要登录，请在浏览器中完成登录...[/yellow]")
                logged_in = await self._wait_for_login(page, self.site.name, timeout_minutes=10)
                if not logged_in:
                    profile_manager.set_login_status(self.site.name, False, "登录超时")
                    yield f"[ERROR:LOGIN_REQUIRED:{self.site.url}] {self.site.name} 登录超时"
                    return
                profile_manager.set_login_status(self.site.name, True, "登录成功")
                console.print(f"[green]✅ {self.site.name} 登录成功，继续使用...[/green]")
                await asyncio.sleep(1)

            await self._select_mode(page)
            await self._reset_to_new_chat(page)
            await self._select_model(page, model, human)

            prompt = self._build_prompt(messages)

            input_sel = await self._find_selector(page, "input", self.COMMON_INPUT_SELECTORS)
            if not input_sel:
                yield "[ERROR] 找不到输入框"
                return

            await human.before_action()

            console.print(f"[cyan]⌨️  输入中... ({len(prompt)} 字符)[/cyan]")
            try:
                await human.type_text(input_sel, prompt, fast=len(prompt) > 300)
            except Exception as type_err:
                console.print(f"[yellow]⚠️ 输入失败，重试: {type_err}[/yellow]")
                page = await browser_manager.get_page(self.site.name)
                try:
                    await human.type_text(input_sel, prompt, fast=len(prompt) > 300)
                except Exception:
                    # 某些站点有不透明遮罩(如 opacity-0 全屏 div)拦截点击，
                    # Playwright 可点性检查永远超时。兜底：JS 聚焦输入框后
                    # 用 insert_text 直接注入文本，绕过命中测试。
                    console.print("[yellow]⚠️ 点击仍被拦截，改用 JS 聚焦注入文本[/yellow]")
                    await self._focus_and_insert(page, input_sel, prompt)

            await asyncio.sleep(random.uniform(0.5, 1.5))

            await self._send_message(page, human)

            await self.limiter.record_request(self.site.name)

            await asyncio.sleep(1)
            ok = await self.security.check_and_handle(page, self.site.name, captcha_wait_minutes)
            if not ok:
                await self.limiter.record_failure(self.site.name, trigger_cooldown=True)
                yield "[ERROR] 发送后触发安全验证"
                return

            response_sel = await self._find_selector(
                page, "response", self.COMMON_RESPONSE_SELECTORS
            )

            total_text = ""
            async for chunk in self._stream_response(page, response_sel):
                total_text += chunk
                yield chunk

            await self.limiter.record_success(self.site.name)
            console.print(f"[green]✅ 完成，共 {len(total_text)} 字符[/green]")

        except PlaywrightTimeout as e:
            await self.limiter.record_failure(self.site.name)
            console.print(f"[red]❌ 超时: {e}[/red]")
            yield "[ERROR] 请求超时"

        except Exception as e:
            # 发送失败(遮罩拦截/按钮禁用等站点侧硬伤)触发冷却，
            # 让后续请求秒级快速失败，而不是每次都重跑慢流程
            msg = str(e)
            trigger = ("发送失败" in msg) or ("发送按钮禁用" in msg)
            await self.limiter.record_failure(self.site.name, trigger_cooldown=trigger)
            console.print(f"[red]❌ 错误: {msg[:80]}[/red]")
            yield f"[ERROR] {msg}"

    async def _check_login_required(self, page) -> bool:
        """检测当前页面是否需要登录，返回 True 表示需要登录。
        站点配置了 logged_in_selector 时按组合条件判断（逗号分隔）：
          - 普通选择器：必须存在且可见（正面特征，任一满足即可）
          - '!' 前缀：必须不存在（反面特征，全部满足才可通过）
        全部条件满足 ⇒ 已登录；否则 ⇒ 需要登录。
        无配置时走通用兜底检测。"""
        sel = self.site.selectors.get("logged_in_selector")
        if sel:
            return not await match_login_state(page, sel)
        # 兜底：URL 关键词 + 登录表单特征
        url = page.url.lower()
        if any(kw in url for kw in self.LOGIN_URL_KEYWORDS):
            return True
        for sel in self.LOGIN_PAGE_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _close_overlays(self, page):
        """关闭页面上的遮罩层、弹窗、cookies 同意横幅等"""
        overlay_selectors = [
            # Cookies 同意
            'button[title*="Accept" i]', 'button[title*="agree" i]',
            'button[class*="cookie" i]', 'button[class*="consent" i]',
            '[class*="cookie-banner" i] button', '[class*="cookie" i] button',
            'button:has-text("Accept")', 'button:has-text("Agree")',
            'button:has-text("Accept all")', 'button:has-text("I accept")',
            'button:has-text("Got it")', 'button:has-text("OK")',
            'button:has-text("Allow")', 'button:has-text("同意")',
            'button:has-text("接受")', 'button:has-text("好的")',
            # 通用关闭按钮
            'button[aria-label*="close" i]', 'button[aria-label*="Close" i]',
            'button[aria-label*="dismiss" i]', 'button[title*="close" i]',
            '[class*="modal" i] button', '[class*="overlay" i] button',
            '[class*="backdrop" i] button', '[class*="banner" i] button',
            # Modal 关闭
            'button[class*="close" i]', '[role="dialog"] button',
            '[role="alertdialog"] button',
        ]

        closed_any = False
        for sel in overlay_selectors:
            try:
                buttons = await page.query_selector_all(sel)
                for btn in buttons:
                    try:
                        if await btn.is_visible():
                            text = await btn.inner_text()
                            # 跳过明显不是关闭按钮的元素
                            if text.strip() and len(text.strip()) < 50:
                                await btn.click(timeout=1000)
                                console.print(f"[cyan]🔔 已点击: {text.strip()[:30]}[/cyan]")
                                await asyncio.sleep(0.5)
                                closed_any = True
                                break
                    except Exception:
                        continue
                if closed_any:
                    break
            except Exception:
                continue

        # 按 ESC 键关闭
        if not closed_any:
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

        return closed_any

    async def _wait_for_login(self, page, site_name: str, timeout_minutes: int = 10) -> bool:
        """等待用户完成登录，最多等 timeout_minutes 分钟"""
        # 浏览器隐藏(BROWSER_HEADLESS=hidden/1)时窗口在屏幕外，人工无法登录，
        # 干等只会让客户端无限 loading —— 立即快速失败，由调用方 yield 登录错误
        import os as _os
        if (_os.getenv("BROWSER_HEADLESS") or "").strip().lower() in ("hidden", "1", "true"):
            console.print(
                f"[red]🔒 {site_name} 需要登录，但浏览器为隐藏模式 —— 快速失败。"
                f"修复：设 BROWSER_HEADLESS=0 后请求该站点，在弹出的窗口中登录[/red]"
            )
            profile_manager.set_login_status(site_name, False, "隐藏模式下无法登录")
            return False
        console.print(f"[yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/yellow]")
        console.print(f"[yellow]🔐 {site_name} 需要登录[/yellow]")
        console.print(f"[yellow]📌 请在弹出的浏览器窗口中完成登录操作[/yellow]")
        console.print(f"[yellow]⏱️  等待最多 {timeout_minutes} 分钟...[/yellow]")
        console.print(f"[yellow]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/yellow]")

        for i in range(timeout_minutes * 12):  # 每5秒检查一次
            await asyncio.sleep(5)
            try:
                # 检查是否已离开登录页面
                if not await self._check_login_required(page):
                    console.print(f"[green]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/green]")
                    console.print(f"[green]✅ {site_name} 登录成功！[/green]")
                    console.print(f"[green]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/green]")
                    return True

                # 每30秒显示一次进度
                if i % 6 == 0 and i > 0:
                    remaining = (timeout_minutes * 12 - i) // 12
                    console.print(f"[cyan]⏳ 仍在等待 {site_name} 登录... ({remaining}分钟剩余)[/cyan]")
            except Exception:
                pass

        console.print(f"[red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/red]")
        console.print(f"[red]❌ {site_name} 登录超时[/red]")
        console.print(f"[red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/red]")
        profile_manager.set_login_status(site_name, False, "登录超时")
        return False

    async def _find_selector(
        self,
        page: Page,
        selector_type: str,
        fallbacks: List[str],
    ) -> Optional[str]:
        """查找有效选择器

        response 类型：配置了就直接返回，不等待出现（回答容器在流式
        生成时才出现，_stream_response 循环里自己等）。
        其他类型：等待元素出现验证有效。
        """
        configured = self.site.selectors.get(selector_type)
        if configured:
            # response 容器延迟出现，直接信任配置
            if selector_type == "response":
                self._detected_selectors[selector_type] = configured
                return configured
            try:
                el = await page.wait_for_selector(configured, timeout=3000)
                if el:
                    self._detected_selectors[selector_type] = configured
                    return configured
            except PlaywrightTimeout:
                pass

        if selector_type in self._detected_selectors:
            try:
                el = await page.wait_for_selector(
                    self._detected_selectors[selector_type], timeout=2000
                )
                if el:
                    return self._detected_selectors[selector_type]
            except PlaywrightTimeout:
                pass

        for selector in fallbacks:
            try:
                el = await page.wait_for_selector(selector, timeout=2000)
                if el and await el.is_visible():
                    self._detected_selectors[selector_type] = selector
                    console.print(f"[dim]🔍 自动检测到 {selector_type}: {selector[:50]}[/dim]")
                    return selector
            except PlaywrightTimeout:
                continue

        return None

    async def _select_mode(self, page: Page):
        """切换站点工作模式（chat.z.ai 的 Chat/Agent 模式）。

        selectors.default_mode: 'agent' 或 'chat'，对应按钮选择器
        mode_agent / mode_chat；已激活（data-active="true"）则跳过。
        """
        mode = (self.site.selectors.get("default_mode") or "").lower()
        if mode not in ("agent", "chat"):
            return
        sel_key = "mode_agent" if mode == "agent" else "mode_chat"
        label = "Agent 模式" if mode == "agent" else "Chat 模式"
        mode_sel = self.site.selectors.get(sel_key)
        if not mode_sel:
            return
        try:
            btn = await page.query_selector(mode_sel)
            if not btn or not await btn.is_visible():
                console.print(f"[yellow]⚠️ 未找到{label}按钮，保持当前模式[/yellow]")
                return
            active = (await btn.get_attribute("data-active") or "") == "true"
            if active:
                console.print(f"[cyan]🤖 {label}已激活[/cyan]")
                return
            await btn.click(timeout=3000)
            await asyncio.sleep(1)
            console.print(f"[cyan]🤖 已切换到 {label}[/cyan]")
        except Exception as e:
            console.print(f"[yellow]⚠️ 切换{label}失败: {e}[/yellow]")

    async def _reset_to_new_chat(self, page: Page):
        """回到站点首页开启全新会话。

        发送消息后 URL 会变为 /c/<id>，复用该页面会让上一条请求的
        会话上下文污染本条回复。首页输入框天然是新会话，直接导航回去即可；
        （不要用「新任务」按钮——它会弹出空内容的 bits-ui 对话框遮罩，
        拦截后续所有点击。）失败不阻断对话。"""
        try:
            if "/c/" in page.url:
                console.print("[cyan]🆕 回到首页开启新会话[/cyan]")
                await page.goto(self.site.url, wait_until="domcontentloaded",
                                timeout=30000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
        except Exception as e:
            console.print(f"[yellow]⚠️ 开启新会话失败(继续当前会话): {e}[/yellow]")

    async def _select_model(self, page: Page, model: str, human: HumanBehavior):
        """选择模型（如果网站支持）。

        策略：点开 model_selector，在下拉中按文本匹配目标模型并点击。
        配置了 model_option（选项叶子节点）时按文本精确匹配，避免误点容器。
        匹配失败（如目标已是当前模型或不在列表）则静默跳过，不阻断对话。
        """
        model_name = self.site.models.get(model, model)
        model_sel = self.site.selectors.get("model_selector")
        if not model_sel:
            return

        # 尝试用逗号分隔的多个选择器
        selectors = [s.strip() for s in model_sel.split(",")]

        # 先看当前选中的模型是否已经是目标，是则跳过（避免触发遮罩）
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    btn_text = (await btn.inner_text()).strip()
                    if model_name in btn_text or model_name == "default":
                        console.print(f"[cyan]🤖 模型已是: {btn_text}, 跳过切换[/cyan]")
                        # 但仍尝试关闭思考模式
                        await self._toggle_thinking(page, turn_off=True)
                        return
                    break
            except Exception:
                continue

        # 打开下拉
        opened = False
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=3000)
                    opened = True
                    await asyncio.sleep(0.8)
                    break
            except Exception:
                continue

        if not opened:
            console.print(f"[yellow]⚠️  无法打开模型选择器[/yellow]")
            return

        # 精确路径：按 model_option 叶子节点的完整文本匹配
        option_sel = self.site.selectors.get("model_option")
        if option_sel:
            try:
                for el in await page.query_selector_all(option_sel):
                    txt = (await el.inner_text()).strip()
                    if txt == model_name and await el.is_visible():
                        await el.click(timeout=2000)
                        await asyncio.sleep(0.5)
                        console.print(f"[cyan]🤖 已选择模型: {model_name}[/cyan]")
                        await self._toggle_thinking(page, turn_off=True)
                        return
            except Exception as e:
                console.print(f"[yellow]⚠️ 精确选模型失败，回退模糊匹配: {e}[/yellow]")

        # 在下拉列表中搜索目标模型
        option_ul = self.site.selectors.get("model_option_ul")
        option_selectors = []
        if option_ul:
            # 先找下拉容器
            dropdown_selectors = [s.strip() for s in option_ul.split(",")]
            dropdown_el = None
            for ds in dropdown_selectors:
                try:
                    de = await page.query_selector(ds)
                    if de and await de.is_visible():
                        dropdown_el = de
                        break
                except Exception:
                    continue
            if dropdown_el:
                # 在容器内搜索选项
                option_selectors = [
                    f'[role="option"]:has-text("{model_name}")',
                    f'li:has-text("{model_name}")',
                    f'button:has-text("{model_name}")',
                    f'div:has-text("{model_name}")',
                    f'span:has-text("{model_name}")',
                ]
        else:
            # 全局搜索
            option_selectors = [
                f'[role="option"]:has-text("{model_name}")',
                f'li:has-text("{model_name}")',
                f'button:has-text("{model_name}")',
                f'div:has-text("{model_name}")',
                f'span:has-text("{model_name}")',
                f'a:has-text("{model_name}")',
            ]

        option_clicked = False
        for opt_sel in option_selectors:
            try:
                el = await page.query_selector(opt_sel)
                if el and await el.is_visible():
                    await el.click(timeout=2000)
                    await asyncio.sleep(0.5)
                    option_clicked = True
                    console.print(f"[cyan]🤖 已选择模型: {model_name}[/cyan]")
                    break
            except Exception:
                continue

        if not option_clicked:
            # 打印下拉内容帮助调试
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            console.print(f"[yellow]⚠️  未找到模型选项: {model_name}[/yellow]")
        else:
            # 模型切换成功后，尝试关闭思考模式
            await self._toggle_thinking(page, turn_off=True)

    async def _toggle_thinking(self, page: Page, turn_off: bool = True):
        """切换思考模式开关（GLM-5.3-Flash 等）"""
        toggle_sel = self.site.selectors.get("thinking_toggle")
        if not toggle_sel:
            return
        selectors = [s.strip() for s in toggle_sel.split(",")]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    state = await el.get_attribute("aria-checked") or ""
                    is_on = "true" in state.lower() or "on" in state.lower()
                    if turn_off and is_on:
                        await el.click(timeout=2000)
                        console.print(f"[cyan]🤖 已关闭思考模式[/cyan]")
                        await asyncio.sleep(0.3)
                    return
            except Exception:
                continue

    async def _focus_and_insert(self, page: Page, input_sel: str, prompt: str):
        """遮罩层拦截点击时的兜底输入。
        优先：原生 value setter + input/change 事件（React/Svelte 受控组件能识别，
        键盘模拟无法触发发送按钮状态的站点用这个可正确提交）；
        仍不行再退回 insert_text。"""
        filled = await page.evaluate("""(args) => {
            const els = document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]');
            for (const el of els) {
                const r = el.getBoundingClientRect();
                if (r.width < 1) continue;
                el.focus();
                if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, args.text);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                } else {
                    el.textContent = args.text;
                    el.dispatchEvent(new InputEvent('input', {bubbles: true}));
                }
                return true;
            }
            return false;
        }""", {"text": prompt})
        await asyncio.sleep(0.8)
        if filled:
            # 框架状态已更新，Enter 提交
            await page.keyboard.press("Enter")
            return
        # 兜底：insert_text
        sel = input_sel.split(",")[0].strip()
        try:
            await page.eval_on_selector(sel, "el => el.focus()")
        except Exception:
            await page.evaluate(
                "() => { const el = document.querySelector('textarea, [contenteditable=\"true\"]');"
                " if (el) el.focus(); }"
            )
        await asyncio.sleep(0.3)
        await page.keyboard.insert_text(prompt)
        await asyncio.sleep(0.5)

    async def _send_message(self, page: Page, human: HumanBehavior):
        """发送消息。

        顺序：点【未禁用】的发送按钮 → 校验输入框被清空 → Enter 兜底 → 再校验。
        关键：JS click 点在 disabled 按钮上是【无声失败】(不报错也不发送)，
        旧逻辑会误以为已发送然后干等回复 —— 必须校验发送是否真的生效。
        """
        async def _input_cleared() -> bool:
            try:
                return await page.evaluate("""() => {
                    const els = document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]');
                    for (const el of els) {
                        const v = (el.value !== undefined ? el.value : el.textContent) || '';
                        if (v.trim().length > 0) return false;
                    }
                    return true;
                }""")
            except Exception:
                return True   # 页面跳转等导致查询失败时按已发送处理

        send_sel = await self._find_selector(page, "send_button", self.COMMON_SEND_SELECTORS)
        if send_sel:
            try:
                # 只点启用的按钮；disabled 的直接跳过
                clicked = await page.evaluate("""(sel) => {
                    for (const el of document.querySelectorAll(sel)) {
                        if (el.disabled) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 1) continue;
                        el.click();
                        return true;
                    }
                    return false;
                }""", send_sel)
                if clicked and await _input_cleared():
                    return
            except Exception:
                pass
            try:
                await human.click_element(send_sel)
                if await _input_cleared():
                    return
            except Exception:
                pass

        # 按钮路径未生效 → Enter 兜底
        await page.keyboard.press("Enter")
        if await _input_cleared():
            return

        console.print("[red]❌ 消息发送失败：发送按钮禁用且 Enter 不生效[/red]")
        raise RuntimeError("消息发送失败：发送按钮禁用且 Enter 兜底无效")

    async def _stream_response(
        self,
        page: Page,
        response_sel: Optional[str],
    ) -> AsyncGenerator[str, None]:
        """流式读取响应"""
        from config import config

        if not response_sel:
            response_sel = '[class*="message"]'

        last_text = ""
        stable_count = 0
        started = False  # 是否已开始收到实质内容
        # 站点可配置 response_timeout(秒)；未配置用全局 max_interval*2
        max_wait = int(self.site.selectors.get("response_timeout") or 0) \
            or config.global_rate_limit.max_interval_seconds * 2

        # 可选：剥离响应前缀（如 z.ai 的「思考过程」块）
        strip_prefix = self.site.selectors.get("response_strip_prefix")

        for _ in range(int(max_wait / 0.5)):
            await asyncio.sleep(0.5)

            try:
                elements = await page.query_selector_all(response_sel)
                if not elements:
                    continue

                last_el = elements[-1]
                current_text = await last_el.inner_text()

                # 剥离指定前缀块（按行匹配，去掉前缀行及其后空行）
                if strip_prefix and current_text:
                    lines = current_text.split("\n")
                    plines = [p.strip() for p in strip_prefix.split("|")]
                    # 跳过所有匹配前缀的起始行
                    i = 0
                    while i < len(lines) and lines[i].strip() in plines:
                        i += 1
                    # 跳过前缀后的空行
                    while i < len(lines) and not lines[i].strip():
                        i += 1
                    current_text = "\n".join(lines[i:])

                if current_text != last_text:
                    new_part = current_text[len(last_text):]
                    if new_part:
                        yield new_part
                    last_text = current_text
                    stable_count = 0
                    if current_text.strip():
                        started = True
                else:
                    # 未开始收到实质内容前不累加 stable（回答容器延迟出现）
                    if not started:
                        continue
                    stable_count += 1

                    stop_sel = self.site.selectors.get("stop_button")
                    if stop_sel:
                        stop_btn = await page.query_selector(stop_sel)
                        if stop_btn:
                            continue

                    for sel in self.COMMON_STOP_SELECTORS:
                        indicator = await page.query_selector(sel)
                        if indicator and await indicator.is_visible():
                            stable_count = 0
                            break

                    if stable_count >= 4:
                        break

            except Exception as e:
                console.print(f"[yellow]⚠️ 读取响应: {e}[/yellow]")
                continue

        # 超时仍无内容：显式报错而不是静默返回空回复，
        # 让上层(Claude/验证器)能看到失败原因
        if not last_text.strip():
            console.print(f"[red]❌ {self.site.name} 等待 {max_wait}s 未捕获到回复[/red]")
            yield "[ERROR] 未捕获到回复(消息可能未发送成功/未生成/触发验证码，请检查站点页面)"

    def _build_prompt(self, messages: List[Message]) -> str:
        """构建提示词"""
        if len(messages) == 1 and messages[0].role == "user":
            return messages[0].content

        parts = []
        for msg in messages:
            if msg.role == "system":
                parts.append(f"[System]: {msg.content}")
            elif msg.role == "user":
                parts.append(f"Human: {msg.content}")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}")
        return "\n\n".join(parts)

    async def get_current_model(self, page: Page) -> str:
        """获取当前使用的模型"""
        model_indicator = self.site.selectors.get("model_indicator")
        if model_indicator:
            try:
                el = await page.query_selector(model_indicator)
                if el:
                    text = await el.inner_text()
                    return text.strip()
            except Exception:
                pass
        return "unknown"
