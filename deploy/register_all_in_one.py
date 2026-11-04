"""将“点淘一键全部执行”注册到自动化大管家的 Python 脚本分类。"""

import argparse
import json
import re
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
TARGET_ID = "diantao-all-in-one"


def register(butler_dir):
    # 与其它点淘入口一致：按注册工具所在目录写绝对路径，避免 ${PARENT_DIR} 指向旧副本。
    registry = butler_dir / "tools" / "managed_process" / "registry.yaml"
    source = registry.read_text(encoding="utf-8")
    command_path = json.dumps((SCRIPT_DIR / "一键全部执行.py").as_posix(), ensure_ascii=False)
    working_dir = json.dumps(SCRIPT_DIR.as_posix(), ensure_ascii=False)
    entry = (
        f"  - id: {TARGET_ID}\n"
        f"    name: {json.dumps('点淘一键全部执行', ensure_ascii=False)}\n"
        "    category: script\n"
        "    lifecycle: managed_job\n"
        f'    command: ["${{PYTHON}}", {command_path}]\n'
        f"    working_directory: {working_dir}\n"
        f"    description: {json.dumps('点淘全流程：连通性 → 下滑 → 日常任务 → 养优惠 → 淘金币双11赚能量（各最多复核5轮）；不设超时，手动停止', ensure_ascii=False)}\n"
        "    risk: device_automation\n"
        "    enabled: true\n"
        "    requires_human_approval: false\n\n"
    )
    pattern = rf"^  - id: {re.escape(TARGET_ID)}\n.*?(?=^  - id: |\Z)"
    if re.search(pattern, source, flags=re.MULTILINE | re.DOTALL):
        source = re.sub(pattern, lambda _: entry, source, count=1,
                        flags=re.MULTILINE | re.DOTALL)
    else:
        # 优先放到点淘脚本分类开头，便于在“Python脚本”列表中看到。
        for marker in ("  - id: diantao-one-click\n", "  - id: diantao-anchor\n"):
            if marker in source:
                source = source.replace(marker, entry + marker, 1)
                break
        else:
            raise ValueError("未找到大管家点淘脚本分类的注册位置")
    registry.write_text(source, encoding="utf-8")
    print(f"已注册点淘一键全部执行：{registry}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("butler_dir", type=Path, help="自动化大管家目录")
    register(parser.parse_args().butler_dir)
