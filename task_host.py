"""管理当前工作进程树：守护到期时结束工作，守护结束后按大任务断点续跑。"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from phone_control import PhoneLock, control_dir, priority_pending, write_json, remove_file
from task_checkpoint import SESSION_ENV, SCOPE_ENV, current_task, snapshot, restore_snapshot


NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def hosted_command(command, python=None):
    return [python or sys.executable, "-u", str(Path(__file__).resolve()), "--", *command]


def stop_tree(process):
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, creationflags=NO_WINDOW, timeout=15,
        )
        if result.returncode and process.poll() is None:
            raise RuntimeError("无法结束工作进程树，不能让守护接管手机")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def run_host(command, *, should_stop=lambda: False, directory=None):
    directory = Path(directory) if directory is not None else control_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock = PhoneLock(directory)
    process = None
    reader = None
    interrupted = False
    # 会话目录只属于这次运行，退出后不提供跨重启恢复。
    with tempfile.TemporaryDirectory(prefix="run-", dir=directory) as session:
        env = os.environ.copy()
        env.update({SESSION_ENV: session, SCOPE_ENV: "root",
                    "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        try:
            while not should_stop():
                if priority_pending(directory) or not lock.acquire():
                    time.sleep(.1)
                    continue
                if priority_pending(directory):
                    lock.release()
                    continue
                if should_stop():
                    lock.release()
                    break
                fallback = next((Path(arg).name for arg in command[1:]
                                 if arg.lower().endswith(".py")), Path(command[0]).name)
                label = current_task(session, fallback)
                print(f"[调度] {'恢复当前任务' if interrupted else '开始流程'}：{label}",
                      flush=True)
                process = subprocess.Popen(
                    command, env=env, creationflags=NO_WINDOW,
                    start_new_session=sys.platform != "win32",
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )

                def forward(output):
                    try:
                        for line in output:
                            print(line.rstrip("\r\n"), flush=True)
                    finally:
                        output.close()

                reader = threading.Thread(target=forward, args=(process.stdout,), daemon=True)
                reader.start()
                write_json(directory / "owner.json", {
                    "host_pid": os.getpid(), "worker_pid": process.pid,
                    "session": session, "task": label,
                })
                while process.poll() is None:
                    if should_stop():
                        stop_tree(process)
                        return 130
                    if priority_pending(directory):
                        saved = snapshot(session)
                        label = current_task(session, label, records=saved)
                        print(f"[让路] 保存当前大任务：{label}；结束工作进程树", flush=True)
                        stop_tree(process)
                        restore_snapshot(session, saved)
                        interrupted = True
                        break
                    time.sleep(.1)
                else:
                    reader.join(timeout=1)
                    return process.returncode
                reader.join(timeout=1)
                process = None
                lock.release()
            return 130
        finally:
            if process is not None:
                stop_tree(process)
            if reader is not None:
                reader.join(timeout=1)
            # 停止工作并清理所有权信息后，才允许守护取得系统锁。
            owner_path = directory / "owner.json"
            from phone_control import read_json
            owner = read_json(owner_path)
            if owner and owner["host_pid"] == os.getpid():
                remove_file(owner_path)
            lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("需要提供待管理的命令")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: stop.set())
    return run_host(command, should_stop=stop.is_set)


if __name__ == "__main__":
    raise SystemExit(main())
