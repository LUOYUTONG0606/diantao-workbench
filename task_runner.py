"""CLI 和任务选择器共用的脚本执行器，不依赖设备或界面。"""

import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path


def run_script(
    filepath: str | Path,
    *,
    cwd: str | Path,
    timeout: float | None,
    should_stop: Callable[[], bool],
    on_line: Callable[[str], None],
    env_overrides: dict[str, str] | None = None,
    command: list[str] | None = None,
) -> tuple[bool, float, str]:
    """受管会话内记录子任务边界，重放时跳过已结束子任务并恢复复核反馈。"""
    from task_checkpoint import script_checkpoint
    from task_recheck import TASK_FOUND, ROUND_FINISHED

    if should_stop():
        return False, 0.0, "用户停止"
    actual_command = command or [sys.executable, "-u", str(filepath)]
    checkpoint = script_checkpoint(filepath, actual_command)
    if checkpoint.done:
        on_line(f"[续跑] 跳过已完成：{Path(filepath).name}")
        for line in checkpoint.cached["feedback"]:
            on_line(line)
        return tuple(checkpoint.cached["result"])
    checkpoint.begin()
    feedback = []

    def forward(line):
        if line.strip() in (TASK_FOUND, ROUND_FINISHED):
            feedback.append(line)
        on_line(line)

    overrides = dict(env_overrides or {})
    overrides.update(checkpoint.child_env())
    try:
        result = _run_script(
            filepath, cwd=cwd, timeout=timeout, should_stop=should_stop,
            on_line=forward, env_overrides=overrides, command=actual_command,
        )
    except Exception as exc:
        checkpoint.finish((False, 0.0, f"启动异常：{exc}"), feedback)
        raise
    if result[2] != "用户停止":
        checkpoint.finish(result, feedback)
    return result


def _run_script(
    filepath: str | Path,
    *,
    cwd: str | Path,
    timeout: float | None,
    should_stop: Callable[[], bool],
    on_line: Callable[[str], None],
    env_overrides: dict[str, str] | None = None,
    command: list[str] | None = None,
) -> tuple[bool, float, str]:
    """返回成功、耗时和状态；超时/取消时终止并回收直接子进程。

    timeout 传 None 表示不设置超时，只等待脚本自行结束或用户停止。
    默认用当前解释器执行 filepath；传入 command 时按显式命令启动（如带子命令的入口），
    此时 filepath 只用于校验存在性和展示。
    """
    start = time.monotonic()
    if timeout is not None and timeout <= 0:
        raise ValueError("任务超时必须大于 0，或传 None 表示不设超时")
    if should_stop():
        return False, 0.0, "用户停止"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.Popen(
        command or [sys.executable, "-u", str(filepath)], cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", env=env, bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert proc.stdout is not None
    output = proc.stdout
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            for line in output:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    eof = False
    reason = ""
    try:
        while True:
            elapsed = time.monotonic() - start
            if should_stop():
                reason = "用户停止"
                break
            if timeout is not None and elapsed >= timeout:
                reason = f"超时 ({timeout:g}s)"
                break
            if eof and proc.poll() is not None:
                break
            # 不设超时时用固定短轮询，既能及时响应停止信号，也不会提前结束。
            wait = 0.1 if timeout is None else min(0.1, timeout - elapsed)
            try:
                line = lines.get(timeout=wait)
            except queue.Empty:
                continue
            if line is None:
                eof = True
            else:
                on_line(line)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        # 后代进程可能继承输出管道，不能让它阻塞主线程的超时处理。
        reader.join(timeout=0.5)
        if not reader.is_alive():
            output.close()

    success = not reason and proc.returncode == 0
    if not reason:
        reason = "成功" if success else f"错误码 {proc.returncode}"
    return success, time.monotonic() - start, reason
