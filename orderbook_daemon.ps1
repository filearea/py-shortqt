# 订单簿记录守护进程

param(
    [int]$days = 3,           # 运行天数
    [int]$interval = 60,      # 记录间隔（秒）
    [string]$symbol = "ETHUSDC"
)

$startTime = Get-Date
$endTime = $startTime.AddDays($days)
$checkInterval = 1800  # 30 分钟检查一次

Write-Host "========================================"
Write-Host "订单簿记录守护进程"
Write-Host "========================================"
Write-Host "开始时间：$startTime"
Write-Host "结束时间：$endTime"
Write-Host "记录间隔：$interval 秒"
Write-Host "交易对：$symbol"
Write-Host "========================================"
Write-Host ""

$process = $null
$lastCheck = Get-Date

while ((Get-Date) -lt $endTime) {
    # 检查进程是否在运行
    $processRunning = $false
    if ($process) {
        try {
            $processRunning = !$process.HasExited
        } catch {
            $processRunning = $false
        }
    }
    
    # 如果进程没运行，重新启动
    if (!$processRunning) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 启动订单簿记录..."
        $process = Start-Process python -ArgumentList "collect_data.py", "--mode", "record", "--interval", $interval, "--symbols", $symbol -WorkingDirectory "D:\Project\py-shortqt" -PassThru
        Start-Sleep -Seconds 5
    }
    
    # 每 30 分钟输出一次状态
    if ((New-TimeSpan -Start $lastCheck -End (Get-Date)).TotalMinutes -ge 30) {
        $elapsed = New-TimeSpan -Start $startTime -End (Get-Date)
        $remaining = New-TimeSpan -Start (Get-Date) -End $endTime
        
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 守护进程状态检查"
        Write-Host "  运行时长：$([int]$elapsed.TotalHours)小时$([int]$elapsed.Minutes 分钟"
        Write-Host "  剩余时间：$([int]$remaining.TotalHours)小时$([int]$remaining.Minutes 分钟"
        Write-Host "  进程状态：$(if($processRunning){'运行中'}else{'已停止'})"
        
        # 检查数据文件
        $today = Get-Date -Format "yyyy-MM-dd"
        $dataFile = "D:\Project\py-shortqt\data\orderbook\$symbol\$today.jsonl"
        if (Test-Path $dataFile) {
            $lineCount = (Get-Content $dataFile | Measure-Object -Line).Lines
            $fileSize = (Get-Item $dataFile).Length / 1KB
            Write-Host "  今日数据：$lineCount 条记录 ($([math]::Round($fileSize, 2)) KB)"
        }
        
        Write-Host ""
        $lastCheck = Get-Date
    }
    
    # 等待 1 分钟后再次检查
    Start-Sleep -Seconds 60
}

Write-Host "========================================"
Write-Host "守护进程运行结束"
Write-Host "总运行时长：$(New-TimeSpan -Start $startTime -End (Get-Date))"
Write-Host "========================================"

# 停止记录进程
if ($process) {
    Stop-Process -Id $process.Id -Force
    Write-Host "订单簿记录进程已停止"
}
