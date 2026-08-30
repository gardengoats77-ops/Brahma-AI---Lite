$ROOT = "C:\Users\garde\projects\REX-AI"

# Update Desktop shortcut
$desktop = "$env:USERPROFILE\Desktop\REX.lnk"
$WshShell = New-Object -ComObject WScript.Shell
$lnk = $WshShell.CreateShortcut($desktop)
$lnk.TargetPath = "$ROOT\.venv\Scripts\python.exe"
$lnk.Arguments = "`"$ROOT\main.py`" --startup"
$lnk.WorkingDirectory = $ROOT
$lnk.WindowStyle = 7
$lnk.Description = "Launch REX"
$lnk.IconLocation = "$ROOT\assets\REX_Logo.ico,0"
$lnk.Save()
Write-Output "[OK] Desktop shortcut updated (python.exe)"

# Update Start Menu shortcut
$startmenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\REX.lnk"
if (Test-Path $startmenu) {
    $lnk2 = $WshShell.CreateShortcut($startmenu)
    $lnk2.TargetPath = "$ROOT\.venv\Scripts\python.exe"
    $lnk2.Arguments = "`"$ROOT\main.py`" --startup"
    $lnk2.WorkingDirectory = $ROOT
    $lnk2.WindowStyle = 7
    $lnk2.Description = "Launch REX"
    $lnk2.IconLocation = "$ROOT\assets\REX_Logo.ico,0"
    $lnk2.Save()
    Write-Output "[OK] Start Menu shortcut updated (python.exe)"
}
