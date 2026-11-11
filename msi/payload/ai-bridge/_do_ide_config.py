"""独立脚本：1) 杀残留python 2) 调API配置IDE 3) 报告结果"""
import subprocess, time, sys

# 先确保 bridge 还在跑
import requests

BASE = "http://127.0.0.1:18792"
try:
    r = requests.get(f"{BASE}/health", timeout=5)
    print(f"✅ Bridge 状态: {r.status_code}")
except Exception as e:
    print(f"❌ Bridge 不通: {e}")
    print("请先启动 Bridge: python main.py")
    sys.exit(1)

# 检测 IDE
try:
    r = requests.get(f"{BASE}/ide/detect", timeout=5)
    data = r.json()
    detected = [d["key"] for d in data.get("detected", [])]
    print(f"检测到的 IDE: {detected}")
except Exception as e:
    print(f"检测 IDE 失败: {e}")
    detected = []

# 优先配置 kiro 和 cursor
targets = [t for t in ["kiro", "cursor"] if t in detected]
if not targets:
    targets = ["kiro", "cursor"]  # fallback: 尝试配置

print(f"\n{'='*50}")
print("开始配置以下 IDE:", targets)
print(f"{'='*50}\n")

results = []
for key in targets:
    print(f"[{key}] 正在写入配置...")
    try:
        r = requests.post(f"{BASE}/ide/configure/{key}", timeout=120)
        result = r.json()
        results.append(result)

        if result.get("success"):
            print(f"  ✅ 成功!")
            print(f"     写入文件: {result.get('written', [])}")
            print(f"     消息: {result.get('message')}")
            print(f"     自动重启: {'是' if result.get('restarted') else '否'}")
            if result.get("extra_configs"):
                for ec in result["extra_configs"]:
                    print(f"     额外: {ec.get('label')}")
        else:
            print(f"  ⚠️ 失败!")
            print(f"     错误: {result.get('errors')}")
            print(f"     消息: {result.get('message')}")
            print(f"     手动配置 JSON:\n{result.get('manual_config', '')}")
    except Exception as e:
        print(f"  ❌ 请求异常: {e}")
        results.append({"ide": key, "success": False, "error": str(e)})
    print()

print(f"{'='*50}")
print("配置完成汇总:")
for res in results:
    status = "✅" if res.get("success") else "❌"
    print(f"  {status} {res.get('ide')}")

print(f"\n💡 现在在 {BASE} 页面点击对应 IDE 卡片也能手动触发配置")
