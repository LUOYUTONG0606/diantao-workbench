"""一键执行点淘全流程：连通性、一键下滑、日常任务与淘金币双11赚能量。

顺序：连通性 → 一键下滑 → 主播 → 签到 → 睡觉 → 钻石 → 摇一摇 → 购物 → 打工
      → 走路 → 元宝中心小任务 → 养优惠 → 淘金币双11赚能量。
本脚本独立运行，不修改或依赖 一键顺序执行.py / 一键任务执行.py 的执行结果。
"""

import argparse
import signal
import sys
from pathlib import Path
from threading import Event
from typing import NamedTuple

from task_runner import run_script
from task_recheck import RECHECK_ENV, TASK_FOUND, ROUND_FINISHED


SCRIPT_DIR = Path(__file__).resolve().parent
ENERGY_SCRIPT = "tools/taobao_energy.py"


class Step(NamedTuple):
    """一个执行步骤：脚本、超时、附加参数与复核轮数。

    timeout 为 None 表示不设置超时，一直等到脚本自行结束或用户按 Ctrl+C 停止。
    """

    filename: str
    timeout: int | None
    args: tuple = ()
    rechecks: int = 0


# 前两步与 一键顺序执行.py 一致：这两个脚本不输出复核标记，只执行一轮。
SEQUENCE_TASKS = (
    Step("连通性脚本.py", None),
    Step("一键下滑.py", None),
)

# 与 一键任务执行.py 的 REMAINING_TASKS 一致，每项最多复核 5 轮。
REMAINING_TASKS = (
    Step("主播打卡粉丝礼.py", None, rechecks=5),
    Step("签到赢元宝.py", None, rechecks=5),
    Step("睡觉赚元宝.py", None, rechecks=5),
    Step("点淘鸭钻石任务.py", None, rechecks=5),
    Step("点淘鸭摇一摇.py", None, rechecks=5),
    Step("点淘购物赚钱.py", None, rechecks=5),
    Step("打工得体力.py", None, rechecks=5),
    Step("走路赚元宝.py", None, rechecks=5),
    Step("元宝中心小任务.py", None, rechecks=5),
    Step("养优惠.py", None, rechecks=5),
)

ENERGY_TASK = Step(
    ENERGY_SCRIPT,
    None,
    args=("run", "--repeat", "30", "--rechecks", "0"),
    rechecks=5,
)
ALL_TASKS = SEQUENCE_TASKS + REMAINING_TASKS + (ENERGY_TASK,)


def build_command(step):
    """点淘任务使用当前解释器。"""
    script = str(SCRIPT_DIR / step.filename)
    return [sys.executable, "-u", script, *step.args]


def run_task(step, stop):
    """首次执行后重新启动原脚本复核；每轮保留原脚本的导航、扫描和执行逻辑。"""
    total_elapsed = 0
    success, reason = False, "未执行"
    for recheck in range(step.rechecks + 1):
        if stop.is_set():
            break
        if recheck:
            print(f"\n🔄 {step.filename}：重新进入界面复核 {recheck}/{step.rechecks}", flush=True)
        feedback = {"found": False, "finished": False}

        def on_line(line):
            if step.rechecks and line.strip() == TASK_FOUND:
                feedback["found"] = True
            elif step.rechecks and line.strip() == ROUND_FINISHED:
                feedback["finished"] = True
            else:
                print(line, flush=True)

        kwargs = {}
        if step.rechecks:
            kwargs["env_overrides"] = {RECHECK_ENV: "1"}
        try:
            command = build_command(step)
            success, elapsed, reason = run_script(
                SCRIPT_DIR / step.filename, cwd=SCRIPT_DIR, timeout=step.timeout,
                should_stop=stop.is_set, on_line=on_line, command=command, **kwargs,
            )
        except Exception as exc:
            success, elapsed, reason = False, 0, f"启动异常：{exc}"
        total_elapsed += elapsed
        print(f"{step.filename}：{reason}（本轮 {elapsed:.1f}s）", flush=True)
        if stop.is_set() or not step.rechecks:
            break
        if recheck and success and feedback["finished"] and not feedback["found"]:
            reason = f"复核 {recheck}/{step.rechecks} 未发现可执行任务，确认结束"
            print(reason, flush=True)
            break
        if not success or not feedback["finished"]:
            success = False
            print("本轮未正常完成扫描，仍计入复核上限。", flush=True)
        elif feedback["found"]:
            print("本轮发现的任务已执行。", flush=True)
        else:
            print("初次未发现任务，准备重新进入复核。", flush=True)
        if recheck == step.rechecks:
            reason = f"{reason}；已达到 {step.rechecks} 次复核上限，当前轮结束后进入下一项"
            print(reason, flush=True)
    return success, total_elapsed, reason


def _timeout_label(timeout):
    """超时展示文案：None 表示不限制。"""
    return "不设超时" if timeout is None else f"超时 {timeout}s"


def execute(stop=None, tasks=None):
    """串行执行一轮；失败/超时继续下一项，停止信号取消后续项。"""
    stop = stop if stop is not None else Event()
    tasks = ALL_TASKS if tasks is None else tasks
    missing = [step.filename for step in tasks if not (SCRIPT_DIR / step.filename).is_file()]
    if missing:
        print(f"缺少脚本：{', '.join(missing)}", flush=True)
        return 1

    results = []
    for index, step in enumerate(tasks, 1):
        if stop.is_set():
            break
        print(f"\n[{index}/{len(tasks)}] 启动 {step.filename}（{_timeout_label(step.timeout)}）", flush=True)
        success, elapsed, reason = run_task(step, stop)
        results.append((step.filename, success, reason))
        print(f"{step.filename}：{reason}（{elapsed:.1f}s）", flush=True)

    print("\n执行汇总：", flush=True)
    for filename, success, reason in results:
        print(f"  {'成功' if success else '失败'} | {filename} | {reason}", flush=True)
    if stop.is_set():
        print("已停止，后续脚本未启动。", flush=True)
        return 130
    return 0 if len(results) == len(tasks) and all(r[1] for r in results) else 1


def main(tasks=None, description=__doc__):
    tasks = ALL_TASKS if tasks is None else tasks
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--list", action="store_true", help="仅显示执行顺序，不操作设备")
    args = parser.parse_args()
    if args.list:
        for index, step in enumerate(tasks, 1):
            print(f"{index}. {step.filename}（{_timeout_label(step.timeout)}）")
        return 0

    stop = Event()

    def request_stop(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)
    return execute(stop, tasks)


if __name__ == "__main__":
    raise SystemExit(main())
