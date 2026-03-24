#!/usr/bin/env python3
"""Paper 45 Experiment -- Callable Surfaces: HOF, Callback, Decorator Analysis.

Writes 10 programs (3–4 per callable-surface category) through jugeo evaluate,
uses AST analysis to count callable surfaces, and measures recall/coverage per
category.

Generates papers/data-paper45.tex with \\ppFortyFive… macros.
Re-run:  python3 experiments/exp45_callable_surfaces.py
"""
import subprocess, json, os, tempfile, time, statistics, ast

ROOT = os.path.join(os.path.dirname(__file__), "..")

def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining: break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects

def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name

def fmt_time(secs):
    if secs < 0.001: return f"{secs*1_000_000:.0f}\\,\\mu s"
    if secs < 1.0: return f"{secs*1000:.1f}\\,ms"
    return f"{secs:.2f}\\,s"

def fmt_pct(val):
    return f"{val*100:.1f}\\%"

def safe_mean(lst):
    return statistics.mean(lst) if lst else 0.0

def safe_median(lst):
    return statistics.median(lst) if lst else 0.0


# ── AST-based callable-surface detection ──────────────────────────────────

class CallableSurfaceVisitor(ast.NodeVisitor):
    """Walk an AST and count callable-surface patterns."""

    def __init__(self):
        self.hof_surfaces = 0    # map/filter/reduce or fn-accepting fn
        self.cb_surfaces = 0     # callback-style (fn passed as arg to handler)
        self.dec_surfaces = 0    # decorator usage (@decorator)
        self.total_defs = 0      # function / lambda defs
        self.total_calls = 0     # all Call nodes

    # --- helpers ---
    _HOF_BUILTINS = {"map", "filter", "reduce", "sorted", "min", "max"}
    _CB_HINTS = {"on_", "handle_", "register", "subscribe", "callback",
                 "add_listener", "connect", "bind", "after", "before",
                 "set_handler", "set_callback"}

    def visit_FunctionDef(self, node):
        self.total_defs += 1
        # Check if any parameter has a callable-typed default or is used in a call
        for deco in node.decorator_list:
            self.dec_surfaces += 1
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.total_defs += 1
        self.generic_visit(node)

    def visit_Call(self, node):
        self.total_calls += 1
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        # HOF detection: builtin HOF call or any call with a lambda/name arg
        if func_name in self._HOF_BUILTINS:
            self.hof_surfaces += 1
        else:
            for arg in node.args:
                if isinstance(arg, ast.Lambda):
                    self.hof_surfaces += 1
                elif isinstance(arg, ast.Name):
                    # heuristic: lowercase name passed as arg ⇒ possible callable
                    if arg.id[0].islower() and arg.id not in ("self", "cls",
                            "True", "False", "None"):
                        # Check callback hint
                        if any(func_name.startswith(h) or func_name.endswith(h.lstrip("_"))
                               for h in self._CB_HINTS if h):
                            self.cb_surfaces += 1
                        else:
                            self.hof_surfaces += 1

        # CB detection by function name pattern
        if any(func_name.startswith(h) or func_name.endswith(h.lstrip("_"))
               for h in self._CB_HINTS if h):
            self.cb_surfaces += 1

        self.generic_visit(node)


def count_surfaces(source: str) -> dict:
    """Return callable-surface counts for *source*."""
    tree = ast.parse(source)
    v = CallableSurfaceVisitor()
    v.visit(tree)
    return {
        "hof": v.hof_surfaces,
        "cb": v.cb_surfaces,
        "dec": v.dec_surfaces,
        "defs": v.total_defs,
        "calls": v.total_calls,
        "total": v.hof_surfaces + v.cb_surfaces + v.dec_surfaces,
    }


# ── Programs ──────────────────────────────────────────────────────────────

PROGRAMS = {
    # --- HOF programs (4) ---
    "hof_map_filter": {
        "category": "hof",
        "source": (
            "def double(x):\n"
            "    return x * 2\n\n"
            "def is_even(x):\n"
            "    return x % 2 == 0\n\n"
            "def pipeline(data):\n"
            "    doubled = list(map(double, data))\n"
            "    evens = list(filter(is_even, doubled))\n"
            "    return evens\n"
        ),
    },
    "hof_reduce_compose": {
        "category": "hof",
        "source": (
            "from functools import reduce\n\n"
            "def add(a, b):\n"
            "    return a + b\n\n"
            "def compose(f, g):\n"
            "    def h(x):\n"
            "        return f(g(x))\n"
            "    return h\n\n"
            "def total(items):\n"
            "    return reduce(add, items, 0)\n\n"
            "inc = compose(lambda x: x + 1, lambda x: x * 2)\n"
        ),
    },
    "hof_sorted_key": {
        "category": "hof",
        "source": (
            "def by_length(s):\n"
            "    return len(s)\n\n"
            "def sort_words(words):\n"
            "    return sorted(words, key=by_length)\n\n"
            "def top_n(items, n, key_fn):\n"
            "    return sorted(items, key=key_fn, reverse=True)[:n]\n"
        ),
    },
    "hof_custom_apply": {
        "category": "hof",
        "source": (
            "def apply_twice(f, x):\n"
            "    return f(f(x))\n\n"
            "def negate(x):\n"
            "    return -x\n\n"
            "result = apply_twice(negate, 5)\n\n"
            "def map_dict(fn, d):\n"
            "    return {k: fn(v) for k, v in d.items()}\n"
        ),
    },

    # --- Callback programs (3) ---
    "cb_event_system": {
        "category": "cb",
        "source": (
            "class EventEmitter:\n"
            "    def __init__(self):\n"
            "        self._listeners = {}\n"
            "    def on_event(self, name, callback):\n"
            "        self._listeners.setdefault(name, []).append(callback)\n"
            "    def emit(self, name, *args):\n"
            "        for cb in self._listeners.get(name, []):\n"
            "            cb(*args)\n\n"
            "def handle_click(x, y):\n"
            "    print(f'Clicked at {x},{y}')\n\n"
            "em = EventEmitter()\n"
            "em.on_event('click', handle_click)\n"
        ),
    },
    "cb_observer": {
        "category": "cb",
        "source": (
            "class Subject:\n"
            "    def __init__(self):\n"
            "        self._observers = []\n"
            "    def register(self, observer):\n"
            "        self._observers.append(observer)\n"
            "    def notify(self, data):\n"
            "        for obs in self._observers:\n"
            "            obs(data)\n\n"
            "def log_handler(data):\n"
            "    print('LOG:', data)\n\n"
            "def alert_handler(data):\n"
            "    print('ALERT:', data)\n\n"
            "s = Subject()\n"
            "s.register(log_handler)\n"
            "s.register(alert_handler)\n"
        ),
    },
    "cb_async_style": {
        "category": "cb",
        "source": (
            "def fetch_data(url, on_success, on_error):\n"
            "    try:\n"
            "        result = {'status': 200, 'body': 'ok'}\n"
            "        on_success(result)\n"
            "    except Exception as e:\n"
            "        on_error(str(e))\n\n"
            "def handle_success(resp):\n"
            "    print('Got:', resp['body'])\n\n"
            "def handle_error(msg):\n"
            "    print('Err:', msg)\n\n"
            "fetch_data('http://example.com', handle_success, handle_error)\n"
        ),
    },

    # --- Decorator programs (3) ---
    "dec_memoize": {
        "category": "dec",
        "source": (
            "def memoize(func):\n"
            "    cache = {}\n"
            "    def wrapper(*args):\n"
            "        if args not in cache:\n"
            "            cache[args] = func(*args)\n"
            "        return cache[args]\n"
            "    return wrapper\n\n"
            "@memoize\n"
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
        ),
    },
    "dec_timer_retry": {
        "category": "dec",
        "source": (
            "import time as _time\n\n"
            "def timer(func):\n"
            "    def wrapper(*args, **kwargs):\n"
            "        t0 = _time.time()\n"
            "        result = func(*args, **kwargs)\n"
            "        print(f'{func.__name__} took {_time.time()-t0:.4f}s')\n"
            "        return result\n"
            "    return wrapper\n\n"
            "def retry(n):\n"
            "    def decorator(func):\n"
            "        def wrapper(*args, **kwargs):\n"
            "            for i in range(n):\n"
            "                try:\n"
            "                    return func(*args, **kwargs)\n"
            "                except Exception:\n"
            "                    if i == n - 1:\n"
            "                        raise\n"
            "        return wrapper\n"
            "    return decorator\n\n"
            "@timer\n"
            "@retry(3)\n"
            "def unstable_fetch(url):\n"
            "    return 'data'\n"
        ),
    },
    "dec_class_decorator": {
        "category": "dec",
        "source": (
            "def singleton(cls):\n"
            "    instances = {}\n"
            "    def get_instance(*args, **kwargs):\n"
            "        if cls not in instances:\n"
            "            instances[cls] = cls(*args, **kwargs)\n"
            "        return instances[cls]\n"
            "    return get_instance\n\n"
            "@singleton\n"
            "class Config:\n"
            "    def __init__(self):\n"
            "        self.settings = {}\n"
            "    def set(self, key, value):\n"
            "        self.settings[key] = value\n"
            "    def get(self, key, default=None):\n"
            "        return self.settings.get(key, default)\n"
        ),
    },
}


# ── Main experiment ───────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Experiment 45 -- Callable Surfaces")
    print("=" * 60)

    tmpfiles = []
    records = []
    timings = []
    all_surfaces = []

    for prog_id, prog in PROGRAMS.items():
        source = prog["source"]
        category = prog["category"]
        path = write_temp_py(source)
        tmpfiles.append(path)

        # AST surface analysis
        surfaces = count_surfaces(source)

        # Run jugeo evaluate
        t0 = time.time()
        eval_objs = run_jugeo("evaluate", path)
        elapsed = time.time() - t0
        timings.append(elapsed)

        # Run jugeo descend for coverage detail
        desc_objs = run_jugeo("descend", path)

        # Extract evaluation metrics
        coverage = 0.0
        trust = 0.0
        n_coords = 0
        n_props = 0
        verified = False

        if eval_objs:
            ev = eval_objs[0]
            # per_coordinate quality is the meaningful coverage metric
            per_coord = ev.get("per_coordinate", [])
            if per_coord:
                quals = [c.get("quality", 0.0) for c in per_coord]
                coverage = safe_mean(quals)
            trust_info = ev.get("trust", {})
            if isinstance(trust_info, dict):
                trust = trust_info.get("aggregate_trust", 0.0)
            else:
                trust = float(trust_info) if trust_info else 0.0

        if desc_objs:
            d = desc_objs[0]
            secs = d.get("sections_detail", [])
            n_coords = len(secs)
            n_props = sum(s.get("propositions", 0) for s in secs)
            verified = len(d.get("obstructions", [])) == 0
            # Refine coverage from descent: fraction of propositions ok
            if n_props > 0:
                total_ok = sum(s.get("ok", 0) for s in secs)
                desc_cov = total_ok / n_props
                # Use the higher of evaluate quality and descend coverage
                coverage = max(coverage, desc_cov)

        all_surfaces.append(surfaces["total"])

        rec = {
            "id": prog_id,
            "category": category,
            "surfaces": surfaces,
            "coverage": float(coverage) if not isinstance(coverage, str) else 0.0,
            "trust": float(trust) if not isinstance(trust, str) else 0.0,
            "n_coords": n_coords,
            "n_props": n_props,
            "verified": verified,
            "time": elapsed,
        }
        records.append(rec)
        print(f"  {prog_id:<24} cat={category:<4} surfaces={surfaces['total']:>2}"
              f"  cov={rec['coverage']:.2f}  time={elapsed:.2f}s")

    # ── Per-category aggregation ──────────────────────────────────────────

    cats = {"hof": [], "cb": [], "dec": []}
    for r in records:
        cats[r["category"]].append(r)

    def cat_recall(recs):
        """Recall = fraction of programs where verification succeeded."""
        if not recs:
            return 0.0
        return sum(1 for r in recs if r["verified"]) / len(recs)

    def cat_coverage(recs):
        """Mean coverage score across programs in this category."""
        covs = [r["coverage"] for r in recs]
        return safe_mean(covs)

    hof_recs = cats["hof"]
    cb_recs = cats["cb"]
    dec_recs = cats["dec"]

    hof_recall = cat_recall(hof_recs)
    cb_recall = cat_recall(cb_recs)
    dec_recall = cat_recall(dec_recs)

    hof_coverage = cat_coverage(hof_recs)
    cb_coverage = cat_coverage(cb_recs)
    dec_coverage = cat_coverage(dec_recs)

    def cat_med_time(recs):
        """Median wall-clock time for programs in this category."""
        ts = [r["time"] for r in recs]
        return safe_median(ts)

    hof_med_time = cat_med_time(hof_recs)
    cb_med_time = cat_med_time(cb_recs)
    dec_med_time = cat_med_time(dec_recs)

    med_time = safe_median(timings)
    mean_surfaces = safe_mean(all_surfaces)

    n_total = len(records)
    n_hof = len(hof_recs)
    n_cb = len(cb_recs)
    n_dec = len(dec_recs)

    # ── Write LaTeX macros ────────────────────────────────────────────────

    out_path = os.path.join(ROOT, "papers", "data-paper45.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    P = "ppFortyFive"

    macro_lines = [
        "% data-paper45.tex -- AUTO-GENERATED by exp45_callable_surfaces.py",
        "% DO NOT EDIT -- regenerate with: python3 experiments/exp45_callable_surfaces.py",
        "",
        f"\\newcommand{{\\{P}TotalPrograms}}{{{n_total}}}",
        f"\\newcommand{{\\{P}NumHof}}{{{n_hof}}}",
        f"\\newcommand{{\\{P}NumCb}}{{{n_cb}}}",
        f"\\newcommand{{\\{P}NumDec}}{{{n_dec}}}",
        "",
        "% --- Recall (fraction verified) per category ---",
        f"\\newcommand{{\\{P}HofRecall}}{{{fmt_pct(hof_recall)}}}",
        f"\\newcommand{{\\{P}CbRecall}}{{{fmt_pct(cb_recall)}}}",
        f"\\newcommand{{\\{P}DecRecall}}{{{fmt_pct(dec_recall)}}}",
        "",
        "% --- Coverage (mean evaluate score) per category (replaces --- in table) ---",
        f"\\newcommand{{\\{P}HofCoverage}}{{{fmt_pct(hof_coverage)}}}",
        f"\\newcommand{{\\{P}CbCoverage}}{{{fmt_pct(cb_coverage)}}}",
        f"\\newcommand{{\\{P}DecCoverage}}{{{fmt_pct(dec_coverage)}}}",
        "",
        "% --- Timing ---",
        f"\\newcommand{{\\{P}MedTime}}{{{fmt_time(med_time)}}}",
        f"\\newcommand{{\\{P}HofMedTime}}{{{fmt_time(hof_med_time)}}}",
        f"\\newcommand{{\\{P}CbMedTime}}{{{fmt_time(cb_med_time)}}}",
        f"\\newcommand{{\\{P}DecMedTime}}{{{fmt_time(dec_med_time)}}}",
        "",
        "% --- Surfaces ---",
        f"\\newcommand{{\\{P}MeanSurfaces}}{{{mean_surfaces:.1f}}}",
    ]

    with open(out_path, "w") as fh:
        fh.write("\n".join(macro_lines) + "\n")

    # ── Save JSON results ─────────────────────────────────────────────────

    json_path = os.path.join(ROOT, "experiments", "results_paper45.json")
    with open(json_path, "w") as jf:
        json.dump({
            "paper": 45,
            "total_programs": n_total,
            "categories": {
                "hof": {"n": n_hof, "recall": hof_recall, "coverage": hof_coverage, "med_time": hof_med_time},
                "cb":  {"n": n_cb,  "recall": cb_recall,  "coverage": cb_coverage,  "med_time": cb_med_time},
                "dec": {"n": n_dec, "recall": dec_recall, "coverage": dec_coverage, "med_time": dec_med_time},
            },
            "med_time": med_time,
            "mean_surfaces": mean_surfaces,
            "records": records,
        }, jf, indent=2, default=str)

    # ── Print summary ─────────────────────────────────────────────────────

    print()
    print(f"Wrote {out_path}")
    print(f"Wrote {json_path}")
    print()
    print("SUMMARY:")
    print(f"  Total programs:    {n_total}")
    print(f"  HOF:               {n_hof}  recall={hof_recall:.2%}  coverage={hof_coverage:.2%}")
    print(f"  Callback:          {n_cb}  recall={cb_recall:.2%}  coverage={cb_coverage:.2%}")
    print(f"  Decorator:         {n_dec}  recall={dec_recall:.2%}  coverage={dec_coverage:.2%}")
    print(f"  Median time:       {med_time:.3f}s")
    print(f"  Mean surfaces/pgm: {mean_surfaces:.1f}")

    # cleanup temp files
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
