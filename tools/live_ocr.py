"""
实时截屏 + 云端 OCR 扫描工具
用法:  python live_ocr.py
功能：截取手机当前屏幕 → 上传云端 PaddleOCRv4 → 打印所有文字块及坐标
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from u2_scanner import connect_device
from utils import get_ocr_text_blocks

def main():
    d = connect_device()
    w, h = d.window_size()
    print(f"📱 设备已连接  分辨率: {w} x {h}")
    print(f"\n=== 正在截屏并上传云端 OCR（PaddleOCRv4）===")
    print("   使用 DIANTAO_OCR_SERVER_URL 配置的OCR服务（本机或远程）")

    blocks = get_ocr_text_blocks(d)

    if not blocks:
        print("❌ OCR 未识别到任何文本（当前页面可能无文字或过渡动画）")
        return

    print(f"✅ 识别到 {len(blocks)} 个文本块\n")

    # ===== 打印详细列表 =====
    for i, b in enumerate(blocks):
        text = b["text"]
        cx, cy = b["cx"], b["cy"]
        conf = b["conf"]
        print(f"  [{i:2d}] {text:30s}  @({cx:4d},{cy:4d})  conf={conf:.2f}")

    # ===== 关键词高亮 =====
    keywords = [
        "去完成", "去浏览", "去观看", "去领取", "去玩",
        "猜价格", "浏览精选", "快速领", "签到", "去签到",
        "去看看", "去搜索",
    ]
    found_kw = []
    for b in blocks:
        for kw in keywords:
            if kw in b["text"]:
                found_kw.append((kw, b))
                break

    if found_kw:
        print(f"\n🎯 关键词匹配（共 {len(found_kw)} 个）：")
        for kw, b in found_kw:
            print(f"    '{kw}' → [{b['text']}]  @({b['cx']},{b['cy']})  conf={b['conf']:.2f}")

    # ===== "去完成" + 左侧任务名关联分析 =====
    qwc_blocks = [b for b in blocks if "去完成" in b["text"]]
    if qwc_blocks:
        print(f"\n📋 去完成按钮 + 左侧任务名分析（垂直距离 < 80px）：")
        for q in qwc_blocks:
            print(f"  🎯 去完成 @({q['cx']},{q['cy']})")
            matched = None
            for b in blocks:
                if b["text"] == "去完成" or not b["text"]:
                    continue
                if b["cx"] < q["cx"] - 30 and abs(b["cy"] - q["cy"]) < 80:
                    matched = b["text"]
                    print(f"     左边文本 [{b['text']:30s}] @({b['cx']:4d},{b['cy']:4d})  ← 匹配")
            print(f"     结果: task_name = [{matched}]")
        print()

    print("=== 完成 ===")

if __name__ == "__main__":
    main()
