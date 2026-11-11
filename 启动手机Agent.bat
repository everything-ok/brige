@echo off
chcp 65001 >nul
rem 一键运行：让 mobilerun 用本地 free-gateway(8787) 作为 OpenAI 兼容后端操控手机
rem 说明：DeepSeek 适配器会启用原生 tool calling，mobilerun 的 fast_agent 才能正确解析工具调用

set "MR=%USERPROFILE%\.local\bin\mobilerun.exe"
set "DEEPSEEK_API_KEY=local-gateway"
set "PYTHONUTF8=1"

if not exist "%MR%" (
  echo [错误] 未找到 mobilerun，请先执行: py -m pip install --user uv && uv tool install mobilerun
  pause
  exit /b 1
)

set "TASK=%~1"
if "%TASK%"=="" set "TASK=打开设置并查看 Android 版本"

echo 任务: %TASK%
"%MR%" run "%TASK%" ^
  -p DeepSeek ^
  -m "or/cohere/north-mini-code:free" ^
  --api_base "http://127.0.0.1:8787/v1" ^
  --stream

pause
