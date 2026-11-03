import asyncio
import random
import math
from typing import List, Tuple
import numpy as np


def bezier_curve(
    start: Tuple[float, float],
    end: Tuple[float, float],
    num_points: int = 50,
) -> List[Tuple[float, float]]:
    """生成贝塞尔曲线鼠标轨迹"""
    num_control = random.randint(1, 3)
    control_points = [start]

    for _ in range(num_control):
        cx = start[0] + (end[0] - start[0]) * random.uniform(0.2, 0.8)
        cy = start[1] + (end[1] - start[1]) * random.uniform(0.2, 0.8)
        offset_x = random.uniform(-100, 100)
        offset_y = random.uniform(-100, 100)
        control_points.append((cx + offset_x, cy + offset_y))

    control_points.append(end)

    points = []
    n = len(control_points) - 1

    for i in range(num_points):
        t = i / (num_points - 1)
        x, y = 0.0, 0.0

        for j, (px, py) in enumerate(control_points):
            binom = math.comb(n, j)
            weight = binom * (t ** j) * ((1 - t) ** (n - j))
            x += weight * px
            y += weight * py

        points.append((x, y))

    return points


def add_noise_to_path(
    points: List[Tuple[float, float]],
    noise_level: float = 2.0,
) -> List[Tuple[float, float]]:
    """给轨迹添加微小抖动（模拟手抖）"""
    noisy = []
    for x, y in points:
        nx = x + random.gauss(0, noise_level)
        ny = y + random.gauss(0, noise_level)
        noisy.append((nx, ny))
    return noisy


async def human_mouse_move(page, target_x: float, target_y: float, config):
    """人类风格的鼠标移动"""
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 800}
        current_x = random.uniform(0, viewport["width"])
        current_y = random.uniform(0, viewport["height"])

        num_points = random.randint(20, 50)
        path = bezier_curve((current_x, current_y), (target_x, target_y), num_points)
        path = add_noise_to_path(path)

        distance = math.sqrt((target_x - current_x)**2 + (target_y - current_y)**2)
        base_time = config.behavior.mouse_speed_min + (
            config.behavior.mouse_speed_max - config.behavior.mouse_speed_min
        ) * (distance / 1000)

        step_delay = base_time / len(path) / 1000

        for x, y in path:
            await page.mouse.move(x, y)
            await asyncio.sleep(step_delay * random.uniform(0.5, 1.5))

    except Exception:
        try:
            await page.mouse.move(target_x, target_y)
        except Exception:
            pass
