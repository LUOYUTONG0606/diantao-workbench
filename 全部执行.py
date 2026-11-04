"""
全部执行调度脚本
从 task_config.json 读取勾选的任务，按顺序逐个执行
支持：
  - 实时日志输出（带时间戳和彩色标识）
  - 读取 task_config.json（与 GUI 选择器共用配置）
  - 超时自动终止
  - 停止标记（Ctrl+C 优雅退出）
  - 运行日志保存到 logs/ 目录
  - 完成后汇总报告
"""
import sys
import os
import time
import json
import signal
from datetime import datetime

from task_rescan import build_rescan_rounds
from task_runner import run_script
from task_catalog import is_task_file

# ========================================================
# 全局变量
# ========================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "task_config.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
stop_flag = False


def signal_handler(sig, frame):
    """处理 Ctrl+C 优雅退出"""
    global stop_flag
    if not stop_flag:
        print(f"\n  ⚠️  收到停止信号，正在优雅退出...（再按一次强制退出）")
        stop_flag = True
    else:
        print(f"\n  ⚠️  强制退出")
        sys.exit(1)


signal.signal(signal.SIGINT, signal_handler)


# ========================================================
# 日志工具
# ========================================================
def now():
    return datetime.now().strftime("%H:%M:%S")


def log(msg, tag=None):
    """带时间戳的日志输出"""
    ts = now()
    tag_map = {
        "ok":    " ✅",
        "fail":  " ❌",
        "warn":  " ⚠️",
        "info":  " ℹ️",
        "step":  " ▶",
        "title": "",
        "time":  "",
    }
    icon = tag_map.get(tag, "")
    print(f"  [{ts}]{icon} {msg}")


# ========================================================
# 任务配置读取
# ========================================================
def load_task_config():
    """从 task_config.json 读取勾选的任务"""
    if not os.path.exists(CONFIG_FILE):
        log(f"配置文件不存在: {CONFIG_FILE}", "fail")
        return []

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            all_tasks = json.load(f)
    except Exception as e:
        log(f"读取配置失败: {e}", "fail")
        return []

    # 只取勾选的任务
    checked = [t for t in all_tasks if t.get("checked", False) and is_task_file(t.get("file", ""))]
    if not checked:
        log("没有勾选的任务，请先在 任务选择器.py 中勾选并保存", "warn")
    return checked


# ========================================================
# 单个任务执行
# ========================================================
def run_task(task_info):
    """执行单个脚本任务，返回 (name, success, elapsed)"""
    global stop_flag

    name = task_info["name"]
    filepath = os.path.join(SCRIPT_DIR, task_info["file"])
    timeout = task_info.get("timeout", 600)

    # 检查文件是否存在
    if not os.path.exists(filepath):
        log(f"文件不存在: {filepath}", "fail")
        return name, False, 0

    # 检查是否可能引用了外部路径（如 ../coin11-tb-main/）
    alt_path = os.path.join(SCRIPT_DIR, os.path.normpath(task_info["file"]))
    if not os.path.exists(filepath) and os.path.exists(alt_path):
        filepath = alt_path

    print(f"\n  {'='*56}")
    print(f"  [{now()}] 开始: {name}")
    print(f"  文件: {task_info['file']}")
    print(f"  超时: {timeout}s ({timeout//60}分{timeout%60}秒)")
    print(f"  {'='*56}")

    start = time.time()

    try:
        success, elapsed, reason = run_script(
            filepath, cwd=SCRIPT_DIR, timeout=timeout,
            should_stop=lambda: stop_flag,
            on_line=lambda line: print(f"     {line}") if line else None,
        )
        log(f"{reason} (耗时 {int(elapsed)}s)", "ok" if success else "fail")
        return name, success, int(elapsed)

    except FileNotFoundError:
        log(f"无法找到 Python 解释器或脚本文件", "fail")
        return name, False, 0
    except Exception as e:
        elapsed = int(time.time() - start)
        log(f"异常: {e}", "fail")
        return name, False, elapsed


# ========================================================
# 保存运行日志
# ========================================================
def save_run_log(results, total_elapsed):
    """将本次运行记录保存到日志文件"""
    os.makedirs(LOG_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H%M%S")
    log_file = os.path.join(LOG_DIR, f"run_{date_str}_{time_str}.log")

    ok_count = sum(1 for _, s, _ in results if s)

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"点淘全部执行运行日志\n")
        f.write(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"总耗时: {total_elapsed//60}分{total_elapsed%60}秒\n")
        f.write(f"结果: 成功 {ok_count}/{len(results)}\n")
        f.write(f"{'='*60}\n\n")
        for i, (name, success, elapsed) in enumerate(results, 1):
            status = "✅ 成功" if success else "❌ 失败"
            f.write(f"  {i}. {name}: {status} ({elapsed}s)\n")

    log(f"运行日志已保存: {log_file}", "info")
    return log_file


# ========================================================
# 主流程
# ========================================================
def main():
    global stop_flag

    print(f"\n{'='*60}")
    print(f"  点淘全部执行调度器")
    print(f"  {'='*56}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  配置: task_config.json")
    print(f"  模式: 按顺序逐个执行")
    print(f"  {'='*60}")

    # 读取任务配置
    tasks = load_task_config()
    if not tasks:
        return

    # 显示任务列表
    print(f"\n  勾选的任务 ({len(tasks)} 个):")
    for i, t in enumerate(tasks, 1):
        timeout_min = t.get("timeout", 600) // 60
        timeout_sec = t.get("timeout", 600) % 60
        print(f"    {i:2d}. {t['name']}")
        print(f"       文件: {t['file']}")
        print(f"       超时: {t.get('timeout', 600)}s ({timeout_min}分{timeout_sec}秒)")
    print(f"\n  按 Ctrl+C 可随时停止\n")

    # 等待 3 秒让用户确认
    for i in range(3, 0, -1):
        print(f"  ⏳ {i} 秒后开始执行...", end="\r")
        time.sleep(1)
    print("  " + " " * 30)

    # 开始逐个执行
    results = []
    total_start = time.time()

    for idx, task in enumerate(tasks, 1):
        if stop_flag:
            log("用户中断，停止执行剩余任务", "warn")
            break

        print(f"\n  ═══════════════════════════════════")
        log(f"任务 {idx}/{len(tasks)}", "step")
        print(f"  ═══════════════════════════════════")

        time.sleep(2)  # 任务间短暂停顿
        if stop_flag:
            break

        name, success, elapsed = run_task(task)
        results.append((name, success, elapsed))

    for round_number, rescan_tasks in enumerate(build_rescan_rounds(tasks), 1):
        if stop_flag:
            log("用户中断，停止复扫", "warn")
            break

        print(f"\n  ═══════════════════════════════════")
        log(f"第 {round_number}/2 次重启复扫", "step")
        print("  点淘将由任务脚本从首页重新启动，仅扫描指定任务。")
        print(f"  ═══════════════════════════════════")

        for idx, task in enumerate(rescan_tasks, 1):
            if stop_flag:
                log("用户中断，停止本轮复扫", "warn")
                break

            time.sleep(2)
            name, success, elapsed = run_task(task)
            results.append((f"复扫{round_number}-{idx}: {name}", success, elapsed))

    # 汇总报告
    total_elapsed = int(time.time() - total_start)
    ok_count = sum(1 for _, s, _ in results if s)

    print(f"\n{'='*60}")
    print(f"  ╔════════════════════════════════════╗")
    print(f"  ║           执行完成                 ║")
    print(f"  ╚════════════════════════════════════╝")
    print(f"  总耗时: {total_elapsed//60}分{total_elapsed%60}秒")
    print(f"  {'='*56}")
    print(f"  结果汇总:")
    for i, (name, success, elapsed) in enumerate(results, 1):
        status_icon = "✅" if success else "❌"
        print(f"    {i:2d}. {status_icon} {name:<20s} ({elapsed}s)")
    print(f"  {'─'*56}")
    print(f"    成功 {ok_count}/{len(results)}")
    print(f"{'='*60}")

    # 保存日志
    save_run_log(results, total_elapsed)
    print()


if __name__ == "__main__":
    main()
