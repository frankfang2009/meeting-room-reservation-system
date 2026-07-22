@echo off
chcp 65001 >nul
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    set "MEETING_ROOM_SETUP_SCRIPT=%~f0"
    powershell -NoProfile -Command "Start-Process -FilePath $env:MEETING_ROOM_SETUP_SCRIPT -Verb RunAs"
    exit /b
)

schtasks /End /TN "会议室预约系统" >nul 2>&1
schtasks /Delete /TN "会议室预约系统" /F >nul 2>&1
netsh advfirewall firewall delete rule name="会议室预约系统-手动" >nul 2>&1
netsh advfirewall firewall delete rule name="会议室预约系统-后台" >nul 2>&1

echo.
echo 已取消开机自动启动。
echo 以后仍可双击“① 启动系统.bat”手动启动。
echo.
pause
