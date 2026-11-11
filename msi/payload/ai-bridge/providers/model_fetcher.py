"""从各站点实时抓取可用模型列表"""
import asyncio
from typing import List, Dict
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
from rich.console import Console

console = Console()


async def fetch_models_for_site(site_name: str, page: Page) -> List[str]:
    """根据站点名称抓取模型列表，失败时返回空列表"""
    try:
        fetcher = _FETCHERS.get(site_name)
        if fetcher:
            return await asyncio.wait_for(fetcher(page), timeout=15.0)
    except Exception as e:
        console.print(f"[yellow]⚠️ {site_name} 模型抓取失败: {e}[/yellow]")
    return []


async def _fetch_poe(page: Page) -> List[str]:
    if not page.url.startswith("https://poe.com"):
        await page.goto("https://poe.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    models = await page.evaluate("""() => {
        const els = document.querySelectorAll('[class*="BotHeader"] span, [class*="ModelName"], [class*="botName"]');
        return [...new Set([...els].map(e => e.innerText.trim()).filter(t => t.length > 1 && t.length < 60))];
    }""")
    return models or []


async def _fetch_perplexity(page: Page) -> List[str]:
    if not page.url.startswith("https://www.perplexity.ai"):
        await page.goto("https://www.perplexity.ai", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    # 点开模型选择器
    try:
        btn = await page.query_selector('[class*="ModelSelector"], [aria-label*="model" i]')
        if btn:
            await btn.click()
            await asyncio.sleep(1)
    except Exception:
        pass
    models = await page.evaluate("""() => {
        const els = document.querySelectorAll('[role="option"], [class*="modelOption"], [class*="ModelOption"]');
        return [...new Set([...els].map(e => e.innerText.trim().split('\\n')[0]).filter(t => t.length > 1 && t.length < 60))];
    }""")
    return models or []


async def _fetch_grok(page: Page) -> List[str]:
    if not page.url.startswith("https://grok.com"):
        await page.goto("https://grok.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    # 尝试点开模型下拉
    try:
        btn = await page.query_selector('[class*="ModelSelector"], [aria-label*="model" i], [class*="model-select" i]')
        if btn:
            await btn.click()
            await asyncio.sleep(1)
    except Exception:
        pass
    models = await page.evaluate("""() => {
        const els = document.querySelectorAll('[role="option"], [class*="modelItem"], [class*="ModelItem"]');
        const names = [...new Set([...els].map(e => e.innerText.trim().split('\\n')[0]).filter(t => t.length > 1 && t.length < 60))];
        return names.length ? names : ['grok-3', 'grok-3-mini'];
    }""")
    return models or ["grok-3", "grok-3-mini"]


async def _fetch_chatgpt(page: Page) -> List[str]:
    if not page.url.startswith("https://chatgpt.com"):
        await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    try:
        btn = await page.query_selector('[data-testid="model-switcher-dropdown-button"], [aria-label*="model" i]')
        if btn:
            await btn.click()
            await asyncio.sleep(1)
    except Exception:
        pass
    models = await page.evaluate("""() => {
        const els = document.querySelectorAll('[data-testid*="model"], [class*="modelOption"], [role="option"]');
        const names = [...new Set([...els].map(e => e.innerText.trim().split('\\n')[0]).filter(t => t.length > 1 && t.length < 60))];
        return names.length ? names : ['gpt-4o', 'gpt-4o-mini', 'o1', 'o3-mini'];
    }""")
    return models or ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]


async def _fetch_zai(page: Page) -> List[str]:
    if not page.url.startswith("https://chat.z.ai"):
        await page.goto("https://chat.z.ai", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    models = await page.evaluate("""() => {
        const els = document.querySelectorAll('[role="option"], [class*="model" i]');
        const names = [...new Set([...els].map(e => e.innerText.trim().split('\\n')[0]).filter(t => t.length > 1 && t.length < 60))];
        return names.length ? names : ['zai-default'];
    }""")
    return models or ["zai-default"]


async def _fetch_arena(page: Page) -> List[str]:
    if not page.url.startswith("https://arena.ai"):
        await page.goto("https://arena.ai", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    models = await page.evaluate("""() => {
        const els = document.querySelectorAll('[role="option"], [class*="model" i]');
        const names = [...new Set([...els].map(e => e.innerText.trim().split('\\n')[0]).filter(t => t.length > 1 && t.length < 60))];
        return names.length ? names : ['arena-default'];
    }""")
    return models or ["arena-default"]


async def _fetch_you(page: Page) -> List[str]:
    return ["you-default"]


_FETCHERS = {
    "poe": _fetch_poe,
    "perplexity": _fetch_perplexity,
    "grok": _fetch_grok,
    "chatgpt": _fetch_chatgpt,
    "zai": _fetch_zai,
    "arena": _fetch_arena,
    "you": _fetch_you,
}
