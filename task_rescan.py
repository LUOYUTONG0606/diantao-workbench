from collections.abc import Sequence
from typing import Final, TypedDict


MAX_RESCAN_ROUNDS: Final = 2
RESCAN_TASK_NAMES: Final[frozenset[str]] = frozenset(
    {
        "签到赢元宝",
        "睡觉赚元宝",
        "点淘鸭钻石任务",
        "点淘鸭摇一摇",
        "点淘购物赚钱",
        "打工得体力",
    }
)


class TaskDefinition(TypedDict):
    name: str


def build_rescan_rounds(
    selected_tasks: Sequence[TaskDefinition],
) -> tuple[tuple[TaskDefinition, ...], ...]:
    target_tasks = tuple(
        task for task in selected_tasks if task["name"] in RESCAN_TASK_NAMES
    )
    if target_tasks:
        return tuple(target_tasks for _ in range(MAX_RESCAN_ROUNDS))
    return ()
