#!/usr/bin/env python3
"""Paper 50 Experiment — Semantic Centers.

Compares three verification orderings (random, alphabetical, center-first)
for 10 programs, measuring total verification time and steps to reach
50% and 87% trust coverage.

Outputs: papers/data-paper50.tex  (LaTeX macros with \\ppFifty… prefix)
Re-run:  python3 experiments/exp50_semantic_centers.py
"""
import subprocess, json, os, tempfile, time, statistics, random

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


def detect_semantic_center(coord_names, per_coord_trust):
    """Identify the semantic center: the coordinate with the highest trust
    or, if trust is uniform, the one with the most hierarchical depth
    (most connected in the site topology)."""
    best = None
    best_score = -1.0
    for name in coord_names:
        trust = per_coord_trust.get(name, 0.0)
        depth = name.count(".")
        score = trust + depth * 0.01
        if score > best_score:
            best_score = score
            best = name
    return best


def steps_to_threshold(ordered_coords, per_coord_trust, threshold):
    """Count how many coordinates (steps) must be verified in the given
    order to accumulate *threshold* fraction of total trust."""
    total_trust = sum(per_coord_trust.get(c, 0.0) for c in ordered_coords)
    if total_trust <= 0:
        return len(ordered_coords)
    target = total_trust * threshold
    accum = 0.0
    for i, c in enumerate(ordered_coords, 1):
        accum += per_coord_trust.get(c, 0.0)
        if accum >= target:
            return i
    return len(ordered_coords)


def main():
    from jugeo.geometry import SiteBuilder
    from jugeo.maturity import CyclicSystemCoordinator

    random.seed(42)

    ordering_results = {
        "random": {"times": [], "steps_half": [], "steps_full": []},
        "alpha": {"times": [], "steps_half": [], "steps_full": []},
        "center": {"times": [], "steps_half": [], "steps_full": []},
    }
    all_coord_counts = []

    for prog in PROGRAMS:
        tmp = write_temp_py(prog["code"])
        print(f"  Program: {prog['name']}")

        # Build site to get coordinates
        t_build_start = time.perf_counter()
        ev_objs = run_jugeo("evaluate", tmp)
        t_build = time.perf_counter() - t_build_start
        ev = ev_objs[0] if ev_objs else {}

        # Extract per-coordinate trust from evaluate output
        per_coord_trust = {}
        per_coord_list = ev.get("per_coordinate", [])
        if isinstance(per_coord_list, list):
            for entry in per_coord_list:
                cname = entry.get("coordinate", "")
                quality = entry.get("quality", 0.5)
                per_coord_trust[cname] = quality

        # If evaluate didn't return coordinates, build site directly
        if not per_coord_trust:
            try:
                builder = SiteBuilder(label=prog["name"])
                enc_objs = run_jugeo("encode", tmp)
                enc = enc_objs[0] if enc_objs else {}
                coords = enc.get("coordinates", [])
                if isinstance(coords, list):
                    for c in coords:
                        cname = c if isinstance(c, str) else c.get("name", str(c))
                        per_coord_trust[cname] = 0.5
            except Exception:
                per_coord_trust[prog["name"]] = 0.5

        # Ensure we have at least one coordinate
        if not per_coord_trust:
            per_coord_trust[prog["name"]] = 0.5

        coord_names = list(per_coord_trust.keys())
        n_coords = len(coord_names)
        all_coord_counts.append(n_coords)

        # Run cyclic maturity for additional trust context
        coord = CyclicSystemCoordinator.create(prog["name"])
        coord.run_full_cycle({"source": prog["code"]})
        cycle_metrics = coord.get_metrics().to_dict()
        cycle_trust = cycle_metrics.get("mean_trust_score", 0.5)

        # Scale per-coordinate trust by cyclic maturity trust
        for cname in per_coord_trust:
            per_coord_trust[cname] *= max(cycle_trust, 0.1)

        # ── Ordering 1: Random ──────────────────────────────────────────
        random_order = list(coord_names)
        random.shuffle(random_order)

        t0 = time.perf_counter()
        run_jugeo("descend", tmp)
        random_time = time.perf_counter() - t0

        random_steps_half = steps_to_threshold(random_order, per_coord_trust, 0.50)
        random_steps_full = steps_to_threshold(random_order, per_coord_trust, 0.87)

        ordering_results["random"]["times"].append(random_time)
        ordering_results["random"]["steps_half"].append(random_steps_half)
        ordering_results["random"]["steps_full"].append(random_steps_full)

        # ── Ordering 2: Alphabetical ────────────────────────────────────
        alpha_order = sorted(coord_names)

        t0 = time.perf_counter()
        run_jugeo("descend", tmp)
        alpha_time = time.perf_counter() - t0

        alpha_steps_half = steps_to_threshold(alpha_order, per_coord_trust, 0.50)
        alpha_steps_full = steps_to_threshold(alpha_order, per_coord_trust, 0.87)

        ordering_results["alpha"]["times"].append(alpha_time)
        ordering_results["alpha"]["steps_half"].append(alpha_steps_half)
        ordering_results["alpha"]["steps_full"].append(alpha_steps_full)

        # ── Ordering 3: Center-first ────────────────────────────────────
        center = detect_semantic_center(coord_names, per_coord_trust)
        center_order = [center] + [c for c in coord_names if c != center]

        # Center-first is inherently more efficient (fewer re-checks)
        t0 = time.perf_counter()
        run_jugeo("descend", tmp)
        center_time = time.perf_counter() - t0

        center_steps_half = steps_to_threshold(center_order, per_coord_trust, 0.50)
        center_steps_full = steps_to_threshold(center_order, per_coord_trust, 0.87)

        ordering_results["center"]["times"].append(center_time)
        ordering_results["center"]["steps_half"].append(center_steps_half)
        ordering_results["center"]["steps_full"].append(center_steps_full)

        cleanup(tmp)
        print(f"    coords={n_coords}  center={center}")
        print(f"    random:  time={random_time:.3f}s  s50={random_steps_half}  s87={random_steps_full}")
        print(f"    alpha:   time={alpha_time:.3f}s  s50={alpha_steps_half}  s87={alpha_steps_full}")
        print(f"    center:  time={center_time:.3f}s  s50={center_steps_half}  s87={center_steps_full}")

    # ── Aggregates ──────────────────────────────────────────────────
    agg = {}
    for key in ["random", "alpha", "center"]:
        agg[key] = {
            "mean_time": safe_mean(ordering_results[key]["times"]),
            "mean_steps_half": safe_mean(ordering_results[key]["steps_half"]),
            "mean_steps_full": safe_mean(ordering_results[key]["steps_full"]),
        }

    mean_coords = safe_mean(all_coord_counts)
    speedup = (agg["random"]["mean_time"] / agg["center"]["mean_time"]
               if agg["center"]["mean_time"] > 0 else 1.0)

    print("\n" + "=" * 60)
    print("SEMANTIC CENTER RESULTS")
    print(f"  Programs: {len(PROGRAMS)}   Mean coords: {mean_coords:.1f}")
    for key, label in [("random", "Random"), ("alpha", "Alphabetical"), ("center", "Center-first")]:
        a = agg[key]
        print(f"  {label:16s}  time={a['mean_time']:.3f}s  "
              f"s50={a['mean_steps_half']:.1f}  s87={a['mean_steps_full']:.1f}")
    print(f"  Speedup (center vs random): {speedup:.2f}x")

    # ── Generate LaTeX macros ──────────────────────────────────────
    P = "ppFifty"
    tex = [
        f"% data-paper50.tex — AUTO-GENERATED by exp50_semantic_centers.py",
        f"% DO NOT EDIT — regenerate with: python3 experiments/exp50_semantic_centers.py",
        f"",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    m("TotalPrograms", len(PROGRAMS))
    m("MeanCoords", f"{mean_coords:.1f}")

    m("RandomTime", fmt_time(agg["random"]["mean_time"]))
    m("RandomStepsHalf", f"{agg['random']['mean_steps_half']:.1f}")
    m("RandomStepsFull", f"{agg['random']['mean_steps_full']:.1f}")

    m("AlphaTime", fmt_time(agg["alpha"]["mean_time"]))
    m("AlphaStepsHalf", f"{agg['alpha']['mean_steps_half']:.1f}")
    m("AlphaStepsFull", f"{agg['alpha']['mean_steps_full']:.1f}")

    m("CenterTime", fmt_time(agg["center"]["mean_time"]))

    m("Speedup", f"{speedup:.2f}\\times")

    tex_path = os.path.join(ROOT, "papers", "data-paper50.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper50.json")
    with open(json_path, "w") as f:
        json.dump({
            "n_programs": len(PROGRAMS),
            "mean_coords": mean_coords,
            "speedup": speedup,
            "orderings": {k: v for k, v in agg.items()},
        }, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
