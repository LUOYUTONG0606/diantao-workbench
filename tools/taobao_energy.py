"""淘金币双11赚能量：U2 优先扫描，OCR 后备，逐项浏览并复查。

uv run python tools/taobao_energy.py scan
uv run python tools/taobao_energy.py browse 好物随心逛 --seconds 25
默认不批量点击未知任务，不升级赛车、不花积分、不购买、不发送邀请。
"""
import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uiautomator2 as u2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device_connection import connect_with_retry
from cloud_ocr import recognize, ocr_server_url
from task_recheck import RECHECK_ENV, report_round_finished, report_task_found, recheck_enabled

TB = "com.taobao.taobao"
SKIP = ("升级赛车", "小游戏", "积分", "拍立淘", "邀请", "下单", "购买", "开通", "订阅", "预约", "答题", "考试")
BROWSE = ("好物随心逛", "好物沉浸看", "去省钱卡领红包", "去芭芭农场领水果", "去签到领红包",
          "搜一搜你心仪的宝贝", "搜一搜你心仪的宝", "搜一搜你喜欢的宝贝", "天天签到免费拿IP周边",
          "去组队可加快升级", "你关注的商品都可抵", "拍立淘逛感兴趣的宝贝",
          "来闪购逛逛领红包", "去惊喜好物逛一逛",
          # 本机新增：金币任务列表里常见的站内浏览任务（页面上实测存在）
          "逛清单")
QUIZ = "淘金币省钱等级考试"
SEARCH_TERMS = ("固态硬盘", "牛肉干", "移动硬盘", "牙刷", "纸巾")
# 仅访问已实测的页面，不操作目的应用内的消费、抽奖、授权或邀请。
# ⚠️ 本机修改：只保留淘宝站内（com.taobao.taobao）任务。
# 跨应用任务（支付宝系/微博/闲鱼/快手/一淘/特价版/百度/天猫）已全部移除——
# 这类跳转最容易触发账号风控（实测后被要求做实名身份验证）。
# 需要恢复时，把下面注释块里的条目放回 VISIT 即可。
VISIT = {
    "领闪购红包下单直接省": TB,
    "看淘宝视频领红包": TB,
}

# 以下为本机主动停用的跨应用条目（原版内容，保留备查）：
#   "去微博看看热搜": "com.sina.weibo",
#   "去闲鱼签到省钱": "com.taobao.idlefish",
#   "去快手打卡领手机": "com.kuaishou.nebula",
#   "去一淘签到领现金": "com.taobao.etao",
#   "去淘宝特价版0元购": "com.taobao.litetao",
#   "去蚂蚁森林收能量": "com.eg.android.AlipayGphone",
#   "逛一逛蚂蚁庄园": "com.eg.android.AlipayGphone",
#   "逛一逛支付宝农场": "com.eg.android.AlipayGphone",
#   "逛一逛支付宝金豆夺宝": "com.eg.android.AlipayGphone",
#   "去支付宝抽1000元红包": "com.eg.android.AlipayGphone",
#   "去抽最高1万淘金币": "com.eg.android.AlipayGphone",
#   "去芝麻信用涨分攒进度": "com.eg.android.AlipayGphone",
#   "去百度App领现金": "com.baidu.searchbox",
#   "去百度极速版领现金": "com.baidu.searchbox.lite",
#   "逛一逛领购物红包": "com.tmall.wireless",
# 淘宝内的“正在跳转”引导页：只点与目标应用同名的“打开X”，不点“下载X”。
LANDING = {
    "com.taobao.etao": "一淘",
}
OUT = Path(__file__).resolve().parents[1] / "screenshots" / "taobao_energy"
LOG = logging.getLogger("taobao_energy")
_raw_scan = False
_session = {}
_vision_recognize = None
_vision_source = "配置的OCR服务"


def setup_logging(directory=None):
    directory = directory or Path(__file__).resolve().parents[1] / "logs" / "taobao_energy"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
    for handler in LOG.handlers[:]:
        LOG.removeHandler(handler)
        handler.close()
    LOG.setLevel(logging.DEBUG)
    LOG.propagate = False
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    detail = logging.FileHandler(path, encoding="utf-8")
    detail.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(console)
    LOG.addHandler(detail)
    _session.clear()
    _session.update(start=time.monotonic(), completed=0)
    return path


def log_summary():
    if "start" not in _session:
        return
    LOG.info("[汇总] 本次确认完成 %s 项，耗时 %s 秒", _session["completed"],
             round(time.monotonic()-_session["start"]))


def guard(deadline):
    if datetime.now(timezone(timedelta(hours=8))) >= deadline:
        raise RuntimeError("本次运行时限已到，已停止手机操作")


def run_deadline(value, minutes, now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    if minutes <= 0:
        raise ValueError("--run-minutes 必须大于0")
    deadline = datetime.fromisoformat(value) if value else now + timedelta(minutes=minutes)
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise ValueError("--deadline 必须包含时区，例如 +08:00")
    if deadline <= now:
        raise ValueError("--deadline 已过期，请指定未来时间")
    return deadline


def scan(d):
    OUT.mkdir(parents=True, exist_ok=True)
    shot = d.screenshot()
    shot.save(OUT / "current.png")
    nodes = []
    app = d.app_current()
    # 实测此活动的 Themis WebView 不暴露文本，倒计时页面的 dump 还会长时间等待空闲。
    tree = "<hierarchy/>" if "TMSActivity" in app["activity"] else d.dump_hierarchy()
    for n in ET.fromstring(tree).iter("node"):
        if n.get("package") != app["package"]:
            continue
        text = n.get("text") or n.get("content-desc")
        bounds = list(map(int, re.findall(r"\d+", n.get("bounds", ""))))
        if text and len(bounds) == 4:
            x1, y1, x2, y2 = bounds
            nodes.append(dict(text=text, cx=(x1+x2)//2, cy=(y1+y2)//2))
    if not any("做任务赚能量" in n["text"] for n in nodes):
        source = "U2+" + _vision_source if nodes else _vision_source
        for node in (_vision_recognize or recognize)(shot):
            if not any(n["text"] == node["text"] and abs(n["cx"]-node["cx"]) < 30
                                      and abs(n["cy"]-node["cy"]) < 30 for n in nodes):
                nodes.append(node)
    else:
        source = "U2"
    result = dict(app=app, source=source, blocks=sorted(nodes, key=lambda n: (n["cy"], n["cx"])))
    (OUT / "current.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.debug("屏幕识别 %s", json.dumps(result, ensure_ascii=False))
    if _raw_scan:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return nodes


def task_button(nodes, title, width):
    if any(s in title for s in SKIP) and title not in ("领闪购红包下单直接省", "拍立淘逛感兴趣的宝贝"):
        raise RuntimeError("此任务按约定跳过: " + title)
    if not any("做任务赚能量" in n["text"] for n in nodes):
        raise RuntimeError("当前不是赚能量任务列表")
    matches = [n for n in nodes if re.split(r"[(（]", n["text"])[0].strip() == title]
    if len(matches) != 1:
        raise RuntimeError("任务未唯一定位: " + title)
    row = matches[0]
    buttons = [n for n in nodes if n["text"] == "去完成" and n["cx"] > width*.7
               and abs(n["cy"]-row["cy"]) < width*.09]
    if len(buttons) != 1:
        raise RuntimeError("任务按钮未唯一定位: " + title)
    return buttons[0]


def task_rows(nodes, width):
    """Return rows with a visible action button; progress totals are not an end signal."""
    buttons = [
        node for node in nodes
        if node["text"] == "去完成" and node["cx"] > width * .7
    ]
    rows = []
    for button in buttons:
        row = [
            node for node in nodes
            if abs(node["cy"] - button["cy"]) < width * .09
            and node["text"] != "去完成"
            and not (
                node["text"] in ("已完成", "已领取")
                and node["cx"] > width * .7
            )
        ]
        candidates = [
            node for node in row
            if node["cx"] < width * .72
            and not re.fullmatch(r"[\d+\s]+", node["text"])
            and "浏览" not in node["text"]
            and "点击" not in node["text"]
        ]
        # 图标文字（￥、支、闲鱼）与按钮同一水平线，按距离会盖过标题；标题带 n/m 进度。
        titled = [node for node in candidates if re.search(r"[(（]\s*\d+\s*/\s*\d+", node["text"])]
        candidates = titled or candidates
        if candidates:
            title = min(candidates, key=lambda node: (
                abs(node["cy"] - button["cy"]),
                node["cy"] > button["cy"],
                abs(node["cx"] - button["cx"]),
            ))
            rows.append((title, button))
    return rows


def has_pending_tasks(nodes, width):
    """Return whether an eligible task still has a visible 去完成 action."""
    for row, _button in task_rows(nodes, width):
        title = re.split(r"[(（]", row["text"])[0].strip()
        if task_kind(title) != "skip":
            return True
    return False


def pending_summary(nodes, width):
    """Summarize visible task rows without reading progress or balances."""
    pending = []
    skipped = []
    for row, _button in task_rows(nodes, width):
        title = re.split(r"[(（]", row["text"])[0].strip()
        (skipped if task_kind(title) == "skip" else pending).append(title)
    return pending, skipped


def task_progress(nodes):
    for n in nodes:
        match = re.fullmatch(r"(?:当前进度\s*)?(\d+)\s*/\s*50", n["text"].strip())
        if n["cx"] < 250 and match:
            return int(match[1])
    return None


def task_row_progress(nodes, title):
    if title is None:
        return None
    rows = [n for n in nodes if re.split(r"[(（]", n["text"])[0].strip() == title]
    if len(rows) != 1:
        return None
    match = re.search(r"[(（](\d+)\s*/\s*(\d+)[)）]", rows[0]["text"])
    return (int(match[1]), int(match[2])) if match else None


# 本机主动停用的跨应用任务：标题精确匹配，一律跳过。
# 为什么要单独一份：allowed_browse() 里「逛一逛 / 逛逛」是宽泛规则，
# 把 VISIT 里的条目删掉后，「逛一逛支付宝农场」这类会被误判成站内浏览任务而点进去。
BLOCKED_CROSS_APP = {
    "去微博看看热搜", "去闲鱼签到省钱", "去快手打卡领手机", "去一淘签到领现金",
    "去淘宝特价版0元购", "去蚂蚁森林收能量", "逛一逛蚂蚁庄园", "逛一逛支付宝农场",
    "逛一逛支付宝金豆夺宝", "去支付宝抽1000元红包", "去抽最高1万淘金币",
    "去芝麻信用涨分攒进度", "去百度App领现金", "去百度极速版领现金",
    "逛一逛领购物红包",
}

# 关键词兜底：任务会被平台改名，出现这些词就直接跳过。
# 注意不要用「农场」——淘宝自家的「芭芭农场」是要做的站内任务。
CROSS_APP_WORDS = (
    "支付宝", "蚂蚁庄园", "蚂蚁森林", "金豆", "微博", "闲鱼", "快手",
    "一淘", "特价版", "百度", "天猫",
)


def is_cross_app(title):
    return title in BLOCKED_CROSS_APP or any(word in title for word in CROSS_APP_WORDS)


def allowed_browse(title):
    if is_cross_app(title):
        return False
    if title == "拍立淘逛感兴趣的宝贝":
        return True
    if any(s in title for s in SKIP):
        return False
    if title in VISIT:
        return False
    return (title in BROWSE or title.startswith("看看#") or title.startswith("逛清单")
            or "逛逛" in title
            or "逛一逛" in title or "随心逛" in title or "沉浸看" in title)


def task_kind(title):
    if allowed_browse(title):
        return "taobao"
    if title in VISIT:
        if VISIT[title] == TB:
            return "taobao"
        return "alipay" if VISIT[title] == "com.eg.android.AlipayGphone" else "external"
    return "skip"


class RewardNotConfirmed(RuntimeError):
    def __init__(self, nodes):
        super().__init__("未确认任务奖励增加")
        self.nodes = nodes


def task_action_state(nodes, title, width):
    """Read only the task row's action. Progress counters are ignored."""
    if title is None or not any("做任务赚能量" in n["text"] for n in nodes):
        return "unknown"
    rows = [n for n in nodes if re.split(r"[(（]", n["text"])[0].strip() == title]
    if len(rows) != 1:
        return "missing"
    actions = [
        n["text"] for n in nodes
        if n["cx"] > width * .7
        and abs(n["cy"] - rows[0]["cy"]) < width * .09
        and n["text"] in ("去完成", "已完成", "已领取", "已领")
    ]
    if actions == ["去完成"]:
        return "pending"
    if len(actions) == 1:
        return "done"
    return "unknown"


def verify_gain(d, nodes, before, before_task, deadline, *, title=None, recover_visit=False):
    """Settle one action without using progress as completion proof."""
    del before
    for attempt in range(3):
        guard(deadline)
        if recover_visit and (
                d.app_current()["package"] != TB
                or not any("做任务赚能量" in n["text"] for n in nodes)):
            nodes = return_from_visit(d, deadline)
        width = d.window_size()[0] if hasattr(d, "window_size") else 1264
        action = task_action_state(nodes, title, width)
        if action == "done" or (action == "missing" and before_task is not None):
            _session["completed"] = _session.get("completed", 0) + 1
            LOG.info("[完成] %s | 任务行已结束", title or "当前任务")
            return nodes
        LOG.info("[核对] %s/3 | %s | 动作=%s | 进度不参与判定",
                 attempt + 1, title or "当前任务", action)
        if attempt < 2:
            time.sleep(3)
            nodes = scan(d)
    LOG.info("[继续] %s | 动作已返回，任务行仍待办，交由下一轮继续",
             title or "当前任务")
    return nodes


def return_from_visit(d, deadline):
    """跳转后有限重试回淘宝；不在目标应用内按返回或处理登录。"""
    restart_needed = False
    for attempt in range(3):
        guard(deadline)
        if d.app_current()["package"] != TB or restart_needed:
            LOG.info("[返回] 切回淘宝（%s/3）", attempt + 1)
            if attempt or restart_needed:
                d.app_start(TB, stop=True, use_monkey=True)
            else:
                d.app_start(TB, use_monkey=True)
            restart_needed = False
            time.sleep(3)
        if d.app_current()["package"] != TB:
            continue
        nodes = scan(d)
        if d.app_current()["package"] != TB:
            # OCR期间也可能发生延迟跳转，不能使用已失效的列表。
            continue
        if any("做任务赚能量" in n["text"] for n in nodes):
            # 延迟的 Android 跳转可能在识别结束后才发生，确认稳定后再交还执行权。
            time.sleep(2)
            guard(deadline)
            if d.app_current()["package"] != TB:
                continue
            LOG.info("[返回] 已确认淘宝能量任务列表，继续任务")
            return nodes
        current = d.app_current()
        home = any(name in current.get("activity", "") for name in ("Welcome", "TBMainActivity"))
        event = any("淘金币双11" in n["text"] for n in nodes) or (
            any(n["text"] == "升级赛车拿钱" for n in nodes)
            and any(n["text"] == "去赚能量" for n in nodes)
        )
        if home or event:
            try:
                nodes = enter(d, deadline)
            except RuntimeError as error:
                guard(deadline)
                if d.app_current()["package"] != TB or any(
                    text in str(error) for text in (
                        "请先回到淘宝首页", "未唯一识别赚能量按钮", "仍未确认任务列表",
                    )
                ):
                    LOG.info("[返回] 活动入口未确认，重新打开淘宝后复查")
                    restart_needed = True
                    continue
                raise
            time.sleep(2)
            guard(deadline)
            if d.app_current()["package"] == TB:
                LOG.info("[返回] 已重新进入淘宝能量任务列表，继续任务")
                return nodes
        else:
            guard(deadline)
            if d.app_current()["package"] != TB:
                continue
            d.press("back")
            time.sleep(2)
    raise RuntimeError("已尝试3次返回淘宝，仍未确认能量任务列表，已停止")


def landing_open_button(nodes, package):
    """在淘宝内的“正在跳转”引导页上定位目标应用自己的“打开X”。"""
    label = LANDING.get(package)
    if not label or not any("正在跳转" in n["text"] for n in nodes):
        return None
    if not any(n["text"] == label for n in nodes):
        return None
    buttons = [n for n in nodes if n["text"] == "打开" + label]
    return buttons[0] if len(buttons) == 1 else None


def allow_open_once(d, deadline):
    """系统“淘宝想要打开X”确认框只点“仅本次允许”，不改成长期权限。"""
    dialog = scan(d)
    once = [n for n in dialog if n["text"] == "仅本次允许"]
    if not any("淘宝" in n["text"] and "想要打开" in n["text"] for n in dialog):
        return False
    if len(once) != 1:
        return False
    guard(deadline)
    d.click(once[0]["cx"], once[0]["cy"])
    return True


def open_landing_app(d, package, deadline, attempts=6, wait=4):
    """引导页延迟出现，需轮询并在确认页面内容后点“打开X”；不点“下载X”。"""
    for _ in range(attempts):
        guard(deadline)
        current = d.app_current()["package"]
        if current == package:
            return True
        if current == "com.oplus.securitypermission":
            allow_open_once(d, deadline)
            continue
        if current != TB:
            return False
        opening = landing_open_button(scan(d), package)
        if opening is None:
            time.sleep(wait)
            continue
        LOG.info("[跳转] 淘宝内引导页，点击打开%s", LANDING[package])
        guard(deadline)
        d.click(opening["cx"], opening["cy"])
        for _ in range(20):
            guard(deadline)
            time.sleep(1)
            current = d.app_current()["package"]
            if current == package:
                return True
            if current == "com.oplus.securitypermission":
                allow_open_once(d, deadline)
    return False


def visit(d, title, deadline, nodes):
    before = task_progress(nodes)
    before_task = task_row_progress(nodes, title)
    package = VISIT[title]
    installed = package == TB or bool(d.adb_device.shell(["pm", "path", package]).strip())
    button = task_button(nodes, title, d.window_size()[0])
    guard(deadline)
    d.click(button["cx"], button["cy"])
    # 只等待跳转发生；进入目标应用后立即返回，不等待活动页面加载。
    landed = package == TB
    if installed and package in LANDING:
        landed = open_landing_app(d, package, deadline)
    for second in range(0 if package in LANDING else (30 if installed else 5)):
        guard(deadline)
        time.sleep(1)
        if installed or second == 4:
            current = d.app_current()["package"]
            if not installed:
                break
            if current == package and (package != TB or second >= 3):
                landed = True
                if package in ("com.eg.android.AlipayGphone", "com.baidu.searchbox"):
                    time.sleep(6)
                break
            if current == "com.oplus.securitypermission":
                dialog = scan(d)
                if d.app_current()["package"] != current:
                    continue
                once = [n for n in dialog if n["text"] == "仅本次允许"]
                opening = [n for n in dialog if n["text"] == "打开"]
                if not any("淘宝" in n["text"] and "想要打开" in n["text"] for n in dialog):
                    LOG.info("[跳过] 未确认应用授权提示，直接返回淘宝")
                    break
                buttons = once or opening
                if len(buttons) != 1:
                    LOG.info("[跳过] 未唯一识别一次性打开按钮，直接返回淘宝")
                    break
                guard(deadline)
                d.click(buttons[0]["cx"], buttons[0]["cy"])
            elif current in ("com.android.chrome", "com.heytap.market", "com.android.packageinstaller"):
                LOG.info("[跳过] %s：进入下载/安装页，直接返回", title)
                break
            elif current not in (TB, package):
                LOG.info("[跳过] 跳转到未确认页面，直接返回淘宝")
                break
    if not installed:
        LOG.info("[跳过] %s：未安装应用，返回并继续，不下载", title)
    elif not landed and package in LANDING:
        LOG.info("[跳过] %s：引导页未拉起目标应用，返回淘宝", title)
    guard(deadline)
    d.app_start(TB, use_monkey=True)
    time.sleep(4)
    nodes = return_from_visit(d, deadline)
    try:
        return verify_gain(d, nodes, before, before_task, deadline, title=title, recover_visit=True)
    except RewardNotConfirmed as error:
        guard(deadline)
        nodes = error.nodes
        if (d.app_current()["package"] != TB
                or not any("做任务赚能量" in n["text"] for n in nodes)):
            nodes = return_from_visit(d, deadline)
        LOG.info("[未到账] %s：本轮不再重复尝试", title)
        return nodes


def next_task(d, nodes, attempted, deadline):
    """只从当前任务页选择一项；执行后的新扫描结果由调用方继续使用。"""
    guard(deadline)
    LOG.info("[扫描] 检查当前可见任务，顺序：淘宝 → 支付宝 → 其他应用")
    candidates = {}
    width = d.window_size()[0]
    for row, _button in task_rows(nodes, width):
        title = re.split(r"[(（]", row["text"])[0].strip()
        if task_kind(title) == "skip" or title in attempted:
            continue
        try:
            task_button(nodes, title, width)
        except RuntimeError:
            continue
        candidates[title] = {"taobao": 0, "alipay": 1, "external": 2}[task_kind(title)]
    selected = min(candidates, key=candidates.get) if candidates else None
    return selected, nodes


DISMISS = ("我知道了", "知道了", "暂不", "暂不开启", "暂不更新", "稍后再说",
           "下次再说", "关闭弹窗", "关闭广告", "关闭按钮")
PROMPTS = ("开启快递物流提醒", "开启消息通知", "开启通知", "开启推送通知", "发现新版本")


def find_home_coupon_popup_close(nodes):
    """Locate the explicit X on the full-screen home coupon popup."""
    if not any(n["text"] == "关闭按钮" for n in nodes):
        return None
    markers = [n for n in nodes if n["text"] in (
        "6张消费券共", "88VIP专享", "后到期", "去使用",
    )]
    if not markers:
        return None
    candidates = [n for n in nodes if n["text"] == "关闭按钮"]
    if len(candidates) != 1:
        raise RuntimeError("首页券包弹窗的关闭按钮未唯一定位")
    return candidates[0]["cx"], candidates[0]["cy"]


def find_popup_close(d, nodes, deadline):
    """Find X only on the recognizable recommendation overlay."""
    if not any(n["text"] in ("为你推荐好价商品", "去抢频道额外优惠") for n in nodes):
        return None
    w, h = d.window_size()
    # The thin X is often missing from full-screen OCR. Read only the lower strip.
    bottom = d.screenshot().crop((0, round(h * .73), w, h))
    blocks = (_vision_recognize or recognize)(bottom)
    candidates = [n for n in blocks if n["text"].strip().upper() in ("X", "×", "✕", "✖")
                  and w * .38 < n["cx"] < w * .62]
    if len(candidates) != 1:
        raise RuntimeError("商品推荐弹窗的X未唯一定位，已停止")
    guard(deadline)
    return candidates[0]["cx"], candidates[0]["cy"] + round(h * .73)


def find_home_banner_close(d, nodes, deadline):
    """Dismiss a visible home coupon banner only when its X is in the same row."""
    if not any(n["text"].startswith("6张共") and "消费券" in n["text"] for n in nodes):
        return None
    w, h = d.window_size()
    coupons = [n for n in nodes if n["text"].startswith("6张共") and "消费券" in n["text"]
               and n["cy"] > h * .7]
    if len(coupons) != 1:
        return None
    y = coupons[0]["cy"]
    offset = max(0, round(y - 200))
    strip = d.screenshot().crop((0, offset, w, min(h, round(y + 100))))
    blocks = (_vision_recognize or recognize)(strip.crop((0, 150, w, 270)))
    row_offset = offset + 150
    candidates = [n for n in blocks if n["text"].strip().upper() in ("X", "×", "✕", "✖")
                  and n["cx"] > w * .85 and abs(n["cy"] + row_offset - y) < 80]
    if len(candidates) != 1:
        return None
    guard(deadline)
    return candidates[0]["cx"], candidates[0]["cy"] + row_offset


def close_entry_popups(d, deadline, nodes=None):
    """仅在入口清理明确的拒绝/关闭控件；每次点击后重新读取，最多关闭3层。"""
    pattern = "^(" + "|".join(DISMISS) + ")$"
    for attempt in range(4):
        guard(deadline)
        if d.app_current()["package"] != TB:
            raise RuntimeError("入口弹窗处理时已离开淘宝，已停止")
        if nodes is not None:
            coupon_close = find_home_coupon_popup_close(nodes)
            if coupon_close:
                LOG.info("[弹窗] 关闭首页券包弹窗（%s/3）", attempt + 1)
                guard(deadline)
                d.click(*coupon_close)
                time.sleep(1)
                nodes = scan(d)
                continue
            overlay_close = find_popup_close(d, nodes, deadline)
            if overlay_close is None:
                overlay_close = find_home_banner_close(d, nodes, deadline)
            if overlay_close:
                LOG.info("[弹窗] 关闭商品推荐弹窗（%s/3）", attempt + 1)
                guard(deadline)
                d.click(*overlay_close)
                time.sleep(1)
                nodes = scan(d)
                continue
        control = None
        if nodes is None:
            if any(d(text=text).exists for text in PROMPTS):
                candidate = d(text="取消")
                if candidate.exists:
                    control = candidate
            if control is None:
                for query in (dict(textMatches=pattern), dict(descriptionMatches=pattern)):
                    candidate = d(**query)
                    if candidate.exists:
                        control = candidate
                        break
            if control is not None and control.count != 1:
                raise RuntimeError("入口弹窗关闭按钮未唯一定位")
        else:
            labels = set(DISMISS)
            if any(n["text"] in PROMPTS for n in nodes):
                labels.update(("取消", "关闭", "×", "X"))
            matches = [n for n in nodes if n["text"] in labels]
            if len(matches) > 1:
                raise RuntimeError("入口弹窗关闭按钮未唯一定位")
            control = matches[0] if matches else None
        if control is None:
            return nodes
        if attempt == 3:
            raise RuntimeError("入口弹窗连续出现，已达3次处理上限，请检查现场")
        LOG.info("[弹窗] 关闭入口提示（%s/3）", attempt + 1)
        guard(deadline)
        if nodes is None:
            control.click()
        else:
            d.click(control["cx"], control["cy"])
        time.sleep(1)
        if nodes is not None:
            nodes = scan(d)


def open_coin_entry(d, deadline, nodes=None):
    """点开「领淘金币」入口，原生控件失败时用 OCR 兜底。

    淘宝首页是动态重绘的：d(text=...).exists 通过之后到 click() 之间元素可能已重建，
    click() 会抛 UiObjectNotFoundError（code -32002）导致整个任务直接终止。
    这里改成逐个选择器宽容尝试，最后用 OCR 按坐标点一次。
    """
    current = d.app_current()
    if not any(name in current["activity"] for name in ("Welcome", "TBMainActivity")):
        return False

    close_entry_popups(d, deadline)
    for selector in (dict(text="领淘金币"), dict(description="领淘金币")):
        try:
            entry = d(**selector)
            if not entry.exists:
                continue
            guard(deadline)
            LOG.info("[导航] 进入领淘金币")
            entry.click()
            time.sleep(5)
            return True
        except Exception as error:  # noqa: BLE001 - 元素失效属正常现象，继续换方式
            LOG.info("[导航] 领淘金币入口点击失败（%s），换方式重试", error)
            continue

    # OCR 兜底：按识别到的文字坐标点
    try:
        found = [n for n in (nodes if nodes is not None else scan(d))
                 if n["text"].strip() == "领淘金币"]
        if len(found) == 1:
            guard(deadline)
            LOG.info("[导航] 进入领淘金币（OCR 定位）")
            d.click(found[0]["cx"], found[0]["cy"])
            time.sleep(5)
            return True
        if found:
            LOG.info("[导航] 领淘金币入口不唯一（%d 个），跳过点击", len(found))
    except Exception as error:  # noqa: BLE001
        LOG.info("[导航] OCR 定位入口失败（%s）", error)
    return False


def enter(d, deadline, *, restart=False):
    """复用农场库的首页 U2 入口；活动内部用实测可读的 OCR。"""
    if restart:
        guard(deadline)
        LOG.info("[导航] 重新启动淘宝，等待首页")
        d.app_start(TB, stop=True, use_monkey=True)
        for _ in range(15):
            guard(deadline)
            current = d.app_current()
            if current["package"] == TB and any(
                name in current["activity"] for name in ("Welcome", "TBMainActivity")
            ):
                close_entry_popups(d, deadline)
                if d(text="领淘金币").exists or d(description="领淘金币").exists:
                    LOG.info("[导航] 淘宝首页已确认")
                    break
            time.sleep(1)
        else:
            raise RuntimeError("重启后未确认淘宝首页，请检查手机登录状态和弹窗")
    elif d.app_current()["package"] != TB:
        guard(deadline)
        d.app_start(TB)
        time.sleep(3)
    current = d.app_current()
    if any(name in current["activity"] for name in ("Welcome", "TBMainActivity")):
        open_coin_entry(d, deadline)
    nodes = close_entry_popups(d, deadline, scan(d))
    for _ in range(3):
        if any("做任务赚能量" in n["text"] for n in nodes):
            return nodes
        current_event = (
            any(n["text"] == "升级赛车拿钱" for n in nodes)
            and any(n["text"] == "去赚能量" for n in nodes)
        )
        buttons = [n for n in nodes if n["text"] in (
            "赚能量", "賺能量", "去赚能量", "去賺能量",
        )]
        if not current_event and not any("淘金币双11" in n["text"] for n in nodes) and not buttons:
            guard(deadline)
            LOG.info("[导航] 当前未确认活动入口，重新打开淘宝后重试")
            d.app_start(TB, stop=True, use_monkey=True)
            time.sleep(3)
            if any(name in d.app_current()["activity"] for name in ("Welcome", "TBMainActivity")):
                open_coin_entry(d, deadline)
            nodes = close_entry_popups(d, deadline, scan(d))
            continue
        if len(buttons) != 1:
            raise RuntimeError("未唯一识别赚能量按钮")
        guard(deadline)
        LOG.info("[导航] 进入赚能量任务列表")
        d.click(buttons[0]["cx"], buttons[0]["cy"])
        time.sleep(2)
        nodes = close_entry_popups(d, deadline, scan(d))
    if any("做任务赚能量" in n["text"] for n in nodes):
        return nodes
    raise RuntimeError("点击赚能量入口后仍未确认任务列表，已停止")


def browse_seconds(nodes, title, fallback):
    rows = [n for n in nodes if title in n["text"]]
    if len(rows) == 1:
        labels = [re.search(r"浏览\s*(\d+)\s*秒", n["text"]) for n in nodes
                  if n["cx"] < 800 and 0 < n["cy"]-rows[0]["cy"] < 100]
        durations = {int(m[1]) for m in labels if m}
        if len(durations) == 1:
            return min(max(durations.pop()+2, 5), 60)
    return min(max(fallback, 5), 60)


def search_saved_term(d, deadline):
    guard(deadline)
    current = d.app_current()
    if current["package"] != TB:
        raise RuntimeError("搜索时已离开淘宝，已停止输入")
    if "search" not in current.get("activity", "").lower():
        nodes = scan(d)
        if not any(n["text"] == "搜索发现" or "搜索有福利" in n["text"] for n in nodes):
            raise RuntimeError("未确认搜索页面，已停止输入")
    field = d(className="android.widget.EditText")
    if not field.exists or field.count != 1:
        raise RuntimeError("未唯一定位搜索输入框，已停止输入")
    index = _session.get("search_index", 0)
    keyword = SEARCH_TERMS[index % len(SEARCH_TERMS)]
    LOG.info("[搜索] 输入常用词：%s", keyword)
    field.click()
    guard(deadline)
    d.send_keys(keyword, clear=True)
    guard(deadline)
    d.press("enter")
    _session["search_index"] = index + 1
    time.sleep(3)


def return_from_browse(d, deadline):
    for _ in range(5):
        guard(deadline)
        if d.app_current()["package"] != TB:
            raise RuntimeError("返回时离开淘宝，请检查现场")
        d.press("back")
        time.sleep(3)
        nodes = scan(d)
        if any("做任务赚能量" in n["text"] for n in nodes):
            return nodes
        if any("淘金币双11" in n["text"] for n in nodes) or (
            any(n["text"] == "升级赛车拿钱" for n in nodes)
            and any(n["text"] == "去赚能量" for n in nodes)
        ):
            return enter(d, deadline)
        activity = d.app_current()["activity"].lower()
        if not ("search" in activity or any(
                label in n["text"] for n in nodes
                for label in ("搜索有福利", "系统出了点问题", "系统开小差", "已得400"))):
            raise RuntimeError("返回了未知页面，请检查截图")
    raise RuntimeError("返回次数达到上限，请检查现场")


def quiz(d, deadline, nodes):
    before = task_progress(nodes)
    before_task = task_row_progress(nodes, QUIZ)
    button = task_button(nodes, QUIZ, d.window_size()[0])
    guard(deadline)
    d.click(button["cx"], button["cy"])
    time.sleep(3)
    nodes = scan(d)
    text = "".join(n["text"] for n in nodes)
    answers = [n for n in nodes if re.fullmatch(r"A\s*及时邀请好友助力", n["text"])]
    question = re.sub(r"[\s，,？?。.]", "", text)
    if (d.app_current()["package"] != TB
            or "进入比赛期后想继续追赶对手哪项操作最有效" not in question or len(answers) != 1):
        raise RuntimeError("遇到未验证的考试题目，保留现场，不自动猜答案")
    guard(deadline)
    d.click(answers[0]["cx"], answers[0]["cy"])
    nodes = scan(d)
    buttons = [n for n in nodes if n["text"] == "我选好了"]
    if len(buttons) != 1:
        raise RuntimeError("未唯一定位答题提交按钮")
    guard(deadline)
    d.click(buttons[0]["cx"], buttons[0]["cy"])
    time.sleep(3)
    nodes = scan(d)
    if not any("回答正确" in n["text"] or "已成功领取奖励" in n["text"] for n in nodes):
        raise RuntimeError("未确认考试结果，保留现场")
    return verify_gain(d, return_from_browse(d, deadline), before, before_task, deadline, title=QUIZ)


def browse(d, title, seconds, deadline, nodes):
    if d.app_current()["package"] != TB:
        raise RuntimeError("浏览任务必须从淘宝开始")
    before = task_progress(nodes)
    before_task = task_row_progress(nodes, title)
    seconds = browse_seconds(nodes, title, seconds)
    button = task_button(nodes, title, d.window_size()[0])
    guard(deadline)
    d.click(button["cx"], button["cy"])
    time.sleep(4)
    current = d.app_current()["package"]
    if current != TB:
        guard(deadline)
        d.app_start(TB)
        raise RuntimeError("任务跳转到其他应用，请先分类: " + current)
    if "搜一搜" in title:
        search_saved_term(d, deadline)
    w, h = d.window_size()
    for second in range(min(max(seconds, 5), 60)):
        guard(deadline)
        if second % 6 == 0 and title != "拍立淘逛感兴趣的宝贝":
            d.swipe(w*.5, h*.78, w*.5, h*.4, duration=.5)
        time.sleep(1)
    d.screenshot().save(OUT / "browse.png")
    after_nodes = return_from_browse(d, deadline)
    return verify_gain(d, after_nodes, before, before_task, deadline, title=title)


def run_round(d, nodes, deadline, repeat, seconds):
    """扫描当前页、执行一项、退出重扫，再检查同一屏幕的下一项。"""
    attempted = set()
    found = False
    limit = min(max(repeat, 1), 30)
    for index in range(limit):
        guard(deadline)
        if not any("做任务赚能量" in n["text"] for n in nodes):
            raise RuntimeError("请先进入赚能量任务列表")
        rewards = [n for n in nodes if n["text"] == "立即领取" and n["cy"] < 900]
        for reward in rewards:
            LOG.info("[领取] 领取已解锁的累计任务奖励")
            guard(deadline)
            d.click(reward["cx"], reward["cy"])
            time.sleep(2)
        if rewards:
            nodes = scan(d)
        selected, nodes = next_task(d, nodes, attempted, deadline)
        if selected is None:
            LOG.info("[本轮结束] 当前可见任务均已尝试或不在白名单，已保留任务列表")
            return found, nodes
        found = True
        tier = {"taobao": "淘宝", "alipay": "支付宝", "external": "其他应用"}[task_kind(selected)]
        LOG.info("[任务] %s/%s | %s | %s", index+1, limit, tier, selected)
        attempted.add(selected)
        try:
            if selected in VISIT:
                LOG.info("[跳转] 确认打开目标应用后立即返回核对奖励")
                nodes = visit(d, selected, deadline, nodes)
            elif selected == QUIZ:
                nodes = quiz(d, deadline, nodes)
            else:
                LOG.info("[浏览] 按任务要求浏览约 %s 秒", browse_seconds(nodes, selected, seconds))
                nodes = browse(d, selected, seconds, deadline, nodes)
        except RewardNotConfirmed as error:
            guard(deadline)
            nodes = error.nodes
            if (d.app_current()["package"] != TB
                    or not any("做任务赚能量" in n["text"] for n in nodes)):
                raise
            LOG.info("[未到账] %s：已核对3次，本轮跳过并继续其他任务", selected)
    return found, nodes


def _ensure_local_ocr(url):
    """OCR 服务配置在本机时，启动前确保它真的在跑。

    本机 OCR 是一个独立进程，不会随任务自动启动；忘了开就会在导航到任务页之后
    才发现「OCR 服务请求失败」，白跑一轮。这里在连接手机之前先探一次活，
    发现没起来就尝试拉起（只对本机地址生效，远程服务原样不动）。
    """
    try:
        from urllib.parse import urlparse
        import json as _json
        import subprocess as _subprocess
        import time as _time
        import urllib.request as _urllib

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return
        port = parsed.port or 8888
        root = Path(__file__).resolve().parents[1]
        opener = _urllib.build_opener(_urllib.ProxyHandler({}))

        def _ready():
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    return _json.loads(response.read().decode("utf-8")).get("status") == "ready"
            except Exception:
                return False

        if _ready():
            return

        ocr_python = root / ".venv-ocr" / "Scripts" / "python.exe"
        server = root / "tools" / "ocr_rapid_server.py"
        if not ocr_python.is_file() or not server.is_file():
            LOG.warning("[OCR] 本机识别服务未运行，且找不到 OCR 环境；请先双击「启动OCR服务.cmd」")
            return

        LOG.info("[OCR] 本机识别服务未运行，正在自动启动…")
        log_path = root / ".runtime" / "ocr_service.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        flags = _subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        if sys.platform == "win32":
            flags |= getattr(_subprocess, "DETACHED_PROCESS", 0)
        # 看板后端会注入 PYTHONPATH 指向 workbench 的 site-packages；
        # 若被子进程继承，会和 .venv-ocr 里的 numpy/onnxruntime 二进制冲突
        # （表现为 numpy._core._multiarray_umath 缺失）。这里为 OCR 进程清干净。
        child_env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME"):
            child_env.pop(key, None)
        child_env["PYTHONNOUSERSITE"] = "1"
        with open(log_path, "wb") as sink:
            _subprocess.Popen(
                [str(ocr_python), "-u", str(server), "--host", "127.0.0.1", "--port", str(port)],
                cwd=str(root), stdout=sink, stderr=_subprocess.STDOUT,
                creationflags=flags, close_fds=True, env=child_env,
            )
        for _ in range(90):
            _time.sleep(1)
            if _ready():
                LOG.info("[OCR] 本机识别服务已就绪")
                return
        LOG.warning("[OCR] 90 秒内未就绪，请手动双击「启动OCR服务.cmd」")
    except Exception as error:  # noqa: BLE001 - 预热失败不应阻断主流程
        LOG.warning("[OCR] 自动预热失败：%s（可手动双击「启动OCR服务.cmd」）", error)


def main():
    global _raw_scan, _vision_recognize, _vision_source
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "enter", "scan", "auto", "browse", "tap", "back", "swipe", "text"])
    parser.add_argument("values", nargs="*")
    parser.add_argument("--serial")
    parser.add_argument("--vision", choices=["ocr", "workbuddy"],
                        default=os.environ.get("TAOBAO_VISION_BACKEND", "ocr"),
                        help="页面识别后端；workbuddy 只返回文字与坐标，不决定动作")
    parser.add_argument("--seconds", type=int, default=25)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--rechecks", type=int, help="重新进入复核次数（0至5）；run默认5，auto默认0")
    parser.add_argument("--deadline", help="本次运行的截止时间，需包含时区；默认从启动起计算")
    parser.add_argument("--run-minutes", type=int, default=60, help="本次最长运行分钟数，默认60；--deadline 优先")
    args = parser.parse_args()
    if args.rechecks is not None and not 0 <= args.rechecks <= 5:
        parser.error("--rechecks 必须在0至5之间")
    try:
        deadline = run_deadline(args.deadline, args.run_minutes)
    except ValueError as error:
        parser.error(str(error))
    _raw_scan = args.action == "scan"
    if not _raw_scan:
        path = setup_logging()
        LOG.info("[开始] 淘金币赚能量 | 本次截止 %s", deadline.strftime("%m-%d %H:%M"))
        LOG.info("[日志] 详细记录：%s", path)
    _vision_recognize = None
    _vision_source = "配置的OCR服务"
    if args.vision == "workbuddy":
        from workbuddy_vision import WorkBuddyVision
        _vision_recognize = WorkBuddyVision().recognize
        _vision_source = "WorkBuddy视觉"
    else:
        _ensure_local_ocr(ocr_server_url())
    guard(deadline)
    serial = args.serial
    if not serial:
        devices = u2.adbutils.adb.device_list()
        if len(devices) != 1:
            raise RuntimeError("请用 --serial 明确选择设备")
        serial = devices[0].serial
    LOG.info("[连接] 正在连接手机")
    d = connect_with_retry(serial)
    LOG.info("[连接] 手机已连接 | U2 控制，%s 识别", _vision_source)
    if args.action == "enter":
        enter(d, deadline, restart=True)
        return
    elif args.action in ("auto", "run"):
        parent_rechecks = os.environ.get(RECHECK_ENV) == "1"
        rechecks = 0 if parent_rechecks else (
            args.rechecks if args.rechecks is not None else (5 if args.action == "run" else 0)
        )
        finished_reported = False
        for check in range(rechecks + 1):
            guard(deadline)
            if check:
                LOG.info("[复核] 重新进入淘宝检查剩余任务 %s/%s", check, rechecks)
            nodes = enter(d, deadline, restart=True) if check or args.action == "run" else scan(d)
            result = run_round(d, nodes, deadline, args.repeat, args.seconds)
            found, nodes = result if isinstance(result, tuple) else (result, nodes)
            nodes = close_entry_popups(d, deadline, nodes)
            pending, skipped = pending_summary(nodes, d.window_size()[0])
            LOG.info("[扫描] 可见待办=%s | 约定跳过=%s | 本轮执行=%s",
                     len(pending), len(skipped), found)
            has_pending = has_pending_tasks(nodes, d.window_size()[0])
            LOG.info("[复核判定] 待办=%s | 轮次 %s/%s", has_pending, check, rechecks)
            if recheck_enabled():
                if found:
                    report_task_found()
                report_round_finished()
                finished_reported = True
            if check and not found and not has_pending:
                LOG.info("[结束] 复核未发现新任务，确认本轮任务已处理完")
                return
        if recheck_enabled() and not finished_reported:
            report_round_finished()
        if has_pending:
            LOG.info("[结束] 已达到复核上限，仍有待办任务，已保留任务列表")
        else:
            LOG.info("[结束] 任务列表没有可继续执行的待办")
        return
    elif args.action == "browse":
        title = " ".join(args.values)
        if not allowed_browse(title):
            raise RuntimeError("只自动浏览已确认的免费任务；其他任务先 scan 检查")
        nodes = scan(d)
        for _ in range(min(max(args.repeat, 1), 10)):
            nodes = browse(d, title, args.seconds, deadline, nodes)
        return
    elif args.action == "tap":
        if len(args.values) != 2:
            parser.error("tap 需要已检查截图中的 x y 坐标")
        d.click(*map(int, args.values))
        time.sleep(3)
    elif args.action == "swipe":
        d.swipe(*map(int, args.values), duration=.5)
        time.sleep(2)
    elif args.action == "text":
        d.send_keys(" ".join(args.values))
        time.sleep(2)
    elif args.action == "back":
        d.press("back")
        time.sleep(3)
    scan(d)


def cli():
    try:
        main()
        return 0
    except KeyboardInterrupt:
        LOG.info("[停止] 用户中断运行")
        return 130
    except Exception as error:
        LOG.error("[停止] %s", error)
        LOG.debug("异常详情", exc_info=True)
        return 1
    finally:
        if not _raw_scan:
            log_summary()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(cli())
