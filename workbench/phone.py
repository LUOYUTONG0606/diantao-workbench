"""Loopback ADB pairing API used by the workbench's single QR entry."""
from __future__ import annotations

import asyncio
import base64
import io
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

from fastapi import APIRouter, HTTPException
import qrcode
from qrcode.image.svg import SvgPathFillImage

router = APIRouter(prefix="/api/adb")
SESSIONS: dict[str, dict] = {}
PAIR_LOCK = asyncio.Lock()
TTL = 180
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def find_adb():
    configured = os.environ.get("DIANTAO_ADB_PATH", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("adb")
    if found:
        return found
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             str(Path.home() / "AppData" / "Local" / "Android" / "Sdk")]
    for root in roots:
        if root:
            candidate = Path(root) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
            if candidate.is_file():
                return str(candidate)
    return None


def exec_adb(args, timeout=15):
    executable = find_adb()
    if not executable:
        return {"ok": False, "message": "未找到 ADB，请安装 Platform-Tools 或设置 DIANTAO_ADB_PATH", "stdout": ""}
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW,
        )
        output = result.stdout.strip()
        return {"ok": result.returncode == 0, "stdout": output,
                "message": output or result.stderr.strip() or "ADB 未返回结果"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "message": "ADB 请求超时，请检查手机与网络"}
    except OSError:
        return {"ok": False, "stdout": "", "message": "无法执行 ADB，请检查安装路径"}


def parse_mdns(line):
    parts = line.split()
    if len(parts) != 3:
        return None
    match = re.fullmatch(r"(?:\[([0-9a-fA-F:]+)\]|(\d+(?:\.\d+){3})):(\d+)", parts[2])
    if not match:
        return None
    return {"instance": parts[0], "type": parts[1].rstrip("."),
            "host": match[1] or match[2], "port": match[3], "endpoint": parts[2]}


def session_for(session_id):
    session = SESSIONS.get(session_id)
    if not session or time.monotonic() - session["created"] > TTL:
        SESSIONS.pop(session_id, None)
        return None
    return session


@router.get("/status")
async def device_status():
    executable = find_adb()
    if not executable:
        return {"installed": False, "devices": []}
    result = await asyncio.to_thread(exec_adb, ["devices", "-l"])
    devices = []
    for line in result["stdout"].splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(("List of devices", "*")):
            devices.append({"serial": parts[0], "status": parts[1], "info": " ".join(parts[2:])})
    if not result["ok"]:
        raise HTTPException(503, result["message"])
    return {"installed": True, "devices": devices}


@router.post("/qr-generate")
def generate_qr():
    if not find_adb():
        raise HTTPException(400, "未找到 ADB，请先安装 Platform-Tools 或设置 DIANTAO_ADB_PATH")
    for key in list(SESSIONS):
        session_for(key)
    service = "studio-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(12)
    payload = f"WIFI:T:ADB;S:{service};P:{password};;"
    output = io.BytesIO()
    qrcode.make(payload, image_factory=SvgPathFillImage).save(output)
    session_id = secrets.token_hex(16)
    SESSIONS[session_id] = {"service": service, "password": password, "created": time.monotonic(),
                            "status": "waiting", "device": None}
    return {"session_id": session_id,
            "qr_image": "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode()}


@router.get("/qr-poll/{session_id}")
async def poll_qr(session_id: str):
    async with PAIR_LOCK:
        session = session_for(session_id)
        if not session:
            return {"status": "expired"}
        if session["status"] != "waiting":
            return {"status": session["status"]}
        discovered = await asyncio.to_thread(exec_adb, ["mdns", "services"])
        if not discovered["ok"]:
            raise HTTPException(503, discovered["message"])
        for line in discovered["stdout"].splitlines():
            target = parse_mdns(line)
            if (target and target["instance"] == session["service"]
                    and target["type"] == "_adb-tls-pairing._tcp"):
                result = await asyncio.to_thread(
                    exec_adb, ["pair", target["endpoint"], session["password"]], 30
                )
                # A cancelled session must not be resurrected by an in-flight reply.
                if session_id not in SESSIONS:
                    return {"status": "cancelled"}
                session["status"] = "paired" if result["ok"] and "Successfully paired" in result["stdout"] else "pair_failed"
                session["device"] = target
                break
        return {"status": session["status"]}


@router.post("/qr-connect/{session_id}")
async def connect_qr(session_id: str):
    session = session_for(session_id)
    if not session or session["status"] not in ("paired", "connected"):
        return {"ok": False, "message": "请先完成扫码配对"}
    discovered = await asyncio.to_thread(exec_adb, ["mdns", "services"])
    if not discovered["ok"]:
        return {"ok": False, "message": discovered["message"]}
    for line in discovered["stdout"].splitlines():
        target = parse_mdns(line)
        if (target and target["type"] == "_adb-tls-connect._tcp"
                and target["host"] == session["device"]["host"]):
            result = await asyncio.to_thread(exec_adb, ["connect", target["endpoint"]])
            if not result["ok"]:
                return {"ok": False, "message": result["message"]}
            # adb connect can return exit code 0 even when the connection was refused.
            devices = await asyncio.to_thread(exec_adb, ["devices"])
            if any(line.split()[:2] == [target["endpoint"], "device"]
                   for line in devices["stdout"].splitlines()):
                session["status"] = "connected"
                return {"ok": True, "message": "手机已连接"}
    return {"ok": False, "message": "暂未找到可用连接端口，请保持无线调试开启"}


@router.post("/qr-cancel/{session_id}")
def cancel_qr(session_id: str):
    SESSIONS.pop(session_id, None)
    return {"ok": True}
