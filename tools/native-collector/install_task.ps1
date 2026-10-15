# 以管理员身份在本机 PowerShell 运行本脚本，注册「TakealotCollector」计划任务：
# 登录时后台启动采集器（持续模式），失败每 1 分钟自动重启（最多 3 次）。
# 仅行情采集，绝不涉及调价/采购；密钥由本机 DPAPI 解密，不落盘。
#
# 若需“无界面”后台（实验性，Takealot 风控可能弹 site_challenge），把下面 $arg 里的
#   start
# 改为
#   start --headless

$python = "C:\Users\Administrator\AppData\Local\TakealotCollector\runtime-cpython-313\Scripts\python.exe"
$script = "E:\takealot-native-collector (3)\run_background.py"
$dir    = "E:\takealot-native-collector (3)"

# 有界面（默认，最稳）：start
$arg = "-u `"$script`" start"

$action   = New-ScheduledTaskAction -Execute $python -Argument $arg -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
               -ExecutionTimeLimit 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "TakealotCollector" -Action $action -Trigger $trigger -Settings $settings -Force

Write-Output "已注册计划任务 TakealotCollector（登录触发 + 失败自动重启）。"
Write-Output "立即启动：schtasks /run /tn TakealotCollector"
Write-Output "停止采集 ：python `"$script`" stop"
Write-Output "注销任务 ：schtasks /delete /tn TakealotCollector /f"
