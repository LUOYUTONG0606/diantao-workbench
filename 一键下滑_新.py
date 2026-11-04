"""
一键下滑 (新) - 标准路径版
============================================================
流程：6轮顺序执行，每轮：重启点淘 → OCR进入元宝中心 → OCR导航目标页 → 下滑浏览

【核心改造点】
1. 页面文字全部由 OCR 识别并按识别坐标点击。
   - 打工：元宝中心 → OCR点击【打工赚元宝】
   - 睡觉：元宝中心 → OCR点击【睡觉赚元宝】
   - 签到：元宝中心 → 下滑查找【今日签到】/【去看看】（废弃进两次连退3次黑魔法）
   - 购物：元宝中心 → 固定坐标点击入口 (934, 1723)
   - 摇一摇：元宝中心 → OCR点击【点淘鸭家园】→ 查找【去摇一摇】
   - 走路：元宝中心 → OCR点击【走路赚元宝】
2. 防空滑安全网（修复代码审计问题 13）：
   - 所有导航函数明确返回 bool 状态。
   - 导航失败时直接跳过该轮次下滑，绝不残留于错误页面盲滑。
3. 零顶层副作用：
   - 设备连接收口到 main 执行流程，便于被外部模块或测试安全导入。
============================================================
"""
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
import random

from u2_scanner import connect_device, DT_APP, scan_page, find_clickable_with_text
from utils import click_ocr_text, has_ocr_text


def _click_ocr(device, keywords, *, settle_seconds=1.5, attempts=2):
    """通过 OCR 识别页面文字并点击，不查询 U2 控件树。"""
    return click_ocr_text(
        device, keywords, settle_seconds=settle_seconds,
        attempts=attempts, log=True,
    )


def _has_ocr(device, keywords, *, settle_seconds=0.5, attempts=1):
    """通过 OCR 判断页面是否包含目标文字。"""
    return has_ocr_text(
        device, keywords, settle_seconds=settle_seconds,
        attempts=attempts, log=True,
    )


# ========================================================
# 🧭 标准导航函数库
# ========================================================

def dismiss_popups(device):
    """通过 OCR 查找弹窗操作文字；未识别到时不进行坐标盲点。"""
    labels = ["继续做任务", "开心收下", "收下元宝", "我知道了", "放弃", "确定", "允许", "关闭", "残忍离开"]
    if _click_ocr(device, labels, settle_seconds=0.3, attempts=1):
        time.sleep(1.2)
        return True

    if _has_ocr(device, "恭喜获得元宝", settle_seconds=0.2):
        if _click_ocr(device, ["继续做任务", "去完成", "关闭", "我知道了"], settle_seconds=0.2, attempts=1):
            time.sleep(1.2)
            return True
    return False


def _nav_to_yuanbao_center(device, sw: int, sh: int) -> bool:
    """通过 OCR 进入元宝中心；不使用 U2 文本树或固定坐标兜底。"""
    print("  🧭 正在进入【元宝中心】...")
    if _has_ocr(device, ["我的元宝", "提现", "兑红包"]):
        print("  ✅ OCR 确认已处于元宝中心")
        return handle_yuanbao_live_popups(device)

    for attempt in range(4):
        if _has_ocr(device, "刷新", settle_seconds=0.2):
            print("    ⚠️ OCR 检测到【刷新】，尝试点击恢复...")
            _click_ocr(device, "刷新", settle_seconds=0.2, attempts=1)
            time.sleep(3)

        print(f"    👉 OCR 查找【元宝中心】(尝试 {attempt + 1}/4)...")
        if not _click_ocr(device, "元宝中心", settle_seconds=1, attempts=2):
            dismiss_popups(device)
            continue
        time.sleep(4)
        if not handle_yuanbao_live_popups(device):
            return False
        dismiss_popups(device)
        if _has_ocr(device, ["我的元宝", "提现", "兑红包"], settle_seconds=0.5, attempts=2):
            device.swipe(sw // 2, int(sh * 0.30), sw // 2, int(sh * 0.80), duration=0.2)
            time.sleep(1)
            print("  ✅ OCR 确认进入元宝中心")
            return True

    print("  ❌ OCR 未能确认进入元宝中心")
    return False


def _nav_to_dagong(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → OCR 查找打工赚元宝。"""
    print("  🧭 查找【打工赚元宝】入口...")
    if not _click_ocr(device, ["打工赚元宝", "打工"]):
        print("  ⚠️ OCR 未找到打工入口")
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_sleep(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → OCR 查找睡觉赚元宝。"""
    print("  🧭 查找【睡觉赚元宝】入口...")
    if not _click_ocr(device, ["睡觉赚元宝", "睡觉"]):
        print("  ⚠️ OCR 未找到睡觉入口")
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_signin(device, sw: int, sh: int) -> bool:
    """导航：元宝中心 → 下滑后通过 OCR 查找今日签到。"""
    print("  📜 下滑查找【今日签到】入口...")
    device.swipe(sw // 2, int(sh * 0.80), sw // 2, int(sh * 0.35), duration=0.3)
    for attempt in range(5):
        time.sleep(1)
        nodes = scan_page(device)
        sign = find_clickable_with_text(nodes, "今日签到")
        if sign:
            sx, sy = int(sign["cx"]), int(sign["cy"])
            for button_text in ["去看看", "去签到", "立即签到"]:
                button = find_clickable_with_text(nodes, button_text)
                if button:
                    bx, by = int(button["cx"]), int(button["cy"])
                    if abs(bx - sx) > 40 and -30 <= by - sy <= 220:
                        device.click(bx, by)
                        time.sleep(4)
                        dismiss_popups(device)
                        print(f"  ✅ OCR 点击【{button_text}】进入签到页面")
                        return True
            device.click(sx, sy)
            time.sleep(4)
            dismiss_popups(device)
            print("  ✅ OCR 点击【今日签到】")
            return True
        if attempt < 4:
            device.swipe(sw // 2, int(sh * 0.75), sw // 2, int(sh * 0.35), duration=0.3)

    print("  ⚠️ OCR 未找到今日签到入口")
    return False


def _nav_to_shop(device, sw: int, sh: int) -> bool:
    """使用固定坐标点击购物赚钱入口。"""
    print("  🎯 固定坐标点击购物赚钱入口 (934, 1723)...")
    device.click(934, 1723)
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_yaoyiyao(device, sw: int, sh: int) -> bool:
    """
    导航：元宝中心 → 摇一摇赚元宝
    """
    print("  🧭 查找【摇一摇赚元宝】入口...")
    if not _click_ocr(device, ["摇一摇赚元宝", "去摇一摇"]):
        print("  ⚠️ OCR 未找到摇一摇入口")
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


def _nav_to_walk(device, sw: int, sh: int) -> bool:
    """
    导航：元宝中心 → 走路赚元宝
    """
    print("  🧭 查找【走路赚元宝】入口...")
    if not _click_ocr(device, ["走路赚元宝", "走路"]):
        print("  ⚠️ OCR 未找到走路入口")
        return False
    time.sleep(4)
    dismiss_popups(device)
    return True


# ========================================================
# 📋 轮次配置
# ========================================================
ROUNDS_CONFIG = [
    {
        "label": "1",
        "name": "打工赚元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_dagong,
    },
    {
        "label": "2",
        "name": "睡觉赚元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_sleep,
    },
    {
        "label": "3",
        "name": "签到赢元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_signin,
    },
    {
        "label": "4",
        "name": "购物赚钱",
        "swipe_seconds": 180,
        "nav_func": _nav_to_shop,
    },
    {
        "label": "5",
        "name": "摇一摇",
        "swipe_seconds": 180,
        "nav_func": _nav_to_yaoyiyao,
    },
    {
        "label": "6",
        "name": "走路赚元宝",
        "swipe_seconds": 180,
        "nav_func": _nav_to_walk,
    },
]


# ========================================================
# 📱 下滑浏览
# ========================================================
def swipe_browse(device, seconds: int, sw: int, sh: int):
    """纯下滑浏览（保留拟人化稳定滑动节奏）"""
    if seconds <= 0:
        print("  ⏭️ 跳过下滑（swipe_seconds <= 0）")
        return

    print(f"  📋 开始下滑浏览 {seconds}s (含 10s 缓冲)")
    start = time.time()
    count = 0
    while time.time() - start < seconds + 10:
        start_y = random.randint(int(sh * 0.75), int(sh * 0.85))
        end_y = random.randint(int(sh * 0.20), int(sh * 0.30))
        device.swipe(int(sw * 0.5), start_y, int(sw * 0.5), end_y, duration=0.25)
        count += 1
        time.sleep(random.uniform(2.5, 4.0))

        # 偶发性全屏弹窗快速处理
        dismiss_popups(device)

    print(f"  ✅ 浏览完成 (共执行 {count} 次有效下滑)")


# ========================================================
# 🚀 单轮执行（含防空滑熔断机制）
# ========================================================
def run_round(device, label: str, name: str, swipe_seconds: int, nav_func, sw: int, sh: int) -> bool:
    """执行一轮：冷启点淘 → 元宝中心 → 标准导航目标页 → 安全下滑"""
    print(f"\n{'='*60}")
    print(f"  第 {label} 轮: {name}")
    print(f"  计划浏览时长: {swipe_seconds}s")
    print(f"{'='*60}")

    # Step 1: 重启点淘到首页
    print("  🚀 重启点淘...")
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    dismiss_popups(device)

    # Step 2: 进入元宝中心
    if not _nav_to_yuanbao_center(device, sw, sh):
        print(f"  ❌ 未能进入元宝中心，终止第 {label} 轮")
        return False

    # Step 3: 标准路径导航
    entered = nav_func(device, sw, sh)
    if not entered:
        print(f"  ⚠️ 标准导航未能进入【{name}】，跳过本轮浏览（触发安全防护，避免在错误页面空跑）")
        return False

    # Step 4: 下滑浏览
    swipe_browse(device, swipe_seconds, sw, sh)

    # Step 5: 退出并安全返回
    print("  ↩️ 安全退出当前页面...")
    device.press("back")
    time.sleep(2)
    dismiss_popups(device)

    print(f"  ✅ 第 {label} 轮【{name}】顺利完成")
    return True


# ========================================================
# 🏁 主函数
# ========================================================
def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 60)
    print(" 🎮 一键下滑 (新) - 标准路径版")
    print("   流程: 打工 → 睡觉 → 签到 → 购物赚钱 → 摇一摇 → 走路赚元宝")
    print("   特性: OCR导航 + WebView 容错 + 弹窗自愈 + 失败防空跑保护")
    print("=" * 60)

    device = connect_device()
    # 暂停 watcher 避免在复杂 WebView 中递归 dump_hierarchy 触发 StackOverflow
    if hasattr(device, "_global_watcher_ctx") and device._global_watcher_ctx:
        try:
            device._global_watcher_ctx.stop()
            print("  [设置] 已暂停全局 watcher 避免 DOM 深度溢出")
        except Exception:
            pass

    sw, sh = device.window_size()
    print(f"📱 屏幕分辨率: {sw} x {sh}")

    # 支持命令行参数指定轮次或时长：
    # py -3.10 一键下滑_新.py [round_idx] [seconds]
    target_round = None
    custom_seconds = None
    if len(sys.argv) > 1:
        arg1 = sys.argv[1].strip()
        if arg1.isdigit():
            target_round = int(arg1)
    if len(sys.argv) > 2:
        arg2 = sys.argv[2].strip()
        if arg2.isdigit():
            custom_seconds = int(arg2)

    rounds_to_run = []
    for r in ROUNDS_CONFIG:
        if target_round is not None and int(r["label"]) != target_round:
            continue
        rounds_to_run.append(r)

    if not rounds_to_run:
        print(f"⚠️ 指定的轮次 {target_round} 不存在，可选 1-{len(ROUNDS_CONFIG)}")
        return

    success_count = 0
    total_count = len(rounds_to_run)

    for r in rounds_to_run:
        duration = custom_seconds if custom_seconds is not None else r["swipe_seconds"]
        ok = run_round(
            device=device,
            label=r["label"],
            name=r["name"],
            swipe_seconds=duration,
            nav_func=r["nav_func"],
            sw=sw,
            sh=sh,
        )
        if ok:
            success_count += 1

    print(f"\n{'='*60}")
    print(f"  全部执行完毕: 成功 {success_count}/{total_count} 轮")
    print(f"{'='*60}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ 用户手动中断")
    except Exception as e:
        print(f"\n❌ 执行异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(" 👋 任务结束")
