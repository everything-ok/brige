' AI-Bridge hidden launcher (no console window)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\project\桥接\ai-bridge"
sh.Run """C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe"" main.py", 0, False
