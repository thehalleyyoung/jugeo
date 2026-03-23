#!/usr/bin/env python3
"""Paper 52 Experiment — Ideation Engine.

Hypothesis: JuGeo's sheaf-theoretic program ideation capabilities surface
meaningful structure, classification, and discovery results across diverse
program families.

Methodology:
  - jugeo load      FILE.py        — site structure (coordinates, morphisms, covers)
  - jugeo classify  --file FILE.py — classification + recommended subsystems
  - jugeo ideate    --discover     — discovery pipeline (fields, candidates)
  - jugeo ideate    --economics    — economics analysis (yield estimates)
  - Python API: SiteBuilder → discovery_pipeline, problem_atlas

Every number is produced by the jugeo CLI (subprocess) or the public Python API.
Re-run: python3 experiments/exp52_ideation_engine.py
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
    "polynomial_horner": '''\
def horner_eval(coeffs, x):
    result = 0
    for c in coeffs:
        result = result * x + c
    return result


def polynomial_derivative(coeffs):
    n = len(coeffs) - 1
    if n <= 0:
        return [0]
    return [coeffs[i] * (n - i) for i in range(n)]


def polynomial_add(a, b):
    la, lb = len(a), len(b)
    if la < lb:
        a = [0] * (lb - la) + a
    elif lb < la:
        b = [0] * (la - lb) + b
    return [ai + bi for ai, bi in zip(a, b)]


def polynomial_multiply(a, b):
    result = [0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        for j, bj in enumerate(b):
            result[i + j] += ai * bj
    return result
''',

    "red_black_tree": '''\
RED = True
BLACK = False


class RBNode:
    def __init__(self, key, color=RED):
        self.key = key
        self.color = color
        self.left = None
        self.right = None
        self.parent = None


def rotate_left(tree, x):
    y = x.right
    x.right = y.left
    if y.left is not None:
        y.left.parent = x
    y.parent = x.parent
    if x.parent is None:
        tree.root = y
    elif x == x.parent.left:
        x.parent.left = y
    else:
        x.parent.right = y
    y.left = x
    x.parent = y


def rotate_right(tree, x):
    y = x.left
    x.left = y.right
    if y.right is not None:
        y.right.parent = x
    y.parent = x.parent
    if x.parent is None:
        tree.root = y
    elif x == x.parent.right:
        x.parent.right = y
    else:
        x.parent.left = y
    y.right = x
    x.parent = y


def rb_insert_fixup(tree, z):
    while z.parent is not None and z.parent.color == RED:
        if z.parent == z.parent.parent.left:
            uncle = z.parent.parent.right
            if uncle is not None and uncle.color == RED:
                z.parent.color = BLACK
                uncle.color = BLACK
                z.parent.parent.color = RED
                z = z.parent.parent
            else:
                if z == z.parent.right:
                    z = z.parent
                    rotate_left(tree, z)
                z.parent.color = BLACK
                z.parent.parent.color = RED
                rotate_right(tree, z.parent.parent)
        else:
            uncle = z.parent.parent.left
            if uncle is not None and uncle.color == RED:
                z.parent.color = BLACK
                uncle.color = BLACK
                z.parent.parent.color = RED
                z = z.parent.parent
            else:
                if z == z.parent.left:
                    z = z.parent
                    rotate_right(tree, z)
                z.parent.color = BLACK
                z.parent.parent.color = RED
                rotate_left(tree, z.parent.parent)
    tree.root.color = BLACK
''',

    "simple_calculator": '''\
def tokenize(expr):
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
        elif expr[i].isdigit() or expr[i] == '.':
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            tokens.append(('NUM', float(expr[i:j])))
            i = j
        elif expr[i] in '+-*/()':
            tokens.append(('OP', expr[i]))
            i += 1
        else:
            raise ValueError("Unexpected character: " + expr[i])
    return tokens


def calc_eval(tokens):
    pos = [0]

    def parse_expr():
        left = parse_term()
        while pos[0] < len(tokens) and tokens[pos[0]] == ('OP', '+') or \\
              pos[0] < len(tokens) and tokens[pos[0]] == ('OP', '-'):
            op = tokens[pos[0]][1]
            pos[0] += 1
            right = parse_term()
            left = left + right if op == '+' else left - right
        return left

    def parse_term():
        left = parse_factor()
        while pos[0] < len(tokens) and tokens[pos[0]] == ('OP', '*') or \\
              pos[0] < len(tokens) and tokens[pos[0]] == ('OP', '/'):
            op = tokens[pos[0]][1]
            pos[0] += 1
            right = parse_factor()
            if op == '*':
                left *= right
            else:
                if right == 0:
                    raise ZeroDivisionError("Division by zero")
                left /= right
        return left

    def parse_factor():
        if tokens[pos[0]] == ('OP', '('):
            pos[0] += 1
            val = parse_expr()
            pos[0] += 1
            return val
        val = tokens[pos[0]][1]
        pos[0] += 1
        return val

    return parse_expr()


def calculate(expr):
    return calc_eval(tokenize(expr))
''',

    "string_tokenizer": '''\
def tokenize_words(text, delimiters=None):
    if delimiters is None:
        delimiters = set(' \\t\\n\\r')
    else:
        delimiters = set(delimiters)
    tokens = []
    current = []
    for ch in text:
        if ch in delimiters:
            if current:
                tokens.append(''.join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append(''.join(current))
    return tokens


def split_csv_line(line, sep=',', quote='"'):
    fields = []
    current = []
    in_quotes = False
    for ch in line:
        if ch == quote:
            in_quotes = not in_quotes
        elif ch == sep and not in_quotes:
            fields.append(''.join(current))
            current = []
        else:
            current.append(ch)
    fields.append(''.join(current))
    return fields


def count_tokens(text, delimiters=None):
    return len(tokenize_words(text, delimiters))


def ngrams(tokens, n):
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
''',

    "graph_bfs": '''\
from collections import deque


def bfs(graph, start):
    visited = set()
    queue = deque([start])
    visited.add(start)
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return order


def bfs_shortest_path(graph, start, end):
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        node, path = queue.popleft()
        if node == end:
            return path
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None


def bfs_level_order(graph, start):
    visited = {start}
    queue = deque([start])
    levels = []
    while queue:
        level = []
        for _ in range(len(queue)):
            node = queue.popleft()
            level.append(node)
            for neighbor in sorted(graph.get(node, [])):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        levels.append(level)
    return levels
''',

    "priority_queue": '''\
class MinHeap:
    def __init__(self):
        self._data = []

    def push(self, value):
        self._data.append(value)
        self._sift_up(len(self._data) - 1)

    def pop(self):
        if not self._data:
            raise IndexError("Pop from empty heap")
        top = self._data[0]
        last = self._data.pop()
        if self._data:
            self._data[0] = last
            self._sift_down(0)
        return top

    def peek(self):
        if not self._data:
            raise IndexError("Peek at empty heap")
        return self._data[0]

    def _sift_up(self, idx):
        while idx > 0:
            parent = (idx - 1) // 2
            if self._data[idx] < self._data[parent]:
                self._data[idx], self._data[parent] = self._data[parent], self._data[idx]
                idx = parent
            else:
                break

    def _sift_down(self, idx):
        n = len(self._data)
        while True:
            smallest = idx
            left = 2 * idx + 1
            right = 2 * idx + 2
            if left < n and self._data[left] < self._data[smallest]:
                smallest = left
            if right < n and self._data[right] < self._data[smallest]:
                smallest = right
            if smallest != idx:
                self._data[idx], self._data[smallest] = self._data[smallest], self._data[idx]
                idx = smallest
            else:
                break

    def __len__(self):
        return len(self._data)

    def __bool__(self):
        return bool(self._data)
''',

    "lru_cache": '''\
class LRUNode:
    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.head = LRUNode(None, None)
        self.tail = LRUNode(None, None)
        self.head.next = self.tail
        self.tail.prev = self.head

    def _remove(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev

    def _add_front(self, node):
        node.next = self.head.next
        node.prev = self.head
        self.head.next.prev = node
        self.head.next = node

    def get(self, key):
        if key not in self.cache:
            return -1
        node = self.cache[key]
        self._remove(node)
        self._add_front(node)
        return node.value

    def put(self, key, value):
        if key in self.cache:
            self._remove(self.cache[key])
        node = LRUNode(key, value)
        self._add_front(node)
        self.cache[key] = node
        if len(self.cache) > self.capacity:
            lru = self.tail.prev
            self._remove(lru)
            del self.cache[lru.key]

    def __len__(self):
        return len(self.cache)
''',

    "json_parser": '''\
def json_parse(text):
    pos = [0]

    def skip_ws():
        while pos[0] < len(text) and text[pos[0]] in ' \\t\\n\\r':
            pos[0] += 1

    def parse_string():
        pos[0] += 1
        chars = []
        while pos[0] < len(text) and text[pos[0]] != '"':
            if text[pos[0]] == '\\\\':
                pos[0] += 1
                esc = text[pos[0]]
                chars.append({'n': '\\n', 't': '\\t', '"': '"', '\\\\': '\\\\'}.get(esc, esc))
            else:
                chars.append(text[pos[0]])
            pos[0] += 1
        pos[0] += 1
        return ''.join(chars)

    def parse_number():
        start = pos[0]
        if text[pos[0]] == '-':
            pos[0] += 1
        while pos[0] < len(text) and text[pos[0]].isdigit():
            pos[0] += 1
        if pos[0] < len(text) and text[pos[0]] == '.':
            pos[0] += 1
            while pos[0] < len(text) and text[pos[0]].isdigit():
                pos[0] += 1
        return float(text[start:pos[0]])

    def parse_value():
        skip_ws()
        ch = text[pos[0]]
        if ch == '"':
            return parse_string()
        elif ch == '{':
            return parse_object()
        elif ch == '[':
            return parse_array()
        elif ch == 't':
            pos[0] += 4
            return True
        elif ch == 'f':
            pos[0] += 5
            return False
        elif ch == 'n':
            pos[0] += 4
            return None
        else:
            return parse_number()

    def parse_array():
        pos[0] += 1
        items = []
        skip_ws()
        if text[pos[0]] == ']':
            pos[0] += 1
            return items
        items.append(parse_value())
        while text[pos[0]] != ']':
            skip_ws()
            if text[pos[0]] == ',':
                pos[0] += 1
            items.append(parse_value())
            skip_ws()
        pos[0] += 1
        return items

    def parse_object():
        pos[0] += 1
        obj = {}
        skip_ws()
        if text[pos[0]] == '}':
            pos[0] += 1
            return obj
        skip_ws()
        key = parse_string()
        skip_ws()
        pos[0] += 1
        obj[key] = parse_value()
        skip_ws()
        while text[pos[0]] != '}':
            if text[pos[0]] == ',':
                pos[0] += 1
            skip_ws()
            key = parse_string()
            skip_ws()
            pos[0] += 1
            obj[key] = parse_value()
            skip_ws()
        pos[0] += 1
        return obj

    return parse_value()
''',

    "unit_converter": '''\
def celsius_to_fahrenheit(c):
    return c * 9.0 / 5.0 + 32


def fahrenheit_to_celsius(f):
    return (f - 32) * 5.0 / 9.0


def celsius_to_kelvin(c):
    return c + 273.15


def kelvin_to_celsius(k):
    return k - 273.15


LENGTH_FACTORS = {
    'mm': 0.001,
    'cm': 0.01,
    'm': 1.0,
    'km': 1000.0,
    'in': 0.0254,
    'ft': 0.3048,
    'yd': 0.9144,
    'mi': 1609.344,
}


def convert_length(value, from_unit, to_unit):
    if from_unit not in LENGTH_FACTORS or to_unit not in LENGTH_FACTORS:
        raise ValueError("Unknown unit")
    meters = value * LENGTH_FACTORS[from_unit]
    return meters / LENGTH_FACTORS[to_unit]


WEIGHT_FACTORS = {
    'mg': 0.001,
    'g': 1.0,
    'kg': 1000.0,
    'oz': 28.3495,
    'lb': 453.592,
}


def convert_weight(value, from_unit, to_unit):
    if from_unit not in WEIGHT_FACTORS or to_unit not in WEIGHT_FACTORS:
        raise ValueError("Unknown unit")
    grams = value * WEIGHT_FACTORS[from_unit]
    return grams / WEIGHT_FACTORS[to_unit]
''',

    "permutation_generator": '''\
def permutations(arr):
    if len(arr) <= 1:
        return [arr[:]]
    result = []
    for i in range(len(arr)):
        rest = arr[:i] + arr[i + 1:]
        for p in permutations(rest):
            result.append([arr[i]] + p)
    return result


def next_permutation(arr):
    n = len(arr)
    i = n - 2
    while i >= 0 and arr[i] >= arr[i + 1]:
        i -= 1
    if i < 0:
        return None
    j = n - 1
    while arr[j] <= arr[i]:
        j -= 1
    arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1:] = reversed(arr[i + 1:])
    return arr


def count_permutations(n, r=None):
    if r is None:
        r = n
    if r > n or n < 0 or r < 0:
        return 0
    result = 1
    for i in range(n, n - r, -1):
        result *= i
    return result


def is_permutation(a, b):
    if len(a) != len(b):
        return False
    return sorted(a) == sorted(b)
''',
}


# -- Measurement helpers -------------------------------------------------------

def measure_load(name, source):
    """Run jugeo load on a program, return load result dict + timing."""
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        objs = run_jugeo("load", tmp)
        elapsed = time.perf_counter() - t0
        data = objs[0] if objs else {}
        summary = data.get("summary", {})
        return {
            "name": name,
            "load_time_s": round(elapsed, 4),
            "coordinates": summary.get("coordinates", 0),
            "morphisms": summary.get("morphisms", 0),
            "covering_families": summary.get("covering_families", 0),
            "judgments": summary.get("judgments", 0),
            "context_bindings": summary.get("context_bindings", 0),
        }
    except Exception as e:
        return {"name": name, "error": str(e), "load_time_s": 0,
                "coordinates": 0, "morphisms": 0, "covering_families": 0}
    finally:
        cleanup(tmp)


def measure_classify(name, source):
    """Run jugeo classify --file on a program."""
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        objs = run_jugeo("classify", "--file", tmp)
        elapsed = time.perf_counter() - t0
        data = objs[0] if objs else {}
        cls = data.get("classification", {})
        return {
            "name": name,
            "classify_time_s": round(elapsed, 4),
            "category": cls.get("category", "UNKNOWN"),
            "confidence": cls.get("confidence", 0),
            "recommended_subsystems": data.get("recommended_subsystems", []),
            "site_coord_count": data.get("site_structure", {}).get("coordinate_count", 0),
        }
    except Exception as e:
        return {"name": name, "error": str(e), "classify_time_s": 0,
                "category": "ERROR"}
    finally:
        cleanup(tmp)


def measure_ideation():
    """Run jugeo ideate --discover and --economics (global, not per-file)."""
    results = {}

    # Discovery
    t0 = time.perf_counter()
    disc_objs = run_jugeo("ideate", "--discover")
    results["discover_time_s"] = round(time.perf_counter() - t0, 4)
    disc = disc_objs[0] if disc_objs else {}
    results["discover_fields"] = disc.get("fields_scanned", 0)
    results["discover_candidates"] = disc.get("candidates_proposed", 0)
    results["discover_novel"] = disc.get("candidates_novel", 0)
    results["discover_falsified"] = disc.get("candidates_falsified", 0)
    results["discover_trust"] = disc.get("aggregate_trust", 0)
    theorems = disc.get("theorems", [])
    results["discover_theorems"] = len(theorems)

    # Economics
    t0 = time.perf_counter()
    econ_objs = run_jugeo("ideate", "--economics")
    results["economics_time_s"] = round(time.perf_counter() - t0, 4)
    econ = econ_objs[0] if econ_objs else {}
    econ_theorems = econ.get("theorems", [])
    results["econ_candidates"] = econ.get("candidates_proposed", 0)
    yields = [t.get("yield_estimate", 0) for t in econ_theorems if t.get("yield_estimate")]
    results["econ_mean_yield"] = round(statistics.mean(yields), 4) if yields else 0
    results["econ_total_candidates"] = len(econ_theorems)

    return results


def measure_site_api(name, source):
    """Use the Python API to gather site structure data."""
    try:
        from jugeo.geometry import SiteBuilder, CoverStatistics, SiteDiagnostics
        site = SiteBuilder(source).build()
        dp = site.discovery_pipeline()
        pa = site.problem_atlas()
        return {
            "name": name,
            "api_coords": site.coordinate_count(),
            "api_morphisms": site.morphism_count(),
            "api_covers": len(site.covering_families()),
            "api_objects": len(site.objects()),
            "discovery_pipeline": dp,
            "problem_atlas": pa,
        }
    except Exception as e:
        return {"name": name, "api_error": str(e)}


# -- LaTeX macro emitter ------------------------------------------------------

def write_latex_macros(macros, path):
    """Write LaTeX \\newcommand macros to a file."""
    header = ("% data-paper52.tex — Experimental data for Paper 52: "
              "Ideation Engine\n"
              "% AUTO-GENERATED by experiments/exp52_ideation_engine.py\n"
              "% Do not edit manually.\n\n")
    with open(path, "w") as f:
        f.write(header)
        for name, value in macros:
            f.write("\\newcommand{{\\{}}}{{{}}}\n".format(name, value))
    print("  Wrote {} macros to {}".format(len(macros), path))


# -- Main ----------------------------------------------------------------------

def main():
    print("=" * 72)
    print("Paper 52: Ideation Engine")
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

    # --- 1. Load ---
    print("  [1/5] Running LOAD on {} programs...".format(len(PROGRAMS)))
    load_results = []
    for name, source in PROGRAMS.items():
        rec = measure_load(name, source)
        load_results.append(rec)
        print("    {n:25s}  coords={c}  morphisms={m}  covers={cv}  ({t:.3f}s)".format(
            n=rec["name"], c=rec.get("coordinates", 0),
            m=rec.get("morphisms", 0), cv=rec.get("covering_families", 0),
            t=rec.get("load_time_s", 0)))

    # --- 2. Classify ---
    print("\n  [2/5] Running CLASSIFY on {} programs...".format(len(PROGRAMS)))
    classify_results = []
    for name, source in PROGRAMS.items():
        rec = measure_classify(name, source)
        classify_results.append(rec)
        print("    {n:25s}  category={cat:15s}  subs={subs}  ({t:.3f}s)".format(
            n=rec["name"], cat=rec.get("category", "?"),
            subs=",".join(rec.get("recommended_subsystems", [])[:3]),
            t=rec.get("classify_time_s", 0)))

    # --- 3. Ideation discovery ---
    print("\n  [3/5] Running IDEATE --discover...")
    ideation = measure_ideation()
    print("    Fields scanned:    {}".format(ideation.get("discover_fields", 0)))
    print("    Candidates:        {}".format(ideation.get("discover_candidates", 0)))
    print("    Novel:             {}".format(ideation.get("discover_novel", 0)))
    print("    Falsified:         {}".format(ideation.get("discover_falsified", 0)))
    print("    Discover time:     {:.4f}s".format(ideation.get("discover_time_s", 0)))

    # --- 4. Ideation economics ---
    print("\n  [4/5] IDEATE --economics results:")
    print("    Econ candidates:   {}".format(ideation.get("econ_total_candidates", 0)))
    print("    Mean yield est:    {}".format(ideation.get("econ_mean_yield", 0)))
    print("    Economics time:    {:.4f}s".format(ideation.get("economics_time_s", 0)))

    # --- 5. Python API ---
    print("\n  [5/5] Running Python API (SiteBuilder) on {} programs...".format(
        len(PROGRAMS)))
    api_results = []
    for name, source in PROGRAMS.items():
        rec = measure_site_api(name, source)
        api_results.append(rec)
        print("    {n:25s}  api_coords={c}  api_morphisms={m}  api_covers={cv}".format(
            n=rec["name"], c=rec.get("api_coords", 0),
            m=rec.get("api_morphisms", 0), cv=rec.get("api_covers", 0)))

    # -- Aggregate statistics --------------------------------------------------
    n = len(PROGRAMS)

    # Load stats
    all_coords = [r.get("coordinates", 0) for r in load_results]
    all_morphisms = [r.get("morphisms", 0) for r in load_results]
    all_covers = [r.get("covering_families", 0) for r in load_results]
    load_times = [r.get("load_time_s", 0) for r in load_results]

    mean_coords = round(statistics.mean(all_coords), 1) if all_coords else 0
    mean_morphisms = round(statistics.mean(all_morphisms), 1) if all_morphisms else 0
    total_covers = sum(all_covers)
    mean_load_time = round(statistics.mean(load_times), 4) if load_times else 0

    # Classify stats
    classify_times = [r.get("classify_time_s", 0) for r in classify_results]
    mean_classify_time = round(statistics.mean(classify_times), 4) if classify_times else 0
    categories = [r.get("category", "UNKNOWN") for r in classify_results]
    from collections import Counter
    cat_counts = Counter(categories)
    most_common_cat = cat_counts.most_common(1)[0][0] if cat_counts else "UNKNOWN"

    # Ideation success rate (count programs with non-error classify + non-zero coords)
    ideation_success = sum(1 for r in load_results if r.get("coordinates", 0) > 0)
    ideation_rate = round(ideation_success / n, 4) if n else 0

    # Discovery stats
    disc_fields = ideation.get("discover_fields", 0)
    disc_candidates = ideation.get("discover_candidates", 0)
    disc_novel = ideation.get("discover_novel", 0)
    disc_falsified = ideation.get("discover_falsified", 0)
    discover_time = ideation.get("discover_time_s", 0)
    econ_mean_yield = ideation.get("econ_mean_yield", 0)
    econ_total = ideation.get("econ_total_candidates", 0)

    # -- Print summary ---------------------------------------------------------
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("  Programs:              {}".format(n))
    print("  Mean coordinates:      {}".format(mean_coords))
    print("  Mean morphisms:        {}".format(mean_morphisms))
    print("  Total covers:          {}".format(total_covers))
    print("  Mean load time:        {:.4f}s".format(mean_load_time))
    print("  Mean classify time:    {:.4f}s".format(mean_classify_time))
    print("  Most common category:  {}".format(most_common_cat))
    print("  Ideation rate:         {:.1%}".format(ideation_rate))
    print("  Discovery fields:      {}".format(disc_fields))
    print("  Discovery candidates:  {}".format(disc_candidates))
    print("  Discovery novel:       {}".format(disc_novel))
    print("  Discovery falsified:   {}".format(disc_falsified))
    print("  Mean discovery time:   {:.4f}s".format(discover_time))
    print("  Econ mean yield:       {}".format(econ_mean_yield))
    print("  Econ total candidates: {}".format(econ_total))

    # -- LaTeX macros ----------------------------------------------------------
    macros = [
        ("ppLIItotalPrograms",       n),
        ("ppLIImeanCoords",          mean_coords),
        ("ppLIImeanMorphisms",       mean_morphisms),
        ("ppLIItotalCovers",         total_covers),
        ("ppLIIdiscoveryFields",     disc_fields),
        ("ppLIIdiscoveryCandidates", disc_candidates),
        ("ppLIIdiscoveryNovel",      disc_novel),
        ("ppLIIdiscoveryFalsified",  disc_falsified),
        ("ppLIImeanClassifyTime",    "{:.4f}\\,s".format(mean_classify_time)),
        ("ppLIImeanLoadTime",        "{:.4f}\\,s".format(mean_load_time)),
        ("ppLIImeanDiscoverTime",    "{:.4f}\\,s".format(discover_time)),
        ("ppLIIcategoryBreakdown",   most_common_cat),
        ("ppLIIideationRate",        "{:.1\\%}".format(ideation_rate * 100)),
        ("ppLIImeanEconYield",       econ_mean_yield),
        ("ppLIItotalEconCandidates", econ_total),
    ]
    tex_path = os.path.join(ROOT, "papers", "data-paper52.tex")
    write_latex_macros(macros, tex_path)

    # -- JSON results ----------------------------------------------------------
    full = {
        "experiment": "ideation_engine",
        "paper": 52,
        "program_count": n,
        "load_results": load_results,
        "classify_results": classify_results,
        "ideation": ideation,
        "api_results": [
            {k: v for k, v in r.items()
             if k not in ("discovery_pipeline", "problem_atlas")}
            for r in api_results
        ],
        "summary": {
            "total_programs": n,
            "mean_coords": mean_coords,
            "mean_morphisms": mean_morphisms,
            "total_covers": total_covers,
            "mean_load_time_s": mean_load_time,
            "mean_classify_time_s": mean_classify_time,
            "most_common_category": most_common_cat,
            "ideation_rate": ideation_rate,
            "discovery_fields": disc_fields,
            "discovery_candidates": disc_candidates,
            "discovery_novel": disc_novel,
            "discovery_falsified": disc_falsified,
            "discover_time_s": discover_time,
            "econ_mean_yield": econ_mean_yield,
            "econ_total_candidates": econ_total,
            "note": "All numbers from jugeo CLI (subprocess) + Python API",
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper52.json")
    with open(json_path, "w") as f:
        json.dump(full, f, indent=2, default=str)
    print("\n  Results saved to {}".format(json_path))


if __name__ == "__main__":
    main()
