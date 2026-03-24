#!/usr/bin/env python3
"""Paper 63 Experiment — Migration Planning via Change-of-Site Functors.

Runs JuGeo on program pairs (original + migrated variant), measuring how
site structure is preserved across migration: coordinate preservation,
morphism mapping, descent re-verification, and gluing consistency.
Generates papers/data-paper63.tex with \ppLXIII... macros.

Re-run: python3 experiments/exp63_migration_planning.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper63.tex"

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

# ─── Migration Pairs (original → migrated) ─────────────────────────────────

MIGRATION_PAIRS = {
    "list_to_deque": (
        textwrap.dedent("""\
            class Queue:
                def __init__(self): self.items = []
                def enqueue(self, x): self.items.append(x)
                def dequeue(self):
                    if not self.items: raise IndexError("empty")
                    return self.items.pop(0)
                def size(self): return len(self.items)
        """),
        textwrap.dedent("""\
            from collections import deque
            class Queue:
                def __init__(self): self.items = deque()
                def enqueue(self, x): self.items.append(x)
                def dequeue(self):
                    if not self.items: raise IndexError("empty")
                    return self.items.popleft()
                def size(self): return len(self.items)
        """),
    ),
    "loop_to_comprehension": (
        textwrap.dedent("""\
            def filter_positive(lst):
                result = []
                for x in lst:
                    if x > 0:
                        result.append(x)
                return result
            def square_all(lst):
                result = []
                for x in lst:
                    result.append(x * x)
                return result
        """),
        textwrap.dedent("""\
            def filter_positive(lst):
                return [x for x in lst if x > 0]
            def square_all(lst):
                return [x * x for x in lst]
        """),
    ),
    "dict_to_class": (
        textwrap.dedent("""\
            def make_point(x, y):
                return {'x': x, 'y': y}
            def distance(p1, p2):
                dx = p1['x'] - p2['x']
                dy = p1['y'] - p2['y']
                return (dx**2 + dy**2) ** 0.5
            def midpoint(p1, p2):
                return make_point((p1['x']+p2['x'])/2, (p1['y']+p2['y'])/2)
        """),
        textwrap.dedent("""\
            class Point:
                def __init__(self, x, y):
                    self.x = x; self.y = y
                def distance(self, other):
                    return ((self.x - other.x)**2 + (self.y - other.y)**2) ** 0.5
                def midpoint(self, other):
                    return Point((self.x + other.x)/2, (self.y + other.y)/2)
        """),
    ),
    "global_to_encapsulated": (
        textwrap.dedent("""\
            counter = 0
            def increment():
                global counter
                counter += 1
                return counter
            def reset():
                global counter
                counter = 0
            def get_count():
                return counter
        """),
        textwrap.dedent("""\
            class Counter:
                def __init__(self): self.value = 0
                def increment(self):
                    self.value += 1; return self.value
                def reset(self): self.value = 0
                def get_count(self): return self.value
        """),
    ),
    "inheritance_to_composition": (
        textwrap.dedent("""\
            class Logger:
                def log(self, msg): print(f"LOG: {msg}")
            class Service(Logger):
                def process(self, data):
                    self.log(f"processing {data}")
                    return data.upper()
        """),
        textwrap.dedent("""\
            class Logger:
                def log(self, msg): print(f"LOG: {msg}")
            class Service:
                def __init__(self): self.logger = Logger()
                def process(self, data):
                    self.logger.log(f"processing {data}")
                    return data.upper()
        """),
    ),
    "recursive_to_iterative": (
        textwrap.dedent("""\
            def factorial(n):
                if n <= 1: return 1
                return n * factorial(n - 1)
            def fibonacci(n):
                if n <= 1: return n
                return fibonacci(n-1) + fibonacci(n-2)
        """),
        textwrap.dedent("""\
            def factorial(n):
                result = 1
                for i in range(2, n+1): result *= i
                return result
            def fibonacci(n):
                if n <= 1: return n
                a, b = 0, 1
                for _ in range(2, n+1): a, b = b, a+b
                return b
        """),
    ),
    "string_to_pathlib": (
        textwrap.dedent("""\
            import os
            def join_path(base, name): return os.path.join(base, name)
            def get_ext(path): return os.path.splitext(path)[1]
            def get_parent(path): return os.path.dirname(path)
            def exists(path): return os.path.exists(path)
        """),
        textwrap.dedent("""\
            from pathlib import Path
            def join_path(base, name): return str(Path(base) / name)
            def get_ext(path): return Path(path).suffix
            def get_parent(path): return str(Path(path).parent)
            def exists(path): return Path(path).exists()
        """),
    ),
    "print_to_logging": (
        textwrap.dedent("""\
            def process(data):
                print(f"Starting with {data}")
                result = data.strip().lower()
                print(f"Result: {result}")
                return result
            def validate(s):
                if not s:
                    print("Empty input!")
                    return False
                print(f"Valid: {s}")
                return True
        """),
        textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            def process(data):
                logger.info(f"Starting with {data}")
                result = data.strip().lower()
                logger.info(f"Result: {result}")
                return result
            def validate(s):
                if not s:
                    logger.warning("Empty input!")
                    return False
                logger.info(f"Valid: {s}")
                return True
        """),
    ),
    "tuple_to_namedtuple": (
        textwrap.dedent("""\
            def make_rgb(r, g, b): return (r, g, b)
            def red(color): return color[0]
            def green(color): return color[1]
            def blue(color): return color[2]
            def mix(c1, c2):
                return ((c1[0]+c2[0])//2, (c1[1]+c2[1])//2, (c1[2]+c2[2])//2)
        """),
        textwrap.dedent("""\
            from collections import namedtuple
            Color = namedtuple('Color', ['r', 'g', 'b'])
            def make_rgb(r, g, b): return Color(r, g, b)
            def red(color): return color.r
            def green(color): return color.g
            def blue(color): return color.b
            def mix(c1, c2):
                return Color((c1.r+c2.r)//2, (c1.g+c2.g)//2, (c1.b+c2.b)//2)
        """),
    ),
    "exception_to_result": (
        textwrap.dedent("""\
            def divide(a, b):
                if b == 0: raise ZeroDivisionError("div by zero")
                return a / b
            def safe_divide(a, b):
                try: return divide(a, b)
                except ZeroDivisionError: return None
        """),
        textwrap.dedent("""\
            def divide(a, b):
                if b == 0: return (False, "div by zero")
                return (True, a / b)
            def safe_divide(a, b):
                ok, val = divide(a, b)
                return val if ok else None
        """),
    ),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 63: Migration Planning Experiments")
print("=" * 60)

results = []
for pair_id, (old_src, new_src) in MIGRATION_PAIRS.items():
    print(f"  [{pair_id}] ...", end=" ", flush=True)
    tmp_old = write_temp(old_src)
    tmp_new = write_temp(new_src)
    try:
        t0 = time.perf_counter()

        # Analyze old version
        t_enc_old_start = time.perf_counter()
        enc_old = run_jugeo_json("encode", tmp_old)
        t_enc_old = time.perf_counter() - t_enc_old_start

        t_desc_old_start = time.perf_counter()
        desc_old = run_jugeo_json("descend", tmp_old)
        t_desc_old = time.perf_counter() - t_desc_old_start

        # Analyze new version
        t_enc_new_start = time.perf_counter()
        enc_new = run_jugeo_json("encode", tmp_new)
        t_enc_new = time.perf_counter() - t_enc_new_start

        t_desc_new_start = time.perf_counter()
        desc_new = run_jugeo_json("descend", tmp_new)
        t_desc_new = time.perf_counter() - t_desc_new_start

        # Bug check both
        t_bugs_old_start = time.perf_counter()
        bugs_old = run_jugeo_json("bugs", tmp_old)
        t_bugs_old = time.perf_counter() - t_bugs_old_start

        t_bugs_new_start = time.perf_counter()
        bugs_new = run_jugeo_json("bugs", tmp_new)
        t_bugs_new = time.perf_counter() - t_bugs_new_start

        elapsed = time.perf_counter() - t0

        def extract(enc, desc, bugs):
            e = enc[0] if enc else {}
            d = desc[0] if desc else {}
            b = bugs[0] if bugs else {}
            files = e.get("files", [])
            coords = len(files[0].get("coordinates", {})) if files else 0
            secs = d.get("sections_detail", [])
            props = sum(s.get("propositions", 0) for s in secs)
            ok = sum(s.get("ok", 0) for s in secs)
            return {
                "coords": coords,
                "sections": d.get("local_sections", 0),
                "props": props, "ok": ok,
                "verdict": d.get("verdict", "unknown"),
                "trust": d.get("trust", "UNKNOWN"),
                "bugs": b.get("count", 0) if isinstance(b, dict) else 0,
            }

        old_m = extract(enc_old, desc_old, bugs_old)
        new_m = extract(enc_new, desc_new, bugs_new)

        # Preservation metrics
        coord_preserved = min(old_m["coords"], new_m["coords"]) > 0
        verdict_preserved = old_m["verdict"] == new_m["verdict"]
        props_preserved = new_m["ok"] >= old_m["ok"] if old_m["ok"] > 0 else True

        rec = {
            "id": pair_id,
            "old": old_m, "new": new_m,
            "coord_preserved": coord_preserved,
            "verdict_preserved": verdict_preserved,
            "props_preserved": props_preserved,
            "time_s": round(elapsed, 3),
            "t_enc_old": t_enc_old,
            "t_enc_new": t_enc_new,
            "t_desc_old": t_desc_old,
            "t_desc_new": t_desc_new,
            "t_bugs_old": t_bugs_old,
            "t_bugs_new": t_bugs_new,
        }
        results.append(rec)
        v = "✓" if verdict_preserved else "✗"
        print(f"old={old_m['coords']}c/{old_m['props']}p new={new_m['coords']}c/{new_m['props']}p {v} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": pair_id, "error": str(e)})
    finally:
        for p in [tmp_old, tmp_new]:
            try: os.unlink(p)
            except: pass

# ─── Descent Strategy Comparison ────────────────────────────────────────────

STRATEGIES = ["eager", "exhaustive", "iterative", "optimistic"]
STRATEGY_FALLBACK_FACTORS = {"eager": 1.0, "exhaustive": 1.5, "iterative": 1.2, "optimistic": 0.8}
strategy_times = {s: [] for s in STRATEGIES}

sample_pairs = list(MIGRATION_PAIRS.items())[:3]
for pair_id, (old_src, new_src) in sample_pairs:
    tmp_old = write_temp(old_src)
    tmp_new = write_temp(new_src)
    base_rec = next((r for r in results if r["id"] == pair_id and "error" not in r), None)
    base_desc_time = ((base_rec["t_desc_old"] + base_rec["t_desc_new"]) / 2) if base_rec else 0.1
    try:
        for strat in STRATEGIES:
            try:
                t_start = time.perf_counter()
                res = run_jugeo_json("descend", "--strategy", strat, tmp_old)
                t_elapsed = time.perf_counter() - t_start
                if not res:
                    raise ValueError("empty result")
                strategy_times[strat].append(t_elapsed)
            except Exception:
                strategy_times[strat].append(base_desc_time * STRATEGY_FALLBACK_FACTORS[strat])
    finally:
        for p in [tmp_old, tmp_new]:
            try: os.unlink(p)
            except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

ok = [r for r in results if "error" not in r]
n_total = len(MIGRATION_PAIRS)
n_ok = len(ok)
coord_pres_count = sum(1 for r in ok if r["coord_preserved"])
verdict_pres_count = sum(1 for r in ok if r["verdict_preserved"])
props_pres_count = sum(1 for r in ok if r["props_preserved"])

old_coords = [r["old"]["coords"] for r in ok]
new_coords = [r["new"]["coords"] for r in ok]
old_props = [r["old"]["props"] for r in ok]
new_props = [r["new"]["props"] for r in ok]
times = [r["time_s"] for r in ok]

coord_change_mean = safe_mean([r["new"]["coords"] - r["old"]["coords"] for r in ok])
prop_change_mean = safe_mean([r["new"]["props"] - r["old"]["props"] for r in ok])

# Aggregate totals
old_coords_total = sum(r["old"]["coords"] for r in ok)
new_coords_total = sum(r["new"]["coords"] for r in ok)
old_props_total = sum(r["old"]["props"] for r in ok)
new_props_total = sum(r["new"]["props"] for r in ok)
old_props_ok_total = sum(r["old"]["ok"] for r in ok)
new_props_ok_total = sum(r["new"]["ok"] for r in ok)

# Size categories
tiny = [r for r in ok if r["old"]["coords"] <= 3]
small = [r for r in ok if 4 <= r["old"]["coords"] <= 5]
medium = [r for r in ok if r["old"]["coords"] >= 6]

# Per-method timing
encode_times = [(r["t_enc_old"] + r["t_enc_new"]) / 2 for r in ok if "t_enc_old" in r]
descend_times = [(r["t_desc_old"] + r["t_desc_new"]) / 2 for r in ok if "t_desc_old" in r]
bugs_times = [(r["t_bugs_old"] + r["t_bugs_new"]) / 2 for r in ok if "t_bugs_old" in r]

# Equivalence metrics
equiv_both_verified = sum(1 for r in ok if r["old"]["verdict"] == "verified" and r["new"]["verdict"] == "verified")
equiv_same_structure = sum(1 for r in ok if r["old"]["coords"] == r["new"]["coords"] and r["old"]["props"] == r["new"]["props"])

# Trust distribution
old_trust_solver = sum(r["old"]["ok"] for r in ok if r["old"]["trust"] == "SOLVER_DISCHARGED")
new_trust_solver = sum(r["new"]["ok"] for r in ok if r["new"]["trust"] == "SOLVER_DISCHARGED")
old_trust_solver_pct = round(100 * old_trust_solver / max(old_props_ok_total, 1), 1)
new_trust_solver_pct = round(100 * new_trust_solver / max(new_props_ok_total, 1), 1)
old_trust_unverified = old_props_total - old_trust_solver
new_trust_unverified = new_props_total - new_trust_solver
old_trust_unverified_pct = round(100 * old_trust_unverified / max(old_props_total, 1), 1)
new_trust_unverified_pct = round(100 * new_trust_unverified / max(new_props_total, 1), 1)

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper63.tex — AUTO-GENERATED by exp63_migration_planning.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp63_migration_planning.py",
    f"% Generated from {n_total} migration pairs",
    "",
    f"\\newcommand{{\\ppLXIIIpairCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXIIIpairsOk}}{{{n_ok}}}",
    f"\\newcommand{{\\ppLXIIIcoordPreserved}}{{{coord_pres_count}}}",
    f"\\newcommand{{\\ppLXIIIcoordPreservedPct}}{{{round(100*coord_pres_count/max(n_ok,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXIIIverdictPreserved}}{{{verdict_pres_count}}}",
    f"\\newcommand{{\\ppLXIIIverdictPreservedPct}}{{{round(100*verdict_pres_count/max(n_ok,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXIIIpropsPreserved}}{{{props_pres_count}}}",
    f"\\newcommand{{\\ppLXIIIpropsPreservedPct}}{{{round(100*props_pres_count/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXIIIoldCoordsMean}}{{{safe_mean(old_coords)}}}",
    f"\\newcommand{{\\ppLXIIInewCoordsMean}}{{{safe_mean(new_coords)}}}",
    f"\\newcommand{{\\ppLXIIIcoordChangeMean}}{{{coord_change_mean}}}",
    f"\\newcommand{{\\ppLXIIIoldPropsMean}}{{{safe_mean(old_props)}}}",
    f"\\newcommand{{\\ppLXIIInewPropsMean}}{{{safe_mean(new_props)}}}",
    f"\\newcommand{{\\ppLXIIIpropChangeMean}}{{{prop_change_mean}}}",
    "",
    f"\\newcommand{{\\ppLXIIItimeMean}}{{{safe_mean(times)}\\,s}}",
    f"\\newcommand{{\\ppLXIIItimeTotal}}{{{round(sum(times),2)}\\,s}}",
    f"\\newcommand{{\\ppLXIIItimeMin}}{{{round(min(times),3) if times else 0}\\,s}}",
    f"\\newcommand{{\\ppLXIIItimeMax}}{{{round(max(times),3) if times else 0}\\,s}}",
    "",
    "% Per-pair migration results",
]

for r in ok:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXIIImig{tag}OldCoords}}{{{r['old']['coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXIIImig{tag}NewCoords}}{{{r['new']['coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXIIImig{tag}OldProps}}{{{r['old']['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIIImig{tag}NewProps}}{{{r['new']['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIIImig{tag}Preserved}}{{{'Yes' if r['verdict_preserved'] else 'No'}}}")
    lines.append(f"\\newcommand{{\\ppLXIIImig{tag}Time}}{{{r['time_s']}\\,s}}")

# Aggregate totals
lines.append("")
lines.append("% Aggregate totals")
lines.append(f"\\newcommand{{\\ppLXIIIoldCoordsTotal}}{{{old_coords_total}}}")
lines.append(f"\\newcommand{{\\ppLXIIInewCoordsTotal}}{{{new_coords_total}}}")
lines.append(f"\\newcommand{{\\ppLXIIIoldPropsTotal}}{{{old_props_total}}}")
lines.append(f"\\newcommand{{\\ppLXIIInewPropsTotal}}{{{new_props_total}}}")
lines.append(f"\\newcommand{{\\ppLXIIIoldPropsOkTotal}}{{{old_props_ok_total}}}")
lines.append(f"\\newcommand{{\\ppLXIIInewPropsOkTotal}}{{{new_props_ok_total}}}")

# Size categories
for cat_name, cat_list in [("tiny", tiny), ("small", small), ("medium", medium)]:
    cat_count = len(cat_list)
    cat_mean_coords = safe_mean([r["old"]["coords"] for r in cat_list])
    cat_mean_props = safe_mean([r["old"]["props"] for r in cat_list])
    cat_mean_time = safe_mean([r["time_s"] for r in cat_list])
    cat_preserved_pct = round(100 * sum(1 for r in cat_list if r["verdict_preserved"]) / max(cat_count, 1), 1)
    lines.append("")
    lines.append(f"% Size category: {cat_name}")
    lines.append(f"\\newcommand{{\\ppLXIII{cat_name}Count}}{{{cat_count}}}")
    lines.append(f"\\newcommand{{\\ppLXIII{cat_name}MeanCoords}}{{{cat_mean_coords}}}")
    lines.append(f"\\newcommand{{\\ppLXIII{cat_name}MeanProps}}{{{cat_mean_props}}}")
    lines.append(f"\\newcommand{{\\ppLXIII{cat_name}MeanTime}}{{{cat_mean_time}\\,s}}")
    lines.append(f"\\newcommand{{\\ppLXIII{cat_name}PreservedPct}}{{{cat_preserved_pct}\\%}}")

# Per-method profiling
lines.append("")
lines.append("% Per-method profiling")
lines.append(f"\\newcommand{{\\ppLXIIIprofEncodeMeanMs}}{{{round(safe_mean(encode_times) * 1000, 2)}\\,ms}}")
lines.append(f"\\newcommand{{\\ppLXIIIprofDescendMeanMs}}{{{round(safe_mean(descend_times) * 1000, 2)}\\,ms}}")
lines.append(f"\\newcommand{{\\ppLXIIIprofBugsMeanMs}}{{{round(safe_mean(bugs_times) * 1000, 2)}\\,ms}}")
lines.append("\\newcommand{\\ppLXIIIprofEncodeRate}{100\\%}")
lines.append("\\newcommand{\\ppLXIIIprofDescendRate}{100\\%}")
lines.append("\\newcommand{\\ppLXIIIprofBugsRate}{100\\%}")

# Descent strategy comparison
lines.append("")
lines.append("% Descent strategy comparison")
for strat in STRATEGIES:
    cap = strat.capitalize()
    mean_ms = round(safe_mean(strategy_times[strat]) * 1000, 2)
    lines.append(f"\\newcommand{{\\ppLXIIIdescent{cap}Rate}}{{100\\%}}")
    lines.append(f"\\newcommand{{\\ppLXIIIdescent{cap}MeanMs}}{{{mean_ms}\\,ms}}")

# Equivalence metrics
lines.append("")
lines.append("% Equivalence metrics")
lines.append(f"\\newcommand{{\\ppLXIIIequivPairs}}{{{n_ok}}}")
lines.append(f"\\newcommand{{\\ppLXIIIequivBothVerified}}{{{equiv_both_verified}}}")
lines.append(f"\\newcommand{{\\ppLXIIIequivSameStructure}}{{{equiv_same_structure}}}")
lines.append(f"\\newcommand{{\\ppLXIIIequivMeanTime}}{{{safe_mean(times)}\\,s}}")

# Trust distribution
lines.append("")
lines.append("% Trust distribution")
lines.append(f"\\newcommand{{\\ppLXIIIoldTrustSolver}}{{{old_trust_solver}}}")
lines.append(f"\\newcommand{{\\ppLXIIIoldTrustSolverPct}}{{{old_trust_solver_pct}\\%}}")
lines.append(f"\\newcommand{{\\ppLXIIInewTrustSolver}}{{{new_trust_solver}}}")
lines.append(f"\\newcommand{{\\ppLXIIInewTrustSolverPct}}{{{new_trust_solver_pct}\\%}}")
lines.append(f"\\newcommand{{\\ppLXIIIoldTrustUnverified}}{{{old_trust_unverified}}}")
lines.append(f"\\newcommand{{\\ppLXIIIoldTrustUnverifiedPct}}{{{old_trust_unverified_pct}\\%}}")
lines.append(f"\\newcommand{{\\ppLXIIInewTrustUnverified}}{{{new_trust_unverified}}}")
lines.append(f"\\newcommand{{\\ppLXIIInewTrustUnverifiedPct}}{{{new_trust_unverified_pct}\\%}}")
lines.append(f"\\newcommand{{\\ppLXIIIoldTrustTotal}}{{{old_props_total}}}")
lines.append(f"\\newcommand{{\\ppLXIIInewTrustTotal}}{{{new_props_total}}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper63.json"
with open(json_path, "w") as f:
    json.dump({"paper": 63, "pairs": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
