#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smart_navigator.py - 智能导航与异常自愈状态机
============================================================
架构设计：
1. 第一层：异常检测与素材库比对 (State Guard)
   - 快速比对已知特殊情况（弹窗遮罩、关闭X、开屏推荐等）
   - 匹配到异常时按规则自动消除，不增加退出的截流逻辑
2. 第二层：正常流程执行 (Fast AI OCR)
   - 无异常时判定为正常状态，极速定位目标坐标 (cx, cy)
   - 精准点击直达目标界面
3. 遇未知阻碍立刻停下并留存现场
"""

import os
import sys
import time
from yuanbao_popup import handle_yuanbao_live_popups
import json
import re
import random
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from PIL import Image

import uiautomator2 as u2

# 常量与配置
DT_APP = "com.taobao.live"
CODEBUDDY_CLI = r"D:\WORKBUDDY\resources\app.asar.unpacked\cli\dist\codebuddy.js"
VISION_MODEL = "deepseek-v4.1-flash"
TEMP_DIR = (Path(__file__).resolve().parent / ".cb_vision_temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
RULES_FILE = (Path(__file__).resolve().parent / "nav_rules.json")


class SmartNavigator:
    """智能视觉导航器：异常检测过滤 + 快速 AI 目标定位"""

    def __init__(self, device):
        self.d = device
        self.sw, self.sh = self.d.window_size()
        self.rules = self._load_rules()

    def _load_rules(self) -> Dict[str, Any]:
        """加载素材库与规则库"""
        if RULES_FILE.exists():
            try:
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        # 默认初始规则库
        default_rules = {
            "exceptions": [
                {
                    "name": "右上角关闭叉号",
                    "description": "带有关闭、X、跳过的小图标弹窗遮罩",
                    "action": "click_target",
                    "target": "右上角关闭X图标"
                },
                {
                    "name": "开奖遮罩弹窗",
                    "description": "开奖时间到啦或立即开奖等弹窗",
                    "action": "click_target",
                    "target": "关闭或我知道了"
                }
            ],
            "nav_targets": {
                "元宝中心": ["元宝中心", "赚元宝"],
                "打工赚元宝": ["打工赚元宝", "打工"],
                "睡觉赚元宝": ["睡觉赚元宝", "睡觉"],
                "今日签到": ["今日签到", "去看看", "去签到"]
            }
        }
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(default_rules, f, ensure_ascii=False, indent=2)
        return default_rules

    def capture_screen(self, prefix: str = "snap") -> Tuple[str, Image.Image]:
        """截屏并快速压缩至轻量尺寸，便于秒级识别"""
        pil_img = self.d.screenshot()
        w, h = pil_img.size

        # 压缩到宽度 720 等比缩放，质量 65%，文件大小约 80~110KB
        scale = 720.0 / float(w)
        new_w = 720
        new_h = int(h * scale)
        small_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        filepath = TEMP_DIR / f"{prefix}_{int(time.time()*1000)}.jpg"
        small_img.convert("RGB").save(str(filepath), "JPEG", quality=65)
        return str(filepath), pil_img

    def query_vision_fast(self, img_path: str, prompt: str) -> str:
        """极速单轮调用 CodeBuddy 多模态识别"""
        cmd = [
            "node",
            CODEBUDDY_CLI,
            "-y",
            "--strict-mcp-config",
            "--mcp-config", "{}",
            "--model", VISION_MODEL,
            "--tools", "Read",
            "-p", prompt
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                cwd=Path(__file__).resolve().parent
            )
            return res.stdout.strip()
        except subprocess.TimeoutExpired:
            print("  ⚠️ [AI Vision] 视觉单轮识别超时 (30s)")
            return ""
        except Exception as e:
            print(f"  ⚠️ [AI Vision] 视觉调用异常: {e}")
            return ""

    def locate_target(self, target_name: str, max_retries: int = 2) -> Optional[Tuple[int, int]]:
        """
        第二层核心：快速 AI OCR 定位目标屏幕像素坐标 (cx, cy)
        """
        for attempt in range(max_retries):
            img_path, original_img = self.capture_screen(prefix=f"find_{target_name}")
            orig_w, orig_h = original_img.size

            prompt = (
                f"请使用Read工具读取图片：{img_path}。\n"
                f"请快速找到目标【{target_name}】（可能是按钮、文字、或金元宝图标）的中心位置。\n"
                f"请严格只输出目标在图片宽度和高度上的百分比（0到100），格式形如：\n"
                f"x:百分比, y:百分比\n"
                f"例如：x:48, y:56\n"
                f"如果完全不存在该目标，请输出：未找到"
            )

            t0 = time.time()
            out = self.query_vision_fast(img_path, prompt)
            cost = time.time() - t0

            # 清理临时图
            try:
                os.remove(img_path)
            except Exception:
                pass

            # 解析百分比
            m = re.search(r"x\s*[:：=]\s*(\d+(?:\.\d+)?)[,\s，]+y\s*[:：=]\s*(\d+(?:\.\d+)?)", out, re.I)
            if m:
                px = float(m.group(1)) / 100.0
                py = float(m.group(2)) / 100.0
                real_cx = int(orig_w * px)
                real_cy = int(orig_h * py)
                print(f"  🎯 [AI OCR] 成功定位【{target_name}】 @({real_cx}, {real_cy}) (耗时: {cost:.2f}s)")
                return real_cx, real_cy

            print(f"  ⚠️ [AI OCR] 未能识别到【{target_name}】 (耗时: {cost:.2f}s, 输出: {out[:60]})")
            time.sleep(1)

        return None

    def check_and_handle_exceptions(self) -> bool:
        """
        第一层核心：状态判断与异常素材库比对
        若检测到异常遮罩/弹窗，则按规则解决；若为正常界面则返回 False
        """
        img_path, original_img = self.capture_screen(prefix="check_state")
        orig_w, orig_h = original_img.size

        prompt = (
            f"请使用Read工具读取图片：{img_path}。\n"
            f"请判断当前画面状态：\n"
            f"1. 是否存在阻塞操作的遮罩弹窗、开奖弹窗、签到遮罩、或右上角有关闭'X'？\n"
            f"如果有，请输出：异常:弹窗名称, x:关闭按钮X百分比, y:关闭按钮Y百分比\n"
            f"2. 如果是正常的点淘页面（首页/元宝中心/任务列表），请只输出：正常"
        )

        t0 = time.time()
        out = self.query_vision_fast(img_path, prompt)
        cost = time.time() - t0

        try:
            os.remove(img_path)
        except Exception:
            pass

        if "异常" in out:
            print(f"  🚨 [状态检测] 发现异常状态: {out[:80]} (耗时: {cost:.2f}s)")
            m = re.search(r"x\s*[:：=]\s*(\d+(?:\.\d+)?)[,\s，]+y\s*[:：=]\s*(\d+(?:\.\d+)?)", out, re.I)
            if m:
                close_x = int(orig_w * (float(m.group(1)) / 100.0))
                close_y = int(orig_h * (float(m.group(2)) / 100.0))
                print(f"  🛠️ [规则处理] 点击消除弹窗遮罩 @({close_x}, {close_y})...")
                self.d.click(close_x, close_y)
                time.sleep(2)
                return True
        else:
            print(f"  🟢 [状态检测] 页面状态正常 (耗时: {cost:.2f}s)")

        return False

    def enter_target(self, target_name: str) -> bool:
        """完整进入目标：异常过滤 -> 正常快速定位 -> 点击进入"""
        print(f"\n👉 [智能进入] 目标：【{target_name}】")

        # 1. 状态判断与异常检测
        self.check_and_handle_exceptions()

        # 2. 正常流程定位目标
        pos = self.locate_target(target_name)
        if pos:
            cx, cy = pos
            print(f"  👆 点击进入【{target_name}】 @({cx}, {cy})...")
            self.d.click(cx, cy)
            time.sleep(4)
            if target_name == "元宝中心":
                return handle_yuanbao_live_popups(self.d)
            return True

        # 3. 若找不到目标，保留现场并停止
        err_path = TEMP_DIR / f"stuck_before_{target_name}.png"
        self.d.screenshot().save(str(err_path))
        print(f"\n🛑 [执行中断] 无法定位【{target_name}】，已停下。现场截图：{err_path}")
        return False
