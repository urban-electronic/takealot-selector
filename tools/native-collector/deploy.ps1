# 从仓库单向同步采集器代码到本机部署目录。
# 保留部署目录中已有的 browser_profile / service-key.dpapi 等用户数据，不覆盖。
$ErrorActionPreference = 'Stop'
$Repo   = "C:\Users\Administrator\WorkBuddy\2026-10-15-00-15-02\takealot-selector\tools\native-collector"
$Deploy = "E:\takealot-native-collector (3)"
$files  = @('gui.py','collector.py','run_background.py','install_task.ps1','DESIGN.md','pricepilot_collector.py')
if (-not (Test-Path $Deploy)) { New-Item -ItemType Directory -Path $Deploy | Out-Null }
foreach ($f in $files) {
    $src = Join-Path $Repo $f
    if (Test-Path $src) { Copy-Item -Force $src (Join-Path $Deploy $f); Write-Output "deploy $f" }
}
Write-Output "DONE: 仓库代码已同步到 $Deploy（用户数据已保留）"
