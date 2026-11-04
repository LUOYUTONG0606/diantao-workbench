"""仅服务于当次调度会话的大任务断点；不会自动加载上次启动的断点。"""
import hashlib
import itertools
import os
from pathlib import Path

from phone_control import read_json, write_json, remove_file


SESSION_ENV = "DIANTAO_CHECKPOINT_DIR"
SCOPE_ENV = "DIANTAO_CHECKPOINT_SCOPE"
_calls = itertools.count()


class Checkpoint:
    def __init__(self, key, label, signature=None):
        directory = os.environ.get(SESSION_ENV)
        parent = os.environ.get(SCOPE_ENV, "root")
        self.scope = parent + "/" + str(key)
        self.label = label
        self.signature = signature
        self.directory = Path(directory) if directory else None
        digest = hashlib.sha256(self.scope.encode("utf-8")).hexdigest()
        self.path = self.directory / f"{digest}.json" if self.directory else None
        self.cached = read_json(self.path) if self.path else None
        if self.cached and self.cached["signature"] != signature:
            raise RuntimeError("当前任务定义已经变化，不能沿用此次断点")

    @property
    def done(self):
        return bool(self.cached and self.cached["status"] == "done")

    def begin(self):
        if self.path:
            write_json(self.path, self.record("active"))
            print(f"[断点] 当前任务：{self.label}", flush=True)

    def record(self, status):
        return dict(scope=self.scope, label=self.label, signature=self.signature,
                    status=status, pid=os.getpid())

    def finish(self, result, feedback=()):
        if self.path:
            write_json(self.path, dict(self.record("done"), result=result,
                                       feedback=list(feedback)))

    def child_env(self):
        return {SCOPE_ENV: self.scope} if self.path else {}


def script_checkpoint(filename, command):
    return Checkpoint(f"script-{next(_calls)}", Path(filename).name,
                      signature=list(command))


def run_step(key, checkpoint_label, function, *args, **kwargs):
    """下滑的当前轮次被打断后整轮重做，已完成轮次跳过。"""
    checkpoint = Checkpoint(key, checkpoint_label)
    if checkpoint.done:
        print(f"[续跑] 跳过已完成：{checkpoint_label}", flush=True)
        return checkpoint.cached["result"]
    checkpoint.begin()
    result = function(*args, **kwargs)
    checkpoint.finish(result)
    return result


def snapshot(directory):
    return {path.name: read_json(path) for path in Path(directory).glob("*.json")}


def restore_snapshot(directory, records):
    """撤回结束进程树期间写入的完成标记，防止被打断任务被误判为已结束。"""
    directory = Path(directory)
    for path in directory.glob("*.json"):
        if path.name not in records:
            remove_file(path)
    for name, record in records.items():
        if record:
            write_json(directory / name, record)


def current_task(directory, fallback, records=None):
    records = list(snapshot(directory).values()) if records is None else list(records.values())
    records = [record for record in records if record]
    done = [record["scope"] + "/" for record in records if record["status"] == "done"]
    active = [record for record in records if record["status"] == "active"
              and not any(record["scope"].startswith(prefix) for prefix in done)]
    if not active:
        return fallback
    return max(active, key=lambda record: record["scope"].count("/"))["label"]
