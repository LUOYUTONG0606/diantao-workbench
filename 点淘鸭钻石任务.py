"""
点淘鸭钻石任务 自动化脚本（OCR重构版）
流程：进点淘 → 元宝中心 → 固定坐标进入点淘鸭家园 → 固定坐标打开任务列表 → OCR扫描执行白名单任务
"""
from task_exclusions import is_blocked_boutique_task
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
from task_recheck import report_task_found, report_round_finished
import re
import unicodedata

import uiautomator2 as u2
from u2_scanner import (
    connect_device, DT_APP,
    click_u2_control,
    execute_browse_task, execute_live_task,
    is_stale_no_buttons,
    click_three_selected_items,
)
from utils import get_ocr_text_blocks, log_task_completed, show_today_summary

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

MAX_CLICK = 2
STALE_LIMIT = 3
ROW_CLUSTER_TOLERANCE = 60    # 行聚类容忍度（同组内 cy 最大差值 px）
TEXT_PAIR_HOFFSET = 950       # 任务名与按钮最大水平间距 px

# 白名单任务（保持不变）
WHITELIST = [
    "看黄金档直播",
    "看黄金8点档直播90秒",
    "看黄金八点档直播90秒",
    "看黄8点档直播90秒",
    "看8点档直播90秒",
    "看福利直播",
    "浏览精选推荐",
    "浏览当季上新好物",
    "浏览省钱专区",
    # 优惠/特惠/精选商品三次点击任务统一排除。
]

# 各任务时长
DURATIONS = {
    "看黄金档直播": 125,
    "看福利直播": 35,
    "浏览精选推荐": 65,
    "浏览当季上新好物": 65,
    "浏览省钱专区": 65,
}

# 危险关键词黑名单：配对这种文本的任务跳过（避免误点下单/购买）
DANGER_KEYWORDS = ["国补", "下单", "购买", "付款", "充值", "付定金", "付全款"]

LIVE_KW = ["看直播", "看福利直播", "看福利", "直播"]

# 固定坐标（点淘鸭家园入口）
# 设备分辨率 1264×2780 下实测坐标：(824, 1771)
DUCK_ENTRY_X = 824
DUCK_ENTRY_Y = 1771

# 任务列表入口比例坐标
# 原坐标 (178, 2581) → 比例 (0.14, 0.93) → 任务列表入口
TASK_LIST_X_RATIO = 0.14
TASK_LIST_Y_RATIO = 0.93

# OCR 按钮关键词（点击目标文本）
BTN_KEYWORDS = ["去完成", "去浏览", "去观看", "去参与"]


# ========================================================
# 🧭 简化导航（使用固定坐标）
# ========================================================
def click_duck_entry(device):
    """优先点击可见的点淘鸭家园文本，找不到时交给固定坐标兜底。"""
    for query in ({"text": "点淘鸭家园"}, {"textContains": "点淘鸭家园"}):
        try:
            selector = device(**query)
            if not selector.exists:
                continue
            cx, cy = selector.center()
            cx, cy = int(cx), int(cy)
            print(f"    ✅ 找到点淘鸭家园，执行设备点击 @({cx},{cy})")
            device.click(cx, cy)
            print("    ✅ 点淘鸭家园点击指令已发出")
            return True
        except Exception as exc:
            print(f"    ⚠️ 文字点击尝试失败: {exc}")
            continue
    return False


def navigate_to_duck_page(device):
    """
    导航到点淘鸭家园并打开任务列表。
    路径：启动点淘 → 元宝中心 → 固定坐标点淘鸭家园 → 固定坐标任务列表
    """
    print("🔄 正在导航到【点淘鸭家园】...")

    # 1. 启动点淘（关闭重开模式）
    print("  📱 启动点淘...")
    device.app_start(DT_APP, stop=True)
    time.sleep(5)

    # 2. 查找"元宝中心"并点击
    print("  Step 1: 进入【元宝中心】...")
    # 先尝试 U2 查找
    btn = device(textContains="元宝中心")
    if btn.exists:
        click_u2_control(device, btn, "元宝中心")
    else:
        # 兜底：点击底部右侧区域
        print("    ⚠️ 未找到【元宝中心】，点击底部右侧区域...")
        device.click(int(screen_width * 0.7), int(screen_height * 0.93))
    time.sleep(4)

    if not handle_yuanbao_live_popups(device):
        return False

    # 3. 优先按可见文字点击，固定坐标只做兜底
    print("  Step 2: 点击点淘鸭家园...")
    if not click_duck_entry(device):
        duck_x = DUCK_ENTRY_X
        duck_y = DUCK_ENTRY_Y
        print(f"    ⚠️ 未找到文字，使用固定坐标 @({duck_x},{duck_y})...")
        device.click(duck_x, duck_y)
    time.sleep(4)

    # 4. 固定坐标点击打开任务列表
    task_x, task_y = 182, 2595
    print(f"  Step 3: 等待3秒后固定坐标点击打开任务列表 @({task_x},{task_y})...")
    time.sleep(3)
    device.click(task_x, task_y)
    time.sleep(3)

    print("  ✅ 成功进入点淘鸭任务列表")
    return True


# ========================================================
# 过滤函数
# ========================================================
def _normalize_task_name(text: str | None) -> str:
    """归一化 OCR 任务名中的空白和秒单位变体。"""
    compact = unicodedata.normalize("NFKC", text or "")
    compact = compact.translate(str.maketrans({
        "點": "点",
        "檔": "档",
        "擋": "档",
        "挡": "档",
    }))
    compact = re.sub(r"[\s\u200b]+", "", compact)
    return re.sub(r"(?<=\d)[sS](?=\D|$)", "秒", compact)


def is_whitelisted(text):
    normalized_text = _normalize_task_name(text)
    return any(_normalize_task_name(kw) in normalized_text for kw in WHITELIST)


def is_live(text):
    if not text:
        return False
    return any(kw in text for kw in LIVE_KW) or ("直播" in text and "秒" in text)


def parse_progress(text):
    m = re.search(r"[（(]\s*(\d+)\s*/\s*(\d+)\s*[）)]", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


# ========================================================
# 🔍 OCR 扫描：找"去完成"按钮 + 配对左侧任务名
# ========================================================
def _cluster_rows(blocks):
    """将 OCR 文本块按 Y 坐标聚合成行
    返回: [[block, ...], ...]  每组内的 blocks 属于同一行
    """
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda b: b["cy"])
    rows = [[sorted_blocks[0]]]
    for b in sorted_blocks[1:]:
        first_cy = rows[-1][0]["cy"]
        if abs(b["cy"] - first_cy) <= ROW_CLUSTER_TOLERANCE:
            rows[-1].append(b)
        else:
            rows.append([b])
    return rows


def scan_duck_tasks(blocks):
    """
    OCR 扫描 — 嵌入进度链式配对
    OCR 返回的任务名已内含进度数字（如 "看黄金档直播2分钟(2/5)"），
    流程：行聚类 → 每行内找含(d/d)的文本块 → 向右配对去完成按钮
    返回: [(task_name_with_progress, btn_cx, btn_cy), ...]
    """
    # ── 1. 行聚类 ──
    rows = _cluster_rows(blocks)
    print(f"  📐 聚类成 {len(rows)} 行")

    # ── 2. 每行内：找含(d/d)的任务名 → 向右配对去完成按钮 ──
    pairs = []

    for ri, row in enumerate(rows):
        # 找含进度数字的任务名块
        task_blocks = []
        for b in row:
            if parse_progress(b["text"]) is not None:
                task_blocks.append(b)

        # 找该行的"去完成"按钮
        btn_blocks = []
        for b in row:
            if b["text"] in BTN_KEYWORDS:
                btn_blocks.append(b)

        if not task_blocks or not btn_blocks:
            continue

        # 配对：每个任务名块 → 找右边最近的去完成按钮
        for tb in task_blocks:
            full_name = tb["text"].strip()
            if is_blocked_boutique_task(full_name):
                print(f"    ⊘ 已屏蔽商品三次点击任务: 【{full_name}】")
                continue
            tb_cx, tb_cy = tb["cx"], tb["cy"]

            # 向右找最近的去完成按钮
            best_btn = None
            best_hdist = 9999
            for bb in btn_blocks:
                if bb["cx"] <= tb_cx:
                    continue
                hd = bb["cx"] - tb_cx
                if hd < best_hdist:
                    best_hdist = hd
                    best_btn = bb

            if not best_btn:
                continue

            btn_cx, btn_cy = int(best_btn["cx"]), int(best_btn["cy"])

            # 危险关键词过滤
            if any(dk in full_name for dk in DANGER_KEYWORDS):
                print(f"    ⊘ 危险跳过: 【{full_name}】")
                continue

            print(f"  🎯 【{full_name}】→ 点击@({btn_cx},{btn_cy})")
            pairs.append((full_name, btn_cx, btn_cy))

    print(f"  ✅ 本轮 {len(pairs)} 个可执行任务")
    return pairs


# ========================================================
# 🎯 执行任务
# ========================================================
def is_danger_page(device):
    """
    安全兜底：用 U2 检测当前页面是否包含危险关键词
    返回 True 表示页面上出现了购买/下单/国补等危险内容
    """
    try:
        nodes = device(className="android.widget.TextView")
        for n in nodes:
            t = n.get_text()
            if t and any(dk in t for dk in DANGER_KEYWORDS):
                return True
    except:
        pass
    return False


def do_task(task_name, btn_cx, btn_cy):
    """执行单个任务（含点击前安全校验）"""
    if is_blocked_boutique_task(task_name):
        print('  ⊘ 已屏蔽精选宝贝任务，跳过')
        return False

    print(f"  🎯 {task_name} @({btn_cx},{btn_cy})")

    # ⚠️ 点击前安全校验：检查按钮坐标附近是否确实有白名单关键词
    # 防止配对错位导致误点危险任务
    safety_blocks = get_ocr_text_blocks(d)
    safety_found = False
    if safety_blocks:
        for sb in safety_blocks:
            if "去完成" in sb.get("text", "") or "去浏览" in sb.get("text", ""):
                hd = abs(sb["cx"] - btn_cx)
                vd = abs(sb["cy"] - btn_cy)
                if hd < 80 and vd < 80:
                    safety_found = True
                    break
    if safety_found:
        print(f"    ✅ 点击安全校验通过")
    else:
        print(f'    ⚠️ 按钮位置未通过校验，跳过本次点击并等待重新扫描')
        return False

    # 特判：查看3个特惠卖场宝贝 → 点击3次
    if "查看" in task_name and ("宝贝" in task_name or "精选" in task_name):
        d.click(btn_cx, btn_cy)
        time.sleep(2)
        click_three_selected_items(d)
        d.press("back")
        time.sleep(1.5)
        try:
            log_task_completed("点淘鸭钻石任务", task_name)
        except:
            pass
        return

    if is_live(task_name):
        prog = parse_progress(task_name)
        if prog:
            cur, total = prog
            if cur >= total:
                print("    已达到任务进度，跳过")
                return False
            for n in range(cur, total):
                d.click(btn_cx, btn_cy)   # 每次循环前重新点击按钮
                time.sleep(0.5)
                print(f"  📺 直播 {n+1}/{total}")
                execute_live_task(d, task_name, btn_cx, btn_cy)
                d.press("back")
                time.sleep(2)
        else:
            d.click(btn_cx, btn_cy)
            time.sleep(0.5)
            execute_live_task(d, task_name, btn_cx, btn_cy)
            d.press("back")
            time.sleep(2)
    else:
        # 浏览任务
        d.click(btn_cx, btn_cy)
        time.sleep(0.5)
        try:
            btn = d(className="android.widget.Button", textMatches=r"打开|允许|始终允许")
            if btn.exists(timeout=1):
                click_u2_control(d, btn, "打开/允许")
                time.sleep(1)
        except:
            pass
        for match_kw, sec in DURATIONS.items():
            if match_kw in task_name:
                execute_browse_task(d, sec)
                break
        else:
            execute_browse_task(d, 20)
        # 浏览完成后返回任务列表
        d.press("back")
        time.sleep(2)

    try:
        log_task_completed("点淘鸭钻石任务", task_name)
    except:
        pass


# ========================================================
# 🔍 OCR 扫描并执行任务主循环
# ========================================================
def scan_and_execute():
    """主循环：OCR扫描 → 白名单过滤 → 执行"""
    total = 0
    clicked = {}
    prev_snap = []
    prev_empty = []

    print("\n" + "=" * 50)
    print("🔍 开始扫描【点淘鸭家园】任务列表（OCR模式）...")
    print(f"   白名单: {' | '.join(WHITELIST)}")
    print("=" * 50)

    while True:
        time.sleep(2)
        print(f"\n📡 OCR 扫描中 (已完成 {total} 个)...")

        blocks = get_ocr_text_blocks(d)
        if not blocks:
            if is_stale_no_buttons(prev_empty, [], STALE_LIMIT):
                print("  🎉 连续3轮 OCR 空白")
                break
            print("  ⚠️ OCR 未识别到文本，下滑...")
            print("  🐛 诊断提示：可能是页面加载慢 / 覆盖层遮挡 / OCR 服务器异常")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)
            continue

        pairs = scan_duck_tasks(blocks)
        if not pairs:
            if is_stale_no_buttons(prev_empty, pairs, STALE_LIMIT):
                print("  🎉 连续3轮无任务按钮")
                break
            print("  ⚠️ 无任务按钮，下滑...")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)
            continue

        # 去重检测：用 frozenset 快照检测相同列表
        current_snap = frozenset(
            (name[:15], cx // 60, cy // 60)
            for name, cx, cy in pairs
        )
        prev_snap.append(current_snap)
        if len(prev_snap) > STALE_LIMIT:
            prev_snap.pop(0)
        if len(prev_snap) >= STALE_LIMIT and len(set(prev_snap)) == 1:
            print("  🎉 连续3轮相同任务列表，完毕")
            break

        # 找可执行任务（白名单 + 去重 + 危险关键词兜底过滤）
        task = None
        for name, cx, cy in pairs:
            if not name:
                continue
            if not is_whitelisted(name):
                print(f"  ⊘ 非白名单: 【{name}】")
                continue
            # 危险关键词兜底（防止 scan_duck_tasks 内合并文本时漏掉）
            if any(dk in name for dk in DANGER_KEYWORDS):
                print(f"  ⊘ 危险关键词兜底过滤: 【{name}】")
                continue
            if clicked.get(name, 0) >= MAX_CLICK:
                print(f"  ⊘ 已达上限: 【{name}】(已做{clicked[name]}次)")
                continue
            task = (name, cx, cy)
            print(f"  ✅ 选中: 【{name}】@({cx},{cy})")
            break

        if task:
            prev_snap.clear()
            prev_empty.clear()
            name, cx, cy = task
            clicked[name] = clicked.get(name, 0) + 1
            report_task_found()
            if do_task(name, cx, cy) is False:
                print("  本次未执行成功，重新扫描")
                continue
            total += 1
            print(f"  ✅ 已完成: {total}")
        else:
            print("  无可执行任务，下滑...")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)

    print(f"\n📊 共完成 {total} 个任务")
    report_round_finished()
    return total


# ========================================================
# 🚀 Main
# ========================================================
def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🦆 点淘鸭钻石任务 自动化脚本（OCR重构版）")
    print("   导航: 首页 → 元宝中心 → 固定坐标点淘鸭家园 → 固定坐标任务列表")
    print(f"   白名单: {' | '.join(WHITELIST)}")
    print("=" * 50)

    navigate_to_duck_page(d)
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
