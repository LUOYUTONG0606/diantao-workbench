#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赚钱卡.py - 点淘赚钱卡日常自动化任务 (CodeBuddy 多模态全能改造版)
===================================================================
入口路径：首页 → 元宝中心 → 下滑 → 赚钱卡
活动内容：
  1. 直播任务 (直播间自动观看、元宝倒计时、CodeBuddy 语义定位返回)
  2. 浏览任务 (滑动浏览商品/活动、智能倒计时检测、自动收尾)
  3. 猜价格 (DeepSeek 多模态视觉推理价格、智能二分出价、自动领奖)
"""

import os
import re
import sys
import time
from yuanbao_popup import handle_yuanbao_live_popups
import random
from typing import Optional

from codebuddy_vision import vision_engine, MODEL_FLASH, MODEL_DEEPSEEK
from u2_scanner import (
    connect_device,
    DT_APP,
    click_u2_control,
    execute_browse_task,
    execute_live_task,
    handle_open_allow_popup,
)
from utils import (
    get_ocr_text_blocks,
    find_text_by_paddleocr,
    find_ui_element,
    classify_current_page,
    log_task_completed,
)

# 连接设备
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"📱 设备连接成功，分辨率: {screen_width} x {screen_height}")


def navigate_to_money_card() -> bool:
    """
    导航流程：启动点淘 → 进元宝中心 → 下滑找到“赚钱卡”入口点击
    """
    print("\n🚀 [赚钱卡] 正在启动点淘并导航到元宝中心...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    # 1. 点击底部/主页“元宝中心”
    found_yuanbao = False
    for _ in range(8):
        for text in ["元宝中心", "赚元宝"]:
            btn = d(textContains=text)
            if btn.exists(timeout=2):
                click_u2_control(d, btn, text)
                time.sleep(3)
                found_yuanbao = True
                break
        if found_yuanbao:
            break
        time.sleep(1)

    if not found_yuanbao:
        # 尝试通过 CodeBuddy 语义定位元宝中心图标
        pos = find_ui_element(d, "底部导航栏的元宝中心或赚元宝")
        if pos:
            d.click(pos[0], pos[1])
            time.sleep(3)
        else:
            # 屏幕底部经验点击
            d.click(int(screen_width * 0.7), int(screen_height * 0.93))
            time.sleep(3)

    if not handle_yuanbao_live_popups(d):
        return False

    handle_open_allow_popup(d)

    # 2. 下滑寻找“赚钱卡”入口
    print("🔍 [赚钱卡] 正在下滑寻找【赚钱卡】入口...")
    for swipe_i in range(10):
        # 先用 U2 文字检测
        card_btn = d(textContains="赚钱卡")
        if card_btn.exists(timeout=1):
            print("  🎯 找到【赚钱卡】入口，正在进入...")
            click_u2_control(d, card_btn, "赚钱卡")
            time.sleep(4)
            return True

        # 用 CodeBuddy 多模态视觉检索入口
        pos = find_ui_element(d, "赚钱卡")
        if pos:
            print(f"  🎯 CodeBuddy 视觉找到【赚钱卡】入口: {pos}")
            d.click(pos[0], pos[1])
            time.sleep(4)
            return True

        # 下滑翻页
        d.swipe(
            int(screen_width * 0.5),
            int(screen_height * 0.7),
            int(screen_width * 0.5),
            int(screen_height * 0.3),
            duration=0.3,
        )
        time.sleep(1.5)

    print("  ⚠️ 未在元宝中心页面找到【赚钱卡】入口，尝试在当前页面继续扫描活动...")
    return False


def run_live_subtask() -> bool:
    """
    赚钱卡 - 直播活动子任务：
    进入直播间，停留指定时长并由 CodeBuddy 视觉识别关闭按钮返回
    """
    print("\n📺 [活动1: 直播任务] 开始检索并执行直播任务...")
    target_pos = find_ui_element(d, "看直播或去观看或去直播按钮")
    if not target_pos:
        # 从文字块寻找
        blocks = get_ocr_text_blocks(d)
        for b in blocks:
            if any(k in b["text"] for k in ["直播", "观看直播", "看直播"]):
                target_pos = (b["cx"], b["cy"])
                break

    if target_pos:
        print(f"  👉 点击直播任务入口: {target_pos}")
        d.click(target_pos[0], target_pos[1])
        time.sleep(4)
        handle_open_allow_popup(d)

        # 观看直播 60 秒
        print("  ⏳ 正在观看直播中 (约60秒)...")
        execute_live_task(d, "赚钱卡直播任务", target_pos[0], target_pos[1], duration=60)
        time.sleep(2)

        # 确保安全返回
        close_pos = find_ui_element(d, "直播间右上角关闭X或左上角返回箭头")
        if close_pos:
            d.click(close_pos[0], close_pos[1])
        else:
            d.press("back")
        time.sleep(2)
        log_task_completed("赚钱卡", "直播任务")
        return True

    print("  ℹ️ 今日直播任务未出现或已全部完成。")
    return False


def run_browse_subtask() -> bool:
    """
    赚钱卡 - 浏览活动子任务：
    进入浏览商品/活动，自动滑动浏览并检测完成
    """
    print("\n🛍️ [活动2: 浏览任务] 开始检索并执行浏览任务...")
    target_pos = find_ui_element(d, "去浏览或滑动浏览按钮")
    if not target_pos:
        blocks = get_ocr_text_blocks(d)
        for b in blocks:
            if "浏览" in b["text"] and any(k in b["text"] for k in ["去", "完成"]):
                target_pos = (b["cx"], b["cy"])
                break

    if target_pos:
        print(f"  👉 点击浏览任务入口: {target_pos}")
        d.click(target_pos[0], target_pos[1])
        time.sleep(3)
        handle_open_allow_popup(d)

        # 启动智能滑动浏览
        print("  📜 开始智能滑动浏览...")
        execute_browse_task(d, min_duration=30, max_wait=70)
        time.sleep(2)

        # 返回赚钱卡
        d.press("back")
        time.sleep(2)
        log_task_completed("赚钱卡", "浏览任务")
        return True

    print("  ℹ️ 今日浏览任务未出现或已全部完成。")
    return False


def run_guess_price_subtask() -> bool:
    """
    赚钱卡 - 猜价格活动子任务：
    结合 CodeBuddy (DeepSeek/Flash) 多模态推理，智能猜测价格并领奖
    """
    print("\n🏷️ [活动3: 猜价格任务] 开始检索猜价格入口...")
    pos = find_ui_element(d, "猜价格或去猜价按钮")
    if not pos:
        blocks = get_ocr_text_blocks(d)
        for b in blocks:
            if "猜价格" in b["text"] or "猜价" in b["text"]:
                pos = (b["cx"], b["cy"])
                break

    if not pos:
        print("  ℹ️ 当前页面未发现猜价格入口。")
        return False

    print(f"  👉 进入猜价格游戏: {pos}")
    d.click(pos[0], pos[1])
    time.sleep(4)
    handle_open_allow_popup(d)

    # 循环进行出价，最多尝试 5 轮
    low_bound = 1
    high_bound = 999
    current_guess = 50

    for round_idx in range(1, 6):
        print(f"\n  🎯 [猜价格 第 {round_idx} 轮] 正在通过 CodeBuddy 多模态分析当前画面...")
        img = d.screenshot()
        analysis = vision_engine.analyze_guess_price(img, model=MODEL_DEEPSEEK)

        status = analysis.get("status", "bidding")
        product_name = analysis.get("product_name", "未知商品")
        suggested = analysis.get("suggested_price")

        print(f"    商品名称: 【{product_name}】，当前状态: {status}")

        if status in ["won", "ended"]:
            print("    🎉 猜价格已成功或已结束！")
            next_btn = analysis.get("next_btn_pos")
            if next_btn:
                d.click(next_btn[0], next_btn[1])
            break

        if status == "too_high":
            high_bound = current_guess - 1
            print(f"    📈 提示价格偏高，调整上限至 {high_bound}")
        elif status == "too_low":
            low_bound = current_guess + 1
            print(f"    📉 提示价格偏低，调整下限至 {low_bound}")

        # 计算新的出价
        if suggested and low_bound <= suggested <= high_bound:
            current_guess = int(suggested)
        else:
            current_guess = (low_bound + high_bound) // 2

        print(f"    💡 推荐本轮出价: {current_guess} 元")

        # 尝试寻找数字键盘或输入框输入
        input_pos = analysis.get("input_box_pos") or find_ui_element(d, "出价输入框")
        if input_pos:
            d.click(input_pos[0], input_pos[1])
            time.sleep(0.5)

        # 输入数字 (通过 adb input text)
        os.system(f"adb shell input text {current_guess}")
        time.sleep(1)

        # 点击确认出价
        confirm_pos = analysis.get("confirm_btn_pos") or find_ui_element(d, "确认出价或确定按钮")
        if confirm_pos:
            d.click(confirm_pos[0], confirm_pos[1])
            print(f"    👆 已确认出价: {current_guess}")
        else:
            d.click(int(screen_width * 0.5), int(screen_height * 0.8))
        time.sleep(3)

    # 退出猜价格回到赚钱卡
    back_pos = find_ui_element(d, "左上角返回箭头或关闭按钮")
    if back_pos:
        d.click(back_pos[0], back_pos[1])
    else:
        d.press("back")
    time.sleep(2)
    log_task_completed("赚钱卡", "猜价格任务")
    return True


def main():
    """赚钱卡全套任务执行入口"""
    print("==================================================")
    print("🎉 启动点淘【赚钱卡】CodeBuddy 多模态全能自动化")
    print("==================================================")

    nav_ok = navigate_to_money_card()
    if not nav_ok:
        print("  ⚠️ 元宝中心活动弹窗或赚钱卡导航未完成，停止任务执行")
        return

    # 依次执行三大任务
    run_live_subtask()
    time.sleep(2)

    run_browse_subtask()
    time.sleep(2)

    run_guess_price_subtask()
    time.sleep(2)

    print("\n==================================================")
    print("✅ 点淘【赚钱卡】全部自动化任务执行完成！")
    print("==================================================")


if __name__ == "__main__":
    main()
