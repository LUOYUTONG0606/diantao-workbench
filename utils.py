import time
import sys
import random
import io
import re
import json
import os
import cv2
import numpy as np
import subprocess
import threading
from datetime import datetime
import traceback
import uiautomator2 as u2
from PIL import Image

# ========================================================
# 📐 包名常量
# ========================================================
TB_APP = "com.taobao.taobao"
DT_APP = "com.taobao.live"       # 点淘
ALIPAY_APP = "com.eg.android.AlipayGphone"
FISH_APP = "com.taobao.idlefish"
TMALL_APP = "com.tmall.wireless"
TMALL_HOME = "com.tmall.wireless.maintab.module.TMMainTabActivity"

# 应用启动配置
APP_START_CONFIG = {
    TB_APP: "com.taobao.tao.welcome.Welcome",
    FISH_APP: "com.taobao.fleamarket.home.activity.InitActivity",
    TMALL_APP: "com.tmall.wireless.maintab.module.TMMainTabActivity",
    ALIPAY_APP: "com.eg.android.AlipayGphone.AlipayLogin"
}
VIDEO_ACTIVITY = ["com.taobao.idlefish.ads.csj.TTAdStandardPortraitActivity"]

# ========================================================
# 🖼️ CodeBuddy 多模态视觉驱动引擎（Flash / DeepSeek / V4.1）
#    替代传统 PaddleOCR/EasyOCR，支持文字识别与语义图标定位
# ========================================================
import io as _io

try:
    from codebuddy_vision import (
        vision_engine,
        codebuddy_ocr,
        find_text_by_codebuddy,
        paddle_ocr_cb,
    )
    HAS_CODEBUDDY_VISION = True
except Exception as _cb_err:
    print(f"⚠️ [CodeBuddy] 初始化失败，将启用云端 OCR 后备: {_cb_err}")
    HAS_CODEBUDDY_VISION = False

from local_config import load_config

load_config()
OCR_SERVER_URL = os.environ.get("DIANTAO_OCR_SERVER_URL", "").strip()

def _cloud_ocr(img, retries=1, log_low_confidence=False):
    """
    调用用户配置的本机或远程 OCR 服务，返回原图中心坐标。
    返回: [{"text": str, "cx": int, "cy": int, "conf": float}]
    """
    if not OCR_SERVER_URL:
        print("  ⚠️ [OCR服务] 请运行配置本机.cmd 设置 DIANTAO_OCR_SERVER_URL；可使用本机OCR服务")
        return []

    # 兼容各类图片输入格式
    if isinstance(img, np.ndarray):
        if len(img.shape) == 3 and img.shape[2] == 3:
            pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        else:
            pil_img = Image.fromarray(img)
    elif isinstance(img, bytes):
        pil_img = Image.open(_io.BytesIO(img))
    elif isinstance(img, Image.Image):
        pil_img = img
    else:
        try:
            pil_img = Image.open(str(img))
        except Exception as e:
            print(f"  ⚠️ [OCR服务] 不支持的图像类型: {type(img)}, {e}")
            return []

    buf = _io.BytesIO()
    pil_img.convert("RGB").save(buf, format="JPEG", quality=70)
    jpeg_bytes = buf.getvalue()

    for attempt in range(retries + 1):
        try:
            import requests as _requests
            t0 = time.time()
            resp = _requests.post(
                OCR_SERVER_URL,
                files={"image": ("screenshot.jpg", jpeg_bytes, "image/jpeg")},
                timeout=45,
                proxies={"http": "", "https": ""},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                raise ValueError(data.get("msg", "unknown"))
            print(f"  🖼️ [OCR服务] 耗时 {time.time()-t0:.2f}s")
        except Exception as e:
            if attempt < retries:
                wait = 2.0 * (attempt + 1)
                print(f"  ⚠️ [OCR服务] 请求失败（第 {attempt + 1} 次）: {e}，{wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ⚠️ [OCR服务] 请求失败（已重试 {retries} 次）: {e}")
            return []

        blocks = []
        for item in data.get("data", []):
            text = item.get("text", "").strip()
            score = item.get("score", 0.0)
            center = item.get("center", {})
            if not text or score < 0.4:
                if log_low_confidence and text:
                    print(f"  🔎 [OCR服务] 跳过低置信度文字：{text!r} conf={score:.3f}")
                continue
            blocks.append({
                "text": text,
                "cx": center.get("x", 0),
                "cy": center.get("y", 0),
                "conf": score,
            })
        return blocks

    return []


def get_ocr_text_blocks(device, retries=2, height_ratio=1.0):
    """
    全屏 OCR（云端 PaddleOCRv4），返回文本块列表 [{"text", "cx", "cy", "conf"}]
    参数:
        device: u2 设备对象
        retries: 截图失败时的重试次数（默认 2 次，应对 activity 过渡期）
        height_ratio: 从截图顶部识别的高度比例（默认全屏）
    返回: 文本块列表
    """
    # ===== 外层重试：截图 + 云 OCR 整个过程 =====
    for outer_attempt in range(3):
        # 截图（带重试）
        img = None
        for attempt in range(retries + 1):
            try:
                img = device.screenshot()
                break
            except Exception as e:
                if attempt < retries:
                    wait = 1.5 * (attempt + 1)
                    print(f"  ⚠️ 截图失败（第 {attempt + 1} 次）: {e}，{wait}s 后重试...")
                    time.sleep(wait)
                else:
                    print(f"  ⚠️ 截图失败（已重试 {retries} 次）: {e}")
                    return []

        if img is None:
            if outer_attempt < 2:
                time.sleep(2.0)
                continue
            return []

        if height_ratio != 1.0:
            img = img.crop((0, 0, img.width, int(img.height * height_ratio)))

        # OCR
        blocks = _cloud_ocr(img, retries=0)
        if blocks:
            return blocks

        # OCR 返回空 → 可能是过渡帧/空白页，等待后重试
        if outer_attempt < 2:
            print(f"  ⚠️ OCR 返回空（第 {outer_attempt + 1} 次），1.5s 后重试...")
            time.sleep(1.5)

    return []


# ========================================================
# 🔧 ADB screencap（绕过 u2 覆盖层关闭问题）
# ========================================================

def resolve_adb():
    """按优先级定位 adb：显式配置 > PATH > 项目自带 .vendor/platform-tools > 常见 SDK 路径。

    原实现直接写死 "adb"，只有在 adb 进了系统 PATH 时才工作；
    本机是按 DIANTAO_ADB_PATH 配的，不走 PATH，于是 get_connected_devices()
    会抛 FileNotFoundError 被吞掉，最终误报「未检测到安卓设备」。
    """
    import shutil as _shutil

    exe = "adb.exe" if os.name == "nt" else "adb"

    configured = os.environ.get("DIANTAO_ADB_PATH", "").strip()
    if configured and os.path.isfile(configured):
        return configured

    found = _shutil.which("adb")
    if found:
        return found

    root = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(root, ".vendor", "platform-tools", exe)]
    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(os.path.join(base, "platform-tools", exe))
    candidates.append(os.path.expanduser(f"~/AppData/Local/Android/Sdk/platform-tools/{exe}"))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return "adb"


ADB_PATH = resolve_adb()


def ensure_local_ocr(wait=90):
    """OCR 配置指向本机时，启动前确保服务在跑。

    本机 OCR 是独立进程，不会随任务自启；忘了开就会「识别结果全空」，
    表现为「金币任务列表：False」或「没有可执行的任务」，很难看出真正原因。
    只对 127.0.0.1/localhost 生效，远程 OCR 服务完全不动。
    """
    try:
        import json as _json
        import time as _time
        import urllib.request as _urllib
        from urllib.parse import urlparse

        url = os.environ.get("DIANTAO_OCR_SERVER_URL", "").strip()
        if not url:
            return None
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
            return None
        port = parsed.port or 80
        opener = _urllib.build_opener(_urllib.ProxyHandler({}))

        def _ready():
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    return _json.loads(response.read().decode("utf-8")).get("status") == "ready"
            except Exception:
                return False

        if _ready():
            return port

        root = os.path.dirname(os.path.abspath(__file__))
        ocr_python = os.path.join(root, ".venv-ocr", "Scripts", "python.exe")
        server = os.path.join(root, "tools", "ocr_rapid_server.py")
        if not (os.path.isfile(ocr_python) and os.path.isfile(server)):
            print("  ⚠️ 本机 OCR 服务未运行，且找不到 OCR 环境；请先双击「启动OCR服务.cmd」")
            return None

        print("  [OCR] 本机识别服务未运行，正在自动启动…")
        log_dir = os.path.join(root, ".runtime")
        os.makedirs(log_dir, exist_ok=True)
        # 必须清掉 PYTHONPATH：看板后端注入的路径是 3.11 的包，
        # 被 3.12 的 OCR 进程继承会导致 numpy/PIL 二进制不兼容。
        child_env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME"):
            child_env.pop(key, None)
        child_env["PYTHONNOUSERSITE"] = "1"
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)

        with open(os.path.join(log_dir, "ocr_service.log"), "wb") as sink:
            subprocess.Popen(
                [ocr_python, "-u", server, "--host", "127.0.0.1", "--port", str(port)],
                cwd=root, stdout=sink, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True, env=child_env,
            )
        for _ in range(wait):
            _time.sleep(1)
            if _ready():
                print("  [OCR] 本机识别服务已就绪")
                return port
        print("  ⚠️ OCR 启动超时，请手动双击「启动OCR服务.cmd」")
        return None
    except Exception as error:  # noqa: BLE001 - 预热失败不应阻断主流程
        print(f"  ⚠️ OCR 预热失败：{error}")
        return None

def _get_adb_serial(device):
    """从 u2 device 对象提取 ADB serial（优先用 Wi-Fi ADB 地址）"""
    try:
        # u2 device 的 serial 属性
        serial = getattr(device, '_serial', None) or getattr(device, 'serial', None)
        if serial:
            return serial
    except:
        pass
    # 兜底：从 device 的 adb_server 信息获取
    try:
        info = device.shell("getprop ro.serialno").output.strip()
        if info:
            return info
    except:
        pass
    return ""


def adb_screencap(device):
    """
    ADB exec-out screencap 截图（不会触发 u2 关闭覆盖层）
    参数:
        device: u2 设备对象
    返回: PIL Image 对象，失败返回 None
    """
    serial = _get_adb_serial(device)
    if not serial:
        print("  ⚠️ 无法获取设备序列号，回退 u2 截图")
        try:
            return device.screenshot()
        except:
            return None

    cmd = [ADB_PATH, "-s", serial, "exec-out", "screencap", "-p"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0 or len(result.stdout) < 100:
            print("  ⚠️ ADB 截图失败，回退 u2 截图")
            try:
                return device.screenshot()
            except:
                return None
        img = Image.open(io.BytesIO(result.stdout))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        return img
    except Exception as e:
        print(f"  ⚠️ ADB 截图异常: {e}，回退 u2 截图")
        try:
            return device.screenshot()
        except:
            return None


def get_ocr_text_blocks_adb(device, retries=2):
    """
    ADB 截图 + OCR（不会触发 u2 覆盖层关闭）
    用于需要在覆盖层上做 OCR 的场景（如淘金币任务列表）
    参数:
        device: u2 设备对象
        retries: 截图失败时的重试次数
    返回: 文本块列表 [{"text", "cx", "cy", "conf"}]
    """
    for outer_attempt in range(3):
        img = None
        for attempt in range(retries + 1):
            img = adb_screencap(device)
            if img is not None:
                break
            if attempt < retries:
                wait = 1.5 * (attempt + 1)
                print(f"  ⚠️ 截图失败（第 {attempt + 1} 次），{wait}s 后重试...")
                time.sleep(wait)

        if img is None:
            if outer_attempt < 2:
                time.sleep(2.0)
                continue
            return []

        blocks = _cloud_ocr(img, retries=0)
        if blocks:
            return blocks

        if outer_attempt < 2:
            print(f"  ⚠️ ADB OCR 返回空（第 {outer_attempt + 1} 次），1.5s 后重试...")
            time.sleep(1.5)

    return []


# ========================================================
# ⏰ 时间工具
# ========================================================

def time_until_target(target_time: str) -> float:
    """
    计算到目标时间（HH:MM）的秒数。
    如果目标时间已过，则计算到明天同一时间的秒数。
    """
    now = datetime.now()
    target = datetime.strptime(target_time, "%H:%M").replace(
        year=now.year, month=now.month, day=now.day
    )
    if target <= now:
        target = target.replace(day=target.day + 1)
    return (target - now).total_seconds()


def is_time_between(start: str, end: str) -> bool:
    """判断当前时间是否在指定时间段内（HH:MM-HH:MM）"""
    now = datetime.now().strftime("%H:%M")
    return start <= now <= end


# ========================================================
# 🖼️ 图像处理与模板匹配
# ========================================================

def find_button_center(screen_shot, template_path, threshold=0.8, method=cv2.TM_CCOEFF_NORMED):
    """单尺度模板匹配，返回按钮中心坐标"""
    template = cv2.imread(template_path, 0)
    if screen_shot is None:
        return None
    if isinstance(screen_shot, Image.Image):
        screen_shot = cv2.cvtColor(np.array(screen_shot), cv2.COLOR_RGB2GRAY)
    elif isinstance(screen_shot, bytes):
        img = Image.open(io.BytesIO(screen_shot))
        screen_shot = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    elif isinstance(screen_shot, np.ndarray):
        if len(screen_shot.shape) == 3:
            screen_shot = cv2.cvtColor(screen_shot, cv2.COLOR_BGR2GRAY)
    if template is None:
        return None
    h, w = template.shape
    result = cv2.matchTemplate(screen_shot, template, method)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
        loc = min_loc
        val = min_val
    else:
        loc = max_loc
        val = max_val
    threshold_val = threshold
    if method == cv2.TM_CCOEFF_NORMED:
        threshold_val = threshold
    elif method == cv2.TM_SQDIFF_NORMED:
        threshold_val = 1 - threshold
    if val >= threshold_val:
        center_x = loc[0] + w // 2
        center_y = loc[1] + h // 2
        return (center_x, center_y)
    return None


def find_button_multiscale(screen_shot, template_path, scales=np.linspace(0.6, 1.4, 20),
                           threshold=0.78, method=cv2.TM_CCOEFF_NORMED):
    """多尺度模板匹配"""
    template = cv2.imread(template_path)
    if screen_shot is None or template is None:
        return None, None, None
    if isinstance(screen_shot, Image.Image):
        screen_shot = cv2.cvtColor(np.array(screen_shot), cv2.COLOR_RGB2BGR)
    elif isinstance(screen_shot, bytes):
        img = Image.open(io.BytesIO(screen_shot))
        screen_shot = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    large_gray = cv2.cvtColor(screen_shot, cv2.COLOR_BGR2GRAY)
    tpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    h, w = tpl_gray.shape[:2]
    best_val = -1
    best_loc = None
    best_scale = 1.0
    best_rect = None
    for scale in scales:
        resize_w = int(w * scale)
        resize_h = int(h * scale)
        if resize_w < 5 or resize_h < 5:
            continue
        resized_tpl = cv2.resize(tpl_gray, (resize_w, resize_h),
                                 interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        if resized_tpl.shape[0] > large_gray.shape[0] or resized_tpl.shape[1] > large_gray.shape[1]:
            continue
        result = cv2.matchTemplate(large_gray, resized_tpl, method)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
            val = min_val
            loc = min_loc
        else:
            val = max_val
            loc = max_loc
        if val > best_val:
            best_val = val
            best_loc = loc
            best_scale = scale
            best_rect = (loc[0], loc[1], resize_w, resize_h)
    if best_val >= threshold:
        x, y, bw, bh = best_rect
        center_x = x + bw // 2
        center_y = y + bh // 2
        cv2.rectangle(screen_shot, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
        cv2.circle(screen_shot, (center_x, center_y), 5, (0, 0, 255), -1)
        cv2.imwrite("result.jpg", screen_shot)
        return (center_x, center_y), best_val, best_scale
    else:
        return None, best_val, None


# ========================================================
# 🔗 设备连接
# ========================================================
def get_connected_devices():
    """通过ADB获取所有连接的安卓设备序列号"""
    try:
        result = subprocess.run(
            [ADB_PATH, "devices"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        output = result.stdout
        devices = []
        for line in output.splitlines():
            if line.strip() == "" or line.startswith("List of devices attached"):
                continue
            match = re.match(r"^([^\s]+)\s+device$", line)
            if match:
                devices.append(match.group(1))
        return devices
    except:
        return []


# ── 启动阶段自动找回手机（工作台直接 Popen 任务脚本时不经过 启动淘金币.cmd） ──
RECONNECT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "tools", "reconnect_phone.py")
_RECOVER_ATTEMPTED = False


def _console_python():
    """pythonw 没有 stdout，子进程输出拿不到；换成同目录的 python.exe。"""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return exe


def recover_phone_connection():
    """检测不到设备时自动重连无线调试，并把电脑移动热点关一次再开。

    工作台是直接 Popen 任务脚本的，不会执行「启动淘金币.cmd」里的重连步骤；
    放在 select_device() 里才能保证工作台 / 命令行 / 任何调用方都覆盖到。
    子进程输出实时转发到任务日志，5 分钟硬超时防止 PowerShell 卡死。
    可用环境变量 DIANTAO_NO_AUTO_RECONNECT=1 关闭。
    """
    global _RECOVER_ATTEMPTED
    if _RECOVER_ATTEMPTED:
        return False
    _RECOVER_ATTEMPTED = True
    if os.environ.get("DIANTAO_NO_AUTO_RECONNECT", "").strip().lower() in ("1", "true", "yes"):
        print("  （自动重连已由 DIANTAO_NO_AUTO_RECONNECT 禁用）", flush=True)
        return False
    if not os.path.isfile(RECONNECT_SCRIPT):
        print("  ⚠ 找不到 tools/reconnect_phone.py，无法自动重连手机", flush=True)
        return False

    print("  ⚠ 未检测到已连接的安卓设备 → 自动重连手机，并把电脑热点关一次再开…", flush=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)  # 跨 venv 子进程不能继承宿主的包搜索路径
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [_console_python(), "-u", RECONNECT_SCRIPT,
               "--adb", ADB_PATH, "--stabilize", "--force-hotspot"]
    try:
        proc = subprocess.Popen(
            command, cwd=os.path.dirname(os.path.dirname(RECONNECT_SCRIPT)),
            env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags,
        )
    except (OSError, ValueError) as exc:
        print(f"  ⚠ 自动重连启动失败：{exc}", flush=True)
        return False

    def _kill_when_stuck():
        try:
            if proc.poll() is None:
                proc.kill()
                print("  ⚠ 自动重连超过 5 分钟，已强制结束", flush=True)
        except OSError:
            pass

    watchdog = threading.Timer(300, _kill_when_stuck)
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line.strip():
                print("  │ " + line, flush=True)
        proc.wait()
    finally:
        watchdog.cancel()
        try:
            proc.stdout.close()
        except Exception:
            pass
    print(f"  [自动重连] 退出码 {proc.returncode}；重新检查设备…", flush=True)
    return bool(get_connected_devices())


def select_device():
    """从已连接的设备中，返回用户选中的设备序列号（没设备时先自动重连并关开热点）"""
    devices = get_connected_devices()
    if not devices and recover_phone_connection():
        devices = get_connected_devices()
    if not devices:
        raise Exception(
            "未检测到任何连接的安卓设备（已自动重连并关开一次电脑热点仍未连上；"
            "请解锁手机、确认手机连的是电脑热点 WiFi，并把「开发者选项 → 无线调试」关一次再开）")
    # 优先选择普通 ADB TCP 连接（排除 TLS 无线调试的 mDNS 地址）
    preferred = [d for d in devices if "_adb-tls" not in d and ".tcp" not in d.split("_")[-1:]]
    usable = preferred if preferred else devices

    if len(usable) == 1:
        print(f"自动选择设备: {usable[0]}")
        return usable[0]
    else:
        print("当前连接多个设备，请输入要执行的设备序号：")
        for i, device in enumerate(usable, 1):
            print(f"  {i}: {device}")
        # 无终端环境（pythonw）下 input() 会抛 EOFError，自动选第一个可用设备
        try:
            choice = input("请输入设备序号：")
            index = int(choice) - 1
            if 0 <= index < len(usable):
                return usable[index]
            print("输入错误，自动选择第一个可用设备")
            return usable[0]
        except (EOFError, OSError):
            print(f"无终端输入，自动选择第一个可用设备: {usable[0]}")
            return usable[0]
        except ValueError:
            print(f"输入错误，自动选择第一个可用设备: {usable[0]}")
            return usable[0]


def setup_global_watcher(d):
    """
    启动 U2 全局弹窗监控（watch_context 后台线程）
    所有常见弹窗自动关闭，避免干扰 OCR 主流程
    """
    ctx = d.watch_context()
    ctx.when("确认").click()
    ctx.when("确定").click()
    ctx.when("关闭").click()
    ctx.when("刷新").click()
    ctx.when("点击刷新").click()
    ctx.when("领取今日奖励").click()
    ctx.when(xpath="//android.app.Dialog//android.widget.Button[@text='关闭']").click()
    ctx.when(xpath="//android.widget.Image[@text='关闭']").click()
    ctx.when(xpath="//android.widget.FrameLayout[@resource-id='com.taobao.taobao:id/poplayer_native_state_center_layout_frame_id']//android.widget.ImageView[@content-desc='关闭按钮']").click()
    ctx.start()
    print("🛡️ U2 全局弹窗监控已启动（watch_context 后台线程）")
    return ctx


def pause_global_watcher(d):
    """暂停设备上的全局弹窗 watcher，避免任务专用流程被后台点击干扰。"""
    ctx = getattr(d, "_global_watcher_ctx", None)
    if ctx is not None:
        depth = getattr(d, "_global_watcher_pause_depth", 0)
        if depth == 0:
            ctx.stop()
        d._global_watcher_pause_depth = depth + 1


def resume_global_watcher(d):
    """恢复设备上的全局弹窗 watcher。"""
    ctx = getattr(d, "_global_watcher_ctx", None)
    if ctx is not None:
        depth = getattr(d, "_global_watcher_pause_depth", 0)
        if depth > 0:
            d._global_watcher_pause_depth = depth - 1
            if depth == 1:
                ctx.start()


def connect_device():
    """连接设备（简化版：自动选设备），自动启动U2全局弹窗监控"""
    from device_connection import connect_with_retry
    selected = select_device()
    d = connect_with_retry(selected)
    print(f"已成功连接设备：{selected}")
    d._global_watcher_ctx = setup_global_watcher(d)
    return d


def start_app(d, package_name, init=False):
    """启动应用，支持特定activity配置"""
    stop = init
    use_monkey = init
    activity = APP_START_CONFIG.get(package_name)
    try_count = 3
    try:
        while try_count > 0:
            print(f"启动应用: {package_name}, stop: {stop}")
            d.app_start(package_name, stop=stop, use_monkey=use_monkey)
            time.sleep(5 if stop else 2)
            cancel_btn = d(className="android.widget.FrameLayout",
                           resourceId="com.taobao.taobao:id/uik_fl_textview_container_2")
            if cancel_btn.exists:
                click_u2_control(d, cancel_btn, "取消弹窗")
                time.sleep(2)
            current_package, _ = get_current_app(d)
            if current_package == package_name:
                print(f"应用 {package_name} 启动成功")
                return
            d.press("back")
            time.sleep(1)
            try_count -= 1
    except:
        pass
    if activity:
        try:
            print(f"使用activity启动: {package_name}, {activity}")
            d.app_start(package_name=package_name, activity=activity, stop=stop)
            time.sleep(2)
        except:
            pass


# ========================================================
# 📱 应用状态检测
# ========================================================
def get_current_app(d):
    """获取当前前台应用包名和Activity"""
    try:
        info = d.shell("dumpsys window | grep mCurrentFocus").output
        match = re.search(r'mCurrentFocus=Window\{.*? u0 (.*?)/(.*?)\}', info)
        if match:
            return match.group(1), match.group(2)
    except:
        pass
    return None, None


def ensure_app(d, package_name):
    """确保在目标App内，不在则重新启动"""
    current_pkg, _ = get_current_app(d)
    if current_pkg != package_name:
        print(f"  ⚠️ 掉出了 {package_name}，重新启动...")
        d.app_start(package_name, stop=False)
        time.sleep(3)
        return False
    return True


# ========================================================
# ✅ 验证码检测
# ========================================================
def check_verify(d):
    """检测并处理验证码滑块"""
    verify_view = d(className="android.webkit.WebView", text="验证码拦截")
    if verify_view.exists:
        while True:
            print("存在验证码的情况")
            d.shell("input swipe 150 1700 1180 1700 500")
            time.sleep(3)
            verify_view = d(className="android.webkit.WebView", text="验证码拦截")
            if verify_view.exists:
                d.click(500, 1700)
                time.sleep(3)
            else:
                break
        time.sleep(3)
    else:
        pass


# ========================================================
# 🤖 界面操作
# ========================================================
def click_device_point(device, cx, cy, label="控件"):
    """以设备坐标点击，并记录点击指令是否已发出。"""
    cx, cy = int(cx), int(cy)
    print(f"    ✅ {label}：执行设备点击 @({cx},{cy})")
    device.click(cx, cy)
    print(f"    ✅ {label}：点击指令已发出")
    return True


def click_u2_control(device, control, label="控件"):
    """将 U2 控件中心转换为设备级点击，避免 H5 selector.click() 丢失点击。"""
    cx, cy = control.center()
    cx, cy = int(cx), int(cy)
    print(f"    ✅ {label}：执行设备点击 @({cx},{cy})")
    device.click(cx, cy)
    print(f"    ✅ {label}：点击指令已发出")
    return True


def click_text(d, text, timeout=10):
    """等待并点击指定文本"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        obj = d(text=text)
        if obj.exists:
            return click_u2_control(d, obj, text)
        time.sleep(0.5)
    return False


def click_text_contains(d, text, timeout=10):
    """等待并点击包含指定文本的元素"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        obj = d(textContains=text)
        if obj.exists:
            return click_u2_control(d, obj, text)
        time.sleep(0.5)
    return False


def scroll_down(d, fraction=0.3):
    """下滑操作"""
    width, height = d.window_size()
    d.swipe(width // 2, int(height * 0.7), width // 2, int(height * 0.3), duration=0.2)
    time.sleep(1.5)


def scroll_up(d, fraction=0.3):
    """上滑操作"""
    width, height = d.window_size()
    d.swipe(width // 2, int(height * 0.3), width // 2, int(height * 0.7), duration=0.2)
    time.sleep(1.5)


# ========================================================
# 📖 通用浏览任务（替代所有独立下滑循环）
# ========================================================
def browse_task(device, min_duration=0, max_wait=120, extra_seconds=5):
    """
    通用浏览任务：实时扫描"滑动浏览XX秒"字样，消失即任务完成
    ...

    参数:
        device: u2 设备对象
        min_duration: 未检测到滑动字样时的最小浏览时间（秒）
        max_wait: 超时上限（秒）
        extra_seconds: 浏览追加的保险时间（秒），30秒以内任务至少追加10秒
    返回:
        True 正常完成, False 超时
    """
    sw, sh = device.window_size()
    swipe_count = 0
    start = time.time()

    # ── 自动适配短任务（30秒以内）的追加时间 ──
    effective_extra = extra_seconds
    task_ref_duration = min_duration if min_duration > 0 else 999
    if task_ref_duration <= 30:
        effective_extra = max(extra_seconds, 10)
        print(f"  ⏱️ 检测到30秒短任务，追加保险时间 {effective_extra}s")

    # 立即下滑一次，不等 OCR（缩短从点击到首次滑动的等待时间）
    swipe_x = int(sw * 0.5)
    s_y = random.randint(int(sh * 0.65), int(sh * 0.75))
    e_y = random.randint(int(sh * 0.15), int(sh * 0.25))
    try:
        device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
    except:
        pass
    time.sleep(1.5)

    # 再用 OCR 检测"滑动浏览XX秒"字样
    # 兼容: "滑动浏览4秒"、"滑动浏览4"（"秒"可能被OCR截到另一行）
    blocks = get_ocr_text_blocks(device)
    has_sliding_text = False
    sliding_min_seconds = 0
    for b in blocks:
        text = b.get("text", "")
        m = re.search(r"滑动浏览\s*(\d+)", text)
        if m:
            detected_sec = int(m.group(1))
            # 滑动文本检测到 <= 30s 也自动追加 10s
            if detected_sec <= 30:
                effective_extra = max(extra_seconds, 10)
            has_sliding_text = True
            sliding_min_seconds = max(min_duration, detected_sec) + effective_extra
            print(f"  📋 检测到【{text}】，提取到 {sliding_min_seconds} 秒，使用滑动文本监控模式")
            break

    if has_sliding_text:
        # 模式A：实时监控"滑动浏览XX秒"直到消失，但至少满足提取的秒数
        # 首次扫描的等待不能抵扣浏览时间；超时仍从函数入口累计。
        browse_start = time.time()
        print(f"  📋 实时监控【滑动浏览XX秒】字样...")
        while time.time() - start < max_wait:
            elapsed = int(time.time() - browse_start)

            blocks = get_ocr_text_blocks(device)
            found_sliding = False
            remaining = -1
            for b in blocks:
                text = b.get("text", "")
                m = re.search(r"滑动浏览\s*(\d+)", text)
                if m:
                    found_sliding = True
                    remaining = int(m.group(1))
                    if swipe_count == 0 or swipe_count % 3 == 0 or remaining <= 5:
                        print(f"    检测到【{text}】剩余 {remaining}s... (已过{elapsed}s)")
                    break

            elapsed = int(time.time() - browse_start)
            if found_sliding and 0 <= remaining <= 3 and elapsed >= sliding_min_seconds:
                # 剩余时间很少了 → 多下滑4次收尾，不等字样消失
                print(f"  ✅ 滑动剩余 {remaining}s，即将完成，下滑收尾...")
                for _ in range(4):
                    swipe_count += 1
                    s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                    e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                    device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
                    time.sleep(0.5)
                time.sleep(1)
                return True

            if not found_sliding and elapsed >= sliding_min_seconds:
                print(f"  ✅ 【滑动浏览XX秒】字样消失，已达 {sliding_min_seconds}s 要求 (共下滑{swipe_count}次)")
                # 多下滑4次确保任务提交
                for _ in range(4):
                    swipe_count += 1
                    s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                    e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                    device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
                    time.sleep(0.5)
                time.sleep(1)
                return True
            elif not found_sliding and elapsed < sliding_min_seconds:
                # 字样提前滑出屏幕了，但时间不够，继续下滑等待时间到
                print(f"  ⚠️ 字样已消失但仅过 {elapsed}s < {sliding_min_seconds}s，继续下滑等候...")
                
            # 每次OCR检查之间滑动多次（增加滑动次数，提高完成效率）
            swipe_x = int(sw * 0.5)
            for _ in range(5):
                swipe_count += 1
                if swipe_count % 2 == 1:
                    s_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                    e_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                else:
                    s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                    e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                try:
                    device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
                except:
                    pass
                time.sleep(0.5)

        print(f"  ⏰ 浏览超时 ({max_wait}s)，共滑动{swipe_count}次")
        time.sleep(1)
        return False

    # 模式B：未检测到滑动字样，按固定时长下滑浏览 + 实时完成检测
    if min_duration > 0:
        # 页面加载和首次扫描耗时不抵扣实际浏览；保险时间也必须计入。
        fixed_duration = min_duration + effective_extra
        browse_start = time.time()
        print(f"  📋 未检测到滑动字样，按固定时长 {fixed_duration} 秒浏览（任务 {min_duration}s + 保险 {effective_extra}s）...")
        while time.time() - browse_start < fixed_duration and time.time() - start < max_wait:
            elapsed = int(time.time() - start)
            # 实时检测完成提示
            try:
                if time.time() - browse_start >= fixed_duration and (
                    device(textContains="恭喜完成任务").exists
                    or device(textContains="恭喜完成").exists
                    or device(textContains="任务完成").exists
                    or device(textContains="获得奖励").exists
                ):
                    print(f"  ✅ 检测到完成提示！(已过{elapsed}s)")
                    # 多下滑4次确保任务提交
                    for _ in range(4):
                        swipe_count += 1
                        s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                        e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                        device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
                        time.sleep(0.5)
                    time.sleep(1)
                    return True
            except:
                pass
            # 交替上下滑
            swipe_count += 1
            swipe_x = int(sw * 0.5)
            if swipe_count % 2 == 1:
                s_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                e_y = random.randint(int(sh * 0.15), int(sh * 0.25))
            else:
                s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
            try:
                device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
            except:
                pass
            time.sleep(0.8)
        if time.time() - browse_start < fixed_duration:
            print(f"  ⏰ 浏览超时 ({max_wait}s)，未达到 {fixed_duration}s 要求")
            return False
        print(f"  浏览完成，滑动 {swipe_count} 次")
    else:
        print(f"  📋 未检测到滑动字样，min_duration=0，快速返回")

    time.sleep(1)
    return True


# ========================================================
# 📋 简单动态区域 OCR（新区域检测用）
# ========================================================
def simple_ocr_region(d, x1, y1, x2, y2, keyword, retries=3):
    """
    在指定区域内OCR搜索关键词，返回第一个匹配的文本块
    参数:
        d: u2设备对象
        x1,y1: 区域左上角
        x2,y2: 区域右下角
        keyword: 要搜索的关键词
        retries: 重试次数
    返回: 匹配的文本块 dict 或 None
    """
    for _ in range(retries):
        blocks = get_ocr_text_blocks(d)
        if not blocks:
            time.sleep(1.5)
            continue
        for b in blocks:
            if (x1 <= b["cx"] <= x2 and y1 <= b["cy"] <= y2
                    and keyword in b["text"]):
                return b
        time.sleep(1.5)
    return None


# ========================================================
# 🔍 CodeBuddy 多模态定位与文本识别兼容函数
# ========================================================
def find_text_by_paddleocr(screenshot, target_text):
    """
    在截图中搜索目标文字/按钮，返回其中心点坐标 (x, y)
    由 CodeBuddy 多模态直接驱动，无需加载笨重本地 PaddleOCR
    """
    if HAS_CODEBUDDY_VISION:
        return find_text_by_codebuddy(screenshot, target_text)

    # 降级备用
    if isinstance(screenshot, np.ndarray):
        pil_img = Image.fromarray(screenshot)
    elif isinstance(screenshot, Image.Image):
        pil_img = screenshot
    else:
        return None

    blocks = _cloud_ocr(pil_img, retries=2)
    for block in blocks:
        if target_text in block["text"]:
            return (block["cx"], block["cy"])
    return None


def paddle_ocr(image):
    """
    识别截图中所有文字并拼接返回
    由 CodeBuddy 多模态视觉驱动
    """
    if HAS_CODEBUDDY_VISION:
        return paddle_ocr_cb(image)

    if isinstance(image, np.ndarray):
        pil_img = Image.fromarray(image)
    elif isinstance(image, Image.Image):
        pil_img = image
    else:
        return ""

    blocks = _cloud_ocr(pil_img, retries=2)
    return ''.join(block["text"] for block in blocks)


def easy_ocr(image):
    """向后兼容 easy_ocr 命名"""
    return paddle_ocr(image)


def find_ui_element(device, target_description: str):
    """
    通过语义描述智能寻找任意 UI 元素（文字、按钮、图标、返回键、关闭X）
    返回: (cx, cy) 或 None
    """
    img = device.screenshot()
    if HAS_CODEBUDDY_VISION:
        return vision_engine.find_target(img, target_description)
    return find_text_by_paddleocr(img, target_description)


def classify_current_page(device, candidates: list):
    """
    使用多模态语义判断当前屏幕状态
    """
    img = device.screenshot()
    if HAS_CODEBUDDY_VISION:
        return vision_engine.classify_screen(img, candidates)
    return "未知"


# ========================================================
# 📊 日报统计
# ========================================================
def show_today_summary():
    """打印当日任务执行统计"""
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n📊 任务日报：{today}")
    print("  淘金币任务完成")


# ========================================================
# ⏱️ 通用重试等待
# ========================================================
def wait_for_text(d, text, timeout=15):
    """等待指定文本出现，返回是否找到"""
    end = time.time() + timeout
    while time.time() < end:
        blocks = get_ocr_text_blocks(d)
        if blocks:
            for b in blocks:
                if text in b.get("text", ""):
                    return True
        time.sleep(1)
    return False


# ========================================================
# 🎯 小任务滑动模块（用于特定任务列表）
# ========================================================
def task_swipe_and_click(d, block_list_texts, max_swipes=15,
                         swipe_start_y=0.7, swipe_end_y=0.3):
    """
    下滑并点击目标文本，用于特定任务列表
    """
    # 默认使用 get_ocr_text_blocks 做扫描
    pass


# ========================================================
# 📝 任务完成记录
# ========================================================
TASK_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_log.json")


def check_can_open(d):
    """检查并处理"打开"/"允许"等系统授权弹窗"""
    open_btn = d(className="android.widget.Button", textMatches=r"打开|允许|始终允许|\d+天内允许")
    if open_btn.exists:
        click_u2_control(d, open_btn, "打开/允许")
        time.sleep(4)


def log_task_completed(script_name, task_name):
    """记录一个任务完成到 task_log.json"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    # 读现有数据
    data = {"days": {}}
    if os.path.exists(TASK_LOG_PATH):
        try:
            with open(TASK_LOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {"days": {}}

    # 确保今天的日期存在
    if today not in data["days"]:
        data["days"][today] = {"tasks": [], "total": 0}

    # 添加任务记录
    data["days"][today]["tasks"].append({
        "time": now_time,
        "script": script_name,
        "task": task_name,
    })
    data["days"][today]["total"] = len(data["days"][today]["tasks"])

    # 写回文件
    try:
        with open(TASK_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


# ========================================================
# 📋 集中常量定义（来自 开发规范.md）
# ========================================================

# 黑名单关键词：邀请/下单/充值等危险任务
BLACKLIST_KEYWORDS = [
    "邀请", "邀请好友", "拉好友", "助力", "分享", "去邀请",
    "下单", "购买", "充值", "付款", "元宝商城", "元宝商城下单",
    "元宝兑换", "元宝好物", "元宝商品", "元宝精选", "元宝购",
    "喂鸡", "蚂蚁庄园", "蚂蚁新村", "饿了么", "果园",
    "消消乐", "农场小游戏", "斗地主", "开心消消乐",
    "大众点评", "拍立淘", "头条极速版", "趣头条",
    "全场3元3件", "好货限时直降", "查看并完成", "玩小游戏",
    # 商品三次点击由 is_blacklisted 的共享规则统一拦截，包含特惠/优惠变体。
    "精选宝贝",
]
# 注意：含"猜"但不含"猜价格/猜价格游戏"的 → 黑名单

# 直播任务关键词
LIVE_KW = ["观看", "直播", "直播间", "看直播", "看福利直播"]

# 弹窗关闭文字（带冷却机制）
POPUP_CLOSE_TEXTS = [
    "关闭", "X", "×", "关闭按钮", "取消",
    "我知道了", "朕知道了", "关闭广告",
    "跳过", "跳过广告",
    "不感兴趣",
]

# 任务按钮关键词
TASK_BTN_KW = ["去完成", "去浏览", "去观看", "去领取"]

# 任务完成提示
COMPLETION_TEXTS = [
    "恭喜完成任务", "恭喜完成", "任务完成",
    "获得奖励", "恭喜获得", "已完成",
    "任务进度已完成", "进度已完成",
    "已看", "任务已",
    "谢谢观看", "感谢观看", "直播已结束",
    "本场直播已结束", "主播已离开",
]


# ========================================================
# 🧰 统一辅助函数
# ========================================================

def any_match(text, keywords):
    """判断文本是否匹配任意关键词"""
    return any(kw in (text or "") for kw in keywords)


def normalize_task_name(raw_name: str) -> str:
    """去空格、去括号内容、统一关键词映射"""
    name = raw_name.strip()
    # 去括号内容
    name = re.sub(r'[\(（][^)）]*[\)）]', '', name).strip()
    # 统一关键词映射
    mappings = {
        "看直播": "看直播",
        "观看直播": "看直播",
        "浏览精选": "浏览精选推荐",
        "精选推荐": "浏览精选推荐",
        "猜价格游戏": "猜价格",
        "去猜价格": "猜价格",
        "查看3个": "查看3个精选宝贝",
        "看3个": "查看3个精选宝贝",
    }
    for key, val in mappings.items():
        if key in name:
            return val
    return name


def is_blacklisted(name: str) -> bool:
    """判断任务名是否在黑名单中"""
    from task_exclusions import is_blocked_boutique_task
    if is_blocked_boutique_task(name):
        return True
    if not name:
        return True
    name = name.strip()
    # OCR 可能在汉字之间插入普通空白或零宽字符；同时检查紧凑文本，
    # 避免“元宝 商城”“元宝\u200b商城”等危险任务漏过关键词匹配。
    compact_name = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", name)
    # 精确匹配
    if name in BLACKLIST_KEYWORDS or compact_name in BLACKLIST_KEYWORDS:
        return True
    # 关键词匹配
    for kw in BLACKLIST_KEYWORDS:
        if kw in name or kw in compact_name:
            return True
    # 含"猜"但不含"猜价格"的 → 黑名单
    if "猜" in compact_name and "猜价格" not in compact_name:
        return True
    return False


def back_to_task(device, target_keywords=None, nav_func=None, max_attempts=8):
    """
    智能返回：先检测是否已在任务页，再尝试返回

    参数:
        device: u2 设备对象
        target_keywords: 任务页面的标识文本（默认 ["去完成", "去浏览"]）
        nav_func: 导航回调，如果不在任务页也不是目标页时调用（如点击"查看更多"）
        max_attempts: 最大返回尝试次数

    返回:
        True 已回到任务页面, False 超时
    """
    if target_keywords is None:
        target_keywords = TASK_BTN_KW

    for attempt in range(max_attempts):
        # 检测是否已在任务页
        for kw in target_keywords:
            try:
                if device(textContains=kw).exists(timeout=0.5):
                    return True
            except:
                pass

        # OCR 兜底检测
        try:
            blocks = get_ocr_text_blocks(device)
            for b in blocks:
                if any_match(b.get("text", ""), target_keywords):
                    return True
        except:
            pass

        # 尝试导航回调
        if nav_func and attempt == 0:
            try:
                nav_func(device)
                time.sleep(1.5)
                continue
            except:
                pass

        # 尝试关闭弹窗/返回
        try:
            bs = get_ocr_text_blocks(device)
            hit = False
            for b in bs:
                if any_match(b["text"], ["关闭", "返回", "再逛逛", "完成", "知道了"]):
                    device.click(b["cx"], b["cy"])
                    hit = True
                    break
            if not hit:
                device.press("back")
        except:
            device.press("back")
        time.sleep(1.5)

    print("  ⚠️ 返回超时")
    return False


# ========================================================
# 📺 通用任务处理器（薄层委托 → u2_scanner）
# ========================================================

def handle_guess_price_game(device):
    """猜价格游戏（委托 u2_scanner 实现）"""
    from u2_scanner import handle_guess_price_game as _real
    return _real(device)


def execute_live_task(device, task_name, btn_cx, btn_cy, duration=None):
    """统一直播任务执行器（委托 u2_scanner 实现）"""
    from u2_scanner import execute_live_task as _real
    return _real(device, task_name, btn_cx, btn_cy, duration)


def click_three_selected_items(device):
    """点击3个精选宝贝（委托 u2_scanner 实现）"""
    from u2_scanner import click_three_selected_items as _real
    return _real(device)


def show_dev_guide(task_type: str = None):
    """
    启动时显示开发规范摘要（从 RAG 查询）

    用法:
        # 在脚本开头调用
        show_dev_guide()
        show_dev_guide("点击3个商品")  # 查询特定任务的规范
    """
    if task_type:
        print(f"  📖 查询规范: {task_type}")
        answer = ask_dev_guide(task_type)
        # 只显示前几行
        lines = answer.strip().split("\n")
        preview = "\n".join(lines[:8])
        if len(lines) > 8:
            preview += "\n    ..."
        print(f"  {preview}")
    else:
        print(f"  📖 LightRAG 知识库已就绪（开发规范查询）")


# ========================================================
# 📚 LightRAG 知识库查询（开发规范 + 历史经验）
# ========================================================

def query_dev_guide(question: str, mode: str = "mix") -> str:
    """
    查询 LightRAG 知识库（开发规范、历史经验）
    参数:
        question: 你的问题（如"如何实现点击3个商品任务？"）
        mode: 查询模式（默认 "mix" 多策略融合）
    返回:
        答案字符串
    用法:
        answer = query_dev_guide("开发规范对日志格式有什么要求？")
        print(answer)
    """
    try:
        from lightrag_helper import query_rag
        return query_rag(question, mode=mode)
    except ImportError:
        return "[RAG 错误] 未找到 lightrag_helper.py，请确认该文件在项目根目录"
    except Exception as e:
        return f"[RAG 查询失败] {e}"


def ask_dev_guide(task_type: str) -> str:
    """
    快捷查询：根据任务类型自动构造问题
    参数:
        task_type: 任务类型（如"点击3个商品"、"浏览店铺"）
    返回:
        开发规范中的标准做法
    用法:
        pattern = ask_dev_guide("点击3个商品")
        print(pattern)  # 打印规范中的标准坐标和步骤
    """
    question = f"开发规范中对【{task_type}】任务有什么要求？具体如何实现？"
    return query_dev_guide(question)


def check_rag_service() -> bool:
    """
    检查 LightRAG 服务是否运行
    返回:
        True  if 服务正常运行，False 否则
    """
    try:
        from lightrag_helper import get_rag_status
        status = get_rag_status()
        if "error" in status:
            print(f"  ⚠️ LightRAG 服务异常: {status['error']}")
            return False
        counts = status.get("status_counts", {})
        print(f"  ✅ LightRAG 服务正常（已索引 {counts.get('processed', 0)} 个文档）")
        return True
    except Exception as e:
        print(f"  ⚠️ LightRAG 服务未启动: {e}")
        print(f"  💡 启动命令: cd /d D:\\WORKBUDDY && python -m lightrag-hku --env-file C:\\... ")
        return False


def find_ocr_text_block(device, keywords, *, settle_seconds=0, attempts=1,
                        exact=False, log=False):
    """OCR 查找文字块；不读取 UIAutomator 控件树。"""
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    if not keywords:
        return None

    normalized_keywords = [re.sub(r"\s+", "", keyword).casefold() for keyword in keywords]
    for attempt in range(max(1, int(attempts))):
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        blocks = get_ocr_text_blocks(device, retries=0, log_low_confidence=log)
        if log:
            print(f"  🔎 OCR 第{attempt + 1}/{max(1, int(attempts))}次识别：{len(blocks)}个文本块")
            for index, block in enumerate(blocks[:40], 1):
                print(
                    f"    OCR[{index:02d}] {block.get('text', '')!r} "
                    f"conf={block.get('conf', 0):.3f} "
                    f"center=({block.get('cx', 0)},{block.get('cy', 0)})"
                )
        for keyword in normalized_keywords:
            for block in blocks:
                text = re.sub(r"\s+", "", str(block.get("text", ""))).casefold()
                matched = text == keyword if exact else keyword in text
                if matched:
                    return block
    return None


def has_ocr_text(device, keywords, *, settle_seconds=0, attempts=1, exact=False, log=False):
    """OCR 判断当前画面是否包含任一目标文字。"""
    return find_ocr_text_block(
        device, keywords, settle_seconds=settle_seconds,
        attempts=attempts, exact=exact, log=log,
    ) is not None


def click_ocr_text(device, keywords, *, settle_seconds=2.5, attempts=2,
                   exact=False, log=True):
    """通过 OCR 文本块坐标点击；找不到时返回 False，不使用 U2 或盲坐标兜底。"""
    block = find_ocr_text_block(
        device, keywords, settle_seconds=settle_seconds,
        attempts=attempts, exact=exact, log=log,
    )
    if not block:
        print(f"  ⚠️ OCR 未找到目标文字：{keywords}")
        return False

    try:
        cx, cy = int(block.get("cx", 0)), int(block.get("cy", 0))
        width, height = device.window_size()
        if not (0 < cx < width and 0 < cy < height):
            print(f"  ⚠️ OCR 坐标越界，拒绝点击：({cx},{cy}) / ({width},{height})")
            return False
        device.click(cx, cy)
        print(f"  ✅ OCR 点击【{block.get('text', '')}】@({cx},{cy})")
        return True
    except Exception as exc:
        print(f"  ⚠️ OCR 点击失败：{exc}")
        return False
