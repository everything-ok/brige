"""探测 DeepSeek 网站的选择器"""
import asyncio
import json
from playwright.async_api import async_playwright

async def probe_site(url, name):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            print(f"\n{'='*60}")
            print(f"Testing {name}: {url}")
            print('='*60)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            print(f"URL: {page.url}")
            print(f"Title: {await page.title()}")

            # 查找输入元素
            print("\nInput elements:")
            selectors = [
                "textarea",
                "input[type='text']",
                "[contenteditable='true']",
            ]
            for sel in selectors:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"\n  {sel}: found {len(els)}")
                    for i, el in enumerate(els[:5]):
                        info = await el.evaluate("""el => ({
                            className: el.className || '',
                            id: el.id || '',
                            placeholder: el.placeholder || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            visible: el.offsetParent !== null
                        })""")
                        print(f"    [{i}] class={info['className'][:80]}, placeholder={info['placeholder'][:50]}")

            # 查找按钮
            print("\nButtons:")
            buttons = await page.query_selector_all("button")
            for i, btn in enumerate(buttons[:15]):
                info = await btn.evaluate("""el => ({
                    text: el.textContent?.trim().slice(0, 40) || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                })""")
                if info['text'] or info['ariaLabel']:
                    print(f"  [{i}] text='{info['text']}', aria={info['ariaLabel'][:50]}")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()

async def main():
    sites = [
        ("https://chat.deepseek.com", "DeepSeek"),
        ("https://huggingface.co/chat", "HuggingChat"),
        ("https://chat.mistral.ai", "Mistral"),
    ]
    for url, name in sites:
        await probe_site(url, name)
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
