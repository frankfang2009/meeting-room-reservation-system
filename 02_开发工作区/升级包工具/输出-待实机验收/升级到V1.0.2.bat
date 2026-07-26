@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统升级

set "MEETING_ROOM_UPGRADE_BAT=%~f0"
set "MEETING_ROOM_UPGRADE_DIRECT_ADMIN="
set "MEETING_ROOM_UPGRADE_BROKER_REQUEST="
set "MEETING_ROOM_UPGRADE_BROKER_RESPONSE="
set "MEETING_ROOM_UPGRADE_BROKER_TOKEN="
set "MEETING_ROOM_UPGRADE_LAUNCH_LOG=%TEMP%\meetingroom_upgrade_launcher.log"

if /i "%~1"=="--upgrade-broker" (
    if "%~2"=="" exit /b 6
    if "%~3"=="" exit /b 6
    if "%~4"=="" exit /b 6
    if not "%~5"=="" exit /b 6
    set "MEETING_ROOM_UPGRADE_BROKER_REQUEST=%~2"
    set "MEETING_ROOM_UPGRADE_BROKER_RESPONSE=%~3"
    set "MEETING_ROOM_UPGRADE_BROKER_TOKEN=%~4"
)

where powershell.exe >nul 2>&1
if not "%errorlevel%"=="0" goto :powershell_unavailable

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $principal=New-Object Security.Principal.WindowsPrincipal($identity); if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}" >nul 2>&1
if not "%errorlevel%"=="0" goto :need_elevation
if not defined MEETING_ROOM_UPGRADE_BROKER_REQUEST set "MEETING_ROOM_UPGRADE_DIRECT_ADMIN=1"
goto :run_upgrade

:need_elevation
echo.
echo 正在请求 Windows 管理员授权，请在弹出的窗口中选择“是”。
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$bat=$env:MEETING_ROOM_UPGRADE_BAT; $tmp=$null; $rc=6; try{$all=[IO.File]::ReadAllText($bat,[Text.Encoding]::UTF8); $broker=[regex]::Matches($all,'(?m)^__UPGRADE_BROKER_PS1_BELOW__\r?$'); $main=[regex]::Matches($all,'(?m)^__UPGRADE_PS1_BELOW__\r?$'); if($broker.Count -ne 1 -or $main.Count -ne 1 -or $broker[0].Index -ge $main[0].Index){throw '升级入口结构损坏'}; $start=$broker[0].Index+$broker[0].Length; if($start -lt $all.Length -and $all[$start] -eq [char]10){$start++}; $length=$main[0].Index-$start; if($length -le 0){throw '升级入口代理为空'}; $tmp=Join-Path $env:TEMP ('meetingroom_upgrade_launcher_{0}.ps1' -f [Guid]::NewGuid().ToString('N')); [IO.File]::WriteAllText($tmp,$all.Substring($start,$length),(New-Object Text.UTF8Encoding($true))); & ([IO.Path]::Combine($PSHOME,'powershell.exe')) -NoProfile -ExecutionPolicy Bypass -File $tmp -PackagePath $bat; $rc=$LASTEXITCODE}catch{Write-Host ''; Write-Host ('升级入口无法启动：'+$_.Exception.Message) -ForegroundColor Red; $rc=6}finally{if($tmp -and (Test-Path -LiteralPath $tmp)){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}; exit $rc"
set "UPGRADE_RC=%errorlevel%"
>>"%MEETING_ROOM_UPGRADE_LAUNCH_LOG%" echo %DATE% %TIME% [BAT] 入口代理退出码=%UPGRADE_RC%
if "%UPGRADE_RC%"=="3" goto :uac_cancelled
if "%UPGRADE_RC%"=="6" goto :elevation_failed
if "%UPGRADE_RC%"=="0" exit /b 0
if "%UPGRADE_RC%"=="1" goto :upgrade_not_completed
if "%UPGRADE_RC%"=="2" goto :upgrade_not_completed
if "%UPGRADE_RC%"=="4" goto :upgrade_not_completed
if "%UPGRADE_RC%"=="5" goto :upgrade_not_completed
goto :unexpected_launcher_failure

:uac_cancelled
echo.
echo 升级未开始，未修改任何文件。
echo.
pause
exit /b 3

:elevation_failed
echo.
echo 无法打开管理员升级窗口，请联系维护人员。
echo 错误详情保存在：
echo "%MEETING_ROOM_UPGRADE_LAUNCH_LOG%"
echo.
pause
exit /b 1

:upgrade_not_completed
echo.
echo 升级没有正常完成，返回代码：%UPGRADE_RC%
echo 如果管理员窗口已经显示具体原因，请按其中提示处理。
echo 入口记录保存在：
echo "%MEETING_ROOM_UPGRADE_LAUNCH_LOG%"
echo.
pause
exit /b %UPGRADE_RC%

:unexpected_launcher_failure
echo.
echo 升级入口异常退出，升级没有正常完成。
echo 错误代码：%UPGRADE_RC%
echo 错误详情保存在：
echo "%MEETING_ROOM_UPGRADE_LAUNCH_LOG%"
echo.
pause
exit /b 1

:powershell_unavailable
echo.
echo 这台电脑无法启动 Windows PowerShell，升级尚未开始。
echo 请联系网管检查 PowerShell、AppLocker 或单位安全策略。
echo.
pause
exit /b 1

:run_upgrade
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$bat=$env:MEETING_ROOM_UPGRADE_BAT; $tmp=$null; $rc=1; try{$all=[IO.File]::ReadAllText($bat,[Text.Encoding]::UTF8); $ps=[regex]::Matches($all,'(?m)^__UPGRADE_PS1_BELOW__\r?$'); $payload=[regex]::Matches($all,'(?m)^__UPGRADE_PAYLOAD_BELOW__\r?$'); if($ps.Count -ne 1 -or $payload.Count -ne 1 -or $ps[0].Index -ge $payload[0].Index){throw '升级文件结构损坏'}; $start=$ps[0].Index+$ps[0].Length; if($start -lt $all.Length -and $all[$start] -eq [char]10){$start++}; $length=$payload[0].Index-$start; if($length -le 0){throw '升级主程序为空'}; $tmp=Join-Path $env:TEMP ('meetingroom_upgrade_{0}.ps1' -f $PID); [IO.File]::WriteAllText($tmp,$all.Substring($start,$length),(New-Object Text.UTF8Encoding($true))); & ([IO.Path]::Combine($PSHOME,'powershell.exe')) -NoProfile -ExecutionPolicy Bypass -File $tmp -PackagePath $bat; $rc=$LASTEXITCODE}catch{Write-Host ''; Write-Host ('升级文件无法读取：'+$_.Exception.Message) -ForegroundColor Red; $rc=1}finally{if($tmp -and (Test-Path -LiteralPath $tmp)){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}; exit $rc"
set "UPGRADE_RC=%errorlevel%"

echo.
if not "%UPGRADE_RC%"=="0" echo 如需帮助，请把“_程序文件\logs”中的最新升级日志交给维护人员。
echo.
pause
exit /b %UPGRADE_RC%
__UPGRADE_BROKER_PS1_BELOW__
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:LauncherLogPath = Join-Path $env:TEMP 'meetingroom_upgrade_launcher.log'
$script:Utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-LauncherLog {
    param(
        [string]$Message,
        [string]$Level = 'INFO'
    )
    $line = '{0} [{1}] {2}' -f @(
        (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'),
        $Level,
        $Message
    )
    try {
        Add-Content -LiteralPath $script:LauncherLogPath -Value $line `
            -Encoding UTF8 -ErrorAction Stop
    }
    catch {
        Write-Debug "升级入口日志写入失败：$($_.Exception.Message)"
    }
}

function Get-Win32NativeErrorCode {
    param($Exception)
    $current = $Exception
    while ($null -ne $current) {
        if ($current -is [System.ComponentModel.Win32Exception]) {
            return [int]$current.NativeErrorCode
        }
        $current = $current.InnerException
    }
    return $null
}

$brokerRoot = $null
$child = $null
$elevationStarted = $false

try {
    $packageFullPath = [IO.Path]::GetFullPath($PackagePath)
    if (-not (Test-Path -LiteralPath $packageFullPath -PathType Leaf)) {
        throw '升级包文件不存在。'
    }
    if (-not [string]::Equals(
        [IO.Path]::GetExtension($packageFullPath),
        '.bat',
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw '升级包文件扩展名不是 .bat。'
    }

    Write-LauncherLog "普通用户升级入口已启动：$packageFullPath"
    $brokerRoot = Join-Path $env:TEMP (
        'meetingroom_upgrade_broker_' + [Guid]::NewGuid().ToString('N')
    )
    [IO.Directory]::CreateDirectory($brokerRoot) | Out-Null
    $request = Join-Path $brokerRoot 'request.json'
    $response = Join-Path $brokerRoot 'response.json'
    $token = [Guid]::NewGuid().ToString('N')
    $brokerArguments = '--upgrade-broker ' +
        [char]34 + $request + [char]34 + ' ' +
        [char]34 + $response + [char]34 + ' ' +
        [char]34 + $token + [char]34

    Write-LauncherLog '正在请求 Windows 管理员授权。'
    $child = Start-Process -FilePath $packageFullPath `
        -ArgumentList $brokerArguments -Verb RunAs -PassThru -ErrorAction Stop
    if ($null -eq $child -or [int]$child.Id -le 0) {
        throw '管理员升级进程启动后没有有效进程 ID。'
    }
    $elevationStarted = $true
    Write-LauncherLog "管理员升级进程已启动，PID=$([int]$child.Id)"

    while (-not $child.HasExited) {
        if (Test-Path -LiteralPath $request -PathType Leaf) {
            $launched = $null
            $launchedId = 0
            try {
                $requestItem = Get-Item -LiteralPath $request -Force
                if (($requestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                    $requestItem.Length -gt 4096 -or
                    (Test-Path -LiteralPath $response)) {
                    throw '启动请求文件异常。'
                }
                $raw = [IO.File]::ReadAllText($request, $script:Utf8Strict)
                $job = $raw | ConvertFrom-Json
                $names = @($job.PSObject.Properties.Name | Sort-Object)
                if (($names -join ',') -ne
                        'python_path,schema,server_path,token,working_directory' -or
                    [int]$job.schema -ne 1 -or
                    -not [string]::Equals(
                        [string]$job.token,
                        $token,
                        [StringComparison]::Ordinal
                    )) {
                    throw '启动请求校验失败。'
                }

                $work = [IO.Path]::GetFullPath(
                    [string]$job.working_directory
                ).TrimEnd('\')
                $python = [IO.Path]::GetFullPath([string]$job.python_path)
                $server = [IO.Path]::GetFullPath([string]$job.server_path)
                if (-not [string]::Equals(
                        $python,
                        (Join-Path $work 'runtime\python.exe'),
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    -not [string]::Equals(
                        $server,
                        (Join-Path $work 'server.py'),
                        [StringComparison]::OrdinalIgnoreCase
                    ) -or
                    -not (Test-Path -LiteralPath $python -PathType Leaf) -or
                    -not (Test-Path -LiteralPath $server -PathType Leaf)) {
                    throw '启动路径校验失败。'
                }

                $info = New-Object Diagnostics.ProcessStartInfo
                $info.FileName = $python
                $info.Arguments = [char]34 + $server + [char]34
                $info.WorkingDirectory = Split-Path -Parent $python
                $info.UseShellExecute = $true
                $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Minimized
                $launched = New-Object Diagnostics.Process
                $launched.StartInfo = $info
                if (-not $launched.Start()) {
                    throw '普通用户服务进程未能启动。'
                }
                $launchedId = [int]$launched.Id
                if ($launchedId -le 0) {
                    throw '普通用户服务进程启动后没有有效进程 ID。'
                }
                Write-LauncherLog "已按普通用户身份恢复服务，PID=$launchedId"
                $reply = [ordered]@{
                    schema = 1
                    token = $token
                    ok = $true
                    process_id = $launchedId
                    error = $null
                }
            }
            catch {
                $launchError = [string]$_.Exception.Message
                Write-LauncherLog "普通用户服务恢复失败：$launchError" 'ERROR'
                if ($launchedId -gt 0) {
                    Stop-Process -Id $launchedId -Force `
                        -ErrorAction SilentlyContinue
                    $launchedId = 0
                }
                $reply = [ordered]@{
                    schema = 1
                    token = $token
                    ok = $false
                    process_id = 0
                    error = $launchError
                }
            }

            $responseTemp = $response + '.tmp.' + $PID
            try {
                [IO.File]::WriteAllText(
                    $responseTemp,
                    ($reply | ConvertTo-Json -Compress),
                    $script:Utf8NoBom
                )
                [IO.File]::Move($responseTemp, $response)
            }
            catch {
                if ($launchedId -gt 0) {
                    Stop-Process -Id $launchedId -Force `
                        -ErrorAction SilentlyContinue
                }
                throw
            }
            finally {
                if ($null -ne $launched) {
                    $launched.Dispose()
                }
            }
            Remove-Item -LiteralPath $request -Force `
                -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 200
        $child.Refresh()
    }

    $child.WaitForExit()
    $childExitCode = [int]$child.ExitCode
    Write-LauncherLog "管理员升级进程已退出，退出码=$childExitCode"
    exit $childExitCode
}
catch {
    $nativeCode = Get-Win32NativeErrorCode -Exception $_.Exception
    Write-LauncherLog (
        '升级入口失败：{0}；Win32={1}' -f @(
            $_.Exception.ToString(),
            $(if ($null -eq $nativeCode) { '无' } else { $nativeCode })
        )
    ) 'ERROR'
    if (-not $elevationStarted -and $nativeCode -eq 1223) {
        exit 3
    }
    exit 6
}
finally {
    if ($null -ne $child) {
        $child.Dispose()
    }
    if ($brokerRoot -and (Test-Path -LiteralPath $brokerRoot)) {
        Remove-Item -LiteralPath $brokerRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}
__UPGRADE_PS1_BELOW__
param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PackageVersionText = '1.0.2'
$script:ExpectedPayloadSha256 = 'f393562a9d9534fd12a06c2d94094306f8b10dce051a877035335b3e5d37f034'
$script:ExpectedRuntimeTreeSha256 = 'b778df06bfc98d699c2aa4c68d4f146f8c6c3d55a0ce1cc7b6811251ed5aad14'
$script:TransactionStateSchema = 2
$script:TaskName = '会议室预约系统'
$script:ServicePort = 8080
$script:LogPath = $null
$script:LockStream = $null
$script:TempRoot = $null
$script:KeepTemporary = $false
$script:PayloadAttributeCheckDegraded = $false
$script:Utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$script:TopFiles = @(
    '① 启动系统.bat', '② 立即备份.bat', '③ 设置开机自动启动.bat',
    '④ 停止本次后台系统.bat', '⑤ 取消开机自动启动.bat', '使用说明.txt'
)
$script:ProgramFiles = @(
    '_程序文件/app.py', '_程序文件/server.py', '_程序文件/backup.py',
    '_程序文件/migrate_check.py', '_程序文件/requirements.txt', '_程序文件/版本.txt'
)
$script:BlackSegments = @('data', 'backups', 'logs', 'runtime', '_升级回滚', '_升级状态.json', '_升级锁')

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Level, $Message
    if ($script:LogPath) {
        try { Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 -ErrorAction Stop }
        catch { Write-Debug "升级日志写入失败：$($_.Exception.Message)" }
    }
}

function Write-User {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White)
    Write-Host $Message -ForegroundColor $Color
}

function Show-Stage {
    param([string]$Message)
    Write-User ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message) Cyan
    Write-Log "用户阶段：$Message"
}

function Throw-UpgradeFailure {
    param([string]$Message, [int]$ExitCode = 1)
    $exception = New-Object -TypeName System.Exception -ArgumentList $Message
    $exception.Data['UpgradeExitCode'] = $ExitCode
    throw $exception
}

function Get-ExitCodeFromError {
    param($ErrorRecord, [int]$DefaultCode = 1)
    if ($ErrorRecord.Exception -and $ErrorRecord.Exception.Data.Contains('UpgradeExitCode')) {
        return [int]$ErrorRecord.Exception.Data['UpgradeExitCode']
    }
    return $DefaultCode
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-Utf8NoBom {
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "文件必须是 UTF-8 无 BOM：$Path"
    }
    return $script:Utf8Strict.GetString($bytes)
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    [IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Replace-FileAtomic {
    param([string]$Temporary, [string]$Destination)
    $backup = '{0}.replace-backup.{1}.{2}' -f @(
        $Destination, $PID, ([Guid]::NewGuid().ToString('N'))
    )
    try {
        # Windows PowerShell 5.1 的 .NET Framework 不接受 null 备份路径。
        # 使用同目录真实备份可保持 Replace 的同卷原子语义；成功后立即清理。
        [IO.File]::Replace($Temporary, $Destination, $backup, $true)
    }
    catch {
        if (-not (Test-Path -LiteralPath $Destination) -and
            (Test-Path -LiteralPath $backup)) {
            [IO.File]::Move($backup, $Destination)
        }
        throw
    }
    finally {
        if (Test-Path -LiteralPath $Temporary) {
            Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
        }
        if ((Test-Path -LiteralPath $Destination) -and
            (Test-Path -LiteralPath $backup)) {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
}

function Write-JsonAtomic {
    param([string]$Path, $Value)
    $temporary = '{0}.tmp.{1}' -f $Path, $PID
    Write-Utf8NoBom -Path $temporary -Text ($Value | ConvertTo-Json -Depth 8)
    if (Test-Path -LiteralPath $Path) {
        Replace-FileAtomic -Temporary $temporary -Destination $Path
    }
    else {
        [IO.File]::Move($temporary, $Path)
    }
}

function Test-StringEqualsPath {
    param([string]$Left, [string]$Right)
    return [string]::Equals([IO.Path]::GetFullPath($Left).TrimEnd('\'), [IO.Path]::GetFullPath($Right).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}

function Get-RelativeSlashPath {
    param([string]$Root, [string]$FullName)
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $full = [IO.Path]::GetFullPath($FullName)
    $prefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "路径越界：$FullName"
    }
    return $full.Substring($prefix.Length).Replace('\', '/')
}

function Resolve-SafeRelativeChild {
    param([string]$Root, [string]$RelativePath)
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or $RelativePath.Contains('\') -or $RelativePath.StartsWith('/') -or $RelativePath -match '^[A-Za-z]:') {
        throw "清单路径非法：$RelativePath"
    }
    foreach ($segment in $RelativePath.Split('/')) {
        if (-not $segment -or $segment -eq '.' -or $segment -eq '..' -or $segment.Contains(':')) { throw "清单路径越界：$RelativePath" }
    }
    $candidate = [IO.Path]::GetFullPath((Join-Path $Root $RelativePath.Replace('/', '\')))
    [void](Get-RelativeSlashPath -Root $Root -FullName $candidate)
    return $candidate
}

function Assert-SafePayloadPath {
    param([string]$RelativePath, [bool]$IsDirectory)
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or $RelativePath.Contains('\')) {
        throw "负载路径格式非法：$RelativePath"
    }
    $path = $RelativePath.TrimEnd('/')
    if (-not $path -or $path.StartsWith('/') -or $path -match '^[A-Za-z]:' -or [IO.Path]::IsPathRooted($path)) {
        throw "负载包含绝对路径：$RelativePath"
    }
    $segments = $path.Split('/')
    foreach ($segment in $segments) {
        if (-not $segment -or $segment -eq '.' -or $segment -eq '..') {
            throw "负载路径包含不安全的路径段：$RelativePath"
        }
        $hasControlCharacter = $false
        foreach ($character in $segment.ToCharArray()) {
            if ([int]$character -lt 32) { $hasControlCharacter = $true; break }
        }
        if ($segment.Length -gt 255 -or $hasControlCharacter -or $segment.IndexOfAny([char[]]'<>"|?*:') -ge 0 -or $segment.EndsWith('.') -or $segment.EndsWith(' ')) {
            throw "负载路径含 Windows 不允许的字符：$RelativePath"
        }
        if ($segment -match '^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)(\..*)?$') {
            throw "负载路径使用 Windows 保留名称：$RelativePath"
        }
        foreach ($blocked in $script:BlackSegments) {
            if ([string]::Equals($segment, $blocked, [StringComparison]::OrdinalIgnoreCase)) {
                throw "负载包含禁止目录或文件：$RelativePath"
            }
        }
    }

    $allowed = $false
    foreach ($name in $script:TopFiles) {
        if ([string]::Equals($path, $name, [StringComparison]::OrdinalIgnoreCase) -and -not $IsDirectory) { $allowed = $true }
    }
    foreach ($name in $script:ProgramFiles) {
        if ([string]::Equals($path, $name, [StringComparison]::OrdinalIgnoreCase) -and -not $IsDirectory) { $allowed = $true }
    }
    if ($IsDirectory -and ($path -ieq '_程序文件' -or $path -ieq '_程序文件/static' -or $path -ieq '_程序文件/templates')) { $allowed = $true }
    if ($path.StartsWith('_程序文件/static/', [StringComparison]::OrdinalIgnoreCase) -or $path.StartsWith('_程序文件/templates/', [StringComparison]::OrdinalIgnoreCase)) { $allowed = $true }
    if (-not $allowed) { throw "负载包含白名单外路径：$RelativePath" }
}

function Assert-CompletePayloadFiles {
    param([string[]]$FilePaths)
    $set = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $FilePaths) { [void]$set.Add($path) }
    foreach ($required in @($script:TopFiles + $script:ProgramFiles)) {
        if (-not $set.Contains($required)) { throw "完整累计负载缺少文件：$required" }
    }
    $staticCount = @($FilePaths | Where-Object { $_.StartsWith('_程序文件/static/', [StringComparison]::OrdinalIgnoreCase) }).Count
    $templateCount = @($FilePaths | Where-Object { $_.StartsWith('_程序文件/templates/', [StringComparison]::OrdinalIgnoreCase) }).Count
    if ($staticCount -lt 1 -or $templateCount -lt 1) { throw 'static 和 templates 必须存在且非空。' }
}

function Assert-NoReparsePoints {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $items = @((Get-Item -LiteralPath $Path -Force)) + @(Get-ChildItem -LiteralPath $Path -Force -Recurse)
    foreach ($item in $items) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "不允许使用符号链接或重解析点：$($item.FullName)"
        }
    }
}

function Initialize-Payload {
    $script:TempRoot = Join-Path $env:TEMP ('meetingroom_upgrade_{0}_{1}' -f $PID, ([Guid]::NewGuid().ToString('N')))
    New-Item -ItemType Directory -Path $script:TempRoot | Out-Null
    $zipPath = Join-Path $script:TempRoot 'payload.zip'
    $payloadRoot = Join-Path $script:TempRoot 'payload'

    $all = [IO.File]::ReadAllText([IO.Path]::GetFullPath($PackagePath), [Text.Encoding]::UTF8)
    $psMarkers = [regex]::Matches($all, '(?m)^__UPGRADE_PS1_BELOW__\r?$')
    $payloadMarkers = [regex]::Matches($all, '(?m)^__UPGRADE_PAYLOAD_BELOW__\r?$')
    if ($psMarkers.Count -ne 1 -or $payloadMarkers.Count -ne 1 -or $psMarkers[0].Index -ge $payloadMarkers[0].Index) {
        throw '升级文件标记损坏。'
    }
    $base64Start = $payloadMarkers[0].Index + $payloadMarkers[0].Length
    $base64 = $all.Substring($base64Start) -replace '\s', ''
    if (-not $base64 -or ($base64.Length % 4) -ne 0 -or $base64 -notmatch '^[A-Za-z0-9+/]*={0,2}$') { throw '升级负载 Base64 损坏。' }
    try { $zipBytes = [Convert]::FromBase64String($base64) } catch { throw '升级负载无法解码。' }
    [IO.File]::WriteAllBytes($zipPath, $zipBytes)
    $actualHash = Get-Sha256Hex -Path $zipPath
    if (-not [string]::Equals($actualHash, $script:ExpectedPayloadSha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw '升级负载 SHA-256 校验失败，文件可能传输不完整。'
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $zipFiles = New-Object System.Collections.Generic.List[string]
    try {
        foreach ($entry in $archive.Entries) {
            $isDirectory = [string]::IsNullOrEmpty($entry.Name) -and $entry.FullName.EndsWith('/')
            $normalized = $entry.FullName.TrimEnd('/')
            Assert-SafePayloadPath -RelativePath $entry.FullName -IsDirectory $isDirectory
            if (-not $seen.Add($normalized)) { throw "负载含重复或仅大小写不同的路径：$normalized" }
            $externalAttributesProperty = $entry.PSObject.Properties['ExternalAttributes']
            if ($null -eq $externalAttributesProperty) {
                # 老版本 Windows 10 自带的 .NET 可能没有此属性。此时仍执行 ZIP 路径
                # 白名单/黑名单校验，并在解包后用文件系统属性再次拒绝重解析点。
                $script:PayloadAttributeCheckDegraded = $true
            }
            else {
                $externalAttributes = [int64]$externalAttributesProperty.Value
                $unixType = (($externalAttributes -shr 16) -band 0xF000)
                if ($unixType -eq 0xA000 -or (($externalAttributes -band 0x400) -ne 0)) { throw "负载含符号链接或重解析点：$normalized" }
            }
            if (-not $isDirectory) { $zipFiles.Add($normalized) }
        }
    }
    finally { $archive.Dispose() }
    Assert-CompletePayloadFiles -FilePaths $zipFiles.ToArray()

    New-Item -ItemType Directory -Path $payloadRoot | Out-Null
    [IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $payloadRoot)
    Assert-NoReparsePoints -Path $payloadRoot
    $diskFiles = New-Object System.Collections.Generic.List[string]
    foreach ($item in Get-ChildItem -LiteralPath $payloadRoot -Force -Recurse) {
        $relative = Get-RelativeSlashPath -Root $payloadRoot -FullName $item.FullName
        Assert-SafePayloadPath -RelativePath $relative -IsDirectory $item.PSIsContainer
        if (-not $item.PSIsContainer) { $diskFiles.Add($relative) }
    }
    Assert-CompletePayloadFiles -FilePaths $diskFiles.ToArray()
    $expectedSet = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $zipFiles) { [void]$expectedSet.Add($path) }
    $actualSet = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $diskFiles) { [void]$actualSet.Add($path) }
    if (-not $expectedSet.SetEquals($actualSet)) { throw '解包后的文件清单与 ZIP 不一致。' }

    $versionPath = Join-Path $payloadRoot '_程序文件\版本.txt'
    $versionText = Read-Utf8NoBom -Path $versionPath
    if ($versionText -notmatch '^\d+\.\d+\.\d+(\r?\n)?$' -or $versionText.TrimEnd([char[]]"`r`n") -ne $script:PackageVersionText) {
        throw 'Payload 版本.txt 与升级包版本不一致。'
    }
    $requirements = (Read-Utf8NoBom -Path (Join-Path $payloadRoot '_程序文件\requirements.txt')).Replace("`r`n", "`n").TrimEnd("`n")
    if ($requirements -ne "Flask>=3.0,<4`nwaitress>=3.0,<4") { throw 'requirements.txt 与 V1.0.0 冻结依赖不一致，本升级包不能修改 runtime 依赖。' }
    $totalBytes = ($diskFiles | ForEach-Object { (Get-Item -LiteralPath (Join-Path $payloadRoot $_.Replace('/', '\'))).Length } | Measure-Object -Sum).Sum
    $payloadManifest = Get-FileManifest -Root $payloadRoot
    return [pscustomobject]@{ Root = $payloadRoot; ZipPath = $zipPath; ZipBytes = $zipBytes.Length; FileBytes = [int64]$totalBytes; Files = $diskFiles.ToArray(); Manifest = $payloadManifest }
}

function Test-NormalInstallRoot {
    param([string]$Root)
    if (-not $Root -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return $false }
    foreach ($relative in @('① 启动系统.bat', '_程序文件\app.py', '_程序文件\server.py', '_程序文件\runtime\python.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relative) -PathType Leaf)) { return $false }
    }
    return $true
}

function Test-RecoverableInstallRoot {
    param([string]$Root)
    if (-not $Root -or -not (Test-Path -LiteralPath $Root -PathType Container)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Root '_程序文件\runtime\python.exe') -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Root '_程序文件\_升级状态.json') -PathType Leaf)
}

function Test-InstallRoot {
    param([string]$Root)
    return (Test-NormalInstallRoot -Root $Root) -or (Test-RecoverableInstallRoot -Root $Root)
}

function Assert-PackageLocationSafe {
    param([string]$InstallRoot)
    $packageFull = [IO.Path]::GetFullPath($PackagePath)
    foreach ($relative in $script:TopFiles) {
        if (Test-StringEqualsPath -Left $packageFull -Right (Join-Path $InstallRoot $relative)) {
            throw '升级 BAT 不能占用安装目录中的受管文件名，请保留“升级到VX.Y.Z.bat”文件名。'
        }
    }
    $programRoot = [IO.Path]::GetFullPath((Join-Path $InstallRoot '_程序文件')).TrimEnd('\')
    $programPrefix = $programRoot + '\'
    if ($packageFull.StartsWith($programPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw '升级 BAT 不能放入“_程序文件”内部，请移到桌面或安装目录根层后重试。'
    }
}

function Clear-RuntimePythonCaches {
    param([string]$ProgramRoot)
    $runtimeRoot = Join-Path $ProgramRoot 'runtime'
    if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
        throw '安装目录缺少 Python runtime。'
    }
    Assert-NoReparsePoints -Path $runtimeRoot
    $cacheDirectories = @(
        Get-ChildItem -LiteralPath $runtimeRoot -Force -Recurse -Directory |
            Where-Object { $_.Name -eq '__pycache__' } |
            Sort-Object { $_.FullName.Length } -Descending
    )
    foreach ($cache in $cacheDirectories) {
        Remove-Item -LiteralPath $cache.FullName -Recurse -Force
    }
    foreach ($cacheFile in @(Get-ChildItem -LiteralPath $runtimeRoot -Force -Recurse -File -Filter '*.pyc')) {
        Remove-Item -LiteralPath $cacheFile.FullName -Force
    }
    if ($cacheDirectories.Count -gt 0) {
        Write-Log "已清理 runtime 中 $($cacheDirectories.Count) 个可丢弃的 __pycache__ 目录。"
    }
}

function Get-RuntimeTreeSha256 {
    param([string]$ProgramRoot)
    $runtimeRoot = Join-Path $ProgramRoot 'runtime'
    Assert-NoReparsePoints -Path $runtimeRoot
    $paths = @(
        Get-ChildItem -LiteralPath $runtimeRoot -Force -Recurse -File |
            ForEach-Object { Get-RelativeSlashPath -Root $runtimeRoot -FullName $_.FullName }
    )
    [Array]::Sort($paths, [StringComparer]::Ordinal)
    $builder = New-Object Text.StringBuilder
    foreach ($relative in $paths) {
        $path = Resolve-SafeRelativeChild -Root $runtimeRoot -RelativePath $relative
        [void]$builder.Append($relative)
        [void]$builder.Append([char]0)
        [void]$builder.Append((Get-Sha256Hex -Path $path))
        [void]$builder.Append("`n")
    }
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $script:Utf8NoBom.GetBytes($builder.ToString())
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Assert-TrustedRuntime {
    param([string]$ProgramRoot)
    Clear-RuntimePythonCaches -ProgramRoot $ProgramRoot
    $actual = Get-RuntimeTreeSha256 -ProgramRoot $ProgramRoot
    Write-Log "冻结 runtime 目录哈希=$actual"
    if (-not [string]::Equals($actual, $script:ExpectedRuntimeTreeSha256, [StringComparison]::OrdinalIgnoreCase)) {
        throw '安装目录中的 Python runtime 与官方冻结版本不一致。为避免以管理员权限运行被替换的程序，升级已停止；请用完整原版部署包修复 runtime 后重试。'
    }
}

function Assert-InstallLocationSafe {
    param([string]$InstallRoot)
    $full = [IO.Path]::GetFullPath($InstallRoot)
    if ($full.StartsWith('\\')) {
        throw '系统安装在网络共享目录中，无法安全升级；请先移到本机固定磁盘。'
    }
    $root = [IO.Path]::GetPathRoot($full)
    if (-not $root -or $root.Length -lt 2) { throw '无法确认安装目录所在磁盘。' }
    try {
        $disk = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $root.Substring(0, 2))
    }
    catch {
        throw "无法确认安装磁盘类型：$($_.Exception.Message)"
    }
    if ($null -eq $disk -or [int]$disk.DriveType -ne 3) {
        throw '系统必须放在本机固定磁盘中；网络盘、U 盘和临时盘不支持无忧升级。'
    }
    foreach ($name in @('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')) {
        $syncRoot = [Environment]::GetEnvironmentVariable($name)
        if ($syncRoot) {
            $syncFull = [IO.Path]::GetFullPath($syncRoot).TrimEnd('\') + '\'
            if (($full.TrimEnd('\') + '\').StartsWith($syncFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw '系统安装在 OneDrive 同步目录中，升级时可能发生文件冲突；请先移到本机普通文件夹。'
            }
        }
    }
}

function Find-InstallRoot {
    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add((Split-Path -Parent ([IO.Path]::GetFullPath($PackagePath))))
    foreach ($candidate in @('D:\会议室预约系统', 'C:\会议室预约系统', 'E:\会议室预约系统')) { $candidates.Add($candidate) }
    foreach ($base in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Downloads'))) {
        if ($base) { $candidates.Add((Join-Path $base '会议室预约系统')) }
    }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $matches = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in $candidates) {
        try { $full = [IO.Path]::GetFullPath($candidate) } catch { continue }
        $full = $full.TrimEnd('\')
        if ($seen.Add($full) -and (Test-InstallRoot -Root $full)) { $matches.Add($full) }
    }
    if ($matches.Count -eq 1) { return $matches[0] }
    if ($matches.Count -gt 1) {
        Write-User ''
        Write-User '发现多个会议室预约系统，升级器不会替您猜测。' Yellow
        foreach ($match in $matches) { Write-User ("  - {0}" -f $match) Yellow }
        Write-User '请在接下来的窗口中明确选择这次要升级的文件夹。' Yellow
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = '请选择会议室预约系统文件夹（通常包含“① 启动系统.bat”；若上次升级中断，也请选择原文件夹）'
        $dialog.ShowNewFolderButton = $false
        if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            Throw-UpgradeFailure -Message '您已取消选择，升级未开始。' -ExitCode 2
        }
        if (-not (Test-InstallRoot -Root $dialog.SelectedPath)) { throw '所选文件夹不是会议室预约系统安装目录，也没有可恢复的升级状态。' }
        return [IO.Path]::GetFullPath($dialog.SelectedPath).TrimEnd('\')
    }
    catch {
        if ($_.Exception.Data.Contains('UpgradeExitCode')) { throw }
        throw "无法定位安装目录：$($_.Exception.Message)"
    }
}

function Initialize-Log {
    param([string]$ProgramRoot)
    $logDirectory = Join-Path $ProgramRoot 'logs'
    if (-not (Test-Path -LiteralPath $logDirectory)) { New-Item -ItemType Directory -Path $logDirectory | Out-Null }
    $script:LogPath = Join-Path $logDirectory ('upgrade-{0}_{1}.log' -f (Get-Date -Format 'yyyyMMdd_HHmmss'), $PID)
    New-Item -ItemType File -Path $script:LogPath | Out-Null
    $env:MEETING_ROOM_UPGRADE_LOG = $script:LogPath
    Write-Log "升级器启动，包版本=$($script:PackageVersionText)，升级文件=$PackagePath"
}

function Open-UpgradeLock {
    param([string]$ProgramRoot)
    $lockPath = Join-Path $ProgramRoot '_升级锁'
    try {
        $script:LockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    }
    catch {
        Write-Log "无法取得升级独占锁：$($_.Exception.Message)" 'WARN'
        Throw-UpgradeFailure -Message '升级正在进行，请不要重复打开。' -ExitCode 4
    }
}

function Read-InstalledVersion {
    param([string]$ProgramRoot)
    $path = Join-Path $ProgramRoot '版本.txt'
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{ Text = '1.0.0'; Version = [version]'1.0.0'; Existed = $false }
    }
    $text = Read-Utf8NoBom -Path $path
    if ($text -notmatch '^\d+\.\d+\.\d+(\r?\n)?$') { throw '已安装的 版本.txt 内容非法，请联系维护人员。' }
    $clean = $text.TrimEnd([char[]]"`r`n")
    try { $version = [version]$clean } catch { throw '已安装版本号无法识别，请联系维护人员。' }
    return [pscustomobject]@{ Text = $clean; Version = $version; Existed = $true }
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $null,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 120
    )
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $quoted = foreach ($argument in $Arguments) { '"{0}"' -f ([string]$argument).Replace('"', '\"') }
    $info.Arguments = $quoted -join ' '
    if ($WorkingDirectory) { $info.WorkingDirectory = $WorkingDirectory }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    if (-not $process.Start()) { throw "无法启动命令：$FilePath" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Write-Log "外部命令执行超过 $TimeoutSeconds 秒，正在强制结束：$FilePath" 'WARN'
            try { $process.Kill() } catch { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
            if (-not $process.WaitForExit(5000)) {
                throw "命令执行超时且无法结束进程：$FilePath"
            }
            $process.WaitForExit()
            $stdout = $stdoutTask.Result
            $stderr = $stderrTask.Result
            foreach ($line in @($stdout, $stderr)) {
                if (-not [string]::IsNullOrWhiteSpace($line)) { Write-Log ($line.TrimEnd([char[]]"`r`n")) 'CMD' }
            }
            throw "命令执行超时（$TimeoutSeconds 秒）：$FilePath"
        }
        # 带超时的 WaitForExit 返回后，再调用无参版本，确保异步输出读取完全结束。
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        foreach ($line in @($stdout, $stderr)) {
            if (-not [string]::IsNullOrWhiteSpace($line)) { Write-Log ($line.TrimEnd([char[]]"`r`n")) 'CMD' }
        }
        $code = $process.ExitCode
        return [pscustomobject]@{ ExitCode = [int]$code; Stdout = $stdout; Stderr = $stderr }
    }
    finally {
        $process.Dispose()
    }
}

function Invoke-Robocopy {
    param([string]$Source, [string]$Destination, [switch]$Mirror)
    if (-not (Test-Path -LiteralPath $Destination)) { New-Item -ItemType Directory -Path $Destination | Out-Null }
    $arguments = @($Source, $Destination)
    if ($Mirror) { $arguments += '/MIR' } else { $arguments += '/E' }
    $arguments += @('/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/XJ', '/NP')
    $result = Invoke-NativeCommand -FilePath 'robocopy.exe' -Arguments $arguments -TimeoutSeconds 1800
    if ($result.ExitCode -lt 0 -or $result.ExitCode -gt 7) { throw "复制失败（robocopy 退出码 $($result.ExitCode)）。" }
}

function Get-OwnedTaskState {
    param([string]$InstallRoot, [switch]$AllowMissing)
    try {
        $tasks = @(
            Get-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue |
                Where-Object { [string]$_.TaskPath -eq '\' }
        )
    }
    catch {
        throw "无法读取计划任务：$($_.Exception.Message)"
    }
    if ($tasks.Count -eq 0) {
        if ($AllowMissing) {
            return [pscustomobject]@{ Exists = $false; Enabled = $false; WasRunning = $false }
        }
        throw '原有计划任务已经消失，无法安全恢复其状态。'
    }
    if ($tasks.Count -ne 1) { throw '发现多个同名计划任务，无法确认归属。' }

    $task = $tasks[0]
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw '同名计划任务不是本系统创建的：动作数量不一致。' }
    $action = $actions[0]
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $expectedExecutable = Join-Path $programRoot 'runtime\pythonw.exe'
    $expectedServer = Join-Path $programRoot 'server.py'
    $actualExecutable = [Environment]::ExpandEnvironmentVariables([string]$action.Execute)
    $actualWorkingDirectory = [Environment]::ExpandEnvironmentVariables([string]$action.WorkingDirectory)
    $argumentText = ([Environment]::ExpandEnvironmentVariables([string]$action.Arguments)).Trim()
    if ($argumentText -notmatch '^"([^"]+)"$') {
        throw '同名计划任务不是本系统创建的：启动参数不一致。'
    }
    $actualServer = $matches[1]
    if (-not (Test-StringEqualsPath -Left $actualExecutable -Right $expectedExecutable) -or
        -not (Test-StringEqualsPath -Left $actualServer -Right $expectedServer) -or
        ($actualWorkingDirectory -and
            -not (Test-StringEqualsPath -Left $actualWorkingDirectory -Right $programRoot))) {
        throw '同名计划任务指向另一个安装目录。为避免停止错误的系统，升级已停止。'
    }
    if (-not $actualWorkingDirectory) {
        Write-Log '检测到 V1.0.0 兼容计划任务（未设置 WorkingDirectory）；执行路径和 server.py 归属已精确验证。' 'WARN'
    }
    $enabled = [bool]$task.Settings.Enabled
    return [pscustomobject]@{
        Exists = $true
        Enabled = $enabled
        WasRunning = ([string]$task.State -eq 'Running')
    }
}

function Set-OwnedTaskEnabledState {
    param([string]$InstallRoot, [bool]$TaskExists, [bool]$Enabled)
    if (-not $TaskExists) { return }
    [void](Get-OwnedTaskState -InstallRoot $InstallRoot)
    if ($Enabled) {
        Enable-ScheduledTask -TaskName $script:TaskName -TaskPath '\' | Out-Null
    }
    else {
        Disable-ScheduledTask -TaskName $script:TaskName -TaskPath '\' | Out-Null
    }
    Write-Log "计划任务启用状态已设置为：$Enabled"
}

function Disable-OwnedTaskForTransaction {
    param([string]$InstallRoot, $TaskState)
    if (-not [bool]$TaskState.Exists) { return }
    Set-OwnedTaskEnabledState -InstallRoot $InstallRoot -TaskExists $true -Enabled $false
}

function Get-OwnedServerProcesses {
    param([string]$ProgramRoot)
    $python = [IO.Path]::GetFullPath((Join-Path $ProgramRoot 'runtime\python.exe'))
    $pythonw = [IO.Path]::GetFullPath((Join-Path $ProgramRoot 'runtime\pythonw.exe'))
    $server = [IO.Path]::GetFullPath((Join-Path $ProgramRoot 'server.py'))
    try { $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'") }
    catch { throw "无法枚举本系统进程：$($_.Exception.Message)" }
    $owned = New-Object System.Collections.Generic.List[object]
    foreach ($process in $processes) {
        if (-not $process.ExecutablePath -or -not $process.CommandLine) { continue }
        try { $executable = [IO.Path]::GetFullPath([string]$process.ExecutablePath) } catch { continue }
        $isRuntime = [string]::Equals($executable, $python, [StringComparison]::OrdinalIgnoreCase) -or
                     [string]::Equals($executable, $pythonw, [StringComparison]::OrdinalIgnoreCase)
        $commandLine = [string]$process.CommandLine
        $absoluteServerPattern = '(?i)(^|\s)"?' + [regex]::Escape($server) + '"?(?=\s|$)'
        $hasAbsoluteServer = [regex]::IsMatch($commandLine, $absoluteServerPattern)
        # V1.0.0 的 ① BAT 使用精确相对 token "_程序文件\server.py"；
        # 只有可执行文件已经精确归属本 runtime 时才兼容此旧格式。
        $hasLegacyRelativeServer = [regex]::IsMatch(
            $commandLine,
            '(?i)(^|\s)"?_程序文件[\\/]server\.py"?(?=\s|$)'
        )
        if ($isRuntime -and ($hasAbsoluteServer -or $hasLegacyRelativeServer)) {
            $owned.Add($process)
        }
    }
    return $owned.ToArray()
}

function Test-SystemRunning {
    param([string]$ProgramRoot)
    return @(Get-OwnedServerProcesses -ProgramRoot $ProgramRoot).Count -gt 0
}

function Get-PortListeners {
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw '当前 Windows 无法检查 TCP 端口归属，升级已停止。'
    }
    try {
        return @(
            Get-NetTCPConnection -State Listen -LocalPort $script:ServicePort -ErrorAction SilentlyContinue
        )
    }
    catch {
        throw "无法检查端口 $($script:ServicePort)：$($_.Exception.Message)"
    }
}

function Assert-ServicePortFree {
    $listeners = @(Get-PortListeners)
    if ($listeners.Count -gt 0) {
        $owners = ($listeners | ForEach-Object { [string]$_.OwningProcess } | Sort-Object -Unique) -join ', '
        throw "端口 $($script:ServicePort) 正被其他程序占用（PID：$owners）。升级不会改用其他端口，请先关闭占用程序。"
    }
}

function Assert-CurrentPortOwnership {
    param([string]$ProgramRoot)
    $listeners = @(Get-PortListeners)
    if ($listeners.Count -eq 0) { return }
    $ownedIds = @(
        Get-OwnedServerProcesses -ProgramRoot $ProgramRoot |
            ForEach-Object { [int]$_.ProcessId }
    )
    foreach ($listener in $listeners) {
        if ($ownedIds -notcontains [int]$listener.OwningProcess) {
            throw "端口 $($script:ServicePort) 由另一个程序或另一套安装占用（PID=$($listener.OwningProcess)）。升级已停止。"
        }
    }
}

function Wait-ServicePortFree {
    param([int]$Seconds = 10)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (@(Get-PortListeners).Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "端口 $($script:ServicePort) 未能在 $Seconds 秒内释放。"
}

function Test-CanonicalInstallId {
    param($Value)
    return $Value -is [string] -and
           [regex]::IsMatch(
               [string]$Value,
               '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
           )
}

function Get-InstallId {
    param([string]$ProgramRoot, [switch]$AllowMissing)
    $path = Join-Path $ProgramRoot 'data\install_id'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if ($AllowMissing) { return $null }
        throw '安装标识 data\install_id 缺失。'
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt 128) {
        throw '安装标识文件异常。'
    }
    $value = (Read-Utf8NoBom -Path $path).TrimEnd([char[]]"`r`n")
    if (-not (Test-CanonicalInstallId -Value $value)) {
        throw '安装标识 data\install_id 已损坏；升级不会自动替换它，请联系维护人员。'
    }
    return $value
}

function Get-ValidatedServiceHealth {
    param([string]$ExpectedInstallId, [string]$ExpectedMode)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/healthz" -f $script:ServicePort) -TimeoutSec 2
        if ([int]$response.StatusCode -ne 200 -or [string]$response.Headers['X-Meeting-Room-System'] -ne '1') {
            return $null
        }
        if (-not $response.Content -or ([string]$response.Content).Length -gt 8192) { return $null }
        $body = ([string]$response.Content) | ConvertFrom-Json
        $names = @($body.PSObject.Properties.Name | Sort-Object)
        if (($names -join ',') -cne 'install_id,lan_url,mode,ok' -or
            $body.ok -isnot [bool] -or -not [bool]$body.ok -or
            -not (Test-CanonicalInstallId -Value $body.install_id) -or
            -not [string]::Equals([string]$body.install_id, $ExpectedInstallId, [StringComparison]::Ordinal) -or
            -not [string]::Equals([string]$body.mode, $ExpectedMode, [StringComparison]::Ordinal)) {
            return $null
        }
        if ($ExpectedMode -eq 'upgrade-check') {
            if ($null -ne $body.lan_url) { return $null }
            $lanUrl = $null
        }
        elseif ($ExpectedMode -eq 'normal') {
            if ($null -ne $body.lan_url -and -not (Test-LanHttpUrl -Value $body.lan_url)) {
                return $null
            }
            $lanUrl = if ($null -eq $body.lan_url) { $null } else { [string]$body.lan_url }
        }
        else {
            return $null
        }
        return [pscustomobject]@{
            InstallId = [string]$body.install_id
            Mode = [string]$body.mode
            LanUrl = $lanUrl
        }
    }
    catch {
        return $null
    }
}

function Test-ServiceHealth {
    param([string]$ExpectedInstallId, [string]$ExpectedMode)
    return $null -ne (Get-ValidatedServiceHealth `
        -ExpectedInstallId $ExpectedInstallId -ExpectedMode $ExpectedMode)
}

function Wait-ServiceHealth {
    param(
        [string]$ProgramRoot,
        [string]$ExpectedInstallId,
        [string]$ExpectedMode,
        [int]$Seconds = 30,
        [int]$ExpectedProcessId = 0
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($ExpectedProcessId -gt 0 -and -not (Get-Process -Id $ExpectedProcessId -ErrorAction SilentlyContinue)) {
            throw "新版健康检查进程提前退出（PID=$ExpectedProcessId）。"
        }
        if ((Test-ServiceHealth -ExpectedInstallId $ExpectedInstallId -ExpectedMode $ExpectedMode) -and
            (Test-SystemRunning -ProgramRoot $ProgramRoot)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "新版服务未能在 $Seconds 秒内通过安装标识健康检查。"
}

function Assert-LoopbackListenerOwnedByProcess {
    param([int]$ProcessId)
    $listeners = @(Get-PortListeners)
    if ($listeners.Count -eq 0) { throw '临时健康检查没有找到 TCP 监听端口。' }
    foreach ($listener in $listeners) {
        if ([int]$listener.OwningProcess -ne $ProcessId -or
            @('127.0.0.1', '::1') -notcontains [string]$listener.LocalAddress) {
            throw '升级前验证服务没有严格限制在本机回环地址，升级已停止。'
        }
    }
}

function Stop-OwnedRuntimeProcesses {
    param([string]$ProgramRoot, [bool]$TaskExists)
    if ($TaskExists) {
        $endResult = Invoke-NativeCommand -FilePath 'schtasks.exe' -Arguments @('/End', '/TN', $script:TaskName)
        if ($endResult.ExitCode -ne 0) { Write-Log "计划任务当前可能未运行，/End 退出码=$($endResult.ExitCode)" 'WARN' }
    }
    foreach ($process in @(Get-OwnedServerProcesses -ProgramRoot $ProgramRoot)) {
        Write-Log "停止本安装目录服务进程 PID=$($process.ProcessId)，路径=$($process.ExecutablePath)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-SystemRunning -ProgramRoot $ProgramRoot)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw '系统未能在 10 秒内停止。'
}

function Wait-OwnedServerProcess {
    param([string]$ProgramRoot, [int]$Seconds = 30)
    $deadline = (Get-Date).AddSeconds($Seconds)
    $stableChecks = 0
    while ((Get-Date) -lt $deadline) {
        $ownedIds = @(
            Get-OwnedServerProcesses -ProgramRoot $ProgramRoot |
                ForEach-Object { [int]$_.ProcessId }
        )
        $listeners = @(Get-PortListeners)
        $ownedListener = $false
        foreach ($listener in $listeners) {
            if ($ownedIds -contains [int]$listener.OwningProcess) {
                $ownedListener = $true
                break
            }
        }
        if ($ownedIds.Count -gt 0 -and $ownedListener) {
            $stableChecks += 1
            if ($stableChecks -ge 2) { return }
        }
        else {
            $stableChecks = 0
        }
        Start-Sleep -Milliseconds 500
    }
    throw "系统服务未能在 $Seconds 秒内稳定启动并取得端口 $($script:ServicePort)。"
}

function Start-ServiceWithCurrentAdministratorToken {
    param([string]$InstallRoot)
    if ([Environment]::GetEnvironmentVariable('MEETING_ROOM_UPGRADE_DIRECT_ADMIN') -ne '1') {
        throw '当前管理员令牌启动降级路径未获升级包入口授权。'
    }
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $python = Join-Path $programRoot 'runtime\python.exe'
    $server = Join-Path $programRoot 'server.py'
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $python
    $info.Arguments = '"{0}"' -f $server
    $info.WorkingDirectory = Split-Path -Parent $python
    $info.UseShellExecute = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Minimized
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $startedProcessId = 0
    $verified = $false
    try {
        if (-not $process.Start()) {
            throw '使用当前管理员令牌启动服务失败。'
        }
        $startedProcessId = [int]$process.Id
        if ($startedProcessId -le 0) {
            throw '使用当前管理员令牌启动服务后没有取得有效进程 ID。'
        }
        Write-Log "升级包初始即处于完整管理员令牌；使用同一用户令牌恢复普通启动方式，PID=$startedProcessId" 'WARN'
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline) {
            $ownedIds = @(
                Get-OwnedServerProcesses -ProgramRoot $programRoot |
                    ForEach-Object { [int]$_.ProcessId }
            )
            if ($ownedIds -contains $startedProcessId) {
                $verified = $true
                return $startedProcessId
            }
            Start-Sleep -Milliseconds 250
        }
        throw '使用当前管理员令牌启动的服务进程不属于当前安装目录。'
    }
    finally {
        if (-not $verified) {
            if ($startedProcessId -gt 0) {
                Stop-Process -Id $startedProcessId -Force -ErrorAction SilentlyContinue
            }
            else {
                try {
                    if (-not $process.HasExited) { $process.Kill() }
                }
                catch {}
            }
        }
        $process.Dispose()
    }
}

function Request-UnelevatedServiceStart {
    param([string]$InstallRoot)
    $requestPath = [Environment]::GetEnvironmentVariable('MEETING_ROOM_UPGRADE_BROKER_REQUEST')
    $responsePath = [Environment]::GetEnvironmentVariable('MEETING_ROOM_UPGRADE_BROKER_RESPONSE')
    $token = [Environment]::GetEnvironmentVariable('MEETING_ROOM_UPGRADE_BROKER_TOKEN')
    if (-not $requestPath -or -not $responsePath -or $token -notmatch '^[0-9a-f]{32}$') {
        if ([Environment]::GetEnvironmentVariable('MEETING_ROOM_UPGRADE_DIRECT_ADMIN') -eq '1') {
            $currentTokenProcessId = Start-ServiceWithCurrentAdministratorToken -InstallRoot $InstallRoot
            return $currentTokenProcessId
        }
        throw '未提升的启动代理不可用。请关闭升级窗口后，再以普通方式双击升级包让它自动收尾。'
    }
    $requestFull = [IO.Path]::GetFullPath($requestPath)
    $responseFull = [IO.Path]::GetFullPath($responsePath)
    $requestParent = Split-Path -Parent $requestFull
    if (-not (Test-StringEqualsPath -Left $requestParent -Right (Split-Path -Parent $responseFull)) -or
        -not (Test-Path -LiteralPath $requestParent -PathType Container)) {
        throw '未提升启动代理的临时路径非法。'
    }
    Assert-NoReparsePoints -Path $requestParent
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $python = Join-Path $programRoot 'runtime\python.exe'
    $server = Join-Path $programRoot 'server.py'
    foreach ($oldBrokerFile in @($requestFull, $responseFull)) {
        if (Test-Path -LiteralPath $oldBrokerFile) {
            $oldItem = Get-Item -LiteralPath $oldBrokerFile -Force
            if (($oldItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $oldItem.PSIsContainer) {
                throw '未提升启动代理发现异常的旧请求文件。'
            }
            Remove-Item -LiteralPath $oldBrokerFile -Force
        }
    }
    Write-JsonAtomic -Path $requestFull -Value ([ordered]@{
        schema = 1
        token = $token
        python_path = $python
        server_path = $server
        working_directory = $programRoot
    })
    Write-Log '已请求未提升的父进程启动普通用户服务。'

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $responseFull -PathType Leaf) {
            $item = Get-Item -LiteralPath $responseFull -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt 4096) {
                throw '未提升启动代理响应异常。'
            }
            $reply = (Read-Utf8NoBom -Path $responseFull) | ConvertFrom-Json
            $names = @($reply.PSObject.Properties.Name | Sort-Object)
            if (($names -join ',') -ne 'error,ok,process_id,schema,token' -or
                ((($reply.schema -isnot [int]) -and ($reply.schema -isnot [long])) -or
                    [int64]$reply.schema -ne 1) -or
                -not [string]::Equals([string]$reply.token, $token, [StringComparison]::Ordinal) -or
                $reply.ok -isnot [bool]) {
                throw '未提升启动代理响应校验失败。'
            }
            if (-not [bool]$reply.ok) {
                throw "普通用户服务启动失败：$([string]$reply.error)"
            }
            $processId = [int]$reply.process_id
            if ($processId -le 0) { throw '未提升启动代理没有返回有效进程 ID。' }
            $ownedDeadline = (Get-Date).AddSeconds(10)
            while ((Get-Date) -lt $ownedDeadline) {
                $ownedIds = @(
                    Get-OwnedServerProcesses -ProgramRoot $programRoot |
                        ForEach-Object { [int]$_.ProcessId }
                )
                if ($ownedIds -contains $processId) {
                    Remove-Item -LiteralPath $responseFull -Force -ErrorAction SilentlyContinue
                    return $processId
                }
                Start-Sleep -Milliseconds 250
            }
            throw '普通用户启动的进程不属于当前安装目录。'
        }
        Start-Sleep -Milliseconds 200
    }
    throw '等待普通用户启动代理响应超时。请关闭升级窗口后重新双击升级包。'
}

function Start-PersistentSystem {
    param(
        [string]$InstallRoot,
        [bool]$TaskExists,
        [bool]$TaskEnabled,
        [bool]$TaskWasRunning
    )
    $programRoot = Join-Path $InstallRoot '_程序文件'
    if ($TaskExists -and $TaskWasRunning) {
        $temporarilyEnabled = -not $TaskEnabled
        if ($temporarilyEnabled) {
            Set-OwnedTaskEnabledState -InstallRoot $InstallRoot -TaskExists $true -Enabled $true
        }
        try {
            $result = Invoke-NativeCommand -FilePath 'schtasks.exe' -Arguments @('/Run', '/TN', $script:TaskName)
            if ($result.ExitCode -ne 0) { throw '计划任务无法启动系统。' }
            Wait-OwnedServerProcess -ProgramRoot $programRoot
        }
        finally {
            if ($temporarilyEnabled) {
                Set-OwnedTaskEnabledState -InstallRoot $InstallRoot -TaskExists $true -Enabled $false
            }
        }
    }
    else {
        [void](Request-UnelevatedServiceStart -InstallRoot $InstallRoot)
        Wait-OwnedServerProcess -ProgramRoot $programRoot
    }
}

function Test-Database {
    param([string]$PayloadRoot, [string]$ProgramRoot, [string]$DatabasePath)
    if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) { throw "数据库不存在：$DatabasePath" }
    $python = Join-Path $ProgramRoot 'runtime\python.exe'
    $checkScript = Join-Path $PayloadRoot '_程序文件\migrate_check.py'
    $result = Invoke-NativeCommand -FilePath $python -Arguments @($checkScript, '--precheck', $DatabasePath) -WorkingDirectory $ProgramRoot
    if ($result.ExitCode -ne 0) { throw "数据库完整性预检失败：$DatabasePath" }
}

function New-StandardPreUpgradeBackup {
    param([string]$PayloadRoot, [string]$ProgramRoot)
    $source = Join-Path $ProgramRoot 'data\reservation.db'
    $backupRoot = Join-Path $ProgramRoot 'backups'
    if (-not (Test-Path -LiteralPath $backupRoot)) {
        New-Item -ItemType Directory -Path $backupRoot | Out-Null
    }
    Assert-NoReparsePoints -Path $backupRoot
    $stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss_fff'
    $target = Join-Path $backupRoot ("reservation_before_upgrade_V{0}_{1}.db" -f $script:PackageVersionText, $stamp)
    $temporary = "$target.part"
    try {
        Copy-Item -LiteralPath $source -Destination $temporary -Force
        if ((Get-Sha256Hex -Path $source) -ne (Get-Sha256Hex -Path $temporary)) {
            throw '升级前标准备份与原数据库哈希不一致。'
        }
        Test-Database -PayloadRoot $PayloadRoot -ProgramRoot $ProgramRoot -DatabasePath $temporary
        [IO.File]::Move($temporary, $target)
    }
    finally {
        foreach ($leftover in @($temporary, "$temporary-wal", "$temporary-shm", "$target-wal", "$target-shm")) {
            if (Test-Path -LiteralPath $leftover) {
                Remove-Item -LiteralPath $leftover -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Write-Log "已生成可长期保留的升级前数据库备份：$target"
    return $target
}

function Assert-FreeSpace {
    param([string]$InstallRoot, $Payload)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $dataRoot = Join-Path $programRoot 'data'
    $dataBytes = [int64](Get-ChildItem -LiteralPath $dataRoot -Force -Recurse -File | Measure-Object Length -Sum).Sum
    $managedBytes = [int64]0
    foreach ($relative in @($script:TopFiles + $script:ProgramFiles)) {
        $path = Join-Path $InstallRoot $relative.Replace('/', '\')
        if (Test-Path -LiteralPath $path -PathType Leaf) { $managedBytes += (Get-Item -LiteralPath $path).Length }
    }
    foreach ($folder in @('static', 'templates')) {
        $path = Join-Path $programRoot $folder
        if (Test-Path -LiteralPath $path) { $managedBytes += [int64](Get-ChildItem -LiteralPath $path -Force -Recurse -File | Measure-Object Length -Sum).Sum }
    }
    $baseNeed = [double]($dataBytes * 3 + $managedBytes + $Payload.FileBytes + $Payload.ZipBytes)
    $required = [int64]([Math]::Ceiling($baseNeed * 1.2) + 256MB)
    $driveName = [IO.Path]::GetPathRoot($InstallRoot).Substring(0, 1)
    $drive = Get-PSDrive -Name $driveName -PSProvider FileSystem
    Write-Log "磁盘空间：可用=$($drive.Free)，估算需要=$required，data=$dataBytes，受管程序=$managedBytes"
    if ([int64]$drive.Free -lt $required) { throw '磁盘空间不足，升级未开始。' }
}

function Get-FileManifest {
    param([string]$Root)
    $records = New-Object System.Collections.Generic.List[object]
    if (Test-Path -LiteralPath $Root) {
        Assert-NoReparsePoints -Path $Root
        foreach ($file in Get-ChildItem -LiteralPath $Root -Force -Recurse -File | Sort-Object FullName) {
            $records.Add([ordered]@{
                RelativePath = (Get-RelativeSlashPath -Root $Root -FullName $file.FullName)
                Length = [int64]$file.Length
                Sha256 = (Get-Sha256Hex -Path $file.FullName)
            })
        }
    }
    return $records.ToArray()
}

function Write-Manifest {
    param([string]$Path, $Manifest)
    Write-Utf8NoBom -Path $Path -Text ($Manifest | ConvertTo-Json -Depth 8)
}

function Assert-ManifestMatches {
    param([string]$Root, $Records)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { throw "快照目录缺失：$Root" }
    Assert-NoReparsePoints -Path $Root
    $actualFiles = @(Get-ChildItem -LiteralPath $Root -Force -Recurse -File)
    if ($actualFiles.Count -ne @($Records).Count) { throw "快照文件数量不一致：$Root" }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in @($Records)) {
        $relative = [string]$record.RelativePath
        if (-not $seen.Add($relative)) { throw "清单含重复路径：$relative" }
        $path = Resolve-SafeRelativeChild -Root $Root -RelativePath $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "清单文件缺失：$relative" }
        $file = Get-Item -LiteralPath $path
        if ([int64]$file.Length -ne [int64]$record.Length -or (Get-Sha256Hex -Path $path) -ne [string]$record.Sha256) {
            throw "清单哈希不匹配：$relative"
        }
    }
}

function Copy-FixedManagedFiles {
    param([string]$SourceRoot, [string]$DestinationRoot, [switch]$ExcludeVersion, [switch]$RequireAll)
    foreach ($relative in @($script:TopFiles + $script:ProgramFiles)) {
        if ($ExcludeVersion -and $relative -ieq '_程序文件/版本.txt') { continue }
        $source = Join-Path $SourceRoot $relative.Replace('/', '\')
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            if (((Get-Item -LiteralPath $source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "不允许复制重解析点：$source" }
            $destination = Join-Path $DestinationRoot $relative.Replace('/', '\')
            $parent = Split-Path -Parent $destination
            if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
        elseif ($RequireAll) { throw "完整累计负载在复制时缺少固定文件：$relative" }
    }
}

function New-RollbackSnapshot {
    param([string]$InstallRoot, [string]$PayloadRoot, [string]$TransactionId)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $snapshotRoot = Join-Path $programRoot (Join-Path '_升级回滚' $TransactionId)
    $snapshotProgram = Join-Path $snapshotRoot 'program'
    $snapshotData = Join-Path $snapshotRoot 'data'
    New-Item -ItemType Directory -Path $snapshotProgram -Force | Out-Null
    New-Item -ItemType Directory -Path $snapshotData -Force | Out-Null

    $existence = New-Object System.Collections.Generic.List[object]
    foreach ($relative in @($script:TopFiles + $script:ProgramFiles)) {
        $source = Join-Path $InstallRoot $relative.Replace('/', '\')
        $exists = Test-Path -LiteralPath $source -PathType Leaf
        $existence.Add([ordered]@{ RelativePath = $relative; Kind = 'File'; Existed = [bool]$exists })
    }
    foreach ($relative in @('_程序文件/static', '_程序文件/templates')) {
        $source = Join-Path $InstallRoot $relative.Replace('/', '\')
        $exists = Test-Path -LiteralPath $source -PathType Container
        $existence.Add([ordered]@{ RelativePath = $relative; Kind = 'Directory'; Existed = [bool]$exists })
        if ($exists) { Assert-NoReparsePoints -Path $source }
    }
    Copy-FixedManagedFiles -SourceRoot $InstallRoot -DestinationRoot $snapshotProgram
    foreach ($folder in @('static', 'templates')) {
        $source = Join-Path $programRoot $folder
        if (Test-Path -LiteralPath $source -PathType Container) {
            Invoke-Robocopy -Source $source -Destination (Join-Path $snapshotProgram (Join-Path '_程序文件' $folder))
        }
    }

    $dataRoot = Join-Path $programRoot 'data'
    Assert-NoReparsePoints -Path $dataRoot
    Invoke-Robocopy -Source $dataRoot -Destination $snapshotData -Mirror
    Test-Database -PayloadRoot $PayloadRoot -ProgramRoot $programRoot -DatabasePath (Join-Path $snapshotData 'reservation.db')

    $programFiles = Get-FileManifest -Root $snapshotProgram
    $dataFiles = Get-FileManifest -Root $snapshotData
    $programManifest = [ordered]@{ Existence = $existence.ToArray(); Files = $programFiles }
    Write-Manifest -Path (Join-Path $snapshotRoot 'program-manifest.json') -Manifest $programManifest
    Write-Manifest -Path (Join-Path $snapshotRoot 'data-manifest.json') -Manifest ([ordered]@{ Files = $dataFiles })
    Assert-ManifestMatches -Root $snapshotProgram -Records $programFiles
    Assert-ManifestMatches -Root $snapshotData -Records $dataFiles
    Write-Log "回滚快照已建立并验证：$snapshotRoot"
    return $snapshotRoot
}

function Get-ValidatedSnapshot {
    param([string]$ProgramRoot, $State)
    $transactionId = [string]$State.TransactionId
    if ($transactionId -notmatch '^[0-9a-fA-F]{32}$') { throw '升级状态中的事务 ID 非法。' }
    $expected = Join-Path $ProgramRoot (Join-Path '_升级回滚' $transactionId)
    if (-not (Test-StringEqualsPath -Left $expected -Right ([string]$State.SnapshotPath))) { throw '升级状态中的快照路径越界。' }
    $programManifestPath = Join-Path $expected 'program-manifest.json'
    $dataManifestPath = Join-Path $expected 'data-manifest.json'
    if (-not (Test-Path -LiteralPath $programManifestPath) -or -not (Test-Path -LiteralPath $dataManifestPath)) { throw '回滚快照清单缺失。' }
    $programManifest = (Read-Utf8NoBom -Path $programManifestPath) | ConvertFrom-Json
    $dataManifest = (Read-Utf8NoBom -Path $dataManifestPath) | ConvertFrom-Json
    Assert-ManifestMatches -Root (Join-Path $expected 'program') -Records $programManifest.Files
    Assert-ManifestMatches -Root (Join-Path $expected 'data') -Records $dataManifest.Files
    return [pscustomobject]@{ Root = $expected; ProgramManifest = $programManifest; DataManifest = $dataManifest }
}

function Remove-ManagedProgram {
    param([string]$InstallRoot)
    foreach ($relative in @($script:TopFiles + $script:ProgramFiles)) {
        $path = Join-Path $InstallRoot $relative.Replace('/', '\')
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    foreach ($relative in @('_程序文件\static', '_程序文件\templates')) {
        $path = Join-Path $InstallRoot $relative
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
}

function Assert-RestoredProgram {
    param([string]$InstallRoot, $ProgramManifest)
    foreach ($record in @($ProgramManifest.Files)) {
        $path = Resolve-SafeRelativeChild -Root $InstallRoot -RelativePath ([string]$record.RelativePath)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "恢复后程序文件缺失：$($record.RelativePath)" }
        $item = Get-Item -LiteralPath $path
        if ([int64]$item.Length -ne [int64]$record.Length -or (Get-Sha256Hex -Path $path) -ne [string]$record.Sha256) {
            throw "恢复后程序文件哈希不一致：$($record.RelativePath)"
        }
    }
    foreach ($entry in @($ProgramManifest.Existence)) {
        $path = Join-Path $InstallRoot ([string]$entry.RelativePath).Replace('/', '\')
        if ([bool]$entry.Existed -ne [bool](Test-Path -LiteralPath $path)) { throw "恢复后存在状态不一致：$($entry.RelativePath)" }
    }
}

function Restore-ExpectedRunState {
    param(
        [string]$InstallRoot,
        [bool]$WasRunning,
        [bool]$TaskExists,
        [bool]$TaskEnabled,
        [bool]$TaskWasRunning,
        [string]$ExpectedInstallId = $null,
        [switch]$RequireHealth
    )
    $programRoot = Join-Path $InstallRoot '_程序文件'
    Set-OwnedTaskEnabledState -InstallRoot $InstallRoot -TaskExists $TaskExists -Enabled $TaskEnabled
    if ($WasRunning) {
        if (Test-SystemRunning -ProgramRoot $programRoot) {
            Write-Log '系统已处于运行状态，无需重复启动。'
        }
        else {
            Start-PersistentSystem -InstallRoot $InstallRoot -TaskExists $TaskExists `
                -TaskEnabled $TaskEnabled -TaskWasRunning $TaskWasRunning
        }
        Wait-OwnedServerProcess -ProgramRoot $programRoot
        if ($RequireHealth) {
            Wait-ServiceHealth -ProgramRoot $programRoot -ExpectedInstallId $ExpectedInstallId -ExpectedMode 'normal'
        }
    }
    else {
        Stop-OwnedRuntimeProcesses -ProgramRoot $programRoot -TaskExists $TaskExists
        if (Test-SystemRunning -ProgramRoot $programRoot) { throw '无法恢复升级前的停止状态。' }
    }
}

function Assert-RunStateInvariants {
    param(
        [bool]$WasRunning,
        [bool]$TaskExists,
        [bool]$TaskEnabled,
        [bool]$TaskWasRunning,
        [string]$Context
    )
    if ((-not $TaskExists -and ($TaskEnabled -or $TaskWasRunning)) -or
        ($TaskWasRunning -and -not $WasRunning)) {
        throw "$Context 中的运行状态互相矛盾。"
    }
}

function Assert-CurrentTransactionStateEnvelope {
    param($State, [string]$Context)
    $names = @($State.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @(
        'BackupPath', 'InstallId', 'OriginalInstallId', 'OriginalVersion',
        'OriginalVersionExisted', 'PackageVersion', 'Schema', 'SnapshotPath',
        'Stage', 'TaskEnabled', 'TaskExists', 'TaskWasRunning',
        'TransactionId', 'WasRunning'
    )
    if (($names -join ',') -cne ($expectedNames -join ',')) {
        throw "$Context 的字段集合非法。"
    }
    if ((($State.Schema -isnot [int]) -and ($State.Schema -isnot [long])) -or
        [int64]$State.Schema -ne [int64]$script:TransactionStateSchema) {
        throw "$Context 的结构版本非法。"
    }
    if ([string]$State.TransactionId -notmatch '^[0-9a-fA-F]{32}$' -or
        $State.PackageVersion -isnot [string] -or
        [string]$State.PackageVersion -notmatch '^\d+\.\d+\.\d+$' -or
        $State.OriginalVersion -isnot [string] -or
        [string]$State.OriginalVersion -notmatch '^\d+\.\d+\.\d+$' -or
        $State.OriginalVersionExisted -isnot [bool]) {
        throw "$Context 的事务信息非法。"
    }
    if ($State.WasRunning -isnot [bool] -or $State.TaskExists -isnot [bool] -or
        $State.TaskEnabled -isnot [bool] -or $State.TaskWasRunning -isnot [bool]) {
        throw "$Context 中的运行信息非法。"
    }
    Assert-RunStateInvariants -WasRunning ([bool]$State.WasRunning) `
        -TaskExists ([bool]$State.TaskExists) -TaskEnabled ([bool]$State.TaskEnabled) `
        -TaskWasRunning ([bool]$State.TaskWasRunning) -Context $Context
    if ($null -ne $State.InstallId -and
        -not (Test-CanonicalInstallId -Value $State.InstallId)) {
        throw "$Context 中的安装标识非法。"
    }
    if ($null -ne $State.OriginalInstallId -and
        -not (Test-CanonicalInstallId -Value $State.OriginalInstallId)) {
        throw "$Context 中的原安装标识非法。"
    }
}

function Convert-LegacyV101TransactionState {
    param([string]$InstallRoot, $State, [switch]$CommittedHandoff)
    $names = @($State.PSObject.Properties.Name | Sort-Object)
    $legacyNames = @(
        'OriginalVersion', 'OriginalVersionExisted', 'PackageVersion',
        'SnapshotPath', 'Stage', 'TaskExists', 'TransactionId', 'WasRunning'
    )
    if (($names -join ',') -cne ($legacyNames -join ',')) { return $null }

    $legacyStages = @(
        'preparing', 'snapshot_ready', 'program_replaced',
        'migration_complete', 'healthcheck_passed', 'version_committed'
    )
    if ($State.PackageVersion -isnot [string] -or
        [string]$State.PackageVersion -cne '1.0.1' -or
        [string]$State.Stage -notin $legacyStages -or
        [string]$State.TransactionId -notmatch '^[0-9a-fA-F]{32}$' -or
        $State.OriginalVersion -isnot [string] -or
        [string]$State.OriginalVersion -notmatch '^\d+\.\d+\.\d+$' -or
        $State.OriginalVersionExisted -isnot [bool] -or
        $State.WasRunning -isnot [bool] -or $State.TaskExists -isnot [bool]) {
        throw 'V1.0.1 遗留升级状态内容非法。'
    }
    try { $legacyOriginalVersion = [version][string]$State.OriginalVersion }
    catch { throw 'V1.0.1 遗留升级状态中的原版本非法。' }
    if ($legacyOriginalVersion -ge [version]'1.0.1' -or
        (-not [bool]$State.OriginalVersionExisted -and
            [string]$State.OriginalVersion -cne '1.0.0')) {
        throw 'V1.0.1 遗留升级状态中的版本关系非法。'
    }
    if ([string]$State.Stage -eq 'preparing') {
        if ($null -ne $State.SnapshotPath -and
            -not [string]::IsNullOrEmpty([string]$State.SnapshotPath)) {
            throw 'V1.0.1 preparing 状态不应包含快照路径。'
        }
    }
    elseif ($State.SnapshotPath -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$State.SnapshotPath)) {
        throw 'V1.0.1 遗留升级状态缺少快照路径。'
    }

    if ($CommittedHandoff) {
        # 已提交事务只保持用户当前运行状态，不恢复旧任务语义；用户可能在
        # 提交后自行增删任务，因此这里不能把当前任务与旧快照强行绑定。
        $taskEnabled = $false
        $taskWasRunning = $false
    }
    else {
        # V1.0.1 只记录“任务是否存在”，且恢复时只要升级前正在运行就一律
        # 优先通过任务重启。未提交事务必须严格核对当前任务归属，并按旧语义补齐。
        $currentTask = Get-OwnedTaskState -InstallRoot $InstallRoot -AllowMissing
        if ([bool]$currentTask.Exists -ne [bool]$State.TaskExists) {
            throw 'V1.0.1 遗留状态记录的开机任务与当前状态不一致，无法安全推断原运行方式。'
        }
        $taskEnabled = if ([bool]$State.TaskExists) { [bool]$currentTask.Enabled } else { $false }
        $taskWasRunning = [bool]$State.TaskExists -and [bool]$State.WasRunning
    }

    return [pscustomobject][ordered]@{
        Schema = $script:TransactionStateSchema
        TransactionId = [string]$State.TransactionId
        PackageVersion = [string]$State.PackageVersion
        SnapshotPath = $State.SnapshotPath
        BackupPath = $null
        InstallId = $null
        Stage = [string]$State.Stage
        OriginalVersion = [string]$State.OriginalVersion
        OriginalVersionExisted = [bool]$State.OriginalVersionExisted
        OriginalInstallId = $null
        WasRunning = [bool]$State.WasRunning
        TaskExists = [bool]$State.TaskExists
        TaskEnabled = $taskEnabled
        TaskWasRunning = $taskWasRunning
    }
}

function Assert-PreparingState {
    param($State)
    Assert-CurrentTransactionStateEnvelope -State $State -Context '升级准备状态'
    if (@('preparing', 'service_stopped', 'backup_ready') -notcontains [string]$State.Stage) {
        throw '升级准备状态内容非法。'
    }
    if ($null -ne $State.SnapshotPath -and -not [string]::IsNullOrEmpty([string]$State.SnapshotPath)) {
        throw '升级准备状态不应包含快照路径。'
    }
}

function Recover-PreparingTransaction {
    param([string]$InstallRoot, $State, [string]$StatePath)
    Assert-PreparingState -State $State
    Write-Log '发现停机或快照阶段中断；程序和数据尚未进入覆盖事务，正在恢复原运行状态。' 'WARN'
    Restore-ExpectedRunState -InstallRoot $InstallRoot -WasRunning ([bool]$State.WasRunning) `
        -TaskExists ([bool]$State.TaskExists) -TaskEnabled ([bool]$State.TaskEnabled) `
        -TaskWasRunning ([bool]$State.TaskWasRunning)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log 'preparing 状态已恢复并清除。'
}

function Assert-CommittedState {
    param($State, [switch]$AllowHealthcheckHandoff)
    Assert-CurrentTransactionStateEnvelope -State $State -Context '升级已提交状态'
    $stage = [string]$State.Stage
    $validStage = $stage -eq 'version_committed' -or $stage -eq 'service_restored' -or
                  ($AllowHealthcheckHandoff -and $stage -eq 'healthcheck_passed')
    if (-not $validStage) {
        throw '升级已提交状态内容非法。'
    }
    if (-not (Test-CanonicalInstallId -Value $State.InstallId)) {
        throw '升级已提交状态中的安装标识非法。'
    }
}

function Test-CommittedTransactionState {
    param([string]$ProgramRoot, $State)
    if (@('version_committed', 'service_restored') -contains [string]$State.Stage) { return $true }
    if ([string]$State.Stage -ne 'healthcheck_passed') { return $false }

    # 版本文件提交与 version_committed 状态落盘之间无法跨两个文件做同一个原子操作。
    # 若在这个极窄窗口断电，正式版本文件已经是目标版本，因此也必须按已提交处理，
    # 绝不能在客户继续使用后把 data 回滚到升级前快照。
    try {
        $installed = Read-InstalledVersion -ProgramRoot $ProgramRoot
        return $installed.Existed -and
               [string]::Equals($installed.Text, [string]$State.PackageVersion, [StringComparison]::Ordinal)
    }
    catch {
        return $false
    }
}

function Remove-SuccessfulTransactionSnapshot {
    param([string]$ProgramRoot, $State)
    $transactionId = [string]$State.TransactionId
    if ($transactionId -notmatch '^[0-9a-fA-F]{32}$') { throw '事务 ID 非法，不能清理快照。' }
    $expected = Join-Path $ProgramRoot (Join-Path '_升级回滚' $transactionId)
    if (-not (Test-StringEqualsPath -Left $expected -Right ([string]$State.SnapshotPath))) {
        throw '事务快照路径越界，拒绝清理。'
    }
    if (Test-Path -LiteralPath $expected) {
        Assert-NoReparsePoints -Path $expected
        Remove-Item -LiteralPath $expected -Recurse -Force
        Write-Log "已清理成功事务的完整回滚副本：$expected"
    }
}

function Recover-CommittedTransaction {
    param([string]$InstallRoot, $State, [string]$StatePath)
    $allowHandoff = [string]$State.Stage -eq 'healthcheck_passed'
    Assert-CommittedState -State $State -AllowHealthcheckHandoff:$allowHandoff
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $installed = Read-InstalledVersion -ProgramRoot $programRoot
    if (-not $installed.Existed -or
        -not [string]::Equals($installed.Text, [string]$State.PackageVersion, [StringComparison]::Ordinal)) {
        throw '升级状态显示版本已提交，但安装目录版本不一致；为保护现有数据，不会自动回滚。'
    }

    $actualInstallId = Get-InstallId -ProgramRoot $programRoot
    if (-not [string]::Equals($actualInstallId, [string]$State.InstallId, [StringComparison]::Ordinal)) {
        throw '已提交版本的安装标识发生变化；为保护数据，不会自动回滚或启动。'
    }

    Write-Log "发现已经提交成功的升级事务，正在安全收尾，版本=$($installed.Text)，原阶段=$($State.Stage)" 'WARN'
    if ([string]$State.Stage -ne 'service_restored') {
        if ([string]$State.Stage -eq 'healthcheck_passed') {
            Update-TransactionStage -State $State -StatePath $StatePath -Stage 'version_committed'
        }
        Restore-ExpectedRunState -InstallRoot $InstallRoot -WasRunning ([bool]$State.WasRunning) `
            -TaskExists ([bool]$State.TaskExists) -TaskEnabled ([bool]$State.TaskEnabled) `
            -TaskWasRunning ([bool]$State.TaskWasRunning) `
            -ExpectedInstallId $actualInstallId -RequireHealth
        Update-TransactionStage -State $State -StatePath $StatePath -Stage 'service_restored'
    }
    else {
        # 服务已向用户开放后，后续可能由用户手动启动或停止；只恢复计划任务
        # 的原启用状态，不再强行改变服务现状。
        Set-OwnedTaskEnabledState -InstallRoot $InstallRoot -TaskExists ([bool]$State.TaskExists) `
            -Enabled ([bool]$State.TaskEnabled)
        if (Test-SystemRunning -ProgramRoot $programRoot) {
            Wait-ServiceHealth -ProgramRoot $programRoot -ExpectedInstallId $actualInstallId -ExpectedMode 'normal'
        }
    }
    Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$State.TransactionId)
    Remove-SuccessfulTransactionSnapshot -ProgramRoot $programRoot -State $State
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log '已提交事务的残留状态和完整回滚副本已清除；现有程序和 data 均未回滚。'
}

function Recover-LegacyV101CommittedTransaction {
    param([string]$InstallRoot, $State, [string]$StatePath)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $installed = Read-InstalledVersion -ProgramRoot $programRoot
    if (-not $installed.Existed -or
        -not [string]::Equals($installed.Text, '1.0.1', [StringComparison]::Ordinal)) {
        throw 'V1.0.1 遗留状态显示版本已提交，但安装目录版本不一致。'
    }
    if (-not (Test-NormalInstallRoot -Root $InstallRoot)) {
        throw 'V1.0.1 已提交目录不完整，拒绝清除恢复标记。'
    }

    # V1.0.1 的已提交语义是：提交后只观察当前运行状态，不再强行恢复
    # 升级前状态。这里保持该语义，也不要求当时尚不存在的 install_id。
    Write-Log "发现 V1.0.1 已提交遗留事务，正在兼容收尾，原阶段=$($State.Stage)" 'WARN'
    if (Test-SystemRunning -ProgramRoot $programRoot) {
        Write-Log 'V1.0.1 已提交版本当前正在运行；保持当前状态。'
    }
    else {
        Write-Log 'V1.0.1 已提交版本当前处于停止状态；保持当前状态。'
    }
    Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$State.TransactionId)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log "V1.0.1 已提交遗留事务已清理；程序、data 和当前运行状态均未改变。旧完整快照继续保留：$($State.SnapshotPath)"
}

function Assert-RollbackRestoredState {
    param($State)
    Assert-CurrentTransactionStateEnvelope -State $State -Context '回滚完成状态'
    if ([string]$State.Stage -ne 'rollback_restored') {
        throw '回滚完成状态内容非法。'
    }
}

function Recover-RollbackRestoredTransaction {
    param([string]$InstallRoot, $State, [string]$StatePath)
    Assert-RollbackRestoredState -State $State
    Write-Log '发现已经恢复完成但尚未收尾的回滚；不会再次覆盖 data。' 'WARN'
    Restore-ExpectedRunState -InstallRoot $InstallRoot -WasRunning ([bool]$State.WasRunning) `
        -TaskExists ([bool]$State.TaskExists) -TaskEnabled ([bool]$State.TaskEnabled) `
        -TaskWasRunning ([bool]$State.TaskWasRunning)
    Remove-VersionTemporaryFile -ProgramRoot (Join-Path $InstallRoot '_程序文件') `
        -TransactionId ([string]$State.TransactionId)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log '回滚收尾完成；失败现场快照已保留供维护人员核查。'
}

function Invoke-Rollback {
    param([string]$InstallRoot, [string]$PayloadRoot, $State, [string]$StatePath)
    Assert-CurrentTransactionStateEnvelope -State $State -Context '待回滚升级状态'
    if (@('snapshot_ready', 'program_replaced', 'migration_complete', 'healthcheck_passed') `
        -notcontains [string]$State.Stage) {
        throw '待回滚升级状态的阶段非法。'
    }
    if ($State.SnapshotPath -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$State.SnapshotPath)) {
        throw '待回滚升级状态缺少快照路径。'
    }
    $programRoot = Join-Path $InstallRoot '_程序文件'
    Write-Log "开始统一回滚，阶段=$($State.Stage)" 'WARN'
    $snapshot = Get-ValidatedSnapshot -ProgramRoot $programRoot -State $State
    Disable-OwnedTaskForTransaction -InstallRoot $InstallRoot -TaskState ([pscustomobject]@{
        Exists = [bool]$State.TaskExists
    })
    Stop-OwnedRuntimeProcesses -ProgramRoot $programRoot -TaskExists ([bool]$State.TaskExists)
    Remove-ManagedProgram -InstallRoot $InstallRoot
    Copy-FixedManagedFiles -SourceRoot (Join-Path $snapshot.Root 'program') -DestinationRoot $InstallRoot
    foreach ($folder in @('static', 'templates')) {
        $source = Join-Path $snapshot.Root (Join-Path 'program\_程序文件' $folder)
        if (Test-Path -LiteralPath $source -PathType Container) {
            Invoke-Robocopy -Source $source -Destination (Join-Path $programRoot $folder) -Mirror
        }
    }
    Assert-RestoredProgram -InstallRoot $InstallRoot -ProgramManifest $snapshot.ProgramManifest
    Clear-RootPythonCache -ProgramRoot $programRoot

    $dataRoot = Join-Path $programRoot 'data'
    if (Test-Path -LiteralPath $dataRoot) {
        $failedData = Join-Path $snapshot.Root ('failed-data-{0}' -f (Get-Date -Format 'yyyyMMdd_HHmmssfff'))
        Move-Item -LiteralPath $dataRoot -Destination $failedData
        Write-Log "失败现场 data 已保留：$failedData" 'WARN'
    }
    New-Item -ItemType Directory -Path $dataRoot | Out-Null
    Invoke-Robocopy -Source (Join-Path $snapshot.Root 'data') -Destination $dataRoot -Mirror
    Assert-ManifestMatches -Root $dataRoot -Records $snapshot.DataManifest.Files
    Test-Database -PayloadRoot $PayloadRoot -ProgramRoot $programRoot -DatabasePath (Join-Path $dataRoot 'reservation.db')
    # 先把“数据已经恢复”作为耐久终态落盘，再允许旧服务重新对用户开放。
    # 即使随后断电，恢复路径也只收尾，不会再次把 data 覆盖为更早的快照。
    Update-TransactionStage -State $State -StatePath $StatePath -Stage 'rollback_restored'
    Restore-ExpectedRunState -InstallRoot $InstallRoot -WasRunning ([bool]$State.WasRunning) `
        -TaskExists ([bool]$State.TaskExists) -TaskEnabled ([bool]$State.TaskEnabled) `
        -TaskWasRunning ([bool]$State.TaskWasRunning)
    Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$State.TransactionId)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log '统一回滚完成，旧程序、旧数据和原运行状态均已恢复。'
}

function Update-TransactionStage {
    param(
        $State,
        [string]$StatePath,
        [string]$Stage,
        [string]$SnapshotPath = $null,
        [string]$BackupPath = $null,
        [string]$InstallId = $null
    )
    # 先写副本，只有同卷原子替换成功后才更新内存；磁盘状态始终是恢复分支的真相。
    $nextState = (($State | ConvertTo-Json -Depth 8) | ConvertFrom-Json)
    $nextState.Stage = $Stage
    if ($PSBoundParameters.ContainsKey('SnapshotPath')) { $nextState.SnapshotPath = $SnapshotPath }
    if ($PSBoundParameters.ContainsKey('BackupPath')) { $nextState.BackupPath = $BackupPath }
    if ($PSBoundParameters.ContainsKey('InstallId')) { $nextState.InstallId = $InstallId }
    Write-JsonAtomic -Path $StatePath -Value $nextState
    $State.Stage = $nextState.Stage
    $State.SnapshotPath = $nextState.SnapshotPath
    $State.BackupPath = $nextState.BackupPath
    $State.InstallId = $nextState.InstallId
    Write-Log "事务阶段更新：$Stage"
}

function Clear-RootPythonCache {
    param([string]$ProgramRoot)
    $cache = Join-Path $ProgramRoot '__pycache__'
    if (Test-Path -LiteralPath $cache) {
        Assert-NoReparsePoints -Path $cache
        Remove-Item -LiteralPath $cache -Recurse -Force
        Write-Log "已清理可丢弃的 Python 根层缓存：$cache"
    }
}

function Install-PayloadProgram {
    param([string]$PayloadRoot, [string]$InstallRoot)
    Copy-FixedManagedFiles -SourceRoot $PayloadRoot -DestinationRoot $InstallRoot -ExcludeVersion -RequireAll
    $payloadProgram = Join-Path $PayloadRoot '_程序文件'
    $installedProgram = Join-Path $InstallRoot '_程序文件'
    Invoke-Robocopy -Source (Join-Path $payloadProgram 'static') -Destination (Join-Path $installedProgram 'static') -Mirror
    Invoke-Robocopy -Source (Join-Path $payloadProgram 'templates') -Destination (Join-Path $installedProgram 'templates') -Mirror
}

function Assert-InstalledMatchesPayload {
    param([string]$InstallRoot, $PayloadManifest)
    $expected = @($PayloadManifest | Where-Object { [string]$_.RelativePath -ine '_程序文件/版本.txt' })
    $expectedSet = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($record in $expected) {
        $relative = [string]$record.RelativePath
        [void]$expectedSet.Add($relative)
        $path = Resolve-SafeRelativeChild -Root $InstallRoot -RelativePath $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "覆盖后缺少受管文件：$relative" }
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or [int64]$item.Length -ne [int64]$record.Length -or (Get-Sha256Hex -Path $path) -ne [string]$record.Sha256) {
            throw "覆盖后受管文件校验失败：$relative"
        }
    }
    $actualSet = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($relative in @($script:TopFiles + ($script:ProgramFiles | Where-Object { $_ -ine '_程序文件/版本.txt' }))) {
        $path = Join-Path $InstallRoot $relative.Replace('/', '\')
        if (Test-Path -LiteralPath $path -PathType Leaf) { [void]$actualSet.Add($relative) }
    }
    foreach ($folder in @('static', 'templates')) {
        $root = Join-Path $InstallRoot (Join-Path '_程序文件' $folder)
        Assert-NoReparsePoints -Path $root
        foreach ($file in Get-ChildItem -LiteralPath $root -Force -Recurse -File) {
            [void]$actualSet.Add((Get-RelativeSlashPath -Root $InstallRoot -FullName $file.FullName))
        }
    }
    if (-not $expectedSet.SetEquals($actualSet)) { throw '覆盖后的受管程序文件集合与累计负载不一致。' }
}

function Invoke-Migration {
    param([string]$ProgramRoot)
    $python = Join-Path $ProgramRoot 'runtime\python.exe'
    $migration = Join-Path $ProgramRoot 'migrate_check.py'
    $result = Invoke-NativeCommand -FilePath $python -Arguments @($migration, '--migrate') -WorkingDirectory $ProgramRoot
    if ($result.ExitCode -ne 0) { throw '数据库迁移或升级后自检失败。' }
}

function Test-NewVersionByStartingService {
    param([string]$InstallRoot, [string]$OriginalInstallId = $null)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    $python = Join-Path $programRoot 'runtime\python.exe'
    $server = Join-Path $programRoot 'server.py'
    $temporaryProcess = $null
    $installId = $null
    Assert-ServicePortFree
    Write-Log '仅在 127.0.0.1 临时启动新版服务进行维护态健康检查；此时局域网用户无法写入。'
    $oldUpgradeCheck = [Environment]::GetEnvironmentVariable('MEETING_ROOM_UPGRADE_CHECK')
    $oldOpenBrowser = [Environment]::GetEnvironmentVariable('MEETING_ROOM_OPEN_BROWSER')
    try {
        $env:MEETING_ROOM_UPGRADE_CHECK = '1'
        $env:MEETING_ROOM_OPEN_BROWSER = '0'
        $temporaryProcess = Start-Process -FilePath $python -ArgumentList @(('"{0}"' -f $server)) `
            -WorkingDirectory $programRoot -WindowStyle Hidden -PassThru
    }
    finally {
        if ($null -eq $oldUpgradeCheck) { Remove-Item Env:MEETING_ROOM_UPGRADE_CHECK -ErrorAction SilentlyContinue }
        else { $env:MEETING_ROOM_UPGRADE_CHECK = $oldUpgradeCheck }
        if ($null -eq $oldOpenBrowser) { Remove-Item Env:MEETING_ROOM_OPEN_BROWSER -ErrorAction SilentlyContinue }
        else { $env:MEETING_ROOM_OPEN_BROWSER = $oldOpenBrowser }
    }
    try {
        $deadline = (Get-Date).AddSeconds(15)
        $installId = $null
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $temporaryProcess.Id -ErrorAction SilentlyContinue)) {
                throw "新版健康检查进程提前退出（PID=$($temporaryProcess.Id)）。"
            }
            $installId = Get-InstallId -ProgramRoot $programRoot -AllowMissing
            if ($installId) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $installId) { throw '新版服务没有生成安装标识。' }
        if ($OriginalInstallId -and
            -not [string]::Equals($installId, $OriginalInstallId, [StringComparison]::Ordinal)) {
            throw '升级过程中安装标识发生变化，拒绝继续。'
        }
        Wait-ServiceHealth -ProgramRoot $programRoot -ExpectedInstallId $installId `
            -ExpectedMode 'upgrade-check' -ExpectedProcessId $temporaryProcess.Id
        Assert-LoopbackListenerOwnedByProcess -ProcessId $temporaryProcess.Id
        Write-Log "回环维护态健康检查通过，安装标识=$installId，PID=$($temporaryProcess.Id)"
    }
    finally {
        if ($null -ne $temporaryProcess -and -not $temporaryProcess.HasExited) {
            Stop-Process -Id $temporaryProcess.Id -Force -ErrorAction SilentlyContinue
            try { $temporaryProcess.WaitForExit(5000) | Out-Null } catch {}
        }
    }
    Wait-ServicePortFree
    if (Test-SystemRunning -ProgramRoot $programRoot) { throw '临时健康检查后仍有本安装目录服务进程。' }
    return $installId
}

function Commit-VersionFile {
    param([string]$PayloadRoot, [string]$ProgramRoot, [string]$TransactionId)
    $source = Join-Path $PayloadRoot '_程序文件\版本.txt'
    $destination = Join-Path $ProgramRoot '版本.txt'
    $temporary = Join-Path $ProgramRoot ('.版本.txt.upgrade-{0}.tmp' -f $TransactionId)
    Copy-Item -LiteralPath $source -Destination $temporary -Force
    $text = Read-Utf8NoBom -Path $temporary
    if ($text -notmatch '^\d+\.\d+\.\d+(\r?\n)?$' -or $text.TrimEnd([char[]]"`r`n") -ne $script:PackageVersionText) {
        throw '最终版本文件校验失败。'
    }
    if (Test-Path -LiteralPath $destination) {
        Replace-FileAtomic -Temporary $temporary -Destination $destination
    }
    else { [IO.File]::Move($temporary, $destination) }
    Write-Log "版本最后提交完成：$($script:PackageVersionText)"
}

function Remove-VersionTemporaryFile {
    param([string]$ProgramRoot, [string]$TransactionId)
    if ($TransactionId -match '^[0-9a-fA-F]{32}$') {
        $path = Join-Path $ProgramRoot ('.版本.txt.upgrade-{0}.tmp' -f $TransactionId)
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
}

function Test-LanHttpUrl {
    param([string]$Value)
    if (-not $Value -or $Value.Length -gt 128) { return $false }
    # 不依赖 [Uri] 的宽松规范化：必须显式写出 IPv4 和端口，并且已经是
    # 本系统保存的无尾斜杠 canonical 形式。
    if ($Value -notmatch '^http://(?<address>(?:[0-9]{1,3}\.){3}[0-9]{1,3}):(?<port>[0-9]{1,5})$') {
        return $false
    }
    $addressText = [string]$matches.address
    $portText = [string]$matches.port
    $address = $null
    $port = 0
    if (-not [Net.IPAddress]::TryParse($addressText, [ref]$address) -or
        -not [int]::TryParse(
            $portText,
            [Globalization.NumberStyles]::None,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$port
        ) -or
        $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        return $false
    }
    if ($port -lt 1 -or $port -gt 65535) { return $false }
    $canonical = 'http://{0}:{1}' -f $address.ToString(), $port
    if (-not [string]::Equals($Value, $canonical, [StringComparison]::Ordinal)) {
        return $false
    }
    $bytes = $address.GetAddressBytes()
    $private = $bytes[0] -eq 10 -or
               ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
               ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
    if (-not $private -or $bytes[0] -eq 127 -or
        ($bytes[0] -eq 169 -and $bytes[1] -eq 254) -or
        ($bytes[0] -eq 198 -and ($bytes[1] -eq 18 -or $bytes[1] -eq 19))) {
        return $false
    }
    return $true
}

function Get-LanAddressUpgradeNotice {
    param([string]$ProgramRoot)
    $path = Join-Path $ProgramRoot 'data\局域网访问地址状态.json'
    try {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            return [pscustomobject]@{ Kind = 'unknown'; OldUrl = $null; NewUrl = $null; CurrentUrl = $null }
        }
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt 65536) {
            throw '地址状态文件大小或属性异常。'
        }
        $state = (Read-Utf8NoBom -Path $path) | ConvertFrom-Json
        $topNames = @($state.PSObject.Properties.Name | Sort-Object)
        if (($topNames -join ',') -ne 'last_acknowledged_url,last_observed_url,pending,schema' -or
            (($state.schema -isnot [int]) -and ($state.schema -isnot [long])) -or
            [int64]$state.schema -ne 1) {
            throw '地址状态文件结构不符合 V1。'
        }
        $acknowledged = [string]$state.last_acknowledged_url
        $current = [string]$state.last_observed_url
        if (-not (Test-LanHttpUrl -Value $acknowledged) -or
            -not (Test-LanHttpUrl -Value $current)) {
            throw '已确认或当前局域网 URL 非法。'
        }
        if ($null -eq $state.pending) {
            if (-not [string]::Equals($acknowledged, $current, [StringComparison]::Ordinal)) {
                throw '地址状态没有 pending，但已确认地址与当前地址不一致。'
            }
            return [pscustomobject]@{ Kind = 'same'; OldUrl = $null; NewUrl = $null; CurrentUrl = $current }
        }
        $pendingKind = [string]$state.pending.kind
        $pendingNames = @($state.pending.PSObject.Properties.Name | Sort-Object)
        if ($pendingKind -eq 'changed') {
            if (($pendingNames -join ',') -ne 'detected_at,kind,new_url,old_url') {
                throw '地址变更提醒结构非法。'
            }
        }
        elseif ($pendingKind -eq 'verify') {
            if (($pendingNames -join ',') -ne 'detected_at,kind,new_url') {
                throw '地址核对提醒结构非法。'
            }
        }
        else {
            throw '地址提醒类型非法。'
        }
        $oldUrl = if ($pendingKind -eq 'changed') { [string]$state.pending.old_url } else { $null }
        $newUrl = [string]$state.pending.new_url
        $detectedAt = [string]$state.pending.detected_at
        $parsedDetectedAt = [DateTimeOffset]::MinValue
        $isoOffsetShape = '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?(?:Z|[+-][0-9]{2}:[0-9]{2})$'
        if (-not (Test-LanHttpUrl -Value $newUrl) -or
            -not [string]::Equals($newUrl, [string]$state.last_observed_url, [StringComparison]::Ordinal) -or
            -not $detectedAt -or $detectedAt.Length -gt 64 -or
            $detectedAt -notmatch $isoOffsetShape -or
            -not [DateTimeOffset]::TryParse(
                $detectedAt,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None,
                [ref]$parsedDetectedAt
            )) {
            throw '地址提醒内容非法。'
        }
        if ($pendingKind -eq 'changed' -and
            (-not (Test-LanHttpUrl -Value $oldUrl) -or
                [string]::Equals($oldUrl, $newUrl, [StringComparison]::Ordinal) -or
                -not [string]::Equals($oldUrl, [string]$state.last_acknowledged_url, [StringComparison]::Ordinal))) {
            throw '地址变更提醒内容非法。'
        }
        if ($pendingKind -eq 'verify' -and
            -not [string]::Equals([string]$state.last_acknowledged_url, [string]$state.last_observed_url, [StringComparison]::Ordinal)) {
            throw '地址核对提醒内容非法。'
        }
        return [pscustomobject]@{ Kind = $pendingKind; OldUrl = $oldUrl; NewUrl = $newUrl; CurrentUrl = $newUrl }
    }
    catch {
        Write-Log "读取局域网地址提醒失败，不影响升级结果：$($_.Exception.Message)" 'WARN'
        return [pscustomobject]@{ Kind = 'unknown'; OldUrl = $null; NewUrl = $null; CurrentUrl = $null }
    }
}

function Show-LanAddressUpgradeNotice {
    param([string]$ProgramRoot, [bool]$WasRunning)
    if (-not $WasRunning) {
        Write-User ''
        Write-User '系统在升级前就是停止状态，现已继续保持停止；下次启动时会自动检查同事访问地址。' Green
        return
    }
    $health = $null
    try {
        $installId = Get-InstallId -ProgramRoot $ProgramRoot
        $health = Get-ValidatedServiceHealth `
            -ExpectedInstallId $installId -ExpectedMode 'normal'
    }
    catch {
        Write-Log "无法验证升级后服务报告的局域网地址：$($_.Exception.Message)" 'WARN'
    }
    $notice = Get-LanAddressUpgradeNotice -ProgramRoot $ProgramRoot
    Write-User ''
    if ($null -eq $health -or -not $health.LanUrl -or
        [string]$notice.Kind -eq 'unknown' -or
        -not [string]::Equals(
            [string]$health.LanUrl,
            [string]$notice.CurrentUrl,
            [StringComparison]::Ordinal
        )) {
        Write-User '本次升级不对同事访问地址作结论。' Yellow
        Write-User '请查看服务器启动窗口提示，并用同事电脑实际打开核对；如果仍无法确认，请联系维护人员。' Yellow
        return
    }
    if ([string]$notice.Kind -eq 'changed') {
        Write-User '注意：同事访问地址和上一个版本相比已经变化。' Yellow
        Write-User ("旧地址：{0}" -f $notice.OldUrl) Yellow
        Write-User ("新地址：{0}" -f $notice.NewUrl) Green
        Write-User '预约数据没有变化。请把新地址复制发给同事；管理员页面也会持续提醒，直到您确认。' Yellow
    }
    elseif ([string]$notice.Kind -eq 'verify') {
        Write-User '局域网地址记录已自动修复，请核对当前网址并通知同事。' Yellow
        Write-User ("当前待核对地址：{0}" -f $notice.NewUrl) Green
        Write-User '预约数据没有变化；管理员页面会持续提醒，直到您确认。' Yellow
    }
    elseif ([string]$notice.Kind -eq 'same') {
        if ($notice.CurrentUrl) {
            Write-User ("同事访问地址未发现变化：{0}" -f $notice.CurrentUrl) Green
        }
        else {
            Write-User '本次未发现同事访问地址变化。' Green
        }
    }
    else {
        Write-User '本次升级不对同事访问地址作结论。' Yellow
        Write-User '请查看服务器启动窗口提示，并用同事电脑实际打开核对；如果仍无法确认，请联系维护人员。' Yellow
    }
}

function Invoke-Upgrade {
    Write-User ''
    Show-Stage '正在校验升级文件完整性'
    $payload = Initialize-Payload
    $targetVersion = [version]$script:PackageVersionText
    Show-Stage '正在定位本机安装目录'
    $installRoot = Find-InstallRoot
    Assert-PackageLocationSafe -InstallRoot $installRoot
    Assert-InstallLocationSafe -InstallRoot $installRoot
    $programRoot = Join-Path $installRoot '_程序文件'
    Initialize-Log -ProgramRoot $programRoot
    Write-Log "定位安装目录：$installRoot"
    if ($script:PayloadAttributeCheckDegraded) {
        Write-Log '当前 .NET 不提供 ZIP ExternalAttributes；已退化为路径白名单/黑名单校验，并保留解包后重解析点检查。' 'WARN'
    }
    Open-UpgradeLock -ProgramRoot $programRoot
    Show-Stage '正在验证冻结运行环境'
    # 在执行安装目录内任何 python.exe/pythonw.exe 之前，先用纯 PowerShell
    # 对冻结 runtime 做全树哈希校验，且只清理已知可丢弃的 Python 缓存。
    Assert-TrustedRuntime -ProgramRoot $programRoot
    $statePath = Join-Path $programRoot '_升级状态.json'
    $recoveredCommitted = $false

    if (Test-Path -LiteralPath $statePath) {
        Write-User '发现上次升级状态，正在安全处理……' Yellow
        try {
            $oldState = (Read-Utf8NoBom -Path $statePath) | ConvertFrom-Json
            $legacyCommittedHandoff = Test-CommittedTransactionState `
                -ProgramRoot $programRoot -State $oldState
            $legacyV101State = Convert-LegacyV101TransactionState `
                -InstallRoot $installRoot -State $oldState `
                -CommittedHandoff:$legacyCommittedHandoff
            if ($null -ne $legacyV101State) {
                if ($legacyCommittedHandoff) {
                    Recover-LegacyV101CommittedTransaction -InstallRoot $installRoot `
                        -State $legacyV101State -StatePath $statePath
                    $recoveredCommitted = $true
                }
                else {
                    # 必须在停止任务/进程前把推导出的旧语义原子固化。若随后断电，
                    # 下次会直接按 Schema=2 恢复，不会从已被禁用的任务反推错误状态。
                    Write-JsonAtomic -Path $statePath -Value $legacyV101State
                    $oldState = $legacyV101State
                    Write-Log 'V1.0.1 遗留事务已原子规范化为 Schema=2。'
                }
            }
            if ($null -ne $legacyV101State -and $recoveredCommitted) {
                # 上方已按 V1.0.1 已提交语义完成收尾。
            }
            elseif (@('preparing', 'service_stopped', 'backup_ready') -contains [string]$oldState.Stage) {
                Recover-PreparingTransaction -InstallRoot $installRoot -State $oldState -StatePath $statePath
            }
            elseif ([string]$oldState.Stage -eq 'rollback_restored') {
                Recover-RollbackRestoredTransaction -InstallRoot $installRoot -State $oldState -StatePath $statePath
            }
            elseif (Test-CommittedTransactionState -ProgramRoot $programRoot -State $oldState) {
                Recover-CommittedTransaction -InstallRoot $installRoot -State $oldState -StatePath $statePath
                $recoveredCommitted = $true
            }
            else {
                Invoke-Rollback -InstallRoot $installRoot -PayloadRoot $payload.Root -State $oldState -StatePath $statePath
            }
            Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$oldState.TransactionId)
            Write-User '上次升级状态已安全处理，正在检查当前版本。' Green
        }
        catch {
            $script:KeepTemporary = $true
            Write-Log "未完成事务恢复失败：$($_.Exception.ToString())" 'ERROR'
            Throw-UpgradeFailure -Message '自动恢复没有完成，请不要继续操作，并把整个会议室预约系统文件夹交给维护人员。' -ExitCode 5
        }
    }

    if (-not (Test-NormalInstallRoot -Root $installRoot)) {
        Throw-UpgradeFailure -Message '未完成事务恢复后，安装目录仍不完整，请停止操作并联系维护人员。' -ExitCode 5
    }

    $installed = Read-InstalledVersion -ProgramRoot $programRoot
    Write-Log "当前版本=$($installed.Text)，目标版本=$($script:PackageVersionText)"
    if ($installed.Version -ge $targetVersion) {
        $currentlyRunning = Test-SystemRunning -ProgramRoot $programRoot
        if ($recoveredCommitted) {
            Write-User "V$($installed.Text) 的中断收尾已经安全完成，程序和数据没有回滚。" Green
        }
        else {
            Write-User "当前已经是 V$($installed.Text)，无需升级。" Green
        }
        if ($currentlyRunning) {
            Write-User '系统当前正在运行，可继续使用。' Green
        }
        else {
            Write-User '系统当前保持停止；需要使用时再双击“① 启动系统.bat”。' Green
        }
        Show-LanAddressUpgradeNotice -ProgramRoot $programRoot -WasRunning $currentlyRunning
        return 0
    }
    Assert-FreeSpace -InstallRoot $installRoot -Payload $payload

    $originalInstallId = Get-InstallId -ProgramRoot $programRoot -AllowMissing
    $processWasRunning = Test-SystemRunning -ProgramRoot $programRoot
    $taskState = Get-OwnedTaskState -InstallRoot $installRoot -AllowMissing
    Assert-CurrentPortOwnership -ProgramRoot $programRoot
    $latestTaskState = Get-OwnedTaskState -InstallRoot $installRoot -AllowMissing
    if ([bool]$taskState.Exists -ne [bool]$latestTaskState.Exists -or
        [bool]$taskState.Enabled -ne [bool]$latestTaskState.Enabled) {
        Throw-UpgradeFailure -Message '检测期间开机自动启动设置发生变化。升级尚未修改任何内容，请稍后重新双击升级包。' -ExitCode 4
    }
    $taskExists = [bool]$taskState.Exists
    $taskEnabled = [bool]$taskState.Enabled
    $taskWasRunning = [bool]$taskState.WasRunning -or [bool]$latestTaskState.WasRunning
    $wasRunning = $processWasRunning -or $taskWasRunning -or `
                  (Test-SystemRunning -ProgramRoot $programRoot)
    Assert-RunStateInvariants -WasRunning $wasRunning -TaskExists $taskExists `
        -TaskEnabled $taskEnabled -TaskWasRunning $taskWasRunning -Context '升级前状态'
    Write-Log "升级前状态：WasRunning=$wasRunning，TaskExists=$taskExists，TaskEnabled=$taskEnabled，TaskWasRunning=$taskWasRunning，InstallId=$originalInstallId"
    $systemStopped = $false
    $transactionId = [Guid]::NewGuid().ToString('N')
    $state = [pscustomobject][ordered]@{
        Schema = $script:TransactionStateSchema
        TransactionId = $transactionId
        PackageVersion = $script:PackageVersionText
        SnapshotPath = $null
        BackupPath = $null
        InstallId = $null
        Stage = 'preparing'
        OriginalVersion = $installed.Text
        OriginalVersionExisted = [bool]$installed.Existed
        OriginalInstallId = $originalInstallId
        WasRunning = [bool]$wasRunning
        TaskExists = [bool]$taskExists
        TaskEnabled = [bool]$taskEnabled
        TaskWasRunning = $taskWasRunning
    }
    Write-JsonAtomic -Path $statePath -Value $state
    Write-Log '已在停机前持久化 preparing 状态。'
    $transactionCommitted = $false
    try {
        # 从开始停机起就必须按原状态恢复；停止函数可能在已结束部分进程后才报错。
        Show-Stage '正在暂停服务并保护自动启动状态'
        $systemStopped = $true
        Disable-OwnedTaskForTransaction -InstallRoot $installRoot -TaskState $taskState
        Stop-OwnedRuntimeProcesses -ProgramRoot $programRoot -TaskExists $taskExists
        Wait-ServicePortFree
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'service_stopped'

        Show-Stage '正在检查数据库并创建升级前备份'
        Test-Database -PayloadRoot $payload.Root -ProgramRoot $programRoot -DatabasePath (Join-Path $programRoot 'data\reservation.db')
        $backupPath = New-StandardPreUpgradeBackup -PayloadRoot $payload.Root -ProgramRoot $programRoot
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'backup_ready' -BackupPath $backupPath

        $snapshotRoot = New-RollbackSnapshot -InstallRoot $installRoot -PayloadRoot $payload.Root -TransactionId $transactionId
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'snapshot_ready' -SnapshotPath $snapshotRoot
        Write-Log '回滚快照已验证；从此处开始的失败将进入统一回滚。'

        Show-Stage '正在更新程序文件'
        Assert-ManifestMatches -Root $payload.Root -Records $payload.Manifest
        Install-PayloadProgram -PayloadRoot $payload.Root -InstallRoot $installRoot
        Assert-InstalledMatchesPayload -InstallRoot $installRoot -PayloadManifest $payload.Manifest
        Clear-RootPythonCache -ProgramRoot $programRoot
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'program_replaced'
        Invoke-Migration -ProgramRoot $programRoot
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'migration_complete'

        Show-Stage '正在本机维护模式验证新版'
        $verifiedInstallId = Test-NewVersionByStartingService -InstallRoot $installRoot -OriginalInstallId $originalInstallId
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'healthcheck_passed' -InstallId $verifiedInstallId
        Commit-VersionFile -PayloadRoot $payload.Root -ProgramRoot $programRoot -TransactionId $transactionId
        # 版本.txt 是事务提交点。提交后即使状态清理失败，也绝不能回滚程序或 data。
        $transactionCommitted = $true
        # 在任何真实 LAN 服务恢复之前，必须先把已提交状态耐久落盘。
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'version_committed'

        Show-Stage '正在恢复升级前的运行状态'
        Restore-ExpectedRunState -InstallRoot $installRoot -WasRunning $wasRunning `
            -TaskExists $taskExists -TaskEnabled $taskEnabled -TaskWasRunning $taskWasRunning `
            -ExpectedInstallId $verifiedInstallId -RequireHealth
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'service_restored'

        try {
            Remove-SuccessfulTransactionSnapshot -ProgramRoot $programRoot -State $state
            Remove-Item -LiteralPath $statePath -Force
            Write-Log '升级事务成功完成；标准数据库备份已保留，完整事务副本和状态文件已清理。'
        }
        catch {
            # 此时 service_restored 已耐久落盘，用户可能开始写入；只允许下次安全
            # 清理成功快照，绝不能再回滚程序或 data。
            Write-Log "版本和运行状态已提交，但成功快照清理未完成；下次运行将自动安全收尾：$($_.Exception.Message)" 'WARN'
        }
        Write-User ''
        Write-User "升级成功！当前版本 V$($script:PackageVersionText)" Green
        Write-User '账号、会议室和预约数据均已保留；升级前数据库备份也已保存。' Green
        if ($wasRunning) {
            Write-User '系统已按升级前状态恢复运行，可继续使用。' Green
        }
        else {
            Write-User '系统已按升级前状态保持停止；需要使用时再双击“① 启动系统.bat”。' Green
        }
        Show-LanAddressUpgradeNotice -ProgramRoot $programRoot -WasRunning $wasRunning
        return 0
    }
    catch {
        $failure = $_
        if ($transactionCommitted) {
            Write-Log "事务已提交，提交后恢复服务或收尾失败；不会回滚新版数据：$($failure.Exception.ToString())" 'ERROR'
            Write-User ''
            Write-User "新版 V$($script:PackageVersionText) 已安全写入，数据不会回滚；但原运行状态没有完全恢复。" Yellow
            Write-User '请再次双击本升级包让它自动收尾；如果仍失败，请把最新升级日志交给维护人员。' Red
            return 5
        }
        Write-Log "升级失败：$($failure.Exception.ToString())" 'ERROR'
        if ($state -ne $null) {
            try {
                $durableState = $state
                if (Test-Path -LiteralPath $statePath) {
                    $durableState = (Read-Utf8NoBom -Path $statePath) | ConvertFrom-Json
                }
                if (@('preparing', 'service_stopped', 'backup_ready') -contains [string]$durableState.Stage) {
                    Recover-PreparingTransaction -InstallRoot $installRoot -State $durableState -StatePath $statePath
                }
                elseif ([string]$durableState.Stage -eq 'rollback_restored') {
                    Recover-RollbackRestoredTransaction -InstallRoot $installRoot -State $durableState -StatePath $statePath
                }
                elseif (Test-CommittedTransactionState -ProgramRoot $programRoot -State $durableState) {
                    Recover-CommittedTransaction -InstallRoot $installRoot -State $durableState -StatePath $statePath
                    Write-User ''
                    Write-User "升级成功！当前版本 V$([string]$durableState.PackageVersion)" Green
                    return 0
                }
                else {
                    Invoke-Rollback -InstallRoot $installRoot -PayloadRoot $payload.Root -State $durableState -StatePath $statePath
                }
                Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId $transactionId
                Write-User ''
                Write-User '升级失败，已自动还原，您的数据没有受影响。' Yellow
                return 1
            }
            catch {
                $script:KeepTemporary = $true
                Write-Log "回滚本身失败：$($_.Exception.ToString())" 'ERROR'
                Write-User ''
                Write-User '自动恢复没有完成，请不要继续操作，并把整个会议室预约系统文件夹交给维护人员。' Red
                return 5
            }
        }
        if ($systemStopped) {
            try {
                Restore-ExpectedRunState -InstallRoot $installRoot -WasRunning $wasRunning `
                    -TaskExists $taskExists -TaskEnabled $taskEnabled `
                    -TaskWasRunning $taskWasRunning
            }
            catch {
                Write-Log "尚未修改程序，但恢复原运行状态失败：$($_.Exception.ToString())" 'ERROR'
                Write-User '升级未修改程序或数据，但原系统未能重新启动，请联系维护人员。' Red
                return 5
            }
        }
        throw $failure
    }
}

$finalExitCode = 1
try {
    $finalExitCode = Invoke-Upgrade
}
catch {
    $finalExitCode = Get-ExitCodeFromError -ErrorRecord $_ -DefaultCode 1
    Write-Log ($_.Exception.ToString()) 'ERROR'
    Write-User ''
    Write-User ($_.Exception.Message) Red
    if ($finalExitCode -eq 1) { Write-User '升级未完成；如果系统曾被修改，升级器已尝试自动还原。' Yellow }
}
finally {
    if ($script:LockStream) { $script:LockStream.Dispose(); $script:LockStream = $null }
    if ($script:TempRoot -and -not $script:KeepTemporary -and (Test-Path -LiteralPath $script:TempRoot)) {
        Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Log "升级器退出，退出码=$finalExitCode"
}
exit $finalExitCode
__UPGRADE_PAYLOAD_BELOW__
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
