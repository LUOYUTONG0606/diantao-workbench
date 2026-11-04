"""Shared guardian clock and rescan requests; importing never touches the phone."""
import math
import os
from pathlib import Path
import time
import uuid

from phone_control import read_json, remove_file, write_json


ROOT = Path(__file__).resolve().parent / ".runtime" / "dagong_guard"
RUN_ENV = "DIANTAO_GUARD_RUN_ID"


class GuardTimer:
    def __init__(self, directory=ROOT, *, now=time.time):
        self.directory = Path(directory)
        self.now = now
        self.value = {"pid": os.getpid(), "run_id": os.environ.get(RUN_ENV),
                      "phase": "starting",
                      "observed_at": None, "work_at": None, "energy_at": None}

    def publish(self, phase, *, schedule=None, message=""):
        self.value.update(phase=phase, updated_at=self.now(), message=message)
        if schedule is not None:
            self.value.update(observed_at=self.now(), work_at=schedule.work_at,
                              energy_at=schedule.energy_at)
        write_json(self.directory / "timer.json", self.value)

    def refresh_pending(self):
        return (self.directory / "refresh.json").is_file()

    def consume_refresh(self):
        remove_file(self.directory / "refresh.json")


def request_refresh(directory=ROOT):
    write_json(Path(directory) / "refresh.json", {"id": uuid.uuid4().hex})


def timer_status(pid=None, *, run_id=None, directory=ROOT, now=time.time):
    try:
        value = read_json(Path(directory) / "timer.json") or {}
    except (OSError, ValueError):
        value = {}
    # Windows venv launchers may have a different PID from their Python worker.
    active = pid is not None and (
        value.get("run_id") == run_id if run_id else value.get("pid") == pid)
    phase = value.get("phase", "starting") if active else ("starting" if pid else "stopped")
    result = {"phase": phase, "active": active, "server_time": now(),
              "message": value.get("message", "") if active else "",
              "observed_at": value.get("observed_at") if active else None}
    for name in ("work", "energy"):
        deadline = value.get(name + "_at") if active else None
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            deadline = None
        result[name + "_at"] = deadline
        result[name + "_remaining"] = (max(0, math.ceil(deadline - result["server_time"]))
                                     if deadline is not None else None)
    return result
