@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Start VSCode with the bridge Claude profile (CLAUDE_CONFIG_DIR=%USERPROFILE%\.claude-bridge).
REM Scope: this VSCode instance only. Kiro and other IDEs are NOT affected.
REM Note: close ALL VSCode windows first, otherwise Windows reuses the running
REM       instance and the injected environment is ignored.

python -c "import ide_config; ide=next(i for i in ide_config.ALL_IDES if i['key']=='code'); exe,err=ide_config.start_ide(ide, extra_env=ide_config.build_vscode_bridge_env(ide_config.get_bridge_api_key())); print(('ERR: '+str(err)) if err else ('OK: '+str(exe)))"

echo.
echo If a VSCode window was already open, close all VSCode windows and run this again.
pause
