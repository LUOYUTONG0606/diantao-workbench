"""Start the dashboard shell and manage a compatible local workbench backend."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / "workbench" / ".venv"
PYTHONW = Path(sys.base_prefix) / "pythonw.exe"
UPSTREAM_HEALTH = "http://127.0.0.1:9060/api/health"


def _compatible_backend() -> bool:
    try:
        with urllib.request.urlopen(UPSTREAM_HEALTH, timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("ok") is True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _stop_owned_backend(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    if not PYTHONW.is_file():
        raise SystemExit(f"pythonw.exe not found: {PYTHONW}")

    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(VENV / "Lib" / "site-packages"), str(ROOT)])
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("PYTHONUTF8", "1")

    runtime = ROOT / ".runtime" / "workbench_update_preview"
    runtime.mkdir(parents=True, exist_ok=True)
    launcher_log = runtime / "launcher.log"
    child_log = runtime / "dashboard-child.log"
    backend_log = runtime / "backend-child.log"
    owned_backend = None

    try:
        if not _compatible_backend():
            with backend_log.open("ab") as output:
                owned_backend = subprocess.Popen(
                    [str(PYTHONW), "-m", "uvicorn", "workbench.app:app", "--host", "127.0.0.1", "--port", "9060"],
                    cwd=str(ROOT), env=environment, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                )
            atexit.register(_stop_owned_backend, owned_backend)
            for _ in range(60):
                if _compatible_backend():
                    break
                if owned_backend.poll() is not None:
                    raise RuntimeError("9060 任务服务启动失败")
                time.sleep(.1)
            else:
                raise RuntimeError("9060 任务服务启动超时")

        with child_log.open("ab") as output:
            dashboard = subprocess.Popen(
                [str(PYTHONW), "-m", "workbench_update_preview.app"],
                cwd=str(ROOT), env=environment, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
            )
        launcher_log.write_text(
            f"started dashboard_pid={dashboard.pid} backend_pid={owned_backend.pid if owned_backend else 'reused'}\n",
            encoding="utf-8",
        )
        dashboard.wait()
        return dashboard.returncode or 0
    except Exception as error:
        launcher_log.write_text(f"failed: {error}\npythonw={PYTHONW}\n", encoding="utf-8")
        raise
    finally:
        _stop_owned_backend(owned_backend)


if __name__ == "__main__":
    raise SystemExit(main())
