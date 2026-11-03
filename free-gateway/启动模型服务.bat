@echo off
rem AI model bridge launcher: starts gateway+bridge if not running, opens admin page
rem %~dp0 = this file's folder (free-gateway); bridge folder is referenced by absolute path
set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
set "BRIDGE_DIR=D:\project\ге╫с\ai-bridge"

netstat -ano | findstr ":8787" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo [start] free-gateway :8787
  cd /d "%~dp0"
  start "free-gateway" /min "%PY%" gateway.py
)

netstat -ano | findstr ":18792" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo [start] ai-bridge :18792
  cd /d "%BRIDGE_DIR%"
  start "ai-bridge" /min "%PY%" main.py
)

rem wait for services, then open admin page
timeout /t 6 /nobreak >nul
start "" http://127.0.0.1:18792/
exit
