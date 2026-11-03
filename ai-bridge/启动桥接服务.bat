@echo off
chcp 65001 >nul
echo ========================================
echo  Z.ai GLM 模型桥接服务启动器
echo ========================================
echo.

REM 检查 Chrome 是否已在调试模式运行
netstat -an | findstr "127.0.0.1:9222" >nul
if %errorlevel%==0 (
    echo [OK] 检测到 Chrome 调试模式已运行
) else (
    echo [!] 启动 Chrome 调试模式...
    echo    请关闭所有 Chrome 窗口后运行此脚本
    echo    或手动启动 Chrome: 
    echo    chrome.exe --remote-debugging-port=9222
    echo.
    set /p choice=是否手动启动 Chrome? (Y/N):
    if /i "%choice%"=="Y" (
        start chrome --remote-debugging-port=9222
        echo 等待 Chrome 启动...
        timeout /t 3 /nobreak >nul
    )
)

echo.
echo 启动桥接服务...
python "%~dp0main.py"

pause
