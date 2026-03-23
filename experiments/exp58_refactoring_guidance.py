#!/usr/bin/env python3
"""Paper 58 Experiment — Refactoring Guidance via Sheaf-Theoretic Analysis.

Runs JuGeo on programs exhibiting common refactoring anti-patterns.
Measures how morphism structure, obstructions, and cover quality reveal
refactoring opportunities.
Generates papers/data-paper58.tex with \\ppLVIII... macros.

Re-run: python3 experiments/exp58_refactoring_guidance.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper58.tex"

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
def safe_stdev(xs): return round(statistics.stdev(xs), 2) if len(xs) > 1 else 0.0

# ─── 10 Programs with Refactoring Anti-Patterns ────────────────────────────

PROGRAMS = {
    "monolithic_calculator": textwrap.dedent("""\
        def calculate(op, a, b, history=None):
            if history is None:
                history = []
            result = None
            if op == 'add':
                result = a + b
            elif op == 'sub':
                result = a - b
            elif op == 'mul':
                result = a * b
            elif op == 'div':
                if b == 0:
                    raise ZeroDivisionError("division by zero")
                result = a / b
            elif op == 'pow':
                result = a ** b
            elif op == 'mod':
                if b == 0:
                    raise ZeroDivisionError("modulo by zero")
                result = a % b
            else:
                raise ValueError(f"unknown operation: {op}")
            history.append({'op': op, 'a': a, 'b': b, 'result': result})
            return result, history
        def batch_calculate(ops):
            history = []
            results = []
            for op, a, b in ops:
                r, history = calculate(op, a, b, history)
                results.append(r)
            return results, history
    """),
    "god_class": textwrap.dedent("""\
        class AppManager:
            def __init__(self):
                self.users = {}
                self.products = {}
                self.orders = []
                self.log = []
            def add_user(self, uid, name):
                self.users[uid] = {'name': name, 'active': True}
                self.log.append(f'user_added:{uid}')
            def deactivate_user(self, uid):
                if uid in self.users:
                    self.users[uid]['active'] = False
                    self.log.append(f'user_deactivated:{uid}')
            def add_product(self, pid, name, price):
                self.products[pid] = {'name': name, 'price': price, 'stock': 0}
                self.log.append(f'product_added:{pid}')
            def restock(self, pid, qty):
                if pid in self.products:
                    self.products[pid]['stock'] += qty
            def place_order(self, uid, pid, qty):
                if uid not in self.users or not self.users[uid]['active']:
                    raise ValueError("invalid user")
                if pid not in self.products:
                    raise ValueError("invalid product")
                if self.products[pid]['stock'] < qty:
                    raise ValueError("insufficient stock")
                total = self.products[pid]['price'] * qty
                self.products[pid]['stock'] -= qty
                order = {'user': uid, 'product': pid, 'qty': qty, 'total': total}
                self.orders.append(order)
                self.log.append(f'order_placed:{uid}:{pid}')
                return order
            def revenue(self):
                return sum(o['total'] for o in self.orders)
    """),
    "long_method": textwrap.dedent("""\
        def process_record(record):
            if not isinstance(record, dict):
                raise TypeError("record must be dict")
            name = record.get('name', '')
            if not name or not isinstance(name, str):
                return {'valid': False, 'reason': 'missing name'}
            name = name.strip()
            if len(name) < 2:
                return {'valid': False, 'reason': 'name too short'}
            age = record.get('age')
            if age is None:
                return {'valid': False, 'reason': 'missing age'}
            if not isinstance(age, (int, float)) or age < 0 or age > 150:
                return {'valid': False, 'reason': 'invalid age'}
            email = record.get('email', '')
            if '@' not in email or '.' not in email.split('@')[-1]:
                return {'valid': False, 'reason': 'invalid email'}
            score = record.get('score', 0)
            if score < 0:
                score = 0
            elif score > 100:
                score = 100
            grade = 'F'
            if score >= 90:
                grade = 'A'
            elif score >= 80:
                grade = 'B'
            elif score >= 70:
                grade = 'C'
            elif score >= 60:
                grade = 'D'
            return {
                'valid': True,
                'name': name.title(),
                'age': int(age),
                'email': email.lower(),
                'score': score,
                'grade': grade,
            }
    """),
    "duplicated_code": textwrap.dedent("""\
        def process_students(students):
            results = []
            for s in students:
                total = sum(s.get('grades', []))
                count = len(s.get('grades', []))
                avg = total / count if count > 0 else 0
                passed = avg >= 60
                results.append({
                    'name': s.get('name', ''),
                    'average': round(avg, 2),
                    'passed': passed,
                    'total': total,
                })
            return results
        def process_employees(employees):
            results = []
            for e in employees:
                total = sum(e.get('ratings', []))
                count = len(e.get('ratings', []))
                avg = total / count if count > 0 else 0
                promoted = avg >= 4.0
                results.append({
                    'name': e.get('name', ''),
                    'average': round(avg, 2),
                    'promoted': promoted,
                    'total': total,
                })
            return results
        def summarize(items, key):
            vals = [i.get(key, 0) for i in items]
            return {'min': min(vals) if vals else 0,
                    'max': max(vals) if vals else 0,
                    'mean': sum(vals)/len(vals) if vals else 0}
    """),
    "feature_envy": textwrap.dedent("""\
        class Address:
            def __init__(self, street, city, state, zipcode):
                self.street = street
                self.city = city
                self.state = state
                self.zipcode = zipcode
        class Customer:
            def __init__(self, name, address):
                self.name = name
                self.address = address
        def format_shipping_label(customer):
            addr = customer.address
            line1 = customer.name.upper()
            line2 = addr.street
            line3 = f"{addr.city}, {addr.state} {addr.zipcode}"
            return f"{line1}\\n{line2}\\n{line3}"
        def is_local(customer, local_state='CA'):
            return customer.address.state == local_state
        def same_city(c1, c2):
            return (c1.address.city == c2.address.city
                    and c1.address.state == c2.address.state)
        def distance_estimate(c1, c2):
            z1 = int(c1.address.zipcode[:3]) if c1.address.zipcode else 0
            z2 = int(c2.address.zipcode[:3]) if c2.address.zipcode else 0
            return abs(z1 - z2) * 10
    """),
    "data_clump": textwrap.dedent("""\
        def create_user(first_name, last_name, email, phone, street, city, state, zipcode):
            return {
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'phone': phone,
                'street': street,
                'city': city,
                'state': state,
                'zipcode': zipcode,
            }
        def update_address(user, street, city, state, zipcode):
            user['street'] = street
            user['city'] = city
            user['state'] = state
            user['zipcode'] = zipcode
            return user
        def format_address(street, city, state, zipcode):
            return f"{street}, {city}, {state} {zipcode}"
        def validate_address(street, city, state, zipcode):
            if not street or not city:
                return False
            if not state or len(state) != 2:
                return False
            if not zipcode or len(zipcode) != 5:
                return False
            return True
    """),
    "primitive_obsession": textwrap.dedent("""\
        def create_money(amount, currency='USD'):
            return (round(amount, 2), currency)
        def add_money(m1, m2):
            if m1[1] != m2[1]:
                raise ValueError("currency mismatch")
            return (round(m1[0] + m2[0], 2), m1[1])
        def subtract_money(m1, m2):
            if m1[1] != m2[1]:
                raise ValueError("currency mismatch")
            return (round(m1[0] - m2[0], 2), m1[1])
        def multiply_money(m, factor):
            return (round(m[0] * factor, 2), m[1])
        def format_money(m):
            symbols = {'USD': '$', 'EUR': '\\u20ac', 'GBP': '\\u00a3'}
            sym = symbols.get(m[1], m[1])
            return f"{sym}{m[0]:.2f}"
        def is_positive(m):
            return m[0] > 0
        def compare_money(m1, m2):
            if m1[1] != m2[1]:
                raise ValueError("currency mismatch")
            if m1[0] < m2[0]: return -1
            if m1[0] > m2[0]: return 1
            return 0
    """),
    "switch_statement": textwrap.dedent("""\
        def calculate_area(shape_type, **kwargs):
            if shape_type == 'circle':
                import math
                return math.pi * kwargs['radius'] ** 2
            elif shape_type == 'rectangle':
                return kwargs['width'] * kwargs['height']
            elif shape_type == 'triangle':
                return 0.5 * kwargs['base'] * kwargs['height']
            elif shape_type == 'square':
                return kwargs['side'] ** 2
            elif shape_type == 'trapezoid':
                return 0.5 * (kwargs['a'] + kwargs['b']) * kwargs['height']
            else:
                raise ValueError(f"unknown shape: {shape_type}")
        def calculate_perimeter(shape_type, **kwargs):
            if shape_type == 'circle':
                import math
                return 2 * math.pi * kwargs['radius']
            elif shape_type == 'rectangle':
                return 2 * (kwargs['width'] + kwargs['height'])
            elif shape_type == 'triangle':
                return kwargs.get('a', 0) + kwargs.get('b', 0) + kwargs.get('c', 0)
            elif shape_type == 'square':
                return 4 * kwargs['side']
            else:
                raise ValueError(f"unknown shape: {shape_type}")
    """),
    "speculative_generality": textwrap.dedent("""\
        class AbstractHandler:
            def handle(self, request):
                raise NotImplementedError
            def can_handle(self, request):
                raise NotImplementedError
            def pre_process(self, request):
                return request
            def post_process(self, response):
                return response
        class ConcreteHandler(AbstractHandler):
            def handle(self, request):
                data = self.pre_process(request)
                result = data.get('value', 0) * 2
                return self.post_process({'result': result})
            def can_handle(self, request):
                return 'value' in request
        class HandlerChain:
            def __init__(self):
                self.handlers = []
            def add(self, handler):
                self.handlers.append(handler)
                return self
            def process(self, request):
                for h in self.handlers:
                    if h.can_handle(request):
                        return h.handle(request)
                raise ValueError("no handler found")
    """),
    "message_chains": textwrap.dedent("""\
        class Config:
            def __init__(self, data=None):
                self._data = data or {}
            def get(self, key, default=None):
                return self._data.get(key, default)
            def section(self, name):
                return Config(self._data.get(name, {}))
        class App:
            def __init__(self, config):
                self.config = config
            def get_db_host(self):
                return self.config.section('database').section('primary').get('host', 'localhost')
            def get_db_port(self):
                return self.config.section('database').section('primary').get('port', 5432)
            def get_cache_ttl(self):
                return self.config.section('cache').section('redis').get('ttl', 300)
            def get_log_level(self):
                return self.config.section('logging').get('level', 'INFO')
        def deep_get(config, *keys, default=None):
            current = config
            for key in keys:
                current = current.section(key)
            return current.get(keys[-1], default) if keys else default
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Paper 58: Refactoring Guidance Experiments")
    print("=" * 60)

    results = []

    for prog_id, source in PROGRAMS.items():
        print(f"  [{prog_id}] ...", end=" ", flush=True)
        tmp = write_temp(source)
        try:
            # 1. load — site structure (morphisms key for refactoring)
            t_load = time.perf_counter()
            load_objs = run_jugeo_json("load", tmp)
            t_load = time.perf_counter() - t_load
            load_data = load_objs[0] if load_objs else {}

            summary = load_data.get("summary", {})
            n_coords = summary.get("coordinates", 0)
            n_morphisms = summary.get("morphisms", 0)
            morphism_list = load_data.get("morphisms", [])

            # 2. evaluate — per-coordinate quality, trust, cover quality
            t_eval = time.perf_counter()
            eval_objs = run_jugeo_json("evaluate", tmp)
            t_eval = time.perf_counter() - t_eval
            eval_data = eval_objs[0] if eval_objs else {}

            trust_data = eval_data.get("trust", {})
            agg_trust = trust_data.get("aggregate_trust", 0.0) if isinstance(trust_data, dict) else 0.0

            cover_q = eval_data.get("cover_quality", {})
            cover_score = cover_q.get("total_score", 0.0) if isinstance(cover_q, dict) else 0.0

            per_coord = eval_data.get("per_coordinate", [])
            qualities = [c.get("quality", 0.0) for c in per_coord]

            # 3. encode — encoding details for morphism analysis
            t_enc = time.perf_counter()
            enc_objs = run_jugeo_json("encode", tmp)
            t_enc = time.perf_counter() - t_enc
            enc_data = enc_objs[0] if enc_objs else {}

            files_enc = enc_data.get("files", [])
            coord_encodings = files_enc[0].get("coordinates", {}) if files_enc else {}
            total_declarations = 0
            total_assertions = 0
            for cname, cdata in coord_encodings.items():
                total_declarations += cdata.get("declarations", 0)
                total_assertions += cdata.get("assertions", 0)

            # Descent verification (propositions)
            desc_objs = run_jugeo_json("descend", tmp)
            desc_data = desc_objs[0] if desc_objs else {}
            sections_detail = desc_data.get("sections_detail", [])
            total_props = sum(s.get("propositions", 0) for s in sections_detail)
            total_ok = sum(s.get("ok", 0) for s in sections_detail)
            obstructions = desc_data.get("obstructions", [])
            verdict = desc_data.get("verdict", "unknown")

            # 4. bugs — bug detection (obstructions relate to refactoring needs)
            t_bugs = time.perf_counter()
            bug_objs = run_jugeo_json("bugs", tmp)
            t_bugs = time.perf_counter() - t_bugs
            bug_data = bug_objs[0] if bug_objs else {}
            bugs_found = bug_data.get("count", 0) if isinstance(bug_data, dict) else 0

            # 5. Python API — site diagnostics
            site_api = {"replay_gluing": {}, "repair_semantics": {}, "specification_satisfaction": {}}
            try:
                sys.path.insert(0, str(ROOT / "src"))
                from jugeo.geometry import SiteBuilder
                site = SiteBuilder(source).build()
                site_api["replay_gluing"] = site.replay_gluing()
                site_api["repair_semantics"] = site.repair_semantics()
                site_api["specification_satisfaction"] = site.specification_satisfaction()
            except Exception:
                pass  # graceful degradation if API unavailable

            spec_sat = site_api.get("specification_satisfaction", {})
            spec_components = spec_sat.get("components", 0)

            rec = {
                "id": prog_id,
                "n_coords": n_coords,
                "n_morphisms": n_morphisms,
                "total_declarations": total_declarations,
                "total_assertions": total_assertions,
                "total_props": total_props,
                "total_ok": total_ok,
                "n_obstructions": len(obstructions),
                "verdict": verdict,
                "agg_trust": agg_trust,
                "cover_score": round(cover_score, 4),
                "mean_quality": safe_mean(qualities),
                "bugs_found": bugs_found,
                "spec_components": spec_components,
                "replay_gluing": site_api.get("replay_gluing", {}),
                "repair_semantics": site_api.get("repair_semantics", {}),
                "time_load": round(t_load, 3),
                "time_eval": round(t_eval, 3),
                "time_encode": round(t_enc, 3),
                "time_bugs": round(t_bugs, 3),
            }
            results.append(rec)
            print(f"coords={n_coords} morph={n_morphisms} props={total_props}/{total_ok} "
                  f"bugs={bugs_found} obstr={len(obstructions)} t={t_load+t_eval+t_enc+t_bugs:.2f}s")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"id": prog_id, "error": str(e)})
        finally:
            try: os.unlink(tmp)
            except OSError: pass

    # ─── Compute aggregates ─────────────────────────────────────────────────

    ok = [r for r in results if "error" not in r]
    n_total = len(PROGRAMS)
    n_ok = len(ok)

    coords_list = [r["n_coords"] for r in ok]
    morph_list = [r["n_morphisms"] for r in ok]
    props_list = [r["total_props"] for r in ok]
    ok_props_list = [r["total_ok"] for r in ok]
    obs_list = [r["n_obstructions"] for r in ok]
    quality_list = [r["mean_quality"] for r in ok]
    cover_list = [r["cover_score"] for r in ok]
    bugs_list = [r["bugs_found"] for r in ok]
    time_load_list = [r["time_load"] for r in ok]
    time_eval_list = [r["time_eval"] for r in ok]
    time_bugs_list = [r["time_bugs"] for r in ok]

    total_morphisms = sum(morph_list)
    total_props = sum(props_list)
    total_ok_props = sum(ok_props_list)
    total_obstructions = sum(obs_list)
    total_bugs = sum(bugs_list)
    verified_count = sum(1 for r in ok if r["verdict"] == "verified")
    overall_accuracy = round(100 * total_ok_props / max(total_props, 1), 1)

    # ─── Generate LaTeX macros ──────────────────────────────────────────────

    print(f"\nGenerating {TEX_PATH}")
    lines = [
        "% data-paper58.tex — AUTO-GENERATED by exp58_refactoring_guidance.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp58_refactoring_guidance.py",
        f"% Generated from {n_total} programs",
        "",
        "% ── Overall statistics ──────────────────────────────────────────",
        f"\\newcommand{{\\ppLVIIItotalPrograms}}{{{n_total}}}",
        f"\\newcommand{{\\ppLVIIImeanCoords}}{{{safe_mean(coords_list)}}}",
        f"\\newcommand{{\\ppLVIIImeanMorphisms}}{{{safe_mean(morph_list)}}}",
        f"\\newcommand{{\\ppLVIIItotalMorphisms}}{{{total_morphisms}}}",
        "",
        "% ── Verification accuracy ──────────────────────────────────────",
        f"\\newcommand{{\\ppLVIIIoverallAccuracy}}{{{overall_accuracy}\\%}}",
        f"\\newcommand{{\\ppLVIIItotalProps}}{{{total_props}}}",
        f"\\newcommand{{\\ppLVIIItotalPropsOk}}{{{total_ok_props}}}",
        f"\\newcommand{{\\ppLVIIItotalObstructions}}{{{total_obstructions}}}",
        "",
        "% ── Quality and trust ──────────────────────────────────────────",
        f"\\newcommand{{\\ppLVIIImeanQuality}}{{{safe_mean(quality_list)}}}",
        f"\\newcommand{{\\ppLVIIIcoverQualityMean}}{{{safe_mean(cover_list)}}}",
        f"\\newcommand{{\\ppLVIIIverifiedCount}}{{{verified_count}}}",
        "",
        "% ── Timing metrics ─────────────────────────────────────────────",
        f"\\newcommand{{\\ppLVIIImeanBuildTime}}{{{safe_mean(time_load_list)}\\,s}}",
        f"\\newcommand{{\\ppLVIIImeanEvalTime}}{{{safe_mean(time_eval_list)}\\,s}}",
        f"\\newcommand{{\\ppLVIIImeanBugsTime}}{{{safe_mean(time_bugs_list)}\\,s}}",
        "",
        "% ── Bug detection ──────────────────────────────────────────────",
        f"\\newcommand{{\\ppLVIIIbugsFound}}{{{total_bugs}}}",
        "",
        "% ── Per-program refactoring data ───────────────────────────────",
    ]

    for r in ok:
        tag = r["id"].replace("_", "")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Coords}}{{{r['n_coords']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Morphisms}}{{{r['n_morphisms']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Props}}{{{r['total_props']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Obstructions}}{{{r['n_obstructions']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Quality}}{{{r['mean_quality']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Bugs}}{{{r['bugs_found']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIIref{tag}Verdict}}{{{r['verdict']}}}")

    PAPERS.mkdir(parents=True, exist_ok=True)
    with open(TEX_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Save JSON results
    json_path = ROOT / "experiments" / "results_paper58.json"
    with open(json_path, "w") as f:
        json.dump({
            "paper": 58,
            "programs": n_total,
            "programs_ok": n_ok,
            "overall_accuracy": overall_accuracy,
            "total_obstructions": total_obstructions,
            "total_bugs": total_bugs,
            "verified_count": verified_count,
            "results": results,
        }, f, indent=2, default=str)

    macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
    print(f"  Wrote {macro_count} macros to {TEX_PATH}")
    print(f"  Wrote results to {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
