#!/usr/bin/env python3
"""Paper 59 Experiment — Documentation Generation from Judgment Certificates.

Runs JuGeo on 10 diverse programs, extracting judgment-certificate richness
for automated documentation: coordinate quality, trust badges, function/class
counts, context bindings, and coverage metrics.
Generates papers/data-paper59.tex with \\ppLIX... macros.

Re-run: python3 experiments/exp59_documentation_generation.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper59.tex"

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

# ─── Trust badge mapping (Python API) ──────────────────────────────────────

def get_trust_badges():
    """Map trust level strings to documentation badge labels."""
    try:
        from jugeo import TrustAlgebra
        from jugeo.evidence.trust import TrustLevel
        TrustAlgebra()  # ensure initialised
        return {
            TrustLevel.MECHANICALLY_VERIFIED: "[PROOF]",
            TrustLevel.SOLVER_DISCHARGED: "[SOLVER]",
            TrustLevel.RUNTIME_WITNESSED: "[RUNTIME]",
            TrustLevel.ORACLE_PROPOSED: "[ORACLE]",
            TrustLevel.COPILOT_SUGGESTED: "[COPILOT]",
            TrustLevel.LOW: "[UNVERIFIED]",
            TrustLevel.CONTRADICTED: "[CONTRADICTED]",
        }
    except Exception:
        return {}

TRUST_BADGE_MAP = {
    "MECHANICALLY_VERIFIED": "[PROOF]",
    "SOLVER_DISCHARGED": "[SOLVER]",
    "RUNTIME_WITNESSED": "[RUNTIME]",
    "HUMAN_ATTESTED": "[ATTESTED]",
    "ORACLE_PROPOSED": "[ORACLE]",
    "COPILOT_SUGGESTED": "[COPILOT]",
    "unverified": "[UNVERIFIED]",
    "CONTRADICTED": "[CONTRADICTED]",
}

def badge_for_trust(trust_str):
    return TRUST_BADGE_MAP.get(trust_str, "[UNVERIFIED]")

def is_positive_trust(trust_str):
    return trust_str not in ("unverified", "CONTRADICTED", "LOW", "UNKNOWN")

# ─── 10 Test Programs for Documentation Generation ─────────────────────────

PROGRAMS = {
    "user_auth": textwrap.dedent("""\
        import hashlib
        class UserAuth:
            def __init__(self):
                self.users = {}
            def register(self, username, password):
                if username in self.users:
                    raise ValueError("user exists")
                self.users[username] = hashlib.sha256(password.encode()).hexdigest()
                return True
            def login(self, username, password):
                if username not in self.users:
                    return False
                return self.users[username] == hashlib.sha256(password.encode()).hexdigest()
            def change_password(self, username, old_pw, new_pw):
                if not self.login(username, old_pw):
                    raise PermissionError("bad credentials")
                self.users[username] = hashlib.sha256(new_pw.encode()).hexdigest()
                return True
    """),
    "db_connection_pool": textwrap.dedent("""\
        import threading
        class ConnectionPool:
            def __init__(self, max_size=5):
                self.max_size = max_size
                self.pool = []
                self.in_use = []
                self.lock = threading.Lock()
            def acquire(self):
                with self.lock:
                    if self.pool:
                        conn = self.pool.pop()
                    elif len(self.in_use) < self.max_size:
                        conn = self._create()
                    else:
                        raise RuntimeError("pool exhausted")
                    self.in_use.append(conn)
                    return conn
            def release(self, conn):
                with self.lock:
                    if conn in self.in_use:
                        self.in_use.remove(conn)
                        self.pool.append(conn)
            def _create(self):
                return {"id": len(self.in_use) + len(self.pool)}
            def size(self):
                return len(self.pool) + len(self.in_use)
    """),
    "file_walker": textwrap.dedent("""\
        import os
        def walk_files(root, ext=None):
            results = []
            for dirpath, dirnames, filenames in os.walk(root):
                for fn in filenames:
                    if ext is None or fn.endswith(ext):
                        results.append(os.path.join(dirpath, fn))
            return results
        def count_lines(filepath):
            with open(filepath) as f:
                return sum(1 for _ in f)
        def find_largest(root, ext=None):
            files = walk_files(root, ext)
            if not files:
                return None
            return max(files, key=lambda p: os.path.getsize(p))
        def total_size(root, ext=None):
            return sum(os.path.getsize(p) for p in walk_files(root, ext))
    """),
    "logging_framework": textwrap.dedent("""\
        import time as _time
        class Logger:
            LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
            def __init__(self, name, level="INFO"):
                self.name = name
                self.level = self.LEVELS.get(level, 1)
                self.handlers = []
            def add_handler(self, handler):
                self.handlers.append(handler)
            def _emit(self, level_name, msg):
                if self.LEVELS.get(level_name, 0) >= self.level:
                    record = f"[{_time.strftime('%H:%M:%S')}] {level_name} {self.name}: {msg}"
                    for h in self.handlers:
                        h(record)
            def debug(self, msg): self._emit("DEBUG", msg)
            def info(self, msg): self._emit("INFO", msg)
            def warn(self, msg): self._emit("WARN", msg)
            def error(self, msg): self._emit("ERROR", msg)
    """),
    "config_parser": textwrap.dedent("""\
        class ConfigParser:
            def __init__(self):
                self.data = {}
            def load(self, text):
                for line in text.strip().splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        raise ValueError(f"bad line: {line}")
                    key, val = line.split('=', 1)
                    self.data[key.strip()] = val.strip()
            def get(self, key, default=None):
                return self.data.get(key, default)
            def get_int(self, key, default=0):
                val = self.data.get(key)
                if val is None:
                    return default
                return int(val)
            def get_bool(self, key, default=False):
                val = self.data.get(key, "").lower()
                if val in ("true", "1", "yes"):
                    return True
                if val in ("false", "0", "no"):
                    return False
                return default
            def keys(self):
                return list(self.data.keys())
    """),
    "email_validator": textwrap.dedent("""\
        import re
        EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')
        def is_valid_email(email):
            if not isinstance(email, str):
                return False
            return bool(EMAIL_RE.match(email))
        def normalize_email(email):
            if not is_valid_email(email):
                raise ValueError("invalid email")
            local, domain = email.split('@')
            return f"{local.lower()}@{domain.lower()}"
        def extract_domain(email):
            if not is_valid_email(email):
                raise ValueError("invalid email")
            return email.split('@')[1].lower()
        def batch_validate(emails):
            return {e: is_valid_email(e) for e in emails}
    """),
    "datetime_utils": textwrap.dedent("""\
        from datetime import datetime, timedelta
        def days_between(d1, d2):
            delta = d2 - d1
            return abs(delta.days)
        def add_business_days(start, n):
            current = start
            added = 0
            while added < n:
                current += timedelta(days=1)
                if current.weekday() < 5:
                    added += 1
            return current
        def is_weekend(dt):
            return dt.weekday() >= 5
        def format_relative(dt):
            now = datetime.now()
            diff = now - dt
            if diff.days == 0:
                return "today"
            elif diff.days == 1:
                return "yesterday"
            elif diff.days < 7:
                return f"{diff.days} days ago"
            elif diff.days < 30:
                return f"{diff.days // 7} weeks ago"
            else:
                return f"{diff.days // 30} months ago"
    """),
    "caching_decorator": textwrap.dedent("""\
        import functools, time as _time
        def timed_cache(max_age_seconds=60):
            def decorator(func):
                cache = {}
                @functools.wraps(func)
                def wrapper(*args):
                    now = _time.time()
                    if args in cache:
                        result, ts = cache[args]
                        if now - ts < max_age_seconds:
                            return result
                    result = func(*args)
                    cache[args] = (result, now)
                    return result
                wrapper.cache_clear = lambda: cache.clear()
                wrapper.cache_info = lambda: {"size": len(cache)}
                return wrapper
            return decorator
        @timed_cache(max_age_seconds=30)
        def expensive_compute(n):
            total = 0
            for i in range(n):
                total += i * i
            return total
    """),
    "retry_mechanism": textwrap.dedent("""\
        import time as _time
        class RetryError(Exception):
            def __init__(self, attempts, last_error):
                self.attempts = attempts
                self.last_error = last_error
                super().__init__(f"failed after {attempts} attempts: {last_error}")
        def retry(func, max_attempts=3, delay=1.0, backoff=2.0):
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func()
                except Exception as e:
                    last_err = e
                    if attempt < max_attempts:
                        _time.sleep(delay)
                        delay *= backoff
            raise RetryError(max_attempts, last_err)
        def retry_decorator(max_attempts=3, delay=1.0, backoff=2.0):
            def decorator(func):
                def wrapper(*args, **kwargs):
                    return retry(lambda: func(*args, **kwargs),
                                 max_attempts, delay, backoff)
                return wrapper
            return decorator
    """),
    "observer_pattern": textwrap.dedent("""\
        class Observable:
            def __init__(self):
                self._observers = {}
            def subscribe(self, event, callback):
                self._observers.setdefault(event, []).append(callback)
            def unsubscribe(self, event, callback):
                if event in self._observers:
                    self._observers[event] = [
                        cb for cb in self._observers[event] if cb is not callback
                    ]
            def emit(self, event, *args, **kwargs):
                for cb in self._observers.get(event, []):
                    cb(*args, **kwargs)
            def listener_count(self, event=None):
                if event:
                    return len(self._observers.get(event, []))
                return sum(len(cbs) for cbs in self._observers.values())
        class EventBus(Observable):
            _instance = None
            @classmethod
            def instance(cls):
                if cls._instance is None:
                    cls._instance = cls()
                return cls._instance
            def clear(self):
                self._observers.clear()
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

def main():
    # Initialise trust badge mapping via Python API
    api_badges = get_trust_badges()
    if api_badges:
        print("  TrustAlgebra API loaded successfully")
    else:
        print("  TrustAlgebra API unavailable — using string-based badges")

    print("=" * 60)
    print("Paper 59: Documentation Generation Experiments")
    print("=" * 60)

    results = []
    for prog_id, source in PROGRAMS.items():
        print(f"  [{prog_id}] ...", end=" ", flush=True)
        tmp = write_temp(source)
        try:
            t0 = time.perf_counter()

            # 1. evaluate — quality, trust, lines, functions, complexity
            eval_objs = run_jugeo_json("evaluate", tmp)
            eval_data = eval_objs[0] if eval_objs else {}
            t_eval = time.perf_counter() - t0

            # 2. encode — SMT encoding details
            enc_objs = run_jugeo_json("encode", tmp)
            enc_data = enc_objs[0] if enc_objs else {}

            # 3. load — site structure (coordinates, morphisms, covering families, judgments, bindings)
            t_load_start = time.perf_counter()
            load_objs = run_jugeo_json("load", tmp)
            load_data = load_objs[0] if load_objs else {}
            t_load = time.perf_counter() - t_load_start

            # 4. classify — classification
            cls_objs = run_jugeo_json("classify", tmp)
            cls_data = cls_objs[0] if cls_objs else {}

            elapsed = time.perf_counter() - t0

            # ── Extract metrics from evaluate ──
            per_coord = eval_data.get("per_coordinate", [])
            total_functions = sum(c.get("functions", 0) for c in per_coord)
            total_lines = sum(c.get("lines", 0) for c in per_coord)
            qualities = [c.get("quality", 0) for c in per_coord]
            complexities = [c.get("complexity", 0) for c in per_coord]
            eval_trust = eval_data.get("trust", {})
            agg_trust = eval_trust.get("aggregate_trust", "unverified") if isinstance(eval_trust, dict) else "unverified"
            cover_q = eval_data.get("cover_quality", {})
            cover_score = cover_q.get("total_score", 0.0) if isinstance(cover_q, dict) else 0.0

            # ── Extract metrics from encode ──
            files_enc = enc_data.get("files", [])
            n_coords_enc = len(files_enc[0].get("coordinates", {})) if files_enc else 0
            enc_judgments = files_enc[0].get("judgments", []) if files_enc else []

            # Trust per coordinate from encode
            coord_trusts = []
            if files_enc:
                for cname, cdata in files_enc[0].get("coordinates", {}).items():
                    coord_trusts.append(cdata.get("trust", "unverified"))

            # Count propositions with positive trust (documentation items)
            props_total_enc = len(enc_judgments)
            props_ok_enc = sum(1 for t in coord_trusts if is_positive_trust(t))
            obstructions_enc = sum(1 for t in coord_trusts if t == "CONTRADICTED")

            # ── Extract metrics from load ──
            summary = load_data.get("summary", {})
            n_coords_load = summary.get("coordinates", 0)
            n_morphisms = summary.get("morphisms", 0)
            n_covers = summary.get("covering_families", 0)
            n_judgments = summary.get("judgments", 0)
            n_bindings = summary.get("context_bindings", 0)

            # ── Extract metrics from classify ──
            classification = cls_data.get("classification", {})
            category = classification.get("category", "UNKNOWN")
            site_struct = cls_data.get("site_structure", {})
            n_coords_cls = site_struct.get("coordinate_count", 0)

            # Count classes (rough heuristic from per_coordinate)
            total_classes = sum(1 for c in per_coord
                                if "." in c.get("coordinate", "") and
                                c.get("coordinate", "").split(".")[-1][0:1].isupper())

            # Assign badges
            badges = [badge_for_trust(t) for t in coord_trusts]
            badge_counts = Counter(badges)

            # Use the larger coordinate count
            n_coords = max(n_coords_enc, n_coords_load, n_coords_cls)

            rec = {
                "id": prog_id,
                "n_coords": n_coords,
                "n_morphisms": n_morphisms,
                "n_covers": n_covers,
                "n_judgments": n_judgments,
                "n_bindings": n_bindings,
                "props_total": props_total_enc,
                "props_ok": props_ok_enc,
                "obstructions": obstructions_enc,
                "total_functions": total_functions,
                "total_classes": total_classes,
                "total_lines": total_lines,
                "mean_quality": round(safe_mean(qualities), 4),
                "mean_complexity": round(safe_mean(complexities), 2),
                "cover_score": round(cover_score, 4),
                "agg_trust": agg_trust,
                "category": category,
                "badge_counts": dict(badge_counts),
                "verdict": "verified" if is_positive_trust(agg_trust) else "unverified",
                "eval_time": round(t_eval, 3),
                "load_time": round(t_load, 3),
                "time_s": round(elapsed, 3),
            }
            results.append(rec)
            print(f"coords={n_coords} funcs={total_functions} props={props_total_enc}/{props_ok_enc} "
                  f"judgments={n_judgments} t={elapsed:.2f}s")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"id": prog_id, "error": str(e), "time_s": 0})
        finally:
            try: os.unlink(tmp)
            except: pass

    # ─── Compute aggregates ─────────────────────────────────────────────────

    ok = [r for r in results if "error" not in r]
    n_total = len(PROGRAMS)
    n_ok = len(ok)

    coords_list = [r["n_coords"] for r in ok]
    judgments_list = [r["n_judgments"] for r in ok]
    bindings_list = [r["n_bindings"] for r in ok]
    quality_list = [r["mean_quality"] for r in ok]
    complexity_list = [r["mean_complexity"] for r in ok]
    eval_times = [r["eval_time"] for r in ok]
    load_times = [r["load_time"] for r in ok]
    props_total_sum = sum(r["props_total"] for r in ok)
    props_ok_sum = sum(r["props_ok"] for r in ok)
    obstruction_sum = sum(r["obstructions"] for r in ok)
    total_functions = sum(r["total_functions"] for r in ok)
    total_classes = sum(r["total_classes"] for r in ok)
    verified_count = sum(1 for r in ok if r["verdict"] == "verified")
    doc_coverage = round(props_ok_sum / max(props_total_sum, 1), 4)

    # ─── Generate LaTeX macros ──────────────────────────────────────────────

    print("\nGenerating", TEX_PATH)
    lines = [
        "% data-paper59.tex — AUTO-GENERATED by exp59_documentation_generation.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp59_documentation_generation.py",
        f"% Generated from {n_total} programs",
        "",
        f"\\newcommand{{\\ppLIXtotalPrograms}}{{{n_total}}}",
        f"\\newcommand{{\\ppLIXtotalProps}}{{{props_total_sum}}}",
        f"\\newcommand{{\\ppLIXtotalPropsOk}}{{{props_ok_sum}}}",
        f"\\newcommand{{\\ppLIXtotalObstructions}}{{{obstruction_sum}}}",
        "",
        f"\\newcommand{{\\ppLIXmeanCoords}}{{{safe_mean(coords_list)}}}",
        f"\\newcommand{{\\ppLIXmeanJudgments}}{{{safe_mean(judgments_list)}}}",
        f"\\newcommand{{\\ppLIXmeanBindings}}{{{safe_mean(bindings_list)}}}",
        f"\\newcommand{{\\ppLIXmeanQuality}}{{{safe_mean(quality_list)}}}",
        f"\\newcommand{{\\ppLIXmeanComplexity}}{{{safe_mean(complexity_list)}}}",
        "",
        f"\\newcommand{{\\ppLIXmeanEvalTime}}{{{safe_mean(eval_times)}\\,s}}",
        f"\\newcommand{{\\ppLIXmeanLoadTime}}{{{safe_mean(load_times)}\\,s}}",
        "",
        f"\\newcommand{{\\ppLIXdocCoverage}}{{{doc_coverage}}}",
        f"\\newcommand{{\\ppLIXtotalFunctions}}{{{total_functions}}}",
        f"\\newcommand{{\\ppLIXtotalClasses}}{{{total_classes}}}",
        f"\\newcommand{{\\ppLIXverifiedCount}}{{{verified_count}}}",
        "",
        "% Per-program documentation detail",
    ]

    for r in ok:
        tag = r["id"].replace("_", "")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Coords}}{{{r['n_coords']}}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Funcs}}{{{r['total_functions']}}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Props}}{{{r['props_total']}}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}PropsOk}}{{{r['props_ok']}}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Judgments}}{{{r['n_judgments']}}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Quality}}{{{r['mean_quality']}}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Time}}{{{r['time_s']}\\,s}}")
        lines.append(f"\\newcommand{{\\ppLIXdoc{tag}Verdict}}{{{r['verdict']}}}")

    with open(TEX_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Save JSON results
    json_path = ROOT / "experiments" / "results_paper59.json"
    with open(json_path, "w") as f:
        json.dump({"paper": 59, "programs": n_total, "results": results}, f, indent=2, default=str)

    macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
    print(f"  Wrote {macro_count} macros to {TEX_PATH}")
    print(f"  Wrote results to {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
