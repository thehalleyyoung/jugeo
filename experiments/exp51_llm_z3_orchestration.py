#!/usr/bin/env python3
"""Paper 51 Experiment — LLM-Z3 Orchestration.

Hypothesis: LLM suggestions get systematically upgraded to solver-discharged
proofs through the JuGeo orchestration pipeline.  We measure how evaluate,
encode, and descend interact on diverse programs.

Methodology:
  - jugeo evaluate FILE.py  — per-coordinate trust, pipeline results
  - jugeo encode   FILE.py  — SMT-LIB encodings (declarations, assertions)
  - jugeo descend  FILE.py  — descent verdict, trust, propositions
  - Time each operation

Every number is produced by the jugeo CLI (subprocess).
Re-run: python3 experiments/exp51_llm_z3_orchestration.py
"""
import subprocess, json, os, tempfile, time, ast, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

# -- CLI helpers ---------------------------------------------------------------

def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
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
    """Write source to a temp .py file, return path."""
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# -- 10 Benchmark Programs ----------------------------------------------------

PROGRAMS = {
    "overflow_safe_arith": '''\
def safe_add(a, b, max_val=2**31 - 1, min_val=-(2**31)):
    if b > 0 and a > max_val - b:
        raise OverflowError("Addition overflow")
    if b < 0 and a < min_val - b:
        raise OverflowError("Addition underflow")
    return a + b


def safe_mul(a, b, max_val=2**31 - 1, min_val=-(2**31)):
    if a == 0 or b == 0:
        return 0
    if a > 0 and b > 0 and a > max_val // b:
        raise OverflowError("Multiplication overflow")
    if a < 0 and b < 0 and a < max_val // b:
        raise OverflowError("Multiplication overflow")
    if a > 0 and b < 0 and b < min_val // a:
        raise OverflowError("Multiplication underflow")
    if a < 0 and b > 0 and a < min_val // b:
        raise OverflowError("Multiplication underflow")
    return a * b


def safe_div(a, b):
    if b == 0:
        raise ZeroDivisionError("Division by zero")
    return a // b
''',

    "binary_search": '''\
def binary_search(arr, target):
    left = 0
    right = len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1


def binary_search_insert(arr, target):
    left = 0
    right = len(arr)
    while left < right:
        mid = (left + right) // 2
        if arr[mid] < target:
            left = mid + 1
        else:
            right = mid
    return left


def search_range(arr, target):
    lo = binary_search_insert(arr, target)
    if lo >= len(arr) or arr[lo] != target:
        return (-1, -1)
    hi = binary_search_insert(arr, target + 1) - 1
    return (lo, hi)
''',

    "stack_class": '''\
class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if self.is_empty():
            raise IndexError("Pop from empty stack")
        return self._items.pop()

    def peek(self):
        if self.is_empty():
            raise IndexError("Peek at empty stack")
        return self._items[-1]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)


def balanced_parens(s):
    stack = Stack()
    mapping = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in '([{':
            stack.push(ch)
        elif ch in ')]}':
            if stack.is_empty() or stack.pop() != mapping[ch]:
                return False
    return stack.is_empty()
''',

    "gcd_lcm": '''\
def gcd(a, b):
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def lcm(a, b):
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x1, y1 = extended_gcd(b % a, a)
    return g, y1 - (b // a) * x1, x1


def multi_gcd(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result


def multi_lcm(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = lcm(result, n)
    return result
''',

    "merge_sort": '''\
def merge_sort(arr):
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)


def merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result


def is_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True
''',

    "caesar_cipher": '''\
def caesar_encode(text, shift):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord('A') if ch.isupper() else ord('a')
            result.append(chr((ord(ch) - base + shift) % 26 + base))
        else:
            result.append(ch)
    return ''.join(result)


def caesar_decode(text, shift):
    return caesar_encode(text, -shift)


def caesar_brute_force(ciphertext):
    candidates = []
    for shift in range(26):
        candidates.append((shift, caesar_decode(ciphertext, shift)))
    return candidates


def rot13(text):
    return caesar_encode(text, 13)
''',

    "matrix_ops": '''\
def mat_multiply(a, b):
    rows_a, cols_a = len(a), len(a[0])
    rows_b, cols_b = len(b), len(b[0])
    if cols_a != rows_b:
        raise ValueError("Incompatible dimensions")
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result


def mat_transpose(m):
    if not m:
        return []
    rows, cols = len(m), len(m[0])
    return [[m[i][j] for i in range(rows)] for j in range(cols)]


def mat_identity(n):
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]


def mat_trace(m):
    return sum(m[i][i] for i in range(min(len(m), len(m[0]))))
''',

    "fibonacci": '''\
def fib_iterative(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def fib_recursive(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    return fib_recursive(n - 1) + fib_recursive(n - 2)


def fib_list(n):
    if n <= 0:
        return []
    fibs = [0]
    if n >= 2:
        fibs.append(1)
    for i in range(2, n):
        fibs.append(fibs[-1] + fibs[-2])
    return fibs


def is_fibonacci(num):
    if num < 0:
        return False
    a, b = 0, 1
    while b < num:
        a, b = b, a + b
    return b == num or num == 0
''',

    "state_machine": '''\
class TrafficLight:
    STATES = ('red', 'green', 'yellow')
    TRANSITIONS = {
        'red': 'green',
        'green': 'yellow',
        'yellow': 'red',
    }

    def __init__(self):
        self.state = 'red'
        self.history = ['red']

    def advance(self):
        self.state = self.TRANSITIONS[self.state]
        self.history.append(self.state)
        return self.state

    def reset(self):
        self.state = 'red'
        self.history = ['red']

    def cycle(self, n):
        for _ in range(n):
            self.advance()
        return self.state

    def is_valid_state(self):
        return self.state in self.STATES

    def get_history(self):
        return list(self.history)
''',

    "url_parser": '''\
def parse_url(url):
    result = {'scheme': '', 'host': '', 'port': None, 'path': '', 'query': '', 'fragment': ''}
    if '#' in url:
        url, result['fragment'] = url.rsplit('#', 1)
    if '?' in url:
        url, result['query'] = url.split('?', 1)
    if '://' in url:
        result['scheme'], url = url.split('://', 1)
    if '/' in url:
        host_part, result['path'] = url.split('/', 1)
        result['path'] = '/' + result['path']
    else:
        host_part = url
    if ':' in host_part:
        result['host'], port_str = host_part.rsplit(':', 1)
        try:
            result['port'] = int(port_str)
        except ValueError:
            result['host'] = host_part
    else:
        result['host'] = host_part
    return result


def parse_query_string(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)
            params[key] = value
        else:
            params[pair] = ''
    return params


def build_url(parts):
    url = ''
    if parts.get('scheme'):
        url += parts['scheme'] + '://'
    url += parts.get('host', '')
    if parts.get('port'):
        url += ':' + str(parts['port'])
    url += parts.get('path', '')
    if parts.get('query'):
        url += '?' + parts['query']
    if parts.get('fragment'):
        url += '#' + parts['fragment']
    return url
''',
}


# -- Measurement helpers -------------------------------------------------------

TRUST_RANK = {
    "CONTRADICTED": 0,
    "UNVERIFIED": 1,
    "unverified": 1,
    "COPILOT_SUGGESTED": 2,
    "copilot_suggested": 2,
    "SOLVER_DISCHARGED": 3,
    "solver_discharged": 3,
    "ORACLE_ATTESTED": 4,
    "oracle_attested": 4,
    "VERIFIED_PROOF": 5,
    "verified_proof": 5,
}


def trust_score(label):
    """Map trust label to numeric score 0-5."""
    return TRUST_RANK.get(str(label).upper(), 1)


def measure_program(name, source):
    """Run evaluate, encode, descend on a single program."""
    tmp = write_temp_py(source)
    rec = {"name": name}

    try:
        # --- evaluate ---
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        rec["evaluate_time_s"] = round(time.perf_counter() - t0, 4)
        ev = eval_objs[0] if eval_objs else {}
        rec["aggregate_trust"] = ev.get("trust", {}).get("aggregate_trust", "unverified")
        per_coord = ev.get("per_coordinate", [])
        rec["eval_coordinates"] = len(per_coord)
        rec["eval_trust_score"] = trust_score(rec["aggregate_trust"])
        coord_trusts = []
        for c in per_coord:
            t = str(c.get("trust", "unverified"))
            for part in t.split(","):
                for k, v in TRUST_RANK.items():
                    if k.lower() in part.lower():
                        coord_trusts.append(v)
                        break
        rec["per_coord_trust_scores"] = coord_trusts
        discharged = sum(1 for s in coord_trusts if s >= 3)
        rec["discharged_coords"] = discharged
        rec["eval_coverage"] = ev.get("coverage", 0.0)

        # --- encode ---
        t0 = time.perf_counter()
        enc_objs = run_jugeo("encode", tmp)
        rec["encode_time_s"] = round(time.perf_counter() - t0, 4)
        enc = enc_objs[0] if enc_objs else {}
        files = enc.get("files", [])
        total_decl = 0
        total_assert = 0
        encoded_coords = 0
        for finfo in files:
            coords_dict = finfo.get("coordinates", {})
            for cname, cdata in coords_dict.items():
                decl = cdata.get("declarations", 0)
                assrt = cdata.get("assertions", 0)
                total_decl += decl
                total_assert += assrt
                if decl > 0 or assrt > 0:
                    encoded_coords += 1
        rec["total_declarations"] = total_decl
        rec["total_assertions"] = total_assert
        rec["encoded_coords"] = encoded_coords

        # --- descend ---
        t0 = time.perf_counter()
        desc_objs = run_jugeo("descend", tmp)
        rec["descent_time_s"] = round(time.perf_counter() - t0, 4)
        ds = desc_objs[0] if desc_objs else {}
        rec["descent_verdict"] = ds.get("verdict", "unknown")
        rec["descent_trust"] = ds.get("trust", "unverified")
        rec["descent_trust_score"] = trust_score(rec["descent_trust"])
        sections = ds.get("sections_detail", [])
        props_total = sum(s.get("propositions", 0) for s in sections)
        props_ok = sum(s.get("ok", 0) for s in sections)
        rec["propositions_total"] = props_total
        rec["propositions_ok"] = props_ok
        rec["obstructions"] = len(ds.get("obstructions", []))
        rec["verified"] = rec["descent_verdict"].lower() == "verified"

    except Exception as e:
        rec["error"] = str(e)

    finally:
        cleanup(tmp)

    return rec


# -- LaTeX macro emitter ------------------------------------------------------

def write_latex_macros(macros, path):
    """Write LaTeX \\newcommand macros to a file."""
    header = ("% data-paper51.tex — Experimental data for Paper 51: "
              "LLM-Z3 Orchestration\n"
              "% AUTO-GENERATED by experiments/exp51_llm_z3_orchestration.py\n"
              "% Do not edit manually.\n\n")
    with open(path, "w") as f:
        f.write(header)
        for name, value in macros:
            f.write("\\newcommand{{\\{}}}{{{}}}\n".format(name, value))
    print("  Wrote {} macros to {}".format(len(macros), path))


# -- Main ----------------------------------------------------------------------

def main():
    print("=" * 72)
    print("Paper 51: LLM-Z3 Orchestration")
    print("=" * 72)

    # Validate all programs parse
    parse_errors = 0
    for name, source in PROGRAMS.items():
        try:
            ast.parse(source)
        except SyntaxError as e:
            print("  PARSE ERROR in {}: {}".format(name, e))
            parse_errors += 1
    if parse_errors:
        print("  {} parse errors — aborting.".format(parse_errors))
        return
    print("  All {} sources parse OK.\n".format(len(PROGRAMS)))

    # Run measurements
    print("  Running evaluate + encode + descend on {} programs...\n".format(
        len(PROGRAMS)))
    results = []
    for name, source in PROGRAMS.items():
        rec = measure_program(name, source)
        results.append(rec)
        mark = "OK" if rec.get("verified", False) else "XX"
        print("  [{m}] {n:25s}  eval={et:.3f}s  enc={nt:.3f}s  desc={dt:.3f}s  "
              "asserts={a}  verdict={v}  trust={t}".format(
                  m=mark, n=rec["name"],
                  et=rec.get("evaluate_time_s", 0),
                  nt=rec.get("encode_time_s", 0),
                  dt=rec.get("descent_time_s", 0),
                  a=rec.get("total_assertions", 0),
                  v=rec.get("descent_verdict", "?"),
                  t=rec.get("descent_trust", "?")))

    # -- Aggregate statistics --------------------------------------------------
    n = len(results)
    total_assertions = sum(r.get("total_assertions", 0) for r in results)
    total_encoded = sum(r.get("encoded_coords", 0) for r in results)
    mean_assertions = round(total_assertions / n, 1) if n else 0
    discharge_count = sum(1 for r in results
                         if r.get("descent_trust_score", 0) >= 3)
    discharge_rate = round(discharge_count / n, 4) if n else 0
    verified_count = sum(1 for r in results if r.get("verified", False))

    eval_times = [r.get("evaluate_time_s", 0) for r in results]
    encode_times = [r.get("encode_time_s", 0) for r in results]
    descent_times = [r.get("descent_time_s", 0) for r in results]

    mean_eval = round(statistics.mean(eval_times), 4) if eval_times else 0
    mean_encode = round(statistics.mean(encode_times), 4) if encode_times else 0
    mean_descent = round(statistics.mean(descent_times), 4) if descent_times else 0

    total_props = sum(r.get("propositions_total", 0) for r in results)
    total_props_ok = sum(r.get("propositions_ok", 0) for r in results)
    mean_props = round(total_props / n, 1) if n else 0
    mean_props_ok = round(total_props_ok / n, 1) if n else 0
    prop_ratio = round(total_props_ok / total_props, 4) if total_props else 0

    trust_scores = [r.get("eval_trust_score", 1) for r in results]
    mean_trust = round(statistics.mean(trust_scores), 2) if trust_scores else 0

    # -- Print summary ---------------------------------------------------------
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("  Programs:              {}".format(n))
    print("  Encoded coordinates:   {}".format(total_encoded))
    print("  Total SMT assertions:  {}".format(total_assertions))
    print("  Mean assertions/prog:  {}".format(mean_assertions))
    print("  Discharge rate:        {:.1%}".format(discharge_rate))
    print("  Verified count:        {}/{}".format(verified_count, n))
    print("  Mean evaluate time:    {:.4f}s".format(mean_eval))
    print("  Mean encode time:      {:.4f}s".format(mean_encode))
    print("  Mean descent time:     {:.4f}s".format(mean_descent))
    print("  Total propositions:    {}".format(total_props))
    print("  Total props satisfied: {}".format(total_props_ok))
    print("  Mean props/prog:       {}".format(mean_props))
    print("  Mean props OK/prog:    {}".format(mean_props_ok))
    print("  Prop satisfaction:     {:.1%}".format(prop_ratio))
    print("  Mean trust score:      {}".format(mean_trust))

    # -- LaTeX macros ----------------------------------------------------------
    macros = [
        ("ppLItotalPrograms",    n),
        ("ppLIencodedCoords",    total_encoded),
        ("ppLItotalAssertions",  total_assertions),
        ("ppLImeanAssertions",   mean_assertions),
        ("ppLIdischargeRate",    "{:.1\\%}".format(discharge_rate * 100)),
        ("ppLImeanEncodeTime",   "{:.4f}\\,s".format(mean_encode)),
        ("ppLImeanDescentTime",  "{:.4f}\\,s".format(mean_descent)),
        ("ppLImeanOrchTime",     "{:.4f}\\,s".format(mean_eval)),
        ("ppLIverifiedCount",    verified_count),
        ("ppLImeanProps",        mean_props),
        ("ppLImeanPropsOk",      mean_props_ok),
        ("ppLItotalProps",       total_props),
        ("ppLItotalPropsOk",     total_props_ok),
        ("ppLIpropRatio",        "{:.1\\%}".format(prop_ratio * 100)),
        ("ppLImeanTrustScore",   mean_trust),
    ]
    tex_path = os.path.join(ROOT, "papers", "data-paper51.tex")
    write_latex_macros(macros, tex_path)

    # -- JSON results ----------------------------------------------------------
    full = {
        "experiment": "llm_z3_orchestration",
        "paper": 51,
        "program_count": n,
        "programs": results,
        "summary": {
            "total_programs": n,
            "encoded_coords": total_encoded,
            "total_assertions": total_assertions,
            "mean_assertions": mean_assertions,
            "discharge_rate": discharge_rate,
            "verified_count": verified_count,
            "mean_evaluate_time_s": mean_eval,
            "mean_encode_time_s": mean_encode,
            "mean_descent_time_s": mean_descent,
            "total_propositions": total_props,
            "total_propositions_ok": total_props_ok,
            "mean_props": mean_props,
            "mean_props_ok": mean_props_ok,
            "prop_ratio": prop_ratio,
            "mean_trust_score": mean_trust,
            "note": "All numbers from jugeo CLI via subprocess",
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper51.json")
    with open(json_path, "w") as f:
        json.dump(full, f, indent=2, default=str)
    print("\n  Results saved to {}".format(json_path))


if __name__ == "__main__":
    main()
