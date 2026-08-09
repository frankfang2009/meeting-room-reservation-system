@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统 V2（关闭此窗口即可停止手动服务）
cd /d "%~dp0"

if not exist "_程序文件\runtime\python.exe" goto :missing
if not exist "_程序文件\service.py" goto :missing
if not exist "_程序文件\data\install_id" goto :missing

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

"_程序文件\runtime\python.exe" "_程序文件\service.py" --check >nul 2>&1
if not errorlevel 1 goto :already_running

set "MEETING_ROOM_OPEN_BROWSER=1"
"_程序文件\runtime\python.exe" "_程序文件\service.py"
set "SERVICE_EXIT=%errorlevel%"
if "%SERVICE_EXIT%"=="0" goto :stopped

echo.
echo V2 服务没有正常启动，请查看上方提示。
echo 如需协助，请把“_程序文件\logs”文件夹交给维护人员。
echo.
pause
exit /b %SERVICE_EXIT%

:already_running
start "" "http://127.0.0.1:8080/"
echo.
echo V2 系统已经在运行，浏览器已打开。
timeout /t 3 /nobreak >nul
exit /b 0

:stopped
echo.
echo 本次手动 V2 服务已经停止。按任意键关闭窗口。
pause >nul
exit /b 0

:missing
echo.
echo V2 运行文件或安装身份不完整，请联系维护人员。
echo.
pause
exit /b 1
