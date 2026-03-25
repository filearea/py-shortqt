# 订单簿记录启动脚本

param(
    [int]$interval = 300  # 默认 5 分钟
)

Write-Host "========================================"
Write-Host "订单簿定时记录"
Write-Host "========================================"
Write-Host "间隔：$interval 秒"
Write-Host "交易对：ETHUSDC"
Write-Host "按 Ctrl+C 停止"
Write-Host "========================================"
Write-Host ""

cd D:\Project\py-shortqt

while ($true) {
    python collect_data.py --mode record --interval $interval --symbols ETHUSDC
    Start-Sleep -Seconds $interval
}
