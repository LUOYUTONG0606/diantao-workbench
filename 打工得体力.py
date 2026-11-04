"""
打工得体力 自动化脚本
流程：进点淘 → 打工赚元宝 → 得体力 → 扫描执行任务
参照签到赢元宝.py 的整体逻辑和写法（OCR 扫描 + 循环执行）
"""
from task_exclusions import is_blocked_boutique_task
import time
import sys
from task_recheck import report_task_found, report_round_finished
import re

from u2_scanner import (
    connect_device, DT_APP,
    navigate_to_dagong_page,
    execute_browse_task, execute_live_task,
    execute_video_task,
    handle_guess_price_game, click_three_selected_items,
    u2_click_text,
)
from utils import get_ocr_text_blocks, log_task_completed, show_today_summary

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

# 🔧 关闭 u2 触摸调试覆盖层（若开启），否则覆盖层会挡住所有 UI 内容
# 特征：屏幕顶部显示 P:0/1, dx:0.0, dY:0.0, Xv:0.0, Yv:0.0, Prs, Size 等调试数据
try:
    d.settings["uiautomator"] = {}
    d.settings["operation_delay"] = 0.5
    print("  [设置] 已关闭 uiautomator 调试覆盖层")
except Exception:
    pass
try:
    d.settings.pop("debug", None)
except Exception:
    pass
# 禁用设备级指针位置 + 显示触摸操作（开发者选项覆盖层）
try:
    d.shell("settings put system pointer_location 0")
except Exception:
    pass
try:
    d.shell("settings put system show_touches 0")
except Exception:
    pass

MAX_SCROLL = 9
NAME_ABOVE_GAP = 200  # 任务名在"去完成"上方，最大垂直间距
NAME_H_OFFSET = 690   # 任务名在左侧(~450px)，"去完成"按钮在右侧(~1080px)，水平间距约630px

# 黑名单关键词（避免误点危险任务）
BLACKLIST_KEYWORDS = [
    "元宝商城", "下单", "购买", "充值", "付款",
    "邀请", "邀请好友", "拉好友", "助力", "分享", "去邀请",
    "喂鸡", "蚂蚁庄园", "蚂蚁新村", "饿了么", "果园",
    "消消乐", "农场小游戏", "斗地主", "开心消消乐",
    "猜谜", "猜一猜",
    "搜索宝贝", "查看并完成", "玩小游戏",
    # 用户要求屏蔽“查看3个精选宝贝 / 看3个精选宝贝 / 精选宝贝”整类任务。
    "精选宝贝",
]

# 去完成按钮关键词
DONE_BTN_KEYWORDS = ["去完成", "去浏览", "去观看", "去参与", "去领取", "去签到", "立即参与", "立即领取"]

# 任务名映射表：先去掉时长/进度等动态后缀，再按完整名称精确匹配。
# 未收录的组合名称一律返回 other，由主循环跳过，避免误走其他执行器。
TASK_TYPE_MAP: dict[str, str] = {
    "猜价格": "guess_price",
    "猜价格游戏": "guess_price",
    "看视频": "video",
    "浏览看视频": "video",
    "看直播": "live",
    "看福利直播": "live",
    "看8点档直播": "live",
    "看黄金8点档直播": "live",
    "看黄金八点档直播": "live",
    "浏览": "browse",
    "浏览精选": "browse",
    "浏览精选推荐": "browse",
    "精选推荐": "browse",
    "猜你喜欢": "browse",
    "浏览省钱专区": "browse",
    "省钱专区": "browse",
    "浏览上新好物": "browse",
    "上新好物": "browse",
    "浏览当季上新好物": "browse",
    "签到": "browse",
    "查看": "browse",
}
TASK_CATEGORY_MAP: dict[str, str] = {
    "浏览": "browse",
    "直播": "live",
    "视频": "video",
}


def click_detili():
    """点击得体力（猜价格任务完成后需要重新进入任务列表页）"""
    print("  Step 3: 查找【得体力】...")
    time.sleep(3)
    if u2_click_text(d, "得体力"):
        print("  ✅ 点击得体力")
        time.sleep(4)
        return True
    print("  ⚠️ 未找到得体力")
    return False


def normalize_task_name(text: str) -> str:
    """去掉 OCR 任务名中的进度、奖励和时长后缀。"""
    normalized = re.sub(r"\s+", "", text)
    normalized = re.sub(r"\+\d+$", "", normalized)
    normalized = re.sub(r"[\(（]\d+/\d+[\)）]$", "", normalized)
    normalized = re.sub(r"\+\d+$", "", normalized)
    return re.sub(r"\d+(?:秒|s|分钟)$", "", normalized, flags=re.IGNORECASE)


def parse_seconds(text, default=60):
    """从任务名提取时长，兼容中文“秒”、英文“s”和“分钟”。"""
    m = re.search(r"(\d+)\s*(?:秒|s)", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*分钟", text)
    return int(m.group(1)) * 60 if m else default


def match_task_type(text):
    """按完整名称或唯一类别词映射任务类型，冲突时返回 ``other``。"""
    normalized = normalize_task_name(text)
    exact_type = TASK_TYPE_MAP.get(normalized)
    if exact_type is not None:
        return exact_type

    category_types = {
        task_type
        for category_word, task_type in TASK_CATEGORY_MAP.items()
        if category_word in normalized
    }
    if len(category_types) == 1:
        return category_types.pop()
    return "other"


def detect_task_name(blocks, btn_block):
    """从按钮上方同一列提取真实任务名（参照签到赢元宝的写法）
    布局：
        任务名              ← 目标
        额外得XX元宝        ← 过滤掉
        (0/15)              ← 过滤掉
        去完成              ← 按钮
    """
    cx, cy = btn_block["cx"], btn_block["cy"]
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
        if "额外得" in t or "元宝" in t or "金币" in t:
            continue
        if re.match(r'^\(\d+/\d+\)$', t.strip()):
            continue
        return t

    # 全部被过滤掉，回退到最近的文本
    return above[0][1] if above else ""


def parse_tasks_from_blocks(blocks):
    """从 OCR 文本块中解析出所有可执行任务
    返回: [(task_type, task_name, btn_cx, btn_cy), ...]
          按执行顺序排序：猜价格 > 浏览 > 视频 > 直播；类型本身由映射表决定
    """
    candidates = []
    for b in blocks:
        text = b["text"]
        if not any(kw in text for kw in DONE_BTN_KEYWORDS):
            continue

        task_name = detect_task_name(blocks, b)
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

    candidates.sort(key=lambda x: x[0])
    return [(t[1], t[2], t[3], t[4]) for t in candidates]


def exec_guess_price(btn_cx, btn_cy):
    """执行猜价格任务（完成后需重新点得体力进入任务列表）"""
    print("  🎯 猜价格游戏")
    d.click(btn_cx, btn_cy)
    time.sleep(3)
    handle_guess_price_game(d)
    # 猜价格完成后需要重新点击"得体力"回到任务列表页
    print("  🔄 猜价格完成，重新打开任务列表...")
    click_detili()


def exec_browse(btn_cx, btn_cy, task_name=""):
    """执行浏览任务（使用OCR智能监控，避免多等extra_seconds）"""
    if is_blocked_boutique_task(task_name):
        print('  ⊘ 已屏蔽精选宝贝任务，跳过')
        return False

    # 处理"查看精选宝贝"特殊分支
    if "查看" in task_name and ("宝贝" in task_name or "精选" in task_name):
        print("  🎯 查看精选宝贝（点击3个商品）")
        d.click(btn_cx, btn_cy)
        time.sleep(0.5)
        click_three_selected_items(d)
        d.press("back")
        time.sleep(1.5)
        return

    print("  🎯 精选推荐浏览")
    d.click(btn_cx, btn_cy)
    time.sleep(3)

    duration = parse_seconds(task_name, default=90)
    execute_browse_task(d, min_duration=duration, max_wait=duration + 30)

    d.press("back")
    time.sleep(2)


def exec_live(btn_cx, btn_cy, task_name=""):
    """执行看直播任务"""
    print("  🎯 看直播")
    d.click(btn_cx, btn_cy)
    time.sleep(0.5)
    execute_live_task(d, task_name, btn_cx, btn_cy)
    d.press("back")
    time.sleep(1.5)


def exec_video(btn_cx, btn_cy, task_name=""):
    """执行看视频任务（视频路由，不走浏览任务执行器）。"""
    duration = parse_seconds(task_name, default=60)
    # 打工得体力保留自己的时长映射：60 秒固定 7 次，其他时长沿用 5 次。
    extra_swipes = 7 if duration == 60 else 5
    print(
        f"  🎯 看视频（视频路由，{duration}s，额外下滑 {extra_swipes} 次，"
        "下滑间隔 +1s）"
    )
    d.click(btn_cx, btn_cy)
    execute_video_task(
        d, task_name, btn_cx, btn_cy,
        duration=duration,
        extra_swipes=extra_swipes,
        swipe_interval_extra_seconds=1,
        verify_media_page=True,
        initial_swipe=True,
    )


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 💪 打工得体力")
    print("=" * 50)

    navigate_to_dagong_page(d)
    click_detili()

    print("\n✅ 已进入任务列表页面")
    # ========================================================
    # 主循环（参照签到赢元宝.py）：扫描 → 执行一个 → 重新扫描 → ...
    # ========================================================
    total_all = 0
    first_round = True
    ocr_empty_retries = 0

    while True:
        # 🔧 每次扫描前确保 u2 调试覆盖层已关闭
        # 猜价格等任务执行后可能恢复覆盖层，导致 OCR 只能识别到调试数据
        try:
            d.settings["uiautomator"] = {}
            d.settings["operation_delay"] = 0.5
        except Exception:
            pass
        try:
            d.settings.pop("debug", None)
        except Exception:
            pass

        if first_round:
            # 第一轮正常扫描，避免立即重复截图导致服务器异常
            first_round = False
            print(f"\n🔍 首次扫描 (已成功{total_all}个)...")
        else:
            time.sleep(1.5)
            print(f"\n🔍 扫描中 (已成功{total_all}个)...")

        blocks = get_ocr_text_blocks(d)
        if not blocks:
            ocr_empty_retries += 1
            if ocr_empty_retries <= 3:
                wait = 3.0 * ocr_empty_retries
                print(f"  ⚠️ OCR 无结果（第 {ocr_empty_retries} 次），{wait}s 后重试...")
                time.sleep(wait)
                continue
            print("  ⚠️ OCR 多次无结果，结束")
            break

        task_list = parse_tasks_from_blocks(blocks)
        if not task_list:
            # 🐛 诊断：打印前 30 个 OCR 文本块 + 保存截图
            print(f"  🐛 诊断：OCR 返回 {len(blocks)} 个文本块，但未匹配到按钮关键词")
            print(f"  🐛 前 30 个文本块:")
            for idx, b in enumerate(blocks[:30]):
                print(f"    [{idx}] \"{b['text']}\" @({b['cx']},{b['cy']}) conf={b.get('conf',0):.2f}")
            try:
                img = d.screenshot()
                img.save("debug_ocr_no_btn.png")
                print(f"  🐛 截图已保存: debug_ocr_no_btn.png")
            except:
                pass

            ocr_empty_retries += 1
            if ocr_empty_retries <= 2:
                wait = 2.0 * ocr_empty_retries
                print(f"  ⚠️ 页面无任务按钮（第 {ocr_empty_retries} 次），{wait}s 后重扫...")
                time.sleep(wait)
                continue
            print("  ✅ 页面无任务按钮，全部完成")
            break

        # 有任务 → 重置空结果计数器
        ocr_empty_retries = 0
        print(f"  📋 本轮找到 {len(task_list)} 个任务，执行第一个...")

        # 只执行第一个"去完成"任务
        task_type, task_name, btn_cx, btn_cy = task_list[0]
        report_task_found()
        print(f"\n  ▶️ [{total_all + 1}] {task_name}")

        if task_type == "guess_price":
            exec_guess_price(btn_cx, btn_cy)
        elif task_type == "browse":
            exec_browse(btn_cx, btn_cy, task_name)
        elif task_type == "video":
            exec_video(btn_cx, btn_cy, task_name)
        elif task_type == "live":
            exec_live(btn_cx, btn_cy, task_name)

        total_all += 1
        print(f"  ✅ 已完成: {total_all} 个任务")

        try:
            log_task_completed("打工得体力", task_name)
        except:
            pass

    print(f"\n📊 共完成 {total_all} 个任务")
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
