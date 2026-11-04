#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
点淘1024游戏 - 棋盘校准工具（matplotlib 版，无 OpenCV GUI 依赖）
用法: python calibrate.py
  1. 确保手机已连接 ADB 且游戏界面已打开
  2. 运行后依次点击棋盘: 左上 -> 右上 -> 右下 -> 左下
  3. 生成的配置保存到 calibration_config.txt
"""
import os
import sys
import subprocess
import numpy as np

try:
    import cv2
except ImportError:
    print("[错误] 需要 OpenCV: pip install opencv-python")
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
except ImportError:
    print("[错误] 需要 matplotlib: pip install matplotlib")
    sys.exit(1)


# ============================================================
# ADB 截图（复用 utils.py 的 ADB_PATH 习惯）
# ============================================================
ADB_PATH = "adb"   # 若 adb 不在 PATH，填写使用者自己的 adb.exe 完整路径


def adb_screenshot(adb_path=ADB_PATH, remote="/sdcard/cal_screen.png"):
    """ADB 截图到本地，返回本地路径或 None"""
    print("[1/3] 截图...")
    subprocess.run([adb_path, "shell", "screencap", "-p", remote],
                   capture_output=True, timeout=15)
    local = "calibration_screen.png"
    subprocess.run([adb_path, "pull", remote, local],
                   capture_output=True, timeout=15)
    if os.path.exists(local):
        size = os.path.getsize(local)
        print(f"  成功: {local} ({size} bytes)")
        return local
    return None


# ============================================================
# 主校准逻辑（matplotlib 实现点击取点）
# ============================================================
class Calibrator:
    def __init__(self, img_path):
        self.img = cv2.imread(img_path)
        if self.img is None:
            raise ValueError("无法读取: " + img_path)
        self.img_rgb = cv2.cvtColor(self.img, cv2.COLOR_BGR2RGB)
        self.h, self.w = self.img.shape[:2]
        self.clicks = []          # [(x, y), ...]
        self.labels = ["左上角", "右上角", "右下角", "左下角"]
        print(f"[2/3] 图片 {self.w}x{self.h}")

    def run(self):
        """
        用 matplotlib 显示截图，用户依次点击 4 个点，
        plt.ginput 阻塞直到收齐指定点数。
        """
        fig, ax = plt.subplots(figsize=(6, max(6, int(6 * self.h / self.w))))
        ax.imshow(self.img_rgb)
        ax.set_title("依次点击: 1.左上  2.右上  3.右下  4.左下  （关闭窗口取消）")
        ax.axis("off")
        plt.tight_layout()

        print("\n依次点击: 1.左上  2.右上  3.右下  4.左下  （关闭窗口取消）\n")

        # ginput 最多收 4 个点，鼠标左键点击
        points = plt.ginput(4, timeout=0, show_clicks=True)
        plt.close(fig)

        if len(points) < 4:
            print("取消（未点够 4 个点）")
            return

        # ginput 返回的是 (x, y) 像素坐标，直接存入
        self.clicks = [(int(round(p[0])), int(round(p[1]))) for p in points]
        for i, (x, y) in enumerate(self.clicks):
            print(f"  [{i+1}/4] {self.labels[i]} ({x},{y})")

        self.show_result()

    # --------------------------------------------------------
    def show_result(self):
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = self.clicks
        left   = min(x1, x4)
        top    = min(y1, y2)
        right  = max(x2, x3)
        bottom = max(y3, y4)
        area = (left, top, right, bottom)
        bw, bh = right - left, bottom - top
        cw, ch = bw // 4, bh // 4

        print("\n" + "=" * 50)
        print(f"BOARD_AREA = {area}")
        print(f"棋盘尺寸: {bw}x{bh}")
        print(f"格子尺寸: {cw}x{ch}")
        print("=" * 50)

        # 绘制结果图：绿色框 + 网格 + 中心点
        res = self.img.copy()
        cv2.rectangle(res, (left, top), (right, bottom), (0, 255, 0), 3)
        for i in range(5):
            x = left + i * cw
            cv2.line(res, (x, top), (x, bottom), (0, 255, 0), 1)
            y = top + i * ch
            cv2.line(res, (left, y), (right, y), (0, 255, 0), 1)
        for r in range(4):
            for c in range(4):
                cx = left + c * cw + cw // 2
                cy = top + r * ch + ch // 2
                cv2.circle(res, (cx, cy), 5, (0, 0, 255), -1)

        cv2.imwrite("calibration_result.png", res)
        print("\n已保存 calibration_result.png  请检查网格对齐\n")

        # 用 matplotlib 展示结果图（避免 cv2.imshow）
        res_rgb = cv2.cvtColor(res, cv2.COLOR_BGR2RGB)
        fig, ax = plt.subplots(figsize=(6, max(6, int(6 * bh / bw))))
        ax.imshow(res_rgb)
        ax.set_title("校准结果 - 关闭窗口继续")
        ax.axis("off")
        plt.tight_layout()
        plt.show()

        # 保存配置
        margin = max(2, min(cw, ch) // 20)
        cfg = f"BOARD_AREA = {area}\nCELL_MARGIN = {margin}\n"
        with open("calibration_config.txt", "w") as f:
            f.write(cfg)
        print("已保存 calibration_config.txt")
        print(cfg)


# ============================================================
# 入口
# ============================================================
def main():
    print("=" * 50 + "\n  点淘1024 - 棋盘校准工具 (matplotlib 版)\n" + "=" * 50)
    path = adb_screenshot()
    if not path:
        for alt in ["screen_temp.png", "screen.png", "calibration_screen.png"]:
            if os.path.exists(alt):
                path = alt
                print("  使用已有: " + path)
                break
        else:
            print("截图失败, 请将截图命名为 screen.png 后重试")
            sys.exit(1)
    Calibrator(path).run()


if __name__ == "__main__":
    main()
