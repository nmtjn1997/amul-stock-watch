# Install amul-watch on Windows 10/11 (PowerShell):
#   irm https://raw.githubusercontent.com/nmtjn1997/amul-stock-watch/main/scripts/install.ps1 | iex
$ErrorActionPreference = "Stop"
$Repo = "git+https://github.com/nmtjn1997/amul-stock-watch.git"
$Src = if (Test-Path "$PSScriptRoot\..\pyproject.toml") { (Resolve-Path "$PSScriptRoot\..").Path } else { $Repo }

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw "curl.exe not found (it ships with Windows 10 1803+)" }
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { throw "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')" }
& py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required" }
if ($Src -eq $Repo -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git is needed to install from GitHub: winget install Git.Git (or download the zip and run this script from it)"
}

$Venv = Join-Path $env:LOCALAPPDATA "amul-watch\venv"
& py -3 -m venv $Venv
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
Write-Host "Installed. Next (in a new terminal):"
Write-Host "  amul-watch serve              # web UI + poller, opens http://127.0.0.1:8847"
Write-Host "  amul-watch service install    # start it automatically at every logon"
