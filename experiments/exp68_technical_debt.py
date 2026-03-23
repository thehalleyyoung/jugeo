#!/usr/bin/env python3
"""Paper 68 Experiment — Technical Debt Measurement via Sheaf Metrics.

Runs JuGeo on programs with varying debt levels, using maturity cycles
and trust tiers to quantify technical debt. Measures site complexity,
trust distribution, and repair potential.
Generates papers/data-paper68.tex with \ppLXVIII... macros.

Re-run: python3 experiments/exp68_technical_debt.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper68.tex"

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
        if not remaining: break
        try:
            obj, end = dec.raw_decode(remaining)
            objects.append(obj); idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects

def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source); f.close(); return f.name

def safe_mean(xs): return round(statistics.mean(xs), 2) if xs else 0.0

# ─── Programs with varying debt levels ──────────────────────────────────────

DEBT_PROGRAMS = {
    "clean_stack": textwrap.dedent("""\
        class Stack:
            def __init__(self): self.items = []
            def push(self, x): self.items.append(x)
            def pop(self):
                if not self.items: raise IndexError("empty")
                return self.items.pop()
            def peek(self):
                if not self.items: raise IndexError("empty")
                return self.items[-1]
            def is_empty(self): return len(self.items) == 0
            def size(self): return len(self.items)
    """),
    "clean_calculator": textwrap.dedent("""\
        class Calculator:
            def __init__(self): self.history = []
            def add(self, a, b):
                r = a + b; self.history.append(r); return r
            def sub(self, a, b):
                r = a - b; self.history.append(r); return r
            def mul(self, a, b):
                r = a * b; self.history.append(r); return r
            def div(self, a, b):
                if b == 0: raise ZeroDivisionError
                r = a / b; self.history.append(r); return r
    """),
    "moderate_debt_parser": textwrap.dedent("""\
        def parse_int(s):
            # TODO: handle negative numbers
            result = 0
            for c in s:
                if not c.isdigit(): return None  # weak error handling
                result = result * 10 + int(c)
            return result
        def parse_float(s):
            parts = s.split('.')
            if len(parts) > 2: return None
            # FIXME: doesn't handle scientific notation
            integer = parse_int(parts[0]) or 0
            if len(parts) == 2:
                frac = parse_int(parts[1]) or 0
                return integer + frac / (10 ** len(parts[1]))
            return float(integer)
        def parse_list(s):
            # HACK: very fragile parsing
            s = s.strip('[]')
            if not s: return []
            return [parse_int(x.strip()) for x in s.split(',')]
    """),
    "high_debt_globals": textwrap.dedent("""\
        _cache = {}
        _counter = 0
        _log = []
        def process(data):
            global _counter, _cache, _log
            _counter += 1
            key = str(data)
            if key in _cache:
                _log.append(f"cache hit #{_counter}")
                return _cache[key]
            result = data * 2  # TODO: real processing
            _cache[key] = result
            _log.append(f"computed #{_counter}")
            return result
        def get_stats():
            global _counter, _log
            return {'calls': _counter, 'log_size': len(_log)}
        def reset():
            global _cache, _counter, _log
            _cache = {}; _counter = 0; _log = []
    """),
    "debt_mixed_concerns": textwrap.dedent("""\
        class UserManager:
            def __init__(self):
                self.users = {}
                self.log = []  # mixing logging with business logic
            def create(self, name, email):
                uid = len(self.users) + 1  # fragile ID generation
                self.users[uid] = {'name': name, 'email': email}
                self.log.append(f"created {name}")
                print(f"User {name} created")  # side effect
                return uid
            def delete(self, uid):
                if uid in self.users:
                    name = self.users[uid]['name']
                    del self.users[uid]
                    self.log.append(f"deleted {name}")
                    print(f"User {name} deleted")  # side effect
            def find(self, name):
                # O(n) linear scan
                for uid, u in self.users.items():
                    if u['name'] == name: return uid
                return None
    """),
    "clean_validator": textwrap.dedent("""\
        def validate_email(email):
            if not isinstance(email, str): return False
            if '@' not in email: return False
            local, domain = email.rsplit('@', 1)
            return len(local) > 0 and '.' in domain
        def validate_password(pw):
            if len(pw) < 8: return False
            return any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
        def validate_age(age):
            return isinstance(age, int) and 0 <= age <= 150
    """),
    "debt_no_error_handling": textwrap.dedent("""\
        def read_config(path):
            with open(path) as f:  # no error handling
                return eval(f.read())  # using eval!
        def save_config(path, data):
            with open(path, 'w') as f:
                f.write(str(data))  # no proper serialization
        def get_setting(config, key):
            return config[key]  # no KeyError handling
    """),
    "clean_sorting": textwrap.dedent("""\
        def insertion_sort(arr):
            for i in range(1, len(arr)):
                key = arr[i]; j = i - 1
                while j >= 0 and arr[j] > key:
                    arr[j+1] = arr[j]; j -= 1
                arr[j+1] = key
            return arr
        def is_sorted(arr):
            return all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
    """),
    "debt_god_class": textwrap.dedent("""\
        class AppManager:
            def __init__(self):
                self.users = {}; self.products = {}
                self.orders = []; self.config = {}
            def add_user(self, name): self.users[name] = True
            def add_product(self, name, price):
                self.products[name] = price
            def place_order(self, user, product):
                if user not in self.users: return False
                if product not in self.products: return False
                self.orders.append({'user': user, 'product': product,
                                    'price': self.products[product]})
                return True
            def get_revenue(self):
                return sum(o['price'] for o in self.orders)
            def set_config(self, key, val): self.config[key] = val
            def get_config(self, key): return self.config.get(key)
    """),
    "clean_tree": textwrap.dedent("""\
        class TreeNode:
            def __init__(self, val, left=None, right=None):
                self.val = val; self.left = left; self.right = right
        def inorder(root):
            if root is None: return []
            return inorder(root.left) + [root.val] + inorder(root.right)
        def height(root):
            if root is None: return 0
            return 1 + max(height(root.left), height(root.right))
        def count_nodes(root):
            if root is None: return 0
            return 1 + count_nodes(root.left) + count_nodes(root.right)
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 68: Technical Debt Experiments")
print("=" * 60)

from jugeo.maturity import CyclicSystemCoordinator

results = []
for prog_id, source in DEBT_PROGRAMS.items():
    print(f"  [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        t0 = time.perf_counter()

        # Run maturity cycle
        coord = CyclicSystemCoordinator.create(prog_id)
        record = coord.run_full_cycle({'source': source})
        metrics = coord.get_metrics().to_dict()

        # Run standard analysis
        desc = run_jugeo_json("descend", tmp)
        enc = run_jugeo_json("encode", tmp)
        bugs = run_jugeo_json("bugs", tmp)

        elapsed = time.perf_counter() - t0

        d = desc[0] if desc else {}
        e = enc[0] if enc else {}
        b = bugs[0] if bugs else {}

        files_enc = e.get("files", [])
        n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
        secs = d.get("sections_detail", [])
        props = sum(s.get("propositions", 0) for s in secs)
        ok_p = sum(s.get("ok", 0) for s in secs)
        verdict = d.get("verdict", "unknown")
        bug_count = b.get("count", 0) if isinstance(b, dict) else 0

        trust_score = metrics.get("mean_trust_score", 0.0)
        obs_count = metrics.get("total_obstructions", 0)
        cycle_dur = metrics.get("mean_cycle_duration", 0.0)

        # Debt score: lower trust + more bugs + more obstructions = higher debt
        debt_score = round(1.0 - trust_score + 0.1 * bug_count + 0.05 * obs_count, 3)

        rec = {
            "id": prog_id,
            "n_coords": n_coords, "props": props, "ok": ok_p,
            "verdict": verdict, "bugs": bug_count,
            "trust_score": round(trust_score, 4),
            "obstructions": obs_count,
            "cycle_duration_ms": round(cycle_dur * 1000, 1),
            "debt_score": debt_score,
            "time_s": round(elapsed, 3),
        }
        results.append(rec)
        print(f"debt={debt_score} trust={trust_score:.2f} bugs={bug_count} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": prog_id, "error": str(e)})
    finally:
        try: os.unlink(tmp)
        except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

ok = [r for r in results if "error" not in r]
n_total = len(DEBT_PROGRAMS)
n_ok = len(ok)

debt_scores = [r["debt_score"] for r in ok]
trust_scores = [r["trust_score"] for r in ok]
times = [r["time_s"] for r in ok]
coords_list = [r["n_coords"] for r in ok]
props_list = [r["props"] for r in ok]
bug_list = [r["bugs"] for r in ok]
cycle_list = [r["cycle_duration_ms"] for r in ok]

clean_debt = [r["debt_score"] for r in ok if r["id"].startswith("clean")]
high_debt = [r["debt_score"] for r in ok if "debt" in r["id"]]

verified_count = sum(1 for r in ok if r["verdict"] == "verified")

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper68.tex — AUTO-GENERATED by exp68_technical_debt.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp68_technical_debt.py",
    f"% Generated from {n_total} programs (clean + debt-laden)",
    "",
    f"\\newcommand{{\\ppLXVIIIprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXVIIIprogramsOk}}{{{n_ok}}}",
    f"\\newcommand{{\\ppLXVIIIverified}}{{{verified_count}}}",
    f"\\newcommand{{\\ppLXVIIIverifiedPct}}{{{round(100*verified_count/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXVIIIdebtMean}}{{{safe_mean(debt_scores)}}}",
    f"\\newcommand{{\\ppLXVIIIdebtMax}}{{{round(max(debt_scores),3) if debt_scores else 0}}}",
    f"\\newcommand{{\\ppLXVIIIdebtMin}}{{{round(min(debt_scores),3) if debt_scores else 0}}}",
    f"\\newcommand{{\\ppLXVIIIcleanDebtMean}}{{{safe_mean(clean_debt)}}}",
    f"\\newcommand{{\\ppLXVIIIhighDebtMean}}{{{safe_mean(high_debt)}}}",
    "",
    f"\\newcommand{{\\ppLXVIIItrustMean}}{{{safe_mean(trust_scores)}}}",
    f"\\newcommand{{\\ppLXVIIItrustMax}}{{{round(max(trust_scores),4) if trust_scores else 0}}}",
    f"\\newcommand{{\\ppLXVIIItrustMin}}{{{round(min(trust_scores),4) if trust_scores else 0}}}",
    "",
    f"\\newcommand{{\\ppLXVIIIcoordsMean}}{{{safe_mean(coords_list)}}}",
    f"\\newcommand{{\\ppLXVIIIpropsMean}}{{{safe_mean(props_list)}}}",
    f"\\newcommand{{\\ppLXVIIIbugsMean}}{{{safe_mean(bug_list)}}}",
    f"\\newcommand{{\\ppLXVIIIbugsTotal}}{{{sum(bug_list)}}}",
    "",
    f"\\newcommand{{\\ppLXVIIIcycleMean}}{{{safe_mean(cycle_list)}\\,ms}}",
    f"\\newcommand{{\\ppLXVIIItimeMean}}{{{safe_mean(times)}\\,s}}",
    f"\\newcommand{{\\ppLXVIIItimeTotal}}{{{round(sum(times),2)}\\,s}}",
    "",
    "% Per-program debt results",
]
for r in ok:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXVIIIdebt{tag}Score}}{{{r['debt_score']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIIdebt{tag}Trust}}{{{round(r['trust_score'],3)}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIIdebt{tag}Coords}}{{{r['n_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIIdebt{tag}Props}}{{{r['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIIdebt{tag}Bugs}}{{{r['bugs']}}}")
    lines.append(f"\\newcommand{{\\ppLXVIIIdebt{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper68.json"
with open(json_path, "w") as f:
    json.dump({"paper": 68, "programs": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
