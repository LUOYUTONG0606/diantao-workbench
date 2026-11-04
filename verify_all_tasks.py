#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_all_tasks.py - CodeBuddy 多模态任务改造全面端到端验证
"""

import os
import sys
import time
from pathlib import Path
from PIL import Image

# 导入改造后的 CodeBuddy 视觉模块
from codebuddy_vision import (
    CodeBuddyVision,
    codebuddy_ocr,
    find_text_by_codebuddy,
    paddle_ocr_cb,
    MODEL_FLASH,
    MODEL_DEEPSEEK,
)

def step1_verify_local_image():
    print("\n--------------------------------------------------")
    print("🧪 Step 1: 验证本地样本图像的多模态定位与识别")
    print("--------------------------------------------------")
    sample_img = str(Path(__file__).resolve().parent / "img" / "click_btn.png")
    if not os.path.exists(sample_img):
        print(f"❌ 样本图片不存在: {sample_img}")
        return False

    t0 = time.time()
    pos = find_text_by_codebuddy(sample_img, "去完成")
    cost = time.time() - t0
    print(f"  定位结果: {pos} (耗时: {cost:.2f}s)")
    if pos and len(pos) == 2:
        print("  ✅ 目标定位成功！")
    else:
        print("  ❌ 目标定位失败")
        return False

    im = Image.open(sample_img)
    blocks = codebuddy_ocr(im)
    print(f"  文字块列表 (共 {len(blocks)} 个): {blocks}")
    if blocks:
        print("  ✅ 多模态文字块解析成功！")
        return True
    return False

def step2_verify_phone_screen():
    print("\n--------------------------------------------------")
    print("🧪 Step 2: 验证真实连接手机的实时屏幕截图与多模态理解")
    print("--------------------------------------------------")
    try:
        import uiautomator2 as u2
        d = u2.connect()
        print(f"  📱 成功连接当前设备: {d.serial}")

        temp_screen = str(Path(__file__).resolve().parent / ".cb_vision_temp" / "live_phone_test.jpg")
        img = d.screenshot()
        img.save(temp_screen)
        print(f"  📸 成功截取手机实时画面: {img.size[0]}x{img.size[1]}")

        print("  🧠 正在通过 CodeBuddy (Flash) 进行全屏多模态结构化理解...")
        t0 = time.time()
        blocks = codebuddy_ocr(img)
        cost = time.time() - t0
        print(f"  ✅ CodeBuddy 识别完成 (耗时: {cost:.2f}s)，识别出 {len(blocks)} 个元素与文字块！")
        for b in blocks[:5]:
            print(f"    - 【{b['text']}】 坐标: ({b['cx']}, {b['cy']}), 置信度: {b['conf']}")

        if len(blocks) > 5:
            print(f"    ... 以及另外 {len(blocks) - 5} 个元素")

        return True
    except Exception as e:
        print(f"  ⚠️ 设备连接或截屏异常: {e}")
        return False

def step3_verify_task_syntax():
    print("\n--------------------------------------------------")
    print("🧪 Step 3: 验证核心任务脚本的导入与兼容性")
    print("--------------------------------------------------")
    scripts_to_check = [
        "utils.py",
        "codebuddy_vision.py",
        "赚钱卡.py",
        "签到赢元宝.py",
        "元宝中心小任务.py",
        "打工得体力.py",
        "点淘购物赚钱.py",
    ]

    all_ok = True
    for s in scripts_to_check:
        full_p = str(Path(__file__).resolve().parent / s)
        try:
            with open(full_p, "r", encoding="utf-8") as f:
                content = f.read()
            compile(content, full_p, "exec")
            print(f"  ✅ 语法检查通过: {s}")
        except Exception as e:
            print(f"  ❌ 语法错误: {s} -> {e}")
            all_ok = False

    return all_ok

def main():
    print("==================================================")
    print("🚀 开始 CodeBuddy 多模态版本全功能验证")
    print("==================================================")

    r1 = step1_verify_local_image()
    r3 = step3_verify_task_syntax()
    r2 = step2_verify_phone_screen()

    print("\n==================================================")
    print(f"📊 验证总览: 样本测试: {'通过' if r1 else '失败'} | 语法检查: {'通过' if r3 else '失败'} | 真机屏幕: {'通过' if r2 else '失败'}")
    print("==================================================")

if __name__ == "__main__":
    main()
