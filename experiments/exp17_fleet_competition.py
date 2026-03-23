#!/usr/bin/env python3
"""Paper 17 Experiment — Fleet Competition: Adversarial Evidence Selection.

Runs ``jugeo descend`` with all three strategies (eager, exhaustive, iterative)
on each program.  Compares trust levels achieved as "fleet competition" results.
Runs ``jugeo evaluate`` for coverage quality.

Every number is reproducible: run `python3 experiments/exp17_fleet_competition.py`.
Writes macros to papers/data-paper17.tex with prefix ppSeventeen.
"""
import subprocess, json, os, tempfile, time, random, statistics

random.seed(42)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# ── helpers ──────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')]
    lines = [l for l in lines if not l.startswith("JuGeo v")]
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
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


# ── trust level numeric mapping ──────────────────────────────────────────

TRUST_NUMERIC = {
    "MECHANICALLY_VERIFIED": 7,
    "mechanically_verified": 7,
    "SOLVER_DISCHARGED": 6,
    "solver_discharged": 6,
    "RUNTIME_WITNESSED": 5,
    "runtime_witnessed": 5,
    "HUMAN_ATTESTED": 4,
    "human_attested": 4,
    "ORACLE_PROPOSED": 3,
    "oracle_proposed": 3,
    "COPILOT_SUGGESTED": 2,
    "copilot_suggested": 2,
    "LOW": 1,
    "unverified": 1,
    "CONTRADICTED": 0,
    "contradicted": 0,
}

STRATEGIES = ["eager", "exhaustive", "iterative"]

# ── test programs ────────────────────────────────────────────────────────

PROGRAMS = {
    "binary_search": '''
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

def search_all(arr, target):
    idx = binary_search(arr, target)
    if idx == -1:
        return []
    results = [idx]
    lo = idx - 1
    while lo >= 0 and arr[lo] == target:
        results.append(lo)
        lo -= 1
    hi = idx + 1
    while hi < len(arr) and arr[hi] == target:
        results.append(hi)
        hi += 1
    return sorted(results)
''',

    "merge_sort": '''
def merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result

def merge_sort(arr):
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)
''',

    "stack_class": '''
class Stack:
    def __init__(self):
        self._items = []
    def push(self, item):
        self._items.append(item)
        return self
    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()
    def peek(self):
        if not self._items:
            raise IndexError("peek at empty stack")
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
''',

    "linked_list": '''
class Node:
    def __init__(self, val, nxt=None):
        self.val = val
        self.next = nxt

class LinkedList:
    def __init__(self):
        self.head = None
        self.length = 0
    def prepend(self, val):
        self.head = Node(val, self.head)
        self.length += 1
    def append(self, val):
        node = Node(val)
        if not self.head:
            self.head = node
        else:
            cur = self.head
            while cur.next:
                cur = cur.next
            cur.next = node
        self.length += 1
    def remove(self, val):
        if not self.head:
            return False
        if self.head.val == val:
            self.head = self.head.next
            self.length -= 1
            return True
        cur = self.head
        while cur.next:
            if cur.next.val == val:
                cur.next = cur.next.next
                self.length -= 1
                return True
            cur = cur.next
        return False
    def to_list(self):
        result, cur = [], self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
''',

    "heap_sort": '''
def heapify(arr, n, i):
    largest = i
    left = 2 * i + 1
    right = 2 * i + 2
    if left < n and arr[left] > arr[largest]:
        largest = left
    if right < n and arr[right] > arr[largest]:
        largest = right
    if largest != i:
        arr[i], arr[largest] = arr[largest], arr[i]
        heapify(arr, n, largest)

def heap_sort(arr):
    n = len(arr)
    result = list(arr)
    for i in range(n // 2 - 1, -1, -1):
        heapify(result, n, i)
    for i in range(n - 1, 0, -1):
        result[0], result[i] = result[i], result[0]
        heapify(result, i, 0)
    return result
''',

    "decorator_memoize": '''
def memoize(func):
    cache = {}
    def wrapper(*args):
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]
    return wrapper

@memoize
def fibonacci(n):
    if n < 2:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

def fib_sequence(count):
    return [fibonacci(i) for i in range(count)]
''',

    "async_producer_consumer": '''
import asyncio

async def producer(queue, items):
    for item in items:
        await queue.put(item)
    await queue.put(None)

async def consumer(queue):
    results = []
    while True:
        item = await queue.get()
        if item is None:
            break
        results.append(item * 2)
    return results

async def pipeline(data):
    queue = asyncio.Queue()
    prod = asyncio.create_task(producer(queue, data))
    cons = asyncio.create_task(consumer(queue))
    await prod
    return await cons
''',

    "graph_bfs": '''
from collections import deque

def bfs(graph, start):
    visited = set()
    queue = deque([start])
    visited.add(start)
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return order

def shortest_path(graph, start, end):
    if start == end:
        return [start]
    visited = {start}
    queue = deque([(start, [start])])
    while queue:
        node, path = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor == end:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return []
''',

    "hash_map": '''
class HashMap:
    def __init__(self, capacity=16):
        self.capacity = capacity
        self.size = 0
        self.buckets = [[] for _ in range(capacity)]

    def _hash(self, key):
        return hash(key) % self.capacity

    def put(self, key, value):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.buckets[idx]):
            if k == key:
                self.buckets[idx][i] = (key, value)
                return
        self.buckets[idx].append((key, value))
        self.size += 1

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self.buckets[idx]:
            if k == key:
                return v
        return default

    def delete(self, key):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.buckets[idx]):
            if k == key:
                del self.buckets[idx][i]
                self.size -= 1
                return True
        return False
''',

    "tree_traversal": '''
class TreeNode:
    def __init__(self, val, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.val] + inorder(root.right)

def preorder(root):
    if root is None:
        return []
    return [root.val] + preorder(root.left) + preorder(root.right)

def tree_height(root):
    if root is None:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))

def is_bst(root, lo=float('-inf'), hi=float('inf')):
    if root is None:
        return True
    if root.val <= lo or root.val >= hi:
        return False
    return is_bst(root.left, lo, root.val) and is_bst(root.right, root.val, hi)
''',
}


# ── main ─────────────────────────────────────────────────────────────────

def main():
    tmpfiles = []
    n_programs = len(PROGRAMS)
    n_tasks = n_programs * len(STRATEGIES)

    print(f"Paper 17 — Fleet Competition Experiment")
    print(f"Programs: {n_programs}, Strategies: {len(STRATEGIES)}, Tasks: {n_tasks}")
    print("=" * 76)

    # ── 1. Run descend with each strategy ────────────────────────────────
    all_results = []
    for prog_name, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)
        prog_row = {"name": prog_name, "strategies": {}}

        for strategy in STRATEGIES:
            print(f"  descend {prog_name} --strategy {strategy} ...", end=" ", flush=True)
            t0 = time.perf_counter()
            try:
                objs = run_jugeo("descend", path, "--strategy", strategy)
                wall_s = time.perf_counter() - t0
                desc = objs[0] if objs else {}
            except Exception as e:
                wall_s = time.perf_counter() - t0
                desc = {}
                print(f"ERROR: {e}")
                continue

            trust_raw = desc.get("trust", desc.get("aggregate_trust", "unverified"))
            if isinstance(trust_raw, dict):
                trust_raw = trust_raw.get("aggregate_trust", "unverified")
            trust_num = TRUST_NUMERIC.get(str(trust_raw).upper(),
                         TRUST_NUMERIC.get(str(trust_raw), 1))

            sections = desc.get("local_sections", 0)
            overlap = desc.get("overlap_conditions_checked", 0)
            obstructions = len(desc.get("obstructions", []))
            verdict = desc.get("verdict", "unknown")

            props_total = 0
            props_ok = 0
            for sd in desc.get("sections_detail", []):
                props_total += sd.get("propositions", 0)
                props_ok += sd.get("ok", 0)

            prog_row["strategies"][strategy] = {
                "trust_raw": str(trust_raw),
                "trust_num": trust_num,
                "verdict": verdict,
                "sections": sections,
                "overlap": overlap,
                "obstructions": obstructions,
                "props_total": props_total,
                "props_ok": props_ok,
                "wall_s": round(wall_s, 4),
            }
            print(f"trust={trust_num} verdict={verdict} t={wall_s:.3f}s")

        all_results.append(prog_row)

    # ── 2. Run evaluate for coverage quality ─────────────────────────────
    print("\n── Evaluate coverage ──")
    eval_results = []
    for prog_name, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)
        print(f"  evaluate {prog_name} ...", end=" ", flush=True)
        try:
            objs = run_jugeo("evaluate", path)
            ev = objs[0] if objs else {}
        except Exception:
            ev = {}
        coverage = ev.get("coverage", 0.0)
        cq = ev.get("cover_quality", {})
        total_score = cq.get("total_score", 0.0)
        eval_results.append({
            "name": prog_name,
            "coverage": coverage,
            "total_score": total_score,
        })
        print(f"coverage={coverage:.2f} score={total_score:.2f}")

    # ── 3. Compute fleet competition metrics ─────────────────────────────
    print("\n── Fleet Competition Analysis ──")

    greedy_trusts = []      # eager alone
    fleet_two_trusts = []   # best of eager + exhaustive
    fleet_three_trusts = [] # best of all three
    all_latencies = []
    total_props = 0
    total_props_ok = 0

    # Per-strategy wins
    sustain_count = 0   # eager is already best
    overturn_count = 0  # another strategy beats eager
    escalate_count = 0  # need all three to find best

    converge_r1 = 0  # converged (verified) after round 1 (eager)
    converge_r2 = 0  # converged after round 2 (+ exhaustive)
    converge_r3 = 0  # converged after round 3 (+ iterative)

    for row in all_results:
        strats = row["strategies"]
        if not strats:
            continue

        eager_t = strats.get("eager", {}).get("trust_num", 0)
        exhaust_t = strats.get("exhaustive", {}).get("trust_num", 0)
        iterative_t = strats.get("iterative", {}).get("trust_num", 0)

        greedy_trusts.append(eager_t)
        fleet_two_trusts.append(max(eager_t, exhaust_t))
        fleet_three_trusts.append(max(eager_t, exhaust_t, iterative_t))

        best_three = max(eager_t, exhaust_t, iterative_t)
        if eager_t == best_three:
            sustain_count += 1
        elif max(eager_t, exhaust_t) == best_three and exhaust_t > eager_t:
            overturn_count += 1
        else:
            escalate_count += 1

        # convergence rounds
        eager_v = strats.get("eager", {}).get("verdict", "")
        exhaust_v = strats.get("exhaustive", {}).get("verdict", "")
        iterative_v = strats.get("iterative", {}).get("verdict", "")
        if eager_v == "verified":
            converge_r1 += 1
        if eager_v == "verified" or exhaust_v == "verified":
            converge_r2 += 1
        if eager_v == "verified" or exhaust_v == "verified" or iterative_v == "verified":
            converge_r3 += 1

        for s in strats.values():
            all_latencies.append(s.get("wall_s", 0))
            total_props += s.get("props_total", 0)
            total_props_ok += s.get("props_ok", 0)

    n_valid = len(greedy_trusts) or 1
    greedy_mean = sum(greedy_trusts) / n_valid
    fleet_two_mean = sum(fleet_two_trusts) / n_valid
    fleet_three_mean = sum(fleet_three_trusts) / n_valid

    greedy_completion = sum(1 for t in greedy_trusts if t >= 6) / n_valid * 100
    fleet_completion = sum(1 for t in fleet_three_trusts if t >= 6) / n_valid * 100

    mean_latency = statistics.mean(all_latencies) if all_latencies else 0
    median_latency = statistics.median(all_latencies) if all_latencies else 0

    total_valid = sustain_count + overturn_count + escalate_count or 1
    sustain_rate = sustain_count / total_valid * 100
    overturn_rate = overturn_count / total_valid * 100
    escalate_rate = escalate_count / total_valid * 100

    challenges_per_task = len(STRATEGIES)  # each program runs all strategies

    r1_pct = converge_r1 / n_valid * 100
    r2_pct = converge_r2 / n_valid * 100
    r3_pct = converge_r3 / n_valid * 100

    # ── Print summary ────────────────────────────────────────────────────
    print(f"\nFLEET COMPETITION SUMMARY:")
    print(f"  Programs: {n_programs}")
    print(f"  Total tasks: {n_tasks}")
    print(f"  Greedy mean trust: {greedy_mean:.2f}")
    print(f"  Fleet-2 mean trust: {fleet_two_mean:.2f}")
    print(f"  Fleet-3 mean trust: {fleet_three_mean:.2f}")
    print(f"  Greedy completion rate: {greedy_completion:.0f}%")
    print(f"  Fleet completion rate: {fleet_completion:.0f}%")
    print(f"  Mean latency: {mean_latency:.4f}s")
    print(f"  Median latency: {median_latency:.4f}s")
    print(f"  Sustain rate: {sustain_rate:.0f}%")
    print(f"  Overturn rate: {overturn_rate:.0f}%")
    print(f"  Escalate rate: {escalate_rate:.0f}%")
    print(f"  Challenges per task: {challenges_per_task}")
    print(f"  Total props: {total_props}")
    print(f"  Total props ok: {total_props_ok}")
    print(f"  Round 1 converge: {r1_pct:.0f}%")
    print(f"  Round 2 converge: {r2_pct:.0f}%")
    print(f"  Round 3 converge: {r3_pct:.0f}%")

    # ── Per-program table ────────────────────────────────────────────────
    print(f"\n{'Program':<24} {'eager':>6} {'exhaust':>8} {'iter':>6} {'fleet':>6}")
    print("-" * 56)
    for row in all_results:
        e = row["strategies"].get("eager", {}).get("trust_num", 0)
        x = row["strategies"].get("exhaustive", {}).get("trust_num", 0)
        it = row["strategies"].get("iterative", {}).get("trust_num", 0)
        fl = max(e, x, it)
        print(f"  {row['name']:<22} {e:>6} {x:>8} {it:>6} {fl:>6}")

    # ── Save JSON ────────────────────────────────────────────────────────
    output = {
        "experiment": "fleet_competition",
        "paper": 17,
        "note": "All numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_programs": n_programs,
        "n_tasks": n_tasks,
        "strategies": STRATEGIES,
        "results": all_results,
        "evaluate_results": eval_results,
        "summary": {
            "greedy_mean_trust": round(greedy_mean, 2),
            "fleet_two_mean_trust": round(fleet_two_mean, 2),
            "fleet_three_mean_trust": round(fleet_three_mean, 2),
            "greedy_completion_rate": round(greedy_completion, 1),
            "fleet_completion_rate": round(fleet_completion, 1),
            "mean_latency": round(mean_latency, 4),
            "median_latency": round(median_latency, 4),
            "sustain_rate": round(sustain_rate, 1),
            "overturn_rate": round(overturn_rate, 1),
            "escalate_rate": round(escalate_rate, 1),
            "total_props": total_props,
            "total_props_ok": total_props_ok,
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper17.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults → {json_path}")

    # ── Write LaTeX macros ───────────────────────────────────────────────
    tex_path = os.path.join(ROOT, "papers", "data-paper17.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w") as f:
        f.write("% data-paper17.tex — AUTO-GENERATED by exp17_fleet_competition.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp17_fleet_competition.py\n\n")

        write_macro(f, "ppSeventeenTotalPrograms", n_programs)
        write_macro(f, "ppSeventeenTotalTasks", n_tasks)
        f.write("\n")

        write_macro(f, "ppSeventeenGreedyMeanTrust", f"{greedy_mean:.2f}")
        write_macro(f, "ppSeventeenFleetTwoMeanTrust", f"{fleet_two_mean:.2f}")
        write_macro(f, "ppSeventeenFleetThreeMeanTrust", f"{fleet_three_mean:.2f}")
        f.write("\n")

        write_macro(f, "ppSeventeenGreedyCompletionRate", f"{greedy_completion:.0f}\\%")
        write_macro(f, "ppSeventeenFleetCompletionRate", f"{fleet_completion:.0f}\\%")
        f.write("\n")

        write_macro(f, "ppSeventeenMeanLatency", f"{mean_latency:.4f}\\,s")
        write_macro(f, "ppSeventeenMedianLatency", f"{median_latency:.4f}\\,s")
        f.write("\n")

        write_macro(f, "ppSeventeenSustainRate", f"{sustain_rate:.0f}\\%")
        write_macro(f, "ppSeventeenOverturnRate", f"{overturn_rate:.0f}\\%")
        write_macro(f, "ppSeventeenEscalateRate", f"{escalate_rate:.0f}\\%")
        f.write("\n")

        write_macro(f, "ppSeventeenChallengesPerTask", challenges_per_task)
        f.write("\n")

        write_macro(f, "ppSeventeenTotalProps", f"{total_props:,}".replace(",", "{,}"))
        write_macro(f, "ppSeventeenTotalPropsOk", f"{total_props_ok:,}".replace(",", "{,}"))
        f.write("\n")

        write_macro(f, "ppSeventeenRoundOneConverge", f"{r1_pct:.0f}\\%")
        write_macro(f, "ppSeventeenRoundTwoConverge", f"{r2_pct:.0f}\\%")
        write_macro(f, "ppSeventeenRoundThreeConverge", f"{r3_pct:.0f}\\%")

    print(f"LaTeX  → {tex_path}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
