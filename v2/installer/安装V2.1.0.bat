@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统 V2.1.0 全新安装

set "INSTALL_TOOL=%~dp0_V2安装工具"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist "%INSTALL_TOOL%\" goto :missing_tool
if not exist "%INSTALL_TOOL%\runtime\python.exe" goto :missing_python
if not exist "%INSTALL_TOOL%\app\install.py" goto :missing_input
if not exist "%INSTALL_TOOL%\app\installer_core.py" goto :missing_input
if not exist "%INSTALL_TOOL%\manifest.json" goto :missing_input

set "INSTALL_OUTPUT=%TEMP%\meeting-room-v2-install-%RANDOM%-%RANDOM%.log"
"%INSTALL_TOOL%\runtime\python.exe" "%INSTALL_TOOL%\app\install.py" >"%INSTALL_OUTPUT%" 2>&1
set "INSTALL_RC=%errorlevel%"
if exist "%INSTALL_OUTPUT%" type "%INSTALL_OUTPUT%"
findstr /x /c:"MRV2_INSTALLER_RESULT=%INSTALL_RC%" "%INSTALL_OUTPUT%" >nul 2>&1
set "MARKER_RC=%errorlevel%"
if exist "%INSTALL_OUTPUT%" del /q "%INSTALL_OUTPUT%" >nul 2>&1

if not "%MARKER_RC%"=="0" goto :python_start_failed

if not "%INSTALL_RC%"=="0" if not "%INSTALL_RC%"=="1" if not "%INSTALL_RC%"=="3" if not "%INSTALL_RC%"=="4" if not "%INSTALL_RC%"=="5" if not "%INSTALL_RC%"=="6" goto :python_start_failed

echo.
if "%INSTALL_RC%"=="0" (
    echo MRV2_GATE=PRODUCT_RC_0
    echo V2.1.0 安装已经完成，请按浏览器提示完成首次设置。
) else if "%INSTALL_RC%"=="1" (
    echo MRV2_GATE=PRODUCT_RC_1
    echo 安装包或安装环境未通过产品安全校验，请保留当前输出和安装日志。
) else if "%INSTALL_RC%"=="3" (
    echo MRV2_GATE=PRODUCT_RC_3
    echo 安装已取消，没有修改 V2 目标目录。
) else if "%INSTALL_RC%"=="4" (
    echo MRV2_GATE=PRODUCT_RC_4
    echo 另一套 V2 安装正在运行，请不要重复打开。
) else if "%INSTALL_RC%"=="5" (
    echo MRV2_GATE=PRODUCT_RC_5
    echo V2 文件已经提交，但启动或检查没有完成。
    echo 请保留安装目录和日志交给维护人员，不要删除可能产生的新数据。
) else if "%INSTALL_RC%"=="6" (
    echo MRV2_GATE=PRODUCT_RC_6
    echo V2 前置事务未能安全回滚，安装现场已经保留。
    echo 请不要覆盖或删除该目录，把安装日志交给维护人员修复。
) else (
    echo 安装没有正常完成，返回代码：%INSTALL_RC%
    echo 安装器不会自动搜索、迁移或删除任何 V1 目录。
)
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b %INSTALL_RC%

:missing_tool
echo.
echo MRV2_GATE=MISSING_TOOL_DIR
echo V2 安装工具目录缺失，请先完整解压收到的 ZIP 后再双击安装。
echo 不要单独复制“安装V2.1.0.bat”。
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b 11

:missing_python
echo.
echo MRV2_GATE=MISSING_RUNTIME_PYTHON
echo V2 安装工具缺少冻结 Python runtime，请重新完整解压安装包。
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b 12

:missing_input
echo.
echo MRV2_GATE=MISSING_PRODUCT_INPUT
echo V2 安装工具缺少产品入口、事务核心或清单，请重新获取完整候选包。
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b 13

:python_start_failed
echo.
echo MRV2_GATE=PYTHON_START_FAILED
echo 冻结 Python 未能正常启动，原始返回代码：%INSTALL_RC%
echo 请保留当前输出并提交候选包 SHA-256 和安装日志。
echo.
if not "%MEETING_ROOM_V2_INSTALL_NO_PAUSE%"=="1" pause
exit /b 14
