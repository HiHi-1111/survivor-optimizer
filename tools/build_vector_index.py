"""Optional GPU/CPU embedding index builder for training outputs.

This is retrieval support, not a black-box recommender. The optimizer can still
run without this file or without PyTorch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def build_vector_index(
    chunks: list[dict[str, Any]],
    output_path: Path,
    device: str = "cpu",
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 64,
) -> dict[str, Any]:
    if not chunks:
        summary = {"enabled": False, "reason": "no chunks", "records": 0}
        output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        summary = {
            "enabled": False,
            "reason": f"sentence-transformers is unavailable: {exc}",
            "records": 0,
        }
        output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    model_device = "cuda" if device == "cuda" else "cpu"
    model = SentenceTransformer(model_name, device=model_device)
    texts = [str(chunk.get("text", "")) for chunk in chunks]
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    records = []
    for chunk, embedding in zip(chunks, embeddings):
        records.append(
            {
                "id": chunk.get("id", ""),
                "source": chunk.get("source", ""),
                "model": model_name,
                "device": model_device,
                "embedding": [round(float(value), 6) for value in embedding.tolist()],
            }
        )

    output = {
        "enabled": True,
        "model": model_name,
        "device": model_device,
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return {"enabled": True, "model": model_name, "device": model_device, "records": len(records)}
