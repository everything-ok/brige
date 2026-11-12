"""
free-gateway + tool_emulation 契约测试。

覆盖：工具调用围栏解析（多种写法）、普通文本不误判、
消息拍平、网关路由分流与错误处理。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent  # 仓库根目录
GW_DIR = ROOT / "free-gateway"
BRIDGE_DIR = ROOT / "ai-bridge"
sys.path.insert(0, str(GW_DIR))
sys.path.insert(0, str(BRIDGE_DIR))

TOOLS = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get weather",
    "parameters": {"type": "object",
                   "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]


# ── 网关侧围栏解析 ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    '```tool_call\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```tool-call\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```toolcall\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```json\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```JSON\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```json object\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '{"name":"get_weather","arguments":{"city":"Beijing"}}',
    '```json\n{"name":"get_weather","arguments":{"city":"Beijing"},}\n```',
    # 裸 JSON + 后面跟解释文字（实测小模型真实输出）
    '{"name": "get_weather", "arguments": {"city": "Beijing"}}\n\n--- 解释 ---\n用了工具\n--- 解释结束 ---',
])
def test_gateway_parses_tool_call_variants(text):
    """各种围栏写法都必须能解析出工具调用（本次修复的回归）"""
    import gateway
    call = gateway._parse_tool_call_text(text)
    assert call is not None, f"未解析出工具调用: {text!r}"
    assert call["name"] == "get_weather"
    assert call["arguments"]["city"] == "Beijing"


@pytest.mark.parametrize("text", [
    "The weather in Beijing is sunny.",
    "```python\nprint('hello')\n```",
    "",
    "```json\n[1,2,3]\n```",
])
def test_gateway_does_not_misparse_plain_text(text):
    """普通文本不能被误判成工具调用"""
    import gateway
    assert gateway._parse_tool_call_text(text) is None


# ── ai-bridge 侧围栏解析 ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    '```tool_call\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```json\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```json object\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
    '```\n{"name":"get_weather","arguments":{"city":"Beijing"}}\n```',
])
def test_tool_emulation_parses_fence_variants(text):
    """ai-bridge/tool_emulation 也要能吃各种围栏"""
    import tool_emulation
    content, call = tool_emulation.parse_tool_call(text, TOOLS)
    assert call is not None, f"未解析出工具调用: {text!r}"
    assert call["name"] == "get_weather"
    assert call["arguments"]["city"] == "Beijing"


def test_tool_emulation_parses_bare_json():
    """裸 JSON（无围栏）必须能识别 —— 本地小模型的实际输出形式。

    实测 local-dolphin-mistral 在网页模式下输出：
        {"name": "get_weather", "arguments": {"city": "Beijing"}}
    修复前会被当成普通文本，IDE 的 Agent 模式完全不可用。
    """
    import tool_emulation
    text = ('{"name": "get_weather", "arguments": {"city": "Beijing"}}\n\n'
            '--- 解释 ---\n用了 "get_weather" 工具...\n--- 解释结束 ---')
    content, call = tool_emulation.parse_tool_call(text, TOOLS)
    assert call is not None, "裸 JSON 工具调用未被识别"
    assert call["name"] == "get_weather"
    assert call["arguments"]["city"] == "Beijing"
    # 正文里不应再残留 JSON
    assert '"name"' not in content
    assert "解释" in content


def test_tool_emulation_bare_json_with_string_arguments():
    """arguments 是 JSON 字符串时要能还原成 dict"""
    import tool_emulation
    text = '{"name": "get_weather", "arguments": "{\\"city\\": \\"Beijing\\"}"}'
    content, call = tool_emulation.parse_tool_call(text, TOOLS)
    assert call is not None
    assert call["arguments"]["city"] == "Beijing"


def test_tool_emulation_bare_json_must_match_known_tool():
    """裸 JSON 里的未知工具名不得产生 tool_call"""
    import tool_emulation
    text = '{"name": "totally_unknown", "arguments": {"x": 1}}'
    content, call = tool_emulation.parse_tool_call(text, TOOLS)
    assert call is None


def test_tool_emulation_plain_text_returns_none():
    import tool_emulation
    content, call = tool_emulation.parse_tool_call("hello there", TOOLS)
    assert call is None
    assert content == "hello there"


def test_tool_emulation_rejects_unknown_tool():
    """幻觉出的未知工具名不得产生 tool_call"""
    import tool_emulation
    text = '```json\n{"name":"rm_rf","arguments":{"path":"/"}}\n```'
    content, call = tool_emulation.parse_tool_call(text, TOOLS)
    assert call is None


def test_tool_emulation_flattens_tool_messages():
    """role=tool 的历史必须拍平成可读文本，否则网页模型看不懂上下文"""
    import tool_emulation
    msgs = [
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "type": "function",
                         "function": {"name": "get_weather",
                                      "arguments": '{"city":"Beijing"}'}}]},
        {"role": "tool", "name": "get_weather", "content": "sunny"},
    ]
    out = tool_emulation.flatten_tool_messages(msgs)
    assert all(m.get("role") != "tool" for m in out)
    assert all(m.get("tool_calls") is None for m in out)
    joined = " ".join(str(m.get("content") or "") for m in out)
    assert "sunny" in joined


def test_tool_emulation_injects_into_last_user_message():
    """工具说明必须注入到最后一条 user 消息"""
    import tool_emulation
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"}]
    out = tool_emulation.inject_tools(msgs, TOOLS)
    assert out[0]["content"] == "sys"
    assert out[1]["content"] == "q1"
    assert "q2" in out[3]["content"]
    assert "get_weather" in out[3]["content"]
    # 原列表不能被就地修改
    assert msgs[3]["content"] == "q2"


def test_tool_emulation_inject_handles_multimodal_content():
    """多模态数组内容不能崩"""
    import tool_emulation
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
    ]}]
    out = tool_emulation.inject_tools(msgs, TOOLS)
    assert isinstance(out[0]["content"], list)
    assert out[0]["content"][-1]["type"] == "text"


def test_tool_emulation_inject_handles_null_content():
    """content 为 None 不能崩"""
    import tool_emulation
    msgs = [{"role": "user", "content": None}]
    out = tool_emulation.inject_tools(msgs, TOOLS)
    assert isinstance(out[0]["content"], str)


def test_tool_emulation_inject_empty_tools_is_noop():
    import tool_emulation
    msgs = [{"role": "user", "content": "q"}]
    assert tool_emulation.inject_tools(msgs, []) == msgs
    assert tool_emulation.inject_tools([], TOOLS) == []


# ── 网关模块契约 ──────────────────────────────────────────────

def test_gateway_module_importable():
    import gateway
    assert hasattr(gateway, "app")


def test_ollama_base_configured():
    import gateway
    assert gateway.OLLAMA_BASE
    assert gateway.OLLAMA_BASE.startswith("http")


def test_gateway_exposes_required_routes():
    import gateway
    paths = set()
    for r in gateway.app.routes:
        p = getattr(r, "path", None)
        if p:
            paths.add(p)
    for need in ("/v1/models", "/health", "/v1/chat/completions",
                 "/local-models", "/local-models/status", "/market/refresh"):
        assert need in paths, f"缺少路由: {need}"


def test_gateway_to_openai_tool_call_shape():
    import gateway
    raw = gateway._sse_tool_calls(
        [{"id": "c1", "type": "function",
          "function": {"name": "f", "arguments": "{}"}}], "m").decode()
    assert raw.startswith("data: ")
    data = json.loads(raw[len("data: "):].strip())
    assert data["object"] == "chat.completion.chunk"
    assert data["choices"][0]["delta"]["tool_calls"]
    assert data["choices"][0]["finish_reason"] == "tool_calls"


def test_gateway_sse_done_marker():
    """流式结束必须有 [DONE] 标记，否则 IDE 端会一直等"""
    import gateway
    raw = gateway._sse_done().decode()
    assert "data: [DONE]" in raw


# ── 「函数名: {参数}」形式（实测小模型真实输出）────────────────
#
# 实测 local-dolphin-mistral 在工具调用时经常输出：
#     get_weather: {"city": "Beijing"}
# 这种格式既没有 ```tool_call 围栏，也没有 "name" 字段，
# 修复前所有正则都匹配不到 → 工具调用退化成普通文本，
# IDE 的 Agent 模式直接不可用（live_e2e 实测复现）。

@pytest.mark.parametrize("text", [
    'get_weather: {"city": "Beijing"}',
    'get_weather({"city": "Beijing"})',
    'get_weather {"city": "Beijing"}',
    '"get_weather": {"city": "Beijing"}',
])
def test_gateway_parses_named_args_format(text):
    """网关侧必须能识别「工具名: {参数}」"""
    import gateway
    call = gateway._parse_tool_call_text(text)
    assert call is not None, f"未解析出工具调用: {text!r}"
    assert call["name"] == "get_weather"
    assert call["arguments"]["city"] == "Beijing"


@pytest.mark.parametrize("text", [
    'get_weather: {"city": "Beijing"}',
    'get_weather({"city": "Beijing"})',
])
def test_tool_emulation_parses_named_args_format(text):
    """ai-bridge 侧必须能识别「工具名: {参数}」"""
    import tool_emulation
    content, call = tool_emulation.parse_tool_call(text, TOOLS)
    assert call is not None, f"未解析出工具调用: {text!r}"
    assert call["name"] == "get_weather"
    assert call["arguments"]["city"] == "Beijing"
    # 整段就是一个调用时，正文不应残留原始文本
    assert "get_weather" not in content


@pytest.mark.parametrize("text", [
    "Here is the result: {\"a\": 1}",
    "I found the weather: {\"city\": \"Beijing\"}",
    "Let me check the weather for you.",
    "{\"data\": {\"a\": 1}}",
])
def test_named_args_format_does_not_misparse_prose(text):
    """正文里的「xxx: {...}」不能被误判成工具调用"""
    import gateway
    assert gateway._parse_tool_call_text(text) is None


def test_tool_emulation_named_args_rejects_unknown_tool():
    """幻觉出的工具名必须被白名单拦掉，退化成普通文本"""
    import tool_emulation
    content, call = tool_emulation.parse_tool_call(
        'evil_tool: {"cmd": "rm -rf /"}', TOOLS)
    assert call is None, "未知工具不应产生 tool_call"
    assert "evil_tool" in content


# ── /local-models/add 输入校验 ──────────────────────────────────
#
# key（模型 ID）会出现在 /v1/models 的 id 与管理页 innerHTML 里。
# 未校验时 display 里塞 "<script>" 就是存储型 XSS，
# 且 key 还能撞掉内置模型（auto / llm7-* / or/*）。

def test_local_models_add_rejects_xss_display_name():
    """display 含脚本标签必须被拒绝"""
    import gateway
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    resp = client.post("/local-models/add", json={
        "ollama_id": "dolphin-mistral:latest",
        "display": "<script>alert(1)</script>",
    })
    assert resp.status_code == 400, f"未拒绝 XSS display: {resp.status_code}"
    assert "<script>" not in resp.text


def test_local_models_add_rejects_builtin_name_collision():
    """模型名不能撞掉内置模型（auto / llm7-* / or/*）"""
    import gateway
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    for bad in ("auto", "llm7-gpt4", "or/meta-llama/llama-3"):
        resp = client.post("/local-models/add", json={
            "ollama_id": "dolphin-mistral:latest", "display": bad})
        assert resp.status_code == 400, f"未拒绝冲突名 {bad}: {resp.status_code}"


def test_local_models_add_rejects_malformed_ollama_id():
    """ollama_id 畸形必须被拒绝"""
    import gateway
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    for bad in ("a" * 200, "model; rm -rf /", "model with space"):
        resp = client.post("/local-models/add", json={"ollama_id": bad})
        assert resp.status_code == 400, f"未拒绝非法 ollama_id {bad!r}"


def test_local_models_add_rejects_empty_ollama_id():
    """空 ollama_id 必须 400"""
    import gateway
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    assert client.post("/local-models/add", json={"ollama_id": ""}).status_code == 400


def test_local_models_add_rejects_invalid_json():
    """非法 JSON 必须 400，不能 500"""
    import gateway
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    resp = client.post("/local-models/add", content=b"{not json",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
