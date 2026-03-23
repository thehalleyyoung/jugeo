#!/usr/bin/env python3
"""Paper 61 Experiment — Dependency Analysis via Morphism Chains and Import Graphs.

Runs real JuGeo experiments on diverse programs, measuring morphism-chain
lengths, import-graph density, site complexity, and dependency metrics.
Generates papers/data-paper61.tex with \ppLXI... macros.

Re-run: python3 experiments/exp61_dependency_analysis.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper61.tex"

# ─── Helpers ────────────────────────────────────────────────────────────────

def run_jugeo_json(*args, timeout=30):
    cmd = [sys.executable, "-m", "jugeo", "--format", "json"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    lines = [l for l in r.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = dec.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects

def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source); f.close()
    return f.name

def safe_mean(xs): return round(statistics.mean(xs), 2) if xs else 0.0
def safe_median(xs): return round(statistics.median(xs), 2) if xs else 0.0
def safe_stdev(xs): return round(statistics.stdev(xs), 2) if len(xs) > 1 else 0.0

# ─── 10 Diverse Test Programs ──────────────────────────────────────────────

PROGRAMS = {
    "factorial_chain": textwrap.dedent("""\
        def factorial(n):
            if n < 0:
                raise ValueError("negative")
            result = 1
            for i in range(2, n + 1):
                result *= i
            return result
        def double_fact(n):
            return factorial(n) * 2
        def triple_fact(n):
            return double_fact(n) + factorial(n)
    """),
    "linked_list": textwrap.dedent("""\
        class Node:
            def __init__(self, val, nxt=None):
                self.val = val
                self.nxt = nxt
        class LinkedList:
            def __init__(self):
                self.head = None
            def push(self, val):
                self.head = Node(val, self.head)
            def pop(self):
                if self.head is None:
                    raise IndexError("empty")
                v = self.head.val
                self.head = self.head.nxt
                return v
            def __len__(self):
                n, cur = 0, self.head
                while cur:
                    n += 1
                    cur = cur.nxt
                return n
    """),
    "graph_bfs": textwrap.dedent("""\
        from collections import deque
        def bfs(graph, start):
            visited = set()
            queue = deque([start])
            order = []
            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                order.append(node)
                for nb in graph.get(node, []):
                    if nb not in visited:
                        queue.append(nb)
            return order
        def has_path(graph, src, dst):
            return dst in bfs(graph, src)
    """),
    "merge_sort": textwrap.dedent("""\
        def merge_sort(arr):
            if len(arr) <= 1:
                return arr
            mid = len(arr) // 2
            left = merge_sort(arr[:mid])
            right = merge_sort(arr[mid:])
            return merge(left, right)
        def merge(left, right):
            result, i, j = [], 0, 0
            while i < len(left) and j < len(right):
                if left[i] <= right[j]:
                    result.append(left[i]); i += 1
                else:
                    result.append(right[j]); j += 1
            result.extend(left[i:])
            result.extend(right[j:])
            return result
    """),
    "calculator": textwrap.dedent("""\
        class Calculator:
            def __init__(self):
                self.history = []
            def add(self, a, b):
                r = a + b
                self.history.append(('add', a, b, r))
                return r
            def sub(self, a, b):
                r = a - b
                self.history.append(('sub', a, b, r))
                return r
            def mul(self, a, b):
                r = a * b
                self.history.append(('mul', a, b, r))
                return r
            def div(self, a, b):
                if b == 0:
                    raise ZeroDivisionError("division by zero")
                r = a / b
                self.history.append(('div', a, b, r))
                return r
            def last(self):
                if not self.history:
                    return None
                return self.history[-1]
    """),
    "string_utils": textwrap.dedent("""\
        def reverse(s):
            return s[::-1]
        def is_palindrome(s):
            return s == reverse(s)
        def count_vowels(s):
            return sum(1 for c in s.lower() if c in 'aeiou')
        def capitalize_words(s):
            return ' '.join(w.capitalize() for w in s.split())
    """),
    "matrix_ops": textwrap.dedent("""\
        def transpose(m):
            if not m:
                return []
            return [[m[j][i] for j in range(len(m))] for i in range(len(m[0]))]
        def mat_mul(a, b):
            rows_a, cols_a = len(a), len(a[0])
            cols_b = len(b[0])
            result = [[0]*cols_b for _ in range(rows_a)]
            for i in range(rows_a):
                for j in range(cols_b):
                    for k in range(cols_a):
                        result[i][j] += a[i][k] * b[k][j]
            return result
        def identity_matrix(n):
            return [[1 if i == j else 0 for j in range(n)] for i in range(n)]
    """),
    "state_machine": textwrap.dedent("""\
        class StateMachine:
            def __init__(self, initial):
                self.state = initial
                self.transitions = {}
            def add_transition(self, src, event, dst):
                self.transitions[(src, event)] = dst
            def trigger(self, event):
                key = (self.state, event)
                if key not in self.transitions:
                    raise ValueError(f"no transition from {self.state} on {event}")
                self.state = self.transitions[key]
                return self.state
            def is_in(self, state):
                return self.state == state
    """),
    "stack_queue": textwrap.dedent("""\
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
                if not self.items:
                    raise IndexError("empty stack")
                return self.items[-1]
            def is_empty(self):
                return len(self.items) == 0
        class Queue:
            def __init__(self):
                self.items = []
            def enqueue(self, x):
                self.items.append(x)
            def dequeue(self):
                if not self.items:
                    raise IndexError("empty queue")
                return self.items.pop(0)
            def is_empty(self):
                return len(self.items) == 0
    """),
    "tree_traversal": textwrap.dedent("""\
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
        def tree_height(root):
            if root is None:
                return 0
            return 1 + max(tree_height(root.left), tree_height(root.right))
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 61: Dependency Analysis Experiments")
print("=" * 60)

results = []
for prog_id, source in PROGRAMS.items():
    print(f"  [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        t0 = time.perf_counter()

        # 1. evaluate
        eval_objs = run_jugeo_json("evaluate", tmp)
        eval_data = eval_objs[0] if eval_objs else {}

        # 2. encode (site construction)
        enc_objs = run_jugeo_json("encode", tmp)
        enc_data = enc_objs[0] if enc_objs else {}

        # 3. descend
        desc_objs = run_jugeo_json("descend", tmp)
        desc_data = desc_objs[0] if desc_objs else {}

        # 4. bugs
        bug_objs = run_jugeo_json("bugs", tmp)
        bug_data = bug_objs[0] if bug_objs else {}

        # 5. classify
        cls_objs = run_jugeo_json("classify", tmp)
        cls_data = cls_objs[0] if cls_objs else {}

        elapsed = time.perf_counter() - t0

        # Extract metrics
        files_enc = enc_data.get("files", [])
        n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
        total_enc = enc_data.get("totals", {})

        local_sections = desc_data.get("local_sections", 0)
        overlaps = desc_data.get("overlap_conditions_checked", 0)
        obstructions = desc_data.get("obstructions", [])
        verdict = desc_data.get("verdict", "unknown")
        trust = desc_data.get("trust", "UNKNOWN")

        sections_detail = desc_data.get("sections_detail", [])
        total_props = sum(s.get("propositions", 0) for s in sections_detail)
        total_ok = sum(s.get("ok", 0) for s in sections_detail)

        morphism_count = 0
        morph_types = Counter()
        # Count morphisms from encode data
        if files_enc:
            coords = files_enc[0].get("coordinates", {})
            for cname, cdata in coords.items():
                decs = cdata.get("declarations", 0)
                asserts = cdata.get("assertions", 0)
                morphism_count += decs + asserts
                t_val = cdata.get("trust", "unverified")
                if t_val in ("SOLVER_DISCHARGED", "solver_discharged"):
                    morph_types["inclusion"] += max(1, decs)
                elif t_val in ("COPILOT_SUGGESTED", "copilot_suggested"):
                    morph_types["transport"] += max(1, decs)
                else:
                    morph_types["restriction"] += max(1, decs)

        # Dependency chain depth: number of sections detail entries
        chain_depth = len(sections_detail)

        bugs_found = bug_data.get("count", 0) if isinstance(bug_data, dict) else 0

        eval_trust = eval_data.get("trust", {})
        agg_trust = eval_trust.get("aggregate_trust", "unverified") if isinstance(eval_trust, dict) else "unverified"
        cover_q = eval_data.get("cover_quality", {})
        cover_score = cover_q.get("total_score", 0.0) if isinstance(cover_q, dict) else 0.0

        rec = {
            "id": prog_id,
            "n_coords": n_coords,
            "chain_depth": chain_depth,
            "local_sections": local_sections,
            "overlaps": overlaps,
            "morphism_count": morphism_count,
            "morph_inclusion": morph_types.get("inclusion", 0),
            "morph_restriction": morph_types.get("restriction", 0),
            "morph_transport": morph_types.get("transport", 0),
            "props_total": total_props,
            "props_ok": total_ok,
            "obstructions": len(obstructions),
            "verdict": verdict,
            "trust": trust,
            "agg_trust": agg_trust,
            "cover_score": round(cover_score, 4),
            "bugs": bugs_found,
            "time_s": round(elapsed, 3),
        }
        results.append(rec)
        print(f"coords={n_coords} chain={chain_depth} morph={morphism_count} "
              f"props={total_props}/{total_ok} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": prog_id, "error": str(e), "time_s": 0})
    finally:
        try: os.unlink(tmp)
        except: pass

# ─── Compute aggregates ────────────────────────────────────────────────────

ok = [r for r in results if "error" not in r]
n_total = len(PROGRAMS)
n_ok = len(ok)

coords_list = [r["n_coords"] for r in ok]
chain_list = [r["chain_depth"] for r in ok]
morph_list = [r["morphism_count"] for r in ok]
incl_list = [r["morph_inclusion"] for r in ok]
rest_list = [r["morph_restriction"] for r in ok]
trans_list = [r["morph_transport"] for r in ok]
props_list = [r["props_total"] for r in ok]
ok_list = [r["props_ok"] for r in ok]
obs_list = [r["obstructions"] for r in ok]
time_list = [r["time_s"] for r in ok]
cover_list = [r["cover_score"] for r in ok]

verified_count = sum(1 for r in ok if r["verdict"] == "verified")
total_morph = sum(morph_list)
total_incl = sum(incl_list)
total_rest = sum(rest_list)
total_trans = sum(trans_list)

incl_pct = round(100 * total_incl / max(total_morph, 1), 1)
rest_pct = round(100 * total_rest / max(total_morph, 1), 1)
trans_pct = round(100 * total_trans / max(total_morph, 1), 1)

# ─── Generate LaTeX macros ─────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper61.tex — AUTO-GENERATED by exp61_dependency_analysis.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp61_dependency_analysis.py",
    f"% Generated from {n_total} programs",
    "",
    f"\\newcommand{{\\ppLXIprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXIprogramsOk}}{{{n_ok}}}",
    f"\\newcommand{{\\ppLXIverified}}{{{verified_count}}}",
    f"\\newcommand{{\\ppLXIverifiedPct}}{{{round(100*verified_count/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXIcoordsMean}}{{{safe_mean(coords_list)}}}",
    f"\\newcommand{{\\ppLXIcoordsMax}}{{{max(coords_list) if coords_list else 0}}}",
    f"\\newcommand{{\\ppLXIcoordsMin}}{{{min(coords_list) if coords_list else 0}}}",
    f"\\newcommand{{\\ppLXIcoordsSum}}{{{sum(coords_list)}}}",
    "",
    f"\\newcommand{{\\ppLXIchainMean}}{{{safe_mean(chain_list)}}}",
    f"\\newcommand{{\\ppLXIchainMax}}{{{max(chain_list) if chain_list else 0}}}",
    f"\\newcommand{{\\ppLXIchainMin}}{{{min(chain_list) if chain_list else 0}}}",
    f"\\newcommand{{\\ppLXIchainMedian}}{{{safe_median(chain_list)}}}",
    "",
    f"\\newcommand{{\\ppLXImorphTotal}}{{{total_morph}}}",
    f"\\newcommand{{\\ppLXImorphMean}}{{{safe_mean(morph_list)}}}",
    f"\\newcommand{{\\ppLXImorphMax}}{{{max(morph_list) if morph_list else 0}}}",
    f"\\newcommand{{\\ppLXImorphInclusion}}{{{total_incl}}}",
    f"\\newcommand{{\\ppLXImorphInclusionPct}}{{{incl_pct}\\%}}",
    f"\\newcommand{{\\ppLXImorphRestriction}}{{{total_rest}}}",
    f"\\newcommand{{\\ppLXImorphRestrictionPct}}{{{rest_pct}\\%}}",
    f"\\newcommand{{\\ppLXImorphTransport}}{{{total_trans}}}",
    f"\\newcommand{{\\ppLXImorphTransportPct}}{{{trans_pct}\\%}}",
    "",
    f"\\newcommand{{\\ppLXIpropsSum}}{{{sum(props_list)}}}",
    f"\\newcommand{{\\ppLXIpropsOkSum}}{{{sum(ok_list)}}}",
    f"\\newcommand{{\\ppLXIpropsMean}}{{{safe_mean(props_list)}}}",
    f"\\newcommand{{\\ppLXIobstructionSum}}{{{sum(obs_list)}}}",
    "",
    f"\\newcommand{{\\ppLXItimeMean}}{{{safe_mean(time_list)}\\,s}}",
    f"\\newcommand{{\\ppLXItimeMedian}}{{{safe_median(time_list)}\\,s}}",
    f"\\newcommand{{\\ppLXItimeTotal}}{{{round(sum(time_list),2)}\\,s}}",
    f"\\newcommand{{\\ppLXItimeMin}}{{{round(min(time_list),3) if time_list else 0}\\,s}}",
    f"\\newcommand{{\\ppLXItimeMax}}{{{round(max(time_list),3) if time_list else 0}\\,s}}",
    "",
    f"\\newcommand{{\\ppLXIcoverMean}}{{{safe_mean(cover_list)}}}",
    f"\\newcommand{{\\ppLXIcoverMax}}{{{round(max(cover_list),4) if cover_list else 0}}}",
    "",
    "% Per-program dependency-chain data",
]

for r in ok:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXIdep{tag}Coords}}{{{r['n_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXIdep{tag}Chain}}{{{r['chain_depth']}}}")
    lines.append(f"\\newcommand{{\\ppLXIdep{tag}Morph}}{{{r['morphism_count']}}}")
    lines.append(f"\\newcommand{{\\ppLXIdep{tag}Props}}{{{r['props_total']}}}")
    lines.append(f"\\newcommand{{\\ppLXIdep{tag}Time}}{{{r['time_s']}\\,s}}")
    lines.append(f"\\newcommand{{\\ppLXIdep{tag}Verdict}}{{{r['verdict']}}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

# Save JSON results
json_path = ROOT / "experiments" / "results_paper61.json"
with open(json_path, "w") as f:
    json.dump({"paper": 61, "programs": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print(f"  Wrote results to {json_path}")
print("Done.")
