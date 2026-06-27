from pathlib import Path
import subprocess
import sys

from tools.check_ai_output import check_ai_outputs
from tools.compile_knowledge import compile_knowledge


ROOT = Path(__file__).resolve().parents[1]


def test_check_ai_output_reads_core_test_json():
    assert check_ai_outputs(ROOT / "data_sources" / "extracted" / "ai_outputs") == 0


def test_compile_and_validate_core_test_output():
    summary = compile_knowledge()
    assert summary["files_processed"] >= 1

    result = subprocess.run(
        [sys.executable, "tools/validate_knowledge.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_compiled_core_test_records_exist():
    compile_knowledge()
    resources = (ROOT / "knowledge" / "resources.json").read_text(encoding="utf-8")
    chests = (ROOT / "knowledge" / "chests.json").read_text(encoding="utf-8")
    rules = (ROOT / "knowledge" / "rules.json").read_text(encoding="utf-8")

    assert "astral_core" in resources
    assert "xeno_core" in resources
    assert "resonance_chip" in resources
    assert "core_selector_chest" in chests
    assert rules.count('"category": "rule"') >= 3
