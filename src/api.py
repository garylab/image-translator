from __future__ import annotations

import os
import traceback
from io import BytesIO
from typing import Optional

from loguru import logger
from PIL import Image

from fastapi import Depends, FastAPI, File, Form, HTTPException, Header, UploadFile
from fastapi.responses import Response

from src.config import settings
from src.google_translate_browser import TranslationUiError, translate_image_google_async

app = FastAPI(title="Image Translate Service", version="0.1.0")
DEBUG_ERRORS = settings.debug_errors


async def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Verify API key if one is configured."""
    if settings.api_key is None:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _infer_format(image_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            return (img.format or "").lower()
    except Exception:
        return ""


def _infer_extension(image_bytes: bytes) -> str:
    fmt = _infer_format(image_bytes)
    if fmt == "jpeg":
        return ".jpg"
    if fmt:
        return f".{fmt}"
    return ".png"


def _infer_media_type(image_bytes: bytes) -> str:
    fmt = _infer_format(image_bytes)
    if fmt == "jpeg":
        return "image/jpeg"
    if fmt == "png":
        return "image/png"
    if fmt == "gif":
        return "image/gif"
    if fmt == "bmp":
        return "image/bmp"
    if fmt == "webp":
        return "image/webp"
    return "application/octet-stream"


def _output_filename(original_name: Optional[str], image_bytes: bytes) -> str:
    ext = _infer_extension(image_bytes)
    if original_name:
        base = os.path.splitext(os.path.basename(original_name))[0]
        return f"{base}_translated{ext}"
    return f"translated{ext}"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/translate", dependencies=[Depends(verify_api_key)])
async def translate(
    file: Optional[UploadFile] = File(default=None),
    timeout_ms: int = Form(default=90000),
) -> Response:
    if file is None:
        raise HTTPException(status_code=400, detail="Provide file.")

    image_bytes = None

    if file is not None:
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        output_bytes = await translate_image_google_async(
            image_bytes=image_bytes,
            headless=settings.headless,
            timeout_ms=timeout_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TranslationUiError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Translate failed")
        detail = str(exc)
        if DEBUG_ERRORS:
            detail = f"{detail}\n{traceback.format_exc()}"
        raise HTTPException(status_code=500, detail=detail) from exc

    media_type = _infer_media_type(output_bytes)
    filename = _output_filename(file.filename if file else None, output_bytes)

    return Response(
        content=output_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
