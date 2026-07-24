@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统升级

set "MEETING_ROOM_UPGRADE_BAT=%~f0"
set "MEETING_ROOM_UPGRADE_DIRECT_ADMIN="
set "MEETING_ROOM_UPGRADE_BROKER_REQUEST="
set "MEETING_ROOM_UPGRADE_BROKER_RESPONSE="
set "MEETING_ROOM_UPGRADE_BROKER_TOKEN="

if /i "%~1"=="--upgrade-broker" (
    if "%~2"=="" exit /b 6
    if "%~3"=="" exit /b 6
    if "%~4"=="" exit /b 6
    if not "%~5"=="" exit /b 6
    set "MEETING_ROOM_UPGRADE_BROKER_REQUEST=%~2"
    set "MEETING_ROOM_UPGRADE_BROKER_RESPONSE=%~3"
    set "MEETING_ROOM_UPGRADE_BROKER_TOKEN=%~4"
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $principal=New-Object Security.Principal.WindowsPrincipal($identity); if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}" >nul 2>&1
if not "%errorlevel%"=="0" goto :need_elevation
if not defined MEETING_ROOM_UPGRADE_BROKER_REQUEST set "MEETING_ROOM_UPGRADE_DIRECT_ADMIN=1"
goto :run_upgrade

:need_elevation
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$brokerRoot=$null; $child=$null; $elevationStarted=$false; try{$ErrorActionPreference='Stop'; $brokerRoot=Join-Path $env:TEMP ('meetingroom_upgrade_broker_'+[Guid]::NewGuid().ToString('N')); [IO.Directory]::CreateDirectory($brokerRoot)|Out-Null; $request=Join-Path $brokerRoot 'request.json'; $response=Join-Path $brokerRoot 'response.json'; $token=[Guid]::NewGuid().ToString('N'); $brokerArguments='--upgrade-broker '+[char]34+$request+[char]34+' '+[char]34+$response+[char]34+' '+[char]34+$token+[char]34; $child=Start-Process -FilePath $env:MEETING_ROOM_UPGRADE_BAT -ArgumentList $brokerArguments -Verb RunAs -PassThru -ErrorAction Stop; if($null -eq $child -or [int]$child.Id -le 0){throw '管理员升级进程启动后没有有效进程 ID'}; $elevationStarted=$true; while(-not $child.HasExited){if(Test-Path -LiteralPath $request -PathType Leaf){$launched=$null; $launchedId=0; try{$requestItem=Get-Item -LiteralPath $request -Force; if(($requestItem.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0 -or $requestItem.Length-gt 4096 -or (Test-Path -LiteralPath $response)){throw '启动请求文件异常'}; $raw=[IO.File]::ReadAllText($request,(New-Object Text.UTF8Encoding($false,$true))); $job=$raw|ConvertFrom-Json; $names=@($job.PSObject.Properties.Name|Sort-Object); if(($names-join ',') -ne 'python_path,schema,server_path,token,working_directory' -or [int]$job.schema -ne 1 -or -not [string]::Equals([string]$job.token,$token,[StringComparison]::Ordinal)){throw '启动请求校验失败'}; $work=[IO.Path]::GetFullPath([string]$job.working_directory).TrimEnd('\'); $python=[IO.Path]::GetFullPath([string]$job.python_path); $server=[IO.Path]::GetFullPath([string]$job.server_path); if(-not [string]::Equals($python,(Join-Path $work 'runtime\python.exe'),[StringComparison]::OrdinalIgnoreCase)-or -not [string]::Equals($server,(Join-Path $work 'server.py'),[StringComparison]::OrdinalIgnoreCase)-or -not(Test-Path -LiteralPath $python -PathType Leaf)-or -not(Test-Path -LiteralPath $server -PathType Leaf)){throw '启动路径校验失败'}; $info=New-Object Diagnostics.ProcessStartInfo; $info.FileName=$python; $info.Arguments=[char]34+$server+[char]34; $info.WorkingDirectory=$work; $info.UseShellExecute=$true; $info.WindowStyle=[Diagnostics.ProcessWindowStyle]::Minimized; $launched=New-Object Diagnostics.Process; $launched.StartInfo=$info; if(-not $launched.Start()){throw '普通用户服务进程未能启动'}; $launchedId=[int]$launched.Id; if($launchedId -le 0){throw '普通用户服务进程启动后没有有效进程 ID'}; $reply=[ordered]@{schema=1;token=$token;ok=$true;process_id=$launchedId;error=$null}}catch{$launchError=[string]$_.Exception.Message; if($launchedId -gt 0){Stop-Process -Id $launchedId -Force -ErrorAction SilentlyContinue; $launchedId=0}; $reply=[ordered]@{schema=1;token=$token;ok=$false;process_id=0;error=$launchError}}; $responseTemp=$response+'.tmp.'+$PID; try{[IO.File]::WriteAllText($responseTemp,($reply|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false))); [IO.File]::Move($responseTemp,$response)}catch{if($launchedId -gt 0){Stop-Process -Id $launchedId -Force -ErrorAction SilentlyContinue}; throw}finally{if($null -ne $launched){$launched.Dispose()}}; Remove-Item -LiteralPath $request -Force -ErrorAction SilentlyContinue}; Start-Sleep -Milliseconds 200; $child.Refresh()}; $child.WaitForExit(); exit([int]$child.ExitCode)}catch{$exception=$_.Exception; $nativeCode=$null; while($null -ne $exception){if($exception -is [System.ComponentModel.Win32Exception]){$nativeCode=$exception.NativeErrorCode; break}; $exception=$exception.InnerException}; if(-not $elevationStarted -and $nativeCode -eq 1223){exit 3}else{exit 6}}finally{if($null -ne $child){$child.Dispose()}; if($brokerRoot -and (Test-Path -LiteralPath $brokerRoot)){Remove-Item -LiteralPath $brokerRoot -Recurse -Force -ErrorAction SilentlyContinue}}"
set "UPGRADE_RC=%errorlevel%"
if "%UPGRADE_RC%"=="3" goto :uac_cancelled
if "%UPGRADE_RC%"=="6" goto :elevation_failed
exit /b %UPGRADE_RC%

:uac_cancelled
echo.
echo 升级未开始，未修改任何文件。
echo.
pause
exit /b 3

:elevation_failed
echo.
echo 无法打开管理员升级窗口，请联系维护人员。
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
