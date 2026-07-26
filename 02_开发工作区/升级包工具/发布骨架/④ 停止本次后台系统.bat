@echo off
chcp 65001 >nul
title 停止会议室预约系统
cd /d "%~dp0"

net session >nul 2>&1
if "%errorlevel%"=="0" goto :elevated
set "MEETING_ROOM_SETUP_SCRIPT=%~f0"
powershell -NoProfile -Command "try { $p=Start-Process -FilePath $env:MEETING_ROOM_SETUP_SCRIPT -Verb RunAs -Wait -PassThru; exit [int]$p.ExitCode } catch { exit 3 }"
set "STOP_EXIT=%errorlevel%"
if "%STOP_EXIT%"=="3" (
    echo.
    echo 已取消管理员授权，系统没有被停止。
    pause
)
exit /b %STOP_EXIT%

:elevated

if not exist "_程序文件\runtime\python.exe" goto :missing
if not exist "_程序文件\server.py" goto :missing

schtasks /End /TN "会议室预约系统" >nul 2>&1

set "MEETING_ROOM_RUNTIME=%~dp0_程序文件\runtime"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=[IO.Path]::GetFullPath($env:MEETING_ROOM_RUNTIME).TrimEnd('\') + '\'; Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

for /L %%G in (1,1,10) do (
    "_程序文件\runtime\python.exe" "_程序文件\server.py" --check >nul 2>&1
    if errorlevel 1 goto :stopped
    timeout /t 1 /nobreak >nul
)
goto :stop_failed

:stopped
echo.
echo 本次会议室预约系统已经停止。
echo 如果设置过开机自动启动，下次开机时仍会自动运行。
echo.
pause
exit /b 0

:missing
echo.
echo 运行文件不完整，请联系维护人员。
echo.
pause
exit /b 1

:stop_failed
echo.
echo 系统没有完全停止，请联系维护人员。
echo.
pause
exit /b 1
