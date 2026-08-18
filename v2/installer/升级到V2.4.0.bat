@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统 V2.4.0 离线累计升级
cd /d "%~dp0"

set "UPDATE_TOOL=%~dp0_V2更新工具"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist "%UPDATE_TOOL%\" goto :missing_tool
if not exist "%UPDATE_TOOL%\runtime\python.exe" goto :missing_runtime
if not exist "%UPDATE_TOOL%\app\update.py" goto :missing_input
if not exist "%UPDATE_TOOL%\app\update_core.py" goto :missing_input
if not exist "%UPDATE_TOOL%\app\installer_core.py" goto :missing_input
if not exist "%UPDATE_TOOL%\manifest.json" goto :missing_input
if not exist "%UPDATE_TOOL%\payload-update.zip" goto :missing_input

set "UPDATE_OUTPUT=%TEMP%\meeting-room-v2-update-%RANDOM%-%RANDOM%.log"
"%UPDATE_TOOL%\runtime\python.exe" "%UPDATE_TOOL%\app\update.py" >"%UPDATE_OUTPUT%" 2>&1
set "UPDATE_RC=%ERRORLEVEL%"
type "%UPDATE_OUTPUT%"
findstr /x /c:"MRV2_UPDATER_RESULT=%UPDATE_RC%" "%UPDATE_OUTPUT%" >nul
if errorlevel 1 goto :python_failed
del /q "%UPDATE_OUTPUT%" >nul 2>&1
echo.
if "%UPDATE_RC%"=="0" (
    echo MRV2_UPDATE_GATE=PRODUCT_RC_0
    echo V2.4.0 升级已完成。
) else if "%UPDATE_RC%"=="1" (
    echo MRV2_UPDATE_GATE=PRODUCT_RC_1
    echo 升级包或现场未通过产品安全校验；未提交前更新器已回滚，不会留下半更新状态。
) else if "%UPDATE_RC%"=="6" (
    echo MRV2_UPDATE_GATE=PRODUCT_RC_6
    echo V2 更新未能安全收尾；请保留 _程序文件\logs 与回滚材料联系维护人员。
) else (
    echo 升级没有正常完成，返回代码：%UPDATE_RC%
)
echo.
if not "%MEETING_ROOM_V2_UPDATE_NO_PAUSE%"=="1" pause
exit /b %UPDATE_RC%

:missing_tool
echo.
echo MRV2_UPDATE_GATE=MISSING_TOOL_DIR
echo 缺少“_V2更新工具”目录。请先完整解压 ZIP，不要从压缩包预览或单独复制 BAT 运行。
echo.
if not "%MEETING_ROOM_V2_UPDATE_NO_PAUSE%"=="1" pause
exit /b 11

:missing_runtime
echo.
echo MRV2_UPDATE_GATE=MISSING_RUNTIME_PYTHON
echo 更新包中的冻结 Python 缺失，请重新完整解压已校验的原始 ZIP。
echo.
if not "%MEETING_ROOM_V2_UPDATE_NO_PAUSE%"=="1" pause
exit /b 12

:missing_input
echo.
echo MRV2_UPDATE_GATE=MISSING_PRODUCT_INPUT
echo 更新包不完整，未执行任何现场更改。
echo.
if not "%MEETING_ROOM_V2_UPDATE_NO_PAUSE%"=="1" pause
exit /b 13

:python_failed
echo.
echo MRV2_UPDATE_GATE=PYTHON_START_FAILED
echo Python 未能运行到可验证的更新器收尾。请保留下方日志并联系维护人员：
echo %UPDATE_OUTPUT%
echo.
if not "%MEETING_ROOM_V2_UPDATE_NO_PAUSE%"=="1" pause
exit /b 14
