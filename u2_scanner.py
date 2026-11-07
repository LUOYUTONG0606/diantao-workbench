"""
========================================================
OCR 通用扫描框架
页面文字与入口通过 OCR 识别；U2 保留给设备连接和原生权限弹窗。
========================================================
"""
import time
from task_exclusions import is_blocked_boutique_task
from yuanbao_popup import handle_yuanbao_live_popups
import re
import random
import math
import os
import cv2
import numpy as np
import uiautomator2 as u2
from utils import click_u2_control
from utils import (
    ADB_PATH, click_ocr_text, find_ocr_text_block, get_ocr_text_blocks, has_ocr_text,
)
from utils import pause_global_watcher, resume_global_watcher


# ========================================================
# 包名常量
# ========================================================
DT_APP = "com.taobao.live"


# ========================================================
# 设备连接
# ========================================================
def get_connected_devices():
    import subprocess
    try:
        result = subprocess.run(
            [ADB_PATH, "devices"], capture_output=True, text=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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


def select_device():
    devices = get_connected_devices()
    if not devices:
        raise Exception("未检测到任何连接的安卓设备")

    # 优先选择普通 ADB TCP 连接（排除 TLS 无线调试的 mDNS 地址）
    preferred = [d for d in devices if "_adb-tls" not in d and ".tcp" not in d.split("_")[-1:]]
    if preferred:
        # 有可控选项时，以此为准
        usable = preferred
    else:
        usable = devices

    if len(usable) == 1:
        print(f"自动选择设备: {usable[0]}")
        return usable[0]

    print("当前连接多个设备，请输入要执行的设备序号：")
    for i, dev in enumerate(usable, 1):
        print(f"  {i}: {dev}")
    # 无终端环境（pythonw）下 input() 会抛 EOFError，自动选第一个可用设备
    try:
        choice = input("请输入设备序号：")
        index = int(choice) - 1
        if 0 <= index < len(usable):
            return usable[index]
        print(f"输入错误，自动选择第一个可用设备")
        return usable[0]
    except (EOFError, OSError):
        print(f"无终端输入，自动选择第一个可用设备: {usable[0]}")
        return usable[0]
    except ValueError:
        print(f"输入错误，自动选择第一个可用设备: {usable[0]}")
        return usable[0]


def connect_device():
    """连接设备并启动原生弹窗监控。"""
    from device_connection import connect_with_retry
    selected = select_device()
    d = connect_with_retry(selected)
    print(f"[OK] 已连接设备：{selected}")

    # 强制关闭 u2 触摸调试覆盖层（若有），否则会挡住所有 UI 内容
    # 特征：屏幕顶部显示 P:0/1, dx:0.0, dY:0.0, Xv:0.0, Yv:0.0, Prs, Size 等调试数据
    try:
        d.settings["uiautomator"] = {}
        print(f"  [设置] 已关闭 uiautomator 调试覆盖")
    except Exception:
        pass
    try:
        d.settings.pop("debug", None)
    except Exception:
        pass

    # 禁用设备级指针位置（开发者选项中的"指针位置"覆盖层）
    try:
        d.shell("settings put system pointer_location 0 2>/dev/null")
    except Exception:
        pass

    # 禁用显示触摸操作（开发者选项中的"显示触摸操作"覆盖层）
    try:
        d.shell("settings put system show_touches 0 2>/dev/null")
    except Exception:
        pass

    # 设置合理的操作延迟（新版 uiautomator2 已移除 operation_delay_ms，统一用 operation_delay）
    d.settings["operation_delay"] = 0.5

    # 只监控 Android 系统权限按钮；点淘页面弹窗由 OCR 处理。
    ctx = d.watch_context()
    for label in ["打开", "允许", "始终允许"]:
        ctx.when(xpath=f"//android.widget.Button[@text='{label}']").click()
    ctx.start()
    d._global_watcher_ctx = ctx

    w, h = d.window_size()
    print(f"   屏幕分辨率: {w} x {h}")
    return d


# ========================================================
# OCR 文字查找（保留历史函数名以兼容现有脚本）
# ========================================================
def u2_find_text(device, text, contains=True):
    """通过 OCR 查找文字块（保留旧函数名以兼容现有脚本）。"""
    return find_ocr_text_block(
        device, text, settle_seconds=2.0, attempts=2,
        exact=not contains, log=True,
    )


def u2_click_text(device, text, contains=True):
    """通过 OCR 查找并点击文字块（旧函数名为兼容保留）。"""
    return click_ocr_text(
        device, text, settle_seconds=2.0, attempts=2,
        exact=not contains, log=True,
    )


def handle_open_allow_popup(device):
    """处理"打开/允许/始终允许"弹窗"""
    try:
        btn = device(className="android.widget.Button", textMatches=r"打开|允许|始终允许")
        if btn.exists:
            click_u2_control(device, btn, "打开/允许")
            time.sleep(1)
            return True
    except:
        pass
    return False


def u2_find_task_buttons(device):
    """用 OCR 识别任务名和同一行的任务按钮，避免依赖 WebView 的 U2 文本树。"""
    screen_w, _ = device.window_size()
    button_x = int(screen_w * 0.84)
    blocks = get_ocr_text_blocks(device, retries=0, log_low_confidence=True)
    print(f"  🔍 [OCR] 任务按钮扫描：识别到 {len(blocks)} 个文本块")
    if not blocks:
        return []

    task_keywords = [
        "浏览精选", "精选推荐", "猜你喜欢", "看直播", "看视频", "看福利",
        "看精选直播", "看黄金档", "去浏览", "去逛逛", "去观看", "省钱",
        "上新好物", "猜价格", "猜价格游戏", "搜索并找低价", "搜索并找最低价",
    ]
    button_keywords = ["去完成", "去浏览", "去观看", "去领取", "点击得", "去逛逛", "去查看"]
    exclude_keywords = ["做任务", "领元宝", "元宝商城", "下单", "购买", "付款", "充值"]
    normalized = [
        {**block, "text": str(block.get("text", "")).strip(),
         "cx": int(block.get("cx", 0)), "cy": int(block.get("cy", 0))}
        for block in blocks if str(block.get("text", "")).strip()
    ]
    buttons = [b for b in normalized if any(k in b["text"] for k in button_keywords)]
    results = []

    for task in normalized:
        text = task["text"]
        if not any(kw in text for kw in task_keywords):
            continue
        if any(ex in text for ex in exclude_keywords):
            continue
        same_row_buttons = [
            b for b in buttons
            if abs(b["cy"] - task["cy"]) <= 90 and b["cx"] >= task["cx"]
        ]
        target = min(same_row_buttons, key=lambda b: abs(b["cy"] - task["cy"])) if same_row_buttons else None
        cx = target["cx"] if target else button_x
        cy = target["cy"] if target else task["cy"]
        results.append({
            "btn": None, "name": text, "cx": cx, "cy": cy,
            "found_by": "ocr_taskname",
        })

    if not results:
        for button in buttons:
            labels = [
                b for b in normalized
                if b is not button and b["cx"] < button["cx"]
                and 30 <= button["cx"] - b["cx"] <= 700
                and abs(button["cy"] - b["cy"]) <= 90
                and not any(k in b["text"] for k in button_keywords)
                and not any(ex in b["text"] for ex in exclude_keywords)
            ]
            task = min(labels, key=lambda b: abs(button["cy"] - b["cy"])) if labels else None
            results.append({
                "btn": None, "name": task["text"] if task else button["text"],
                "cx": button["cx"], "cy": button["cy"],
                "found_by": "ocr_button",
            })

    unique = []
    seen_y = set()
    for result in sorted(results, key=lambda item: item["cy"]):
        key = result["cy"] // 35
        if key not in seen_y:
            seen_y.add(key)
            unique.append(result)
    print(f"  📊 [OCR] 找到 {len(unique)} 个任务按钮候选")
    return unique


# ========================================================
# 黑名单过滤
# ========================================================
BLACKLIST_KEYWORDS = [
    "猜一猜", "猜谜",
    "邀请", "邀请好友", "拉好友", "助力", "分享", "去邀请",
    "下单", "购买", "充值", "付款", "元宝商城",
    "喂鸡", "蚂蚁庄园", "蚂蚁新村", "饿了么", "果园",
    "消消乐", "农场小游戏", "斗地主", "开心消消乐",
    "大众点评", "拍立淘",
    "3元抢3件包邮到家", "1元抢爆款好货",
    "全场3元3件", "好货限时直降",
    "头条极速版", "趣头条",
    "查看并完成", "玩小游戏",
    # 用户要求屏蔽“查看3个精选宝贝 / 看3个精选宝贝 / 精选宝贝”整类任务。
    "精选宝贝",
]

def is_blacklisted(text):
    if not text:
        return True
    if is_blocked_boutique_task(text):
        return True
    if '猜' in text and '猜价格' not in text and '猜价格游戏' not in text:
        return True
    for kw in BLACKLIST_KEYWORDS:
        if kw in text:
            return True
    return False


# ========================================================
# 页面扫描（返回结构化数据）
# ========================================================
def scan_page(device):
    """使用 OCR 扫描页面文字与坐标，不读取应用 WebView 的 U2 控件树。"""
    return _ocr_nodes(device)


def _ocr_nodes(device):
    """将 OCR 文本块适配为 scan_page 的节点结构。"""
    blocks = get_ocr_text_blocks(device, retries=0)
    return [
        {
            "text": str(block.get("text", "")).strip(),
            "content_desc": "",
            "class": "ocr_text",
            "resource_id": "",
            "clickable": True,
            "enabled": True,
            "scrollable": False,
            "bounds": "",
            "depth": 0,
            "cx": int(block.get("cx", 0)),
            "cy": int(block.get("cy", 0)),
        }
        for block in blocks if str(block.get("text", "")).strip()
    ]


def find_clickable_with_text(nodes, keyword):
    """查找可点击且包含文字的关键节点"""
    for n in nodes:
        if n["clickable"] and (keyword in n["text"] or keyword in n["content_desc"]):
            return n
    return None


# ========================================================
# 导航链（通用）
# ========================================================
def navigate_chain(device, steps):
    """
    OCR 通用导航链
    steps: [("关键词1", "描述1"), ("关键词2", "描述2"), ...]
    """
    for step_name, keyword in steps:
        print(f"  🧭 导航: {step_name} → 查找【{keyword}】...")
        if not u2_click_text(device, keyword):
            print(f"     ⚠️ OCR 未找到【{keyword}】，本步骤未点击")
            return False
        time.sleep(4)
        if keyword == "元宝中心" and not handle_yuanbao_live_popups(device):
            return False
    return True


def navigate_to_sleep_page(device):
    """导航：点淘 → 元宝中心 → 睡觉赚元宝"""
    print("🔄 导航到【睡觉赚元宝】...")
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    
    return navigate_chain(device, [
        ("Step 1: 元宝中心", "元宝中心"),
        ("Step 2: 睡觉赚元宝", "睡觉赚元宝"),
    ])


def navigate_to_dagong_page(device):
    """导航：点淘 → 元宝中心 → 打工赚元宝"""
    print("🔄 导航到【打工赚元宝】...")
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    
    return navigate_chain(device, [
        ("Step 1: 元宝中心", "元宝中心"),
        ("Step 2: 打工赚元宝", "打工赚元宝"),
    ])


def navigate_to_duck_page(device):
    """导航：点淘 → 元宝中心 → 点淘鸭家园"""
    print("🔄 导航到【点淘鸭家园】...")
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    
    if not navigate_chain(device, [
        ("Step 1: 元宝中心", "元宝中心"),
        ("Step 2: 点淘鸭家园", "点淘鸭家园"),
    ]):
        return False
    
    # Step 3: 点击任务列表
    print("  Step 3: 打开任务列表...")
    time.sleep(2)
    nodes = scan_page(device)
    for n in nodes:
        if "任务" in n["text"] and "去完" not in n["text"] and "完成" not in n["text"]:
            if n["cx"] > 0 and n["cy"] > 0:
                device.click(n["cx"], n["cy"])
                print(f"    OCR 点击【{n['text']}】打开任务列表")
                time.sleep(4)
                return True
    
    if u2_click_text(device, "任务"):
        time.sleep(4)
        return True
    
    return False


# ========================================================
# 返回任务列表
# ========================================================
def back_to_task(device, target_keywords=None, nav_func=None):
    """智能返回任务列表：多次尝试返回 + nav_func 兜底"""
    if target_keywords is None:
        target_keywords = ["去完成", "打工", "任务"]
    
    def _is_on_task_page():
        """检测当前是否在任务列表页"""
        found = find_ocr_text_block(device, target_keywords, attempts=1, log=False)
        if found:
            print(f"  ✅ OCR 检测到任务页文字【{found.get('text', '')}】")
            return True
        return False
    
    # 先检测是否已经在任务列表页
    if _is_on_task_page():
        return True
    
    # 多次按 back，每次检测是否回到任务页
    for attempt in range(1, 4):
        print(f"  🔙 back_to_task() 第{attempt}次返回")
        device.press("back")
        time.sleep(1.5)
        if _is_on_task_page():
            return True
    
    # 仍然没回到任务页，使用 nav_func 兜底导航
    if nav_func is not None:
        print(f"  🚀 多次返回未到任务页，调用 nav_func 重新导航...")
        try:
            nav_func(device)
            time.sleep(3)
            if _is_on_task_page():
                return True
        except Exception as e:
            print(f"  ⚠️ nav_func 导航异常: {e}")
    
    print(f"  ⚠️ back_to_task: 最终未能回到任务列表页，继续执行")
    return False


# ========================================================
# 等待任务完成
# ========================================================
def wait_completion(device, timeout=25):
    """等待任务完成（检测"恭喜完成任务"等文字）"""
    print("  ⏳ 等待任务完成...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            if has_ocr_text(
                device,
                ["恭喜完成任务", "恭喜完成", "任务完成", "获得奖励", "恭喜获得"],
                attempts=1,
            ):
                print("  ✅ 检测到完成提示")
                return True
        except:
            pass
        time.sleep(1)
    print(f"  ⏰ 等待超时 ({timeout}s)")
    return False


# ========================================================
# 猜价格游戏处理器
# ========================================================
def _close_guess_price_popup(device):
    """关闭猜价格游戏结束后的弹窗（用 live_close.png 模板匹配）"""
    screen_w, screen_h = device.window_size()
    
    # 搜索区域：X 固定 1179 ±50，Y 范围 696~795
    search_x1 = 1129
    search_y1 = 696
    search_x2 = 1229
    search_y2 = 795
    
    # 模板路径：AGENT工具脚本/../diantao/img/live_close.png
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "..", "diantao", "img", "live_close.png")
    
    found = False
    if os.path.exists(template_path):
        try:
            screenshot = device.screenshot(format='opencv')
            if screenshot is not None:
                # 裁剪搜索区域
                roi = screenshot[search_y1:search_y2, search_x1:search_x2]
                template = cv2.imread(template_path)
                if template is not None and roi.shape[0] > 0 and roi.shape[1] > 0:
                    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    tpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
                    
                    res = cv2.matchTemplate(roi_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)
                    
                    if max_val >= 0.6:
                        # 将 ROI 坐标转回屏幕坐标
                        click_x = search_x1 + max_loc[0] + template.shape[1] // 2
                        click_y = search_y1 + max_loc[1] + template.shape[0] // 2
                        print(f"    关闭弹窗: 模板匹配 live_close.png @({click_x},{click_y}) 置信度={max_val:.3f}")
                        device.click(click_x, click_y)
                        found = True
        except Exception as e:
            print(f"    ⚠️ 模板匹配异常: {e}")
    
    if not found:
        # 兜底：点区域中点
        close_x = 1179
        close_y = (search_y1 + search_y2) // 2  # 745
        print(f"    关闭弹窗: 兜底点击 @({close_x},{close_y})")
        device.click(close_x, close_y)
    
    time.sleep(1)
    return True


def is_search_low_price_task(task_name):
    text = re.sub(r'\s+', '', task_name)
    return "搜索" in text and ("低价" in text or "最低价" in text)


def _guess_price_retry_countdown(nodes):
    text = " ".join(str(n.get("text", "")) for n in nodes)
    normalized = re.sub(r'\s+', '', text).replace("：", ":")
    has_time = re.search(
        r'\d{1,2}:\d{2}(?::\d{2})?|\d+(?:\.\d+)?(?:小时|分钟|分|秒)',
        normalized,
    )
    return bool(has_time and re.search(r'后(?:再来玩|再重试|重试|再试)', normalized))


def _search_low_price(device, keyword="牙刷"):
    """搜索商品；OCR 定位失败时使用截图中的框体和按钮比例坐标。"""
    sw, sh = device.window_size()
    if not click_ocr_text(
        device, ["请输入想找的商品", "请输入", "想找的商品"],
        settle_seconds=2.5, attempts=2, log=True,
    ):
        x, y = int(sw * 0.45), int(sh * 0.657)
        print(f"  搜索框 OCR 未命中，按截图比例点击 @({x},{y})")
        device.click(x, y)
    time.sleep(2)
    try:
        device.send_keys(keyword, clear=True)
    except Exception as exc:
        print(f"  ⚠️ 搜索词输入失败：{exc}")
        return False
    print(f"  输入搜索词：{keyword}")
    if not click_ocr_text(
        device, "找最低价", settle_seconds=2, attempts=2, log=True,
    ):
        # 输入框获得焦点后整体上移；使用第二张截图中按钮的位置。
        x, y = int(sw * 0.85), int(sh * 0.392)
        print(f"  搜索按钮 OCR 未命中，按截图比例点击 @({x},{y})")
        device.click(x, y)
    time.sleep(4)
    return True


def handle_guess_price_game(device, search_first=False):
    """
    猜价格游戏处理器（前三个坐标 + 每次提交后 OCR）
    固定坐标策略：
      - 3列商品，checkmark X=[225, 633, 1043]
      - 仅第一行，最多尝试三个商品
      - 确认按钮 (633, 2520)
    退出策略：先关弹窗（X/关闭）→ 再返回任务列表
    """
    print("\n  ========== 猜价格游戏 (最多3次，每次OCR检查重试倒计时) ==========")
    screen_w, screen_h = device.window_size()

    if search_first:
        if not _search_low_price(device):
            _close_guess_price_popup(device)
            return False
        nodes = scan_page(device)
        text = " ".join(str(n.get("text", "")) for n in nodes)
        print(f"  🔎 搜索后 OCR：{text or '无识别结果'}")
        if _guess_price_retry_countdown(nodes):
            print("  ⏳ 搜索后已出现重试倒计时，停止点击")
            _close_guess_price_popup(device)
            return True
    
    if screen_w > 1000:
        col_x = [225, 633, 1043]
    else:
        col_w = screen_w // 3
        col_x = [col_w // 2, col_w * 3 // 2, col_w * 5 // 2]
    
    row1_y = int(screen_h * 0.58)
    confirm_y = int(screen_h * 0.905)
    
    max_attempts = 3
    
    for attempt in range(max_attempts):
        check_x = col_x[attempt]
        check_y = row1_y
        
        print(f"  --- 第 {attempt+1}/{max_attempts} 次: 点击 @({check_x},{check_y}) ---")
        device.click(check_x, check_y)
        time.sleep(1)
        
        device.click(int(screen_w * 0.5), confirm_y)
        time.sleep(2.5)
        
        # 每次提交后只做一次 OCR；重试提示和时间可能被 OCR 拆成多个文本块。
        try:
            nodes = scan_page(device)
            text = " ".join(str(n.get("text", "")) for n in nodes)
            print(f"  🔎 第{attempt+1}次提交后 OCR：{text or '无识别结果'}")
            if _guess_price_retry_countdown(nodes):
                print(f"  ⏳ 检测到重试倒计时 [{text}]，停止后续点击")
                time.sleep(1)
                _close_guess_price_popup(device)
                print("  ========== 猜价格游戏结束 ==========\n")
                return True
            print("  未识别到重试倒计时")
        except Exception as exc:
            print(f"  ⚠️ 提交后 OCR 异常：{exc}，停止点击")
            _close_guess_price_popup(device)
            return False
    
    print("  ⚠️ 三次尝试结束，未确认重试倒计时")
    _close_guess_price_popup(device)
    print(f"  ========== 猜价格游戏结束 ==========\n")
    return False


# ========================================================
# 浮动气泡检测
# ========================================================
def scan_float_bubbles(device, ocr_height_ratio=None):
    """
    扫描浮动 "+数字" 或纯数字气泡，排除下方带任务标题的奖励金额
    返回 [{"text": str, "cx": int, "cy": int}, ...]
    """
    nodes = (scan_page(device) if ocr_height_ratio is None else
             get_ocr_text_blocks(device, retries=0, height_ratio=ocr_height_ratio))
    screen_w, screen_h = device.window_size()
    bubbles = []
    
    seen = set()
    for n in nodes:
        text = n["text"].strip()
        if not text:
            continue
        if n["cy"] < int(screen_h * 0.13) or n["cy"] > int(screen_h * 0.40):
            continue
        
        if re.fullmatch(r"\+?\d{1,4}", text):
            # 任务圆圈的金额也可以点进任务，不能绕过任务识别当作直接奖励领取。
            if any(abs(other["cx"] - n["cx"]) <= screen_w * 0.10
                   and 0 < other["cy"] - n["cy"] <= screen_h * 0.10
                   and re.search(r"直播|视频|浏览|查看|精选|猜价格|去玩", other["text"])
                   for other in nodes):
                continue
            key = (n["cx"] // 30, n["cy"] // 30)
            if key not in seen:
                seen.add(key)
                bubbles.append({"text": text, "cx": n["cx"], "cy": n["cy"]})
    
    return bubbles


def click_float_bubbles(device, max_collect=10, ocr_height_ratio=None):
    """点击浮动气泡（每次只点1个，防止多点导致UI错乱）"""
    bubbles = scan_float_bubbles(device, ocr_height_ratio=ocr_height_ratio)
    clicked = 0
    for b in bubbles[:max_collect]:
        print(f"      点击浮动气泡: [{b['text']}] @({b['cx']}, {b['cy']})")
        device.click(b["cx"], b["cy"])
        time.sleep(1.5)
        remaining = scan_float_bubbles(device, ocr_height_ratio=ocr_height_ratio)
        if any(other["text"] == b["text"]
               and abs(other["cx"] - b["cx"]) <= 30
               and abs(other["cy"] - b["cy"]) <= 30
               for other in remaining):
            print("      气泡点击后未变化，不计为收集成功")
            break
        clicked += 1
        break  # 每次只点1个，主循环下次扫描再点下一个
    if clicked > 0:
        print(f"      本次收集 {clicked} 个浮动气泡")
    return clicked


# ========================================================
# 直播相关处理（滑动 + 关弹窗 + 检测完成）
# ========================================================
# 直播弹窗关闭
# ========================================================
# 弹窗关闭关键词库（仅保留明确关闭/跳过意图的词）
# 注意：不要加"领取""红包""福袋""分享"等，直播间的正常UI大量包含这些词，会误点
POPUP_CLOSE_TEXTS = [
    "关闭", "×", "关闭按钮", "取消",
    "我知道了", "朕知道了", "关闭广告",
    "跳过", "跳过广告",
    "不感兴趣",
]

# 关闭弹窗冷却记录：{keyword: last_click_time}，防止同一关键词高频重复点击
_last_popup_click_time = {}
_POPUP_COOLDOWN = 15  # 同一关键词 15 秒内不重复点击


def close_live_popups(device):
    """
    关闭直播间的弹窗（带冷却机制，防止误点正常UI）
    返回 True 表示关掉了至少一个弹窗
    
    策略:
      1. OCR 匹配关闭类关键词（带冷却，同一关键词15秒内不重复点击）
    """
    global _last_popup_click_time
    closed = 0
    sw, sh = device.window_size()
    now = time.time()
    upper_limit = int(sh * 0.6)
    
    # 策略1: OCR 匹配关闭类文字（带冷却）
    popup_blocks = get_ocr_text_blocks(device, retries=0)
    for text in POPUP_CLOSE_TEXTS:
        # 冷却检查：同一关键词 15 秒内不重复点
        last_click = _last_popup_click_time.get(text, 0)
        if now - last_click < _POPUP_COOLDOWN:
            continue
        btn = next((b for b in popup_blocks if text in b.get("text", "")), None)
        if not btn:
            continue
        btn_cx, btn_cy = int(btn.get("cx", 0)), int(btn.get("cy", 0))
        if btn_cy >= upper_limit or btn_cx <= 0 or btn_cy <= 0:
            continue
        device.click(btn_cx, btn_cy)
        _last_popup_click_time[text] = time.time()
        closed += 1
        time.sleep(0.3)
    
    # 不使用 hierarchy 或盲点坐标，避免误触直播间控件。
    
    if closed > 0:
        time.sleep(0.5)
    return closed > 0


def detect_live_completion(device):
    """
    通过 OCR 检测直播任务是否完成。
    返回 True 表示任务已完成
    """
    completion_texts = [
        "恭喜完成任务", "恭喜完成", "任务完成",
        "获得奖励", "恭喜获得", "已完成",
        "任务进度已完成", "进度已完成",
        "已看", "任务已",
        "谢谢观看", "感谢观看", "直播已结束",
        "本场直播已结束", "主播已离开",
    ]
    blocks = get_ocr_text_blocks(device, retries=0)
    all_text = " ".join(str(block.get("text", "")) for block in blocks)
    matched = next((text for text in completion_texts if text in all_text), None)
    if matched:
        print(f"  🔎 OCR 检测到完成状态文字：【{matched}】")
        return True
    return False


def execute_live_task(device, task_name, btn_cx, btn_cy, duration=None):
    """
    统一直播任务执行器（含 OCR 状态识别与弹窗关闭）
    
    功能：
      - 进入直播间
      - 每 5-10 秒随机滑动一次
      - 实时检测并关闭弹窗（带冷却机制，不影响正常UI）
      - 实时检测完成提示 立即退出返回任务列表
      - 停留指定时长后自动退出
      - 自动从任务名解析时长（如"看直播60秒" → 60秒）
    
    参数:
      duration: 最长停留时间（秒），None 则从 task_name 自动解析
    """
    # 智能解析：先从任务名提取 XX秒 或 X分钟
    if duration is None:
        match = re.search(r"(\d+)\s*秒", task_name)
        if match:
            duration = int(match.group(1))
        else:
            match = re.search(r"(\d+)\s*分钟", task_name)
            if match:
                duration = int(match.group(1)) * 60
            else:
                duration = 120  # 默认 2 分钟
        # 额外加 5 秒缓冲，确保看够
        duration += 5
    
    print(f"  📺 执行直播任务: 【{task_name}】 → 停留 {duration} 秒")
    sw, sh = device.window_size()
    
    time.sleep(3)
    
    handle_open_allow_popup(device)
    
    time.sleep(2)
    
    # ── 进入直播间后首次扫描：检测是否有"XX秒后完成"倒计时字样 ──
    countdown_active = False
    try:
        init_nodes = scan_page(device)
        init_text = " ".join(n.get("text", "") for n in init_nodes)
        countdown_match = re.search(r"(\d+)\s*秒后完成", init_text)
        if countdown_match:
            countdown_active = True
            remaining_sec = int(countdown_match.group(1))
            print(f"  ⏱️ 检测到倒计时【{remaining_sec}秒后完成】，等待倒计时消失...")
    except:
        pass
    
    print(f"  ⏳ 进入直播间，最长停留 {duration} 秒...")
    start = time.time()
    last_swipe = 0
    completion_detected = False
    
    while True:
        elapsed = int(time.time() - start)
        remain = duration - elapsed
        
        if elapsed >= duration:
            print(f"  ⏰ 停留时间到 ({duration}s)")
            break
        
        # ── 倒计时消失检测（直播类"XX秒后完成"文本消失即完成） ──
        if countdown_active:
            try:
                cur_nodes = scan_page(device)
                cur_text = " ".join(n.get("text", "") for n in cur_nodes)
                cur_match = re.search(r"(\d+)\s*秒后完成", cur_text)
                if not cur_match:
                    print(f"  ✅ 【{remaining_sec}秒后完成】倒计时消失，继续停留到要求时长")
                    completion_detected = True
                    countdown_active = False
                else:
                    remaining_sec = int(cur_match.group(1))
            except:
                pass
        
        # ── 关键词完成检测兜底（已有逻辑） ──
        try:
            if not completion_detected and detect_live_completion(device):
                print(f"  ✅ 检测到任务完成，继续停留到要求时长")
                completion_detected = True
        except:
            pass
        
        # ── 弹窗关闭（带冷却机制，不影响正常UI） ──
        try:
            close_live_popups(device)
        except:
            pass
        
        if elapsed >= last_swipe + random.randint(5, 10):
            swipe_x = random.randint(int(sw * 0.3), int(sw * 0.7))
            s_y = random.randint(int(sh * 0.65), int(sh * 0.80))
            e_y = random.randint(int(sh * 0.15), int(sh * 0.30))
            try:
                device.swipe(swipe_x, s_y, swipe_x, e_y, duration=random.uniform(0.3, 0.6))
            except:
                pass
            last_swipe = elapsed
            if remain > 0:
                print(f"    已过 {elapsed}s / 剩余 {remain}s，上滑一次 ⬆️")
            else:
                print(f"    已过 {elapsed}s，上滑一次 ⬆️")
        
        time.sleep(2)
    
    if completion_detected:
        print(f"  ✅ 直播任务【{task_name}】已完成")
    else:
        print(f"  ⚠️ 直播停留完成")

    time.sleep(2)
    return completion_detected


# ========================================================
# 点击后媒体页面识别
# ========================================================
def classify_media_page(blocks):
    """根据点击后的页面文本判定实际是视频流还是直播间。"""
    all_text = " ".join(
        str(block.get("text", "")).strip()
        for block in (blocks or [])
        if block.get("text")
    )

    video_score = 0
    if "上滑查看更多视频" in all_text or "查看更多视频" in all_text:
        video_score += 5
    if "发放奖励" in all_text:
        video_score += 4
    if "查看详情" in all_text:
        video_score += 1

    live_score = 0
    if "直播间" in all_text:
        live_score += 5
    if "说点什么" in all_text:
        live_score += 4
    if "秒后完成" in all_text:
        live_score += 3
    if "关注" in all_text:
        live_score += 1

    if video_score >= live_score + 2:
        return "video"
    if live_score >= video_score + 2:
        return "live"
    return None


def detect_entered_media_type(device, expected_type, task_name=""):
    """点击任务入口后只做一次识别，返回实际媒体页类型。

    页面切换期间 OCR 可能暂时为空，因此同一轮校验最多取两次页面快照；
    不会在任务执行期间持续扫描。无法确认时保留点击前的预判类型。
    """
    from utils import get_ocr_text_blocks

    for attempt in range(2):
        try:
            detected = classify_media_page(get_ocr_text_blocks(device))
        except Exception as exc:
            detected = None
            if attempt == 1:
                print(f"  ⚠️ 点击后媒体页识别失败: {exc}")

        if detected:
            print(f"  🔎 点击后 OCR 判定实际页面: {detected}")
            return detected

        if attempt == 0:
            time.sleep(1)

    print(
        f"  ⚠️ 点击后 OCR 无法确认页面，保留预判路由: "
        f"{expected_type}（任务: {task_name}）"
    )
    return expected_type


# ========================================================
# 看视频任务执行（翻页风格滑动）
# ========================================================
def video_swipe_count_range(duration: int) -> tuple[int, int]:
    """按视频时长返回基础下滑次数范围；60 秒对应 8–12 次。"""
    if duration <= 0:
        return 0, 0
    minimum = max(1, math.ceil(duration / 7.5))
    maximum = max(minimum, math.ceil(duration / 5))
    return minimum, maximum


DEFAULT_VIDEO_EXTRA_SWIPES_RANGE = (7, 9)


def execute_video_task(
    device, task_name, btn_cx, btn_cy, duration=None,
    extra_time_seconds=0, extra_swipes=None,
    swipe_interval_extra_seconds=0, verify_media_page=False,
    initial_swipe=False, initial_swipe_start_ratio=0.70,
):
    """暂停后台弹窗点击，执行固定次数视频任务，并恢复原暂停层级。"""
    pause_global_watcher(device)
    try:
        return _execute_video_task(
            device, task_name, btn_cx, btn_cy, duration=duration,
            extra_time_seconds=extra_time_seconds, extra_swipes=extra_swipes,
            swipe_interval_extra_seconds=swipe_interval_extra_seconds,
            verify_media_page=verify_media_page, initial_swipe=initial_swipe,
            initial_swipe_start_ratio=initial_swipe_start_ratio,
        )
    finally:
        resume_global_watcher(device)


def _execute_video_task(
    device, task_name, btn_cx, btn_cy, duration=None,
    extra_time_seconds=0, extra_swipes=None,
    swipe_interval_extra_seconds=0, verify_media_page=False,
    initial_swipe=False, initial_swipe_start_ratio=0.70,
):
    """
    看视频任务执行器：完成固定下滑次数，并满足最低停留时间后返回。

    `btn_cx`、`btn_cy` 仅用于兼容调用方。启用 `verify_media_page` 时，
    进入后先做一次视频/直播页面识别；如果实际落地为直播间，则转交直播
    执行器，避免把直播页当成视频流滑动。确认是视频或无法确认后，才按
    时长和下滑计划执行。`initial_swipe` 用于兼容部分任务的首个额外下滑，
    该动作也会在媒体页确认之后执行。未指定额外下滑次数时，从 7–9 次
    范围内随机选择。
    """
    if duration is None:
        match = re.search(r"(\d+)\s*秒", task_name)
        if match:
            duration = int(match.group(1))
        else:
            match = re.search(r"(\d+)\s*分钟", task_name)
            if match:
                duration = int(match.group(1)) * 60
            else:
                duration = 120
    duration += extra_time_seconds

    base_min, base_max = video_swipe_count_range(duration)
    base_swipes = random.randint(base_min, base_max) if base_max else 0
    if extra_swipes is None:
        extra_swipes = random.randint(*DEFAULT_VIDEO_EXTRA_SWIPES_RANGE)
    extra_swipes = max(0, extra_swipes)
    target_swipes = base_swipes + extra_swipes
    swipe_interval_extra_seconds = max(0, swipe_interval_extra_seconds) + 2
    base_interval = duration / target_swipes if target_swipes else 0
    swipe_interval = base_interval + swipe_interval_extra_seconds
    effective_duration = duration + swipe_interval_extra_seconds * max(0, target_swipes - 1)

    print(f"  🎬 执行看视频任务: 【{task_name}】 → 停留 {duration} 秒")
    print(
        f"  🎬 下滑计划: 基础 {base_min}-{base_max} 次，随机 {base_swipes} 次"
        f" + 额外 {extra_swipes} 次 = 共 {target_swipes} 次"
        f"（平均间隔约 {swipe_interval:.1f}s"
        f"，间隔增加 {swipe_interval_extra_seconds:.1f}s）"
    )
    sw, sh = device.window_size()

    # 入口点击已由上层任务执行器完成，这里只等待视频流稳定。
    time.sleep(2)

    if verify_media_page:
        actual_type = detect_entered_media_type(device, "video", task_name)
        print(f"  📋 点击后实际类型: {actual_type}（任务名预判: video）")
        if actual_type == "live":
            print(f"  🔁 实际进入直播间，切换直播任务执行器: 【{task_name}】")
            try:
                return execute_live_task(
                    device, task_name, btn_cx, btn_cy, duration=duration
                )
            finally:
                # 视频入口没有上层直播分支负责返回，切换到直播执行器后
                # 由这里保持视频入口原有的“执行完返回任务页”契约。
                try:
                    device.press("back")
                    time.sleep(1)
                except Exception:
                    pass

    completed = True
    if initial_swipe:
        start_y = int(sh * max(0.0, min(1.0, initial_swipe_start_ratio)))
        end_y = int(sh * 0.30)
        print(f"  🎬 视频首滑：({sw // 2},{start_y}) → ({sw // 2},{end_y})")
        try:
            device.swipe(
                sw // 2,
                start_y,
                sw // 2,
                end_y,
                duration=random.uniform(0.08, 0.15),
            )
        except Exception as exc:
            print(f"  ⚠️ 视频首滑指令异常：{exc}")
            completed = False
        time.sleep(1)

    print(
        f"  ⏳ 进入视频流，任务时长 {duration} 秒，"
        f"最低停留 {effective_duration:.1f} 秒，完成全部下滑后返回..."
    )
    start = time.time()
    swipe_index = 0
    next_swipe_at = 0.0

    while completed:
        elapsed = time.time() - start
        if swipe_index >= target_swipes and elapsed >= effective_duration:
            print(f"  ⏰ 视频调度完成 ({effective_duration:.1f}s)")
            break

        # 只执行翻页风格上滑，不读取页面、不处理弹窗、不检测完成提示。
        if swipe_index < target_swipes and elapsed >= next_swipe_at:
            swipe_x = sw // 2
            s_y = int(sh * 0.70)
            e_y = int(sh * 0.30)
            print(f"    🎬 视频滑动：({swipe_x},{s_y}) → ({swipe_x},{e_y})")
            try:
                device.swipe(swipe_x, s_y, swipe_x, e_y, duration=random.uniform(0.08, 0.15))
                print("    ✅ 视频滑动指令已发出")
            except Exception as exc:
                print(f"    ⚠️ 视频滑动指令异常：{exc}")
                completed = False
                break
            swipe_index += 1
            # 以本次实际发起时间安排下一次，延迟后不追赶旧时间表。
            next_swipe_at = elapsed + swipe_interval
            print(f"    已过 {elapsed:.1f}s，翻页 ⬆️ ({swipe_index}/{target_swipes})")
            continue

        wake_at = next_swipe_at if swipe_index < target_swipes else effective_duration
        time.sleep(min(0.5, max(0.001, wake_at - elapsed)))

    print(f"  {'✅' if completed else '⚠️'} 看视频任务【{task_name}】退出，完成下滑 {swipe_index}/{target_swipes}")
    try:
        device.press("back")
        time.sleep(1)
    except:
        pass

    return completed


# ========================================================
# 浏览任务执行
# ========================================================
# ========================================================
# 任务完成提示（「看到完成就提前收工，去做下一个」）
# ========================================================
# 实测做完任务页面会显示「已得XX金币」，这是最可靠的结算信号。
# 只认「明确的完成态」文案。绝不能用 "任务完成" 这种宽泛子串：页面常见的
# 规则文案「完成任务得额外金币」「任务完成后可领」「完成下方任务」都会误命中，
# 导致任务还没做够时长就退出。
COMPLETION_HINTS = (
    "恭喜完成任务", "恭喜完成", "恭喜获得", "任务已完成", "本轮任务已完成",
    "浏览已完成", "已完成浏览", "已完成本次", "已领取", "已完成",
    "奖励已到账", "金币已到账", "任务已达成",
)
# 「已得 N 金币」这类带金额的到账文案：只在任务真正结算后出现。
# 引导文案写的是「可得 / 最高得 / 浏览15秒得」，不含「已」，不会误命中。
# 使用者 2026-11-07：真机结算浮层**有时不写「淘金币」三个字**，只写
# 「已得30」或「下单再得100」，这两种同样是到账信号，看到就立即收工。
# (?!\d) 阻止数字回溯，(?![/／%％天次秒分钟小时件单]) 挡住「已得1/3」
# 这类进度 / 统计文案，避免把没做完的任务误判成已完成。
STRONG_DONE_RE = re.compile(
    r"(?:已得|已获得|已领取|已到账|已领到)\s*\d+\s*(?:个)?(?:淘)?金币"
    r"|(?:淘)?金币已(?:到账|入账|领取)"
    r"|已得(?:淘)?金币"
    r"|(?:已得|已获得|已领取|已到账|已领到)\s*\d{1,5}(?!\d)(?![/／%％天次秒分钟小时件单])"
    r"|下单再得\s*\d{1,5}(?!\d)(?![/／%％天次秒分钟小时件单])"
)
# 「下单再得N」命中负向词「再得」，但它就是结算浮层的固定伴随文案，必须放行。
SETTLEMENT_OVERRIDE_RE = re.compile(r"^下单再得\s*\d{1,5}(?!\d)")
# 命中即否决：规则 / 引导 / 商品文案 / 常驻统计，都不是本次完成提示。
COMPLETION_NEGATIVE = (
    "完成任务得", "完成任务可", "完成任务后", "完成任务即", "完成任务领",
    "完成任务赚", "完成下方任务", "完成以下任务", "完成指定任务", "做任务得",
    "看视频得", "浏览得", "去完成", "待完成", "未完成", "还需", "再浏览",
    "前往完成", "即可获得", "可获得", "有机会得",
    "可得", "最高得", "最高可", "再得", "即得", "将得",
    "今日已得", "累计已得", "共已得", "总共已得", "累计获得",
)


def is_strong_completion(text):
    """是否「已得N / 下单再得N」这类带金额的结算文案（页面只在做完后显示）。"""
    return bool(STRONG_DONE_RE.search(re.sub(r"\s+", "", str(text or ""))))


def page_shows_completion(nodes, exclude=None):
    """从 OCR 文字块里判断页面是否出现「本任务已完成」的明确提示。

    返回命中的文案，没有则返回 None。exclude 是进入任务页时首屏已有的文字，
    用来排除「今日已得300金币」这类常驻统计：只有浏览过程中新出现的完成
    文案才算数，避免一进页面就被旧文案骗到提前退出。长句、规则/引导文案、
    未做满进度一律不认。
    """
    short = []
    for n in nodes or []:
        try:
            t = re.sub(r"\s+", "", str(n.get("text", "")))
        except Exception:
            continue
        if not t:
            continue
        short.append(t)
        if len(t) > 24 or (exclude and t in exclude):
            continue
        if (any(w in t for w in COMPLETION_NEGATIVE)
                and not SETTLEMENT_OVERRIDE_RE.search(t)):
            continue
        # 「已完成 1/3」「已领取 1/2」是进度不是完成，只有做满才算。
        m = re.search(r"(\d+)\s*/\s*(\d+)", t)
        if m and m.group(1) != m.group(2):
            continue
        for h in COMPLETION_HINTS:
            if h in t:
                return t
        if STRONG_DONE_RE.search(t):
            return t
    # OCR 有时把「已得30」和「金币」拆成两块，补一次同屏组合判定。
    for s in short:
        m = re.fullmatch(r"((?:已得|已获得|已到账)\s*\d+)", s)
        if m and not (exclude and m.group(1) in exclude) \
                and any("金币" in x and len(x) <= 12 for x in short):
            return m.group(1) + "金币"
    return None


def page_shows_coin_settlement(nodes, exclude=None):
    """只认「已得N / 下单再得N」这类金币到账的强结算信号。

    数字后面有没有「淘金币」三个字都认（真机浮层两种都出现过）；「下单再得N」
    含「再得」但属于结算浮层伴随文案，由 SETTLEMENT_OVERRIDE_RE 放行。
    与 page_shows_completion 的区别：只认带金额 / 到账的结算文案（长度放宽到
    40 字，结算浮层常带一句说明），不受泛化「已完成」文案影响。看到它即代表
    金币已经到账，可以立刻收工，不需要再等待任何保险时间。
    """
    short = []
    for n in nodes or []:
        try:
            t = re.sub(r"\s+", "", str(n.get("text", "")))
        except Exception:
            continue
        if t:
            short.append(t)
    for t in short:
        if len(t) > 40 or (exclude and t in exclude):
            continue
        if (any(w in t for w in COMPLETION_NEGATIVE)
                and not SETTLEMENT_OVERRIDE_RE.search(t)):
            continue
        if STRONG_DONE_RE.search(t):
            return t
    # OCR 把「已得30」和「淘金币」拆成两块时，补一次同屏组合判定。
    for s in short:
        m = re.fullmatch(r"((?:已得|已获得|已领取|已到账)\s*\d+)", s)
        if m and not (exclude and m.group(1) in exclude) \
                and any("金币" in x and len(x) <= 12 for x in short):
            return m.group(1) + "淘金币"
    return None


def confirm_page_completion(device, first_nodes, elapsed, min_duration, exclude=None):
    """浏览途中检测「页面已提示完成」，全部条件满足才允许提前收工。

    三重保险，任一不满足都继续正常浏览，绝不提前退出：
      1) 已满足任务要求的最低时长 min_duration（没待够绝不提前走）
      2) 命中的是浏览过程中新出现的完成态文案（排除规则/引导文案、
         常驻统计、未满进度）
      3) 「已得N金币」等带金额的结算文案一次命中即收工（浮层 2~3s 就消失，
         等二次扫描反而会错过）；泛化文案仍需独立二次 OCR 确认
    扫描异常一律按「未确认」处理，不影响原有浏览流程。
    """
    if elapsed < min_duration:
        return False
    hit = page_shows_completion(first_nodes, exclude)
    if not hit:
        return False
    if is_strong_completion(hit):
        print(f"  ✅ 页面显示【{hit}】，金币已结算，立即收工做下一个"
              f"（已过 {int(elapsed)}s / 要求 {min_duration}s，不追加保险时间）")
        return True
    print(f"  🔎 疑似完成提示【{hit}】，二次确认中…")
    time.sleep(0.8)
    try:
        second_nodes = scan_page(device)
    except Exception:
        print("  ⚠ 二次确认扫描失败，继续浏览（不提前退出）")
        return False
    hit2 = page_shows_completion(second_nodes, exclude)
    if not hit2:
        print("  ⚠ 二次确认未命中，继续浏览（不提前退出）")
        return False
    print(f"  ✅ 页面连续两次提示完成【{hit2}】，提前收工去做下一个"
          f"（已过 {elapsed}s / 要求 {min_duration}s）")
    return True


# 「全程没出现结算信号」时的兜底时间上限（秒）。使用者 2026-11-07 明确：
# 兜底 2s 就够，不要再等 4s / 5s。看到「已得N淘金币」仍是立即收工，
# 这段兜底只在 OCR 漏读 / 浮层错过 / 文案变化时才会用到。
FALLBACK_GRACE_SECONDS = 2


def execute_browse_task(device, min_duration=0, max_wait=120,
                        extra_seconds=FALLBACK_GRACE_SECONDS):
    """
    通用浏览任务：实时扫描"滑动浏览XX秒"字样，消失即任务完成
    适用于：签到赢元宝、点淘购物赚钱、睡觉赚元宝等所有浏览类任务
    
    策略：
      1. 先实时扫描"滑动浏览XX秒"字样，如果找到 → 提取秒数N，至少滑动N秒后再退出
      2. 如果未找到滑动字样 → 按 min_duration 下滑；看到「已得N淘金币」立即收工，
         始终没看到才兜底 extra_seconds 秒
      3. 兜底 max_wait 秒超时退出
      4. 下滑间隔缩短，防止文字提前滑出屏幕导致误判完成
    
    参数:
        min_duration: 无论是否检测到滑动字样，都必须满足的最小浏览时间（秒）
        max_wait: 超时上限（秒）
        extra_seconds: 仅「全程没出现结算信号」时的兜底时间（秒），
                       上限 FALLBACK_GRACE_SECONDS(2s)；页面显示
                       「已得N淘金币」时立即收工，不追加
    返回:
        True 正常完成, False 超时
    """
    sw, sh = device.window_size()
    swipe_count = 0
    start = time.time()

    # ── 「保险时间」只在始终没看到结算信号时兜底 ──
    # 实测任务做完页面会显示「已得30淘金币」，即金币已到账的强信号。看到就
    # 立刻收工，不再追加任何保险时间（使用者要求）。只有全程没出现结算文案
    # （OCR 漏读 / 浮层错过 / 文案变化）时，才额外兜底 N 秒，保证任务做够，
    # 不会「没完成就退出」。
    # 无论任务标注 15s / 30s / 60s，兜底一律最多 2s（调用方显式传更大值
    # 也封顶，避免别的任务又拖出 10s 保险时间）。旧版对 <=30s 任务硬写 4s、
    # 其余用默认 5s，使用者已明确「兜底 2s 就够了」。
    effective_extra = min(max(extra_seconds, 0), FALLBACK_GRACE_SECONDS)
    print(f"  ⏱️ 看到【已得N / 下单再得N】立即收工；仅无结算信号时兜底 {effective_extra}s")

    # 立即下滑一次，不等页面扫描
    swipe_x = int(sw * 0.5)
    s_y = random.randint(int(sh * 0.65), int(sh * 0.75))
    e_y = random.randint(int(sh * 0.15), int(sh * 0.25))
    try:
        device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
    except:
        pass
    time.sleep(0.8)

    # 再检测"滑动浏览XX秒"字样，兼容"秒"字未被控件树收录
    nodes = scan_page(device)
    # 进入任务页时首屏已有的文字：用于排除「今日已得300金币」这类常驻统计，
    # 只有浏览过程中新出现的完成文案才算完成信号。
    baseline_texts = {
        re.sub(r"\s+", "", str(n.get("text", "")))
        for n in nodes or [] if isinstance(n, dict)
    }
    has_sliding_text = False
    sliding_min_seconds = 0
    for n in nodes:
        text = n["text"]
        match = re.search(r"滑动浏览\s*(\d+)", text)
        if match:
            detected_sec = int(match.group(1))
            # 兜底已在函数入口统一封顶为 FALLBACK_GRACE_SECONDS(2s)，
            # 这里不再按倒计时秒数把 4s 写回去。
            has_sliding_text = True
            # 页面倒计时可能已减少，不能覆盖调用方要求的最低时长。
            # 保险时间不再计入「必须等满」的时长：页面一旦显示「已得N淘金币」
            # 就立即收工；始终没出现结算信号才兜底到 fallback_deadline。
            sliding_min_seconds = max(min_duration, detected_sec)
            fallback_deadline = sliding_min_seconds + effective_extra
            print(f"  📋 检测到【{text}】，任务要求 {sliding_min_seconds}s"
                  f"（看到【已得N / 下单再得N】立即收工，不追加保险时间）")
            break
    
    if has_sliding_text:
        # 模式A：实时监控"滑动浏览XX秒"直到消失，但至少满足提取的秒数
        # 首次扫描的等待不能抵扣浏览时间；超时仍从函数入口累计。
        browse_start = time.time()
        print(f"  📋 实时监控【滑动浏览XX秒】字样...")
        while time.time() - start < max_wait:
            elapsed = int(time.time() - browse_start)
            
            nodes = scan_page(device)
            found_sliding = False
            remaining = -1
            for n in nodes:
                text = n["text"]
                match = re.search(r"滑动浏览\s*(\d+)", text)
                if match:
                    found_sliding = True
                    remaining = int(match.group(1))
                    if swipe_count == 0 or swipe_count % 3 == 0 or remaining <= 5:
                        print(f"    检测到【{text}】剩余 {remaining}s... (已过{elapsed}s)")
                    break

            elapsed_f = time.time() - browse_start
            elapsed = int(elapsed_f)
            hard_min = max(min_duration, sliding_min_seconds)
            # ① 「已得N淘金币」= 金币已到账，最强证据：看到立即收工，
            #    不受倒计时是否仍在跑的限制，也不追加任何保险时间。
            #    仍需待够任务要求时长，避免把常驻统计误当成本次结算。
            settled = page_shows_coin_settlement(nodes, baseline_texts)
            if settled and elapsed_f >= hard_min:
                print(f"  ✅ 已显示【{settled}】，金币已结算，立即收工"
                      f"（{elapsed}s / 要求 {hard_min}s，不追加保险时间）")
                return True
            # ② 泛化完成文案仍走三重保险（含独立二次 OCR 确认）；
            #    倒计时还在跑（remaining > 3）时不认，防止误读。
            if (not found_sliding or remaining <= 3) and confirm_page_completion(
                    device, nodes, elapsed_f, hard_min, baseline_texts):
                return True
            # ③ 倒计时消失 / 将尽，且已待够任务要求时长 → 直接收工
            if found_sliding and 0 <= remaining <= 3 and elapsed_f >= hard_min:
                print(f"  ✅ 滑动剩余 {remaining}s，即将完成，下滑收尾...")
                for _ in range(4):
                    swipe_count += 1
                    s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                    e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                    try:
                        device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
                    except:
                        pass
                    time.sleep(0.5)
                time.sleep(1)
                return True

            if not found_sliding and elapsed_f >= hard_min:
                print(f"  ✅ 【滑动浏览XX秒】字样消失，已达 {hard_min}s 要求 (共滑动{swipe_count}次)")
                for _ in range(4):
                    swipe_count += 1
                    s_y = random.randint(int(sh * 0.15), int(sh * 0.25))
                    e_y = random.randint(int(sh * 0.65), int(sh * 0.75))
                    try:
                        device.swipe(swipe_x, s_y, swipe_x, e_y, duration=0.3)
                    except:
                        pass
                    time.sleep(0.5)
                time.sleep(1)
                return True
            if not found_sliding:
                print(f"  ⚠️ 字样已消失但仅过 {elapsed}s < {hard_min}s，继续滑动等候...")

            # ④ 全程没有任何完成信号：兜底时间到了也正常收工，
            #    避免 OCR 一直漏读时无限滑下去。
            if elapsed_f >= fallback_deadline:
                print(f"  ⏱️ 全程未见结算提示，兜底 {effective_extra}s 已满，正常收工（{elapsed}s）")
                return True

            # 每次页面扫描之间连续滑动，提升浏览任务完成效率
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
        time.sleep(0.2)
        return False
    
    # 模式B：未检测到滑动字样，按任务要求时长下滑 + 实时结算检测
    if min_duration > 0:
        # 「保险时间」不再计入必须等满的时长：页面一旦显示「已得N淘金币」
        # 就立即收工。只有全程没出现结算文案（OCR 漏读 / 浮层错过）时，
        # 才额外兜底 effective_extra 秒，保证任务做够、不会没完成就退出。
        fixed_duration = min_duration
        grace_duration = min_duration + effective_extra
        browse_start = time.time()
        reached = False
        print(f"  📋 未检测到滑动字样，任务要求 {fixed_duration}s；"
              f"看到【已得N / 下单再得N】立即收工，仅无结算信号时兜底 {effective_extra}s...")
        while time.time() - start < max_wait:
            now = time.time()
            elapsed_f = now - browse_start
            if elapsed_f >= fixed_duration:
                reached = True
                # 兜底时间到点就直接收工，不再多花一次 OCR
                if elapsed_f >= grace_duration:
                    print(f"  ⏱️ 全程未见结算提示，兜底 {effective_extra}s 已满，正常收工")
                    break
                # 达到任务要求时长后每一轮都查（原来每 2s 一次，2~3s 的
                # 结算浮层经常整个错过，白等到保险时间结束）。
                hint_nodes = None
                try:
                    hint_nodes = scan_page(device)
                except Exception:
                    pass
                if hint_nodes is not None:
                    settled = page_shows_coin_settlement(hint_nodes, baseline_texts)
                    if settled:
                        print(f"  ✅ 已显示【{settled}】，金币已结算，立即收工"
                              f"（{int(elapsed_f)}s / 要求 {fixed_duration}s，不追加保险时间）")
                        return True
                    if confirm_page_completion(device, hint_nodes, elapsed_f,
                                               fixed_duration, baseline_texts):
                        return True
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
            time.sleep(0.5)
        if not reached:
            print(f"  ⏰ 浏览超时 ({max_wait}s)，未达到 {fixed_duration}s 要求")
            return False
        print(f"  浏览完成，滑动 {swipe_count} 次")
    else:
        print(f"  📋 未检测到滑动字样，min_duration=0，快速返回")
    
    time.sleep(0.2)
    return True


# ========================================================
# 通用执行任务入口
# ========================================================
def execute_task(device, task_name, btn_cx, btn_cy, browse_duration=20, guess_price_enabled=True):
    """通用任务执行：点击 → 等待 → 返回"""
    if is_blocked_boutique_task(task_name):
        print(f"  ⊘ 已屏蔽商品三次点击任务: 【{task_name}】")
        return False
    print(f"  🎯 执行任务: 【{task_name}】")
    device.click(btn_cx, btn_cy)
    time.sleep(3)
    
    handle_open_allow_popup(device)
    
    if guess_price_enabled and ("猜价格" in task_name or is_search_low_price_task(task_name)):
        print(f"  检测到猜价格任务")
        return handle_guess_price_game(device, search_first=is_search_low_price_task(task_name))
    
    # 浏览任务（含完成检测）
    execute_browse_task(device, browse_duration)
    return True


# ========================================================
# 旧版三次商品点击入口（保留兼容，禁止设备操作）
# ========================================================
def click_three_selected_items(device):
    """旧调用方仍可导入；任务已禁用，不点击商品，也不报告完成。"""
    print("  ⊘ 已禁用优惠/精选商品三次点击任务")
    return False


# ========================================================
# 结束判断：连续N轮相同任务列表 + 下滑
# ========================================================
def is_stale_task_list(prev_snapshots, current_buttons, max_same=3):
    """
    判断任务列表是否已经耗尽（连续 max_same 轮快照相同）
    
    用法：
      prev = []  # 在 while 循环外初始化
      while True:
          stale, current = is_stale_task_list(prev, u2_find_task_buttons(device), 3)
          if stale: break
          ...
    """
    current_set = frozenset(
        (b["name"][:15], b["cx"] // 60, b["cy"] // 60)
        for b in current_buttons
    )
    prev_snapshots.append(current_set)
    if len(prev_snapshots) > max_same:
        prev_snapshots.pop(0)
    
    if len(prev_snapshots) >= max_same and len(set(prev_snapshots)) == 1:
        return True, current_buttons
    return False, current_buttons


def is_stale_no_buttons(prev_snapshots, current_buttons, max_same=3):
    """
    判断任务列表是否为空 + 连续 max_same 轮无按钮（列表已滑到底）
    用于没找到任何按钮时判断是否真的结束
    """
    no_btn = len(current_buttons) == 0
    prev_snapshots.append(no_btn)
    if len(prev_snapshots) > max_same:
        prev_snapshots.pop(0)
    
    if len(prev_snapshots) >= max_same and all(prev_snapshots):
        return True
    return False
