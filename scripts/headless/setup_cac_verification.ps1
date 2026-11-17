<#
Build and install a checkout into a disposable Blender 5.2 extension profile,
prove the environment through run_cac_suite.py's child-process path and,
optionally, run the CAC suite in that same profile.

The profile is checkout-local (<RepoRoot>\.cac-verification) and is always
rebuilt from scratch, so the loaded add-on is exactly the checkout's code at
build time.  It never uses the user's Blender resources, %APPDATA% add-ons or
the legacy scripts\headless\pydeps shim.  A cac_source_stamp.json (checkout
path, HEAD, dirty-file list) is written next to the installed package and is
printed by the diagnostic from inside Blender.

  setup_cac_verification.ps1                         # build + diagnostic
  setup_cac_verification.ps1 -SuiteOut <dir> [-Only p3]
  setup_cac_verification.ps1 -RepoRoot <other checkout> -WheelsDir <wheel cache> -SuiteOut <dir>
  setup_cac_verification.ps1 -NoRebuild -SuiteOut <dir>   # reuse the profile
#>
[CmdletBinding()]
param(
    [string]$BlenderExe = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    [string]$RepoRoot = "",
    [string]$SuiteOut = "",
    [string]$Only = "",
    [string]$WheelsDir = "",
    [switch]$NoRebuild
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$ProfileRoot = Join-Path $RepoRoot ".cac-verification"
$resources = Join-Path $ProfileRoot "resources"
$temp = Join-Path $ProfileRoot "tmp"
$zip = Join-Path $ProfileRoot "city_modeler.zip"
$out = Join-Path $ProfileRoot "diagnostic"
$installed = Join-Path $resources "extensions\user_default\city_modeler"

$env:BLENDER_USER_RESOURCES = $resources
$env:TEMP = $temp
$env:TMP = $temp
$env:BLENDER_EXE = $BlenderExe
$env:CAC_ADDON_MODULE = "bl_ext.user_default.city_modeler"
# Nothing from a system/user Python may leak into Blender's interpreter.
Remove-Item Env:PYTHONPATH, Env:PYTHONHOME, Env:BLENDER_USER_SCRIPTS -ErrorAction SilentlyContinue

if (-not $NoRebuild) {
    # Always a clean profile: the build must never package a previous profile.
    if (Test-Path $ProfileRoot) { Remove-Item -LiteralPath $ProfileRoot -Recurse -Force }
    New-Item -ItemType Directory -Force $resources, $temp | Out-Null

    # wheels/ is generated (scripts/fetch_deps.py) and git-ignored, so a fresh
    # worktree has none.  -WheelsDir fills in exactly the manifest-declared
    # files; nothing undeclared is added, and the hashes go into the stamp.
    $declared = @(Select-String -Path (Join-Path $RepoRoot "blender_manifest.toml") -Pattern '"\./wheels/([^"]+)"' |
        ForEach-Object { $_.Matches[0].Groups[1].Value })
    $wheelDir = Join-Path $RepoRoot "wheels"
    foreach ($w in $declared) {
        $dst = Join-Path $wheelDir $w
        if (Test-Path $dst) { continue }
        if (-not $WheelsDir) { throw "declared wheel missing: $dst (run scripts/fetch_deps.py or pass -WheelsDir)" }
        New-Item -ItemType Directory -Force $wheelDir | Out-Null
        Copy-Item -LiteralPath (Join-Path $WheelsDir $w) -Destination $dst
    }
    $wheelHashes = [ordered]@{}
    foreach ($w in $declared | Where-Object { $_ -match 'win_amd64' }) {
        $wheelHashes[$w] = (Get-FileHash (Join-Path $wheelDir $w) -Algorithm SHA256).Hash
    }

    & $BlenderExe --command extension build --source-dir $RepoRoot --output-filepath $zip
    if ($LASTEXITCODE -ne 0) { throw "extension build failed ($LASTEXITCODE)" }
    & $BlenderExe --command extension install-file --repo user_default --enable $zip
    if ($LASTEXITCODE -ne 0) { throw "extension install failed ($LASTEXITCODE)" }

    $head = (git -C $RepoRoot rev-parse HEAD).Trim()
    $describe = (git -C $RepoRoot describe --tags --always --dirty 2>$null)
    $branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD).Trim()
    $dirty = @(git -C $RepoRoot status --porcelain --untracked-files=all 2>$null | Where-Object { $_ -notmatch '\.cac-verification/' })
    [ordered]@{
        repo_root = $RepoRoot
        head = $head
        describe = "$describe".Trim()
        branch = $branch
        dirty = $dirty
        zip_sha256 = (Get-FileHash $zip -Algorithm SHA256).Hash
        windows_wheels_sha256 = $wheelHashes
    } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $installed "cac_source_stamp.json")
} elseif (-not (Test-Path $installed)) {
    throw "no profile at $ProfileRoot; run without -NoRebuild first"
}

New-Item -ItemType Directory -Force $out | Out-Null
$runner = Join-Path $RepoRoot "scripts\headless\run_cac_suite.py"
$tests = Join-Path $RepoRoot "scripts\headless\verification"
& $BlenderExe --background --factory-startup --python $runner -- $out --tests-dir $tests
if ($LASTEXITCODE -ne 0) { throw "CAC environment diagnostic failed ($LASTEXITCODE); see $out" }
Write-Host "CAC verification profile: $ProfileRoot"

if ($SuiteOut) {
    $suiteArgs = @($SuiteOut)
    if ($Only) { $suiteArgs += @("--only", $Only) }
    & $BlenderExe --background --factory-startup --python $runner -- @suiteArgs
    Write-Host "CAC suite exit $LASTEXITCODE; results: $(Join-Path $SuiteOut 'results.json')"
    exit $LASTEXITCODE
}
