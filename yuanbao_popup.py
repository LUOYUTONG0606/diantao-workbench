"""元宝中心活动弹窗：只执行 OCR 确认的十秒直播任务。"""
import re
import time


def _text(block):
    return re.sub(r"[\s\u200b-\u200d\ufeff]+", "", str(block.get("text", "")))


def _reward_button(blocks, width, height):
    return next((block for block in blocks
                 if _text(block) == "立即领奖"
                 and 0.2 * width < block.get("cx", 0) < 0.85 * width
                 and 0.25 * height < block.get("cy", 0) < 0.9 * height), None)


def _is_ten_second_live(blocks, button, width, height):
    # 只拼接按钮上方弹窗区域，避免被遮罩背后的普通任务误匹配。
    nearby = sorted((block for block in blocks
                     if abs(block.get("cx", 0) - button["cx"]) < 0.35 * width
                     and 0 < button["cy"] - block.get("cy", 0) < 0.35 * height),
                    key=lambda block: (block["cy"], block["cx"]))
    text = "".join(_text(block) for block in nearby)
    return (bool(re.search(r"(?:看|观看)[^。！？]{0,12}直播10秒|看10秒直播", text))
            and not any(word in text for word in ("视频", "下单", "购买", "邀请", "浏览")))


def handle_yuanbao_live_popups(device, max_tasks=30):
    """清空连续直播弹窗；确认元宝中心可继续时返回 True，异常时返回 False。"""
    from utils import get_ocr_text_blocks
    from u2_scanner import classify_media_page

    width, height = device.window_size()
    completed = 0
    clear_scans = 0
    time.sleep(3)
    while True:
        # 空 OCR 不能作为“没有弹窗”的证据。
        blocks = []
        for attempt in range(3):
            blocks = get_ocr_text_blocks(device, retries=0)
            if blocks:
                break
            time.sleep(2)
        if not blocks:
            print("  ⚠️ 活动弹窗 OCR 连续为空，停止当前导航")
            return False

        button = _reward_button(blocks, width, height)
        if button:
            clear_scans = 0
            if not _is_ten_second_live(blocks, button, width, height):
                print("  ⚠️ 活动弹窗不是十秒直播任务，不点击领奖，停止当前导航")
                return False
            if completed >= max_tasks:
                print("  ⚠️ 活动直播弹窗达到执行上限仍未清空，停止当前导航")
                return False
            device.click(int(button["cx"]), int(button["cy"]))
            print(f"  📺 活动直播任务 {completed + 1}：点击立即领奖")
            media_type = None
            for attempt in range(3):
                time.sleep(2)
                live_blocks = get_ocr_text_blocks(device, retries=0)
                # 点击未生效时不能把原弹窗中的“直播”当成已进入直播间。
                if _reward_button(live_blocks, width, height):
                    continue
                media_type = classify_media_page(live_blocks)
                if media_type:
                    break
            if media_type != "live":
                print("  ⚠️ 未确认进入直播间，停止当前导航")
                return False
            # 从直播页确认后开始计时，额外两秒留给奖励提交。
            time.sleep(12)
            device.press("back")
            completed += 1
            print(f"  🔙 十秒直播观看结束，返回后重扫活动弹窗（已执行 {completed} 个）")
            time.sleep(3)
            continue

        texts = [_text(block) for block in blocks]
        if not any(any(marker in text for marker in
                       ("元宝中心", "我的元宝", "兑红包", "走路赚元宝", "打工赚元宝"))
                   for text in texts):
            print("  ⚠️ 未确认回到元宝中心，停止当前导航")
            return False
        clear_scans += 1
        if clear_scans >= 2:
            print(f"  ✅ 元宝中心活动直播弹窗已清空（已执行 {completed} 个），继续原流程")
            return True
        # 给返回后延迟出现的下一轮弹窗留一次复核机会。
        time.sleep(2)
