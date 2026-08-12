Set oShell = CreateObject("WScript.Shell")
oShell.CurrentDirectory = "c:\Users\SYMBOTIC\Documents\Symbotic_Teleops\autoclaim2\Automation"
oShell.Run "pythonw.exe main.py run --config config_2.json", 0, False
