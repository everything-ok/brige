"""测试 GLM-5.3-Flash 桥接"""
import requests
import json
import time
import sys

# 设置 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

def test_glm_bridge():
    base_url = "http://127.0.0.1:18792"

    print("=== 测试 GLM-5.3-Flash 桥接 ===\n")

    # 1. 检查健康状态
    print("1. 健康检查...")
    r = requests.get(f"{base_url}/health")
    print(f"   状态: {r.json()}\n")

    # 2. 检查站点状态
    print("2. 站点状态...")
    r = requests.get(f"{base_url}/login/status")
    status = r.json()
    zai_status = status.get("sites", {}).get("zai", {})
    print(f"   zai 登录状态: {zai_status}\n")

    # 3. 列出模型
    print("3. 可用模型...")
    r = requests.get(f"{base_url}/v1/models")
    models = r.json()
    glm_models = [m for m in models.get("data", []) if "glm" in m["id"].lower()]
    print(f"   GLM 模型: {[m['id'] for m in glm_models]}\n")

    # 4. 测试简单对话 (非流式)
    print("4. 测试对话 (GLM-5.3-Flash)...")
    payload = {
        "model": "zai/glm-5.3-flash",
        "messages": [{"role": "user", "content": "请用一句话介绍你自己"}],
        "stream": False
    }
    start = time.time()
    try:
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            timeout=60
        )
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"   ✓ 成功! (耗时 {elapsed:.1f}s)")
            print(f"   模型: {data.get('model', 'unknown')}")
            print(f"   回复: {content[:200]}...")
        else:
            print(f"   ✗ 失败: {r.status_code}")
            print(f"   {r.text[:500]}")
    except Exception as e:
        print(f"   ✗ 错误: {e}")

if __name__ == "__main__":
    test_glm_bridge()
