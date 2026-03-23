#!/usr/bin/env python3
"""Paper 04 Experiment — Cohomological Obstructions: When H¹≠0 Blocks Descent.

Runs jugeo prove and jugeo encode on 130 Python programs (100 clean + 30 intentionally
inconsistent) to measure H¹ cohomology values and classify obstruction behavior.

Every number is produced by calling the `python3 -m jugeo` CLI as a subprocess.
Re-run: python3 experiments/exp04_cohomological_obstructions.py
"""
import ast, json, os, random, subprocess, sys, tempfile, time, statistics

random.seed(42)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


# ── 100 clean programs ───────────────────────────────────────────────────

PROGRAMS = {

    # ── Sorting algorithms ───────────────────────────────────────────────

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
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True

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

def quicksort(arr, low, high):
    if low < high:
        pi = partition(arr, low, high)
        quicksort(arr, low, pi - 1)
        quicksort(arr, pi + 1, high)

def sort_array(data):
    arr = list(data)
    if len(arr) <= 1:
        return arr
    quicksort(arr, 0, len(arr) - 1)
    return arr

def verify_sorted(original, sorted_arr):
    assert len(original) == len(sorted_arr)
    for i in range(len(sorted_arr) - 1):
        assert sorted_arr[i] <= sorted_arr[i + 1]
    return True
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

def build_max_heap(arr):
    n = len(arr)
    for i in range(n // 2 - 1, -1, -1):
        heapify(arr, n, i)
    return arr

def heap_peek(arr):
    if not arr:
        return None
    return arr[0]
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
''',

    "radix_sort": '''
def counting_sort_by_digit(arr, exp):
    n = len(arr)
    output = [0] * n
    count = [0] * 10
    for i in range(n):
        index = (arr[i] // exp) % 10
        count[index] += 1
    for i in range(1, 10):
        count[i] += count[i - 1]
    for i in range(n - 1, -1, -1):
        index = (arr[i] // exp) % 10
        output[count[index] - 1] = arr[i]
        count[index] -= 1
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

def get_digit_count(n):
    if n == 0:
        return 1
    count = 0
    while n > 0:
        count += 1
        n //= 10
    return count
''',

    "counting_sort": '''
def counting_sort(arr):
    if not arr:
        return []
    min_val = min(arr)
    max_val = max(arr)
    range_val = max_val - min_val + 1
    count = [0] * range_val
    output = [0] * len(arr)
    for val in arr:
        count[val - min_val] += 1
    for i in range(1, range_val):
        count[i] += count[i - 1]
    for i in range(len(arr) - 1, -1, -1):
        output[count[arr[i] - min_val] - 1] = arr[i]
        count[arr[i] - min_val] -= 1
    return output

def frequency_table(arr):
    freq = {}
    for val in arr:
        freq[val] = freq.get(val, 0) + 1
    return freq

def most_frequent(arr):
    freq = frequency_table(arr)
    best = None
    best_count = 0
    for val, cnt in freq.items():
        if cnt > best_count:
            best = val
            best_count = cnt
    return best
''',

    "bucket_sort": '''
def bucket_sort(arr, bucket_count=10):
    if not arr:
        return []
    min_val = min(arr)
    max_val = max(arr)
    bucket_range = (max_val - min_val + 1) / bucket_count
    buckets = [[] for _ in range(bucket_count)]
    for val in arr:
        idx = int((val - min_val) / bucket_range)
        if idx == bucket_count:
            idx -= 1
        buckets[idx].append(val)
    result = []
    for bucket in buckets:
        bucket.sort()
        result.extend(bucket)
    return result

def distribute_into_buckets(arr, n_buckets):
    if not arr:
        return [[] for _ in range(n_buckets)]
    lo = min(arr)
    hi = max(arr)
    span = (hi - lo + 1) / n_buckets
    buckets = [[] for _ in range(n_buckets)]
    for v in arr:
        idx = min(int((v - lo) / span), n_buckets - 1)
        buckets[idx].append(v)
    return buckets
''',

    "shell_sort": '''
def shell_sort(arr):
    result = list(arr)
    n = len(result)
    gap = n // 2
    while gap > 0:
        for i in range(gap, n):
            temp = result[i]
            j = i
            while j >= gap and result[j - gap] > temp:
                result[j] = result[j - gap]
                j -= gap
            result[j] = temp
        gap //= 2
    return result

def generate_gaps(n):
    gaps = []
    gap = 1
    while gap < n:
        gaps.append(gap)
        gap = gap * 3 + 1
    gaps.reverse()
    return gaps

def shell_sort_custom_gaps(arr, gaps):
    result = list(arr)
    n = len(result)
    for gap in gaps:
        for i in range(gap, n):
            temp = result[i]
            j = i
            while j >= gap and result[j - gap] > temp:
                result[j] = result[j - gap]
                j -= gap
            result[j] = temp
    return result
''',

    "selection_sort": '''
def selection_sort(arr):
    result = list(arr)
    n = len(result)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if result[j] < result[min_idx]:
                min_idx = j
        result[i], result[min_idx] = result[min_idx], result[i]
    return result

def find_kth_smallest(arr, k):
    sorted_arr = selection_sort(arr)
    if k < 0 or k >= len(sorted_arr):
        return None
    return sorted_arr[k]

def double_selection_sort(arr):
    result = list(arr)
    n = len(result)
    for i in range(n // 2):
        min_idx = i
        max_idx = i
        for j in range(i + 1, n - i):
            if result[j] < result[min_idx]:
                min_idx = j
            if result[j] > result[max_idx]:
                max_idx = j
        result[i], result[min_idx] = result[min_idx], result[i]
        if max_idx == i:
            max_idx = min_idx
        result[n - 1 - i], result[max_idx] = result[max_idx], result[n - 1 - i]
    return result
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
        return None
    return result

def has_cycle(graph):
    result = topological_sort(graph)
    return result is None

def build_dependency_order(tasks, deps):
    graph = {t: [] for t in tasks}
    for src, dst in deps:
        graph[src].append(dst)
    return topological_sort(graph)
''',


    # ── Data structures ──────────────────────────────────────────────────

    "linked_list": '''
class Node:
    def __init__(self, val, nxt=None):
        self.val = val
        self.nxt = nxt

def make_list(values):
    head = None
    for v in reversed(values):
        head = Node(v, head)
    return head

def to_array(head):
    result = []
    current = head
    while current is not None:
        result.append(current.val)
        current = current.nxt
    return result

def length(head):
    count = 0
    current = head
    while current is not None:
        count += 1
        current = current.nxt
    return count

def reverse_list(head):
    prev = None
    current = head
    while current is not None:
        nxt = current.nxt
        current.nxt = prev
        prev = current
        current = nxt
    return prev

def find_value(head, target):
    current = head
    idx = 0
    while current is not None:
        if current.val == target:
            return idx
        current = current.nxt
        idx += 1
    return -1
''',

    "binary_search_tree": '''
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

def search(root, key):
    if root is None:
        return False
    if key == root.key:
        return True
    if key < root.key:
        return search(root.left, key)
    return search(root.right, key)

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.key] + inorder(root.right)

def find_min(root):
    current = root
    while current and current.left:
        current = current.left
    return current.key if current else None

def tree_height(root):
    if root is None:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))
''',

    "hash_table": '''
class HashTable:
    def __init__(self, size=16):
        self.size = size
        self.count = 0
        self.buckets = [[] for _ in range(size)]

    def _hash(self, key):
        h = 0
        for ch in str(key):
            h = (h * 31 + ord(ch)) % self.size
        return h

    def put(self, key, value):
        idx = self._hash(key)
        bucket = self.buckets[idx]
        for i, (k, v) in enumerate(bucket):
            if k == key:
                bucket[i] = (key, value)
                return
        bucket.append((key, value))
        self.count += 1

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self.buckets[idx]:
            if k == key:
                return v
        return default

    def delete(self, key):
        idx = self._hash(key)
        bucket = self.buckets[idx]
        for i, (k, v) in enumerate(bucket):
            if k == key:
                bucket.pop(i)
                self.count -= 1
                return True
        return False

    def keys(self):
        result = []
        for bucket in self.buckets:
            for k, v in bucket:
                result.append(k)
        return result
''',

    "trie": '''
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
                return False
            node = node.children[ch]
        return True

    def collect_words(self, node=None, prefix=""):
        if node is None:
            node = self.root
        words = []
        if node.is_end:
            words.append(prefix)
        for ch, child in sorted(node.children.items()):
            words.extend(self.collect_words(child, prefix + ch))
        return words
''',

    "graph_bfs": '''
def bfs(graph, start):
    visited = set()
    queue = [start]
    visited.add(start)
    order = []
    while queue:
        node = queue.pop(0)
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
    queue = [(start, [start])]
    while queue:
        node, path = queue.pop(0)
        for neighbor in graph.get(node, []):
            if neighbor == end:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None

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

    "priority_queue": '''
class MinHeap:
    def __init__(self):
        self.heap = []

    def _parent(self, i):
        return (i - 1) // 2

    def _left(self, i):
        return 2 * i + 1

    def _right(self, i):
        return 2 * i + 2

    def _swap(self, i, j):
        self.heap[i], self.heap[j] = self.heap[j], self.heap[i]

    def push(self, val):
        self.heap.append(val)
        i = len(self.heap) - 1
        while i > 0 and self.heap[self._parent(i)] > self.heap[i]:
            self._swap(i, self._parent(i))
            i = self._parent(i)

    def pop(self):
        if not self.heap:
            return None
        if len(self.heap) == 1:
            return self.heap.pop()
        root = self.heap[0]
        self.heap[0] = self.heap.pop()
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
            self._swap(i, smallest)
            self._sift_down(smallest)

    def peek(self):
        return self.heap[0] if self.heap else None

    def size(self):
        return len(self.heap)
''',

    "stack_with_min": '''
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

    "doubly_linked_list": '''
class DNode:
    def __init__(self, val):
        self.val = val
        self.prev = None
        self.nxt = None

class DoublyLinkedList:
    def __init__(self):
        self.head = None
        self.tail = None
        self.count = 0

    def append(self, val):
        node = DNode(val)
        if self.tail is None:
            self.head = node
            self.tail = node
        else:
            node.prev = self.tail
            self.tail.nxt = node
            self.tail = node
        self.count += 1

    def prepend(self, val):
        node = DNode(val)
        if self.head is None:
            self.head = node
            self.tail = node
        else:
            node.nxt = self.head
            self.head.prev = node
            self.head = node
        self.count += 1

    def remove(self, val):
        current = self.head
        while current:
            if current.val == val:
                if current.prev:
                    current.prev.nxt = current.nxt
                else:
                    self.head = current.nxt
                if current.nxt:
                    current.nxt.prev = current.prev
                else:
                    self.tail = current.prev
                self.count -= 1
                return True
            current = current.nxt
        return False

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.val)
            current = current.nxt
        return result
''',

    "circular_buffer": '''
class CircularBuffer:
    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = [None] * capacity
        self.head = 0
        self.tail = 0
        self.count = 0

    def push(self, item):
        if self.count == self.capacity:
            self.head = (self.head + 1) % self.capacity
        else:
            self.count += 1
        self.buffer[self.tail] = item
        self.tail = (self.tail + 1) % self.capacity

    def pop(self):
        if self.count == 0:
            return None
        item = self.buffer[self.head]
        self.buffer[self.head] = None
        self.head = (self.head + 1) % self.capacity
        self.count -= 1
        return item

    def peek(self):
        if self.count == 0:
            return None
        return self.buffer[self.head]

    def is_full(self):
        return self.count == self.capacity

    def is_empty(self):
        return self.count == 0

    def to_list(self):
        result = []
        idx = self.head
        for _ in range(self.count):
            result.append(self.buffer[idx])
            idx = (idx + 1) % self.capacity
        return result
''',

    "disjoint_set": '''
class DisjointSet:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.count = n

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
            self.parent[rx] = ry
        elif self.rank[rx] > self.rank[ry]:
            self.parent[ry] = rx
        else:
            self.parent[ry] = rx
            self.rank[rx] += 1
        self.count -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def component_count(self):
        return self.count

    def component_sizes(self):
        sizes = {}
        for i in range(len(self.parent)):
            root = self.find(i)
            sizes[root] = sizes.get(root, 0) + 1
        return list(sizes.values())
''',


    # ── Math computations ────────────────────────────────────────────────

    "matrix_multiply": '''
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

def identity_matrix(n):
    result = [[0] * n for _ in range(n)]
    for i in range(n):
        result[i][i] = 1
    return result

def matrix_add(a, b):
    rows = len(a)
    cols = len(a[0])
    return [[a[i][j] + b[i][j] for j in range(cols)] for i in range(rows)]
''',

    "prime_sieve": '''
def sieve_of_eratosthenes(limit):
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = False
    is_prime[1] = False
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
''',

    "polynomial_eval": '''
def evaluate(coeffs, x):
    result = 0
    for i, c in enumerate(coeffs):
        result += c * (x ** i)
    return result

def horner(coeffs, x):
    result = 0
    for c in reversed(coeffs):
        result = result * x + c
    return result

def add_poly(a, b):
    length = max(len(a), len(b))
    result = [0] * length
    for i in range(len(a)):
        result[i] += a[i]
    for i in range(len(b)):
        result[i] += b[i]
    return result

def multiply_poly(a, b):
    result = [0] * (len(a) + len(b) - 1)
    for i in range(len(a)):
        for j in range(len(b)):
            result[i + j] += a[i] * b[j]
    return result

def derivative(coeffs):
    if len(coeffs) <= 1:
        return [0]
    return [i * coeffs[i] for i in range(1, len(coeffs))]
''',

    "statistics_calc": '''
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
    return float(sorted_data[mid])

def variance(data):
    if len(data) < 2:
        return 0.0
    avg = mean(data)
    return sum((x - avg) ** 2 for x in data) / (len(data) - 1)

def std_dev(data):
    return variance(data) ** 0.5

def percentile(data, p):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return float(sorted_data[f])
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])

def mode(data):
    freq = {}
    for x in data:
        freq[x] = freq.get(x, 0) + 1
    max_count = max(freq.values())
    return [k for k, v in freq.items() if v == max_count]
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
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)

def mod_inverse(a, m):
    g, x, _ = extended_gcd(a % m, m)
    if g != 1:
        return None
    return x % m

def solve_linear_congruence(a, b, m):
    g, x, _ = extended_gcd(a, m)
    if b % g != 0:
        return None
    x0 = (x * (b // g)) % m
    solutions = []
    for i in range(g):
        solutions.append((x0 + i * (m // g)) % m)
    return solutions
''',

    "fibonacci_matrix": '''
def fib_recursive(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    return fib_recursive(n - 1) + fib_recursive(n - 2)

def fib_iterative(n):
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b

def matrix_mult_2x2(a, b):
    return [
        [a[0][0] * b[0][0] + a[0][1] * b[1][0],
         a[0][0] * b[0][1] + a[0][1] * b[1][1]],
        [a[1][0] * b[0][0] + a[1][1] * b[1][0],
         a[1][0] * b[0][1] + a[1][1] * b[1][1]],
    ]

def matrix_pow(m, n):
    if n == 1:
        return m
    if n % 2 == 0:
        half = matrix_pow(m, n // 2)
        return matrix_mult_2x2(half, half)
    return matrix_mult_2x2(m, matrix_pow(m, n - 1))

def fib_matrix(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    base = [[1, 1], [1, 0]]
    result = matrix_pow(base, n - 1)
    return result[0][0]
''',

    "newtons_method": '''
def newtons_sqrt(n, tolerance=1e-10):
    if n < 0:
        return None
    if n == 0:
        return 0.0
    guess = n / 2.0
    while True:
        new_guess = (guess + n / guess) / 2.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess

def newtons_root(f, df, x0, tol=1e-10, max_iter=100):
    x = x0
    for _ in range(max_iter):
        fx = f(x)
        dfx = df(x)
        if abs(dfx) < 1e-15:
            return None
        x_new = x - fx / dfx
        if abs(x_new - x) < tol:
            return x_new
        x = x_new
    return x

def cube_root(n):
    if n == 0:
        return 0.0
    sign = 1 if n > 0 else -1
    n = abs(n)
    guess = n / 3.0
    for _ in range(100):
        new_guess = (2 * guess + n / (guess * guess)) / 3.0
        if abs(new_guess - guess) < 1e-12:
            return sign * new_guess
        guess = new_guess
    return sign * guess
''',

    "combinatorics": '''
def factorial(n):
    if n < 0:
        return None
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

def choose(n, k):
    if k < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    k = min(k, n - k)
    result = 1
    for i in range(k):
        result = result * (n - i) // (i + 1)
    return result

def permutations_count(n, k):
    if k < 0 or k > n:
        return 0
    result = 1
    for i in range(n, n - k, -1):
        result *= i
    return result

def generate_combinations(items, k):
    if k == 0:
        return [[]]
    if not items:
        return []
    first = items[0]
    rest = items[1:]
    with_first = [[first] + c for c in generate_combinations(rest, k - 1)]
    without_first = generate_combinations(rest, k)
    return with_first + without_first

def catalan(n):
    return choose(2 * n, n) // (n + 1)
''',

    "modular_exponentiation": '''
def mod_pow(base, exp, mod):
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp = exp >> 1
        base = (base * base) % mod
    return result

def is_prime_fermat(n, k=10):
    if n < 2:
        return False
    if n < 4:
        return True
    for _ in range(k):
        a = 2 + (hash(str(n) + str(_)) % (n - 3))
        if mod_pow(a, n - 1, n) != 1:
            return False
    return True

def euler_totient(n):
    result = n
    p = 2
    while p * p <= n:
        if n % p == 0:
            while n % p == 0:
                n //= p
            result -= result // p
        p += 1
    if n > 1:
        result -= result // n
    return result

def discrete_log_naive(g, h, p):
    val = 1
    for x in range(p):
        if val == h:
            return x
        val = (val * g) % p
    return None
''',

    "fraction_arithmetic": '''
def gcd_simple(a, b):
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a

def frac_normalize(num, den):
    if den == 0:
        return (num, 0)
    if num == 0:
        return (0, 1)
    sign = -1 if (num < 0) != (den < 0) else 1
    num, den = abs(num), abs(den)
    g = gcd_simple(num, den)
    return (sign * num // g, den // g)

def frac_add(a, b):
    num = a[0] * b[1] + b[0] * a[1]
    den = a[1] * b[1]
    return frac_normalize(num, den)

def frac_sub(a, b):
    num = a[0] * b[1] - b[0] * a[1]
    den = a[1] * b[1]
    return frac_normalize(num, den)

def frac_mul(a, b):
    num = a[0] * b[0]
    den = a[1] * b[1]
    return frac_normalize(num, den)

def frac_div(a, b):
    if b[0] == 0:
        return (0, 0)
    num = a[0] * b[1]
    den = a[1] * b[0]
    return frac_normalize(num, den)

def frac_to_float(f):
    if f[1] == 0:
        return float("inf")
    return f[0] / f[1]
''',


    # ── String processing ────────────────────────────────────────────────

    "tokenizer": '''
def tokenize(text):
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i].isdigit():
            start = i
            while i < len(text) and text[i].isdigit():
                i += 1
            if i < len(text) and text[i] == '.':
                i += 1
                while i < len(text) and text[i].isdigit():
                    i += 1
            tokens.append(("NUMBER", text[start:i]))
        elif text[i].isalpha() or text[i] == '_':
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] == '_'):
                i += 1
            tokens.append(("IDENT", text[start:i]))
        elif text[i] in "+-*/=<>!":
            if i + 1 < len(text) and text[i + 1] == '=':
                tokens.append(("OP", text[i:i + 2]))
                i += 2
            else:
                tokens.append(("OP", text[i]))
                i += 1
        elif text[i] in "(){}[],;:.":
            tokens.append(("PUNCT", text[i]))
            i += 1
        elif text[i] == '"':
            start = i
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == '\\\\':
                    i += 1
                i += 1
            i += 1
            tokens.append(("STRING", text[start:i]))
        else:
            tokens.append(("UNKNOWN", text[i]))
            i += 1
    return tokens
''',

    "pattern_matcher": '''
def match_pattern(text, pattern):
    if not pattern:
        return not text
    if pattern[0] == '*':
        return (match_pattern(text, pattern[1:]) or
                (bool(text) and match_pattern(text[1:], pattern)))
    if pattern[0] == '?':
        return bool(text) and match_pattern(text[1:], pattern[1:])
    return bool(text) and text[0] == pattern[0] and match_pattern(text[1:], pattern[1:])

def find_all_matches(texts, pattern):
    results = []
    for t in texts:
        if match_pattern(t, pattern):
            results.append(t)
    return results

def compile_pattern(pattern):
    parts = []
    i = 0
    while i < len(pattern):
        if pattern[i] == '*':
            parts.append(('STAR',))
        elif pattern[i] == '?':
            parts.append(('ANY',))
        elif pattern[i] == '[':
            end = pattern.index(']', i)
            parts.append(('SET', pattern[i + 1:end]))
            i = end
        else:
            parts.append(('LITERAL', pattern[i]))
        i += 1
    return parts
''',

    "string_formatter": '''
def format_table(rows, headers=None, padding=2):
    if not rows:
        return ""
    all_rows = [headers] + rows if headers else rows
    col_widths = []
    num_cols = len(all_rows[0])
    for col in range(num_cols):
        max_width = 0
        for row in all_rows:
            if col < len(row):
                max_width = max(max_width, len(str(row[col])))
        col_widths.append(max_width)
    lines = []
    for row_idx, row in enumerate(all_rows):
        parts = []
        for col in range(num_cols):
            cell = str(row[col]) if col < len(row) else ""
            parts.append(cell.ljust(col_widths[col] + padding))
        lines.append("".join(parts).rstrip())
        if headers and row_idx == 0:
            sep = ""
            for w in col_widths:
                sep += "-" * (w + padding)
            lines.append(sep.rstrip())
    return "\\n".join(lines)

def wrap_text(text, width=72):
    words = text.split()
    lines = []
    current = []
    length = 0
    for word in words:
        if length + len(word) + len(current) > width and current:
            lines.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += len(word)
    if current:
        lines.append(" ".join(current))
    return "\\n".join(lines)
''',

    "palindrome_checker": '''
def is_palindrome(s):
    cleaned = ""
    for ch in s.lower():
        if ch.isalnum():
            cleaned += ch
    left = 0
    right = len(cleaned) - 1
    while left < right:
        if cleaned[left] != cleaned[right]:
            return False
        left += 1
        right -= 1
    return True

def longest_palindrome(s):
    if not s:
        return ""
    best_start = 0
    best_len = 1
    for center in range(len(s)):
        lo, hi = center, center
        while lo >= 0 and hi < len(s) and s[lo] == s[hi]:
            if hi - lo + 1 > best_len:
                best_start = lo
                best_len = hi - lo + 1
            lo -= 1
            hi += 1
        lo, hi = center, center + 1
        while lo >= 0 and hi < len(s) and s[lo] == s[hi]:
            if hi - lo + 1 > best_len:
                best_start = lo
                best_len = hi - lo + 1
            lo -= 1
            hi += 1
    return s[best_start:best_start + best_len]

def count_palindromic_substrings(s):
    count = 0
    for i in range(len(s)):
        lo, hi = i, i
        while lo >= 0 and hi < len(s) and s[lo] == s[hi]:
            count += 1
            lo -= 1
            hi += 1
        lo, hi = i, i + 1
        while lo >= 0 and hi < len(s) and s[lo] == s[hi]:
            count += 1
            lo -= 1
            hi += 1
    return count
''',

    "text_wrapper": '''
def justify_text(text, width):
    words = text.split()
    lines = []
    current = []
    current_len = 0
    for word in words:
        if current_len + len(word) + len(current) > width and current:
            if len(current) == 1:
                lines.append(current[0].ljust(width))
            else:
                total_spaces = width - current_len
                gaps = len(current) - 1
                base_spaces = total_spaces // gaps
                extra = total_spaces % gaps
                line = ""
                for i, w in enumerate(current):
                    line += w
                    if i < gaps:
                        spaces = base_spaces + (1 if i < extra else 0)
                        line += " " * spaces
                lines.append(line)
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word)
    if current:
        lines.append(" ".join(current).ljust(width))
    return lines

def indent_block(text, level, indent_char="    "):
    prefix = indent_char * level
    return "\\n".join(prefix + line for line in text.split("\\n"))
''',

    "caesar_cipher": '''
def encrypt(text, shift):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord('A') if ch.isupper() else ord('a')
            shifted = (ord(ch) - base + shift) % 26
            result.append(chr(base + shifted))
        else:
            result.append(ch)
    return "".join(result)

def decrypt(text, shift):
    return encrypt(text, -shift)

def crack_caesar(ciphertext):
    english_freq = {
        'e': 12.7, 't': 9.1, 'a': 8.2, 'o': 7.5, 'i': 7.0,
        'n': 6.7, 's': 6.3, 'h': 6.1, 'r': 6.0, 'd': 4.3,
    }
    best_shift = 0
    best_score = -1
    for shift in range(26):
        decrypted = decrypt(ciphertext, shift)
        score = 0.0
        total = 0
        for ch in decrypted.lower():
            if ch.isalpha():
                total += 1
                score += english_freq.get(ch, 0)
        if total > 0:
            score /= total
        if score > best_score:
            best_score = score
            best_shift = shift
    return best_shift, decrypt(ciphertext, best_shift)
''',

    "run_length_codec": '''
def rle_encode(data):
    if not data:
        return []
    encoded = []
    count = 1
    prev = data[0]
    for i in range(1, len(data)):
        if data[i] == prev:
            count += 1
        else:
            encoded.append((prev, count))
            prev = data[i]
            count = 1
    encoded.append((prev, count))
    return encoded

def rle_decode(encoded):
    result = []
    for char, count in encoded:
        result.extend([char] * count)
    return result

def rle_to_string(encoded):
    parts = []
    for char, count in encoded:
        if count == 1:
            parts.append(str(char))
        else:
            parts.append(str(count) + str(char))
    return "".join(parts)

def rle_from_string(s):
    encoded = []
    i = 0
    while i < len(s):
        count = 0
        while i < len(s) and s[i].isdigit():
            count = count * 10 + int(s[i])
            i += 1
        if count == 0:
            count = 1
        if i < len(s):
            encoded.append((s[i], count))
            i += 1
    return encoded
''',

    "levenshtein_distance": '''
def levenshtein(s1, s2):
    m = len(s1)
    n = len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(
                    dp[i - 1][j],
                    dp[i][j - 1],
                    dp[i - 1][j - 1],
                )
    return dp[m][n]

def similarity_ratio(s1, s2):
    dist = levenshtein(s1, s2)
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - dist / max_len

def closest_match(query, candidates):
    best = None
    best_dist = float("inf")
    for c in candidates:
        d = levenshtein(query, c)
        if d < best_dist:
            best_dist = d
            best = c
    return best, best_dist
''',

    "anagram_finder": '''
def sort_key(word):
    return "".join(sorted(word.lower()))

def find_anagrams(words):
    groups = {}
    for word in words:
        key = sort_key(word)
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    result = {}
    for key, group in groups.items():
        if len(group) > 1:
            result[key] = sorted(group)
    return result

def are_anagrams(w1, w2):
    return sort_key(w1) == sort_key(w2)

def largest_anagram_group(words):
    groups = find_anagrams(words)
    if not groups:
        return []
    best_key = max(groups, key=lambda k: len(groups[k]))
    return groups[best_key]

def count_anagram_pairs(words):
    groups = {}
    for word in words:
        key = sort_key(word)
        groups[key] = groups.get(key, 0) + 1
    count = 0
    for n in groups.values():
        count += n * (n - 1) // 2
    return count
''',

    "bracket_matcher": '''
def is_balanced(s):
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in '([{':
            stack.append(ch)
        elif ch in ')]}':
            if not stack:
                return False
            if stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return len(stack) == 0

def find_mismatch(s):
    stack = []
    for i, ch in enumerate(s):
        if ch in '([{':
            stack.append((ch, i))
        elif ch in ')]}':
            pairs = {')': '(', ']': '[', '}': '{'}
            if not stack:
                return i
            if stack[-1][0] != pairs[ch]:
                return i
            stack.pop()
    if stack:
        return stack[-1][1]
    return -1

def max_nesting_depth(s):
    depth = 0
    max_depth = 0
    for ch in s:
        if ch in '([{':
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch in ')]}':
            depth -= 1
    return max_depth

def extract_balanced(s):
    results = []
    stack = []
    for i, ch in enumerate(s):
        if ch == '(':
            stack.append(i)
        elif ch == ')' and stack:
            start = stack.pop()
            if not stack:
                results.append(s[start:i + 1])
    return results
''',


    # ── Validators ───────────────────────────────────────────────────────

    "email_validator": '''
def validate_email(email):
    if not email or not isinstance(email, str):
        return False
    at_idx = email.find("@")
    if at_idx < 1:
        return False
    if email.count("@") != 1:
        return False
    local = email[:at_idx]
    domain = email[at_idx + 1:]
    if not local or not domain:
        return False
    if len(local) > 64 or len(domain) > 253:
        return False
    if domain.startswith(".") or domain.endswith("."):
        return False
    if ".." in domain:
        return False
    parts = domain.split(".")
    if len(parts) < 2:
        return False
    for part in parts:
        if not part:
            return False
        if not all(c.isalnum() or c == '-' for c in part):
            return False
    return True

def extract_domain(email):
    if not validate_email(email):
        return None
    return email.split("@")[1]

def normalize_email(email):
    if not validate_email(email):
        return None
    local, domain = email.split("@")
    return local.lower() + "@" + domain.lower()
''',

    "json_validator": '''
def validate_json_string(s):
    s = s.strip()
    if not s:
        return False, "empty input"
    pos = [0]

    def skip_ws():
        while pos[0] < len(s) and s[pos[0]] in ' \\t\\n\\r':
            pos[0] += 1

    def parse_value():
        skip_ws()
        if pos[0] >= len(s):
            return False
        ch = s[pos[0]]
        if ch == '"':
            return parse_string()
        if ch == '{':
            return parse_object()
        if ch == '[':
            return parse_array()
        if ch in '-0123456789':
            return parse_number()
        if s[pos[0]:pos[0]+4] == 'true':
            pos[0] += 4
            return True
        if s[pos[0]:pos[0]+5] == 'false':
            pos[0] += 5
            return True
        if s[pos[0]:pos[0]+4] == 'null':
            pos[0] += 4
            return True
        return False

    def parse_string():
        if s[pos[0]] != '"':
            return False
        pos[0] += 1
        while pos[0] < len(s):
            if s[pos[0]] == '\\\\':
                pos[0] += 2
            elif s[pos[0]] == '"':
                pos[0] += 1
                return True
            else:
                pos[0] += 1
        return False

    def parse_object():
        pos[0] += 1
        skip_ws()
        if pos[0] < len(s) and s[pos[0]] == '}':
            pos[0] += 1
            return True
        while True:
            skip_ws()
            if not parse_string():
                return False
            skip_ws()
            if pos[0] >= len(s) or s[pos[0]] != ':':
                return False
            pos[0] += 1
            if not parse_value():
                return False
            skip_ws()
            if pos[0] < len(s) and s[pos[0]] == '}':
                pos[0] += 1
                return True
            if pos[0] >= len(s) or s[pos[0]] != ',':
                return False
            pos[0] += 1
        return False

    def parse_array():
        pos[0] += 1
        skip_ws()
        if pos[0] < len(s) and s[pos[0]] == ']':
            pos[0] += 1
            return True
        while True:
            if not parse_value():
                return False
            skip_ws()
            if pos[0] < len(s) and s[pos[0]] == ']':
                pos[0] += 1
                return True
            if pos[0] >= len(s) or s[pos[0]] != ',':
                return False
            pos[0] += 1
        return False

    def parse_number():
        start = pos[0]
        if s[pos[0]] == '-':
            pos[0] += 1
        if pos[0] >= len(s) or not s[pos[0]].isdigit():
            return False
        while pos[0] < len(s) and s[pos[0]].isdigit():
            pos[0] += 1
        if pos[0] < len(s) and s[pos[0]] == '.':
            pos[0] += 1
            if pos[0] >= len(s) or not s[pos[0]].isdigit():
                return False
            while pos[0] < len(s) and s[pos[0]].isdigit():
                pos[0] += 1
        return True

    ok = parse_value()
    skip_ws()
    return ok and pos[0] == len(s), ""
''',

    "csv_parser": '''
def parse_csv_line(line, delimiter=",", quote_char='"'):
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
                    i += 1
                else:
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

def parse_csv(text, delimiter=",", has_header=True):
    lines = text.strip().split("\\n")
    if not lines:
        return []
    rows = [parse_csv_line(l, delimiter) for l in lines]
    if has_header and len(rows) > 1:
        headers = rows[0]
        return [dict(zip(headers, row)) for row in rows[1:]]
    return rows
''',

    "date_validator": '''
def is_leap_year(year):
    if year % 400 == 0:
        return True
    if year % 100 == 0:
        return False
    if year % 4 == 0:
        return True
    return False

def days_in_month(year, month):
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    if 1 <= month <= 12:
        return days[month]
    return 0

def validate_date(date_str):
    parts = date_str.split("-")
    if len(parts) != 3:
        return False
    try:
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
    except ValueError:
        return False
    if year < 1 or year > 9999:
        return False
    if month < 1 or month > 12:
        return False
    max_day = days_in_month(year, month)
    if day < 1 or day > max_day:
        return False
    return True

def day_of_year(year, month, day):
    total = 0
    for m in range(1, month):
        total += days_in_month(year, m)
    total += day
    return total
''',

    "schema_validator": '''
def validate_schema(data, schema):
    if schema.get("type") == "object":
        if not isinstance(data, dict):
            return False, "expected object"
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                return False, "missing required key: " + key
        props = schema.get("properties", {})
        for key, sub_schema in props.items():
            if key in data:
                ok, err = validate_schema(data[key], sub_schema)
                if not ok:
                    return False, key + ": " + err
        return True, ""
    if schema.get("type") == "array":
        if not isinstance(data, list):
            return False, "expected array"
        item_schema = schema.get("items", {})
        for i, item in enumerate(data):
            ok, err = validate_schema(item, item_schema)
            if not ok:
                return False, "[" + str(i) + "]: " + err
        return True, ""
    if schema.get("type") == "string":
        if not isinstance(data, str):
            return False, "expected string"
        min_len = schema.get("minLength", 0)
        if len(data) < min_len:
            return False, "too short"
        return True, ""
    if schema.get("type") == "integer":
        if not isinstance(data, int):
            return False, "expected integer"
        min_val = schema.get("minimum")
        if min_val is not None and data < min_val:
            return False, "below minimum"
        return True, ""
    return True, ""
''',

    "ip_address_validator": '''
def validate_ipv4(addr):
    parts = addr.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part:
            return False
        if not part.isdigit():
            return False
        if len(part) > 1 and part[0] == '0':
            return False
        val = int(part)
        if val < 0 or val > 255:
            return False
    return True

def validate_ipv6(addr):
    parts = addr.split(":")
    if len(parts) < 3 or len(parts) > 8:
        return False
    for part in parts:
        if not part:
            continue
        if len(part) > 4:
            return False
        for ch in part:
            if ch not in "0123456789abcdefABCDEF":
                return False
    return True

def ip_to_int(addr):
    parts = addr.split(".")
    result = 0
    for part in parts:
        result = result * 256 + int(part)
    return result

def int_to_ip(num):
    parts = []
    for _ in range(4):
        parts.append(str(num % 256))
        num //= 256
    return ".".join(reversed(parts))

def is_private_ip(addr):
    val = ip_to_int(addr)
    if ip_to_int("10.0.0.0") <= val <= ip_to_int("10.255.255.255"):
        return True
    if ip_to_int("172.16.0.0") <= val <= ip_to_int("172.31.255.255"):
        return True
    if ip_to_int("192.168.0.0") <= val <= ip_to_int("192.168.255.255"):
        return True
    return False
''',

    "phone_validator": '''
def normalize_phone(phone):
    digits = ""
    for ch in phone:
        if ch.isdigit():
            digits += ch
    return digits

def validate_us_phone(phone):
    digits = normalize_phone(phone)
    if len(digits) == 10:
        return True
    if len(digits) == 11 and digits[0] == '1':
        return True
    return False

def format_phone(phone, style="us"):
    digits = normalize_phone(phone)
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    if len(digits) != 10:
        return None
    if style == "us":
        return "({}) {}-{}".format(digits[:3], digits[3:6], digits[6:])
    if style == "dots":
        return "{}.{}.{}".format(digits[:3], digits[3:6], digits[6:])
    if style == "dashes":
        return "{}-{}-{}".format(digits[:3], digits[3:6], digits[6:])
    return digits

def extract_area_code(phone):
    digits = normalize_phone(phone)
    if len(digits) == 11 and digits[0] == '1':
        return digits[1:4]
    if len(digits) == 10:
        return digits[:3]
    return None
''',

    "credit_card_validator": '''
def luhn_check(number):
    digits = [int(d) for d in str(number) if d.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    for i in range(len(digits) - 1, -1, -1):
        d = digits[i]
        if (len(digits) - 1 - i) % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0

def identify_card_type(number):
    s = str(number).replace(" ", "").replace("-", "")
    if s.startswith("4") and len(s) in (13, 16):
        return "Visa"
    if s[:2] in ("51", "52", "53", "54", "55") and len(s) == 16:
        return "MasterCard"
    if s[:2] in ("34", "37") and len(s) == 15:
        return "AmEx"
    if s[:4] == "6011" and len(s) == 16:
        return "Discover"
    return "Unknown"

def validate_card(number):
    s = str(number).replace(" ", "").replace("-", "")
    if not s.isdigit():
        return False, "non-digit characters"
    if not luhn_check(s):
        return False, "failed Luhn check"
    card_type = identify_card_type(s)
    return True, card_type
''',

    "password_strength": '''
def check_strength(password):
    score = 0
    feedback = []
    if len(password) >= 8:
        score += 1
    else:
        feedback.append("at least 8 characters")
    if len(password) >= 12:
        score += 1
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    if has_upper:
        score += 1
    else:
        feedback.append("add uppercase")
    if has_lower:
        score += 1
    else:
        feedback.append("add lowercase")
    if has_digit:
        score += 1
    else:
        feedback.append("add digit")
    if has_special:
        score += 1
    else:
        feedback.append("add special character")
    if score >= 5:
        return "strong", feedback
    if score >= 3:
        return "medium", feedback
    return "weak", feedback

def has_common_pattern(password):
    common = ["password", "123456", "qwerty", "admin", "letmein"]
    lower = password.lower()
    for pat in common:
        if pat in lower:
            return True
    return False
''',

    "url_validator": '''
def validate_url(url):
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("http://") and not url.startswith("https://"):
        return False
    rest = url.split("://", 1)[1]
    if not rest:
        return False
    path_start = rest.find("/")
    if path_start == -1:
        host = rest
    else:
        host = rest[:path_start]
    if not host:
        return False
    if ":" in host:
        host_part, port_str = host.rsplit(":", 1)
        if not port_str.isdigit():
            return False
        port = int(port_str)
        if port < 1 or port > 65535:
            return False
        host = host_part
    parts = host.split(".")
    if len(parts) < 2:
        return False
    for part in parts:
        if not part:
            return False
        if not all(c.isalnum() or c == '-' for c in part):
            return False
    return True

def extract_parts(url):
    scheme = url.split("://")[0]
    rest = url.split("://", 1)[1]
    host = rest.split("/")[0]
    path = "/" + "/".join(rest.split("/")[1:]) if "/" in rest else "/"
    query = ""
    if "?" in path:
        path, query = path.split("?", 1)
    return {"scheme": scheme, "host": host, "path": path, "query": query}
''',


    # ── State machines ───────────────────────────────────────────────────

    "vending_machine": '''
class VendingMachine:
    def __init__(self):
        self.state = "idle"
        self.balance = 0
        self.inventory = {
            "cola": {"price": 150, "stock": 5},
            "chips": {"price": 100, "stock": 3},
            "water": {"price": 75, "stock": 10},
        }

    def insert_coin(self, amount):
        if self.state == "idle":
            self.state = "accepting"
        if self.state == "accepting":
            self.balance += amount
            return self.balance
        return None

    def select(self, item):
        if self.state != "accepting":
            return "insert coins first"
        if item not in self.inventory:
            return "invalid item"
        info = self.inventory[item]
        if info["stock"] <= 0:
            return "out of stock"
        if self.balance < info["price"]:
            return "insufficient funds"
        info["stock"] -= 1
        change = self.balance - info["price"]
        self.balance = 0
        self.state = "idle"
        return {"item": item, "change": change}

    def cancel(self):
        refund = self.balance
        self.balance = 0
        self.state = "idle"
        return refund
''',

    "traffic_light": '''
class TrafficLight:
    STATES = ["red", "green", "yellow"]
    DURATIONS = {"red": 30, "green": 25, "yellow": 5}

    def __init__(self):
        self.state = "red"
        self.timer = 0
        self.cycle_count = 0

    def tick(self):
        self.timer += 1
        if self.timer >= self.DURATIONS[self.state]:
            self.advance()
            self.timer = 0
        return self.state

    def advance(self):
        idx = self.STATES.index(self.state)
        next_idx = (idx + 1) % len(self.STATES)
        self.state = self.STATES[next_idx]
        if self.state == "red":
            self.cycle_count += 1

    def is_safe_to_cross(self):
        return self.state == "green"

    def time_until_change(self):
        return self.DURATIONS[self.state] - self.timer

    def simulate(self, ticks):
        history = []
        for _ in range(ticks):
            history.append(self.tick())
        return history
''',

    "protocol_handler": '''
class ProtocolHandler:
    def __init__(self):
        self.state = "disconnected"
        self.buffer = []
        self.seq_num = 0
        self.ack_num = 0

    def connect(self):
        if self.state != "disconnected":
            return False
        self.state = "syn_sent"
        self.seq_num = 1
        return True

    def receive_syn_ack(self):
        if self.state != "syn_sent":
            return False
        self.state = "established"
        self.ack_num = 1
        return True

    def send(self, data):
        if self.state != "established":
            return False
        self.buffer.append({
            "seq": self.seq_num,
            "data": data,
        })
        self.seq_num += len(data)
        return True

    def receive_ack(self, ack):
        if self.state != "established":
            return False
        self.ack_num = max(self.ack_num, ack)
        self.buffer = [p for p in self.buffer if p["seq"] >= ack]
        return True

    def disconnect(self):
        if self.state == "disconnected":
            return False
        self.state = "fin_sent"
        return True

    def receive_fin_ack(self):
        if self.state != "fin_sent":
            return False
        self.state = "disconnected"
        self.buffer = []
        return True
''',

    "elevator_controller": '''
class ElevatorController:
    def __init__(self, floors=10):
        self.current_floor = 1
        self.direction = "idle"
        self.requests = set()
        self.max_floor = floors
        self.door_open = False

    def request(self, floor):
        if 1 <= floor <= self.max_floor:
            self.requests.add(floor)
            if self.direction == "idle":
                if floor > self.current_floor:
                    self.direction = "up"
                elif floor < self.current_floor:
                    self.direction = "down"

    def step(self):
        if not self.requests:
            self.direction = "idle"
            return self.current_floor
        if self.current_floor in self.requests:
            self.requests.discard(self.current_floor)
            self.door_open = True
            return self.current_floor
        self.door_open = False
        if self.direction == "up":
            above = [f for f in self.requests if f > self.current_floor]
            if above:
                self.current_floor += 1
            else:
                self.direction = "down"
        elif self.direction == "down":
            below = [f for f in self.requests if f < self.current_floor]
            if below:
                self.current_floor -= 1
            else:
                self.direction = "up"
        return self.current_floor

    def status(self):
        return {
            "floor": self.current_floor,
            "direction": self.direction,
            "pending": sorted(self.requests),
            "door": "open" if self.door_open else "closed",
        }
''',

    "turnstile": '''
class Turnstile:
    def __init__(self):
        self.state = "locked"
        self.coins_collected = 0
        self.entries = 0
        self.log = []

    def coin(self):
        self.coins_collected += 1
        if self.state == "locked":
            self.state = "unlocked"
            self.log.append("coin: locked -> unlocked")
        else:
            self.log.append("coin: already unlocked")
        return self.state

    def push(self):
        if self.state == "unlocked":
            self.state = "locked"
            self.entries += 1
            self.log.append("push: unlocked -> locked (entry)")
            return True
        self.log.append("push: locked (blocked)")
        return False

    def reset(self):
        coins = self.coins_collected
        entries = self.entries
        self.state = "locked"
        self.coins_collected = 0
        self.entries = 0
        self.log = []
        return {"coins": coins, "entries": entries}

    def get_stats(self):
        return {
            "state": self.state,
            "coins": self.coins_collected,
            "entries": self.entries,
            "log_size": len(self.log),
        }
''',

    "door_lock": '''
class DoorLock:
    def __init__(self, code):
        self.correct_code = code
        self.state = "locked"
        self.attempts = 0
        self.max_attempts = 3
        self.lockout_remaining = 0

    def enter_code(self, code):
        if self.state == "lockout":
            return "device locked out"
        if self.state == "open":
            return "already open"
        self.attempts += 1
        if code == self.correct_code:
            self.state = "open"
            self.attempts = 0
            return "access granted"
        if self.attempts >= self.max_attempts:
            self.state = "lockout"
            self.lockout_remaining = 30
            return "too many attempts"
        return "wrong code"

    def lock(self):
        if self.state == "open":
            self.state = "locked"
            return True
        return False

    def tick(self):
        if self.state == "lockout":
            self.lockout_remaining -= 1
            if self.lockout_remaining <= 0:
                self.state = "locked"
                self.attempts = 0
                return "lockout expired"
        return self.state

    def change_code(self, old_code, new_code):
        if old_code != self.correct_code:
            return False
        self.correct_code = new_code
        return True
''',

    "washing_machine": '''
class WashingMachine:
    CYCLES = {
        "normal": ["fill", "wash", "rinse", "spin", "done"],
        "delicate": ["fill", "wash", "rinse", "done"],
        "quick": ["fill", "wash", "spin", "done"],
    }

    def __init__(self):
        self.state = "idle"
        self.cycle_type = None
        self.step_index = 0
        self.steps = []
        self.water_level = 0
        self.timer = 0

    def start(self, cycle="normal"):
        if self.state != "idle":
            return False
        if cycle not in self.CYCLES:
            return False
        self.cycle_type = cycle
        self.steps = self.CYCLES[cycle]
        self.step_index = 0
        self.state = self.steps[0]
        return True

    def advance(self):
        if self.state == "idle" or self.state == "done":
            return self.state
        self.step_index += 1
        if self.step_index >= len(self.steps):
            self.state = "done"
        else:
            self.state = self.steps[self.step_index]
        return self.state

    def stop(self):
        self.state = "idle"
        self.step_index = 0
        self.steps = []
        self.water_level = 0
        return True

    def status(self):
        return {
            "state": self.state,
            "cycle": self.cycle_type,
            "progress": self.step_index,
            "total_steps": len(self.steps),
        }
''',

    "order_state_machine": '''
class Order:
    TRANSITIONS = {
        "created": ["confirmed", "cancelled"],
        "confirmed": ["processing", "cancelled"],
        "processing": ["shipped", "cancelled"],
        "shipped": ["delivered", "returned"],
        "delivered": ["returned", "completed"],
        "completed": [],
        "cancelled": [],
        "returned": ["refunded"],
        "refunded": [],
    }

    def __init__(self, order_id, items):
        self.order_id = order_id
        self.items = items
        self.state = "created"
        self.history = [("created", None)]

    def transition(self, new_state, reason=None):
        allowed = self.TRANSITIONS.get(self.state, [])
        if new_state not in allowed:
            return False
        self.state = new_state
        self.history.append((new_state, reason))
        return True

    def can_cancel(self):
        return "cancelled" in self.TRANSITIONS.get(self.state, [])

    def total_value(self):
        return sum(item.get("price", 0) * item.get("qty", 1) for item in self.items)

    def get_timeline(self):
        return [(state, reason) for state, reason in self.history]

    def is_terminal(self):
        return len(self.TRANSITIONS.get(self.state, [])) == 0
''',

    "game_state": '''
class GameState:
    def __init__(self, width=10, height=10):
        self.width = width
        self.height = height
        self.player = {"x": 0, "y": 0, "hp": 100, "score": 0}
        self.enemies = []
        self.items = []
        self.state = "playing"

    def add_enemy(self, x, y, hp):
        self.enemies.append({"x": x, "y": y, "hp": hp})

    def add_item(self, x, y, item_type):
        self.items.append({"x": x, "y": y, "type": item_type})

    def move_player(self, dx, dy):
        if self.state != "playing":
            return False
        nx = self.player["x"] + dx
        ny = self.player["y"] + dy
        if 0 <= nx < self.width and 0 <= ny < self.height:
            self.player["x"] = nx
            self.player["y"] = ny
            self._check_pickups()
            self._check_combat()
            return True
        return False

    def _check_pickups(self):
        px, py = self.player["x"], self.player["y"]
        remaining = []
        for item in self.items:
            if item["x"] == px and item["y"] == py:
                if item["type"] == "health":
                    self.player["hp"] = min(100, self.player["hp"] + 25)
                elif item["type"] == "coin":
                    self.player["score"] += 10
            else:
                remaining.append(item)
        self.items = remaining

    def _check_combat(self):
        px, py = self.player["x"], self.player["y"]
        for enemy in self.enemies:
            if enemy["x"] == px and enemy["y"] == py:
                self.player["hp"] -= 20
                enemy["hp"] -= 30
        self.enemies = [e for e in self.enemies if e["hp"] > 0]
        if self.player["hp"] <= 0:
            self.state = "game_over"

    def get_state(self):
        return {
            "player": dict(self.player),
            "enemies": len(self.enemies),
            "items": len(self.items),
            "status": self.state,
        }
''',

    "parser_state_machine": '''
class MarkdownParser:
    def __init__(self):
        self.state = "normal"
        self.tokens = []
        self.buffer = []

    def feed(self, text):
        for ch in text:
            self._process_char(ch)
        self._flush()
        return self.tokens

    def _process_char(self, ch):
        if self.state == "normal":
            if ch == '*':
                self._flush()
                self.state = "maybe_bold"
            elif ch == '`':
                self._flush()
                self.state = "code"
            elif ch == '#':
                self._flush()
                self.state = "heading"
                self.buffer.append(ch)
            elif ch == '\\n':
                self._flush()
                self.tokens.append(("newline", ""))
            else:
                self.buffer.append(ch)
        elif self.state == "maybe_bold":
            if ch == '*':
                self.state = "bold"
            else:
                self.state = "italic"
                self.buffer.append(ch)
        elif self.state == "bold":
            if ch == '*':
                self.state = "maybe_end_bold"
            else:
                self.buffer.append(ch)
        elif self.state == "maybe_end_bold":
            if ch == '*':
                self.tokens.append(("bold", "".join(self.buffer)))
                self.buffer = []
                self.state = "normal"
            else:
                self.buffer.append('*')
                self.buffer.append(ch)
                self.state = "bold"
        elif self.state == "italic":
            if ch == '*':
                self.tokens.append(("italic", "".join(self.buffer)))
                self.buffer = []
                self.state = "normal"
            else:
                self.buffer.append(ch)
        elif self.state == "code":
            if ch == '`':
                self.tokens.append(("code", "".join(self.buffer)))
                self.buffer = []
                self.state = "normal"
            else:
                self.buffer.append(ch)
        elif self.state == "heading":
            if ch == '\\n':
                self.tokens.append(("heading", "".join(self.buffer).strip()))
                self.buffer = []
                self.state = "normal"
            else:
                self.buffer.append(ch)

    def _flush(self):
        if self.buffer:
            if self.state == "normal":
                self.tokens.append(("text", "".join(self.buffer)))
            self.buffer = []
''',


    # ── Calculators ──────────────────────────────────────────────────────

    "expression_evaluator": '''
def tokenize_expr(expr):
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
        elif expr[i].isdigit() or expr[i] == '.':
            start = i
            while i < len(expr) and (expr[i].isdigit() or expr[i] == '.'):
                i += 1
            tokens.append(("NUM", float(expr[start:i])))
        elif expr[i] in "+-*/":
            tokens.append(("OP", expr[i]))
            i += 1
        elif expr[i] == '(':
            tokens.append(("LPAREN",))
            i += 1
        elif expr[i] == ')':
            tokens.append(("RPAREN",))
            i += 1
        else:
            i += 1
    return tokens

def shunting_yard(tokens):
    output = []
    ops = []
    prec = {'+': 1, '-': 1, '*': 2, '/': 2}
    for tok in tokens:
        if tok[0] == "NUM":
            output.append(tok)
        elif tok[0] == "OP":
            while (ops and ops[-1][0] == "OP" and
                   prec.get(ops[-1][1], 0) >= prec.get(tok[1], 0)):
                output.append(ops.pop())
            ops.append(tok)
        elif tok[0] == "LPAREN":
            ops.append(tok)
        elif tok[0] == "RPAREN":
            while ops and ops[-1][0] != "LPAREN":
                output.append(ops.pop())
            if ops:
                ops.pop()
    while ops:
        output.append(ops.pop())
    return output

def eval_rpn(rpn):
    stack = []
    for tok in rpn:
        if tok[0] == "NUM":
            stack.append(tok[1])
        elif tok[0] == "OP":
            b = stack.pop()
            a = stack.pop()
            if tok[1] == '+':
                stack.append(a + b)
            elif tok[1] == '-':
                stack.append(a - b)
            elif tok[1] == '*':
                stack.append(a * b)
            elif tok[1] == '/':
                stack.append(a / b if b != 0 else 0)
    return stack[0] if stack else 0

def evaluate_expression(expr):
    tokens = tokenize_expr(expr)
    rpn = shunting_yard(tokens)
    return eval_rpn(rpn)
''',

    "unit_converter": '''
LENGTH_FACTORS = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "mi": 1609.344,
    "ft": 0.3048,
    "in": 0.0254,
    "yd": 0.9144,
}

WEIGHT_FACTORS = {
    "kg": 1.0,
    "g": 0.001,
    "mg": 0.000001,
    "lb": 0.453592,
    "oz": 0.0283495,
    "ton": 1000.0,
}

def convert_length(value, from_unit, to_unit):
    if from_unit not in LENGTH_FACTORS or to_unit not in LENGTH_FACTORS:
        return None
    meters = value * LENGTH_FACTORS[from_unit]
    return meters / LENGTH_FACTORS[to_unit]

def convert_weight(value, from_unit, to_unit):
    if from_unit not in WEIGHT_FACTORS or to_unit not in WEIGHT_FACTORS:
        return None
    kg = value * WEIGHT_FACTORS[from_unit]
    return kg / WEIGHT_FACTORS[to_unit]

def convert_temperature(value, from_unit, to_unit):
    if from_unit == to_unit:
        return value
    if from_unit == "C" and to_unit == "F":
        return value * 9.0 / 5.0 + 32
    if from_unit == "F" and to_unit == "C":
        return (value - 32) * 5.0 / 9.0
    if from_unit == "C" and to_unit == "K":
        return value + 273.15
    if from_unit == "K" and to_unit == "C":
        return value - 273.15
    if from_unit == "F" and to_unit == "K":
        return (value - 32) * 5.0 / 9.0 + 273.15
    if from_unit == "K" and to_unit == "F":
        return (value - 273.15) * 9.0 / 5.0 + 32
    return None
''',

    "compound_interest": '''
def compound_interest(principal, rate, n, t):
    amount = principal * (1 + rate / n) ** (n * t)
    interest = amount - principal
    return round(amount, 2), round(interest, 2)

def future_value(payment, rate, n, t):
    if rate == 0:
        return round(payment * n * t, 2)
    r = rate / n
    periods = n * t
    fv = payment * ((1 + r) ** periods - 1) / r
    return round(fv, 2)

def present_value(future, rate, n, t):
    r = rate / n
    periods = n * t
    pv = future / (1 + r) ** periods
    return round(pv, 2)

def amortization_schedule(principal, rate, n, t):
    r = rate / n
    periods = n * t
    if r == 0:
        payment = principal / periods
    else:
        payment = principal * r * (1 + r) ** periods / ((1 + r) ** periods - 1)
    schedule = []
    balance = principal
    for i in range(1, int(periods) + 1):
        interest_payment = balance * r
        principal_payment = payment - interest_payment
        balance -= principal_payment
        schedule.append({
            "period": i,
            "payment": round(payment, 2),
            "interest": round(interest_payment, 2),
            "principal": round(principal_payment, 2),
            "balance": round(max(balance, 0), 2),
        })
    return schedule
''',

    "mortgage_calculator": '''
def monthly_payment(principal, annual_rate, years):
    if annual_rate == 0:
        return round(principal / (years * 12), 2)
    monthly_rate = annual_rate / 12
    n_payments = years * 12
    numerator = monthly_rate * (1 + monthly_rate) ** n_payments
    denominator = (1 + monthly_rate) ** n_payments - 1
    return round(principal * numerator / denominator, 2)

def total_cost(principal, annual_rate, years):
    payment = monthly_payment(principal, annual_rate, years)
    return round(payment * years * 12, 2)

def total_interest(principal, annual_rate, years):
    cost = total_cost(principal, annual_rate, years)
    return round(cost - principal, 2)

def affordability(income, annual_rate, years, ratio=0.28):
    max_payment = income * ratio / 12
    if annual_rate == 0:
        return round(max_payment * years * 12, 2)
    monthly_rate = annual_rate / 12
    n = years * 12
    denom = monthly_rate * (1 + monthly_rate) ** n
    numer = (1 + monthly_rate) ** n - 1
    return round(max_payment * numer / denom, 2)

def compare_rates(principal, years, rates):
    results = []
    for r in rates:
        pmt = monthly_payment(principal, r, years)
        total = total_cost(principal, r, years)
        interest = total - principal
        results.append({"rate": r, "payment": pmt, "total": total, "interest": round(interest, 2)})
    return results
''',

    "bmi_calculator": '''
def calculate_bmi(weight_kg, height_m):
    if height_m <= 0:
        return None
    return round(weight_kg / (height_m ** 2), 1)

def bmi_category(bmi):
    if bmi is None:
        return "invalid"
    if bmi < 18.5:
        return "underweight"
    if bmi < 25.0:
        return "normal"
    if bmi < 30.0:
        return "overweight"
    return "obese"

def ideal_weight_range(height_m):
    low = 18.5 * (height_m ** 2)
    high = 24.9 * (height_m ** 2)
    return round(low, 1), round(high, 1)

def bmi_from_imperial(weight_lbs, height_inches):
    weight_kg = weight_lbs * 0.453592
    height_m = height_inches * 0.0254
    return calculate_bmi(weight_kg, height_m)

def batch_bmi(records):
    results = []
    for rec in records:
        bmi = calculate_bmi(rec["weight"], rec["height"])
        cat = bmi_category(bmi)
        results.append({
            "name": rec.get("name", "unknown"),
            "bmi": bmi,
            "category": cat,
        })
    return results
''',

    "temperature_converter": '''
def celsius_to_fahrenheit(c):
    return round(c * 9.0 / 5.0 + 32, 2)

def fahrenheit_to_celsius(f):
    return round((f - 32) * 5.0 / 9.0, 2)

def celsius_to_kelvin(c):
    return round(c + 273.15, 2)

def kelvin_to_celsius(k):
    return round(k - 273.15, 2)

def convert_temp(value, from_scale, to_scale):
    if from_scale == to_scale:
        return round(value, 2)
    if from_scale == "C":
        if to_scale == "F":
            return celsius_to_fahrenheit(value)
        return celsius_to_kelvin(value)
    if from_scale == "F":
        c = fahrenheit_to_celsius(value)
        if to_scale == "C":
            return c
        return celsius_to_kelvin(c)
    if from_scale == "K":
        c = kelvin_to_celsius(value)
        if to_scale == "C":
            return c
        return celsius_to_fahrenheit(c)
    return None

def temperature_table(start, end, step, from_scale, to_scale):
    table = []
    val = start
    while val <= end:
        converted = convert_temp(val, from_scale, to_scale)
        table.append((val, converted))
        val += step
    return table
''',

    "roman_numeral_converter": '''
ROMAN_VALUES = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]

def int_to_roman(num):
    if num <= 0 or num > 3999:
        return None
    result = []
    for value, numeral in ROMAN_VALUES:
        while num >= value:
            result.append(numeral)
            num -= value
    return "".join(result)

def roman_to_int(s):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(s.upper()):
        val = values.get(ch, 0)
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total

def validate_roman(s):
    valid_chars = set("IVXLCDM")
    for ch in s.upper():
        if ch not in valid_chars:
            return False
    converted = roman_to_int(s)
    return int_to_roman(converted) == s.upper()

def roman_range(start, end):
    return [int_to_roman(i) for i in range(start, end + 1) if int_to_roman(i)]
''',

    "base_converter": '''
DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def to_base(number, base):
    if number == 0:
        return "0"
    if base < 2 or base > 36:
        return None
    negative = number < 0
    number = abs(number)
    result = []
    while number > 0:
        result.append(DIGITS[number % base])
        number //= base
    if negative:
        result.append("-")
    return "".join(reversed(result))

def from_base(s, base):
    if base < 2 or base > 36:
        return None
    negative = s.startswith("-")
    if negative:
        s = s[1:]
    result = 0
    for ch in s.upper():
        idx = DIGITS.index(ch)
        if idx >= base:
            return None
        result = result * base + idx
    return -result if negative else result

def convert_base(s, from_base_val, to_base_val):
    decimal = from_base(s, from_base_val)
    if decimal is None:
        return None
    return to_base(decimal, to_base_val)

def format_binary(n, width=8):
    binary = to_base(abs(n), 2)
    if binary is None:
        return None
    return binary.zfill(width)
''',

    "date_difference": '''
def is_leap(year):
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0

def days_in_year(year):
    return 366 if is_leap(year) else 365

def month_days(year, month):
    table = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap(year):
        return 29
    return table[month]

def date_to_days(year, month, day):
    total = 0
    for y in range(1, year):
        total += days_in_year(y)
    for m in range(1, month):
        total += month_days(year, m)
    total += day
    return total

def days_between(y1, m1, d1, y2, m2, d2):
    return abs(date_to_days(y2, m2, d2) - date_to_days(y1, m1, d1))

def add_days(year, month, day, n):
    total = date_to_days(year, month, day) + n
    y = 1
    while total > days_in_year(y):
        total -= days_in_year(y)
        y += 1
    m = 1
    while total > month_days(y, m):
        total -= month_days(y, m)
        m += 1
    return (y, m, total)

def day_of_week(year, month, day):
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    d = date_to_days(year, month, day)
    return names[(d - 1) % 7]
''',

    "tax_calculator": '''
BRACKETS = [
    (10000, 0.10),
    (30000, 0.15),
    (50000, 0.25),
    (100000, 0.30),
    (float("inf"), 0.35),
]

def calculate_tax(income):
    if income <= 0:
        return 0.0
    tax = 0.0
    prev_limit = 0
    for limit, rate in BRACKETS:
        taxable = min(income, limit) - prev_limit
        if taxable > 0:
            tax += taxable * rate
        prev_limit = limit
        if income <= limit:
            break
    return round(tax, 2)

def effective_rate(income):
    if income <= 0:
        return 0.0
    tax = calculate_tax(income)
    return round(tax / income * 100, 2)

def marginal_rate(income):
    for limit, rate in BRACKETS:
        if income <= limit:
            return rate
    return BRACKETS[-1][1]

def after_tax_income(gross):
    return round(gross - calculate_tax(gross), 2)

def tax_comparison(incomes):
    results = []
    for inc in incomes:
        results.append({
            "gross": inc,
            "tax": calculate_tax(inc),
            "effective_rate": effective_rate(inc),
            "net": after_tax_income(inc),
        })
    return results
''',


    # ── File/data handlers ───────────────────────────────────────────────

    "csv_reader": '''
def read_csv_records(text, delimiter=","):
    lines = text.strip().split("\\n")
    if not lines:
        return []
    headers = split_csv_line(lines[0], delimiter)
    records = []
    for line in lines[1:]:
        fields = split_csv_line(line, delimiter)
        record = {}
        for i, h in enumerate(headers):
            record[h] = fields[i] if i < len(fields) else ""
        records.append(record)
    return records

def split_csv_line(line, delimiter):
    fields = []
    current = []
    in_quote = False
    for ch in line:
        if in_quote:
            if ch == '"':
                in_quote = False
            else:
                current.append(ch)
        elif ch == '"':
            in_quote = True
        elif ch == delimiter:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields

def csv_to_table(records, columns=None):
    if not records:
        return ""
    if columns is None:
        columns = list(records[0].keys())
    widths = {c: len(c) for c in columns}
    for rec in records:
        for c in columns:
            widths[c] = max(widths[c], len(str(rec.get(c, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    rows = []
    for rec in records:
        row = "  ".join(str(rec.get(c, "")).ljust(widths[c]) for c in columns)
        rows.append(row)
    return "\\n".join([header, sep] + rows)
''',

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
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if current_section:
                config[current_section][key] = value
            else:
                config[key] = value
    return config

def get_value(config, section, key, default=None):
    if section in config and isinstance(config[section], dict):
        return config[section].get(key, default)
    return default

def config_to_string(config):
    lines = []
    for key, value in config.items():
        if isinstance(value, dict):
            lines.append("[" + key + "]")
            for k, v in value.items():
                lines.append(k + " = " + str(v))
            lines.append("")
        else:
            lines.append(key + " = " + str(value))
    return "\\n".join(lines)
''',

    "log_analyzer": '''
def parse_log_line(line):
    parts = line.split(" ", 3)
    if len(parts) < 4:
        return None
    return {
        "date": parts[0],
        "time": parts[1],
        "level": parts[2].strip("[]"),
        "message": parts[3],
    }

def parse_log(text):
    entries = []
    for line in text.strip().split("\\n"):
        entry = parse_log_line(line)
        if entry:
            entries.append(entry)
    return entries

def count_by_level(entries):
    counts = {}
    for entry in entries:
        level = entry["level"]
        counts[level] = counts.get(level, 0) + 1
    return counts

def filter_by_level(entries, level):
    return [e for e in entries if e["level"] == level]

def search_logs(entries, keyword):
    results = []
    for entry in entries:
        if keyword.lower() in entry["message"].lower():
            results.append(entry)
    return results

def error_summary(entries):
    errors = filter_by_level(entries, "ERROR")
    summary = {}
    for e in errors:
        msg = e["message"][:50]
        summary[msg] = summary.get(msg, 0) + 1
    return sorted(summary.items(), key=lambda x: -x[1])
''',

    "ini_parser": '''
def parse_ini(text):
    sections = {}
    current = None
    for raw_line in text.split("\\n"):
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections[current] = {}
        elif "=" in line and current is not None:
            key, val = line.split("=", 1)
            sections[current][key.strip()] = val.strip()
    return sections

def merge_ini(base, override):
    result = {}
    for section in base:
        result[section] = dict(base[section])
    for section in override:
        if section not in result:
            result[section] = {}
        result[section].update(override[section])
    return result

def ini_to_flat(sections):
    flat = {}
    for section, pairs in sections.items():
        for key, val in pairs.items():
            flat[section + "." + key] = val
    return flat

def flat_to_ini(flat):
    sections = {}
    for compound_key, val in flat.items():
        parts = compound_key.split(".", 1)
        if len(parts) == 2:
            section, key = parts
            if section not in sections:
                sections[section] = {}
            sections[section][key] = val
    return sections
''',

    "key_value_store": '''
class KeyValueStore:
    def __init__(self):
        self.data = {}
        self.versions = {}

    def set(self, key, value):
        if key in self.versions:
            self.versions[key].append(self.data[key])
        else:
            self.versions[key] = []
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def delete(self, key):
        if key in self.data:
            self.versions[key].append(self.data[key])
            del self.data[key]
            return True
        return False

    def exists(self, key):
        return key in self.data

    def keys(self):
        return list(self.data.keys())

    def history(self, key):
        past = list(self.versions.get(key, []))
        if key in self.data:
            past.append(self.data[key])
        return past

    def rollback(self, key):
        versions = self.versions.get(key, [])
        if not versions:
            return False
        self.data[key] = versions.pop()
        return True

    def size(self):
        return len(self.data)
''',

    "record_formatter": '''
def format_records(records, fmt="table"):
    if fmt == "table":
        return format_as_table(records)
    if fmt == "csv":
        return format_as_csv(records)
    if fmt == "json_lines":
        return format_as_json_lines(records)
    return str(records)

def format_as_table(records):
    if not records:
        return ""
    keys = list(records[0].keys())
    widths = {k: len(k) for k in keys}
    for rec in records:
        for k in keys:
            widths[k] = max(widths[k], len(str(rec.get(k, ""))))
    header = " | ".join(k.ljust(widths[k]) for k in keys)
    sep = "-+-".join("-" * widths[k] for k in keys)
    rows = []
    for rec in records:
        row = " | ".join(str(rec.get(k, "")).ljust(widths[k]) for k in keys)
        rows.append(row)
    return "\\n".join([header, sep] + rows)

def format_as_csv(records):
    if not records:
        return ""
    keys = list(records[0].keys())
    lines = [",".join(keys)]
    for rec in records:
        lines.append(",".join(str(rec.get(k, "")) for k in keys))
    return "\\n".join(lines)

def format_as_json_lines(records):
    lines = []
    for rec in records:
        parts = []
        for k, v in rec.items():
            parts.append('"' + str(k) + '": "' + str(v) + '"')
        lines.append("{" + ", ".join(parts) + "}")
    return "\\n".join(lines)
''',

    "data_aggregator": '''
def group_by(records, key):
    groups = {}
    for rec in records:
        val = rec.get(key)
        if val not in groups:
            groups[val] = []
        groups[val].append(rec)
    return groups

def aggregate(records, group_key, agg_key, func="sum"):
    groups = group_by(records, group_key)
    results = []
    for gval, recs in groups.items():
        values = [r.get(agg_key, 0) for r in recs]
        if func == "sum":
            agg_val = sum(values)
        elif func == "avg":
            agg_val = sum(values) / len(values) if values else 0
        elif func == "count":
            agg_val = len(values)
        elif func == "min":
            agg_val = min(values) if values else 0
        elif func == "max":
            agg_val = max(values) if values else 0
        else:
            agg_val = None
        results.append({group_key: gval, func: agg_val})
    return results

def pivot(records, row_key, col_key, val_key):
    rows = {}
    cols = set()
    for rec in records:
        r = rec[row_key]
        c = rec[col_key]
        v = rec[val_key]
        cols.add(c)
        if r not in rows:
            rows[r] = {}
        rows[r][c] = rows[r].get(c, 0) + v
    return rows, sorted(cols)
''',

    "histogram_builder": '''
def build_histogram(data, bins=10):
    if not data:
        return []
    lo = min(data)
    hi = max(data)
    if lo == hi:
        return [{"lo": lo, "hi": hi, "count": len(data)}]
    bin_width = (hi - lo) / bins
    counts = [0] * bins
    for val in data:
        idx = int((val - lo) / bin_width)
        if idx == bins:
            idx -= 1
        counts[idx] += 1
    result = []
    for i in range(bins):
        result.append({
            "lo": round(lo + i * bin_width, 4),
            "hi": round(lo + (i + 1) * bin_width, 4),
            "count": counts[i],
        })
    return result

def print_histogram(data, bins=10, width=40):
    hist = build_histogram(data, bins)
    if not hist:
        return ""
    max_count = max(h["count"] for h in hist)
    lines = []
    for h in hist:
        bar_len = int(h["count"] / max_count * width) if max_count > 0 else 0
        label = "{:.2f}-{:.2f}".format(h["lo"], h["hi"])
        lines.append("{:>15} | {} ({})".format(label, "#" * bar_len, h["count"]))
    return "\\n".join(lines)

def cumulative_histogram(data, bins=10):
    hist = build_histogram(data, bins)
    total = 0
    for h in hist:
        total += h["count"]
        h["cumulative"] = total
    return hist
''',

    "table_formatter": '''
def make_table(data, headers=None, align="left"):
    if not data:
        return ""
    if headers is None:
        headers = ["col_" + str(i) for i in range(len(data[0]))]
    col_widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    def fmt_cell(val, width):
        s = str(val)
        if align == "right":
            return s.rjust(width)
        if align == "center":
            return s.center(width)
        return s.ljust(width)
    lines = []
    header_line = " | ".join(fmt_cell(h, col_widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("-+-".join("-" * w for w in col_widths))
    for row in data:
        cells = []
        for i in range(len(headers)):
            val = row[i] if i < len(row) else ""
            cells.append(fmt_cell(val, col_widths[i]))
        lines.append(" | ".join(cells))
    return "\\n".join(lines)

def transpose_table(data):
    if not data:
        return []
    max_cols = max(len(row) for row in data)
    result = []
    for col in range(max_cols):
        new_row = []
        for row in data:
            new_row.append(row[col] if col < len(row) else "")
        result.append(new_row)
    return result
''',

    "report_generator": '''
def generate_report(title, sections):
    lines = []
    lines.append("=" * 60)
    lines.append(title.center(60))
    lines.append("=" * 60)
    lines.append("")
    for section in sections:
        lines.append(section["heading"])
        lines.append("-" * len(section["heading"]))
        if "text" in section:
            lines.append(section["text"])
        if "table" in section:
            lines.append(format_simple_table(section["table"]))
        if "items" in section:
            for item in section["items"]:
                lines.append("  * " + str(item))
        lines.append("")
    return "\\n".join(lines)

def format_simple_table(rows):
    if not rows:
        return ""
    widths = [0] * len(rows[0])
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    lines = []
    for row in rows:
        parts = []
        for i, cell in enumerate(row):
            if i < len(widths):
                parts.append(str(cell).ljust(widths[i]))
        lines.append("  ".join(parts))
    return "\\n".join(lines)

def summarize_data(data, label="Summary"):
    if not data:
        return label + ": no data"
    total = sum(data)
    avg = total / len(data)
    lo = min(data)
    hi = max(data)
    return "{}: n={}, sum={}, avg={:.2f}, min={}, max={}".format(
        label, len(data), total, avg, lo, hi
    )
''',


    # ── Web utilities ────────────────────────────────────────────────────

    "url_parser": '''
def parse_url(url):
    result = {"scheme": "", "host": "", "port": None, "path": "/", "query": "", "fragment": ""}
    if "://" in url:
        result["scheme"], rest = url.split("://", 1)
    else:
        rest = url
    if "#" in rest:
        rest, result["fragment"] = rest.rsplit("#", 1)
    if "?" in rest:
        rest, result["query"] = rest.split("?", 1)
    if "/" in rest:
        host_part, path = rest.split("/", 1)
        result["path"] = "/" + path
    else:
        host_part = rest
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        result["host"] = host
        result["port"] = int(port_str) if port_str.isdigit() else None
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

def parse_query_string(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            key, val = pair.split("=", 1)
            params[key] = val
        else:
            params[pair] = ""
    return params
''',

    "http_header_builder": '''
def build_headers(method, path, host, extra=None):
    lines = []
    lines.append("{} {} HTTP/1.1".format(method, path))
    lines.append("Host: {}".format(host))
    lines.append("Accept: */*")
    lines.append("Connection: keep-alive")
    if extra:
        for key, val in extra.items():
            lines.append("{}: {}".format(key, val))
    lines.append("")
    lines.append("")
    return "\\r\\n".join(lines)

def parse_headers(raw):
    headers = {}
    lines = raw.split("\\r\\n")
    status_line = lines[0] if lines else ""
    for line in lines[1:]:
        if ": " in line:
            key, val = line.split(": ", 1)
            headers[key.lower()] = val
    return status_line, headers

def build_response(status_code, body, content_type="text/plain"):
    status_map = {200: "OK", 404: "Not Found", 500: "Internal Server Error"}
    reason = status_map.get(status_code, "Unknown")
    lines = []
    lines.append("HTTP/1.1 {} {}".format(status_code, reason))
    lines.append("Content-Type: {}".format(content_type))
    lines.append("Content-Length: {}".format(len(body)))
    lines.append("")
    lines.append(body)
    return "\\r\\n".join(lines)

def get_content_length(headers):
    val = headers.get("content-length", "0")
    try:
        return int(val)
    except ValueError:
        return 0
''',

    "query_string_encoder": '''
def encode_component(s):
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")
    result = []
    for ch in s:
        if ch in safe:
            result.append(ch)
        elif ch == " ":
            result.append("+")
        else:
            for byte in ch.encode("utf-8"):
                result.append("%{:02X}".format(byte))
    return "".join(result)

def decode_component(s):
    result = []
    i = 0
    while i < len(s):
        if s[i] == "+" :
            result.append(" ")
            i += 1
        elif s[i] == "%" and i + 2 < len(s):
            hex_str = s[i + 1:i + 3]
            result.append(chr(int(hex_str, 16)))
            i += 3
        else:
            result.append(s[i])
            i += 1
    return "".join(result)

def encode_query(params):
    parts = []
    for key, val in sorted(params.items()):
        parts.append(encode_component(str(key)) + "=" + encode_component(str(val)))
    return "&".join(parts)

def decode_query(qs):
    params = {}
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[decode_component(k)] = decode_component(v)
    return params
''',

    "cookie_parser": '''
def parse_cookie_header(header):
    cookies = {}
    for pair in header.split(";"):
        pair = pair.strip()
        if "=" in pair:
            key, val = pair.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies

def build_set_cookie(name, value, max_age=None, path="/", secure=False, http_only=False):
    parts = ["{}={}".format(name, value)]
    if path:
        parts.append("Path={}".format(path))
    if max_age is not None:
        parts.append("Max-Age={}".format(max_age))
    if secure:
        parts.append("Secure")
    if http_only:
        parts.append("HttpOnly")
    return "; ".join(parts)

def parse_set_cookie(header):
    parts = header.split(";")
    main = parts[0].strip()
    if "=" not in main:
        return None
    name, value = main.split("=", 1)
    result = {"name": name.strip(), "value": value.strip()}
    for part in parts[1:]:
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip().lower()] = v.strip()
        else:
            result[part.lower()] = True
    return result

def filter_cookies(cookies, prefix):
    return {k: v for k, v in cookies.items() if k.startswith(prefix)}
''',

    "html_entity_encoder": '''
ENTITIES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}

REVERSE_ENTITIES = {v: k for k, v in ENTITIES.items()}

def html_escape(text):
    result = []
    for ch in text:
        if ch in ENTITIES:
            result.append(ENTITIES[ch])
        else:
            result.append(ch)
    return "".join(result)

def html_unescape(text):
    result = text
    for entity, ch in REVERSE_ENTITIES.items():
        result = result.replace(entity, ch)
    i = 0
    output = []
    while i < len(result):
        if result[i:i+2] == "&#":
            end = result.find(";", i)
            if end != -1:
                code_str = result[i+2:end]
                if code_str.startswith("x"):
                    code = int(code_str[1:], 16)
                else:
                    code = int(code_str)
                output.append(chr(code))
                i = end + 1
                continue
        output.append(result[i])
        i += 1
    return "".join(output)

def strip_tags(html):
    result = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            result.append(ch)
    return "".join(result)
''',

    "content_type_parser": '''
def parse_content_type(header):
    parts = header.split(";")
    media_type = parts[0].strip()
    params = {}
    for part in parts[1:]:
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            val = val.strip().strip('"')
            params[key.strip()] = val
    main_type = media_type.split("/")[0] if "/" in media_type else media_type
    sub_type = media_type.split("/")[1] if "/" in media_type else ""
    return {
        "media_type": media_type,
        "main_type": main_type,
        "sub_type": sub_type,
        "params": params,
    }

def build_content_type(media_type, charset=None, boundary=None):
    parts = [media_type]
    if charset:
        parts.append("charset=" + charset)
    if boundary:
        parts.append("boundary=" + boundary)
    return "; ".join(parts)

def is_text_type(content_type):
    parsed = parse_content_type(content_type)
    if parsed["main_type"] == "text":
        return True
    if parsed["sub_type"] in ("json", "xml", "html", "javascript"):
        return True
    return False

def get_charset(content_type, default="utf-8"):
    parsed = parse_content_type(content_type)
    return parsed["params"].get("charset", default)
''',

    "basic_auth_encoder": '''
def encode_base64_simple(data):
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    if isinstance(data, str):
        data = data.encode("utf-8")
    result = []
    i = 0
    while i < len(data):
        b0 = data[i]
        b1 = data[i + 1] if i + 1 < len(data) else 0
        b2 = data[i + 2] if i + 2 < len(data) else 0
        result.append(chars[b0 >> 2])
        result.append(chars[((b0 & 3) << 4) | (b1 >> 4)])
        if i + 1 < len(data):
            result.append(chars[((b1 & 15) << 2) | (b2 >> 6)])
        else:
            result.append("=")
        if i + 2 < len(data):
            result.append(chars[b2 & 63])
        else:
            result.append("=")
        i += 3
    return "".join(result)

def make_basic_auth(username, password):
    credentials = username + ":" + password
    encoded = encode_base64_simple(credentials)
    return "Basic " + encoded

def parse_basic_auth(header):
    if not header.startswith("Basic "):
        return None
    encoded = header[6:]
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    bits = []
    for ch in encoded:
        if ch == "=":
            break
        idx = chars.index(ch)
        for bit in range(5, -1, -1):
            bits.append((idx >> bit) & 1)
    bytes_out = []
    for i in range(0, len(bits) - 7, 8):
        val = 0
        for bit in bits[i:i + 8]:
            val = (val << 1) | bit
        bytes_out.append(chr(val))
    decoded = "".join(bytes_out)
    if ":" in decoded:
        user, pwd = decoded.split(":", 1)
        return {"username": user, "password": pwd}
    return None
''',

    "path_normalizer": '''
def normalize_path(path):
    parts = path.replace("\\\\", "/").split("/")
    stack = []
    is_absolute = path.startswith("/")
    for part in parts:
        if part == "" or part == ".":
            continue
        if part == "..":
            if stack and stack[-1] != "..":
                stack.pop()
            elif not is_absolute:
                stack.append("..")
        else:
            stack.append(part)
    result = "/".join(stack)
    if is_absolute:
        result = "/" + result
    if not result:
        return "." if not is_absolute else "/"
    return result

def join_paths(*paths):
    if not paths:
        return "."
    result = paths[0]
    for p in paths[1:]:
        if p.startswith("/"):
            result = p
        else:
            if not result.endswith("/"):
                result += "/"
            result += p
    return normalize_path(result)

def split_path(path):
    normalized = normalize_path(path)
    if "/" not in normalized:
        return (".", normalized)
    last_slash = normalized.rfind("/")
    directory = normalized[:last_slash] or "/"
    filename = normalized[last_slash + 1:]
    return (directory, filename)

def get_extension(path):
    _, filename = split_path(path)
    if "." in filename:
        return filename.rsplit(".", 1)[1]
    return ""
''',

    "form_data_encoder": '''
def encode_form_data(fields, boundary="----FormBoundary"):
    parts = []
    for key, val in fields.items():
        parts.append("--" + boundary)
        if isinstance(val, dict):
            filename = val.get("filename", "file")
            content = val.get("content", "")
            ct = val.get("content_type", "application/octet-stream")
            parts.append(
                'Content-Disposition: form-data; name="{}"; filename="{}"'.format(key, filename)
            )
            parts.append("Content-Type: {}".format(ct))
            parts.append("")
            parts.append(content)
        else:
            parts.append('Content-Disposition: form-data; name="{}"'.format(key))
            parts.append("")
            parts.append(str(val))
    parts.append("--" + boundary + "--")
    return "\\r\\n".join(parts)

def parse_form_urlencoded(body):
    fields = {}
    for pair in body.split("&"):
        if "=" in pair:
            key, val = pair.split("=", 1)
            fields[key] = val
    return fields

def build_form_urlencoded(fields):
    parts = []
    for key, val in sorted(fields.items()):
        parts.append("{}={}".format(key, val))
    return "&".join(parts)

def validate_form_data(fields, required_keys):
    missing = []
    for key in required_keys:
        if key not in fields or not fields[key]:
            missing.append(key)
    return len(missing) == 0, missing
''',

    "response_builder": '''
class ResponseBuilder:
    STATUS_CODES = {
        200: "OK",
        201: "Created",
        204: "No Content",
        301: "Moved Permanently",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        500: "Internal Server Error",
    }

    def __init__(self, status=200):
        self.status = status
        self.headers = {}
        self.body = ""

    def set_header(self, key, value):
        self.headers[key] = value
        return self

    def set_body(self, body, content_type="text/plain"):
        self.body = body
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))
        return self

    def set_json(self, data):
        body = str(data)
        return self.set_body(body, "application/json")

    def redirect(self, url, permanent=False):
        self.status = 301 if permanent else 302
        self.headers["Location"] = url
        return self

    def build(self):
        reason = self.STATUS_CODES.get(self.status, "Unknown")
        lines = ["HTTP/1.1 {} {}".format(self.status, reason)]
        for key, val in self.headers.items():
            lines.append("{}: {}".format(key, val))
        lines.append("")
        lines.append(self.body)
        return "\\r\\n".join(lines)

    def to_dict(self):
        return {
            "status": self.status,
            "headers": dict(self.headers),
            "body": self.body,
        }
''',


    # ── Compression / encoding ───────────────────────────────────────────

    "rle_compression": '''
def rle_compress(data):
    if not data:
        return []
    runs = []
    current = data[0]
    count = 1
    for i in range(1, len(data)):
        if data[i] == current and count < 255:
            count += 1
        else:
            runs.append((current, count))
            current = data[i]
            count = 1
    runs.append((current, count))
    return runs

def rle_decompress(runs):
    result = []
    for val, count in runs:
        result.extend([val] * count)
    return result

def rle_compress_string(s):
    runs = rle_compress(list(s))
    parts = []
    for ch, count in runs:
        if count > 1:
            parts.append(str(count) + ch)
        else:
            parts.append(ch)
    return "".join(parts)

def compression_ratio(original, compressed):
    if not original:
        return 0.0
    orig_size = len(original)
    comp_size = sum(2 for _ in compressed)
    return round(1.0 - comp_size / orig_size, 4) if orig_size > 0 else 0.0

def analyze_runs(data):
    runs = rle_compress(data)
    if not runs:
        return {"total_runs": 0, "avg_length": 0, "max_length": 0}
    lengths = [c for _, c in runs]
    return {
        "total_runs": len(runs),
        "avg_length": round(sum(lengths) / len(lengths), 2),
        "max_length": max(lengths),
    }
''',

    "base64_codec": '''
B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

def b64_encode(data):
    if isinstance(data, str):
        data = [ord(c) for c in data]
    result = []
    i = 0
    while i < len(data):
        b0 = data[i]
        b1 = data[i + 1] if i + 1 < len(data) else 0
        b2 = data[i + 2] if i + 2 < len(data) else 0
        result.append(B64_CHARS[b0 >> 2])
        result.append(B64_CHARS[((b0 & 3) << 4) | (b1 >> 4)])
        if i + 1 < len(data):
            result.append(B64_CHARS[((b1 & 15) << 2) | (b2 >> 6)])
        else:
            result.append("=")
        if i + 2 < len(data):
            result.append(B64_CHARS[b2 & 63])
        else:
            result.append("=")
        i += 3
    return "".join(result)

def b64_decode(encoded):
    result = []
    buf = []
    for ch in encoded:
        if ch == "=":
            break
        if ch in B64_CHARS:
            buf.append(B64_CHARS.index(ch))
        if len(buf) == 4:
            result.append((buf[0] << 2) | (buf[1] >> 4))
            result.append(((buf[1] & 15) << 4) | (buf[2] >> 2))
            result.append(((buf[2] & 3) << 6) | buf[3])
            buf = []
    if len(buf) == 3:
        result.append((buf[0] << 2) | (buf[1] >> 4))
        result.append(((buf[1] & 15) << 4) | (buf[2] >> 2))
    elif len(buf) == 2:
        result.append((buf[0] << 2) | (buf[1] >> 4))
    return result
''',

    "huffman_coding": '''
def build_freq_table(data):
    freq = {}
    for ch in data:
        freq[ch] = freq.get(ch, 0) + 1
    return freq

def build_huffman_tree(freq):
    nodes = [(count, i, char) for i, (char, count) in enumerate(freq.items())]
    nodes.sort()
    counter = len(nodes)
    while len(nodes) > 1:
        lo = nodes.pop(0)
        hi = nodes.pop(0)
        merged = (lo[0] + hi[0], counter, (lo, hi))
        counter += 1
        nodes.append(merged)
        nodes.sort()
    return nodes[0] if nodes else None

def build_code_table(tree, prefix=""):
    if tree is None:
        return {}
    _, _, val = tree
    if not isinstance(val, tuple):
        return {val: prefix or "0"}
    left, right = val
    codes = {}
    codes.update(build_code_table(left, prefix + "0"))
    codes.update(build_code_table(right, prefix + "1"))
    return codes

def huffman_encode(data):
    freq = build_freq_table(data)
    if not freq:
        return "", {}
    tree = build_huffman_tree(freq)
    codes = build_code_table(tree)
    encoded = "".join(codes[ch] for ch in data)
    return encoded, codes

def huffman_stats(data):
    freq = build_freq_table(data)
    tree = build_huffman_tree(freq)
    codes = build_code_table(tree)
    total_bits = sum(len(codes[ch]) * freq[ch] for ch in freq)
    return {
        "symbols": len(freq),
        "total_bits": total_bits,
        "avg_bits": round(total_bits / len(data), 3) if data else 0,
        "compression": round(1 - total_bits / (len(data) * 8), 3) if data else 0,
    }
''',

    "lzw_compress": '''
def lzw_encode(data):
    if not data:
        return []
    dictionary = {}
    for i in range(256):
        dictionary[chr(i)] = i
    next_code = 256
    result = []
    w = ""
    for ch in data:
        wc = w + ch
        if wc in dictionary:
            w = wc
        else:
            result.append(dictionary[w])
            dictionary[wc] = next_code
            next_code += 1
            w = ch
    if w:
        result.append(dictionary[w])
    return result

def lzw_decode(codes):
    if not codes:
        return ""
    dictionary = {}
    for i in range(256):
        dictionary[i] = chr(i)
    next_code = 256
    result = [dictionary[codes[0]]]
    w = result[0]
    for code in codes[1:]:
        if code in dictionary:
            entry = dictionary[code]
        elif code == next_code:
            entry = w + w[0]
        else:
            return "".join(result)
        result.append(entry)
        dictionary[next_code] = w + entry[0]
        next_code += 1
        w = entry
    return "".join(result)

def lzw_compression_ratio(data):
    codes = lzw_encode(data)
    orig_bits = len(data) * 8
    comp_bits = len(codes) * 12
    return round(1.0 - comp_bits / orig_bits, 4) if orig_bits > 0 else 0.0
''',

    "bit_packer": '''
class BitPacker:
    def __init__(self):
        self.bits = []

    def write_bits(self, value, n_bits):
        for i in range(n_bits - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def write_byte(self, value):
        self.write_bits(value & 0xFF, 8)

    def to_bytes(self):
        result = []
        for i in range(0, len(self.bits), 8):
            byte = 0
            for j in range(8):
                if i + j < len(self.bits):
                    byte = (byte << 1) | self.bits[i + j]
                else:
                    byte = byte << 1
            result.append(byte)
        return result

    def size_bits(self):
        return len(self.bits)

class BitReader:
    def __init__(self, data):
        self.bits = []
        for byte in data:
            for i in range(7, -1, -1):
                self.bits.append((byte >> i) & 1)
        self.pos = 0

    def read_bits(self, n):
        value = 0
        for _ in range(n):
            if self.pos < len(self.bits):
                value = (value << 1) | self.bits[self.pos]
                self.pos += 1
        return value

    def read_byte(self):
        return self.read_bits(8)

    def remaining(self):
        return len(self.bits) - self.pos
''',

    "checksum_calculator": '''
def simple_checksum(data):
    total = 0
    for byte in data:
        if isinstance(byte, str):
            byte = ord(byte)
        total = (total + byte) & 0xFFFF
    return total

def xor_checksum(data):
    result = 0
    for byte in data:
        if isinstance(byte, str):
            byte = ord(byte)
        result ^= byte
    return result

def fletcher16(data):
    sum1 = 0
    sum2 = 0
    for byte in data:
        if isinstance(byte, str):
            byte = ord(byte)
        sum1 = (sum1 + byte) % 255
        sum2 = (sum2 + sum1) % 255
    return (sum2 << 8) | sum1

def adler32(data):
    a = 1
    b = 0
    for byte in data:
        if isinstance(byte, str):
            byte = ord(byte)
        a = (a + byte) % 65521
        b = (b + a) % 65521
    return (b << 16) | a

def verify_checksum(data, expected, algorithm="simple"):
    if algorithm == "simple":
        return simple_checksum(data) == expected
    if algorithm == "xor":
        return xor_checksum(data) == expected
    if algorithm == "fletcher16":
        return fletcher16(data) == expected
    if algorithm == "adler32":
        return adler32(data) == expected
    return False
''',

    "hex_encoder": '''
HEX_CHARS = "0123456789abcdef"

def hex_encode(data):
    result = []
    for byte in data:
        if isinstance(byte, str):
            byte = ord(byte)
        result.append(HEX_CHARS[byte >> 4])
        result.append(HEX_CHARS[byte & 0x0F])
    return "".join(result)

def hex_decode(hex_str):
    result = []
    for i in range(0, len(hex_str), 2):
        high = HEX_CHARS.index(hex_str[i].lower())
        low = HEX_CHARS.index(hex_str[i + 1].lower())
        result.append(high << 4 | low)
    return result

def hex_dump(data, width=16):
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_part = " ".join("{:02x}".format(b if isinstance(b, int) else ord(b)) for b in chunk)
        ascii_part = ""
        for b in chunk:
            v = b if isinstance(b, int) else ord(b)
            ascii_part += chr(v) if 32 <= v < 127 else "."
        lines.append("{:08x}  {:<{}}  {}".format(offset, hex_part, width * 3 - 1, ascii_part))
    return "\\n".join(lines)

def is_valid_hex(s):
    valid = set("0123456789abcdefABCDEF")
    return all(c in valid for c in s) and len(s) % 2 == 0
''',

    "varint_encoder": '''
def encode_varint(value):
    if value < 0:
        value = ((-value) << 1) | 1
    else:
        value = value << 1
    result = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return result

def decode_varint(data, offset=0):
    result = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80):
            break
        shift += 7
    if result & 1:
        result = -((result >> 1))
    else:
        result = result >> 1
    return result, offset

def encode_varint_list(values):
    result = []
    result.extend(encode_varint(len(values)))
    for v in values:
        result.extend(encode_varint(v))
    return result

def decode_varint_list(data):
    offset = 0
    count, offset = decode_varint(data, offset)
    values = []
    for _ in range(count):
        val, offset = decode_varint(data, offset)
        values.append(val)
    return values
''',

    "xor_cipher": '''
def xor_encrypt(data, key):
    result = []
    key_len = len(key)
    for i, byte in enumerate(data):
        if isinstance(byte, str):
            byte = ord(byte)
        key_byte = key[i % key_len]
        if isinstance(key_byte, str):
            key_byte = ord(key_byte)
        result.append(byte ^ key_byte)
    return result

def xor_decrypt(data, key):
    return xor_encrypt(data, key)

def xor_encrypt_string(text, key):
    encrypted = xor_encrypt(text, key)
    return "".join("{:02x}".format(b) for b in encrypted)

def xor_decrypt_string(hex_text, key):
    data = []
    for i in range(0, len(hex_text), 2):
        data.append(int(hex_text[i:i + 2], 16))
    decrypted = xor_decrypt(data, key)
    return "".join(chr(b) for b in decrypted)

def find_xor_key_length(ciphertext, max_len=20):
    best_len = 1
    best_score = float("inf")
    for kl in range(1, max_len + 1):
        score = 0
        for i in range(len(ciphertext) - kl):
            score += bin(ciphertext[i] ^ ciphertext[i + kl]).count("1")
        normalized = score / (len(ciphertext) - kl) if len(ciphertext) > kl else float("inf")
        if normalized < best_score:
            best_score = normalized
            best_len = kl
    return best_len
''',

    "crc32_calculator": '''
def make_crc_table():
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
        table.append(crc)
    return table

CRC_TABLE = make_crc_table()

def crc32(data):
    crc = 0xFFFFFFFF
    for byte in data:
        if isinstance(byte, str):
            byte = ord(byte)
        crc = CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF

def crc32_hex(data):
    val = crc32(data)
    return "{:08x}".format(val)

def verify_crc32(data, expected):
    return crc32(data) == expected

def crc32_combine(data_parts):
    combined = []
    for part in data_parts:
        combined.extend(part)
    return crc32(combined)

def crc32_file_chunks(chunks):
    results = []
    for chunk in chunks:
        results.append({
            "size": len(chunk),
            "crc32": crc32_hex(chunk),
        })
    overall = crc32_combine(chunks)
    results.append({"overall_crc32": "{:08x}".format(overall)})
    return results
''',

}


# ── 30 intentionally inconsistent programs (H¹≠0 expected) ──────────────

INCONSISTENT_PROGRAMS = {

    "mixed_return_types": '''
def process_value(x):
    if isinstance(x, int) and x > 0:
        return x * 2
    elif isinstance(x, str):
        return x.upper()
    else:
        return None

def accumulate(values):
    result = 0
    for v in values:
        processed = process_value(v)
        result = result + processed
    return result

def format_result(value):
    if value > 100:
        return "large: " + str(value)
    return value

def pipeline(data):
    intermediate = [format_result(process_value(d)) for d in data]
    total = sum(intermediate)
    return total
''',

    "missing_return_branch": '''
def classify(value):
    if value > 100:
        return "high"
    elif value > 50:
        return "medium"
    elif value > 0:
        return "low"

def process_batch(items):
    results = []
    for item in items:
        label = classify(item)
        results.append(label.upper())
    return results

def find_category(items, target):
    for item in items:
        cat = classify(item)
        if cat == target:
            return item

def summarize(data):
    categories = {}
    for val in data:
        cat = classify(val)
        categories[cat] = categories.get(cat, 0) + 1
    return categories
''',

    "type_coercion_fail": '''
def parse_input(raw):
    parts = raw.split(",")
    result = []
    for p in parts:
        p = p.strip()
        if p.isdigit():
            result.append(int(p))
        else:
            result.append(p)
    return result

def total_score(items):
    score = 0
    for item in items:
        score += item
    return score

def average_score(items):
    total = total_score(items)
    count = len(items)
    return total / count

def format_scores(items):
    result = []
    for item in items:
        result.append("Score: " + item + " points")
    return result
''',

    "undefined_variable_path": '''
def compute(mode, value):
    if mode == "double":
        result = value * 2
    elif mode == "triple":
        result = value * 3
    return result

def batch_compute(pairs):
    outputs = []
    for mode, value in pairs:
        out = compute(mode, value)
        outputs.append(out)
    return outputs

def find_max_result(pairs):
    best = None
    for mode, value in pairs:
        r = compute(mode, value)
        if best is None or r > best:
            best = r
            best_mode = mode
    return best, best_mode

def summarize_results(pairs):
    results = batch_compute(pairs)
    total = sum(results)
    avg = total / len(results)
    return {"total": total, "average": avg, "count": len(results)}
''',

    "contradictory_assertions": '''
def validate_range(value, low, high):
    assert low < high, "low must be less than high"
    assert value >= low, "value below minimum"
    assert value <= high, "value above maximum"
    return True

def process_data(data):
    cleaned = []
    for item in data:
        assert item > 0, "must be positive"
        assert item < 0, "must be negative"
        cleaned.append(item)
    return cleaned

def check_invariants(state):
    assert state["count"] >= 0
    assert state["total"] >= 0
    avg = state["total"] / state["count"]
    assert avg >= 0
    return avg

def validate_config(config):
    assert config.get("timeout") > 0, "timeout must be positive"
    assert config.get("retries") >= 0, "retries must be non-negative"
    assert config.get("timeout") < config.get("max_wait", 100)
    return True
''',

    "dead_code_undefined": '''
def calculate(x, y, op):
    if op == "add":
        return x + y
    elif op == "sub":
        return x - y
    elif op == "mul":
        return x * y
    elif op == "div":
        if y != 0:
            return x / y
        return 0
    return None
    z = undefined_var + x
    result = z * phantom_function(y)
    return result

def batch_calculate(operations):
    results = []
    for x, y, op in operations:
        r = calculate(x, y, op)
        results.append(r)
    return results

def running_total(operations):
    total = 0
    for x, y, op in operations:
        total += calculate(x, y, op)
    return total
''',

    "loop_invariant_broken": '''
def sorted_insert(arr, value):
    result = list(arr)
    inserted = False
    for i in range(len(result)):
        if value <= result[i]:
            result.insert(i, value)
            inserted = True
            break
    if not inserted:
        result.append(value)
    return result

def build_sorted(values):
    result = []
    for v in values:
        result = sorted_insert(result, v)
        result[0] = result[0] + 1
    return result

def verify_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True

def median_of_sorted(arr):
    n = len(arr)
    if n == 0:
        return None
    if n % 2 == 1:
        return arr[n // 2]
    return (arr[n // 2 - 1] + arr[n // 2]) / 2.0
''',

    "mutable_default_leak": '''
def add_item(item, items=[]):
    items.append(item)
    return items

def build_list(values):
    result = add_item(values[0])
    for v in values[1:]:
        result = add_item(v)
    return result

def create_user(name, roles=[]):
    roles.append("viewer")
    return {"name": name, "roles": roles}

def process_users(names):
    users = []
    for name in names:
        user = create_user(name)
        users.append(user)
    return users

def create_config(key, value, options={}):
    options[key] = value
    return dict(options)

def batch_config(pairs):
    configs = []
    for key, value in pairs:
        configs.append(create_config(key, value))
    return configs
''',

    "division_by_zero_path": '''
def safe_divide(a, b):
    return a / b

def average(values):
    total = sum(values)
    return safe_divide(total, len(values))

def weighted_average(values, weights):
    total = 0
    weight_sum = 0
    for v, w in zip(values, weights):
        total += v * w
        weight_sum += w
    return safe_divide(total, weight_sum)

def normalize(values):
    max_val = max(values) - min(values)
    return [safe_divide(v - min(values), max_val) for v in values]

def percentage_change(old, new):
    return safe_divide(new - old, old) * 100

def harmonic_mean(values):
    reciprocals = [safe_divide(1, v) for v in values]
    return safe_divide(len(values), sum(reciprocals))
''',

    "index_out_of_range": '''
def get_element(arr, idx):
    return arr[idx]

def get_neighbors(arr, idx):
    left = arr[idx - 1]
    right = arr[idx + 1]
    return left, right

def sliding_window(arr, size):
    results = []
    for i in range(len(arr)):
        window = []
        for j in range(size):
            window.append(arr[i + j])
        results.append(sum(window) / size)
    return results

def matrix_get(matrix, row, col):
    return matrix[row][col]

def diagonal(matrix, n):
    return [matrix[i][i] for i in range(n)]

def zip_arrays(a, b):
    result = []
    for i in range(max(len(a), len(b))):
        result.append((a[i], b[i]))
    return result
''',


    "inconsistent_comparator": '''
class BadComparator:
    def __init__(self, val):
        self.val = val

    def __lt__(self, other):
        return self.val % 3 < other.val % 3

    def __gt__(self, other):
        return self.val > other.val

    def __eq__(self, other):
        return self.val == other.val

def sort_with_comparator(values):
    items = [BadComparator(v) for v in values]
    n = len(items)
    for i in range(n):
        for j in range(0, n - i - 1):
            if items[j] > items[j + 1]:
                items[j], items[j + 1] = items[j + 1], items[j]
    return [item.val for item in items]

def find_minimum(values):
    items = [BadComparator(v) for v in values]
    best = items[0]
    for item in items[1:]:
        if item < best:
            best = item
    return best.val
''',

    "shadowed_variable": '''
x = 10

def outer():
    x = 20
    def inner():
        return x + 5
    result = inner()
    return result

def calculate_total(items):
    total = 0
    for item in items:
        total = item.get("price", 0)
        tax = total * 0.1
        total = total + tax
    return total

def process(data):
    result = []
    for item in data:
        result = item
    for item in data:
        result.append(item * 2)
    return result

def confusing_scope():
    values = [1, 2, 3]
    total = sum(values)
    values = total
    return len(values)
''',

    "unreachable_code": '''
def always_returns(x):
    if x > 0:
        return "positive"
    else:
        return "non-positive"
    result = x * 2
    return result + 10

def early_exit(items):
    for item in items:
        return item
    return None
    total = sum(items)
    average = total / len(items)
    return average

def nested_returns(a, b):
    if a > b:
        return a
    return b
    diff = abs(a - b)
    ratio = a / b
    return {"diff": diff, "ratio": ratio}

def process_all(values):
    results = []
    for v in values:
        results.append(always_returns(v))
    return results
''',

    "mixed_none_return": '''
def find_item(collection, key):
    for item in collection:
        if item.get("id") == key:
            return item

def get_name(collection, key):
    item = find_item(collection, key)
    return item["name"]

def safe_get(collection, key, field):
    item = find_item(collection, key)
    if item is not None:
        return item.get(field)

def batch_lookup(collection, keys):
    results = []
    for key in keys:
        item = find_item(collection, key)
        results.append(item["name"])
    return results

def count_matching(collection, field, value):
    count = 0
    for item in collection:
        found = find_item([item], item.get("id"))
        if found.get(field) == value:
            count += 1
    return count
''',

    "recursive_type_change": '''
def flatten(data):
    if isinstance(data, list):
        result = []
        for item in data:
            result.extend(flatten(item))
        return result
    return data

def deep_sum(data):
    if isinstance(data, list):
        return sum(deep_sum(item) for item in data)
    return data

def transform(data, depth=0):
    if isinstance(data, list):
        return [transform(item, depth + 1) for item in data]
    if depth > 2:
        return str(data)
    return data * 2

def collect_values(data):
    flat = flatten(data)
    total = 0
    for v in flat:
        total += v
    return total
''',

    "stale_closure": '''
def make_adders(n):
    adders = []
    for i in range(n):
        adders.append(lambda x: x + i)
    return adders

def apply_adders(adders, value):
    return [f(value) for f in adders]

def make_multipliers(factors):
    funcs = []
    for f in factors:
        funcs.append(lambda x: x * f)
    return funcs

def build_pipeline(operations):
    steps = []
    for op in operations:
        name = op["name"]
        val = op["value"]
        steps.append(lambda x: x + val if name == "add" else x * val)
    return steps

def run_pipeline(steps, initial):
    result = initial
    for step in steps:
        result = step(result)
    return result
''',

    "wrong_exception_type": '''
def parse_number(s):
    try:
        return int(s)
    except TypeError:
        return None

def safe_index(arr, idx):
    try:
        return arr[idx]
    except KeyError:
        return None

def read_config_value(config, key):
    try:
        value = config[key]
        return int(value)
    except IndexError:
        return 0

def divide_safe(a, b):
    try:
        return a / b
    except TypeError:
        return 0

def batch_parse(values):
    results = []
    for v in values:
        parsed = parse_number(v)
        results.append(parsed)
    return results

def process_config(config, keys):
    result = {}
    for key in keys:
        result[key] = read_config_value(config, key)
    return result
''',

    "aliased_mutation": '''
def process_list(data):
    backup = data
    data.append(999)
    if len(data) > 10:
        data.clear()
    return backup

def merge_configs(base, override):
    result = base
    for key, val in override.items():
        result[key] = val
    return result, base

def update_nested(data, path, value):
    current = data
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    return data

def copy_and_modify(items):
    copy = items
    copy.sort()
    copy.reverse()
    return items, copy

def accumulate_results(batches):
    all_results = []
    for batch in batches:
        results = batch
        results.extend([0, 0])
        all_results.append(results)
    return all_results
''',

    "off_by_one_loop": '''
def sum_range(start, end):
    total = 0
    for i in range(start, end):
        total += i
    return total

def find_last(arr, target):
    for i in range(len(arr), -1, -1):
        if arr[i] == target:
            return i
    return -1

def rotate_left(arr, k):
    n = len(arr)
    result = [0] * n
    for i in range(n):
        result[i] = arr[(i + k) % (n + 1)]
    return result

def binary_search_off(arr, target):
    lo = 0
    hi = len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid
        else:
            hi = mid
    return -1

def subarray_sum(arr, start, length):
    total = 0
    for i in range(length + 1):
        total += arr[start + i]
    return total
''',

    "inconsistent_encoding": '''
def encode_message(text):
    result = []
    for ch in text:
        if ch.isascii():
            result.append(ord(ch))
        else:
            result.append(ch)
    return result

def decode_message(codes):
    result = []
    for code in codes:
        result.append(chr(code))
    return "".join(result)

def roundtrip(text):
    encoded = encode_message(text)
    decoded = decode_message(encoded)
    return decoded

def encode_with_header(text, encoding="utf-8"):
    header = "ENC:" + encoding + ":"
    if encoding == "utf-8":
        payload = encode_message(text)
    else:
        payload = list(text)
    return header + str(payload)

def batch_encode(messages):
    results = []
    for msg in messages:
        results.append(encode_message(msg))
    total_size = sum(len(r) for r in results)
    return results, total_size
''',


    "partial_initialization": '''
class DataProcessor:
    def __init__(self, mode):
        self.mode = mode
        if mode == "fast":
            self.cache = {}
        elif mode == "safe":
            self.validator = True

    def process(self, data):
        if self.mode == "fast":
            if data in self.cache:
                return self.cache[data]
            result = data * 2
            self.cache[data] = result
            return result
        result = data * 2
        if self.validator:
            assert result >= 0
        return result

    def get_cache_size(self):
        return len(self.cache)

    def reset(self):
        self.cache.clear()
        self.validator = False
''',

    "wrong_operator_usage": '''
def check_permission(user, resource):
    if user["role"] == "admin":
        return True
    if user["level"] >= resource["min_level"]:
        return True
    return False

def assign_role(user, role):
    user["role"] = role
    if role == "admin":
        user["level"] = 100
    result = user
    result["active"] == True
    return result

def compare_versions(v1, v2):
    parts1 = v1.split(".")
    parts2 = v2.split(".")
    for a, b in zip(parts1, parts2):
        if int(a) > int(b):
            return 1
        if int(a) < int(b):
            return -1
    return 0

def validate_access(users, resource):
    results = []
    for user in users:
        ok = check_permission(user, resource)
        results.append({"user": user["name"], "allowed": ok})
    return results
''',

    "float_equality_compare": '''
def calculate_area(radius):
    pi = 3.14159265358979
    return pi * radius * radius

def check_unit_circle(x, y):
    distance = (x * x + y * y) ** 0.5
    return distance == 1.0

def sum_series(n):
    total = 0.0
    for i in range(1, n + 1):
        total += 1.0 / i
    return total

def check_convergence(series_func, n, expected):
    result = series_func(n)
    return result == expected

def find_root(f, a, b, tol=1e-10):
    while abs(b - a) > tol:
        mid = (a + b) / 2.0
        if f(mid) == 0.0:
            return mid
        if f(a) * f(mid) < 0:
            b = mid
        else:
            a = mid
    return (a + b) / 2.0

def verify_calculation(expected, actual):
    return expected == actual
''',

    "null_dereference_path": '''
def find_user(users, user_id):
    for user in users:
        if user.get("id") == user_id:
            return user
    return None

def get_user_email(users, user_id):
    user = find_user(users, user_id)
    return user["email"]

def get_user_role(users, user_id):
    user = find_user(users, user_id)
    return user.get("role", "guest")

def format_user_info(users, user_id):
    user = find_user(users, user_id)
    name = user["name"]
    email = user["email"]
    return "{} <{}>".format(name, email)

def batch_emails(users, user_ids):
    emails = []
    for uid in user_ids:
        email = get_user_email(users, uid)
        emails.append(email)
    return emails
''',

    "infinite_recursion_path": '''
def tree_depth(node):
    if node is None:
        return 0
    left = tree_depth(node.get("left"))
    right = tree_depth(node.get("right"))
    return 1 + max(left, right)

def flatten_tree(node):
    if node is None:
        return []
    result = [node["value"]]
    result.extend(flatten_tree(node.get("left")))
    result.extend(flatten_tree(node.get("right")))
    return result

def find_in_tree(node, target):
    if node is None:
        return False
    if node["value"] == target:
        return True
    if node.get("parent"):
        return find_in_tree(node["parent"], target)
    return find_in_tree(node.get("left"), target) or find_in_tree(node.get("right"), target)

def count_nodes(node):
    if node is None:
        return 0
    return 1 + count_nodes(node.get("left")) + count_nodes(node.get("right"))
''',

    "concurrent_modification": '''
def remove_negatives(items):
    for item in items:
        if item < 0:
            items.remove(item)
    return items

def deduplicate(items):
    seen = set()
    for item in items:
        if item in seen:
            items.remove(item)
        else:
            seen.add(item)
    return items

def filter_and_transform(items, predicate, transform):
    for i, item in enumerate(items):
        if not predicate(item):
            items.pop(i)
        else:
            items[i] = transform(item)
    return items

def prune_expired(records, cutoff):
    for record in records:
        if record.get("timestamp", 0) < cutoff:
            records.remove(record)
    return records

def batch_update(items, updates):
    for item in items:
        for key, val in updates.items():
            item[key] = val
            if val is None:
                items.remove(item)
    return items
''',

    "string_int_confusion": '''
def add_values(a, b):
    return a + b

def process_form(data):
    name = data.get("name", "")
    age = data.get("age", "0")
    score = data.get("score", "0")
    total = add_values(age, score)
    return {"name": name, "total": total}

def aggregate_scores(records):
    total = 0
    for rec in records:
        total = add_values(total, rec["score"])
    return total

def build_summary(records):
    names = []
    scores = []
    for rec in records:
        names.append(rec["name"])
        scores.append(rec["score"])
    avg = aggregate_scores(records) / len(records)
    return {"names": names, "average": avg}

def validate_input(value):
    if len(value) > 100:
        return False
    return True
''',

    "wrong_default_type": '''
def search(items, key, default=0):
    for item in items:
        if item.get("key") == key:
            return item.get("value")
    return default

def get_config_value(config, path, default=[]):
    parts = path.split(".")
    current = config
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current

def merge_defaults(provided, defaults={}):
    result = defaults
    result.update(provided)
    return result

def initialize_counters(names, initial={}):
    for name in names:
        initial[name] = 0
    return initial

def build_registry(entries, registry={}):
    for entry in entries:
        registry[entry["id"]] = entry
    return registry
''',

    "missing_base_case_edge": '''
def power(base, exp):
    if exp == 0:
        return 1
    return base * power(base, exp - 1)

def sum_digits(n):
    if n < 10:
        return n
    return n % 10 + sum_digits(n // 10)

def ackermann(m, n):
    if m == 0:
        return n + 1
    if n == 0:
        return ackermann(m - 1, 1)
    return ackermann(m - 1, ackermann(m, n - 1))

def collatz_length(n):
    if n == 1:
        return 0
    if n % 2 == 0:
        return 1 + collatz_length(n // 2)
    return 1 + collatz_length(3 * n + 1)

def gcd_recursive(a, b):
    if b == 0:
        return a
    return gcd_recursive(b, a % b)

def list_length(lst):
    return 1 + list_length(lst[1:])
''',

    "inconsistent_state_machine": '''
class OrderProcessor:
    def __init__(self):
        self.state = "new"
        self.items = []
        self.total = 0

    def add_item(self, item, price):
        self.items.append({"item": item, "price": price})
        self.total += price
        return True

    def checkout(self):
        if self.state == "new":
            self.state = "pending"
            return True
        return False

    def pay(self):
        if self.state == "pending":
            self.state = "paid"
            return True
        if self.state == "shipped":
            self.state = "paid"
            return True
        return False

    def ship(self):
        if self.state == "paid":
            self.state = "shipped"
            return True
        return False

    def refund(self):
        if self.state == "paid":
            self.state = "refunded"
            return True
        if self.state == "shipped":
            self.state = "pending"
            return True
        return False

    def get_status(self):
        return {
            "state": self.state,
            "items": len(self.items),
            "total": self.total,
        }
''',

}



# ── main experiment ──────────────────────────────────────────────────────

def extract_h1(prove_objs):
    """Extract H1 value from prove output."""
    for obj in prove_objs:
        fv = obj.get("formal_verification", {})
        ov = fv.get("obstruction_vanishing", {})
        if "H1" in ov:
            return ov["H1"]
    return "?"


def extract_site_complexity(encode_objs):
    """Extract site complexity metrics from encode output."""
    for obj in encode_objs:
        sc = obj.get("site_complexity", {})
        if sc:
            return sc
        if "coordinates" in obj:
            return {"coordinates": obj["coordinates"]}
    return {}


def main():
    print("=" * 76)
    print("PAPER 04 — Cohomological Obstructions: When H¹≠0 Blocks Descent")
    print("  All numbers from `python3 -m jugeo` CLI (subprocess)")
    print("=" * 76)
    print()

    tmpfiles = []
    clean_results = []
    inconsistent_results = []

    # ── Run prove + encode on all 100 clean programs ─────────────────────
    print("Running 100 clean programs ...")
    for idx, (name, source) in enumerate(PROGRAMS.items(), 1):
        path = write_temp(source)
        tmpfiles.append(path)

        t0 = time.perf_counter()
        prove_objs = run_jugeo("prove", path)
        prove_wall = time.perf_counter() - t0

        t0 = time.perf_counter()
        encode_objs = run_jugeo("encode", path)
        encode_wall = time.perf_counter() - t0

        h1 = extract_h1(prove_objs)
        site = extract_site_complexity(encode_objs)

        prove_first = prove_objs[0] if prove_objs else {}
        finfo = (prove_first.get("files") or [{}])[0]

        clean_results.append({
            "name": name,
            "category": "clean",
            "h1": h1,
            "h1_is_zero": (h1 == 0 or h1 == "0"),
            "verdict": finfo.get("verdict", "?"),
            "trust": finfo.get("trust", "?"),
            "coordinates": finfo.get("coordinates", 0),
            "propositions_total": finfo.get("propositions_total", 0),
            "propositions_ok": finfo.get("propositions_ok", 0),
            "n_obstructions": len(finfo.get("obstructions", [])),
            "site_complexity": site,
            "prove_wall_s": round(prove_wall, 4),
            "encode_wall_s": round(encode_wall, 4),
        })

        if idx % 20 == 0 or idx == len(PROGRAMS):
            print(f"  [{idx:>3}/{len(PROGRAMS)}] {name}")

    # ── Run prove + encode on all 30 inconsistent programs ───────────────
    print()
    print("Running 30 inconsistent programs ...")
    for idx, (name, source) in enumerate(INCONSISTENT_PROGRAMS.items(), 1):
        path = write_temp(source)
        tmpfiles.append(path)

        t0 = time.perf_counter()
        prove_objs = run_jugeo("prove", path)
        prove_wall = time.perf_counter() - t0

        t0 = time.perf_counter()
        encode_objs = run_jugeo("encode", path)
        encode_wall = time.perf_counter() - t0

        h1 = extract_h1(prove_objs)
        site = extract_site_complexity(encode_objs)

        prove_first = prove_objs[0] if prove_objs else {}
        finfo = (prove_first.get("files") or [{}])[0]

        inconsistent_results.append({
            "name": name,
            "category": "inconsistent",
            "h1": h1,
            "h1_is_zero": (h1 == 0 or h1 == "0"),
            "verdict": finfo.get("verdict", "?"),
            "trust": finfo.get("trust", "?"),
            "coordinates": finfo.get("coordinates", 0),
            "propositions_total": finfo.get("propositions_total", 0),
            "propositions_ok": finfo.get("propositions_ok", 0),
            "n_obstructions": len(finfo.get("obstructions", [])),
            "site_complexity": site,
            "prove_wall_s": round(prove_wall, 4),
            "encode_wall_s": round(encode_wall, 4),
        })

        if idx % 10 == 0 or idx == len(INCONSISTENT_PROGRAMS):
            print(f"  [{idx:>3}/{len(INCONSISTENT_PROGRAMS)}] {name}")

    all_results = clean_results + inconsistent_results

    # ── H¹ classification ────────────────────────────────────────────────
    print()
    print("=" * 76)
    print("H¹ CLASSIFICATION RESULTS")
    print("=" * 76)

    # True positive:  inconsistent program with H¹≠0
    # True negative:  clean program with H¹=0
    # False positive: clean program with H¹≠0
    # False negative: inconsistent program with H¹=0
    tp = sum(1 for r in inconsistent_results if not r["h1_is_zero"])
    tn = sum(1 for r in clean_results if r["h1_is_zero"])
    fp = sum(1 for r in clean_results if not r["h1_is_zero"])
    fn = sum(1 for r in inconsistent_results if r["h1_is_zero"])

    total = len(all_results)
    accuracy = (tp + tn) / total if total else 0
    precision = tp / (tp + fp) if (tp + fp) else 0
    recall = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    print(f"  Clean programs:        {len(clean_results)}")
    print(f"  Inconsistent programs: {len(inconsistent_results)}")
    print()
    print(f"  True  positives (inconsistent, H¹≠0): {tp}")
    print(f"  True  negatives (clean, H¹=0):         {tn}")
    print(f"  False positives (clean, H¹≠0):         {fp}")
    print(f"  False negatives (inconsistent, H¹=0):  {fn}")
    print()
    print(f"  Accuracy:  {accuracy:.4f}")
    print(f"  Precision: {precision:.4f}")
    print(f"  Recall:    {recall:.4f}")
    print(f"  F1 Score:  {f1:.4f}")

    # ── Per-program detail table ─────────────────────────────────────────
    print()
    print("PER-PROGRAM RESULTS (clean):")
    print("-" * 100)
    print(f"  {'Name':<28} {'H¹':>4} {'Verdict':<10} {'Trust':<18} "
          f"{'Coords':>6} {'Props':>6} {'Obs':>4} {'prove(s)':>9}")
    print(f"  {'-'*94}")
    for r in clean_results:
        print(f"  {r['name']:<28} {str(r['h1']):>4} {r['verdict']:<10} "
              f"{r['trust']:<18} {r['coordinates']:>6} "
              f"{r['propositions_total']:>6} {r['n_obstructions']:>4} "
              f"{r['prove_wall_s']:>9.4f}")

    print()
    print("PER-PROGRAM RESULTS (inconsistent):")
    print("-" * 100)
    print(f"  {'Name':<28} {'H¹':>4} {'Verdict':<10} {'Trust':<18} "
          f"{'Coords':>6} {'Props':>6} {'Obs':>4} {'prove(s)':>9}")
    print(f"  {'-'*94}")
    for r in inconsistent_results:
        mark = "✓" if not r["h1_is_zero"] else "✗"
        print(f"  {r['name']:<28} {str(r['h1']):>4}{mark} {r['verdict']:<10} "
              f"{r['trust']:<18} {r['coordinates']:>6} "
              f"{r['propositions_total']:>6} {r['n_obstructions']:>4} "
              f"{r['prove_wall_s']:>9.4f}")

    # ── Timing statistics ────────────────────────────────────────────────
    print()
    print("TIMING STATISTICS:")
    print("-" * 60)

    prove_times = [r["prove_wall_s"] for r in all_results]
    encode_times = [r["encode_wall_s"] for r in all_results]

    if prove_times:
        prove_mean = statistics.mean(prove_times)
        prove_median = statistics.median(prove_times)
        prove_sorted = sorted(prove_times)
        p95_idx = int(len(prove_sorted) * 0.95)
        prove_p95 = prove_sorted[min(p95_idx, len(prove_sorted) - 1)]
        print(f"  prove  — mean: {prove_mean:.4f}s  median: {prove_median:.4f}s  "
              f"p95: {prove_p95:.4f}s  total: {sum(prove_times):.2f}s")

    if encode_times:
        encode_mean = statistics.mean(encode_times)
        encode_median = statistics.median(encode_times)
        encode_sorted = sorted(encode_times)
        p95_idx = int(len(encode_sorted) * 0.95)
        encode_p95 = encode_sorted[min(p95_idx, len(encode_sorted) - 1)]
        print(f"  encode — mean: {encode_mean:.4f}s  median: {encode_median:.4f}s  "
              f"p95: {encode_p95:.4f}s  total: {sum(encode_times):.2f}s")

    clean_times = [r["prove_wall_s"] for r in clean_results]
    incon_times = [r["prove_wall_s"] for r in inconsistent_results]
    if clean_times:
        print(f"  clean prove mean:        {statistics.mean(clean_times):.4f}s")
    if incon_times:
        print(f"  inconsistent prove mean: {statistics.mean(incon_times):.4f}s")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print("SUMMARY:")
    print(f"  {len(PROGRAMS)} clean programs + {len(INCONSISTENT_PROGRAMS)} "
          f"inconsistent programs = {total} total")
    h1_zero_clean = sum(1 for r in clean_results if r["h1_is_zero"])
    h1_nonzero_incon = sum(1 for r in inconsistent_results if not r["h1_is_zero"])
    print(f"  Clean with H¹=0:         {h1_zero_clean}/{len(clean_results)}")
    print(f"  Inconsistent with H¹≠0:  {h1_nonzero_incon}/{len(inconsistent_results)}")

    # ── Save results ─────────────────────────────────────────────────────
    output = {
        "experiment": "cohomological_obstructions",
        "paper": 4,
        "note": "All JuGeo numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_clean": len(clean_results),
        "n_inconsistent": len(inconsistent_results),
        "n_total": total,
        "classification": {
            "true_positives": tp,
            "true_negatives": tn,
            "false_positives": fp,
            "false_negatives": fn,
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "timing": {
            "prove_mean_s": round(statistics.mean(prove_times), 4) if prove_times else 0,
            "prove_median_s": round(statistics.median(prove_times), 4) if prove_times else 0,
            "encode_mean_s": round(statistics.mean(encode_times), 4) if encode_times else 0,
            "encode_median_s": round(statistics.median(encode_times), 4) if encode_times else 0,
        },
        "clean_results": clean_results,
        "inconsistent_results": inconsistent_results,
    }
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_paper04.json")
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults → {outpath}")

    # ── Cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
