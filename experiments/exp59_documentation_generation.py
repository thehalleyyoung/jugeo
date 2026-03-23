#!/usr/bin/env python3
"""Paper 59 Experiment — Documentation Generation from Judgment Certificates.

Hypothesis: JuGeo judgment certificates provide sufficient information to
generate meaningful documentation with trust badges.

Re-run: python3 experiments/exp59_documentation_generation.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
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
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects

def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name

def cleanup(path):
    try: os.unlink(path)
    except OSError: pass

PROGRAMS = {
    "user_auth": '''\
class UserAuth:
    def __init__(self):
        self.users = {}
        self.sessions = {}

    def register(self, username, password):
        if username in self.users:
            raise ValueError("User exists")
        import hashlib
        hashed = hashlib.sha256(password.encode()).hexdigest()
        self.users[username] = {"password": hashed, "active": True}
        return True

    def login(self, username, password):
        user = self.users.get(username)
        if not user or not user["active"]:
            return None
        import hashlib
        if hashlib.sha256(password.encode()).hexdigest() != user["password"]:
            return None
        import uuid
        token = str(uuid.uuid4())
        self.sessions[token] = username
        return token

    def logout(self, token):
        return self.sessions.pop(token, None) is not None
''',
    "db_pool": '''\
class ConnectionPool:
    def __init__(self, max_size=10):
        self.max_size = max_size
        self.available = []
        self.in_use = set()

    def acquire(self):
        if self.available:
            conn = self.available.pop()
        elif len(self.in_use) < self.max_size:
            conn = self._create_connection()
        else:
            raise RuntimeError("Pool exhausted")
        self.in_use.add(id(conn))
        return conn

    def release(self, conn):
        if id(conn) in self.in_use:
            self.in_use.discard(id(conn))
            self.available.append(conn)

    def _create_connection(self):
        return {"id": len(self.in_use) + len(self.available), "active": True}

    def size(self):
        return len(self.available) + len(self.in_use)
''',
    "fs_walker": '''\
import os

def walk_directory(path, extensions=None):
    results = []
    for root, dirs, files in os.walk(path):
        for fname in files:
            if extensions is None or any(fname.endswith(e) for e in extensions):
                full = os.path.join(root, fname)
                results.append(full)
    return results

def file_sizes(path):
    sizes = {}
    for fpath in walk_directory(path):
        try:
            sizes[fpath] = os.path.getsize(fpath)
        except OSError:
            sizes[fpath] = -1
    return sizes

def find_duplicates(path):
    import hashlib
    hashes = {}
    for fpath in walk_directory(path):
        try:
            with open(fpath, 'rb') as f:
                h = hashlib.md5(f.read()).hexdigest()
            hashes.setdefault(h, []).append(fpath)
        except OSError:
            pass
    return {h: paths for h, paths in hashes.items() if len(paths) > 1}
''',
    "logging_fw": '''\
import datetime

class Logger:
    LEVELS = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3, 'CRITICAL': 4}

    def __init__(self, name, level='INFO'):
        self.name = name
        self.level = self.LEVELS.get(level, 1)
        self.handlers = []
        self.entries = []

    def add_handler(self, handler):
        self.handlers.append(handler)

    def log(self, level, message):
        if self.LEVELS.get(level, 0) >= self.level:
            entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "logger": self.name,
                "level": level,
                "message": message,
            }
            self.entries.append(entry)
            for handler in self.handlers:
                handler(entry)

    def debug(self, msg): self.log('DEBUG', msg)
    def info(self, msg): self.log('INFO', msg)
    def warning(self, msg): self.log('WARNING', msg)
    def error(self, msg): self.log('ERROR', msg)
''',
    "config_parser": '''\
def parse_ini(text):
    config = {}
    current_section = None
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current_section = line[1:-1].strip()
            config[current_section] = {}
        elif '=' in line and current_section is not None:
            key, value = line.split('=', 1)
            config[current_section][key.strip()] = value.strip()
    return config

def get_value(config, section, key, default=None):
    return config.get(section, {}).get(key, default)

def sections(config):
    return list(config.keys())
''',
    "email_validator": '''\
import re

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def extract_domain(email):
    if '@' not in email:
        return None
    return email.split('@')[1]

def normalize_email(email):
    local, domain = email.split('@')
    local = local.split('+')[0]
    return f"{local.lower()}@{domain.lower()}"

def validate_batch(emails):
    return {e: is_valid_email(e) for e in emails}
''',
    "datetime_utils": '''\
from datetime import datetime, timedelta

def days_between(date1, date2):
    d1 = datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.strptime(date2, "%Y-%m-%d")
    return abs((d2 - d1).days)

def add_business_days(start_date, num_days):
    current = datetime.strptime(start_date, "%Y-%m-%d")
    added = 0
    while added < num_days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current.strftime("%Y-%m-%d")

def is_weekend(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.weekday() >= 5

def format_relative(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    delta = (datetime.now() - d).days
    if delta == 0: return "today"
    if delta == 1: return "yesterday"
    if delta < 7: return f"{delta} days ago"
    if delta < 30: return f"{delta // 7} weeks ago"
    return f"{delta // 30} months ago"
''',
    "cache_decorator": '''\
def lru_cache(maxsize=128):
    def decorator(func):
        cache = {}
        order = []
        def wrapper(*args):
            key = args
            if key in cache:
                order.remove(key)
                order.append(key)
                return cache[key]
            result = func(*args)
            cache[key] = result
            order.append(key)
            if len(cache) > maxsize:
                oldest = order.pop(0)
                del cache[oldest]
            return result
        wrapper.cache_info = lambda: {"size": len(cache), "maxsize": maxsize}
        wrapper.cache_clear = lambda: (cache.clear(), order.clear())
        return wrapper
    return decorator

def memoize(func):
    cache = {}
    def wrapper(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]
    return wrapper
''',
    "retry_mechanism": '''\
import time as _time

def retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(Exception,)):
    def decorator(func):
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    _time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator

def with_timeout(timeout_seconds):
    def decorator(func):
        def wrapper(*args, **kwargs):
            import signal
            def handler(signum, frame):
                raise TimeoutError(f"Timed out after {timeout_seconds}s")
            old = signal.signal(signal.SIGALRM, handler)
            signal.alarm(timeout_seconds)
            try:
                return func(*args, **kwargs)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old)
        return wrapper
    return decorator
''',
    "observer": '''\
class Observable:
    def __init__(self):
        self._observers = {}

    def subscribe(self, event, callback):
        if event not in self._observers:
            self._observers[event] = []
        self._observers[event].append(callback)

    def unsubscribe(self, event, callback):
        if event in self._observers:
            self._observers[event] = [
                cb for cb in self._observers[event] if cb != callback
            ]

    def notify(self, event, *args, **kwargs):
        for callback in self._observers.get(event, []):
            callback(*args, **kwargs)

class Observer:
    def __init__(self, name):
        self.name = name
        self.received = []

    def on_event(self, *args, **kwargs):
        self.received.append((args, kwargs))
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        load_time = time.perf_counter() - t1

        eval_data = eval_objs[0] if eval_objs else {}
        per_coord = eval_data.get("per_coordinate", [])
        qualities = [c.get("quality", 0) for c in per_coord]
        complexities = [c.get("complexity", 0) for c in per_coord]
        total_funcs = sum(c.get("functions", 0) for c in per_coord)

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        judgments = summary.get("judgments", 0)
        bindings = summary.get("context_bindings", 0)

        desc_objs = run_jugeo("descend", tmp)
        desc_data = desc_objs[0] if desc_objs else {}
        verdict = desc_data.get("verdict", "unknown")
        sections = desc_data.get("sections_detail", [])
        props_total = sum(s.get("propositions", 0) for s in sections)
        props_ok = sum(s.get("ok", 0) for s in sections)
        obstructions = len(desc_data.get("obstructions", []))

        # Check for classes
        total_classes = sum(1 for c in per_coord if "class" in str(c.get("coordinate", "")).lower())

        return {
            "name": name,
            "eval_time": round(eval_time, 4),
            "load_time": round(load_time, 4),
            "coords": coords,
            "judgments": judgments,
            "bindings": bindings,
            "mean_quality": statistics.mean(qualities) if qualities else 0,
            "mean_complexity": statistics.mean(complexities) if complexities else 0,
            "total_functions": total_funcs,
            "total_classes": total_classes,
            "verdict": verdict,
            "props_total": props_total,
            "props_ok": props_ok,
            "obstructions": obstructions,
        }
    finally:
        cleanup(tmp)


def fmt_time(s):
    return f"{s*1000:.1f}\\,ms" if s < 0.01 else f"{s:.2f}\\,s"

def fmt_float(v, d=1):
    return f"{v:.{d}f}"

def fmt_pct(r):
    return f"{r*100:.1f}\\%"


def main():
    print("=" * 72)
    print("Paper 59: Documentation Generation from Judgment Certificates")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Coords: {m['coords']}, Judgments: {m['judgments']}")
        print(f"    Props: {m['props_ok']}/{m['props_total']}, Quality: {m['mean_quality']:.3f}")

    n = len(results)
    total_props = sum(r["props_total"] for r in results)
    total_props_ok = sum(r["props_ok"] for r in results)
    total_obs = sum(r["obstructions"] for r in results)
    mean_coords = statistics.mean([r["coords"] for r in results])
    mean_judgments = statistics.mean([r["judgments"] for r in results])
    mean_bindings = statistics.mean([r["bindings"] for r in results])
    mean_quality = statistics.mean([r["mean_quality"] for r in results])
    mean_complexity = statistics.mean([r["mean_complexity"] for r in results])
    mean_eval = statistics.mean([r["eval_time"] for r in results])
    mean_load = statistics.mean([r["load_time"] for r in results])
    doc_coverage = total_props_ok / total_props if total_props else 0
    total_funcs = sum(r["total_functions"] for r in results)
    total_classes = sum(r["total_classes"] for r in results)
    verified_count = sum(1 for r in results if r["verdict"] == "verified")

    tex_path = os.path.join(ROOT, "papers", "data-paper59.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper59.tex — AUTO-GENERATED by exp59_documentation_generation.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp59_documentation_generation.py\n\n")
        f.write(f"\\newcommand{{\\ppLIXtotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLIXtotalProps}}{{{total_props}}}\n")
        f.write(f"\\newcommand{{\\ppLIXtotalPropsOk}}{{{total_props_ok}}}\n")
        f.write(f"\\newcommand{{\\ppLIXtotalObstructions}}{{{total_obs}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanJudgments}}{{{fmt_float(mean_judgments)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanBindings}}{{{fmt_float(mean_bindings)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanQuality}}{{{fmt_float(mean_quality, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanComplexity}}{{{fmt_float(mean_complexity)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanEvalTime}}{{{fmt_time(mean_eval)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXmeanLoadTime}}{{{fmt_time(mean_load)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXdocCoverage}}{{{fmt_pct(doc_coverage)}}}\n")
        f.write(f"\\newcommand{{\\ppLIXtotalFunctions}}{{{total_funcs}}}\n")
        f.write(f"\\newcommand{{\\ppLIXtotalClasses}}{{{total_classes}}}\n")
        f.write(f"\\newcommand{{\\ppLIXverifiedCount}}{{{verified_count}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper59.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
