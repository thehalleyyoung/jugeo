#!/usr/bin/env python3
"""
Experiment 08 -- Treaty Negotiation: Modular Verification Comparison
====================================================================

Runs multi-module programs through jugeo descend, counts treaty negotiations
(overlap_conditions_checked), and generates comparison macros for Dafny/F*/Lean.

Writes macros to papers/data-paper08.tex with prefix ppEIGHT.
Re-run: python3 experiments/exp08_treaty_negotiation.py
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


def main():
    print("=" * 60)
    print("Experiment 08 -- Treaty Negotiation")
    print("=" * 60)

    tmpfiles = []
    results = []

    for pname, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        descend_objs = run_jugeo("descend", path)

        treaties = 0
        sections = 0
        verdict = "unknown"
        if descend_objs:
            d = descend_objs[0]
            treaties = d.get("overlap_conditions_checked", 0)
            sections = d.get("local_sections", 0)
            verdict = d.get("verdict", "unknown")

        results.append({
            "name": pname,
            "treaties": treaties,
            "sections": sections,
            "verdict": verdict,
        })
        print("  {:<24} treaties={:>2}  sections={:>2}  verdict={}".format(
            pname, treaties, sections, verdict))

    # -- Aggregate -------------------------------------------------------------
    n_total = len(results)
    total_treaties = sum(r["treaties"] for r in results)
    total_sections = sum(r["sections"] for r in results)
    treaty_vals = [r["treaties"] for r in results]
    mean_treaties = total_treaties / max(n_total, 1)
    max_treaties = max(treaty_vals) if treaty_vals else 0
    mean_sections = total_sections / max(n_total, 1)
    verified = sum(1 for r in results if r["verdict"] == "verified")
    success_rate = (verified / max(n_total, 1)) * 100

    # -- Write macros ----------------------------------------------------------
    out_path = os.path.join(REPO_ROOT, "papers", "data-paper08.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:
        f.write("% data-paper08.tex -- AUTO-GENERATED by exp08_treaty_negotiation.py\n")
        f.write("% DO NOT EDIT -- regenerate with: python3 experiments/exp08_treaty_negotiation.py\n\n")

        write_macro(f, "ppEIGHTtotalPrograms", n_total)
        write_macro(f, "ppEIGHTtotalTreaties", total_treaties)
        write_macro(f, "ppEIGHTmeanTreaties", "{:.1f}".format(mean_treaties))
        write_macro(f, "ppEIGHTmaxTreaties", max_treaties)
        write_macro(f, "ppEIGHTsuccessRate", "{:.0f}\\%".format(success_rate))

        f.write("\n% --- Dafny comparison ---\n")
        write_macro(f, "ppEIGHTdafnyAutoReconcile", "$\\times$")
        write_macro(f, "ppEIGHTdafnyDiagnostics", "error msg")
        write_macro(f, "ppEIGHTdafnyPython", "$\\times$")
        write_macro(f, "ppEIGHTdafnyTrust", "binary")

        f.write("\n% --- F* comparison ---\n")
        write_macro(f, "ppEIGHTfstarAutoReconcile", "$\\times$")
        write_macro(f, "ppEIGHTfstarDiagnostics", "type error")
        write_macro(f, "ppEIGHTfstarPython", "$\\times$")
        write_macro(f, "ppEIGHTfstarTrust", "binary")

        f.write("\n% --- Lean comparison ---\n")
        write_macro(f, "ppEIGHTleanAutoReconcile", "$\\times$")
        write_macro(f, "ppEIGHTleanDiagnostics", "type error")
        write_macro(f, "ppEIGHTleanPython", "$\\times$")
        write_macro(f, "ppEIGHTleanTrust", "binary")

        f.write("\n% --- JuGeo comparison ---\n")
        write_macro(f, "ppEIGHTjugeoAutoReconcile", "\\checkmark")
        write_macro(f, "ppEIGHTjugeoDiagnostics", "structured")
        write_macro(f, "ppEIGHTjugeoPython", "\\checkmark")
        write_macro(f, "ppEIGHTjugeoTrust", "$\\mathcal{T}_{\\mathrm{alg}}$")

        f.write("\n% --- Section stats ---\n")
        write_macro(f, "ppEIGHTmeanSections", "{:.1f}".format(mean_sections))
        write_macro(f, "ppEIGHTtotalSections", total_sections)

    print()
    print("Wrote " + out_path)
    print()
    print("SUMMARY:")
    print("  Total programs:      {}".format(n_total))
    print("  Total treaties:      {}".format(total_treaties))
    print("  Mean treaties:       {:.1f}".format(mean_treaties))
    print("  Max treaties:        {}".format(max_treaties))
    print("  Success rate:        {:.0f}%".format(success_rate))
    print("  Total sections:      {}".format(total_sections))
    print("  Mean sections:       {:.1f}".format(mean_sections))

    # cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()

# Also write results JSON
import json as _json, os as _os
_n = os.path.basename(__file__).split('_')[0].replace('exp','')
_results_path = _os.path.join(_os.path.dirname(__file__), f"results_paper{_n}.json")
with open(_results_path, "w") as _f:
    _json.dump({"paper": int(_n), "status": "completed"}, _f, indent=2)
print(f"Wrote {_results_path}")
