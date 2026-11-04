"""
合成百元大红包 自动化脚本
流程：进点淘 → 元宝中心 → 下滑找合成百元大红包 → 去合成 → 扫描赚能量 → 自动执行任务

任务列表（右下角扫描赚能量打开）：
    看视频30秒       → 视频类
    看精彩内容30秒    → 直播类
    浏览上新好物120秒 → 浏览类
    浏览精选推荐60秒  → 浏览类
    浏览省钱专区30秒  → 浏览类
"""
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
import re

from u2_scanner import connect_device, DT_APP, click_u2_control
from u2_scanner import (
    execute_browse_task, execute_live_task, execute_video_task,
    handle_guess_price_game,
)
from utils import get_ocr_text_blocks, show_today_summary

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

MAX_SCROLL = 8

# 黑名单关键词（避免误点元宝商城下单、邀请等危险任务）
BLACKLIST_KEYWORDS = [
    "元宝商城", "下单", "购买", "充值", "付款",
    "邀请", "邀请好友", "拉好友", "助力", "分享", "去邀请",
    "喂鸡", "蚂蚁庄园", "蚂蚁新村", "饿了么", "果园",
    "消消乐", "农场小游戏", "斗地主", "开心消消乐",
    "猜谜", "猜一猜",
    "查看并完成", "玩小游戏",
]

# 任务关键词 → 类型映射
VIDEO_KEYWORDS = ["看视频"]
LIVE_KEYWORDS = ["看精彩内容", "看福利直播", "直播"]
BROWSE_KEYWORDS = ["浏览上新好物", "浏览精选推荐", "浏览省钱专区",
                   "浏览", "上新好物", "精选推荐", "省钱专区"]
GUESS_PRICE_KEYWORDS = ["猜价格", "猜一猜"]

# 浏览类任务时长映射（任务名关键词 → 秒数）
BROWSE_DURATION_MAP = {
    "浏览上新好物": 120,
    "浏览精选推荐": 60,
    "浏览省钱专区": 30,
}


def navigate():
    """进点淘 → 元宝中心 → 下滑找合成百元大红包 → 点去合成"""
    print("🔄 导航到元宝中心...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    for _ in range(10):
        for text in ["元宝中心", "赚元宝"]:
            btn = d(textContains=text)
            if btn.exists(timeout=2):
                click_u2_control(d, btn, text)
                time.sleep(4)
                break
        else:
            time.sleep(2)
            continue
        break
    else:
        d.click(int(screen_width * 0.7), int(screen_height * 0.93))
        time.sleep(4)

    if not handle_yuanbao_live_popups(d):
        return False

    # 下滑找合成百元大红包 → 点去合成
    print("🔄 下滑找合成百元大红包...")
    for _ in range(MAX_SCROLL):
        target = d(textContains="合成百元大红包")
        if target.exists(timeout=2):
            try:
                tx, ty = target.center()
                tx, ty = int(tx), int(ty)
            except:
                tx, ty = 0, 0

            go_btn = d(textContains="去合成")
            if go_btn.exists(timeout=1):
                try:
                    gx, gy = go_btn.center()
                    gx, gy = int(gx), int(gy)
                except:
                    gx, gy = 0, 0
                if abs(gx - tx) > 50 and -20 <= gy - ty <= 200:
                    d.click(gx, gy)
                    print(f"  ✅ 点击去合成")
                    time.sleep(4)
                    return True

            # 兜底：点合成百元大红包右侧（盲点）
            d.click(tx + 200, ty)
            time.sleep(4)
            return True

        d.swipe(screen_width // 2, int(screen_height * 0.7),
                screen_width // 2, int(screen_height * 0.2))
        time.sleep(2)

    print("  ⚠️ 未找到合成百元大红包")
    return False


def click_scan_energy():
    """点击右下角「扫描赚能量」展开任务列表（首次需要）"""
    print("🔄 查找扫描赚能量...")
    # 方式1：U2 文本匹配
    for text in ["扫描赚能量", "赚能量"]:
        btn = d(textContains=text)
        if btn.exists(timeout=2):
            click_u2_control(d, btn, text)
            time.sleep(3)
            return True

    # 方式2：OCR 扫描（限制屏幕下25%区域）
    time.sleep(2)
    blocks = get_ocr_text_blocks(d)
    for b in blocks:
        if "赚能量" in b["text"] and b["cy"] > screen_height * 0.75:
            d.click(b["cx"], b["cy"])
            print(f"  ✅ OCR点击赚能量 @({b['cx']},{b['cy']})")
            time.sleep(3)
            return True

    print("  ⚠️ 未找到扫描赚能量（可能已展开）")
    return False


# ========================================================
# 任务解析
# ========================================================

def parse_seconds(text, default=60):
    """从任务名提取秒数，如"看视频30秒"→30"""
    m = re.search(r"(\d+)\s*秒", text)
    return int(m.group(1)) if m else default


def match_task_type(text):
    """匹配任务名 → 返回类型字符串 'live' / 'browse' / 'guess_price'"""
    for kw in GUESS_PRICE_KEYWORDS:
        if kw in text:
            return "guess_price"
    for kw in VIDEO_KEYWORDS:
        if kw in text:
            return "video"
    for kw in LIVE_KEYWORDS:
        if kw in text:
            return "live"
    for kw in BROWSE_KEYWORDS:
        if kw in text:
            return "browse"
    return "other"


def get_browse_duration(task_name):
    """根据任务名获取浏览时长"""
    for kw, sec in BROWSE_DURATION_MAP.items():
        if kw in task_name:
            return sec
    # 兜底：从文本解析秒数
    return parse_seconds(task_name, default=60)


def detect_task_name(blocks, btn_block):
    """从"去完成"按钮同一行左侧提取真实任务名
    此页面布局（左右结构，Y轴相同）：
        看视频30秒(0/5)  @(cx≈300~500)   ← 任务名
        去完成           @(cx≈1080)       ← 按钮
        两者 Y轴相同（±5px内）
    排除：上方区域标题"做任务领元宝"、右侧"+2"标签等干扰
    """
    cx, cy = btn_block["cx"], btn_block["cy"]
    print(f"  🔍 detect_task_name: 按钮@({cx},{cy}) '{btn_block['text']}'")

    same_row = []
    for nb in blocks:
        if nb["cx"] >= cx:                     # 必须在按钮左侧
            continue
        if nb["cx"] < screen_width * 0.1:      # 太靠左可能是装饰文本
            continue
        if nb["cx"] > screen_width * 0.6:      # 限制左半屏
            continue
        if nb["cy"] > cy or cy - nb["cy"] > 40:    # 任务名必须在按钮上方 0~40px 范围内
            continue
        print(f"    候选: [{nb['cx']:4d},{nb['cy']:4d}] '{nb['text']}'")
        same_row.append(nb)

    # 按水平距离从大到小排序（靠左的任务名比靠右的"额外得元宝"等标签更可靠）
    same_row.sort(key=lambda x: abs(cx - x["cx"]), reverse=True)

    for nb in same_row:
        t = nb["text"]
        if "额外得" in t or "元宝" in t or "金币" in t:
            print(f"    ⊘ 过滤(额外/元宝/金币): '{t}'")
            continue
        if "得星能量" in t:
            print(f"    ⊘ 过滤(得星能量): '{t}'")
            continue
        if re.match(r'^[+=]\d+$', t.strip()) or re.match(r'^\(\d+/\d+\)$', t.strip()):
            print(f"    ⊘ 过滤(纯数字/+N): '{t}'")
            continue
        print(f"    ✅ 选中: '{t}'")
        return t

    result = same_row[0]["text"] if same_row else ""
    print(f"    {'⚠️ 兜底' if same_row else '❌ 无候选'} 结果: '{result}'")
    return result


# ========================================================
# 任务执行
# ========================================================

def exec_guess_price(btn_cx, btn_cy):
    """执行猜价格任务：点去完成 → 猜价格游戏 → 已回到任务列表"""
    print("  🎯 猜价格游戏")
    d.click(btn_cx, btn_cy)
    time.sleep(3)
    handle_guess_price_game(d)


def exec_browse(btn_cx, btn_cy, task_name=""):
    """执行浏览类任务（使用OCR智能监控，避免多等extra_seconds）"""
    duration = get_browse_duration(task_name)
    print(f"  🎯 浏览任务（{duration}秒）: {task_name}")
    d.click(btn_cx, btn_cy)
    time.sleep(3)

    execute_browse_task(d, min_duration=duration, max_wait=duration + 30)

    d.press("back")
    time.sleep(2)


def exec_live(btn_cx, btn_cy, task_name=""):
    """执行看直播或精彩内容任务：复用 execute_live_task"""
    print("  🎯 看直播（直播挂机）")
    d.click(btn_cx, btn_cy)
    time.sleep(0.5)
    execute_live_task(d, task_name, btn_cx, btn_cy)
    d.press("back")
    time.sleep(1.5)


# ========================================================
# 扫描主循环
# ========================================================

def execute_tasks():
    """OCR全屏扫描 → 找"去完成"按钮 → 取上方任务名 → 执行 → 循环"""
    total = 0
    MAX_EMPTY_RETRIES = 3
    empty_retries = 0

    print("\n🔍 扫描合成百元大红包任务...")
    while True:
        time.sleep(3)
        blocks = get_ocr_text_blocks(d)
        if not blocks:
            print("  ⚠️ 未识别到任何文本")
            empty_retries += 1
            if empty_retries >= MAX_EMPTY_RETRIES:
                break
            print(f"  ⏳ 等待页面刷新... (第{empty_retries}次)")
            continue

        # 调试：打印所有识别到的文本块
        print(f"  📋 OCR 文本块 ({len(blocks)}):")
        for b in blocks:
            print(f"    [{b['cx']:4d},{b['cy']:4d}] {b['text']}_{b.get('conf',1.0):.2f}")

        candidates = []
        for b in blocks:
            text = b["text"]
            if any(kw in text for kw in ["去完成", "去浏览", "去观看", "去参与", "去领取", "立即参与", "立即领取"]):
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
                candidates.append((priority, task_type, task_name,
                                   int(b["cx"]), int(b["cy"])))

        if not candidates:
            empty_retries += 1
            if empty_retries >= MAX_EMPTY_RETRIES:
                print("  🎉 连续扫描无任务，结束")
                break
            print(f"  ⏳ 等待页面刷新... (第{empty_retries}次)")
            continue

        empty_retries = 0

        # 按优先级排序（browse 优先，live 最后）
        candidates.sort(key=lambda x: x[0])
        prio, task_type, task_name, btn_cx, btn_cy = candidates[0]
        print(f"\n  ▶️ [{prio}] {task_name}")

        if task_type == "guess_price":
            exec_guess_price(btn_cx, btn_cy)
        elif task_type == "browse":
            exec_browse(btn_cx, btn_cy, task_name)
        elif task_type == "video":
            d.click(btn_cx, btn_cy)
            time.sleep(0.5)
            execute_video_task(
                d, task_name, btn_cx, btn_cy,
                verify_media_page=True,
            )
        elif task_type == "live":
            exec_live(btn_cx, btn_cy, task_name)

        total += 1
        print(f"  ✅ 已完成 {total} 个任务")

    print(f"\n📊 共执行 {total} 个任务")


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 合成百元大红包")
    print("=" * 50)

    if not navigate():
        print("\n⚠️ 未能进入合成百元大红包页面")
        return

    print("\n✅ 已进入合成百元大红包页面")

    # 首次点击右下角"扫描赚能量"展开任务列表
    time.sleep(2)
    click_scan_energy()

    execute_tasks()

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
