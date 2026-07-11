Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

Root = FSO.GetParentFolderName(FSO.GetParentFolderName(WScript.ScriptFullName))
PythonExe = WshShell.Environment("PROCESS")("HERMES_BRIDGE_PYTHON")
If PythonExe = "" Then
    EnvFile = Root & "\.env"
    If FSO.FileExists(EnvFile) Then
        Set EnvStream = CreateObject("ADODB.Stream")
        EnvStream.Type = 2
        EnvStream.Charset = "utf-8"
        EnvStream.Open
        EnvStream.LoadFromFile EnvFile
        EnvText = EnvStream.ReadText
        EnvStream.Close
        For Each RawLine In Split(Replace(EnvText, vbCrLf, vbLf), vbLf)
            Line = Trim(RawLine)
            If LCase(Left(Line, 21)) = "hermes_bridge_python=" Then
                PythonExe = Trim(Mid(Line, 22))
                If Len(PythonExe) >= 2 Then
                    If (Left(PythonExe, 1) = """" And Right(PythonExe, 1) = """") Or _
                       (Left(PythonExe, 1) = "'" And Right(PythonExe, 1) = "'") Then
                        PythonExe = Mid(PythonExe, 2, Len(PythonExe) - 2)
                    End If
                End If
                Exit For
            End If
        Next
    End If
End If
If PythonExe = "" Then
    PythonExe = "pythonw.exe"
End If

If InStr(PythonExe, "\") > 0 And Not FSO.FileExists(PythonExe) Then
    WScript.Quit 2
End If

Command = """" & PythonExe & """ """ & Root & "\bridge_control\control_service.py"""
WshShell.Run Command, 0, False
