@echo off
chcp 65001 >nul
title 取消会议室预约系统开机自动启动
cd /d "%~dp0"

net session >nul 2>&1
if "%errorlevel%"=="0" goto :elevated
set "MEETING_ROOM_SETUP_SCRIPT=%~f0"
powershell -NoProfile -Command "try { $p=Start-Process -FilePath $env:MEETING_ROOM_SETUP_SCRIPT -Verb RunAs -Wait -PassThru; exit [int]$p.ExitCode } catch { exit 3 }"
set "CANCEL_EXIT=%errorlevel%"
if "%CANCEL_EXIT%"=="3" (
    echo.
    echo 已取消管理员授权，没有修改任何设置。
    pause
)
exit /b %CANCEL_EXIT%

:elevated

schtasks /End /TN "会议室预约系统" >nul 2>&1

set "MEETING_ROOM_RUNTIME=%~dp0_程序文件\runtime"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=[IO.Path]::GetFullPath($env:MEETING_ROOM_RUNTIME).TrimEnd('\') + '\'; Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

schtasks /Delete /TN "会议室预约系统" /F >nul 2>&1
netsh advfirewall firewall delete rule name="会议室预约系统-手动" >nul 2>&1
netsh advfirewall firewall delete rule name="会议室预约系统-后台" >nul 2>&1

schtasks /Query /TN "会议室预约系统" >nul 2>&1
if "%errorlevel%"=="0" goto :cancel_failed

if exist "_程序文件\runtime\python.exe" if exist "_程序文件\server.py" (
    for /L %%G in (1,1,10) do (
        "_程序文件\runtime\python.exe" "_程序文件\server.py" --check >nul 2>&1
        if errorlevel 1 goto :cancelled
        timeout /t 1 /nobreak >nul
    )
    goto :cancel_failed
)

:cancelled
echo.
echo 已取消开机自动启动，当前后台系统也已停止。
echo 以后仍可双击“① 启动系统.bat”手动启动。
echo.
pause
exit /b 0

:cancel_failed
echo.
echo 取消没有完成，请联系单位电脑管理员。
echo.
pause
exit /b 1
