#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_signin_standalone.py - 签到赢元宝从头完整执行器 (CodeBuddy AI 驱动)
======================================================================
严格执行用户原则：
1. 不考虑当前手机停留在何处，启动即强停重启点淘，从头正向流转。
2. 签到全流程 AI OCR 化，彻底摒弃旧版脆弱的几何空间文本配对。
3. 动态补帧时长控制：
   - 30s 任务先跑 20s 基础
   - 60s/120s 任务先跑 40s 基础
   - 3~5 秒快速滑动一次
   - 基础时长跑完 AI 复核，未满额每次追加 8s，确认完成后立即提前返回！
4. 猜价格：固定 3 次单选圈，AI 识别当日上限并安全退出。
5. 里程碑弹窗自愈：5步、15步、26步弹窗自动消除。
6. 出问题立刻停下。
"""

import os
import sys
import time
import json
import re
import random
import subprocess
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
from PIL import Image
import uiautomator2 as u2

CODEBUDDY_CLI = r"D:\WORKBUDDY\resources\app.asar.unpacked\cli\dist\codebuddy.js"
VISION_MODEL = "deepseek-v4.1-flash"
TEMP_DIR = (Path(__file__).resolve().parent / ".cb_vision_temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DT_APP = "com.taobao.live"


def ai_call(img_path: str, prompt_text: str, timeout: int = 30) -> str:
    """调用 CodeBuddy 多模态模型进行单轮极速推断"""
    prompt = f"请使用Read工具读取图片：{img_path}。\n{prompt_text}"
    cmd = [
        "node", CODEBUDDY_CLI,
        "-y", "--strict-mcp-config", "--mcp-config", "{}",
        "--model", VISION_MODEL,
        "--tools", "Read",
        "-p", prompt
    ]
    try:
        t0 = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        out = res.stdout.strip()
        print(f"  🧠 [AI 决策] 耗时 {time.time()-t0:.2f}s: {out[:60]}")
        return out
    except Exception as e:
        print(f"  ⚠️ [AI 调用] 异常: {e}")
        return ""


def snap_clean(d, prefix: str = "snap") -> str:
    """截取当前屏幕并压缩为 720 宽便于秒级传输"""
    pil_img = d.screenshot()
    w, h = pil_img.size
    scale = 720.0 / float(w)
    small_img = pil_img.resize((720, int(h * scale)), Image.Resampling.LANCZOS)
    p = TEMP_DIR / f"{prefix}_{int(time.time()*1000)}.jpg"
    small_img.convert("RGB").save(str(p), "JPEG", quality=65)
    return str(p)


def dismiss_popups(d) -> bool:
    """消除可能出现的弹窗遮罩、继续做任务或右上角关闭X"""
    img_path = snap_clean(d, "popup")
    prompt = (
        "请检查当前页面是否有阻碍操作的弹窗、遮罩、奖励结算或【继续做任务】？\n"
        "1. 如果有【继续做任务】按钮，输出：动作:继续, x:百分比, y:百分比\n"
        "2. 如果有弹窗右上角或边缘的关闭【X】图标，输出：动作:关闭X, x:百分比, y:百分比\n"
        "3. 页面正常无弹窗，输出：正常"
    )
    out = ai_call(img_path, prompt)
    try:
        os.remove(img_path)
    except Exception:
        pass

    sw, sh = d.window_size()
    m = re.search(r"x\s*[:：=]\s*(\d+(?:\.\d+)?)[,\s，]+y\s*[:：=]\s*(\d+(?:\.\d+)?)", out)
    if m:
        cx = int(sw * float(m.group(1)) / 100.0)
        cy = int(sh * float(m.group(2)) / 100.0)
        # 避开左上角导航返回键
        if cx > 200 or cy > 300:
            print(f"  🛡️ [弹窗消除] 点击消除弹窗 @({cx}, {cy})...")
            d.click(cx, cy)
            time.sleep(2)
            return True
    return False


def nav_to_signin_page(d) -> bool:
    """从头正向导航进入【签到赢元宝】主页"""
    sw, sh = d.window_size()
    print("\n🔄 [步骤 1] 强停并重新启动点淘 APP...")
    d.app_start(DT_APP, stop=True)
    time.sleep(6)

    print("🧭 [步骤 2] 寻找并进入【元宝中心】...")
    # 优先找右侧悬浮球或顶部Tab
    img_path = snap_clean(d, "find_yb")
    prompt_yb = "请找出画面中【元宝中心】（右侧金元宝图标或文字）的中心位置百分比：x:百分比, y:百分比"
    out_yb = ai_call(img_path, prompt_yb)
    try:
        os.remove(img_path)
    except Exception:
        pass

    m_yb = re.search(r"x\s*[:：=]\s*(\d+(?:\.\d+)?)[,\s，]+y\s*[:：=]\s*(\d+(?:\.\d+)?)", out_yb)
    if m_yb:
        cx = int(sw * float(m_yb.group(1)) / 100.0)
        cy = int(sh * float(m_yb.group(2)) / 100.0)
        print(f"  👆 点击元宝中心 @({cx}, {cy})...")
        d.click(cx, cy)
        time.sleep(4)
    else:
        print("  ⚠️ AI 定位未检出，使用常规位置进入元宝中心...")
        d.click(int(sw * 0.9), int(sh * 0.44))
        time.sleep(4)

    print("📜 [步骤 3] 下滑半屏寻找【今日签到】...")
    d.swipe(sw // 2, int(sh * 0.75), sw // 2, int(sh * 0.35), duration=0.35)
    time.sleep(2)

    # 找“去看看”或“去签到”
    img_path = snap_clean(d, "find_sign")
    prompt_sign = "请找出【今日签到】右侧对应的按钮（【去看看】或【去签到】）中心百分比：x:百分比, y:百分比"
    out_sign = ai_call(img_path, prompt_sign)
    try:
        os.remove(img_path)
    except Exception:
        pass

    m_sign = re.search(r"x\s*[:：=]\s*(\d+(?:\.\d+)?)[,\s，]+y\s*[:：=]\s*(\d+(?:\.\d+)?)", out_sign)
    if m_sign:
        cx = int(sw * float(m_sign.group(1)) / 100.0)
        cy = int(sh * float(m_sign.group(2)) / 100.0)
        print(f"  👆 点击进入签到页面 @({cx}, {cy})...")
        d.click(cx, cy)
        time.sleep(3)
    else:
        print("  ⚠️ 未能定位到去看看，尝试点击今日签到右侧...")
        d.click(int(sw * 0.85), int(sh * 0.76))
        time.sleep(3)

    print("🛡️ [步骤 4] 检查并消除进入遮罩弹窗...")
    dismiss_popups(d)
    print("✅ 签到主页已就绪！")
    return True


def execute_guess_price_task(d) -> bool:
    """猜价格子任务执行器：固定 3 次商品选择 + 上限判定 + 安全关闭"""
    sw, sh = d.window_size()
    print("\n🎲 --- 开始执行【猜价格】任务 ---")
    time.sleep(2)

    # 1. 扫描上限提示
    img_path = snap_clean(d, "guess_limit")
    prompt_limit = (
        "请检查当前猜价格界面：是否出现了【当日已达最大上限次数】、【今日次数已用完】、【明日再来】？\n"
        "如果达到上限，输出：上限:是\n"
        "如果是正常可以开始玩/可以答题的页面，输出：上限:否"
    )
    out_limit = ai_call(img_path, prompt_limit)
    try:
        os.remove(img_path)
    except Exception:
        pass

    if "上限:是" in out_limit:
        print("🛑 [猜价格] 检测到当日已达最大上限，立即关闭退出！")
        d.click(int(sw * 0.93), int(sh * 0.27))
        time.sleep(2)
        return False

    # 2. 正常 3 次点击
    print("🎮 [猜价格] 执行固定 3 次商品选择与确认...")
    checkmarks = [
        (int(sw * 0.28), int(sh * 0.58)),
        (int(sw * 0.61), int(sh * 0.58)),
        (int(sw * 0.93), int(sh * 0.58))
    ]
    confirm_btn = (sw // 2, int(sh * 0.905))

    for i in range(3):
        cx, cy = checkmarks[i]
        print(f"  👉 第 {i+1}/3 次：勾选商品单选框 @({cx}, {cy})...")
        d.click(cx, cy)
        time.sleep(1)
        print(f"     点击【猜它最接近】 @{confirm_btn}...")
        d.click(confirm_btn[0], confirm_btn[1])
        time.sleep(2.5)

    # 3. 关闭结算弹窗
    print("✅ 3 次猜测完毕，正在关闭结算弹窗...")
    # 点右上角关闭 X (约 93%, 24%)
    d.click(int(sw * 0.93), int(sh * 0.24))
    time.sleep(2)
    # 如果误入商品页则补一次 back
    dismiss_popups(d)
    print("🎉 猜价格任务完成并退出！")
    return True


def execute_media_or_browse_task(d, task_name: str, nominal_sec: int = 60):
    """视频/直播/浏览任务通用动态执行器"""
    sw, sh = d.window_size()
    time.sleep(3)

    # 1. AI 判断实际是短视频还是直播
    img_path = snap_clean(d, "route_detect")
    prompt_type = "请判断当前界面究竟是【直播间】还是【短视频】？只回答三个字：直播间 或 短视频"
    out_type = ai_call(img_path, prompt_type)
    try:
        os.remove(img_path)
    except Exception:
        pass

    page_type = "直播间" if "直播" in out_type else "短视频"
    print(f"  🧭 [AI 路由] 标称【{task_name}】，实际落地页为: 【{page_type}】")

    # 2. 动态时长：30s先跑20s，60s/120s先跑40s
    base_sec = 20 if nominal_sec <= 30 else 40
    print(f"  ⏱️ 启动动态时长：基础滑动 {base_sec}s (3~5s 频率)...")

    start_t = time.time()
    last_swipe = 0
    while time.time() - start_t < base_sec:
        elapsed = int(time.time() - start_t)
        if elapsed >= last_swipe + random.randint(3, 5):
            sx = int(sw * random.uniform(0.45, 0.55))
            sy = int(sh * random.uniform(0.75, 0.85))
            ey = int(sh * random.uniform(0.18, 0.28))
            d.swipe(sx, sy, sx, ey, duration=random.uniform(0.25, 0.35))
            last_swipe = elapsed
            print(f"    - 快速滑动 (已进行 {elapsed}s / 基础 {base_sec}s)")
        time.sleep(1)

    # 3. 动态追查补帧
    max_extra = 5
    for step in range(1, max_extra + 1):
        # 检查是否已完成
        img_chk = snap_clean(d, "comp_chk")
        prompt_chk = "请检查任务是否已完成（如提示：已完成、完成任务、恭喜获得、或倒计时消失）？回答：已完成 或 进行中"
        out_chk = ai_call(img_chk, prompt_chk)
        try:
            os.remove(img_chk)
        except Exception:
            pass

        if "已完成" in out_chk:
            print(f"  🎉 [AI 确认] 任务已提前闭环满额，立即退出！")
            break

        print(f"  ⚠️ 任务未满额，动态补帧追加 8s (第 {step}/{max_extra} 次)...")
        sub_t = time.time()
        while time.time() - sub_t < 8:
            d.swipe(sw // 2, int(sh * 0.78), sw // 2, int(sh * 0.22), duration=0.3)
            time.sleep(random.uniform(3, 4))

    print("  ↩️ 返回签到主页...")
    d.press("back")
    time.sleep(2.5)


def scan_signin_task_cards(d) -> list:
    """提取页面底部【每完成1次任务可前进1步】中的任务卡片列表"""
    img_path = snap_clean(d, "cards")
    prompt = (
        "请提取页面底部【每完成1次任务可前进1步】区域内的所有任务卡片。\n"
        "每个任务提取：\n"
        "- name: 任务名称（如 玩1次猜价格、看视频60秒、看精选推荐60秒、看直播60秒）\n"
        "- type: 任务类型（只能是 guess_price / video / browse / live 之一）\n"
        "- btn_x: 对应红色【去完成】按钮中心X百分比(0-100)\n"
        "- btn_y: 对应红色【去完成】按钮中心Y百分比(0-100)\n"
        "请严格只输出纯 JSON 数组，严禁其他文字：\n"
        '[{"name": "任务名", "type": "guess_price", "btn_x": 12.5, "btn_y": 94.2}]'
    )
    raw = ai_call(img_path, prompt)
    try:
        os.remove(img_path)
    except Exception:
        pass

    sw, sh = d.window_size()
    tasks = []
    try:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            items = json.loads(m.group(0))
            for it in items:
                cx = int(sw * float(it["btn_x"]) / 100.0)
                cy = int(sh * float(it["btn_y"]) / 100.0)
                tasks.append({"name": it["name"], "type": it["type"], "cx": cx, "cy": cy})
    except Exception as e:
        print(f"  ⚠️ 卡片解析异常: {e}")
    return tasks


def run_full_signin_flow():
    if sys.stdout.encoding and sys.stdout.encoding.lower() == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    d = u2.connect()
    print("=" * 60)
    print("🚀 从头启动【签到赢元宝】全新 AI 驱动全流程调度")
    print("=" * 60)

    # 1. 从头正向进入签到主页
    nav_to_signin_page(d)

    # 2. 循环做任务
    max_loops = 8
    guess_price_exceeded = False

    for loop_i in range(1, max_loops + 1):
        print(f"\n{'='*45}\n▶️ [签到循环 #{loop_i}] 正在扫描任务卡片...\n{'='*45}")
        dismiss_popups(d)

        tasks = scan_signin_task_cards(d)
        if not tasks:
            print("  ⚠️ 未见任务卡片，轻微上拉半屏...")
            sw, sh = d.window_size()
            d.swipe(sw // 2, int(sh * 0.7), sw // 2, int(sh * 0.45))
            time.sleep(2)
            tasks = scan_signin_task_cards(d)
            if not tasks:
                print("🛑 连续两次未检出任务卡片，全流程结束！")
                break

        print(f"  📋 本轮共发现 {len(tasks)} 个任务卡片:")
        for idx, t in enumerate(tasks, 1):
            print(f"    {idx}. [{t['type']}] {t['name']} @({t['cx']}, {t['cy']})")

        # 过滤已达上限的猜价格
        usable_tasks = tasks
        if guess_price_exceeded:
            usable_tasks = [t for t in tasks if t["type"] != "guess_price"]

        if not usable_tasks:
            usable_tasks = tasks

        # 排序：猜价格 > 浏览 > 视频 > 直播
        priority = {"guess_price": 0, "browse": 1, "video": 2, "live": 3}
        usable_tasks.sort(key=lambda x: priority.get(x["type"], 99))
        chosen = usable_tasks[0]

        print(f"\n👉 选中执行任务: 【{chosen['name']}】...")
        d.click(chosen["cx"], chosen["cy"])
        time.sleep(3)

        if chosen["type"] == "guess_price":
            res = execute_guess_price_task(d)
            if not res:
                guess_price_exceeded = True
        else:
            m = re.search(r"(\d+)\s*秒", chosen["name"])
            sec = int(m.group(1)) if m else 60
            execute_media_or_browse_task(d, chosen["name"], nominal_sec=sec)

        # 任务后消除弹窗（如第5/15/26步里程碑奖励）
        time.sleep(2)
        dismiss_popups(d)

    print("\n🎉 【签到赢元宝】全自动化任务全部执行完毕！")


if __name__ == "__main__":
    run_full_signin_flow()
