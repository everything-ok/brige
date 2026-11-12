# AI 模型桥接（AI Bridge）

把 20+ 个 AI 网站、OpenRouter 免费模型、本机 Ollama 模型，统一封装成
**OpenAI 兼容 API**，让 VS Code / Cursor / Zed / Claude Code 等 IDE 直接使用。

```
IDE (Continue / Cursor / Zed / Claude Code)
        │  OpenAI 兼容协议  http://127.0.0.1:18792/v1
        ▼
┌────────────────────────────────────────────────┐
│  ai-bridge (18792)                             │
│   · 网页站点：Playwright 自动化 + 反检测       │
│   · or/* local-*：直通 free-gateway             │
└───────────────────┬────────────────────────────┘
                    │
┌───────────────────▼────────────────────────────┐
│  free-gateway (8787)                           │
│   · or/<model>  OpenRouter 免费模型            │
│   · local-*     本机 Ollama                    │
│   · auto        自动链：在线 > 本地 > 网页      │
└────────────────────────────────────────────────┘
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r ai-bridge/requirements.txt
pip install -r free-gateway/requirements.txt   # 若存在
playwright install chromium                     # 浏览器内核（仅网页站点需要）
```

### 2. 配置

`ai-bridge/.env`（复制自 `.env.example`）：

```ini
PORT=18792
HOST=127.0.0.1          # 默认只监听本机
BROWSER_MODE=profile    # profile = 独立持久化 profile；cdp = 复用系统 Chrome
BROWSER_HEADLESS=1      # 1=真无头（默认，完全不弹窗口）
```

`free-gateway/.env`：

```ini
GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=8787
OLLAMA_BASE=http://127.0.0.1:11434
BRIDGE_BASE=http://127.0.0.1:18792
# OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx   # 可选，配了才有 or/* 模型
```

### 3. 启动

```bash
# 一键启动（同时拉起 8787 与 18792）
python launcher.py

# 或分别启动
cd free-gateway && python gateway.py    # 8787
cd ai-bridge    && python main.py       # 18792
```

Windows 下也可双击 `启动桥接服务.bat` / `启动免费网关.bat`。

### 4. 接入 IDE

| IDE | 入口 | 说明 |
|-----|------|------|
| VS Code | Continue 插件 | `POST /ide/configure/vscode` 自动写 `~/.continue/config.yaml` |
| Cursor | OpenAI 自定义 Provider | `cursor.openai.baseUrl = http://127.0.0.1:18792/v1` |
| Zed | `bridge` / `openrouter` 两组模型 | `POST /ide/configure/zed` |
| Claude Code | `ANTHROPIC_BASE_URL=http://127.0.0.1:18792` | 走 `/v1/messages` |

管理页：<http://127.0.0.1:18792/>

---

## 核心特性

### 真无头 + 反检测

默认 `BROWSER_HEADLESS=1`，**运行期完全不弹浏览器窗口**。

`browser/stealth.py` 处理了真无头下 Chromium 暴露的自动化特征：

- `navigator.userAgent` / `appVersion` 去掉 `HeadlessChrome`
- `window.outerWidth` / `outerHeight` / `screen.width|height` 从 0 / 视口宽
  归一化为标准桌面分辨率（1920×1080）
- `navigator.webdriver = undefined`、补 `window.chrome` 对象
- 上下文级 HTTP 头替换，移除出站请求里的 `HeadlessChrome`

### 后台批量登录

20 个站点需要人工登录，逐个等待可能耗时数小时，因此改为**后台任务 + 轮询**：

```bash
# 启动（后台执行，立即返回）
curl -X POST http://127.0.0.1:18792/login/batch

# 轮询进度
curl http://127.0.0.1:18792/login/batch/status
```

```json
{ "running": true, "total": 12, "done": 3, "current": "yuanbao",
  "logged_in": 2, "failed": 1, "results": [...] }
```

批量登录期间会**临时**把目标站点切到可见窗口供你手动登录，完成后自动恢复无头。
重复启动会被拒绝（返回 `success: false`），不会并发开一堆浏览器。

### 工具调用（Agent 模式）

网页站点没有原生 function calling，因此用业界通用的 **text-based tool calling**：

1. 把 `tools[]` 的 JSON Schema 渲染成自然语言说明，追加到最后一条 user 消息
2. 要求模型输出固定围栏
3. 从回复文本解析回标准 OpenAI `tool_calls` 结构

支持的输出格式（全部实测通过）：

| 格式 | 示例 |
|------|------|
| ` ```tool_call ` | ` ```tool_call\n{"name":"f","arguments":{...}}\n``` ` |
| ` ```tool-call ` / ` ```toolcall ` | 同上 |
| ` ```json ` | ` ```json\n{"name":"f","arguments":{...}}\n``` ` |
| ` ```json object ` | 同上 |
| 裸围栏 | ` ```\n{"name":"f",...}\n``` ` |
| **裸 JSON** | `{"name": "f", "arguments": {...}}`（后面可跟解释文字） |
| **名值形式** | `get_weather: {"city": "Beijing"}` / `get_weather({"city": "Beijing"})` |

裸 JSON 支持是修复的关键：本地小模型（dolphin-mistral / qwen-abliterate）
默认输出裸 JSON，修复前工具调用会**静默降级为普通文本**，IDE 的 Agent 模式
完全不可用。

「名值形式」是第二轮实测发现的：小模型有时连 `name` 字段都不写，
直接输出 `get_weather: {...}`。这类输出没有围栏、没有 `"name"`，
所有旧正则都匹配不到 —— 实测 `live_e2e.py` 曾因此判定工具调用失败。
现在支持 3 种名值写法，但**只在整段文本就是一个调用时才认定**，
避免把正文里的 `note: {...}` 误判成工具调用。

另外，不支持原生 tools 的 Ollama 模型会自动降级为文本模拟
（`does not support tools` / `can't find closing '}'` 都会触发回退）。

---

## API 一览

### ai-bridge (18792)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 管理页（HTML） |
| GET | `/health` | 健康检查 |
| GET | `/v1/models` | 模型列表（**一个桥接站点 = 一个模型**，ID = 站点名） |
| POST | `/v1/chat/completions` | 对话（OpenAI 兼容，支持流式 / 工具调用） |
| POST | `/v1/messages` | Anthropic Messages 兼容 |
| GET | `/login/status` | 各站点登录态 |
| POST | `/login/{site}` | 单站点登录（弹可见窗口） |
| POST | `/login/batch` | **后台**批量登录 |
| GET | `/login/batch/status` | 批量登录进度 |
| POST | `/login/open` | 用系统浏览器打开登录页（仅 http/https） |
| GET | `/admin/status` | 限流 / 冷却状态 |
| POST | `/admin/unlock` | 管理页输入 API Key 后下发鉴权 Cookie |
| GET | `/sites/check` | 站点连通性探测（不启动浏览器） |
| GET | `/sites/models` | 站点模型明细 |
| POST | `/sites/add` | 新增站点到 `sites.yaml`（严格校验 + 并发去重 + 原子写） |
| POST | `/sites/remove/{site}` | 移除站点 |
| GET | `/ide/detect` | 检测本机已安装的 IDE |
| POST | `/ide/configure/{ide}` | 自动写入 IDE 配置 |
| POST | `/ide/refresh-continue` | 刷新 Continue 模型列表 |

> 除 `/health`、`/v1/*` 与 `/admin/unlock` 外，所有接口都受 `API_KEY` 保护
> （未配置 `API_KEY` 时不限制）。

### free-gateway (8787)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/models` | 模型目录（`or/*` + `local-*`） |
| POST | `/v1/chat/completions` | 对话（自动链） |
| GET | `/local-models` / `/local-models/status` | 本地模型列表 / 状态 |
| POST | `/local-models/add` | 接入本机 Ollama 模型 |
| POST | `/market/refresh` | 刷新模型市场 |
| GET | `/health` | 健康检查 |

---

## 安全

| 项 | 行为 |
|----|------|
| 默认监听 | `HOST=127.0.0.1`，仅本机可访问 |
| 对外监听 | 设 `HOST=0.0.0.0` 且**未设** `API_KEY` → **拒绝启动**（否则整个局域网可代理你的模型请求、写入 `sites.yaml`、打开本机浏览器） |
| API Key | 设置 `API_KEY` 后**全部**接口（不只是 `/v1/*`）都需鉴权。三种凭据来源任选其一：`Authorization: Bearer <key>`、`X-API-Key: <key>`、Cookie `bridge_admin_key`（管理页输入 Key 后自动下发，HttpOnly + SameSite=Strict） |
| 管理页鉴权 | 配了 `API_KEY` 后 `GET /` 也会返回 401 并渲染"输入 API Key"页面；解锁后页面内的 fetch 会自动带上 `X-API-Key` |
| 路由级鉴权 | 鉴权以 FastAPI **依赖**形式挂在路由上，而不是写在 handler 第一行 —— 后者会被请求体校验（422）抢先返回，等于绕过 |
| CORS | 对外监听 + 有 Key 时关闭任意来源跨域 |
| XSS | 管理页所有动态字段（`sites.yaml` 的 name/url/模型名、网关返回的 label/id/备注）均做 HTML 转义；放进 JS 字符串的额外转义引号与反斜杠 |
| YAML 注入 | `POST /sites/add` 严格校验：站点名限 `^[a-z0-9][a-z0-9_-]{0,63}$`，URL 必须 http/https 且不含引号/尖括号/换行 |
| 文件原子写 | `sites.yaml` 与 `metadata.json` 均用「临时文件 + `os.replace`」写入，写入中途崩溃不会留下半截文件 |
| 路径穿越 | `ProfileManager` 的站点目录名经过 `_safe_dirname()` 收敛，无法用 `../` 越出 profile 根目录 |
| 协议限制 | `POST /login/open` 只允许 http/https（拒绝 `file://`） |
| 模型 ID 校验 | 网关 `POST /local-models/add` 的模型名限 `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`，且不允许与内置模型（`auto` / `llm7-*` / `or/*`）撞名 |
| 工具白名单 | 模型输出的工具名必须在请求的 `tools[]` 内，否则降级为普通文本 |
| 限流原子性 | `acquire(reserve=True)` 把「检查」与「占用」合并为一步，并发请求不会突破限额；配额用唯一 token 记账，失败释放不会偷走别的并发请求的槽位 |

### API Key 使用

```bash
# .env
API_KEY=<随机长字符串>
```

| 场景 | 用法 |
|------|------|
| IDE / 脚本 | `Authorization: Bearer <key>` |
| 浏览器 fetch | `X-API-Key: <key>` |
| 管理页 | 打开 `http://127.0.0.1:18792/` 会要求输入 Key，输入后自动记住 |
| free-gateway → ai-bridge | 网关自动读取同一个 `.env` 的 `API_KEY`（或 `BRIDGE_API_KEY`）并带上 |

未配置 `API_KEY` 时（本机自用的默认场景）**一切照旧，不加任何限制**。

---

## 测试

```bash
# 单元 + 集成测试
cd ai-bridge    && python -m pytest -q     # 212 passed
cd free-gateway && python -m pytest -q     #  49 passed

# 真实服务端到端测试（需先启动两个服务）
python live_e2e.py                          # 57 passed
```

`live_e2e.py` 会真的发 HTTP 请求、真的调模型推理，覆盖：
健康检查、模型列表、非流式/流式对话、工具调用、错误码、认证、
边界输入（空内容 / 100k 字符 / 多轮 / 空 schema）、并发（4 并发对话 +
8 并发健康检查）、性能（延迟阈值）。

### 测试文件分工

| 文件 | 覆盖 |
|------|------|
| `ai-bridge/tests/test_api.py` | API 契约、鉴权、错误码 |
| `ai-bridge/tests/test_config.py` | 配置加载、**热重载语义**（重载不得翻倍） |
| `ai-bridge/tests/test_profile.py` | profile 元数据损坏恢复、原子写、路径穿越 |
| `ai-bridge/tests/test_rate_limiter*.py` | 限流边界与**并发抢占**、配额 token 配对 |
| `ai-bridge/tests/test_security_regressions.py` | XSS / YAML 注入 / 鉴权绕过 / 路由冲突 / 并发写 / 上下文裁剪 |
| `free-gateway/tests/test_gateway.py` | 工具调用围栏解析（含名值形式）、消息拍平、模型 ID 校验 |

> 注意：涉及写文件的测试必须把 `sites.yaml` / IDE 配置指向临时目录。
> 项目里已用 `tmp_sites_yaml` fixture 隔离，避免测试污染开发机。

---

## 项目结构

```
ai-bridge/
  main.py               FastAPI 主服务（18792）
  config.py             配置（HOST/PORT/API_KEY/sites.yaml 加载）
  tool_emulation.py     网页站点的文本工具调用模拟
  model_catalog.py      统一模型目录（bridge + openrouter + local）
  ide_config.py         VS Code / Cursor / Zed / Claude Code 配置写入
  continue_config.py    Continue config.yaml 生成
  sites.yaml            站点定义（URL / 选择器 / 模型）
  browser/
    manager.py          Playwright 上下文与页面管理（无头 / 反检测）
    stealth.py          反检测 JS 注入
    profile.py          持久化 profile 与登录态记录
  providers/            站点通用对话实现 + 模型抓取 + 连通性校验
  limiter/              限流、冷却、负载均衡
  security/             验证码检测与处理
  tests/                单元与安全回归测试

free-gateway/
  gateway.py            模型聚合网关（8787）
  tests/                网关与工具模拟契约测试

msi/payload/            MSI 安装包源文件（与上面对应目录保持同步）
launcher.py             一键启动器
live_e2e.py             真实服务端到端测试
```

---

## 已知限制

- 网页站点受站点风控影响，超频会触发验证码；桥接已做限流 + 冷却快拒，
  但无法完全避免。
- 不支持原生 tools 的小模型靠文本模拟，工具调用准确率取决于模型能力。
- `cdp` 浏览器模式在 Chrome 151+ 已失效（默认用户数据目录禁止开调试端口），
  请用 `profile` 模式。
- 兼容性主要在 Windows / Python 3.14 / Chromium 上验证；
  macOS、Linux 未实测（代码本身无平台专属逻辑）。
