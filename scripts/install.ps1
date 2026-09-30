# Install amul-watch on Windows 10/11 (PowerShell):
#   irm https://raw.githubusercontent.com/nmtjn1997/amul-stock-watch/main/scripts/install.ps1 | iex
$ErrorActionPreference = "Stop"
$Repo = "git+https://github.com/nmtjn1997/amul-stock-watch.git"
$Src = if (Test-Path "$PSScriptRoot\..\pyproject.toml") { (Resolve-Path "$PSScriptRoot\..").Path } else { $Repo }

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw "curl.exe not found (it ships with Windows 10 1803+)" }
# The py launcher (python.org installer) first, then plain python (Microsoft Store, winget).
$Py = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
  if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
    $args0 = @($cand | Select-Object -Skip 1)
    & $cand[0] @args0 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $Py = $cand; break }
  }
}
if (-not $Py) { throw "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')" }
$PyExe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1)
if ($Src -eq $Repo -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git is needed to install from GitHub: winget install Git.Git (or download the zip and run this script from it)"
}

$Venv = Join-Path $env:LOCALAPPDATA "amul-watch\venv"
& $PyExe @PyArgs -m venv $Venv
& "$Venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$Venv\Scripts\python.exe" -m pip install --quiet $Src

$Bin = Join-Path $env:LOCALAPPDATA "amul-watch\bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Content -Path (Join-Path $Bin "amul-watch.cmd") -Value "@`"$Venv\Scripts\amul-watch.exe`" %*"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$Bin*") {
  [Environment]::SetEnvironmentVariable("Path", "$userPath;$Bin", "User")
  Write-Host "Added $Bin to your PATH (open a new terminal to use it)."
}
& "$Venv\Scripts\amul-watch.exe" init
Write-Host ""
Write-Host "Installed: $Bin\amul-watch.cmd"
Write-Host "Next (in a NEW terminal, so PATH is refreshed):"
Write-Host "  amul-watch serve              # web UI + poller, opens http://127.0.0.1:8847"
Write-Host "  amul-watch service install    # start it automatically at every logon"
