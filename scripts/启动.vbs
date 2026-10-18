' Edifier Guard - manual launcher (no registry changes, no auto-start)
'
' Double-click to start Edifier Guard in the background.
'
' Target resolution:
'   1. the single-file  EdifierGuard.exe  in the project root
'      (self-contained: needs no Python and no external files)   <-- preferred
'   2. otherwise the Python sources (watchdog.py) in the project root
'
' The watchdog launches and supervises guard.py, so use this launcher
' (not guard.py directly) to keep the crash-restart protection.
Option Explicit

Dim sh, fso, here, app, exe, target, pyCmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)      ' ...\scripts
app  = fso.GetParentFolderName(here)                        ' project root
exe  = app & "\EdifierGuard.exe"

' ---- 1) single-file exe ----
If fso.FileExists(exe) Then
    sh.CurrentDirectory = app
    sh.Run """" & exe & """", 0, False
    WScript.Quit 0
End If

' ---- 2) Python sources ----
target = app & "\watchdog.py"

If Not fso.FileExists(target) Then
    MsgBox "Neither EdifierGuard.exe nor watchdog.py was found in:" & vbCrLf & app, 16, "Edifier Guard"
    WScript.Quit 1
End If

pyCmd = FindPython()
If pyCmd = "" Then
    MsgBox "Python not found." & vbCrLf & vbCrLf & _
           "Use EdifierGuard.exe instead (no Python needed), or drop a portable" & vbCrLf & _
           "python.exe into:" & vbCrLf & app & "\runtime\", 16, "Edifier Guard"
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

Function OnPath(exeName)
    On Error Resume Next
    OnPath = (sh.Run("cmd /c where " & exeName & " >nul 2>nul", 0, True) = 0)
    On Error GoTo 0
End Function
