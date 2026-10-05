' Abre CazaOfertas sin mostrar la consola negra
Set fso = CreateObject("Scripting.FileSystemObject")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = carpeta
sh.Run "pyw -3 """ & carpeta & "\app.py""", 0, False
