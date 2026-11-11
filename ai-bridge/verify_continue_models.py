"""校验 ~/.continue/config.yaml 里的每个模型是否真能跑通。

对每个模型发一条最短的 chat 请求，记录状态/耗时/首句回复。
用途：写入配置后确认"模型可正确使用"，而不是只出现在下拉框里。
"""
import json
import os
import sys
import time

import httpx
import yaml

BRIDGE = os.getenv("BRIDGE_BASE", "http://127.0.0.1:18792")
CONFIG = os.path.join(os.path.expanduser("~"), ".continue", "config.yaml")

GROUPS = ("bridge", "openrouter", "local", "auto")


def load_models():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    out = []
    for m in cfg.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("title") or ""
        if not name.startswith(GROUPS):
            continue
        out.append((name, m.get("model"), m.get("apiBase") or f"{BRIDGE}/v1"))
    return out


def probe(name, model, api_base, timeout=240):
    url = api_base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "stream": False,
        "max_tokens": 24,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
    }
    t0 = time.time()
    try:
        r = httpx.post(url, json=body, timeout=timeout)
        dt = round(time.time() - t0, 1)
        if r.status_code != 200:
            return "FAIL", dt, f"HTTP {r.status_code} {r.text[:120]}"
        data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        if msg.get("tool_calls"):
            return "OK", dt, "tool_calls"
        if not text:
            return "EMPTY", dt, json.dumps(msg, ensure_ascii=False)[:120]
        if text.startswith("[ERROR"):
            return "FAIL", dt, text[:120]
        return "OK", dt, text[:60].replace("\n", " ")
    except httpx.TimeoutException:
        return "TIMEOUT", round(time.time() - t0, 1), "请求超时（模型冷启动或站点无响应）"
    except Exception as e:
        return "ERR", round(time.time() - t0, 1), f"{type(e).__name__}: {e}"[:120]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    models = load_models()
    if only:
        models = [m for m in models if m[0].startswith(only)]
    print(f"共 {len(models)} 个模型待测\n" + "=" * 78)
    results = {}
    for name, model, base in models:
        status, dt, detail = probe(name, model, base)
        results[name] = status
        mark = {"OK": "[OK]", "EMPTY": "[..]", "FAIL": "[x]", "ERR": "[!]",
            "TIMEOUT": "[~]"}[status]
        print(f"{mark} {name:42s} {dt:6.1f}s  {detail}")
    print("=" * 78)
    ok = sum(1 for v in results.values() if v == "OK")
    print(f"可用 {ok}/{len(results)}")
    bad = {k: v for k, v in results.items() if v != "OK"}
    if bad:
        print("不可用：")
        for k, v in bad.items():
            print(f"  {k} -> {v}")


if __name__ == "__main__":
    main()
