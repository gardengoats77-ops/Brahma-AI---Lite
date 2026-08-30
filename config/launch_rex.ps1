$ROOT = "C:\Users\garde\projects\REX-AI"
$python = "$ROOT\.venv\Scripts\python.exe"
$main = "$ROOT\main.py"

Write-Output "Launching REX with visible console..."
$p = Start-Process -FilePath $python -ArgumentList $main,"--startup" -WorkingDirectory $ROOT -PassThru
Write-Output "PID: $($p.Id)"
