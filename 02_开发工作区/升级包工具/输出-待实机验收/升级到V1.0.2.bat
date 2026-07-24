@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 会议室预约系统升级

set "MEETING_ROOM_UPGRADE_BAT=%~f0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $principal=New-Object Security.Principal.WindowsPrincipal($identity); if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}" >nul 2>&1
if not "%errorlevel%"=="0" goto :need_elevation
goto :run_upgrade

:need_elevation
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try{$child=Start-Process -FilePath $env:MEETING_ROOM_UPGRADE_BAT -Verb RunAs -Wait -PassThru; if($null -ne $child -and $null -ne $child.ExitCode){exit ([int]$child.ExitCode)}else{exit 6}}catch{$exception=$_.Exception; $nativeCode=$null; while($null -ne $exception){if($exception -is [System.ComponentModel.Win32Exception]){$nativeCode=$exception.NativeErrorCode; break}; $exception=$exception.InnerException}; if($nativeCode -eq 1223){exit 3}else{exit 6}}"
set "UPGRADE_RC=%errorlevel%"
if "%UPGRADE_RC%"=="3" goto :uac_cancelled
if "%UPGRADE_RC%"=="6" goto :elevation_failed
exit /b %UPGRADE_RC%

:uac_cancelled
echo.
echo 升级未开始，未修改任何文件。
echo.
pause
exit /b 3

:elevation_failed
echo.
echo 无法打开管理员升级窗口，请联系维护人员。
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
__UPGRADE_PS1_BELOW__
param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PackageVersionText = '1.0.2'
$script:ExpectedPayloadSha256 = '8cbe9b7b8c8df798825fe7657f17a7ecbb73046f2fa40eabdfc4cb0af9ddeacc'
$script:TaskName = '会议室预约系统'
$script:LogPath = $null
$script:LockStream = $null
$script:TempRoot = $null
$script:KeepTemporary = $false
$script:PayloadAttributeCheckDegraded = $false
$script:Utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
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
                # 枚举后进程可能已经自行退出；最终以 --check 的轮询结果为准。
                Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
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

function Assert-CommittedState {
    param($State, [switch]$AllowHealthcheckHandoff)
    $stage = [string]$State.Stage
    $validStage = $stage -eq 'version_committed' -or
                  ($AllowHealthcheckHandoff -and $stage -eq 'healthcheck_passed')
    if (-not $validStage -or [string]$State.TransactionId -notmatch '^[0-9a-fA-F]{32}$') {
        throw '升级已提交状态内容非法。'
    }
    if ([string]$State.PackageVersion -notmatch '^\d+\.\d+\.\d+$') {
        throw '升级已提交状态中的包版本非法。'
    }
    if ($State.WasRunning -isnot [bool] -or $State.TaskExists -isnot [bool]) {
        throw '升级已提交状态中的运行信息非法。'
    }
}

function Test-CommittedTransactionState {
    param([string]$ProgramRoot, $State)
    if ([string]$State.Stage -eq 'version_committed') { return $true }
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

    Write-Log "发现已经提交成功的升级事务，正在安全收尾，版本=$($installed.Text)，原阶段=$($State.Stage)" 'WARN'
    # 事务早已提交后，用户可能又手动启动或停止过系统；只能观察当前状态，
    # 不得再按升级前的 WasRunning 改变它，更不得恢复程序或 data。
    if (Test-SystemRunning -ProgramRoot $programRoot) {
        Write-Log '已提交版本当前正在运行，健康检查通过。'
    }
    else {
        Write-Log '已提交版本当前处于停止状态；保留用户当前运行状态，不主动启动。'
    }
    Remove-VersionTemporaryFile -ProgramRoot $programRoot -TransactionId ([string]$State.TransactionId)
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
    Write-Log '已提交事务的残留状态已清除；现有程序和 data 均未回滚。'
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
    if ($script:PayloadAttributeCheckDegraded) {
        Write-Log '当前 .NET 不提供 ZIP ExternalAttributes；已退化为路径白名单/黑名单校验，并保留解包后重解析点检查。' 'WARN'
    }
    Open-UpgradeLock -ProgramRoot $programRoot
    $statePath = Join-Path $programRoot '_升级状态.json'

    if (Test-Path -LiteralPath $statePath) {
        Write-User '发现上次升级状态，正在安全处理……' Yellow
        try {
            $oldState = (Read-Utf8NoBom -Path $statePath) | ConvertFrom-Json
            if ([string]$oldState.Stage -eq 'preparing') {
                Recover-PreparingTransaction -InstallRoot $installRoot -State $oldState -StatePath $statePath
            }
            elseif (Test-CommittedTransactionState -ProgramRoot $programRoot -State $oldState) {
                Recover-CommittedTransaction -InstallRoot $installRoot -State $oldState -StatePath $statePath
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
        # 版本.txt 是事务提交点。提交后即使状态清理失败，也绝不能回滚程序或 data。
        $transactionCommitted = $true
        try {
            Update-TransactionStage -State $state -StatePath $statePath -Stage 'version_committed'
            Remove-Item -LiteralPath $statePath -Force
            Write-Log '升级事务成功完成，version_committed 状态文件已删除。'
        }
        catch {
            # 状态文件可能仍是 healthcheck_passed，也可能已经是 version_committed。
            # 下次运行会先核对正式版本文件并按已提交事务安全收尾，绝不回滚数据。
            Write-Log "版本已提交，但事务状态清理未完成；下次运行将自动安全收尾：$($_.Exception.Message)" 'WARN'
        }
        Write-User ''
        Write-User "升级成功！当前版本 V$($script:PackageVersionText)" Green
        return 0
    }
    catch {
        $failure = $_
        if ($transactionCommitted) {
            Write-Log "事务已提交，提交后的显示或日志步骤异常：$($failure.Exception.Message)" 'WARN'
            Write-User ''
            Write-User "升级成功！当前版本 V$($script:PackageVersionText)；收尾状态将在下次运行时自动清理。" Yellow
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
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
GQAZAIEHAABSkgAAAAA=
