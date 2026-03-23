#!/usr/bin/env python3
"""Run all JuGeo verification scripts and report pass/fail."""
import subprocess
import sys
import os

SCRIPTS = [
    "paper00_end_to_end.py",
    "paper01_site_axioms.py",
    "paper02_judgment_algebra.py",
    "paper03_descent_verification.py",
    "paper04_trust_soundness.py",
    "paper05_fragment_soundness.py",
    "paper06_move_soundness.py",
    "paper07_effect_verification.py",
    "paper08_treaty_verification.py",
    "paper09_certificate_verification.py",
    "paper10_benchmark_verification.py",
]

HERE = os.path.dirname(os.path.abspath(__file__))
passed = []
failed = []

print("=" * 60)
print("  JuGeo Verification Suite — All Papers")
print("=" * 60)

for script in SCRIPTS:
    path = os.path.join(HERE, script)
    label = script.replace(".py", "")
    result = subprocess.run(
        [sys.executable, path],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        # Extract summary line
        lines = result.stdout.strip().splitlines()
        summary = [l for l in lines if "passed" in l and "failed" in l]
        summary_text = summary[-1].strip() if summary else "OK"
        print(f"  ✅ {label:45s} {summary_text}")
        passed.append(label)
    else:
        # Show the last few lines of output on failure
        lines = (result.stdout + result.stderr).strip().splitlines()
        err_line = lines[-1] if lines else "unknown error"
        print(f"  ❌ {label:45s} {err_line}")
        failed.append(label)

print()
print("=" * 60)
total = len(passed) + len(failed)
print(f"  Results: {len(passed)}/{total} passed, {len(failed)} failed")
if failed:
    print(f"  Failed: {', '.join(failed)}")
print("=" * 60)

sys.exit(1 if failed else 0)
