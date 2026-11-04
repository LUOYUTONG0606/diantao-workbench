"""点淘任务入口连通性排障脚本。

沿用一键下滑.py 的六个入口导航流程，每个入口从点淘首页重新开始并尝试三次。
本脚本只验证入口路径，不执行入口页面内的计时下滑任务。
"""
import sys
import time
from yuanbao_popup import handle_yuanbao_live_popups

from u2_scanner import connect_device, DT_APP
from utils import get_ocr_text_blocks


ATTEMPTS_PER_ENTRY = 3
APP_START_WAIT_SECONDS = 8
CENTER_OCR_PRE_SCAN_DELAY_SECONDS = 5
CENTER_OCR_SCAN_ATTEMPTS = 3
CENTER_OCR_RETRY_DELAY_SECONDS = 3
STEP_SETTLE_SECONDS = 3
SWIPE_SETTLE_SECONDS = 3
RETRY_SETTLE_SECONDS = 3
ACTION_SETTLE_SECONDS = 5
DUCK_OPEN_WAIT_SECONDS = 7


def _ocr_click_target(device, keyword, label=None, attempts=5,
                      click_wait=ACTION_SETTLE_SECONDS):
    """等待页面稳定后，用 OCR 查找入口文本并点击识别坐标。"""
    label = label or keyword
    print(f"  🔍 OCR 查找【{label}】...")
    time.sleep(STEP_SETTLE_SECONDS)

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
            time.sleep(RETRY_SETTLE_SECONDS)

    print(f"  ⚠️ 未能通过 OCR 进入【{label}】")
    return False


def _nav_to_dagong(device, sw, sh):
    """一键下滑.py 路径：OCR 定位打工赚元宝。"""
    _ocr_click_target(device, "打工赚元宝")


def _nav_to_sleep(device, sw, sh):
    """一键下滑.py 路径：OCR 定位睡觉赚元宝。"""
    _ocr_click_target(device, "睡觉赚元宝")


def _scan_and_enter_signin(device, sw, sh):
    """下滑后用 OCR 查找今日签到，并点击同一行的去看看入口。"""
    time.sleep(STEP_SETTLE_SECONDS)
    for scan_index in range(8):
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
                # 与旧版一键下滑的签到入口兜底一致：从 OCR 识别到的标题定位右侧入口。
                target_x, target_y = sign_x + 200, sign_y
                print(f"  🎯 OCR 定位【今日签到】并点击右侧入口@({target_x},{target_y})")

            device.click(target_x, target_y)
            time.sleep(ACTION_SETTLE_SECONDS)
            return True

        print(f"  ⚠️ OCR 未找到【今日签到】（第 {scan_index + 1}/8 次）")
        device.swipe(
            sw // 2,
            int(sh * 0.85),
            sw // 2,
            int(sh * 0.20),
        )
        time.sleep(SWIPE_SETTLE_SECONDS)

    print("  ⚠️ 本次未通过 OCR 找到【今日签到】")
    return False


def _nav_to_signin(device, sw, sh):
    """每轮只按 OCR 扫描并尝试点击一次；固定三轮由外层循环负责。"""
    print("  📜 下滑扫描签到入口...")
    _scan_and_enter_signin(device, sw, sh)


def _nav_to_shop(device, sw, sh):
    """与购物任务一致，固定点击元宝中心双11活动卡片。"""
    print("  🎯 固定点击购物入口 @(934,1530)")
    device.click(934, 1530)
    time.sleep(ACTION_SETTLE_SECONDS)


def _nav_to_yaoyiyao(device, sw, sh):
    """一键下滑.py 路径：点淘鸭家园 → 右滑两次 → OCR 点击去摇一摇。"""
    if not _ocr_click_target(
        device, "点淘鸭家园", click_wait=DUCK_OPEN_WAIT_SECONDS
    ):
        return

    for swipe_index in range(2):
        sy = int(sh * 0.50)
        device.swipe(int(sw * 0.15), sy, int(sw * 0.85), sy, duration=0.3)
        print(f"  👉 右滑 {swipe_index + 1}/2 次")
        time.sleep(SWIPE_SETTLE_SECONDS)

    _ocr_click_target(device, "去摇一摇")


def _nav_to_walk(device, sw, sh):
    """一键下滑.py 路径：OCR 扫描并点击走路赚元宝。"""
    _ocr_click_target(device, "走路赚元宝")


ENTRY_ROUTES = (
    ("打工赚元宝", _nav_to_dagong),
    ("睡觉赚元宝", _nav_to_sleep),
    ("签到赢元宝", _nav_to_signin),
    ("购物赚钱", _nav_to_shop),
    ("摇一摇", _nav_to_yaoyiyao),
    ("走路赚元宝", _nav_to_walk),
)


def _enter_yuanbao_center_ocr(device):
    """等待首页稳定后，用 OCR 查找并点击元宝中心。"""
    for scan_index in range(1, CENTER_OCR_SCAN_ATTEMPTS + 1):
        blocks = get_ocr_text_blocks(device)
        target = next(
            (b for b in blocks if "元宝中心" in b.get("text", "")),
            None,
        )
        if target:
            target_x, target_y = int(target["cx"]), int(target["cy"])
            print(f"  🎯 OCR 点击【元宝中心】@({target_x},{target_y})")
            device.click(target_x, target_y)
            return handle_yuanbao_live_popups(device)

        print(
            f"  ⚠️ OCR 未找到【元宝中心】（第 "
            f"{scan_index}/{CENTER_OCR_SCAN_ATTEMPTS} 次）"
        )
        if scan_index < CENTER_OCR_SCAN_ATTEMPTS:
            time.sleep(CENTER_OCR_RETRY_DELAY_SECONDS)

    return False


def _run_attempt(device, entry_name, nav_func, sw, sh, attempt_index):
    print(f"\n{'=' * 56}")
    print(f"  【{entry_name}】第 {attempt_index}/{ATTEMPTS_PER_ENTRY} 次连通性尝试")
    print(f"{'=' * 56}")

    try:
        print("  🚀 重启点淘，回到首页...")
        device.app_start(DT_APP, stop=True)
    except Exception as exc:
        print(f"  ⚠️ 启动点淘失败，仍继续本轮后续步骤：{exc}")
    time.sleep(APP_START_WAIT_SECONDS)

    print(
        f"  ⏳ 等待点淘首页稳定 "
        f"{CENTER_OCR_PRE_SCAN_DELAY_SECONDS}s，再用 OCR 定位元宝中心..."
    )
    time.sleep(CENTER_OCR_PRE_SCAN_DELAY_SECONDS)

    try:
        print("  🧭 OCR 查找元宝中心...")
        if not _enter_yuanbao_center_ocr(device):
            print("  ⚠️ 元宝中心导航或活动弹窗未完成，停止本次入口尝试")
            return
    except Exception as exc:
        print(f"  ⚠️ 元宝中心导航中断，停止本次入口尝试：{exc}")
        return
    time.sleep(STEP_SETTLE_SECONDS)

    try:
        nav_func(device, sw, sh)
        print(f"  ✅ 已执行【{entry_name}】入口导航函数")
    except Exception as exc:
        print(f"  ⚠️【{entry_name}】入口步骤中断：{exc}")
    time.sleep(STEP_SETTLE_SECONDS)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 56)
    print("🔌 点淘连通性脚本：6 个入口 × 每个固定尝试 3 次")
    print("只执行首页到入口的导航，不执行入口页面内的计时下滑。")
    print("=" * 56)

    device = connect_device()
    sw, sh = device.window_size()
    print(f"📱 屏幕分辨率: {sw} x {sh}")

    for entry_name, nav_func in ENTRY_ROUTES:
        for attempt_index in range(1, ATTEMPTS_PER_ENTRY + 1):
            _run_attempt(device, entry_name, nav_func, sw, sh, attempt_index)

    print(f"\n{'=' * 56}")
    print("✅ 六个入口的固定连通性尝试均已执行完毕。")
    print(f"{'=' * 56}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ 用户手动中断连通性脚本")
    except Exception as exc:
        print(f"\n❌ 连通性脚本启动失败：{exc}")
        import traceback
        traceback.print_exc()
    finally:
        print("👋 连通性排障结束")
