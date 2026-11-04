"""
实时截取手机当前屏幕 → 本地 PaddleOCR → 打印所有文本块及坐标
用于调试淘金币页面文本结构
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import uiautomator2 as u2
from utils import get_ocr_text_blocks, connect_device

d = connect_device()
w, h = d.window_size()
print(f"📱 已连接: {d.app_current}")
print(f"屏幕: {w}x{h}")

# 本地OCR扫描
print("\n=== 本地 PaddleOCR 实时扫描 ===")
blocks = get_ocr_text_blocks(d)

if not blocks:
    print("❌ OCR 未识别出任何文本")
    sys.exit(1)

print(f"✅ 识别到 {len(blocks)} 个文本块\n")

# 按Y坐标排序，从上到下
blocks.sort(key=lambda b: b["cy"])

for i, b in enumerate(blocks):
    text = b["text"]
    cx, cy = b["cx"], b["cy"]
    conf = b["conf"]
    print(f"  [{i:2d}] @({cx:4d},{cy:4d}) conf={conf:.2f}  {text}")

# 关键词标出
print("\n=== 关键词匹配 ===")
keywords = ["赚更多金币", "更多专享福利", "去完成", "去浏览", "去观看",
            "去参与", "去领取", "浏览", "直播", "视频", "点击",
            "逛", "跳转", "猜价格"]
for b in blocks:
    for kw in keywords:
        if kw in b["text"]:
            print(f"  ✅ '{kw}' → [{b['text']}] @({b['cx']},{b['cy']})")
            break

print("\n✅ 完成")