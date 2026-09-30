"""Run `amul-watch serve` in the background at login, using the OS's own service manager.

    macOS    launchd LaunchAgent   ~/Library/LaunchAgents/io.github.amul-watch.plist
    Linux    systemd user unit     ~/.config/systemd/user/amul-watch.service
    Windows  Startup folder        amul-watch.vbs (per user, no admin rights needed)

Docker needs none of this: `restart: unless-stopped` in docker-compose.yml does the job.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from amul_watch.config import DATA_DIR, HOME

LABEL = "io.github.amul-watch"
UNIT = "amul-watch.service"


def _argv() -> list[str]:
    python = sys.executable
    if sys.platform.startswith("win"):
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)
    return [python, "-m", "amul_watch", "--home", str(HOME), "serve", "--no-open"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _mac_install() -> int:
    from xml.sax.saxutils import escape

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    args = "\n".join(f"    <string>{escape(a)}</string>" for a in _argv())
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{escape(str(DATA_DIR / 'service.out.log'))}</string>
  <key>StandardErrorPath</key><string>{escape(str(DATA_DIR / 'service.err.log'))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    target = f"gui/{os.getuid()}"
    _run(["launchctl", "bootout", f"{target}/{LABEL}"])
    boot = _run(["launchctl", "bootstrap", target, str(plist)])
    if boot.returncode != 0:
        print(f"launchctl bootstrap failed: {boot.stderr.strip()}")
        print("  hint: macOS blocks background jobs whose files live under ~/Documents or ~/Desktop.")
        return 1
    print(f"installed launchd agent {LABEL} ({plist})")
    return 0


def _mac_uninstall() -> int:
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    _plist_path().unlink(missing_ok=True)
    print(f"removed launchd agent {LABEL}")
    return 0


def _mac_status() -> int:
    proc = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
    if proc.returncode != 0:
        print("service: not installed")
        return 1
    state = next((ln.split("=", 1)[1].strip() for ln in proc.stdout.splitlines()
                  if ln.strip().startswith("state =")), "unknown")
    print(f"service: installed ({state})")
    return 0


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #

def _unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / UNIT


def _linux_install() -> int:
    unit = _unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    exec_start = " ".join(f'"{a}"' if " " in a else a for a in _argv())
    unit.write_text(
        f"""[Unit]
Description=Amul stock watch (poller + web UI)
After=network-online.target

[Service]
ExecStart={exec_start}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    _run(["systemctl", "--user", "daemon-reload"])
    proc = _run(["systemctl", "--user", "enable", "--now", UNIT])
    if proc.returncode != 0:
        print(f"systemctl failed: {proc.stderr.strip()}")
        return 1
    print(f"installed systemd user unit {unit}")
    print("  to keep it running after logout: loginctl enable-linger $USER")
    return 0


def _linux_uninstall() -> int:
    _run(["systemctl", "--user", "disable", "--now", UNIT])
    _unit_path().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    print("removed systemd user unit")
    return 0


def _linux_status() -> int:
    proc = _run(["systemctl", "--user", "is-active", UNIT])
    print(f"service: {proc.stdout.strip() or 'not installed'}")
    return 0 if proc.returncode == 0 else 1


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #

def _startup_script() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "amul-watch.vbs"


def _win_install() -> int:
    # A scheduled logon task needs an elevated shell; the per-user Startup folder does not.
    # The .vbs wrapper starts pythonw with no console window.
    command = subprocess.list2cmdline(_argv()).replace('"', '""')
    script = _startup_script()
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f'CreateObject("WScript.Shell").Run "{command}", 0, False\r\n', encoding="utf-16")
    from amul_watch.daemon import watcher_state

    if watcher_state()["running"]:
        print(f"installed {script} (starts at every logon; already running now)")
    else:
        subprocess.Popen(["wscript.exe", str(script)])
        print(f"installed {script} (starts at every logon, and started now)")
    return 0


def _win_uninstall() -> int:
    _startup_script().unlink(missing_ok=True)
    print("removed the startup entry. A copy already running keeps running until you log off,")
    print("or stop it from Task Manager (pythonw.exe).")
    return 0


def _win_status() -> int:
    installed = _startup_script().is_file()
    print("service: installed (Startup folder)" if installed else "service: not installed")
    return 0 if installed else 1


def _dispatch(action: str) -> int:
    if sys.platform == "darwin":
        table = {"install": _mac_install, "uninstall": _mac_uninstall, "status": _mac_status}
    elif sys.platform.startswith("win"):
        table = {"install": _win_install, "uninstall": _win_uninstall, "status": _win_status}
    else:
        import shutil

        if not shutil.which("systemctl"):
            print("systemd not found (containers, WSL1 and some distros do not have it).")
            print("Run `amul-watch serve` under your own supervisor instead (tmux, nohup,")
            print("a cron @reboot line), or use Docker, which restarts it for you.")
            return 2
        table = {"install": _linux_install, "uninstall": _linux_uninstall, "status": _linux_status}
    return table[action]()


def install() -> int:
    return _dispatch("install")


def uninstall() -> int:
    return _dispatch("uninstall")


def status() -> int:
    return _dispatch("status")
