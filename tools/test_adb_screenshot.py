"""
纯 ADB 截图验证：点击"赚更多金币"后，ADB 截图能否捕获覆盖层
完全不使用 u2.screenshot，只使用 u2 做导航点击
"""
import sys
import os
import time
import subprocess
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PIL import Image
from cloud_ocr import ocr_server_url
from utils import connect_device, TB_APP, get_ocr_text_blocks

ADB_PATH = os.environ.get("ADB_PATH", "adb")
d = connect_device()

def adb_screencap_pil():
    """ADB 截图返回 PIL Image"""
    cmd = [ADB_PATH, "-s", str(d.serial), "exec-out", "screencap", "-p"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"  ❌ ADB 截图失败")
        return None
    return Image.open(io.BytesIO(result.stdout))

def adb_ocr_analysis(img):
    """对 ADB 截图做 OCR 分析"""
    import requests as _requests
    if img.mode == "RGBA":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    buf.seek(0)
    files = {"image": ("screenshot.jpg", buf, "image/jpeg")}
    try:
        resp = _requests.post(ocr_server_url(), files=files, timeout=60)
        data = resp.json()
        lines = data.get("data", [])
        lines.sort(key=lambda x: x.get("center", {}).get("y", 0))
        print(f"  📋 ADB截图OCR识别到 {len(lines)} 个文本块:")
        has_task = False
        for item in lines:
            text = item.get("text", "").strip()
            score = item.get("score", 0.0)
            center = item.get("center", {})
            cx = center.get("x", 0)
            cy = center.get("y", 0)
            if text and score >= 0.4:
                print(f"    ({cx:4d},{cy:4d}) [{score:.2f}] {text}")
                if any(kw in text for kw in ["去完成", "专享福利", "赚更多", "浏览", "直播", "逛"]):
                    has_task = True
        if has_task:
            print(f"  ✅ 发现任务列表特征！")
        else:
            print(f"  ⚠️ 未发现任务列表特征")
        return lines
    except Exception as e:
        print(f"  ❌ OCR 失败: {e}")
        return []

print("=" * 60)
print('🔍 验证：ADB截图能否捕获"赚更多金币"覆盖层')
print("=" * 60)

# Step 1: 打开淘宝
print("\n📱 打开淘宝...")
d.app_start(TB_APP, stop=True)
time.sleep(5)

# Step 2: 用 u2 找淘金币入口
print("\n🔍 找淘金币入口...")
found = False
for i in range(8):
    btn_desc = d(description="领淘金币")
    if btn_desc.exists(timeout=1):
        btn_desc.click()
        print(f"  ✅ 点击领淘金币")
        time.sleep(4)
        found = True
        break
    btn_text = d(textContains="淘金币")
    if btn_text.exists(timeout=1):
        btn_text.click()
        print(f"  ✅ 点击淘金币")
        time.sleep(4)
        found = True
        break
    d.swipe(540, 1200, 540, 800)
    time.sleep(1.5)

if not found:
    print("❌ 未找到淘金币入口")
    sys.exit(1)

# Step 3: 直接找"赚更多金币"
print("\n🔍 找【赚更多金币】...")
time.sleep(2)

# ADB 截图看看当前页面（步骤A：点击前）
print("\n📸 [步骤A] 点击前 - ADB截图...")
img_before = adb_screencap_pil()
if img_before:
    img_before.save("adb_before_click.png")
    print(f"  ✅ 截图尺寸: {img_before.size}")

# 点击赚更多金币
earn_btn = d(textContains="赚更多金币")
if earn_btn.exists(timeout=2):
    earn_btn.click()
    print(f"  ✅ 点击【赚更多金币】")
else:
    print("❌ 未找到赚更多金币")
    sys.exit(1)

# 等待覆盖层弹出
time.sleep(2)

# 步骤B：点击后立即 ADB 截图（捕获覆盖层）
print("\n📸 [步骤B] 点击后 - ADB截图（验证覆盖层）...")
img_after = adb_screencap_pil()
if img_after:
    img_after.save("adb_after_click.png")
    print(f"  ✅ 截图尺寸: {img_after.size}")
    print("\n🔍 OCR分析ADB截图（点击后）:")
    adb_ocr_analysis(img_after)
else:
    print("  ❌ ADB截图失败")

# 步骤C：再用 u2.screenshot 看覆盖层是否还在
print("\n📸 [步骤C] 点击后 - u2.screenshot（对比）...")
try:
    u2_img = d.screenshot()
    if u2_img:
        u2_img.save("u2_after_click.png")
        print(f"  ✅ u2截图尺寸: {u2_img.size}")
        import requests as _requests
        buf = io.BytesIO()
        u2_img.save(buf, format="JPEG", quality=70)
        buf.seek(0)
        files = {"image": ("screenshot.jpg", buf, "image/jpeg")}
        resp = _requests.post(ocr_server_url(), files=files, timeout=60)
        data = resp.json()
        lines = data.get("data", [])
        lines.sort(key=lambda x: x.get("center", {}).get("y", 0))
        print(f"  📋 u2截图OCR识别到 {len(lines)} 个文本块:")
        for item in lines:
            text = item.get("text", "").strip()
            score = item.get("score", 0.0)
            center = item.get("center", {})
            cx = center.get("x", 0)
            cy = center.get("y", 0)
            if text and score >= 0.4:
                print(f"    ({cx:4d},{cy:4d}) [{score:.2f}] {text}")
except Exception as e:
    print(f"  ❌ u2截图失败: {e}")

print("\n✅ 调试完成")
print("请对比截图：")
print("  1. adb_before_click.png  - 点击前页面")
print("  2. adb_after_click.png   - 点击后ADB截图")
print("  3. u2_after_click.png    - 点击后u2截图")
