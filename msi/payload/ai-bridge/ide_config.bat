@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Double-click (no args): auto-pick running IDE (VSCode first), configure it,
REM then auto-send a dynamic "Hello" verify through the bridge (Agent mode, no browser window).
REM With args: passthrough, e.g. ide_config.bat code --force-verify / ide_config.bat --list

if "%~1"=="" (
    python "%~dp0ide_config.py" auto
) else (
    python "%~dp0ide_config.py" %*
)

pause
