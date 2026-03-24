#!/usr/bin/env python3
"""Analyze 11 LaTeX files for structural properties."""
import os

files = [
    "papers/paper74-copilot-construction-participant.tex",
    "papers/paper75-copilot-deduction-assist.tex",
    "papers/paper76-copilot-channel.tex",
    "papers/paper77-copilot-oracle-channel.tex",
    "papers/paper80-copilot-heap-scope-advisors.tex",
    "papers/paper81-copilot-import-callable-advisors.tex",
    "papers/paper82-copilot-research-experiment-advisors.tex",
    "papers/paper83-copilot-optimization-advisor.tex",
    "papers/paper84-copilot-lowering-hints.tex",
    "papers/paper99-copilot-connection-health.tex",
    "papers/paper100-copilot-api-bridge.tex",
]

BASE = os.path.dirname(os.path.abspath(__file__))

results = []
for filepath in files:
    full = os.path.join(BASE, filepath)
    if not os.path.exists(full):
        import sys
        print(f"Warning: {filepath} not found", file=sys.stderr)
        continue

    with open(full, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    line_count = content.count("\n")
    has_jugeo = "yes" if r"\input{jugeo-common}" in content else "no"
    lst_count = content.count(r"\begin{lstlisting}")
    has_def = "yes" if r"\begin{definition}" in content else "no"
    has_thm = "yes" if r"\begin{theorem}" in content else "no"
    has_toc = "yes" if r"\tableofcontents" in content else "no"

    filename = os.path.basename(filepath).replace(".tex", "")
    results.append((filename, line_count, has_jugeo, lst_count, has_def, has_thm, has_toc))

# Header
print(
    f"{'File':<50} {'Lines':>6} {'jugeo-common':>14} {'lstlisting':>12} {'definition':>12} {'theorem':>9} {'toc':>5}"
)
print("-" * 110)

# Rows
for r in results:
    print(
        f"{r[0]:<50} {r[1]:>6} {r[2]:>14} {r[3]:>12} {r[4]:>12} {r[5]:>9} {r[6]:>5}"
    )
