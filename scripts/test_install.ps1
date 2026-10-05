# Hermetic tests for scripts/install.ps1, against fake packages served from a
# local folder. Runs under PowerShell 7 on Windows, Linux or macOS; no network
# and no real package. Off Windows, the Start menu and path-length steps are
# skipped, as the installer itself skips them.
#
#   pwsh scripts/test_install.ps1

$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ("vista-install-test-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $root | Out-Null
$script:failures = 0

function Check([string]$What, [bool]$Ok) {
  if ($Ok) { Write-Host "  ok   $What" } else { Write-Host "  FAIL $What"; $script:failures++ }
}

# A fake package: VERSION, and a vista.cmd that records that it ran.
function New-Release([string]$Dir, [string]$Version) {
  $name = "vista-$Version-win-x86"
  $src = Join-Path $root "src/$Version"
  New-Item -ItemType Directory -Force -Path (Join-Path $src $name), $Dir | Out-Null
  Set-Content -Path (Join-Path $src "$name/VERSION") -Value $Version
  Set-Content -Path (Join-Path $src "$name/vista.cmd") -Value "@echo ran > `"$root\ran`""
  $zip = Join-Path $Dir "$name.zip"
  Compress-Archive -Path (Join-Path $src $name) -DestinationPath $zip
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  Set-Content -Path "$zip.sha256" -Value "$hash  $name.zip"
}

# Run the installer in a child PowerShell, with its own environment.
function Invoke-Installer([hashtable]$Env, [string[]]$Arguments, [string]$Script = $installer) {
  $saved = @{}
  foreach ($k in $Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $Env[$k]) }
  try {
    $out = & pwsh -NoProfile -NonInteractive -File $Script @Arguments 2>&1 | Out-String
    return @{ Ok = ($LASTEXITCODE -eq 0); Out = $out }
  } finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  }
}

try {
  $installDir = Join-Path $root 'VISTA/app'
  $state = Join-Path $root 'state'
  New-Item -ItemType Directory -Path $state | Out-Null
  Set-Content -Path (Join-Path $state 'vista.db') -Value 'my chats'
  foreach ($v in '1.0.0', '1.1.0', '2.0.0') { New-Release (Join-Path $root "releases/$v") $v }
  $common = @{ VISTA_INSTALL_DIR = $installDir; VISTA_INSTALL_MIN_FREE_GB = '0'; VISTA_INSTALL_NO_SHORTCUT = '1'; VISTA_HOME = $state }

  Write-Host 'install.ps1'

  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/1.0.0') }) @('-Version', '1.0.0', '-NoLaunch')
  if (-not $r.Ok) { Write-Host $r.Out }
  Check 'a fresh install succeeds' $r.Ok
  Check 'a fresh install unpacks the package' (Test-Path (Join-Path $installDir 'vista.cmd'))
  Check 'it records the installed version' ((Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.0.0')
  Check 'it leaves no work folder behind' (-not (Get-ChildItem (Join-Path $root 'VISTA') -Filter '.install.*' -Force))
  Check '-NoLaunch does not start VISTA' (-not (Test-Path (Join-Path $root 'ran')))

  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'nowhere') }) @('-Version', 'v1.0.0', '-NoLaunch')
  Check 'a re-run of the installed version downloads nothing' ($r.Ok -and $r.Out -match 'already installed')

  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/1.1.0'); VISTA_INSTALL_NO_LAUNCH = '1' }) @('-Version', '1.1.0')
  if (-not $r.Ok) { Write-Host $r.Out }
  Check 'an upgrade installs the new version' ($r.Ok -and (Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.1.0')
  Check 'VISTA_INSTALL_NO_LAUNCH=1 does not start VISTA' (-not (Test-Path (Join-Path $root 'ran')))
  Check "an upgrade keeps VISTA's state" ((Get-Content (Join-Path $state 'vista.db')) -eq 'my chats')

  $bad = Join-Path $root 'releases/bad'
  Copy-Item -Recurse (Join-Path $root 'releases/2.0.0') $bad
  Add-Content -Path (Join-Path $bad 'vista-2.0.0-win-x86.zip') -Value 'corrupt'
  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = $bad }) @('-Version', '2.0.0', '-NoLaunch')
  Check 'a checksum mismatch fails and says so' ((-not $r.Ok) -and $r.Out -match 'does not match its checksum')
  Check 'a checksum mismatch keeps the installed version' ((Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.1.0')
  Check 'a checksum mismatch leaves no work folder' (-not (Get-ChildItem (Join-Path $root 'VISTA') -Filter '.install.*' -Force))

  $r = Invoke-Installer $common @('-NoLaunch')
  Check 'an unrendered installer without -Version is refused' ((-not $r.Ok) -and $r.Out -match 'no version written in')

  $rendered = Join-Path $root 'install.ps1'
  (Get-Content $installer -Raw).Replace('@VISTA_VERSION@', '1.0.0') | Set-Content -Path $rendered -NoNewline
  Remove-Item -Recurse -Force $installDir
  $r = Invoke-Installer ($common + @{ VISTA_INSTALL_BASE_URL = (Join-Path $root 'releases/1.0.0') }) @('-NoLaunch') $rendered
  Check 'a rendered installer installs its own version' ($r.Ok -and (Get-Content (Join-Path $installDir 'VERSION')).Trim() -eq '1.0.0')

  if ($IsWindows) {
    $r = Invoke-Installer $common @('-Version', '1.0.0')
    Check 'without -NoLaunch it starts vista.cmd' ($r.Ok -and (Test-Path (Join-Path $root 'ran')))
  }
}
finally {
  Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}

if ($script:failures) { Write-Host "$($script:failures) failed"; exit 1 }
Write-Host 'all passed'
