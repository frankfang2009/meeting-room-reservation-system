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
