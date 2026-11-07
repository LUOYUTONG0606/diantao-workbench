# -*- coding: utf-8 -*-
"""任务运行期间的手机连接守护。

每 12 秒看一次 `adb devices`；连续确认掉线后，调用 reconnect_phone.py
（它会把电脑移动热点关一次再开、等手机重新关联、再重扫无线调试端口）。
本脚本只管「连接」，不碰淘金币任务.py 的任何逻辑，因此不会影响任务本身。

由「启动淘金币.cmd」在设备检查通过后启动；任务结束写入
`.runtime/watch_phone.stop` 标志，守护看到即退出。日志在
`.runtime/watch_phone.log`，用 pythonw 运行时无控制台窗口。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
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


def acquire_single_instance():
    """用本机端口做进程锁：cmd 与工作台可能都想起守护，只留一个。

    持有 socket 直到进程退出，第二个实例 bind 失败即秒退，
    避免两个守护同时把电脑热点开来开去。
    """
    global _LOCK_SOCKET
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(LOCK_ADDR)
        sock.listen(1)
    except OSError:
        sock.close()
        return False
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
    """同步跑一次重连（含热点关开），把输出落到守护日志便于排查。"""
    py = str(CONSOLE_PY) if CONSOLE_PY.is_file() else sys.executable
    log("调用重连脚本（会关开一次电脑热点）…")
    try:
        result = subprocess.run(
            [py, "-u", str(RECONNECT), "--adb", adb_path],
            capture_output=True, timeout=300,
        )
        text = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                log("  | " + line.rstrip()[:200])
        log(f"重连脚本退出码 {result.returncode}")
    except subprocess.TimeoutExpired:
        log("重连脚本超时（5 分钟），放弃本次恢复")
    except Exception as exc:
        log(f"重连脚本异常：{type(exc).__name__}: {exc}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(DEFAULT_ADB))
    parser.add_argument("--interval", type=int, default=12, help="检查间隔（秒）")
    parser.add_argument("--max-minutes", type=int, default=120, help="最长守护时长，兜底防残留")
    args = parser.parse_args()
    adb_path = args.adb if os.path.isfile(args.adb) else "adb"
    if not acquire_single_instance():
        log("已有手机连接守护在运行，本实例退出（避免两个守护同时开关热点）")
        return 0
    try:
        STOP_FLAG.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass

    deadline = time.monotonic() + args.max_minutes * 60
    log(f"守护启动（每 {args.interval}s 检查一次，最长 {args.max_minutes} 分钟）")
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
