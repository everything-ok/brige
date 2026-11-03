"""
Bridge Chat CLI - 通过 Bridge API 与 Z.ai 模型对话

Usage:
  python bridge_chat.py                            # 交互模式
  python bridge_chat.py --prompt "你好"            # 单次对话
  python bridge_chat.py --prompt "你好" --model glm-5.3
  python bridge_chat.py --prompt "你好" --no-stream
  python bridge_chat.py --test                     # 测试链路（不消耗积分）
  python bridge_chat.py --status                   # 查看限流状态
  python bridge_chat.py --models                   # 列出可用模型
"""
import sys, os, asyncio, json, argparse, time, threading, re

try:
    import requests
except ImportError:
    os.system(sys.executable + " -m pip install requests -q")
    import requests

_loop_lock = threading.Lock()
_main_loop = None

def _get_loop():
    global _main_loop
    with _loop_lock:
        if _main_loop is None:
            def _run():
                global _main_loop
                _main_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_main_loop)
                _main_loop.run_forever()
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            time.sleep(0.3)
    return _main_loop

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18792

def _decode(raw):
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")

def http_get(path, timeout=5):
    import http.client
    conn = http.client.HTTPConnection(BRIDGE_HOST, BRIDGE_PORT, timeout=timeout)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, _decode(resp.read())

TOP3_MODELS = ["zai/glm-5.3-flash", "zai/glm-5.3", "zai/glm-5.2"]

def list_models():
    try:
        status, body = http_get("/v1/models")
        if status == 200:
            data = json.loads(body)
            return [m["id"] for m in data.get("data", []) if "zai/" in m["id"]]
    except:
        pass
    return TOP3_MODELS

RATE_LIMIT_KEYWORDS = ("rate", "limit", "频率", "限流", "too many", "429", "ratelimit", "throttle")

def _is_rate_limited(text):
    return any(k in text.lower() for k in RATE_LIMIT_KEYWORDS)

def _strip_thinking_prefix(content):
    if not content:
        return content
    lines = content.split(chr(10))
    markers = ('深度思考', '思考过程', 'thinking', 'reasoning')
    out, in_block = [], False
    for line in lines:
        s = line.strip()
        if not in_block and any(m.lower() in s.lower() for m in markers):
            in_block = True; continue
        if in_block and s:
            out.append(line)
    return chr(10).join(out) if out else content
