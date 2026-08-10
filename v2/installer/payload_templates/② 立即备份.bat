@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 备份会议室预约系统 V2
cd /d "%~dp0"

set "MRV2_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "MRV2_PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "PSModulePath=%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules"
set "ComSpec=%SystemRoot%\System32\cmd.exe"
if not exist "%MRV2_PS%" goto :missing

"%MRV2_PS%" -NoProfile -NonInteractive -Command "if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :elevated
set "MRV2_ELEVATE_SCRIPT=%~f0"
"%MRV2_PS%" -NoProfile -NonInteractive -Command "try { $p=Start-Process -FilePath $env:MRV2_ELEVATE_SCRIPT -Verb RunAs -Wait -PassThru; exit [int]$p.ExitCode } catch { exit 3 }"
set "BACKUP_EXIT=%errorlevel%"
if "%BACKUP_EXIT%"=="3" (
    echo.
    echo 已取消管理员授权，没有执行备份。
    pause
)
exit /b %BACKUP_EXIT%

:elevated
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "MRV2_ROOT=%~dp0"
set "MRV2_INFO=%~dp0_程序文件\data\install.json"
set "MRV2_ID_FILE=%~dp0_程序文件\data\install_id"
set "MRV2_APP=%~dp0_程序文件\app"
set "MRV2_PYTHON=%~dp0_程序文件\runtime\python.exe"
set "MRV2_PYTHONW=%~dp0_程序文件\runtime\pythonw.exe"
set "MRV2_SERVICE=%~dp0_程序文件\app\service.py"
set "MRV2_BACKUP=%~dp0_程序文件\app\backup.py"
set "MRV2_STATUS=%~dp0_程序文件\data\backup-status.json"
set "MRV2_REGISTRY=HKLM:\Software\MeetingRoomReservationV2"
set "MRV2_TASK_NAME=会议室预约系统 V2"
set "MRV2_BACKUP_TASK_NAME=会议室预约系统 V2 每日备份"
set "MRV2_FW_MANUAL=会议室预约系统V2-手动"
set "MRV2_FW_BACKGROUND=会议室预约系统V2-后台"

if not exist "%MRV2_PYTHON%" goto :missing
if not exist "%MRV2_PYTHONW%" goto :missing
if not exist "%MRV2_SERVICE%" goto :missing
if not exist "%MRV2_BACKUP%" goto :missing
if not exist "%MRV2_INFO%" goto :missing
if not exist "%MRV2_ID_FILE%" goto :missing

"%MRV2_PS%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=[IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\'); $programFiles=[Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles); $expectedRoot=[IO.Path]::GetFullPath((Join-Path $programFiles '会议室预约系统V2')).TrimEnd('\'); if (-not [string]::Equals($root,$expectedRoot,[StringComparison]::OrdinalIgnoreCase)) { throw ('当前目录不是固定 Program Files 安装根：'+$expectedRoot) }; $info=Get-Content -LiteralPath $env:MRV2_INFO -Raw -Encoding UTF8 | ConvertFrom-Json; $installId=[string]$info.install_id; $diskId=(Get-Content -LiteralPath $env:MRV2_ID_FILE -Raw -Encoding ASCII).Trim(); if ($installId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or $diskId -cne $installId -or [int]$info.product_generation -ne 2 -or [int]$info.port -ne 8080) { throw 'V2 磁盘安装身份无效。' }; $identity='MeetingRoomReservationV2:'+$installId; $registered=Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY -ErrorAction Stop; if ([string]$registered.InstallRoot -ne $root -or [string]$registered.TransactionInstallId -cne $installId -or [string]$registered.InstallId -cne $installId -or [string]$registered.SecurityInstallId -cne $installId -or [int]$registered.ProductGeneration -ne 2 -or [int]$registered.SecurityDescriptorVersion -ne 1 -or [int]$registered.Port -ne 8080 -or [string]$registered.ServiceTaskName -ne $env:MRV2_TASK_NAME -or [string]$registered.BackupTaskName -ne $env:MRV2_BACKUP_TASK_NAME -or [string]$registered.ManualFirewallName -ne $env:MRV2_FW_MANUAL -or [string]$registered.BackgroundFirewallName -ne $env:MRV2_FW_BACKGROUND) { throw 'V2 HKLM 安装登记与当前目录或 install_id 不一致。' }; foreach ($name in @($env:MRV2_TASK_NAME,$env:MRV2_BACKUP_TASK_NAME)) { $task=Get-ScheduledTask -TaskPath '\' -TaskName $name -ErrorAction Stop; if ([string]$task.Description -cne $identity -or [string]$task.Principal.UserId -ne 'SYSTEM' -or [string]$task.Principal.LogonType -ne 'ServiceAccount' -or [string]$task.Principal.RunLevel -ne 'Highest') { throw ('V2 计划任务不属于当前安装：'+$name) } }; $serviceTask=Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop; $backupTask=Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_BACKUP_TASK_NAME -ErrorAction Stop; $quote=[char]34; $expectedServiceArguments=$quote+$env:MRV2_SERVICE+$quote; $expectedBackupArguments=$quote+$env:MRV2_BACKUP+$quote+' --scheduled --expected-install-id '+$installId; $bootTriggers=@($serviceTask.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' }); $dailyTriggers=@($backupTask.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' }); if ($serviceTask.Actions.Count -ne 1 -or [string]$serviceTask.Actions[0].Execute -ne $env:MRV2_PYTHONW -or [string]$serviceTask.Actions[0].Arguments -ne $expectedServiceArguments -or [string]$serviceTask.Actions[0].WorkingDirectory -ne $env:MRV2_APP -or $bootTriggers.Count -ne 1) { throw 'V2 主任务动作或开机触发器不符合固定契约。' }; if ($backupTask.Actions.Count -ne 1 -or [string]$backupTask.Actions[0].Execute -ne $env:MRV2_PYTHONW -or [string]$backupTask.Actions[0].Arguments -ne $expectedBackupArguments -or [string]$backupTask.Actions[0].WorkingDirectory -ne $env:MRV2_APP -or -not [bool]$backupTask.Settings.StartWhenAvailable -or [string]$backupTask.Settings.MultipleInstances -ne 'IgnoreNew' -or $dailyTriggers.Count -ne 1) { throw 'V2 每日备份任务动作或补跑设置不符合固定契约。' }; $dailyStart=[datetime]$dailyTriggers[0].StartBoundary; if ([int]$dailyTriggers[0].DaysInterval -ne 1 -or $dailyStart.Hour -ne 2 -or $dailyStart.Minute -ne 0) { throw 'V2 每日备份任务不是本地每日 02:00。' }; foreach ($name in @($env:MRV2_FW_MANUAL,$env:MRV2_FW_BACKGROUND)) { $rules=@(Get-NetFirewallRule -DisplayName $name -ErrorAction Stop); if ($rules.Count -ne 1 -or [string]$rules[0].Description -cne $identity -or [string]$rules[0].Direction -ne 'Inbound' -or [string]$rules[0].Action -ne 'Allow') { throw ('V2 防火墙规则不属于当前安装：'+$name) }; $portFilter=@($rules[0] | Get-NetFirewallPortFilter); $addressFilter=@($rules[0] | Get-NetFirewallAddressFilter); $applicationFilter=@($rules[0] | Get-NetFirewallApplicationFilter); $expectedProgram=if ($name -eq $env:MRV2_FW_MANUAL) { $env:MRV2_PYTHON } else { $env:MRV2_PYTHONW }; if ($portFilter.Count -ne 1 -or [string]$portFilter[0].Protocol -ne 'TCP' -or [string]$portFilter[0].LocalPort -ne '8080' -or $addressFilter.Count -ne 1 -or [string]$addressFilter[0].RemoteAddress -ne 'LocalSubnet' -or $applicationFilter.Count -ne 1 -or [string]$applicationFilter[0].Program -ne $expectedProgram) { throw ('V2 防火墙范围或程序路径不一致：'+$name) } }; $beforeSequence=$null; if (Test-Path -LiteralPath $env:MRV2_STATUS) { try { $before=Get-Content -LiteralPath $env:MRV2_STATUS -Raw -Encoding UTF8 | ConvertFrom-Json; if ($null -ne $before.sequence) { $beforeSequence=[long]$before.sequence } } catch { $beforeSequence=$null } }; $output=@(& $env:MRV2_PYTHON $env:MRV2_BACKUP --expected-install-id $installId 2>&1); $backupExit=[int]$LASTEXITCODE; $output | ForEach-Object { Write-Host $_ }; if ($backupExit -ne 0) { exit $backupExit }; $status=Get-Content -LiteralPath $env:MRV2_STATUS -Raw -Encoding UTF8 | ConvertFrom-Json; if ([string]$status.status -cne 'succeeded' -or $null -eq $status.sequence) { throw '人工备份进程退出成功，但没有留下成功状态或备份序列。' }; $newSequence=[long]$status.sequence; if ($null -ne $beforeSequence -and $newSequence -le $beforeSequence) { throw '人工备份没有创建更高序列的新备份。' }; Write-Host ('人工备份确认成功：序列 {0}，完成时间 {1}' -f $newSequence,[string]$status.detail); exit 0"
set "BACKUP_EXIT=%errorlevel%"
if not "%BACKUP_EXIT%"=="0" goto :failed

echo.
echo 备份成功。备份数据库和校验 sidecar 已写入受保护的“_程序文件\backups”。
echo 普通用户无法直接读取该目录；如需导出，请联系维护人员。
echo.
pause
exit /b 0

:missing
echo.
echo V2 备份程序或安装身份不完整，请联系维护人员。
echo.
pause
exit /b 1

:failed
echo.
echo 备份没有完成。工具未读取其他安装目录，也未改变开机任务。
echo 请保留“_程序文件\data”和“_程序文件\logs”，联系维护人员。
echo.
pause
exit /b %BACKUP_EXIT%
