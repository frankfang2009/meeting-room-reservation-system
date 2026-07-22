@echo off
chcp 65001 >nul
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    set "MEETING_ROOM_SETUP_SCRIPT=%~f0"
    powershell -NoProfile -Command "Start-Process -FilePath $env:MEETING_ROOM_SETUP_SCRIPT -Verb RunAs"
    exit /b
)

schtasks /Query /TN "会议室预约系统" >nul 2>&1
if not "%errorlevel%"=="0" (
    echo.
    echo 这台电脑还没有设置开机自动启动。
    echo 如果系统是双击启动的，请直接关闭黑色系统窗口。
    echo.
    pause
    exit /b 0
)

schtasks /End /TN "会议室预约系统" >nul 2>&1
echo.
echo 本次后台系统已经停止。
echo 下次电脑开机时，系统仍会自动启动。
echo.
pause
