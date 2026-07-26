@echo off
chcp 65001 >nul
cd /d "%~dp0"

net session >nul 2>&1
if "%errorlevel%"=="0" goto :elevated
set "MEETING_ROOM_SETUP_SCRIPT=%~f0"
powershell -NoProfile -Command "try { $p=Start-Process -FilePath $env:MEETING_ROOM_SETUP_SCRIPT -Verb RunAs -Wait -PassThru; exit [int]$p.ExitCode } catch { exit 3 }"
set "SETUP_EXIT=%errorlevel%"
if "%SETUP_EXIT%"=="3" (
    echo.
    echo 已取消管理员授权，没有修改任何设置。
    pause
)
exit /b %SETUP_EXIT%

:elevated

if not exist "_程序文件\runtime\python.exe" goto :missing
if not exist "_程序文件\runtime\pythonw.exe" goto :missing
if not exist "_程序文件\server.py" goto :missing

set "TASK_NAME=会议室预约系统"
set "MEETING_ROOM_TASK_PYTHONW=%~dp0_程序文件\runtime\pythonw.exe"
set "MEETING_ROOM_TASK_SERVER=%~dp0_程序文件\server.py"
set "MEETING_ROOM_TASK_WORKDIR=%~dp0_程序文件"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$action=New-ScheduledTaskAction -Execute $env:MEETING_ROOM_TASK_PYTHONW -Argument ([char]34 + $env:MEETING_ROOM_TASK_SERVER + [char]34) -WorkingDirectory $env:MEETING_ROOM_TASK_WORKDIR; $trigger=New-ScheduledTaskTrigger -AtStartup; $principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; Register-ScheduledTask -TaskName $env:TASK_NAME -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null"
if not "%errorlevel%"=="0" (
    echo 设置失败，请联系单位电脑管理员。
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="会议室预约系统-手动" >nul 2>&1
netsh advfirewall firewall delete rule name="会议室预约系统-后台" >nul 2>&1
netsh advfirewall firewall add rule name="会议室预约系统-手动" dir=in action=allow program="%~dp0_程序文件\runtime\python.exe" protocol=TCP localport=8080 profile=private,domain remoteip=localsubnet >nul
if not "%errorlevel%"=="0" goto :firewall_failed
netsh advfirewall firewall add rule name="会议室预约系统-后台" dir=in action=allow program="%~dp0_程序文件\runtime\pythonw.exe" protocol=TCP localport=8080 profile=private,domain remoteip=localsubnet >nul
if not "%errorlevel%"=="0" goto :firewall_failed

"_程序文件\runtime\python.exe" "_程序文件\server.py" --check >nul 2>&1
if "%errorlevel%"=="0" goto :already_running

schtasks /Run /TN "%TASK_NAME%" >nul 2>&1
if not "%errorlevel%"=="0" goto :run_failed

for /L %%G in (1,1,8) do (
    timeout /t 1 /nobreak >nul
    "_程序文件\runtime\python.exe" "_程序文件\server.py" --check >nul 2>&1
    if not errorlevel 1 goto :started
)
goto :run_failed

:started
echo.
echo 已设置完成。以后电脑开机时，系统会自动启动。
echo 现有账号、会议室和预约数据都不会改变。
echo 局域网地址可能随网络环境变化，不能保证一直使用原地址。
echo 请双击“① 启动系统.bat”打开浏览器，以管理员页面显示的当前地址为准。
echo 如果地址发生变化，页面会持续提醒您复制新地址发给同事。
echo.
pause
exit /b 0

:already_running
echo.
echo 开机自动启动已经设置好。
echo 当前系统本来就在运行，不需要重复启动。
echo 下次电脑开机时，它会自动在后台运行。
echo 请以管理员页面显示的当前“同事访问”地址为准。
echo 如果地址发生变化，页面会持续提醒您复制新地址发给同事。
echo.
pause
exit /b 0

:missing
echo.
echo 运行文件不完整，请重新解压完整的部署包。
echo.
pause
exit /b 1

:firewall_failed
echo.
echo 开机任务已创建，但 Windows 没有允许局域网访问。
echo 请联系单位电脑管理员检查防火墙或组策略。
echo.
pause
exit /b 1

:run_failed
echo.
echo 开机任务已创建，但系统没有在后台成功启动。
echo 请先双击“① 启动系统.bat”查看具体提示，
echo 或把“_程序文件\logs”文件夹交给维护人员。
echo.
pause
exit /b 1
