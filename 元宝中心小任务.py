"""
元宝中心小任务 自动化脚本
流程：首页 → 元宝中心 → OCR扫描"额外任务"区域 → 执行各类任务
布局：
   额外任务  @(124, y)
   任务名    @(224, y)    去完成 @(900, y)
"""
from task_exclusions import is_blocked_boutique_task
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
from task_recheck import recheck_enabled, report_task_found, report_round_finished
import re

from u2_scanner import connect_device, DT_APP, click_u2_control
from u2_scanner import handle_guess_price_game, execute_live_task, execute_video_task
from u2_scanner import execute_browse_task
from utils import get_ocr_text_blocks, show_today_summary

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

MAX_TASKS = 30
STALE_LIMIT = 3

# 布局常量（区域扫描，不依赖固定X坐标）
EXTRA_AREA_SEARCH_BELOW = 600  # "额外"标题下方搜索范围（px）
VERTICAL_PAIR_TOLERANCE = 80   # 任务名与按钮 Y 配对容忍度

# 黑名单关键词
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


def navigate():
    """首页 → 元宝中心"""
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


def is_blacklisted(text):
    """检查任务名是否在黑名单中"""
    if not text:
        return True
    if "猜" in text and "猜价格" not in text and "猜价格游戏" not in text:
        return True
    return any(kw in text for kw in BLACKLIST_KEYWORDS)


def match_task_type(text):
    """根据任务名匹配任务类型"""
    if "猜价格" in text:
        return "guess_price"
    if "看视频" in text:
        return "video"
    if "浏览" in text or "精选推荐" in text or "猜你喜欢" in text \
       or "省钱" in text or "上新好物" in text or "签到" in text \
       or "查看" in text:
        return "browse"
    if "直播" in text:
        return "live"
    return "other"


def parse_seconds(text, default=60):
    """从任务名提取秒数"""
    m = re.search(r"(\d+)\s*秒", text)
    return int(m.group(1)) if m else default


def scan_extra_area_tasks(blocks):
    """
    OCR扫描"额外"区域，提取任务列表。
    实际布局（以OCR反馈为准）：
      额外福利  @(150,1500)  ← 标题（含"额外"）
      查看3个精选宝贝  @(471,1500)  去完成 @(1109,1500)
      ...
    策略：找到含"额外"的标题 → 在下方区域找"去完成"按钮 → 对每个按钮找同行左侧的任务名
    返回: [(task_name, btn_cx, btn_cy), ...]
    """
    # 1. 找"额外"标题（排除"额外得"、"额外返"等奖励文本）
    extra_title = None
    for b in blocks:
        txt = b["text"]
        if "额外" in txt and "额外得" not in txt and "额外返" not in txt:
            extra_title = b
            break

    if not extra_title:
        print("  ⚠️ 未找到「额外」标题")
        return []

    title_cy = extra_title["cy"]
    print(f"  📌 找到「额外」标题: 「{extra_title['text']}」 @({extra_title['cx']},{title_cy})")

    # 2. 在标题下方区域内收集"去完成"按钮
    # 搜索范围：Y 从标题Y到标题Y+600px（标题行本身也可包含按钮）
    search_ymax = title_cy + EXTRA_AREA_SEARCH_BELOW
    go_buttons = []
    for b in blocks:
        if b["cy"] < title_cy - 5:
            continue
        if b["cy"] > search_ymax:
            continue
        if b["text"] in ["去完成", "去浏览", "去观看", "去参与", "去领取", "去签到"]:
            go_buttons.append(b)

    print(f"  📌 找到 {len(go_buttons)} 个「去完成」按钮")

    if not go_buttons:
        return []

    # 3. 收集区域内所有候选文本（排除按钮本身和干扰文本）
    candidates = []
    for b in blocks:
        if b["cy"] < title_cy - 5:
            continue
        if b["cy"] > search_ymax:
            continue
        txt = b["text"]
        if len(txt) < 2:
            continue
        if "额外" in txt or "额外得" in txt or "额外返" in txt:
            continue
        if "元宝" in txt and len(txt) <= 4:
            continue
        if "去完成" in txt or "去浏览" in txt or "去观看" in txt:
            continue
        if re.match(r'^\(\d+/\d+\)$', txt.strip()):
            continue
        if re.match(r'^\d+$', txt.strip()):
            continue
        candidates.append(b)

    # 4. 配对：每个"去完成"按钮找同行左侧最近的任务名
    go_buttons.sort(key=lambda b: b["cy"])
    pairs = []
    used_names = set()

    for btn in go_buttons:
        btn_cx, btn_cy = btn["cx"], btn["cy"]
        best_name = None
        best_dist = VERTICAL_PAIR_TOLERANCE + 1

        for nc in candidates:
            if nc["text"] in used_names:
                continue
            # 任务名应在按钮左侧
            if nc["cx"] >= btn_cx:
                continue
            # 任务名应在按钮上方或同一行（Y差≤80px）
            if nc["cy"] > btn_cy + 30:
                continue
            dist = abs(nc["cy"] - btn_cy)
            if dist < best_dist:
                best_dist = dist
                best_name = nc

        if best_name and best_dist <= VERTICAL_PAIR_TOLERANCE:
            name = best_name["text"]
            used_names.add(name)
            pairs.append((name, int(btn_cx), int(btn_cy)))
            print(f"    配对: 【{name}】 → 去完成 @({btn_cx},{btn_cy}) (Y差={best_dist})")
        else:
            pairs.append((btn["text"], int(btn_cx), int(btn_cy)))
            print(f"    配对(兜底): 【{btn['text']}】 → @({btn_cx},{btn_cy})")

    return pairs


def execute_task(task_name, btn_cx, btn_cy):
    """执行单个任务"""
    if is_blocked_boutique_task(task_name):
        print('  ⊘ 已屏蔽精选宝贝任务，跳过')
        return False

    print(f"  🎯 执行: 【{task_name}】")
    task_type = match_task_type(task_name)

    if task_type == "guess_price":
        print("    类型: 猜价格游戏")
        d.click(btn_cx, btn_cy)
        time.sleep(3)
        handle_guess_price_game(d)
        # 游戏结束返回任务列表
        d.press("back")
        time.sleep(2)

    elif task_type == "browse":
        duration = parse_seconds(task_name, default=90)
        print(f"    类型: 浏览任务 ({duration}秒)")
        d.click(btn_cx, btn_cy)
        time.sleep(0.5)
        execute_browse_task(d, duration)
        # 浏览完成后返回
        d.press("back")
        time.sleep(2)

    elif task_type == "live":
        dur = parse_seconds(task_name, default=60)
        print(f"    类型: 直播观看 ({dur}秒)")
        d.click(btn_cx, btn_cy)
        time.sleep(0.5)
        execute_live_task(d, task_name, btn_cx, btn_cy)
        # 返回任务列表
        d.press("back")
        time.sleep(2)

    elif task_type == "video":
        duration = parse_seconds(task_name, default=60)
        print(f"    类型: 视频任务 ({duration}秒)")
        d.click(btn_cx, btn_cy)
        time.sleep(0.5)
        execute_video_task(
            d, task_name, btn_cx, btn_cy,
            duration=duration,
            verify_media_page=True,
        )

    else:
        print(f"    类型: 其他（等待20秒）")
        d.click(btn_cx, btn_cy)
        time.sleep(3)
        time.sleep(20)
        d.press("back")
        time.sleep(2)

    print(f"  ✅ 完成: 【{task_name}】")


def scan_and_execute():
    """主循环：OCR扫描 → 执行任务 → 重复"""
    total = 0
    prev_snap = []
    prev_empty = []

    print("\n🔍 扫描「额外任务」区域...")
    while True:
        if total >= MAX_TASKS:
            print(f"  🎉 达到最大任务数 ({MAX_TASKS})，结束")
            break

        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        if not blocks:
            print("  ⚠️ OCR未识别到任何文本")
            if is_stale_no_buttons(prev_empty, [], STALE_LIMIT):
                print("  🎉 连续3轮空白，结束")
                break
            time.sleep(2)
            continue
        pairs = scan_extra_area_tasks(blocks)
        if not pairs:
            if is_stale_no_buttons(prev_empty, [], STALE_LIMIT):
                print("  🎉 连续3轮无任务，结束")
                break
            print("  ⏳ 无任务，等待刷新...")
            time.sleep(2)
            continue
        prev_empty.clear()

        # 不再检查"连续3轮相同任务列表"，有任务就继续做
        # prev_snap 仅保留用于调试观察（不触发退出）
        current_snap = frozenset(
            (name[:15], cx // 60, cy // 60)
            for name, cx, cy in pairs
        )
        prev_snap.append(current_snap)
        if len(prev_snap) > STALE_LIMIT:
            prev_snap.pop(0)

        # 执行任务（不再去重，有任务就做）
        task = None
        for name, cx, cy in pairs:
            if not name:
                continue
            if is_blacklisted(name):
                print(f"  ⊘ 黑名单跳过: 【{name}】")
                continue
            task = (name, cx, cy)
            break

        if task:
            name, cx, cy = task
            report_task_found()
            execute_task(name, cx, cy)
            total += 1
            print(f"  📊 已完成 {total}/{MAX_TASKS}")
        else:
            if recheck_enabled():
                print("  当前屏无可执行任务，结束本轮并交由一键任务复核")
                break
            print("  当前屏无可执行任务，等待刷新...")
            time.sleep(2)

    report_round_finished()


def is_stale_no_buttons(prev_list, current_buttons, max_same=3):
    """判断连续无任务"""
    no_btn = len(current_buttons) == 0
    prev_list.append(no_btn)
    if len(prev_list) > max_same:
        prev_list.pop(0)
    if len(prev_list) >= max_same and all(prev_list):
        return True
    return False


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🪙 元宝中心小任务")
    print("=" * 50)

    if not navigate():
        print("❌ 导航到元宝中心失败")
        return

    print("\n✅ 已进入元宝中心")
    scan_and_execute()

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
