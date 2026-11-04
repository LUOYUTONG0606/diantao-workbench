"""
下滑看看"更多专享福利"下面的任务列表
"""
import sys
import os
import time
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from utils import connect_device, TB_APP, get_ocr_text_blocks
import requests

d = connect_device()
time.sleep(2)

# 先在当前页OCR
print("=== 当前页面 OCR ===")
blocks = get_ocr_text_blocks(d)
if blocks:
    for b in blocks:
        text = b.get("text", "").strip()
        score = b.get("score", 0.0)
        cx, cy = b.get("cx", 0), b.get("cy", 0)
        if text and score >= 0.4:
            print(f"  ({cx:4d},{cy:4d}) [{score:.2f}] {text}")

# 下滑一下看看"更多专享福利"下面的内容
print("\n\n=== 下滑一次 ===")
d.swipe(540, 1800, 540, 600)
time.sleep(2)

blocks = get_ocr_text_blocks(d)
if blocks:
    for b in blocks:
        text = b.get("text", "").strip()
        score = b.get("score", 0.0)
        cx, cy = b.get("cx", 0), b.get("cy", 0)
        if text and score >= 0.4:
            print(f"  ({cx:4d},{cy:4d}) [{score:.2f}] {text}")

print("\n\n=== 再下滑一次 ===")
d.swipe(540, 1800, 540, 600)
time.sleep(2)

blocks = get_ocr_text_blocks(d)
if blocks:
    for b in blocks:
        text = b.get("text", "").strip()
        score = b.get("score", 0.0)
        cx, cy = b.get("cx", 0), b.get("cy", 0)
        if text and score >= 0.4:
            print(f"  ({cx:4d},{cy:4d}) [{score:.2f}] {text}")