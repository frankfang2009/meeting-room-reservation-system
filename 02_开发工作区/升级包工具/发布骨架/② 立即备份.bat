@echo off
chcp 65001 >nul
title 备份会议室预约系统
cd /d "%~dp0"

if not exist "_程序文件\runtime\python.exe" (
    echo 运行环境不完整，请重新解压完整的部署包。
    pause
    exit /b 1
)

set "PYTHONUTF8=1"
"_程序文件\runtime\python.exe" "_程序文件\backup.py"
if not "%errorlevel%"=="0" (
    echo.
    echo 备份没有完成。请确认系统文件夹可以正常写入，然后再试一次。
    echo 如果仍然失败，请把“_程序文件\logs”文件夹交给维护人员。
    echo.
    pause
    exit /b 1
)
echo.
echo 备份成功。文件在“_程序文件\backups”文件夹里。
pause
