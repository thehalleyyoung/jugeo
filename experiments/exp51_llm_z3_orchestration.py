#!/usr/bin/env python3
"""Paper 51 Experiment — LLM-Z3 Orchestration: Timing the Trust Upgrade Pipeline.

Hypothesis: JuGeo's orchestration pipeline reliably upgrades LLM-suggested
judgments to solver-discharged proofs, with bounded overhead.

Methodology:
  - jugeo evaluate  on 10 programs (full orchestration)
  - jugeo encode    on 10 programs (SMT encoding)
  - jugeo descend   on 10 programs (descent verification)
  - Measure timing, trust tiers, proposition counts

Every number is produced by the jugeo CLI (subprocess).
Re-run: python3 experiments/exp51_llm_z3_orchestration.py
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
    "overflow_arith": '''\
def safe_add(x, y, max_val=2**31 - 1, min_val=-(2**31)):
    result = x + y
    if result > max_val:
        return max_val
    if result < min_val:
        return min_val
    return result

def safe_multiply(x, y, max_val=2**31 - 1, min_val=-(2**31)):
    result = x * y
    if result > max_val:
        return max_val
    if result < min_val:
        return min_val
    return result
''',
    "binary_search": '''\
def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1

def search_range(arr, target):
    left = binary_search(arr, target)
    if left == -1:
        return (-1, -1)
    right = left
    while right + 1 < len(arr) and arr[right + 1] == target:
        right += 1
    return (left, right)
''',
    "stack_class": '''\
class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if self.is_empty():
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        if self.is_empty():
            raise IndexError("peek at empty stack")
        return self._items[-1]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)
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

def gcd_of_list(numbers):
    from functools import reduce
    return reduce(gcd, numbers)
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
''',
    "caesar_cipher": '''\
def caesar_encrypt(text, shift):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord('A') if ch.isupper() else ord('a')
            result.append(chr((ord(ch) - base + shift) % 26 + base))
        else:
            result.append(ch)
    return ''.join(result)

def caesar_decrypt(text, shift):
    return caesar_encrypt(text, -shift)
''',
    "matrix_ops": '''\
def matrix_multiply(a, b):
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result

def transpose(matrix):
    rows, cols = len(matrix), len(matrix[0])
    return [[matrix[i][j] for i in range(rows)] for j in range(cols)]

def matrix_add(a, b):
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]
''',
    "fibonacci": '''\
def fibonacci_iter(n):
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b

def fibonacci_list(n):
    if n <= 0:
        return []
    fibs = [0, 1]
    for i in range(2, n):
        fibs.append(fibs[-1] + fibs[-2])
    return fibs[:n]

def is_fibonacci(num):
    a, b = 0, 1
    while b < num:
        a, b = b, a + b
    return b == num or num == 0
''',
    "state_machine": '''\
class TrafficLight:
    STATES = ['red', 'green', 'yellow']

    def __init__(self):
        self.state = 'red'
        self.cycle_count = 0

    def advance(self):
        idx = self.STATES.index(self.state)
        self.state = self.STATES[(idx + 1) % len(self.STATES)]
        if self.state == 'red':
            self.cycle_count += 1

    def is_safe_to_cross(self):
        return self.state == 'green'

    def reset(self):
        self.state = 'red'
        self.cycle_count = 0
''',
    "url_parser": '''\
def parse_url(url):
    result = {'scheme': '', 'host': '', 'port': '', 'path': '', 'query': ''}
    if '://' in url:
        result['scheme'], url = url.split('://', 1)
    if '?' in url:
        url, result['query'] = url.split('?', 1)
    if '/' in url:
        host_part, result['path'] = url.split('/', 1)
        result['path'] = '/' + result['path']
    else:
        host_part = url
    if ':' in host_part:
        result['host'], result['port'] = host_part.rsplit(':', 1)
    else:
        result['host'] = host_part
    return result

def build_url(scheme, host, path='/', port='', query=''):
    url = f"{scheme}://{host}"
    if port:
        url += f":{port}"
    url += path
    if query:
        url += f"?{query}"
    return url
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        # Evaluate (orchestration)
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t0

        # Encode (SMT)
        t1 = time.perf_counter()
        enc_objs = run_jugeo("encode", tmp)
        encode_time = time.perf_counter() - t1

        # Descend
        t2 = time.perf_counter()
        desc_objs = run_jugeo("descend", tmp)
        descend_time = time.perf_counter() - t2

        # Parse evaluate
        eval_data = eval_objs[0] if eval_objs else {}
        trust_info = eval_data.get("trust", {})
        per_coord = eval_data.get("per_coordinate", [])
        descent = eval_data.get("descent", {})
        cover_q = eval_data.get("cover_quality", {})

        # Parse encode
        enc_data = enc_objs[0] if enc_objs else {}
        enc_files = enc_data.get("files", [{}])
        enc_file = enc_files[0] if enc_files else {}
        coordinates = enc_file.get("coordinates", {})
        total_assertions = sum(
            c.get("assertions", 0) for c in coordinates.values()
        )
        total_declarations = sum(
            c.get("declarations", 0) for c in coordinates.values()
        )
        encoded_coords = len(coordinates)
        decidable_coords = sum(
            1 for c in coordinates.values() if c.get("decidability") == "decidable"
        )

        # Parse descend
        desc_data = desc_objs[0] if desc_objs else {}
        verdict = desc_data.get("verdict", "unknown")
        desc_trust = desc_data.get("trust", "unknown")
        local_sections = desc_data.get("local_sections", 0)
        sections_detail = desc_data.get("sections_detail", [])
        props_total = sum(s.get("propositions", 0) for s in sections_detail)
        props_ok = sum(s.get("ok", 0) for s in sections_detail)
        obstructions = len(desc_data.get("obstructions", []))

        discharged = desc_trust in ("SOLVER_DISCHARGED", "solver_discharged")

        return {
            "name": name,
            "eval_time": round(eval_time, 4),
            "encode_time": round(encode_time, 4),
            "descend_time": round(descend_time, 4),
            "encoded_coords": encoded_coords,
            "total_assertions": total_assertions,
            "total_declarations": total_declarations,
            "decidable_coords": decidable_coords,
            "verdict": verdict,
            "trust": desc_trust,
            "discharged": discharged,
            "local_sections": local_sections,
            "props_total": props_total,
            "props_ok": props_ok,
            "obstructions": obstructions,
            "cover_quality": cover_q.get("total_score", 0),
            "per_coord_qualities": [c.get("quality", 0) for c in per_coord],
        }
    finally:
        cleanup(tmp)


def fmt_time(seconds):
    if seconds < 0.01:
        return f"{seconds*1000:.1f}\\,ms"
    return f"{seconds:.2f}\\,s"

def fmt_pct(ratio):
    return f"{ratio*100:.1f}\\%"

def fmt_float(val, decimals=1):
    return f"{val:.{decimals}f}"


def main():
    print("=" * 72)
    print("Paper 51: LLM-Z3 Orchestration — Trust Upgrade Pipeline")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Coords: {m['encoded_coords']}, Assertions: {m['total_assertions']}")
        print(f"    Props: {m['props_ok']}/{m['props_total']}, Verdict: {m['verdict']}")
        print(f"    Trust: {m['trust']}, Discharged: {m['discharged']}")
        print(f"    Times: eval={m['eval_time']:.3f}s encode={m['encode_time']:.3f}s descend={m['descend_time']:.3f}s")

    # Aggregates
    n = len(results)
    total_assertions = sum(r["total_assertions"] for r in results)
    total_encoded = sum(r["encoded_coords"] for r in results)
    total_props = sum(r["props_total"] for r in results)
    total_props_ok = sum(r["props_ok"] for r in results)
    discharged_count = sum(1 for r in results if r["discharged"])
    verified_count = sum(1 for r in results if r["verdict"] == "verified")
    mean_assertions = total_assertions / n if n else 0
    mean_encode = statistics.mean([r["encode_time"] for r in results])
    mean_descend = statistics.mean([r["descend_time"] for r in results])
    mean_eval = statistics.mean([r["eval_time"] for r in results])
    mean_props = total_props / n if n else 0
    mean_props_ok = total_props_ok / n if n else 0
    prop_ratio = total_props_ok / total_props if total_props else 0
    discharge_rate = discharged_count / n if n else 0
    all_qualities = [q for r in results for q in r["per_coord_qualities"]]
    mean_trust_score = statistics.mean(all_qualities) if all_qualities else 0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:         {n}")
    print(f"  Encoded coords:   {total_encoded}")
    print(f"  Total assertions: {total_assertions}")
    print(f"  Discharge rate:   {fmt_pct(discharge_rate)}")
    print(f"  Verified:         {verified_count}/{n}")
    print(f"  Props ratio:      {fmt_pct(prop_ratio)}")
    print(f"  Mean eval time:   {fmt_time(mean_eval)}")

    # Write LaTeX macros
    tex_path = os.path.join(ROOT, "papers", "data-paper51.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper51.tex — AUTO-GENERATED by exp51_llm_z3_orchestration.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp51_llm_z3_orchestration.py\n\n")
        f.write(f"\\newcommand{{\\ppLItotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLIencodedCoords}}{{{total_encoded}}}\n")
        f.write(f"\\newcommand{{\\ppLItotalAssertions}}{{{total_assertions}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanAssertions}}{{{fmt_float(mean_assertions)}}}\n")
        f.write(f"\\newcommand{{\\ppLIdischargeRate}}{{{fmt_pct(discharge_rate)}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanEncodeTime}}{{{fmt_time(mean_encode)}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanDescentTime}}{{{fmt_time(mean_descend)}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanOrchTime}}{{{fmt_time(mean_eval)}}}\n")
        f.write(f"\\newcommand{{\\ppLIverifiedCount}}{{{verified_count}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanProps}}{{{fmt_float(mean_props)}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanPropsOk}}{{{fmt_float(mean_props_ok)}}}\n")
        f.write(f"\\newcommand{{\\ppLItotalProps}}{{{total_props}}}\n")
        f.write(f"\\newcommand{{\\ppLItotalPropsOk}}{{{total_props_ok}}}\n")
        f.write(f"\\newcommand{{\\ppLIpropRatio}}{{{fmt_pct(prop_ratio)}}}\n")
        f.write(f"\\newcommand{{\\ppLImeanTrustScore}}{{{fmt_float(mean_trust_score, 3)}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    # Save JSON
    json_path = os.path.join(os.path.dirname(__file__), "results_paper51.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results, "summary": {
            "total": n, "verified": verified_count, "discharged": discharged_count,
            "total_assertions": total_assertions, "total_props": total_props,
            "total_props_ok": total_props_ok, "mean_eval": mean_eval,
        }}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
