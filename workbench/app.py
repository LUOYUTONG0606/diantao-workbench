"""Portable desktop entry point. Importing this module never starts phone tasks."""
from __future__ import annotations

import asyncio
import atexit
from collections import deque
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from local_config import load_config
from workbench.phone import router as phone_router
from workbench.tasks import TASKS
from task_host import hosted_command
from guard_timer import RUN_ENV, request_refresh, timer_status

load_config()

PROCESSES: dict[str, subprocess.Popen] = {}
STARTED: dict[str, float] = {}
LOGS: dict[str, deque] = {}
SEQUENCES: dict[str, int] = {}
EXIT_CODES: dict[str, int] = {}
GUARD_RUN_ID = None
RUNTIME = ROOT / ".runtime" / "workbench"
LOG_LOCK = threading.Lock()
TASK_LOCK = threading.Lock()
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
APP_USER_MODEL_ID = "DianTao.AutomationButler"


def runtime_version() -> dict:
    metadata = ROOT / "VERSION.json"
    if metadata.is_file():
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            if isinstance(payload.get("version"), str):
                return payload
        except (OSError, ValueError):
            pass
    return {"version": "v0.3.0", "commit": None}


def append_log(task_id: str, text: str):
    with LOG_LOCK:
        SEQUENCES[task_id] = SEQUENCES.get(task_id, 0) + 1
        LOGS.setdefault(task_id, deque(maxlen=500)).append(
            (SEQUENCES[task_id], str(text))
        )


def status(task_id: str):
    process = PROCESSES.get(task_id)
    if process is not None and process.poll() is None:
        return {"status": "running", "pid": process.pid, "started_at": STARTED[task_id]}
    code = process.returncode if process is not None else EXIT_CODES.get(task_id)
    return {"status": "stopped", "pid": None, "exit_code": code}


def read_output(task_id: str, process: subprocess.Popen):
    path = RUNTIME / f"{task_id}.log"
    try:
        with path.open("w", encoding="utf-8") as output:
            for line in process.stdout:
                text = line.rstrip("\r\n")
                append_log(task_id, text)
                output.write(text + "\n")
                output.flush()
    finally:
        process.stdout.close()
        code = process.wait()
        EXIT_CODES[task_id] = code
        append_log(task_id, f"[结束] 退出码 {code}")


def stop_process(task_id: str):
    process = PROCESSES.get(task_id)
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=10, creationflags=NO_WINDOW,
        )
        if result.returncode != 0 and process.poll() is None:
            process.kill()
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def cleanup():
    for task_id in list(PROCESSES):
        stop_process(task_id)


@asynccontextmanager
async def lifespan(_app):
    yield
    await asyncio.to_thread(cleanup)


app = FastAPI(title="Tearice · 自动化大管家", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(phone_router)


@app.get("/")
def index():
    return HTMLResponse((ROOT / "workbench" / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/static/{name}", include_in_schema=False)
def static_asset(name: str):
    if urlsplit(name).path != name or name != Path(name).name:
        raise HTTPException(404, "未找到静态资源")
    path = ROOT / "workbench" / "static" / name
    if not path.is_file():
        raise HTTPException(404, "未找到静态资源")
    return FileResponse(path)


@app.get("/api/health")
def health():
    return {"ok": True, "running_count": sum(status(key)["status"] == "running" for key in TASKS),
            "version": runtime_version()["version"]}


@app.get("/api/version")
def version():
    return runtime_version()


@app.get("/api/apps")
def get_apps():
    return {"apps": [
        {"id": key, "category": "script", "name": task["name"],
         "description": task["description"], **availability(task), **status(key),
         **({"timer": timer_status(status(key)["pid"], run_id=GUARD_RUN_ID)}
            if key == "diantao-work-guard" else {})}
        for key, task in TASKS.items()
    ]}


def availability(task):
    present = (ROOT / task["script"]).is_file()
    return {
        "available": present,
        "unavailable_reason": "" if present else "缺少脚本：" + task["script"],
    }


def require_task(task_id):
    if task_id not in TASKS:
        raise HTTPException(404, "未找到任务")
    return TASKS[task_id]


@app.post("/api/start/{task_id}")
def start_task(task_id: str):
    task = require_task(task_id)
    available = availability(task)
    if not available["available"]:
        raise HTTPException(400, available["unavailable_reason"])
    if task.get("requires_ocr", True) and not os.environ.get("DIANTAO_OCR_SERVER_URL", "").strip():
        raise HTTPException(400, "请运行配置本机.cmd，填写自己的 DIANTAO_OCR_SERVER_URL 后重新打开工作台")
    with TASK_LOCK:
        is_guard = task_id == "diantao-work-guard"
        if status(task_id)["status"] == "running":
            raise HTTPException(409, "该任务已在运行")
        if not is_guard and any(
            status(key)["status"] == "running"
            for key in TASKS if key != "diantao-work-guard"
        ):
            raise HTTPException(409, "已有任务控制手机，请等待完成或先停止，再启动另一项")
        script = ROOT / task["script"]
        if not script.is_file():
            raise HTTPException(400, "任务脚本不完整，请重新下载整个仓库")
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        RUNTIME.mkdir(parents=True, exist_ok=True)
        if task_id == "diantao-all-in-one" and status("diantao-work-guard")["status"] != "running":
            guard_task = require_task("diantao-work-guard")
            launch_process("diantao-work-guard", guard_task, python, env, is_guard=True)
        process = launch_process(task_id, task, python, env, is_guard=is_guard)
    return {"ok": True, "message": task["name"] + "已启动", "pid": process.pid}


def launch_process(task_id, task, python, env, *, is_guard):
    global GUARD_RUN_ID
    script = ROOT / task["script"]
    if is_guard:
        env = dict(env, **{RUN_ENV: uuid.uuid4().hex})
    try:
        command = [str(python), "-u", str(script), *task["args"]]
        process = subprocess.Popen(
            command if is_guard else hosted_command(command, str(python)),
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW, start_new_session=sys.platform != "win32",
        )
    except OSError as error:
        raise HTTPException(500, "无法启动任务，请检查 Python 环境") from error
    with LOG_LOCK:
        LOGS[task_id] = deque(maxlen=500)
    append_log(task_id, "[启动] " + task["name"])
    PROCESSES[task_id] = process
    if is_guard:
        GUARD_RUN_ID = env[RUN_ENV]
    STARTED[task_id] = time.time()
    EXIT_CODES.pop(task_id, None)
    threading.Thread(target=read_output, args=(task_id, process), daemon=True).start()
    return process

@app.post("/api/work-guard/refresh")
def refresh_work_guard():
    with TASK_LOCK:
        if status("diantao-work-guard")["status"] != "running":
            raise HTTPException(409, "请先启动打工守护")
        request_refresh()
    return {"ok": True, "message": "已请求重新识别，守护将接管手机校准倒计时"}


@app.post("/api/stop/{task_id}")
def stop_task(task_id: str):
    require_task(task_id)
    with TASK_LOCK:
        stop_process(task_id)
    return {"ok": True, "message": "任务已停止"}


@app.get("/api/logs/{task_id}")
def get_logs(task_id: str, tail: int = 100):
    require_task(task_id)
    with LOG_LOCK:
        lines = [text for _, text in list(LOGS.get(task_id, []))]
    return {"lines": lines[-max(1, min(tail, 500)):],
            "running": status(task_id)["status"] == "running", "total": len(lines)}


@app.get("/api/logs/{task_id}/stream")
def stream_logs(task_id: str):
    require_task(task_id)

    async def events():
        sequence = 0
        while True:
            with LOG_LOCK:
                entries = list(LOGS.get(task_id, []))
            for number, text in entries:
                if number > sequence:
                    yield "data: " + json.dumps({"type": "log", "text": text}, ensure_ascii=False) + "\n\n"
                    sequence = number
            if status(task_id)["status"] != "running":
                # The reader may still be draining the final stdout lines.
                await asyncio.sleep(.1)
                with LOG_LOCK:
                    entries = list(LOGS.get(task_id, []))
                for number, text in entries:
                    if number > sequence:
                        yield "data: " + json.dumps({"type": "log", "text": text}, ensure_ascii=False) + "\n\n"
                yield 'data: {"type":"exit","text":"任务已结束"}\n\n'
                return
            await asyncio.sleep(.2)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def desktop():
    import uvicorn
    import webview
    import urllib.request

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID
            )
        except (AttributeError, OSError):
            pass

    listener = socket.socket()
    if sys.platform == "win32":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    # Bind once, then hand the same socket to the server: another application is never reused.
    try:
        listener.bind(("127.0.0.1", 9060))
    except OSError:
        listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.set_inheritable(True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(.1)
    else:
        raise RuntimeError("工作台启动失败，请查看本地运行环境")
    atexit.register(cleanup)
    icon_name = "tearice-logo.ico" if sys.platform == "win32" else "tearice-logo.png"
    icon = ROOT / "workbench" / "static" / icon_name
    webview.create_window("Tearice · 自动化大管家", url=url, width=1280, height=820,
                          resizable=True, min_size=(700, 500))
    try:
        webview.start(private_mode=False, storage_path=str(RUNTIME / "webview"),
                      icon=str(icon) if icon.is_file() else None)
    finally:
        cleanup()
        server.should_exit = True
        thread.join(timeout=3)
        listener.close()


if __name__ == "__main__":
    desktop()
