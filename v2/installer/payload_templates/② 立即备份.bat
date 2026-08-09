@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 备份会议室预约系统 V2
cd /d "%~dp0"

if not exist "_程序文件\runtime\python.exe" goto :missing
if not exist "_程序文件\backup.py" goto :missing
if not exist "_程序文件\data\install_id" goto :missing

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
"_程序文件\runtime\python.exe" "_程序文件\backup.py"
set "BACKUP_EXIT=%errorlevel%"
if not "%BACKUP_EXIT%"=="0" goto :failed

echo.
echo 备份成功，文件位于“_程序文件\backups”。
echo.
pause
exit /b 0

:missing
echo.
echo V2 运行文件或安装身份不完整，请联系维护人员。
echo.
pause
exit /b 1

:failed
echo.
echo 备份没有完成。请保留“_程序文件\data”和“_程序文件\logs”，联系维护人员。
echo.
pause
exit /b %BACKUP_EXIT%
