Set WshShell = CreateObject("WScript.Shell")
scriptPath = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.CurrentDirectory = scriptPath

extraArgs = ""
For i = 0 To WScript.Arguments.Count - 1
    extraArgs = extraArgs & " " & WScript.Arguments(i)
Next

WshShell.Run "pythonw """ & scriptPath & "spotify_sync_gui.py""" & extraArgs, 0, False
Set WshShell = Nothing
