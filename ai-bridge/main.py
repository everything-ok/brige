"""
fix Unicode encoding for Windows console (Python 3.14 + Windows GBK)
"""
import sys
import io
import os as _os
if sys.platform == "win32":
    # pythonw(无控制台)环境下 sys.stdout/stderr 为 None，后续 rich/uvicorn 会崩
    if sys.stdout is None:
        sys.stdout = open(_os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(_os.devnull, "w", encoding="utf-8")
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import json
import re
import time
import uuid
import threading
from contextlib import asynccontextmanager
from typing import List, Optional, AsyncGenerator, Union

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rich.console import Console

from config import config
from browser.manager import browser_manager
from limiter.rate import RateLimiter
from limiter.balancer import LoadBalancer
from security import SecurityManager
from providers.generic import GenericProvider, Message, match_login_state
from providers.validator import BridgeValidator
from providers.model_fetcher import fetch_models_for_site

console = Console()

rate_limiter = RateLimiter(config)
security_manager = SecurityManager(config)
load_balancer = LoadBalancer(config, rate_limiter)
validator = BridgeValidator(rate_limiter, security_manager)

_providers = {}
_usage_history = []  # 全局使用记录：[{time, site, model, success, error}, ...]


def get_provider(site_name: str) -> GenericProvider:
    if site_name not in _providers:
        site = config.get_site(site_name)
        if not site:
            raise ValueError(f"站点不存在: {site_name}")
        _providers[site_name] = GenericProvider(site, rate_limiter, security_manager)
    return _providers[site_name]


class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[dict]]  # 兼容字符串和多模态数组格式

    def get_text(self) -> str:
        """统一提取文本内容"""
        if isinstance(self.content, str):
            return self.content
        # 多模态数组：提取所有 text 类型的内容拼接
        parts = [
            block.get("text", "")
            for block in self.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)


class ChatRequest(BaseModel):
    model: str = "glm-4.7"
    messages: List[ChatMessage]
    stream: bool = True
    site: Optional[str] = None


# ── 模型暴露策略：每个已启用站点 = 一个模型，ID 即站点名 ──────
# （IDE 模型选择器里直接显示桥接站点名：zai / perplexity / arena ...，
#   与管理页「已接入站点」列表一致）
# GLM_LATEST_MODELS 仅保留用于兼容旧式裸 GLM 模型名的解析
GLM_LATEST_MODELS = ["glm-5.3-flash", "glm-5.3", "glm-5.2"]

# 已知需要登录/注册账号才能使用的站点（venice/openrouter 游客可用）
LOGIN_REQUIRED_SITES = {"poe", "perplexity", "you", "chatgpt", "kimi", "doubao",
                        "yuanbao", "tiangong", "qwen", "zai", "arena",
                        "grok", "deepseek", "huggingchat", "mistral", "cohere", "groq", "phind", "happycapy", "hackaigc", "nanogpt"}

# 其中：注册/登录需要手机号验证的站点（独立页签，暂不支持邮箱自动登录）
PHONE_REQUIRED_SITES = {"zai", "deepseek", "qwen", "tiangong", "yuanbao", "kimi", "doubao"}
# 其余为邮箱登录站点（可在管理页配置邮箱+密码自动登录）
EMAIL_LOGIN_SITES = LOGIN_REQUIRED_SITES - PHONE_REQUIRED_SITES


def get_site_model_ids() -> List[str]:
    """按优先级返回所有启用站点的名字（一个站点 = 一个模型）"""
    return [s.name for s in config.get_enabled_sites()]


def default_site_model() -> str:
    """默认模型 = 优先级最高站点名（zai）"""
    ids = get_site_model_ids()
    return ids[0] if ids else "zai"


def resolve_site_model(model: Optional[str]):
    """把客户端传入的模型名解析为 (站点名|None, 站内模型ID)：

    - 'zai'                站点名即模型 -> (zai, 该站点第一个模型, 即 default 不切模型)
    - 'zai/glm-5.3-flash'  站点/模型    -> (zai, glm-5.3-flash)
    - 'glm-5.3-flash'      旧式裸模型名 -> (zai, glm-5.3-flash)
    - 其他未知名           -> (None, 原名) 交给负载均衡按模型匹配站点
    """
    raw = (model or "").split("[")[0].strip()   # 去掉上下文后缀如 [1m]
    if "/" in raw:
        site_part, model_part = raw.split("/", 1)
        site = config.get_site(site_part.strip())
        if site:
            model_id = model_part.strip()
            return site.name, model_id if model_id in site.models else next(iter(site.models), model_id)
        return None, raw.split("/")[-1].strip()
    site = config.get_site(raw)
    if site:
        return site.name, next(iter(site.models), "default")
    if raw in GLM_LATEST_MODELS:
        return "zai", raw
    return None, raw or GLM_LATEST_MODELS[0]


@asynccontextmanager
async def lifespan(app: FastAPI):
    console.print("[bold cyan]\n╔══════════════════════════════╗\n║  🛡️  Bridge  v2.0.0      ║\n╚══════════════════════════════╝[/bold cyan]")
    await browser_manager.start()
    sites = config.get_enabled_sites()
    console.print(f"[green]✅ 已加载 {len(sites)} 个站点[/green]")
    for s in sites:
        console.print(f"   • {s.name}: {s.url}")
    console.print(f"\n[blue]🌐 http://{config.HOST}:{config.PORT}[/blue]\n")
    yield
    await browser_manager.stop()


app = FastAPI(title="Bridge", description="AI 模型网页桥接器", version="2.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def verify_key(authorization: Optional[str] = None):
    if not config.API_KEY:
        return
    if not authorization:
        raise HTTPException(401, "Missing Authorization")
    token = authorization.replace("Bearer ", "")
    if token != config.API_KEY:
        raise HTTPException(401, "Invalid API Key")


@app.get("/", response_class=HTMLResponse)
async def root():
    sites = config.get_enabled_sites()
    models = load_balancer.get_available_models()

    # 需登录站点集合已提取为模块级 LOGIN_REQUIRED_SITES（venice 免登录）
    LOGIN_REQUIRED = LOGIN_REQUIRED_SITES

    def _model_tags(s):
        return "".join(f'<span class="mtag">{m}</span>' for m in s.models.keys()) or "-"

    def _login_badge(s):
        if s.name in LOGIN_REQUIRED:
            return (f'<a href="{s.url}" target="_blank" '
                    f'style="color:#fbbf24;font-size:.75rem;text-decoration:none" '
                    f'title="需要先登录该网站才能使用">⚠ 需登录</a>')
        return '<span style="color:#22c55e;font-size:.75rem">✓ 免登录</span>'

    def _site_row(s):
        return (f'<tr>'
                f'<td>{s.name}</td>'
                f'<td><a href="{s.url}" target="_blank">{s.url}</a></td>'
                f'<td>{_login_badge(s)}</td>'
                f'<td>{_model_tags(s)}</td>'
                f'</tr>')

    # 三页签分组：可用站点 / 邮箱登录(可配置自动登录) / 手机号验证
    priority_sites = [s for s in sites if s.name not in LOGIN_REQUIRED]
    email_sites = [s for s in sites if s.name in EMAIL_LOGIN_SITES]
    phone_sites = [s for s in sites if s.name in PHONE_REQUIRED_SITES]
    site_rows = "".join(_site_row(s) for s in priority_sites)
    pending_rows = "".join(_site_row(s) for s in phone_sites)

    def _email_login_row(s):
        return (f'<tr data-site="{s.name}">'
                f'<td style="white-space:nowrap"><strong>{s.name}</strong></td>'
                f'<td>'
                f'<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">'
                f'<input class="al-email" type="email" placeholder="邮箱" style="background:#0f1117;border:1px solid #334155;color:#e2e8f0;border-radius:5px;padding:5px 8px;font-size:.8rem;width:170px">'
                f'<input class="al-pass" type="password" placeholder="密码" style="background:#0f1117;border:1px solid #334155;color:#e2e8f0;border-radius:5px;padding:5px 8px;font-size:.8rem;width:130px">'
                f'<button class="btn al-go" onclick="autoLogin(\'{s.name}\', this)" style="background:#7c3aed;color:#fff;border:none;border-radius:5px;padding:5px 12px;font-size:.8rem;cursor:pointer">自动登录</button>'
                f'</div>'
                f'<div class="al-code-row" style="display:none;margin-top:6px;gap:6px;align-items:center">'
                f'<input class="al-code" type="text" placeholder="邮件验证码" style="background:#0f1117;border:1px solid #334155;color:#e2e8f0;border-radius:5px;padding:5px 8px;font-size:.8rem;width:170px">'
                f'<button class="btn al-code-go" onclick="autoLoginCode(\'{s.name}\', this)" style="background:#166534;color:#86efac;border:none;border-radius:5px;padding:5px 12px;font-size:.8rem;cursor:pointer">提交验证码</button>'
                f'</div>'
                f'<div class="al-status" style="color:#64748b;font-size:.75rem;margin-top:4px">未登录 · 填入邮箱密码后点自动登录</div>'
                f'</td>'
                f'<td><a href="{s.url}" target="_blank" style="color:#60a5fa;font-size:.75rem" '
                f'onclick="openForManualLogin(\'{s.name}\');return true">打开站点</a></td>'
                f'</tr>')

    email_rows = "".join(_email_login_row(s) for s in email_sites)
    # API 暴露的模型 = 站点名（与 /v1/models 一致）
    model_tags = "".join(f'<span class="tag">{m}</span>' for m in get_site_model_ids())

    ide_icons = {
        "kiro":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M12 3 C8 7 8 17 12 21"/><path d="M12 3 C16 7 16 17 12 21"/><circle cx="12" cy="12" r="2" fill="currentColor"/></svg>',
        "zed":      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 4h16v3L8 17h12v3H4v-3l12-10H4V4z"/></svg>',
        "code":     '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.5 3.5 22 12l-4.5 8.5-1.8-1L19.5 12 15.7 4.5l1.8-1zM6.5 3.5l1.8 1L4.5 12l3.8 7.5-1.8 1L2 12l4.5-8.5zM14.1 2l1.9.5-6.1 19-1.9-.5 6.1-19z"/></svg>',
        "claude":   '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"/></svg>',
        "cursor":   '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 2l16 10-7 2-3 7z"/></svg>',
        "windsurf": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 18 C6 10 14 6 21 8"/><path d="M3 18 C8 14 14 16 16 22"/><path d="M3 18 l18-6"/></svg>',
    }
    ide_svgs_json = json.dumps(ide_icons)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <title>Bridge</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;padding:40px 20px}}
    .container{{max-width:960px;margin:0 auto}}
    .header{{text-align:center;margin-bottom:40px}}
    .header h1{{font-size:2.4rem;font-weight:700;color:#7c3aed}}
    .header p{{color:#94a3b8;margin-top:8px;font-size:1rem}}
    .badge{{display:inline-block;background:#1e293b;border:1px solid #334155;border-radius:20px;padding:4px 14px;font-size:.8rem;color:#64748b;margin-top:10px}}
    .card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:24px}}
    .card h2{{font-size:1rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:16px}}
    table{{width:100%;border-collapse:collapse}}
    th{{text-align:left;padding:8px 12px;color:#64748b;font-size:.8rem;text-transform:uppercase;border-bottom:1px solid #334155}}
    td{{padding:10px 12px;border-bottom:1px solid #1a2332;font-size:.9rem;vertical-align:middle}}
    td a{{color:#7c3aed;text-decoration:none}}
    tr:last-child td{{border-bottom:none}}
    .tag{{display:inline-block;background:#312e81;color:#a5b4fc;border-radius:6px;padding:3px 10px;font-size:.8rem;margin:3px}}
    .mtag{{display:inline-block;background:#1a2332;color:#94a3b8;border-radius:4px;padding:2px 8px;font-size:.78rem;margin:2px}}
    .loading-models{{color:#475569;font-size:.8rem;font-style:italic}}
    .status{{display:inline-block;width:8px;height:8px;background:#22c55e;border-radius:50%;margin-right:8px}}
    .links{{display:flex;gap:12px;justify-content:center;margin-top:24px;flex-wrap:wrap}}
    .links a{{background:#312e81;color:#a5b4fc;padding:8px 20px;border-radius:8px;text-decoration:none;font-size:.9rem}}
    .endpoint{{font-family:monospace;background:#0f1117;padding:2px 6px;border-radius:4px;font-size:.85rem;color:#a5b4fc}}
    .ide-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-bottom:16px}}
    .ide-card{{background:#0f1117;border:1px solid #334155;border-radius:10px;padding:16px;text-align:center;cursor:pointer;transition:border-color .2s,transform .1s}}
    .ide-card:hover{{border-color:#7c3aed;transform:translateY(-2px)}}
    .ide-card.detected{{border-color:#22c55e}}
    .ide-card .icon{{width:36px;height:36px;margin:0 auto 8px;display:flex;align-items:center;justify-content:center}}
    .ide-svg svg{{width:32px;height:32px}}
    .ide-card .name{{font-size:.9rem;font-weight:600}}
    .det{{font-size:.75rem;color:#22c55e;margin-top:4px}}
    .undet{{font-size:.75rem;color:#475569;margin-top:4px}}
    .btn{{padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-size:.85rem;font-weight:600}}
    .btn-primary{{background:#7c3aed;color:#fff}}
    .btn-secondary{{background:#1e293b;color:#94a3b8;border:1px solid #334155}}
    .btn-copy{{background:#1e3a5f;color:#93c5fd;border:1px solid #1e40af;margin-top:8px;padding:4px 12px;font-size:.8rem;border-radius:4px;cursor:pointer}}
    #ide-result{{margin-top:16px;padding:16px;border-radius:8px;display:none}}
    #ide-result.ok{{background:#052e16;border:1px solid #166534;color:#86efac}}
    #ide-result.err{{background:#2d0a0a;border:1px solid #7f1d1d;color:#fca5a5}}
    pre{{background:#0f1117;padding:12px;border-radius:6px;font-size:.78rem;overflow-x:auto;margin-top:8px;color:#a5b4fc;white-space:pre-wrap;word-break:break-all}}
    .extra-cfg{{margin-top:12px;border-top:1px solid #1e3a5f;padding-top:10px}}
    .extra-cfg-title{{font-size:.8rem;color:#60a5fa;margin-bottom:4px;font-weight:600}}
    .modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}}
    .modal-overlay.show{{display:flex}}
    .modal{{background:#1e293b;border:1px solid #334155;border-radius:14px;padding:28px;max-width:480px;width:90%}}
    .modal h3{{font-size:1.1rem;margin-bottom:12px}}
    .modal p{{color:#94a3b8;font-size:.9rem;margin-bottom:20px;line-height:1.6;white-space:pre-line}}
    .modal-btns{{display:flex;gap:10px;justify-content:flex-end}}
    .ide-kiro svg{{color:#a78bfa}} .ide-zed svg{{color:#f59e0b}} .ide-code svg{{color:#3b82f6}}
    .ide-claude svg{{color:#f97316}} .ide-cursor svg{{color:#06b6d4}} .ide-windsurf svg{{color:#10b981}}
    .ide-card.detected .ide-svg svg{{color:#22c55e !important}}
    .btn-refresh{{background:#1e3a5f;color:#93c5fd;border:1px solid #1e40af;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:.8rem;display:flex;align-items:center;gap:6px;transition:background .2s}}
    .btn-refresh:hover{{background:#1e40af}}
    .btn-refresh.loading svg{{animation:spin 1s linear infinite}}
    @keyframes spin{{to{{transform:rotate(360deg)}}}}
    .discover-list{{display:flex;flex-direction:column;gap:8px;max-height:320px;overflow-y:auto;margin:12px 0}}
    .discover-item{{background:#0f1117;border:1px solid #334155;border-radius:8px;padding:12px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
    .discover-item .info{{flex:1}}
    .discover-item .info strong{{font-size:.9rem;color:#e2e8f0}}
    .discover-item .info small{{color:#64748b;font-size:.78rem;display:block;margin-top:2px}}
    .discover-item .btn-add{{background:#166534;color:#86efac;border:1px solid #166534;padding:4px 12px;border-radius:5px;cursor:pointer;font-size:.8rem;white-space:nowrap}}
    .discover-item .btn-add:disabled{{opacity:.5;cursor:not-allowed}}
    .discover-item .btn-skip{{background:transparent;color:#475569;border:1px solid #334155;padding:4px 10px;border-radius:5px;cursor:pointer;font-size:.8rem}}
    .usage-steps{{counter-reset:step}}
    .step{{counter-increment:step;position:relative;padding:14px 16px 14px 56px;margin-bottom:12px;background:#0f1117;border:1px solid #334155;border-radius:8px}}
    .step::before{{content:counter(step);position:absolute;left:16px;top:14px;width:28px;height:28px;background:#7c3aed;color:#fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.9rem}}
    .step h4{{font-size:.95rem;color:#e2e8f0;margin-bottom:6px}}
    .step p{{font-size:.85rem;color:#94a3b8;line-height:1.6;margin-bottom:8px}}
    .step pre{{margin-top:6px;margin-bottom:0;font-size:.78rem}}
    .tabs{{display:flex;gap:6px;margin-bottom:12px;border-bottom:1px solid #334155}}
    .tab{{padding:8px 16px;background:transparent;border:none;color:#64748b;cursor:pointer;font-size:.85rem;border-bottom:2px solid transparent;transition:all .2s}}
    .tab:hover{{color:#94a3b8}}
    .tab.active{{color:#a78bfa;border-bottom-color:#7c3aed}}
    .tab-content{{display:none}}
    .tab-content.active{{display:block}}
    .stab{{padding:8px 16px;background:transparent;border:none;color:#64748b;cursor:pointer;font-size:.88rem;border-bottom:2px solid transparent;transition:all .2s;font-weight:600}}
    .stab:hover{{color:#94a3b8}}
    .stab.active{{color:#a78bfa;border-bottom-color:#7c3aed}}
    .stab-content{{display:none}}
    .stab-content.active{{display:block}}
    .quick-cmd{{display:flex;align-items:center;justify-content:space-between;background:#0f1117;padding:10px 14px;border-radius:6px;margin-top:8px;font-family:monospace;font-size:.82rem;border:1px solid #1e3a5f}}
    .quick-cmd code{{color:#a5b4fc;flex:1;overflow-x:auto;white-space:nowrap}}
    .quick-cmd button{{background:#1e3a5f;color:#93c5fd;border:1px solid #1e40af;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:.75rem;margin-left:10px}}
    .quick-cmd button:hover{{background:#1e40af}}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🛡️ Bridge</h1>
    <span class="badge"><span class="status"></span>运行中 · v2.0.0 · 端口 {config.PORT}</span>
  </div>
  <div class="card">
    <h2>🔌 接入 IDE</h2>
    <div id="ide-grid" class="ide-grid"><div style="color:#64748b;font-size:.9rem">正在检测运行中的 IDE...</div></div>
    <div id="ide-result"></div>
  </div>
  <div class="card">
    <h2 style="display:flex;align-items:center;justify-content:space-between">
      📦 模型市场
      <div style="display:flex;gap:8px">
        <button id="btn-market-refresh" class="btn-refresh" onclick="marketRefresh(this)" title="拉新：获取 OpenRouter 最新免费模型 + 扫描本机新装 Ollama 模型，并自动刷新到 Zed">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M1 4v6h6M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
          🔄 拉新
        </button>
        <button id="btn-zed-refresh" class="btn-refresh" onclick="refreshZedModels(this)" title="把下方已勾选/已拉取的模型一键写入 Zed IDE（不重启 Zed）">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          一键加载到 Zed
        </button>
      </div>
    </h2>
    <p style="color:#64748b;font-size:.8rem;margin-bottom:12px">免费模型来自 OpenRouter（约1000次/天）；无限制模型本地 Ollama 运行（无配额）。加载到 Zed 后在模型选择器立即可用。</p>
    <div class="mkt-sec">
      <h3 style="font-size:.85rem;color:#a5b4fc;margin:8px 0 6px">🆓 免费模型（OpenRouter）</h3>
      <div id="mkt-free" style="color:#475569;font-size:.8rem">加载中...</div>
    </div>
    <div class="mkt-sec" style="margin-top:14px">
      <h3 style="font-size:.85rem;color:#86efac;margin:8px 0 6px">♾️ 无限制模型（本地 Ollama，无配额）</h3>
      <div id="mkt-device" style="color:#94a3b8;font-size:.78rem;margin-bottom:8px">检测设备中...</div>
      <div id="mkt-local" style="color:#475569;font-size:.8rem">加载中...</div>
    </div>
    <div id="mkt-status" style="margin-top:10px;color:#94a3b8;font-size:.8rem;min-height:18px"></div>
  </div>
  <style>
    .mkt-item{{display:flex;align-items:center;justify-content:space-between;background:#0f1117;border:1px solid #334155;border-radius:7px;padding:8px 12px;margin-bottom:6px;gap:10px}}
    .mkt-item .nm{{font-size:.85rem;color:#e2e8f0;font-weight:600}}
    .mkt-item .full{{font-size:.72rem;color:#64748b}}
    .mkt-item .tag{{font-size:.72rem;padding:2px 8px;border-radius:4px;margin-left:6px}}
    .tag-ok{{background:#052e16;color:#86efac}}
    .tag-pending{{background:#1e3a5f;color:#93c5fd}}
    .mkt-btn{{background:#312e81;color:#a5b4fc;border:none;border-radius:5px;padding:4px 12px;font-size:.78rem;cursor:pointer;white-space:nowrap}}
    .mkt-btn:hover{{background:#4338ca}}
    .mkt-btn:disabled{{opacity:.5;cursor:not-allowed}}
  </style>
  <div class="card">
    <h2 style="display:flex;align-items:center;justify-content:space-between">
      已接入站点
      <div style="display:flex;gap:8px">
        <button class="btn-refresh" onclick="checkSitesHealth(event)" title="检查站点可用性">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          检查
        </button>
        <button id="btn-discover" class="btn-refresh" onclick="discoverSites()" title="发现免费站点">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M1 4v6h6M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
          刷新站点
        </button>
      </div>
    </h2>
    <div class="tabs" style="margin-bottom:14px">
      <button class="stab active" data-stab="ready">✅ 可用站点 ({len(priority_sites)})</button>
      <button class="stab" data-stab="email">📧 邮箱登录 ({len(email_sites)})</button>
      <button class="stab" data-stab="pending">📱 手机号验证 ({len(phone_sites)})</button>
    </div>
    <div class="stab-content active" data-stab="ready">
      <table>
        <thead><tr><th>名称</th><th>地址</th><th>状态</th><th>可用模型</th></tr></thead>
        <tbody id="sites-tbody">{site_rows}</tbody>
      </table>
    </div>
    <div class="stab-content" data-stab="email">
      <p style="color:#64748b;font-size:.82rem;margin-bottom:10px">以下站点支持邮箱登录：填入邮箱和密码点「自动登录」，桥接会在隐藏浏览器中完成登录；若站点改用邮件验证码，会出现验证码输入框。</p>
      <table>
        <thead><tr><th>名称</th><th>登录配置</th><th>站点</th></tr></thead>
        <tbody id="email-tbody">{email_rows}</tbody>
      </table>
    </div>
    <div class="stab-content" data-stab="pending">
      <p style="color:#64748b;font-size:.82rem;margin-bottom:10px">以下站点注册/登录需要手机号验证，暂不支持自动登录 —— 点状态列的站点名手动完成一次登录即可永久使用。</p>
      <table>
        <thead><tr><th>名称</th><th>地址</th><th>状态</th><th>可用模型</th></tr></thead>
        <tbody id="pending-tbody">{pending_rows}</tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <h2>实时使用记录</h2>
    <div id="usage-list" style="font-size:.82rem;max-height:200px;overflow-y:auto">
      <div style="color:#475569;text-align:center;padding:12px">暂无记录</div>
    </div>
  </div>
  <div class="card">
    <h2>📘 使用说明</h2>
    <div class="tabs">
      <button class="tab active" data-tab="quickstart">快速开始</button>
      <button class="tab" data-tab="chat">对话 API</button>
      <button class="tab" data-tab="login">一键登录</button>
      <button class="tab" data-tab="ide">IDE 集成</button>
      <button class="tab" data-tab="curl">curl 示例</button>
    </div>

    <div class="tab-content active" data-tab="quickstart">
      <p style="color:#94a3b8;font-size:.88rem;margin-bottom:16px;line-height:1.7">
        Bridge 把 20 个 AI 网站封装成 <strong style="color:#a78bfa">OpenAI 兼容</strong> 的 API 接口，你只需要把请求地址指向 <code class="endpoint" style="font-size:.85rem">http://127.0.0.1:{config.PORT}/v1</code>，
        就能在任何支持自定义 OpenAI 端点的工具中使用，无需任何 API Key。
      </p>
      <div class="usage-steps">
        <div class="step">
          <h4>选择模型</h4>
          <p>从「已接入站点」表格中选择想用的站点和模型，或不指定 <code>site</code> 字段让系统自动分配。</p>
        </div>
        <div class="step">
          <h4>配置客户端</h4>
          <p>在 IDE 或工具中将 API Base URL 设置为：</p>
          <div class="quick-cmd"><code>http://127.0.0.1:{config.PORT}/v1</code><button onclick="copyText('http://127.0.0.1:{config.PORT}/v1')">复制</button></div>
          <p style="margin-top:10px">API Key 留空即可（如需填写可填任意字符串）。</p>
        </div>
        <div class="step">
          <h4>登录所需站点</h4>
          <p>带 ⚠ 标记的站点需要登录一次。点击「一键登录」标签页，按提示完成登录后即可永久使用。</p>
        </div>
        <div class="step">
          <h4>开始对话</h4>
          <p>像使用 OpenAI 一样发送请求即可，返回格式完全兼容 OpenAI Chat Completions API。</p>
        </div>
      </div>
    </div>

    <div class="tab-content" data-tab="chat">
      <p style="color:#94a3b8;font-size:.88rem;margin-bottom:12px;line-height:1.6">
        支持两种调用方式：<strong style="color:#a78bfa">OpenAI 兼容</strong>（推荐，IDE/工具直接接入）和 <strong style="color:#a78bfa">原生接口</strong>（可指定 site）。
      </p>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:12px 0 6px">① OpenAI 兼容接口</h4>
      <pre>POST http://127.0.0.1:{config.PORT}/v1/chat/completions
Content-Type: application/json

{{
  "model": "doubao",
  "messages": [{{"role":"user","content":"你好，请自我介绍"}}],
  "stream": true
}}</pre>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">② 原生接口（指定 site + model）</h4>
      <pre>POST http://127.0.0.1:{config.PORT}/chat/completions
Content-Type: application/json

{{
  "site": "doubao",
  "model": "default",
  "messages": [{{"role":"user","content":"用一句话介绍你自己"}}],
  "stream": true
}}</pre>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">JS 前端调用示例</h4>
      <pre>const r = await fetch('http://127.0.0.1:{config.PORT}/v1/chat/completions', {{
  method:'POST',
  headers:{{'Content-Type':'application/json'}},
  body: JSON.stringify({{
    model: 'doubao',
    messages: [{{role:'user',content:'你好'}}]
  }})
}});
const data = await r.json();
console.log(data.choices[0].message.content);</pre>
    </div>

    <div class="tab-content" data-tab="login">
      <p style="color:#94a3b8;font-size:.88rem;margin-bottom:12px;line-height:1.6">
        标记 ⚠ <strong style="color:#fbbf24">需登录</strong> 的站点必须先登录才能使用。每个站点只需登录一次，Cookie 会自动保存到本地。
      </p>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:8px 0 6px">通过网页界面</h4>
      <p style="color:#94a3b8;font-size:.85rem;margin-bottom:6px">在「已接入站点」表格的「状态」列点击「需登录」链接，跳转到对应网站手动登录即可。</p>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">通过 API 触发</h4>
      <pre>POST http://127.0.0.1:{config.PORT}/login/open?url=https://www.doubao.com
→ 浏览器自动打开 doubao 登录页，完成登录后 Cookie 自动保存</pre>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">支持的快捷登录命令</h4>
      <div class="quick-cmd"><code>POST /login/open?url={{站点URL}}</code><button onclick="copyText('POST /login/open?url=https://www.doubao.com')">复制</button></div>
      <p style="color:#64748b;font-size:.78rem;margin-top:10px;line-height:1.6">
        💡 提示：登录状态保存在本地浏览器上下文中，重启服务不会丢失。如需清除登录状态，删除 <code style="color:#94a3b8">data/profiles/</code> 目录下对应站点文件夹即可。
      </p>
    </div>

    <div class="tab-content" data-tab="ide">
      <p style="color:#94a3b8;font-size:.88rem;margin-bottom:12px;line-height:1.6">
        支持 Cursor / Claude Code / Kiro / Zed / VSCode / Windsurf 等 IDE 一键接入。
      </p>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:8px 0 6px">① 一键接入（推荐）</h4>
      <p style="color:#94a3b8;font-size:.85rem;margin-bottom:6px">运行中的 IDE 会在页面顶部「🔌 接入 IDE」区域显示绿色 ✓，点击卡片即可自动写入配置。</p>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">② 手动配置</h4>
      <p style="color:#94a3b8;font-size:.85rem;margin-bottom:6px">将 IDE 的 OpenAI 兼容端点设置为：</p>
      <div class="quick-cmd"><code>http://127.0.0.1:{config.PORT}/v1</code><button onclick="copyText('http://127.0.0.1:{config.PORT}/v1')">复制</button></div>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">Cursor 配置示例</h4>
      <pre>// settings.json
{{
  "cursor.openai.baseUrl": "http://127.0.0.1:{config.PORT}/v1",
  "cursor.openai.apiKey": "any-key"
}}</pre>
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">Claude Code 配置示例</h4>
      <pre>export ANTHROPIC_BASE_URL=http://127.0.0.1:{config.PORT}
export ANTHROPIC_API_KEY=any-key</pre>
    </div>

    <div class="tab-content" data-tab="curl">
      <h4 style="color:#cbd5e1;font-size:.9rem;margin:8px 0 6px">基础对话</h4>
      <pre>curl -X POST http://127.0.0.1:{config.PORT}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{{"model":"doubao","messages":[{{"role":"user","content":"你好"}}]}}'</pre>

      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">流式响应</h4>
      <pre>curl -X POST http://127.0.0.1:{config.PORT}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{{"model":"chatgpt","stream":true,"messages":[{{"role":"user","content":"写首七言绝句"}}]}}'</pre>

      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">列出所有模型</h4>
      <pre>curl http://127.0.0.1:{config.PORT}/v1/models</pre>

      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">检查登录状态</h4>
      <pre>curl http://127.0.0.1:{config.PORT}/admin/status</pre>

      <h4 style="color:#cbd5e1;font-size:.9rem;margin:16px 0 6px">打开浏览器登录</h4>
      <pre>curl -X POST "http://127.0.0.1:{config.PORT}/login/open?url=https://www.doubao.com"</pre>
    </div>
  </div>
  <div class="card">
    <h2>API 接口</h2>
    <table>
      <tr><td><span class="endpoint">GET</span></td><td><span class="endpoint">/v1/models</span></td><td>列出所有模型</td></tr>
      <tr><td><span class="endpoint">POST</span></td><td><span class="endpoint">/v1/chat/completions</span></td><td>对话（OpenAI 兼容）</td></tr>
      <tr><td><span class="endpoint">GET</span></td><td><span class="endpoint">/admin/status</span></td><td>限流状态</td></tr>
      <tr><td><span class="endpoint">POST</span></td><td><span class="endpoint">/admin/validate</span></td><td>验证站点连通性</td></tr>
      <tr><td><span class="endpoint">GET</span></td><td><span class="endpoint">/health</span></td><td>健康检查</td></tr>
    </table>
  </div>
  <div class="links">
    <a href="/docs">📖 Swagger UI</a><a href="/redoc">📄 ReDoc</a>
    <a href="/health">❤️ 健康检查</a><a href="/admin/status">📊 限流状态</a>
  </div>
</div>
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3 id="modal-title">确认接入</h3>
    <p id="modal-body"></p>
    <div class="modal-btns">
      <button class="btn btn-secondary" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" id="modal-confirm">确认接入</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="discover-modal">
  <div class="modal" style="max-width:560px">
    <h3>🔄 发现免费站点</h3>
    <p style="color:#64748b;font-size:.85rem;margin:8px 0 12px">以下站点可免费访问且无需登录，点击「添加」确认接入，点击「跳过」忽略</p>
    <div id="discover-list" class="discover-list"><div style="color:#64748b;text-align:center;padding:20px">正在探测...</div></div>
    <div class="modal-btns" style="margin-top:12px">
      <button class="btn btn-secondary" onclick="closeDiscoverModal()">关闭</button>
    </div>
  </div>
</div>
<script>
const IDE_SVGS = {ide_svgs_json};
const ALL_IDES = [
  {{key:'kiro',name:'Kiro'}},{{key:'zed',name:'Zed'}},{{key:'code',name:'VSCode'}},
  {{key:'claude',name:'Claude'}},{{key:'cursor',name:'Cursor'}},{{key:'windsurf',name:'Windsurf'}},
];
fetch('/ide/detect').then(r=>r.json()).then(data=>{{
  const on = data.detected.map(d=>d.key);
  document.getElementById('ide-grid').innerHTML = ALL_IDES.map(ide=>{{
    const active = on.includes(ide.key);
    return '<div class="ide-card '+(active?'detected':'')+'" data-key="'+ide.key+'" data-name="'+ide.name+'">'
      +'<div class="icon ide-svg ide-'+ide.key+'">'+(IDE_SVGS[ide.key]||'')+'</div>'
      +'<div class="name">'+ide.name+'</div>'
      +'<div class="'+(active?'det':'undet')+'">'+(active?'✓ 运行中':'未检测到')+'</div></div>';
  }}).join('');
  document.getElementById('ide-grid').addEventListener('click',function(e){{
    const c=e.target.closest('.ide-card'); if(c) doConfirm(c.dataset.key,c.dataset.name);
  }});
}}).catch(e=>{{ document.getElementById('ide-grid').innerHTML='<div style="color:#ef4444">检测失败: '+e.message+'</div>'; }});

const LOGIN_REQUIRED_SITES = new Set(['poe','perplexity','you','chatgpt','kimi','doubao','yuanbao','tiangong','qwen','zai','arena']);

function doConfirm(key,name){{
  document.getElementById('modal-title').textContent='接入 '+name;
  document.getElementById('modal-body').textContent='将以合并方式写入 '+name+' 的配置文件。\\n\\n原有模型和配置不受影响，仅追加 Bridge 作为新的端点。\\n\\n接入后无需任何 API Key 即可使用桥接的网页模型。';
  document.getElementById('modal-confirm').onclick=function(){{ doConnect(key,name); }};
  document.getElementById('modal').classList.add('show');
}}
function closeModal(){{ document.getElementById('modal').classList.remove('show'); }}
function copyText(text){{ navigator.clipboard.writeText(text).catch(()=>{{}}); }}

// 登录弹窗：询问用户是否打开对应网站登录
function promptLogin(siteUrl, siteName){{
  return new Promise(function(resolve){{
    document.getElementById('modal-title').textContent='需要登录 ' + (siteName||siteUrl);
    document.getElementById('modal-body').textContent=
      '该模型所在站点需要登录才能使用。\\n\\n点击「去登录」将在浏览器中打开 ' + siteUrl + '\\n完成登录后即可正常使用。';
    document.getElementById('modal-confirm').textContent='去登录';
    document.getElementById('modal-confirm').onclick=function(){{
      closeModal();
      fetch('/login/open?url='+encodeURIComponent(siteUrl),{{method:'POST'}});
      resolve(true);
    }};
    // 覆盖取消按钮
    document.querySelector('#modal .btn-secondary').onclick=function(){{
      closeModal(); resolve(false);
    }};
    document.getElementById('modal').classList.add('show');
  }});
}}

function closeDiscoverModal(){{ document.getElementById('discover-modal').classList.remove('show'); }}

// 发现站点：进度显示在按钮上
function discoverSites(){{
  const btn = document.getElementById('btn-discover');
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<svg style="animation:spin 1s linear infinite" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M1 4v6h6M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg> 刷新中...';

  fetch('/sites/discover').then(r=>r.json()).then(data=>{{
    if(!data.found || !data.found.length){{
      btn.style.background='#1e293b'; btn.style.color='#64748b';
      btn.innerHTML='ℹ️ 未发现新站点';
      setTimeout(function(){{ btn.disabled=false; btn.innerHTML=originalHTML; btn.style.background=''; btn.style.color=''; }}, 4000);
      return;
    }}

    const noLogin = data.found.filter(function(s){{ return !s.needs_login; }}).length;
    btn.style.background='#166534'; btn.style.color='#86efac';
    btn.innerHTML='✅ 发现 '+data.found.length+' 个站点';
    setTimeout(function(){{ btn.disabled=false; btn.innerHTML=originalHTML; btn.style.background=''; btn.style.color=''; }}, 4000);

    // 构建弹窗内容
    document.getElementById('discover-list').innerHTML = data.found.map(function(s){{
      const dn = encodeURIComponent(s.name);
      const du = encodeURIComponent(s.url);
      const dd = encodeURIComponent(s.desc||'');
      const dm = encodeURIComponent(JSON.stringify(s.models||[]));
      return '<div class="discover-item" id="di-'+s.name+'">'
        +'<div class="info"><strong>'+s.name+'</strong>'
        +(s.needs_login
          ?'<span style="background:#7f1d1d;color:#fca5a5;font-size:.7rem;padding:1px 6px;border-radius:3px;margin-left:6px">需登录</span>'
          :'<span style="background:#052e16;color:#86efac;font-size:.7rem;padding:1px 6px;border-radius:3px;margin-left:6px">无需登录</span>')
        +'<small>'+s.url+'</small>'
        +(s.models&&s.models.length?'<small style="color:#60a5fa">模型: '+s.models.join(', ')+'</small>':'')
        +'<small style="color:#94a3b8">'+s.desc+'</small></div>'
        +'<div style="display:flex;gap:6px;flex-shrink:0">'
        +'<button class="btn-add" data-name="'+dn+'" data-url="'+du+'" data-desc="'+dd+'" data-models="'+dm+'">添加</button>'
        +'<button class="btn-skip" data-name="'+dn+'">跳过</button>'
        +'</div></div>';
    }}).join('')
    +'<div style="text-align:right;margin-top:10px" id="add-all-wrap">'
    +'<button class="btn btn-primary" id="btn-add-all" style="font-size:.85rem">全部添加 ('+data.found.length+')</button></div>';

    document.getElementById('btn-add-all').onclick = function(){{
      this.disabled=true; this.textContent='添加中...';
      let pending = data.found.length;
      data.found.forEach(function(s){{
        addSite(s.name, s.url, s.desc||'', s.models||[], function(){{
          pending--;
          if(pending===0){{
            const wrap = document.getElementById('add-all-wrap');
            if(wrap) wrap.innerHTML='<span style="color:#86efac;font-size:.85rem">✅ 全部添加完成</span>';
          }}
        }});
      }});
    }};

    // 事件委托
    const list = document.getElementById('discover-list');
    list.onclick = null;
    list.addEventListener('click', function(e){{
      const ab=e.target.closest('.btn-add'), sb=e.target.closest('.btn-skip');
      if(ab) addSite(decodeURIComponent(ab.dataset.name),decodeURIComponent(ab.dataset.url),
                     decodeURIComponent(ab.dataset.desc),JSON.parse(decodeURIComponent(ab.dataset.models||'%5B%5D')));
      if(sb) skipSite(decodeURIComponent(sb.dataset.name));
    }});
    document.getElementById('discover-modal').classList.add('show');
  }}).catch(function(e){{
    btn.style.background='#7f1d1d'; btn.style.color='#fca5a5';
    btn.innerHTML='❌ 刷新失败';
    setTimeout(function(){{ btn.disabled=false; btn.innerHTML=originalHTML; btn.style.background=''; btn.style.color=''; }}, 4000);
  }});
}}

function addSite(name, url, desc, models, callback){{
  const item = document.getElementById('di-'+name);
  if(!item) return;
  const addBtn = item.querySelector('.btn-add');
  const skipBtn = item.querySelector('.btn-skip');
  if(addBtn){{ addBtn.disabled=true; addBtn.textContent='添加中...'; }}
  fetch('/sites/add', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{name,url,desc,models:models||[]}})}})
    .then(r=>r.json()).then(data=>{{
      if(data.success){{
        // 隐藏按钮，只显示成功提示
        if(addBtn) addBtn.style.display='none';
        if(skipBtn) skipBtn.style.display='none';
        item.querySelector('.info').insertAdjacentHTML('afterend',
          '<div style="color:#86efac;font-size:.8rem;padding:4px 0">✅ 已添加</div>');
        // 追加到已接入站点表格
        const tbody=document.getElementById('sites-tbody');
        if(tbody){{
          const mHtml=(data.new_models||[]).map(m=>'<span class="mtag">'+m+'</span>').join('')||'-';
          const loginBadge=LOGIN_REQUIRED_SITES.has(name)
            ?'<a href="'+url+'" target="_blank" style="color:#fbbf24;font-size:.75rem;text-decoration:none">⚠ 需登录</a>'
            :'<span style="color:#22c55e;font-size:.75rem">✓ 免登录</span>';
          tbody.insertAdjacentHTML('beforeend',
            '<tr><td>'+name+'</td><td><a href="'+url+'" target="_blank">'+url+'</a></td>'
            +'<td>'+loginBadge+'</td>'
            +'<td>'+mHtml+'</td></tr>');
        }}
        if(callback) callback();
      }} else {{
        if(addBtn){{ addBtn.disabled=false; addBtn.textContent='添加'; }}
        item.insertAdjacentHTML('beforeend','<div style="color:#fca5a5;font-size:.78rem;margin-top:4px">'+data.message+'</div>');
        if(callback) callback();
      }}
    }}).catch(function(){{
      if(addBtn){{ addBtn.disabled=false; addBtn.textContent='添加'; }}
      if(callback) callback();
    }});
}}

function skipSite(name){{
  const item=document.getElementById('di-'+name);
  item.style.opacity='0.3';
  item.querySelectorAll('button').forEach(function(b){{ b.disabled=true; }});
}}

// 站点健康检查：进度显示在按钮上
function checkSitesHealth(e){{
  const btn = e.currentTarget;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<svg style="animation:spin 1s linear infinite" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M1 4v6h6M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg> 检查中...';

  fetch('/sites/check').then(r=>r.json()).then(data=>{{
    const total = data.results.length;
    const dead = data.results.filter(r=>!r.alive);
    const alive = total - dead.length;

    // 更新表格行状态
    data.results.forEach(function(r){{
      document.querySelectorAll('#sites-tbody tr').forEach(function(tr){{
        const nameCell = tr.cells[0];
        if(!nameCell) return;
        const name = nameCell.textContent.replace(/失效|✓/g,'').trim();
        if(name === r.name){{
          if(!r.alive){{
            tr.style.opacity='0.5';
            nameCell.innerHTML=r.name+' <span style="color:#ef4444;font-size:.75rem">失效</span>';
          }} else {{
            tr.style.opacity='1';
            nameCell.innerHTML=r.name+' <span style="color:#22c55e;font-size:.72rem">✓</span>';
          }}
        }}
      }});
    }});

    // 按钮显示结果
    if(dead.length===0){{
      btn.style.background='#166534'; btn.style.color='#86efac';
      btn.innerHTML='✅ 全部正常 ('+total+')';
    }} else {{
      btn.style.background='#78350f'; btn.style.color='#fbbf24';
      btn.innerHTML='⚠️ '+alive+'/'+total+' 正常';
    }}
    setTimeout(function(){{ btn.disabled=false; btn.innerHTML=originalHTML; btn.style.background=''; btn.style.color=''; }}, 5000);
  }}).catch(function(e){{
    btn.style.background='#7f1d1d'; btn.style.color='#fca5a5';
    btn.innerHTML='❌ 检查失败';
    setTimeout(function(){{ btn.disabled=false; btn.innerHTML=originalHTML; btn.style.background=''; btn.style.color=''; }}, 4000);
  }});
}}

// 定时拉取实时使用记录
function updateUsageList(){{
  fetch('/usage/recent').then(r=>r.json()).then(data=>{{
    if(!data.records||!data.records.length)return;
    const list=document.getElementById('usage-list');
    list.innerHTML=data.records.map(function(r){{
      const t=new Date(r.time*1000);
      const timeStr=t.getHours().toString().padStart(2,'0')+':'+t.getMinutes().toString().padStart(2,'0')+':'+t.getSeconds().toString().padStart(2,'0');
      const status=r.success
        ?'<span style="color:#22c55e;font-size:.8rem">✓</span>'
        :'<span style="color:#ef4444;font-size:.8rem">✗</span>';
      const switchedTag=r.switched_from
        ?'<span style="color:#f59e0b;font-size:.72rem"> ← 切换自 '+r.switched_from+'</span>'
        :'';
      const errTag=r.error
        ?'<span style="color:#ef4444;font-size:.72rem;margin-left:4px">['+r.error+']</span>'
        :'';
      return '<div style="padding:5px 0;border-bottom:1px solid #1a2332;display:flex;align-items:center;gap:6px">'
        +status
        +'<span style="color:#475569;font-size:.75rem;min-width:52px">'+timeStr+'</span>'
        +'<span style="color:#a78bfa;font-weight:600">'+r.site+'</span>'
        +'<span style="color:#64748b">/</span>'
        +'<span style="color:#94a3b8">'+r.model+'</span>'
        +switchedTag+errTag
        +'</div>';
    }}).join('');
  }}).catch(()=>{{}});
}}
setInterval(updateUsageList, 5000);
setTimeout(updateUsageList, 2000);
function doConnect(key,name){{
  closeModal();
  const result=document.getElementById('ide-result');
  result.style.display='block'; result.className=''; result.innerHTML='⏳ 正在写入配置...';
  fetch('/ide/configure/'+key,{{method:'POST'}}).then(r=>r.json()).then(data=>{{
    if(data.success){{
      result.className='ok';
      let html='✅ <strong>'+name+' 配置成功</strong><br><small>已写入: '+data.written[0]+'</small><br>'
        +'<small style="color:#86efac">'+data.message+'</small>';
      if(data.restarted){{
        html+='<br><small style="color:#86efac">🔄 已自动重启 '+name+'</small>';
      }}
      if(data.extra_configs){{
        data.extra_configs.forEach(function(ec){{
          html+='<div class="extra-cfg"><div class="extra-cfg-title">'+ec.label+'</div><pre>'+ec.content+'</pre><button class="btn-copy" onclick="copyText('+JSON.stringify(ec.content)+')">复制</button></div>';
        }});
      }}
      result.innerHTML=html;
    }} else {{
      result.className='err';
      result.innerHTML='⚠️ <strong>'+data.message+'</strong><pre>'+data.manual_config+'</pre><small>base_url: '+data.base_url+'/v1</small><button class="btn-copy" onclick="copyText('+JSON.stringify(data.manual_config)+')">复制</button>';
    }}
  }}).catch(e=>{{ result.className='err'; result.innerHTML='❌ 请求失败: '+e.message; }});
}}

// 邮箱自动登录
async function autoLogin(site, btn){{
  const row = btn.closest('tr');
  const email = row.querySelector('.al-email').value.trim();
  const pass = row.querySelector('.al-pass').value.trim();
  const status = row.querySelector('.al-status');
  const codeRow = row.querySelector('.al-code-row');
  if (!email || !pass) {{ status.textContent = '⚠ 请先填入邮箱和密码'; status.style.color = '#fbbf24'; return; }}
  btn.disabled = true; btn.textContent = '登录中...';
  status.textContent = '⏳ 正在隐藏浏览器中自动登录...'; status.style.color = '#93c5fd';
  try {{
    const r = await fetch('/autologin/' + site, {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email, password: pass}})}});
    const d = await r.json().catch(() => ({{success:false, message:'HTTP '+r.status}}));
    if (d.success) {{
      status.textContent = '✅ ' + d.message; status.style.color = '#22c55e';
      setTimeout(() => location.reload(), 1200);
    }} else if (d.needs_code) {{
      status.textContent = '📩 ' + d.message; status.style.color = '#93c5fd';
      codeRow.style.display = 'flex';
    }} else {{
      status.textContent = '❌ ' + (d.message || d.detail || '登录失败'); status.style.color = '#fca5a5';
      if (d.stage === 'code') codeRow.style.display = 'flex';
    }}
  }} catch(e) {{ status.textContent = '❌ 请求失败: ' + e.message; status.style.color = '#fca5a5'; }}
  btn.disabled = false; btn.textContent = '自动登录';
}}

// 验证码提交
async function autoLoginCode(site, btn){{
  const row = btn.closest('tr');
  const code = row.querySelector('.al-code').value.trim();
  const status = row.querySelector('.al-status');
  if (!code) {{ status.textContent = '⚠ 请输入验证码'; status.style.color = '#fbbf24'; return; }}
  btn.disabled = true; btn.textContent = '提交中...';
  status.textContent = '⏳ 正在提交验证码...'; status.style.color = '#93c5fd';
  try {{
    const r = await fetch('/autologin/' + site + '/code', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{code}})}});
    const d = await r.json().catch(() => ({{success:false, message:'HTTP '+r.status}}));
    if (d.success) {{
      status.textContent = '✅ ' + d.message; status.style.color = '#22c55e';
      setTimeout(() => location.reload(), 1200);
    }} else {{
      status.textContent = '❌ ' + (d.message || '验证失败'); status.style.color = '#fca5a5';
    }}
  }} catch(e) {{ status.textContent = '❌ 请求失败: ' + e.message; status.style.color = '#fca5a5'; }}
  btn.disabled = false; btn.textContent = '提交验证码';
}}

// 手动登录：以可见模式打开站点（登录态存入 profile）
async function openForManualLogin(site){{
  try {{ await fetch('/login/' + site, {{method:'POST'}}); }} catch(e) {{}}
}}

// ── 模型市场 ─────────────────────────────────────────────
const GW = 'http://127.0.0.1:8787';

async function marketRefresh(btn) {{
  btn.disabled = true; const old = btn.textContent; btn.textContent = '拉新中…';
  const status = document.getElementById('mkt-status');
  status.textContent = '⏳ 正在拉取 OpenRouter 最新免费目录 + 扫描本机 Ollama...'; status.style.color = '#93c5fd';
  try {{
    const r = await fetch(GW + '/market/refresh', {{method:'POST'}});
    const d = await r.json();
    let msg = '✅ 拉新完成：免费模型 ' + d.free_total + ' 个';
    if (d.new_free && d.new_free.length) msg += '（新增 ' + d.new_free.length + ' 个: ' + d.new_free.slice(0,3).join(', ') + '…）';
    if (d.local_added && d.local_added.length) msg += '，本机新接入: ' + d.local_added.join(', ');
    status.textContent = msg; status.style.color = '#22c55e';
    await loadMarket();
    await refreshZedModels(document.getElementById('btn-zed-refresh'));
    status.textContent += ' ｜ 已自动刷新到 Zed';
  }} catch(e) {{
    status.textContent = '❌ 拉新失败: ' + e.message; status.style.color = '#fca5a5';
  }}
  btn.disabled = false; btn.textContent = '🔄 拉新';
}}

async function loadMarket() {{
  // 免费模型
  const freeBox = document.getElementById('mkt-free');
  // 本地模型（已装 + 推荐目录 + 设备自适应）
  const localBox = document.getElementById('mkt-local');
  try {{
    const r = await fetch(GW + '/local-models');
    const d = await r.json();
    const installed = {{}};
    d.installed.forEach(x => installed[x.ollama_id] = x.id);
    const dev = d.device || {{}};
    const devLine = '当前设备: ' + (dev.ram_total_gb || '?') + 'GB 内存' +
      (dev.ram_free_gb != null ? '（当前可用 ' + dev.ram_free_gb + 'GB）' : '') +
      ' · ' + (dev.gpu || 'CPU') +
      (dev.ram_free_gb != null && dev.ram_free_gb < 4 ? ' ⚠️ 可用内存偏低，建议先关闭占内存的程序' : '');
    const devEl = document.getElementById('mkt-device');
    if (devEl) devEl.textContent = devLine;
    const FIT = {{recommended: ['✅推荐', '#052e16', '#86efac'],
                 tight: ['⚠️勉强', '#3a2e05', '#fbbf24'],
                 not_recommended: ['❌不建议', '#2d0a0a', '#fca5a5'],
                 unknown: ['?', '#1e293b', '#94a3b8']}};
    localBox.innerHTML = d.curated.map(c => {{
      const isInstalled = !!installed[c.ollama_id];
      const job = d.pull_jobs[c.ollama_id];
      const f = FIT[c.fit] || FIT.unknown;
      return '<div class="mkt-item"><div><span class="nm">' + c.label + '</span>' +
        '<span class="tag" style="background:' + f[1] + ';color:' + f[2] + '">' + f[0] + '</span>' +
        (isInstalled ? '<span class="tag tag-ok">已装</span>' : '') +
        (job && job.status === 'pulling' ? '<span class="tag tag-pending">拉取中…</span>' : '') +
        '<div class="full">' + c.note + ' · ' + (c.speed || '') + ' · ' + (c.reason || '') + '</div></div>' +
        (isInstalled ? '<button class="mkt-btn" disabled>已接入</button>'
          : (c.fit === 'not_recommended' ? '<span class="tag" style="color:#475569">本机不支持</span>'
          : '<button class="mkt-btn" onclick="pullModel(\\'' + c.ollama_id + '\\', this)">拉取并接入</button>')) +
        '</div>';
    }}).join('');
  }} catch(e) {{ localBox.textContent = '网关未运行(8787)，无法加载本地模型列表'; }}

  try {{
    const r2 = await fetch(GW + '/v1/models');
    const d2 = await r2.json();
    const orids = d2.data.map(x => x.id).filter(i => i.indexOf('or/') === 0);
    freeBox.innerHTML = orids.length
      ? orids.map(i => '<div class="mkt-item"><div><span class="nm">' + i + '</span></div><span class="tag tag-ok">免费可用</span></div>').join('')
      : '暂无免费模型';
  }} catch(e) {{ freeBox.textContent = '网关未运行(8787)，无法加载免费模型列表'; }}
}}

async function pullModel(id, btn) {{
  const status = document.getElementById('mkt-status');
  btn.disabled = true; btn.textContent = '拉取中…';
  status.textContent = '⏳ 正在拉取 ' + id + '（约数分钟，完成后点「一键加载到 Zed」）';
  try {{
    await fetch(GW + '/local-models/add', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{ollama_id: id}})}});
    // 轮询状态
    const timer = setInterval(async () => {{
      const s = await (await fetch(GW + '/local-models/status')).json();
      const job = s.jobs[id];
      if (job && job.status === 'pulling') {{ status.textContent = '⏳ 拉取中 ' + id + '...'; return; }}
      clearInterval(timer);
      if (job && job.status === 'done') {{
        status.textContent = '✅ ' + id + ' 拉取完成，点「一键加载到 Zed」接入'; status.style.color = '#22c55e';
      }} else {{
        status.textContent = '❌ 拉取失败: ' + (job && job.error || '未知'); status.style.color = '#fca5a5';
      }}
      loadMarket();
    }}, 5000);
  }} catch(e) {{ status.textContent = '❌ ' + e.message; }}
}}

async function refreshZedModels(btn) {{
  btn.disabled = true; const old = btn.textContent; btn.textContent = '写入中…';
  const status = document.getElementById('mkt-status');
  try {{
    const r = await fetch('/zed/refresh-models', {{method:'POST'}});
    const d = await r.json();
    if (d.success) {{
      status.textContent = '✅ 已写入 Zed：' + JSON.stringify(d.groups) + '（选择器重开即见）';
      status.style.color = '#22c55e';
    }} else {{ status.textContent = '❌ ' + JSON.stringify(d); status.style.color = '#fca5a5'; }}
  }} catch(e) {{ status.textContent = '❌ ' + e.message; status.style.color = '#fca5a5'; }}
  btn.disabled = false; btn.textContent = old;
}}

loadMarket();

// 站点页签切换（可用站点 / 待登录）
document.querySelectorAll('.stab').forEach(function(tab){{
  tab.addEventListener('click', function(){{
    const target = tab.dataset.stab;
    document.querySelectorAll('.stab').forEach(function(t){{ t.classList.remove('active'); }});
    document.querySelectorAll('.stab-content').forEach(function(c){{ c.classList.remove('active'); }});
    tab.classList.add('active');
    document.querySelector('.stab-content[data-stab="'+target+'"]').classList.add('active');
  }});
}});

// 使用说明标签页切换
document.querySelectorAll('.tab').forEach(function(tab){{
  tab.addEventListener('click', function(){{
    const target = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(function(t){{ t.classList.remove('active'); }});
    document.querySelectorAll('.tab-content').forEach(function(c){{ c.classList.remove('active'); }});
    tab.classList.add('active');
    document.querySelector('.tab-content[data-tab="'+target+'"]').classList.add('active');
  }});
}});
</script>
</body>
</html>""".replace("{ide_svgs_json}", ide_svgs_json))



@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    import os as _os
    # 盾牌 ICO（与桌面快捷方式同一文件），Edge 应用窗口/任务栏图标跟随
    ico = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "bridge.ico")
    if _os.path.exists(ico):
        with open(ico, "rb") as f:
            return Response(content=f.read(), media_type="image/x-icon",
                            headers={"Cache-Control": "no-cache"})
    # 兜底：简单盾牌 SVG
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<path d="M16 2 L28 7 L28 17 C28 23 22 28 16 30 C10 28 4 23 4 17 L4 7 Z"'
        ' fill="#7c3aed" stroke="#a78bfa" stroke-width="1.5"/>'
        '<path d="M11 16 L14 19 L21 12" stroke="white" stroke-width="2.5"'
        ' fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": int(time.time())}


@app.get("/login/status")
async def login_status():
    """获取所有站点的登录状态"""
    from browser import profile_manager
    status = profile_manager.get_all_login_status()
    # 也包含已知站点但没有 profile 记录的
    all_sites = {s.name: s for s in config.get_enabled_sites()}
    result = {}
    for site_name in all_sites:
        result[site_name] = {
            "url": all_sites[site_name].url,
            "has_profile": profile_manager.has_profile(site_name),
            "is_logged_in": status.get(site_name, {}).get("is_logged_in"),
            "last_used": status.get(site_name, {}).get("last_used"),
            "login_check_time": status.get(site_name, {}).get("login_check_time"),
        }
    return {"sites": result}


# ── 邮箱自动登录：管理页「邮箱登录」页签的配置表单走这两个端点 ──
# 通用流程：打开站点 → 点"登录"入口 → 填邮箱/密码 → 提交 → 等待登录态
# 若站点改为邮箱验证码模式（邮箱密码无密码框），用 /autologin/{site}/code 填码


@app.post("/autologin/{site_name}")
async def autologin_email(site_name: str, request: Request):
    """用邮箱+密码自动登录指定站点"""
    from browser import profile_manager, browser_manager

    site = config.get_site(site_name)
    if not site:
        raise HTTPException(status_code=404, detail=f"站点 {site_name} 不存在")
    body = await request.json()
    email = (body.get("email") or "").strip()
    password = (body.get("password") or "").strip()
    if not email or not password:
        raise HTTPException(status_code=400, detail="需要 email 和 password")

    console.print(f"[cyan]📧 [autologin] {site_name}: {email}[/cyan]")
    try:
        page = await browser_manager.get_page(site_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"浏览器启动失败: {e}")

    login_url = site.selectors.get("login_url") or site.url
    try:
        await page.goto(login_url, timeout=30000)
        await asyncio.sleep(3)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开站点失败: {e}")

    # 1) 尝试点开"登录"入口（落地页 → 登录页）
    entry_sels = [
        'button:has-text("登录")', 'a:has-text("登录")',
        'button:has-text("Sign in")', 'a:has-text("Sign in")',
        'button:has-text("Log in")', 'a:has-text("Log in")',
        'button:has-text("登录")', 'a:has-text("登录")',
        '[class*="login" i] button', '[class*="login" i] a',
    ]
    for sel in entry_sels:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                t = (await el.inner_text() or "").strip()
                if 0 < len(t) <= 12:          # 避免误点长文本
                    await el.click(timeout=3000)
                    console.print(f"[cyan]🔗 [autologin] 点击入口: {t[:20]}[/cyan]")
                    await asyncio.sleep(2.5)
                    break
        except Exception:
            continue

    # 2) 填邮箱（通用定位）
    email_sels = ['input[type="email"]', 'input[name*="email" i]',
                  'input[placeholder*="邮箱" i]', 'input[placeholder*="email" i]',
                  'input[name*="user" i]', 'input[autocomplete="username"]']
    filled_email = False
    for sel in email_sels:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.fill(email)
                filled_email = True
                break
        except Exception:
            continue
    if not filled_email:
        return {"success": False, "stage": "email",
                "message": "未找到邮箱输入框（该站点可能只支持手机号/第三方登录，请手动登录）"}

    # 3) 填密码
    await asyncio.sleep(1)
    pwd_found = None
    for sel in ['input[type="password"]']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.fill(password)
                pwd_found = el
                break
        except Exception:
            continue

    if not pwd_found:
        # 无密码框 ⇒ 很可能是邮箱验证码模式：提交邮箱，等用户收码
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
        await asyncio.sleep(2)
        return {"success": False, "stage": "code",
                "needs_code": True,
                "message": "该站点是邮箱验证码登录：验证码已发送到邮箱，请在下方输入收到的验证码"}

    # 4) 提交
    await asyncio.sleep(0.5)
    submit_sels = ['button[type="submit"]',
                   'button:has-text("登录")', 'button:has-text("Sign in")',
                   'button:has-text("Log in")', 'button:has-text("Continue")']
    for sel in submit_sels:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible() and not el.evaluate("e => e.disabled"):
                await el.click(timeout=3000)
                break
        except Exception:
            continue
    else:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    # 5) 等待登录态（最多 60s）
    sel_login = site.selectors.get("logged_in_selector")
    for _ in range(20):
        await asyncio.sleep(3)
        if sel_login:
            try:
                el = await page.query_selector(sel_login)
                if el and await el.is_visible():
                    profile_manager.set_login_status(site_name, True, "autologin 成功")
                    rate_limiter.reset_site(site_name)
                    console.print(f"[green]✅ [autologin] {site_name} 登录成功[/green]")
                    return {"success": True, "message": "登录成功，站点已提升为可用"}
            except Exception:
                pass
        else:
            try:
                pw = await page.query_selector('input[type="password"]')
                if not pw or not await pw.is_visible():
                    profile_manager.set_login_status(site_name, True, "autologin 成功")
                    return {"success": True, "message": "登录成功，站点已提升为可用"}
            except Exception:
                pass

    # 仍在登录页 ⇒ 可能需要验证码
    code_present = False
    for sel in ['input[placeholder*="验证码" i]', 'input[name*="code" i]',
                'input[inputmode="numeric"]', 'input[autocomplete="one-time-code"]']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                code_present = True
                break
        except Exception:
            continue
    if code_present:
        return {"success": False, "stage": "code", "needs_code": True,
                "message": "站点要求输入验证码，请在下方填入邮件/短信中收到的验证码"}
    return {"success": False, "stage": "unknown",
            "message": "登录未完成（可能有图形验证码或 SSO 限制），可点'打开站点'手动登录一次"}


@app.post("/autologin/{site_name}/code")
async def autologin_code(site_name: str, request: Request):
    """邮箱/短信验证码阶段：填入验证码完成登录"""
    from browser import profile_manager, browser_manager

    site = config.get_site(site_name)
    if not site:
        raise HTTPException(status_code=404, detail=f"站点 {site_name} 不存在")
    body = await request.json()
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="需要 code")

    try:
        page = await browser_manager.get_page(site_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"浏览器上下文失效，请重新发起登录: {e}")

    code_sels = ['input[placeholder*="验证码" i]', 'input[name*="code" i]',
                 'input[autocomplete="one-time-code"]', 'input[inputmode="numeric"]',
                 'input[type="text"]']
    filled = False
    for sel in code_sels:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.fill(code)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        return {"success": False, "message": "未找到验证码输入框，请手动输入"}

    await asyncio.sleep(0.5)
    submitted = False
    for sel in ['button[type="submit"]', 'button:has-text("验证")',
                'button:has-text("登录")', 'button:has-text("Verify")',
                'button:has-text("Continue")']:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible() and not el.evaluate("e => e.disabled"):
                await el.click(timeout=3000)
                submitted = True
                break
        except Exception:
            continue
    if not submitted:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    sel_login = site.selectors.get("logged_in_selector")
    for _ in range(20):
        await asyncio.sleep(3)
        if sel_login:
            try:
                el = await page.query_selector(sel_login)
                if el and await el.is_visible():
                    profile_manager.set_login_status(site_name, True, "autologin code 成功")
                    rate_limiter.reset_site(site_name)
                    return {"success": True, "message": "验证码登录成功，站点已提升为可用"}
            except Exception:
                pass
    return {"success": False, "message": "验证码提交后未检测到登录态，请确认验证码是否正确或手动完成"}


@app.post("/zed/refresh-models")
async def zed_refresh_models():
    """只更新 Zed settings.json 的三组模型列表，不重启 Zed、不动其他配置。
    供管理页「模型市场」的一键加载使用。"""
    import httpx as _hx
    import os as _os
    from browser import profile_manager as _pm

    p = _os.path.join(_os.path.expanduser("~"), "AppData", "Roaming", "Zed", "settings.json")
    if not _os.path.exists(p):
        raise HTTPException(status_code=404, detail="未找到 Zed settings.json")

    site_models = get_site_model_ids()
    ready = [m for m in site_models
             if m not in LOGIN_REQUIRED_SITES and _pm.get_login_status(m) is not False]
    pending = [m for m in site_models if m in LOGIN_REQUIRED_SITES]
    or_ids = []
    try:
        import urllib.request
        # 绕过系统代理（本地网关直连，否则代理返回 502）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8787/v1/models", timeout=60) as resp:
            gw_data = json.loads(resp.read().decode("utf-8"))
        # or/* 免费模型 + local-* 本地无限制模型都要进 Zed
        or_ids = [m["id"] for m in gw_data.get("data", [])
                  if m["id"].startswith("or/") or m["id"].startswith("local")]
    except Exception as e:
        console.print(f"[yellow]⚠️ 网关模型拉取失败(保留现有): {e}[/yellow]")

    def _zm(names):
        return [{"name": n, "display_name": n, "max_tokens": 16384,
                 "supports_tools": False, "supports_images": False} for n in names]

    def _short(mid):
        part = mid[3:] if mid.startswith("or/") else mid
        part = part.split(":")[0].split("/")[-1]
        toks = part.split("-")
        return "-".join(toks[:2])[:18]

    # bridge 组：openrouter(auto链) + or/* 具体模型（简写名，所见即所得）
    bridge_models = _zm(ready)
    seen = {}
    for i in or_ids:
        s = _short(i)
        seen[s] = seen.get(s, 0) + 1
        if seen[s] > 1:
            s = f"{s}·{seen[s]}"
        bridge_models.append({"name": i, "display_name": s, "max_tokens": 16384,
                              "supports_tools": False, "supports_images": False})

    d = json.load(open(p, encoding="utf-8"))
    oc = d.setdefault("language_models", {}).setdefault("openai_compatible", {})
    prev_or = oc.get("openrouter")
    oc["bridge"] = {"api_url": f"http://127.0.0.1:{config.PORT}/v1", "api_key": "any",
                    "available_models": bridge_models}
    oc["pending-login"] = {"api_url": f"http://127.0.0.1:{config.PORT}/v1", "api_key": "any",
                           "available_models": _zm(pending)}
    if or_ids:
        oc["openrouter"] = {"api_url": "http://127.0.0.1:8787/v1", "api_key": "any",
                            "available_models": _zm(["auto"] + or_ids)}
    elif prev_or and prev_or.get("available_models"):
        oc["openrouter"] = prev_or
    oc.pop("openai-compatible", None)
    oc.pop("openai_compatible_legacy", None)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    return {"success": True,
            "groups": {k: len(v["available_models"]) for k, v in oc.items()}}


@app.post("/login/{site_name}")
async def login_site(site_name: str):
    """
    一键登录指定站点
    - 如果已有 Profile 且登录有效，直接使用（浏览器保持隐藏，不弹窗）
    - 如果登录失效/缺失，强制弹出【可见】浏览器让用户登录，完成后自动恢复隐藏
    """
    from browser import profile_manager, browser_manager

    site = config.get_site(site_name)
    if not site:
        raise HTTPException(status_code=404, detail=f"站点 {site_name} 不存在")

    console.print(f"[cyan]🔐 正在检查 {site_name} 登录状态...[/cyan]")

    # 检查是否已有有效登录（此阶段浏览器保持隐藏，不弹窗打扰）
    existing_login = profile_manager.get_login_status(site_name)
    has_profile = profile_manager.has_profile(site_name)

    if existing_login and has_profile:
        console.print(f"[green]✅ {site_name} 已登录，直接使用现有 Profile[/green]")
        try:
            page = await browser_manager.get_page(site_name)
            await page.goto(site.url, timeout=10000)
            await asyncio.sleep(2)

            if await _check_still_logged_in(page, site_name):
                profile_manager.set_login_status(site_name, True, "登录有效")
                return {
                    "success": True,
                    "site": site_name,
                    "message": "使用现有登录",
                    "url": f"http://127.0.0.1:18792/login/{site_name}"
                }
            console.print(f"[yellow]⚠️ {site_name} 登录已过期，需要重新登录[/yellow]")
            profile_manager.set_login_status(site_name, False, "登录已过期")
        except Exception as e:
            console.print(f"[yellow]⚠️ 验证现有登录失败: {e}[/yellow]")

    # 需要人工登录：清掉旧上下文，强制弹出可见窗口
    browser_manager.visible_sites.add(site_name)
    try:
        try:
            await browser_manager.clear_site(site_name)
        except Exception:
            pass
        console.print(f"[yellow]🔓 {site_name} 需要登录，正在打开可见浏览器...[/yellow]")
        page = await browser_manager.get_page(site_name)
        await page.goto(site.url, timeout=15000)
        await asyncio.sleep(3)

        # 等待用户登录（最多 10 分钟）
        login_success = await _wait_for_login(page, site_name, timeout_minutes=10)

        if login_success:
            profile_manager.set_login_status(site_name, True, "登录成功")
            rate_limiter.reset_site(site_name)
            console.print(f"[green]✅ {site_name} 登录成功！[/green]")
            return {"success": True, "site": site_name, "message": "登录成功"}
        profile_manager.set_login_status(site_name, False, "登录超时")
        return {"success": False, "site": site_name, "message": "登录超时"}
    except Exception as e:
        console.print(f"[red]❌ 登录失败: {e}[/red]")
        return {"success": False, "site": site_name, "message": str(e)}
    finally:
        browser_manager.visible_sites.discard(site_name)


async def _check_still_logged_in(page, site_name: str) -> bool:
    """检查页面是否仍然处于登录状态。
    站点配置 logged_in_selector 时导航到 login_check_url 并按组合条件判断
    （落地页/营销页没有密码表单，通用检测会被骗过 —— 如 z.ai 改版后的 zchat.z.ai）"""
    site = config.get_site(site_name)
    sel = site.selectors.get("logged_in_selector") if site else None
    if sel:
        check_url = site.selectors.get("login_check_url") or site.url
        try:
            await page.goto(check_url, timeout=20000)
            await asyncio.sleep(3)
            return await match_login_state(page, sel)
        except Exception as e:
            console.print(f"[yellow]⚠️ {site_name} 登录校验失败: {e}[/yellow]")
            return False

    # 通用检查：URL 是否包含登录相关关键词
    url = page.url.lower()
    login_kws = ["login", "signin", "sign-in", "auth", "register", "signup", "logged-out", "signout"]
    if any(kw in url for kw in login_kws):
        return False

    # 检查是否存在登录表单
    try:
        pw = await page.query_selector('input[type="password"]')
        if pw and await pw.is_visible():
            return False
    except Exception:
        pass

    return True


async def _wait_for_login(page, site_name: str, timeout_minutes: int = 10) -> bool:
    """等待用户完成登录"""
    site = config.get_site(site_name)
    sel = site.selectors.get("logged_in_selector") if site else None
    for i in range(timeout_minutes * 12):  # 每5秒检查一次
        await asyncio.sleep(5)
        try:
            if sel:
                # 站点感知：按组合条件判断（每 5 次导航回 login_check_url 校验一次）
                if i % 5 == 0:
                    check_url = site.selectors.get("login_check_url") or site.url
                    try:
                        await page.goto(check_url, timeout=20000)
                    except Exception:
                        pass
                if await match_login_state(page, sel):
                    return True
            else:
                # 检查 URL 是否离开登录页
                url = page.url.lower()
                login_kws = ["login", "signin", "sign-in", "auth", "register", "signup"]
                if not any(kw in url for kw in login_kws):
                    # 额外检查登录表单
                    pw = await page.query_selector('input[type="password"]')
                    if not pw or not await pw.is_visible():
                        return True
        except Exception:
            pass

    return False


@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(None)):
    verify_key(authorization)
    # 每个站点暴露为一个模型，ID = 站点名（IDE 模型选择器显示桥接站点名）
    # 需登录站点若登录态已明确失效(is_logged_in=False)则不暴露，
    # 避免选中后必失败的体验；None(未检测过) 视为可用
    from browser import profile_manager
    models = []
    for site in config.get_enabled_sites():
        if site.name in LOGIN_REQUIRED_SITES and \
                profile_manager.get_login_status(site.name) is False:
            continue
        models.append({
            "id": site.name,
            "object": "model",
            "created": int(time.time()),
            "owned_by": site.name,
        })
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest, authorization: Optional[str] = Header(None)):
    verify_key(authorization)

    # or/* 前缀 = OpenRouter 具体模型直通网关；local 前缀 = 本地 Ollama 模型直通网关
    # （Zed bridge 组的具体模型条目都走这条快速路径，无浏览器）
    rm = request.model or ""
    if rm.startswith("or/") or rm.startswith("local"):
        console.print(f"[cyan]📨 [gateway-proxy] {rm} stream={request.stream}[/cyan]")
        openai_msgs = [{"role": m.role, "content": m.get_text()} for m in request.messages]
        return await _gateway_proxy(openai_msgs, request.stream, requested_model=rm)

    site_name, model_name = resolve_site_model(request.model)
    if not site_name:
        site_name = request.site
    if not site_name:
        site_name = await load_balancer.select_site(model_name)
        if not site_name:
            raise HTTPException(503, "没有可用站点")

    messages = [Message(role=m.role, content=m.get_text()) for m in request.messages]

    # openrouter 站点 = 代理到 free-gateway(8787) 的 or/* 模型链，
    # 不做浏览器自动化（网页沙盒游客提交被拦，且 Zed 大上下文逐字输入极慢）
    if site_name == "openrouter":
        # 转发前瘦身：免费模型对超大上下文慢且易拒，单条 4000 / 总量 12000 字符
        slim, total = [], 0
        for m in reversed(messages):
            if m.role == "system":
                continue
            content = (m.content or "")[:4000]
            slim.append({"role": m.role, "content": content})
            total += len(content)
            if total >= 12000:
                break
        slim.reverse()
        if not slim:
            slim = [{"role": "user", "content": "hello"}]
        console.print(f"[cyan]📨 [gateway-proxy] or-auto msgs={len(slim)} chars={sum(len(x['content']) for x in slim)} stream={request.stream}[/cyan]")
        return await _gateway_proxy(slim, request.stream)

    # 网页桥接瘦身（与 anthropic 路径同策略）：IDE 会发来几万字上下文，
    # 网页逐字模拟输入撑不住 —— 丢弃 system 脚手架、单条截 2000、总长 6000
    MAX_MSG_CHARS, MAX_TOTAL_CHARS = 2000, 6000
    slim, total = [], 0
    for m in reversed(messages):
        if m.role == "system":
            continue
        content = (m.content or "")[:MAX_MSG_CHARS]
        slim.append(Message(role=m.role, content=content))
        total += len(content)
        if total >= MAX_TOTAL_CHARS:
            break
    slim.reverse()
    if slim:
        messages = slim
    console.print(f"[dim]✂️ 网页瘦身: {len(messages)} 条消息, 共 {sum(len(m.content) for m in messages)} 字符[/dim]")

    # 冷却快拒：站点此前失败进入冷却期时，直接秒级返回，不跑慢流程
    cd = rate_limiter.cooldown_remaining(site_name)
    if cd > 5:
        mins = int(cd // 60)
        raise HTTPException(429, f"{site_name} 冷却中（此前请求失败），剩余约 {mins} 分钟 {int(cd % 60)} 秒。"
                                 f"若是登录问题请先完成登录；冷却结束会自动恢复")

    console.print(f"[cyan]📨 site={site_name} model={model_name} msgs={len(messages)} stream={request.stream}[/cyan]")

    if request.stream:
        return StreamingResponse(
            _stream_sse_with_fallback(messages, model_name, site_name, request.stream),
            media_type="text/event-stream"
        )
    else:
        return await _blocking_with_fallback(messages, model_name, site_name)


GATEWAY_BASE = "http://127.0.0.1:8787"   # free-gateway（or/* 模型链）


async def _gateway_proxy(openai_msgs: list, stream: bool, requested_model: str = "auto"):
    """把 openrouter 请求转发到 free-gateway 并原样透传（流式或整块）
    requested_model: 'auto'（自动链）或 'or/<model>'（指定模型直通）"""
    import httpx
    payload = {"model": requested_model, "messages": openai_msgs, "stream": bool(stream)}
    try:
        if stream:
            async def gen():
                async with httpx.AsyncClient(timeout=600) as cli:
                    async with cli.stream("POST", f"{GATEWAY_BASE}/v1/chat/completions",
                                          json=payload) as r:
                        async for chunk in r.aiter_bytes():
                            yield chunk
            return StreamingResponse(gen(), media_type="text/event-stream")
        async with httpx.AsyncClient(timeout=600) as cli:
            r = await cli.post(f"{GATEWAY_BASE}/v1/chat/completions", json=payload)
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(502, f"free-gateway 未运行({GATEWAY_BASE}): {e}")


async def _try_with_fallback(messages, model_name, original_site):
    """尝试请求，失败时自动切换到其他站点（无感切换）"""
    tried = set()
    current_site = original_site
    prev_site = None

    while True:
        tried.add(current_site)
        provider = get_provider(current_site)
        chunks = []
        error_msg = None

        try:
            async for chunk in provider.chat(messages, model_name, stream=False):
                if chunk.startswith("[ERROR]"):
                    error_msg = chunk
                    break
                chunks.append(chunk)
        except Exception as e:
            error_msg = str(e)

        if not error_msg:
            _record_usage(current_site, model_name, True, switched_from=prev_site)
            return current_site, model_name, "".join(chunks), None

        # 登录错误：直接返回，不切换，让上层处理
        if "[ERROR:LOGIN_REQUIRED:" in error_msg:
            _record_usage(current_site, model_name, False, error_msg)
            return current_site, model_name, None, error_msg

        _record_usage(current_site, model_name, False, error_msg)
        console.print(f"[yellow]⚠️ {current_site}/{model_name} 失败: {error_msg}，尝试切换...[/yellow]")

        next_site = await load_balancer.select_site_exclude(model_name, exclude=tried)
        if not next_site:
            return current_site, model_name, None, error_msg
        console.print(f"[cyan]🔄 切换到 {next_site}[/cyan]")
        prev_site = current_site
        current_site = next_site


def _record_usage(site: str, model: str, success: bool, error: str = None, switched_from: str = None):
    """记录使用情况，最多保留 100 条"""
    # 简化错误信息
    short_error = None
    if error:
        if "登录" in error or "login" in error.lower():
            short_error = "需要登录"
        elif "超时" in error or "timeout" in error.lower():
            short_error = "超时"
        elif "限制" in error or "limit" in error.lower() or "rate" in error.lower():
            short_error = "超限"
        else:
            short_error = error[:25]
    _usage_history.insert(0, {
        "time": int(time.time()),
        "site": site,
        "model": model,
        "success": success,
        "error": short_error,
        "switched_from": switched_from,
    })
    if len(_usage_history) > 100:
        _usage_history.pop()


@app.get("/usage/recent")
async def usage_recent():
    """返回最近的模型使用记录"""
    return {"records": _usage_history[:20]}


@app.post("/login/open")
async def login_open(url: str):
    """用系统默认浏览器打开登录页面"""
    import webbrowser
    try:
        webbrowser.open(url)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _stream_sse_with_fallback(messages, model_name, original_site, stream) -> AsyncGenerator[bytes, None]:
    """带自动切换的 SSE 流式响应"""
    rid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())
    tried = set()
    current_site = original_site
    prev_site = None

    while True:
        tried.add(current_site)
        provider = get_provider(current_site)
        got_content = False
        error_msg = None

        try:
            async for chunk in provider.chat(messages, model_name, stream=True):
                if chunk.startswith("[ERROR]"):
                    error_msg = chunk
                    break
                got_content = True
                data = {
                    "id": rid, "object": "chat.completion.chunk", "created": created,
                    "model": f"{current_site}/{model_name}",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": chunk}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
        except Exception as e:
            error_msg = str(e)

        if not error_msg:
            _record_usage(current_site, model_name, True, switched_from=prev_site)
            done_data = json.dumps({
                "id": rid, "object": "chat.completion.chunk", "created": created,
                "model": f"{current_site}/{model_name}",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }, ensure_ascii=False)
            yield f"data: {done_data}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return

        _record_usage(current_site, model_name, False, error_msg)
        if got_content:
            yield b"data: [DONE]\n\n"
            return

        # 登录错误：返回特殊错误码，不切换
        if "[ERROR:LOGIN_REQUIRED:" in error_msg:
            yield f"data: {json.dumps({'error': {'message': error_msg, 'code': 'login_required'}}, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return

        next_site = await load_balancer.select_site_exclude(model_name, exclude=tried)
        if not next_site:
            yield f"data: {json.dumps({'error':{'message':error_msg}})}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return
        console.print(f"[cyan]🔄 流式切换到 {next_site}[/cyan]")
        prev_site = current_site
        current_site = next_site


async def _blocking_with_fallback(messages, model_name, original_site):
    site, model, text, error = await _try_with_fallback(messages, model_name, original_site)
    if error:
        raise HTTPException(500, error)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"{site}/{model}",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": sum(len(m.content) // 4 for m in messages),
            "completion_tokens": len(text) // 4,
            "total_tokens": sum(len(m.content) // 4 for m in messages) + len(text) // 4,
        },
    }


# ── Anthropic 兼容层：Claude Code 通过 ANTHROPIC_BASE_URL 直连桥接 ──
def _blocks_to_text(content) -> str:
    """Anthropic content 可能是字符串或 block 数组，统一提取文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


_SYSTEM_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def _verify_anthropic_auth(authorization: Optional[str], x_api_key: Optional[str]):
    """兼容 x-api-key 与 Authorization Bearer 两种鉴权头"""
    verify_key(x_api_key and f"Bearer {x_api_key}" or authorization)


async def _anthropic_messages_impl(body: dict, stream: bool):
    pinned_site, model = resolve_site_model(body.get("model"))

    MAX_SYS_CHARS = 300     # 超过视为客户端内置提示词(如 Claude Code 的 ~14k 工具说明)，丢弃
    MAX_MSG_CHARS = 2000    # 单条消息限长，网页打字模拟才能在合理时间内完成

    system_text = _blocks_to_text(body.get("system"))
    if len(system_text) > MAX_SYS_CHARS:
        console.print(f"[dim]✂️ 丢弃超长 system prompt ({len(system_text)} 字符, 网页桥接用不上)[/dim]")
        system_text = ""

    messages = []
    if system_text:
        messages.append(Message(role="system", content=system_text))
    for m in body.get("messages", []):
        role = m.get("role", "user")
        text = _blocks_to_text(m.get("content"))
        if not text:
            continue
        # 网页桥接瘦身：
        # 1) messages 数组里的 system 条目是客户端脚手架(agent 清单等)，整条丢弃
        if role == "system":
            continue
        # 2) 用户消息里的 <system-reminder> 包装是 Claude Code 注入的环境说明，剥掉
        text = _SYSTEM_RE.sub("", text).strip()
        if not text:
            continue
        messages.append(Message(role=role, content=text[:MAX_MSG_CHARS]))

    if not messages:
        raise HTTPException(400, "messages 为空")

    console.print(f"[cyan]📨 [anthropic] model={model} site={pinned_site or 'auto'} msgs={len(messages)} stream={stream}[/cyan]")
    if stream:
        # _stream_sse_anthropic 是 async generator，需交给 StreamingResponse 消费
        return StreamingResponse(_stream_sse_anthropic(messages, model, pinned_site),
                                 media_type="text/event-stream")
    return await _blocking_anthropic(messages, model, pinned_site)


def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _blocking_anthropic(messages, model_name: str, pinned_site: Optional[str] = None):
    site = await load_balancer.select_site(model_name, preferred_site=pinned_site)
    if not site:
        raise HTTPException(503, "没有可用站点")
    site, model, text, error = await _try_with_fallback(messages, model_name, site)
    if error:
        raise HTTPException(502, error)
    input_tokens = sum(len(m.content) // 4 for m in messages)
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens,
                  "output_tokens": max(1, len(text) // 4)},
    }


async def _stream_sse_anthropic(messages, model_name: str, pinned_site: Optional[str] = None) -> AsyncGenerator[bytes, None]:
    """Anthropic SSE 流式响应，失败时自动切换站点（指定站点时固定不改）"""
    current_site = await load_balancer.select_site(model_name, preferred_site=pinned_site)
    if not current_site:
        yield _sse("error", {"type": "error",
                             "error": {"type": "api_error", "message": "没有可用站点"}})
        return

    base_event = {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message",
                  "role": "assistant", "model": model_name, "content": [],
                  "stop_reason": None, "stop_sequence": None}
    tried = set()

    while True:
        tried.add(current_site)
        provider = get_provider(current_site)
        got_content = False
        error_msg = None

        try:
            async for chunk in provider.chat(messages, model_name, stream=True):
                if chunk.startswith("[ERROR]"):
                    error_msg = chunk
                    break
                if not got_content:
                    got_content = True
                    yield _sse("message_start", {"type": "message_start",
                                                 "message": {**base_event,
                                                             "usage": {"input_tokens": 0,
                                                                       "output_tokens": 0}}})
                    yield _sse("content_block_start",
                               {"type": "content_block_start", "index": 0,
                                "content_block": {"type": "text", "text": ""}})
                    yield _sse("ping", {"type": "ping"})
                yield _sse("content_block_delta",
                           {"type": "content_block_delta", "index": 0,
                            "delta": {"type": "text_delta", "text": chunk}})
        except Exception as e:
            error_msg = str(e)

        if not error_msg:
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield _sse("message_delta",
                       {"type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 0}})
            yield _sse("message_stop", {"type": "message_stop"})
            return

        # 已输出过内容则收尾，避免半截响应重复拼接
        if got_content:
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield _sse("message_stop", {"type": "message_stop"})
            return

        # 登录错误：直接报错，不切换（网页需要人工登录）
        if "[ERROR:LOGIN_REQUIRED:" in error_msg:
            yield _sse("error", {"type": "error",
                                 "error": {"type": "invalid_request_error", "message": error_msg}})
            return

        next_site = await load_balancer.select_site_exclude(model_name, exclude=tried)
        if not next_site:
            yield _sse("error", {"type": "error",
                                 "error": {"type": "api_error", "message": error_msg}})
            return
        console.print(f"[cyan]🔄 [anthropic] 切换到 {next_site}[/cyan]")
        current_site = next_site


@app.post("/v1/messages")
async def anthropic_messages(request: Request,
                             authorization: Optional[str] = Header(None),
                             x_api_key: Optional[str] = Header(None)):
    """Anthropic Messages API 兼容端点，供 Claude Code 设置
    ANTHROPIC_BASE_URL=http://127.0.0.1:18792 后直连使用。模型只接受最新的前 3 个 GLM。"""
    _verify_anthropic_auth(authorization, x_api_key)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    return await _anthropic_messages_impl(body, bool(body.get("stream")))


@app.get("/admin/status")
async def admin_status(authorization: Optional[str] = Header(None)):
    verify_key(authorization)
    return {
        "rate_limits": rate_limiter.get_status(),
        "sites": [{"name": s.name, "url": s.url, "priority": s.priority} for s in config.get_enabled_sites()],
    }


@app.get("/sites/models")
async def sites_models():
    """直接返回 yaml 配置的静态模型列表，不启动浏览器"""
    return {
        site.name: list(site.models.keys())
        for site in config.get_enabled_sites()
    }


# 已知免费 AI 站点候选库
_FREE_SITE_CANDIDATES = [
    {"name": "huggingchat", "url": "https://huggingface.co/chat",  "desc": "HuggingFace Chat - 开源模型免费"},
    {"name": "groq",        "url": "https://groq.com",             "desc": "Groq - 超快推理，免费额度"},
    {"name": "mistral",     "url": "https://chat.mistral.ai",      "desc": "Mistral Le Chat - 免费"},
    {"name": "deepseek",    "url": "https://chat.deepseek.com",    "desc": "DeepSeek Chat - 免费"},
    {"name": "qwen",        "url": "https://chat.qwen.ai",         "desc": "通义千问 - 免费"},
    {"name": "kimi",        "url": "https://kimi.moonshot.cn",     "desc": "Kimi - 免费"},
    {"name": "doubao",      "url": "https://www.doubao.com/chat",  "desc": "豆包 - 免费"},
    {"name": "yuanbao",     "url": "https://yuanbao.tencent.com",  "desc": "元宝 - 免费"},
    {"name": "tiangong",    "url": "https://www.tiangong.cn",      "desc": "天工 AI - 免费"},
    {"name": "cohere",      "url": "https://coral.cohere.com",     "desc": "Cohere Coral - 免费"},
    {"name": "phind",       "url": "https://www.phind.com",        "desc": "Phind - 开发者专用，免费"},
    {"name": "venice",      "url": "https://venice.ai",            "desc": "Venice AI - 隐私优先，免费"},
]


@app.get("/sites/discover")
async def sites_discover():
    """后台 headless 探测候选免费站点，全部完成后返回结果"""
    existing_urls = {s.url.rstrip("/") for s in config.get_enabled_sites()}
    existing_names = {s.name for s in config.get_enabled_sites()}
    candidates = [
        c for c in _FREE_SITE_CANDIDATES
        if c["url"].rstrip("/") not in existing_urls and c["name"] not in existing_names
    ]

    async def _probe(candidate):
        result = await browser_manager.probe_headless(candidate["url"])
        if result.get("accessible"):
            return {**candidate, **result}
        return None

    results = await asyncio.gather(*[_probe(c) for c in candidates], return_exceptions=True)
    found = [r for r in results if isinstance(r, dict) and r]
    # 无需登录的排前面
    found.sort(key=lambda x: (x.get("needs_login", True), x["name"]))
    return {"found": found, "total_checked": len(candidates)}


@app.get("/sites/check")
async def sites_check():
    """用轻量 HTTP 请求检测站点可用性，不启动浏览器"""
    import urllib.request as _req
    sites = config.get_enabled_sites()

    def _http_check(url: str) -> bool:
        try:
            r = _req.urlopen(url, timeout=5)
            return r.status < 500
        except Exception:
            return False

    loop = asyncio.get_event_loop()
    results = []
    for site in sites:
        alive = await loop.run_in_executor(None, _http_check, site.url)
        results.append({"name": site.name, "url": site.url, "alive": alive,
                        "models": list(site.models.keys())})
    return {"results": results}


class SiteAddRequest(BaseModel):
    name: str
    url: str
    desc: str = ""
    models: list = []


@app.post("/sites/add")
async def sites_add(req: SiteAddRequest):
    """将用户确认的站点追加到 sites.yaml 并热重载"""
    from pathlib import Path
    yaml_path = Path(__file__).parent / "sites.yaml"

    if config.get_site(req.name):
        return {"success": False, "message": f"站点 {req.name} 已存在"}

    existing = config.get_enabled_sites()
    next_priority = max((s.priority for s in existing), default=0) + 1

    # 构建模型列表：优先用探测到的真实模型，否则用默认
    if req.models:
        models_yaml = "\n".join(
            f'      {m.lower().replace(" ", "-").replace("/", "-")}: "{m}"'
            for m in req.models[:10]
        )
    else:
        models_yaml = f'      {req.name}-default: "default"'

    new_block = f"""
  - name: {req.name}
    enabled: true
    url: "{req.url}"
    priority: {next_priority}

    selectors:
      input: 'textarea[placeholder*="message" i], textarea[placeholder*="ask" i], [contenteditable="true"], textarea'
      send_button: 'button[aria-label*="send" i], button[type="submit"]'
      response: '[class*="message" i][class*="assistant" i], [class*="prose"], [class*="markdown"]'
      stop_button: 'button[aria-label*="stop" i]'

    models:
{models_yaml}
"""
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        insert_pos = content.find("\ncaptcha_indicators:")
        content = content[:insert_pos] + new_block + content[insert_pos:] if insert_pos != -1 else content + new_block
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(content)
        config._load_yaml()
        new_site = config.get_site(req.name)
        new_models = list(new_site.models.keys()) if new_site else []
        return {"success": True, "message": f"已添加站点 {req.name}", "new_models": new_models}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/sites/remove/{site_name}")
async def sites_remove(site_name: str):
    """从 sites.yaml 移除失效站点并热重载"""
    from pathlib import Path
    import re
    yaml_path = Path(__file__).parent / "sites.yaml"

    site = config.get_site(site_name)
    if not site:
        return {"success": False, "message": f"站点 {site_name} 不存在"}

    removed_models = list(site.models.keys())

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 删除该站点的 yaml 块（从 "  - name: xxx" 到下一个 "  - name:" 或文件末尾）
        pattern = rf'\n  - name: {re.escape(site_name)}\n.*?(?=\n  - name:|\ncaptcha_indicators:|\Z)'
        content = re.sub(pattern, '', content, flags=re.DOTALL)
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(content)
        # 清理浏览器 profile
        await browser_manager.clear_site(site_name)
        config._load_yaml()
        # 计算移除后其他站点还在用的模型，排除掉
        remaining_models = set(load_balancer.get_available_models())
        truly_removed = [m for m in removed_models if m not in remaining_models]
        return {"success": True, "removed_models": truly_removed, "message": f"已移除站点 {site_name}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/admin/validate")
async def admin_validate(site: Optional[str] = None, authorization: Optional[str] = Header(None)):
    verify_key(authorization)
    if site:
        success, model, error = await validator.validate_site(site)
        return {"site": site, "success": success, "model": model, "error": error}
    else:
        return {"results": await validator.validate_all()}


@app.post("/admin/clear-profile/{site}")
async def admin_clear_profile(site: str, authorization: Optional[str] = Header(None)):
    verify_key(authorization)
    await browser_manager.clear_site(site)
    return {"cleared": site}


# ─── IDE 接入 ─────────────────────────────────────────────────

@app.get("/ide/detect")
async def ide_detect():
    import psutil
    import concurrent.futures

    ide_patterns = [
        ("kiro",     ["kiro"],                            {"name": "Kiro",     "icon": "🪐"}),
        ("zed",      ["zed"],                             {"name": "Zed",      "icon": "⚡"}),
        ("code",     ["code", "vscode", "code - insiders"], {"name": "VSCode", "icon": "💙"}),
        ("claude",   ["claude"],                          {"name": "Claude",   "icon": "🤖"}),
        ("cursor",   ["cursor"],                          {"name": "Cursor",   "icon": "🖱️"}),
        ("windsurf", ["windsurf"],                        {"name": "Windsurf", "icon": "🏄"}),
    ]

    def _scan():
        detected = []
        detected_keys = set()
        try:
            # 只读 name，避免读 exe 路径导致权限慢查询
            for proc in psutil.process_iter(["name"]):
                try:
                    pname = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                    for key, keywords, meta in ide_patterns:
                        if key in detected_keys:
                            continue
                        if any(pname == kw or pname.startswith(kw) for kw in keywords):
                            detected.append({"key": key, **meta})
                            detected_keys.add(key)
                except Exception:
                    continue
        except Exception:
            pass
        return detected

    loop = asyncio.get_event_loop()
    try:
        detected = await asyncio.wait_for(
            loop.run_in_executor(None, _scan),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        detected = []

    return {
        "detected": detected,
        "base_url": f"http://127.0.0.1:{config.PORT}",
        "api_key": config.API_KEY or "any",
        "models": load_balancer.get_available_models(),
    }


def _open_zed_agent_panel():
    """启动 Zed 后自动打开 agent 面板（Windows）。

    策略：等 Zed 窗口就绪 → 按进程 MainWindowHandle 激活前台 →
    Ctrl+Shift+A 打开 agent 面板。
    """
    import time, subprocess, os

    ps_script = r'''
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
}
"@
$wsh = New-Object -ComObject WScript.Shell
$hwnd = [IntPtr]::Zero

# 等待 Zed 窗口出现，最多 30 秒
for ($i = 0; $i -lt 100; $i++) {
    $proc = Get-Process -Name Zed -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if ($proc -and $proc.MainWindowHandle -ne 0) {
        $hwnd = $proc.MainWindowHandle
        break
    }
    Start-Sleep -Milliseconds 300
}

if ($hwnd -eq [IntPtr]::Zero) {
    Write-Error "Zed window not found"
    exit 1
}

# 恢复窗口并激活
[Win32]::ShowWindow($hwnd, 9)
if ([Win32]::IsIconic($hwnd)) { [Win32]::ShowWindow($hwnd, 9) }
Start-Sleep -Milliseconds 500
[Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 800

# 发送 Ctrl+Shift+A 打开 Agent 面板
$wsh.SendKeys("^%a")
Start-Sleep -Milliseconds 1000
'''
    for attempt in range(3):
        try:
            time.sleep(3)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                console.print("[green]✅ Zed agent 面板已打开[/green]")
                return
            else:
                err = r.stderr.strip() if r.stderr else ""
                console.print(f"[yellow]⚠️ 激活 Zed 失败 (rc={r.returncode}){f': {err}' if err else ''}，重试 {attempt+1}/3[/yellow]")
        except Exception as e:
            console.print(f"[yellow]⚠️ 打开 Zed agent 面板重试 {attempt+1}: {e}[/yellow]")
        time.sleep(2)
    console.print("[yellow]⚠️ Zed agent 面板自动打开失败，请手动 Ctrl+Shift+A 打开 Agent 面板[/yellow]")


async def _restart_ide(ide_key: str, env_vars: dict = None, project_path: str = None) -> bool:
    """通过查找进程 exe 路径来重启 IDE，找不到时用已知安装路径"""
    import asyncio

    keywords = {
        "kiro": ["kiro"], "zed": ["zed"], "code": ["code"],
        "claude": ["claude"], "cursor": ["cursor"], "windsurf": ["windsurf"],
    }.get(ide_key)

    if not keywords:
        return False

    # 各平台的已知安装路径备用列表
    import platform, os
    _home = os.path.expanduser("~")
    _sys = platform.system()
    _fallback_paths = {
        "kiro": [
            r"D:\Kiro\Kiro.exe",
            os.path.join(_home, "AppData", "Local", "Programs", "Kiro", "Kiro.exe"),
        ] if _sys == "Windows" else ["/usr/bin/kiro", "/Applications/Kiro.app/Contents/MacOS/Kiro"],
        "zed": [
            os.path.join(_home, "AppData", "Local", "Programs", "Zed", "bin", "Zed.exe"),
            os.path.join(_home, "AppData", "Local", "Programs", "Zed", "Zed.exe"),
            r"C:\Program Files\Zed\Zed.exe",
            r"D:\Zed\Zed.exe",
            r"D:\Zed\bin\Zed.exe",
            r"E:\Zed\Zed.exe",
        ] if _sys == "Windows" else ["/usr/bin/zed", "/Applications/Zed.app/Contents/MacOS/Zed"],
        "code": [
            os.path.join(_home, "AppData", "Local", "Programs", "Microsoft VS Code", "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ] if _sys == "Windows" else ["/usr/bin/code", "/Applications/Visual Studio Code.app/Contents/MacOS/Electron"],
        "cursor": [
            os.path.join(_home, "AppData", "Local", "Programs", "cursor", "Cursor.exe"),
        ] if _sys == "Windows" else ["/usr/bin/cursor", "/Applications/Cursor.app/Contents/MacOS/Cursor"],
        "windsurf": [
            os.path.join(_home, "AppData", "Local", "Programs", "windsurf", "Windsurf.exe"),
        ] if _sys == "Windows" else ["/usr/bin/windsurf"],
        "claude": [
            os.path.join(_home, "AppData", "Local", "Programs", "claude", "Claude.exe"),
            os.path.join(_home, "AppData", "Roaming", "npm", "claude.cmd"),
        ] if _sys == "Windows" else ["/usr/bin/claude"],
    }

    loop = asyncio.get_event_loop()

    def _do_restart():
        import psutil, time, subprocess, sys, os

        # 1. 先从运行中的进程找 exe 路径
        exe_path = None
        procs_to_kill = []
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                if any(pname == kw or pname.startswith(kw) for kw in keywords):
                    procs_to_kill.append(proc)
                    if not exe_path and proc.info.get("exe"):
                        exe_path = proc.info["exe"]
            except Exception:
                continue

        # 2. 进程不在运行时，从备用路径找
        if not exe_path:
            for path in _fallback_paths.get(ide_key, []):
                if os.path.exists(path):
                    exe_path = path
                    break

        if not exe_path:
            console.print(f"[yellow]未找到 {ide_key} 可执行文件[/yellow]")
            return False

        # 终止所有匹配进程
        for proc in procs_to_kill:
            try:
                proc.terminate()
            except Exception:
                pass
        if procs_to_kill:
            time.sleep(2)

        # 用 exe 路径重启
        try:
            env = os.environ.copy()
            # 从用户环境注册表读取最新变量（含刚写入的 env_vars）
            if sys.platform == "win32":
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as rk:
                        i = 0
                        while True:
                            try:
                                name, value, _ = winreg.EnumValue(rk, i)
                                env[name] = value
                                i += 1
                            except OSError:
                                break
                except Exception:
                    pass
            if env_vars:
                env.update(env_vars)
            # zed 启动时打开项目目录（若提供）使 agent 面板有上下文
            launch_args = [exe_path]
            if ide_key == "zed" and project_path:
                launch_args.append(project_path)
            if sys.platform == "win32":
                subprocess.Popen(
                    launch_args,
                    env=env,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                subprocess.Popen(launch_args, env=env, start_new_session=True)
            console.print(f"[green]✅ 已启动 {ide_key}: {exe_path}[/green]")

            # zed 启动后异步打开 agent 面板（模拟命令面板触发 agent: new thread）
            if ide_key == "zed" and sys.platform == "win32":
                threading.Thread(
                    target=_open_zed_agent_panel, daemon=True
                ).start()
            return True
        except Exception as e:
            console.print(f"[red]启动 {ide_key} 失败: {e}[/red]")
            return False

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _do_restart), timeout=15)
    except Exception:
        return False


@app.post("/ide/configure/{ide_key}")
async def ide_configure(ide_key: str, project: Optional[str] = None):
    """合并写入 IDE 配置，不影响原有模型和配置。

    project: 可选项目路径，zed 启动时打开该项目。
    """
    import os, platform
    # 必须用 127.0.0.1：localhost 会被 Zed 等 Rust HTTP 栈解析为 IPv6 ::1，
    # 而桥接只监听 IPv4，导致连接被拒、客户端无限重试
    base_url = f"http://127.0.0.1:{config.PORT}"
    api_key  = config.API_KEY or "any"
    home     = os.path.expanduser("~")
    sys_     = platform.system()

    def _win(p): return p if sys_ == "Windows" else None
    def _mac(p): return p if sys_ == "Darwin" else None
    def _lin(p): return p if sys_ not in ("Windows", "Darwin") else None

    # ── 通用 MCP block ──────────────────────────────────────────
    mcp_block = {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-openai-compatible",
                 "--base-url", f"{base_url}/v1", "--api-key", api_key],
        "disabled": False,
        "autoApprove": ["chat"],
    }

    # ── Claude Code (~/.claude.json) openai provider block ──────
    # 每个桥接站点暴露为一个模型（ID=站点名）
    claude_code_block = {
        "type": "openai",
        "baseURL": f"{base_url}/v1",
        "apiKey": api_key,
        "name": "Bridge",
        "models": get_site_model_ids(),
    }

    # ── Zed: language_models.openai_compatible 用命名键（provider id = "bridge"）
    # 环境变量 BRIDGE_API_KEY 自动对应（Zed 从 provider id 生成）
    # 分组与管理页页签一致：可用站点(bridge) / 待登录(pending-login)，
    # 第三方 provider（openrouter/free-gateway 指向 8787）原样保留不覆盖
    site_models = get_site_model_ids()
    # 未登录(is_logged_in=False)的站点不进 Zed 列表（venice 激活后自动回来）
    from browser import profile_manager as _pm
    ready_models = [m for m in site_models
                    if m not in LOGIN_REQUIRED_SITES
                    and _pm.get_login_status(m) is not False]
    pending_models = [m for m in site_models if m in LOGIN_REQUIRED_SITES]

    # 先拉网关目录（bridge 组要追加 or/* 具体模型；openrouter 组也要用）
    # 注意：网关首次被调用时要现拉 OpenRouter 目录（约10-20s），超时必须给足
    _gids = []
    try:
        import httpx as _hx
        # httpx.get 是同步 API（AsyncClient 才可 await）
        _gr = _hx.get("http://127.0.0.1:8787/v1/models", timeout=30)
        _gids = [m["id"] for m in _gr.json().get("data", [])]
    except Exception as e:
        console.print(f"[yellow]⚠️ 网关模型列表拉取失败(保留现有配置): {e}[/yellow]")

    def _short_or(mid):
        part = mid[3:] if mid.startswith("or/") else mid
        part = part.split(":")[0].split("/")[-1]
        toks = part.split("-")
        return "-".join(toks[:2])[:18]

    # bridge 组模型：可用站点 + or/* 具体模型（简写名，所选即所答）
    bridge_model_entries = [{"name": m, "display_name": m, "max_tokens": 8192,
                             "supports_tools": False, "supports_images": False}
                            for m in ready_models]
    seen = {}
    for i in _gids:
        if not i.startswith("or/"):
            continue
        s = _short_or(i)
        seen[s] = seen.get(s, 0) + 1
        if seen[s] > 1:
            s = f"{s}·{seen[s]}"
        bridge_model_entries.append({"name": i, "display_name": s, "max_tokens": 16384,
                                     "supports_tools": False, "supports_images": False})

    # 默认模型 = openrouter（已验证可用的 API 代理链，快速稳定）
    zed_default_model = "openrouter" if "openrouter" in ready_models else \
        (ready_models[0] if ready_models else default_site_model())
    # Zed 格式：provider 嵌套在 openai_compatible（下划线）下，键名即 provider id
    # 注意：连字符 "openai-compatible" 会被 Zed 静默忽略，模型列表会显示为空
    def _zed_models(names):
        return [
            {"name": m, "display_name": m, "max_tokens": 8192,
             "supports_tools": False, "supports_images": False}
            for m in names
        ]
    zed_lm_block = {
        "openai_compatible": {
            "bridge": {
                "api_url": f"{base_url}/v1",
                "api_key": api_key,
                "available_models": bridge_model_entries,
            },
            "pending-login": {
                "api_url": f"{base_url}/v1",
                "api_key": api_key,
                "available_models": _zed_models(pending_models),
            },
        }
    }
    # openrouter 组（free-gateway 8787，or/* 免费模型动态列表）
    # 拉取失败时保留 existing 里已有的 openrouter 组，绝不用兜底短列表覆盖
    if _gids:
        zed_lm_block["openai_compatible"]["openrouter"] = {
            "api_url": "http://127.0.0.1:8787/v1",
            "api_key": "any",
            "available_models": _zed_models(_gids),
        }
    zed_block = {
        "version": "2",
        "default_model": {"provider": "bridge", "model": zed_default_model},
    }

    ide_configs = {
        # Kiro: ~/.kiro/settings/mcp.json  +  额外提示 claude code 配置
        "kiro": {
            "paths": [os.path.join(home, ".kiro", "settings", "mcp.json")],
            "type": "mcp",
            "extra": "claude_code",
        },
        # Zed: settings.json agent/assistant 块 + language_models.openai_compatible + 环境变量
        "zed": {
            "paths": [p for p in [
                _win(os.path.join(home, "AppData", "Roaming", "Zed", "settings.json")),
                _mac(os.path.join(home, "Library", "Application Support", "Zed", "settings.json")),
                _lin(os.path.join(home, ".config", "zed", "settings.json")),
            ] if p],
            "type": "zed",
            "env_vars": {"BRIDGE_API_KEY": api_key},
        },
        # VSCode: Continue 插件 customModels  +  额外提示 claude code 配置
        "code": {
            "paths": [p for p in [
                _win(os.path.join(home, "AppData", "Roaming", "Code", "User", "settings.json")),
                _mac(os.path.join(home, "Library", "Application Support", "Code", "User", "settings.json")),
                _lin(os.path.join(home, ".config", "Code", "User", "settings.json")),
            ] if p],
            "type": "vscode",
            "extra": "claude_code",
        },
        # Claude Desktop: ~/.claude/mcp.json
        "claude": {
            "paths": [os.path.join(home, ".claude", "mcp.json")],
            "type": "mcp",
        },
        # Cursor: mcp.json
        "cursor": {
            "paths": [p for p in [
                _win(os.path.join(home, "AppData", "Roaming", "Cursor", "User", "globalStorage", "cursor.mcp", "mcp.json")),
                _mac(os.path.join(home, "Library", "Application Support", "Cursor", "User", "globalStorage", "cursor.mcp", "mcp.json")),
                _lin(os.path.join(home, ".config", "Cursor", "User", "globalStorage", "cursor.mcp", "mcp.json")),
                os.path.join(home, ".cursor", "mcp.json"),
            ] if p],
            "type": "mcp",
        },
        # Windsurf: mcp_config.json
        "windsurf": {
            "paths": [os.path.join(home, ".codeium", "windsurf", "mcp_config.json")],
            "type": "mcp",
        },
    }

    if ide_key not in ide_configs:
        raise HTTPException(400, f"不支持的 IDE: {ide_key}")

    cfg = ide_configs[ide_key]
    written, errors = [], []

    # ── 写主配置文件 ─────────────────────────────────────────────
    continue_block = {
        "title": "Bridge", "provider": "openai", "model": default_site_model(),
        "apiBase": f"{base_url}/v1", "apiKey": api_key,
    }

    for path in cfg["paths"]:
        if not path:
            continue
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            existing = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    try:
                        existing = json.load(f)
                    except Exception:
                        existing = {}

            if cfg["type"] == "mcp":
                existing.setdefault("mcpServers", {})["ai-bridge"] = mcp_block
            elif cfg["type"] == "vscode":
                models_list = [m for m in existing.get("continue.customModels", []) if m.get("title") != "Bridge"]
                models_list.append(continue_block)
                existing["continue.customModels"] = models_list
            elif cfg["type"] == "zed":
                # 移除可能存在的 disable_ai 设置
                existing.pop("disable_ai", None)
                # 新版 Zed 用 "agent" 键；"assistant" 已废弃，清掉避免警告
                existing["agent"] = {"default_model": zed_block["default_model"]}
                existing.pop("assistant", None)
                # 直接更新 language_models，zed_lm_block 已经包含 openai_compatible 嵌套
                _lm = existing.setdefault("language_models", {})
                # openrouter 组：拉取成功才写入；失败时保留 existing 已有的完整列表
                _prev_or = _lm.get("openai_compatible", {}).get("openrouter")
                _lm.update(zed_lm_block)
                if _gids:
                    _lm["openai_compatible"]["openrouter"] = zed_lm_block["openai_compatible"]["openrouter"]
                elif _prev_or and _prev_or.get("available_models"):
                    _lm["openai_compatible"]["openrouter"] = _prev_or   # 还原，不用兜底短列表覆盖
                else:
                    _lm["openai_compatible"].pop("openrouter", None)
                _lm.pop("openai-compatible", None)   # 清理连字符旧键（Zed 不识别）
                # 确保启动恢复最近项目和默认布局
                existing["restore_on_startup"] = "last_session"

            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            written.append(path)
            break
        except Exception as e:
            errors.append(f"{path}: {e}")

    # ── Claude Code 桥接说明（不写全局文件）─────────────────────
    # ~/.claude.json 与 ~/.claude/settings.json 是用户级全局文件，
    # 会被 Kiro 等其他 IDE 的 Claude 共用；桥接只针对 VSCode 时，
    # 通过 ide_config.py 启动 VSCode 时注入进程环境变量实现，
    # 这里仅提示，不再写入任何全局配置。
    extra_configs = []
    if cfg.get("extra") == "claude_code":
        vscode_env = {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_MODEL": default_site_model(),
        }
        extra_configs.append({
            "label": "Claude Code 桥接（仅 VSCode 实例，不影响 Kiro 等其他 IDE）",
            "content": "运行 ai-bridge/ide_config.bat 或 启动VSCode桥接.bat，"
                       "会以以下环境变量启动 VSCode：\n" +
                       json.dumps(vscode_env, indent=2),
        })

    # ── 构建 manual fallback ─────────────────────────────────────
    manual_map = {
        "mcp":    json.dumps({"mcpServers": {"ai-bridge": mcp_block}}, indent=2),
        "vscode": json.dumps({"continue.customModels": [continue_block]}, indent=2),
        "zed":    json.dumps({"restore_on_startup": "last_session", "agent": {"default_model": zed_block["default_model"]}, "assistant": zed_block, "language_models": zed_lm_block}, indent=2, ensure_ascii=False),
    }
    manual = manual_map[cfg["type"]]

    if written:
        # 写入 IDE 所需的环境变量（如 Zed 需要 BRIDGE_API_KEY）
        env_vars = cfg.get("env_vars", {})
        if env_vars:
            import winreg, ctypes
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
                for k, v in env_vars.items():
                    winreg.SetValueEx(key, k, 0, winreg.REG_SZ, v)
                winreg.CloseKey(key)
                # 广播环境变量变更消息
                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, None)
            except Exception:
                pass  # 非 Windows 或权限不足时忽略

        # 自动重启对应 IDE
        restarted = await _restart_ide(ide_key, env_vars=env_vars, project_path=project)
        msg = "已合并写入，配置已生效" if restarted else "已合并写入，请手动重启 IDE 后生效"
        return {
            "success": True, "ide": ide_key, "written": written,
            "base_url": base_url, "extra_configs": extra_configs,
            "restarted": restarted, "message": msg,
        }
    else:
        return {
            "success": False, "ide": ide_key, "errors": errors,
            "manual_config": manual, "base_url": base_url,
            "message": "自动写入失败，请手动复制以下配置",
        }


if __name__ == "__main__":
    import socket
    # 检测端口是否已被占用，避免多进程冲突
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", config.PORT)) == 0:
            console.print(f"[red]❌ 端口 {config.PORT} 已被占用，请先关闭旧进程[/red]")
            import sys; sys.exit(1)
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False, log_level="warning")
