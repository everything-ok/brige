@echo off
rem Hidden launcher: runs bridge with pythonw, no console window stays
cd /d "%~dp0"
start "" /min "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe" main.py
exit
