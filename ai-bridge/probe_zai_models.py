"""探测 chat.z.ai 的模型列表"""
import asyncio
import json
from playwright.async_api import async_playwright

async def probe_zai_models():
    async with async_playwright() as p:
        profile_path = r"d:\project\桥接\ai-bridge\profiles\zai"
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            print("Navigating to https://chat.z.ai")
            await page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # 点击模型选择器
            print("\n=== 点击模型选择器 ===")
            model_btn = await page.query_selector("#model-selector-glm-5_2-button")
            if model_btn:
                await model_btn.click()
                await asyncio.sleep(1)
                print("已点击模型选择器")

            # 等待模型列表出现
            await asyncio.sleep(2)

            # 查找模型列表弹窗/下拉
            print("\n=== 查找模型列表 ===")
            # 尝试多种选择器
            list_selectors = [
                "[role='listbox']",
                "[role='option']",
                "[class*='model' i]",
                "[class*='dropdown' i]",
                "[class*='select' i]",
                "[class*='menu' i]",
            ]
            models = []
            for sel in list_selectors:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"\n{sel}: found {len(els)}")
                    for i, el in enumerate(els[:20]):
                        info = await el.evaluate("""el => ({
                            text: el.textContent?.trim().slice(0, 100) || '',
                            role: el.getAttribute('role') || '',
                            className: el.className?.slice(0, 100) || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                        })""")
                        if info['text'] and len(info['text']) > 1:
                            print(f"  [{i}] role={info['role']}, text='{info['text']}', class='{info['className'][:60]}'")
                            if info['text'] not in models:
                                models.append(info['text'])

            # 尝试从页面 DOM 提取模型名称
            print("\n=== 搜索页面中的模型文本 ===")
            all_text = await page.evaluate("""() => {
                const text = document.body.innerText;
                // 搜索 GLM 相关文本
                const glmMatches = text.match(/GLM[-\\s]?\\d+[\\.\\d]*(?:[- ]?(?:Flash|Plus|Pro|Air|AirX|V|7B|32B)?)/gi);
                return glmMatches ? [...new Set(glmMatches)] : [];
            }""")
            print(f"找到的 GLM 模型: {json.dumps(all_text, ensure_ascii=False)}")

            # 关闭弹窗
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)

            # 再探查一次 - 检查完整的模型列表弹窗
            print("\n=== 再次点击并获取完整列表 ===")
            if model_btn:
                await model_btn.click()
                await asyncio.sleep(1.5)

                # 获取弹窗内容
                modal_info = await page.evaluate("""() => {
                    const modals = document.querySelectorAll('[class*="modal" i], [class*="dropdown" i], [class*="select" i], [class*="option" i]');
                    const results = [];
                    modals.forEach(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.push({
                                tag: el.tagName,
                                className: el.className?.slice(0, 100) || '',
                                text: el.textContent?.trim().slice(0, 200) || '',
                                visible: rect.width > 0 && rect.height > 0
                            });
                        }
                    });
                    return results;
                }""")
                for m in modal_info[:10]:
                    if m['visible'] and m['text']:
                        print(f"可见元素: {json.dumps(m, ensure_ascii=False)}")

            # 尝试直接获取模型列表
            print("\n=== 获取模型选择下拉框内容 ===")
            dropdown_content = await page.evaluate("""() => {
                // 查找包含 "GLM" 的所有元素
                const allElements = document.querySelectorAll('*');
                const models = [];
                allElements.forEach(el => {
                    const text = el.textContent?.trim() || '';
                    if (text.match(/^GLM[-\\s]?\\d+/) && text.length < 50 && el.children.length < 5) {
                        models.push(text);
                    }
                });
                return [...new Set(models)].slice(0, 30);
            }""")
            print(f"模型列表: {json.dumps(dropdown_content, ensure_ascii=False)}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await asyncio.sleep(5)
            await context.close()

if __name__ == "__main__":
    asyncio.run(probe_zai_models())
