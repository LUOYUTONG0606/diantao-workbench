"""将淘金币双11赚能量独立注册到自动化大管家。"""
import argparse
import json
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
TARGET_ID = "taobao-double11-energy"


def register(butler_dir):
    registry = butler_dir / "tools" / "managed_process" / "registry.yaml"
    source = registry.read_text(encoding="utf-8")
    command = [sys.executable, "-u",
               (SCRIPT_DIR / "tools" / "run_taobao_energy.py").as_posix(),
               "run", "--repeat", "30"]
    entry = (
        f"  - id: {TARGET_ID}\n"
        '    name: "淘金币双11赚能量"\n'
        "    category: script\n"
        "    lifecycle: managed_job\n"
        f"    command: {json.dumps(command, ensure_ascii=False)}\n"
        f"    working_directory: {json.dumps(SCRIPT_DIR.as_posix(), ensure_ascii=False)}\n"
        '    description: "赚能量：淘宝→支付宝→其他应用；缺应用/下载页直接返回，不下载不升级不兑换；每次启动最多运行60分钟"\n'
        "    risk: device_automation\n"
        "    enabled: true\n"
        "    requires_human_approval: false\n\n"
    )
    pattern = rf"^  - id: {re.escape(TARGET_ID)}\n.*?(?=^  - id: |\Z)"
    if re.search(pattern, source, flags=re.MULTILINE | re.DOTALL):
        source = re.sub(pattern, lambda _: entry, source, count=1,
                        flags=re.MULTILINE | re.DOTALL)
    else:
        marker = "  - id: taobao-farm\n"
        if marker not in source:
            raise ValueError("未找到淘宝脚本的注册位置")
        source = source.replace(marker, entry + marker, 1)
    registry.write_text(source, encoding="utf-8")
    print(f"已注册淘金币双11赚能量：{registry}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("butler_dir", type=Path)
    register(parser.parse_args().butler_dir)
