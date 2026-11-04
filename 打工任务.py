"""打工任务：厨师两小时循环，并按独立倒计时领取左侧体力。"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import time
from phone_control import guardian_turn
from guard_timer import GuardTimer


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / ".runtime" / "dagong_guard" / "state.json"
CHEF_SECONDS = 2 * 60 * 60
MAX_ENERGY_SECONDS = 3 * 60 * 60
RECHECK_SECONDS = 15


class GuardBlocked(RuntimeError):
    """页面需要人工确认时保留现场，禁止猜测点击。"""


class ScanUnavailable(RuntimeError):
    """截图或 OCR 暂时失败，可稍后重试。"""


def text(block):
    return re.sub(r"[\s\u200b-\u200d\ufeff]+", "",
                  str(block.get("text", ""))).replace("廚", "厨")


def region(blocks, size, left, top, right, bottom):
    width, height = size
    return [b for b in blocks
            if left * width <= b.get("cx", -1) <= right * width
            and top * height <= b.get("cy", -1) <= bottom * height]


def find(blocks, label):
    return next((b for b in blocks if text(b) == label), None)


def countdown(blocks, maximum):
    for block in blocks:
        value = text(block).replace("：", ":")
        match = re.fullmatch(r"(\d{1,2}):([0-5]\d)(?::([0-5]\d))?", value)
        if match:
            first, second, third = match.groups()
            seconds = (int(first) * 3600 + int(second) * 60 + int(third)
                       if third is not None else int(first) * 60 + int(second))
            if seconds <= maximum:
                return seconds
    return None


@dataclass
class Page:
    kind: str
    work_seconds: int | None = None
    energy_seconds: int | None = None
    claim: dict | None = None
    work: dict | None = None
    energy: dict | None = None
    chef: dict | None = None
    start: dict | None = None
    complete: dict | None = None
    dismiss: dict | None = None


def inspect_page(blocks, size):
    """用区域区分左侧体力、中间打工和装饰时钟，不把商品文案当成操作。"""
    labels = [text(b) for b in blocks]
    if "定时奖励" in labels and any(re.fullmatch(r"获得\d+体力", t) for t in labels):
        dismiss = find(region(blocks, size, .15, .5, .85, .75), "放弃获得额外奖励")
        if dismiss:
            return Page("energy_reward", dismiss=dismiss)
        return Page("unknown")
    if "选工作赚元宝" in labels:
        chef = find(blocks, "厨师")
        start = find(blocks, "开始打工")
        if chef and start:
            column = [b for b in blocks
                      if abs(b.get("cx", 0) - chef["cx"]) < size[0] * .20
                      and chef["cy"] < b.get("cy", 0) < start["cy"]]
            details = "".join(text(b) for b in column)
            if "120分钟" in details and "888" in details:
                return Page("chooser", chef=chef, start=start)
        return Page("unknown")
    if "恭喜获得元宝" in labels:
        complete = find(region(blocks, size, .15, .35, .85, .75), "去完成")
        if complete and any(re.fullmatch(r"888(?:元宝)?", t) for t in labels):
            return Page("reward", complete=complete)
        return Page("unknown")

    middle = region(blocks, size, .23, .52, .79, .71)
    left = region(blocks, size, 0, .58, .23, .71)
    right = region(blocks, size, .79, .58, 1, .71)
    work_seconds = countdown(middle, CHEF_SECONDS)
    claim = next((b for b in middle
                  if re.fullmatch(r"领取888(?:元宝|奖励)", text(b))), None)
    work = find(middle, "去打工赚钱")
    if find(right, "得体力") and (work_seconds is not None or claim or work):
        return Page("home", work_seconds=work_seconds,
                    energy_seconds=countdown(left, MAX_ENERGY_SECONDS),
                    claim=claim, work=work, energy=find(left, "领体力"))
    return Page("unknown")


@dataclass
class Schedule:
    work_at: float
    energy_at: float

    @classmethod
    def from_page(cls, page, observed_at):
        if page.kind != "home" or page.work_seconds is None:
            raise GuardBlocked("未确认打工倒计时，不能安排下一轮")
        # OCR/页面动画可能暂时遮住体力计时；短暂复查，不假设已领或等待三小时。
        energy_wait = (page.energy_seconds if page.energy_seconds is not None
                       else RECHECK_SECONDS)
        return cls(observed_at + max(1, page.work_seconds) + 1,
                   observed_at + max(1, energy_wait) + 1)

    def next_at(self):
        return min(self.work_at, self.energy_at)

    def save(self, path=STATE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)


class Phone:
    """设备和现有 OCR/导航/媒体执行器只在真正运行时导入。"""

    def __init__(self, device):
        self.device = device
        self.size = device.window_size()

    def scan(self):
        from utils import get_ocr_text_blocks
        try:
            self.device.shell("input keyevent KEYCODE_WAKEUP")
            time.sleep(1)
            policy = str(self.device.shell("dumpsys window policy").output)
            if (re.search(r"\bmIsShowing=true\b", policy)
                    and re.search(r"\bsecure=true\b", policy)):
                raise ScanUnavailable("手机已锁屏，请解锁后点击立即校准")
            self.device.shell("wm dismiss-keyguard")
            blocks = get_ocr_text_blocks(self.device, retries=0)
        except ScanUnavailable:
            raise
        except Exception as exc:
            raise ScanUnavailable(f"OCR 暂不可用：{type(exc).__name__}") from exc
        if not blocks:
            raise ScanUnavailable("OCR 返回空结果")
        return blocks

    def read(self):
        return inspect_page(self.scan(), self.size)

    def click(self, block):
        cx, cy = int(block.get("cx", -1)), int(block.get("cy", -1))
        if not (0 < cx < self.size[0] and 0 < cy < self.size[1]):
            raise GuardBlocked("OCR 操作坐标无效")
        print(f"[点击] {text(block)} @({cx},{cy})", flush=True)
        self.device.click(cx, cy)
        time.sleep(1)

    def enter(self):
        page = self.read()
        if page.kind != "unknown":
            return page
        from u2_scanner import navigate_to_dagong_page
        if not navigate_to_dagong_page(self.device):
            # 冷启动可能弹出更新提示，遮住元宝中心入口；只处理已确认的提示。
            blocks = self.scan()
            if find(blocks, "快来更新点淘") and find(blocks, "立即更新"):
                self.device.press("back")
                time.sleep(2)
                blocks = self.scan()
            entry = find(blocks, "元宝中心") or find(blocks, "领元宝")
            if entry is None:
                raise ScanUnavailable("暂时无法导航到打工页")
            self.click(entry)
            time.sleep(3)
            from yuanbao_popup import handle_yuanbao_live_popups
            if not handle_yuanbao_live_popups(self.device):
                raise ScanUnavailable("元宝中心弹窗未处理完成")
            entry = find(self.scan(), "打工赚元宝")
            if entry is None:
                raise ScanUnavailable("元宝中心未识别到打工入口")
            self.click(entry)
            time.sleep(3)
        page = self.read()
        if page.kind == "unknown":
            raise ScanUnavailable("导航后未确认打工主页，稍后重新识别")
        return page

    def finish_reward(self, page):
        """奖励弹窗中的任务必须执行，不能关闭弹窗就算完成。"""
        from u2_scanner import (
            classify_media_page, execute_live_task, execute_video_task,
            execute_browse_task,
        )
        self.click(page.complete)
        time.sleep(2)
        blocks = self.scan()
        if inspect_page(blocks, self.size).kind == "home":
            return
        content = "".join(text(b) for b in blocks)
        media = classify_media_page(blocks)
        match = re.search(r"(\d+)秒(?:后完成|后领取|后可领取)", content)
        duration = int(match[1]) if match else 120
        if not 0 < duration <= 180:
            raise GuardBlocked("奖励任务时长超出已支持范围，保留现场")
        if media == "live":
            execute_live_task(self.device, "打工奖励直播任务", 0, 0, duration=duration + 5)
        elif media == "video":
            if not execute_video_task(self.device, "打工奖励视频任务", 0, 0,
                                      duration=duration, extra_time_seconds=5):
                raise GuardBlocked("奖励视频任务未执行完成")
        elif re.search(r"(?:滑动|浏览)[^。]{0,10}\d+秒", content):
            seconds = int(re.search(r"(?:滑动|浏览)[^。]{0,10}?(\d+)秒", content)[1])
            if not 0 < seconds <= 180:
                raise GuardBlocked("奖励浏览任务时长超出已支持范围")
            if not execute_browse_task(self.device, min_duration=seconds,
                                       max_wait=seconds + 40):
                raise GuardBlocked("奖励浏览任务未执行完成")
        else:
            raise GuardBlocked("奖励任务落地页尚未支持，保留现场供检查")
        for attempt in range(4):
            if self.read().kind == "home":
                print("[奖励] 已执行弹窗任务并确认返回打工主页", flush=True)
                return
            if attempt < 3:
                self.device.press("back")
                time.sleep(2)
        raise GuardBlocked("奖励任务后未确认返回打工主页")


def visit(phone, now=time.time):
    """处理一次到期事件；所有点击都使用本轮 OCR，完成后重新确认状态。"""
    page = phone.enter()
    energy_clicked = False
    reward_clicked = False
    reward_completed = False
    energy_bonus_dismissed = False
    started = False
    for _ in range(12):
        if page.kind == "energy_reward":
            if energy_bonus_dismissed:
                raise ScanUnavailable("体力奖励弹窗尚未消失，稍后复查")
            phone.click(page.dismiss)
            energy_bonus_dismissed = True
            print("[体力] 已领取基础体力，关闭可选的额外直播奖励", flush=True)
        elif page.kind == "reward":
            if reward_completed:
                raise GuardBlocked("奖励任务后仍出现相同弹窗，停止重复执行")
            phone.finish_reward(page)
            reward_completed = True
        elif page.kind == "chooser":
            if started:
                raise GuardBlocked("开工后仍停留选工作面板，未确认厨师倒计时")
            phone.click(page.chef)
            selected = phone.read()
            if selected.kind != "chooser":
                raise GuardBlocked("选择厨师后页面异常，未点击开始打工")
            phone.click(selected.start)
            started = True
        elif page.kind == "home":
            if started and (page.work_seconds is None or not 6900 <= page.work_seconds <= CHEF_SECONDS):
                raise GuardBlocked("未确认厨师两小时倒计时，不重复开工")
            if page.energy and not energy_clicked:
                phone.click(page.energy)
                energy_clicked = True
            elif page.claim:
                if reward_clicked or reward_completed:
                    raise ScanUnavailable("领取后入口未变化，稍后重扫确认")
                phone.click(page.claim)
                reward_clicked = True
            elif page.work:
                phone.click(page.work)
            elif page.work_seconds is not None:
                if energy_clicked and page.energy:
                    raise ScanUnavailable("领取体力后入口未变化，稍后重扫确认")
                schedule = Schedule.from_page(page, now())
                print("[打工] 厨师已开工" if started else "[打工] 已读取正在进行的倒计时",
                      flush=True)
                print(f"[定时] 打工复查 {format_time(schedule.work_at)}；"
                      f"体力复查 {format_time(schedule.energy_at)}", flush=True)
                return schedule
            else:
                raise GuardBlocked("未识别打工状态")
        else:
            raise GuardBlocked("出现未知页面，保留现场")
        page = phone.read()
    raise GuardBlocked("页面状态转换次数过多，停止本次操作")


def format_time(timestamp):
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def guard(phone, *, once=False, state_path=STATE_PATH, now=time.time, sleep=time.sleep,
          turn=guardian_turn):
    timer = GuardTimer(Path(state_path).parent, now=now)
    timer.publish("starting", message="正在读取当前打工和体力状态")
    try:
        return _guard(phone, once=once, state_path=state_path, now=now, sleep=sleep,
                      turn=turn, timer=timer)
    except GuardBlocked as exc:
        timer.publish("blocked", message=str(exc))
        raise
    finally:
        if timer.value["phase"] != "blocked":
            timer.publish("stopped", message="守护已停止")


def _guard(phone, *, once, state_path, now, sleep, turn, timer):
    failures = 0
    while True:
        try:
            timer.consume_refresh()
            timer.publish("checking", message="等待手机使用权，重新识别实际剩余时间")
            with turn():
                timer.publish("acting", message="正在检查打工、领取奖励或重新开工")
                active_phone = phone() if callable(phone) else phone
                try:
                    schedule = visit(active_phone, now=now)
                finally:
                    watcher = getattr(getattr(active_phone, "device", None),
                                      "_global_watcher_ctx", None)
                    if watcher is not None:
                        watcher.stop()
        except ScanUnavailable as exc:
            failures += 1
            delay = min(300, 15 * 2 ** min(failures - 1, 5))
            print(f"[重试] {exc}，{delay}秒后复查", flush=True)
            timer.publish("retrying", message=f"{exc}；{delay}秒后重试")
            if once:
                raise
            wait_until(now() + delay, now=now, sleep=sleep,
                       wake=timer.refresh_pending)
            continue
        failures = 0
        schedule.save(state_path)
        timer.publish("waiting", schedule=schedule, message="后台计时中，到期接管后继续原任务")
        if once:
            return schedule
        delay = max(0, schedule.next_at() - now())
        print(f"[守护] 等待{delay:.0f}秒；不进行点击或滑动，可随时停止", flush=True)
        wait_until(schedule.next_at(), now=now, sleep=sleep,
                   wake=timer.refresh_pending)


def wait_until(deadline, *, now=time.time, sleep=time.sleep, wake=None):
    while (remaining := deadline - now()) > 0:
        if wake is not None and wake():
            return
        sleep(min(remaining, 1 if wake is not None else 30))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="仅处理一次并输出下一次检查时间")
    args = parser.parse_args()
    from u2_scanner import connect_device
    print("[打工任务] 厨师两小时循环 + 左侧领体力独立定时；手动停止结束守护", flush=True)
    guard(lambda: Phone(connect_device()), once=args.once)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[停止] 打工任务守护已停止", flush=True)
    except (GuardBlocked, ScanUnavailable) as exc:
        print(f"[停止] {exc}", flush=True)
        raise SystemExit(1)
