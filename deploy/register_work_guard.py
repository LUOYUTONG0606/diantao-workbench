"""将独立打工守护任务注册到自动化大管家，不加入一键任务队列。"""
import argparse
import json
from pathlib import Path
import re


SCRIPT_DIR = Path(__file__).resolve().parents[1]
TARGET_ID = "diantao-work-guard"


def register(butler_dir):
    registry = butler_dir / "tools" / "managed_process" / "registry.yaml"
    source = registry.read_text(encoding="utf-8")
    command = json.dumps(
        ["${PYTHON}", "-u", (SCRIPT_DIR / "打工任务.py").as_posix()], ensure_ascii=False,
    )
    entry = (
        f"  - id: {TARGET_ID}\n"
        '    name: "打工任务"\n'
        "    category: script\n"
        "    lifecycle: managed_job\n"
        f"    command: {command}\n"
        f"    working_directory: {json.dumps(SCRIPT_DIR.as_posix(), ensure_ascii=False)}\n"
        '    description: "持续守护：厨师两小时循环，领取到期元宝并完成奖励任务；左侧领体力按独立倒计时领取，手动停止"\n'
        "    risk: device_automation\n"
        "    enabled: true\n"
        "    requires_human_approval: false\n\n"
    )
    pattern = rf"^  - id: {re.escape(TARGET_ID)}\n.*?(?=^  - id: |\Z)"
    if re.search(pattern, source, flags=re.MULTILINE | re.DOTALL):
        source = re.sub(pattern, lambda _: entry, source, count=1,
                        flags=re.MULTILINE | re.DOTALL)
    else:
        marker = next((m for m in ("  - id: diantao-work\n", "  - id: diantao-anchor\n")
                       if m in source), None)
        if not marker:
            raise ValueError("未找到大管家点淘脚本分类的注册位置")
        source = source.replace(marker, entry + marker, 1)
    registry.write_text(source, encoding="utf-8")
    print(f"已注册打工任务守护：{registry}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("butler_dir", type=Path)
    register(parser.parse_args().butler_dir)
