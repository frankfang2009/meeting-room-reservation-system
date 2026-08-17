<#
V2 Windows 真实安装验收（T1 自动化层）。

在 GitHub Actions windows-latest（管理员会话、一次性虚拟机）上执行：
  1. 用真实顶层零参数 BAT 完成生产安装（固定 Program Files 根、DACL、
     HKLM 登记、SYSTEM 计划任务、LocalSubnet 防火墙）；
  2. 回环 /healthz 契约与 install_id；
  3. 首次设置 API → 服务重启为 LAN 绑定；
  4. 登录、bootstrap、创建预约、时段冲突 409、取消；
  5. 未认证公开大屏投影（字段白名单）；
  6. ② 立即备份 BAT：新备份 + sidecar、无 -wal/-shm/-journal/.part-* 伴随文件；
  7. ①/④ 客户启动停止 BAT：安装身份校验链与生命周期；
  8. 端口 8080 被他人占用时拒绝启动且不杀占用进程；
  9. 数据库损坏后 fail-closed 恢复状态、不重开首次设置、不重建数据库；
  10. DACL 边界：app 树 Users 只读执行，data/backups/logs 无 Users ACE。

本脚本不覆盖（T2 层，需要真实交互式 Windows 桌面）：
UAC 同意框、SmartScreen、真实重启、真实局域网第二设备、长时间运行、签名。
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$CandidateZip,
    [Parameter(Mandatory = $true)]
    [string]$WorkRoot
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Script:Base = "http://localhost:8080"
$Script:Version = (Get-Content -LiteralPath "v2/VERSION" -Raw -Encoding UTF8).Trim()
$Script:LauncherName = "安装V$Script:Version.bat"
$Script:InstallRoot = Join-Path $env:ProgramFiles "会议室预约系统V2"
$Script:ProgramDir = Join-Path $Script:InstallRoot "_程序文件"
$Script:DataDir = Join-Path $Script:ProgramDir "data"
$Script:BackupDir = Join-Path $Script:ProgramDir "backups"
$Script:ServiceLog = Join-Path $Script:ProgramDir "logs\service.log"
$Script:StartBat = "① 启动系统.bat"
$Script:BackupBat = "② 立即备份.bat"
$Script:StopBat = "④ 停止本次后台系统.bat"
$Script:MainTaskName = "会议室预约系统 V2"
$Script:BackupTaskName = "会议室预约系统 V2 每日备份"

function Write-Step([string]$Name) {
    Write-Host ""
    Write-Host "MRV2_T1=STEP:$Name"
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw "MRV2_T1=ASSERT_FAILED: $Message"
    }
}

function Invoke-CandidateBat {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$InputText = "",
        [string]$EntryName = $Script:LauncherName
    )
    $launcher = Join-Path $Root $EntryName
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        return @{ Code = 10; Output = "MRV2_T1=MISSING_ENTRY:$EntryName" }
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
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    if (-not $process.Start()) {
        return @{ Code = 15; Output = "MRV2_T1=CMD_START_FAILED" }
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
    throw "MRV2_T1=TIMEOUT: $Label"
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
    Write-Host "MRV2_T1=DIAGNOSTICS_BEGIN"
    foreach ($taskName in @($Script:MainTaskName, $Script:BackupTaskName)) {
        try {
            $task = Get-ScheduledTask -TaskPath "\" -TaskName $taskName -ErrorAction Stop
            $info = $task | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
            Write-Host ("task '{0}' state={1} lastResult={2}" -f $taskName, $task.State, $(if ($info) { $info.LastTaskResult } else { "?" }))
        }
        catch {
            Write-Host "task '$taskName' not found"
        }
    }
    if (Test-Path -LiteralPath $Script:ServiceLog -PathType Leaf) {
        Write-Host "--- service.log (tail 80) ---"
        Get-Content -LiteralPath $Script:ServiceLog -Tail 80 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    else {
        Write-Host "service.log not found at $Script:ServiceLog"
    }
    if (Test-Path -LiteralPath $Script:DataDir) {
        Write-Host "--- data dir ---"
        Get-ChildItem -LiteralPath $Script:DataDir -Force | ForEach-Object { Write-Host ("{0} {1}" -f $_.Length, $_.Name) }
    }
    Write-Host "MRV2_T1=DIAGNOSTICS_END"
}

try {
    Write-Step "preflight"
    Assert-True (Test-Path -LiteralPath $CandidateZip -PathType Leaf) "candidate zip not found: $CandidateZip"
    Assert-True (-not (Test-Path -LiteralPath $WorkRoot)) "work root already exists: $WorkRoot"
    Assert-True (-not (Test-Path -LiteralPath $Script:InstallRoot)) "previous install still present at $Script:InstallRoot"
    $occupied = @(Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue)
    Assert-True ($occupied.Count -eq 0) "port 8080 is already occupied before install"
    New-Item -ItemType Directory -Path $WorkRoot | Out-Null

    Write-Step "install"
    $formal = Join-Path $WorkRoot "formal"
    Expand-Archive -LiteralPath $CandidateZip -DestinationPath $formal
    $install = Invoke-CandidateBat $formal "YES"
    if ($install.Code -ne 0 -or $install.Output -notmatch [regex]::Escape("MRV2_GATE=PRODUCT_RC_0")) {
        throw "install BAT failed: code=$($install.Code); output tail=$($install.Output.Substring([Math]::Max(0, $install.Output.Length - 2000)))"
    }
    Write-Host "install BAT returned 0 with product marker"

    Write-Step "loopback-health"
    Wait-Until { $null -ne (Get-HealthJson) } 60 "service did not answer /healthz after install"
    $health = Get-HealthJson
    Assert-True ($health.ok -eq $true) "healthz ok is not true"
    Assert-True ([int]$health.product_generation -eq 2) "healthz product_generation is not 2"
    Assert-True ([string]$health.bind_mode -eq "loopback") "healthz bind_mode is not loopback before setup"
    Assert-True ([bool]($health.PSObject.Properties.Name -contains "setup_complete") -and $health.setup_complete -eq $false) "setup_complete is not false before setup"
    Assert-True ($health.install_id -cmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') "loopback healthz did not expose a valid install_id"
    $installId = [string]$health.install_id
    Write-Host "install_id = $installId"

    Write-Step "first-setup"
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $sessionState = Invoke-Api -Method GET -Path "/api/v1/session" -Session $session
    Assert-True ($sessionState.Status -eq 200) "GET /api/v1/session failed: $($sessionState.Status)"
    $csrf = [string]$sessionState.Json.csrfToken
    Assert-True (-not [string]::IsNullOrEmpty($csrf)) "csrfToken missing"
    $setupBody = @{
        admin     = @{
            username   = "admin"
            password   = "admin-pass-123"
            name       = "验收管理员"
            department = "验收部门"
        }
        rooms     = @(
            @{ name = "验收笔录室一" },
            @{ name = "验收笔录室二" }
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
    $health = Get-HealthJson
    Assert-True ([string]$health.install_id -ceq $installId) "install_id changed across the LAN restart"
    Write-Host "service restarted into LAN mode with stable install_id"

    Write-Step "login-bootstrap"
    $login = Invoke-Api -Method POST -Path "/api/v1/session" -Body @{ username = "admin"; password = "admin-pass-123" } -Session $session -CsrfToken $csrf
    Assert-True ($login.Status -eq 200) "login failed: $($login.Status) $($login.Json | ConvertTo-Json -Compress)"
    # 登录会轮换会话；与后端测试辅助一致，登录后重新获取 CSRF token。
    $sessionState = Invoke-Api -Method GET -Path "/api/v1/session" -Session $session
    Assert-True ($sessionState.Status -eq 200) "GET /api/v1/session after login failed: $($sessionState.Status)"
    $csrf = [string]$sessionState.Json.csrfToken
    Assert-True (-not [string]::IsNullOrEmpty($csrf)) "csrfToken missing after login"
    $bootstrap = Invoke-Api -Method GET -Path "/api/v1/bootstrap" -Session $session
    Assert-True ($bootstrap.Status -eq 200) "bootstrap failed: $($bootstrap.Status)"
    $rooms = @($bootstrap.Json.rooms)
    Assert-True ($rooms.Count -eq 2) "bootstrap rooms count is $($rooms.Count), expected 2"
    $roomId = [string]$rooms[0].id
    Assert-True (-not [string]::IsNullOrEmpty($roomId)) "bootstrap room id missing"
    Assert-True ([string]$bootstrap.Json.settings.workStart -eq "08:30") "bootstrap workStart mismatch"

    Write-Step "booking-smoke"
    $tomorrow = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
    $bookingBody = @{
        date      = $tomorrow
        roomId    = $roomId
        start     = "09:00"
        duration  = 60
        partyName = "验收当事人"
        caseNumber = "T1-SMOKE-001"
        purpose   = "验收用途"
        notes     = "T1 合成验收备注"
        tagId     = "tag-1"
    }
    $created = Invoke-Api -Method POST -Path "/api/v1/reservations" -Body $bookingBody -Session $session -CsrfToken $csrf
    Assert-True ($created.Status -eq 201) "create reservation failed: $($created.Status) $($created.Json | ConvertTo-Json -Compress)"
    $reservationId = [string]$created.Json.id
    $revision = [int]$created.Json.revision
    Assert-True (-not [string]::IsNullOrEmpty($reservationId)) "created reservation id missing"
    $conflict = Invoke-Api -Method POST -Path "/api/v1/reservations" -Body $bookingBody -Session $session -CsrfToken $csrf
    Assert-True ($conflict.Status -eq 409) "duplicate slot did not return 409: $($conflict.Status)"
    Assert-True ([string]$conflict.Json.error.code -eq "SLOT_CONFLICT") "duplicate slot error code is not SLOT_CONFLICT"
    $cancelled = Invoke-Api -Method POST -Path "/api/v1/reservations/$reservationId/cancel" -Body @{ expectedRevision = $revision } -Session $session -CsrfToken $csrf
    Assert-True ($cancelled.Status -eq 200) "cancel reservation failed: $($cancelled.Status) $($cancelled.Json | ConvertTo-Json -Compress)"
    Write-Host "create/conflict/cancel smoke passed"

    Write-Step "public-display"
    $publicSession = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $display = Invoke-Api -Method GET -Path "/api/v1/display" -Session $publicSession
    Assert-True ($display.Status -eq 200) "public display failed: $($display.Status)"
    Assert-True ($display.Json.PSObject.Properties.Name -contains "rooms") "public display response has no rooms"
    $displayRaw = [string]($display.Json | ConvertTo-Json -Depth 10 -Compress)
    foreach ($forbidden in @('"caseNumber"', '"purpose"', '"notes"', '"partyName"')) {
        Assert-True ($displayRaw -notlike "*$forbidden*") "public display leaked $forbidden"
    }
    Write-Host "public display allowlist projection passed"

    Write-Step "manual-backup"
    $before = @(Get-ChildItem -LiteralPath $Script:BackupDir -Filter "*.db" -File -ErrorAction SilentlyContinue).Count
    $backup = Invoke-CandidateBat $Script:InstallRoot " " $Script:BackupBat
    Assert-True ($backup.Code -eq 0) "manual backup BAT failed: code=$($backup.Code); output tail=$($backup.Output.Substring([Math]::Max(0, $backup.Output.Length - 2000)))"
    $after = @(Get-ChildItem -LiteralPath $Script:BackupDir -Filter "*.db" -File).Count
    Assert-True ($after -gt $before) "manual backup did not add a new .db (before=$before after=$after)"
    $sidecars = @(Get-ChildItem -LiteralPath $Script:BackupDir -Filter "*.json" -File)
    Assert-True ($sidecars.Count -ge 1) "backup sidecar json missing"
    $statusPath = Join-Path $Script:DataDir "backup-status.json"
    Assert-True (Test-Path -LiteralPath $statusPath -PathType Leaf) "backup-status.json missing"
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([string]$status.status -eq "succeeded") "backup-status.json status is $($status.status)"
    $companions = @(
        Get-ChildItem -LiteralPath $Script:BackupDir -Recurse -File -Force
        Get-ChildItem -LiteralPath $Script:DataDir -Recurse -File -Force
    ) | Where-Object { $_.Name -match '(-wal|-shm|-journal|\.part-)' }
    Assert-True ($companions.Count -eq 0) ("wal/journal companions left behind: " + (($companions | ForEach-Object FullName) -join "; "))
    Write-Host "manual backup produced db+sidecar with no companion files"

    Write-Step "stop-start-lifecycle"
    $stopped = Invoke-CandidateBat $Script:InstallRoot " " $Script:StopBat
    Assert-True ($stopped.Code -eq 0) "stop BAT failed: code=$($stopped.Code); output tail=$($stopped.Output.Substring([Math]::Max(0, $stopped.Output.Length - 2000)))"
    Wait-Until { $null -eq (Get-HealthJson) } 60 "service still answers /healthz after stop"
    $started = Invoke-CandidateBat $Script:InstallRoot " " $Script:StartBat
    Assert-True ($started.Code -eq 0) ("start BAT failed: code=" + $started.Code + "; output tail=" + $started.Output.Substring([Math]::Max(0, $started.Output.Length - 2000)))
    Wait-Until {
        $h = Get-HealthJson
        ($null -ne $h) -and ([string]$h.bind_mode -eq "lan")
    } 120 "service did not come back after start BAT"
    Write-Host "stop/start BAT lifecycle passed (identity chain verified by the BAT itself)"

    Write-Step "system-registration"
    $mainTask = Get-ScheduledTask -TaskPath "\" -TaskName $Script:MainTaskName
    Assert-True ([string]$mainTask.Principal.UserId -eq "SYSTEM") "main task principal is not SYSTEM"
    $backupTask = Get-ScheduledTask -TaskPath "\" -TaskName $Script:BackupTaskName
    Assert-True ([string]$backupTask.Principal.UserId -eq "SYSTEM") "backup task principal is not SYSTEM"
    Assert-True ([bool]$backupTask.Settings.StartWhenAvailable) "backup task does not set StartWhenAvailable"
    $registered = Get-ItemProperty -LiteralPath "HKLM:\Software\MeetingRoomReservationV2"
    Assert-True ([string]$registered.InstallRoot -eq $Script:InstallRoot) "HKLM InstallRoot mismatch"
    Assert-True ([string]$registered.InstallId -ceq $installId) "HKLM InstallId mismatch"
    foreach ($ruleName in @("会议室预约系统V2-手动", "会议室预约系统V2-后台")) {
        $rules = @(Get-NetFirewallRule -DisplayName $ruleName)
        Assert-True ($rules.Count -eq 1) "firewall rule '$ruleName' count is $($rules.Count)"
        $address = @($rules[0] | Get-NetFirewallAddressFilter)
        $port = @($rules[0] | Get-NetFirewallPortFilter)
        Assert-True ([string]$address[0].RemoteAddress -eq "LocalSubnet") "firewall rule '$ruleName' is not LocalSubnet"
        Assert-True ([string]$port[0].Protocol -eq "TCP" -and [string]$port[0].LocalPort -eq "8080") "firewall rule '$ruleName' is not TCP/8080"
    }
    Write-Host "scheduled tasks, HKLM registration and LocalSubnet firewall rules verified"

    Write-Step "port-conflict"
    $stopped = Invoke-CandidateBat $Script:InstallRoot " " $Script:StopBat
    Assert-True ($stopped.Code -eq 0) "stop BAT failed before port conflict test"
    Wait-Until { $null -eq (Get-HealthJson) } 60 "service still answers /healthz before port occupation"
    $listener = $null
    $deadline = (Get-Date).AddSeconds(30)
    while ($null -eq $listener -and (Get-Date) -lt $deadline) {
        try {
            $candidate = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, 8080)
            $candidate.Start()
            $listener = $candidate
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    Assert-True ($null -ne $listener) "could not occupy port 8080 with a probe listener"
    try {
        $conflicted = Invoke-CandidateBat $Script:InstallRoot " " $Script:StartBat
        Assert-True ($conflicted.Code -ne 0) "start BAT unexpectedly succeeded while port 8080 was occupied"
        $occupierAlive = $false
        try {
            $probe = [System.Net.Sockets.TcpClient]::new()
            $probe.Connect("127.0.0.1", 8080)
            $occupierAlive = $probe.Connected
            $probe.Close()
        }
        catch {
        }
        Assert-True ($occupierAlive) "the occupying listener was killed or stopped answering"
        Write-Host "start BAT refused to start with port 8080 occupied and left the occupier alive"
    }
    finally {
        $listener.Stop()
    }
    $restarted = Invoke-CandidateBat $Script:InstallRoot " " $Script:StartBat
    Assert-True ($restarted.Code -eq 0) ("start BAT failed after freeing port 8080: code=" + $restarted.Code + "; output tail=" + $restarted.Output.Substring([Math]::Max(0, $restarted.Output.Length - 2000)))
    Wait-Until {
        $h = Get-HealthJson
        ($null -ne $h) -and ([string]$h.bind_mode -eq "lan")
    } 120 "service did not come back after port conflict test"
    Write-Host "port conflict refusal and recovery passed"

    Write-Step "fail-closed-corruption"
    $stopped = Invoke-CandidateBat $Script:InstallRoot " " $Script:StopBat
    Assert-True ($stopped.Code -eq 0) "stop BAT failed before corruption test"
    Wait-Until { $null -eq (Get-HealthJson) } 60 "service still answers /healthz before corruption"
    $database = Get-ChildItem -LiteralPath $Script:DataDir -Filter "*.db" -File | Select-Object -First 1
    Assert-True ($null -ne $database) "no database file found under $Script:DataDir"
    foreach ($suffix in @("-wal", "-shm")) {
        $companion = "$($database.FullName)$suffix"
        if (Test-Path -LiteralPath $companion) {
            Remove-Item -LiteralPath $companion -Force
        }
    }
    $garbage = "MRV2-T1-CORRUPTION-PROBE not a sqlite database"
    [IO.File]::WriteAllBytes($database.FullName, [System.Text.Encoding]::UTF8.GetBytes($garbage))
    $corruptStart = Invoke-CandidateBat $Script:InstallRoot " " $Script:StartBat
    Assert-True ($corruptStart.Code -ne 0) "start BAT unexpectedly reported healthy with a corrupted database"
    Wait-Until {
        $h = Get-HealthJson
        ($null -ne $h) -and ([string]$h.status -eq "recovery")
    } 120 "service did not enter recovery state with a corrupted database"
    $health = Get-HealthJson
    Assert-True (-not [string]::IsNullOrEmpty([string]$health.recovery_code)) "loopback healthz did not expose recovery_code in recovery state"
    $replaySession = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $replayState = Invoke-Api -Method GET -Path "/api/v1/session" -Session $replaySession
    $replayCsrf = ""
    if ($replayState.Status -eq 200) {
        $replayCsrf = [string]$replayState.Json.csrfToken
    }
    $replay = Invoke-Api -Method POST -Path "/api/v1/setup/complete" -Body $setupBody -Session $replaySession -CsrfToken $replayCsrf
    Assert-True ($replay.Status -ne 201) "setup/complete was reopened (201) against a corrupted database"
    $bytes = [IO.File]::ReadAllBytes($database.FullName)
    $current = [System.Text.Encoding]::UTF8.GetString($bytes, 0, [Math]::Min($bytes.Length, $garbage.Length))
    Assert-True ($current -ceq $garbage) "corrupted database was rewritten instead of being preserved"
    Write-Host "fail-closed recovery state, closed setup and preserved database verified"
    try {
        Invoke-CandidateBat $Script:InstallRoot " " $Script:StopBat | Out-Null
    }
    catch {
        Write-Host "best-effort stop after corruption test did not complete; runner VM is disposable"
    }

    Write-Step "dacl-boundaries"
    $appAcl = (& icacls.exe "$Script:ProgramDir\app") -join "`n"
    Assert-True ($appAcl -match 'S-1-5-32-545') "app tree does not carry a Users (S-1-5-32-545) ACE"
    Assert-True ($appAcl -match '\(RX\)') "app tree Users ACE does not look read-and-execute"
    foreach ($private in @("data", "backups", "logs")) {
        $privateAcl = (& icacls.exe (Join-Path $Script:ProgramDir $private)) -join "`n"
        Assert-True ($privateAcl -notmatch 'S-1-5-32-545') "$private must not carry a Users (S-1-5-32-545) ACE"
    }
    Write-Host "DACL boundaries verified: app readable by Users, private dirs admin/SYSTEM only"

    Write-Host ""
    Write-Host "MRV2_T1=PASS"
    exit 0
}
catch {
    Dump-Diagnostics
    throw
}
