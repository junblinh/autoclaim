Set oShell = CreateObject("WScript.Shell")
monitor = InputBox("Which monitor do you want to calibrate?" & vbCrLf & vbCrLf & "On your system, the monitors are usually numbered 1, 2, 3, or 4." & vbCrLf & "If the window appears on the wrong screen, close the terminal window, run this again, and try a different number.", "Select Monitor", "1")
If monitor <> "" Then
    oShell.CurrentDirectory = "c:\Users\SYMBOTIC\Documents\Symbotic_Teleops\autoclaim2\Automation"
    oShell.Run "cmd.exe /k python main.py calibrate --config config_1.json --monitor " & monitor, 1, False
End If
