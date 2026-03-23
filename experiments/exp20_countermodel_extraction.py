#!/usr/bin/env python3
"""Paper 20 Experiment — Countermodel Extraction: Diagnostic Synthesis.

Runs four jugeo pipeline stages (encode, bugs, evaluate, descend) on programs
from three domains (arithmetic, collections, higher-order).  Measures:
  - Raw model size  = coordinates + morphisms from ``jugeo load --coordinates``
  - Minimized size  = local_sections after ``jugeo descend`` (sheaf gluing)
  - Per-stage wall-clock times for encode / bugs / evaluate / descend

Every number is reproducible: run `python3 experiments/exp20_countermodel_extraction.py`.
Writes macros to papers/data-paper20.tex with prefix ppTwenty.
Writes results to experiments/results_paper20.json.
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# ── helpers ──────────────────────────────────────────────────────────────

def run_jugeo_timed(*args):
    """Run jugeo CLI, return (elapsed_s, parsed_json_or_None)."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    elapsed = time.perf_counter() - t0
    lines = [l for l in result.stdout.splitlines()
             if not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(text.lstrip())
    except (json.JSONDecodeError, ValueError):
        obj = None
    return elapsed, obj


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


def fmt_s(val):
    return f"{val:.4f}\\,s"


def fmt_pct(val):
    return f"{val:.0f}\\%"


# ── test programs by domain ───────────────────────────────────────────────
#
# Each domain has 3 programs.  Programs intentionally have bugs (division by
# zero, missing bounds checks, closure/decorator misuse) so that jugeo can
# identify structural issues via SMT encoding and descent analysis.

PROGRAMS = {
    # ── Arithmetic ────────────────────────────────────────────────────────
    "arith_div_zero": {
        "domain": "arith",
        "source": '''\
def divide(a, b):
    # BUG: no zero-divisor guard
    return a / b

def ratio(x, total):
    return divide(x, total)

def normalize(values):
    total = sum(values)
    return [ratio(v, total) for v in values]
''',
    },
    "arith_off_by_one": {
        "domain": "arith",
        "source": '''\
def prefix_sum(arr):
    # BUG: off-by-one — iterates to len(arr) inclusive
    result = [0] * (len(arr) + 1)
    for i in range(1, len(arr) + 1):
        result[i] = result[i - 1] + arr[i]
    return result

def range_check(lo, hi, val):
    return lo <= val <= hi

def bounded_sum(arr, lo, hi):
    return sum(x for x in arr if range_check(lo, hi, x))
''',
    },
    "arith_overflow": {
        "domain": "arith",
        "source": '''\
def power_mod(base, exp, mod):
    # BUG: misses mod reduction on result accumulation
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = result * base  # should be (result * base) % mod
        exp = exp >> 1
        base = (base * base) % mod
    return result

def safe_add(a, b, limit):
    if a + b > limit:
        raise OverflowError("sum exceeds limit")
    return a + b

def gcd(a, b):
    while b:
        a, b = b, a % b
    return a
''',
    },

    # ── Collections ───────────────────────────────────────────────────────
    "coll_index_oob": {
        "domain": "coll",
        "source": '''\
def get_nth(items, n):
    # BUG: no bounds check
    return items[n]

def head(items):
    return get_nth(items, 0)

def last(items):
    return get_nth(items, len(items))  # BUG: should be len-1

def slice_pair(items):
    return head(items), last(items)
''',
    },
    "coll_empty_access": {
        "domain": "coll",
        "source": '''\
def first(items):
    # BUG: no empty-list guard
    return items[0]

def rest(items):
    return items[1:]

def minimum(items):
    m = first(items)
    for x in rest(items):
        if x < m:
            m = x
    return m

def maximum(items):
    m = first(items)
    for x in rest(items):
        if x > m:
            m = x
    return m
''',
    },
    "coll_mutation": {
        "domain": "coll",
        "source": '''\
def remove_odds(items):
    # BUG: mutates list while iterating
    for x in items:
        if x % 2 != 0:
            items.remove(x)
    return items

def keep_evens(items):
    return remove_odds(list(items))

def partition(items):
    evens = [x for x in items if x % 2 == 0]
    odds = remove_odds([x for x in items if x % 2 != 0])
    return evens, odds

def flatten(nested):
    result = []
    for sub in nested:
        result.extend(sub)
    return result
''',
    },

    # ── Higher-order ──────────────────────────────────────────────────────
    "ho_callback": {
        "domain": "ho",
        "source": '''\
def apply_twice(f, x):
    # BUG: passes result of first call as second arg positionally
    return f(f(x), x)

def increment(n):
    return n + 1

def double(n):
    return n * 2

def pipeline(fns, x):
    for f in fns:
        x = f(x)
    return x
''',
    },
    "ho_closure": {
        "domain": "ho",
        "source": '''\
def make_adders(n):
    # BUG: all closures capture the same loop variable
    adders = []
    for i in range(n):
        adders.append(lambda x: x + i)
    return adders

def compose(f, g):
    return lambda x: f(g(x))

def memoize(f):
    cache = {}
    def wrapper(x):
        if x not in cache:
            cache[x] = f(x)
        return cache[x]
    return wrapper

def apply_all(fns, x):
    return [f(x) for f in fns]
''',
    },
    "ho_decorator": {
        "domain": "ho",
        "source": '''\
def logger(func):
    # BUG: wrapper forgets to return the result
    def wrapper(*args, **kwargs):
        func(*args, **kwargs)
    return wrapper

def retry(times):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for _ in range(times):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    pass
            return None
        return wrapper
    return decorator

@logger
def compute(a, b):
    return a + b

@retry(times=3)
def fetch(url):
    return f"data from {url}"
''',
    },
}

DOMAIN_LABELS = {"arith": "Arithmetic", "coll": "Collections", "ho": "Higher-order"}


# ── main ─────────────────────────────────────────────────────────────────

def main():
    tmpfiles = []
    n_total = len(PROGRAMS)
    print(f"Paper 20 — Countermodel Extraction Experiment")
    print(f"Programs: {n_total} ({sum(1 for p in PROGRAMS.values() if p['domain']=='arith')} arith, "
          f"{sum(1 for p in PROGRAMS.values() if p['domain']=='coll')} coll, "
          f"{sum(1 for p in PROGRAMS.values() if p['domain']=='ho')} ho)")
    print("=" * 76)

    results = {}

    for prog_name, prog in PROGRAMS.items():
        path = write_temp_py(prog["source"])
        tmpfiles.append(path)
        print(f"\n  [{prog['domain']}] {prog_name}")

        # Stage 1: encode (SMT formula generation)
        print(f"    encode ...", end=" ", flush=True)
        t_encode, enc = run_jugeo_timed("encode", path)
        enc_coords = 0
        if isinstance(enc, dict):
            files = enc.get("files", [])
            if files:
                enc_coords = sum(len(f.get("coordinates", {})) for f in files)
        print(f"t={t_encode:.3f}s  enc_coords={enc_coords}")

        # Stage 2: bugs (bug detection / analysis)
        print(f"    bugs   ...", end=" ", flush=True)
        t_bugs, bugs_out = run_jugeo_timed("bugs", path)
        bugs_list = []
        if isinstance(bugs_out, list) and bugs_out:
            bugs_list = bugs_out[0].get("bugs", [])
        elif isinstance(bugs_out, dict):
            bugs_list = bugs_out.get("bugs", [])
        n_bugs = len(bugs_list)
        print(f"t={t_bugs:.3f}s  bugs={n_bugs}")

        # Stage 3: evaluate (trust/coverage evaluation)
        print(f"    evaluate ...", end=" ", flush=True)
        t_eval, eval_out = run_jugeo_timed("evaluate", path)
        settled = 0
        if isinstance(eval_out, dict):
            settled = eval_out.get("descent", {}).get("settled_count", 0)
        print(f"t={t_eval:.3f}s  settled={settled}")

        # Stage 4: descend (gluing/sheaf verification — used for model sizing)
        print(f"    descend ...", end=" ", flush=True)
        t_descend, desc_out = run_jugeo_timed("descend", path)
        local_sections = 0
        obstructions = []
        verdict = "unknown"
        if isinstance(desc_out, dict):
            local_sections = desc_out.get("local_sections", 0)
            obstructions = desc_out.get("obstructions", [])
            verdict = desc_out.get("verdict", "unknown")
        print(f"t={t_descend:.3f}s  local_sec={local_sections} obs={len(obstructions)}")

        # Raw model size: total coordinates from encode + morphisms from load
        # Use encode coordinate count as raw, local_sections as minimized
        t_load, load_out = run_jugeo_timed("load", "--coordinates", path)
        raw_coords = 0
        raw_morphisms = 0
        if isinstance(load_out, dict):
            summary = load_out.get("summary", {})
            raw_coords = summary.get("coordinates", 0)
            raw_morphisms = summary.get("morphisms", 0)
        raw_size = raw_coords + raw_morphisms
        min_size = local_sections  # after sheaf/descent gluing

        results[prog_name] = {
            "domain": prog["domain"],
            "t_encode": round(t_encode, 4),
            "t_bugs": round(t_bugs, 4),
            "t_eval": round(t_eval, 4),
            "t_descend": round(t_descend, 4),
            "t_total": round(t_encode + t_bugs + t_eval + t_descend, 4),
            "raw_size": raw_size,
            "min_size": min_size,
            "n_bugs": n_bugs,
            "verdict": verdict,
            "obstructions": len(obstructions),
            "local_sections": local_sections,
        }

    # ── Compute per-domain model-size metrics ─────────────────────────────
    print("\n── Per-domain model size metrics ──")
    domain_metrics = {}
    for dom in ("arith", "coll", "ho"):
        rows = [r for r in results.values() if r["domain"] == dom]
        raw_vals = [r["raw_size"] for r in rows]
        min_vals = [r["min_size"] for r in rows]
        mean_raw = statistics.mean(raw_vals) if raw_vals else 0.0
        mean_min = statistics.mean(min_vals) if min_vals else 0.0
        reduction = (1.0 - mean_min / max(mean_raw, 0.001)) * 100.0 if mean_raw > 0 else 0.0
        domain_metrics[dom] = {
            "mean_raw": round(mean_raw, 1),
            "mean_min": round(mean_min, 1),
            "reduction": round(reduction, 1),
        }
        print(f"  {DOMAIN_LABELS[dom]:<14}  raw={mean_raw:.1f}  min={mean_min:.1f}  reduction={reduction:.0f}%")

    # ── Compute per-stage latency (medians across all programs) ───────────
    print("\n── Per-stage latency (median across all programs) ──")
    med_encode  = statistics.median([r["t_encode"]  for r in results.values()])
    med_bugs    = statistics.median([r["t_bugs"]    for r in results.values()])
    med_eval    = statistics.median([r["t_eval"]    for r in results.values()])
    med_descend = statistics.median([r["t_descend"] for r in results.values()])
    total_time  = med_encode + med_bugs + med_eval + med_descend  # exact sum

    print(f"  Encoding:   {med_encode:.4f}s")
    print(f"  Analysis:   {med_bugs:.4f}s")
    print(f"  Evaluation: {med_eval:.4f}s")
    print(f"  Descent:    {med_descend:.4f}s")
    print(f"  Total:      {total_time:.4f}s  (= sum of above)")

    # ── Save JSON ─────────────────────────────────────────────────────────
    output = {
        "experiment": "countermodel_extraction",
        "paper": 20,
        "note": "All numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_total": n_total,
        "programs": results,
        "domain_metrics": domain_metrics,
        "stage_latency": {
            "encode":  round(med_encode,  4),
            "bugs":    round(med_bugs,    4),
            "eval":    round(med_eval,    4),
            "descend": round(med_descend, 4),
            "total":   round(total_time,  4),
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper20.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults → {json_path}")

    # ── Write LaTeX macros ────────────────────────────────────────────────
    tex_path = os.path.join(ROOT, "papers", "data-paper20.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w") as f:
        f.write("% data-paper20.tex — AUTO-GENERATED by exp20_countermodel_extraction.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp20_countermodel_extraction.py\n\n")

        # Per-domain model-size macros (Table 2)
        f.write("% Table 2: per-domain model size (raw = coordinates+morphisms from load;\n")
        f.write("%          min = local_sections after descend/gluing)\n")
        dm = domain_metrics
        write_macro(f, "ppTwentyArithRawSize",  f"{dm['arith']['mean_raw']:.1f}")
        write_macro(f, "ppTwentyArithMinSize",  f"{dm['arith']['mean_min']:.1f}")
        write_macro(f, "ppTwentyArithReduction", fmt_pct(dm['arith']['reduction']))
        f.write("\n")
        write_macro(f, "ppTwentyCollRawSize",  f"{dm['coll']['mean_raw']:.1f}")
        write_macro(f, "ppTwentyCollMinSize",  f"{dm['coll']['mean_min']:.1f}")
        write_macro(f, "ppTwentyCollReduction", fmt_pct(dm['coll']['reduction']))
        f.write("\n")
        write_macro(f, "ppTwentyHORawSize",  f"{dm['ho']['mean_raw']:.1f}")
        write_macro(f, "ppTwentyHOMinSize",  f"{dm['ho']['mean_min']:.1f}")
        write_macro(f, "ppTwentyHOReduction", fmt_pct(dm['ho']['reduction']))
        f.write("\n")

        # Per-stage latency macros (Table 3)
        f.write("% Table 3: per-stage pipeline latency (medians; Total = exact sum)\n")
        write_macro(f, "ppTwentyEncodeTime",  fmt_s(med_encode))
        write_macro(f, "ppTwentyBugsTime",    fmt_s(med_bugs))
        write_macro(f, "ppTwentyEvalTime",    fmt_s(med_eval))
        write_macro(f, "ppTwentyDescendTime", fmt_s(med_descend))
        write_macro(f, "ppTwentyTotalTime",   fmt_s(total_time))
        f.write("\n")

        # Summary counts
        f.write("% Summary counts\n")
        write_macro(f, "ppTwentyTotalPrograms",    n_total)
        write_macro(f, "ppTwentyArithPrograms",
                    sum(1 for p in PROGRAMS.values() if p["domain"] == "arith"))
        write_macro(f, "ppTwentyCollPrograms",
                    sum(1 for p in PROGRAMS.values() if p["domain"] == "coll"))
        write_macro(f, "ppTwentyHOPrograms",
                    sum(1 for p in PROGRAMS.values() if p["domain"] == "ho"))

    print(f"LaTeX  → {tex_path}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
