"""AI 模型桥接 - 桌面启动器
双击后：后台启动网关(8787)+桥接(18792)（无窗口），并打开管理页。
模型服务路径固定为 D:\project\ 下（与仓库部署一致）。
"""
import os
import subprocess
import sys
import time
import webbrowser

GATEWAY_DIR = r"D:\project\桥接\free-gateway"
BRIDGE_DIR = r"D:\project\桥接\ai-bridge"
GATEWAY_DIR_LEGACY = r"D:\project\free-gateway"
PYTHONW = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe"
ADMIN_URL = "http://127.0.0.1:18792/"


def port_listening(port: int) -> bool:
    try:
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def start_hidden(cwd: str, script: str):
    """pythonw 后台启动（完全无窗口）"""
    subprocess.Popen(
        [PYTHONW, script],
        cwd=cwd,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    started = []
    if not port_listening(8787):
        gw_dir = GATEWAY_DIR if os.path.isdir(GATEWAY_DIR) else GATEWAY_DIR_LEGACY
        start_hidden(gw_dir, "gateway.py")
        started.append("free-gateway")
    if not port_listening(18792):
        start_hidden(BRIDGE_DIR, "main.py")
        started.append("ai-bridge")

    if started:
        # 等服务就绪再开管理页
        for _ in range(30):
            time.sleep(1)
            if port_listening(18792) and port_listening(8787):
                break

    webbrowser.open(ADMIN_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
