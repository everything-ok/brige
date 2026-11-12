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


# ── 网关 → 桥接 的鉴权 Key 同步 ───────────────────────────────
#
# 网关与桥接是两个进程、两个 .env 文件。管理员改了 ai-bridge/.env 的
# API_KEY 后，网关的 .env 没同步 → 网关回调桥接被 401，
# 表现为"本地模型正常、一走网页站点就失败"。

def test_read_env_key_reads_bridge_env(tmp_path):
    """网关必须能读到 ai-bridge/.env 里的 API_KEY"""
    import gateway
    bridge_env = tmp_path / "ai-bridge" / ".env"
    bridge_env.parent.mkdir(parents=True)
    bridge_env.write_text("# c\nAPI_KEY=bridge-secret\n", encoding="utf-8")
    gw_env = tmp_path / "free-gateway" / ".env"
    gw_env.parent.mkdir(parents=True)
    gw_env.write_text("GATEWAY_PORT=8787\n", encoding="utf-8")

    assert gateway._read_env_key("API_KEY", base_dir=gw_env.parent) == "bridge-secret"
    assert gateway._read_env_key("NOT_THERE", base_dir=gw_env.parent) == ""


def test_read_env_key_skips_comments_and_quotes(tmp_path):
    """注释行不能被当成键；引号要被剥掉"""
    import gateway
    gw_dir = tmp_path / "free-gateway"
    gw_dir.mkdir(parents=True)
    (gw_dir / ".env").write_text(
        '# API_KEY=commented\nAPI_KEY="quoted-key"\n', encoding="utf-8")
    assert gateway._read_env_key("API_KEY", base_dir=gw_dir) == "quoted-key"


def test_bridge_key_candidates_include_bridge_env(monkeypatch):
    """候选 Key 列表必须包含从 ai-bridge/.env 读到的值"""
    import gateway
    monkeypatch.setattr(gateway, "_read_env_key",
                        lambda name, base_dir=None:
                        "from-bridge-env" if name == "API_KEY" else "")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("BRIDGE_API_KEY", raising=False)
    candidates = gateway._build_bridge_key_candidates()
    assert "from-bridge-env" in candidates


def test_bridge_key_candidates_are_deduped(monkeypatch):
    """候选 Key 不能重复（否则 401 重试会白试同一个值）"""
    import gateway
    monkeypatch.setattr(gateway, "_read_env_key",
                        lambda name, base_dir=None: "same-key")
    monkeypatch.setenv("API_KEY", "same-key")
    monkeypatch.setenv("BRIDGE_API_KEY", "same-key")
    candidates = gateway._build_bridge_key_candidates()
    assert candidates.count("same-key") == 1


def test_bridge_stream_retries_with_next_key_on_401(monkeypatch):
    """第一个 Key 拿到 401 时必须换下一个候选 Key 重试，而不是直接失败"""
    import asyncio
    import gateway

    seen_keys = []

    class FakeResp:
        def __init__(self, status):
            self.status_code = status
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def aiter_lines(self):
            if False:
                yield ""

    class FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def stream(self, method, url, json=None, headers=None):
            key = (headers or {}).get("Authorization", "")
            seen_keys.append(key)
            return FakeResp(401 if len(seen_keys) == 1 else 200)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(gateway, "_BRIDGE_KEY_CANDIDATES", ["bad-key", "good-key"])

    async def run():
        out = []
        async for kind, payload in gateway._stream_bridge("poe", {"messages": []}):
            out.append((kind, payload))
        return out

    asyncio.run(run())
    assert seen_keys == ["Bearer bad-key", "Bearer good-key"], seen_keys


# ── 本地模型并发闸门 ──────────────────────────────────────────
#
# Ollama 在无 GPU 机器上是 CPU 推理，一次只跑一个请求。
# 没有闸门时并发请求会无限排队，客户端全部超时（实测 4 并发 3 个超时）。

def test_ollama_concurrency_gate_rejects_over_limit(monkeypatch):
    """在途数达上限且队列满时必须直接拒绝，而不是无限堆积"""
    import asyncio
    import gateway
    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_QUEUE", 0)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_WAIT", 0.05)
    gateway._ollama_state["inflight"] = 0
    gateway._ollama_state["queued"] = 0

    async def run():
        assert await gateway._ollama_acquire_slot() is True
        # 在途已满 + 不允许排队 → 必须拒绝
        assert await gateway._ollama_acquire_slot() is False
        await gateway._ollama_release()
        # 释放后又能拿
        assert await gateway._ollama_acquire_slot() is True
        await gateway._ollama_release()

    asyncio.run(run())


def test_ollama_concurrency_gate_zero_disables_limit(monkeypatch):
    """上限为 0 表示不限制"""
    import asyncio
    import gateway
    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 0)

    async def run():
        for _ in range(5):
            assert await gateway._ollama_acquire_slot() is True

    asyncio.run(run())


def test_ollama_concurrency_gate_waits_for_slot(monkeypatch):
    """队列允许时应该等待，而不是立刻拒绝"""
    import asyncio
    import gateway
    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_QUEUE", 4)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_WAIT", 5)
    gateway._ollama_state["inflight"] = 0
    gateway._ollama_state["queued"] = 0

    async def run():
        assert await gateway._ollama_acquire_slot() is True

        async def releaser():
            await asyncio.sleep(0.3)
            await gateway._ollama_release()

        t = asyncio.create_task(releaser())
        # 第二个应该等到释放后拿到名额
        assert await gateway._ollama_acquire_slot() is True
        await gateway._ollama_release()
        await t

    asyncio.run(run())


def test_ollama_concurrency_gate_times_out_waiting(monkeypatch):
    """等待超过上限必须返回 False（而不是一直挂着）"""
    import asyncio
    import gateway
    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_QUEUE", 4)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_WAIT", 0.3)
    gateway._ollama_state["inflight"] = 0
    gateway._ollama_state["queued"] = 0

    async def run():
        assert await gateway._ollama_acquire_slot() is True
        assert await gateway._ollama_acquire_slot() is False
        await gateway._ollama_release()

    asyncio.run(run())


def test_ollama_stream_raises_busy_when_gate_full(monkeypatch):
    """过载时 _stream_ollama 必须抛 _OllamaBusy"""
    import asyncio
    import gateway
    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_QUEUE", 0)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_WAIT", 0.05)
    gateway._ollama_state["inflight"] = 0
    gateway._ollama_state["queued"] = 0

    async def run():
        # 先占满名额
        assert await gateway._ollama_acquire_slot() is True
        try:
            async for _ in gateway._stream_ollama("dolphin-mistral:latest", {}):
                pass
        finally:
            await gateway._ollama_release()

    with pytest.raises(gateway._OllamaBusy):
        asyncio.run(run())


def test_ollama_stream_releases_slot_on_failure(monkeypatch):
    """异常退出也必须释放名额，否则闸门会被永久占死"""
    import asyncio
    import gateway
    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_QUEUE", 4)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_WAIT", 1)
    gateway._ollama_state["inflight"] = 0
    gateway._ollama_state["queued"] = 0

    class Boom(Exception):
        pass

    async def fake_inner(model, body):
        raise Boom("upstream down")
        yield  # pragma: no cover

    monkeypatch.setattr(gateway, "_stream_ollama_inner", fake_inner)

    async def run():
        async for _ in gateway._stream_ollama("m", {}):
            pass

    with pytest.raises(Boom):
        asyncio.run(run())
    # 名额必须已归还（在途计数归零）
    assert gateway._ollama_state["inflight"] == 0


def test_gateway_returns_429_when_ollama_busy(monkeypatch):
    """过载时非流式请求必须返回 429（可重试），而不是 502"""
    import gateway
    from fastapi.testclient import TestClient

    monkeypatch.setattr(gateway, "OLLAMA_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_QUEUE", 0)
    monkeypatch.setattr(gateway, "OLLAMA_MAX_WAIT", 0.05)
    gateway._ollama_state["inflight"] = 0
    gateway._ollama_state["queued"] = 0
    monkeypatch.setattr(gateway, "_model_catalog",
                        lambda: {"local-dolphin-mistral": ("ollama", "dolphin-mistral:latest")})

    async def fake_acquire():
        return False

    monkeypatch.setattr(gateway, "_ollama_acquire_slot", fake_acquire)

    client = TestClient(gateway.app, raise_server_exceptions=False)
    resp = client.post("/v1/chat/completions", json={
        "model": "local-dolphin-mistral",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    })
    assert resp.status_code == 429, f"过载应返回 429，实际 {resp.status_code}: {resp.text[:200]}"
    assert resp.headers.get("Retry-After")


def test_gateway_ollama_concurrency_default_is_four():
    """默认在途上限必须是 4（CPU 单次推理 30~60s，限 1 会误伤正常并发）"""
    import gateway
    assert gateway.OLLAMA_MAX_CONCURRENCY == 4
    assert gateway.OLLAMA_MAX_QUEUE == 8
