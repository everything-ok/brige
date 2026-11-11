"""
VSCode / Continue 2.0 接入

关键事实（针对 Continue >= 1.x / 2.x）：
  · Continue 2.x **不再读** VSCode settings.json 里的 `continue.customModels`
    （扩展包内 customModels 出现 0 次），只读 `~/.continue/config.yaml`。
  · config.yaml 的 schema 顶层没有 `defaultModel`；默认模型存在
    `~/.continue/index/globalContext.json` 的 `selectedModelsByProfileId`。
  · provider 必须是已知 id；`openai` + 自定义 apiBase 会走 /chat/completions
    （非官方 OpenAI 域名不会误用 /responses 端点），正好匹配桥接。
  · 模型条目必填 name / model / provider；`roles` 决定它出现在哪些选择器里。

因此本模块负责：
  1) 合并写入 ~/.continue/config.yaml 的 models（保留用户原有模型）
  2) 写入 globalContext.json 的 selectedModelsByProfileId（默认模型）
  3) 完全重启 VSCode 使配置生效
"""
import json
import os
import sys
import time
from typing import Dict, List, Optional

import yaml

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = int(os.getenv("PORT", 18792))
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

CONTINUE_HOME = os.path.join(os.path.expanduser("~"), ".continue")
CONFIG_YAML = os.path.join(CONTINUE_HOME, "config.yaml")
CONFIG_JSON = os.path.join(CONTINUE_HOME, "config.json")
GLOBAL_CONTEXT = os.path.join(CONTINUE_HOME, "index", "globalContext.json")

# Continue 内置的角色（config.yaml 里用 roles 字段声明）
DEFAULT_ROLES = ["chat", "summarize", "apply", "edit"]

# VSCode 扩展 ID → 扩展安装目录名（用于检测 Continue 是否已安装）
CONTINUE_EXT_MARKERS = ("continue.continue",)


# ── 路径/检测 ───────────────────────────────────────────────────
def continue_installed() -> bool:
    """检测 VSCode 是否装了 Continue 扩展"""
    ext_roots = [
        os.path.join(os.path.expanduser("~"), ".vscode", "extensions"),
        os.path.join(os.path.expanduser("~"), ".vscode-insiders", "extensions"),
    ]
    for root in ext_roots:
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                if name.startswith(CONTINUE_EXT_MARKERS):
                    return True
        except OSError:
            continue
    return False


def continue_config_path() -> Optional[str]:
    """Continue 2.x 优先 config.yaml；退回 config.json（老版本）"""
    if os.path.exists(CONFIG_YAML):
        return CONFIG_YAML
    if os.path.exists(CONFIG_JSON):
        return CONFIG_JSON
    return None


# ── 写入 config.yaml ────────────────────────────────────────────
def _load_yaml_config(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_yaml_config(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _normalize_model(m: dict) -> dict:
    """把老式（Continue 1.x / 自定义写法）模型条目规范成 Continue 2.0 schema。

    变化：
      title      → name
      apiBaseUrl → apiBase
      maxTokens/temperature/topP → defaultCompletionOptions
      systemMessage → chatOptions.baseSystemMessage
    未知 provider 的条目保持原样（避免猜错后模型直接不可用）。
    """
    out = dict(m)
    if "name" not in out and "title" in out:
        out["name"] = out.pop("title")
    if "apiBase" not in out and "apiBaseUrl" in out:
        out["apiBase"] = out.pop("apiBaseUrl")
    if "apiKey" in out and isinstance(out["apiKey"], (int, float)):
        out["apiKey"] = str(out["apiKey"])

    dco = dict(out.get("defaultCompletionOptions") or {})
    for k in ("maxTokens", "temperature", "topP", "topK", "stop"):
        if k in out and k not in dco:
            dco[k] = out.pop(k)
    if dco:
        out["defaultCompletionOptions"] = dco

    if "systemMessage" in out:
        sm = out.pop("systemMessage")
        chat_opts = dict(out.get("chatOptions") or {})
        chat_opts.setdefault("baseSystemMessage", sm)
        out["chatOptions"] = chat_opts

    return out


def write_continue_config(models: List[dict], set_default: bool = True,
                          default_title: Optional[str] = None) -> Dict:
    """把桥接模型合并写入 Continue 配置。

    models: model_catalog.continue_models() 产出的条目
    返回 {"path":..., "added": n, "default": title}
    """
    path = continue_config_path() or CONFIG_YAML
    cfg = _load_yaml_config(path)

    # Continue 2.x 要求 name/version；老版本 config.json 用 models/title
    cfg.setdefault("name", "Main Config")
    cfg.setdefault("version", "1.0.0")
    cfg.setdefault("schema", "v1")

    existing = cfg.get("models") or []
    if not isinstance(existing, list):
        existing = []

    # 移除旧的桥接条目（按 apiBase 指向本机桥接判定），避免重复累积
    kept = []
    for m in existing:
        if not isinstance(m, dict):
            continue
        base = str(m.get("apiBase") or m.get("apiBaseUrl") or "")
        title = str(m.get("name") or m.get("title") or "")
        if BRIDGE_BASE in base or title.startswith("bridge｜"):
            continue
        kept.append(_normalize_model(m))

    added = 0
    for m in models:
        # 同名条目先移除，避免重复
        kept = [k for k in kept
                if not (isinstance(k, dict)
                        and (k.get("name") or k.get("title")) == m["name"])]
        kept.append(m)
        added += 1

    cfg["models"] = kept
    _save_yaml_config(path, cfg)

    default = None
    if set_default:
        default = default_title or (models[0]["name"] if models else None)
        if default:
            set_selected_model(default, roles=["chat", "edit", "apply", "summarize"])

    return {"path": path, "added": added, "default": default}


# ── 写入默认模型（globalContext.json）───────────────────────────
def _load_global_context() -> dict:
    if not os.path.exists(GLOBAL_CONTEXT):
        return {}
    try:
        with open(GLOBAL_CONTEXT, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_selected_model(title: str, roles: Optional[List[str]] = None,
                       profile_id: str = "local") -> bool:
    """把某个模型设为各角色的默认选择（Continue 的模型下拉框默认项）。"""
    roles = roles or DEFAULT_ROLES
    ctx = _load_global_context()
    sel = ctx.get("selectedModelsByProfileId") or {}
    for_profile = sel.get(profile_id) or {}
    for_profile = dict(for_profile)
    for role in roles:
        for_profile[role] = title
    sel[profile_id] = for_profile
    ctx["selectedModelsByProfileId"] = sel

    os.makedirs(os.path.dirname(GLOBAL_CONTEXT), exist_ok=True)
    tmp = GLOBAL_CONTEXT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ctx, f, indent=2, ensure_ascii=False)
    os.replace(tmp, GLOBAL_CONTEXT)
    return True


# ── VSCode 重启（完全退出再启动）─────────────────────────────────
def _vscode_exe() -> Optional[str]:
    """定位 VSCode 可执行文件（覆盖常见安装位置 + 注册表 + PATH）"""
    cands = [
        os.path.join(os.path.expanduser("~"), "AppData", "Local",
                     "Programs", "Microsoft VS Code", "Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
        r"D:\Microsoft VS Code\Code.exe",
        r"D:\Program Files\Microsoft VS Code\Code.exe",
        r"D:\VSCode\Code.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            return c

    # 注册表 App Paths
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                k = winreg.OpenKey(
                    root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Code.exe")
                val, _ = winreg.QueryValueEx(k, "")
                winreg.CloseKey(k)
                if val and os.path.exists(val):
                    return val
            except OSError:
                continue
    except ImportError:
        pass

    # 磁盘扫描（常见盘符的顶层目录）
    for drive in ("C:", "D:", "E:"):
        for sub in ("Microsoft VS Code", "VSCode", "Programs\\Microsoft VS Code"):
            p = os.path.join(drive + "\\", sub, "Code.exe")
            if os.path.exists(p):
                return p
    return None


def _vscode_running() -> bool:
    """判断是否还有 VSCode 进程在跑"""
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "")
                if pname == "code" or pname.startswith("code"):
                    return True
            except Exception:
                continue
    except ImportError:
        pass
    return False


def _kill_vscode() -> List[str]:
    """强制结束所有 VSCode 进程（含子进程窗口）"""
    killed = []
    try:
        import subprocess
        subprocess.run(["taskkill", "/F", "/T", "/IM", "Code.exe"],
                       capture_output=True, timeout=30)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "Code - Insiders.exe"],
                       capture_output=True, timeout=30)
        killed.append("Code.exe")
    except Exception:
        pass
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "")
                if pname == "code" or pname.startswith("code"):
                    proc.kill()
                    killed.append(pname)
            except Exception:
                continue
    except ImportError:
        pass
    return killed


def restart_vscode(extra_env: Optional[dict] = None) -> Dict:
    """完全退出 VSCode 后重新启动，确保配置 100% 生效。"""
    import subprocess

    exe = _vscode_exe()
    if not exe:
        return {"ok": False, "reason": "未找到 VSCode 可执行文件"}

    killed = _kill_vscode()
    # 等进程完全退出，避免新实例被旧实例接管
    for _ in range(40):
        time.sleep(0.5)
        if not _vscode_running():
            break

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        subprocess.Popen(
            [exe],
            env=env,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return {"ok": False, "reason": f"启动失败: {e}", "killed": killed}

    return {"ok": True, "exe": exe, "killed": killed}
