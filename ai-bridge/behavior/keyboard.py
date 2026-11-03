import asyncio
import random
from typing import Optional
from .timing import typing_delay, should_make_typo, get_typo_char


async def human_type(
    page,
    selector: str,
    text: str,
    config,
    clear_first: bool = True,
):
    """人类风格打字"""
    element = await page.wait_for_selector(selector, timeout=15000)

    await element.click()
    await asyncio.sleep(random.uniform(0.2, 0.5))

    if clear_first:
        await element.fill("")
        await asyncio.sleep(random.uniform(0.1, 0.3))

    i = 0
    while i < len(text):
        char = text[i]

        if should_make_typo(config) and char.isalpha():
            wrong_char = get_typo_char(char)
            await element.type(wrong_char, delay=0)
            await asyncio.sleep(typing_delay(wrong_char, config))

            await asyncio.sleep(random.uniform(0.1, 0.4))

            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.05, 0.15))

        await element.type(char, delay=0)
        await asyncio.sleep(typing_delay(char, config))

        if random.random() < config.behavior.pause_probability:
            await asyncio.sleep(
                random.uniform(
                    config.behavior.pause_duration_min,
                    config.behavior.pause_duration_max
                )
            )

        i += 1


async def human_type_fast(
    page,
    selector: str,
    text: str,
    config,
):
    """快速但仍像人类的输入（用于长文本）"""
    element = await page.wait_for_selector(selector, timeout=15000)
    await element.click()
    await element.fill("")
    await asyncio.sleep(random.uniform(0.2, 0.5))

    chunk_size = random.randint(30, 80)
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        await element.type(chunk, delay=random.randint(10, 30))
        await asyncio.sleep(random.uniform(0.1, 0.4))
