#!/usr/bin/env python3
"""Paper 10 Experiment — Full Benchmark Suite for Sheaf-Theoretic Verification.

Hypothesis: JuGeo achieves high accuracy across spec-checking, equivalence-
checking, bug-detection, site encoding, and descent on a diverse benchmark
suite of 100 real-world Python programs spanning 10 categories.

Methodology:
  - jugeo prove  on all 100 programs (spec-checking accuracy)
  - jugeo equiv  on 30 program pairs (equivalence checking)
  - jugeo bugs   on 30 buggy + 30 clean programs (bug detection TP/TN)
  - jugeo encode on all 100 programs (site construction)
  - jugeo descend on a representative subset

Every number is produced by the jugeo CLI (subprocess).
Re-run: python3 experiments/exp10_full_benchmark.py
"""
import subprocess, json, os, tempfile, time, random, ast, statistics

random.seed(42)
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


def write_temp_txt(text):
    """Write text to a temp .txt file, return path."""
    f = tempfile.NamedTemporaryFile(suffix='.txt', mode='w', delete=False, dir='/tmp')
    f.write(text)
    f.close()
    return f.name


def ast_check(source):
    """AST-based check: can it parse? Returns (ok, time_us)."""
    t0 = time.perf_counter()
    try:
        ast.parse(source)
        ok = True
    except SyntaxError:
        ok = False
    return ok, (time.perf_counter() - t0) * 1e6


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# -- Literature baselines (NOT measured by this script) ------------------------

LITERATURE_BASELINES = {
    "CrossHair": {
        "description": "Symbolic execution for Python contracts",
        "estimated_accuracy_pct": "70-75%",
        "measured": False,
        "label": "LITERATURE_ESTIMATED",
        "cite": "Hallstrom, CrossHair, PyCon 2021",
    },
    "Hypothesis": {
        "description": "Property-based testing for Python",
        "estimated_accuracy_pct": "60-65%",
        "measured": False,
        "label": "LITERATURE_ESTIMATED",
        "cite": "MacIver, Hypothesis, 2019",
    },
    "mypy": {
        "description": "Static type checking only",
        "estimated_accuracy_pct": "~30%",
        "measured": False,
        "label": "LITERATURE_ESTIMATED",
        "cite": "mypy documentation",
    },
    "GPT_4o": {
        "description": "LLM-based verification",
        "estimated_accuracy_pct": "60-70%",
        "measured": False,
        "label": "LITERATURE_ESTIMATED",
        "cite": "OpenAI, GPT-4 Technical Report, 2024",
    },
    "Lean4": {
        "description": "Interactive theorem prover",
        "estimated_accuracy_pct": "~95% (manual proofs)",
        "measured": False,
        "label": "LITERATURE_ESTIMATED",
        "cite": "de Moura et al., Lean 4, CADE 2021",
    },
    "Dafny": {
        "description": "Verification-aware language",
        "estimated_accuracy_pct": "~90% (Dafny subset)",
        "measured": False,
        "label": "LITERATURE_ESTIMATED",
        "cite": "Leino, Dafny, LPAR 2010",
    },
}


# ==============================================================================
# BENCHMARK SUITE -- 100 programs across 10 categories
# Each program is >= 20 lines of natural, real-world Python.
# ==============================================================================

PROGRAMS = {

    # ---- Category 1: Sorting and Searching Algorithms (10) -------------------

    "sort_merge": '''\
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
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result
''',

    "sort_quick": '''\
def quicksort(arr, low=None, high=None):
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low < high:
        pi = _partition(arr, low, high)
        quicksort(arr, low, pi - 1)
        quicksort(arr, pi + 1, high)
    return arr


def _partition(arr, low, high):
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1
''',

    "search_binary": '''\
def binary_search(arr, target):
    left = 0
    right = len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1


def binary_search_recursive(arr, target, left=0, right=None):
    if right is None:
        right = len(arr) - 1
    if left > right:
        return -1
    mid = (left + right) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, right)
    return binary_search_recursive(arr, target, left, mid - 1)
''',

    "search_bfs": '''\
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
''',

    "search_dfs": '''\
def dfs_iterative(graph, start):
    visited = set()
    stack = [start]
    order = []
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        order.append(node)
        for neighbor in reversed(sorted(graph.get(node, []))):
            if neighbor not in visited:
                stack.append(neighbor)
    return order


def dfs_recursive(graph, start, visited=None):
    if visited is None:
        visited = set()
    visited.add(start)
    result = [start]
    for neighbor in sorted(graph.get(start, [])):
        if neighbor not in visited:
            result.extend(dfs_recursive(graph, neighbor, visited))
    return result
''',

    "sort_heap": '''\
def heap_sort(arr):
    n = len(arr)
    for i in range(n // 2 - 1, -1, -1):
        _heapify(arr, n, i)
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        _heapify(arr, i, 0)
    return arr


def _heapify(arr, n, i):
    largest = i
    left = 2 * i + 1
    right = 2 * i + 2
    if left < n and arr[left] > arr[largest]:
        largest = left
    if right < n and arr[right] > arr[largest]:
        largest = right
    if largest != i:
        arr[i], arr[largest] = arr[largest], arr[i]
        _heapify(arr, n, largest)
''',

    "sort_insertion": '''\
def insertion_sort(arr):
    result = list(arr)
    for i in range(1, len(result)):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def binary_insertion_sort(arr):
    result = list(arr)
    for i in range(1, len(result)):
        key = result[i]
        lo, hi = 0, i
        while lo < hi:
            mid = (lo + hi) // 2
            if result[mid] > key:
                hi = mid
            else:
                lo = mid + 1
        result[lo + 1:i + 1] = result[lo:i]
        result[lo] = key
    return result
''',

    "sort_radix": '''\
def counting_sort_by_digit(arr, exp):
    n = len(arr)
    output = [0] * n
    count = [0] * 10
    for i in range(n):
        idx = (arr[i] // exp) % 10
        count[idx] += 1
    for i in range(1, 10):
        count[i] += count[i - 1]
    for i in range(n - 1, -1, -1):
        idx = (arr[i] // exp) % 10
        output[count[idx] - 1] = arr[i]
        count[idx] -= 1
    for i in range(n):
        arr[i] = output[i]


def radix_sort(arr):
    if not arr:
        return arr
    max_val = max(arr)
    exp = 1
    while max_val // exp > 0:
        counting_sort_by_digit(arr, exp)
        exp *= 10
    return arr
''',

    "search_linear": '''\
def linear_search(arr, target):
    for i, val in enumerate(arr):
        if val == target:
            return i
    return -1


def linear_search_sentinel(arr, target):
    n = len(arr)
    if n == 0:
        return -1
    last = arr[n - 1]
    arr[n - 1] = target
    i = 0
    while arr[i] != target:
        i += 1
    arr[n - 1] = last
    if i < n - 1 or arr[n - 1] == target:
        return i
    return -1


def find_all_occurrences(arr, target):
    indices = []
    for i, val in enumerate(arr):
        if val == target:
            indices.append(i)
    return indices
''',

    "sort_topological": '''\
from collections import defaultdict


def topological_sort(num_nodes, edges):
    graph = defaultdict(list)
    in_degree = [0] * num_nodes
    for u, v in edges:
        graph[u].append(v)
        in_degree[v] += 1
    queue = []
    for i in range(num_nodes):
        if in_degree[i] == 0:
            queue.append(i)
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != num_nodes:
        return None
    return result
''',

    # ---- Category 2: Data Structures (10) ------------------------------------

    "ds_linked_list": '''\
class Node:
    def __init__(self, data):
        self.data = data
        self.next = None


class LinkedList:
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

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.data)
            current = current.next
        return result

    def reverse(self):
        prev = None
        current = self.head
        while current:
            nxt = current.next
            current.next = prev
            prev = current
            current = nxt
        self.head = prev
''',

    "ds_bst": '''\
class BSTNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None


def bst_insert(root, key):
    if root is None:
        return BSTNode(key)
    if key < root.key:
        root.left = bst_insert(root.left, key)
    elif key > root.key:
        root.right = bst_insert(root.right, key)
    return root


def bst_search(root, key):
    if root is None or root.key == key:
        return root
    if key < root.key:
        return bst_search(root.left, key)
    return bst_search(root.right, key)


def bst_inorder(root):
    if root is None:
        return []
    return bst_inorder(root.left) + [root.key] + bst_inorder(root.right)
''',

    "ds_min_heap": '''\
class MinHeap:
    def __init__(self):
        self.heap = []

    def _parent(self, i):
        return (i - 1) // 2

    def _left(self, i):
        return 2 * i + 1

    def _right(self, i):
        return 2 * i + 2

    def push(self, val):
        self.heap.append(val)
        i = len(self.heap) - 1
        while i > 0 and self.heap[self._parent(i)] > self.heap[i]:
            pi = self._parent(i)
            self.heap[i], self.heap[pi] = self.heap[pi], self.heap[i]
            i = pi

    def pop(self):
        if not self.heap:
            return None
        root = self.heap[0]
        self.heap[0] = self.heap[-1]
        self.heap.pop()
        self._sift_down(0)
        return root

    def _sift_down(self, i):
        smallest = i
        left = self._left(i)
        right = self._right(i)
        if left < len(self.heap) and self.heap[left] < self.heap[smallest]:
            smallest = left
        if right < len(self.heap) and self.heap[right] < self.heap[smallest]:
            smallest = right
        if smallest != i:
            self.heap[i], self.heap[smallest] = self.heap[smallest], self.heap[i]
            self._sift_down(smallest)
''',

    "ds_hash_map": '''\
class HashMap:
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

    def keys(self):
        result = []
        for bucket in self.buckets:
            for k, v in bucket:
                result.append(k)
        return result
''',

    "ds_trie": '''\
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end = False


class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        node = self.root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.is_end = True

    def search(self, word):
        node = self._find_node(word)
        return node is not None and node.is_end

    def starts_with(self, prefix):
        return self._find_node(prefix) is not None

    def _find_node(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return None
            node = node.children[ch]
        return node
''',

    "ds_stack_min": '''\
class MinStack:
    def __init__(self):
        self.stack = []
        self.min_stack = []

    def push(self, val):
        self.stack.append(val)
        if not self.min_stack or val <= self.min_stack[-1]:
            self.min_stack.append(val)

    def pop(self):
        if not self.stack:
            return None
        val = self.stack.pop()
        if val == self.min_stack[-1]:
            self.min_stack.pop()
        return val

    def top(self):
        if not self.stack:
            return None
        return self.stack[-1]

    def get_min(self):
        if not self.min_stack:
            return None
        return self.min_stack[-1]

    def is_empty(self):
        return len(self.stack) == 0

    def size(self):
        return len(self.stack)
''',

    "ds_queue_circular": '''\
class CircularQueue:
    def __init__(self, capacity):
        self.capacity = capacity
        self.queue = [None] * capacity
        self.head = 0
        self.tail = 0
        self.count = 0

    def enqueue(self, val):
        if self.is_full():
            return False
        self.queue[self.tail] = val
        self.tail = (self.tail + 1) % self.capacity
        self.count += 1
        return True

    def dequeue(self):
        if self.is_empty():
            return None
        val = self.queue[self.head]
        self.queue[self.head] = None
        self.head = (self.head + 1) % self.capacity
        self.count -= 1
        return val

    def peek(self):
        if self.is_empty():
            return None
        return self.queue[self.head]

    def is_empty(self):
        return self.count == 0

    def is_full(self):
        return self.count == self.capacity
''',

    "ds_graph": '''\
class Graph:
    def __init__(self, directed=False):
        self.adj = {}
        self.directed = directed

    def add_vertex(self, v):
        if v not in self.adj:
            self.adj[v] = []

    def add_edge(self, u, v, weight=1):
        self.add_vertex(u)
        self.add_vertex(v)
        self.adj[u].append((v, weight))
        if not self.directed:
            self.adj[v].append((u, weight))

    def neighbors(self, v):
        return [n for n, w in self.adj.get(v, [])]

    def has_path(self, start, end):
        visited = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == end:
                return True
            if node in visited:
                continue
            visited.add(node)
            for n, w in self.adj.get(node, []):
                stack.append(n)
        return False

    def vertex_count(self):
        return len(self.adj)

    def edge_count(self):
        total = sum(len(v) for v in self.adj.values())
        if not self.directed:
            total //= 2
        return total
''',

    "ds_doubly_linked": '''\
class DNode:
    def __init__(self, data):
        self.data = data
        self.prev = None
        self.next = None


class DoublyLinkedList:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0

    def append(self, data):
        node = DNode(data)
        if not self.tail:
            self.head = self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        self.size += 1

    def prepend(self, data):
        node = DNode(data)
        if not self.head:
            self.head = self.tail = node
        else:
            node.next = self.head
            self.head.prev = node
            self.head = node
        self.size += 1

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.data)
            current = current.next
        return result

    def to_list_reverse(self):
        result = []
        current = self.tail
        while current:
            result.append(current.data)
            current = current.prev
        return result
''',

    "ds_union_find": '''\
class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.components = n

    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        rx = self.find(x)
        ry = self.find(y)
        if rx == ry:
            return False
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.components -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def count_components(self):
        return self.components
''',

    # ---- Category 3: String / Text Processing (10) ---------------------------

    "str_tokenizer": '''\
def tokenize(expression):
    tokens = []
    i = 0
    while i < len(expression):
        ch = expression[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == '.' and i + 1 < len(expression) and expression[i + 1].isdigit()):
            num = []
            while i < len(expression) and (expression[i].isdigit() or expression[i] == '.'):
                num.append(expression[i])
                i += 1
            tokens.append(("NUMBER", "".join(num)))
        elif ch.isalpha() or ch == '_':
            ident = []
            while i < len(expression) and (expression[i].isalnum() or expression[i] == '_'):
                ident.append(expression[i])
                i += 1
            tokens.append(("IDENT", "".join(ident)))
        elif ch in '+-*/()=<>!':
            if i + 1 < len(expression) and expression[i + 1] == '=':
                tokens.append(("OP", ch + '='))
                i += 2
            else:
                tokens.append(("OP", ch))
                i += 1
        else:
            tokens.append(("UNKNOWN", ch))
            i += 1
    return tokens
''',

    "str_csv_parse": '''\
def parse_csv_line(line, delimiter=',', quote_char='"'):
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == quote_char:
                if i + 1 < len(line) and line[i + 1] == quote_char:
                    current.append(quote_char)
                    i += 2
                    continue
                in_quotes = False
            else:
                current.append(ch)
        else:
            if ch == quote_char:
                in_quotes = True
            elif ch == delimiter:
                fields.append("".join(current))
                current = []
            else:
                current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


def parse_csv(text, delimiter=','):
    lines = text.strip().split('\\n')
    if not lines:
        return []
    headers = parse_csv_line(lines[0], delimiter)
    rows = []
    for line in lines[1:]:
        values = parse_csv_line(line, delimiter)
        row = dict(zip(headers, values))
        rows.append(row)
    return rows
''',

    "str_pattern": '''\
def wildcard_match(pattern, text):
    m = len(pattern)
    n = len(text)
    dp = [[False] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = True
    for i in range(1, m + 1):
        if pattern[i - 1] == '*':
            dp[i][0] = dp[i - 1][0]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if pattern[i - 1] == '*':
                dp[i][j] = dp[i - 1][j] or dp[i][j - 1]
            elif pattern[i - 1] == '?' or pattern[i - 1] == text[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
    return dp[m][n]


def glob_match(pattern, text):
    parts = pattern.split('*')
    if len(parts) == 1:
        return pattern == text
    pos = 0
    if parts[0]:
        if not text.startswith(parts[0]):
            return False
        pos = len(parts[0])
    if parts[-1]:
        if not text.endswith(parts[-1]):
            return False
    for part in parts[1:-1]:
        idx = text.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    return True
''',

    "str_wrap": '''\
def wrap_text(text, width=72):
    words = text.split()
    if not words:
        return ""
    lines = []
    current_line = [words[0]]
    current_len = len(words[0])
    for word in words[1:]:
        if current_len + 1 + len(word) <= width:
            current_line.append(word)
            current_len += 1 + len(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_len = len(word)
    lines.append(" ".join(current_line))
    return "\\n".join(lines)


def center_text(text, width=72, fill=' '):
    lines = text.split('\\n')
    result = []
    for line in lines:
        padding = max(0, width - len(line))
        left_pad = padding // 2
        right_pad = padding - left_pad
        result.append(fill * left_pad + line + fill * right_pad)
    return "\\n".join(result)
''',

    "str_html_strip": '''\
def strip_html_tags(html):
    result = []
    in_tag = False
    i = 0
    while i < len(html):
        if html[i] == '<':
            in_tag = True
            i += 1
            continue
        if html[i] == '>':
            in_tag = False
            i += 1
            continue
        if not in_tag:
            result.append(html[i])
        i += 1
    return "".join(result)


def extract_links(html):
    links = []
    i = 0
    tag = 'href="'
    while i < len(html):
        pos = html.find(tag, i)
        if pos == -1:
            break
        start = pos + len(tag)
        end = html.find('"', start)
        if end == -1:
            break
        links.append(html[start:end])
        i = end + 1
    return links
''',

    "str_slug": '''\
def slugify(text):
    result = []
    prev_dash = False
    for ch in text.lower().strip():
        if ch.isalnum():
            result.append(ch)
            prev_dash = False
        elif ch in (' ', '-', '_', '.'):
            if not prev_dash and result:
                result.append('-')
                prev_dash = True
    while result and result[-1] == '-':
        result.pop()
    return "".join(result)


def unslugify(slug):
    words = slug.split('-')
    return " ".join(word.capitalize() for word in words if word)


def truncate_slug(slug, max_length=50):
    if len(slug) <= max_length:
        return slug
    truncated = slug[:max_length]
    last_dash = truncated.rfind('-')
    if last_dash > 0:
        truncated = truncated[:last_dash]
    return truncated
''',

    "str_case_convert": '''\
def to_camel_case(text):
    parts = text.replace('-', '_').split('_')
    if not parts:
        return ""
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def to_snake_case(text):
    result = []
    for i, ch in enumerate(text):
        if ch.isupper() and i > 0:
            if text[i - 1].islower() or (i + 1 < len(text) and text[i + 1].islower()):
                result.append('_')
        result.append(ch.lower())
    return "".join(result)


def to_pascal_case(text):
    parts = text.replace('-', '_').split('_')
    return "".join(p.capitalize() for p in parts if p)


def to_kebab_case(text):
    return to_snake_case(text).replace('_', '-')


def to_title_case(text):
    small_words = {'a', 'an', 'the', 'and', 'but', 'or', 'for', 'in', 'on'}
    words = text.split()
    result = []
    for i, word in enumerate(words):
        if i == 0 or word.lower() not in small_words:
            result.append(word.capitalize())
        else:
            result.append(word.lower())
    return " ".join(result)
''',

    "str_template": '''\
def render_template(template, context):
    result = []
    i = 0
    while i < len(template):
        if template[i:i+2] == '{{':
            end = template.find('}}', i + 2)
            if end == -1:
                result.append(template[i:])
                break
            key = template[i+2:end].strip()
            value = context.get(key, '')
            result.append(str(value))
            i = end + 2
        else:
            result.append(template[i])
            i += 1
    return "".join(result)


def render_with_filters(template, context, filters=None):
    if filters is None:
        filters = {}
    result = render_template(template, context)
    for name, func in filters.items():
        tag = '{|' + name + '|}'
        while tag in result:
            result = result.replace(tag, str(func(result)))
    return result
''',

    "str_diff": '''\
def compute_lcs(a, b):
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


def diff_lines(old_lines, new_lines):
    result = []
    m, n = len(old_lines), len(new_lines)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if old_lines[i - 1] == new_lines[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    i, j = m, n
    ops = []
    while i > 0 and j > 0:
        if old_lines[i - 1] == new_lines[j - 1]:
            ops.append((' ', old_lines[i - 1]))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            ops.append(('-', old_lines[i - 1]))
            i -= 1
        else:
            ops.append(('+', new_lines[j - 1]))
            j -= 1
    while i > 0:
        ops.append(('-', old_lines[i - 1]))
        i -= 1
    while j > 0:
        ops.append(('+', new_lines[j - 1]))
        j -= 1
    return list(reversed(ops))
''',

    "str_word_freq": '''\
def word_frequencies(text):
    words = text.lower().split()
    freq = {}
    for word in words:
        cleaned = ""
        for ch in word:
            if ch.isalnum():
                cleaned += ch
        if cleaned:
            freq[cleaned] = freq.get(cleaned, 0) + 1
    return freq


def top_n_words(text, n=10):
    freq = word_frequencies(text)
    pairs = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return pairs[:n]


def word_count(text):
    return len(text.split())


def unique_words(text):
    freq = word_frequencies(text)
    return sorted(freq.keys())


def hapax_legomena(text):
    freq = word_frequencies(text)
    return sorted(w for w, c in freq.items() if c == 1)
''',

    # ---- Category 4: Math / Numeric (10) -------------------------------------

    "math_matrix": '''\
def matrix_multiply(a, b):
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result


def matrix_transpose(m):
    rows = len(m)
    cols = len(m[0])
    result = [[0] * rows for _ in range(cols)]
    for i in range(rows):
        for j in range(cols):
            result[j][i] = m[i][j]
    return result


def matrix_add(a, b):
    rows = len(a)
    cols = len(a[0])
    result = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            result[i][j] = a[i][j] + b[i][j]
    return result
''',

    "math_stats": '''\
def mean(data):
    if not data:
        return 0.0
    return sum(data) / len(data)


def median(data):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_data[mid - 1] + sorted_data[mid]) / 2.0
    return sorted_data[mid]


def variance(data):
    if len(data) < 2:
        return 0.0
    m = mean(data)
    return sum((x - m) ** 2 for x in data) / (len(data) - 1)


def std_dev(data):
    return variance(data) ** 0.5


def percentile(data, p):
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
''',

    "math_polynomial": '''\
def poly_evaluate(coeffs, x):
    # Horner's method: coeffs[0]*x^n + coeffs[1]*x^(n-1) + ... + coeffs[n]
    result = 0
    for c in coeffs:
        result = result * x + c
    return result


def poly_add(a, b):
    max_len = max(len(a), len(b))
    pa = [0] * (max_len - len(a)) + list(a)
    pb = [0] * (max_len - len(b)) + list(b)
    return [pa[i] + pb[i] for i in range(max_len)]


def poly_multiply(a, b):
    result = [0] * (len(a) + len(b) - 1)
    for i in range(len(a)):
        for j in range(len(b)):
            result[i + j] += a[i] * b[j]
    return result


def poly_derivative(coeffs):
    n = len(coeffs) - 1
    if n <= 0:
        return [0]
    return [coeffs[i] * (n - i) for i in range(n)]
''',

    "math_primes": '''\
def sieve_of_eratosthenes(limit):
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit ** 0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
    return [i for i in range(limit + 1) if is_prime[i]]


def is_prime(n):
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


def prime_factorization(n):
    factors = []
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors.append(d)
            n //= d
        d += 1
    if n > 1:
        factors.append(n)
    return factors
''',

    "math_gcd": '''\
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def lcm(a, b):
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x1, y1 = extended_gcd(b % a, a)
    x = y1 - (b // a) * x1
    y = x1
    return g, x, y


def gcd_of_list(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result


def lcm_of_list(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = lcm(result, n)
    return result
''',

    "math_newton": '''\
def newton_sqrt(n, tolerance=1e-10):
    if n < 0:
        raise ValueError("Cannot compute square root of negative number")
    if n == 0:
        return 0.0
    guess = n / 2.0
    while True:
        new_guess = (guess + n / guess) / 2.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess


def newton_cbrt(n, tolerance=1e-10):
    if n == 0:
        return 0.0
    guess = n / 3.0
    while True:
        new_guess = (2 * guess + n / (guess * guess)) / 3.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess


def newton_root(f, f_prime, x0, tolerance=1e-10, max_iter=100):
    x = x0
    for _ in range(max_iter):
        fx = f(x)
        fpx = f_prime(x)
        if abs(fpx) < 1e-15:
            break
        x_new = x - fx / fpx
        if abs(x_new - x) < tolerance:
            return x_new
        x = x_new
    return x
''',

    "math_fibonacci": '''\
def fibonacci_memo(n, memo=None):
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci_memo(n - 1, memo) + fibonacci_memo(n - 2, memo)
    return memo[n]


def fibonacci_iterative(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def fibonacci_generator(limit):
    a, b = 0, 1
    while a <= limit:
        yield a
        a, b = b, a + b


def fibonacci_matrix(n):
    if n <= 1:
        return n
    mat = [[1, 1], [1, 0]]
    result = matrix_pow(mat, n - 1)
    return result[0][0]


def matrix_pow(m, p):
    result = [[1, 0], [0, 1]]
    while p > 0:
        if p % 2 == 1:
            result = mat_mult_2x2(result, m)
        m = mat_mult_2x2(m, m)
        p //= 2
    return result


def mat_mult_2x2(a, b):
    return [
        [a[0][0]*b[0][0]+a[0][1]*b[1][0], a[0][0]*b[0][1]+a[0][1]*b[1][1]],
        [a[1][0]*b[0][0]+a[1][1]*b[1][0], a[1][0]*b[0][1]+a[1][1]*b[1][1]],
    ]
''',

    "math_fraction": '''\
def frac_gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def frac_normalize(num, den):
    if den == 0:
        raise ValueError("Denominator cannot be zero")
    if den < 0:
        num, den = -num, -den
    g = frac_gcd(abs(num), den)
    return num // g, den // g


def frac_add(n1, d1, n2, d2):
    num = n1 * d2 + n2 * d1
    den = d1 * d2
    return frac_normalize(num, den)


def frac_subtract(n1, d1, n2, d2):
    return frac_add(n1, d1, -n2, d2)


def frac_multiply(n1, d1, n2, d2):
    num = n1 * n2
    den = d1 * d2
    return frac_normalize(num, den)


def frac_divide(n1, d1, n2, d2):
    if n2 == 0:
        raise ValueError("Cannot divide by zero fraction")
    return frac_multiply(n1, d1, d2, n2)
''',

    "math_combinatorics": '''\
def factorial(n):
    if n < 0:
        raise ValueError("Negative factorial undefined")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def combinations(n, r):
    if r < 0 or r > n:
        return 0
    if r == 0 or r == n:
        return 1
    r = min(r, n - r)
    result = 1
    for i in range(r):
        result = result * (n - i) // (i + 1)
    return result


def permutations(n, r):
    if r < 0 or r > n:
        return 0
    result = 1
    for i in range(n, n - r, -1):
        result *= i
    return result


def generate_permutations(items):
    if len(items) <= 1:
        return [list(items)]
    result = []
    for i, item in enumerate(items):
        rest = items[:i] + items[i + 1:]
        for perm in generate_permutations(rest):
            result.append([item] + perm)
    return result
''',

    "math_regression": '''\
def linear_regression(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-15:
        return 0.0, sum_y / n
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def r_squared(xs, ys):
    slope, intercept = linear_regression(xs, ys)
    y_mean = sum(ys) / len(ys)
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    if abs(ss_tot) < 1e-15:
        return 1.0
    return 1.0 - ss_res / ss_tot


def predict(xs, ys, x_new):
    slope, intercept = linear_regression(xs, ys)
    return slope * x_new + intercept
''',

    # ---- Category 5: Web / HTTP Patterns (10) --------------------------------

    "web_router": '''\
class Router:
    def __init__(self):
        self.routes = []
        self.not_found_handler = None

    def add_route(self, method, path, handler):
        parts = path.strip('/').split('/')
        self.routes.append((method.upper(), parts, handler))

    def match(self, method, path):
        parts = path.strip('/').split('/')
        for route_method, route_parts, handler in self.routes:
            if route_method != method.upper():
                continue
            params = self._match_parts(route_parts, parts)
            if params is not None:
                return handler, params
        return self.not_found_handler, {}

    def _match_parts(self, pattern, actual):
        if len(pattern) != len(actual):
            return None
        params = {}
        for p, a in zip(pattern, actual):
            if p.startswith(':'):
                params[p[1:]] = a
            elif p != a:
                return None
        return params

    def get(self, path, handler):
        self.add_route('GET', path, handler)

    def post(self, path, handler):
        self.add_route('POST', path, handler)
''',

    "web_request": '''\
class Request:
    def __init__(self, method, path, headers=None, body=None, query=None):
        self.method = method.upper()
        self.path = path
        self.headers = headers or {}
        self.body = body or ""
        self.query = query or {}

    def get_header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def is_json(self):
        ct = self.get_header('content-type', '')
        return 'application/json' in ct.lower()

    def content_length(self):
        cl = self.get_header('content-length', '0')
        try:
            return int(cl)
        except ValueError:
            return 0


class Response:
    def __init__(self, status=200, body="", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def set_header(self, name, value):
        self.headers[name] = value

    def json(self, data):
        import json
        self.body = json.dumps(data)
        self.set_header('Content-Type', 'application/json')
        return self

    def text(self, content):
        self.body = content
        self.set_header('Content-Type', 'text/plain')
        return self
''',

    "web_form_validate": '''\
def validate_email(email):
    if not email or '@' not in email:
        return False, "Invalid email format"
    parts = email.split('@')
    if len(parts) != 2:
        return False, "Multiple @ symbols"
    local, domain = parts
    if not local or not domain:
        return False, "Empty local or domain part"
    if '.' not in domain:
        return False, "Domain must contain a dot"
    return True, ""


def validate_password(password, min_length=8):
    errors = []
    if len(password) < min_length:
        errors.append("Too short")
    if not any(c.isupper() for c in password):
        errors.append("Needs uppercase")
    if not any(c.islower() for c in password):
        errors.append("Needs lowercase")
    if not any(c.isdigit() for c in password):
        errors.append("Needs digit")
    return len(errors) == 0, errors


def validate_form(fields, rules):
    errors = {}
    for field_name, field_rules in rules.items():
        value = fields.get(field_name, "")
        field_errors = []
        if "required" in field_rules and not value:
            field_errors.append("Field is required")
        if "min_length" in field_rules and len(str(value)) < field_rules["min_length"]:
            field_errors.append("Too short")
        if "max_length" in field_rules and len(str(value)) > field_rules["max_length"]:
            field_errors.append("Too long")
        if field_errors:
            errors[field_name] = field_errors
    return len(errors) == 0, errors
''',

    "web_middleware": '''\
class MiddlewareChain:
    def __init__(self):
        self.middlewares = []

    def use(self, middleware):
        self.middlewares.append(middleware)

    def execute(self, request, response):
        index = 0
        def next_middleware():
            nonlocal index
            if index < len(self.middlewares):
                mw = self.middlewares[index]
                index += 1
                mw(request, response, next_middleware)
        next_middleware()
        return response


def logging_middleware(request, response, next_fn):
    request.log = request.get("log", [])
    request.log.append("before")
    next_fn()
    request.log.append("after")


def auth_middleware(request, response, next_fn):
    token = request.get("headers", {}).get("authorization", "")
    if token.startswith("Bearer "):
        request["user"] = {"token": token[7:]}
        next_fn()
    else:
        response["status"] = 401
        response["body"] = "Unauthorized"


def cors_middleware(request, response, next_fn):
    response["headers"] = response.get("headers", {})
    response["headers"]["Access-Control-Allow-Origin"] = "*"
    response["headers"]["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE"
    next_fn()
''',

    "web_query_string": '''\
def parse_query_string(qs):
    if not qs:
        return {}
    if qs.startswith('?'):
        qs = qs[1:]
    params = {}
    for pair in qs.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)
            key = url_decode(key)
            value = url_decode(value)
            if key in params:
                if isinstance(params[key], list):
                    params[key].append(value)
                else:
                    params[key] = [params[key], value]
            else:
                params[key] = value
        elif pair:
            params[url_decode(pair)] = ""
    return params


def url_decode(s):
    result = []
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            hex_str = s[i+1:i+3]
            try:
                result.append(chr(int(hex_str, 16)))
                i += 3
                continue
            except ValueError:
                pass
        elif s[i] == '+':
            result.append(' ')
            i += 1
            continue
        result.append(s[i])
        i += 1
    return "".join(result)
''',

    "web_cookie": '''\
def parse_cookies(cookie_header):
    cookies = {}
    if not cookie_header:
        return cookies
    for pair in cookie_header.split(';'):
        pair = pair.strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            cookies[name.strip()] = value.strip()
    return cookies


def build_set_cookie(name, value, max_age=None, path='/', secure=False,
                     http_only=False, same_site=None):
    parts = [name + '=' + value]
    if max_age is not None:
        parts.append('Max-Age=' + str(max_age))
    if path:
        parts.append('Path=' + path)
    if secure:
        parts.append('Secure')
    if http_only:
        parts.append('HttpOnly')
    if same_site:
        parts.append('SameSite=' + same_site)
    return '; '.join(parts)


def parse_set_cookie(header):
    parts = header.split(';')
    name_value = parts[0].strip()
    if '=' not in name_value:
        return None
    name, value = name_value.split('=', 1)
    attrs = {}
    for part in parts[1:]:
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            attrs[k.strip().lower()] = v.strip()
        else:
            attrs[part.lower()] = True
    return {"name": name, "value": value, "attributes": attrs}
''',

    "web_rate_limit": '''\
import time as _time


class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = {}

    def allow(self, client_id):
        now = _time.time()
        self._cleanup(client_id, now)
        history = self.requests.get(client_id, [])
        if len(history) >= self.max_requests:
            return False
        history.append(now)
        self.requests[client_id] = history
        return True

    def _cleanup(self, client_id, now):
        if client_id in self.requests:
            cutoff = now - self.window
            self.requests[client_id] = [
                t for t in self.requests[client_id] if t > cutoff
            ]

    def remaining(self, client_id):
        now = _time.time()
        self._cleanup(client_id, now)
        used = len(self.requests.get(client_id, []))
        return max(0, self.max_requests - used)

    def reset(self, client_id):
        if client_id in self.requests:
            del self.requests[client_id]
''',

    "web_response": '''\
STATUS_MESSAGES = {
    200: "OK", 201: "Created", 204: "No Content",
    301: "Moved Permanently", 302: "Found", 304: "Not Modified",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed",
    500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
}


class ResponseBuilder:
    def __init__(self):
        self.status = 200
        self.headers = {}
        self.body = ""

    def set_status(self, code):
        self.status = code
        return self

    def set_header(self, name, value):
        self.headers[name] = value
        return self

    def set_body(self, content):
        self.body = content
        return self

    def json_body(self, data):
        import json
        self.body = json.dumps(data)
        self.headers["Content-Type"] = "application/json"
        return self

    def build(self):
        msg = STATUS_MESSAGES.get(self.status, "Unknown")
        header_lines = ["{}: {}".format(k, v) for k, v in self.headers.items()]
        return {
            "status": self.status,
            "status_message": msg,
            "headers": self.headers,
            "body": self.body,
            "header_text": "\\r\\n".join(header_lines),
        }
''',

    "web_auth": '''\
import hashlib
import secrets


class AuthManager:
    def __init__(self):
        self.users = {}
        self.sessions = {}

    def register(self, username, password):
        if username in self.users:
            return False, "Username taken"
        salt = secrets.token_hex(16)
        hashed = self._hash_password(password, salt)
        self.users[username] = {"hash": hashed, "salt": salt}
        return True, "Registered"

    def login(self, username, password):
        user = self.users.get(username)
        if not user:
            return None, "User not found"
        hashed = self._hash_password(password, user["salt"])
        if hashed != user["hash"]:
            return None, "Invalid password"
        token = secrets.token_hex(32)
        self.sessions[token] = username
        return token, "Logged in"

    def verify_token(self, token):
        return self.sessions.get(token)

    def logout(self, token):
        if token in self.sessions:
            del self.sessions[token]
            return True
        return False

    def _hash_password(self, password, salt):
        return hashlib.sha256((salt + password).encode()).hexdigest()
''',

    "web_cors": '''\
class CorsConfig:
    def __init__(self):
        self.allowed_origins = set()
        self.allowed_methods = {"GET", "POST", "OPTIONS"}
        self.allowed_headers = {"Content-Type", "Authorization"}
        self.max_age = 86400
        self.allow_credentials = False

    def allow_origin(self, origin):
        self.allowed_origins.add(origin)

    def allow_method(self, method):
        self.allowed_methods.add(method.upper())

    def is_origin_allowed(self, origin):
        if "*" in self.allowed_origins:
            return True
        return origin in self.allowed_origins

    def get_headers(self, origin):
        headers = {}
        if self.is_origin_allowed(origin):
            headers["Access-Control-Allow-Origin"] = origin
        else:
            return headers
        headers["Access-Control-Allow-Methods"] = ", ".join(sorted(self.allowed_methods))
        headers["Access-Control-Allow-Headers"] = ", ".join(sorted(self.allowed_headers))
        headers["Access-Control-Max-Age"] = str(self.max_age)
        if self.allow_credentials:
            headers["Access-Control-Allow-Credentials"] = "true"
        return headers

    def handle_preflight(self, origin, method):
        if not self.is_origin_allowed(origin):
            return 403, {}
        if method.upper() not in self.allowed_methods:
            return 405, {}
        return 204, self.get_headers(origin)
''',

    # ---- Category 6: Database / ORM Patterns (10) ----------------------------

    "db_model": '''\
class Field:
    def __init__(self, field_type, required=True, default=None):
        self.field_type = field_type
        self.required = required
        self.default = default

    def validate(self, value):
        if value is None:
            if self.required and self.default is None:
                return False, "Field is required"
            return True, ""
        if not isinstance(value, self.field_type):
            return False, "Expected type {}".format(self.field_type.__name__)
        return True, ""


class Model:
    _fields = {}

    def __init__(self, **kwargs):
        self._data = {}
        for name, field in self._fields.items():
            value = kwargs.get(name, field.default)
            self._data[name] = value

    def validate(self):
        errors = {}
        for name, field in self._fields.items():
            ok, msg = field.validate(self._data.get(name))
            if not ok:
                errors[name] = msg
        return len(errors) == 0, errors

    def to_dict(self):
        return dict(self._data)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        raise AttributeError("No field: {}".format(name))
''',

    "db_query_builder": '''\
class QueryBuilder:
    def __init__(self, table):
        self.table = table
        self._select = ["*"]
        self._where = []
        self._order = []
        self._limit = None
        self._offset = None
        self._joins = []
        self._params = []

    def select(self, *columns):
        self._select = list(columns)
        return self

    def where(self, condition, value=None):
        self._where.append(condition)
        if value is not None:
            self._params.append(value)
        return self

    def order_by(self, column, direction="ASC"):
        self._order.append("{} {}".format(column, direction))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def join(self, table, on):
        self._joins.append("JOIN {} ON {}".format(table, on))
        return self

    def build(self):
        parts = ["SELECT {} FROM {}".format(", ".join(self._select), self.table)]
        for j in self._joins:
            parts.append(j)
        if self._where:
            parts.append("WHERE " + " AND ".join(self._where))
        if self._order:
            parts.append("ORDER BY " + ", ".join(self._order))
        if self._limit is not None:
            parts.append("LIMIT {}".format(self._limit))
        if self._offset is not None:
            parts.append("OFFSET {}".format(self._offset))
        return " ".join(parts), self._params
''',

    "db_migration": '''\
class Migration:
    def __init__(self, version, description):
        self.version = version
        self.description = description

    def up(self):
        raise NotImplementedError

    def down(self):
        raise NotImplementedError


class MigrationRunner:
    def __init__(self):
        self.migrations = []
        self.applied = set()

    def register(self, migration):
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: m.version)

    def migrate_up(self, target=None):
        results = []
        for m in self.migrations:
            if m.version in self.applied:
                continue
            if target is not None and m.version > target:
                break
            m.up()
            self.applied.add(m.version)
            results.append(("up", m.version, m.description))
        return results

    def migrate_down(self, target=None):
        results = []
        for m in reversed(self.migrations):
            if m.version not in self.applied:
                continue
            if target is not None and m.version <= target:
                break
            m.down()
            self.applied.discard(m.version)
            results.append(("down", m.version, m.description))
        return results

    def current_version(self):
        if not self.applied:
            return 0
        return max(self.applied)
''',

    "db_validator": '''\
def validate_field(value, rules):
    errors = []
    for rule in rules:
        name = rule["type"]
        if name == "required":
            if value is None or value == "":
                errors.append("Field is required")
        elif name == "min_length":
            if isinstance(value, str) and len(value) < rule["value"]:
                errors.append("Minimum length is {}".format(rule["value"]))
        elif name == "max_length":
            if isinstance(value, str) and len(value) > rule["value"]:
                errors.append("Maximum length is {}".format(rule["value"]))
        elif name == "min_value":
            if isinstance(value, (int, float)) and value < rule["value"]:
                errors.append("Minimum value is {}".format(rule["value"]))
        elif name == "max_value":
            if isinstance(value, (int, float)) and value > rule["value"]:
                errors.append("Maximum value is {}".format(rule["value"]))
        elif name == "pattern":
            import re
            if isinstance(value, str) and not re.match(rule["value"], value):
                errors.append("Does not match pattern")
        elif name == "one_of":
            if value not in rule["value"]:
                errors.append("Must be one of: {}".format(rule["value"]))
    return errors


def validate_record(record, schema):
    all_errors = {}
    for field_name, rules in schema.items():
        value = record.get(field_name)
        errs = validate_field(value, rules)
        if errs:
            all_errors[field_name] = errs
    return len(all_errors) == 0, all_errors
''',

    "db_pool": '''\
import threading


class ConnectionPool:
    def __init__(self, factory, max_size=10):
        self.factory = factory
        self.max_size = max_size
        self.pool = []
        self.in_use = set()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            if self.pool:
                conn = self.pool.pop()
                self.in_use.add(id(conn))
                return conn
            if len(self.in_use) < self.max_size:
                conn = self.factory()
                self.in_use.add(id(conn))
                return conn
        return None

    def release(self, conn):
        with self.lock:
            conn_id = id(conn)
            if conn_id in self.in_use:
                self.in_use.discard(conn_id)
                self.pool.append(conn)

    def size(self):
        with self.lock:
            return len(self.pool)

    def active(self):
        with self.lock:
            return len(self.in_use)

    def close_all(self):
        with self.lock:
            self.pool.clear()
            self.in_use.clear()
''',

    "db_mapper": '''\
def map_row_to_dict(columns, row):
    return dict(zip(columns, row))


def map_rows_to_dicts(columns, rows):
    return [map_row_to_dict(columns, row) for row in rows]


def map_dict_to_object(data, cls):
    obj = cls.__new__(cls)
    for key, value in data.items():
        setattr(obj, key, value)
    return obj


def map_rows_to_objects(columns, rows, cls):
    return [map_dict_to_object(map_row_to_dict(columns, row), cls) for row in rows]


def dict_to_insert_sql(table, data):
    columns = list(data.keys())
    placeholders = ["?"] * len(columns)
    sql = "INSERT INTO {} ({}) VALUES ({})".format(
        table, ", ".join(columns), ", ".join(placeholders)
    )
    return sql, list(data.values())


def dict_to_update_sql(table, data, where_col, where_val):
    set_parts = ["{} = ?".format(k) for k in data.keys()]
    sql = "UPDATE {} SET {} WHERE {} = ?".format(
        table, ", ".join(set_parts), where_col
    )
    return sql, list(data.values()) + [where_val]
''',

    "db_schema": '''\
class Column:
    def __init__(self, name, col_type, primary_key=False, nullable=True,
                 default=None, unique=False):
        self.name = name
        self.col_type = col_type
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.unique = unique

    def to_sql(self):
        parts = [self.name, self.col_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable:
            parts.append("NOT NULL")
        if self.unique:
            parts.append("UNIQUE")
        if self.default is not None:
            parts.append("DEFAULT {}".format(repr(self.default)))
        return " ".join(parts)


class Table:
    def __init__(self, name):
        self.name = name
        self.columns = []

    def add_column(self, column):
        self.columns.append(column)

    def create_sql(self):
        col_defs = [c.to_sql() for c in self.columns]
        return "CREATE TABLE {} ({})".format(
            self.name, ", ".join(col_defs)
        )

    def drop_sql(self):
        return "DROP TABLE IF EXISTS {}".format(self.name)
''',

    "db_transaction": '''\
class Transaction:
    def __init__(self, connection):
        self.connection = connection
        self.operations = []
        self.committed = False
        self.rolled_back = False

    def execute(self, sql, params=None):
        if self.committed or self.rolled_back:
            raise RuntimeError("Transaction already finalized")
        self.operations.append((sql, params or []))
        return self

    def commit(self):
        if self.rolled_back:
            raise RuntimeError("Cannot commit rolled-back transaction")
        self.committed = True
        return self.operations

    def rollback(self):
        self.rolled_back = True
        self.operations.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        elif not self.committed and not self.rolled_back:
            self.commit()
        return False


def execute_in_transaction(connection, operations):
    txn = Transaction(connection)
    try:
        for sql, params in operations:
            txn.execute(sql, params)
        return txn.commit()
    except Exception:
        txn.rollback()
        raise
''',

    "db_cache": '''\
import time as _time


class QueryCache:
    def __init__(self, ttl_seconds=60, max_entries=100):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.cache = {}
        self.access_order = []

    def get(self, key):
        entry = self.cache.get(key)
        if entry is None:
            return None
        if _time.time() - entry["created"] > self.ttl:
            del self.cache[key]
            return None
        entry["hits"] += 1
        return entry["value"]

    def put(self, key, value):
        if len(self.cache) >= self.max_entries:
            self._evict()
        self.cache[key] = {
            "value": value,
            "created": _time.time(),
            "hits": 0,
        }
        self.access_order.append(key)

    def _evict(self):
        while self.access_order:
            oldest = self.access_order.pop(0)
            if oldest in self.cache:
                del self.cache[oldest]
                break

    def invalidate(self, key):
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        self.cache.clear()
        self.access_order.clear()

    def stats(self):
        total_hits = sum(e["hits"] for e in self.cache.values())
        return {"entries": len(self.cache), "total_hits": total_hits}
''',

    "db_seed": '''\
import random as _rnd


def generate_user(seed_id):
    first_names = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank",
                   "Grace", "Hank", "Ivy", "Jack"]
    last_names = ["Smith", "Jones", "Brown", "Wilson", "Taylor",
                  "Davis", "Clark", "Lewis", "Hall", "Young"]
    fn = first_names[seed_id % len(first_names)]
    ln = last_names[(seed_id * 7) % len(last_names)]
    return {
        "id": seed_id,
        "first_name": fn,
        "last_name": ln,
        "email": "{}.{}@example.com".format(fn.lower(), ln.lower()),
        "age": 20 + (seed_id * 13) % 50,
        "active": seed_id % 3 != 0,
    }


def generate_users(count, start_id=1):
    return [generate_user(i) for i in range(start_id, start_id + count)]


def generate_orders(users, orders_per_user=3):
    products = ["Widget", "Gadget", "Gizmo", "Doohickey", "Thingamajig"]
    orders = []
    oid = 1
    for user in users:
        for j in range(orders_per_user):
            orders.append({
                "id": oid,
                "user_id": user["id"],
                "product": products[oid % len(products)],
                "quantity": 1 + (oid * 3) % 10,
                "price": round(9.99 + (oid * 7.3) % 90, 2),
            })
            oid += 1
    return orders
''',

    # ---- Category 7: File / Config Processing (10) ---------------------------

    "file_csv_reader": '''\
def read_csv_file(filepath, delimiter=','):
    rows = []
    headers = None
    with open(filepath, 'r') as fh:
        for line_num, line in enumerate(fh):
            line = line.rstrip('\\n').rstrip('\\r')
            if not line:
                continue
            fields = _split_csv_line(line, delimiter)
            if headers is None:
                headers = fields
            else:
                row = {}
                for i, h in enumerate(headers):
                    row[h] = fields[i] if i < len(fields) else ""
                rows.append(row)
    return headers or [], rows


def _split_csv_line(line, delimiter):
    fields = []
    current = []
    in_quotes = False
    for ch in line:
        if in_quotes:
            if ch == '"':
                in_quotes = False
            else:
                current.append(ch)
        elif ch == '"':
            in_quotes = True
        elif ch == delimiter:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields
''',

    "file_json_transform": '''\
def flatten_json(obj, prefix="", sep="."):
    result = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_key = prefix + sep + key if prefix else key
            result.update(flatten_json(value, new_key, sep))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            new_key = prefix + sep + str(i) if prefix else str(i)
            result.update(flatten_json(value, new_key, sep))
    else:
        result[prefix] = obj
    return result


def unflatten_json(flat, sep="."):
    result = {}
    for key, value in flat.items():
        parts = key.split(sep)
        current = result
        for i, part in enumerate(parts[:-1]):
            next_part = parts[i + 1]
            if next_part.isdigit():
                current = current.setdefault(part, [])
                while len(current) <= int(next_part):
                    current.append({})
                current = current[int(next_part)]
            else:
                current = current.setdefault(part, {})
        current[parts[-1]] = value
    return result


def json_diff(a, b, path=""):
    diffs = []
    if type(a) != type(b):
        diffs.append({"path": path, "old": a, "new": b})
    elif isinstance(a, dict):
        all_keys = set(list(a.keys()) + list(b.keys()))
        for k in sorted(all_keys):
            p = path + "." + k if path else k
            if k not in a:
                diffs.append({"path": p, "old": None, "new": b[k]})
            elif k not in b:
                diffs.append({"path": p, "old": a[k], "new": None})
            else:
                diffs.extend(json_diff(a[k], b[k], p))
    elif a != b:
        diffs.append({"path": path, "old": a, "new": b})
    return diffs
''',

    "file_yaml_parse": '''\
def parse_simple_yaml(text):
    result = {}
    stack = [(result, -1)]
    for line in text.split('\\n'):
        stripped = line.rstrip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        content = stripped.lstrip()
        while len(stack) > 1 and indent <= stack[-1][1]:
            stack.pop()
        if ':' in content:
            key, _, value = content.partition(':')
            key = key.strip()
            value = value.strip()
            if value:
                value = _convert_yaml_value(value)
                stack[-1][0][key] = value
            else:
                new_dict = {}
                stack[-1][0][key] = new_dict
                stack.append((new_dict, indent))
        elif content.startswith('- '):
            item = content[2:].strip()
            parent = stack[-1][0]
            if isinstance(parent, dict):
                last_key = list(parent.keys())[-1] if parent else None
                if last_key and isinstance(parent[last_key], dict) and not parent[last_key]:
                    parent[last_key] = [_convert_yaml_value(item)]
                else:
                    parent[last_key] = parent.get(last_key, [])
                    if not isinstance(parent[last_key], list):
                        parent[last_key] = [parent[last_key]]
                    parent[last_key].append(_convert_yaml_value(item))
    return result


def _convert_yaml_value(s):
    if s.lower() in ('true', 'yes'):
        return True
    if s.lower() in ('false', 'no'):
        return False
    if s.lower() == 'null':
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s
''',

    "file_log_analyzer": '''\
def parse_log_line(line):
    parts = line.split(' ', 3)
    if len(parts) < 4:
        return None
    date_str = parts[0]
    time_str = parts[1]
    level = parts[2].strip('[]')
    message = parts[3] if len(parts) > 3 else ""
    return {
        "date": date_str,
        "time": time_str,
        "level": level,
        "message": message.strip(),
    }


def analyze_logs(lines):
    entries = []
    level_counts = {}
    for line in lines:
        entry = parse_log_line(line)
        if entry:
            entries.append(entry)
            lvl = entry["level"]
            level_counts[lvl] = level_counts.get(lvl, 0) + 1
    error_entries = [e for e in entries if e["level"] in ("ERROR", "CRITICAL")]
    return {
        "total": len(entries),
        "level_counts": level_counts,
        "error_count": len(error_entries),
        "errors": error_entries[:20],
    }


def filter_logs(entries, level=None, keyword=None):
    result = entries
    if level:
        result = [e for e in result if e["level"] == level.upper()]
    if keyword:
        kw = keyword.lower()
        result = [e for e in result if kw in e["message"].lower()]
    return result
''',

    "file_ini_parse": '''\
def parse_ini(text):
    sections = {}
    current_section = None
    for line in text.split('\\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current_section = line[1:-1].strip()
            sections[current_section] = {}
        elif '=' in line and current_section is not None:
            key, _, value = line.partition('=')
            sections[current_section][key.strip()] = _parse_ini_value(value.strip())
    return sections


def _parse_ini_value(value):
    if value.lower() in ('true', 'yes', 'on'):
        return True
    if value.lower() in ('false', 'no', 'off'):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def write_ini(sections):
    lines = []
    for section, values in sections.items():
        lines.append("[{}]".format(section))
        for key, value in values.items():
            lines.append("{} = {}".format(key, value))
        lines.append("")
    return "\\n".join(lines)
''',

    "file_watcher": '''\
import os as _os


class FileSnapshot:
    def __init__(self, directory):
        self.directory = directory
        self.state = {}

    def capture(self):
        self.state = {}
        for root, dirs, files in _os.walk(self.directory):
            for fname in files:
                path = _os.path.join(root, fname)
                try:
                    stat = _os.stat(path)
                    self.state[path] = {
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                except OSError:
                    pass
        return self.state

    def diff(self, other_state):
        added = []
        removed = []
        modified = []
        for path in other_state:
            if path not in self.state:
                added.append(path)
            elif other_state[path]["mtime"] != self.state[path]["mtime"]:
                modified.append(path)
        for path in self.state:
            if path not in other_state:
                removed.append(path)
        return {"added": added, "removed": removed, "modified": modified}

    def has_changes(self, other_state):
        d = self.diff(other_state)
        return bool(d["added"] or d["removed"] or d["modified"])
''',

    "file_path_resolver": '''\
def normalize_path(path):
    parts = []
    for segment in path.replace('\\\\', '/').split('/'):
        if segment == '' or segment == '.':
            continue
        if segment == '..':
            if parts:
                parts.pop()
        else:
            parts.append(segment)
    prefix = '/' if path.startswith('/') else ''
    return prefix + '/'.join(parts)


def join_paths(*paths):
    if not paths:
        return ''
    result = paths[0]
    for p in paths[1:]:
        if p.startswith('/'):
            result = p
        else:
            if not result.endswith('/'):
                result += '/'
            result += p
    return normalize_path(result)


def relative_path(base, target):
    base_parts = normalize_path(base).strip('/').split('/')
    target_parts = normalize_path(target).strip('/').split('/')
    common = 0
    for a, b in zip(base_parts, target_parts):
        if a != b:
            break
        common += 1
    ups = len(base_parts) - common
    downs = target_parts[common:]
    parts = ['..'] * ups + downs
    return '/'.join(parts) if parts else '.'


def split_extension(path):
    name = path.rsplit('/', 1)[-1]
    if '.' in name and not name.startswith('.'):
        base, ext = name.rsplit('.', 1)
        return base, '.' + ext
    return name, ''
''',

    "file_config_merge": '''\
def deep_merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        else:
            result[key] = value
    return result


def merge_configs(*configs):
    if not configs:
        return {}
    result = configs[0]
    for cfg in configs[1:]:
        result = deep_merge(result, cfg)
    return result


def resolve_references(config, root=None):
    if root is None:
        root = config
    if isinstance(config, dict):
        return {k: resolve_references(v, root) for k, v in config.items()}
    if isinstance(config, list):
        return [resolve_references(v, root) for v in config]
    if isinstance(config, str) and config.startswith('$'):
        ref_path = config[1:].split('.')
        current = root
        for part in ref_path:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return config
        return current
    return config
''',

    "file_line_counter": '''\
def count_lines(text):
    lines = text.split('\\n')
    total = len(lines)
    blank = 0
    comment = 0
    code = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
        elif stripped.startswith('#'):
            comment += 1
        else:
            code += 1
    return {"total": total, "blank": blank, "comment": comment, "code": code}


def count_file_lines(filepath):
    with open(filepath, 'r') as f:
        return count_lines(f.read())


def summarize_directory(file_counts):
    totals = {"total": 0, "blank": 0, "comment": 0, "code": 0}
    for path, counts in file_counts.items():
        for key in totals:
            totals[key] += counts.get(key, 0)
    return totals


def complexity_ratio(counts):
    code = counts.get("code", 0)
    comment = counts.get("comment", 0)
    if code == 0:
        return 0.0
    return comment / code


def code_density(counts):
    total = counts.get("total", 0)
    code = counts.get("code", 0)
    if total == 0:
        return 0.0
    return code / total
''',

    "file_env_loader": '''\
def parse_env_file(text):
    env = {}
    for line in text.split('\\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        env[key] = value
    return env


def interpolate_env(env):
    resolved = dict(env)
    max_passes = 10
    for _ in range(max_passes):
        changed = False
        for key, value in resolved.items():
            if not isinstance(value, str):
                continue
            new_value = value
            for var_name, var_value in resolved.items():
                placeholder = '${' + var_name + '}'
                if placeholder in new_value:
                    new_value = new_value.replace(placeholder, str(var_value))
            if new_value != value:
                resolved[key] = new_value
                changed = True
        if not changed:
            break
    return resolved


def get_env(env, key, default=None, cast=None):
    value = env.get(key, default)
    if value is not None and cast is not None:
        try:
            return cast(value)
        except (ValueError, TypeError):
            return default
    return value
''',

    # ---- Category 8: State Machines / Protocols (10) -------------------------

    "sm_tcp": '''\
TCP_TRANSITIONS = {
    "CLOSED":       {"open_passive": "LISTEN", "open_active": "SYN_SENT"},
    "LISTEN":       {"recv_syn": "SYN_RECEIVED", "close": "CLOSED"},
    "SYN_SENT":     {"recv_syn_ack": "ESTABLISHED", "recv_syn": "SYN_RECEIVED",
                     "close": "CLOSED"},
    "SYN_RECEIVED": {"recv_ack": "ESTABLISHED", "close": "FIN_WAIT_1"},
    "ESTABLISHED":  {"close": "FIN_WAIT_1", "recv_fin": "CLOSE_WAIT"},
    "FIN_WAIT_1":   {"recv_ack": "FIN_WAIT_2", "recv_fin": "CLOSING"},
    "FIN_WAIT_2":   {"recv_fin": "TIME_WAIT"},
    "CLOSING":      {"recv_ack": "TIME_WAIT"},
    "TIME_WAIT":    {"timeout": "CLOSED"},
    "CLOSE_WAIT":   {"close": "LAST_ACK"},
    "LAST_ACK":     {"recv_ack": "CLOSED"},
}


class TCPStateMachine:
    def __init__(self):
        self.state = "CLOSED"
        self.history = ["CLOSED"]

    def transition(self, event):
        transitions = TCP_TRANSITIONS.get(self.state, {})
        new_state = transitions.get(event)
        if new_state is None:
            return False
        self.state = new_state
        self.history.append(new_state)
        return True

    def is_connected(self):
        return self.state == "ESTABLISHED"

    def is_closed(self):
        return self.state == "CLOSED"

    def get_history(self):
        return list(self.history)
''',

    "sm_auth_flow": '''\
class AuthFlow:
    STATES = {"anonymous", "pending_verification", "authenticated",
              "locked", "expired"}

    def __init__(self):
        self.state = "anonymous"
        self.attempts = 0
        self.max_attempts = 3

    def login(self, valid_credentials):
        if self.state == "locked":
            return False, "Account locked"
        if valid_credentials:
            self.state = "pending_verification"
            self.attempts = 0
            return True, "Verification required"
        self.attempts += 1
        if self.attempts >= self.max_attempts:
            self.state = "locked"
            return False, "Account locked"
        return False, "Invalid credentials"

    def verify(self, code_valid):
        if self.state != "pending_verification":
            return False, "Not in verification state"
        if code_valid:
            self.state = "authenticated"
            return True, "Authenticated"
        return False, "Invalid code"

    def logout(self):
        if self.state == "authenticated":
            self.state = "anonymous"
            return True
        return False

    def unlock(self):
        if self.state == "locked":
            self.state = "anonymous"
            self.attempts = 0
            return True
        return False

    def expire(self):
        if self.state == "authenticated":
            self.state = "expired"
            return True
        return False
''',

    "sm_game": '''\
class GameState:
    def __init__(self):
        self.state = "menu"
        self.score = 0
        self.lives = 3
        self.level = 1

    def start(self):
        if self.state == "menu":
            self.state = "playing"
            self.score = 0
            self.lives = 3
            self.level = 1
            return True
        return False

    def pause(self):
        if self.state == "playing":
            self.state = "paused"
            return True
        return False

    def resume(self):
        if self.state == "paused":
            self.state = "playing"
            return True
        return False

    def lose_life(self):
        if self.state != "playing":
            return False
        self.lives -= 1
        if self.lives <= 0:
            self.state = "game_over"
        return True

    def complete_level(self):
        if self.state != "playing":
            return False
        self.score += self.level * 100
        self.level += 1
        return True

    def restart(self):
        if self.state == "game_over":
            self.state = "playing"
            self.score = 0
            self.lives = 3
            self.level = 1
            return True
        return False

    def quit(self):
        self.state = "menu"
        return True
''',

    "sm_parser": '''\
class JSONParser:
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def parse(self):
        self._skip_whitespace()
        value = self._parse_value()
        self._skip_whitespace()
        return value

    def _parse_value(self):
        ch = self.text[self.pos]
        if ch == '"':
            return self._parse_string()
        if ch in '-0123456789':
            return self._parse_number()
        if ch == '{':
            return self._parse_object()
        if ch == '[':
            return self._parse_array()
        if self.text[self.pos:self.pos+4] == 'true':
            self.pos += 4
            return True
        if self.text[self.pos:self.pos+5] == 'false':
            self.pos += 5
            return False
        if self.text[self.pos:self.pos+4] == 'null':
            self.pos += 4
            return None
        raise ValueError("Unexpected character at pos {}".format(self.pos))

    def _parse_string(self):
        self.pos += 1
        result = []
        while self.pos < len(self.text) and self.text[self.pos] != '"':
            result.append(self.text[self.pos])
            self.pos += 1
        self.pos += 1
        return "".join(result)

    def _parse_number(self):
        start = self.pos
        if self.text[self.pos] == '-':
            self.pos += 1
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos < len(self.text) and self.text[self.pos] == '.':
            self.pos += 1
            while self.pos < len(self.text) and self.text[self.pos].isdigit():
                self.pos += 1
            return float(self.text[start:self.pos])
        return int(self.text[start:self.pos])

    def _parse_object(self):
        self.pos += 1
        obj = {}
        self._skip_whitespace()
        if self.text[self.pos] == '}':
            self.pos += 1
            return obj
        while True:
            self._skip_whitespace()
            key = self._parse_string()
            self._skip_whitespace()
            self.pos += 1
            self._skip_whitespace()
            value = self._parse_value()
            obj[key] = value
            self._skip_whitespace()
            if self.text[self.pos] == '}':
                self.pos += 1
                return obj
            self.pos += 1

    def _parse_array(self):
        self.pos += 1
        arr = []
        self._skip_whitespace()
        if self.text[self.pos] == ']':
            self.pos += 1
            return arr
        while True:
            self._skip_whitespace()
            arr.append(self._parse_value())
            self._skip_whitespace()
            if self.text[self.pos] == ']':
                self.pos += 1
                return arr
            self.pos += 1

    def _skip_whitespace(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \\t\\n\\r':
            self.pos += 1
''',

    "sm_order": '''\
class OrderProcessor:
    VALID_TRANSITIONS = {
        "created":    {"submit": "submitted"},
        "submitted":  {"pay": "paid", "cancel": "cancelled"},
        "paid":       {"ship": "shipped", "refund": "refunded"},
        "shipped":    {"deliver": "delivered", "return_req": "return_requested"},
        "delivered":  {"return_req": "return_requested", "complete": "completed"},
        "return_requested": {"approve_return": "returned", "deny_return": "delivered"},
        "returned":   {"refund": "refunded"},
    }

    def __init__(self, order_id):
        self.order_id = order_id
        self.state = "created"
        self.history = [("created", None)]

    def apply(self, action):
        transitions = self.VALID_TRANSITIONS.get(self.state, {})
        new_state = transitions.get(action)
        if new_state is None:
            return False, "Invalid action {} in state {}".format(action, self.state)
        self.state = new_state
        self.history.append((new_state, action))
        return True, "Transitioned to {}".format(new_state)

    def can_cancel(self):
        return self.state in ("created", "submitted")

    def is_terminal(self):
        return self.state in ("completed", "refunded", "cancelled")

    def get_available_actions(self):
        return list(self.VALID_TRANSITIONS.get(self.state, {}).keys())
''',

    "sm_traffic": '''\
class TrafficLight:
    SEQUENCE = ["red", "green", "yellow"]
    DURATIONS = {"red": 30, "green": 25, "yellow": 5}

    def __init__(self):
        self.state = "red"
        self.elapsed = 0
        self.cycle_count = 0

    def tick(self, seconds=1):
        self.elapsed += seconds
        if self.elapsed >= self.DURATIONS[self.state]:
            self._advance()
            return True
        return False

    def _advance(self):
        idx = self.SEQUENCE.index(self.state)
        next_idx = (idx + 1) % len(self.SEQUENCE)
        self.state = self.SEQUENCE[next_idx]
        self.elapsed = 0
        if self.state == "red":
            self.cycle_count += 1

    def is_safe_to_go(self):
        return self.state == "green"

    def time_remaining(self):
        return self.DURATIONS[self.state] - self.elapsed

    def override(self, new_state):
        if new_state in self.SEQUENCE:
            self.state = new_state
            self.elapsed = 0
            return True
        return False

    def get_status(self):
        return {
            "state": self.state,
            "elapsed": self.elapsed,
            "remaining": self.time_remaining(),
            "cycles": self.cycle_count,
        }
''',

    "sm_elevator": '''\
class Elevator:
    def __init__(self, min_floor=1, max_floor=10):
        self.min_floor = min_floor
        self.max_floor = max_floor
        self.current_floor = 1
        self.direction = "idle"
        self.requests = set()
        self.door_open = False

    def request(self, floor):
        if self.min_floor <= floor <= self.max_floor:
            self.requests.add(floor)
            return True
        return False

    def step(self):
        if self.door_open:
            self.door_open = False
        if self.current_floor in self.requests:
            self.requests.discard(self.current_floor)
            self.door_open = True
            return "stop"
        if not self.requests:
            self.direction = "idle"
            return "idle"
        if self.direction == "up" or self.direction == "idle":
            above = [f for f in self.requests if f > self.current_floor]
            if above:
                self.direction = "up"
                self.current_floor += 1
                return "moving_up"
        if self.direction == "down" or self.direction == "idle":
            below = [f for f in self.requests if f < self.current_floor]
            if below:
                self.direction = "down"
                self.current_floor -= 1
                return "moving_down"
        above = [f for f in self.requests if f > self.current_floor]
        if above:
            self.direction = "up"
            self.current_floor += 1
            return "moving_up"
        below = [f for f in self.requests if f < self.current_floor]
        if below:
            self.direction = "down"
            self.current_floor -= 1
            return "moving_down"
        return "idle"

    def status(self):
        return {
            "floor": self.current_floor,
            "direction": self.direction,
            "door": "open" if self.door_open else "closed",
            "pending": sorted(self.requests),
        }
''',

    "sm_vending": '''\
class VendingMachine:
    def __init__(self, inventory=None):
        self.state = "idle"
        self.balance = 0
        self.inventory = inventory or {}

    def insert_coin(self, amount):
        if self.state == "idle":
            self.state = "accepting"
        if self.state != "accepting":
            return False, "Cannot insert coins now"
        if amount not in (5, 10, 25, 50, 100):
            return False, "Invalid coin"
        self.balance += amount
        return True, "Balance: {}".format(self.balance)

    def select_item(self, item_code):
        if self.state != "accepting":
            return False, "Insert coins first"
        item = self.inventory.get(item_code)
        if not item:
            return False, "Invalid item"
        if item["quantity"] <= 0:
            return False, "Out of stock"
        if self.balance < item["price"]:
            return False, "Insufficient funds"
        change = self.balance - item["price"]
        self.inventory[item_code]["quantity"] -= 1
        self.balance = 0
        self.state = "idle"
        return True, "Dispensed {}. Change: {}".format(item["name"], change)

    def cancel(self):
        refund = self.balance
        self.balance = 0
        self.state = "idle"
        return refund

    def get_menu(self):
        return {code: {"name": item["name"], "price": item["price"],
                       "available": item["quantity"] > 0}
                for code, item in self.inventory.items()}
''',

    "sm_turnstile": '''\
class Turnstile:
    def __init__(self):
        self.state = "locked"
        self.entry_count = 0
        self.coin_count = 0
        self.rejected = 0

    def coin(self):
        self.coin_count += 1
        if self.state == "locked":
            self.state = "unlocked"
            return "unlocked"
        return "already_unlocked"

    def push(self):
        if self.state == "unlocked":
            self.state = "locked"
            self.entry_count += 1
            return "entered"
        self.rejected += 1
        return "blocked"

    def reset(self):
        self.state = "locked"
        self.entry_count = 0
        self.coin_count = 0
        self.rejected = 0

    def stats(self):
        return {
            "state": self.state,
            "entries": self.entry_count,
            "coins": self.coin_count,
            "rejected": self.rejected,
        }

    def revenue(self, price_per_entry=100):
        return self.coin_count * price_per_entry
''',

    "sm_connection": '''\
class ConnectionManager:
    def __init__(self, max_retries=3, timeout=30):
        self.state = "disconnected"
        self.retries = 0
        self.max_retries = max_retries
        self.timeout = timeout
        self.elapsed = 0

    def connect(self):
        if self.state == "connected":
            return True, "Already connected"
        self.state = "connecting"
        self.elapsed = 0
        return True, "Connecting"

    def on_success(self):
        if self.state == "connecting":
            self.state = "connected"
            self.retries = 0
            return True
        return False

    def on_failure(self):
        if self.state != "connecting":
            return False
        self.retries += 1
        if self.retries >= self.max_retries:
            self.state = "failed"
            return False
        self.state = "reconnecting"
        return True

    def retry(self):
        if self.state == "reconnecting":
            self.state = "connecting"
            return True
        return False

    def disconnect(self):
        if self.state in ("connected", "connecting", "reconnecting"):
            self.state = "disconnected"
            self.retries = 0
            return True
        return False

    def tick(self, seconds=1):
        if self.state == "connecting":
            self.elapsed += seconds
            if self.elapsed >= self.timeout:
                self.on_failure()
                return "timeout"
        return self.state
''',

    # ---- Category 9: Error Handling Patterns (10) ----------------------------

    "err_retry": '''\
import time as _time


class RetryPolicy:
    def __init__(self, max_retries=3, base_delay=1.0, backoff_factor=2.0,
                 max_delay=60.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay

    def get_delay(self, attempt):
        delay = self.base_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)

    def execute(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    _time.sleep(self.get_delay(attempt))
        raise last_error


def retry(max_retries=3, delay=1.0):
    def decorator(func):
        def wrapper(*args, **kwargs):
            policy = RetryPolicy(max_retries=max_retries, base_delay=delay)
            return policy.execute(func, *args, **kwargs)
        return wrapper
    return decorator
''',

    "err_circuit_breaker": '''\
import time as _time


class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = 0

    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if _time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = "half_open"
            else:
                raise RuntimeError("Circuit breaker is open")
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = _time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def reset(self):
        self.failure_count = 0
        self.state = "closed"

    def get_state(self):
        return {
            "state": self.state,
            "failures": self.failure_count,
            "threshold": self.failure_threshold,
        }
''',

    "err_fallback": '''\
class FallbackChain:
    def __init__(self):
        self.handlers = []
        self.default = None

    def add(self, handler, name=None):
        self.handlers.append({"handler": handler, "name": name or str(len(self.handlers))})

    def set_default(self, default_value):
        self.default = default_value

    def execute(self, *args, **kwargs):
        errors = []
        for entry in self.handlers:
            try:
                return entry["handler"](*args, **kwargs)
            except Exception as e:
                errors.append({"handler": entry["name"], "error": str(e)})
        if self.default is not None:
            return self.default
        raise RuntimeError("All handlers failed: {}".format(errors))


def with_fallback(*funcs):
    def wrapper(*args, **kwargs):
        chain = FallbackChain()
        for f in funcs:
            chain.add(f, name=f.__name__ if hasattr(f, '__name__') else str(f))
        return chain.execute(*args, **kwargs)
    return wrapper


def fallback_value(func, default, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception:
        return default
''',

    "err_validator": '''\
class ValidationError:
    def __init__(self, field, message, code=None):
        self.field = field
        self.message = message
        self.code = code or "invalid"


class Validator:
    def __init__(self):
        self.errors = []

    def require(self, field, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            self.errors.append(ValidationError(field, "is required", "required"))
        return self

    def min_length(self, field, value, length):
        if isinstance(value, str) and len(value) < length:
            self.errors.append(ValidationError(field, "too short", "min_length"))
        return self

    def max_length(self, field, value, length):
        if isinstance(value, str) and len(value) > length:
            self.errors.append(ValidationError(field, "too long", "max_length"))
        return self

    def numeric_range(self, field, value, min_val=None, max_val=None):
        if not isinstance(value, (int, float)):
            self.errors.append(ValidationError(field, "not a number", "type"))
            return self
        if min_val is not None and value < min_val:
            self.errors.append(ValidationError(field, "too small", "min_value"))
        if max_val is not None and value > max_val:
            self.errors.append(ValidationError(field, "too large", "max_value"))
        return self

    def is_valid(self):
        return len(self.errors) == 0

    def get_errors(self):
        return {e.field: e.message for e in self.errors}
''',

    "err_aggregator": '''\
class ErrorAggregator:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def add_error(self, message, source=None, code=None):
        self.errors.append({
            "message": message,
            "source": source,
            "code": code,
            "severity": "error",
        })

    def add_warning(self, message, source=None):
        self.warnings.append({
            "message": message,
            "source": source,
            "severity": "warning",
        })

    def has_errors(self):
        return len(self.errors) > 0

    def has_warnings(self):
        return len(self.warnings) > 0

    def merge(self, other):
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def summary(self):
        return {
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "has_errors": self.has_errors(),
        }

    def format_report(self):
        lines = []
        for e in self.errors:
            prefix = "[{}] ".format(e["source"]) if e["source"] else ""
            lines.append("ERROR: {}{}".format(prefix, e["message"]))
        for w in self.warnings:
            prefix = "[{}] ".format(w["source"]) if w["source"] else ""
            lines.append("WARN:  {}{}".format(prefix, w["message"]))
        return "\\n".join(lines)
''',

    "err_timeout": '''\
import time as _time
import threading


class TimeoutError(Exception):
    pass


def run_with_timeout(func, timeout, *args, **kwargs):
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("Function timed out after {} seconds".format(timeout))
    if error[0]:
        raise error[0]
    return result[0]


class DeadlineTracker:
    def __init__(self, deadline_seconds):
        self.deadline = _time.time() + deadline_seconds

    def remaining(self):
        return max(0, self.deadline - _time.time())

    def is_expired(self):
        return _time.time() >= self.deadline

    def check(self):
        if self.is_expired():
            raise TimeoutError("Deadline exceeded")
        return self.remaining()
''',

    "err_cleanup": '''\
class ResourceManager:
    def __init__(self):
        self.resources = []

    def acquire(self, resource, cleanup_fn):
        self.resources.append((resource, cleanup_fn))
        return resource

    def release_all(self):
        errors = []
        while self.resources:
            resource, cleanup_fn = self.resources.pop()
            try:
                cleanup_fn(resource)
            except Exception as e:
                errors.append(str(e))
        return errors

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release_all()
        return False


def ensure_cleanup(setup_fn, work_fn, cleanup_fn):
    resource = setup_fn()
    try:
        return work_fn(resource)
    finally:
        cleanup_fn(resource)


class TempFile:
    def __init__(self, path, content=""):
        self.path = path
        self.content = content

    def __enter__(self):
        with open(self.path, 'w') as f:
            f.write(self.content)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        import os
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False
''',

    "err_null_object": '''\
class NullLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class RealLogger:
    def __init__(self):
        self.messages = []

    def debug(self, msg):
        self.messages.append(("DEBUG", msg))

    def info(self, msg):
        self.messages.append(("INFO", msg))

    def warning(self, msg):
        self.messages.append(("WARNING", msg))

    def error(self, msg):
        self.messages.append(("ERROR", msg))

    def get_messages(self, level=None):
        if level is None:
            return list(self.messages)
        return [(l, m) for l, m in self.messages if l == level]

    def clear(self):
        self.messages.clear()


def get_logger(enabled=True):
    if enabled:
        return RealLogger()
    return NullLogger()
''',

    "err_result": '''\
class Ok:
    def __init__(self, value):
        self.value = value
        self.is_ok = True
        self.is_err = False

    def unwrap(self):
        return self.value

    def unwrap_or(self, default):
        return self.value

    def map(self, func):
        return Ok(func(self.value))

    def flat_map(self, func):
        return func(self.value)


class Err:
    def __init__(self, error):
        self.error = error
        self.is_ok = False
        self.is_err = True

    def unwrap(self):
        raise RuntimeError("Called unwrap on Err: {}".format(self.error))

    def unwrap_or(self, default):
        return default

    def map(self, func):
        return self

    def flat_map(self, func):
        return self


def safe_divide(a, b):
    if b == 0:
        return Err("Division by zero")
    return Ok(a / b)


def safe_index(lst, idx):
    if idx < 0 or idx >= len(lst):
        return Err("Index out of range")
    return Ok(lst[idx])


def collect_results(results):
    values = []
    for r in results:
        if r.is_err:
            return Err(r.error)
        values.append(r.value)
    return Ok(values)
''',

    "err_boundary": '''\
class ErrorBoundary:
    def __init__(self, fallback_value=None, on_error=None):
        self.fallback_value = fallback_value
        self.on_error = on_error
        self.last_error = None
        self.error_count = 0

    def run(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            self.last_error = e
            self.error_count += 1
            if self.on_error:
                self.on_error(e)
            return self.fallback_value

    def reset(self):
        self.last_error = None
        self.error_count = 0


class RecoveryStrategy:
    def __init__(self):
        self.strategies = {}

    def register(self, exception_type, handler):
        self.strategies[exception_type] = handler

    def handle(self, error):
        for exc_type, handler in self.strategies.items():
            if isinstance(error, exc_type):
                return handler(error)
        raise error


def safe_execute(func, recovery, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        return recovery.handle(e)
''',

    # ---- Category 10: Scientific / Engineering (10) --------------------------

    "sci_moving_avg": '''\
def simple_moving_average(data, window):
    if window <= 0 or window > len(data):
        return []
    result = []
    window_sum = sum(data[:window])
    result.append(window_sum / window)
    for i in range(window, len(data)):
        window_sum += data[i] - data[i - window]
        result.append(window_sum / window)
    return result


def exponential_moving_average(data, alpha=0.3):
    if not data:
        return []
    result = [data[0]]
    for i in range(1, len(data)):
        ema = alpha * data[i] + (1 - alpha) * result[-1]
        result.append(ema)
    return result


def weighted_moving_average(data, weights):
    if not data or not weights:
        return []
    w = len(weights)
    total_weight = sum(weights)
    result = []
    for i in range(w - 1, len(data)):
        val = sum(data[i - w + 1 + j] * weights[j] for j in range(w))
        result.append(val / total_weight)
    return result
''',

    "sci_interpolation": '''\
def linear_interpolate(x0, y0, x1, y1, x):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def piecewise_linear(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            return linear_interpolate(xs[i], ys[i], xs[i + 1], ys[i + 1], x)
    return ys[-1]


def lagrange_interpolation(xs, ys, x):
    n = len(xs)
    result = 0.0
    for i in range(n):
        term = ys[i]
        for j in range(n):
            if i != j:
                term *= (x - xs[j]) / (xs[i] - xs[j])
        result += term
    return result


def interpolate_table(xs, ys, query_points):
    return [piecewise_linear(xs, ys, q) for q in query_points]
''',

    "sci_gradient": '''\
def gradient_descent(f, grad_f, x0, learning_rate=0.01, max_iter=1000,
                     tolerance=1e-8):
    x = list(x0)
    n = len(x)
    history = [list(x)]
    for iteration in range(max_iter):
        g = grad_f(x)
        new_x = [x[i] - learning_rate * g[i] for i in range(n)]
        diff = sum((new_x[i] - x[i]) ** 2 for i in range(n)) ** 0.5
        x = new_x
        history.append(list(x))
        if diff < tolerance:
            break
    return x, f(x), history


def numerical_gradient(f, x, epsilon=1e-7):
    grad = []
    for i in range(len(x)):
        x_plus = list(x)
        x_minus = list(x)
        x_plus[i] += epsilon
        x_minus[i] -= epsilon
        grad.append((f(x_plus) - f(x_minus)) / (2 * epsilon))
    return grad


def minimize_1d(f, a, b, tolerance=1e-6):
    golden = (5 ** 0.5 - 1) / 2
    while abs(b - a) > tolerance:
        x1 = b - golden * (b - a)
        x2 = a + golden * (b - a)
        if f(x1) < f(x2):
            b = x2
        else:
            a = x1
    return (a + b) / 2
''',

    "sci_euler": '''\
def euler_method(f, y0, t0, t_end, h):
    t = t0
    y = y0
    ts = [t]
    ys = [y]
    while t < t_end:
        y = y + h * f(t, y)
        t = t + h
        ts.append(t)
        ys.append(y)
    return ts, ys


def euler_system(f_vec, y0_vec, t0, t_end, h):
    t = t0
    y = list(y0_vec)
    n = len(y)
    ts = [t]
    ys = [list(y)]
    while t < t_end:
        dydt = f_vec(t, y)
        y = [y[i] + h * dydt[i] for i in range(n)]
        t = t + h
        ts.append(t)
        ys.append(list(y))
    return ts, ys


def improved_euler(f, y0, t0, t_end, h):
    t = t0
    y = y0
    ts = [t]
    ys = [y]
    while t < t_end:
        k1 = f(t, y)
        k2 = f(t + h, y + h * k1)
        y = y + h * (k1 + k2) / 2.0
        t = t + h
        ts.append(t)
        ys.append(y)
    return ts, ys
''',

    "sci_fft": '''\
import cmath


def fft(x):
    n = len(x)
    if n <= 1:
        return x
    even = fft(x[0::2])
    odd = fft(x[1::2])
    T = [cmath.exp(-2j * cmath.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + T[k] for k in range(n // 2)] + \
           [even[k] - T[k] for k in range(n // 2)]


def ifft(x):
    n = len(x)
    conjugated = [v.conjugate() for v in x]
    result = fft(conjugated)
    return [v.conjugate() / n for v in result]


def power_spectrum(signal):
    spectrum = fft(signal)
    return [abs(s) ** 2 for s in spectrum]


def dominant_frequency(signal, sample_rate):
    spectrum = power_spectrum(signal)
    n = len(spectrum) // 2
    half_spectrum = spectrum[:n]
    max_idx = max(range(n), key=lambda i: half_spectrum[i])
    freq = max_idx * sample_rate / len(signal)
    return freq, half_spectrum[max_idx]
''',

    "sci_histogram": '''\
def compute_histogram(data, num_bins=10):
    if not data:
        return [], []
    min_val = min(data)
    max_val = max(data)
    if min_val == max_val:
        return [min_val], [len(data)]
    bin_width = (max_val - min_val) / num_bins
    edges = [min_val + i * bin_width for i in range(num_bins + 1)]
    counts = [0] * num_bins
    for val in data:
        idx = int((val - min_val) / bin_width)
        if idx == num_bins:
            idx = num_bins - 1
        counts[idx] += 1
    return edges, counts


def normalize_histogram(counts):
    total = sum(counts)
    if total == 0:
        return counts
    return [c / total for c in counts]


def cumulative_histogram(counts):
    result = []
    running = 0
    for c in counts:
        running += c
        result.append(running)
    return result


def histogram_to_ascii(counts, width=40):
    max_count = max(counts) if counts else 0
    lines = []
    for i, c in enumerate(counts):
        bar_len = int(c / max_count * width) if max_count > 0 else 0
        lines.append("{:3d} | {}".format(i, '#' * bar_len))
    return "\\n".join(lines)
''',

    "sci_convolution": '''\
def convolve_1d(signal, kernel):
    n = len(signal)
    k = len(kernel)
    output_len = n + k - 1
    result = [0.0] * output_len
    for i in range(n):
        for j in range(k):
            result[i + j] += signal[i] * kernel[j]
    return result


def convolve_same(signal, kernel):
    full = convolve_1d(signal, kernel)
    k = len(kernel)
    start = (k - 1) // 2
    return full[start:start + len(signal)]


def correlate_1d(signal, pattern):
    n = len(signal)
    k = len(pattern)
    result = []
    for i in range(n - k + 1):
        val = sum(signal[i + j] * pattern[j] for j in range(k))
        result.append(val)
    return result


def apply_filter(signal, kernel):
    result = convolve_same(signal, kernel)
    norm = sum(kernel)
    if abs(norm) > 1e-10:
        result = [r / norm for r in result]
    return result
''',

    "sci_rk4": '''\
def rk4_step(f, t, y, h):
    k1 = f(t, y)
    k2 = f(t + h/2, y + h/2 * k1)
    k3 = f(t + h/2, y + h/2 * k2)
    k4 = f(t + h, y + h * k3)
    return y + (h / 6) * (k1 + 2*k2 + 2*k3 + k4)


def rk4_solve(f, y0, t0, t_end, h):
    t = t0
    y = y0
    ts = [t]
    ys = [y]
    while t < t_end - 1e-12:
        y = rk4_step(f, t, y, h)
        t += h
        ts.append(t)
        ys.append(y)
    return ts, ys


def rk4_system(f_vec, y0_vec, t0, t_end, h):
    n = len(y0_vec)
    t = t0
    y = list(y0_vec)
    ts = [t]
    ys = [list(y)]
    while t < t_end - 1e-12:
        k1 = f_vec(t, y)
        k2 = f_vec(t + h/2, [y[i] + h/2 * k1[i] for i in range(n)])
        k3 = f_vec(t + h/2, [y[i] + h/2 * k2[i] for i in range(n)])
        k4 = f_vec(t + h, [y[i] + h * k3[i] for i in range(n)])
        y = [y[i] + (h/6) * (k1[i] + 2*k2[i] + 2*k3[i] + k4[i])
             for i in range(n)]
        t += h
        ts.append(t)
        ys.append(list(y))
    return ts, ys
''',

    "sci_pca": '''\
def compute_mean_vector(data):
    n = len(data)
    d = len(data[0])
    mean = [0.0] * d
    for row in data:
        for j in range(d):
            mean[j] += row[j]
    return [m / n for m in mean]


def compute_covariance(data, mean_vec):
    n = len(data)
    d = len(mean_vec)
    cov = [[0.0] * d for _ in range(d)]
    for row in data:
        centered = [row[j] - mean_vec[j] for j in range(d)]
        for i in range(d):
            for j in range(d):
                cov[i][j] += centered[i] * centered[j]
    for i in range(d):
        for j in range(d):
            cov[i][j] /= (n - 1)
    return cov


def power_iteration(matrix, num_iterations=100):
    n = len(matrix)
    vec = [1.0 / n ** 0.5] * n
    for _ in range(num_iterations):
        new_vec = [sum(matrix[i][j] * vec[j] for j in range(n)) for i in range(n)]
        norm = sum(v ** 2 for v in new_vec) ** 0.5
        vec = [v / norm for v in new_vec]
    eigenvalue = sum(
        sum(matrix[i][j] * vec[j] for j in range(n)) * vec[i]
        for i in range(n)
    )
    return eigenvalue, vec


def project_data(data, mean_vec, principal_components):
    projected = []
    for row in data:
        centered = [row[j] - mean_vec[j] for j in range(len(mean_vec))]
        coords = [sum(centered[j] * pc[j] for j in range(len(centered)))
                   for pc in principal_components]
        projected.append(coords)
    return projected
''',

    "sci_monte_carlo": '''\
import random as _rnd


def estimate_pi(num_samples):
    inside = 0
    for _ in range(num_samples):
        x = _rnd.random()
        y = _rnd.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return 4.0 * inside / num_samples


def monte_carlo_integrate(f, a, b, num_samples=10000):
    total = 0.0
    for _ in range(num_samples):
        x = a + (b - a) * _rnd.random()
        total += f(x)
    return (b - a) * total / num_samples


def bootstrap_mean(data, num_bootstrap=1000):
    n = len(data)
    means = []
    for _ in range(num_bootstrap):
        sample = [data[_rnd.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return {
        "mean": sum(means) / len(means),
        "ci_lower": means[int(0.025 * len(means))],
        "ci_upper": means[int(0.975 * len(means))],
    }


def random_walk_1d(steps, step_size=1.0):
    position = 0.0
    positions = [position]
    for _ in range(steps):
        direction = 1 if _rnd.random() > 0.5 else -1
        position += direction * step_size
        positions.append(position)
    return positions
''',
}


# ==============================================================================
# BUGGY PROGRAMS -- 30 programs with known bugs for bug-detection testing
# Each bug is a realistic mistake a developer might make.
# ==============================================================================

BUGGY_PROGRAMS = [
    # Bug type: division by zero
    {"id": "bug-div-zero-avg", "bug_type": "division_by_zero", "source": '''\
def compute_average(numbers):
    total = 0
    count = 0
    for n in numbers:
        if isinstance(n, (int, float)):
            total += n
            count += 1
    # BUG: no check for count == 0
    return total / count


def weighted_average(values, weights):
    total = sum(v * w for v, w in zip(values, weights))
    weight_sum = sum(weights)
    # BUG: weight_sum can be 0
    return total / weight_sum


def normalize(data):
    min_val = min(data)
    max_val = max(data)
    # BUG: max_val == min_val causes division by zero
    return [(x - min_val) / (max_val - min_val) for x in data]
'''},

    # Bug type: division by zero in harmonic mean
    {"id": "bug-div-zero-harmonic", "bug_type": "division_by_zero", "source": '''\
def harmonic_mean(values):
    n = len(values)
    if n == 0:
        return 0.0
    # BUG: values may contain zero
    reciprocal_sum = sum(1.0 / v for v in values)
    return n / reciprocal_sum


def geometric_mean(values):
    product = 1.0
    for v in values:
        product *= v
    # BUG: negative values will cause issues with ** (1/n)
    return product ** (1.0 / len(values))


def coefficient_of_variation(data):
    avg = sum(data) / len(data)
    var = sum((x - avg) ** 2 for x in data) / len(data)
    std = var ** 0.5
    # BUG: avg can be 0
    return std / avg
'''},

    # Bug type: division by zero in slope calculation
    {"id": "bug-div-zero-slope", "bug_type": "division_by_zero", "source": '''\
def calculate_slope(x1, y1, x2, y2):
    # BUG: x2 - x1 can be 0 (vertical line)
    return (y2 - y1) / (x2 - x1)


def line_equation(x1, y1, x2, y2):
    slope = calculate_slope(x1, y1, x2, y2)
    intercept = y1 - slope * x1
    return slope, intercept


def interpolate_point(x1, y1, x2, y2, x):
    slope = calculate_slope(x1, y1, x2, y2)
    return y1 + slope * (x - x1)


def midpoint(x1, y1, x2, y2):
    return (x1 + x2) / 2, (y1 + y2) / 2


def distance(x1, y1, x2, y2):
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
'''},

    # Bug type: off-by-one in binary search
    {"id": "bug-obo-binary-search", "bug_type": "off_by_one", "source": '''\
def binary_search(arr, target):
    left = 0
    # BUG: should be len(arr) - 1
    right = len(arr)
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1


def find_insert_position(arr, target):
    left = 0
    right = len(arr)
    while left < right:
        mid = (left + right) // 2
        # BUG: should be arr[mid] < target for correct position
        if arr[mid] <= target:
            left = mid + 1
        else:
            right = mid
    return left
'''},

    # Bug type: off-by-one in range processing
    {"id": "bug-obo-range", "bug_type": "off_by_one", "source": '''\
def extract_subarray(arr, start, end):
    # BUG: should be end + 1 to include end element
    return arr[start:end]


def rotate_array(arr, k):
    n = len(arr)
    # BUG: does not handle k > n
    return arr[n - k:] + arr[:n - k]


def sliding_window_max(arr, k):
    result = []
    # BUG: range should go to len(arr) - k + 1
    for i in range(len(arr) - k):
        window = arr[i:i + k]
        result.append(max(window))
    return result


def chunk_list(lst, size):
    chunks = []
    # BUG: range step size misses last partial chunk
    for i in range(0, len(lst), size):
        chunks.append(lst[i:i + size])
    return chunks
'''},

    # Bug type: off-by-one in pagination
    {"id": "bug-obo-pagination", "bug_type": "off_by_one", "source": '''\
def paginate(items, page, per_page):
    # BUG: page is 1-based but calculation treats it as 0-based
    start = page * per_page
    end = start + per_page
    total_pages = len(items) // per_page
    return {
        "items": items[start:end],
        "page": page,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def get_page_numbers(current, total, window=2):
    # BUG: should start from max(1, ...) not max(0, ...)
    start = max(0, current - window)
    end = min(total, current + window)
    return list(range(start, end + 1))


def nth_element(items, n):
    # BUG: 1-based index but using 0-based access
    return items[n]
'''},

    # Bug type: missing return statement
    {"id": "bug-no-return-find", "bug_type": "missing_return", "source": '''\
def find_element(collection, predicate):
    for item in collection:
        if predicate(item):
            return item
    # BUG: missing return None


def find_index(lst, target):
    for i, item in enumerate(lst):
        if item == target:
            return i
    # BUG: missing return -1


def find_max_pair(pairs):
    if not pairs:
        return None
    best = pairs[0]
    for pair in pairs[1:]:
        if pair[1] > best[1]:
            best = pair
    # BUG: should return best but forgot return
    best


def safe_get(dictionary, key, default=None):
    if key in dictionary:
        return dictionary[key]
    # BUG: missing return default
'''},

    # Bug type: missing return in recursive function
    {"id": "bug-no-return-recurse", "bug_type": "missing_return", "source": '''\
def flatten(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            # BUG: missing return; should extend result
            flatten(item)
        else:
            result.append(item)
    return result


def deep_copy(obj):
    if isinstance(obj, dict):
        # BUG: missing return
        {k: deep_copy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [deep_copy(item) for item in obj]
    return obj


def tree_height(node):
    if node is None:
        return 0
    left_h = tree_height(node.get("left"))
    right_h = tree_height(node.get("right"))
    # BUG: returns None instead of value (missing return)
    1 + max(left_h, right_h)
'''},

    # Bug type: unhandled None
    {"id": "bug-none-chain", "bug_type": "unhandled_none", "source": '''\
def get_user_city(user):
    # BUG: user["address"] might be None
    return user["address"]["city"]


def get_nested(data, keys):
    current = data
    for key in keys:
        # BUG: current might be None at any step
        current = current[key]
    return current


def first_word(text):
    # BUG: text could be None
    words = text.split()
    if words:
        return words[0]
    return ""


def process_config(config):
    # BUG: config.get("settings") might return None
    settings = config.get("settings")
    timeout = settings.get("timeout", 30)
    retries = settings.get("retries", 3)
    return {"timeout": timeout, "retries": retries}
'''},

    # Bug type: unhandled None from dict.get
    {"id": "bug-none-method", "bug_type": "unhandled_none", "source": '''\
def format_name(record):
    first = record.get("first_name")
    last = record.get("last_name")
    # BUG: first or last might be None, can't call .strip() on None
    return first.strip() + " " + last.strip()


def get_display_name(user):
    name = user.get("display_name")
    # BUG: name might be None
    return name.upper()


def parse_header(headers, name):
    value = headers.get(name)
    # BUG: value might be None
    parts = value.split(",")
    return [p.strip() for p in parts]


def extract_domain(email):
    # BUG: email might be None or not contain @
    return email.split("@")[1]
'''},

    # Bug type: unhandled None in list operations
    {"id": "bug-none-list", "bug_type": "unhandled_none", "source": '''\
def find_longest(strings):
    if not strings:
        return None
    longest = strings[0]
    for s in strings[1:]:
        # BUG: s could be None in the list
        if len(s) > len(longest):
            longest = s
    return longest


def join_parts(parts, separator=", "):
    # BUG: parts might contain None values
    return separator.join(parts)


def sum_values(records, key):
    total = 0
    for record in records:
        # BUG: record[key] might be None
        total += record[key]
    return total


def average_lengths(strings):
    # BUG: strings might contain None
    lengths = [len(s) for s in strings]
    return sum(lengths) / len(lengths)
'''},

    # Bug type: infinite loop (bounded)
    {"id": "bug-inf-loop-converge", "bug_type": "infinite_loop", "source": '''\
def find_fixed_point(f, x0, max_iter=10000):
    x = x0
    # BUG: no convergence check; could loop forever if no max_iter
    for _ in range(max_iter):
        x_new = f(x)
        # BUG: missing tolerance check, always iterates max_iter times
        x = x_new
    return x


def binary_to_decimal(binary_str):
    result = 0
    i = 0
    # BUG: loop goes wrong direction, never terminates properly
    while i < len(binary_str):
        result = result * 2 + int(binary_str[i])
        # BUG: i is never incremented
    return result


def remove_duplicates(lst):
    i = 0
    while i < len(lst):
        j = i + 1
        while j < len(lst):
            if lst[j] == lst[i]:
                lst.pop(j)
                # BUG: should not increment j after pop
            j += 1
        i += 1
    return lst
'''},

    # Bug type: infinite loop from wrong condition
    {"id": "bug-inf-loop-gcd", "bug_type": "infinite_loop", "source": '''\
def gcd_iterative(a, b):
    # BUG: should be 'while b:' not 'while a:'
    while a:
        a, b = b, a % b
    return b


def collatz_length(n):
    count = 0
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        count += 1
        # BUG: no guard for n <= 0 (negative or zero input)
    return count


def consume_whitespace(text, pos):
    # BUG: condition should check pos < len(text) first
    while text[pos] == ' ':
        pos += 1
    return pos


def find_zero(f, lo, hi, max_iter=100):
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
        # BUG: no convergence check, always runs max_iter
    return mid
'''},

    # Bug type: wrong comparison operator
    {"id": "bug-wrong-cmp-sort", "bug_type": "wrong_comparison", "source": '''\
def insertion_sort(arr):
    result = list(arr)
    for i in range(1, len(result)):
        key = result[i]
        j = i - 1
        # BUG: should be result[j] > key for ascending sort
        while j >= 0 and result[j] < key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def find_min(arr):
    if not arr:
        return None
    result = arr[0]
    for x in arr[1:]:
        # BUG: should be x < result
        if x > result:
            result = x
    return result


def clamp(value, min_val, max_val):
    # BUG: comparisons are swapped
    if value > max_val:
        return min_val
    if value < min_val:
        return max_val
    return value
'''},

    # Bug type: wrong comparison in search
    {"id": "bug-wrong-cmp-search", "bug_type": "wrong_comparison", "source": '''\
def linear_search_last(arr, target):
    result = -1
    for i in range(len(arr)):
        # BUG: should be == not !=
        if arr[i] != target:
            result = i
    return result


def is_sorted(arr):
    for i in range(len(arr) - 1):
        # BUG: should be > for ascending check, not >=
        if arr[i] >= arr[i + 1]:
            return False
    return True


def count_greater_than(arr, threshold):
    count = 0
    for x in arr:
        # BUG: should be > not >=
        if x >= threshold:
            count += 1
    return count


def binary_search_leftmost(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        # BUG: should be arr[mid] < target, not <=
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo
'''},

    # Bug type: wrong comparison in validation
    {"id": "bug-wrong-cmp-validate", "bug_type": "wrong_comparison", "source": '''\
def validate_age(age):
    # BUG: should reject negative ages
    if age > 150:
        return False, "Too old"
    if age < -1:
        return False, "Invalid age"
    return True, ""


def is_valid_percentage(value):
    # BUG: should be >= 0, not > 0 (0% is valid)
    return value > 0 and value <= 100


def check_range(value, min_val, max_val):
    # BUG: exclusive instead of inclusive check
    if value < min_val or value > max_val:
        return False
    return True


def is_palindrome(s):
    cleaned = s.lower()
    n = len(cleaned)
    for i in range(n // 2):
        # BUG: should compare [i] with [n - 1 - i], not [n - i]
        if cleaned[i] != cleaned[n - i]:
            return False
    return True
'''},

    # Bug type: missing base case in recursion
    {"id": "bug-no-base-flatten", "bug_type": "missing_base_case", "source": '''\
def flatten_nested(data):
    result = []
    if isinstance(data, list):
        for item in data:
            result.extend(flatten_nested(item))
    elif isinstance(data, dict):
        for value in data.values():
            # BUG: missing base case for non-container dict values
            result.extend(flatten_nested(value))
    # BUG: non-list, non-dict items never get appended
    return result


def count_nodes(tree):
    if tree is None:
        return 0
    # BUG: missing check for leaf nodes (no children key)
    return 1 + sum(count_nodes(child) for child in tree["children"])


def power(base, exp):
    # BUG: missing base case for exp == 0
    if exp == 1:
        return base
    if exp % 2 == 0:
        half = power(base, exp // 2)
        return half * half
    return base * power(base, exp - 1)
'''},

    # Bug type: missing base case in tree traversal
    {"id": "bug-no-base-tree", "bug_type": "missing_base_case", "source": '''\
def tree_sum(node):
    # BUG: missing None check
    left_sum = tree_sum(node.get("left"))
    right_sum = tree_sum(node.get("right"))
    return node["value"] + left_sum + right_sum


def max_depth(node):
    # BUG: no base case for None
    return 1 + max(max_depth(node.get("left")), max_depth(node.get("right")))


def tree_to_list(node):
    # BUG: will recurse forever on None
    result = tree_to_list(node.get("left"))
    result.append(node["value"])
    result.extend(tree_to_list(node.get("right")))
    return result


def count_leaves(node):
    if not node.get("left") and not node.get("right"):
        return 1
    total = 0
    # BUG: no None check before recursing
    total += count_leaves(node.get("left"))
    total += count_leaves(node.get("right"))
    return total
'''},

    # Bug type: key error / attribute error
    {"id": "bug-key-missing", "bug_type": "key_error", "source": '''\
def process_event(event):
    # BUG: assumes "data" key always exists
    data = event["data"]
    timestamp = event["timestamp"]
    source = event["source"]
    return {
        "processed": True,
        "data": data,
        "timestamp": timestamp,
        "source": source,
    }


def merge_profiles(profile_a, profile_b):
    # BUG: assumes both profiles have all keys
    return {
        "name": profile_a["name"],
        "email": profile_b["email"],
        "age": max(profile_a["age"], profile_b["age"]),
        "score": profile_a["score"] + profile_b["score"],
    }


def extract_fields(record, fields):
    result = {}
    for field in fields:
        # BUG: field might not exist in record
        result[field] = record[field]
    return result
'''},

    # Bug type: type error in string concatenation
    {"id": "bug-type-concat", "bug_type": "type_error", "source": '''\
def build_message(name, count, items):
    # BUG: count is int, cannot concatenate with str
    header = "Hello " + name + ", you have " + count + " items"
    body = []
    for item in items:
        # BUG: item might not be string
        body.append("- " + item)
    return header + "\\n" + "\\n".join(body)


def format_record(record):
    parts = []
    for key, value in record.items():
        # BUG: value might not be a string
        parts.append(key + ": " + value)
    return ", ".join(parts)


def log_entry(level, message, context):
    # BUG: context is a dict, cannot concatenate with str
    return "[" + level + "] " + message + " " + context
'''},

    # Bug type: modifying list during iteration
    {"id": "bug-modify-iter", "bug_type": "modify_during_iteration", "source": '''\
def remove_evens(numbers):
    # BUG: modifying list while iterating
    for n in numbers:
        if n % 2 == 0:
            numbers.remove(n)
    return numbers


def deduplicate(items):
    seen = set()
    # BUG: modifying list while iterating
    for item in items:
        if item in seen:
            items.remove(item)
        seen.add(item)
    return items


def filter_by_threshold(data, key, threshold):
    # BUG: deleting from dict while iterating
    for k, v in data.items():
        if v.get(key, 0) < threshold:
            del data[k]
    return data


def compact_list(lst):
    # BUG: index shifts when removing items
    for i in range(len(lst)):
        if lst[i] is None:
            lst.pop(i)
    return lst
'''},

    # Bug type: mutable default argument
    {"id": "bug-mutable-default", "bug_type": "mutable_default", "source": '''\
def add_item(item, inventory=[]):
    # BUG: mutable default argument shared across calls
    inventory.append(item)
    return inventory


def create_user(name, roles=[]):
    # BUG: mutable default
    roles.append("user")
    return {"name": name, "roles": roles}


def collect_errors(error, error_list=[]):
    # BUG: mutable default
    error_list.append(error)
    return error_list


def build_cache(key, value, cache={}):
    # BUG: mutable default dict
    cache[key] = value
    return cache


def register_handler(event, handler, registry={}):
    # BUG: mutable default
    if event not in registry:
        registry[event] = []
    registry[event].append(handler)
    return registry
'''},

    # Bug type: variable scope / shadowing
    {"id": "bug-scope-shadow", "bug_type": "variable_scope", "source": '''\
def process_items(items):
    result = []
    total = 0
    for item in items:
        # BUG: shadows built-in sum
        sum = item["price"] * item["quantity"]
        total += sum
        result.append(sum)
    # BUG: built-in sum is now shadowed
    average = total / len(items)
    return result, average


def apply_discount(prices, discount):
    result = []
    for price in prices:
        # BUG: discount is reassigned inside loop
        discount = price * discount
        result.append(price - discount)
    return result


def count_by_category(items):
    counts = {}
    for item in items:
        # BUG: type shadows built-in
        type = item.get("category", "unknown")
        counts[type] = counts.get(type, 0) + 1
    return counts
'''},

    # Bug type: incorrect exception handling
    {"id": "bug-except-broad", "bug_type": "bad_exception_handling", "source": '''\
def safe_parse_int(s):
    try:
        return int(s)
    except:
        # BUG: catches ALL exceptions including KeyboardInterrupt
        return 0


def read_config_file(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        # BUG: bare except hides real errors
        return ""


def divide_values(a, b):
    try:
        return a / b
    except TypeError:
        return 0
    # BUG: doesn't catch ZeroDivisionError


def process_batch(items, processor):
    results = []
    for item in items:
        try:
            results.append(processor(item))
        except Exception:
            # BUG: silently swallows all errors, no logging
            pass
    return results
'''},

    # Bug type: incorrect string formatting
    {"id": "bug-format-string", "bug_type": "format_error", "source": '''\
def format_table_row(columns, widths):
    parts = []
    for col, width in zip(columns, widths):
        # BUG: format spec wrong for right-align
        parts.append("{:<{}}".format(col, width))
    return " | ".join(parts)


def build_url(base, path, params):
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if params:
        pairs = []
        for k, v in params.items():
            # BUG: doesn't URL-encode values
            pairs.append("{}={}".format(k, v))
        url += "?" + "&".join(pairs)
    return url


def format_duration(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    # BUG: should use int values, seconds might be float
    return "{:02d}:{:02d}:{:02d}".format(hours, minutes, secs)
'''},

    # Bug type: incorrect boolean logic
    {"id": "bug-bool-logic", "bug_type": "wrong_boolean", "source": '''\
def is_valid_email(email):
    has_at = "@" in email
    has_dot = "." in email
    no_spaces = " " not in email
    # BUG: should be 'and' not 'or'
    return has_at or has_dot or no_spaces


def is_business_hours(hour):
    # BUG: should be 'and' not 'or' - currently always True
    return hour >= 9 or hour <= 17


def should_retry(status_code, attempt, max_attempts):
    # BUG: wrong logic, should retry on 5xx only
    return status_code >= 400 and attempt < max_attempts


def is_valid_triangle(a, b, c):
    # BUG: missing check that all sides must be positive
    return a + b > c and a + c > b and b + c > a


def has_permission(user, required_role):
    # BUG: reversed logic
    return required_role not in user.get("roles", [])
'''},

    # Bug type: wrong data structure usage
    {"id": "bug-wrong-ds", "bug_type": "wrong_data_structure", "source": '''\
def unique_elements(lst):
    # BUG: set loses order (should use dict.fromkeys or ordered approach)
    seen = set()
    result = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def stack_operations(ops):
    stack = []
    for op, val in ops:
        if op == "push":
            stack.append(val)
        elif op == "pop":
            # BUG: no empty check
            return stack.pop()
    return stack


def priority_insert(lst, item, key):
    # BUG: O(n) linear scan instead of using heap/bisect
    for i in range(len(lst)):
        if key(item) < key(lst[i]):
            lst.insert(i, item)
            return
    lst.append(item)


def cache_get(cache_list, key):
    # BUG: using list instead of dict for O(n) lookup
    for k, v in cache_list:
        if k == key:
            return v
    return None
'''},

    # Bug type: integer overflow / precision
    {"id": "bug-precision", "bug_type": "precision_error", "source": '''\
def running_sum(data):
    # BUG: accumulating float errors
    total = 0.0
    sums = []
    for val in data:
        total += val
        sums.append(total)
    return sums


def percentage(part, whole):
    # BUG: integer division in Python 2 style (works in 3, but wrong rounding)
    return round(part / whole * 100, 2)


def compound_interest(principal, rate, years):
    # BUG: rate should be decimal but often passed as percentage
    return principal * (1 + rate) ** years


def fahrenheit_to_celsius(f):
    # BUG: integer arithmetic loses precision
    return (f - 32) * 5 // 9


def average_score(scores):
    # BUG: sum of large floats loses precision
    return sum(scores) / len(scores)
'''},

    # Bug type: incorrect iteration order
    {"id": "bug-iter-order", "bug_type": "wrong_iteration", "source": '''\
def reverse_words(sentence):
    words = sentence.split()
    # BUG: reverses characters of each word, not word order
    return " ".join(word[::-1] for word in words)


def matrix_flatten_row_major(matrix):
    result = []
    # BUG: iterates column-major instead of row-major
    for j in range(len(matrix[0])):
        for i in range(len(matrix)):
            result.append(matrix[i][j])
    return result


def process_queue(items):
    results = []
    # BUG: processes from end (stack) instead of front (queue)
    while items:
        item = items.pop()
        results.append(item * 2)
    return results


def interleave(a, b):
    result = []
    # BUG: appends all of a then all of b instead of interleaving
    for item in a:
        result.append(item)
    for item in b:
        result.append(item)
    return result
'''},

    # Bug type: incorrect operator
    {"id": "bug-wrong-op", "bug_type": "wrong_operator", "source": '''\
def is_power_of_two(n):
    # BUG: should be & not |
    return n > 0 and (n | (n - 1)) == 0


def toggle_bit(value, position):
    # BUG: should use ^ (XOR) not | (OR)
    return value | (1 << position)


def clear_bit(value, position):
    # BUG: should use & ~mask, not | ~mask
    mask = 1 << position
    return value | ~mask


def count_set_bits(n):
    count = 0
    while n:
        # BUG: should be n &= (n - 1) not n &= (n + 1)
        n &= (n + 1)
        count += 1
    return count


def merge_flags(a, b):
    # BUG: should OR flags together, using AND instead
    return a & b
'''},
]


# ==============================================================================
# CLEAN PROGRAM IDS -- 30 programs from PROGRAMS used as clean (no bugs)
# ==============================================================================

CLEAN_PROGRAM_IDS = [
    "sort_merge", "sort_quick", "search_binary", "search_bfs", "search_dfs",
    "ds_linked_list", "ds_bst", "ds_min_heap", "ds_hash_map", "ds_trie",
    "str_tokenizer", "str_csv_parse", "str_pattern", "str_wrap", "str_slug",
    "math_matrix", "math_stats", "math_primes", "math_gcd", "math_fibonacci",
    "web_router", "web_request", "web_form_validate", "web_rate_limit",
    "db_query_builder", "db_validator", "file_ini_parse", "sm_tcp",
    "err_circuit_breaker", "sci_rk4",
]


# ==============================================================================
# EQUIVALENCE PAIRS -- 30 pairs for equivalence checking
# 15 equivalent, 15 non-equivalent.
# left_id references PROGRAMS; right is an inline variant.
# ==============================================================================

EQUIV_PAIRS = [
    # --- 15 Equivalent pairs ---

    {"id": "eq-merge-sort", "left_id": "sort_merge", "expected_equiv": True,
     "right": '''\
def merge_sort(arr):
    if len(arr) < 2:
        return list(arr)
    mid = len(arr) // 2
    left_half = merge_sort(arr[:mid])
    right_half = merge_sort(arr[mid:])
    merged = []
    i = j = 0
    while i < len(left_half) and j < len(right_half):
        if left_half[i] <= right_half[j]:
            merged.append(left_half[i])
            i += 1
        else:
            merged.append(right_half[j])
            j += 1
    while i < len(left_half):
        merged.append(left_half[i])
        i += 1
    while j < len(right_half):
        merged.append(right_half[j])
        j += 1
    return merged


def _merge(left, right):
    return merge_sort(left + right)
'''},

    {"id": "eq-binary-search", "left_id": "search_binary", "expected_equiv": True,
     "right": '''\
def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = lo + (hi - lo) // 2
        if arr[mid] == target:
            return mid
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def binary_search_recursive(arr, target, left=0, right=None):
    if right is None:
        right = len(arr) - 1
    if left > right:
        return -1
    mid = left + (right - left) // 2
    if arr[mid] == target:
        return mid
    if arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, right)
    return binary_search_recursive(arr, target, left, mid - 1)
'''},

    {"id": "eq-linked-list", "left_id": "ds_linked_list", "expected_equiv": True,
     "right": '''\
class Node:
    def __init__(self, data):
        self.data = data
        self.next = None


class LinkedList:
    def __init__(self):
        self.head = None

    def append(self, data):
        node = Node(data)
        if self.head is None:
            self.head = node
        else:
            tail = self.head
            while tail.next is not None:
                tail = tail.next
            tail.next = node

    def to_list(self):
        items = []
        node = self.head
        while node is not None:
            items.append(node.data)
            node = node.next
        return items

    def reverse(self):
        previous = None
        current = self.head
        while current is not None:
            next_node = current.next
            current.next = previous
            previous = current
            current = next_node
        self.head = previous
'''},

    {"id": "eq-gcd", "left_id": "math_gcd", "expected_equiv": True,
     "right": '''\
def gcd(a, b):
    a, b = abs(a), abs(b)
    while b != 0:
        a, b = b, a % b
    return a


def lcm(a, b):
    if a == 0 or b == 0:
        return 0
    g = gcd(a, b)
    return abs(a * b) // g


def extended_gcd(a, b):
    old_r, r = a, b
    old_s, s = 1, 0
    old_t, t = 0, 1
    while r != 0:
        quotient = old_r // r
        old_r, r = r, old_r - quotient * r
        old_s, s = s, old_s - quotient * s
        old_t, t = t, old_t - quotient * t
    return old_r, old_s, old_t


def gcd_of_list(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result


def lcm_of_list(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = lcm(result, n)
    return result
'''},

    {"id": "eq-fibonacci", "left_id": "math_fibonacci", "expected_equiv": True,
     "right": '''\
def fibonacci_memo(n, _cache=None):
    if _cache is None:
        _cache = {}
    if n in _cache:
        return _cache[n]
    if n <= 1:
        return n
    result = fibonacci_memo(n - 1, _cache) + fibonacci_memo(n - 2, _cache)
    _cache[n] = result
    return result


def fibonacci_iterative(n):
    if n <= 1:
        return n
    prev, curr = 0, 1
    for _ in range(2, n + 1):
        prev, curr = curr, prev + curr
    return curr


def fibonacci_generator(limit):
    a, b = 0, 1
    while a <= limit:
        yield a
        a, b = b, a + b


def fibonacci_matrix(n):
    if n <= 1:
        return n
    mat = [[1, 1], [1, 0]]
    result = matrix_pow(mat, n - 1)
    return result[0][0]


def matrix_pow(m, p):
    result = [[1, 0], [0, 1]]
    while p > 0:
        if p % 2 == 1:
            result = mat_mult_2x2(result, m)
        m = mat_mult_2x2(m, m)
        p //= 2
    return result


def mat_mult_2x2(a, b):
    return [
        [a[0][0]*b[0][0]+a[0][1]*b[1][0], a[0][0]*b[0][1]+a[0][1]*b[1][1]],
        [a[1][0]*b[0][0]+a[1][1]*b[1][0], a[1][0]*b[0][1]+a[1][1]*b[1][1]],
    ]
'''},

    {"id": "eq-word-freq", "left_id": "str_word_freq", "expected_equiv": True,
     "right": '''\
def word_frequencies(text):
    freq = {}
    for word in text.lower().split():
        clean = "".join(c for c in word if c.isalnum())
        if clean:
            freq[clean] = freq.get(clean, 0) + 1
    return freq


def top_n_words(text, n=10):
    freq = word_frequencies(text)
    return sorted(freq.items(), key=lambda p: (-p[1], p[0]))[:n]


def word_count(text):
    return len(text.split())


def unique_words(text):
    return sorted(word_frequencies(text).keys())


def hapax_legomena(text):
    freq = word_frequencies(text)
    return sorted(w for w, c in freq.items() if c == 1)
'''},

    {"id": "eq-stack-min", "left_id": "ds_stack_min", "expected_equiv": True,
     "right": '''\
class MinStack:
    def __init__(self):
        self.items = []
        self.mins = []

    def push(self, val):
        self.items.append(val)
        if len(self.mins) == 0 or val <= self.mins[-1]:
            self.mins.append(val)

    def pop(self):
        if len(self.items) == 0:
            return None
        val = self.items.pop()
        if val == self.mins[-1]:
            self.mins.pop()
        return val

    def top(self):
        if len(self.items) == 0:
            return None
        return self.items[-1]

    def get_min(self):
        if len(self.mins) == 0:
            return None
        return self.mins[-1]

    def is_empty(self):
        return len(self.items) == 0

    def size(self):
        return len(self.items)
'''},

    {"id": "eq-slug", "left_id": "str_slug", "expected_equiv": True,
     "right": '''\
def slugify(text):
    slug = []
    last_was_sep = True
    for ch in text.strip().lower():
        if ch.isalnum():
            slug.append(ch)
            last_was_sep = False
        elif ch in (' ', '-', '_', '.') and not last_was_sep and slug:
            slug.append('-')
            last_was_sep = True
    while slug and slug[-1] == '-':
        slug.pop()
    return "".join(slug)


def unslugify(slug):
    return " ".join(part.capitalize() for part in slug.split('-') if part)


def truncate_slug(slug, max_length=50):
    if len(slug) <= max_length:
        return slug
    cut = slug[:max_length]
    idx = cut.rfind('-')
    if idx > 0:
        return cut[:idx]
    return cut
'''},

    {"id": "eq-union-find", "left_id": "ds_union_find", "expected_equiv": True,
     "right": '''\
class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.components = n

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.components -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def count_components(self):
        return self.components
'''},

    {"id": "eq-linear-regression", "left_id": "math_regression", "expected_equiv": True,
     "right": '''\
def linear_regression(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    x_bar = sum(xs) / n
    y_bar = sum(ys) / n
    num = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
    den = sum((x - x_bar) ** 2 for x in xs)
    if abs(den) < 1e-15:
        return 0.0, y_bar
    slope = num / den
    intercept = y_bar - slope * x_bar
    return slope, intercept


def r_squared(xs, ys):
    slope, intercept = linear_regression(xs, ys)
    y_mean = sum(ys) / len(ys)
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    if abs(ss_tot) < 1e-15:
        return 1.0
    return 1.0 - ss_res / ss_tot


def predict(xs, ys, x_new):
    slope, intercept = linear_regression(xs, ys)
    return slope * x_new + intercept
'''},

    {"id": "eq-ini-parser", "left_id": "file_ini_parse", "expected_equiv": True,
     "right": '''\
def parse_ini(text):
    sections = {}
    section = None
    for raw_line in text.split('\\n'):
        line = raw_line.strip()
        if not line or line[0] in ('#', ';'):
            continue
        if line[0] == '[' and line[-1] == ']':
            section = line[1:-1].strip()
            sections[section] = {}
        elif '=' in line and section is not None:
            k, _, v = line.partition('=')
            sections[section][k.strip()] = _parse_ini_value(v.strip())
    return sections


def _parse_ini_value(value):
    low = value.lower()
    if low in ('true', 'yes', 'on'):
        return True
    if low in ('false', 'no', 'off'):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def write_ini(sections):
    out = []
    for sec, vals in sections.items():
        out.append("[{}]".format(sec))
        for k, v in vals.items():
            out.append("{} = {}".format(k, v))
        out.append("")
    return "\\n".join(out)
'''},

    {"id": "eq-trie", "left_id": "ds_trie", "expected_equiv": True,
     "right": '''\
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end = False


class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        cur = self.root
        for c in word:
            if c not in cur.children:
                cur.children[c] = TrieNode()
            cur = cur.children[c]
        cur.is_end = True

    def search(self, word):
        node = self._find_node(word)
        return node is not None and node.is_end

    def starts_with(self, prefix):
        return self._find_node(prefix) is not None

    def _find_node(self, s):
        cur = self.root
        for c in s:
            if c not in cur.children:
                return None
            cur = cur.children[c]
        return cur
'''},

    {"id": "eq-turnstile", "left_id": "sm_turnstile", "expected_equiv": True,
     "right": '''\
class Turnstile:
    def __init__(self):
        self.state = "locked"
        self.entry_count = 0
        self.coin_count = 0
        self.rejected = 0

    def coin(self):
        self.coin_count += 1
        if self.state != "unlocked":
            self.state = "unlocked"
            return "unlocked"
        return "already_unlocked"

    def push(self):
        if self.state == "unlocked":
            self.state = "locked"
            self.entry_count += 1
            return "entered"
        else:
            self.rejected += 1
            return "blocked"

    def reset(self):
        self.state = "locked"
        self.entry_count = 0
        self.coin_count = 0
        self.rejected = 0

    def stats(self):
        return {
            "state": self.state,
            "entries": self.entry_count,
            "coins": self.coin_count,
            "rejected": self.rejected,
        }

    def revenue(self, price_per_entry=100):
        return self.coin_count * price_per_entry
'''},

    {"id": "eq-result-type", "left_id": "err_result", "expected_equiv": True,
     "right": '''\
class Ok:
    def __init__(self, value):
        self.value = value
        self.is_ok = True
        self.is_err = False

    def unwrap(self):
        return self.value

    def unwrap_or(self, default):
        return self.value

    def map(self, func):
        return Ok(func(self.value))

    def flat_map(self, func):
        return func(self.value)


class Err:
    def __init__(self, error):
        self.error = error
        self.is_ok = False
        self.is_err = True

    def unwrap(self):
        raise RuntimeError("Unwrap on Err: " + str(self.error))

    def unwrap_or(self, default):
        return default

    def map(self, func):
        return self

    def flat_map(self, func):
        return self


def safe_divide(a, b):
    if b == 0:
        return Err("Division by zero")
    return Ok(a / b)


def safe_index(lst, idx):
    if idx < 0 or idx >= len(lst):
        return Err("Index out of range")
    return Ok(lst[idx])


def collect_results(results):
    values = []
    for r in results:
        if r.is_err:
            return Err(r.error)
        values.append(r.value)
    return Ok(values)
'''},

    {"id": "eq-histogram", "left_id": "sci_histogram", "expected_equiv": True,
     "right": '''\
def compute_histogram(data, num_bins=10):
    if len(data) == 0:
        return [], []
    lo = min(data)
    hi = max(data)
    if lo == hi:
        return [lo], [len(data)]
    width = (hi - lo) / num_bins
    edges = [lo + i * width for i in range(num_bins + 1)]
    counts = [0] * num_bins
    for val in data:
        b = int((val - lo) / width)
        if b >= num_bins:
            b = num_bins - 1
        counts[b] += 1
    return edges, counts


def normalize_histogram(counts):
    s = sum(counts)
    if s == 0:
        return list(counts)
    return [c / s for c in counts]


def cumulative_histogram(counts):
    cum = []
    acc = 0
    for c in counts:
        acc += c
        cum.append(acc)
    return cum


def histogram_to_ascii(counts, width=40):
    peak = max(counts) if counts else 0
    lines = []
    for i, c in enumerate(counts):
        blen = int(c / peak * width) if peak > 0 else 0
        lines.append("{:3d} | {}".format(i, '#' * blen))
    return "\\n".join(lines)
'''},

    # --- 15 Non-equivalent pairs ---

    {"id": "neq-sort-direction", "left_id": "sort_merge", "expected_equiv": False,
     "right": '''\
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
        if left[i] >= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result
'''},

    {"id": "neq-search-first-last", "left_id": "search_binary", "expected_equiv": False,
     "right": '''\
def binary_search(arr, target):
    left = 0
    right = len(arr) - 1
    result = -1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            result = mid
            left = mid + 1
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return result


def binary_search_recursive(arr, target, left=0, right=None):
    if right is None:
        right = len(arr) - 1
    if left > right:
        return -1
    mid = (left + right) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, right)
    return binary_search_recursive(arr, target, left, mid - 1)
'''},

    {"id": "neq-bst-balanced", "left_id": "ds_bst", "expected_equiv": False,
     "right": '''\
class BSTNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None
        self.height = 1


def avl_insert(root, key):
    if root is None:
        return BSTNode(key)
    if key < root.key:
        root.left = avl_insert(root.left, key)
    elif key > root.key:
        root.right = avl_insert(root.right, key)
    else:
        return root
    root.height = 1 + max(_height(root.left), _height(root.right))
    balance = _height(root.left) - _height(root.right)
    if balance > 1 and key < root.left.key:
        return _rotate_right(root)
    if balance < -1 and key > root.right.key:
        return _rotate_left(root)
    return root


def _height(node):
    return node.height if node else 0


def _rotate_right(z):
    y = z.left
    z.left = y.right
    y.right = z
    z.height = 1 + max(_height(z.left), _height(z.right))
    y.height = 1 + max(_height(y.left), _height(y.right))
    return y


def _rotate_left(z):
    y = z.right
    z.right = y.left
    y.left = z
    z.height = 1 + max(_height(z.left), _height(z.right))
    y.height = 1 + max(_height(y.left), _height(y.right))
    return y


def bst_search(root, key):
    if root is None or root.key == key:
        return root
    if key < root.key:
        return bst_search(root.left, key)
    return bst_search(root.right, key)


def bst_inorder(root):
    if root is None:
        return []
    return bst_inorder(root.left) + [root.key] + bst_inorder(root.right)
'''},

    {"id": "neq-stats-population", "left_id": "math_stats", "expected_equiv": False,
     "right": '''\
def mean(data):
    if not data:
        return 0.0
    return sum(data) / len(data)


def median(data):
    if not data:
        return 0.0
    s = sorted(data)
    n = len(s)
    if n % 2 == 0:
        return (s[n // 2 - 1] + s[n // 2]) / 2.0
    return float(s[n // 2])


def variance(data):
    if len(data) < 2:
        return 0.0
    m = mean(data)
    return sum((x - m) ** 2 for x in data) / len(data)


def std_dev(data):
    return variance(data) ** 0.5


def percentile(data, p):
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])
'''},

    {"id": "neq-hash-open-addr", "left_id": "ds_hash_map", "expected_equiv": False,
     "right": '''\
class HashMap:
    def __init__(self, capacity=16):
        self.capacity = capacity
        self.size = 0
        self.keys_arr = [None] * capacity
        self.vals_arr = [None] * capacity

    def _hash(self, key):
        return hash(key) % self.capacity

    def put(self, key, value):
        idx = self._hash(key)
        while self.keys_arr[idx] is not None:
            if self.keys_arr[idx] == key:
                self.vals_arr[idx] = value
                return
            idx = (idx + 1) % self.capacity
        self.keys_arr[idx] = key
        self.vals_arr[idx] = value
        self.size += 1

    def get(self, key, default=None):
        idx = self._hash(key)
        while self.keys_arr[idx] is not None:
            if self.keys_arr[idx] == key:
                return self.vals_arr[idx]
            idx = (idx + 1) % self.capacity
        return default

    def delete(self, key):
        idx = self._hash(key)
        while self.keys_arr[idx] is not None:
            if self.keys_arr[idx] == key:
                self.keys_arr[idx] = None
                self.vals_arr[idx] = None
                self.size -= 1
                return True
            idx = (idx + 1) % self.capacity
        return False

    def keys(self):
        return [k for k in self.keys_arr if k is not None]
'''},

    {"id": "neq-router-regex", "left_id": "web_router", "expected_equiv": False,
     "right": '''\
import re


class Router:
    def __init__(self):
        self.routes = []
        self.not_found_handler = None

    def add_route(self, method, pattern, handler):
        regex = self._pattern_to_regex(pattern)
        self.routes.append((method.upper(), regex, handler))

    def _pattern_to_regex(self, pattern):
        parts = pattern.strip('/').split('/')
        regex_parts = []
        for p in parts:
            if p.startswith(':'):
                regex_parts.append('(?P<' + p[1:] + '>[^/]+)')
            else:
                regex_parts.append(re.escape(p))
        return re.compile('^/' + '/'.join(regex_parts) + '$')

    def match(self, method, path):
        for route_method, regex, handler in self.routes:
            if route_method != method.upper():
                continue
            m = regex.match(path)
            if m:
                return handler, m.groupdict()
        return self.not_found_handler, {}

    def get(self, path, handler):
        self.add_route('GET', path, handler)

    def post(self, path, handler):
        self.add_route('POST', path, handler)
'''},

    {"id": "neq-csv-tsv", "left_id": "str_csv_parse", "expected_equiv": False,
     "right": '''\
def parse_csv_line(line, delimiter='\\t', quote_char='"'):
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == quote_char:
                if i + 1 < len(line) and line[i + 1] == quote_char:
                    current.append(quote_char)
                    i += 2
                    continue
                in_quotes = False
            else:
                current.append(ch)
        else:
            if ch == quote_char:
                in_quotes = True
            elif ch == delimiter:
                fields.append("".join(current))
                current = []
            else:
                current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


def parse_csv(text, delimiter='\\t'):
    lines = text.strip().split('\\n')
    if not lines:
        return []
    headers = parse_csv_line(lines[0], delimiter)
    rows = []
    for line in lines[1:]:
        values = parse_csv_line(line, delimiter)
        row = dict(zip(headers, values))
        rows.append(row)
    return rows
'''},

    {"id": "neq-matrix-strassen", "left_id": "math_matrix", "expected_equiv": False,
     "right": '''\
def matrix_multiply(a, b):
    n = len(a)
    if n == 1:
        return [[a[0][0] * b[0][0]]]
    mid = n // 2
    a11 = [row[:mid] for row in a[:mid]]
    a12 = [row[mid:] for row in a[:mid]]
    a21 = [row[:mid] for row in a[mid:]]
    a22 = [row[mid:] for row in a[mid:]]
    b11 = [row[:mid] for row in b[:mid]]
    b12 = [row[mid:] for row in b[:mid]]
    b21 = [row[:mid] for row in b[mid:]]
    b22 = [row[mid:] for row in b[mid:]]
    m1 = matrix_multiply(matrix_add(a11, a22), matrix_add(b11, b22))
    m2 = matrix_multiply(matrix_add(a21, a22), b11)
    c11 = matrix_add(matrix_add(m1, m2), [[0]*mid]*mid)
    c12 = matrix_add(a11, [[0]*mid]*mid)
    c21 = matrix_add(a21, [[0]*mid]*mid)
    c22 = matrix_add(m1, [[0]*mid]*mid)
    top = [c11[i] + c12[i] for i in range(mid)]
    bot = [c21[i] + c22[i] for i in range(mid)]
    return top + bot


def matrix_transpose(m):
    return [[m[j][i] for j in range(len(m))] for i in range(len(m[0]))]


def matrix_add(a, b):
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]
'''},

    {"id": "neq-query-builder-nosql", "left_id": "db_query_builder", "expected_equiv": False,
     "right": '''\
class QueryBuilder:
    def __init__(self, collection):
        self.collection = collection
        self._filter = {}
        self._projection = None
        self._sort = []
        self._limit = None
        self._skip = None

    def select(self, *fields):
        self._projection = {f: 1 for f in fields}
        return self

    def where(self, field, op, value):
        ops = {"eq": "$eq", "gt": "$gt", "lt": "$lt",
               "gte": "$gte", "lte": "$lte", "ne": "$ne"}
        mongo_op = ops.get(op, "$eq")
        self._filter[field] = {mongo_op: value}
        return self

    def order_by(self, field, direction="asc"):
        self._sort.append((field, 1 if direction == "asc" else -1))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._skip = n
        return self

    def join(self, table, on):
        return self

    def build(self):
        query = {"collection": self.collection, "filter": self._filter}
        if self._projection:
            query["projection"] = self._projection
        if self._sort:
            query["sort"] = self._sort
        if self._limit is not None:
            query["limit"] = self._limit
        if self._skip is not None:
            query["skip"] = self._skip
        return query, []
'''},

    {"id": "neq-rate-limit-token", "left_id": "web_rate_limit", "expected_equiv": False,
     "right": '''\
import time as _time


class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_tokens = max_requests
        self.refill_rate = max_requests / window_seconds
        self.tokens = {}
        self.last_refill = {}

    def allow(self, client_id):
        now = _time.time()
        if client_id not in self.tokens:
            self.tokens[client_id] = self.max_tokens
            self.last_refill[client_id] = now
        elapsed = now - self.last_refill[client_id]
        self.tokens[client_id] = min(
            self.max_tokens,
            self.tokens[client_id] + elapsed * self.refill_rate,
        )
        self.last_refill[client_id] = now
        if self.tokens[client_id] >= 1:
            self.tokens[client_id] -= 1
            return True
        return False

    def remaining(self, client_id):
        if client_id not in self.tokens:
            return self.max_tokens
        return int(self.tokens[client_id])

    def reset(self, client_id):
        if client_id in self.tokens:
            del self.tokens[client_id]
            del self.last_refill[client_id]
'''},

    {"id": "neq-env-loader-typed", "left_id": "file_env_loader", "expected_equiv": False,
     "right": '''\
def parse_env_file(text):
    env = {}
    for line in text.split('\\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env[key] = _auto_cast(value)
    return env


def _auto_cast(value):
    if value.lower() in ('true', 'yes'):
        return True
    if value.lower() in ('false', 'no'):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def interpolate_env(env):
    resolved = dict(env)
    for _ in range(10):
        changed = False
        for key, value in resolved.items():
            if not isinstance(value, str):
                continue
            new_val = value
            for vn, vv in resolved.items():
                ph = '${' + vn + '}'
                if ph in new_val:
                    new_val = new_val.replace(ph, str(vv))
            if new_val != value:
                resolved[key] = new_val
                changed = True
        if not changed:
            break
    return resolved


def get_env(env, key, default=None, cast=None):
    value = env.get(key, default)
    if value is not None and cast is not None:
        try:
            return cast(value)
        except (ValueError, TypeError):
            return default
    return value
'''},

    {"id": "neq-traffic-pedestrian", "left_id": "sm_traffic", "expected_equiv": False,
     "right": '''\
class TrafficLight:
    SEQUENCE = ["red", "green", "yellow", "red_pedestrian"]
    DURATIONS = {"red": 30, "green": 20, "yellow": 5, "red_pedestrian": 10}

    def __init__(self):
        self.state = "red"
        self.elapsed = 0
        self.cycle_count = 0

    def tick(self, seconds=1):
        self.elapsed += seconds
        if self.elapsed >= self.DURATIONS[self.state]:
            self._advance()
            return True
        return False

    def _advance(self):
        idx = self.SEQUENCE.index(self.state)
        next_idx = (idx + 1) % len(self.SEQUENCE)
        self.state = self.SEQUENCE[next_idx]
        self.elapsed = 0
        if self.state == "red":
            self.cycle_count += 1

    def is_safe_to_go(self):
        return self.state == "green"

    def time_remaining(self):
        return self.DURATIONS[self.state] - self.elapsed

    def override(self, new_state):
        if new_state in self.SEQUENCE:
            self.state = new_state
            self.elapsed = 0
            return True
        return False

    def get_status(self):
        return {
            "state": self.state,
            "elapsed": self.elapsed,
            "remaining": self.time_remaining(),
            "cycles": self.cycle_count,
        }
'''},

    {"id": "neq-euler-midpoint", "left_id": "sci_euler", "expected_equiv": False,
     "right": '''\
def euler_method(f, y0, t0, t_end, h):
    t = t0
    y = y0
    ts = [t]
    ys = [y]
    while t < t_end:
        k1 = f(t, y)
        y = y + h * f(t + h / 2, y + h / 2 * k1)
        t = t + h
        ts.append(t)
        ys.append(y)
    return ts, ys


def euler_system(f_vec, y0_vec, t0, t_end, h):
    t = t0
    y = list(y0_vec)
    n = len(y)
    ts = [t]
    ys = [list(y)]
    while t < t_end:
        dydt = f_vec(t, y)
        y = [y[i] + h * dydt[i] for i in range(n)]
        t = t + h
        ts.append(t)
        ys.append(list(y))
    return ts, ys


def improved_euler(f, y0, t0, t_end, h):
    t = t0
    y = y0
    ts = [t]
    ys = [y]
    while t < t_end:
        k1 = f(t, y)
        k2 = f(t + h, y + h * k1)
        y = y + h * (k1 + k2) / 2.0
        t = t + h
        ts.append(t)
        ys.append(y)
    return ts, ys
'''},

    {"id": "neq-moving-avg-weighted", "left_id": "sci_moving_avg", "expected_equiv": False,
     "right": '''\
def simple_moving_average(data, window):
    if window <= 0 or window > len(data):
        return []
    result = []
    for i in range(len(data) - window + 1):
        chunk = data[i:i + window]
        weights = list(range(1, window + 1))
        total_w = sum(weights)
        result.append(sum(c * w for c, w in zip(chunk, weights)) / total_w)
    return result


def exponential_moving_average(data, alpha=0.3):
    if not data:
        return []
    result = [data[0]]
    for i in range(1, len(data)):
        ema = alpha * data[i] + (1 - alpha) * result[-1]
        result.append(ema)
    return result


def weighted_moving_average(data, weights):
    if not data or not weights:
        return []
    w = len(weights)
    total_weight = sum(weights)
    result = []
    for i in range(w - 1, len(data)):
        val = sum(data[i - w + 1 + j] * weights[j] for j in range(w))
        result.append(val / total_weight)
    return result
'''},

    {"id": "neq-circuit-breaker-count", "left_id": "err_circuit_breaker", "expected_equiv": False,
     "right": '''\
import time as _time


class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.success_count = 0
        self.state = "closed"
        self.last_failure_time = 0

    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if _time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = "half_open"
            else:
                raise RuntimeError("Circuit breaker is open")
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        self.success_count += 1
        if self.state == "half_open" and self.success_count >= 3:
            self.failure_count = 0
            self.state = "closed"

    def _on_failure(self):
        self.failure_count += 1
        self.success_count = 0
        self.last_failure_time = _time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def reset(self):
        self.failure_count = 0
        self.success_count = 0
        self.state = "closed"

    def get_state(self):
        return {
            "state": self.state,
            "failures": self.failure_count,
            "successes": self.success_count,
            "threshold": self.failure_threshold,
        }
'''},
]


# ==============================================================================
# BENCHMARK RUNNERS
# ==============================================================================

def run_prove_benchmarks():
    """Run `jugeo prove` on all 100 programs."""
    results = []
    for prog_id, source in PROGRAMS.items():
        ast_ok, ast_us = ast_check(source)
        tmp = write_temp_py(source)
        t0 = time.perf_counter()
        objs = run_jugeo("prove", tmp)
        elapsed = time.perf_counter() - t0

        verdict = "unknown"
        n_coords = 0
        n_morphisms = 0
        n_propositions = 0
        if objs and isinstance(objs[0], dict):
            for finfo in objs[0].get("files", [objs[0]]):
                verdict = finfo.get("verdict", verdict)
                n_coords += finfo.get("coordinates", 0)
                n_morphisms += finfo.get("morphisms", 0)
                n_propositions += finfo.get("propositions", 0)

        results.append({
            "id": prog_id,
            "verdict": verdict,
            "verified": verdict.lower() in ("verified", "safe", "correct", "valid"),
            "coordinates": n_coords,
            "morphisms": n_morphisms,
            "propositions": n_propositions,
            "time_s": round(elapsed, 4),
            "ast_us": round(ast_us, 1),
            "ast_ok": ast_ok,
        })
        cleanup(tmp)
    return results


def run_encode_benchmarks():
    """Run `jugeo encode` on all 100 programs."""
    results = []
    for prog_id, source in PROGRAMS.items():
        tmp = write_temp_py(source)
        t0 = time.perf_counter()
        objs = run_jugeo("encode", tmp)
        elapsed = time.perf_counter() - t0

        n_coords = 0
        n_morphisms = 0
        n_covers = 0
        if objs and isinstance(objs[0], dict):
            site = objs[0]
            n_coords = site.get("coordinates", site.get("n_objects", 0))
            n_morphisms = site.get("morphisms", site.get("n_morphisms", 0))
            n_covers = site.get("covers", site.get("n_covers", 0))

        results.append({
            "id": prog_id,
            "coordinates": n_coords,
            "morphisms": n_morphisms,
            "covers": n_covers,
            "time_s": round(elapsed, 4),
        })
        cleanup(tmp)
    return results


def run_descend_benchmarks(sample_ids=None):
    """Run `jugeo descend` on a representative subset."""
    if sample_ids is None:
        all_ids = list(PROGRAMS.keys())
        random.shuffle(all_ids)
        sample_ids = all_ids[:20]
    results = []
    for prog_id in sample_ids:
        source = PROGRAMS[prog_id]
        tmp = write_temp_py(source)
        t0 = time.perf_counter()
        objs = run_jugeo("descend", tmp)
        elapsed = time.perf_counter() - t0

        descent_ok = False
        n_patches = 0
        if objs and isinstance(objs[0], dict):
            d = objs[0]
            descent_ok = d.get("descent_successful", d.get("success", False))
            n_patches = d.get("patches", d.get("n_patches", 0))

        results.append({
            "id": prog_id,
            "descent_ok": descent_ok,
            "patches": n_patches,
            "time_s": round(elapsed, 4),
        })
        cleanup(tmp)
    return results


def run_bug_benchmarks():
    """Run `jugeo bugs` on 30 buggy + 30 clean programs."""
    results = []

    # Buggy programs (expect bugs detected -> TP if found, FN if missed)
    for case in BUGGY_PROGRAMS:
        tmp = write_temp_py(case["source"])
        t0 = time.perf_counter()
        objs = run_jugeo("bugs", tmp)
        elapsed = time.perf_counter() - t0

        bugs_found = 0
        if objs and isinstance(objs[0], (list, dict)):
            data = objs[0] if isinstance(objs[0], list) else [objs[0]]
            for item in data:
                bugs_found += item.get("count", 0)

        detected = bugs_found > 0
        classification = "TP" if detected else "FN"

        results.append({
            "id": case["id"],
            "has_bug": True,
            "bug_type": case.get("bug_type", ""),
            "detected": detected,
            "bugs_found": bugs_found,
            "classification": classification,
            "correct": classification == "TP",
            "time_s": round(elapsed, 4),
        })
        cleanup(tmp)

    # Clean programs (expect no bugs -> TN if clean, FP if flagged)
    for prog_id in CLEAN_PROGRAM_IDS:
        source = PROGRAMS[prog_id]
        tmp = write_temp_py(source)
        t0 = time.perf_counter()
        objs = run_jugeo("bugs", tmp)
        elapsed = time.perf_counter() - t0

        bugs_found = 0
        if objs and isinstance(objs[0], (list, dict)):
            data = objs[0] if isinstance(objs[0], list) else [objs[0]]
            for item in data:
                bugs_found += item.get("count", 0)

        detected = bugs_found > 0
        classification = "FP" if detected else "TN"

        results.append({
            "id": "clean-" + prog_id,
            "has_bug": False,
            "bug_type": "",
            "detected": detected,
            "bugs_found": bugs_found,
            "classification": classification,
            "correct": classification == "TN",
            "time_s": round(elapsed, 4),
        })
        cleanup(tmp)

    return results


def run_equiv_benchmarks():
    """Run `jugeo equiv` on 30 program pairs."""
    results = []
    for pair in EQUIV_PAIRS:
        # Left source comes from PROGRAMS dict
        left_source = PROGRAMS[pair["left_id"]]
        right_source = pair["right"]

        tmp_l = write_temp_py(left_source)
        tmp_r = write_temp_py(right_source)
        t0 = time.perf_counter()
        objs = run_jugeo("equiv", tmp_l, tmp_r)
        elapsed = time.perf_counter() - t0

        verdict = "unknown"
        n_obs = 0
        if objs and isinstance(objs[0], dict):
            verdict = objs[0].get("verdict", "unknown")
            n_obs = len(objs[0].get("obstructions", []))

        actual_equiv = (
            "equivalent" in verdict.lower()
            and "not" not in verdict.lower()
            and n_obs == 0
        )
        expected = pair["expected_equiv"]

        if expected and actual_equiv:
            classification = "TP"
        elif expected and not actual_equiv:
            classification = "FN"
        elif not expected and not actual_equiv:
            classification = "TN"
        else:
            classification = "FP"

        results.append({
            "id": pair["id"],
            "expected_equiv": expected,
            "actual_equiv": actual_equiv,
            "verdict": verdict,
            "obstructions": n_obs,
            "classification": classification,
            "correct": classification in ("TP", "TN"),
            "time_s": round(elapsed, 4),
        })
        cleanup(tmp_l)
        cleanup(tmp_r)

    return results


def cross_validate_with_strategies(sample_ids, category):
    """Run a few cases with multiple strategies to check agreement."""
    cross = []
    for prog_id in sample_ids[:5]:
        source = PROGRAMS.get(prog_id)
        if not source:
            continue
        tmp = write_temp_py(source)
        strat_results = {}
        for strat in ["eager", "exhaustive", "iterative"]:
            objs = run_jugeo("prove", tmp, "--strategy", strat)
            verdict = "unknown"
            if objs and isinstance(objs[0], dict):
                for finfo in objs[0].get("files", [objs[0]]):
                    verdict = finfo.get("verdict", verdict)
            strat_results[strat] = verdict
        all_same = len(set(strat_results.values())) == 1
        cross.append({
            "category": category,
            "program": prog_id,
            "strategies": strat_results,
            "agreement": all_same,
        })
        cleanup(tmp)
    return cross


# ==============================================================================
# METRICS
# ==============================================================================

def compute_classification_metrics(case_results):
    """Compute accuracy/precision/recall/f1 from classification results."""
    tp = sum(1 for r in case_results if r["classification"] == "TP")
    tn = sum(1 for r in case_results if r["classification"] == "TN")
    fp = sum(1 for r in case_results if r["classification"] == "FP")
    fn = sum(1 for r in case_results if r["classification"] == "FN")
    total = len(case_results)
    correct = tp + tn
    accuracy = correct / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    times = [r["time_s"] for r in case_results]
    sorted_times = sorted(times)
    return {
        "total_cases": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "time_mean_s": round(statistics.mean(times), 4) if times else 0,
        "time_median_s": round(statistics.median(times), 4) if times else 0,
        "time_p95_s": round(sorted_times[int(len(sorted_times) * 0.95)], 4)
                      if times else 0,
        "time_max_s": round(max(times), 4) if times else 0,
    }


def compute_timing_stats(results_list):
    """Compute timing statistics from a list of results with time_s."""
    times = [r["time_s"] for r in results_list]
    if not times:
        return {}
    sorted_times = sorted(times)
    return {
        "count": len(times),
        "mean_s": round(statistics.mean(times), 4),
        "median_s": round(statistics.median(times), 4),
        "p95_s": round(sorted_times[int(len(sorted_times) * 0.95)], 4),
        "max_s": round(max(times), 4),
        "min_s": round(min(times), 4),
        "total_s": round(sum(times), 3),
    }


def compute_site_complexity_stats(encode_results):
    """Distribution of site complexity."""
    coords = [r["coordinates"] for r in encode_results]
    morphs = [r["morphisms"] for r in encode_results]
    covers = [r["covers"] for r in encode_results]
    def _stats(vals):
        if not vals:
            return {}
        return {
            "mean": round(statistics.mean(vals), 2),
            "median": round(statistics.median(vals), 2),
            "max": max(vals),
            "min": min(vals),
        }
    return {
        "coordinates": _stats(coords),
        "morphisms": _stats(morphs),
        "covers": _stats(covers),
    }


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 76)
    print("Paper 10: Full Benchmark Suite for Sheaf-Theoretic Program Verification")
    print("Programs: {} | Buggy: {} | Clean: {} | Equiv pairs: {}".format(
        len(PROGRAMS), len(BUGGY_PROGRAMS), len(CLEAN_PROGRAM_IDS), len(EQUIV_PAIRS)))
    print("=" * 76)

    # --- Validate all programs parse ---
    print("\n  Validating all program sources with ast.parse()...")
    parse_errors = 0
    for prog_id, source in PROGRAMS.items():
        try:
            ast.parse(source)
        except SyntaxError as e:
            print("    PARSE ERROR in {}: {}".format(prog_id, e))
            parse_errors += 1
    for case in BUGGY_PROGRAMS:
        try:
            ast.parse(case["source"])
        except SyntaxError as e:
            print("    PARSE ERROR in {}: {}".format(case["id"], e))
            parse_errors += 1
    for pair in EQUIV_PAIRS:
        try:
            ast.parse(pair["right"])
        except SyntaxError as e:
            print("    PARSE ERROR in {}: {}".format(pair["id"], e))
            parse_errors += 1
    if parse_errors:
        print("    {} parse errors found — aborting.".format(parse_errors))
        return
    print("    All sources parse OK.")

    # --- 1. Prove ---
    print("\n  [1/5] Running PROVE on {} programs...".format(len(PROGRAMS)))
    prove_results = run_prove_benchmarks()
    verified = sum(1 for r in prove_results if r["verified"])
    print("    Verified: {}/{} ({:.1%})".format(
        verified, len(prove_results), verified / len(prove_results)))

    # --- 2. Encode ---
    print("\n  [2/5] Running ENCODE on {} programs...".format(len(PROGRAMS)))
    encode_results = run_encode_benchmarks()
    print("    Encoded: {} programs".format(len(encode_results)))

    # --- 3. Bugs ---
    print("\n  [3/5] Running BUGS on {} buggy + {} clean programs...".format(
        len(BUGGY_PROGRAMS), len(CLEAN_PROGRAM_IDS)))
    bug_results = run_bug_benchmarks()
    bug_metrics = compute_classification_metrics(bug_results)
    print("    TP={tp} TN={tn} FP={fp} FN={fn} | Accuracy={accuracy:.1%}".format(
        **bug_metrics))

    # --- 4. Equiv ---
    print("\n  [4/5] Running EQUIV on {} pairs...".format(len(EQUIV_PAIRS)))
    equiv_results = run_equiv_benchmarks()
    equiv_metrics = compute_classification_metrics(equiv_results)
    print("    TP={tp} TN={tn} FP={fp} FN={fn} | Accuracy={accuracy:.1%}".format(
        **equiv_metrics))

    # --- 5. Descend ---
    descend_ids = list(PROGRAMS.keys())[:20]
    print("\n  [5/5] Running DESCEND on {} programs...".format(len(descend_ids)))
    descend_results = run_descend_benchmarks(descend_ids)
    descend_ok = sum(1 for r in descend_results if r["descent_ok"])
    print("    Descent OK: {}/{} ({:.1%})".format(
        descend_ok, len(descend_results),
        descend_ok / len(descend_results) if descend_results else 0))

    # --- Cross-validation ---
    print("\n  Cross-validating with multiple strategies...")
    cat_samples = {
        "sort": ["sort_merge", "sort_quick", "sort_heap", "sort_insertion", "sort_radix"],
        "ds": ["ds_linked_list", "ds_bst", "ds_min_heap", "ds_hash_map", "ds_trie"],
        "str": ["str_tokenizer", "str_csv_parse", "str_pattern", "str_wrap", "str_slug"],
        "math": ["math_matrix", "math_stats", "math_polynomial", "math_primes", "math_gcd"],
        "web": ["web_router", "web_request", "web_form_validate", "web_rate_limit", "web_auth"],
    }
    cross_results = []
    for cat, ids in cat_samples.items():
        cross_results.extend(cross_validate_with_strategies(ids, cat))
    total_agree = sum(1 for c in cross_results if c["agreement"])
    print("    Strategy agreement: {}/{} ({:.1%})".format(
        total_agree, len(cross_results),
        total_agree / len(cross_results) if cross_results else 0))

    # --- Timing stats ---
    prove_timing = compute_timing_stats(prove_results)
    encode_timing = compute_timing_stats(encode_results)
    bug_timing = compute_timing_stats(bug_results)
    equiv_timing = compute_timing_stats(equiv_results)
    descend_timing = compute_timing_stats(descend_results)

    # --- Site complexity ---
    site_complexity = compute_site_complexity_stats(encode_results)

    # --- Assemble final results ---
    results = {
        "experiment": "full_benchmark",
        "paper": 10,
        "program_count": len(PROGRAMS),
        "suite_results": {
            "prove": {
                "cases": prove_results,
                "verified": verified,
                "total": len(prove_results),
                "accuracy": round(verified / len(prove_results), 4)
                            if prove_results else 0,
                "timing": prove_timing,
            },
            "encode": {
                "cases": encode_results,
                "timing": encode_timing,
                "site_complexity": site_complexity,
            },
            "bug": {
                "cases": bug_results,
                "metrics": bug_metrics,
                "timing": bug_timing,
            },
            "equivalence": {
                "cases": equiv_results,
                "metrics": equiv_metrics,
                "timing": equiv_timing,
            },
            "descend": {
                "cases": descend_results,
                "success_count": descend_ok,
                "total": len(descend_results),
                "timing": descend_timing,
            },
        },
        "cross_validation": {
            "cases": cross_results,
            "agreement_count": total_agree,
            "total": len(cross_results),
            "agreement_pct": round(
                total_agree / len(cross_results) * 100, 1
            ) if cross_results else 0,
        },
        "literature_baselines": LITERATURE_BASELINES,
        "summary": {
            "total_programs": len(PROGRAMS),
            "prove_verified": verified,
            "prove_accuracy": round(verified / len(prove_results), 4)
                              if prove_results else 0,
            "bug_accuracy": bug_metrics.get("accuracy", 0),
            "bug_tp": bug_metrics.get("tp", 0),
            "bug_tn": bug_metrics.get("tn", 0),
            "bug_fp": bug_metrics.get("fp", 0),
            "bug_fn": bug_metrics.get("fn", 0),
            "equiv_accuracy": equiv_metrics.get("accuracy", 0),
            "descend_success": descend_ok,
            "cross_agreement_pct": round(
                total_agree / len(cross_results) * 100, 1
            ) if cross_results else 0,
            "total_time_s": round(
                prove_timing.get("total_s", 0) +
                encode_timing.get("total_s", 0) +
                bug_timing.get("total_s", 0) +
                equiv_timing.get("total_s", 0) +
                descend_timing.get("total_s", 0), 3),
            "note": "All numbers from jugeo CLI via subprocess",
        },
    }

    # --- Print summary ---
    print("\n" + "=" * 76)
    print("OVERALL SUMMARY")
    s = results["summary"]
    print("  Programs:             {}".format(s["total_programs"]))
    print("  Prove accuracy:       {:.1%}".format(s["prove_accuracy"]))
    print("  Bug detection:")
    print("    TP={} TN={} FP={} FN={}".format(
        s["bug_tp"], s["bug_tn"], s["bug_fp"], s["bug_fn"]))
    print("    Accuracy: {:.1%}".format(s["bug_accuracy"]))
    print("  Equivalence accuracy: {:.1%}".format(s["equiv_accuracy"]))
    print("  Descent success:      {}/{}".format(s["descend_success"], len(descend_results)))
    print("  Strategy agreement:   {}%".format(s["cross_agreement_pct"]))
    print("  Total time:           {:.3f}s".format(s["total_time_s"]))

    print("\n  TIMING (prove):  mean={mean_s}s  median={median_s}s  p95={p95_s}s  "
          "max={max_s}s".format(**prove_timing) if prove_timing else "")
    print("  TIMING (encode): mean={mean_s}s  median={median_s}s  p95={p95_s}s  "
          "max={max_s}s".format(**encode_timing) if encode_timing else "")
    print("  TIMING (bugs):   mean={mean_s}s  median={median_s}s  p95={p95_s}s  "
          "max={max_s}s".format(**bug_timing) if bug_timing else "")
    print("  TIMING (equiv):  mean={mean_s}s  median={median_s}s  p95={p95_s}s  "
          "max={max_s}s".format(**equiv_timing) if equiv_timing else "")

    print("\n  SITE COMPLEXITY:")
    for key in ("coordinates", "morphisms", "covers"):
        st = site_complexity.get(key, {})
        if st:
            print("    {}: mean={mean} median={median} min={min} max={max}".format(
                key, **st))

    print("\n  LITERATURE BASELINES (LITERATURE_ESTIMATED):")
    for key, bl in LITERATURE_BASELINES.items():
        print("    {}: {} [{label}]".format(key, bl["estimated_accuracy_pct"], **bl))

    # --- Detail per suite ---
    for suite_name, cases in [("prove", prove_results[:10]),
                               ("bug", bug_results[:10]),
                               ("equiv", equiv_results[:10])]:
        print("\n  {} detail (first 10):".format(suite_name.upper()))
        for c in cases:
            if "classification" in c:
                mark = "OK" if c.get("correct", False) else "XX"
                print("    [{m}] {id:30s} {classification:4s}  ({time_s}s)".format(
                    m=mark, **c))
            elif "verified" in c:
                mark = "OK" if c["verified"] else "XX"
                print("    [{m}] {id:30s} {verdict:12s}  ({time_s}s)".format(
                    m=mark, **c))
            else:
                print("    {id:30s}  ({time_s}s)".format(**c))

    # --- Save ---
    out = os.path.join(os.path.dirname(__file__), "results_paper10.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nResults saved to {}".format(out))


if __name__ == "__main__":
    main()
