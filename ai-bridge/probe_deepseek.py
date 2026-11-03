"""详细探测 DeepSeek 网站的选择器"""
import asyncio
import json
from playwright.async_api import async_playwright

async def probe_deepseek():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # 非无头模式看实际效果
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            print("Testing https://chat.deepseek.com")
            await page.goto("https://chat.deepseek.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            print(f"URL: {page.url}")
            print(f"Title: {await page.title()}")

            # 查找输入元素
            print("\nSearching for input elements...")
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
                        })""")
                        print(f"    [{i}] {json.dumps(info, ensure_ascii=False)}")

            # 查找按钮
            print("\nButtons:")
            buttons = await page.query_selector_all("button")
            for i, btn in enumerate(buttons[:20]):
                info = await btn.evaluate("""el => ({
                    text: el.textContent?.trim().slice(0, 50) || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    className: el.className || '',
                })""")
                if info['text'] or info['ariaLabel']:
                    print(f"  [{i}] text='{info['text']}', aria={info['ariaLabel'][:50]}")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            await asyncio.sleep(10)
            await browser.close()

if __name__ == "__main__":
    asyncio.run(probe_deepseek())
