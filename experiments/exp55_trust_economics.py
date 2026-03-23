#!/usr/bin/env python3
"""Paper 55 Experiment — Trust Economics: Economic Models for Trust Allocation.

Hypothesis: JuGeo's trust algebra and maturity cycles produce economically
meaningful trust scores across security-sensitive programs.

Re-run: python3 experiments/exp55_trust_economics.py
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
    "bank_account": '''\
class BankAccount:
    def __init__(self, owner, balance=0):
        self.owner = owner
        self.balance = balance
        self.history = []

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError("Deposit must be positive")
        self.balance += amount
        self.history.append(('deposit', amount))

    def withdraw(self, amount):
        if amount <= 0:
            raise ValueError("Withdrawal must be positive")
        if amount > self.balance:
            raise ValueError("Insufficient funds")
        self.balance -= amount
        self.history.append(('withdraw', amount))

    def get_balance(self):
        return self.balance
''',
    "auth_module": '''\
import hashlib

def hash_password(password, salt="default_salt"):
    return hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(password, hashed, salt="default_salt"):
    return hash_password(password, salt) == hashed

def create_user(username, password):
    hashed = hash_password(password)
    return {"username": username, "password_hash": hashed, "active": True}

def authenticate(user, password):
    if not user.get("active", False):
        return False
    return verify_password(password, user["password_hash"])
''',
    "rate_limiter": '''\
import time as _time

class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = []

    def allow(self):
        now = _time.time()
        self.requests = [t for t in self.requests if now - t < self.window]
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True
        return False

    def remaining(self):
        now = _time.time()
        self.requests = [t for t in self.requests if now - t < self.window]
        return max(0, self.max_requests - len(self.requests))
''',
    "xor_cipher": '''\
def xor_encrypt(plaintext, key):
    result = []
    for i, ch in enumerate(plaintext):
        result.append(chr(ord(ch) ^ ord(key[i % len(key)])))
    return ''.join(result)

def xor_decrypt(ciphertext, key):
    return xor_encrypt(ciphertext, key)

def rotate_key(key, n):
    n = n % len(key)
    return key[n:] + key[:n]
''',
    "acl": '''\
class AccessControl:
    def __init__(self):
        self.permissions = {}

    def grant(self, user, resource, permission):
        key = (user, resource)
        if key not in self.permissions:
            self.permissions[key] = set()
        self.permissions[key].add(permission)

    def revoke(self, user, resource, permission):
        key = (user, resource)
        if key in self.permissions:
            self.permissions[key].discard(permission)

    def check(self, user, resource, permission):
        key = (user, resource)
        return permission in self.permissions.get(key, set())

    def list_permissions(self, user, resource):
        key = (user, resource)
        return list(self.permissions.get(key, set()))
''',
    "voting": '''\
class VotingSystem:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.votes = {c: 0 for c in candidates}
        self.voters = set()

    def cast_vote(self, voter_id, candidate):
        if voter_id in self.voters:
            raise ValueError("Already voted")
        if candidate not in self.votes:
            raise ValueError("Invalid candidate")
        self.votes[candidate] += 1
        self.voters.add(voter_id)

    def tally(self):
        return dict(self.votes)

    def winner(self):
        if not self.voters:
            return None
        return max(self.votes, key=self.votes.get)

    def turnout(self, total_eligible):
        return len(self.voters) / total_eligible if total_eligible > 0 else 0
''',
    "contract_checker": '''\
def requires(condition, message="Precondition failed"):
    if not condition:
        raise ValueError(message)

def ensures(condition, message="Postcondition failed"):
    if not condition:
        raise AssertionError(message)

def checked_divide(a, b):
    requires(b != 0, "Division by zero")
    result = a / b
    ensures(isinstance(result, (int, float)), "Result must be numeric")
    return result

def checked_sqrt(x):
    requires(x >= 0, "Cannot take sqrt of negative")
    result = x ** 0.5
    ensures(result >= 0, "sqrt must be non-negative")
    return result

def checked_index(lst, i):
    requires(0 <= i < len(lst), f"Index {i} out of bounds")
    return lst[i]
''',
    "audit_logger": '''\
from datetime import datetime

class AuditLogger:
    def __init__(self):
        self.entries = []

    def log(self, action, user, details=None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "user": user,
            "details": details,
        }
        self.entries.append(entry)
        return entry

    def query(self, user=None, action=None):
        results = self.entries
        if user:
            results = [e for e in results if e["user"] == user]
        if action:
            results = [e for e in results if e["action"] == action]
        return results

    def count(self):
        return len(self.entries)
''',
    "checksum": '''\
def checksum_xor(data):
    result = 0
    for byte in data:
        result ^= byte
    return result

def checksum_sum(data, modulus=256):
    return sum(data) % modulus

def crc_simple(data, polynomial=0x1021):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ polynomial
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc

def verify_checksum(data, expected, method='xor'):
    if method == 'xor':
        return checksum_xor(data) == expected
    elif method == 'sum':
        return checksum_sum(data) == expected
    return False
''',
    "signature_stub": '''\
import hashlib

def sign_message(message, private_key):
    combined = message + private_key
    return hashlib.sha256(combined.encode()).hexdigest()

def verify_signature(message, signature, private_key):
    expected = sign_message(message, private_key)
    return expected == signature

def create_keypair(seed):
    private = hashlib.sha256(seed.encode()).hexdigest()
    public = hashlib.sha256(private.encode()).hexdigest()
    return {"private": private, "public": public}
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        enc_objs = run_jugeo("encode", tmp)
        encode_time = time.perf_counter() - t1

        eval_data = eval_objs[0] if eval_objs else {}
        per_coord = eval_data.get("per_coordinate", [])
        qualities = [c.get("quality", 0) for c in per_coord]

        enc_data = enc_objs[0] if enc_objs else {}
        enc_files = enc_data.get("files", [{}])
        enc_file = enc_files[0] if enc_files else {}
        coordinates = enc_file.get("coordinates", {})
        discharge_count = sum(1 for c in coordinates.values() if c.get("assertions", 0) > 0)
        total_assertions = sum(c.get("assertions", 0) for c in coordinates.values())

        # Run maturity cycle
        from jugeo.maturity import CyclicSystemCoordinator
        coord = CyclicSystemCoordinator.create(name)
        record, transitions = coord.run_full_cycle({'source': source})
        metrics = coord.get_metrics().to_dict()

        # Trust algebra ops
        from jugeo import TrustAlgebra
        from jugeo.evidence.trust import TrustLevel
        ta = TrustAlgebra()
        levels = list(TrustLevel)
        ops_count = 0
        admissible_count = 0
        sheaf_pass = 0
        yields = []
        for lev in levels:
            try:
                if ta.is_admissible({"level": lev.name, "evidence": "test"}):
                    admissible_count += 1
            except Exception:
                pass
            ops_count += 1
            try:
                y = ta.theorem_yield(lev)
                yields.append(y if isinstance(y, (int, float)) else 0)
            except Exception:
                yields.append(0)
        for i in range(len(levels)):
            for j in range(len(levels)):
                ta.compose(levels[i], levels[j])
                ops_count += 1
        try:
            assignment = {f"coord_{i}": levels[i % len(levels)] for i in range(3)}
            sheaf_result = ta.sheaf_condition_check(None, assignment)
            sheaf_pass = 1 if sheaf_result else 0
        except Exception:
            sheaf_pass = 0

        return {
            "name": name,
            "eval_time": round(eval_time, 4),
            "mean_quality": statistics.mean(qualities) if qualities else 0,
            "discharge_count": discharge_count,
            "total_assertions": total_assertions,
            "trust_score": metrics.get("mean_trust_score", 0),
            "cycle_duration": metrics.get("mean_cycle_duration", 0),
            "success_rate": metrics.get("success_rate", 0),
            "obstruction_rate": metrics.get("obstruction_rate", 0),
            "phases_completed": len(record.phases_completed),
            "ops_count": ops_count,
            "admissible_count": admissible_count,
            "sheaf_pass": sheaf_pass,
            "mean_yield": statistics.mean(yields) if yields else 0,
            "total_props": total_assertions,
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
    print("Paper 55: Trust Economics — Economic Models for Trust Allocation")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Trust: {m['trust_score']:.2f}, Quality: {m['mean_quality']:.3f}")
        print(f"    Discharge: {m['discharge_count']}, Assertions: {m['total_assertions']}")

    n = len(results)
    mean_trust = statistics.mean([r["trust_score"] for r in results])
    mean_cycle = statistics.mean([r["cycle_duration"] for r in results])
    success_rate = statistics.mean([r["success_rate"] for r in results])
    obs_rate = statistics.mean([r["obstruction_rate"] for r in results])
    mean_phases = statistics.mean([r["phases_completed"] for r in results])
    total_judgments = sum(r["total_props"] for r in results)
    total_discharge = sum(r["discharge_count"] for r in results)
    discharge_pct = total_discharge / max(1, sum(len(PROGRAMS[r["name"]].splitlines()) for r in results))
    mean_quality = statistics.mean([r["mean_quality"] for r in results])
    total_ops = sum(r["ops_count"] for r in results)
    admissible_pct = statistics.mean([r["admissible_count"] / 8 for r in results])
    sheaf_passes = sum(r["sheaf_pass"] for r in results)
    mean_yield = statistics.mean([r["mean_yield"] for r in results])
    mean_eval = statistics.mean([r["eval_time"] for r in results])

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:      {n}")
    print(f"  Mean trust:    {mean_trust:.3f}")
    print(f"  Success rate:  {fmt_pct(success_rate)}")

    tex_path = os.path.join(ROOT, "papers", "data-paper55.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper55.tex — AUTO-GENERATED by exp55_trust_economics.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp55_trust_economics.py\n\n")
        f.write(f"\\newcommand{{\\ppLVtotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLVmeanTrustScore}}{{{fmt_float(mean_trust, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLVmeanCycleDuration}}{{{fmt_time(mean_cycle)}}}\n")
        f.write(f"\\newcommand{{\\ppLVsuccessRate}}{{{fmt_pct(success_rate)}}}\n")
        f.write(f"\\newcommand{{\\ppLVobstructionRate}}{{{fmt_pct(obs_rate)}}}\n")
        f.write(f"\\newcommand{{\\ppLVmeanPhases}}{{{fmt_float(mean_phases)}}}\n")
        f.write(f"\\newcommand{{\\ppLVtotalJudgments}}{{{total_judgments}}}\n")
        f.write(f"\\newcommand{{\\ppLVdischargeCount}}{{{total_discharge}}}\n")
        f.write(f"\\newcommand{{\\ppLVdischargePct}}{{{fmt_pct(discharge_pct)}}}\n")
        f.write(f"\\newcommand{{\\ppLVmeanQuality}}{{{fmt_float(mean_quality, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLVtrustLatticeOps}}{{{total_ops}}}\n")
        f.write(f"\\newcommand{{\\ppLVadmissiblePct}}{{{fmt_pct(admissible_pct)}}}\n")
        f.write(f"\\newcommand{{\\ppLVsheafCheckPass}}{{{sheaf_passes}}}\n")
        f.write(f"\\newcommand{{\\ppLVmeanYield}}{{{fmt_float(mean_yield, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLVmeanEvalTime}}{{{fmt_time(mean_eval)}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper55.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
