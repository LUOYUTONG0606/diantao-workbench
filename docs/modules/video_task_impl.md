# 视频任务实现索引

- `utils.pause_global_watcher()`: [utils.py](../../utils.py#L442)
- `utils.resume_global_watcher()`: [utils.py](../../utils.py#L452)
- `match_task_type()`: [走路赚元宝.py:81-97]
- `exec_video()`: [走路赚元宝.py:200-210]
- `execute_task()`: [走路赚元宝.py:234-250]
- `video_swipe_count_range()`: [u2_scanner.py](../../u2_scanner.py#L1106)
- `execute_video_task()` 暂停/恢复入口: [u2_scanner.py](../../u2_scanner.py#L1118)
- `_execute_video_task()` 次数和停留执行: [u2_scanner.py](../../u2_scanner.py#L1138)

## 调用链

`看视频` → 调用方入口点击 → `execute_video_task()` 暂停 watcher → 按配置识别媒体类型/执行首滑 → 完成固定次数并满足最低停留 → `back` → 恢复 watcher。走路任务保留外层暂停。

验证见 `tests/test_video_task_contract.py`、`tests/test_task_timing_guards.py`、`tests/test_media_page_verification.py`、`tests/test_audit_regressions.py`，覆盖慢设备、失败指令、额外次数/时间/首滑、嵌套暂停和异常恢复。
