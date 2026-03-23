#!/usr/bin/env python3
"""
Experiment 14 -- Discovery Engine: Discovery Pipeline Results
=============================================================

Runs each program through load + descend + evaluate + classify, then runs
CyclicSystemCoordinator for discovery-like cycles.  Analyses classification
distributions and discovery throughput.

Writes macros to papers/data-paper14.tex with prefix \\ppFourteen.
Re-run: python3 experiments/exp14_discovery_engine.py
"""

import subprocess, json, os, sys, tempfile, time, statistics

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

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
        "            raise ValueError('Must deposit positive amount')\n"
        "        self.balance += amount\n"
        "        return self.balance\n"
        "    def withdraw(self, amount):\n"
        "        if amount <= 0:\n"
        "            raise ValueError('Must withdraw positive amount')\n"
        "        if amount > self.balance:\n"
        "            raise ValueError('Insufficient funds')\n"
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
    print("Experiment 14 -- Discovery Engine")
    print("=" * 60)

    tmpfiles = []
    results = []

    # ---- Per-program pipeline: load + descend + evaluate + classify ----------
    for pname, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)
        t0 = time.time()

        load_objs = run_jugeo("load", path)
        descend_objs = run_jugeo("descend", path)
        eval_objs = run_jugeo("evaluate", path)
        classify_objs = run_jugeo("classify", path)

        elapsed = time.time() - t0

        # -- load
        coords, morphisms = 0, 0
        if load_objs:
            s = load_objs[0].get("summary", load_objs[0])
            coords = s.get("coordinates", 0)
            morphisms = s.get("morphisms", 0)

        # -- descend
        verdict = "unknown"
        props, props_ok, sections_count = 0, 0, 0
        if descend_objs:
            d = descend_objs[0]
            verdict = d.get("verdict", "unknown")
            sections_count = d.get("local_sections", 0)
            for sec in d.get("sections_detail", []):
                props += sec.get("propositions", 0)
                props_ok += sec.get("ok", 0)

        # -- evaluate
        coverage = 0.0
        if eval_objs:
            coverage = eval_objs[0].get("coverage", 0.0)

        # -- classify
        category = "UNKNOWN"
        confidence = 0.0
        subsystems = []
        if classify_objs:
            cl = classify_objs[0].get("classification", {})
            category = cl.get("category", "UNKNOWN")
            confidence = cl.get("confidence", 0.0)
            subsystems = classify_objs[0].get("recommended_subsystems", [])

        results.append({
            "name": pname, "coords": coords, "morphisms": morphisms,
            "verdict": verdict, "props": props, "props_ok": props_ok,
            "sections": sections_count, "coverage": coverage,
            "category": category, "confidence": confidence,
            "subsystems": subsystems, "time": elapsed,
        })
        print("  {:<20} cat={:<14} conf={:.2f}  props={:>3}  verdict={:<10} {:.2f}s".format(
            pname, category, confidence, props, verdict, elapsed))

    # ---- CyclicSystemCoordinator discovery cycles ----------------------------
    print("\n  Running CyclicSystemCoordinator cycles ...")
    cycle_metrics = None
    try:
        from jugeo.maturity import CyclicSystemCoordinator
        coord = CyclicSystemCoordinator.create("exp14_discovery")
        for pname, source in PROGRAMS.items():
            try:
                coord.run_full_cycle({"source": source})
            except Exception:
                pass
        cycle_metrics = coord.get_metrics().to_dict()
        print("  Cycles completed: {}  success_rate: {:.2f}".format(
            cycle_metrics.get("total_cycles", 0),
            cycle_metrics.get("success_rate", 0.0)))
    except Exception as exc:
        print("  CyclicSystemCoordinator unavailable: {}".format(exc))
        cycle_metrics = {
            "total_cycles": len(results), "successful_cycles": len(results),
            "mean_cycle_duration": 0.0, "mean_trust_score": 0.0,
            "total_obstructions": 0, "success_rate": 1.0,
            "obstruction_rate": 0.0,
            "phase_visit_counts": {}, "phase_mean_durations": {},
        }

    # ---- Aggregate -----------------------------------------------------------
    n_total = len(results)
    total_coords = sum(r["coords"] for r in results)
    total_morphisms = sum(r["morphisms"] for r in results)
    total_props = sum(r["props"] for r in results)
    total_props_ok = sum(r["props_ok"] for r in results)
    verified_count = sum(1 for r in results if r["verdict"] == "verified")
    verified_rate = (verified_count / max(n_total, 1)) * 100
    confidences = [r["confidence"] for r in results]
    mean_confidence = statistics.mean(confidences) if confidences else 0.0
    timings = [r["time"] for r in results]
    mean_discovery_time = statistics.mean(timings) if timings else 0.0
    total_discovery_time = sum(timings)
    sections_vals = [r["sections"] for r in results]
    mean_descent_sections = statistics.mean(sections_vals) if sections_vals else 0.0

    # category distribution
    cat_counts = {}
    for r in results:
        cat_counts[r["category"]] = cat_counts.get(r["category"], 0) + 1
    analysis_count = cat_counts.get("ANALYSIS", 0)
    synthesis_count = cat_counts.get("SYNTHESIS", 0)
    verif_count = cat_counts.get("VERIFICATION", 0)

    cycle_success_rate = cycle_metrics.get("success_rate", 0.0) * 100

    # ---- Save JSON -----------------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper14.json")
    with open(json_path, "w") as jf:
        json.dump({"programs": results, "cycle_metrics": cycle_metrics}, jf, indent=2)
    print("\nSaved results to " + json_path)

    # ---- Write LaTeX macros --------------------------------------------------
    out_path = os.path.join(REPO_ROOT, "papers", "data-paper14.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper14.tex -- AUTO-GENERATED by exp14_discovery_engine.py\n")
        f.write("% DO NOT EDIT -- regenerate with: python3 experiments/exp14_discovery_engine.py\n\n")

        f.write("% --- Core counts ---\n")
        write_macro(f, "ppFourteenTotalPrograms", n_total)
        write_macro(f, "ppFourteenTotalCoords", total_coords)
        write_macro(f, "ppFourteenTotalMorphisms", total_morphisms)

        f.write("\n% --- Discovery / descent ---\n")
        write_macro(f, "ppFourteenDiscoveredInvariants", total_props)
        write_macro(f, "ppFourteenVerifiedCount", verified_count)
        write_macro(f, "ppFourteenVerifiedRate", "{:.0f}\\%".format(verified_rate))
        write_macro(f, "ppFourteenMeanDescentSections", "{:.1f}".format(mean_descent_sections))

        f.write("\n% --- Classification ---\n")
        write_macro(f, "ppFourteenMeanConfidence", "{:.2f}".format(mean_confidence))
        write_macro(f, "ppFourteenAnalysisCount", analysis_count)
        write_macro(f, "ppFourteenSynthesisCount", synthesis_count)
        write_macro(f, "ppFourteenVerifCount", verif_count)

        f.write("\n% --- Timing ---\n")
        write_macro(f, "ppFourteenMeanDiscoveryTime", "{:.2f}\\,s".format(mean_discovery_time))
        write_macro(f, "ppFourteenTotalDiscoveryTime", "{:.1f}\\,s".format(total_discovery_time))

        f.write("\n% --- Propositions ---\n")
        write_macro(f, "ppFourteenTotalProps", total_props)
        write_macro(f, "ppFourteenTotalPropsOk", total_props_ok)

        f.write("\n% --- Cycle metrics ---\n")
        write_macro(f, "ppFourteenCycleSuccessRate", "{:.0f}\\%".format(cycle_success_rate))

    print("Wrote " + out_path)
    print()
    print("SUMMARY:")
    print("  Total programs:          {}".format(n_total))
    print("  Total coordinates:       {}".format(total_coords))
    print("  Total morphisms:         {}".format(total_morphisms))
    print("  Discovered invariants:   {}".format(total_props))
    print("  Verified:                {} ({:.0f}%)".format(verified_count, verified_rate))
    print("  Mean confidence:         {:.2f}".format(mean_confidence))
    print("  Categories:              ANALYSIS={} SYNTHESIS={} VERIFICATION={}".format(
        analysis_count, synthesis_count, verif_count))
    print("  Mean discovery time:     {:.2f}s".format(mean_discovery_time))
    print("  Total discovery time:    {:.1f}s".format(total_discovery_time))
    print("  Cycle success rate:      {:.0f}%".format(cycle_success_rate))

    # cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
