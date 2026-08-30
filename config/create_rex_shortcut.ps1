$ROOT = "C:\Users\garde\projects\REX-AI"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\REX.lnk")
$Shortcut.TargetPath = "$ROOT\.venv\Scripts\pythonw.exe"
$Shortcut.Arguments = "`"$ROOT\main.py`" --startup"
$Shortcut.WorkingDirectory = $ROOT
$Shortcut.WindowStyle = 7
$Shortcut.Description = "Launch REX"
$Shortcut.IconLocation = "$ROOT\assets\REX_Logo.ico,0"
$Shortcut.Save()
Write-Output "REX shortcut created on Desktop"
