"""验证各分组的工具调用（Agent 模式）是否真能产出 tool_calls。"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:18792/v1/chat/completions"
TOOLS = [{"type": "function", "function": {
    "name": "get_weather",
    "description": "Get weather for a city",
    "parameters": {"type": "object",
                   "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]


def check(model, timeout=300):
    body = {"model": model, "stream": False, "max_tokens": 300,
            "messages": [{"role": "user",
                          "content": "What is the weather in Beijing right now? "
                                     "Use the get_weather tool."}],
            "tools": TOOLS}
    try:
        r = httpx.post(BASE, json=body, timeout=timeout)
        if r.status_code != 200:
            return "FAIL", f"HTTP {r.status_code} {r.text[:140]}"
        d = r.json()
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls")
        if tcs:
            fn = (tcs[0].get("function") or {})
            return "OK", f"{fn.get('name')}({fn.get('arguments')})"
        text = (msg.get("content") or "").strip()
        if text.startswith("[ERROR"):
            return "FAIL", text[:140]
        return "NOTOOL", (text[:90].replace("\n", " ") or "(空)")
    except httpx.TimeoutException:
        return "TIMEOUT", "超时"
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"[:140]


if __name__ == "__main__":
    models = sys.argv[1:] or ["or/liquid/lfm-3-1.7b:free"]
    for m in models:
        st, detail = check(m)
        print(f"[{st:6s}] {m:42s} {detail}", flush=True)
