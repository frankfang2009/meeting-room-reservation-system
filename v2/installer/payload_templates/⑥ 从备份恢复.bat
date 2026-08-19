@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 从受保护备份恢复会议室预约系统 V2
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
set "RESTORE_EXIT=%errorlevel%"
if "%RESTORE_EXIT%"=="3" (
    echo.
    echo 已取消管理员授权，没有恢复任何数据。
    pause
)
exit /b %RESTORE_EXIT%

:elevated
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "MRV2_ROOT=%~dp0"
set "MRV2_INFO=%~dp0_程序文件\data\install.json"
set "MRV2_ID_FILE=%~dp0_程序文件\data\install_id"
set "MRV2_BACKUPS=%~dp0_程序文件\backups"
set "MRV2_APP=%~dp0_程序文件\app"
set "MRV2_PYTHON=%~dp0_程序文件\runtime\python.exe"
set "MRV2_PYTHONW=%~dp0_程序文件\runtime\pythonw.exe"
set "MRV2_SERVICE=%~dp0_程序文件\app\service.py"
set "MRV2_BACKUP=%~dp0_程序文件\app\backup.py"
set "MRV2_RESTORE=%~dp0_程序文件\app\restore.py"
set "MRV2_REGISTRY=HKLM:\Software\MeetingRoomReservationV2"
set "MRV2_TASK_NAME=会议室预约系统 V2"
set "MRV2_BACKUP_TASK_NAME=会议室预约系统 V2 每日备份"
set "MRV2_FW_MANUAL=会议室预约系统V2-手动"
set "MRV2_FW_BACKGROUND=会议室预约系统V2-后台"
set "MRV2_SCRIPT=%~f0"

if not exist "%MRV2_PYTHON%" goto :missing
if not exist "%MRV2_PYTHONW%" goto :missing
if not exist "%MRV2_SERVICE%" goto :missing
if not exist "%MRV2_BACKUP%" goto :missing
if not exist "%MRV2_RESTORE%" goto :missing
if not exist "%MRV2_INFO%" goto :missing
if not exist "%MRV2_ID_FILE%" goto :missing
if not exist "%MRV2_BACKUPS%\" goto :missing

"%MRV2_PS%" -NoProfile -ExecutionPolicy Bypass -Command "$lines=Get-Content -LiteralPath $env:MRV2_SCRIPT -Encoding UTF8; $marker=-1; for ($index=0; $index -lt $lines.Length; $index++) { if ($lines[$index] -ceq '# MRV2-POWERSHELL-BEGIN') { $marker=$index; break } }; if ($marker -lt 0 -or $marker -ge ($lines.Length-1)) { [Console]::Error.WriteLine('恢复脚本内容不完整。'); exit 1 }; & ([ScriptBlock]::Create(($lines[($marker+1)..($lines.Length-1)] -join [Environment]::NewLine)))"
set "RESTORE_EXIT=%errorlevel%"
if "%RESTORE_EXIT%"=="3" goto :cancelled
if not "%RESTORE_EXIT%"=="0" goto :failed

echo.
echo V2 已从当前安装的最新有效备份恢复。
echo 原有开机任务和每日备份任务的启用状态已恢复。
echo.
pause
exit /b 0

:cancelled
echo.
echo 已取消恢复，服务和计划任务没有被修改。
echo.
pause
exit /b 0

:missing
echo.
echo V2 恢复程序、受保护备份或安装身份不完整，请联系维护人员。
echo.
pause
exit /b 1

:failed
echo.
echo V2 恢复未完成，或原有任务与服务状态未能完整恢复。
echo restore.py 会在写入前保留恢复前快照，并在失败时尝试回滚数据库。
echo 请不要重复操作；保留“_程序文件\data”、“backups”和“logs”并联系维护人员。
echo.
pause
exit /b %RESTORE_EXIT%

# MRV2-POWERSHELL-BEGIN
$ErrorActionPreference = 'Stop'

$root = [IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\')
$programFiles = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $programFiles '会议室预约系统V2')).TrimEnd('\')
if (-not [string]::Equals($root, $expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw ('当前目录不是固定 Program Files 安装根：' + $expectedRoot)
}

$info = Get-Content -LiteralPath $env:MRV2_INFO -Raw -Encoding UTF8 | ConvertFrom-Json
$installId = [string]$info.install_id
$diskId = (Get-Content -LiteralPath $env:MRV2_ID_FILE -Raw -Encoding ASCII).Trim()
if (
    $installId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
    $diskId -cne $installId -or
    [int]$info.product_generation -ne 2 -or
    [int]$info.port -ne 8080
) {
    throw 'V2 磁盘安装身份无效。'
}

$identity = 'MeetingRoomReservationV2:' + $installId
$registered = Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY -ErrorAction Stop
if (
    [string]$registered.InstallRoot -ne $root -or
    [string]$registered.TransactionInstallId -cne $installId -or
    [string]$registered.InstallId -cne $installId -or
    [string]$registered.SecurityInstallId -cne $installId -or
    [int]$registered.ProductGeneration -ne 2 -or
    [int]$registered.SecurityDescriptorVersion -ne 1 -or
    [int]$registered.Port -ne 8080 -or
    [string]$registered.ServiceTaskName -ne $env:MRV2_TASK_NAME -or
    [string]$registered.BackupTaskName -ne $env:MRV2_BACKUP_TASK_NAME -or
    [string]$registered.ManualFirewallName -ne $env:MRV2_FW_MANUAL -or
    [string]$registered.BackgroundFirewallName -ne $env:MRV2_FW_BACKGROUND
) {
    throw 'V2 HKLM 安装登记与当前目录或 install_id 不一致。'
}

$mainTask = Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
$backupTask = Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_BACKUP_TASK_NAME -ErrorAction Stop
foreach ($task in @($mainTask, $backupTask)) {
    if (
        [string]$task.Description -cne $identity -or
        [string]$task.Principal.UserId -ne 'SYSTEM' -or
        [string]$task.Principal.LogonType -ne 'ServiceAccount' -or
        [string]$task.Principal.RunLevel -ne 'Highest'
    ) {
        throw ('V2 计划任务不属于当前安装：' + $task.TaskName)
    }
}
$quote = [char]34
$expectedServiceArguments = $quote + $env:MRV2_SERVICE + $quote
$expectedBackupArguments = $quote + $env:MRV2_BACKUP + $quote + ' --scheduled --expected-install-id ' + $installId
$bootTriggers = @($mainTask.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' })
$dailyTriggers = @($backupTask.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' })
if (
    $mainTask.Actions.Count -ne 1 -or
    [string]$mainTask.Actions[0].Execute -ne $env:MRV2_PYTHONW -or
    [string]$mainTask.Actions[0].Arguments -ne $expectedServiceArguments -or
    [string]$mainTask.Actions[0].WorkingDirectory -ne $env:MRV2_APP -or
    $bootTriggers.Count -ne 1
) {
    throw 'V2 主任务动作或开机触发器不符合固定契约。'
}
if (
    $backupTask.Actions.Count -ne 1 -or
    [string]$backupTask.Actions[0].Execute -ne $env:MRV2_PYTHONW -or
    [string]$backupTask.Actions[0].Arguments -ne $expectedBackupArguments -or
    [string]$backupTask.Actions[0].WorkingDirectory -ne $env:MRV2_APP -or
    -not [bool]$backupTask.Settings.StartWhenAvailable -or
    [string]$backupTask.Settings.MultipleInstances -ne 'IgnoreNew' -or
    $dailyTriggers.Count -ne 1
) {
    throw 'V2 每日备份任务动作或补跑设置不符合固定契约。'
}
$dailyStart = [datetime]$dailyTriggers[0].StartBoundary
if ([int]$dailyTriggers[0].DaysInterval -ne 1 -or $dailyStart.Hour -ne 2 -or $dailyStart.Minute -ne 0) {
    throw 'V2 每日备份任务不是本地每日 02:00。'
}
foreach ($name in @($env:MRV2_FW_MANUAL, $env:MRV2_FW_BACKGROUND)) {
    $rules = @(Get-NetFirewallRule -DisplayName $name -ErrorAction Stop)
    if (
        $rules.Count -ne 1 -or
        [string]$rules[0].Description -cne $identity -or
        [string]$rules[0].Direction -ne 'Inbound' -or
        [string]$rules[0].Action -ne 'Allow'
    ) {
        throw ('V2 防火墙规则不属于当前安装：' + $name)
    }
    $portFilter = @($rules[0] | Get-NetFirewallPortFilter)
    $addressFilter = @($rules[0] | Get-NetFirewallAddressFilter)
    $applicationFilter = @($rules[0] | Get-NetFirewallApplicationFilter)
    $expectedProgram = if ($name -eq $env:MRV2_FW_MANUAL) {
        $env:MRV2_PYTHON
    } else {
        $env:MRV2_PYTHONW
    }
    if (
        $portFilter.Count -ne 1 -or
        [string]$portFilter[0].Protocol -ne 'TCP' -or
        [string]$portFilter[0].LocalPort -ne '8080' -or
        $addressFilter.Count -ne 1 -or
        [string]$addressFilter[0].RemoteAddress -ne 'LocalSubnet' -or
        $applicationFilter.Count -ne 1 -or
        [string]$applicationFilter[0].Program -ne $expectedProgram
    ) {
        throw ('V2 防火墙范围或程序路径不一致：' + $name)
    }
}

$selected = $null
$selectedMetadata = $null
$sidecars = @(
    Get-ChildItem -LiteralPath $env:MRV2_BACKUPS -File -Filter 'reservation-v2-backup-*.json' |
        Sort-Object -Property Name -Descending
)
foreach ($sidecar in $sidecars) {
    try {
        if ($sidecar.Length -gt 64KB) { continue }
        $metadata = Get-Content -LiteralPath $sidecar.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $sequence = 0
        if (-not [int]::TryParse([string]$metadata.sequence, [ref]$sequence) -or $sequence -lt 1) { continue }
        if (
            [int]$metadata.schema -ne 1 -or
            [string]$metadata.kind -cne 'meeting-room-v2-backup' -or
            [string]$metadata.installId -cne $installId -or
            [int]$metadata.productGeneration -ne 2 -or
            # 与 update_core.SUPPORTED_DATABASE_SCHEMA_VERSIONS（当前 1..3）保持同步：
            # 备份 sidecar 的 databaseSchemaVersion 是备份当时的库 schema，历史备份为 1/2，
            # 当前为 3；真正的可恢复性由 restore.py 恢复后复检兜底。
            [int]$metadata.databaseSchemaVersion -lt 1 -or
            [int]$metadata.databaseSchemaVersion -gt 3 -or
            $metadata.setupComplete -isnot [bool] -or
            $metadata.setupComplete -ne $true
        ) { continue }
        $expectedName = 'reservation-v2-backup-{0:D8}.db' -f $sequence
        $fileName = [string]$metadata.fileName
        if (
            $fileName -cne $expectedName -or
            [IO.Path]::GetFileName($fileName) -cne $fileName -or
            $sidecar.Name -cne [IO.Path]::ChangeExtension($fileName, '.json')
        ) { continue }
        $candidate = Get-Item -LiteralPath (Join-Path $env:MRV2_BACKUPS $fileName) -ErrorAction Stop
        if ($candidate.PSIsContainer) { continue }
        $selected = $candidate
        $selectedMetadata = $metadata
        break
    } catch {
        continue
    }
}
if ($null -eq $selected) {
    throw '当前安装的受保护 backups 目录中没有可恢复的配对备份。'
}

Write-Host ''
Write-Host ('即将恢复：' + $selected.Name)
Write-Host ('备份序列：' + [string]$selectedMetadata.sequence)
Write-Host ('备份时间：' + [string]$selectedMetadata.createdAtUtc)
Write-Host '警告：恢复后，该备份之后的业务变更将不再保留。'
$confirmation = Read-Host '请输入 RESTORE 确认，输入其他内容取消'
if ($confirmation -cne 'RESTORE') {
    exit 3
}

$mainWasEnabled = ([string]$mainTask.State -ne 'Disabled')
$backupWasEnabled = ([string]$backupTask.State -ne 'Disabled')
& $env:MRV2_PYTHON $env:MRV2_SERVICE --check *> $null
$serviceWasRunning = ($LASTEXITCODE -eq 0)
$operationExit = 1
$operationError = $null
$stateErrors = @()

try {
    if ($mainWasEnabled) {
        Disable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME | Out-Null
    }
    if ($backupWasEnabled) {
        Disable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_BACKUP_TASK_NAME | Out-Null
    }

    $backupDeadline = (Get-Date).AddSeconds(120)
    while ((Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_BACKUP_TASK_NAME).State -eq 'Running') {
        if ((Get-Date) -ge $backupDeadline) {
            throw '每日备份任务在 120 秒内未结束，已取消恢复。'
        }
        Start-Sleep -Seconds 1
    }

    & $env:MRV2_PYTHON $env:MRV2_SERVICE --stop
    if ($LASTEXITCODE -ne 0) {
        throw 'service.py 未能通过安装身份校验安全停止服务。'
    }

    & $env:MRV2_PYTHON $env:MRV2_RESTORE --backup $selected.FullName --expected-install-id $installId
    if ($LASTEXITCODE -ne 0) {
        throw 'restore.py 拒绝了备份，或恢复后数据库复检失败。'
    }
    $operationExit = 0
} catch {
    $operationError = $_.Exception.Message
    $operationExit = 1
} finally {
    try {
        if ($mainWasEnabled) {
            Enable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME | Out-Null
        } else {
            Disable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME | Out-Null
        }
    } catch {
        $stateErrors += ('恢复主任务启用状态失败：' + $_.Exception.Message)
    }
    try {
        if ($backupWasEnabled) {
            Enable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_BACKUP_TASK_NAME | Out-Null
        } else {
            Disable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_BACKUP_TASK_NAME | Out-Null
        }
    } catch {
        $stateErrors += ('恢复备份任务启用状态失败：' + $_.Exception.Message)
    }

    if ($serviceWasRunning) {
        try {
            $currentMain = Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
            if ([string]$currentMain.State -eq 'Disabled') {
                Enable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME | Out-Null
            }
            $healthy = $false
            $startIssued = $false
            foreach ($attempt in 1..30) {
                & $env:MRV2_PYTHON $env:MRV2_SERVICE --check *> $null
                if ($LASTEXITCODE -eq 0) {
                    $healthy = $true
                    break
                }
                if (-not $startIssued) {
                    $currentMain = Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
                    if ($currentMain.State -ne 'Running') {
                        Start-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME
                        $startIssued = $true
                    }
                }
                Start-Sleep -Seconds 1
            }
            if (-not $healthy) {
                throw '原本运行的 V2 服务未能在 30 秒内恢复健康状态。'
            }
        } catch {
            $stateErrors += ('恢复服务运行状态失败：' + $_.Exception.Message)
        } finally {
            if (-not $mainWasEnabled) {
                try {
                    Disable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME | Out-Null
                } catch {
                    $stateErrors += ('重新禁用原本禁用的主任务失败：' + $_.Exception.Message)
                }
            }
        }
    }
}

if ($null -ne $operationError) {
    [Console]::Error.WriteLine($operationError)
}
foreach ($stateError in $stateErrors) {
    [Console]::Error.WriteLine($stateError)
}
if ($stateErrors.Count -gt 0) {
    $operationExit = 1
}
exit $operationExit
