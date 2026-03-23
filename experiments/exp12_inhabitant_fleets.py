#!/usr/bin/env python3
"""Paper 12 Experiment -- Inhabitant Fleets.

Hypothesis: JuGeo's fleet-like verification pipeline (load → descend →
evaluate → cyclic coordination) converges reliably across diverse programs,
and CyclicSystemCoordinator metrics capture fleet behaviour.

Methodology:
  - jugeo load     → site summary (coordinates, morphisms, covering_families)
  - jugeo descend  → descent verdict, sections, obstructions, trust
  - jugeo evaluate → coverage, trust, cover quality, sheaf check
  - CyclicSystemCoordinator.run_full_cycle → cycle metrics (phases, convergence)

Writes macros to papers/data-paper12.tex with prefix \\ppTwelve.
Re-run: python3 experiments/exp12_inhabitant_fleets.py
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
    "insertion_sort": '''\
def insertion_sort(arr):
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and arr[j] > key:
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return arr

def is_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True

def sort_and_verify(arr):
    result = insertion_sort(list(arr))
    assert is_sorted(result)
    return result
''',

    "hash_map": '''\
class HashMap:
    def __init__(self, capacity=16):
        self.capacity = capacity
        self.buckets = [[] for _ in range(capacity)]
        self._size = 0

    def _hash(self, key):
        return hash(key) % self.capacity

    def put(self, key, value):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.buckets[idx]):
            if k == key:
                self.buckets[idx][i] = (key, value)
                return
        self.buckets[idx].append((key, value))
        self._size += 1

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self.buckets[idx]:
            if k == key:
                return v
        return default

    def contains(self, key):
        return self.get(key) is not None

    def size(self):
        return self._size
''',

    "fibonacci_memo": '''\
def fib_memo(n, memo=None):
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fib_memo(n - 1, memo) + fib_memo(n - 2, memo)
    return memo[n]

def fib_iter(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

def fib_sequence(n):
    return [fib_iter(i) for i in range(n)]
''',

    "priority_queue": '''\
class PriorityQueue:
    def __init__(self):
        self._heap = []

    def push(self, priority, item):
        self._heap.append((priority, item))
        self._sift_up(len(self._heap) - 1)

    def pop(self):
        if not self._heap:
            raise IndexError("pop from empty queue")
        self._swap(0, len(self._heap) - 1)
        item = self._heap.pop()
        if self._heap:
            self._sift_down(0)
        return item[1]

    def _sift_up(self, i):
        while i > 0:
            parent = (i - 1) // 2
            if self._heap[i][0] < self._heap[parent][0]:
                self._swap(i, parent)
                i = parent
            else:
                break

    def _sift_down(self, i):
        n = len(self._heap)
        while 2 * i + 1 < n:
            child = 2 * i + 1
            if child + 1 < n and self._heap[child+1][0] < self._heap[child][0]:
                child += 1
            if self._heap[child][0] < self._heap[i][0]:
                self._swap(i, child)
                i = child
            else:
                break

    def _swap(self, i, j):
        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]

    def is_empty(self):
        return len(self._heap) == 0
''',

    "string_utils": '''\
def is_palindrome(s):
    cleaned = ''.join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]

def caesar_cipher(text, shift):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord('A') if ch.isupper() else ord('a')
            result.append(chr((ord(ch) - base + shift) % 26 + base))
        else:
            result.append(ch)
    return ''.join(result)

def count_vowels(text):
    return sum(1 for c in text.lower() if c in 'aeiou')

def reverse_words(text):
    return ' '.join(text.split()[::-1])
''',

    "event_emitter": '''\
class EventEmitter:
    def __init__(self):
        self._listeners = {}

    def on(self, event, callback):
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    def off(self, event, callback):
        if event in self._listeners:
            self._listeners[event] = [
                cb for cb in self._listeners[event] if cb != callback
            ]

    def emit(self, event, *args, **kwargs):
        results = []
        for cb in self._listeners.get(event, []):
            results.append(cb(*args, **kwargs))
        return results

    def once(self, event, callback):
        def wrapper(*args, **kwargs):
            self.off(event, wrapper)
            return callback(*args, **kwargs)
        self.on(event, wrapper)

    def listener_count(self, event):
        return len(self._listeners.get(event, []))
''',

    "tree_traversal": '''\
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

def postorder(root):
    if root is None:
        return []
    return postorder(root.left) + postorder(root.right) + [root.val]

def tree_height(root):
    if root is None:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))

def level_order(root):
    if root is None:
        return []
    from collections import deque
    q = deque([root])
    result = []
    while q:
        node = q.popleft()
        result.append(node.val)
        if node.left:
            q.append(node.left)
        if node.right:
            q.append(node.right)
    return result
''',

    "validator_chain": '''\
class Validator:
    def __init__(self):
        self._rules = []

    def add_rule(self, name, fn):
        self._rules.append((name, fn))
        return self

    def validate(self, value):
        errors = []
        for name, fn in self._rules:
            if not fn(value):
                errors.append(name)
        return {"valid": len(errors) == 0, "errors": errors}

def make_string_validator(min_len=0, max_len=1000):
    v = Validator()
    v.add_rule("is_string", lambda s: isinstance(s, str))
    v.add_rule("min_length", lambda s: isinstance(s, str) and len(s) >= min_len)
    v.add_rule("max_length", lambda s: isinstance(s, str) and len(s) <= max_len)
    return v

def make_number_validator(min_val=None, max_val=None):
    v = Validator()
    v.add_rule("is_number", lambda n: isinstance(n, (int, float)))
    if min_val is not None:
        v.add_rule("min_value", lambda n: isinstance(n, (int, float)) and n >= min_val)
    if max_val is not None:
        v.add_rule("max_value", lambda n: isinstance(n, (int, float)) and n <= max_val)
    return v
''',

    "rate_limiter": '''\
import time as _time

class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self._timestamps = []

    def allow(self):
        now = _time.time()
        self._timestamps = [
            t for t in self._timestamps
            if now - t < self.window
        ]
        if len(self._timestamps) < self.max_requests:
            self._timestamps.append(now)
            return True
        return False

    def remaining(self):
        now = _time.time()
        active = sum(1 for t in self._timestamps if now - t < self.window)
        return max(0, self.max_requests - active)

    def reset(self):
        self._timestamps.clear()

    def stats(self):
        now = _time.time()
        active = sum(1 for t in self._timestamps if now - t < self.window)
        return {"active": active, "max": self.max_requests, "window": self.window}
''',

    "pipeline": '''\
class Pipeline:
    def __init__(self):
        self._steps = []

    def add_step(self, name, fn):
        self._steps.append((name, fn))
        return self

    def run(self, data):
        result = data
        log = []
        for name, fn in self._steps:
            try:
                result = fn(result)
                log.append({"step": name, "ok": True})
            except Exception as e:
                log.append({"step": name, "ok": False, "error": str(e)})
                break
        return {"result": result, "log": log, "steps_run": len(log)}

def build_text_pipeline():
    p = Pipeline()
    p.add_step("strip", lambda s: s.strip())
    p.add_step("lower", lambda s: s.lower())
    p.add_step("remove_extra_spaces",
               lambda s: ' '.join(s.split()))
    p.add_step("truncate", lambda s: s[:500] if len(s) > 500 else s)
    return p
''',
}

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 72)
    print("Paper 12: Inhabitant Fleets")
    print("Programs: {}".format(len(PROGRAMS)))
    print("=" * 72)

    from jugeo.maturity import CyclicSystemCoordinator

    tmpfiles = []
    results = []

    total_coords = 0
    total_morphisms = 0
    total_sections = 0
    total_props = 0
    total_props_ok = 0
    total_descent_time = 0.0
    total_cycles = 0
    success_cycles = 0

    for pname, source in PROGRAMS.items():
        idx = list(PROGRAMS.keys()).index(pname) + 1
        print("\n  [{}/{}] {}".format(idx, len(PROGRAMS), pname))
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
            rec["covering_families"] = summary.get("covering_families", 0)
            rec["judgments"] = summary.get("judgments", 0)
            total_coords += rec["coordinates"]
            total_morphisms += rec["morphisms"]
            print("    load: coords={} morphisms={} covers={}".format(
                rec["coordinates"], rec["morphisms"],
                rec["covering_families"]))

            # --- CLI: descend ---
            t0 = time.perf_counter()
            desc_out = run_jugeo("descend", path)
            descent_time = time.perf_counter() - t0
            desc = {}
            for obj in desc_out:
                if isinstance(obj, dict) and "verdict" in obj:
                    desc = obj
                    break
            rec["verdict"] = desc.get("verdict", "unknown")
            rec["trust"] = desc.get("trust", "UNKNOWN")
            rec["local_sections"] = desc.get("local_sections", 0)
            rec["obstructions"] = len(desc.get("obstructions", []))
            rec["descent_time"] = round(descent_time, 4)
            total_sections += rec["local_sections"]
            total_descent_time += descent_time

            # Count propositions from sections_detail
            sections_detail = desc.get("sections_detail", [])
            sec_props = 0
            sec_props_ok = 0
            for sec in sections_detail:
                props = sec.get("propositions", sec.get("props", []))
                if isinstance(props, list):
                    sec_props += len(props)
                    sec_props_ok += sum(
                        1 for p in props
                        if (isinstance(p, dict) and p.get("status") in
                            ("verified", "ok", "pass", True))
                        or (isinstance(p, bool) and p)
                    )
                elif isinstance(props, int):
                    sec_props += props
                    sec_props_ok += props
            rec["props"] = sec_props
            rec["props_ok"] = sec_props_ok
            total_props += sec_props
            total_props_ok += sec_props_ok
            print("    descend: verdict={} sections={} props={} time={:.3f}s".format(
                rec["verdict"], rec["local_sections"], sec_props, descent_time))

            # --- CLI: evaluate ---
            eval_out = run_jugeo("evaluate", path)
            ev = {}
            for obj in eval_out:
                if isinstance(obj, dict) and "coverage" in obj:
                    ev = obj
                    break
            rec["coverage"] = ev.get("coverage", 0.0)
            rec["eval_trust"] = ev.get("trust", {})
            print("    evaluate: coverage={:.2f}".format(rec["coverage"]))

            # --- CyclicSystemCoordinator ---
            try:
                coord = CyclicSystemCoordinator.create(pname)
                t0 = time.perf_counter()
                cycle_result = coord.run_full_cycle({"source": source})
                cycle_time = time.perf_counter() - t0
                metrics = coord.get_metrics().to_dict()
                rec["cycle_time"] = round(cycle_time, 4)
                rec["cycle_metrics"] = metrics
                rec["cycle_phases"] = metrics.get("phases_completed",
                                                  metrics.get("phase_count", 0))
                rec["cycle_converged"] = metrics.get("converged",
                                                     metrics.get("success", False))
                total_cycles += 1
                if rec["cycle_converged"]:
                    success_cycles += 1
                print("    cycle: time={:.3f}s converged={} phases={}".format(
                    cycle_time, rec["cycle_converged"], rec["cycle_phases"]))
            except Exception as e:
                rec["cycle_error"] = str(e)
                rec["cycle_time"] = 0.0
                rec["cycle_phases"] = 0
                rec["cycle_converged"] = False
                print("    cycle: ERROR {}".format(e))

        except Exception as e:
            print("    ERROR: {}".format(e))
            rec["error"] = str(e)

        results.append(rec)

    # -- Aggregate statistics --------------------------------------------------
    ok = [r for r in results if "error" not in r]
    n_total = len(ok)

    def safe_mean(vals):
        return round(statistics.mean(vals), 4) if vals else 0.0

    sections_list = [r["local_sections"] for r in ok]
    props_list = [r.get("props", 0) for r in ok]
    descent_times = [r["descent_time"] for r in ok]
    cycle_times = [r.get("cycle_time", 0) for r in ok if r.get("cycle_time", 0) > 0]
    phase_counts = [r.get("cycle_phases", 0) for r in ok]
    coverages = [r.get("coverage", 0) for r in ok]

    mean_sections = safe_mean(sections_list)
    mean_props = safe_mean(props_list)
    success_rate = round(sum(1 for r in ok if r.get("verdict") == "verified") /
                         max(n_total, 1), 4)
    fleet_conv = round(success_cycles / max(total_cycles, 1), 4)
    mean_cycle_time = safe_mean(cycle_times)
    mean_phase_count = safe_mean(phase_counts)
    mean_descent_time = safe_mean(descent_times)

    # Trust distribution
    trust_vals = []
    for r in ok:
        t = r.get("eval_trust", {})
        if isinstance(t, dict):
            tv = t.get("level", t.get("value", t.get("score", 0)))
            if isinstance(tv, (int, float)):
                trust_vals.append(tv)
        elif isinstance(t, (int, float)):
            trust_vals.append(t)
    mean_trust = safe_mean(trust_vals) if trust_vals else 0.0

    # -- Write LaTeX macros ----------------------------------------------------
    out_path = os.path.join(ROOT, "papers", "data-paper12.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper12.tex -- AUTO-GENERATED by exp12_inhabitant_fleets.py\n")
        f.write("% DO NOT EDIT -- regenerate with: "
                "python3 experiments/exp12_inhabitant_fleets.py\n\n")

        f.write("% -- Suite parameters --\n")
        write_macro(f, "ppTwelveTotalPrograms", n_total)
        write_macro(f, "ppTwelveTotalCoords", total_coords)
        write_macro(f, "ppTwelveTotalMorphisms", total_morphisms)

        f.write("\n% -- Section and proposition counts --\n")
        write_macro(f, "ppTwelveMeanSections", "{:.1f}".format(mean_sections))
        write_macro(f, "ppTwelveMeanProps", "{:.1f}".format(mean_props))
        write_macro(f, "ppTwelveTotalProps", total_props)
        write_macro(f, "ppTwelveTotalPropsOk", total_props_ok)

        f.write("\n% -- Verification rates --\n")
        write_macro(f, "ppTwelveSuccessRate",
                    "{:.1f}\\%".format(success_rate * 100))
        write_macro(f, "ppTwelveMeanTrust", "{:.2f}".format(mean_trust))

        f.write("\n% -- Cycle metrics --\n")
        write_macro(f, "ppTwelveMeanCycleTime",
                    "{:.4f}\\,s".format(mean_cycle_time))
        write_macro(f, "ppTwelveTotalCycles", total_cycles)
        write_macro(f, "ppTwelveSuccessCycles", success_cycles)
        write_macro(f, "ppTwelveFleetConvergenceRate",
                    "{:.1f}\\%".format(fleet_conv * 100))
        write_macro(f, "ppTwelveMeanPhaseCount",
                    "{:.1f}".format(mean_phase_count))

        f.write("\n% -- Descent timing --\n")
        write_macro(f, "ppTwelveMeanDescentTime",
                    "{:.4f}\\,s".format(mean_descent_time))
        write_macro(f, "ppTwelveTotalDescentTime",
                    "{:.2f}\\,s".format(total_descent_time))

    print("\nWrote {}".format(out_path))

    # -- Save JSON results -----------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper12.json")
    full_results = {
        "experiment": "inhabitant_fleets",
        "paper": 12,
        "program_count": n_total,
        "per_program": results,
        "aggregates": {
            "total_coords": total_coords,
            "total_morphisms": total_morphisms,
            "mean_sections": mean_sections,
            "mean_props": mean_props,
            "total_props": total_props,
            "total_props_ok": total_props_ok,
            "success_rate": success_rate,
            "mean_trust": mean_trust,
            "mean_cycle_time": mean_cycle_time,
            "total_cycles": total_cycles,
            "success_cycles": success_cycles,
            "fleet_convergence_rate": fleet_conv,
            "mean_phase_count": mean_phase_count,
            "mean_descent_time": mean_descent_time,
            "total_descent_time": round(total_descent_time, 4),
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
    print("  Total morphisms:     {}".format(total_morphisms))
    print("  Mean sections:       {:.1f}".format(mean_sections))
    print("  Total props:         {} (ok={})".format(total_props, total_props_ok))
    print("  Success rate:        {:.1%}".format(success_rate))
    print("  Mean trust:          {:.2f}".format(mean_trust))
    print("  Cycles:              {} total, {} converged ({:.1%})".format(
        total_cycles, success_cycles, fleet_conv))
    print("  Mean cycle time:     {:.4f}s".format(mean_cycle_time))
    print("  Mean descent time:   {:.4f}s".format(mean_descent_time))
    print("  Total descent time:  {:.2f}s".format(total_descent_time))
    print("=" * 72)

    # -- Cleanup ---------------------------------------------------------------
    for p in tmpfiles:
        cleanup(p)


if __name__ == "__main__":
    main()
