"""Extract readable text from legacy sources into data_sources/extracted/text."""

from __future__ import annotations

from pathlib import Path

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data_sources" / "legacy"
OUT_DIR = ROOT / "data_sources" / "extracted" / "text"
SUPPORTED = {".pdf", ".txt", ".md"}


def extract_pdf(path: Path) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed. Run: pip install -r requirements.txt")
    parts: list[str] = []
    with fitz.open(path) as document:
        for page_num, page in enumerate(document, start=1):
            parts.append(f"\n\n--- PAGE {page_num} ---\n\n{page.get_text('text')}")
    return "".join(parts).strip()


def extract_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def extract_all() -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for path in RAW_DIR.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep" or path.suffix.lower() not in SUPPORTED:
            continue

        try:
            text = extract_pdf(path) if path.suffix.lower() == ".pdf" else extract_text_file(path)
        except Exception as exc:
            print(f"warning: skipped {path.relative_to(ROOT)}: {exc}")
            continue

        if not text.strip():
            print(f"warning: no text extracted from {path.relative_to(ROOT)}")
            continue

        output_path = OUT_DIR / path.relative_to(RAW_DIR).with_suffix(".txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
        written.append(output_path)
        print(f"wrote {output_path.relative_to(ROOT)}")

    return written


if __name__ == "__main__":
    extract_all()
