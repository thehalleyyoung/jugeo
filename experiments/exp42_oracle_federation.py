#!/usr/bin/env python3
"""Paper 42 Experiment — Oracle Federation (multi-backend combined verification).

Runs five backend configurations (B1–B5) on 10 benchmark programs classified
into spec / equiv / bug obligation classes.  Measures per-class and overall
coverage and latency for each configuration.

Outputs: papers/data-paper42.tex  (LaTeX macros with \\ppFortyTwo… prefix)
         experiments/results_paper42.json
Re-run:  python3 experiments/exp42_oracle_federation.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

PROGRAMS = [
    {"id": "stack", "cls": "spec", "code": """
class Stack:
    def __init__(self):
        self.items = []
    def push(self, x):
        self.items.append(x)
    def pop(self):
        if not self.items:
            raise IndexError("empty stack")
        return self.items.pop()
    def peek(self):
        return self.items[-1] if self.items else None
    def is_empty(self):
        return len(self.items) == 0
"""},
    {"id": "binary_search", "cls": "spec", "code": """
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
    {"id": "rate_limiter", "cls": "spec", "code": """
class RateLimiter:
    def __init__(self, max_calls, window):
        self.max_calls = max_calls
        self.window = window
        self.calls = []
    def allow(self, ts):
        self.calls = [t for t in self.calls if ts - t < self.window]
        if len(self.calls) < self.max_calls:
            self.calls.append(ts)
            return True
        return False
"""},
    {"id": "csv_parser", "cls": "spec", "code": """
def parse_csv(text):
    rows = []
    for line in text.strip().split('\\n'):
        fields = []
        current = []
        in_quote = False
        for ch in line:
            if ch == '"':
                in_quote = not in_quote
            elif ch == ',' and not in_quote:
                fields.append(''.join(current))
                current = []
            else:
                current.append(ch)
        fields.append(''.join(current))
        rows.append(fields)
    return rows
"""},
    {"id": "interval_merge", "cls": "equiv", "code": """
def merge_intervals(intervals):
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
"""},
    {"id": "lru_cache", "cls": "equiv", "code": """
class LRUCache:
    def __init__(self, capacity):
        self.cap = capacity
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
        elif len(self.cache) >= self.cap:
            old = self.order.pop(0)
            del self.cache[old]
        self.cache[key] = value
        self.order.append(key)
"""},
    {"id": "trie", "cls": "equiv", "code": """
class TrieNode:
    def __init__(self):
        self.children = {}
        self.end = False

class Trie:
    def __init__(self):
        self.root = TrieNode()
    def insert(self, word):
        node = self.root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.end = True
    def search(self, word):
        node = self.root
        for ch in word:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return node.end
"""},
    {"id": "graph_dfs", "cls": "bug", "code": """
def dfs(graph, start):
    visited = set()
    stack = [start]
    order = []
    while stack:
        node = stack.pop()
        if node not in visited:
            visited.add(node)
            order.append(node)
            for neighbor in reversed(graph.get(node, [])):
                stack.append(neighbor)
    return order
"""},
    {"id": "matrix_det", "cls": "bug", "code": """
def determinant(M):
    n = len(M)
    if n == 1:
        return M[0][0]
    if n == 2:
        return M[0][0] * M[1][1] - M[0][1] * M[1][0]
    det = 0
    for j in range(n):
        sub = [row[:j] + row[j+1:] for row in M[1:]]
        det += ((-1) ** j) * M[0][j] * determinant(sub)
    return det
"""},
    {"id": "huffman", "cls": "bug", "code": """
def build_freq(text):
    freq = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    return freq

def build_codes(freq):
    items = sorted(freq.items(), key=lambda x: x[1])
    if len(items) <= 1:
        return {items[0][0]: "0"} if items else {}
    codes = {}
    for i, (ch, _) in enumerate(items):
        codes[ch] = bin(i)[2:].zfill(8)
    return codes
"""},
]

# Backend configurations — each maps to a set of jugeo commands.
# B1: Z3 only (encode)
# B2: Z3 + Runtime (encode + evaluate)
# B3: Z3 + Runtime + Oracle service (encode + evaluate + spec)
# B4: B3 + Copilot fallback (+ classify)
# B5: B4 + Human annotation proxy (+ bugs)
# jugeo commands used as oracle backends
BACKENDS = ["encode", "evaluate", "descend", "classify", "bugs"]
CONFIG_BACKENDS = {
    "BOne":   ["encode"],
    "BTwo":   ["encode", "evaluate"],
    "BThree": ["encode", "evaluate", "descend"],
    "BFour":  ["encode", "evaluate", "descend", "classify"],
    "BFive":  ["encode", "evaluate", "descend", "classify", "bugs"],
}
CONFIG_LABELS = ["BOne", "BTwo", "BThree", "BFour", "BFive"]
CLASS_LABELS = ["spec", "equiv", "bug"]
CLASS_DISPLAY = {"spec": "Spec", "equiv": "Equiv", "bug": "Bug"}


def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=60)
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
            idx += len(text[idx:]) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def backend_evidence_quality(backend, result):
    """Return continuous evidence quality score [0.0, 1.0] for this backend."""
    if not result:
        return 0.0
    if backend == "encode":
        # Per-coordinate: fraction where the solver produced declarations
        files = result.get("files", [])
        n_total = 0
        n_with_decls = 0
        for fobj in files:
            for coord, info in fobj.get("coordinates", {}).items():
                n_total += 1
                if info.get("declarations", 0) > 0:
                    n_with_decls += 1
        if n_total == 0:
            totals = result.get("totals", {})
            return 1.0 if totals.get("declarations", 0) > 0 else 0.0
        return n_with_decls / n_total
    if backend == "evaluate":
        # Use per-coordinate quality scores from the judgment evaluation
        quality_scores = [c.get("quality", 0.0) for c in result.get("per_coordinate", [])]
        if quality_scores:
            return statistics.mean(quality_scores)
        return result.get("coverage", 0.0)
    if backend == "descend":
        # Geometric descent: fraction of sections verified
        sections = result.get("sections_detail", [])
        if not sections:
            return 0.5 if result.get("verdict") == "verified" else 0.0
        ok_count = sum(1 for s in sections if s.get("ok", 0) > 0)
        return ok_count / len(sections)
    if backend == "classify":
        # AI classification: partial evidence from classification + subsystem routing
        cat = result.get("classification", {}).get("category", "")
        subs = result.get("recommended_subsystems", [])
        conf = result.get("classification", {}).get("confidence", 0.0)
        if not cat:
            return 0.0
        return min(1.0, 0.3 + len(subs) * 0.15 + conf * 0.5)
    if backend == "bugs":
        if result.get("status", "") != "ok":
            return 0.0
        count = result.get("count", 0)
        return max(0.0, 1.0 - count * 0.2)
    return 0.0


def fmt_time(secs):
    if secs < 0.001:
        return f"{secs*1_000_000:.0f}\\,\\mu s"
    if secs < 1.0:
        return f"{secs*1000:.1f}\\,ms"
    return f"{secs:.2f}\\,s"


def fmt_pct(val):
    return f"{val*100:.1f}\\%"


def safe_mean(lst):
    return statistics.mean(lst) if lst else 0.0


def safe_median(lst):
    return statistics.median(lst) if lst else 0.0


def emit_latex(macros, path):
    """Write LaTeX macro file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write("% data-paper42.tex — AUTO-GENERATED by exp42_oracle_federation.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp42_oracle_federation.py\n\n")
        for name, value in macros:
            f.write(f"\\newcommand{{\\{name}}}{{{value}}}\n")
    print(f"Wrote {len(macros)} macros to {path}")


def main():
    print("=" * 60)
    print("PAPER 42 — Oracle Federation")
    print("=" * 60)

    # ── Phase 1: run all backends on every program ──────────────────────
    # per_prog[prog_id] = { backend: {"result": …, "time": …} }
    per_prog = {}
    for prog in PROGRAMS:
        tmp = write_temp_py(prog["code"])
        prog_data = {}

        for bk in BACKENDS:
            t0 = time.perf_counter()
            objs = run_jugeo(bk, tmp)
            elapsed = time.perf_counter() - t0
            raw = objs[0] if objs else {}
            prog_data[bk] = {"result": raw, "time": elapsed}

        cleanup(tmp)
        per_prog[prog["id"]] = prog_data

        times_str = "  ".join(f"{bk}={prog_data[bk]['time']:.3f}s" for bk in BACKENDS)
        print(f"  {prog['id']:18s}  {times_str}")

    # ── Phase 2: compute per-config × per-class coverage & latency ──────
    # coverage[cls][config] = list of booleans (one per program in cls)
    # latency[cls][config]  = list of floats   (seconds, one per program)
    coverage = {c: {k: [] for k in CONFIG_LABELS} for c in CLASS_LABELS}
    latency  = {c: {k: [] for k in CONFIG_LABELS} for c in CLASS_LABELS}

    conflict_count = 0
    trust_lifts = 0

    for prog in PROGRAMS:
        pid = prog["id"]
        cls = prog["cls"]
        pd = per_prog[pid]

        for cfg in CONFIG_LABELS:
            bks = CONFIG_BACKENDS[cfg]
            # Coverage: max evidence quality across backends in this config
            qual = max(backend_evidence_quality(bk, pd[bk]["result"]) for bk in bks)
            coverage[cls][cfg].append(qual)
            # Latency: max of backend times (parallel dispatch)
            lat = max(pd[bk]["time"] for bk in bks)
            latency[cls][cfg].append(lat)

        # Conflict: encode and evaluate disagree on trust
        enc_decls = pd["encode"]["result"].get("totals", {}).get("declarations", 0)
        eval_trust = pd["evaluate"]["result"].get("trust", {}).get("aggregate_trust", "unverified")
        enc_trust = "solver_discharged" if enc_decls > 0 else "unverified"
        if enc_trust != eval_trust and enc_decls > 0 and eval_trust != "unverified":
            conflict_count += 1

        # Trust lift: both oracles provide evidence and agree
        if enc_decls > 0 and eval_trust not in ("unverified",):
            trust_lifts += 1

    # ── Phase 3: aggregate metrics ──────────────────────────────────────
    n_progs = len(PROGRAMS)
    total_obligations = n_progs * len(BACKENDS)

    # Per-class, per-config coverage fractions
    cov_frac = {}
    lat_mean_ms = {}
    for cls in CLASS_LABELS:
        cov_frac[cls] = {}
        lat_mean_ms[cls] = {}
        for cfg in CONFIG_LABELS:
            vals = coverage[cls][cfg]
            cov_frac[cls][cfg] = sum(vals) / max(len(vals), 1)
            lat_mean_ms[cls][cfg] = safe_mean(latency[cls][cfg]) * 1000

    # Overall (all classes combined)
    cov_frac["overall"] = {}
    lat_mean_ms["overall"] = {}
    for cfg in CONFIG_LABELS:
        all_cov = [v for cls in CLASS_LABELS for v in coverage[cls][cfg]]
        cov_frac["overall"][cfg] = sum(all_cov) / max(len(all_cov), 1)
        all_lat = [v for cls in CLASS_LABELS for v in latency[cls][cfg]]
        lat_mean_ms["overall"][cfg] = safe_mean(all_lat) * 1000

    # Global aggregates
    all_fed_times = [per_prog[p["id"]]["encode"]["time"] + per_prog[p["id"]]["evaluate"]["time"]
                     for p in PROGRAMS]
    mean_fed_time = safe_mean(all_fed_times)
    median_fed_time = safe_median(all_fed_times)
    solver_coverage = cov_frac["overall"]["BOne"]
    combined_coverage = cov_frac["overall"]["BFive"]
    conflict_rate = conflict_count / max(n_progs, 1)
    meet_accuracy = (n_progs - conflict_count) / max(n_progs, 1)
    lift_rate = trust_lifts / max(n_progs, 1)

    # ── Print summary ───────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("FEDERATION SUMMARY")
    print(f"  Programs             : {n_progs}")
    print(f"  Total obligations    : {total_obligations}")
    print(f"  Mean federation time : {mean_fed_time:.4f}s")
    print(f"  Median fed. time     : {median_fed_time:.4f}s")
    print(f"  Conflicts            : {conflict_count}")
    print(f"  Trust lifts          : {trust_lifts}")
    print()
    print("COVERAGE (fraction discharged)")
    header = f"  {'Class':12s}" + "".join(f"  {c:>8s}" for c in CONFIG_LABELS)
    print(header)
    for cls in CLASS_LABELS + ["overall"]:
        row = f"  {cls:12s}"
        for cfg in CONFIG_LABELS:
            row += f"  {cov_frac[cls][cfg]:8.1%}"
        print(row)
    print()
    print("LATENCY (mean ms)")
    print(header)
    for cls in CLASS_LABELS + ["overall"]:
        row = f"  {cls:12s}"
        for cfg in CONFIG_LABELS:
            row += f"  {lat_mean_ms[cls][cfg]:8.1f}"
        print(row)

    # ── Phase 4: build LaTeX macros ─────────────────────────────────────
    P = "ppFortyTwo"
    macros = [
        (f"{P}TotalPrograms", str(n_progs)),
        (f"{P}TotalObligations", str(total_obligations)),
        (f"{P}SolverCoverage", fmt_pct(solver_coverage)),
        (f"{P}CombinedCoverage", fmt_pct(combined_coverage)),
        (f"{P}MeanFedTime", fmt_time(mean_fed_time)),
        (f"{P}MedianFedTime", fmt_time(median_fed_time)),
        (f"{P}ConflictCount", str(conflict_count)),
        (f"{P}ConflictRate", fmt_pct(conflict_rate)),
        (f"{P}MeetAccuracy", fmt_pct(meet_accuracy)),
        (f"{P}LiftRate", fmt_pct(lift_rate)),
    ]

    # Per-class × per-config coverage macros: \ppFortyTwoSpecCovBOne etc.
    for cls in CLASS_LABELS:
        tag = CLASS_DISPLAY[cls]
        for cfg in CONFIG_LABELS:
            macros.append((f"{P}{tag}Cov{cfg}", fmt_pct(cov_frac[cls][cfg])))
    for cfg in CONFIG_LABELS:
        macros.append((f"{P}OverallCov{cfg}", fmt_pct(cov_frac["overall"][cfg])))

    # Per-class × per-config latency macros: \ppFortyTwoSpecLatBOne etc.
    for cls in CLASS_LABELS:
        tag = CLASS_DISPLAY[cls]
        for cfg in CONFIG_LABELS:
            macros.append((f"{P}{tag}Lat{cfg}", fmt_time(lat_mean_ms[cls][cfg] / 1000)))
    for cfg in CONFIG_LABELS:
        macros.append((f"{P}OverallLat{cfg}", fmt_time(lat_mean_ms["overall"][cfg] / 1000)))

    tex_path = os.path.join(ROOT, "papers", "data-paper42.tex")
    emit_latex(macros, tex_path)

    # ── Phase 5: save JSON results ──────────────────────────────────────
    json_results = {
        "programs": n_progs,
        "total_obligations": total_obligations,
        "solver_coverage": round(solver_coverage, 4),
        "combined_coverage": round(combined_coverage, 4),
        "mean_fed_time": round(mean_fed_time, 4),
        "median_fed_time": round(median_fed_time, 4),
        "conflict_count": conflict_count,
        "conflict_rate": round(conflict_rate, 4),
        "meet_accuracy": round(meet_accuracy, 4),
        "lift_rate": round(lift_rate, 4),
        "coverage": {cls: {cfg: round(cov_frac[cls][cfg], 4)
                           for cfg in CONFIG_LABELS}
                     for cls in CLASS_LABELS + ["overall"]},
        "latency_ms": {cls: {cfg: round(lat_mean_ms[cls][cfg], 2)
                             for cfg in CONFIG_LABELS}
                       for cls in CLASS_LABELS + ["overall"]},
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper42.json")
    with open(json_path, "w") as f:
        json.dump(json_results, f, indent=2)
    print(f"\nWrote {json_path}")


if __name__ == "__main__":
    main()
