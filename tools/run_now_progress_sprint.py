import importlib.util, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

gauntlet_path = ROOT / "tools" / "run_neverending_goal_gauntlet.py"
spec = importlib.util.spec_from_file_location("gauntlet", gauntlet_path)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

def extract_failures(hard):
    if hasattr(g, "extract_pytest_failures"):
        return g.extract_pytest_failures(hard)
    return []

def boost_state(s):
    s["difficulty"] = max(int(s.get("difficulty", 1)), 10)
    s["trap_strength"] = max(int(s.get("trap_strength", 1000)), 1_000_000)
    s["random_cases"] = max(int(s.get("random_cases", 100)), 1200)
    return s

def sprint_adapt(s, passed):
    if passed:
        s["difficulty"] = int(s.get("difficulty", 1)) + 5
        s["trap_strength"] = min(10**12, int(s.get("trap_strength", 1000)) * 5)
        s["random_cases"] = min(5000, int(s.get("random_cases", 100)) + 800)
    else:
        s["difficulty"] = int(s.get("difficulty", 1)) + 2
        s["trap_strength"] = min(10**12, int(s.get("trap_strength", 1000)) * 3)
        s["random_cases"] = min(5000, int(s.get("random_cases", 100)) + 500)
    return s

state = boost_state(g.load_state())
scorecard = []

print("NOW PROGRESS SPRINT")
print("No waiting. No dashboard. No useless training when already passing.")
print()

for sprint in range(1, 4):
    started = time.time()
    state["cycle"] = int(state.get("cycle", 0)) + 1

    print("=" * 70)
    print(f"SPRINT {sprint} | cycle={state['cycle']} | difficulty={state['difficulty']} | traps={state['trap_strength']} | random_cases={state['random_cases']}")

    tested, factory_failures = g.mine_enemy_cases(state)
    g.write_generated_test()

    hard = g.run_hard_tests()
    pytest_failures = extract_failures(hard)

    failures = list(factory_failures) + list(pytest_failures)
    bank_size = g.update_mistake_bank(failures)

    hard_ok = hard["returncode"] == 0
    factory_ok = len(factory_failures) == 0
    pass_now = hard_ok and factory_ok

    print("Enemy tested:", tested)
    print("Factory failures:", len(factory_failures))
    print("Pytest learned failures:", len(pytest_failures))
    print("Mistake bank size:", bank_size)
    print("Hard tests:", "PASS" if hard_ok else "FAIL", "in", hard["elapsed"], "sec")

    if not hard_ok:
        tail = "\n".join((hard["stdout"] + "\n" + hard["stderr"]).splitlines()[-16:])
        print("Hard fail tail:")
        print(tail)

    if pass_now:
        print("Training skipped: already passing. Escalating difficulty instead.")
        train_rc = "skipped"
    else:
        print("Failure found. Running 1-minute adversarial repair training.")
        train = g.run_adversarial_training(1)
        train_rc = train["returncode"]
        print("Training rc:", train_rc, "elapsed:", train["elapsed"])

    state = sprint_adapt(state, pass_now)
    g.save_state(state)

    scorecard.append({
        "sprint": sprint,
        "tested": tested,
        "factory_failures": len(factory_failures),
        "pytest_failures": len(pytest_failures),
        "hard_ok": hard_ok,
        "bank_size": bank_size,
        "train": train_rc,
        "seconds": round(time.time() - started, 3),
        "next_difficulty": state["difficulty"],
        "next_traps": state["trap_strength"],
        "next_random_cases": state["random_cases"],
    })

    print("SPRINT DONE in", scorecard[-1]["seconds"], "sec")
    print("Next difficulty:", state["difficulty"])
    print()

print("=" * 70)
print("RUNNING ONE SPEED CHECK")
if hasattr(g, "ensure_speed_profiles"):
    g.ensure_speed_profiles()
speed = g.run_speed_check()
print("Speed rc:", speed["returncode"], "elapsed:", speed["elapsed"], "sec")
print("\n".join((speed["stdout"] + "\n" + speed["stderr"]).splitlines()[-10:]))

rt = g.latest_runtime()
zipped = g.zip_runtime(rt) if rt else None

summary = {
    "created_at": time.time(),
    "mode": "now_progress_sprint",
    "scorecard": scorecard,
    "speed_rc": speed["returncode"],
    "speed_elapsed": speed["elapsed"],
    "runtime": zipped,
    "state": state,
}

out = ROOT / "training_outputs" / "now_progress_sprint_summary.json"
out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

print()
print("=" * 70)
print("NOW PROGRESS SCORECARD")
for row in scorecard:
    print(row)
print("Runtime:", zipped)
print("Saved:", out)
