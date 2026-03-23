#!/usr/bin/env python3
"""Paper 65 Experiment — CI/CD Integration via Pipeline Verification.

Runs JuGeo on programs in simulated CI stages (pre-merge, post-merge, release),
measuring gate pass rates, incremental vs full verification times, and
pipeline overhead.
Generates papers/data-paper65.tex with \ppLXV... macros.

Re-run: python3 experiments/exp65_ci_cd_integration.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper65.tex"

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
def safe_median(xs): return round(statistics.median(xs), 2) if xs else 0.0

# ─── CI Programs (simulate pipeline stages) ────────────────────────────────

CI_PROGRAMS = {
    "payment_processor": textwrap.dedent("""\
        class PaymentProcessor:
            def __init__(self):
                self.transactions = []
            def charge(self, amount, card):
                if amount <= 0: raise ValueError("amount must be positive")
                if len(card) != 16: raise ValueError("invalid card")
                txn = {'amount': amount, 'card': card[-4:], 'status': 'ok'}
                self.transactions.append(txn)
                return txn
            def refund(self, idx):
                if idx >= len(self.transactions): raise IndexError
                self.transactions[idx]['status'] = 'refunded'
            def total(self):
                return sum(t['amount'] for t in self.transactions if t['status'] == 'ok')
    """),
    "user_service": textwrap.dedent("""\
        class UserService:
            def __init__(self): self.users = {}
            def create(self, uid, name, email):
                if uid in self.users: raise ValueError("duplicate")
                self.users[uid] = {'name': name, 'email': email, 'active': True}
            def deactivate(self, uid):
                if uid not in self.users: raise KeyError(uid)
                self.users[uid]['active'] = False
            def find(self, uid):
                if uid not in self.users: raise KeyError(uid)
                return self.users[uid]
            def active_users(self):
                return [u for u in self.users.values() if u['active']]
    """),
    "inventory": textwrap.dedent("""\
        class Inventory:
            def __init__(self): self.items = {}
            def add(self, sku, qty):
                if qty < 0: raise ValueError("negative qty")
                self.items[sku] = self.items.get(sku, 0) + qty
            def remove(self, sku, qty):
                current = self.items.get(sku, 0)
                if qty > current: raise ValueError("insufficient stock")
                self.items[sku] = current - qty
            def check(self, sku): return self.items.get(sku, 0)
            def low_stock(self, threshold=5):
                return {k: v for k, v in self.items.items() if v <= threshold}
    """),
    "rate_limiter": textwrap.dedent("""\
        import time as _time
        class RateLimiter:
            def __init__(self, max_calls, window_s):
                self.max_calls = max_calls
                self.window = window_s
                self.calls = []
            def allow(self):
                now = _time.time()
                self.calls = [t for t in self.calls if now - t < self.window]
                if len(self.calls) >= self.max_calls:
                    return False
                self.calls.append(now)
                return True
            def remaining(self):
                now = _time.time()
                self.calls = [t for t in self.calls if now - t < self.window]
                return max(0, self.max_calls - len(self.calls))
    """),
    "json_parser": textwrap.dedent("""\
        def tokenize(s):
            tokens = []
            i = 0
            while i < len(s):
                if s[i] in ' \\t\\n': i += 1
                elif s[i] in '{}[]:,':
                    tokens.append(s[i]); i += 1
                elif s[i] == '"':
                    j = i + 1
                    while j < len(s) and s[j] != '"': j += 1
                    tokens.append(s[i:j+1]); i = j + 1
                elif s[i].isdigit() or s[i] == '-':
                    j = i + 1
                    while j < len(s) and (s[j].isdigit() or s[j] == '.'): j += 1
                    tokens.append(s[i:j]); i = j
                else: i += 1
            return tokens
        def is_valid_json(s):
            tokens = tokenize(s)
            return len(tokens) > 0
    """),
    "task_scheduler": textwrap.dedent("""\
        class Task:
            def __init__(self, name, priority=0):
                self.name = name; self.priority = priority; self.done = False
            def complete(self): self.done = True
        class Scheduler:
            def __init__(self): self.tasks = []
            def add(self, task): self.tasks.append(task)
            def next(self):
                pending = [t for t in self.tasks if not t.done]
                if not pending: return None
                pending.sort(key=lambda t: -t.priority)
                return pending[0]
            def all_done(self): return all(t.done for t in self.tasks)
            def progress(self):
                total = len(self.tasks)
                if total == 0: return 1.0
                return sum(1 for t in self.tasks if t.done) / total
    """),
    "text_analyzer": textwrap.dedent("""\
        def word_freq(text):
            words = text.lower().split()
            freq = {}
            for w in words: freq[w] = freq.get(w, 0) + 1
            return freq
        def top_words(text, n=5):
            freq = word_freq(text)
            return sorted(freq.items(), key=lambda x: -x[1])[:n]
        def sentence_count(text):
            return sum(1 for c in text if c in '.!?')
        def avg_word_length(text):
            words = text.split()
            if not words: return 0
            return sum(len(w) for w in words) / len(words)
    """),
    "binary_tree": textwrap.dedent("""\
        class BST:
            def __init__(self): self.root = None
            def insert(self, val):
                if self.root is None:
                    self.root = {'val': val, 'left': None, 'right': None}
                else:
                    self._insert(self.root, val)
            def _insert(self, node, val):
                if val < node['val']:
                    if node['left'] is None:
                        node['left'] = {'val': val, 'left': None, 'right': None}
                    else: self._insert(node['left'], val)
                else:
                    if node['right'] is None:
                        node['right'] = {'val': val, 'left': None, 'right': None}
                    else: self._insert(node['right'], val)
            def contains(self, val):
                return self._find(self.root, val)
            def _find(self, node, val):
                if node is None: return False
                if val == node['val']: return True
                if val < node['val']: return self._find(node['left'], val)
                return self._find(node['right'], val)
    """),
    "retry_decorator": textwrap.dedent("""\
        import time as _time
        def retry(max_attempts=3, delay=0.1):
            def decorator(fn):
                def wrapper(*args, **kwargs):
                    last_err = None
                    for attempt in range(max_attempts):
                        try:
                            return fn(*args, **kwargs)
                        except Exception as e:
                            last_err = e
                            if attempt < max_attempts - 1:
                                _time.sleep(delay)
                    raise last_err
                return wrapper
            return decorator
        def always_fail():
            raise RuntimeError("fail")
        def sometimes_fail(p=0.5):
            import random
            if random.random() < p:
                raise RuntimeError("transient")
            return "ok"
    """),
    "matrix_math": textwrap.dedent("""\
        def zeros(rows, cols):
            return [[0]*cols for _ in range(rows)]
        def add(a, b):
            return [[a[i][j]+b[i][j] for j in range(len(a[0]))] for i in range(len(a))]
        def scale(m, s):
            return [[m[i][j]*s for j in range(len(m[0]))] for i in range(len(m))]
        def transpose(m):
            return [[m[j][i] for j in range(len(m))] for i in range(len(m[0]))]
        def trace(m):
            return sum(m[i][i] for i in range(min(len(m), len(m[0]))))
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 65: CI/CD Integration Experiments")
print("=" * 60)

results = []
for prog_id, source in CI_PROGRAMS.items():
    print(f"  [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        # Stage 1: Pre-merge (fast classify + evaluate)
        t1 = time.perf_counter()
        cls_objs = run_jugeo_json("classify", tmp)
        eval_objs = run_jugeo_json("evaluate", tmp)
        premerge_time = time.perf_counter() - t1

        # Stage 2: Post-merge (encode + descend)
        t2 = time.perf_counter()
        enc_objs = run_jugeo_json("encode", tmp)
        desc_objs = run_jugeo_json("descend", tmp)
        postmerge_time = time.perf_counter() - t2

        # Stage 3: Release (full bugs scan)
        t3 = time.perf_counter()
        bugs_objs = run_jugeo_json("bugs", tmp)
        release_time = time.perf_counter() - t3

        total_time = premerge_time + postmerge_time + release_time

        eval_data = eval_objs[0] if eval_objs else {}
        enc_data = enc_objs[0] if enc_objs else {}
        desc_data = desc_objs[0] if desc_objs else {}
        bugs_data = bugs_objs[0] if bugs_objs else {}

        files_enc = enc_data.get("files", [])
        n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
        sections = desc_data.get("sections_detail", [])
        props = sum(s.get("propositions", 0) for s in sections)
        ok_p = sum(s.get("ok", 0) for s in sections)
        verdict = desc_data.get("verdict", "unknown")
        bugs = bugs_data.get("count", 0) if isinstance(bugs_data, dict) else 0

        gate_pre = verdict in ("verified",) or (eval_data.get("trust", {}).get("aggregate_trust", "") != "")
        gate_post = verdict == "verified"
        gate_release = verdict == "verified" and bugs == 0

        rec = {
            "id": prog_id,
            "n_coords": n_coords, "props": props, "ok": ok_p,
            "verdict": verdict, "bugs": bugs,
            "premerge_s": round(premerge_time, 3),
            "postmerge_s": round(postmerge_time, 3),
            "release_s": round(release_time, 3),
            "total_s": round(total_time, 3),
            "gate_pre": gate_pre, "gate_post": gate_post, "gate_release": gate_release,
        }
        results.append(rec)
        g = "✓" if gate_release else "✗"
        print(f"pre={premerge_time:.2f}s post={postmerge_time:.2f}s "
              f"rel={release_time:.2f}s gate={g}")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": prog_id, "error": str(e)})
    finally:
        try: os.unlink(tmp)
        except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

ok_r = [r for r in results if "error" not in r]
n_total = len(CI_PROGRAMS)
n_ok = len(ok_r)

pre_times = [r["premerge_s"] for r in ok_r]
post_times = [r["postmerge_s"] for r in ok_r]
rel_times = [r["release_s"] for r in ok_r]
total_times = [r["total_s"] for r in ok_r]
coords_list = [r["n_coords"] for r in ok_r]
props_list = [r["props"] for r in ok_r]

gate_pre_pass = sum(1 for r in ok_r if r["gate_pre"])
gate_post_pass = sum(1 for r in ok_r if r["gate_post"])
gate_rel_pass = sum(1 for r in ok_r if r["gate_release"])

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper65.tex — AUTO-GENERATED by exp65_ci_cd_integration.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp65_ci_cd_integration.py",
    f"% Generated from {n_total} pipeline programs",
    "",
    f"\\newcommand{{\\ppLXVprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXVprogramsOk}}{{{n_ok}}}",
    "",
    f"\\newcommand{{\\ppLXVgatePrePass}}{{{gate_pre_pass}}}",
    f"\\newcommand{{\\ppLXVgatePrePassPct}}{{{round(100*gate_pre_pass/max(n_ok,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXVgatePostPass}}{{{gate_post_pass}}}",
    f"\\newcommand{{\\ppLXVgatePostPassPct}}{{{round(100*gate_post_pass/max(n_ok,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXVgateReleasePass}}{{{gate_rel_pass}}}",
    f"\\newcommand{{\\ppLXVgateReleasePassPct}}{{{round(100*gate_rel_pass/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXVpreMean}}{{{safe_mean(pre_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVpreMedian}}{{{safe_median(pre_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVpostMean}}{{{safe_mean(post_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVpostMedian}}{{{safe_median(post_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVreleaseMean}}{{{safe_mean(rel_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVreleaseMedian}}{{{safe_median(rel_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVtotalMean}}{{{safe_mean(total_times)}\\,s}}",
    f"\\newcommand{{\\ppLXVtotalTotal}}{{{round(sum(total_times),2)}\\,s}}",
    "",
    f"\\newcommand{{\\ppLXVcoordsMean}}{{{safe_mean(coords_list)}}}",
    f"\\newcommand{{\\ppLXVpropsMean}}{{{safe_mean(props_list)}}}",
    "",
    "% Per-program pipeline results",
]
for r in ok_r:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXVci{tag}Pre}}{{{r['premerge_s']}\\,s}}")
    lines.append(f"\\newcommand{{\\ppLXVci{tag}Post}}{{{r['postmerge_s']}\\,s}}")
    lines.append(f"\\newcommand{{\\ppLXVci{tag}Release}}{{{r['release_s']}\\,s}}")
    lines.append(f"\\newcommand{{\\ppLXVci{tag}Gate}}{{{'Pass' if r['gate_release'] else 'Fail'}}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper65.json"
with open(json_path, "w") as f:
    json.dump({"paper": 65, "programs": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
