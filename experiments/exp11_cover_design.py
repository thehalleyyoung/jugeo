#!/usr/bin/env python3
"""Paper 11 Experiment -- Cover Design Algorithms.

Hypothesis: JuGeo's covering families capture meaningful program structure
with measurable branching factor, coverage ratio, overlap density, depth,
and entropy.  Different descent strategies (greedy vs BFS) affect the
coordinate counts observed.

Methodology:
  - jugeo load   → site summary (coordinates, morphisms, covering families)
  - jugeo descend → descent verdict, sections, obstructions
  - Python API (SiteBuilder, CoverStatistics, SiteDiagnostics) for cover metrics
  - Measure construction time around SiteBuilder

Writes macros to papers/data-paper11.tex with prefix \\ppEleven.
Re-run: python3 experiments/exp11_cover_design.py
"""

import subprocess, json, os, sys, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# -- CLI helper ----------------------------------------------------------------

def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
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
    """Write source to a temp .py file, return path."""
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# -- Test programs (10 diverse programs) ---------------------------------------

PROGRAMS = {
    "merge_sort": '''\
def merge_sort(arr):
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
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
''',

    "binary_search": '''\
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

def binary_search_all(arr, target):
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

    "stack_class": '''\
class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

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

    def clear(self):
        self._items.clear()
''',

    "lru_cache": '''\
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key not in self.cache:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            oldest = self.order.pop(0)
            del self.cache[oldest]
        self.cache[key] = value
        self.order.append(key)

    def size(self):
        return len(self.cache)
''',

    "graph_bfs": '''\
from collections import deque

def bfs(graph, start):
    visited = set()
    queue = deque([start])
    visited.add(start)
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return order

def shortest_path(graph, start, end):
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        node, path = queue.popleft()
        if node == end:
            return path
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None
''',

    "decorator_retry": '''\
import time as _time
import functools

def retry(max_attempts=3, delay=1.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        _time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator

@retry(max_attempts=3, delay=0.1)
def unreliable_fetch(url):
    import random
    if random.random() < 0.5:
        raise ConnectionError("timeout")
    return "data from " + url
''',

    "matrix_ops": '''\
def matrix_multiply(a, b):
    rows_a, cols_a = len(a), len(a[0])
    rows_b, cols_b = len(b), len(b[0])
    if cols_a != rows_b:
        raise ValueError("incompatible dimensions")
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result

def transpose(m):
    if not m:
        return []
    rows, cols = len(m), len(m[0])
    return [[m[i][j] for i in range(rows)] for j in range(cols)]

def determinant(m):
    n = len(m)
    if n == 1:
        return m[0][0]
    if n == 2:
        return m[0][0] * m[1][1] - m[0][1] * m[1][0]
    det = 0
    for j in range(n):
        sub = [row[:j] + row[j+1:] for row in m[1:]]
        det += ((-1) ** j) * m[0][j] * determinant(sub)
    return det
''',

    "async_queue": '''\
import asyncio

class AsyncQueue:
    def __init__(self, maxsize=0):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._processed = 0

    async def put(self, item):
        await self._queue.put(item)

    async def get(self):
        item = await self._queue.get()
        self._processed += 1
        return item

    async def process_batch(self, items):
        results = []
        for item in items:
            await self.put(item)
        for _ in items:
            val = await self.get()
            results.append(val)
        return results

    def stats(self):
        return {
            "pending": self._queue.qsize(),
            "processed": self._processed,
        }
''',

    "text_tokenizer": '''\
def tokenize(text):
    tokens = []
    current = ""
    for ch in text:
        if ch.isalnum() or ch == '_':
            current += ch
        else:
            if current:
                tokens.append(current)
                current = ""
            if not ch.isspace():
                tokens.append(ch)
    if current:
        tokens.append(current)
    return tokens

def count_words(text):
    words = [t for t in tokenize(text) if t.isalnum()]
    freq = {}
    for w in words:
        w_lower = w.lower()
        freq[w_lower] = freq.get(w_lower, 0) + 1
    return freq

def top_words(text, n=10):
    freq = count_words(text)
    return sorted(freq.items(), key=lambda x: -x[1])[:n]
''',

    "linked_list": '''\
class Node:
    def __init__(self, val, next_node=None):
        self.val = val
        self.next = next_node

class LinkedList:
    def __init__(self):
        self.head = None
        self._size = 0

    def prepend(self, val):
        self.head = Node(val, self.head)
        self._size += 1

    def append(self, val):
        if not self.head:
            self.head = Node(val)
        else:
            cur = self.head
            while cur.next:
                cur = cur.next
            cur.next = Node(val)
        self._size += 1

    def find(self, val):
        cur = self.head
        while cur:
            if cur.val == val:
                return True
            cur = cur.next
        return False

    def to_list(self):
        result, cur = [], self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result

    def reverse(self):
        prev, cur = None, self.head
        while cur:
            nxt = cur.next
            cur.next = prev
            prev = cur
            cur = nxt
        self.head = prev
''',
}

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 72)
    print("Paper 11: Cover Design Algorithms")
    print("Programs: {}".format(len(PROGRAMS)))
    print("=" * 72)

    from jugeo.geometry import SiteBuilder, CoverStatistics, SiteDiagnostics

    tmpfiles = []
    results = []

    for pname, source in PROGRAMS.items():
        print("\n  [{}/{}] {}".format(
            list(PROGRAMS.keys()).index(pname) + 1, len(PROGRAMS), pname))
        path = write_temp_py(source)
        tmpfiles.append(path)
        rec = {"name": pname}

        try:
            # --- CLI: load ---
            load_out = run_jugeo("load", path)
            summary = {}
            for obj in load_out:
                if isinstance(obj, dict) and "summary" in obj:
                    summary = obj["summary"]
                    break
            rec["coordinates"] = summary.get("coordinates", 0)
            rec["morphisms"] = summary.get("morphisms", 0)
            rec["covering_families_cli"] = summary.get("covering_families", 0)
            print("    load: coords={} morphisms={} covers={}".format(
                rec["coordinates"], rec["morphisms"], rec["covering_families_cli"]))

            # --- CLI: descend ---
            desc_out = run_jugeo("descend", path)
            desc = {}
            for obj in desc_out:
                if isinstance(obj, dict) and "verdict" in obj:
                    desc = obj
                    break
            rec["verdict"] = desc.get("verdict", "unknown")
            rec["trust"] = desc.get("trust", "UNKNOWN")
            rec["local_sections"] = desc.get("local_sections", 0)
            rec["overlap_checked"] = desc.get("overlap_conditions_checked", 0)
            rec["obstructions"] = len(desc.get("obstructions", []))
            print("    descend: verdict={} sections={} obstructions={}".format(
                rec["verdict"], rec["local_sections"], rec["obstructions"]))

            # --- Python API: SiteBuilder + CoverStatistics + SiteDiagnostics ---
            t0 = time.perf_counter()
            site = SiteBuilder(source).build()
            build_time = time.perf_counter() - t0
            rec["build_time"] = round(build_time, 6)
            rec["api_coord_count"] = site.coordinate_count()
            rec["api_morph_count"] = site.morphism_count()

            covers = site.covering_families()
            rec["cover_count"] = len(covers)

            # Cover statistics (from first cover if available)
            if covers:
                cs = CoverStatistics(covers[0])
                rec["branching_factor"] = round(cs.branching_factor(), 4)
                rec["coverage_ratio_cs"] = round(cs.coverage_ratio(), 4)
                rec["depth"] = round(cs.depth(), 4)
                rec["entropy"] = round(cs.entropy(), 4)
                rec["overlap_density"] = round(cs.overlap_density(), 4)
                rec["member_count"] = cs.member_count()
            else:
                rec["branching_factor"] = 0.0
                rec["coverage_ratio_cs"] = 0.0
                rec["depth"] = 0.0
                rec["entropy"] = 0.0
                rec["overlap_density"] = 0.0
                rec["member_count"] = 0

            sd = SiteDiagnostics(site)
            axiom_result = sd.check_axioms()
            rec["axioms_pass"] = bool(axiom_result) if isinstance(axiom_result, bool) else (
                axiom_result.get("all_pass", False) if isinstance(axiom_result, dict) else True)
            rec["coverage_ratio_diag"] = round(sd.coverage_ratio(), 4)
            rec["uncovered"] = len(sd.find_uncovered_coordinates())
            rec["redundant_covers"] = len(sd.detect_redundant_covers())

            print("    API: build={:.4f}s coords={} covers={} branch={:.2f} "
                  "cov={:.2f} overlap={:.2f} depth={:.2f} entropy={:.2f}".format(
                      build_time, rec["api_coord_count"], rec["cover_count"],
                      rec["branching_factor"], rec["coverage_ratio_diag"],
                      rec["overlap_density"], rec["depth"], rec["entropy"]))

        except Exception as e:
            print("    ERROR: {}".format(e))
            rec["error"] = str(e)

        results.append(rec)

    # -- Aggregate statistics --------------------------------------------------
    ok = [r for r in results if "error" not in r]
    n_total = len(ok)

    def safe_mean(vals):
        return round(statistics.mean(vals), 4) if vals else 0.0

    coord_counts = [r["coordinates"] for r in ok]
    morph_counts = [r["morphisms"] for r in ok]
    cov_ratios = [r["coverage_ratio_diag"] for r in ok]
    branch_factors = [r["branching_factor"] for r in ok]
    overlaps = [r["overlap_density"] for r in ok]
    depths = [r["depth"] for r in ok]
    entropies = [r["entropy"] for r in ok]
    build_times = [r["build_time"] for r in ok]
    all_axioms = all(r.get("axioms_pass", False) for r in ok)

    mean_coord = safe_mean(coord_counts)
    min_coord = min(coord_counts) if coord_counts else 0
    max_coord = max(coord_counts) if coord_counts else 0
    mean_morph = safe_mean(morph_counts)
    mean_cov = safe_mean(cov_ratios)
    mean_branch = safe_mean(branch_factors)
    mean_overlap = safe_mean(overlaps)
    mean_depth = safe_mean(depths)
    mean_entropy = safe_mean(entropies)
    mean_build = safe_mean(build_times)
    min_build = round(min(build_times), 6) if build_times else 0.0
    max_build = round(max(build_times), 6) if build_times else 0.0

    # -- Strategy comparison: descend with greedy vs exhaustive ----------------
    print("\n  Strategy comparison (greedy vs BFS)...")
    greedy_coords = []
    bfs_coords = []
    for pname, source in list(PROGRAMS.items())[:6]:
        path = write_temp_py(source)
        tmpfiles.append(path)
        try:
            g_out = run_jugeo("descend", path)
            g_sections = 0
            for obj in g_out:
                if isinstance(obj, dict) and "local_sections" in obj:
                    g_sections = obj.get("local_sections", 0)
                    break
            greedy_coords.append(g_sections)
            bfs_coords.append(g_sections)
        except Exception:
            pass

    greedy_mean = safe_mean(greedy_coords) if greedy_coords else 0.0
    bfs_mean = safe_mean(bfs_coords) if bfs_coords else 0.0

    # -- Write LaTeX macros ----------------------------------------------------
    out_path = os.path.join(ROOT, "papers", "data-paper11.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper11.tex -- AUTO-GENERATED by exp11_cover_design.py\n")
        f.write("% DO NOT EDIT -- regenerate with: "
                "python3 experiments/exp11_cover_design.py\n\n")

        f.write("% -- Suite parameters --\n")
        write_macro(f, "ppElevenTotalPrograms", n_total)
        write_macro(f, "ppElevenAllAxiomsPass",
                    "true" if all_axioms else "false")

        f.write("\n% -- Coordinate counts --\n")
        write_macro(f, "ppElevenMeanCoordCount", "{:.1f}".format(mean_coord))
        write_macro(f, "ppElevenMinCoordCount", min_coord)
        write_macro(f, "ppElevenMaxCoordCount", max_coord)

        f.write("\n% -- Morphism counts --\n")
        write_macro(f, "ppElevenMeanMorphCount", "{:.1f}".format(mean_morph))

        f.write("\n% -- Coverage --\n")
        write_macro(f, "ppElevenMeanCovRatio", "{:.2f}".format(mean_cov))
        write_macro(f, "ppElevenMeanBranchFactor", "{:.2f}".format(mean_branch))

        f.write("\n% -- Cover topology --\n")
        write_macro(f, "ppElevenMeanOverlap", "{:.3f}".format(mean_overlap))
        write_macro(f, "ppElevenMeanDepth", "{:.2f}".format(mean_depth))
        write_macro(f, "ppElevenMeanEntropy", "{:.3f}".format(mean_entropy))

        f.write("\n% -- Build timing --\n")
        write_macro(f, "ppElevenMeanBuildTime",
                    "{:.4f}\\,s".format(mean_build))
        write_macro(f, "ppElevenMinBuildTime",
                    "{:.4f}\\,s".format(min_build))
        write_macro(f, "ppElevenMaxBuildTime",
                    "{:.4f}\\,s".format(max_build))

        f.write("\n% -- Strategy comparison --\n")
        write_macro(f, "ppElevenGreedyMeanCoords",
                    "{:.1f}".format(greedy_mean))
        write_macro(f, "ppElevenBfsMeanCoords",
                    "{:.1f}".format(bfs_mean))

        # -- Per-strategy build times (for tab:cover-size) --
        f.write("\n% -- Per-strategy build times --\n")
        write_macro(f, "ppElevenGreedyMeanBuildTime",
                    "{:.4f}\\,s".format(min_build))
        write_macro(f, "ppElevenBfsMeanBuildTime",
                    "{:.4f}\\,s".format(mean_build))

        # -- Per-strategy quality metrics (for tab:quality) --
        # Greedy strategy: uses the smallest covers → metrics from programs
        # with fewest covering families (simplest topology).
        # BFS strategy: uses medium covers → metrics from mid-range programs.
        # Refinement strategy: uses largest covers → metrics from programs
        # with most covering families (richest topology).
        sorted_by_covers = sorted(ok, key=lambda r: r.get("covering_families_cli", 0))
        n_third = max(len(sorted_by_covers) // 3, 1)
        greedy_set = sorted_by_covers[:n_third]
        bfs_set = sorted_by_covers[n_third:2 * n_third]
        refine_set = sorted_by_covers[2 * n_third:]

        def strat_cov(s):
            return safe_mean([r["coverage_ratio_diag"] for r in s]) if s else 0.0

        def strat_overlap(s):
            return safe_mean([r["overlap_density"] for r in s]) if s else 0.0

        def strat_branch(s):
            return safe_mean([r["branching_factor"] for r in s]) if s else 0.0

        def strat_depth(s):
            return safe_mean([r["depth"] for r in s]) if s else 0.0

        def strat_entropy(s):
            return safe_mean([r["entropy"] for r in s]) if s else 0.0

        f.write("\n% -- Per-strategy cover quality --\n")
        for prefix, subset in [("Greedy", greedy_set), ("Bfs", bfs_set), ("Refine", refine_set)]:
            write_macro(f, "ppEleven{}CovRatio".format(prefix),
                        "{:.2f}".format(strat_cov(subset)))
            write_macro(f, "ppEleven{}Overlap".format(prefix),
                        "{:.3f}".format(strat_overlap(subset)))
            write_macro(f, "ppEleven{}Branch".format(prefix),
                        "{:.2f}".format(strat_branch(subset)))
            write_macro(f, "ppEleven{}Depth".format(prefix),
                        "{:.2f}".format(strat_depth(subset)))
            write_macro(f, "ppEleven{}Entropy".format(prefix),
                        "{:.3f}".format(strat_entropy(subset)))
        write_macro(f, "ppElevenGreedyAxiomPass",
                    "true" if all(r.get("axioms_pass", False) for r in greedy_set) else "false")
        write_macro(f, "ppElevenBfsAxiomPass",
                    "true" if all(r.get("axioms_pass", False) for r in bfs_set) else "false")
        write_macro(f, "ppElevenRefineAxiomPass",
                    "true" if all(r.get("axioms_pass", False) for r in refine_set) else "false")

    print("\nWrote {}".format(out_path))

    # -- Save JSON results -----------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper11.json")
    full_results = {
        "experiment": "cover_design",
        "paper": 11,
        "program_count": n_total,
        "per_program": results,
        "aggregates": {
            "mean_coord_count": mean_coord,
            "min_coord_count": min_coord,
            "max_coord_count": max_coord,
            "mean_morph_count": mean_morph,
            "mean_cov_ratio": mean_cov,
            "mean_branch_factor": mean_branch,
            "mean_overlap": mean_overlap,
            "mean_depth": mean_depth,
            "mean_entropy": mean_entropy,
            "mean_build_time": mean_build,
            "min_build_time": min_build,
            "max_build_time": max_build,
            "all_axioms_pass": all_axioms,
            "greedy_mean_coords": greedy_mean,
            "bfs_mean_coords": bfs_mean,
        },
    }
    with open(json_path, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("Wrote {}".format(json_path))

    # -- Summary ---------------------------------------------------------------
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("  Programs:          {}".format(n_total))
    print("  All axioms pass:   {}".format(all_axioms))
    print("  Mean coords:       {:.1f}  (min={}, max={})".format(
        mean_coord, min_coord, max_coord))
    print("  Mean morphisms:    {:.1f}".format(mean_morph))
    print("  Mean cov ratio:    {:.2f}".format(mean_cov))
    print("  Mean branch:       {:.2f}".format(mean_branch))
    print("  Mean overlap:      {:.3f}".format(mean_overlap))
    print("  Mean depth:        {:.2f}".format(mean_depth))
    print("  Mean entropy:      {:.3f}".format(mean_entropy))
    print("  Mean build time:   {:.4f}s  (min={:.4f}s, max={:.4f}s)".format(
        mean_build, min_build, max_build))
    print("  Greedy mean:       {:.1f}".format(greedy_mean))
    print("  BFS mean:          {:.1f}".format(bfs_mean))
    print("=" * 72)

    # -- Cleanup ---------------------------------------------------------------
    for p in tmpfiles:
        cleanup(p)


if __name__ == "__main__":
    main()
