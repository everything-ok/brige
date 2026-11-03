from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Optional
from dataclasses import dataclass


@dataclass
class Message:
    role: str
    content: str


@dataclass
class ChatResult:
    content: str
    model: str
    site: str
    tokens_estimated: int = 0
