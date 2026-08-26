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
$Script:SanitizedDiagnosticsOnly = $false

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
    if ($Script:SanitizedDiagnosticsOnly) {
        Write-Host "MRV2_T1=DIAGNOSTICS_REDACTED"
        Write-Host "MRV2_T1=DIAGNOSTICS_END"
        return
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
    $backupStatus = Join-Path $Script:DataDir "backup-status.json"
    if (Test-Path -LiteralPath $backupStatus -PathType Leaf) {
        Write-Host "--- backup-status.json ---"
        Get-Content -LiteralPath $backupStatus -Raw -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    $backupLog = Join-Path $Script:ProgramDir "logs\backup.log"
    if (Test-Path -LiteralPath $backupLog -PathType Leaf) {
        Write-Host "--- backup.log (tail 40) ---"
        Get-Content -LiteralPath $backupLog -Tail 40 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
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
    $display = Invoke-Api -Method GET -Path "/api/v1/display/today" -Session $publicSession
    Assert-True ($display.Status -eq 200) "public display failed: $($display.Status)"
    Assert-True ($display.Json.PSObject.Properties.Name -contains "rooms") "public display response has no rooms"
    $displayRaw = [string]($display.Json | ConvertTo-Json -Depth 10 -Compress)
    foreach ($forbidden in @('"caseNumber"', '"purpose"', '"notes"', '"partyName"')) {
        Assert-True ($displayRaw -notlike "*$forbidden*") "public display leaked $forbidden"
    }
    Write-Host "public display allowlist projection passed"

    Write-Step "manual-backup"
    # 服务在启动与 LAN 重启时会补跑备份 worker 并持有 maintenance.lock；
    # 先等补跑落定（状态 succeeded 且锁释放），避免人工备份与补跑竞争维护锁。
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
    Wait-Until { -not (Test-Path -LiteralPath $lockPath -PathType Leaf) } 60 "maintenance lock still held before manual backup"
    Start-Sleep -Seconds 3
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
    # T2-B9 回归：常驻服务任务必须显式禁用电池策略（笔记本部署电源波动不得停服务）。
    # 读回对象只有 DisallowStartIfOnBatteries/StopIfGoingOnBatteries（负面语义属性）。
    Assert-True ([bool]$mainTask.Settings.DisallowStartIfOnBatteries -eq $false) "main task disallows start on batteries"
    Assert-True ([bool]$mainTask.Settings.StopIfGoingOnBatteries -eq $false) "main task stops on batteries"
    $backupTask = Get-ScheduledTask -TaskPath "\" -TaskName $Script:BackupTaskName
    Assert-True ([string]$backupTask.Principal.UserId -eq "SYSTEM") "backup task principal is not SYSTEM"
    Assert-True ([bool]$backupTask.Settings.DisallowStartIfOnBatteries -eq $false) "backup task disallows start on batteries"
    Assert-True ([bool]$backupTask.Settings.StopIfGoingOnBatteries -eq $false) "backup task stops on batteries"
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

    Write-Step "dacl-boundaries"
    $Script:SanitizedDiagnosticsOnly = $true
    $systemSid = "S-1-5-18"
    $adminSid = "S-1-5-32-544"
    $usersSid = "S-1-5-32-545"
    $fullControl = [int64][System.Security.AccessControl.FileSystemRights]::FullControl
    $readExecute = [int64][System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    $synchronize = [int64][System.Security.AccessControl.FileSystemRights]::Synchronize

    function Get-RootAclSummary([string]$Name, [string]$Path, [bool]$AllowUsers) {
        $acl = Get-Acl -LiteralPath $Path
        $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        $rights = @{}
        foreach ($rule in $acl.Access) {
            if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
                continue
            }
            $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
            if (-not $rights.ContainsKey($sid)) {
                $rights[$sid] = 0
            }
            $rights[$sid] = [int64]$rights[$sid] -bor [int64]$rule.FileSystemRights
        }
        Assert-True ([bool]$acl.AreAccessRulesProtected) "$Name ACL inherits from its parent"
        Assert-True ($ownerSid -eq $adminSid) "$Name ACL owner SID is not Administrators"
        foreach ($sid in @($systemSid, $adminSid)) {
            Assert-True ($rights.ContainsKey($sid) -and (($rights[$sid] -band $fullControl) -eq $fullControl)) "$Name ACL lacks required full-control SID"
        }
        if ($AllowUsers) {
            $allowed = $readExecute -bor $synchronize
            Assert-True ($rights.ContainsKey($usersSid)) "$Name ACL lacks Users read-and-execute"
            Assert-True (($rights[$usersSid] -band $readExecute) -eq $readExecute) "$Name Users rights omit read-and-execute"
            Assert-True (($rights[$usersSid] -band (-bnot $allowed)) -eq 0) "$Name Users rights exceed read-and-execute"
        }
        else {
            Assert-True (-not $rights.ContainsKey($usersSid)) "$Name ACL grants Users access"
        }
        return [PSCustomObject]@{
            root      = $Name
            protected = [bool]$acl.AreAccessRulesProtected
            ownerSid  = $ownerSid
            users     = $(if ($AllowUsers) { "RX" } else { "NONE" })
        }
    }

    $aclSummaries = @()
    foreach ($public in @('app', 'runtime')) {
        $aclSummaries += Get-RootAclSummary $public (Join-Path $Script:ProgramDir $public) $true
    }
    foreach ($private in @('data', 'backups', 'logs')) {
        $aclSummaries += Get-RootAclSummary $private (Join-Path $Script:ProgramDir $private) $false
    }
    Write-Host ("MRV2_T1=ACL_SUMMARY:" + ($aclSummaries | ConvertTo-Json -Compress))

    $probeRoot = Join-Path $WorkRoot "standard-user-acl-probe"
    New-Item -ItemType Directory -Path $probeRoot | Out-Null
    $probeAcl = Get-Acl -LiteralPath $probeRoot
    $probeAcl.SetAccessRuleProtection($true, $false)
    $probeAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
        [System.Security.Principal.SecurityIdentifier]::new($usersSid),
        [System.Security.AccessControl.FileSystemRights]::Modify,
        [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    ))
    Set-Acl -LiteralPath $probeRoot -AclObject $probeAcl

    $probeFiles = @{}
    foreach ($private in @('data', 'backups', 'logs')) {
        $probeFile = Join-Path (Join-Path $Script:ProgramDir $private) "standard-user-read-probe.txt"
        Set-Content -LiteralPath $probeFile -Value "synthetic ACL probe" -Encoding ASCII
        $probeFiles[$private] = $probeFile
    }
    $probeScript = Join-Path $probeRoot "probe.ps1"
    $probeOutput = Join-Path $probeRoot "result.txt"
    @'
param([string]$ProgramDir, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
$results = @()
$failed = $false
foreach ($name in @('data', 'backups', 'logs')) {
    $root = Join-Path $ProgramDir $name
    $directoryDenied = $false
    try {
        [void][IO.Directory]::GetFileSystemEntries($root)
    }
    catch [System.UnauthorizedAccessException] {
        $directoryDenied = $true
    }
    $fileDenied = $false
    try {
        [void][IO.File]::ReadAllText((Join-Path $root 'standard-user-read-probe.txt'))
    }
    catch [System.UnauthorizedAccessException] {
        $fileDenied = $true
    }
    $directoryResult = $(if ($directoryDenied) { 'PASS' } else { 'FAIL' })
    $fileResult = $(if ($fileDenied) { 'PASS' } else { 'FAIL' })
    $results += "$name`:directory=$directoryResult;file=$fileResult"
    if (-not $directoryDenied -or -not $fileDenied) {
        $failed = $true
    }
}
[IO.File]::WriteAllLines($OutputPath, $results, [Text.Encoding]::ASCII)
if ($failed) { exit 1 }
exit 0
'@ | Set-Content -LiteralPath $probeScript -Encoding UTF8

    $probeUser = "MRV2AclProbe"
    $probePasswordText = "Mrv2!" + [Guid]::NewGuid().ToString("N") + "aA1"
    $probePassword = ConvertTo-SecureString $probePasswordText -AsPlainText -Force
    Assert-True ($null -eq (Get-LocalUser -Name $probeUser -ErrorAction SilentlyContinue)) "standard ACL probe account already exists"
    try {
        $probeAccount = New-LocalUser -Name $probeUser -Password $probePassword -AccountNeverExpires -PasswordNeverExpires
        $usersGroup = Get-LocalGroup -SID ([System.Security.Principal.SecurityIdentifier]::new($usersSid))
        $isUsersMember = @(
            Get-LocalGroupMember -Group $usersGroup | Where-Object {
                $_.SID.Value -eq $probeAccount.SID.Value
            }
        ).Count -eq 1
        if (-not $isUsersMember) {
            Add-LocalGroupMember -Group $usersGroup -Member $probeAccount
        }
        Assert-True (@(Get-LocalGroupMember -Group $usersGroup | Where-Object { $_.SID.Value -eq $probeAccount.SID.Value }).Count -eq 1) "standard ACL probe account is not a Users member"
        $administratorsGroup = Get-LocalGroup -SID ([System.Security.Principal.SecurityIdentifier]::new($adminSid))
        Assert-True (@(Get-LocalGroupMember -Group $administratorsGroup | Where-Object { $_.SID.Value -eq $probeAccount.SID.Value }).Count -eq 0) "standard ACL probe account unexpectedly has administrator membership"
        $credential = [Management.Automation.PSCredential]::new("$env:COMPUTERNAME\$probeUser", $probePassword)
        $escapedScript = $probeScript.Replace("'", "''")
        $escapedProgram = $Script:ProgramDir.Replace("'", "''")
        $escapedOutput = $probeOutput.Replace("'", "''")
        $command = "& '$escapedScript' -ProgramDir '$escapedProgram' -OutputPath '$escapedOutput'"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $probeProcess = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList "-NoProfile -NonInteractive -EncodedCommand $encoded" -Credential $credential -LoadUserProfile -Wait -PassThru
        $probeResults = @(Get-Content -LiteralPath $probeOutput -Encoding ASCII)
        $probeResults | ForEach-Object { Write-Host "MRV2_T1=STANDARD_USER_ACL:$_" }
        Assert-True ($probeProcess.ExitCode -eq 0) "standard user could access a private root"
        Assert-True ($probeResults.Count -eq 3) "standard-user ACL probe returned incomplete results"
    }
    finally {
        Remove-LocalUser -Name $probeUser -ErrorAction SilentlyContinue
        foreach ($probeFile in $probeFiles.Values) {
            Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
        }
        $probePasswordText = $null
        $probePassword = $null
        $credential = $null
    }

    $stoppedForSystemStart = Invoke-CandidateBat $Script:InstallRoot " " $Script:StopBat
    Assert-True ($stoppedForSystemStart.Code -eq 0) "stop BAT failed before SYSTEM task start proof"
    Wait-Until { $null -eq (Get-HealthJson) } 60 "service still answers before SYSTEM task start proof"
    $mainTask = Get-ScheduledTask -TaskPath "\" -TaskName $Script:MainTaskName
    Assert-True ([string]$mainTask.Principal.UserId -eq "SYSTEM") "main task principal is not SYSTEM during ACL proof"
    Enable-ScheduledTask -InputObject $mainTask | Out-Null
    Start-ScheduledTask -InputObject $mainTask
    Wait-Until {
        $healthAfterAcl = Get-HealthJson
        ($null -ne $healthAfterAcl) -and
        ($healthAfterAcl.ok -eq $true) -and
        ($healthAfterAcl.bind_mode -eq "lan") -and
        ($healthAfterAcl.setup_complete -eq $true) -and
        ($healthAfterAcl.install_id -ceq $installId) -and
        ($healthAfterAcl.product_generation -eq 2)
    } 120 "application did not return the complete healthy identity through the SYSTEM task after ACL checks"
    Write-Host "DACL boundaries and SYSTEM-task startup verified"
    $Script:SanitizedDiagnosticsOnly = $false

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

    Write-Host ""
    Write-Host "MRV2_T1=PASS"
    exit 0
}
catch {
    Dump-Diagnostics
    throw
}
