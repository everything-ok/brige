@echo off
chcp 65001 >nul
echo ========================================
echo  Chrome 调试模式启动器
echo ========================================
echo.
echo 正在启动 Chrome...
start chrome --remote-debugging-port=9222
echo.
echo Chrome 已启动，端口: 9222
echo.
echo 现在可以运行: 启动桥接服务.bat
pause
