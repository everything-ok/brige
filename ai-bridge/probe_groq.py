"""探测 Groq 网站的选择器"""
import asyncio
import json
from playwright.async_api import async_playwright

async def probe_groq():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            # 尝试直接访问聊天页面
            print("Testing https://groq.com/chat")
            await page.goto("https://groq.com/chat", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            print("URL:", page.url)
            print("Title:", await page.title())

            # 查找所有输入元素
            print("\nSearching for all input elements...")
            selectors = [
                "textarea",
                "input[type='text']",
                "input[type='search']",
                "[contenteditable='true']",
            ]
            for sel in selectors:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"\n  {sel}: found {len(els)}")
                    for i, el in enumerate(els[:3]):
                        info = await el.evaluate("""el => ({
                            className: el.className || '',
                            id: el.id || '',
                            placeholder: el.placeholder || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            visible: el.offsetParent !== null
                        })""")
                        print(f"    [{i}] {json.dumps(info, ensure_ascii=False)}")

            # 查找按钮
            print("\nSearching for buttons...")
            buttons = await page.query_selector_all("button")
            print(f"Total buttons: {len(buttons)}")
            for i, btn in enumerate(buttons[:10]):
                info = await btn.evaluate("""el => ({
                    text: el.textContent?.trim().slice(0, 50) || '',
                    className: el.className || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    type: el.type || ''
                })""")
                print(f"  [{i}] {json.dumps(info, ensure_ascii=False)}")

            # 查找可能的聊天区域
            print("\nSearching for chat-related elements...")
            chat_selectors = [
                '[class*="chat"]',
                '[class*="message"]',
                '[class*="conversation"]',
                '[class*="prompt"]',
                '[class*="input"]',
            ]
            for sel in chat_selectors:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"  {sel}: found {len(els)}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(probe_groq())
