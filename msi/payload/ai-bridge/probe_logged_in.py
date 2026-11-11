"""探测和测试已登录站点的选择器"""
import asyncio
import json
from playwright.async_api import async_playwright

SITES = [
    ("https://chat.z.ai", "zai", "Z.ai (GLM)"),
    ("https://chatgpt.com", "chatgpt", "ChatGPT"),
    ("https://arena.ai/code", "arena", "Code Arena"),
    ("https://grok.com", "grok", "Grok"),
    ("https://www.doubao.com/chat", "doubao", "豆包"),
    ("https://happycapy.ai/app", "happycapy", "Happycapy"),
    ("https://poe.com", "poe", "Poe"),
]

PROFILES_BASE = r"d:\project\桥接\ai-bridge\profiles"


async def probe_site(url, name, display_name):
    """探测单个站点的选择器"""
    profile_dir = f"{PROFILES_BASE}\\{name}"

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
        except Exception as e:
            print(f"  无法使用 profile {name}: {e}")
            print(f"  创建新浏览器上下文...")
            context = await p.chromium.launch(headless=False)
            context = await context.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )

        page = await context.new_page()

        try:
            print(f"\n{'='*70}")
            print(f"🔍 探测 {display_name}: {url}")
            print(f"   Profile: {name}")
            print('='*70)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            print(f"📍 URL: {page.url}")
            print(f"📄 Title: {await page.title()}")

            # 查找输入元素
            print("\n📝 输入元素:")
            found_inputs = []
            selectors = [
                ("textarea", "textarea"),
                ("input[type='text']", "input[type='text']"),
                ("input[type='search']", "input[type='search']"),
                ("[contenteditable]", "[contenteditable='true']"),
            ]
            for sel_name, sel in selectors:
                els = await page.query_selector_all(sel)
                if els:
                    for el in els:
                        try:
                            visible = await el.is_visible()
                            if visible:
                                info = await el.evaluate("""el => ({
                                    className: el.className || '',
                                    id: el.id || '',
                                    placeholder: el.placeholder || '',
                                    ariaLabel: el.getAttribute('aria-label') || '',
                                    name: el.name || '',
                                    disabled: el.disabled
                                })""")
                                print(f"  [{sel_name}]")
                                print(f"      class: '{info['className'][:80]}'")
                                print(f"      placeholder: '{info['placeholder'][:50]}'")
                                print(f"      aria-label: '{info['ariaLabel'][:50]}'")
                                found_inputs.append({
                                    'type': sel_name,
                                    'className': info['className'],
                                    'placeholder': info['placeholder'],
                                    'ariaLabel': info['ariaLabel']
                                })
                        except Exception:
                            pass

            # 查找按钮
            print("\n🔘 按钮:")
            buttons = await page.query_selector_all("button")
            send_buttons = []
            for btn in buttons:
                try:
                    info = await btn.evaluate("""el => ({
                        text: el.textContent?.trim().slice(0, 40) || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        className: el.className || '',
                        type: el.type || 'button'
                    })""")
                    if any(k in (info['text'] + info['ariaLabel'] + info['className']).lower()
                           for k in ['send', 'submit', 'arrow', 'up', '发送', 'go', 'chat']):
                        print(f"  - text='{info['text']}'")
                        print(f"    aria='{info['ariaLabel']}'")
                        print(f"    class='{info['className'][:60]}'")
                        send_buttons.append(info)
                except Exception:
                    pass

            # 查找响应区域
            print("\n💬 响应区域:")
            response_selectors = [
                "[class*='message'][class*='bot' i]",
                "[class*='message'][class*='assistant' i]",
                "[class*='prose']",
                "[class*='markdown']",
                "[class*='content']",
            ]
            for sel in response_selectors:
                els = await page.query_selector_all(sel)
                visible_count = 0
                for el in els:
                    try:
                        if await el.is_visible():
                            visible_count += 1
                    except Exception:
                        pass
                if visible_count > 0:
                    print(f"  {sel}: {visible_count} visible")

            # 生成建议配置
            if found_inputs:
                print(f"\n📋 建议 YAML 配置:")
                inp = found_inputs[0]
                ph = inp['placeholder'].lower()
                if inp['className']:
                    cls = inp['className']
                    # 提取可能的 class 选择器
                    classes = cls.split()[:3]
                    input_sel = f"textarea.{classes[0]}" if classes else "textarea"
                elif ph:
                    input_sel = f"textarea[placeholder*='{ph[:15]}']"
                else:
                    input_sel = "textarea"

                print(f"""
  - name: {name}
    enabled: true
    url: "{url}"
    priority: 1

    selectors:
      input: '{input_sel}'
      send_button: 'button[type="submit"], button[aria-label*="send" i]'
      response: '[class*="message" i][class*="bot" i], [class*="prose"], [class*="markdown"]'
      stop_button: 'button[aria-label*="stop" i]'

    models:
      {name}-default: "default"
""")

        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await context.close()


async def main():
    import sys
    site_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    for i, (url, name, display) in enumerate(SITES):
        if i < site_index:
            continue
        await probe_site(url, name, display)
        await asyncio.sleep(1)

    print("\n" + "="*70)
    print("✅ 探测完成!")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())
