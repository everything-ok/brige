import asyncio
from typing import Optional, Tuple
from playwright.async_api import Page
from rich.console import Console

console = Console()


class CaptchaDetector:
    """验证码/人机验证检测器"""

    # 额外的严格检查：只有当元素足够大时才认为是验证码
    CAPTCHA_MIN_SIZE = 100  # 验证码元素最小宽度/高度

    def __init__(self, config):
        self.config = config

    async def check(self, page: Page) -> Tuple[bool, Optional[str]]:
        """检查页面是否有验证"""
        for indicator in self.config.captcha_indicators:
            try:
                element = await page.query_selector(indicator.selector)
                if element:
                    is_visible = await element.is_visible()
                    if is_visible:
                        # 严格检查：排除通用选择器的误报
                        if indicator.name == "Generic Captcha" or indicator.name == "Generic Challenge":
                            # 只有当元素尺寸足够大时才认为是验证码
                            box = await element.bounding_box()
                            if not box or box['width'] < self.CAPTCHA_MIN_SIZE or box['height'] < self.CAPTCHA_MIN_SIZE:
                                continue
                            # 额外检查：是否在页面中央（验证码通常在中央）
                            viewport = page.viewport_size or {"width": 1280, "height": 800}
                            center_x = viewport['width'] / 2
                            center_y = viewport['height'] / 2
                            elem_x = box['x'] + box['width'] / 2
                            elem_y = box['y'] + box['height'] / 2
                            # 如果不在页面中央附近，跳过
                            if abs(elem_x - center_x) > viewport['width'] * 0.3 or abs(elem_y - center_y) > viewport['height'] * 0.3:
                                continue

                        console.print(
                            f"[yellow]⚠️  检测到验证: {indicator.name}[/yellow]"
                        )
                        return True, indicator.name
            except Exception:
                continue

        url = page.url.lower()
        if any(x in url for x in ["challenge", "captcha", "verify", "check"]):
            return True, "URL Challenge"

        return False, None

    async def wait_for_resolution(
        self,
        page: Page,
        timeout_minutes: int = 30,
        check_interval: int = 2,
    ) -> bool:
        """等待验证被解决"""
        console.print("[cyan]⏳ 等待人工完成验证...[/cyan]")

        timeout_seconds = timeout_minutes * 60
        elapsed = 0

        while elapsed < timeout_seconds:
            has_captcha, _ = await self.check(page)
            if not has_captcha:
                console.print("[green]✅ 验证已完成[/green]")
                return True

            await asyncio.sleep(check_interval)
            elapsed += check_interval

            if elapsed % 30 == 0:
                console.print(
                    f"[yellow]⏳ 仍在等待验证... ({elapsed}s/{timeout_seconds}s)[/yellow]"
                )

        console.print("[red]❌ 等待验证超时[/red]")
        return False
