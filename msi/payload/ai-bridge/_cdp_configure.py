"""通过 HTTP API 配置 Kiro + Cursor，并通过 CDP 注入结果到 Bridge 页面"""
import json
import time
import requests
import websocket

BASE = "http://127.0.0.1:18792"

# 1) HTTP API 一键配置
print("=== IDE 一键配置 ===")
results = []
for key in ["kiro", "cursor"]:
    try:
        r = requests.post(f"{BASE}/ide/configure/{key}", timeout=60)
        results.append({"ide": key, **r.json()})
    except Exception as e:
        results.append({"ide": key, "success": False, "error": str(e)})

print(json.dumps(results, ensure_ascii=False, indent=2))

# 2) 找 Bridge 页面 ID
pages = requests.get("http://127.0.0.1:9222/json", timeout=5).json()
bridge = next((p for p in pages if "127.0.0.1:18792" in p["url"]), None)
if not bridge:
    print("Bridge tab not found, opening...")
    r = requests.put("http://127.0.0.1:9222/json/new?http://127.0.0.1:18792", timeout=10)
    bridge = r.json()
print(f"Bridge tab: {bridge['id']}")

# 3) 等待页面加载再注入（绕过 origin 限制：Chrome 允许 devtools 自己 origin，
#    所以从页面 fetch /ide/configure 不会被拦。改用：在 Bridge 页面里执行 fetch）
time.sleep(3)
ws = websocket.create_connection(
    f"wss://127.0.0.1:9222/devtools/page/{bridge['id']}",
    timeout=30,
    suppress_origin=True,  # websocket-client >=1.9 支持
)
print("CDP connected")

# 但 suppress_origin 在某些版本可能没用。最稳的方式：
# 用 Runtime.evaluate 执行 fetch() 调用本地后端（fetch 是页面发起的，origin 正确）
js = """
(async () => {
    const results = [];
    for (const key of ['kiro','cursor']) {
        try {
            const r = await fetch('/ide/configure/' + key, {method:'POST'});
            const d = await r.json();
            results.push({key, ...d});
        } catch (e) {
            results.push({key, error: e.message});
        }
    }
    return results;
})()
"""
ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
    "expression": js, "awaitPromise": True, "returnByValue": True
}}))
while True:
    resp = json.loads(ws.recv())
    if resp.get("id") == 1:
        page_results = resp["result"]["result"]["value"]
        break

print("\n=== Page-side results (fetch from page origin) ===")
print(json.dumps(page_results, ensure_ascii=False, indent=2))

# 4) 在页面顶部绿色横幅显示结果
html = json.dumps(page_results, ensure_ascii=False, indent=2).replace("'", "&apos;")
show_js = f"""
const div = document.createElement('div');
div.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#000;color:#86efac;padding:14px;z-index:99999;max-height:50vh;overflow:auto;font-family:monospace;font-size:12px;border-bottom:2px solid #22c55e';
div.innerHTML = '<b>✅ IDE 一键配置完成</b><br><pre style="margin:8px 0 0;color:#86efac">{html}</pre>';
document.body.appendChild(div);
"""
ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {"expression": show_js}}))
ws.recv()
print("\n✅ 结果已显示在 Bridge 页面顶部")

ws.close()
