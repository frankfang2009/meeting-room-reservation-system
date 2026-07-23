Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$toolRoot = Join-Path $repoRoot '02_开发工作区\升级包工具'
$payloadRoot = Join-Path $toolRoot '负载示例'
$candidatePackage = Join-Path $toolRoot '输出-待实机验收\升级到V1.0.1.bat'
$expectedCandidateSha256 = 'cd0d52b9ffb5d2864e7ad98d8969b86376d8577391399c30295d0722d34848cd'
$oldReference = Join-Path $repoRoot '02_开发工作区\Windows部署目录-V1.0.0'
$workRoot = Join-Path $env:RUNNER_TEMP 'meeting-room-upgrade-ci'
$packageRoot = Join-Path $workRoot 'packages'
$installRoot = Join-Path $workRoot 'installs'
$hostPython = (Get-Command python.exe -ErrorAction Stop).Source
$hostPythonRoot = Split-Path -Parent $hostPython

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
    Copy-TreeWithRobocopy -Source $hostPythonRoot -Destination $runtime
    $runtimePython = Join-Path $runtime 'python.exe'
    $runtimePythonw = Join-Path $runtime 'pythonw.exe'
    Assert-True (Test-Path -LiteralPath $runtimePython -PathType Leaf) '测试 runtime 缺少 python.exe'
    Assert-True (Test-Path -LiteralPath $runtimePythonw -PathType Leaf) '测试 runtime 缺少 pythonw.exe'
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
    param([string]$BatPath)
    # 空行供 BAT 末尾 pause 使用；runner 进程本身已具管理员权限，不走交互式 UAC。
    $command = 'call "{0}"' -f $BatPath
    '' | & $env:ComSpec /d /c $command | Out-Host
    $exitCode = $LASTEXITCODE
    return [int]$exitCode
}

function Assert-NoOpenTransaction {
    param($Install)
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Install.ProgramRoot '_升级状态.json'))) '升级状态文件不应残留'
}

if (Test-Path -LiteralPath $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
Assert-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'GitHub Windows runner 不是管理员，无法验证真实 BAT 主路径'

$goodPackage = Join-Path $packageRoot '升级到V1.0.1.bat'
Assert-True (Test-Path -LiteralPath $candidatePackage -PathType Leaf) '仓库缺少待验收的 V1.0.1 BAT'
$candidateSha256 = (Get-FileHash -LiteralPath $candidatePackage -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-True ($candidateSha256 -eq $expectedCandidateSha256) "候选 BAT SHA-256 不匹配：$candidateSha256"
Copy-Item -LiteralPath $candidatePackage -Destination $goodPackage

$brokenPayload = Join-Path $workRoot 'broken-payload'
Copy-TreeWithRobocopy -Source $payloadRoot -Destination $brokenPayload
$brokenApp = Join-Path $brokenPayload '_程序文件\app.py'
[IO.File]::AppendAllText($brokenApp, "`nthis is deliberately invalid python !!!`n", (New-Object Text.UTF8Encoding($false)))
$brokenPackage = Join-Path $packageRoot '升级到V1.0.1-故障回滚测试.bat'
Invoke-NativeChecked -FilePath $hostPython -Arguments @(
    (Join-Path $toolRoot '制作升级包.py'),
    $brokenPayload,
    '1.0.1',
    '--out',
    $brokenPackage
) | Out-Null

Write-Host '=== Windows success and idempotency path ==='
$successInstall = New-TestInstall -Name 'success\会议室预约系统'
$successStateBefore = Get-LogicalDataState -Install $successInstall
$successSecretBefore = (Get-FileHash -LiteralPath (Join-Path $successInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
$successBat = Join-Path $successInstall.Root '升级到V1.0.1.bat'
Copy-Item -LiteralPath $goodPackage -Destination $successBat

$successExit = Invoke-UpgradeBat -BatPath $successBat
Assert-True ($successExit -eq 0) "成功升级返回了退出码 $successExit"
Assert-True ((Get-Content -LiteralPath (Join-Path $successInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq '1.0.1') '版本.txt 未提交为 1.0.1'
Assert-NoOpenTransaction -Install $successInstall
$successStateAfter = Get-LogicalDataState -Install $successInstall
$successStateExpected = ($successStateBefore | ConvertFrom-Json)
$successStateExpected.schema_version = '1'
$successExpectedJson = $successStateExpected | ConvertTo-Json -Compress
$successActualJson = ($successStateAfter | ConvertFrom-Json) | ConvertTo-Json -Compress
Assert-True ($successActualJson -eq $successExpectedJson) '成功升级后逻辑数据发生变化'
$successSecretAfter = (Get-FileHash -LiteralPath (Join-Path $successInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
Assert-True ($successSecretAfter -eq $successSecretBefore) '成功升级后会话密钥发生变化'

$secondExit = Invoke-UpgradeBat -BatPath $successBat
Assert-True ($secondExit -eq 0) "重复运行返回了退出码 $secondExit"
Assert-NoOpenTransaction -Install $successInstall
Assert-True ((Get-LogicalDataState -Install $successInstall) -eq $successStateAfter) '重复运行改变了数据'

Write-Host '=== Windows committed-state cleanup must preserve newer data ==='
$postCommitKey = 'ci_after_version_commit'
$postCommitValue = 'must-survive-committed-cleanup'
Set-AppMetaValue -Install $successInstall -Key $postCommitKey -Value $postCommitValue
$rollbackRoot = Join-Path $successInstall.ProgramRoot '_升级回滚'
$snapshot = @(Get-ChildItem -LiteralPath $rollbackRoot -Directory | Where-Object { $_.Name -match '^[0-9a-fA-F]{32}$' } | Sort-Object LastWriteTimeUtc | Select-Object -Last 1)
Assert-True ($snapshot.Count -eq 1) '成功升级后没有找到用于中断恢复测试的事务快照'
$committedStatePath = Join-Path $successInstall.ProgramRoot '_升级状态.json'
$committedState = [ordered]@{
    TransactionId = $snapshot[0].Name
    PackageVersion = '1.0.1'
    SnapshotPath = $snapshot[0].FullName
    Stage = 'version_committed'
    OriginalVersion = '1.0.0'
    OriginalVersionExisted = $false
    WasRunning = $false
    TaskExists = $false
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
Assert-True ((Get-Content -LiteralPath (Join-Path $successInstall.ProgramRoot '版本.txt') -Raw).Trim() -eq '1.0.1') 'version_committed 安全收尾改变了已提交版本'

Write-Host '=== Windows failure and rollback path ==='
$failureInstall = New-TestInstall -Name 'rollback\会议室预约系统'
$failureStateBefore = Get-LogicalDataState -Install $failureInstall
$failureSecretBefore = (Get-FileHash -LiteralPath (Join-Path $failureInstall.ProgramRoot 'data\.secret_key') -Algorithm SHA256).Hash
$oldAppHash = (Get-FileHash -LiteralPath (Join-Path $failureInstall.ProgramRoot 'app.py') -Algorithm SHA256).Hash
$precheck = Join-Path $payloadRoot '_程序文件\migrate_check.py'
Invoke-NativeChecked -FilePath $failureInstall.RuntimePython -Arguments @($precheck, '--precheck', $failureInstall.Database) | Out-Null
$databaseHashBefore = (Get-FileHash -LiteralPath $failureInstall.Database -Algorithm SHA256).Hash
$failureBat = Join-Path $failureInstall.Root '升级到V1.0.1-故障回滚测试.bat'
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

$successLogs = @(Get-ChildItem -LiteralPath (Join-Path $successInstall.ProgramRoot 'logs') -Filter 'upgrade-*.log')
$failureLogs = @(Get-ChildItem -LiteralPath (Join-Path $failureInstall.ProgramRoot 'logs') -Filter 'upgrade-*.log')
Assert-True ($successLogs.Count -ge 2) '成功与重复运行应各生成升级日志'
Assert-True ($failureLogs.Count -ge 1) '故障回滚应生成升级日志'

Write-Host 'Windows PowerShell 5.1 BAT integration tests passed.' -ForegroundColor Green
