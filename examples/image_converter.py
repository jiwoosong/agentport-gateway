"""CPU-only PNG/JPEG to WebP tool. Images stay in memory; no model or GPU is used."""

import base64
import hashlib
import io
import warnings

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
app = FastAPI(title="Image to WebP")
invocations = 0


class ConversionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=1, max_length=MAX_BASE64_LENGTH)
    quality: int = Field(default=80, ge=1, le=100, strict=True)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    # Validation responses must not echo uploaded bytes.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]})


@app.get("/health")
def health():
    return {"ok": True, "invocations": invocations, "processor": "cpu"}


@app.post("/convert")
def convert(value: ConversionInput):
    global invocations
    try:
        data = base64.b64decode(value.image_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "Supply a base64-encoded PNG or JPEG image") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Maximum image size is 20 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise HTTPException(413, "Maximum decoded image size is 16 megapixels")
                if getattr(source, "n_frames", 1) != 1:
                    raise HTTPException(422, "Animated images are not supported")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
                image = oriented.convert(mode)
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=value.quality, method=4)
                width, height = image.size
                encoded = output.getvalue()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "Maximum decoded image size is 16 megapixels") from None
    except UnidentifiedImageError:
        raise HTTPException(415, "Only PNG and JPEG images are supported") from None
    except (OSError, SyntaxError, ValueError):
        raise HTTPException(422, "The image could not be decoded") from None
    if len(encoded) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Converted image exceeds the 20 MiB result limit")
    invocations += 1
    return {
        "filename": "converted.webp",
        "media_type": "image/webp",
        "content_base64": base64.b64encode(encoded).decode("ascii"),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "width": width,
        "height": height,
    }
