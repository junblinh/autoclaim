Set oShell = CreateObject("WScript.Shell")
oShell.CurrentDirectory = "C:\Users\SYMBOTIC\Documents\Symbotic_Teleops\autoclaim2\Automation"
oShell.Run "pythonw.exe main.py run", 0, False
