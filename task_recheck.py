"""一键任务执行的子脚本反馈；单独运行任务时不输出反馈。"""

import os


RECHECK_ENV = "DIANTAO_ONE_CLICK_RECHECK"
TASK_FOUND = "[DIANTAO_RECHECK] task_found"
ROUND_FINISHED = "[DIANTAO_RECHECK] round_finished"


def recheck_enabled():
    return os.environ.get(RECHECK_ENV) == "1"


def report_task_found():
    if recheck_enabled():
        print(TASK_FOUND, flush=True)


def report_round_finished():
    if recheck_enabled():
        print(ROUND_FINISHED, flush=True)
