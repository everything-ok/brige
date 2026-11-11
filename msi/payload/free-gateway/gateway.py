"""
Free Gateway v1 — 本地免费模型聚合网关 (端口 8787)
====================================================
在 Zed 等 IDE 的模型选择器里提供 free-gateway 分组：
  · auto                    自动链: 在线(llm7) > 本地(Ollama) > 网页(桥接18792)
  · llm7-*                  llm7.io 免费 API（turbo 档，无需 Key）
  · local-*                 本机 Ollama 模型
  · or/<model>              OpenRouter（在 .env 配置 OPENROUTER_API_KEY 后自动出现）

所有模型在加入列表前都经过真实请求验证（见 /health）。
"""
import os
import sys
import json
import time
import asyncio
from typing import AsyncGenerator, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from dotenv import load_dotenv

if sys.platform == "win32":
    # pythonw(无控制台)环境下 sys.stdout/stderr 为 None，rich/uvicorn 写日志会崩
    import os as _os
    if sys.stdout is None:
        sys.stdout = open(_os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(_os.devnull, "w", encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("GATEWAY_PORT", "8787"))
LLM7_BASE = "https://api.llm7.io/v1"
LLM7_KEY = os.getenv("LLM7_KEY", "unused")          # 免费档 token 固定填 unused
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
BRIDGE_BASE = os.getenv("BRIDGE_BASE", "http://127.0.0.1:18792")   # ai-bridge 桥接
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()       # 可选

# ── 模型注册表：Zed 显示名 -> (后端, 真实模型ID) ──────────────────
# 原则：只暴露保证可用的模型（2026-11-02 验证）
# llm7 匿名 Key("unused") 为全球共享池，当天已持续 429 饱和，
# 故 llm7 不再单独暴露为模型，仅保留在 AUTO_CHAIN 作为在线优先项：
# 配额可用时 auto 走在线，饱和时自动降级本地 Ollama，永不失败。
# 若在 dash.llm7.io 申请专属免费 Key 填入 .env 的 LLM7_KEY，
# 可将下方 LLM7_MODELS 重新填上以恢复直连模型。
LLM7_MODELS = {}
# 本地模型持久化目录（管理页"模型市场"可动态添加）
LOCAL_MODELS_FILE = "local_models.json"

def _load_local_models() -> dict:
    try:
        with open(LOCAL_MODELS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"local-dolphin-mistral": "dolphin-mistral:latest",
                "local-lexi-llama3": "HammerAI/llama-3-lexi-uncensored:latest"}

def _save_local_models(models: dict):
    with open(LOCAL_MODELS_FILE, "w", encoding="utf-8") as f:
        json.dump(models, f, ensure_ascii=False, indent=2)

LOCAL_MODELS = _load_local_models()


def _ollama_installed() -> dict:
    """本机 Ollama 已安装的模型：tag -> {size_mb}
    用于把 local_models.json 里的过期映射纠正成实际存在的 tag。
    """
    try:
        import httpx
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        out = {}
        for m in r.json().get("models", []):
            name = m.get("name") or ""
            if name:
                out[name] = m
        return out
    except Exception:
        return {}


def _resolve_local_tag(real_model: str, installed: dict) -> str:
    """把配置的模型名映射到本机实际存在的 tag。

    例：配置写的是 qwen3t4:latest，但本机装的是 qwen3:4b
    → 通过去掉 :latest 后按前缀/体积匹配找到真实 tag。
    """
    if not installed:
        return real_model
    if real_model in installed:
        return real_model

    base = real_model.split(":")[0]
    # 1) 同名不同 tag（如 qwen3:4b vs qwen3:latest）
    for tag in installed:
        if tag.split(":")[0] == base:
            return tag
    # 2) 名称里含 base 的候选（如 qwen3t4 → qwen3:4b 的数字匹配）
    digits = "".join(ch for ch in base if ch.isdigit())
    if digits:
        cands = [t for t in installed if digits in t.replace(":", "").replace("-", "")]
        if len(cands) == 1:
            return cands[0]
    return real_model


def _sync_local_models() -> None:
    """启动时校正本地模型映射：把不存在的 tag 换成实际安装的 tag。

    避免用户在 IDE 里选了 local 模型却得到 404（表现为"模型没生效"）。
    """
    installed = _ollama_installed()
    if not installed:
        return
    changed = False
    for key, real in list(LOCAL_MODELS.items()):
        if real in installed:
            continue
        resolved = _resolve_local_tag(real, installed)
        if resolved != real and resolved in installed:
            LOCAL_MODELS[key] = resolved
            changed = True
            print(f"[gateway] 本地模型映射已校正: {key} {real} -> {resolved}")
    if changed:
        try:
            _save_local_models(LOCAL_MODELS)
        except Exception:
            pass


_sync_local_models()

# 高质量 Ollama 推荐目录（管理页"无限制"区展示，均适配 16GB 内存 CPU 推理）
CURATED_OLLAMA = [
    # min_ram_gb = 流畅运行所需内存（GB），用于按设备推荐
    {"ollama_id": "qwen2.5-coder:7b",              "label": "Qwen2.5-Coder 7B",    "note": "代码特化，7B最强",        "min_ram_gb": 6},
    {"ollama_id": "huihui_ai/qwen2.5-coder-abliterate:7b","label": "Coder-无审查 7B", "note": "编程特化+无审查",       "min_ram_gb": 6},
    {"ollama_id": "huihui_ai/qwen2.5-abliterate:7b","label": "Qwen2.5 无审查 7B",   "note": "中文+无审查",           "min_ram_gb": 6},
    {"ollama_id": "dolphin-mistral:latest",         "label": "Dolphin Mistral 7B",  "note": "无审查，已预装",         "min_ram_gb": 6},
    {"ollama_id": "llama3.1:8b",                    "label": "Llama3.1 8B",        "note": "Meta 通用",             "min_ram_gb": 7},
    {"ollama_id": "qwen2.5:7b",                     "label": "Qwen2.5 7B",         "note": "中文通用",              "min_ram_gb": 6},
    {"ollama_id": "deepseek-r1:7b",                 "label": "DeepSeek-R1 7B",     "note": "推理链",                "min_ram_gb": 6},
    # 高配设备梯队（14B / 32B）
    {"ollama_id": "qwen2.5-coder:14b",             "label": "Qwen2.5-Coder 14B",   "note": "代码能力大幅提升",        "min_ram_gb": 12},
    {"ollama_id": "huihui_ai/qwen2.5-coder-abliterate:14b","label": "Coder-无审查 14B","note": "14B编程+无审查",      "min_ram_gb": 12},
    {"ollama_id": "qwen2.5:14b",                    "label": "Qwen2.5 14B",        "note": "14B 通用",              "min_ram_gb": 12},
    {"ollama_id": "deepseek-r1:14b",                "label": "DeepSeek-R1 14B",    "note": "14B 推理链",            "min_ram_gb": 12},
    {"ollama_id": "qwen2.5-coder:32b",             "label": "Qwen2.5-Coder 32B",   "note": "接近开源顶级（旗舰级）",   "min_ram_gb": 24},
    {"ollama_id": "deepseek-r1:32b",                "label": "DeepSeek-R1 32B",    "note": "32B 顶级推理",           "min_ram_gb": 24},
]

# 拉取任务状态
_pull_jobs = {}   # {ollama_id: {"status": "pulling|done|failed", "error": str}}

# Auto 链：OpenRouter 免费模型双保险 > llm7 > 本地 > 网页
# （轻量 lightning 最快；ultra 质量高；避开易 429 的 llm7 首位）
AUTO_CHAIN = [
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
    ("openrouter", "google/gemma-4-26b-a4b-it:free"),
    ("llm7", "DeepSeek-V4-Flash-0731"),
    ("ollama", "dolphin-mistral:latest"),
    ("bridge", "zai"),
]

app = FastAPI(title="Free Gateway", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── OpenRouter 免费模型动态目录（10 分钟缓存） ──────────────────
_or_cache = {"ids": [], "ts": 0.0}
_last_served = {"requested": None, "served": None, "ts": 0.0}   # 最近一次实际服务的模型


async def _openrouter_free_ids() -> list:
    """从 OpenRouter 拉取当前免费(prompts=0/completion=0)模型 id 列表"""
    if time.time() - _or_cache["ts"] < 600 and _or_cache["ids"]:
        return _or_cache["ids"]
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get("https://openrouter.ai/api/v1/models",
                              headers={"Authorization": f"Bearer {OPENROUTER_KEY}"})
            r.raise_for_status()
            ids = []
            for m in r.json().get("data", []):
                p = m.get("pricing", {})
                if str(p.get("prompt")) == "0" and str(p.get("completion")) == "0":
                    ids.append(m["id"])
            _or_cache["ids"] = ids
            _or_cache["ts"] = time.time()
            return ids
    except Exception as e:
        print(f"[gateway] openrouter models fetch failed: {e}")
        return _or_cache["ids"]


def _model_catalog() -> dict:
    """暴露给客户端的模型表：id -> (backend, real_id)
    or/<id> 前缀模型在 chat 路由里按前缀动态解析，无需登记"""
    catalog = {"auto": ("auto", "auto")}
    for k, v in LLM7_MODELS.items():
        catalog[k] = ("llm7", v)
    for k, v in LOCAL_MODELS.items():
        catalog[k] = ("ollama", v)
    return catalog


@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    data = [{"id": mid, "object": "model", "created": now, "owned_by": backend}
            for mid, (backend, _) in _model_catalog().items()]
    if OPENROUTER_KEY:
        for rid in await _openrouter_free_ids():
            data.append({"id": f"or/{rid}", "object": "model",
                         "created": now, "owned_by": "openrouter"})
    return {"object": "list", "data": data}


@app.get("/health")
async def health():
    return {"status": "ok", "openrouter": bool(OPENROUTER_KEY),
            "llm7_models": len(LLM7_MODELS), "local_models": len(LOCAL_MODELS)}


def _device_info() -> dict:
    """探测当前设备：内存/显卡（任何设备打开管理页都自动适配推荐）"""
    info = {"ram_total_gb": None, "ram_free_gb": None, "gpu": "核显/CPU", "gpu_vram_gb": 0}
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
        info["ram_free_gb"] = round(vm.available / (1024 ** 3), 1)
    except Exception:
        pass
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            name, vram = r.stdout.strip().splitlines()[0].split(",")[:2]
            info["gpu"] = name.strip()
            info["gpu_vram_gb"] = round(int(vram.strip().split()[0]) / 1024, 1)
    except Exception:
        pass
    return info


def _fit(min_ram_gb, device) -> dict:
    """按设备判定：recommended(推荐) / tight(勉强) / not_recommended(不建议)"""
    total = device.get("ram_total_gb")
    if not total:
        return {"fit": "unknown", "reason": "无法检测设备内存"}
    if total >= min_ram_gb + 4:
        fit, reason = "recommended", "内存充裕，推荐"
    elif total >= min_ram_gb:
        fit, reason = "tight", "内存刚好够用，速度偏慢"
    else:
        fit, reason = "not_recommended", f"需要 {min_ram_gb}GB+ 内存，本机不够"
    speed = "GPU加速" if device.get("gpu_vram_gb", 0) >= min_ram_gb * 0.75 else "CPU推理(速度慢)"
    return {"fit": fit, "reason": reason, "speed": speed}


@app.get("/local-models")
async def local_models_list():
    """市场：设备信息 + 已装本地模型 + 推荐目录(按设备标注) + 拉取任务状态"""
    device = _device_info()
    curated = []
    for m in CURATED_OLLAMA:
        row = dict(m)
        row.update(_fit(m.get("min_ram_gb", 6), device))
        curated.append(row)
    # 推荐的排前面
    order = {"recommended": 0, "tight": 1, "not_recommended": 2, "unknown": 3}
    curated.sort(key=lambda x: order.get(x.get("fit"), 3))
    return {"device": device,
            "installed": [{"id": k, "ollama_id": v} for k, v in LOCAL_MODELS.items()],
            "curated": curated,
            "pull_jobs": _pull_jobs}


@app.post("/local-models/add")
async def local_models_add(request: Request):
    """拉取 Ollama 模型并在完成后自动接入（后台执行，立即返回）"""
    import subprocess
    body = await request.json()
    ollama_id = (body.get("ollama_id") or "").strip()
    display = (body.get("display") or "").strip()
    if not ollama_id:
        raise HTTPException(status_code=400, detail="需要 ollama_id")
    key = display or ollama_id.split("/")[-1].split(":")[0]
    job_key = ollama_id
    if _pull_jobs.get(job_key, {}).get("status") == "pulling":
        return {"success": True, "message": "该模型正在拉取中", "job": job_key}

    def _pull():
        _pull_jobs[job_key] = {"status": "pulling"}
        try:
            r = subprocess.run(["ollama", "pull", ollama_id], capture_output=True,
                               text=True, timeout=3600)
            if r.returncode == 0:
                LOCAL_MODELS[key] = ollama_id
                _save_local_models(LOCAL_MODELS)
                _pull_jobs[job_key] = {"status": "done"}
            else:
                _pull_jobs[job_key] = {"status": "failed", "error": (r.stderr or r.stdout)[-200:]}
        except Exception as e:
            _pull_jobs[job_key] = {"status": "failed", "error": str(e)[-200:]}

    import threading
    threading.Thread(target=_pull, daemon=True).start()
    return {"success": True, "message": f"开始拉取 {ollama_id}（约数分钟，完成后自动接入）"}


@app.get("/local-models/status")
async def local_models_status():
    return {"jobs": _pull_jobs, "installed": len(LOCAL_MODELS)}


@app.post("/market/refresh")
async def market_refresh():
    """拉新：强制重拉 OpenRouter 最新免费目录 + 扫描本机 Ollama 新装模型自动接入"""
    import subprocess, json as _json
    prev_free = set(_or_cache.get("ids", []))
    _or_cache["ts"] = 0.0            # 强制缓存失效
    fresh_free = await _openrouter_free_ids()
    new_free = [i for i in fresh_free if i not in prev_free]

    # 扫描本机 ollama list，新装的模型自动接入 LOCAL_MODELS
    added_local = []
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if not parts:
                continue
            oid = parts[0]
            if oid not in LOCAL_MODELS.values():
                key = "local-" + oid.split("/")[-1].split(":")[0]
                LOCAL_MODELS[key] = oid
                added_local.append(key)
        if added_local:
            _save_local_models(LOCAL_MODELS)
    except Exception as e:
        pass
    return {"success": True, "free_total": len(fresh_free),
            "new_free": new_free[:10], "local_added": added_local}


@app.get("/last")
async def last_served_info():
    """最近一次请求实际由哪个模型服务（auto 链解析结果）"""
    r = dict(_last_served)
    if r.get("ts"):
        r["ago_sec"] = round(time.time() - r["ts"], 1)
    return r


# ── 上游流式调用：统一产出 ("text", str) / ("tool_calls", list) ────
async def _stream_llm7(real_model: str, body: dict) -> AsyncGenerator[tuple, None]:
    payload = {k: v for k, v in body.items() if k in
               ("messages", "temperature", "max_tokens", "top_p", "tools", "tool_choice")}
    payload["model"] = real_model
    payload["stream"] = True
    async with httpx.AsyncClient(timeout=180) as cli:
        async with cli.stream("POST", f"{LLM7_BASE}/chat/completions", json=payload,
                              headers={"Authorization": f"Bearer {LLM7_KEY}"}) as r:
            if r.status_code != 200:
                detail = (await r.aread()).decode(errors="replace")[:200]
                raise RuntimeError(f"llm7 {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        delta = obj["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    content = delta.get("content")
                    if content:
                        yield ("text", content)
                    tcs = delta.get("tool_calls")
                    if tcs:
                        yield ("tool_calls", tcs)


async def _stream_ollama(real_model: str, body: dict) -> AsyncGenerator[tuple, None]:
    # keep_alive: 模型驻留内存 30 分钟 —— 低内存设备上重复使用同一模型
    # 不会反复冷加载（切换模型时上一个自动换出，不占双份内存）
    payload = {"model": real_model, "messages": body.get("messages", []),
               "stream": True, "keep_alive": "30m"}
    # Ollama 原生支持 tools（格式与 OpenAI 一致）
    if body.get("tools"):
        payload["tools"] = body["tools"]
    async with httpx.AsyncClient(timeout=300) as cli:
        async with cli.stream("POST", f"{OLLAMA_BASE}/api/chat", json=payload) as r:
            if r.status_code == 404:
                # 模型名与本机已安装的 tag 不一致 → 给出可操作的提示
                detail = (await r.aread()).decode(errors="replace")[:200]
                raise RuntimeError(f"ollama 模型不存在: {real_model}（{detail}）")
            if r.status_code != 200:
                raise RuntimeError(f"ollama {r.status_code}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message", {})
                delta = msg.get("content", "")
                if delta:
                    yield ("text", delta)
                tcs = msg.get("tool_calls")
                if tcs:
                    # Ollama 的 tool_calls 用 {function: {name, arguments}} 结构，
                    # 需补上 OpenAI 要求的 id / type
                    norm = []
                    for i, tc in enumerate(tcs):
                        fn = tc.get("function", {})
                        norm.append({
                            "id": tc.get("id") or f"call_{i}_{abs(hash(fn.get('name',''))) % 100000}",
                            "type": "function",
                            "function": {
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments")
                                if isinstance(fn.get("arguments"), str)
                                else json.dumps(fn.get("arguments") or {}, ensure_ascii=False),
                            },
                        })
                    yield ("tool_calls", norm)
                if obj.get("done"):
                    break


async def _stream_bridge(model_name: str, body: dict) -> AsyncGenerator[tuple, None]:
    payload = {"model": model_name, "messages": body.get("messages", []),
               "stream": True}
    if body.get("tools"):
        payload["tools"] = body["tools"]
    async with httpx.AsyncClient(timeout=600) as cli:
        async with cli.stream("POST", f"{BRIDGE_BASE}/v1/chat/completions",
                              json=payload) as r:
            if r.status_code != 200:
                raise RuntimeError(f"bridge {r.status_code}")
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0].get("delta", {})
                except Exception:
                    continue
                content = delta.get("content")
                if content:
                    yield ("text", content)
                tcs = delta.get("tool_calls")
                if tcs:
                    yield ("tool_calls", tcs)


async def _stream_openrouter(real_model: str, body: dict) -> AsyncGenerator[tuple, None]:
    payload = {k: v for k, v in body.items() if k in
               ("messages", "temperature", "max_tokens", "top_p", "tools", "tool_choice")}
    payload["model"] = real_model
    payload["stream"] = True
    async with httpx.AsyncClient(timeout=180) as cli:
        async with cli.stream("POST", "https://openrouter.ai/api/v1/chat/completions",
                              json=payload,
                              headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}) as r:
            if r.status_code != 200:
                detail = (await r.aread()).decode(errors="replace")[:200]
                raise RuntimeError(f"openrouter {r.status_code}: {detail}")
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        delta = obj["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    content = delta.get("content")
                    if content:
                        yield ("text", content)
                    tcs = delta.get("tool_calls")
                    if tcs:
                        yield ("tool_calls", tcs)


def _sse_chunk(text: str, model: str = None) -> bytes:
    data = {"id": "gw-chatcmpl", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model or "auto",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _sse_tool_calls(tool_calls: list, model: str = None) -> bytes:
    """工具调用 chunk（OpenAI 协议）"""
    data = {"id": "gw-chatcmpl", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model or "auto",
            "choices": [{"index": 0, "delta": {"tool_calls": tool_calls},
                         "finish_reason": "tool_calls"}]}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _sse_done() -> bytes:
    data = {"id": "gw-chatcmpl", "object": "chat.completion.chunk", "created": int(time.time()),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = (body.get("model") or "auto").strip()
    catalog = _model_catalog()
    # 工具定义（IDE Agent 模式必需），原样透传给支持 tool call 的后端
    tools = body.get("tools") or None

    if model.startswith("or/") and OPENROUTER_KEY:
        # or/<model> 前缀直通 OpenRouter（模型 id 透传，无需登记）
        backend, real = "openrouter", model[3:]
    elif model in catalog:
        backend, real = catalog[model]
    else:
        # 未知模型：走 auto 链兜底
        backend, real = "auto", "auto"

    async def gen() -> AsyncGenerator[bytes, None]:
        # or/<model> 直通 OpenRouter：一旦上游失败（429 配额耗尽 / 401 / 5xx），
        # 自动降级到 auto 链，避免客户端直接拿到“所有后端失败”。
        if backend == "openrouter":
            candidates = [("openrouter", real)] + AUTO_CHAIN
        else:
            candidates = AUTO_CHAIN if backend == "auto" else [(backend, real)]
        last_err = None
        served = None            # 实际服务的模型（auto 链解析后）
        for bk, rl in candidates:
            try:
                got = False
                stream_fn = {"llm7": _stream_llm7, "ollama": _stream_ollama,
                             "bridge": _stream_bridge, "openrouter": _stream_openrouter}[bk]
                async for kind, payload in stream_fn(rl, body):
                    if not got:
                        got = True
                        served = f"{bk}/{rl}"
                        _last_served.update(requested=model, served=served, ts=time.time())
                    if kind == "text":
                        yield _sse_chunk(payload, served)
                    elif kind == "tool_calls":
                        yield _sse_tool_calls(payload, served)
                if got:
                    yield _sse_done()
                    return
                last_err = f"{bk} 返回空回复"
            except Exception as e:
                last_err = f"{bk}: {e}"
                continue
        err = {"error": {"message": f"所有后端失败: {last_err}", "type": "gateway_error"}}
        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    served_model = None

    async def gen_tracked() -> AsyncGenerator[bytes, None]:
        """包装 gen()，记录本次实际服务的模型（避免读到上一次的全局值）"""
        nonlocal served_model
        async for piece in gen():
            served_model = _last_served.get("served")
            yield piece

    is_stream = bool(body.get("stream", False))
    if is_stream:
        return StreamingResponse(gen_tracked(), media_type="text/event-stream")
    # 非流式：聚合后一次性返回
    parts = []
    tool_calls = []
    error_msg = None

    async def collect():
        nonlocal tool_calls, error_msg
        async for piece in gen_tracked():
            try:
                obj = json.loads(piece.decode().removeprefix("data: ").strip())
            except Exception:
                continue
            # 上游全部失败时 gen() 会吐一个 error chunk —— 必须冒泡，
            # 否则客户端只会收到一个空回复（"模型没生效"的假象）
            if "error" in obj:
                error_msg = (obj["error"] or {}).get("message") or "上游请求失败"
                continue
            delta = (obj.get("choices") or [{}])[0].get("delta", {})
            if delta.get("content"):
                parts.append(delta["content"])
            if delta.get("tool_calls"):
                tool_calls.extend(delta["tool_calls"])

    await collect()
    if error_msg and not parts and not tool_calls:
        raise HTTPException(502, error_msg)
    text = "".join(parts)
    msg = {"role": "assistant", "content": text}
    finish = "stop"
    if tool_calls:
        # 上游（OpenRouter/Ollama）是分片流式下发 tool_calls：
        #   第 1 片带 id/name，后面的片只带 arguments 的片段。
        # 直接 extend 会得到一串"半截调用"，客户端解析必然失败
        # （表现为模型不调用工具 / 报 malformed tool-call）。
        # 这里按 index 合并成一条完整调用。
        merged = []
        by_index = {}
        for tc in tool_calls:
            idx = tc.get("index", 0)
            cur = by_index.get(idx)
            if cur is None:
                cur = {"index": idx, "id": "", "type": "function",
                       "function": {"name": "", "arguments": ""}}
                by_index[idx] = cur
                merged.append(cur)
            if tc.get("id"):
                cur["id"] = tc["id"]
            if tc.get("type"):
                cur["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                cur["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                cur["function"]["arguments"] += fn["arguments"]
        # arguments 可能是 dict（部分后端）或字符串，统一成 JSON 字符串
        for cur in merged:
            args = cur["function"]["arguments"]
            if not isinstance(args, str):
                cur["function"]["arguments"] = json.dumps(args, ensure_ascii=False)
            elif args.strip() == "":
                cur["function"]["arguments"] = "{}"
        msg["tool_calls"] = merged
        finish = "tool_calls"
    return JSONResponse({
        "id": "gw-chatcmpl", "object": "chat.completion", "created": int(time.time()),
        "model": served_model or model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(text) // 4, "total_tokens": len(text) // 4},
    })


if __name__ == "__main__":
    import uvicorn
    print(f"🌐 Free Gateway on http://{HOST}:{PORT}")
    print(f"   llm7 免费: {len(LLM7_MODELS)} | 本地: {len(LOCAL_MODELS)} | OpenRouter: {'启用' if OPENROUTER_KEY else '未配Key'}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
