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

$script:PackageVersionText = '1.0.1'
$script:ExpectedPayloadSha256 = '221cb0d21b59c064234adba69c50a792bab11c075a1bade97cb6c998abbf0ea2'
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
