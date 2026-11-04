"""本机 OCR 服务（RapidOCR 版），实现项目要求的 predict_ocr 契约。

为什么不用 PaddleOCR：
  PaddleOCR 3.x 需要额外安装 paddlepaddle，Windows 上体积大、版本兼容问题多。
  RapidOCR 用的是同一系列 PP-OCR 中英文模型，走 onnxruntime 推理，体积小、部署稳定，
  对手机截图这类短中文文本的识别效果足够。

接口契约与 tools/ocr_compat_server.py 完全一致，任务脚本无需任何改动：
  POST /predict_ocr   表单字段 image（JPEG/PNG），返回
                      {"code":200,"msg":"success","data":[{"text","score","center":{"x","y"}}]}
  GET  /health        返回 {"status":"ready"|"starting","service":"rapidocr-compat"}

用法：
  python tools/ocr_rapid_server.py            # 监听 127.0.0.1:8888
  python tools/ocr_rapid_server.py --port 8899
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("ocr_rapid_server")
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

# 服务标识：ensure_ocr.py 用它判断跑着的是不是新版本，旧版本会被自动换成新的。
# 改推理参数时记得同步 +1，否则用户机器上残留的旧进程不会被替换。
SERVICE_NAME = "rapidocr-compat-v2"

_engine = None
_engine_lock = threading.Lock()
_infer_lock = threading.Lock()


def _infer_threads() -> int:
    """推理线程数。

    实测（1260×2800 全屏、20+ 文本行）：2 线程 0.86s，4 线程 0.71s，
    6 线程 0.70s，8 线程反而 1.00s（线程争抢）。所以固定取 4。
    """
    cpu = os.cpu_count() or 4
    return max(2, min(4, cpu // 2))



class CenterResponse(BaseModel):
    x: int
    y: int


class OcrBlockResponse(BaseModel):
    text: str
    score: float
    center: CenterResponse


class OcrResponse(BaseModel):
    code: int
    msg: str
    data: list[OcrBlockResponse] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str


def load_engine():
    """首次加载模型（约几十 MB，之后走本地缓存）。"""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            threads = _infer_threads()
            LOGGER.info("正在加载 RapidOCR 模型…（推理线程 %d）", threads)
            _engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
            LOGGER.info("RapidOCR 模型加载完成")
    return _engine


def recognize(image_bytes: bytes):
    """把图片字节交给 RapidOCR，转换成标准的文字块列表。"""
    engine = load_engine()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    # rapidocr 期望 BGR ndarray
    array = np.asarray(image)[:, :, ::-1].copy()

    with _infer_lock:
        rows, _ = engine(array)

    blocks = []
    for row in rows or []:
        try:
            box, text, score = row
        except (TypeError, ValueError):
            continue
        text = (text or "").strip()
        if not text:
            continue
        try:
            conf = float(score)
        except (TypeError, ValueError):
            conf = 0.0
        # box 是四个角点，取几何中心
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            cx = int(round(sum(xs) / len(xs)))
            cy = int(round(sum(ys) / len(ys)))
        except (TypeError, ValueError, IndexError):
            continue
        blocks.append(
            OcrBlockResponse(
                text=text,
                score=round(conf, 6),
                center=CenterResponse(x=cx, y=cy),
            )
        )
    return blocks


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # 启动时就把模型加载好，这样 /health 能真实反映可用状态
        import anyio

        LOGGER.info("预热 OCR 模型…")
        await anyio.to_thread.run_sync(load_engine)
        LOGGER.info("OCR 服务就绪")
        yield

    app = FastAPI(
        title="DianTao OCR Service (RapidOCR)",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/", response_model=HealthResponse)
    async def root() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ready" if _engine is not None else "starting",
            service=SERVICE_NAME,
        )

    @app.post("/shutdown")
    async def shutdown():
        """让 ensure_ocr.py 能替换掉旧版本的服务进程。

        只监听 127.0.0.1，仅本机可调用；延迟退出以便先把响应写回去。
        """
        def _exit_later():
            time.sleep(0.3)
            os._exit(0)

        threading.Thread(target=_exit_later, daemon=True).start()
        return {"status": "bye", "service": SERVICE_NAME}

    @app.post("/predict_ocr", response_model=OcrResponse)
    async def predict_ocr(image: UploadFile = File(...)):
        try:
            data = await image.read()
        except Exception as exc:
            return JSONResponse(status_code=400, content=OcrResponse(
                code=400, msg=f"读取上传图片失败：{exc}", data=[]).model_dump())
        finally:
            await image.close()

        if not data:
            return JSONResponse(status_code=400, content=OcrResponse(
                code=400, msg="空图片", data=[]).model_dump())
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse(status_code=400, content=OcrResponse(
                code=400, msg="图片过大", data=[]).model_dump())

        try:
            # 推理是同步阻塞的，丢到线程池，避免卡住事件循环
            import anyio

            blocks = await anyio.to_thread.run_sync(recognize, data)
            return OcrResponse(code=200, msg="success", data=blocks)
        except Exception as exc:  # noqa: BLE001 - HTTP 边界不能泄漏异常
            LOGGER.exception("OCR 识别失败")
            return JSONResponse(status_code=500, content=OcrResponse(
                code=500, msg=f"OCR 服务错误：{exc}", data=[]).model_dump())

    return app


app = create_app()


def main():
    parser = argparse.ArgumentParser(description="本机 OCR 服务（RapidOCR）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--preload", action="store_true", help="启动前先加载模型")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.preload:
        load_engine()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
