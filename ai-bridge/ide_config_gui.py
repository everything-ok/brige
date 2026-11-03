"""
AI-Bridge IDE 一键配置工具
- 自动检测正在运行的 AI IDE
- 一键配置 + 重启
- 支持 Kiro / Cursor / Claude Desktop / VSCode / Windsurf / Zed
"""
import os, sys, json, time, subprocess, platform, threading, socket, http.client
import tkinter as tk
from tkinter import ttk, messagebox

# ── 基础路径 ────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
SYS = platform.system()
HOME = os.path.expanduser("~")

# ── Bridge 配置 ──────────────────────────────────────────────
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18792
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

# ── IDE 定义 ────────────────────────────────────────────────
ALL_IDES = [
    {
        "key": "kiro", "name": "Kiro", "icon": "K",
        "exe_names": ["kiro"],
        "win_paths": [
            r"D:\Kiro\Kiro.exe",
            os.path.join(HOME, "AppData", "Local", "Programs", "Kiro", "Kiro.exe"),
        ],
        "config_file": os.path.join(HOME, ".kiro", "settings", "mcp.json"),
        "type": "mcp",
    },
    {
        "key": "cursor", "name": "Cursor", "icon": "C",
        "exe_names": ["cursor"],
        "win_paths": [
            os.path.join(HOME, "AppData", "Local", "Programs", "cursor", "Cursor.exe"),
            os.path.join(HOME, "AppData", "Local", "Programs", "Cursor", "Cursor.exe"),
        ],
        "config_file": os.path.join(HOME, ".cursor", "mcp.json"),
        "type": "mcp",
    },
    {
        "key": "claude", "name": "Claude Desktop", "icon": "A",
        "exe_names": ["claude"],
        "win_paths": [
            os.path.join(HOME, "AppData", "Local", "Programs", "claude", "Claude.exe"),
            os.path.join(HOME, "AppData", "Roaming", "npm", "claude.cmd"),
        ],
        "config_file": os.path.join(HOME, ".claude", "mcp.json"),
        "type": "mcp",
    },
    {
        "key": "code", "name": "VSCode", "icon": "V",
        "exe_names": ["code"],
        "win_paths": [
            os.path.join(HOME, "AppData", "Local", "Programs", "Microsoft VS Code", "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "config_file": os.path.join(HOME, "AppData", "Roaming", "Code", "User", "settings.json"),
        "type": "vscode",
    },
    {
        "key": "windsurf", "name": "Windsurf", "icon": "W",
        "exe_names": ["windsurf"],
        "win_paths": [
            os.path.join(HOME, "AppData", "Local", "Programs", "windsurf", "Windsurf.exe"),
        ],
        "config_file": os.path.join(HOME, ".codeium", "windsurf", "mcp_config.json"),
        "type": "mcp",
    },
    {
        "key": "zed", "name": "Zed", "icon": "Z",
        "exe_names": ["zed"],
        "win_paths": [
            os.path.join(HOME, "AppData", "Local", "Programs", "Zed", "bin", "Zed.exe"),
            os.path.join(HOME, "AppData", "Local", "Programs", "Zed", "Zed.exe"),
            r"D:\Zed\Zed.exe",
            r"D:\Zed\bin\Zed.exe",
        ],
        "config_file": os.path.join(HOME, "AppData", "Roaming", "Zed", "settings.json"),
        "type": "zed",
    },
]

# ── 工具函数 ────────────────────────────────────────────────
def http_get(path, timeout=5):
    conn = http.client.HTTPConnection(BRIDGE_HOST, BRIDGE_PORT, timeout=timeout)
    conn.request("GET", path, headers={"Accept": "application/json"})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body

def http_post(path, body="", timeout=130):
    conn = http.client.HTTPConnection(BRIDGE_HOST, BRIDGE_PORT, timeout=timeout)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    conn.request("POST", path, body=body.encode() if body else None, headers=headers)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body

def check_bridge():
    """检查 Bridge 服务是否在跑"""
    try:
        status, _ = http_get("/health")
        return status == 200
    except:
        return False

def find_ide_exe(ide):
    """从运行进程或已知路径找 IDE 可执行文件"""
    # 先从运行进程找
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

    # 从已知路径找
    for path in ide["win_paths"]:
        if os.path.exists(path):
            return path
    return None

def find_running_ides():
    """检测所有正在运行的 IDE"""
    found = []
    try:
        import psutil
        for proc in psutil.process_iter(["name", "exe"]):
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
        # 没 psutil，用 subprocess
        try:
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"], text=True, timeout=5
            )
            for line in out.splitlines():
                pname = line.split('"')[1].lower().replace(".exe", "").strip() if '"' in line else line.split()[0].lower().replace(".exe", "").strip()
                for ide in ALL_IDES:
                    if ide["key"] in found:
                        continue
                    for kw in ide["exe_names"]:
                        if pname == kw or pname.startswith(kw):
                            found.append(ide["key"])
                            break
        except Exception:
            pass
    return found

def detect_ide_at_path(ide):
    """检测 IDE 是否已安装（配置文件是否存在）"""
    cfg = ide["config_file"]
    # Claude Desktop 特殊路径检查
    if ide["key"] == "claude":
        alt = os.path.join(HOME, ".claude", "mcp.json")
        return os.path.exists(cfg) or os.path.exists(alt)
    return os.path.exists(cfg)

def get_bridge_api_key():
    """从 .env 读 API_KEY"""
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("API_KEY=") and not line.startswith("API_KEY=#"):
                return line.split("=", 1)[1].strip()
    return "any"

def build_mcp_block(base_url, api_key):
    return {
        "command": "npx",
        "args": [
            "-y", "@modelcontextprotocol/server-openai-compatible",
            "--base-url", f"{base_url}/v1",
            "--api-key", api_key,
        ],
        "disabled": False,
        "autoApprove": ["chat"],
    }

def build_claude_code_block(base_url, api_key, models):
    return {
        "type": "openai",
        "baseURL": f"{base_url}/v1",
        "apiKey": api_key,
        "name": "Bridge",
        "models": models,
    }

def write_mcp_config(ide, mcp_block, claude_code_block, base_url, api_key):
    """写 MCP 配置文件"""
    path = ide["config_file"]
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    if ide["type"] == "mcp":
        existing.setdefault("mcpServers", {})["ai-bridge"] = mcp_block
        # Claude Code 额外配置
        if ide["key"] in ("kiro", "code"):
            claude_json = os.path.join(HOME, ".claude.json")
            os.makedirs(os.path.dirname(claude_json), exist_ok=True)
            cc_existing = {}
            if os.path.exists(claude_json):
                try:
                    with open(claude_json, "r", encoding="utf-8") as f:
                        cc_existing = json.load(f)
                except Exception:
                    cc_existing = {}
            providers = [p for p in cc_existing.get("customApiKeyConfig", []) if p.get("name") != "Bridge"]
            providers.append(claude_code_block)
            cc_existing["customApiKeyConfig"] = providers
            with open(claude_json, "w", encoding="utf-8") as f:
                json.dump(cc_existing, f, indent=2, ensure_ascii=False)

    elif ide["type"] == "vscode":
        models_list = [m for m in existing.get("continue.customModels", []) if m.get("title") != "Bridge"]
        models_list.append({
            "title": "Bridge", "provider": "openai", "model": "gpt-4o",
            "apiBase": f"{base_url}/v1", "apiKey": api_key,
        })
        existing["continue.customModels"] = models_list

    elif ide["type"] == "zed":
        existing.pop("disable_ai", None)
        existing["restore_on_startup"] = "last_session"
        existing["agent"] = {"default_model": {"provider": "bridge", "model": "glm-5.2"}}
        existing["assistant"] = {
            "version": "2",
            "default_model": {"provider": "bridge", "model": "glm-5.2"},
        }
        existing.setdefault("language_models", {}).update({
            "openai-compatible": {
                "bridge": {
                    "api_url": f"{base_url}/v1",
                    "api_key": api_key,
                    "available_models": [
                        {"name": m, "display_name": m, "max_tokens": 8192,
                         "supports_tools": False, "supports_images": False}
                        for m in ["glm-4v", "glm-4-flash", "glm-4-plus", "glm-4-alltools",
                                  "glm-4.1", "glm-4.7", "glm-5", "glm-5.2"]
                    ],
                }
            }
        })

    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    return path

def kill_ide(ide):
    """杀掉 IDE 进程"""
    try:
        import psutil
        killed = []
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info["name"] or "").lower().replace(".exe", "").strip()
                for kw in ide["exe_names"]:
                    if pname == kw or pname.startswith(kw):
                        proc.terminate()
                        killed.append(pname)
            except Exception:
                continue
        return killed
    except ImportError:
        killed = []
        for kw in ide["exe_names"]:
            try:
                subprocess.run(["taskkill", "/F", "/IM", f"{kw}.exe"],
                             capture_output=True, timeout=10)
                killed.append(kw)
            except Exception:
                pass
        return killed

def start_ide(ide):
    """启动 IDE"""
    exe = find_ide_exe(ide)
    if not exe:
        return None, "未找到可执行文件"
    try:
        if SYS == "Windows":
            subprocess.Popen([exe], detached=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen([exe], start_new_session=True)
        return exe, None
    except Exception as e:
        return None, str(e)

def set_env_var(key, value):
    """设置用户环境变量（Windows）"""
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

def get_available_models():
    """从 Bridge 获取可用模型列表"""
    try:
        _, body = http_get("/v1/models")
        data = json.loads(body)
        return [m["id"] for m in data.get("data", [])]
    except Exception:
        return ["glm-4v", "glm-4-flash", "glm-4-plus", "glm-4-alltools",
                "glm-4.1", "glm-4.7", "glm-5", "glm-5.2"]

# ── GUI ──────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI-Bridge IDE 配置")
        self.root.geometry("700x520")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e2e")

        self.ide_vars = {}
        self.status_text = tk.StringVar(value="正在检测...")
        self.running = False
        self.detected = []
        self.bridge_ok = False

        self._build_ui()
        threading.Thread(target=self._init_async, daemon=True).start()

    def _build_ui(self):
        style = {
            "bg": "#1e1e2e", "fg": "#cdd6f4",
            "selectcolor": "#313244", "activebackground": "#45475a",
        }

        def label(text, size=11, bold=False, fg="#cdd6f4", bg="#1e1e2e"):
            l = tk.Label(self.root, text=text, font=("Segoe UI", size, "bold" if bold else "normal"),
                        fg=fg, bg=bg)
            return l

        def btn(text, cmd, bg="#89b4fa", fg="#1e1e2e"):
            b = tk.Button(self.root, text=text, command=cmd, font=("Segoe UI", 10, "bold"),
                         bg=bg, fg=fg, relief="flat", cursor="hand2",
                         activebackground="#a6adc8", padx=15, pady=6)
            b.pack_configure(padx=4)
            return b

        # 标题
        tk.Label(self.root, text="AI-Bridge", font=("Segoe UI", 18, "bold"),
                fg="#89b4fa", bg="#1e1e2e").pack(pady=(18, 2))
        tk.Label(self.root, text="IDE MCP 一键配置", font=("Segoe UI", 11),
                fg="#6c7086", bg="#1e1e2e").pack(pady=(0, 12))

        # Bridge 状态
        frame_status = tk.Frame(self.root, bg="#1e1e2e")
        frame_status.pack(pady=(0, 12))
        tk.Label(frame_status, text="Bridge: ", font=("Segoe UI", 10),
                fg="#6c7086", bg="#1e1e2e").pack(side="left")
        self.lbl_bridge = tk.Label(frame_status, text="检测中...", font=("Segoe UI", 10, "bold"),
                                   fg="#f9e2af", bg="#1e1e2e")
        self.lbl_bridge.pack(side="left")

        # 模型信息
        self.lbl_models = tk.Label(self.root, text="", font=("Segoe UI", 9),
                                   fg="#6c7086", bg="#1e1e2e", wraplength=600)
        self.lbl_models.pack(pady=(0, 10))

        # IDE 列表
        frame_ide = tk.Frame(self.root, bg="#181825", relief="solid", bd=1)
        frame_ide.pack(padx=20, pady=(0, 10), fill="both", expand=True)

        tk.Label(frame_ide, text="选择要配置的 IDE（检测到运行中的会高亮）",
                font=("Segoe UI", 10), fg="#6c7086", bg="#181825").pack(pady=(10, 6))

        self.ide_frames = {}
        for ide in ALL_IDES:
            ff = tk.Frame(frame_ide, bg="#181825", relief="flat", padx=10, pady=4)
            ff.pack(fill="x", padx=8, pady=2)

            var = tk.BooleanVar(value=False)
            self.ide_vars[ide["key"]] = var

            cb = tk.Checkbutton(ff, text=f"  {ide['name']}  ({', '.join(ide['exe_names'])})",
                                variable=var, font=("Segoe UI", 10),
                                fg="#cdd6f4", bg="#181825", selectcolor="#313244",
                                activebackground="#181825", anchor="w",
                                command=lambda k=ide["key"]: self._on_toggle(k))
            cb.pack(side="left", fill="x", expand=True)

            self.ide_frames[ide["key"]] = {"frame": ff, "cb": cb, "ide": ide}

        # 按钮行
        frame_btns = tk.Frame(self.root, bg="#1e1e2e")
        frame_btns.pack(pady=10)

        self.btn_config = btn("配置 + 重启", self._do_configure, bg="#a6e3a1")
        self.btn_config.pack(side="left", padx=6)
        btn("全选", self._select_all, bg="#89b4fa").pack(side="left", padx=6)
        btn("全不选", self._deselect_all, bg="#fab387").pack(side="left", padx=6)

        # 状态输出
        frame_out = tk.Frame(self.root, bg="#11111b", relief="solid", bd=1, padx=10, pady=6)
        frame_out.pack(padx=20, pady=(0, 15), fill="both", expand=True)
        self.txt_out = tk.Text(frame_out, font=("Cascadia Code", 9), bg="#11111b",
                              fg="#cdd6f4", relief="flat", state="disabled",
                              height=6, wrap="word")
        self.txt_out.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(frame_out, command=self.txt_out.yview)
        scroll.pack(side="right", fill="y")
        self.txt_out.config(yscrollcommand=scroll.set)

        self._update_buttons()

    def _init_async(self):
        time.sleep(0.3)
        # 检测 Bridge
        self.bridge_ok = check_bridge()
        self._safe_call(self._update_bridge_status)

        # 获取模型列表
        models = get_available_models()
        self._safe_call(lambda: self.lbl_models.config(
            text=f"可用模型: {', '.join(models[:10])}{'...' if len(models) > 10 else ''}"))

        # 检测运行中的 IDE
        self.detected = find_running_ides()
        self._safe_call(self._update_ide_frames)

        self._safe_call(lambda: self.status_text.set("检测完成，请选择要配置的 IDE"))

    def _safe_call(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _update_bridge_status(self):
        if self.bridge_ok:
            self.lbl_bridge.config(text="✅ 运行中 (127.0.0.1:18792)", fg="#a6e3a1")
        else:
            self.lbl_bridge.config(text="❌ 未运行（请先 python main.py）", fg="#f38ba8")

    def _update_ide_frames(self):
        for key, data in self.ide_frames.items():
            ff = data["frame"]
            ide = data["ide"]
            if key in self.detected:
                ff.config(bg="#1e3a2f", relief="solid", bd=1)
                data["cb"].config(fg="#a6e3a1", bg="#1e3a2f", selectcolor="#2e4a3e")
                # 自动勾选检测到的
                self.ide_vars[key].set(True)
            else:
                ff.config(bg="#181825", relief="flat")
                data["cb"].config(fg="#cdd6f4", bg="#181825", selectcolor="#313244")

    def _update_buttons(self):
        any_selected = any(v.get() for v in self.ide_vars.values())
        state = "normal" if (self.bridge_ok and any_selected and not self.running) else "disabled"
        self.btn_config.config(state=state)

    def _on_toggle(self, key):
        self._update_buttons()

    def _select_all(self):
        for v in self.ide_vars.values():
            v.set(True)
        self._update_buttons()

    def _deselect_all(self):
        for v in self.ide_vars.values():
            v.set(False)
        self._update_buttons()

    def _log(self, msg):
        def _inner():
            self.txt_out.config(state="normal")
            self.txt_out.insert("end", msg + "\n")
            self.txt_out.see("end")
            self.txt_out.config(state="disabled")
        self.root.after(0, _inner)

    def _do_configure(self):
        selected = [k for k, v in self.ide_vars.items() if v.get()]
        if not selected:
            return

        self.running = True
        self.btn_config.config(state="disabled", text="配置中...")
        threading.Thread(target=self._configure_thread, args=(selected,), daemon=True).start()

    def _configure_thread(self, selected):
        base_url = BRIDGE_BASE
        api_key = get_bridge_api_key()
        models = get_available_models()
        mcp_block = build_mcp_block(base_url, api_key)
        claude_code_block = build_claude_code_block(base_url, api_key, models)

        self._log("=" * 50)
        self._log(f"开始配置: {', '.join(selected)}")
        self._log(f"API: {base_url}/v1  Key: {api_key[:8]}...")
        self._log("=" * 50)

        # 先杀掉 IDE
        for key in selected:
            ide = next(i for i in ALL_IDES if i["key"] == key)
            killed = kill_ide(ide)
            if killed:
                self._log(f"✅ 已终止 {ide['name']}: {killed}")
            else:
                self._log(f"⚠️  未找到运行中的 {ide['name']} 进程")
            time.sleep(0.5)

        # 写配置
        for key in selected:
            ide = next(i for i in ALL_IDES if i["key"] == key)
            try:
                path = write_mcp_config(ide, mcp_block, claude_code_block, base_url, api_key)
                self._log(f"✅ 写入配置 → {path}")

                # Claude Code 额外写入
                if key in ("kiro", "code"):
                    cc_path = os.path.join(HOME, ".claude.json")
                    self._log(f"✅ 写入 Claude Code 配置 → {cc_path}")

            except Exception as e:
                self._log(f"❌ 写入 {ide['name']} 配置失败: {e}")

        # Zed 需要环境变量
        if "zed" in selected:
            set_env_var("BRIDGE_API_KEY", api_key)
            set_env_var("BRIDGE_BASE_URL", f"{base_url}/v1")
            self._log("✅ 设置环境变量 BRIDGE_API_KEY / BRIDGE_BASE_URL")

        self._log("")
        self._log("▶ 正在启动 IDE...")

        # 重新启动
        started_any = False
        for key in selected:
            ide = next(i for i in ALL_IDES if i["key"] == key)
            exe, err = start_ide(ide)
            if exe:
                self._log(f"✅ 已启动 {ide['name']}: {exe}")
                started_any = True
            else:
                self._log(f"⚠️  启动 {ide['name']} 失败: {err}")

        self._log("")
        self._log("=" * 50)
        self._log("🎉 配置完成！")
        self._log("")
        self._log("📌 下一步:")
        self._log("  1. 在 IDE 中打开 MCP 设置，确认 ai-bridge 已连接")
        self._log("  2. 发送消息即可自动路由到 Z.ai / ChatGPT / DeepSeek 等模型")
        self._log("  3. 模型选择请在 Bridge 页面 http://127.0.0.1:18792 设置")

        def _finish():
            self.running = False
            self.btn_config.config(state="normal", text="配置 + 重启")
        self.root.after(0, _finish)

    def run(self):
        self.root.mainloop()

# ── 入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.run()
