' Free Gateway hidden launcher (no console window)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\project\free-gateway"
sh.Run """C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe"" gateway.py", 0, False
