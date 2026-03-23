#!/usr/bin/env python3
"""Paper 56 Experiment — Analogy Transport: Proof Transport Between Programs.

Studies structural similarity, analogy transport, and proof reuse across pairs
of structurally related programs via CLI descent/evaluation and the SiteBuilder API.

Every number is produced by calling the ``python3 -m jugeo`` CLI as a subprocess
or via the public Python API.
Re-run: python3 experiments/exp56_analogy_transport.py
Outputs: papers/data-paper56.tex  (LaTeX macros with \\ppLVI… prefix)
         experiments/results_paper56.json
"""
import ast, json, os, random, statistics, subprocess, sys, tempfile, time
from itertools import combinations

random.seed(42)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from jugeo.geometry.site import Coordinate, CoordinateKind, CoordinateMorphism
from jugeo.geometry import SiteBuilder

# ── helpers ──────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')]
    lines = [l for l in lines if not l.startswith("JuGeo v")]
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


def write_temp(source):
    """Write source to a temp .py file and return its path."""
    f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ── test programs (pairs of structurally similar programs) ───────────────

PROGRAMS = {
    "bubble_sort": '''
def bubble_sort(arr):
    n = len(arr)
    result = list(arr)
    for i in range(n):
        swapped = False
        for j in range(0, n - i - 1):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
                swapped = True
        if not swapped:
            break
    return result

def is_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True

def sort_and_check(arr):
    out = bubble_sort(arr)
    assert is_sorted(out)
    return out
''',

    "selection_sort": '''
def selection_sort(arr):
    n = len(arr)
    result = list(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if result[j] < result[min_idx]:
                min_idx = j
        result[i], result[min_idx] = result[min_idx], result[i]
    return result

def find_minimum(arr, start):
    min_idx = start
    for i in range(start + 1, len(arr)):
        if arr[i] < arr[min_idx]:
            min_idx = i
    return min_idx

def sort_and_check(arr):
    out = selection_sort(arr)
    for i in range(len(out) - 1):
        assert out[i] <= out[i + 1]
    return out
''',

    "stack_list": '''
class Stack:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)

    def pop(self):
        if self.is_empty():
            raise IndexError("Stack is empty")
        return self.items.pop()

    def peek(self):
        if self.is_empty():
            raise IndexError("Stack is empty")
        return self.items[-1]

    def is_empty(self):
        return len(self.items) == 0

    def size(self):
        return len(self.items)

    def clear(self):
        self.items = []
''',

    "queue_list": '''
class Queue:
    def __init__(self):
        self.items = []

    def enqueue(self, item):
        self.items.append(item)

    def dequeue(self):
        if self.is_empty():
            raise IndexError("Queue is empty")
        return self.items.pop(0)

    def front(self):
        if self.is_empty():
            raise IndexError("Queue is empty")
        return self.items[0]

    def is_empty(self):
        return len(self.items) == 0

    def size(self):
        return len(self.items)

    def clear(self):
        self.items = []
''',

    "binary_tree_insert": '''
class TreeNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None

def insert(root, val):
    if root is None:
        return TreeNode(val)
    if val < root.val:
        root.left = insert(root.left, val)
    elif val > root.val:
        root.right = insert(root.right, val)
    return root

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.val] + inorder(root.right)

def build_tree(values):
    root = None
    for v in values:
        root = insert(root, v)
    return root

def tree_size(root):
    if root is None:
        return 0
    return 1 + tree_size(root.left) + tree_size(root.right)
''',

    "bst_search": '''
class BSTNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None

def search(root, key):
    if root is None or root.key == key:
        return root
    if key < root.key:
        return search(root.left, key)
    return search(root.right, key)

def find_min(root):
    current = root
    while current and current.left:
        current = current.left
    return current

def find_max(root):
    current = root
    while current and current.right:
        current = current.right
    return current

def contains(root, key):
    return search(root, key) is not None

def count_nodes(root):
    if root is None:
        return 0
    return 1 + count_nodes(root.left) + count_nodes(root.right)
''',

    "singly_linked_list": '''
class Node:
    def __init__(self, data):
        self.data = data
        self.next = None

class SinglyLinkedList:
    def __init__(self):
        self.head = None

    def append(self, data):
        new_node = Node(data)
        if not self.head:
            self.head = new_node
            return
        current = self.head
        while current.next:
            current = current.next
        current.next = new_node

    def prepend(self, data):
        new_node = Node(data)
        new_node.next = self.head
        self.head = new_node

    def delete(self, data):
        if not self.head:
            return
        if self.head.data == data:
            self.head = self.head.next
            return
        current = self.head
        while current.next:
            if current.next.data == data:
                current.next = current.next.next
                return
            current = current.next

    def length(self):
        count = 0
        current = self.head
        while current:
            count += 1
            current = current.next
        return count
''',

    "doubly_linked_list": '''
class DNode:
    def __init__(self, data):
        self.data = data
        self.prev = None
        self.next = None

class DoublyLinkedList:
    def __init__(self):
        self.head = None
        self.tail = None

    def append(self, data):
        new_node = DNode(data)
        if not self.head:
            self.head = self.tail = new_node
            return
        new_node.prev = self.tail
        self.tail.next = new_node
        self.tail = new_node

    def prepend(self, data):
        new_node = DNode(data)
        if not self.head:
            self.head = self.tail = new_node
            return
        new_node.next = self.head
        self.head.prev = new_node
        self.head = new_node

    def delete(self, data):
        current = self.head
        while current:
            if current.data == data:
                if current.prev:
                    current.prev.next = current.next
                else:
                    self.head = current.next
                if current.next:
                    current.next.prev = current.prev
                else:
                    self.tail = current.prev
                return
            current = current.next

    def length(self):
        count = 0
        current = self.head
        while current:
            count += 1
            current = current.next
        return count
''',

    "min_heap": '''
class MinHeap:
    def __init__(self):
        self.heap = []

    def parent(self, i):
        return (i - 1) // 2

    def left_child(self, i):
        return 2 * i + 1

    def right_child(self, i):
        return 2 * i + 2

    def insert(self, val):
        self.heap.append(val)
        self._sift_up(len(self.heap) - 1)

    def extract_min(self):
        if not self.heap:
            raise IndexError("Heap is empty")
        min_val = self.heap[0]
        last = self.heap.pop()
        if self.heap:
            self.heap[0] = last
            self._sift_down(0)
        return min_val

    def _sift_up(self, i):
        while i > 0 and self.heap[self.parent(i)] > self.heap[i]:
            p = self.parent(i)
            self.heap[i], self.heap[p] = self.heap[p], self.heap[i]
            i = p

    def _sift_down(self, i):
        smallest = i
        left = self.left_child(i)
        right = self.right_child(i)
        if left < len(self.heap) and self.heap[left] < self.heap[smallest]:
            smallest = left
        if right < len(self.heap) and self.heap[right] < self.heap[smallest]:
            smallest = right
        if smallest != i:
            self.heap[i], self.heap[smallest] = self.heap[smallest], self.heap[i]
            self._sift_down(smallest)

    def peek(self):
        if not self.heap:
            raise IndexError("Heap is empty")
        return self.heap[0]

    def size(self):
        return len(self.heap)
''',

    "max_heap": '''
class MaxHeap:
    def __init__(self):
        self.heap = []

    def parent(self, i):
        return (i - 1) // 2

    def left_child(self, i):
        return 2 * i + 1

    def right_child(self, i):
        return 2 * i + 2

    def insert(self, val):
        self.heap.append(val)
        self._sift_up(len(self.heap) - 1)

    def extract_max(self):
        if not self.heap:
            raise IndexError("Heap is empty")
        max_val = self.heap[0]
        last = self.heap.pop()
        if self.heap:
            self.heap[0] = last
            self._sift_down(0)
        return max_val

    def _sift_up(self, i):
        while i > 0 and self.heap[self.parent(i)] < self.heap[i]:
            p = self.parent(i)
            self.heap[i], self.heap[p] = self.heap[p], self.heap[i]
            i = p

    def _sift_down(self, i):
        largest = i
        left = self.left_child(i)
        right = self.right_child(i)
        if left < len(self.heap) and self.heap[left] > self.heap[largest]:
            largest = left
        if right < len(self.heap) and self.heap[right] > self.heap[largest]:
            largest = right
        if largest != i:
            self.heap[i], self.heap[largest] = self.heap[largest], self.heap[i]
            self._sift_down(largest)

    def peek(self):
        if not self.heap:
            raise IndexError("Heap is empty")
        return self.heap[0]

    def size(self):
        return len(self.heap)
''',
}

# Structurally paired programs for analogy comparison
PAIRS = [
    ("bubble_sort", "selection_sort"),
    ("stack_list", "queue_list"),
    ("binary_tree_insert", "bst_search"),
    ("singly_linked_list", "doubly_linked_list"),
    ("min_heap", "max_heap"),
]


def structural_distance(site_a, site_b):
    """Compute a simple structural distance between two site summaries."""
    coords_a = site_a.get("coordinates", 0)
    coords_b = site_b.get("coordinates", 0)
    morphs_a = site_a.get("morphisms", 0)
    morphs_b = site_b.get("morphisms", 0)
    covers_a = site_a.get("covering_families", 0)
    covers_b = site_b.get("covering_families", 0)

    d_coords = abs(coords_a - coords_b)
    d_morphs = abs(morphs_a - morphs_b)
    d_covers = abs(covers_a - covers_b)
    max_coords = max(coords_a, coords_b, 1)
    max_morphs = max(morphs_a, morphs_b, 1)
    max_covers = max(covers_a, covers_b, 1)

    return round(
        (d_coords / max_coords + d_morphs / max_morphs + d_covers / max_covers) / 3.0,
        4,
    )


def structural_match(site_a, site_b):
    """Check if two sites have structurally matching shape."""
    return (
        site_a.get("coordinates", 0) == site_b.get("coordinates", 0)
        and site_a.get("morphisms", 0) == site_b.get("morphisms", 0)
    )


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("EXPERIMENT 56 — Analogy Transport")
    print("  All numbers from `python3 -m jugeo` CLI + Python API")
    print("=" * 72)
    print()

    tmpfiles = []
    program_results = {}

    load_times = []
    eval_times = []
    descent_times = []
    all_coords = []
    all_morphisms = []
    all_props = []
    all_props_ok = []
    verified_count = 0
    transport_fields = 0
    transport_strengths = []

    # ── per-program analysis ─────────────────────────────────────────────
    for name, source in PROGRAMS.items():
        print(f"  [{name}]")
        path = write_temp(source)
        tmpfiles.append(path)

        # ── load ─────────────────────────────────────────────────────────
        t0 = time.perf_counter()
        load_objs = run_jugeo("load", path)
        load_wall = time.perf_counter() - t0
        load_times.append(load_wall)

        load_data = load_objs[0] if load_objs else {}
        summary = load_data.get("summary", load_data)
        n_coords = summary.get("coordinates", 0)
        n_morphisms = summary.get("morphisms", 0)
        all_coords.append(n_coords)
        all_morphisms.append(n_morphisms)

        # ── evaluate ─────────────────────────────────────────────────────
        t0 = time.perf_counter()
        eval_objs = run_jugeo("evaluate", path)
        eval_wall = time.perf_counter() - t0
        eval_times.append(eval_wall)

        ev = eval_objs[0] if eval_objs else {}
        per_coord = ev.get("per_coordinate", [])
        trust_info = ev.get("trust", {})

        props_total = 0
        props_ok = 0
        for pc in per_coord:
            props_total += pc.get("functions", 0) + 1
            if pc.get("status", "").endswith("SETTLED"):
                props_ok += pc.get("functions", 0) + 1
        all_props.append(props_total)
        all_props_ok.append(props_ok)

        # ── descend ──────────────────────────────────────────────────────
        t0 = time.perf_counter()
        desc_objs = run_jugeo("descend", path)
        desc_wall = time.perf_counter() - t0
        descent_times.append(desc_wall)

        desc = desc_objs[0] if desc_objs else {}
        verdict = desc.get("verdict", "unknown")
        if verdict == "verified":
            verified_count += 1

        # ── analogy transport via SiteBuilder API ────────────────────────
        try:
            tree = ast.parse(source)
            func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            class_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

            sb = SiteBuilder(label=name)
            coord_objs = []
            for fn in func_names:
                c = Coordinate(f"{name}.{fn}", CoordinateKind.FUNCTION)
                sb.add_coordinate(c)
                coord_objs.append(c)
            for cn in class_names:
                c = Coordinate(f"{name}.{cn}", CoordinateKind.MODULE)
                sb.add_coordinate(c)
                coord_objs.append(c)
            if not coord_objs:
                c = Coordinate(name, CoordinateKind.MODULE)
                sb.add_coordinate(c)
                coord_objs.append(c)

            site = sb.build()
            at = site.analogy_transport()
            if isinstance(at, dict):
                for key in at:
                    transport_fields += 1
        except Exception as exc:
            at = {}
            print(f"    site API error: {exc}")

        program_results[name] = {
            "name": name,
            "load_wall_s": round(load_wall, 4),
            "eval_wall_s": round(eval_wall, 4),
            "descend_wall_s": round(desc_wall, 4),
            "coordinates": n_coords,
            "morphisms": n_morphisms,
            "verdict": verdict,
            "props_total": props_total,
            "props_ok": props_ok,
            "site_summary": summary,
            "analogy_transport": at,
        }
        print(f"    load={load_wall:.3f}s  eval={eval_wall:.3f}s  desc={desc_wall:.3f}s  "
              f"coords={n_coords}  morphisms={n_morphisms}  verdict={verdict}")

    # ── ideate analogy transport ─────────────────────────────────────────
    print("\n  Running ideate --analogy …")
    ideate_objs = run_jugeo("ideate", "--analogy", "topology → verification")
    ideate = ideate_objs[0] if ideate_objs else {}
    ideate_output = ideate.get("output", "")
    strength_line = [l for l in ideate_output.splitlines() if "strength" in l.lower()]
    if strength_line:
        import re
        m = re.search(r"(\d+\.\d+)", strength_line[0])
        if m:
            transport_strengths.append(float(m.group(1)))
    print(f"    ideate fields: {len(ideate)}")

    # ── pairwise comparison ──────────────────────────────────────────────
    print("\n  Pairwise structural comparison:")
    pair_distances = []
    structural_matches = 0
    analogy_successes = 0

    for name_a, name_b in PAIRS:
        res_a = program_results.get(name_a, {})
        res_b = program_results.get(name_b, {})
        site_a = res_a.get("site_summary", {})
        site_b = res_b.get("site_summary", {})

        dist = structural_distance(site_a, site_b)
        pair_distances.append(dist)

        matched = structural_match(site_a, site_b)
        if matched:
            structural_matches += 1

        # Analogy success: both verified or same structure
        both_verified = res_a.get("verdict") == "verified" and res_b.get("verdict") == "verified"
        if both_verified or matched:
            analogy_successes += 1

        print(f"    {name_a:25s} ↔ {name_b:25s}  dist={dist:.4f}  "
              f"match={'✓' if matched else '✗'}  "
              f"analogy={'✓' if (both_verified or matched) else '✗'}")

    # Also compute all C(10,2) = 45 pairs for total_pairs stat
    all_pair_names = list(PROGRAMS.keys())
    all_pairs = list(combinations(all_pair_names, 2))

    # ── aggregate stats ──────────────────────────────────────────────────
    n = len(PROGRAMS)
    n_pairs = len(all_pairs)
    mean_coords = round(statistics.mean(all_coords), 1) if all_coords else 0.0
    mean_morphisms = round(statistics.mean(all_morphisms), 1) if all_morphisms else 0.0
    mean_descent = round(statistics.mean(descent_times), 4) if descent_times else 0.0
    mean_load = round(statistics.mean(load_times), 4) if load_times else 0.0
    mean_eval = round(statistics.mean(eval_times), 4) if eval_times else 0.0
    total_props = sum(all_props)
    total_props_ok = sum(all_props_ok)
    mean_transport_strength = round(statistics.mean(transport_strengths), 4) if transport_strengths else 0.0
    struct_match_rate = round(structural_matches / len(PAIRS) * 100, 1) if PAIRS else 0.0
    mean_pair_dist = round(statistics.mean(pair_distances), 4) if pair_distances else 0.0
    analogy_success_rate = round(analogy_successes / len(PAIRS) * 100, 1) if PAIRS else 0.0

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:              {n}")
    print(f"  Total pairs:           {n_pairs}")
    print(f"  Mean coordinates:      {mean_coords}")
    print(f"  Mean morphisms:        {mean_morphisms}")
    print(f"  Mean descent time:     {mean_descent}s")
    print(f"  Verified count:        {verified_count}")
    print(f"  Transport fields:      {transport_fields}")
    print(f"  Transport strength:    {mean_transport_strength}")
    print(f"  Mean load time:        {mean_load}s")
    print(f"  Mean eval time:        {mean_eval}s")
    print(f"  Total propositions:    {total_props}")
    print(f"  Propositions OK:       {total_props_ok}")
    print(f"  Structural match rate: {struct_match_rate}%")
    print(f"  Mean pair distance:    {mean_pair_dist}")
    print(f"  Analogy success rate:  {analogy_success_rate}%")
    print("=" * 72)

    # ── write LaTeX macros ───────────────────────────────────────────────
    P = "ppLVI"
    tex = [
        "% data-paper56.tex — AUTO-GENERATED by exp56_analogy_transport.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp56_analogy_transport.py",
        "",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    m("totalPrograms", n)
    m("totalPairs", n_pairs)
    m("meanCoords", mean_coords)
    m("meanMorphisms", mean_morphisms)
    m("meanDescentTime", f"{mean_descent}\\,s")
    m("verifiedCount", verified_count)
    m("transportFields", transport_fields)
    m("transportStrength", mean_transport_strength)
    m("meanLoadTime", f"{mean_load}\\,s")
    m("meanEvalTime", f"{mean_eval}\\,s")
    m("totalProps", total_props)
    m("totalPropsOk", total_props_ok)
    m("structuralMatchRate", f"{struct_match_rate}\\%")
    m("meanPairDistance", mean_pair_dist)
    m("analogySuccessRate", f"{analogy_success_rate}\\%")

    tex_path = os.path.join(ROOT, "papers", "data-paper56.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    # ── write JSON results ───────────────────────────────────────────────
    output = {
        "experiment": "analogy_transport",
        "paper": 56,
        "note": "All JuGeo numbers from CLI subprocess + Python API.",
        "n_programs": n,
        "programs": list(program_results.values()),
        "pairs": [
            {"a": a, "b": b, "distance": pair_distances[i],
             "matched": structural_match(
                 program_results[a].get("site_summary", {}),
                 program_results[b].get("site_summary", {}),
             )}
            for i, (a, b) in enumerate(PAIRS)
        ],
        "ideate": ideate,
        "summary": {
            "total_programs": n,
            "total_pairs": n_pairs,
            "mean_coords": mean_coords,
            "mean_morphisms": mean_morphisms,
            "mean_descent_time": mean_descent,
            "verified_count": verified_count,
            "transport_fields": transport_fields,
            "transport_strength": mean_transport_strength,
            "mean_load_time": mean_load,
            "mean_eval_time": mean_eval,
            "total_props": total_props,
            "total_props_ok": total_props_ok,
            "structural_match_rate": struct_match_rate,
            "mean_pair_distance": mean_pair_dist,
            "analogy_success_rate": analogy_success_rate,
        },
    }
    json_path = os.path.join(os.path.dirname(__file__), "results_paper56.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {json_path}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        cleanup(p)

    print("\nDone.")


if __name__ == "__main__":
    main()
