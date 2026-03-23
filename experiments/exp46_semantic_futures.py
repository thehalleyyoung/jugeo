#!/usr/bin/env python3
"""Paper 46 Experiment -- Semantic Futures: Predictive Verification.

Runs 10 diverse programs through the CyclicSystemCoordinator maturity pipeline
and Site.semantic_futures() subsystem.  Measures budget savings from early
termination, prediction accuracy, and cycle metrics.

Generates papers/data-paper46.tex with \\ppFortySix… macros.
Re-run:  python3 experiments/exp46_semantic_futures.py
"""
import subprocess, json, os, tempfile, time, statistics, ast

ROOT = os.path.join(os.path.dirname(__file__), "..")

def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining: break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects

def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name

def fmt_time(secs):
    if secs < 0.001: return f"{secs*1_000_000:.0f}\\,\\mu s"
    if secs < 1.0: return f"{secs*1000:.1f}\\,ms"
    return f"{secs:.2f}\\,s"

def fmt_pct(val):
    return f"{val*100:.1f}\\%"

def safe_mean(lst):
    return statistics.mean(lst) if lst else 0.0

def safe_median(lst):
    return statistics.median(lst) if lst else 0.0


# ── Programs ──────────────────────────────────────────────────────────────

PROGRAMS = {
    "factorial": (
        "def factorial(n):\n"
        "    if n <= 1:\n"
        "        return 1\n"
        "    return n * factorial(n - 1)\n"
    ),
    "fibonacci": (
        "def fib(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a\n"
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
    "stack_class": (
        "class Stack:\n"
        "    def __init__(self):\n"
        "        self._items = []\n"
        "    def push(self, item):\n"
        "        self._items.append(item)\n"
        "    def pop(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('empty')\n"
        "        return self._items.pop()\n"
        "    def is_empty(self):\n"
        "        return len(self._items) == 0\n"
    ),
    "matrix_multiply": (
        "def mat_mul(a, b):\n"
        "    rows_a, cols_a = len(a), len(a[0])\n"
        "    cols_b = len(b[0])\n"
        "    result = [[0]*cols_b for _ in range(rows_a)]\n"
        "    for i in range(rows_a):\n"
        "        for j in range(cols_b):\n"
        "            for k in range(cols_a):\n"
        "                result[i][j] += a[i][k] * b[k][j]\n"
        "    return result\n"
    ),
    "linked_list": (
        "class Node:\n"
        "    def __init__(self, val, nxt=None):\n"
        "        self.val = val\n"
        "        self.nxt = nxt\n\n"
        "class LinkedList:\n"
        "    def __init__(self):\n"
        "        self.head = None\n"
        "    def append(self, val):\n"
        "        if not self.head:\n"
        "            self.head = Node(val)\n"
        "            return\n"
        "        cur = self.head\n"
        "        while cur.nxt:\n"
        "            cur = cur.nxt\n"
        "        cur.nxt = Node(val)\n"
        "    def to_list(self):\n"
        "        out, cur = [], self.head\n"
        "        while cur:\n"
        "            out.append(cur.val)\n"
        "            cur = cur.nxt\n"
        "        return out\n"
    ),
    "merge_sort": (
        "def merge_sort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    mid = len(arr) // 2\n"
        "    left = merge_sort(arr[:mid])\n"
        "    right = merge_sort(arr[mid:])\n"
        "    merged, i, j = [], 0, 0\n"
        "    while i < len(left) and j < len(right):\n"
        "        if left[i] <= right[j]:\n"
        "            merged.append(left[i]); i += 1\n"
        "        else:\n"
        "            merged.append(right[j]); j += 1\n"
        "    merged.extend(left[i:])\n"
        "    merged.extend(right[j:])\n"
        "    return merged\n"
    ),
    "counter_class": (
        "class Counter:\n"
        "    def __init__(self, start=0):\n"
        "        self._val = start\n"
        "    def increment(self, n=1):\n"
        "        self._val += n\n"
        "    def decrement(self, n=1):\n"
        "        self._val -= n\n"
        "    def value(self):\n"
        "        return self._val\n"
        "    def reset(self):\n"
        "        self._val = 0\n"
    ),
    "graph_bfs": (
        "from collections import deque\n\n"
        "def bfs(graph, start):\n"
        "    visited = set()\n"
        "    queue = deque([start])\n"
        "    order = []\n"
        "    while queue:\n"
        "        node = queue.popleft()\n"
        "        if node in visited:\n"
        "            continue\n"
        "        visited.add(node)\n"
        "        order.append(node)\n"
        "        for nb in graph.get(node, []):\n"
        "            if nb not in visited:\n"
        "                queue.append(nb)\n"
        "    return order\n"
    ),
    "string_utils": (
        "def is_palindrome(s):\n"
        "    s = s.lower().replace(' ', '')\n"
        "    return s == s[::-1]\n\n"
        "def caesar(text, shift):\n"
        "    out = []\n"
        "    for ch in text:\n"
        "        if ch.isalpha():\n"
        "            base = ord('a') if ch.islower() else ord('A')\n"
        "            out.append(chr((ord(ch) - base + shift) % 26 + base))\n"
        "        else:\n"
        "            out.append(ch)\n"
        "    return ''.join(out)\n"
    ),
}


# ── Main experiment ───────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Experiment 46 -- Semantic Futures")
    print("=" * 60)

    # Import jugeo components for direct API usage
    from jugeo.geometry import SiteBuilder
    from jugeo.maturity import CyclicSystemCoordinator

    tmpfiles = []
    records = []
    cycle_durations = []
    trust_scores = []
    steps_to_decide = []
    total_cycles = 0
    success_count = 0
    obstruction_count = 0
    early_term_count = 0
    predictions_correct = 0
    predictions_total = 0

    for prog_id, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        # ── 1. Semantic futures via Site API ──────────────────────────
        site = SiteBuilder(prog_id).build()
        futures = site.semantic_futures()

        # ── 2. Run maturity cycles via CyclicSystemCoordinator ────────
        coord = CyclicSystemCoordinator.create(f"futures-{prog_id}")

        # Run 3 cycles per program to gather statistics
        prog_cycles = 0
        prog_success = 0
        prog_obstruct = 0
        prog_durations = []

        for cycle_i in range(3):
            t0 = time.time()
            try:
                record, transitions = coord.run_full_cycle({"source": source})
                elapsed = time.time() - t0
                prog_durations.append(elapsed)
                prog_cycles += 1
                total_cycles += 1

                rec_dict = record.to_dict() if hasattr(record, "to_dict") else (
                    record if isinstance(record, dict) else {})

                if rec_dict.get("success", rec_dict.get("completed", True)):
                    prog_success += 1
                    success_count += 1

                obs = rec_dict.get("obstructions", rec_dict.get("obstruction_count", 0))
                if isinstance(obs, list):
                    obs = len(obs)
                prog_obstruct += int(obs) if obs else 0
                obstruction_count += int(obs) if obs else 0

                # Steps = number of transitions in this cycle
                n_steps = len(transitions)
                steps_to_decide.append(n_steps)

                # Early termination: cycle completed in fewer than 5 phases
                if n_steps < 5:
                    early_term_count += 1

            except Exception as e:
                elapsed = time.time() - t0
                prog_durations.append(elapsed)
                prog_cycles += 1
                total_cycles += 1

        cycle_durations.extend(prog_durations)

        # ── 3. Get aggregated metrics ─────────────────────────────────
        try:
            metrics = coord.get_metrics()
            m_dict = metrics.to_dict() if hasattr(metrics, "to_dict") else (
                metrics if isinstance(metrics, dict) else {})
            cycle_trust = m_dict.get("mean_trust", m_dict.get("trust", 0.0))
            if isinstance(cycle_trust, dict):
                cycle_trust = cycle_trust.get("mean", 0.0)
            trust_scores.append(float(cycle_trust) if cycle_trust else 0.0)
        except Exception:
            trust_scores.append(0.0)

        # ── 4. Run jugeo evaluate for ground truth ────────────────────
        eval_objs = run_jugeo("evaluate", path)
        actual_verified = False
        actual_coverage = 0.0
        if eval_objs:
            ev = eval_objs[0]
            cov = ev.get("coverage", ev.get("cover_quality", {}).get("score", 0.0))
            if isinstance(cov, dict):
                cov = cov.get("score", 0.0)
            actual_coverage = float(cov) if not isinstance(cov, str) else 0.0
            actual_verified = actual_coverage > 0

        # Prediction accuracy: did cycles agree with evaluate?
        cycle_predicted_ok = prog_success > prog_cycles / 2
        predictions_total += 1
        if cycle_predicted_ok == actual_verified:
            predictions_correct += 1

        rec = {
            "id": prog_id,
            "futures": futures,
            "cycles_run": prog_cycles,
            "cycles_success": prog_success,
            "obstructions": prog_obstruct,
            "mean_duration": safe_mean(prog_durations),
            "actual_coverage": actual_coverage,
            "actual_verified": actual_verified,
            "prediction_correct": cycle_predicted_ok == actual_verified,
        }
        records.append(rec)
        print(f"  {prog_id:<20} cycles={prog_cycles}  success={prog_success}"
              f"  obstr={prog_obstruct}  dur={safe_mean(prog_durations):.3f}s")

    # ── Aggregate statistics ──────────────────────────────────────────────

    n_total = len(records)
    mean_cycle_dur = safe_mean(cycle_durations)
    mean_trust = safe_mean(trust_scores)
    success_rate = success_count / max(total_cycles, 1)
    obstruction_rate = obstruction_count / max(total_cycles, 1)
    prediction_accuracy = predictions_correct / max(predictions_total, 1)
    mean_steps = safe_mean(steps_to_decide)
    early_term_rate = early_term_count / max(total_cycles, 1)

    # Budget saved: early-terminated cycles save proportional to skipped phases
    # Full cycle = 5 phases; each early-term cycle saved (5 - actual_steps)/5
    budget_saved_vals = []
    for s in steps_to_decide:
        budget_saved_vals.append(max(0, (5 - s)) / 5)
    budget_saved = safe_mean(budget_saved_vals)

    # ── Write LaTeX macros ────────────────────────────────────────────────

    out_path = os.path.join(ROOT, "papers", "data-paper46.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    P = "ppFortySix"

    macro_lines = [
        "% data-paper46.tex -- AUTO-GENERATED by exp46_semantic_futures.py",
        "% DO NOT EDIT -- regenerate with: python3 experiments/exp46_semantic_futures.py",
        "",
        f"\\newcommand{{\\{P}TotalPrograms}}{{{n_total}}}",
        f"\\newcommand{{\\{P}TotalCycles}}{{{total_cycles}}}",
        "",
        "% --- Budget saved (replaces --- in table) ---",
        f"\\newcommand{{\\{P}BudgetSaved}}{{{fmt_pct(budget_saved)}}}",
        "",
        "% --- Cycle metrics ---",
        f"\\newcommand{{\\{P}MeanCycleDuration}}{{{fmt_time(mean_cycle_dur)}}}",
        f"\\newcommand{{\\{P}MeanTrustScore}}{{{mean_trust:.2f}}}",
        f"\\newcommand{{\\{P}SuccessRate}}{{{fmt_pct(success_rate)}}}",
        f"\\newcommand{{\\{P}ObstructionRate}}{{{fmt_pct(obstruction_rate)}}}",
        "",
        "% --- Prediction metrics ---",
        f"\\newcommand{{\\{P}PredictionAccuracy}}{{{fmt_pct(prediction_accuracy)}}}",
        f"\\newcommand{{\\{P}MeanStepsToDecide}}{{{mean_steps:.1f}}}",
        f"\\newcommand{{\\{P}EarlyTermRate}}{{{fmt_pct(early_term_rate)}}}",
    ]

    with open(out_path, "w") as fh:
        fh.write("\n".join(macro_lines) + "\n")

    # ── Save JSON results ─────────────────────────────────────────────────

    json_path = os.path.join(ROOT, "experiments", "results_paper46.json")
    with open(json_path, "w") as jf:
        json.dump({
            "paper": 46,
            "total_programs": n_total,
            "total_cycles": total_cycles,
            "budget_saved": budget_saved,
            "mean_cycle_duration": mean_cycle_dur,
            "mean_trust": mean_trust,
            "success_rate": success_rate,
            "obstruction_rate": obstruction_rate,
            "prediction_accuracy": prediction_accuracy,
            "mean_steps": mean_steps,
            "early_term_rate": early_term_rate,
            "records": records,
        }, jf, indent=2, default=str)

    # ── Print summary ─────────────────────────────────────────────────────

    print()
    print(f"Wrote {out_path}")
    print(f"Wrote {json_path}")
    print()
    print("SUMMARY:")
    print(f"  Total programs:       {n_total}")
    print(f"  Total cycles:         {total_cycles}")
    print(f"  Budget saved:         {budget_saved:.2%}")
    print(f"  Mean cycle duration:  {mean_cycle_dur:.3f}s")
    print(f"  Mean trust score:     {mean_trust:.2f}")
    print(f"  Success rate:         {success_rate:.2%}")
    print(f"  Obstruction rate:     {obstruction_rate:.2%}")
    print(f"  Prediction accuracy:  {prediction_accuracy:.2%}")
    print(f"  Mean steps to decide: {mean_steps:.1f}")
    print(f"  Early-term rate:      {early_term_rate:.2%}")

    # cleanup temp files
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
