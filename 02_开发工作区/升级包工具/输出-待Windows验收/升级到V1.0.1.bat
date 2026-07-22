@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统升级

set "MEETING_ROOM_UPGRADE_BAT=%~f0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $principal=New-Object Security.Principal.WindowsPrincipal($identity); if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}" >nul 2>&1
if not "%errorlevel%"=="0" goto :need_elevation
goto :run_upgrade

:need_elevation
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try{$child=Start-Process -FilePath $env:MEETING_ROOM_UPGRADE_BAT -Verb RunAs -Wait -PassThru; exit $child.ExitCode}catch{exit 3}"
set "UPGRADE_RC=%errorlevel%"
if "%UPGRADE_RC%"=="3" goto :uac_cancelled
exit /b %UPGRADE_RC%

:uac_cancelled
echo.
echo 升级未开始，未修改任何文件。
echo.
pause
exit /b 3

:run_upgrade
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$bat=$env:MEETING_ROOM_UPGRADE_BAT; $tmp=$null; $rc=1; try{$all=[IO.File]::ReadAllText($bat,[Text.Encoding]::UTF8); $ps=[regex]::Matches($all,'(?m)^__UPGRADE_PS1_BELOW__\r?$'); $payload=[regex]::Matches($all,'(?m)^__UPGRADE_PAYLOAD_BELOW__\r?$'); if($ps.Count -ne 1 -or $payload.Count -ne 1 -or $ps[0].Index -ge $payload[0].Index){throw '升级文件结构损坏'}; $start=$ps[0].Index+$ps[0].Length; if($start -lt $all.Length -and $all[$start] -eq [char]10){$start++}; $length=$payload[0].Index-$start; if($length -le 0){throw '升级主程序为空'}; $tmp=Join-Path $env:TEMP ('meetingroom_upgrade_{0}.ps1' -f $PID); [IO.File]::WriteAllText($tmp,$all.Substring($start,$length),(New-Object Text.UTF8Encoding($true))); & ([IO.Path]::Combine($PSHOME,'powershell.exe')) -NoProfile -ExecutionPolicy Bypass -File $tmp -PackagePath $bat; $rc=$LASTEXITCODE}catch{Write-Host ''; Write-Host ('升级文件无法读取：'+$_.Exception.Message) -ForegroundColor Red; $rc=1}finally{if($tmp -and (Test-Path -LiteralPath $tmp)){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}}; exit $rc"
set "UPGRADE_RC=%errorlevel%"

echo.
if not "%UPGRADE_RC%"=="0" echo 如需帮助，请把“_程序文件\logs”中的最新升级日志交给维护人员。
echo.
pause
exit /b %UPGRADE_RC%
__UPGRADE_PS1_BELOW__
param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PackageVersionText = '1.0.1'
$script:ExpectedPayloadSha256 = '330ba7a1d65413147f65bccc49d939da8ba9f008bc3492411b7ab8072d2865ff'
$script:TaskName = '会议室预约系统'
$script:LogPath = $null
$script:LockStream = $null
$script:TempRoot = $null
$script:KeepTemporary = $false
$script:Utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
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
            $unixType = (($entry.ExternalAttributes -shr 16) -band 0xF000)
            if ($unixType -eq 0xA000 -or (($entry.ExternalAttributes -band 0x400) -ne 0)) { throw "负载含符号链接或重解析点：$normalized" }
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

function Find-InstallRoot {
    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add((Split-Path -Parent ([IO.Path]::GetFullPath($PackagePath))))
    foreach ($candidate in @('D:\会议室预约系统', 'C:\会议室预约系统', 'E:\会议室预约系统')) { $candidates.Add($candidate) }
    foreach ($base in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Downloads'))) {
        if ($base) { $candidates.Add((Join-Path $base '会议室预约系统')) }
    }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in $candidates) {
        try { $full = [IO.Path]::GetFullPath($candidate) } catch { continue }
        if ($seen.Add($full) -and (Test-InstallRoot -Root $full)) { return $full }
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
        return [IO.Path]::GetFullPath($dialog.SelectedPath)
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
    catch [IO.IOException] {
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
    param([string]$FilePath, [string[]]$Arguments = @(), [string]$WorkingDirectory = $null)
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
    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    foreach ($line in @($stdout, $stderr)) {
        if (-not [string]::IsNullOrWhiteSpace($line)) { Write-Log ($line.TrimEnd([char[]]"`r`n")) 'CMD' }
    }
    $code = $process.ExitCode
    $process.Dispose()
    return [pscustomobject]@{ ExitCode = [int]$code; Stdout = $stdout; Stderr = $stderr }
}

function Invoke-Robocopy {
    param([string]$Source, [string]$Destination, [switch]$Mirror)
    if (-not (Test-Path -LiteralPath $Destination)) { New-Item -ItemType Directory -Path $Destination | Out-Null }
    $arguments = @($Source, $Destination)
    if ($Mirror) { $arguments += '/MIR' } else { $arguments += '/E' }
    $arguments += @('/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/XJ', '/NP')
    $result = Invoke-NativeCommand -FilePath 'robocopy.exe' -Arguments $arguments
    if ($result.ExitCode -lt 0 -or $result.ExitCode -gt 7) { throw "复制失败（robocopy 退出码 $($result.ExitCode)）。" }
}

function Test-TaskExists {
    $result = Invoke-NativeCommand -FilePath 'schtasks.exe' -Arguments @('/Query', '/TN', $script:TaskName)
    return $result.ExitCode -eq 0
}

function Test-SystemRunning {
    param([string]$ProgramRoot)
    $python = Join-Path $ProgramRoot 'runtime\python.exe'
    $server = Join-Path $ProgramRoot 'server.py'
    $result = Invoke-NativeCommand -FilePath $python -Arguments @($server, '--check') -WorkingDirectory $ProgramRoot
    return $result.ExitCode -eq 0
}

function Stop-OwnedRuntimeProcesses {
    param([string]$ProgramRoot, [bool]$TaskExists)
    if ($TaskExists) {
        $endResult = Invoke-NativeCommand -FilePath 'schtasks.exe' -Arguments @('/End', '/TN', $script:TaskName)
        if ($endResult.ExitCode -ne 0) { Write-Log "计划任务当前可能未运行，/End 退出码=$($endResult.ExitCode)" 'WARN' }
    }
    $runtime = [IO.Path]::GetFullPath((Join-Path $ProgramRoot 'runtime')).TrimEnd('\') + '\'
    try { $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'") }
    catch { throw "无法枚举本系统进程：$($_.Exception.Message)" }
    foreach ($process in $processes) {
        if ($process.ExecutablePath) {
            $executable = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
            if ($executable.StartsWith($runtime, [StringComparison]::OrdinalIgnoreCase)) {
                Write-Log "停止本安装目录进程 PID=$($process.ProcessId)，路径=$executable"
                Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            }
        }
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-SystemRunning -ProgramRoot $ProgramRoot)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw '系统未能在 10 秒内停止。'
}

function Wait-SystemHealth {
    param([string]$ProgramRoot, [int]$Seconds = 30)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-SystemRunning -ProgramRoot $ProgramRoot) { return }
        Start-Sleep -Seconds 1
    }
    throw "新版服务未能在 $Seconds 秒内通过健康检查。"
}

function Start-PersistentSystem {
    param([string]$InstallRoot, [bool]$TaskExists)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    if ($TaskExists) {
        $result = Invoke-NativeCommand -FilePath 'schtasks.exe' -Arguments @('/Run', '/TN', $script:TaskName)
        if ($result.ExitCode -ne 0) { throw '计划任务无法启动系统。' }
    }
    else {
        $startBat = Join-Path $InstallRoot '① 启动系统.bat'
        Start-Process -FilePath $startBat -WorkingDirectory $InstallRoot -WindowStyle Minimized | Out-Null
    }
    Wait-SystemHealth -ProgramRoot $programRoot
}

function Test-Database {
    param([string]$PayloadRoot, [string]$ProgramRoot, [string]$DatabasePath)
    if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) { throw "数据库不存在：$DatabasePath" }
    $python = Join-Path $ProgramRoot 'runtime\python.exe'
    $checkScript = Join-Path $PayloadRoot '_程序文件\migrate_check.py'
    $result = Invoke-NativeCommand -FilePath $python -Arguments @($checkScript, '--precheck', $DatabasePath) -WorkingDirectory $ProgramRoot
    if ($result.ExitCode -ne 0) { throw "数据库完整性预检失败：$DatabasePath" }
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
    param([string]$InstallRoot, [bool]$WasRunning, [bool]$TaskExists)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    if ($WasRunning) {
        if (Test-SystemRunning -ProgramRoot $programRoot) {
            Write-Log '系统已处于运行状态，无需重复启动。'
            return
        }
        Start-PersistentSystem -InstallRoot $InstallRoot -TaskExists $TaskExists
    }
    else {
        Stop-OwnedRuntimeProcesses -ProgramRoot $programRoot -TaskExists $false
        if (Test-SystemRunning -ProgramRoot $programRoot) { throw '无法恢复升级前的停止状态。' }
    }
}

function Assert-PreparingState {
    param($State)
    if ([string]$State.Stage -ne 'preparing' -or [string]$State.TransactionId -notmatch '^[0-9a-fA-F]{32}$') {
        throw '升级准备状态内容非法。'
    }
    if ($State.WasRunning -isnot [bool] -or $State.TaskExists -isnot [bool]) {
        throw '升级准备状态中的运行信息非法。'
    }
    if ($null -ne $State.SnapshotPath -and -not [string]::IsNullOrEmpty([string]$State.SnapshotPath)) {
        throw '升级准备状态不应包含快照路径。'
    }
}

function Recover-PreparingTransaction {
    param([string]$InstallRoot, $State, [string]$StatePath)
    Assert-PreparingState -State $State
    Write-Log '发现停机或快照阶段中断；程序和数据尚未进入覆盖事务，正在恢复原运行状态。' 'WARN'
    Restore-ExpectedRunState -InstallRoot $InstallRoot -WasRunning ([bool]$State.WasRunning) -TaskExists ([bool]$State.TaskExists)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log 'preparing 状态已恢复并清除。'
}

function Invoke-Rollback {
    param([string]$InstallRoot, [string]$PayloadRoot, $State, [string]$StatePath)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    Write-Log "开始统一回滚，阶段=$($State.Stage)" 'WARN'
    $snapshot = Get-ValidatedSnapshot -ProgramRoot $programRoot -State $State
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
    Restore-ExpectedRunState -InstallRoot $InstallRoot -WasRunning ([bool]$State.WasRunning) -TaskExists ([bool]$State.TaskExists)
    Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$State.TransactionId)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log '统一回滚完成，旧程序、旧数据和原运行状态均已恢复。'
}

function Update-TransactionStage {
    param($State, [string]$StatePath, [string]$Stage, [string]$SnapshotPath = $null)
    # 先写副本，只有同卷原子替换成功后才更新内存；磁盘状态始终是恢复分支的真相。
    $nextState = (($State | ConvertTo-Json -Depth 8) | ConvertFrom-Json)
    $nextState.Stage = $Stage
    if ($PSBoundParameters.ContainsKey('SnapshotPath')) { $nextState.SnapshotPath = $SnapshotPath }
    Write-JsonAtomic -Path $StatePath -Value $nextState
    $State.Stage = $nextState.Stage
    $State.SnapshotPath = $nextState.SnapshotPath
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
    param([string]$InstallRoot, [bool]$WasRunning, [bool]$TaskExists)
    $programRoot = Join-Path $InstallRoot '_程序文件'
    if ($WasRunning) {
        Start-PersistentSystem -InstallRoot $InstallRoot -TaskExists $TaskExists
        return
    }

    $python = Join-Path $programRoot 'runtime\python.exe'
    $server = Join-Path $programRoot 'server.py'
    Write-Log '系统升级前未运行；临时启动新版服务进行真实健康检查。'
    $temporaryProcess = Start-Process -FilePath $python -ArgumentList @(('"{0}"' -f $server)) -WorkingDirectory $programRoot -WindowStyle Hidden -PassThru
    try {
        Wait-SystemHealth -ProgramRoot $programRoot
    }
    finally {
        Stop-OwnedRuntimeProcesses -ProgramRoot $programRoot -TaskExists $false
    }
    if (Test-SystemRunning -ProgramRoot $programRoot) { throw '临时健康检查后未能恢复原停止状态。' }
    Write-Log "临时服务健康检查通过并已停止，启动进程 PID=$($temporaryProcess.Id)"
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

function Invoke-Upgrade {
    Write-User ''
    Write-User '正在校验升级文件，请稍候……' Cyan
    $payload = Initialize-Payload
    $targetVersion = [version]$script:PackageVersionText
    $installRoot = Find-InstallRoot
    Assert-PackageLocationSafe -InstallRoot $installRoot
    $programRoot = Join-Path $installRoot '_程序文件'
    Initialize-Log -ProgramRoot $programRoot
    Write-Log "定位安装目录：$installRoot"
    Open-UpgradeLock -ProgramRoot $programRoot
    $statePath = Join-Path $programRoot '_升级状态.json'

    if (Test-Path -LiteralPath $statePath) {
        Write-User '发现上次升级未完成，正在先恢复原系统……' Yellow
        try {
            $oldState = (Read-Utf8NoBom -Path $statePath) | ConvertFrom-Json
            if ([string]$oldState.Stage -eq 'preparing') {
                Recover-PreparingTransaction -InstallRoot $installRoot -State $oldState -StatePath $statePath
            }
            else {
                Invoke-Rollback -InstallRoot $installRoot -PayloadRoot $payload.Root -State $oldState -StatePath $statePath
            }
            Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$oldState.TransactionId)
            Write-User '上次未完成的升级已安全恢复，正在重新升级。' Green
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
        Write-User "当前已经是 V$($installed.Text)，无需升级。" Green
        return 0
    }
    Assert-FreeSpace -InstallRoot $installRoot -Payload $payload

    $wasRunning = Test-SystemRunning -ProgramRoot $programRoot
    $taskExists = Test-TaskExists
    Write-Log "升级前状态：WasRunning=$wasRunning，TaskExists=$taskExists"
    $systemStopped = $false
    $transactionId = [Guid]::NewGuid().ToString('N')
    $state = [pscustomobject][ordered]@{
        TransactionId = $transactionId
        PackageVersion = $script:PackageVersionText
        SnapshotPath = $null
        Stage = 'preparing'
        OriginalVersion = $installed.Text
        OriginalVersionExisted = [bool]$installed.Existed
        WasRunning = [bool]$wasRunning
        TaskExists = [bool]$taskExists
    }
    Write-JsonAtomic -Path $statePath -Value $state
    Write-Log '已在停机前持久化 preparing 状态。'
    $transactionCommitted = $false
    try {
        # 从开始停机起就必须按原状态恢复；停止函数可能在已结束部分进程后才报错。
        $systemStopped = $true
        Stop-OwnedRuntimeProcesses -ProgramRoot $programRoot -TaskExists $taskExists
        Test-Database -PayloadRoot $payload.Root -ProgramRoot $programRoot -DatabasePath (Join-Path $programRoot 'data\reservation.db')
        $snapshotRoot = New-RollbackSnapshot -InstallRoot $installRoot -PayloadRoot $payload.Root -TransactionId $transactionId
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'snapshot_ready' -SnapshotPath $snapshotRoot
        Write-Log '回滚快照已验证；从此处开始的失败将进入统一回滚。'

        Assert-ManifestMatches -Root $payload.Root -Records $payload.Manifest
        Install-PayloadProgram -PayloadRoot $payload.Root -InstallRoot $installRoot
        Assert-InstalledMatchesPayload -InstallRoot $installRoot -PayloadManifest $payload.Manifest
        Clear-RootPythonCache -ProgramRoot $programRoot
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'program_replaced'
        Invoke-Migration -ProgramRoot $programRoot
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'migration_complete'
        Test-NewVersionByStartingService -InstallRoot $installRoot -WasRunning $wasRunning -TaskExists $taskExists
        Update-TransactionStage -State $state -StatePath $statePath -Stage 'healthcheck_passed'
        Commit-VersionFile -PayloadRoot $payload.Root -ProgramRoot $programRoot -TransactionId $transactionId
        Remove-Item -LiteralPath $statePath -Force
        # 状态文件删除是事务完成点；此后的日志或界面异常不得再触发回滚。
        $transactionCommitted = $true
        Write-Log '升级事务成功完成，状态文件已删除。'
        Write-User ''
        Write-User "升级成功！当前版本 V$($script:PackageVersionText)" Green
        return 0
    }
    catch {
        $failure = $_
        if ($transactionCommitted) {
            Write-Log "事务已提交，提交后的显示或日志步骤异常：$($failure.Exception.Message)" 'WARN'
            return 0
        }
        Write-Log "升级失败：$($failure.Exception.ToString())" 'ERROR'
        if ($state -ne $null) {
            try {
                $durableState = $state
                if (Test-Path -LiteralPath $statePath) {
                    $durableState = (Read-Utf8NoBom -Path $statePath) | ConvertFrom-Json
                }
                if ([string]$durableState.Stage -eq 'preparing') {
                    Recover-PreparingTransaction -InstallRoot $installRoot -State $durableState -StatePath $statePath
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
            try { Restore-ExpectedRunState -InstallRoot $installRoot -WasRunning $wasRunning -TaskExists $taskExists }
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
UEsDBBQAAAgIAAAAIQAT7aaydiUAABavAAAUAAAAX+eoi+W6j+aWh+S7ti9hcHAucHntPWlz29a1
3/krUMxkDDg0Izlr2TB5ikw7aizJpegsT9VgIBKUEJMAC4C2VT/P2M3mbLWbJm6cOIvzsnWJ7TZp
4tpO/V9akbI+5S+8czfgXuBioSSn6bw6GZsE73Luueece9aLjuf2FMPoDIKBZxmGYvf6rhcopuO4
gRnYruOXSvTZas9ssc9dd2XFdlbYV9dnn3yr5VlB9PUXXTuw7i110DRtM7ACu2exSdD3cvi0rKC/
21Y3MEnzzsBpBa7b9Vn7Y57Z98lvfTNY7drL7JdD8JX8EKz1AS72fMpZKyvTZrdrLndhgvk+WpHZ
LdEJuqZ/hDXVSgr82Y8elfFHcxkek4+tgedZTmCY/T55gHquko8r5B/Patue1QrYN6dteUZg9fpd
tEj68BcDy6ctfMv3ARbyZeB1jY7rlUs6AeyY5R35pTVYqQA2B54drDEgW6tW64jRN33/mOu1jVUE
hbJiOZYHs4jPS6XSocb8T+vTTWPfTEOpYRxpsNN2F/ZZr3iW73aPWppe6ZtocaV99f1Thw9C66nm
FOvCDXCPosJOmWqptDD9eH12yniy3liYmZ+DZpOl2ZkDjakmfFuoKl3bDxaDQb9rLdpOEKF/cZES
Q2XadRxAFSx/qazMuY61tLQEwywuwdjNqUbTaM7M1hfgCR6FbExHPdGznUFg+co99ygPTFQn9rZP
VsNnd7FHKmntegr7yXYUz3RWLO0hZTe0Uu5W7p0oK5MPsm+TZXigA+rrc/vu0Mw/xnPxk94bzloq
tYCYfKVh+ZZ3FLMc4KfTtVuBVj/esjDJ6lU8uKqqDdP2rbZybNVygEndYNXygLDCrorZ9Syzvaa4
xxxfAdQqbkeBRoz4oKvfdQO/AkPJpj7smEdNG+9X3uxoVN/qwkbCE88FsoWlU05RzFbLHTiIYBEC
fKUNZAxsuey6R+BfOnvb6ihG1zXbhusZIDYQDRP5oSH+rmKS1ZU9jyh+4BEY0HNKsJXeEWA5jXzx
a01vABxuHQfqM9wj+KuOuwTeGumL/hw1uwML9hePgzAFPHo80Cyn5bYBsJo6CDp7HlL1Csxo9zU9
7Gh3SN9oKMLSIDYd8gv+wcI4U/YDk825wX7AQbvuea4XdUNMWirxsFCRWQncI5ZjrFrHtXv3kokN
M3B7dss4BjLAIpAiwMukK2nTtzzfxhs75qpgRUA/Uf8IRA/tstKA/QOBjMHX1NHvPhx9+dbwzLvD
G9c3vryxceOD9Zvv3L7y3vDKi5tvfKLqJQ4d4ZBsj+XLIPtLF1NFe4z3GgmErW+2hQSl6a0xbByz
g1XDMXuW1lErJ/Aj9O1k5YTrV1asoG+3Nf1kJej1VQm5tC2/BRgLgLJrcMhV3L7laAIFhBOWhcfQ
dt54qjE/d/AZ5X/It+lGfarJvjQbh+emxS4T7gMTE9GjiPTQElCvThtPH8FUVtRjKqAhvs2K6SvA
d+1ujFrJswreBo0jodjvne7AX9X0+Ho6/prT0lgbIHDH1fSoFbTwLDjtWpYW4QTvAWnTseHg7XK4
FRAtoLIycLq2c4QDIZ+tItbCJEelCRzYAA0QSQtEqr1SDTWAxTbI10UguTLSEfDxg+gOEyDWAcjI
0B9+wQ/g6ESEA0cn4V5/0O93batNh0ZnRjQREoUnTuJ26NA0gHbZCRxCHBsAEaOmssNX5RDrIdxa
zlHbcx3SarZeb87MHTAa8/Oz4XkNlBA/wskgugBHUU5C7ZdNHwuoNEgfm1qow7SARi1cJugJ3HlU
aS+rlEiImDOOWGtpQy7UgUeaxhP1Z4CGYdkpZ0PEndyclWh4lVs1bGCFToE0K6MHD4BVojHYMmps
vREHRuDUosGjn2fmZpozUweNqX2zM3PGoamFhafmG/tqmXsl76PqyVFh6n31Ofxx/8zBeg3hWCB2
TEwMaKbEIUxsfnp+9KdLGxduDL996/ZXnw7PfgMyeuPD05XgeKBG4oVf5wJS5Izp+fknZurG3NRs
vab2LFDKnRUDHewGVVbV1D6PN5uHkKwj5JTWagFGXphpwugHzePcYLNTT0OTuSas1zhYnzvQfLy2
9/4HQFWanNh7X5nbTTiyeG4OB+B2edBH9gTP9JRfUZvAMr02aEZILsCP+CRqdV3fMtrL9MT1rBV0
dHkGVZiMVVBZfA06xBsw5R5O7C468WRtPBcpgrJfLCS+DCJN+b70CIXvVJIBEQF0GlGDEgp0leFF
BUbD5zkonRxm2suI2Wi3Fukm0hFn21AcLka8vSQeUUgfgBXVJifE5zaYEpjdja511OrWkCiVnWTt
5YrnHjM6Jth1+IhmkDXcY3wj6zjYPbCL6qHG1IHZKaRUW/aKg/jPh17zc6qe1Xx54K8ZFFZknkzA
n+we6GhbBZZ1B2j8/YcPHuTar1QwFtvL/Aahh+ysoRSkkU3lDpnHgDVDLVo4YyIlB4+9Uum7fQ3t
ITGIQnKHX20f72vUg64BT6vpTMeC89xGpN82evaKR0x3QjVbM8iqvJJo+7bjB6YDh7to/ZWB3gIs
rDOagNbf1XPUy7eujl6/PLz+240bvx29//zGy2dGF/80vPXC5qUbo7evjN76ChqoIVZiFujDyuQW
Rl+/9vrt574dXj27fv3XyqSqh0o5QeP2DFnMLaYHzOvTgWAI7jdkJgLXARMAv3LWM29vxBCPm5eJ
ZYoxDhoYeagrP6ope2OWSRYabt86vfHZDTgibl++Ovrw5vDmWaTev3WGI3oCfFkJiQlgx5NlQMj6
SEiC/RQjhaKQvnt59OFLWUTBAdSi26SFoOdP2InPqJwgEJ9UgExgsuHZK7evPrfx5ufDl74VZ6W7
XAH5aTltulCdNzgJPbEGWgKzOiU963ifGNM1TDEa8R3sLcfJ/W5lUo/OQzI9ogDWP5MXBEzIKYJg
eP3Gr5W9yvDmqeFnr35387XRay8T/N++9f7GjS/+eer08M0r69dOrV97c/3aJ0BEm6feGH70/nc3
31WFKTrq8Dq0uK6cYOCdhNGGl9/fvPACfkwXcFKNnReRaU3wx+QcNm/91qrVM42jyNB0wSRarkrO
Riz8QlmM+I9gBo4gLM7DUyCcGTTQg/XpJrXM9zfmZ9FBbPSswFSeerzeqCtEe90lzr+LapyVjhW0
VkEMRBY2mgsEuCi86dLQQ4mHgowJswDEGvRfnFjSed+C1lzrk80sK08iQPFnbPRZok2UyVnr177Y
eOd5XiAyGaBgHyQeiy2DAQWSFjE2UkbpIyx70HcKKXMxbBsKgQjIXIwECOdYlAqydj/Ce3QoAmZT
DsuS4OyNNiKF5sJNjvdIbDivcTxWPzAzp8zMztb3zUw167wkiRvEY0LCycFciCSQJX7DHDEzt1Bv
NMEsac5HvKAhQ4g6oZQnpw4eri8oWpwpysquyV26mhhWTzxJrnOylAKkOj0/OzvTVBOugVDNqsZx
Ab1txwhAmvpmK9kiPkFj/uDBx6amn1D15KFRStvxR2IyeisimGeCzT++DVrJ8NvfDl9+fePzV4fX
z5LnIDo3Pj09+uKj9W9vwWE0+t1n5NfhuSvDVz7nRahI9MYyKMRY0CLJh1zLEd3roUJCW4Ru6/gq
sa9cchhFq+W1Bcnki+Sfpe0yRji0jPaRDlBgz/M2JjrAUlWD0dlzaH9g3977YP36i+vXXx2+cim2
RZvvnEPq1dlz69c/zuOFLGZUDx8Cq6we8eBCvRl6kB/NPp3KidE0JLGpqlLWxd/1HyrvYeFvO3Zk
D8etKGoql1KsvGddOEzMrtFz2whrT00xKy9qS1yrnEagRpuGHbh1BUzjg3VlZr8yN99U6k/PLDQX
lIEPuFbEXbPbSGrWD9QbyqHGzOxU4xnlifozytTh5vzMHIw1W59riphHoyAfo9KsP91UDs/N/Oxw
Hc8yBwZpOeHqDEN9pL28Ydv2+11zzYjGlbezfcNs94D1GcysGXMqKhMKcP70E4rGNVU0FNXS9eRY
sNtHrfTBJvnBWFv5aNT5FEqh9DHFfsRp2DbMQFx32H76cKOBXE445tecmj0Uic+flIrsenQeChMj
LsQzctsugkb4VoBq3LmRX277FFeQ2nZ4Q10vMIB2LS+D2MbGR+Rz3j5asNMz6ib5EYcCfMfs+6tu
kMVXBC7LQNpmVjuwkwErODsioxUYkLltkBThgE8VIF4gkwrhDuzaFWMn07cMZ9Bbhm0r1gMOYssv
2BaWHwxSGyPD2XZWdiUOD0pztDciuLAtqJ8t5Hrodq32rjgJji8bxP775xv1mQNzmI40Si260qjv
h1N4brpO2VNDD0FH2gdWJZAtOrGTGyEMRfdOGAqfLWiobbCEgYP+Mb7gf8+llxhLbJfaAZxcQuaZ
lSG5LExRjgbSszZIWKi4T5zYiG3X9NTC9NS++g5sPB0pa/9m5vbVn47tn90+bvDwkVgMWrcAEswj
rCINU5hHYOqtzIxoMHNSSrjipNxkTI+ihkm+3i/o/L5ltbFfLE1DLuy4QeqjDSogGtFoub1+1wqs
XZzhFPfhcEYFgaKaYm+E66GgAK9gOIhqeHAGNGfkZebGT+rCDDqm3QHEyfAMCSumRRKTY1I3bWzo
qtTYMbv9VXMZW4nq1GPTIA0PPP7TJw7Ozh36WWOhefjJp55+5r/33nvf/Q88+NCPVekIkiWoe9TK
s67tyO0rQh2kAcuEAQTZLUtj0OjYOjUiw/Q+XU8dK9b0XnlLiRPCg911MOwouSERrs+LmaUEbtWl
Ag4QSYKMfF4RxrLcZYMScy5fHl7+ePOj5zeuf0qydZJR4Z878h1Ua6l/fu6kdkLeiDPfDM+9/t3N
d7B1kNayo5JwNLQ7ESeWk1kTYNCH5359+8o3G398dfj6l8OLn//j1MWNy5c2zr0Iz4dnryr/ePEN
hUBCHv/j1Hvr175Yv3V59ObfyLT/PPWr1ClIu9GZc8NXPoABwZK/fevC+rU/DM+8N/zsVdIfIfel
Pwxf+Xz48Z9Hb50hw5UL7PFYnjZqTzKDsKzEUj55q64c2m66krIw5qJ7tKyQ/yd1VU48mopHUtOy
SrX4lull2BqyBb95W9XzMJEvL4lhU0xeIl7HhkSZWDLA9RaoqBhuTVM3/vQmIpnLH0+qCEr2ba/w
7V4AWvBhjbNpiY2jZhnZl8jQibykCP9pyMcbQLqSXuUCsivTaTSG91Z+MsqcuPJIuuAcSncM5TuF
chxCnCM2GQjQoyhRWhpHlaRzxfxH/4VE+bKFEg1YF6KowGB9DwyZVmD0XRiH/ujHHVAs3kN+rgCi
V902io2oh+YXmqosbZRzo4WBP+rsIGe80fK9joHTQhHJckhg6VPQg80IoPfyulHuC+cD9sGKDRuN
fkeJ/hVEAiZgo22vwPAa61MOW8dYBqfLa/dNgMXPMhRhITV189JfN9/7aPjNX27feml08QMkVK98
c/vWm8N33wcZu/nS66PzV0e/fX3924sgS1kUHm+H2eE2MNwNs902WFq8sWqZbWQdgdbZBzXUqqJs
Prwz8C8fcCM/V2j7RXUaZf84wZ4FOtKeQ27Xbq2pKEQfUy1hUnPQDfb4XkvZ5Vvdzq6fKHZvhfuO
E9GqP4lJX9UP1rqW0I2gRXjU8YDf9yBD1Q9ckPm7HCCpXaokeSa5iKf3sGWgkOAeEur08RpUx/Ud
u9NRM7vvx5ML/UB3eSarU8PqWKD8eDzCVB8N43r2iu1k9Z02gU8xzJ7bZWDuQeu2suGcJcloexog
XfcsrAFj90j3STUeVGXdWW5OyAqamEiOn2UxnJAkjR9xqathZz5ze+B1fbNjhdnbnPNyURgagY4/
8TFO8oCKL6ZgIgVAE+PYscTVKAGM76PG0sDCpCW+EWE15i6KoYI+FtFAH1a51Ch+QJrgJI1zc5F3
5qZPj7/v5k0nYr9hCB9Vpub2cd7IGmgI0RkZGqH03MyJxyNph4LWaszBDNvzoxgy4i30anyDK62u
ZXqavlXMJJrjIB2Ap5cy9g8TS9cFtsNi0gbzQDtqW8eqUa5SpVIhlIKpKPmYHn+4tAr31UNJi571
YcTdprfiV0kt1e7dR46FX5OC1k7SrjT0jOunNBVOguELZ4hmj44qGNoBPo+HfxhnkworjVZLaSpe
uarrcbSiZRCoI4DpwUJb0KVRDGKV9weCQbr9MSzyCMYtvk+scsy/qDJbQ12Szr158dTtT0+HBsHo
vec2L5xDQLSRNe4VhcF22tbxHdtZYlZvb39VVZ0agE7n2b+0FNMhI4OKCfonyBFzxQSBixUWxV0O
4AuqdkIVUmEaPQYCmLV1hFQ//Ydo7hjRiNnPQlS4eNIB2imwhJg4TnM8qqLSJ3P+CQ2yT7PwCY6v
xvNgFPxzPB4KA8VBEu1GjW5CW10q0w1JHHmcqZnmAuURIqWkApH8tNMy/8TkSRW7rwiVgF2BXDtn
3yZ2BTEntk/F6aknTL8E+OTCaHsZEIkUCxJ663aXzdYRP8oUJ7YREW9gqfXsAJa7uCT2Q+4R1hcp
hOE4SRgS64zPrSU9WtTIn1+QlGJFXITrxCsW8wRoKsmFAdtv453nUT7vxauj8y+t3/h6dO0FxP8f
//n2V5+kbRjV7f91uV/SUya7JiTD4UBrUIy+54L959N8S5Lm8ixyOax03WWzS50NouafUO5PiFJA
MAeq8bOhHGscGSdVzmaKWp1kS0WxNvjRoDXOWqx403YCAtiqO/D8clgKXVN6Zl/DafS4R8Xvd23Q
rKvszKCLwN1YkTTtHGFZjGdiWHwtipxjMMphkDyCCifgwxeKNNwDnZfxxURDUTeW05Y1YxMIkC+W
Ig/3OAXjqUXjGBi8GlIqjhouMVz0Tc8nQbb4DoSGYrRegbkpuOzWBZSv20cfyDggM+96Zs9dvT13
gelXwePrFdt3kXfJDLQiicjyPGcBbpTZTMHG6g11sVTFbG2+XkaSxi1bVTjwlgClYISV6TaIVYvW
UvPg4h+qpMyB9ynQYXB2NA5EwraqUdX1YhV3DPcQB2ploVs8dJmmY+JNjpGy1AcAxqKfbV1zugrV
U7zKblAJKnw4IWxCvPF8Wk0UAv7pPGhNxDwfoFjwoII1Gq9CTfCSqO54FZY+wLQeryKkCkSPaSpH
TZL0Md/YV28ojz1DWlFGLUnVnigIziFRdAvAuaaJDBya3JgfsafAwVgNd2x5YHfbRPYgccYjpyrd
mZjclu+b5WPaT20GyKC1xBgu7rIH2xE2qCoIlCjHApqlCc+0hBBQEUMUI7VR+InJPy62qSe0JFhT
xbcCylNaOKcwlrABpFN4ZwBWigW4O3Y3YP4wUpeFkcR9ZH8x3Dqk1IPJOvgq7jnHHJpAkI8AQc43
FE1KpWEWFrTS+WCOBhNIhGZZyXoO4HawAFbverx616zKcpV02bnHOJWwCU+B1RjZ4MGr4dpFSYUj
FeJmw3DIMVkDZYClTakJCamCvj08e3709RmVHZAoh6omOU7445CfaxeP0l1LJxXxV4Zb+IVDbHgo
KQRHfFEwD9rl10ZnzqloeRSyhzEJWF1Q2NXh318gOdlqMkoU1ulmaGu4kabeQ6wE0G1wjMevLaoH
6k1kbeBIz1JkyeOGmtRLFosT1eRxojAhuBY7jZJBH9YURTMfmtClycIKrdRJdGYNSLgIDqq99z+w
lIAk83Ap4MLlVpPtyBUMVxwPjaeoy0xU3tNhguImuSuJWsLCMyTdwmB20jzIslgFBz9zmCNxHRnc
6e2TXudamnGezFXhohrJoDixkokRTJIZ0L76gxayMSQZQsW9cPzwLN9jdOY8yYzYfPPC7StXpG6b
cAbhVixqb1dWg15XCPxxjAafVIGh4IGEo9J2iQILomHz1KnhS9cjz4DtdFwZhNkuAQE82v2/RAc8
Z8IB9jRZMJLexMQOFcaPyH9A+BH9EAvdMvON9uENAHFAXHmLPlQCt22uSdT3+HCGu/ysVITzjWSG
QeQmIWkPNc4IIrUHfAyD102ocpVwzaE/hWULmZQ61AQvWqgpRhkYZQXYMaPOJa4UEmtLhNntEY0Q
pq2mKU64vFCu2TMXHI9WPZFkC/xP9UssErJ0Tn4D5BwmWvmYIgm7laWFhgiiWpw0YnnjnnWUtNNS
6/hCotoTXemnAS36tUlpfmKi3yOEgMEkTjqd0FEe7yDuaywLHTlYigJ89xYBfpgCbB7fPsCYbWs8
Byczsf0a/jt5OwdJ9K5x1+iV40WBggyjylieNpMm42h3iZSTu963KCcyc5x2VijI5cD4qltk8HI+
j6TmRZvFD9mY2SGR9vwYXGOqwaWVlsjShThDTzxyhIITWc/QDkz248pL8nXXqDHOxds7oadWnuQP
xrWWjkaqUvLHwe3QCPdPTHCCNqJ8LGlxlbwGlrRvrnBeGoEVRGWEtk2LbhUV5iH5092XifVciREj
1hrzmsgbcWRWEwgUeyXyqVI+bER+teijvCmjtxr7kDdiiiCUjenXwus/5Q0jIq1FH8spMZOQBGvc
Z3ljTGc1/LcseTYhVGSBMZzYQuSN9LagWP/cVOQMkdoubL0JfjhZYnCGEUd0rbRLC0T+ICyIAtib
p14evfr74dkrYJxsvPN8mAevJsfnyXeL84y++N+NS5dhHpJmP/rdJ6OLHySn4qQvvZ6Lv+MWXwPU
Fn4O6XDbAH29+buvkgBlRT2UR7LCHQUBIgXxZHp6W8+Fd9B1BvgumRCsREEYUZ3zgjxRfEeX6rA5
uRDoD83zJRkZ6SU2j9VRtZfRqC/UG0/i+5mMpxozQpIARSk/YBJJ/K8xSKRh12L5CePlKaTlKxTO
WxgjfyEvQSE1USGUGvzexa2m/ASGXAHDoe3fQyKmZ2Dge8C4paTKMu7KC+ltz6VED3yX66rbRUm4
KNe2TIu01EfVqMgK862kmIreYF0AtZ0ckpxMBKKIjZNBnYlgU8yJn1J8gWk3DJqgYuITPA5OyivJ
aKXKmBSeUqS5m+BzfNJgCC+48eyG8ZLMJPbd7UgSoRomvShfon6WJaX1kirWTBWRV+nKSliVymls
vHZWJtqXvMpGSVRNxf/Xx9z0XASkNuBYfFHFhtJSemMBY6mtxsJkHmCcoE5tmqc1xzTn9EYpurLE
lZJXidcznbUdoe4MkUTYXahALyu51ezSoZIEOSYFLqZDSPi+AiZq4LnHtg6jNPqcjp2lMUsn5Qly
NM9DfrJVZfKyQGJYweQwkrqIk/KElCv+Ite4Bl4kRV6a9LjxytejU6e3lPQ4fgpvppIfGlcZIBGz
RL5XmuQ0KofX+M04gbWC6rLimTp3bAtTTKtPwFwZXf4rKl/76I/ErqIrJLbmC1+v3zhP2sjXmZKb
eGfWEOUpxjOsiUWIg4Hf3TydHg/MiQWSt/UI3h8+W5a9DsSQej9jE8WDXqErlIgef3FiiRwpzCHg
E1/6pKQ2Lpw4w2EqC68VC5ZJHajRcKIDdeLHVeG26TwXQDXdV0vH4t5GgXNpDHQs4/3At+w5Gjda
hUQdOZO+jO8JDp0KurJH4YIa3JAwWNhqMTFVFAuTuIQjXHAuYW4IARsF/R3cNNxIY0a8MpyjqU5R
5gyNE3P6TTc1gfzS7nVKdW/muDULuTNz3Zic+zK5a5wDHrPFYnVy78RS6v1PkhF4r3vqEMTVmexM
XO202/0TfDc+ZoXqrzX1HpKedM/DKNUYdgF25pGcSDzpwbuWNNIxyuEsELvCxkR45Ve6h2mLd1oW
cODIlFJJuk3pDvlspGqlJqCFrzLhHuZ5a3LzeYrcX5t1MuZl8eTXnuy0KjaeGsYnfdbGCofyFjhP
BwAdZYKyXngvhNzTbW6JoJmsX3t9+MXbw4ufq4XiYnGc9daEbIs49uLZjlGq1o/4XC2cNZZftDb+
GtGV1u89R/ImR1/Qa3m2ttJkjVvRNMydTMfMTsuUG3J8TqaYkHvH8B5eOsHFRPBbzEgG69b4U0Jr
Y1/iK3AluhYwynqP7iyMsWtJ5lSQOJLHuB2GXk8n968yX6p4SWCm5Nipi4MLl2Ql95tsbcRhRdP8
0nZVSJbpreUoGrFRZMl/eUUaGUWluFhjen7qYH1huq55vQrxZ9LiCsFtqitTC5E3VeoMSqn6yKv8
GKv6A/vH6/ubpAex4bwevjywx/pQ7VpSJBuOl9BGuEoQwbe/DzBTFspD8KOc8tiYIkd1mGRFrJgU
lFpaIEkIXIxnEoalJbGKRTBjEnc/8D8uqmIBAEkRzCgQQJ1wJUCqPhG+uQQ1zU/ZjZE4tayEAWuS
HEWBj4h4p6wkXr7AXfPTk+bOp3IwGyePiXG7e8R2+ZDk8TUzUVlpocY7FZJ5kA/qck+DaI6LgyRy
E++d0LP8FYVcIVFT3iHCL0aw+wuNyRpKRkQW/I6JQlGCoe9hvYBM4G1Fcv4wxaIg8x6rN5+q1+eo
ZXcn5WREK+WQGPKkZFwQZtTYFRKshSvgEvVsYwrOkjyqhttigZqi+hRySyVFitRDlSZTZf4h4oeK
dihhH+AG7EN2UjARkZiSC8lGckO1/FqSHa8KkBYYhYQe3lhVLK8/7ZTj1sVOOPylhv+WOKU4lMHn
tog28f6ZGPLQfXbRNV8J/G2z/GubpV/C2yPyoeCbxyHhbzXh06ij3qHhpxNH/4TsMhkMCb2nMFwc
/c5Pn3rJzaU/Dl+8gAoF3/pq/dal0ekr4180w1EH72pIuPQKVMtJPHo7cA9saSuR43T3Hhk95UJY
7iLYFGjS7FNqBMpjftXMojNk6WX7i8bdPGGG0Tc3hq98mFU7V3T4TEEBnQPixx5wTuwCMgN1JEJj
sAUn9jZFyg7KBMc6ZuRKKL4ReS+0cKeCIK+2JWQEp3hqZ9yksIjKFUkRSb/x2ujtv298fB0+k1eT
rl+7vvH76ztG4czKjd4DlnOZfuotjMgDNBDcP2l3RsXmzLqAjCAi3xE87rJJlD1zoeJV/WnFyvg2
30dld2tR88Nup9yi9X3LMvx6ZRp+l/k2hIvhbFzCFHZhvtiQ1KXA03fnnr6I3ohK3lLH3Sy/k3sn
UhDvHSZF3pKfCYeS37V4ADISEZRDwx5pd1nQiyhwJ79g9GV6/vBcU9utSziIl1DJpOHUUMzixFLc
nS4C9nCNfwtyXPF56cvh1d+Qe/bWb7238daF9WunEO2do7US0RXuWwpLyE490uGoe8Ri749H2EMv
AI5tCiffhR/Q7WXpuw/MyL4V6cWIgnaL7bggxjl4qsWd6RJNjnr75UFZ5PbnhUw5frA+WuaJhX5j
lPJoWpES/1o43CsZ2Y0/uVsS6+XFfUFNUQpQpkdGVBjlrxOhGEj/FWNE/nOKxspvcEpRGj3/ReIl
J768w0CWvavnxMOo7JWw7ff5MpJkkA2XN/bcoyhti7wUIIJHdu99bkVLyrIqA6drO7KrBTnzYD80
nHOD/e7AaWdcNMiovySJ7XPXEKVenMi84Ck5kynYyEheRdTy7yY+/h/LijvM8SmaVmzC6g5elkrr
jBPvM8CbQm5sAc2TvPKGJK/Qdwqfv0rvc+FzWaSvlUo7uBn5K/GrWvnZ0oeX3TcTVU2PmbeaSKcp
kqstmiTYS4QSfd79CiC+g/6AtoXeubIljwDpmucTiKhQahdkqPobn54GbKA3H33z5x1T7+UuCpl9
WtwkTbNEt2aBxtIHtmjDpNguEvX0P5bHNi2PwmVs8QM39bBFBy2PqYliKvVkqdARKbmsW0zpSRFK
II4IS6rUSEbFO557rAVqUkCFbz49jy+1YlF0FLosFj7Hr8X8/q6JkRbspt0Ao2cHiSi1+7/oltnn
vumZGMKsayoL3x6Fq/r4EfAu4hBlFst3MvKBRTHABw9LxWpmk/ew4scnImScLKjr8Vdf7Rbwl5EF
zAud7CAdRmiYhhIl9aeetfjXLQTpUD8JBRd0g8vc39yLsHOuCYpaojEmEq9ZSHUvh2VaIHI3Pru6
E45lyvTbi3vtyDvzkl12LtQUQ9y2nbQJrMXn2XbMKZwhk+6jmJO3hZgT5gFvzJjTv5hDdiKs8y/l
r+Q7MzOjGMnLKXDGbmq8Jntd2w7IyJazlaXIgzFMBnjC+9ELr+5OMXfqAlkCOF4YUi0jb41A63F/
TVYyeEIMcn0JZmTqZC6I8Rz1ZK4cBxinPsihSwMjQW87YG0Xk4S8te1tydrekjTctjaZc2UN4yFM
WFsSCMJlh6mXymQKivHt5gQLEUsmfZ2dlNTQgprvzmi9SfMNoTVdzU1TcfENLgMnSMla+iTSEi6+
PLr4B3LPeVjV8t3N12hhy5kPNy98/P1JsZ0SEcK9JqmiQmbTizUs6YSeLnCQ8CdoS7ekCxH5NoVS
RllrAYm0MyWu25ZNuW6XTorfpVBFVPaL1HaAd0lVUwb7xjITeFKp/kDqroS6yuh1DUl6TQao4l3J
RX7/xFWutHrxzHmBBZL1c7m1B/GLZ2W31uaUGog30MoGCC9dwKMgZytrWc2UiIm9Ykwsv2dHUnOS
dSVpZo52gVxt+YbrW66Nib0EA196YqwCtrr5ry7DjWlb9Nbv6JL+ZbPNXtqt4VbRSyu5N7VE73Hi
II9fEZJz/62Kh0+7+Tawg65VU8m7xUd/uQRHJ30/SLItDS/VgKjMIPAI3OgYjd5kjkjs9pVvRn/+
1fDFF4aX/wZ8QC7cxK8tL2fd6I1eii5NuI8h8b4IiWDwwdYNnLZmfI84ZDgj72znOZ0hSA1f3r7x
5Y2NGx9sfnoeGm/c+GzjxhfRW9yzkXGfgIwSIAJdwulZKFMDvqD3CpTQLUlEkzDwDduG0TOBhA16
y/YxO1hV8Dvi+32DvkWPt/VQpD462rDjeuBoq3De1tTJvQ9WJuC/SVhWH2ym2kMTD+GX1i8PVmr7
TZCKeun/AFBLAwQUAAAICAAAACEApr5mGGcEAAAQDAAAFwAAAF/nqIvluo/mlofku7YvYmFja3Vw
LnB5tVZbb9xEGH33rxgsrWSjjUnVt0iLFFKXS2kSbTYPqKpGjnecmPV6lvE4F0WRIgQiRI3IS6Cq
QL2QAg9lC7QCRBL1x5B1wlP/At94fN31JhUqfkjWM9/1fGfO2GG0izB2Qh4ygjFyuz3KOLJ8n3KL
u9QPFCVZCz7xXE6uKo5waVuccLdLUof0Xe72LL7iuUvp5jy8Kooy35z7wJxp4WvvN1EjXtQgs+tB
Xt1gJKDeKtF0o2cx4nNlYW6xOWMKw4LbW0iFTJYqfoAHYatxkUZ7SVXemZ65sTifRi87LVl2J+wF
qnLDNOfxzNzibAuMrk5CVW3ioK7l+lpAQ2aTqbgw2JT560i64rbLsq08k44m3kauz6cUBI/rIIAN
yUAGWXcDHmi63BNPj4Gppp6/uBv99jD69suzZ0dnR/ejg1+ivf7L4zvnT/8YfL4z2H862P3p9M/t
6MnD0+N75/3+oH/4z6PPzv76Qdr/vf2pqmcxGYHJ+eiKEq/kxRrdDvzVJJhBo8VCUkdxSZh24lcZ
g1tsmXBoKncFwJwiungzna7h0zVoqPbRRK07UWvj2nu1m7UFXHO24gnE8YiYucU2IKSMbay5fAUH
oeO465oKhmLCPGlBYoXbS2A+S31SqGl4kW3kSBbdEl4aNvV9YvNkjnUkCqYhb1yZzNEqRh72yyrX
R9MYEh0t868ICQMndsiJps43p9+9OY3WLA/bK8Tu9KiYe6u5ODsz3TL1wvQ+hgQ+mHVpm2SAVcUq
GjaumR+aLVPVDYdwewUA0vRbk7ezoEDDornh0TXCNB290YDTQzzCiZojGXPIcgOCmqEvIDMZo0xT
o28eRM8Oot3dweEXp0cn5ydPor1H0c7+YO8g+hpWfo8eHA+OvyoxMQg9fmEXAANZZi7fkLhc1EES
TdRMO5fX66iyzkH/TnTwPNr+Mfp+O7r/eHD46/nzxy+P723KeFtq1eBsjwZQQSVLMv6V+TDsUsnj
0nkAhet5lk0SDklXsm6THkdm/A+OGrICRERDw6KR9Zc1FJttVSmBeHFcGL9XODEAad6WG8RCJQot
IzseE/DPexzrPx4ghzLkEYfTVcKAB0gr501RqpeW4zvCUTez7a0JOFSqfqlVsNIdaxW3eHGgxGQo
ij6EVVGQ0idt0Qh9z/U7BQAK474Od94s5ddp6LfN8rSzqVtBUJT0QAgW3KWkrRU0ftmjS1pJqt8U
QqzXgQxQREAKQi8GIF0F/EnUW1P5hXg7ryJNMcrm4Z6LlsOCKvf0avv/opYXB6iUyJIvcHi8+yXa
VK1E4oF2ueuHOUjJlFM4KiY84jJyXIfLHX/kyqBUHTt59caHTo1pX0eq5PYrEDo9EzLF1qYMBrrz
+hguaqReG4+yMyfnVIGdue1oDa+t3zzJ/9LzkKrDrbWzL1Q9+WTyrS5JtT01PX3x3eDnu6cne2cn
fWGa60BqmdwA4qsWuIOxiALf9A1gLsbiGxfjhL/yBl3YCEAzzXWXa/EXsK4r/wJQSwMEFAAACAgA
AAAhAGAtIhMZBwAA+xEAAB4AAABf56iL5bqP5paH5Lu2L21pZ3JhdGVfY2hlY2sucHmlWF9vG0UQ
f/enWE6yeiecaypekMEgk5qkqIkjO22FQrS6+NbOkfPd9XbPiRVFalGrplKDQE0KrUpLoaU8QFLU
CkpSyJeJ7eSJr8Ds3q195z8pFX7J3e3s7MxvZn4zm6rv1hHG1YAFPsEYWXXP9RkyHMdlBrNch6ZS
8ptf8wyfEvnuUvlEL9sWI+90X5s0VeV6TYMRZtWJ1Crfw1XPYEu2tSgXZ+E1XGBNz3Jq8nvR42YY
dgaVyeWAOBWSSqVmS8VPChNz+Oy5EsqJrSo4Ydnggqb7hLp2g6iaDvYSh6XOFyelZGzfaaTYbo0q
qQuzk6X82QLmYoWZiyCmTBcKc+dmJnGpWJzGsXUFzjZJFWHXIw4mqxZlYCoGx4xFgxKV+5QV9mho
7AMJjD7hOg6pcD+yKQQ/q4oAX4GAblFhuKqFS/znGxYlqBQ4HKyC77u+WlXa28/amzutvdvtOzcO
938/fLnZ+vXb1v2f/3l1b40rWlc0oSDwLfBAqO4BYVAM31UNvY2UD+uuSXL+iiLEzUWQlnZWQjtV
kM1wRbk5PyAZxM1wA5Y7M55BFqgUiYFt0iB2bsZ1iBZp0skqqQSMqMpsKT85nUeLAW3iaDccc2Yc
fspI6arrE6vm4GXSpCBdnIlEfQLJ6cAOCb5PDBPTyhKpG7hBfArWqOZidgjcIgqWw0Jsmd+Mgeyu
wCExM7or/KeUC+chUVDDsAOCPi4Vp5HhebhOmIEuTRVKBQRWwv5TSTNOKV0tml4lrLIE8KihG2S1
QrxusegirsigiPCHE4Pfi33n1V7r2ded/dvtB9c6Nzfa9385PHjUvrqraEiUjtAlU4x7aFHEI/S/
1Q/iF3kMGAC+Kpw1P76QcFSda3rhCRl0kcMonrU3djlmTfub79vbG0Odlea8j84gwJUyX40+aeit
nHiPbNTh2fJeU28nnx9Py+gUmZuQDpXlYYSQAVw8yErSn7rZLsXNA5ILAKioKZ65vdCJMj2JdYZE
SJjiuaA0meiy3lYMG/dk1LnShZmJ/FxBUwZSN8I4phDyijMYNxCahRlbAog54OPZRD2dCDG6lD+P
Ilrb3wROO/zroLMFzHaLA/58u7Vzq73xVfvHK+2HTzpf/An496xyGKn5FmtiiC4d7mdPRlgp3TNs
O+leny5wYl5V3GUloy0knTGBBSybn6a8hxT9c3BbjSUYJ7Kw+Jw+ndrrMInRPHd6+0X7ytPQ79bj
345ePOFkH52+HochRp1YlMUIKOJyJ4MxqPG1AU2sC6ti3ohehdZs4qiDqrV11P7uUevxneOtneOt
u0e7u0pCWTzg1VF1FM/JpK1GhQWQ633iuVGNJBklOHD4fsiPURU9gMR/QUu0naG0w3v9rT+Pr29C
+JWh+6pKa2/r8OUeWhth0joUU2vnwfHd60JqqEfrg7pDKKoWkJMdYxbIrIrtUk4PsiUHDq5bNV+M
BqogLxZ4NpkPqY8TW7jdpXplybR8NTaKRadwTocmKwe/8sRUYTqPLxZK5XPFmQxf4ooshvkkwHes
WGyJf9Z5a4bZhZFVFuf1SDiW2JIy5dDIN8PGqlWbV87m5/If5csFJWpj/VQuH0YSeS5pcXJ46W5O
Ckn8VoAjCK5CYfM5HOZStU4oNWoky1tXXzMILQZBE/wAQInTsHzX0WuEqX3DrCb7Y29PDx8GIz1h
EoueRNTGbRqro2iG1uvLPHjhYE2j+VB0JOwui1dtUL2cv09DngYeJIlJomQheE1eCnTHXYHgpT9N
19MmTk+lp9NlnK6u6wCGMtjcQuXRhP8mVomkiXbzfqoqhgLCTsU1oanmlIBVx95VxJiyBJ3N7uOS
8Jsu4gV0PWj+WLo+ljZReiqbns6my8Bs3cG9j8ijCK9/5iiJwalYLiTnI8+gVCaKuIL5YYXJG5me
92tBHVyfFYvhxlAQ0B8hpZqEVmAQ4gWbUw5f3Tva2WntPD7+4Vpn76fO8/3O/sMee2/e6OxFjSiy
lV8iqLhocGW6YZq4HnBSsYHUVyt2QK0GwTXfDTzVh4ubBXkVi4TYLnYZkVWqMjbm+STsShnEJ+2G
4ed6jHj0x27r72uwBPdDkpvtzjzDVUX5BeJGJXSRMug7mIEJyQEu9CDCt25ALwc9jdhUJu+d81CJ
C4n5rHuzkCdzRLoh0sUDt4oKlUNGNN5c5FZdej+6kfVT0uDeZO/yfD7YxdowRBeieHzl3tHBDZ6C
gwrkNXKQAeL8mUFDmmmyBQwx5KQB4ejgaufp/uHLL6MqESOfGHeiI6GDbcSbIlob6HA3+6eGbvsN
Iz0er7KC+MNtH7yQRJWJwnsDCe8tMNSJJx3jig0ViTE8OUadYNzdOJrHe8YMBGUUL0Du8v8M5GiT
wp3FhMMHPDoDaQs5JO1AORhJMeZJjLES+hOOHuUmZaReWLWYKlJc01L/AlBLAwQUAAAICAAAACEA
JZgRfhoAAAAfAAAAHgAAAF/nqIvluo/mlofku7YvcmVxdWlyZW1lbnRzLnR4dHPLSSzOtrM11jPQ
sTHhKk/MLClKLS6GCwAAUEsDBBQAAAgIAAAAIQAf4bl2mgoAAPkbAAAXAAAAX+eoi+W6j+aWh+S7
ti9zZXJ2ZXIucHmtWP9P20gW/z1/hddSJXsvmISWOy7aXNVtoZdbCihl706iyHLiCVg4ts92+CKE
RPe2XZZ+gepaurT0Wrptt9puQ7vt9gspy/+yFyfhp/0X7s2M7diOw6LTIQHjmTdv3rwvn/felEy9
zIhiqWJXTCSKjFI2dNNmJE3TbclWdM1KJNw5VZ+aUrQp71MxJFk2kWV5E7o/MpE3svTiDLL9r0rB
MPViYI+14A/taRNJcuCAiqmqSkFApqmbkTkT/aOCLJ/vHCoUTH3OQmaihO/jSipMS5qsItPybpUn
d9KmhhQV/Zmu0Q2GZE8DX49uDD4TibH86F8GT4+LZ3J5JkvmOFAUbBVFHiSwdHUWcbxgSCbS7MTw
6FmPMrCvl2FBGItNJBIyKjFiUddKyhRWtV6xjYrN8UzPn5gRXUOZBAM/SgmrRLBsGdYZxQos4Z/A
WpbRDaRxuiXIaFarqGqSYefYJIO0oo61mGUrdqlngOUjfEGdXfnitf+Jr6FKdkk3y0w2C9SKdryP
bTO3zYX2R/gWoEZfI1z8CZ0iHnETmi8iw2a4U7ZtKoWKjQaxIyWZ0fNkwIdlMiRwyg4juY4UtZJr
a6E8IysmRx3Ayo6bFQR6mlcsW9RnyCeVxnVDUG2MA3K+GJ4Hgc+AJ88iU4Dj2aS/XpbmP12wkZXt
Yz5m0qm+E+6/NkVBKs5UjNN6RbOz/e3pqI7oSkg2wUL2EBhQsu2gSF4gtZfYY5xkFW2ljHiLOcap
aBapmuR+0UEGRmWIcWkKPlyD0L8euylkD8MQ2PH44GHMhPMWcyNDo93JAXM8xbmi857ZVL0oqaJi
UGtZtkmNhdcqllRQETcrqRWUwUuEpKDr6iFu6sIbmM2HOkExRHdImXW421/xLHGwMDMTAcBqzJCk
WigRmcRycHFHC+AEFmAwDqsTYQJN9okUSzRMZVayUQcJgHiQTNV1A/vIb9Ip2gxVZ7dDyQ5FC2tG
Q/acbs5wbPqPA0J6QEgJqd50fyAkwVTk2hD5SKSpAbRLBwL9x7lfp4bE3MjgeNJbPT96+jPxzNn8
qXOUXchaQYYCRK+GijYHYqSEvv5+7xcwLM3zkT2KAQKEtoOz4RF2ZY6fSE36GxTfjbytfKyJvdVE
wCtczGnTE7jBg5KiSara9S6qboEYVG1F0L8ig5khwiBqJsCNJ7H6QGn0WkW9XAYa7LETk0eGZ2+X
IBkA/DI3wSoGRUB2kvJFahwfWTKB1eGMSlFGFjqMflq3bKx4MBXbk8O7qI500yPGPuftOyR0wSMr
KvEtv+IQzIoWDrKADMnOBcmw22maYnsn0TQqzmRJTHcuYoyE3dnj4aVwVqPssQ8Sib20KEOGkxHn
4TVDaiAryypTmm6iSGY8knG8H4JaFjlQAN+Twfk4k71QUCB4L1i/47iTmQvyYjp5fOmCwC/CX/rB
gxBU1vDZ8a4RdbGOwycmO9awkQF3ELYwPUmwDFWx8ZzF8Z3M3KuzubHZEyzehSnjydpnC2jexr7W
lYx6T0A1XRQC+sDn8V0ZRfVkoaMagzv5yUcTF2Rhku9yNnfSXQ8YJRHVJeGN1UIP6TxcCafFLhpu
gw5OvV2SHufiWzIYb+f9Yfd6y5UjgGw+EU7bpqTNRJO2XTFUNKFodhIuZ09moqFAyCGOJNO25hSo
2SEb9Qnp3w8IbMwVpwBucQpIdXp1DKPUoTzSR7C4R9sXlzk4skruxR1ScvABa7s7LehakMy19Zhk
ZtBCFuuP5LDOlOlBLc2A7dyH5wsLJPuFJ2lC5KMnB/m0PSowyxNNMGxj64fG1m5ujD00L7pcA9Ru
cQc5QoTKBEBcwwU5btMyWFGRQi5cFtAuEe4XbhuFPP0fRoESO23bRqa3N933B1y6COnMIj5mqRfq
UMDSMIxPQ68KxVl2kf0civWeU1PQArAZhj2HEC7w87peHpYqGmQIszctpNilZKITGbBbRWWDT9KA
ud9JP4+khX6ekSycKQxoy1F88eEuCq582Hoc+/ceV6weLFfP+QXLRmWWJ1idDhmkHcnB7lv4PD8c
DeJQQesaCQsuup04BwxI1CYhlFVpIcOUVF3CxkgLfZ1dL/SbSJtVTF2jIp8bHBzPjZwV86Oj58TR
scER8dP86N/OD+ZB7I+I2FFRPE7kNOYTiOk2RfuBQCDKBdn4uO1Y17hP898ihHE8wxGegPhSuSBL
mVh2fJuBIEuojKt2BpcNgXkCJpzXsJQlRaO9CvgxlTXmfSA677ekZAE7qGhDUsN9+2EqHBvNj+Oy
aiA1kGJjSmjy7pEl0OPz5BO/0di4mzDPRLDelMyp2Yl0ZhI72ATb00PqJHayw3lSeENcaLugkU64
fSBu7EDJcFbXKGU9AWL5BWQ28R35yDdb/3CnVa061UcHD79s7j5pvqo1a/cxCt177Lx46Ww9be2v
t7av/vrhauP5t/BZf7db33vQ+Ppfzoflxk9rre9WnM2n/1n+IlCbhcPBv4QbD9lUB5amUzFATd6n
5iTFJm2X+0BVBPfEXQJ5JgiTwu39RzyDJBPFFuVCO2XQTaDKEBOOEGPczrIpotgUuAtmk8V/km5I
WNmBkFcMkn+4RQVkQmHv8Np35NFwAOvXnNXtZu2GU73jPHrZev2YjVqixDrrO87qU7r864c7zWc7
ztq3DLUy46zttP6557z9sfXwmXPpTb220Xx6xdldc649aN6MGMA1rfPki8a/t5y1J/V3y/V338db
mhjVt3Fr523z7uvG9ceHGtgTuLG63Njaqe9vNy7ugMCLRA9LbKd9Y8xLUgDoHjdBOMpx3EWrXdeE
3JE17z4fed3jIQZxb3/rReNa1Vm553x3xbm60cUyHYahe28/aLy65azcdWq7jZWN1k7NWdugDBsb
X9Vrb2JtAgpurK42br0Gg1Ay59H7xs2fnZUXEHTO1gvn7i74SPPuN2AN5/2b5na1VX3kU4IX1GuP
ncubzqXH/xebqJIWBZjFwIPSUghkPD0qWknvghxUVRgtSCGDL3RvOXvMwrd5uezcv9/cu+FP4l6i
jQ6uKC6+B9DKVV2WZT5m+gdCcwwTL8XbH5u1NSoL241hCXZTKamzY2354iyxUVJn/Wp990qrun9w
u0pIqbhLMfwBYGQoixRghZ/NvUd07O00n02wuZHceO7UsHg6P3hmcIQMh3LDg96DAe4NwkwE8r4a
agh9HRw82Wj8sN3crDl7twAMGitvnfVrIKMkl6F+4w/f4Oxcbj64CNTYNYkiGKikJcb3uYOvrjbv
fPnL8lZwV+v1E2ftLd0r2PP2L8v34g5yj1i/TrEFoIbK16xuN9cv1989bz674lx7Vd+vNm6+p5PO
jW8o27Z/h1uKNvMwPTV6q/pzc6+K3W1th0JZfW8fDm1ehwD7mopNpWrz9zk6l14d3H7e2t+E6Gx+
fxvgF4QDRs7Frcbzh9S3OrbF+KVbvMTnQT4GDV3gwk81IbD7DC0UdMmUc4CPplkx7A41+A5PZQwo
LQKXR8hPNA10Q0F6EqVp3H7jrN1o3rx/cHOztbNDrQvAhnlbbdep7z5q1jabtdeN1Uf13V0wVRi0
fCyKfRGMQXOvfoJKElc8Ium1RFLPiyKuK0XRrY9NSYFCilb8g/OKzdGqk0/8F1BLAwQUAAAICAAA
ACEA8DQg/oMNAAAKNAAAHAAAAF/nqIvluo/mlofku7Yvc3RhdGljL2FwcC5jc3OlW1mP47gRfu9f
oexggO7AcnRbtoFFgAWCIIsEAfZpHymJspWWJUWS+9jB/PcUTxUlyu2ezAAzbZosFuv8qsg+9G07
Ot8eHPjjull9pe7O8w7OF7+IaJEe8RcJ/yKIk5Bmxhc+/6LIKCmp8UXMxmlZJmWpxqvmmVHfBV4Y
qrHLdaQFjCbRLko16bpqKCNb0pjmanC49iXJ2XjJ/+jtSP586ttrw+jAfmmp6Zx6Shu2Z5LEYWSM
utmJMZiXRamZ6TkrWRSEforGxFTY0S89NUwuGe1hdB9EnkeNUT09m2Q1nEnRvh4cz0m7Nydg//Sn
jDwG+40TpRsn9TaOt/XSJ70tKarrcHD8qHs7Pnx/ePiz883J2jd3qP6oGtgga/uC7dW+HR34+jxe
aqnLvK1bYO2F9I9c5pImFpP4bhqRU8q2Gd2SXKr6/eD89M8q79uhLUfnd/J3Wv20cX76N2z9N9Kc
nN9+YR//1Y6t8xtpBueXf/wqxob3YaQX91rBj/CFO9C+KhF1YB806CfyVFlbvMPBLqQ/VaAo7+hc
qsY90+p0HmGa572cjx9wDqcnQEKeumrOsOPIRrPrOLbNBoa66wjs0Jrm8P9I30bSU7aGcWRdMlwv
wNL7xmGz3FeaPVejO5LOPQNjNWPOlfuNPZyyA3oNJ/CwHaqRwgEIKEfqo2thrGrheMNY5c/vQhpj
27Hz8p//ADUV9O3ghN5SV8JO4njjTP94232i1KrsADi/gMjAsoa2rgopKeZJyACKvu3csqpHZrzg
p/2jH3RvT1wXW8E18NJo3l+rYjwfmFIe/SjyureNk5M6fwTNfHVch408SfJYcUnK9MtHlWYdch1b
MVZUQ1cTMLGypnIaAaE2LojuAjafgzBpL744kY6JRZrLNgNxF5O2pTXI0AW2gG1s6wc9vcixV8lZ
6oGNMRNwC5q3PRGKadqGHp3XM9MdaJMFmaZ97UnHdLq9EDhaQ15gX5N1yTZYVsmU29MxPx+tZxHn
YL5sEGTmhYz969G+wYxWR4qCxwBPONLcBkJtA4Z1Spl9iXZxnOxncknidbkYHB/O7Qszj1UVWLxV
5IOnGaUtycfqhd4ihc/lLmclniRK8hw2HF3p18Lo3JqWcDJud3fJlevIZ7a2agpqo4ZcqAwgWoQ7
Zlowp29r6tYko/X8aDw9WEWkEscT0i5TY4rUq1LCfr9no8jUve0uXpq64qduT+11ZJHgec4Qz75P
q4rXrKTScBEpux30VETkh21HTiDAM62ZFO4LI/NggUQBngOjcSL5KGsynJW2p/haVm+0OIrQmgZs
bi9EEXCl6jCbgmAQS1GwypE2m1NfFYaFKC6WQeE/V4jz5TvYK5gVSzDcjNyMjq+AO27aHj+fPrXP
LMCPJxNA4X1hFYIrjhAk1hAaEZ+sRicB1ZMtFGghD9c8p8PA0QffUIURsit2WXw09c+hlXUzBbqe
EO0CoAS3oRnp0st2WXC0mdaSsABomOwr6RsQoI1uTtP8+ANOqWlXTdkuCWe7vCh3xx+NiYJ0XrcD
1bQ5GMLrbLFcQxeUEqJZcPC3oQgO+bUf2KKurYTNaTdliV8I7P8xZbbEpY1yk4AzIkOxSk1BIH3H
2PfsIwwYcFf3jDPkNbl0j/42YSfZOOHLKwChLUvvIEAGcSbIuAXo7tR0hBPy+M2l4gK2DpgMYGf6
TrO+fTVR51puMYJsuoYn5tt5W19uxgPsWtSF7weIuU0BUNNlIfjGRJGDWCi1Z6aHbU56ttMtN19E
EQMkziOKdC/+6elmaAH+BHJmQkUwMOLa1jZVNVxVNzLwwt6M8GhGxz0Ljok1OJq+Mk+f2j90omQB
b25Fq0lx4UZ8M5mBJuk7Wz8eNkagkEN8ftn2F/4ZSU+nVD1Den1NRvr7o+szpD5Nd7u+YlXKZDWs
6FwNNgmGVDewlElc87RCdXeb6m5GVds7YjoMIz+OTb7lScwom2dFTH0ruY9RqUlq72UB1P0fBWa5
jc5THyQkK9e0zEJSLMndkKxOaTNSxT7dp6YALoQDLOx1YWT4ScL8xFsgxjTpZYySpAAQ0QmqiWoE
4sp69WzMNNyeM7BsQ6zKCruu1rLNbSdo5LFT8fACgFS0i1SxgljsqTguCBpKb1Iv3JzZ3XdxxkPZ
5tdBnVR9UsTE50Xmt/jXG2r2sL+h6vWEuw1gd0hcIa/g/ZDbmCgUfmaZtcEJOKvb/HmRP3cLPe4t
wF8AOEFZWcgKYY6V44W+UObp6X+vVb/MYgrqb1mgApHWHUqpbqyyuJUsOsE2jfgJ5ooR6XEEi3F5
rPxmx+I7iSfyM4WZquq6u9rjVmXYrmchyO0DWXw6XyVHJjwxIyCVa0zg1RTQYEhoFgWYLEgPkBls
H7h99FOvoKcNxBJaRmXpeF83qtMJOfbrk6rNFDVr8SX8dAKKaJGEFpbW20zm8DPAv7V2RMigW+BN
tSLQk6hkWW0xdoxqV5S6d0SIW5jFT+dVkMc5chJPt1xj2XJNkRdKbkFBzxO3MY9jSiLi0z0CmWNe
D7nX/Sna6ACbgD6yVPtp7M3FbsBqb0YjiVX8Ryt+drAT77kPB7L4f9gWAEM4NOvbelh62eRQvBJg
RnVwdOcEoi9lqVpZBEt+Zc00dK6KghUTMGkkWQ2Gm8MGC8tVC9w31dPBVPWaC3nTRqwq+pczlPQB
yxlPR7Txkgrf35ba5ECSeqghAyqtSTeAOAcKYBOko7+aSgHAh/xQICjIVLpHsdx1PG+cxVgxZRzZ
yrjtB7fbwNZdoQaDf40+iuxTqx711DiJkTsEi4R8w8i/BGmc7Utb8bTO0wHK4tHNz1Vd2ESDvl5I
yU6X3zaMPV5olbptHtpDCdm6yVYIzx0r3iFcClV0JbFUI92N2qlGm1UiM9J6/U74Doy6uQj5ZuOO
FzI8B04xat4MNgNvWpIyt6MK5HI1uz7UBhGZCC1a21lhMTV+AfevqYX0AHGmOWH1iGE7UFrGE775
NAySqbqhGtabu7btVZN3CZutXGFUFSy7tHpx2VOqBPhhr0zPJXOkvzdS0o8U1vbu3acK6iUmlZDu
7osX85CiMAK94zENv7GdFmm5o9GiTEqzfE88S2UApc7wrsT+6eaiVnhNT5TfQt0NM4NZsGToOFoF
3aYs04XByf3njvAREwoqi+Vu0WJIG2KoIz7N1Kwur7hWio8t11LaKcUoTaxRmdW/Bg26I+G+wAcx
Sg/j0oeBFl6dSIyNUOguwSh0fvHApN1eIECMqDe6bHZCedKP1tm68acop6oeUgWThEHaJhTMEt8y
dLlS8miAPb62zOSvl4Y5BpvhAm9dLVAaHz9ABdxRMj4GG3ZqwEaPHhy67J9kaLnWtcA2ioJYCBJ3
/uK4/sQQu6trmzXUJ7K/EfwShBqJHVgtYJT6yVrifjdICcRi3pRgj7qd2O5DSzh7MbNayRymu4pi
dtYQnrrP+AjFyhFWuftCi9Iro5s5FG/wKRxjW2hrUX0ps7Jg+E7U5+PVMIqPe7wYIYhy4Z77zWQl
o6zlcsGZ20GAEN77g1czkk5OGgatKAOicgiOzIRV/EgqUSQgL6FGqNVkoeQPygSvsl+b33vz1tPh
WrPnK9dmvHkdLE2LA+q5ke+kRz5sIeCM7ywQjhRbMy+3g1UM+N1cePZBrMZAgCtX+LuXMQ9P6mZz
RJJda2PRvm97zSjKBElgywT8AFMWIQXDXeAdaxEwwQ0gV1xXi0itZcK7bHLjshoVFjvykvWDUtc0
iT0YRbxsFPhSSIhZgpWyn10n65UpDpxxkPi7yAb+P/lsBrNxx1sPS+fH6OKI1xDLHg5/NcfzFH/W
5Gak/wAVTXeUooM869eIHtIUmHVinuirPqPS2t4zRL+Sve0ZOuS9bDM7G6ne4H2CHENVUNcOJdQl
62zKwqsCb9Fakm+m+FMXpmRVUX4OZaKWiR/GyncLMPqiInV7mnUEw8X7C9ZKFC9CVro5kWjm4Hbm
py4cvKX7xJbWIXPk1No63AbS7NCxDgf1xm6WM/lqn73eC4ECgLFtFIvVYuHNFndsmKMIqpYmlqS0
epd/X104u+BHz1KWW5j2NDMkbUdykXrdIPUeJrjgCM2r3bVrA1OdfhmX+1sajVksNZiKLVcN1ocR
Btf2y0WLhdGApqWH16/C5oXgZ68nlog6lID6rxewOOI8oqyx95g3yCebs2ecxtsr4VzMhCDORNKH
jIebPHQIDiJrK9fIJvysfFP1MHN2fci/Q+8npa7CoznRvDm0pD+hWC6H1damuZeR+TwZ6eWk2XPB
qUcHuQrCMuvjqm4rXyb29RCF1ddtVglLPmRlFGFOcL5YSRCQEPT8RdBHVpB4mMPbnXHVEbHbUxJh
e8JvHiFyGc8btVUrgpN07iic+c9F1dNciF8ceXmEiRSKiHLS/FLCZoDmnOmtCtufRwClIFG96PeO
fPFdZTbW0XpRrWK1mKa0CDI17sqMUK/L/WnNFFM+kJ8xezq1TULL3tZtpeGIoOOVhkqcpNmdMW9U
cKtmiiEGgJuhHvm2+qMTzyDaxhi6LQIzE09KwPKfRXWLJWGX6vqqUb9gg38vYKMWbhwEkhmv+Fmr
4VrOn6pL1/YjEb9msBqDbJe8UsHidRiCN1MM+B9QSwMEFAAACAgAAAAhAPMbK7A3AgAABggAABsA
AABf56iL5bqP5paH5Lu2L3N0YXRpYy9hcHAuanPFVcGO2jAQvfMVbqSunO7i0q7UQ1cctloOlbq9
cFxxMPGEWHVsajsgVO2/d+wkEEJaViuq+kCMM/PevOexQ/NKZ14aTWhKfo0IjqRyQJy3MvPJ3Sgu
CZNVJWjPflZgd3NQkHlj75WiCcsVd8U4U8ZBkrLc2BnPCnqAXVbeG92Ch1GvMC7EbIOg36TzoMHS
JFMy+5HckNOaeqmRzfmWHXktlGYDNL3bhz838/A8JwKrLp8E93ycGZ1LWy4GpYSwbkXh/4AMVy1L
6Y90QIjoi5E5oW+2UguzZQ1vpGChEge+XUz7iWFERLa28fkAOa+U78qP0l9lRm2EWYMeC8mVWS3+
/b5uuCU1GZkealuBnykI0y+7r6Jh3LsTKnyIOT3ZwdcabMi4+g1zhdk+GsHVJU2LffmfXOsfjfpF
8hpvIsZlfGkQQUg/bisasOW0pCbxJbb88XDVZ8Rzi41EptMpubjysAMOCXwt+i/Nm8SwsZcltHsS
kkGL86kYdJQYtHVZr64OOF1xnZghIwuuV3C2wdaI4bC6LtiGqwqYWyuJjfa532Mhq5S68hDyvlfl
EukizNNkkZJ35NOEXB+vf8D1a3I7OQWKVAgzxw+SXtFH7gu89I2xtKV4j3hpytZczEOJ9OMNSSZJ
wMPS8LfJbMPfhvCT6AEF3GcFJiL3vbV8x3JrSrr3Ga+f4JnDbsYbvNPK9fpQj1nwldWkDmCNMOzK
OOu13MCxbQsa/Brsy2r9GgI97ePRM01x/htQSwMEFAAACAgAAAAhAMfT8WjrAAAArwEAACAAAABf
56iL5bqP5paH5Lu2L3N0YXRpYy9mYXZpY29uLnN2Z3WRP2+DMBDF93yKkzMT/OdASYUzZOrC2qEb
DQY7JTYCF+fj16RpilJFPulZP92955OLcWrhcu7sKIn2vn9J0xDCJoiNG9qUU0rT2EFgMioc3EUS
ChRyjEX2K4BiUEcPwdReSxIZaGVa7X/uQ2xnURvTdZKseZYL9UHS61xfeQ21JCXbAss031YICLM5
i4qToK8M39huiRN8v5s1TfPPCYFnWuQlR2B82pVIr/o7Y51VBEY/uE/195wbSG474B10xqpj1Usy
uC9bP4SduQCRw3yYSJh4ksHySmD1mJE9y1jikzN2kV3M37BffQNQSwMEFAAACAgAAAAhAFzr6jTQ
AAAAywEAACgAAABf56iL5bqP5paH5Lu2L3RlbXBsYXRlcy9fYWRtaW5fdGFicy5odG1snZExCsJA
EEV7TzEsBLWIXiDJVWQSN7iQ7OruJk1IJ2KlFlZ2iohVsBPE4xijt3AxCBY2cep5/z9mHI4pBBEq
5RIcxozbGn1FACVDO0KfRi6pim21mpWrRbk8Ea8FZhz8QJkFLARJJwlVukf5cCwY1+C60H7nDSRV
VKaomeCqDVaOgWYpNZjZNaSVExhJGpqkDBIZDUIhO7/QLuQ58Z67aXU51EZOHxvbJCaxqUbN1P3V
+nifn//vl0LEjc/wZur+23XzKIqy2H8pOH3zRK/1AlBLAwQUAAAICAAAACEAPYmAkQMEAAA5CwAA
LwAAAF/nqIvluo/mlofku7YvdGVtcGxhdGVzL2FkbWluX3Jlc2VydmF0aW9ucy5odG1snVbbbts2
GL73UxACAmXAXGG7lvUqBiXStlCKEkQqnZEF8A5pm3VeAiRdsDZF4KHtejOja4sUSOL1XTrLTq72
CvspSpZ8SJZEgGHp538+fdxcQ/QbSTkRyHCxoPc6MmAGWtuqba4hl4XefSR9yShQLn//cXr6ejoc
TPceojoanz+7GA7T4cuc/v5senYMQqBLy1V1eCEHI1LRbEE96YcceQwL0TAi3Kb1DsXE523DsYm/
4dhRcUi71I3DB4ajzaZ7v6S7b20rcuzOV07VIdsCgm1l0lZuwVH2fe6xhFBkNDEJfN6U2BVlkDW7
FcYBCqjshKRhtKk0CtMejglq+UzSuO7i2HBqCB6bYZcyxxYR5k563kv/eDI5fDU5OgarimT7PEok
kt2INgyCJTUQxwG8C4lj2dSUDcwSIG1uopKKtrYgekurXzY1PdufvDi+mSmowJKhgrbCjJtIWZYj
/9J/9Sj2Axx3jdyKSNzAl1CNP59f9nZsS3PlevBqFVCMkJNMSSemrcybJGZNSPy6qWsSU0HjDaxq
JswvMhcvH/Wno6FtYadmW6pGTi2rZgtVmVf1U1Y3qDKjdfVa1A06o+DQh8KLQ8by44wloxdMkCxc
zygVFs2mmtWxZQy/jlOUBF7158nl4YfZ52xIZhTdtOPT0xklHe2PT58AxUr7T8ejfqlr8Djd/Vjy
vXw0ef9m9jn96WTS+65k3u+PR0f601K+WdrPBd/dkHTnaZBVyG81rTAzS1lGC48Kf4moD4gDFa7I
39PvtOg/8IxcKVukn4cPYhwZi6r0vEg/UIo+9/YXjlWX54fXWlnyMAyDphqe2woSX0QMd+8kG0Eo
uSTk3/zcOzBvq8KDjd3kSeDS+M46eCipuLl0to6KKkE5ZCKQ/quDYjOCEsAiNxcmdZaoXKLRQGb6
9/Z49Gl68MZElAmK1k0Pc48yRsmNxD++S3d/nZw8zsVNEnKab48rqpRLqxD1Br020pUH+cTcKroV
4zOzMwc/USgAf3C2zlbtSZ2f6ro0v1R+NH3SqLrjkywNKNthsH1bfhw0jOkAFtEznbOLT79NXgz0
Lkr3Dv89PzaujjdztAo2HZ8Qygu4aXoibjVleF+RSsQpqeu6KHfVfyPkvKvy/8XK6xVfh50E8zbM
ZQGDAQa0WcBRXYx5HF1pJ4fAazoym4E12IkH+g4GHXpF363u+gw0FoEB9ChsWFAErPMwAgQFlDnS
ZpewWuUWVl7noEkTJqElE66i3/4LzU+q+JZR3pYdyDyC/rwYvk1HT9V1r1ZGWKtCOQ0i2a2rsQPg
ztDe7nztTN4NJkc7ursLHUC2I4W16U5fY/bFzz+kzz+kD7er7P/0vs+ulzqIaioXrrb/AVBLAwQU
AAAICAAAACEA3LxYJvAEAADPDwAAKAAAAF/nqIvluo/mlofku7YvdGVtcGxhdGVzL2FkbWluX3Jv
b21zLmh0bWy9V91OG0cUvucpRiMhp1I3JlEvemHvVW/6FNZ4Z7BX7I+7MyYghJREbZrQgmkLcaGg
kCqkESoEJRKkwQkXfZR6bXPFK/TMzHp3sdeOSapastdz5sw538x852eXphFbEMyjHOEy4exmVbgO
RtPLU0vTqOz41hwStnAYSNqt7d7RUXj0rHv0tLv+ABkollz8/m337fPu67Pu2RNYB+b00rQZy/fA
j5CyAmeWsH0PWQ7hvIhrpMKMKiPU9irYLFB73izU+pNskZUD/w42tdtwfS1sHBfyNbNQvWUOYCrk
QVbIKwP5yIkpIdie5dQpQ7hEqGt7JUHKPNnqlHTZ96cUjEpgU2xOIfgMorVIQBG3KTNm/cA15DDS
VNrV22bn9Cxc2YuxAarbKQW5CrlMVH0KW/e5wIgoB0W8tITqgVMCjRs5DZRQWgp83819hpaXcR8C
F8SaU+5TnpVx26vVBRKLNVbEVZtS5mHkERdGJYsHsyXhz0nRPHHqTPlLpDeUiwF7DikzxyzwGvGS
ww7XV7t/wB0o6RWXAsjUdyh/MXLJgsO8iqgW8ZczGAXsm7odMIpqDrFY1XcoC4q4/f6H8Pn9y9Z2
98+N8N0muPgCeJDXzkcD6qz9HL5thI3TLChe3S2zoA+G+4Eo+QGVkmjzM+CCu8RxzM7mcXjY7J2s
hMcN+L3Y3QsfrYJNNZkNo1wXIqFENNIPoxbYLgkW+8M7QBUcgeL1smsLPMwQrZtiSV7ebkTAhMqj
CQmMdtgQGVO81grcCnzHGbxlNddXpEQQQ0kG1LSqjFOzIAL4Vs2vvyrk4SH/9kkRDfXdxMPuyknn
7r1kduegs7uv0wacfiL/ZbX9bkcP89JHXvvLwFH26eKwHGIdDg7JkIGgV08uQxxlfOQmMif0JI2j
jUHuonCjhqK3CVEj7d60KQQMIKRjrcTqkojXWpBwdqJlKiZSGULUOdIPAyzmZI6ZZzlkz0boeUmL
EHM4Qzlqc3nnNKeygFwRrr/sbrwYvSK8t6PmJTgdfxNvLWCcBfNEsrhk+XUoCxPsMM7Qiv2GxYaI
PLRoXJjG1xoLZLz3I1XLMFLR4NeYZ1CbOH4FChK1hSF3YVwhAhSo1uPe+5+GYzkTmbYWFzlpU4sw
sukYJ2OtXr++SD+6wHyOoOQVI1dXqo3GlVVuRmL4j8vQmFOcH0CZtBFQdPWNXC3EA5yIllmOz1nm
3auZ6G4QCWxiqHpQxOF3ry+ah9j8uxlfuW49JoOeLmSfUFCTI0wnmaTQZteviVB9WnnNzGL4Y+DE
da7KoOtRInwVjJop+wt9OHGmitHcwrI0DGey6WW1llHdtoLC9HJ8KTL9pcgT5bjr4I+7WNUqVplT
w6ZOm9DHXrZ+7L3cjx3An/bZWlQTm/vh2oOL7fVes3HZ+i08fdXZeaSn2m9W5ZLvD8KVF2Hjcefk
4T9378t++GMjRicHjs3JsuUHg8TUqJKgmKBXGuyP2ue74eGv7fOjzsZf142udOs0WifCOlYrxZjh
glUsoplRncXH5WLKHCbYyGysD9r3Zu3ALeLuU+DMdvhw72LrWe98q/3mINWeNy9bT4A/YeNVxJkz
yRk41O7mFrBlkiryP2TwscSgxKtAysgqzX2S6M1PWG8nIEUqBYxuQ0Y2Kapfncq2KVvSDKOwZLiL
BaHsw9KvAjHtU28CkXTgbftfUEsDBBQAAAgIAAAAIQAIFADI0gQAALsRAAAoAAAAX+eoi+W6j+aW
h+S7ti90ZW1wbGF0ZXMvYWRtaW5fdXNlcnMuaHRtbL1YW28bRRR+z68YRopcJLamkUA82PvEC7/C
Gu9M7FX2xu5sLooiNQKqUik0hZKbqNqgKi1IdSOKmpKE5oG/YnudJ/4CZ2Z2vev12tkEg6XI3jNn
zvfNnOtmfR6xVc4cGiDcJAG73ea2hdH8xtz6PGparrGEuMktBpLo8cv+/ZOocxht30Ma6p4fDDqd
Xuf55c9fR6dH0Zuz6OwpbAJbal/WhuE6AMKFrBYwg5uugwyLBEEde6TFtDYj1HRaWK9Rc1mvecki
W2NN313BuoLtbX/Xe3hcq3p6rX1HzxKqVUFQq8rd1RhBF/imY1ghZQg3CLVNp8FJM0gPOSfwEjCp
oLV8k2J9DsEnT9UgPkWBSZm26Pq2Jh5jTandXtD7J2e9B88UMaC0kFkVW5DNeNulcGg34BgRab2O
19dR6FsN0LhVUSwJpY0wYH7lQ7SxgRP8gBNjSWJnYKVx0/FCjviax+q4bVLKHIwcYsNTwwj8xQZ3
l4RomVghk3ip9JaEyNmzSJNZei3wiBNfc297Cy5WPI+AcYieBEoQFr8wssmqxZwWb9fxZx9j5LMv
Q9NnFFyjDE8G6++9j56fAlj04vgqPGoGnkXWGv8Ws/f6XvRsswjNg2tfccHLMWL6nEFb+ORTcGXI
XcO1PYtx0HPYipbqlqEyDLE2AxdLER7lIlea7mrCxQxURA+9egcnDlO58miv/+Sry/3t5GCF4M2Q
8zTA4yf1pXm+aRN/LXlcgcDHMZkgbNomx7l4V4qZmK+KWI1zKc3KybkFyWmxsbzKpKhSCAzftax8
zMq1RJESTjQpyakpVVFv9Br34a+tf/F5rQpf4mcm1mPJaEDGwsGL7wff/pbuevC2f3cz3fLDVvfP
n9RjVWBUFV4Bj6ZL18blULbg4pDIJ6hf8jsQ1QoVfMQhChfUIh3WDgY1mII7NZlCuqg5YPe2SSH9
gSGdamWonuT4tTZlE7XsxsowiivIXIy5xiGPmBUwVOnvdy7vHiiXVUrZlfmRqaY8DJD60gSkqMfL
bBRPimJAOIeIKFqRFVPs6G2/BvzJO6KjTbkuyMVZeAXHYTOS2aEZbCzQxzZNy+Gh24cCm4DFOI2V
DCOZLa7HHI2axHJb0HipyTVxJG0kUKARn+8M3j8az/VCZsrasJkLm0qEkUmngEy1ev1uKnBUO/0I
hQAcQ430VsWrqLlO5DDjpjvlFpdzLNNxCUYM5ZHMzJELiHiPYbkBK3S8XIkdg4hvEtV/6rj3zZvL
3VdY/2t36G81YpXjPdshIr3GfCG6qsOWIjizwSPHM1f7ZsN157j8zAKzSONmcwvwNljbtSjz6zj6
cT/65XRw+BJuqftuq3vR6T/+A9/kEDMZdUSPHGsL8xtyJ6PqNQTW5zeuPxP9F+xlT5hCX/WMKfxV
oxn8ftR7eHIj8uNFRNXLAOvlGsiVpUPvPdzpv72flooSs2V+nuxePOm92lPBdd2ak502J+vEXKdq
ZZ1D0Qd1ZIS+D6+vjUREHDruu7nZNSzKRD5ObFnq6l1n0fRtyMxDeAs/UJPG4GK/++7XpNju/n3+
tEwz/R8a2dRgoMRpwahbNKEkgaGOV3LsKBEImQybPI1NnNXkWD9XbFNM7gVGYcv4sA9CMVBm35iG
oZ55YYqluX+u/ANQSwMEFAAACAgAAAAhAPMUkJNHAwAAmwgAACEAAABf56iL5bqP5paH5Lu2L3Rl
bXBsYXRlcy9iYXNlLmh0bWyVVsFuEzEQvfcrzEpVWtFNxI3Dbi7c+YWV450kpl7vYnvThqpSD1B6
gvZU1AsCFQ5IBCQuqOXGr5CG/gXj9W6b3aQQcok8nnl+82Y83uBenDIzzoAMTSK6a4H9I4LKQeg9
G/qPHnvWBjTurhH8BQkYStiQKg0m9HLT9x9681uSJhB6Iw47WaqMR1gqDUh03eGxGYYxjDgDv1hs
ES654VT4mlEB4YMKyHAjoLu3TnoiZdukWJL1/V8/zn5PJtPJ+fX757OLj7Nvl7PLt+gFMnaO6/tB
x8U6HMHlNlEgQo8jDY8MFfRDb2+P5EpE/VRttLShhrPWFulzAQX1Vp+OrHdbjwatTbK/7xGrDkIk
dAAdtN7fTYS3cII2YwF6CGBWPIdmWZtp7c4o4TRTPDNEK7ZS+JMymsTQB9UNOi4eC9ZxFQt6aTwm
TFCtEbDS0xqjwoiK1fUriaCR9wnLlcLSRbkGhXuOogXGZYmpuQHfmcrQwinmo8rDbfpcyppL4UYr
p56iMl6mG5cx7JYSLS1/0KENUElvzk4olz6uPUIVp76gPVupX98vp19+/D761KBTp+QkUPA0B23a
KFGWcmlIGJKSFCpCmeEjcAqiM8r3jxQc86vTD9NXhwvMVz4/GUcKsCYjbIpU6pWZNOMcp6ujk9nZ
c8dsKafFZmhzHdE44bLqiv9KAUu9YGxjhyujd7gZbrQKaGS3YlqVu01mNnk3Oz6cHr+avv56VzIV
Vr1tOtgnjU6a62LKWJpL41MFdFnb6IzKpq+9p14Xmdaki7nOBB1Hdhcp453F0P8VvXaeSgW43r7J
/+RNiXtXuvVCiXSQ5sa3w2yZwG67auGDg+nLi8Vr10G15ibA7dLNIhxPa03915oq9/Fv6DQubyxW
P/SyVOCYmZMdQWynkAS0xqGsSUgGYKIiHOKoMm9Yp4hRA4NUcdChUTlsNqVAMMySlG7jrQoVH6fb
A5rd0iRNHHVb7BLndqovNIrtieqQuzsg6OXGpLJ8fdzCqyvFRKqhPtymL75dn36+en08O7/wuj9P
g46L/Gu1bgtjlZjL1RkLrav57yLLBx/na8Uow2x8lF+IeqHcy1J+BSy8NyWmBbKvln2Ziter+BT5
A1BLAwQUAAAICAAAACEAOO8SkfQAAABYAQAAIgAAAF/nqIvluo/mlofku7YvdGVtcGxhdGVzL2Vy
cm9yLmh0bWxdT8FqwzAMvecrhCF0O2Rhdze/UpxEacwcO8jOaAm+9RN62mCDMXbMZbd1sJ9ZSfcX
c0nLynSR9J7ek9THgCuHurTAcmHxpnaNYhD7qI8hV6a4AyedwoD0/an0HhL4/nw4DMN+eP152Ywf
b+P7btw9B02wmmSXFoXRYYc7YryU91AoYe2cYdO6dWKdcAiFoBKQyNAEsCyCELy+zS4W8zT0E9Ee
8QatFcuJaU+EONvnnXNGw5SSlmQjaM2gJqzmLIg7UovK0NVM6hJXs2uQFRQdUbh00VkkQGXxb0qZ
pdRhynuWHb62+8cnnoos4mn4KIv+vf4LUEsDBBQAAAgIAAAAIQDqv9MWwAMAALAKAAAiAAAAX+eo
i+W6j+aWh+S7ti90ZW1wbGF0ZXMvaW5kZXguaHRtbJVW3W7TShC+71OsLEUpEo4F17bfg6tobW8S
C/9pd1OIqkihQlDQKacIdHJUFRUQILgpEiAKTZ+m1E571VdgdtdOHOenSm7inZ2fb2a+nd3tGiIP
OYk8hjQHM9Lo8DDQUK2/sV1DThC79xH3eUBAcvX+8fj0Uzb8mL54gnR0cXZweXycHn9Q8vH30Xh0
BEbgS9mVfbhxBEG4kJmMuNyPI+QGmDFLS3Cb6B2CPT9qIxcHYI9pIdDsDQQ/0/O31JdcJYUt6RGH
xg80u4zNNJKSbueOvb2N3C6lEL/pYU4ajNMW90OyWa/dS3//qIXZ4W7NA+P6LdTvmwaYqKjGJKwA
UAQVPnSREI0DpiFMfawH2CGBpYGP7PAo3X2a7b3XSiBwYet0OYfU1Z8OlYhFtj0NdShpWRog7dKg
2YrpZt2PPPKwfhuJcFZCyZYELxBqdvps7+LXIP3wxTTwijBQfr81kzuyLMRjD/egEzmIhPohQBCd
CxiZyifgVE/BUa2/HKfCdTF6fiOotXOPgKDl3PdfVHLP+2QaObPsjQ2VOY3jkC3inIupN+WaWM32
scLty3eftXkecOwERGcu0CCYOlPrcvOl3jRyrielJTWlKmg/K1NyOi/MDQrHjPvu/Z4ueK3Z2fDn
1fCHafDOYjsoD9RZ1gf50aRO4E4cFrFsRDgk8jQImWSAsIBazmEwquiEzVweJndirzcPJ4fCgpgL
KAJ/UyzYwljrFUJ6010i+gF5yRh5SuuUZaFurs8IR5QwQrewJJilqidCNUOcNNqEb4rFrRv8CLaW
3KxQNrk3RyaZmdNlPfmlqZEtj1TJaSPBlPeasrGQZb0OtUAVFReugGbUDR1CJzrXZ7sVLcbBU1NW
uN8/H7yqbAOmYvP67B91hC5OT6/PDiqKns+SAOeQhPIzzV6at8wdRnccte3VfmAQKLWVvuaKXq4P
nAWW4MheXkEZRqpMx+Pynhncs1e1Px+96ze9RQlRTb+hcnjBmFWpERi0grRN37MkeX0vn7zlm+M2
mnbdUowW03j85fRq+G1m4q+f/KryrTl7VpqA+uwQAoEYw8uukfK0n5Q9IG34KO4DSQHTL7TUpu4V
nfFEY0zDz+uU/vtVHYecOzf5kEd64iM9+bbEumLaIUECrdn5nT4dnQ8OVezzwZt07/scgjzrEgfL
WZMw4TBUuHg6yGsyD9q5a2cHO9nwLfgbv/48uS/h8XQ3V0nsy68nl49ei4fh8bvx/pP05f/ZySh9
/jbb/S/dnzH7M9iRr7YSmIITlRflX1BLAwQUAAAICAAAACEAQD+YfwYCAABSBAAAIgAAAF/nqIvl
uo/mlofku7YvdGVtcGxhdGVzL2xvZ2luLmh0bWyNU01v00AQvfdXrFaKBAdjQAJxsP1XovV6E6+y
9i770TSKckBqq14ikKAHegEhqDilB5AiEQJ/Jsbpv2ASJ9gJAdUXe968tztvPDNsIXZiWZ4YhGNi
2IPUZgKj1uho2EKxkLSHLLeCAVK+nRXzS+Shxfer5WRSTD7efjgtv12XX2bl7B3Q4ZRK0VTHMhm0
qSDGACxkl+eeIl32LzqVOVRjV1hgGLVc5mitDnEl7muicHSE4AkSfrybpEQnm+RhQkZ0DyOiOfFS
niQsD7HVjuEIPAU+0Bvi9FF00GngQ6amqe0NmbMMbl/eTBfzn+Wbz8X4cjEfFxfnt2fj8up0+fW6
eDmtmhj4qnFCR+oMZcymMgmxksbi7ZHGEtrzVnmMcnlMBE+IZbV0Lee5chbZgWIhrkwBmWQQtanR
nbaVvRUEagfYcIhq9N59NBrhvfMEiZnYxda4USSPwNevi2nxahz46/hvWrMcC7O1LcYZpldf0H5n
JZWZEszu4Bk5ESzv2jTEzx5ipNlzxzVL1vyOpM4gJQhlqRQJ0yGGRi9/vC7OPv2pad+Jf8DK/+0V
N+fl+xd38qbgD/UlzNvGXx3v+qNOa5hor843fD5+8rRh9LC9qqY7eYudtfXCbKLq5SnNYfoH27DP
E2h5ZcW4OOMWR9vhrCiNCfVXI7hZumpLoEXVdkZHe6v8G1BLAwQUAAAICAAAACEAJhentYEDAAAu
CQAALAAAAF/nqIvluo/mlofku7YvdGVtcGxhdGVzL215X3Jlc2VydmF0aW9ucy5odG1snVbdTttI
FL7nKUaWkFmpIWqvHb+KNfFMiFX/yTOhjRBSWrUL2zbQClpWLBKialfsSptW0NI2IeVdaMaGq75C
z3jsxOQHwebGM9+cc+b8zXeyMo/oQ059wpBWxYwu1rnnamh+dW5lHlXdwL6PuMNdCki8/irZfXL5
9knS/RuV0OB096LTEZ13CkmOe0lvH5TAltIr2rADHy7hEjMYtbkT+Mh2MWMVLcRLtFSnmDj+kmbO
IfgZxFlWq3QX5qK0SatR8EAzB1//HXS7F52Pov/aKIcF2fpds+inUQZA2SynRo1ydr05J71zaiii
jEbLWGJsmn82jgjiuOrSklwWXMwl1CGzo8B1tYIvKZ4LEcxxKUUKIkpMBn8VU3g0CWYKZrzzPt7b
N8qwVNuTy51Pw+2wNENE9LcG3eeQs7Jovx702+nBbOsH62Lzy0j53Vp8fDjcJs9O4taj0d1b7UF/
b7pBQMeCkHIT4Rq8GpDmpDoUqBZExQohx58o2C0SR8yVlaL+olpTC6pD0eoqeEdm6uaV9IMHEQ61
cVOM44hb3PGkofPW1tgxPIv88NpbJjwMAs/y8e0VQ3CnqTQhh/p5a1u/rQkbCMHyG16VRv/bhh9w
ym6ubbAQD58epJQ3GFKfEhjWQ0gj8IQ+9nAXicNCFzetTKNSQbr4/nTQP0u2D3VEXUbRgm5j36au
S8mN1L8cic038cl6pq6TwKf6bxDAROXHtGWIMgrz+kinHmRdf6vopjyB4T3wfDzkUV4PCDBtwLiG
cMpuFQ2CaESuBRJ5ZqzCnfod6YHlkErREYekCUApmQGn15zIq2gqT+LlxsWH98BEceczENDlWjt+
81GctuLt7z9PXyQHwEe7ueTOz9N9bXYGUtcdP2xwxJshrWh1hxDqa0h2c0WzbBbVLB7cl9Aydhs0
jWaELqgyXW+/2uB8xPLZTn1KBPtL0PPZjnkYiD1zhTWqnsM1U4WSTxklOftGoywrcW3N0y6bB+bY
VkMUemBGZaf31STX5oZ9Ill0zBiIXyVdAOR0mjUtRx7OFWcf9ULeLMnGhElXGI/1e+bF2Z/x0UG8
94dKUj6t4USJhKbYO1RnMM7Exu+Dr/8lj7+JtV7yT/dy50i1kmgfi80PYv0v0esq4R+tx8OZb+Dp
BQwjx8NRU0P1iNau9rrjE/owe8hio5dXEMto06CL6R/7P/MLUEsDBBQAAAgIAAAAIQCZcDpRVwMA
ACQLAAAkAAAAX+eoi+W6j+aWh+S7ti90ZW1wbGF0ZXMvcmVzZXJ2ZS5odG1s1VbbTttAEH3nK1Yr
IWjVEGjVN8e/Eq3tTbzC9rrrNRBFkVBV9SJKWxVapN4QFS1UlSgSVZESEB/TOAl/0Vk7TpyQUG4P
rV/sncycnTNzNjvVSUSXJPWsAGGDBHTGlq6D0WRtojqJDIeb80gy6VCwtN7uR4366edH7fpXlEPN
o3edvb1obzuxtA8a7cYmBAFWEpfFMLkHm0hl0wJqSsY9ZDokCAq4xIWb80mZYn0CwaNZbCH9TZlz
NiUW88qA4frElOm6656G9FexxU8haIUagi9iPUmztfElevFYy/tD/vacnuWn5cEwBtENJbWw3vlx
eLr8rLXyrVeI6PXz5vFJe323tfHrdOPn7+WHA/to+V6a3c/kWxUAuVTa3ALGPJA43ckkwkJxfdQX
Rh5fIA6ziKQZVOb5oUSy4tMCtpllUQ8ciQurohmIUlHyeWWCyBBs1SrqW6dvoVptqI4DbSkLZiG5
yHMmd0LXC/BQTRxiUGfQFtsDn3h6ry5IM1JUQR+ETKjy3dbyhq7lY88RANQBlXR5CM7dIgP+afTZ
APWA2CBppLwR8+J3oAQ3ylfjfqzBflWU+wywhYIoJFbqWQoF1M0A0JLEqJUIHbwma3oarbKFeC2f
gI/NEgJVoiNSg3rE+ENlzo+o8/m174m99WHzCuXPakrJLVWUoAEVC7SY2DLFy9jjCo7u1BWIREfL
0c5KcqCuq6NAEiGLkrmQO7O661yyvpC0YsJKW32kSygsie7rK1mDuvpo4wWWBv8L4mo31lofN2+m
J5BupiOwulI/UpSb6EaK9X/0Ijpea9ZXmvV6Plp90zxejV6ttnf2L3SwJVz86cH2QYGVovrGyCVL
DvXK0i7gubuz2WPe94oL5zvEpDZ3LCoKGO7CaOs7vjah1tbT6OXhpQmYMLsUvdA1qDiPQcbt+hR6
V2XoOLlFZkkbj23T9pPWwe5YVooJEZR0yXhc0mCAxv1ZoAFjDOx2b0zWwC6Oi4WZAv6FS2YkGXn/
k3hSO3PtG6GU/QGuu0peOV8wl4gK7vYoCA2XSay3t/Y66agI/w6x7xAqGQ0I4yL3rBjSFrQUtzEU
ThESnJ5inkWXpu4gdfEUsrdQMtl0Ttaj95/SmY+MmcUUVX1CHUkzOc5DU+wfUEsDBBQAAAgIAAAA
IQApmWXkCAAAAAYAAAAYAAAAX+eoi+W6j+aWh+S7ti/niYjmnKwudHh0M9Qz0DPkAgBQSwMEFAAA
CAgAAAAhAIcKsiQ8AgAAlAMAABQAAADikaAg5ZCv5Yqo57O757ufLmJhdI2TXW/SYBTH70n6HZ40
IdEbXmI03mCMSdVdDBbEt2QJ2eBBiEhJKQu7MbixjPdi1G0sRTLWbcQITJjbbFn8LrPP89ArvsIK
7RRcjOtVe3J6zv/8/ufch4EwC9hQiLIEwoE4uHPb4XCCe7FklLLwET4KgdrfHrTbqC1pjQyR90lP
IUp92M+itZ622cItiXzZRMIuKvWQ0EHvRNxqDPs5vVwQ2IOAtr4Nxh00ZaEskRCIsTyAqUiCB7Sf
NAtIFvDGuqocz3PJGB95A+fjy3yYjdlgCtLgBmUB+jMSaPvzCgY/K4OdIil3UGNVPS2hdhF/Ohr2
i4POibZewhuHg4NdVC4YcbKd0Vab5KyLimu/0itXKsYXkgloRlMRHtgXgZOy3BzJTUBd5dxL32OP
+6nv4V2XkzZjswzjm3E/8ns9nlm/Z45x+x94Pc+fMN5xyjUG+yslAbklyNniy5cN9FLPGK+feTHj
c1khx7FcFC7BqPWSIm2dyLDSLhftdPwTl+EXFr/i2h46/IbEpgFQJ4a/C4ODLKo20UmXKALOfUD9
9G9KI9lsUkfCg1vAHmMXObjw2tyMSV4Ok5dp71Vx/9XW3cFiDmcrKF9HlQ7KN3UNupu4vkfEgnqa
12oN3UcsVIgkT5kI0P4K/iyqSolkjvVdwFtlUj0zdgHn8+dpcZp0lH2VOE/XjC8k/VBliShVohzh
vKTKMnq/dc0dmZrRnN/8Y2IwA6txEnphXMypioIzgvaxbRyPcTnjnuMmJt0LUEsDBBQAAAgIAAAA
IQA2ti3KtAEAAKECAAAUAAAA4pGhIOeri+WNs+Wkh+S7vS5iYXSNkD1PwkAcxvcm/Q6XJiS6IAwa
F4yTcVIHHUxIjMAhjZU2UAwuhgCKYnlx0KhBDVqUQYsvRLGF+F2wd1cmv4KHJQR00I7N/57n93um
oT8kAjEYZBl/yC+BiXGXyw2mwjGBZWReFiBAasY0WmbzzNI0pKmdqzTRb8izQYxL+iYAxgKAc2wH
JBfHMizDB0FYlAGM81EZcCukeoD0Aj6mES/eSCws8xvQK23JITHshHHIgRGWAfT7prDei1ZZIfka
ukqZjRzSFHxU/2wqVu21k8nh4wfr9hrlD+z/5CzdSVVJ6wkpOx+JpB0jrcaisJcY52Uw5gNulhnt
gkUh5VlYXpydn1tanJn0uCnuP/h+nPhW/esxySltcX1VzgEjETEiwE0oODiPh3MNWTkHBO0p8VMZ
l/a7FntFSk7tSFmzNNXe1C5C6hsq1Eyjgu+vUaOBdk/RToVOQdIvqJhHuzmrdmQ2Eviu3He3G26S
+KJkGrnuofpo1Sv2fjibbSdKwy6CuBZtJ877haauEuOUGHWcVU1dR4cnQ9nOPybu3QyK7hVR9pKG
9CpK1V8M9p5DGJ2M8t3bK/oCUEsDBBQAAAgIAAAAIQAFpesa5QUAACMOAAAgAAAA4pGiIOiuvue9
ruW8gOacuuiHquWKqOWQr+WKqC5iYXTNVm1TE1cU/p6Z/Ic7Gag6dYHUtuPoxCnFVBklZEiUOuow
y+5NssNm787dDci0OqGCIO+MgC8NQ1FQptWI1PqSmPJfbO5u+NS/0LMvCREI4OiH5sNOdu855z7n
Oec8936HhQRBJBbzeoSEoKJvv2lq8qNTSkqGDyJqFJGv/oaoNvm8Hq9HwTrSsKZJRLFN0FenvvB7
PVIMjDClhMq4F8v1vkDA1+RDcaITdALDN17HotejgbevLRiMtobOdHW0t7d1RYLRC+GuSEtHazga
qL8Rs3ZRSR+mWgLLMuJCJExJTJIx4lpIMskrgEan/egnVKcGIjpPdQ4MBECEuB/ALMzrCVSHld4T
NbdB3EVMu1FHSmkGp05e0hEX5jUtmqCpkwhfg/fLkqJfrVMbgvDSQkSMriOB14UEbGuvH0PXfW42
TuTgj60Av5oAl5OtZZuTYz502OtB8LNIb9j6i9jrdTY1b7waMbNL5vQtNnPPmBwxFm7++27cWF8y
MreLG1lj9m0xny8W5krZv81C9p/0L04AlU9p2Os54vXY6Bq7UfW+VtmqamADU4huZaJBAl3m6hjL
TRnzw8X8qys0pehSEl9R+/UEURrwNVyuYlKCqivxj3Hv+2h/DdNeTBvU/h1eLt3R5si5rlBzWzBQ
fPeglM2y7PLmw0Ez99j8M2/mF3279ZjtE74UPdse6gzYrbw/6JpxIsGOi8GO3cJsYa/p3Nnece50
627e9nDVaPzgNSykdJi4MJEloR9936/yVr9XBqKOF6zlQAj3cREhgcWUjMUor/U029/LEfAug1HN
DeKaaTyVxIqODl8WEjy9euxr9GUtJ4cIWC+bHoFhIrQHqnVaoljQCYxpDV+Xh5OoTqdSPI7pTuhR
ZwEw6faYp1SwVqmkCJLKyzvtw+UlxF2ASrSK6FDkUiQabDuEuPMkTpRov4pRBGokCbhZEAgUHXEg
AueteUVnpXgCa/pJ1IHj0JeYfhgdcdYzxCddDittCPgcjt0agKELvJwaiEsF2lYCoFaEChj9jNpT
OhdKybKvMhm7KWmVaiBn+tnyi9LLFZCH0vPXpYFZ6H82MVcsTJizf5UGZyoqsl0k7CiuSvhtzbBF
XUsgXuyNQeH6eGi/yh8RhAMahwIRSIH0A75d544zbo+x0VVf9ZnwGaKy6Uk2tXbQqLwoHhyoKNGA
pCB3csCd9CGVkjjlkwHfviLhCBvY60QgciDaEkYyEXhZJVQPHG863mStWdMbgIpbwntUJEke9qM4
SXQsqQHbXEt1Wweqc9buUX1HC8uJdsV4iCx+Ohdlej+Ni77/BxlezwGOstrHDcfBvAs9B77U8DLF
vNjfBbso5QNKSOigEhpqBFlBjdEQuFeEot63LfTe6UHYqsxihKLG86i+/gwC2g77j/qPHj+CRFKW
BStPkoKZ1pEfNSqkG7D1uExa65+bGStm+RivJABbO9g1S68t4CAuu2WzZeBegsoXIFfYsuPGyDTo
VjG/Ah3q6Bl7lzYyOePuKxA8p3utlh7+HWaZTT+Hp61zTqDp8WJujE09B38z/8TMPysWNszZVTa5
aCysmA8G2Ys0W1w0CzMss8YW0hVPQOKKZFkgm2y4O0r9AWwbWDUSSMTMT7m5rBSqgBXusNsTDnoj
8xTAsLUXLLNa2pguLY1DYsU3E5uZdOnxwObwBFue2J5Y8c2Y8XRpJx8se3OLjMyqM9VO0H1zq9yv
qnNyfJ1OAExWQeZeOicNIDPm10pPHrHJMec7ELp5c9UsrLPxoT2289vb7ZjanVTCDZeNLlnX4ZFf
WT5n0VK4hTolRSR9GnLuwmxooJR9U6ljKbuxeTe7RdTeJ6LxKG0srmzeWzcH/mAP7xsj82Z+0Hw2
b86t7Iu/upEPCN2tt4O7XB1ocDa6uL3AgJsNjbCpcTacf5/OvJ/5DTkmToyGbl5/n14A8GZmjA29
LhbuGFPT5rK1kRsBkjFGR8H3w2mWSVyzPO03tvy2mFs28/fN/EtjdLmYy5WvCTUz/w9QSwMEFAAA
CAgAAAAhABhzSePGAwAA8AYAACAAAADikaMg5YGc5q2i5pys5qyh5ZCO5Y+w57O757ufLmJhdJ1U
bW8aRxD+jsR/GJ2gBjXHS6z2gyOipghbSDUgQ+pKdoSWY/Fdfexe9xbHqEmUpC9OE6d2pTatKtrK
VhRZbUqkNHIicJsfU+6gn/oXOndgQA2OqoLEsTczO8/MPM+8QzWdA6/VggFN1yx4+61EIgkXWcMM
BqQhTQrOrZb762Hv5PtBu+20H/51+Gm/86j/W7ff/QljqhCvghK+UbUSSjAQDDAqwaa2bXDm3wLn
L76RDAaMGjpRIbgw6RY1w0oqpSQU2OCSwwLFd0TSajBgY7SynMmUsrml8ko+v1wuZkqXC+VieiVb
KKXCN2peFotfpcLWqWmCmuMFwWsG4lTTvF4nDNFI0YSPIWSlipIIqaKDhohAXUS3ApE6hCjbWjgz
DajvU1GBlQa7hEGrxJCgFohtl3TRuAB0G89rBpNXQlYsg4c0r1K4DhqRmo5pffs8XFdG1RRL+UI5
80EW0U/XP2rJ2Op3ZF6BSDAA+PGmEpv8Bef5U2fvgXt8p98+6O9/7nz1nfvlHfeHT/4+2R2Own16
4La+GBz+MhzXnzdvD6Mt0rBpMBANBnxg8QpM5fQGNtV9HxPj0qvBRujl/tE9p7PnPtjpdY/XRYNJ
o07XrabUOYvRbXo6v7qB82Ybrw+3qdiiImY1X4nCRmm6JPamDfEMzi9eyoEyk2/KNKVmsWXlcq6U
Xc6kfD7OhH82fTLbVGtI5G2Bm4bWhHebFvFYM6ZVSHAuU2vZfMwj0ZWFhSUqFxum6Z0ir1JqhCUa
KwmjjnVF5tbnovAm4OMCYKiaNupZZkvCNAqrBps/Xz5l6jVY1amgar7yIdUkkioSKsdypI4o6Ucw
N5nAHKhcwAzjVd8aBdVDjvZhcaQyEsDs1zFfL/aq4dXjFXturSgFzghbYBFh2Jxh1XlRNRgxsxuM
C5omNo0i+6/BIhcZoukTzEXJrYn2sn6+0REPKvprXtc9TVzSvL5DEefApNlMcxwVa6Cq/jXwGhYb
fw/C4SUwGESS5/CbiEKVn8rmP1D2bFqqqqZTbXM6pXcnknoiXEiO2GtjdZYnGs/FS8IbqC6J9jjj
FUHJ5miJovImEeUawRp9qU1uGGnd17nbeuw+PphJflwB/e7elLyHi+HRbffH1qD9R//39uDljnNy
0211Bjs/O3ePnP0n+IsbovfiHl46NLnfHve6970Evs/g5f7gYHd8HcIYLYzTbZHwsY61Oo11GDvs
Y+/Ffae9637zDNMNnjwf3PraB/3Mvfuw1+ngunpNiuS4HeP2TKeZ3m+Yw/nsaNiD/5fpH1BLAwQU
AAAICAAAACEAzSVpNRIEAAD/BwAAIAAAAOKRpCDlj5bmtojlvIDmnLroh6rliqjlkK/liqguYmF0
rVVRT9tWFH6PlP9wZMFItDoJRdsDVaqxLKBIJWQkHZOgim6cG+zh+HrX10C0tqJbNWCFAhqtpomq
a7VN1aSFqdNalUD3X1jsZE/7Czu2QwhrQH2YI8W+Puee853zfff4A6qoDFilEg4pqmLC++8lEsNw
1bD1cEhoQqfgbD10X6w1D79v1+tO/ce/n95tHfzc+r3Rajx2DlfcvYP26i/ON8+c7X38xyhliJdB
GrxdNhNSOBQOGVSARS1LY4YfFy5ffWc4HNIq6EQ5Z1yni1QflJJJKSHBPBMMRim+I4KWwyELd0uT
6XQhk50oTk9NTRbz6cL1XDGfms7kCsnB2xUvi8mWKLdUqusgZ1mOs4qGyOUUq1aJgWgEr8EXMGAm
84JwIaODgohAHke3HBEqDFBjcfTcNCB/QnkJpm1jDDfNEE2AnCOWVVC5fQXoMq5nNUPcGDBjaVyk
WJnCLVCIUFRM69tH4JbUqSY1lk2lrxXTn2YQf28HOk3psftdGZEgEg4BXh5XsdNHcF4+D9hp1Z+0
tr92dr5z76+5j77653DDff7E3Vtv/ll3d181G43m0YN2/XXrqP7XypdBAJPYFg2HouGQjy9egjOJ
PeZ6aEDoiiqItWBBPI0djReyIPXVhNRLcj/+pq9nC5nJdNJXSLH17J5zsOU+XG02Xsxx2xBalZ5P
aHqZKrZAJeWYrik1+LBmEo/HLtEDnDGRnM1MxTxab4yOTlAxbuu6t4q8SXIHSzRW4FoV64oMzQ1F
4V3A2xXArXJKq2YMSxBDoTCjGSOXiyfauQkzKuVUnip9RhWBNEcGirEsqSJK+jkMmTWhMiNGl+kQ
yIxDH+OSb42C7CFHe1AcKXUk2f91zFewNaN59XjFXprNC64Z89gCk3DNYgZWPcXLmkH0zLzBOE0R
i0ZRjzdhnPE0UdRTzHnBzNPTkPHzdZa4kNFf8bruaXRM8foOeeTBEHotxZAqw0ad/5fwrk4+QvkI
eqFU4uO9m3FSWCqQ8mJF43SJIPfdh3IQi9uoAgP7mOwfUXbX7+EUkv7nqM72fWfrt3ML/dimOF/e
8khcOPcUT2h6sUKwy/6pQ288nhaeob4nZe5UZhKc52tRvkh5zKx150gFBRm/BoODE6AZEBm+hL9E
FMrsxMG73iLj+YlkWVGpstBb+ElcD2a3fBg+U7lf9Ymjl47ZOJoEesUNVuKULHQ+TZ49Gtz6di7q
z6+eoJ3ReXZsvvn9wsnpHH3rrG8GlAf8NV899rbc2XN/ferPTz9Ks/ETOjUbm87WvrO14aw2jlf2
jnd+gCBSsDVWIuJ45VEgy8DQjYBwOkP4ZAInelB3SzmD3IcdzHanvuGubSPg9v7L9p1dzOdsPmge
bbZ2/2jf3el+ES5Ih6T8C1BLAwQUAAAICAAAACEA5Wu4oTcFAACcCgAAEAAAAOS9v+eUqOivtOaY
ji50eHSNVstSG1cQ3atK/3B/AJWxSRapys6b7LJJsqEqBYFKUUkgBSTxcoRBLzSMZIOeoyAJSRAe
IzBYiBkJ/Qu+j5mVfyF9b88MAgsniKKk0b3dp0+fPg0dVFzLYlbLa24KuyMuHeHs32m78MtNTVga
0/focCR2j9zuFS/tRCNf/8dPNBKNeNoG3BC7793N/MdB5csZQoc6+WFpeWHlrzUy/YzwVOH+47S8
Eo2I01Pa1/hpgxnHLF4hM4SftaORqUc/8uh0jPBMhu9d0f4xLySp02OtG757y1LnCq8OgZhx7u2N
uLlP+zrbuuSmLSqbCIn2Mx+0jWiEEMIcG+qXV80j8pKIaunjIEtvt1lnA4C//GqWTiJIYngeI8zI
sqRzp5l3+TphuS7LHOH3sfm59TutppJEIy8A7XvDPUyx8hFPv2UDjeV2IA1QxFPXLKezxgkkm1v4
bWkZQaVq7HCbdROiHgdckOBHcbTNbANrnV2YW5+b9ToF4EqUHTbcc686zLjGC7H1VzK3l8z66Wdi
BE9BVkwPAYXVELkEPAGayF3iDUEw+BiuS3gbN0RhgtJ2vFoT6JMXBwX3No8nEGzjhCXKvHDu473p
0VGNnZUgO5KhGPyHDusyRXDMS+rwHnH5OKMROMZyWWpvo+LGe69U5eTd9DsMKo6LzDiA0Lx0K1o2
AMObrjXyihbAY+Y5q2kqNEjFHZVBKviMGXnhlOVxEMmFxvb3xTAPdEF9GIPpl8zoqqv+A+PYMzW3
Ew/bKEXS1/Ehs9LuwRZ1HDrco33TbY/CgqjTlqRrO/QmNaGgKcLT2yAalA5I4H8JKrwVN/lZU97a
uvSKZ7ye9Jol2pdc0MlTXYMSfRIVfX441gJVDfneOdethyAaRJwoOtSJhyBARzBTbvIYoLijnNvI
qruKKfURh1nFaRLXuhVDa/wOlveosMqGrMo8lS6gtDkWOUR1QLD08XOTaJKDn9CfwBlGa8EYF3gv
9Xls8sV3e8pgksI+xEnkxd5Dg/LNCWhACQvnimda1LZZvgS647U2ThEGgWjfv4rJ11im58rbHmRR
7sQbWRhCEB5iZ9ZrdRzMJTBSt9sE7fNSl+U6bCvuWn06Amu7AZQ4zpAYvg0SgS/4Wcx9rwhWqksx
K6f8oMXDjyAtVIuMYW4D5hA/T+VYRupK9iCh42EV+wuI3dngf5vcyMF4+uW2LtyrNoRxu9cYXrJ9
fcFSda/cgpXgdhKiWsBJwtIDkwFGPnHBX1d+XoPcof9TuwVzPc542Dk22vIajmud06Fx3zB/BB9u
ETnlyLd5Kv2iajOrotZCFTHLb7cuqH0CD4Gn7+TOAOzgIfAG/AKWlqhaga1NPal83AEYUxw68ASi
QLdgH6BbhZCC4TB1lmmA+eASu+/UIC/MurxcOAMU2G00Ll6s80t/fwdR1Jh7r4dPLRbcHbLZTo11
b3wdqBaFQGDC8+TFMyJtA71IGcSkkPNzP/3yx+9rYxtJDaYkMrBlnF3JkNrg1H7jxnehksBo1IqW
u0Gtdkk0bCmn/IgP4eShU9iv0NbJN98+zAlor9uspbNUTzJWHXG9SVYX1xZX/5xbX1pZji3MQ6d5
8VCkU1IBKNxJ4wzKprdVGGd+dsAGxvjA3jtG+q00Iahs3Ch8k4BhelQCN9P8XQP+Ym8BIPY28AW4
8ekOxHl3RyVgH6Dj2AFWVFywWrLjqwWaARtCVK/4Thv/IwgXW+ApogGMt2i/Jb0V9WYe4c6Ui1R1
ImQ5sBPq6GKzFxYNWZ+a21lJ+OJqDN5LMJMn919QSwECFAMUAAAICAAAACEAE+2msnYlAAAWrwAA
FAAAAAAAAAAAAAAApIEAAAAAX+eoi+W6j+aWh+S7ti9hcHAucHlQSwECFAMUAAAICAAAACEApr5m
GGcEAAAQDAAAFwAAAAAAAAAAAAAApIGoJQAAX+eoi+W6j+aWh+S7ti9iYWNrdXAucHlQSwECFAMU
AAAICAAAACEAYC0iExkHAAD7EQAAHgAAAAAAAAAAAAAApIFEKgAAX+eoi+W6j+aWh+S7ti9taWdy
YXRlX2NoZWNrLnB5UEsBAhQDFAAACAgAAAAhACWYEX4aAAAAHwAAAB4AAAAAAAAAAAAAAKSBmTEA
AF/nqIvluo/mlofku7YvcmVxdWlyZW1lbnRzLnR4dFBLAQIUAxQAAAgIAAAAIQAf4bl2mgoAAPkb
AAAXAAAAAAAAAAAAAACkge8xAABf56iL5bqP5paH5Lu2L3NlcnZlci5weVBLAQIUAxQAAAgIAAAA
IQDwNCD+gw0AAAo0AAAcAAAAAAAAAAAAAACkgb48AABf56iL5bqP5paH5Lu2L3N0YXRpYy9hcHAu
Y3NzUEsBAhQDFAAACAgAAAAhAPMbK7A3AgAABggAABsAAAAAAAAAAAAAAKSBe0oAAF/nqIvluo/m
lofku7Yvc3RhdGljL2FwcC5qc1BLAQIUAxQAAAgIAAAAIQDH0/Fo6wAAAK8BAAAgAAAAAAAAAAAA
AACkgetMAABf56iL5bqP5paH5Lu2L3N0YXRpYy9mYXZpY29uLnN2Z1BLAQIUAxQAAAgIAAAAIQBc
6+o00AAAAMsBAAAoAAAAAAAAAAAAAACkgRROAABf56iL5bqP5paH5Lu2L3RlbXBsYXRlcy9fYWRt
aW5fdGFicy5odG1sUEsBAhQDFAAACAgAAAAhAD2JgJEDBAAAOQsAAC8AAAAAAAAAAAAAAKSBKk8A
AF/nqIvluo/mlofku7YvdGVtcGxhdGVzL2FkbWluX3Jlc2VydmF0aW9ucy5odG1sUEsBAhQDFAAA
CAgAAAAhANy8WCbwBAAAzw8AACgAAAAAAAAAAAAAAKSBelMAAF/nqIvluo/mlofku7YvdGVtcGxh
dGVzL2FkbWluX3Jvb21zLmh0bWxQSwECFAMUAAAICAAAACEACBQAyNIEAAC7EQAAKAAAAAAAAAAA
AAAApIGwWAAAX+eoi+W6j+aWh+S7ti90ZW1wbGF0ZXMvYWRtaW5fdXNlcnMuaHRtbFBLAQIUAxQA
AAgIAAAAIQDzFJCTRwMAAJsIAAAhAAAAAAAAAAAAAACkgchdAABf56iL5bqP5paH5Lu2L3RlbXBs
YXRlcy9iYXNlLmh0bWxQSwECFAMUAAAICAAAACEAOO8SkfQAAABYAQAAIgAAAAAAAAAAAAAApIFO
YQAAX+eoi+W6j+aWh+S7ti90ZW1wbGF0ZXMvZXJyb3IuaHRtbFBLAQIUAxQAAAgIAAAAIQDqv9MW
wAMAALAKAAAiAAAAAAAAAAAAAACkgYJiAABf56iL5bqP5paH5Lu2L3RlbXBsYXRlcy9pbmRleC5o
dG1sUEsBAhQDFAAACAgAAAAhAEA/mH8GAgAAUgQAACIAAAAAAAAAAAAAAKSBgmYAAF/nqIvluo/m
lofku7YvdGVtcGxhdGVzL2xvZ2luLmh0bWxQSwECFAMUAAAICAAAACEAJhentYEDAAAuCQAALAAA
AAAAAAAAAAAApIHIaAAAX+eoi+W6j+aWh+S7ti90ZW1wbGF0ZXMvbXlfcmVzZXJ2YXRpb25zLmh0
bWxQSwECFAMUAAAICAAAACEAmXA6UVcDAAAkCwAAJAAAAAAAAAAAAAAApIGTbAAAX+eoi+W6j+aW
h+S7ti90ZW1wbGF0ZXMvcmVzZXJ2ZS5odG1sUEsBAhQDFAAACAgAAAAhACmZZeQIAAAABgAAABgA
AAAAAAAAAAAAAKSBLHAAAF/nqIvluo/mlofku7Yv54mI5pysLnR4dFBLAQIUAxQAAAgIAAAAIQCH
CrIkPAIAAJQDAAAUAAAAAAAAAAAAAACkgWpwAADikaAg5ZCv5Yqo57O757ufLmJhdFBLAQIUAxQA
AAgIAAAAIQA2ti3KtAEAAKECAAAUAAAAAAAAAAAAAACkgdhyAADikaEg56uL5Y2z5aSH5Lu9LmJh
dFBLAQIUAxQAAAgIAAAAIQAFpesa5QUAACMOAAAgAAAAAAAAAAAAAACkgb50AADikaIg6K6+572u
5byA5py66Ieq5Yqo5ZCv5YqoLmJhdFBLAQIUAxQAAAgIAAAAIQAYc0njxgMAAPAGAAAgAAAAAAAA
AAAAAACkgeF6AADikaMg5YGc5q2i5pys5qyh5ZCO5Y+w57O757ufLmJhdFBLAQIUAxQAAAgIAAAA
IQDNJWk1EgQAAP8HAAAgAAAAAAAAAAAAAACkgeV+AADikaQg5Y+W5raI5byA5py66Ieq5Yqo5ZCv
5YqoLmJhdFBLAQIUAxQAAAgIAAAAIQDla7ihNwUAAJwKAAAQAAAAAAAAAAAAAACkgTWDAADkvb/n
lKjor7TmmI4udHh0UEsFBgAAAAAZABkAgQcAAJqIAAAAAA==
