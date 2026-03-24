#!/usr/bin/env python3
"""Paper 37 Experiment — Pack Federation (distributed verification, bridge theorems).

Splits programs into packs, discovers bridges, and checks sheaf conditions.

Outputs: papers/data-paper37.tex  (LaTeX macros with \\ppXXXVII… prefix)
Re-run:  python3 experiments/exp37_pack_federation.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

# Multi-function programs to split into packs
PROGRAMS = [
    {"id": "math_utils", "code": """
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    return abs(a * b) // gcd(a, b)

def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True
"""},
    {"id": "sort_utils", "code": """
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(n - i - 1):
            if arr[j] > arr[j+1]:
                arr[j], arr[j+1] = arr[j+1], arr[j]
    return arr

def is_sorted(arr):
    return all(arr[i] <= arr[i+1] for i in range(len(arr)-1))

def merge(a, b):
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            result.append(a[i]); i += 1
        else:
            result.append(b[j]); j += 1
    result.extend(a[i:])
    result.extend(b[j:])
    return result
"""},
    {"id": "string_utils", "code": """
def reverse(s):
    return s[::-1]

def is_palindrome(s):
    s = s.lower()
    return s == reverse(s)

def char_count(s):
    d = {}
    for c in s:
        d[c] = d.get(c, 0) + 1
    return d
"""},
    {"id": "collection_ops", "code": """
def flatten(lst):
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result

def unique(lst):
    seen = set()
    result = []
    for x in lst:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result

def chunk(lst, size):
    return [lst[i:i+size] for i in range(0, len(lst), size)]
"""},
    {"id": "search_ops", "code": """
def linear_search(arr, target):
    for i, x in enumerate(arr):
        if x == target:
            return i
    return -1

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
    {"id": "stat_ops", "code": """
def mean(xs):
    return sum(xs) / len(xs)

def variance(xs):
    m = mean(xs)
    return sum((x - m)**2 for x in xs) / len(xs)

def std_dev(xs):
    return variance(xs) ** 0.5
"""},
    {"id": "tree_ops", "code": """
class TreeNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None

def inorder(node):
    if node is None:
        return []
    return inorder(node.left) + [node.val] + inorder(node.right)

def height(node):
    if node is None:
        return 0
    return 1 + max(height(node.left), height(node.right))
"""},
    {"id": "io_utils", "code": """
def parse_csv_line(line):
    return line.strip().split(',')

def format_table(rows):
    if not rows:
        return ''
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for row in rows:
        parts = [str(row[i]).ljust(widths[i]) for i in range(len(row))]
        lines.append(' | '.join(parts))
    return '\\n'.join(lines)
"""},
]

# Pairs for bridge checking
PAIRS = [
    ("math_utils", "sort_utils"),
    ("math_utils", "string_utils"),
    ("sort_utils", "collection_ops"),
    ("collection_ops", "search_ops"),
    ("stat_ops", "math_utils"),
    ("tree_ops", "collection_ops"),
]


def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=30)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = decoder.raw_decode(remaining)
            return obj
        except json.JSONDecodeError:
            break
    return {}


def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def main():
    from jugeo.encodings.pack_federation import compute_sheaf_condition
    from jugeo.encodings.pack_federation.models import BridgeTheoremEncoding
    from jugeo.maturity import CyclicSystemCoordinator

    # Per-pack analysis: run encode, descend, and bugs for each program
    pack_data = {}
    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])

        t0 = time.perf_counter()
        enc = run_jugeo("encode", tmp)
        encode_wall = time.perf_counter() - t0
        if isinstance(enc, list):
            enc = enc[0] if enc else {}

        t0 = time.perf_counter()
        desc = run_jugeo("descend", tmp)
        descend_wall = time.perf_counter() - t0
        if isinstance(desc, list):
            desc = desc[0] if desc else {}

        t0 = time.perf_counter()
        bugs_out = run_jugeo("bugs", tmp)
        bugs_wall = time.perf_counter() - t0
        if isinstance(bugs_out, list):
            bugs_out = bugs_out[0] if bugs_out else {}

        t0 = time.perf_counter()
        coord = CyclicSystemCoordinator.create(prog["id"])
        record, _ = coord.run_full_cycle({"source": prog["code"]})
        coord_wall = time.perf_counter() - t0

        n_coords = enc.get("totals", {}).get("coordinates", 0)
        n_declarations = enc.get("totals", {}).get("declarations", 0)
        n_assertions = enc.get("totals", {}).get("assertions", 0)
        sections = desc.get("local_sections", 0)
        verdict = desc.get("verdict", "unknown")
        obstruction_list = desc.get("obstructions", [])
        n_obstructions = len(obstruction_list) if isinstance(obstruction_list, list) else 0
        overlap_checked = desc.get("overlap_conditions_checked", 0)
        eff_ratio = desc.get("effective_descent", {}).get("effectiveness_ratio", 0.0)
        bug_count = bugs_out.get("count", 0)
        if bug_count == 0 and isinstance(bugs_out.get("bugs"), list):
            bug_count = len(bugs_out["bugs"])
        bug_obstruction_count = bugs_out.get("obstruction_count", 0)
        trust_score = record.trust_score
        n_functions = len([l for l in prog["code"].splitlines()
                          if l.strip().startswith("def ")])
        n_lines = len(prog["code"].strip().splitlines())

        # Evidence dict derived from actual JuGeo analysis.
        # All packs share the same coordinate keys so bridges can compare them.
        evidence = {
            "coord_count": n_coords,
            "declaration_count": n_declarations,
            "assertion_count": n_assertions,
            "section_count": sections,
            "overlap_checked": overlap_checked,
            "effectiveness_pct": round(eff_ratio * 100),
            "verified": 1 if verdict == "verified" else 0,
            "obstruction_count": n_obstructions,
            "bug_count": bug_count,
            "bug_obstruction_count": bug_obstruction_count,
            "trust_pct": round(trust_score * 100),
            "function_count": n_functions,
            "code_lines": n_lines,
        }

        pack_data[prog["id"]] = {
            "coords": n_coords,
            "sections": sections,
            "verified": verdict == "verified",
            "n_obstructions": n_obstructions,
            "bug_count": bug_count,
            "trust_score": trust_score,
            "coord_wall": coord_wall,
            "evidence": evidence,
        }

        cleanup(tmp)
        print(f"  pack {prog['id']:18s}  coords={n_coords}  decls={n_declarations}  "
              f"asserts={n_assertions}  sections={sections}  verdict={verdict}  "
              f"bugs={bug_count}  funcs={n_functions}  lines={n_lines}")

    # Bridge discovery between pairs using actual BridgeTheoremEncoding objects
    pair_results = []
    for p1, p2 in PAIRS:
        d1 = pack_data[p1]
        d2 = pack_data[p2]
        ev1 = d1["evidence"]
        ev2 = d2["evidence"]

        # Create one bridge per shared coordinate key
        coord_keys = sorted(set(ev1.keys()) & set(ev2.keys()))

        t0 = time.perf_counter()
        bridges = []
        for i, key in enumerate(coord_keys):
            bridges.append(BridgeTheoremEncoding(
                bridge_id=f"br_{p1}_{p2}_{i}",
                source_pack_id=p1,
                target_pack_id=p2,
                overlap_region=frozenset({key}),
                source_formula=str(ev1[key]),
                target_formula=str(ev2[key]),
                trust_ceiling=min(d1["trust_score"], d2["trust_score"]),
                morphism_type="bijective" if ev1[key] == ev2[key] else "injective",
            ))

        total_bridges = max(len(bridges), 1)
        exported = len(coord_keys)

        # Check sheaf condition with real bridges
        packs_dict = {p1: ev1, p2: ev2}
        sheaf_ok, violations = compute_sheaf_condition(packs_dict, bridges)
        bridge_wall = time.perf_counter() - t0

        valid_count = total_bridges - len(violations)
        valid_pct = round(valid_count / total_bridges * 100, 1)

        # Discovery time: in-process coordination + bridge construction + sheaf check
        disc_time_ms = round(
            (d1["coord_wall"] + d2["coord_wall"] + bridge_wall) * 1000, 1)

        pair_results.append({
            "pair": f"{p1}--{p2}",
            "exported": exported,
            "bridges": total_bridges,
            "valid_pct": valid_pct,
            "disc_time_ms": disc_time_ms,
        })

        print(f"  {p1:18s}--{p2:18s}  exported={exported}  bridges={total_bridges}  "
              f"valid={valid_pct:.1f}%  violations={len(violations)}  "
              f"time={disc_time_ms}ms")

    # Aggregates
    total_bridges = sum(r["bridges"] for r in pair_results)
    total_valid = sum(int(r["bridges"] * r["valid_pct"] / 100) for r in pair_results)
    overall_valid_pct = round(total_valid / total_bridges * 100, 1) if total_bridges else 0
    mean_disc_ms = round(statistics.mean([r["disc_time_ms"] for r in pair_results]), 1)

    print("\n" + "=" * 60)
    print("FEDERATION SUMMARY")
    print(f"  Pairs:   {len(pair_results)}")
    print(f"  Bridges: {total_bridges} (valid: {total_valid}, {overall_valid_pct}%)")
    print(f"  Mean discovery time: {mean_disc_ms}ms")

    # Generate LaTeX
    P = "ppXXXVII"
    tex = [
        f"% data-paper37.tex — AUTO-GENERATED by exp37_pack_federation.py",
        f"% DO NOT EDIT — regenerate with: python3 experiments/exp37_pack_federation.py",
        f"",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    # Per-pair macros (first 3 pairs match paper table: alpha-beta, alpha-gamma, beta-gamma)
    pair_labels = ["AlphaBeta", "AlphaGamma", "BetaGamma"]
    for i, label in enumerate(pair_labels):
        if i < len(pair_results):
            r = pair_results[i]
            m(f"{label}ValidPct", f"{r['valid_pct']}\\%")
            m(f"{label}DiscTime", f"{r['disc_time_ms']}\\,ms")

    m("TotalBridges", total_bridges)
    m("TotalValid", total_valid)
    m("OverallValidPct", f"{overall_valid_pct}\\%")
    m("MeanDiscTime", f"{mean_disc_ms}\\,ms")
    m("TotalPacks", len(PROGRAMS))
    m("TotalPairs", len(pair_results))

    tex_path = os.path.join(ROOT, "papers", "data-paper37.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper37.json")
    with open(json_path, "w") as f:
        json.dump({"pairs": pair_results, "total_bridges": total_bridges}, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
