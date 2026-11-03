import asyncio
from typing import Dict, Tuple
from rich.console import Console

from .generic import GenericProvider, Message
from browser.manager import browser_manager
from limiter.rate import RateLimiter
from security import SecurityManager

console = Console()


class BridgeValidator:
    """桥接验证器"""

    TEST_PROMPT = "请用一句话介绍你自己，包括你的名字或型号。"

    def __init__(self, rate_limiter: RateLimiter, security_manager: SecurityManager):
        self.limiter = rate_limiter
        self.security = security_manager

    async def validate_site(self, site_name: str) -> Tuple[bool, str, str]:
        """
        验证单个站点
        返回: (是否成功, 检测到的模型, 错误信息)
        """
        from config import config

        site = config.get_site(site_name)
        if not site:
            return False, "", f"站点 {site_name} 不存在"

        console.print(f"\n[cyan]🔍 验证站点: {site.name} ({site.url})[/cyan]")

        provider = GenericProvider(site, self.limiter, self.security)

        try:
            response = ""
            async for chunk in provider.chat(
                [Message(role="user", content=self.TEST_PROMPT)],
                model="default",
            ):
                response += chunk

            if "[ERROR]" in response:
                return False, "", response

            if len(response) < 10:
                return False, "", "响应过短"

            page = await browser_manager.get_page(site_name)
            detected_model = await provider.get_current_model(page)

            console.print(f"[green]✅ {site.name} 验证成功[/green]")
            console.print(f"[dim]   响应: {response[:100]}...[/dim]")
            console.print(f"[dim]   检测到模型: {detected_model}[/dim]")

            return True, detected_model, ""

        except Exception as e:
            return False, "", str(e)

    async def validate_all(self) -> Dict:
        """验证所有已配置站点"""
        from config import config

        results = {}

        console.print("\n[bold cyan]═══ 开始验证所有站点 ═══[/bold cyan]\n")

        for site in config.get_enabled_sites():
            success, model, error = await self.validate_site(site.name)
            results[site.name] = {
                "success": success,
                "detected_model": model,
                "error": error,
                "url": site.url,
            }

            await asyncio.sleep(5)

        console.print("\n[bold cyan]═══ 验证结果总结 ═══[/bold cyan]")
        success_count = sum(1 for r in results.values() if r["success"])
        console.print(
            f"[{'green' if success_count > 0 else 'red'}]"
            f"成功: {success_count}/{len(results)}[/]"
        )

        for name, result in results.items():
            status = "✅" if result["success"] else "❌"
            console.print(f"  {status} {name}: {result['detected_model'] or result['error']}")

        return results
