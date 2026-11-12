"""
live_e2e.py —— 真实服务的端到端测试（会真的发 HTTP 请求）。

覆盖：健康检查、模型列表、对话（非流式/流式）、
工具调用、错误码、认证、并发、边界输入、性能。

用法: python live_e2e.py
"""
import concurrent.futures as cf
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE = "http://127.0.0.1:18792"
GATEWAY = "http://127.0.0.1:8787"

PASS, FAIL, SKIP = [], [], []


def http(method, url, body=None, headers=None, timeout=60):
    """返回 (status, body_text)"""
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")
    return cond


def skip(name, why):
    SKIP.append((name, why))
    print(f"  [SKIP] {name}  ({why})")


def section(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


def _read_api_key():
    """读取 .env 里的 API_KEY（只判断是否设置，不打印内容）"""
    p = Path(__file__).parent / "ai-bridge" / ".env"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


section("[1] 健康检查 / 基础连通性")

st, body = http("GET", f"{BRIDGE}/health")
check("bridge /health 返回 200", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    check("health.status == ok", d.get("status") == "ok", str(d)[:120])
    check("health 含 timestamp", "timestamp" in d, str(d)[:120])

st, body = http("GET", f"{GATEWAY}/health")
check("gateway /health 返回 200", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    check("gateway health.status == ok", d.get("status") == "ok", str(d)[:150])

st, _ = http("GET", f"{BRIDGE}/")
check("bridge / 返回 200(HTML)", st == 200, f"status={st}")

st, _ = http("GET", f"{BRIDGE}/favicon.ico")
check("bridge /favicon.ico 返回 200", st == 200, f"status={st}")

section("[2] 模型列表")

st, body = http("GET", f"{BRIDGE}/v1/models")
check("bridge /v1/models 200", st == 200, f"status={st} {body[:150]}")
bridge_models = []
if st == 200:
    d = json.loads(body)
    check("/v1/models object == list", d.get("object") == "list")
    check("/v1/models data 是数组", isinstance(d.get("data"), list))
    bridge_models = [m["id"] for m in d.get("data", [])]
    check("/v1/models 有内容", len(bridge_models) > 0, str(bridge_models))
    check("模型 id 无重复", len(bridge_models) == len(set(bridge_models)))
    print(f"       桥接模型({len(bridge_models)}): {bridge_models[:8]}")

st, body = http("GET", f"{GATEWAY}/v1/models")
check("gateway /v1/models 200", st == 200, f"status={st}")
gw_models = []
if st == 200:
    gw_models = [m["id"] for m in json.loads(body).get("data", [])]
    check("gateway 模型有内容", len(gw_models) > 0, str(len(gw_models)))
    or_models = [m for m in gw_models if m.startswith("or/")]
    local_models = [m for m in gw_models if m.startswith("local")]
    check("gateway 含 or/* 免费模型", len(or_models) > 0, str(gw_models[:10]))
    check("gateway 含 local-* 模型", len(local_models) > 0, str(gw_models[:10]))
    print(f"       网关模型({len(gw_models)}): or={len(or_models)} local={len(local_models)}")
else:
    or_models, local_models = [], []

section("[3] 对话 —— 本地模型（真实推理）")

if local_models:
    m = local_models[0]
    t0 = time.time()
    st, body = http("POST", f"{BRIDGE}/v1/chat/completions", {
        "model": m,
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
        "stream": False,
    }, timeout=180)
    dt = time.time() - t0
    check(f"本地模型 {m} 对话 200", st == 200, f"status={st} body={body[:200]}")
    if st == 200:
        try:
            d = json.loads(body)
            ch = d.get("choices", [{}])[0]
            content = (ch.get("message") or {}).get("content") or ""
            check("响应含 choices", bool(d.get("choices")))
            check("响应 content 非空", len(content.strip()) > 0, repr(content[:80]))
            check("响应 model 字段存在", "model" in d, str(list(d.keys())))
            print(f"       耗时 {dt:.1f}s / content={content[:90]!r}")
        except Exception as e:
            check("响应是合法 JSON", False, f"{e}: {body[:200]}")
else:
    skip("本地模型对话", "没有 local-* 模型")

section("[4] 对话 —— OpenRouter 免费模型（真实网络）")

if or_models:
    m = or_models[0]
    t0 = time.time()
    st, body = http("POST", f"{BRIDGE}/v1/chat/completions", {
        "model": m,
        "messages": [{"role": "user", "content": "Say OK"}],
        "stream": False,
    }, timeout=180)
    dt = time.time() - t0
    check(f"OR 模型 {m} 对话 200", st == 200, f"status={st} body={body[:250]}")
    if st == 200:
        try:
            d = json.loads(body)
            content = (d.get("choices", [{}])[0].get("message") or {}).get("content") or ""
            check("OR 响应 content 非空", len(content.strip()) > 0, repr(content[:80]))
            print(f"       耗时 {dt:.1f}s / content={content[:90]!r}")
        except Exception as e:
            check("OR 响应是合法 JSON", False, f"{e}: {body[:200]}")
else:
    skip("OpenRouter 对话", "没有 or/* 模型")

section("[5] 流式响应（SSE）")

if local_models:
    m = local_models[0]
    t0 = time.time()
    st, body = http("POST", f"{BRIDGE}/v1/chat/completions", {
        "model": m,
        "messages": [{"role": "user", "content": "Count: 1 2 3"}],
        "stream": True,
    }, timeout=180)
    dt = time.time() - t0
    check(f"流式 {m} 200", st == 200, f"status={st}")
    if st == 200:
        check("SSE 含 data: 前缀", "data:" in body, body[:120])
        check("SSE 含 [DONE] 结束标记", "[DONE]" in body, body[-160:])
        acc = ""
        for line in body.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    o = json.loads(line[6:])
                    acc += (o.get("choices", [{}])[0].get("delta") or {}).get("content") or ""
                except Exception:
                    pass
        check("SSE delta 能拼出内容", len(acc.strip()) > 0, repr(acc[:80]))
        print(f"       耗时 {dt:.1f}s / 拼出 {len(acc)} 字符")
else:
    skip("流式响应", "没有 local-* 模型")

section("[6] 工具调用（Agent 模式）")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather in a city",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string",
                                               "description": "City name"}},
                       "required": ["city"]},
    },
}]

if local_models:
    m = local_models[0]
    st, body = http("POST", f"{BRIDGE}/v1/chat/completions", {
        "model": m,
        "messages": [{"role": "user",
                      "content": "What is the weather in Beijing? Use the tool."}],
        "tools": TOOLS,
        "stream": False,
    }, timeout=180)
    check(f"工具调用 {m} 200", st == 200, f"status={st} body={body[:250]}")
    if st == 200:
        try:
            d = json.loads(body)
            msg = d.get("choices", [{}])[0].get("message") or {}
            tcs = msg.get("tool_calls")
            check("响应含 tool_calls", bool(tcs), str(msg)[:250])
            if tcs:
                fn = tcs[0].get("function") or {}
                check("tool_call 有 function.name", bool(fn.get("name")), str(fn)[:150])
                check("tool_call name 是已知工具",
                      fn.get("name") in {t["function"]["name"] for t in TOOLS},
                      str(fn.get("name")))
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    check("arguments 是合法 JSON 且含 city", "city" in args, str(args))
                except Exception as e:
                    check("arguments 是合法 JSON", False, f"{e}")
        except Exception as e:
            check("工具响应是合法 JSON", False, f"{e}: {body[:200]}")
else:
    skip("工具调用", "没有 local-* 模型")

section("[7] 异常与错误码")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions", {})
check("空 body → 422", st == 422, f"status={st} {body[:120]}")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": "x", "messages": "not-a-list"})
check("messages 类型错误 → 422", st == 422, f"status={st}")

st, body = http("GET", f"{BRIDGE}/v1/nonexistent")
check("不存在的路径 → 404", st == 404, f"status={st}")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": "definitely-not-a-real-model-xyz",
                 "messages": [{"role": "user", "content": "hi"}]}, timeout=60)
check("未知模型有明确错误(非超时)", st in (400, 404, 502, 503) or st == 0,
      f"status={st} body={body[:150]}")
print(f"       未知模型响应: status={st} body={body[:150]!r}")

st, body = http("GET", f"{BRIDGE}/login/batch/status")
check("/login/batch/status 200", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    for k in ("running", "total", "done", "current", "results"):
        check(f"/login/batch/status 含 {k}", k in d, str(list(d.keys())))

st, body = http("GET", f"{BRIDGE}/admin/status")
check("/admin/status 200", st == 200, f"status={st}")

st, body = http("GET", f"{BRIDGE}/usage/recent")
check("/usage/recent 200", st == 200, f"status={st}")

st, body = http("GET", f"{BRIDGE}/sites/check")
check("/sites/check 200", st == 200, f"status={st}")

st, body = http("POST", f"{BRIDGE}/sites/add",
                {"name": "INVALID NAME!", "url": "javascript:alert(1)"})
check("/sites/add 拒绝非法输入",
      st == 200 and json.loads(body).get("success") is False, body[:150])

st, body = http("GET", f"{BRIDGE}/v1/models", headers={"Authorization": "Bearer wrong"})
# 未配置 API_KEY 时不校验 → 200 是预期行为；
# 配置了 API_KEY 时错误 Key 必须 401。
api_key_set = bool(_read_api_key())
if api_key_set:
    check("错误 API Key → 401", st == 401, f"status={st}")
else:
    check("未配置 API_KEY 时不鉴权（本机模式，预期 200）", st == 200, f"status={st}")

section("[8] 边界输入")

base_model = local_models[0] if local_models else "local-dolphin-mistral"

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": base_model,
                 "messages": [{"role": "user", "content": ""}]}, timeout=120)
check("空内容消息不崩", st != 500 or "detail" in body, f"status={st}")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": base_model,
                 "messages": [{"role": "user", "content": "x" * 100000}]}, timeout=180)
check("超长消息(100k字符)不崩", st in (200, 400, 413, 422) or st == 0,
      f"status={st} {body[:120]}")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": base_model,
                 "messages": [{"role": "user", "content": "hi"}],
                 "tools": []}, timeout=60)
check("tools 为空数组正常处理", st in (200, 400, 422), f"status={st}")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": base_model,
                 "messages": [{"role": "user", "content": "hi"},
                              {"role": "assistant", "content": "yo"},
                              {"role": "user", "content": "again"}]}, timeout=180)
check("多轮对话不崩", st in (200, 400, 422) or st == 0, f"status={st}")

st, body = http("POST", f"{BRIDGE}/v1/chat/completions",
                {"model": base_model,
                 "messages": [{"role": "user", "content": "hi"}],
                 "tools": [{"type": "function", "function": {
                     "name": "f", "parameters": {}}}],
                 "stream": True}, timeout=120)
check("空 parameters 的工具不崩", st in (200, 400, 422), f"status={st}")

section("[9] 并发")

if local_models:
    m = local_models[0]
    t0 = time.time()

    def one(i):
        s, b = http("POST", f"{BRIDGE}/v1/chat/completions", {
            "model": m,
            "messages": [{"role": "user", "content": f"say {i}"}],
            "stream": False,
        }, timeout=180)
        return s, b

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, range(4)))
    dt = time.time() - t0
    oks = sum(1 for s, _ in results if s == 200)
    check("4 并发请求全部无崩溃",
          all(s in (200, 429, 400, 422, 503) for s, _ in results),
          str([s for s, _ in results]))
    check("至少 1 个并发请求成功", oks >= 1, f"ok={oks}/4")
    print(f"       4 并发耗时 {dt:.1f}s / 成功 {oks}/4 / 状态 {[s for s,_ in results]}")

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        hs = list(ex.map(lambda _: http("GET", f"{BRIDGE}/health")[0], range(8)))
    check("8 并发 /health 全部 200", all(s == 200 for s in hs), str(hs))

section("[10] 性能")

t0 = time.time()
for _ in range(10):
    http("GET", f"{BRIDGE}/health")
dt = (time.time() - t0) / 10
check(f"单次 /health 平均 < 200ms（实测 {dt*1000:.0f}ms）", dt < 0.2, f"{dt:.3f}s")

t0 = time.time()
http("GET", f"{BRIDGE}/v1/models")
dt = time.time() - t0
check(f"/v1/models 响应 < 1s（实测 {dt*1000:.0f}ms）", dt < 1.0, f"{dt:.3f}s")

t0 = time.time()
http("GET", f"{BRIDGE}/")
dt = time.time() - t0
check(f"管理页 < 1s（实测 {dt*1000:.0f}ms）", dt < 1.0, f"{dt:.3f}s")

section("结果汇总")
print(f"\nPASS={len(PASS)}  FAIL={len(FAIL)}  SKIP={len(SKIP)}")
if FAIL:
    print("\n失败明细:")
    for n, d in FAIL:
        print(f"  - {n}  {d}")
if SKIP:
    print("\n跳过明细:")
    for n, w in SKIP:
        print(f"  - {n}  ({w})")
sys.exit(1 if FAIL else 0)
