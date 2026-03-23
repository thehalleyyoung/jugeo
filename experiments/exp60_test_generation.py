#!/usr/bin/env python3
"""Paper 60 Experiment — Test Generation from Covers and Descent Obstructions.

Hypothesis: JuGeo covering families and descent obstructions provide a
systematic basis for generating comprehensive test suites.

Re-run: python3 experiments/exp60_test_generation.py
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
    "shopping_cart": '''\
class ShoppingCart:
    def __init__(self):
        self.items = {}

    def add(self, product, price, quantity=1):
        if price < 0:
            raise ValueError("Price must be non-negative")
        if quantity < 1:
            raise ValueError("Quantity must be positive")
        if product in self.items:
            self.items[product]["quantity"] += quantity
        else:
            self.items[product] = {"price": price, "quantity": quantity}

    def remove(self, product):
        if product not in self.items:
            raise KeyError(f"Product not in cart: {product}")
        del self.items[product]

    def total(self):
        return sum(i["price"] * i["quantity"] for i in self.items.values())

    def apply_discount(self, pct):
        if not 0 <= pct <= 100:
            raise ValueError("Discount must be 0-100")
        return self.total() * (1 - pct / 100)

    def item_count(self):
        return sum(i["quantity"] for i in self.items.values())
''',
    "user_registration": '''\
import re

def validate_username(username):
    if len(username) < 3 or len(username) > 20:
        return False, "Username must be 3-20 characters"
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, "Username must be alphanumeric"
    return True, "Valid"

def validate_password(password):
    if len(password) < 8:
        return False, "Password too short"
    if not any(c.isupper() for c in password):
        return False, "Need uppercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Need digit"
    return True, "Valid"

def create_user(username, password, email):
    u_ok, u_msg = validate_username(username)
    if not u_ok:
        return {"error": u_msg}
    p_ok, p_msg = validate_password(password)
    if not p_ok:
        return {"error": p_msg}
    return {"username": username, "email": email, "active": False}

def confirm_user(user):
    user["active"] = True
    return user
''',
    "payment_proc": '''\
class PaymentProcessor:
    def __init__(self):
        self.transactions = []

    def charge(self, amount, card_token):
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if not card_token:
            raise ValueError("Card token required")
        txn = {"type": "charge", "amount": amount, "token": card_token,
               "status": "completed"}
        self.transactions.append(txn)
        return txn

    def refund(self, txn_index):
        if txn_index >= len(self.transactions):
            raise IndexError("Transaction not found")
        original = self.transactions[txn_index]
        if original["type"] != "charge":
            raise ValueError("Can only refund charges")
        refund_txn = {"type": "refund", "amount": original["amount"],
                      "original": txn_index, "status": "completed"}
        self.transactions.append(refund_txn)
        return refund_txn

    def balance(self):
        total = 0
        for txn in self.transactions:
            if txn["type"] == "charge":
                total += txn["amount"]
            elif txn["type"] == "refund":
                total -= txn["amount"]
        return total
''',
    "inventory": '''\
class Inventory:
    def __init__(self):
        self.stock = {}
        self.reorder_levels = {}

    def add_stock(self, item, quantity):
        if quantity < 0:
            raise ValueError("Quantity must be non-negative")
        self.stock[item] = self.stock.get(item, 0) + quantity

    def remove_stock(self, item, quantity):
        current = self.stock.get(item, 0)
        if quantity > current:
            raise ValueError("Insufficient stock")
        self.stock[item] = current - quantity

    def set_reorder_level(self, item, level):
        self.reorder_levels[item] = level

    def needs_reorder(self):
        alerts = []
        for item, level in self.reorder_levels.items():
            if self.stock.get(item, 0) <= level:
                alerts.append(item)
        return alerts

    def get_stock(self, item):
        return self.stock.get(item, 0)
''',
    "search_engine": '''\
def build_index(documents):
    index = {}
    for doc_id, text in enumerate(documents):
        words = text.lower().split()
        for word in set(words):
            if word not in index:
                index[word] = set()
            index[word].add(doc_id)
    return index

def query(index, terms):
    if not terms:
        return set()
    results = None
    for term in terms.lower().split():
        matches = index.get(term, set())
        if results is None:
            results = matches.copy()
        else:
            results &= matches
    return results or set()

def rank_results(index, documents, terms):
    matches = query(index, terms)
    scored = []
    for doc_id in matches:
        score = sum(documents[doc_id].lower().count(t) for t in terms.lower().split())
        scored.append((doc_id, score))
    return sorted(scored, key=lambda x: x[1], reverse=True)
''',
    "scheduler": '''\
from datetime import datetime, timedelta

class Scheduler:
    def __init__(self):
        self.events = []

    def schedule(self, name, start_time, duration_minutes):
        end_time = start_time + timedelta(minutes=duration_minutes)
        for event in self.events:
            if start_time < event["end"] and end_time > event["start"]:
                raise ValueError(f"Conflict with {event['name']}")
        event = {"name": name, "start": start_time, "end": end_time}
        self.events.append(event)
        return event

    def cancel(self, name):
        self.events = [e for e in self.events if e["name"] != name]

    def upcoming(self, after=None):
        if after is None:
            after = datetime.now()
        return sorted(
            [e for e in self.events if e["start"] > after],
            key=lambda e: e["start"]
        )

    def conflicts(self, start, end):
        return [e for e in self.events
                if start < e["end"] and end > e["start"]]
''',
    "notification": '''\
class NotificationSystem:
    def __init__(self):
        self.queue = []
        self.sent = []
        self.failed = []

    def send(self, recipient, message, channel="email"):
        notification = {
            "recipient": recipient,
            "message": message,
            "channel": channel,
            "status": "pending",
        }
        self.queue.append(notification)
        return notification

    def process_queue(self):
        while self.queue:
            notif = self.queue.pop(0)
            try:
                notif["status"] = "sent"
                self.sent.append(notif)
            except Exception:
                notif["status"] = "failed"
                self.failed.append(notif)

    def retry_failed(self):
        to_retry = self.failed[:]
        self.failed.clear()
        self.queue.extend(to_retry)
''',
    "data_pipeline": '''\
def extract(source):
    if isinstance(source, str):
        return source.strip().splitlines()
    if isinstance(source, list):
        return source
    return list(source)

def transform(records, mapping):
    result = []
    for record in records:
        if isinstance(record, str):
            parts = record.split(',')
            row = {}
            for i, key in enumerate(mapping):
                row[key] = parts[i].strip() if i < len(parts) else None
            result.append(row)
        elif isinstance(record, dict):
            result.append({mapping.get(k, k): v for k, v in record.items()})
    return result

def load(records, target):
    if isinstance(target, list):
        target.extend(records)
    elif isinstance(target, dict):
        for i, r in enumerate(records):
            target[i] = r
    return len(records)

def run_pipeline(source, mapping, target):
    raw = extract(source)
    transformed = transform(raw, mapping)
    count = load(transformed, target)
    return {"extracted": len(raw), "transformed": len(transformed), "loaded": count}
''',
    "permission_sys": '''\
class PermissionSystem:
    def __init__(self):
        self.roles = {}
        self.user_roles = {}

    def define_role(self, role, permissions):
        self.roles[role] = set(permissions)

    def assign_role(self, user, role):
        if role not in self.roles:
            raise ValueError(f"Unknown role: {role}")
        if user not in self.user_roles:
            self.user_roles[user] = set()
        self.user_roles[user].add(role)

    def revoke_role(self, user, role):
        if user in self.user_roles:
            self.user_roles[user].discard(role)

    def check_permission(self, user, permission):
        for role in self.user_roles.get(user, set()):
            if permission in self.roles.get(role, set()):
                return True
        return False

    def user_permissions(self, user):
        perms = set()
        for role in self.user_roles.get(user, set()):
            perms.update(self.roles.get(role, set()))
        return perms
''',
    "workflow_engine": '''\
class Workflow:
    def __init__(self, name, steps):
        self.name = name
        self.steps = steps
        self.current = 0
        self.status = "pending"
        self.results = {}

    def start(self):
        if self.status != "pending":
            raise RuntimeError("Already started")
        self.status = "running"
        self.current = 0

    def advance(self, result=None):
        if self.status != "running":
            raise RuntimeError("Not running")
        self.results[self.steps[self.current]] = result
        self.current += 1
        if self.current >= len(self.steps):
            self.status = "completed"

    def complete(self):
        return self.status == "completed"

    def current_step(self):
        if self.status != "running":
            return None
        return self.steps[self.current]

    def progress(self):
        return self.current / len(self.steps) if self.steps else 1.0
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        desc_objs = run_jugeo("descend", tmp)
        descend_time = time.perf_counter() - t1

        t2 = time.perf_counter()
        bugs_objs = run_jugeo("bugs", tmp)
        bugs_time = time.perf_counter() - t2

        t3 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        load_time = time.perf_counter() - t3

        eval_data = eval_objs[0] if eval_objs else {}
        cover_q = eval_data.get("cover_quality", {}).get("total_score", 0)
        per_coord = eval_data.get("per_coordinate", [])

        desc_data = desc_objs[0] if desc_objs else {}
        verdict = desc_data.get("verdict", "unknown")
        sections = desc_data.get("sections_detail", [])
        props_total = sum(s.get("propositions", 0) for s in sections)
        props_ok = sum(s.get("ok", 0) for s in sections)
        obstructions = len(desc_data.get("obstructions", []))
        local_sections = desc_data.get("local_sections", 0)

        bugs_data = bugs_objs[0] if bugs_objs else {}
        bugs_found = bugs_data.get("count", 0)

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        morphisms = summary.get("morphisms", 0)
        covers = summary.get("covering_families", 0)

        return {
            "name": name,
            "eval_time": round(eval_time, 4),
            "descend_time": round(descend_time, 4),
            "bugs_time": round(bugs_time, 4),
            "coords": coords, "morphisms": morphisms,
            "covers": covers,
            "verdict": verdict,
            "props_total": props_total,
            "props_ok": props_ok,
            "obstructions": obstructions,
            "local_sections": local_sections,
            "bugs_found": bugs_found,
            "cover_quality": cover_q,
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
    print("Paper 60: Test Generation from Covers and Descent Obstructions")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Coords: {m['coords']}, Covers: {m['covers']}")
        print(f"    Props: {m['props_ok']}/{m['props_total']}, Obstructions: {m['obstructions']}")
        print(f"    Verdict: {m['verdict']}, Bugs: {m['bugs_found']}")

    n = len(results)
    total_props = sum(r["props_total"] for r in results)
    total_props_ok = sum(r["props_ok"] for r in results)
    total_obs = sum(r["obstructions"] for r in results)
    mean_coords = statistics.mean([r["coords"] for r in results])
    mean_morphisms = statistics.mean([r["morphisms"] for r in results])
    total_covers = sum(r["covers"] for r in results)
    mean_descent = statistics.mean([r["descend_time"] for r in results])
    mean_eval = statistics.mean([r["eval_time"] for r in results])
    mean_bugs_time = statistics.mean([r["bugs_time"] for r in results])
    total_bugs = sum(r["bugs_found"] for r in results)
    verified_count = sum(1 for r in results if r["verdict"] == "verified")
    descent_rate = verified_count / n if n else 0
    cover_q_mean = statistics.mean([r["cover_quality"] for r in results])
    mean_sections = statistics.mean([r["local_sections"] for r in results])

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:        {n}")
    print(f"  Verified:        {verified_count}")
    print(f"  Total covers:    {total_covers}")
    print(f"  Total obs:       {total_obs}")

    tex_path = os.path.join(ROOT, "papers", "data-paper60.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper60.tex — AUTO-GENERATED by exp60_test_generation.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp60_test_generation.py\n\n")
        f.write(f"\\newcommand{{\\ppLXtotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLXtotalProps}}{{{total_props}}}\n")
        f.write(f"\\newcommand{{\\ppLXtotalPropsOk}}{{{total_props_ok}}}\n")
        f.write(f"\\newcommand{{\\ppLXtotalObstructions}}{{{total_obs}}}\n")
        f.write(f"\\newcommand{{\\ppLXmeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLXmeanMorphisms}}{{{fmt_float(mean_morphisms)}}}\n")
        f.write(f"\\newcommand{{\\ppLXtotalCovers}}{{{total_covers}}}\n")
        f.write(f"\\newcommand{{\\ppLXmeanDescentTime}}{{{fmt_time(mean_descent)}}}\n")
        f.write(f"\\newcommand{{\\ppLXmeanEvalTime}}{{{fmt_time(mean_eval)}}}\n")
        f.write(f"\\newcommand{{\\ppLXmeanBugsTime}}{{{fmt_time(mean_bugs_time)}}}\n")
        f.write(f"\\newcommand{{\\ppLXbugsFound}}{{{total_bugs}}}\n")
        f.write(f"\\newcommand{{\\ppLXverifiedCount}}{{{verified_count}}}\n")
        f.write(f"\\newcommand{{\\ppLXdescentSuccessRate}}{{{fmt_pct(descent_rate)}}}\n")
        f.write(f"\\newcommand{{\\ppLXcoverQualityMean}}{{{fmt_float(cover_q_mean, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLXmeanSections}}{{{fmt_float(mean_sections)}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper60.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
