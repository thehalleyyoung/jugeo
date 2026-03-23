#!/usr/bin/env python3
"""Paper 69 Experiment — Security Analysis via Property Verification.

Runs JuGeo on programs with security-relevant patterns: input validation,
access control, data sanitization, and error handling. Measures security
property verification rates and trust distributions.
Generates papers/data-paper69.tex with \ppLXIX... macros.

Re-run: python3 experiments/exp69_security_analysis.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper69.tex"

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

# ─── Security-focused programs ──────────────────────────────────────────────

SECURITY_PROGRAMS = {
    "input_validation": textwrap.dedent("""\
        def sanitize_string(s):
            if not isinstance(s, str): raise TypeError("expected string")
            return s.strip().replace('<', '&lt;').replace('>', '&gt;')
        def validate_port(port):
            if not isinstance(port, int): raise TypeError("expected int")
            if port < 1 or port > 65535: raise ValueError("invalid port")
            return port
        def validate_url(url):
            if not isinstance(url, str): raise TypeError("expected string")
            if not url.startswith(('http://', 'https://')):
                raise ValueError("invalid scheme")
            return url
    """),
    "access_control": textwrap.dedent("""\
        class AccessControl:
            def __init__(self):
                self.roles = {}
                self.permissions = {}
            def add_role(self, user, role):
                self.roles.setdefault(user, set()).add(role)
            def add_permission(self, role, perm):
                self.permissions.setdefault(role, set()).add(perm)
            def check(self, user, perm):
                user_roles = self.roles.get(user, set())
                for role in user_roles:
                    if perm in self.permissions.get(role, set()):
                        return True
                return False
            def revoke_role(self, user, role):
                if user in self.roles:
                    self.roles[user].discard(role)
    """),
    "password_policy": textwrap.dedent("""\
        def check_length(pw):
            return len(pw) >= 8
        def check_uppercase(pw):
            return any(c.isupper() for c in pw)
        def check_digit(pw):
            return any(c.isdigit() for c in pw)
        def check_special(pw):
            return any(c in '!@#$%^&*()' for c in pw)
        def validate_password(pw):
            checks = [check_length, check_uppercase, check_digit, check_special]
            return all(check(pw) for check in checks)
        def password_strength(pw):
            score = sum(1 for check in [check_length, check_uppercase,
                        check_digit, check_special] if check(pw))
            return score / 4.0
    """),
    "data_sanitization": textwrap.dedent("""\
        def escape_html(s):
            replacements = {'&': '&amp;', '<': '&lt;', '>': '&gt;',
                           '"': '&quot;', "'": '&#x27;'}
            for old, new in replacements.items():
                s = s.replace(old, new)
            return s
        def strip_tags(s):
            result = []
            in_tag = False
            for c in s:
                if c == '<': in_tag = True
                elif c == '>': in_tag = False
                elif not in_tag: result.append(c)
            return ''.join(result)
        def truncate_safe(s, maxlen):
            if len(s) <= maxlen: return s
            return s[:maxlen-3] + '...'
    """),
    "token_manager": textwrap.dedent("""\
        import hashlib, time as _time
        class TokenManager:
            def __init__(self, secret):
                self.secret = secret
                self.tokens = {}
            def generate(self, user_id):
                payload = f"{user_id}:{_time.time()}:{self.secret}"
                token = hashlib.sha256(payload.encode()).hexdigest()
                self.tokens[token] = {'user': user_id, 'created': _time.time()}
                return token
            def validate(self, token):
                if token not in self.tokens: return False
                entry = self.tokens[token]
                if _time.time() - entry['created'] > 3600:
                    del self.tokens[token]
                    return False
                return True
            def revoke(self, token):
                self.tokens.pop(token, None)
    """),
    "rate_limiting": textwrap.dedent("""\
        class RateLimiter:
            def __init__(self, max_requests, window_s):
                self.max_req = max_requests
                self.window = window_s
                self.requests = {}
            def allow(self, client_id):
                import time as _t
                now = _t.time()
                reqs = self.requests.get(client_id, [])
                reqs = [t for t in reqs if now - t < self.window]
                if len(reqs) >= self.max_req:
                    self.requests[client_id] = reqs
                    return False
                reqs.append(now)
                self.requests[client_id] = reqs
                return True
            def remaining(self, client_id):
                import time as _t
                now = _t.time()
                reqs = self.requests.get(client_id, [])
                reqs = [t for t in reqs if now - t < self.window]
                return max(0, self.max_req - len(reqs))
    """),
    "secure_config": textwrap.dedent("""\
        class SecureConfig:
            def __init__(self):
                self._data = {}
                self._sensitive_keys = set()
            def set(self, key, value, sensitive=False):
                self._data[key] = value
                if sensitive: self._sensitive_keys.add(key)
            def get(self, key, default=None):
                return self._data.get(key, default)
            def dump_safe(self):
                result = {}
                for k, v in self._data.items():
                    if k in self._sensitive_keys:
                        result[k] = '***REDACTED***'
                    else:
                        result[k] = v
                return result
            def has(self, key): return key in self._data
    """),
    "audit_logger": textwrap.dedent("""\
        class AuditLogger:
            def __init__(self):
                self.entries = []
            def log(self, action, user, details=None):
                import time as _t
                entry = {
                    'timestamp': _t.time(),
                    'action': action,
                    'user': user,
                    'details': details or {}
                }
                self.entries.append(entry)
            def query(self, user=None, action=None):
                results = self.entries
                if user: results = [e for e in results if e['user'] == user]
                if action: results = [e for e in results if e['action'] == action]
                return results
            def count(self): return len(self.entries)
    """),
    "path_traversal_guard": textwrap.dedent("""\
        import os
        def safe_path(base, requested):
            full = os.path.normpath(os.path.join(base, requested))
            if not full.startswith(os.path.normpath(base)):
                raise ValueError("path traversal detected")
            return full
        def is_safe_filename(name):
            dangerous = ['..', '/', '\\\\', '~', '\\x00']
            return not any(d in name for d in dangerous)
        def sanitize_filename(name):
            return ''.join(c for c in name if c.isalnum() or c in '._-')
    """),
    "session_manager": textwrap.dedent("""\
        import hashlib, os
        class SessionManager:
            def __init__(self):
                self.sessions = {}
            def create(self, user_id):
                sid = hashlib.sha256(os.urandom(32)).hexdigest()
                self.sessions[sid] = {'user': user_id, 'data': {}}
                return sid
            def get(self, sid):
                if sid not in self.sessions: raise KeyError("invalid session")
                return self.sessions[sid]
            def destroy(self, sid):
                self.sessions.pop(sid, None)
            def set_data(self, sid, key, value):
                if sid not in self.sessions: raise KeyError("invalid session")
                self.sessions[sid]['data'][key] = value
            def active_count(self): return len(self.sessions)
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

print("=" * 60)
print("Paper 69: Security Analysis Experiments")
print("=" * 60)

results = []
for prog_id, source in SECURITY_PROGRAMS.items():
    print(f"  [{prog_id}] ...", end=" ", flush=True)
    tmp = write_temp(source)
    try:
        t0 = time.perf_counter()

        desc = run_jugeo_json("descend", tmp)
        enc = run_jugeo_json("encode", tmp)
        bugs = run_jugeo_json("bugs", tmp)
        cls = run_jugeo_json("classify", tmp)

        elapsed = time.perf_counter() - t0

        d = desc[0] if desc else {}
        e = enc[0] if enc else {}
        b = bugs[0] if bugs else {}
        c = cls[0] if cls else {}

        files_enc = e.get("files", [])
        n_coords = len(files_enc[0].get("coordinates", {})) if files_enc else 0
        secs = d.get("sections_detail", [])
        props = sum(s.get("propositions", 0) for s in secs)
        ok_p = sum(s.get("ok", 0) for s in secs)
        verdict = d.get("verdict", "unknown")
        trust = d.get("trust", "UNKNOWN")
        bug_count = b.get("count", 0) if isinstance(b, dict) else 0
        obs = len(d.get("obstructions", []))

        category = c.get("classification", {}).get("category", "UNKNOWN") if isinstance(c, dict) else "UNKNOWN"

        rec = {
            "id": prog_id,
            "n_coords": n_coords, "props": props, "ok": ok_p,
            "verdict": verdict, "trust": trust,
            "bugs": bug_count, "obstructions": obs,
            "category": category,
            "time_s": round(elapsed, 3),
        }
        results.append(rec)
        print(f"coords={n_coords} props={props}/{ok_p} bugs={bug_count} t={elapsed:.2f}s")
    except Exception as e:
        print(f"ERROR: {e}")
        results.append({"id": prog_id, "error": str(e)})
    finally:
        try: os.unlink(tmp)
        except: pass

# ─── Aggregates ─────────────────────────────────────────────────────────────

ok_r = [r for r in results if "error" not in r]
n_total = len(SECURITY_PROGRAMS)
n_ok = len(ok_r)
verified = sum(1 for r in ok_r if r["verdict"] == "verified")
total_bugs = sum(r["bugs"] for r in ok_r)
total_obs = sum(r["obstructions"] for r in ok_r)
total_props = sum(r["props"] for r in ok_r)
total_ok = sum(r["ok"] for r in ok_r)

coords_list = [r["n_coords"] for r in ok_r]
props_list = [r["props"] for r in ok_r]
times = [r["time_s"] for r in ok_r]

prop_pass_rate = round(100 * total_ok / max(total_props, 1), 1)

# ─── Generate LaTeX ────────────────────────────────────────────────────────

print("\nGenerating", TEX_PATH)
lines = [
    "% data-paper69.tex — AUTO-GENERATED by exp69_security_analysis.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp69_security_analysis.py",
    f"% Generated from {n_total} security-focused programs",
    "",
    f"\\newcommand{{\\ppLXIXprogramCount}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXIXprogramsOk}}{{{n_ok}}}",
    f"\\newcommand{{\\ppLXIXverified}}{{{verified}}}",
    f"\\newcommand{{\\ppLXIXverifiedPct}}{{{round(100*verified/max(n_ok,1),1)}\\%}}",
    "",
    f"\\newcommand{{\\ppLXIXpropsTotal}}{{{total_props}}}",
    f"\\newcommand{{\\ppLXIXpropsOk}}{{{total_ok}}}",
    f"\\newcommand{{\\ppLXIXpropPassRate}}{{{prop_pass_rate}\\%}}",
    f"\\newcommand{{\\ppLXIXpropsMean}}{{{safe_mean(props_list)}}}",
    "",
    f"\\newcommand{{\\ppLXIXbugsTotal}}{{{total_bugs}}}",
    f"\\newcommand{{\\ppLXIXobsTotal}}{{{total_obs}}}",
    f"\\newcommand{{\\ppLXIXcoordsMean}}{{{safe_mean(coords_list)}}}",
    "",
    f"\\newcommand{{\\ppLXIXtimeMean}}{{{safe_mean(times)}\\,s}}",
    f"\\newcommand{{\\ppLXIXtimeTotal}}{{{round(sum(times),2)}\\,s}}",
    f"\\newcommand{{\\ppLXIXtimeMin}}{{{round(min(times),3) if times else 0}\\,s}}",
    f"\\newcommand{{\\ppLXIXtimeMax}}{{{round(max(times),3) if times else 0}\\,s}}",
    "",
    "% Per-program security results",
]
for r in ok_r:
    tag = r["id"].replace("_", "")
    lines.append(f"\\newcommand{{\\ppLXIXsec{tag}Coords}}{{{r['n_coords']}}}")
    lines.append(f"\\newcommand{{\\ppLXIXsec{tag}Props}}{{{r['props']}}}")
    lines.append(f"\\newcommand{{\\ppLXIXsec{tag}Ok}}{{{r['ok']}}}")
    lines.append(f"\\newcommand{{\\ppLXIXsec{tag}Bugs}}{{{r['bugs']}}}")
    lines.append(f"\\newcommand{{\\ppLXIXsec{tag}Verdict}}{{{r['verdict']}}}")
    lines.append(f"\\newcommand{{\\ppLXIXsec{tag}Time}}{{{r['time_s']}\\,s}}")

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

json_path = ROOT / "experiments" / "results_paper69.json"
with open(json_path, "w") as f:
    json.dump({"paper": 69, "programs": n_total, "results": results}, f, indent=2, default=str)

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")
print("Done.")
