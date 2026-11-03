import random
import numpy as np
from typing import Tuple


def gaussian_delay(mean: float, std: float, min_val: float = 0.01) -> float:
    """高斯分布延迟"""
    delay = random.gauss(mean, std)
    return max(min_val, delay)


def exponential_delay(scale: float) -> float:
    """指数分布延迟（模拟思考时间）"""
    return random.expovariate(1.0 / scale)


def human_reading_time(text_length: int, wpm: int = 200) -> float:
    """根据文本长度计算人类阅读时间"""
    words = text_length / 5
    base_time = (words / wpm) * 60
    return base_time * random.uniform(0.8, 1.3)


def typing_delay(char: str, config) -> float:
    """单字符打字延迟（模拟真人打字节奏）"""
    base_min = config.behavior.typing_speed_min_ms / 1000
    base_max = config.behavior.typing_speed_max_ms / 1000

    delay = random.uniform(base_min, base_max)

    if char in '.,!?;:':
        delay *= random.uniform(1.5, 2.5)
    elif char == ' ':
        delay *= random.uniform(0.8, 1.2)
    elif char == '\n':
        delay *= random.uniform(2.0, 4.0)
    elif char.isupper():
        delay *= random.uniform(1.1, 1.4)

    if random.random() < 0.03:
        delay += random.uniform(0.3, 1.5)

    return delay


def should_make_typo(config) -> bool:
    """是否产生打字错误"""
    return random.random() < config.behavior.typing_mistake_rate


def get_typo_char(char: str) -> str:
    """生成打字错误字符"""
    keyboard_neighbors = {
        'a': 'qwsz', 'b': 'vghn', 'c': 'xdfv', 'd': 'erfcxs',
        'e': 'wrsdf', 'f': 'rtgvcd', 'g': 'tyhbvf', 'h': 'yujnbg',
        'i': 'uojkl', 'j': 'uikmnh', 'k': 'ioljm', 'l': 'opk',
        'm': 'njk', 'n': 'bhjm', 'o': 'iplk', 'p': 'ol',
        'q': 'wa', 'r': 'etdf', 's': 'wedxza', 't': 'ryfg',
        'u': 'yihj', 'v': 'cfgb', 'w': 'qeas', 'x': 'zsdc',
        'y': 'tugh', 'z': 'asx',
    }
    lower = char.lower()
    if lower in keyboard_neighbors:
        typo = random.choice(keyboard_neighbors[lower])
        return typo.upper() if char.isupper() else typo
    return char
