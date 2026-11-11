AI 模型桥接 (AI-Bridge + Free-Gateway)
========================================
安装内容：
  AI模型桥接.exe   一键启动器（桌面/开始菜单快捷方式自动创建）
  ai-bridge\       桥接服务（OpenAI/Anthropic 兼容 API, 端口18792）
  free-gateway\    免费模型网关（OpenRouter 免费模型 + 本地 Ollama, 端口8787）

首次使用：
  1. 双击「AI模型桥接.exe」（或桌面快捷方式）
     → 若提示安装运行时，按提示完成（需联网，约5-10分钟）
  2. 管理页 http://127.0.0.1:18792/ 自动打开
  3. Zed/其他 IDE 接入配置见管理页「接入 IDE」卡片

注意事项：
  - ai-bridge\.env 内含 OpenRouter API Key（安装者本人使用需自行填入，
    到 https://openrouter.ai/keys 免费创建；不填也可用 auto 链的本地/网页兜底）
  - 本地无限制模型需先安装 Ollama 并拉取模型
  - 服务在后台运行；重启电脑后双击启动器即可恢复
