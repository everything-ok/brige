@echo off
rem Gateway: runs in the calling console (VBS hides it), full log capture
cd /d "%~dp0"
if not exist logs mkdir logs
"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe" gateway.py >> logs\run.log 2>&1
