"""
点淘 - 点淘购物赚钱 自动化脚本
流程：进点淘 → OCR识别元宝中心 → 购物入口 → 立即签到/快速领 → OCR扫描任务列表
"""
from task_exclusions import is_blocked_boutique_task
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
from task_recheck import report_task_found, report_round_finished
import re
import random

import uiautomator2 as u2
from u2_scanner import (
    connect_device, DT_APP, click_three_selected_items, handle_guess_price_game, is_search_low_price_task,
    execute_video_task,
)
from utils import (
    click_ocr_text,
    get_ocr_text_blocks,
    check_can_open, log_task_completed, show_today_summary,
    browse_task, any_match, is_blacklisted, back_to_task, show_dev_guide,
    BLACKLIST_KEYWORDS, LIVE_KW, TASK_BTN_KW,
)

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

SCAN_INTERVAL = 2                   # 扫描间隔(秒)
MAX_TASKS = 100                      # 最大任务数
MAX_BACK_ATTEMPTS = 6               # 返回最大尝试次数

# OCR 无法判定落地页时的保守兜底：官方临时把“看视频”入口改成了直播页。
# 正常路径仍然在点击后用实际页面 OCR 判定，不在点击前强制改类型。
SHOPPING_TASK_ROUTE_OVERRIDE_MAP: dict[str, str] = {
    "看视频": "live",
}


def enter_shopping_tasks():
    """固定点击元宝中心购物卡片，兼容双11活动改名。"""
    # 有/无“额外福利”横条时，购物卡片的共同覆盖区域。
    print("  🎯 固定点击购物入口 @(934,1530)")
    d.click(934, 1530)
    time.sleep(2.5)


def _find_ocr_block(blocks, keyword):
    """在 OCR 文本块中查找包含指定文字的第一个控件。"""
    normalized_keyword = re.sub(r"\s+", "", keyword)
    return next(
        (
            block
            for block in blocks
            if normalized_keyword in re.sub(
                r"\s+", "", str(block.get("text", ""))
            )
        ),
        None,
    )


def _shopping_task_list_open(blocks):
    """确认任务列表浮层已经打开，而不是首页露出零散任务卡片。"""
    texts = [re.sub(r"\s+", "", str(block.get("text", ""))) for block in blocks]
    return any("做任务领元宝" in text for text in texts)


def _shopping_task_list_ready(blocks):
    """确认当前页面包含任务列表或至少一个可执行任务。"""
    texts = [re.sub(r"\s+", "", str(block.get("text", ""))) for block in blocks]
    return _shopping_task_list_open(blocks) or any(
        any(keyword in text for keyword in TASK_BTN_KW) for text in texts
    )


def _find_show_more_block(blocks):
    """查找任务列表的【查看更多】展开按钮；排除“查看更多视频”等媒体页文字。"""
    for block in blocks:
        text = re.sub(r"\s+", "", str(block.get("text", "")))
        if text in ("查看更多", "查看更多>", "查看更多›", "查看更多>"):
            return block
    return None


def _task_area_top(blocks):
    """返回任务列表区域的上边界 Y。

    优先使用浮层标题【做任务领元宝】；列表尚未展开时退回到【快速领】等
    标签所在行。以前硬编码的 852 只适用于旧版布局，双11 页面把内联任务
    列表上移后会把按钮全部过滤掉，因此改为按当前画面锚点动态计算。
    """
    title_y = next(
        (block["cy"] for block in blocks
         if "做任务领元宝" in re.sub(r"\s+", "", str(block.get("text", "")))),
        None,
    )
    if title_y is not None:
        return int(title_y)
    tab_y = max(
        (block["cy"] for block in blocks
         if any(kw in re.sub(r"\s+", "", str(block.get("text", "")))
                for kw in ("快速领", "签到领", "下单返"))),
        default=None,
    )
    return int(tab_y) if tab_y is not None else 0


def _task_buttons(blocks):
    """返回任务区域内可点击的【去完成】/【去浏览】按钮。"""
    top = _task_area_top(blocks)
    return [
        block for block in blocks
        if ("去完成" in block.get("text", "") or "去浏览" in block.get("text", ""))
        and block["cy"] > top
    ]


def _scan_shopping_page(attempts=3):
    """扫描购物页；OCR 偶发空结果时等待后重试。"""
    for attempt in range(1, attempts + 1):
        blocks = get_ocr_text_blocks(d, retries=0)
        if blocks:
            return blocks
        if attempt < attempts:
            print(f"  ⚠️ 购物页 OCR 为空（第 {attempt}/{attempts} 次），重试...")
            time.sleep(1.5)
    return []


def unlock_shopping_task_list():
    """
    打开购物任务列表。

    页面可能先停留在【签到领】：存在【立即签到】时先签到，再重新扫描并
    点击【快速领】；随后展开【查看更多】，最后返回任务列表的 OCR 结果。
    """
    blocks = _scan_shopping_page()
    if not blocks:
        print("  ⚠️ 购物任务页 OCR 无结果，无法检查立即签到/快速领")
        return []
    if _shopping_task_list_open(blocks):
        print("  ✅ OCR 确认购物任务列表已打开")
        return blocks

    sign_block = _find_ocr_block(blocks, "立即签到")
    if sign_block:
        sign_x, sign_y = int(sign_block["cx"]), int(sign_block["cy"])
        print(f"  🎯 发现【立即签到】，先签到 @({sign_x},{sign_y})")
        d.click(sign_x, sign_y)
        time.sleep(2)
        blocks = _scan_shopping_page()
        if not blocks:
            print("  ⚠️ 签到后 OCR 无结果，无法继续查找【快速领】")
            return []
    else:
        print("  ℹ️ 当前页面没有【立即签到】，继续检查【快速领】")

    quick_block = _find_ocr_block(blocks, "快速领")
    if quick_block:
        quick_x, quick_y = int(quick_block["cx"]), int(quick_block["cy"])
        print(f"  🎯 点击【快速领】进入任务列表 @({quick_x},{quick_y})")
        d.click(quick_x, quick_y)
        time.sleep(2)
        blocks = _scan_shopping_page()
        if not blocks:
            print("  ⚠️ 点击【快速领】后 OCR 无结果")
            return []
    else:
        print("  ℹ️ 当前页面没有【快速领】，继续检查任务列表入口")

    more_block = _find_show_more_block(blocks)
    if more_block is not None:
        more_x, more_y = int(more_block["cx"]), int(more_block["cy"])
        print(f"  🔽 点击【查看更多】展开完整任务列表 @({more_x},{more_y})")
        d.click(more_x, more_y)
        time.sleep(2)
        blocks = _scan_shopping_page()
        if not blocks:
            print("  ⚠️ 展开任务列表后 OCR 无结果")
            return []

    return blocks


def navigate():
    """进点淘 → 元宝中心 → 购物入口 → 立即签到 → 快速领 → 任务列表"""
    print("🔄 导航中...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    if not click_ocr_text(
        d, ["元宝中心", "赚元宝"], settle_seconds=2.5,
        attempts=4, log=True,
    ):
        return False
    time.sleep(3)
    if not handle_yuanbao_live_popups(d):
        return False

    enter_shopping_tasks()
    time.sleep(3)

    blocks = unlock_shopping_task_list()
    if not blocks:
        print("  ⚠️ 无法读取购物任务页，停止导航")
        return False
    if not _shopping_task_list_ready(blocks):
        print("  ⚠️ 未确认进入购物任务列表，停止导航")
        return False
    print("  ✅ 导航完成")
    return True


def browse(seconds):
    """滑动浏览：使用通用 browse_task"""
    print(f"  📋 滑动浏览 {seconds}s")
    browse_task(d, min_duration=seconds, max_wait=seconds + 10)


def classify_media_page(blocks):
    """根据点击后的 OCR 文本判定实际落地页，无法确认时返回 None。"""
    all_text = " ".join(
        str(block.get("text", "")).strip()
        for block in blocks
        if block.get("text")
    )

    video_score = 0
    if "上滑查看更多视频" in all_text or "查看更多视频" in all_text:
        video_score += 5
    if "发放奖励" in all_text:
        video_score += 4
    if "查看详情" in all_text:
        video_score += 1

    live_score = 0
    if "直播间" in all_text:
        live_score += 5
    if "说点什么" in all_text:
        live_score += 4
    if "秒后完成" in all_text:
        live_score += 3
    if "关注" in all_text:
        live_score += 1

    if video_score >= live_score + 2:
        return "video"
    if live_score >= video_score + 2:
        return "live"
    return None


def detect_entered_media_type(task_type, task_name):
    """点击后用 OCR 判定实际页面类型，并在不确定时使用安全兜底。"""
    for attempt in range(2):
        blocks = get_ocr_text_blocks(d)
        detected = classify_media_page(blocks)
        if detected:
            print(f"  🔎 点击后 OCR 判定实际页面: {detected}")
            return detected
        if attempt == 0:
            time.sleep(1)

    fallback = task_type
    if task_type == "video":
        fallback = SHOPPING_TASK_ROUTE_OVERRIDE_MAP.get(
            normalize_task_name(task_name), task_type
        )
    print(f"  ⚠️ 点击后 OCR 无法确认页面，使用兜底路由: {fallback}")
    return fallback


def do_task(tp, sec, task_name=""):
    """执行任务（已点击进入，根据类型执行）"""
    if tp == "click" or is_blocked_boutique_task(task_name):
        print('  ⊘ 已屏蔽商品三次点击任务，跳过')
        return False

    time.sleep(3)
    check_can_open(d)

    actual_tp = tp
    if tp in ("video", "live"):
        actual_tp = detect_entered_media_type(tp, task_name)
        print(f"  📋 点击后实际类型: {actual_tp}（任务名预判: {tp}）")

    # 计时起点必须晚于页面稳定和 OCR 判定，不能把入口加载时间算进任务时长。
    task_started_at = time.time()

    if actual_tp == "guess":
        handle_guess_price_game(d, search_first=is_search_low_price_task(task_name))
        # 猜价格完成后：handle_guess_price_game 已处理退出，直接找"查看更多"或确认在任务列表
        time.sleep(1)
        for _ in range(5):
            bs = get_ocr_text_blocks(d)
            for b in bs:
                if "查看更多" in b.get("text", ""):
                    d.click(b["cx"], b["cy"])
                    print(f"  🔽 猜价格后重新点击【查看更多】@({b['cx']},{b['cy']})")
                    time.sleep(2)
                    return task_started_at
            if any("去完成" in b.get("text", "") or "去浏览" in b.get("text", "") for b in bs):
                return task_started_at
            d.press("back")
            time.sleep(1)
        return task_started_at
    elif actual_tp == "video":
        print(f"  🎬 看视频 {sec}s")
        # 此脚本在 do_task 开头已经完成点击后媒体页识别，避免公共执行器重复 OCR。
        execute_video_task(
            d, task_name, 0, 0,
            duration=sec,
            verify_media_page=False,
        )
    elif actual_tp == "live":
        raw_seconds = sec
        cache = 3 + int(time.time() % 3)
        total_wait = raw_seconds + cache
        print(f"  📺 看直播 {sec}s")
        print(f"  ⏱️ 解析={raw_seconds}s + 缓存={cache}s = 总等待 {total_wait}s")
        print(f"  ⏳ 进入直播间，停留 {total_wait} 秒（每10秒上滑一次）...")
        start = task_started_at
        last_swipe = 0
        has_countdown = False
        while time.time() - start < total_wait:
            elapsed = int(time.time() - start)
            remain = total_wait - elapsed
            if elapsed >= last_swipe + 10:
                swipe_x = random.randint(int(screen_width * 0.3), int(screen_width * 0.7))
                s_y = random.randint(int(screen_height * 0.65), int(screen_height * 0.80))
                e_y = random.randint(int(screen_height * 0.15), int(screen_height * 0.30))
                try:
                    d.swipe(swipe_x, s_y, swipe_x, e_y, duration=random.uniform(0.4, 0.6))
                except:
                    pass
                last_swipe = elapsed
                print(f"    已过 {elapsed}s / 剩余 {remain}s，上滑一次 ⬆️")
                time.sleep(1)

            # 每5秒OCR检测完成状态
            if elapsed % 5 < 2:
                try:
                    bs = get_ocr_text_blocks(d)
                    countdown_text = None
                    for b in bs:
                        if re.search(r"\d+秒后完成", b.get("text", "")):
                            countdown_text = b["text"]
                            break
                    if countdown_text:
                        if not has_countdown:
                            print(f"    🔍 检测到倒计时【{countdown_text}】，等待字样消失...")
                            has_countdown = True
                    elif has_countdown:
                        print("    倒计时暂未识别，继续满足规定停留时间")
                    elif any(any(kw in b.get("text", "") for kw in ["即将完成", "已完成", "恭喜"]) for b in bs):
                        print("    检测到完成提示，继续满足规定停留时间")
                except:
                    pass
            print(f"    已过 {elapsed}s / 剩余 {remain}s，直播间挂机中...")
            time.sleep(5)
        print(f"  ✅ 直播等待完成，共等待 {int(time.time() - start)}s")
    elif tp == "browse":
        browse(sec)

    back_to_task(d, target_keywords=TASK_BTN_KW)
    return task_started_at


# 页面标题黑名单（这些是页面大标题/导航文字，绝非任务名）
_TITLE_BLACKLIST = ["做任务", "领元宝", "任务列表", "赚钱购物",
                    "合成百元大红包", "元宝中心", "今日任务"]


def normalize_task_name(text: str) -> str:
    """去掉 OCR 任务名中的进度、奖励和时长后缀。"""
    normalized = re.sub(r"\s+", "", text)
    normalized = re.sub(r"\+\d+$", "", normalized)
    normalized = re.sub(r"[\(（]\d+/\d+[\)）]$", "", normalized)
    normalized = re.sub(r"\+\d+$", "", normalized)
    return re.sub(r"\d+(?:秒|s|分钟)$", "", normalized, flags=re.IGNORECASE)


def detect_task_name_before_click(bs, btn_block):
    """点击'去完成'之前，从同一行左侧提取任务名，预判任务类型
    注意：使用全量OCR文本块，_TITLE_BLACKLIST 负责过滤标题级文字

    参数:
        bs: 所有OCR文本块
        btn_block: "去完成"/"去浏览"按钮的OCR块
    返回:
        (tp, sec, task_name)；商品三次点击返回 skip。
    """
    cx, cy = btn_block["cx"], btn_block["cy"]
    same_row = []
    above_row = []
    for nb in bs:
        txt = nb.get("text", "")
        if not txt or "去完成" in txt or "去浏览" in txt:
            continue
        dy = cy - nb["cy"]
        dx = abs(nb["cx"] - cx)

        if -30 <= dy <= 30 and 0 < (cx - nb["cx"]) < 800:
            same_row.append((cx - nb["cx"], txt))
        elif 10 < dy <= 300 and dx < 800:
            above_row.append((abs(dy), txt))

    if same_row:
        same_row.sort(key=lambda x: x[0], reverse=True)
        real_parts = [t for _, t in same_row if not re.match(r'^[+＋]\d+$', t.strip())]
        task_name = " ".join(real_parts) if real_parts else same_row[0][1]
    elif above_row:
        above_row.sort(key=lambda x: x[0])
        task_name = above_row[0][1]
    else:
        print(f"  ⚠️ 附近未找到任务名文本，默认 browse 60s")
        return "browse", 60, ""

    # 黑名单必须先于任何任务类型判断，否则附近的“浏览/滑动”文字
    # 会让危险任务提前返回 browse，进而绕过黑名单并触发点击。
    if is_blacklisted(task_name):
        print(f"  ⊘ 黑名单跳过: 【{task_name}】")
        return "skip", 0, task_name

    if any(kw in task_name for kw in _TITLE_BLACKLIST):
        print(f"  ⊘ 过滤标题: [{task_name}]，默认 browse 60s")
        return "browse", 60, task_name

    print(f"  📝 检测到任务名: [{task_name}]")

    all_nearby_texts = [t for _, t in same_row] + [t for _, t in above_row]

    if "猜价格" in task_name or is_search_low_price_task(task_name):
        return "guess", 0, task_name
    if "看视频" in task_name:
        sec = 60
        m = re.search(r"(\d+)\s*秒", task_name)
        if m:
            sec = int(m.group(1))
        return "video", sec, task_name
    if "直播" in task_name or "直播间" in task_name:
        sec = 30 if ("30" in task_name or "幸运" in task_name) else 60
        return "live", sec, task_name
    if "查看" in task_name:
        return "skip", 0, task_name

    if "浏览" in task_name or "滑动" in task_name:
        sec = 60
        m = re.search(r"(\d+)\s*秒", task_name)
        if m:
            sec = int(m.group(1))
        return "browse", sec, task_name

    for t in all_nearby_texts:
        if "猜价格" in t:
            return "guess", 0, task_name
        if "直播" in t or "直播间" in t:
            sec = 30 if ("30" in t or "幸运" in t) else 60
            return "live", sec, task_name
        if "查看" in t:
            return "skip", 0, task_name

    is_browse = any("浏览" in t or "滑动" in t for t in all_nearby_texts)
    if is_browse:
        sec = 60
        m = re.search(r"(\d+)\s*秒", task_name)
        if m:
            sec = int(m.group(1))
        return "browse", sec, task_name

    print(f"  ⚠️ 附近文本[{task_name}]未匹配已知类型，默认 browse 60s")
    return "browse", 60, task_name


def detect_task_type():
    """OCR检测当前页面是什么任务类型（进入页面后的兜底检测）"""
    bs = get_ocr_text_blocks(d)
    print(f"\n  📝 OCR识别到的文字块 ({len(bs)} 个):")
    for b in bs:
        print(f"    [{b['text']}] @({b['cx']},{b['cy']})")

    all_text = " ".join(b.get("text", "") for b in bs)

    if "猜价格" in all_text:
        print(f"  ✅ 匹配 → guess")
        return "guess", 0
    if "直播" in all_text:
        sec = 30 if ("30" in all_text or "幸运" in all_text) else 60
        print(f"  ✅ 匹配 → live {sec}s")
        return "live", sec
    if "查看" in all_text:
        print(f"  ✅ 匹配 → click")
        return "click", 0
    if "浏览" in all_text or "滑动" in all_text:
        print(f"  ✅ 匹配 → browse 60s")
        return "browse", 60

    print(f"  ⚠️ 未匹配到已知任务类型，默认 browse 60s")
    return "browse", 60


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🛒 点淘购物赚钱")
    print("=" * 50)

    show_dev_guide()  # 启动时显示开发规范摘要

    if not navigate():
        print("⚠️ OCR 导航未完成，停止购物赚钱任务")
        return
    total = 0

    print("\n🔍 OCR扫描去完成/去浏览按钮...")
    while total < MAX_TASKS:
        time.sleep(SCAN_INTERVAL)

        # ── OCR 检测是否仍有任务按钮 ──
        bs = get_ocr_text_blocks(d, retries=0)
        if not bs:
            print("  ⚠️ OCR未返回任何文本块")
            break

        buttons = _task_buttons(bs)
        if not buttons:
            # 列表可能被折叠回【查看更多】：优先展开后重扫，再判定完成。
            more_block = _find_show_more_block(bs)
            if more_block is not None:
                print(f"  🔽 任务列表折叠，重新展开 @({more_block['cx']},{more_block['cy']})")
                d.click(more_block["cx"], more_block["cy"])
                time.sleep(2)
                bs = get_ocr_text_blocks(d, retries=0)
                buttons = _task_buttons(bs)
            if not buttons:
                print(f"  🎉 已做{total}个，OCR未检测到可执行按钮（可能全部完成）")
                break

        print(f"  📋 OCR扫描 {len(bs)} 个文本块，查找去完成/去浏览...")

        clicked = False
        for b in buttons:
            text = b.get("text", "")
            cx, cy = b["cx"], b["cy"]
            tp, sec, task_name = detect_task_name_before_click(bs, b)
            type_names = {"guess": "猜价格", "live": "直播", "video": "视频", "browse": "浏览", "click": "点3次"}
            if tp == "skip":
                print(f"  ⏭️ 跳过黑名单任务: [{task_name}]")
                continue
            # 点击前的最终安全闸门：即使后续分类逻辑发生变化，
            # 已识别出的黑名单任务也不能进入任何任务页面。
            if task_name and is_blacklisted(task_name):
                print(f"  ⏭️ 点击前拦截黑名单任务: [{task_name}]")
                continue
            print(f"  📋 预判 → {type_names.get(tp, tp)} {sec}s (任务名: [{task_name}])")

            print(f"  🎯 OCR点击【{text}】@({cx},{cy})")
            report_task_found()
            d.click(cx, cy)
            total += 1
            clicked = True
            break

        if not clicked:
            print("  ⚠️ OCR未找到去完成/去浏览按钮")
            break

        task_started_at = do_task(tp, sec, task_name)
        elapsed = int(time.time() - task_started_at)
        print(f"  ⏱️ 任务耗时 {elapsed}s")

        # 任务退出后，使用统一的 back_to_task 恢复页面
        time.sleep(1)
        if not back_to_task(d, target_keywords=TASK_BTN_KW, max_attempts=MAX_BACK_ATTEMPTS):
            # back_to_task 失败 → 可能回到元宝中心首页
            bs_after = get_ocr_text_blocks(d)
            if any("元宝中心" in b.get("text", "") for b in bs_after):
                print("  ⚠️ 回到元宝中心首页，重新进入任务列表")
                enter_shopping_tasks()
                time.sleep(3)
                bs_new = get_ocr_text_blocks(d)
                more_block = _find_show_more_block(bs_new)
                if more_block is not None:
                    d.click(more_block["cx"], more_block["cy"])
                    print(f"  ✅ 重新点击查看更多 @({more_block['cx']},{more_block['cy']})")
                    time.sleep(2)

        print(f"  ✅ 完成第 {total}/{MAX_TASKS} 个任务\n")

    print(f"\n📊 共完成 {total} 个任务")
    report_round_finished()
    try:
        show_today_summary()
    except Exception:
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
        print(" 结束")
