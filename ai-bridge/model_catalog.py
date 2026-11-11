"""
统一模型目录（ai-bridge / free-gateway / IDE 接入共用）

设计目标：让 VSCode(Continue)、Zed、以及桥接管理页看到**同一套**模型分组：
    bridge｜<站点>       网页桥接站点（zai / venice / perplexity ...）
    openrouter｜<模型>   OpenRouter 免费模型（or/* 直通）
    local｜<模型>        本机 Ollama 模型（local-* 直通）
    auto｜<模型>         free-gateway 自动链（在线 > 本地 > 网页）

所有模型都可通过**同一个入口**访问：
    apiBase = http://127.0.0.1:18792/v1
桥接的 /v1/chat/completions 对 or/ 与 local 前缀走网关直通，
其余站点名走网页自动化。
"""
import os
import re
from typing import Dict, List, Optional, Tuple

BRIDGE_PORT = int(os.getenv("PORT", 18792))
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", 8787))
BRIDGE_BASE = f"http://127.0.0.1:{BRIDGE_PORT}"
GATEWAY_BASE = f"http://127.0.0.1:{GATEWAY_PORT}"

# ── 模型用途标签（显示名后缀，告诉用户这个模型适合干什么）────────
MODEL_PURPOSE_TAGS = [
    # OpenRouter 免费模型特征
    ("lightning", "⚡最快"), ("ultra-550b", "质量最强"), ("super-vl", "视觉"),
    ("nano-omni", "全模态推理"), ("content-safety", "审核勿用"),
    ("north-mini", "代码"), ("coder", "代码"), ("inkling", "推理新秀"),
    ("lyria", "音乐生成勿用"), ("gemma", "Google通用"), ("nemotron", "通用"),
    ("flint", "轻量"), ("lfm", "端侧轻量"), ("xing", "中文"), ("dots", "笔记预览"),
    ("openrouter/free", "官方免费路由"),
    # 本地 Ollama 模型特征
    ("abliterate", "无审查"), ("uncensored", "无审查"), ("dolphin", "无审查"),
    ("deepseek-coder", "代码"), ("deepseek-r1", "推理链"), ("deepseek", "推理"),
    ("qwen3", "通用中文"), ("qwen", "中文通用"), ("llama", "通用"), ("coder-v2", "代码"),
]


def short_or(model_id: str) -> str:
    """or/xxx 与 local-xxx 的短显示名（取前两段，最长 18 字符）"""
    if model_id.startswith("or/"):
        part = model_id[3:]
    elif model_id.startswith("local-"):
        part = model_id[len("local-"):]
    else:
        part = model_id
    part = part.split(":")[0].split("/")[-1]
    toks = part.split("-")
    return "-".join(toks[:2])[:18]


def purpose_tag(model_id: str) -> str:
    low = model_id.lower()
    for k, tag in MODEL_PURPOSE_TAGS:
        if k in low:
            return tag
    return ""


def display_name(model_id: str, seen: Optional[Dict[str, int]] = None) -> str:
    """短名 + 用途标签 + 同名去重编号（与 Zed 侧显示风格一致）"""
    s = short_or(model_id)
    if seen is not None:
        seen[s] = seen.get(s, 0) + 1
        if seen[s] > 1:
            s = f"{s}·{seen[s]}"
    tag = purpose_tag(model_id)
    return f"{s}｜{tag}" if tag else s


def group_of(model_id: str) -> str:
    """模型所属分组：bridge / openrouter / local / auto"""
    if model_id.startswith("or/"):
        return "openrouter"
    if model_id.startswith("local"):
        return "local"
    if model_id in ("auto",):
        return "auto"
    return "bridge"


# ── 目录拉取 ────────────────────────────────────────────────────
def fetch_gateway_models(timeout: float = 30.0) -> List[str]:
    """从 free-gateway(8787) 拉取全部模型 id（auto/local-*/or-*）"""
    try:
        import httpx
        # httpx.get 是同步 API（AsyncClient 才可 await）
        r = httpx.get(f"{GATEWAY_BASE}/v1/models", timeout=timeout)
        return [m["id"] for m in r.json().get("data", [])]
    except Exception:
        return []


def build_catalog(site_ids: List[str], gateway_ids: Optional[List[str]] = None,
                  include_pending: bool = True) -> List[dict]:
    """构建统一模型目录。

    site_ids:   桥接站点名（网页自动化），来自 config.get_enabled_sites()
    gateway_ids: free-gateway 的模型 id 列表（auto / local-* / or/*）

    返回每项：
        {"group": "bridge", "model": "<真实 model id>", "title": "<显示名>",
         "api_base": "<入口 base>", "tools": bool}
    所有项 api_base 统一为桥接入口 —— 桥接内部再决定走网页还是直通网关。
    """
    out: List[dict] = []
    seen: Dict[str, int] = {}

    # 1) 网页桥接站点
    for sid in site_ids:
        out.append({
            "group": "bridge",
            "model": sid,
            "title": f"bridge｜{sid}",
            "api_base": f"{BRIDGE_BASE}/v1",
            # 网页站点无原生 function calling，但桥接有文本模拟层，
            # 对 IDE 来说依然算「支持工具」→ 声明 tool_use 才能进 Agent 模式
            "tools": True,
        })

    # 2) 网关侧模型（auto / local-* / or/*）
    gids = gateway_ids if gateway_ids is not None else fetch_gateway_models()
    for gid in gids:
        grp = group_of(gid)
        if grp == "auto":
            title = "auto｜自动链（在线>本地>网页）"
        else:
            title = f"{grp}｜{display_name(gid, seen)}"
        out.append({
            "group": grp,
            "model": gid,
            "title": title,
            "api_base": f"{BRIDGE_BASE}/v1",
            "tools": grp in ("openrouter", "local"),   # API 后端原生支持 tool call
        })

    return out


def continue_models(catalog: List[dict], api_key: str = "any") -> List[dict]:
    """转成 Continue 2.0 config.yaml 的 models 条目。

    Continue 2.0 的 schema：name/model/provider 必填，provider 必须是已知 id。
    "openai" + 自定义 apiBase 会走 /chat/completions（非官方 OpenAI 域名，
    因此不会误用 /responses 端点），正好匹配桥接。
    """
    out = []
    for item in catalog:
        entry = {
            "name": item["title"],
            "model": item["model"],
            "provider": "openai",
            "apiBase": item["api_base"],
            "apiKey": api_key,
            "contextLength": 32000,
            "roles": ["chat", "edit", "apply", "summarize"],
            "capabilities": ["tool_use"],
            "defaultCompletionOptions": {
                "maxTokens": 4096,
                "temperature": 0.6,
                "topP": 0.95,
            },
        }
        out.append(entry)
    return out
