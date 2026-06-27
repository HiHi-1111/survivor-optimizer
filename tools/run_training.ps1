param(
    [int]$Minutes = 30,
    [int]$ProfileCount = 5000,
    [string]$Workers = "auto",
    [string]$Device = "auto",
    [switch]$GpuScore,
    [switch]$NoGpuScore,
    [int]$BatchSize = 256,
    [int]$Seed = 0,
    [switch]$Fresh,
    [switch]$NoResume,
    [int]$ChainDepth = 2,
    [int]$BeamSize = 100,
    [int]$MaxActionsPerProfile = 500,
    [switch]$CoverageReport,
    [switch]$ComboMode,
    [switch]$NoGlobalPlanner,
    [switch]$AllowExhaustiveSmallInventory,
    [switch]$TimeOnly,
    [switch]$StopWhenExhausted,
    [switch]$NoLive,
    [switch]$FastMode,
    [switch]$DeepEveryProfile,
    [int]$ChainProfileInterval = 10,
    [int]$GlobalProfileInterval = 100,
    [switch]$NoLearnedPruning,
    [ValidateSet("off", "soft", "normal", "aggressive")]
    [string]$LearnedPruning = "normal",
    [double]$ExplorationRate = 0.08,
    [int]$AuditFullSearchInterval = 100,
    [double]$AuditFullSearchRate = 0.0,
    [switch]$KeepGenerating,
    [switch]$LearnedRanker,
    [ValidateSet("cpu", "cuda", "auto")]
    [string]$RankerDevice = "auto",
    [int]$PriorMinSamples = 20,
    [double]$CheckpointInterval = 30,
    [switch]$GpuProfileFeatures,
    [switch]$NoGpuProfileFeatures,
    [int]$ProfileBatchSize = 65536,
    [ValidateSet("on_demand", "all")]
    [string]$MaterializeProfiles = "on_demand",
    [Alias("Verbose")]
    [switch]$DebugLogs,
    [switch]$AllowCpuFallback,
    [switch]$ForceStaleLock,
    [switch]$RunPostChecks
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot
$PythonExe = "python"
if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
$DebugLog = Join-Path $ProjectRoot "logs\training\latest_debug.log"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DebugLog) | Out-Null

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

if ($Seed -eq 0) {
    $Seed = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}
$InitialProfileCount = $ProfileCount
if ($MaterializeProfiles -eq "on_demand") {
    $InitialProfileCount = [Math]::Min($ProfileCount, 100)
}

if ($FastMode -and $DeepEveryProfile) {
    throw "Choose either -FastMode or -DeepEveryProfile, not both."
}
if ($FastMode) {
} else {
    if (-not $NoGlobalPlanner) {
        $ComboMode = $true
    }
}
$UseGpuScore = -not $NoGpuScore
if ($GpuScore) {
    $UseGpuScore = $true
}
$UseGpuProfileFeatures = -not $NoGpuProfileFeatures
if ($GpuProfileFeatures) {
    $UseGpuProfileFeatures = $true
}
if ($NoLearnedPruning) {
    $LearnedPruning = "off"
}
$TrainArgs = @(
    "tools/train_optimizer.py",
    "--minutes", $Minutes,
    "--seed", $Seed,
    "--workers", $Workers,
    "--device", $Device,
    "--batch-size", $BatchSize,
    "--profile-batch-size", $ProfileBatchSize,
    "--materialize-profiles", $MaterializeProfiles,
    "--checkpoint-interval", $CheckpointInterval,
    "--profile-count", $ProfileCount,
    "--prepare-profile-count", $InitialProfileCount,
    "--logging-mode", $(if ($DebugLogs) { "debug" } else { "json-log-to-file" })
)
if ($UseGpuProfileFeatures) {
    $TrainArgs += "--gpu-profile-features"
}
if (-not $NoResume -and -not $Fresh) {
    $TrainArgs += "--resume"
}
if ($Fresh) {
    $TrainArgs += "--fresh"
}
if ($UseGpuScore) {
    $TrainArgs += "--gpu-score"
}
if ($AllowCpuFallback) {
    $TrainArgs += "--allow-cpu-fallback"
}
if ($ForceStaleLock) {
    $TrainArgs += "--force-stale-lock"
}
if ($CoverageReport) {
    $TrainArgs += "--coverage-report"
}
if ($ComboMode) {
    $TrainArgs += "--combo-mode"
}
if ($AllowExhaustiveSmallInventory) {
    $TrainArgs += "--allow-exhaustive-small-inventory"
}
if (-not $StopWhenExhausted -or $TimeOnly -or $KeepGenerating) {
    $TrainArgs += "--keep-generating"
} else {
    $TrainArgs += "--stop-when-exhausted"
}
if ($FastMode) {
    $TrainArgs += @(
        "--fast-throughput",
        "--chain-profile-interval", $ChainProfileInterval,
        "--global-profile-interval", $GlobalProfileInterval
    )
} else {
    $TrainArgs += @(
        "--chain-profile-interval", 1,
        "--global-profile-interval", 1
    )
}
if ($LearnedPruning -ne "off") {
    $TrainArgs += @(
        "--learned-pruning",
        $LearnedPruning,
        "--exploration-rate", $ExplorationRate,
        "--audit-full-search-interval", $AuditFullSearchInterval,
        "--audit-full-search-rate", $AuditFullSearchRate,
        "--prior-min-samples", $PriorMinSamples
    )
}
if ($LearnedRanker) {
    $TrainArgs += @("--learned-ranker", "--ranker-device", $RankerDevice)
}
if (-not $NoLive -and -not [Console]::IsOutputRedirected) {
    $TrainArgs += "--live"
}
$TrainArgs += @(
    "--chain-depth", $ChainDepth,
    "--beam-size", $BeamSize,
    "--max-actions-per-profile", $MaxActionsPerProfile
)
& $PythonExe @TrainArgs
Assert-NativeSuccess "Optimizer training"

if ($RunPostChecks) {
    & $PythonExe tools/validate_knowledge.py *>> $DebugLog
    Assert-NativeSuccess "Knowledge validation"
    & $PythonExe -m pytest tests/test_training_startup.py tests/test_preprune_ranker.py -q *>> $DebugLog
    Assert-NativeSuccess "Targeted startup tests"
}

$SummaryPath = Join-Path $ProjectRoot "training_outputs\latest_summary.json"
$MetricsPath = Join-Path $ProjectRoot "training_outputs\latest_metrics.json"
$Summary = Get-Content -Raw -LiteralPath $SummaryPath | ConvertFrom-Json
$Summary.test_result_status = $(if ($RunPostChecks) { "passed" } else { "not_run" })
$Summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $SummaryPath -Encoding utf8
$Metrics = Get-Content -Raw -LiteralPath $MetricsPath | ConvertFrom-Json
$Metrics.test_result_status = $(if ($RunPostChecks) { "passed" } else { "not_run" })
$Metrics | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $MetricsPath -Encoding utf8
if (-not $DebugLogs) {
    Write-Host "Optimizer training complete"
    $Summary.PSObject.Properties | ForEach-Object { Write-Host ("{0}: {1}" -f $_.Name, $_.Value) }
}
