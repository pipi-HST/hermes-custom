"""Install the Hermes vault watcher as a macOS LaunchAgent."""
from __future__ import annotations

import argparse
import html
import subprocess
from pathlib import Path

LABEL = "com.pipi.hermes-vault-watcher"
DEFAULT_INTERVAL_SECONDS = 21600


def build_plist(
    *,
    python_path: str | Path,
    script_path: str | Path,
    db_path: str | Path,
    vault_path: str | Path,
    log_path: str | Path,
    interval: int = DEFAULT_INTERVAL_SECONDS,
) -> str:
    """Build the LaunchAgent plist contents."""
    args = [
        python_path,
        script_path,
        "--db",
        db_path,
        "--vault",
        vault_path,
        "--interval",
        str(interval),
    ]
    arg_xml = "\n".join(
        f"    <string>{html.escape(str(Path(arg).expanduser()))}</string>"
        if i in {0, 1, 3, 5}
        else f"    <string>{html.escape(str(arg))}</string>"
        for i, arg in enumerate(args)
    )
    log = html.escape(str(Path(log_path).expanduser()))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def install(
    *,
    project_root: str | Path,
    vault_path: str | Path = "~/Obsidian/hermes-vault",
    interval: int = DEFAULT_INTERVAL_SECONDS,
    load: bool = False,
) -> Path:
    """Write the LaunchAgent plist and optionally load it."""
    root = Path(project_root).expanduser().resolve()
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LABEL}.plist"
    log_path = root / "data" / "vault_watcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    plist_path.write_text(
        build_plist(
            python_path=root / ".venv" / "bin" / "python",
            script_path=root / "scripts" / "watch_vault.py",
            db_path=root / "data" / "captures.db",
            vault_path=Path(vault_path).expanduser(),
            log_path=log_path,
            interval=interval,
        ),
        encoding="utf-8",
    )

    if load:
        _load_launch_agent(plist_path)
    return plist_path


def _load_launch_agent(plist_path: Path) -> None:
    domain = f"gui/{_uid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=True)


def _uid() -> str:
    return subprocess.check_output(["id", "-u"], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install Hermes vault watcher LaunchAgent."
    )
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--vault", default="~/Obsidian/hermes-vault")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()

    plist_path = install(
        project_root=args.project_root,
        vault_path=args.vault,
        interval=args.interval,
        load=args.load,
    )
    print(plist_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
