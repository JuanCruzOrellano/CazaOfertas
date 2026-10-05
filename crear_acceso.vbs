' Crea el acceso directo "CazaOfertas" en el Escritorio
Set fso = CreateObject("Scripting.FileSystemObject")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
Set s = sh.CreateShortcut(sh.SpecialFolders("Desktop") & "\CazaOfertas.lnk")
exe = carpeta & "\CazaOfertas.exe"
If fso.FileExists(exe) Then
  s.TargetPath = exe
  s.Arguments = ""
  s.IconLocation = exe & ",0"
Else
  s.TargetPath = "wscript.exe"
  s.Arguments = """" & carpeta & "\Abrir CazaOfertas.vbs"""
  s.IconLocation = carpeta & "\app.ico"
End If
s.WorkingDirectory = carpeta
s.Description = "CazaOfertas"
s.Save
