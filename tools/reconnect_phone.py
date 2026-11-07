# -*- coding: utf-8 -*-
"""手机连「电脑移动热点」时，自动找回 adb 无线调试连接。

Windows 移动热点不转发 mDNS 广播，所以工作台的扫码发现 / `adb mdns services`
在这种拓扑下看不到手机。但电脑(192.168.137.1)与手机(192.168.137.x)在同一
网段，可以直接扫手机的无线调试端口再 `adb connect`；手机之前授权过这台电脑
(adbkey 仍在)，端口变了也不必重新扫码配对。

    python tools/reconnect_phone.py [--adb <adb.exe>] [--stabilize]
    python tools/reconnect_phone.py [--adb <adb.exe>] --hotspot-first --stabilize

已有可用设备时直接返回；否则先试上次成功的地址，再扫端口。
加 --force-hotspot（任务启动阶段）或 --hotspot-first（任务运行中掉线）时，
**先**把电脑移动热点关一次再开（WinRT Start/StopTetheringAsync），
等手机重新关联后再扫一遍。本机实测：On -> Stop(Success) -> Off ->
Start(Success) -> On，SSID 会重新广播。运行中掉线由 watch_phone.py 守护
带上 --hotspot-first，保证进行中掉线与启动阶段一样「先关开热点」。

开关热点有副作用（会踢掉所有客户端），因此启动阶段忽略冷却、运行中 45s 冷却、
常规兜底 120s 冷却，每次运行最多一次、且必须确认最终状态回到 On；
失败会明确报错，不会把热点留在关闭状态。加 --no-hotspot 可完全禁用这一步。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import os
import re
import socket
import subprocess
import threading
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
# 「任务进行中掉线」专用冷却：比启动阶段短，保证掉线时基本能真的关开一次；
# 又比不设冷却安全——反复关开热点会把手机反复踢下线，反而连不回来。
RUNTIME_HOTSPOT_COOLDOWN = 45
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


def powershell_stream(script, timeout=110):
    """同 powershell()，但把输出逐行实时打印。

    关开热点要 10~30 秒，用 subprocess.run 会整段静默，用户会以为脚本卡死
    或「根本没关热点」；这里让 STATE_BEFORE / STOP / REACHED_OFF / START /
    FINAL 一行一行出现在任务日志里，同时仍返回完整文本供上层解析。
    """
    if os.name != "nt":
        return ""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    chunks = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as exc:
        return f"ERR {exc}"

    def _kill_when_stuck():
        try:
            if proc.poll() is None:
                proc.kill()
                print("  ⚠ 热点开关脚本超时，已强制结束", flush=True)
        except OSError:
            pass

    watchdog = threading.Timer(timeout, _kill_when_stuck)
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            chunks.append(line)
            print("  " + line, flush=True)
        proc.wait()
    except Exception as exc:
        chunks.append(f"ERR {exc}")
    finally:
        watchdog.cancel()
        try:
            proc.stdout.close()
        except Exception:
            pass
    return "\n".join(chunks)


def hotspot_state():
    out = powershell(HOTSPOT_STATE_PS, timeout=25)
    hit = re.search(r"^STATE=(\w+)", out, re.M)
    return hit.group(1) if hit else "Unknown"


def hotspot_cooldown_active(cooldown=HOTSPOT_COOLDOWN):
    try:
        last = float(HOTSPOT_TS.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    left = cooldown - (time.time() - last)
    if left > 0:
        print(f"[热点] 距上次重新开关热点仅 {cooldown - left:.0f}s，"
              f"冷却中（{left:.0f}s 后可再来，本次冷却 {cooldown}s），本次跳过", flush=True)
        return True
    return False


def mark_hotspot_restart():
    try:
        HOTSPOT_TS.parent.mkdir(parents=True, exist_ok=True)
        HOTSPOT_TS.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def toggle_hotspot(force=False, cooldown=HOTSPOT_COOLDOWN):
    """把电脑移动热点关一次再开。返回最终是否确实回到 On（广播中）。

    安全性：只有当前 On 才 Stop；Start 失败会再试一次；仍失败则重启 icssvc
    并复核状态。无论结果如何都写冷却时间戳，避免反复把热点开来开去。
    force=True 用于「任务启动时一台设备都没有」：忽略冷却（那是给运行期
    守护防抖用的），但仍保持每次运行最多一次、且必须回到 On。
    cooldown 可缩短：任务进行中掉线用 RUNTIME_HOTSPOT_COOLDOWN=45s。
    """
    if os.name != "nt":
        return False
    if not force and hotspot_cooldown_active(cooldown):
        return False
    if force:
        print("[热点] 任务启动时检测不到设备，忽略冷却，电脑移动热点关一次再开…", flush=True)
    else:
        print("[热点] 手机连接掉了，电脑移动热点关一次再开…", flush=True)
    print("[热点] 正在关闭再打开（约 10~30 秒），下面会实时显示每一步…", flush=True)
    out = powershell_stream(HOTSPOT_PS, timeout=110)
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
    parser.add_argument("--force-hotspot", action="store_true",
                        help="任务启动阶段使用：忽略 120s 冷却，连不上必定关开一次热点（每次运行仍最多一次）")
    parser.add_argument("--hotspot-first", action="store_true",
                        help="任务运行中掉线使用：先关开一次电脑热点再扫端口（45s 冷却防抖，每次运行最多一次）")
    args = parser.parse_args()
    adb_path = args.adb if os.path.isfile(args.adb) else "adb"

    existing = online_devices(adb_path)
    if existing:
        print(f"[OK] 已有可用设备：{existing[0]}，无需重连，也不会动热点", flush=True)
        if args.stabilize:
            stabilize_phone(adb_path, existing[0])
        return 0

    print("[重连] adb 里没有任何设备，开始自动找回手机…", flush=True)
    cached = CACHE.read_text(encoding="utf-8").strip() if CACHE.is_file() else ""
    # 任务运行中掉线：使用者要求「也先重新开关一次热点」，所以跳过缓存直连，
    # 直接进热点优先分支；启动阶段仍先试缓存地址（能连上就不必踢掉手机）。
    if cached and not args.hotspot_first:
        print(f"[重连] 先试上次成功的地址 {cached}（约 1~12 秒）…", flush=True)
        if try_endpoint(adb_path, cached, args.stabilize):
            return 0

    prefixes = local_prefixes()
    ips = candidate_ips(prefixes)
    if cached and cached.split(":")[0] not in ips:
        ips.insert(0, cached.split(":")[0])
    print(f"[重连] 本机网段 {prefixes or '未知'}，候选手机 IP {ips or '无'}", flush=True)

    connected, alive, opened = False, [], False
    hotspot_done = False
    hotspot_attempted = False   # 每次运行最多关开一次，失败也不重试

    # ── 热点优先（启动阶段 --force-hotspot / 运行中掉线 --hotspot-first）──
    # 旧顺序是「先扫 2 万个端口（20~40s）→ 全失败才关热点」，前面几十秒日志
    # 一片空白，用户会以为脚本没关热点、甚至中途点停止把重连进程整棵树杀掉，
    # 结果热点永远没被关过（.runtime/hotspot_restart.ts 都不存在）。
    # 现在启动与「任务进行中掉线」都把热点开关提到最前面，关开过程逐行实时输出，
    # 几秒内就能在 Windows 热点磁贴和工作台执行日志里看到变化。
    hotspot_first = args.force_hotspot or args.hotspot_first
    if hotspot_first and not args.no_hotspot:
        if args.hotspot_first:
            print("[运行自愈] 任务进行中手机连接掉了 → 现在就关开一次电脑移动热点…", flush=True)
        else:
            print("[启动自愈] 没检测到设备 → 现在就关开一次电脑移动热点…", flush=True)
        hotspot_attempted = True
        hotspot_done = toggle_hotspot(
            force=args.force_hotspot,
            cooldown=RUNTIME_HOTSPOT_COOLDOWN if args.hotspot_first else HOTSPOT_COOLDOWN,
        )
        if hotspot_done:
            back = wait_phone_back(prefixes, ips)
            rescan_ips = back or ips
            connected, alive, opened = scan_and_connect(
                adb_path, prefixes, "", args.stabilize, ips=rescan_ips,
                label="热点重启后扫描")
            if connected:
                return 0
        else:
            # 运行中掉线多数是「刚关过热点、手机还没回来」的冷却期；此时不重复
            # 开关（反复踢手机反而更连不上），直接扫端口找回它，并在日志里说清楚。
            print("  [热点] 本次未再关开（刚关过仍在冷却，或开关未成功），"
                  "直接按常规方式扫描端口找回手机", flush=True)

    # 常规流程：热点已优先关开过就不再重复扫描，否则先扫一次端口。
    if not connected and not (hotspot_first and hotspot_done):
        if not ips:
            print("[扫描] 热点网段里没有设备，手机可能已被热点停播踢掉", flush=True)
            connected, alive2, opened2 = False, [], False
        else:
            connected, alive2, opened2 = scan_and_connect(
                adb_path, prefixes, cached, args.stabilize, ips=ips, label="端口扫描")
            if connected:
                return 0
        alive = alive2 or alive
        opened = opened or opened2

    # 未走「热点优先」的调用（如手动双击 重连手机.cmd）：扫描失败后才关开热点
    # 再重扫（每次运行最多关开一次）。带 --hotspot-first 的守护已在最前面关过。
    if not connected and not args.no_hotspot and not hotspot_attempted and toggle_hotspot():
        back = wait_phone_back(prefixes, ips)
        rescan_ips = back or ips
        connected, alive2, opened2 = scan_and_connect(
            adb_path, prefixes, "", args.stabilize, ips=rescan_ips,
            label="热点重启后重扫")
        if connected:
            return 0
        alive = alive2 or alive
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
