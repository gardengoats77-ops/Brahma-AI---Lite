$ROOT = "C:\Users\garde\projects\REX-AI"
$target = "$ROOT\.venv\Scripts\pythonw.exe"
$args = "`"$ROOT\main.py`" --startup"
$icon = "$ROOT\assets\REX_Logo.ico,0"
$desc = "Launch REX"

# ── 1. Start Menu shortcut ──────────────────────────────────────────
$startMenuDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
$startMenuLnk = "$startMenuDir\REX.lnk"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($startMenuLnk)
$Shortcut.TargetPath = $target
$Shortcut.Arguments = $args
$Shortcut.WorkingDirectory = $ROOT
$Shortcut.WindowStyle = 7
$Shortcut.Description = $desc
$Shortcut.IconLocation = $icon
$Shortcut.Save()
Write-Output "[OK] Start Menu shortcut created: $startMenuLnk"

# ── 2. Taskbar pin ──────────────────────────────────────────────────
try {
    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.Namespace($startMenuDir)
    $item = $folder.ParseName("REX.lnk")
    if ($item) {
        $item.InvokeVerb("taskbarpin")
        Write-Output "[OK] Pinned to taskbar"
    } else {
        Write-Output "[!] Could not find shortcut for pinning"
    }
} catch {
    Write-Output "[!] Taskbar pin failed: $_"
    Write-Output "    Open Start Menu, right-click REX, and select 'Pin to taskbar'"
}
