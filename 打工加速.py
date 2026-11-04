"""
打工加速 自动化脚本（打工领红包）
流程：OCR原地扫描（不滑动）→ 找到"浏览精选推荐30秒" → 直接点击文字 → 执行浏览
"""
import time
import sys
import re

from u2_scanner import connect_device, execute_browse_task
from utils import get_ocr_text_blocks, log_task_completed, show_today_summary

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")


def parse_seconds(text, default=30):
    """从任务名提取秒数"""
    m = re.search(r"(\d+)\s*秒", text)
    return int(m.group(1)) if m else default


def is_search_browse_task(text):
    """搜索后浏览需要独立搜索流程，不能交给普通浏览执行器。"""
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", text or "")
    return "搜索" in compact and "浏览" in compact


def has_search_browse_nearby(blocks, block):
    """兼容同一行任务名被 OCR 拆成多个文字块。"""
    row = [b for b in blocks
           if abs(b["cy"] - block["cy"]) <= 60
           and abs(b["cx"] - block["cx"]) <= 500]
    row.sort(key=lambda b: b["cx"])
    return is_search_browse_task("".join(b["text"] for b in row))


def is_sliding_browse_task(text):
    """「滑动浏览XX秒」是页面内的通用浏览倒计时/浮层，不是打工加速的目标任务。"""
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", text or "")
    return re.search(r"滑动浏览\d+", compact) is not None


def has_sliding_browse_nearby(blocks, block):
    """排除「滑动浏览XX秒」；兼容 OCR 把该行拆成多个文字块。

    含「精选/推荐」的任务块不做同行拼接判断，避免浮层文字误伤真正的
    「浏览精选推荐XX秒」任务。
    """
    if is_sliding_browse_task(block["text"]):
        return True
    compact = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", block["text"] or "")
    if "浏览" not in compact or "精选" in compact or "推荐" in compact:
        return False
    row = [b for b in blocks
           if abs(b["cy"] - block["cy"]) <= 60
           and abs(b["cx"] - block["cx"]) <= 500]
    row.sort(key=lambda b: b["cx"])
    return is_sliding_browse_task("".join(b["text"] for b in row))


def find_browse_task(blocks):
    """在当前屏幕 OCR 文本块中找"浏览精选推荐"字样的文本，直接返回其坐标
    
    返回: (block_text, cx, cy) 或 None
    """
    # 调试：打印所有文本块
    print(f"  📋 OCR 文本块 ({len(blocks)}):")
    for b in blocks:
        print(f"    [{b['cx']:4d},{b['cy']:4d}] {b['text']}")

    # 搜索任务优先排除，不能被下面的精选推荐或秒数兜底重新选中。
    blocks = [b for b in blocks if not has_search_browse_nearby(blocks, b)]
    # 「滑动浏览XX秒」是页面浮层/通用浏览倒计时，同样不能被任何兜底选中。
    blocks = [b for b in blocks if not has_sliding_browse_nearby(blocks, b)]

    # 找包含"浏览精选推荐"或"精选推荐"的文字块
    for b in blocks:
        text = b["text"]
        if "精选推荐" in text and "浏览" in text:
            print(f"  ✅ 找到: {text} @({b['cx']},{b['cy']})")
            return (text, int(b["cx"]), int(b["cy"]))

    # 放宽：只要包含"浏览"和"精选推荐"两个词（可能被分割在两个块，但先找单个块包含的最优匹配）
    for b in blocks:
        text = b["text"]
        if "浏览" in text and ("精选" in text or "推荐" in text):
            print(f"  ✅ 找到: {text} @({b['cx']},{b['cy']})")
            return (text, int(b["cx"]), int(b["cy"]))

    # 再放宽：只要包含"浏览"且有数字+秒，也可能是目标
    for b in blocks:
        text = b["text"]
        if "浏览" in text and re.search(r"\d+秒", text):
            print(f"  ✅ 找到: {text} @({b['cx']},{b['cy']})")
            return (text, int(b["cx"]), int(b["cy"]))

    print("  ⊘ 屏幕无浏览精选推荐类文字")
    return None


def find_go_btn_nearby(blocks, cx, cy):
    """在 (cx,cy) 附近找"去完成/去浏览"按钮，返回更精确的点击坐标"""
    for b in blocks:
        text = b["text"]
        if "去完成" in text or "去浏览" in text or "去观看" in text:
            # 垂直距离在 50px 内，水平偏移不超过 400px
            vdist = abs(b["cy"] - cy)
            hdist = abs(b["cx"] - cx)
            if vdist <= 60 and hdist <= 500:
                print(f"    → 找到附近按钮: {text} @({b['cx']},{b['cy']})")
                return (int(b["cx"]), int(b["cy"]))
    return None


def exec_browse(cx, cy, task_name="", blocks=None):
    """点击任务文字（或附近的去完成按钮），进入后执行浏览"""
    if is_search_browse_task(task_name):
        print(f"  ⊘ 搜索后浏览任务不按普通浏览执行: 【{task_name}】")
        return False
    if is_sliding_browse_task(task_name):
        print(f"  ⊘ 滑动浏览倒计时浮层不按任务执行: 【{task_name}】")
        return False
    click_cx, click_cy = cx, cy

    # 优先点击附近"去完成/去浏览"按钮（更精确）
    if blocks:
        btn_xy = find_go_btn_nearby(blocks, cx, cy)
        if btn_xy:
            click_cx, click_cy = btn_xy
        else:
            # 兜底：点击文字块下方区域（卡片可点击区域通常在文字下方）
            click_cy = cy + 25

    print(f"  🎯 点击: {task_name} @({click_cx},{click_cy})")
    d.click(click_cx, click_cy)
    time.sleep(3)

    # 精选推荐页的顶部搜索奖励是独立内嵌任务；本次只执行外层浏览。
    duration = parse_seconds(task_name, default=30)
    if not execute_browse_task(d, min_duration=duration, max_wait=duration + 30):
        print("  ⚠️ 浏览未完成，保留当前页面")
        return False

    d.press("back")
    time.sleep(2)
    return True


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🚀 打工加速（打工领红包）")
    print("=" * 50)
    print("\n🔄 原地扫描中（每2秒），等待浏览精选推荐30秒出现...")
    print("   脚本仅截图识别，不会操作屏幕，可放心手动点击")

    total_all = 0
    ocr_empty_retries = 0
    MAX_RETRIES = float("inf")  # 不限制时间：一直原地扫描，直到手动停止

    while True:
        time.sleep(2)
        blocks = get_ocr_text_blocks(d)

        if not blocks:
            ocr_empty_retries += 1
            if ocr_empty_retries >= MAX_RETRIES:
                print("  ⚠️ 等待超时，结束")
                break
            print(f"  ⏳ 未识别到文本（{ocr_empty_retries}/{MAX_RETRIES}）...")
            continue

        task_info = find_browse_task(blocks)
        if not task_info:
            ocr_empty_retries += 1
            if ocr_empty_retries >= MAX_RETRIES:
                print("  ⚠️ 等待超时，结束")
                break
            print(f"  ⏳ 未找到目标任务（{ocr_empty_retries}/{MAX_RETRIES}），继续等待...")
            continue

        ocr_empty_retries = 0
        task_name, cx, cy = task_info
        if not exec_browse(cx, cy, task_name, blocks):
            print("  ⏹️ 停止自动执行，本次任务不计入完成数量")
            break

        total_all += 1
        print(f"  ✅ 已完成: {total_all} 个任务")

        try:
            log_task_completed("打工加速", task_name)
        except:
            pass

        print("\n🔄 继续原地扫描下一个任务...")

        if total_all >= 100:
            print("  ✅ 已达任务上限")
            break

    print(f"\n📊 共完成 {total_all} 个任务")

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
