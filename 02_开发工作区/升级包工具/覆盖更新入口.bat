@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统 V1.0.2 修复更新

set "UPDATE_TOOL=%~dp0_V1.0.2更新工具"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist "%UPDATE_TOOL%\runtime\python.exe" goto :missing
if not exist "%UPDATE_TOOL%\update.py" goto :missing
if not exist "%UPDATE_TOOL%\manifest.json" goto :missing

"%UPDATE_TOOL%\runtime\python.exe" "%UPDATE_TOOL%\update.py"
set "UPDATE_RC=%errorlevel%"

echo.
if "%UPDATE_RC%"=="0" (
    echo 修复更新已经完成。
    echo 请回到会议室预约系统文件夹，
    echo 双击“① 启动系统.bat”。
) else if "%UPDATE_RC%"=="3" (
    echo 已取消 Windows 管理员授权，系统没有被修改。
) else if "%UPDATE_RC%"=="4" (
    echo 另一个修复更新正在运行，请不要重复打开。
) else (
    echo 修复更新没有正常完成，返回代码：%UPDATE_RC%
    echo 请不要删除旧系统文件，把窗口中的提示或更新日志交给维护人员。
)
echo.
if not "%MEETING_ROOM_UPDATE_NO_PAUSE%"=="1" pause
exit /b %UPDATE_RC%

:missing
echo.
echo 修复更新工具不完整，请重新解压维护人员发送的完整 ZIP。
echo 不要单独复制这个 BAT。
echo.
if not "%MEETING_ROOM_UPDATE_NO_PAUSE%"=="1" pause
exit /b 1
