"""Honest optional NPU inference backend detection."""

from __future__ import annotations

from typing import Any


NPU_PROVIDERS = ("VitisAIExecutionProvider",)


class NpuBackend:
    def __init__(self) -> None:
        self.available = False
        self.provider: str | None = None
        self.providers: list[str] = []
        self.reason = "NPU unavailable for this Python path; using CPU/GPU."
        try:
            import onnxruntime as ort
            self.providers = list(ort.get_available_providers())
        except Exception as exc:
            self.reason = f"NPU unavailable for this Python path; using CPU/GPU. onnxruntime import failed: {exc}"
            return
        self.provider = next((provider for provider in NPU_PROVIDERS if provider in self.providers), None)
        if self.provider:
            self.available = True
            self.reason = f"NPU-capable ONNX provider detected: {self.provider}. No ranker model loaded."
        elif "DmlExecutionProvider" in self.providers:
            self.reason = "DirectML is available, but that does not prove AMD XRT/VitisAI NPU execution; using CPU/GPU."

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available, "provider": self.provider, "providers": self.providers,
            "active": False, "reason": self.reason,
            "idle_reason": "no_onnx_ranker_model" if self.available else "backend_unavailable",
        }
