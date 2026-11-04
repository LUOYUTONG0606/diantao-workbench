"""
主播打卡粉丝礼 自动化脚本
流程：进点淘 → OCR找粉丝礼打卡入口 → 扫描去打卡按钮
"""
import time
import sys
from task_recheck import recheck_enabled, report_task_found, report_round_finished
import re

import uiautomator2 as u2
from u2_scanner import connect_device, DT_APP, scan_page, is_stale_no_buttons, click_u2_control
from utils import get_ocr_text_blocks, get_current_app, log_task_completed, show_today_summary

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

CLICK_WAIT = 5
STALE_LIMIT = 3


def navigate():
    """进点淘 → OCR找粉丝礼打卡入口"""
    print("🔄 导航中...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    blocks = get_ocr_text_blocks(d)
    # 策略1: 精准找打卡有奖/有礼
    for b in blocks:
        if "打卡有奖" in b["text"] or "打卡有礼" in b["text"]:
            d.click(b["cx"], b["cy"])
            print(f"  ✅ 点击【{b['text']}】")
            time.sleep(4)
            return True

    # 策略2: 打卡 (Y:20%~50%)
    for b in blocks:
        if "打卡" in b["text"] and 0.2 <= b["cy"] / screen_height <= 0.5:
            d.click(b["cx"], b["cy"])
            print(f"  ✅ 点击【{b['text']}】")
            time.sleep(4)
            return True

    # 策略3: 粉丝礼 (Y:25%~35%)
    for b in blocks:
        if "粉丝礼" in b["text"] and 0.25 <= b["cy"] / screen_height <= 0.35:
            d.click(b["cx"], b["cy"])
            print(f"  ✅ 点击【{b['text']}】")
            time.sleep(4)
            return True

    print("  ❌ 未找到入口")
    return False


def back():
    """返回粉丝礼页面"""
    print("  🔙 返回中...")
    for _ in range(5):
        pkg, _ = get_current_app(d)
        if pkg != DT_APP:
            print("  ⚠️ 掉出点淘，重启...")
            d.app_start(DT_APP, stop=False)
            time.sleep(3)
            navigate()
            return

        if d(textContains="打卡").exists(timeout=1) or d(textContains="粉丝礼").exists(timeout=1):
            print("  ✅ 已回到")
            return

        btn = d(textMatches=r"关闭|返回|完成")
        if btn.exists(timeout=1):
            click_u2_control(d, btn, "关闭/返回/完成")
            time.sleep(2)
        else:
            d.press("back")
            time.sleep(2)

    navigate()
    return


def do_clock(btn_cx, btn_cy, task_name=""):
    """点击打卡"""
    print(f"  🎯 打卡: {task_name}")
    d.click(btn_cx, btn_cy)
    time.sleep(2)

    try:
        btn = d(className="android.widget.Button", textMatches=r"打开|允许|始终允许")
        if btn.exists(timeout=1):
            click_u2_control(d, btn, "打开/允许")
            time.sleep(2)
    except:
        pass

    print(f"  ⏳ 等待 {CLICK_WAIT}s")
    time.sleep(CLICK_WAIT)
    back()

    try:
        log_task_completed("主播打卡粉丝礼", task_name)
    except:
        pass


def scan_and_clock():
    """主循环：U2找去打卡 → 执行"""
    total = 0
    prev_snap = []
    prev_empty = []

    print("\n🔍 扫描打卡任务...")
    while True:
        time.sleep(1.5)
        print(f"\n📡 扫描中 ({total}个)...")

        # 直接找去打卡按钮
        btn = d(textContains="去打卡")
        if btn.exists(timeout=1):
            text = btn.get_text().strip()
            if "已打卡" in text or "已完成" in text:
                prev_snap.clear()
                prev_empty.clear()
                print(f"  ⊘ {text}")
                continue
            try:
                cx, cy = btn.center()
                cx, cy = int(cx), int(cy)
            except:
                cx, cy = screen_width // 2, screen_height // 2
            report_task_found()
            do_clock(cx, cy, text)
            total += 1
            prev_snap.clear()
            prev_empty.clear()
            continue

        # 用 scan_page 兜底
        nodes = scan_page(d)
        found = False
        for n in nodes:
            if "去打卡" in n["text"] and "已打卡" not in n["text"]:
                report_task_found()
                do_clock(n["cx"], n["cy"], n["text"])
                total += 1
                found = True
                prev_snap.clear()
                prev_empty.clear()
                break

        if found:
            continue

        # 检测是否还有粉丝礼页面
        has_fans = any("粉丝礼" in n["text"] or "打卡" in n["text"] for n in nodes)
        if not has_fans and total > 0:
            print("  🎉 所有打卡完成")
            break

        # 下滑
        d.swipe(screen_width // 2, int(screen_height * 0.7),
                screen_width // 2, int(screen_height * 0.3))
        time.sleep(2)

        # 再次确认
        nodes = scan_page(d)
        if not any("去打卡" in n["text"] and "已打卡" not in n["text"] for n in nodes) and (total > 0 or recheck_enabled()):
            print("  🎉 全部完成")
            break

    print(f"\n📊 共完成 {total} 个打卡")
    report_round_finished()
    return total


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🎤 主播打卡粉丝礼")
    print("=" * 50)

    if not navigate():
        print("❌ 无法进入")
        return

    scan_and_clock()

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
        print(" ")
