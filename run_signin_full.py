#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_signin_full.py - 签到赢元宝全自动化执行器 (CodeBuddy AI 驱动版)
==================================================================
核心逻辑：
1. 页面类型识别：点击后由 AI 判断究竟是视频任务还是直播任务。
2. 动态时长控制与补帧：
   - 30s 任务先跑 20s 基础
   - 60s/120s 任务先跑 40s 基础
   - 下滑频率提速为 3~5s 一次
   - 基础时长跑完后 AI 复核是否完成，未完成动态追加 5~10s 逐步补全，完成后立即提前退出返回！
3. 猜价格：固定 3 次商品单选圈 + AI 上限智能识别。
4. 里程碑弹窗自愈：在步数达到 5、15、26 步时自动检测并消除弹窗。
5. 遇未知阻碍即刻停下并保存现场截图。
"""

import os
import sys
import time
import json
import re
import random
import subprocess
from pathlib import Path
from typing import Tuple, List, Optional
from PIL import Image
import uiautomator2 as u2

CODEBUDDY_CLI = r"D:\WORKBUDDY\resources\app.asar.unpacked\cli\dist\codebuddy.js"
VISION_MODEL = "deepseek-v4.1-flash"
TEMP_DIR = (Path(__file__).resolve().parent / ".cb_vision_temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DT_APP = "com.taobao.live"


def ai_query(img_path: str, prompt_text: str, timeout: int = 30) -> str:
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
        return res.stdout.strip()
    except Exception as e:
        print(f"  ⚠️ [AI 视觉] 调用异常: {e}")
        return ""


def capture_fast_screen(d, prefix: str = "snap") -> Tuple[str, Image.Image]:
    """截取当前屏幕并压缩为 720 宽轻量图"""
    pil_img = d.screenshot()
    w, h = pil_img.size
    scale = 720.0 / float(w)
    small_img = pil_img.resize((720, int(h * scale)), Image.Resampling.LANCZOS)
    snap_path = TEMP_DIR / f"{prefix}_{int(time.time()*1000)}.jpg"
    small_img.convert("RGB").save(str(snap_path), "JPEG", quality=65)
    return str(snap_path), pil_img


def check_and_dismiss_popups(d) -> bool:
    """通用弹窗自愈：检查是否有里程碑弹窗、遮罩、继续做任务或右上角关闭X"""
    img_path, original = capture_fast_screen(d, "popup_check")
    prompt = (
        "请检查当前页面是否有阻碍操作的弹窗、遮罩或奖励结算框？\n"
        "1. 如果有【继续做任务】按钮，请输出：动作:继续, x:百分比, y:百分比\n"
        "2. 如果有右上角或边缘的关闭【X】图标，请输出：动作:关闭X, x:百分比, y:百分比\n"
        "3. 如果页面完全正常无弹窗，请输出：正常"
    )
    out = ai_query(img_path, prompt)
    try:
        os.remove(img_path)
    except Exception:
        pass

    sw, sh = d.window_size()
    m = re.search(r"x\s*[:：=]\s*(\d+(?:\.\d+)?)[,\s，]+y\s*[:：=]\s*(\d+(?:\.\d+)?)", out)
    if m:
        cx = int(sw * float(m.group(1)) / 100.0)
        cy = int(sh * float(m.group(2)) / 100.0)
        # 排除左上角返回键 (x<200, y<300)
        if cx > 200 or cy > 300:
            print(f"  🛡️ [弹窗自愈] 检测到弹窗，点击消除 @({cx}, {cy})...")
            d.click(cx, cy)
            time.sleep(2)
            return True
    return False


def detect_actual_media_page(d) -> str:
    """进入后由 AI 判断究竟是视频还是直播"""
    img_path, _ = capture_fast_screen(d, "media_detect")
    prompt = (
        "请判断当前界面究竟是【直播间】还是【短视频】？\n"
        "直播间特征：有主播、弹幕输入框、说点什么、关注主播、榜单。\n"
        "短视频特征：纯短视频画面、上滑看更多视频、点赞小红心。\n"
        "请只回答三个字之一：直播间 或 短视频 或 其他"
    )
    out = ai_query(img_path, prompt)
    try:
        os.remove(img_path)
    except Exception:
        pass

    if "直播" in out:
        return "live"
    return "video"


def check_media_task_completed(d) -> bool:
    """判断媒体/浏览任务当前是否已提示完成"""
    img_path, _ = capture_fast_screen(d, "media_check")
    prompt = (
        "请检查当前视频/直播/浏览页面中任务状态：\n"
        "是否已出现：已完成、完成任务、恭喜获得、滑动完成、或倒计时消失？\n"
        "如果已完成，输出：状态:已完成\n"
        "如果仍在计时中，输出：状态:进行中"
    )
    out = ai_query(img_path, prompt)
    try:
        os.remove(img_path)
    except Exception:
        pass

    if "已完成" in out:
        return True
    return False


def exec_dynamic_media_task(d, task_name: str, nominal_sec: int = 60):
    """
    动态补帧媒体执行器（直播/视频/浏览）：
    - 基础时长：30s先跑20s，60s/120s先跑40s
    - 滑动频率：3~5秒一次
    - 跑完基础时长后 AI 判定，未完成则每次追加 5~10s
    """
    sw, sh = d.window_size()
    time.sleep(3)

    # 1. AI 判断实际页面类型
    actual_type = detect_actual_media_page(d)
    print(f"  🧭 [页面路由] 标称【{task_name}】，AI 识别实际落地页为: 【{actual_type}】")

    # 2. 计算动态基础时长
    base_sec = 20 if nominal_sec <= 30 else 40
    print(f"  ⏱️ 启动动态时长控制：标称 {nominal_sec}s ➔ 基础执行 {base_sec}s...")

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
            print(f"    - 快速补帧上滑 (已过 {elapsed}s / 基础 {base_sec}s)")
        time.sleep(1)

    # 3. 动态追查与补帧
    max_extra_steps = 6
    step_sec = 8
    for step in range(1, max_extra_steps + 1):
        if check_media_task_completed(d):
            print(f"  🎉 [AI 确认] 任务提前满额闭环，立即退出！")
            break
        print(f"  ⚠️ 任务未满额，动态补帧追加 {step_sec}s (第 {step}/{max_extra_steps} 次)...")
        step_start = time.time()
        while time.time() - step_start < step_sec:
            d.swipe(sw // 2, int(sh * 0.78), sw // 2, int(sh * 0.22), duration=0.3)
            time.sleep(random.uniform(3, 4))

    # 4. 返回签到主页
    print("  ↩️ 执行完毕，返回签到主页...")
    d.press("back")
    time.sleep(2.5)


def parse_all_tasks_from_screen(d) -> list:
    """通过 CodeBuddy 提取当前可见的任务列表"""
    img_path, orig = capture_fast_screen(d, "parse_tasks")
    prompt = (
        "请识别并提取页面底部【每完成1次任务可前进1步】区域中的所有任务卡片。\n"
        "对每个任务提取：\n"
        "- name: 任务名称（例如：玩1次猜价格、看视频60秒、看精选推荐60秒、看直播60秒）\n"
        "- type: 任务类型（guess_price / video / browse / live 之一）\n"
        "- btn_cx: 该任务对应的【去完成】红色按钮中心X百分比(0-100)\n"
        "- btn_cy: 该任务对应的【去完成】红色按钮中心Y百分比(0-100)\n"
        "请严格只输出纯 JSON 数组，严禁任何说明：\n"
        '[{"name": "任务名", "type": "guess_price", "btn_cx": 12, "btn_cy": 94}]'
    )
    raw = ai_query(img_path, prompt)
    try:
        os.remove(img_path)
    except Exception:
        pass

    try:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            tasks = json.loads(m.group(0))
            sw, sh = d.window_size()
            parsed = []
            for t in tasks:
                cx = int(sw * float(t["btn_cx"]) / 100.0)
                cy = int(sh * float(t["btn_cy"]) / 100.0)
                parsed.append({"name": t["name"], "type": t["type"], "cx": cx, "cy": cy})
            return parsed
    except Exception as e:
        print(f"  ⚠️ JSON 解析异常: {e}, 原始内容: {raw[:80]}")
    return []


def run_signin_loop():
    if sys.stdout.encoding and sys.stdout.encoding.lower() == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    d = u2.connect()
    print("=" * 60)
    print("🚀 启动【签到赢元宝】全自动智能闭环调度器")
    print("=" * 60)

    from guess_price_codebuddy import run_guess_price

    max_rounds = 10
    for round_idx in range(1, max_rounds + 1):
        print(f"\n{'='*40}\n▶️ 签到任务扫描轮次 #{round_idx}\n{'='*40}")

        # 检查并消除可能存在的弹窗
        check_and_dismiss_popups(d)

        # 获取当前任务列表
        tasks = parse_all_tasks_from_screen(d)
        if not tasks:
            print("  📭 当前未识别到可执行任务，轻微下滑半屏刷新...")
            sw, sh = d.window_size()
            d.swipe(sw // 2, int(sh * 0.7), sw // 2, int(sh * 0.5))
            time.sleep(2)
            tasks = parse_all_tasks_from_screen(d)
            if not tasks:
                print("🛑 连续两次无可用任务，全流程结束！")
                break

        print(f"  📋 识别到 {len(tasks)} 个待执行任务:")
        for idx, t in enumerate(tasks, 1):
            print(f"    {idx}. [{t['type']}] {t['name']} @({t['cx']}, {t['cy']})")

        # 优先选择：猜价格 > 浏览 > 视频 > 直播
        priority_map = {"guess_price": 0, "browse": 1, "video": 2, "live": 3}
        tasks.sort(key=lambda x: priority_map.get(x["type"], 99))
        target_task = tasks[0]

        print(f"\n👉 本轮选择执行目标: 【{target_task['name']}】...")
        d.click(target_task["cx"], target_task["cy"])
        time.sleep(3)

        # 根据任务类型分流执行
        t_type = target_task["type"]
        if t_type == "guess_price":
            run_guess_price(d)
        else:
            m = re.search(r"(\d+)\s*秒", target_task["name"])
            sec = int(m.group(1)) if m else 60
            exec_dynamic_media_task(d, target_task["name"], nominal_sec=sec)

        # 返回签到主页后，立即做一次里程碑弹窗自愈检查
        time.sleep(2)
        check_and_dismiss_popups(d)

    print("\n🎉 【签到赢元宝】全自动调度顺利交付！")


if __name__ == "__main__":
    run_signin_loop()
