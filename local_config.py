"""Private settings live outside the installation; existing .env remains compatible."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def config_path():
    explicit = os.environ.get("DIANTAO_CONFIG_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
    return base / "DianTao" / "config.env"


def load_config():
    """Priority: process environment > user config > legacy project .env."""
    path = config_path()
    if os.environ.get("DIANTAO_CONFIG_FILE", "").strip() and not path.is_file():
        raise FileNotFoundError("DIANTAO_CONFIG_FILE 指定的配置文件不存在")
    load_dotenv(path, override=False)
    if not os.environ.get("DIANTAO_CONFIG_FILE", "").strip():
        load_dotenv(ROOT / ".env", override=False)
    return path


def prepare_config(*, import_legacy=False):
    """Create once, never overwrite. Keep the legacy file intact when migrating."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    source = ROOT / (".env" if import_legacy and (ROOT / ".env").is_file() else ".env.example")
    try:
        with path.open("xb") as output:
            output.write(source.read_bytes())
    except FileExistsError:
        pass
    return path


def main():
    parser = argparse.ArgumentParser(description="准备项目外的本机配置，不输出配置内容")
    parser.add_argument("--import-legacy", action="store_true", help="首次创建时复制旧 .env")
    parser.add_argument("--open", action="store_true", help="用记事本编辑本机配置")
    args = parser.parse_args()
    path = prepare_config(import_legacy=args.import_legacy)
    print(f"本机配置：{path}")
    if args.open:
        if sys.platform != "win32":
            parser.error("--open 仅适用于 Windows；请用文本编辑器打开上面的文件")
        subprocess.Popen(["notepad.exe", str(path)])


if __name__ == "__main__":
    main()
