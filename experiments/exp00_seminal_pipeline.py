#!/usr/bin/env python3
"""Seminal Paper Experiment — Judgment Geometry: End-to-End Pipeline.

Runs the FULL Judgment Geometry pipeline on representative Python programs
via the ``jugeo prove`` CLI:  site → judgment → descent → trust → certificate.

Every number is produced by calling the ``python3 -m jugeo`` CLI as a subprocess.
Re-run: python3 experiments/exp00_seminal_pipeline.py
"""
import ast, json, os, random, subprocess, sys, tempfile, time

random.seed(42)

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")

# ── helpers ──────────────────────────────────────────────────────────────

def run_jugeo(*args):
    """Run jugeo CLI and return a list of parsed JSON objects."""
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


def write_temp(source: str) -> str:
    """Write source to a temp .py file and return its path."""
    f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    f.write(source)
    f.close()
    return f.name


# ── test programs ────────────────────────────────────────────────────────

PROGRAMS = {

    # ── Sorting/Searching ────────────────────────────────────────────────

    "binary_search": '''
def binary_search(arr, target):
    left, right = 0, len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1

def search_range(arr, target):
    first = binary_search(arr, target)
    if first == -1:
        return (-1, -1)
    left = first
    while left > 0 and arr[left - 1] == target:
        left -= 1
    right = first
    while right < len(arr) - 1 and arr[right + 1] == target:
        right += 1
    return (left, right)

def count_occurrences(arr, target):
    lo, hi = search_range(arr, target)
    if lo == -1:
        return 0
    return hi - lo + 1
''',

    "merge_sort": '''
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

def merge_sort(arr):
    if len(arr) <= 1:
        return list(arr)
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)

def is_sorted(arr):
    return all(arr[i] <= arr[i + 1] for i in range(len(arr) - 1))

def sort_and_verify(arr):
    result = merge_sort(arr)
    assert is_sorted(result)
    assert len(result) == len(arr)
    return result
''',

    "quicksort": '''
def partition(arr, low, high):
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1

def quicksort(arr, low=None, high=None):
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low < high:
        pi = partition(arr, low, high)
        quicksort(arr, low, pi - 1)
        quicksort(arr, pi + 1, high)

def quicksort_copy(arr):
    copy = list(arr)
    quicksort(copy)
    return copy

def median_of_three(arr, low, high):
    mid = (low + high) // 2
    if arr[low] > arr[mid]:
        arr[low], arr[mid] = arr[mid], arr[low]
    if arr[low] > arr[high]:
        arr[low], arr[high] = arr[high], arr[low]
    if arr[mid] > arr[high]:
        arr[mid], arr[high] = arr[high], arr[mid]
    return mid
''',

    "heap_sort": '''
def heapify(arr, n, i):
    largest = i
    left = 2 * i + 1
    right = 2 * i + 2
    if left < n and arr[left] > arr[largest]:
        largest = left
    if right < n and arr[right] > arr[largest]:
        largest = right
    if largest != i:
        arr[i], arr[largest] = arr[largest], arr[i]
        heapify(arr, n, largest)

def heap_sort(arr):
    n = len(arr)
    for i in range(n // 2 - 1, -1, -1):
        heapify(arr, n, i)
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        heapify(arr, i, 0)
    return arr

def heap_push(heap, val):
    heap.append(val)
    idx = len(heap) - 1
    while idx > 0:
        parent = (idx - 1) // 2
        if heap[parent] < heap[idx]:
            heap[parent], heap[idx] = heap[idx], heap[parent]
            idx = parent
        else:
            break
''',

    "insertion_sort": '''
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
        for j in range(i, lo, -1):
            result[j] = result[j - 1]
        result[lo] = key
    return result

def is_nearly_sorted(arr, k):
    for i in range(len(arr)):
        target = insertion_sort(arr).index(arr[i])
        if abs(i - target) > k:
            return False
    return True
''',

    "counting_sort": '''
def counting_sort(arr):
    if not arr:
        return []
    min_val = min(arr)
    max_val = max(arr)
    range_size = max_val - min_val + 1
    count = [0] * range_size
    output = [0] * len(arr)
    for val in arr:
        count[val - min_val] += 1
    for i in range(1, range_size):
        count[i] += count[i - 1]
    for val in reversed(arr):
        idx = count[val - min_val] - 1
        output[idx] = val
        count[val - min_val] -= 1
    return output

def counting_sort_by_key(items, key_func, max_key):
    count = [0] * (max_key + 1)
    for item in items:
        count[key_func(item)] += 1
    for i in range(1, max_key + 1):
        count[i] += count[i - 1]
    output = [None] * len(items)
    for item in reversed(items):
        k = key_func(item)
        count[k] -= 1
        output[count[k]] = item
    return output
''',

    "topological_sort": '''
def topological_sort(graph):
    in_degree = {}
    for node in graph:
        if node not in in_degree:
            in_degree[node] = 0
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
    queue = [n for n in graph if in_degree.get(n, 0) == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != len(graph):
        raise ValueError("Graph has a cycle")
    return result

def has_cycle(graph):
    visited = set()
    rec_stack = set()
    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.discard(node)
        return False
    for node in graph:
        if node not in visited:
            if dfs(node):
                return True
    return False
''',

    "radix_sort": '''
def counting_sort_digit(arr, exp):
    n = len(arr)
    output = [0] * n
    count = [0] * 10
    for val in arr:
        idx = (val // exp) % 10
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
        counting_sort_digit(arr, exp)
        exp *= 10
    return arr

def num_digits(n):
    if n == 0:
        return 1
    count = 0
    while n > 0:
        count += 1
        n //= 10
    return count
''',

    "search_rotated": '''
def search_rotated(arr, target):
    left, right = 0, len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        if arr[left] <= arr[mid]:
            if arr[left] <= target < arr[mid]:
                right = mid - 1
            else:
                left = mid + 1
        else:
            if arr[mid] < target <= arr[right]:
                left = mid + 1
            else:
                right = mid - 1
    return -1

def find_pivot(arr):
    left, right = 0, len(arr) - 1
    if arr[left] <= arr[right]:
        return 0
    while left <= right:
        mid = (left + right) // 2
        if mid < len(arr) - 1 and arr[mid] > arr[mid + 1]:
            return mid + 1
        if arr[left] <= arr[mid]:
            left = mid + 1
        else:
            right = mid
    return 0

def find_min_rotated(arr):
    pivot = find_pivot(arr)
    return arr[pivot]
''',

    "kth_smallest": '''
def kth_smallest(arr, k):
    if k < 1 or k > len(arr):
        raise ValueError("k out of range")
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    mid = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    if k <= len(left):
        return kth_smallest(left, k)
    elif k <= len(left) + len(mid):
        return pivot
    else:
        return kth_smallest(right, k - len(left) - len(mid))

def median(arr):
    n = len(arr)
    if n % 2 == 1:
        return kth_smallest(arr, n // 2 + 1)
    else:
        lo = kth_smallest(arr, n // 2)
        hi = kth_smallest(arr, n // 2 + 1)
        return (lo + hi) / 2.0

def top_k(arr, k):
    if k >= len(arr):
        return sorted(arr, reverse=True)
    result = []
    remaining = list(arr)
    for i in range(k):
        mx = kth_smallest(remaining, len(remaining))
        result.append(mx)
        remaining.remove(mx)
    return result
''',


    # ── Data Structures ──────────────────────────────────────────────────

    "linked_list": '''
class Node:
    def __init__(self, val, next_node=None):
        self.val = val
        self.next = next_node

class LinkedList:
    def __init__(self):
        self.head = None
        self.size = 0

    def push(self, val):
        self.head = Node(val, self.head)
        self.size += 1

    def pop(self):
        if self.head is None:
            raise IndexError("pop from empty list")
        val = self.head.val
        self.head = self.head.next
        self.size -= 1
        return val

    def to_list(self):
        result = []
        cur = self.head
        while cur is not None:
            result.append(cur.val)
            cur = cur.next
        return result

    def reverse(self):
        prev = None
        cur = self.head
        while cur is not None:
            nxt = cur.next
            cur.next = prev
            prev = cur
            cur = nxt
        self.head = prev

    def find(self, val):
        cur = self.head
        idx = 0
        while cur is not None:
            if cur.val == val:
                return idx
            cur = cur.next
            idx += 1
        return -1
''',

    "stack_calculator": '''
def tokenize(expr):
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
            continue
        if expr[i].isdigit() or (expr[i] == "-" and (not tokens or tokens[-1] == "(")):
            j = i
            if expr[i] == "-":
                i += 1
            while i < len(expr) and (expr[i].isdigit() or expr[i] == "."):
                i += 1
            tokens.append(expr[j:i])
        elif expr[i] in "+-*/()":
            tokens.append(expr[i])
            i += 1
        else:
            raise ValueError("Unexpected character: " + expr[i])
    return tokens

def precedence(op):
    if op in ("+", "-"):
        return 1
    if op in ("*", "/"):
        return 2
    return 0

def infix_to_postfix(tokens):
    output = []
    ops = []
    for tok in tokens:
        if tok == "(":
            ops.append(tok)
        elif tok == ")":
            while ops and ops[-1] != "(":
                output.append(ops.pop())
            ops.pop()
        elif tok in "+-*/":
            while ops and ops[-1] != "(" and precedence(ops[-1]) >= precedence(tok):
                output.append(ops.pop())
            ops.append(tok)
        else:
            output.append(float(tok))
    while ops:
        output.append(ops.pop())
    return output

def eval_postfix(postfix):
    stack = []
    for tok in postfix:
        if isinstance(tok, float):
            stack.append(tok)
        else:
            b = stack.pop()
            a = stack.pop()
            if tok == "+":
                stack.append(a + b)
            elif tok == "-":
                stack.append(a - b)
            elif tok == "*":
                stack.append(a * b)
            elif tok == "/":
                stack.append(a / b)
    return stack[0]

def calculate(expr):
    tokens = tokenize(expr)
    postfix = infix_to_postfix(tokens)
    return eval_postfix(postfix)
''',

    "queue_scheduler": '''
class Task:
    def __init__(self, name, priority, duration):
        self.name = name
        self.priority = priority
        self.duration = duration
        self.remaining = duration

class Scheduler:
    def __init__(self, quantum=2):
        self.quantum = quantum
        self.queues = {0: [], 1: [], 2: []}
        self.completed = []
        self.time = 0

    def add_task(self, task):
        level = min(task.priority, 2)
        self.queues[level].append(task)

    def get_next(self):
        for level in sorted(self.queues.keys()):
            if self.queues[level]:
                return level, self.queues[level].pop(0)
        return None, None

    def step(self):
        level, task = self.get_next()
        if task is None:
            return False
        run_time = min(self.quantum, task.remaining)
        task.remaining -= run_time
        self.time += run_time
        if task.remaining <= 0:
            self.completed.append((task.name, self.time))
        else:
            next_level = min(level + 1, 2)
            self.queues[next_level].append(task)
        return True

    def run_all(self):
        while self.step():
            pass
        return self.completed

    def average_turnaround(self):
        if not self.completed:
            return 0.0
        total = sum(t for _, t in self.completed)
        return total / len(self.completed)
''',

    "binary_search_tree": '''
class BSTNode:
    def __init__(self, key, value=None):
        self.key = key
        self.value = value
        self.left = None
        self.right = None

class BST:
    def __init__(self):
        self.root = None
        self.count = 0

    def insert(self, key, value=None):
        if self.root is None:
            self.root = BSTNode(key, value)
            self.count += 1
            return
        cur = self.root
        while True:
            if key < cur.key:
                if cur.left is None:
                    cur.left = BSTNode(key, value)
                    self.count += 1
                    return
                cur = cur.left
            elif key > cur.key:
                if cur.right is None:
                    cur.right = BSTNode(key, value)
                    self.count += 1
                    return
                cur = cur.right
            else:
                cur.value = value
                return

    def search(self, key):
        cur = self.root
        while cur is not None:
            if key < cur.key:
                cur = cur.left
            elif key > cur.key:
                cur = cur.right
            else:
                return cur.value
        return None

    def inorder(self):
        result = []
        stack = []
        cur = self.root
        while cur is not None or stack:
            while cur is not None:
                stack.append(cur)
                cur = cur.left
            cur = stack.pop()
            result.append(cur.key)
            cur = cur.right
        return result

    def height(self):
        def _h(node):
            if node is None:
                return 0
            return 1 + max(_h(node.left), _h(node.right))
        return _h(self.root)
''',

    "hash_table": '''
class HashTable:
    def __init__(self, capacity=16):
        self.capacity = capacity
        self.size = 0
        self.buckets = [[] for _ in range(capacity)]
        self.load_threshold = 0.75

    def _hash(self, key):
        h = hash(key) % self.capacity
        return h

    def put(self, key, value):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.buckets[idx]):
            if k == key:
                self.buckets[idx][i] = (key, value)
                return
        self.buckets[idx].append((key, value))
        self.size += 1
        if self.size / self.capacity > self.load_threshold:
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
            for key, val in bucket:
                self.put(key, val)

    def keys(self):
        result = []
        for bucket in self.buckets:
            for k, v in bucket:
                result.append(k)
        return result
''',

    "trie_lookup": '''
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end = False
        self.count = 0

class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        node = self.root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
            node.count += 1
        node.is_end = True

    def search(self, word):
        node = self.root
        for ch in word:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return node.is_end

    def starts_with(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return []
            node = node.children[ch]
        results = []
        self._collect(node, prefix, results)
        return results

    def _collect(self, node, prefix, results):
        if node.is_end:
            results.append(prefix)
        for ch in sorted(node.children):
            self._collect(node.children[ch], prefix + ch, results)

    def count_prefix(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return 0
            node = node.children[ch]
        return node.count
''',

    "graph_bfs": '''
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

def shortest_path(graph, start, end):
    if start == end:
        return [start]
    visited = {start}
    queue = deque([(start, [start])])
    while queue:
        node, path = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor == end:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return []

def connected_components(graph):
    visited = set()
    components = []
    for node in graph:
        if node not in visited:
            component = bfs(graph, node)
            visited.update(component)
            components.append(component)
    return components

def is_bipartite(graph):
    color = {}
    for node in graph:
        if node in color:
            continue
        queue = deque([node])
        color[node] = 0
        while queue:
            cur = queue.popleft()
            for nb in graph.get(cur, []):
                if nb not in color:
                    color[nb] = 1 - color[cur]
                    queue.append(nb)
                elif color[nb] == color[cur]:
                    return False
    return True
''',

    "disjoint_set": '''
class DisjointSet:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.size = [1] * n
        self.count = n

    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        self.size[rx] += self.size[ry]
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.count -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def component_size(self, x):
        return self.size[self.find(x)]

    def components(self):
        groups = {}
        for i in range(len(self.parent)):
            root = self.find(i)
            if root not in groups:
                groups[root] = []
            groups[root].append(i)
        return list(groups.values())

def kruskal(n, edges):
    edges_sorted = sorted(edges, key=lambda e: e[2])
    ds = DisjointSet(n)
    mst = []
    total = 0
    for u, v, w in edges_sorted:
        if ds.union(u, v):
            mst.append((u, v, w))
            total += w
    return mst, total
''',

    "lru_cache": '''
class LRUNode:
    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None

class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.head = LRUNode(0, 0)
        self.tail = LRUNode(0, 0)
        self.head.next = self.tail
        self.tail.prev = self.head

    def _remove(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev

    def _add_front(self, node):
        node.next = self.head.next
        node.prev = self.head
        self.head.next.prev = node
        self.head.next = node

    def get(self, key):
        if key in self.cache:
            node = self.cache[key]
            self._remove(node)
            self._add_front(node)
            return node.value
        return -1

    def put(self, key, value):
        if key in self.cache:
            self._remove(self.cache[key])
        node = LRUNode(key, value)
        self._add_front(node)
        self.cache[key] = node
        if len(self.cache) > self.capacity:
            lru = self.tail.prev
            self._remove(lru)
            del self.cache[lru.key]

    def size(self):
        return len(self.cache)

    def keys_in_order(self):
        result = []
        cur = self.head.next
        while cur != self.tail:
            result.append(cur.key)
            cur = cur.next
        return result
''',

    "circular_buffer": '''
class CircularBuffer:
    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = [None] * capacity
        self.head = 0
        self.tail = 0
        self.size = 0

    def is_empty(self):
        return self.size == 0

    def is_full(self):
        return self.size == self.capacity

    def push(self, item):
        if self.is_full():
            self.head = (self.head + 1) % self.capacity
        else:
            self.size += 1
        self.buffer[self.tail] = item
        self.tail = (self.tail + 1) % self.capacity

    def pop(self):
        if self.is_empty():
            raise IndexError("pop from empty buffer")
        item = self.buffer[self.head]
        self.buffer[self.head] = None
        self.head = (self.head + 1) % self.capacity
        self.size -= 1
        return item

    def peek(self):
        if self.is_empty():
            raise IndexError("peek on empty buffer")
        return self.buffer[self.head]

    def to_list(self):
        result = []
        idx = self.head
        for _ in range(self.size):
            result.append(self.buffer[idx])
            idx = (idx + 1) % self.capacity
        return result

    def clear(self):
        self.buffer = [None] * self.capacity
        self.head = 0
        self.tail = 0
        self.size = 0
''',


    # ── String Processing ────────────────────────────────────────────────

    "csv_parser": '''
def parse_csv_line(line):
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == '"' and i + 1 < len(line) and line[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            elif ch == '"':
                in_quotes = False
            else:
                current.append(ch)
        else:
            if ch == '"':
                in_quotes = True
            elif ch == ",":
                fields.append("".join(current))
                current = []
            else:
                current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields

def parse_csv(text):
    lines = text.strip().split("\\n")
    if not lines:
        return []
    headers = parse_csv_line(lines[0])
    rows = []
    for line in lines[1:]:
        values = parse_csv_line(line)
        row = dict(zip(headers, values))
        rows.append(row)
    return rows

def csv_to_table(text):
    rows = parse_csv(text)
    if not rows:
        return ""
    headers = list(rows[0].keys())
    widths = [len(h) for h in headers]
    for row in rows:
        for i, h in enumerate(headers):
            widths[i] = max(widths[i], len(str(row.get(h, ""))))
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * w for w in widths)
    lines = [header_line, sep]
    for row in rows:
        line = " | ".join(str(row.get(h, "")).ljust(widths[i]) for i, h in enumerate(headers))
        lines.append(line)
    return "\\n".join(lines)
''',

    "json_tokenizer": '''
def json_tokenize(text):
    tokens = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in " \\t\\n\\r":
            i += 1
            continue
        if ch in "{}[]:,":
            tokens.append(("PUNCT", ch))
            i += 1
        elif ch == '"':
            j = i + 1
            buf = []
            while j < len(text):
                if text[j] == "\\\\":
                    buf.append(text[j:j + 2])
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    buf.append(text[j])
                    j += 1
            tokens.append(("STRING", "".join(buf)))
            i = j + 1
        elif ch == "-" or ch.isdigit():
            j = i
            if ch == "-":
                j += 1
            while j < len(text) and text[j].isdigit():
                j += 1
            if j < len(text) and text[j] == ".":
                j += 1
                while j < len(text) and text[j].isdigit():
                    j += 1
            tokens.append(("NUMBER", text[i:j]))
            i = j
        elif text[i:i + 4] == "true":
            tokens.append(("BOOL", "true"))
            i += 4
        elif text[i:i + 5] == "false":
            tokens.append(("BOOL", "false"))
            i += 5
        elif text[i:i + 4] == "null":
            tokens.append(("NULL", "null"))
            i += 4
        else:
            raise ValueError("Unexpected: " + ch)
    return tokens

def count_token_types(text):
    tokens = json_tokenize(text)
    counts = {}
    for ttype, _ in tokens:
        counts[ttype] = counts.get(ttype, 0) + 1
    return counts
''',

    "pattern_matcher": '''
def match_pattern(text, pattern):
    m, n = len(text), len(pattern)
    dp = [[False] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = True
    for j in range(1, n + 1):
        if pattern[j - 1] == "*":
            dp[0][j] = dp[0][j - 1]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if pattern[j - 1] == "*":
                dp[i][j] = dp[i - 1][j] or dp[i][j - 1]
            elif pattern[j - 1] == "?" or pattern[j - 1] == text[i - 1]:
                dp[i][j] = dp[i - 1][j - 1]
    return dp[m][n]

def find_all_matches(text, sub):
    positions = []
    start = 0
    while True:
        idx = text.find(sub, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions

def glob_match(path, pattern):
    parts = pattern.split("/")
    path_parts = path.split("/")
    if len(parts) != len(path_parts):
        return False
    for pp, pat in zip(path_parts, parts):
        if not match_pattern(pp, pat):
            return False
    return True

def replace_pattern(text, old, new):
    result = []
    i = 0
    while i < len(text):
        if text[i:i + len(old)] == old:
            result.append(new)
            i += len(old)
        else:
            result.append(text[i])
            i += 1
    return "".join(result)
''',

    "template_engine": '''
def render_template(template, context):
    result = []
    i = 0
    while i < len(template):
        if template[i:i + 2] == "{{":
            end = template.find("}}", i + 2)
            if end == -1:
                result.append(template[i])
                i += 1
                continue
            expr = template[i + 2:end].strip()
            value = resolve_var(expr, context)
            result.append(str(value))
            i = end + 2
        elif template[i:i + 2] == "{%":
            end = template.find("%}", i + 2)
            if end == -1:
                result.append(template[i])
                i += 1
                continue
            directive = template[i + 2:end].strip()
            i = end + 2
            if directive.startswith("if "):
                cond = directive[3:].strip()
                block_end = template.find("{% endif %}", i)
                block = template[i:block_end]
                if resolve_var(cond, context):
                    result.append(render_template(block, context))
                i = block_end + len("{% endif %}")
        else:
            result.append(template[i])
            i += 1
    return "".join(result)

def resolve_var(name, context):
    parts = name.split(".")
    val = context
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part, "")
        else:
            val = getattr(val, part, "")
    return val

def escape_html(text):
    replacements = [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")]
    for old, new in replacements:
        text = text.replace(old, new)
    return text
''',

    "slug_generator": '''
import re

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^\\w\\s-]", "", text)
    text = re.sub(r"[\\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text

def unique_slug(text, existing):
    base = slugify(text)
    if base not in existing:
        return base
    counter = 1
    while True:
        candidate = base + "-" + str(counter)
        if candidate not in existing:
            return candidate
        counter += 1

def slug_to_title(slug):
    words = slug.split("-")
    return " ".join(w.capitalize() for w in words)

def batch_slugify(titles):
    seen = set()
    result = []
    for title in titles:
        slug = unique_slug(title, seen)
        seen.add(slug)
        result.append((title, slug))
    return result

def is_valid_slug(slug):
    if not slug:
        return False
    if slug.startswith("-") or slug.endswith("-"):
        return False
    if "--" in slug:
        return False
    return all(c.isalnum() or c == "-" for c in slug)
''',

    "email_validator": '''
import re

def validate_email(email):
    errors = []
    if not email or not isinstance(email, str):
        return False, ["Email is empty or not a string"]
    parts = email.split("@")
    if len(parts) != 2:
        errors.append("Must contain exactly one @ symbol")
        return False, errors
    local, domain = parts
    if not local:
        errors.append("Local part is empty")
    if len(local) > 64:
        errors.append("Local part exceeds 64 characters")
    if not domain:
        errors.append("Domain part is empty")
    if len(domain) > 253:
        errors.append("Domain exceeds 253 characters")
    domain_parts = domain.split(".")
    if len(domain_parts) < 2:
        errors.append("Domain must have at least two parts")
    for part in domain_parts:
        if not part:
            errors.append("Domain has empty label")
        elif not all(c.isalnum() or c == "-" for c in part):
            errors.append("Domain label has invalid characters")
    if errors:
        return False, errors
    return True, []

def normalize_email(email):
    local, domain = email.split("@")
    domain = domain.lower()
    if "+" in local:
        local = local.split("+")[0]
    return local + "@" + domain

def batch_validate(emails):
    results = {}
    for email in emails:
        valid, errors = validate_email(email)
        results[email] = {"valid": valid, "errors": errors}
    return results
''',

    "markdown_headers": '''
def parse_headers(text):
    headers = []
    for line in text.split("\\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = 0
            while level < len(stripped) and stripped[level] == "#":
                level += 1
            title = stripped[level:].strip()
            if title:
                headers.append({"level": level, "title": title})
    return headers

def build_toc(headers):
    lines = []
    for h in headers:
        indent = "  " * (h["level"] - 1)
        anchor = h["title"].lower().replace(" ", "-")
        anchor = "".join(c for c in anchor if c.isalnum() or c == "-")
        lines.append(indent + "- [" + h["title"] + "](#" + anchor + ")")
    return "\\n".join(lines)

def renumber_headers(text, offset=0):
    lines = text.split("\\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            level = 0
            while level < len(stripped) and stripped[level] == "#":
                level += 1
            new_level = min(level + offset, 6)
            new_level = max(new_level, 1)
            title = stripped[level:].strip()
            result.append("#" * new_level + " " + title)
        else:
            result.append(line)
    return "\\n".join(result)

def extract_sections(text):
    headers = parse_headers(text)
    sections = {}
    lines = text.split("\\n")
    for i, h in enumerate(headers):
        start = next(j for j, l in enumerate(lines) if h["title"] in l)
        if i + 1 < len(headers):
            end = next(j for j, l in enumerate(lines) if headers[i + 1]["title"] in l)
        else:
            end = len(lines)
        sections[h["title"]] = "\\n".join(lines[start:end])
    return sections
''',

    "text_wrapper": '''
def wrap_text(text, width=72):
    words = text.split()
    lines = []
    current = []
    current_len = 0
    for word in words:
        if current_len + len(word) + (1 if current else 0) > width:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + (1 if len(current) > 1 else 0)
    if current:
        lines.append(" ".join(current))
    return "\\n".join(lines)

def indent_text(text, prefix="    "):
    lines = text.split("\\n")
    return "\\n".join(prefix + line if line.strip() else line for line in lines)

def center_text(text, width=72):
    lines = text.split("\\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        padding = max(0, (width - len(stripped)) // 2)
        result.append(" " * padding + stripped)
    return "\\n".join(result)

def justify_text(text, width=72):
    words = text.split()
    lines = []
    current = []
    current_len = 0
    for word in words:
        if current_len + len(word) + len(current) > width:
            if len(current) == 1:
                lines.append(current[0])
            else:
                total_spaces = width - sum(len(w) for w in current)
                gaps = len(current) - 1
                base_space = total_spaces // gaps
                extra = total_spaces % gaps
                parts = []
                for i, w in enumerate(current[:-1]):
                    parts.append(w)
                    spaces = base_space + (1 if i < extra else 0)
                    parts.append(" " * spaces)
                parts.append(current[-1])
                lines.append("".join(parts))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word)
    if current:
        lines.append(" ".join(current))
    return "\\n".join(lines)
''',

    "diff_lines": '''
def compute_lcs(a, b):
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = []
    i, j = m, n
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            lcs.append(a[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(lcs))

def diff_lines(old_text, new_text):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    lcs = compute_lcs(old_lines, new_lines)
    result = []
    oi, ni, li = 0, 0, 0
    while li < len(lcs):
        while oi < len(old_lines) and old_lines[oi] != lcs[li]:
            result.append("- " + old_lines[oi])
            oi += 1
        while ni < len(new_lines) and new_lines[ni] != lcs[li]:
            result.append("+ " + new_lines[ni])
            ni += 1
        result.append("  " + lcs[li])
        oi += 1
        ni += 1
        li += 1
    while oi < len(old_lines):
        result.append("- " + old_lines[oi])
        oi += 1
    while ni < len(new_lines):
        result.append("+ " + new_lines[ni])
        ni += 1
    return result

def count_changes(diff_result):
    adds = sum(1 for l in diff_result if l.startswith("+ "))
    deletes = sum(1 for l in diff_result if l.startswith("- "))
    return {"additions": adds, "deletions": deletes, "total": adds + deletes}
''',

    "base64_codec": '''
import string

BASE64_CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits + "+/"

def base64_encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    result = []
    padding = 0
    i = 0
    while i < len(data):
        chunk = data[i:i + 3]
        i += 3
        n = len(chunk)
        bits = 0
        for b in chunk:
            bits = (bits << 8) | b
        if n == 3:
            result.append(BASE64_CHARS[(bits >> 18) & 0x3F])
            result.append(BASE64_CHARS[(bits >> 12) & 0x3F])
            result.append(BASE64_CHARS[(bits >> 6) & 0x3F])
            result.append(BASE64_CHARS[bits & 0x3F])
        elif n == 2:
            bits <<= 8
            result.append(BASE64_CHARS[(bits >> 18) & 0x3F])
            result.append(BASE64_CHARS[(bits >> 12) & 0x3F])
            result.append(BASE64_CHARS[(bits >> 6) & 0x3F])
            result.append("=")
        elif n == 1:
            bits <<= 16
            result.append(BASE64_CHARS[(bits >> 18) & 0x3F])
            result.append(BASE64_CHARS[(bits >> 12) & 0x3F])
            result.append("=")
            result.append("=")
    return "".join(result)

def base64_decode(encoded):
    lookup = {c: i for i, c in enumerate(BASE64_CHARS)}
    encoded = encoded.rstrip("=")
    bits = 0
    nbits = 0
    output = bytearray()
    for ch in encoded:
        bits = (bits << 6) | lookup[ch]
        nbits += 6
        if nbits >= 8:
            nbits -= 8
            output.append((bits >> nbits) & 0xFF)
    return bytes(output)

def is_valid_base64(text):
    import re
    return bool(re.match(r"^[A-Za-z0-9+/]*={0,2}$", text)) and len(text) % 4 == 0
''',


    # ── Math/Numeric ─────────────────────────────────────────────────────

    "matrix_ops": '''
def matrix_add(a, b):
    rows = len(a)
    cols = len(a[0])
    return [[a[i][j] + b[i][j] for j in range(cols)] for i in range(rows)]

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

def transpose(matrix):
    rows = len(matrix)
    cols = len(matrix[0])
    return [[matrix[i][j] for i in range(rows)] for j in range(cols)]

def identity(n):
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]

def determinant(matrix):
    n = len(matrix)
    if n == 1:
        return matrix[0][0]
    if n == 2:
        return matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    det = 0
    for j in range(n):
        minor = [row[:j] + row[j + 1:] for row in matrix[1:]]
        det += ((-1) ** j) * matrix[0][j] * determinant(minor)
    return det
''',

    "statistics_calc": '''
import math

def mean(data):
    if not data:
        raise ValueError("Empty dataset")
    return sum(data) / len(data)

def variance(data):
    avg = mean(data)
    return sum((x - avg) ** 2 for x in data) / len(data)

def std_dev(data):
    return math.sqrt(variance(data))

def median(data):
    s = sorted(data)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0

def percentile(data, p):
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])

def covariance(x, y):
    if len(x) != len(y):
        raise ValueError("Datasets must have same length")
    mx = mean(x)
    my = mean(y)
    return sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / len(x)

def correlation(x, y):
    cov = covariance(x, y)
    sx = std_dev(x)
    sy = std_dev(y)
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)
''',

    "polynomial_eval": '''
def poly_eval(coeffs, x):
    result = 0
    for i, c in enumerate(coeffs):
        result += c * (x ** i)
    return result

def poly_eval_horner(coeffs, x):
    result = 0
    for c in reversed(coeffs):
        result = result * x + c
    return result

def poly_add(a, b):
    n = max(len(a), len(b))
    result = [0] * n
    for i in range(len(a)):
        result[i] += a[i]
    for i in range(len(b)):
        result[i] += b[i]
    return result

def poly_multiply(a, b):
    n = len(a) + len(b) - 1
    result = [0] * n
    for i in range(len(a)):
        for j in range(len(b)):
            result[i + j] += a[i] * b[j]
    return result

def poly_derivative(coeffs):
    if len(coeffs) <= 1:
        return [0]
    return [i * coeffs[i] for i in range(1, len(coeffs))]

def poly_to_string(coeffs):
    terms = []
    for i, c in enumerate(coeffs):
        if c == 0:
            continue
        if i == 0:
            terms.append(str(c))
        elif i == 1:
            terms.append(str(c) + "x")
        else:
            terms.append(str(c) + "x^" + str(i))
    return " + ".join(terms) if terms else "0"
''',

    "prime_sieve": '''
def sieve_of_eratosthenes(limit):
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    i = 2
    while i * i <= limit:
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
        i += 1
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

def prime_factors(n):
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

def nth_prime(n):
    count = 0
    candidate = 1
    while count < n:
        candidate += 1
        if is_prime(candidate):
            count += 1
    return candidate
''',

    "gcd_extended": '''
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x1, y1 = extended_gcd(b % a, a)
    x = y1 - (b // a) * x1
    y = x1
    return g, x, y

def lcm(a, b):
    return abs(a * b) // gcd(a, b)

def mod_inverse(a, m):
    g, x, _ = extended_gcd(a % m, m)
    if g != 1:
        raise ValueError("Inverse does not exist")
    return x % m

def chinese_remainder(remainders, moduli):
    total = 0
    prod = 1
    for m in moduli:
        prod *= m
    for r, m in zip(remainders, moduli):
        p = prod // m
        total += r * mod_inverse(p, m) * p
    return total % prod

def solve_linear_congruence(a, b, m):
    g, x, _ = extended_gcd(a, m)
    if b % g != 0:
        return []
    x0 = (x * (b // g)) % m
    solutions = []
    step = m // g
    for i in range(g):
        solutions.append((x0 + i * step) % m)
    return sorted(solutions)
''',

    "fraction_math": '''
def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a

class Fraction:
    def __init__(self, num, den=1):
        if den == 0:
            raise ZeroDivisionError("Denominator cannot be zero")
        if den < 0:
            num, den = -num, -den
        g = _gcd(abs(num), den)
        self.num = num // g
        self.den = den // g

    def __add__(self, other):
        return Fraction(
            self.num * other.den + other.num * self.den,
            self.den * other.den
        )

    def __sub__(self, other):
        return Fraction(
            self.num * other.den - other.num * self.den,
            self.den * other.den
        )

    def __mul__(self, other):
        return Fraction(self.num * other.num, self.den * other.den)

    def __truediv__(self, other):
        if other.num == 0:
            raise ZeroDivisionError("Division by zero fraction")
        return Fraction(self.num * other.den, self.den * other.num)

    def __eq__(self, other):
        return self.num == other.num and self.den == other.den

    def __repr__(self):
        if self.den == 1:
            return str(self.num)
        return str(self.num) + "/" + str(self.den)

    def to_float(self):
        return self.num / self.den

def sum_fractions(fractions):
    result = Fraction(0)
    for f in fractions:
        result = result + f
    return result
''',

    "newton_sqrt": '''
import math

def newton_sqrt(n, tolerance=1e-12):
    if n < 0:
        raise ValueError("Cannot compute sqrt of negative number")
    if n == 0:
        return 0.0
    guess = n / 2.0
    while True:
        next_guess = (guess + n / guess) / 2.0
        if abs(next_guess - guess) < tolerance:
            return next_guess
        guess = next_guess

def nth_root(n, k, tolerance=1e-12):
    if n == 0:
        return 0.0
    guess = n / float(k)
    while True:
        next_guess = ((k - 1) * guess + n / (guess ** (k - 1))) / k
        if abs(next_guess - guess) < tolerance:
            return next_guess
        guess = next_guess

def is_perfect_square(n):
    if n < 0:
        return False
    root = int(newton_sqrt(n) + 0.5)
    return root * root == n

def integer_sqrt(n):
    if n < 0:
        raise ValueError("Negative input")
    if n == 0:
        return 0
    x = n
    y = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x

def compare_with_math(values):
    results = []
    for v in values:
        ours = newton_sqrt(v)
        theirs = math.sqrt(v)
        diff = abs(ours - theirs)
        results.append({"value": v, "newton": ours, "math": theirs, "diff": diff})
    return results
''',

    "linear_regression": '''
def linear_regression(x, y):
    n = len(x)
    if n != len(y) or n < 2:
        raise ValueError("Need at least 2 matching data points")
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        raise ValueError("All x values are identical")
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept

def predict(slope, intercept, x):
    return slope * x + intercept

def r_squared(x, y, slope, intercept):
    y_mean = sum(y) / len(y)
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    ss_res = sum((yi - predict(slope, intercept, xi)) ** 2 for xi, yi in zip(x, y))
    if ss_tot == 0:
        return 1.0
    return 1.0 - ss_res / ss_tot

def residuals(x, y, slope, intercept):
    return [yi - predict(slope, intercept, xi) for xi, yi in zip(x, y)]

def confidence_interval(x, y, slope, intercept, confidence=0.95):
    n = len(x)
    res = residuals(x, y, slope, intercept)
    mse = sum(r * r for r in res) / (n - 2)
    import math
    se = math.sqrt(mse)
    x_mean = sum(x) / n
    ss_xx = sum((xi - x_mean) ** 2 for xi in x)
    se_slope = se / math.sqrt(ss_xx)
    return slope - 1.96 * se_slope, slope + 1.96 * se_slope
''',

    "combinations_perms": '''
def factorial(n):
    if n <= 1:
        return 1
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

def permutations_count(n, r):
    if r > n:
        return 0
    return factorial(n) // factorial(n - r)

def combinations_count(n, r):
    if r > n:
        return 0
    return factorial(n) // (factorial(r) * factorial(n - r))

def generate_permutations(items):
    if len(items) <= 1:
        return [list(items)]
    result = []
    for i, item in enumerate(items):
        rest = items[:i] + items[i + 1:]
        for perm in generate_permutations(rest):
            result.append([item] + perm)
    return result

def generate_combinations(items, r):
    if r == 0:
        return [[]]
    if len(items) < r:
        return []
    result = []
    first = items[0]
    rest = items[1:]
    with_first = generate_combinations(rest, r - 1)
    for combo in with_first:
        result.append([first] + combo)
    without_first = generate_combinations(rest, r)
    result.extend(without_first)
    return result

def pascals_triangle(n):
    triangle = [[1]]
    for i in range(1, n):
        row = [1]
        for j in range(1, i):
            row.append(triangle[i - 1][j - 1] + triangle[i - 1][j])
        row.append(1)
        triangle.append(row)
    return triangle
''',

    "complex_numbers": '''
import math

class Complex:
    def __init__(self, real, imag=0.0):
        self.real = float(real)
        self.imag = float(imag)

    def __add__(self, other):
        return Complex(self.real + other.real, self.imag + other.imag)

    def __sub__(self, other):
        return Complex(self.real - other.real, self.imag - other.imag)

    def __mul__(self, other):
        r = self.real * other.real - self.imag * other.imag
        i = self.real * other.imag + self.imag * other.real
        return Complex(r, i)

    def __truediv__(self, other):
        denom = other.real ** 2 + other.imag ** 2
        r = (self.real * other.real + self.imag * other.imag) / denom
        i = (self.imag * other.real - self.real * other.imag) / denom
        return Complex(r, i)

    def magnitude(self):
        return math.sqrt(self.real ** 2 + self.imag ** 2)

    def phase(self):
        return math.atan2(self.imag, self.real)

    def conjugate(self):
        return Complex(self.real, -self.imag)

    def __repr__(self):
        if self.imag >= 0:
            return str(self.real) + "+" + str(self.imag) + "j"
        return str(self.real) + str(self.imag) + "j"

def from_polar(r, theta):
    return Complex(r * math.cos(theta), r * math.sin(theta))

def roots_of_unity(n):
    result = []
    for k in range(n):
        angle = 2 * math.pi * k / n
        result.append(from_polar(1.0, angle))
    return result
''',


    # ── File/IO Simulation ───────────────────────────────────────────────

    "config_parser": '''
def parse_config(text):
    config = {}
    current_section = None
    for line in text.split("\\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            config[current_section] = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.lower() in ("true", "yes"):
                value = True
            elif value.lower() in ("false", "no"):
                value = False
            elif value.isdigit():
                value = int(value)
            if current_section:
                config[current_section][key] = value
            else:
                config[key] = value
    return config

def get_nested(config, path, default=None):
    parts = path.split(".")
    val = config
    for part in parts:
        if isinstance(val, dict) and part in val:
            val = val[part]
        else:
            return default
    return val

def merge_configs(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result

def serialize_config(config, prefix=""):
    lines = []
    for key, value in sorted(config.items()):
        if isinstance(value, dict):
            lines.append("[" + key + "]")
            for k, v in sorted(value.items()):
                lines.append(k + " = " + str(v))
            lines.append("")
        else:
            lines.append(key + " = " + str(value))
    return "\\n".join(lines)
''',

    "log_analyzer": '''
import re

def parse_log_line(line):
    pattern = r"(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}) \\[(\\w+)\\] (.+)"
    match = re.match(pattern, line)
    if match:
        return {
            "timestamp": match.group(1),
            "level": match.group(2),
            "message": match.group(3),
        }
    return None

def analyze_logs(lines):
    entries = []
    for line in lines:
        parsed = parse_log_line(line)
        if parsed:
            entries.append(parsed)
    level_counts = {}
    for entry in entries:
        lvl = entry["level"]
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
    return {"entries": entries, "level_counts": level_counts, "total": len(entries)}

def filter_by_level(entries, level):
    return [e for e in entries if e["level"] == level]

def search_logs(entries, keyword):
    return [e for e in entries if keyword.lower() in e["message"].lower()]

def error_summary(entries):
    errors = filter_by_level(entries, "ERROR")
    messages = {}
    for e in errors:
        msg = e["message"]
        messages[msg] = messages.get(msg, 0) + 1
    sorted_msgs = sorted(messages.items(), key=lambda x: -x[1])
    return sorted_msgs

def time_range(entries):
    if not entries:
        return None, None
    timestamps = [e["timestamp"] for e in entries]
    return min(timestamps), max(timestamps)
''',

    "csv_writer": '''
def escape_csv_field(value):
    s = str(value)
    needs_quote = False
    if "," in s or '"' in s or "\\n" in s:
        needs_quote = True
    if needs_quote:
        s = s.replace('"', '""')
        s = '"' + s + '"'
    return s

def write_csv_row(fields):
    return ",".join(escape_csv_field(f) for f in fields)

def write_csv(headers, rows):
    lines = [write_csv_row(headers)]
    for row in rows:
        if isinstance(row, dict):
            values = [row.get(h, "") for h in headers]
        else:
            values = list(row)
        lines.append(write_csv_row(values))
    return "\\n".join(lines)

def dicts_to_csv(dicts):
    if not dicts:
        return ""
    all_keys = []
    seen = set()
    for d in dicts:
        for k in d:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)
    return write_csv(all_keys, dicts)

def csv_stats(csv_text, column):
    lines = csv_text.strip().split("\\n")
    if len(lines) < 2:
        return {}
    headers = lines[0].split(",")
    col_idx = headers.index(column)
    values = []
    for line in lines[1:]:
        fields = line.split(",")
        try:
            values.append(float(fields[col_idx]))
        except (ValueError, IndexError):
            continue
    if not values:
        return {}
    return {
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
        "count": len(values),
    }
''',

    "path_resolver": '''
import os

def normalize_path(path):
    parts = path.replace("\\\\", "/").split("/")
    normalized = []
    for part in parts:
        if part == "." or part == "":
            continue
        elif part == "..":
            if normalized and normalized[-1] != "..":
                normalized.pop()
            else:
                normalized.append(part)
        else:
            normalized.append(part)
    result = "/".join(normalized)
    if path.startswith("/"):
        result = "/" + result
    return result or "."

def resolve_relative(base, relative):
    if relative.startswith("/"):
        return normalize_path(relative)
    combined = base.rstrip("/") + "/" + relative
    return normalize_path(combined)

def split_extension(path):
    name = path.rsplit("/", 1)[-1]
    if "." in name and not name.startswith("."):
        base, ext = name.rsplit(".", 1)
        return base, "." + ext
    return name, ""

def common_prefix(paths):
    if not paths:
        return ""
    split = [p.split("/") for p in paths]
    prefix = []
    for parts in zip(*split):
        if len(set(parts)) == 1:
            prefix.append(parts[0])
        else:
            break
    return "/".join(prefix)

def make_relative(path, base):
    path_parts = normalize_path(path).split("/")
    base_parts = normalize_path(base).split("/")
    common_len = 0
    for a, b in zip(path_parts, base_parts):
        if a == b:
            common_len += 1
        else:
            break
    ups = len(base_parts) - common_len
    remainder = path_parts[common_len:]
    parts = [".."] * ups + remainder
    return "/".join(parts) or "."
''',

    "file_monitor": '''
import hashlib

class FileMonitor:
    def __init__(self):
        self.snapshots = {}
        self.changes = []

    def take_snapshot(self, file_dict):
        snapshot = {}
        for path, content in file_dict.items():
            h = hashlib.md5(content.encode("utf-8")).hexdigest()
            snapshot[path] = {"hash": h, "size": len(content)}
        self.snapshots[len(self.snapshots)] = snapshot
        return len(self.snapshots) - 1

    def compare_snapshots(self, id_a, id_b):
        a = self.snapshots.get(id_a, {})
        b = self.snapshots.get(id_b, {})
        changes = []
        all_paths = set(a.keys()) | set(b.keys())
        for path in sorted(all_paths):
            if path not in a:
                changes.append({"path": path, "type": "added"})
            elif path not in b:
                changes.append({"path": path, "type": "deleted"})
            elif a[path]["hash"] != b[path]["hash"]:
                changes.append({"path": path, "type": "modified"})
        return changes

    def watch_changes(self, old_files, new_files):
        id_a = self.take_snapshot(old_files)
        id_b = self.take_snapshot(new_files)
        return self.compare_snapshots(id_a, id_b)

    def summary(self, changes):
        counts = {"added": 0, "deleted": 0, "modified": 0}
        for c in changes:
            counts[c["type"]] += 1
        return counts

    def filter_changes(self, changes, change_type):
        return [c for c in changes if c["type"] == change_type]
''',

    "temp_cleaner": '''
import time as _time

class TempCleaner:
    def __init__(self, max_age_seconds=3600):
        self.max_age = max_age_seconds
        self.files = {}
        self.deleted = []

    def register(self, path, created_at=None):
        if created_at is None:
            created_at = _time.time()
        self.files[path] = {
            "created_at": created_at,
            "size": 0,
            "locked": False,
        }

    def lock(self, path):
        if path in self.files:
            self.files[path]["locked"] = True

    def unlock(self, path):
        if path in self.files:
            self.files[path]["locked"] = False

    def scan(self, current_time=None):
        if current_time is None:
            current_time = _time.time()
        expired = []
        for path, info in self.files.items():
            age = current_time - info["created_at"]
            if age > self.max_age and not info["locked"]:
                expired.append(path)
        return expired

    def clean(self, current_time=None):
        expired = self.scan(current_time)
        for path in expired:
            del self.files[path]
            self.deleted.append(path)
        return expired

    def stats(self):
        return {
            "active": len(self.files),
            "deleted": len(self.deleted),
            "locked": sum(1 for f in self.files.values() if f["locked"]),
        }

    def all_paths(self):
        return sorted(self.files.keys())
''',

    "line_counter": '''
TRIPLE_DQ = chr(34) * 3
TRIPLE_SQ = chr(39) * 3

def count_lines(text):
    if not text:
        return {"total": 0, "code": 0, "blank": 0, "comment": 0}
    lines = text.split("\\n")
    total = len(lines)
    blank = 0
    comment = 0
    code = 0
    in_multiline = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
            continue
        if in_multiline:
            comment += 1
            if stripped.endswith(TRIPLE_DQ) or stripped.endswith(TRIPLE_SQ):
                in_multiline = False
            continue
        if stripped.startswith(TRIPLE_DQ) or stripped.startswith(TRIPLE_SQ):
            comment += 1
            if not (stripped.count(TRIPLE_DQ) >= 2 or stripped.count(TRIPLE_SQ) >= 2):
                in_multiline = True
            continue
        if stripped.startswith("#"):
            comment += 1
        else:
            code += 1
    return {"total": total, "code": code, "blank": blank, "comment": comment}

def count_by_extension(files_dict):
    stats = {}
    for path, content in files_dict.items():
        ext = path.rsplit(".", 1)[-1] if "." in path else "none"
        if ext not in stats:
            stats[ext] = {"files": 0, "code": 0, "blank": 0, "comment": 0}
        counts = count_lines(content)
        stats[ext]["files"] += 1
        stats[ext]["code"] += counts["code"]
        stats[ext]["blank"] += counts["blank"]
        stats[ext]["comment"] += counts["comment"]
    return stats
''',

    "checksum_calc": '''
import hashlib

def md5_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()

def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def crc32(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
    return crc ^ 0xFFFFFFFF

def adler32(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    a = 1
    b = 0
    mod = 65521
    for byte in data:
        a = (a + byte) % mod
        b = (b + a) % mod
    return (b << 16) | a

def verify_checksum(data, expected, algorithm="md5"):
    if algorithm == "md5":
        actual = md5_hex(data)
    elif algorithm == "sha256":
        actual = sha256_hex(data)
    else:
        raise ValueError("Unknown algorithm: " + algorithm)
    return actual == expected

def file_checksums(content):
    return {
        "md5": md5_hex(content),
        "sha256": sha256_hex(content),
        "crc32": hex(crc32(content)),
        "adler32": hex(adler32(content)),
    }
''',

    "env_loader": '''
def parse_env(text):
    env = {}
    for line in text.split("\\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or \\
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        env[key] = value
    return env

def expand_vars(env):
    expanded = dict(env)
    changed = True
    max_iter = 10
    iteration = 0
    while changed and iteration < max_iter:
        changed = False
        iteration += 1
        for key in expanded:
            value = expanded[key]
            new_value = value
            for other_key, other_val in expanded.items():
                placeholder = "${" + other_key + "}"
                if placeholder in new_value:
                    new_value = new_value.replace(placeholder, other_val)
            if new_value != value:
                expanded[key] = new_value
                changed = True
    return expanded

def merge_env(base, override):
    result = dict(base)
    result.update(override)
    return result

def validate_env(env, required_keys):
    missing = [k for k in required_keys if k not in env]
    empty = [k for k in required_keys if k in env and not env[k]]
    return {"missing": missing, "empty": empty, "valid": not missing}

def env_to_string(env):
    lines = []
    for key in sorted(env.keys()):
        value = env[key]
        if " " in value or "=" in value:
            value = '"' + value + '"'
        lines.append(key + "=" + value)
    return "\\n".join(lines)
''',

    "backup_rotator": '''
class BackupRotator:
    def __init__(self, max_backups=5):
        self.max_backups = max_backups
        self.backups = []

    def add_backup(self, name, size, timestamp):
        self.backups.append({
            "name": name,
            "size": size,
            "timestamp": timestamp,
        })
        self.backups.sort(key=lambda b: b["timestamp"], reverse=True)

    def rotate(self):
        removed = []
        while len(self.backups) > self.max_backups:
            removed.append(self.backups.pop())
        return removed

    def total_size(self):
        return sum(b["size"] for b in self.backups)

    def latest(self):
        if not self.backups:
            return None
        return self.backups[0]

    def oldest(self):
        if not self.backups:
            return None
        return self.backups[-1]

    def find_by_name(self, name):
        for b in self.backups:
            if b["name"] == name:
                return b
        return None

    def remove_by_name(self, name):
        self.backups = [b for b in self.backups if b["name"] != name]

    def list_backups(self):
        return [b["name"] for b in self.backups]

    def prune_by_size(self, max_total_size):
        removed = []
        while self.total_size() > max_total_size and self.backups:
            removed.append(self.backups.pop())
        return removed
''',


    # ── Web/HTTP ─────────────────────────────────────────────────────────

    "url_parser": '''
def parse_url(url):
    result = {"scheme": "", "host": "", "port": None, "path": "/", "query": "", "fragment": ""}
    remaining = url
    if "://" in remaining:
        result["scheme"], remaining = remaining.split("://", 1)
    if "#" in remaining:
        remaining, result["fragment"] = remaining.rsplit("#", 1)
    if "?" in remaining:
        remaining, result["query"] = remaining.split("?", 1)
    if "/" in remaining:
        host_part, path = remaining.split("/", 1)
        result["path"] = "/" + path
    else:
        host_part = remaining
    if ":" in host_part:
        host, port = host_part.rsplit(":", 1)
        result["host"] = host
        result["port"] = int(port)
    else:
        result["host"] = host_part
    return result

def build_url(parts):
    url = ""
    if parts.get("scheme"):
        url += parts["scheme"] + "://"
    url += parts.get("host", "")
    if parts.get("port"):
        url += ":" + str(parts["port"])
    url += parts.get("path", "/")
    if parts.get("query"):
        url += "?" + parts["query"]
    if parts.get("fragment"):
        url += "#" + parts["fragment"]
    return url

def is_absolute_url(url):
    return "://" in url

def join_url(base, relative):
    if is_absolute_url(relative):
        return relative
    parsed = parse_url(base)
    if relative.startswith("/"):
        parsed["path"] = relative
    else:
        base_path = parsed["path"].rsplit("/", 1)[0]
        parsed["path"] = base_path + "/" + relative
    parsed["query"] = ""
    parsed["fragment"] = ""
    return build_url(parsed)
''',

    "query_string": '''
def parse_query_string(qs):
    params = {}
    if not qs:
        return params
    if qs.startswith("?"):
        qs = qs[1:]
    for pair in qs.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            key = url_decode(key)
            value = url_decode(value)
        else:
            key = url_decode(pair)
            value = ""
        if key in params:
            if isinstance(params[key], list):
                params[key].append(value)
            else:
                params[key] = [params[key], value]
        else:
            params[key] = value
    return params

def url_decode(s):
    result = []
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            hex_val = s[i + 1:i + 3]
            result.append(chr(int(hex_val, 16)))
            i += 3
        elif s[i] == "+":
            result.append(" ")
            i += 1
        else:
            result.append(s[i])
            i += 1
    return "".join(result)

def url_encode(s):
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")
    result = []
    for ch in s:
        if ch in safe:
            result.append(ch)
        else:
            for byte in ch.encode("utf-8"):
                result.append("%" + format(byte, "02X"))
    return "".join(result)

def build_query_string(params):
    parts = []
    for key, value in params.items():
        if isinstance(value, list):
            for v in value:
                parts.append(url_encode(key) + "=" + url_encode(str(v)))
        else:
            parts.append(url_encode(key) + "=" + url_encode(str(value)))
    return "&".join(parts)
''',

    "cookie_manager": '''
class Cookie:
    def __init__(self, name, value, path="/", domain="", max_age=None, secure=False, http_only=False):
        self.name = name
        self.value = value
        self.path = path
        self.domain = domain
        self.max_age = max_age
        self.secure = secure
        self.http_only = http_only

class CookieJar:
    def __init__(self):
        self.cookies = {}

    def set_cookie(self, cookie):
        key = (cookie.name, cookie.domain, cookie.path)
        self.cookies[key] = cookie

    def get_cookie(self, name, domain="", path="/"):
        key = (name, domain, path)
        return self.cookies.get(key)

    def delete_cookie(self, name, domain="", path="/"):
        key = (name, domain, path)
        if key in self.cookies:
            del self.cookies[key]

    def get_cookies_for_url(self, domain, path):
        matching = []
        for key, cookie in self.cookies.items():
            if domain.endswith(cookie.domain) and path.startswith(cookie.path):
                matching.append(cookie)
        return matching

    def to_header(self, domain, path):
        cookies = self.get_cookies_for_url(domain, path)
        pairs = [c.name + "=" + c.value for c in cookies]
        return "; ".join(pairs)

    def parse_set_cookie(self, header):
        parts = header.split(";")
        name_val = parts[0].strip()
        name, value = name_val.split("=", 1)
        cookie = Cookie(name.strip(), value.strip())
        for part in parts[1:]:
            part = part.strip().lower()
            if part.startswith("path="):
                cookie.path = part[5:]
            elif part.startswith("domain="):
                cookie.domain = part[7:]
            elif part == "secure":
                cookie.secure = True
            elif part == "httponly":
                cookie.http_only = True
        self.set_cookie(cookie)
        return cookie

    def count(self):
        return len(self.cookies)
''',

    "route_matcher": '''
def compile_route(pattern):
    parts = pattern.strip("/").split("/")
    regex_parts = []
    param_names = []
    for part in parts:
        if part.startswith(":"):
            param_names.append(part[1:])
            regex_parts.append("([^/]+)")
        elif part == "*":
            param_names.append("wildcard")
            regex_parts.append("(.+)")
        else:
            regex_parts.append(part)
    return {
        "pattern": pattern,
        "parts": parts,
        "param_names": param_names,
        "n_parts": len(parts),
    }

def match_route(compiled, path):
    path_parts = path.strip("/").split("/")
    route_parts = compiled["parts"]
    if len(path_parts) != len(route_parts):
        has_wildcard = any(p == "*" for p in route_parts)
        if not has_wildcard:
            return None
    params = {}
    for i, rpart in enumerate(route_parts):
        if rpart == "*":
            params["wildcard"] = "/".join(path_parts[i:])
            return params
        if i >= len(path_parts):
            return None
        if rpart.startswith(":"):
            params[rpart[1:]] = path_parts[i]
        elif rpart != path_parts[i]:
            return None
    return params

class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, handler):
        compiled = compile_route(pattern)
        self.routes.append({"method": method, "compiled": compiled, "handler": handler})

    def match(self, method, path):
        for route in self.routes:
            if route["method"] != method:
                continue
            params = match_route(route["compiled"], path)
            if params is not None:
                return route["handler"], params
        return None, {}

    def routes_list(self):
        return [(r["method"], r["compiled"]["pattern"]) for r in self.routes]
''',

    "form_validator": '''
class FormValidator:
    def __init__(self):
        self.rules = {}
        self.errors = {}

    def add_rule(self, field, rule_type, **kwargs):
        if field not in self.rules:
            self.rules[field] = []
        self.rules[field].append({"type": rule_type, **kwargs})

    def validate(self, data):
        self.errors = {}
        for field, rules in self.rules.items():
            value = data.get(field, "")
            field_errors = []
            for rule in rules:
                error = self._check_rule(value, rule, field)
                if error:
                    field_errors.append(error)
            if field_errors:
                self.errors[field] = field_errors
        return len(self.errors) == 0

    def _check_rule(self, value, rule, field):
        rtype = rule["type"]
        if rtype == "required" and not value:
            return field + " is required"
        if rtype == "min_length" and len(str(value)) < rule.get("length", 0):
            return field + " is too short"
        if rtype == "max_length" and len(str(value)) > rule.get("length", 999):
            return field + " is too long"
        if rtype == "pattern":
            import re
            if not re.match(rule.get("regex", ""), str(value)):
                return field + " has invalid format"
        if rtype == "range":
            try:
                num = float(value)
                if num < rule.get("min", float("-inf")) or num > rule.get("max", float("inf")):
                    return field + " is out of range"
            except (ValueError, TypeError):
                return field + " is not a number"
        return None

    def get_errors(self):
        return dict(self.errors)

    def is_valid(self):
        return len(self.errors) == 0
''',

    "rate_limiter": '''
import time as _time

class TokenBucket:
    def __init__(self, capacity, refill_rate):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate
        self.last_refill = _time.time()

    def _refill(self):
        now = _time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def consume(self, tokens=1):
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def available(self):
        self._refill()
        return int(self.tokens)

class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.clients = {}

    def allow(self, client_id):
        now = _time.time()
        if client_id not in self.clients:
            self.clients[client_id] = []
        requests = self.clients[client_id]
        requests[:] = [t for t in requests if now - t < self.window]
        if len(requests) >= self.max_requests:
            return False
        requests.append(now)
        return True

    def remaining(self, client_id):
        now = _time.time()
        requests = self.clients.get(client_id, [])
        active = [t for t in requests if now - t < self.window]
        return max(0, self.max_requests - len(active))

    def reset(self, client_id):
        if client_id in self.clients:
            del self.clients[client_id]

    def status(self, client_id):
        return {
            "client": client_id,
            "remaining": self.remaining(client_id),
            "limit": self.max_requests,
            "window": self.window,
        }
''',

    "response_builder": '''
class Response:
    def __init__(self, status=200, body=""):
        self.status = status
        self.headers = {}
        self.body = body
        self.cookies = []

    def set_header(self, name, value):
        self.headers[name.lower()] = value
        return self

    def set_content_type(self, ct):
        self.headers["content-type"] = ct
        return self

    def set_json(self, data):
        import json
        self.body = json.dumps(data)
        self.headers["content-type"] = "application/json"
        return self

    def set_cookie(self, name, value, path="/"):
        self.cookies.append(name + "=" + value + "; Path=" + path)
        return self

    def redirect(self, url, permanent=False):
        self.status = 301 if permanent else 302
        self.headers["location"] = url
        return self

    def build(self):
        status_text = {200: "OK", 201: "Created", 301: "Moved", 302: "Found",
                       400: "Bad Request", 404: "Not Found", 500: "Server Error"}
        line = "HTTP/1.1 " + str(self.status) + " " + status_text.get(self.status, "Unknown")
        header_lines = [line]
        for name, value in self.headers.items():
            header_lines.append(name + ": " + value)
        for cookie in self.cookies:
            header_lines.append("set-cookie: " + cookie)
        header_lines.append("content-length: " + str(len(self.body)))
        return "\\r\\n".join(header_lines) + "\\r\\n\\r\\n" + self.body

def json_response(data, status=200):
    return Response(status).set_json(data).build()

def error_response(status, message):
    return Response(status).set_json({"error": message}).build()
''',

    "cors_validator": '''
class CORSPolicy:
    def __init__(self):
        self.allowed_origins = set()
        self.allowed_methods = {"GET", "HEAD", "POST"}
        self.allowed_headers = set()
        self.max_age = 86400
        self.allow_credentials = False

    def allow_origin(self, origin):
        self.allowed_origins.add(origin)
        return self

    def allow_method(self, method):
        self.allowed_methods.add(method.upper())
        return self

    def allow_header(self, header):
        self.allowed_headers.add(header.lower())
        return self

    def is_origin_allowed(self, origin):
        if "*" in self.allowed_origins:
            return True
        return origin in self.allowed_origins

    def is_method_allowed(self, method):
        return method.upper() in self.allowed_methods

    def validate_preflight(self, origin, method, headers=None):
        errors = []
        if not self.is_origin_allowed(origin):
            errors.append("Origin not allowed: " + origin)
        if not self.is_method_allowed(method):
            errors.append("Method not allowed: " + method)
        if headers:
            for h in headers:
                if h.lower() not in self.allowed_headers and h.lower() not in ("content-type", "accept"):
                    errors.append("Header not allowed: " + h)
        return {"allowed": len(errors) == 0, "errors": errors}

    def build_headers(self, origin):
        headers = {}
        if self.is_origin_allowed(origin):
            headers["access-control-allow-origin"] = origin
        headers["access-control-allow-methods"] = ", ".join(sorted(self.allowed_methods))
        if self.allowed_headers:
            headers["access-control-allow-headers"] = ", ".join(sorted(self.allowed_headers))
        headers["access-control-max-age"] = str(self.max_age)
        if self.allow_credentials:
            headers["access-control-allow-credentials"] = "true"
        return headers
''',

    "session_store": '''
import hashlib
import time as _time

def generate_session_id(seed=""):
    data = str(_time.time()) + seed
    return hashlib.sha256(data.encode()).hexdigest()[:32]

class SessionStore:
    def __init__(self, ttl=3600):
        self.sessions = {}
        self.ttl = ttl

    def create(self, data=None):
        sid = generate_session_id()
        self.sessions[sid] = {
            "data": data or {},
            "created_at": _time.time(),
            "last_accessed": _time.time(),
        }
        return sid

    def get(self, sid):
        session = self.sessions.get(sid)
        if session is None:
            return None
        if _time.time() - session["last_accessed"] > self.ttl:
            del self.sessions[sid]
            return None
        session["last_accessed"] = _time.time()
        return session["data"]

    def set(self, sid, key, value):
        session = self.sessions.get(sid)
        if session is None:
            return False
        session["data"][key] = value
        session["last_accessed"] = _time.time()
        return True

    def delete(self, sid):
        if sid in self.sessions:
            del self.sessions[sid]
            return True
        return False

    def cleanup(self):
        now = _time.time()
        expired = [sid for sid, s in self.sessions.items()
                   if now - s["last_accessed"] > self.ttl]
        for sid in expired:
            del self.sessions[sid]
        return len(expired)

    def count(self):
        return len(self.sessions)
''',

    "content_negotiator": '''
def parse_accept(header):
    entries = []
    for item in header.split(","):
        item = item.strip()
        parts = item.split(";")
        media_type = parts[0].strip()
        quality = 1.0
        for param in parts[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        entries.append({"type": media_type, "quality": quality})
    entries.sort(key=lambda e: -e["quality"])
    return entries

def negotiate(accept_header, available):
    preferences = parse_accept(accept_header)
    for pref in preferences:
        ptype = pref["type"]
        if ptype in available:
            return ptype
        if ptype == "*/*" and available:
            return available[0]
        if ptype.endswith("/*"):
            prefix = ptype.split("/")[0]
            for a in available:
                if a.startswith(prefix + "/"):
                    return a
    return None

def parse_accept_language(header):
    entries = parse_accept(header)
    return [e["type"] for e in entries]

def negotiate_language(header, supported):
    preferences = parse_accept_language(header)
    for lang in preferences:
        if lang in supported:
            return lang
        base = lang.split("-")[0]
        for s in supported:
            if s.startswith(base):
                return s
    return supported[0] if supported else None

def negotiate_encoding(header, supported):
    entries = parse_accept(header)
    for entry in entries:
        if entry["type"] in supported:
            return entry["type"]
    if "identity" in supported:
        return "identity"
    return None
''',


    # ── Database Models ──────────────────────────────────────────────────

    "query_builder_sql": '''
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
        self._order.append(column + " " + direction)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def join(self, table, on_clause, join_type="INNER"):
        self._joins.append(join_type + " JOIN " + table + " ON " + on_clause)
        return self

    def build(self):
        sql = "SELECT " + ", ".join(self._select)
        sql += " FROM " + self.table
        for j in self._joins:
            sql += " " + j
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        if self._order:
            sql += " ORDER BY " + ", ".join(self._order)
        if self._limit is not None:
            sql += " LIMIT " + str(self._limit)
        if self._offset is not None:
            sql += " OFFSET " + str(self._offset)
        return sql, self._params

    def build_count(self):
        sql = "SELECT COUNT(*) FROM " + self.table
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        return sql, self._params
''',

    "model_base": '''
class Field:
    def __init__(self, field_type, required=True, default=None):
        self.field_type = field_type
        self.required = required
        self.default = default

class ModelBase:
    _fields = {}
    _table = ""

    def __init__(self, **kwargs):
        self._data = {}
        for name, field in self._fields.items():
            if name in kwargs:
                self._data[name] = kwargs[name]
            elif field.default is not None:
                self._data[name] = field.default
            elif field.required:
                raise ValueError("Missing required field: " + name)

    def get(self, name):
        return self._data.get(name)

    def set(self, name, value):
        if name not in self._fields:
            raise KeyError("Unknown field: " + name)
        self._data[name] = value

    def validate(self):
        errors = []
        for name, field in self._fields.items():
            value = self._data.get(name)
            if field.required and value is None:
                errors.append(name + " is required")
            if value is not None and not isinstance(value, field.field_type):
                errors.append(name + " must be " + field.field_type.__name__)
        return errors

    def to_dict(self):
        return dict(self._data)

    def to_insert_sql(self):
        columns = list(self._data.keys())
        placeholders = ["?"] * len(columns)
        sql = "INSERT INTO " + self._table + " (" + ", ".join(columns) + ") VALUES (" + ", ".join(placeholders) + ")"
        values = [self._data[c] for c in columns]
        return sql, values

    def to_update_sql(self, pk_field="id"):
        sets = []
        values = []
        for name, value in self._data.items():
            if name != pk_field:
                sets.append(name + " = ?")
                values.append(value)
        values.append(self._data[pk_field])
        sql = "UPDATE " + self._table + " SET " + ", ".join(sets) + " WHERE " + pk_field + " = ?"
        return sql, values
''',

    "migration_tracker": '''
class Migration:
    def __init__(self, version, name, up_sql, down_sql):
        self.version = version
        self.name = name
        self.up_sql = up_sql
        self.down_sql = down_sql

class MigrationTracker:
    def __init__(self):
        self.migrations = []
        self.applied = set()

    def register(self, migration):
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: m.version)

    def mark_applied(self, version):
        self.applied.add(version)

    def pending(self):
        return [m for m in self.migrations if m.version not in self.applied]

    def rollback_target(self, version):
        return [m for m in reversed(self.migrations)
                if m.version in self.applied and m.version > version]

    def migrate_up(self):
        applied = []
        for m in self.pending():
            applied.append({"version": m.version, "sql": m.up_sql})
            self.applied.add(m.version)
        return applied

    def migrate_down(self, steps=1):
        rolled_back = []
        applied_sorted = sorted(self.applied, reverse=True)
        for version in applied_sorted[:steps]:
            m = next((x for x in self.migrations if x.version == version), None)
            if m:
                rolled_back.append({"version": m.version, "sql": m.down_sql})
                self.applied.discard(version)
        return rolled_back

    def current_version(self):
        if not self.applied:
            return 0
        return max(self.applied)

    def status(self):
        return {
            "current": self.current_version(),
            "applied": len(self.applied),
            "pending": len(self.pending()),
            "total": len(self.migrations),
        }
''',

    "connection_pool": '''
import time as _time

class Connection:
    def __init__(self, conn_id):
        self.id = conn_id
        self.in_use = False
        self.created_at = _time.time()
        self.last_used = _time.time()

    def execute(self, query):
        self.last_used = _time.time()
        return {"query": query, "connection": self.id}

class ConnectionPool:
    def __init__(self, max_size=10, max_idle=300):
        self.max_size = max_size
        self.max_idle = max_idle
        self.connections = []
        self.next_id = 0

    def _create(self):
        conn = Connection(self.next_id)
        self.next_id += 1
        self.connections.append(conn)
        return conn

    def acquire(self):
        for conn in self.connections:
            if not conn.in_use:
                conn.in_use = True
                conn.last_used = _time.time()
                return conn
        if len(self.connections) < self.max_size:
            conn = self._create()
            conn.in_use = True
            return conn
        raise RuntimeError("Pool exhausted")

    def release(self, conn):
        conn.in_use = False
        conn.last_used = _time.time()

    def evict_idle(self):
        now = _time.time()
        evicted = []
        active = []
        for conn in self.connections:
            if not conn.in_use and now - conn.last_used > self.max_idle:
                evicted.append(conn.id)
            else:
                active.append(conn)
        self.connections = active
        return evicted

    def stats(self):
        in_use = sum(1 for c in self.connections if c.in_use)
        return {
            "total": len(self.connections),
            "in_use": in_use,
            "available": len(self.connections) - in_use,
            "max_size": self.max_size,
        }
''',

    "record_mapper": '''
class RecordMapper:
    def __init__(self, model_class, table_name):
        self.model_class = model_class
        self.table_name = table_name
        self.column_map = {}
        self.records = []

    def map_column(self, attr_name, col_name):
        self.column_map[attr_name] = col_name
        return self

    def to_record(self, row_dict):
        kwargs = {}
        reverse_map = {v: k for k, v in self.column_map.items()}
        for col, val in row_dict.items():
            attr = reverse_map.get(col, col)
            kwargs[attr] = val
        return self.model_class(**kwargs)

    def to_row(self, obj):
        row = {}
        for attr in vars(obj):
            if attr.startswith("_"):
                continue
            col = self.column_map.get(attr, attr)
            row[col] = getattr(obj, attr)
        return row

    def insert_sql(self, obj):
        row = self.to_row(obj)
        columns = list(row.keys())
        placeholders = ["?"] * len(columns)
        sql = "INSERT INTO " + self.table_name
        sql += " (" + ", ".join(columns) + ")"
        sql += " VALUES (" + ", ".join(placeholders) + ")"
        return sql, list(row.values())

    def select_sql(self, where=None):
        sql = "SELECT * FROM " + self.table_name
        if where:
            sql += " WHERE " + where
        return sql

    def update_sql(self, obj, pk="id"):
        row = self.to_row(obj)
        sets = [col + " = ?" for col in row if col != pk]
        values = [v for k, v in row.items() if k != pk]
        values.append(row[pk])
        sql = "UPDATE " + self.table_name + " SET " + ", ".join(sets) + " WHERE " + pk + " = ?"
        return sql, values
''',

    "schema_validator_db": '''
class Column:
    def __init__(self, name, col_type, nullable=True, primary_key=False, default=None):
        self.name = name
        self.col_type = col_type
        self.nullable = nullable
        self.primary_key = primary_key
        self.default = default

class TableSchema:
    def __init__(self, name):
        self.name = name
        self.columns = {}
        self.constraints = []

    def add_column(self, column):
        self.columns[column.name] = column
        return self

    def add_constraint(self, constraint):
        self.constraints.append(constraint)
        return self

    def validate_row(self, row):
        errors = []
        for col_name, col in self.columns.items():
            value = row.get(col_name)
            if value is None and not col.nullable and col.default is None:
                errors.append(col_name + " cannot be null")
            if value is not None:
                if col.col_type == "int" and not isinstance(value, int):
                    errors.append(col_name + " must be integer")
                elif col.col_type == "str" and not isinstance(value, str):
                    errors.append(col_name + " must be string")
                elif col.col_type == "float" and not isinstance(value, (int, float)):
                    errors.append(col_name + " must be numeric")
        return errors

    def create_table_sql(self):
        parts = []
        for col in self.columns.values():
            type_map = {"int": "INTEGER", "str": "TEXT", "float": "REAL"}
            line = col.name + " " + type_map.get(col.col_type, "TEXT")
            if col.primary_key:
                line += " PRIMARY KEY"
            if not col.nullable:
                line += " NOT NULL"
            if col.default is not None:
                line += " DEFAULT " + repr(col.default)
            parts.append(line)
        return "CREATE TABLE " + self.name + " (\\n  " + ",\\n  ".join(parts) + "\\n)"

    def column_names(self):
        return list(self.columns.keys())
''',

    "transaction_ctx": '''
class Transaction:
    def __init__(self):
        self.operations = []
        self.committed = False
        self.rolled_back = False
        self.savepoints = []

    def execute(self, sql, params=None):
        if self.committed or self.rolled_back:
            raise RuntimeError("Transaction already completed")
        self.operations.append({"sql": sql, "params": params or []})

    def savepoint(self, name):
        self.savepoints.append({"name": name, "ops_count": len(self.operations)})

    def rollback_to(self, name):
        for sp in reversed(self.savepoints):
            if sp["name"] == name:
                self.operations = self.operations[:sp["ops_count"]]
                return True
        return False

    def commit(self):
        if self.rolled_back:
            raise RuntimeError("Cannot commit rolled-back transaction")
        self.committed = True
        return self.operations

    def rollback(self):
        self.rolled_back = True
        self.operations = []

    def is_active(self):
        return not self.committed and not self.rolled_back

    def operation_count(self):
        return len(self.operations)

class TransactionManager:
    def __init__(self):
        self.history = []

    def begin(self):
        return Transaction()

    def record(self, txn):
        self.history.append({
            "ops": len(txn.operations),
            "committed": txn.committed,
            "rolled_back": txn.rolled_back,
        })

    def stats(self):
        committed = sum(1 for h in self.history if h["committed"])
        rolled_back = sum(1 for h in self.history if h["rolled_back"])
        return {"total": len(self.history), "committed": committed, "rolled_back": rolled_back}
''',

    "index_advisor": '''
class IndexAdvisor:
    def __init__(self):
        self.query_log = []
        self.existing_indexes = set()

    def log_query(self, table, columns, query_type="SELECT"):
        self.query_log.append({
            "table": table,
            "columns": tuple(sorted(columns)),
            "type": query_type,
        })

    def add_existing_index(self, table, columns):
        self.existing_indexes.add((table, tuple(sorted(columns))))

    def analyze(self):
        column_freq = {}
        for q in self.query_log:
            key = (q["table"], q["columns"])
            column_freq[key] = column_freq.get(key, 0) + 1
        recommendations = []
        for (table, cols), freq in sorted(column_freq.items(), key=lambda x: -x[1]):
            if (table, cols) not in self.existing_indexes:
                recommendations.append({
                    "table": table,
                    "columns": list(cols),
                    "frequency": freq,
                    "priority": "high" if freq > 10 else "medium" if freq > 3 else "low",
                })
        return recommendations

    def create_index_sql(self, table, columns):
        name = "idx_" + table + "_" + "_".join(columns)
        return "CREATE INDEX " + name + " ON " + table + " (" + ", ".join(columns) + ")"

    def generate_all_sql(self):
        recs = self.analyze()
        return [self.create_index_sql(r["table"], r["columns"]) for r in recs]

    def coverage(self):
        total_patterns = len(set((q["table"], q["columns"]) for q in self.query_log))
        covered = sum(1 for q in self.query_log
                      if (q["table"], q["columns"]) in self.existing_indexes)
        total = len(self.query_log)
        return {"total_patterns": total_patterns, "covered_queries": covered,
                "total_queries": total, "rate": covered / total if total else 0.0}
''',

    "query_cache": '''
import hashlib
import time as _time

class QueryCache:
    def __init__(self, max_size=100, ttl=300):
        self.max_size = max_size
        self.ttl = ttl
        self.cache = {}
        self.hits = 0
        self.misses = 0

    def _key(self, query, params):
        raw = query + str(params)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, query, params=None):
        key = self._key(query, params or [])
        entry = self.cache.get(key)
        if entry is None:
            self.misses += 1
            return None
        if _time.time() - entry["time"] > self.ttl:
            del self.cache[key]
            self.misses += 1
            return None
        self.hits += 1
        entry["access_count"] += 1
        return entry["result"]

    def put(self, query, params, result):
        key = self._key(query, params or [])
        if len(self.cache) >= self.max_size:
            self._evict()
        self.cache[key] = {
            "result": result,
            "time": _time.time(),
            "access_count": 1,
            "query": query,
        }

    def _evict(self):
        if not self.cache:
            return
        oldest_key = min(self.cache, key=lambda k: self.cache[k]["time"])
        del self.cache[oldest_key]

    def invalidate(self, pattern=None):
        if pattern is None:
            self.cache.clear()
            return
        to_remove = [k for k, v in self.cache.items() if pattern in v["query"]]
        for k in to_remove:
            del self.cache[k]

    def stats(self):
        total = self.hits + self.misses
        return {
            "size": len(self.cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total else 0.0,
        }
''',

    "data_seeder": '''
import random as _random

class DataSeeder:
    def __init__(self, seed=42):
        self.rng = _random.Random(seed)
        self.generated = {}

    def generate_int(self, min_val=0, max_val=1000):
        return self.rng.randint(min_val, max_val)

    def generate_string(self, length=10):
        chars = "abcdefghijklmnopqrstuvwxyz"
        return "".join(self.rng.choice(chars) for _ in range(length))

    def generate_email(self):
        user = self.generate_string(8)
        domains = ["example.com", "test.org", "demo.net"]
        return user + "@" + self.rng.choice(domains)

    def generate_row(self, schema):
        row = {}
        for col_name, col_type in schema.items():
            if col_type == "int":
                row[col_name] = self.generate_int()
            elif col_type == "string":
                row[col_name] = self.generate_string()
            elif col_type == "email":
                row[col_name] = self.generate_email()
            elif col_type == "bool":
                row[col_name] = self.rng.choice([True, False])
            elif col_type == "float":
                row[col_name] = round(self.rng.uniform(0, 1000), 2)
        return row

    def seed_table(self, table_name, schema, count=10):
        rows = [self.generate_row(schema) for _ in range(count)]
        self.generated[table_name] = rows
        return rows

    def insert_statements(self, table_name):
        rows = self.generated.get(table_name, [])
        stmts = []
        for row in rows:
            cols = ", ".join(row.keys())
            vals = ", ".join(repr(v) for v in row.values())
            stmts.append("INSERT INTO " + table_name + " (" + cols + ") VALUES (" + vals + ")")
        return stmts

    def summary(self):
        return {t: len(rows) for t, rows in self.generated.items()}
''',


    # ── State Machines ───────────────────────────────────────────────────

    "traffic_light": '''
class TrafficLight:
    STATES = ["red", "green", "yellow"]
    DURATIONS = {"red": 30, "green": 25, "yellow": 5}

    def __init__(self):
        self.state = "red"
        self.timer = 0
        self.cycle_count = 0
        self.history = []

    def tick(self):
        self.timer += 1
        if self.timer >= self.DURATIONS[self.state]:
            self.transition()

    def transition(self):
        old = self.state
        idx = self.STATES.index(self.state)
        self.state = self.STATES[(idx + 1) % len(self.STATES)]
        self.timer = 0
        self.history.append({"from": old, "to": self.state})
        if self.state == "red":
            self.cycle_count += 1

    def is_safe_to_cross(self):
        return self.state == "green" and self.timer < self.DURATIONS["green"] - 5

    def remaining_time(self):
        return self.DURATIONS[self.state] - self.timer

    def run_cycles(self, n):
        while self.cycle_count < n:
            self.tick()
        return self.history

    def stats(self):
        state_time = {"red": 0, "green": 0, "yellow": 0}
        for entry in self.history:
            state_time[entry["from"]] = state_time.get(entry["from"], 0) + 1
        return {"cycles": self.cycle_count, "transitions": len(self.history),
                "current": self.state}
''',

    "order_workflow": '''
class OrderWorkflow:
    TRANSITIONS = {
        "created": ["confirmed", "cancelled"],
        "confirmed": ["processing", "cancelled"],
        "processing": ["shipped", "cancelled"],
        "shipped": ["delivered", "returned"],
        "delivered": ["returned", "completed"],
        "returned": ["refunded"],
        "cancelled": [],
        "refunded": [],
        "completed": [],
    }

    def __init__(self, order_id):
        self.order_id = order_id
        self.state = "created"
        self.history = [{"state": "created", "note": "Order created"}]

    def can_transition(self, target):
        return target in self.TRANSITIONS.get(self.state, [])

    def transition(self, target, note=""):
        if not self.can_transition(target):
            raise ValueError("Cannot go from " + self.state + " to " + target)
        self.state = target
        self.history.append({"state": target, "note": note})

    def available_transitions(self):
        return list(self.TRANSITIONS.get(self.state, []))

    def is_final(self):
        return len(self.TRANSITIONS.get(self.state, [])) == 0

    def revert(self):
        if len(self.history) < 2:
            raise ValueError("Nothing to revert")
        self.history.pop()
        self.state = self.history[-1]["state"]

    def timeline(self):
        return [h["state"] for h in self.history]

    def summary(self):
        return {
            "order_id": self.order_id,
            "current": self.state,
            "steps": len(self.history),
            "is_final": self.is_final(),
        }
''',

    "tcp_state": '''
class TCPStateMachine:
    TRANSITIONS = {
        "CLOSED": {"passive_open": "LISTEN", "active_open": "SYN_SENT"},
        "LISTEN": {"syn_received": "SYN_RCVD", "close": "CLOSED"},
        "SYN_SENT": {"syn_ack_received": "ESTABLISHED", "close": "CLOSED"},
        "SYN_RCVD": {"ack_received": "ESTABLISHED", "close": "FIN_WAIT_1"},
        "ESTABLISHED": {"close": "FIN_WAIT_1", "fin_received": "CLOSE_WAIT"},
        "FIN_WAIT_1": {"ack_received": "FIN_WAIT_2", "fin_received": "CLOSING"},
        "FIN_WAIT_2": {"fin_received": "TIME_WAIT"},
        "CLOSING": {"ack_received": "TIME_WAIT"},
        "TIME_WAIT": {"timeout": "CLOSED"},
        "CLOSE_WAIT": {"close": "LAST_ACK"},
        "LAST_ACK": {"ack_received": "CLOSED"},
    }

    def __init__(self):
        self.state = "CLOSED"
        self.history = []

    def handle_event(self, event):
        transitions = self.TRANSITIONS.get(self.state, {})
        if event not in transitions:
            raise ValueError("Invalid event " + event + " in state " + self.state)
        old = self.state
        self.state = transitions[event]
        self.history.append({"from": old, "event": event, "to": self.state})

    def is_connected(self):
        return self.state == "ESTABLISHED"

    def is_closed(self):
        return self.state == "CLOSED"

    def available_events(self):
        return list(self.TRANSITIONS.get(self.state, {}).keys())

    def connection_lifecycle(self):
        self.handle_event("active_open")
        self.handle_event("syn_ack_received")
        return self.state

    def close_connection(self):
        self.handle_event("close")
        self.handle_event("ack_received")
        self.handle_event("fin_received")
        self.handle_event("timeout")
        return self.state
''',

    "vending_machine": '''
class VendingMachine:
    def __init__(self):
        self.state = "idle"
        self.balance = 0
        self.inventory = {}
        self.transactions = []

    def add_product(self, name, price, quantity):
        self.inventory[name] = {"price": price, "quantity": quantity}

    def insert_coin(self, amount):
        if self.state not in ("idle", "selecting"):
            raise ValueError("Cannot insert coins in state: " + self.state)
        self.balance += amount
        self.state = "selecting"
        return self.balance

    def select_product(self, name):
        if self.state != "selecting":
            raise ValueError("Must insert coins first")
        if name not in self.inventory:
            raise ValueError("Product not found: " + name)
        product = self.inventory[name]
        if product["quantity"] <= 0:
            raise ValueError("Product out of stock: " + name)
        if self.balance < product["price"]:
            raise ValueError("Insufficient funds")
        self.balance -= product["price"]
        product["quantity"] -= 1
        self.transactions.append({"product": name, "price": product["price"]})
        self.state = "dispensing"
        return name

    def dispense(self):
        if self.state != "dispensing":
            raise ValueError("Nothing to dispense")
        self.state = "idle" if self.balance == 0 else "selecting"
        return "Dispensed"

    def return_change(self):
        change = self.balance
        self.balance = 0
        self.state = "idle"
        return change

    def status(self):
        return {
            "state": self.state,
            "balance": self.balance,
            "products": len(self.inventory),
            "transactions": len(self.transactions),
        }

    def total_revenue(self):
        return sum(t["price"] for t in self.transactions)
''',

    "lexer_state": '''
class Lexer:
    def __init__(self, source):
        self.source = source
        self.pos = 0
        self.state = "default"
        self.tokens = []
        self.current = []

    def peek(self):
        if self.pos >= len(self.source):
            return None
        return self.source[self.pos]

    def advance(self):
        ch = self.peek()
        self.pos += 1
        return ch

    def emit(self, token_type):
        value = "".join(self.current)
        if value:
            self.tokens.append((token_type, value))
        self.current = []

    def tokenize(self):
        while self.pos < len(self.source):
            ch = self.peek()
            if self.state == "default":
                if ch.isalpha() or ch == "_":
                    self.state = "identifier"
                elif ch.isdigit():
                    self.state = "number"
                elif ch == '"':
                    self.advance()
                    self.state = "string"
                elif ch in " \\t\\n":
                    self.advance()
                else:
                    self.current.append(self.advance())
                    self.emit("symbol")
            elif self.state == "identifier":
                if ch.isalnum() or ch == "_":
                    self.current.append(self.advance())
                else:
                    self.emit("identifier")
                    self.state = "default"
            elif self.state == "number":
                if ch.isdigit() or ch == ".":
                    self.current.append(self.advance())
                else:
                    self.emit("number")
                    self.state = "default"
            elif self.state == "string":
                if ch == '"':
                    self.emit("string")
                    self.advance()
                    self.state = "default"
                elif ch == "\\\\":
                    self.advance()
                    self.current.append(self.advance())
                else:
                    self.current.append(self.advance())
        if self.current:
            self.emit(self.state)
        return self.tokens
''',

    "game_turns": '''
class Player:
    def __init__(self, name, health=100, attack=10):
        self.name = name
        self.health = health
        self.attack = attack
        self.alive = True
        self.actions = []

    def take_damage(self, amount):
        self.health -= amount
        if self.health <= 0:
            self.health = 0
            self.alive = False

    def do_attack(self, target):
        if not self.alive:
            return 0
        target.take_damage(self.attack)
        self.actions.append({"type": "attack", "target": target.name, "damage": self.attack})
        return self.attack

class TurnGame:
    def __init__(self):
        self.players = []
        self.current_turn = 0
        self.log = []

    def add_player(self, player):
        self.players.append(player)

    def current_player(self):
        alive = [p for p in self.players if p.alive]
        if not alive:
            return None
        return alive[self.current_turn % len(alive)]

    def next_turn(self):
        alive = [p for p in self.players if p.alive]
        if len(alive) <= 1:
            return None
        self.current_turn += 1
        return self.current_player()

    def execute_turn(self, attacker, defender):
        dmg = attacker.do_attack(defender)
        self.log.append({
            "turn": self.current_turn,
            "attacker": attacker.name,
            "defender": defender.name,
            "damage": dmg,
        })
        return dmg

    def winner(self):
        alive = [p for p in self.players if p.alive]
        if len(alive) == 1:
            return alive[0]
        return None

    def is_over(self):
        return sum(1 for p in self.players if p.alive) <= 1

    def scoreboard(self):
        return [{"name": p.name, "health": p.health, "alive": p.alive} for p in self.players]
''',

    "auth_flow": '''
class AuthFlow:
    def __init__(self):
        self.state = "unauthenticated"
        self.user = None
        self.attempts = 0
        self.max_attempts = 3
        self.locked_until = 0
        self.history = []

    def login(self, username, password, users_db):
        if self.state == "locked":
            return {"success": False, "error": "Account locked"}
        if self.state == "authenticated":
            return {"success": False, "error": "Already logged in"}
        user = users_db.get(username)
        if user and user["password"] == password:
            self.state = "authenticated"
            self.user = username
            self.attempts = 0
            self.history.append({"action": "login", "user": username})
            return {"success": True}
        self.attempts += 1
        if self.attempts >= self.max_attempts:
            self.state = "locked"
            self.history.append({"action": "locked", "user": username})
        return {"success": False, "error": "Invalid credentials", "remaining": self.max_attempts - self.attempts}

    def logout(self):
        if self.state != "authenticated":
            return False
        self.history.append({"action": "logout", "user": self.user})
        self.state = "unauthenticated"
        self.user = None
        return True

    def unlock(self):
        if self.state == "locked":
            self.state = "unauthenticated"
            self.attempts = 0
            return True
        return False

    def is_authenticated(self):
        return self.state == "authenticated"

    def current_user(self):
        return self.user

    def audit_log(self):
        return list(self.history)
''',

    "elevator_ctrl": '''
class Elevator:
    def __init__(self, min_floor=1, max_floor=10):
        self.current = 1
        self.min_floor = min_floor
        self.max_floor = max_floor
        self.direction = "idle"
        self.requests = set()
        self.log = []

    def request(self, floor):
        if floor < self.min_floor or floor > self.max_floor:
            raise ValueError("Floor out of range")
        self.requests.add(floor)
        if self.direction == "idle":
            if floor > self.current:
                self.direction = "up"
            elif floor < self.current:
                self.direction = "down"

    def step(self):
        if not self.requests:
            self.direction = "idle"
            return self.current
        if self.direction == "up":
            self.current += 1
        elif self.direction == "down":
            self.current -= 1
        if self.current in self.requests:
            self.requests.discard(self.current)
            self.log.append({"stopped": self.current})
        if not self.requests:
            self.direction = "idle"
        elif self.direction == "up" and all(f < self.current for f in self.requests):
            self.direction = "down"
        elif self.direction == "down" and all(f > self.current for f in self.requests):
            self.direction = "up"
        return self.current

    def run_to_completion(self):
        while self.requests:
            self.step()
        return self.log

    def status(self):
        return {
            "floor": self.current,
            "direction": self.direction,
            "pending": sorted(self.requests),
        }

    def floors_visited(self):
        return [entry["stopped"] for entry in self.log]
''',

    "document_lifecycle": '''
class Document:
    TRANSITIONS = {
        "draft": ["review", "archived"],
        "review": ["approved", "rejected", "draft"],
        "approved": ["published", "draft"],
        "rejected": ["draft"],
        "published": ["archived", "draft"],
        "archived": ["draft"],
    }

    def __init__(self, title, author):
        self.title = title
        self.author = author
        self.state = "draft"
        self.version = 1
        self.history = [{"state": "draft", "version": 1}]
        self.reviewers = []
        self.comments = []

    def transition(self, target):
        if target not in self.TRANSITIONS.get(self.state, []):
            raise ValueError("Cannot transition from " + self.state + " to " + target)
        self.state = target
        if target == "draft":
            self.version += 1
        self.history.append({"state": target, "version": self.version})

    def add_reviewer(self, name):
        if name not in self.reviewers:
            self.reviewers.append(name)

    def add_comment(self, author, text):
        self.comments.append({"author": author, "text": text, "state": self.state})

    def submit_for_review(self):
        self.transition("review")

    def approve(self):
        self.transition("approved")

    def reject(self):
        self.transition("rejected")

    def publish(self):
        self.transition("approved")
        self.transition("published")

    def archive(self):
        self.transition("archived")

    def summary(self):
        return {
            "title": self.title,
            "state": self.state,
            "version": self.version,
            "reviewers": len(self.reviewers),
            "comments": len(self.comments),
        }
''',

    "protocol_handler": '''
class Message:
    def __init__(self, msg_type, payload=None):
        self.msg_type = msg_type
        self.payload = payload or {}
        self.seq = 0

class ProtocolHandler:
    def __init__(self):
        self.state = "disconnected"
        self.seq_counter = 0
        self.inbox = []
        self.outbox = []
        self.handlers = {}

    def register_handler(self, msg_type, handler):
        self.handlers[msg_type] = handler

    def connect(self):
        if self.state != "disconnected":
            raise RuntimeError("Already connected")
        self.state = "handshaking"
        self.send(Message("HELLO", {"version": "1.0"}))

    def send(self, msg):
        self.seq_counter += 1
        msg.seq = self.seq_counter
        self.outbox.append(msg)

    def receive(self, msg):
        self.inbox.append(msg)
        if self.state == "handshaking" and msg.msg_type == "HELLO_ACK":
            self.state = "connected"
        elif self.state == "connected":
            handler = self.handlers.get(msg.msg_type)
            if handler:
                response = handler(msg)
                if response:
                    self.send(response)
        elif msg.msg_type == "DISCONNECT":
            self.state = "disconnected"

    def disconnect(self):
        self.send(Message("DISCONNECT"))
        self.state = "disconnected"

    def is_connected(self):
        return self.state == "connected"

    def pending_messages(self):
        return len(self.outbox)

    def drain_outbox(self):
        messages = list(self.outbox)
        self.outbox.clear()
        return messages

    def stats(self):
        return {
            "state": self.state,
            "sent": self.seq_counter,
            "received": len(self.inbox),
            "pending": len(self.outbox),
        }
''',


    # ── Error Handling ───────────────────────────────────────────────────

    "retry_executor": '''
import time as _time

class RetryConfig:
    def __init__(self, max_retries=3, base_delay=1.0, backoff_factor=2.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor

def retry(func, config=None, on_error=None):
    if config is None:
        config = RetryConfig()
    last_error = None
    attempts = []
    for attempt in range(config.max_retries + 1):
        try:
            result = func()
            attempts.append({"attempt": attempt, "success": True})
            return {"result": result, "attempts": attempts}
        except Exception as e:
            last_error = e
            attempts.append({"attempt": attempt, "error": str(e)})
            if on_error:
                on_error(e, attempt)
            if attempt < config.max_retries:
                delay = config.base_delay * (config.backoff_factor ** attempt)
                _time.sleep(min(delay, 0.001))
    return {"result": None, "error": str(last_error), "attempts": attempts}

def with_jitter(base_delay, attempt, max_jitter=0.5):
    import random
    delay = base_delay * (2 ** attempt)
    jitter = random.uniform(0, max_jitter)
    return delay + jitter

class RetryStats:
    def __init__(self):
        self.total = 0
        self.successes = 0
        self.failures = 0
        self.total_attempts = 0

    def record(self, result):
        self.total += 1
        n = len(result["attempts"])
        self.total_attempts += n
        if result.get("error"):
            self.failures += 1
        else:
            self.successes += 1

    def summary(self):
        return {
            "total": self.total,
            "success_rate": self.successes / self.total if self.total else 0,
            "avg_attempts": self.total_attempts / self.total if self.total else 0,
        }
''',

    "circuit_breaker": '''
import time as _time

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = "closed"
        self.failure_count = 0
        self.last_failure_time = 0
        self.success_count = 0
        self.calls = []

    def call(self, func):
        if self.state == "open":
            if _time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                self.calls.append({"state": "open", "result": "rejected"})
                raise RuntimeError("Circuit is open")
        try:
            result = func()
            self._on_success()
            self.calls.append({"state": self.state, "result": "success"})
            return result
        except Exception as e:
            self._on_failure()
            self.calls.append({"state": self.state, "result": "failure"})
            raise

    def _on_success(self):
        self.failure_count = 0
        self.success_count += 1
        if self.state == "half-open":
            self.state = "closed"

    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = _time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def reset(self):
        self.state = "closed"
        self.failure_count = 0
        self.success_count = 0

    def status(self):
        return {
            "state": self.state,
            "failures": self.failure_count,
            "successes": self.success_count,
            "total_calls": len(self.calls),
        }
''',

    "input_validator": '''
class ValidationError:
    def __init__(self, field, message, code="invalid"):
        self.field = field
        self.message = message
        self.code = code

class InputValidator:
    def __init__(self):
        self.errors = []

    def require(self, data, field):
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            self.errors.append(ValidationError(field, field + " is required", "required"))
            return None
        return value

    def require_type(self, data, field, expected_type):
        value = data.get(field)
        if value is not None and not isinstance(value, expected_type):
            self.errors.append(ValidationError(field, field + " must be " + expected_type.__name__, "type"))
        return value

    def require_range(self, data, field, min_val=None, max_val=None):
        value = data.get(field)
        if value is not None:
            if min_val is not None and value < min_val:
                self.errors.append(ValidationError(field, field + " below minimum", "range"))
            if max_val is not None and value > max_val:
                self.errors.append(ValidationError(field, field + " above maximum", "range"))
        return value

    def require_length(self, data, field, min_len=None, max_len=None):
        value = data.get(field)
        if value is not None and isinstance(value, str):
            if min_len is not None and len(value) < min_len:
                self.errors.append(ValidationError(field, field + " too short", "length"))
            if max_len is not None and len(value) > max_len:
                self.errors.append(ValidationError(field, field + " too long", "length"))
        return value

    def is_valid(self):
        return len(self.errors) == 0

    def error_messages(self):
        return {e.field: e.message for e in self.errors}

    def error_count(self):
        return len(self.errors)
''',

    "error_collector": '''
class ErrorCollector:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.context_stack = []

    def push_context(self, name):
        self.context_stack.append(name)

    def pop_context(self):
        if self.context_stack:
            return self.context_stack.pop()
        return None

    def current_context(self):
        return ".".join(self.context_stack)

    def add_error(self, message, code=None):
        self.errors.append({
            "message": message,
            "code": code,
            "context": self.current_context(),
        })

    def add_warning(self, message):
        self.warnings.append({
            "message": message,
            "context": self.current_context(),
        })

    def has_errors(self):
        return len(self.errors) > 0

    def merge(self, other):
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def by_context(self):
        result = {}
        for err in self.errors:
            ctx = err["context"]
            if ctx not in result:
                result[ctx] = []
            result[ctx].append(err)
        return result

    def summary(self):
        return {
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "contexts": list(set(e["context"] for e in self.errors)),
        }

    def format_report(self):
        lines = []
        for err in self.errors:
            prefix = "[" + err["context"] + "] " if err["context"] else ""
            lines.append("ERROR: " + prefix + err["message"])
        for warn in self.warnings:
            prefix = "[" + warn["context"] + "] " if warn["context"] else ""
            lines.append("WARN: " + prefix + warn["message"])
        return "\\n".join(lines)
''',

    "fallback_chain": '''
class FallbackChain:
    def __init__(self):
        self.handlers = []
        self.results = []

    def add(self, name, handler):
        self.handlers.append({"name": name, "handler": handler})
        return self

    def execute(self, *args, **kwargs):
        for entry in self.handlers:
            try:
                result = entry["handler"](*args, **kwargs)
                self.results.append({
                    "handler": entry["name"],
                    "success": True,
                    "result": result,
                })
                return result
            except Exception as e:
                self.results.append({
                    "handler": entry["name"],
                    "success": False,
                    "error": str(e),
                })
        raise RuntimeError("All handlers in fallback chain failed")

    def last_result(self):
        if not self.results:
            return None
        return self.results[-1]

    def successful_handler(self):
        for r in self.results:
            if r["success"]:
                return r["handler"]
        return None

    def failure_count(self):
        return sum(1 for r in self.results if not r["success"])

    def reset(self):
        self.results = []

    def summary(self):
        return {
            "total_handlers": len(self.handlers),
            "attempts": len(self.results),
            "failures": self.failure_count(),
            "success": any(r["success"] for r in self.results),
        }
''',

    "timeout_handler": '''
import time as _time

class TimeoutError(Exception):
    pass

class TimeoutHandler:
    def __init__(self, default_timeout=30):
        self.default_timeout = default_timeout
        self.active_timers = {}
        self.expired = []

    def start_timer(self, name, timeout=None):
        if timeout is None:
            timeout = self.default_timeout
        self.active_timers[name] = {
            "start": _time.time(),
            "timeout": timeout,
        }

    def check_timer(self, name):
        timer = self.active_timers.get(name)
        if timer is None:
            return None
        elapsed = _time.time() - timer["start"]
        remaining = timer["timeout"] - elapsed
        if remaining <= 0:
            self.expired.append(name)
            del self.active_timers[name]
            return 0
        return remaining

    def cancel_timer(self, name):
        if name in self.active_timers:
            del self.active_timers[name]
            return True
        return False

    def is_expired(self, name):
        return name in self.expired

    def with_timeout(self, func, timeout=None):
        if timeout is None:
            timeout = self.default_timeout
        start = _time.time()
        result = func()
        elapsed = _time.time() - start
        if elapsed > timeout:
            raise TimeoutError("Operation exceeded " + str(timeout) + "s")
        return result

    def active_count(self):
        return len(self.active_timers)

    def stats(self):
        return {
            "active": len(self.active_timers),
            "expired": len(self.expired),
            "default_timeout": self.default_timeout,
        }
''',

    "graceful_shutdown": '''
class ShutdownManager:
    def __init__(self):
        self.hooks = []
        self.state = "running"
        self.results = []

    def register(self, name, handler, priority=0):
        self.hooks.append({
            "name": name,
            "handler": handler,
            "priority": priority,
        })

    def shutdown(self):
        if self.state != "running":
            return self.results
        self.state = "shutting_down"
        sorted_hooks = sorted(self.hooks, key=lambda h: h["priority"])
        for hook in sorted_hooks:
            try:
                hook["handler"]()
                self.results.append({"name": hook["name"], "status": "ok"})
            except Exception as e:
                self.results.append({"name": hook["name"], "status": "error", "error": str(e)})
        self.state = "stopped"
        return self.results

    def is_running(self):
        return self.state == "running"

    def is_stopped(self):
        return self.state == "stopped"

    def remove_hook(self, name):
        self.hooks = [h for h in self.hooks if h["name"] != name]

    def hook_names(self):
        return [h["name"] for h in self.hooks]

    def summary(self):
        ok = sum(1 for r in self.results if r["status"] == "ok")
        err = sum(1 for r in self.results if r["status"] == "error")
        return {
            "state": self.state,
            "total_hooks": len(self.hooks),
            "successful": ok,
            "failed": err,
        }
''',

    "error_recovery": '''
class RecoveryAction:
    def __init__(self, name, check_fn, recover_fn):
        self.name = name
        self.check_fn = check_fn
        self.recover_fn = recover_fn

class ErrorRecovery:
    def __init__(self):
        self.actions = []
        self.history = []

    def register(self, action):
        self.actions.append(action)

    def diagnose(self, context):
        issues = []
        for action in self.actions:
            try:
                if action.check_fn(context):
                    issues.append(action.name)
            except Exception:
                pass
        return issues

    def recover(self, context):
        issues = self.diagnose(context)
        results = []
        for issue in issues:
            action = next(a for a in self.actions if a.name == issue)
            try:
                action.recover_fn(context)
                results.append({"action": issue, "status": "recovered"})
            except Exception as e:
                results.append({"action": issue, "status": "failed", "error": str(e)})
        self.history.extend(results)
        return results

    def auto_heal(self, context, max_rounds=3):
        for round_num in range(max_rounds):
            issues = self.diagnose(context)
            if not issues:
                return {"rounds": round_num, "status": "healthy"}
            self.recover(context)
        remaining = self.diagnose(context)
        return {"rounds": max_rounds, "status": "degraded", "remaining": remaining}

    def stats(self):
        recovered = sum(1 for h in self.history if h["status"] == "recovered")
        failed = sum(1 for h in self.history if h["status"] == "failed")
        return {"recovered": recovered, "failed": failed, "total": len(self.history)}
''',

    "validation_pipeline": '''
class ValidationStep:
    def __init__(self, name, validator):
        self.name = name
        self.validator = validator

class ValidationPipeline:
    def __init__(self):
        self.steps = []
        self.results = []

    def add_step(self, name, validator):
        self.steps.append(ValidationStep(name, validator))
        return self

    def validate(self, data):
        self.results = []
        all_valid = True
        for step in self.steps:
            try:
                is_valid = step.validator(data)
                self.results.append({
                    "step": step.name,
                    "valid": bool(is_valid),
                    "error": None,
                })
                if not is_valid:
                    all_valid = False
            except Exception as e:
                self.results.append({
                    "step": step.name,
                    "valid": False,
                    "error": str(e),
                })
                all_valid = False
        return all_valid

    def validate_fail_fast(self, data):
        self.results = []
        for step in self.steps:
            try:
                is_valid = step.validator(data)
                self.results.append({"step": step.name, "valid": bool(is_valid), "error": None})
                if not is_valid:
                    return False
            except Exception as e:
                self.results.append({"step": step.name, "valid": False, "error": str(e)})
                return False
        return True

    def failed_steps(self):
        return [r for r in self.results if not r["valid"]]

    def passed_steps(self):
        return [r for r in self.results if r["valid"]]

    def summary(self):
        passed = len(self.passed_steps())
        failed = len(self.failed_steps())
        return {"total": len(self.results), "passed": passed, "failed": failed}
''',

    "health_checker": '''
import time as _time

class HealthCheck:
    def __init__(self, name, check_fn, critical=True):
        self.name = name
        self.check_fn = check_fn
        self.critical = critical

class HealthChecker:
    def __init__(self):
        self.checks = []
        self.last_results = {}

    def register(self, name, check_fn, critical=True):
        self.checks.append(HealthCheck(name, check_fn, critical))

    def run(self):
        results = {}
        for check in self.checks:
            start = _time.time()
            try:
                status = check.check_fn()
                elapsed = _time.time() - start
                results[check.name] = {
                    "status": "healthy" if status else "unhealthy",
                    "critical": check.critical,
                    "time_ms": round(elapsed * 1000, 2),
                }
            except Exception as e:
                elapsed = _time.time() - start
                results[check.name] = {
                    "status": "error",
                    "critical": check.critical,
                    "error": str(e),
                    "time_ms": round(elapsed * 1000, 2),
                }
        self.last_results = results
        return results

    def is_healthy(self):
        if not self.last_results:
            return False
        for name, result in self.last_results.items():
            if result["critical"] and result["status"] != "healthy":
                return False
        return True

    def unhealthy_checks(self):
        return [name for name, r in self.last_results.items() if r["status"] != "healthy"]

    def summary(self):
        healthy = sum(1 for r in self.last_results.values() if r["status"] == "healthy")
        total = len(self.last_results)
        return {
            "total": total,
            "healthy": healthy,
            "unhealthy": total - healthy,
            "overall": "healthy" if self.is_healthy() else "unhealthy",
        }
''',


    # ── Scientific Computing ─────────────────────────────────────────────

    "fft_simple": '''
import math

def fft(x):
    n = len(x)
    if n <= 1:
        return x
    if n % 2 != 0:
        raise ValueError("Length must be a power of 2")
    even = fft(x[0::2])
    odd = fft(x[1::2])
    result = [0] * n
    for k in range(n // 2):
        angle = -2 * math.pi * k / n
        w_real = math.cos(angle)
        w_imag = math.sin(angle)
        if isinstance(odd[k], tuple):
            o_real, o_imag = odd[k]
        else:
            o_real, o_imag = odd[k], 0.0
        if isinstance(even[k], tuple):
            e_real, e_imag = even[k]
        else:
            e_real, e_imag = even[k], 0.0
        t_real = w_real * o_real - w_imag * o_imag
        t_imag = w_real * o_imag + w_imag * o_real
        result[k] = (e_real + t_real, e_imag + t_imag)
        result[k + n // 2] = (e_real - t_real, e_imag - t_imag)
    return result

def magnitude_spectrum(x):
    spectrum = fft(x)
    return [math.sqrt(r ** 2 + i ** 2) for r, i in spectrum]

def power_spectrum(x):
    spectrum = fft(x)
    return [r ** 2 + i ** 2 for r, i in spectrum]

def dominant_frequency(x, sample_rate=1.0):
    mags = magnitude_spectrum(x)
    half = len(mags) // 2
    max_idx = max(range(1, half), key=lambda i: mags[i])
    return max_idx * sample_rate / len(x)
''',

    "interpolation": '''
def linear_interp(x0, y0, x1, y1, x):
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
            return linear_interp(xs[i], ys[i], xs[i + 1], ys[i + 1], x)
    return ys[-1]

def lagrange_interp(xs, ys, x):
    n = len(xs)
    result = 0.0
    for i in range(n):
        term = ys[i]
        for j in range(n):
            if i != j:
                term *= (x - xs[j]) / (xs[i] - xs[j])
        result += term
    return result

def cubic_spline_natural(xs, ys):
    n = len(xs) - 1
    h = [xs[i + 1] - xs[i] for i in range(n)]
    alpha = [0.0] * (n + 1)
    for i in range(1, n):
        alpha[i] = (3 / h[i]) * (ys[i + 1] - ys[i]) - (3 / h[i - 1]) * (ys[i] - ys[i - 1])
    l = [1.0] * (n + 1)
    mu = [0.0] * (n + 1)
    z = [0.0] * (n + 1)
    for i in range(1, n):
        l[i] = 2 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1]
        mu[i] = h[i] / l[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i]
    c = [0.0] * (n + 1)
    b = [0.0] * n
    d = [0.0] * n
    for j in range(n - 1, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = (ys[j + 1] - ys[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3
        d[j] = (c[j + 1] - c[j]) / (3 * h[j])
    return [(ys[i], b[i], c[i], d[i]) for i in range(n)]
''',

    "numerical_integrate": '''
def trapezoidal(f, a, b, n=100):
    h = (b - a) / n
    total = 0.5 * (f(a) + f(b))
    for i in range(1, n):
        total += f(a + i * h)
    return total * h

def simpson(f, a, b, n=100):
    if n % 2 != 0:
        n += 1
    h = (b - a) / n
    total = f(a) + f(b)
    for i in range(1, n):
        x = a + i * h
        if i % 2 == 0:
            total += 2 * f(x)
        else:
            total += 4 * f(x)
    return total * h / 3

def midpoint(f, a, b, n=100):
    h = (b - a) / n
    total = 0.0
    for i in range(n):
        mid = a + (i + 0.5) * h
        total += f(mid)
    return total * h

def romberg(f, a, b, max_iter=10, tol=1e-10):
    R = [[0.0] * (max_iter + 1) for _ in range(max_iter + 1)]
    R[0][0] = 0.5 * (b - a) * (f(a) + f(b))
    for n in range(1, max_iter + 1):
        h = (b - a) / (2 ** n)
        total = 0.0
        for k in range(1, 2 ** (n - 1) + 1):
            total += f(a + (2 * k - 1) * h)
        R[n][0] = 0.5 * R[n - 1][0] + h * total
        for m in range(1, n + 1):
            R[n][m] = R[n][m - 1] + (R[n][m - 1] - R[n - 1][m - 1]) / (4 ** m - 1)
        if n > 0 and abs(R[n][n] - R[n - 1][n - 1]) < tol:
            return R[n][n]
    return R[max_iter][max_iter]
''',

    "ode_euler": '''
def euler_method(f, y0, t0, t_end, h):
    t = t0
    y = y0
    trajectory = [(t, y)]
    while t < t_end:
        y = y + h * f(t, y)
        t = t + h
        trajectory.append((t, y))
    return trajectory

def improved_euler(f, y0, t0, t_end, h):
    t = t0
    y = y0
    trajectory = [(t, y)]
    while t < t_end:
        k1 = f(t, y)
        k2 = f(t + h, y + h * k1)
        y = y + h * (k1 + k2) / 2
        t = t + h
        trajectory.append((t, y))
    return trajectory

def rk4(f, y0, t0, t_end, h):
    t = t0
    y = y0
    trajectory = [(t, y)]
    while t < t_end:
        k1 = f(t, y)
        k2 = f(t + h / 2, y + h * k1 / 2)
        k3 = f(t + h / 2, y + h * k2 / 2)
        k4 = f(t + h, y + h * k3)
        y = y + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6
        t = t + h
        trajectory.append((t, y))
    return trajectory

def compare_methods(f, y0, t0, t_end, h, exact=None):
    results = {
        "euler": euler_method(f, y0, t0, t_end, h),
        "improved_euler": improved_euler(f, y0, t0, t_end, h),
        "rk4": rk4(f, y0, t0, t_end, h),
    }
    if exact:
        for name, traj in results.items():
            errors = [abs(y - exact(t)) for t, y in traj]
            results[name + "_max_error"] = max(errors)
    return results
''',

    "signal_filter": '''
import math

def moving_average(signal, window):
    n = len(signal)
    result = []
    for i in range(n):
        start = max(0, i - window // 2)
        end = min(n, i + window // 2 + 1)
        result.append(sum(signal[start:end]) / (end - start))
    return result

def exponential_smooth(signal, alpha=0.3):
    if not signal:
        return []
    result = [signal[0]]
    for i in range(1, len(signal)):
        val = alpha * signal[i] + (1 - alpha) * result[-1]
        result.append(val)
    return result

def high_pass(signal, alpha=0.5):
    if len(signal) < 2:
        return list(signal)
    result = [signal[0]]
    for i in range(1, len(signal)):
        val = alpha * (result[-1] + signal[i] - signal[i - 1])
        result.append(val)
    return result

def detect_peaks(signal, threshold=0.0):
    peaks = []
    for i in range(1, len(signal) - 1):
        if signal[i] > signal[i - 1] and signal[i] > signal[i + 1]:
            if signal[i] > threshold:
                peaks.append(i)
    return peaks

def snr(signal, noise):
    signal_power = sum(s ** 2 for s in signal) / len(signal)
    noise_power = sum(n ** 2 for n in noise) / len(noise)
    if noise_power == 0:
        return float("inf")
    return 10 * math.log10(signal_power / noise_power)

def normalize(signal):
    min_val = min(signal)
    max_val = max(signal)
    rng = max_val - min_val
    if rng == 0:
        return [0.0] * len(signal)
    return [(s - min_val) / rng for s in signal]
''',

    "curve_fit": '''
import math

def least_squares_linear(x, y):
    n = len(x)
    sx = sum(x)
    sy = sum(y)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    sx2 = sum(xi ** 2 for xi in x)
    denom = n * sx2 - sx ** 2
    if denom == 0:
        return 0.0, sum(y) / n
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    return a, b

def poly_fit(x, y, degree):
    n = degree + 1
    matrix = [[0.0] * (n + 1) for _ in range(n)]
    for row in range(n):
        for col in range(n):
            matrix[row][col] = sum(xi ** (row + col) for xi in x)
        matrix[row][n] = sum(yi * xi ** row for xi, yi in zip(x, y))
    for col in range(n):
        max_row = max(range(col, n), key=lambda r: abs(matrix[r][col]))
        matrix[col], matrix[max_row] = matrix[max_row], matrix[col]
        for row in range(col + 1, n):
            factor = matrix[row][col] / matrix[col][col]
            for j in range(col, n + 1):
                matrix[row][j] -= factor * matrix[col][j]
    coeffs = [0.0] * n
    for i in range(n - 1, -1, -1):
        coeffs[i] = matrix[i][n]
        for j in range(i + 1, n):
            coeffs[i] -= matrix[i][j] * coeffs[j]
        coeffs[i] /= matrix[i][i]
    return coeffs

def r_squared(x, y, coeffs):
    y_mean = sum(y) / len(y)
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    ss_res = sum((yi - sum(c * xi ** k for k, c in enumerate(coeffs))) ** 2
                 for xi, yi in zip(x, y))
    return 1.0 - ss_res / ss_tot if ss_tot != 0 else 1.0

def exponential_fit(x, y):
    log_y = [math.log(yi) for yi in y if yi > 0]
    a_log, b_log = least_squares_linear(x[:len(log_y)], log_y)
    return math.exp(b_log), a_log
''',

    "monte_carlo_pi": '''
import random as _random
import math

def estimate_pi(n_points, seed=42):
    rng = _random.Random(seed)
    inside = 0
    for _ in range(n_points):
        x = rng.uniform(-1, 1)
        y = rng.uniform(-1, 1)
        if x * x + y * y <= 1:
            inside += 1
    return 4 * inside / n_points

def pi_convergence(max_points, step=100, seed=42):
    rng = _random.Random(seed)
    inside = 0
    estimates = []
    for i in range(1, max_points + 1):
        x = rng.uniform(-1, 1)
        y = rng.uniform(-1, 1)
        if x * x + y * y <= 1:
            inside += 1
        if i % step == 0:
            est = 4 * inside / i
            estimates.append({"n": i, "estimate": est, "error": abs(est - math.pi)})
    return estimates

def monte_carlo_integrate(f, a, b, n=10000, seed=42):
    rng = _random.Random(seed)
    total = 0.0
    for _ in range(n):
        x = rng.uniform(a, b)
        total += f(x)
    return (b - a) * total / n

def buffon_needle(n_throws, needle_length=1.0, line_spacing=2.0, seed=42):
    rng = _random.Random(seed)
    crossings = 0
    for _ in range(n_throws):
        center = rng.uniform(0, line_spacing / 2)
        angle = rng.uniform(0, math.pi)
        half_proj = (needle_length / 2) * math.sin(angle)
        if half_proj >= center:
            crossings += 1
    if crossings == 0:
        return 0.0
    return (2 * needle_length * n_throws) / (line_spacing * crossings)
''',

    "gradient_descent": '''
def gradient_descent(grad_fn, x0, learning_rate=0.01, max_iter=1000, tol=1e-8):
    x = list(x0)
    history = [list(x)]
    for iteration in range(max_iter):
        grad = grad_fn(x)
        new_x = [xi - learning_rate * gi for xi, gi in zip(x, grad)]
        diff = sum((a - b) ** 2 for a, b in zip(new_x, x)) ** 0.5
        x = new_x
        history.append(list(x))
        if diff < tol:
            break
    return x, history

def gradient_descent_momentum(grad_fn, x0, lr=0.01, momentum=0.9, max_iter=1000):
    x = list(x0)
    velocity = [0.0] * len(x0)
    history = [list(x)]
    for iteration in range(max_iter):
        grad = grad_fn(x)
        velocity = [momentum * v - lr * g for v, g in zip(velocity, grad)]
        x = [xi + vi for xi, vi in zip(x, velocity)]
        history.append(list(x))
    return x, history

def line_search(f, grad_fn, x, direction, alpha=1.0, beta=0.5, sigma=1e-4):
    fx = f(x)
    grad = grad_fn(x)
    slope = sum(g * d for g, d in zip(grad, direction))
    while f([xi + alpha * di for xi, di in zip(x, direction)]) > fx + sigma * alpha * slope:
        alpha *= beta
    return alpha

def numerical_gradient(f, x, epsilon=1e-7):
    grad = []
    for i in range(len(x)):
        x_plus = list(x)
        x_minus = list(x)
        x_plus[i] += epsilon
        x_minus[i] -= epsilon
        grad.append((f(x_plus) - f(x_minus)) / (2 * epsilon))
    return grad
''',

    "pca_simple": '''
import math

def mean_vec(data):
    n = len(data)
    d = len(data[0])
    return [sum(row[j] for row in data) / n for j in range(d)]

def center_data(data):
    mu = mean_vec(data)
    return [[row[j] - mu[j] for j in range(len(mu))] for row in data]

def covariance_matrix(data):
    centered = center_data(data)
    n = len(centered)
    d = len(centered[0])
    cov = [[0.0] * d for _ in range(d)]
    for i in range(d):
        for j in range(d):
            cov[i][j] = sum(centered[k][i] * centered[k][j] for k in range(n)) / (n - 1)
    return cov

def power_iteration(matrix, n_iter=100):
    n = len(matrix)
    vec = [1.0] * n
    for _ in range(n_iter):
        new_vec = [0.0] * n
        for i in range(n):
            for j in range(n):
                new_vec[i] += matrix[i][j] * vec[j]
        norm = math.sqrt(sum(v ** 2 for v in new_vec))
        vec = [v / norm for v in new_vec]
    eigenvalue = sum(
        vec[i] * sum(matrix[i][j] * vec[j] for j in range(n))
        for i in range(n)
    )
    return eigenvalue, vec

def project(data, components):
    result = []
    for row in data:
        projected = []
        for comp in components:
            val = sum(r * c for r, c in zip(row, comp))
            projected.append(val)
        result.append(projected)
    return result

def explained_variance(eigenvalues):
    total = sum(eigenvalues)
    return [ev / total for ev in eigenvalues] if total > 0 else eigenvalues
''',

    "kalman_filter_1d": '''
class KalmanFilter1D:
    def __init__(self, initial_state, initial_uncertainty, process_noise, measurement_noise):
        self.state = initial_state
        self.uncertainty = initial_uncertainty
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.history = []

    def predict(self, motion=0.0):
        self.state = self.state + motion
        self.uncertainty = self.uncertainty + self.process_noise
        return self.state, self.uncertainty

    def update(self, measurement):
        kalman_gain = self.uncertainty / (self.uncertainty + self.measurement_noise)
        self.state = self.state + kalman_gain * (measurement - self.state)
        self.uncertainty = (1 - kalman_gain) * self.uncertainty
        self.history.append({
            "measurement": measurement,
            "state": self.state,
            "uncertainty": self.uncertainty,
            "gain": kalman_gain,
        })
        return self.state, self.uncertainty

    def filter_sequence(self, measurements, motions=None):
        results = []
        for i, z in enumerate(measurements):
            motion = motions[i] if motions else 0.0
            self.predict(motion)
            state, unc = self.update(z)
            results.append({"state": state, "uncertainty": unc})
        return results

    def smooth(self):
        if len(self.history) < 2:
            return [h["state"] for h in self.history]
        smoothed = [self.history[-1]["state"]]
        for i in range(len(self.history) - 2, -1, -1):
            s = self.history[i]["state"]
            u = self.history[i]["uncertainty"]
            gain = u / (u + self.process_noise)
            smoothed_val = s + gain * (smoothed[0] - s)
            smoothed.insert(0, smoothed_val)
        return smoothed

    def reset(self, state, uncertainty):
        self.state = state
        self.uncertainty = uncertainty
        self.history = []
''',

}

STRATEGIES = ["eager", "exhaustive", "iterative"]

# ── Literature baselines (NOT measured — sourced from publications) ───────

LITERATURE_BASELINES = {
    "source": "Literature values — NOT measured by this script",
    "lean4_pipeline_stages": {
        "description": "LEAN 4: parse → elaborate → type-check → kernel-check → extract",
        "stages": 5,
        "has_trust": False,
        "has_fragment_routing": False,
        "has_descent": False,
        "cite": "de Moura & Ullrich, CADE 2021",
    },
    "fstar_pipeline_stages": {
        "description": "F*: parse → desugar → type-check → VC gen → Z3 → extract",
        "stages": 6,
        "has_trust": False,
        "has_fragment_routing": False,
        "has_descent": False,
        "cite": "Swamy et al., POPL 2016",
    },
    "gpt4o_verification": {
        "description": "GPT-4o: prompt → generate answer (no pipeline, no proof structure)",
        "stages": 1,
        "has_trust": False,
        "has_fragment_routing": False,
        "has_descent": False,
        "accuracy_estimate": "60-70% on code correctness (OpenAI, 2024)",
    },
}


def main():
    print("=" * 76)
    print("SEMINAL EXPERIMENT — Judgment Geometry: End-to-End Pipeline")
    print("  All numbers from `python3 -m jugeo` CLI (subprocess)")
    print("=" * 76)
    print()

    tmpfiles = []
    all_results = []

    # ── 1. Run `jugeo prove` on every program × strategy ─────────────────
    for name, source in PROGRAMS.items():
        path = write_temp(source)
        tmpfiles.append(path)

        tree = ast.parse(source)
        n_nodes = sum(1 for _ in ast.walk(tree))
        n_funcs = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))

        strategy_rows = []
        for strategy in STRATEGIES:
            t0 = time.perf_counter()
            objs = run_jugeo("prove", path, "--strategy", strategy)
            wall_s = time.perf_counter() - t0

            prove = objs[0] if objs else {}
            formal = objs[1] if len(objs) > 1 else {}

            finfo = (prove.get("files") or [{}])[0]
            cat = formal.get("formal_verification", {}).get("category_structure", {})
            trust_ax = formal.get("formal_verification", {}).get("trust_algebra", {})
            obs_van = formal.get("formal_verification", {}).get("obstruction_vanishing", {})
            desc_loc = formal.get("descent_locality", {})

            strategy_rows.append({
                "strategy": strategy,
                "wall_s": round(wall_s, 4),
                "elapsed_s": prove.get("elapsed_s", 0),
                "verdict": finfo.get("verdict", "?"),
                "trust": finfo.get("trust", "?"),
                "coordinates": finfo.get("coordinates", 0),
                "local_sections": finfo.get("local_sections", 0),
                "propositions_total": finfo.get("propositions_total", 0),
                "propositions_ok": finfo.get("propositions_ok", 0),
                "obstructions": len(finfo.get("obstructions", [])),
                "certificate_hash": (finfo.get("certificate") or {}).get("hash", ""),
                "n_objects": cat.get("n_objects", 0),
                "n_morphisms": cat.get("n_morphisms", 0),
                "category_ok": cat.get("axioms", {}).get("all_pass", False),
                "trust_algebra_ok": trust_ax.get("passed", False),
                "H1": obs_van.get("H1", "?"),
                "descent_ok": desc_loc.get("effective_descent", {}).get("all_effective", False),
            })

        result = {
            "name": name,
            "ast_nodes": n_nodes,
            "functions": n_funcs,
            "strategies": strategy_rows,
        }
        all_results.append(result)

    # ── 2. Run `jugeo encode` on a subset ────────────────────────────────
    encode_names = list(PROGRAMS.keys())[:10]
    encode_results = []
    for name in encode_names:
        path = write_temp(PROGRAMS[name])
        tmpfiles.append(path)
        t0 = time.perf_counter()
        objs = run_jugeo("encode", path)
        wall_s = time.perf_counter() - t0
        enc = objs[0] if objs else {}
        encode_results.append({
            "name": name,
            "wall_s": round(wall_s, 4),
            "coordinates": enc.get("coordinates", 0),
            "morphisms": enc.get("morphisms", 0),
            "covering_families": enc.get("covering_families", 0),
        })

    # ── 3. Run `jugeo bugs` on a subset ──────────────────────────────────
    bug_names = list(PROGRAMS.keys())[10:20]
    bug_results = []
    for name in bug_names:
        path = write_temp(PROGRAMS[name])
        tmpfiles.append(path)
        t0 = time.perf_counter()
        objs = run_jugeo("bugs", path)
        wall_s = time.perf_counter() - t0
        bugs_obj = objs[0] if objs else {}
        if isinstance(bugs_obj, list):
            bugs_obj = bugs_obj[0] if bugs_obj else {}
        bug_results.append({
            "name": name,
            "wall_s": round(wall_s, 4),
            "status": bugs_obj.get("status", "?"),
            "bug_count": bugs_obj.get("count", 0),
            "obstruction_count": bugs_obj.get("obstruction_count", 0),
        })

    # ── 4. Run `jugeo equiv` on pairs ────────────────────────────────────
    prog_names = list(PROGRAMS.keys())
    equiv_pairs = [(prog_names[i], prog_names[i + 1]) for i in range(0, 10, 2)]
    equiv_results = []
    for left_name, right_name in equiv_pairs:
        left_path = write_temp(PROGRAMS[left_name])
        right_path = write_temp(PROGRAMS[right_name])
        tmpfiles.extend([left_path, right_path])
        t0 = time.perf_counter()
        objs = run_jugeo("equiv", left_path, right_path)
        wall_s = time.perf_counter() - t0
        equiv_obj = objs[0] if objs else {}
        equiv_results.append({
            "left": left_name,
            "right": right_name,
            "wall_s": round(wall_s, 4),
            "verdict": equiv_obj.get("verdict", "?"),
            "method": equiv_obj.get("method", "?"),
            "obstructions": len(equiv_obj.get("obstructions", [])),
            "cover_refinement": equiv_obj.get("cover_refinement", None),
        })

    # ── 5. Run `jugeo descend` on a subset ───────────────────────────────
    descend_names = list(PROGRAMS.keys())[20:25]
    descend_results = []
    for name in descend_names:
        path = write_temp(PROGRAMS[name])
        tmpfiles.append(path)
        t0 = time.perf_counter()
        objs = run_jugeo("descend", path)
        wall_s = time.perf_counter() - t0
        desc_obj = objs[0] if objs else {}
        descend_results.append({
            "name": name,
            "wall_s": round(wall_s, 4),
            "effective": desc_obj.get("effective_descent", {}).get("all_effective", False),
            "local_sections": desc_obj.get("local_sections", 0),
            "obstructions": len(desc_obj.get("obstructions", [])),
        })

    # ── Print tables ─────────────────────────────────────────────────────
    print(f"PIPELINE RESULTS — {len(all_results)} programs × {len(STRATEGIES)} strategies")
    print("-" * 100)
    print(f"  {'Program':<24} {'Strategy':<11} {'Verdict':<10} {'Trust':<20} "
          f"{'Coords':>6} {'Props':>5} {'Obs':>4} {'Wall(s)':>8}")
    print(f"  {'-'*94}")
    for r in all_results:
        for s in r["strategies"]:
            print(f"  {r['name']:<24} {s['strategy']:<11} {s['verdict']:<10} "
                  f"{s['trust']:<20} {s['coordinates']:>6} "
                  f"{s['propositions_total']:>5} {s['obstructions']:>4} "
                  f"{s['wall_s']:>8.4f}")

    # Formal verification summary (eager strategy only)
    print(f"\nFORMAL VERIFICATION (eager strategy):")
    print("-" * 76)
    print(f"  {'Program':<24} {'Cat OK':>7} {'Trust OK':>9} {'H1':>4} {'Desc OK':>8}")
    print(f"  {'-'*56}")
    for r in all_results:
        s = r["strategies"][0]
        print(f"  {r['name']:<24} {str(s['category_ok']):>7} "
              f"{str(s['trust_algebra_ok']):>9} {s['H1']:>4} "
              f"{str(s['descent_ok']):>8}")

    # Encode results
    print(f"\nENCODE RESULTS (from `jugeo encode` CLI):")
    print("-" * 76)
    for e in encode_results:
        print(f"  {e['name']:<24} coords={e['coordinates']} morphs={e['morphisms']} "
              f"covers={e['covering_families']} wall={e['wall_s']:.4f}s")

    # Bug detection
    print(f"\nBUG DETECTION (from `jugeo bugs` CLI):")
    print("-" * 76)
    for b in bug_results:
        print(f"  {b['name']:<24} status={b['status']:<4} bugs={b['bug_count']} "
              f"obstructions={b['obstruction_count']} wall={b['wall_s']:.4f}s")

    # Equivalence checking
    print(f"\nEQUIVALENCE CHECKING (from `jugeo equiv` CLI):")
    print("-" * 76)
    for eq in equiv_results:
        print(f"  {eq['left']:<20} vs {eq['right']:<20} verdict={eq['verdict']} "
              f"method={eq['method']} wall={eq['wall_s']:.4f}s")

    # Descent results
    print(f"\nDESCENT ANALYSIS (from `jugeo descend` CLI):")
    print("-" * 76)
    for d in descend_results:
        print(f"  {d['name']:<24} effective={d['effective']} "
              f"sections={d['local_sections']} obstructions={d['obstructions']} "
              f"wall={d['wall_s']:.4f}s")

    # Strategy comparison
    print(f"\nSTRATEGY COMPARISON (wall-clock seconds):")
    print("-" * 76)
    print(f"  {'Program':<24} {'eager':>8} {'exhaustive':>11} {'iterative':>10}")
    print(f"  {'-'*56}")
    for r in all_results[:20]:  # show first 20 for readability
        by_s = {s["strategy"]: s["wall_s"] for s in r["strategies"]}
        print(f"  {r['name']:<24} {by_s.get('eager',0):>8.4f} "
              f"{by_s.get('exhaustive',0):>11.4f} {by_s.get('iterative',0):>10.4f}")
    if len(all_results) > 20:
        print(f"  ... and {len(all_results) - 20} more programs")

    # Literature comparison
    print(f"\nPIPELINE COMPARISON (literature baselines, NOT measured):")
    print("-" * 76)
    print(f"  {'System':<14} {'Stages':>6} {'Trust':>6} {'Fragment routing':>16} {'Descent':>8}")
    print(f"  {'-'*52}")
    print(f"  {'JuGeo':<14} {'6':>6} {'Yes':>6} {'Yes':>16} {'Yes':>8}")
    for key, info in LITERATURE_BASELINES.items():
        if key == "source":
            continue
        yn = lambda b: "Yes" if b else "No"
        label = {"lean4_pipeline_stages": "LEAN 4",
                 "fstar_pipeline_stages": "F*",
                 "gpt4o_verification": "GPT-4o"}.get(key, key)
        print(f"  {label:<14} {info['stages']:>6} {yn(info['has_trust']):>6} "
              f"{yn(info['has_fragment_routing']):>16} {yn(info['has_descent']):>8}")

    # ── Summary statistics ───────────────────────────────────────────────
    eager_results = [r["strategies"][0] for r in all_results]
    avg_wall = sum(s["wall_s"] for s in eager_results) / len(eager_results)
    avg_coords = sum(s["coordinates"] for s in eager_results) / len(eager_results)
    avg_props = sum(s["propositions_total"] for s in eager_results) / len(eager_results)
    print(f"\nSUMMARY STATISTICS ({len(all_results)} programs, eager strategy):")
    print(f"  Average wall time:   {avg_wall:.4f}s")
    print(f"  Average coordinates: {avg_coords:.1f}")
    print(f"  Average propositions: {avg_props:.1f}")

    # ── Save JSON results ────────────────────────────────────────────────
    output = {
        "experiment": "end_to_end_pipeline",
        "paper": "seminal",
        "note": "All JuGeo numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_programs": len(all_results),
        "programs": all_results,
        "encode_results": encode_results,
        "bug_detection": bug_results,
        "equivalence_checking": equiv_results,
        "descent_analysis": descend_results,
        "literature_baselines": LITERATURE_BASELINES,
        "summary": {
            "avg_wall_s": round(avg_wall, 4),
            "avg_coordinates": round(avg_coords, 1),
            "avg_propositions": round(avg_props, 1),
        },
    }
    outpath = os.path.join(os.path.dirname(__file__), "results_paper00.json")
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults → {outpath}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
