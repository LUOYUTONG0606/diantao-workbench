"""跨进程手机使用权：普通任务让出后，守护才可以开始操作。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import uuid


CONTROL_ENV = "DIANTAO_CONTROL_DIR"
DEFAULT_DIR = Path(__file__).resolve().parent / ".runtime" / "task_control"


def control_dir():
    return Path(os.environ.get(CONTROL_ENV, DEFAULT_DIR))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.01)


def read_json(path):
    for attempt in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.01)


def remove_file(path):
    for attempt in range(10):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.01)


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            # 无权查询时保守视为存活；不存在的 PID 返回 ERROR_INVALID_PARAMETER。
            return ctypes.get_last_error() != 87
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def priority_pending(directory=None):
    directory = Path(directory) if directory is not None else control_dir()
    for path in sorted((directory / "requests").glob("*.json")):
        try:
            request = read_json(path)
        except PermissionError:
            return True
        if request and process_alive(request["pid"]):
            return True
        remove_file(path)
    return False


class PhoneLock:
    """使用系统文件锁；持有进程退出时由系统释放，不靠删除锁文件解锁。"""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else control_dir()
        self.handle = None

    def acquire(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = (self.directory / "phone.lock").open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


@contextmanager
def guardian_turn(directory=None):
    """请求抢占，等待普通任务停止整棵工作进程树并释放使用权。"""
    directory = Path(directory) if directory is not None else control_dir()
    request = directory / "requests" / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    write_json(request, {"pid": os.getpid()})
    lock = PhoneLock(directory)
    try:
        print("[调度] 守护请求手机使用权，等待当前工作进程树退出", flush=True)
        while not lock.acquire():
            time.sleep(.1)
        print("[调度] 已取得手机使用权，开始守护操作", flush=True)
        yield
    finally:
        lock.release()
        remove_file(request)
        print("[调度] 守护已让出手机，原流程可从当前任务恢复", flush=True)
