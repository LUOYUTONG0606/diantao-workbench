"""
走路赚元宝 自动化脚本
流程：首页 → 元宝中心 → 走路赚元宝 → 上传/消耗步数 → 赚步数任务列表
优先级：福利直播 > 看直播 > 猜价格 > 浏览精选

采用纯 OCR + 坐标配对方案（参考打工得体力.py）
"""
import time
import sys
import re
from task_recheck import report_task_found, report_round_finished
from utils import (
    connect_device, get_ocr_text_blocks, check_can_open,
    log_task_completed, show_today_summary,
    DT_APP, pause_global_watcher, resume_global_watcher,
)
from u2_scanner import (
    execute_live_task, execute_video_task, execute_browse_task,
    handle_guess_price_game,
)

# ========================================================
# 全局变量
# ========================================================
d = connect_device()
screen_width, screen_height = d.window_size()
print(f"屏幕分辨率: {screen_width} x {screen_height}")

MAX_SCROLL = 12
MAX_TASK_ATTEMPTS = 3

# 黑名单关键词
BLACKLIST_KEYWORDS = [
    "元宝商城", "下单", "购买", "充值", "付款",
    "邀请", "邀请好友", "拉好友", "助力", "分享", "去邀请",
    "喂鸡", "蚂蚁庄园", "蚂蚁新村", "饿了么", "果园",
    "消消乐", "农场小游戏", "斗地主", "开心消消乐",
    "猜谜", "猜一猜",
    "查看并完成", "玩小游戏",
    "同程旅行", "同城旅行", "头条极速版",
    "搜索", "找低价",  # 搜索任务仅通过下面的精确标题规则放行
    # 用户要求屏蔽“查看3个精选宝贝 / 看3个精选宝贝 / 精选宝贝”整类任务。
    "精选宝贝",
]

# 白名单：只执行白名单中的任务，其他任务一律跳过
# 走路赚元宝页面的典型安全任务列表
WHITELIST = [
    "看福利直播",
    "看直播",
    "直播",
    "猜价格",
    "浏览元宝商城",
    "浏览精选推荐",
    "浏览上新好物",
    "浏览当季上新好物",
    "浏览省钱专区",
    "浏览神券专区",
    "浏览",
    "签到",
    "查看",
    "看视频",
]

# 去完成按钮关键词
DONE_BTN_KEYWORDS = ["去完成", "去浏览", "去观看", "去参与", "去领取", "去签到", "立即参与", "立即领取"]

# 任务类型优先级：福利直播 > 看直播 > 猜价格 > 浏览
TASK_PRIORITY = {
    "live_welfare": 1,
    "live": 2,
    "video": 2,
    "guess_price": 3,
    "browse": 4,
    "search_browse": 4,
    "search_low_price": 3,
}


def parse_seconds(text, default=60):
    """从任务名提取秒数"""
    m = re.search(r"(\d+)\s*秒", text)
    return int(m.group(1)) if m else default


def match_task_type(text):
    """精确匹配任务名 → 返回类型字符串"""
    if is_search_browse_task(text):
        return "search_browse"
    if is_walk_low_price_task(text):
        return "search_low_price"
    if "搜索" in text or "找低价" in text:
        return "other"
    if "福利直播" in text or ("看直播" in text and "福利" in text):
        return "live_welfare"
    if "看视频" in text:
        return "video"
    if "猜价格" in text:
        return "guess_price"
    if "直播" in text:
        return "live"
    if "浏览" in text or "精选推荐" in text or "猜你喜欢" in text \
       or "省钱" in text or "上新好物" in text or "签到" in text \
       or "查看" in text:
        return "browse"
    return "other"


def detect_task_name(blocks, btn_block):
    """同一行优先；用相邻按钮的中线隔离任务，合并拆行的搜索低价标题。"""
    cx, cy = btn_block["cx"], btn_block["cy"]
    scale = screen_height / 2400
    buttons = [b for b in blocks if is_task_button(b)]
    above = [b["cy"] for b in buttons if b["cy"] < cy - 20 * scale]
    below = [b["cy"] for b in buttons if b["cy"] > cy + 20 * scale]
    lower = max(cy - 180 * scale, (max(above) + cy) / 2 if above else 0)
    upper = min(cy + 90 * scale, (min(below) + cy) / 2 if below else screen_height)
    labels = []
    for block in blocks:
        text = block["text"].strip()
        if not (0 < block["cx"] < cx and lower < block["cy"] < upper):
            continue
        if is_task_button(block) or re.fullmatch(r"[\d\s()+/，,]+", text):
            continue
        if re.match(r"^(额外得|额外返|奖励|\+|得\d|.{0,2}[+＋]\s*\d)", text):
            continue
        if text in ("赚步数", "任务列表", "每日任务", "随机任务", "Q"):
            continue
        if re.fullmatch(r"(?:第\d+天|明天|\s)+", text):
            continue
        labels.append(block)
    if not labels:
        return ""
    # 任何标题片段包含禁用词，整行都不可执行，不能回退到邻行的安全任务。
    joined = "".join(b["text"] for b in sorted(labels, key=lambda b: (b["cy"], b["cx"])))
    if is_search_browse_task(joined) or is_walk_low_price_task(joined):
        return joined
    if is_blacklisted(joined):
        return joined
    titles = [b for b in labels if is_whitelisted(b["text"])]
    if not titles:
        return ""
    target = min(titles, key=lambda b: abs(b["cy"] - cy))
    return target["text"]


def is_task_button(block):
    return block["text"].strip() in DONE_BTN_KEYWORDS


def is_walk_map(blocks):
    texts = [b["text"] for b in blocks]
    return any("现在可走" in t for t in texts) and any("出发" in t for t in texts)


def is_walk_task_list(blocks):
    if is_walk_map(blocks) or is_walk_reward(blocks):
        return False
    texts = [b["text"] for b in blocks]
    heading = any(any(k in t for k in ("赚步数", "步数任务", "任务列表", "每日任务")) for t in texts)
    return heading and any(is_task_button(b) or "已完成" in b["text"] or "暂无任务" in b["text"] for b in blocks)


def is_walk_reward(blocks):
    texts = [b["text"] for b in blocks]
    return any("恭喜额外获得" in t for t in texts) and any("步数" in t and "奖励" in t for t in texts)


def navigate_to_walk_page(device):
    """复用其他任务的首页启动和 OCR 导航链。"""
    from u2_scanner import navigate_chain
    device.app_start(DT_APP, stop=True)
    time.sleep(5)
    if not navigate_chain(device, [("元宝中心", "元宝中心"), ("走路赚元宝", "走路赚元宝")]):
        return False
    return is_walk_map(get_ocr_text_blocks(device))


def read_available_steps(blocks):
    """只读取‘现在可走’，避免把‘今日已走’和里程碑奖励当成剩余步数。"""
    return read_step_counter(blocks, "现在可走")


def read_step_counter(blocks, keyword):
    for label in blocks:
        if keyword not in label["text"]:
            continue
        match = re.search(re.escape(keyword) + r"\s*([\d,，]+)", label["text"])
        if match:
            return int(re.sub(r"[,，]", "", match[1]))
        nearby = [b for b in blocks
                  if abs(b["cx"] - label["cx"]) < screen_width * .12
                  and 0 < b["cy"] - label["cy"] < screen_height * .06
                  and re.fullmatch(r"[\d,，]+", b["text"].strip())]
        if nearby:
            number = min(nearby, key=lambda b: b["cy"] - label["cy"])
            return int(re.sub(r"[,，]", "", number["text"].strip()))
    return None


def prepare_walk_steps():
    """先扫描上传入口，再点击出发；每次点击后重扫，验证步数确实减少。"""
    blocks = get_ocr_text_blocks(d)
    if not is_walk_map(blocks):
        return False
    upload = next((b for b in blocks if b["text"].strip() in
                   ("上传步数", "同步步数", "领取步数", "转步数")), None)
    if upload:
        d.click(int(upload["cx"]), int(upload["cy"]))
        time.sleep(3)
        check_can_open(d)
        blocks = get_ocr_text_blocks(d)
        if not is_walk_map(blocks):
            print("  ⚠️ 上传后未返回走路页，停止")
            return False
    else:
        print("  扫描完成，页面没有独立上传入口，读取当前可走步数")
    previous = None
    for _ in range(8):
        steps = read_available_steps(blocks)
        if steps is None:
            print("  ⚠️ 无法识别现在可走步数，停止")
            return False
        if steps == 0:
            return True
        if previous is not None and steps >= previous:
            print(f"  ⚠️ 出发后步数未减少 ({previous} → {steps})，停止重复点击")
            return False
        departure = next((b for b in blocks if b["text"].strip() == "出发"), None)
        if not departure:
            return False
        print(f"  🚶 当前可走 {steps} 步，点击出发")
        walked_before = read_step_counter(blocks, "今日已走")
        d.click(int(departure["cx"]), int(departure["cy"]))
        previous = steps
        # 等待走路动画结束，期间不重复点击，也不使用旧截图坐标。
        for _ in range(5):
            time.sleep(2)
            blocks = get_ocr_text_blocks(d)
            current = read_available_steps(blocks)
            walked_after = read_step_counter(blocks, "今日已走")
            if (is_walk_reward(blocks) and walked_before is not None
                    and walked_after is not None and walked_after >= walked_before + steps):
                print("  已确认步数转入今日已走，接下来处理奖励弹窗")
                return True
            if is_walk_map(blocks) and current is not None and current < previous:
                break
        else:
            print("  ⚠️ 未确认出发后的步数变化，停止")
            return False
    if read_available_steps(blocks) == 0:
        return True
    print("  ⚠️ 步数处理达到次数上限")
    return False


def open_walk_task_list(blocks=None):
    """从已确认的走路主页点击赚步数，并验证任务面板确实打开。"""
    blocks = get_ocr_text_blocks(d) if blocks is None else blocks
    if is_walk_task_list(blocks):
        return True
    if is_walk_reward(blocks):
        # “去领步数”会进入推荐直播；这里只打开明确的任务列表入口。
        entry_name = "查看更多任务"
    elif is_walk_map(blocks):
        entry_name = "赚步数"
    else:
        return False
    entry = next((b for b in blocks if b["text"].strip() == entry_name), None)
    if not entry:
        return False
    d.click(int(entry["cx"]), int(entry["cy"]))
    for _ in range(3):
        time.sleep(2)
        if is_walk_task_list(get_ocr_text_blocks(d)):
            return True
    return False


def restore_walk_task_list(blocks):
    """任务返回后重认页面；最多返回三次，禁止在商品页继续扫任务。"""
    for attempt in range(4):
        if is_walk_task_list(blocks):
            return True
        if is_walk_map(blocks) or is_walk_reward(blocks):
            return open_walk_task_list(blocks)
        if attempt < 3:
            d.press("back")
            time.sleep(2)
            blocks = get_ocr_text_blocks(d)
    return False


def is_search_browse_task(text):
    """只放行搜索宝贝后限时浏览，不放行搜索找低价或购买任务。"""
    compact = re.sub(r"\s+", "", text)
    return bool(re.fullmatch(r"搜索宝贝后浏览\d+秒(?:[（(]\d+/\d+[）)])?", compact))


def is_walk_low_price_task(text):
    compact = re.sub(r"\s+", "", text)
    return bool(re.fullmatch(r"玩(?:1|一)次搜索并找低价(?:[（(]\d+/\d+[）)])?", compact))


def is_blacklisted(text):
    """检查任务名是否在黑名单中"""
    if not text:
        return True
    if is_search_browse_task(text) or is_walk_low_price_task(text):
        return False
    # 走路任务中的商城限时浏览已确认可执行，只放行该任务标题。
    compact = re.sub(r"\s+", "", text)
    if re.fullmatch(r"浏览元宝商城(?:\d+秒)?(?:[（(]\d+/\d+[）)])?", compact):
        return False
    return any(kw in text for kw in BLACKLIST_KEYWORDS)


def is_whitelisted(text):
    """白名单自读：检查任务名是否在白名单中
    只有白名单中的任务才会被执行，其他一律跳过
    """
    if not text:
        return False
    if is_search_browse_task(text) or is_walk_low_price_task(text):
        return True
    return any(kw in text for kw in WHITELIST)


def parse_tasks_from_blocks(blocks):
    """从 OCR 文本块中解析出所有可执行任务
    返回: [(priority, task_type, task_name, btn_cx, btn_cy), ...]
          按优先级排序：福利直播 > 看直播 > 猜价格 > 浏览
    """
    candidates = []
    for b in blocks:
        if not is_task_button(b):
            continue

        task_name = detect_task_name(blocks, b)
        if not task_name:
            continue

        # 🟢 白名单自读：只执行白名单中的任务
        if not is_whitelisted(task_name):
            print(f"    ⊘ 非白名单跳过: 【{task_name}】")
            continue

        # 黑名单过滤（二级安全网）
        if is_blacklisted(task_name):
            print(f"    ⊘ 黑名单跳过: 【{task_name}】")
            continue

        task_type = match_task_type(task_name)
        if task_type == "other":
            continue

        priority = TASK_PRIORITY[task_type]
        candidates.append((priority, task_type, task_name, int(b["cx"]), int(b["cy"])))

    candidates.sort(key=lambda x: x[0])
    return candidates


def exec_live(btn_cx, btn_cy, task_name=""):
    """执行看直播任务"""
    print(f"  🎯 {task_name}")
    d.click(btn_cx, btn_cy)
    time.sleep(0.5)
    check_can_open(d)
    execute_live_task(d, task_name, btn_cx, btn_cy)
    d.press("back")
    time.sleep(1.5)


def exec_video(btn_cx, btn_cy, task_name=""):
    """执行看视频任务：入口点击后只定时上滑，到点返回。"""
    print(f"  🎯 {task_name}")
    pause_global_watcher(d)
    try:
        d.click(btn_cx, btn_cy)
        execute_video_task(
            d, task_name, btn_cx, btn_cy,
            verify_media_page=True,
        )
    finally:
        resume_global_watcher(d)


def exec_guess_price(btn_cx, btn_cy, task_name=""):
    """执行猜价格任务"""
    print(f"  🎯 {task_name}")
    d.click(btn_cx, btn_cy)
    time.sleep(3)
    check_can_open(d)
    handle_guess_price_game(d)


def exec_browse(btn_cx, btn_cy, task_name=""):
    """执行浏览任务（使用OCR智能监控，避免多等extra_seconds）"""
    print(f"  🎯 {task_name}")
    d.click(btn_cx, btn_cy)
    time.sleep(3)
    check_can_open(d)

    duration = parse_seconds(task_name, default=90)
    execute_browse_task(d, min_duration=duration, max_wait=duration + 30)

    d.press("back")
    time.sleep(2)


def read_search_step_balance():
    """关闭任务面板读取可走步数，再重新打开，返回最新任务坐标。"""
    blocks = get_ocr_text_blocks(d)
    if not is_walk_task_list(blocks):
        return None, []
    heading = next((b for b in blocks if "赚步数" in b["text"]), None)
    close = next((b for b in blocks if b["text"].strip() in ("X", "×", "x")
                  and b["cx"] > screen_width * .8 and heading
                  and abs(b["cy"] - heading["cy"]) < screen_height * .05), None)
    if not close:
        return None, blocks
    d.click(int(close["cx"]), int(close["cy"]))
    time.sleep(1)
    blocks = get_ocr_text_blocks(d)
    if not is_walk_map(blocks):
        return None, []
    balance = read_available_steps(blocks)
    if not open_walk_task_list(blocks):
        return None, []
    return balance, get_ocr_text_blocks(d)


def exec_search_browse(btn_cx, btn_cy, task_name=""):
    """入口、框体、键盘候选和搜索按钮均从当前 OCR 定位。"""
    print(f"  🎯 {task_name}")
    pause_global_watcher(d)
    try:
        before, tasks = read_search_step_balance()
        if tasks:
            fresh = next((t for t in parse_tasks_from_blocks(tasks) if t[1] == "search_browse"), None)
            if not fresh:
                return False
            btn_cx, btn_cy = fresh[3:5]
        elif before is None:
            return False
        d.click(btn_cx, btn_cy)
        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        field = next((b for b in blocks if "输入想买的商品" in b["text"]), None)
        if not field or not any("搜索领步数" in b["text"] for b in blocks):
            return False
        d.click(int(field["cx"]), int(field["cy"]))
        time.sleep(1)
        # 手机已有搜狗输入法；通过拼音候选输入中文，避免 send_keys 安装 ATX。
        d.shell(["input", "text", "yashua"])
        time.sleep(1)
        blocks = get_ocr_text_blocks(d)
        candidate = next((b for b in blocks if "牙刷" in b["text"]
                          and b["cy"] > screen_height * .6), None)
        if not candidate:
            return False
        d.click(int(candidate["cx"]), int(candidate["cy"]))
        time.sleep(1)
        blocks = get_ocr_text_blocks(d)
        if not any("牙刷" in b["text"] and b["cy"] < screen_height * .2 for b in blocks):
            return False
        button = next((b for b in blocks if b["text"].strip() in ("搜索", "领步数", "下一步")
                       and b["cx"] > screen_width * .7 and b["cy"] < screen_height * .2), None)
        if not button:
            return False
        d.click(int(button["cx"]), int(button["cy"]))
        for _ in range(3):
            time.sleep(2)
            blocks = get_ocr_text_blocks(d)
            if any(re.search(r"滑动浏览\s*\d+", b["text"]) for b in blocks):
                break
        else:
            print("  ⚠️ 搜索后未识别到浏览倒计时，停止")
            return False
        duration = parse_seconds(task_name, default=45)
        if not execute_browse_task(d, min_duration=duration, max_wait=duration + 30):
            return False
        d.press("back")
        time.sleep(2)
        if not restore_walk_task_list(get_ocr_text_blocks(d)):
            return False
        blocks = get_ocr_text_blocks(d)
        if not blocks or not is_walk_task_list(blocks):
            return False
        titles = [b for b in blocks if is_search_browse_task(b["text"])]
        if any(b["text"].strip() == "已完成" and b["cx"] > title["cx"]
               and abs(b["cy"] - title["cy"]) < screen_height * .04
               for title in titles for b in blocks):
            return True
        # 随机任务可重复执行，按钮会保留；以实际增加的可走步数验证奖励。
        after, _ = read_search_step_balance()
        print(f"  可走步数：{before} → {after}")
        return before is not None and after is not None and after > before
    finally:
        resume_global_watcher(d)


def close_walk_low_price(blocks):
    """OCR 确认找低价弹窗，图像检测定位右上角圆形关闭框。"""
    import cv2
    heading = next((b for b in blocks if "找最低价" in b["text"]
                    and b["cx"] < screen_width * .7), None)
    if not heading:
        return False
    x0 = int(screen_width * .85)
    y0 = max(0, int(heading["cy"] - screen_height * .1))
    y1 = int(heading["cy"] - screen_height * .03)
    img = d.screenshot(format="opencv")
    gray = cv2.cvtColor(img[y0:y1, x0:screen_width], cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 80,
                               param1=100, param2=20,
                               minRadius=int(screen_width * .025),
                               maxRadius=int(screen_width * .05))
    if circles is None or len(circles[0]) != 1:
        return False
    x, y, _ = circles[0][0]
    d.click(x0 + int(x), y0 + int(y))
    time.sleep(1)
    return True


def exec_search_low_price(btn_cx, btn_cy, task_name=""):
    """搜索后参与一次找低价；确认步数奖励即返回，不耗尽额外猜价机会。"""
    pause_global_watcher(d)
    try:
        before, tasks = read_search_step_balance()
        fresh = next((t for t in parse_tasks_from_blocks(tasks) if t[1] == "search_low_price"), None)
        if not fresh:
            return False
        d.click(fresh[3], fresh[4])
        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        field = next((b for b in blocks if "请输入想找的商品" in b["text"]), None)
        if not field or not any("寻找最低价格" in b["text"] for b in blocks):
            return False
        d.click(int(field["cx"]), int(field["cy"]))
        time.sleep(1)
        d.shell(["input", "text", "yashua"])
        time.sleep(1)
        blocks = get_ocr_text_blocks(d)
        candidate = next((b for b in blocks if "牙刷" in b["text"]
                          and b["cy"] > screen_height * .6), None)
        if not candidate:
            return False
        d.click(int(candidate["cx"]), int(candidate["cy"]))
        # 收起键盘后重新定位；弹窗和按钮会随键盘状态上下移动。
        d.press("back")
        time.sleep(1)
        blocks = get_ocr_text_blocks(d)
        if not any(b["text"].strip() == "牙刷" and b["cy"] < screen_height * .6 for b in blocks):
            return False
        search = next((b for b in blocks if b["text"].strip() == "找最低价"
                       and b["cx"] > screen_width * .7), None)
        if not search:
            return False
        d.click(int(search["cx"]), int(search["cy"]))
        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        if not any("找找哪个商品价格最低" in b["text"] for b in blocks):
            return False
        prices = [b for b in blocks if re.fullmatch(r"[￥¥]?\?{2,}", b["text"].strip())
                  and b["cy"] > screen_height * .4]
        if not prices:
            return False
        price = min(prices, key=lambda b: (b["cy"], b["cx"]))
        d.click(int(price["cx"]), int(price["cy"]))
        time.sleep(1)
        blocks = get_ocr_text_blocks(d)
        submit = next((b for b in blocks if b["text"].strip() == "猜它价格最低"), None)
        if not submit:
            return False
        d.click(int(submit["cx"]), int(submit["cy"]))
        time.sleep(2)
        blocks = get_ocr_text_blocks(d)
        rewarded = any(re.search(r"恭喜完成任务.*获得\d+步数", b["text"]) for b in blocks)
        if not close_walk_low_price(blocks):
            return False
        if not restore_walk_task_list(get_ocr_text_blocks(d)):
            return False
        after, _ = read_search_step_balance()
        print(f"  搜索找低价可走步数：{before} → {after}")
        return rewarded or (before is not None and after is not None and after > before)
    finally:
        resume_global_watcher(d)


def execute_task(task_type, task_name, btn_cx, btn_cy):
    """根据任务类型执行"""
    if is_blacklisted(task_name) or not is_whitelisted(task_name):
        return False
    if is_search_browse_task(task_name):
        if not exec_search_browse(btn_cx, btn_cy, task_name):
            return False
    elif is_walk_low_price_task(task_name):
        if not exec_search_low_price(btn_cx, btn_cy, task_name):
            return False
    elif task_type == "video":
        exec_video(btn_cx, btn_cy, task_name)
    elif task_type in ("live_welfare", "live"):
        exec_live(btn_cx, btn_cy, task_name)
    elif task_type == "guess_price":
        exec_guess_price(btn_cx, btn_cy, task_name)
    elif task_type == "browse":
        exec_browse(btn_cx, btn_cy, task_name)
    else:
        return False

    try:
        log_task_completed("走路赚元宝", task_name)
    except:
        pass
    return True


def main():
    if sys.stdout.encoding == 'gbk':
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 50)
    print(" 🚶 走路赚元宝")
    print("   优先级: 福利直播 > 看直播 > 猜价格 > 浏览")
    print(" 🟢 白名单自读模式已启用")
    print(f"   白名单: {' | '.join(WHITELIST)}")
    print("=" * 50)

    if not navigate_to_walk_page(d):
        print("⚠️ 导航未确认到达走路页，停止")
        return
    if not prepare_walk_steps():
        print("⚠️ 步数上传/转换未确认完成，停止")
        return
    if not open_walk_task_list():
        print("⚠️ 未确认打开赚步数任务列表，停止")
        return

    total_all = 0
    scroll_count = 0
    ocr_empty_retries = 0
    first_scan = True
    attempts = {}

    while True:
        # 每次扫描前确保 u2 调试覆盖层已关闭
        try:
            d.settings["uiautomator"] = {}
            d.settings["operation_delay"] = 0.5
        except Exception:
            pass
        try:
            d.settings.pop("debug", None)
        except Exception:
            pass

        # 首次 OCR 前先等待页面稳定
        if first_scan:
            first_scan = False
            time.sleep(2)

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
            return

        ocr_empty_retries = 0
        if not is_walk_task_list(blocks):
            if not restore_walk_task_list(blocks):
                print("  ⚠️ 无法恢复走路任务列表，停止")
                return
            continue

        task_list = [task for task in parse_tasks_from_blocks(blocks)
                     if attempts.get(task[2], 0) < MAX_TASK_ATTEMPTS]
        if not task_list:
            # 诊断：打印前 20 个 OCR 文本块
            print(f"  🐛 OCR 返回 {len(blocks)} 个文本块，但未匹配到任务按钮")
            print(f"  🐛 前 20 个文本块:")
            for idx, b in enumerate(blocks[:20]):
                print(f"    [{idx}] \"{b['text']}\" @({b['cx']},{b['cy']}) conf={b.get('conf',0):.2f}")

            # 下滑找更多任务
            scroll_count += 1
            if scroll_count > MAX_SCROLL:
                print(f"  已下滑 {MAX_SCROLL} 次，没有其他可执行任务，结束")
                break

            print(f"  ⬇️ 下滑 ({scroll_count}/{MAX_SCROLL})...")
            d.swipe(screen_width // 2, int(screen_height * 0.7),
                    screen_width // 2, int(screen_height * 0.3))
            time.sleep(2)
            continue

        # 有任务 → 重置计数
        scroll_count = 0
        ocr_empty_retries = 0

        # 只执行第一个（优先级最高的）
        priority, task_type, task_name, btn_cx, btn_cy = task_list[0]
        attempts[task_name] = attempts.get(task_name, 0) + 1
        report_task_found()
        print(f"\n  ▶️ [{total_all + 1}] {task_name} (优先级{priority})")
        if not execute_task(task_type, task_name, btn_cx, btn_cy):
            continue
        total_all += 1
        print(f"  已执行: {total_all} 个任务")

    print(f"\n📊 共执行 {total_all} 个任务")
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
