#!/usr/bin/env python3
"""Paper 58 Experiment — Refactoring Guidance: Sheaf-Theoretic Refactoring.

Hypothesis: JuGeo's site structure reveals refactoring opportunities via
morphism analysis, obstruction detection, and cover quality assessment.

Re-run: python3 experiments/exp58_refactoring_guidance.py
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
    "monolithic_calc": '''\
def calculate(op, a, b):
    if op == 'add':
        result = a + b
        if result > 1e15:
            return 1e15
        return result
    elif op == 'sub':
        result = a - b
        if result < -1e15:
            return -1e15
        return result
    elif op == 'mul':
        result = a * b
        if abs(result) > 1e15:
            return 1e15 if result > 0 else -1e15
        return result
    elif op == 'div':
        if b == 0:
            raise ZeroDivisionError
        return a / b
    elif op == 'mod':
        if b == 0:
            raise ZeroDivisionError
        return a % b
    elif op == 'pow':
        if b < 0:
            raise ValueError("Negative exponent")
        return a ** b
    else:
        raise ValueError(f"Unknown op: {op}")
''',
    "god_class": '''\
class DataManager:
    def __init__(self):
        self.data = {}
        self.log = []
        self.config = {}
    def load(self, key, value):
        self.data[key] = value
        self.log.append(f"load:{key}")
    def save(self, key):
        val = self.data.get(key)
        self.log.append(f"save:{key}")
        return val
    def configure(self, key, value):
        self.config[key] = value
    def get_config(self, key):
        return self.config.get(key)
    def validate(self, key):
        return key in self.data and self.data[key] is not None
    def transform(self, key, func):
        if key in self.data:
            self.data[key] = func(self.data[key])
    def export_log(self):
        return list(self.log)
    def clear(self):
        self.data.clear()
        self.log.clear()
        self.config.clear()
''',
    "long_method": '''\
def process_order(order):
    if not order:
        return {"error": "empty order"}
    items = order.get("items", [])
    if not items:
        return {"error": "no items"}
    subtotal = 0
    for item in items:
        price = item.get("price", 0)
        qty = item.get("quantity", 1)
        if price < 0:
            return {"error": "negative price"}
        if qty < 1:
            return {"error": "invalid quantity"}
        subtotal += price * qty
    tax_rate = 0.08
    if order.get("state") == "OR":
        tax_rate = 0.0
    elif order.get("state") == "CA":
        tax_rate = 0.0925
    tax = subtotal * tax_rate
    discount = 0
    if subtotal > 100:
        discount = subtotal * 0.1
    elif subtotal > 50:
        discount = subtotal * 0.05
    total = subtotal + tax - discount
    shipping = 5.99 if total < 35 else 0
    total += shipping
    return {
        "subtotal": round(subtotal, 2),
        "tax": round(tax, 2),
        "discount": round(discount, 2),
        "shipping": round(shipping, 2),
        "total": round(total, 2),
    }
''',
    "duplicated_code": '''\
def process_students(students):
    results = []
    for s in students:
        total = sum(s.get("grades", []))
        count = len(s.get("grades", []))
        avg = total / count if count > 0 else 0
        results.append({"name": s["name"], "average": avg, "total": total})
    return sorted(results, key=lambda x: x["average"], reverse=True)

def process_employees(employees):
    results = []
    for e in employees:
        total = sum(e.get("scores", []))
        count = len(e.get("scores", []))
        avg = total / count if count > 0 else 0
        results.append({"name": e["name"], "average": avg, "total": total})
    return sorted(results, key=lambda x: x["average"], reverse=True)
''',
    "feature_envy": '''\
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

class Geometry:
    def distance(self, p1, p2):
        dx = p1.x - p2.x
        dy = p1.y - p2.y
        return (dx * dx + dy * dy) ** 0.5
    def midpoint(self, p1, p2):
        return Point((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)
    def slope(self, p1, p2):
        if p1.x == p2.x:
            return float('inf')
        return (p1.y - p2.y) / (p1.x - p2.x)
''',
    "data_clump": '''\
def create_rect(x1, y1, x2, y2):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

def rect_area(x1, y1, x2, y2):
    return abs(x2 - x1) * abs(y2 - y1)

def rect_perimeter(x1, y1, x2, y2):
    return 2 * (abs(x2 - x1) + abs(y2 - y1))

def rect_contains(x1, y1, x2, y2, px, py):
    return x1 <= px <= x2 and y1 <= py <= y2

def rect_overlap(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)
''',
    "primitive_obsession": '''\
def create_money(amount, currency):
    return (amount, currency)

def add_money(m1, m2):
    if m1[1] != m2[1]:
        raise ValueError("Currency mismatch")
    return (m1[0] + m2[0], m1[1])

def subtract_money(m1, m2):
    if m1[1] != m2[1]:
        raise ValueError("Currency mismatch")
    return (m1[0] - m2[0], m1[1])

def format_money(money):
    symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(money[1], money[1])
    return f"{symbol}{money[0]:.2f}"
''',
    "switch_stmt": '''\
def get_shape_area(shape_type, params):
    if shape_type == "circle":
        import math
        return math.pi * params["radius"] ** 2
    elif shape_type == "rectangle":
        return params["width"] * params["height"]
    elif shape_type == "triangle":
        return 0.5 * params["base"] * params["height"]
    elif shape_type == "square":
        return params["side"] ** 2
    elif shape_type == "trapezoid":
        return 0.5 * (params["a"] + params["b"]) * params["height"]
    else:
        raise ValueError(f"Unknown shape: {shape_type}")

def get_shape_perimeter(shape_type, params):
    if shape_type == "circle":
        import math
        return 2 * math.pi * params["radius"]
    elif shape_type == "rectangle":
        return 2 * (params["width"] + params["height"])
    elif shape_type == "triangle":
        return params["a"] + params["b"] + params["c"]
    elif shape_type == "square":
        return 4 * params["side"]
    else:
        raise ValueError(f"Unknown shape: {shape_type}")
''',
    "speculative_gen": '''\
class AbstractProcessor:
    def process(self, data):
        raise NotImplementedError
    def validate(self, data):
        raise NotImplementedError
    def transform(self, data):
        raise NotImplementedError
    def rollback(self, data):
        raise NotImplementedError

class SimpleProcessor(AbstractProcessor):
    def process(self, data):
        return data.upper() if isinstance(data, str) else str(data)
    def validate(self, data):
        return data is not None
    def transform(self, data):
        return self.process(data)
    def rollback(self, data):
        return data
''',
    "message_chain": '''\
class Config:
    def __init__(self):
        self.settings = {}
    def get_section(self, name):
        return Section(self.settings.get(name, {}))

class Section:
    def __init__(self, data):
        self.data = data
    def get_subsection(self, name):
        return SubSection(self.data.get(name, {}))

class SubSection:
    def __init__(self, data):
        self.data = data
    def get_value(self, key, default=None):
        return self.data.get(key, default)

def get_deep_config(config, section, subsection, key, default=None):
    return config.get_section(section).get_subsection(subsection).get_value(key, default)
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        build_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t1

        t2 = time.perf_counter()
        bugs_objs = run_jugeo("bugs", tmp)
        bugs_time = time.perf_counter() - t2

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        morphisms = summary.get("morphisms", 0)

        eval_data = eval_objs[0] if eval_objs else {}
        per_coord = eval_data.get("per_coordinate", [])
        qualities = [c.get("quality", 0) for c in per_coord]
        cover_q = eval_data.get("cover_quality", {}).get("total_score", 0)

        desc_objs = run_jugeo("descend", tmp)
        desc_data = desc_objs[0] if desc_objs else {}
        verdict = desc_data.get("verdict", "unknown")
        sections = desc_data.get("sections_detail", [])
        props_total = sum(s.get("propositions", 0) for s in sections)
        props_ok = sum(s.get("ok", 0) for s in sections)
        obstructions = len(desc_data.get("obstructions", []))

        bugs_data = bugs_objs[0] if bugs_objs else {}
        bugs_found = bugs_data.get("count", 0)
        obs_count = bugs_data.get("obstruction_count", 0)

        return {
            "name": name,
            "build_time": round(build_time, 4),
            "eval_time": round(eval_time, 4),
            "bugs_time": round(bugs_time, 4),
            "coords": coords, "morphisms": morphisms,
            "mean_quality": statistics.mean(qualities) if qualities else 0,
            "cover_quality": cover_q,
            "verdict": verdict,
            "props_total": props_total, "props_ok": props_ok,
            "obstructions": obstructions + obs_count,
            "bugs_found": bugs_found,
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
    print("Paper 58: Refactoring Guidance — Sheaf-Theoretic Analysis")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Coords: {m['coords']}, Morphisms: {m['morphisms']}")
        print(f"    Quality: {m['mean_quality']:.3f}, Bugs: {m['bugs_found']}")

    n = len(results)
    mean_coords = statistics.mean([r["coords"] for r in results])
    mean_morphisms = statistics.mean([r["morphisms"] for r in results])
    total_morphisms = sum(r["morphisms"] for r in results)
    verified_count = sum(1 for r in results if r["verdict"] == "verified")
    accuracy = verified_count / n if n else 0
    total_props = sum(r["props_total"] for r in results)
    total_props_ok = sum(r["props_ok"] for r in results)
    total_obs = sum(r["obstructions"] for r in results)
    mean_quality = statistics.mean([r["mean_quality"] for r in results])
    mean_build = statistics.mean([r["build_time"] for r in results])
    mean_eval = statistics.mean([r["eval_time"] for r in results])
    mean_bugs = statistics.mean([r["bugs_time"] for r in results])
    total_bugs = sum(r["bugs_found"] for r in results)
    cover_q_mean = statistics.mean([r["cover_quality"] for r in results])

    tex_path = os.path.join(ROOT, "papers", "data-paper58.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper58.tex — AUTO-GENERATED by exp58_refactoring_guidance.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp58_refactoring_guidance.py\n\n")
        f.write(f"\\newcommand{{\\ppLVIIItotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIImeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIImeanMorphisms}}{{{fmt_float(mean_morphisms)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIItotalMorphisms}}{{{total_morphisms}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIIoverallAccuracy}}{{{fmt_pct(accuracy)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIItotalProps}}{{{total_props}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIItotalPropsOk}}{{{total_props_ok}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIItotalObstructions}}{{{total_obs}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIImeanQuality}}{{{fmt_float(mean_quality, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIImeanBuildTime}}{{{fmt_time(mean_build)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIImeanEvalTime}}{{{fmt_time(mean_eval)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIImeanBugsTime}}{{{fmt_time(mean_bugs)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIIbugsFound}}{{{total_bugs}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIIcoverQualityMean}}{{{fmt_float(cover_q_mean, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIIverifiedCount}}{{{verified_count}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper58.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
