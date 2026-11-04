"""
签到赢元宝 自动化脚本
流程：进点淘 → 元宝中心 → 下滑找今日签到 → 去看看 → 自动执行任务
"""
from task_exclusions import is_blocked_boutique_task
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
from task_recheck import report_task_found, report_round_finished
import re

from u2_scanner import connect_device, DT_APP, click_u2_control
from u2_scanner import handle_guess_price_game, execute_live_task, execute_video_task
from u2_scanner import click_three_selected_items, is_search_low_price_task
from u2_scanner import execute_browse_task
from utils import get_ocr_text_blocks, show_today_summary, browse_task

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

MAX_SCROLL = 9
NAME_ABOVE_GAP = 200  # 任务名在"去完成"上方，最大垂直间距
NAME_H_OFFSET = 50    # 任务名与"去完成"水平方向最大偏移（缩小防误抓邻近文本）

# 黑名单关键词（避免误点元宝商城下单、邀请等危险任务）
BLACKLIST_KEYWORDS = [
    "元宝商城", "下单", "购买", "充值", "付款",
    "邀请", "邀请好友", "拉好友", "助力", "分享", "去邀请",
    "喂鸡", "蚂蚁庄园", "蚂蚁新村", "饿了么", "果园",
    "消消乐", "农场小游戏", "斗地主", "开心消消乐",
    "猜谜", "猜一猜",
    "查看并完成", "玩小游戏",
    # 用户要求屏蔽“查看3个精选宝贝 / 看3个精选宝贝 / 精选宝贝”整类任务。
    "精选宝贝",
]

# 去完成按钮关键词
DONE_BTN_KEYWORDS = ["去完成", "去浏览", "去观看", "去参与", "去领取", "去签到", "立即参与", "立即领取"]


def navigate():
    """进点淘 → 元宝中心"""
    print("🔄 导航到元宝中心...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    for _ in range(10):
        for text in ["元宝中心", "赚元宝"]:
            btn = d(textContains=text)
            if btn.exists(timeout=2):
                click_u2_control(d, btn, text)
                time.sleep(4)
                return handle_yuanbao_live_popups(d)
        time.sleep(2)

    d.click(int(screen_width * 0.7), int(screen_height * 0.93))
    time.sleep(4)
    return False


def find_sign():
    """下滑找今日签到 → 点去看看（U2 textContains）"""
    print("🔄 下滑找今日签到...")
    for _ in range(MAX_SCROLL):
        sign_btn = d(textContains="今日签到")
        if sign_btn.exists(timeout=2):
            try:
                sign_cx, sign_cy = sign_btn.center()
                sign_cx, sign_cy = int(sign_cx), int(sign_cy)
            except:
                continue

            go_btn = d(textContains="去看看")
            if go_btn.exists(timeout=1):
                try:
                    gx, gy = go_btn.center()
                    gx, gy = int(gx), int(gy)
                except:
                    gx, gy = 0, 0
                if abs(gx - sign_cx) > 50 and -20 <= gy - sign_cy <= 200:
                    d.click(gx, gy)
                    print(f"  ✅ 点击去看看")
                    time.sleep(4)
                    return True

            # 兜底：点签到右侧（盲点）
            d.click(sign_cx + 200, sign_cy)
            time.sleep(4)
            return True

        d.swipe(screen_width // 2, int(screen_height * 0.85),
                screen_width // 2, int(screen_height * 0.2))
        time.sleep(2)

    print("  ⚠️ 未找到今日签到")
    return False


# ========================================================
# 任务解析（单次 OCR → 任务列表）
# ========================================================


def find_money_card() -> bool:
    """在元宝中心下滑查找赚钱卡，优先 U2，随后 OCR。"""
    for _ in range(MAX_SCROLL):
        entry = d(text="赚钱卡")
        if entry.exists(timeout=2):
            click_u2_control(d, entry, "赚钱卡")
            time.sleep(4)
            return True
        for block in get_ocr_text_blocks(d) or []:
            if block["text"].strip() == "赚钱卡":
                d.click(int(block["cx"]), int(block["cy"]))
                time.sleep(4)
                return True
        d.swipe(screen_width // 2, int(screen_height * 0.85),
                screen_width // 2, int(screen_height * 0.2))
        time.sleep(2)
    print("未找到赚钱卡入口")
    return False


def select_entry_tasks(
    tasks: list[tuple[str, str, int, int]], entry_name: str,
) -> list[tuple[str, str, int, int]]:
    """赚钱卡仅执行用户指定的三类活动。"""
    if entry_name == "赚钱卡":
        return [task for task in tasks if task[0] in {"live", "browse", "guess_price"}]
    return tasks


def parse_seconds(text, default=60):
    """从任务名提取秒数，如"看直播60秒"→60"""
    m = re.search(r"(\d+)\s*秒", text)
    return int(m.group(1)) if m else default


def match_task_type(text):
    """精确匹配任务名 → 返回类型字符串"""
    if "猜价格" in text or is_search_low_price_task(text):
        return "guess_price"
    if "浏览" in text or "精选推荐" in text or "猜你喜欢" in text \
       or "省钱" in text or "上新好物" in text or "签到" in text \
       or "查看" in text:
        return "browse"
    if "看视频" in text:
        return "video"
    if "直播" in text:
        return "live"
    return "other"


def detect_sign_task_name(blocks, btn_block):
    """签到任务专用：从'去完成'按钮正上方同一列提取真实任务名
    布局：
        任务名          @(225,2509)   ← 目标（玩1次猜价格/看福利直播30秒/看直播60秒）
        额外得XX元宝    @(225,2560)   ← 过滤掉
        (0/15)          @(1021,2600)  ← 过滤掉
        去完成           @(226,2677)   ← 按钮
    参数:
        blocks: OCR文本块列表
        btn_block: "去完成"按钮的OCR块
    返回:
        task_name: 真实任务名字符串，未找到返回""
    """
    cx, cy = btn_block["cx"], btn_block["cy"]
    # 只取正上方同一列的文本（水平偏移 < 50px，垂直间距 < 200px）
    above = []
    for nb in blocks:
        if nb["cy"] >= cy:
            continue
        vdist = cy - nb["cy"]
        if vdist > NAME_ABOVE_GAP:
            continue
        hdist = abs(nb["cx"] - cx)
        if hdist > NAME_H_OFFSET:
            continue
        above.append((vdist, nb["text"]))

    above.sort(key=lambda x: x[0])  # 按垂直距离排序（最近优先）

    # 过滤干扰文本，取第一个非干扰文本作为任务名
    for _, t in above:
        # 跳过奖励/进度干扰
        if "额外得" in t or "元宝" in t or "金币" in t:
            continue
        if re.match(r'^\(\d+/\d+\)$', t.strip()):
            continue
        return t  # 第一个非干扰文本 = 真实任务名

    # 全部被过滤掉，回退到最近的文本
    return above[0][1] if above else ""


def parse_money_card_tasks(blocks):
    """赚钱卡完整名称配对：同一行右侧按钮，或同一列下方按钮。"""
    known_names = {"看直播60秒": "live", "浏览精选推荐60秒": "browse"}
    names = []
    buttons = []
    for block in blocks:
        text = re.sub(r"[\s\u200b\ufeff]+", "", block["text"])
        text = re.sub(r"[（(]\d+/\d+[）)]$", "", text)
        if text in known_names:
            names.append((text, block))
        elif any(keyword in text for keyword in DONE_BTN_KEYWORDS):
            buttons.append(block)

    tasks = []
    used_names = set()
    for button in buttons:
        nearby = []
        for index, (name, block) in enumerate(names):
            if index in used_names:
                continue
            dx = button["cx"] - block["cx"]
            dy = button["cy"] - block["cy"]
            if dx > NAME_H_OFFSET and abs(dy) <= 60:
                nearby.append((abs(dy), index, name))
            elif abs(dx) <= NAME_H_OFFSET and 0 < dy <= NAME_ABOVE_GAP:
                nearby.append((dy, index, name))
        nearby.sort()
        if not nearby:
            continue
        if len(nearby) > 1 and nearby[0][0] == nearby[1][0]:
            continue
        _, index, name = nearby[0]
        used_names.add(index)
        tasks.append((known_names[name], name, int(button["cx"]), int(button["cy"])))

    # 保留赚钱卡原有猜价格支持，不扩展其他未知活动。
    used_buttons = {(task[2], task[3]) for task in tasks}
    tasks.extend(task for task in parse_tasks_from_blocks(blocks)
                 if task[0] == "guess_price" and (task[2], task[3]) not in used_buttons)
    priority = {"guess_price": 0, "browse": 1, "live": 3}
    tasks.sort(key=lambda task: priority[task[0]])
    return tasks


def parse_tasks_from_blocks(blocks, entry_name="签到赢元宝"):
    """从 OCR 文本块中解析出所有可执行的签到任务
    返回: [(task_type, task_name, btn_cx, btn_cy), ...]
          按优先级排序：猜价格 > 浏览 > 直播
    """
    if entry_name == "赚钱卡":
        return parse_money_card_tasks(blocks)

    candidates = []
    for b in blocks:
        text = b["text"]
        if not any(kw in text for kw in DONE_BTN_KEYWORDS):
            continue

        task_name = detect_sign_task_name(blocks, b)
        if not task_name:
            task_name = text

        # 黑名单过滤
        if any(kw in task_name for kw in BLACKLIST_KEYWORDS):
            print(f"    ⊘ 黑名单跳过: 【{task_name}】")
            continue

        task_type = match_task_type(task_name)
        if task_type == "other":
            continue

        priority = {"guess_price": 0, "browse": 1, "video": 2, "live": 3}[task_type]
        candidates.append((priority, task_type, task_name, int(b["cx"]), int(b["cy"])))

    # 按优先级排序
    candidates.sort(key=lambda x: x[0])
    # 返回简化的元组（去掉 priority）
    return [(t[1], t[2], t[3], t[4]) for t in candidates]


# ========================================================
# 任务执行
# ========================================================


MILESTONE_STEPS = {5, 15, 26}

def get_progress_number(blocks):
    """从 OCR 文本块中提取"已前进步数"的当前值，返回整数或 None"""
    for b in blocks:
        text = b["text"]
        if "前进步数" in text:
            # 尝试 "26/30" 格式
            m = re.search(r"(\d+)\s*/\s*\d+", text)
            if m:
                return int(m.group(1))
            # 尝试纯数字格式
            m = re.search(r"(\d+)", text)
            if m:
                return int(m.group(1))
    return None


def check_and_handle_popup(d, blocks):
    """
    检查当前页面是否有里程碑弹窗，有则关闭。
    策略：
    1. 先在传入的 blocks 里找"继续做任务"
    2. 找不到但前进步数 ∈ MILESTONE_STEPS → 专门扫一次
    返回 True 表示处理了弹窗，调用方需重新扫描 blocks
    """
    # 1. 在当前 blocks 里直接找
    for b in blocks:
        if "继续做任务" in b.get("text", ""):
            print(f"  🎯 检测到里程碑弹窗，点击继续做任务")
            d.click(b["cx"], b["cy"])
            time.sleep(2)
            return True

    # 2. 通过前进步数判断是否需要专门扫描
    progress = get_progress_number(blocks)
    if progress in MILESTONE_STEPS:
        print(f"  🚩 前进步数到达里程碑 {progress}，专门扫描弹窗按钮...")
        popup_blocks = get_ocr_text_blocks(d)
        if popup_blocks:
            for b in popup_blocks:
                if "继续做任务" in b.get("text", ""):
                    print(f"  🎯 找到弹窗按钮，点击继续做任务")
                    d.click(b["cx"], b["cy"])
                    time.sleep(2)
                    return True
            print(f"  ⚠️ 前进步数={progress} 但未找到弹窗按钮（可能已消失或OCR漏检）")
        else:
            print(f"  ⚠️ 专门扫描弹窗时 OCR 无结果")

    return False


def detect_milestone_number(blocks):
    """扫描 OCR 文本块，检测里程碑进度值"""
    for b in blocks:
        text = b["text"]
        if "前进步数" in text or "任务进度" in text or "任务" in text:
            m = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if m:
                current = int(m.group(1))
                total = int(m.group(2))
                print(f"  📊 检测到里程碑进度: {current}/{total}")
                if current in {4, 14, 25}:
                    return current
        if "签到" in text or "进度" in text:
            m = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if m:
                current = int(m.group(1))
                print(f"  📊 检测到签到进度: {current}/{m.group(2)}")
                if current in {4, 14, 25}:
                    return current
    return None


def handle_milestone_close():
    """关闭里程碑弹窗"""
    print(f"  🎯 里程碑弹窗 → 点击继续做任务 @(654, 2031)")
    time.sleep(2)
    d.click(654, 2031)
    time.sleep(2)


def exec_guess_price(btn_cx, btn_cy, task_name=""):
    """执行猜价格任务"""
    print("  🎯 猜价格游戏")
    d.click(btn_cx, btn_cy)
    time.sleep(3)
    return handle_guess_price_game(d, search_first=is_search_low_price_task(task_name))


def exec_browse(btn_cx, btn_cy, task_name="", use_ocr=True):
    """执行精选推荐浏览任务"""
    if is_blocked_boutique_task(task_name):
        print('  ⊘ 已屏蔽精选宝贝任务，跳过')
        return False

    if "查看" in task_name and ("宝贝" in task_name or "精选" in task_name):
        print("  🎯 查看精选宝贝（点击3个商品）")
        d.click(btn_cx, btn_cy)
        time.sleep(0.5)
        click_three_selected_items(d)
        # click_three_selected_items 从详情退到列表页，
        # 再按一次 back 从列表页退回签到任务主页
        d.press("back")
        time.sleep(1.5)
        return

    print("  🎯 精选推荐浏览")
    d.click(btn_cx, btn_cy)
    time.sleep(0.5)

    min_sec = parse_seconds(task_name, default=90)
    try:
        executor = browse_task if use_ocr else execute_browse_task
        executor(d, min_duration=min_sec, max_wait=min_sec + 30)
    finally:
        print("  ↩️ 浏览结束，返回任务页")
        d.press("back")
        time.sleep(2)


def exec_live(btn_cx, btn_cy, task_name=""):
    """执行看直播任务"""
    print("  🎯 看直播")
    d.click(btn_cx, btn_cy)
    time.sleep(0.5)
    try:
        execute_live_task(d, task_name, btn_cx, btn_cy)
    finally:
        print("  ↩️ 直播结束，返回任务页")
        d.press("back")
        time.sleep(1.5)


def exec_video(btn_cx, btn_cy, task_name=""):
    """执行看视频任务（翻页风格滑动，需多下滑一次）"""
    print("  🎯 看视频")
    # 首滑由公共视频执行器在确认真实媒体页后执行
    d.click(btn_cx, btn_cy)
    execute_video_task(
        d, task_name, btn_cx, btn_cy,
        verify_media_page=True,
        initial_swipe=True,
        initial_swipe_start_ratio=0.85,
    )


# ========================================================
# 主执行流程
# ========================================================


def execute_tasks(task_list):
    """
    依次执行签到任务列表（不再重新 OCR 扫描）
    参数:
        task_list: [(task_type, task_name, btn_cx, btn_cy), ...]
    """
    if not task_list:
        print("  📭 无可执行的签到任务")
        return 0

    print(f"\n  📋 本次共扫描到 {len(task_list)} 个任务:")
    for i, (ttype, tname, _, _) in enumerate(task_list, 1):
        print(f"    {i}. [{ttype}] {tname}")

    total = 0
    milestone_triggered = None

    for task_type, task_name, btn_cx, btn_cy in task_list:
        # OCR 检查里程碑（轻量单次扫描，失败不影响流程）
        milestone_blocks = get_ocr_text_blocks(d)
        if milestone_blocks:
            current_ms = detect_milestone_number(milestone_blocks)
            if current_ms:
                milestone_triggered = current_ms
                print(f"  🚩 里程碑触发: {current_ms}，任务完成后将关闭弹窗")

        print(f"\n  ▶️ [{total+1}/{len(task_list)}] {task_name}")

        if task_type == "guess_price":
            exec_guess_price(btn_cx, btn_cy, task_name)
        elif task_type == "browse":
            exec_browse(btn_cx, btn_cy, task_name)
        elif task_type == "video":
            exec_video(btn_cx, btn_cy, task_name)
        elif task_type == "live":
            exec_live(btn_cx, btn_cy, task_name)

        total += 1
        print(f"  ✅ 已完成 {total}/{len(task_list)} 个任务")

        # 里程碑弹窗关闭
        if milestone_triggered:
            handle_milestone_close()
            milestone_triggered = None

    print(f"\n📊 共执行 {total} 个签到任务")
    return total


def main(entry_name: str = "签到赢元宝") -> None:
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(f" {entry_name}")
    print("=" * 50)

    navigated = navigate()
    if entry_name == "赚钱卡":
        if not navigated or not find_money_card():
            raise RuntimeError("未能进入赚钱卡页面")
    elif not find_sign():
        print("\n⚠️ 未能进入签到页面")
        return

    print(f"\n✅ 已进入{entry_name}任务页面")

    # 单次 OCR：截图 → 展开查看更多 → 再截图 → 解析任务列表
    time.sleep(2)

    blocks = get_ocr_text_blocks(d)
    if not blocks:
        print("  ⚠️ 首次 OCR 失败，无法获取任务信息")
        return

    # 点击"查看更多"展开完整任务列表
    for b in blocks:
        if "查看更多" in b.get("text", ""):
            d.click(b["cx"], b["cy"])
            print(f"  ✅ 点击查看更多 @({b['cx']},{b['cy']})")
            time.sleep(3)
            blocks = get_ocr_text_blocks(d)
            if not blocks:
                print("  ⚠️ 展开后 OCR 失败，使用展开前数据")
            break

    # 每次只执行一个任务 → 重新扫描 → 继续，直到页面无"去完成"按钮
    total_all = 0
    first_round = True
    ocr_empty_retries = 0
    while True:
        if entry_name == "赚钱卡" and total_all >= 60:
            print("赚钱卡达到本次 60 次执行上限，停止")
            break
        if first_round:
            # 第一轮复用首次扫描的数据，避免立即重复截图导致服务器异常
            first_round = False
            print(f"\n🔍 使用首次扫描数据 (已成功{total_all}个)...")
        else:
            time.sleep(1.5)
            print(f"\n🔍 扫描中 (已成功{total_all}个)...")
            blocks = get_ocr_text_blocks(d)
            if not blocks:
                # 服务器偶发 crash → 等几秒重试，不要立刻 break
                ocr_empty_retries += 1
                if ocr_empty_retries <= 3:
                    wait = 3.0 * ocr_empty_retries
                    print(f"  ⚠️ OCR 无结果（第 {ocr_empty_retries} 次），{wait}s 后重试...")
                    time.sleep(wait)
                    continue
                print("  ⚠️ OCR 多次无结果，结束")
                break

        # 检查并关闭里程碑弹窗（通过前进步数判断是否需要专门扫描）
        if entry_name != "赚钱卡" and check_and_handle_popup(d, blocks):
            time.sleep(1)
            blocks = get_ocr_text_blocks(d)
            if not blocks:
                print("  ⚠️ 弹窗关闭后 OCR 失败，重试...")
                time.sleep(2)
                blocks = get_ocr_text_blocks(d)
            if not blocks:
                print("  ⚠️ 无法获取任务列表，跳过本轮")
                continue

        task_list = select_entry_tasks(parse_tasks_from_blocks(blocks, entry_name), entry_name)
        if not task_list:
            # OCR 有结果但无"去完成"按钮 → 尝试多扫一轮（可能只是页面抖动）
            ocr_empty_retries += 1
            if ocr_empty_retries <= 2:
                wait = 2.0 * ocr_empty_retries
                print(f"  ⚠️ 页面无任务按钮（第 {ocr_empty_retries} 次），{wait}s 后重扫...")
                time.sleep(wait)
                continue
            print("  页面未识别到可执行任务，结束本次扫描")
            break

        # 有任务 → 重置空结果计数器
        ocr_empty_retries = 0

        print(f"  📋 本轮找到 {len(task_list)} 个任务，执行第一个...")

        # 只执行第一个"去完成"任务
        task_type, task_name, btn_cx, btn_cy = task_list[0]
        report_task_found()
        print(f"\n  ▶️ [{total_all+1}] {task_name}")

        if task_type == "guess_price":
            exec_guess_price(btn_cx, btn_cy, task_name)
        elif task_type == "browse":
            exec_browse(btn_cx, btn_cy, task_name, use_ocr=entry_name != "赚钱卡")
        elif task_type == "video":
            exec_video(btn_cx, btn_cy, task_name)
        elif task_type == "live":
            exec_live(btn_cx, btn_cy, task_name)

        total_all += 1
        print(f"  ✅ 已完成: {total_all} 个任务")

    print(f"\n📊 共执行 {total_all} 个{entry_name}任务")
    report_round_finished()

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
