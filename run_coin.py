"""ASCII 入口：以 __main__ 方式运行 淘金币任务.py。

为什么需要这个：.cmd 里直接写中文文件名，在不同代码页/系统区域设置下
可能找不到文件（尤其是 UTF-8 与 GBK 混用时）。这里用纯 ASCII 路径做中转，
内部再按绝对路径加载真正的中文脚本。

用法：
    python run_coin.py run --max-tasks 30
    python run_coin.py here
"""

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "淘金币任务.py"

if not TARGET.is_file():
    print(f"[ERROR] 找不到脚本：{TARGET}")
    raise SystemExit(1)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.argv = [str(TARGET), *sys.argv[1:]]
runpy.run_path(str(TARGET), run_name="__main__")
