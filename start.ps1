# AI OKX Trader v2 启动脚本
# 自动清理旧进程并启动服务器

$PORT = 8000

Write-Host "检查端口 $PORT 是否被占用..." -ForegroundColor Yellow

# 查找占用端口的进程
$processes = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue | 
    Select-Object -ExpandProperty OwningProcess -Unique

if ($processes) {
    Write-Host "发现 $($processes.Count) 个进程占用端口 $PORT" -ForegroundColor Red
    foreach ($processId in $processes) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  - 停止进程: $($proc.ProcessName) (PID: $processId)" -ForegroundColor Yellow
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 1
}

Write-Host "启动服务器..." -ForegroundColor Green
uvicorn src.main:app --host 0.0.0.0 --port $PORT --reload
