# -*- coding: utf-8 -*-
"""手机连「电脑移动热点」时，自动找回 adb 无线调试连接。

Windows 移动热点不转发 mDNS 广播，所以工作台的扫码发现 / `adb mdns services`
在这种拓扑下看不到手机。但电脑(192.168.137.1)与手机(192.168.137.x)在同一
网段，可以直接扫手机的无线调试端口再 `adb connect`；手机之前授权过这台电脑
(adbkey 仍在)，端口变了也不必重新扫码配对。

    python tools/reconnect_phone.py [--adb <adb.exe>] [--stabilize]

已有可用设备时直接返回；否则先试上次成功的地址，再扫端口。
一次扫描仍连不上（= 手机连接掉了）时，会把电脑的移动热点**关一次再开**
（WinRT Start/StopTetheringAsync），等手机重新关联后再扫一遍。本机实测：
On -> Stop(Success) -> Off -> Start(Success) -> On，SSID 会重新广播。

开关热点有副作用（会踢掉所有客户端），因此有 120s 冷却、每次运行最多一次、
且必须确认最终状态回到 On；失败会明确报错，不会把热点留在关闭状态。
加 --no-hotspot 可完全禁用这一步。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import os
import re
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".runtime" / "phone_endpoint.txt"
HOTSPOT_TS = ROOT / ".runtime" / "hotspot_restart.ts"
DEFAULT_ADB = ROOT / ".vendor" / "platform-tools" / "adb.exe"
PORTS = (5555,) + tuple(range(30000, 50001))
SCAN_WORKERS = 400
SOCKET_TIMEOUT = 0.35
ADB_TIMEOUT = 12

# 热点自愈参数：冷却避免反复开关；重启后最多等这么久让手机重新关联。
HOTSPOT_COOLDOWN = 120
HOTSPOT_WAIT = 25

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


# ---------------------------------------------------------------------------
# 电脑移动热点：关一次再开（WinRT）。
# 旧的 netsh wlan stop/start hostednetwork 在本机「支持的承载网络: 否」，不可用。
# PowerShell 5.1 必须先 Add-Type System.Runtime.WindowsRuntime 才能 AsTask。
# ---------------------------------------------------------------------------
HOTSPOT_PS = r'''
$ErrorActionPreference = 'Continue'
try { Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop } catch {}
$T  = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$NI = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
$RT = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$generic = $null
try {
    foreach ($m in [System.WindowsRuntimeSystemExtensions].GetMethods()) {
        if ($m.Name -eq 'AsTask' -and $m.IsGenericMethodDefinition) {
            $pp = $m.GetParameters()
            if ($pp.Count -eq 1 -and $pp[0].ParameterType.Name -eq 'IAsyncOperation`1') { $generic = $m; break }
        }
    }
} catch {}
function Invoke-Tether($mgr, $verb, $RT, $generic) {
    try {
        if ($verb -eq 'Stop') { $op = $mgr.StopTetheringAsync() } else { $op = $mgr.StartTetheringAsync() }
    } catch { return ('CALL_ERR ' + $_.Exception.Message) }
    if ($null -ne $generic -and $null -ne $RT) {
        try {
            $m = $generic.MakeGenericMethod($RT)
            $task = $m.Invoke($null, @($op))
            if ($task.Wait(20000)) { return ('AWAIT ' + $task.Result.Status) }
            return 'AWAIT_TIMEOUT'
        } catch { return ('AWAIT_ERR ' + $_.Exception.Message) }
    }
    return 'FIRE_AND_FORGET'
}
function Wait-State($mgr, $want, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if ($mgr.TetheringOperationalState.ToString() -eq $want) { return $true }
        Start-Sleep -Milliseconds 400
    }
    return $false
}
try {
    $profile = $NI::GetInternetConnectionProfile()
    if ($null -eq $profile) { Write-Output 'NO_INTERNET_PROFILE'; Write-Output 'FINAL=Unknown'; exit }
    $mgr = $T::CreateFromConnectionProfile($profile)
    $before = $mgr.TetheringOperationalState.ToString()
    Write-Output ('STATE_BEFORE=' + $before)
    if ($before -ne 'Off') {
        Write-Output ('STOP=' + (Invoke-Tether $mgr 'Stop' $RT $generic))
        Write-Output ('REACHED_OFF=' + (Wait-State $mgr 'Off' 12))
        Start-Sleep -Seconds 2
    } else {
        Write-Output 'STOP=SKIPPED_ALREADY_OFF'
    }
    Write-Output ('START=' + (Invoke-Tether $mgr 'Start' $RT $generic))
    $ok = Wait-State $mgr 'On' 20
    if (-not $ok) {
        Write-Output ('RETRY=' + (Invoke-Tether $mgr 'Start' $RT $generic))
        $ok = Wait-State $mgr 'On' 15
    }
    Write-Output ('STATE_FINAL=' + $mgr.TetheringOperationalState.ToString())
    Write-Output ('FINAL=' + $(if ($mgr.TetheringOperationalState.ToString() -eq 'On') { 'On' } else { 'Off' }))
    try { Write-Output ('SSID=' + $mgr.GetCurrentAccessPointConfiguration().Ssid) } catch {}
} catch {
    Write-Output ('ERR=' + $_.Exception.Message)
    Write-Output 'FINAL=Unknown'
}
'''

HOTSPOT_STATE_PS = r'''
$ErrorActionPreference = 'Continue'
try {
    $T  = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
    $NI = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
    $mgr = $T::CreateFromConnectionProfile($NI::GetInternetConnectionProfile())
    Write-Output ('STATE=' + $mgr.TetheringOperationalState.ToString())
} catch { Write-Output 'STATE=Unknown' }
'''

HOTSPOT_FALLBACK_PS = r'''
$ErrorActionPreference = 'Continue'
try { Restart-Service icssvc -Force -ErrorAction Stop; Write-Output 'ICS=RESTARTED' }
catch { Write-Output ('ICS_ERR=' + $_.Exception.Message) }
'''


def powershell(script, timeout=90):
    """用 -EncodedCommand 传脚本，绕开 WinRT 类型字面量里的引号地狱。"""
    if os.name != "nt":
        return ""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return run(["powershell", "-NoProfile", "-NonInteractive",
                "-EncodedCommand", encoded], timeout)


def hotspot_state():
    out = powershell(HOTSPOT_STATE_PS, timeout=25)
    hit = re.search(r"^STATE=(\w+)", out, re.M)
    return hit.group(1) if hit else "Unknown"


def hotspot_cooldown_active():
    try:
        last = float(HOTSPOT_TS.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    left = HOTSPOT_COOLDOWN - (time.time() - last)
    if left > 0:
        print(f"[热点] 距上次重新开关热点仅 {HOTSPOT_COOLDOWN - left:.0f}s，"
              f"冷却中（{left:.0f}s 后可再来），本次跳过")
        return True
    return False


def mark_hotspot_restart():
    try:
        HOTSPOT_TS.parent.mkdir(parents=True, exist_ok=True)
        HOTSPOT_TS.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def toggle_hotspot():
    """把电脑移动热点关一次再开。返回最终是否确实回到 On（广播中）。

    安全性：只有当前 On 才 Stop；Start 失败会再试一次；仍失败则重启 icssvc
    并复核状态。无论结果如何都写冷却时间戳，避免反复把热点开来开去。
    """
    if os.name != "nt":
        return False
    if hotspot_cooldown_active():
        return False
    print("[热点] 手机连接掉了，电脑移动热点关一次再开…")
    out = powershell(HOTSPOT_PS, timeout=110)
    for line in out.splitlines():
        if line.startswith(("STATE_BEFORE=", "STOP=", "REACHED_OFF=", "START=",
                            "RETRY=", "STATE_FINAL=", "SSID=", "ERR=",
                            "NO_INTERNET_PROFILE", "FINAL=")):
            print("  " + line)
    hit = re.search(r"^FINAL=(\w+)", out, re.M)
    state = hit.group(1) if hit else "Unknown"
    if state != "On":
        # WinRT 没能把热点开回来：最后兜底，重启承载网络服务并复核
        print("  ⚠ WinRT 未能确认热点已开，尝试重启承载网络服务…")
        fb = powershell(HOTSPOT_FALLBACK_PS, timeout=40)
        for line in fb.splitlines():
            if line.startswith(("ICS=", "ICS_ERR=")):
                print("  " + line)
        time.sleep(3)
        state = hotspot_state()
        print(f"  STATE_AFTER_FALLBACK={state}")
    ok = state == "On"
    if ok:
        print("  ✅ 热点已重新广播（SSID 重新发射），等待手机连回来")
    else:
        print("  ⚠ 热点没能自动打开！请到 设置 → 网络和 Internet → 移动热点 手动开一次")
        print("     （脚本不会把热点留在关闭状态继续跑，避免越弄越糟）")
    mark_hotspot_restart()
    return ok


def wait_phone_back(prefixes, known_ips, timeout=HOTSPOT_WAIT):
    """热点重新广播后，轮询等手机重新关联（ARP 里可能是过期静态条目，要 ping）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ips = candidate_ips(prefixes)
        for ip in known_ips:
            if ip not in ips:
                ips.append(ip)
        alive = [ip for ip in ips if is_alive(ip)]
        if alive:
            print(f"  ✅ 手机已重新连上热点：{alive}")
            return alive
        time.sleep(2)
    print(f"  ⏳ 等了 {timeout}s，热点里还没看到手机（手机可能没自动重连）")
    return []


def scan_and_connect(adb_path, prefixes, cached, stabilize, ips=None, label="扫描"):
    """扫热点网段里的无线调试端口并 adb connect。返回 (连上?, 在线IP, 有开放端口?)。"""
    if ips is None:
        ips = candidate_ips(prefixes)
        if cached and cached.split(":")[0] not in ips:
            ips.insert(0, cached.split(":")[0])
    print(f"[{label}] 本机网段 {prefixes or '未知'}，候选设备 {ips or '无'}")
    if not ips:
        return False, [], False
    alive = [ip for ip in ips if is_alive(ip)]
    print(f"[{label}] ping 得通的设备：{alive or '无（可能只是手机不回 ping）'}")
    targets = [(ip, port) for ip in ips for port in PORTS]
    print(f"[{label}] 正在查找手机的无线调试端口（{len(targets)} 个，约 20~40 秒）…")
    opened = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for hit in pool.map(probe, targets):
            if hit:
                opened.append(hit)
    print(f"[{label}] 开放端口：{opened[:10]}（共 {len(opened)} 个）")
    for ip, port in opened[:12]:
        if try_endpoint(adb_path, f"{ip}:{port}", stabilize):
            return True, alive, True
    return False, alive, bool(opened)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="adb.exe 完整路径")
    parser.add_argument("--stabilize", action="store_true",
                        help="连上后设置手机 WiFi 不休眠/充电常亮，减少途中掉线")
    parser.add_argument("--no-hotspot", action="store_true",
                        help="连不上时不自动关开电脑热点（默认会自动开关一次）")
    args = parser.parse_args()
    adb_path = args.adb if os.path.isfile(args.adb) else "adb"

    existing = online_devices(adb_path)
    if existing:
        print(f"[OK] 已有可用设备：{existing[0]}，无需重连，也不会动热点")
        if args.stabilize:
            stabilize_phone(adb_path, existing[0])
        return 0

    cached = CACHE.read_text(encoding="utf-8").strip() if CACHE.is_file() else ""
    if cached and try_endpoint(adb_path, cached, args.stabilize):
        return 0

    prefixes = local_prefixes()
    ips = candidate_ips(prefixes)
    if cached and cached.split(":")[0] not in ips:
        ips.insert(0, cached.split(":")[0])

    # 热点网段里一个客户端都没有 = 手机被停播踢掉了。这时扫端口纯属浪费 30 秒，
    # 直接先修热点，再扫一次。
    if not ips:
        print("[扫描] 热点网段里没有设备，手机可能已被热点停播踢掉")
        connected, alive, opened = False, [], False
    else:
        connected, alive, opened = scan_and_connect(
            adb_path, prefixes, cached, args.stabilize, ips=ips, label="第 1 次扫描")
        if connected:
            return 0

    # ---- 手机连接掉了：电脑热点关一次再开，再重扫一遍（每次运行最多一次） ----
    if not args.no_hotspot and toggle_hotspot():
        back = wait_phone_back(prefixes, ips)
        rescan_ips = back or ips
        connected, alive2, opened2 = scan_and_connect(
            adb_path, prefixes, "", args.stabilize, ips=rescan_ips,
            label="热点重启后重扫")
        if connected:
            return 0
        if alive2:
            alive = alive2
        opened = opened or opened2

    if not opened:
        if alive:
            print("[失败] 手机在线，但没有任何无线调试端口在监听")
            print("  → Android 11+ 在锁屏、WiFi 切换或判定「无互联网」时会直接关掉「无线调试」")
            print("  → 解锁手机，开发者选项 -> 无线调试 关一次再开（端口会刷新）")
            print("  → 热点已由脚本重新开关过一次；若仍不行，问题在手机的无线调试开关")
        else:
            print("[失败] 热点里找不到手机，手机可能已切到移动数据或断开热点 WiFi")
            print("  → 手机上手动连一次电脑热点 SSID（脚本已让电脑重新广播）")
    report_hotspot_restarts()
    print("[提示] 最稳的做法：改用 USB 数据线 + 开发者选项「USB 调试」，完全绕开 WiFi")
    return 1


if __name__ == "__main__":
    sys_code = main()
    raise SystemExit(sys_code)
