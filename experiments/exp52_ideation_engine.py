#!/usr/bin/env python3
"""Paper 52 Experiment — Sheaf-Theoretic Program Ideation Engine.

Hypothesis: JuGeo's ideation subsystem (discovery pipeline, theorem ecology,
problem atlas) generates meaningful program insights across diverse domains.

Re-run: python3 experiments/exp52_ideation_engine.py
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
    "polynomial": '''\
def poly_eval(coeffs, x):
    result = 0
    for c in reversed(coeffs):
        result = result * x + c
    return result

def poly_derivative(coeffs):
    if len(coeffs) <= 1:
        return [0]
    return [i * coeffs[i] for i in range(1, len(coeffs))]

def poly_add(a, b):
    length = max(len(a), len(b))
    result = [0] * length
    for i in range(len(a)):
        result[i] += a[i]
    for i in range(len(b)):
        result[i] += b[i]
    return result
''',
    "calculator": '''\
def tokenize(expr):
    tokens, i = [], 0
    while i < len(expr):
        if expr[i].isdigit():
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            tokens.append(float(expr[i:j]))
            i = j
        elif expr[i] in '+-*/()':
            tokens.append(expr[i])
            i += 1
        else:
            i += 1
    return tokens

def evaluate(expr):
    tokens = tokenize(expr)
    pos = [0]
    def parse_expr():
        result = parse_term()
        while pos[0] < len(tokens) and tokens[pos[0]] in ('+', '-'):
            op = tokens[pos[0]]; pos[0] += 1
            right = parse_term()
            result = result + right if op == '+' else result - right
        return result
    def parse_term():
        result = parse_factor()
        while pos[0] < len(tokens) and tokens[pos[0]] in ('*', '/'):
            op = tokens[pos[0]]; pos[0] += 1
            right = parse_factor()
            result = result * right if op == '*' else result / right
        return result
    def parse_factor():
        if tokens[pos[0]] == '(':
            pos[0] += 1
            result = parse_expr()
            pos[0] += 1
            return result
        val = tokens[pos[0]]; pos[0] += 1
        return val
    return parse_expr()
''',
    "tokenizer": '''\
def tokenize_words(text):
    words = []
    current = []
    for ch in text:
        if ch.isalnum() or ch == '_':
            current.append(ch)
        else:
            if current:
                words.append(''.join(current))
                current = []
    if current:
        words.append(''.join(current))
    return words

def word_frequency(text):
    words = tokenize_words(text.lower())
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return freq
''',
    "graph_bfs": '''\
from collections import deque

def bfs(graph, start):
    visited = set()
    queue = deque([start])
    order = []
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                queue.append(neighbor)
    return order

def shortest_path(graph, start, end):
    queue = deque([(start, [start])])
    visited = set()
    while queue:
        node, path = queue.popleft()
        if node == end:
            return path
        if node in visited:
            continue
        visited.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                queue.append((neighbor, path + [neighbor]))
    return []
''',
    "priority_queue": '''\
class MinHeap:
    def __init__(self):
        self._data = []

    def push(self, val):
        self._data.append(val)
        self._sift_up(len(self._data) - 1)

    def pop(self):
        if not self._data:
            raise IndexError("pop from empty heap")
        self._data[0], self._data[-1] = self._data[-1], self._data[0]
        val = self._data.pop()
        if self._data:
            self._sift_down(0)
        return val

    def _sift_up(self, i):
        while i > 0:
            parent = (i - 1) // 2
            if self._data[i] < self._data[parent]:
                self._data[i], self._data[parent] = self._data[parent], self._data[i]
                i = parent
            else:
                break

    def _sift_down(self, i):
        n = len(self._data)
        while 2 * i + 1 < n:
            child = 2 * i + 1
            if child + 1 < n and self._data[child + 1] < self._data[child]:
                child += 1
            if self._data[child] < self._data[i]:
                self._data[i], self._data[child] = self._data[child], self._data[i]
                i = child
            else:
                break
''',
    "lru_cache": '''\
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key not in self.cache:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            oldest = self.order.pop(0)
            del self.cache[oldest]
        self.cache[key] = value
        self.order.append(key)

    def size(self):
        return len(self.cache)
''',
    "unit_converter": '''\
def celsius_to_fahrenheit(c):
    return c * 9.0 / 5.0 + 32

def fahrenheit_to_celsius(f):
    return (f - 32) * 5.0 / 9.0

def meters_to_feet(m):
    return m * 3.28084

def feet_to_meters(ft):
    return ft / 3.28084

def kg_to_pounds(kg):
    return kg * 2.20462

def pounds_to_kg(lb):
    return lb / 2.20462

def convert(value, from_unit, to_unit):
    conversions = {
        ('celsius', 'fahrenheit'): celsius_to_fahrenheit,
        ('fahrenheit', 'celsius'): fahrenheit_to_celsius,
        ('meters', 'feet'): meters_to_feet,
        ('feet', 'meters'): feet_to_meters,
        ('kg', 'pounds'): kg_to_pounds,
        ('pounds', 'kg'): pounds_to_kg,
    }
    func = conversions.get((from_unit, to_unit))
    if func is None:
        raise ValueError(f"Unknown conversion: {from_unit} -> {to_unit}")
    return func(value)
''',
    "permutations": '''\
def permutations(lst):
    if len(lst) <= 1:
        return [lst[:]]
    result = []
    for i in range(len(lst)):
        rest = lst[:i] + lst[i+1:]
        for perm in permutations(rest):
            result.append([lst[i]] + perm)
    return result

def combinations(lst, k):
    if k == 0:
        return [[]]
    if not lst:
        return []
    first = lst[0]
    rest = lst[1:]
    with_first = [[first] + c for c in combinations(rest, k - 1)]
    without_first = combinations(rest, k)
    return with_first + without_first

def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
''',
    "red_black_stub": '''\
class RBNode:
    def __init__(self, key, color='red'):
        self.key = key
        self.color = color
        self.left = None
        self.right = None
        self.parent = None

def rotate_left(root, node):
    right = node.right
    node.right = right.left
    if right.left:
        right.left.parent = node
    right.parent = node.parent
    if not node.parent:
        root = right
    elif node == node.parent.left:
        node.parent.left = right
    else:
        node.parent.right = right
    right.left = node
    node.parent = right
    return root

def insert(root, key):
    node = RBNode(key)
    if not root:
        node.color = 'black'
        return node
    current = root
    while current:
        node.parent = current
        if key < current.key:
            if not current.left:
                current.left = node
                break
            current = current.left
        else:
            if not current.right:
                current.right = node
                break
            current = current.right
    return root
''',
    "json_parser": '''\
def parse_json(text):
    pos = [0]
    def skip_ws():
        while pos[0] < len(text) and text[pos[0]] in ' \\t\\n\\r':
            pos[0] += 1
    def parse_value():
        skip_ws()
        ch = text[pos[0]]
        if ch == '"':
            return parse_string()
        elif ch == '{':
            return parse_object()
        elif ch == '[':
            return parse_array()
        elif ch in '-0123456789':
            return parse_number()
        elif text[pos[0]:pos[0]+4] == 'true':
            pos[0] += 4; return True
        elif text[pos[0]:pos[0]+5] == 'false':
            pos[0] += 5; return False
        elif text[pos[0]:pos[0]+4] == 'null':
            pos[0] += 4; return None
    def parse_string():
        pos[0] += 1
        start = pos[0]
        while text[pos[0]] != '"':
            pos[0] += 1
        val = text[start:pos[0]]
        pos[0] += 1
        return val
    def parse_number():
        start = pos[0]
        while pos[0] < len(text) and text[pos[0]] in '-0123456789.eE+':
            pos[0] += 1
        return float(text[start:pos[0]])
    def parse_object():
        pos[0] += 1; obj = {}
        skip_ws()
        if text[pos[0]] == '}':
            pos[0] += 1; return obj
        while True:
            skip_ws()
            key = parse_string()
            skip_ws(); pos[0] += 1
            obj[key] = parse_value()
            skip_ws()
            if text[pos[0]] == '}':
                pos[0] += 1; return obj
            pos[0] += 1
    def parse_array():
        pos[0] += 1; arr = []
        skip_ws()
        if text[pos[0]] == ']':
            pos[0] += 1; return arr
        while True:
            arr.append(parse_value())
            skip_ws()
            if text[pos[0]] == ']':
                pos[0] += 1; return arr
            pos[0] += 1
    return parse_value()
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        # Load
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        load_time = time.perf_counter() - t0

        # Classify
        t1 = time.perf_counter()
        class_objs = run_jugeo("classify", tmp)
        classify_time = time.perf_counter() - t1

        # Parse load
        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        morphisms = summary.get("morphisms", 0)
        covers = summary.get("covering_families", 0)
        judgments = summary.get("judgments", 0)
        bindings = summary.get("context_bindings", 0)

        # Parse classify
        class_data = class_objs[0] if class_objs else {}
        classification = class_data.get("classification", {})
        category = classification.get("category", "UNKNOWN")
        site_struct = class_data.get("site_structure", {})
        coord_count = site_struct.get("coordinate_count", coords)

        return {
            "name": name,
            "load_time": round(load_time, 4),
            "classify_time": round(classify_time, 4),
            "coords": coords,
            "morphisms": morphisms,
            "covers": covers,
            "judgments": judgments,
            "bindings": bindings,
            "category": category,
            "coord_count": coord_count,
        }
    finally:
        cleanup(tmp)


def fmt_time(seconds):
    if seconds < 0.01:
        return f"{seconds*1000:.1f}\\,ms"
    return f"{seconds:.2f}\\,s"

def fmt_float(val, decimals=1):
    return f"{val:.{decimals}f}"

def fmt_pct(ratio):
    return f"{ratio*100:.1f}\\%"


def main():
    print("=" * 72)
    print("Paper 52: Sheaf-Theoretic Program Ideation Engine")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Coords: {m['coords']}, Morphisms: {m['morphisms']}, Covers: {m['covers']}")
        print(f"    Category: {m['category']}")
        print(f"    Times: load={m['load_time']:.3f}s classify={m['classify_time']:.3f}s")

    # Run discovery and economics (global, not per-program)
    print("\n  Running ideation discovery...")
    t_disc = time.perf_counter()
    disc_objs = run_jugeo("ideate", "--discover")
    discover_time = time.perf_counter() - t_disc
    disc_data = disc_objs[0] if disc_objs else {}
    fields_scanned = disc_data.get("fields_scanned", 0)
    candidates_proposed = disc_data.get("candidates_proposed", 0)
    candidates_novel = disc_data.get("candidates_novel", 0)
    candidates_falsified = disc_data.get("candidates_falsified", 0)

    print("\n  Running ideation economics...")
    t_econ = time.perf_counter()
    econ_objs = run_jugeo("ideate", "--economics")
    econ_time = time.perf_counter() - t_econ
    econ_data = econ_objs[0] if econ_objs else {}
    econ_candidates = econ_data.get("candidates_proposed", 0)
    theorems = econ_data.get("theorems", [])
    mean_yield = statistics.mean([t.get("yield_estimate", 0) for t in theorems]) if theorems else 0

    # Aggregates
    n = len(results)
    mean_coords = statistics.mean([r["coords"] for r in results])
    mean_morphisms = statistics.mean([r["morphisms"] for r in results])
    total_covers = sum(r["covers"] for r in results)
    mean_classify = statistics.mean([r["classify_time"] for r in results])
    mean_load = statistics.mean([r["load_time"] for r in results])
    categories = [r["category"] for r in results]
    top_category = max(set(categories), key=categories.count)
    ideation_rate = sum(1 for r in results if r["coords"] > 0) / n if n else 0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:           {n}")
    print(f"  Mean coords:        {mean_coords:.1f}")
    print(f"  Mean morphisms:     {mean_morphisms:.1f}")
    print(f"  Discovery fields:   {fields_scanned}")
    print(f"  Discovery novel:    {candidates_novel}")
    print(f"  Top category:       {top_category}")

    # Write LaTeX
    tex_path = os.path.join(ROOT, "papers", "data-paper52.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper52.tex — AUTO-GENERATED by exp52_ideation_engine.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp52_ideation_engine.py\n\n")
        f.write(f"\\newcommand{{\\ppLIItotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLIImeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLIImeanMorphisms}}{{{fmt_float(mean_morphisms)}}}\n")
        f.write(f"\\newcommand{{\\ppLIItotalCovers}}{{{total_covers}}}\n")
        f.write(f"\\newcommand{{\\ppLIIdiscoveryFields}}{{{fields_scanned}}}\n")
        f.write(f"\\newcommand{{\\ppLIIdiscoveryCandidates}}{{{candidates_proposed}}}\n")
        f.write(f"\\newcommand{{\\ppLIIdiscoveryNovel}}{{{candidates_novel}}}\n")
        f.write(f"\\newcommand{{\\ppLIIdiscoveryFalsified}}{{{candidates_falsified}}}\n")
        f.write(f"\\newcommand{{\\ppLIImeanClassifyTime}}{{{fmt_time(mean_classify)}}}\n")
        f.write(f"\\newcommand{{\\ppLIImeanLoadTime}}{{{fmt_time(mean_load)}}}\n")
        f.write(f"\\newcommand{{\\ppLIImeanDiscoverTime}}{{{fmt_time(discover_time)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIcategoryBreakdown}}{{{top_category}}}\n")
        f.write(f"\\newcommand{{\\ppLIIideationRate}}{{{fmt_pct(ideation_rate)}}}\n")
        f.write(f"\\newcommand{{\\ppLIImeanEconYield}}{{{fmt_float(mean_yield, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLIItotalEconCandidates}}{{{econ_candidates}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper52.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results, "discovery": disc_data, "economics": econ_data}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
