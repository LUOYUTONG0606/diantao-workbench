# -*- coding: utf-8 -*-
"""任务运行期间的手机连接守护。

每 12 秒看一次 `adb devices`；连续确认掉线后，调用 reconnect_phone.py
带 --hotspot-first：**先**把电脑移动热点关一次再开、等手机重新关联、
再重扫无线调试端口（和任务启动阶段一样，不再等 20~40s 长扫描失败后才关）。
本脚本只管「连接」，不碰淘金币任务.py 的任何逻辑，因此不会影响任务本身。

由「启动淘金币.cmd」或工作台在任务启动时拉起；任务结束写入
`.runtime/watch_phone.stop` 标志，守护看到即退出。日志同时写
`.runtime/watch_phone.log` 和 stdout（工作台把 stdout 接进「执行日志」，
所以进行中掉线的每一步都能在页面上看到）；pythonw 运行时无控制台窗口。

若旧守护还占着单实例锁，新守护会写停止标志请它退出、最多等 16s 接管，
避免「改了代码却还是上一版守护在跑」。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOP_FLAG = ROOT / ".runtime" / "watch_phone.stop"
LOG = ROOT / ".runtime" / "watch_phone.log"
RECONNECT = ROOT / "tools" / "reconnect_phone.py"
CONSOLE_PY = ROOT / "workbench" / ".venv" / "Scripts" / "python.exe"
DEFAULT_ADB = ROOT / ".vendor" / "platform-tools" / "adb.exe"
LOCK_ADDR = ("127.0.0.1", 47831)
_LOCK_SOCKET = None


def acquire_single_instance(wait_seconds=16):
    """用本机端口做进程锁：cmd 与工作台可能都想起守护，只留一个。

    锁被旧守护占着时不再秒退（否则改了代码、跑的还是上一版守护）：
    写停止标志请旧守护退出，最多等 wait_seconds 秒接管；超时才放弃。
    接管成功后 main() 会清掉停止标志，避免自己一启动就退出。
    """
    global _LOCK_SOCKET
    deadline = time.monotonic() + wait_seconds
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(LOCK_ADDR)
            sock.listen(1)
        except OSError:
            sock.close()
            if time.monotonic() >= deadline:
                return False
            try:
                STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
                STOP_FLAG.write_text("stop", encoding="utf-8")
            except OSError:
                pass
            time.sleep(1)
            continue
        _LOCK_SOCKET = sock
        return True


def log(message):
    line = f"{time.strftime('%H:%M:%S')} {message}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass  # pythonw 无控制台，print 可能不可用


def online_devices(adb_path):
    try:
        result = subprocess.run([adb_path, "devices"], capture_output=True, timeout=12)
        text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        return [ln.split()[0] for ln in text.splitlines()
                if ln.strip().endswith("device")]
    except Exception:
        return []


def recover(adb_path):
    """任务进行中掉线：先关开一次电脑热点再扫端口，输出逐行实时进日志。"""
    py = str(CONSOLE_PY) if CONSOLE_PY.is_file() else sys.executable
    log("任务进行中手机连接掉了 → 现在就重新开关一次电脑热点（和启动时一样优先）…")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)  # 跨 venv 子进程不能继承宿主的包搜索路径
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    command = [py, "-u", str(RECONNECT), "--adb", adb_path,
               "--hotspot-first", "--stabilize"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags,
        )
    except OSError as exc:
        log(f"重连脚本启动失败：{exc}")
        return

    def _kill_when_stuck():
        try:
            if proc.poll() is None:
                proc.kill()
                log("  ⚠ 重连脚本超过 5 分钟，已强制结束")
        except OSError:
            pass

    watchdog = threading.Timer(300, _kill_when_stuck)
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line.strip():
                log("  | " + line[:200])
        proc.wait()
        log(f"重连脚本退出码 {proc.returncode}")
    except Exception as exc:
        log(f"重连脚本异常：{type(exc).__name__}: {exc}")
    finally:
        watchdog.cancel()
        try:
            proc.stdout.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(DEFAULT_ADB))
    parser.add_argument("--interval", type=int, default=12, help="检查间隔（秒）")
    parser.add_argument("--max-minutes", type=int, default=120, help="最长守护时长，兜底防残留")
    args = parser.parse_args()
    adb_path = args.adb if os.path.isfile(args.adb) else "adb"
    if not acquire_single_instance():
        # 等不到锁时撤掉接管用的停止标志：别让旧守护稍后读到它退出，
        # 却没人接班（旧守护正卡在重连里时最容易出现这种「守护真空」）。
        try:
            STOP_FLAG.unlink()
        except OSError:
            pass
        log("旧守护 16s 内没让出连接锁，本实例退出（避免两个守护同时开关热点）；"
            "旧守护继续负责连接，如需新版守护请重启工作台或结束旧的 watch_phone.py 进程")
        return 0
    try:
        STOP_FLAG.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass

    deadline = time.monotonic() + args.max_minutes * 60
    log(f"守护启动（每 {args.interval}s 检查一次，最长 {args.max_minutes} 分钟；"
        f"运行中掉线会先关开一次电脑热点再扫端口）")
    while time.monotonic() < deadline:
        if STOP_FLAG.exists():
            log("收到停止标志，退出")
            return 0
        if online_devices(adb_path):
            time.sleep(args.interval)
            continue
        # 二次确认：避免 adb 服务重启 / 页面切换时的一次性抖动误触发热点开关
        time.sleep(3)
        if STOP_FLAG.exists():
            log("收到停止标志，退出")
            return 0
        if online_devices(adb_path):
            log("adb 短暂抖动后已自行恢复，不动热点")
            continue
        log("adb 连续两次读不到设备 → 判定手机连接掉了")
        recover(adb_path)
        # 给重连脚本和手机一点时间，避免紧接着又判掉线
        time.sleep(10)
    log("达到最长守护时长，自动退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
