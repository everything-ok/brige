"""
桥接链路自动验证：动态生成「Hello」探测消息，验证 Claude Code -> 桥接 -> chat.z.ai(GLM) 全链路。

用法:
  python verify_hello.py                 # 默认验证（走 /v1/messages，即 Claude Code 同款协议）
  python verify_hello.py --force         # 忽略缓存强制重发
  python verify_hello.py --claude        # 用真实 claude CLI（headless）发送
  python verify_hello.py --json          # 输出 JSON 结果

限流规避策略:
  1. 发送前查询 /admin/status，zai 处于冷却期或分钟配额用满时等待/跳过，不硬发；
  2. 每次验证最多发一条消息；模型按 最新->次新 回退（glm-5.3-flash -> glm-5.3 -> glm-5.2），
     重试间隔 >= 站点最小间隔(8s)，绝不开多线程并发轰炸；
  3. 检测到需要登录立即停止（换模型无意义），提示人工处理；
  4. 成功结果写入 logs/last_verify.json，默认 10 分钟内重复运行直接复用结论，不再消耗配额。
"""
import os
import sys
import json
import time
import uuid
import random
import shutil
import argparse
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18792
BRIDGE_BASE = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

# 只选最新的前 3 个 GLM 模型（新 -> 旧）
LATEST_MODELS = ["glm-5.3-flash", "glm-5.3", "glm-5.2"]

VERIFY_MARKER = os.path.join(SCRIPT_DIR, "logs", "last_verify.json")
CACHE_TTL_SECONDS = 600          # 10 分钟内的成功结果直接复用
RETRY_GAP_SECONDS = 8            # 模型回退间隔，>= sites.yaml zai 的 min_interval_seconds
REQUEST_TIMEOUT = 420            # Agent 模式+网页打字模拟较慢，给足超时


def cprint(msg, color=None):
    """Windows GBK 安全输出"""
    colors = dict(green="\033[92m", red="\033[91m", yellow="\033[93m",
                  cyan="\033[96m", dim="\033[2m", bold="\033[1m", reset="\033[0m")
    c = colors.get(color, "")
    print(f"{c}{msg}{colors['reset']}")


def get_api_key():
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("API_KEY=") and not line.startswith("API_KEY=#"):
                return line.split("=", 1)[1].strip()
    return "any"


def http_request(method, path, body=None, headers=None, timeout=10):
    import http.client
    conn = http.client.HTTPConnection(BRIDGE_HOST, BRIDGE_PORT, timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload,
                     headers={"Content-Type": "application/json",
                              "Authorization": f"Bearer {get_api_key()}",
                              "Accept": "application/json",
                              **(headers or {})})
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", errors="replace")
        return resp.status, data
    finally:
        conn.close()


def gen_prompt():
    """动态生成 Hello 探测消息：随机 token 保证每次请求都是新内容，
    避免误读浏览器里残留的旧回复造成假阳性。返回 (prompt, token)"""
    token = uuid.uuid4().hex[:6].upper()
    prompt = (f"[bridge-ping-{token}] 请严格按照以下要求回复："
              f"只输出 Hello 这个单词，不要加标点、解释或任何其他字符。")
    return prompt, token


def contains_hello(text):
    """宽容判定：全角->半角、忽略所有空白后做大小写无关的子串匹配。
    这样「Ｈｅｌ ｌｏ」、「Hello!」、「你好 Hello」等都算命中"""
    import unicodedata
    norm = unicodedata.normalize("NFKC", text or "")
    norm = "".join(norm.split()).casefold()
    return "hello" in norm


# ── 限流闸门：冷却中 / 分钟配额满 则等待或放弃 ──────────────────
def wait_for_quota(max_wait=90):
    status_ok, _ = _bridge_alive()
    if not status_ok:
        raise RuntimeError(f"桥接服务不可用 ({BRIDGE_BASE})，请先运行 python main.py")

    deadline = time.time() + max_wait
    while True:
        _, body = http_request("GET", "/admin/status")
        zai = (json.loads(body).get("rate_limits") or {}).get("zai", {})
        cooldown = zai.get("cooldown_remaining") or 0
        minute_used = zai.get("requests_last_minute") or 0
        minute_limit = zai.get("limit_per_minute") or 6

        if cooldown <= 0 and minute_used < minute_limit:
            hour_used = zai.get("requests_last_hour") or 0
            # 刚用过站点时留一点随机间隔，避免贴着 min_interval 连击
            if minute_used > 0:
                gap = random.uniform(RETRY_GAP_SECONDS, RETRY_GAP_SECONDS * 2)
                cprint(f"  [~] 距上次请求较近，退避 {gap:.0f}s 避免触发反爬", "yellow")
                time.sleep(gap)
            elif hour_used > 0:
                time.sleep(random.uniform(1, 3))
            return {"minute_used": minute_used, "hour_used": hour_used}

        remaining = int(deadline - time.time())
        if remaining <= 0:
            raise RuntimeError(
                f"zai 限流中 (冷却 {cooldown:.0f}s, 本分钟 {minute_used}/{minute_limit})，"
                f"本次跳过验证以免雪上加霜，稍后重试")
        wait = min(cooldown if cooldown > 0 else 15, remaining, 30)
        cprint(f"  [~] 配额暂满/冷却中，等待 {wait:.0f}s...", "yellow")
        time.sleep(wait)


def _bridge_alive():
    try:
        status, body = http_request("GET", "/health", timeout=5)
        return status == 200, body
    except Exception as e:
        return False, str(e)


def read_cache():
    try:
        with open(VERIFY_MARKER, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("ok") and time.time() - data.get("time", 0) < CACHE_TTL_SECONDS:
            return data
    except Exception:
        pass
    return None


def write_cache(result):
    os.makedirs(os.path.dirname(VERIFY_MARKER), exist_ok=True)
    result["time"] = int(time.time())
    with open(VERIFY_MARKER, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


# ── 发送方式一：Anthropic /v1/messages（与 Claude Code 完全同款协议） ──
def send_via_anthropic(prompt, model):
    payload = {
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    start = time.time()
    status, body = http_request("POST", "/v1/messages", body=payload,
                                timeout=REQUEST_TIMEOUT)
    elapsed = time.time() - start
    if status == 200:
        data = json.loads(body)
        text = "".join(b.get("text", "") for b in data.get("content", []))
        hit = contains_hello(text)
        return {"ok": hit, "reply": text.strip(),
                "model_used": data.get("model", model), "elapsed": elapsed,
                "error": None if hit else f"回复未包含 Hello (原始回复: {text.strip()[:80]!r})"}
    return {"ok": False, "reply": body[:200], "model_used": model, "elapsed": elapsed,
            "error": f"HTTP {status}: {body[:200]}"}


# ── 发送方式二：真实 claude CLI headless ──────────────────────────
def find_claude_exe():
    exe = os.environ.get("CLAUDE_CODE_EXECPATH")
    if exe and os.path.exists(exe):
        return exe
    for name in ("claude", "claude.cmd", "claude.exe"):
        which = shutil.which(name)
        if which:
            return which
    return None


def send_via_claude(prompt, model):
    """用 claude -p 走真实 Claude Code 二进制，通过临时 settings 指向桥接，
    不改动用户现有的 ~/.claude/settings.json"""
    exe = find_claude_exe()
    if not exe:
        raise RuntimeError("未找到 claude 可执行文件，改用 --anthropic 方式")

    settings_path = None
    try:
        settings_path = os.path.join(tempfile.gettempdir(), f"bridge_verify_{uuid.uuid4().hex[:6]}.json")
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump({"env": {
                "ANTHROPIC_BASE_URL": BRIDGE_BASE,
                "ANTHROPIC_AUTH_TOKEN": get_api_key(),
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_SMALL_FAST_MODEL": model,
                "DISABLE_TELEMETRY": "1",
            }}, f)

        cmd = [exe, "-p", prompt, "--output-format", "text", "--settings", settings_path]
        start = time.time()
        proc_out = subprocess_run(cmd, timeout=300)
        elapsed = time.time() - start
        reply = proc_out.stdout.strip()
        hit = contains_hello(reply)
        if proc_out.returncode != 0 and not reply:
            return {"ok": False, "reply": "", "model_used": model, "elapsed": elapsed,
                    "error": f"claude exit {proc_out.returncode}: {proc_out.stderr.strip()[:200]}"}
        return {"ok": hit, "reply": reply[:200], "model_used": model, "elapsed": elapsed,
                "error": None if hit else f"回复未包含 Hello (原始回复: {reply[:80]!r})"}
    finally:
        if settings_path and os.path.exists(settings_path):
            os.remove(settings_path)


def subprocess_run(cmd, timeout):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# ── 主流程 ────────────────────────────────────────────────────────
def run_verification(force=False, via_claude=False, quiet=False):
    """执行一次验证，返回结果 dict：
    {ok, skipped, reply, model_used, elapsed, error}"""
    # 1. 缓存命中则不发请求（省配额，避免短时间重复验证触发限流）
    if not force:
        cached = read_cache()
        if cached:
            if not quiet:
                cprint(f"  [OK] {CACHE_TTL_SECONDS//60} 分钟内已验证成功，复用结果 "
                       f"(model={cached.get('model_used')}, reply={cached.get('reply')!r})", "green")
                cprint("       如需重新发送请加 --force", "dim")
            return dict(cached, skipped="cache")

    sender = send_via_claude if via_claude else send_via_anthropic
    errors = []

    # 2. 发送前过限流闸门
    quota = wait_for_quota()

    last_error_kind = None
    for i, model in enumerate(LATEST_MODELS):
        if i > 0:
            gap = random.uniform(RETRY_GAP_SECONDS, RETRY_GAP_SECONDS * 1.5)
            cprint(f"  [~] 回退下一模型前间隔 {gap:.0f}s（遵守站点最小请求间隔）", "yellow")
            time.sleep(gap)
            quota = wait_for_quota()
        if not quiet:
            cprint(f"  [>] 动态消息经 {('claude CLI' if via_claude else '/v1/messages')} -> {model} ...", "cyan")
        try:
            prompt, token = gen_prompt()
            res = sender(prompt, model)
        except Exception as e:
            res = {"ok": False, "reply": "", "model_used": model, "elapsed": 0, "error": str(e)}

        error_text = (res.get("error") or "").lower()
        if res["ok"]:
            res.update(quota=quota, token=token)
            write_cache(res)
            if not quiet:
                cprint(f"  [OK] 收到回复: {res['reply']!r}  (耗时 {res['elapsed']:.1f}s)", "green")
            return res

        errors.append(f"{model}: {res['error']}")
        error_raw = res.get("error") or ""
        if "login_required" in error_text or "登录" in error_raw:
            last_error_kind = "login"
        elif any(k in error_text for k in ("限制", "limit", "cooldown", "429")):
            last_error_kind = "quota"
        elif error_raw.startswith("回复未包含 Hello"):
            last_error_kind = "nomatch"
        else:
            last_error_kind = "other"

        # 登录/配额问题换模型或重发都无意义；已拿到回复只是没匹配上也停止，
        # 避免白白消耗剩余模型的配额
        if last_error_kind in ("login", "quota", "nomatch"):
            break

    result = {"ok": False, "skipped": None, "reply": res.get("reply", ""),
              "model_used": None, "elapsed": 0,
              "error": "; ".join(errors) or "未知错误"}
    write_cache({**result, "ok": False})
    if not quiet:
        hint = "请在 Chrome 里重新登录 chat.z.ai" if last_error_kind == "login" else \
               "站点处于限流/冷却，稍后再试 (python verify_hello.py --force)" if last_error_kind == "quota" else \
               "链路已通但回复没匹配上 Hello，见上方原始回复" if last_error_kind == "nomatch" else \
               "查看桥接服务窗口日志定位"
        cprint(f"  [x] 验证失败: {result['error']}", "red")
        cprint(f"      提示: {hint}", "yellow")
    return result


def main():
    parser = argparse.ArgumentParser(description="Bridge Hello 验证器")
    parser.add_argument("--force", action="store_true", help="忽略缓存强制重发")
    parser.add_argument("--claude", action="store_true",
                        help="用真实 claude CLI 发送（默认直接调 /v1/messages 同协议）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = parser.parse_args()

    print()
    cprint("=" * 46, "blue")
    cprint("   Bridge Hello Verification (zai/GLM)", "blue")
    cprint("=" * 46, "blue")

    result = run_verification(force=args.force, via_claude=args.claude)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print()
        if result["ok"]:
            cprint("[OK] 全链路验证通过", "green")
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
