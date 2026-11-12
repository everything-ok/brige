"""
网页桥接的 tool-call 模拟层

背景：网页站点（zai / deepseek / qwen ...）的聊天 UI 没有原生 function calling，
无法直接吃 OpenAI 的 tools[] 数组。但 IDE 的 Agent 模式（Continue 的 edit/apply、
Claude Code）必须靠 tool call 才能改代码。

做法（业界通用的 "text-based tool calling"）：
  1. 把 tools[] 的 JSON Schema 渲染成一段自然语言说明，追加到最后一条 user 消息；
  2. 要求模型用固定围栏格式输出工具调用：
        ```tool_call
        {"name": "read_file", "arguments": {"path": "src/a.py"}}
        ```
  3. 从回复文本里解析出围栏内容，转成标准 OpenAI tool_calls 结构。

限制：一次只支持一个工具调用；网页站点无法真正执行工具，执行由 IDE 侧完成，
再把 tool 结果作为新的一轮消息发回来（此时走同样的注入-解析流程）。
"""
import json
import re
from typing import Dict, List, Optional, Tuple

# 围栏格式：```tool_call ... ```（也兼容裸 JSON 行）
_FENCE_RE = re.compile(
    r"```(?:tool_call|tool-call|toolcall)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

# 允许模型用 ```json 包裹工具调用
_FENCE_JSON_RE = re.compile(
    r"```json\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)

# 小模型有时写成 ```JSON（大小写混合）或 ```JSON Object 前缀 → 放宽匹配
_FENCE_JSON_LOOSE_RE = re.compile(
    r"```\s*json(?:[ \t]+object)?[ \t\r\n]*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def render_tool_instructions(tools: List[dict]) -> str:
    """把 tools[] 渲染成给模型看的说明文本"""
    if not tools:
        return ""
    lines = [
        "",
        "--- 可用工具 ---",
        "你可以调用下列工具来完成操作。如果需要调用工具，"
        "请**只**输出一段 JSON（不要有多余解释），格式如下：",
        "",
        "```tool_call",
        '{"name": "工具名", "arguments": {"参数名": "参数值"}}',
        "```",
        "",
        "工具列表：",
    ]
    for t in tools:
        fn = (t or {}).get("function") or {}
        name = fn.get("name", "")
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        required = set(params.get("required") or [])
        arg_desc = []
        for pname, pschema in props.items():
            ptype = (pschema or {}).get("type", "string")
            mark = "*" if pname in required else ""
            pdesc = (pschema or {}).get("description", "")
            arg_desc.append(f"  - {pname}{mark} ({ptype}){': ' + pdesc if pdesc else ''}")
        lines.append(f"- {name}" + (f": {desc}" if desc else ""))
        if arg_desc:
            lines.extend(arg_desc)
        lines.append(f"  完整参数 schema: {json.dumps(params, ensure_ascii=False)}")
    lines.append("")
    lines.append("参数 schema 里带 * 的是必填参数。若不需要调用工具，正常回答即可。")
    lines.append("--- 工具说明结束 ---")
    return "\n".join(lines)


def flatten_tool_messages(messages: List[dict]) -> List[dict]:
    """把 OpenAI 的 tool 角色消息/带 tool_calls 的 assistant 消息转成网页能读的纯文本。

    网页站点不认识 role="tool" 和 assistant.tool_calls，直接转发会丢信息。
    这里：
      · role="tool"                  → user 文本「[工具结果] ...」
      · assistant 带 tool_calls      → user 文本「[已请求调用工具 X，参数 ...]」
    """
    out: List[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            name = ""
            fn = m.get("name") or ""
            content = m.get("content")
            if isinstance(content, list):
                content = "".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            out.append({
                "role": "user",
                "content": f"[工具 {fn} 执行结果]\n{content or ''}",
            })
            continue
        if role == "assistant" and m.get("tool_calls"):
            names = []
            for tc in m.get("tool_calls") or []:
                f = tc.get("function") or {}
                names.append(f"{f.get('name', '')}({f.get('arguments', '')})")
            out.append({
                "role": "user",
                "content": f"[我已请求调用工具: {', '.join(names)}]",
            })
            continue
        out.append(dict(m))
    return out


def inject_tools(messages: List[dict], tools: List[dict]) -> List[dict]:
    """把工具说明注入到最后一条 user 消息末尾（不改动原 message 之外的字段）"""
    if not tools or not messages:
        return messages
    instr = render_tool_instructions(tools)
    if not instr:
        return messages

    out = [dict(m) for m in messages]
    # 找到最后一条 user 消息
    idx = None
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            idx = i
            break
    if idx is None:
        idx = len(out) - 1

    target = out[idx]
    content = target.get("content")
    if isinstance(content, str):
        target["content"] = content + instr
    elif isinstance(content, list):
        # 多模态数组：追加一个 text 块
        target["content"] = list(content) + [{"type": "text", "text": instr}]
    else:
        target["content"] = instr
    return out


def _collect_fence(text: str) -> Optional[dict]:
    """从文本中提取第一个工具调用 JSON 对象"""
    if not text:
        return None
    for rx in (_FENCE_RE, _FENCE_JSON_RE, _FENCE_JSON_LOOSE_RE):
        for m in rx.finditer(text):
            raw = m.group(1).strip()
            try:
                obj = json.loads(raw)
            except Exception:
                # 尝试修复常见的尾逗号
                try:
                    obj = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
                except Exception:
                    continue
            if isinstance(obj, dict) and obj.get("name"):
                return obj
    return None


def parse_tool_call(
    text: str,
    tools: Optional[List[dict]] = None,
) -> Tuple[str, Optional[dict]]:
    """从模型回复里解析工具调用。

    返回 (清理后的文本, 工具调用对象或 None)
    工具调用对象: {"name": str, "arguments": dict}
    """
    call = _collect_fence(text)
    if not call:
        return text, None

    # 校验工具名是否在允许列表里（防止模型幻觉出未知工具）
    name = call.get("name")
    args = call.get("arguments") or call.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"input": args}
    if not isinstance(args, dict):
        args = {"input": args}

    if tools:
        allowed = {((t or {}).get("function") or {}).get("name") for t in tools}
        allowed.discard(None)
        if allowed and name not in allowed:
            # 未知工具 → 当作普通文本，不产生 tool_call
            return text, None

    # 去掉围栏内容，剩下的作为普通文本
    cleaned = _FENCE_RE.sub("", text)
    cleaned = _FENCE_JSON_RE.sub("", cleaned).strip()
    return cleaned, {"name": name, "arguments": args}


def to_openai_tool_call(call: dict, index: int = 0) -> dict:
    """转成 OpenAI tool_calls 结构"""
    return {
        "id": f"call_{index}_{abs(hash(call.get('name', ''))) % 100000}",
        "type": "function",
        "function": {
            "name": call.get("name", ""),
            "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
        },
    }
