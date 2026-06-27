"""Extract text from the full local dataset folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import re
import zipfile

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

from tools.ocr_images import IMAGE_EXTENSIONS, ocr_image


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}
DOC_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOC_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS


@dataclass
class ExtractedFile:
    source: str
    source_type: str
    file_type: str
    text: str = ""
    text_path: str | None = None
    processed_path: str | None = None
    warnings: list[str] = field(default_factory=list)


def file_fingerprint(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def source_type_for(path: Path) -> str:
    text = str(path).lower()
    if "official" in text or "game_text" in text:
        return "game"
    if "discord" in text or "guide" in text or path.suffix.lower() in IMAGE_EXTENSIONS:
        return "discord"
    return "unknown"


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _read_pdf(path: Path) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install -r requirements.txt")
    parts: list[str] = []
    with fitz.open(path) as document:
        for page_num, page in enumerate(document, start=1):
            parts.append(f"\n\n--- PAGE {page_num} ---\n\n{page.get_text('text')}")
    return "".join(parts).strip()


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", xml)
    return re.sub(r"\s+", " ", text).strip()


def output_text_path(data_folder: Path, path: Path, extracted_text_dir: Path) -> Path:
    relative = path.relative_to(data_folder)
    return extracted_text_dir / data_folder.name / relative.with_suffix(relative.suffix + ".txt")


def output_processed_image_path(data_folder: Path, path: Path, processed_images_dir: Path) -> Path:
    return processed_images_dir / data_folder.name / path.relative_to(data_folder)


def extract_file(
    path: Path,
    data_folder: Path,
    extracted_text_dir: Path,
    processed_images_dir: Path,
    device: str = "cpu",
) -> ExtractedFile:
    suffix = path.suffix.lower()
    source = str(path.relative_to(data_folder))
    warnings: list[str] = []
    processed_path: Path | None = None

    try:
        if suffix in TEXT_EXTENSIONS:
            text = _read_text_file(path)
        elif suffix in PDF_EXTENSIONS:
            text = _read_pdf(path)
        elif suffix in DOC_EXTENSIONS:
            text = _read_docx(path)
        elif suffix in IMAGE_EXTENSIONS:
            processed_path = output_processed_image_path(data_folder, path, processed_images_dir)
            text, warnings = ocr_image(path, processed_path, device=device)
        else:
            return ExtractedFile(source=source, source_type=source_type_for(path), file_type=suffix or "unknown", warnings=["Unsupported file type."])
    except Exception as exc:
        return ExtractedFile(source=source, source_type=source_type_for(path), file_type=suffix or "unknown", warnings=[f"Extraction failed: {exc}"])

    text_path: Path | None = None
    if text.strip():
        text_path = output_text_path(data_folder, path, extracted_text_dir)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text.strip() + "\n", encoding="utf-8")

    return ExtractedFile(
        source=source,
        source_type=source_type_for(path),
        file_type=suffix or "unknown",
        text=text.strip(),
        text_path=str(text_path) if text_path else None,
        processed_path=str(processed_path) if processed_path else None,
        warnings=warnings,
    )


def scan_files(data_folder: Path) -> list[Path]:
    return sorted(path for path in data_folder.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def dump_extraction_log(path: Path, entries: list[ExtractedFile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([entry.__dict__ for entry in entries], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
