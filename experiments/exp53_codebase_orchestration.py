#!/usr/bin/env python3
"""
Experiment 53 — Codebase Orchestration: Scaling JuGeo to Large Codebases
=========================================================================

Studies how JuGeo scales with program size by running load, evaluate, and
encode on programs ranging from tiny (5 lines) to large (80+ lines).

Writes macros to papers/data-paper53.tex with prefix ppLIII.
Re-run: python3 experiments/exp53_codebase_orchestration.py
"""

import ast, json, os, statistics, subprocess, sys, tempfile, time

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


# ── CLI helper ────────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
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
    """Write source to a temporary .py file and return its path."""
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


# ── Test programs (10 programs, tiny → large) ────────────────────────────────

PROGRAMS = {
    "increment": '''\
def increment(x):
    """Return x + 1."""
    return x + 1

result = increment(5)
''',

    "absolute_value": '''\
def absolute_value(x):
    """Return absolute value of x."""
    if x < 0:
        return -x
    return x

def sign(x):
    if x > 0:
        return 1
    elif x < 0:
        return -1
    return 0
''',

    "factorial": '''\
def factorial(n):
    """Compute n! iteratively."""
    if n < 0:
        raise ValueError("Negative input")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

def factorial_recursive(n):
    if n <= 1:
        return 1
    return n * factorial_recursive(n - 1)

assert factorial(5) == 120
assert factorial_recursive(5) == 120
''',

    "binary_search": '''\
def binary_search(arr, target):
    """Return index of target in sorted arr, or -1."""
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

def binary_search_recursive(arr, target, lo=0, hi=None):
    if hi is None:
        hi = len(arr) - 1
    if lo > hi:
        return -1
    mid = (lo + hi) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, hi)
    else:
        return binary_search_recursive(arr, target, lo, mid - 1)
''',

    "linked_list": '''\
class Node:
    def __init__(self, val, next_node=None):
        self.val = val
        self.next = next_node

class LinkedList:
    def __init__(self):
        self.head = None
        self.size = 0

    def prepend(self, val):
        self.head = Node(val, self.head)
        self.size += 1

    def append(self, val):
        if not self.head:
            self.head = Node(val)
        else:
            cur = self.head
            while cur.next:
                cur = cur.next
            cur.next = Node(val)
        self.size += 1

    def find(self, val):
        cur = self.head
        while cur:
            if cur.val == val:
                return True
            cur = cur.next
        return False

    def remove(self, val):
        if not self.head:
            return False
        if self.head.val == val:
            self.head = self.head.next
            self.size -= 1
            return True
        cur = self.head
        while cur.next:
            if cur.next.val == val:
                cur.next = cur.next.next
                self.size -= 1
                return True
            cur = cur.next
        return False
''',

    "hash_table": '''\
class HashTable:
    def __init__(self, capacity=16):
        self.capacity = capacity
        self.size = 0
        self.buckets = [[] for _ in range(capacity)]

    def _hash(self, key):
        return hash(key) % self.capacity

    def put(self, key, value):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.buckets[idx]):
            if k == key:
                self.buckets[idx][i] = (key, value)
                return
        self.buckets[idx].append((key, value))
        self.size += 1
        if self.size > self.capacity * 0.75:
            self._resize()

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self.buckets[idx]:
            if k == key:
                return v
        return default

    def delete(self, key):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.buckets[idx]):
            if k == key:
                self.buckets[idx].pop(i)
                self.size -= 1
                return True
        return False

    def _resize(self):
        old = self.buckets
        self.capacity *= 2
        self.buckets = [[] for _ in range(self.capacity)]
        self.size = 0
        for bucket in old:
            for k, v in bucket:
                self.put(k, v)

    def keys(self):
        result = []
        for bucket in self.buckets:
            for k, v in bucket:
                result.append(k)
        return result
''',

    "expression_evaluator": '''\
class Token:
    def __init__(self, kind, value):
        self.kind = kind
        self.value = value

def tokenize(expr):
    tokens = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit():
            j = i
            while j < len(expr) and expr[j].isdigit():
                j += 1
            tokens.append(Token("NUM", int(expr[i:j])))
            i = j
        elif ch in "+-*/()":
            tokens.append(Token("OP", ch))
            i += 1
        else:
            raise ValueError(f"Unexpected char: {ch}")
    return tokens

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, kind=None):
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end")
        if kind and tok.kind != kind:
            raise ValueError(f"Expected {kind}")
        self.pos += 1
        return tok

    def parse_expr(self):
        result = self.parse_term()
        while self.peek() and self.peek().value in "+-":
            op = self.consume().value
            right = self.parse_term()
            result = (op, result, right)
        return result

    def parse_term(self):
        result = self.parse_factor()
        while self.peek() and self.peek().value in "*/":
            op = self.consume().value
            right = self.parse_factor()
            result = (op, result, right)
        return result

    def parse_factor(self):
        tok = self.peek()
        if tok.kind == "NUM":
            self.consume()
            return tok.value
        elif tok.value == "(":
            self.consume()
            result = self.parse_expr()
            self.consume()
            return result
        raise ValueError("Unexpected token")
''',

    "sorting_algorithms": '''\
def insertion_sort(arr):
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and arr[j] > key:
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return arr

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

def quick_sort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quick_sort(left) + middle + quick_sort(right)

def selection_sort(arr):
    n = len(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
    return arr

def heap_sort(arr):
    def heapify(a, n, i):
        largest = i
        l = 2 * i + 1
        r = 2 * i + 2
        if l < n and a[l] > a[largest]:
            largest = l
        if r < n and a[r] > a[largest]:
            largest = r
        if largest != i:
            a[i], a[largest] = a[largest], a[i]
            heapify(a, n, largest)
    n = len(arr)
    for i in range(n // 2 - 1, -1, -1):
        heapify(arr, n, i)
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        heapify(arr, i, 0)
    return arr
''',

    "graph_algorithms": '''\
from collections import deque, defaultdict

def bfs(graph, start):
    visited = set()
    queue = deque([start])
    order = []
    visited.add(start)
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return order

def dfs(graph, start):
    visited = set()
    order = []
    def _dfs(node):
        visited.add(node)
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                _dfs(neighbor)
    _dfs(start)
    return order

def shortest_path(graph, start, end):
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

def has_cycle(graph):
    visited = set()
    rec_stack = set()
    def _cycle(node):
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if _cycle(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.discard(node)
        return False
    for node in graph:
        if node not in visited:
            if _cycle(node):
                return True
    return False

def connected_components(graph):
    visited = set()
    components = []
    def _explore(node, component):
        visited.add(node)
        component.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                _explore(neighbor, component)
    for node in graph:
        if node not in visited:
            comp = []
            _explore(node, comp)
            components.append(comp)
    return components
''',

    "full_calculator": '''\
class CalcError(Exception):
    pass

class Lexer:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.tokens = []
        self._scan()

    def _scan(self):
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch.isspace():
                self.pos += 1
            elif ch.isdigit() or ch == '.':
                self._number()
            elif ch.isalpha():
                self._identifier()
            elif ch in '+-*/()^%=,':
                self.tokens.append(('OP', ch))
                self.pos += 1
            else:
                raise CalcError(f"Unknown character: {ch}")

    def _number(self):
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] == '.'):
            self.pos += 1
        self.tokens.append(('NUM', float(self.text[start:self.pos])))

    def _identifier(self):
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isalnum():
            self.pos += 1
        self.tokens.append(('ID', self.text[start:self.pos]))

class Calculator:
    BUILTINS = {'abs': abs, 'max': max, 'min': min, 'round': round}

    def __init__(self):
        self.variables = {}

    def evaluate(self, text):
        lexer = Lexer(text)
        tokens = lexer.tokens
        if not tokens:
            return 0
        if len(tokens) >= 3 and tokens[0][0] == 'ID' and tokens[1] == ('OP', '='):
            name = tokens[0][1]
            value = self._eval_expr(tokens[2:])
            self.variables[name] = value
            return value
        return self._eval_expr(tokens)

    def _eval_expr(self, tokens):
        result, rest = self._term(tokens)
        while rest and rest[0][0] == 'OP' and rest[0][1] in '+-':
            op = rest[0][1]
            right, rest = self._term(rest[1:])
            result = result + right if op == '+' else result - right
        return result

    def _term(self, tokens):
        result, rest = self._power(tokens)
        while rest and rest[0][0] == 'OP' and rest[0][1] in '*/%':
            op = rest[0][1]
            right, rest = self._power(rest[1:])
            if op == '*':
                result *= right
            elif op == '/':
                if right == 0:
                    raise CalcError("Division by zero")
                result /= right
            else:
                result %= right
        return result, rest

    def _power(self, tokens):
        base, rest = self._factor(tokens)
        if rest and rest[0] == ('OP', '^'):
            exp, rest = self._power(rest[1:])
            base = base ** exp
        return base, rest

    def _factor(self, tokens):
        if not tokens:
            raise CalcError("Unexpected end of expression")
        tok = tokens[0]
        if tok[0] == 'NUM':
            return tok[1], tokens[1:]
        if tok[0] == 'ID':
            name = tok[1]
            rest = tokens[1:]
            if rest and rest[0] == ('OP', '('):
                args, rest = self._parse_args(rest[1:])
                if name in self.BUILTINS:
                    return self.BUILTINS[name](*args), rest
                raise CalcError(f"Unknown function: {name}")
            if name in self.variables:
                return self.variables[name], rest
            raise CalcError(f"Undefined variable: {name}")
        if tok == ('OP', '('):
            result = self._eval_expr(tokens[1:])
            return result, []
        if tok == ('OP', '-'):
            val, rest = self._factor(tokens[1:])
            return -val, rest
        raise CalcError(f"Unexpected token: {tok}")

    def _parse_args(self, tokens):
        args = []
        if tokens and tokens[0] == ('OP', ')'):
            return args, tokens[1:]
        args.append(self._eval_expr(tokens))
        return args, []
''',
}


# ── Size classification helpers ───────────────────────────────────────────────

def classify_size(n_lines):
    if n_lines <= 20:
        return "small"
    elif n_lines <= 50:
        return "medium"
    else:
        return "large"


def safe_mean(values):
    return statistics.mean(values) if values else 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("Experiment 53 — Codebase Orchestration: Scaling JuGeo")
    print("=" * 70)

    tmpfiles = []
    results = []

    for name, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        # Count program complexity via AST
        tree = ast.parse(source)
        n_lines = len([l for l in source.splitlines() if l.strip()])
        n_funcs = sum(1 for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        n_classes = sum(1 for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef))

        # ── 1. jugeo load ────────────────────────────────────────────────
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", path)
        load_wall = time.perf_counter() - t0
        load_data = load_objs[0] if load_objs else {}
        load_summary = load_data.get("summary", load_data)

        # ── 2. jugeo evaluate ────────────────────────────────────────────
        t1 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", path)
        eval_wall = time.perf_counter() - t1
        eval_data = eval_objs[0] if eval_objs else {}

        # ── 3. jugeo encode ──────────────────────────────────────────────
        t2 = time.perf_counter()
        encode_objs = run_jugeo("encode", path)
        encode_wall = time.perf_counter() - t2
        encode_data = encode_objs[0] if encode_objs else {}

        # Extract metrics
        coords = load_summary.get("coordinates", 0)
        morphisms = load_summary.get("morphisms", 0)

        eval_verdict = eval_data.get("verdict", "unknown")
        eval_trust = eval_data.get("trust", "unknown")
        eval_descent = eval_data.get("descent_ok", False)
        eval_cover_quality = eval_data.get("cover_quality", 0.0)
        eval_descent_time = eval_data.get("descent_time_s",
                                          eval_data.get("descent_wall_s", 0.0))

        encode_coords = encode_data.get("coordinates", 0)
        encode_morphisms = encode_data.get("morphisms", 0)

        row = {
            "program": name,
            "lines": n_lines,
            "functions": n_funcs,
            "classes": n_classes,
            "size_class": classify_size(n_lines),
            "load_coords": coords,
            "load_morphisms": morphisms,
            "load_wall_s": round(load_wall, 4),
            "eval_verdict": eval_verdict,
            "eval_trust": eval_trust,
            "eval_descent_ok": eval_descent,
            "eval_cover_quality": eval_cover_quality,
            "eval_descent_time_s": round(eval_descent_time, 4) if isinstance(eval_descent_time, (int, float)) else 0.0,
            "eval_wall_s": round(eval_wall, 4),
            "encode_coords": encode_coords,
            "encode_morphisms": encode_morphisms,
            "encode_wall_s": round(encode_wall, 4),
            "total_wall_s": round(load_wall + eval_wall + encode_wall, 4),
        }
        results.append(row)

        print("  {:<24} lines={:>3}  coords={:>3}  morphs={:>3}  "
              "verdict={:<12}  total={:.2f}s".format(
                  name, n_lines, coords, encode_morphisms,
                  eval_verdict, row["total_wall_s"]))

    # ── Aggregate statistics ──────────────────────────────────────────────────

    n_total = len(results)
    total_functions = sum(r["functions"] for r in results)
    total_classes = sum(r["classes"] for r in results)

    verified = sum(1 for r in results if r["eval_verdict"] == "verified")
    accuracy = (verified / max(n_total, 1)) * 100

    load_times = [r["load_wall_s"] for r in results]
    eval_times = [r["eval_wall_s"] for r in results]
    encode_times = [r["encode_wall_s"] for r in results]
    descent_times = [r["eval_descent_time_s"] for r in results if r["eval_descent_time_s"] > 0]
    cover_quals = [r["eval_cover_quality"] for r in results
                   if isinstance(r["eval_cover_quality"], (int, float)) and r["eval_cover_quality"] > 0]

    coord_vals = [r["load_coords"] for r in results]
    morph_vals = [r["encode_morphisms"] for r in results]

    # Size-bucketed timing
    small_times = [r["total_wall_s"] for r in results if r["size_class"] == "small"]
    medium_times = [r["total_wall_s"] for r in results if r["size_class"] == "medium"]
    large_times = [r["total_wall_s"] for r in results if r["size_class"] == "large"]

    mean_small = safe_mean(small_times)
    mean_medium = safe_mean(medium_times)
    mean_large = safe_mean(large_times)
    scaling_ratio = round(mean_large / mean_small, 2) if mean_small > 0 else 0.0

    # ── Print summary ─────────────────────────────────────────────────────────

    print()
    print("-" * 70)
    print(f"  Total programs:        {n_total}")
    print(f"  Total functions:       {total_functions}")
    print(f"  Total classes:         {total_classes}")
    print(f"  Verified:              {verified}/{n_total} ({accuracy:.1f}%)")
    print(f"  Mean load time:        {safe_mean(load_times):.4f}s")
    print(f"  Mean eval time:        {safe_mean(eval_times):.4f}s")
    print(f"  Mean encode time:      {safe_mean(encode_times):.4f}s")
    print(f"  Mean coords:           {safe_mean(coord_vals):.1f}")
    print(f"  Mean morphisms:        {safe_mean(morph_vals):.1f}")
    print(f"  Small mean time:       {mean_small:.4f}s")
    print(f"  Medium mean time:      {mean_medium:.4f}s")
    print(f"  Large mean time:       {mean_large:.4f}s")
    print(f"  Scaling ratio (L/S):   {scaling_ratio}")
    print("-" * 70)

    # ── Write LaTeX macros ────────────────────────────────────────────────────

    tex_path = os.path.join(REPO_ROOT, "papers", "data-paper53.tex")
    os.makedirs(os.path.dirname(tex_path), exist_ok=True)

    with open(tex_path, "w") as fh:
        fh.write("% data-paper53.tex — AUTO-GENERATED by exp53_codebase_orchestration.py\n")
        fh.write("% DO NOT EDIT — regenerate with: python3 experiments/exp53_codebase_orchestration.py\n\n")

        fh.write("% ── Overall statistics ────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIIItotalPrograms", n_total)
        write_macro(fh, "ppLIIItotalFunctions", total_functions)
        write_macro(fh, "ppLIIItotalClasses", total_classes)
        write_macro(fh, "ppLIIIoverallAccuracy", "{:.1f}\\%".format(accuracy))

        fh.write("\n% ── Timing statistics ─────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIIImeanBuildTime", "{:.3f}\\,s".format(safe_mean(load_times)))
        write_macro(fh, "ppLIIImeanEvalTime", "{:.3f}\\,s".format(safe_mean(eval_times)))
        write_macro(fh, "ppLIIImeanEncodeTime", "{:.3f}\\,s".format(safe_mean(encode_times)))
        write_macro(fh, "ppLIIImeanDescentTime",
                    "{:.3f}\\,s".format(safe_mean(descent_times)) if descent_times else "N/A")

        fh.write("\n% ── Structural metrics ────────────────────────────────────────────────\n")
        write_macro(fh, "ppLIIImeanCoords", "{:.1f}".format(safe_mean(coord_vals)))
        write_macro(fh, "ppLIIImeanMorphisms", "{:.1f}".format(safe_mean(morph_vals)))
        write_macro(fh, "ppLIIIcoverQualityMean",
                    "{:.3f}".format(safe_mean(cover_quals)) if cover_quals else "N/A")

        fh.write("\n% ── Scaling by program size ───────────────────────────────────────────\n")
        write_macro(fh, "ppLIIIsmallMeanTime", "{:.3f}\\,s".format(mean_small))
        write_macro(fh, "ppLIIImediumMeanTime", "{:.3f}\\,s".format(mean_medium))
        write_macro(fh, "ppLIIIlargeMeanTime", "{:.3f}\\,s".format(mean_large))
        write_macro(fh, "ppLIIIscalingRatio", "{:.2f}$\\times$".format(scaling_ratio))

    print(f"\n  Macros written to: {tex_path}")

    # ── Save JSON results ─────────────────────────────────────────────────────

    json_path = os.path.join(os.path.dirname(__file__), "results_paper53.json")
    with open(json_path, "w") as f:
        json.dump({
            "experiment": "codebase_orchestration",
            "paper": 53,
            "note": "All numbers from subprocess CLI calls to jugeo load/evaluate/encode.",
            "n_programs": n_total,
            "results": results,
            "summary": {
                "total_functions": total_functions,
                "total_classes": total_classes,
                "verified": verified,
                "accuracy_pct": round(accuracy, 1),
                "mean_load_s": round(safe_mean(load_times), 4),
                "mean_eval_s": round(safe_mean(eval_times), 4),
                "mean_encode_s": round(safe_mean(encode_times), 4),
                "mean_descent_s": round(safe_mean(descent_times), 4) if descent_times else None,
                "mean_coords": round(safe_mean(coord_vals), 1),
                "mean_morphisms": round(safe_mean(morph_vals), 1),
                "cover_quality_mean": round(safe_mean(cover_quals), 3) if cover_quals else None,
                "small_mean_s": round(mean_small, 4),
                "medium_mean_s": round(mean_medium, 4),
                "large_mean_s": round(mean_large, 4),
                "scaling_ratio": scaling_ratio,
            },
        }, f, indent=2, default=str)

    print(f"  Results saved to:  {json_path}")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass

    print("\nDone.")


if __name__ == "__main__":
    main()
