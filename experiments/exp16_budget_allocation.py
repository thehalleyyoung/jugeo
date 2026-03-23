#!/usr/bin/env python3
"""
Experiment 16 -- Budget Allocation: Verification Resource Allocation
====================================================================

Runs descend with three strategies (eager, exhaustive, iterative) on each
program, comparing resource usage, verification success, and timing.
Strategies act as competing budget-allocation policies.

Writes macros to papers/data-paper16.tex with prefix \\ppSixteen.
Re-run: python3 experiments/exp16_budget_allocation.py
"""

import subprocess, json, os, sys, tempfile, time, statistics

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

STRATEGIES = ["eager", "exhaustive", "iterative"]

# -- CLI helper ----------------------------------------------------------------

def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
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


# -- Test programs -------------------------------------------------------------

PROGRAMS = {
    "bubble_sort": (
        "def bubble_sort(arr):\n"
        "    n = len(arr)\n"
        "    for i in range(n):\n"
        "        for j in range(0, n - i - 1):\n"
        "            if arr[j] > arr[j + 1]:\n"
        "                arr[j], arr[j + 1] = arr[j + 1], arr[j]\n"
        "    return arr\n"
    ),
    "binary_search": (
        "def binary_search(arr, target):\n"
        "    lo, hi = 0, len(arr) - 1\n"
        "    while lo <= hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if arr[mid] == target:\n"
        "            return mid\n"
        "        elif arr[mid] < target:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return -1\n"
    ),
    "stack": (
        "class Stack:\n"
        "    def __init__(self):\n"
        "        self._items = []\n"
        "    def push(self, item):\n"
        "        self._items.append(item)\n"
        "    def pop(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('pop from empty stack')\n"
        "        return self._items.pop()\n"
        "    def peek(self):\n"
        "        return self._items[-1] if self._items else None\n"
        "    def is_empty(self):\n"
        "        return len(self._items) == 0\n"
        "    def size(self):\n"
        "        return len(self._items)\n"
    ),
    "linked_list": (
        "class Node:\n"
        "    def __init__(self, val, nxt=None):\n"
        "        self.val = val\n"
        "        self.next = nxt\n"
        "\n"
        "class LinkedList:\n"
        "    def __init__(self):\n"
        "        self.head = None\n"
        "    def prepend(self, val):\n"
        "        self.head = Node(val, self.head)\n"
        "    def append(self, val):\n"
        "        if not self.head:\n"
        "            self.head = Node(val)\n"
        "            return\n"
        "        cur = self.head\n"
        "        while cur.next:\n"
        "            cur = cur.next\n"
        "        cur.next = Node(val)\n"
        "    def find(self, val):\n"
        "        cur = self.head\n"
        "        while cur:\n"
        "            if cur.val == val:\n"
        "                return True\n"
        "            cur = cur.next\n"
        "        return False\n"
    ),
    "bank_account": (
        "class BankAccount:\n"
        "    def __init__(self, owner, balance=0):\n"
        "        self.owner = owner\n"
        "        self.balance = balance\n"
        "    def deposit(self, amount):\n"
        "        if amount <= 0:\n"
        "            raise ValueError('positive')\n"
        "        self.balance += amount\n"
        "        return self.balance\n"
        "    def withdraw(self, amount):\n"
        "        if amount <= 0:\n"
        "            raise ValueError('positive')\n"
        "        if amount > self.balance:\n"
        "            raise ValueError('funds')\n"
        "        self.balance -= amount\n"
        "        return self.balance\n"
        "    def get_balance(self):\n"
        "        return self.balance\n"
    ),
    "merge_sort": (
        "def merge_sort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    mid = len(arr) // 2\n"
        "    left = merge_sort(arr[:mid])\n"
        "    right = merge_sort(arr[mid:])\n"
        "    result, i, j = [], 0, 0\n"
        "    while i < len(left) and j < len(right):\n"
        "        if left[i] <= right[j]:\n"
        "            result.append(left[i]); i += 1\n"
        "        else:\n"
        "            result.append(right[j]); j += 1\n"
        "    result.extend(left[i:])\n"
        "    result.extend(right[j:])\n"
        "    return result\n"
    ),
    "priority_queue": (
        "class PriorityQueue:\n"
        "    def __init__(self):\n"
        "        self._heap = []\n"
        "    def push(self, priority, item):\n"
        "        self._heap.append((priority, item))\n"
        "        self._sift_up(len(self._heap) - 1)\n"
        "    def pop(self):\n"
        "        if not self._heap:\n"
        "            raise IndexError('empty')\n"
        "        self._swap(0, len(self._heap) - 1)\n"
        "        item = self._heap.pop()\n"
        "        if self._heap:\n"
        "            self._sift_down(0)\n"
        "        return item\n"
        "    def _sift_up(self, i):\n"
        "        while i > 0:\n"
        "            p = (i - 1) // 2\n"
        "            if self._heap[i][0] < self._heap[p][0]:\n"
        "                self._swap(i, p); i = p\n"
        "            else: break\n"
        "    def _sift_down(self, i):\n"
        "        n = len(self._heap)\n"
        "        while 2 * i + 1 < n:\n"
        "            c = 2 * i + 1\n"
        "            if c + 1 < n and self._heap[c+1][0] < self._heap[c][0]:\n"
        "                c += 1\n"
        "            if self._heap[c][0] < self._heap[i][0]:\n"
        "                self._swap(i, c); i = c\n"
        "            else: break\n"
        "    def _swap(self, i, j):\n"
        "        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]\n"
    ),
    "quick_sort": (
        "def quick_sort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    pivot = arr[len(arr) // 2]\n"
        "    left = [x for x in arr if x < pivot]\n"
        "    mid = [x for x in arr if x == pivot]\n"
        "    right = [x for x in arr if x > pivot]\n"
        "    return quick_sort(left) + mid + quick_sort(right)\n"
    ),
    "decorator_memoize": (
        "def memoize(func):\n"
        "    cache = {}\n"
        "    def wrapper(*args):\n"
        "        if args not in cache:\n"
        "            cache[args] = func(*args)\n"
        "        return cache[args]\n"
        "    return wrapper\n"
        "\n"
        "@memoize\n"
        "def fibonacci(n):\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
    ),
    "async_fetcher": (
        "import asyncio\n"
        "\n"
        "async def fetch(url, session=None):\n"
        "    await asyncio.sleep(0.01)\n"
        "    return {'url': url, 'status': 200}\n"
        "\n"
        "async def fetch_all(urls):\n"
        "    tasks = [fetch(u) for u in urls]\n"
        "    return await asyncio.gather(*tasks)\n"
        "\n"
        "def run_fetcher(urls):\n"
        "    return asyncio.run(fetch_all(urls))\n"
    ),
}


# -- Main ---------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Experiment 16 -- Budget Allocation")
    print("=" * 60)

    tmpfiles = []
    # results[strategy] = list of per-program dicts
    by_strategy = {s: [] for s in STRATEGIES}

    for pname, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        # Also run load once to get coordinate count
        load_objs = run_jugeo("load", path)
        coords = 0
        if load_objs:
            s = load_objs[0].get("summary", load_objs[0])
            coords = s.get("coordinates", 0)

        for strategy in STRATEGIES:
            t0 = time.time()
            descend_objs = run_jugeo("descend", "--strategy", strategy, path)
            elapsed = time.time() - t0

            verdict = "unknown"
            trust = "UNVERIFIED"
            props, props_ok, obstructions, sections = 0, 0, 0, 0
            if descend_objs:
                d = descend_objs[0]
                verdict = d.get("verdict", "unknown")
                trust = d.get("trust", "UNVERIFIED")
                obstructions = len(d.get("obstructions", []))
                sections = d.get("local_sections", 0)
                for sec in d.get("sections_detail", []):
                    props += sec.get("propositions", 0)
                    props_ok += sec.get("ok", 0)

            by_strategy[strategy].append({
                "name": pname, "coords": coords,
                "verdict": verdict, "trust": trust,
                "props": props, "props_ok": props_ok,
                "obstructions": obstructions, "sections": sections,
                "time": elapsed,
            })

        print("  {:<20} coords={:>2}  eager={:.2f}s  exhaustive={:.2f}s  iterative={:.2f}s".format(
            pname, coords,
            by_strategy["eager"][-1]["time"],
            by_strategy["exhaustive"][-1]["time"],
            by_strategy["iterative"][-1]["time"],
        ))

    # ---- Aggregate per strategy ----------------------------------------------
    n_total = len(PROGRAMS)
    total_sessions = n_total * len(STRATEGIES)

    def agg(strategy):
        rows = by_strategy[strategy]
        verified = sum(1 for r in rows if r["verdict"] == "verified")
        times = [r["time"] for r in rows]
        props_list = [r["props"] for r in rows]
        obs = sum(r["obstructions"] for r in rows)
        return {
            "verified": verified,
            "mean_time": statistics.mean(times) if times else 0.0,
            "mean_props": statistics.mean(props_list) if props_list else 0.0,
            "total_obstructions": obs,
            "obstruction_rate": obs / max(n_total, 1),
            "times": times,
            "props_list": props_list,
        }

    eager = agg("eager")
    exhaustive = agg("exhaustive")
    iterative = agg("iterative")

    # Adaptive ratio = exhaustive mean time / eager mean time
    adaptive_ratio = exhaustive["mean_time"] / max(eager["mean_time"], 1e-9)

    # Stall rates (obstruction rate for "fixed" = eager, "adaptive" = exhaustive)
    fixed_stall_rate = eager["obstruction_rate"]
    adaptive_stall_rate = exhaustive["obstruction_rate"]

    # Mean budget per channel: average time per coordinate across all strategies
    all_times = []
    all_coords = []
    for strategy in STRATEGIES:
        for r in by_strategy[strategy]:
            all_times.append(r["time"])
            all_coords.append(max(r["coords"], 1))
    per_channel = [t / c for t, c in zip(all_times, all_coords)]
    mean_budget_per_channel = statistics.mean(per_channel) if per_channel else 0.0

    # Competitive ratio = exhaustive verified / eager verified
    competitive_ratio = (
        exhaustive["verified"] / max(eager["verified"], 1)
    )

    # ---- Save JSON -----------------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper16.json")
    with open(json_path, "w") as jf:
        json.dump({
            "by_strategy": {s: by_strategy[s] for s in STRATEGIES},
            "aggregate": {
                "eager": eager, "exhaustive": exhaustive, "iterative": iterative,
                "adaptive_ratio": adaptive_ratio,
                "competitive_ratio": competitive_ratio,
            },
        }, jf, indent=2, default=str)
    print("\nSaved results to " + json_path)

    # ---- Write LaTeX macros --------------------------------------------------
    out_path = os.path.join(REPO_ROOT, "papers", "data-paper16.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper16.tex -- AUTO-GENERATED by exp16_budget_allocation.py\n")
        f.write("% DO NOT EDIT -- regenerate with: python3 experiments/exp16_budget_allocation.py\n\n")

        f.write("% --- Core counts ---\n")
        write_macro(f, "ppSixteenTotalPrograms", n_total)
        write_macro(f, "ppSixteenTotalSessions", total_sessions)

        f.write("\n% --- Verified counts per strategy ---\n")
        write_macro(f, "ppSixteenEagerVerified", eager["verified"])
        write_macro(f, "ppSixteenExhaustiveVerified", exhaustive["verified"])
        write_macro(f, "ppSixteenIterativeVerified", iterative["verified"])

        f.write("\n% --- Mean time per strategy ---\n")
        write_macro(f, "ppSixteenEagerMeanTime", "{:.2f}\\,s".format(eager["mean_time"]))
        write_macro(f, "ppSixteenExhaustiveMeanTime", "{:.2f}\\,s".format(exhaustive["mean_time"]))
        write_macro(f, "ppSixteenIterativeMeanTime", "{:.2f}\\,s".format(iterative["mean_time"]))

        f.write("\n% --- Mean propositions per strategy ---\n")
        write_macro(f, "ppSixteenEagerMeanProps", "{:.1f}".format(eager["mean_props"]))
        write_macro(f, "ppSixteenExhaustiveMeanProps", "{:.1f}".format(exhaustive["mean_props"]))
        write_macro(f, "ppSixteenIterativeMeanProps", "{:.1f}".format(iterative["mean_props"]))

        f.write("\n% --- Ratios and rates ---\n")
        write_macro(f, "ppSixteenAdaptiveRatio", "{:.2f}$\\times$".format(adaptive_ratio))
        write_macro(f, "ppSixteenFixedStallRate", "{:.1f}\\%".format(fixed_stall_rate * 100))
        write_macro(f, "ppSixteenAdaptiveStallRate", "{:.1f}\\%".format(adaptive_stall_rate * 100))
        write_macro(f, "ppSixteenMeanBudgetPerChannel", "{:.3f}\\,s".format(mean_budget_per_channel))
        write_macro(f, "ppSixteenCompetitiveRatio", "{:.2f}".format(competitive_ratio))

    print("Wrote " + out_path)
    print()
    print("SUMMARY:")
    print("  Total programs:           {}".format(n_total))
    print("  Total sessions:           {}".format(total_sessions))
    print("  Eager verified:           {}".format(eager["verified"]))
    print("  Exhaustive verified:      {}".format(exhaustive["verified"]))
    print("  Iterative verified:       {}".format(iterative["verified"]))
    print("  Eager mean time:          {:.2f}s".format(eager["mean_time"]))
    print("  Exhaustive mean time:     {:.2f}s".format(exhaustive["mean_time"]))
    print("  Iterative mean time:      {:.2f}s".format(iterative["mean_time"]))
    print("  Adaptive ratio:           {:.2f}x".format(adaptive_ratio))
    print("  Fixed stall rate:         {:.1f}%".format(fixed_stall_rate * 100))
    print("  Adaptive stall rate:      {:.1f}%".format(adaptive_stall_rate * 100))
    print("  Mean budget/channel:      {:.3f}s".format(mean_budget_per_channel))
    print("  Competitive ratio:        {:.2f}".format(competitive_ratio))

    # cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
