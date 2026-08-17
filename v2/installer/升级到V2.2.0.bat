@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统 V2.2.0 离线累计升级
cd /d "%~dp0"

set "UPDATE_TOOL=%~dp0_V2更新工具"
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
    echo V2.2.0 升级已完成。
) else (
    echo 升级未完成，请依照上方提示处理。
)
pause
exit /b %UPDATE_RC%

:missing_tool
echo MRV2_UPDATE_GATE=MISSING_TOOL_DIR
echo 缺少“_V2更新工具”目录。请先完整解压 ZIP，不要从压缩包预览或单独复制 BAT 运行。
pause
exit /b 11

:missing_runtime
echo MRV2_UPDATE_GATE=MISSING_RUNTIME_PYTHON
echo 更新包中的冻结 Python 缺失，请重新完整解压已校验的原始 ZIP。
pause
exit /b 12

:missing_input
echo MRV2_UPDATE_GATE=MISSING_PRODUCT_INPUT
echo 更新包不完整，未执行任何现场更改。
pause
exit /b 13

:python_failed
echo MRV2_UPDATE_GATE=PYTHON_START_FAILED
echo Python 未能运行到可验证的更新器收尾。请保留下方日志并联系维护人员：
echo %UPDATE_OUTPUT%
pause
exit /b 14
