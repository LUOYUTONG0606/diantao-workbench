"""本机或自建 PaddleOCR 的 HTTP 兼容服务，不依赖云厂商。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from typing import AsyncIterator, Iterable, Protocol, cast

import anyio
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tools.ocr_compat_contract import JsonValue, OcrBlock, normalize_paddle_result


LOGGER = logging.getLogger("ocr_compat_server")
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
TEMP_FILE_DIRECTORY = gettempdir()


class OcrInputError(Exception):
    """Raised when an uploaded image cannot be accepted."""


class OcrInferenceError(Exception):
    """Raised when PaddleOCR cannot process an accepted image."""


class PaddleResultLike(Protocol):
    @property
    def json(self) -> dict[str, JsonValue]: ...


class PaddleEngineLike(Protocol):
    def predict(self, input: str) -> Iterable[PaddleResultLike]: ...


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


def _load_engine() -> PaddleEngineLike:
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MALLOC_ARENA_MAX", "2")

    from paddleocr import PaddleOCR

    engine = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
        enable_mkldnn=True,
        mkldnn_cache_capacity=1,
        cpu_threads=2,
        text_recognition_batch_size=1,
        text_det_limit_side_len=1280,
        text_det_limit_type="max",
    )
    return cast(PaddleEngineLike, engine)


def _run_engine(engine: PaddleEngineLike, image_path: str) -> list[OcrBlock]:
    try:
        result = next(iter(engine.predict(image_path)), None)
    except (OSError, RuntimeError, ValueError) as exc:
        raise OcrInferenceError(f"PaddleOCR inference failed: {exc}") from exc

    if result is None:
        return []

    try:
        payload = result.json
    except (AttributeError, TypeError, ValueError) as exc:
        raise OcrInferenceError(f"PaddleOCR returned an invalid result: {exc}") from exc

    return normalize_paddle_result(payload)


async def _save_upload(upload: UploadFile) -> Path:
    filename = upload.filename or "screenshot.jpg"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        suffix = ".jpg"

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix="ocr-",
            suffix=suffix,
            dir=TEMP_FILE_DIRECTORY,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            total_bytes = 0
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise OcrInputError("uploaded image is too large")
                handle.write(chunk)
    except OcrInputError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise OcrInputError(f"could not store uploaded image: {exc}") from exc

    if temporary_path is None:
        raise OcrInputError("could not create a temporary image file")
    return temporary_path


def _response_from_blocks(blocks: list[OcrBlock]) -> OcrResponse:
    return OcrResponse(
        code=200,
        msg="success",
        data=[
            OcrBlockResponse(
                text=block.text,
                score=block.score,
                center=CenterResponse(x=block.center.x, y=block.center.y),
            )
            for block in blocks
        ],
    )


def _error_response(status_code: int, message: str) -> JSONResponse:
    payload = OcrResponse(code=status_code, msg=message, data=[])
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_app(engine: PaddleEngineLike | None = None) -> FastAPI:
    active_engine = engine
    inference_lock = anyio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal active_engine
        LOGGER.info("loading PaddleOCR engine")
        if active_engine is None:
            active_engine = await anyio.to_thread.run_sync(_load_engine)
        LOGGER.info("PaddleOCR engine is ready")
        yield
        active_engine = None

    app = FastAPI(
        title="DianTao OCR Compatibility Service",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/", response_model=HealthResponse)
    async def root() -> HealthResponse:
        return HealthResponse(status="ok", service="paddleocr-compat")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        status = "ready" if active_engine is not None else "starting"
        return HealthResponse(status=status, service="paddleocr-compat")

    @app.post("/predict_ocr", response_model=OcrResponse)
    async def predict_ocr(image: UploadFile = File(...)) -> OcrResponse | JSONResponse:
        image_path: Path | None = None
        try:
            image_path = await _save_upload(image)
            async with inference_lock:
                if active_engine is None:
                    raise OcrInferenceError("PaddleOCR engine is not ready")
                blocks = await anyio.to_thread.run_sync(
                    _run_engine,
                    active_engine,
                    str(image_path),
                )
            return _response_from_blocks(blocks)
        except OcrInputError as exc:
            LOGGER.warning("invalid OCR request: %s", exc)
            return _error_response(400, str(exc))
        except OcrInferenceError as exc:
            LOGGER.error("OCR request failed: %s", exc)
            return _error_response(500, str(exc))
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - HTTP boundary must not leak worker exceptions
            LOGGER.exception("unexpected OCR request failure")
            return _error_response(500, f"OCR service error: {exc}")
        finally:
            if image_path is not None:
                image_path.unlink(missing_ok=True)
            await image.close()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8888)
