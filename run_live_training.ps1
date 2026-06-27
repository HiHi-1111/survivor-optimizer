$ErrorActionPreference = "Stop"

cd C:\Users\iyoua\Downloads\survivor-optimizer
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONUNBUFFERED = "1"

Write-Host "Checking for active Python trainer..."
$trainers = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*" -and $_.CommandLine -match "survivor-optimizer|train_optimizer|run_training"
}

if ($trainers) {
    Write-Host "Found old trainer process. Killing it..."
    $trainers | ForEach-Object {
        Write-Host "Killing PID $($_.ProcessId): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force
    }
    Start-Sleep 2
}

$lock = "C:\Users\iyoua\Downloads\survivor-optimizer\training_outputs\training.lock"
if (Test-Path $lock) {
    Write-Host "Removing stale training lock..."
    Remove-Item $lock -Force
}

Write-Host "CUDA check:"
python -c "import torch; print('cuda_available=', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA')"

Write-Host "Starting 1-hour live GPU training..."
.\tools\run_training.ps1 -Minutes 60 -ProfileCount 1000000 -Workers auto -Device auto -GpuScore -BatchSize 4096 -TimeOnly -ComboMode -ChainDepth 3 -BeamSize 300 -MaxActionsPerProfile 2000 -CoverageReport | Tee-Object reports\training_compare\training_1hr_live_log.txt
