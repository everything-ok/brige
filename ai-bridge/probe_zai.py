"""详细探测 chat.z.ai 网站的 DOM 结构"""
import asyncio
import json
from playwright.async_api import async_playwright

async def probe_zai():
    async with async_playwright() as p:
        # 使用已有的 zai profile
        profile_path = r"d:\project\桥接\ai-bridge\profiles\zai"
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            print("Testing https://chat.z.ai")
            await page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            print(f"URL: {page.url}")
            print(f"Title: {await page.title()}")

            # 查找输入元素
            print("\n=== 搜索输入元素 ===")
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
                            dataTestid: el.getAttribute('data-testid') || '',
                            name: el.getAttribute('name') || '',
                        })""")
                        print(f"    [{i}] {json.dumps(info, ensure_ascii=False)}")

            # 查找按钮
            print("\n=== 按钮列表 ===")
            buttons = await page.query_selector_all("button")
            for i, btn in enumerate(buttons[:30]):
                info = await btn.evaluate("""el => ({
                    text: el.textContent?.trim().slice(0, 80) || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    className: el.className || '',
                    dataTestid: el.getAttribute('data-testid') || '',
                })""")
                if info['text'] or info['ariaLabel']:
                    print(f"  [{i}] text='{info['text']}' aria={info['ariaLabel'][:50] if info['ariaLabel'] else 'none'}")

            # 查找模型选择器
            print("\n=== 查找模型选择器 ===")
            model_selectors = [
                "[class*='model' i]",
                "[data-testid*='model' i]",
                "[role='combobox']",
                "[role='listbox']",
                "select",
                "[aria-label*='model' i]",
                "[aria-label*='Model' i]",
            ]
            for sel in model_selectors:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"\n  {sel}: found {len(els)}")
                    for i, el in enumerate(els[:5]):
                        info = await el.evaluate("""el => ({
                            className: el.className || '',
                            id: el.id || '',
                            text: el.textContent?.trim().slice(0, 100) || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            role: el.getAttribute('role') || '',
                        })""")
                        print(f"    [{i}] {json.dumps(info, ensure_ascii=False)}")

            # 查找消息容器
            print("\n=== 消息容器 ===")
            msg_selectors = [
                "[class*='message' i]",
                "[class*='chat' i][class*='container' i]",
                "[class*='response' i]",
                "[class*='markdown' i]",
                "[class*='content' i]",
            ]
            for sel in msg_selectors:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"\n  {sel}: found {len(els)}")
                    for i, el in enumerate(els[:3]):
                        info = await el.evaluate("""el => ({
                            className: el.className || '',
                            id: el.id || '',
                            text: el.textContent?.trim().slice(0, 100) || '',
                            childCount: el.children?.length || 0,
                        })""")
                        print(f"    [{i}] {json.dumps(info, ensure_ascii=False)}")

            # 查找发送按钮
            print("\n=== 发送按钮 ===")
            send_btns = await page.query_selector_all("button")
            for btn in send_btns:
                info = await btn.evaluate("""el => ({
                    text: el.textContent?.trim().slice(0, 50) || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    className: el.className || '',
                    type: el.type || '',
                })""")
                if 'send' in info['className'].lower() or 'send' in info['ariaLabel'].lower() or 'submit' in info['className'].lower():
                    print(f"  发送按钮候选: {json.dumps(info, ensure_ascii=False)}")

            # 尝试获取完整的 HTML 结构截图
            print("\n=== 页面结构快照 ===")
            # 获取 body 下的直接子元素
            body_children = await page.evaluate("""() => {
                const body = document.body;
                const children = [];
                for (let child of body.children) {
                    children.push({
                        tag: child.tagName,
                        id: child.id,
                        className: child.className?.slice(0, 100) || '',
                        childCount: child.children.length
                    });
                }
                return children;
            }""")
            print(f"Body 直接子元素: {json.dumps(body_children, ensure_ascii=False, indent=2)}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await asyncio.sleep(10)
            await context.close()

if __name__ == "__main__":
    asyncio.run(probe_zai())
