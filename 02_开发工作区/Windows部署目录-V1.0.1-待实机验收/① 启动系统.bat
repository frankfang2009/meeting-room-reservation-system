@echo off
chcp 65001 >nul
title 会议室预约系统（关闭此窗口即可停止）
cd /d "%~dp0"

if not exist "_程序文件\runtime\python.exe" (
    echo.
    echo 运行环境不完整，请重新解压完整的部署包。
    echo.
    pause
    exit /b 1
)

set "PYTHONUTF8=1"
set "MEETING_ROOM_OPEN_BROWSER=1"
"_程序文件\runtime\python.exe" "_程序文件\server.py"
set "SERVER_EXIT=%errorlevel%"

if "%SERVER_EXIT%"=="10" (
    echo.
    echo 系统本来就在运行，浏览器已经打开。
    timeout /t 3 /nobreak >nul
    exit /b 0
)

if not "%SERVER_EXIT%"=="0" (
    echo.
    echo 系统没有成功启动。请查看上面的提示。
    echo 如果仍然不明白，请把“_程序文件\logs”文件夹交给维护人员。
    echo.
    pause
    exit /b %SERVER_EXIT%
)

echo.
echo 系统已经停止。按任意键关闭窗口。
pause >nul
