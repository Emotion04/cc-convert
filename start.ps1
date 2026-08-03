# CC Convert 一键启动脚本
# 启动管理后台 + 打开浏览器

Write-Host "╔════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     CC Convert Multi-Instance       ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 启动后台服务
Write-Host "[1/3] 启动管理后台..." -ForegroundColor Yellow
ccconvert start
if ($LASTEXITCODE -ne 0) {
    Write-Host "启动失败，请检查 ccconvert logs" -ForegroundColor Red
    pause
    exit 1
}

# 短暂等待端口就绪
Start-Sleep -Seconds 1

# 打开 Dashboard
Write-Host "[2/3] 打开 Dashboard..." -ForegroundColor Yellow
ccconvert open

# 显示状态
Write-Host "[3/3] 当前状态:" -ForegroundColor Yellow
ccconvert status

Write-Host ""
Write-Host "Dashboard 已打开，按任意键退出（不会停止后台服务）" -ForegroundColor Green
pause
