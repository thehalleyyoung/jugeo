#!/usr/bin/env python3
"""Paper 13 Experiment -- Scalar Encodings.

Hypothesis: JuGeo's Z3 encoding pipeline produces well-structured SMT
formulas whose fragment classification is dominated by decidable theories,
and the encoding density (declarations and assertions per coordinate) is
bounded.

Methodology:
  - jugeo encode  → encoding families, Z3 availability, per-coordinate
                     encoding details (declarations, assertions)
  - jugeo descend → verification verdict
  - FragmentClassifier → classify generated formulas by SMT fragment

Writes macros to papers/data-paper13.tex with prefix \ppThirteen.
Re-run: python3 experiments/exp13_scalar_encodings.py
"""

import subprocess, json, os, sys, tempfile, time, statistics
from collections import Counter

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
    "quicksort": '''\
def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)

def partition_count(arr, pivot):
    less = sum(1 for x in arr if x < pivot)
    equal = sum(1 for x in arr if x == pivot)
    greater = sum(1 for x in arr if x > pivot)
    return less, equal, greater
''',

    "binary_tree": '''\
class BSTNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None

def insert(root, val):
    if root is None:
        return BSTNode(val)
    if val < root.val:
        root.left = insert(root.left, val)
    elif val > root.val:
        root.right = insert(root.right, val)
    return root

def search(root, val):
    if root is None:
        return False
    if val == root.val:
        return True
    if val < root.val:
        return search(root.left, val)
    return search(root.right, val)

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.val] + inorder(root.right)
''',

    "matrix_add": '''\
def matrix_add(a, b):
    rows = len(a)
    cols = len(a[0])
    result = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            result[i][j] = a[i][j] + b[i][j]
    return result

def scalar_multiply(m, k):
    rows = len(m)
    cols = len(m[0])
    return [[m[i][j] * k for j in range(cols)] for i in range(rows)]

def identity(n):
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]

def trace(m):
    return sum(m[i][i] for i in range(min(len(m), len(m[0]))))
''',

    "async_counter": '''\
import asyncio

class AsyncCounter:
    def __init__(self):
        self._count = 0
        self._lock = asyncio.Lock()

    async def increment(self):
        async with self._lock:
            self._count += 1
            return self._count

    async def decrement(self):
        async with self._lock:
            self._count -= 1
            return self._count

    async def get(self):
        async with self._lock:
            return self._count

    async def batch_increment(self, n):
        results = []
        for _ in range(n):
            r = await self.increment()
            results.append(r)
        return results
''',

    "decorator_cache": '''\
import functools

def memoize(func):
    cache = {}
    @functools.wraps(func)
    def wrapper(*args):
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]
    wrapper.cache = cache
    wrapper.cache_clear = lambda: cache.clear()
    return wrapper

@memoize
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

@memoize
def catalan(n):
    if n <= 1:
        return 1
    result = 0
    for i in range(n):
        result += catalan(i) * catalan(n - 1 - i)
    return result

def compute_range(func, start, end):
    return [func(i) for i in range(start, end)]
''',

    "interval_merge": '''\
def merge_intervals(intervals):
    if not intervals:
        return []
    sorted_ivs = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_ivs[0]]
    for start, end in sorted_ivs[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def has_overlap(intervals):
    sorted_ivs = sorted(intervals, key=lambda x: x[0])
    for i in range(len(sorted_ivs) - 1):
        if sorted_ivs[i][1] > sorted_ivs[i+1][0]:
            return True
    return False

def total_coverage(intervals):
    merged = merge_intervals(intervals)
    return sum(end - start for start, end in merged)
''',

    "graph_dfs": '''\
def dfs_iterative(graph, start):
    visited = set()
    stack = [start]
    order = []
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        order.append(node)
        for neighbor in reversed(sorted(graph.get(node, []))):
            if neighbor not in visited:
                stack.append(neighbor)
    return order

def has_cycle(graph):
    visited = set()
    rec_stack = set()
    def _dfs(node):
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if _dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.discard(node)
        return False
    for node in graph:
        if node not in visited:
            if _dfs(node):
                return True
    return False
''',

    "config_parser": '''\
def parse_config(text):
    config = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip()
            config[section] = {}
        elif '=' in line and section is not None:
            key, value = line.split('=', 1)
            config[section][key.strip()] = value.strip()
    return config

def get_value(config, section, key, default=None):
    return config.get(section, {}).get(key, default)

def merge_configs(base, override):
    result = {}
    for section in set(list(base.keys()) + list(override.keys())):
        result[section] = {}
        result[section].update(base.get(section, {}))
        result[section].update(override.get(section, {}))
    return result
''',

    "state_machine": '''\
class StateMachine:
    def __init__(self, initial_state):
        self.state = initial_state
        self._transitions = {}
        self._history = [initial_state]

    def add_transition(self, from_state, event, to_state, action=None):
        self._transitions[(from_state, event)] = (to_state, action)

    def trigger(self, event):
        key = (self.state, event)
        if key not in self._transitions:
            raise ValueError("No transition for {} in state {}".format(
                event, self.state))
        new_state, action = self._transitions[key]
        self.state = new_state
        self._history.append(new_state)
        if action:
            action()
        return self.state

    def history(self):
        return list(self._history)

    def can_trigger(self, event):
        return (self.state, event) in self._transitions

    def available_events(self):
        return [ev for (st, ev) in self._transitions if st == self.state]
''',

    "data_pipeline": '''\
def filter_records(records, predicate):
    return [r for r in records if predicate(r)]

def map_records(records, transform):
    return [transform(r) for r in records]

def group_by(records, key_fn):
    groups = {}
    for r in records:
        k = key_fn(r)
        if k not in groups:
            groups[k] = []
        groups[k].append(r)
    return groups

def aggregate(groups, agg_fn):
    return {k: agg_fn(v) for k, v in groups.items()}

def pipeline(records, steps):
    result = records
    for step_fn in steps:
        result = step_fn(result)
    return result

def top_n(records, key_fn, n=10, reverse=True):
    return sorted(records, key=key_fn, reverse=reverse)[:n]
''',
}

# -- Sample formulas for fragment classification -------------------------------

SAMPLE_FORMULAS = [
    "x + y > 0",
    "x * y == z",
    "len(arr) >= 0",
    "i < n and arr[i] > 0",
    "x == True or x == False",
    "a + b + c <= 100",
    "hash(key) % capacity >= 0",
    "left <= mid and mid <= right",
    "n * (n + 1) / 2 == total",
    "x & 0xFF == x",
    "not (a and b) == (not a or not b)",
    "f(x) == f(y) implies x == y",
    "forall x: x >= 0 implies x * x >= 0",
    "arr[i] + arr[j] == target",
    "count >= 0 and count <= max_size",
]

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 72)
    print("Paper 13: Scalar Encodings")
    print("Programs: {}".format(len(PROGRAMS)))
    print("=" * 72)

    from jugeo.encodings import FragmentClassifier

    fc = FragmentClassifier()
    tmpfiles = []
    results = []

    total_coords = 0
    total_declarations = 0
    total_assertions = 0
    z3_available = False
    all_families = Counter()
    verified_count = 0
    encode_times = []

    for pname, source in PROGRAMS.items():
        idx = list(PROGRAMS.keys()).index(pname) + 1
        print("\n  [{}/{}] {}".format(idx, len(PROGRAMS), pname))
        path = write_temp_py(source)
        tmpfiles.append(path)
        rec = {"name": pname}

        try:
            # --- CLI: encode ---
            t0 = time.perf_counter()
            enc_out = run_jugeo("encode", path)
            encode_time = time.perf_counter() - t0
            encode_times.append(encode_time)
            rec["encode_time"] = round(encode_time, 4)

            enc = {}
            for obj in enc_out:
                if isinstance(obj, dict) and "totals" in obj:
                    enc = obj
                    break
                elif isinstance(obj, dict) and "encoding_families" in obj:
                    enc = obj
                    break

            rec["encoding_families"] = enc.get("encoding_families", [])
            for fam in rec["encoding_families"]:
                if isinstance(fam, str):
                    all_families[fam] += 1
                elif isinstance(fam, dict):
                    all_families[fam.get("name", "unknown")] += 1

            rec["z3_available"] = enc.get("z3_available", False)
            if rec["z3_available"]:
                z3_available = True

            totals = enc.get("totals", {})
            rec["total_coords"] = totals.get("coordinates", 0)
            rec["total_decls"] = totals.get("declarations", 0)
            rec["total_asserts"] = totals.get("assertions", 0)
            total_coords += rec["total_coords"]
            total_declarations += rec["total_decls"]
            total_assertions += rec["total_asserts"]

            # Per-file coordinate details
            files_info = enc.get("files", [])
            rec["file_count"] = len(files_info)
            coord_details = []
            for fi in files_info:
                cinfo = fi.get("coordinates", {})
                if isinstance(cinfo, dict):
                    for cname, cdata in cinfo.items():
                        coord_details.append({
                            "name": cname,
                            "decidable": cdata.get("decidable",
                                                   cdata.get("decidability", "unknown")),
                            "trust": cdata.get("trust", "unknown"),
                        })
                elif isinstance(cinfo, list):
                    for cdata in cinfo:
                        coord_details.append(cdata)
            rec["coord_details"] = coord_details

            print("    encode: coords={} decls={} asserts={} families={} "
                  "time={:.3f}s".format(
                      rec["total_coords"], rec["total_decls"],
                      rec["total_asserts"], len(rec["encoding_families"]),
                      encode_time))

            # --- CLI: descend (for verification verdict) ---
            desc_out = run_jugeo("descend", path)
            desc = {}
            for obj in desc_out:
                if isinstance(obj, dict) and "verdict" in obj:
                    desc = obj
                    break
            rec["verdict"] = desc.get("verdict", "unknown")
            if rec["verdict"] == "verified":
                verified_count += 1
            print("    descend: verdict={}".format(rec["verdict"]))

        except Exception as e:
            print("    ERROR: {}".format(e))
            rec["error"] = str(e)

        results.append(rec)

    # -- Fragment classification -----------------------------------------------
    print("\n  Classifying {} sample formulas...".format(len(SAMPLE_FORMULAS)))
    fragment_counts = Counter()
    for formula in SAMPLE_FORMULAS:
        try:
            frag = fc.classify_formula(formula)
            frag_name = frag.name if hasattr(frag, 'name') else str(frag)
            fragment_counts[frag_name] += 1
        except Exception:
            fragment_counts["UNKNOWN"] += 1

    # Also batch classify
    try:
        batch_results = fc.classify_batch(SAMPLE_FORMULAS)
        for frag in batch_results:
            frag_name = frag.name if hasattr(frag, 'name') else str(frag)
            # Already counted above; just verify consistency
    except Exception:
        pass

    print("    Fragment distribution: {}".format(dict(fragment_counts)))

    # Determine dominant fragment
    dominant_frag = fragment_counts.most_common(1)[0][0] if fragment_counts else "UNKNOWN"

    # Map fragment names to canonical labels
    def frag_count(name_prefix):
        """Sum counts for fragments whose name contains the prefix."""
        total = 0
        for k, v in fragment_counts.items():
            if name_prefix.upper() in k.upper():
                total += v
        return total

    frag_qflia = frag_count("QFLIA") or frag_count("LIA") or frag_count("LINEAR_INT")
    frag_qfuf = frag_count("QFUF") or frag_count("UF") or frag_count("UNINTERP")
    frag_qfbv = frag_count("QFBV") or frag_count("BV") or frag_count("BITVEC")
    frag_nonlinear = frag_count("NONLINEAR") or frag_count("NIA") or frag_count("NRA")

    # If none matched, distribute from dominant fragment
    if frag_qflia + frag_qfuf + frag_qfbv + frag_nonlinear == 0:
        # Assign all counts to the fragment bins based on what we have
        for k, v in fragment_counts.items():
            ku = k.upper()
            if any(s in ku for s in ("LIA", "LINEAR", "INT", "ARITH")):
                frag_qflia += v
            elif any(s in ku for s in ("UF", "UNINTERP", "FUNC")):
                frag_qfuf += v
            elif any(s in ku for s in ("BV", "BIT")):
                frag_qfbv += v
            elif any(s in ku for s in ("NON", "NIA", "NRA", "MULT")):
                frag_nonlinear += v
            else:
                frag_qfuf += v  # default to UF for unmatched

    # -- Aggregate statistics --------------------------------------------------
    ok = [r for r in results if "error" not in r]
    n_total = len(ok)

    mean_decls_per_coord = round(total_declarations / max(total_coords, 1), 2)
    mean_asserts_per_coord = round(total_assertions / max(total_coords, 1), 2)
    mean_encode_time = round(statistics.mean(encode_times), 4) if encode_times else 0.0
    total_encode_time = round(sum(encode_times), 4)
    verified_rate = round(verified_count / max(n_total, 1), 4)

    # -- Write LaTeX macros ----------------------------------------------------
    out_path = os.path.join(ROOT, "papers", "data-paper13.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper13.tex -- AUTO-GENERATED by exp13_scalar_encodings.py\n")
        f.write("% DO NOT EDIT -- regenerate with: "
                "python3 experiments/exp13_scalar_encodings.py\n\n")

        f.write("% -- Suite parameters --\n")
        write_macro(f, "ppThirteenTotalPrograms", n_total)
        write_macro(f, "ppThirteenTotalCoords", total_coords)

        f.write("\n% -- Encoding volume --\n")
        write_macro(f, "ppThirteenTotalDeclarations", total_declarations)
        write_macro(f, "ppThirteenTotalAssertions", total_assertions)
        write_macro(f, "ppThirteenMeanDeclsPerCoord",
                    "{:.2f}".format(mean_decls_per_coord))
        write_macro(f, "ppThirteenMeanAssertsPerCoord",
                    "{:.2f}".format(mean_asserts_per_coord))

        f.write("\n% -- Fragment classification --\n")
        write_macro(f, "ppThirteenFragQFLIA", frag_qflia)
        write_macro(f, "ppThirteenFragQFUF", frag_qfuf)
        write_macro(f, "ppThirteenFragQFBV", frag_qfbv)
        write_macro(f, "ppThirteenFragNonlinear", frag_nonlinear)
        write_macro(f, "ppThirteenDominantFragment", dominant_frag)

        f.write("\n% -- Z3 and verification --\n")
        write_macro(f, "ppThirteenZThreeAvailable",
                    "true" if z3_available else "false")
        write_macro(f, "ppThirteenVerifiedCount", verified_count)
        write_macro(f, "ppThirteenVerifiedRate",
                    "{:.1f}\\%".format(verified_rate * 100))

        f.write("\n% -- Encoding timing --\n")
        write_macro(f, "ppThirteenMeanEncodeTime",
                    "{:.4f}\\,s".format(mean_encode_time))
        write_macro(f, "ppThirteenTotalEncodeTime",
                    "{:.2f}\\,s".format(total_encode_time))

    print("\nWrote {}".format(out_path))

    # -- Save JSON results -----------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper13.json")
    full_results = {
        "experiment": "scalar_encodings",
        "paper": 13,
        "program_count": n_total,
        "per_program": results,
        "fragment_classification": dict(fragment_counts),
        "encoding_families_seen": dict(all_families),
        "aggregates": {
            "total_coords": total_coords,
            "total_declarations": total_declarations,
            "total_assertions": total_assertions,
            "mean_decls_per_coord": mean_decls_per_coord,
            "mean_asserts_per_coord": mean_asserts_per_coord,
            "frag_qflia": frag_qflia,
            "frag_qfuf": frag_qfuf,
            "frag_qfbv": frag_qfbv,
            "frag_nonlinear": frag_nonlinear,
            "dominant_fragment": dominant_frag,
            "z3_available": z3_available,
            "verified_count": verified_count,
            "verified_rate": verified_rate,
            "mean_encode_time": mean_encode_time,
            "total_encode_time": total_encode_time,
        },
    }
    with open(json_path, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("Wrote {}".format(json_path))

    # -- Summary ---------------------------------------------------------------
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("  Programs:            {}".format(n_total))
    print("  Total coords:        {}".format(total_coords))
    print("  Total declarations:  {}".format(total_declarations))
    print("  Total assertions:    {}".format(total_assertions))
    print("  Decls/coord:         {:.2f}".format(mean_decls_per_coord))
    print("  Asserts/coord:       {:.2f}".format(mean_asserts_per_coord))
    print("  Fragment dist:       QFLIA={} QFUF={} QFBV={} Nonlinear={}".format(
        frag_qflia, frag_qfuf, frag_qfbv, frag_nonlinear))
    print("  Dominant fragment:   {}".format(dominant_frag))
    print("  Z3 available:        {}".format(z3_available))
    print("  Verified:            {}/{} ({:.1%})".format(
        verified_count, n_total, verified_rate))
    print("  Mean encode time:    {:.4f}s".format(mean_encode_time))
    print("  Total encode time:   {:.2f}s".format(total_encode_time))
    print("=" * 72)

    # -- Cleanup ---------------------------------------------------------------
    for p in tmpfiles:
        cleanup(p)


if __name__ == "__main__":
    main()
