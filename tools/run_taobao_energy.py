"""Prepare the energy environment once, then launch without dependency resolution."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".venv-energy"
PYTHON = ENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
READY = ENV / ".energy-ready"
PACKAGES = ("uiautomator2", "requests", "python-dotenv")


def main(args=None):
    args = sys.argv[1:] if args is None else args
    output = sys.stderr if args and args[0] == "scan" else sys.stdout
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    options = dict(
        creationflags=flags, stdin=subprocess.DEVNULL,
        stdout=sys.stdout, stderr=sys.stderr,
    )
    if not PYTHON.is_file() or not READY.is_file():
        uv = shutil.which("uv")
        if not uv:
            print("[准备] 首次运行需要安装 uv，请安装后重新启动。", file=output, flush=True)
            return 1
        print("[准备] 首次创建能量任务环境，之后启动将直接复用。", file=output, flush=True)
        try:
            if not PYTHON.is_file():
                subprocess.run(
                    [uv, "venv", str(ENV), "--python", sys.executable],
                    check=True, **options,
                )
            print("[准备] 正在安装手机连接与识别依赖…", file=output, flush=True)
            subprocess.run(
                [uv, "pip", "install", "--python", str(PYTHON), *PACKAGES],
                check=True, **options,
            )
            READY.touch()
        except subprocess.CalledProcessError:
            print("[准备] 环境准备失败，请检查网络后重新启动。", file=output, flush=True)
            return 1
    print("[启动] 环境已就绪，正在启动能量任务…", file=output, flush=True)
    try:
        return subprocess.call(
            [str(PYTHON), "-u", str(ROOT / "tools" / "taobao_energy.py"), *args],
            **options,
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
