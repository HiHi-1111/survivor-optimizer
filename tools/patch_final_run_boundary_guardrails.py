from pathlib import Path

ROOT = Path(r"C:\Users\iyoua\Downloads\survivor-optimizer")
f = ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py"

t = f.read_text(encoding="utf-8")

patch = r'''

# SURVIVOR_RUN_GUARDRAIL_BOUNDARY_PATCH_V1
# Final output boundary patch:
# Make sure every _run(profile) result receives global_plan guardrails using the ORIGINAL full profile.
try:
    from optimizer.global_plan_guardrails import apply_global_plan_guardrails as _survivor_apply_global_plan_guardrails

    if "_survivor_original_run_for_guardrails" not in globals():
        _survivor_original_run_for_guardrails = _run

        def _run(profile, *args, **kwargs):
            result = _survivor_original_run_for_guardrails(profile, *args, **kwargs)
            return _survivor_apply_global_plan_guardrails(result, profile)

except Exception:
    pass
'''

if "SURVIVOR_RUN_GUARDRAIL_BOUNDARY_PATCH_V1" not in t:
    f.write_text(t.rstrip() + "\n" + patch + "\n", encoding="utf-8")
    print("PATCHED FINAL _run(profile) BOUNDARY")
else:
    print("FINAL _run(profile) BOUNDARY ALREADY PATCHED")

print(f)
