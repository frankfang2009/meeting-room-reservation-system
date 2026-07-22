param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PackageVersionText = '__PACKAGE_VERSION__'
$script:ExpectedPayloadSha256 = '__PAYLOAD_SHA256__'
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
