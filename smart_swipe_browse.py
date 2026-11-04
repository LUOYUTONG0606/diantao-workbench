#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smart_swipe_browse.py - 智能闭环下滑：基础时长 + 10s 步进状态追查
==================================================================
1. 执行 base_seconds (默认 180s) 基础下滑浏览
2. 基础滑动完成后，截屏通过 CodeBuddy 多模态判定任务是否完成
3. 若未完成，步进追加 step_seconds (默认 10s) 并复查，直至确认完成或达到最大安全上限
"""

import os
import sys
import time
import random
import subprocess
from pathlib import Path
from PIL import Image
import uiautomator2 as u2

CODEBUDDY_CLI = r"D:\WORKBUDDY\resources\app.asar.unpacked\cli\dist\codebuddy.js"
VISION_MODEL = "deepseek-v4.1-flash"
TEMP_DIR = (Path(__file__).resolve().parent / ".cb_vision_temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)


def do_swipe(d, sw: int, sh: int, count: int = 1):
    """执行一次或多次随机平滑下滑"""
    for _ in range(count):
        sx = int(sw * random.uniform(0.45, 0.55))
        sy = int(sh * random.uniform(0.80, 0.88))
        ex = int(sw * random.uniform(0.45, 0.55))
        ey = int(sh * random.uniform(0.18, 0.28))
        d.swipe(sx, sy, ex, ey, duration=random.uniform(0.25, 0.35))
        time.sleep(random.uniform(2.5, 3.8))


def check_task_completion(d, task_type: str = "signin") -> bool:
    """通过 CodeBuddy 判定当前任务是否已经完成"""
    pil_img = d.screenshot()
    w, h = pil_img.size

    # 压缩为 720 宽便于极速识别
    scale = 720.0 / float(w)
    small_img = pil_img.resize((720, int(h * scale)), Image.Resampling.LANCZOS)
    snap_path = TEMP_DIR / f"check_completion_{int(time.time()*1000)}.jpg"
    small_img.convert("RGB").save(str(snap_path), "JPEG", quality=65)

    prompt = (
        f"请使用Read工具读取图片：{snap_path}。\n"
        f"请检查页面中浏览任务（如【滑动浏览30秒】或底部进度）：\n"
        f"1. 是否已经完成？例如显示为：已完成、6/6、今日已领、奖励已到账、或进度条已满。\n"
        f"2. 还是仍在进行中？例如显示为：(0/6)、(1/6)、(2/6)、(3/6)、(4/6)、(5/6)、还剩XX秒。\n"
        f"请只输出以下两种结果之一，严禁多余废话：\n"
        f"状态:已完成 或 状态:进行中"
    )

    cmd = [
        "node",
        CODEBUDDY_CLI,
        "-y",
        "--strict-mcp-config",
        "--mcp-config", "{}",
        "--model", VISION_MODEL,
        "--tools", "Read",
        "-p", prompt
    ]

    try:
        t0 = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
        cost = time.time() - t0
        out = res.stdout.strip()
        print(f"  🧠 [AI 完成度判断] 识别结果: {out[:50]} (耗时: {cost:.2f}s)")

        try:
            os.remove(snap_path)
        except Exception:
            pass

        if "已完成" in out or "6/6" in out:
            return True
        return False
    except Exception as e:
        print(f"  ⚠️ [AI 完成度判断] 调用异常: {e}")
        return False


def run_smart_browse(base_seconds: int = 180, step_seconds: int = 10, max_steps: int = 6):
    """
    智能滑动总控：基础时长 + 10s 步进追查
    """
    if sys.stdout.encoding and sys.stdout.encoding.lower() == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    d = u2.connect()
    sw, sh = d.window_size()
    print("=" * 60)
    print(f"🚀 开始执行【签到赢元宝】智能闭环下滑浏览")
    print(f"   - 基础滑动时长: {base_seconds}s")
    print(f"   - 冗余步进时长: 每次 +{step_seconds}s (最多追加 {max_steps} 次)")
    print("=" * 60)

    # 1. 基础时长滑动
    print(f"\n📋 [阶段一] 开始 {base_seconds}s 基础滑动浏览...")
    start_t = time.time()
    count = 0
    while time.time() - start_t < base_seconds:
        elapsed = int(time.time() - start_t)
        remain = base_seconds - elapsed
        do_swipe(d, sw, sh, count=1)
        count += 1
        print(f"  - 基础滑动 #{count} (已进行 {elapsed}s / 剩余 {remain}s)")

    print(f"✅ 基础 {base_seconds}s 滑动完毕，共滑动 {count} 次。")

    # 2. 状态检查与步进追加循环
    print(f"\n🔍 [阶段二] 启动 AI 动态步进追查机制...")
    for step in range(1, max_steps + 1):
        print(f"\n👉 检查点 #{step}: 正在通过 AI 研判任务是否已真正完成...")
        if check_task_completion(d):
            print(f"🎉🎉 [AI 确认] 签到浏览任务已百分之百达成奖励，提前闭环完成！")
            return True

        print(f"⚠️ [尚未满额] 步进追加滑动 {step_seconds}s (第 {step}/{max_steps} 次追加)...")
        step_start = time.time()
        while time.time() - step_start < step_seconds:
            do_swipe(d, sw, sh, count=1)

    print(f"ℹ️ 已达到最大冗余上限 ({max_steps} 次)，任务收尾退出。")
    return True


if __name__ == "__main__":
    run_smart_browse(base_seconds=180, step_seconds=10, max_steps=6)
