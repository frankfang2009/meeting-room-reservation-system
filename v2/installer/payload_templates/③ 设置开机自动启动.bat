@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 设置会议室预约系统 V2 开机自动启动
cd /d "%~dp0"

powershell.exe -NoProfile -NonInteractive -Command "if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :elevated
set "MRV2_ELEVATE_SCRIPT=%~f0"
powershell.exe -NoProfile -NonInteractive -Command "try { $p=Start-Process -FilePath $env:MRV2_ELEVATE_SCRIPT -Verb RunAs -Wait -PassThru; exit [int]$p.ExitCode } catch { exit 3 }"
set "SETUP_EXIT=%errorlevel%"
if "%SETUP_EXIT%"=="3" (
    echo.
    echo 已取消管理员授权，没有修改 V2 设置。
    pause
)
exit /b %SETUP_EXIT%

:elevated
if not exist "_程序文件\runtime\python.exe" goto :missing
if not exist "_程序文件\service.py" goto :missing
if not exist "_程序文件\data\install.json" goto :missing
set "MRV2_ROOT=%~dp0"
set "MRV2_INFO=%~dp0_程序文件\data\install.json"
set "MRV2_REGISTRY=HKLM:\Software\MeetingRoomReservationV2"
set "MRV2_TASK_NAME=会议室预约系统 V2"
set "MRV2_FW_MANUAL=会议室预约系统V2-手动"
set "MRV2_FW_BACKGROUND=会议室预约系统V2-后台"

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=[IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\'); $info=Get-Content -LiteralPath $env:MRV2_INFO -Raw -Encoding UTF8 | ConvertFrom-Json; if ([int]$info.product_generation -ne 2 -or [string]::IsNullOrWhiteSpace([string]$info.install_id)) { throw 'V2 安装身份无效。' }; $identity='MeetingRoomReservationV2:' + [string]$info.install_id; $registered=Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY -ErrorAction Stop; if ([string]$registered.InstallRoot -ne $root -or [string]$registered.InstallId -ne [string]$info.install_id) { throw 'V2 安装登记不属于当前目录。' }; $task=Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop; if ([string]$task.Description -ne $identity) { throw 'V2 计划任务不属于当前安装。' }; foreach ($name in @($env:MRV2_FW_MANUAL,$env:MRV2_FW_BACKGROUND)) { $rules=@(Get-NetFirewallRule -DisplayName $name -ErrorAction Stop); if ($rules.Count -ne 1 -or [string]$rules[0].Description -ne $identity) { throw ('V2 防火墙规则不属于当前安装：' + $name) } }; Enable-NetFirewallRule -DisplayName $env:MRV2_FW_MANUAL; Enable-NetFirewallRule -DisplayName $env:MRV2_FW_BACKGROUND; Enable-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME | Out-Null; if ((Get-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME).State -ne 'Running') { Start-ScheduledTask -TaskPath '\' -TaskName $env:MRV2_TASK_NAME }"
if errorlevel 1 goto :failed

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
for /L %%G in (1,1,15) do (
    "_程序文件\runtime\python.exe" "_程序文件\service.py" --check >nul 2>&1
    if not errorlevel 1 goto :started
    timeout /t 1 /nobreak >nul
)
goto :failed

:started
echo.
echo V2 开机自动启动已经启用，当前服务也已启动。
echo.
pause
exit /b 0

:missing
echo.
echo V2 运行文件或安装身份不完整，请联系维护人员。
echo.
pause
exit /b 1

:failed
echo.
echo V2 开机自动启动没有安全启用；没有覆盖其他任务或防火墙规则。
echo 请把“_程序文件\logs”交给维护人员。
echo.
pause
exit /b 1
