$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('C:\Users\garde\OneDrive\Desktop\REX.lnk')
$Shortcut.TargetPath = 'C:\Users\garde\projects\REX-AI\.venv\Scripts\python.exe'
$Shortcut.Arguments = '"C:\Users\garde\projects\REX-AI\main.py"'
$Shortcut.WorkingDirectory = 'C:\Users\garde\projects\REX-AI'
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Launch REX'
if ('C:\Users\garde\projects\REX-AI\assets\REX_Logo.ico') { $Shortcut.IconLocation = 'C:\Users\garde\projects\REX-AI\assets\REX_Logo.ico,0' }
$Shortcut.Save()