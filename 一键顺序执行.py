"""一键顺序执行连通性脚本和一键下滑。"""

import argparse
import signal
from pathlib import Path
from threading import Event

from task_runner import run_script
from task_recheck import RECHECK_ENV, TASK_FOUND, ROUND_FINISHED


SCRIPT_DIR = Path(__file__).resolve().parent
# 使用现有任务选择器的超时预算；连通性导航单独预留 30 分钟。
TASKS = (
    ("连通性脚本.py", 1800),
    ("一键下滑.py", 2100),
)

REMAINING_TASKS = (
    ("主播打卡粉丝礼.py", 2100),
    ("签到赢元宝.py", 2100),
    ("睡觉赚元宝.py", 3600),
    ("点淘鸭钻石任务.py", 2100),
    ("点淘鸭摇一摇.py", 2100),
    ("点淘购物赚钱.py", 2100),
    ("打工得体力.py", 3600),
    ("走路赚元宝.py", 900),
    ("元宝中心小任务.py", 1500),
    ("养优惠.py", 14400),
)


def run_task(filename, timeout, stop, max_rechecks):
    """首次执行后重新启动原脚本复核；每轮保留原脚本的导航、扫描和执行逻辑。"""
    total_elapsed = 0
    success, reason = False, "未执行"
    for recheck in range(max_rechecks + 1):
        if stop.is_set():
            break
        if recheck:
            print(f"\n🔄 {filename}：重新进入界面复核 {recheck}/{max_rechecks}", flush=True)
        feedback = {"found": False, "finished": False}

        def on_line(line):
            if max_rechecks and line.strip() == TASK_FOUND:
                feedback["found"] = True
            elif max_rechecks and line.strip() == ROUND_FINISHED:
                feedback["finished"] = True
            else:
                print(line, flush=True)

        kwargs = {}
        if max_rechecks:
            kwargs["env_overrides"] = {RECHECK_ENV: "1"}
        try:
            success, elapsed, reason = run_script(
                SCRIPT_DIR / filename, cwd=SCRIPT_DIR, timeout=timeout,
                should_stop=stop.is_set, on_line=on_line, **kwargs,
            )
        except Exception as exc:
            success, elapsed, reason = False, 0, f"启动异常：{exc}"
        total_elapsed += elapsed
        print(f"{filename}：{reason}（本轮 {elapsed:.1f}s）", flush=True)
        if stop.is_set() or not max_rechecks:
            break
        if recheck and success and feedback["finished"] and not feedback["found"]:
            reason = f"复核 {recheck}/{max_rechecks} 未发现可执行任务，确认结束"
            print(reason, flush=True)
            break
        if not success or not feedback["finished"]:
            success = False
            print("本轮未正常完成扫描，仍计入复核上限。", flush=True)
        elif feedback["found"]:
            print("本轮发现的任务已执行。", flush=True)
        else:
            print("初次未发现任务，准备重新进入复核。", flush=True)
        if recheck == max_rechecks:
            reason = f"{reason}；已达到 {max_rechecks} 次复核上限，当前轮结束后进入下一项"
            print(reason, flush=True)
    return success, total_elapsed, reason


def execute(stop=None, tasks=None, max_rechecks=0):
    """串行执行一轮；失败/超时继续下一项，停止信号取消后续项。"""
    stop = stop if stop is not None else Event()
    tasks = TASKS if tasks is None else tasks
    missing = [filename for filename, _ in tasks if not (SCRIPT_DIR / filename).is_file()]
    if missing:
        print(f"缺少脚本：{', '.join(missing)}", flush=True)
        return 1

    results = []
    for index, (filename, timeout) in enumerate(tasks, 1):
        if stop.is_set():
            break
        print(f"\n[{index}/{len(tasks)}] 启动 {filename}（超时 {timeout}s）", flush=True)
        success, elapsed, reason = run_task(filename, timeout, stop, max_rechecks)
        results.append((filename, success, reason))
        print(f"{filename}：{reason}（{elapsed:.1f}s）", flush=True)

    print("\n执行汇总：", flush=True)
    for filename, success, reason in results:
        print(f"  {'成功' if success else '失败'} | {filename} | {reason}", flush=True)
    if stop.is_set():
        print("已停止，后续脚本未启动。", flush=True)
        return 130
    return 0 if len(results) == len(tasks) and all(r[1] for r in results) else 1


def main(tasks=None, description=__doc__, max_rechecks=0):
    tasks = TASKS if tasks is None else tasks
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--list", action="store_true", help="仅显示执行顺序，不操作设备")
    args = parser.parse_args()
    if args.list:
        for index, (filename, timeout) in enumerate(tasks, 1):
            print(f"{index}. {filename}（{timeout}s）")
        return 0

    stop = Event()

    def request_stop(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)
    return execute(stop, tasks, max_rechecks=max_rechecks)


if __name__ == "__main__":
    raise SystemExit(main())
