"""
安全回归测试：覆盖本次自测发现的真实漏洞。

每个用例都对应一个曾经可实际触发的问题，
修复后必须通过 —— 不允许为了让测试通过而弱化断言。
"""
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

XSS = '"><script>alert(document.domain)</script>'


@pytest.fixture
def client_and_cfg():
    from fastapi.testclient import TestClient
    from config import (RateLimitConfig, BehaviorConfig, CooldownConfig,
                        SecurityConfig, SiteConfig, CaptchaIndicator)

    cfg = MagicMock()
    cfg.global_rate_limit = RateLimitConfig(
        requests_per_minute=10, requests_per_hour=100, requests_per_day=500,
        min_interval_seconds=1, max_interval_seconds=5)
    cfg.behavior = BehaviorConfig(
        typing_speed_min_ms=10, typing_speed_max_ms=30, typing_mistake_rate=0.0,
        scroll_probability=0.0, pause_probability=0.0, pause_duration_min=0.0,
        pause_duration_max=0.0, mouse_speed_min=1, mouse_speed_max=5)
    cfg.cooldown = CooldownConfig(initial_seconds=10, max_seconds=60,
                                  multiplier=2.0, reset_after_success=3)
    cfg.security = SecurityConfig(auto_pause=False, notification_sound=False,
                                  desktop_notification=False, max_wait_minutes=0,
                                  screenshot_on_captcha=False)
    cfg.captcha_indicators = [CaptchaIndicator(selector="#x", name="cf")]
    site = SiteConfig(name="poe", url="https://poe.com", enabled=True, priority=1,
                      selectors={"input": "textarea"}, models={"gpt-4o": "GPT-4o"})
    cfg.sites = [site]
    cfg.get_site = lambda n: next((s for s in cfg.sites if s.name == n), None)
    cfg.get_enabled_sites = lambda: [s for s in cfg.sites if s.enabled]
    cfg.API_KEY = ""

    import main as m
    m.config = cfg
    m.rate_limiter = MagicMock()
    m.rate_limiter.cooldown_remaining = MagicMock(return_value=0)
    m.security_manager = MagicMock()
    m.load_balancer = MagicMock()
    m.validator = MagicMock()
    m._providers = {}

    client = TestClient(m.app, raise_server_exceptions=False)
    yield client, cfg, m
    m.config = MagicMock()


# ── 1. 存储型 XSS：站点字段拼进 HTML 未转义 ──────────────────

def test_admin_page_escapes_site_url(client_and_cfg):
    """site.url 含 script 时，管理页必须转义，不能出现可执行脚本"""
    client, cfg, _ = client_and_cfg
    cfg.sites[0].url = f"https://evil.example/{XSS}"
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<script>alert(document.domain)</script>" not in resp.text
    assert "&quot;" in resp.text or "&#x27;" in resp.text


def test_admin_page_escapes_site_name(client_and_cfg):
    """site.name 含 script 时必须转义"""
    client, cfg, _ = client_and_cfg
    cfg.sites[0].name = f"poe{XSS}"
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<script>alert(document.domain)</script>" not in resp.text


def test_admin_page_escapes_model_name(client_and_cfg):
    """模型名含 script 时必须转义"""
    client, cfg, _ = client_and_cfg
    cfg.sites[0].models = {f"m{XSS}": "x"}
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<script>alert(document.domain)</script>" not in resp.text


def test_site_name_cannot_break_out_of_js_string(client_and_cfg):
    """site.name 含单引号时不能闭合 JS 字符串字面量"""
    client, cfg, _ = client_and_cfg
    cfg.sites[0].name = "poe');alert(1);//"
    resp = client.get("/")
    assert resp.status_code == 200
    assert "poe');alert(1);//" not in resp.text


# ── 2. YAML 注入：/sites/add 未校验就写进 sites.yaml ──────────

@pytest.fixture
def tmp_sites_yaml(client_and_cfg):
    """把 sites.yaml 的读写指向临时副本，绝不碰真实文件。

    注意：sites_add() 内部是 `from pathlib import Path` 的局部导入，
    并且写法是 `Path(__file__).parent / "sites.yaml"` ——
    所以必须让「Path(...).parent」返回临时目录，代理单个 Path 调用是不够的。
    这里改用「把 __file__ 指向临时目录」的方案：
    临时目录里放一份 main.py 的副本，其同级的 sites.yaml 就是目标文件。
    """
    client, cfg, m = client_and_cfg
    import shutil as _sh

    tmpdir = Path(tempfile.mkdtemp(prefix="sec_test_"))
    _sh.copy(ROOT / "sites.yaml", tmpdir / "sites.yaml")
    # 复制 main.py 到临时目录 → 其 Path(__file__).parent 就是 tmpdir
    _sh.copy(ROOT / "main.py", tmpdir / "main.py")

    real_main_file = m.__file__
    # 让 main.py 里的 __file__ 指向临时副本
    with patch.object(m, "__file__", str(tmpdir / "main.py")):
        yield tmpdir / "sites.yaml"
    _sh.rmtree(tmpdir, ignore_errors=True)


def test_sites_add_rejects_yaml_injection_in_name(client_and_cfg, tmp_sites_yaml):
    """name 含换行 + 引号必须被拒绝，不能破坏 sites.yaml 结构"""
    client, cfg, m = client_and_cfg
    import yaml as _y

    resp = client.post("/sites/add", json={
        "name": 'evil" \n    enabled: true\n  - name: injected',
        "url": "https://y.example",
        "models": ["m2"],
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is False

    txt = tmp_sites_yaml.read_text(encoding="utf-8")
    assert "injected" not in txt
    # 文件仍必须是合法 YAML
    _y.safe_load(txt)


def test_sites_add_rejects_xss_in_url(client_and_cfg, tmp_sites_yaml):
    """url 含尖括号/引号必须被拒绝"""
    client, cfg, m = client_and_cfg
    resp = client.post("/sites/add", json={
        "name": "pwned",
        "url": f"https://x.example/{XSS}",
        "models": ["m1"],
    })
    assert resp.json()["success"] is False
    assert "pwned" not in tmp_sites_yaml.read_text(encoding="utf-8")


def test_sites_add_rejects_non_http_scheme(client_and_cfg, tmp_sites_yaml):
    """file:// 等本地协议必须被拒绝"""
    client, cfg, m = client_and_cfg
    for bad in ("file:///C:/Windows/System32/cmd.exe",
                "javascript:alert(1)",
                "data:text/html,<script>alert(1)</script>"):
        resp = client.post("/sites/add", json={"name": "bad", "url": bad})
        assert resp.json()["success"] is False, f"未拒绝: {bad}"


def test_sites_add_accepts_valid_site(client_and_cfg, tmp_sites_yaml):
    """合法输入仍应成功（不能把功能改死）"""
    client, cfg, m = client_and_cfg
    with patch.object(m.config, "_load_yaml", return_value=None):
        resp = client.post("/sites/add", json={
            "name": "good-site",
            "url": "https://good.example",
            "models": ["Model A"],
        })
    assert resp.json()["success"] is True
    txt = tmp_sites_yaml.read_text(encoding="utf-8")
    assert "good-site" in txt
    import yaml as _y
    _y.safe_load(txt)   # 结构完好

def test_sites_add_model_name_with_quotes_keeps_yaml_valid(client_and_cfg,
                                                              tmp_sites_yaml):
    """模型名含引号时 YAML 仍须可解析"""
    client, cfg, m = client_and_cfg
    import yaml as _y
    with patch.object(m.config, "_load_yaml", return_value=None):
        resp = client.post("/sites/add", json={
            "name": "quoted-site",
            "url": "https://q.example",
            "models": ['say "hi"', "back\\slash"],
        })
    assert resp.json()["success"] is True
    _y.safe_load(tmp_sites_yaml.read_text(encoding="utf-8"))


# ── 3. 路由冲突：/login/open 被 /login/{site} 吞掉 ─────────────

def test_login_open_route_not_shadowed(client_and_cfg):
    """POST /login/open 必须命中自己的处理函数，而不是 /login/{site}"""
    client, cfg, _ = client_and_cfg
    with patch("webbrowser.open") as wb:
        resp = client.post("/login/open?url=https://example.com")
    assert resp.status_code == 200
    assert resp.json().get("success") is True
    assert wb.called


def test_login_open_rejects_file_scheme(client_and_cfg):
    """file:// 不得被打开"""
    client, cfg, _ = client_and_cfg
    with patch("webbrowser.open") as wb:
        resp = client.post("/login/open?url=file:///C:/Windows/System32/cmd.exe")
    assert resp.json().get("success") is False
    assert not wb.called


# ── 4. 认证绕过：API_KEY 校验 ────────────────────────────────

def test_api_key_required_when_configured(client_and_cfg):
    """配置了 API_KEY 时，缺 token 必须 401"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/models",
                      headers={"Authorization": "Bearer secret"}).status_code == 200


def test_chat_completions_requires_key(client_and_cfg):
    """对话接口同样受 API_KEY 保护"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.post("/v1/chat/completions", json={
        "model": "poe", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 401


# ── 5. 输入边界：请求体校验 ───────────────────────────────────

def test_chat_completions_rejects_missing_messages(client_and_cfg):
    """缺少 messages 必须 422，不能崩"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = ""
    resp = client.post("/v1/chat/completions", json={"model": "poe"})
    assert resp.status_code == 422


def test_chat_completions_rejects_empty_body(client_and_cfg):
    """空 body 必须 422"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = ""
    resp = client.post("/v1/chat/completions", json={})
    assert resp.status_code == 422


def test_chat_completions_handles_null_message_content(client_and_cfg):
    """content 为 null 不能 500"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = ""
    cfg.load_balancer.select_site = AsyncMock(return_value="poe")
    provider = MagicMock()

    # 非流式路径：provider.chat 是 async generator（返回分片）
    async def fake_chat(*a, **k):
        if False:
            yield None

    provider.chat = fake_chat
    with patch("main.get_provider", return_value=provider):
        resp = client.post("/v1/chat/completions", json={
            "model": "poe",
            "messages": [{"role": "user", "content": None}],
            "stream": False,
        })
    assert resp.status_code in (200, 422)
    assert resp.status_code != 500


# ── 6. 并发：/login/batch 重复调用 ───────────────────────────

def test_login_batch_rejects_concurrent_start(client_and_cfg):
    """批量登录重复启动必须被拒绝，不能并发开一堆浏览器"""
    client, cfg, m = client_and_cfg
    m._batch_login_state.clear()
    m._batch_login_state.update({
        "running": True, "total": 1, "done": 0, "current": "x",
        "results": [], "started_at": 0, "finished_at": None,
    })
    resp = client.post("/login/batch")
    assert resp.json()["success"] is False
    assert "运行中" in resp.json()["message"]


def test_login_batch_status_shape(client_and_cfg):
    """进度接口返回结构固定，前端才不会崩"""
    client, cfg, m = client_and_cfg
    resp = client.get("/login/batch/status")
    assert resp.status_code == 200
    d = resp.json()
    for k in ("running", "total", "done", "current", "results",
              "logged_in", "failed"):
        assert k in d


# ── 7. 暴露面护栏：对外监听必须有鉴权 ──────────────────────────

def test_public_bind_without_api_key_is_rejected():
    """HOST=0.0.0.0 且没有 API_KEY 时必须拒绝启动

    否则整个局域网都能代理用户的模型请求、写入 sites.yaml、
    打开本机浏览器。
    """
    from config import Config
    c = Config.__new__(Config)
    c.HOST = "0.0.0.0"
    c.API_KEY = ""
    assert c.is_public_bind is True

    c2 = Config.__new__(Config)
    c2.HOST = "127.0.0.1"
    c2.API_KEY = ""
    assert c2.is_public_bind is False

    c3 = Config.__new__(Config)
    c3.HOST = "0.0.0.0"
    c3.API_KEY = "secret"
    assert c3.is_public_bind is True


def test_lifespan_raises_on_public_bind_without_key(client_and_cfg):
    """lifespan 必须真的抛错，而不是只打个警告"""
    client, cfg, m = client_and_cfg
    import asyncio

    cfg.HOST = "0.0.0.0"
    cfg.API_KEY = ""

    async def go():
        async with m.lifespan(m.app):
            pass

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(go())
    assert "API_KEY" in str(exc.value)


def test_default_host_is_loopback():
    """默认必须监听本机，而不是 0.0.0.0"""
    import config as config_mod
    # 直接看环境默认值（不依赖 .env）
    assert config_mod.Config.HOST in ("127.0.0.1", "localhost", "::1"), \
        f"默认 HOST 不安全: {config_mod.Config.HOST}"


# ── 8. 上下文护栏：网关路径不能转发无限长上下文 ───────────────

def test_gateway_path_truncates_oversized_context(client_and_cfg):
    """or/* local-* 路径必须裁剪超长上下文，否则本地模型会长时间无响应"""
    client, cfg, m = client_and_cfg
    cfg.API_KEY = ""
    captured = {}

    async def fake_proxy(openai_msgs, stream, requested_model="auto", tools=None):
        captured["msgs"] = openai_msgs
        captured["model"] = requested_model
        return {"ok": True}

    with patch.object(m, "_gateway_proxy", side_effect=fake_proxy):
        client.post("/v1/chat/completions", json={
            "model": "local-dolphin-mistral",
            "messages": [{"role": "user", "content": "x" * 200000}],
            "stream": False,
        })

    assert captured, "没有走 _gateway_proxy"
    total = sum(len(d.get("content") or "") for d in captured["msgs"])
    assert total <= 60000, f"上下文未裁剪: {total} 字符"
    assert total > 0, "上下文被清空了"


def test_gateway_path_keeps_recent_messages_when_truncating(client_and_cfg):
    """裁剪时必须保留最近的消息（最新的用户输入不能丢）"""
    client, cfg, m = client_and_cfg
    cfg.API_KEY = ""
    captured = {}

    async def fake_proxy(openai_msgs, stream, requested_model="auto", tools=None):
        captured["msgs"] = openai_msgs
        return {"ok": True}

    msgs = [{"role": "user", "content": f"old-{i} " + "y" * 15000}
            for i in range(8)]
    msgs.append({"role": "user", "content": "LATEST_MARKER"})

    with patch.object(m, "_gateway_proxy", side_effect=fake_proxy):
        client.post("/v1/chat/completions", json={
            "model": "local-dolphin-mistral",
            "messages": msgs,
            "stream": False,
        })

    assert captured
    contents = [d.get("content") or "" for d in captured["msgs"]]
    assert any("LATEST_MARKER" in c for c in contents), "最近的消息被丢掉了"


# ── 9. 管理接口鉴权：配置 API_KEY 后，管理面必须同样受保护 ────────
#
# 背景：原先只有 /v1/* 走 verify_key()，而 /sites/add（写 sites.yaml）、
# /ide/configure（写 IDE 配置）、/autologin（收邮箱密码）、
# /login/batch（拉起本机浏览器）全部裸奔。
# 一旦用户为了局域网访问而配置了 API_KEY 并把 HOST 改成 0.0.0.0，
# 整个局域网就能改配置、拿账号密码、往用户机器上弹浏览器窗口。

_ADMIN_ENDPOINTS = [
    ("GET",  "/login/status"),
    ("GET",  "/usage/recent"),
    ("GET",  "/sites/models"),
    ("GET",  "/sites/check"),
    ("GET",  "/sites/discover"),
    ("GET",  "/login/batch/status"),
    ("GET",  "/admin/status"),
    ("GET",  "/ide/detect"),
    ("POST", "/sites/add"),
    ("POST", "/sites/remove/poe"),
    ("POST", "/login/batch"),
    ("POST", "/login/open"),
    ("POST", "/login/poe"),
    ("POST", "/autologin/poe"),
    ("POST", "/autologin/poe/code"),
    ("POST", "/ide/configure/zed"),
    ("POST", "/ide/refresh-continue"),
    ("POST", "/zed/refresh-models"),
    ("POST", "/admin/validate"),
    ("POST", "/admin/clear-profile/poe"),
]


@pytest.mark.parametrize("method,path", _ADMIN_ENDPOINTS)
def test_admin_endpoints_require_key_when_configured(client_and_cfg, method, path):
    """配置了 API_KEY 后，所有管理接口缺 Key 必须 401"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.request(method, path, json={})
    assert resp.status_code == 401, f"{method} {path} 未受鉴权保护"


# 用来验证「带了 Key 就放行」的路由子集。
# 只挑副作用可控、能在测试里跑完的接口；
# 那些会拉起浏览器 / 长时间探测 / 改用户配置的接口，
# 其鉴权由上面的 401 用例 + 下面的依赖级用例覆盖。
_ADMIN_ENDPOINTS_FAST = [
    ("GET",  "/login/status"),
    ("GET",  "/usage/recent"),
    ("GET",  "/sites/models"),
    ("GET",  "/login/batch/status"),
    ("GET",  "/admin/status"),
    ("POST", "/sites/add"),
    ("POST", "/login/open"),
    ("POST", "/autologin/poe/code"),
    ("POST", "/admin/validate"),
    ("POST", "/admin/clear-profile/poe"),
]


@pytest.mark.parametrize("method,path", _ADMIN_ENDPOINTS_FAST)
def test_admin_endpoints_accept_x_api_key_header(client_and_cfg, method, path,
                                                 tmp_sites_yaml):
    """带 X-API-Key 时应放行（管理页 fetch 用这个头）"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    with patch("main.browser_manager.clear_site", AsyncMock()), \
         patch("main.browser_manager.get_page", AsyncMock()):
        resp = client.request(method, path, json={}, headers={"X-API-Key": "secret"})
    assert resp.status_code != 401, f"{method} {path} 不接受 X-API-Key"


def test_require_key_dependency_accepts_all_three_sources(client_and_cfg):
    """鉴权依赖本身必须支持 Authorization / X-API-Key / Cookie 三种来源"""
    import asyncio
    from fastapi import HTTPException
    client, cfg, m = client_and_cfg

    class _Req:
        def __init__(self, headers=None, cookies=None):
            self.headers = headers or {}
            self.cookies = cookies or {}

    cfg.API_KEY = "secret"

    async def _call(req):
        try:
            await m.require_key(req)
            return 200
        except HTTPException as e:
            return e.status_code

    assert asyncio.run(_call(_Req({"authorization": "Bearer secret"}))) == 200
    assert asyncio.run(_call(_Req({"x-api-key": "secret"}))) == 200
    assert asyncio.run(_call(_Req({}, {"bridge_admin_key": "secret"}))) == 200
    assert asyncio.run(_call(_Req({"authorization": "Bearer wrong"}))) == 401
    assert asyncio.run(_call(_Req({}))) == 401


def test_all_admin_routes_declare_auth_dependency(client_and_cfg):
    """静态校验：所有管理类路由都必须挂上 admin_auth 依赖。

    这是「以后再加新接口也不会忘记鉴权」的护栏 ——
    只靠逐个跑接口，新增路由时很容易漏掉。
    """
    import main as m
    client, cfg, _ = client_and_cfg

    admin_paths = {
        "/login/status", "/usage/recent", "/sites/models", "/sites/check",
        "/sites/discover", "/login/batch/status", "/admin/status",
        "/ide/detect", "/sites/add", "/login/batch", "/login/open",
        "/ide/configure/{ide_key}", "/ide/refresh-continue",
        "/zed/refresh-models", "/admin/validate",
    }

    def _norm(p):
        return p.replace("/poe", "/{x}").replace("/{site_name}", "/{x}") \
                .replace("/{site}", "/{x}").replace("/{ide_key}", "/{x}") \
                .replace("/{x}/code", "/{x}/code")

    protected = set()
    for r in m.app.routes:
        path = getattr(r, "path", None)
        if not path:
            continue
        deps = getattr(r, "dependant", None)
        call_deps = getattr(deps, "dependencies", []) if deps else []
        names = {getattr(d.call, "__name__", "") for d in call_deps}
        if "require_key" in names:
            protected.add(path)

    missing = set()
    for r in m.app.routes:
        path = getattr(r, "path", None)
        if not path or path in ("/openapi.json", "/docs", "/redoc", "/favicon.ico",
                                "/health", "/v1/models", "/v1/chat/completions",
                                "/v1/messages", "/", "/admin/unlock"):
            continue
        if path in admin_paths and path not in protected:
            missing.add(path)

    assert not missing, f"以下管理路由没有挂鉴权依赖: {sorted(missing)}"


def test_admin_endpoints_accept_bearer_token(client_and_cfg, tmp_sites_yaml):
    """Bearer 方式也要能过（IDE / 脚本调用习惯）"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.post("/sites/add", json={"name": "x", "url": "https://x.com"},
                       headers={"Authorization": "Bearer secret"})
    assert resp.status_code != 401


def test_admin_page_requires_key(client_and_cfg):
    """配置了 API_KEY 后，管理页本身必须鉴权（页面里含站点地址等信息）"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.get("/")
    assert resp.status_code == 401
    assert "API Key" in resp.text


def test_admin_page_accessible_with_key(client_and_cfg):
    """带正确 Key 时管理页正常返回"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.get("/", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_admin_unlock_sets_cookie(client_and_cfg):
    """POST /admin/unlock 正确时下发 HttpOnly Cookie"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.post("/admin/unlock", json={"key": "secret"})
    assert resp.status_code == 200
    assert "bridge_admin_key" in resp.cookies or "bridge_admin_key" in resp.headers.get(
        "set-cookie", "")


def test_admin_unlock_rejects_wrong_key(client_and_cfg):
    """错误 Key 必须被拒绝"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    resp = client.post("/admin/unlock", json={"key": "wrong"})
    assert resp.status_code == 401


def test_admin_cookie_grants_access(client_and_cfg):
    """带 Cookie 也能访问管理页（浏览器导航场景）"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = "secret"
    client.cookies.set("bridge_admin_key", "secret")
    resp = client.get("/")
    assert resp.status_code == 200


def test_no_api_key_means_no_auth_needed(client_and_cfg):
    """没配 API_KEY 时（本机自用默认场景）一切照旧，不破坏使用"""
    client, cfg, _ = client_and_cfg
    cfg.API_KEY = ""
    assert client.get("/").status_code == 200
    assert client.get("/login/status").status_code == 200
    assert client.get("/sites/models").status_code == 200


# ── 10. sites.yaml 写入：并发重名 + 原子写 ───────────────────────
#
# 实测 bug：/sites/add 的「查重」在锁外，两个并发同名请求会双双通过
# → sites.yaml 里出现两个同名站点。而站点名就是模型 ID，
#   /v1/models 因此返回重复模型 ID，IDE 模型列表出现重复项。

def test_sites_add_concurrent_same_name_yields_single_entry(tmp_path):
    """并发添加同名站点，sites.yaml 里只能有一个"""
    import threading
    import shutil
    from fastapi.testclient import TestClient
    from config import (RateLimitConfig, BehaviorConfig, CooldownConfig,
                        SecurityConfig, SiteConfig, CaptchaIndicator)
    import main as m

    # main.py 内部用 `Path(__file__).parent / "sites.yaml"` 定位文件，
    # 所以必须把 __file__ 指向临时目录，否则测试会写进真实的 sites.yaml。
    tmpdir = Path(tempfile.mkdtemp(prefix="dup_test_"))
    shutil.copy(Path(m.__file__).parent / "sites.yaml", tmpdir / "sites.yaml")
    shutil.copy(Path(m.__file__).parent / "main.py", tmpdir / "main.py")
    tmp_yaml = tmpdir / "sites.yaml"

    cfg = MagicMock()
    cfg.global_rate_limit = RateLimitConfig(requests_per_minute=10,
                                            requests_per_hour=100,
                                            requests_per_day=500,
                                            min_interval_seconds=1,
                                            max_interval_seconds=5)
    cfg.behavior = BehaviorConfig(typing_speed_min_ms=10, typing_speed_max_ms=30,
                                  typing_mistake_rate=0.0, scroll_probability=0.0,
                                  pause_probability=0.0, pause_duration_min=0.0,
                                  pause_duration_max=0.0, mouse_speed_min=1,
                                  mouse_speed_max=5)
    cfg.cooldown = CooldownConfig(initial_seconds=10, max_seconds=60,
                                  multiplier=2.0, reset_after_success=3)
    cfg.security = SecurityConfig(auto_pause=False, notification_sound=False,
                                   desktop_notification=False, max_wait_minutes=0,
                                   screenshot_on_captcha=False)
    cfg.captcha_indicators = [CaptchaIndicator(selector="#x", name="cf")]
    site = SiteConfig(name="poe", url="https://poe.com", enabled=True, priority=1,
                      selectors={"input": "textarea"}, models={"gpt-4o": "GPT-4o"})
    cfg.sites = [site]
    cfg.get_site = lambda n: next((s for s in cfg.sites if s.name == n), None)
    cfg.get_enabled_sites = lambda: [s for s in cfg.sites if s.enabled]
    cfg.API_KEY = ""

    old_cfg = m.config
    m.config = cfg
    try:
        with patch.object(m, "__file__", str(tmpdir / "main.py")):
            client = TestClient(m.app, raise_server_exceptions=False)
            results = []
            lock = threading.Lock()

            def add():
                r = client.post("/sites/add",
                                json={"name": "dupsite", "url": "https://dup.example"})
                with lock:
                    results.append(r.json())

            threads = [threading.Thread(target=add) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # 只能有一个成功，其余必须被「已存在」拦下
        ok = [r for r in results if r.get("success")]
        assert len(ok) == 1, f"并发同名添加出现了 {len(ok)} 次成功"

        import yaml
        data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8")) or {}
        names = [s.get("name") for s in data.get("sites", [])]
        assert names.count("dupsite") == 1, f"sites.yaml 里出现 {names.count('dupsite')} 个同名站点"
    finally:
        m.config = old_cfg
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_sites_yaml_write_is_atomic(tmp_path):
    """写 sites.yaml 必须是原子替换，不能留半截文件"""
    import main as m
    target = tmp_path / "sites.yaml"
    target.write_text("original", encoding="utf-8")

    m._write_sites_yaml_atomic(target, "new content")

    assert target.read_text(encoding="utf-8") == "new content"
    # 不应留下临时文件
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "sites.yaml"]
    assert not leftovers, f"残留临时文件: {leftovers}"
