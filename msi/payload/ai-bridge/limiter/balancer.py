import asyncio
import random
from typing import Optional, List
from rich.console import Console

console = Console()


class LoadBalancer:
    """多站点负载均衡器"""

    def __init__(self, config, rate_limiter):
        self.config = config
        self.limiter = rate_limiter
        self._lock = asyncio.Lock()

    # 已知需要登录的站点（与 main.py 保持一致）
    # venice/openrouter 免登录（游客可用）
    LOGIN_REQUIRED_SITES = {
        "poe", "perplexity", "you", "chatgpt", "kimi", "doubao",
        "yuanbao", "tiangong", "qwen", "zai", "arena",
        "grok", "deepseek", "huggingchat", "mistral", "cohere", "groq",
        "phind", "happycapy", "hackaigc", "nanogpt"
    }

    async def select_site(
        self,
        model: str,
        preferred_site: Optional[str] = None,
    ) -> Optional[str]:
        """
        选择最佳站点
        优先级：指定站点 > 免登录站点 > 需登录站点
        """
        async with self._lock:
            sites = self.config.get_enabled_sites()

            if preferred_site:
                site = self.config.get_site(preferred_site)
                if site and site.enabled:
                    wait = await self.limiter.acquire(site.name)
                    if wait >= 0:
                        return site.name

            candidates = []
            for site in sites:
                if model in site.models or not site.models:
                    wait = await self.limiter.acquire(site.name)
                    if wait == 0:
                        candidates.append((site, 0))
                    elif wait > 0:
                        candidates.append((site, wait))

            if not candidates:
                console.print(f"[red]❌ 没有可用站点支持模型 {model}[/red]")
                return None

            # 优先免登录站点
            no_login = [c for c in candidates if c[0].name not in self.LOGIN_REQUIRED_SITES]
            pool = no_login if no_login else candidates

            immediate = [c for c in pool if c[1] == 0]
            if immediate:
                site, _ = random.choice(immediate)
                return site.name

            pool.sort(key=lambda x: (x[0].priority, x[1]))
            best_site, wait_time = pool[0]

            console.print(f"[cyan]⏳ 最佳站点 {best_site.name} 需等待 {wait_time:.0f}s[/cyan]")
            await asyncio.sleep(wait_time)
            return best_site.name

    def get_available_models(self) -> List[str]:
        """获取所有可用模型"""
        models = set()
        for site in self.config.get_enabled_sites():
            models.update(site.models.keys())
        return sorted(list(models))

    async def select_site_exclude(
        self,
        model: str,
        exclude: set,
    ) -> Optional[str]:
        """选择站点，排除已尝试过的站点"""
        async with self._lock:
            sites = [s for s in self.config.get_enabled_sites() if s.name not in exclude]
            candidates = []
            for site in sites:
                if model in site.models or not site.models:
                    wait = await self.limiter.acquire(site.name)
                    if wait >= 0:
                        candidates.append((site, wait))
            if not candidates:
                return None
            candidates.sort(key=lambda x: (x[1], x[0].priority))
            return candidates[0][0].name
