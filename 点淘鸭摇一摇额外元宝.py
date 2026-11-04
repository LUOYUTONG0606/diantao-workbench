import re
import sys
import time

from u2_scanner import connect_device, execute_browse_task, execute_video_task
from utils import get_ocr_text_blocks


d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

EXTRA_YUANBAO_VIDEO_SECONDS = 30
EXTRA_YUANBAO_EXTRA_SECONDS = 10
EXTRA_YUANBAO_SCAN_TIMEOUT_SECONDS = 30
EXTRA_YUANBAO_PATTERN = re.compile(r"(额外获得|再得)\s*\d+\s*元宝")


def scan_extra_yuanbao_task(blocks):
    for block in blocks:
        text = block["text"]
        match = EXTRA_YUANBAO_PATTERN.search(text)
        if match:
            task_type = "video" if match.group(1) == "额外获得" else "browse"
            return task_type, text, int(block["cx"]), int(block["cy"])
    return None


def execute_extra_yuanbao_task(task_type, task_name, btn_cx, btn_cy):
    print(f"  🎯 执行【{task_name}】")
    d.click(btn_cx, btn_cy)
    if task_type == "video":
        completed = execute_video_task(
            d,
            task_name,
            btn_cx,
            btn_cy,
            duration=EXTRA_YUANBAO_VIDEO_SECONDS + EXTRA_YUANBAO_EXTRA_SECONDS,
            verify_media_page=True,
            initial_swipe=True,
        )
    else:
        time.sleep(1.5)
        completed = execute_browse_task(
            d,
            min_duration=EXTRA_YUANBAO_VIDEO_SECONDS,
            max_wait=(EXTRA_YUANBAO_VIDEO_SECONDS + EXTRA_YUANBAO_EXTRA_SECONDS
                      + EXTRA_YUANBAO_SCAN_TIMEOUT_SECONDS),
            extra_seconds=EXTRA_YUANBAO_EXTRA_SECONDS,
        )
        if not completed:
            print(f"  ⚠️ 【{task_name}】浏览超时，保留当前页面，停止自动执行")
            return False
        d.press("back")
        time.sleep(2)
    if not completed:
        print(f"  ⚠️ 【{task_name}】未完成，停止自动执行")
        return False
    print(f"  ✅ 完成【{task_name}】")
    return True


def scan_and_execute():
    total = 0

    print("\n🔍 开始扫描当前屏幕上的【额外获得XX元宝 / 再得XX元宝】...")
    while True:
        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        if not blocks:
            print("  ⚠️ OCR 无文本，继续等待刷新")
            continue

        task = scan_extra_yuanbao_task(blocks)
        if not task:
            print("  ⏳ 未发现额外元宝/再得元宝，继续等待")
            continue

        task_type, name, cx, cy = task
        print(f"  📌 发现【{name}】 @({cx},{cy})")
        if not execute_extra_yuanbao_task(task_type, name, cx, cy):
            return
        total += 1
        print(f"  📊 已完成 {total} 个额外元宝任务")


def main():
    if sys.stdout.encoding == "gbk":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 50)
    print(" 🦆 点淘鸭摇一摇额外元宝")
    print("=" * 50)

    scan_and_execute()


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
