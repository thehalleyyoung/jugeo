#!/usr/bin/env python3
"""Paper 53 Experiment — Codebase Orchestration: Scaling to Large Codebases.

Hypothesis: JuGeo scales sub-linearly with program size for site construction,
evaluation, and encoding.

Re-run: python3 experiments/exp53_codebase_orchestration.py
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
    "tiny_incr": '''\
def increment(x):
    return x + 1

def decrement(x):
    return x - 1
''',
    "tiny_abs": '''\
def absolute(x):
    if x < 0:
        return -x
    return x

def sign(x):
    if x > 0: return 1
    if x < 0: return -1
    return 0
''',
    "small_factorial": '''\
def factorial(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

def double_factorial(n):
    result = 1
    i = n
    while i > 0:
        result *= i
        i -= 2
    return result

def falling_factorial(n, k):
    result = 1
    for i in range(k):
        result *= (n - i)
    return result
''',
    "small_bsearch": '''\
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

def lower_bound(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo

def upper_bound(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo
''',
    "medium_linked_list": '''\
class Node:
    def __init__(self, val):
        self.val = val
        self.next = None

class LinkedList:
    def __init__(self):
        self.head = None
        self.size = 0

    def append(self, val):
        node = Node(val)
        if not self.head:
            self.head = node
        else:
            cur = self.head
            while cur.next:
                cur = cur.next
            cur.next = node
        self.size += 1

    def prepend(self, val):
        node = Node(val)
        node.next = self.head
        self.head = node
        self.size += 1

    def delete(self, val):
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

    def find(self, val):
        cur = self.head
        idx = 0
        while cur:
            if cur.val == val:
                return idx
            cur = cur.next
            idx += 1
        return -1

    def to_list(self):
        result = []
        cur = self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
''',
    "medium_hashtable": '''\
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
        return [k for bucket in self.buckets for k, v in bucket]

    def values(self):
        return [v for bucket in self.buckets for k, v in bucket]
''',
    "medium_expr_eval": '''\
def tokenize(expr):
    tokens, i = [], 0
    while i < len(expr):
        if expr[i].isdigit() or expr[i] == '.':
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            tokens.append(('NUM', float(expr[i:j])))
            i = j
        elif expr[i] in '+-*/()':
            tokens.append(('OP', expr[i]))
            i += 1
        elif expr[i].isspace():
            i += 1
        else:
            raise ValueError(f"Unknown char: {expr[i]}")
    return tokens

def evaluate(expr):
    tokens = tokenize(expr)
    pos = [0]
    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None
    def consume():
        t = tokens[pos[0]]; pos[0] += 1; return t
    def parse_expr():
        left = parse_term()
        while peek() and peek()[1] in ('+', '-'):
            op = consume()[1]
            right = parse_term()
            left = left + right if op == '+' else left - right
        return left
    def parse_term():
        left = parse_factor()
        while peek() and peek()[1] in ('*', '/'):
            op = consume()[1]
            right = parse_factor()
            left = left * right if op == '*' else left / right
        return left
    def parse_factor():
        t = peek()
        if t[0] == 'NUM':
            consume(); return t[1]
        if t[1] == '(':
            consume()
            val = parse_expr()
            consume()
            return val
        raise ValueError("Unexpected token")
    return parse_expr()

def safe_evaluate(expr):
    try:
        return evaluate(expr), None
    except Exception as e:
        return None, str(e)
''',
    "large_sorting": '''\
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
    return _merge(left, right)

def _merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i]); i += 1
        else:
            result.append(right[j]); j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result

def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    mid = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + mid + quicksort(right)

def heapsort(arr):
    def sift_down(a, start, end):
        root = start
        while 2 * root + 1 <= end:
            child = 2 * root + 1
            if child + 1 <= end and a[child] < a[child + 1]:
                child += 1
            if a[root] < a[child]:
                a[root], a[child] = a[child], a[root]
                root = child
            else:
                return
    n = len(arr)
    for start in range(n // 2 - 1, -1, -1):
        sift_down(arr, start, n - 1)
    for end in range(n - 1, 0, -1):
        arr[0], arr[end] = arr[end], arr[0]
        sift_down(arr, 0, end - 1)
    return arr
''',
    "large_graph": '''\
from collections import deque
import heapq

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
        for neighbor in sorted(graph.get(node, [])):
            if neighbor not in visited:
                queue.append(neighbor)
    return order

def dfs(graph, start):
    visited = set()
    order = []
    def _dfs(node):
        if node in visited:
            return
        visited.add(node)
        order.append(node)
        for neighbor in sorted(graph.get(node, [])):
            _dfs(neighbor)
    _dfs(start)
    return order

def dijkstra(graph, start):
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float('inf')):
            continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float('inf')):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist

def topological_sort(graph):
    in_degree = {}
    for node in graph:
        in_degree.setdefault(node, 0)
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
    queue = deque([n for n in in_degree if in_degree[n] == 0])
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return order

def connected_components(graph):
    visited = set()
    components = []
    for node in graph:
        if node not in visited:
            component = bfs(graph, node)
            visited.update(component)
            components.append(component)
    return components
''',
    "large_calculator": '''\
class Token:
    def __init__(self, kind, value):
        self.kind = kind
        self.value = value

def lex(text):
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text[i].isdigit() or text[i] == '.':
            j = i
            while j < len(text) and (text[j].isdigit() or text[j] == '.'):
                j += 1
            tokens.append(Token('NUM', float(text[i:j])))
            i = j
        elif text[i].isalpha():
            j = i
            while j < len(text) and text[j].isalnum():
                j += 1
            tokens.append(Token('ID', text[i:j]))
            i = j
        elif text[i] in '+-*/()^%=,':
            tokens.append(Token('OP', text[i]))
            i += 1
        else:
            raise ValueError(f"Unknown: {text[i]}")
    return tokens

class Calculator:
    def __init__(self):
        self.variables = {}
        self.functions = {
            'abs': abs, 'min': min, 'max': max,
            'round': round, 'int': int, 'float': float,
        }

    def evaluate(self, expr):
        tokens = lex(expr)
        if len(tokens) >= 3 and tokens[1].value == '=':
            name = tokens[0].value
            val = self._parse(tokens[2:])
            self.variables[name] = val
            return val
        return self._parse(tokens)

    def _parse(self, tokens):
        pos = [0]
        def peek():
            return tokens[pos[0]] if pos[0] < len(tokens) else None
        def consume():
            t = tokens[pos[0]]; pos[0] += 1; return t
        def expr():
            left = term()
            while peek() and peek().value in ('+', '-'):
                op = consume().value
                right = term()
                left = left + right if op == '+' else left - right
            return left
        def term():
            left = power()
            while peek() and peek().value in ('*', '/', '%'):
                op = consume().value
                right = power()
                if op == '*': left *= right
                elif op == '/': left /= right
                else: left %= right
            return left
        def power():
            base = factor()
            if peek() and peek().value == '^':
                consume()
                exp = power()
                return base ** exp
            return base
        def factor():
            t = peek()
            if t.kind == 'NUM':
                return consume().value
            if t.kind == 'ID':
                name = consume().value
                if peek() and peek().value == '(':
                    consume()
                    args = []
                    if peek() and peek().value != ')':
                        args.append(expr())
                        while peek() and peek().value == ',':
                            consume()
                            args.append(expr())
                    consume()
                    return self.functions[name](*args)
                return self.variables.get(name, 0)
            if t.value == '(':
                consume()
                val = expr()
                consume()
                return val
            if t.value == '-':
                consume()
                return -factor()
            raise ValueError(f"Unexpected: {t.value}")
        return expr()
''',
}

SIZE_CLASSES = {
    "tiny_incr": "small", "tiny_abs": "small",
    "small_factorial": "small", "small_bsearch": "small",
    "medium_linked_list": "medium", "medium_hashtable": "medium",
    "medium_expr_eval": "medium",
    "large_sorting": "large", "large_graph": "large",
    "large_calculator": "large",
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    lines = len(source.strip().splitlines())
    try:
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        build_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t1

        t2 = time.perf_counter()
        enc_objs = run_jugeo("encode", tmp)
        encode_time = time.perf_counter() - t2

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        morphisms = summary.get("morphisms", 0)

        eval_data = eval_objs[0] if eval_objs else {}
        per_coord = eval_data.get("per_coordinate", [])
        cover_q = eval_data.get("cover_quality", {}).get("total_score", 0)
        descent = eval_data.get("descent", {})
        total_funcs = sum(1 for c in per_coord if c.get("functions", 0) > 0)
        total_classes = sum(1 for c in per_coord if "class" in str(c.get("coordinate", "")).lower())
        verified = eval_data.get("descent", {}).get("coverage", 0) > 0

        enc_data = enc_objs[0] if enc_objs else {}
        enc_files = enc_data.get("files", [{}])
        enc_file = enc_files[0] if enc_files else {}
        enc_coords = enc_file.get("coordinates", {})
        total_assertions = sum(c.get("assertions", 0) for c in enc_coords.values())

        return {
            "name": name, "lines": lines,
            "size_class": SIZE_CLASSES.get(name, "medium"),
            "build_time": round(build_time, 4),
            "eval_time": round(eval_time, 4),
            "encode_time": round(encode_time, 4),
            "coords": coords, "morphisms": morphisms,
            "total_functions": total_funcs,
            "total_classes": total_classes,
            "cover_quality": cover_q,
            "verified": verified,
            "total_assertions": total_assertions,
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
    print("Paper 53: Codebase Orchestration — Scaling Analysis")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Lines: {m['lines']}, Size: {m['size_class']}")
        print(f"    Coords: {m['coords']}, Morphisms: {m['morphisms']}")
        print(f"    Build: {m['build_time']:.3f}s, Eval: {m['eval_time']:.3f}s")

    n = len(results)
    small = [r for r in results if r["size_class"] == "small"]
    medium = [r for r in results if r["size_class"] == "medium"]
    large = [r for r in results if r["size_class"] == "large"]

    mean_build = statistics.mean([r["build_time"] for r in results])
    mean_eval = statistics.mean([r["eval_time"] for r in results])
    mean_coords = statistics.mean([r["coords"] for r in results])
    mean_morphisms = statistics.mean([r["morphisms"] for r in results])
    total_funcs = sum(r["total_functions"] for r in results)
    total_classes = sum(r["total_classes"] for r in results)
    mean_encode = statistics.mean([r["encode_time"] for r in results])
    small_mean = statistics.mean([r["eval_time"] for r in small]) if small else 0
    medium_mean = statistics.mean([r["eval_time"] for r in medium]) if medium else 0
    large_mean = statistics.mean([r["eval_time"] for r in large]) if large else 0
    scaling_ratio = large_mean / small_mean if small_mean > 0 else 0
    verified = sum(1 for r in results if r["verified"])
    accuracy = verified / n if n else 0
    cover_q_mean = statistics.mean([r["cover_quality"] for r in results])

    # Descent time via separate descend calls
    desc_times = []
    for name, source in PROGRAMS.items():
        tmp = write_temp_py(source)
        try:
            t0 = time.perf_counter()
            run_jugeo("descend", tmp)
            desc_times.append(time.perf_counter() - t0)
        finally:
            cleanup(tmp)
    mean_descent = statistics.mean(desc_times) if desc_times else 0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:        {n}")
    print(f"  Mean build:      {fmt_time(mean_build)}")
    print(f"  Small mean:      {fmt_time(small_mean)}")
    print(f"  Large mean:      {fmt_time(large_mean)}")
    print(f"  Scaling ratio:   {scaling_ratio:.2f}x")

    tex_path = os.path.join(ROOT, "papers", "data-paper53.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper53.tex — AUTO-GENERATED by exp53_codebase_orchestration.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp53_codebase_orchestration.py\n\n")
        f.write(f"\\newcommand{{\\ppLIIItotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImeanBuildTime}}{{{fmt_time(mean_build)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImeanEvalTime}}{{{fmt_time(mean_eval)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImeanMorphisms}}{{{fmt_float(mean_morphisms)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIItotalFunctions}}{{{total_funcs}}}\n")
        f.write(f"\\newcommand{{\\ppLIIItotalClasses}}{{{total_classes}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImeanEncodeTime}}{{{fmt_time(mean_encode)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIIsmallMeanTime}}{{{fmt_time(small_mean)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImediumMeanTime}}{{{fmt_time(medium_mean)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIIlargeMeanTime}}{{{fmt_time(large_mean)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIIscalingRatio}}{{{fmt_float(scaling_ratio)}$\\times$}}\n")
        f.write(f"\\newcommand{{\\ppLIIIoverallAccuracy}}{{{fmt_pct(accuracy)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIIcoverQualityMean}}{{{fmt_float(cover_q_mean, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLIIImeanDescentTime}}{{{fmt_time(mean_descent)}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper53.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
