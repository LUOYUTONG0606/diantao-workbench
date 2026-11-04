#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
点淘1024游戏 - 棋盘校准工具
用法: python calibrate.py
  1. 确保手机已连接ADB且游戏界面已打开
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
    print("[错误] 需要OpenCV: pip install opencv-python")
    sys.exit(1)


def adb_screenshot(adb_path="adb", remote="/sdcard/screen_cal.png"):
    """ADB截图到本地"""
    print("[1/3] 截图...")
    subprocess.run([adb_path, "shell", "screencap", "-p", remote],
                   capture_output=True, timeout=15)
    local = "calibration_screen.png"
    subprocess.run([adb_path, "pull", remote, local],
                   capture_output=True, timeout=15)
    if os.path.exists(local):
        print("  成功: " + local + " (" + str(os.path.getsize(local)) + " bytes)")
        return local
    return None


class Calibrator:
    def __init__(self, img_path):
        self.img = cv2.imread(img_path)
        if self.img is None:
            raise ValueError("无法读取: " + img_path)
        h, w = self.img.shape[:2]
        self.clicks = []
        self.labels = ["左上角", "右上角", "右下角", "左下角"]
        # 缩放显示
        self.scale = min(1.0, 900.0 / max(w, h))
        self.dw = int(w * self.scale)
        self.dh = int(h * self.scale)
        print("[2/3] 图片 " + str(w) + "x" + str(h) +
              "  缩放 " + str(round(self.scale, 2)))

    def on_mouse(self, event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        ox, oy = int(x / self.scale), int(y / self.scale)
        self.clicks.append((ox, oy))
        idx = len(self.clicks) - 1
        print("  [" + str(idx + 1) + "/4] " + self.labels[idx] +
              " (" + str(ox) + "," + str(oy) + ")")

        colors = [(0,255,0), (255,0,0), (0,0,255), (255,255,0)]
        cv2.circle(self.display_img, (x, y), 10, colors[idx], 3)
        cv2.putText(self.display_img, str(idx + 1), (x + 12, y - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, colors[idx], 3)
        cv2.imshow("Calibration", self.display_img)

        if len(self.clicks) >= 4:
            self.show_result()

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
        print("BOARD_AREA = " + str(area))
        print("棋盘尺寸: " + str(bw) + "x" + str(bh))
        print("格子尺寸: " + str(cw) + "x" + str(ch))
        print("=" * 50)

        # 绘制结果图
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

        # 保存配置
        margin = max(2, min(cw, ch) // 20)
        cfg = ("BOARD_AREA = " + str(area) + "\n" +
                "CELL_MARGIN = " + str(margin) + "\n")
        with open("calibration_config.txt", "w") as f:
            f.write(cfg)
        print("已保存 calibration_config.txt")
        print(cfg)

        cv2.imshow("Result", res)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def run(self):
        self.display_img = cv2.resize(self.img, (self.dw, self.dh))
        cv2.imshow("Calibration", self.display_img)
        cv2.setMouseCallback("Calibration", self.on_mouse)
        print("\n依次点击: 1.左上 2.右上 3.右下 4.左下  (ESC取消)\n")
        while len(self.clicks) < 4:
            if cv2.waitKey(100) & 0xFF == 27:
                print("取消")
                cv2.destroyAllWindows()
                return
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def main():
    print("=" * 50 + "\n  点淘1024 - 棋盘校准工具\n" + "=" * 50)
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
