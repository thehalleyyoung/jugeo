#!/usr/bin/env python3
"""
Experiment 54 — Foundational Synthesis: The Synthesis Pipeline
==============================================================

Studies the JuGeo synthesis pipeline by running evaluate, descend (with
multiple strategies), encode, and classify on 10 algorithmic programs.
Also exercises the Python API (SiteBuilder, specification_satisfaction,
replay_gluing).

Writes macros to papers/data-paper54.tex with prefix ppLIV.
Re-run: python3 experiments/exp54_foundational_synthesis.py
"""

import ast, json, os, statistics, subprocess, sys, tempfile, time

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


# ── CLI helper ────────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
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


def write_temp_py(source):
    """Write source to a temporary .py file and return its path."""
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


def safe_mean(values):
    return statistics.mean(values) if values else 0.0


# ── Test programs (10 algorithmic / data-structure programs) ──────────────────

PROGRAMS = {
    "tower_of_hanoi": '''\
def hanoi(n, source, target, auxiliary):
    """Solve Tower of Hanoi for n disks."""
    if n <= 0:
        return []
    moves = []
    moves.extend(hanoi(n - 1, source, auxiliary, target))
    moves.append((source, target))
    moves.extend(hanoi(n - 1, auxiliary, target, source))
    return moves

def hanoi_iterative(n):
    total = 2 ** n - 1
    pegs = {'A': [], 'B': [], 'C': []}
    for i in range(n, 0, -1):
        pegs['A'].append(i)
    moves = []
    if n % 2 == 0:
        order = ['A', 'B', 'C']
    else:
        order = ['A', 'C', 'B']
    for i in range(1, total + 1):
        if i % 3 == 1:
            _move(pegs, order[0], order[1], moves)
        elif i % 3 == 2:
            _move(pegs, order[0], order[2], moves)
        else:
            _move(pegs, order[1], order[2], moves)
    return moves

def _move(pegs, a, b, moves):
    if not pegs[a] and not pegs[b]:
        return
    if not pegs[a]:
        pegs[a].append(pegs[b].pop())
        moves.append((b, a))
    elif not pegs[b]:
        pegs[b].append(pegs[a].pop())
        moves.append((a, b))
    elif pegs[a][-1] < pegs[b][-1]:
        pegs[b].append(pegs[a].pop())
        moves.append((a, b))
    else:
        pegs[a].append(pegs[b].pop())
        moves.append((b, a))
''',

    "n_queens": '''\
def solve_n_queens(n):
    """Return all solutions for the N-Queens problem."""
    solutions = []
    board = [-1] * n

    def is_safe(row, col):
        for prev_row in range(row):
            prev_col = board[prev_row]
            if prev_col == col:
                return False
            if abs(prev_row - row) == abs(prev_col - col):
                return False
        return True

    def solve(row):
        if row == n:
            solutions.append(list(board))
            return
        for col in range(n):
            if is_safe(row, col):
                board[row] = col
                solve(row + 1)
                board[row] = -1

    solve(0)
    return solutions

def format_board(solution):
    n = len(solution)
    lines = []
    for row in range(n):
        line = ['.'] * n
        line[solution[row]] = 'Q'
        lines.append(' '.join(line))
    return '\\n'.join(lines)
''',

    "knapsack_dp": '''\
def knapsack_01(weights, values, capacity):
    """0/1 Knapsack using dynamic programming."""
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                dp[i][w] = max(dp[i][w],
                               dp[i - 1][w - weights[i - 1]] + values[i - 1])
    # Backtrack to find selected items
    selected = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i - 1][w]:
            selected.append(i - 1)
            w -= weights[i - 1]
    selected.reverse()
    return dp[n][capacity], selected

def fractional_knapsack(weights, values, capacity):
    items = sorted(range(len(weights)),
                   key=lambda i: values[i] / weights[i], reverse=True)
    total = 0.0
    for i in items:
        if capacity <= 0:
            break
        take = min(weights[i], capacity)
        total += take * (values[i] / weights[i])
        capacity -= take
    return total
''',

    "kmp_string_match": '''\
def compute_failure(pattern):
    """Compute KMP failure function."""
    m = len(pattern)
    failure = [0] * m
    j = 0
    for i in range(1, m):
        while j > 0 and pattern[i] != pattern[j]:
            j = failure[j - 1]
        if pattern[i] == pattern[j]:
            j += 1
        failure[i] = j
    return failure

def kmp_search(text, pattern):
    """Find all occurrences of pattern in text using KMP."""
    n, m = len(text), len(pattern)
    if m == 0:
        return []
    failure = compute_failure(pattern)
    matches = []
    j = 0
    for i in range(n):
        while j > 0 and text[i] != pattern[j]:
            j = failure[j - 1]
        if text[i] == pattern[j]:
            j += 1
        if j == m:
            matches.append(i - m + 1)
            j = failure[j - 1]
    return matches

def naive_search(text, pattern):
    matches = []
    for i in range(len(text) - len(pattern) + 1):
        if text[i:i + len(pattern)] == pattern:
            matches.append(i)
    return matches
''',

    "avl_tree": '''\
class AVLNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None
        self.height = 1

class AVLTree:
    def __init__(self):
        self.root = None

    def height(self, node):
        return node.height if node else 0

    def balance_factor(self, node):
        return self.height(node.left) - self.height(node.right) if node else 0

    def update_height(self, node):
        node.height = 1 + max(self.height(node.left), self.height(node.right))

    def rotate_right(self, y):
        x = y.left
        t = x.right
        x.right = y
        y.left = t
        self.update_height(y)
        self.update_height(x)
        return x

    def rotate_left(self, x):
        y = x.right
        t = y.left
        y.left = x
        x.right = t
        self.update_height(x)
        self.update_height(y)
        return y

    def insert(self, key):
        self.root = self._insert(self.root, key)

    def _insert(self, node, key):
        if not node:
            return AVLNode(key)
        if key < node.key:
            node.left = self._insert(node.left, key)
        elif key > node.key:
            node.right = self._insert(node.right, key)
        else:
            return node
        self.update_height(node)
        bf = self.balance_factor(node)
        if bf > 1 and key < node.left.key:
            return self.rotate_right(node)
        if bf < -1 and key > node.right.key:
            return self.rotate_left(node)
        if bf > 1 and key > node.left.key:
            node.left = self.rotate_left(node.left)
            return self.rotate_right(node)
        if bf < -1 and key < node.right.key:
            node.right = self.rotate_right(node.right)
            return self.rotate_left(node)
        return node

    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)
''',

    "dijkstra_shortest": '''\
import heapq

def dijkstra(graph, start):
    """Dijkstra shortest path from start to all nodes."""
    dist = {start: 0}
    prev = {start: None}
    pq = [(0, start)]
    visited = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        for v, w in graph.get(u, []):
            alt = d + w
            if v not in dist or alt < dist[v]:
                dist[v] = alt
                prev[v] = u
                heapq.heappush(pq, (alt, v))
    return dist, prev

def reconstruct_path(prev, target):
    path = []
    node = target
    while node is not None:
        path.append(node)
        node = prev.get(node)
    path.reverse()
    return path

def bellman_ford(edges, n, start):
    dist = [float('inf')] * n
    dist[start] = 0
    for _ in range(n - 1):
        for u, v, w in edges:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
    for u, v, w in edges:
        if dist[u] + w < dist[v]:
            raise ValueError("Negative cycle")
    return dist
''',

    "coin_change": '''\
def coin_change(coins, amount):
    """Minimum coins to make amount (DP)."""
    dp = [float('inf')] * (amount + 1)
    dp[0] = 0
    parent = [-1] * (amount + 1)
    for i in range(1, amount + 1):
        for c in coins:
            if c <= i and dp[i - c] + 1 < dp[i]:
                dp[i] = dp[i - c] + 1
                parent[i] = c
    if dp[amount] == float('inf'):
        return -1, []
    # Reconstruct solution
    result = []
    a = amount
    while a > 0:
        result.append(parent[a])
        a -= parent[a]
    return dp[amount], result

def coin_change_ways(coins, amount):
    """Count number of ways to make amount."""
    dp = [0] * (amount + 1)
    dp[0] = 1
    for c in coins:
        for i in range(c, amount + 1):
            dp[i] += dp[i - c]
    return dp[amount]

def coin_change_greedy(coins, amount):
    coins_sorted = sorted(coins, reverse=True)
    result = []
    for c in coins_sorted:
        while amount >= c:
            result.append(c)
            amount -= c
    return result if amount == 0 else None
''',

    "matrix_chain": '''\
def matrix_chain_order(dims):
    """Matrix chain multiplication — minimum scalar multiplications."""
    n = len(dims) - 1
    dp = [[0] * n for _ in range(n)]
    split = [[0] * n for _ in range(n)]
    for length in range(2, n + 1):
        for i in range(n - length + 1):
            j = i + length - 1
            dp[i][j] = float('inf')
            for k in range(i, j):
                cost = dp[i][k] + dp[k + 1][j] + dims[i] * dims[k + 1] * dims[j + 1]
                if cost < dp[i][j]:
                    dp[i][j] = cost
                    split[i][j] = k
    return dp[0][n - 1], split

def print_optimal_parens(split, i, j):
    if i == j:
        return f"A{i}"
    k = split[i][j]
    left = print_optimal_parens(split, i, k)
    right = print_optimal_parens(split, k + 1, j)
    return f"({left} x {right})"

def matrix_multiply(A, B):
    rows_a, cols_a = len(A), len(A[0])
    rows_b, cols_b = len(B), len(B[0])
    if cols_a != rows_b:
        raise ValueError("Incompatible dimensions")
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += A[i][k] * B[k][j]
    return result
''',

    "interval_scheduling": '''\
def interval_scheduling(intervals):
    """Greedy interval scheduling maximization."""
    sorted_intervals = sorted(intervals, key=lambda x: x[1])
    selected = []
    last_end = float('-inf')
    for start, end in sorted_intervals:
        if start >= last_end:
            selected.append((start, end))
            last_end = end
    return selected

def weighted_interval_scheduling(intervals):
    """Weighted interval scheduling via DP."""
    intervals = sorted(intervals, key=lambda x: x[1])
    n = len(intervals)
    dp = [0] * (n + 1)
    def latest_compatible(i):
        lo, hi = 0, i - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if intervals[mid][1] <= intervals[i][0]:
                if mid + 1 < i and intervals[mid + 1][1] <= intervals[i][0]:
                    lo = mid + 1
                else:
                    return mid + 1
            else:
                hi = mid - 1
        return 0
    for i in range(1, n + 1):
        incl = intervals[i - 1][2] + dp[latest_compatible(i - 1)]
        dp[i] = max(dp[i - 1], incl)
    return dp[n]

def count_overlaps(intervals):
    events = []
    for s, e in intervals:
        events.append((s, 1))
        events.append((e, -1))
    events.sort()
    max_overlap = current = 0
    for _, delta in events:
        current += delta
        max_overlap = max(max_overlap, current)
    return max_overlap
''',

    "topological_sort": '''\
from collections import defaultdict, deque

def topological_sort_kahn(graph, n):
    """Kahn's algorithm for topological sort."""
    in_degree = [0] * n
    for u in range(n):
        for v in graph.get(u, []):
            in_degree[v] += 1
    queue = deque([u for u in range(n) if in_degree[u] == 0])
    order = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v in graph.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
    if len(order) != n:
        raise ValueError("Graph has a cycle")
    return order

def topological_sort_dfs(graph, n):
    """DFS-based topological sort."""
    visited = [False] * n
    stack = []
    def dfs(u):
        visited[u] = True
        for v in graph.get(u, []):
            if not visited[v]:
                dfs(v)
        stack.append(u)
    for u in range(n):
        if not visited[u]:
            dfs(u)
    stack.reverse()
    return stack

def all_topological_sorts(graph, n):
    in_degree = [0] * n
    for u in range(n):
        for v in graph.get(u, []):
            in_degree[v] += 1
    results = []
    visited = [False] * n
    current = []
    def backtrack():
        if len(current) == n:
            results.append(list(current))
            return
        for u in range(n):
            if not visited[u] and in_degree[u] == 0:
                visited[u] = True
                current.append(u)
                for v in graph.get(u, []):
                    in_degree[v] -= 1
                backtrack()
                current.pop()
                visited[u] = False
                for v in graph.get(u, []):
                    in_degree[v] += 1
    backtrack()
    return results
''',
}


# ── Descent strategies to test ────────────────────────────────────────────────

DESCENT_STRATEGIES = ["eager", "exhaustive", "iterative"]


# ── Python API helper ─────────────────────────────────────────────────────────

def run_python_api(source):
    """Exercise SiteBuilder Python API and return metrics dict."""
    result = {
        "spec_sat": "unavailable",
        "replay_gluing": "unavailable",
        "api_coords": 0,
        "api_morphisms": 0,
    }
    try:
        from jugeo.geometry.site import SiteBuilder
        site = SiteBuilder(source).build()
        result["api_coords"] = len(site.coordinates()) if hasattr(site, 'coordinates') else 0
        result["api_morphisms"] = len(site.morphisms()) if hasattr(site, 'morphisms') else 0

        ss = site.specification_satisfaction()
        result["spec_sat"] = ss.get("satisfaction", "unknown") if isinstance(ss, dict) else str(ss)

        rg = site.replay_gluing()
        result["replay_gluing"] = rg.get("replay", "unknown") if isinstance(rg, dict) else str(rg)
    except Exception as e:
        result["api_error"] = str(e)
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("Experiment 54 — Foundational Synthesis: The Synthesis Pipeline")
    print("=" * 70)

    tmpfiles = []
    results = []

    for name, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        tree = ast.parse(source)
        n_lines = len([l for l in source.splitlines() if l.strip()])

        print(f"\n  [{name}] ({n_lines} lines)")

        # ── 1. jugeo evaluate ────────────────────────────────────────────
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", path)
        eval_wall = time.perf_counter() - t0
        eval_data = eval_objs[0] if eval_objs else {}

        eval_verdict = eval_data.get("verdict", "unknown")
        eval_props_total = eval_data.get("propositions_total", 0)
        eval_props_ok = eval_data.get("propositions_ok", 0)

        print(f"    evaluate: verdict={eval_verdict}  props={eval_props_ok}/{eval_props_total}  "
              f"time={eval_wall:.2f}s")

        # ── 2. jugeo descend (multiple strategies) ───────────────────────
        descent_results = {}
        for strategy in DESCENT_STRATEGIES:
            t1 = time.perf_counter()
            desc_objs = run_jugeo("descend", "--strategy", strategy, path)
            desc_wall = time.perf_counter() - t1
            desc_data = desc_objs[0] if desc_objs else {}

            desc_verdict = desc_data.get("verdict", "unknown")
            desc_obstructions = desc_data.get("obstructions", [])
            n_obstructions = len(desc_obstructions) if isinstance(desc_obstructions, list) else int(desc_obstructions or 0)

            descent_results[strategy] = {
                "verdict": desc_verdict,
                "obstructions": n_obstructions,
                "wall_s": round(desc_wall, 4),
                "effective_descent": desc_data.get("effective_descent", False),
            }
            print(f"    descend[{strategy}]: verdict={desc_verdict}  "
                  f"obstructions={n_obstructions}  time={desc_wall:.2f}s")

        # ── 3. jugeo encode ──────────────────────────────────────────────
        t2 = time.perf_counter()
        encode_objs = run_jugeo("encode", path)
        encode_wall = time.perf_counter() - t2
        encode_data = encode_objs[0] if encode_objs else {}
        encode_coords = encode_data.get("coordinates", 0)
        encode_morphisms = encode_data.get("morphisms", 0)

        print(f"    encode:   coords={encode_coords}  morphisms={encode_morphisms}  "
              f"time={encode_wall:.2f}s")

        # ── 4. jugeo classify ────────────────────────────────────────────
        t3 = time.perf_counter()
        class_objs = run_jugeo("classify", "--file", path)
        class_wall = time.perf_counter() - t3
        class_data = class_objs[0] if class_objs else {}

        print(f"    classify: time={class_wall:.2f}s")

        # ── 5. Python API ────────────────────────────────────────────────
        api_result = run_python_api(source)

        # ── Collect row ──────────────────────────────────────────────────
        row = {
            "program": name,
            "lines": n_lines,
            "eval_verdict": eval_verdict,
            "eval_props_total": eval_props_total,
            "eval_props_ok": eval_props_ok,
            "eval_wall_s": round(eval_wall, 4),
            "descent": descent_results,
            "encode_coords": encode_coords,
            "encode_morphisms": encode_morphisms,
            "encode_wall_s": round(encode_wall, 4),
            "classify_wall_s": round(class_wall, 4),
            "classify_data": class_data,
            "api": api_result,
        }
        results.append(row)

    # ── Aggregate statistics ──────────────────────────────────────────────────

    n_total = len(results)

    # Evaluation
    verified = sum(1 for r in results if r["eval_verdict"] == "verified")
    accuracy = (verified / max(n_total, 1)) * 100
    total_props = sum(r["eval_props_total"] for r in results)
    total_props_ok = sum(r["eval_props_ok"] for r in results)
    prop_ratio = (total_props_ok / max(total_props, 1)) * 100
    eval_times = [r["eval_wall_s"] for r in results]

    # Descent — per strategy success rates
    strategy_success = {}
    all_descent_times = []
    total_obstructions = 0
    for strategy in DESCENT_STRATEGIES:
        successes = sum(1 for r in results
                        if r["descent"].get(strategy, {}).get("verdict") == "verified")
        strategy_success[strategy] = (successes / max(n_total, 1)) * 100
        for r in results:
            sd = r["descent"].get(strategy, {})
            all_descent_times.append(sd.get("wall_s", 0))
            total_obstructions += sd.get("obstructions", 0)

    # Encoding
    coord_vals = [r["encode_coords"] for r in results]
    morph_vals = [r["encode_morphisms"] for r in results]

    # Classification
    classify_times = [r["classify_wall_s"] for r in results]

    # ── Print summary ─────────────────────────────────────────────────────────

    print()
    print("-" * 70)
    print(f"  Total programs:        {n_total}")
    print(f"  Verified:              {verified}/{n_total} ({accuracy:.1f}%)")
    print(f"  Propositions:          {total_props_ok}/{total_props} ({prop_ratio:.1f}%)")
    print(f"  Total obstructions:    {total_obstructions}")
    print(f"  Mean eval time:        {safe_mean(eval_times):.4f}s")
    print(f"  Mean descent time:     {safe_mean(all_descent_times):.4f}s")
    print(f"  Mean classify time:    {safe_mean(classify_times):.4f}s")
    print(f"  Mean coords:           {safe_mean(coord_vals):.1f}")
    print(f"  Mean morphisms:        {safe_mean(morph_vals):.1f}")
    for strategy in DESCENT_STRATEGIES:
        print(f"  {strategy} success rate:  {strategy_success[strategy]:.1f}%")
    print("-" * 70)

    # ── Write LaTeX macros ────────────────────────────────────────────────────

    tex_path = os.path.join(REPO_ROOT, "papers", "data-paper54.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)

    with open(tex_path, "w") as fh:
        fh.write("% data-paper54.tex — AUTO-GENERATED by exp54_foundational_synthesis.py\n")
        fh.write("% DO NOT EDIT — regenerate with: python3 experiments/exp54_foundational_synthesis.py\n\n")

        fh.write("% ── Overall statistics ────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIVtotalPrograms", n_total)
        write_macro(fh, "ppLIVoverallAccuracy", "{:.1f}\\%".format(accuracy))
        write_macro(fh, "ppLIVverifiedCount", verified)

        fh.write("\n% ── Proposition statistics ────────────────────────────────────────────\n")
        write_macro(fh, "ppLIVtotalProps", total_props)
        write_macro(fh, "ppLIVtotalPropsOk", total_props_ok)
        write_macro(fh, "ppLIVpropRatio", "{:.1f}\\%".format(prop_ratio))

        fh.write("\n% ── Descent strategies ────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIVmeanDescentTime", "{:.3f}\\,s".format(safe_mean(all_descent_times)))
        write_macro(fh, "ppLIVeagerRate", "{:.1f}\\%".format(strategy_success.get("eager", 0)))
        write_macro(fh, "ppLIVexhaustiveRate", "{:.1f}\\%".format(strategy_success.get("exhaustive", 0)))
        write_macro(fh, "ppLIViterativeRate", "{:.1f}\\%".format(strategy_success.get("iterative", 0)))
        write_macro(fh, "ppLIVtotalObstructions", total_obstructions)

        fh.write("\n% ── Structural metrics ────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIVmeanCoords", "{:.1f}".format(safe_mean(coord_vals)))
        write_macro(fh, "ppLIVmeanMorphisms", "{:.1f}".format(safe_mean(morph_vals)))

        fh.write("\n% ── Timing statistics ─────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIVmeanClassifyTime", "{:.3f}\\,s".format(safe_mean(classify_times)))
        write_macro(fh, "ppLIVmeanEvalTime", "{:.3f}\\,s".format(safe_mean(eval_times)))

    print(f"\n  Macros written to: {tex_path}")

    # ── Save JSON results ─────────────────────────────────────────────────────

    json_path = os.path.join(os.path.dirname(__file__), "results_paper54.json")
    with open(json_path, "w") as f:
        json.dump({
            "experiment": "foundational_synthesis",
            "paper": 54,
            "note": "CLI subprocess calls (evaluate, descend, encode, classify) + Python API.",
            "n_programs": n_total,
            "results": results,
            "summary": {
                "verified": verified,
                "accuracy_pct": round(accuracy, 1),
                "total_props": total_props,
                "total_props_ok": total_props_ok,
                "prop_ratio_pct": round(prop_ratio, 1),
                "total_obstructions": total_obstructions,
                "mean_eval_s": round(safe_mean(eval_times), 4),
                "mean_descent_s": round(safe_mean(all_descent_times), 4),
                "mean_classify_s": round(safe_mean(classify_times), 4),
                "mean_coords": round(safe_mean(coord_vals), 1),
                "mean_morphisms": round(safe_mean(morph_vals), 1),
                "strategy_success": {k: round(v, 1) for k, v in strategy_success.items()},
            },
        }, f, indent=2, default=str)

    print(f"  Results saved to:  {json_path}")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass

    print("\nDone.")


if __name__ == "__main__":
    main()
