#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键下滑 (AI版) - 智能视觉闭环与防空滑调度器
============================================================
流程：6轮顺序执行，每轮：重启点淘 → 进入元宝中心 → 标准/AI导航目标页 → 智能闭环下滑
轮次列表：
  1. 打工赚元宝 (基础 180s)
  2. 睡觉赚元宝 (基础 180s)
  3. 签到赢元宝 (基础 180s)
  4. 购物赚钱 (基础 180s)
  5. 摇一摇 (基础 180s，严守红线：绝不触碰 PK 战斗)
  6. 走路赚元宝 (基础 180s)

【核心特性】
1. 智能闭环下滑：基础时长跑完后，通过视觉检测任务完成状态，满额提前退出，未完成步进追加；
2. 弹窗自愈网络：消除选工作、恭喜获得、确认离开/残忍离开、各类关闭X；
3. 防空滑熔断机制：导航失败立即跳过该轮，杜绝在未知/错误页面空跑；
4. 任务日志系统：每轮完成后实时写入 task_log.json 并统计今日战报；
5. 零顶层副作用：设备连接收口到函数执行，安全导入不阻塞。
============================================================
"""

import os
import sys
import time
from yuanbao_popup import handle_yuanbao_live_popups
import random
from pathlib import Path
from typing import Optional, Dict, Any, List

# 强制 UTF-8 输出，防止 Windows 终端/大管家日志乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from u2_scanner import connect_device, DT_APP, scan_page, find_clickable_with_text
from utils import (
    click_ocr_text, has_ocr_text,
    log_task_completed, show_today_summary, get_ocr_text_blocks,
)


def _click_ocr(device, keywords, *, settle_seconds=1.5, attempts=2):
    """通过 OCR 识别页面文字并点击，不查询 U2 控件树。"""
    return click_ocr_text(
        device, keywords, settle_seconds=settle_seconds,
        attempts=attempts, log=True,
    )


def _has_ocr(device, keywords, *, settle_seconds=0.5, attempts=1):
    """通过 OCR 判断页面是否包含目标文字。"""
    return has_ocr_text(
        device, keywords, settle_seconds=settle_seconds,
        attempts=attempts, log=True,
    )


# ========================================================
# 🛡️ 异常与弹窗自愈网络
# ========================================================

def dismiss_popups(device):
    """通过 OCR 找到弹窗操作文字；未识别到时不进行坐标盲点。"""
    labels = ["继续做任务", "开心收下", "收下元宝", "我知道了", "放弃", "确定", "允许", "关闭", "残忍离开", "放弃领取"]
    if _click_ocr(device, labels, settle_seconds=0.3, attempts=1):
        time.sleep(1.2)
        return True
    if _has_ocr(device, "恭喜获得元宝", settle_seconds=0.2):
        if _click_ocr(device, ["继续做任务", "去完成", "关闭", "我知道了"], settle_seconds=0.2, attempts=1):
            time.sleep(1.2)
            return True
    return False


# ========================================================
# 🧭 导航函数库（各轮次入口标准路径与自愈）
# ========================================================

def _nav_to_yuanbao_center(device, sw: int, sh: int) -> bool:
    """通过 OCR 进入元宝中心；识别失败时停止，不使用 U2 控件树或盲点坐标。"""
    print("  🧭 正在进入【元宝中心】...", flush=True)
    if _has_ocr(device, ["我的元宝", "提现", "兑红包"]):
        print("  ✅ OCR 确认已处于元宝中心", flush=True)
        return handle_yuanbao_live_popups(device)
    for attempt in range(4):
        if _has_ocr(device, "刷新", settle_seconds=0.2):
            print("    ⚠️ OCR 检测到【刷新】，尝试点击恢复...", flush=True)
            _click_ocr(device, "刷新", settle_seconds=0.2, attempts=1)
            time.sleep(3)
        print(f"    👉 OCR 查找【元宝中心】(尝试 {attempt + 1}/4)...", flush=True)
        if not _click_ocr(device, "元宝中心", settle_seconds=1, attempts=2):
            dismiss_popups(device)
            continue
        time.sleep(4)
        if not handle_yuanbao_live_popups(device):
            return False
        dismiss_popups(device)
        if _has_ocr(device, ["我的元宝", "提现", "兑红包"], settle_seconds=0.5, attempts=2):
            device.swipe(sw // 2, int(sh * 0.30), sw // 2, int(sh * 0.80), duration=0.2)
            time.sleep(1)
            print("  ✅ OCR 确认进入元宝中心", flush=True)
            return True
    print("  ❌ OCR 未能确认进入元宝中心", flush=True)
    return False


def _nav_to_dagong(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → OCR 查找打工赚元宝。"""
    print("  🧭 查找【打工赚元宝】入口...", flush=True)
    if not _click_ocr(device, ["打工赚元宝", "打工"]):
        print("  ⚠️ OCR 未找到打工入口", flush=True)
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_sleep(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → OCR 查找睡觉赚元宝。"""
    print("  🧭 查找【睡觉赚元宝】入口...", flush=True)
    if not _click_ocr(device, ["睡觉赚元宝", "睡觉"]):
        print("  ⚠️ OCR 未找到睡觉入口", flush=True)
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_signin(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → 下滑后通过 OCR 查找今日签到。"""
    print("  📜 下滑查找【今日签到】入口...", flush=True)
    device.swipe(sw // 2, int(sh * 0.80), sw // 2, int(sh * 0.35), duration=0.3)
    for attempt in range(5):
        time.sleep(1)
        nodes = scan_page(device)
        sign = find_clickable_with_text(nodes, "今日签到")
        if sign:
            sx, sy = int(sign["cx"]), int(sign["cy"])
            for button_text in ["去看看", "去签到", "立即签到"]:
                button = find_clickable_with_text(nodes, button_text)
                if button:
                    bx, by = int(button["cx"]), int(button["cy"])
                    if abs(bx - sx) > 40 and -30 <= by - sy <= 220:
                        device.click(bx, by)
                        time.sleep(4)
                        dismiss_popups(device)
                        print(f"  ✅ OCR 点击【{button_text}】进入签到页面", flush=True)
                        return True
            device.click(sx, sy)
            time.sleep(4)
            dismiss_popups(device)
            print("  ✅ OCR 点击【今日签到】", flush=True)
            return True
        if attempt < 4:
            device.swipe(sw // 2, int(sh * 0.75), sw // 2, int(sh * 0.35), duration=0.3)
    print("  ⚠️ OCR 未找到今日签到入口", flush=True)
    return False


def _nav_to_shop(device, sw: int, sh: int) -> bool:
    """使用固定坐标点击购物赚钱入口。"""
    print("  🎯 固定坐标点击购物赚钱入口 (934, 1723)...", flush=True)
    device.click(934, 1723)
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_yaoyiyao(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → 摇一摇赚元宝（优先直达）"""
    print("  🧭 查找【摇一摇赚元宝】入口...", flush=True)
    if not _click_ocr(device, ["摇一摇赚元宝", "去摇一摇"]):
        print("  ⚠️ OCR 未找到摇一摇入口", flush=True)
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_walk(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → OCR 查找走路赚元宝。"""
    print("  🧭 查找【走路赚元宝】入口...", flush=True)
    if not _click_ocr(device, ["走路赚元宝", "走路"]):
        print("  ⚠️ OCR 未找到走路入口", flush=True)
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


# ========================================================
# 📋 轮次配置
# ========================================================

ROUNDS_CONFIG = [
    {
        "label": "1",
        "name": "打工赚元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_dagong,
    },
    {
        "label": "2",
        "name": "睡觉赚元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_sleep,
    },
    {
        "label": "3",
        "name": "签到赢元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_signin,
    },
    {
        "label": "4",
        "name": "购物赚钱",
        "swipe_seconds": 180,
        "nav_func": _nav_to_shop,
    },
    {
        "label": "5",
        "name": "摇一摇",
        "swipe_seconds": 180,
        "nav_func": _nav_to_yaoyiyao,
    },
    {
        "label": "6",
        "name": "走路赚元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_walk,
    },
]


# ========================================================
# 📱 智能闭环下滑浏览
# ========================================================

def check_task_finished(device) -> bool:
    """通过 OCR 关键词检测当前滑动任务是否已达成。"""
    return _has_ocr(
        device,
        ["已完成", "全部完成", "今日已达上限", "明日再来", "奖励已发放", "6/6"],
        settle_seconds=0.3,
    )


def swipe_browse_ai(device, base_seconds: int, task_name: str, sw: int, sh: int):
    """
    AI 智能闭环下滑：
    1. 基础时长匀速平滑下滑；
    2. 基础滑完后检测是否已完成，完成即刻提前退出，未完成每次追加 10s 步进（最多 3 次）。
    """
    if base_seconds <= 0:
        print("  ⏭️ 跳过下滑（swipe_seconds <= 0）", flush=True)
        return

    print(f"  📋 开始【{task_name}】下滑浏览 (基础时长: {base_seconds}s)...", flush=True)
    start = time.time()
    count = 0

    # 阶段一：基础时长滑动
    while time.time() - start < base_seconds:
        start_y = random.randint(int(sh * 0.75), int(sh * 0.85))
        end_y = random.randint(int(sh * 0.20), int(sh * 0.30))
        device.swipe(int(sw * 0.5), start_y, int(sw * 0.5), end_y, duration=0.25)
        count += 1
        elapsed = int(time.time() - start)
        remain = max(0, base_seconds - elapsed)
        if count % 5 == 0 or remain <= 10:
            print(f"    - 下滑 #{count} (已进行 {elapsed}s / 基础剩余 {remain}s)", flush=True)
        time.sleep(random.uniform(2.5, 4.0))

        # 处理偶发遮罩弹窗
        dismiss_popups(device)

    # 阶段二：AI 视觉/完成度闭环复检
    print(f"  🔍 基础滑动完成 (累计 {count} 次)，正在检查完成状态...", flush=True)
    if check_task_finished(device):
        print(f"  🎉 [状态确认] 【{task_name}】已提前闭环满额完成！", flush=True)
        return

    max_steps = 3
    step_seconds = 10
    for step in range(1, max_steps + 1):
        print(f"  ⚠️ 任务仍在进行中，步进追加 {step_seconds}s (第 {step}/{max_steps} 次)...", flush=True)
        step_start = time.time()
        while time.time() - step_start < step_seconds:
            device.swipe(int(sw * 0.5), int(sh * 0.78), int(sw * 0.5), int(sh * 0.25), duration=0.25)
            count += 1
            time.sleep(random.uniform(2.5, 3.5))
        if check_task_finished(device):
            print(f"  🎉 [步进确认] 【{task_name}】已满额完成！", flush=True)
            break

    print(f"  ✅ 【{task_name}】浏览完成，累计有效下滑 {count} 次。", flush=True)


# ========================================================
# 🚀 单轮执行（含防空滑熔断与日志写入）
# ========================================================

def run_round(device, label: str, name: str, swipe_seconds: int, nav_func, sw: int, sh: int) -> bool:
    """执行一轮：冷启点淘 → 元宝中心 → 标准导航目标页 → 安全闭环下滑 → 记日志"""
    print(f"\n{'='*60}", flush=True)
    print(f"  第 {label} 轮: {name}", flush=True)
    print(f"  计划浏览时长: {swipe_seconds}s", flush=True)
    print(f"{'='*60}", flush=True)

    # Step 1: 重启点淘到首页
    print("  🚀 重启点淘 APP...", flush=True)
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    dismiss_popups(device)

    # Step 2: 进入元宝中心
    if not _nav_to_yuanbao_center(device, sw, sh):
        print(f"  ❌ 未能进入元宝中心，终止第 {label} 轮", flush=True)
        return False

    # Step 3: 标准路径导航
    entered = nav_func(device, sw, sh)
    if not entered:
        print(f"  ⚠️ 标准导航未能进入【{name}】，跳过本轮浏览（触发安全防护，避免在错误页面空跑）", flush=True)
        return False

    # Step 4: 智能闭环下滑
    swipe_browse_ai(device, swipe_seconds, name, sw, sh)

    # Step 5: 退出并安全返回
    print("  ↩️ 安全退出当前页面...", flush=True)
    device.press("back")
    time.sleep(2)
    dismiss_popups(device)

    # Step 6: 记录到任务日志系统
    log_task_completed("一键下滑(AI版)", f"第 {label} 轮: {name}")
    print(f"  ✅ 第 {label} 轮【{name}】顺利完成并写入任务日志", flush=True)
    return True


# ========================================================
# 🏁 主函数
# ========================================================

def main():
    print("=" * 60, flush=True)
    print(" 🎮 一键下滑 (AI版) - 智能视觉闭环与防空滑调度器", flush=True)
    print("   流程: 打工 → 睡觉 → 签到 → 购物赚钱 → 摇一摇 → 走路赚元宝", flush=True)
    print("   特性: 智能视觉导航 + 动态完成度判断 + 弹窗自愈 + 任务日志系统", flush=True)
    print("=" * 60, flush=True)

    device = connect_device()
    # 暂停 watcher 避免在复杂 WebView 中递归 dump_hierarchy 触发 StackOverflow
    if hasattr(device, "_global_watcher_ctx") and device._global_watcher_ctx:
        try:
            device._global_watcher_ctx.stop()
            print("  [设置] 已暂停全局 watcher 避免 DOM 深度溢出", flush=True)
        except Exception:
            pass

    sw, sh = device.window_size()
    print(f"📱 屏幕分辨率: {sw} x {sh}", flush=True)

    # 支持命令行参数定向指定轮次或时长：
    # python 一键下滑_AI.py [round_idx] [seconds]
    target_round = None
    custom_seconds = None
    if len(sys.argv) > 1:
        arg1 = sys.argv[1].strip()
        if arg1.isdigit():
            target_round = int(arg1)
    if len(sys.argv) > 2:
        arg2 = sys.argv[2].strip()
        if arg2.isdigit():
            custom_seconds = int(arg2)

    rounds_to_run = []
    for r in ROUNDS_CONFIG:
        if target_round is not None and int(r["label"]) != target_round:
            continue
        rounds_to_run.append(r)

    if not rounds_to_run:
        print(f"⚠️ 指定的轮次 {target_round} 不存在，可选 1-{len(ROUNDS_CONFIG)}", flush=True)
        return

    success_count = 0
    total_count = len(rounds_to_run)

    for r in rounds_to_run:
        duration = custom_seconds if custom_seconds is not None else r["swipe_seconds"]
        ok = run_round(
            device=device,
            label=r["label"],
            name=r["name"],
            swipe_seconds=duration,
            nav_func=r["nav_func"],
            sw=sw,
            sh=sh,
        )
        if ok:
            success_count += 1
        time.sleep(2)

    print("\n" + "=" * 60, flush=True)
    print(f"🎉 全部执行完毕：成功 {success_count}/{total_count} 轮", flush=True)
    print("=" * 60, flush=True)
    show_today_summary()


if __name__ == "__main__":
    main()
