"""确保本机 OCR 服务在跑；没跑就自己拉起来。供 启动淘金币.cmd 调用。

为什么要单独这一步：
  能量任务强依赖 OCR 服务（127.0.0.1:8888）。手动「先开 OCR 窗口、再开任务」
  很容易忘记，结果是任务跑一半报「OCR 服务请求失败」。
  这里做成自动检查 + 自动拉起，少一个出错环节。

注意：强制绕开系统代理访问 127.0.0.1（本机有代理时回环请求会被劫持成 502）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OCR_PYTHON = ROOT / ".venv-ocr" / "Scripts" / "python.exe"
OCR_SERVER = ROOT / "tools" / "ocr_rapid_server.py"
LOG = ROOT / ".runtime" / "ocr_service.log"

# 与 tools/ocr_rapid_server.py 里的 SERVICE_NAME 保持一致。
# 不一致说明本机跑的是旧版本（比如推理线程数还是旧的），要换掉。
EXPECTED_SERVICE = "rapidocr-compat-v2"

# 空 ProxyHandler = 明确不走任何代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(port: int, timeout: float = 2.0):
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def shutdown(port: int, timeout: float = 3.0) -> bool:
    """让旧版本服务自己退出，好换新版。失败则返回 False。"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/shutdown", method="POST")
        with _OPENER.open(req, timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False


def start_service(port: int) -> subprocess.Popen | None:
    if not OCR_PYTHON.is_file():
        print(f"[OCR] 找不到 OCR 环境：{OCR_PYTHON}", flush=True)
        print("[OCR] 请先创建：uv venv .venv-ocr --python 3.12", flush=True)
        return None
    if not OCR_SERVER.is_file():
        print(f"[OCR] 找不到服务脚本：{OCR_SERVER}", flush=True)
        return None

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(LOG, "wb")
    flags = 0
    if sys.platform == "win32":
        # 脱离当前控制台，任务窗口关掉也不影响服务
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    print(f"[OCR] 服务未运行，正在后台启动（端口 {port}）…", flush=True)
    return subprocess.Popen(
        [str(OCR_PYTHON), "-u", str(OCR_SERVER), "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT,
        creationflags=flags, close_fds=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="确保本机 OCR 服务可用")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--wait", type=int, default=90, help="等待就绪的最长秒数")
    args = parser.parse_args()

    info = health(args.port)
    if info and info.get("status") == "ready":
        if info.get("service") == EXPECTED_SERVICE:
            print(f"[OCR] 已就绪（{info.get('service')}）", flush=True)
            return 0
        # 旧版本进程还在跑（参数是旧的，会明显更慢），让它退出后重启
        print(f"[OCR] 发现旧版本服务（{info.get('service')}），正在替换…", flush=True)
        shutdown(args.port)
        for _ in range(10):
            time.sleep(1)
            if health(args.port) is None:
                break
        if health(args.port) is not None:
            print("[OCR] 旧服务未能停止，继续沿用（速度可能偏慢）", flush=True)
            return 0
        start_service(args.port)
    if info:
        print(f"[OCR] 服务在启动中（{info.get('status')}），等待就绪…", flush=True)
    else:
        proc = start_service(args.port)
        if proc is None:
            return 2

    for i in range(args.wait):
        time.sleep(1)
        info = health(args.port)
        if info and info.get("status") == "ready":
            print(f"[OCR] 就绪（用时 {i} 秒）", flush=True)
            return 0
        if i and i % 15 == 0:
            print(f"[OCR] 仍在加载模型…（{i}s）", flush=True)

    print("[OCR] 启动超时。请手动双击「启动OCR服务.cmd」查看报错。", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
