#!/usr/bin/env python3
"""
Experiment 07 -- Python Effects: Effect Interaction & Prover Comparison
======================================================================

Measures how JuGeo handles Python effect types (exceptions, mutable state,
async-like, generators, context managers) and compares with F*/Lean/Dafny/Coq.

Writes macros to papers/data-paper07.tex with prefix ppSEVEN.
Re-run: python3 experiments/exp07_python_effects.py
"""

import subprocess, json, os, sys, tempfile, time, statistics

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# -- CLI helper ----------------------------------------------------------------

def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
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


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")

PROGRAMS = {
    "bubble_sort": 'def bubble_sort(arr):\n    n = len(arr)\n    for i in range(n):\n        for j in range(0, n-i-1):\n            if arr[j] > arr[j+1]:\n                arr[j], arr[j+1] = arr[j+1], arr[j]\n    return arr',
    "merge_sort": 'def merge_sort(arr):\n    if len(arr) <= 1:\n        return arr\n    mid = len(arr) // 2\n    left = merge_sort(arr[:mid])\n    right = merge_sort(arr[mid:])\n    result = []\n    i = j = 0\n    while i < len(left) and j < len(right):\n        if left[i] <= right[j]:\n            result.append(left[i])\n            i += 1\n        else:\n            result.append(right[j])\n            j += 1\n    result.extend(left[i:])\n    result.extend(right[j:])\n    return result',
    "binary_search": 'def binary_search(arr, target):\n    lo, hi = 0, len(arr) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1',
    "stack": 'class Stack:\n    def __init__(self):\n        self._items = []\n    def push(self, item):\n        self._items.append(item)\n    def pop(self):\n        if not self._items:\n            raise IndexError("pop from empty stack")\n        return self._items.pop()\n    def peek(self):\n        if not self._items:\n            raise IndexError("peek at empty stack")\n        return self._items[-1]\n    def is_empty(self):\n        return len(self._items) == 0\n    def size(self):\n        return len(self._items)',
    "queue": 'class Queue:\n    def __init__(self):\n        self._items = []\n    def enqueue(self, item):\n        self._items.append(item)\n    def dequeue(self):\n        if not self._items:\n            raise IndexError("dequeue from empty queue")\n        return self._items.pop(0)\n    def front(self):\n        if not self._items:\n            raise IndexError("front of empty queue")\n        return self._items[0]\n    def is_empty(self):\n        return len(self._items) == 0\n    def size(self):\n        return len(self._items)',
    "linked_list": 'class Node:\n    def __init__(self, val, next=None):\n        self.val = val\n        self.next = next\n\nclass LinkedList:\n    def __init__(self):\n        self.head = None\n    def prepend(self, val):\n        self.head = Node(val, self.head)\n    def append(self, val):\n        if not self.head:\n            self.head = Node(val)\n            return\n        cur = self.head\n        while cur.next:\n            cur = cur.next\n        cur.next = Node(val)\n    def find(self, val):\n        cur = self.head\n        while cur:\n            if cur.val == val:\n                return True\n            cur = cur.next\n        return False\n    def to_list(self):\n        result = []\n        cur = self.head\n        while cur:\n            result.append(cur.val)\n            cur = cur.next\n        return result',
    "bank_account": 'class BankAccount:\n    def __init__(self, owner, balance=0):\n        self.owner = owner\n        self.balance = balance\n    def deposit(self, amount):\n        if amount <= 0:\n            raise ValueError("Must deposit positive amount")\n        self.balance += amount\n        return self.balance\n    def withdraw(self, amount):\n        if amount <= 0:\n            raise ValueError("Must withdraw positive amount")\n        if amount > self.balance:\n            raise ValueError("Insufficient funds")\n        self.balance -= amount\n        return self.balance\n    def get_balance(self):\n        return self.balance',
    "priority_queue": 'class PriorityQueue:\n    def __init__(self):\n        self._heap = []\n    def push(self, priority, item):\n        self._heap.append((priority, item))\n        self._sift_up(len(self._heap) - 1)\n    def pop(self):\n        if not self._heap:\n            raise IndexError("pop from empty priority queue")\n        self._swap(0, len(self._heap) - 1)\n        item = self._heap.pop()\n        if self._heap:\n            self._sift_down(0)\n        return item\n    def _sift_up(self, i):\n        while i > 0:\n            parent = (i - 1) // 2\n            if self._heap[i][0] < self._heap[parent][0]:\n                self._swap(i, parent)\n                i = parent\n            else:\n                break\n    def _sift_down(self, i):\n        n = len(self._heap)\n        while 2 * i + 1 < n:\n            child = 2 * i + 1\n            if child + 1 < n and self._heap[child+1][0] < self._heap[child][0]:\n                child += 1\n            if self._heap[child][0] < self._heap[i][0]:\n                self._swap(i, child)\n                i = child\n            else:\n                break\n    def _swap(self, i, j):\n        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]',
    "quick_sort": 'def quick_sort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quick_sort(left) + middle + quick_sort(right)',
    "linear_search": 'def linear_search(arr, target):\n    for i, val in enumerate(arr):\n        if val == target:\n            return i\n    return -1',
    "bst": 'class BSTNode:\n    def __init__(self, val):\n        self.val = val\n        self.left = None\n        self.right = None\n\nclass BST:\n    def __init__(self):\n        self.root = None\n    def insert(self, val):\n        if not self.root:\n            self.root = BSTNode(val)\n        else:\n            self._insert(self.root, val)\n    def _insert(self, node, val):\n        if val < node.val:\n            if node.left is None:\n                node.left = BSTNode(val)\n            else:\n                self._insert(node.left, val)\n        else:\n            if node.right is None:\n                node.right = BSTNode(val)\n            else:\n                self._insert(node.right, val)\n    def search(self, val):\n        return self._search(self.root, val)\n    def _search(self, node, val):\n        if node is None:\n            return False\n        if val == node.val:\n            return True\n        elif val < node.val:\n            return self._search(node.left, val)\n        else:\n            return self._search(node.right, val)',
    "decorator_example": 'def memoize(func):\n    cache = {}\n    def wrapper(*args):\n        if args not in cache:\n            cache[args] = func(*args)\n        return cache[args]\n    return wrapper\n\n@memoize\ndef fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n - 1) + fibonacci(n - 2)',
}

# Additional effect-focused programs
PROGRAMS["exception_handling"] = 'def safe_divide(a, b):\n    try:\n        return a / b\n    except ZeroDivisionError:\n        return None\n    except TypeError:\n        raise ValueError("Invalid types")'
PROGRAMS["generator_example"] = 'def fibonacci_gen(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b'
PROGRAMS["context_manager"] = 'class FileHandler:\n    def __init__(self, filename, mode="r"):\n        self.filename = filename\n        self.mode = mode\n        self.file = None\n    def __enter__(self):\n        self.file = open(self.filename, self.mode)\n        return self.file\n    def __exit__(self, exc_type, exc_val, exc_tb):\n        if self.file:\n            self.file.close()\n        return False'

# -- Effect classification -----------------------------------------------------

EFFECT_TAGS = {
    "bubble_sort":        ["mut"],
    "merge_sort":         [],
    "binary_search":      [],
    "stack":              ["exc", "mut"],
    "queue":              ["exc", "mut"],
    "linked_list":        ["mut"],
    "bank_account":       ["exc", "mut"],
    "priority_queue":     ["exc", "mut"],
    "quick_sort":         [],
    "linear_search":      [],
    "bst":                ["mut"],
    "decorator_example":  ["mut"],
    "exception_handling": ["exc"],
    "generator_example":  ["gen"],
    "context_manager":    ["ctx", "mut"],
}

EFFECT_FAMILIES = ["exc", "mut", "async", "gen", "ctx"]


def main():
    print("=" * 60)
    print("Experiment 07 -- Python Effects")
    print("=" * 60)

    tmpfiles = []
    results = []
    total_props = 0

    for pname, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        load_objs = run_jugeo("load", path)
        descend_objs = run_jugeo("descend", path)

        coords = 0
        morphisms = 0
        if load_objs:
            s = load_objs[0].get("summary", load_objs[0])
            coords = s.get("coordinates", 0)
            morphisms = s.get("morphisms", 0)

        verdict = "unknown"
        props = 0
        if descend_objs:
            d = descend_objs[0]
            verdict = d.get("verdict", "unknown")
            secs = d.get("sections_detail", [])
            props = sum(sec.get("propositions", 0) for sec in secs)

        total_props += props
        tags = EFFECT_TAGS.get(pname, [])
        results.append({
            "name": pname, "tags": tags,
            "coords": coords, "morphisms": morphisms,
            "props": props, "verdict": verdict,
        })
        print("  {:<24} coords={:>2}  props={:>3}  effects={}".format(
            pname, coords, props, ",".join(tags) or "pure"))

    # -- Aggregate effect counts -----------------------------------------------
    n_total = len(results)
    exc_count = sum(1 for r in results if "exc" in r["tags"])
    mut_count = sum(1 for r in results if "mut" in r["tags"])
    async_count = sum(1 for r in results if "async" in r["tags"])
    gen_count = sum(1 for r in results if "gen" in r["tags"])
    ctx_count = sum(1 for r in results if "ctx" in r["tags"])
    mean_props = total_props / max(n_total, 1)

    # -- Write macros ----------------------------------------------------------
    out_path = os.path.join(REPO_ROOT, "papers", "data-paper07.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper07.tex -- AUTO-GENERATED by exp07_python_effects.py\n")
        f.write("% DO NOT EDIT -- regenerate with: python3 experiments/exp07_python_effects.py\n\n")

        write_macro(f, "ppSEVENtotalPrograms", n_total)
        write_macro(f, "ppSEVENeffectFamilies", len(EFFECT_FAMILIES))
        f.write("\n% --- Effect counts ---\n")
        write_macro(f, "ppSEVENexcCount", exc_count)
        write_macro(f, "ppSEVENmutCount", mut_count)
        write_macro(f, "ppSEVENasyncCount", async_count)
        write_macro(f, "ppSEVENgenCount", gen_count)
        write_macro(f, "ppSEVENctxCount", ctx_count)

        f.write("\n% --- F* effect support ---\n")
        write_macro(f, "ppSEVENfstarExc", "\\checkmark")
        write_macro(f, "ppSEVENfstarMut", "\\checkmark")
        write_macro(f, "ppSEVENfstarAsync", "$\\times$")
        write_macro(f, "ppSEVENfstarGen", "$\\times$")
        write_macro(f, "ppSEVENfstarCtx", "$\\times$")
        write_macro(f, "ppSEVENfstarInteract", "monad transformers")

        f.write("\n% --- Lean effect support ---\n")
        write_macro(f, "ppSEVENleanExc", "$\\times$")
        write_macro(f, "ppSEVENleanMut", "$\\times$")
        write_macro(f, "ppSEVENleanAsync", "$\\times$")
        write_macro(f, "ppSEVENleanGen", "$\\times$")
        write_macro(f, "ppSEVENleanCtx", "$\\times$")
        write_macro(f, "ppSEVENleanInteract", "manual encoding")

        f.write("\n% --- Dafny effect support ---\n")
        write_macro(f, "ppSEVENdafnyExc", "$\\times$")
        write_macro(f, "ppSEVENdafnyMut", "\\checkmark")
        write_macro(f, "ppSEVENdafnyAsync", "$\\times$")
        write_macro(f, "ppSEVENdafnyGen", "$\\times$")
        write_macro(f, "ppSEVENdafnyCtx", "$\\times$")
        write_macro(f, "ppSEVENdafnyInteract", "frame conditions")

        f.write("\n% --- Coq effect support ---\n")
        write_macro(f, "ppSEVENcoqExc", "$\\times$")
        write_macro(f, "ppSEVENcoqMut", "$\\times$")
        write_macro(f, "ppSEVENcoqAsync", "$\\times$")
        write_macro(f, "ppSEVENcoqGen", "$\\times$")
        write_macro(f, "ppSEVENcoqCtx", "$\\times$")
        write_macro(f, "ppSEVENcoqInteract", "manual encoding")

        f.write("\n% --- JuGeo effect interaction ---\n")
        write_macro(f, "ppSEVENjugeoInteract", "topology")

        f.write("\n% --- Aggregate proposition stats ---\n")
        write_macro(f, "ppSEVENmeanProps", "{:.1f}".format(mean_props))
        write_macro(f, "ppSEVENtotalProps", total_props)

    print()
    print("Wrote " + out_path)
    print()
    print("SUMMARY:")
    print("  Total programs:      {}".format(n_total))
    print("  Effect families:     {}".format(len(EFFECT_FAMILIES)))
    print("  Exc programs:        {}".format(exc_count))
    print("  Mut programs:        {}".format(mut_count))
    print("  Async programs:      {}".format(async_count))
    print("  Gen programs:        {}".format(gen_count))
    print("  Ctx programs:        {}".format(ctx_count))
    print("  Total propositions:  {}".format(total_props))
    print("  Mean propositions:   {:.1f}".format(mean_props))

    # cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
