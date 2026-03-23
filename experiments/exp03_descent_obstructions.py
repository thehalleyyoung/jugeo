#!/usr/bin/env python3
"""Paper 03 Experiment -- Descent Obstructions.

Classifies descent results into cohomology levels (H0, H1, H2, Hinf) and
measures obstruction counts. Writes LaTeX macros to papers/data-paper03.tex.
"""

import json
import os
import subprocess
import statistics
import tempfile
import time

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

PROGRAMS = {
    "bubble_sort": (
        "def bubble_sort(arr):\n"
        "    n = len(arr)\n"
        "    for i in range(n):\n"
        "        for j in range(0, n-i-1):\n"
        "            if arr[j] > arr[j+1]:\n"
        "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
        "    return arr"
    ),
    "merge_sort": (
        "def merge_sort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    mid = len(arr) // 2\n"
        "    left = merge_sort(arr[:mid])\n"
        "    right = merge_sort(arr[mid:])\n"
        "    result = []\n"
        "    i = j = 0\n"
        "    while i < len(left) and j < len(right):\n"
        "        if left[i] <= right[j]:\n"
        "            result.append(left[i])\n"
        "            i += 1\n"
        "        else:\n"
        "            result.append(right[j])\n"
        "            j += 1\n"
        "    result.extend(left[i:])\n"
        "    result.extend(right[j:])\n"
        "    return result"
    ),
    "binary_search": (
        "def binary_search(arr, target):\n"
        "    lo, hi = 0, len(arr) - 1\n"
        "    while lo <= hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if arr[mid] == target:\n"
        "            return mid\n"
        "        elif arr[mid] < target:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return -1"
    ),
    "stack": (
        "class Stack:\n"
        "    def __init__(self):\n"
        "        self._items = []\n"
        "    def push(self, item):\n"
        "        self._items.append(item)\n"
        "    def pop(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('pop from empty stack')\n"
        "        return self._items.pop()\n"
        "    def peek(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('peek at empty stack')\n"
        "        return self._items[-1]\n"
        "    def is_empty(self):\n"
        "        return len(self._items) == 0\n"
        "    def size(self):\n"
        "        return len(self._items)"
    ),
    "queue": (
        "class Queue:\n"
        "    def __init__(self):\n"
        "        self._items = []\n"
        "    def enqueue(self, item):\n"
        "        self._items.append(item)\n"
        "    def dequeue(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('dequeue from empty queue')\n"
        "        return self._items.pop(0)\n"
        "    def front(self):\n"
        "        if not self._items:\n"
        "            raise IndexError('front of empty queue')\n"
        "        return self._items[0]\n"
        "    def is_empty(self):\n"
        "        return len(self._items) == 0\n"
        "    def size(self):\n"
        "        return len(self._items)"
    ),
    "linked_list": (
        "class Node:\n"
        "    def __init__(self, val, next=None):\n"
        "        self.val = val\n"
        "        self.next = next\n"
        "\n"
        "class LinkedList:\n"
        "    def __init__(self):\n"
        "        self.head = None\n"
        "    def prepend(self, val):\n"
        "        self.head = Node(val, self.head)\n"
        "    def append(self, val):\n"
        "        if not self.head:\n"
        "            self.head = Node(val)\n"
        "            return\n"
        "        cur = self.head\n"
        "        while cur.next:\n"
        "            cur = cur.next\n"
        "        cur.next = Node(val)\n"
        "    def find(self, val):\n"
        "        cur = self.head\n"
        "        while cur:\n"
        "            if cur.val == val:\n"
        "                return True\n"
        "            cur = cur.next\n"
        "        return False\n"
        "    def to_list(self):\n"
        "        result = []\n"
        "        cur = self.head\n"
        "        while cur:\n"
        "            result.append(cur.val)\n"
        "            cur = cur.next\n"
        "        return result"
    ),
    "bank_account": (
        "class BankAccount:\n"
        "    def __init__(self, owner, balance=0):\n"
        "        self.owner = owner\n"
        "        self.balance = balance\n"
        "    def deposit(self, amount):\n"
        "        if amount <= 0:\n"
        "            raise ValueError('Must deposit positive amount')\n"
        "        self.balance += amount\n"
        "        return self.balance\n"
        "    def withdraw(self, amount):\n"
        "        if amount <= 0:\n"
        "            raise ValueError('Must withdraw positive amount')\n"
        "        if amount > self.balance:\n"
        "            raise ValueError('Insufficient funds')\n"
        "        self.balance -= amount\n"
        "        return self.balance\n"
        "    def get_balance(self):\n"
        "        return self.balance"
    ),
    "priority_queue": (
        "class PriorityQueue:\n"
        "    def __init__(self):\n"
        "        self._heap = []\n"
        "    def push(self, priority, item):\n"
        "        self._heap.append((priority, item))\n"
        "        self._sift_up(len(self._heap) - 1)\n"
        "    def pop(self):\n"
        "        if not self._heap:\n"
        "            raise IndexError('pop from empty priority queue')\n"
        "        self._swap(0, len(self._heap) - 1)\n"
        "        item = self._heap.pop()\n"
        "        if self._heap:\n"
        "            self._sift_down(0)\n"
        "        return item\n"
        "    def _sift_up(self, i):\n"
        "        while i > 0:\n"
        "            parent = (i - 1) // 2\n"
        "            if self._heap[i][0] < self._heap[parent][0]:\n"
        "                self._swap(i, parent)\n"
        "                i = parent\n"
        "            else:\n"
        "                break\n"
        "    def _sift_down(self, i):\n"
        "        n = len(self._heap)\n"
        "        while 2 * i + 1 < n:\n"
        "            child = 2 * i + 1\n"
        "            if child + 1 < n and self._heap[child+1][0] < self._heap[child][0]:\n"
        "                child += 1\n"
        "            if self._heap[child][0] < self._heap[i][0]:\n"
        "                self._swap(i, child)\n"
        "                i = child\n"
        "            else:\n"
        "                break\n"
        "    def _swap(self, i, j):\n"
        "        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]"
    ),
    "quick_sort": (
        "def quick_sort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    pivot = arr[len(arr) // 2]\n"
        "    left = [x for x in arr if x < pivot]\n"
        "    middle = [x for x in arr if x == pivot]\n"
        "    right = [x for x in arr if x > pivot]\n"
        "    return quick_sort(left) + middle + quick_sort(right)"
    ),
    "linear_search": (
        "def linear_search(arr, target):\n"
        "    for i, val in enumerate(arr):\n"
        "        if val == target:\n"
        "            return i\n"
        "    return -1"
    ),
    "bst": (
        "class BSTNode:\n"
        "    def __init__(self, val):\n"
        "        self.val = val\n"
        "        self.left = None\n"
        "        self.right = None\n"
        "\n"
        "class BST:\n"
        "    def __init__(self):\n"
        "        self.root = None\n"
        "    def insert(self, val):\n"
        "        if not self.root:\n"
        "            self.root = BSTNode(val)\n"
        "        else:\n"
        "            self._insert(self.root, val)\n"
        "    def _insert(self, node, val):\n"
        "        if val < node.val:\n"
        "            if node.left is None:\n"
        "                node.left = BSTNode(val)\n"
        "            else:\n"
        "                self._insert(node.left, val)\n"
        "        else:\n"
        "            if node.right is None:\n"
        "                node.right = BSTNode(val)\n"
        "            else:\n"
        "                self._insert(node.right, val)\n"
        "    def search(self, val):\n"
        "        return self._search(self.root, val)\n"
        "    def _search(self, node, val):\n"
        "        if node is None:\n"
        "            return False\n"
        "        if val == node.val:\n"
        "            return True\n"
        "        elif val < node.val:\n"
        "            return self._search(node.left, val)\n"
        "        else:\n"
        "            return self._search(node.right, val)"
    ),
    "decorator_example": (
        "def memoize(func):\n"
        "    cache = {}\n"
        "    def wrapper(*args):\n"
        "        if args not in cache:\n"
        "            cache[args] = func(*args)\n"
        "        return cache[args]\n"
        "    return wrapper\n"
        "\n"
        "@memoize\n"
        "def fibonacci(n):\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fibonacci(n - 1) + fibonacci(n - 2)"
    ),
}

def run_jugeo(*args):
    """Run a JuGeo CLI command and return parsed JSON objects."""
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


def classify_cohomology(descend_result):
    """Classify a descent result into a cohomology level.

    H0: fully discharged -- verdict=verified, no obstructions
    H1: overlap gap -- obstructions with overlap/gluing issues
    H2: structural -- obstructions from structural/type mismatches
    Hinf: oracle-deferred -- trust is UNVERIFIED or COPILOT_SUGGESTED only
    """
    verdict = descend_result.get("verdict", "")
    trust = descend_result.get("trust", "")
    obstructions = descend_result.get("obstructions", [])

    if verdict == "verified" and not obstructions:
        return "H0"

    if obstructions:
        obs_types = [o.get("type", "").lower() if isinstance(o, dict) else str(o).lower()
                     for o in obstructions]
        has_overlap = any("overlap" in t or "gluing" in t or "gap" in t for t in obs_types)
        has_structural = any("struct" in t or "type" in t or "arity" in t for t in obs_types)
        if has_overlap:
            return "H1"
        if has_structural:
            return "H2"
        return "H1"

    if trust in ("UNVERIFIED", "COPILOT_SUGGESTED"):
        return "Hinf"

    return "H0"


def fmt(value):
    """Format a number for LaTeX: integers as-is, floats to 1 decimal."""
    if isinstance(value, int):
        return str(value)
    return f"{value:.1f}"


def write_macro(f, name, value):
    f.write(f"\\newcommand{{\\{name}}}{{{fmt(value)}}}\n")


def main():
    print("=" * 60)
    print("Paper 03: Descent Obstructions")
    print("=" * 60)

    results = []
    for name, code in PROGRAMS.items():
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
        tmp.write(code)
        tmp.close()
        try:
            t0 = time.time()
            desc_objs = run_jugeo("descend", tmp.name)
            elapsed = time.time() - t0

            if not desc_objs:
                print(f"  WARN: no descend data for {name}")
                continue

            d = desc_objs[0]
            level = classify_cohomology(d)
            obstructions = d.get("obstructions", [])
            sections = d.get("local_sections", 0)
            overlaps = d.get("overlap_conditions_checked", 0)
            verdict = d.get("verdict", "unknown")
            trust = d.get("trust", "unknown")

            repairable = 0
            escalated = 0
            for o in obstructions:
                if isinstance(o, dict) and o.get("repairable", False):
                    repairable += 1
                else:
                    escalated += 1

            rec = {
                "name": name,
                "level": level,
                "obstructions": len(obstructions),
                "sections": sections,
                "overlaps": overlaps,
                "verdict": verdict,
                "trust": trust,
                "elapsed": elapsed,
                "repairable": repairable,
                "escalated": escalated,
            }
            results.append(rec)
            print(f"  {name:20s}  level={level:4s}  verdict={verdict:10s}  "
                  f"trust={trust:20s}  sections={sections}  overlaps={overlaps}  "
                  f"obstructions={len(obstructions)}  time={elapsed:.3f}s")
        finally:
            os.unlink(tmp.name)

    if not results:
        print("ERROR: no results collected")
        return

    total_programs = len(results)
    total_sections = sum(r["sections"] for r in results)
    total_overlaps = sum(r["overlaps"] for r in results)
    total_obstructions = sum(r["obstructions"] for r in results)
    total_repairable = sum(r["repairable"] for r in results)
    total_escalated = sum(r["escalated"] for r in results)

    h0 = sum(1 for r in results if r["level"] == "H0")
    h1 = sum(1 for r in results if r["level"] == "H1")
    h2 = sum(1 for r in results if r["level"] == "H2")
    hinf = sum(1 for r in results if r["level"] == "Hinf")

    def pct(n):
        return (n / total_programs * 100) if total_programs > 0 else 0.0

    mean_sections = statistics.mean([r["sections"] for r in results])
    mean_overlaps = statistics.mean([r["overlaps"] for r in results])
    mean_descent_time = statistics.mean([r["elapsed"] for r in results])

    out_path = os.path.join(REPO_ROOT, "papers", "data-paper03.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write("% Auto-generated by experiments/exp03_descent_obstructions.py\n")
        f.write("% Do not edit manually.\n\n")

        write_macro(f, "ppTHREEtotalPrograms", total_programs)
        write_macro(f, "ppTHREEtotalSections", total_sections)
        write_macro(f, "ppTHREEtotalOverlaps", total_overlaps)
        write_macro(f, "ppTHREEhZeroCount", h0)
        write_macro(f, "ppTHREEhOneCount", h1)
        write_macro(f, "ppTHREEhTwoCount", h2)
        write_macro(f, "ppTHREEhInfCount", hinf)
        write_macro(f, "ppTHREEhZeroPct", pct(h0))
        write_macro(f, "ppTHREEhOnePct", pct(h1))
        write_macro(f, "ppTHREEhTwoPct", pct(h2))
        write_macro(f, "ppTHREEhInfPct", pct(hinf))
        write_macro(f, "ppTHREEtotalObstructions", total_obstructions)
        write_macro(f, "ppTHREEmeanSections", mean_sections)
        write_macro(f, "ppTHREEmeanOverlaps", mean_overlaps)
        write_macro(f, "ppTHREErepairableCount", total_repairable)
        write_macro(f, "ppTHREEescalatedCount", total_escalated)
        write_macro(f, "ppTHREEmeanDescentTime", mean_descent_time)

    print(f"\nMacros written to {out_path}")
    print(f"\nSummary:")
    print(f"  Programs analysed:      {total_programs}")
    print(f"  Total sections:         {total_sections}")
    print(f"  Total overlaps:         {total_overlaps}")
    print(f"  Total obstructions:     {total_obstructions}")
    print(f"  H0 (fully discharged):  {h0} ({pct(h0):.1f}%)")
    print(f"  H1 (overlap gap):       {h1} ({pct(h1):.1f}%)")
    print(f"  H2 (structural):        {h2} ({pct(h2):.1f}%)")
    print(f"  Hinf (oracle-deferred): {hinf} ({pct(hinf):.1f}%)")
    print(f"  Mean sections/prog:     {mean_sections:.1f}")
    print(f"  Mean overlaps/prog:     {mean_overlaps:.1f}")
    print(f"  Repairable:             {total_repairable}")
    print(f"  Escalated:              {total_escalated}")
    print(f"  Mean descent time:      {mean_descent_time:.3f}s")


if __name__ == "__main__":
    main()
