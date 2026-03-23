#!/usr/bin/env python3
"""Paper 64 Experiment — Team Workflow via Multi-Developer Jurisdiction.

Runs JuGeo on programs split into overlapping jurisdictions (sub-sites),
measuring independent verification, merge consistency, and delegation.
Generates papers/data-paper64.tex with \ppLXIV... macros.

Re-run: python3 experiments/exp64_team_workflow.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper64.tex"

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

# ─── Team Programs (each represents a multi-dev codebase) ──────────────────

TEAM_PROGRAMS = {
    "auth_module": {
        "full": textwrap.dedent("""\
            class AuthManager:
                def __init__(self): self.users = {}; self.sessions = {}
                def register(self, user, pw):
                    if user in self.users: raise ValueError("exists")
                    self.users[user] = pw
                def login(self, user, pw):
                    if self.users.get(user) != pw: raise ValueError("bad creds")
                    self.sessions[user] = True; return True
                def logout(self, user):
                    self.sessions.pop(user, None)
                def is_active(self, user): return self.sessions.get(user, False)
            class PermissionChecker:
                def __init__(self, auth): self.auth = auth; self.perms = {}
                def grant(self, user, perm):
                    self.perms.setdefault(user, set()).add(perm)
                def check(self, user, perm):
                    if not self.auth.is_active(user): return False
                    return perm in self.perms.get(user, set())
        """),
        "dev_a": textwrap.dedent("""\
            class AuthManager:
                def __init__(self): self.users = {}; self.sessions = {}
                def register(self, user, pw):
                    if user in self.users: raise ValueError("exists")
                    self.users[user] = pw
                def login(self, user, pw):
                    if self.users.get(user) != pw: raise ValueError("bad creds")
                    self.sessions[user] = True; return True
                def logout(self, user): self.sessions.pop(user, None)
                def is_active(self, user): return self.sessions.get(user, False)
        """),
        "dev_b": textwrap.dedent("""\
            class PermissionChecker:
                def __init__(self, auth): self.auth = auth; self.perms = {}
                def grant(self, user, perm):
                    self.perms.setdefault(user, set()).add(perm)
                def check(self, user, perm):
                    return perm in self.perms.get(user, set())
        """),
    },
    "data_pipeline": {
        "full": textwrap.dedent("""\
            def read_csv(path):
                with open(path) as f:
                    return [line.strip().split(',') for line in f]
            def filter_rows(rows, col, val):
                return [r for r in rows if r[col] == val]
            def transform(rows, col, fn):
                for r in rows: r[col] = fn(r[col])
                return rows
            def aggregate(rows, col):
                vals = [float(r[col]) for r in rows]
                return {'sum': sum(vals), 'mean': sum(vals)/len(vals), 'count': len(vals)}
            def write_csv(path, rows):
                with open(path, 'w') as f:
                    for r in rows: f.write(','.join(str(c) for c in r) + '\\n')
        """),
        "dev_a": textwrap.dedent("""\
            def read_csv(path):
                with open(path) as f:
                    return [line.strip().split(',') for line in f]
            def write_csv(path, rows):
                with open(path, 'w') as f:
                    for r in rows: f.write(','.join(str(c) for c in r) + '\\n')
        """),
        "dev_b": textwrap.dedent("""\
            def filter_rows(rows, col, val):
                return [r for r in rows if r[col] == val]
            def transform(rows, col, fn):
                for r in rows: r[col] = fn(r[col])
                return rows
            def aggregate(rows, col):
                vals = [float(r[col]) for r in rows]
                return {'sum': sum(vals), 'mean': sum(vals)/len(vals), 'count': len(vals)}
        """),
    },
    "web_handler": {
        "full": textwrap.dedent("""\
            class Router:
                def __init__(self): self.routes = {}
                def add(self, path, handler): self.routes[path] = handler
                def dispatch(self, path):
                    if path not in self.routes: raise KeyError(f"404: {path}")
                    return self.routes[path]()
            class Middleware:
                def __init__(self): self.stack = []
                def use(self, fn): self.stack.append(fn)
                def run(self, req):
                    for fn in self.stack: req = fn(req)
                    return req
        """),
        "dev_a": textwrap.dedent("""\
            class Router:
                def __init__(self): self.routes = {}
                def add(self, path, handler): self.routes[path] = handler
                def dispatch(self, path):
                    if path not in self.routes: raise KeyError(f"404: {path}")
                    return self.routes[path]()
        """),
        "dev_b": textwrap.dedent("""\
            class Middleware:
                def __init__(self): self.stack = []
                def use(self, fn): self.stack.append(fn)
                def run(self, req):
                    for fn in self.stack: req = fn(req)
                    return req
        """),
    },
    "cache_system": {
        "full": textwrap.dedent("""\
            class Cache:
                def __init__(self, maxsize=100):
                    self.store = {}; self.maxsize = maxsize
                def get(self, key):
                    return self.store.get(key)
                def put(self, key, val):
                    if len(self.store) >= self.maxsize:
                        oldest = next(iter(self.store))
                        del self.store[oldest]
                    self.store[key] = val
                def evict(self, key): self.store.pop(key, None)
                def clear(self): self.store.clear()
                def size(self): return len(self.store)
            def cached(cache, fn):
                def wrapper(*args):
                    key = str(args)
                    val = cache.get(key)
                    if val is not None: return val
                    result = fn(*args)
                    cache.put(key, result)
                    return result
                return wrapper
        """),
        "dev_a": textwrap.dedent("""\
            class Cache:
                def __init__(self, maxsize=100):
                    self.store = {}; self.maxsize = maxsize
                def get(self, key): return self.store.get(key)
                def put(self, key, val):
                    if len(self.store) >= self.maxsize:
                        oldest = next(iter(self.store))
                        del self.store[oldest]
                    self.store[key] = val
                def evict(self, key): self.store.pop(key, None)
                def clear(self): self.store.clear()
                def size(self): return len(self.store)
        """),
        "dev_b": textwrap.dedent("""\
            def cached(cache, fn):
                def wrapper(*args):
                    key = str(args)
                    val = cache.get(key)
                    if val is not None: return val
                    result = fn(*args)
                    cache.put(key, result)
                    return result
                return wrapper
        """),
    },
    "event_system": {
        "full": textwrap.dedent("""\
            class EventBus:
                def __init__(self): self.handlers = {}
                def on(self, event, fn):
                    self.handlers.setdefault(event, []).append(fn)
                def emit(self, event, *args):
                    for fn in self.handlers.get(event, []): fn(*args)
                def off(self, event, fn=None):
                    if fn: self.handlers.get(event, []).remove(fn)
                    else: self.handlers.pop(event, None)
            class Logger:
                def __init__(self, bus):
                    self.entries = []
                    bus.on('log', self.record)
                def record(self, msg):
                    self.entries.append(msg)
                def dump(self): return list(self.entries)
        """),
        "dev_a": textwrap.dedent("""\
            class EventBus:
                def __init__(self): self.handlers = {}
                def on(self, event, fn):
                    self.handlers.setdefault(event, []).append(fn)
                def emit(self, event, *args):
                    for fn in self.handlers.get(event, []): fn(*args)
                def off(self, event, fn=None):
                    if fn: self.handlers.get(event, []).remove(fn)
                    else: self.handlers.pop(event, None)
        """),
        "dev_b": textwrap.dedent("""\
            class Logger:
                def __init__(self, bus):
                    self.entries = []
                def record(self, msg):
                    self.entries.append(msg)
                def dump(self): return list(self.entries)
        """),
    },
    "math_library": {
        "full": textwrap.dedent("""\
            def gcd(a, b):
                while b: a, b = b, a % b
                return a
            def lcm(a, b): return a * b // gcd(a, b)
            def is_prime(n):
                if n < 2: return False
                for i in range(2, int(n**0.5)+1):
                    if n % i == 0: return False
                return True
            def primes_up_to(n):
                return [i for i in range(2, n+1) if is_prime(i)]
            def factorize(n):
                factors = []
                d = 2
                while d * d <= n:
                    while n % d == 0: factors.append(d); n //= d
                    d += 1
                if n > 1: factors.append(n)
                return factors
        """),
        "dev_a": textwrap.dedent("""\
            def gcd(a, b):
                while b: a, b = b, a % b
                return a
            def lcm(a, b): return a * b // gcd(a, b)
        """),
        "dev_b": textwrap.dedent("""\
            def is_prime(n):
                if n < 2: return False
                for i in range(2, int(n**0.5)+1):
                    if n % i == 0: return False
                return True
            def primes_up_to(n):
                return [i for i in range(2, n+1) if is_prime(i)]
            def factorize(n):
                factors = []; d = 2
                while d * d <= n:
                    while n % d == 0: factors.append(d); n //= d
                    d += 1
                if n > 1: factors.append(n)
                return factors
        """),
    },
    "validator": {
        "full": textwrap.dedent("""\
            def validate_email(email):
                if '@' not in email: return False
                local, domain = email.rsplit('@', 1)
                return len(local) > 0 and '.' in domain
            def validate_password(pw):
                if len(pw) < 8: return False
                has_upper = any(c.isupper() for c in pw)
                has_digit = any(c.isdigit() for c in pw)
                return has_upper and has_digit
            def validate_age(age):
                return isinstance(age, int) and 0 <= age <= 150
        """),
        "dev_a": textwrap.dedent("""\
            def validate_email(email):
                if '@' not in email: return False
                local, domain = email.rsplit('@', 1)
                return len(local) > 0 and '.' in domain
        """),
        "dev_b": textwrap.dedent("""\
            def validate_password(pw):
                if len(pw) < 8: return False
                has_upper = any(c.isupper() for c in pw)
                has_digit = any(c.isdigit() for c in pw)
                return has_upper and has_digit
            def validate_age(age):
                return isinstance(age, int) and 0 <= age <= 150
        """),
    },
    "config_system": {
        "full": textwrap.dedent("""\
            class Config:
                def __init__(self): self.data = {}
                def set(self, key, val): self.data[key] = val
                def get(self, key, default=None): return self.data.get(key, default)
                def has(self, key): return key in self.data
                def delete(self, key): self.data.pop(key, None)
                def keys(self): return list(self.data.keys())
            class Environment:
                def __init__(self, name, cfg):
                    self.name = name; self.cfg = cfg
                def is_production(self): return self.name == 'production'
                def get_setting(self, key): return self.cfg.get(key)
        """),
        "dev_a": textwrap.dedent("""\
            class Config:
                def __init__(self): self.data = {}
                def set(self, key, val): self.data[key] = val
                def get(self, key, default=None): return self.data.get(key, default)
                def has(self, key): return key in self.data
                def delete(self, key): self.data.pop(key, None)
                def keys(self): return list(self.data.keys())
        """),
        "dev_b": textwrap.dedent("""\
            class Environment:
                def __init__(self, name, cfg):
                    self.name = name; self.cfg = cfg
                def is_production(self): return self.name == 'production'
                def get_setting(self, key): return self.cfg.get(key)
        """),
    },
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 64: Team Workflow Experiments")
print("=" * 60)

results = []
for team_id, parts in TEAM_PROGRAMS.items():
    print(f"  [{team_id}] ...", end=" ", flush=True)
    full_src = parts["full"]
    deva_src = parts["dev_a"]
    devb_src = parts["dev_b"]
    tmp_full = write_temp(full_src)
    tmp_a = write_temp(deva_src)
    tmp_b = write_temp(devb_src)
    try:
        t0 = time.perf_counter()

        # Full verification
        desc_full = run_jugeo_json("descend", tmp_full)
        eval_full = run_jugeo_json("evaluate", tmp_full)
        enc_full = run_jugeo_json("encode", tmp_full)

        # Per-jurisdiction verification
        desc_a = run_jugeo_json("descend", tmp_a)
        desc_b = run_jugeo_json("descend", tmp_b)

        elapsed = time.perf_counter() - t0

        df = desc_full[0] if desc_full else {}
        da = desc_a[0] if desc_a else {}
        db = desc_b[0] if desc_b else {}
        ef = eval_full[0] if eval_full else {}
        en = enc_full[0] if enc_full else {}

        full_secs = df.get("sections_detail", [])
        full_props = sum(s.get("propositions", 0) for s in full_secs)
        full_ok = sum(s.get("ok", 0) for s in full_secs)

        a_secs = da.get("sections_detail", [])
        b_secs = db.get("sections_detail", [])
        a_props = sum(s.get("propositions", 0) for s in a_secs)
        b_props = sum(s.get("propositions", 0) for s in b_secs)
        a_ok = sum(s.get("ok", 0) for s in a_secs)
        b_ok = sum(s.get("ok", 0) for s in b_secs)

        files_enc = en.get("files", [])
        n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0

        full_verdict = df.get("verdict", "unknown")
        a_verdict = da.get("verdict", "unknown")
        b_verdict = db.get("verdict", "unknown")
        merge_ok = (a_verdict == "verified" and b_verdict == "verified"
                    and full_verdict == "verified")

        rec = {
            "id": team_id,
            "full_coords": n_coords,
            "full_props": full_props, "full_ok": full_ok,
            "full_verdict": full_verdict,
            "a_props": a_props, "a_ok": a_ok, "a_verdict": a_verdict,
            "b_props": b_props, "b_ok": b_ok, "b_verdict": b_verdict,
            "merge_consistent": merge_ok,
            "overlap_props": max(0, (a_props + b_props) - full_props),
            "time_s": round(elapsed, 3),
        }
        results.append(rec)
        m = "✓" if merge_ok else "✗"
        print(f"full={full_props}p a={a_props}p b={b_props}p merge={m} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": team_id, "error": str(e)})
    finally:
        for p in [tmp_full, tmp_a, tmp_b]:
            try: os.unlink(p)
            except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

ok = [r for r in results if "error" not in r]
n_total = len(TEAM_PROGRAMS)
n_ok = len(ok)
merge_count = sum(1 for r in ok if r["merge_consistent"])
full_verified = sum(1 for r in ok if r["full_verdict"] == "verified")

full_props_list = [r["full_props"] for r in ok]
full_coords_list = [r["full_coords"] for r in ok]
overlap_list = [r["overlap_props"] for r in ok]
times = [r["time_s"] for r in ok]

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper64.tex — AUTO-GENERATED by exp64_team_workflow.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp64_team_workflow.py",
    f"% Generated from {n_total} team programs",
    "",
    f"\\newcommand{{\\ppLXIVteamCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXIVteamsOk}}{{{n_ok}}}",
    f"\\newcommand{{\\ppLXIVfullVerified}}{{{full_verified}}}",
    f"\\newcommand{{\\ppLXIVfullVerifiedPct}}{{{round(100*full_verified/max(n_ok,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXIVmergeConsistent}}{{{merge_count}}}",
    f"\\newcommand{{\\ppLXIVmergeConsistentPct}}{{{round(100*merge_count/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXIVcoordsMean}}{{{safe_mean(full_coords_list)}}}",
    f"\\newcommand{{\\ppLXIVpropsMean}}{{{safe_mean(full_props_list)}}}",
    f"\\newcommand{{\\ppLXIVoverlapMean}}{{{safe_mean(overlap_list)}}}",
    "",
    f"\\newcommand{{\\ppLXIVtimeMean}}{{{safe_mean(times)}\\,s}}",
    f"\\newcommand{{\\ppLXIVtimeTotal}}{{{round(sum(times),2)}\\,s}}",
    f"\\newcommand{{\\ppLXIVtimeMin}}{{{round(min(times),3) if times else 0}\\,s}}",
    f"\\newcommand{{\\ppLXIVtimeMax}}{{{round(max(times),3) if times else 0}\\,s}}",
    "",
    "% Per-team results",
]
for r in ok:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}Coords}}{{{r['full_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}FullProps}}{{{r['full_props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}DevAProps}}{{{r['a_props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}DevBProps}}{{{r['b_props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}Overlap}}{{{r['overlap_props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}Merge}}{{{'Yes' if r['merge_consistent'] else 'No'}}}")
    lines.append(f"\\newcommand{{\\ppLXIVteam{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper64.json"
with open(json_path, "w") as f:
    json.dump({"paper": 64, "teams": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
