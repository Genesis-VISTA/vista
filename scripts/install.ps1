# Install and start VISTA on Windows (x64), from a GitHub release. Each release
# carries its own copy of this script, with its version written in, so the
# newest release installs with:
#
#   powershell -ExecutionPolicy Bypass -c "irm https://github.com/Genesis-VISTA/vista/releases/latest/download/install.ps1 | iex"
#
# and a given one, prereleases included, from releases/download/<tag>/install.ps1.
# Run as a file, it takes options:
#
#   -NoLaunch          install, but don't start VISTA
#   -Version <ver>     install another release, e.g. 0.2.0 or v0.2.0-rc1
#
# Through `irm | iex` there is no way to pass them, so the same are read from
# VISTA_INSTALL_NO_LAUNCH=1 and VISTA_INSTALL_VERSION.
#
# It downloads the package, checks it against its .sha256 file, unpacks it into
# %LOCALAPPDATA%\VISTA\app, adds VISTA to the Start menu, and starts it. Run it
# again to start the installed copy, which downloads nothing, or to upgrade.
# VISTA's state (VISTA_HOME, ~\.vista by default) lives outside the install and
# is never touched.
#
# Environment:
#   VISTA_INSTALL_DIR          where the package goes (default: %LOCALAPPDATA%\VISTA\app)
#   VISTA_INSTALL_BASE_URL     where the archives are, instead of the release's
#                              download URL; a local folder works. For testing.
#   VISTA_INSTALL_MIN_FREE_GB  free disk to insist on (default: 7)
#   VISTA_INSTALL_NO_SHORTCUT  1 to skip the Start menu entry

param(
  [string]$Version = $env:VISTA_INSTALL_VERSION,
  [switch]$NoLaunch
)

# The whole script is one function, called on the last line, so a download that
# stops part way runs nothing. Through `irm | iex` it runs in the caller's own
# session, so nothing here changes that session's settings.
function Install-Vista([string]$Version, [bool]$Launch) {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with it

  # Written in by .github/scripts/render-installers.sh at release time.
  $releaseVersion = '@VISTA_VERSION@'
  $repoUrl = '@VISTA_REPO_URL@'
  if ($repoUrl -like '@*@') { $repoUrl = 'https://github.com/Genesis-VISTA/vista' }

  function Say([string]$Message) { Write-Host "==> $Message" }
  function Die([string]$Message) { throw "VISTA was not installed: $Message" }

  if ($Version) { $Version = $Version.TrimStart('v') }
  elseif ($releaseVersion -notlike '@*@') { $Version = $releaseVersion }
  else { Die 'this copy of the installer has no version written in; pass -Version <ver>' }

  # --- this machine ----------------------------------------------------------
  $onWindows = ($PSVersionTable.PSVersion.Major -le 5) -or $IsWindows
  if ($onWindows -and -not [Environment]::Is64BitOperatingSystem) {
    Die 'VISTA has a Windows package for x64 only.'
  }
  $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
  if ($onWindows -and $arch -eq 'ARM64') {
    Die 'VISTA has no Windows package for ARM. Packages exist for Windows on x64, macOS on Apple Silicon and Linux on x86-64.'
  }
  $platform = 'win-x86'
  $name = "vista-$Version-$platform"
  $archive = "$name.zip"

  $installDir = if ($env:VISTA_INSTALL_DIR) { $env:VISTA_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'VISTA\app' }
  $installDir = [IO.Path]::GetFullPath($installDir)
  $parent = Split-Path $installDir -Parent

  # --- already installed -----------------------------------------------------
  $versionFile = Join-Path $installDir 'VERSION'
  if ((Test-Path (Join-Path $installDir 'vista.cmd')) -and (Test-Path $versionFile) -and
      ((Get-Content $versionFile -TotalCount 1).Trim().Split('+')[0] -eq $Version)) {
    Say "VISTA $Version is already installed in $installDir"
    Add-Shortcut $installDir
    Start-Installed $installDir $Launch
    return
  }

  # --- before downloading ----------------------------------------------------
  # Windows caps a path at 260 characters unless long paths are enabled, and the
  # package's deepest file is 176 characters below its folder. The launcher
  # makes the exact check against the package's manifest; this one keeps a
  # 1.7 GB download from ending in its refusal.
  $longPaths = $null
  if ($onWindows) {
    $longPaths = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
        -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
  }
  if ($onWindows -and $longPaths -ne 1 -and $installDir.Length -gt 60) {
    Die ("the install folder's path is too long for Windows' path-length limit:`n" +
      "    $installDir`n" +
      "  is $($installDir.Length) characters, and VISTA needs it to be at most 60. Set VISTA_INSTALL_DIR`n" +
      "  to a shorter folder, such as C:\vista, and run this again.")
  }

  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  $needGb = if ($env:VISTA_INSTALL_MIN_FREE_GB) { [double]$env:VISTA_INSTALL_MIN_FREE_GB } else { 7 }
  $freeGb = [math]::Floor((New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($parent))).AvailableFreeSpace / 1GB)
  if ($freeGb -lt $needGb) {
    Die "VISTA needs about $needGb GB free on $([IO.Path]::GetPathRoot($parent)), and there is $freeGb GB. Free some space, or set VISTA_INSTALL_DIR to a drive with more room."
  }

  # --- download and check ----------------------------------------------------
  # The work folder sits beside the install, so the final move is a rename on
  # one drive, and an interrupted install leaves nothing where the next run
  # looks.
  $base = if ($env:VISTA_INSTALL_BASE_URL) { $env:VISTA_INSTALL_BASE_URL } else { "$repoUrl/releases/download/v$Version" }
  $work = Join-Path $parent (".install." + [IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Path $work | Out-Null
  try {
    Say "downloading VISTA $Version for $platform"
    Get-Asset $base $archive (Join-Path $work $archive)
    Get-Asset $base "$archive.sha256" (Join-Path $work "$archive.sha256")

    Say 'checking the download'
    $expected = ((Get-Content (Join-Path $work "$archive.sha256") -TotalCount 1) -split '\s+')[0].ToLower()
    $actual = (Get-FileHash (Join-Path $work $archive) -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $expected) {
      Die "$archive does not match its checksum. Run the command again; if it fails the same way, report it."
    }

    # --- unpack --------------------------------------------------------------
    # Windows' own tar (bsdtar) reads zip files, and is far faster than
    # Expand-Archive on a package this size.
    Say "unpacking into $installDir"
    $unpacked = Join-Path $work 'unpacked'
    New-Item -ItemType Directory -Path $unpacked | Out-Null
    if ($onWindows) {
      & (Join-Path $env:SystemRoot 'System32\tar.exe') -xf (Join-Path $work $archive) -C $unpacked
      if ($LASTEXITCODE -ne 0) { Die "could not unpack $archive" }
    } else {
      # Only the hermetic tests run it elsewhere, where GNU tar cannot read zip.
      Expand-Archive -Path (Join-Path $work $archive) -DestinationPath $unpacked
    }
    Remove-Item (Join-Path $work $archive)
    $fresh = Join-Path $unpacked $name
    if (-not (Test-Path (Join-Path $fresh 'vista.cmd'))) { Die "$archive does not hold $name\vista.cmd" }

    # The previous version goes only once the new one is ready to take its place.
    $old = Join-Path $work 'previous'
    if (Test-Path $installDir) {
      Say 'removing the previous version'
      Move-Item $installDir $old
    }
    try { Move-Item $fresh $installDir }
    catch {
      if (Test-Path $old) { Move-Item $old $installDir }
      Die "could not move the new version into $installDir ($($_.Exception.Message)); the previous one was kept"
    }
  }
  finally {
    if (Test-Path $work) { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue }
  }

  Add-Shortcut $installDir
  Say "installed VISTA $Version"
  Start-Installed $installDir $Launch
}

# One release asset, from a URL or, for testing, a local folder.
function Get-Asset([string]$Base, [string]$File, [string]$To) {
  if ($Base -match '^https?://') {
    # Windows PowerShell 5.1 on an older .NET may not offer TLS 1.2, which GitHub requires.
    if ($PSVersionTable.PSVersion.Major -le 5) {
      [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    }
    try { Invoke-WebRequest -UseBasicParsing -Uri "$Base/$File" -OutFile $To }
    catch { throw "VISTA was not installed: could not download $Base/$File ($($_.Exception.Message))" }
  } else {
    $from = Join-Path ($Base -replace '^file:///?', '') $File
    if (-not (Test-Path $from)) { throw "VISTA was not installed: could not download $from" }
    Copy-Item $from $To
  }
}

# A Start menu entry, so the next start needs no terminal.
function Add-Shortcut([string]$InstallDir) {
  if ($env:VISTA_INSTALL_NO_SHORTCUT -eq '1' -or -not $env:APPDATA) { return }
  $onWindows = ($PSVersionTable.PSVersion.Major -le 5) -or $IsWindows
  if (-not $onWindows) { return }
  $lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\VISTA.lnk'
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($lnk)
  $shortcut.TargetPath = Join-Path $InstallDir 'vista.cmd'
  $shortcut.WorkingDirectory = $InstallDir
  $shortcut.Description = 'VISTA'
  $shortcut.Save()
  Write-Host "==> start it later from the Start menu (VISTA), or run $(Join-Path $InstallDir 'vista.cmd')"
}

function Start-Installed([string]$InstallDir, [bool]$Launch) {
  if (-not $Launch) { return }
  Write-Host '==> starting VISTA'
  & (Join-Path $InstallDir 'vista.cmd')
}

# Through `irm | iex` the param() block above may not apply, so its defaults are
# taken again here.
$installVersion = if ($Version) { $Version } else { $env:VISTA_INSTALL_VERSION }
$launch = -not ($NoLaunch -or $env:VISTA_INSTALL_NO_LAUNCH -eq '1')
Install-Vista $installVersion $launch
