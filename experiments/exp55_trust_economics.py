#!/usr/bin/env python3
"""Paper 55 Experiment — Trust Economics: Economic Models for Trust Allocation.

Studies trust algebra operations, maturity cycle economics, and per-coordinate
quality across programs with varying trust-relevant patterns.

Every number is produced by calling the ``python3 -m jugeo`` CLI as a subprocess
or via the public Python API.
Re-run: python3 experiments/exp55_trust_economics.py
Outputs: papers/data-paper55.tex  (LaTeX macros with \\ppLV… prefix)
         experiments/results_paper55.json
"""
import ast, json, os, random, statistics, subprocess, sys, tempfile, time

random.seed(42)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from jugeo import TrustAlgebra
from jugeo.evidence.trust import TrustLevel
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.geometry import SiteBuilder
from jugeo.maturity import CyclicSystemCoordinator

# ── helpers ──────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
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


def write_temp(source):
    """Write source to a temp .py file and return its path."""
    f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ── test programs ────────────────────────────────────────────────────────

PROGRAMS = {
    "bank_account": '''
class BankAccount:
    def __init__(self, owner, balance=0):
        self.owner = owner
        self.balance = balance
        self.history = []

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError("Deposit must be positive")
        self.balance += amount
        self.history.append(("deposit", amount))
        return self.balance

    def withdraw(self, amount):
        if amount <= 0:
            raise ValueError("Withdrawal must be positive")
        if amount > self.balance:
            raise ValueError("Insufficient funds")
        self.balance -= amount
        self.history.append(("withdraw", amount))
        return self.balance

    def check_balance(self):
        return self.balance

    def transfer(self, other, amount):
        self.withdraw(amount)
        other.deposit(amount)
        return True
''',

    "auth_module": '''
import hashlib

def hash_password(password, salt="default_salt"):
    return hashlib.sha256((salt + password).encode()).hexdigest()

def verify_password(password, hashed, salt="default_salt"):
    return hash_password(password, salt) == hashed

class AuthManager:
    def __init__(self):
        self.users = {}
        self.sessions = {}

    def register(self, username, password):
        if username in self.users:
            return False
        self.users[username] = hash_password(password)
        return True

    def login(self, username, password):
        if username not in self.users:
            return None
        if not verify_password(password, self.users[username]):
            return None
        token = hashlib.sha256(f"{username}{id(self)}".encode()).hexdigest()[:16]
        self.sessions[token] = username
        return token

    def validate_session(self, token):
        return self.sessions.get(token)
''',

    "rate_limiter": '''
import time as _time

class RateLimiter:
    def __init__(self, max_calls, window_seconds):
        self.max_calls = max_calls
        self.window = window_seconds
        self.calls = {}

    def allow(self, client_id):
        now = _time.time()
        if client_id not in self.calls:
            self.calls[client_id] = []
        self.calls[client_id] = [
            t for t in self.calls[client_id] if now - t < self.window
        ]
        if len(self.calls[client_id]) >= self.max_calls:
            return False
        self.calls[client_id].append(now)
        return True

    def remaining(self, client_id):
        now = _time.time()
        recent = [t for t in self.calls.get(client_id, []) if now - t < self.window]
        return max(0, self.max_calls - len(recent))

    def reset(self, client_id):
        self.calls.pop(client_id, None)
''',

    "xor_cipher": '''
def xor_encrypt(plaintext, key):
    key_bytes = key.encode() if isinstance(key, str) else key
    plain_bytes = plaintext.encode() if isinstance(plaintext, str) else plaintext
    encrypted = bytearray()
    for i, b in enumerate(plain_bytes):
        encrypted.append(b ^ key_bytes[i % len(key_bytes)])
    return bytes(encrypted)

def xor_decrypt(ciphertext, key):
    return xor_encrypt(ciphertext, key)

def rotate_key(key, n):
    key_bytes = key.encode() if isinstance(key, str) else key
    n = n % len(key_bytes)
    return bytes(key_bytes[n:] + key_bytes[:n])

def double_encrypt(plaintext, key1, key2):
    first_pass = xor_encrypt(plaintext, key1)
    return xor_encrypt(first_pass, key2)
''',

    "access_control": '''
class Permission:
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"

class ACL:
    def __init__(self):
        self.rules = {}

    def grant(self, user, resource, permission):
        key = (user, resource)
        if key not in self.rules:
            self.rules[key] = set()
        self.rules[key].add(permission)

    def revoke(self, user, resource, permission):
        key = (user, resource)
        if key in self.rules:
            self.rules[key].discard(permission)

    def check(self, user, resource, permission):
        key = (user, resource)
        return permission in self.rules.get(key, set())

    def list_permissions(self, user, resource):
        return list(self.rules.get((user, resource), set()))

    def has_admin(self, user, resource):
        return self.check(user, resource, Permission.ADMIN)
''',

    "voting_system": '''
class Ballot:
    def __init__(self, voter_id, choices):
        self.voter_id = voter_id
        self.choices = choices
        self.timestamp = None

class VotingSystem:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.ballots = {}
        self.closed = False

    def cast_vote(self, voter_id, choice):
        if self.closed:
            raise ValueError("Voting is closed")
        if choice not in self.candidates:
            raise ValueError(f"Invalid candidate: {choice}")
        if voter_id in self.ballots:
            raise ValueError("Already voted")
        self.ballots[voter_id] = Ballot(voter_id, choice)
        return True

    def tally(self):
        counts = {c: 0 for c in self.candidates}
        for ballot in self.ballots.values():
            counts[ballot.choices] += 1
        return counts

    def winner(self):
        counts = self.tally()
        return max(counts, key=counts.get)

    def close(self):
        self.closed = True
        return self.tally()
''',

    "contract_checker": '''
def requires(condition, message="Precondition failed"):
    if not condition:
        raise AssertionError(message)

def ensures(condition, message="Postcondition failed"):
    if not condition:
        raise AssertionError(message)

def checked_divide(a, b):
    requires(b != 0, "Division by zero")
    result = a / b
    ensures(isinstance(result, float), "Result must be float")
    return result

def checked_sqrt(x):
    requires(x >= 0, "Cannot take sqrt of negative")
    result = x ** 0.5
    ensures(result >= 0, "sqrt must be non-negative")
    ensures(abs(result * result - x) < 1e-10, "sqrt accuracy")
    return result

def checked_factorial(n):
    requires(isinstance(n, int) and n >= 0, "n must be non-negative integer")
    result = 1
    for i in range(1, n + 1):
        result *= i
    ensures(result >= 1, "Factorial must be >= 1")
    return result

def invariant_check(obj, predicate, message="Invariant violated"):
    if not predicate(obj):
        raise AssertionError(message)
''',

    "audit_logger": '''
import json as _json
from datetime import datetime

class AuditEntry:
    def __init__(self, action, user, resource, success):
        self.action = action
        self.user = user
        self.resource = resource
        self.success = success
        self.timestamp = datetime.now().isoformat()

    def to_dict(self):
        return {
            "action": self.action,
            "user": self.user,
            "resource": self.resource,
            "success": self.success,
            "timestamp": self.timestamp,
        }

class AuditLogger:
    def __init__(self):
        self.entries = []

    def log(self, action, user, resource, success=True):
        entry = AuditEntry(action, user, resource, success)
        self.entries.append(entry)
        return entry

    def query(self, user=None, action=None):
        results = self.entries
        if user:
            results = [e for e in results if e.user == user]
        if action:
            results = [e for e in results if e.action == action]
        return results

    def export_json(self):
        return _json.dumps([e.to_dict() for e in self.entries], indent=2)

    def count_failures(self):
        return sum(1 for e in self.entries if not e.success)
''',

    "checksum_calc": '''
def adler32(data):
    a = 1
    b = 0
    MOD = 65521
    for byte in data:
        a = (a + byte) % MOD
        b = (b + a) % MOD
    return (b << 16) | a

def fletcher16(data):
    sum1 = 0
    sum2 = 0
    for byte in data:
        sum1 = (sum1 + byte) % 255
        sum2 = (sum2 + sum1) % 255
    return (sum2 << 8) | sum1

def simple_hash(data, modulus=1000003):
    h = 0
    for i, byte in enumerate(data):
        h = (h * 31 + byte) % modulus
    return h

def verify_checksum(data, expected, algorithm="adler32"):
    funcs = {"adler32": adler32, "fletcher16": fletcher16, "simple_hash": simple_hash}
    if algorithm not in funcs:
        raise ValueError(f"Unknown algorithm: {algorithm}")
    return funcs[algorithm](data) == expected
''',

    "digital_signature": '''
import hashlib

def generate_keypair(seed):
    private = hashlib.sha256(seed.encode()).hexdigest()
    public = hashlib.sha256(private.encode()).hexdigest()
    return private, public

def sign_message(message, private_key):
    combined = f"{private_key}:{message}"
    signature = hashlib.sha256(combined.encode()).hexdigest()
    return signature

def verify_signature(message, signature, public_key):
    expected_private = None
    rehash = hashlib.sha256(signature.encode()).hexdigest()
    return len(signature) == 64

def sign_document(doc_lines, private_key):
    content_hash = hashlib.sha256("\\n".join(doc_lines).encode()).hexdigest()
    return sign_message(content_hash, private_key)

def create_certificate(subject, issuer_key):
    sig = sign_message(subject, issuer_key)
    return {"subject": subject, "signature": sig, "valid": True}
''',
}

# ── trust algebra experiment ─────────────────────────────────────────────

TRUST_LEVELS = [
    TrustLevel.CONTRADICTED,
    TrustLevel.LOW,
    TrustLevel.COPILOT_SUGGESTED,
    TrustLevel.ORACLE_PROPOSED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.HUMAN_ATTESTED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.MECHANICALLY_VERIFIED,
]


def run_trust_algebra_experiments(ta):
    """Exercise every pairwise trust-algebra operation and collect stats."""
    ops_count = 0
    admissible_count = 0
    sheaf_pass = 0
    yields = []

    for a in TRUST_LEVELS:
        for b in TRUST_LEVELS:
            ta.compose(a, b)
            ta.meet(a, b)
            ta.join(a, b)
            ops_count += 3

    for level in TRUST_LEVELS:
        try:
            if ta.is_admissible({"evidence": [level]}):
                admissible_count += 1
        except Exception:
            pass

        try:
            ta.promote(level, "experiment_justification")
        except Exception:
            pass

        try:
            y = ta.theorem_yield(level)
            if isinstance(y, dict) and "yield" in y:
                yields.append(1.0 if y["yield"] == "computed" else 0.0)
        except Exception:
            pass

    # Sheaf checks on small sites
    for i in range(len(TRUST_LEVELS)):
        try:
            sb = SiteBuilder()
            sb.add_coordinate(Coordinate(f"c{i}", CoordinateKind.FUNCTION))
            site = sb.build()
            result = ta.sheaf_condition_check(
                site, {f"c{i}": TRUST_LEVELS[i]}
            )
            if result.get("satisfied", False):
                sheaf_pass += 1
        except Exception:
            pass

    return {
        "ops_count": ops_count,
        "admissible_count": admissible_count,
        "admissible_total": len(TRUST_LEVELS),
        "sheaf_pass": sheaf_pass,
        "sheaf_total": len(TRUST_LEVELS),
        "mean_yield": round(statistics.mean(yields), 4) if yields else 0.0,
    }


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("EXPERIMENT 55 — Trust Economics")
    print("  All numbers from `python3 -m jugeo` CLI + Python API")
    print("=" * 72)
    print()

    ta = TrustAlgebra()
    tmpfiles = []
    program_results = []

    eval_times = []
    cycle_durations = []
    trust_scores = []
    phase_counts = []
    qualities = []
    total_judgments = 0
    discharge_count = 0
    total_encode_coords = 0
    cycle_successes = 0
    obstruction_total = 0

    for name, source in PROGRAMS.items():
        print(f"  [{name}]")
        path = write_temp(source)
        tmpfiles.append(path)

        # ── evaluate ─────────────────────────────────────────────────────
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", path)
        eval_wall = time.perf_counter() - t0
        eval_times.append(eval_wall)

        ev = eval_objs[0] if eval_objs else {}
        trust_info = ev.get("trust", {})
        per_coord = ev.get("per_coordinate", [])
        cover_quality = ev.get("cover_quality", {})

        for pc in per_coord:
            qualities.append(pc.get("quality", 0.0))

        # ── encode ───────────────────────────────────────────────────────
        enc_objs = run_jugeo("encode", path)
        enc = enc_objs[0] if enc_objs else {}
        files_enc = (enc.get("files") or [{}])
        if isinstance(files_enc, list) and files_enc:
            file_enc = files_enc[0]
        elif isinstance(enc, dict) and "coordinates" in enc:
            file_enc = enc
        else:
            file_enc = {}

        coords_dict = file_enc.get("coordinates", {})
        judgments_list = file_enc.get("judgments", [])
        total_judgments += len(judgments_list)

        for cname, cdata in coords_dict.items():
            total_encode_coords += 1
            assertions = cdata.get("assertions", 0)
            if assertions > 0:
                discharge_count += 1

        # ── maturity cycle ───────────────────────────────────────────────
        try:
            coord = CyclicSystemCoordinator.create(name)
            record, transitions = coord.run_full_cycle({"source": source})
            metrics = coord.get_metrics().to_dict()

            trust_scores.append(metrics.get("mean_trust_score", 0.0))
            cycle_durations.append(metrics.get("mean_cycle_duration", 0.0))
            phase_counts.append(len(record.phases_completed))
            obstruction_total += metrics.get("total_obstructions", 0)
            if metrics.get("success_rate", 0.0) > 0:
                cycle_successes += 1
        except Exception as exc:
            print(f"    maturity cycle error: {exc}")
            trust_scores.append(0.0)
            cycle_durations.append(0.0)
            phase_counts.append(0)

        prog_result = {
            "name": name,
            "eval_wall_s": round(eval_wall, 4),
            "trust": trust_info.get("aggregate_trust", "unknown"),
            "cover_quality": cover_quality.get("total_score", 0.0),
            "coordinates_encoded": len(coords_dict),
            "judgments": len(judgments_list),
            "per_coord_qualities": [pc.get("quality", 0.0) for pc in per_coord],
        }
        program_results.append(prog_result)
        print(f"    eval={eval_wall:.3f}s  coords={len(coords_dict)}  "
              f"judgments={len(judgments_list)}  trust={trust_info.get('aggregate_trust', '?')}")

    # ── trust algebra pairwise experiment ────────────────────────────────
    print("\n  Running trust algebra experiments …")
    ta_results = run_trust_algebra_experiments(ta)
    print(f"    ops={ta_results['ops_count']}  admissible={ta_results['admissible_count']}/{ta_results['admissible_total']}  "
          f"sheaf_pass={ta_results['sheaf_pass']}/{ta_results['sheaf_total']}  "
          f"mean_yield={ta_results['mean_yield']}")

    # ── aggregate stats ──────────────────────────────────────────────────
    n = len(PROGRAMS)
    mean_trust = round(statistics.mean(trust_scores), 4) if trust_scores else 0.0
    mean_cycle = round(statistics.mean(cycle_durations), 4) if cycle_durations else 0.0
    success_rate = round(cycle_successes / n * 100, 1) if n else 0.0
    obstruction_rate = round(obstruction_total / n * 100, 1) if n else 0.0
    mean_phases = round(statistics.mean(phase_counts), 1) if phase_counts else 0.0
    discharge_pct = round(discharge_count / total_encode_coords * 100, 1) if total_encode_coords else 0.0
    mean_quality = round(statistics.mean(qualities), 4) if qualities else 0.0
    admissible_pct = round(ta_results["admissible_count"] / ta_results["admissible_total"] * 100, 1) \
        if ta_results["admissible_total"] else 0.0
    mean_eval = round(statistics.mean(eval_times), 4) if eval_times else 0.0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:            {n}")
    print(f"  Mean trust score:    {mean_trust}")
    print(f"  Mean cycle duration: {mean_cycle}s")
    print(f"  Success rate:        {success_rate}%")
    print(f"  Obstruction rate:    {obstruction_rate}%")
    print(f"  Mean phases/cycle:   {mean_phases}")
    print(f"  Total judgments:     {total_judgments}")
    print(f"  Discharge count:     {discharge_count}/{total_encode_coords}  ({discharge_pct}%)")
    print(f"  Mean coord quality:  {mean_quality}")
    print(f"  Trust lattice ops:   {ta_results['ops_count']}")
    print(f"  Admissible pct:      {admissible_pct}%")
    print(f"  Sheaf check pass:    {ta_results['sheaf_pass']}")
    print(f"  Mean theorem yield:  {ta_results['mean_yield']}")
    print(f"  Mean eval time:      {mean_eval}s")
    print("=" * 72)

    # ── write LaTeX macros ───────────────────────────────────────────────
    P = "ppLV"
    tex = [
        "% data-paper55.tex — AUTO-GENERATED by exp55_trust_economics.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp55_trust_economics.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    m("totalPrograms", n)
    m("meanTrustScore", mean_trust)
    m("meanCycleDuration", f"{mean_cycle}\\,s")
    m("successRate", f"{success_rate}\\%")
    m("obstructionRate", f"{obstruction_rate}\\%")
    m("meanPhases", mean_phases)
    m("totalJudgments", total_judgments)
    m("dischargeCount", discharge_count)
    m("dischargePct", f"{discharge_pct}\\%")
    m("meanQuality", mean_quality)
    m("trustLatticeOps", ta_results["ops_count"])
    m("admissiblePct", f"{admissible_pct}\\%")
    m("sheafCheckPass", ta_results["sheaf_pass"])
    m("meanYield", ta_results["mean_yield"])
    m("meanEvalTime", f"{mean_eval}\\,s")

    tex_path = os.path.join(ROOT, "papers", "data-paper55.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # ── write JSON results ───────────────────────────────────────────────
    output = {
        "experiment": "trust_economics",
        "paper": 55,
        "note": "All JuGeo numbers from CLI subprocess + Python API.",
        "n_programs": n,
        "programs": program_results,
        "trust_algebra": ta_results,
        "summary": {
            "mean_trust_score": mean_trust,
            "mean_cycle_duration": mean_cycle,
            "success_rate": success_rate,
            "obstruction_rate": obstruction_rate,
            "mean_phases": mean_phases,
            "total_judgments": total_judgments,
            "discharge_count": discharge_count,
            "discharge_pct": discharge_pct,
            "mean_quality": mean_quality,
            "trust_lattice_ops": ta_results["ops_count"],
            "admissible_pct": admissible_pct,
            "sheaf_check_pass": ta_results["sheaf_pass"],
            "mean_yield": ta_results["mean_yield"],
            "mean_eval_time": mean_eval,
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper55.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {json_path}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        cleanup(p)

    print("\nDone.")


if __name__ == "__main__":
    main()
