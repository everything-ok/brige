"""验证 chat.z.ai 登录状态"""
import asyncio
from playwright.async_api import async_playwright

async def check_login():
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
            print("Checking login status at https://chat.z.ai")
            await page.goto("https://chat.z.ai", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            url = page.url
            print(f"Current URL: {url}")

            # 检查是否跳转到登录页
            if "login" in url.lower() or "signin" in url.lower():
                print("❌ 需要登录")
                return False

            # 检查页面内容
            page_text = await page.inner_text("body")
            if "登录" in page_text and "注册" in page_text and len(page_text) < 1000:
                print("❌ 需要登录 - 检测到登录/注册表单")
                return False

            # 检查用户信息元素
            user_elements = await page.query_selector_all("[class*='user' i], [class*='avatar' i], [class*='profile' i]")
            if not user_elements:
                print("⚠️ 未检测到用户信息元素")

            # 获取页面标题
            title = await page.title()
            print(f"✅ 已登录 - 页面标题: {title}")
            return True

        except Exception as e:
            print(f"Error: {e}")
            return False
        finally:
            await asyncio.sleep(2)
            await context.close()

if __name__ == "__main__":
    result = asyncio.run(check_login())
    print(f"\n登录状态: {'已登录' if result else '未登录'}")
