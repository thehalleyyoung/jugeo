#!/usr/bin/env python3
"""Paper 66 Experiment — Education Platform via JuGeo Learning Tool.

Runs JuGeo on pedagogically-graded programs (beginner through advanced),
measuring learning-relevant metrics: site complexity progression,
trust-tier advancement, and interactive exploration feasibility.
Generates papers/data-paper66.tex with \ppLXVI... macros.

Re-run: python3 experiments/exp66_education_platform.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper66.tex"

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

# ─── Pedagogical Programs by Difficulty ─────────────────────────────────────

EDU_PROGRAMS = {
    "beginner": {
        "hello_math": textwrap.dedent("""\
            def add(a, b): return a + b
            def subtract(a, b): return a - b
            def multiply(a, b): return a * b
        """),
        "conditionals": textwrap.dedent("""\
            def abs_val(x):
                if x < 0: return -x
                return x
            def max_of_two(a, b):
                if a >= b: return a
                return b
            def clamp(x, lo, hi):
                if x < lo: return lo
                if x > hi: return hi
                return x
        """),
        "simple_loop": textwrap.dedent("""\
            def count_to(n):
                result = []
                for i in range(1, n+1):
                    result.append(i)
                return result
            def sum_to(n):
                total = 0
                for i in range(1, n+1):
                    total += i
                return total
        """),
    },
    "intermediate": {
        "list_ops": textwrap.dedent("""\
            def find_max(lst):
                if not lst: raise ValueError("empty")
                m = lst[0]
                for x in lst[1:]:
                    if x > m: m = x
                return m
            def find_min(lst):
                if not lst: raise ValueError("empty")
                m = lst[0]
                for x in lst[1:]:
                    if x < m: m = x
                return m
            def average(lst):
                if not lst: raise ValueError("empty")
                return sum(lst) / len(lst)
        """),
        "string_processing": textwrap.dedent("""\
            def count_chars(s, ch):
                return sum(1 for c in s if c == ch)
            def remove_spaces(s):
                return ''.join(c for c in s if c != ' ')
            def caesar_cipher(s, shift):
                result = []
                for c in s:
                    if c.isalpha():
                        base = ord('a') if c.islower() else ord('A')
                        result.append(chr((ord(c) - base + shift) % 26 + base))
                    else:
                        result.append(c)
                return ''.join(result)
        """),
        "recursion_basics": textwrap.dedent("""\
            def factorial(n):
                if n <= 1: return 1
                return n * factorial(n - 1)
            def power(base, exp):
                if exp == 0: return 1
                return base * power(base, exp - 1)
            def reverse_string(s):
                if len(s) <= 1: return s
                return reverse_string(s[1:]) + s[0]
        """),
    },
    "advanced": {
        "data_structures": textwrap.dedent("""\
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
                def size(self): return len(self.items)
        """),
        "sorting_algorithms": textwrap.dedent("""\
            def bubble_sort(arr):
                n = len(arr)
                for i in range(n):
                    for j in range(0, n-i-1):
                        if arr[j] > arr[j+1]:
                            arr[j], arr[j+1] = arr[j+1], arr[j]
                return arr
            def insertion_sort(arr):
                for i in range(1, len(arr)):
                    key = arr[i]; j = i - 1
                    while j >= 0 and arr[j] > key:
                        arr[j+1] = arr[j]; j -= 1
                    arr[j+1] = key
                return arr
            def selection_sort(arr):
                for i in range(len(arr)):
                    min_idx = i
                    for j in range(i+1, len(arr)):
                        if arr[j] < arr[min_idx]: min_idx = j
                    arr[i], arr[min_idx] = arr[min_idx], arr[i]
                return arr
        """),
        "graph_algorithms": textwrap.dedent("""\
            def topological_sort(graph):
                in_degree = {u: 0 for u in graph}
                for u in graph:
                    for v in graph[u]:
                        in_degree[v] = in_degree.get(v, 0) + 1
                queue = [u for u in in_degree if in_degree[u] == 0]
                order = []
                while queue:
                    u = queue.pop(0)
                    order.append(u)
                    for v in graph.get(u, []):
                        in_degree[v] -= 1
                        if in_degree[v] == 0: queue.append(v)
                if len(order) != len(in_degree):
                    raise ValueError("cycle detected")
                return order
        """),
    },
    "expert": {
        "design_patterns": textwrap.dedent("""\
            class Singleton:
                _instance = None
                def __new__(cls):
                    if cls._instance is None:
                        cls._instance = super().__new__(cls)
                        cls._instance.data = {}
                    return cls._instance
                def set(self, k, v): self.data[k] = v
                def get(self, k): return self.data.get(k)
            class Observer:
                def __init__(self): self.listeners = []
                def subscribe(self, fn): self.listeners.append(fn)
                def notify(self, event):
                    for fn in self.listeners: fn(event)
            class Strategy:
                def __init__(self, fn): self.execute = fn
                def run(self, data): return self.execute(data)
        """),
        "concurrent_patterns": textwrap.dedent("""\
            class BoundedBuffer:
                def __init__(self, capacity):
                    self.buffer = []
                    self.capacity = capacity
                def put(self, item):
                    if len(self.buffer) >= self.capacity:
                        raise OverflowError("buffer full")
                    self.buffer.append(item)
                def get(self):
                    if not self.buffer:
                        raise IndexError("buffer empty")
                    return self.buffer.pop(0)
                def size(self): return len(self.buffer)
                def is_full(self): return len(self.buffer) >= self.capacity
                def is_empty(self): return len(self.buffer) == 0
        """),
    },
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 66: Education Platform Experiments")
print("=" * 60)

results = []
level_results = {}

for level, progs in EDU_PROGRAMS.items():
    print(f"\n  Level: {level}")
    level_results[level] = []
    for prog_id, source in progs.items():
        print(f"    [{prog_id}] ...", end=" ", flush=True)
        tmp = write_temp(source)
        try:
            t0 = time.perf_counter()

            eval_objs = run_jugeo_json("evaluate", tmp)
            enc_objs = run_jugeo_json("encode", tmp)
            desc_objs = run_jugeo_json("descend", tmp)

            elapsed = time.perf_counter() - t0

            eval_data = eval_objs[0] if eval_objs else {}
            enc_data = enc_objs[0] if enc_objs else {}
            desc_data = desc_objs[0] if desc_objs else {}

            files_enc = enc_data.get("files", [])
            n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
            sections = desc_data.get("sections_detail", [])
            props = sum(s.get("propositions", 0) for s in sections)
            ok_p = sum(s.get("ok", 0) for s in sections)
            verdict = desc_data.get("verdict", "unknown")
            trust = desc_data.get("trust", "UNKNOWN")

            cover_q = eval_data.get("cover_quality", {})
            cover_score = cover_q.get("total_score", 0.0) if isinstance(cover_q, dict) else 0.0

            rec = {
                "id": prog_id, "level": level,
                "n_coords": n_coords, "props": props, "ok": ok_p,
                "verdict": verdict, "trust": trust,
                "cover_score": round(cover_score, 4),
                "time_s": round(elapsed, 3),
            }
            results.append(rec)
            level_results[level].append(rec)
            print(f"coords={n_coords} props={props} trust={trust} t={elapsed:.2f}s")
        except Exception as e:
            print(f"ERROR: {e}")
        finally:
            try: os.unlink(tmp)
            except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

n_total = len(results)
n_verified = sum(1 for r in results if r.get("verdict") == "verified")
all_times = [r["time_s"] for r in results if "time_s" in r]
all_coords = [r["n_coords"] for r in results if "n_coords" in r]
all_props = [r["props"] for r in results if "props" in r]

level_agg = {}
for level, recs in level_results.items():
    if not recs: continue
    level_agg[level] = {
        "count": len(recs),
        "verified": sum(1 for r in recs if r["verdict"] == "verified"),
        "mean_coords": safe_mean([r["n_coords"] for r in recs]),
        "mean_props": safe_mean([r["props"] for r in recs]),
        "mean_time": safe_mean([r["time_s"] for r in recs]),
        "mean_cover": safe_mean([r["cover_score"] for r in recs]),
    }

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper66.tex — AUTO-GENERATED by exp66_education_platform.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp66_education_platform.py",
    f"% Generated from {n_total} pedagogical programs across {len(EDU_PROGRAMS)} levels",
    "",
    f"\\newcommand{{\\ppLXVIprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXVIverified}}{{{n_verified}}}",
    f"\\newcommand{{\\ppLXVIverifiedPct}}{{{round(100*n_verified/max(n_total,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXVIlevelCount}}{{{len(EDU_PROGRAMS)}}}",
    "",
    f"\\newcommand{{\\ppLXVIcoordsMean}}{{{safe_mean(all_coords)}}}",
    f"\\newcommand{{\\ppLXVIpropsMean}}{{{safe_mean(all_props)}}}",
    f"\\newcommand{{\\ppLXVItimeMean}}{{{safe_mean(all_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVItimeTotal}}{{{round(sum(all_times),2)}\\,s}}",
    "",
]

level_labels = {"beginner": "Beginner", "intermediate": "Intermediate",
                "advanced": "Advanced", "expert": "Expert"}
for lkey, llabel in level_labels.items():
    a = level_agg.get(lkey, {"count": 0, "verified": 0, "mean_coords": 0,
                              "mean_props": 0, "mean_time": 0, "mean_cover": 0})
    lines.append(f"\\newcommand{{\\ppLXVI{llabel}Count}}{{{a['count']}}}")
    lines.append(f"\\newcommand{{\\ppLXVI{llabel}Verified}}{{{a['verified']}}}")
    lines.append(f"\\newcommand{{\\ppLXVI{llabel}MeanCoords}}{{{a['mean_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXVI{llabel}MeanProps}}{{{a['mean_props']}}}")
    lines.append(f"\\newcommand{{\\ppLXVI{llabel}MeanTime}}{{{a['mean_time']}\\,s}}")
    lines.append(f"\\newcommand{{\\ppLXVI{llabel}MeanCover}}{{{a['mean_cover']}}}")
    lines.append("")

lines.append("% Per-program educational results")
for r in results:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXVIedu{tag}Coords}}{{{r['n_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIedu{tag}Props}}{{{r['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIedu{tag}Trust}}{{{r['trust']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIedu{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper66.json"
with open(json_path, "w") as f:
    json.dump({"paper": 66, "programs": n_total, "levels": level_agg,
               "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
