#!/usr/bin/env python3
"""Paper 30 Experiment — Semantic Control Laws (verification control).

Uses orchestrate_verification() from SiteBuilder, CLI descend with different
strategies (eager, exhaustive), and CyclicSystemCoordinator for control-loop
metrics.  Measures site complexity, descent strategy comparison, and control
loop convergence.

Outputs: papers/data-paper30.tex  (LaTeX macros with \\ppThirty… prefix)
Re-run:  python3 experiments/exp30_semantic_control.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

# ---------------------------------------------------------------------------
# Test programs (10 diverse programs)
# ---------------------------------------------------------------------------
PROGRAMS = [
    {"id": "gcd", "code": """
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a
"""},
    {"id": "fib_memo", "code": """
def fib(n, memo={}):
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fib(n-1, memo) + fib(n-2, memo)
    return memo[n]
"""},
    {"id": "matrix_mul", "code": """
def matmul(A, B):
    rows_A, cols_A = len(A), len(A[0])
    cols_B = len(B[0])
    C = [[0]*cols_B for _ in range(rows_A)]
    for i in range(rows_A):
        for j in range(cols_B):
            for k in range(cols_A):
                C[i][j] += A[i][k] * B[k][j]
    return C
"""},
    {"id": "topo_sort", "code": """
def topo_sort(graph):
    visited = set()
    order = []
    def dfs(node):
        if node in visited:
            return
        visited.add(node)
        for neighbor in graph.get(node, []):
            dfs(neighbor)
        order.append(node)
    for node in graph:
        dfs(node)
    return list(reversed(order))
"""},
    {"id": "lru_cache", "code": """
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key in self.cache:
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key]
        return -1

    def put(self, key, value):
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            oldest = self.order.pop(0)
            del self.cache[oldest]
        self.cache[key] = value
        self.order.append(key)
"""},
    {"id": "regex_match", "code": """
def match(pattern, text):
    if not pattern:
        return not text
    first_match = bool(text) and pattern[0] in (text[0], '.')
    if len(pattern) >= 2 and pattern[1] == '*':
        return (match(pattern[2:], text) or
                (first_match and match(pattern, text[1:])))
    return first_match and match(pattern[1:], text[1:])
"""},
    {"id": "interval_merge", "code": """
def merge_intervals(intervals):
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
"""},
    {"id": "tree_traversal", "code": """
class TreeNode:
    def __init__(self, val, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.val] + inorder(root.right)

def level_order(root):
    if root is None:
        return []
    queue = [root]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node.val)
        if node.left:
            queue.append(node.left)
        if node.right:
            queue.append(node.right)
    return result
"""},
    {"id": "state_machine", "code": """
class StateMachine:
    def __init__(self, transitions, initial, accepting):
        self.transitions = transitions
        self.state = initial
        self.accepting = accepting

    def step(self, symbol):
        key = (self.state, symbol)
        if key in self.transitions:
            self.state = self.transitions[key]
        else:
            self.state = None

    def accepts(self, symbols):
        self.state = list(self.transitions.keys())[0][0]  # reset
        for s in symbols:
            self.step(s)
            if self.state is None:
                return False
        return self.state in self.accepting
"""},
    {"id": "stream_processor", "code": """
class StreamProcessor:
    def __init__(self):
        self.filters = []
        self.transforms = []

    def add_filter(self, fn):
        self.filters.append(fn)

    def add_transform(self, fn):
        self.transforms.append(fn)

    def process(self, items):
        result = list(items)
        for f in self.filters:
            result = [x for x in result if f(x)]
        for t in self.transforms:
            result = [t(x) for x in result]
        return result
"""},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_jugeo(*args, timeout=30):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=ROOT, timeout=timeout)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo")]
    text = "\n".join(lines)
    decoder = json.JSONDecoder()
    objects = []
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects


def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False,
                                    dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def pct_str(val):
    return f"{val * 100:.1f}\\%"


def ms_str(val):
    return f"{val:.2f}\\,\\text{{ms}}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from jugeo.geometry import SiteBuilder, SiteDiagnostics
    from jugeo.maturity import CyclicSystemCoordinator

    print("Paper 30 — Semantic Control Laws Experiment")
    print("=" * 60)

    # Accumulators
    complexity_stats = []       # per-program site complexity
    strategy_results = {
        "eager": [],
        "exhaustive": [],
        "iterative": [],
    }
    control_loop_metrics = []   # CyclicSystemCoordinator data

    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])
        pid = prog["id"]

        try:
            # ---- Site complexity via CLI load ----
            load_objs = run_jugeo("load", tmp)
            load_data = load_objs[0] if load_objs else {}
            summary = load_data.get("summary", load_data)
            n_coords = summary.get("coordinates", 0)
            n_morphisms = summary.get("morphisms", 0)
            n_families = summary.get("covering_families", 0)
            n_judgments = summary.get("judgments", 0)
            n_bindings = summary.get("context_bindings", 0)

            # SiteDiagnostics for coverage
            site = SiteBuilder(prog["code"]).build()
            diag = SiteDiagnostics(site)
            coverage_ratio = diag.coverage_ratio()
            refinements = diag.suggest_refinements()
            n_refinements = (len(refinements) if isinstance(refinements, list)
                             else 0)

            complexity_stats.append({
                "id": pid,
                "coords": n_coords,
                "morphisms": n_morphisms,
                "families": n_families,
                "judgments": n_judgments,
                "coverage": coverage_ratio if isinstance(coverage_ratio, (int, float)) else 0,
                "refinements": n_refinements,
            })

            # ---- orchestrate_verification → ControlDecision object ----
            ov = site.orchestrate_verification()
            ov_goal = getattr(ov, 'goal', None)
            ov_notes = getattr(ov, 'notes', ())

            # ---- CLI descend (default / eager) ----
            t0 = time.perf_counter()
            desc_objs = run_jugeo("descend", tmp)
            t_eager = (time.perf_counter() - t0) * 1000
            desc_eager = desc_objs[0] if desc_objs else {}
            eager_verdict = desc_eager.get("verdict", "unknown")
            eager_sections = desc_eager.get("local_sections", 0)
            eager_overlaps = desc_eager.get("overlap_conditions_checked", 0)

            strategy_results["eager"].append({
                "id": pid, "time_ms": t_eager,
                "verdict": eager_verdict,
                "sections": eager_sections,
                "overlaps": eager_overlaps,
            })

            # ---- Exhaustive: load + descend + evaluate ----
            t0 = time.perf_counter()
            run_jugeo("load", tmp)
            desc_objs2 = run_jugeo("descend", tmp)
            run_jugeo("evaluate", tmp)
            t_exhaustive = (time.perf_counter() - t0) * 1000
            desc_exh = desc_objs2[0] if desc_objs2 else {}

            strategy_results["exhaustive"].append({
                "id": pid, "time_ms": t_exhaustive,
                "verdict": desc_exh.get("verdict", "unknown"),
                "sections": desc_exh.get("local_sections", 0),
                "overlaps": desc_exh.get("overlap_conditions_checked", 0),
            })

            # ---- Iterative: SiteBuilder + orchestrate_verification ----
            t0 = time.perf_counter()
            site2 = SiteBuilder(prog["code"]).build()
            ov2 = site2.orchestrate_verification()
            t_iterative = (time.perf_counter() - t0) * 1000
            ov2_goal = getattr(ov2, 'goal', None)

            strategy_results["iterative"].append({
                "id": pid, "time_ms": t_iterative,
                "verdict": "verified" if ov2_goal is None and not getattr(ov2, 'reasons', ()) else "incomplete",
                "sections": eager_sections,
                "overlaps": eager_overlaps,
            })

            # ---- CyclicSystemCoordinator control loop ----
            coord = CyclicSystemCoordinator.create(pid)
            record = coord.run_full_cycle({"source": prog["code"]})
            met = coord.get_metrics().to_dict()

            cycles = met.get("total_cycles", met.get("cycles", 1))
            success_rate = met.get("success_rate", 0)
            mean_duration = met.get("mean_cycle_duration", 0)

            control_loop_metrics.append({
                "id": pid,
                "cycles": cycles if isinstance(cycles, (int, float)) else 1,
                "converged": success_rate >= 0.5,
                "avg_cycle_ms": (mean_duration * 1000
                                 if isinstance(mean_duration, (int, float))
                                 else 0),
                "success_rate": success_rate,
                "trust_score": met.get("mean_trust_score", 0),
            })

            print(f"  {pid:18s}  coords={n_coords:3d}  morph={n_morphisms:3d}  "
                  f"eager={t_eager:.1f}ms  exh={t_exhaustive:.1f}ms  "
                  f"iter={t_iterative:.1f}ms  cycles={cycles}")

        except Exception as e:
            print(f"  {pid:18s}  ERROR: {e}")
            complexity_stats.append({
                "id": pid, "coords": 0, "morphisms": 0,
                "families": 0, "judgments": 0, "coverage": 0,
                "refinements": 0,
            })
            for strat in strategy_results:
                strategy_results[strat].append({
                    "id": pid, "time_ms": 0, "verdict": "error",
                    "sections": 0, "overlaps": 0,
                })
            control_loop_metrics.append({
                "id": pid, "cycles": 0, "converged": False,
                "avg_cycle_ms": 0,
            })
        finally:
            cleanup(tmp)

    # ------------------------------------------------------------------
    # Aggregate & emit LaTeX
    # ------------------------------------------------------------------
    P = "ppThirty"
    tex = [
        "% data-paper30.tex — AUTO-GENERATED by exp30_semantic_control.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp30_semantic_control.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    # --- Site complexity summary ---
    print("\n" + "=" * 60)
    print("Site Complexity Summary")
    print("-" * 60)

    coords_list = [c["coords"] for c in complexity_stats]
    morphisms_list = [c["morphisms"] for c in complexity_stats]
    families_list = [c["families"] for c in complexity_stats]
    judgments_list = [c["judgments"] for c in complexity_stats]
    coverage_list = [c["coverage"] for c in complexity_stats
                     if isinstance(c["coverage"], (int, float))]

    def safe_mean(lst):
        return statistics.mean(lst) if lst else 0

    def safe_max(lst):
        return max(lst) if lst else 0

    avg_coords = safe_mean(coords_list)
    avg_morphisms = safe_mean(morphisms_list)
    avg_families = safe_mean(families_list)
    avg_judgments = safe_mean(judgments_list)
    avg_coverage = safe_mean(coverage_list)

    print(f"  Programs:    {len(PROGRAMS)}")
    print(f"  Avg coords:  {avg_coords:.1f}")
    print(f"  Avg morph:   {avg_morphisms:.1f}")
    print(f"  Avg families:{avg_families:.1f}")
    print(f"  Avg judgmts: {avg_judgments:.1f}")
    print(f"  Avg coverage:{avg_coverage:.2f}")

    m("ProgCount", len(PROGRAMS))
    m("AvgCoords", f"{avg_coords:.1f}")
    m("MaxCoords", safe_max(coords_list))
    m("AvgMorphisms", f"{avg_morphisms:.1f}")
    m("AvgFamilies", f"{avg_families:.1f}")
    m("AvgJudgments", f"{avg_judgments:.1f}")
    m("AvgCoverage", f"{avg_coverage:.2f}")
    m("TotalCoords", sum(coords_list))
    m("TotalMorphisms", sum(morphisms_list))

    # --- Strategy comparison ---
    print("\n" + "=" * 60)
    print(f"{'Strategy':<14} {'Avg ms':>8} {'P99 ms':>8} {'Verified%':>10} {'AvgSec':>7}")
    print("-" * 55)

    for strat, macro_label in [("eager", "Eager"),
                                ("exhaustive", "Exhaustive"),
                                ("iterative", "Iterative")]:
        entries = strategy_results[strat]
        times = [e["time_ms"] for e in entries]
        verified = sum(1 for e in entries if e["verdict"] == "verified")
        sections = [e["sections"] for e in entries]

        avg_t = safe_mean(times)
        sorted_t = sorted(times)
        p99_idx = max(0, int(len(sorted_t) * 0.99) - 1)
        p99_t = sorted_t[p99_idx] if sorted_t else 0
        ver_rate = verified / max(len(entries), 1)
        avg_sec = safe_mean(sections)

        print(f"  {strat:<12} {avg_t:>8.1f} {p99_t:>8.1f} {ver_rate*100:>9.1f}% {avg_sec:>7.1f}")

        m(f"{macro_label}AvgMs", ms_str(avg_t))
        m(f"{macro_label}PnnMs", ms_str(p99_t))
        m(f"{macro_label}Rate", pct_str(ver_rate))
        m(f"{macro_label}AvgSections", f"{avg_sec:.1f}")

    # --- Control loop convergence ---
    print("\n" + "=" * 60)
    print("Control Loop Convergence")
    print("-" * 60)

    cycle_counts = [c["cycles"] for c in control_loop_metrics
                    if isinstance(c["cycles"], (int, float))]
    converged_count = sum(1 for c in control_loop_metrics if c["converged"])
    cycle_times = [c["avg_cycle_ms"] for c in control_loop_metrics
                   if isinstance(c["avg_cycle_ms"], (int, float))]

    avg_cycles = safe_mean(cycle_counts)
    convergence_rate = converged_count / max(len(control_loop_metrics), 1)
    avg_cycle_time = safe_mean(cycle_times)

    print(f"  Avg cycles:       {avg_cycles:.1f}")
    print(f"  Convergence rate: {convergence_rate*100:.1f}%")
    print(f"  Avg cycle time:   {avg_cycle_time:.2f} ms")

    m("AvgCycles", f"{avg_cycles:.1f}")
    m("MaxCycles", safe_max(cycle_counts) if cycle_counts else 0)
    m("ConvergenceRate", pct_str(convergence_rate))
    m("ConvergedCount", converged_count)
    m("AvgCycleTimeMs", ms_str(avg_cycle_time))

    # Write LaTeX
    tex_path = os.path.join(ROOT, "papers", "data-paper30.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "results_paper30.json")
    results = {
        "complexity": complexity_stats,
        "strategies": {s: v for s, v in strategy_results.items()},
        "control_loop": control_loop_metrics,
    }
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
