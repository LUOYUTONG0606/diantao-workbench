"""
点淘鸭摇一摇 自动化脚本
流程：导航 → 右滑 → 点击去摇一摇 → OCR扫描赚次数 → 循环做直播/浏览
"""
from task_exclusions import is_blocked_boutique_task
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
from task_recheck import report_task_found, report_round_finished
import re
import unicodedata

from u2_scanner import (
    connect_device, DT_APP,
    execute_browse_task, execute_live_task,
    is_stale_no_buttons,
    click_three_selected_items,
    click_u2_control,
)
from utils import get_ocr_text_blocks

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

# 点淘鸭家园入口固定坐标（1264×2780 实测）
DUCK_ENTRY_X = 824
DUCK_ENTRY_Y = 1771

# 按钮关键词（参照其他任务的匹配方式）
BTN_KEYWORDS = ["去完成", "去浏览", "去观看", "去参与"]

# 白名单任务（只做看直播和看黄金档）
TASK_WHITELIST = [
    "看直播",
    "看福利直播",
    "看黄金档",
    "看黄金8点档直播90秒",
    "看黄金八点档直播90秒",
    # OCR 实测会漏掉“黄金”中的“金”或整个“黄金”。
    "看黄8点档直播90秒",
    "看8点档直播90秒",
    "浏览精选推荐",
    # “查看3个精选宝贝 / 精选宝贝”已按用户要求屏蔽，不再列入白名单。
]

# 各任务时长
TASK_DURATIONS = {
    "看黄金8点档直播90秒": 90,
    "看黄金八点档直播90秒": 90,
    "看黄8点档直播90秒": 90,
    "看8点档直播90秒": 90,
    "看黄金档": 125,
    "看福利直播": 35,
    "看直播": 60,
    "浏览精选推荐": 60,
}

MAX_TASKS = 100
STALE_LIMIT = 3


# ========================================================
# 🧭 导航：首页 → 元宝中心 → 点淘鸭家园
# ========================================================
def navigate():
    """导航到点淘鸭家园页面（不打开任务列表）"""
    print("🔄 导航到【点淘鸭家园】...")

    print("  📱 启动点淘...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    print("  Step 1: 进入【元宝中心】...")
    btn = d(textContains="元宝中心")
    if btn.exists(timeout=3):
        click_u2_control(d, btn, "元宝中心")
    else:
        print("    ⚠️ 未找到元宝中心文字，点击底部右侧区域...")
        d.click(int(screen_width * 0.7), int(screen_height * 0.93))
    time.sleep(4)
    if not handle_yuanbao_live_popups(d):
        return False

    duck_btn = d(textContains="点淘鸭家园")
    if duck_btn.exists(timeout=3):
        click_u2_control(d, duck_btn, "点淘鸭家园")
    else:
        print(f"  ⚠️ 未找到点淘鸭家园文字，使用固定坐标 @({DUCK_ENTRY_X},{DUCK_ENTRY_Y})...")
        d.click(DUCK_ENTRY_X, DUCK_ENTRY_Y)
    time.sleep(5)
    print("  ✅ 已进入点淘鸭家园页面")


def swipe_right_one():
    """右滑一次"""
    swipe_x_start = int(screen_width * 0.15)
    swipe_x_end = int(screen_width * 0.85)
    swipe_y = int(screen_height * 0.50)
    print(f"  👉 右滑: ({swipe_x_start},{swipe_y}) → ({swipe_x_end},{swipe_y})")
    d.swipe(swipe_x_start, swipe_y, swipe_x_end, swipe_y, duration=0.3)
    time.sleep(1.5)


def swipe_to_rightmost():
    """右滑2次到屏幕最右边"""
    print("  Step 3: 右滑到最右边...")
    for i in range(2):
        print(f"    第 {i+1}/2 次右滑...")
        swipe_right_one()
    print("  ✅ 已右滑到最右边")


def find_block_by_text(blocks, keyword):
    """从OCR结果中匹配文本块"""
    for b in blocks:
        if keyword in b["text"]:
            return b
    return None


def print_ocr_blocks(blocks):
    """打印OCR识别结果"""
    if not blocks:
        print("  ⚠️ OCR 未识别到任何文本")
        return
    print(f"  📊 OCR 识别到 {len(blocks)} 个文本块:")
    for idx, b in enumerate(blocks):
        print(f"    [{idx:2d}] \"{b['text']}\" @({b['cx']:4d},{b['cy']:4d}) conf={b.get('conf', 0):.2f}")


# ========================================================
# 🔍 任务扫描（参照点淘鸭钻石任务的OCR配对模式）
# ========================================================
def scan_tasks(blocks):
    """
    OCR扫描"去完成"按钮，收集全部左侧候选文本并合并，
    用合并后的完整名做白名单匹配（参照 scan_duck_tasks）
    返回: [(task_name, btn_cx, btn_cy), ...]
    """
    # 1. 找"去完成"按钮
    go_buttons = []
    for b in blocks:
        if b["text"] in BTN_KEYWORDS:
            go_buttons.append(b)

    if not go_buttons:
        return []

    # 2. 收集候选文本（排除按钮自身和短文本）
    candidates = []
    for b in blocks:
        txt = b["text"]
        if len(txt) < 2 or txt in BTN_KEYWORDS:
            continue
        candidates.append(b)

    # 3. 配对：每个按钮收集同行左侧所有文本碎片并合并
    go_buttons.sort(key=lambda b: b["cy"])
    pairs = []
    used_names = set()

    for btn in go_buttons:
        btn_cx, btn_cy = btn["cx"], btn["cy"]
        best_name = None
        best_dist = 81

        # 收集该按钮行左侧的所有候选文本（同一行任务名可能被OCR拆成多段）
        row_candidates = []
        for nc in candidates:
            if nc["text"] in used_names:
                continue
            if nc["cx"] >= btn_cx - 20:
                continue
            hdist = btn_cx - nc["cx"]
            if hdist > 950:
                continue
            if nc["cy"] > btn_cy + 30:
                continue
            if btn_cy - nc["cy"] > 80:
                continue
            row_candidates.append(nc)

        if not row_candidates:
            print(f"    ⚠️ 未找到【去完成】@({btn_cx},{btn_cy}) 的任务名")
            continue

        # 按X坐标排序，合并左侧碎片文本
        row_candidates.sort(key=lambda x: x["cx"])
        merged_name = "".join(c["text"] for c in row_candidates)

        # 找垂直最接近的单个候选
        for nc in row_candidates:
            dist = abs(nc["cy"] - btn_cy)
            if dist < best_dist:
                best_dist = dist
                best_name = nc

        # 优先使用合并后的完整名（白名单匹配更准确）
        name = merged_name if len(merged_name) >= 4 else (best_name["text"] if best_name else "")

        if name and best_dist <= 80:
            used_names.add(name)
            pairs.append((name, int(btn_cx), int(btn_cy)))
            print(f"    配对: 【{name}】 → 去完成 @({btn_cx},{btn_cy})")

    return pairs


def _normalize_task_name(name: str | None) -> str:
    """归一化 OCR 任务名中的空白和秒单位变体。"""
    compact = unicodedata.normalize("NFKC", name or "")
    compact = compact.translate(str.maketrans({
        "點": "点",
        "檔": "档",
        "擋": "档",
        "挡": "档",
    }))
    compact = re.sub(r"[\s\u200b]+", "", compact)
    return re.sub(r"(?<=\d)[sS](?=\D|$)", "秒", compact)


def is_whitelisted(name):
    """检查任务名是否在白名单中"""
    normalized_name = _normalize_task_name(name)
    # 90 秒黄金档是当前要执行的明确目标；即使 OCR 把“档”识别成
    # 繁体/近形字，经过归一化后也应保留白名单资格。
    target_90s = "8点档直播90秒" in normalized_name
    return target_90s or any(
        _normalize_task_name(kw) in normalized_name
        for kw in TASK_WHITELIST
    )


def _task_duration(task_name: str | None) -> int:
    """按归一化后的任务名返回直播任务时长。"""
    normalized_name = _normalize_task_name(task_name)
    for keyword, seconds in TASK_DURATIONS.items():
        if _normalize_task_name(keyword) in normalized_name:
            return seconds
    # OCR 可能漏掉任务名前缀，但“直播 + 秒数”仍能确定执行时长。
    if "直播" in normalized_name:
        match = re.search(r"(\d+)秒", normalized_name)
        if match:
            return int(match.group(1))
    return 60


def do_task(task_name, btn_cx, btn_cy):
    """执行单个白名单任务"""
    if is_blocked_boutique_task(task_name):
        print('  ⊘ 已屏蔽精选宝贝任务，跳过')
        return False

    print(f"  🎯 {task_name}")

    d.click(btn_cx, btn_cy)
    time.sleep(2)

    if "精选宝贝" in task_name:
        print(f"    类型: 查看精选宝贝（点击3个商品）")
        click_three_selected_items(d)
        # click_three_selected_items 只从商品详情退回商品列表，再按一次退回摇一摇任务列表
        d.press("back")
        time.sleep(1.5)
    elif "直播" in task_name or "黄金档" in task_name:
        duration = _task_duration(task_name)
        print(f"    类型: 直播 ({duration}秒)")
        execute_live_task(d, task_name, btn_cx, btn_cy, duration=duration)
        d.press("back")
        time.sleep(1.5)
    else:
        print(f"    类型: 浏览 (60秒)")
        execute_browse_task(d, 60)
        # 参照签到任务：execute_browse_task 后直接 back 返回任务列表
        d.press("back")
        time.sleep(2)

    print(f"  ✅ 完成: 【{task_name}】")


# ========================================================
# 🧪 测试模式：仅导航 + 扫描展示
# ========================================================
def test_mode():
    """测试路径：导航 → 右滑 → OCR扫描 → 展示结果"""
    print("\n" + "=" * 60)
    print(" 🧪 【测试模式】点淘鸭摇一摇 - 路径探测")
    print("    仅展示页面内容，不执行任何点击操作")
    print("=" * 60)

    if navigate() is False:
        return
    swipe_to_rightmost()
    time.sleep(2)

    print("\n  📋 OCR扫描 - 右滑2次后...")
    blocks = get_ocr_text_blocks(d)
    print_ocr_blocks(blocks)

    target = find_block_by_text(blocks, "去摇一摇")
    if target:
        print(f"\n  ✅ 找到了【去摇一摇】按钮！可切换执行模式运行")
    else:
        print(f"\n  ⚠️ 未找到【去摇一摇】按钮")
        print(f"     请确认是否在正确的页面，或 OCR 是否识别准确")

    print("\n" + "=" * 60)
    print(" 🧪 测试结束")
    print("=" * 60)


# ========================================================
# 🚀 执行模式：进入摇一摇 → 扫描 → 循环执行任务
# ========================================================
def run_mode():
    """完整流程：导航 → 进入摇一摇 → OCR扫描 → 循环执行"""
    print("\n" + "=" * 60)
    print(" 🚀 【执行模式】点淘鸭摇一摇")
    print(f"    白名单: {' | '.join(TASK_WHITELIST)}")
    print("=" * 60)

    if navigate() is False:
        return
    swipe_to_rightmost()
    time.sleep(2)

    # 找"去摇一摇"并点击进入
    print("\n  🔍 OCR 查找【去摇一摇】...")
    entered = False
    for attempt in range(5):
        blocks = get_ocr_text_blocks(d)
        if blocks:
            target = find_block_by_text(blocks, "去摇一摇")
            if target:
                print(f"\n  🎯 点击【{target['text']}】 @({target['cx']},{target['cy']})")
                d.click(target["cx"], target["cy"])
                time.sleep(5)
                entered = True
                print("  ✅ 已进入摇一摇页面")
                break
        else:
            print(f"  ⚠️ 未找到去摇一摇（第 {attempt+1} 次），重试...")
        time.sleep(1.5)

    if not entered:
        print("  ❌ 未能进入摇一摇页面")
        return

    # OCR扫描赚次数 → 点击进入任务区
    print("\n  🔍 OCR扫描赚次数...")
    blocks = get_ocr_text_blocks(d)
    print_ocr_blocks(blocks)
    zhuang_target = find_block_by_text(blocks, "赚次数")
    if zhuang_target:
        print(f"  🎯 点击赚次数 @({zhuang_target['cx']},{zhuang_target['cy']})")
        d.click(zhuang_target["cx"], zhuang_target["cy"])
        time.sleep(3)
    else:
        print("  ⚠️ 未找到赚次数，直接开始任务循环")

    # ===== 任务循环 =====
    total = 0
    clicked = {}
    prev_snap = []
    prev_empty = []

    print("\n" + "=" * 50)
    print("🔍 开始扫描任务（OCR模式）")
    print(f"   白名单: {' | '.join(TASK_WHITELIST)}")
    print("=" * 50)

    while True:
        if total >= MAX_TASKS:
            print(f"  🎉 达到最大任务数 ({MAX_TASKS})，结束")
            break

        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        if not blocks:
            if is_stale_no_buttons(prev_empty, [], STALE_LIMIT):
                print("  🎉 连续3轮 OCR 空白")
                break
            print("  ⚠️ OCR 无文本，下滑刷新...")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)
            continue

        pairs = scan_tasks(blocks)
        if not pairs:
            if is_stale_no_buttons(prev_empty, pairs, STALE_LIMIT):
                print("  🎉 连续3轮无任务按钮")
                break
            print("  ⚠️ 无任务按钮，下滑刷新...")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)
            continue

        # 去重检测
        current_snap = frozenset(
            (name[:15], cx // 60, cy // 60)
            for name, cx, cy in pairs
        )
        prev_snap.append(current_snap)
        if len(prev_snap) > STALE_LIMIT:
            prev_snap.pop(0)
        if len(prev_snap) >= STALE_LIMIT and len(set(prev_snap)) == 1:
            print("  🎉 连续3轮相同任务列表，结束")
            break

        # 找可执行任务
        task = None
        for name, cx, cy in pairs:
            if not name:
                continue
            if not is_whitelisted(name):
                print(f"  ⊘ 非白名单: {name}")
                continue
            if clicked.get(name, 0) >= 2:
                print(f"  ⊘ 已达上限: {name}")
                continue
            task = (name, cx, cy)
            break

        if task:
            prev_snap.clear()
            prev_empty.clear()
            name, cx, cy = task
            clicked[name] = clicked.get(name, 0) + 1
            report_task_found()
            do_task(name, cx, cy)
            total += 1
            print(f"  📊 已完成 {total}/{MAX_TASKS}")
        else:
            print("  无可执行任务，下滑刷新...")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)

    print(f"\n📊 共完成 {total} 个任务")
    report_round_finished()


# ========================================================
# 🚀 Main
# ========================================================
if __name__ == "__main__":
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        run_mode()
    except KeyboardInterrupt:
        print("\n⚠️ 中断")
    except Exception as e:
        print(f"\n❌ {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(" 👋")
