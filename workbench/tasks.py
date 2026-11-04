"""Explicit dashboard entries; importing the catalog never connects to a phone."""
from task_catalog import TASK_FILES
from 一键全部执行 import ALL_TASKS


def entry(name, script, description, *, args=(), requires_ocr=True):
    return {
        "name": name, "script": script, "description": description,
        "args": list(args), "requires_ocr": requires_ocr,
    }


TASKS = {
    "diantao-all-in-one": entry(
        "点淘一键全部执行", "一键全部执行.py",
        "连通性 → 下滑 → 日常任务 → 养优惠；串行执行，各任务沿用原有复核规则。",
    ),
    "diantao-one-click": entry(
        "点淘一键顺序执行", "一键顺序执行.py", "先检查连通性，再执行六轮下滑。",
    ),
    "diantao-one-click-tasks": entry(
        "点淘一键任务执行", "一键任务执行.py", "按原顺序执行十项日常任务，各最多复核五轮。",
    ),
    "taobao-double11-energy": entry(
        "淘金币双11能量", "tools/taobao_energy.py",
        "淘宝 → 支付宝 → 其他应用；沿用任务排除项和返回复查规则。",
        args=("run", "--repeat", "30"),
    ),
    "taobao-double11-workbuddy": entry(
        "淘金币双11能量 · WorkBuddy", "tools/taobao_energy.py",
        "使用本机 WorkBuddy 识别截图 JSON，沿用赚能量任务逻辑；需已登录 WorkBuddy。",
        args=("run", "--repeat", "30", "--vision", "workbuddy"), requires_ocr=False,
    ),
}

# Explicit mapping keeps debug and calibration scripts out of the dashboard.
SCRIPT_IDS = {
    "一键下滑.py": "diantao-swipe",
    "一键下滑_新.py": "diantao-swipe-new",
    "主播打卡粉丝礼.py": "diantao-anchor",
    "元宝中心小任务.py": "diantao-yuanbao",
    "合成百元大红包.py": "diantao-merge",
    "打工加速.py": "diantao-work-speed",
    "打工得体力.py": "diantao-work",
    "淘金币任务.py": "diantao-coins",
    "点淘购物赚钱.py": "diantao-shopping",
    "点淘鸭摇一摇.py": "diantao-duck-shake",
    "点淘鸭摇一摇额外元宝.py": "diantao-duck-extra",
    "点淘鸭钻石任务.py": "diantao-duck-diamond",
    "睡觉赚元宝.py": "diantao-sleep",
    "签到赢元宝.py": "diantao-signin",
    "走路赚元宝.py": "diantao-walk",
    "赚钱卡.py": "diantao-money-card",
    "养优惠.py": "diantao-coupon",
}
for filename in sorted(TASK_FILES & SCRIPT_IDS.keys()):
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
    TASKS[SCRIPT_IDS[filename]] = entry(
        "点淘养优惠" if filename == "养优惠.py" else name,
        filename,
        "沿用原脚本的导航、识别和任务规则；手动启动，可随时停止。",
    )

TASKS["diantao-connectivity"] = entry(
    "点淘连通性检查", "连通性脚本.py", "连接设备并进入点淘，沿用原连通性脚本。",
)
TASKS["diantao-work-guard"] = entry(
    "打工任务", "打工任务.py",
    "持续守护：领取到期元宝并完成奖励任务，厨师每两小时开工，左侧领体力按独立倒计时领取；手动停止。",
)

# Keep the common entries first, then follow the runner's actual sequence.
# Entries with alternate arguments (such as WorkBuddy) stay with manual tasks.
_pinned_ids = (
    # 本机调整：淘金币任务放最前面（最常用）
    "diantao-coins",
    "diantao-all-in-one",
    "diantao-one-click",
    "diantao-one-click-tasks",
    "taobao-double11-energy",
    "diantao-work-speed",
    "diantao-duck-extra",
    "diantao-work-guard",
)
_script_ids = {
    task["script"]: task_id
    for task_id, task in TASKS.items()
    if not task["args"]
}
_sequence_ids = (
    _script_ids[step.filename]
    for step in ALL_TASKS
    if step.filename in _script_ids
)
TASKS = {
    task_id: TASKS[task_id]
    for task_id in dict.fromkeys((*_pinned_ids, *_sequence_ids, *TASKS))
}
