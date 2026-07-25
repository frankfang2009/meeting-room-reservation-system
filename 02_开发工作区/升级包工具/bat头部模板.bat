@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统升级

set "MEETING_ROOM_UPGRADE_BAT=%~f0"
set "MEETING_ROOM_UPGRADE_DIRECT_ADMIN="
set "MEETING_ROOM_UPGRADE_BROKER_REQUEST="
set "MEETING_ROOM_UPGRADE_BROKER_RESPONSE="
set "MEETING_ROOM_UPGRADE_BROKER_TOKEN="
set "MEETING_ROOM_UPGRADE_LAUNCH_LOG=%TEMP%\meetingroom_upgrade_launcher.log"

if /i "%~1"=="--upgrade-broker" (
    if "%~2"=="" exit /b 6
    if "%~3"=="" exit /b 6
    if "%~4"=="" exit /b 6
    if not "%~5"=="" exit /b 6
    set "MEETING_ROOM_UPGRADE_BROKER_REQUEST=%~2"
    set "MEETING_ROOM_UPGRADE_BROKER_RESPONSE=%~3"
    set "MEETING_ROOM_UPGRADE_BROKER_TOKEN=%~4"
)

where powershell.exe >nul 2>&1
if not "%errorlevel%"=="0" goto :powershell_unavailable

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $principal=New-Object Security.Principal.WindowsPrincipal($identity); if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}" >nul 2>&1
if not "%errorlevel%"=="0" goto :need_elevation
if not defined MEETING_ROOM_UPGRADE_BROKER_REQUEST set "MEETING_ROOM_UPGRADE_DIRECT_ADMIN=1"
goto :run_upgrade

:need_elevation
echo.
echo 正在请求 Windows 管理员授权，请在弹出的窗口中选择“是”。
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$bat=$env:MEETING_ROOM_UPGRADE_BAT; $tmp=$null; $rc=6; try{$all=[IO.File]::ReadAllText($bat,[Text.Encoding]::UTF8); $broker=[regex]::Matches($all,'(?m)^__UPGRADE_BROKER_PS1_BELOW__\r?$'); $main=[regex]::Matches($all,'(?m)^__UPGRADE_PS1_BELOW__\r?$'); if($broker.Count -ne 1 -or $main.Count -ne 1 -or $broker[0].Index -ge $main[0].Index){throw '升级入口结构损坏'}; $start=$broker[0].Index+$broker[0].Length; if($start -lt $all.Length -and $all[$start] -eq [char]10){$start++}; $length=$main[0].Index-$start; if($length -le 0){throw '升级入口代理为空'}; $tmp=Join-Path $env:TEMP ('meetingroom_upgrade_launcher_{0}.ps1' -f [Guid]::NewGuid().ToString('N')); [IO.File]::WriteAllText($tmp,$all.Substring($start,$length),(New-Object Text.UTF8Encoding($true))); & ([IO.Path]::Combine($PSHOME,'powershell.exe')) -NoProfile -ExecutionPolicy Bypass -File $tmp -PackagePath $bat; $rc=$LASTEXITCODE}catch{Write-Host ''; Write-Host ('升级入口无法启动：'+$_.Exception.Message) -ForegroundColor Red; $rc=6}finally{if($tmp -and (Test-Path -LiteralPath $tmp)){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}; exit $rc"
set "UPGRADE_RC=%errorlevel%"
>>"%MEETING_ROOM_UPGRADE_LAUNCH_LOG%" echo %DATE% %TIME% [BAT] 入口代理退出码=%UPGRADE_RC%
if "%UPGRADE_RC%"=="3" goto :uac_cancelled
if "%UPGRADE_RC%"=="6" goto :elevation_failed
if "%UPGRADE_RC%"=="0" exit /b 0
if "%UPGRADE_RC%"=="1" goto :upgrade_not_completed
if "%UPGRADE_RC%"=="2" goto :upgrade_not_completed
if "%UPGRADE_RC%"=="4" goto :upgrade_not_completed
if "%UPGRADE_RC%"=="5" goto :upgrade_not_completed
goto :unexpected_launcher_failure

:uac_cancelled
echo.
echo 升级未开始，未修改任何文件。
echo.
pause
exit /b 3

:elevation_failed
echo.
echo 无法打开管理员升级窗口，请联系维护人员。
echo 错误详情保存在：
echo "%MEETING_ROOM_UPGRADE_LAUNCH_LOG%"
echo.
pause
exit /b 1

:upgrade_not_completed
echo.
echo 升级没有正常完成，返回代码：%UPGRADE_RC%
echo 如果管理员窗口已经显示具体原因，请按其中提示处理。
echo 入口记录保存在：
echo "%MEETING_ROOM_UPGRADE_LAUNCH_LOG%"
echo.
pause
exit /b %UPGRADE_RC%

:unexpected_launcher_failure
echo.
echo 升级入口异常退出，升级没有正常完成。
echo 错误代码：%UPGRADE_RC%
echo 错误详情保存在：
echo "%MEETING_ROOM_UPGRADE_LAUNCH_LOG%"
echo.
pause
exit /b 1

:powershell_unavailable
echo.
echo 这台电脑无法启动 Windows PowerShell，升级尚未开始。
echo 请联系网管检查 PowerShell、AppLocker 或单位安全策略。
echo.
pause
exit /b 1

:run_upgrade
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$bat=$env:MEETING_ROOM_UPGRADE_BAT; $tmp=$null; $rc=1; try{$all=[IO.File]::ReadAllText($bat,[Text.Encoding]::UTF8); $ps=[regex]::Matches($all,'(?m)^__UPGRADE_PS1_BELOW__\r?$'); $payload=[regex]::Matches($all,'(?m)^__UPGRADE_PAYLOAD_BELOW__\r?$'); if($ps.Count -ne 1 -or $payload.Count -ne 1 -or $ps[0].Index -ge $payload[0].Index){throw '升级文件结构损坏'}; $start=$ps[0].Index+$ps[0].Length; if($start -lt $all.Length -and $all[$start] -eq [char]10){$start++}; $length=$payload[0].Index-$start; if($length -le 0){throw '升级主程序为空'}; $tmp=Join-Path $env:TEMP ('meetingroom_upgrade_{0}.ps1' -f $PID); [IO.File]::WriteAllText($tmp,$all.Substring($start,$length),(New-Object Text.UTF8Encoding($true))); & ([IO.Path]::Combine($PSHOME,'powershell.exe')) -NoProfile -ExecutionPolicy Bypass -File $tmp -PackagePath $bat; $rc=$LASTEXITCODE}catch{Write-Host ''; Write-Host ('升级文件无法读取：'+$_.Exception.Message) -ForegroundColor Red; $rc=1}finally{if($tmp -and (Test-Path -LiteralPath $tmp)){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}; exit $rc"
set "UPGRADE_RC=%errorlevel%"

echo.
if not "%UPGRADE_RC%"=="0" echo 如需帮助，请把“_程序文件\logs”中的最新升级日志交给维护人员。
echo.
pause
exit /b %UPGRADE_RC%
