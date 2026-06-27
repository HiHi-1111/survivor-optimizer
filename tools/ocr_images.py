"""Local image OCR helpers.

OCR is optional. If Pillow or pytesseract is unavailable, images are still copied
to processed_images and the caller receives a warning instead of failing.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
_EASYOCR_READERS: dict[bool, Any] = {}


def copy_processed_image(source: Path, processed_path: Path) -> Path:
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, processed_path)
    return processed_path


def _easyocr_text(path: Path, use_gpu: bool) -> tuple[str, list[str]]:
    try:
        import easyocr
    except Exception as exc:
        return "", [f"EasyOCR is unavailable: {exc}"]

    try:
        if use_gpu not in _EASYOCR_READERS:
            _EASYOCR_READERS[use_gpu] = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
        results = _EASYOCR_READERS[use_gpu].readtext(str(path), detail=0, paragraph=True)
    except Exception as exc:
        return "", [f"EasyOCR failed: {exc}"]

    return "\n".join(str(item) for item in results).strip(), []


def ocr_image(source: Path, processed_path: Path, device: str = "cpu") -> tuple[str, list[str]]:
    """Copy an image and OCR it if local OCR dependencies are installed."""
    copy_processed_image(source, processed_path)
    warnings: list[str] = []

    if device in {"gpu", "cuda"}:
        text, easy_warnings = _easyocr_text(processed_path, use_gpu=True)
        if text:
            return text, warnings
        warnings.extend(easy_warnings)
        warnings.append("Falling back to CPU OCR.")

    if device == "auto":
        text, easy_warnings = _easyocr_text(processed_path, use_gpu=False)
        if text:
            return text, warnings
        warnings.extend(easy_warnings)

    try:
        from PIL import Image
        import pytesseract
    except Exception:
        return "", warnings + ["Local OCR dependencies are unavailable; image was copied but not OCRed."]

    try:
        with Image.open(processed_path) as image:
            text = pytesseract.image_to_string(image)
    except Exception as exc:
        return "", warnings + [f"OCR failed: {exc}"]

    return text.strip(), warnings
