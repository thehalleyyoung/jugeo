#!/usr/bin/env python3
"""Paper 49 Experiment — Cyclic Maturity.

Measures cycle pass rate, median latency, and stale incidents across
multiple maturity cycles on 10 diverse programs using the
CyclicSystemCoordinator.

Outputs: papers/data-paper49.tex  (LaTeX macros with \\ppFortyNine… prefix)
Re-run:  python3 experiments/exp49_cyclic_maturity.py
"""
import subprocess, json, os, tempfile, time, statistics

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


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


PROGRAMS = [
    {"name": "binary_search", "code": """
def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
"""},
    {"name": "merge_sort", "code": """
def merge_sort(arr):
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    merged, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i]); i += 1
        else:
            merged.append(right[j]); j += 1
    return merged + left[i:] + right[j:]
"""},
    {"name": "fibonacci", "code": """
def fibonacci(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""},
    {"name": "matrix_multiply", "code": """
def matrix_multiply(a, b):
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result
"""},
    {"name": "dijkstra", "code": """
import heapq
def dijkstra(graph, start):
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float('inf')):
            continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float('inf')):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist
"""},
    {"name": "lru_cache", "code": """
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
            old = self.order.pop(0)
            del self.cache[old]
        self.cache[key] = value
        self.order.append(key)
"""},
    {"name": "knapsack", "code": """
def knapsack(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i-1][w]
            if weights[i-1] <= w:
                dp[i][w] = max(dp[i][w], dp[i-1][w - weights[i-1]] + values[i-1])
    return dp[n][capacity]
"""},
    {"name": "topo_sort", "code": """
def topological_sort(graph):
    visited = set()
    stack = []
    def dfs(node):
        visited.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor)
        stack.append(node)
    for node in graph:
        if node not in visited:
            dfs(node)
    return stack[::-1]
"""},
    {"name": "tree_depth", "code": """
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def max_depth(root):
    if root is None:
        return 0
    return 1 + max(max_depth(root.left), max_depth(root.right))
"""},
    {"name": "prime_sieve", "code": """
def sieve_of_eratosthenes(limit):
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i*i, limit + 1, i):
                is_prime[j] = False
    return [i for i in range(limit + 1) if is_prime[i]]
"""},
]

NUM_CYCLES = 4


def main():
    from jugeo.maturity import CyclicSystemCoordinator

    # per_cycle[cycle_idx] = {"pass_rates": [...], "latencies": [...], "stale": [...]}
    per_cycle = {i: {"pass_rates": [], "latencies": [], "stale": []} for i in range(NUM_CYCLES)}
    all_trust = []
    all_success = []
    total_cycles_run = 0

    for prog in PROGRAMS:
        tmp = write_temp_py(prog["code"])
        print(f"  Program: {prog['name']}")

        coord = CyclicSystemCoordinator.create(prog["name"])

        for cycle_idx in range(NUM_CYCLES):
            t0 = time.perf_counter()
            record, transitions = coord.run_full_cycle({"source": prog["code"]})
            latency = time.perf_counter() - t0
            total_cycles_run += 1

            metrics = coord.get_metrics().to_dict()

            # Pass rate: fraction of successful cycles so far
            pass_rate = metrics.get("success_rate", 0.0)
            trust = metrics.get("mean_trust_score", 0.0)

            # Stale incidents: obstructions accumulated indicate staleness
            stale = metrics.get("total_obstructions", 0)

            per_cycle[cycle_idx]["pass_rates"].append(pass_rate)
            per_cycle[cycle_idx]["latencies"].append(latency)
            per_cycle[cycle_idx]["stale"].append(stale)

            print(f"    cycle {cycle_idx}: pass_rate={pass_rate:.2f}  "
                  f"latency={latency:.3f}s  stale={stale}  trust={trust:.3f}")

        # Collect overall metrics after all cycles
        final_metrics = coord.get_metrics().to_dict()
        all_trust.append(final_metrics.get("mean_trust_score", 0.0))
        all_success.append(final_metrics.get("success_rate", 0.0))

        # Also run evaluate via CLI for additional coverage
        ev = run_jugeo("evaluate", tmp)
        cleanup(tmp)

    # Aggregate per-cycle
    cycle_agg = {}
    for ci in range(NUM_CYCLES):
        cycle_agg[ci] = {
            "pass_rate": safe_mean(per_cycle[ci]["pass_rates"]),
            "med_latency": safe_median(per_cycle[ci]["latencies"]),
            "stale": round(safe_mean(per_cycle[ci]["stale"])),
        }

    mean_trust = safe_mean(all_trust)
    overall_success = safe_mean(all_success)

    print("\n" + "=" * 60)
    print("CYCLIC MATURITY RESULTS")
    print(f"  Programs: {len(PROGRAMS)}   Total cycles: {total_cycles_run}")
    print(f"  Mean trust: {mean_trust:.3f}   Overall success: {overall_success:.3f}")
    for ci in range(NUM_CYCLES):
        a = cycle_agg[ci]
        print(f"  Cycle {ci}: pass_rate={a['pass_rate']:.3f}  "
              f"med_latency={a['med_latency']:.4f}s  stale={a['stale']}")

    # ── Generate LaTeX macros ──────────────────────────────────────────
    P = "ppFortyNine"
    tex = [
        f"% data-paper49.tex — AUTO-GENERATED by exp49_cyclic_maturity.py",
        f"% DO NOT EDIT — regenerate with: python3 experiments/exp49_cyclic_maturity.py",
        f"",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    m("TotalPrograms", len(PROGRAMS))
    m("TotalCycles", total_cycles_run)
    m("MeanTrust", f"{mean_trust:.3f}")
    m("SuccessRate", fmt_pct(overall_success))

    cycle_labels = ["Zero", "One", "Two", "Three"]
    for ci, label in enumerate(cycle_labels):
        a = cycle_agg[ci]
        m(f"Cycle{label}PassRate", fmt_pct(a["pass_rate"]))
        m(f"Cycle{label}Latency", fmt_time(a["med_latency"]))
        m(f"Cycle{label}Stale", a["stale"])

    tex_path = os.path.join(ROOT, "papers", "data-paper49.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper49.json")
    with open(json_path, "w") as f:
        json.dump({
            "n_programs": len(PROGRAMS),
            "total_cycles": total_cycles_run,
            "mean_trust": mean_trust,
            "overall_success": overall_success,
            "per_cycle": {str(k): v for k, v in cycle_agg.items()},
        }, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
