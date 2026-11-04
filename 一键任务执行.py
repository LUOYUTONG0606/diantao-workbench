"""一键顺序执行主播、签到、睡觉、钻石、摇一摇、购物、打工、走路、元宝中心小任务和养优惠。"""

from 一键顺序执行 import REMAINING_TASKS, main

MAX_RECHECKS = 5

if __name__ == "__main__":
    raise SystemExit(main(tasks=REMAINING_TASKS, description=__doc__, max_rechecks=MAX_RECHECKS))
