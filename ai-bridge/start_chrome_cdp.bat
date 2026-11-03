@echo off
REM 以 CDP 调试端口启动系统 Chrome，复用已有 user-data-dir 登录态
REM 用法：双击或 cmd 运行 start_chrome_cdp.bat
REM 停止：关闭该 Chrome 窗口即可

set CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME_EXE%" set CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe

echo [*] Starting Chrome with remote debugging port 9222 ...
echo [*] Reuses your existing Chrome profile / login state.
echo [*] Keep this Chrome open while Bridge runs. Close it to stop debugging.
echo.

start "" "%CHROME_EXE%" --remote-debugging-port=9222 --remote-allow-origins=*

timeout /t 3 >nul
echo [*] Chrome started. Verify: http://127.0.0.1:9222/json/version
start "" http://127.0.0.1:9222/json/version
