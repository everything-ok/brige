RATE_LIMIT_KEYWORDS = ("rate", "limit", "频率", "限流", "too many", "429", "ratelimit", "throttle")

def _is_rate_limited(text):
    t = text.lower()
    return any(k in t for k in RATE_LIMIT_KEYWORDS)


def _strip_thinking_prefix(content):
    if not content:
        return content
    lines = content.split("\n")
    markers = ("深度思考", "思考过程", "正在思考", "思考中", "thinking", "reasoning")
    out, in_block = [], False
    for line in lines:
        stripped = line.strip()
        if not in_block and any(m.lower() in stripped.lower() for m in markers):
            in_block = True
            continue
        if in_block:
            if not stripped:
                continue
            out.append(line)
    return "\n".join(out) if out else content
