#!/usr/bin/env python3
"""Paper 60 Experiment — Test Generation from Covers and Descent Obstructions.

Runs JuGeo on 10 programs with rich method surfaces, measuring how covering
families decompose into test targets: each cover member → unit test, overlaps
→ integration tests, obstructions → regression tests.
Generates papers/data-paper60.tex with \\ppLX... macros.

Re-run: python3 experiments/exp60_test_generation.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper60.tex"

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

# ─── 10 Test Programs for Test Generation ──────────────────────────────────

PROGRAMS = {
    "shopping_cart": textwrap.dedent("""\
        class ShoppingCart:
            def __init__(self):
                self.items = {}
                self.discount = 0.0
            def add(self, item, price, qty=1):
                if price < 0:
                    raise ValueError("negative price")
                if item in self.items:
                    self.items[item] = (self.items[item][0], self.items[item][1] + qty)
                else:
                    self.items[item] = (price, qty)
            def remove(self, item):
                if item not in self.items:
                    raise KeyError(f"no such item: {item}")
                del self.items[item]
            def total(self):
                raw = sum(p * q for p, q in self.items.values())
                return round(raw * (1 - self.discount), 2)
            def apply_discount(self, pct):
                if not 0 <= pct <= 1:
                    raise ValueError("discount must be 0..1")
                self.discount = pct
    """),
    "user_registration": textwrap.dedent("""\
        import re
        class UserRegistration:
            EMAIL_RE = re.compile(r'^[^@]+@[^@]+\\.[^@]+$')
            def __init__(self):
                self.users = {}
                self.pending = set()
            def validate(self, username, email, password):
                if len(username) < 3:
                    return False, "username too short"
                if not self.EMAIL_RE.match(email):
                    return False, "invalid email"
                if len(password) < 8:
                    return False, "password too short"
                return True, "ok"
            def create(self, username, email, password):
                ok, msg = self.validate(username, email, password)
                if not ok:
                    raise ValueError(msg)
                if username in self.users:
                    raise ValueError("username taken")
                self.users[username] = {"email": email, "password": password}
                self.pending.add(username)
                return username
            def confirm(self, username):
                if username not in self.pending:
                    raise ValueError("not pending")
                self.pending.discard(username)
                return True
    """),
    "payment_processor": textwrap.dedent("""\
        class PaymentProcessor:
            def __init__(self):
                self.transactions = []
                self.balance = {}
            def charge(self, account, amount):
                if amount <= 0:
                    raise ValueError("amount must be positive")
                self.balance.setdefault(account, 0.0)
                self.balance[account] -= amount
                txn = {"type": "charge", "account": account, "amount": amount}
                self.transactions.append(txn)
                return len(self.transactions) - 1
            def refund(self, txn_id):
                if txn_id < 0 or txn_id >= len(self.transactions):
                    raise IndexError("invalid transaction")
                txn = self.transactions[txn_id]
                if txn.get("refunded"):
                    raise ValueError("already refunded")
                self.balance[txn["account"]] += txn["amount"]
                txn["refunded"] = True
                return True
            def receipt(self, txn_id):
                if txn_id < 0 or txn_id >= len(self.transactions):
                    raise IndexError("invalid transaction")
                return dict(self.transactions[txn_id])
    """),
    "inventory_manager": textwrap.dedent("""\
        class InventoryManager:
            def __init__(self, reorder_threshold=10):
                self.stock = {}
                self.threshold = reorder_threshold
                self.alerts = []
            def add_stock(self, item, qty):
                if qty < 0:
                    raise ValueError("negative quantity")
                self.stock[item] = self.stock.get(item, 0) + qty
            def remove_stock(self, item, qty):
                current = self.stock.get(item, 0)
                if qty > current:
                    raise ValueError("insufficient stock")
                self.stock[item] = current - qty
                self._check_reorder(item)
            def _check_reorder(self, item):
                if self.stock.get(item, 0) < self.threshold:
                    self.alerts.append(f"reorder: {item}")
            def get_stock(self, item):
                return self.stock.get(item, 0)
            def pending_alerts(self):
                result = list(self.alerts)
                self.alerts.clear()
                return result
    """),
    "search_engine": textwrap.dedent("""\
        from collections import defaultdict
        class SearchEngine:
            def __init__(self):
                self.index = defaultdict(set)
                self.documents = {}
            def add_document(self, doc_id, text):
                self.documents[doc_id] = text
                for word in text.lower().split():
                    clean = ''.join(c for c in word if c.isalnum())
                    if clean:
                        self.index[clean].add(doc_id)
            def query(self, terms):
                if not terms:
                    return []
                words = terms.lower().split()
                result_sets = [self.index.get(w, set()) for w in words]
                if not result_sets:
                    return []
                matches = result_sets[0]
                for s in result_sets[1:]:
                    matches = matches & s
                return sorted(matches)
            def rank(self, terms):
                words = terms.lower().split()
                scores = defaultdict(int)
                for w in words:
                    for doc_id in self.index.get(w, set()):
                        scores[doc_id] += 1
                return sorted(scores.items(), key=lambda x: -x[1])
    """),
    "scheduler": textwrap.dedent("""\
        from datetime import datetime, timedelta
        class Scheduler:
            def __init__(self):
                self.events = {}
                self.next_id = 0
            def schedule(self, name, when, callback=None):
                eid = self.next_id
                self.next_id += 1
                self.events[eid] = {"name": name, "when": when,
                                     "callback": callback, "cancelled": False}
                return eid
            def cancel(self, eid):
                if eid not in self.events:
                    raise KeyError(f"no event {eid}")
                self.events[eid]["cancelled"] = True
            def due_events(self, now=None):
                if now is None:
                    now = datetime.now()
                return [e for e in self.events.values()
                        if not e["cancelled"] and e["when"] <= now]
            def notify(self, now=None):
                due = self.due_events(now)
                for e in due:
                    if e["callback"]:
                        e["callback"](e["name"])
                return len(due)
    """),
    "notification_system": textwrap.dedent("""\
        from collections import deque
        class NotificationSystem:
            def __init__(self, max_retries=3):
                self.queue = deque()
                self.sent = []
                self.failed = []
                self.max_retries = max_retries
            def send(self, recipient, message):
                self.queue.append({"to": recipient, "msg": message, "attempts": 0})
            def process_queue(self, sender_fn=None):
                processed = 0
                while self.queue:
                    notif = self.queue.popleft()
                    notif["attempts"] += 1
                    if sender_fn:
                        try:
                            sender_fn(notif["to"], notif["msg"])
                            self.sent.append(notif)
                            processed += 1
                        except Exception:
                            if notif["attempts"] < self.max_retries:
                                self.queue.append(notif)
                            else:
                                self.failed.append(notif)
                    else:
                        self.sent.append(notif)
                        processed += 1
                return processed
            def retry_failed(self):
                for notif in self.failed:
                    notif["attempts"] = 0
                    self.queue.append(notif)
                count = len(self.failed)
                self.failed.clear()
                return count
    """),
    "data_pipeline": textwrap.dedent("""\
        class DataPipeline:
            def __init__(self):
                self.steps = []
                self.errors = []
            def add_step(self, name, fn):
                self.steps.append((name, fn))
            def extract(self, source):
                if not isinstance(source, (list, dict)):
                    raise TypeError("source must be list or dict")
                return list(source) if isinstance(source, dict) else source[:]
            def transform(self, data, fn):
                try:
                    return [fn(item) for item in data]
                except Exception as e:
                    self.errors.append(str(e))
                    return data
            def load(self, data, sink):
                sink.extend(data)
                return len(data)
            def run(self, source, sink):
                data = self.extract(source)
                for name, fn in self.steps:
                    data = self.transform(data, fn)
                return self.load(data, sink)
    """),
    "permission_system": textwrap.dedent("""\
        class PermissionSystem:
            def __init__(self):
                self.roles = {}
                self.user_roles = {}
            def define_role(self, role, permissions):
                self.roles[role] = set(permissions)
            def grant(self, user, role):
                if role not in self.roles:
                    raise ValueError(f"undefined role: {role}")
                self.user_roles.setdefault(user, set()).add(role)
            def revoke(self, user, role):
                if user in self.user_roles:
                    self.user_roles[user].discard(role)
            def check(self, user, permission):
                for role in self.user_roles.get(user, set()):
                    if permission in self.roles.get(role, set()):
                        return True
                return False
            def user_permissions(self, user):
                perms = set()
                for role in self.user_roles.get(user, set()):
                    perms |= self.roles.get(role, set())
                return sorted(perms)
    """),
    "workflow_engine": textwrap.dedent("""\
        class WorkflowEngine:
            def __init__(self):
                self.workflows = {}
            def define(self, name, steps):
                if not steps:
                    raise ValueError("empty workflow")
                self.workflows[name] = {
                    "steps": steps, "instances": {}
                }
            def start(self, workflow_name, instance_id):
                wf = self.workflows.get(workflow_name)
                if not wf:
                    raise KeyError(f"no workflow: {workflow_name}")
                if instance_id in wf["instances"]:
                    raise ValueError("instance exists")
                wf["instances"][instance_id] = {"step": 0, "status": "active"}
                return wf["steps"][0]
            def advance(self, workflow_name, instance_id):
                wf = self.workflows.get(workflow_name)
                if not wf:
                    raise KeyError(f"no workflow: {workflow_name}")
                inst = wf["instances"].get(instance_id)
                if not inst or inst["status"] != "active":
                    raise ValueError("not active")
                inst["step"] += 1
                if inst["step"] >= len(wf["steps"]):
                    inst["status"] = "completed"
                    return None
                return wf["steps"][inst["step"]]
            def complete(self, workflow_name, instance_id):
                wf = self.workflows.get(workflow_name)
                if not wf:
                    raise KeyError(f"no workflow: {workflow_name}")
                inst = wf["instances"].get(instance_id)
                if not inst:
                    raise ValueError("no instance")
                inst["status"] = "completed"
                return True
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Paper 60: Test Generation Experiments")
    print("=" * 60)

    results = []
    for prog_id, source in PROGRAMS.items():
        print(f"  [{prog_id}] ...", end=" ", flush=True)
        tmp = write_temp(source)
        try:
            t0 = time.perf_counter()

            # 1. evaluate — quality, cover info
            t_eval_start = time.perf_counter()
            eval_objs = run_jugeo_json("evaluate", tmp)
            eval_data = eval_objs[0] if eval_objs else {}
            t_eval = time.perf_counter() - t_eval_start

            # 2. descend — descent results, local sections, overlaps, obstructions
            t_desc_start = time.perf_counter()
            desc_objs = run_jugeo_json("descend", tmp)
            desc_data = desc_objs[0] if desc_objs else {}
            t_desc = time.perf_counter() - t_desc_start

            # 3. bugs — bug detection
            t_bugs_start = time.perf_counter()
            bug_objs = run_jugeo_json("bugs", tmp)
            bug_data = bug_objs[0] if bug_objs else {}
            t_bugs = time.perf_counter() - t_bugs_start

            # 4. encode — encoding details
            enc_objs = run_jugeo_json("encode", tmp)
            enc_data = enc_objs[0] if enc_objs else {}

            # 5. load — site structure (covering families are key for test generation)
            load_objs = run_jugeo_json("load", tmp)
            load_data = load_objs[0] if load_objs else {}

            elapsed = time.perf_counter() - t0

            # ── Extract from descend ──
            sections_detail = desc_data.get("sections_detail", [])
            local_sections = desc_data.get("local_sections", 0)
            overlaps = desc_data.get("overlap_conditions_checked", 0)
            obstructions = desc_data.get("obstructions", [])
            verdict = desc_data.get("verdict", "unknown")
            trust = desc_data.get("trust", "UNKNOWN")
            total_props = sum(s.get("propositions", 0) for s in sections_detail)
            total_ok = sum(s.get("ok", 0) for s in sections_detail)

            # ── Extract from bugs ──
            if isinstance(bug_data, dict):
                bugs_found = bug_data.get("count", 0)
                obstruction_count = bug_data.get("obstruction_count", 0)
            else:
                bugs_found = 0
                obstruction_count = 0

            # ── Extract from encode ──
            files_enc = enc_data.get("files", [])
            n_coords_enc = len(files_enc[0].get("coordinates", {})) if files_enc else 0
            morphism_count = 0
            if files_enc:
                for cname, cdata in files_enc[0].get("coordinates", {}).items():
                    morphism_count += cdata.get("declarations", 0) + cdata.get("assertions", 0)

            # ── Extract from load ──
            summary = load_data.get("summary", {})
            n_coords_load = summary.get("coordinates", 0)
            n_morphisms_load = summary.get("morphisms", 0)
            n_covers = summary.get("covering_families", 0)

            # ── Extract from evaluate ──
            cover_q = eval_data.get("cover_quality", {})
            cover_score = cover_q.get("total_score", 0.0) if isinstance(cover_q, dict) else 0.0
            eval_trust = eval_data.get("trust", {})
            agg_trust = eval_trust.get("aggregate_trust", "unverified") if isinstance(eval_trust, dict) else "unverified"

            n_coords = max(n_coords_enc, n_coords_load)

            rec = {
                "id": prog_id,
                "n_coords": n_coords,
                "morphisms": morphism_count,
                "n_morphisms_load": n_morphisms_load,
                "n_covers": n_covers,
                "local_sections": local_sections,
                "overlaps": overlaps,
                "obstructions": len(obstructions),
                "props_total": total_props,
                "props_ok": total_ok,
                "bugs_found": bugs_found,
                "obstruction_count": obstruction_count,
                "verdict": verdict,
                "trust": trust,
                "agg_trust": agg_trust,
                "cover_score": round(cover_score, 4),
                "eval_time": round(t_eval, 3),
                "descent_time": round(t_desc, 3),
                "bugs_time": round(t_bugs, 3),
                "time_s": round(elapsed, 3),
            }
            results.append(rec)
            print(f"coords={n_coords} covers={n_covers} secs={local_sections} "
                  f"obs={len(obstructions)} bugs={bugs_found} t={elapsed:.2f}s")
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
    morph_list = [r["morphisms"] for r in ok]
    covers_list = [r["n_covers"] for r in ok]
    sections_list = [r["local_sections"] for r in ok]
    props_total_sum = sum(r["props_total"] for r in ok)
    props_ok_sum = sum(r["props_ok"] for r in ok)
    obstruction_sum = sum(r["obstructions"] for r in ok)
    bugs_sum = sum(r["bugs_found"] for r in ok)
    descent_times = [r["descent_time"] for r in ok]
    eval_times = [r["eval_time"] for r in ok]
    bugs_times = [r["bugs_time"] for r in ok]
    cover_scores = [r["cover_score"] for r in ok]

    verified_count = sum(1 for r in ok if r["verdict"] == "verified")
    descent_success = sum(1 for r in ok if r["verdict"] == "verified")
    descent_rate = round(descent_success / max(n_ok, 1), 4)

    # ─── Generate LaTeX macros ──────────────────────────────────────────────

    print("\nGenerating", TEX_PATH)
    lines = [
        "% data-paper60.tex — AUTO-GENERATED by exp60_test_generation.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp60_test_generation.py",
        f"% Generated from {n_total} programs",
        "",
        f"\\newcommand{{\\ppLXtotalPrograms}}{{{n_total}}}",
        f"\\newcommand{{\\ppLXtotalProps}}{{{props_total_sum}}}",
        f"\\newcommand{{\\ppLXtotalPropsOk}}{{{props_ok_sum}}}",
        f"\\newcommand{{\\ppLXtotalObstructions}}{{{obstruction_sum}}}",
        "",
        f"\\newcommand{{\\ppLXmeanCoords}}{{{safe_mean(coords_list)}}}",
        f"\\newcommand{{\\ppLXmeanMorphisms}}{{{safe_mean(morph_list)}}}",
        f"\\newcommand{{\\ppLXtotalCovers}}{{{sum(covers_list)}}}",
        "",
        f"\\newcommand{{\\ppLXmeanDescentTime}}{{{safe_mean(descent_times)}\\,s}}",
        f"\\newcommand{{\\ppLXmeanEvalTime}}{{{safe_mean(eval_times)}\\,s}}",
        f"\\newcommand{{\\ppLXmeanBugsTime}}{{{safe_mean(bugs_times)}\\,s}}",
        "",
        f"\\newcommand{{\\ppLXbugsFound}}{{{bugs_sum}}}",
        f"\\newcommand{{\\ppLXverifiedCount}}{{{verified_count}}}",
        f"\\newcommand{{\\ppLXdescentSuccessRate}}{{{descent_rate}}}",
        f"\\newcommand{{\\ppLXcoverQualityMean}}{{{safe_mean(cover_scores)}}}",
        f"\\newcommand{{\\ppLXmeanSections}}{{{safe_mean(sections_list)}}}",
        "",
        "% Per-program test-generation detail",
    ]

    for r in ok:
        tag = r["id"].replace("_", "")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Coords}}{{{r['n_coords']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Covers}}{{{r['n_covers']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Sections}}{{{r['local_sections']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Overlaps}}{{{r['overlaps']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Obstructions}}{{{r['obstructions']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Props}}{{{r['props_total']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Bugs}}{{{r['bugs_found']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Verdict}}{{{r['verdict']}}}")
        lines.append(f"\\newcommand{{\\ppLXtg{tag}Time}}{{{r['time_s']}\\,s}}")

    with open(TEX_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Save JSON results
    json_path = ROOT / "experiments" / "results_paper60.json"
    with open(json_path, "w") as f:
        json.dump({"paper": 60, "programs": n_total, "results": results}, f, indent=2, default=str)

    macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
    print(f"  Wrote {macro_count} macros to {TEX_PATH}")
    print(f"  Wrote results to {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
