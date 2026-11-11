@echo off
rem AI 模型桥接 - 运行时安装（内嵌 Python + 依赖 + Chromium）
setlocal
cd /d "%~dp0"
set "RT=%~dp0runtime"
set "PYEXE=%RT%\python.exe"

if exist "%PYEXE%" goto deps

echo [1/4] 下载内嵌 Python 3.13 运行时...
if not exist runtime.zip curl -L -o runtime.zip https://www.python.org/ftp/python/3.13.1/python-3.13.1-embed-amd64.zip
mkdir "%RT%" 2>nul
tar -xf runtime.zip -C "%RT%"
if errorlevel 1 goto fail
(echo python313.zip& echo Lib\site-packages& echo .) > "%RT%\python313._pth"
echo [2/4] 安装依赖包...
curl -L -o get-pip.py https://bootstrap.pypa.io/get-pip.py
"%PYEXE%" get-pip.py --no-warn-script-location
"%PYEXE%" -m pip install -r requirements-bridge.txt --no-warn-script-location
if errorlevel 1 goto fail
echo [3/4] 安装 Chromium（Playwright 浏览器）...
"%PYEXE%" -m playwright install chromium
if errorlevel 1 goto fail
echo [4/4] 完成！
del runtime.zip get-pip.py 2>nul
echo 即将启动管理页面...
start "" "%~dp0AI模型桥接.exe"
goto eof2

:fail
echo 安装失败，请检查网络后重试
pause

:eof2
endlocal
