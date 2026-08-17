<#
V2 Windows 真实升级验收（T1 自动化层）。

在 GitHub Actions windows-latest（管理员会话、一次性虚拟机）上执行：
  1. 用冻结 V2.1.0 基线安装包完成真实全新安装（BAT、DACL、HKLM、任务、防火墙）；
  2. 首次设置 → LAN 重启 → 登录并创建一条业务预约（升级数据保留的对照物）；
  3. 等待服务备份补跑落定后，用当前 V2.2.0 累计升级包执行真实零参数 BAT 升级；
  4. 校验升级收尾：版本三处一致、install_id 不变、回执完成、无残留临时目录；
  5. 校验数据与身份：预约仍在、账号可登录、bootstrap 房间一致；
  6. 校验升级后的 DACL 边界：app/runtime 树 Users 只读执行，
     data/backups/logs 无 Users ACE（替换后必须重固化受保护 DACL）。

本脚本不覆盖（T2 层，需要真实交互式 Windows 桌面）：
UAC 同意框、SmartScreen、真实重启、真实局域网第二设备、长时间运行、签名。
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$BaselineZip,
    [Parameter(Mandatory = $true)]
    [string]$UpdateZip,
    [Parameter(Mandatory = $true)]
    [string]$WorkRoot
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Script:Base = "http://localhost:8080"
$Script:TargetVersion = (Get-Content -LiteralPath "v2/VERSION" -Raw -Encoding UTF8).Trim()
$Script:BaselineVersion = "2.1.0"
$Script:InstallRoot = Join-Path $env:ProgramFiles "会议室预约系统V2"
$Script:ProgramDir = Join-Path $Script:InstallRoot "_程序文件"
$Script:DataDir = Join-Path $Script:ProgramDir "data"
$Script:BackupDir = Join-Path $Script:ProgramDir "backups"

function Write-Step([string]$Name) {
    Write-Host ""
    Write-Host "MRV2_T1U=STEP:$Name"
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw "MRV2_T1U=ASSERT_FAILED: $Message"
    }
}

function Invoke-CandidateBat {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$EntryName,
        [string]$InputText = ""
    )
    $launcher = Join-Path $Root $EntryName
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        return @{ Code = 10; Output = "MRV2_T1U=MISSING_ENTRY:$EntryName" }
    }
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $env:ComSpec
    $start.Arguments = "/d /c `"`"$launcher`"`""
    $start.WorkingDirectory = $Root
    $start.UseShellExecute = $false
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.CreateNoWindow = $true
    $start.Environment["MEETING_ROOM_V2_INSTALL_NO_PAUSE"] = "1"
    $start.Environment["MEETING_ROOM_V2_UPDATE_NO_PAUSE"] = "1"
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    if (-not $process.Start()) {
        return @{ Code = 15; Output = "MRV2_T1U=CMD_START_FAILED" }
    }
    if ($InputText) {
        $process.StandardInput.WriteLine($InputText)
    }
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return @{ Code = $process.ExitCode; Output = $stdout + $stderr }
}

function Get-HealthJson {
    try {
        $response = Invoke-WebRequest -Uri "$Script:Base/healthz" -TimeoutSec 5 -SkipHttpErrorCheck
        if ([int]$response.StatusCode -eq 200) {
            return $response.Content | ConvertFrom-Json
        }
    }
    catch {
    }
    return $null
}

function Wait-Until([scriptblock]$Predicate, [int]$TimeoutSeconds, [string]$Label) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Predicate) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "MRV2_T1U=TIMEOUT: $Label"
}

function Invoke-Api {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        $Body,
        [Parameter(Mandatory = $true)][Microsoft.PowerShell.Commands.WebRequestSession]$Session,
        [string]$CsrfToken
    )
    $headers = @{}
    if ($CsrfToken) {
        $headers["X-CSRF-Token"] = $CsrfToken
    }
    $requestArguments = @{
        Uri                = "$Script:Base$Path"
        Method             = $Method
        WebSession         = $Session
        Headers            = $headers
        SkipHttpErrorCheck = $true
        TimeoutSec         = 30
    }
    if ($null -ne $Body) {
        $requestArguments["ContentType"] = "application/json; charset=utf-8"
        $requestArguments["Body"] = ($Body | ConvertTo-Json -Depth 8)
    }
    $response = Invoke-WebRequest @requestArguments
    $json = $null
    try {
        $json = $response.Content | ConvertFrom-Json
    }
    catch {
    }
    return @{ Status = [int]$response.StatusCode; Json = $json }
}

function Dump-Diagnostics {
    Write-Host "MRV2_T1U=DIAGNOSTICS_BEGIN"
    $updateLog = Get-ChildItem -LiteralPath (Join-Path $Script:ProgramDir "logs") -Filter "update-*.log" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if ($updateLog) {
        Write-Host "--- $($updateLog.Name) (tail 60) ---"
        Get-Content -LiteralPath $updateLog.FullName -Tail 60 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "no update-*.log found under logs"
    }
    $serviceLog = Join-Path $Script:ProgramDir "logs\service.log"
    if (Test-Path -LiteralPath $serviceLog -PathType Leaf) {
        Write-Host "--- service.log (tail 60) ---"
        Get-Content -LiteralPath $serviceLog -Tail 60 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    if (Test-Path -LiteralPath $Script:ProgramDir) {
        Write-Host "--- program dir ---"
        Get-ChildItem -LiteralPath $Script:ProgramDir -Force | ForEach-Object { Write-Host $_.Name }
    }
    $backupStatus = Join-Path $Script:DataDir "backup-status.json"
    if (Test-Path -LiteralPath $backupStatus -PathType Leaf) {
        Write-Host "--- backup-status.json ---"
        Get-Content -LiteralPath $backupStatus -Raw -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    Write-Host "MRV2_T1U=DIAGNOSTICS_END"
}

try {
    Write-Step "preflight"
    Assert-True (Test-Path -LiteralPath $BaselineZip -PathType Leaf) "baseline zip not found: $BaselineZip"
    Assert-True (Test-Path -LiteralPath $UpdateZip -PathType Leaf) "update zip not found: $UpdateZip"
    Assert-True ((Split-Path -Leaf $BaselineZip) -ceq "会议室预约系统-V$Script:BaselineVersion-安装包.zip") "baseline zip name mismatch"
    Assert-True ((Split-Path -Leaf $UpdateZip) -ceq "会议室预约系统-V$Script:TargetVersion-累计升级包.zip") "update zip name mismatch"
    Assert-True (-not (Test-Path -LiteralPath $WorkRoot)) "work root already exists: $WorkRoot"
    Assert-True (-not (Test-Path -LiteralPath $Script:InstallRoot)) "previous install still present at $Script:InstallRoot"
    $occupied = @(Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue)
    Assert-True ($occupied.Count -eq 0) "port 8080 is already occupied before baseline install"
    New-Item -ItemType Directory -Path $WorkRoot | Out-Null

    Write-Step "install-baseline"
    $baselineFormal = Join-Path $WorkRoot "baseline"
    Expand-Archive -LiteralPath $BaselineZip -DestinationPath $baselineFormal
    $install = Invoke-CandidateBat $baselineFormal "安装V$Script:BaselineVersion.bat" "YES"
    if ($install.Code -ne 0 -or $install.Output -notmatch [regex]::Escape("MRV2_GATE=PRODUCT_RC_0")) {
        throw "baseline install BAT failed: code=$($install.Code); output tail=$($install.Output.Substring([Math]::Max(0, $install.Output.Length - 2000)))"
    }
    Wait-Until { $null -ne (Get-HealthJson) } 60 "baseline service did not answer /healthz"
    $health = Get-HealthJson
    Assert-True ($health.ok -eq $true -and [int]$health.product_generation -eq 2) "baseline healthz identity invalid"
    $installId = [string]$health.install_id
    Write-Host "baseline install_id = $installId"

    Write-Step "first-setup-and-business-data"
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $sessionState = Invoke-Api -Method GET -Path "/api/v1/session" -Session $session
    $csrf = [string]$sessionState.Json.csrfToken
    $setupBody = @{
        admin     = @{
            username   = "admin"
            password   = "admin-pass-123"
            name       = "升级验收管理员"
            department = "验收部门"
        }
        rooms     = @(
            @{ name = "升级笔录室一" },
            @{ name = "升级笔录室二" }
        )
        workStart = "08:30"
        workEnd   = "17:30"
    }
    $setup = Invoke-Api -Method POST -Path "/api/v1/setup/complete" -Body $setupBody -Session $session -CsrfToken $csrf
    Assert-True ($setup.Status -eq 201) "setup/complete did not return 201: $($setup.Status) $($setup.Json | ConvertTo-Json -Compress)"
    Wait-Until {
        $h = Get-HealthJson
        ($null -ne $h) -and ([string]$h.bind_mode -eq "lan") -and ($h.setup_complete -eq $true)
    } 120 "service did not restart into LAN mode after setup"
    $login = Invoke-Api -Method POST -Path "/api/v1/session" -Body @{ username = "admin"; password = "admin-pass-123" } -Session $session -CsrfToken $csrf
    Assert-True ($login.Status -eq 200) "login failed: $($login.Status)"
    $sessionState = Invoke-Api -Method GET -Path "/api/v1/session" -Session $session
    $csrf = [string]$sessionState.Json.csrfToken
    $bootstrap = Invoke-Api -Method GET -Path "/api/v1/bootstrap" -Session $session
    Assert-True ($bootstrap.Status -eq 200) "bootstrap failed: $($bootstrap.Status)"
    $roomId = [string]@($bootstrap.Json.rooms)[0].id
    $tomorrow = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
    $created = Invoke-Api -Method POST -Path "/api/v1/reservations" -Body @{
        date       = $tomorrow
        roomId     = $roomId
        start      = "09:00"
        duration   = 60
        partyName  = "升级验收当事人"
        caseNumber = "T1U-UPGRADE-001"
        purpose    = "升级数据保留对照"
        tagId      = "tag-1"
    } -Session $session -CsrfToken $csrf
    Assert-True ($created.Status -eq 201) "create reservation failed: $($created.Status) $($created.Json | ConvertTo-Json -Compress)"
    $reservationId = [string]$created.Json.id
    Write-Host "created reservation $reservationId as the data-preservation probe"

    Write-Step "wait-backup-catch-up"
    # 更新器会在停服前执行一次在线备份；先等服务自身补跑完成并释放维护锁，
    # 避免在线备份与补跑竞争 maintenance.lock。
    $statusPath = Join-Path $Script:DataDir "backup-status.json"
    $lockPath = Join-Path $Script:DataDir "maintenance.lock"
    Wait-Until {
        if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
            return $false
        }
        try {
            $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            return $false
        }
        return ([string]$status.status -eq "succeeded")
    } 180 "service backup catch-up did not reach succeeded"
    Wait-Until { -not (Test-Path -LiteralPath $lockPath -PathType Leaf) } 60 "maintenance lock still held before upgrade"
    Start-Sleep -Seconds 3

    Write-Step "run-cumulative-update"
    $updateFormal = Join-Path $WorkRoot "update"
    Expand-Archive -LiteralPath $UpdateZip -DestinationPath $updateFormal
    # 服务保持运行：更新器必须自行停服、替换、重固化 DACL、健康检查并恢复运行状态。
    $upgrade = Invoke-CandidateBat $updateFormal "升级V$Script:TargetVersion.bat" "YES"
    if ($upgrade.Code -ne 0 -or $upgrade.Output -notmatch [regex]::Escape("MRV2_UPDATER_RESULT=0") -or $upgrade.Output -notmatch [regex]::Escape("MRV2_UPDATE_GATE=PRODUCT_RC_0")) {
        throw "upgrade BAT failed: code=$($upgrade.Code); output tail=$($upgrade.Output.Substring([Math]::Max(0, $upgrade.Output.Length - 3000)))"
    }
    Write-Host "upgrade BAT returned 0 with product markers"

    Write-Step "verify-version-and-identity"
    Assert-True ((Get-Content -LiteralPath (Join-Path $Script:ProgramDir "版本.txt") -Raw -Encoding UTF8).Trim() -ceq $Script:TargetVersion) "版本.txt is not $Script:TargetVersion"
    $installInfo = Get-Content -LiteralPath (Join-Path $Script:DataDir "install.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([string]$installInfo.installed_version -ceq $Script:TargetVersion) "install.json installed_version is not $Script:TargetVersion"
    Assert-True ([string]$installInfo.install_id -ceq $installId) "install_id changed across the upgrade"
    $releaseManifest = Get-Content -LiteralPath (Join-Path $Script:ProgramDir "release-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([string]$releaseManifest.kind -ceq "v2-cumulative-update") "release-manifest.json kind is not v2-cumulative-update"
    Assert-True ([string]$releaseManifest.version -ceq $Script:TargetVersion) "release-manifest.json version is not $Script:TargetVersion"
    $receiptPath = Join-Path $Script:ProgramDir "update-receipt.json"
    Assert-True (Test-Path -LiteralPath $receiptPath -PathType Leaf) "update-receipt.json missing"
    $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([string]$receipt.stage -ceq "complete") "update receipt stage is $($receipt.stage)"
    Assert-True ([string]$receipt.source_version -ceq $Script:BaselineVersion) "receipt source_version is $($receipt.source_version)"
    $leftovers = @(Get-ChildItem -LiteralPath $Script:ProgramDir -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like ".update-displaced-*" -or $_.Name -like ".update-staging-*" })
    Assert-True ($leftovers.Count -eq 0) ("upgrade left temp dirs behind: " + (($leftovers | ForEach-Object Name) -join "; "))

    Write-Step "verify-service-and-data"
    Wait-Until {
        $h = Get-HealthJson
        ($null -ne $h) -and ([string]$h.bind_mode -eq "lan") -and ($h.setup_complete -eq $true)
    } 120 "service did not come back after the upgrade"
    $health = Get-HealthJson
    Assert-True ([string]$health.install_id -ceq $installId) "install_id changed in healthz after the upgrade"
    $detail = Invoke-Api -Method GET -Path "/api/v1/reservations/$reservationId" -Session $session
    Assert-True ($detail.Status -eq 200) "reservation $reservationId did not survive the upgrade: $($detail.Status)"
    Assert-True ([string]$detail.Json.caseNumber -ceq "T1U-UPGRADE-001") "survived reservation caseNumber mismatch"
    $bootstrapAfter = Invoke-Api -Method GET -Path "/api/v1/bootstrap" -Session $session
    Assert-True ($bootstrapAfter.Status -eq 200) "bootstrap failed after the upgrade: $($bootstrapAfter.Status)"
    Assert-True (@($bootstrapAfter.Json.rooms).Count -eq 2) "rooms did not survive the upgrade"
    Write-Host "business data, login session and rooms survived the upgrade"

    Write-Step "dacl-boundaries-after-upgrade"
    function Test-AclHasUsersAce([string]$AclText) {
        return ($AclText -match 'S-1-5-32-545') -or ($AclText -match 'BUILTIN\\Users:') -or ($AclText -match '(^|[\r\n])Users:')
    }
    foreach ($name in @("app", "runtime")) {
        $aclText = (& icacls.exe (Join-Path $Script:ProgramDir $name)) -join "`n"
        Assert-True (Test-AclHasUsersAce $aclText) "$name tree does not carry a Users (S-1-5-32-545) ACE after upgrade: $aclText"
        Assert-True ($aclText -match '\(RX\)') "$name tree Users ACE does not look read-and-execute after upgrade: $aclText"
    }
    foreach ($private in @("data", "backups", "logs")) {
        $privateAcl = (& icacls.exe (Join-Path $Script:ProgramDir $private)) -join "`n"
        Assert-True (-not (Test-AclHasUsersAce $privateAcl)) "$private must not carry a Users (S-1-5-32-545) ACE after upgrade: $privateAcl"
    }
    Write-Host "DACL boundaries re-verified after the cumulative upgrade"

    Write-Host ""
    Write-Host "MRV2_T1U=PASS"
    exit 0
}
catch {
    Dump-Diagnostics
    throw
}
