# 订单簿记录健康检查脚本

$symbol = "ETHUSDC"
$dataDir = "D:\Project\py-shortqt\data\orderbook\$symbol"
$logFile = "D:\Project\py-shortqt\logs\orderbook_health.log"

# 确保日志目录存在
$logDir = Split-Path $logFile -Parent
if (!(Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $message"
    Write-Host $logMessage
    Add-Content -Path $logFile -Value $logMessage
}

Write-Log "========================================"
Write-Log "订单簿记录健康检查"

# 检查守护进程是否在运行
$daemonProcess = Get-Process | Where-Object { $_.CommandLine -like "*orderbook_daemon.ps1*" }

if ($daemonProcess) {
    Write-Log "[OK] 守护进程运行中 (PID: $($daemonProcess.Id))"
} else {
    Write-Log "[WARN] 守护进程未运行，尝试重启..."
    
    # 重启守护进程
    Start-Process powershell -ArgumentList "-NoExit", "-File", "D:\Project\py-shortqt\orderbook_daemon.ps1", "-days", "3", "-interval", "60", "-symbol", $symbol -WorkingDirectory "D:\Project\py-shortqt"
    Write-Log "[OK] 守护进程已重启"
}

# 检查记录进程是否在运行
$recordProcess = Get-Process | Where-Object { $_.CommandLine -like "*collect_data.py*record*" }

if ($recordProcess) {
    Write-Log "[OK] 记录进程运行中 (PID: $($recordProcess.Id))"
} else {
    Write-Log "[WARN] 记录进程未运行，守护进程会重启它"
}

# 检查今日数据文件
$today = Get-Date -Format "yyyy-MM-dd"
$dataFile = "$dataDir\$today.jsonl"

if (Test-Path $dataFile) {
    $lineCount = (Get-Content $dataFile | Measure-Object -Line).Lines
    $fileSize = (Get-Item $dataFile).Length / 1KB
    $lastModified = (Get-Item $dataFile).LastWriteTime
    
    Write-Log "[OK] 今日数据文件：$lineCount 条记录 ($([math]::Round($fileSize, 2)) KB)"
    Write-Log "  最后修改：$lastModified"
    
    # 检查最后一条记录是否在 2 分钟内
    $lastRecord = Get-Content $dataFile | Select-Object -Last 1 | ConvertFrom-Json
    $lastRecordTime = [DateTimeOffset]::FromUnixTimeMilliseconds($lastRecord.timestamp).DateTime
    $timeDiff = New-TimeSpan -End $lastRecordTime -Start (Get-Date)
    
    if ($timeDiff.TotalMinutes -le 2) {
        Write-Log "[OK] 记录正常（最新：$([int]$timeDiff.TotalSeconds)秒前）"
    } else {
        Write-Log "[WARN] 记录可能停滞（最新：$([int]$timeDiff.TotalMinutes)分钟前）"
    }
} else {
    Write-Log "[WARN] 今日数据文件不存在"
}

Write-Log "========================================"
Write-Log ""
