@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统 V2.0.0 全新安装

set "INSTALL_TOOL=%~dp0_V2安装工具"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist "%INSTALL_TOOL%\runtime\python.exe" goto :missing
if not exist "%INSTALL_TOOL%\install.py" goto :missing
if not exist "%INSTALL_TOOL%\installer_core.py" goto :missing
if not exist "%INSTALL_TOOL%\manifest.json" goto :missing

"%INSTALL_TOOL%\runtime\python.exe" "%INSTALL_TOOL%\install.py"
set "INSTALL_RC=%errorlevel%"

echo.
if "%INSTALL_RC%"=="0" (
    echo V2.0.0 安装已经完成，请按浏览器提示完成首次设置。
) else if "%INSTALL_RC%"=="3" (
    echo 安装已取消，没有修改 V2 目标目录。
) else if "%INSTALL_RC%"=="4" (
    echo 另一套 V2 安装正在运行，请不要重复打开。
) else if "%INSTALL_RC%"=="5" (
    echo V2 文件已经提交，但启动或检查没有完成。
    echo 请保留安装目录和日志交给维护人员，不要删除可能产生的新数据。
) else if "%INSTALL_RC%"=="6" (
    echo V2 前置事务未能安全回滚，安装现场已经保留。
    echo 请不要覆盖或删除该目录，把安装日志交给维护人员修复。
) else (
    echo 安装没有正常完成，返回代码：%INSTALL_RC%
    echo 安装器不会自动搜索、迁移或删除任何 V1 目录。
)
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b %INSTALL_RC%

:missing
echo.
echo V2 安装工具不完整，请先完整解压收到的 ZIP 后再双击安装。
echo 不要单独复制“安装V2.0.0.bat”。
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b 1
