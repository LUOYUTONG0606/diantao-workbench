#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_codebuddy_pipeline.py - 基于 SmartNavigator 的全流程执行
============================================================
1. 第一层：异常检测与素材库比对 (State Guard)
2. 第二层：正常流程快速 AI OCR 直击 (Fast Target Locator)
3. 严格执行：
   Step 1: 重启点淘 -> 进入元宝中心 -> 快速定位进入【打工赚元宝】 -> 浏览 240s
   Step 2: 重启点淘 -> 进入元宝中心 -> 快速定位进入【睡觉赚元宝】 -> 浏览 230s
   Step 3: 重启点淘 -> 进入元宝中心 -> 到达【今日签到】检查点，立刻停下并留存现场截图！
"""

import os
import sys
import time
import random
import uiautomator2 as u2
from PIL import Image
from smart_navigator import SmartNavigator, DT_APP, TEMP_DIR


def swipe_browse(d, seconds: int, task_name: str, sw: int, sh: int):
    """纯正向浏览下滑，绝不添加退出的截流逻辑"""
    print(f"\n  📋 开始【{task_name}】浏览下滑 (总时长: {seconds}s)...")
    start_t = time.time()
    count = 0
    while time.time() - start_t < seconds:
        elapsed = int(time.time() - start_t)
        remain = seconds - elapsed

        # 随机从屏幕下方向上滑动
        sx = int(sw * random.uniform(0.45, 0.55))
        sy = int(sh * random.uniform(0.80, 0.88))
        ex = int(sw * random.uniform(0.45, 0.55))
        ey = int(sh * random.uniform(0.18, 0.28))

        d.swipe(sx, sy, ex, ey, duration=random.uniform(0.25, 0.35))
        count += 1
        print(f"    - 下滑 #{count} (已进行 {elapsed}s / 剩余 {remain}s)")
        time.sleep(random.uniform(2.5, 4.0))

    print(f"  ✅ 【{task_name}】浏览完成，累计下滑 {count} 次。")


def run_pipeline():
    if sys.stdout.encoding and sys.stdout.encoding.lower() == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 60)
    print(" 🚀 CodeBuddy 智能视觉驱动 - 阶段一流水线正式启动")
    print(" 流程: 1.打工赚元宝(240s) -> 2.睡觉赚元宝(230s) -> 3.签到检查点停下")
    print("=" * 60)

    try:
        d = u2.connect()
        sw, sh = d.window_size()
        print(f"📱 成功连接设备: {d.serial} (屏幕分辨率: {sw}x{sh})")
    except Exception as e:
        print(f"❌ 设备连接失败: {e}")
        sys.exit(1)

    nav = SmartNavigator(d)

    # ========================================================
    # Step 1: 打工赚元宝
    # ========================================================
    print("\n" + "#" * 60)
    print("▶️ [Step 1] 开始执行【打工赚元宝】")
    print("#" * 60)

    print("  🔄 重启点淘 APP...")
    d.app_start(DT_APP, stop=True)
    time.sleep(6)

    # 1.1 进入元宝中心
    if not nav.enter_target("元宝中心"):
        print("🛑 在首页未能进入元宝中心，立即终止！")
        sys.exit(1)

    # 1.2 进入打工赚元宝
    if not nav.enter_target("打工赚元宝"):
        print("🛑 在元宝中心未能进入打工赚元宝，立即终止！")
        sys.exit(1)

    # 1.3 开始下滑浏览 180s
    swipe_browse(d, seconds=180, task_name="打工赚元宝", sw=sw, sh=sh)
    print("🎉 【打工赚元宝】圆满完成！")

    # ========================================================
    # Step 2: 睡觉赚元宝
    # ========================================================
    print("\n" + "#" * 60)
    print("▶️ [Step 2] 开始执行【睡觉赚元宝】")
    print("#" * 60)

    print("  🔄 重启点淘 APP...")
    d.app_start(DT_APP, stop=True)
    time.sleep(6)

    # 2.1 进入元宝中心
    if not nav.enter_target("元宝中心"):
        print("🛑 在首页未能进入元宝中心，立即终止！")
        sys.exit(1)

    # 2.2 进入睡觉赚元宝
    if not nav.enter_target("睡觉赚元宝"):
        print("🛑 在元宝中心未能进入睡觉赚元宝，立即终止！")
        sys.exit(1)

    # 2.3 开始下滑浏览 180s
    swipe_browse(d, seconds=180, task_name="睡觉赚元宝", sw=sw, sh=sh)
    print("🎉 【睡觉赚元宝】圆满完成！")

    # ========================================================
    # Step 3: 签到检查点
    # ========================================================
    print("\n" + "#" * 60)
    print("🏁 [Step 3] 达到【今日签到】检查点！")
    print("#" * 60)

    print("  🔄 重启点淘并进入元宝中心，准备呈现签到现场...")
    d.app_start(DT_APP, stop=True)
    time.sleep(6)

    if not nav.enter_target("元宝中心"):
        print("🛑 检查点未能进入元宝中心，立即终止！")
        sys.exit(1)

    checkpoint_img = TEMP_DIR / "signin_checkpoint.png"
    d.screenshot().save(str(checkpoint_img))
    print(f"\n📸 【今日签到】现场已保存至: {checkpoint_img}")
    print("🛑 任务已停下，等待我们一起进行下一步处理！")


if __name__ == "__main__":
    run_pipeline()
