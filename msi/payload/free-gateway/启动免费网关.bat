@echo off
chcp 65001 >nul
title Free Gateway (port 8787)
echo ========================================
echo  Free Gateway - free local model gateway
echo  http://127.0.0.1:8787
echo ========================================
python "%~dp0gateway.py"
pause
