Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$toolRoot = Join-Path $repoRoot '02_开发工作区\升级包工具'
$builder = Join-Path $toolRoot '制作覆盖更新包.py'
$v101Reference = Join-Path $repoRoot '02_开发工作区\Windows部署目录-V1.0.1-待实机验收'
$workRoot = Join-Path $env:RUNNER_TEMP 'meeting-room-overlay-ci'
$releaseRoot = Join-Path $workRoot 'release'
$installsRoot = Join-Path $workRoot 'installs'
$evidenceRoot = Join-Path $workRoot 'evidence'
$hostPython = (Get-Command python.exe -ErrorAction Stop).Source
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [IO.File]::WriteAllText(
        $Path,
        $Content,
        (New-Object Text.UTF8Encoding($false))
    )
}

function Invoke-NativeChecked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [int[]]$AllowedExitCodes = @(0)
    )
    $output = @(& $FilePath @Arguments)
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host ([string]$line)
    }
    if ($AllowedExitCodes -notcontains $exitCode) {
        throw "命令失败（退出码 $exitCode）：$FilePath $($Arguments -join ' ')"
    }
    return [int]$exitCode
}

function Invoke-PythonCodeChecked {
    param(
        [string]$Python,
        [string]$Code,
        [string[]]$Arguments = @()
    )
    $variableName = 'MEETING_ROOM_OVERLAY_CI_PYTHON_CODE'
    $hadPreviousValue = Test-Path -LiteralPath "Env:$variableName"
    $previousValue = [Environment]::GetEnvironmentVariable($variableName)
    try {
        [Environment]::SetEnvironmentVariable($variableName, $Code)
        $bootstrap = "import os; exec(os.environ['MEETING_ROOM_OVERLAY_CI_PYTHON_CODE'])"
        return Invoke-NativeChecked `
            -FilePath $Python `
            -Arguments (@('-c', $bootstrap) + $Arguments)
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
    $output = @(
        & robocopy.exe $Source $Destination `
            /MIR /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ `
            /NP /NFL /NDL /NJH
    )
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host ([string]$line)
    }
    if ($exitCode -lt 0 -or $exitCode -gt 7) {
        throw "robocopy 失败（退出码 $exitCode）：$Source -> $Destination"
    }
}

function Get-TreeState {
    param([string]$Root)
    Assert-True (Test-Path -LiteralPath $Root -PathType Container) "目录不存在：$Root"
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $records = @(
        Get-ChildItem -LiteralPath $fullRoot -Force -Recurse -File |
            ForEach-Object {
                $relative = $_.FullName.Substring($fullRoot.Length + 1).Replace('\', '/')
                [ordered]@{
                    path = $relative
                    size = [long]$_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            } |
            Sort-Object { [string]$_.path }
    )
    return [ordered]@{
        root = $fullRoot
        files = $records
    }
}

function Get-TreeFingerprint {
    param([string]$Root)
    $state = Get-TreeState -Root $Root
    $lines = @(
        foreach ($record in $state.files) {
            "{0}`t{1}`t{2}" -f $record.path, $record.size, $record.sha256
        }
    )
    return [string]::Join("`n", [string[]]$lines)
}

function Save-TreeState {
    param([string]$Root, [string]$Destination)
    $state = Get-TreeState -Root $Root
    Write-Utf8NoBom `
        -Path $Destination `
        -Content (($state | ConvertTo-Json -Depth 8) + "`n")
}

function Assert-TreeUnchanged {
    param(
        [string]$Root,
        [string]$ExpectedFingerprint,
        [string]$EvidencePath,
        [string]$Description
    )
    Save-TreeState -Root $Root -Destination $EvidencePath
    $actual = Get-TreeFingerprint -Root $Root
    Assert-True (
        [string]::Equals(
            $ExpectedFingerprint,
            $actual,
            [StringComparison]::Ordinal
        )
    ) "$Description 的文件集合、大小或 SHA-256 发生变化"
}

function New-TestInstall {
    param([string]$Root)
    Copy-TreeWithRobocopy -Source $v101Reference -Destination $Root
    $programRoot = Join-Path $Root '_程序文件'
    $runtimePython = Join-Path $programRoot 'runtime\python.exe'
    Assert-True (
        Test-Path -LiteralPath $runtimePython -PathType Leaf
    ) "冻结 V1.0.1 安装缺少 runtime\python.exe：$Root"

    $hadPassword = Test-Path -LiteralPath 'Env:MEETING_ROOM_INITIAL_ADMIN_PASSWORD'
    $oldPassword = [Environment]::GetEnvironmentVariable(
        'MEETING_ROOM_INITIAL_ADMIN_PASSWORD'
    )
    try {
        $env:MEETING_ROOM_INITIAL_ADMIN_PASSWORD = 'CI-Overlay-Admin-Password-2026!'
        Push-Location $programRoot
        try {
            Invoke-PythonCodeChecked `
                -Python $runtimePython `
                -Code 'from app import app, init_db; app.app_context().push(); init_db()' |
                Out-Null
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if ($hadPassword) {
            $env:MEETING_ROOM_INITIAL_ADMIN_PASSWORD = $oldPassword
        }
        else {
            Remove-Item Env:MEETING_ROOM_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue
        }
    }

    $database = Join-Path $programRoot 'data\reservation.db'
    $seedCode = @'
import sqlite3
import sys

database = sqlite3.connect(sys.argv[1])
admin_id = database.execute(
    "SELECT id FROM users WHERE username='admin'"
).fetchone()[0]
room_id, room_name = database.execute(
    "SELECT id, name FROM rooms ORDER BY id LIMIT 1"
).fetchone()
cursor = database.execute(
    """
    INSERT INTO reservations (
        room_id, room_name_snapshot, reserve_date, start_time, end_time,
        user_id, party_name, case_number, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (
        room_id, room_name, "2099-12-31", "09:00", "10:00",
        admin_id, "CI保留单位", "CI-KEEP-001", "升级前测试数据",
    ),
)
reservation_id = cursor.lastrowid
for slot in ("09:00", "09:30"):
    database.execute(
        """
        INSERT INTO reservation_slots (
            reservation_id, room_id, reserve_date, slot_time
        ) VALUES (?, ?, ?, ?)
        """,
        (reservation_id, room_id, "2099-12-31", slot),
    )
database.execute(
    "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
    ("ci_preserve_marker", "keep-me"),
)
database.commit()
database.close()
'@
    Invoke-PythonCodeChecked `
        -Python $runtimePython `
        -Code $seedCode `
        -Arguments @($database) |
        Out-Null

    $dataRoot = Join-Path $programRoot 'data'
    Write-Utf8NoBom `
        -Path (Join-Path $dataRoot 'install_id.txt') `
        -Content "ci-install-中文-&-(1)`n"
    Write-Utf8NoBom `
        -Path (Join-Path $dataRoot 'network_address_state.json') `
        -Content "{`"last_lan_url`":`"http://192.0.2.10:5055`"}`n"
    $attachment = Join-Path $dataRoot '客户附件 & (1)\保留 数据.bin'
    New-Item -ItemType Directory -Path (Split-Path -Parent $attachment) -Force |
        Out-Null
    [IO.File]::WriteAllBytes(
        $attachment,
        [byte[]](0, 1, 2, 3, 13, 10, 255, 128, 64)
    )

    Assert-True (
        (Get-Content -LiteralPath (Join-Path $programRoot '版本.txt') -Raw).Trim() -eq '1.0.1'
    ) "测试安装不是冻结 V1.0.1：$Root"
    return $Root
}

function Expand-RepairPackage {
    param([string]$Artifact, [string]$Destination)
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Expand-Archive -LiteralPath $Artifact -DestinationPath $Destination -Force
    $originalLauncher = Join-Path $Destination '修复并更新到V1.0.2.bat'
    $specialLauncher = Join-Path $Destination '修复并更新到V1.0.2 (1) & CI.bat'
    Assert-True (
        Test-Path -LiteralPath $originalLauncher -PathType Leaf
    ) "修复 ZIP 缺少零参数 BAT：$originalLauncher"
    Move-Item -LiteralPath $originalLauncher -Destination $specialLauncher
    Assert-True (
        Test-Path -LiteralPath (Join-Path $Destination '_V1.0.2更新工具\update.py') -PathType Leaf
    ) "修复 ZIP 缺少 update.py：$Destination"
    return $specialLauncher
}

function Invoke-ZeroArgumentBat {
    param(
        [string]$BatPath,
        [string]$InstallRoot,
        [int]$TimeoutSeconds = 300
    )
    Assert-True (
        Test-Path -LiteralPath $BatPath -PathType Leaf
    ) "待启动 BAT 不存在：$BatPath"
    $hadInstallRoot = Test-Path -LiteralPath 'Env:MEETING_ROOM_UPDATE_INSTALL_ROOT'
    $oldInstallRoot = [Environment]::GetEnvironmentVariable(
        'MEETING_ROOM_UPDATE_INSTALL_ROOT'
    )
    $hadNoPause = Test-Path -LiteralPath 'Env:MEETING_ROOM_UPDATE_NO_PAUSE'
    $oldNoPause = [Environment]::GetEnvironmentVariable(
        'MEETING_ROOM_UPDATE_NO_PAUSE'
    )
    $hadBatToRun = Test-Path -LiteralPath 'Env:MEETING_ROOM_UPDATE_BAT_TO_RUN'
    $oldBatToRun = [Environment]::GetEnvironmentVariable(
        'MEETING_ROOM_UPDATE_BAT_TO_RUN'
    )
    $hadExitFile = Test-Path -LiteralPath 'Env:MEETING_ROOM_UPDATE_EXIT_FILE'
    $oldExitFile = [Environment]::GetEnvironmentVariable(
        'MEETING_ROOM_UPDATE_EXIT_FILE'
    )
    $workingDirectory = Split-Path -Parent $BatPath
    $wrapperName = '__overlay_ci_zero_argument.cmd'
    $wrapperPath = Join-Path $workingDirectory $wrapperName
    $exitCodePath = Join-Path $workingDirectory '__overlay_ci_zero_argument.exit'
    try {
        $env:MEETING_ROOM_UPDATE_INSTALL_ROOT = $InstallRoot
        $env:MEETING_ROOM_UPDATE_NO_PAUSE = '1'
        $env:MEETING_ROOM_UPDATE_BAT_TO_RUN = $BatPath
        $env:MEETING_ROOM_UPDATE_EXIT_FILE = $exitCodePath
        Remove-Item -LiteralPath $exitCodePath -Force -ErrorAction SilentlyContinue
        [IO.File]::WriteAllText(
            $wrapperPath,
            (
                "@echo off`r`n" +
                "call `"%MEETING_ROOM_UPDATE_BAT_TO_RUN%`"`r`n" +
                "set `"OVERLAY_CI_RC=%errorlevel%`"`r`n" +
                "> `"%MEETING_ROOM_UPDATE_EXIT_FILE%`" echo %OVERLAY_CI_RC%`r`n" +
                "exit /b %OVERLAY_CI_RC%`r`n"
            ),
            [Text.Encoding]::ASCII
        )
        Write-Host "Start-Process zero-argument BAT: $BatPath"
        Write-Host "Install root inherited through environment: $InstallRoot"

        # 故意不传任何命令行参数：验证交付给客户的零参数 BAT，且 BAT 路径
        # 自身包含中文、空格、(1) 和 &。Hosted runner 已是管理员，因此本函数
        # 不覆盖普通用户 Explorer -> UAC 安全桌面的人工验收路径。CMD 包装器
        # 只负责可靠取得批处理退出码；原 BAT 仍以零参数运行。
        $process = Start-Process `
            -FilePath $env:ComSpec `
            -ArgumentList @('/d', '/c', $wrapperName) `
            -WorkingDirectory $workingDirectory `
            -NoNewWindow `
            -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "零参数 BAT 在 $TimeoutSeconds 秒内没有退出：$BatPath"
        }
        $process.Refresh()
        Assert-True (
            Test-Path -LiteralPath $exitCodePath -PathType Leaf
        ) "零参数 BAT 没有写出退出码：$BatPath"
        $exitCodeText = (
            Get-Content -LiteralPath $exitCodePath -Raw -Encoding ASCII
        ).Trim()
        $exitCode = 0
        Assert-True (
            [int]::TryParse($exitCodeText, [ref]$exitCode)
        ) "零参数 BAT 退出码格式非法：$exitCodeText"
        Write-Host "Zero-argument BAT exit code: $exitCode"
        return [int]$exitCode
    }
    finally {
        if ($hadInstallRoot) {
            $env:MEETING_ROOM_UPDATE_INSTALL_ROOT = $oldInstallRoot
        }
        else {
            Remove-Item Env:MEETING_ROOM_UPDATE_INSTALL_ROOT -ErrorAction SilentlyContinue
        }
        if ($hadNoPause) {
            $env:MEETING_ROOM_UPDATE_NO_PAUSE = $oldNoPause
        }
        else {
            Remove-Item Env:MEETING_ROOM_UPDATE_NO_PAUSE -ErrorAction SilentlyContinue
        }
        if ($hadBatToRun) {
            $env:MEETING_ROOM_UPDATE_BAT_TO_RUN = $oldBatToRun
        }
        else {
            Remove-Item Env:MEETING_ROOM_UPDATE_BAT_TO_RUN -ErrorAction SilentlyContinue
        }
        if ($hadExitFile) {
            $env:MEETING_ROOM_UPDATE_EXIT_FILE = $oldExitFile
        }
        else {
            Remove-Item Env:MEETING_ROOM_UPDATE_EXIT_FILE -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $wrapperPath, $exitCodePath `
            -Force -ErrorAction SilentlyContinue
    }
}

function Assert-PackageContract {
    param([string]$PackageRoot)
    $tool = Join-Path $PackageRoot '_V1.0.2更新工具'
    $runtimePython = Join-Path $tool 'runtime\python.exe'
    $code = @'
import importlib.util
import pathlib
import sys

tool = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("overlay_ci_update", tool / "update.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
bundle = module.Bundle.load(tool)
print(bundle.release, bundle.baseline.version, bundle.target.version)
'@
    Invoke-PythonCodeChecked `
        -Python $runtimePython `
        -Code $code `
        -Arguments @($tool) |
        Out-Null
}

function Assert-InstalledPayload {
    param(
        [string]$PackageRoot,
        [string]$InstallRoot,
        [ValidateSet('baseline', 'target')]
        [string]$Payload
    )
    $tool = Join-Path $PackageRoot '_V1.0.2更新工具'
    $runtimePython = Join-Path $tool 'runtime\python.exe'
    $code = @'
import importlib.util
import pathlib
import sys

tool = pathlib.Path(sys.argv[1])
install = pathlib.Path(sys.argv[2])
payload_name = sys.argv[3]
spec = importlib.util.spec_from_file_location("overlay_ci_update", tool / "update.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
bundle = module.Bundle.load(tool)
payload = bundle.baseline if payload_name == "baseline" else bundle.target
module._assert_installed_payload(
    install,
    payload,
    include_version=True,
)
print("installed-payload-ok", payload.version)
'@
    Invoke-PythonCodeChecked `
        -Python $runtimePython `
        -Code $code `
        -Arguments @($tool, $InstallRoot, $Payload) |
        Out-Null
}

function Get-LatestRepairLog {
    param([string]$InstallRoot)
    $logs = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $InstallRoot '_程序文件\logs') `
            -File `
            -Filter 'repair-update-*.log' |
            Sort-Object LastWriteTimeUtc, Name
    )
    Assert-True ($logs.Count -gt 0) "没有找到 repair-update 日志：$InstallRoot"
    return $logs[-1].FullName
}

function Assert-BaselineThenTargetLog {
    param([string]$LogPath, [string]$EvidencePath)
    $text = [IO.File]::ReadAllText(
        $LogPath,
        (New-Object Text.UTF8Encoding($false, $true))
    )
    Write-Utf8NoBom -Path $EvidencePath -Content $text
    $baselineMessage = '受管程序已严格恢复并校验为冻结 V1.0.1；真实 data 未改变'
    $targetMessage = 'V1.0.2 版本文件已最后提交；真实客户 data 全树哈希未改变'
    $baselineIndex = $text.IndexOf(
        $baselineMessage,
        [StringComparison]::Ordinal
    )
    $targetIndex = $text.IndexOf(
        $targetMessage,
        [StringComparison]::Ordinal
    )
    Assert-True ($baselineIndex -ge 0) "日志没有证明先恢复冻结 V1.0.1：$LogPath"
    Assert-True ($targetIndex -gt $baselineIndex) "日志没有证明 V1.0.1 基线之后才提交 V1.0.2：$LogPath"
    Assert-True (
        $text.Contains(
            'V1.0.2-r1 修复更新完成；计划任务启用状态已恢复，系统保持停止等待普通用户启动'
        )
    ) "日志缺少修复更新完成记录：$LogPath"
}

function Assert-RepeatNoOpLog {
    param([string]$LogPath, [string]$EvidencePath)
    $text = [IO.File]::ReadAllText(
        $LogPath,
        (New-Object Text.UTF8Encoding($false, $true))
    )
    Write-Utf8NoBom -Path $EvidencePath -Content $text
    Assert-True (
        $text.Contains(
            '当前受管程序和 runtime 已严格匹配 V1.0.2-r1；本次无需停机、无需回写 data'
        )
    ) "重复执行日志没有证明严格匹配后的无写入收敛：$LogPath"
}

function Set-TargetHealthCheckFailure {
    param([string]$PackageRoot)
    $tool = Join-Path $PackageRoot '_V1.0.2更新工具'
    $runtimePython = Join-Path $tool 'runtime\python.exe'
    $code = @'
import hashlib
import json
import os
import pathlib
import sys
import zipfile

tool = pathlib.Path(sys.argv[1])
manifest_path = tool / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
section = manifest["target"]
zip_path = tool / section["file"]
with zipfile.ZipFile(zip_path, "r") as archive:
    files = {
        info.filename: archive.read(info)
        for info in archive.infolist()
        if not info.is_dir()
    }

injected_path = "_程序文件/migrate_check.py"
if injected_path not in files:
    raise SystemExit("target payload has no migrate_check.py")
files[injected_path] = (
    "import sys\n"
    "print('CI injected target health-check failure')\n"
    "raise SystemExit(91)\n"
).encode("utf-8")

temporary = zip_path.with_suffix(".ci-tmp")
with zipfile.ZipFile(
    temporary,
    "w",
    compression=zipfile.ZIP_DEFLATED,
    compresslevel=9,
    allowZip64=True,
) as archive:
    for name in sorted(files):
        archive.writestr(name, files[name])
os.replace(temporary, zip_path)

zip_bytes = zip_path.read_bytes()
section["size"] = len(zip_bytes)
section["sha256"] = hashlib.sha256(zip_bytes).hexdigest()
matching = [
    record for record in section["files"]
    if record["path"] == injected_path
]
if len(matching) != 1:
    raise SystemExit("target manifest migrate_check record is not unique")
matching[0]["size"] = len(files[injected_path])
matching[0]["sha256"] = hashlib.sha256(files[injected_path]).hexdigest()
manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print("target-health-check-failure-injected")
'@
    Invoke-PythonCodeChecked `
        -Python $runtimePython `
        -Code $code `
        -Arguments @($tool) |
        Out-Null
}

if (Test-Path -LiteralPath $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $installsRoot -Force | Out-Null
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
Assert-True $isAdministrator (
    '本集成测试要求 GitHub hosted runner 已经处于管理员上下文；' +
    '普通用户 UAC 路径属于独立实机门禁'
)

$results = [ordered]@{
    schema = 1
    runner_is_administrator = [bool]$isAdministrator
    standard_user_explorer_uac_covered = $false
    standard_user_explorer_uac_gate = 'manual Windows 10/11 acceptance'
    package_contract = $false
    special_path_success = $false
    legacy_residue_recovered = $false
    data_unchanged_after_success = $false
    repeat_run_success = $false
    lock_exit_code = $null
    target_failure_exit_code = $null
    target_failure_baseline_restored = $false
}

try {
    Invoke-NativeChecked `
        -FilePath $hostPython `
        -Arguments @($builder, '--release-root', $releaseRoot) |
        Out-Null

    $releaseDir = Join-Path $releaseRoot 'V1.0.2-r1'
    $artifact = Join-Path $releaseDir '会议室预约系统-V1.0.2-修复更新-r1.zip'
    $releaseManifest = Join-Path $releaseDir 'V1.0.2-r1-发布清单.json'
    Assert-True (
        Test-Path -LiteralPath $artifact -PathType Leaf
    ) "构建没有生成修复 ZIP：$artifact"
    Assert-True (
        Test-Path -LiteralPath $releaseManifest -PathType Leaf
    ) "构建没有生成发布清单：$releaseManifest"

    $manifest = Get-Content -LiteralPath $releaseManifest -Raw |
        ConvertFrom-Json
    $artifactHash = (
        Get-FileHash -LiteralPath $artifact -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    Assert-True (
        $artifactHash -eq [string]$manifest.artifact.sha256
    ) '构建 ZIP 与外部发布清单 SHA-256 不一致'

    $successScenario = Join-Path $installsRoot '客户现场 中文 空格 & (1)'
    $successInstall = Join-Path $successScenario '会议室预约系统-单位局域网版-最终'
    $successPackage = Join-Path $successScenario '下载的修复包 副本'
    New-TestInstall -Root $successInstall | Out-Null
    $successLauncher = Expand-RepairPackage `
        -Artifact $artifact `
        -Destination $successPackage
    Assert-PackageContract -PackageRoot $successPackage
    $results.package_contract = $true

    $successData = Join-Path $successInstall '_程序文件\data'
    $successDataBefore = Get-TreeFingerprint -Root $successData
    Save-TreeState `
        -Root $successData `
        -Destination (Join-Path $evidenceRoot 'success-data-before.json')

    # 构造客户旧 V1.0.2 中途中断后的混合程序和确定性旧残留。
    $successProgram = Join-Path $successInstall '_程序文件'
    $targetScratch = Join-Path $successScenario 'target-payload-scratch'
    Expand-Archive `
        -LiteralPath (Join-Path $successPackage '_V1.0.2更新工具\target-v1.0.2.zip') `
        -DestinationPath $targetScratch `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $targetScratch '_程序文件\app.py') `
        -Destination (Join-Path $successProgram 'app.py') `
        -Force
    # 不把展开后的 target 树留在修复包同级，否则它本身也会像一套安装，
    # 干扰“唯一安装目录”的零参数自动发现。
    Remove-Item -LiteralPath $targetScratch -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $successProgram 'server.py') -Force
    Write-Utf8NoBom `
        -Path (Join-Path $successProgram 'static\旧版残留.js') `
        -Content "stale legacy target file`n"
    New-Item `
        -ItemType Directory `
        -Path (Join-Path $successProgram '__pycache__') `
        -Force |
        Out-Null
    [IO.File]::WriteAllBytes(
        (Join-Path $successProgram '__pycache__\app.cpython-313.pyc'),
        [byte[]](1, 2, 3, 4)
    )
    Write-Utf8NoBom `
        -Path (Join-Path $successProgram '_升级状态.json') `
        -Content "{`"Stage`":`"program_replaced`"}`n"
    [IO.File]::WriteAllBytes(
        (Join-Path $successProgram '_升级锁'),
        [Text.Encoding]::UTF8.GetBytes('legacy-lock-evidence')
    )
    $legacyRollbackEvidence = Join-Path $successProgram (
        '_升级回滚\old-transaction\data\reservation.db'
    )
    New-Item `
        -ItemType Directory `
        -Path (Split-Path -Parent $legacyRollbackEvidence) `
        -Force |
        Out-Null
    [IO.File]::WriteAllBytes(
        $legacyRollbackEvidence,
        [Text.Encoding]::UTF8.GetBytes('legacy-evidence-must-survive')
    )

    $successExit = Invoke-ZeroArgumentBat `
        -BatPath $successLauncher `
        -InstallRoot $successInstall
    Assert-True ($successExit -eq 0) "特殊路径首次修复退出码不是 0：$successExit"
    Assert-TreeUnchanged `
        -Root $successData `
        -ExpectedFingerprint $successDataBefore `
        -EvidencePath (Join-Path $evidenceRoot 'success-data-after-first-run.json') `
        -Description '首次修复后的真实 data'
    $results.data_unchanged_after_success = $true
    Assert-InstalledPayload `
        -PackageRoot $successPackage `
        -InstallRoot $successInstall `
        -Payload target
    Assert-True (
        (Get-Content -LiteralPath (Join-Path $successProgram '版本.txt') -Raw).Trim() -eq '1.0.2'
    ) '特殊路径首次修复后版本不是 1.0.2'
    Assert-True (
        -not (Test-Path -LiteralPath (Join-Path $successProgram 'static\旧版残留.js'))
    ) '严格覆盖后仍保留旧版 static 残留'
    Assert-True (
        -not (Test-Path -LiteralPath (Join-Path $successProgram '__pycache__'))
    ) '严格覆盖后仍保留根 __pycache__'
    Assert-True (
        [IO.File]::ReadAllText($legacyRollbackEvidence) -eq
            'legacy-evidence-must-survive'
    ) '旧升级回滚证据被删除或改写'
    Assert-True (
        -not (Test-Path -LiteralPath (Join-Path $successProgram '_升级状态.json'))
    ) '旧升级状态没有被归档'
    Assert-True (
        -not (Test-Path -LiteralPath (Join-Path $successProgram '_升级锁'))
    ) '旧升级锁没有被安全清理'
    $legacyArchives = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $successProgram 'logs') `
            -Directory `
            -Filter 'V1.0.2旧升级残留_*'
    )
    $matchingArchive = @(
        $legacyArchives |
            Where-Object {
                (Test-Path -LiteralPath (Join-Path $_.FullName '_升级状态.json')) -and
                (Test-Path -LiteralPath (Join-Path $_.FullName '_升级锁'))
            }
    )
    Assert-True ($matchingArchive.Count -ge 1) '旧升级状态和锁没有作为证据一起归档'
    $results.legacy_residue_recovered = $true

    $firstSuccessLog = Get-LatestRepairLog -InstallRoot $successInstall
    Assert-BaselineThenTargetLog `
        -LogPath $firstSuccessLog `
        -EvidencePath (Join-Path $evidenceRoot 'success-first-run.log')
    $results.special_path_success = $true

    # 同一零参数 BAT 再运行一次，证明重复执行仍收敛到目标版本且 data 不变。
    $repeatExit = Invoke-ZeroArgumentBat `
        -BatPath $successLauncher `
        -InstallRoot $successInstall
    Assert-True ($repeatExit -eq 0) "重复执行退出码不是 0：$repeatExit"
    Assert-TreeUnchanged `
        -Root $successData `
        -ExpectedFingerprint $successDataBefore `
        -EvidencePath (Join-Path $evidenceRoot 'success-data-after-repeat.json') `
        -Description '重复修复后的真实 data'
    Assert-InstalledPayload `
        -PackageRoot $successPackage `
        -InstallRoot $successInstall `
        -Payload target
    $repeatLog = Get-LatestRepairLog -InstallRoot $successInstall
    Assert-RepeatNoOpLog `
        -LogPath $repeatLog `
        -EvidencePath (Join-Path $evidenceRoot 'success-repeat-run.log')
    $results.repeat_run_success = $true

    # 用包内 updater 自身持有新锁后启动同一个零参数 BAT，应稳定返回 4，
    # 且不改程序或 data。不要用 FileShare.None 代替真实字节锁：那会在
    # Python 打开锁文件时提前触发共享拒绝，不是两个更新器并发的实际路径。
    $lockPath = Join-Path $successProgram '_V102覆盖更新锁'
    $versionBeforeLock = (
        Get-Content -LiteralPath (Join-Path $successProgram '版本.txt') -Raw
    ).Trim()
    $dataBeforeLock = Get-TreeFingerprint -Root $successData
    $lockHelper = Join-Path $workRoot 'hold-overlay-updater-lock.py'
    $lockReady = Join-Path $workRoot 'overlay-updater-lock.ready'
    $lockRelease = Join-Path $workRoot 'overlay-updater-lock.release'
    $lockStdout = Join-Path $workRoot 'overlay-updater-lock.stdout.log'
    $lockStderr = Join-Path $workRoot 'overlay-updater-lock.stderr.log'
    $lockPython = Join-Path $successPackage '_V1.0.2更新工具\runtime\python.exe'
    $lockUpdater = Join-Path $successPackage '_V1.0.2更新工具\update.py'
    Write-Utf8NoBom -Path $lockHelper -Content @'
import importlib.util
import os
import pathlib
import sys
import time

updater_path = pathlib.Path(os.environ["MEETING_ROOM_CI_LOCK_UPDATER"])
lock_path = pathlib.Path(os.environ["MEETING_ROOM_CI_LOCK_PATH"])
ready_path = pathlib.Path(os.environ["MEETING_ROOM_CI_LOCK_READY"])
release_path = pathlib.Path(os.environ["MEETING_ROOM_CI_LOCK_RELEASE"])
spec = importlib.util.spec_from_file_location("meeting_room_ci_lock_updater", updater_path)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load overlay updater")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
with module.ExclusiveLock(lock_path):
    ready_path.write_text("ready\n", encoding="utf-8")
    while not release_path.exists():
        time.sleep(0.05)
'@
    Remove-Item -LiteralPath $lockReady, $lockRelease, $lockStdout, $lockStderr `
        -Force -ErrorAction SilentlyContinue
    $env:MEETING_ROOM_CI_LOCK_UPDATER = $lockUpdater
    $env:MEETING_ROOM_CI_LOCK_PATH = $lockPath
    $env:MEETING_ROOM_CI_LOCK_READY = $lockReady
    $env:MEETING_ROOM_CI_LOCK_RELEASE = $lockRelease
    try {
        $lockHolder = Start-Process `
            -FilePath $lockPython `
            -ArgumentList @(('"{0}"' -f $lockHelper)) `
            -WorkingDirectory (Split-Path -Parent $lockUpdater) `
            -RedirectStandardOutput $lockStdout `
            -RedirectStandardError $lockStderr `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        Remove-Item Env:MEETING_ROOM_CI_LOCK_UPDATER -ErrorAction SilentlyContinue
        Remove-Item Env:MEETING_ROOM_CI_LOCK_PATH -ErrorAction SilentlyContinue
        Remove-Item Env:MEETING_ROOM_CI_LOCK_READY -ErrorAction SilentlyContinue
        Remove-Item Env:MEETING_ROOM_CI_LOCK_RELEASE -ErrorAction SilentlyContinue
    }
    try {
        for ($attempt = 0; $attempt -lt 100; $attempt++) {
            if (Test-Path -LiteralPath $lockReady -PathType Leaf) {
                break
            }
            if ($lockHolder.HasExited) {
                $lockHolder.Refresh()
                $details = @(
                    Get-Content -LiteralPath $lockStdout, $lockStderr `
                        -ErrorAction SilentlyContinue
                ) -join "`n"
                throw "包内 updater 未能持有并发锁，退出码 $($lockHolder.ExitCode)：$details"
            }
            Start-Sleep -Milliseconds 100
        }
        Assert-True (
            Test-Path -LiteralPath $lockReady -PathType Leaf
        ) '等待包内 updater 持有并发锁超时'
        $lockExit = Invoke-ZeroArgumentBat `
            -BatPath $successLauncher `
            -InstallRoot $successInstall
    }
    finally {
        Write-Utf8NoBom -Path $lockRelease -Content "release`n"
        if (-not $lockHolder.WaitForExit(10000)) {
            Stop-Process -Id $lockHolder.Id -Force -ErrorAction SilentlyContinue
            throw '包内 updater 并发锁辅助进程没有退出'
        }
    }
    Assert-True ($lockExit -eq 4) "锁冲突退出码不是 4：$lockExit"
    Assert-True (
        (Get-Content -LiteralPath (Join-Path $successProgram '版本.txt') -Raw).Trim() -eq
            $versionBeforeLock
    ) '锁冲突路径改写了版本文件'
    Assert-True (
        [string]::Equals(
            $dataBeforeLock,
            (Get-TreeFingerprint -Root $successData),
            [StringComparison]::Ordinal
        )
    ) '锁冲突路径改写了真实 data'
    Assert-InstalledPayload `
        -PackageRoot $successPackage `
        -InstallRoot $successInstall `
        -Payload target
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    $results.lock_exit_code = [int]$lockExit

    # 重新解压一份独立包，并让目标健康检查确定性失败。包清单同步更新，
    # 因而失败发生在 target 已应用后的真实健康检查，而不是前置哈希校验。
    $failureScenario = Join-Path $installsRoot '失败注入 中文 空格 & (1)'
    $failureInstall = Join-Path $failureScenario '会议室预约系统-单位局域网版-最终'
    $failurePackage = Join-Path $failureScenario '下载的修复包 副本'
    New-TestInstall -Root $failureInstall | Out-Null
    $failureLauncher = Expand-RepairPackage `
        -Artifact $artifact `
        -Destination $failurePackage
    Set-TargetHealthCheckFailure -PackageRoot $failurePackage
    Assert-PackageContract -PackageRoot $failurePackage

    $failureProgram = Join-Path $failureInstall '_程序文件'
    $failureData = Join-Path $failureProgram 'data'
    $failureDataBefore = Get-TreeFingerprint -Root $failureData
    Save-TreeState `
        -Root $failureData `
        -Destination (Join-Path $evidenceRoot 'failure-data-before.json')

    $failureExit = Invoke-ZeroArgumentBat `
        -BatPath $failureLauncher `
        -InstallRoot $failureInstall
    Assert-True ($failureExit -eq 1) "目标健康检查失败退出码不是 1：$failureExit"
    Assert-TreeUnchanged `
        -Root $failureData `
        -ExpectedFingerprint $failureDataBefore `
        -EvidencePath (Join-Path $evidenceRoot 'failure-data-after.json') `
        -Description '目标失败回退后的真实 data'
    Assert-InstalledPayload `
        -PackageRoot $failurePackage `
        -InstallRoot $failureInstall `
        -Payload baseline
    Assert-True (
        (Get-Content -LiteralPath (Join-Path $failureProgram '版本.txt') -Raw).Trim() -eq
            '1.0.1'
    ) '目标失败后没有回到冻结 V1.0.1'
    $failureStatePath = Join-Path $failureProgram '_V102覆盖更新状态.json'
    Assert-True (
        Test-Path -LiteralPath $failureStatePath -PathType Leaf
    ) '目标失败后缺少可恢复的修复状态'
    $failureState = Get-Content -LiteralPath $failureStatePath -Raw |
        ConvertFrom-Json
    Assert-True (
        [string]$failureState.stage -eq 'baseline_rollback_complete'
    ) "目标失败后的状态不是 baseline_rollback_complete：$($failureState.stage)"
    $failureLog = Get-LatestRepairLog -InstallRoot $failureInstall
    $failureText = [IO.File]::ReadAllText(
        $failureLog,
        (New-Object Text.UTF8Encoding($false, $true))
    )
    Write-Utf8NoBom `
        -Path (Join-Path $evidenceRoot 'target-failure.log') `
        -Content $failureText
    Assert-True (
        $failureText.Contains(
            '目标更新失败，受管程序已安全收敛到冻结 V1.0.1；真实 data 未改变'
        )
    ) '目标失败日志没有证明安全收敛到冻结 V1.0.1'
    $results.target_failure_exit_code = [int]$failureExit
    $results.target_failure_baseline_restored = $true

    Write-Utf8NoBom `
        -Path (Join-Path $evidenceRoot 'summary.json') `
        -Content (($results | ConvertTo-Json -Depth 8) + "`n")
    Write-Host 'Overlay update Windows integration passed.'
    Write-Host (
        'This elevated-runner CI does not validate standard-user Explorer -> ' +
        'UAC secure desktop; that remains a manual Windows 10/11 gate.'
    )
}
catch {
    Write-Utf8NoBom `
        -Path (Join-Path $evidenceRoot 'failure.txt') `
        -Content (($_ | Out-String) + "`n")
    Write-Utf8NoBom `
        -Path (Join-Path $evidenceRoot 'summary.partial.json') `
        -Content (($results | ConvertTo-Json -Depth 8) + "`n")
    throw
}
finally {
    $launcherLog = Join-Path ([IO.Path]::GetTempPath()) (
        'meetingroom_v102_repair_launcher.log'
    )
    if (Test-Path -LiteralPath $launcherLog -PathType Leaf) {
        Copy-Item `
            -LiteralPath $launcherLog `
            -Destination (Join-Path $evidenceRoot 'meetingroom_v102_repair_launcher.log') `
            -Force
    }
}
