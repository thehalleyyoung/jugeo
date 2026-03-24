#!/usr/bin/env python3
"""Paper 02 Experiment -- Judgment Algebra.

Measures judgment and proposition counts per coordinate kind (MODULE,
FUNCTION, INTERFACE). Writes LaTeX macros to papers/data-paper02.tex.
"""

import ast
import json
import os
import subprocess
import statistics
import tempfile

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


def classify_coordinate(coord_name, code):
    """Classify a coordinate name as MODULE, FUNCTION, or INTERFACE."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "FUNCTION"
    class_names = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef)}
    func_names = {node.name for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)}
    if coord_name in class_names:
        return "INTERFACE"
    if coord_name in func_names:
        return "FUNCTION"
    return "MODULE"


def fmt(value):
    """Format a number for LaTeX: integers as-is, floats to 1 decimal."""
    if isinstance(value, int):
        return str(value)
    return f"{value:.1f}"


def write_macro(f, name, value):
    f.write(f"\\newcommand{{\\{name}}}{{{fmt(value)}}}\n")


def main():
    print("=" * 60)
    print("Paper 02: Judgment Algebra")
    print("=" * 60)

    results = []
    for name, code in PROGRAMS.items():
        tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
        tmp.write(code)
        tmp.close()
        try:
            load_objs = run_jugeo("load", tmp.name)
            desc_objs = run_jugeo("descend", tmp.name)

            if not load_objs or not desc_objs:
                print(f"  WARN: missing data for {name}")
                continue

            summary = load_objs[0].get("summary", load_objs[0])
            descend = desc_objs[0]

            judgments = summary.get("judgments", 0)
            bindings = summary.get("context_bindings", 0)

            sections = descend.get("sections_detail", [])
            total_props = sum(s.get("propositions", 0) for s in sections)

            kind_props = {"MODULE": [], "FUNCTION": [], "INTERFACE": []}
            for s in sections:
                coord = s.get("coordinate", "")
                kind = classify_coordinate(coord, code)
                kind_props[kind].append(s.get("propositions", 0))

            rec = {
                "name": name,
                "judgments": judgments,
                "bindings": bindings,
                "total_props": total_props,
                "kind_props": kind_props,
            }
            results.append(rec)
            print(f"  {name:20s}  judgments={judgments}  props={total_props}  "
                  f"bindings={bindings}  sections={len(sections)}")
        finally:
            os.unlink(tmp.name)

    if not results:
        print("ERROR: no results collected")
        return

    total_programs = len(results)
    total_judgments = sum(r["judgments"] for r in results)
    total_props = sum(r["total_props"] for r in results)
    total_bindings = sum(r["bindings"] for r in results)
    mean_judgments = statistics.mean([r["judgments"] for r in results])
    mean_props = statistics.mean([r["total_props"] for r in results])

    all_module = []
    all_function = []
    all_interface = []
    # Per-coordinate prop lists (one entry per individual coordinate)
    coord_module_props = []
    coord_function_props = []
    coord_interface_props = []
    module_dominant = 0
    function_dominant = 0
    for r in results:
        kp = r["kind_props"]
        m_sum = sum(kp["MODULE"]) if kp["MODULE"] else 0
        f_sum = sum(kp["FUNCTION"]) if kp["FUNCTION"] else 0
        i_sum = sum(kp["INTERFACE"]) if kp["INTERFACE"] else 0
        all_module.append(m_sum)
        all_function.append(f_sum)
        all_interface.append(i_sum)
        coord_module_props.extend(kp["MODULE"])
        coord_function_props.extend(kp["FUNCTION"])
        coord_interface_props.extend(kp["INTERFACE"])
        if m_sum >= f_sum and m_sum >= i_sum:
            module_dominant += 1
        elif f_sum >= m_sum and f_sum >= i_sum:
            function_dominant += 1

    avg_module_props = statistics.mean(all_module) if all_module else 0.0
    avg_function_props = statistics.mean(all_function) if all_function else 0.0
    avg_interface_props = statistics.mean(all_interface) if all_interface else 0.0
    overall_avg_props = mean_props

    # Per-coordinate-type mean props (avg props per individual coordinate)
    coord_module_mean = (statistics.mean(coord_module_props)
                         if coord_module_props else 0.0)
    coord_function_mean = (statistics.mean(coord_function_props)
                           if coord_function_props else 0.0)
    coord_interface_mean = (statistics.mean(coord_interface_props)
                            if coord_interface_props else 0.0)
    all_coord_props = coord_module_props + coord_function_props + coord_interface_props
    coord_overall_mean = (statistics.mean(all_coord_props)
                          if all_coord_props else 0.0)
    coord_module_count = len(coord_module_props)
    coord_function_count = len(coord_function_props)
    coord_interface_count = len(coord_interface_props)
    coord_total_count = len(all_coord_props)

    out_path = os.path.join(REPO_ROOT, "papers", "data-paper02.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write("% Auto-generated by experiments/exp02_judgment_algebra.py\n")
        f.write("% Do not edit manually.\n\n")

        write_macro(f, "ppTWOtotalPrograms", total_programs)
        write_macro(f, "ppTWOtotalJudgments", total_judgments)
        write_macro(f, "ppTWOtotalProps", total_props)
        write_macro(f, "ppTWOmeanJudgments", mean_judgments)
        write_macro(f, "ppTWOmeanProps", mean_props)
        write_macro(f, "ppTWOmoduleProps", avg_module_props)
        write_macro(f, "ppTWOfunctionProps", avg_function_props)
        write_macro(f, "ppTWOinterfaceProps", avg_interface_props)
        write_macro(f, "ppTWOmoduleDominant", module_dominant)
        write_macro(f, "ppTWOfunctionDominant", function_dominant)
        write_macro(f, "ppTWOoverallAvgProps", overall_avg_props)
        write_macro(f, "ppTWOtotalBindings", total_bindings)
        write_macro(f, "ppTWOfieldCount", 8)
        # Per-coordinate-type average propositions
        write_macro(f, "ppTWOmoduleCoordCount", coord_module_count)
        write_macro(f, "ppTWOfunctionCoordCount", coord_function_count)
        write_macro(f, "ppTWOinterfaceCoordCount", coord_interface_count)
        write_macro(f, "ppTWOtotalCoordCount", coord_total_count)
        write_macro(f, "ppTWOmoduleCoordMeanProps", coord_module_mean)
        write_macro(f, "ppTWOfunctionCoordMeanProps", coord_function_mean)
        write_macro(f, "ppTWOinterfaceCoordMeanProps", coord_interface_mean)
        write_macro(f, "ppTWOoverallCoordMeanProps", coord_overall_mean)

    print(f"\nMacros written to {out_path}")
    print(f"\nSummary:")
    print(f"  Programs analysed:     {total_programs}")
    print(f"  Total judgments:       {total_judgments}")
    print(f"  Total propositions:    {total_props}")
    print(f"  Mean judgments/prog:   {mean_judgments:.1f}")
    print(f"  Mean props/prog:       {mean_props:.1f}")
    print(f"  Avg MODULE props:      {avg_module_props:.1f}")
    print(f"  Avg FUNCTION props:    {avg_function_props:.1f}")
    print(f"  Avg INTERFACE props:   {avg_interface_props:.1f}")
    print(f"  Module-dominant progs: {module_dominant}")
    print(f"  Function-dominant:     {function_dominant}")
    print(f"  Total bindings:        {total_bindings}")
    print(f"  Judgment 8-tuple fields: 8")
    print(f"  Per-coordinate-type props:")
    print(f"    MODULE coords:       {coord_module_count}  mean props: {coord_module_mean:.1f}")
    print(f"    FUNCTION coords:     {coord_function_count}  mean props: {coord_function_mean:.1f}")
    print(f"    INTERFACE coords:    {coord_interface_count}  mean props: {coord_interface_mean:.1f}")
    print(f"    Overall per-coord:   {coord_total_count}  mean props: {coord_overall_mean:.1f}")


if __name__ == "__main__":
    main()

# Also write results JSON
import json as _json, os as _os
_n = os.path.basename(__file__).split('_')[0].replace('exp','')
_results_path = _os.path.join(_os.path.dirname(__file__), f"results_paper{_n}.json")
with open(_results_path, "w") as _f:
    _json.dump({"paper": int(_n), "status": "completed"}, _f, indent=2)
print(f"Wrote {_results_path}")
