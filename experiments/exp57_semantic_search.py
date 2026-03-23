#!/usr/bin/env python3
"""Paper 57 Experiment — Semantic Search: Sheaf-Indexed Code Search.

Hypothesis: JuGeo site structures provide rich semantic indices enabling
meaningful code search beyond syntactic matching.

Re-run: python3 experiments/exp57_semantic_search.py
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
    "matrix_mul": '''\
def matrix_multiply(a, b):
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result

def dot_product(a, b):
    return sum(x * y for x, y in zip(a, b))
''',
    "pattern_match": '''\
def naive_search(text, pattern):
    matches = []
    n, m = len(text), len(pattern)
    for i in range(n - m + 1):
        if text[i:i+m] == pattern:
            matches.append(i)
    return matches

def count_occurrences(text, pattern):
    return len(naive_search(text, pattern))
''',
    "hash_chain": '''\
class HashTable:
    def __init__(self, size=16):
        self.size = size
        self.table = [[] for _ in range(size)]

    def _hash(self, key):
        return hash(key) % self.size

    def put(self, key, value):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.table[idx]):
            if k == key:
                self.table[idx][i] = (key, value)
                return
        self.table[idx].append((key, value))

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self.table[idx]:
            if k == key:
                return v
        return default
''',
    "bst": '''\
class Node:
    def __init__(self, key, value=None):
        self.key = key
        self.value = value
        self.left = None
        self.right = None

def insert(root, key, value=None):
    if root is None:
        return Node(key, value)
    if key < root.key:
        root.left = insert(root.left, key, value)
    elif key > root.key:
        root.right = insert(root.right, key, value)
    else:
        root.value = value
    return root

def search(root, key):
    if root is None:
        return None
    if key == root.key:
        return root.value
    if key < root.key:
        return search(root.left, key)
    return search(root.right, key)
''',
    "regex_simple": '''\
def match_char(pattern, text):
    if not pattern:
        return True
    if not text:
        return pattern == '$'
    if len(pattern) > 1 and pattern[1] == '*':
        return match_star(pattern[0], pattern[2:], text)
    if pattern[0] == '$' and len(pattern) == 1:
        return len(text) == 0
    if pattern[0] == '.' or pattern[0] == text[0]:
        return match_char(pattern[1:], text[1:])
    return False

def match_star(c, pattern, text):
    if match_char(pattern, text):
        return True
    while text and (text[0] == c or c == '.'):
        text = text[1:]
        if match_char(pattern, text):
            return True
    return False
''',
    "csv_parser": '''\
def parse_csv(text, delimiter=','):
    rows = []
    for line in text.strip().split('\\n'):
        fields = []
        current = []
        in_quotes = False
        for ch in line:
            if ch == '"':
                in_quotes = not in_quotes
            elif ch == delimiter and not in_quotes:
                fields.append(''.join(current))
                current = []
            else:
                current.append(ch)
        fields.append(''.join(current))
        rows.append(fields)
    return rows

def csv_to_dicts(text, delimiter=','):
    rows = parse_csv(text, delimiter)
    if not rows:
        return []
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:]]
''',
    "http_builder": '''\
class HttpRequest:
    def __init__(self, method, url):
        self.method = method
        self.url = url
        self.headers = {}
        self.body = None

    def add_header(self, key, value):
        self.headers[key] = value
        return self

    def set_body(self, body):
        self.body = body
        return self

    def build(self):
        lines = [f"{self.method} {self.url} HTTP/1.1"]
        for k, v in self.headers.items():
            lines.append(f"{k}: {v}")
        if self.body:
            lines.append(f"Content-Length: {len(self.body)}")
            lines.append("")
            lines.append(self.body)
        return "\\r\\n".join(lines)
''',
    "event_emitter": '''\
class EventEmitter:
    def __init__(self):
        self.listeners = {}

    def on(self, event, callback):
        if event not in self.listeners:
            self.listeners[event] = []
        self.listeners[event].append(callback)

    def off(self, event, callback):
        if event in self.listeners:
            self.listeners[event] = [
                cb for cb in self.listeners[event] if cb != callback
            ]

    def emit(self, event, *args, **kwargs):
        for callback in self.listeners.get(event, []):
            callback(*args, **kwargs)

    def once(self, event, callback):
        def wrapper(*args, **kwargs):
            callback(*args, **kwargs)
            self.off(event, wrapper)
        self.on(event, wrapper)
''',
    "command_pattern": '''\
class Command:
    def execute(self):
        raise NotImplementedError
    def undo(self):
        raise NotImplementedError

class InsertCommand(Command):
    def __init__(self, document, position, text):
        self.document = document
        self.position = position
        self.text = text
    def execute(self):
        self.document.insert(self.position, self.text)
    def undo(self):
        self.document.delete(self.position, len(self.text))

class CommandHistory:
    def __init__(self):
        self.history = []
        self.redo_stack = []
    def execute(self, command):
        command.execute()
        self.history.append(command)
        self.redo_stack.clear()
    def undo(self):
        if self.history:
            cmd = self.history.pop()
            cmd.undo()
            self.redo_stack.append(cmd)
    def redo(self):
        if self.redo_stack:
            cmd = self.redo_stack.pop()
            cmd.execute()
            self.history.append(cmd)
''',
    "iter_utils": '''\
def take(iterable, n):
    result = []
    for i, item in enumerate(iterable):
        if i >= n:
            break
        result.append(item)
    return result

def drop(iterable, n):
    result = []
    for i, item in enumerate(iterable):
        if i >= n:
            result.append(item)
    return result

def chunk(lst, size):
    return [lst[i:i+size] for i in range(0, len(lst), size)]

def flatten(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result

def unique(lst):
    seen = set()
    result = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
''',
}


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        build_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        enc_objs = run_jugeo("encode", tmp)
        encode_time = time.perf_counter() - t1

        t2 = time.perf_counter()
        class_objs = run_jugeo("classify", tmp)
        classify_time = time.perf_counter() - t2

        t3 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t3

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        morphisms = summary.get("morphisms", 0)

        enc_data = enc_objs[0] if enc_objs else {}
        enc_files = enc_data.get("files", [{}])
        enc_file = enc_files[0] if enc_files else {}
        enc_coords = enc_file.get("coordinates", {})
        total_assertions = sum(c.get("assertions", 0) for c in enc_coords.values())
        decidable = sum(1 for c in enc_coords.values() if c.get("decidability") == "decidable")
        boundary = sum(1 for c in enc_coords.values() if c.get("frontier_side") == "boundary")

        class_data = class_objs[0] if class_objs else {}
        category = class_data.get("classification", {}).get("category", "UNKNOWN")

        eval_data = eval_objs[0] if eval_objs else {}
        per_coord = eval_data.get("per_coordinate", [])
        qualities = [c.get("quality", 0) for c in per_coord]
        complexities = [c.get("complexity", 0) for c in per_coord]
        total_funcs = sum(c.get("functions", 0) for c in per_coord)
        total_classes = 0
        for c in per_coord:
            coord_name = str(c.get("coordinate", ""))
            if "class" in coord_name.lower() or c.get("functions", 0) > 2:
                total_classes += 1

        return {
            "name": name,
            "build_time": round(build_time, 4),
            "encode_time": round(encode_time, 4),
            "classify_time": round(classify_time, 4),
            "eval_time": round(eval_time, 4),
            "coords": coords, "morphisms": morphisms,
            "total_assertions": total_assertions,
            "decidable": decidable, "boundary": boundary,
            "category": category,
            "mean_quality": statistics.mean(qualities) if qualities else 0,
            "mean_complexity": statistics.mean(complexities) if complexities else 0,
            "total_functions": total_funcs,
            "total_classes": total_classes,
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
    print("Paper 57: Semantic Search — Sheaf-Indexed Code Search")
    print("=" * 72)

    results = []
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results.append(m)
        print(f"    Coords: {m['coords']}, Assertions: {m['total_assertions']}")
        print(f"    Category: {m['category']}, Quality: {m['mean_quality']:.3f}")

    n = len(results)
    mean_build = statistics.mean([r["build_time"] for r in results])
    mean_coords = statistics.mean([r["coords"] for r in results])
    mean_morphisms = statistics.mean([r["morphisms"] for r in results])
    total_funcs = sum(r["total_functions"] for r in results)
    total_classes = sum(r["total_classes"] for r in results)
    total_assertions = sum(r["total_assertions"] for r in results)
    mean_assertions = total_assertions / max(1, sum(r["coords"] for r in results))
    decidable_total = sum(r["decidable"] for r in results)
    boundary_total = sum(r["boundary"] for r in results)
    mean_classify = statistics.mean([r["classify_time"] for r in results])
    mean_encode = statistics.mean([r["encode_time"] for r in results])
    categories = [r["category"] for r in results]
    top_category = max(set(categories), key=categories.count) if categories else "UNKNOWN"
    mean_quality = statistics.mean([r["mean_quality"] for r in results])
    mean_complexity = statistics.mean([r["mean_complexity"] for r in results])

    tex_path = os.path.join(ROOT, "papers", "data-paper57.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper57.tex — AUTO-GENERATED by exp57_semantic_search.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp57_semantic_search.py\n\n")
        f.write(f"\\newcommand{{\\ppLVIItotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanBuildTime}}{{{fmt_time(mean_build)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanMorphisms}}{{{fmt_float(mean_morphisms)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIItotalFunctions}}{{{total_funcs}}}\n")
        f.write(f"\\newcommand{{\\ppLVIItotalClasses}}{{{total_classes}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanAssertions}}{{{fmt_float(mean_assertions, 2)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIItotalAssertions}}{{{total_assertions}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIdecidableCoords}}{{{decidable_total}}}\n")
        f.write(f"\\newcommand{{\\ppLVIIboundaryCoords}}{{{boundary_total}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanClassifyTime}}{{{fmt_time(mean_classify)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanEncodeTime}}{{{fmt_time(mean_encode)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIItopCategory}}{{{top_category}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanQuality}}{{{fmt_float(mean_quality, 3)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIImeanComplexity}}{{{fmt_float(mean_complexity, 1)}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper57.json")
    with open(json_path, "w") as f:
        json.dump({"programs": results}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
