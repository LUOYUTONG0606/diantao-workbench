"""Install the Windows desktop shortcut with the butler icon."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "启动大管家.cmd"
ICON = ROOT / "workbench" / "static" / "butler-logo.ico"


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This helper only supports Windows.")

    desktop = Path.home() / "Desktop"
    shortcuts = [desktop / "启动大管家.lnk", desktop / "启动大管家 (2).lnk"]
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        + "; ".join(
            f"$shortcut = $shell.CreateShortcut('{shortcut}'); "
            f"$shortcut.TargetPath = '{LAUNCHER}'; "
            f"$shortcut.WorkingDirectory = '{ROOT}'; "
            f"$shortcut.IconLocation = '{ICON},0'; "
            "$shortcut.Save()"
            for shortcut in shortcuts
        )
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for shortcut in shortcuts:
        print(shortcut)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
