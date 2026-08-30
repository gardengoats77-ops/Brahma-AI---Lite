$p = Start-Process -FilePath "C:\Users\garde\projects\REX-AI\.venv\Scripts\python.exe" -ArgumentList "C:\Users\garde\projects\REX-AI\main.py","--startup" -WorkingDirectory "C:\Users\garde\projects\REX-AI" -WindowStyle Normal -PassThru
Write-Host "PID=$($p.Id)"
