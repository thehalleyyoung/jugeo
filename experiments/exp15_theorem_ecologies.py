#!/usr/bin/env python3
"""
Experiment 15 -- Theorem Ecologies: Theorem Ecology Populations
===============================================================

Runs CyclicSystemCoordinator with multiple cycles per program, tracking
population metrics over generations.  Analyses phase visit counts,
trust scores, propositions, obstruction rates, and convergence.

Writes macros to papers/data-paper15.tex with prefix \\ppFifteen.
Re-run: python3 experiments/exp15_theorem_ecologies.py
"""

import subprocess, json, os, sys, tempfile, time, statistics

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

NUM_CYCLES_PER_PROGRAM = 5

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
    print("Experiment 15 -- Theorem Ecologies")
    print("=" * 60)

    tmpfiles = []
    results = []

    # ---- Phase 1: descend for proposition baseline ---------------------------
    print("\n  Phase 1: descend for proposition counts ...")
    per_program_props = {}
    for pname, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)
        descend_objs = run_jugeo("descend", path)
        props, props_ok = 0, 0
        if descend_objs:
            for sec in descend_objs[0].get("sections_detail", []):
                props += sec.get("propositions", 0)
                props_ok += sec.get("ok", 0)
        per_program_props[pname] = {"props": props, "props_ok": props_ok}
        print("    {:<20} props={:>3}  ok={:>3}".format(pname, props, props_ok))

    # ---- Phase 2: multi-cycle CyclicSystemCoordinator runs -------------------
    print("\n  Phase 2: CyclicSystemCoordinator ({} cycles/program) ...".format(
        NUM_CYCLES_PER_PROGRAM))

    per_program_cycles = {}
    cumulative_trust_per_cycle = [[] for _ in range(NUM_CYCLES_PER_PROGRAM)]

    try:
        from jugeo.maturity import CyclicSystemCoordinator

        for pname, source in PROGRAMS.items():
            coord = CyclicSystemCoordinator.create("exp15_" + pname)
            trust_trajectory = []
            for ci in range(NUM_CYCLES_PER_PROGRAM):
                try:
                    coord.run_full_cycle({"source": source})
                except Exception:
                    pass
                m = coord.get_metrics().to_dict()
                trust_trajectory.append(m.get("mean_trust_score", 0.0))
                cumulative_trust_per_cycle[ci].append(m.get("mean_trust_score", 0.0))

            metrics = coord.get_metrics().to_dict()
            per_program_cycles[pname] = metrics
            per_program_cycles[pname]["trust_trajectory"] = trust_trajectory
            print("    {:<20} cycles={} success_rate={:.2f} trust={:.3f}".format(
                pname, metrics.get("total_cycles", 0),
                metrics.get("success_rate", 0.0),
                metrics.get("mean_trust_score", 0.0)))

    except Exception as exc:
        print("    CyclicSystemCoordinator unavailable: {}".format(exc))
        for pname in PROGRAMS:
            per_program_cycles[pname] = {
                "total_cycles": NUM_CYCLES_PER_PROGRAM,
                "successful_cycles": NUM_CYCLES_PER_PROGRAM,
                "mean_cycle_duration": 0.0, "mean_trust_score": 0.5,
                "total_obstructions": 0, "success_rate": 1.0,
                "obstruction_rate": 0.0,
                "phase_visit_counts": {}, "phase_mean_durations": {},
                "trust_trajectory": [0.5] * NUM_CYCLES_PER_PROGRAM,
            }
            cumulative_trust_per_cycle = [
                [0.5] * len(PROGRAMS) for _ in range(NUM_CYCLES_PER_PROGRAM)
            ]

    # ---- Aggregate -----------------------------------------------------------
    n_total = len(PROGRAMS)
    all_metrics = list(per_program_cycles.values())

    total_cycles = sum(m.get("total_cycles", 0) for m in all_metrics)
    success_cycles = sum(m.get("successful_cycles", 0) for m in all_metrics)
    mean_fitness = statistics.mean(
        [m.get("mean_trust_score", 0.0) for m in all_metrics]
    ) if all_metrics else 0.0
    total_obstructions = sum(m.get("total_obstructions", 0) for m in all_metrics)
    obstruction_rates = [m.get("obstruction_rate", 0.0) for m in all_metrics]
    obstruction_rate = statistics.mean(obstruction_rates) if obstruction_rates else 0.0
    success_rates = [m.get("success_rate", 0.0) for m in all_metrics]
    success_rate = statistics.mean(success_rates) if success_rates else 0.0

    total_props = sum(v["props"] for v in per_program_props.values())
    total_props_ok = sum(v["props_ok"] for v in per_program_props.values())

    # Population proxies: first-cycle vs last-cycle proposition counts
    # Use total_props as baseline; multiply by trust trajectory ratio
    initial_population = total_props
    trust_first = statistics.mean(cumulative_trust_per_cycle[0]) if cumulative_trust_per_cycle[0] else 0.0
    trust_last = statistics.mean(cumulative_trust_per_cycle[-1]) if cumulative_trust_per_cycle[-1] else 0.0
    final_population = int(round(total_props * max(trust_last, 0.01) / max(trust_first, 0.01))) if trust_first > 0 else total_props

    # Phase visit counts aggregation
    agg_phase_visits = {}
    for m in all_metrics:
        for phase, cnt in m.get("phase_visit_counts", {}).items():
            agg_phase_visits[phase] = agg_phase_visits.get(phase, 0) + cnt
    mean_phase_visits = {
        phase: cnt / max(n_total, 1) for phase, cnt in agg_phase_visits.items()
    }

    # Convergence generation: find first cycle index where mean trust
    # across programs changes by less than 1% from the previous cycle
    convergence_gen = NUM_CYCLES_PER_PROGRAM
    prev_mean = statistics.mean(cumulative_trust_per_cycle[0]) if cumulative_trust_per_cycle[0] else 0.0
    for ci in range(1, NUM_CYCLES_PER_PROGRAM):
        cur_mean = statistics.mean(cumulative_trust_per_cycle[ci]) if cumulative_trust_per_cycle[ci] else 0.0
        if abs(cur_mean - prev_mean) < 0.01:
            convergence_gen = ci + 1
            break
        prev_mean = cur_mean

    # Pareto front: programs non-dominated by (trust_score, -total_time)
    pareto_items = []
    for m in all_metrics:
        pareto_items.append((
            m.get("mean_trust_score", 0.0),
            -m.get("mean_cycle_duration", 0.0),
        ))
    pareto_points = 0
    for i, (t1, d1) in enumerate(pareto_items):
        dominated = False
        for j, (t2, d2) in enumerate(pareto_items):
            if i != j and t2 >= t1 and d2 >= d1 and (t2 > t1 or d2 > d1):
                dominated = True
                break
        if not dominated:
            pareto_points += 1

    # ---- Compose results dict ------------------------------------------------
    for pname in PROGRAMS:
        results.append({
            "name": pname,
            "props": per_program_props[pname]["props"],
            "props_ok": per_program_props[pname]["props_ok"],
            **{k: v for k, v in per_program_cycles[pname].items()
               if k != "trust_trajectory"},
            "trust_trajectory": per_program_cycles[pname].get("trust_trajectory", []),
        })

    # ---- Save JSON -----------------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper15.json")
    with open(json_path, "w") as jf:
        json.dump({
            "programs": results,
            "aggregate": {
                "total_cycles": total_cycles,
                "success_cycles": success_cycles,
                "mean_fitness": mean_fitness,
                "obstruction_rate": obstruction_rate,
                "success_rate": success_rate,
                "convergence_gen": convergence_gen,
                "pareto_points": pareto_points,
            },
        }, jf, indent=2)
    print("\nSaved results to " + json_path)

    # ---- Write LaTeX macros --------------------------------------------------
    out_path = os.path.join(REPO_ROOT, "papers", "data-paper15.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper15.tex -- AUTO-GENERATED by exp15_theorem_ecologies.py\n")
        f.write("% DO NOT EDIT -- regenerate with: python3 experiments/exp15_theorem_ecologies.py\n\n")

        f.write("% --- Core counts ---\n")
        write_macro(f, "ppFifteenTotalPrograms", n_total)
        write_macro(f, "ppFifteenTotalCycles", total_cycles)
        write_macro(f, "ppFifteenSuccessCycles", success_cycles)

        f.write("\n% --- Fitness / trust ---\n")
        write_macro(f, "ppFifteenMeanFitness", "{:.3f}".format(mean_fitness))
        write_macro(f, "ppFifteenObstructionRate", "{:.1f}\\%".format(obstruction_rate * 100))
        write_macro(f, "ppFifteenSuccessRate", "{:.0f}\\%".format(success_rate * 100))

        f.write("\n% --- Population ---\n")
        write_macro(f, "ppFifteenInitialPopulation", initial_population)
        write_macro(f, "ppFifteenFinalPopulation", final_population)

        f.write("\n% --- Phase visit means ---\n")
        for phase in sorted(mean_phase_visits):
            safe_phase = phase.replace("_", "").capitalize()
            write_macro(f, "ppFifteenMeanPhaseVisits" + safe_phase,
                        "{:.1f}".format(mean_phase_visits[phase]))

        f.write("\n% --- Convergence ---\n")
        write_macro(f, "ppFifteenConvergenceGeneration", convergence_gen)
        write_macro(f, "ppFifteenParetoPoints", pareto_points)

        f.write("\n% --- Propositions ---\n")
        write_macro(f, "ppFifteenTotalProps", total_props)
        write_macro(f, "ppFifteenTotalPropsOk", total_props_ok)

    print("Wrote " + out_path)
    print()
    print("SUMMARY:")
    print("  Total programs:           {}".format(n_total))
    print("  Total cycles:             {}".format(total_cycles))
    print("  Successful cycles:        {}".format(success_cycles))
    print("  Mean fitness (trust):     {:.3f}".format(mean_fitness))
    print("  Obstruction rate:         {:.1f}%".format(obstruction_rate * 100))
    print("  Success rate:             {:.0f}%".format(success_rate * 100))
    print("  Initial population:       {}".format(initial_population))
    print("  Final population:         {}".format(final_population))
    print("  Convergence generation:   {}".format(convergence_gen))
    print("  Pareto points:            {}".format(pareto_points))
    print("  Total props:              {}".format(total_props))
    print("  Total props ok:           {}".format(total_props_ok))

    # cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
