"""
AI-Bridge IDE 一键配置工具（CLI 版）
用法:
  python ide_config.py                    # 交互式菜单
  python ide_config.py --list             # 只列出检测到的 IDE
  python ide_config.py kiro               # 只配置 Kiro
  python ide_config.py kiro cursor         # 配置多个
  python ide_config.py --all              # 配置所有检测到的
  python ide_config.py --restart kiro      # 只重启 Kiro
"""
import os, sys, json, time, platform, argparse, subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYS = platform.system()
HOME = os.path.expanduser("~")
PYTHON = sys.executable
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18792
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

# 模型 ID = 桥接站点名（IDE 选择器按站点名显示，与管理页「已接入站点」一致）
# 兜底列表：服务未启动时使用，zai 优先级最高
DEFAULT_SITE_MODELS = ["zai", "perplexity", "arena", "you", "chatgpt", "grok",
                       "doubao", "poe", "happycapy", "deepseek"]
DEFAULT_MODEL = DEFAULT_SITE_MODELS[0]

# 本阶段主要以 VSCode 验证，kiro 暂缓
VERIFY_TARGET = "code"
DEFERRED_IDES = ["kiro"]

# Windows-only imports, loaded lazily
_winreg = None
def _get_winreg():
    global _winreg
    if _winreg is None:
        try:
            import winreg
            _winreg = winreg
        except ImportError:
            pass
    return _winreg

# ── IDE 定义 ──────────────────────────────────────────────────
ALL_IDES = [
    dict(key="kiro",     name="Kiro",          exe_names=["kiro"],
         win_paths=[r"D:\Kiro\Kiro.exe",
                    os.path.join(HOME,"AppData","Local","Programs","Kiro","Kiro.exe")],
         config_file=os.path.join(HOME,".kiro","settings","mcp.json"),
         type="mcp", extra_cc=True),
    # cursor kept for future use - hidden from CLI
    dict(key="windsurf", name="Windsurf",       exe_names=["windsurf"],
         win_paths=[os.path.join(HOME,"AppData","Local","Programs","windsurf","Windsurf.exe")],
         config_file=os.path.join(HOME,".codeium","windsurf","mcp_config.json"),
         type="mcp", extra_cc=False),
    dict(key="zed",     name="Zed",            exe_names=["zed"],
         win_paths=[os.path.join(HOME,"AppData","Local","Programs","Zed","bin","Zed.exe"),
                    os.path.join(HOME,"AppData","Local","Programs","Zed","Zed.exe"),
                    r"D:\Zed\Zed.exe", r"D:\Zed\bin\Zed.exe"],
         config_file=os.path.join(HOME,"AppData","Roaming","Zed","settings.json"),
         type="zed", extra_cc=False),
    dict(key="code",     name="VSCode",         exe_names=["code"],
         win_paths=[os.path.join(HOME,"AppData","Local","Programs","Microsoft VS Code","Code.exe"),
                    r"C:\Program Files\Microsoft VS Code\Code.exe",
                    r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
                    r"D:\Microsoft VS Code\Code.exe",
                    r"D:\Program Files\Microsoft VS Code\Code.exe",
                    r"D:\VSCode\Code.exe"],
         config_file=os.path.join(HOME,"AppData","Roaming","Code","User","settings.json"),
         type="vscode", extra_cc=True),
    dict(key="claude",   name="Claude Desktop", exe_names=["claude"],
         win_paths=[os.path.join(HOME,"AppData","Local","Programs","claude","Claude.exe"),
                    os.path.join(HOME,"AppData","Roaming","npm","claude.cmd")],
         config_file=os.path.join(HOME,".claude","mcp.json"),
         type="mcp", extra_cc=False),
]

# ── 工具函数 ───────────────────────────────────────────────────
def cprint(msg, color=None):
    # Windows GBK 安全，去掉所有 emoji
    for old, new in [
        ("\u2705", "[OK]"), ("\u274c", "[x]"), ("\u26a0", "[~]"),
        ("\u2015", "-"), ("\u2014", "--"),
        ("\U0001f7e2", "[Y]"), ("\U0001f7e3", "[O]"), ("\U0001f7e1", "[W]"),
        ("\U0001f534", "[o]"), ("\U0001f4e6", "[pkg]"), ("\U0001f680", "[>]"),
        ("\U0001f7e0", "[Y]"), ("\U0001f7e4", "[o]"),
    ]:
        msg = msg.replace(old, new)
    colors = dict(
        green="\033[92m", red="\033[91m", yellow="\033[93m",
        blue="\033[94m", cyan="\033[96m", bold="\033[1m",
        dim="\033[2m", reset="\033[0m",
    )
    c = colors.get(color, "")
    print(f"{c}{msg}{colors['reset']}")

def http_get(path, timeout=5):
    import http.client
    conn = http.client.HTTPConnection(BRIDGE_HOST, BRIDGE_PORT, timeout=timeout)
    conn.request("GET", path, headers={"Accept": "application/json"})
    resp = conn.getresponse()
    return resp.status, resp.read().decode("utf-8")

def http_get_url(url, timeout=5):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")

# 与 main.py 的 LOGIN_REQUIRED_SITES 保持一致（用于 Zed 待登录分组）
_LOGIN_REQUIRED_SITES = {"poe", "perplexity", "you", "chatgpt", "kimi", "doubao",
                         "yuanbao", "tiangong", "qwen", "zai", "arena",
                         "grok", "deepseek", "huggingchat", "mistral", "cohere", "groq", "phind", "happycapy", "hackaigc", "nanogpt"}

def check_bridge():
    try:
        status, _ = http_get("/health")
        return status == 200
    except:
        return False

def find_ide_exe(ide):
    # 1. 从 App Paths 注册表找（Windows）
    wr = _get_winreg()
    if SYS == "Windows" and wr:
        for root in [wr.HKEY_LOCAL_MACHINE, wr.HKEY_CURRENT_USER]:
            try:
                h = wr.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
                for i in range(wr.QueryInfoKey(h)[0]):
                    try:
                        name = wr.EnumKey(h, i).lower()
                        for kw in ide["exe_names"]:
                            if kw in name or name == kw + ".exe":
                                sub_h = wr.OpenKey(h, wr.EnumKey(h, i))
                                try:
                                    val, _ = wr.QueryValueEx(sub_h, "")
                                    if val and os.path.exists(val):
                                        wr.CloseKey(sub_h)
                                        wr.CloseKey(h)
                                        return val
                                except FileNotFoundError:
                                    pass
                                wr.CloseKey(sub_h)
                    except Exception:
                        continue
                wr.CloseKey(h)
            except FileNotFoundError:
                pass
            except Exception:
                pass

    # 2. 从运行进程找 exe 路径（沙箱外才有效）
    try:
        import psutil
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                for kw in ide["exe_names"]:
                    if pname == kw or pname.startswith(kw):
                        if proc.info.get("exe") and os.path.exists(proc.info["exe"]):
                            return proc.info["exe"]
            except Exception:
                continue
    except ImportError:
        pass

    # 3. 从已知路径找
    for path in ide["win_paths"]:
        if os.path.exists(path):
            return path
    return None

def find_running_ides():
    found = []
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                for ide in ALL_IDES:
                    if ide["key"] in found:
                        continue
                    for kw in ide["exe_names"]:
                        if pname == kw or pname.startswith(kw):
                            found.append(ide["key"])
                            break
            except Exception:
                continue
    except ImportError:
        pass
    return found

def kill_ide(ide):
    killed = []
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                for kw in ide["exe_names"]:
                    if pname == kw or pname.startswith(kw):
                        proc.terminate()
                        killed.append(pname)
            except Exception:
                continue
    except ImportError:
        for kw in ide["exe_names"]:
            try:
                subprocess.run(["taskkill", "/F", "/IM", f"{kw}.exe"],
                             capture_output=True, timeout=10)
                killed.append(kw)
            except Exception:
                pass
    return killed

def start_ide(ide, extra_env=None):
    exe = find_ide_exe(ide)
    if not exe:
        return None, "executable not found"
    try:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        if SYS == "Windows":
            subprocess.Popen(
                [exe],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        else:
            subprocess.Popen([exe], start_new_session=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=env)
        return exe, None
    except Exception as e:
        return None, str(e)


def build_vscode_bridge_env(api_key):
    """仅注入 VSCode 进程的桥接环境变量。
    CLAUDE_CONFIG_DIR 指向独立配置目录 ~/.claude-bridge（内含桥接专属 settings.json），
    使该 VSCode 里的 Claude 与全局 ~/.claude(bigmodel) 彻底隔离、不混用；
    Kiro 等其他 IDE 的 Claude 完全不受影响。"""
    return {
        "CLAUDE_CONFIG_DIR": os.path.join(HOME, ".claude-bridge"),
        "ANTHROPIC_BASE_URL": BRIDGE_BASE,
        "ANTHROPIC_AUTH_TOKEN": api_key or "any",
        "ANTHROPIC_MODEL": DEFAULT_MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": DEFAULT_MODEL,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": DEFAULT_MODEL,
        "ANTHROPIC_SMALL_FAST_MODEL": DEFAULT_MODEL,
    }

def set_env_var(key, value):
    if SYS != "Windows":
        return
    try:
        import winreg, ctypes
        key_h = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                               winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key_h, key, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key_h)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, None)
    except Exception:
        pass

def get_bridge_api_key():
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("API_KEY=") and not line.startswith("API_KEY=#"):
                return line.split("=", 1)[1].strip()
    return "any"

def get_available_models():
    """每个桥接站点 = 一个模型（ID=站点名），按优先级从 /v1/models 获取"""
    try:
        _, body = http_get("/v1/models")
        data = json.loads(body)
        models = [m["id"] for m in data.get("data", [])]
        return models or list(DEFAULT_SITE_MODELS)
    except Exception:
        return list(DEFAULT_SITE_MODELS)

# ── 配置写入 ───────────────────────────────────────────────────
def build_mcp_block(base_url, api_key):
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-openai-compatible",
                 "--base-url", f"{base_url}/v1", "--api-key", api_key],
        "disabled": False,
        "autoApprove": ["chat"],
    }

def build_claude_code_block(base_url, api_key, models):
    return dict(
        type="openai", baseURL=f"{base_url}/v1", apiKey=api_key,
        name="Bridge", models=models,
    )

def write_config(ide, mcp_block, claude_code_block, base_url, api_key, models=None):
    path = ide["config_file"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    models = models or list(DEFAULT_SITE_MODELS)

    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    if ide["type"] == "mcp":
        existing.setdefault("mcpServers", {})["ai-bridge"] = mcp_block
    elif ide["type"] == "vscode":
        # Continue 2.x 只读 ~/.continue/config.yaml，不读 settings.json 的
        # continue.customModels —— 旧写法会导致模型在下拉框里完全看不到。
        try:
            import model_catalog
            import continue_config as cc

            catalog = model_catalog.build_catalog(models)
            entries = model_catalog.continue_models(catalog, api_key=api_key)
            res = cc.write_continue_config(entries, set_default=True)
            return res["path"], None
        except Exception as e:
            # 兜底：老版本 Continue 仍写 settings.json
            models_list = [m for m in existing.get("continue.customModels", [])
                           if m.get("title") != "Bridge"]
            models_list.append(dict(title="Bridge", provider="openai",
                                    model=DEFAULT_MODEL,
                                    apiBase=f"{base_url}/v1", apiKey=api_key))
            existing["continue.customModels"] = models_list
    elif ide["type"] == "zed":
        existing.pop("disable_ai", None)
        existing["restore_on_startup"] = "last_session"
        # 新版 Zed 用 "agent" 键；"assistant" 已废弃，清掉避免警告
        existing["agent"] = {"default_model": {"provider": "bridge",
                                               "model": DEFAULT_MODEL}}
        existing.pop("assistant", None)
        _lm = existing.setdefault("language_models", {})
        # 需登录站点单独分组（与桥接管理页页签一致）
        pending = [m for m in models if m in _LOGIN_REQUIRED_SITES]
        ready = [m for m in models if m not in _LOGIN_REQUIRED_SITES]
        _lm.update({
            # 注意：Zed 只认下划线 openai_compatible，连字符写法会被静默忽略
            "openai_compatible": {
                "bridge": dict(
                    api_url=f"{base_url}/v1", api_key=api_key,
                    available_models=[
                        dict(name=m, display_name=m, max_tokens=8192,
                             supports_tools=False, supports_images=False)
                        for m in ready
                    ],
                ),
                "pending-login": dict(
                    api_url=f"{base_url}/v1", api_key=api_key,
                    available_models=[
                        dict(name=m, display_name=m, max_tokens=8192,
                             supports_tools=False, supports_images=False)
                        for m in pending
                    ],
                ),
            }
        })
        # openrouter 组（free-gateway 8787，or/* 免费模型）；网关未运行时兜底
        try:
            _, gw_body = http_get_url("http://127.0.0.1:8787/v1/models")
            gw_ids = [x["id"] for x in json.loads(gw_body).get("data", [])]
        except Exception:
            gw_ids = ["auto", "local-dolphin-mistral", "local-lexi-llama3"]
        _lm["openai_compatible"]["openrouter"] = dict(
            api_url="http://127.0.0.1:8787/v1", api_key="any",
            available_models=[
                dict(name=g, display_name=g, max_tokens=8192,
                     supports_tools=False, supports_images=False)
                for g in gw_ids
            ],
        )
        _lm.pop("openai-compatible", None)   # 清理连字符旧键（Zed 不识别）

    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    # 注意：不写 ~/.claude.json（用户级全局文件，会影响 Kiro 等其他 IDE 的 Claude）。
    # VSCode 的 Claude 桥接通过启动时注入进程环境变量实现（见 configure_ide / start_ide）。
    return path, None

# ── 核心逻辑 ───────────────────────────────────────────────────
def configure_ide(ide_key, base_url, api_key, models, mcp_block, claude_code_block, restart=True):
    ide = next((i for i in ALL_IDES if i["key"] == ide_key), None)
    if not ide:
        cprint(f"  Unknown IDE: {ide_key}", "red")
        return False

    cprint(f"\n{'-'*50}", "dim")
    cprint(f"[>] {ide['name']} ({ide_key})", "cyan")

    # 1. 杀掉进程
    if restart:
        killed = kill_ide(ide)
        if killed:
            cprint(f"  [OK] Killed: {killed}", "green")
        else:
            cprint(f"  [--] No running process found", "yellow")
        time.sleep(0.5)

    # 2. 写配置（仅 IDE 私有文件，不碰全局 ~/.claude.json / ~/.claude/settings.json）
    try:
        path, cc_path = write_config(ide, mcp_block, claude_code_block, base_url, api_key, models=models)
        cprint(f"  [OK] Wrote MCP config -> {path}", "green")
    except Exception as e:
        cprint(f"  [x] Write failed: {e}", "red")
        return False

    # 3. Zed 环境变量
    if ide["key"] == "zed":
        set_env_var("BRIDGE_API_KEY", api_key)
        set_env_var("BRIDGE_BASE_URL", f"{base_url}/v1")
        cprint(f"  [OK] Set env BRIDGE_API_KEY / BRIDGE_BASE_URL", "green")

    # 4. 重启（VSCode 注入桥接环境变量，仅对该实例生效）
    if restart:
        time.sleep(0.3)
        if ide["key"] == "code":
            # VSCode 走 continue_config.restart_vscode：
            # 完全退出（taskkill /F /T，等进程真正结束）再重开，
            # 否则旧实例接管新请求，Continue 的 config.yaml 不会刷新。
            try:
                import continue_config as cc
                res = cc.restart_vscode(extra_env=build_vscode_bridge_env(api_key))
                if res.get("ok"):
                    cprint("  [OK] Restarted VSCode (完全重启)", "green")
                    cprint(f"  [OK] 桥接环境变量已注入此 VSCode 实例 "
                           f"(Claude -> {BRIDGE_BASE}/v1, 模型 {DEFAULT_MODEL})", "green")
                    cprint("  [i] 仅对此 VSCode 生效；Kiro 等其他 IDE 的 Claude 不受影响", "dim")
                else:
                    cprint(f"  [--] Restart failed: {res.get('reason')}", "yellow")
                    exe, err = start_ide(ide, extra_env=build_vscode_bridge_env(api_key))
                    if exe:
                        cprint("  [OK] Started VSCode (fallback)", "green")
            except Exception as e:
                cprint(f"  [--] restart_vscode 失败: {e}", "yellow")
                exe, err = start_ide(ide, extra_env=build_vscode_bridge_env(api_key))
                if exe:
                    cprint("  [OK] Started VSCode (fallback)", "green")
        else:
            exe, err = start_ide(ide)
            if exe:
                cprint(f"  [OK] Started {ide['name']}", "green")
            else:
                cprint(f"  [--] Start failed: {err} (please start manually)", "yellow")
    else:
        cprint(f"  [i] Config written, please restart {ide['name']} manually", "yellow")

    return True

# ── 主程序 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AI-Bridge IDE 一键配置工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ide_config.py                  # 交互式菜单
  python ide_config.py --list           # 列出检测到的 IDE
  python ide_config.py kiro             # 配置 Kiro
  python ide_config.py kiro cursor       # 配置多个
  python ide_config.py --all            # 配置所有检测到的
  python ide_config.py --no-restart kiro # 只写配置不重启
        """
    )
    parser.add_argument("targets", nargs="*", help="要配置的 IDE（auto=当前运行的，VSCode优先 / kiro/cursor/claude/code/windsurf/zed）")
    parser.add_argument("--list", action="store_true", help="只列出检测到的 IDE")
    parser.add_argument("--all", action="store_true", help="配置所有检测到的 IDE（kiro 暂缓）")
    parser.add_argument("--no-restart", dest="no_restart", action="store_true", help="只写配置，不杀进程重启")
    parser.add_argument("--skip-restart", action="store_true", help="同 --no-restart")
    parser.add_argument("--no-verify", dest="no_verify", action="store_true",
                        help="配置完成后不自动发送 Hello 验证")
    parser.add_argument("--force-verify", action="store_true",
                        help="验证时忽略缓存强制重发一条动态消息")
    parser.add_argument("--verify-claude", action="store_true",
                        help="验证走真实 claude CLI（默认走 /v1/messages 同款协议）")
    args = parser.parse_args()

    no_restart = args.no_restart or args.skip_restart

    # Banner
    print()
    cprint("==========================================", "blue")
    cprint("       AI-Bridge IDE Configuration Tool  ", "blue")
    cprint("==========================================", "blue")
    print()

    # 检查 Bridge
    bridge_ok = check_bridge()
    if bridge_ok:
        cprint("✅ Bridge 服务正常  (127.0.0.1:18792)", "green")
    else:
        cprint("❌ Bridge 服务未运行（请先 python main.py）", "red")
        if not args.list:
            sys.exit(1)

    # 读取配置
    global DEFAULT_MODEL
    base_url = BRIDGE_BASE
    api_key = get_bridge_api_key()
    models = get_available_models() if bridge_ok else []
    if models:
        DEFAULT_MODEL = models[0]   # 默认模型 = 优先级最高的站点（zai）
    mcp_block = build_mcp_block(base_url, api_key)
    claude_code_block = build_claude_code_block(base_url, api_key, models)

    cprint(f"  API: {base_url}/v1", "dim")
    cprint(f"  可用模型: {', '.join(models[:8])}{'...' if len(models) > 8 else ''}", "dim")
    print()

    # 检测 IDE
    detected = find_running_ides()
    cprint(f"[*] Running IDEs detected: {', '.join(detected) if detected else 'none'}", "cyan")
    print()

    # --list 模式
    if args.list:
        print("Running IDEs:")
        if detected:
            for ide in ALL_IDES:
                if ide["key"] in detected:
                    exe = find_ide_exe(ide)
                    print(f"  [OK] {ide['name']:20s}  [{ide['key']}]  -> {exe or '?'}")
        else:
            print("  (none)")
        print()
        print("All supported IDEs:")
        for ide in ALL_IDES:
            exe = find_ide_exe(ide)
            status = "[OK]" if ide["key"] in detected else "[o] "
            print(f"  {status} {ide['name']:20s}  [{ide['key']}]")
        return

    # 确定目标
    if args.all:
        targets = detected if detected else [ide["key"] for ide in ALL_IDES]
        deferred = [t for t in targets if t in DEFERRED_IDES]
        if deferred:
            cprint(f"[i] 本阶段暂缓配置: {', '.join(deferred)}（先以 VSCode 验证为主）", "yellow")
            targets = [t for t in targets if t not in DEFERRED_IDES]
    elif "auto" in args.targets:
        # auto: 选当前正在运行的 IDE，VSCode 优先
        priority = ["code", "claude", "zed", "windsurf"]
        running = [k for k in priority if k in detected] + \
                  [k for k in detected if k not in priority and k not in DEFERRED_IDES]
        pick = running[0] if running else VERIFY_TARGET
        cprint(f"[i] auto -> {pick} (运行中的 IDE: {', '.join(detected) or 'none'}，VSCode 优先)", "cyan")
        targets = [pick if t == "auto" else t for t in args.targets]
    elif args.targets:
        targets = args.targets
    else:
        # 交互式菜单
        print("Select IDE(s) to configure (numbers separated by space, enter to confirm):")
        print()
        for i, ide in enumerate(ALL_IDES, 1):
            exe = find_ide_exe(ide)
            running = "[Y]" if ide["key"] in detected else "[o]"
            installed = "[pkg]" if exe else "    "
            note = "(running)" if ide["key"] in detected else ("(installed)" if exe else "(not found)")
            print(f"  {i}. {running}{installed} {ide['name']:20s} [{ide['key']}]  {note}")
        print()
        print("  a. All    q. Quit")
        print()
        try:
            choice = input("Enter number(s) or a: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "q":
            return
        if choice == "a":
            targets = [ide["key"] for ide in ALL_IDES]
        else:
            targets = []
            for part in choice.split():
                part = part.strip().rstrip(".")
                if part.isdigit() and 1 <= int(part) <= len(ALL_IDES):
                    targets.append(ALL_IDES[int(part)-1]["key"])

    if not targets:
        cprint("No IDE selected, exiting.", "yellow")
        return

    # 去重
    targets = list(dict.fromkeys(targets))

    print()
    cprint(f"[>] About to configure: {', '.join(targets)}", "bold")
    print()

    # 执行
    success = []
    for key in targets:
        ok = configure_ide(key, base_url, api_key, models, mcp_block, claude_code_block,
                          restart=not no_restart)
        if ok:
            success.append(key)

    # 汇总
    print()
    cprint("==========================================", "blue")
    if success:
        cprint(f"[OK] Configured: {', '.join(success)}", "green")
    else:
        cprint("[x] All configurations failed", "red")

    # ── 自动验证：向 Claude 动态输入 Hello，验证 VSCode 链路 ──
    verify_exit = 0
    if VERIFY_TARGET in targets or VERIFY_TARGET in success:
        print()
        cprint("[>] Auto verification: sending dynamic 'Hello' to Claude...", "cyan")
        try:
            from verify_hello import run_verification
            result = run_verification(force=args.force_verify, via_claude=args.verify_claude)
            if not result.get("ok"):
                verify_exit = 1
        except ImportError as e:
            cprint(f"[x] verify_hello 模块不可用: {e}", "red")
            verify_exit = 1
    else:
        cprint("[i] 跳过自动验证（仅对 code/VSCode 触发，可用 --verify-claude 等手动运行 python verify_hello.py）", "dim")

    print()
    cprint("Next steps:", "dim")
    cprint("  1. Open Claude Code / Continue in VSCode, pick model glm-5.3-flash", "dim")
    cprint("  2. Only latest 3 models exposed: glm-5.3-flash > glm-5.3 > glm-5.2", "dim")
    cprint(f"  3. Re-run verify manually: python verify_hello.py [--force|--claude]", "dim")

    sys.exit(verify_exit)


if __name__ == "__main__":
    main()
