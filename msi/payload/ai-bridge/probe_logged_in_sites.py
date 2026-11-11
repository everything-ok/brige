"""探测已登录站点的选择器"""
import asyncio
import json
from playwright.async_api import async_playwright

SITES = [
    ("https://chat.z.ai", "zai", "GLM"),
    ("https://chatgpt.com", "chatgpt", "ChatGPT"),
    ("https://arena.ai/code", "arena", "Code Arena"),
    ("https://grok.com", "grok", "Grok"),
    ("https://www.doubao.com/chat", "doubao", "豆包"),
    ("https://happycapy.ai/app", "happycapy", "Happycapy"),
    ("https://poe.com", "poe", "Poe"),
]

async def probe_site(url, name, display_name, profile_dir=None):
    async with async_playwright() as p:
        # 使用已有 profile 或创建新的
        if profile_dir:
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = await context.new_page()

        try:
            print(f"\n{'='*70}")
            print(f"Testing {display_name}: {url}")
            print('='*70)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            print(f"URL: {page.url}")
            print(f"Title: {await page.title()}")

            # 查找输入元素
            print("\n[Input Elements]")
            selectors = [
                "textarea",
                "input[type='text']",
                "input[type='search']",
                "[contenteditable='true']",
            ]
            found_inputs = []
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
                            name: el.name || '',
                            visible: el.offsetParent !== null,
                            disabled: el.disabled
                        })""")
                        if info['visible'] and not info['disabled']:
                            print(f"    [{i}] class='{info['className'][:80]}'")
                            print(f"        placeholder='{info['placeholder'][:60]}'")
                            print(f"        aria-label='{info['ariaLabel'][:60]}'")
                            found_inputs.append({
                                'selector': sel,
                                'className': info['className'],
                                'placeholder': info['placeholder'],
                                'ariaLabel': info['ariaLabel']
                            })

            # 查找发送按钮
            print("\n[Buttons with send/submit]")
            buttons = await page.query_selector_all("button")
            send_buttons = []
            for i, btn in enumerate(buttons[:20]):
                info = await btn.evaluate("""el => ({
                    text: el.textContent?.trim().slice(0, 40) || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    className: el.className || '',
                    type: el.type || 'button'
                })""")
                if any(k in (info['text'] + info['ariaLabel'] + info['className']).lower()
                       for k in ['send', 'submit', 'arrow', '发送', 'arrowright', 'up']):
                    print(f"  [{i}] text='{info['text']}', aria='{info['ariaLabel']}'")
                    print(f"      class='{info['className'][:60]}', type='{info['type']}'")
                    send_buttons.append(info)

            # 查找响应区域
            print("\n[Response Areas]")
            response_selectors = [
                "[class*='message'][class*='assistant']",
                "[class*='prose']",
                "[class*='markdown']",
                "[class*='content']",
                "[data-message-author-role='assistant']",
            ]
            for sel in response_selectors:
                els = await page.query_selector_all(sel)
                if els:
                    visible_els = [el for el in els if await el.evaluate("e => e.offsetParent !== null")]
                    if visible_els:
                        print(f"  {sel}: {len(visible_els)} visible")

            # 生成 YAML 配置
            if found_inputs:
                inp = found_inputs[0]
                print(f"\n[Suggested YAML Config for {name}]")
                placeholder = inp['placeholder'].lower()
                input_sel = f"textarea[placeholder*='{placeholder[:20]}']" if placeholder else "textarea"
                print(f"""  - name: {name}
    enabled: true
    url: "{url}"
    priority: 1

    selectors:
      input: '{input_sel}'
      send_button: 'button[type="submit"], button[aria-label*="send" i]'
      response: '[class*="message" i][class*="assistant" i], [class*="prose"], [class*="markdown"]'
      stop_button: 'button[aria-label*="stop" i]'

    models:
      {name}-default: "default"
""")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if profile_dir:
                await context.close()
            else:
                await browser.close()

async def main():
    import sys
    site_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    for i, (url, name, display) in enumerate(SITES):
        if i < site_index:
            continue
        await probe_site(url, name, display)
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
