from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Optional
from dataclasses import dataclass


@dataclass
class Message:
    role: str
    content: str
    # assistant 回合可能带工具调用；网页站点无法原生处理，
    # 由 tool_emulation 转成可读文本再注入 prompt
    tool_calls: Optional[List[dict]] = None
    # role="tool" 时携带的工具名
    name: Optional[str] = None


@dataclass
class ChatResult:
    content: str
    model: str
    site: str
    tokens_estimated: int = 0
