param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PackageVersionText = '__PACKAGE_VERSION__'
$script:ExpectedPayloadSha256 = '__PAYLOAD_SHA256__'
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
    $process = Start-Process -FilePath $python `
        -ArgumentList @(('"{0}"' -f $server)) `
        -WorkingDirectory $programRoot -WindowStyle Minimized -PassThru
    Write-Log "升级包初始即处于完整管理员令牌；使用同一用户令牌恢复普通启动方式，PID=$($process.Id)" 'WARN'
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        $ownedIds = @(
            Get-OwnedServerProcesses -ProgramRoot $programRoot |
                ForEach-Object { [int]$_.ProcessId }
        )
        if ($ownedIds -contains [int]$process.Id) { return [int]$process.Id }
        Start-Sleep -Milliseconds 250
    }
    throw '使用当前管理员令牌启动的服务进程不属于当前安装目录。'
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
