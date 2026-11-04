"""
任务选择器 GUI - 扫描脚本、勾选、排序、一键执行
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import json
import time
import threading
from datetime import datetime

from task_runner import run_script
from task_catalog import is_task_file

# ========================================================
# 全局变量
# ========================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "task_config.json")

EXTRA_TASKS = [
    {"name": "2026淘宝618活动", "file": "../coin11-tb-main/2026淘宝618活动.py", "timeout": 2100},
]

DEFAULT_TIMEOUTS = {
    "一键下滑": 2100,
    "主播打卡粉丝礼": 2100, "点淘购物赚钱": 2100, "睡觉赚元宝": 3600,
    "点淘鸭钻石任务": 2100, "打工得体力": 3600, "618花花乐": 2100,
    "2026淘宝618活动": 2100, "签到赢元宝": 2100, "走路赚元宝": 600,
    "点淘鸭": 2100,
    "养优惠": 14400,
}

LOG_COLORS = {
    "green": "#4ec94e", "red": "#f44747", "yellow": "#dcdcaa",
    "cyan": "#4fc1ff", "dim": "#8a8a8a", "time": "#569cd6",
    "title": "#c586c0", "task": "#d4d4d4",
}


def scan_tasks():
    """只发现登记过的业务脚本。"""
    tasks = []
    seen = set()
    for f in sorted(os.listdir(SCRIPT_DIR)):
        if not is_task_file(f):
            continue
        name = f.replace(".py", "")
        if name in seen:
            continue
        seen.add(name)
        tasks.append({"name": name, "file": f, "timeout": DEFAULT_TIMEOUTS.get(name, 600)})
    for ext in EXTRA_TASKS:
        if ext["name"] not in seen and os.path.isfile(os.path.join(SCRIPT_DIR, ext["file"])):
            tasks.append(ext)
            seen.add(ext["name"])
    return tasks


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return [t for t in json.load(f) if is_task_file(t.get("file", ""))]
    except Exception:
        return None


def save_config(tasks):
    data = []
    for t in tasks:
        data.append({"name": t["name"], "file": t["file"], "timeout": t["timeout"], "checked": t["checked"]})
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        messagebox.showerror("保存失败", str(e))
        return False


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("点淘任务选择器")
        self.root.geometry("780x680")
        self.root.minsize(650, 550)

        self.tasks = []
        self.stop_flag = False
        self.running = False
        self.closing = False

        self._build_ui()
        self._load_tasks()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # 标题
        title_frame = ttk.Frame(self.root, padding=(10, 8))
        title_frame.pack(fill=tk.X)
        ttk.Label(title_frame, text="点淘任务选择器", font=("Microsoft YaHei", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(title_frame, text=f"目录: {SCRIPT_DIR}", font=("Microsoft YaHei", 8), foreground="gray").pack(side=tk.RIGHT)

        # 任务列表
        list_frame = ttk.LabelFrame(self.root, text=" 任务列表 ", padding=(8, 5))
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        hdr = ttk.Frame(list_frame)
        hdr.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(hdr, text="  ", width=3).pack(side=tk.LEFT)
        ttk.Label(hdr, text="任务名称", font=("", 9, "bold"), width=22).pack(side=tk.LEFT, anchor=tk.W)
        ttk.Label(hdr, text="脚本文件", font=("", 9, "bold"), width=28).pack(side=tk.LEFT, anchor=tk.W)
        ttk.Label(hdr, text="超时(s)", font=("", 9, "bold"), width=8).pack(side=tk.LEFT)

        cf = ttk.Frame(list_frame)
        cf.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(cf, highlightthickness=0, bg="#f9f9f9")
        scrollbar = ttk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        self.task_container = ttk.Frame(self.canvas)
        self.task_container.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.task_container, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", lambda ev: self.canvas.yview_scroll(int(-ev.delta/120), "units")))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

        # 按钮栏
        btn_frame = ttk.Frame(self.root, padding=(10, 3))
        btn_frame.pack(fill=tk.X)

        ttk.Label(btn_frame, text="排序:", font=("", 9)).pack(side=tk.LEFT, padx=(0, 5))
        for txt, cmd in [("↑ 上移", self._move_up), ("↓ 下移", self._move_down),
                          ("⬆ 置顶", self._move_top), ("⬇ 置底", self._move_bottom)]:
            ttk.Button(btn_frame, text=txt, width=8, command=cmd).pack(side=tk.LEFT, padx=2)

        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(btn_frame, text="☑ 全选", width=8, command=self._select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="☐ 取消全选", width=9, command=self._deselect_all).pack(side=tk.LEFT, padx=2)
        ttk.Separator(btn_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(btn_frame, text="💾 保存", width=8, command=self._save).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="📂 加载", width=8, command=self._load).pack(side=tk.LEFT, padx=2)

        # 运行
        run_frame = ttk.Frame(self.root, padding=(10, 3))
        run_frame.pack(fill=tk.X)
        self.btn_run = ttk.Button(run_frame, text="▶ 运行选中", command=self._run_tasks)
        self.btn_run.pack(side=tk.LEFT, padx=2)
        self.btn_stop = ttk.Button(run_frame, text="■ 停止", command=self._stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=2)
        self.progress_label = ttk.Label(run_frame, text="", font=("", 9))
        self.progress_label.pack(side=tk.LEFT, padx=10)

        # 日志
        log_frame = ttk.LabelFrame(self.root, text=" 日志 ", padding=(8, 3))
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log = scrolledtext.ScrolledText(log_frame, height=10, font=("", 10),
                                             bg="#1e1e1e", fg="#f0f0f0", state=tk.DISABLED, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        for tag, color in LOG_COLORS.items():
            self.log.tag_config(tag, foreground=color)

    def _load_tasks(self):
        config = load_config()
        scanned = scan_tasks()
        if config:
            cfg_names = {t["name"] for t in config}
            ordered = []
            for c in config:
                ordered.append({"name": c["name"], "file": c["file"], "timeout": c["timeout"], "checked": c["checked"]})
            for nt in [t for t in scanned if t["name"] not in cfg_names]:
                ordered.append({"name": nt["name"], "file": nt["file"], "timeout": nt["timeout"], "checked": False})
            self.tasks = ordered
        else:
            self.tasks = [{"name": s["name"], "file": s["file"], "timeout": s["timeout"], "checked": False} for s in scanned]
        for t in self.tasks:
            t["var"] = tk.BooleanVar(value=t["checked"])
        self._render()

    def _render(self):
        for w in self.task_container.winfo_children():
            w.destroy()
        for idx, task in enumerate(self.tasks):
            row = ttk.Frame(self.task_container, padding=(2, 2))
            row.pack(fill=tk.X, padx=5, pady=1)
            ttk.Label(row, text=f"{idx+1}.", width=3, anchor=tk.E, font=("", 9)).pack(side=tk.LEFT)
            ttk.Checkbutton(row, text="", variable=task["var"]).pack(side=tk.LEFT, padx=(0, 3))
            ttk.Label(row, text=task["name"], width=22, anchor=tk.W, font=("", 9)).pack(side=tk.LEFT)
            ttk.Label(row, text=task["file"], width=28, anchor=tk.W, font=("", 8), foreground="#666").pack(side=tk.LEFT)
            entry = ttk.Entry(row, width=8, font=("Consolas", 9), justify=tk.CENTER)
            entry.insert(0, str(task["timeout"]))
            entry.bind("<FocusOut>", lambda e, i=idx, w=entry: self._on_timeout(i, w))
            entry.pack(side=tk.LEFT, padx=5)
            ttk.Label(row, text="⠿", font=("", 12), foreground="#bbb").pack(side=tk.RIGHT, padx=(5, 2))
            task["entry"] = entry

    def _on_timeout(self, idx, w):
        try:
            v = int(w.get().strip())
            self.tasks[idx]["timeout"] = max(v, 10)
        except ValueError:
            w.delete(0, tk.END)
            w.insert(0, str(self.tasks[idx]["timeout"]))

    def _selected(self):
        return [i for i, t in enumerate(self.tasks) if t["var"].get()]

    def _move_up(self):
        for i in sorted(self._selected()):
            if i > 0:
                self.tasks[i], self.tasks[i-1] = self.tasks[i-1], self.tasks[i]
        self._render()

    def _move_down(self):
        for i in sorted(self._selected(), reverse=True):
            if i < len(self.tasks) - 1:
                self.tasks[i], self.tasks[i+1] = self.tasks[i+1], self.tasks[i]
        self._render()

    def _move_top(self):
        sel = sorted(self._selected())
        self.tasks = [self.tasks[i] for i in sel] + [self.tasks[i] for i in range(len(self.tasks)) if i not in sel]
        self._render()

    def _move_bottom(self):
        sel = set(self._selected())
        self.tasks = [self.tasks[i] for i in range(len(self.tasks)) if i not in sel] + [self.tasks[i] for i in sorted(sel)]
        self._render()

    def _select_all(self):
        for t in self.tasks:
            t["var"].set(True)

    def _deselect_all(self):
        for t in self.tasks:
            t["var"].set(False)

    def _save(self):
        for t in self.tasks:
            t["checked"] = t["var"].get()
        if save_config(self.tasks):
            messagebox.showinfo("保存", f"已保存到 {CONFIG_FILE}")

    def _load(self):
        config = load_config()
        if not config:
            messagebox.showinfo("提示", "无已保存配置")
            return
        cfg_names = {c["name"] for c in config}
        new_list = [t for t in self.tasks if t["name"] not in cfg_names]
        self.tasks = [{"name": c["name"], "file": c["file"], "timeout": c["timeout"], "checked": c["checked"]} for c in config] + new_list
        for t in self.tasks:
            t["var"] = tk.BooleanVar(value=t["checked"])
        self._render()
        messagebox.showinfo("加载", "配置已加载")

    def _run_tasks(self):
        if self.running:
            return
        self.stop_flag = False
        for t in self.tasks:
            t["checked"] = t["var"].get()
        selected = [t for t in self.tasks if t["checked"]]
        if not selected:
            messagebox.showwarning("提示", "请勾选任务")
            return
        self.running = True
        self.btn_run.config(state=tk.DISABLED, text="⏳ 运行中...")
        self.btn_stop.config(state=tk.NORMAL)
        self.progress_label.config(text=f"共 {len(selected)} 个")
        self.log.config(state=tk.NORMAL)
        self.log.delete(1.0, tk.END)
        self.log.config(state=tk.DISABLED)

        def _run():
            try:
                self._execute(selected)
            finally:
                self.root.after(0, self._on_finish)
        threading.Thread(target=_run, daemon=True).start()

    def _stop(self):
        self.stop_flag = True
        self.btn_stop.config(state=tk.DISABLED)
        self.progress_label.config(text="正在停止...")

    def _execute(self, selected):
        total = len(selected)
        ok_count = 0
        self._log("="*60, "title")
        self._log(f"  开始 {total} 个任务", "title")
        self._log(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "time")
        self._log("="*60, "title")

        for idx, task in enumerate(selected, 1):
            if self.stop_flag:
                self._log("\n  用户停止", "yellow")
                break
            self.root.after(0, lambda idx=idx: self.progress_label.config(text=f"执行: {idx}/{total}"))
            name = task["name"]
            filepath = os.path.join(SCRIPT_DIR, task["file"])
            if not os.path.exists(filepath):
                self._log(f"\n  [X] {name} 文件不存在", "red")
                continue

            self._log(f"\n{'─'*60}")
            self._log(f"  [{datetime.now().strftime('%H:%M:%S')}] {name}", "task")
            time.sleep(3)
            if self.stop_flag:
                break

            try:
                success, elapsed, reason = run_script(
                    filepath, cwd=SCRIPT_DIR, timeout=task.get("timeout", 600),
                    should_stop=lambda: self.stop_flag,
                    on_line=lambda line: self._log(line, "cyan"),
                )
                self._log(f"\n  {name}: {reason} ({int(elapsed)}s)", "green" if success else "red")
                if success:
                    ok_count += 1
            except Exception as e:
                self._log(f"\n  [X] {name} 异常: {e}", "red")
        if not self.stop_flag:
            self._log(f"\n{'='*60}", "title")
            self._log(f"  完成! 成功 {ok_count}/{total}", "green" if ok_count == total else "yellow")
            self._log(f"{'='*60}", "title")

    def _log(self, msg, tag=None):
        def _do():
            self.log.config(state=tk.NORMAL)
            if tag:
                self.log.insert(tk.END, msg + "\n", tag)
            else:
                self.log.insert(tk.END, msg + "\n")
            self.log.see(tk.END)
            self.log.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _on_finish(self):
        self.running = False
        self.btn_run.config(state=tk.NORMAL, text="▶ 运行选中")
        self.btn_stop.config(state=tk.DISABLED)
        self.progress_label.config(text="已停止" if self.stop_flag else "完成")
        if self.closing:
            self._on_close()

    def _on_close(self):
        if self.running:
            self.closing = True
            self._stop()
            return
        self.stop_flag = True
        for t in self.tasks:
            t["checked"] = t["var"].get()
        save_config(self.tasks)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
