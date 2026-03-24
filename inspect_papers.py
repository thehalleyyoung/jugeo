#!/usr/bin/env python3
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

results = []

for filepath in files:
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found", file=__import__('sys').stderr)
        continue
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    line_count = content.count('\n')
    has_jugeo = "yes" if "\\input{jugeo-common}" in content else "no"
    lst_count = content.count("\\begin{lstlisting}")
    has_def = "yes" if "\\begin{definition}" in content else "no"
    has_thm = "yes" if "\\begin{theorem}" in content else "no"
    has_toc = "yes" if "\\tableofcontents" in content else "no"
    
    filename = os.path.basename(filepath).replace('.tex', '')
    
    results.append((filename, line_count, has_jugeo, lst_count, has_def, has_thm, has_toc))

# Print header
print(f"{'File':<45} {'Lines':>6} {'jugeo-common':>12} {'lstlisting':>10} {'definition':>11} {'theorem':>9} {'toc':>6}")
print("-" * 120)

# Print rows
for r in results:
    print(f"{r[0]:<45} {r[1]:>6} {r[2]:>12} {r[3]:>10} {r[4]:>11} {r[5]:>9} {r[6]:>6}")
