# -*- coding: utf-8 -*-
"""手机连「电脑移动热点」时，自动找回 adb 无线调试连接。

Windows 移动热点不转发 mDNS 广播，所以工作台的扫码发现 / `adb mdns services`
在这种拓扑下看不到手机。但电脑(192.168.137.1)与手机(192.168.137.x)在同一
网段，可以直接扫手机的无线调试端口再 `adb connect`；手机之前授权过这台电脑
(adbkey 仍在)，端口变了也不必重新扫码配对。

    python tools/reconnect_phone.py [--adb <adb.exe>]

已有可用设备时直接返回；否则先试上次成功的地址，再扫端口。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".runtime" / "phone_endpoint.txt"
DEFAULT_ADB = ROOT / ".vendor" / "platform-tools" / "adb.exe"
PORTS = (5555,) + tuple(range(30000, 50001))
SCAN_WORKERS = 400
SOCKET_TIMEOUT = 0.35
ADB_TIMEOUT = 12


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run(cmd, timeout=ADB_TIMEOUT) -> str:
    try:
        result = subprocess.run(list(cmd), capture_output=True, timeout=timeout)
        return decode(result.stdout + result.stderr).strip()
    except Exception as exc:
        return f"ERR {exc}"


def adb(adb_path, *args, timeout=ADB_TIMEOUT) -> str:
    return run([adb_path, *args], timeout)


def online_devices(adb_path):
    output = adb(adb_path, "devices")
    found = []
    for line in output.splitlines():
        match = re.match(r"^([^\s]+)\s+device$", line.strip())
        if match:
            found.append(match.group(1))
    return found


def local_prefixes():
    """本机各网卡的 /24 前缀，Windows 移动热点段(192.168.137.)优先。"""
    output = run(["ipconfig"], timeout=15)
    prefixes = set()
    for ip in re.findall(r"IPv4[^\d]*(\d+\.\d+\.\d+\.\d+)", output):
        if ip.startswith(("127.", "169.254.")):
            continue
        prefixes.add(".".join(ip.split(".")[:3]) + ".")
    return sorted(prefixes, key=lambda p: 0 if p == "192.168.137." else 1)


def candidate_ips(prefixes):
    """从 ARP 表取同网段客户端；热点下手机一定在这里。"""
    output = run(["arp", "-a"], timeout=15)
    found = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})", line)
        if not match:
            continue
        ip = match.group(1)
        if not any(ip.startswith(p) for p in prefixes):
            continue
        if ip.endswith((".0", ".1", ".255")) or ip.startswith(("224.", "239.")):
            continue
        if ip not in found:
            found.append(ip)
    return found


def probe(item):
    ip, port = item
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(SOCKET_TIMEOUT)
    try:
        if sock.connect_ex((ip, port)) == 0:
            return (ip, port)
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


def try_endpoint(adb_path, endpoint) -> bool:
    """adb connect 可能返回 0 但实际没连上，必须用 adb devices 复核。"""
    output = adb(adb_path, "connect", endpoint)
    for _ in range(3):
        if endpoint in online_devices(adb_path):
            print(f"[OK] 已连接手机：{endpoint}")
            try:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(endpoint, encoding="utf-8")
            except Exception:
                pass
            return True
        time.sleep(0.6)
    first = output.splitlines()[0] if output else "无响应"
    print(f"  {endpoint} -> {first}")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="adb.exe 完整路径")
    args = parser.parse_args()
    adb_path = args.adb if os.path.isfile(args.adb) else "adb"

    existing = online_devices(adb_path)
    if existing:
        print(f"[OK] 已有可用设备：{existing[0]}，无需重连")
        return 0

    cached = CACHE.read_text(encoding="utf-8").strip() if CACHE.is_file() else ""
    if cached and try_endpoint(adb_path, cached):
        return 0

    prefixes = local_prefixes()
    ips = candidate_ips(prefixes)
    if cached:
        cached_ip = cached.split(":")[0]
        if cached_ip not in ips:
            ips.insert(0, cached_ip)
    print(f"[扫描] 本机网段 {prefixes or '未知'}，候选设备 {ips or '无'}")
    if not ips:
        print("[失败] 热点网段里没发现设备，请确认手机仍连着电脑的移动热点")
        return 1

    targets = [(ip, port) for ip in ips for port in PORTS]
    print(f"[扫描] 正在查找手机的无线调试端口（{len(targets)} 个，约 20~40 秒）…")
    opened = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for hit in pool.map(probe, targets):
            if hit:
                opened.append(hit)
    print(f"[扫描] 开放端口：{opened[:10]}（共 {len(opened)} 个）")
    for ip, port in opened[:12]:
        if try_endpoint(adb_path, f"{ip}:{port}"):
            return 0

    print("[失败] 没找到可用的无线调试连接，请依次检查：")
    print("  1. 手机连的是【电脑的移动热点】，不是别的 WiFi")
    print("  2. 手机 开发者选项 -> 无线调试 已打开（关一次再开会刷新端口）")
    print("  3. 手机已解锁、屏幕亮着")
    print("  4. 仍不行就改用 USB 数据线 + 「USB 调试」")
    return 1


if __name__ == "__main__":
    sys_code = main()
    raise SystemExit(sys_code)
