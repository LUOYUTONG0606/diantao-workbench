"""调用用户配置的本机或远程 OCR HTTP 服务，保持原图坐标。"""
import io
import os

import requests
from local_config import load_config

load_config()


def ocr_server_url():
    value = os.environ.get("DIANTAO_OCR_SERVER_URL", "").strip()
    if not value:
        raise RuntimeError("未配置 OCR 服务：请运行配置本机.cmd，设置自己的 DIANTAO_OCR_SERVER_URL")
    return value


def recognize(image):
    """上传 JPEG；只保留有效文字和置信度不低于0.4的原图中心点。"""
    url = ocr_server_url()
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=70)
    try:
        response = requests.post(
            url,
            files={"image": ("screenshot.jpg", buffer.getvalue(), "image/jpeg")},
            timeout=(5, 45),
            proxies={"http": "", "https": ""},
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError("OCR 服务请求失败，请检查配置的服务和网络；已停止本次任务") from error
    if not isinstance(result, dict) or result.get("code") != 200 or not isinstance(result.get("data"), list):
        raise RuntimeError("OCR 服务返回异常，请检查接口契约；已停止本次任务")
    nodes = []
    for block in result["data"]:
        text = block.get("text", "").strip()
        center = block.get("center", {})
        if text and block.get("score", 0) >= .4 and "x" in center and "y" in center:
            nodes.append(dict(text=text, cx=round(center["x"]), cy=round(center["y"])))
    return nodes
