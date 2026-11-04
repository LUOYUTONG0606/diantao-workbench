"""将点淘一键入口和养优惠注册到自动化大管家的 Python 脚本分类。"""

import argparse
import json
import re
from pathlib import Path


TARGET_ID = "diantao-one-click"
REMAINING_TARGET_ID = "diantao-one-click-tasks"
SCRIPT_DIR = Path(__file__).resolve().parents[1]


def register(butler_dir):
    # 更新已有入口并补充独立任务，保留其他配置；重复注册不会新增按钮。
    registry = butler_dir / "tools" / "managed_process" / "registry.yaml"
    source = registry.read_text(encoding="utf-8")
    entries = (
        (TARGET_ID, "点淘一键顺序执行", "一键顺序执行.py", "连通性 → 下滑"),
        (REMAINING_TARGET_ID, "点淘一键任务执行", "一键任务执行.py",
         "主播 → 签到 → 睡觉 → 钻石 → 摇一摇 → 购物 → 打工 → 走路 → 元宝中心小任务 → 养优惠"),
        ("diantao-coupon", "养优惠", "养优惠.py", "首页养优惠：先浏览和媒体任务，搜索宝贝浏览最后执行"),
    )
    for target_id, name, filename, description in entries:
        command_path = json.dumps((SCRIPT_DIR / filename).as_posix(), ensure_ascii=False)
        working_dir = json.dumps(SCRIPT_DIR.as_posix(), ensure_ascii=False)
        entry = (
            f"  - id: {target_id}\n"
            f"    name: {json.dumps(name, ensure_ascii=False)}\n"
            "    category: script\n"
            "    lifecycle: managed_job\n"
            f'    command: ["${{PYTHON}}", {command_path}]\n'
            f"    working_directory: {working_dir}\n"
            f"    description: {json.dumps(description, ensure_ascii=False)}\n"
            "    risk: device_automation\n"
            "    enabled: true\n"
            "    requires_human_approval: false\n\n"
        )
        pattern = rf"^  - id: {re.escape(target_id)}\n.*?(?=^  - id: |\Z)"
        if re.search(pattern, source, flags=re.MULTILINE | re.DOTALL):
            source = re.sub(pattern, lambda _: entry, source, count=1,
                            flags=re.MULTILINE | re.DOTALL)
        else:
            marker = "  - id: diantao-anchor\n"
            if marker not in source:
                raise ValueError("未找到大管家点淘脚本分类的注册位置")
            source = source.replace(marker, entry + marker, 1)
    # 这些独立按钮也必须指向本项目，避免 ${PARENT_DIR} 启动另一份旧副本。
    routes = []
    for match in re.finditer(r"^  - id: (diantao-[^\n]+)\n.*?(?=^  - id: |\Z)",
                             source, flags=re.MULTILINE | re.DOTALL):
        command_line = re.search(r"^    command: (.+)$", match.group(0), flags=re.MULTILINE)
        if not command_line:
            continue
        command = json.loads(command_line.group(1))
        if len(command) < 2:
            continue
        script = command[1].replace("\\", "/")
        filename = script.rsplit("/", 1)[-1]
        if "/diantao/" in script and (SCRIPT_DIR / filename).is_file():
            routes.append((match.group(1), filename, command))
    for target_id, filename, command_args in routes:
        pattern = rf"^  - id: {re.escape(target_id)}\n.*?(?=^  - id: |\Z)"
        match = re.search(pattern, source, flags=re.MULTILINE | re.DOTALL)
        if match:
            block = match.group(0)
            command_args[1] = (SCRIPT_DIR / filename).as_posix()
            command = json.dumps(command_args, ensure_ascii=False)
            block = re.sub(r"^    command:.*$", lambda _: f"    command: {command}",
                           block, flags=re.MULTILINE)
            working_dir = json.dumps(SCRIPT_DIR.as_posix(), ensure_ascii=False)
            block = re.sub(r"^    working_directory:.*$",
                           lambda _: f"    working_directory: {working_dir}",
                           block, flags=re.MULTILINE)
            source = source[:match.start()] + block + source[match.end():]
    registry.write_text(source, encoding="utf-8")
    print(f"已注册点淘一键入口和养优惠：{registry}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("butler_dir", type=Path, help="自动化大管家目录")
    register(parser.parse_args().butler_dir)
