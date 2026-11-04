"""
分析已保存的 u2 截图 (temp_u2_screencap.png) 内容
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PIL import Image
from cloud_ocr import ocr_server_url
import requests

img = Image.open("temp_u2_screencap.png")
print(f"📸 截图尺寸: {img.size}")

buf = io.BytesIO()
img.save(buf, format="JPEG", quality=70)
buf.seek(0)

files = {"image": ("screenshot.jpg", buf, "image/jpeg")}
resp = requests.post(ocr_server_url(), files=files, timeout=60)
data = resp.json()
ocr_lines = data.get("data", [])
print(f"🔍 OCR 识别到 {len(ocr_lines)} 个文本块:\n")

# 按 Y 坐标排序
ocr_lines.sort(key=lambda x: x.get("center", {}).get("y", 0))

for item in ocr_lines:
    text = item.get("text", "").strip()
    score = item.get("score", 0.0)
    center = item.get("center", {})
    cx = center.get("x", 0)
    cy = center.get("y", 0)
    if text and score >= 0.4:
        print(f"  ({cx:4d},{cy:4d}) [{score:.2f}] {text}")
