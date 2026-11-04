"""
睡觉赚元宝 自动化脚本
流程：进点淘 → 睡觉赚元宝页面 → 扫描气泡任务 + 收集浮动气泡
"""
from task_exclusions import is_blocked_boutique_task
import time
import sys
from task_recheck import report_task_found, report_round_finished
import re
import random

import uiautomator2 as u2
from u2_scanner import (
    connect_device, DT_APP, navigate_to_sleep_page,
    click_u2_control,
    execute_browse_task, execute_video_task,
    execute_live_task,
    handle_guess_price_game, click_three_selected_items,
    scan_float_bubbles, click_float_bubbles,
    u2_find_task_buttons, is_stale_task_list, is_stale_no_buttons,
)
from utils import get_ocr_text_blocks, log_task_completed, show_today_summary
from utils import click_device_point

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

BROWSE_TIME = 65
STALE_LIMIT = 3

# 直播气泡白名单：主标题和时长均精确匹配，只忽略 OCR 空白/换行。
LIVE_TASK_WHITELIST = {
    "看李佳琦直播": (60,),
    "看香菇来了直播间": (60,),
    "看福利直播": (30,),
    "看烈儿宝贝直播间": (60,),
    "看蜜蜂惊喜社直播": (60,),
    "看直播": (30, 60, 120),
}

TASK_KW = [
    "猜价格", "去玩一次猜",
    "看视频",
    "精选推荐", "精选好物", "精选宝贝", "新好物", "省钱专区",
    "浏览", "去浏览", "查看",
]

# 用户要求屏蔽的任务关键词；命中后不进入候选，避免反复命中同一气泡。
BLOCKED_TASK_KEYWORDS = ("精选宝贝",)

CENTER_WAITING = ["后可开启", "熟睡中", "倒计时"]
CENTER_ACTIVE = ["开启晚睡", "开始睡觉", "收下元宝"]


def check_center_btn():
    """检测中央睡觉按钮"""
    print("  检测中央按钮...")
    # 倒计时中
    for kw in CENTER_WAITING:
        if d(textContains=kw).exists(timeout=1):
            print(f"    倒计时中: {kw}")
            return "waiting"
    # 可点击
    for kw in CENTER_ACTIVE:
        btn = d(textContains=kw)
        if btn.exists(timeout=1):
            click_u2_control(d, btn, kw)
            print(f"    点击 {kw}")
            time.sleep(3)
            return "clicked"
    print("    无按钮")
    return "no_button"


def match_live_bubble(blocks):
    """合并同一气泡的相邻标题行，按白名单完整标题精确识别。"""
    scale = screen_height / 2400
    for block in blocks:
        cx, cy = int(block.get("cx", 0)), int(block.get("cy", 0))
        if not screen_height * 0.15 <= cy <= screen_height * 0.50:
            continue
        text = re.sub(r"\s+", "", str(block.get("text", "")))
        candidates = [text]
        for below in blocks:
            if (below.get("cy", 0) <= screen_height * 0.50
                    and abs(below.get("cx", 0) - cx) <= 100 * scale
                    and 0 < below.get("cy", 0) - cy <= 100 * scale):
                candidates.append(text + re.sub(r"\s+", "", str(below.get("text", ""))))
        for name in candidates:
            for title, durations in LIVE_TASK_WHITELIST.items():
                for seconds in durations:
                    if re.fullmatch(re.escape(f"{title}{seconds}秒") + r"(?:[（(]\d+/\d+[）)])?", name):
                        return {"name": name, "cx": cx, "cy": cy}
    return None


def find_bubble(blocks=None):
    """找气泡任务（文字本身就是可点击的气泡按钮）"""
    from utils import get_ocr_text_blocks

    if blocks is None:
        blocks = get_ocr_text_blocks(d, retries=0, height_ratio=0.5)
    live = match_live_bubble(blocks)
    if live:
        print(f"    [白名单精确匹配] {live['name']} @({live['cx']},{live['cy']})")
        return live
    for kw in TASK_KW:
        btn = d(textContains=kw)
        if btn.exists(timeout=1):
            text = btn.get_text().strip()
            if any(kw_blocked in text for kw_blocked in BLOCKED_TASK_KEYWORDS):
                continue
            if "直播" in text:
                continue
            try:
                cx, cy = btn.center()
                cx, cy = int(cx), int(cy)
            except:
                continue
            # 气泡按钮在屏幕 15%~50% 范围内才接受
            if cy < int(screen_height * 0.15) or cy > int(screen_height * 0.50):
                continue
            print(f"    [匹配] {text} → 点击文字本身 @({cx},{cy})")
            return {"name": text, "cx": cx, "cy": cy}
    return None


def is_sleep_page(blocks):
    """只接受顶部页面标题，避免将元宝中心的入口当成睡觉页。"""
    return any(re.sub(r"\s+", "", block.get("text", "")) == "睡觉赚元宝"
               and 0 <= block.get("cy", screen_height) <= screen_height * 0.12
               for block in blocks)


def return_to_sleep_page():
    """先逐层返回，再尝试一次重新导航；确认失败时禁止继续扫奖励。"""
    for attempt in range(STALE_LIMIT + 1):
        if is_sleep_page(get_ocr_text_blocks(d, retries=0, height_ratio=0.5)):
            return True
        if attempt < STALE_LIMIT:
            print(f"  未回到睡觉页，按返回 {attempt + 1}/{STALE_LIMIT}")
            d.press("back")
            time.sleep(1.5)
    print("  返回后仍未确认睡觉页，尝试重新导航")
    if navigate_to_sleep_page(d) and is_sleep_page(get_ocr_text_blocks(d, retries=0, height_ratio=0.5)):
        return True
    print("  ⚠️ 未能返回睡觉页，停止本轮，不扫描奖励")
    return False


def do_bubble_task(name, btn_cx, btn_cy):
    """执行气泡任务"""
    if is_blocked_boutique_task(name):
        print('  ⊘ 已屏蔽精选宝贝任务，跳过')
        return False

    print(f"  🎯 {name}")

    # 用户要求屏蔽“查看3个精选宝贝 / 精选宝贝”类任务，不点击、不记录。
    if "精选宝贝" in name:
        print("  ⊘ 已屏蔽精选宝贝任务，跳过")
        return

    if "猜价格" in name:
        d.click(btn_cx, btn_cy)
        time.sleep(3)
        try:
            btn = d(className="android.widget.Button", textMatches=r"打开|允许|始终允许")
            if btn.exists(timeout=1):
                click_u2_control(d, btn, "打开/允许")
                time.sleep(1)
        except:
            pass
        handle_guess_price_game(d)
        time.sleep(2)
        try:
            log_task_completed("睡觉赚元宝", name)
        except:
            pass
        return

    if "看视频" in name:
        click_device_point(d, btn_cx, btn_cy, "视频任务按钮")
        time.sleep(0.5)
        execute_video_task(
            d, name, btn_cx, btn_cy,
            extra_time_seconds=10,
            verify_media_page=True,
        )
        time.sleep(1.5)
    elif any(kw in name for kw in ["看直播", "直播"]):
        click_device_point(d, btn_cx, btn_cy, "直播任务按钮")
        time.sleep(0.5)
        execute_live_task(d, name, btn_cx, btn_cy)
        if not return_to_sleep_page():
            return False
    elif "精选宝贝" in name or ("查看" in name and "个" in name) or "签到" in name:
        click_device_point(d, btn_cx, btn_cy, "精选宝贝任务按钮")
        time.sleep(0.5)
        click_three_selected_items(d)
        # click_three_selected_items 每次点完商品已 back 回到列表页，
        # 再按一次从列表页退回睡觉页
        d.press("back")
        time.sleep(1.5)
    else:
        # 浏览任务
        click_device_point(d, btn_cx, btn_cy, "浏览任务按钮")
        time.sleep(0.5)
        try:
            btn = d(className="android.widget.Button", textMatches=r"打开|允许|始终允许")
            if btn.exists(timeout=1):
                click_u2_control(d, btn, "打开/允许")
                time.sleep(1)
        except:
            pass
        # 看精选/上新类任务固定60秒浏览
        if any(kw in name for kw in ["精选推荐", "精选好物", "上新好物", "省钱专区"]):
            execute_browse_task(d, BROWSE_TIME)
        else:
            execute_browse_task(d, 20)
        d.press("back")
        time.sleep(1.5)

    try:
        log_task_completed("睡觉赚元宝", name)
    except:
        pass


def scan_and_collect():
    """主循环：找气泡 → 执行 → 收集浮动气泡"""
    total = 0

    btn_status = check_center_btn()
    if btn_status == "waiting":
        print("  跳过点击")
    elif btn_status == "clicked":
        time.sleep(3)

    print("\n🔍 扫描气泡...")
    while True:
        time.sleep(1.5)
        print(f"\n📡 扫描中 ({total}个)...")

        blocks = get_ocr_text_blocks(d, retries=0, height_ratio=0.5)
        if not is_sleep_page(blocks):
            if not return_to_sleep_page():
                return total
            continue

        target = find_bubble(blocks)
        if target:
            if do_bubble_task(target["name"], target["cx"], target["cy"]) is False:
                return total
            report_task_found()
            total += 1
            print(f"  ✅ 已完成: {total}")
            continue

        # 收集浮动气泡
        count = click_float_bubbles(d, max_collect=5, ocr_height_ratio=0.5)
        if count > 0:
            report_task_found()
            total += count
            continue

        # 当前可视区域无气泡+无浮动气泡，直接结束（不下滑）
        print("  当前区域无可用任务，结束")
        break

    print(f"\n📊 共完成 {total} 个")
    report_round_finished()
    return total


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 睡觉赚元宝")
    print("=" * 50)

    navigate_to_sleep_page(d)
    scan_and_collect()

    try:
        show_today_summary()
    except:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ 中断")
    except Exception as e:
        print(f"\n❌ {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(" 👋")
