"""
测试脚本：用云端 PaddleOCRv4 分析截图，打印所有文字块及坐标
用法：可直接运行，默认测试 img/Screenshot_20260607_232540.jpg
      也可通过命令行参数指定图片路径
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PIL import Image
from cloud_ocr import ocr_server_url
import io
import requests

OCR_SERVER_URL = ocr_server_url()

def cloud_ocr(img_pil):
    """上传 PIL 图片到云端 OCR，返回文本块列表"""
    buf = io.BytesIO()
    img_pil.save(buf, format="JPEG", quality=70)
    jpeg_bytes = buf.getvalue()

    resp = requests.post(
        OCR_SERVER_URL,
        files={"image": ("screenshot.jpg", jpeg_bytes, "image/jpeg")},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 200:
        raise Exception(f"云端 OCR 返回异常: {data.get('msg', 'unknown')}")

    blocks = []
    for item in data.get("data", []):
        text = item.get("text", "").strip()
        score = item.get("score", 0.0)
        center = item.get("center", {})
        cx = center.get("x", 0)
        cy = center.get("y", 0)
        box = item.get("box", [])
        blocks.append({
            "text": text,
            "cx": cx,
            "cy": cy,
            "score": score,
            "box": box,
        })
    return blocks

# 读取截图
default_img = os.path.join(os.path.dirname(__file__), "img", "Screenshot_20260607_232540.jpg")
img_path = sys.argv[1] if len(sys.argv) > 1 else default_img

if not os.path.exists(img_path):
    print(f"❌ 图片不存在: {img_path}")
    print(f"   用法: python test_ocr_screenshot.py [图片路径]")
    sys.exit(1)

with Image.open(img_path) as img:
    w, h = img.size
    print(f"📐 图片尺寸: {w} x {h}")

# 调用云端 OCR
print(f"\n=== 云端 PaddleOCRv4 识别结果 ===")
print(f"  服务器: {OCR_SERVER_URL}")
results = cloud_ocr(img)
print(f"✅ 识别到 {len(results)} 个文本块\n")

for i, block in enumerate(results):
    text = block["text"]
    cx, cy = block["cx"], block["cy"]
    score = block["score"]
    box = block["box"]
    if box:
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        left = min(xs)
        top = min(ys)
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        print(f"  [{i:2d}] {text:30s}  @({cx:4d},{cy:4d})  conf={score:.2f}  box=({left},{top},{width},{height})")
    else:
        print(f"  [{i:2d}] {text:30s}  @({cx:4d},{cy:4d})  conf={score:.2f}")

# 特别标出关键词
print("\n=== 关键词分析 ===")
keywords = ["去完成", "去浏览", "去观看", "去领取", "去玩", "猜价格", "浏览精选", "快速领"]
for block in results:
    text = block["text"]
    cx, cy = block["cx"], block["cy"]
    score = block["score"]
    for kw in keywords:
        if kw in text:
            print(f"  ✅ '{kw}' in [{text}]  @({cx},{cy})  conf={score:.2f}")
            break

# 查找 "去完成" 按钮和其左边的任务名
print("\n=== 去完成按钮 + 左侧任务名关联测试 ===")
qwc_list = []
for block in results:
    text = block["text"]
    if "去完成" not in text:
        continue
    qwc_list.append(block)

# 找每个去完成左边的文本（当前OCR关联逻辑）
for q in qwc_list:
    print(f"\n  🎯 去完成 @({q['cx']},{q['cy']})")
    found = None
    for block in results:
        text = block["text"]
        if not text or text == "去完成":
            continue
        lcx, lcy = block["cx"], block["cy"]
        # 当前代码逻辑：
        if lcx < q["cx"] - 30 and abs(lcy - q["cy"]) < 80:
            print(f"     左边文本 [{text:30s}] @({lcx:4d},{lcy:4d})  ✓ 匹配（80px阈值）")
            if found is None:
                found = text
        else:
            if lcx < q["cx"] - 30:
                print(f"     左边文本 [{text:30s}] @({lcx:4d},{lcy:4d})  ✗ 垂直距离={abs(lcy-q['cy'])}px（超80px阈值）")
    print(f"     当前匹配结果: task_name = [{found}]")

print("\n=== 完成 ===")
