"""
618花花乐 自动化脚本
流程：进点淘 → 红包花花乐 → 集元气 → 扫描执行任务（直播+浏览）
"""
import time
import sys

from u2_scanner import connect_device, DT_APP
from utils import (
    get_ocr_text_blocks, execute_live_task, handle_guess_price_game,
    check_can_open, log_task_completed, show_today_summary,
    any_match, is_blacklisted, back_to_task, show_dev_guide,
    BLACKLIST_KEYWORDS, LIVE_KW, TASK_BTN_KW,
)

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width}x{screen_height}")

BROWSE_TIME = 10
MAX_ROUNDS = 100


def navigate():
    """导航：进点淘 → 红包花花乐 → 集元气"""
    print("🔄 导航中...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    # 红包花花乐
    for b in get_ocr_text_blocks(d):
        if "红包花花乐" in b["text"]:
            d.click(b["cx"], b["cy"])
            print(f"  ✅ 点击【{b['text']}】")
            time.sleep(3)
            break
    else:
        print("  ⚠️ 未找到红包花花乐")

    # 集元气 - 固定坐标点击
    time.sleep(2)
    d.click(1126, 1620)
    print("  ✅ 点击集元气(1126,1620)")
    time.sleep(3)
    if d(textContains="去完成").exists(timeout=2):
        print("  ✅ 弹窗已打开")
        return True
    print("  ⚠️ 集元气弹窗打开失败")
    return False


def scan_tasks(done_names):
    """OCR 扫描新任务"""
    blocks = get_ocr_text_blocks(d)
    if not blocks:
        return []

    new_tasks = []
    for b in blocks:
        if not any_match(b["text"], TASK_BTN_KW):
            continue
        btn_cx, btn_cy = b["cx"], b["cy"]

        # 收集关联任务名
        parts = []
        for nb in blocks:
            if abs(nb["cy"] - btn_cy) < 80:
                if nb["cx"] < btn_cx - 30 or nb["cx"] > btn_cx + 30 or nb["cy"] < btn_cy - 20:
                    if not any_match(nb["text"], TASK_BTN_KW):
                        parts.append(nb["text"])
        name = " | ".join(parts) if parts else b["text"]
        if name in done_names or is_blacklisted(name):
            continue

        new_tasks.append({"cx": btn_cx, "cy": btn_cy, "name": name})
        print(f"    📌 {name}")

    return new_tasks


def do_task(btn_cx, btn_cy, task_name):
    """执行单个任务"""
    if any_match(task_name, LIVE_KW):
        print(f"  📺 直播: {task_name}")
        d.click(btn_cx, btn_cy)
        try:
            time.sleep(3)
            check_can_open(d)
            execute_live_task(d, task_name, btn_cx, btn_cy, duration=10)
        finally:
            d.press("back")
            time.sleep(1)
    else:
        print(f"  🎯 浏览: {task_name}")
        d.click(btn_cx - 60, btn_cy)
        time.sleep(3)
        check_can_open(d)
        time.sleep(BROWSE_TIME)
        for _ in range(15):
            if d(textContains="恭喜完成任务").exists(timeout=1) or \
               d(textContains="任务完成").exists(timeout=1):
                break
            time.sleep(1)
        # 退出浏览，按一次返回回到任务列表
        d.press("back")
        time.sleep(1)


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🎉 618花花乐")
    print("=" * 50)

    show_dev_guide()  # 启动时显示开发规范摘要

    navigate()
    done = set()
    total = 0

    for round_idx in range(1, MAX_ROUNDS + 1):
        print(f"\n{'='*40}\n  第 {round_idx} 轮\n{'='*40}")

        if round_idx > 1:
            back_to_task(d, target_keywords=["去完成"])
            time.sleep(1)

        new_tasks = scan_tasks(done)

        if not new_tasks:
            print("  📭 没有新任务，结束")
            break

        print(f"  >>> {len(new_tasks)} 个新任务")
        for t in new_tasks:
            back_to_task(d, target_keywords=["去完成"])
            time.sleep(1)
            do_task(t["cx"], t["cy"], t["name"])
            done.add(t["name"])
            total += 1
            try:
                log_task_completed("618花花乐", t["name"])
            except:
                pass
            print(f"  ✅ 完成 [{t['name']}]（共 {total} 个）")

    print(f"\n🎉 全部完成! 共 {total} 个")
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
        print("\n 👋")
