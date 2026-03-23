#!/usr/bin/env python3
"""Paper 1 Experiment — Judgment Geometry: Semantic Site Construction Scaling.

Measures how site complexity (coordinates, morphisms, covers) scales with
program complexity (AST nodes, lines) by running the ``jugeo prove`` and
``jugeo encode`` CLI commands as subprocesses.

Every number is reproducible: run `python3 experiments/exp01_site_scaling.py`.
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
    f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    f.write(source)
    f.close()
    return f.name


# ── test programs of increasing complexity ───────────────────────────────

PROGRAMS = {
    # ── Sorting/Searching ────────────────────────────────────────────────

    "bubble_sort": '''
def bubble_sort(arr):
    """Sort array in-place using bubble sort with early termination."""
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

def run_bubble():
    data = [64, 34, 25, 12, 22, 11, 90]
    result = bubble_sort(list(data))
    assert result == sorted(data)
    empty = bubble_sort([])
    assert empty == []
    single = bubble_sort([1])
    assert single == [1]
    already = bubble_sort([1, 2, 3, 4, 5])
    assert already == [1, 2, 3, 4, 5]
    return result
''',

    "selection_sort": '''

def selection_sort(arr):
    n = len(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
    return arr

def find_kth_smallest(arr, k):
    working = list(arr)
    n = len(working)
    for i in range(min(k, n)):
        min_idx = i
        for j in range(i + 1, n):
            if working[j] < working[min_idx]:
                min_idx = j
        working[i], working[min_idx] = working[min_idx], working[i]
    return working[k - 1] if k <= n else None

''',

    "shell_sort": '''

def shell_sort(arr):
    n = len(arr)
    gap = 1
    while gap < n // 3:
        gap = gap * 3 + 1
    while gap > 0:
        for i in range(gap, n):
            temp = arr[i]
            j = i
            while j >= gap and arr[j - gap] > temp:
                arr[j] = arr[j - gap]
                j -= gap
            arr[j] = temp
        gap //= 3
    return arr

def shell_sort_custom_gaps(arr, gaps):
    arr = list(arr)
    for gap in gaps:
        for i in range(gap, len(arr)):
            temp = arr[i]
            j = i
            while j >= gap and arr[j - gap] > temp:
                arr[j] = arr[j - gap]
                j -= gap
            arr[j] = temp
    return arr

''',

    "bucket_sort": '''

def bucket_sort(arr, num_buckets=10):
    if not arr:
        return arr
    min_val = min(arr)
    max_val = max(arr)
    bucket_range = (max_val - min_val + 1) / num_buckets
    buckets = [[] for _ in range(num_buckets)]
    for val in arr:
        idx = int((val - min_val) / bucket_range)
        if idx == num_buckets:
            idx -= 1
        buckets[idx].append(val)
    result = []
    for bucket in buckets:
        bucket.sort()
        result.extend(bucket)
    return result

def counting_sort(arr, max_val=None):
    if not arr:
        return arr
    if max_val is None:
        max_val = max(arr)
    counts = [0] * (max_val + 1)
    for val in arr:
        counts[val] += 1
    result = []
    for val, count in enumerate(counts):
        result.extend([val] * count)
    return result

''',

    "merge_sort_iterative": '''
def merge_sort_iterative(arr):
    """Bottom-up iterative merge sort."""
    n = len(arr)
    if n <= 1:
        return list(arr)
    result = list(arr)
    width = 1
    while width < n:
        for start in range(0, n, 2 * width):
            mid = min(start + width, n)
            end = min(start + 2 * width, n)
            left = result[start:mid]
            right = result[mid:end]
            merged = []
            i = j = 0
            while i < len(left) and j < len(right):
                if left[i] <= right[j]:
                    merged.append(left[i])
                    i += 1
                else:
                    merged.append(right[j])
                    j += 1
            merged.extend(left[i:])
            merged.extend(right[j:])
            result[start:end] = merged
        width *= 2
    return result

def count_inversions(arr):
    """Count inversions using merge sort approach."""
    if len(arr) <= 1:
        return list(arr), 0
    mid = len(arr) // 2
    left, left_inv = count_inversions(arr[:mid])
    right, right_inv = count_inversions(arr[mid:])
    merged = []
    inversions = left_inv + right_inv
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            inversions += len(left) - i
            j += 1
    merged.extend(left[i:])
    merged.extend(right[j:])
    return merged, inversions
''',

    "binary_search_tree_sort": '''
class BSTNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None

def bst_insert(root, val):
    if root is None:
        return BSTNode(val)
    if val < root.val:
        root.left = bst_insert(root.left, val)
    else:
        root.right = bst_insert(root.right, val)
    return root

def inorder(root, result):
    if root is not None:
        inorder(root.left, result)
        result.append(root.val)
        inorder(root.right, result)

def bst_sort(arr):
    """Sort using binary search tree insertion and inorder traversal."""
    if not arr:
        return []
    root = None
    for val in arr:
        root = bst_insert(root, val)
    result = []
    inorder(root, result)
    return result

def bst_find(root, val):
    if root is None:
        return False
    if val == root.val:
        return True
    elif val < root.val:
        return bst_find(root.left, val)
    else:
        return bst_find(root.right, val)

def test_bst():
    data = [5, 3, 7, 1, 9, 2, 8]
    assert bst_sort(data) == sorted(data)
    root = None
    for v in data:
        root = bst_insert(root, v)
    assert bst_find(root, 7) is True
    assert bst_find(root, 4) is False
''',

    "patience_sort": '''

import bisect

def patience_sort(arr):
    piles = []
    for val in arr:
        pile_tops = [p[-1] for p in piles]
        pos = bisect.bisect_left(pile_tops, val)
        if pos == len(piles):
            piles.append([val])
        else:
            piles[pos].append(val)
    result = []
    while piles:
        min_idx = 0
        for i in range(1, len(piles)):
            if piles[i][-1] < piles[min_idx][-1]:
                min_idx = i
        result.append(piles[min_idx].pop())
        if not piles[min_idx]:
            piles.pop(min_idx)
    return result

def longest_increasing_subsequence_len(arr):
    tails = []
    for val in arr:
        pos = bisect.bisect_left(tails, val)
        if pos == len(tails):
            tails.append(val)
        else:
            tails[pos] = val
    return len(tails)

''',

    "dutch_flag": '''

def dutch_flag_partition(arr, pivot):
    low = 0
    mid = 0
    high = len(arr) - 1
    while mid <= high:
        if arr[mid] < pivot:
            arr[low], arr[mid] = arr[mid], arr[low]
            low += 1
            mid += 1
        elif arr[mid] == pivot:
            mid += 1
        else:
            arr[mid], arr[high] = arr[high], arr[mid]
            high -= 1
    return arr

def sort_colors(arr):
    return dutch_flag_partition(list(arr), 1)

def three_way_count(arr, pivot):
    less = sum(1 for x in arr if x < pivot)
    equal = sum(1 for x in arr if x == pivot)
    greater = sum(1 for x in arr if x > pivot)
    return less, equal, greater

''',

    "three_way_partition": '''

def three_way_partition(arr, lo_pivot, hi_pivot):
    arr = list(arr)
    low = 0
    mid = 0
    high = len(arr) - 1
    while mid <= high:
        if arr[mid] < lo_pivot:
            arr[low], arr[mid] = arr[mid], arr[low]
            low += 1
            mid += 1
        elif arr[mid] > hi_pivot:
            arr[mid], arr[high] = arr[high], arr[mid]
            high -= 1
        else:
            mid += 1
    return arr

def partition_around_median(arr):
    sorted_arr = sorted(arr)
    n = len(sorted_arr)
    lo = sorted_arr[n // 3]
    hi = sorted_arr[2 * n // 3]
    return three_way_partition(arr, lo, hi)

def classify_elements(arr, lo, hi):
    below = [x for x in arr if x < lo]
    between = [x for x in arr if lo <= x <= hi]
    above = [x for x in arr if x > hi]
    return below, between, above

''',

    "external_merge": '''

import heapq

class ExternalMerge:
    def __init__(self, chunk_size=1000):
        self.chunk_size = chunk_size

    def sort_chunks(self, data):
        chunks = []
        for i in range(0, len(data), self.chunk_size):
            chunk = sorted(data[i:i + self.chunk_size])
            chunks.append(chunk)
        return chunks

    def merge_two(self, a, b):
        result = []
        i = j = 0
        while i < len(a) and j < len(b):
            if a[i] <= b[j]:
                result.append(a[i])
                i += 1
            else:
                result.append(b[j])
                j += 1
        result.extend(a[i:])
        result.extend(b[j:])
        return result

    def k_way_merge(self, chunks):
        heap = []
        for ci, chunk in enumerate(chunks):
            if chunk:
                heapq.heappush(heap, (chunk[0], ci, 0))
        result = []
        while heap:
            val, ci, idx = heapq.heappop(heap)
            result.append(val)
            if idx + 1 < len(chunks[ci]):
                heapq.heappush(heap, (chunks[ci][idx + 1], ci, idx + 1))
        return result

    def sort(self, data):
        chunks = self.sort_chunks(data)
        return self.k_way_merge(chunks)

''',

    # ── Data Structures ──────────────────────────────────────────────────

    "doubly_linked_list": '''
class DLLNode:
    def __init__(self, val):
        self.val = val
        self.prev = None
        self.next = None

class DoublyLinkedList:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0

    def append(self, val):
        node = DLLNode(val)
        if self.tail is None:
            self.head = self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        self.size += 1

    def prepend(self, val):
        node = DLLNode(val)
        if self.head is None:
            self.head = self.tail = node
        else:
            node.next = self.head
            self.head.prev = node
            self.head = node
        self.size += 1

    def remove(self, val):
        current = self.head
        while current:
            if current.val == val:
                if current.prev:
                    current.prev.next = current.next
                else:
                    self.head = current.next
                if current.next:
                    current.next.prev = current.prev
                else:
                    self.tail = current.prev
                self.size -= 1
                return True
            current = current.next
        return False

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.val)
            current = current.next
        return result

    def to_list_reverse(self):
        result = []
        current = self.tail
        while current:
            result.append(current.val)
            current = current.prev
        return result

def test_dll():
    dll = DoublyLinkedList()
    for v in [1, 2, 3, 4, 5]:
        dll.append(v)
    dll.prepend(0)
    dll.remove(3)
    assert dll.to_list() == [0, 1, 2, 4, 5]
    assert dll.to_list_reverse() == [5, 4, 2, 1, 0]
''',

    "min_heap": '''
class MinHeap:
    def __init__(self):
        self.data = []

    def _parent(self, i):
        return (i - 1) // 2

    def _left(self, i):
        return 2 * i + 1

    def _right(self, i):
        return 2 * i + 2

    def _swap(self, i, j):
        self.data[i], self.data[j] = self.data[j], self.data[i]

    def push(self, val):
        self.data.append(val)
        self._sift_up(len(self.data) - 1)

    def pop(self):
        if not self.data:
            raise IndexError("heap is empty")
        self._swap(0, len(self.data) - 1)
        val = self.data.pop()
        if self.data:
            self._sift_down(0)
        return val

    def peek(self):
        if not self.data:
            raise IndexError("heap is empty")
        return self.data[0]

    def _sift_up(self, i):
        while i > 0 and self.data[i] < self.data[self._parent(i)]:
            self._swap(i, self._parent(i))
            i = self._parent(i)

    def _sift_down(self, i):
        n = len(self.data)
        while True:
            smallest = i
            left = self._left(i)
            right = self._right(i)
            if left < n and self.data[left] < self.data[smallest]:
                smallest = left
            if right < n and self.data[right] < self.data[smallest]:
                smallest = right
            if smallest == i:
                break
            self._swap(i, smallest)
            i = smallest

    def __len__(self):
        return len(self.data)

def heapsort(arr):
    h = MinHeap()
    for v in arr:
        h.push(v)
    return [h.pop() for _ in range(len(h))]
''',

    "deque_impl": '''

class Deque:
    def __init__(self, capacity=16):
        self.buffer = [None] * capacity
        self.capacity = capacity
        self.head = 0
        self.tail = 0
        self.size = 0

    def _resize(self):
        new_cap = self.capacity * 2
        new_buf = [None] * new_cap
        for i in range(self.size):
            new_buf[i] = self.buffer[(self.head + i) % self.capacity]
        self.buffer = new_buf
        self.head = 0
        self.tail = self.size
        self.capacity = new_cap

    def push_back(self, val):
        if self.size == self.capacity:
            self._resize()
        self.buffer[self.tail] = val
        self.tail = (self.tail + 1) % self.capacity
        self.size += 1

    def push_front(self, val):
        if self.size == self.capacity:
            self._resize()
        self.head = (self.head - 1) % self.capacity
        self.buffer[self.head] = val
        self.size += 1

    def pop_back(self):
        if self.size == 0:
            raise IndexError("deque is empty")
        self.tail = (self.tail - 1) % self.capacity
        val = self.buffer[self.tail]
        self.size -= 1
        return val

    def pop_front(self):
        if self.size == 0:
            raise IndexError("deque is empty")
        val = self.buffer[self.head]
        self.head = (self.head + 1) % self.capacity
        self.size -= 1
        return val

''',

    "avl_tree": '''
class AVLNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None
        self.height = 1

def avl_height(node):
    return node.height if node else 0

def avl_balance(node):
    return avl_height(node.left) - avl_height(node.right) if node else 0

def avl_rotate_right(y):
    x = y.left
    t2 = x.right
    x.right = y
    y.left = t2
    y.height = 1 + max(avl_height(y.left), avl_height(y.right))
    x.height = 1 + max(avl_height(x.left), avl_height(x.right))
    return x

def avl_rotate_left(x):
    y = x.right
    t2 = y.left
    y.left = x
    x.right = t2
    x.height = 1 + max(avl_height(x.left), avl_height(x.right))
    y.height = 1 + max(avl_height(y.left), avl_height(y.right))
    return y

def avl_insert(root, key):
    if not root:
        return AVLNode(key)
    if key < root.key:
        root.left = avl_insert(root.left, key)
    elif key > root.key:
        root.right = avl_insert(root.right, key)
    else:
        return root
    root.height = 1 + max(avl_height(root.left), avl_height(root.right))
    bal = avl_balance(root)
    if bal > 1 and key < root.left.key:
        return avl_rotate_right(root)
    if bal < -1 and key > root.right.key:
        return avl_rotate_left(root)
    if bal > 1 and key > root.left.key:
        root.left = avl_rotate_left(root.left)
        return avl_rotate_right(root)
    if bal < -1 and key < root.right.key:
        root.right = avl_rotate_right(root.right)
        return avl_rotate_left(root)
    return root

def avl_inorder(root):
    if not root:
        return []
    return avl_inorder(root.left) + [root.key] + avl_inorder(root.right)

def test_avl():
    root = None
    for v in [10, 20, 30, 40, 50, 25]:
        root = avl_insert(root, v)
    assert avl_inorder(root) == [10, 20, 25, 30, 40, 50]
    assert abs(avl_balance(root)) <= 1
''',

    "bloom_filter": '''
class BloomFilter:
    """Probabilistic set membership with configurable false positive rate."""

    def __init__(self, size=1024, num_hashes=3):
        self.size = size
        self.num_hashes = num_hashes
        self.bits = [False] * size
        self.count = 0

    def _hashes(self, item):
        """Generate hash positions using double hashing."""
        h1 = hash(item) % self.size
        h2 = (hash(item + "__salt") % (self.size - 1)) + 1
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, item):
        for pos in self._hashes(str(item)):
            self.bits[pos] = True
        self.count += 1

    def might_contain(self, item):
        return all(self.bits[pos] for pos in self._hashes(str(item)))

    def false_positive_rate(self):
        set_bits = sum(self.bits)
        if set_bits == 0:
            return 0.0
        return (set_bits / self.size) ** self.num_hashes

    def merge(self, other):
        """Merge another bloom filter into this one."""
        assert self.size == other.size
        for i in range(self.size):
            self.bits[i] = self.bits[i] or other.bits[i]

def test_bloom():
    bf = BloomFilter(size=2048, num_hashes=5)
    for i in range(100):
        bf.add(i)
    for i in range(100):
        assert bf.might_contain(i)
    fp_count = sum(1 for i in range(1000, 2000) if bf.might_contain(i))
    assert fp_count < 200
''',

    "skip_list": '''

import random

class SkipNode:
    def __init__(self, key, level):
        self.key = key
        self.forward = [None] * (level + 1)

class SkipList:
    def __init__(self, max_level=16, p=0.5):
        self.max_level = max_level
        self.p = p
        self.level = 0
        self.header = SkipNode(-1, max_level)

    def _random_level(self):
        lvl = 0
        while random.random() < self.p and lvl < self.max_level:
            lvl += 1
        return lvl

    def insert(self, key):
        update = [None] * (self.max_level + 1)
        current = self.header
        for i in range(self.level, -1, -1):
            while current.forward[i] and current.forward[i].key < key:
                current = current.forward[i]
            update[i] = current
        new_level = self._random_level()
        if new_level > self.level:
            for i in range(self.level + 1, new_level + 1):
                update[i] = self.header
            self.level = new_level
        node = SkipNode(key, new_level)
        for i in range(new_level + 1):
            node.forward[i] = update[i].forward[i]
            update[i].forward[i] = node

    def search(self, key):
        current = self.header
        for i in range(self.level, -1, -1):
            while current.forward[i] and current.forward[i].key < key:
                current = current.forward[i]
        current = current.forward[0]
        return current is not None and current.key == key

''',

    "graph_adjacency": '''
class Graph:
    """Directed graph using adjacency lists."""

    def __init__(self):
        self.adj = {}

    def add_vertex(self, v):
        if v not in self.adj:
            self.adj[v] = []

    def add_edge(self, u, v, weight=1):
        self.add_vertex(u)
        self.add_vertex(v)
        self.adj[u].append((v, weight))

    def neighbors(self, v):
        return self.adj.get(v, [])

    def bfs(self, start):
        visited = set()
        queue = [start]
        order = []
        visited.add(start)
        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor, _ in self.neighbors(node):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return order

    def dfs(self, start, visited=None):
        if visited is None:
            visited = set()
        visited.add(start)
        result = [start]
        for neighbor, _ in self.neighbors(start):
            if neighbor not in visited:
                result.extend(self.dfs(neighbor, visited))
        return result

    def has_cycle(self):
        visited = set()
        rec_stack = set()
        def _dfs(v):
            visited.add(v)
            rec_stack.add(v)
            for neighbor, _ in self.neighbors(v):
                if neighbor not in visited:
                    if _dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(v)
            return False
        for v in self.adj:
            if v not in visited:
                if _dfs(v):
                    return True
        return False
''',

    "segment_tree": '''
class SegmentTree:
    """Segment tree for range sum queries with point updates."""

    def __init__(self, data):
        self.n = len(data)
        self.tree = [0] * (4 * self.n)
        if self.n > 0:
            self._build(data, 1, 0, self.n - 1)

    def _build(self, data, node, start, end):
        if start == end:
            self.tree[node] = data[start]
        else:
            mid = (start + end) // 2
            self._build(data, 2 * node, start, mid)
            self._build(data, 2 * node + 1, mid + 1, end)
            self.tree[node] = self.tree[2 * node] + self.tree[2 * node + 1]

    def update(self, idx, val, node=1, start=0, end=None):
        if end is None:
            end = self.n - 1
        if start == end:
            self.tree[node] = val
        else:
            mid = (start + end) // 2
            if idx <= mid:
                self.update(idx, val, 2 * node, start, mid)
            else:
                self.update(idx, val, 2 * node + 1, mid + 1, end)
            self.tree[node] = self.tree[2 * node] + self.tree[2 * node + 1]

    def query(self, l, r, node=1, start=0, end=None):
        if end is None:
            end = self.n - 1
        if r < start or end < l:
            return 0
        if l <= start and end <= r:
            return self.tree[node]
        mid = (start + end) // 2
        left_sum = self.query(l, r, 2 * node, start, mid)
        right_sum = self.query(l, r, 2 * node + 1, mid + 1, end)
        return left_sum + right_sum

def test_segment_tree():
    st = SegmentTree([1, 3, 5, 7, 9, 11])
    assert st.query(1, 3) == 15
    st.update(1, 10)
    assert st.query(1, 3) == 22
    assert st.query(0, 5) == 43
''',

    "fenwick_tree": '''

class FenwickTree:
    def __init__(self, n):
        self.n = n
        self.tree = [0] * (n + 1)

    def update(self, i, delta):
        i += 1
        while i <= self.n:
            self.tree[i] += delta
            i += i & (-i)

    def query(self, i):
        i += 1
        total = 0
        while i > 0:
            total += self.tree[i]
            i -= i & (-i)
        return total

    def range_query(self, l, r):
        if l == 0:
            return self.query(r)
        return self.query(r) - self.query(l - 1)

    @classmethod
    def from_array(cls, arr):
        ft = cls(len(arr))
        for i, val in enumerate(arr):
            ft.update(i, val)
        return ft

''',

    "persistent_stack": '''

class _Node:
    def __init__(self, value, next_node=None):
        self.value = value
        self.next = next_node

class PersistentStack:
    def __init__(self, top=None, size=0):
        self._top = top
        self._size = size

    def push(self, value):
        new_top = _Node(value, self._top)
        return PersistentStack(new_top, self._size + 1)

    def pop(self):
        if self._top is None:
            raise IndexError("pop from empty stack")
        return self._top.value, PersistentStack(self._top.next, self._size - 1)

    def peek(self):
        if self._top is None:
            raise IndexError("peek at empty stack")
        return self._top.value

    def is_empty(self):
        return self._top is None

    def __len__(self):
        return self._size

''',

    # ── String Processing ────────────────────────────────────────────────

    "regex_engine": '''
class RegexMatcher:
    """Simple regex engine supporting . * + ? and literal chars."""

    def __init__(self, pattern):
        self.pattern = pattern
        self.pos = 0

    def match(self, text):
        return self._match_here(self.pattern, text, 0)

    def _match_here(self, pat, text, ti):
        if not pat:
            return True
        if len(pat) >= 2 and pat[1] == "*":
            return self._match_star(pat[0], pat[2:], text, ti)
        if len(pat) >= 2 and pat[1] == "?":
            if ti < len(text) and self._char_match(pat[0], text[ti]):
                if self._match_here(pat[2:], text, ti + 1):
                    return True
            return self._match_here(pat[2:], text, ti)
        if len(pat) >= 2 and pat[1] == "+":
            if ti < len(text) and self._char_match(pat[0], text[ti]):
                return self._match_star(pat[0], pat[2:], text, ti + 1)
            return False
        if pat[0] == "$" and len(pat) == 1:
            return ti == len(text)
        if ti < len(text) and self._char_match(pat[0], text[ti]):
            return self._match_here(pat[1:], text, ti + 1)
        return False

    def _match_star(self, ch, rest, text, ti):
        while True:
            if self._match_here(rest, text, ti):
                return True
            if ti >= len(text) or not self._char_match(ch, text[ti]):
                break
            ti += 1
        return False

    def _char_match(self, pat_ch, text_ch):
        return pat_ch == "." or pat_ch == text_ch

def test_regex():
    assert RegexMatcher("a*b").match("aaab")
    assert RegexMatcher("a.c").match("abc")
    assert not RegexMatcher("a.c").match("abbc")
    assert RegexMatcher("x+y").match("xxxy")
    assert RegexMatcher("a?b").match("b")
''',

    "html_parser": '''

class HtmlNode:
    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.text = ""

def parse_html(source):
    nodes = []
    stack = []
    i = 0
    n = len(source)
    while i < n:
        if source[i] == "<":
            end = source.find(">", i)
            if end == -1:
                break
            tag_content = source[i+1:end].strip()
            if tag_content.startswith("/"):
                if stack:
                    stack.pop()
            else:
                parts = tag_content.split(None, 1)
                tag_name = parts[0].rstrip("/")
                attrs = {}
                if len(parts) > 1:
                    attr_str = parts[1].rstrip("/")
                    for attr in attr_str.split():
                        if "=" in attr:
                            k, v = attr.split("=", 1)
                            attrs[k] = v.strip('"').strip("'")
                node = HtmlNode(tag_name, attrs)
                if stack:
                    stack[-1].children.append(node)
                else:
                    nodes.append(node)
                if not tag_content.endswith("/"):
                    stack.append(node)
            i = end + 1
        else:
            text_end = source.find("<", i)
            if text_end == -1:
                text_end = n
            text = source[i:text_end].strip()
            if text and stack:
                stack[-1].text += text
            i = text_end
    return nodes

''',

    "xml_tokenizer": '''

class XmlToken:
    OPEN_TAG = "open"
    CLOSE_TAG = "close"
    TEXT = "text"
    SELF_CLOSE = "self_close"

    def __init__(self, token_type, value, attrs=None):
        self.type = token_type
        self.value = value
        self.attrs = attrs or {}

def tokenize_xml(source):
    tokens = []
    i = 0
    n = len(source)
    while i < n:
        if source[i] == "<":
            end = source.find(">", i)
            if end == -1:
                break
            content = source[i+1:end].strip()
            if content.startswith("/"):
                tokens.append(XmlToken(XmlToken.CLOSE_TAG, content[1:].strip()))
            elif content.endswith("/"):
                parts = content[:-1].strip().split(None, 1)
                attrs = _parse_attrs(parts[1]) if len(parts) > 1 else {}
                tokens.append(XmlToken(XmlToken.SELF_CLOSE, parts[0], attrs))
            else:
                parts = content.split(None, 1)
                attrs = _parse_attrs(parts[1]) if len(parts) > 1 else {}
                tokens.append(XmlToken(XmlToken.OPEN_TAG, parts[0], attrs))
            i = end + 1
        else:
            text_end = source.find("<", i)
            if text_end == -1:
                text_end = n
            text = source[i:text_end].strip()
            if text:
                tokens.append(XmlToken(XmlToken.TEXT, text))
            i = text_end
    return tokens

def _parse_attrs(attr_str):
    attrs = {}
    for part in attr_str.split():
        if "=" in part:
            k, v = part.split("=", 1)
            attrs[k] = v.strip('"').strip("'")
    return attrs

''',

    "levenshtein": '''

def levenshtein(s, t):
    m, n = len(s), len(t)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if s[i-1] == t[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j-1])
            prev = temp
    return dp[n]

def edit_operations(s, t):
    m, n = len(s), len(t)
    dp = [[0]*(n+1) for _ in range(m+1)]
    for i in range(m+1):
        dp[i][0] = i
    for j in range(n+1):
        dp[0][j] = j
    for i in range(1, m+1):
        for j in range(1, n+1):
            if s[i-1] == t[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j-1], dp[i-1][j], dp[i][j-1])
    return dp[m][n]

''',

    "longest_common_substr": '''

def longest_common_substring(s1, s2):
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    max_len = 0
    end_pos = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
                if dp[i][j] > max_len:
                    max_len = dp[i][j]
                    end_pos = i
    return s1[end_pos - max_len:end_pos]

def all_common_substrings(s1, s2, min_len=2):
    found = set()
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
                if dp[i][j] >= min_len:
                    found.add(s1[i - dp[i][j]:i])
    return sorted(found, key=len, reverse=True)

''',

    "suffix_array": '''

def build_suffix_array(text):
    n = len(text)
    suffixes = [(text[i:], i) for i in range(n)]
    suffixes.sort()
    return [idx for _, idx in suffixes]
def build_lcp_array(text, sa):
    n = len(text)
    rank = [0] * n
    for i, s in enumerate(sa):
        rank[s] = i
    lcp = [0] * n
    k = 0
    for i in range(n):
        if rank[i] == 0:
            k = 0
            continue
        j = sa[rank[i] - 1]
        while i + k < n and j + k < n and text[i + k] == text[j + k]:
            k += 1
        lcp[rank[i]] = k
        if k > 0:
            k -= 1
    return lcp
def search_pattern(text, pattern, sa):
    lo, hi = 0, len(sa) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        suffix = text[sa[mid]:sa[mid] + len(pattern)]
        if suffix < pattern:
            lo = mid + 1
        elif suffix > pattern:
            hi = mid - 1
        else:
            return sa[mid]
    return -1

''',

    "string_compression": '''

def rle_encode(data):
    if not data:
        return ""
    result = []
    count = 1
    for i in range(1, len(data)):
        if data[i] == data[i-1]:
            count += 1
        else:
            result.append("{}{}".format(count, data[i-1]))
            count = 1
    result.append("{}{}".format(count, data[-1]))
    return "".join(result)
def rle_decode(encoded):
    result = []
    i = 0
    while i < len(encoded):
        num = ""
        while i < len(encoded) and encoded[i].isdigit():
            num += encoded[i]
            i += 1
        if i < len(encoded):
            result.append(encoded[i] * int(num))
            i += 1
    return "".join(result)

''',

    "bracket_validator": '''

def validate_brackets(expr):
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for ch in expr:
        if ch in pairs:
            stack.append(ch)
        elif ch in pairs.values():
            if not stack:
                return False
            top = stack.pop()
            if pairs[top] != ch:
                return False
    return len(stack) == 0

def min_brackets_to_remove(expr):
    stack = []
    remove = set()
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    for i, ch in enumerate(expr):
        if ch in pairs:
            stack.append(i)
        elif ch in closers:
            if stack:
                stack.pop()
            else:
                remove.add(i)
    remove.update(stack)
    return len(remove)

''',

    "roman_numerals": '''

def to_roman(num):
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),
            (100,"C"),(90,"XC"),(50,"L"),(40,"XL"),
            (10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    result = []
    for value, symbol in vals:
        while num >= value:
            result.append(symbol)
            num -= value
    return "".join(result)

def from_roman(s):
    roman_map = {"I":1,"V":5,"X":10,"L":50,"C":100,"D":500,"M":1000}
    total = 0
    prev = 0
    for ch in reversed(s):
        val = roman_map[ch]
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total

''',

    "word_frequency": '''

import re

def word_frequency(text):
    words = re.findall(r"[a-zA-Z]+", text.lower())
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return freq

def top_n_words(text, n=10):
    freq = word_frequency(text)
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return ranked[:n]

def tf_idf(docs):
    import math
    df = {}
    all_tf = []
    for doc in docs:
        tf = word_frequency(doc)
        total = sum(tf.values())
        tf = {w: c / total for w, c in tf.items()}
        all_tf.append(tf)
        for w in tf:
            df[w] = df.get(w, 0) + 1
    n_docs = len(docs)
    results = []
    for tf in all_tf:
        tfidf = {}
        for w, freq in tf.items():
            tfidf[w] = freq * math.log(n_docs / df[w])
        results.append(tfidf)
    return results

''',

    # ── Math/Numeric ─────────────────────────────────────────────────────

    "matrix_inverse": '''
def matrix_multiply(a, b):
    """Multiply two matrices."""
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result

def matrix_inverse(mat):
    """Compute inverse using Gauss-Jordan elimination."""
    n = len(mat)
    augmented = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
                 for i, row in enumerate(mat)]
    for col in range(n):
        max_row = col
        for row in range(col + 1, n):
            if abs(augmented[row][col]) > abs(augmented[max_row][col]):
                max_row = row
        augmented[col], augmented[max_row] = augmented[max_row], augmented[col]
        pivot = augmented[col][col]
        if abs(pivot) < 1e-12:
            raise ValueError("matrix is singular")
        for j in range(2 * n):
            augmented[col][j] /= pivot
        for row in range(n):
            if row != col:
                factor = augmented[row][col]
                for j in range(2 * n):
                    augmented[row][j] -= factor * augmented[col][j]
    return [row[n:] for row in augmented]

def identity_matrix(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
''',

    "eigenvalue_power": '''

import math

def power_iteration(matrix, num_iter=100, tol=1e-10):
    n = len(matrix)
    b = [1.0 / math.sqrt(n)] * n
    eigenvalue = 0.0
    for _ in range(num_iter):
        ab = [sum(matrix[i][j] * b[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x * x for x in ab))
        new_b = [x / norm for x in ab]
        new_eigenvalue = sum(ab[i] * b[i] for i in range(n))
        if abs(new_eigenvalue - eigenvalue) < tol:
            return new_eigenvalue, new_b
        eigenvalue = new_eigenvalue
        b = new_b
    return eigenvalue, b

def rayleigh_quotient(matrix, v):
    n = len(matrix)
    av = [sum(matrix[i][j] * v[j] for j in range(n)) for i in range(n)]
    vav = sum(v[i] * av[i] for i in range(n))
    vv = sum(v[i] * v[i] for i in range(n))
    return vav / vv

''',

    "fft_radix2": '''
import cmath

def fft(x):
    """Cooley-Tukey radix-2 FFT."""
    n = len(x)
    if n <= 1:
        return x
    if n % 2 != 0:
        raise ValueError("length must be power of 2")
    even = fft(x[0::2])
    odd = fft(x[1::2])
    result = [0] * n
    for k in range(n // 2):
        w = cmath.exp(-2j * cmath.pi * k / n)
        result[k] = even[k] + w * odd[k]
        result[k + n // 2] = even[k] - w * odd[k]
    return result

def ifft(x):
    """Inverse FFT."""
    n = len(x)
    conjugated = [v.conjugate() for v in x]
    result = fft(conjugated)
    return [v.conjugate() / n for v in result]

def polynomial_multiply(a, b):
    """Multiply two polynomials using FFT."""
    n = 1
    while n < len(a) + len(b):
        n *= 2
    fa = [complex(v) for v in a] + [0] * (n - len(a))
    fb = [complex(v) for v in b] + [0] * (n - len(b))
    fa = fft(fa)
    fb = fft(fb)
    fc = [fa[i] * fb[i] for i in range(n)]
    result = ifft(fc)
    return [round(v.real) for v in result[:len(a) + len(b) - 1]]
''',

    "big_integer": '''
class BigInt:
    """Arbitrary precision integer using digit arrays."""

    def __init__(self, value="0"):
        self.negative = value.startswith("-")
        digits_str = value.lstrip("-").lstrip("0") or "0"
        self.digits = [int(d) for d in digits_str]

    def __str__(self):
        prefix = "-" if self.negative and self.digits != [0] else ""
        return prefix + "".join(str(d) for d in self.digits)

    def _compare_abs(self, other):
        if len(self.digits) != len(other.digits):
            return 1 if len(self.digits) > len(other.digits) else -1
        for a, b in zip(self.digits, other.digits):
            if a != b:
                return 1 if a > b else -1
        return 0

    def add_abs(self, other):
        a = list(reversed(self.digits))
        b = list(reversed(other.digits))
        n = max(len(a), len(b))
        result = []
        carry = 0
        for i in range(n):
            da = a[i] if i < len(a) else 0
            db = b[i] if i < len(b) else 0
            total = da + db + carry
            result.append(total % 10)
            carry = total // 10
        if carry:
            result.append(carry)
        return list(reversed(result))

    def sub_abs(self, other):
        a = list(reversed(self.digits))
        b = list(reversed(other.digits))
        result = []
        borrow = 0
        for i in range(len(a)):
            da = a[i]
            db = b[i] if i < len(b) else 0
            diff = da - db - borrow
            if diff < 0:
                diff += 10
                borrow = 1
            else:
                borrow = 0
            result.append(diff)
        while len(result) > 1 and result[-1] == 0:
            result.pop()
        return list(reversed(result))

    def multiply(self, other):
        a = list(reversed(self.digits))
        b = list(reversed(other.digits))
        result = [0] * (len(a) + len(b))
        for i in range(len(a)):
            for j in range(len(b)):
                result[i + j] += a[i] * b[j]
                result[i + j + 1] += result[i + j] // 10
                result[i + j] %= 10
        while len(result) > 1 and result[-1] == 0:
            result.pop()
        return list(reversed(result))
''',

    "rational_arithmetic": '''

import math

class Rational:
    def __init__(self, num, den=1):
        if den == 0:
            raise ZeroDivisionError("denominator is zero")
        g = math.gcd(abs(num), abs(den))
        sign = -1 if (num < 0) != (den < 0) else 1
        self.num = sign * abs(num) // g
        self.den = abs(den) // g

    def __add__(self, other):
        return Rational(self.num * other.den + other.num * self.den, self.den * other.den)

    def __sub__(self, other):
        return Rational(self.num * other.den - other.num * self.den, self.den * other.den)

    def __mul__(self, other):
        return Rational(self.num * other.num, self.den * other.den)

    def __truediv__(self, other):
        if other.num == 0:
            raise ZeroDivisionError("division by zero")
        return Rational(self.num * other.den, self.den * other.num)

    def __eq__(self, other):
        return self.num == other.num and self.den == other.den

    def __repr__(self):
        if self.den == 1:
            return str(self.num)
        return "{}/{}".format(self.num, self.den)

    def to_float(self):
        return self.num / self.den

''',

    "continued_fraction": '''

def to_continued_fraction(numerator, denominator, max_terms=20):
    terms = []
    for _ in range(max_terms):
        if denominator == 0:
            break
        q = numerator // denominator
        terms.append(q)
        numerator, denominator = denominator, numerator - q * denominator
    return terms

def from_continued_fraction(terms):
    if not terms:
        return 0, 1
    n, d = terms[-1], 1
    for coeff in reversed(terms[:-1]):
        n, d = coeff * n + d, n
    return n, d

def convergents(terms):
    result = []
    for i in range(1, len(terms) + 1):
        n, d = from_continued_fraction(terms[:i])
        result.append((n, d))
    return result

''',

    "chinese_remainder": '''

def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = extended_gcd(b % a, a)
    return g, y - (b // a) * x, x

def chinese_remainder(remainders, moduli):
    if len(remainders) != len(moduli):
        raise ValueError("length mismatch")
    M = 1
    for m in moduli:
        M *= m
    result = 0
    for r, m in zip(remainders, moduli):
        Mi = M // m
        _, inv, _ = extended_gcd(Mi % m, m)
        result += r * Mi * inv
    return result % M

def solve_system(equations):
    remainders = [r for r, _ in equations]
    moduli = [m for _, m in equations]
    return chinese_remainder(remainders, moduli)

''',

    "modular_exp": '''

def mod_pow(base, exp, mod):
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp = exp >> 1
        base = (base * base) % mod
    return result

def mod_inverse(a, m):
    g, x, _ = _ext_gcd(a, m)
    if g != 1:
        raise ValueError("inverse does not exist")
    return x % m

def _ext_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = _ext_gcd(b % a, a)
    return g, y - (b // a) * x, x

''',

    "sieve_linear": '''

def linear_sieve(limit):
    is_prime = [True] * (limit + 1)
    primes = []
    spf = [0] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, limit + 1):
        if is_prime[i]:
            primes.append(i)
            spf[i] = i
        for p in primes:
            if p > spf[i] or i * p > limit:
                break
            is_prime[i * p] = False
            spf[i * p] = p
    return primes, spf
def factorize(n, spf):
    factors = {}
    while n > 1:
        p = spf[n]
        count = 0
        while n % p == 0:
            n //= p
            count += 1
        factors[p] = count
    return factors

''',

    "ntt_simple": '''

def ntt(a, mod, g, invert=False):
    n = len(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        w = pow(g, (mod - 1) // length, mod)
        if invert:
            w = pow(w, mod - 2, mod)
        for i in range(0, n, length):
            wn = 1
            for k in range(length // 2):
                u = a[i + k]
                v = a[i + k + length // 2] * wn % mod
                a[i + k] = (u + v) % mod
                a[i + k + length // 2] = (u - v) % mod
                wn = wn * w % mod
        length <<= 1
    if invert:
        inv_n = pow(n, mod - 2, mod)
        for i in range(n):
            a[i] = a[i] * inv_n % mod
    return a

''',

    # ── File/IO Simulation ───────────────────────────────────────────────

    "ini_writer": '''

class IniWriter:
    def __init__(self):
        self.sections = {}
        self.order = []

    def add_section(self, name):
        if name not in self.sections:
            self.sections[name] = {}
            self.order.append(name)

    def set(self, section, key, value):
        if section not in self.sections:
            self.add_section(section)
        self.sections[section][key] = value

    def get(self, section, key, default=None):
        return self.sections.get(section, {}).get(key, default)

    def render(self):
        lines = []
        for section in self.order:
            lines.append("[{}]".format(section))
            for key, val in self.sections[section].items():
                lines.append("{} = {}".format(key, val))
            lines.append("")
        return "\\n".join(lines)

    def remove_section(self, name):
        if name in self.sections:
            del self.sections[name]
            self.order.remove(name)

''',

    "log_rotator": '''

import os
import time

class LogRotator:
    def __init__(self, base_path, max_size=1048576, max_files=5):
        self.base_path = base_path
        self.max_size = max_size
        self.max_files = max_files
        self.current_size = 0

    def _rotate(self):
        for i in range(self.max_files - 1, 0, -1):
            src = "{}.{}".format(self.base_path, i)
            dst = "{}.{}".format(self.base_path, i + 1)
            if os.path.exists(src):
                os.rename(src, dst)
        if os.path.exists(self.base_path):
            os.rename(self.base_path, self.base_path + ".1")
        self.current_size = 0

    def write(self, message):
        line = message + "\\n"
        if self.current_size + len(line) > self.max_size:
            self._rotate()
        self.current_size += len(line)
        return line

    def should_rotate(self):
        return self.current_size >= self.max_size

''',

    "csv_dialect": '''

class CsvDialect:
    def __init__(self, delimiter=",", quote_char='"', escape_char=None):
        self.delimiter = delimiter
        self.quote_char = quote_char
        self.escape_char = escape_char

    def parse_row(self, line):
        fields = []
        current = []
        in_quotes = False
        i = 0
        while i < len(line):
            ch = line[i]
            if in_quotes:
                if ch == self.quote_char:
                    if i + 1 < len(line) and line[i + 1] == self.quote_char:
                        current.append(ch)
                        i += 1
                    else:
                        in_quotes = False
                else:
                    current.append(ch)
            else:
                if ch == self.quote_char:
                    in_quotes = True
                elif ch == self.delimiter:
                    fields.append("".join(current))
                    current = []
                else:
                    current.append(ch)
            i += 1
        fields.append("".join(current))
        return fields

''',

    "path_matcher": '''

def match_pattern(pattern, path):
    return _match(pattern, 0, path, 0)

def _match(pattern, pi, path, si):
    while pi < len(pattern) and si < len(path):
        if pattern[pi] == "*":
            if pi + 1 < len(pattern) and pattern[pi + 1] == "*":
                pi += 2
                if pi >= len(pattern):
                    return True
                for i in range(si, len(path) + 1):
                    if _match(pattern, pi, path, i):
                        return True
                return False
            else:
                pi += 1
                while si < len(path) and path[si] != "/":
                    si += 1
        elif pattern[pi] == "?":
            if path[si] == "/":
                return False
            pi += 1
            si += 1
        elif pattern[pi] == path[si]:
            pi += 1
            si += 1
        else:
            return False
    while pi < len(pattern) and pattern[pi] == "*":
        pi += 1
    return pi >= len(pattern) and si >= len(path)

def filter_paths(patterns, paths):
    return [p for p in paths if any(match_pattern(pat, p) for pat in patterns)]

''',

    "file_differ": '''
def compute_diff(lines_a, lines_b):
    """Compute unified diff between two sequences of lines."""
    m, n = len(lines_a), len(lines_b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if lines_a[i - 1] == lines_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = []
    i, j = m, n
    while i > 0 and j > 0:
        if lines_a[i - 1] == lines_b[j - 1]:
            lcs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    lcs.reverse()
    hunks = []
    ai = bi = 0
    for la, lb in lcs:
        while ai < la:
            hunks.append(("-", lines_a[ai]))
            ai += 1
        while bi < lb:
            hunks.append(("+", lines_b[bi]))
            bi += 1
        hunks.append((" ", lines_a[ai]))
        ai += 1
        bi += 1
    while ai < m:
        hunks.append(("-", lines_a[ai]))
        ai += 1
    while bi < n:
        hunks.append(("+", lines_b[bi]))
        bi += 1
    return hunks

def format_diff(hunks):
    """Format diff hunks as unified diff output."""
    return "\\n".join("{}{}".format(tag, line) for tag, line in hunks)
''',

    "archive_builder": '''
class TarEntry:
    def __init__(self, name, content, mode=0o644):
        self.name = name
        self.content = content
        self.mode = mode
        self.size = len(content.encode("utf-8"))

class ArchiveBuilder:
    """Simulate building a tar-like archive."""

    def __init__(self):
        self.entries = []
        self.index = {}

    def add_file(self, name, content, mode=0o644):
        entry = TarEntry(name, content, mode)
        self.entries.append(entry)
        self.index[name] = len(self.entries) - 1

    def add_directory(self, name):
        entry = TarEntry(name + "/", "", mode=0o755)
        self.entries.append(entry)
        self.index[name + "/"] = len(self.entries) - 1

    def list_files(self):
        return [e.name for e in self.entries]

    def get_file(self, name):
        idx = self.index.get(name)
        if idx is None:
            return None
        return self.entries[idx].content

    def total_size(self):
        return sum(e.size for e in self.entries)

    def serialize(self):
        header_lines = []
        data_parts = []
        offset = 0
        for entry in self.entries:
            header_lines.append("{}|{}|{:o}|{}".format(
                entry.name, entry.size, entry.mode, offset))
            data_parts.append(entry.content)
            offset += entry.size
        header = "\\n".join(header_lines) + "\\n---\\n"
        return header + "".join(data_parts)
''',

    "line_index": '''

class LineIndex:
    def __init__(self, text):
        self.text = text
        self.offsets = [0]
        for i, ch in enumerate(text):
            if ch == "\\n":
                self.offsets.append(i + 1)

    def line_count(self):
        return len(self.offsets)

    def get_line(self, line_num):
        if line_num < 0 or line_num >= len(self.offsets):
            return None
        start = self.offsets[line_num]
        if line_num + 1 < len(self.offsets):
            end = self.offsets[line_num + 1] - 1
        else:
            end = len(self.text)
        return self.text[start:end]

    def offset_to_line(self, offset):
        lo, hi = 0, len(self.offsets) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.offsets[mid] <= offset:
                lo = mid + 1
            else:
                hi = mid - 1
        return hi

    def line_to_offset(self, line_num):
        if 0 <= line_num < len(self.offsets):
            return self.offsets[line_num]
        return -1

''',

    "crc32_calc": '''

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
        crc = CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF

def crc32_str(s):
    return crc32(s.encode("utf-8"))

''',

    "dotenv_writer": '''

class DotenvFile:
    def __init__(self):
        self.entries = {}
        self.order = []

    def set(self, key, value):
        if key not in self.entries:
            self.order.append(key)
        self.entries[key] = value

    def get(self, key, default=None):
        return self.entries.get(key, default)

    def render(self):
        lines = []
        for key in self.order:
            val = self.entries[key]
            if " " in str(val) or '"' in str(val):
                val = '"{}"'.format(str(val).replace('"', '\\"'))
            lines.append("{}={}".format(key, val))
        return "\\n".join(lines) + "\\n"

''',

    "cache_dir": '''

import os
import time
class CacheDir:
    def __init__(self, base_dir, max_entries=100):
        self.base_dir = base_dir
        self.max_entries = max_entries
        self.entries = {}

    def _key_path(self, key):
        return os.path.join(self.base_dir, key)

    def put(self, key, value, ttl=None):
        entry = {"value": value, "created": time.time()}
        if ttl is not None:
            entry["expires"] = time.time() + ttl
        self.entries[key] = entry

    def get(self, key):
        entry = self.entries.get(key)
        if entry is None:
            return None
        if "expires" in entry and time.time() > entry["expires"]:
            del self.entries[key]
            return None
        return entry["value"]

''',

    # ── Web/HTTP ─────────────────────────────────────────────────────────

    "http_parser": '''

def parse_http_request(raw):
    parts = raw.split("\\r\\n\\r\\n", 1)
    header_section = parts[0]
    body = parts[1] if len(parts) > 1 else ""
    lines = header_section.split("\\r\\n")
    request_line = lines[0]
    method, path, version = request_line.split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            key, val = line.split(": ", 1)
            headers[key.lower()] = val
    return {"method": method, "path": path, "version": version, "headers": headers, "body": body}

def format_http_response(status_code, reason, headers, body):
    status_line = "HTTP/1.1 {} {}".format(status_code, reason)
    header_lines = ["{}: {}".format(k, v) for k, v in headers.items()]
    return "\\r\\n".join([status_line] + header_lines + ["", body])

def parse_query_string(qs):
    params = {}
    for pair in qs.split("&"):
        if "=" in pair:
            key, val = pair.split("=", 1)
            params[key] = val
    return params

''',

    "multipart_parser": '''

class MultipartPart:
    def __init__(self):
        self.headers = {}
        self.content = ""
        self.name = ""

def parse_multipart(body, boundary):
    parts = []
    sections = body.split("--" + boundary)
    for section in sections[1:]:
        if section.strip() == "--" or section.strip() == "--\\r\\n":
            break
        part = MultipartPart()
        header_end = section.find("\\r\\n\\r\\n")
        if header_end == -1:
            header_end = section.find("\\n\\n")
            sep = "\\n\\n"
        else:
            sep = "\\r\\n\\r\\n"
        header_text = section[:header_end].strip()
        part.content = section[header_end + len(sep):].rstrip("\\r\\n")
        for line in header_text.split("\\r\\n"):
            if ": " in line:
                key, val = line.split(": ", 1)
                part.headers[key.lower()] = val
        disp = part.headers.get("content-disposition", "")
        if "name=" in disp:
            part.name = disp.split("name=")[1].strip('"').split('"')[0]
        parts.append(part)
    return parts

''',

    "jwt_decoder": '''

import base64
import json
import hmac
import hashlib

def decode_jwt(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid JWT format")
    header = json.loads(_b64decode(parts[0]))
    payload = json.loads(_b64decode(parts[1]))
    signature = parts[2]
    return {"header": header, "payload": payload, "signature": signature}

def _b64decode(s):
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s).decode("utf-8")

def validate_jwt_structure(token):
    parts = token.split(".")
    if len(parts) != 3:
        return False
    for part in parts[:2]:
        try:
            _b64decode(part)
        except Exception:
            return False
    return True

''',

    "oauth_flow": '''

import hashlib
import time
import secrets

class OAuthFlow:
    def __init__(self, client_id, client_secret, redirect_uri):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.codes = {}
        self.tokens = {}

    def authorize(self, scope="read"):
        code = secrets.token_hex(16)
        self.codes[code] = {"scope": scope, "created": time.time(), "used": False}
        return "{}?code={}&client_id={}".format(self.redirect_uri, code, self.client_id)

    def exchange_code(self, code):
        entry = self.codes.get(code)
        if not entry or entry["used"]:
            raise ValueError("invalid or used code")
        entry["used"] = True
        token = secrets.token_hex(32)
        refresh = secrets.token_hex(32)
        self.tokens[token] = {
            "scope": entry["scope"],
            "created": time.time(),
            "expires_in": 3600,
            "refresh_token": refresh
        }
        return {"access_token": token, "refresh_token": refresh, "expires_in": 3600}

    def validate_token(self, token):
        info = self.tokens.get(token)
        if not info:
            return None
        if time.time() - info["created"] > info["expires_in"]:
            return None
        return {"scope": info["scope"], "valid": True}

''',

    "websocket_frame": '''

import struct

class WebSocketFrame:
    OPCODES = {0x0: "continuation", 0x1: "text", 0x2: "binary",
               0x8: "close", 0x9: "ping", 0xA: "pong"}

    def __init__(self, opcode, payload, fin=True, mask=None):
        self.opcode = opcode
        self.payload = payload
        self.fin = fin
        self.mask = mask

    def encode(self):
        first_byte = (0x80 if self.fin else 0) | self.opcode
        payload_bytes = self.payload.encode() if isinstance(self.payload, str) else self.payload
        length = len(payload_bytes)
        if length < 126:
            header = struct.pack("!BB", first_byte, length)
        elif length < 65536:
            header = struct.pack("!BBH", first_byte, 126, length)
        else:
            header = struct.pack("!BBQ", first_byte, 127, length)
        return header + payload_bytes

    @classmethod
    def decode(cls, data):
        first = data[0]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        second = data[1]
        masked = bool(second & 0x80)
        length = second & 0x7F
        offset = 2
        if length == 126:
            length = struct.unpack("!H", data[2:4])[0]
            offset = 4
        elif length == 127:
            length = struct.unpack("!Q", data[2:10])[0]
            offset = 10
        if masked:
            mask = data[offset:offset + 4]
            offset += 4
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data[offset:offset + length]))
        else:
            payload = data[offset:offset + length]
        return cls(opcode, payload, fin)

''',

    "sse_parser": '''

class SSEEvent:
    def __init__(self):
        self.event = ""
        self.data = []
        self.id = ""
        self.retry = None

def parse_sse_stream(text):
    events = []
    current = SSEEvent()
    for line in text.split("\\n"):
        if not line:
            if current.data:
                current.data = "\\n".join(current.data)
                events.append(current)
                current = SSEEvent()
            continue
        if line.startswith(":"):
            continue
        if ": " in line:
            field, value = line.split(": ", 1)
        else:
            field, value = line, ""
        if field == "event":
            current.event = value
        elif field == "data":
            current.data.append(value)
        elif field == "id":
            current.id = value
    return events

''',

    "graphql_lexer": '''
class GQLToken:
    def __init__(self, kind, value, line=0, col=0):
        self.kind = kind
        self.value = value
        self.line = line
        self.col = col

def lex_graphql(source):
    """Tokenize a GraphQL query string."""
    tokens = []
    i = 0
    line = 1
    col = 1
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in " \\t\\r,":
            i += 1
            col += 1
            continue
        if ch == "\\n":
            i += 1
            line += 1
            col = 1
            continue
        if ch == "#":
            while i < n and source[i] != "\\n":
                i += 1
            continue
        if ch in "{}()[]:.!=@|":
            if ch == "." and i + 2 < n and source[i + 1:i + 3] == "..":
                tokens.append(GQLToken("spread", "...", line, col))
                i += 3
                col += 3
            else:
                tokens.append(GQLToken("punctuation", ch, line, col))
                i += 1
                col += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == "\\\\":
                    j += 1
                j += 1
            val = source[i + 1:j]
            tokens.append(GQLToken("string", val, line, col))
            i = j + 1
            col += j - i + 2
            continue
        if ch.isdigit() or ch == "-":
            j = i + 1
            while j < n and (source[j].isdigit() or source[j] in ".eE+-"):
                j += 1
            tokens.append(GQLToken("number", source[i:j], line, col))
            col += j - i
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            keywords = {"query", "mutation", "subscription", "fragment",
                        "on", "true", "false", "null"}
            kind = "keyword" if word in keywords else "name"
            tokens.append(GQLToken(kind, word, line, col))
            col += j - i
            i = j
            continue
        i += 1
        col += 1
    return tokens
''',

    "rest_client": '''

class RestClient:
    def __init__(self, base_url, headers=None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.history = []

    def _build_url(self, path, params=None):
        url = "{}/{}".format(self.base_url, path.lstrip("/"))
        if params:
            query = "&".join("{}={}".format(k, v) for k, v in params.items())
            url += "?" + query
        return url

    def request(self, method, path, params=None, body=None):
        url = self._build_url(path, params)
        req = {"method": method, "url": url, "headers": dict(self.headers), "body": body}
        self.history.append(req)
        return req

    def get(self, path, params=None):
        return self.request("GET", path, params)

    def post(self, path, body=None):
        return self.request("POST", path, body=body)

    def put(self, path, body=None):
        return self.request("PUT", path, body=body)

    def delete(self, path):
        return self.request("DELETE", path)

''',

    "api_paginator": '''

class ApiPaginator:
    def __init__(self, items, page_size=20):
        self.items = list(items)
        self.page_size = page_size

    def total_pages(self):
        return max(1, (len(self.items) + self.page_size - 1) // self.page_size)

    def get_page(self, page_num):
        if page_num < 1 or page_num > self.total_pages():
            return {"items": [], "page": page_num, "total_pages": self.total_pages()}
        start = (page_num - 1) * self.page_size
        end = start + self.page_size
        return {
            "items": self.items[start:end],
            "page": page_num,
            "total_pages": self.total_pages(),
            "has_next": page_num < self.total_pages(),
            "has_prev": page_num > 1
        }

    def cursor_page(self, cursor=0, limit=None):
        limit = limit or self.page_size
        items = self.items[cursor:cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(self.items) else None
        return {"items": items, "next_cursor": next_cursor}

    def total_items(self):
        return len(self.items)

''',

    "webhook_validator": '''

import hashlib
import hmac
import time

class WebhookValidator:
    def __init__(self, secret, tolerance=300):
        self.secret = secret
        self.tolerance = tolerance

    def sign(self, payload, timestamp=None):
        ts = timestamp or int(time.time())
        message = "{}:{}".format(ts, payload)
        sig = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return "t={},v1={}".format(ts, sig)

    def verify(self, payload, signature):
        parts = {}
        for item in signature.split(","):
            if "=" in item:
                key, val = item.split("=", 1)
                parts[key] = val
        ts = int(parts.get("t", 0))
        if abs(time.time() - ts) > self.tolerance:
            return False
        message = "{}:{}".format(ts, payload)
        expected = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts.get("v1", ""))

    def is_replay(self, signature, seen_set):
        return signature in seen_set

''',

    # ── Database Models ──────────────────────────────────────────────────

    "table_schema": '''

class Column:
    def __init__(self, name, col_type, nullable=True, default=None):
        self.name = name
        self.col_type = col_type
        self.nullable = nullable
        self.default = default

class TableSchema:
    def __init__(self, name, columns=None):
        self.name = name
        self.columns = list(columns or [])
        self.primary_key = []

    def add_column(self, column):
        self.columns.append(column)

    def set_primary_key(self, col_names):
        self.primary_key = list(col_names)

    def get_column(self, name):
        for col in self.columns:
            if col.name == name:
                return col
        return None

    def validate_row(self, row):
        errors = []
        for col in self.columns:
            val = row.get(col.name)
            if val is None and not col.nullable and col.default is None:
                errors.append("{} is required".format(col.name))
        return errors

''',

    "row_mapper": '''

class RowMapper:
    def __init__(self, columns):
        self.columns = list(columns)
        self.transforms = {}

    def add_transform(self, col_name, func):
        self.transforms[col_name] = func

    def map_row(self, row_tuple):
        result = {}
        for i, col in enumerate(self.columns):
            val = row_tuple[i] if i < len(row_tuple) else None
            if col in self.transforms:
                val = self.transforms[col](val)
            result[col] = val
        return result

    def map_many(self, rows):
        return [self.map_row(r) for r in rows]

    def to_tuples(self, dicts):
        result = []
        for d in dicts:
            row = tuple(d.get(c) for c in self.columns)
            result.append(row)
        return result

    def rename_column(self, old_name, new_name):
        for i, c in enumerate(self.columns):
            if c == old_name:
                self.columns[i] = new_name
                if old_name in self.transforms:
                    self.transforms[new_name] = self.transforms.pop(old_name)
                break

''',

    "sql_where_builder": '''

class WhereClause:
    def __init__(self):
        self.conditions = []
        self.params = []

    def eq(self, column, value):
        self.conditions.append("{} = ?".format(column))
        self.params.append(value)
        return self

    def neq(self, column, value):
        self.conditions.append("{} != ?".format(column))
        self.params.append(value)
        return self

    def gt(self, column, value):
        self.conditions.append("{} > ?".format(column))
        self.params.append(value)
        return self

    def lt(self, column, value):
        self.conditions.append("{} < ?".format(column))
        self.params.append(value)
        return self

    def build(self):
        if not self.conditions:
            return "", []
        return "WHERE " + " AND ".join(self.conditions), list(self.params)

''',

    "index_btree": '''
class BTreeNode:
    def __init__(self, leaf=True):
        self.keys = []
        self.children = []
        self.leaf = leaf

class BTree:
    """Simple B-tree for database index simulation."""

    def __init__(self, order=4):
        self.root = BTreeNode()
        self.order = order

    def search(self, key, node=None):
        if node is None:
            node = self.root
        i = 0
        while i < len(node.keys) and key > node.keys[i]:
            i += 1
        if i < len(node.keys) and key == node.keys[i]:
            return True
        if node.leaf:
            return False
        return self.search(key, node.children[i])

    def insert(self, key):
        root = self.root
        if len(root.keys) == self.order - 1:
            new_root = BTreeNode(leaf=False)
            new_root.children.append(self.root)
            self._split_child(new_root, 0)
            self.root = new_root
        self._insert_non_full(self.root, key)

    def _insert_non_full(self, node, key):
        i = len(node.keys) - 1
        if node.leaf:
            node.keys.append(None)
            while i >= 0 and key < node.keys[i]:
                node.keys[i + 1] = node.keys[i]
                i -= 1
            node.keys[i + 1] = key
        else:
            while i >= 0 and key < node.keys[i]:
                i -= 1
            i += 1
            if len(node.children[i].keys) == self.order - 1:
                self._split_child(node, i)
                if key > node.keys[i]:
                    i += 1
            self._insert_non_full(node.children[i], key)

    def _split_child(self, parent, index):
        order = self.order
        child = parent.children[index]
        mid = order // 2 - 1
        new_node = BTreeNode(leaf=child.leaf)
        parent.keys.insert(index, child.keys[mid])
        parent.children.insert(index + 1, new_node)
        new_node.keys = child.keys[mid + 1:]
        child.keys = child.keys[:mid]
        if not child.leaf:
            new_node.children = child.children[mid + 1:]
            child.children = child.children[:mid + 1]
''',

    "wal_logger": '''

import time

class WALEntry:
    def __init__(self, seq, operation, data):
        self.seq = seq
        self.operation = operation
        self.data = data
        self.timestamp = time.time()
        self.committed = False

class WALLogger:
    def __init__(self):
        self.entries = []
        self.seq_counter = 0
        self.checkpoint_seq = 0

    def append(self, operation, data):
        self.seq_counter += 1
        entry = WALEntry(self.seq_counter, operation, data)
        self.entries.append(entry)
        return entry.seq

    def commit(self, seq):
        for entry in self.entries:
            if entry.seq == seq:
                entry.committed = True
                return True
        return False

    def replay(self, from_seq=0):
        return [e for e in self.entries if e.seq > from_seq and e.committed]

    def checkpoint(self):
        committed = [e for e in self.entries if e.committed]
        if committed:
            self.checkpoint_seq = committed[-1].seq
        self.entries = [e for e in self.entries if e.seq > self.checkpoint_seq]
        return self.checkpoint_seq

    def pending(self):
        return [e for e in self.entries if not e.committed]

    def rollback(self, seq):
        self.entries = [e for e in self.entries if e.seq != seq or e.committed]

''',

    "connection_retry": '''

import time
import random

class ConnectionRetry:
    def __init__(self, max_retries=5, base_delay=1.0, max_delay=30.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.attempts = 0
        self.connected = False
        self.log = []

    def _delay(self):
        exp = self.base_delay * (2 ** self.attempts)
        jitter = random.uniform(0, exp * 0.1)
        return min(exp + jitter, self.max_delay)

    def connect(self, connect_func):
        self.attempts = 0
        while self.attempts <= self.max_retries:
            try:
                result = connect_func()
                self.connected = True
                self.log.append({"attempt": self.attempts, "status": "success"})
                return result
            except Exception as e:
                self.log.append({"attempt": self.attempts, "status": "failed", "error": str(e)})
                if self.attempts >= self.max_retries:
                    raise
                delay = self._delay()
                time.sleep(delay)
                self.attempts += 1
        return None

    def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def get_log(self):
        return list(self.log)

    def reset(self):
        self.attempts = 0
        self.connected = False
        self.log.clear()

''',

    "result_cursor": '''

class ResultCursor:
    def __init__(self, rows, columns):
        self.rows = list(rows)
        self.columns = list(columns)
        self.position = -1
        self.closed = False

    def fetchone(self):
        if self.closed:
            raise RuntimeError("cursor is closed")
        self.position += 1
        if self.position >= len(self.rows):
            return None
        return dict(zip(self.columns, self.rows[self.position]))

    def fetchmany(self, size=10):
        results = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            results.append(row)
        return results

    def fetchall(self):
        return self.fetchmany(len(self.rows))

    def close(self):
        self.closed = True

''',

    "schema_diff": '''

class SchemaDiff:
    def __init__(self):
        self.changes = []

    def compare_tables(self, old_cols, new_cols):
        old_names = {c["name"] for c in old_cols}
        new_names = {c["name"] for c in new_cols}
        for name in new_names - old_names:
            col = next(c for c in new_cols if c["name"] == name)
            self.changes.append({"action": "add_column", "column": col})
        for name in old_names - new_names:
            self.changes.append({"action": "drop_column", "name": name})
        for name in old_names & new_names:
            old_c = next(c for c in old_cols if c["name"] == name)
            new_c = next(c for c in new_cols if c["name"] == name)
            if old_c.get("type") != new_c.get("type"):
                self.changes.append({"action": "alter_column", "column": new_c, "old_type": old_c.get("type")})
        return self.changes

    def generate_sql(self, table_name):
        stmts = []
        for ch in self.changes:
            if ch["action"] == "add_column":
                stmts.append("ALTER TABLE {} ADD COLUMN {} {}".format(table_name, ch["column"]["name"], ch["column"].get("type", "TEXT")))
            elif ch["action"] == "drop_column":
                stmts.append("ALTER TABLE {} DROP COLUMN {}".format(table_name, ch["name"]))
            elif ch["action"] == "alter_column":
                stmts.append("ALTER TABLE {} ALTER COLUMN {} TYPE {}".format(table_name, ch["column"]["name"], ch["column"]["type"]))
        return stmts

''',

    "trigger_manager": '''

class TriggerManager:
    def __init__(self):
        self.triggers = {}

    def register(self, event, callback, priority=0):
        if event not in self.triggers:
            self.triggers[event] = []
        self.triggers[event].append({"callback": callback, "priority": priority})
        self.triggers[event].sort(key=lambda t: t["priority"], reverse=True)

    def fire(self, event, context=None):
        results = []
        for trigger in self.triggers.get(event, []):
            result = trigger["callback"](context)
            results.append(result)
        return results

    def unregister(self, event, callback):
        if event in self.triggers:
            self.triggers[event] = [t for t in self.triggers[event] if t["callback"] is not callback]

    def list_events(self):
        return list(self.triggers.keys())

    def count(self, event):
        return len(self.triggers.get(event, []))

    def clear(self, event=None):
        if event:
            self.triggers.pop(event, None)
        else:
            self.triggers.clear()

''',

    "vacuum_analyzer": '''

class VacuumAnalyzer:
    def __init__(self):
        self.tables = {}

    def register_table(self, name, row_count, dead_rows=0, last_vacuum=None):
        self.tables[name] = {
            "row_count": row_count,
            "dead_rows": dead_rows,
            "last_vacuum": last_vacuum,
            "auto_vacuum_threshold": 0.2
        }

    def needs_vacuum(self, table_name):
        info = self.tables.get(table_name)
        if info is None:
            return False
        if info["row_count"] == 0:
            return info["dead_rows"] > 0
        ratio = info["dead_rows"] / info["row_count"]
        return ratio > info["auto_vacuum_threshold"]

    def vacuum(self, table_name):
        if table_name in self.tables:
            self.tables[table_name]["dead_rows"] = 0
            import time
            self.tables[table_name]["last_vacuum"] = time.time()

    def analyze_all(self):
        return [name for name in self.tables if self.needs_vacuum(name)]

    def get_stats(self, table_name):
        return self.tables.get(table_name)

''',

    # ── State Machines ───────────────────────────────────────────────────

    "regex_nfa": '''

class NFAState:
    def __init__(self, label=None):
        self.label = label
        self.transitions = {}
        self.epsilon = []
        self.accepting = False

class NFA:
    def __init__(self, start, accept):
        self.start = start
        self.accept = accept
        accept.accepting = True

    def match(self, text):
        current = self._epsilon_closure({self.start})
        for ch in text:
            next_states = set()
            for state in current:
                if ch in state.transitions:
                    next_states.update(state.transitions[ch])
                if "." in state.transitions:
                    next_states.update(state.transitions["."])
            current = self._epsilon_closure(next_states)
        return any(s.accepting for s in current)

    def _epsilon_closure(self, states):
        stack = list(states)
        closure = set(states)
        while stack:
            state = stack.pop()
            for eps in state.epsilon:
                if eps not in closure:
                    closure.add(eps)
                    stack.append(eps)
        return closure

def build_literal_nfa(text):
    start = NFAState()
    current = start
    for ch in text:
        next_state = NFAState()
        current.transitions[ch] = [next_state]
        current = next_state
    return NFA(start, current)

''',

    "tcp_handshake": '''

class TCPState:
    CLOSED = "CLOSED"
    LISTEN = "LISTEN"
    SYN_SENT = "SYN_SENT"
    SYN_RECEIVED = "SYN_RECEIVED"
    ESTABLISHED = "ESTABLISHED"
    FIN_WAIT_1 = "FIN_WAIT_1"
    FIN_WAIT_2 = "FIN_WAIT_2"
    TIME_WAIT = "TIME_WAIT"
    CLOSE_WAIT = "CLOSE_WAIT"

class TCPConnection:
    def __init__(self):
        self.state = TCPState.CLOSED
        self.seq = 0
        self.ack = 0
        self.log = []

    def _transition(self, new_state, msg):
        self.log.append("{} -> {} ({})".format(self.state, new_state, msg))
        self.state = new_state

    def connect(self, server_seq=1000):
        if self.state != TCPState.CLOSED:
            raise RuntimeError("invalid state for connect")
        self.seq = 100
        self._transition(TCPState.SYN_SENT, "SYN sent")
        self.ack = server_seq + 1
        self.seq += 1
        self._transition(TCPState.ESTABLISHED, "ACK sent")

''',

    "http_chunked": '''

def encode_chunked(data, chunk_size=1024):
    chunks = []
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        size_hex = hex(len(chunk))[2:]
        chunks.append(size_hex + "\\r\\n" + chunk + "\\r\\n")
    chunks.append("0\\r\\n\\r\\n")
    return "".join(chunks)

def decode_chunked(raw):
    result = []
    pos = 0
    while pos < len(raw):
        line_end = raw.find("\\r\\n", pos)
        if line_end == -1:
            break
        size = int(raw[pos:line_end], 16)
        if size == 0:
            break
        start = line_end + 2
        result.append(raw[start:start + size])
        pos = start + size + 2
    return "".join(result)

''',

    "json_parser_fsm": '''
class JSONParser:
    """Simple JSON parser using a state machine."""

    def __init__(self, text):
        self.text = text
        self.pos = 0

    def parse(self):
        self._skip_ws()
        return self._parse_value()

    def _skip_ws(self):
        while self.pos < len(self.text) and self.text[self.pos] in " \\t\\n\\r":
            self.pos += 1

    def _parse_value(self):
        self._skip_ws()
        ch = self.text[self.pos]
        if ch == '"':
            return self._parse_string()
        elif ch == '{':
            return self._parse_object()
        elif ch == '[':
            return self._parse_array()
        elif ch in '-0123456789':
            return self._parse_number()
        elif self.text[self.pos:self.pos + 4] == "true":
            self.pos += 4
            return True
        elif self.text[self.pos:self.pos + 5] == "false":
            self.pos += 5
            return False
        elif self.text[self.pos:self.pos + 4] == "null":
            self.pos += 4
            return None
        raise ValueError("unexpected char at {}".format(self.pos))

    def _parse_string(self):
        self.pos += 1
        start = self.pos
        while self.text[self.pos] != '"':
            if self.text[self.pos] == "\\\\":
                self.pos += 1
            self.pos += 1
        val = self.text[start:self.pos]
        self.pos += 1
        return val

    def _parse_number(self):
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos < len(self.text) and self.text[self.pos] == ".":
            self.pos += 1
            while self.pos < len(self.text) and self.text[self.pos].isdigit():
                self.pos += 1
        return float(self.text[start:self.pos])

    def _parse_object(self):
        self.pos += 1
        obj = {}
        self._skip_ws()
        if self.text[self.pos] == "}":
            self.pos += 1
            return obj
        while True:
            self._skip_ws()
            key = self._parse_string()
            self._skip_ws()
            self.pos += 1
            val = self._parse_value()
            obj[key] = val
            self._skip_ws()
            if self.text[self.pos] == "}":
                self.pos += 1
                return obj
            self.pos += 1

    def _parse_array(self):
        self.pos += 1
        arr = []
        self._skip_ws()
        if self.text[self.pos] == "]":
            self.pos += 1
            return arr
        while True:
            arr.append(self._parse_value())
            self._skip_ws()
            if self.text[self.pos] == "]":
                self.pos += 1
                return arr
            self.pos += 1
''',

    "csv_state_parser": '''
class CSVStateMachine:
    """CSV parser implemented as explicit state machine."""

    START = "start"
    IN_FIELD = "in_field"
    IN_QUOTED = "in_quoted"
    QUOTE_END = "quote_end"

    def __init__(self, delimiter=",", quote='"'):
        self.delimiter = delimiter
        self.quote = quote
        self.state = self.START
        self.current_field = []
        self.fields = []
        self.rows = []

    def feed_char(self, ch):
        if self.state == self.START:
            if ch == self.quote:
                self.state = self.IN_QUOTED
            elif ch == self.delimiter:
                self.fields.append("")
            elif ch == "\\n":
                self.fields.append("")
                self.rows.append(self.fields)
                self.fields = []
            else:
                self.current_field.append(ch)
                self.state = self.IN_FIELD
        elif self.state == self.IN_FIELD:
            if ch == self.delimiter:
                self.fields.append("".join(self.current_field))
                self.current_field = []
                self.state = self.START
            elif ch == "\\n":
                self.fields.append("".join(self.current_field))
                self.current_field = []
                self.rows.append(self.fields)
                self.fields = []
                self.state = self.START
            else:
                self.current_field.append(ch)
        elif self.state == self.IN_QUOTED:
            if ch == self.quote:
                self.state = self.QUOTE_END
            else:
                self.current_field.append(ch)
        elif self.state == self.QUOTE_END:
            if ch == self.quote:
                self.current_field.append(self.quote)
                self.state = self.IN_QUOTED
            elif ch == self.delimiter:
                self.fields.append("".join(self.current_field))
                self.current_field = []
                self.state = self.START
            elif ch == "\\n":
                self.fields.append("".join(self.current_field))
                self.current_field = []
                self.rows.append(self.fields)
                self.fields = []
                self.state = self.START

    def finish(self):
        if self.current_field or self.fields:
            self.fields.append("".join(self.current_field))
            self.rows.append(self.fields)
        return self.rows
''',

    "ansi_escape": '''
class ANSIParser:
    """Parse ANSI escape sequences from terminal output."""

    NORMAL = "normal"
    ESCAPE = "escape"
    CSI = "csi"

    def __init__(self):
        self.state = self.NORMAL
        self.output = []
        self.current_seq = []
        self.sequences = []

    def feed(self, text):
        for ch in text:
            if self.state == self.NORMAL:
                if ch == "\\x1b":
                    self.state = self.ESCAPE
                    self.current_seq = [ch]
                else:
                    self.output.append(ch)
            elif self.state == self.ESCAPE:
                self.current_seq.append(ch)
                if ch == "[":
                    self.state = self.CSI
                else:
                    self.sequences.append("".join(self.current_seq))
                    self.state = self.NORMAL
            elif self.state == self.CSI:
                self.current_seq.append(ch)
                if ch.isalpha():
                    seq = "".join(self.current_seq)
                    self.sequences.append(seq)
                    self._handle_sequence(seq)
                    self.current_seq = []
                    self.state = self.NORMAL

    def _handle_sequence(self, seq):
        """Interpret common ANSI sequences."""
        body = seq[2:-1]
        command = seq[-1]
        params = body.split(";") if body else []
        self.sequences.append({
            "raw": seq,
            "command": command,
            "params": params,
        })

    def get_text(self):
        return "".join(self.output)

    def strip_ansi(self, text):
        """Remove all ANSI escape sequences from text."""
        result = []
        i = 0
        while i < len(text):
            if text[i] == "\\x1b" and i + 1 < len(text) and text[i + 1] == "[":
                i += 2
                while i < len(text) and not text[i].isalpha():
                    i += 1
                i += 1
            else:
                result.append(text[i])
                i += 1
        return "".join(result)
''',

    "midi_parser": '''
class MidiEvent:
    def __init__(self, delta, event_type, channel=0, data=None):
        self.delta = delta
        self.event_type = event_type
        self.channel = channel
        self.data = data or []

def parse_variable_length(data, offset):
    """Parse MIDI variable-length quantity."""
    result = 0
    while offset < len(data):
        byte = data[offset]
        result = (result << 7) | (byte & 0x7F)
        offset += 1
        if not (byte & 0x80):
            break
    return result, offset

def encode_variable_length(value):
    """Encode integer as MIDI variable-length quantity."""
    if value == 0:
        return [0]
    result = []
    result.append(value & 0x7F)
    value >>= 7
    while value > 0:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    return list(reversed(result))

def parse_midi_track(data):
    """Parse MIDI track events from byte data."""
    events = []
    offset = 0
    running_status = 0
    while offset < len(data):
        delta, offset = parse_variable_length(data, offset)
        if offset >= len(data):
            break
        status = data[offset]
        if status & 0x80:
            running_status = status
            offset += 1
        else:
            status = running_status
        event_type = (status >> 4) & 0x0F
        channel = status & 0x0F
        if event_type in (0x08, 0x09, 0x0A, 0x0B, 0x0E):
            d1 = data[offset] if offset < len(data) else 0
            d2 = data[offset + 1] if offset + 1 < len(data) else 0
            events.append(MidiEvent(delta, event_type, channel, [d1, d2]))
            offset += 2
        elif event_type in (0x0C, 0x0D):
            d1 = data[offset] if offset < len(data) else 0
            events.append(MidiEvent(delta, event_type, channel, [d1]))
            offset += 1
        elif status == 0xFF:
            meta_type = data[offset] if offset < len(data) else 0
            offset += 1
            length, offset = parse_variable_length(data, offset)
            meta_data = data[offset:offset + length]
            events.append(MidiEvent(delta, 0xFF, 0, [meta_type] + list(meta_data)))
            offset += length
        else:
            break
    return events
''',

    "promise_states": '''
class Promise:
    """Simulate Promise state machine (pending/fulfilled/rejected)."""

    PENDING = "pending"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"

    def __init__(self, executor=None):
        self.state = self.PENDING
        self.value = None
        self.reason = None
        self._then_cbs = []
        self._catch_cbs = []
        if executor:
            try:
                executor(self._resolve, self._reject)
            except Exception as e:
                self._reject(e)

    def _resolve(self, value):
        if self.state != self.PENDING:
            return
        self.state = self.FULFILLED
        self.value = value
        for cb in self._then_cbs:
            cb(value)

    def _reject(self, reason):
        if self.state != self.PENDING:
            return
        self.state = self.REJECTED
        self.reason = reason
        for cb in self._catch_cbs:
            cb(reason)

    def then(self, on_fulfilled):
        if self.state == self.FULFILLED:
            on_fulfilled(self.value)
        else:
            self._then_cbs.append(on_fulfilled)
        return self

    def catch(self, on_rejected):
        if self.state == self.REJECTED:
            on_rejected(self.reason)
        else:
            self._catch_cbs.append(on_rejected)
        return self

    @staticmethod
    def all(promises):
        results = [None] * len(promises)
        remaining = [len(promises)]
        combined = Promise()
        for i, p in enumerate(promises):
            def make_handler(idx):
                def handler(val):
                    results[idx] = val
                    remaining[0] -= 1
                    if remaining[0] == 0:
                        combined._resolve(results)
                return handler
            p.then(make_handler(i))
        return combined
''',

    "workflow_engine": '''
class WorkflowStep:
    def __init__(self, name, action, next_steps=None, condition=None):
        self.name = name
        self.action = action
        self.next_steps = next_steps or []
        self.condition = condition

class WorkflowEngine:
    """Execute a workflow defined as a directed graph of steps."""

    def __init__(self):
        self.steps = {}
        self.history = []
        self.context = {}

    def add_step(self, step):
        self.steps[step.name] = step

    def run(self, start_step, context=None):
        self.context = context or {}
        current = start_step
        while current:
            step = self.steps.get(current)
            if step is None:
                break
            self.history.append(current)
            try:
                result = step.action(self.context)
                self.context["_last_result"] = result
            except Exception as e:
                self.context["_error"] = str(e)
                break
            next_step = None
            for candidate in step.next_steps:
                if isinstance(candidate, tuple):
                    cond, name = candidate
                    if cond(self.context):
                        next_step = name
                        break
                else:
                    next_step = candidate
                    break
            current = next_step
        return self.context

    def get_history(self):
        return list(self.history)

    def reset(self):
        self.history = []
        self.context = {}

def test_workflow():
    def init(ctx):
        ctx["count"] = 0
    def increment(ctx):
        ctx["count"] += 1
    engine = WorkflowEngine()
    engine.add_step(WorkflowStep("init", init, ["inc"]))
    engine.add_step(WorkflowStep("inc", increment, [
        (lambda ctx: ctx["count"] < 3, "inc"),
    ]))
    result = engine.run("init")
    assert result["count"] == 3
''',

    "cron_scheduler": '''

class CronField:
    def __init__(self, expr, min_val, max_val):
        self.values = self._parse(expr, min_val, max_val)

    def _parse(self, expr, min_val, max_val):
        if expr == "*":
            return set(range(min_val, max_val + 1))
        values = set()
        for part in expr.split(","):
            if "/" in part:
                base, step = part.split("/")
                start = min_val if base == "*" else int(base)
                values.update(range(start, max_val + 1, int(step)))
            elif "-" in part:
                lo, hi = part.split("-")
                values.update(range(int(lo), int(hi) + 1))
            else:
                values.add(int(part))
        return values

class CronScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, name, cron_expr, func):
        parts = cron_expr.split()
        fields = [
            CronField(parts[0], 0, 59),
            CronField(parts[1], 0, 23),
            CronField(parts[2], 1, 31),
            CronField(parts[3], 1, 12),
            CronField(parts[4], 0, 6)
        ]
        self.jobs[name] = {"fields": fields, "func": func}

''',

    # ── Error Handling ───────────────────────────────────────────────────

    "bulkhead_pattern": '''

import threading

class Bulkhead:
    def __init__(self, name, max_concurrent=10, max_queue=50):
        self.name = name
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.semaphore = threading.Semaphore(max_concurrent)
        self.active = 0
        self.rejected = 0
        self.lock = threading.Lock()

    def acquire(self, timeout=None):
        acquired = self.semaphore.acquire(timeout=timeout)
        if acquired:
            with self.lock:
                self.active += 1
        else:
            with self.lock:
                self.rejected += 1
        return acquired

    def release(self):
        with self.lock:
            self.active -= 1
        self.semaphore.release()

    def execute(self, func, *args, timeout=None, **kwargs):
        if not self.acquire(timeout=timeout):
            raise RuntimeError("bulkhead {} rejected".format(self.name))
        try:
            return func(*args, **kwargs)
        finally:
            self.release()

''',

    "retry_with_jitter": '''

import random
import time

class RetryWithJitter:
    def __init__(self, max_retries=3, base_delay=1.0, max_delay=30.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def _calc_delay(self, attempt):
        exp_delay = self.base_delay * (2 ** attempt)
        capped = min(exp_delay, self.max_delay)
        return random.uniform(0, capped)

    def execute(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = self._calc_delay(attempt)
                    time.sleep(delay)
        raise last_error

    def execute_dry(self, func, *args, **kwargs):
        delays = []
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs), delays
            except Exception:
                if attempt < self.max_retries:
                    delays.append(self._calc_delay(attempt))
        return None, delays

''',

    "dead_letter_queue": '''

import time

class DeadLetterQueue:
    def __init__(self, max_retries=3):
        self.max_retries = max_retries
        self.queue = []
        self.dead_letters = []

    def enqueue(self, message):
        self.queue.append({"message": message, "attempts": 0, "errors": []})

    def process(self, handler):
        results = []
        remaining = []
        for item in self.queue:
            try:
                result = handler(item["message"])
                results.append(result)
            except Exception as e:
                item["attempts"] += 1
                item["errors"].append(str(e))
                if item["attempts"] >= self.max_retries:
                    item["failed_at"] = time.time()
                    self.dead_letters.append(item)
                else:
                    remaining.append(item)
        self.queue = remaining
        return results

    def get_dead_letters(self):
        return list(self.dead_letters)

''',

    "rate_limit_backoff": '''

import time

class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = []

    def allow(self):
        now = time.time()
        self.requests = [t for t in self.requests if now - t < self.window]
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True
        return False

class BackoffStrategy:
    def __init__(self, initial=1.0, maximum=60.0, multiplier=2.0):
        self.initial = initial
        self.maximum = maximum
        self.multiplier = multiplier
        self.current = initial

    def next_delay(self):
        delay = self.current
        self.current = min(self.current * self.multiplier, self.maximum)
        return delay

''',

    "idempotency_guard": '''

import hashlib
import time

class IdempotencyGuard:
    def __init__(self, ttl=300):
        self.store = {}
        self.ttl = ttl

    def _hash_request(self, key, payload):
        raw = "{}:{}".format(key, payload)
        return hashlib.sha256(raw.encode()).hexdigest()

    def check(self, key, payload):
        h = self._hash_request(key, payload)
        now = time.time()
        self._purge(now)
        if h in self.store:
            return True, self.store[h]["result"]
        return False, None

    def record(self, key, payload, result):
        h = self._hash_request(key, payload)
        self.store[h] = {"result": result, "ts": time.time()}

''',

    "saga_orchestrator": '''

class SagaStep:
    def __init__(self, name, action, compensation):
        self.name = name
        self.action = action
        self.compensation = compensation

class SagaOrchestrator:
    def __init__(self):
        self.steps = []
        self.completed = []
        self.state = "idle"
        self.log = []

    def add_step(self, name, action, compensation):
        self.steps.append(SagaStep(name, action, compensation))

    def execute(self):
        self.state = "running"
        self.completed = []
        for step in self.steps:
            try:
                result = step.action()
                self.completed.append((step, result))
                self.log.append({"step": step.name, "status": "success"})
            except Exception as e:
                self.log.append({"step": step.name, "status": "failed", "error": str(e)})
                self._compensate()
                self.state = "failed"
                raise
        self.state = "completed"
        return [r for _, r in self.completed]

    def _compensate(self):
        for step, _ in reversed(self.completed):
            try:
                step.compensation()
                self.log.append({"step": step.name, "status": "compensated"})
            except Exception as e:
                self.log.append({"step": step.name, "status": "compensation_failed", "error": str(e)})

    def get_state(self):
        return self.state

    def get_log(self):
        return list(self.log)

    def reset(self):
        self.completed.clear()
        self.state = "idle"
        self.log.clear()

''',

    "compensating_action": '''

class CompensatingAction:
    def __init__(self):
        self.actions = []
        self.compensations = []
        self.completed = []

    def add_step(self, action, compensation):
        self.actions.append(action)
        self.compensations.append(compensation)

    def execute(self):
        self.completed = []
        for i, action in enumerate(self.actions):
            try:
                result = action()
                self.completed.append((i, result))
            except Exception as e:
                self._rollback()
                raise RuntimeError("step {} failed: {}".format(i, e))
        return [r for _, r in self.completed]

    def _rollback(self):
        for idx, _ in reversed(self.completed):
            try:
                self.compensations[idx]()
            except Exception:
                pass

    def reset(self):
        self.actions.clear()
        self.compensations.clear()
        self.completed.clear()

    def step_count(self):
        return len(self.actions)

''',

    "error_boundary": '''

class ErrorBoundary:
    def __init__(self):
        self.errors = []
        self.handlers = {}

    def register(self, exc_type, handler):
        self.handlers[exc_type] = handler

    def execute(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            self.errors.append(e)
            handler = self.handlers.get(type(e))
            if handler:
                return handler(e)
            raise

    def get_errors(self):
        return list(self.errors)

    def clear(self):
        self.errors.clear()

''',

    "panic_recovery": '''

import traceback

class PanicRecovery:
    def __init__(self):
        self.panics = []
        self.recovered = False

    def protect(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            self.panics.append({
                "error": str(e),
                "type": type(e).__name__,
                "trace": traceback.format_exc()
            })
            self.recovered = True
            return None

    def last_panic(self):
        return self.panics[-1] if self.panics else None

    def panic_count(self):
        return len(self.panics)

''',

    "watchdog_timer": '''

import time
import threading

class WatchdogTimer:
    def __init__(self, timeout, callback=None):
        self.timeout = timeout
        self.callback = callback or self._default_callback
        self.last_kick = time.time()
        self.running = False
        self._lock = threading.Lock()

    def _default_callback(self):
        raise TimeoutError("watchdog timer expired")

    def start(self):
        self.running = True
        self.last_kick = time.time()

    def kick(self):
        with self._lock:
            self.last_kick = time.time()

    def stop(self):
        self.running = False

    def check(self):
        if not self.running:
            return True
        with self._lock:
            elapsed = time.time() - self.last_kick
        if elapsed > self.timeout:
            self.callback()
            return False
        return True

''',

    # ── Scientific Computing ─────────────────────────────────────────────

    "runge_kutta": '''

def rk4_step(f, t, y, h):
    k1 = f(t, y)
    k2 = f(t + h/2, y + h*k1/2)
    k3 = f(t + h/2, y + h*k2/2)
    k4 = f(t + h, y + h*k3)
    return y + h * (k1 + 2*k2 + 2*k3 + k4) / 6

def rk4_solve(f, t0, y0, t_end, n_steps):
    h = (t_end - t0) / n_steps
    t = t0
    y = y0
    trajectory = [(t, y)]
    for _ in range(n_steps):
        y = rk4_step(f, t, y, h)
        t += h
        trajectory.append((t, y))
    return trajectory

def rk4_adaptive(f, t, y, h, tol=1e-6):
    y1 = rk4_step(f, t, y, h)
    mid = rk4_step(f, t, y, h/2)
    y2 = rk4_step(f, t + h/2, mid, h/2)
    error = abs(y2 - y1)
    return y2, error, error < tol

''',

    "bisection_method": '''

def bisect_root(f, a, b, tol=1e-10, max_iter=100):
    if f(a) * f(b) > 0:
        raise ValueError("f(a) and f(b) must have different signs")
    for _ in range(max_iter):
        mid = (a + b) / 2.0
        if abs(f(mid)) < tol or (b - a) / 2.0 < tol:
            return mid
        if f(mid) * f(a) < 0:
            b = mid
        else:
            a = mid
    return (a + b) / 2.0

def find_all_roots(f, start, end, step=0.5, tol=1e-10):
    roots = []
    x = start
    while x < end:
        x2 = min(x + step, end)
        if f(x) * f(x2) <= 0:
            r = bisect_root(f, x, x2, tol)
            if not roots or abs(r - roots[-1]) > tol * 10:
                roots.append(r)
        x = x2
    return roots

''',

    "lu_decomposition": '''
def lu_decompose(matrix):
    """LU decomposition with partial pivoting."""
    n = len(matrix)
    L = [[0.0] * n for _ in range(n)]
    U = [row[:] for row in matrix]
    P = list(range(n))
    for k in range(n):
        max_val = 0
        max_row = k
        for i in range(k, n):
            if abs(U[i][k]) > max_val:
                max_val = abs(U[i][k])
                max_row = i
        if max_row != k:
            U[k], U[max_row] = U[max_row], U[k]
            L[k], L[max_row] = L[max_row], L[k]
            P[k], P[max_row] = P[max_row], P[k]
        for i in range(k + 1, n):
            if abs(U[k][k]) < 1e-12:
                continue
            factor = U[i][k] / U[k][k]
            L[i][k] = factor
            for j in range(k, n):
                U[i][j] -= factor * U[k][j]
    for i in range(n):
        L[i][i] = 1.0
    return L, U, P

def lu_solve(L, U, P, b):
    """Solve Ax = b using LU decomposition."""
    n = len(b)
    pb = [b[P[i]] for i in range(n)]
    y = [0.0] * n
    for i in range(n):
        y[i] = pb[i] - sum(L[i][j] * y[j] for j in range(i))
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(U[i][i]) < 1e-12:
            x[i] = 0.0
        else:
            x[i] = (y[i] - sum(U[i][j] * x[j] for j in range(i + 1, n))) / U[i][i]
    return x

def determinant_from_lu(U, P):
    """Compute determinant from LU decomposition."""
    det = 1.0
    for i in range(len(U)):
        det *= U[i][i]
    swaps = sum(1 for i in range(len(P)) if P[i] != i)
    return det * ((-1) ** swaps)
''',

    "conjugate_gradient": '''
def dot(a, b):
    """Dot product of two vectors."""
    return sum(ai * bi for ai, bi in zip(a, b))

def mat_vec(A, x):
    """Matrix-vector product."""
    n = len(A)
    return [sum(A[i][j] * x[j] for j in range(n)) for i in range(n)]

def vec_add(a, b, scale_b=1.0):
    """a + scale_b * b."""
    return [ai + scale_b * bi for ai, bi in zip(a, b)]

def conjugate_gradient(A, b, x0=None, tol=1e-10, max_iter=1000):
    """Solve Ax = b for symmetric positive-definite A."""
    n = len(b)
    x = x0 if x0 else [0.0] * n
    r = vec_add(b, mat_vec(A, x), -1.0)
    p = list(r)
    rs_old = dot(r, r)
    for iteration in range(max_iter):
        if rs_old < tol * tol:
            return x, iteration
        Ap = mat_vec(A, p)
        alpha = rs_old / dot(p, Ap)
        x = vec_add(x, p, alpha)
        r = vec_add(r, Ap, -alpha)
        rs_new = dot(r, r)
        if rs_new < tol * tol:
            return x, iteration + 1
        beta = rs_new / rs_old
        p = vec_add(r, p, beta)
        rs_old = rs_new
    return x, max_iter

def test_cg():
    A = [[4.0, 1.0], [1.0, 3.0]]
    b = [1.0, 2.0]
    x, iters = conjugate_gradient(A, b)
    Ax = mat_vec(A, x)
    assert all(abs(Ax[i] - b[i]) < 1e-6 for i in range(len(b)))
''',

    "lanczos_iteration": '''
def lanczos(A, q1, k):
    """Lanczos iteration to tridiagonalize symmetric matrix."""
    n = len(A)
    Q = [None] * (k + 1)
    alpha = [0.0] * k
    beta = [0.0] * (k + 1)
    norm_q = sum(x * x for x in q1) ** 0.5
    Q[0] = [x / norm_q for x in q1]
    for j in range(k):
        v = [sum(A[i][m] * Q[j][m] for m in range(n)) for i in range(n)]
        alpha[j] = sum(v[i] * Q[j][i] for i in range(n))
        v = [v[i] - alpha[j] * Q[j][i] for i in range(n)]
        if j > 0:
            v = [v[i] - beta[j] * Q[j - 1][i] for i in range(n)]
        beta[j + 1] = sum(x * x for x in v) ** 0.5
        if beta[j + 1] < 1e-12:
            break
        Q[j + 1] = [x / beta[j + 1] for x in v]
    return alpha, beta[1:k + 1], [q for q in Q if q is not None]

def tridiag_eigenvalues(alpha, beta, tol=1e-10):
    """Compute eigenvalues of tridiagonal matrix using QR iteration."""
    n = len(alpha)
    d = list(alpha)
    e = list(beta) + [0.0]
    for _ in range(100 * n):
        converged = True
        for i in range(n - 1):
            if abs(e[i]) > tol:
                converged = False
                break
        if converged:
            break
        shift = d[-1]
        d = [di - shift for di in d]
        for i in range(n - 1):
            if abs(d[i]) < 1e-15:
                d[i] = 1e-15
            r = (d[i] ** 2 + e[i] ** 2) ** 0.5
            c = d[i] / r
            s = e[i] / r
            d[i] = r
            if i + 1 < n:
                d[i + 1] = c * d[i + 1] + s * e[i + 1] if i + 1 < n else d[i + 1]
        d = [di + shift for di in d]
    return sorted(d)
''',

    "chebyshev_approx": '''
import math

def chebyshev_nodes(n, a=-1, b=1):
    """Generate Chebyshev interpolation nodes."""
    return [0.5 * (a + b) + 0.5 * (b - a) * math.cos((2 * k + 1) * math.pi / (2 * n))
            for k in range(n)]

def chebyshev_coefficients(f, n, a=-1, b=1):
    """Compute Chebyshev coefficients for function f."""
    nodes = chebyshev_nodes(n, a, b)
    values = [f(x) for x in nodes]
    coeffs = [0.0] * n
    for j in range(n):
        total = 0.0
        for k in range(n):
            xk = math.cos((2 * k + 1) * math.pi / (2 * n))
            tj = math.cos(j * math.acos(xk))
            total += values[k] * tj
        coeffs[j] = 2.0 * total / n
    coeffs[0] /= 2.0
    return coeffs

def chebyshev_evaluate(coeffs, x, a=-1, b=1):
    """Evaluate Chebyshev approximation at point x."""
    mapped = (2.0 * x - a - b) / (b - a)
    if abs(mapped) > 1:
        mapped = max(-1, min(1, mapped))
    n = len(coeffs)
    if n == 0:
        return 0.0
    if n == 1:
        return coeffs[0]
    b_prev = 0.0
    b_curr = 0.0
    for i in range(n - 1, 0, -1):
        b_next = coeffs[i] + 2.0 * mapped * b_curr - b_prev
        b_prev = b_curr
        b_curr = b_next
    return coeffs[0] + mapped * b_curr - b_prev

def chebyshev_error(f, coeffs, n_test=100, a=-1, b=1):
    """Estimate maximum approximation error."""
    max_err = 0.0
    for i in range(n_test):
        x = a + (b - a) * i / (n_test - 1)
        approx = chebyshev_evaluate(coeffs, x, a, b)
        exact = f(x)
        max_err = max(max_err, abs(approx - exact))
    return max_err
''',

    "adaptive_quadrature": '''
def simpson(f, a, b):
    """Simple Simpson rule."""
    mid = (a + b) / 2.0
    return (b - a) / 6.0 * (f(a) + 4 * f(mid) + f(b))

def adaptive_simpson(f, a, b, tol=1e-10, max_depth=50):
    """Adaptive Simpson quadrature."""
    whole = simpson(f, a, b)
    return _adaptive_helper(f, a, b, tol, whole, max_depth)

def _adaptive_helper(f, a, b, tol, whole, depth):
    mid = (a + b) / 2.0
    left = simpson(f, a, mid)
    right = simpson(f, mid, b)
    combined = left + right
    if depth <= 0 or abs(combined - whole) < 15 * tol:
        return combined + (combined - whole) / 15.0
    return (_adaptive_helper(f, a, mid, tol / 2, left, depth - 1) +
            _adaptive_helper(f, mid, b, tol / 2, right, depth - 1))

def gauss_legendre_5(f, a, b):
    """5-point Gauss-Legendre quadrature."""
    nodes = [-0.9061798, -0.5384693, 0.0, 0.5384693, 0.9061798]
    weights = [0.2369269, 0.4786287, 0.5688889, 0.4786287, 0.2369269]
    mid = (a + b) / 2.0
    half = (b - a) / 2.0
    total = 0.0
    for xi, wi in zip(nodes, weights):
        total += wi * f(mid + half * xi)
    return half * total

def composite_gauss(f, a, b, n_intervals=10):
    """Composite Gauss-Legendre quadrature."""
    h = (b - a) / n_intervals
    total = 0.0
    for i in range(n_intervals):
        ai = a + i * h
        bi = ai + h
        total += gauss_legendre_5(f, ai, bi)
    return total
''',

    "sparse_matrix": '''
class SparseMatrix:
    """Compressed sparse row (CSR) matrix."""

    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.data = {}

    def set(self, i, j, val):
        if abs(val) > 1e-15:
            self.data[(i, j)] = val
        elif (i, j) in self.data:
            del self.data[(i, j)]

    def get(self, i, j):
        return self.data.get((i, j), 0.0)

    def nnz(self):
        return len(self.data)

    def matvec(self, x):
        """Multiply sparse matrix by vector."""
        result = [0.0] * self.rows
        for (i, j), val in self.data.items():
            result[i] += val * x[j]
        return result

    def add(self, other):
        """Add two sparse matrices."""
        result = SparseMatrix(self.rows, self.cols)
        for key, val in self.data.items():
            result.data[key] = val
        for key, val in other.data.items():
            result.data[key] = result.data.get(key, 0.0) + val
        return result

    def transpose(self):
        result = SparseMatrix(self.cols, self.rows)
        for (i, j), val in self.data.items():
            result.data[(j, i)] = val
        return result

    def to_dense(self):
        mat = [[0.0] * self.cols for _ in range(self.rows)]
        for (i, j), val in self.data.items():
            mat[i][j] = val
        return mat

    @classmethod
    def from_dense(cls, mat):
        rows = len(mat)
        cols = len(mat[0]) if rows > 0 else 0
        sp = cls(rows, cols)
        for i in range(rows):
            for j in range(cols):
                if abs(mat[i][j]) > 1e-15:
                    sp.set(i, j, mat[i][j])
        return sp

    def density(self):
        total = self.rows * self.cols
        return self.nnz() / total if total > 0 else 0.0
''',

    "wavelet_transform": '''
import math

def haar_transform(signal):
    """Forward Haar wavelet transform."""
    n = len(signal)
    if n < 2:
        return list(signal)
    output = list(signal)
    length = n
    while length >= 2:
        half = length // 2
        temp = [0.0] * length
        for i in range(half):
            temp[i] = (output[2 * i] + output[2 * i + 1]) / math.sqrt(2)
            temp[half + i] = (output[2 * i] - output[2 * i + 1]) / math.sqrt(2)
        output[:length] = temp
        length = half
    return output

def haar_inverse(coeffs):
    """Inverse Haar wavelet transform."""
    n = len(coeffs)
    output = list(coeffs)
    length = 2
    while length <= n:
        half = length // 2
        temp = [0.0] * length
        for i in range(half):
            temp[2 * i] = (output[i] + output[half + i]) / math.sqrt(2)
            temp[2 * i + 1] = (output[i] - output[half + i]) / math.sqrt(2)
        output[:length] = temp
        length *= 2
    return output

def wavelet_denoise(signal, threshold):
    """Denoise signal using wavelet thresholding."""
    coeffs = haar_transform(signal)
    denoised = []
    for i, c in enumerate(coeffs):
        if i == 0:
            denoised.append(c)
        elif abs(c) > threshold:
            denoised.append(c)
        else:
            denoised.append(0.0)
    return haar_inverse(denoised)

def wavelet_energy(coeffs):
    """Compute energy distribution across wavelet levels."""
    n = len(coeffs)
    levels = {}
    level = 0
    start = n // 2
    while start >= 1:
        energy = sum(c * c for c in coeffs[start:start * 2])
        levels[level] = energy
        start //= 2
        level += 1
    return levels
''',

    "particle_filter": '''
import random as _rng
import math

class Particle:
    def __init__(self, state, weight=1.0):
        self.state = state
        self.weight = weight

class ParticleFilter:
    """Sequential Monte Carlo particle filter."""

    def __init__(self, n_particles, initial_state, noise_std=1.0):
        self.n_particles = n_particles
        self.noise_std = noise_std
        self.particles = [
            Particle(initial_state + _rng.gauss(0, noise_std))
            for _ in range(n_particles)
        ]

    def predict(self, motion_model):
        """Move particles according to motion model."""
        for p in self.particles:
            p.state = motion_model(p.state) + _rng.gauss(0, self.noise_std)

    def update(self, measurement, sensor_model):
        """Update particle weights based on measurement."""
        for p in self.particles:
            p.weight = sensor_model(p.state, measurement)
        total = sum(p.weight for p in self.particles)
        if total > 0:
            for p in self.particles:
                p.weight /= total

    def resample(self):
        """Systematic resampling."""
        n = self.n_particles
        positions = [((_rng.random() + i) / n) for i in range(n)]
        cumulative = []
        cum_sum = 0.0
        for p in self.particles:
            cum_sum += p.weight
            cumulative.append(cum_sum)
        new_particles = []
        idx = 0
        for pos in positions:
            while idx < n - 1 and cumulative[idx] < pos:
                idx += 1
            new_particles.append(Particle(self.particles[idx].state, 1.0 / n))
        self.particles = new_particles

    def estimate(self):
        """Weighted mean estimate."""
        return sum(p.state * p.weight for p in self.particles)

    def effective_sample_size(self):
        """Compute effective sample size."""
        w_sq = sum(p.weight ** 2 for p in self.particles)
        return 1.0 / w_sq if w_sq > 0 else 0

    def step(self, measurement, motion_model, sensor_model):
        """Full predict-update-resample cycle."""
        self.predict(motion_model)
        self.update(measurement, sensor_model)
        if self.effective_sample_size() < self.n_particles / 2:
            self.resample()
        return self.estimate()
''',
}


def main():
    print("=" * 72)
    print("PAPER 1 — Judgment Geometry: Semantic Site Construction Scaling")
    print("  All numbers from `python3 -m jugeo` CLI (subprocess)")
    print("=" * 72)
    print()

    tmpfiles = []
    results = []

    for name, source in PROGRAMS.items():
        path = write_temp(source)
        tmpfiles.append(path)

        tree = ast.parse(source)
        n_nodes = sum(1 for _ in ast.walk(tree))
        n_lines = len(source.strip().splitlines())
        n_funcs = sum(1 for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        n_classes = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))

        # Baseline: raw ast.parse time
        t_ast = time.perf_counter()
        for _ in range(100):
            ast.parse(source)
        ast_us = (time.perf_counter() - t_ast) / 100 * 1e6

        # `jugeo prove` — get coordinates & morphisms from formal verification
        t0 = time.perf_counter()
        prove_objs = run_jugeo("prove", path)
        prove_wall_s = time.perf_counter() - t0

        prove = prove_objs[0] if prove_objs else {}
        formal = prove_objs[1] if len(prove_objs) > 1 else {}

        finfo = (prove.get("files") or [{}])[0]
        cat = formal.get("formal_verification", {}).get("category_structure", {})

        # `jugeo encode` — get site topology
        t0 = time.perf_counter()
        encode_objs = run_jugeo("encode", path)
        encode_wall_s = time.perf_counter() - t0

        encode_data = encode_objs[0] if encode_objs else {}

        row = {
            "program": name,
            "lines": n_lines,
            "ast_nodes": n_nodes,
            "functions": n_funcs,
            "classes": n_classes,
            # From `jugeo prove`
            "prove_coordinates": finfo.get("coordinates", 0),
            "prove_propositions": finfo.get("propositions_total", 0),
            "prove_n_objects": cat.get("n_objects", 0),
            "prove_n_morphisms": cat.get("n_morphisms", 0),
            "prove_wall_s": round(prove_wall_s, 4),
            # From `jugeo encode`
            "encode_coordinates": encode_data.get("coordinates", 0),
            "encode_morphisms": encode_data.get("morphisms", 0),
            "encode_covers": encode_data.get("covering_families", 0),
            "encode_wall_s": round(encode_wall_s, 4),
            # AST parse baseline
            "ast_parse_us": round(ast_us, 1),
            # Derived ratios
            "coords_per_line": round(
                encode_data.get("coordinates", 0) / max(n_lines, 1), 4),
            "coords_per_ast_node": round(
                encode_data.get("coordinates", 0) / max(n_nodes, 1), 4),
            "morphs_per_coord": round(
                encode_data.get("morphisms", 0)
                / max(encode_data.get("coordinates", 1), 1), 4),
            "morphs_per_func": round(
                encode_data.get("morphisms", 0) / max(n_funcs, 1), 4),
        }
        results.append(row)

    # ── Print main table ─────────────────────────────────────────────────
    print(f"SITE SCALING — {len(results)} programs")
    print(
        f"{'Program':<24} {'Lines':>5} {'AST':>5} {'Funcs':>5} {'Cls':>3} "
        f"{'Coords':>7} {'Morphs':>7} {'Covers':>7} {'prove(s)':>9} {'encode(s)':>9}"
    )
    print("-" * 100)
    for r in results:
        print(
            f"{r['program']:<24} {r['lines']:>5} {r['ast_nodes']:>5} "
            f"{r['functions']:>5} {r['classes']:>3} "
            f"{r['encode_coordinates']:>7} {r['encode_morphisms']:>7} "
            f"{r['encode_covers']:>7} {r['prove_wall_s']:>9.4f} "
            f"{r['encode_wall_s']:>9.4f}"
        )

    # ── Scaling ratios ───────────────────────────────────────────────────
    print()
    print("SCALING RATIOS:")
    print(f"  {'Program':<24} {'coords/line':>11} {'coords/AST':>11} "
          f"{'morphs/coord':>13} {'morphs/func':>12}")
    print(f"  {'-'*72}")
    for r in results:
        print(
            f"  {r['program']:<24} {r['coords_per_line']:>11.4f} "
            f"{r['coords_per_ast_node']:>11.4f} "
            f"{r['morphs_per_coord']:>13.4f} "
            f"{r['morphs_per_func']:>12.4f}"
        )

    # ── Size buckets ─────────────────────────────────────────────────────
    buckets = {"small": [], "medium": [], "large": [], "xlarge": []}
    for r in results:
        if r["lines"] <= 25:
            buckets["small"].append(r)
        elif r["lines"] <= 35:
            buckets["medium"].append(r)
        elif r["lines"] <= 50:
            buckets["large"].append(r)
        else:
            buckets["xlarge"].append(r)

    print()
    print("SIZE BUCKET AVERAGES:")
    print(f"  {'Bucket':<10} {'Count':>5} {'Avg Lines':>10} {'Avg Coords':>11} "
          f"{'Avg Morphs':>11} {'Avg prove(s)':>13} {'Avg encode(s)':>14}")
    print(f"  {'-'*76}")
    for bname, items in buckets.items():
        if not items:
            continue
        n = len(items)
        avg_lines = sum(r["lines"] for r in items) / n
        avg_coords = sum(r["encode_coordinates"] for r in items) / n
        avg_morphs = sum(r["encode_morphisms"] for r in items) / n
        avg_prove = sum(r["prove_wall_s"] for r in items) / n
        avg_encode = sum(r["encode_wall_s"] for r in items) / n
        print(f"  {bname:<10} {n:>5} {avg_lines:>10.1f} {avg_coords:>11.1f} "
              f"{avg_morphs:>11.1f} {avg_prove:>13.4f} {avg_encode:>14.4f}")

    # ── Category structure from prove ────────────────────────────────────
    print()
    print("CATEGORY STRUCTURE (from `jugeo prove` formal_verification):")
    print(f"  {'Program':<24} {'n_objects':>10} {'n_morphisms':>12}")
    print(f"  {'-'*48}")
    for r in results:
        print(
            f"  {r['program']:<24} {r['prove_n_objects']:>10} "
            f"{r['prove_n_morphisms']:>12}"
        )

    # ── Construction overhead vs ast.parse ───────────────────────────────
    print()
    print("CONSTRUCTION OVERHEAD vs ast.parse:")
    for r in results[:20]:  # first 20 for readability
        overhead = (r["encode_wall_s"] * 1e6) / max(r["ast_parse_us"], 0.1)
        print(
            f"  {r['program']:<24} encode={r['encode_wall_s']*1e6:>8.0f}us  "
            f"ast.parse={r['ast_parse_us']:>6.1f}us  "
            f"overhead={overhead:.1f}x"
        )
    if len(results) > 20:
        print(f"  ... and {len(results) - 20} more programs")

    # ── Summary statistics ───────────────────────────────────────────────
    n = len(results)
    avg_lines = sum(r["lines"] for r in results) / n
    avg_coords = sum(r["encode_coordinates"] for r in results) / n
    avg_morphs = sum(r["encode_morphisms"] for r in results) / n
    avg_prove = sum(r["prove_wall_s"] for r in results) / n
    avg_encode = sum(r["encode_wall_s"] for r in results) / n

    print(f"\nSUMMARY ({n} programs):")
    print(f"  Average lines:       {avg_lines:.1f}")
    print(f"  Average coordinates: {avg_coords:.1f}")
    print(f"  Average morphisms:   {avg_morphs:.1f}")
    print(f"  Average prove time:  {avg_prove:.4f}s")
    print(f"  Average encode time: {avg_encode:.4f}s")

    # ── Save JSON ────────────────────────────────────────────────────────
    outpath = os.path.join(os.path.dirname(__file__), "results_paper01.json")
    with open(outpath, "w") as f:
        json.dump(
            {
                "experiment": "site_scaling",
                "paper": 1,
                "n_programs": n,
                "results": results,
                "bucket_summary": {
                    bname: {
                        "count": len(items),
                        "avg_lines": round(sum(r["lines"] for r in items) / max(len(items), 1), 1),
                        "avg_coordinates": round(sum(r["encode_coordinates"] for r in items) / max(len(items), 1), 1),
                        "avg_morphisms": round(sum(r["encode_morphisms"] for r in items) / max(len(items), 1), 1),
                    }
                    for bname, items in buckets.items()
                    if items
                },
                "summary": {
                    "avg_lines": round(avg_lines, 1),
                    "avg_coordinates": round(avg_coords, 1),
                    "avg_morphisms": round(avg_morphs, 1),
                    "avg_prove_s": round(avg_prove, 4),
                    "avg_encode_s": round(avg_encode, 4),
                },
            },
            f,
            indent=2,
        )
    print(f"\nResults -> {outpath}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
