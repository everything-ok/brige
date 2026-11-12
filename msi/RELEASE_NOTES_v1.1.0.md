# AI 模型桥接 v1.1.0

把「网页版大模型」变成你本地可调用的 **OpenAI 兼容 API**。本版本是继 v1.0.0 之后的稳定性与安全性更新。

## 下载

| 文件 | 说明 |
| --- | --- |
| `AI-Bridge-1.1.0.msi` | Windows 安装包（x64，无需管理员权限，安装到 `%LOCALAPPDATA%\AI 模型桥接`） |

安装后桌面与开始菜单会生成「AI 模型桥接」快捷方式，双击即可一键拉起两个服务并打开管理页。

## 本版本主要变化

### 🔐 安全

- **数据面鉴权前置**：`/v1/models`、`/v1/chat/completions`、`/v1/messages` 改为依赖注入式鉴权（`require_api_key`），只接受 `Authorization: Bearer` 或 `X-API-Key` 请求头，**拒绝 Cookie**，从根本上阻断浏览器侧的 CSRF 伪造。
- 修掉了原先「请求体校验先于鉴权触发、导致 422 掩盖 401」的鉴权抢跑问题。

### ⚡ 稳定性

- **Ollama 准入控制**：本地 CPU 推理场景下新增在途请求数上限（默认 4）、排队上限（默认 8）与最长等待（默认 120 秒），超限直接返回 `429` 并带 `Retry-After`，不再无限排队导致客户端超时。
- 桥接侧在流式转发中正确透传上游 `429 gateway_busy`，不再被静默吞掉。

### 🧹 工程

- 清理主链路文件的无用导入与死代码（`main.py` / `config.py` / `gateway.py`）。
- `launcher.py` 修复 docstring 转义告警，EXE 重新打包。
- 新增 `msi/build_wxs.py`：按 `msi/payload` 实际内容自动推导 WiX 目录树与 Component，历史 GUID 自动沿用（升级链不断），新增文件用 UUIDv5 确定性推导 —— **MSI 构建从此可复现**。

## 质量验证

| 测试套件 | 结果 |
| --- | --- |
| `ai-bridge` | 217 passed |
| `free-gateway` | 62 passed |
| `live_e2e.py`（真实联调） | 57 passed / 0 failed / 0 skipped |

安装包亦做过真实安装验证：静默安装退出码 0，落盘文件与源码逐字节哈希一致，卸载后无残留。

## 升级说明

沿用与 v1.0.0 相同的 `UpgradeCode`，可直接覆盖升级。若需重装，请先在「设置 → 应用」中卸载旧版本。
