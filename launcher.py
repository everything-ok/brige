"""AI 模型桥接 - 启动器（安装版兼容）
路径解析规则：
  1) 若 exe 同目录存在 ai-bridge/ 与 free-gateway/ → 安装模式（MSI 安装后的目录结构）
  2) 否则回退到开发机路径 D:\project\ 桥接\...
启动行为：后台静默拉起网关(8787)+桥接(18792) → 等就绪 → 打开管理页
"""
import os
import subprocess
import sys
import time
import webbrowser

PYTHONW_CANDIDATES = [
    r"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe",
]


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    # 安装模式：exe 同目录存在两个服务子目录
    if os.path.isdir(os.path.join(base, "ai-bridge")) and \
       os.path.isdir(os.path.join(base, "free-gateway")):
        return base
    # 开发模式回退
    for cand in (r"D:\project\桥接", r"D:\project\free-gateway"):
        if os.path.isdir(os.path.join(cand, "ai-bridge")):
            return cand
    return base


def pythonw() -> str:
    root = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else sys.executable)
    candidates = [os.path.join(root, "pythonw.exe"), os.path.join(root, "Scripts", "pythonw.exe")]
    candidates += PYTHONW_CANDIDATES
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "pythonw"


def port_listening(port: int) -> bool:
    try:
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def fix_playwright_env():
    """IDE 沙箱终端会把 PLAYWRIGHT_BROWSERS_PATH 指向临时目录，导致 Chromium 找不到。

    在拉起子进程前改回系统真实路径（%LOCALAPPDATA%\\ms-playwright）。
    """
    cur = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    default = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
    sandboxed = "sandbox" in cur.lower()
    missing = bool(cur) and not os.path.isdir(cur)
    if (sandboxed or missing) and os.path.isdir(default):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = default


def start_hidden(cwd: str, script: str):
    subprocess.Popen(
        [pythonw(), script],
        cwd=cwd,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    base = base_dir()
    started = []
    fix_playwright_env()

    # 全新机器：无 Python 环境时先运行运行时安装脚本（可见窗口，需联网）
    if not port_listening(8787) and not port_listening(18792):
        try:
            import socket
        except Exception:
            pass
        if not (os.path.isfile(pythonw()) or os.path.isfile(
                os.path.join(base, "runtime", "python.exe"))):
            setup = os.path.join(base, "setup_runtime.bat")
            if os.path.isfile(setup):
                subprocess.Popen([setup], cwd=base)
                return 0

    if not port_listening(8787):
        start_hidden(os.path.join(base, "free-gateway"), "gateway.py")
        started.append("free-gateway")
    if not port_listening(18792):
        start_hidden(os.path.join(base, "ai-bridge"), "main.py")
        started.append("ai-bridge")

    if started:
        for _ in range(40):
            time.sleep(1)
            if port_listening(18792) and port_listening(8787):
                break

    webbrowser.open("http://127.0.0.1:18792/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
