"""
一键下滑 自动化脚本
流程：6轮顺序执行，每轮：重启点淘 → 元宝中心 → OCR 定位入口 → 下滑浏览

轮次顺序:
  1. 打工赚元宝
  2. 睡觉赚元宝
  3. 签到赢元宝（OCR下滑找"今日签到"→"去看看"）
  4. 购物赚钱（固定点击双11活动入口）
  5. 摇一摇（OCR定位点淘鸭家园 → 右滑2次 → OCR找去摇一摇）
  6. 走路赚元宝（OCR定位入口）

配置说明：
  修改 ROUNDS_CONFIG 列表，每个轮次可独立配置：
    - swipe_seconds: 下滑浏览秒数
    - nav_func:     导航函数（接收 device 参数，负责进入对应页面）
"""
import time
from yuanbao_popup import handle_yuanbao_live_popups
import sys
import random

from u2_scanner import connect_device, DT_APP
from utils import get_ocr_text_blocks

CENTER_OCR_PRE_SCAN_DELAY_SECONDS = 5
CENTER_OCR_SCAN_ATTEMPTS = 3
CENTER_OCR_RETRY_DELAY_SECONDS = 3
CENTER_OCR_CLICK_WAIT_SECONDS = 4
OCR_PRE_SCAN_DELAY_SECONDS = 3
OCR_SCAN_ATTEMPTS = 5
OCR_RETRY_DELAY_SECONDS = 3
OCR_CLICK_WAIT_SECONDS = 5
DUCK_OPEN_WAIT_SECONDS = 7


# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")


# ========================================================
# 🧭 导航函数库（每个轮次使用不同的导航策略）
# ========================================================

def _is_yuanbao_center_ocr(device):
    """用 OCR 判断当前是否已在元宝中心页面。"""
    try:
        blocks = get_ocr_text_blocks(device)
    except Exception as exc:
        print(f"  ⚠️ OCR 检查元宝中心失败：{exc}")
        return False

    center_markers = ("我的元宝", "提现", "兑红包")
    return any(
        marker in block.get("text", "")
        for block in blocks
        for marker in center_markers
    )


def _enter_yuanbao_center_ocr(device):
    """等待首页稳定后，用 OCR 查找并点击元宝中心。"""
    print(
        f"  ⏳ 等待首页稳定 {CENTER_OCR_PRE_SCAN_DELAY_SECONDS}s，"
        "再用 OCR 查找元宝中心..."
    )
    time.sleep(CENTER_OCR_PRE_SCAN_DELAY_SECONDS)

    for scan_index in range(1, CENTER_OCR_SCAN_ATTEMPTS + 1):
        try:
            blocks = get_ocr_text_blocks(device)
        except Exception as exc:
            blocks = []
            print(f"  ⚠️ 元宝中心 OCR 第 {scan_index} 次扫描失败：{exc}")

        target = next(
            (b for b in blocks if "元宝中心" in b.get("text", "")),
            None,
        )
        if target:
            target_x, target_y = int(target["cx"]), int(target["cy"])
            print(f"  🎯 OCR 点击【元宝中心】@({target_x},{target_y})")
            device.click(target_x, target_y)
            time.sleep(CENTER_OCR_CLICK_WAIT_SECONDS)
            return handle_yuanbao_live_popups(device)

        print(
            f"  ⚠️ OCR 未找到【元宝中心】（第 "
            f"{scan_index}/{CENTER_OCR_SCAN_ATTEMPTS} 次）"
        )
        if scan_index < CENTER_OCR_SCAN_ATTEMPTS:
            time.sleep(CENTER_OCR_RETRY_DELAY_SECONDS)

    return False


def _ocr_click_target(device, keyword, label=None, attempts=OCR_SCAN_ATTEMPTS,
                      click_wait=OCR_CLICK_WAIT_SECONDS):
    """OCR 查找入口文本并点击其识别坐标。"""
    label = label or keyword
    print(f"  🔍 OCR 查找【{label}】...")
    time.sleep(OCR_PRE_SCAN_DELAY_SECONDS)

    for attempt in range(1, attempts + 1):
        try:
            blocks = get_ocr_text_blocks(device)
        except Exception as exc:
            blocks = []
            print(f"  ⚠️ OCR 扫描【{label}】失败：{exc}")

        normalized_keyword = "".join(str(keyword).split()).casefold()
        target = next(
            (
                b for b in blocks
                if normalized_keyword in "".join(
                    str(b.get("text", "")).split()
                ).casefold()
            ),
            None,
        )
        if target:
            target_x, target_y = int(target["cx"]), int(target["cy"])
            print(f"  🎯 OCR 点击【{target['text']}】@({target_x},{target_y})")
            device.click(target_x, target_y)
            time.sleep(click_wait)
            return True

        print(f"  ⚠️ OCR 未找到【{label}】（第 {attempt}/{attempts} 次）")
        if attempt < attempts:
            time.sleep(OCR_RETRY_DELAY_SECONDS)

    print(f"  ⚠️ 未能通过 OCR 进入【{label}】")
    return False


def _nav_to_dagong(device):
    """OCR 查找并点击打工赚元宝入口。"""
    _ocr_click_target(device, "打工赚元宝")


def _nav_to_sleep(device):
    """OCR 查找并点击睡觉赚元宝入口。"""
    _ocr_click_target(device, "睡觉赚元宝")


def _nav_to_signin(device):
    """导航：元宝中心 → OCR 下滑查找今日签到并尝试进入一次。"""
    print("  📜 下滑找今日签到...")
    time.sleep(OCR_PRE_SCAN_DELAY_SECONDS)

    for scan_index in range(1, 9):
        try:
            blocks = get_ocr_text_blocks(device)
        except Exception as exc:
            blocks = []
            print(f"  ⚠️ 签到 OCR 扫描失败：{exc}")

        sign_block = next(
            (b for b in blocks if "今日签到" in b.get("text", "")),
            None,
        )
        if sign_block:
            sign_x, sign_y = int(sign_block["cx"]), int(sign_block["cy"])
            go_block = next(
                (
                    b for b in blocks
                    if "去看看" in b.get("text", "")
                    and abs(int(b["cx"]) - sign_x) > 50
                    and -20 <= int(b["cy"]) - sign_y <= 200
                ),
                None,
            )
            if go_block:
                target_x, target_y = int(go_block["cx"]), int(go_block["cy"])
                print(f"  🎯 OCR 点击【去看看】@({target_x},{target_y})")
            else:
                target_x, target_y = sign_x + 200, sign_y
                print(f"  🎯 OCR 定位签到标题并点击右侧入口@({target_x},{target_y})")

            device.click(target_x, target_y)
            time.sleep(OCR_CLICK_WAIT_SECONDS)
            return

        print(f"  ⚠️ OCR 未找到【今日签到】（第 {scan_index}/8 次）")
        device.swipe(screen_width // 2, int(screen_height * 0.85),
                     screen_width // 2, int(screen_height * 0.2))
        time.sleep(OCR_RETRY_DELAY_SECONDS)

    print("  ⚠️ 未找到今日签到")


def _nav_to_shop(device):
    """固定点击元宝中心购物卡片，兼容双11活动改名。"""
    # 有/无“额外福利”横条时，购物卡片的共同覆盖区域。
    print("  🎯 固定点击购物入口 @(934,1530)")
    device.click(934, 1530)
    time.sleep(OCR_CLICK_WAIT_SECONDS)


def _nav_to_walk(device):
    """OCR 查找并点击走路赚元宝入口。"""
    _ocr_click_target(device, "走路赚元宝")


def _nav_to_yaoyiyao(device):
    """
    导航：元宝中心 → 点淘鸭家园 → 右滑2次 → OCR找去摇一摇
    （复用点淘鸭摇一摇.py 的导航逻辑，5次 OCR 重试）
    """
    # Step 1: OCR 定位并点击点淘鸭家园
    if not _ocr_click_target(device, "点淘鸭家园", click_wait=7):
        return

    # Step 2: 右滑2次到最右边
    print("  右滑2次到最右边...")
    for i in range(2):
        sx = int(screen_width * 0.15)
        ex = int(screen_width * 0.85)
        sy = int(screen_height * 0.50)
        device.swipe(sx, sy, ex, sy, duration=0.3)
        print(f"    第 {i+1}/2 次右滑")
        time.sleep(OCR_RETRY_DELAY_SECONDS)
    print("  ✅ 已右滑到最右边")

    # Step 3: OCR找"去摇一摇"并点击（5次重试）
    entered = _ocr_click_target(device, "去摇一摇")

    if not entered:
        print("  ⚠️ 未能进入摇一摇页面，跳过该轮")


# ========================================================
# 📋 轮次配置（每个轮次独立设置）
# ========================================================
# ✨ 修改以下配置即可调整每步的行为：
#   - swipe_seconds: 下滑浏览秒数
#   - nav_func:      导航函数（定义在上面）
ROUNDS_CONFIG = [
    {
        "label": "1",
        "name": "打工赚元宝",
        "swipe_seconds": 240,
        "nav_func": _nav_to_dagong,
    },
    {
        "label": "2",
        "name": "睡觉赚元宝",
        "swipe_seconds": 230,
        "nav_func": _nav_to_sleep,
    },
    {
        "label": "3",
        "name": "签到赢元宝",
        "swipe_seconds": 240,
        "nav_func": _nav_to_signin,
    },
    {
        "label": "4",
        "name": "购物赚钱",
        "swipe_seconds": 230,
        "nav_func": _nav_to_shop,
    },
    {
        "label": "5",
        "name": "摇一摇",
        "swipe_seconds": 230,
        "nav_func": _nav_to_yaoyiyao,
    },
    {
        "label": "6",
        "name": "走路赚元宝",
        "swipe_seconds": 240,
        "nav_func": _nav_to_walk,
    },
]


# ========================================================
# 下滑浏览
# ========================================================
def swipe_browse(seconds):
    """纯下滑浏览"""
    if seconds <= 0:
        print("  ⏭️ 跳过下滑（swipe_seconds <= 0）")
        return

    print(f"  📋 下滑浏览 {seconds}s")
    sw, sh = screen_width, screen_height
    start = time.time()
    count = 0
    while time.time() - start < seconds + 10:
        d.swipe(int(sw * 0.5), random.randint(int(sh * 0.80), int(sh * 0.90)),
                int(sw * 0.5), random.randint(int(sh * 0.15), int(sh * 0.25)))
        count += 1
        time.sleep(random.uniform(2, 4))
    print(f"  ✅ 浏览完成 ({count}次下滑)")


# ========================================================
# 执行一轮
# ========================================================
def run_round(label, name, swipe_seconds, nav_func):
    """执行一轮：重启点淘 → 元宝中心 → 导航目标页 → 下滑"""
    print(f"\n{'='*60}")
    print(f"  第 {label} 轮: {name}")
    print(f"  下滑时间: {swipe_seconds}s")
    print(f"{'='*60}")

    # Step 1: 重启点淘到首页
    print("  🚀 重启点淘...")
    d.app_start(DT_APP, stop=True)
    time.sleep(5)

    # Step 2: 进入元宝中心
    if not _enter_yuanbao_center_ocr(d):
        print("  ⚠️ 元宝中心导航或活动弹窗未完成，停止本轮")
        return

    # Step 3: 使用该轮次自己的导航函数进入目标页面
    nav_func(d)

    # Step 4: 下滑浏览
    swipe_browse(swipe_seconds)

    print(f"  ✅ 第 {label} 轮完成")


# ========================================================
# 主函数
# ========================================================
def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 60)
    print(" 🎮 一键下滑")
    print("   流程: 打工 → 睡觉 → 签到 → 购物赚钱 → 摇一摇 → 走路赚元宝")
    print("   每轮配置如下：")
    for r in ROUNDS_CONFIG:
        print(f"     {r['label']}. {r['name']}: 下滑 {r['swipe_seconds']}s")
    print("=" * 60)

    for r in ROUNDS_CONFIG:
        run_round(
            label=r["label"],
            name=r["name"],
            swipe_seconds=r["swipe_seconds"],
            nav_func=r["nav_func"],
        )

    print(f"\n{'='*60}")
    print(" ✅ 全部完成！")
    print(f"{'='*60}")


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
