"""首页养优惠：先完成浏览/媒体任务，再执行搜索宝贝浏览；按进度复核。"""
import re
import sys
import time

from task_recheck import report_round_finished, report_task_found

DT_APP = "com.taobao.live"
MAX_TASKS = 180
TITLE_RE = re.compile(r"^(搜索宝贝后浏览|看精彩直播|看精彩内容|看精美内容|看视频|逛直播|看直播|浏览)\d+秒")
PROGRESS_RE = re.compile(r"[（(](\d+)/(\d+)[）)]")


def normalize(text):
    return "".join(str(text).split())


def read_page(device):
    """沿用项目 OCR；实测 WebView 控件树包含隐藏弹层及失效坐标。"""
    from utils import get_ocr_text_blocks
    return get_ocr_text_blocks(device)


def parse_tasks(blocks, width):
    tasks = []
    for block in blocks:
        text = normalize(block["text"])
        match = TITLE_RE.match(text)
        if not match or any(kw in text for kw in ("下单", "购买", "充值", "付款", "邀请", "分享", "助力")):
            continue
        buttons = [b for b in blocks if normalize(b["text"]) in ("去完成", "去浏览", "去观看")
                   and b["cx"] > width * .65 and abs(b["cy"] - block["cy"]) < width * .06]
        if not buttons:
            continue
        button = min(buttons, key=lambda b: abs(b["cy"] - block["cy"]))
        row = text + "".join(normalize(b["text"]) for b in blocks
                             if b is not block and b["cx"] < button["cx"]
                             and abs(b["cy"] - block["cy"]) < width * .025)
        progress = PROGRESS_RE.search(row)
        done, total = map(int, progress.groups()) if progress else (0, None)
        if total is not None and done >= total:
            continue
        name = match.group()
        kind = "search" if name.startswith("搜索") else "live" if "直播" in name else "video" if "视频" in name or "内容" in name else "browse"
        tasks.append({"name": name, "key": (name, total), "kind": kind,
                      "done": done, "total": total,
                      "seconds": int(re.search(r"\d+", name).group()),
                      "cx": button["cx"], "cy": button["cy"]})
    return tasks


def click_text(device, texts, exact=False):
    for block in read_page(device):
        text = normalize(block["text"])
        if any(text == normalize(t) if exact else normalize(t) in text for t in texts):
            device.click(block["cx"], block["cy"])
            time.sleep(2)
            return True
    return False


def is_task_list(blocks):
    return any("做任务养更大" in normalize(b["text"]) for b in blocks)


def is_coupon_home(blocks):
    return any("连续签到" in normalize(b["text"]) for b in blocks) and not is_task_list(blocks)


def navigate(device):
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    # 首页搜索的是活动入口文字；不把“养优惠”提交为商品搜索词。
    for _ in range(3):
        if click_text(device, ["养优惠"], exact=True):
            return is_coupon_home(read_page(device))
        time.sleep(2)
    return False


def open_tasks(device):
    blocks = read_page(device)
    if is_task_list(blocks):
        return True
    if not is_coupon_home(blocks):
        return False
    return click_text(device, ["查看更多", "做任务最高抵"]) and is_task_list(read_page(device))


def scroll_tasks(device, down=False):
    width, height = device.window_size()
    # 只滑动底部任务弹层，避开背景优惠券和领取入口。
    start, end = (.42, .85) if down else (.85, .42)
    device.swipe(width // 2, int(height * start), width // 2, int(height * end), .3)
    time.sleep(1)


def collect_tasks(device, target=None):
    if not open_tasks(device):
        raise RuntimeError("未确认养优惠任务弹层，停止扫描")
    for _ in range(3):
        scroll_tasks(device, down=True)
    found, previous, recognized = {}, None, False
    for _ in range(6):
        blocks = read_page(device)
        if not is_task_list(blocks):
            raise RuntimeError("任务弹层识别失败，不能把空扫描当作任务完成")
        tasks = parse_tasks(blocks, device.window_size()[0])
        recognized |= any(TITLE_RE.match(normalize(b["text"])) or
                          any(kw in normalize(b["text"]) for kw in ("暂无任务", "任务已完成", "明日再来"))
                          for b in blocks)
        if target is not None:
            visible = next((t for t in tasks if t["key"] == target), None)
            if visible:
                return visible
        found.update((t["key"], t) for t in tasks)
        fingerprint = tuple((t["key"], t["done"]) for t in tasks)
        if fingerprint == previous:
            if not recognized:
                raise RuntimeError("未识别到任务或明确完成提示，不能把空扫描当作任务完成")
            return None if target is not None else list(found.values())
        previous = fingerprint
        scroll_tasks(device)
    raise RuntimeError("任务列表扫描未到末尾，停止而非误报完成")


def home_browse_progress(blocks, width):
    for block in blocks:
        if "滑动浏览" not in normalize(block["text"]):
            continue
        row = "".join(normalize(b["text"]) for b in sorted(blocks, key=lambda b: b["cx"])
                      if abs(b["cy"] - block["cy"]) < width * .035)
        duration, progress = re.search(r"滑动浏览(\d+)", row), PROGRESS_RE.search(row)
        if duration and progress:
            return int(duration[1]), int(progress[1]), int(progress[2])
    return None


def run_home_browse(device):
    """养优惠首页商品流自带浏览任务，执行器不返回，也不点击商品/下单。"""
    from u2_scanner import execute_browse_task
    width = device.window_size()[0]
    progress = home_browse_progress(read_page(device), width)
    if progress is None:
        return True  # 当前活动没有提供此任务。
    for _ in range(20):
        seconds, done, total = progress
        if done >= total:
            return True
        report_task_found()
        print(f"首页滑动浏览 {seconds} 秒 ({done}/{total})", flush=True)
        execute_browse_task(device, min_duration=seconds, max_wait=seconds + 60)
        after = home_browse_progress(read_page(device), width)
        if after is None:
            # 只有最后一次的任务提示消失才视为完成。
            return done + 1 == total
        if after[1] <= done:
            print("首页浏览进度未增加，停止重复滑动", flush=True)
            return False
        progress = after
    return progress[1] >= progress[2]


def return_to_tasks(device):
    # 执行器拥有一次正常返回；这里只处理搜索页等多层路径并逐层确认。
    for _ in range(5):
        blocks = read_page(device)
        if is_task_list(blocks) or is_coupon_home(blocks):
            return open_tasks(device)
        device.press("back")
        time.sleep(2)
    return False


def execute_media(device, task):
    from u2_scanner import execute_browse_task, execute_live_task, execute_video_task, detect_entered_media_type
    current = device.app_current()
    if current.get("package") != DT_APP:
        raise RuntimeError("任务跳转到外部应用，停止执行")
    activity = current.get("activity", "")
    kind = task["kind"]
    if "TaoLiveVideoActivity" in activity:
        kind = "live"
    elif "VideoListActivity" in activity:
        kind = "video"
    elif kind in ("live", "video"):
        kind = detect_entered_media_type(device, kind, task["name"])
    print(f"  实际页面: {kind}，最低 {task['seconds']} 秒", flush=True)
    if kind == "video":
        # 视频执行器内部返回，调用方不再返回。
        execute_video_task(device, task["name"], task["cx"], task["cy"], duration=task["seconds"])
    else:
        # 当前直播、浏览执行器均不返回；只在此处返回一次。
        if kind == "live":
            execute_live_task(device, task["name"], task["cx"], task["cy"], duration=task["seconds"] + 5)
        else:
            execute_browse_task(device, min_duration=task["seconds"], max_wait=task["seconds"] + 60)
        device.press("back")
        time.sleep(2)


def execute_search(device, task):
    """搜索落地页必须实际提交查询，再浏览结果；不能在输入页空等。"""
    from u2_scanner import execute_browse_task
    if device.app_current().get("package") != DT_APP:
        raise RuntimeError("搜索任务离开点淘，停止执行")
    if not click_text(device, ["输入想买的商品"]):
        raise RuntimeError("搜索任务未找到输入框，停止而非在错误页面浏览")
    # ASCII 商品词由系统输入，无须额外安装/切换手机输入法。
    device.shell(["input", "text", "USB"])
    time.sleep(1)
    width, height = device.window_size()
    blocks = read_page(device)
    if not any("USB" in b["text"].upper() and b["cy"] < height * .25 for b in blocks):
        raise RuntimeError("未确认搜索词输入成功")
    suggestion = next((b for b in blocks if normalize(b["text"]).lower().startswith("usb")
                       and len(normalize(b["text"])) > 3 and b["cx"] < width * .6
                       and height * .17 < b["cy"] < height * .6), None)
    submit = suggestion or next((b for b in blocks if normalize(b["text"]) in ("搜索", "领红包")
                   and b["cx"] > width * .65 and b["cy"] < height * .25), None)
    if submit is None:
        raise RuntimeError("未找到搜索提交按钮")
    device.click(submit["cx"], submit["cy"])
    time.sleep(3)
    if device.app_current().get("package") != DT_APP:
        raise RuntimeError("搜索任务离开点淘，停止执行")
    blocks = read_page(device)
    if not any("滑动浏览" in normalize(b["text"]) for b in blocks):
        raise RuntimeError("未确认搜索结果页，停止浏览")
    execute_browse_task(device, min_duration=task["seconds"], max_wait=task["seconds"] + 60)
    # 特殊备注：搜索输入和搜索结果可能有两层，由 return_to_tasks 逐层确认返回。
    device.press("back")
    time.sleep(2)


def run_tasks(device, max_tasks=MAX_TASKS):
    failed, completed = set(), 0
    for phase in ("normal", "search"):
        while True:
            tasks = collect_tasks(device)
            eligible = [t for t in tasks if (t["kind"] == "search") == (phase == "search")]
            pending = [t for t in eligible if t["key"] not in failed]
            if not pending:
                break
            if completed >= max_tasks:
                print("达到本轮次数上限，仍有任务待完成", flush=True)
                return False
            # 同一阶段每类轮流做，避免单个 20 次任务占住整个验证窗口。
            task = min(pending, key=lambda t: t["done"])
            task = collect_tasks(device, task["key"])
            if task is None:
                raise RuntimeError("任务定位时列表已变化，请重新运行")
            print(f"\n执行 {task['name']} ({task['done']}/{task['total'] or '?'})", flush=True)
            report_task_found()
            device.click(task["cx"], task["cy"])
            time.sleep(3)
            try:
                if task["kind"] == "search":
                    execute_search(device, task)
                else:
                    execute_media(device, task)
            except RuntimeError as exc:
                print(f"  未完成：{exc}", flush=True)
                failed.add(task["key"])
            if not return_to_tasks(device):
                raise RuntimeError("无法返回养优惠任务列表")
            after = collect_tasks(device)
            current = next((t for t in after if t["key"] == task["key"]), None)
            if task["key"] not in failed and (current is None or current["done"] > task["done"]):
                print("  已确认进度增加或任务完成退出列表", flush=True)
            else:
                print("  进度未增加，本轮跳过以避免重复点击", flush=True)
                failed.add(task["key"])
            completed += 1
    report_round_finished()
    return not failed


def main():
    from utils import connect_device
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    device = connect_device()
    if not navigate(device):
        print("未确认首页养优惠入口")
        return 1
    home_ok = run_home_browse(device)
    # 商品流浏览后重新从首页进入，以恢复任务弹层入口位置。
    if not navigate(device):
        return 1
    tasks_ok = run_tasks(device)
    return 0 if home_ok and tasks_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
