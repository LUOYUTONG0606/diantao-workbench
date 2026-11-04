"""双十一淘金币：淘宝首页 -> 领淘金币 -> 去赚淘金币，使用 OCR 识别。"""

import argparse
import re
import sys
import time
import unicodedata

from task_exclusions import is_blocked_boutique_task
from u2_scanner import (
    execute_browse_task, execute_live_task, execute_video_task,
    is_blacklisted, scan_page,
)
from utils import TB_APP, _cloud_ocr, connect_device, ensure_local_ocr
from utils import pause_global_watcher


d = None
screen_width, screen_height = 0, 0
ENTRY_TEXTS = ("去赚淘金币", "去赚金币")
ACTION_TEXTS = ("去完成", "去浏览", "去观看", "去逛逛", "逛一逛", "去领取", "立即领取")
CLAIM_TEXTS = ("去领取", "立即领取", "领取")
# 去赚金币列表顶部「每日任务」区的按钮/标签文案。当前脚本只认 ACTION_TEXTS /
# CLAIM_TEXTS 与「更多专享福利」上方的 +N，会漏掉这批（签到 / 收金币 / 去领 等）。
# 这些标签本身往往就是可点区域，没有独立的右侧按钮。
DAILY_LABEL_TEXTS = (
    "签到", "立即签到", "去签到", "天天签到", "连续签到",
    "收金币", "去收金币", "领金币", "去领金币", "天天领金币",
    "逛会场", "去逛会场", "看视频", "天天领",
    "做任务", "去参与", "去使用", "去兑换", "去领", "立即领", "领",
)
MAX_ATTEMPTS = 2
EMPTY_LIMIT = 12

# 会跳出淘宝的任务：一律不做（含支付宝系、其他 App、以及站内的下单/授权类）。
# 使用者明确要求：打开别的应用的任务不要做。
EXTERNAL_APP_WORDS = (
    "支付宝", "蚂蚁森林", "蚂蚁庄园", "蚂蚁新村", "蚂蚁", "金豆", "芝麻信用",
    "微博", "闲鱼", "快手", "抖音", "一淘", "淘宝特价版", "特价版", "天猫", "百度",
    "饿了么", "美团", "拼多多", "京东", "唯品会", "高德", "小红书", "b站",
    "下App", "下载App", "安装App", "打开App",
)

# 标题里看不出目标 App、但实测会跳转的任务，按全名精确屏蔽
EXTERNAL_APP_TITLES = (
    "逛一逛领购物红包",   # 天猫
)

# 「搜一搜」类任务要先在搜索框输入关键词再浏览，否则不算完成。
# 轮换使用，避免每次搜同一个词太像脚本。
SEARCH_TERMS = ("固态硬盘", "移动硬盘", "蓝牙耳机", "牙刷", "纸巾", "牛肉干")
_search_index = {"i": 0}


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


def progress(text):
    match = re.search(r"(\d+)\s*/\s*(\d+)", normalize(text))
    return tuple(map(int, match.groups())) if match else None


def task_key(task):
    return re.sub(r"\(\d+/\d+\)", "", normalize(task["name"]))


def match_task_type(task_name):
    """未知任务不默认执行，保留商品三次点击任务的统一屏蔽规则。"""
    if is_blocked_boutique_task(task_name) or is_blacklisted(task_name):
        return "skip"
    # 会打开其他 App 的任务：直接跳过，不点
    if any(word in task_name for word in EXTERNAL_APP_WORDS):
        return "skip"
    if task_name.strip() in EXTERNAL_APP_TITLES:
        return "skip"
    if any(word in task_name for word in (
        "答题", "课堂", "考试", "下单", "购买", "支付", "开通", "授权",
        "邀请", "分享", "订阅", "去关注", "关注店铺", "关注主播", "关注领",
        "蚂蚁", "支付宝", "抽奖", "猜价格",
    )):
        return "skip"
    if "直播" in task_name:
        return "live"
    if "视频" in task_name:
        return "video"
    if "任务到访" in task_name:
        return "visit"
    # 每日任务区的「签到 / 收金币 / 领金币 / 去领 / 立即领」：点一下直接到账，无需浏览。
    if any(word in task_name for word in ("收金币", "领金币", "去领", "立即领", "领红包")):
        return "claim"
    if "签到" in task_name:
        return "claim"
    if any(word in task_name for word in ("浏览", "逛", "精选好物", "商品都可抵",
                                          "沉浸看", "随心逛", "看看", "参与", "做任务",
                                          "兑换", "使用")):
        return "browse"
    # 「搜一搜」类：需要先搜索关键词
    if "搜一搜" in task_name.replace("\u200b", "") or (
            task_name.startswith("搜") and "宝贝" in task_name):
        return "search"
    return "skip"


def is_task_page(nodes):
    texts = [normalize(n["text"]) for n in nodes]
    # 双11 能量任务页不归本脚本管
    if any("做任务赚能量" in text for text in texts):
        return False
    # 淘金币活动主页（升级赛车 + 去赚能量）也不是任务列表，只是入口
    if any("升级赛车拿钱" in text for text in texts) and any(text == "去赚能量" for text in texts):
        return False
    # 新版金币任务列表的稳定特征文案（都在列表顶部/底部，滚动后会移出屏幕）
    markers = ("更多专享福利", "任务到访得金币", "为你找到最简单的任务", "完成下方任务得额外金币")
    if any(
        ("今日速赚" in text and "淘金币" in text)
        or text in ("赚淘金币", "做任务赚金币", "做任务赚淘金币")
        or any(marker in text for marker in markers)
        for text in texts
    ):
        return True
    # 滚动到列表中段时上面的锚点都看不到了，改用「任务行特征」兜底：
    # 有「浏览N秒」这类时长说明 + 右侧「+N」奖励，或者有动作按钮 + 奖励
    has_duration = any(re.fullmatch(r"(?:滑动)?浏览\d+秒", text) for text in texts)
    has_reward = any(re.fullmatch(r"\+\d+", text) for text in texts)
    has_action = any(text in ("去完成", "去领取", "立即领取", "逛一逛", "去逛逛") for text in texts)
    if has_reward and (has_duration or has_action):
        return True
    return False


# ── 提速核心：同屏不重复 OCR ──────────────────────────────
# 每一步都全屏 OCR 是主要耗时来源（实测每次 1.5~3s，文字多的页面更久）。
# 页面没变化时直接复用上一次结果；所有点击/滑动/按键都会让缓存失效，
# 所以不会读到过期数据。
SCAN_CACHE_TTL = 4.0
_scan_cache = {"at": 0.0, "nodes": None}


def invalidate_scan():
    """任何会改变屏幕的操作之后都要调用，避免读到旧画面。"""
    _scan_cache["at"] = 0.0
    _scan_cache["nodes"] = None


def tap(x, y):
    invalidate_scan()
    d.click(int(x), int(y))


def press_back():
    invalidate_scan()
    d.press("back")


def swipe(x1, y1, x2, y2, duration=.3):
    invalidate_scan()
    d.swipe(int(x1), int(y1), int(x2), int(y2), duration=duration)


def click_node(node):
    cx, cy = int(node.get("cx", 0)), int(node.get("cy", 0))
    if not (0 < cx < screen_width and 0 < cy < screen_height):
        return False
    tap(cx, cy)
    time.sleep(1.1)
    return True


def click_unique(nodes, labels):
    matches = [n for n in nodes if normalize(n["text"]) in labels]
    return len(matches) == 1 and click_node(matches[0])


def read_page(force=False):
    """读当前页面文字块。同屏重复读直接命中缓存，省一次 OCR。"""
    if d.app_current().get("package") != TB_APP:
        return []
    now = time.monotonic()
    if (not force and _scan_cache["nodes"] is not None
            and now - _scan_cache["at"] <= SCAN_CACHE_TTL):
        return _scan_cache["nodes"]
    nodes = scan_page(d)
    _scan_cache["at"] = time.monotonic()
    _scan_cache["nodes"] = nodes
    return nodes


def dismiss_notice(nodes):
    return click_unique(nodes, ("我知道了", "稍后再说", "暂不", "关闭弹窗",
                                "开心收下", "收下", "开心领取"))


def home_entry(nodes):
    entries = [n for n in nodes if normalize(n["text"]) == "领淘金币"]
    if entries:
        return entries[0] if len(entries) == 1 else None
    merged = [n for n in nodes if "领淘金币" in normalize(n["text"])]
    if len(merged) != 1:
        return None
    # 首页宫格文案会被 OCR 合并成一行；分块重新识别，使用局部识别的真实坐标。
    image = d.screenshot()
    top = max(0, int(merged[0]["cy"] - screen_width * .05))
    bottom = min(screen_height, int(merged[0]["cy"] + screen_width * .05))
    step = max(1, screen_width // 6)
    for left in range(0, screen_width, step):
        right = min(screen_width, left + int(step * 1.2))
        blocks = _cloud_ocr(image.crop((left, top, right, bottom)), retries=0)
        matches = [n for n in blocks if normalize(n["text"]) == "领淘金币"]
        if len(matches) == 1:
            return dict(matches[0], cx=matches[0]["cx"] + left, cy=matches[0]["cy"] + top)
    return None


def is_home_page(nodes):
    bottom = {normalize(n["text"]) for n in nodes if n["cy"] > screen_height * .85}
    return len(bottom & {"视频", "消息", "购物车", "我的淘宝"}) >= 3 and any(
        normalize(n["text"]) == "推荐" and n["cy"] < screen_height * .15 for n in nodes)


def clone_dialog_visible():
    """判断当前是否停在 vivo 的应用分身选择框。"""
    cur = d.app_current()
    sig = (cur.get("package") or "") + "/" + (cur.get("activity") or "")
    if "doubleinstance" in sig or "DoubleAppResolver" in sig:
        return True
    try:
        return d(text="选择要使用的应用").exists
    except Exception:
        return False


def dismiss_clone_resolver():
    """vivo 的「选择要使用的应用」分身选择框会挡住淘宝启动，自动选主应用。

    手机上开了淘宝应用分身（双开），用 intent 启动时会弹这个系统框，
    里面有「淘宝」和「Ⅱ·淘宝」两个选项，不选就一直停在 launcher。

    注意：那个「淘宝」TextView 本身 clickable=false，对它调 .click() 无效，
    必须按它的 bounds 取中心坐标去点（点下去会命中外面的可点击容器）。
    """
    if not clone_dialog_visible():
        return False
    print("  检测到应用分身选择框，选择主应用【淘宝】")
    for _ in range(3):
        try:
            el = d(text="淘宝")
            if el.exists:
                b = (el.info or {}).get("bounds") or {}
                if all(k in b for k in ("left", "top", "right", "bottom")):
                    cx = (b["left"] + b["right"]) // 2
                    cy = (b["top"] + b["bottom"]) // 2
                    print(f"  点击主应用选项 ({cx},{cy})")
                    tap(cx, cy)
                    # 冷启动可能十几秒，轮询等前台切过去
                    for _ in range(12):
                        time.sleep(2)
                        if d.app_current().get("package") == TB_APP:
                            time.sleep(2)
                            return True
                        if clone_dialog_visible():
                            break
        except Exception:
            pass
        time.sleep(2)
        if not clone_dialog_visible() and d.app_current().get("package") != TB_APP:
            # 框消失了但没进淘宝，说明点空了，重来一次
            continue
    return d.app_current().get("package") == TB_APP


def ensure_taobao(timeout=45):
    """（当前流程已不调用）主动拉起淘宝，仅作备用。

    按使用者的要求，现在由人自己打开淘宝首页后再启动脚本，脚本不主动启动/重启淘宝
    ——那会弹应用分身选择框，还会把已打开的页面顶掉。
    实测这台 vivo：`d.app_start`(monkey) 无效，`am start` 有效但会弹分身框。
    """
    if dismiss_clone_resolver():
        pass
    if d.app_current().get("package") == TB_APP:
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        # 分身选择框挡着就先选主应用
        if clone_dialog_visible():
            dismiss_clone_resolver()
            if d.app_current().get("package") == TB_APP:
                time.sleep(2)
                return True
            continue
        # 这台机器 d.app_start(monkey) 无效，直接用 am start
        try:
            d.shell("am start -n com.taobao.taobao/com.taobao.tao.welcome.Welcome")
        except Exception:
            pass
        time.sleep(3)
        if clone_dialog_visible():
            dismiss_clone_resolver()
        if d.app_current().get("package") == TB_APP:
            time.sleep(2)
            return True

    dismiss_clone_resolver()
    ok = d.app_current().get("package") == TB_APP
    if not ok:
        print(f"  拉起淘宝失败（前台 {d.app_current().get('package')}）")
    else:
        time.sleep(2)
    return ok


def navigate_to_task_list():
    """从当前所处层级逐步走到金币任务列表。

    淘宝重启后不一定回到首页（会恢复上次停留的页面，常是淘金币活动主页），
    所以不能假设「首页 → 领淘金币」这一条固定路径。这里按屏幕上实际有的入口走：
      已在任务列表 → 直接返回
      有「去赚金币」→ 点（活动主页）
      有「领淘金币」→ 点（首页宫格）
      都没有 → 返回键退一层，最多 3 次
    """
    # 不重启淘宝，直接从当前页面往下走
    if d.app_current().get("package") != TB_APP:
        print("  不在淘宝前台，停止（请先在手机上打开淘宝首页）")
        return False
    time.sleep(1)

    backs = 0
    entry_clicks = 0
    for _ in range(10):
        # 不重启淘宝：退出去了就停止，由人手动再打开
        if d.app_current().get("package") != TB_APP:
            print("  已离开淘宝，停止（请重新打开淘宝首页后再启动）")
            return False
        nodes = read_page()
        if not nodes:
            time.sleep(1.2)
            continue
        if dismiss_notice(nodes):
            continue
        if is_task_page(nodes):
            print("  已进入淘金币任务列表")
            return True
        if entry_clicks < 6 and click_unique(nodes, ("去赚金币", "去赚淘金币")):
            entry_clicks += 1
            print(f"  点击【去赚金币】（第 {entry_clicks} 次）")
            # 这个列表页停留时间很短（实测十几秒后会自己退回活动主页），
            # 不能固定 sleep 后再读，要高频轮询，一旦识别到马上返回。
            for _ in range(6):
                time.sleep(1.2)
                probe = read_page(force=True)
                if is_task_page(probe):
                    print("  已进入淘金币任务列表")
                    return True
            continue
        entry = home_entry(nodes)
        if entry and click_node(entry):
            print("  点击首页【领淘金币】")
            time.sleep(2.0)
            continue
        if backs < 2:
            backs += 1
            press_back()
            time.sleep(2)
            continue
        break

    for _ in range(3):
        if is_task_page(read_page()):
            print("  已进入淘金币任务列表")
            return True
        time.sleep(2)
    print("  未能进入金币任务列表（当前页面没有可用的金币入口）")
    return False


def parse_task_list(nodes, include_skipped=False):
    """匹配金币图标或文字按钮与所在任务行，拒绝纯数字及未定位标题的按钮。"""
    if not is_task_page(nodes):
        return []
    nodes = [dict(n, text=normalize(n["text"])) for n in nodes]
    split = next((n["cy"] for n in nodes if n["text"] == "更多专享福利"), screen_height)
    buttons = [n for n in nodes if (
        (n["cx"] > screen_width * .7
         and (n["text"] in ACTION_TEXTS + CLAIM_TEXTS
              or (n["cy"] < split and re.fullmatch(r"\+\d+", n["text"]))))
        # 顶部「每日任务」区的标签本身就是可点区域（签到 / 收金币 / 去领 等），
        # 未必有独立右侧按钮；另外任意位置的 +N 奖励图标也计入。
        or n["text"] in DAILY_LABEL_TEXTS
        or re.fullmatch(r"\+\d+", n["text"])
    )]
    results = []
    seen = set()
    for button in buttons:
        # 标题位于奖励图标左上方；下方的“浏览15秒”等小字只作为任务说明。
        titles = [
            n for n in nodes
            if screen_width * .15 < n["cx"] < screen_width * .75
            and -.008 * screen_height <= button["cy"] - n["cy"] <= .045 * screen_height
            and len(n["text"]) >= 2
            and n["text"] not in ACTION_TEXTS + CLAIM_TEXTS
            and not re.fullmatch(r"[\d+()/]+", n["text"])
            and not any(word in n["text"] for word in (
                "额外", "今日速赚", "更多专享", "更多金币", "点击去逛", "参与答题",
            ))
        ]
        titles = [n for n in titles if not re.fullmatch(r"(?:滑动)?浏览\d+秒", n["text"])]
        if not titles:
            # 每日任务标签本身即点击区域（如「签到」「收金币」可能没有独立右侧按钮），
            # 直接以标签文案作为任务名，避免整项被漏掉。
            if button["text"] in DAILY_LABEL_TEXTS:
                name = button["text"]
                title_y = button["cy"]
            else:
                continue
        else:
            titles.sort(key=lambda n: abs(n["cy"] - button["cy"]))
            if len(titles) > 1 and abs(titles[0]["cy"] - titles[1]["cy"]) < screen_height * .008:
                # 同行拆分标题必须合并，避免漏掉三商品任务的后半段。
                row = sorted([n for n in titles if abs(n["cy"] - titles[0]["cy"]) < screen_height * .008],
                             key=lambda n: n["cx"])
                name = "".join(n["text"] for n in row)
            else:
                name = titles[0]["text"]
            title_y = titles[0]["cy"]
        details = " ".join(n["text"] for n in nodes
                           if n["cx"] < screen_width * .75
                           and 0 <= n["cy"] - title_y <= screen_height * .03)
        task_progress = progress(details)
        kind = match_task_type(name)
        if task_progress and task_progress[0] >= task_progress[1]:
            kind = "skip"
        if kind != "skip" and button["text"] in CLAIM_TEXTS:
            kind = "claim"
        duration = re.search(r"(\d+)(秒|分钟)", details)
        seconds = int(duration[1]) * (60 if duration[2] == "分钟" else 1) if duration else 60
        task = dict(name=name, type=kind, cx=button["cx"], cy=button["cy"],
                    progress=task_progress, seconds=seconds, click_text=button["text"])
        key = task_key(task)
        if key not in seen and (include_skipped or kind != "skip"):
            seen.add(key)
            results.append(task)
    return results


def exec_task(task_def):
    """执行器保留独立屏蔽门禁，调用者不得绕过解析器的限制。"""
    kind, name = task_def["type"], task_def["name"]
    if kind == "click_three" or is_blocked_boutique_task(name):
        return False
    if kind not in ("browse", "live", "video", "claim", "visit", "search") or match_task_type(name) == "skip":
        return False
    # 重新识别并定位，避免点击上一轮扫描留下的过期坐标。
    fresh = [task for task in parse_task_list(read_page())
             if task_key(task) == task_key(task_def)
             and task["type"] == kind and task["progress"] == task_def.get("progress")]
    if len(fresh) != 1 or not click_node(fresh[0]):
        return False
    # ⛔ 点完立刻检查：一旦跳到别的 App 就马上退回淘宝，这一项不计入
    # （使用者要求：打开别的应用的任务不做）
    # 原本固定等 3×1s；click_node 已等 1.1s，跳转通常早就发生，故第一次直接查。
    for _ in range(2):
        current = d.app_current().get("package")
        if current == TB_APP:
            break
        print(f"  ⛔ 已跳到其他应用（{current}），立即返回淘宝，跳过该项")
        try:
            press_back()
        except Exception:
            pass
        time.sleep(1)
    if d.app_current().get("package") != TB_APP:
        try:
            d.shell("am start -n com.taobao.taobao/com.taobao.tao.welcome.Welcome")
            time.sleep(3)
        except Exception:
            pass
        return False

    if kind in ("claim", "visit"):
        return True
    # 外部跳转不滑动；返回和到账检查统一由主循环负责。
    landed = read_page()
    if not landed or is_task_page(landed):
        return False
    seconds = fresh[0]["seconds"]
    if kind == "search":
        # 搜一搜：先真的搜一次，直接在搜索结果页浏览，不点开具体商品。
        if not do_search():
            return False
        print("  搜索完成，在搜索结果页浏览")
        result = execute_browse_task(d, min_duration=seconds,
                                     max_wait=30)
    elif kind == "live":
        result = execute_live_task(d, name, fresh[0]["cx"], fresh[0]["cy"],
                                   duration=seconds + 5)
    elif kind == "video":
        result = execute_video_task(d, name, fresh[0]["cx"], fresh[0]["cy"],
                                    duration=seconds, verify_media_page=True)
    else:
        result = execute_browse_task(d, min_duration=seconds,
                                     max_wait=30)
    # 浏览/观看过程中是 u2_scanner 内部在滑动屏幕，不会经过本文件的封装，
    # 所以这里必须手动作废扫描缓存，否则下一步会读到浏览前的旧画面。
    invalidate_scan()
    return result


def do_search():
    """在搜索页输入一个预设关键词并提交。返回是否成功。

    为什么必须搜：这类「搜一搜你心仪的宝贝」任务要求真的发生一次搜索行为，
    只停留在页面上滑动不算完成，金币不会到账。
    """
    field = None
    for _ in range(6):   # 搜索页有时加载慢，等输入框出现
        try:
            candidate = d(className="android.widget.EditText")
            if candidate.exists and candidate.count == 1:
                field = candidate
                break
        except Exception:
            pass
        time.sleep(1.0)

    if field is None:
        print("  未定位到唯一的搜索输入框，本项跳过")
        return False

    keyword = SEARCH_TERMS[_search_index["i"] % len(SEARCH_TERMS)]
    _search_index["i"] += 1
    print(f"  搜索关键词：{keyword}")
    try:
        invalidate_scan()
        field.click()
        time.sleep(0.6)
        d.send_keys(keyword, clear=True)
        time.sleep(0.6)
        d.press("enter")
        invalidate_scan()
        time.sleep(2)
        return True
    except Exception as exc:
        print(f"  搜索输入失败：{exc}")
        return False


def back_to_task_page():
    """回到金币任务列表。不重启淘宝：退到活动主页就重进，离开淘宝就停止。

    返回键不再固定按 2 次：走了「搜索 → 商品详情」的任务需要连退多一层
    （详情 → 结果页 → 搜索落地页 → 任务列表）。真正导致退出桌面的是
    在淘宝主界面（TBMainActivity）上继续按返回，所以在那一层直接停手。
    """
    backs = 0
    for _ in range(7):
        current = d.app_current()
        if current.get("package") != TB_APP:
            print("  已离开淘宝，停止")
            return False
        nodes = read_page()
        if is_task_page(nodes):
            return True
        # 退到了淘金币活动主页：直接重新进列表，别继续按返回（会退回淘宝首页）
        reentry = [n for n in nodes
                   if normalize(n["text"]) in ("去赚金币", "去赚淘金币")]
        if len(reentry) == 1:
            print("  回到活动主页，重新点击【去赚金币】")
            if click_node(reentry[0]):
                time.sleep(2)
                continue
        # ⛔ 淘宝主界面再按返回就是退出 App，这一层必须停手
        if current.get("activity", "").endswith("TBMainActivity"):
            print("  已到淘宝主界面，不再按返回（避免退出 App）")
            return False
        if backs < 4:
            backs += 1
            press_back()
            time.sleep(1.2)
        else:
            print("  返回次数已达上限，停止本轮")
            return False
    print("  未能返回淘金币任务列表，停止本轮执行")
    return False


def verify_task(before):
    """仅有进度增长或同一任务行的明确完成状态才计入已确认。

    这里强制重新 OCR：到账后进度/已完成标记是页面异步刷新的，
    用缓存会读到任务完成前的旧画面，把做成的任务记成失败。
    """
    for _ in range(2):
        nodes = read_page(force=True)
        if not is_task_page(nodes):
            return False
        matches = [task for task in parse_task_list(nodes, include_skipped=True)
                   if task_key(task) == task_key(before)]
        if len(matches) == 1:
            after = matches[0]["progress"]
            if before.get("progress") and after and after[0] > before["progress"][0]:
                return True
        title = [n for n in nodes
                 if re.sub(r"\(\d+/\d+\)", "", normalize(n["text"])) == task_key(before)]
        if len(title) == 1:
            # 完成后金币入口会消失，但标题仍显示 (2/2)，不能依赖可点击行解析。
            after = progress(title[0]["text"])
            if before.get("progress") and after and after[0] > before["progress"][0]:
                return True
            if any(normalize(n["text"]) in ("已完成", "已领取")
                   and n["cx"] > screen_width * .7
                   and abs(n["cy"] - title[0]["cy"]) < screen_height * .045 for n in nodes):
                return True
        time.sleep(1.2)
    return False


def scroll_list():
    swipe(screen_width // 2, int(screen_height * .75),
          screen_width // 2, int(screen_height * .48), duration=.4)
    time.sleep(1.2)


def scroll_up_to_list(tries=3):
    """往上滚回任务区。金币页下面接着商品流，滑下去后任务行会离开视口。"""
    for _ in range(tries):
        nodes = read_page()
        if is_task_page(nodes):
            return nodes
        swipe(screen_width // 2, int(screen_height * .35),
              screen_width // 2, int(screen_height * .8), duration=.3)
        time.sleep(1.2)
    return read_page()


def today_coins(nodes):
    """读页面上「今日速赚 N 淘金币」的 N，用作真实到账依据。

    为什么不用任务行进度（0/3 → 1/3）判断：列表进度经常不实时刷新，
    实测任务确实完成后金币也涨了，但行内进度还是旧值，导致误判为未完成。
    顶部这个「今日速赚」累计值反而会跟着变。
    """
    for n in nodes:
        match = re.search(r"今日速赚(\d+)淘金币", normalize(n["text"]))
        if match:
            return int(match.group(1))
    return None


def claim_accumulated_reward(nodes):
    """领取页面上的「今日累计奖励」（做任务累计进度满后可领的额外金币）。

    只在按钮文案为领取类（领取 / 立即领取 / 去领取）时才点；
    进度未满时按钮通常是「去完成 / 做任务」，不会误点跳转。
    标题含「累计」即视为累计奖励模块（覆盖「今日累计奖励」「累计奖励」）。
    """
    titles = [n for n in nodes if "累计" in normalize(n["text"])]
    if not titles:
        return False
    for t in titles:
        # 同行右侧、纵向对齐的领取按钮
        btns = [n for n in nodes
                if normalize(n["text"]) in CLAIM_TEXTS
                and n["cx"] > t["cx"]
                and abs(n["cy"] - t["cy"]) < screen_height * .06]
        if btns and click_node(btns[0]):
            print("  领取【今日累计奖励】")
            time.sleep(1.2)
            invalidate_scan()
            try:
                dismiss_notice(read_page(force=True))
            except Exception:
                pass
            return True
    return False


def run_tasks(max_tasks=30):
    attempts, finished = {}, set()
    confirmed_progress = {}
    total, confirmed, empty_times = 0, 0, 0
    expanded = False
    expand_clicks = 0
    start_coins = None
    last_coins = None
    while total < max_tasks:
        nodes = read_page()
        if not is_task_page(nodes):
            # 这里的金币任务列表会自己退回淘金币活动主页（实测约 4 秒后），
            # 所以不能只是等待：发现退回就重新点一次「去赚金币」再进。
            recovered = False
            for attempt in range(3):
                time.sleep(1.2)
                nodes = read_page(force=True)   # 这里就是「等页面自己变化」，必须重扫
                if is_task_page(nodes):
                    recovered = True
                    break
                try:
                    dismiss_notice(nodes)
                except Exception:
                    pass
                # 先往上滚一下：滑进商品流时任务行会离开视口，误判成「不在列表」
                swipe(screen_width // 2, int(screen_height * .35),
                      screen_width // 2, int(screen_height * .8), duration=.3)
                time.sleep(1)
                nodes = read_page(force=True)
                if is_task_page(nodes):
                    recovered = True
                    break
                reentry = [n for n in nodes
                           if normalize(n["text"]) in ("去赚金币", "去赚淘金币")]
                if len(reentry) == 1:
                    print("  页面已退回活动主页，重新点击【去赚金币】再进")
                    if click_node(reentry[0]):
                        time.sleep(1.5)
                        nodes = read_page(force=True)
                        if is_task_page(nodes):
                            recovered = True
                            break
                else:
                    print(f"  第 {attempt + 1} 次复核仍不是任务列表"
                          f"（可见 {len(nodes)} 块，前台 {d.app_current().get('package')}）")
            if not recovered:
                print("  当前页面无法确认为金币任务列表，停止")
                break
        # 每轮先尝试领取「今日累计奖励」（进度满后的额外金币）。
        # 仅当按钮为领取类文案时才点，进度未满的「去完成/做任务」不会误点。
        if claim_accumulated_reward(nodes):
            invalidate_scan()
            time.sleep(1.2)
            continue
        # ⛔ 不在迭代开头就点「展开」：那样会把列表往下滚，把顶部「每日任务」
        # 整批滚出屏幕、永远做不掉。改成「当前屏幕已无可执行任务」时才展开/下滚，
        # 保证顶部每日任务优先做掉。
        tasks = [t for t in parse_task_list(nodes)
                 if task_key(t) not in finished
                 and attempts.get(task_key(t), 0) < MAX_ATTEMPTS
                 and (not t["progress"] or t["progress"][0] >= confirmed_progress.get(task_key(t), 0))]
        print(f"  [扫描] 可见 {len(nodes)} 块文字，可执行 {len(tasks)} 个任务")
        if not tasks:
            empty_times += 1
            if empty_times >= EMPTY_LIMIT:
                break
            if not expanded and any(normalize(n["text"]) == "更多专享福利" for n in nodes):
                expand = [n for n in nodes if normalize(n["text"]) == "展开"
                          and n["cy"] > screen_height * .5]
                if len(expand) == 1 and click_node(expand[0]):
                    expanded = True
                    empty_times = 0
                    print("  点击【展开】，显示更多任务")
                    time.sleep(1.5)
                    for _ in range(4):
                        scroll_list()
                    continue
            scroll_list()
            continue
        empty_times = 0
        task = tasks[0]
        key = task_key(task)
        attempts[key] = attempts.get(key, 0) + 1
        total += 1
        before_coins = today_coins(nodes)
        if start_coins is None:
            start_coins = before_coins
        print(f"  [{total}] {task['name']} ({task['type']}, {task['seconds']}秒)")
        try:
            exec_task(task)
        except Exception as exc:
            print(f"  任务执行异常：{exc}")
        if not back_to_task_page():
            break

        # 优先用「今日速赚」的增量确认到账，其次才看任务行进度
        after_nodes = read_page()
        after_coins = today_coins(after_nodes)
        gained = (before_coins is not None and after_coins is not None
                  and after_coins > before_coins)
        last_coins = after_coins if after_coins is not None else last_coins

        if gained:
            confirmed += 1
            prog = task["progress"]
            if prog and prog[1] > 1 and prog[0] + 1 < prog[1]:
                # 多轮任务（如 0/3），还要继续做几轮
                attempts[key] = 0
            else:
                finished.add(key)
            print(f"  到账确认：今日速赚 {before_coins} → {after_coins}"
                  f"（+{after_coins - before_coins}）")
        elif verify_task(task):
            confirmed += 1
            if task["progress"]:
                confirmed_progress[key] = task["progress"][0] + 1
            if not task["progress"] or task["progress"][0] + 1 >= task["progress"][1]:
                finished.add(key)
            else:
                attempts[key] = 0
        else:
            print("  未确认进度或到账；同项最多尝试两次")
    print(f"  共尝试 {total} 次，确认完成/进度增长 {confirmed} 次")
    if start_coins is not None and last_coins is not None:
        print(f"  今日速赚：{start_coins} → {last_coins}"
              f"（本次 +{last_coins - start_coins}）")
    return dict(attempted=total, confirmed=confirmed)


def bootstrap():
    """初始化：亮屏 + 确认淘宝在前台。**不启动、不重启淘宝**。

    按使用者要求：由人自己打开淘宝首页后再点启动，脚本只从当前页面往下走。
    主动拉起淘宝会弹应用分身选择框，还会把已打开的页面顶掉，反而更不稳。
    """
    try:
        if not d.info.get("screenOn", True):
            d.screen_on()
            time.sleep(1)
    except Exception:
        pass

    # 刚连上 u2 时 app_current() 偶尔读到旧值，重试几次再判定
    for _ in range(5):
        try:
            if d.app_current().get("package") == TB_APP:
                dismiss_clone_resolver()
                return True
        except Exception:
            pass
        time.sleep(1.5)

    try:
        current = d.app_current().get("package")
    except Exception:
        current = "未知"
    print(f"  当前不在淘宝前台（{current}）。请先在手机上打开淘宝首页，再点启动。")
    return False


def main(argv=None):
    global d, screen_width, screen_height
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="run",
                        choices=("run", "enter", "scan", "here"))
    parser.add_argument("--max-tasks", type=int, default=50)
    args = parser.parse_args(argv)
    if args.max_tasks <= 0:
        parser.error("--max-tasks 必须大于 0")
    ensure_local_ocr()
    d = connect_device()
    screen_width, screen_height = d.window_size()

    # ⛔ 必须停掉后台弹窗监控。
    # connect_device() 会启动 watch_context，它注册了「刷新 / 点击刷新 / 关闭」的自动点击；
    # 金币任务列表页上正好有这些元素，后台线程会反复点它，导致刚进去就被刷回活动主页
    # （现象：已进入任务列表 → 十几秒后自动退回）。本任务全程不用它。
    pause_global_watcher(d)
    print("  （已停用后台弹窗监控，避免它刷新掉金币列表页）")

    print("  淘金币任务：首页 -> 领淘金币 -> 去赚淘金币")

    # ---- 初始化：亮屏 + 确保淘宝已启动并到前台 ----
    # 之前这里没有显式初始化，淘宝没在前台时后续全部读空，
    # 而且这台机器上 d.app_start 无效、还会弹应用分身选择框。
    if not bootstrap():
        print("  淘宝没有启动成功，停止。"
              "请确认手机已解锁、且没有停在其他应用。")
        return 1

    if args.command == "scan":
        nodes = read_page()
        print(f"  金币任务列表：{is_task_page(nodes)}")
        for task in parse_task_list(nodes, include_skipped=True):
            print(task)
        return 0 if is_task_page(nodes) else 1
    # here：不做任何导航，直接在当前屏幕上做任务。
    # 适合「页面会自己退回活动主页、自动导航不稳」的情况：
    # 由人手动点进「去赚金币」并点开「展开」，脚本只负责识别和执行。
    if args.command == "here":
        nodes = read_page()
        if not is_task_page(nodes):
            # 可能滑进了商品流，先往上滚回任务区再判断
            nodes = scroll_up_to_list()
        if not is_task_page(nodes):
            print("  当前页面不是金币任务列表，请先手动点进【去赚金币】")
            return 1
        print("  当前页任务模式：不做导航，直接执行（含自动展开）")
        run_tasks(args.max_tasks)
        return 0
    if not navigate_to_task_list():
        return 1
    if args.command == "run":
        run_tasks(args.max_tasks)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("  手动停止")
        sys.exit(130)
