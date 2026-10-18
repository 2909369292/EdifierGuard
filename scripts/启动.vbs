' Edifier Guard - manual launcher (no registry changes, no auto-start)
'
' Double-click to start the watchdog (watchdog.py) in the background.
' The watchdog launches and supervises guard.py, so use this launcher
' (not guard.py directly) to keep the crash-restart protection.
'
' Target resolution (stable by design):
'   1. the fixed install location  %LOCALAPPDATA%\EdifierGuard  (if present)
'   2. otherwise the project folder this script ships in (..\ from here)
'
' Python is auto-detected: bundled runtime\, then PATH, then the py launcher.
Option Explicit

Dim sh, fso, here, inst, app, target, pyCmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)      ' ...\scripts
app  = fso.GetParentFolderName(here)                        ' project root
inst = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\EdifierGuard"
If fso.FileExists(inst & "\guard.py") Then app = inst

target = app & "\watchdog.py"

If Not fso.FileExists(target) Then
    MsgBox "watchdog.py not found:" & vbCrLf & target, 16, "Edifier Guard"
    WScript.Quit 1
End If

pyCmd = FindPython()
If pyCmd = "" Then
    MsgBox "Python not found." & vbCrLf & vbCrLf & _
           "Install Python 3.x, or drop a portable python.exe into:" & vbCrLf & _
           app & "\runtime\", 16, "Edifier Guard"
    WScript.Quit 1
End If

sh.CurrentDirectory = app
sh.Run pyCmd & " """ & target & """", 0, False


' Returns a runnable command prefix (may include arguments), or "" if none found.
Function FindPython()
    Dim c
    ' 1) bundled portable runtime
    For Each c In Array(app & "\runtime\pythonw.exe", app & "\runtime\python.exe")
        If fso.FileExists(c) Then
            FindPython = """" & c & """"
            Exit Function
        End If
    Next
    ' 2) python on PATH (windowless first)
    If OnPath("pythonw.exe") Then FindPython = "pythonw.exe" : Exit Function
    If OnPath("python.exe")  Then FindPython = "python.exe"  : Exit Function
    ' 3) Windows py launcher
    If OnPath("pyw.exe") Then FindPython = "pyw.exe -3" : Exit Function
    If OnPath("py.exe")  Then FindPython = "py.exe -3"  : Exit Function
    FindPython = ""
End Function

Function OnPath(exe)
    On Error Resume Next
    OnPath = (sh.Run("cmd /c where " & exe & " >nul 2>nul", 0, True) = 0)
    On Error GoTo 0
End Function
