param(
    [Parameter(Mandatory = $true)]
    [string]$UpdateZip,
    [Parameter(Mandatory = $true)]
    [string]$WorkRoot
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

$version = (Get-Content -LiteralPath 'v2/VERSION' -Raw -Encoding UTF8).Trim()
$expectedName = "会议室预约系统-V$version-累计升级包.zip"
Assert-True ((Split-Path -Leaf $UpdateZip) -ceq $expectedName) "累计升级包名称不一致：$UpdateZip"
Assert-True (Test-Path -LiteralPath $UpdateZip -PathType Leaf) "累计升级包不存在：$UpdateZip"

$shaPath = "$UpdateZip.sha256"
$manifestPath = "$UpdateZip.manifest.json"
Assert-True (Test-Path -LiteralPath $shaPath -PathType Leaf) '累计升级包缺少 SHA-256 侧车'
Assert-True (Test-Path -LiteralPath $manifestPath -PathType Leaf) '累计升级包缺少 manifest 侧车'

$expectedSha = ((Get-Content -LiteralPath $shaPath -Raw -Encoding UTF8).Trim() -split '\s+')[0].ToLowerInvariant()
$actualSha = (Get-FileHash -LiteralPath $UpdateZip -Algorithm SHA256).Hash.ToLowerInvariant()
Assert-True ($actualSha -ceq $expectedSha) '累计升级包 SHA-256 与侧车不一致'

$external = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-True ($external.kind -ceq 'v2-cumulative-update') '累计升级外部 manifest 类型错误'
Assert-True ($external.version -ceq $version) '累计升级外部 manifest 版本错误'
Assert-True ($external.formal_external_release_allowed -eq $false) '未完成实机验收的累计升级包不得开放正式外发'
Assert-True (($external.supported_source_versions -join ',') -ceq '2.1.0') '累计升级来源矩阵必须只允许 V2.1.0'
Assert-True ($external.artifact.sha256 -ceq $actualSha) '累计升级外部 manifest 未绑定 ZIP SHA-256'

if (Test-Path -LiteralPath $WorkRoot) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $WorkRoot | Out-Null
try {
    Expand-Archive -LiteralPath $UpdateZip -DestinationPath $WorkRoot -Force
    $launcher = Join-Path $WorkRoot "升级到V$version.bat"
    $toolRoot = Join-Path $WorkRoot '_V2更新工具'
    $runtimePython = Join-Path $toolRoot 'runtime\python.exe'
    $innerManifestPath = Join-Path $toolRoot 'manifest.json'
    foreach ($required in @(
        $launcher,
        (Join-Path $WorkRoot '升级说明.txt'),
        $runtimePython,
        (Join-Path $toolRoot 'app\update.py'),
        (Join-Path $toolRoot 'app\update_core.py'),
        (Join-Path $toolRoot 'app\installer_core.py'),
        (Join-Path $toolRoot 'payload-update.zip'),
        $innerManifestPath
    )) {
        Assert-True (Test-Path -LiteralPath $required -PathType Leaf) "累计升级包缺少：$required"
    }

    $launcherText = Get-Content -LiteralPath $launcher -Raw -Encoding UTF8
    Assert-True (-not $launcherText.Contains('%*')) '累计升级 BAT 必须是零参数入口'
    Assert-True ($launcherText.Contains('MRV2_UPDATER_RESULT=%UPDATE_RC%')) '累计升级 BAT 缺少精确结果标记'

    $inner = Get-Content -LiteralPath $innerManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ($inner.version -ceq $version) '累计升级内部 manifest 版本错误'
    Assert-True ($inner.acceptance.formal_external_release_allowed -eq $false) '累计升级内部 manifest 不得开放正式外发'
    foreach ($record in $inner.payload.files) {
        $relative = [string]$record.path
        Assert-True (-not ($relative -match '(^|/)(data|backups|logs)(/|$)')) "累计升级 payload 触碰可变目录：$relative"
        Assert-True (-not ($relative -match '(^|/)(install\.json|\.secret_key|update-transaction\.json)(/|$)')) "累计升级 payload 触碰安装身份：$relative"
    }

    $verifyCode = @'
import pathlib
import sys
tool = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(tool / "app"))
from update_core import UpdateBundle
bundle = UpdateBundle.load(tool)
assert bundle.manifest["version"] == sys.argv[2]
assert bundle.supported_source_versions == frozenset({"2.1.0"})
'@
    & $runtimePython -I -c $verifyCode $toolRoot $version
    Assert-True ($LASTEXITCODE -eq 0) "包内冻结 runtime 无法反向加载累计升级包，退出码 $LASTEXITCODE"
    Write-Host 'MRV2_WINDOWS_UPDATE_PACKAGE_GATE=PASS'
}
finally {
    if (Test-Path -LiteralPath $WorkRoot) {
        Remove-Item -LiteralPath $WorkRoot -Recurse -Force
    }
}
