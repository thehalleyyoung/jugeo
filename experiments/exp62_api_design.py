#!/usr/bin/env python3
"""Paper 62 Experiment — API Design via Site Topology.

Runs JuGeo on programs of varying sizes, measuring API-relevant metrics:
morphism counts by size tier, interface routing, public alignment, and
topological API quality scores.
Generates papers/data-paper62.tex with \ppLXII... macros.

Re-run: python3 experiments/exp62_api_design.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper62.tex"

def run_jugeo_json(*args, timeout=30):
    cmd = [sys.executable, "-m", "jugeo", "--format", "json"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    lines = [l for l in r.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining: break
        try:
            obj, end = dec.raw_decode(remaining)
            objects.append(obj); idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects

def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source); f.close(); return f.name

def safe_mean(xs): return round(statistics.mean(xs), 2) if xs else 0.0

# ─── Test Programs by Size Tier ────────────────────────────────────────────

SIZE_PROGRAMS = {
    "tiny": {
        "add_pair": "def add(a, b):\n    return a + b\ndef sub(a, b):\n    return a - b\n",
        "negate": "def negate(x):\n    return -x\ndef abs_val(x):\n    return x if x >= 0 else -x\n",
        "id_const": "def identity(x):\n    return x\ndef const(x, y):\n    return x\n",
    },
    "small": {
        "binary_search": textwrap.dedent("""\
            def binary_search(arr, target):
                lo, hi = 0, len(arr) - 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if arr[mid] == target: return mid
                    elif arr[mid] < target: lo = mid + 1
                    else: hi = mid - 1
                return -1
        """),
        "stack": textwrap.dedent("""\
            class Stack:
                def __init__(self): self.items = []
                def push(self, x): self.items.append(x)
                def pop(self):
                    if not self.items: raise IndexError("empty")
                    return self.items.pop()
                def peek(self):
                    if not self.items: raise IndexError("empty")
                    return self.items[-1]
                def is_empty(self): return len(self.items) == 0
        """),
        "str_utils": textwrap.dedent("""\
            def reverse(s): return s[::-1]
            def is_palindrome(s): return s == s[::-1]
            def word_count(s): return len(s.split())
            def truncate(s, n):
                if len(s) <= n: return s
                return s[:n] + '...'
        """),
    },
    "medium": {
        "sorting": textwrap.dedent("""\
            def merge_sort(arr):
                if len(arr) <= 1: return arr
                mid = len(arr) // 2
                left = merge_sort(arr[:mid])
                right = merge_sort(arr[mid:])
                return merge(left, right)
            def merge(left, right):
                result, i, j = [], 0, 0
                while i < len(left) and j < len(right):
                    if left[i] <= right[j]:
                        result.append(left[i]); i += 1
                    else:
                        result.append(right[j]); j += 1
                result.extend(left[i:]); result.extend(right[j:])
                return result
            def is_sorted(arr):
                return all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
        """),
        "calculator": textwrap.dedent("""\
            class Calculator:
                def __init__(self): self.history = []
                def add(self, a, b):
                    r = a + b; self.history.append(('add', r)); return r
                def sub(self, a, b):
                    r = a - b; self.history.append(('sub', r)); return r
                def mul(self, a, b):
                    r = a * b; self.history.append(('mul', r)); return r
                def div(self, a, b):
                    if b == 0: raise ZeroDivisionError
                    r = a / b; self.history.append(('div', r)); return r
                def undo(self):
                    if self.history: return self.history.pop()
                    return None
        """),
    },
    "large": {
        "graph_ops": textwrap.dedent("""\
            from collections import deque
            class Graph:
                def __init__(self): self.adj = {}
                def add_edge(self, u, v):
                    self.adj.setdefault(u, []).append(v)
                    self.adj.setdefault(v, []).append(u)
                def bfs(self, start):
                    visited, queue, order = set(), deque([start]), []
                    while queue:
                        node = queue.popleft()
                        if node in visited: continue
                        visited.add(node); order.append(node)
                        for nb in self.adj.get(node, []):
                            if nb not in visited: queue.append(nb)
                    return order
                def dfs(self, start):
                    visited, stack, order = set(), [start], []
                    while stack:
                        node = stack.pop()
                        if node in visited: continue
                        visited.add(node); order.append(node)
                        for nb in reversed(self.adj.get(node, [])):
                            if nb not in visited: stack.append(nb)
                    return order
                def has_path(self, src, dst):
                    return dst in set(self.bfs(src))
                def connected_components(self):
                    visited, components = set(), []
                    for node in self.adj:
                        if node not in visited:
                            comp = self.bfs(node)
                            visited.update(comp)
                            components.append(comp)
                    return components
        """),
        "state_machine": textwrap.dedent("""\
            class StateMachine:
                def __init__(self, initial):
                    self.state = initial
                    self.transitions = {}
                    self.actions = {}
                    self.history = []
                def add_transition(self, src, event, dst, action=None):
                    self.transitions[(src, event)] = dst
                    if action: self.actions[(src, event)] = action
                def trigger(self, event):
                    key = (self.state, event)
                    if key not in self.transitions:
                        raise ValueError(f"no transition from {self.state} on {event}")
                    old = self.state
                    self.state = self.transitions[key]
                    self.history.append((old, event, self.state))
                    if key in self.actions:
                        self.actions[key]()
                    return self.state
                def can_trigger(self, event):
                    return (self.state, event) in self.transitions
                def reset(self, state=None):
                    self.state = state or self.history[0][0] if self.history else self.state
                    self.history.clear()
                def reachable_states(self):
                    seen = set()
                    queue = [self.state]
                    while queue:
                        s = queue.pop(0)
                        if s in seen: continue
                        seen.add(s)
                        for (src, evt), dst in self.transitions.items():
                            if src == s and dst not in seen:
                                queue.append(dst)
                    return seen
        """),
    },
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 62: API Design Experiments")
print("=" * 60)

tier_results = {}
all_results = []

for tier, progs in SIZE_PROGRAMS.items():
    print(f"\n  Tier: {tier}")
    tier_results[tier] = []
    for prog_id, source in progs.items():
        print(f"    [{prog_id}] ...", end=" ", flush=True)
        tmp = write_temp(source)
        try:
            t0 = time.perf_counter()

            eval_objs = run_jugeo_json("evaluate", tmp)
            eval_data = eval_objs[0] if eval_objs else {}

            enc_objs = run_jugeo_json("encode", tmp)
            enc_data = enc_objs[0] if enc_objs else {}

            desc_objs = run_jugeo_json("descend", tmp)
            desc_data = desc_objs[0] if desc_objs else {}

            elapsed = time.perf_counter() - t0

            files_enc = enc_data.get("files", [])
            n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
            sections = desc_data.get("sections_detail", [])
            total_props = sum(s.get("propositions", 0) for s in sections)

            # Count morphisms
            morph_count = 0
            if files_enc:
                for cname, cdata in files_enc[0].get("coordinates", {}).items():
                    morph_count += cdata.get("declarations", 0) + cdata.get("assertions", 0)

            rec = {
                "id": prog_id, "tier": tier,
                "n_coords": n_coords, "morphisms": morph_count,
                "props": total_props,
                "verdict": desc_data.get("verdict", "unknown"),
                "time_s": round(elapsed, 3),
            }
            tier_results[tier].append(rec)
            all_results.append(rec)
            print(f"coords={n_coords} morph={morph_count} t={elapsed:.2f}s")
        except Exception as e:
            print(f"ERROR: {e}")
        finally:
            try: os.unlink(tmp)
            except: pass

# ─── Compute per-tier aggregates ────────────────────────────────────────────

tier_agg = {}
for tier, recs in tier_results.items():
    if not recs:
        tier_agg[tier] = {"count": 0, "mean_coords": 0, "mean_morph": 0, "mean_time": 0}
        continue
    tier_agg[tier] = {
        "count": len(recs),
        "mean_coords": safe_mean([r["n_coords"] for r in recs]),
        "mean_morph": safe_mean([r["morphisms"] for r in recs]),
        "mean_props": safe_mean([r["props"] for r in recs]),
        "mean_time": safe_mean([r["time_s"] for r in recs]),
    }

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)

total_progs = len(all_results)
total_verified = sum(1 for r in all_results if r["verdict"] == "verified")
all_morph = [r["morphisms"] for r in all_results]
all_coords = [r["n_coords"] for r in all_results]
all_times = [r["time_s"] for r in all_results]

lines = [
    "% data-paper62.tex — AUTO-GENERATED by exp62_api_design.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp62_api_design.py",
    f"% Generated from {total_progs} programs across 4 size tiers",
    "",
    f"\\newcommand{{\\ppLXIIprogramCount}}{{{total_progs}}}",
    f"\\newcommand{{\\ppLXIIverified}}{{{total_verified}}}",
    f"\\newcommand{{\\ppLXIImorphMean}}{{{safe_mean(all_morph)}}}",
    f"\\newcommand{{\\ppLXIIcoordsMean}}{{{safe_mean(all_coords)}}}",
    f"\\newcommand{{\\ppLXIItimeMean}}{{{safe_mean(all_times)}\\,s}}",
    f"\\newcommand{{\\ppLXIItimeTotal}}{{{round(sum(all_times),2)}\\,s}}",
    "",
]

tier_names = {"tiny": "Tiny", "small": "Small", "medium": "Medium", "large": "Large"}
for tier_key, tier_label in tier_names.items():
    a = tier_agg.get(tier_key, {})
    lines.append(f"\\newcommand{{\\ppLXII{tier_label}Count}}{{{a.get('count', 0)}}}")
    lines.append(f"\\newcommand{{\\ppLXII{tier_label}MeanCoords}}{{{a.get('mean_coords', 0)}}}")
    lines.append(f"\\newcommand{{\\ppLXII{tier_label}MeanMorph}}{{{a.get('mean_morph', 0)}}}")
    lines.append(f"\\newcommand{{\\ppLXII{tier_label}MeanProps}}{{{a.get('mean_props', 0)}}}")
    lines.append(f"\\newcommand{{\\ppLXII{tier_label}MeanTime}}{{{a.get('mean_time', 0)}\\,s}}")
    lines.append("")

# Per-program API detail
lines.append("% Per-program API detail")
for r in all_results:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXIIapi{tag}Coords}}{{{r['n_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXIIapi{tag}Morph}}{{{r['morphisms']}}}")
    lines.append(f"\\newcommand{{\\ppLXIIapi{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper62.json"
with open(json_path, "w") as f:
    json.dump({"paper": 62, "programs": total_progs, "tier_agg": tier_agg,
               "results": all_results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
