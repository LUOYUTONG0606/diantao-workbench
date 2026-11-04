#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codebuddy_vision.py - CodeBuddy 多模态视觉驱动模块
===================================================
全面替代传统本地/云端 OCR (PaddleOCR, EasyOCR, ddddocr)，
通过 CodeBuddy 多模态大模型 (Flash, DeepSeek, V4.1) 直接理解手机截屏：
1. 智能文字与按钮定位 (返回物理屏幕坐标 cx, cy)
2. 语义图标识别 (无需文字亦可精准定位返回按钮、关闭X、分享等)
3. 兼容旧版 OCR 接口 (get_text_blocks, find_text_position)
4. 页面类型与活动状态理解 (分类当前页面、弹窗类型、猜价格决策)
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

# CodeBuddy CLI 可执行脚本路径
CODEBUDDY_CLI = r"D:\WORKBUDDY\resources\app.asar.unpacked\cli\dist\codebuddy.js"

# 支持的官方/自定义模型名称
MODEL_FLASH = "glm-5.3-flash"
MODEL_DEEPSEEK = "deepseek-v4-flash"
MODEL_V41 = "custom-local:antigravity/claude-sonnet-4-6"
MODEL_GEMINI_FLASH = "custom-local:gemini-3.7-flash-high"

DEFAULT_MODEL = MODEL_FLASH


class CodeBuddyVision:
    """CodeBuddy 视觉驱动多模态识别与定位引擎"""

    def __init__(
        self,
        cli_path: str = CODEBUDDY_CLI,
        default_model: str = DEFAULT_MODEL,
        temp_dir: Optional[str] = None,
        timeout: int = 60,
    ):
        self.cli_path = os.path.abspath(cli_path)
        self.default_model = default_model
        self.timeout = timeout

        # 确保用于 CodeBuddy 读取的临时图片目录在项目或工作区受控范围内
        if temp_dir is None:
            base_dir = Path(__file__).resolve().parent
            self.temp_dir = base_dir / ".cb_vision_temp"
        else:
            self.temp_dir = Path(temp_dir).resolve()

        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _prepare_image_file(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
        prefix: str = "screen",
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[str, int, int, int, int]:
        """
        统一将各种格式的图像保存到受控临时文件供 CodeBuddy 读取
        返回: (abs_path, width, height, offset_x, offset_y)
        """
        offset_x, offset_y = 0, 0

        if isinstance(image_input, (str, Path)):
            pil_img = Image.open(str(image_input))
        elif isinstance(image_input, Image.Image):
            pil_img = image_input
        elif isinstance(image_input, np.ndarray):
            # 兼容 OpenCV BGR 或 RGB
            if len(image_input.shape) == 3 and image_input.shape[2] == 3:
                pil_img = Image.fromarray(cv2.cvtColor(image_input, cv2.COLOR_BGR2RGB))
            else:
                pil_img = Image.fromarray(image_input)
        else:
            raise ValueError(f"Unsupported image input type: {type(image_input)}")

        if region is not None:
            rx, ry, rw, rh = region
            pil_img = pil_img.crop((rx, ry, rx + rw, ry + rh))
            offset_x, offset_y = rx, ry

        width, height = pil_img.size

        # 压缩保存以提高传输与模型解析速度 (保持最高 1280 边长)
        max_side = 1440
        scale = 1.0
        if max(width, height) > max_side:
            scale = max_side / float(max(width, height))
            new_w = int(width * scale)
            new_h = int(height * scale)
            resized_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        else:
            resized_img = pil_img

        filename = f"{prefix}_{int(time.time() * 1000)}.jpg"
        save_path = self.temp_dir / filename

        # 转换为 RGB 后保存为 85% 质量 JPEG
        if resized_img.mode != "RGB":
            resized_img = resized_img.convert("RGB")
        resized_img.save(str(save_path), format="JPEG", quality=85)

        return str(save_path), width, height, offset_x, offset_y

    def _invoke_cli(self, prompt: str, model: Optional[str] = None) -> str:
        """调用 CodeBuddy CLI 获取回答"""
        use_model = model or self.default_model
        cmd = [
            "node",
            self.cli_path,
            "--permission-mode", "acceptEdits",
            "--model", use_model,
            "--tools", "Read",
            "-p", prompt,
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=str(self.temp_dir.parent),
            )
            output = res.stdout.strip()
            return output
        except subprocess.TimeoutExpired:
            print(f"  ⚠️ [CodeBuddy Vision] 调用超时 ({self.timeout}s)")
            return ""
        except Exception as e:
            print(f"  ⚠️ [CodeBuddy Vision] 调用异常: {e}")
            return ""

    def _extract_json(self, text: str) -> Optional[Any]:
        """从模型输出中稳健提取 JSON 数据结构"""
        if not text:
            return None

        # 尝试直接解析
        try:
            return json.loads(text)
        except Exception:
            pass

        # 匹配 ```json ... ``` 块
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass

        # 匹配大括号或中括号范围
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass

        return None

    def find_target(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
        target_description: str,
        model: Optional[str] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        精确寻找指定目标元素（文字、按钮、图标、弹窗关闭等）的中心点物理坐标 (cx, cy)
        参数:
            image_input: 图片（文件路径、PIL Image、numpy array）
            target_description: 目标描述（如 "去浏览"、"去完成"、"关闭弹窗的X"、"右上角返回箭头"、"签到"）
            model: 使用的模型，默认 Flash
            region: 搜索区域 (x, y, w, h)，可选
        返回: (cx, cy) 或 None
        """
        img_path, orig_w, orig_h, off_x, off_y = self._prepare_image_file(
            image_input, prefix="find_target", region=region
        )

        prompt = f"""你是一个顶级的移动端自动化视觉定位引擎。
请使用 Read 工具读取此手机界面截图：{img_path}
原始分辨率为宽度 {orig_w} 像素，高度 {orig_h} 像素。

【任务】
请在截图中寻找："{target_description}"。
无论该目标是纯文字、带边框的按钮、图标（如返回箭头、关闭叉叉、红包图标）、还是图形化元素，请准确定位其中心点所在的物理像素坐标 (cx, cy)。

【输出规范】
请严格输出纯 JSON，不要附加任何解释或 markdown 说明：
如果找到目标：
{{"found": true, "name": "{target_description}", "cx": 540, "cy": 1200, "conf": 0.98}}
如果完全不存在该目标：
{{"found": false, "reason": "未在屏幕中发现匹配元素"}}
"""

        raw_output = self._invoke_cli(prompt, model=model)
        data = self._extract_json(raw_output)

        # 清理临时文件
        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass

        if not data or not isinstance(data, dict):
            return None

        if data.get("found"):
            cx = data.get("cx")
            cy = data.get("cy")
            if cx is not None and cy is not None:
                final_x = int(cx) + off_x
                final_y = int(cy) + off_y
                print(f"  🎯 [CodeBuddy Vision] 找到 '{target_description}': ({final_x}, {final_y}) [置信度: {data.get('conf', 1.0)}]")
                return (final_x, final_y)

        return None

    def get_text_blocks(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
        model: Optional[str] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
        min_conf: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        全量获取屏幕中的文字与关键控件列表（100% 兼容原 _cloud_ocr / PaddleOCR 格式）
        返回: [{"text": str, "cx": int, "cy": int, "conf": float}, ...]
        """
        img_path, orig_w, orig_h, off_x, off_y = self._prepare_image_file(
            image_input, prefix="get_blocks", region=region
        )

        prompt = f"""你是一个专业的移动端 OCR 与界面结构识别引擎。
请使用 Read 工具读取此手机界面截图：{img_path}
原始分辨率为宽度 {orig_w} 像素，高度 {orig_h} 像素。

【任务】
请全面识别屏幕上所有可见的文字、按钮、可点击标签，并计算它们各自的中心点像素坐标 (cx, cy)。

【输出格式】
请严格输出纯 JSON，格式如下：
{{
  "blocks": [
    {{"text": "识别到的文字或按钮名称", "cx": 100, "cy": 200, "conf": 0.95}}
  ]
}}
"""

        raw_output = self._invoke_cli(prompt, model=model)
        data = self._extract_json(raw_output)

        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass

        if not data or not isinstance(data, dict):
            return []

        raw_blocks = data.get("blocks", [])
        blocks = []
        for item in raw_blocks:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            cx = item.get("cx", 0)
            cy = item.get("cy", 0)
            conf = float(item.get("conf", 0.9))
            if not text or conf < min_conf:
                continue

            blocks.append({
                "text": text,
                "cx": int(cx) + off_x,
                "cy": int(cy) + off_y,
                "conf": conf,
            })

        return blocks

    def paddle_ocr(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
        model: Optional[str] = None,
    ) -> str:
        """
        识别截图中所有文字并拼接返回（完全兼容原 utils.paddle_ocr / easy_ocr）
        """
        img_path, orig_w, orig_h, _, _ = self._prepare_image_file(
            image_input, prefix="ocr_full"
        )

        prompt = f"""请使用 Read 工具读取此图片：{img_path}
请识别图中所有的文字内容，将全部文本按自然阅读顺序提取出来。
直接输出提取到的文本内容，不要包含任何前缀、解释或 markdown 标记。
"""
        raw_output = self._invoke_cli(prompt, model=model)

        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass

        cleaned = re.sub(r"```[\s\S]*?```", "", raw_output).strip()
        print(f"  📝 [CodeBuddy Vision] 提取文字: {cleaned[:60]}...")
        return cleaned

    def classify_screen(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
        candidates: List[str],
        model: Optional[str] = None,
    ) -> str:
        """
        对当前手机屏幕进行语义分类，判断属于哪个页面或活动状态
        参数:
            candidates: 候选分类列表，如 ["点淘元宝中心", "任务列表弹窗", "直播间", "商品浏览页", "未知"]
        """
        img_path, orig_w, orig_h, _, _ = self._prepare_image_file(
            image_input, prefix="classify"
        )

        cand_str = "、".join([f'"{c}"' for c in candidates])
        prompt = f"""请使用 Read 工具读取手机截图：{img_path}
请分析当前屏幕属于以下哪个分类：[{cand_str}]。
输出格式必须为纯 JSON：
{{"category": "最匹配的分类名称", "confidence": 0.95, "detail": "简短分析理由"}}
"""
        raw_output = self._invoke_cli(prompt, model=model)
        data = self._extract_json(raw_output)

        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass

        if data and isinstance(data, dict):
            cat = data.get("category", "")
            if cat in candidates:
                return cat

        return candidates[-1] if candidates else "未知"

    def analyze_guess_price(
        self,
        image_input: Union[str, Path, Image.Image, np.ndarray],
        model: str = MODEL_DEEPSEEK,
    ) -> Dict[str, Any]:
        """
        点淘猜价格专门视觉决策（结合 DeepSeek 逻辑推理能力）
        识别商品名称、价格参考线索、当前出价框位置、高了/低了提示与确认按钮
        """
        img_path, orig_w, orig_h, _, _ = self._prepare_image_file(
            image_input, prefix="guess_price"
        )

        prompt = f"""你是一个点淘“猜价格”游戏的专家辅助。
请使用 Read 工具读取截图：{img_path}
分辨率: {orig_w}x{orig_h}。

【分析要点】
1. 识别当前商品名称与价格范围线索（如标价、打折信息）。
2. 判断当前状态：是出价中、提示“高了”、提示“低了”、还是“猜中了/结束”？
3. 定位关键控件坐标：
   - 出价输入框/出价键盘中心 (cx, cy)
   - 确认出价按钮中心 (cx, cy)
   - 继续/领取奖励按钮中心 (cx, cy)
4. 给出下一次出价的推荐整数值。

【输出纯 JSON 格式】
{{
  "status": "bidding | too_high | too_low | won | ended",
  "product_name": "商品名",
  "suggested_price": 50,
  "input_box_pos": [cx, cy],
  "confirm_btn_pos": [cx, cy],
  "next_btn_pos": [cx, cy]
}}
"""
        raw_output = self._invoke_cli(prompt, model=model)
        data = self._extract_json(raw_output)

        try:
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass

        return data if isinstance(data, dict) else {}


# 全局默认单例
vision_engine = CodeBuddyVision()


# ========================================================
# 兼容旧版 utils.py / u2_scanner.py 的无缝替换函数
# ========================================================

def codebuddy_ocr(screenshot_pil, retries: int = 2) -> List[Dict[str, Any]]:
    """
    完全替代旧版 _cloud_ocr() 的多模态驱动函数
    输入: PIL Image 对象
    返回: [{"text": str, "cx": int, "cy": int, "conf": float}, ...]
    """
    for attempt in range(retries + 1):
        try:
            blocks = vision_engine.get_text_blocks(screenshot_pil)
            if blocks:
                return blocks
        except Exception as e:
            print(f"  ⚠️ [CodeBuddy Vision] 识别失败 (第 {attempt + 1} 次): {e}")
            time.sleep(1.0)
    return []


def find_text_by_codebuddy(screenshot, target_text: str) -> Optional[Tuple[int, int]]:
    """
    完全替代旧版 find_text_by_paddleocr() 的多模态驱动函数
    输入: 截图 (PIL / numpy), 目标文本或语义描述
    返回: 中心坐标 (cx, cy) 或 None
    """
    return vision_engine.find_target(screenshot, target_text)


def paddle_ocr_cb(image) -> str:
    """
    完全替代旧版 paddle_ocr() / easy_ocr() 的多模态文本提取函数
    """
    return vision_engine.paddle_ocr(image)
