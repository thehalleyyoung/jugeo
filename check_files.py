#!/usr/bin/env python3
import os
import subprocess
import sys

files = [
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/partiality_model_reconstruction/s05_model_reconstruction_as_a_first_cl.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/cover_design/s03_module_boundaries_overlap_quality.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/local_construction/s03_coordination_with_semantic_account.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/semantic_closure/manifest.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/state_space/s02_the_core_state_space_for_generatio.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/state_space/s03_generation_moves_as_dependent_tran.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/state_space/s04_implementation_consequences.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/state_space/algorithms.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/state_space/integration.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/generation/state_space/theorems.py",
]

print("=== FILE EXISTENCE AND SIZE CHECK ===")
existing_files = []
for i, f in enumerate(files, 1):
    if os.path.isfile(f):
        size = os.path.getsize(f)
        print(f"{i}. EXISTS ({size} bytes)")
        existing_files.append(f)
    else:
        print(f"{i}. NOT EXISTS")

print("\n=== COMPILATION CHECK ===")
for f in existing_files:
    try:
        subprocess.run([sys.executable, "-m", "py_compile", f], check=True, capture_output=True, timeout=10)
        print(f"✓ {os.path.basename(f)}")
    except subprocess.CalledProcessError as e:
        print(f"✗ {os.path.basename(f)} - Compilation failed")
        print(f"  Error: {e.stderr.decode()}")
    except Exception as e:
        print(f"✗ {os.path.basename(f)} - Error: {e}")
