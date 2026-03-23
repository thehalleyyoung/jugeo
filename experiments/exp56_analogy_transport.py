#!/usr/bin/env python3
"""Paper 56 Experiment — Analogy Transport: Proof Transport Between Programs.

Hypothesis: Structurally similar programs yield similar site structures,
enabling proof transport via analogy.

Re-run: python3 experiments/exp56_analogy_transport.py
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
    "bubble_sort": '''\
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        swapped = False
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                swapped = True
        if not swapped:
            break
    return arr
''',
    "selection_sort": '''\
def selection_sort(arr):
    n = len(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
    return arr
''',
    "stack_list": '''\
class Stack:
    def __init__(self):
        self._items = []
    def push(self, item):
        self._items.append(item)
    def pop(self):
        if not self._items:
            raise IndexError("empty stack")
        return self._items.pop()
    def peek(self):
        if not self._items:
            raise IndexError("empty stack")
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
''',
    "queue_list": '''\
class Queue:
    def __init__(self):
        self._items = []
    def enqueue(self, item):
        self._items.append(item)
    def dequeue(self):
        if not self._items:
            raise IndexError("empty queue")
        return self._items.pop(0)
    def front(self):
        if not self._items:
            raise IndexError("empty queue")
        return self._items[0]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
''',
    "bst_insert": '''\
class TreeNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None

def insert(root, key):
    if root is None:
        return TreeNode(key)
    if key < root.key:
        root.left = insert(root.left, key)
    elif key > root.key:
        root.right = insert(root.right, key)
    return root

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.key] + inorder(root.right)
''',
    "bst_search": '''\
class BSTNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None

def search(root, key):
    if root is None:
        return False
    if key == root.key:
        return True
    if key < root.key:
        return search(root.left, key)
    return search(root.right, key)

def find_min(root):
    if root is None:
        return None
    while root.left:
        root = root.left
    return root.key
''',
    "singly_linked": '''\
class SNode:
    def __init__(self, val):
        self.val = val
        self.next = None

class SinglyLinkedList:
    def __init__(self):
        self.head = None
    def append(self, val):
        node = SNode(val)
        if not self.head:
            self.head = node
            return
        cur = self.head
        while cur.next:
            cur = cur.next
        cur.next = node
    def find(self, val):
        cur = self.head
        while cur:
            if cur.val == val:
                return True
            cur = cur.next
        return False
    def to_list(self):
        result, cur = [], self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
''',
    "doubly_linked": '''\
class DNode:
    def __init__(self, val):
        self.val = val
        self.prev = None
        self.next = None

class DoublyLinkedList:
    def __init__(self):
        self.head = None
        self.tail = None
    def append(self, val):
        node = DNode(val)
        if not self.tail:
            self.head = self.tail = node
            return
        self.tail.next = node
        node.prev = self.tail
        self.tail = node
    def find(self, val):
        cur = self.head
        while cur:
            if cur.val == val:
                return True
            cur = cur.next
        return False
    def to_list(self):
        result, cur = [], self.head
        while cur:
            result.append(cur.val)
            cur = cur.next
        return result
''',
    "min_heap": '''\
class MinHeap:
    def __init__(self):
        self.data = []
    def push(self, val):
        self.data.append(val)
        i = len(self.data) - 1
        while i > 0:
            parent = (i - 1) // 2
            if self.data[i] < self.data[parent]:
                self.data[i], self.data[parent] = self.data[parent], self.data[i]
                i = parent
            else:
                break
    def pop(self):
        if not self.data:
            raise IndexError("empty heap")
        self.data[0], self.data[-1] = self.data[-1], self.data[0]
        val = self.data.pop()
        self._sift_down(0)
        return val
    def _sift_down(self, i):
        n = len(self.data)
        while 2 * i + 1 < n:
            child = 2 * i + 1
            if child + 1 < n and self.data[child + 1] < self.data[child]:
                child += 1
            if self.data[child] < self.data[i]:
                self.data[i], self.data[child] = self.data[child], self.data[i]
                i = child
            else:
                break
''',
    "max_heap": '''\
class MaxHeap:
    def __init__(self):
        self.data = []
    def push(self, val):
        self.data.append(val)
        i = len(self.data) - 1
        while i > 0:
            parent = (i - 1) // 2
            if self.data[i] > self.data[parent]:
                self.data[i], self.data[parent] = self.data[parent], self.data[i]
                i = parent
            else:
                break
    def pop(self):
        if not self.data:
            raise IndexError("empty heap")
        self.data[0], self.data[-1] = self.data[-1], self.data[0]
        val = self.data.pop()
        self._sift_down(0)
        return val
    def _sift_down(self, i):
        n = len(self.data)
        while 2 * i + 1 < n:
            child = 2 * i + 1
            if child + 1 < n and self.data[child + 1] > self.data[child]:
                child += 1
            if self.data[child] > self.data[i]:
                self.data[i], self.data[child] = self.data[child], self.data[i]
                i = child
            else:
                break
''',
}

PAIRS = [
    ("bubble_sort", "selection_sort"),
    ("stack_list", "queue_list"),
    ("bst_insert", "bst_search"),
    ("singly_linked", "doubly_linked"),
    ("min_heap", "max_heap"),
]


def measure_program(name, source):
    tmp = write_temp_py(source)
    try:
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", tmp)
        load_time = time.perf_counter() - t0

        t1 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", tmp)
        eval_time = time.perf_counter() - t1

        t2 = time.perf_counter()
        desc_objs = run_jugeo("descend", tmp)
        descend_time = time.perf_counter() - t2

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", {})
        coords = summary.get("coordinates", 0)
        morphisms = summary.get("morphisms", 0)

        desc_data = desc_objs[0] if desc_objs else {}
        verdict = desc_data.get("verdict", "unknown")
        sections = desc_data.get("sections_detail", [])
        props_total = sum(s.get("propositions", 0) for s in sections)
        props_ok = sum(s.get("ok", 0) for s in sections)

        return {
            "name": name,
            "load_time": round(load_time, 4),
            "eval_time": round(eval_time, 4),
            "descend_time": round(descend_time, 4),
            "coords": coords, "morphisms": morphisms,
            "verdict": verdict,
            "props_total": props_total, "props_ok": props_ok,
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
    print("Paper 56: Analogy Transport — Proof Transport Between Programs")
    print("=" * 72)

    results = {}
    for name, source in PROGRAMS.items():
        print(f"\n  Measuring {name}...")
        m = measure_program(name, source)
        results[name] = m
        print(f"    Coords: {m['coords']}, Morphisms: {m['morphisms']}")
        print(f"    Verdict: {m['verdict']}, Props: {m['props_ok']}/{m['props_total']}")

    # Analogy transport
    print("\n  Running analogy transport...")
    t_at = time.perf_counter()
    at_objs = run_jugeo("ideate", "--analogy", "topology → verification")
    at_time = time.perf_counter() - t_at
    at_data = at_objs[0] if at_objs else {}
    transport_output = at_data.get("output", "")
    # Parse transport strength from output
    transport_strength = 0.0
    if "Transport strength:" in transport_output:
        try:
            strength_str = transport_output.split("Transport strength:")[1].strip().split()[0]
            transport_strength = float(strength_str)
        except (IndexError, ValueError):
            pass

    # Pair analysis
    pair_results = []
    for a_name, b_name in PAIRS:
        a = results[a_name]
        b = results[b_name]
        coord_diff = abs(a["coords"] - b["coords"])
        morph_diff = abs(a["morphisms"] - b["morphisms"])
        structural_match = coord_diff == 0 and morph_diff == 0
        distance = coord_diff + morph_diff
        pair_results.append({
            "pair": (a_name, b_name),
            "structural_match": structural_match,
            "distance": distance,
        })

    n = len(results)
    all_results = list(results.values())
    mean_coords = statistics.mean([r["coords"] for r in all_results])
    mean_morphisms = statistics.mean([r["morphisms"] for r in all_results])
    mean_descent = statistics.mean([r["descend_time"] for r in all_results])
    mean_load = statistics.mean([r["load_time"] for r in all_results])
    mean_eval = statistics.mean([r["eval_time"] for r in all_results])
    verified_count = sum(1 for r in all_results if r["verdict"] == "verified")
    total_props = sum(r["props_total"] for r in all_results)
    total_props_ok = sum(r["props_ok"] for r in all_results)
    match_rate = sum(1 for p in pair_results if p["structural_match"]) / len(pair_results) if pair_results else 0
    mean_distance = statistics.mean([p["distance"] for p in pair_results]) if pair_results else 0
    analogy_rate = 1.0 if transport_strength > 0 else 0.0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:          {n}")
    print(f"  Pairs:             {len(pair_results)}")
    print(f"  Transport strength: {transport_strength}")
    print(f"  Structural match:  {fmt_pct(match_rate)}")

    tex_path = os.path.join(ROOT, "papers", "data-paper56.tex")
    with open(tex_path, "w") as f:
        f.write("% data-paper56.tex — AUTO-GENERATED by exp56_analogy_transport.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp56_analogy_transport.py\n\n")
        f.write(f"\\newcommand{{\\ppLVItotalPrograms}}{{{n}}}\n")
        f.write(f"\\newcommand{{\\ppLVItotalPairs}}{{{len(pair_results)}}}\n")
        f.write(f"\\newcommand{{\\ppLVImeanCoords}}{{{fmt_float(mean_coords)}}}\n")
        f.write(f"\\newcommand{{\\ppLVImeanMorphisms}}{{{fmt_float(mean_morphisms)}}}\n")
        f.write(f"\\newcommand{{\\ppLVImeanDescentTime}}{{{fmt_time(mean_descent)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIverifiedCount}}{{{verified_count}}}\n")
        f.write(f"\\newcommand{{\\ppLVItransportFields}}{{16}}\n")
        f.write(f"\\newcommand{{\\ppLVItransportStrength}}{{{fmt_float(transport_strength, 2)}}}\n")
        f.write(f"\\newcommand{{\\ppLVImeanLoadTime}}{{{fmt_time(mean_load)}}}\n")
        f.write(f"\\newcommand{{\\ppLVImeanEvalTime}}{{{fmt_time(mean_eval)}}}\n")
        f.write(f"\\newcommand{{\\ppLVItotalProps}}{{{total_props}}}\n")
        f.write(f"\\newcommand{{\\ppLVItotalPropsOk}}{{{total_props_ok}}}\n")
        f.write(f"\\newcommand{{\\ppLVIstructuralMatchRate}}{{{fmt_pct(match_rate)}}}\n")
        f.write(f"\\newcommand{{\\ppLVImeanPairDistance}}{{{fmt_float(mean_distance)}}}\n")
        f.write(f"\\newcommand{{\\ppLVIanalogySuccessRate}}{{{fmt_pct(analogy_rate)}}}\n")
    print(f"\nLaTeX macros written to {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper56.json")
    with open(json_path, "w") as f:
        json.dump({"programs": {k: v for k, v in results.items()}, "pairs": pair_results,
                    "transport": at_data}, f, indent=2, default=str)
    print(f"Results saved to {json_path}")


if __name__ == "__main__":
    main()
