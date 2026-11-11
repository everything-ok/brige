@echo off
rem Hidden launcher: runs gateway with pythonw, no console window stays
cd /d "%~dp0"
start "" /min "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe" gateway.py
exit
