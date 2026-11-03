import asyncio
import random
from .mouse import human_mouse_move
from .keyboard import human_type, human_type_fast
from .timing import human_reading_time, gaussian_delay


class HumanBehavior:
    """人类行为模拟器"""

    def __init__(self, page, config):
        self.page = page
        self.config = config

    async def random_scroll(self):
        """随机滚动页面"""
        if random.random() > self.config.behavior.scroll_probability:
            return

        direction = random.choice([-1, 1])
        amount = random.randint(100, 400) * direction
        await self.page.mouse.wheel(0, amount)
        await asyncio.sleep(random.uniform(0.3, 1.0))

    async def random_pause(self):
        """随机暂停（模拟思考/阅读）"""
        if random.random() > self.config.behavior.pause_probability:
            return

        duration = random.uniform(
            self.config.behavior.pause_duration_min,
            self.config.behavior.pause_duration_max,
        )
        await asyncio.sleep(duration)

    async def read_response(self, text_length: int):
        """模拟阅读响应内容"""
        read_time = human_reading_time(text_length)
        await asyncio.sleep(min(read_time, 5.0))

    async def move_to_element(self, selector: str):
        """移动鼠标到元素"""
        try:
            element = await self.page.wait_for_selector(selector, timeout=5000)
            box = await element.bounding_box()
            if box:
                x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
                await human_mouse_move(self.page, x, y, self.config)
        except Exception:
            pass

    async def click_element(self, selector: str):
        """人类风格点击元素"""
        await self.move_to_element(selector)
        await asyncio.sleep(random.uniform(0.05, 0.2))
        element = await self.page.query_selector(selector)
        if element:
            await element.click()
        await asyncio.sleep(random.uniform(0.1, 0.3))

    async def type_text(self, selector: str, text: str, fast: bool = False):
        """人类风格输入文本"""
        await self.move_to_element(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))

        if fast or len(text) > 500:
            await human_type_fast(self.page, selector, text, self.config)
        else:
            await human_type(self.page, selector, text, self.config)

    async def before_action(self):
        """执行动作前的随机行为"""
        await self.random_scroll()
        await self.random_pause()

    async def after_action(self):
        """执行动作后的随机行为"""
        await asyncio.sleep(random.uniform(0.3, 1.0))
        await self.random_scroll()
