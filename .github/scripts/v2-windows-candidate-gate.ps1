param(
    [Parameter(Mandatory = $true)]
    [string]$CandidateZip,
    [Parameter(Mandatory = $true)]
    [string]$WorkRoot
)

$ErrorActionPreference = "Stop"
$version = (Get-Content -LiteralPath "v2/VERSION" -Raw -Encoding UTF8).Trim()
$artifactName = "会议室预约系统-V$version-安装包.zip"
$launcherName = "安装V$version.bat"
$guideName = "安装说明.txt"
$toolName = "_V2安装工具"

function Invoke-CandidateBat {
    param(
        [Parameter(Mandatory = $true)] [string]$Root,
        [string]$InputText = "",
        [string]$EntryName = $launcherName
    )
    $launcher = Join-Path $Root $EntryName
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        return @{ Code = 10; Output = "MRV2_GATE=MISSING_LAUNCHER" }
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
        return @{ Code = 15; Output = "MRV2_GATE=CMD_START_FAILED" }
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

function Assert-GateResult {
    param(
        [hashtable]$Result,
        [int]$Code,
        [string]$Marker,
        [string]$Label
    )
    if ($Result.Code -ne $Code -or $Result.Output -notmatch [regex]::Escape($Marker)) {
        throw "$Label gate mismatch: code=$($Result.Code), output=$($Result.Output)"
    }
    Write-Host "$Label => code $Code / $Marker"
}

if (-not (Test-Path -LiteralPath $CandidateZip -PathType Leaf)) {
    Write-Error "MRV2_GATE=MISSING_CANDIDATE_ZIP"
    exit 9
}
if ([IO.Path]::GetFileName($CandidateZip) -ne $artifactName) {
    Write-Error "MRV2_GATE=WRONG_CANDIDATE_NAME"
    exit 9
}
if (Test-Path -LiteralPath $WorkRoot) {
    Write-Error "MRV2_GATE=WORK_ROOT_ALREADY_EXISTS"
    exit 9
}

$formal = Join-Path $WorkRoot "formal"
New-Item -ItemType Directory -Path $formal | Out-Null
Expand-Archive -LiteralPath $CandidateZip -DestinationPath $formal
$topNames = @(Get-ChildItem -LiteralPath $formal | ForEach-Object Name | Sort-Object)
$expectedTop = @($toolName, $guideName, $launcherName) | Sort-Object
if (($topNames -join "`n") -ne ($expectedTop -join "`n")) {
    Write-Error "MRV2_GATE=INVALID_TOP_LEVEL"
    exit 9
}
foreach ($required in @(
    $launcherName,
    $guideName,
    "$toolName\manifest.json",
    "$toolName\app\install.py",
    "$toolName\app\installer_core.py",
    "$toolName\runtime\python.exe"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $formal $required))) {
        Write-Error "MRV2_GATE=MISSING_REQUIRED_STRUCTURE:$required"
        exit 9
    }
}

$missingLauncher = Join-Path $WorkRoot "missing-launcher"
Copy-Item -LiteralPath $formal -Destination $missingLauncher -Recurse
Remove-Item -LiteralPath (Join-Path $missingLauncher $launcherName)
Assert-GateResult (Invoke-CandidateBat $missingLauncher) 10 "MRV2_GATE=MISSING_LAUNCHER" "missing launcher"

$missingTool = Join-Path $WorkRoot "missing-tool"
Copy-Item -LiteralPath $formal -Destination $missingTool -Recurse
Rename-Item -LiteralPath (Join-Path $missingTool $toolName) -NewName "_hidden-tool"
Assert-GateResult (Invoke-CandidateBat $missingTool) 11 "MRV2_GATE=MISSING_TOOL_DIR" "missing tool"

$missingPython = Join-Path $WorkRoot "missing-python"
Copy-Item -LiteralPath $formal -Destination $missingPython -Recurse
Remove-Item -LiteralPath (Join-Path $missingPython "$toolName\runtime\python.exe")
Assert-GateResult (Invoke-CandidateBat $missingPython) 12 "MRV2_GATE=MISSING_RUNTIME_PYTHON" "missing runtime Python"

$missingProduct = Join-Path $WorkRoot "missing-product"
Copy-Item -LiteralPath $formal -Destination $missingProduct -Recurse
Remove-Item -LiteralPath (Join-Path $missingProduct "$toolName\app\install.py")
Assert-GateResult (Invoke-CandidateBat $missingProduct) 13 "MRV2_GATE=MISSING_PRODUCT_INPUT" "missing product input"

$badPython = Join-Path $WorkRoot "bad-python"
Copy-Item -LiteralPath $formal -Destination $badPython -Recurse
[IO.File]::WriteAllBytes(
    (Join-Path $badPython "$toolName\runtime\python.exe"),
    [byte[]](0x4d, 0x5a, 0x00, 0x00)
)
Assert-GateResult (Invoke-CandidateBat $badPython) 14 "MRV2_GATE=PYTHON_START_FAILED" "Python startup"

$productFailure = Join-Path $WorkRoot "product-failure"
Copy-Item -LiteralPath $formal -Destination $productFailure -Recurse
Add-Content -LiteralPath (Join-Path $productFailure "$toolName\manifest.json") -Value "tamper"
Assert-GateResult (Invoke-CandidateBat $productFailure) 1 "MRV2_GATE=PRODUCT_RC_1" "product-defined rejection"

$installed = Invoke-CandidateBat $formal "YES"
Assert-GateResult $installed 0 "MRV2_GATE=PRODUCT_RC_0" "formal candidate"
$health = Invoke-RestMethod -Uri "http://127.0.0.1:8080/healthz" -TimeoutSec 15
if (-not $health.ok -or $health.product_generation -ne 2 -or $health.bind_mode -ne "loopback") {
    throw "formal candidate health contract mismatch: $($health | ConvertTo-Json -Compress)"
}
Write-Host "formal candidate health => generation 2 / loopback"

$installRoot = Join-Path $env:ProgramFiles "会议室预约系统V2"
$stopEntryName = "④ 停止本次后台系统.bat"
$stopEntry = Join-Path $installRoot $stopEntryName
if (-not (Test-Path -LiteralPath $stopEntry -PathType Leaf)) {
    throw "installed service cleanup entry is missing: $stopEntry"
}

# T2-B4 回归：所有 BAT 通过单行 powershell -Command 内联执行 PowerShell；
# 任何一行的语法错误都会让该入口在客户机上完全失效（⑥ 恢复即真实案例）。
# 这里对解压包与已安装根的每个 -Command 行做 ScriptBlock 语法解析冒烟。
foreach ($scanRoot in @($formal, $installRoot)) {
    $batEntries = @(Get-ChildItem -LiteralPath $scanRoot -Recurse -Filter "*.bat" -File -ErrorAction SilentlyContinue)
    if ($batEntries.Count -eq 0) { throw "no bat entries found under $scanRoot" }
    $smokeCount = 0
    foreach ($bat in $batEntries) {
        $lineNumber = 0
        $allLines = @(Get-Content -LiteralPath $bat.FullName -Encoding UTF8)
        foreach ($line in $allLines) {
            $lineNumber++
            if ($line -notmatch '-Command "') { continue }
            $marker = '-Command "'
            $payload = $line.Substring($line.IndexOf($marker) + $marker.Length).TrimEnd('"')
            try {
                $null = [ScriptBlock]::Create($payload)
            }
            catch {
                throw ("BAT embedded PowerShell parse failed: {0}:{1} : {2}" -f $bat.Name, $lineNumber, $_.Exception.Message)
            }
            $smokeCount++
        }
        $embedMarker = [array]::IndexOf($allLines, "# MRV2-POWERSHELL-BEGIN")
        if ($embedMarker -ge 0 -and $embedMarker -lt $allLines.Count - 1) {
            $embedded = ($allLines[($embedMarker + 1)..($allLines.Count - 1)] -join [Environment]::NewLine)
            try {
                $null = [ScriptBlock]::Create($embedded)
            }
            catch {
                throw ("BAT embedded block parse failed: {0} (after # MRV2-POWERSHELL-BEGIN) : {1}" -f $bat.Name, $_.Exception.Message)
            }
            $smokeCount++
        }
    }
    Write-Host ("embedded PowerShell parse smoke => {0} command lines OK under {1}" -f $smokeCount, $scanRoot)
}

$stopped = Invoke-CandidateBat $installRoot " " $stopEntryName
if ($stopped.Code -ne 0) {
    $serviceLog = Join-Path $installRoot "_程序文件\logs\service.log"
    if (Test-Path -LiteralPath $serviceLog -PathType Leaf) {
        Write-Host (Get-Content -LiteralPath $serviceLog -Raw -Encoding UTF8)
    }
    throw "installed service cleanup stop failed: code=$($stopped.Code), output=$($stopped.Output)"
}
Write-Host "formal candidate cleanup => customer stop entry returned code 0"
