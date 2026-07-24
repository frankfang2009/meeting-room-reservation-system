Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$toolRoot = Join-Path $repoRoot '02_开发工作区\升级包工具'
$sourceRoot = Join-Path $repoRoot '02_开发工作区\源代码工作区'
$targetVersion = (Get-Content -LiteralPath (Join-Path $sourceRoot '版本.txt') -Raw).Trim()
if ($targetVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "源码版本号无效：$targetVersion"
}
$targetPackageName = "升级到V$targetVersion.bat"
$candidatePackage = Join-Path $toolRoot "输出-待实机验收\$targetPackageName"
$candidateManifest = Join-Path $toolRoot "输出-待实机验收\V$targetVersion-发布清单.json"
$frozenV101Package = Join-Path $toolRoot '输出-待实机验收\升级到V1.0.1.bat'
$expectedV101Sha256 = 'cd0d52b9ffb5d2864e7ad98d8969b86376d8577391399c30295d0722d34848cd'
$expectedRuntimeTreeSha256 = 'b778df06bfc98d699c2aa4c68d4f146f8c6c3d55a0ce1cc7b6811251ed5aad14'
$frozenPythonVersion = '3.13.14'
$oldReference = Join-Path $repoRoot '02_开发工作区\Windows部署目录-V1.0.0'
$v101Reference = Join-Path $repoRoot '02_开发工作区\Windows部署目录-V1.0.1-待实机验收'
$workRoot = Join-Path $env:RUNNER_TEMP 'meeting-room-upgrade-ci'
$releaseRoot = Join-Path $workRoot 'release'
$packageRoot = Join-Path $workRoot 'packages'
$installRoot = Join-Path $workRoot 'installs'
$frozenRuntimeRoot = Join-Path $workRoot 'frozen-runtime'
$hostPython = (Get-Command python.exe -ErrorAction Stop).Source
$env:PYTHONDONTWRITEBYTECODE = '1'
$managedTopFiles = @(
    '① 启动系统.bat', '② 立即备份.bat', '③ 设置开机自动启动.bat',
    '④ 停止本次后台系统.bat', '⑤ 取消开机自动启动.bat', '使用说明.txt'
)
$managedProgramFiles = @(
    '_程序文件/app.py', '_程序文件/server.py', '_程序文件/backup.py',
    '_程序文件/migrate_check.py', '_程序文件/requirements.txt', '_程序文件/版本.txt'
)

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-NativeChecked {
    param([string]$FilePath, [string[]]$Arguments, [int[]]$AllowedExitCodes = @(0))
    $output = @(& $FilePath @Arguments)
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) { Write-Host ([string]$line) }
    if ($AllowedExitCodes -notcontains $exitCode) {
        throw "命令失败（退出码 $exitCode）：$FilePath $($Arguments -join ' ')"
    }
    return $exitCode
}

function Invoke-PythonCodeChecked {
    param(
        [string]$Python,
        [string]$Code,
        [string[]]$Arguments = @()
    )
    $variableName = 'MEETING_ROOM_CI_PYTHON_CODE'
    $hadPreviousValue = Test-Path -LiteralPath "Env:$variableName"
    $previousValue = [Environment]::GetEnvironmentVariable($variableName)
    try {
        [Environment]::SetEnvironmentVariable($variableName, $Code)
        $bootstrap = "import os; exec(os.environ['MEETING_ROOM_CI_PYTHON_CODE'])"
        $result = Invoke-NativeChecked -FilePath $Python -Arguments (@('-c', $bootstrap) + $Arguments)
        return $result
    }
    finally {
        if ($hadPreviousValue) {
            [Environment]::SetEnvironmentVariable($variableName, $previousValue)
        }
        else {
            Remove-Item -LiteralPath "Env:$variableName" -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-PythonCodeCapture {
    param(
        [string]$Python,
        [string]$Code,
        [string[]]$Arguments = @()
    )
    $variableName = 'MEETING_ROOM_CI_PYTHON_CODE'
    $hadPreviousValue = Test-Path -LiteralPath "Env:$variableName"
    $previousValue = [Environment]::GetEnvironmentVariable($variableName)
    try {
        [Environment]::SetEnvironmentVariable($variableName, $Code)
        $bootstrap = "import os; exec(os.environ['MEETING_ROOM_CI_PYTHON_CODE'])"
        $nativeArguments = @('-c', $bootstrap) + $Arguments
        $output = @(& $Python @nativeArguments)
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Python 测试代码失败（退出码 $exitCode）"
        }
        return ($output -join "`n").Trim()
    }
    finally {
        if ($hadPreviousValue) {
            [Environment]::SetEnvironmentVariable($variableName, $previousValue)
        }
        else {
            Remove-Item -LiteralPath "Env:$variableName" -ErrorAction SilentlyContinue
        }
    }
}

function Get-TreeSha256 {
    param([string]$Root)
    $code = @'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
records = []
for path in root.rglob("*"):
    if path.is_file():
        records.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
digest = hashlib.sha256()
for relative, file_hash in sorted(records):
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(file_hash.encode("ascii"))
    digest.update(b"\n")
print(digest.hexdigest())
'@
    return Invoke-PythonCodeCapture -Python $hostPython -Code $code -Arguments @($Root)
}

function Initialize-FrozenRuntime {
    if (Test-Path -LiteralPath $frozenRuntimeRoot) {
        Remove-Item -LiteralPath $frozenRuntimeRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $frozenRuntimeRoot -Force | Out-Null
    $archive = Join-Path $workRoot "python-$frozenPythonVersion-embed-amd64.zip"
    $url = "https://www.python.org/ftp/python/$frozenPythonVersion/python-$frozenPythonVersion-embed-amd64.zip"
    Write-Host "Downloading frozen Python runtime: $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $frozenRuntimeRoot -Force

    $sitePackages = Join-Path $frozenRuntimeRoot 'Lib\site-packages'
    New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
    $wheelRoot = Join-Path $workRoot 'frozen-runtime-wheels'
    New-Item -ItemType Directory -Path $wheelRoot -Force | Out-Null
    Invoke-NativeChecked -FilePath $hostPython -Arguments @(
        '-m', 'pip', 'download',
        '--disable-pip-version-check',
        '--no-deps',
        '--only-binary=:all:',
        '--dest', $wheelRoot,
        'blinker==1.9.0',
        'click==8.4.2',
        'colorama==0.4.6',
        'Flask==3.1.3',
        'importlib_metadata==9.0.0',
        'itsdangerous==2.2.0',
        'Jinja2==3.1.6',
        'MarkupSafe==3.0.3',
        'waitress==3.0.2',
        'Werkzeug==3.1.8',
        'zipp==4.1.0'
    ) | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $wheels = @(Get-ChildItem -LiteralPath $wheelRoot -File -Filter '*.whl' | Sort-Object Name)
    Assert-True ($wheels.Count -eq 11) "冻结 runtime wheel 数量错误：$($wheels.Count)"
    foreach ($wheel in $wheels) {
        # 原样解压 wheel，保留发布者提供的 LF RECORD；不让 pip install
        # 生成 INSTALLER/REQUESTED 或改写 RECORD。
        [IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, $sitePackages)
    }

    foreach ($cache in @(Get-ChildItem -LiteralPath $frozenRuntimeRoot -Directory -Filter '__pycache__' -Recurse)) {
        Remove-Item -LiteralPath $cache.FullName -Recurse -Force
    }
    foreach ($cacheFile in @(Get-ChildItem -LiteralPath $frozenRuntimeRoot -File -Filter '*.pyc' -Recurse)) {
        Remove-Item -LiteralPath $cacheFile.FullName -Force
    }
    [IO.File]::WriteAllText(
        (Join-Path $frozenRuntimeRoot 'python313._pth'),
        "python313.zip`n.`n..`nLib/site-packages`nimport site`n",
        (New-Object Text.UTF8Encoding($false))
    )

    $actual = Get-TreeSha256 -Root $frozenRuntimeRoot
    Assert-True ($actual -eq $expectedRuntimeTreeSha256) "重建的冻结 runtime 哈希不匹配：$actual"
    $runtimePython = Join-Path $frozenRuntimeRoot 'python.exe'
    Invoke-PythonCodeChecked -Python $runtimePython -Code 'import flask, waitress; print("frozen-runtime-ok")' | Out-Null
    Assert-True ((Get-TreeSha256 -Root $frozenRuntimeRoot) -eq $expectedRuntimeTreeSha256) '冻结 runtime 执行自检后发生漂移'
}

function Copy-TreeWithRobocopy {
    param([string]$Source, [string]$Destination)
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $output = @(& robocopy.exe $Source $Destination /MIR /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ /NP /NFL /NDL /NJH)
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) { Write-Host ([string]$line) }
    if ($exitCode -lt 0 -or $exitCode -gt 7) {
        throw "robocopy 失败（退出码 $exitCode）：$Source -> $Destination"
    }
}

function New-TestInstall {
    param([string]$Name)
    $root = Join-Path $installRoot $Name
    Copy-TreeWithRobocopy -Source $oldReference -Destination $root

    $runtime = Join-Path $root '_程序文件\runtime'
    Copy-TreeWithRobocopy -Source $frozenRuntimeRoot -Destination $runtime
    $runtimePython = Join-Path $runtime 'python.exe'
    $runtimePythonw = Join-Path $runtime 'pythonw.exe'
    Assert-True (Test-Path -LiteralPath $runtimePython -PathType Leaf) '测试 runtime 缺少 python.exe'
    Assert-True (Test-Path -LiteralPath $runtimePythonw -PathType Leaf) '测试 runtime 缺少 pythonw.exe'
    Assert-True ((Get-TreeSha256 -Root $runtime) -eq $expectedRuntimeTreeSha256) '测试安装的冻结 runtime 哈希不匹配'
    Invoke-PythonCodeChecked -Python $runtimePython -Code 'import flask, waitress; print("runtime-ok")' | Out-Null

    $programRoot = Join-Path $root '_程序文件'
    $env:MEETING_ROOM_INITIAL_ADMIN_PASSWORD = 'CI-Only-Admin-Password-2026'
    Push-Location $programRoot
    try {
        Invoke-PythonCodeChecked -Python $runtimePython -Code 'from app import app, init_db; app.app_context().push(); init_db()' | Out-Null
    }
    finally {
        Pop-Location
        Remove-Item Env:MEETING_ROOM_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    }

    $database = Join-Path $programRoot 'data\reservation.db'
    $seedCode = @'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
admin_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
room_id = db.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()[0]
room_name = db.execute("SELECT name FROM rooms WHERE id=?", (room_id,)).fetchone()[0]
cursor = db.execute(
    "INSERT INTO reservations (room_id, room_name_snapshot, reserve_date, start_time, end_time, user_id, party_name, case_number, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (room_id, room_name, "2099-12-31", "09:00", "10:00", admin_id, "CI保留单位", "CI-KEEP-001", "升级前测试数据"),
)
reservation_id = cursor.lastrowid
for slot in ("09:00", "09:30"):
    db.execute("INSERT INTO reservation_slots (reservation_id, room_id, reserve_date, slot_time) VALUES (?, ?, ?, ?)", (reservation_id, room_id, "2099-12-31", slot))
db.execute("INSERT INTO app_meta (key, value) VALUES ('ci_preserve_marker', 'keep-me')")
db.commit()
db.close()
'@
    Invoke-PythonCodeChecked -Python $runtimePython -Code $seedCode -Arguments @($database) | Out-Null

    return [pscustomobject]@{
        Root = $root
        ProgramRoot = $programRoot
        RuntimePython = $runtimePython
        Database = $database
    }
}

function Get-LogicalDataState {
    param($Install)
    $stateCode = @'
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
schema = db.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
state = {
    "schema_version": None if schema is None else schema[0],
    "marker": db.execute("SELECT value FROM app_meta WHERE key='ci_preserve_marker'").fetchone()[0],
    "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
    "rooms": db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
    "reservation": db.execute("SELECT party_name, case_number, notes, status FROM reservations WHERE case_number='CI-KEEP-001'").fetchone(),
    "slots": db.execute("SELECT slot_time FROM reservation_slots rs JOIN reservations r ON r.id=rs.reservation_id WHERE r.case_number='CI-KEEP-001' ORDER BY slot_time").fetchall(),
}
print(json.dumps(state, ensure_ascii=False, sort_keys=True))
db.close()
'@
    return Invoke-PythonCodeCapture -Python $Install.RuntimePython -Code $stateCode -Arguments @($Install.Database)
}

function Get-ExpectedPostUpgradeLogicalState {
    param([string]$BeforeJson)
    $expected = $BeforeJson | ConvertFrom-Json
    $expected.schema_version = $targetSchemaVersion
    return ($expected | ConvertTo-Json -Compress)
}

function Write-TestJsonUtf8NoBom {
    param([string]$Path, $Value)
    [IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 8),
        (New-Object Text.UTF8Encoding($false))
    )
}

function Get-TestFileManifest {
    param([string]$Root)
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $records = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -Force -Recurse -File | Sort-Object FullName)) {
        $full = [IO.Path]::GetFullPath($file.FullName)
        Assert-True ($full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) 'CI 快照文件越界'
        $records += [ordered]@{
            RelativePath = $full.Substring($rootFull.Length).Replace('\', '/')
            Length = [int64]$file.Length
            Sha256 = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return @($records)
}

function Copy-TestManagedProgram {
    param([string]$SourceRoot, [string]$DestinationRoot)
    foreach ($relative in @($managedTopFiles + $managedProgramFiles)) {
        $source = Join-Path $SourceRoot $relative.Replace('/', '\')
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            $destination = Join-Path $DestinationRoot $relative.Replace('/', '\')
            $parent = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
    }
    foreach ($folder in @('static', 'templates')) {
        $source = Join-Path $SourceRoot (Join-Path '_程序文件' $folder)
        $destination = Join-Path $DestinationRoot (Join-Path '_程序文件' $folder)
        Copy-TreeWithRobocopy -Source $source -Destination $destination
    }
}

function New-LegacyV101Snapshot {
    param($Install, [string]$TransactionId)
    $snapshotRoot = Join-Path $Install.ProgramRoot (Join-Path '_升级回滚' $TransactionId)
    $snapshotProgram = Join-Path $snapshotRoot 'program'
    $snapshotData = Join-Path $snapshotRoot 'data'
    New-Item -ItemType Directory -Path $snapshotProgram -Force | Out-Null
    New-Item -ItemType Directory -Path $snapshotData -Force | Out-Null

    $existence = @()
    foreach ($relative in @($managedTopFiles + $managedProgramFiles)) {
        $existence += [ordered]@{
            RelativePath = $relative
            Kind = 'File'
            Existed = [bool](Test-Path -LiteralPath (Join-Path $Install.Root $relative.Replace('/', '\')) -PathType Leaf)
        }
    }
    foreach ($relative in @('_程序文件/static', '_程序文件/templates')) {
        $existence += [ordered]@{
            RelativePath = $relative
            Kind = 'Directory'
            Existed = [bool](Test-Path -LiteralPath (Join-Path $Install.Root $relative.Replace('/', '\')) -PathType Container)
        }
    }
    Copy-TestManagedProgram -SourceRoot $Install.Root -DestinationRoot $snapshotProgram
    Copy-TreeWithRobocopy -Source (Join-Path $Install.ProgramRoot 'data') -Destination $snapshotData
    Write-TestJsonUtf8NoBom -Path (Join-Path $snapshotRoot 'program-manifest.json') -Value ([ordered]@{
        Existence = @($existence)
        Files = @(Get-TestFileManifest -Root $snapshotProgram)
    })
    Write-TestJsonUtf8NoBom -Path (Join-Path $snapshotRoot 'data-manifest.json') -Value ([ordered]@{
        Files = @(Get-TestFileManifest -Root $snapshotData)
    })
    return $snapshotRoot
}

function Write-LegacyV101State {
    param(
        $Install,
        [string]$TransactionId,
        $SnapshotPath,
        [string]$Stage,
        [bool]$WasRunning = $false,
        [bool]$TaskExists = $false
    )
    $state = [ordered]@{
        TransactionId = $TransactionId
        PackageVersion = '1.0.1'
        SnapshotPath = $SnapshotPath
        Stage = $Stage
        OriginalVersion = '1.0.0'
        OriginalVersionExisted = $false
        WasRunning = $WasRunning
        TaskExists = $TaskExists
    }
    Write-TestJsonUtf8NoBom -Path (Join-Path $Install.ProgramRoot '_升级状态.json') -Value $state
}

function Set-AppMetaValue {
    param($Install, [string]$Key, [string]$Value)
    $code = @'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute(
    "INSERT INTO app_meta (key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    (sys.argv[2], sys.argv[3]),
)
db.commit()
db.close()
'@
    Invoke-PythonCodeChecked -Python $Install.RuntimePython -Code $code -Arguments @($Install.Database, $Key, $Value) | Out-Null
}

function Get-AppMetaValue {
    param($Install, [string]$Key)
    $code = @'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
row = db.execute("SELECT value FROM app_meta WHERE key=?", (sys.argv[2],)).fetchone()
print("" if row is None else row[0])
db.close()
'@
    return Invoke-PythonCodeCapture -Python $Install.RuntimePython -Code $code -Arguments @($Install.Database, $Key)
}

function Invoke-UpgradeBat {
    param([string]$BatPath, $Broker = $null)
    # 空行供 BAT 末尾 pause 使用；runner 进程本身已具管理员权限，不走交互式 UAC。
    if ($null -ne $Broker) {
        $command = 'call "{0}" --upgrade-broker "{1}" "{2}" "{3}"' -f @(
            $BatPath, $Broker.Request, $Broker.Response, $Broker.Token
        )
    }
    else {
        $command = 'call "{0}"' -f $BatPath
    }
    '' | & $env:ComSpec /d /c $command | Out-Host
    $exitCode = $LASTEXITCODE
    return [int]$exitCode
}

function New-V101TestInstall {
    param([string]$Name)
    $install = New-TestInstall -Name $Name
    $upgradeBat = Join-Path $install.Root '升级到V1.0.1.bat'
    Copy-Item -LiteralPath $frozenV101Package -Destination $upgradeBat
    $exitCode = Invoke-UpgradeBat -BatPath $upgradeBat
    Assert-True ($exitCode -eq 0) "准备 V1.0.1 测试起点失败，退出码 $exitCode"
    Assert-True (
        (Get-Content -LiteralPath (Join-Path $install.ProgramRoot '版本.txt') -Raw).Trim() -eq '1.0.1'
    ) 'V1.0.1 测试起点版本不正确'
    return $install
}

function Assert-NoOpenTransaction {
    param($Install)
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Install.ProgramRoot '_升级状态.json'))) '升级状态文件不应残留'
}

function Remove-TestScheduledTask {
    $task = Get-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Stop-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -Confirm:$false
    }
}

function Register-OwnedTestTask {
    param($Install, [switch]$LegacyWithoutWorkingDirectory)
    Remove-TestScheduledTask
    $pythonw = Join-Path $Install.ProgramRoot 'runtime\pythonw.exe'
    $server = Join-Path $Install.ProgramRoot 'server.py'
    if ($LegacyWithoutWorkingDirectory) {
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $server)
    }
    else {
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $server) -WorkingDirectory $Install.ProgramRoot
    }
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $trigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal | Out-Null
}

function Wait-MeetingRoomHttp {
    param([int]$Seconds = 30)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8080/login' -TimeoutSec 2
            if ([string]$response.Headers['X-Meeting-Room-System'] -eq '1') { return }
        }
        catch {}
        Start-Sleep -Milliseconds 500
    }
    throw '测试会议室服务未能启动'
}

function Stop-TestInstallProcesses {
    param($Install)
    $runtime = [IO.Path]::GetFullPath((Join-Path $Install.ProgramRoot 'runtime')).TrimEnd('\') + '\'
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")) {
        if ($process.ExecutablePath) {
            $executable = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
            if ($executable.StartsWith($runtime, [StringComparison]::OrdinalIgnoreCase)) {
                Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Start-TestUpgradeBroker {
    $brokerRoot = Join-Path $workRoot ('ci broker ' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $brokerRoot -Force | Out-Null
    $request = Join-Path $brokerRoot 'request.json'
    $response = Join-Path $brokerRoot 'response.json'
    $token = [Guid]::NewGuid().ToString('N')
    $job = Start-Job -ArgumentList @($request, $response, $token) -ScriptBlock {
        param($RequestPath, $ResponsePath, $Token)
        Set-StrictMode -Version Latest
        $ErrorActionPreference = 'Stop'
        $encoding = New-Object Text.UTF8Encoding($false)
        $deadline = (Get-Date).AddMinutes(3)
        while ((Get-Date) -lt $deadline) {
            if (Test-Path -LiteralPath $RequestPath -PathType Leaf) {
                $launched = $null
                $launchedId = 0
                try {
                    $jobRequest = (Get-Content -LiteralPath $RequestPath -Raw) | ConvertFrom-Json
                    if (-not [string]::Equals([string]$jobRequest.token, $Token, [StringComparison]::Ordinal)) {
                        throw 'CI broker token mismatch'
                    }
                    $info = New-Object Diagnostics.ProcessStartInfo
                    $info.FileName = [string]$jobRequest.python_path
                    $info.Arguments = '"{0}"' -f [string]$jobRequest.server_path
                    $info.WorkingDirectory = [string]$jobRequest.working_directory
                    $info.UseShellExecute = $true
                    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Minimized
                    $launched = New-Object Diagnostics.Process
                    $launched.StartInfo = $info
                    if (-not $launched.Start()) { throw 'CI broker could not start the service process' }
                    $launchedId = [int]$launched.Id
                    if ($launchedId -le 0) { throw 'CI broker did not receive a valid service process ID' }
                    $reply = [ordered]@{ schema = 1; token = $Token; ok = $true; process_id = $launchedId; error = $null }
                }
                catch {
                    if ($launchedId -gt 0) {
                        Stop-Process -Id $launchedId -Force -ErrorAction SilentlyContinue
                        $launchedId = 0
                    }
                    $reply = [ordered]@{ schema = 1; token = $Token; ok = $false; process_id = 0; error = [string]$_.Exception.Message }
                }
                $temporary = "$ResponsePath.tmp.$PID"
                try {
                    [IO.File]::WriteAllText($temporary, ($reply | ConvertTo-Json -Compress), $encoding)
                    [IO.File]::Move($temporary, $ResponsePath)
                }
                catch {
                    if ($launchedId -gt 0) {
                        Stop-Process -Id $launchedId -Force -ErrorAction SilentlyContinue
                    }
                    throw
                }
                finally {
                    if ($null -ne $launched) { $launched.Dispose() }
                }
                Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Milliseconds 100
        }
    }
    return [pscustomobject]@{
        Root = $brokerRoot
        Job = $job
        Request = $request
        Response = $response
        Token = $token
    }
}

function Stop-TestUpgradeBroker {
    param($Broker)
    Stop-Job -Job $Broker.Job -ErrorAction SilentlyContinue
    $brokerErrors = @(
        foreach ($childJob in @($Broker.Job.ChildJobs)) {
            foreach ($errorRecord in @($childJob.Error)) { $errorRecord }
        }
    )
    $brokerOutput = @(Receive-Job -Job $Broker.Job -ErrorAction SilentlyContinue)
    foreach ($line in $brokerOutput) {
        if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
            Write-Host ("CI broker: {0}" -f [string]$line)
        }
    }
    foreach ($errorRecord in $brokerErrors) {
        Write-Warning ("CI broker error: {0}" -f [string]$errorRecord)
    }
    Remove-Job -Job $Broker.Job -Force -ErrorAction SilentlyContinue
    foreach ($name in @(
        'MEETING_ROOM_UPGRADE_BROKER_REQUEST',
        'MEETING_ROOM_UPGRADE_BROKER_RESPONSE',
        'MEETING_ROOM_UPGRADE_BROKER_TOKEN'
    )) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $Broker.Root) {
        Remove-Item -LiteralPath $Broker.Root -Recurse -Force
    }
}

if (Test-Path -LiteralPath $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Initialize-FrozenRuntime
Remove-TestScheduledTask

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
Assert-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'GitHub Windows runner 不是管理员，无法验证真实 BAT 主路径'

$v101Sha256 = (Get-FileHash -LiteralPath $frozenV101Package -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-True ($v101Sha256 -eq $expectedV101Sha256) "V1.0.1 冻结 BAT SHA-256 不匹配：$v101Sha256"

Invoke-NativeChecked -FilePath $hostPython -Arguments @(
    (Join-Path $toolRoot '准备发布.py'),
    '--release-root',
    $releaseRoot,
    '--package-only'
) | Out-Null

$preparedRelease = Join-Path $releaseRoot "V$targetVersion"
$payloadRoot = Join-Path $preparedRelease '完整累计负载'
$preparedManifestPath = Join-Path $preparedRelease '发布清单.json'
Assert-True (Test-Path -LiteralPath $candidatePackage -PathType Leaf) "仓库缺少待验收的 $targetPackageName"
Assert-True (Test-Path -LiteralPath $candidateManifest -PathType Leaf) "仓库缺少 V$targetVersion 发布清单"
$candidateManifestData = Get-Content -LiteralPath $candidateManifest -Raw | ConvertFrom-Json
$preparedManifestData = Get-Content -LiteralPath $preparedManifestPath -Raw | ConvertFrom-Json
Assert-True ([string]$candidateManifestData.version -eq $targetVersion) '发布清单版本与源码不一致'
Assert-True (
    [string]$candidateManifestData.source_tree_sha256 -eq [string]$preparedManifestData.source_tree_sha256
) '发布清单对应的源码树与当前源码不一致'
$candidateSha256 = (Get-FileHash -LiteralPath $candidatePackage -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-True (
    $candidateSha256 -eq [string]$candidateManifestData.package_sha256
) "V$targetVersion 候选 BAT SHA-256 与发布清单不一致：$candidateSha256"
$targetSchemaVersion = [string]$candidateManifestData.database_schema_version

$verifyCandidateCode = @'
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import 制作升级包 as builder
payload = pathlib.Path(sys.argv[2])
version = sys.argv[3]
package = pathlib.Path(sys.argv[4])
manifest = json.loads(pathlib.Path(sys.argv[5]).read_text(encoding="utf-8"))
files = builder.collect_payload(payload, version)
package_bytes = package.read_bytes()
zip_bytes = builder.verify_package_bytes(
    package_bytes,
    files,
    manifest["payload_zip_sha256"],
)
records = {item["path"]: item for item in manifest["payload_files"]}
if manifest["payload_file_count"] != len(files) or set(records) != set(files):
    raise SystemExit("manifest payload file set mismatch")
for path, content in files.items():
    record = records[path]
    if record["size"] != len(content):
        raise SystemExit(f"manifest payload size mismatch: {path}")
    if record["sha256"] != hashlib.sha256(content).hexdigest():
        raise SystemExit(f"manifest payload hash mismatch: {path}")
if manifest["payload_zip_size"] != len(zip_bytes):
    raise SystemExit("manifest ZIP size mismatch")
if manifest["package_size"] != len(package_bytes):
    raise SystemExit("manifest package size mismatch")
stub_text = builder._load_template(
    pathlib.Path(sys.argv[1]) / "bat头部模板.bat",
    "BAT 头部模板",
)
powershell_text = builder._load_template(
    pathlib.Path(sys.argv[1]) / "升级主逻辑.ps1",
    "PowerShell 主逻辑模板",
)
rendered, rendered_zip_sha256, expected_stub, expected_powershell = (
    builder.render_package(stub_text, powershell_text, version, zip_bytes)
)
if rendered_zip_sha256 != manifest["payload_zip_sha256"]:
    raise SystemExit("rendered ZIP hash mismatch")
builder.verify_package_bytes(
    package_bytes,
    files,
    manifest["payload_zip_sha256"],
    expected_stub,
    expected_powershell,
)
if rendered != package_bytes:
    raise SystemExit("candidate package does not match current upgrade templates")
print("candidate-payload-match")
'@
Invoke-PythonCodeChecked -Python $hostPython -Code $verifyCandidateCode -Arguments @(
    $toolRoot,
    $payloadRoot,
    $targetVersion,
    $candidatePackage,
    $candidateManifest
) | Out-Null

$goodPackage = Join-Path $packageRoot $targetPackageName
Copy-Item -LiteralPath $candidatePackage -Destination $goodPackage

$brokenPayload = Join-Path $workRoot 'broken-payload'
Copy-TreeWithRobocopy -Source $payloadRoot -Destination $brokenPayload
$brokenApp = Join-Path $brokenPayload '_程序文件\app.py'
[IO.File]::AppendAllText($brokenApp, "`nthis is deliberately invalid python !!!`n", (New-Object Text.UTF8Encoding($false)))
$brokenPackage = Join-Path $packageRoot "升级到V$targetVersion-故障回滚测试.bat"
Invoke-NativeChecked -FilePath $hostPython -Arguments @(
    (Join-Path $toolRoot '制作升级包.py'),
    $brokenPayload,
    $targetVersion,
    '--out',
    $brokenPackage
) | Out-Null

Write-Host "=== Windows V1.0.0 -> V$targetVersion success and idempotency path ==="
$successInstall = New-TestInstall -Name 'success\会议室预约系统'
$successStateBefore = Get-LogicalDataState -Install $successInstall
$successSecretBefore = (Get-FileHash -LiteralPath (Join-Path $successInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
$successBat = Join-Path $successInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $successBat

$successExit = Invoke-UpgradeBat -BatPath $successBat
Assert-True ($successExit -eq 0) "成功升级返回了退出码 $successExit"
Assert-True (
    (Get-Content -LiteralPath (Join-Path $successInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq $targetVersion
) "版本.txt 未提交为 $targetVersion"
Assert-NoOpenTransaction -Install $successInstall
$successStateAfter = Get-LogicalDataState -Install $successInstall
$successExpectedJson = Get-ExpectedPostUpgradeLogicalState -BeforeJson $successStateBefore
$successActualJson = ($successStateAfter | ConvertFrom-Json) | ConvertTo-Json -Compress
Assert-True ($successActualJson -eq $successExpectedJson) '成功升级后逻辑数据发生变化'
$successSecretAfter = (Get-FileHash -LiteralPath (Join-Path $successInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
Assert-True ($successSecretAfter -eq $successSecretBefore) '成功升级后会话密钥发生变化'
$successSnapshots = @(
    Get-ChildItem -LiteralPath (Join-Path $successInstall.ProgramRoot '_升级回滚') -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^[0-9a-fA-F]{32}$' }
)
Assert-True ($successSnapshots.Count -eq 0) '成功升级后不应残留完整事务快照'
$successBackups = @(Get-ChildItem -LiteralPath (Join-Path $successInstall.ProgramRoot 'backups') -Filter 'reservation_before_upgrade_*.db')
Assert-True ($successBackups.Count -eq 1) '成功升级后应保留一份标准升级前数据库备份'

$secondExit = Invoke-UpgradeBat -BatPath $successBat
Assert-True ($secondExit -eq 0) "重复运行返回了退出码 $secondExit"
Assert-NoOpenTransaction -Install $successInstall
Assert-True ((Get-LogicalDataState -Install $successInstall) -eq $successStateAfter) '重复运行改变了数据'

Write-Host "=== Windows V1.0.1 -> V$targetVersion success and idempotency path ==="
$v101SuccessInstall = New-V101TestInstall -Name 'success-from-v101\会议室预约系统'
$v101SuccessStateBefore = Get-LogicalDataState -Install $v101SuccessInstall
$v101SuccessSecretBefore = (Get-FileHash -LiteralPath (Join-Path $v101SuccessInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
$v101SuccessBat = Join-Path $v101SuccessInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $v101SuccessBat

$v101SuccessExit = Invoke-UpgradeBat -BatPath $v101SuccessBat
Assert-True ($v101SuccessExit -eq 0) "V1.0.1 起点升级返回了退出码 $v101SuccessExit"
Assert-True (
    (Get-Content -LiteralPath (Join-Path $v101SuccessInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq $targetVersion
) "V1.0.1 起点升级后版本不是 $targetVersion"
Assert-NoOpenTransaction -Install $v101SuccessInstall
$v101SuccessStateAfter = Get-LogicalDataState -Install $v101SuccessInstall
Assert-True ($v101SuccessStateAfter -eq $v101SuccessStateBefore) 'V1.0.1 起点升级后逻辑数据发生变化'
$v101SuccessSecretAfter = (Get-FileHash -LiteralPath (Join-Path $v101SuccessInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
Assert-True ($v101SuccessSecretAfter -eq $v101SuccessSecretBefore) 'V1.0.1 起点升级后会话密钥发生变化'

$v101SecondExit = Invoke-UpgradeBat -BatPath $v101SuccessBat
Assert-True ($v101SecondExit -eq 0) "V1.0.1 起点升级后重复运行返回了退出码 $v101SecondExit"
Assert-NoOpenTransaction -Install $v101SuccessInstall
Assert-True ((Get-LogicalDataState -Install $v101SuccessInstall) -eq $v101SuccessStateAfter) 'V1.0.1 起点升级后重复运行改变了数据'

Write-Host '=== Windows frozen V1.0.1 legacy preparing-state normalization ==='
$legacyPreparingInstall = New-TestInstall -Name 'legacy-v101-preparing\会议室预约系统'
$legacyPreparingBefore = Get-LogicalDataState -Install $legacyPreparingInstall
$legacyPreparingTransaction = [Guid]::NewGuid().ToString('N')
Write-LegacyV101State -Install $legacyPreparingInstall `
    -TransactionId $legacyPreparingTransaction -SnapshotPath $null -Stage 'preparing'
$legacyPreparingBat = Join-Path $legacyPreparingInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $legacyPreparingBat
$legacyPreparingExit = Invoke-UpgradeBat -BatPath $legacyPreparingBat
Assert-True ($legacyPreparingExit -eq 0) "V1.0.1 preparing 遗留状态恢复返回了退出码 $legacyPreparingExit"
Assert-NoOpenTransaction -Install $legacyPreparingInstall
Assert-True (
    (Get-Content -LiteralPath (Join-Path $legacyPreparingInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq $targetVersion
) 'V1.0.1 preparing 遗留状态恢复后未继续升级到目标版本'
$legacyPreparingActual = (Get-LogicalDataState -Install $legacyPreparingInstall | ConvertFrom-Json) | ConvertTo-Json -Compress
Assert-True (
    $legacyPreparingActual -eq (Get-ExpectedPostUpgradeLogicalState -BeforeJson $legacyPreparingBefore)
) 'V1.0.1 preparing 遗留状态恢复改变了逻辑数据'

Write-Host '=== Windows frozen V1.0.1 legacy snapshot rollback normalization ==='
$legacyRollbackInstall = New-TestInstall -Name 'legacy-v101-snapshot-rollback\会议室预约系统'
$legacyRollbackBefore = Get-LogicalDataState -Install $legacyRollbackInstall
$legacyRollbackTransaction = [Guid]::NewGuid().ToString('N')
$legacyRollbackSnapshot = New-LegacyV101Snapshot `
    -Install $legacyRollbackInstall -TransactionId $legacyRollbackTransaction
Copy-TestManagedProgram -SourceRoot $v101Reference -DestinationRoot $legacyRollbackInstall.Root
Set-AppMetaValue -Install $legacyRollbackInstall -Key 'ci_preserve_marker' -Value 'legacy-migration-transient'
Set-AppMetaValue -Install $legacyRollbackInstall -Key 'ci_legacy_transient' -Value 'must-be-rolled-back'
Write-LegacyV101State -Install $legacyRollbackInstall `
    -TransactionId $legacyRollbackTransaction -SnapshotPath $legacyRollbackSnapshot `
    -Stage 'migration_complete'
$legacyRollbackBat = Join-Path $legacyRollbackInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $legacyRollbackBat
$legacyRollbackExit = Invoke-UpgradeBat -BatPath $legacyRollbackBat
Assert-True ($legacyRollbackExit -eq 0) "V1.0.1 未提交快照回滚返回了退出码 $legacyRollbackExit"
Assert-NoOpenTransaction -Install $legacyRollbackInstall
$legacyRollbackActual = (Get-LogicalDataState -Install $legacyRollbackInstall | ConvertFrom-Json) | ConvertTo-Json -Compress
Assert-True (
    $legacyRollbackActual -eq (Get-ExpectedPostUpgradeLogicalState -BeforeJson $legacyRollbackBefore)
) 'V1.0.1 未提交快照回滚没有恢复升级前逻辑数据'
Assert-True (
    (Get-AppMetaValue -Install $legacyRollbackInstall -Key 'ci_legacy_transient') -eq ''
) 'V1.0.1 未提交快照回滚保留了迁移后的瞬时数据'
Assert-True (Test-Path -LiteralPath $legacyRollbackSnapshot -PathType Container) 'V1.0.1 失败快照证据未保留'

Write-Host '=== Windows frozen V1.0.1 legacy committed-state handoff ==='
$legacyCommittedInstall = New-TestInstall -Name 'legacy-v101-committed\会议室预约系统'
$legacyCommittedTransaction = [Guid]::NewGuid().ToString('N')
$legacyCommittedSnapshot = New-LegacyV101Snapshot `
    -Install $legacyCommittedInstall -TransactionId $legacyCommittedTransaction
$legacyFrozenBat = Join-Path $legacyCommittedInstall.Root '升级到V1.0.1.bat'
Copy-Item -LiteralPath $frozenV101Package -Destination $legacyFrozenBat
$legacyFrozenExit = Invoke-UpgradeBat -BatPath $legacyFrozenBat
Assert-True ($legacyFrozenExit -eq 0) "构造 V1.0.1 已提交起点失败，退出码 $legacyFrozenExit"
Set-AppMetaValue -Install $legacyCommittedInstall `
    -Key 'ci_legacy_post_commit' -Value 'must-survive-v101-committed-handoff'
Write-LegacyV101State -Install $legacyCommittedInstall `
    -TransactionId $legacyCommittedTransaction -SnapshotPath $legacyCommittedSnapshot `
    -Stage 'version_committed'
# 冻结旧状态记录 TaskExists=false；提交后用户新建了合法任务。已提交收尾
# 必须忽略这项历史差异，随后 V1.0.2 再按当前真实状态采样并保留任务。
Register-OwnedTestTask -Install $legacyCommittedInstall
$legacyCommittedBat = Join-Path $legacyCommittedInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $legacyCommittedBat
$legacyCommittedExit = Invoke-UpgradeBat -BatPath $legacyCommittedBat
Assert-True ($legacyCommittedExit -eq 0) "V1.0.1 已提交遗留状态收尾返回了退出码 $legacyCommittedExit"
Assert-NoOpenTransaction -Install $legacyCommittedInstall
Assert-True (
    (Get-AppMetaValue -Install $legacyCommittedInstall -Key 'ci_legacy_post_commit') -eq 'must-survive-v101-committed-handoff'
) 'V1.0.1 已提交遗留状态错误回滚了提交后数据'
Assert-True (
    (Test-Path -LiteralPath $legacyCommittedSnapshot -PathType Container)
) 'V1.0.1 已提交遗留事务的旧完整快照没有保留'
$legacyCommittedTaskAfter = Get-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\'
Assert-True ([bool]$legacyCommittedTaskAfter.Settings.Enabled) 'V1.0.1 已提交后新建的合法任务没有被 V1.0.2 保留'
Assert-True ([string]$legacyCommittedTaskAfter.State -ne 'Running') 'V1.0.1 已提交后新建的静止任务被错误启动'
Assert-True (
    (Get-Content -LiteralPath (Join-Path $legacyCommittedInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq $targetVersion
) 'V1.0.1 已提交遗留状态收尾后未继续升级到目标版本'
Remove-TestScheduledTask

Write-Host "=== Windows running service and enabled owned task restoration ==="
$runningInstall = New-TestInstall -Name 'running-task\会议室预约系统'
$runningStateBefore = Get-LogicalDataState -Install $runningInstall
$runningBat = Join-Path $runningInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $runningBat
try {
    Register-OwnedTestTask -Install $runningInstall -LegacyWithoutWorkingDirectory
    Start-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\'
    Wait-MeetingRoomHttp
    $runningExit = Invoke-UpgradeBat -BatPath $runningBat
    Assert-True ($runningExit -eq 0) "运行中系统升级返回了退出码 $runningExit"
    Wait-MeetingRoomHttp
    $restoredTask = Get-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\'
    Assert-True ([bool]$restoredTask.Settings.Enabled) '升级后原本启用的计划任务没有恢复启用'
    Assert-True ([string]$restoredTask.State -eq 'Running') '升级后原本运行的计划任务没有恢复运行'
    $runningActualJson = (Get-LogicalDataState -Install $runningInstall | ConvertFrom-Json) | ConvertTo-Json -Compress
    Assert-True (
        $runningActualJson -eq (Get-ExpectedPostUpgradeLogicalState -BeforeJson $runningStateBefore)
    ) '运行中升级改变了逻辑数据'
    Assert-NoOpenTransaction -Install $runningInstall
    $runningSnapshots = @(
        Get-ChildItem -LiteralPath (Join-Path $runningInstall.ProgramRoot '_升级回滚') -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^[0-9a-fA-F]{32}$' }
    )
    Assert-True ($runningSnapshots.Count -eq 0) '运行中成功升级后残留事务快照'
}
finally {
    Remove-TestScheduledTask
    Stop-TestInstallProcesses -Install $runningInstall
}

Write-Host "=== Windows legacy manual BAT running state and parent broker restoration ==="
$manualInstall = New-TestInstall -Name 'manual running\会议室预约系统'
$manualStateBefore = Get-LogicalDataState -Install $manualInstall
$manualBat = Join-Path $manualInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $manualBat
$legacyStartBat = Join-Path $manualInstall.Root '① 启动系统.bat'
Register-OwnedTestTask -Install $manualInstall -LegacyWithoutWorkingDirectory
$legacyLauncher = Start-Process -FilePath $legacyStartBat -WorkingDirectory $manualInstall.Root -PassThru -WindowStyle Hidden
$testBroker = $null
try {
    Wait-MeetingRoomHttp
    $testBroker = Start-TestUpgradeBroker
    $manualExit = Invoke-UpgradeBat -BatPath $manualBat -Broker $testBroker
    Assert-True ($manualExit -eq 0) "手动运行系统升级返回了退出码 $manualExit"
    Wait-MeetingRoomHttp
    $manualActualJson = (Get-LogicalDataState -Install $manualInstall | ConvertFrom-Json) | ConvertTo-Json -Compress
    Assert-True (
        $manualActualJson -eq (Get-ExpectedPostUpgradeLogicalState -BeforeJson $manualStateBefore)
    ) '手动运行升级改变了逻辑数据'
    Assert-NoOpenTransaction -Install $manualInstall
    $manualTaskAfter = Get-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\'
    Assert-True ([bool]$manualTaskAfter.Settings.Enabled) '手动运行升级后计划任务启用状态发生变化'
    Assert-True ([string]$manualTaskAfter.State -ne 'Running') '手动运行升级错误改用 SYSTEM 计划任务启动'
}
finally {
    Remove-TestScheduledTask
    Stop-TestInstallProcesses -Install $manualInstall
    Stop-Process -Id $legacyLauncher.Id -Force -ErrorAction SilentlyContinue
    if ($null -ne $testBroker) { Stop-TestUpgradeBroker -Broker $testBroker }
}

Write-Host '=== Windows already-elevated manual service restoration without broker ==='
$directAdminInstall = New-TestInstall -Name 'direct admin manual\会议室预约系统'
$directAdminBefore = Get-LogicalDataState -Install $directAdminInstall
$directAdminBat = Join-Path $directAdminInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $directAdminBat
$directAdminStartBat = Join-Path $directAdminInstall.Root '① 启动系统.bat'
$directAdminLauncher = Start-Process -FilePath $directAdminStartBat `
    -WorkingDirectory $directAdminInstall.Root -PassThru -WindowStyle Hidden
try {
    Wait-MeetingRoomHttp
    # GitHub runner 本身就是完整管理员令牌，且不传 broker 参数，覆盖
    # UAC 关闭/内置 Administrator 直接双击的真实分支。
    $directAdminExit = Invoke-UpgradeBat -BatPath $directAdminBat
    Assert-True ($directAdminExit -eq 0) "初始即管理员且无 broker 的升级返回了退出码 $directAdminExit"
    Wait-MeetingRoomHttp
    $directAdminActual = (Get-LogicalDataState -Install $directAdminInstall | ConvertFrom-Json) | ConvertTo-Json -Compress
    Assert-True (
        $directAdminActual -eq (Get-ExpectedPostUpgradeLogicalState -BeforeJson $directAdminBefore)
    ) '初始即管理员且无 broker 的升级改变了逻辑数据'
    Assert-NoOpenTransaction -Install $directAdminInstall
    Assert-True (
        $null -eq (Get-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -ErrorAction SilentlyContinue)
    ) '初始即管理员且无 broker 的升级意外创建了计划任务'
}
finally {
    Stop-TestInstallProcesses -Install $directAdminInstall
    Stop-Process -Id $directAdminLauncher.Id -Force -ErrorAction SilentlyContinue
}

Write-Host '=== Windows wrong scheduled-task ownership rejection ==='
$wrongTaskInstall = New-TestInstall -Name 'wrong-task\会议室预约系统'
$wrongTaskDatabaseHash = (Get-FileHash -LiteralPath $wrongTaskInstall.Database -Algorithm SHA256).Hash
$wrongTaskBat = Join-Path $wrongTaskInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $wrongTaskBat
try {
    $wrongAction = New-ScheduledTaskAction -Execute $hostPython -Argument '-c "import time; time.sleep(30)"'
    $wrongPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $wrongTrigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -Action $wrongAction -Trigger $wrongTrigger -Principal $wrongPrincipal | Out-Null
    $wrongTaskExit = Invoke-UpgradeBat -BatPath $wrongTaskBat
    Assert-True ($wrongTaskExit -eq 1) "错误任务归属应返回 1，实际为 $wrongTaskExit"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $wrongTaskInstall.ProgramRoot '版本.txt'))) '错误任务归属仍提交了版本'
    Assert-NoOpenTransaction -Install $wrongTaskInstall
    Assert-True (
        (Get-FileHash -LiteralPath $wrongTaskInstall.Database -Algorithm SHA256).Hash -eq $wrongTaskDatabaseHash
    ) '错误任务归属拒绝时数据库发生变化'
}
finally {
    Remove-TestScheduledTask
}

Write-Host '=== Windows occupied port rejection without cross-instance health ==='
$portConflictInstall = New-TestInstall -Name 'port-conflict\会议室预约系统'
$portConflictDatabaseHash = (Get-FileHash -LiteralPath $portConflictInstall.Database -Algorithm SHA256).Hash
$portConflictBat = Join-Path $portConflictInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $portConflictBat
$blockerScript = Join-Path $workRoot 'port-blocker.py'
[IO.File]::WriteAllText(
    $blockerScript,
    "import socket, time`ns=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('127.0.0.1',8080)); s.listen(); time.sleep(120)`n",
    (New-Object Text.UTF8Encoding($false))
)
$blocker = Start-Process -FilePath $hostPython -ArgumentList @($blockerScript) -PassThru -WindowStyle Hidden
try {
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline -and
        @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue |
            Where-Object { [int]$_.OwningProcess -eq $blocker.Id }).Count -eq 0) {
        Start-Sleep -Milliseconds 250
    }
    Assert-True (
        @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue |
            Where-Object { [int]$_.OwningProcess -eq $blocker.Id }).Count -gt 0
    ) '端口占用测试进程没有取得 8080'
    $portConflictExit = Invoke-UpgradeBat -BatPath $portConflictBat
    Assert-True ($portConflictExit -eq 1) "端口串台拒绝应返回 1，实际为 $portConflictExit"
    Assert-NoOpenTransaction -Install $portConflictInstall
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $portConflictInstall.ProgramRoot '版本.txt'))) '端口冲突仍提交了版本'
    Assert-True (
        (Get-FileHash -LiteralPath $portConflictInstall.Database -Algorithm SHA256).Hash -eq $portConflictDatabaseHash
    ) '端口冲突拒绝时数据库发生变化'
}
finally {
    Stop-Process -Id $blocker.Id -Force -ErrorAction SilentlyContinue
}

Write-Host '=== Windows concurrent double-click lock rejection ==='
$lockedInstall = New-TestInstall -Name 'locked\会议室预约系统'
$lockedDatabaseHash = (Get-FileHash -LiteralPath $lockedInstall.Database -Algorithm SHA256).Hash
$lockedBat = Join-Path $lockedInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $lockedBat
$lockPath = Join-Path $lockedInstall.ProgramRoot '_升级锁'
$heldLock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    $lockedExit = Invoke-UpgradeBat -BatPath $lockedBat
    Assert-True ($lockedExit -eq 4) "并发升级锁应返回 4，实际为 $lockedExit"
    Assert-NoOpenTransaction -Install $lockedInstall
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $lockedInstall.ProgramRoot '版本.txt'))) '并发锁拒绝仍提交了版本'
    Assert-True (
        (Get-FileHash -LiteralPath $lockedInstall.Database -Algorithm SHA256).Hash -eq $lockedDatabaseHash
    ) '并发锁拒绝时数据库发生变化'
}
finally {
    $heldLock.Dispose()
}

Write-Host '=== Windows tampered runtime rejected before installed Python execution ==='
$runtimeTamperInstall = New-TestInstall -Name 'runtime-tamper\会议室预约系统'
$runtimeTamperDatabaseHash = (Get-FileHash -LiteralPath $runtimeTamperInstall.Database -Algorithm SHA256).Hash
$runtimeTamperBat = Join-Path $runtimeTamperInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $runtimeTamperBat
$runtimeArchive = Join-Path $runtimeTamperInstall.ProgramRoot 'runtime\python313.zip'
[IO.File]::AppendAllText($runtimeArchive, 'tamper', (New-Object Text.UTF8Encoding($false)))
$runtimeTamperExit = Invoke-UpgradeBat -BatPath $runtimeTamperBat
Assert-True ($runtimeTamperExit -eq 1) "runtime 篡改应返回 1，实际为 $runtimeTamperExit"
Assert-NoOpenTransaction -Install $runtimeTamperInstall
Assert-True (-not (Test-Path -LiteralPath (Join-Path $runtimeTamperInstall.ProgramRoot '版本.txt'))) 'runtime 篡改仍提交了版本'
Assert-True (
    (Get-FileHash -LiteralPath $runtimeTamperInstall.Database -Algorithm SHA256).Hash -eq $runtimeTamperDatabaseHash
) 'runtime 篡改拒绝时数据库发生变化'

Write-Host '=== Windows committed-state cleanup must preserve newer data ==='
$postCommitKey = 'ci_after_version_commit'
$postCommitValue = 'must-survive-committed-cleanup'
Set-AppMetaValue -Install $successInstall -Key $postCommitKey -Value $postCommitValue
$rollbackRoot = Join-Path $successInstall.ProgramRoot '_升级回滚'
$committedTransactionId = [Guid]::NewGuid().ToString('N')
$snapshot = New-Item -ItemType Directory -Path (Join-Path $rollbackRoot $committedTransactionId) -Force
$committedStatePath = Join-Path $successInstall.ProgramRoot '_升级状态.json'
$committedState = [ordered]@{
    Schema = 2
    TransactionId = $committedTransactionId
    PackageVersion = $targetVersion
    SnapshotPath = $snapshot.FullName
    BackupPath = $successBackups[0].FullName
    InstallId = (Get-Content -LiteralPath (Join-Path $successInstall.ProgramRoot 'data\install_id') -Raw).Trim()
    Stage = 'version_committed'
    OriginalVersion = '1.0.0'
    OriginalVersionExisted = $false
    OriginalInstallId = $null
    WasRunning = $false
    TaskExists = $false
    TaskEnabled = $false
    TaskWasRunning = $false
}
[IO.File]::WriteAllText(
    $committedStatePath,
    ($committedState | ConvertTo-Json -Depth 8),
    (New-Object Text.UTF8Encoding($false))
)

$committedCleanupExit = Invoke-UpgradeBat -BatPath $successBat
Assert-True ($committedCleanupExit -eq 0) "version_committed 安全收尾返回了退出码 $committedCleanupExit"
Assert-NoOpenTransaction -Install $successInstall
Assert-True ((Get-AppMetaValue -Install $successInstall -Key $postCommitKey) -eq $postCommitValue) 'version_committed 安全收尾错误回滚了升级后新增数据'
Assert-True (
    (Get-Content -LiteralPath (Join-Path $successInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq $targetVersion
) 'version_committed 安全收尾改变了已提交版本'
Assert-True (-not (Test-Path -LiteralPath $snapshot.FullName)) '已提交事务收尾后仍残留完整事务快照'

Write-Host '=== Windows Schema=2 nil and UUIDv1 install_id rejection ==='
$invalidInstallIdCases = @(
    [pscustomobject]@{ Name = 'nil'; Value = $null },
    [pscustomobject]@{ Name = 'uuid-v1'; Value = '123e4567-e89b-12d3-a456-426614174000' }
)
foreach ($invalidCase in $invalidInstallIdCases) {
    $invalidTransactionId = [Guid]::NewGuid().ToString('N')
    $invalidSnapshot = New-Item -ItemType Directory `
        -Path (Join-Path $rollbackRoot $invalidTransactionId) -Force
    $invalidState = [ordered]@{
        Schema = 2
        TransactionId = $invalidTransactionId
        PackageVersion = $targetVersion
        SnapshotPath = $invalidSnapshot.FullName
        BackupPath = $successBackups[0].FullName
        InstallId = $invalidCase.Value
        Stage = 'version_committed'
        OriginalVersion = '1.0.0'
        OriginalVersionExisted = $false
        OriginalInstallId = $null
        WasRunning = $false
        TaskExists = $false
        TaskEnabled = $false
        TaskWasRunning = $false
    }
    Write-TestJsonUtf8NoBom -Path $committedStatePath -Value $invalidState
    $invalidExit = Invoke-UpgradeBat -BatPath $successBat
    Assert-True ($invalidExit -eq 5) "无效 install_id（$($invalidCase.Name)）应返回 5，实际为 $invalidExit"
    Assert-True (Test-Path -LiteralPath $committedStatePath -PathType Leaf) "无效 install_id（$($invalidCase.Name)）的状态被错误清除"
    Assert-True (
        (Get-AppMetaValue -Install $successInstall -Key $postCommitKey) -eq $postCommitValue
    ) "无效 install_id（$($invalidCase.Name)）处理改变了现有数据"
    Remove-Item -LiteralPath $committedStatePath -Force
    Remove-Item -LiteralPath $invalidSnapshot.FullName -Recurse -Force
}

Write-Host '=== Windows failure and rollback path ==='
$failureInstall = New-TestInstall -Name 'rollback\会议室预约系统'
$failureStateBefore = Get-LogicalDataState -Install $failureInstall
$failureSecretBefore = (Get-FileHash -LiteralPath (Join-Path $failureInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
$oldAppHash = (Get-FileHash -LiteralPath (Join-Path $failureInstall.ProgramRoot 'app.py') -Algorithm SHA256).Hash
$precheck = Join-Path $payloadRoot '_程序文件\migrate_check.py'
Invoke-NativeChecked -FilePath $failureInstall.RuntimePython -Arguments @($precheck, '--precheck', $failureInstall.Database) | Out-Null
$databaseHashBefore = (Get-FileHash -LiteralPath $failureInstall.Database -Algorithm SHA256).Hash
$failureBat = Join-Path $failureInstall.Root "升级到V$targetVersion-故障回滚测试.bat"
Copy-Item -LiteralPath $brokenPackage -Destination $failureBat

$failureExit = Invoke-UpgradeBat -BatPath $failureBat
Assert-True ($failureExit -eq 1) "故障升级应返回 1，实际为 $failureExit"
Assert-NoOpenTransaction -Install $failureInstall
Assert-True (-not (Test-Path -LiteralPath (Join-Path $failureInstall.ProgramRoot '版本.txt'))) '回滚后不应残留版本.txt'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $failureInstall.ProgramRoot 'migrate_check.py'))) '回滚后不应残留 migrate_check.py'
Assert-True ((Get-FileHash -LiteralPath (Join-Path $failureInstall.ProgramRoot 'app.py') -Algorithm SHA256).Hash -eq $oldAppHash) '回滚后 app.py 未恢复'
Assert-True ((Get-LogicalDataState -Install $failureInstall) -eq $failureStateBefore) '回滚后逻辑数据未恢复'
$failureSecretAfter = (Get-FileHash -LiteralPath (Join-Path $failureInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
Assert-True ($failureSecretAfter -eq $failureSecretBefore) '回滚后会话密钥未恢复'
Invoke-NativeChecked -FilePath $failureInstall.RuntimePython -Arguments @($precheck, '--precheck', $failureInstall.Database) | Out-Null
$databaseHashAfter = (Get-FileHash -LiteralPath $failureInstall.Database -Algorithm SHA256).Hash
Assert-True ($databaseHashAfter -eq $databaseHashBefore) '回滚后的数据库文件哈希与升级前不一致'
$failureSnapshots = @(
    Get-ChildItem -LiteralPath (Join-Path $failureInstall.ProgramRoot '_升级回滚') -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^[0-9a-fA-F]{32}$' }
)
Assert-True ($failureSnapshots.Count -ge 1) '故障回滚后应保留事务现场证据'
$failureBackups = @(Get-ChildItem -LiteralPath (Join-Path $failureInstall.ProgramRoot 'backups') -Filter 'reservation_before_upgrade_*.db')
Assert-True ($failureBackups.Count -ge 1) '故障回滚后应保留标准升级前数据库备份'

Write-Host '=== Windows rollback_restored recovery must not overwrite newer data ==='
$postRollbackKey = 'ci_after_rollback_restored'
$postRollbackValue = 'must-survive-rollback-finalization'
Set-AppMetaValue -Install $failureInstall -Key $postRollbackKey -Value $postRollbackValue
$rollbackRestoredStatePath = Join-Path $failureInstall.ProgramRoot '_升级状态.json'
$rollbackRestoredState = [ordered]@{
    Schema = 2
    TransactionId = $failureSnapshots[0].Name
    PackageVersion = $targetVersion
    SnapshotPath = $failureSnapshots[0].FullName
    BackupPath = $failureBackups[0].FullName
    InstallId = $null
    Stage = 'rollback_restored'
    OriginalVersion = '1.0.0'
    OriginalVersionExisted = $false
    OriginalInstallId = $null
    WasRunning = $false
    TaskExists = $false
    TaskEnabled = $false
    TaskWasRunning = $false
}
[IO.File]::WriteAllText(
    $rollbackRestoredStatePath,
    ($rollbackRestoredState | ConvertTo-Json -Depth 8),
    (New-Object Text.UTF8Encoding($false))
)
$failureRecoveryBat = Join-Path $failureInstall.Root $targetPackageName
Copy-Item -LiteralPath $goodPackage -Destination $failureRecoveryBat
$failureRecoveryExit = Invoke-UpgradeBat -BatPath $failureRecoveryBat
Assert-True ($failureRecoveryExit -eq 0) "rollback_restored 收尾并重试升级返回了退出码 $failureRecoveryExit"
Assert-True (
    (Get-AppMetaValue -Install $failureInstall -Key $postRollbackKey) -eq $postRollbackValue
) 'rollback_restored 收尾错误覆盖了回滚完成后新增数据'
Assert-NoOpenTransaction -Install $failureInstall

Write-Host "=== Windows V1.0.1 -> V$targetVersion failure and rollback path ==="
$v101FailureInstall = New-V101TestInstall -Name 'rollback-from-v101\会议室预约系统'
$v101FailureStateBefore = Get-LogicalDataState -Install $v101FailureInstall
$v101FailureSecretBefore = (Get-FileHash -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
Invoke-NativeChecked -FilePath $v101FailureInstall.RuntimePython -Arguments @($precheck, '--precheck', $v101FailureInstall.Database) | Out-Null
$v101FailureDatabaseHashBefore = (Get-FileHash -LiteralPath $v101FailureInstall.Database -Algorithm SHA256).Hash
$v101OldAppHash = (Get-FileHash -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'app.py') -Algorithm SHA256).Hash
$v101OldMigrateHash = (Get-FileHash -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'migrate_check.py') -Algorithm SHA256).Hash
$v101FailureBat = Join-Path $v101FailureInstall.Root "升级到V$targetVersion-故障回滚测试.bat"
Copy-Item -LiteralPath $brokenPackage -Destination $v101FailureBat

$v101FailureExit = Invoke-UpgradeBat -BatPath $v101FailureBat
Assert-True ($v101FailureExit -eq 1) "V1.0.1 起点故障升级应返回 1，实际为 $v101FailureExit"
Assert-NoOpenTransaction -Install $v101FailureInstall
Assert-True (
    (Get-Content -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq '1.0.1'
) 'V1.0.1 起点故障回滚后版本.txt 未恢复'
Assert-True (
    (Get-FileHash -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'app.py') -Algorithm SHA256).Hash -eq $v101OldAppHash
) 'V1.0.1 起点故障回滚后 app.py 未恢复'
Assert-True (
    (Get-FileHash -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'migrate_check.py') -Algorithm SHA256).Hash -eq $v101OldMigrateHash
) 'V1.0.1 起点故障回滚后 migrate_check.py 未恢复'
Assert-True ((Get-LogicalDataState -Install $v101FailureInstall) -eq $v101FailureStateBefore) 'V1.0.1 起点故障回滚后逻辑数据未恢复'
$v101FailureSecretAfter = (Get-FileHash -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
Assert-True ($v101FailureSecretAfter -eq $v101FailureSecretBefore) 'V1.0.1 起点故障回滚后会话密钥未恢复'
$v101FailureDatabaseHashAfter = (Get-FileHash -LiteralPath $v101FailureInstall.Database -Algorithm SHA256).Hash
Assert-True ($v101FailureDatabaseHashAfter -eq $v101FailureDatabaseHashBefore) 'V1.0.1 起点故障回滚后数据库文件哈希不一致'
$v101FailureSnapshots = @(
    Get-ChildItem -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot '_升级回滚') -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^[0-9a-fA-F]{32}$' }
)
Assert-True ($v101FailureSnapshots.Count -ge 1) 'V1.0.1 故障回滚后应保留事务现场证据'
$v101FailureBackups = @(Get-ChildItem -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'backups') -Filter 'reservation_before_upgrade_*.db')
Assert-True ($v101FailureBackups.Count -ge 1) 'V1.0.1 故障回滚后应保留标准升级前数据库备份'

$successLogs = @(Get-ChildItem -LiteralPath (Join-Path $successInstall.ProgramRoot 'logs') -Filter 'upgrade-*.log')
$failureLogs = @(Get-ChildItem -LiteralPath (Join-Path $failureInstall.ProgramRoot 'logs') -Filter 'upgrade-*.log')
$v101SuccessLogs = @(Get-ChildItem -LiteralPath (Join-Path $v101SuccessInstall.ProgramRoot 'logs') -Filter 'upgrade-*.log')
$v101FailureLogs = @(Get-ChildItem -LiteralPath (Join-Path $v101FailureInstall.ProgramRoot 'logs') -Filter 'upgrade-*.log')
Assert-True ($successLogs.Count -ge 2) '成功与重复运行应各生成升级日志'
Assert-True ($failureLogs.Count -ge 1) '故障回滚应生成升级日志'
Assert-True ($v101SuccessLogs.Count -ge 3) 'V1.0.1 起点准备、成功与重复运行应各生成升级日志'
Assert-True ($v101FailureLogs.Count -ge 2) 'V1.0.1 起点准备与故障回滚应各生成升级日志'

Write-Host 'Windows PowerShell 5.1 BAT integration tests passed.' -ForegroundColor Green
