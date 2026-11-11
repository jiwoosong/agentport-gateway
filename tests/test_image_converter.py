import base64
import hashlib
import importlib.util
import io
from pathlib import Path

import httpx
import pytest

Image = pytest.importorskip("PIL.Image")

spec = importlib.util.spec_from_file_location(
    "image_converter", Path(__file__).resolve().parents[1] / "examples/image_converter.py"
)
image_converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_converter)


def image_bytes(format="PNG"):
    image = Image.new("RGB", (5, 3), "red")
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
async def test_photo_converts_to_downloadable_webp_bytes(format):
    body = {"image_base64": base64.b64encode(image_bytes(format)).decode(), "quality": 80}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=image_converter.app), base_url="http://image") as client:
        response = await client.post("/convert", json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        data = base64.b64decode(result["content_base64"], validate=True)
        assert result["filename"] == "converted.webp" and result["media_type"] == "image/webp"
        assert result["sha256"] == hashlib.sha256(data).hexdigest()
        with Image.open(io.BytesIO(data)) as converted:
            assert converted.format == "WEBP" and converted.size == (5, 3)
        assert (await client.post("/convert", json=body)).json() == result


async def test_invalid_and_oversized_images_do_not_echo_input(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=image_converter.app), base_url="http://image") as client:
        assert (await client.post("/convert", json={"image_base64": "not base64!"})).status_code == 422
        private = base64.b64encode(b"PRIVATE-FILE-CONTENTS").decode()
        response = await client.post("/convert", json={"image_base64": private})
        assert response.status_code == 415 and "PRIVATE" not in response.text
        response = await client.post("/convert", json={"image_base64": private, "quality": 0})
        assert response.status_code == 422 and private not in response.text
        monkeypatch.setattr(image_converter, "MAX_IMAGE_BYTES", 2)
        assert (await client.post("/convert", json={"image_base64": private})).status_code == 413


async def test_decompression_limit_and_exif_are_handled(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=image_converter.app), base_url="http://image") as client:
        with monkeypatch.context() as patch:
            patch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
            assert (await client.post("/convert", json={"image_base64": base64.b64encode(image_bytes()).decode()})).status_code == 413
        original = Image.new("RGB", (5, 3), "green")
        exif = original.getexif()
        exif[274], exif[305] = 6, "Private camera metadata"
        encoded = io.BytesIO()
        original.save(encoded, format="JPEG", exif=exif)
        result = (await client.post("/convert", json={"image_base64": base64.b64encode(encoded.getvalue()).decode()})).json()
        with Image.open(io.BytesIO(base64.b64decode(result["content_base64"]))) as converted:
            assert converted.size == (3, 5)
            assert not converted.getexif()
