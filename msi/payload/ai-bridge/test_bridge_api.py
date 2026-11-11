"""测试 Bridge API 连接已登录的站点"""
import requests
import json
import sys

BASE_URL = "http://127.0.0.1:18792"

def test_chat_completion(site_name, model_name="default", message="你好，请回复'测试成功'"):
    """测试单个站点的聊天功能"""
    url = f"{BASE_URL}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    data = {
        "model": model_name,
        "messages": [{"role": "user", "content": message}],
        "stream": False,  # 使用非流式便于测试
        "site": site_name
    }

    print(f"\n测试 {site_name}/{model_name}...")
    try:
        response = requests.post(url, json=data, headers=headers, timeout=60)
        if response.status_code == 200:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"  状态: 成功")
            print(f"  响应: {content[:200]}...")
            return True, content
        else:
            print(f"  状态: 失败 ({response.status_code})")
            print(f"  响应: {response.text[:200]}")
            return False, response.text
    except Exception as e:
        print(f"  状态: 异常 - {e}")
        return False, str(e)

def list_models():
    """列出所有可用模型"""
    try:
        response = requests.get(f"{BASE_URL}/v1/models", timeout=10)
        if response.status_code == 200:
            models = response.json()
            print("\n可用模型:")
            for m in models.get("data", []):
                print(f"  - {m['id']}")
            return models
        else:
            print(f"获取模型失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"连接失败: {e}")
        return None

def main():
    print("="*60)
    print("Bridge API 测试")
    print("="*60)

    # 首先检查服务是否运行
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"\n服务状态: {response.json()}")
    except Exception as e:
        print(f"\n无法连接到 Bridge 服务: {e}")
        print("请先启动 Bridge 服务: python -m uvicorn main:app --host 0.0.0.0 --port 7860")
        return

    # 列出模型
    list_models()

    # 测试站点
    sites_to_test = [
        ("zai", "default", "你好"),
        ("chatgpt", "default", "你好"),
        ("arena", "default", "Hello"),
        ("grok", "default", "Hello"),
        ("doubao", "default", "你好"),
        ("poe", "gpt-4o-mini", "Hello"),
        ("poe", "claude-3-5-sonnet", "Hello"),
    ]

    for site, model, msg in sites_to_test:
        success, content = test_chat_completion(site, model, msg)
        if success:
            print("  [OK]")
        else:
            print("  [FAILED]")
        print()

if __name__ == "__main__":
    main()
