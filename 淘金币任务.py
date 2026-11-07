"""双十一淘金币：淘宝首页 -> 领淘金币 -> 去赚淘金币，使用 OCR 识别。"""

import argparse
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

from task_exclusions import is_blocked_boutique_task
from u2_scanner import (
    execute_browse_task, execute_live_task, execute_video_task,
    is_blacklisted, scan_page,
    external_link_dialog, external_link_blocked_count,
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
# 单轮「从顶部扫到底部」最多扫多少屏，防止页面卡住时无限滚动。
EMPTY_LIMIT = 20
# 连续多少屏读到完全相同的文字块 → 判定已经滚到底 / 页面没动。
STILL_SCREEN_LIMIT = 3
# 连续多少轮「顶部 → 底部 → 回顶部」完整遍历都没有可执行任务才收工。
# 旧逻辑只数「连续 20 屏空」：滚到底后反复读同一片区域就会误判「任务做完了」。
SWEEP_LIMIT = 3
# 「更多专享福利」折叠区的展开按钮。实测空屏右侧文字块里就有「展开」
# （同排还有 51% / 爱X / 秋冬摩托骑行>），旧逻辑要等扫到底才点、
# 点完又立刻回顶部，刚展开出来的任务又被滚没了。
EXPAND_TEXTS = ("展开", "展开更多", "展开全部", "查看更多", "更多任务", "查看全部", "展开任务")
COLLAPSE_TEXTS = ("收起", "收拢")
# 任务行右侧出现这些文字 = 这项今天已经做完了，直接跳过做下一个，
# 避免「+N」奖励图标或每日标签仍可点时把已完成的任务重复做一遍。
DONE_ROW_TEXTS = ("已完成", "已领取", "已领完", "今日已完成", "明日再来", "明天再来")
# 折叠区可能有多层；而且页面退回活动主页再进来时折叠状态会重置，
# 本轮累计次数并不等于「当前还有几层没展开」，所以上限放宽、看到就点。
MAX_EXPAND_CLICKS = 6
EXPAND_RETRY_GAP = 6.0  # 同一位置的展开按钮 6s 内不重复点，避免点了没效果时空转
_last_expand = {"pos": None, "at": 0.0}
# 浏览/观看类任务的「无倒计时页面」兜底时长上限（秒）。使用者要求浏览最长 30s。
# 任务要求的真实时长超过此上限时不再整项跳过（带浏览的任务都做）：
# 页面自带「滑动浏览XX秒」倒计时时按真实秒数做满，出现「已得N淘金币」立即
# 收工；没有倒计时的页面才按 30s 兜底，避免被强制退出导致进度不计入。
# 注意：任务若真的跳到别的 App 会直接退回并跳过，不会在外部 App 里浏览。
BROWSE_CAP = 30
# ⛔ 绝不点会把手机带出淘宝 / 带进安装流程的按钮。
# 真机 2026-11-07 出现「提示：下个页面不受我们控制…https://z.toutiao.com」
# +【取消】【浏览器打开】；使用者明确：以后不要跳到外部链接。
CLICK_FORBIDDEN_TEXTS = (
    "浏览器打开", "用浏览器打开", "在浏览器打开", "打开链接", "打开外部链接",
    "立即打开", "继续访问", "下载App", "安装App",
)

# ── 弹窗关闭（只在「点进任务之后」生效） ──────────────────
# 真机实测：「充值大礼包 / 首充礼包」这类浮层给一个底部居中的「X」
# （1260x2800 屏上 x=633, y=2111）和顶部「跳过」。
# 使用者明确：不要什么弹窗都关 —— 只有「开始做任务、点进去之后」出现弹窗
# 才关；任务列表扫描 / 导航 / 页面恢复阶段一律不碰（见 exec_task 调用处）。
POPUP_X_TEXTS = ("X", "x", "×", "✕", "✖", "✗", "✘", "╳")
POPUP_CLOSE_TEXTS = (
    "跳过", "跳过广告", "关闭", "关闭弹窗", "我知道了", "知道了",
    "稍后再说", "暂不", "以后再说", "不再提示", "开心收下", "收下", "开心领取",
)
# ⚠️ 使用者明确要求：不要在任务列表/导航阶段「见 X 就点」。
# 实测被误点的是列表页右上角小叉 (x=1171, y=361)，那是正常控件；
# 因此只认「底部居中的 X」（真机弹窗位置），右上角/其他位置一律不碰。
MAX_POPUP_DISMISS = 2   # 点进任务后最多连关两个浮层
_popup_clicks = {"pos": None, "count": 0, "at": 0.0}

# ── 快速档：把散落的固定等待集中管理 ──────────────────
# 实测整轮慢的主因不是 OCR 单次耗时（约 1s，缩小截图并不能更快且会丢字），
# 而是「每个动作后无脑等 1~2 秒」+「返回列表时逐层按返回、每层都重扫」
# +「浏览任务白等 10 秒保险时间」。这里只保留 WebView 重绘所需的最短等待，
# 页面没就绪时仍由 read_page(force=True) 强制重扫兜底。
SETTLE_CLICK = 0.6       # 点击后（原 1.1）
SETTLE_BACK = 0.5        # 按返回后（原 1.2）
SETTLE_SCROLL = 0.55     # 滑动后（原 1.2）
SETTLE_PAGE = 0.5        # 页面切换/弹窗后（原 1.0~2.0）
# 离开浏览任务时，淘宝可能弹「放弃进度/确认离开」对话框。
# 这些词出现在对话框里表示「留在页面」（点了就不放弃进度），优先点它们。
LEAVE_STAY_TEXTS = ("继续浏览", "继续", "取消", "暂不", "再看看", "我再看看", "留在页面", "留在本页")
# 这些词表示「放弃/确认离开」（点了进度就作废），绝不能点。
LEAVE_ABANDON_TEXTS = ("放弃", "确认离开", "确定离开", "离开", "不保留", "退出浏览", "退出任务")

# ── 「任务老是自动结束」防护 ──────────────────────────────
# 旧逻辑：只要一次 d.app_current().package != com.taobao.taobao，就直接
# return False → 主循环 break → 整轮任务结束（日志「已离开淘宝，停止」）。
# 但界面切换瞬间前台包名会短时变成桌面/系统弹窗/空值，属于正常抖动；
# 浏览任务按返回也可能短暂退到桌面。现在改成：多次确认 → 自动恢复 →
# 只有连续多次恢复都失败才结束，并在结束时打印明确原因。
TAOBAO_CONFIRM = 3        # 连续 N 次读到非淘宝包名才判定「真的离开了」
RECOVER_WAIT = 45         # 等待淘宝回到前台的最长秒数（期间任务不结束）
MAX_RECOVER_FAILURES = 4  # 连续恢复失败次数上限
LEAVE_DIALOG_LIMIT = 2    # 「放弃进度」对话框最多拦几次，避免无限留在页面

# ── 不要转到别的应用去，淘宝内的小程序可以 ────────────────────
# 使用者 2026-11-07 明确：不要真的切到别的 App；但淘宝内打开的小程序 /
# H5 页面照常做。所以判定依据是「点击后的前台包名」，不是任务标题 ——
# 标题里带「UC / 闲鱼 / 天猫」也可能是淘宝内小程序，不能一刀切跳过。
# 下面这些词只用于日志提示，不构成跳过条件。
APP_HINT_WORDS = (
    "支付宝", "蚂蚁森林", "蚂蚁庄园", "蚂蚁新村", "蚂蚁", "金豆", "芝麻信用",
    "微博", "闲鱼", "快手", "抖音", "一淘", "淘宝特价版", "特价版", "天猫", "百度",
    "饿了么", "美团", "拼多多", "京东", "唯品会", "高德", "小红书", "b站",
    "下App", "下载App", "安装App", "打开App",
    "uC", "UC", "UC浏览器", "夸克", "QQ浏览器", "浏览器",
)
APP_HINT_TITLES = (
    "逛一逛领购物红包",   # 天猫
)

# 本轮已确认「点了会跳到别的 App」的任务：直接跳过、不再重试。
_external_skipped = set()

# 「点进去会跳外部链接」的任务持久记录：这次点进去发现后，下次启动直接不做，
# 不用再触发一次外链提示（使用者 2026-11-07「以后不要这样」）。
EXTERNAL_LINK_TASKS_FILE = (Path(__file__).resolve().parent
                            / ".runtime" / "external_link_tasks.txt")


def load_external_link_tasks():
    try:
        if EXTERNAL_LINK_TASKS_FILE.is_file():
            for line in EXTERNAL_LINK_TASKS_FILE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    _external_skipped.add(line.strip())
    except Exception:
        pass


def remember_external_link_task(key):
    """记下这项会跳外链的任务，本轮与以后都不再点进去。"""
    _external_skipped.add(key)
    try:
        EXTERNAL_LINK_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EXTERNAL_LINK_TASKS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(key + "\n")
    except Exception as exc:
        print(f"  ⚠️ 记录外链任务失败：{exc}")

# 仍然不做：用户点名不要的蚂蚁系 + 资金/账号风险 + 非浏览类。
# 注意「支付」不能作为子串写在这里，否则「支付宝」的浏览任务会被误杀。
HARD_SKIP_WORDS = (
    "蚂蚁庄园", "蚂蚁新村", "蚂蚁森林", "喂鸡", "果园",
    "下单", "购买", "充值", "付款", "支付订单", "立即支付", "开通", "授权",
    "邀请", "助力", "分享", "订阅", "去关注", "关注店铺", "关注主播", "关注领",
    "抽奖", "猜价格", "猜一猜", "猜谜", "答题", "课堂", "考试",
    "玩小游戏", "查看并完成", "消消乐", "斗地主", "拍立淘",
)

# 实测点进去就是淘宝外部链接引导页的任务（真机 2026-11-07：空白 WebView
# 反复弹「下个页面不受我们控制…https://z.toutiao.com」+ 浏览器打开）。
# 使用者明确「以后不要跳到外部链接」，所以这类任务直接不做，连点都不点。
# 运行时新发现的外链任务会追加到 .runtime/external_link_tasks.txt，
# 下次启动由 load_external_link_tasks() 自动跳过。
KNOWN_EXTERNAL_LINK_TASKS = (
    "头条极速版刷视频",
)

# 用户明确要求做的「趣味课堂」类答题每日任务：进入后随机选一个答案（见 quiz 处理）。
# 只放行以下明确点名的任务，不波及同类的其他屏蔽项。
QUIZ_TASKS = (
    "淘金币趣味课堂",   # 名字含「课堂」但属于每日任务，需答题
)

# 「搜一搜」类任务要先在搜索框输入关键词再浏览，否则不算完成。
# 轮换使用，避免每次搜同一个词太像脚本。
SEARCH_TERMS = ("固态硬盘", "移动硬盘", "蓝牙耳机", "牙刷", "纸巾", "牛肉干")
_search_index = {"i": 0}

# 输入关键词后必须点「搜索 / 确认」按钮才会跳到搜索内容页；这个 WebView
# 页面上只按回车不生效（使用者 2026-11-07 反馈「不会点击确认跳转搜索的内容」）。
SEARCH_SUBMIT_TEXTS = (
    "确认", "确定", "去搜索", "立即搜索", "搜索一下", "搜一下", "搜索宝贝",
    "搜索", "查找",
)
# 「搜索有福利」是搜索输入页的活动横幅；用它 + 输入框提示定位输入区。
SEARCH_INPUT_HINTS = ("搜索有福利", "请输入", "想找的商品", "搜一搜", "搜索宝贝")
# 已跳到搜索结果页的特征（要求至少 2 个，避免输入页的推荐词误判）。
SEARCH_RESULT_TEXTS = ("综合", "销量", "筛选", "店铺", "价格", "搜索结果", "找到")



def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


def progress(text):
    match = re.search(r"(\d+)\s*/\s*(\d+)", normalize(text))
    return tuple(map(int, match.groups())) if match else None


def task_key(task):
    return re.sub(r"\(\d+/\d+\)", "", normalize(task["name"]))


def match_task_type(task_name):
    """带浏览的任务一律尝试；只跳过明确点名/有风险的任务。

    是否跳出淘宝不按标题判断（标题像外部 App 也可能是淘宝内小程序），
    由 exec_task 在点击后按前台包名决定：真跳出去就退回并跳过该项。
    """
    # 趣味课堂需要答题：单独归为 quiz，进入后随机选一个答案。
    if any(kw in task_name for kw in QUIZ_TASKS):
        return "quiz"
    if is_blocked_boutique_task(task_name):
        return "skip"
    # 已确认会跳到淘宝外部链接的任务：直接不做（连弹窗都不触发）。
    if any(k in normalize(task_name) for k in KNOWN_EXTERNAL_LINK_TASKS):
        return "skip"
    # 用户点名不要的蚂蚁系、资金/账号风险、非浏览类：不做。
    if any(word in task_name for word in HARD_SKIP_WORDS):
        return "skip"
    # ⛔ 不按任务标题里的 App 名拦截：使用者要求「淘宝内的小程序就可以」。
    #    真正跳到别的 App 时，由 exec_task 按前台包名立刻退回并跳过该项。
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
    # 「搜一搜」类：需要先搜索关键词。任务可能被改名成「搜索有福利」等，
    # 标题里带「搜索」的一律按搜索任务处理，别因分类不明直接跳过。
    _name = task_name.replace("\u200b", "")
    if "搜一搜" in _name or "搜索" in _name or (
            _name.startswith("搜") and "宝贝" in _name):
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
    label = normalize(node.get("text", ""))
    # 会跳到外部链接 / 下载别的 App 的按钮一律不点（使用者要求）。
    # scan_page 里另有「外链提示自动点取消」的兜底，双保险。
    if any(w in label for w in CLICK_FORBIDDEN_TEXTS):
        print(f"  ⛔ 拒绝点击【{label}】：会跳到外部链接或安装应用")
        return False
    cx, cy = int(node.get("cx", 0)), int(node.get("cy", 0))
    if not (0 < cx < screen_width and 0 < cy < screen_height):
        return False
    tap(cx, cy)
    time.sleep(SETTLE_CLICK)
    return True


def click_unique(nodes, labels):
    matches = [n for n in nodes if normalize(n["text"]) in labels]
    return len(matches) == 1 and click_node(matches[0])


def _pkg_once():
    """单次读取前台包名；异常或空值返回 None（表示「未知」，不据此判定离开）。"""
    try:
        info = d.app_current()
        if isinstance(info, dict):
            return info.get("package") or None
    except Exception:
        pass
    return None


def taobao_on_top(confirm=1, interval=0.8):
    """淘宝是否在前台。

    app_current() 在 Activity 切换的 1~2 秒窗口里会返回桌面 / 系统弹窗 /
    空 dict，这是正常抖动，不能当成「已离开淘宝」。所以：
      - 读到空值一律按「仍在前台」处理；
      - confirm>1 时要求连续多次都读到别的包名，才判定真的离开。
    """
    for i in range(confirm):
        pkg = _pkg_once()
        if pkg is None or pkg == TB_APP or pkg.startswith(TB_APP + ":"):
            return True
        if i < confirm - 1:
            time.sleep(interval)
    return False


def recover_taobao_front(timeout=RECOVER_WAIT):
    """真不在淘宝前台时的恢复流程：先等自动恢复，再温和拉回，全程不结束任务。

    优先「等一下 + 多次确认」，因为多数情况只是界面切换抖动；确实离开时
    （误退桌面 / 系统弹窗 / 外部 App）先按一次返回，再调用已有的
    ensure_taobao()（内含应用分身选择框处理）把淘宝带回来。
    """
    if taobao_on_top(confirm=TAOBAO_CONFIRM):
        return True
    pkg = _pkg_once()
    print(f"  ⚠ 前台不是淘宝（{pkg}），等待恢复（最多 {timeout}s，任务不会结束）…")
    deadline = time.monotonic() + timeout
    tried_back = False
    tried_launch = False
    while time.monotonic() < deadline:
        if taobao_on_top(confirm=2, interval=0.5):
            print("  ✅ 淘宝已回到前台，继续执行")
            invalidate_scan()
            return True
        # 跳到外部 App 时，先按一次返回，通常能直接退回淘宝
        if not tried_back:
            tried_back = True
            try:
                press_back()
            except Exception:
                pass
            time.sleep(SETTLE_BACK)
            if taobao_on_top(confirm=2, interval=0.5):
                print("  ✅ 已退回淘宝，继续执行")
                invalidate_scan()
                return True
        # 等了几秒还没回来，才尝试把淘宝带回前台（会处理分身选择框）
        if not tried_launch and time.monotonic() > deadline - (timeout - 4):
            tried_launch = True
            print("  尝试把淘宝带回前台…")
            try:
                ensure_taobao(timeout=20)
            except Exception as exc:
                print(f"  带回淘宝时出错：{exc}")
        time.sleep(1.0)
    print("  ❌ 等待超时，淘宝仍未回到前台")
    return False


def read_page(force=False):
    """读当前页面文字块。同屏重复读直接命中缓存，省一次 OCR。"""
    if not taobao_on_top():
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
    """关掉「点进任务之后」出现的弹窗/浮层，优先点底部居中的「X」。

    ⚠️ 调用时机由调用方保证：只在 exec_task 点进任务后（以及领奖/答完题后）
    调用；任务列表扫描、导航、恢复阶段一律不调用，避免误关正常控件。
    只点关闭类控件，绝不点「一键领取 / 去完成」这类会跳业务页的按钮；
    「放弃进度」对话框交给 dismiss_leave_dialog 选「留在页面」，绝不碰。
    """
    if not nodes:
        return False
    # 外部链接提示已由 scan_page 统一点【取消】，这里绝不碰它的任何按钮。
    if external_link_dialog(nodes):
        return False
    if any(any(w in normalize(n["text"]) for w in LEAVE_ABANDON_TEXTS)
           for n in nodes):
        return False

    W, H = max(screen_width, 1), max(screen_height, 1)
    xs = [n for n in nodes if normalize(n["text"]) in POPUP_X_TEXTS]
    if xs:
        def ratio(n):
            return n["cx"] / W, n["cy"] / H
        # 只认弹窗底部居中的关闭 X；右上/侧边的小叉是列表页正常控件，绝不点。
        candidates = [n for n in xs
                      if .28 <= ratio(n)[0] <= .72 and ratio(n)[1] >= .55]
        if candidates:
            target = max(candidates,
                         key=lambda n: (n["cy"], -abs(n["cx"] - W // 2)))
            # 同一个位置连点 3 次还没关掉，说明不是有效关闭键，别再空转
            pos = (target["cx"] // 20, target["cy"] // 20)
            now = time.monotonic()
            if _popup_clicks["pos"] == pos and now - _popup_clicks["at"] < 8:
                _popup_clicks["count"] += 1
            else:
                _popup_clicks["pos"], _popup_clicks["count"] = pos, 1
            _popup_clicks["at"] = now
            if _popup_clicks["count"] > MAX_POPUP_DISMISS:
                return False
            if click_node(target):
                print(f"  ✖ 已点【{normalize(target['text'])}】关闭弹窗"
                      f"（x={target['cx']}, y={target['cy']}）")
                return True

    # 没有 X（或 X 不在弹窗位置）时，退回明确的文字关闭按钮
    if click_unique(nodes, POPUP_CLOSE_TEXTS):
        print("  ✖ 已关闭弹窗（跳过/关闭类按钮）")
        return True
    return False


def dismiss_leave_dialog(nodes):
    """离开浏览任务时，淘宝常弹「放弃进度/确认离开」对话框。

    只要页面上出现「放弃进度/确认离开」这类放弃按钮，就优先点「继续浏览/继续/取消」
    留在页面，绝不点放弃。返回 True 表示拦下了一次放弃对话框（已选「留在本页」）。
    用子串匹配，兼容 OCR 读出的「放弃进度」「确认要离开吗」等变体。
    """
    # 外部链接提示不是「放弃进度」框，不在这里点任何按钮（含「取消」）。
    if external_link_dialog(nodes):
        return False
    def _has(n, words):
        t = normalize(n["text"])
        return any(w in t for w in words)
    abandon = [n for n in nodes if _has(n, LEAVE_ABANDON_TEXTS)]
    if not abandon:
        return False
    # 优先点「留在页面」类按钮；若没有，点「取消」（大多数对话框里取消=不离开）。
    stay = [n for n in nodes if _has(n, LEAVE_STAY_TEXTS)]
    if stay:
        target = stay[0]
        label = normalize(target["text"])
        if click_node(target):
            print(f"  🛡 检测到放弃进度对话框，已点【{label}】留在页面，避免进度取消")
            return True
    elif click_unique(nodes, ("取消", "暂不")):
        print("  🛡 检测到放弃进度对话框，已点【取消】留在页面，避免进度取消")
        return True
    return False


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
    # 不重启淘宝，直接从当前页面往下走；短暂抖动不算离开
    if not taobao_on_top(confirm=TAOBAO_CONFIRM):
        if not recover_taobao_front():
            print("  当前不在淘宝前台，无法进入任务列表")
            return False
    time.sleep(SETTLE_PAGE)

    backs = 0
    entry_clicks = 0
    for _ in range(10):
        # 退出去了先自动恢复，只有多次恢复都失败才结束本轮导航
        if not taobao_on_top(confirm=TAOBAO_CONFIRM):
            if not recover_taobao_front():
                print("  已离开淘宝且无法恢复，停止本轮导航")
                return False
            invalidate_scan()
            continue
        nodes = read_page()
        if not nodes:
            time.sleep(SETTLE_PAGE)
            continue
        if is_task_page(nodes):
            print("  已进入淘金币任务列表")
            return True
        if entry_clicks < 6 and click_unique(nodes, ("去赚金币", "去赚淘金币")):
            entry_clicks += 1
            print(f"  点击【去赚金币】（第 {entry_clicks} 次）")
            # 这个列表页停留时间很短（实测十几秒后会自己退回活动主页），
            # 不能固定 sleep 后再读，要高频轮询，一旦识别到马上返回。
            for _ in range(8):
                time.sleep(SETTLE_PAGE)
                probe = read_page(force=True)
                if is_task_page(probe):
                    print("  已进入淘金币任务列表")
                    return True
            continue
        entry = home_entry(nodes)
        if entry and click_node(entry):
            print("  点击首页【领淘金币】")
            time.sleep(1.0)
            continue
        if backs < 2:
            backs += 1
            press_back()
            time.sleep(SETTLE_BACK)
            continue
        break

    for _ in range(3):
        if is_task_page(read_page()):
            print("  已进入淘金币任务列表")
            return True
        time.sleep(SETTLE_PAGE)
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
        # 同一行右侧已显示「已完成/已领取/明日再来」→ 这项已完成，跳过做下一个。
        # 只认短整块文字（排除长规则文案），并要求 y 坐标同排，避免误伤别的行。
        if any(n["cx"] > screen_width * .62
               and abs(n["cy"] - button["cy"]) < screen_height * .02
               and len(n["text"]) <= 12
               and (n["text"] in DONE_ROW_TEXTS
                    or (n["text"].startswith("已完成") and "/" not in n["text"]))
               for n in nodes):
            continue
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
        _name_lower = normalize(name).lower()
        app_hint = (any(word.lower() in _name_lower for word in APP_HINT_WORDS)
                    or normalize(name) in APP_HINT_TITLES)
        task = dict(name=name, type=kind, cx=button["cx"], cy=button["cy"],
                    progress=task_progress, seconds=seconds, click_text=button["text"],
                    app_hint=app_hint)
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
    if kind not in ("browse", "live", "video", "claim", "visit", "search", "quiz") or match_task_type(name) == "skip":
        return False
    # 重新识别并定位，避免点击上一轮扫描留下的过期坐标。
    fresh = [task for task in parse_task_list(read_page())
             if task_key(task) == task_key(task_def)
             and task["type"] == kind and task["progress"] == task_def.get("progress")]
    if len(fresh) != 1 or not click_node(fresh[0]):
        return False
    # 点击之后先看落在哪儿。使用者要求：不要转到别的应用去，但淘宝内的
    # 小程序 / H5 页面照常做。判定依据是前台包名，不是标题：
    #   - 仍是 com.taobao.taobao（含 :子进程 / 小程序容器）→ 正常做；
    #   - 真变成别的包名 → 立刻退回淘宝，这一项不做、也不再重试。
    # 多次确认 + 一次额外等待，过滤 Activity 切换与小程序启动时的短暂抖动。
    time.sleep(SETTLE_CLICK)
    if not taobao_on_top(confirm=2, interval=0.6):
        time.sleep(0.8)
    if not taobao_on_top(confirm=TAOBAO_CONFIRM, interval=0.5):
        current = _pkg_once()
        print(f"  ⛔ 已跳到别的应用（{current}），按要求不做这一项，立即退回淘宝")
        _external_skipped.add(task_key(task_def))
        # 系统「打开其它应用」确认框也按返回取消，绝不点允许/打开。
        for _ in range(3):
            if taobao_on_top(confirm=2, interval=0.4):
                break
            try:
                press_back()
            except Exception:
                pass
            time.sleep(SETTLE_BACK)
        if not taobao_on_top(confirm=2, interval=0.5):
            recover_taobao_front()
        invalidate_scan()
        return False
    if task_def.get("app_hint"):
        print("  ℹ 仍在淘宝内（小程序 / H5），照常做这个浏览任务")

    if kind in ("claim", "visit"):
        return True
    # ✅ 只有「点进任务之后」才处理弹窗：列表页/导航阶段一律不碰。
    # 每个任务独立重置连点计数；最多连关两个浮层（有的任务会连弹两层）。
    # 只有真的关掉才多一次 OCR，没有弹窗时不增加耗时。
    _popup_clicks.update(pos=None, count=0, at=0.0)
    landed = read_page(force=True)
    # 点进来就要跳外部链接（如 https://z.toutiao.com）：scan_page 已自动点
    # 【取消】，这一项按用户要求不做，退回列表且本轮不再重试。
    if external_link_dialog(landed):
        print(f"  ⛔ 【{name}】会跳到外部链接，按要求不做（已点【取消】）")
        remember_external_link_task(task_key(task_def))
        invalidate_scan()
        return False
    for _ in range(MAX_POPUP_DISMISS):
        if not dismiss_notice(landed):
            break
        invalidate_scan()
        time.sleep(SETTLE_PAGE)
        landed = read_page(force=True)
    # 外部跳转不滑动；返回和到账检查统一由主循环负责。
    if not landed or is_task_page(landed):
        return False
    seconds = fresh[0]["seconds"]
    if kind == "search":
        # 搜一搜：先真的搜一次，直接在搜索结果页浏览，不点开具体商品。
        if not do_search():
            return False
        print("  搜索已提交，开始浏览")
        # 带浏览的任务一律做：min_duration 仍以 30s 为上限，但页面若显示
        # 「滑动浏览XX秒」（例如 60 秒任务），execute_browse_task 会按真实
        # 倒计时等满，所以 max_wait 要给足，不能再固定 40s 把它截断。
        result = execute_browse_task(d, min_duration=min(seconds, BROWSE_CAP),
                                     max_wait=min(max(seconds + 20, 45), 100))
    elif kind == "quiz":
        # 趣味课堂类答题：进入后随机选一个答案。
        result = execute_quiz_task(d)
    elif kind == "live":
        result = execute_live_task(d, name, fresh[0]["cx"], fresh[0]["cy"],
                                   duration=seconds + 5)
    elif kind == "video":
        result = execute_video_task(d, name, fresh[0]["cx"], fresh[0]["cy"],
                                    duration=seconds, verify_media_page=True)
    else:
        # 带浏览的任务一律做：min_duration 仍以 30s 为上限，但页面若显示
        # 「滑动浏览XX秒」（例如 60 秒任务），execute_browse_task 会按真实
        # 倒计时等满，所以 max_wait 要给足，不能再固定 40s 把它截断。
        result = execute_browse_task(d, min_duration=min(seconds, BROWSE_CAP),
                                     max_wait=min(max(seconds + 20, 45), 100))
    # 浏览/观看过程中是 u2_scanner 内部在滑动屏幕，不会经过本文件的封装，
    # 所以这里必须手动作废扫描缓存，否则下一步会读到浏览前的旧画面。
    invalidate_scan()
    return result


def find_quiz_options(nodes):
    """在答题页识别可选项：优先选项标号(A-D / ①②③④)，其次判断题(对/错/是/否)。

    找不到明确选项时，退化为「屏幕中下部一组纵向并列的 2~4 个短文本块」作为选项卡片。
    """
    def _is_opt(t):
        if not t:
            return False
        if re.match(r"^[A-Da-d][.、．。)、]?\s*\S", t) or re.fullmatch(r"[A-Da-d][.、．。)、]?", t):
            return len(t) <= 40
        if re.match(r"^[①②③④⑤]\s*\S", t) or re.fullmatch(r"[①②③④⑤]", t):
            return len(t) <= 40
        return False

    opts = [n for n in nodes if _is_opt(normalize(n["text"]))]
    if len(opts) >= 2:
        return opts
    # 判断题：对/错/正确/错误/是/否，至少出现两项才视为选项
    tf = [n for n in nodes if normalize(n["text"]) in ("对", "错", "正确", "错误", "是", "否")]
    if len(tf) >= 2:
        return tf
    # 兜底：屏幕中下部、纵向并列、宽度相近的 2~4 个短文本块，当作选项卡片
    cands = [n for n in nodes
             if screen_height * .2 <= n["cy"] <= screen_height * .85
             and 2 <= len(normalize(n["text"])) <= 24]
    cands.sort(key=lambda n: n["cy"])
    groups, cur = [], []
    for n in cands:
        if cur and n["cy"] - cur[-1]["cy"] > screen_height * .12:
            groups.append(cur)
            cur = []
        cur.append(n)
    if cur:
        groups.append(cur)
    for g in groups:
        if 2 <= len(g) <= 4:
            xs = [n["cx"] for n in g]
            if max(xs) - min(xs) <= screen_width * .35:
                return g
    return []


def execute_quiz_task(d):
    """进入答题页后随机选一个答案。找不到明确选项时退化为短暂浏览再返回。"""
    landed = read_page()
    if not landed or is_task_page(landed):
        return False
    options = find_quiz_options(landed)
    if options:
        choice = random.choice(options)
        print(f"  答题：随机选「{choice['text']}」@({choice['cx']},{choice['cy']})")
        click_node(choice)
        time.sleep(SETTLE_PAGE)
        dismiss_notice(read_page())   # 答完可能弹「答对/答错」或领取窗，顺手关掉
        invalidate_scan()
        return True
    # 没识别到选项：退化浏览，避免空跑（连测时发截图可优化识别）
    print("  答题页未识别到明确选项，退化为短暂浏览")
    execute_browse_task(d, min_duration=5, max_wait=9)
    return True


def _find_search_submit(nodes):
    """从 OCR 文字块里挑出「确认 / 搜索」提交按钮。

    优先「确认」（搜索有福利浮层的确认键）与右上搜索框旁的按钮，其次键盘
    右下角的「搜索」键。精确匹配短词，绝不点「搜索发现 / 搜索有福利」横幅，
    也不碰「放弃进度」对话框里的确认。
    """
    hits = []
    for n in nodes or []:
        t = normalize(n["text"])
        if t not in SEARCH_SUBMIT_TEXTS:
            continue
        cx, cy = int(n.get("cx", 0)), int(n.get("cy", 0))
        if not (0 < cx < screen_width and 0 < cy < screen_height):
            continue
        # 「搜索」二字也可能是顶部横幅/标题，只认右侧或键盘区。
        if t == "搜索" and not (cx > screen_width * 0.6
                                or cy > screen_height * 0.6):
            continue
        if t in ("确认", "确定") and any(
                any(w in normalize(m["text"]) for w in LEAVE_ABANDON_TEXTS)
                for m in nodes or []):
            continue
        top_right = cx > screen_width * 0.6 and cy < screen_height * 0.35
        hits.append((0 if t in ("确认", "确定") else 1,
                     0 if top_right else 1, cy, n))
    if not hits:
        return None
    hits.sort(key=lambda item: item[:3])
    return hits[0][3]


def _on_search_result_page(nodes):
    """是否已跳到搜索内容页：倒计时浮层直接算，否则要凑够 2 个结果页特征。"""
    texts = [normalize(n["text"]) for n in nodes or []]
    if any("滑动浏览" in t for t in texts):
        return True
    return sum(1 for k in SEARCH_RESULT_TEXTS
               if any(k in t for t in texts)) >= 2


def do_search():
    """在搜索页输入关键词并点「确认 / 搜索」跳到结果页。返回是否继续浏览。

    为什么必须搜：这类「搜一搜你心仪的宝贝」任务要求真的发生一次搜索行为，
    只停留在页面上滑动不算完成，金币不会到账。
    ⚠️ 旧版只按 d.press("enter")，在「搜索有福利」这个 WebView 页面上
    不会跳转，日志会假装「搜索完成」却一直不计进度。现在改成 OCR 定位
    「确认 / 搜索」按钮点击，并复核是否真的进了搜索结果页。
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
        time.sleep(SETTLE_PAGE)

    # WebView 搜索页可能没有原生 EditText：OCR 找「搜索有福利 / 请输入」点出输入框。
    if field is None:
        nodes = read_page(force=True)
        hint = next((n for n in nodes
                     if any(k in normalize(n["text"]) for k in SEARCH_INPUT_HINTS)), None)
        if hint and click_node(hint):
            invalidate_scan()
            time.sleep(SETTLE_PAGE)
            field = "ocr"
        else:
            print("  未定位到搜索输入框，本项跳过")
            return False

    keyword = SEARCH_TERMS[_search_index["i"] % len(SEARCH_TERMS)]
    _search_index["i"] += 1
    print(f"  搜索关键词：{keyword}")
    try:
        invalidate_scan()
        if field != "ocr":
            field.click()
            time.sleep(0.3)
        d.send_keys(keyword, clear=True)
        time.sleep(0.3)
        invalidate_scan()

        # ⚠️ 只按 enter 不跳转：优先 OCR 点「确认 / 搜索」，找不到才回车兜底。
        before_texts = set()
        for attempt in range(3):
            invalidate_scan()
            time.sleep(0.4)
            nodes = read_page(force=True)
            now_texts = {normalize(n["text"]) for n in nodes}
            if attempt:
                # 结果页不一定 OCR 得到「综合 / 销量」，用「页面文字大量更新」
                # 兜底，避免把已跳走的页面当成没跳而重复提交、重置倒计时。
                if _on_search_result_page(nodes) or len(now_texts - before_texts) >= 3:
                    print("  ✅ 已跳转到搜索结果页")
                    return True
            before_texts = now_texts
            btn = _find_search_submit(nodes)
            if btn is not None:
                print(f"  ↗ 点击【{normalize(btn['text'])}】提交搜索，等待跳转…")
                if click_node(btn):
                    invalidate_scan()
                    time.sleep(1.2)
                    continue
            print("  ⌨ 未识别到搜索按钮，按回车提交")
            d.press("enter")
            invalidate_scan()
            time.sleep(1.2)

        invalidate_scan()
        if _on_search_result_page(read_page(force=True)):
            print("  ✅ 已跳转到搜索结果页")
        else:
            # 不直接判失败：仍继续浏览，到账与否由主循环「今日速赚增量」复核。
            print("  ⚠️ 未确认跳转到搜索结果页，继续浏览（由主循环核对到账）")
        return True
    except Exception as exc:
        print(f"  搜索输入失败：{exc}")
        return False


def back_to_task_page():
    """回到金币任务列表。退到活动主页就重进，误退/抖动会自动恢复，不再一退就结束整轮。

    返回键不固定按 2 次：走了「搜索 → 商品详情」的任务需要连退多一层
    （详情 → 结果页 → 搜索落地页 → 任务列表）。真正导致退出桌面的是
    在淘宝主界面（TBMainActivity）上继续按返回，所以在那一层停手并重新导航。
    """
    backs = 0
    leave_dialogs = 0
    for _ in range(10):
        if not taobao_on_top(confirm=TAOBAO_CONFIRM):
            # 旧逻辑在这里打印「已离开淘宝，停止」并直接结束整轮，
            # 正是「任务老是自动结束」的主因。现在先恢复淘宝再重新导航。
            if not recover_taobao_front():
                return False
            invalidate_scan()
            if navigate_to_task_list():
                return True
            continue
        current = d.app_current() or {}
        nodes = read_page()
        if is_task_page(nodes):
            return True
        # 退到了淘金币活动主页：直接重新进列表，别继续按返回（会退回淘宝首页）
        reentry = [n for n in nodes
                   if normalize(n["text"]) in ("去赚金币", "去赚淘金币")]
        if len(reentry) == 1:
            print("  回到活动主页，重新点击【去赚金币】")
            if click_node(reentry[0]):
                time.sleep(SETTLE_PAGE)
                continue
        # ⛔ 淘宝主界面再按返回就是退出 App：停手，改走重新导航回列表
        if str(current.get("activity") or "").endswith("TBMainActivity"):
            print("  已到淘宝主界面，不再按返回，重新导航回任务列表")
            if navigate_to_task_list():
                return True
            return False
        if backs < 6:
            backs += 1
            press_back()
            time.sleep(SETTLE_BACK)
            # 离开浏览任务时淘宝可能弹「放弃进度」对话框：拦下并留在页面，
            # 避免把刚做了一半的任务进度作废（即「自动取消进度」）。
            after = read_page()
            if dismiss_leave_dialog(after):
                if is_task_page(after):
                    return True
                leave_dialogs += 1
                if leave_dialogs <= LEAVE_DIALOG_LIMIT:
                    # 弹「放弃进度」说明时长还没做够：留在页面继续浏览补时，
                    # 而不是立刻再按返回（旧逻辑会反复弹框直到循环耗尽 → 结束）。
                    print(f"  进度未满足，留在页面继续浏览补足时长"
                          f"（第 {leave_dialogs}/{LEAVE_DIALOG_LIMIT} 次）")
                    try:
                        execute_browse_task(d, min_duration=10, max_wait=14)
                    except Exception:
                        time.sleep(10)
                    invalidate_scan()
                    backs = max(0, backs - 1)
                    continue
                print("  已补足浏览时长，允许离开本页")
        else:
            print("  返回次数已达上限，尝试重新导航回任务列表")
            if navigate_to_task_list():
                return True
            return False
    print("  未能返回淘金币任务列表，本轮交给主循环恢复重试")
    return False


def verify_task(before):
    """仅有进度增长或同一任务行的明确完成状态才计入已确认。

    这里强制重新 OCR：到账后进度/已完成标记是页面异步刷新的，
    用缓存会读到任务完成前的旧画面，把做成的任务记成失败。
    """
    for _ in range(1):
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
    return False


def scroll_list():
    swipe(screen_width // 2, int(screen_height * .75),
          screen_width // 2, int(screen_height * .48), duration=.4)
    time.sleep(SETTLE_SCROLL)


def scroll_up_to_list(tries=3):
    """往上滚回任务区。金币页下面接着商品流，滑下去后任务行会离开视口。"""
    for _ in range(tries):
        nodes = read_page()
        if is_task_page(nodes):
            return nodes
        swipe(screen_width // 2, int(screen_height * .35),
              screen_width // 2, int(screen_height * .8), duration=.3)
        time.sleep(SETTLE_SCROLL)
    return read_page()


def find_expand_button(nodes):
    """定位「更多专享福利」折叠区的展开按钮。

    实测空屏右侧文字块里就有「展开」。已展开状态会变成「收起」，必须排除，
    否则再点一次反而把列表收回去。多个候选取列表下方最靠上的一个。
    """
    cands = []
    for n in nodes:
        t = normalize(n["text"])
        if not t or len(t) > 10 or any(w in t for w in COLLAPSE_TEXTS):
            continue
        if any(t == w or t.startswith(w) for w in EXPAND_TEXTS):
            cands.append(n)
    if not cands:
        return None
    lower = [n for n in cands if n["cy"] > screen_height * .3]
    cands = lower or cands
    cands.sort(key=lambda n: n["cy"])
    return cands[-1]


def expand_button_ready(node):
    """同一位置的【展开】刚点过就先跳过，防止页面没展开时反复点同一处。"""
    pos = (int(node["cx"]) // 20, int(node["cy"]) // 20)
    return not (_last_expand["pos"] == pos
                and time.monotonic() - _last_expand["at"] < EXPAND_RETRY_GAP)


def mark_expand(node):
    _last_expand["pos"] = (int(node["cx"]) // 20, int(node["cy"]) // 20)
    _last_expand["at"] = time.monotonic()


def at_list_top(nodes):
    """是否已经回到金币任务列表顶部（顶部锚点可见）。"""
    texts = [normalize(n["text"]) for n in nodes]
    return any("今日速赚" in t or t in ("为你找到最简单的任务", "完成下方任务得额外金币")
               for t in texts)


def scroll_to_top(tries=8):
    """一路往上滑回列表顶部，供「完整遍历」重新开始。

    旧逻辑只会往下滚，滚到底后就一直读同一片商品流，再也看不到顶部任务，
    于是把「还有一堆任务没做」误判成「本轮已做完」。
    """
    for _ in range(tries):
        nodes = read_page(force=True)
        if at_list_top(nodes):
            return nodes
        swipe(screen_width // 2, int(screen_height * .35),
              screen_width // 2, int(screen_height * .8), duration=.3)
        time.sleep(SETTLE_SCROLL)
    return read_page(force=True)


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
            time.sleep(SETTLE_PAGE)
            invalidate_scan()
            try:
                dismiss_notice(read_page(force=True))
            except Exception:
                pass
            return True
    return False


def recover_task_page():
    """任务列表失联后的统一恢复：确认淘宝在前台 → 重新导航 → 必要时上滑回列表。"""
    if not recover_taobao_front():
        return False
    for i in range(3):
        nodes = read_page(force=True)
        if is_task_page(nodes):
            print("  ✅ 已恢复金币任务列表，继续执行剩余任务")
            return True
        if i == 0:
            print("  正在重新进入【去赚金币】…")
        if navigate_to_task_list():
            nodes = read_page(force=True)
            if is_task_page(nodes):
                print("  ✅ 已恢复金币任务列表，继续执行剩余任务")
                return True
        scroll_up_to_list()
        invalidate_scan()
        time.sleep(SETTLE_PAGE)
    return is_task_page(read_page(force=True))


def run_tasks(max_tasks=30):
    # 上次运行记下的「会跳外部链接」任务，这次启动直接不做。
    load_external_link_tasks()
    attempts, finished = {}, set()
    confirmed_progress = {}
    total, confirmed, empty_times = 0, 0, 0
    expand_clicks = 0
    start_coins = None
    last_coins = None
    recover_fail = 0       # 连续恢复失败次数
    claim_streak = 0       # 连续领取累计奖励次数，防止一直卡在领取
    sweep_empty = 0        # 连续「完整遍历都没有任务」的轮数
    still_screens = 0      # 连续读到相同文字块的屏数（到底判定）
    last_signature = None
    end_reason = f"已达本轮最大执行次数 {max_tasks}"

    # 刚进列表时 WebView 还在渲染：实测首屏只 OCR 到 20 多块、任务行尚未出现。
    # 旧逻辑在这里立刻判「没任务」并点【展开】，页面一重排顶部任务就被滚走，
    # 之后全程 0 个任务却报「本轮已做完」。先等任务行渲染出来再开始。
    for _ in range(6):
        boot_nodes = read_page(force=True)
        if parse_task_list(boot_nodes):
            break
        time.sleep(SETTLE_PAGE)
    else:
        print("  [提示] 任务行尚未渲染，仍按正常流程从顶部开始扫描")

    while total < max_tasks:
        nodes = read_page()
        # ⛔ 这里绝不关弹窗：任务列表页上的 X 是正常控件（实测误点过
        # x=1171,y=361 的右上小叉）。弹窗只在「点进任务之后」处理。
        if not is_task_page(nodes):
            # 这里的金币任务列表会自己退回淘金币活动主页（实测约 4 秒后），
            # 所以不能只是等待：发现退回就重新点一次「去赚金币」再进。
            recovered = False
            for attempt in range(3):
                time.sleep(SETTLE_PAGE)
                nodes = read_page(force=True)   # 这里就是「等页面自己变化」，必须重扫
                if is_task_page(nodes):
                    recovered = True
                    break
                # 先往上滚一下：滑进商品流时任务行会离开视口，误判成「不在列表」
                swipe(screen_width // 2, int(screen_height * .35),
                      screen_width // 2, int(screen_height * .8), duration=.3)
                time.sleep(SETTLE_SCROLL)
                nodes = read_page(force=True)
                if is_task_page(nodes):
                    recovered = True
                    break
                reentry = [n for n in nodes
                           if normalize(n["text"]) in ("去赚金币", "去赚淘金币")]
                if len(reentry) == 1:
                    print("  页面已退回活动主页，重新点击【去赚金币】再进")
                    if click_node(reentry[0]):
                        time.sleep(SETTLE_PAGE)
                        nodes = read_page(force=True)
                        if is_task_page(nodes):
                            recovered = True
                            break
                else:
                    print(f"  第 {attempt + 1} 次复核仍不是任务列表"
                          f"（可见 {len(nodes)} 块，前台 {_pkg_once()}）")
            if not recovered:
                recover_fail += 1
                print(f"  页面不是任务列表（第 {recover_fail}/{MAX_RECOVER_FAILURES} 次），"
                      f"尝试恢复后继续，不会直接结束")
                if recover_fail >= MAX_RECOVER_FAILURES:
                    end_reason = "连续多次无法回到金币任务列表"
                    break
                if recover_task_page():
                    recover_fail = 0
                    # 重新导航进列表 → 折叠区又收起了，累计次数不能再拦着
                    expand_clicks = 0
                invalidate_scan()
                continue
        recover_fail = 0
        # 每轮先尝试领取「今日累计奖励」（进度满后的额外金币）。
        # 仅当按钮为领取类文案时才点，进度未满的「去完成/做任务」不会误点。
        if claim_accumulated_reward(nodes) and claim_streak < 3:
            claim_streak += 1
            invalidate_scan()
            time.sleep(SETTLE_PAGE)
            continue
        claim_streak = 0
        # 顶部「每日任务」优先：主循环每轮先做当前屏可见的任务，只有当前屏
        # 已经没有可执行的了才去点【展开】或往下滚，避免一进列表就展开、
        # 把顶部任务整批滚出屏幕（上次「还有好几个任务没做」的成因之一）。
        tasks = [t for t in parse_task_list(nodes)
                 if task_key(t) not in finished
                 and task_key(t) not in _external_skipped
                 and attempts.get(task_key(t), 0) < MAX_ATTEMPTS
                 and (not t["progress"] or t["progress"][0] >= confirmed_progress.get(task_key(t), 0))]
        print(f"  [扫描] 可见 {len(nodes)} 块文字，可执行 {len(tasks)} 个任务")
        if not tasks:
            # 只有「疑似还在加载/切换」（OCR 读到的文字块很少）才额外重扫一次。
            # 旧逻辑每一屏空都无条件 sleep 1s + 重扫一次，滚 20 屏就白等 40 秒。
            if len(nodes) < 12:
                time.sleep(SETTLE_PAGE)
                nodes = read_page(force=True)
                tasks = [t for t in parse_task_list(nodes)
                         if task_key(t) not in finished
                         and task_key(t) not in _external_skipped
                         and attempts.get(task_key(t), 0) < MAX_ATTEMPTS
                         and (not t["progress"] or t["progress"][0] >= confirmed_progress.get(task_key(t), 0))]
            if not tasks:
                if not is_task_page(nodes):
                    # 滑进商品流或页面正在切换：先回列表，不要继续往下滚
                    scroll_up_to_list()
                    invalidate_scan()
                    continue
                # ★ 只要屏上还看得到【展开】就立刻点（常在右下角）。
                # 旧逻辑只在「本轮第一次」趁早点，之后必须等扫到底才点：
                # 实测页面退回活动主页再进来后折叠区重新收起，却因计数已是 1
                # 连扫 11 屏都不点。折叠区可能多层，不再用次数限制触发时机。
                if expand_clicks < MAX_EXPAND_CLICKS:
                    expand_btn = find_expand_button(nodes)
                    if (expand_btn and expand_button_ready(expand_btn)
                            and click_node(expand_btn)):
                        mark_expand(expand_btn)
                        expand_clicks += 1
                        print(f"  点击【{normalize(expand_btn['text'])}】，展开更多任务"
                              f"（第 {expand_clicks}/{MAX_EXPAND_CLICKS} 次）")
                        # 展开会重排页面，等切换完成；随后直接回主循环重新解析。
                        # 绝不在这里回顶部，否则刚展开出来的任务又被滚走。
                        for _ in range(4):
                            time.sleep(SETTLE_PAGE)
                            fresh = read_page(force=True)
                            if fresh and is_task_page(fresh):
                                break
                        invalidate_scan()
                        empty_times = 0
                        still_screens = 0
                        last_signature = None
                        sweep_empty = 0
                        continue
                empty_times += 1
                signature = "|".join(normalize(n["text"]) for n in nodes)
                if signature == last_signature:
                    still_screens += 1
                else:
                    still_screens = 0
                    last_signature = signature
                if empty_times % 5 == 1:
                    preview = [normalize(n["text"]) for n in nodes
                               if n["cx"] > screen_width * .7][:6]
                    print(f"  [空屏 {empty_times}] 可见 {len(nodes)} 块；右侧文字：{preview}")
                # 还没到底就继续往下滚（滚到出现【展开】的那一屏会立刻点）
                if still_screens < STILL_SCREEN_LIMIT and empty_times < EMPTY_LIMIT:
                    scroll_list()
                    continue
                # 扫到底了：若还有未展开的折叠区，再点一次并继续扫，别急着回顶
                if expand_clicks < MAX_EXPAND_CLICKS:
                    expand_btn = find_expand_button(nodes)
                    if (expand_btn and expand_button_ready(expand_btn)
                            and click_node(expand_btn)):
                        mark_expand(expand_btn)
                        expand_clicks += 1
                        print(f"  再点【{normalize(expand_btn['text'])}】"
                              f"（第 {expand_clicks}/{MAX_EXPAND_CLICKS} 次展开）")
                        for _ in range(3):
                            time.sleep(SETTLE_PAGE)
                            if is_task_page(read_page(force=True)):
                                break
                        invalidate_scan()
                        empty_times = 0
                        still_screens = 0
                        last_signature = None
                        continue
                # 回顶部完整重扫。收工前必须已经展开过，否则宁可多扫几轮，
                # 也不能把折叠区里没露出来的任务漏掉。
                sweep_empty += 1
                if (sweep_empty >= SWEEP_LIMIT and expand_clicks >= 1
                        and not find_expand_button(nodes)):
                    end_reason = (f"已展开 {expand_clicks} 次，连续 {sweep_empty} 轮"
                                  f"「顶部→底部→回顶部」完整遍历都没有可执行任务，本轮任务已做完")
                    break
                if sweep_empty >= SWEEP_LIMIT * 2:
                    end_reason = (f"已展开 {expand_clicks} 次、完整遍历 {sweep_empty} 轮，"
                                  f"屏上仍有【展开】或没有可执行任务，停止"
                                  f"（请确认手机停在淘金币任务列表）")
                    break
                print(f"  已扫到列表底部，回到顶部重新遍历（第 {sweep_empty + 1} 轮，"
                      f"已展开 {expand_clicks} 次）")
                scroll_to_top()
                attempts.clear()   # 给被 MAX_ATTEMPTS 挡住的任务一次重试机会
                empty_times = 0
                still_screens = 0
                last_signature = None
                continue
        empty_times = 0
        still_screens = 0
        last_signature = None
        sweep_empty = 0
        task = tasks[0]
        key = task_key(task)
        # 「带有浏览的任务都做」：不再因为要求时长 > 30s 就整项跳过；
        # 是否跳出淘宝由 exec_task 按前台包名决定，标题不作跳过依据。
        # 单次兜底浏览仍以 30s 为上限，页面自带倒计时时按真实秒数做满，
        # 出现「已得N淘金币」立即收工。
        attempts[key] = attempts.get(key, 0) + 1
        total += 1
        before_coins = today_coins(nodes)
        if start_coins is None:
            start_coins = before_coins
        print(f"  [{total}] {task['name']} ({task['type']}, {task['seconds']}秒)")
        task_t0 = time.monotonic()
        link_before = external_link_blocked_count()
        try:
            exec_task(task)
        except Exception as exc:
            print(f"  任务执行异常：{exc}")
        # 执行过程中才弹出外链提示（视频/浏览下滑到站外链接）：已取消跳转，
        # 这一项本轮不再做，也不重试，避免反复弹窗。
        if external_link_blocked_count() > link_before:
            print(f"  ⛔ 【{task['name']}】执行中要跳外部链接，已取消；以后不再做这一项")
            remember_external_link_task(key)
            finished.add(key)
            invalidate_scan()
        if not back_to_task_page():
            recover_fail += 1
            print(f"  返回任务列表失败（第 {recover_fail}/{MAX_RECOVER_FAILURES} 次），"
                  f"尝试恢复后继续，不会直接结束")
            if recover_fail >= MAX_RECOVER_FAILURES:
                end_reason = "连续多次无法返回金币任务列表"
                break
            if recover_task_page():
                recover_fail = 0
            invalidate_scan()
            continue
        recover_fail = 0

        # 优先用「今日速赚」的增量确认到账，其次才看任务行进度
        print(f"  ⏱ 本项执行+返回耗时 {time.monotonic() - task_t0:.1f}s")
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
    print(f"  [结束原因] {end_reason}")
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
        time.sleep(SETTLE_PAGE)

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
