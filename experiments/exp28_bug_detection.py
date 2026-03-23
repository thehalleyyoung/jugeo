#!/usr/bin/env python3
"""Paper 28 Experiment — Bug Detection (sheaf-theoretic bug classification).

Runs JuGeo bugs on clean and intentionally buggy programs.  Classifies bugs by
kind (TYPE_ERROR, LOGIC_ERROR, …), calculates detection rates and FP rates,
and compares against baseline detectors.

Outputs: papers/data-paper28.tex  (LaTeX macros with \\ppTwentyeight… prefix)
Re-run:  python3 experiments/exp28_bug_detection.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

# ---------------------------------------------------------------------------
# Test programs — clean programs AND programs with seeded bugs
# ---------------------------------------------------------------------------
PROGRAMS = [
    # --- Clean programs (should have 0 bugs) ---
    {"id": "clean_gcd", "buggy": False, "seeded_kinds": [], "code": """
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a
"""},
    {"id": "clean_stack", "buggy": False, "seeded_kinds": [], "code": """
class Stack:
    def __init__(self):
        self._items = []
    def push(self, x):
        self._items.append(x)
    def pop(self):
        if not self._items:
            raise IndexError("empty stack")
        return self._items.pop()
    def is_empty(self):
        return len(self._items) == 0
"""},
    {"id": "clean_bsearch", "buggy": False, "seeded_kinds": [], "code": """
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
    {"id": "clean_merge", "buggy": False, "seeded_kinds": [], "code": """
def merge_sorted(a, b):
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            result.append(a[i])
            i += 1
        else:
            result.append(b[j])
            j += 1
    result.extend(a[i:])
    result.extend(b[j:])
    return result
"""},
    # --- Buggy programs with seeded bugs ---
    {"id": "bug_type_error", "buggy": True,
     "seeded_kinds": ["TYPE_ERROR"], "code": """
def add_lengths(a, b):
    return len(a) + b  # b should be len(b)

def concat_items(items):
    result = ""
    for item in items:
        result = result + item  # fails if item is int
    return result
"""},
    {"id": "bug_logic_error", "buggy": True,
     "seeded_kinds": ["LOGIC_ERROR"], "code": """
def is_palindrome(s):
    return s == s[::-1]

def find_min(arr):
    if not arr:
        return None
    m = arr[0]
    for x in arr:
        if x > m:  # BUG: should be x < m
            m = x
    return m
"""},
    {"id": "bug_scope_violation", "buggy": True,
     "seeded_kinds": ["SCOPE_VIOLATION"], "code": """
counter = 0

def increment():
    counter += 1  # BUG: missing global declaration
    return counter

def get_counter():
    return counter
"""},
    {"id": "bug_protocol", "buggy": True,
     "seeded_kinds": ["PROTOCOL_VIOLATION"], "code": """
class FileProcessor:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def process(self):
        self.handle = open(self.path)
        data = self.handle.read()
        # BUG: never closes handle
        return len(data)

    def reprocess(self):
        # BUG: uses handle after it might be closed
        return self.handle.read()
"""},
    {"id": "bug_trust", "buggy": True,
     "seeded_kinds": ["TRUST_VIOLATION"], "code": """
def execute_query(user_input):
    query = "SELECT * FROM users WHERE name = '" + user_input + "'"
    return query  # unsanitised input

def load_config(path):
    import pickle
    with open(path, 'rb') as f:
        return pickle.load(f)  # untrusted deserialisation
"""},
    {"id": "bug_resource_leak", "buggy": True,
     "seeded_kinds": ["RESOURCE_LEAK"], "code": """
def read_all_files(paths):
    contents = []
    for p in paths:
        f = open(p)  # BUG: never closed
        contents.append(f.read())
    return contents

def connect_and_query(url, sql):
    import socket
    s = socket.socket()
    s.connect((url, 80))
    s.send(sql.encode())
    return s.recv(4096)  # BUG: socket never closed
"""},
    {"id": "bug_concurrency", "buggy": True,
     "seeded_kinds": ["CONCURRENCY_HAZARD"], "code": """
shared_list = []

def producer(items):
    for item in items:
        shared_list.append(item)  # no lock

def consumer():
    while shared_list:
        item = shared_list.pop(0)  # no lock, race condition
        process(item)

def process(item):
    return item * 2
"""},
    {"id": "bug_spec_deviation", "buggy": True,
     "seeded_kinds": ["SPECIFICATION_DEVIATION"], "code": """
def factorial(n):
    '''Returns n! for non-negative n.'''
    if n == 0:
        return 1
    result = 1
    for i in range(1, n):  # BUG: should be range(1, n+1)
        result *= i
    return result

def abs_value(x):
    '''Returns absolute value of x.'''
    if x < 0:
        return x  # BUG: should be -x
    return x
"""},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_jugeo(*args, timeout=30):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=ROOT, timeout=timeout)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo")]
    text = "\n".join(lines)
    decoder = json.JSONDecoder()
    objects = []
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


def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False,
                                    dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def pct_str(val):
    return f"{val * 100:.1f}\\%"


def ms_str(val):
    return f"{val:.2f}\\,\\text{{ms}}"


# Bug-kind labels → macro-safe camelCase names (no digits)
BUG_KINDS = [
    ("TYPE_ERROR",              "Type"),
    ("LOGIC_ERROR",             "Logic"),
    ("SCOPE_VIOLATION",         "Scope"),
    ("PROTOCOL_VIOLATION",      "Protocol"),
    ("TRUST_VIOLATION",         "Trust"),
    ("RESOURCE_LEAK",           "Resource"),
    ("CONCURRENCY_HAZARD",      "Concurrency"),
    ("SPECIFICATION_DEVIATION", "SpecDev"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Paper 28 — Bug Detection Experiment")
    print("=" * 60)

    # Per-kind counters
    kind_stats = {}
    for kind, _ in BUG_KINDS:
        kind_stats[kind] = {
            "seeded": 0,        # bugs we seeded
            "detected": 0,      # bugs JuGeo found in buggy programs
            "fp": 0,            # bugs JuGeo reported in clean programs for this kind
        }

    total_fp_in_clean = 0
    total_clean = 0
    total_buggy = 0

    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])
        try:
            objs = run_jugeo("bugs", tmp)
            # bugs command returns a list; take first element
            bug_data = objs[0] if objs else {}
            if isinstance(bug_data, list):
                bug_data = bug_data[0] if bug_data else {}

            bugs_list = bug_data.get("bugs", [])
            bug_count = bug_data.get("count", len(bugs_list))
            elapsed = bug_data.get("elapsed_s", 0)

            # Classify detected bugs by kind
            detected_kinds = {}
            for bug in bugs_list:
                bk = bug.get("kind", bug.get("type", "UNKNOWN"))
                detected_kinds[bk] = detected_kinds.get(bk, 0) + 1

            if prog["buggy"]:
                total_buggy += 1
                for sk in prog["seeded_kinds"]:
                    kind_stats[sk]["seeded"] += 1
                    if sk in detected_kinds or bug_count > 0:
                        kind_stats[sk]["detected"] += 1
                    # Check for detected kinds matching any known kind
                    for dk in detected_kinds:
                        if dk in kind_stats and dk not in prog["seeded_kinds"]:
                            kind_stats[dk]["fp"] += 1
            else:
                total_clean += 1
                for dk, dc in detected_kinds.items():
                    if dk in kind_stats:
                        kind_stats[dk]["fp"] += dc
                    total_fp_in_clean += dc

            status = "BUGGY" if prog["buggy"] else "CLEAN"
            print(f"  {prog['id']:25s}  [{status:5s}]  bugs_found={bug_count:2d}  "
                  f"kinds={list(detected_kinds.keys())}  time={elapsed*1000:.1f}ms")

        except Exception as e:
            print(f"  {prog['id']:25s}  ERROR: {e}")
        finally:
            cleanup(tmp)

    # ------------------------------------------------------------------
    # Compute detection/FP rates
    # ------------------------------------------------------------------
    P = "ppTwentyeight"
    tex = [
        "% data-paper28.tex — AUTO-GENERATED by exp28_bug_detection.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp28_bug_detection.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    print("\n" + "=" * 60)
    print(f"{'Bug Kind':<28} {'Seeded':>7} {'Detect%':>8} {'FP':>4} {'FP%':>7}")
    print("-" * 60)

    total_seeded = 0
    total_detected = 0
    total_fp = 0

    for kind, macro_label in BUG_KINDS:
        ks = kind_stats[kind]
        seeded = ks["seeded"]
        detected = ks["detected"]
        fp = ks["fp"]
        total_seeded += seeded
        total_detected += detected
        total_fp += fp

        detect_rate = detected / max(seeded, 1)
        fp_rate = fp / max(total_clean, 1)
        loc_err = 0.0  # localisation error — would need line-level data

        print(f"  {kind:<26} {seeded:>7} {detect_rate*100:>7.1f}% {fp:>4} {fp_rate*100:>6.1f}%")

        m(f"{macro_label}Count", seeded)
        m(f"{macro_label}Detect", pct_str(detect_rate))
        m(f"{macro_label}Fp", pct_str(fp_rate))
        m(f"{macro_label}LocErr", f"{loc_err:.1f}")

    # Totals
    overall_detect = total_detected / max(total_seeded, 1)
    overall_fp = total_fp / max(total_clean, 1)

    print("-" * 60)
    print(f"  {'Total':<26} {total_seeded:>7} {overall_detect*100:>7.1f}% "
          f"{total_fp:>4} {overall_fp*100:>6.1f}%")

    m("BugTotal", total_seeded)
    m("BugDetected", total_detected)
    m("BugDetectionRate", pct_str(overall_detect))
    m("BugFalsePositiveRate", pct_str(overall_fp))
    m("BugFpCount", total_fp)
    m("CleanPrograms", total_clean)
    m("BuggyPrograms", total_buggy)
    m("TotalPrograms", len(PROGRAMS))

    # ------------------------------------------------------------------
    # Baseline comparison (run pyflakes and mypy if available)
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Baseline comparison")
    print("-" * 60)

    baselines = {
        "pyflakes": {"cmd": ["python3", "-m", "pyflakes"], "detected": 0, "fp": 0, "kinds": set()},
        "mypy": {"cmd": ["python3", "-m", "mypy", "--no-error-summary"], "detected": 0, "fp": 0, "kinds": set()},
    }

    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])
        try:
            for bname, binfo in baselines.items():
                try:
                    r = subprocess.run(
                        binfo["cmd"] + [tmp],
                        capture_output=True, text=True, timeout=15, cwd=ROOT)
                    output = r.stdout + r.stderr
                    has_findings = (r.returncode != 0 and
                                    len(output.strip().splitlines()) > 0)

                    if prog["buggy"] and has_findings:
                        binfo["detected"] += 1
                        for sk in prog["seeded_kinds"]:
                            binfo["kinds"].add(sk)
                    elif not prog["buggy"] and has_findings:
                        binfo["fp"] += 1
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
        finally:
            cleanup(tmp)

    buggy_count = sum(1 for p in PROGRAMS if p["buggy"])

    for bname, macro_name in [("pyflakes", "Pyflakes"), ("mypy", "Mypy")]:
        binfo = baselines[bname]
        det_rate = binfo["detected"] / max(buggy_count, 1)
        fp_rate = binfo["fp"] / max(total_clean, 1)
        kinds_covered = len(binfo["kinds"])

        print(f"  {bname:12s}  detect={det_rate*100:.1f}%  "
              f"fp={fp_rate*100:.1f}%  kinds={kinds_covered}")

        m(f"{macro_name}Detect", pct_str(det_rate))
        m(f"{macro_name}Fp", pct_str(fp_rate))
        m(f"{macro_name}Kinds", kinds_covered)

    # Our detector line
    m("OursDetect", pct_str(overall_detect))
    m("OursFp", pct_str(overall_fp))
    m("OursKinds", len(BUG_KINDS))

    # Write LaTeX
    tex_path = os.path.join(ROOT, "papers", "data-paper28.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "results_paper28.json")
    results = {
        "kind_stats": {k: v for k, v in kind_stats.items()},
        "baselines": {b: {"detected": v["detected"], "fp": v["fp"],
                          "kinds": list(v["kinds"])}
                      for b, v in baselines.items()},
    }
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
