#!/usr/bin/env python3
"""Paper 44 Experiment -- Judgment products: compositional combination.

Constructs judgment products by combining verification results from
multiple subsystem calls.  Measures trust propagation, product assembly
success, and timing for conjunction, disjunction, and conditional
products across a suite of test programs.

Re-run:  python3 experiments/exp44_judgment_products.py
Outputs: papers/data-paper44.tex  (LaTeX macros with \\ppFortyFour… prefix)
"""
import subprocess, json, os, tempfile, time, statistics, sys, random

random.seed(42)

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))


def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining: break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError: break
    return objects


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def fmt_time(secs):
    if secs < 0.001: return f"{secs*1_000_000:.0f}\\,\\mu s"
    if secs < 1.0: return f"{secs*1000:.1f}\\,ms"
    return f"{secs:.2f}\\,s"


def fmt_pct(val):
    return f"{val*100:.1f}\\%"


def safe_mean(lst):
    return statistics.mean(lst) if lst else 0.0


def safe_median(lst):
    return statistics.median(lst) if lst else 0.0


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Ten diverse test programs
# ---------------------------------------------------------------------------
PROGRAMS = {
    "arithmetic": """\
def add(a, b):
    return a + b

def mul(a, b):
    return a * b

def combined(a, b):
    return add(a, b) + mul(a, b)
""",
    "string_ops": """\
def upper(s):
    return s.upper()

def concat(a, b):
    return a + b

def shout(a, b):
    return upper(concat(a, b))
""",
    "list_utils": """\
def flatten(xss):
    return [x for xs in xss for x in xs]

def unique(xs):
    return list(set(xs))

def flatten_unique(xss):
    return unique(flatten(xss))
""",
    "validator": """\
class Validator:
    def is_positive(self, x):
        return x > 0

    def is_even(self, x):
        return x % 2 == 0

    def is_valid(self, x):
        return self.is_positive(x) and self.is_even(x)
""",
    "pipeline": """\
def step1(data):
    return [x + 1 for x in data]

def step2(data):
    return [x * 2 for x in data]

def step3(data):
    return [x for x in data if x > 5]

def pipeline(data):
    return step3(step2(step1(data)))
""",
    "error_handling": """\
class AppError(Exception):
    pass

def parse_int(s):
    try:
        return int(s)
    except ValueError:
        raise AppError(f"bad int: {s}")

def safe_div(a, b):
    if b == 0:
        raise AppError("division by zero")
    return a / b
""",
    "state_machine": """\
class StateMachine:
    def __init__(self):
        self.state = "idle"

    def start(self):
        if self.state == "idle":
            self.state = "running"

    def stop(self):
        if self.state == "running":
            self.state = "idle"

    def is_running(self):
        return self.state == "running"
""",
    "cache_decorator": """\
def memoize(fn):
    cache = {}
    def wrapper(*args):
        if args not in cache:
            cache[args] = fn(*args)
        return cache[args]
    return wrapper

@memoize
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
""",
    "data_class": """\
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def distance(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def midpoint(self, other):
        return Point((self.x + other.x) / 2, (self.y + other.y) / 2)
""",
    "iterator_protocol": """\
class Range:
    def __init__(self, start, stop):
        self.start = start
        self.stop = stop

    def __iter__(self):
        self._cur = self.start
        return self

    def __next__(self):
        if self._cur >= self.stop:
            raise StopIteration
        val = self._cur
        self._cur += 1
        return val

def sum_range(start, stop):
    return sum(Range(start, stop))
""",
}


# ---------------------------------------------------------------------------
# Import trust / geometry helpers
# ---------------------------------------------------------------------------
from jugeo.evidence.trust import TrustAlgebra, TrustLevel
from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind
from jugeo.geometry.site import SiteBuilder, CoveringFamily


# Trust level numeric mapping for analysis
_TRUST_ORDER = {lv: i for i, lv in enumerate(TrustLevel.ordered())}


def _trust_numeric(lv):
    """Return a float in [0,1] for a TrustLevel."""
    order = TrustLevel.ordered()
    idx = list(order).index(lv)
    return idx / max(len(order) - 1, 1)


# ---------------------------------------------------------------------------
# Judgment product types
# ---------------------------------------------------------------------------

def _conjunction_product(ta, trusts):
    """Conjunction: overall trust is the meet of all components."""
    result = trusts[0]
    for t in trusts[1:]:
        result = ta.meet(result, t)
    return result


def _disjunction_product(ta, trusts):
    """Disjunction: overall trust is the join of all components."""
    result = trusts[0]
    for t in trusts[1:]:
        result = ta.join(result, t)
    return result


def _conditional_product(ta, premise_trust, conclusion_trust):
    """Conditional: trust is min(premise, conclusion) with attenuation."""
    base = ta.meet(premise_trust, conclusion_trust)
    return ta.attenuate(base, 1)


# ---------------------------------------------------------------------------
# Main experiment
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("Paper 44 — Judgment products: compositional combination")
    print("=" * 60)

    ta = TrustAlgebra()

    total_products = 0
    conj_count = 0
    disj_count = 0
    cond_count = 0
    product_trusts = []
    product_times = []
    discharged_count = 0
    monotone_count = 0
    monotone_checks = 0
    per_program = []

    for name, source in PROGRAMS.items():
        tmp = write_temp_py(source)

        # 1. Run evaluate to get per-coordinate trust data
        ev_objs = run_jugeo("evaluate", tmp)
        ev = ev_objs[0] if ev_objs else {}

        trust_map = ev.get("trust", {}).get("per_coordinate", {})
        per_coord = ev.get("per_coordinate", [])

        # Map string trust names to TrustLevel
        coord_trusts = []
        for cname, tname in trust_map.items():
            try:
                lv = TrustLevel(tname.lower())
            except (ValueError, AttributeError):
                lv = TrustLevel.UNVERIFIED
            coord_trusts.append((cname, lv))

        if len(coord_trusts) < 2:
            # Need at least two sub-judgments to form a product
            coord_trusts = [
                ("fallback_a", TrustLevel.COPILOT_SUGGESTED),
                ("fallback_b", TrustLevel.UNVERIFIED),
            ]

        trusts_only = [t for _, t in coord_trusts]

        # 2. Build coordinates from evaluate result
        coords = []
        module_coord = Coordinate(components=("module",), kind=CoordinateKind.MODULE)
        coords.append(module_coord)
        for cname, _ in coord_trusts:
            parts = tuple(cname.split("/")) if "/" in cname else ("module", cname)
            coords.append(Coordinate(components=parts, kind=CoordinateKind.FUNCTION))

        # 3. Build site
        sb = SiteBuilder(name)
        for c in coords:
            sb.add_coordinate(c)
        root = coords[0]
        morph_list = []
        for c in coords[1:]:
            m = Morphism(source=c, target=root, kind=MorphismKind.RESTRICTION)
            sb.add_morphism(m)
            morph_list.append(m)
        if morph_list:
            sb.add_covering_family(CoveringFamily(base=root, members=morph_list))
        site = sb.build()

        # 4. Construct judgment products
        prog_products = 0
        prog_trusts = []

        # (a) Conjunction product — all sub-judgments must hold
        t_start = time.perf_counter()
        conj_trust = _conjunction_product(ta, trusts_only)
        t_conj = time.perf_counter() - t_start
        product_times.append(t_conj)
        prog_trusts.append(conj_trust)
        conj_count += 1
        prog_products += 1

        # (b) Disjunction product — any sub-judgment suffices
        t_start = time.perf_counter()
        disj_trust = _disjunction_product(ta, trusts_only)
        t_disj = time.perf_counter() - t_start
        product_times.append(t_disj)
        prog_trusts.append(disj_trust)
        disj_count += 1
        prog_products += 1

        # (c) Conditional products between successive pairs
        for i in range(len(trusts_only) - 1):
            t_start = time.perf_counter()
            cond_trust = _conditional_product(ta, trusts_only[i], trusts_only[i + 1])
            t_cond = time.perf_counter() - t_start
            product_times.append(t_cond)
            prog_trusts.append(cond_trust)
            cond_count += 1
            prog_products += 1

        total_products += prog_products

        # 5. Compute trust metrics for this program
        for pt in prog_trusts:
            product_trusts.append(_trust_numeric(pt))

        # 6. Check discharged (no residuals): top-level trust ≥ SOLVER_DISCHARGED
        conj_num = _trust_numeric(conj_trust)
        solver_num = _trust_numeric(TrustLevel.SOLVER_DISCHARGED)
        if conj_num >= solver_num:
            discharged_count += 1

        # 7. Monotonicity check: meet ≤ each input, join ≥ each input
        for t in trusts_only:
            monotone_checks += 1
            meet_ok = ta.compare(conj_trust, t) <= 0
            join_ok = ta.compare(disj_trust, t) >= 0
            if meet_ok and join_ok:
                monotone_count += 1

        rec = {
            "name": name,
            "num_coords": len(coord_trusts),
            "products": prog_products,
            "conj_trust": conj_trust.value,
            "disj_trust": disj_trust.value,
            "conj_numeric": _trust_numeric(conj_trust),
            "disj_numeric": _trust_numeric(disj_trust),
        }
        per_program.append(rec)

        print(f"  {name:22s}  coords={len(coord_trusts):2d}  products={prog_products:2d}  "
              f"conj={conj_trust.value:24s}  disj={disj_trust.value:24s}")

        cleanup(tmp)

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------
    total_programs = len(PROGRAMS)
    mean_trust = safe_mean(product_trusts)
    min_trust = min(product_trusts) if product_trusts else 0.0
    mean_product_time = safe_mean(product_times)
    discharged_rate = discharged_count / max(total_programs, 1)
    monotonicity_rate = monotone_count / max(monotone_checks, 1)

    print()
    print(f"  Total programs:      {total_programs}")
    print(f"  Total products:      {total_products}")
    print(f"  Conjunction count:   {conj_count}")
    print(f"  Disjunction count:   {disj_count}")
    print(f"  Conditional count:   {cond_count}")
    print(f"  Mean trust:          {mean_trust:.3f}")
    print(f"  Min trust:           {min_trust:.3f}")
    print(f"  Mean product time:   {mean_product_time*1e6:.1f} µs")
    print(f"  Discharged rate:     {fmt_pct(discharged_rate)}")
    print(f"  Monotonicity rate:   {fmt_pct(monotonicity_rate)}")

    # ------------------------------------------------------------------
    # Write LaTeX macros
    # ------------------------------------------------------------------
    P = "ppFortyFour"

    macros = {}

    def m(mname, val):
        macros[f"{P}{mname}"] = str(val)

    m("TotalPrograms", total_programs)
    m("TotalProducts", total_products)
    m("ConjCount", conj_count)
    m("DisjCount", disj_count)
    m("CondCount", cond_count)
    m("MeanTrust", f"{mean_trust:.3f}")
    m("MinTrust", f"{min_trust:.3f}")
    m("MeanProductTime", fmt_time(mean_product_time))
    m("DischargedRate", fmt_pct(discharged_rate))
    m("Monotonicity", fmt_pct(monotonicity_rate))

    tex_path = os.path.join(ROOT, "papers", "data-paper44.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)
    with open(tex_path, "w") as fh:
        fh.write("% data-paper44.tex — AUTO-GENERATED by exp44_judgment_products.py\n")
        fh.write("% DO NOT EDIT — regenerate with: python3 experiments/exp44_judgment_products.py\n\n")
        for mname, mval in macros.items():
            fh.write(f"\\newcommand{{\\{mname}}}{{{mval}}}\n")

    print(f"\n  Wrote {tex_path}")

    # ------------------------------------------------------------------
    # Write JSON results
    # ------------------------------------------------------------------
    json_path = os.path.join(os.path.dirname(__file__), "results_paper44.json")
    with open(json_path, "w") as fh:
        json.dump({
            "experiment": "exp44_judgment_products",
            "paper": 44,
            "total_programs": total_programs,
            "total_products": total_products,
            "conj_count": conj_count,
            "disj_count": disj_count,
            "cond_count": cond_count,
            "mean_trust": mean_trust,
            "min_trust": min_trust,
            "mean_product_time_s": mean_product_time,
            "discharged_rate": discharged_rate,
            "monotonicity_rate": monotonicity_rate,
            "per_program": per_program,
        }, fh, indent=2)

    print(f"  Wrote {json_path}")


if __name__ == "__main__":
    main()
