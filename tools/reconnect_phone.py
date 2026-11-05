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

# 手机连「无互联网」的电脑热点时，Android 会判定 WiFi 不可用 → 切移动数据、
# 锁屏后 WiFi 休眠，而 Android 11+ 锁屏还会直接关掉「无线调试」。这几项能钉住它。
STABILIZE_SETTINGS = (
    ("captive_portal_mode", "0", "不再把电脑热点判成「无互联网」而切到移动数据"),
    ("wifi_sleep_policy", "2", "锁屏时 WiFi 不休眠"),
    ("stay_on_while_plugged_in", "7", "充电时屏幕常亮，避免锁屏关掉无线调试"),
)


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


def is_alive(ip):
    """ARP 里可能是过期条目；ping 一下区分「手机在线但无线调试没开」。"""
    output = run(["ping", "-n", "1", "-w", "600", ip], timeout=3)
    return "TTL=" in output or "ttl=" in output


def stabilize_phone(adb_path, serial):
    """连上后钉住手机 WiFi/屏幕，减少跑任务途中被系统断掉。"""
    print("[加固] 正在钉住手机 WiFi，减少热点掉线…")
    for key, value, why in STABILIZE_SETTINGS:
        adb(adb_path, "-s", serial, "shell", "settings", "put", "global", key, value)
        actual = adb(adb_path, "-s", serial, "shell", "settings", "get", "global", key).strip()
        flag = "" if actual == value else "  [未生效，可能被厂商 ROM 限制]"
        print(f"  {key} = {actual}  ({why}){flag}")


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


def try_endpoint(adb_path, endpoint, stabilize=False) -> bool:
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
            if stabilize:
                stabilize_phone(adb_path, endpoint)
            return True
        time.sleep(0.6)
    first = output.splitlines()[0] if output else "无响应"
    print(f"  {endpoint} -> {first}")
    return False


def hotspot_restarts(minutes=10):
    """读 Windows WLAN 日志，看电脑热点的「承载网络」最近是否被反复停止/启动。

    8008 = 开始停止承载网络，8006 = 已完成启动承载网络。两者成对出现即一次
    广播中断：这期间 SSID 根本不发射，手机自然「搜不到 WiFi」。返回时间线文本。
    """
    if os.name != "nt":
        return []
    ps = (
        "Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational';"
        f"StartTime=(Get-Date).AddMinutes(-{minutes});Id=@(8008,8006)}} "
        "-ErrorAction SilentlyContinue | Sort-Object TimeCreated | "
        "ForEach-Object { $_.Id.ToString() + ':' + $_.TimeCreated.ToString('HH:mm:ss') }"
    )
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=20)
    if out.startswith("ERR ") or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def report_hotspot_restarts():
    restarts = hotspot_restarts()
    if not restarts:
        return
    stops = sum(1 for r in restarts if r.startswith("8008:"))
    print(f"[诊断] 电脑热点在最近 10 分钟内被系统重启过约 {stops} 次："
          f"{', '.join(restarts[-6:])}（8008=停播 / 8006=恢复）")
    print("  → 停播期间 SSID 不广播，这就是手机「搜不到 WiFi」的原因；"
          "已连上的手机也会被踢掉且不会自动重连")
    print("  → 根因通常是电脑的 USB 无线网卡反复掉线重枚举："
          "换主板后置 USB 口、别用延长线/Hub，清理设备管理器里的残留网卡实例")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="adb.exe 完整路径")
    parser.add_argument("--stabilize", action="store_true",
                        help="连上后设置手机 WiFi 不休眠/充电常亮，减少途中掉线")
    args = parser.parse_args()
    adb_path = args.adb if os.path.isfile(args.adb) else "adb"

    existing = online_devices(adb_path)
    if existing:
        print(f"[OK] 已有可用设备：{existing[0]}，无需重连")
        if args.stabilize:
            stabilize_phone(adb_path, existing[0])
        return 0

    cached = CACHE.read_text(encoding="utf-8").strip() if CACHE.is_file() else ""
    if cached and try_endpoint(adb_path, cached, args.stabilize):
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
    alive = [ip for ip in ips if is_alive(ip)]
    print(f"[扫描] ping 得通的设备：{alive or '无（可能只是手机不回 ping）'}")

    targets = [(ip, port) for ip in ips for port in PORTS]
    print(f"[扫描] 正在查找手机的无线调试端口（{len(targets)} 个，约 20~40 秒）…")
    opened = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for hit in pool.map(probe, targets):
            if hit:
                opened.append(hit)
    print(f"[扫描] 开放端口：{opened[:10]}（共 {len(opened)} 个）")
    for ip, port in opened[:12]:
        if try_endpoint(adb_path, f"{ip}:{port}", args.stabilize):
            return 0

    if not opened:
        if alive:
            print("[失败] 手机在线，但没有任何无线调试端口在监听")
            print("  → Android 11+ 在锁屏、WiFi 切换或判定「无互联网」时会直接关掉「无线调试」")
            print("  → 解锁手机，开发者选项 -> 无线调试 关一次再开（端口会刷新）")
        else:
            print("[失败] 热点里找不到手机，手机可能已切到移动数据或断开热点 WiFi")
    report_hotspot_restarts()
    print("[提示] 最稳的做法：改用 USB 数据线 + 开发者选项「USB 调试」，完全绕开 WiFi")
    return 1


if __name__ == "__main__":
    sys_code = main()
    raise SystemExit(sys_code)
