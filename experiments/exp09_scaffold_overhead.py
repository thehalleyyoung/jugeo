#!/usr/bin/env python3
"""Paper 9 Experiment -- Verification Certificates That Ship With Code.

Hypothesis: JuGeo proof certificates (scaffolds) add bounded overhead and
enable O(1) re-verification.

Methodology: Write programs of varying sizes, run jugeo prove on each,
extract certificate data, measure initial vs re-verification time,
compare strategies, and measure overhead against an AST-only baseline.

Every number is produced by the jugeo CLI (subprocess).
Re-run: python3 experiments/exp09_scaffold_overhead.py
"""
import subprocess, json, os, tempfile, time, random, ast, statistics

random.seed(42)
ROOT = os.path.join(os.path.dirname(__file__), "..")

# -- CLI helpers -----------------------------------------------------------

def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
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


LITERATURE_BASELINES = {
    "Fstar_to_OCaml": {
        "description": "F* -> OCaml extraction: ~1.5x LOC overhead",
        "overhead_ratio_loc": 1.5,
        "measured": False,
        "cite": "Protzenko et al., ICFP 2017",
    },
    "LEAN_to_C": {
        "description": "LEAN -> C: ~2x LOC overhead",
        "overhead_ratio_loc": 2.0,
        "measured": False,
        "cite": "Lean 4 documentation",
    },
    "Coq_to_OCaml": {
        "description": "Coq -> OCaml extraction: ~1.8x LOC overhead",
        "overhead_ratio_loc": 1.8,
        "measured": False,
        "cite": "Letouzey, Types 2002",
    },
}

# -- 100 Benchmark Programs ------------------------------------------------

PROGRAMS = {
    "factorial_iterative": '''def factorial(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

def double_factorial(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    i = n
    while i > 0:
        result *= i
        i -= 2
    return result

def falling_factorial(n, k):
    result = 1
    for i in range(k):
        result *= (n - i)
    return result
''',
    "fibonacci_variants": '''def fibonacci(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

def fibonacci_list(n):
    if n <= 0:
        return []
    fibs = [0]
    if n == 1:
        return fibs
    fibs.append(1)
    for i in range(2, n):
        fibs.append(fibs[-1] + fibs[-2])
    return fibs

def is_fibonacci(num):
    if num < 0:
        return False
    a, b = 0, 1
    while b < num:
        a, b = b, a + b
    return b == num or num == 0
''',
    "binary_search": '''def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1

def lower_bound(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo

def upper_bound(arr, target):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo
''',
    "merge_sort": '''def merge_sort(arr):
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)

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

def is_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True
''',
    "quicksort": '''def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)

def partition(arr, low, high):
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1

def quicksort_inplace(arr, low=None, high=None):
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low < high:
        pi = partition(arr, low, high)
        quicksort_inplace(arr, low, pi - 1)
        quicksort_inplace(arr, pi + 1, high)
''',
    "heap_sort": '''def heap_sort(arr):
    n = len(arr)
    for i in range(n // 2 - 1, -1, -1):
        heapify(arr, n, i)
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        heapify(arr, i, 0)
    return arr

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

def find_kth_largest(arr, k):
    sorted_arr = heap_sort(list(arr))
    if k <= 0 or k > len(sorted_arr):
        return None
    return sorted_arr[-k]
''',
    "gcd_lcm": '''def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)

def gcd_list(numbers):
    result = numbers[0]
    for num in numbers[1:]:
        result = gcd(result, num)
    return result

def lcm_list(numbers):
    result = numbers[0]
    for num in numbers[1:]:
        result = lcm(result, num)
    return result

def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = extended_gcd(b % a, a)
    return g, y - (b // a) * x, x

def coprime(a, b):
    return gcd(a, b) == 1
''',
    "prime_utils": '''def is_prime(n):
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

def primes_up_to(n):
    sieve = [True] * (n + 1)
    sieve[0] = sieve[1] = False
    for i in range(2, int(n ** 0.5) + 1):
        if sieve[i]:
            for j in range(i * i, n + 1, i):
                sieve[j] = False
    return [i for i, v in enumerate(sieve) if v]

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
    "power_mod": '''def power(base, exp):
    if exp < 0:
        return 1.0 / power(base, -exp)
    result = 1
    while exp > 0:
        if exp % 2 == 1:
            result *= base
        base *= base
        exp //= 2
    return result

def mod_power(base, exp, mod):
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp = exp >> 1
        base = (base * base) % mod
    return result

def mod_inverse(a, mod):
    g, x, _ = _ext_gcd(a, mod)
    if g != 1:
        return None
    return x % mod

def _ext_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = _ext_gcd(b % a, a)
    return g, y - (b // a) * x, x
''',
    "combinatorics": '''def permutations_count(n, r):
    if r > n or r < 0:
        return 0
    result = 1
    for i in range(n, n - r, -1):
        result *= i
    return result

def combinations_count(n, r):
    if r > n or r < 0:
        return 0
    if r > n - r:
        r = n - r
    result = 1
    for i in range(r):
        result = result * (n - i) // (i + 1)
    return result

def generate_permutations(items):
    if len(items) <= 1:
        return [list(items)]
    result = []
    for i, item in enumerate(items):
        rest = items[:i] + items[i+1:]
        for perm in generate_permutations(rest):
            result.append([item] + perm)
    return result

def pascal_triangle_row(n):
    row = [1]
    for k in range(1, n + 1):
        row.append(row[-1] * (n - k + 1) // k)
    return row
''',
    "dijkstra_shortest_path": '''def dijkstra(graph, start):
    import heapq
    dist = {start: 0}
    pq = [(0, start)]
    visited = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist

def shortest_path(graph, start, end):
    import heapq
    dist = {start: 0}
    prev = {start: None}
    pq = [(0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == end:
            path = []
            while u is not None:
                path.append(u)
                u = prev[u]
            return list(reversed(path))
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    return []
''',
    "lru_cache_impl": '''class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key in self.cache:
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key]
        return -1

    def put(self, key, value):
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            evicted = self.order.pop(0)
            del self.cache[evicted]
        self.cache[key] = value
        self.order.append(key)

    def size(self):
        return len(self.cache)

    def contains(self, key):
        return key in self.cache

    def keys(self):
        return list(self.order)
''',
    "linked_list_operations": '''class Node:
    def __init__(self, val, nxt=None):
        self.val = val
        self.nxt = nxt

class LinkedList:
    def __init__(self):
        self.head = None
        self._size = 0

    def push(self, val):
        self.head = Node(val, self.head)
        self._size += 1

    def pop(self):
        if self.head is None:
            raise IndexError("pop from empty list")
        val = self.head.val
        self.head = self.head.nxt
        self._size -= 1
        return val

    def __len__(self):
        return self._size

    def reverse(self):
        prev = None
        cur = self.head
        while cur:
            nxt = cur.nxt
            cur.nxt = prev
            prev = cur
            cur = nxt
        self.head = prev

    def to_list(self):
        result = []
        cur = self.head
        while cur:
            result.append(cur.val)
            cur = cur.nxt
        return result
''',
    "binary_tree_ops": '''class TreeNode:
    def __init__(self, val, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def insert_bst(root, val):
    if root is None:
        return TreeNode(val)
    if val < root.val:
        root.left = insert_bst(root.left, val)
    else:
        root.right = insert_bst(root.right, val)
    return root

def inorder(root):
    if root is None:
        return []
    return inorder(root.left) + [root.val] + inorder(root.right)

def tree_height(root):
    if root is None:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))

def count_nodes(root):
    if root is None:
        return 0
    return 1 + count_nodes(root.left) + count_nodes(root.right)

def search_bst(root, val):
    if root is None:
        return False
    if val == root.val:
        return True
    if val < root.val:
        return search_bst(root.left, val)
    return search_bst(root.right, val)
''',
    "hash_table_impl": '''class HashTable:
    def __init__(self, size=64):
        self._size = size
        self._buckets = [[] for _ in range(size)]
        self._count = 0

    def _hash(self, key):
        h = 0
        for ch in str(key):
            h = (h * 31 + ord(ch)) % self._size
        return h

    def put(self, key, value):
        idx = self._hash(key)
        bucket = self._buckets[idx]
        for i, (k, v) in enumerate(bucket):
            if k == key:
                bucket[i] = (key, value)
                return
        bucket.append((key, value))
        self._count += 1

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self._buckets[idx]:
            if k == key:
                return v
        return default

    def remove(self, key):
        idx = self._hash(key)
        bucket = self._buckets[idx]
        for i, (k, v) in enumerate(bucket):
            if k == key:
                bucket.pop(i)
                self._count -= 1
                return True
        return False

    def __len__(self):
        return self._count

    def keys(self):
        result = []
        for bucket in self._buckets:
            for k, v in bucket:
                result.append(k)
        return result
''',
    "deque_implementation": '''class Deque:
    def __init__(self):
        self._data = []

    def push_front(self, item):
        self._data.insert(0, item)

    def push_back(self, item):
        self._data.append(item)

    def pop_front(self):
        if not self._data:
            raise IndexError("pop from empty deque")
        return self._data.pop(0)

    def pop_back(self):
        if not self._data:
            raise IndexError("pop from empty deque")
        return self._data.pop()

    def peek_front(self):
        if not self._data:
            return None
        return self._data[0]

    def peek_back(self):
        if not self._data:
            return None
        return self._data[-1]

    def size(self):
        return len(self._data)

    def is_empty(self):
        return len(self._data) == 0

    def to_list(self):
        return list(self._data)

    def clear(self):
        self._data.clear()
''',
    "union_find": '''class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.components = n

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
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.components -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def component_count(self):
        return self.components

    def component_sizes(self):
        sizes = {}
        for i in range(len(self.parent)):
            root = self.find(i)
            sizes[root] = sizes.get(root, 0) + 1
        return list(sizes.values())
''',
    "trie_impl": '''class TrieNode:
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

    def count_words(self):
        return self._count(self.root)

    def _count(self, node):
        total = 1 if node.is_end else 0
        for child in node.children.values():
            total += self._count(child)
        return total
''',
    "min_heap": '''class MinHeap:
    def __init__(self):
        self._data = []

    def push(self, val):
        self._data.append(val)
        self._sift_up(len(self._data) - 1)

    def pop(self):
        if not self._data:
            raise IndexError("pop from empty heap")
        self._swap(0, len(self._data) - 1)
        val = self._data.pop()
        if self._data:
            self._sift_down(0)
        return val

    def peek(self):
        return self._data[0] if self._data else None

    def _sift_up(self, idx):
        while idx > 0:
            parent = (idx - 1) // 2
            if self._data[idx] < self._data[parent]:
                self._swap(idx, parent)
                idx = parent
            else:
                break

    def _sift_down(self, idx):
        n = len(self._data)
        while True:
            smallest = idx
            left = 2 * idx + 1
            right = 2 * idx + 2
            if left < n and self._data[left] < self._data[smallest]:
                smallest = left
            if right < n and self._data[right] < self._data[smallest]:
                smallest = right
            if smallest != idx:
                self._swap(idx, smallest)
                idx = smallest
            else:
                break

    def _swap(self, i, j):
        self._data[i], self._data[j] = self._data[j], self._data[i]

    def size(self):
        return len(self._data)
''',
    "graph_algorithms": '''def bfs(graph, start):
    visited = []
    queue = [start]
    seen = {start}
    while queue:
        node = queue.pop(0)
        visited.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return visited

def dfs(graph, start):
    visited = []
    stack = [start]
    seen = set()
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        visited.append(node)
        for neighbor in reversed(graph.get(node, [])):
            if neighbor not in seen:
                stack.append(neighbor)
    return visited

def topological_sort(graph):
    in_degree = {}
    for node in graph:
        in_degree.setdefault(node, 0)
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
    queue = [n for n, d in in_degree.items() if d == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return result
''',
    "string_utils": '''def reverse_string(s):
    return s[::-1]

def is_palindrome(s):
    cleaned = "".join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]

def count_words(text):
    words = text.split()
    return len(words)

def capitalize_words(text):
    words = text.split()
    return " ".join(w[0].upper() + w[1:] if w else "" for w in words)

def truncate(text, max_len, suffix="..."):
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix

def repeat_string(s, n):
    result = ""
    for _ in range(n):
        result += s
    return result

def char_frequency(text):
    freq = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    return freq
''',
    "string_matching": '''def find_all(text, pattern):
    positions = []
    start = 0
    while True:
        idx = text.find(pattern, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions

def count_occurrences(text, pattern):
    return len(find_all(text, pattern))

def replace_all(text, old, new):
    result = []
    i = 0
    while i < len(text):
        if text[i:i+len(old)] == old:
            result.append(new)
            i += len(old)
        else:
            result.append(text[i])
            i += 1
    return "".join(result)

def longest_common_prefix(strings):
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix
''',
    "text_analysis": '''def word_frequency(text):
    words = text.lower().split()
    freq = {}
    for word in words:
        clean = "".join(c for c in word if c.isalnum())
        if clean:
            freq[clean] = freq.get(clean, 0) + 1
    return freq

def most_common_words(text, n=10):
    freq = word_frequency(text)
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return sorted_words[:n]

def sentence_count(text):
    count = 0
    for ch in text:
        if ch in ".!?":
            count += 1
    return max(count, 1)

def average_word_length(text):
    words = text.split()
    if not words:
        return 0.0
    total = sum(len(w) for w in words)
    return total / len(words)

def readability_score(text):
    words = len(text.split())
    sentences = sentence_count(text)
    syllables = sum(1 for c in text.lower() if c in "aeiou")
    if words == 0 or sentences == 0:
        return 0.0
    return 206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words)
''',
    "matrix_operations": '''def create_matrix(rows, cols, fill=0):
    return [[fill] * cols for _ in range(rows)]

def matrix_add(a, b):
    rows = len(a)
    cols = len(a[0])
    result = create_matrix(rows, cols)
    for i in range(rows):
        for j in range(cols):
            result[i][j] = a[i][j] + b[i][j]
    return result

def matrix_multiply(a, b):
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = create_matrix(rows_a, cols_b)
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result

def matrix_transpose(m):
    rows = len(m)
    cols = len(m[0])
    result = create_matrix(cols, rows)
    for i in range(rows):
        for j in range(cols):
            result[j][i] = m[i][j]
    return result

def matrix_trace(m):
    n = min(len(m), len(m[0]))
    return sum(m[i][i] for i in range(n))
''',
    "vector_operations": '''def vector_add(a, b):
    return [a[i] + b[i] for i in range(len(a))]

def vector_subtract(a, b):
    return [a[i] - b[i] for i in range(len(a))]

def dot_product(a, b):
    return sum(a[i] * b[i] for i in range(len(a)))

def vector_magnitude(v):
    return sum(x * x for x in v) ** 0.5

def vector_normalize(v):
    mag = vector_magnitude(v)
    if mag == 0:
        return [0.0] * len(v)
    return [x / mag for x in v]

def cross_product(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]

def vector_angle(a, b):
    import math
    dot = dot_product(a, b)
    mag_a = vector_magnitude(a)
    mag_b = vector_magnitude(b)
    if mag_a == 0 or mag_b == 0:
        return 0.0
    cos_angle = max(-1, min(1, dot / (mag_a * mag_b)))
    return math.acos(cos_angle)
''',
    "geometry_2d": '''import math as _math

def distance_2d(x1, y1, x2, y2):
    return _math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

def triangle_area(x1, y1, x2, y2, x3, y3):
    return abs((x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)) / 2.0)

def circle_area(radius):
    return _math.pi * radius * radius

def circle_circumference(radius):
    return 2 * _math.pi * radius

def point_in_circle(px, py, cx, cy, r):
    return distance_2d(px, py, cx, cy) <= r

def line_length(points):
    total = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        total += distance_2d(x1, y1, x2, y2)
    return total

def polygon_area(vertices):
    n = len(vertices)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0

def midpoint(x1, y1, x2, y2):
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
''',
    "statistics_basic": '''def mean(data):
    if not data:
        return 0.0
    return sum(data) / len(data)

def median(data):
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return sorted_data[n // 2]
    return (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2.0

def mode(data):
    freq = {}
    for val in data:
        freq[val] = freq.get(val, 0) + 1
    max_count = max(freq.values()) if freq else 0
    modes = [k for k, v in freq.items() if v == max_count]
    return modes

def variance(data):
    if len(data) < 2:
        return 0.0
    avg = mean(data)
    return sum((x - avg) ** 2 for x in data) / (len(data) - 1)

def std_dev(data):
    return variance(data) ** 0.5

def percentile(data, p):
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]
''',
    "number_conversion": '''def decimal_to_binary(n):
    if n == 0:
        return "0"
    negative = n < 0
    n = abs(n)
    bits = []
    while n > 0:
        bits.append(str(n % 2))
        n //= 2
    result = "".join(reversed(bits))
    return "-" + result if negative else result

def binary_to_decimal(binary_str):
    negative = binary_str.startswith("-")
    if negative:
        binary_str = binary_str[1:]
    result = 0
    for bit in binary_str:
        result = result * 2 + int(bit)
    return -result if negative else result

def decimal_to_hex(n):
    if n == 0:
        return "0"
    hex_chars = "0123456789abcdef"
    negative = n < 0
    n = abs(n)
    result = []
    while n > 0:
        result.append(hex_chars[n % 16])
        n //= 16
    s = "".join(reversed(result))
    return "-" + s if negative else s

def decimal_to_octal(n):
    if n == 0:
        return "0"
    negative = n < 0
    n = abs(n)
    result = []
    while n > 0:
        result.append(str(n % 8))
        n //= 8
    s = "".join(reversed(result))
    return "-" + s if negative else s
''',
    "temperature_converter": '''def celsius_to_fahrenheit(c):
    return c * 9.0 / 5.0 + 32.0

def fahrenheit_to_celsius(f):
    return (f - 32.0) * 5.0 / 9.0

def celsius_to_kelvin(c):
    return c + 273.15

def kelvin_to_celsius(k):
    return k - 273.15

def fahrenheit_to_kelvin(f):
    return celsius_to_kelvin(fahrenheit_to_celsius(f))

def kelvin_to_fahrenheit(k):
    return celsius_to_fahrenheit(kelvin_to_celsius(k))

def convert_temperature(value, from_unit, to_unit):
    converters = {
        ("C", "F"): celsius_to_fahrenheit,
        ("F", "C"): fahrenheit_to_celsius,
        ("C", "K"): celsius_to_kelvin,
        ("K", "C"): kelvin_to_celsius,
        ("F", "K"): fahrenheit_to_kelvin,
        ("K", "F"): kelvin_to_fahrenheit,
    }
    key = (from_unit.upper(), to_unit.upper())
    if key in converters:
        return round(converters[key](value), 2)
    if from_unit.upper() == to_unit.upper():
        return value
    return None

def is_absolute_zero(value, unit):
    if unit.upper() == "K":
        return value <= 0
    if unit.upper() == "C":
        return value <= -273.15
    return value <= -459.67
''',
    "distance_converter": '''def km_to_miles(km):
    return km * 0.621371

def miles_to_km(miles):
    return miles / 0.621371

def meters_to_feet(meters):
    return meters * 3.28084

def feet_to_meters(feet):
    return feet / 3.28084

def inches_to_cm(inches):
    return inches * 2.54

def cm_to_inches(cm):
    return cm / 2.54

def convert_distance(value, from_unit, to_unit):
    to_meters = {
        "m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
        "mi": 1609.344, "ft": 0.3048, "in": 0.0254, "yd": 0.9144,
    }
    from_factor = to_meters.get(from_unit.lower())
    to_factor = to_meters.get(to_unit.lower())
    if from_factor is None or to_factor is None:
        return None
    meters = value * from_factor
    return round(meters / to_factor, 6)

def format_distance(value, unit):
    if value >= 1000 and unit == "m":
        return "{:.2f} km".format(value / 1000)
    return "{:.2f} {}".format(value, unit)
''',
    "email_validator": '''def validate_email(email):
    if not isinstance(email, str):
        return False
    email = email.strip()
    if not email:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    if local.startswith(".") or local.endswith("."):
        return False
    for ch in local:
        if not (ch.isalnum() or ch in "._-+"):
            return False
    domain_parts = domain.split(".")
    for part in domain_parts:
        if not part:
            return False
    return True

def normalize_email(email):
    email = email.strip().lower()
    parts = email.split("@")
    if len(parts) == 2 and "+" in parts[0]:
        parts[0] = parts[0][:parts[0].index("+")]
    return "@".join(parts)
''',
    "json_like_parser": '''def parse_value(text, pos):
    text = text.strip()
    if pos >= len(text):
        return None, pos
    ch = text[pos]
    if ch == '"':
        return parse_string(text, pos)
    if ch in "-0123456789":
        return parse_number(text, pos)
    if text[pos:pos+4] == "true":
        return True, pos + 4
    if text[pos:pos+5] == "false":
        return False, pos + 5
    if text[pos:pos+4] == "null":
        return None, pos + 4
    return None, pos

def parse_string(text, pos):
    if text[pos] != '"':
        return "", pos
    pos += 1
    result = []
    while pos < len(text) and text[pos] != '"':
        result.append(text[pos])
        pos += 1
    return "".join(result), pos + 1

def parse_number(text, pos):
    start = pos
    if pos < len(text) and text[pos] == "-":
        pos += 1
    while pos < len(text) and text[pos].isdigit():
        pos += 1
    if pos < len(text) and text[pos] == ".":
        pos += 1
        while pos < len(text) and text[pos].isdigit():
            pos += 1
        return float(text[start:pos]), pos
    return int(text[start:pos]), pos
''',
    "data_validator": '''def validate_required(data, fields):
    missing = []
    for field in fields:
        if field not in data or data[field] is None:
            missing.append(field)
    return missing

def validate_types(data, schema):
    errors = []
    for field, expected_type in schema.items():
        if field in data and not isinstance(data[field], expected_type):
            errors.append("{} should be {}".format(field, expected_type.__name__))
    return errors

def validate_range(value, min_val=None, max_val=None):
    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True

def validate_length(value, min_len=None, max_len=None):
    length = len(value)
    if min_len is not None and length < min_len:
        return False
    if max_len is not None and length > max_len:
        return False
    return True

def validate_pattern(value, allowed_chars):
    for ch in value:
        if ch not in allowed_chars:
            return False
    return True
''',
    "date_utils": '''def is_leap_year(year):
    if year % 400 == 0:
        return True
    if year % 100 == 0:
        return False
    return year % 4 == 0

def days_in_month(year, month):
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    return days[month]

def is_valid_date(year, month, day):
    if month < 1 or month > 12:
        return False
    if day < 1 or day > days_in_month(year, month):
        return False
    return True

def day_of_year(year, month, day):
    total = 0
    for m in range(1, month):
        total += days_in_month(year, m)
    return total + day

def days_between(y1, m1, d1, y2, m2, d2):
    def to_days(y, m, d):
        total = y * 365 + y // 4 - y // 100 + y // 400
        for month in range(1, m):
            total += days_in_month(y, month)
        return total + d
    return abs(to_days(y2, m2, d2) - to_days(y1, m1, d1))

def add_days(year, month, day, n):
    for _ in range(n):
        day += 1
        if day > days_in_month(year, month):
            day = 1
            month += 1
            if month > 12:
                month = 1
                year += 1
    return year, month, day
''',
    "currency_formatter": '''def format_currency(amount, symbol="$", decimals=2):
    if amount < 0:
        return "-" + symbol + format_number(abs(amount), decimals)
    return symbol + format_number(amount, decimals)

def format_number(n, decimals=2):
    integer_part = int(n)
    frac = round(n - integer_part, decimals)
    int_str = str(integer_part)
    groups = []
    while int_str:
        groups.append(int_str[-3:])
        int_str = int_str[:-3]
    formatted = ",".join(reversed(groups))
    if decimals > 0:
        frac_str = str(round(frac, decimals))[2:]
        frac_str = frac_str.ljust(decimals, "0")
        return formatted + "." + frac_str
    return formatted

def parse_currency(text):
    cleaned = ""
    for ch in text:
        if ch.isdigit() or ch == "." or ch == "-":
            cleaned += ch
    if cleaned:
        return float(cleaned)
    return 0.0

def exchange_rate_convert(amount, rate):
    return round(amount * rate, 2)

def format_accounting(amount, symbol="$"):
    if amount < 0:
        return "(" + symbol + format_number(abs(amount), 2) + ")"
    return symbol + format_number(amount, 2)
''',
    "slug_generator": '''def generate_slug(text):
    text = text.lower().strip()
    result = []
    prev_dash = False
    for ch in text:
        if ch.isalnum():
            result.append(ch)
            prev_dash = False
        elif ch in " -_":
            if not prev_dash and result:
                result.append("-")
                prev_dash = True
    slug = "".join(result).rstrip("-")
    return slug

def unique_slug(text, existing):
    base = generate_slug(text)
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
    return " ".join(w.capitalize() for w in words if w)

def truncate_slug(slug, max_len=50):
    if len(slug) <= max_len:
        return slug
    truncated = slug[:max_len]
    last_dash = truncated.rfind("-")
    if last_dash > max_len // 2:
        truncated = truncated[:last_dash]
    return truncated.rstrip("-")
''',
    "caesar_cipher": '''def encrypt(text, shift=3):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            shifted = (ord(ch) - base + shift) % 26
            result.append(chr(base + shifted))
        else:
            result.append(ch)
    return "".join(result)

def decrypt(text, shift=3):
    return encrypt(text, -shift)

def brute_force(text):
    results = {}
    for shift in range(26):
        results[shift] = decrypt(text, shift)
    return results

def frequency_analysis(text):
    freq = {}
    total = 0
    for ch in text.upper():
        if ch.isalpha():
            freq[ch] = freq.get(ch, 0) + 1
            total += 1
    if total == 0:
        return {}
    return {ch: round(count / total * 100, 2) for ch, count in sorted(freq.items())}

def detect_shift(ciphertext):
    freq = frequency_analysis(ciphertext)
    if not freq:
        return 0
    most_common = max(freq, key=freq.get)
    return (ord(most_common) - ord("E")) % 26
''',
    "run_length_encoding": '''def rle_encode(data):
    if not data:
        return ""
    result = []
    current = data[0]
    count = 1
    for i in range(1, len(data)):
        if data[i] == current:
            count += 1
        else:
            if count > 1:
                result.append(str(count) + current)
            else:
                result.append(current)
            current = data[i]
            count = 1
    if count > 1:
        result.append(str(count) + current)
    else:
        result.append(current)
    return "".join(result)

def rle_decode(encoded):
    result = []
    i = 0
    while i < len(encoded):
        num_str = ""
        while i < len(encoded) and encoded[i].isdigit():
            num_str += encoded[i]
            i += 1
        if i < len(encoded):
            ch = encoded[i]
            i += 1
            count = int(num_str) if num_str else 1
            result.append(ch * count)
    return "".join(result)

def compression_ratio(original, encoded):
    if not original:
        return 0.0
    return 1.0 - len(encoded) / len(original)
''',
    "roman_numerals": '''def to_roman(num):
    if num <= 0 or num > 3999:
        return ""
    vals = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syms = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    result = []
    for i, val in enumerate(vals):
        while num >= val:
            result.append(syms[i])
            num -= val
    return "".join(result)

def from_roman(roman):
    roman = roman.upper().strip()
    values = {"I": 1, "V": 5, "X": 10, "L": 50,
              "C": 100, "D": 500, "M": 1000}
    result = 0
    prev = 0
    for ch in reversed(roman):
        curr = values.get(ch, 0)
        if curr < prev:
            result -= curr
        else:
            result += curr
        prev = curr
    return result

def is_valid_roman(roman):
    try:
        val = from_roman(roman)
        return val > 0 and to_roman(val) == roman.upper()
    except (KeyError, ValueError):
        return False
''',
    "checksum_utils": '''def luhn_check(number):
    digits = [int(d) for d in str(number) if d.isdigit()]
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0

def compute_checksum(data):
    total = 0
    for ch in data:
        total = (total + ord(ch)) % 256
    return total

def adler32(data):
    a = 1
    b = 0
    for ch in data:
        a = (a + ord(ch)) % 65521
        b = (b + a) % 65521
    return (b << 16) | a

def verify_checksum(data, expected):
    return compute_checksum(data) == expected

def isbn10_check_digit(isbn9):
    total = 0
    for i, ch in enumerate(isbn9):
        total += int(ch) * (10 - i)
    remainder = total % 11
    check = 11 - remainder
    if check == 10:
        return "X"
    if check == 11:
        return "0"
    return str(check)
''',
    "stack_impl": '''class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        if not self._items:
            return None
        return self._items[-1]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)

    def clear(self):
        self._items.clear()

    def to_list(self):
        return list(self._items)

    def contains(self, item):
        return item in self._items
''',
    "queue_impl": '''class Queue:
    def __init__(self, max_size=0):
        self._items = []
        self._max_size = max_size

    def enqueue(self, item):
        if self._max_size > 0 and len(self._items) >= self._max_size:
            return False
        self._items.append(item)
        return True

    def dequeue(self):
        if not self._items:
            raise IndexError("dequeue from empty queue")
        return self._items.pop(0)

    def peek(self):
        if not self._items:
            return None
        return self._items[0]

    def is_empty(self):
        return len(self._items) == 0

    def is_full(self):
        return self._max_size > 0 and len(self._items) >= self._max_size

    def size(self):
        return len(self._items)

    def clear(self):
        self._items.clear()

    def to_list(self):
        return list(self._items)
''',
    "event_system": '''class EventBus:
    def __init__(self):
        self._handlers = {}
        self._history = []

    def subscribe(self, event, handler):
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    def unsubscribe(self, event, handler):
        if event in self._handlers:
            self._handlers[event] = [
                h for h in self._handlers[event] if h != handler
            ]

    def publish(self, event, data=None):
        self._history.append({"event": event, "data": data})
        for handler in self._handlers.get(event, []):
            handler(data)

    def has_subscribers(self, event):
        return event in self._handlers and len(self._handlers[event]) > 0

    def subscriber_count(self, event):
        return len(self._handlers.get(event, []))

    def event_history(self):
        return list(self._history)

    def clear_history(self):
        self._history.clear()
''',
    "state_machine": '''class StateMachine:
    def __init__(self, initial_state):
        self._state = initial_state
        self._transitions = {}
        self._history = [initial_state]

    def add_transition(self, from_state, event, to_state):
        key = (from_state, event)
        self._transitions[key] = to_state

    def trigger(self, event):
        key = (self._state, event)
        if key not in self._transitions:
            return False
        self._state = self._transitions[key]
        self._history.append(self._state)
        return True

    def current_state(self):
        return self._state

    def can_trigger(self, event):
        return (self._state, event) in self._transitions

    def available_events(self):
        events = []
        for (state, event) in self._transitions:
            if state == self._state:
                events.append(event)
        return events

    def history(self):
        return list(self._history)

    def reset(self, state=None):
        if state is None:
            state = self._history[0]
        self._state = state
        self._history = [state]
''',
    "observer_pattern": '''class Subject:
    def __init__(self):
        self._observers = []
        self._state = None

    def attach(self, observer):
        if observer not in self._observers:
            self._observers.append(observer)

    def detach(self, observer):
        self._observers = [o for o in self._observers if o != observer]

    def notify(self):
        for observer in self._observers:
            observer.update(self._state)

    def set_state(self, state):
        self._state = state
        self.notify()

    def get_state(self):
        return self._state

class Observer:
    def __init__(self, name):
        self.name = name
        self.received = []

    def update(self, state):
        self.received.append(state)

    def last_state(self):
        return self.received[-1] if self.received else None

    def update_count(self):
        return len(self.received)
''',
    "builder_pattern": '''class QueryBuilder:
    def __init__(self, table):
        self._table = table
        self._conditions = []
        self._columns = ["*"]
        self._order = None
        self._limit = None

    def select(self, *columns):
        self._columns = list(columns)
        return self

    def where(self, condition):
        self._conditions.append(condition)
        return self

    def order_by(self, column, desc=False):
        self._order = column + (" DESC" if desc else " ASC")
        return self

    def limit(self, n):
        self._limit = n
        return self

    def build(self):
        query = "SELECT " + ", ".join(self._columns)
        query += " FROM " + self._table
        if self._conditions:
            query += " WHERE " + " AND ".join(self._conditions)
        if self._order:
            query += " ORDER BY " + self._order
        if self._limit is not None:
            query += " LIMIT " + str(self._limit)
        return query

    def reset(self):
        self._conditions = []
        self._columns = ["*"]
        self._order = None
        self._limit = None
        return self
''',
    "shopping_cart": '''class ShoppingCart:
    def __init__(self):
        self._items = {}

    def add(self, product_id, name, price, qty=1):
        if product_id in self._items:
            self._items[product_id]["quantity"] += qty
        else:
            self._items[product_id] = {
                "name": name, "price": price, "quantity": qty
            }

    def remove(self, product_id):
        if product_id in self._items:
            del self._items[product_id]

    def update_qty(self, product_id, qty):
        if product_id in self._items:
            if qty <= 0:
                del self._items[product_id]
            else:
                self._items[product_id]["quantity"] = qty

    def subtotal(self):
        total = 0.0
        for item in self._items.values():
            total += item["price"] * item["quantity"]
        return round(total, 2)

    def item_count(self):
        return sum(i["quantity"] for i in self._items.values())

    def is_empty(self):
        return len(self._items) == 0

    def get_items(self):
        return dict(self._items)

    def clear(self):
        self._items.clear()
''',
    "inventory_manager": '''class Inventory:
    def __init__(self):
        self._products = {}

    def add_product(self, sku, name, price, stock=0):
        self._products[sku] = {
            "name": name, "price": price, "stock": stock
        }

    def restock(self, sku, quantity):
        if sku in self._products:
            self._products[sku]["stock"] += quantity
            return self._products[sku]["stock"]
        return -1

    def sell(self, sku, quantity):
        if sku not in self._products:
            return False
        if self._products[sku]["stock"] < quantity:
            return False
        self._products[sku]["stock"] -= quantity
        return True

    def get_product(self, sku):
        return self._products.get(sku)

    def low_stock(self, threshold=5):
        return [
            sku for sku, p in self._products.items()
            if p["stock"] < threshold
        ]

    def total_value(self):
        total = 0.0
        for p in self._products.values():
            total += p["price"] * p["stock"]
        return round(total, 2)

    def product_count(self):
        return len(self._products)

    def search(self, keyword):
        keyword = keyword.lower()
        return [
            sku for sku, p in self._products.items()
            if keyword in p["name"].lower()
        ]
''',
    "invoice_calculator": '''class Invoice:
    def __init__(self, number):
        self._number = number
        self._items = []
        self._tax_rate = 0.0
        self._discount = 0.0

    def add_item(self, desc, qty, price):
        self._items.append({
            "description": desc,
            "quantity": qty,
            "price": price,
        })

    def set_tax_rate(self, rate):
        self._tax_rate = rate

    def set_discount(self, amount):
        self._discount = amount

    def subtotal(self):
        total = 0.0
        for item in self._items:
            total += item["quantity"] * item["price"]
        return round(total, 2)

    def tax(self):
        taxable = max(0, self.subtotal() - self._discount)
        return round(taxable * self._tax_rate, 2)

    def total(self):
        sub = self.subtotal()
        after_discount = max(0, sub - self._discount)
        return round(after_discount + self.tax(), 2)

    def line_items(self):
        items = []
        for item in self._items:
            items.append({
                "description": item["description"],
                "quantity": item["quantity"],
                "price": item["price"],
                "total": round(item["quantity"] * item["price"], 2),
            })
        return items

    def summary(self):
        return {
            "number": self._number,
            "subtotal": self.subtotal(),
            "discount": self._discount,
            "tax": self.tax(),
            "total": self.total(),
        }
''',
    "password_policy": '''class PasswordPolicy:
    def __init__(self):
        self.min_length = 8
        self.require_upper = True
        self.require_lower = True
        self.require_digit = True
        self.require_special = True
        self.max_length = 128

    def validate(self, password):
        errors = []
        if len(password) < self.min_length:
            errors.append("too short")
        if len(password) > self.max_length:
            errors.append("too long")
        if self.require_upper and not any(c.isupper() for c in password):
            errors.append("needs uppercase")
        if self.require_lower and not any(c.islower() for c in password):
            errors.append("needs lowercase")
        if self.require_digit and not any(c.isdigit() for c in password):
            errors.append("needs digit")
        if self.require_special:
            specials = "!@#$%^&*()-_=+[]{}|;:,.<>?"
            if not any(c in specials for c in password):
                errors.append("needs special character")
        return errors

    def strength(self, password):
        errors = self.validate(password)
        if errors:
            return "invalid"
        score = len(password) // 4
        if score >= 4:
            return "strong"
        if score >= 2:
            return "medium"
        return "weak"

    def is_valid(self, password):
        return len(self.validate(password)) == 0
''',
    "ip_address_utils": '''def is_valid_ipv4(addr):
    parts = addr.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        num = int(part)
        if num < 0 or num > 255:
            return False
        if len(part) > 1 and part.startswith("0"):
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
    parts = [int(p) for p in addr.split(".")]
    if parts[0] == 10:
        return True
    if parts[0] == 172 and 16 <= parts[1] <= 31:
        return True
    if parts[0] == 192 and parts[1] == 168:
        return True
    return False
''',
    "color_utils": '''def hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c + c for c in h)
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (r, g, b)

def rgb_to_hex(r, g, b):
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

def rgb_to_hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        h = s = 0.0
    else:
        d = mx - mn
        s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h = (g - b) / d + (6.0 if g < b else 0.0)
        elif mx == g:
            h = (b - r) / d + 2.0
        else:
            h = (r - g) / d + 4.0
        h /= 6.0
    return (round(h * 360), round(s * 100), round(l * 100))

def complementary_color(r, g, b):
    return (255 - r, 255 - g, 255 - b)
''',
    "path_utils": '''def normalize_path(path):
    parts = path.replace("\\\\", "/").split("/")
    stack = []
    for part in parts:
        if part == "..":
            if stack and stack[-1] != "":
                stack.pop()
        elif part == "." or part == "":
            if not stack:
                stack.append(part)
        else:
            stack.append(part)
    return "/".join(stack) or "/"

def join_paths(*parts):
    if not parts:
        return ""
    result = parts[0]
    for part in parts[1:]:
        if part.startswith("/"):
            result = part
        else:
            if not result.endswith("/"):
                result += "/"
            result += part
    return normalize_path(result)

def split_extension(path):
    last_slash = max(path.rfind("/"), path.rfind("\\\\"))
    filename = path[last_slash + 1:] if last_slash >= 0 else path
    dot = filename.rfind(".")
    if dot <= 0:
        return (path, "")
    return (path[:last_slash + 1 + dot] if last_slash >= 0 else path[:dot],
            filename[dot:])

def parent_directory(path):
    path = path.rstrip("/")
    last_slash = path.rfind("/")
    if last_slash <= 0:
        return "/"
    return path[:last_slash]

def basename(path):
    path = path.rstrip("/")
    last_slash = path.rfind("/")
    return path[last_slash + 1:]
''',
    "time_utils": '''def seconds_to_hms(total_seconds):
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return (hours, minutes, seconds)

def hms_to_seconds(hours, minutes, seconds):
    return hours * 3600 + minutes * 60 + seconds

def format_duration(seconds):
    h, m, s = seconds_to_hms(int(seconds))
    parts = []
    if h > 0:
        parts.append("{}h".format(h))
    if m > 0:
        parts.append("{}m".format(m))
    if s > 0 or not parts:
        parts.append("{}s".format(s))
    return " ".join(parts)

def format_timestamp(hours, minutes, seconds):
    return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

def parse_duration(text):
    total = 0
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
        elif ch in "hHmMsS" and current:
            num = int(current)
            if ch in "hH":
                total += num * 3600
            elif ch in "mM":
                total += num * 60
            else:
                total += num
            current = ""
    if current:
        total += int(current)
    return total

def time_ago(seconds):
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return "{}m ago".format(seconds // 60)
    if seconds < 86400:
        return "{}h ago".format(seconds // 3600)
    return "{}d ago".format(seconds // 86400)
''',
    "pagination": '''class Paginator:
    def __init__(self, items, page_size=10):
        self._items = list(items)
        self._page_size = max(1, page_size)

    def total_pages(self):
        total = len(self._items)
        return (total + self._page_size - 1) // self._page_size

    def get_page(self, page_num):
        if page_num < 1 or page_num > self.total_pages():
            return []
        start = (page_num - 1) * self._page_size
        end = start + self._page_size
        return self._items[start:end]

    def has_next(self, page_num):
        return page_num < self.total_pages()

    def has_prev(self, page_num):
        return page_num > 1

    def page_info(self, page_num):
        return {
            "page": page_num,
            "total_pages": self.total_pages(),
            "total_items": len(self._items),
            "page_size": self._page_size,
            "has_next": self.has_next(page_num),
            "has_prev": self.has_prev(page_num),
        }

    def all_pages(self):
        return [
            self.get_page(i) for i in range(1, self.total_pages() + 1)
        ]
''',
    "retry_logic": '''class RetryPolicy:
    def __init__(self, max_retries=3, base_delay=1.0, backoff=2.0):
        self._max = max_retries
        self._base = base_delay
        self._backoff = backoff

    def should_retry(self, attempt):
        return attempt < self._max

    def delay(self, attempt):
        return min(self._base * (self._backoff ** attempt), 60.0)

    def execute(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(self._max + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if not self.should_retry(attempt):
                    break
        raise last_error

    def max_retries(self):
        return self._max

    def total_max_wait(self):
        total = 0.0
        for i in range(self._max):
            total += self.delay(i)
        return total

    def describe(self):
        return "RetryPolicy(max={}, base={}, backoff={})".format(
            self._max, self._base, self._backoff)
''',
    "rate_limiter": '''class TokenBucket:
    def __init__(self, capacity, refill_rate):
        self._capacity = capacity
        self._tokens = capacity
        self._refill_rate = refill_rate
        self._last_refill = 0

    def _refill(self, now):
        if self._last_refill == 0:
            self._last_refill = now
            return
        elapsed = now - self._last_refill
        tokens_to_add = elapsed * self._refill_rate
        self._tokens = min(self._capacity, self._tokens + tokens_to_add)
        self._last_refill = now

    def consume(self, tokens=1, now=0):
        self._refill(now)
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def available(self):
        return int(self._tokens)

    def capacity(self):
        return self._capacity

    def is_full(self):
        return self._tokens >= self._capacity

    def wait_time(self, tokens=1):
        if self._tokens >= tokens:
            return 0.0
        needed = tokens - self._tokens
        return needed / self._refill_rate
''',
    "config_parser": '''def parse_ini(text):
    result = {}
    current_section = "DEFAULT"
    for line in text.split("\\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            if current_section not in result:
                result[current_section] = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if current_section not in result:
                result[current_section] = {}
            result[current_section][key] = _parse_value(value)
    return result

def _parse_value(value):
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
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

def get_nested(config, path, default=None):
    parts = path.split(".")
    current = config
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current
''',
    "table_formatter": '''def format_table(headers, rows, padding=1):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    pad = " " * padding
    sep = "+" + "+".join("-" * (w + 2 * padding) for w in widths) + "+"
    lines = [sep]
    header_line = "|"
    for i, h in enumerate(headers):
        header_line += pad + str(h).ljust(widths[i]) + pad + "|"
    lines.append(header_line)
    lines.append(sep)
    for row in rows:
        row_line = "|"
        for i, cell in enumerate(row):
            if i < len(widths):
                row_line += pad + str(cell).ljust(widths[i]) + pad + "|"
        lines.append(row_line)
    lines.append(sep)
    return "\\n".join(lines)

def format_csv_row(row, delimiter=","):
    parts = []
    for cell in row:
        s = str(cell)
        if delimiter in s or '"' in s:
            s = '"' + s.replace('"', '""') + '"'
        parts.append(s)
    return delimiter.join(parts)

def format_markdown_table(headers, rows):
    lines = []
    lines.append("| " + " | ".join(str(h) for h in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\\n".join(lines)
''',
    "tree_printer": '''def print_tree(node, prefix="", is_last=True, get_children=None, get_label=None):
    if node is None:
        return []
    if get_label is None:
        get_label = str
    if get_children is None:
        get_children = lambda n: getattr(n, "children", [])
    connector = "-- " if is_last else "|-- "
    lines = [prefix + connector + get_label(node)]
    children = get_children(node)
    for i, child in enumerate(children):
        extension = "   " if is_last else "|  "
        child_lines = print_tree(
            child, prefix + extension, i == len(children) - 1,
            get_children, get_label)
        lines.extend(child_lines)
    return lines

def tree_to_dict(node, get_children=None, get_label=None):
    if node is None:
        return None
    if get_label is None:
        get_label = lambda n: str(getattr(n, "value", n))
    if get_children is None:
        get_children = lambda n: getattr(n, "children", [])
    return {
        "label": get_label(node),
        "children": [
            tree_to_dict(c, get_children, get_label)
            for c in get_children(node)
        ],
    }

def tree_depth(node, get_children=None):
    if node is None:
        return 0
    if get_children is None:
        get_children = lambda n: getattr(n, "children", [])
    children = get_children(node)
    if not children:
        return 1
    return 1 + max(tree_depth(c, get_children) for c in children)
''',
    "expression_evaluator": '''def evaluate_postfix(expression):
    stack = []
    operators = {"+", "-", "*", "/"}
    for token in expression.split():
        if token in operators:
            if len(stack) < 2:
                return None
            b = stack.pop()
            a = stack.pop()
            if token == "+":
                stack.append(a + b)
            elif token == "-":
                stack.append(a - b)
            elif token == "*":
                stack.append(a * b)
            elif token == "/":
                if b == 0:
                    return None
                stack.append(a / b)
        else:
            try:
                stack.append(float(token))
            except ValueError:
                return None
    return stack[0] if len(stack) == 1 else None

def infix_to_postfix(expr):
    prec = {"+": 1, "-": 1, "*": 2, "/": 2}
    output = []
    ops = []
    tokens = expr.replace("(", " ( ").replace(")", " ) ").split()
    for token in tokens:
        if token in prec:
            while ops and ops[-1] != "(" and prec.get(ops[-1], 0) >= prec[token]:
                output.append(ops.pop())
            ops.append(token)
        elif token == "(":
            ops.append(token)
        elif token == ")":
            while ops and ops[-1] != "(":
                output.append(ops.pop())
            if ops:
                ops.pop()
        else:
            output.append(token)
    while ops:
        output.append(ops.pop())
    return " ".join(output)
''',
    "balanced_brackets": '''def is_balanced(text):
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for ch in text:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack[-1] != ch:
                return False
            stack.pop()
    return len(stack) == 0

def find_unbalanced(text):
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for i, ch in enumerate(text):
        if ch in pairs:
            stack.append((i, pairs[ch]))
        elif ch in pairs.values():
            if not stack or stack[-1][1] != ch:
                return i
            stack.pop()
    if stack:
        return stack[0][0]
    return -1

def count_depth(text):
    max_depth = 0
    current = 0
    openers = set("([{")
    closers = set(")]}")
    for ch in text:
        if ch in openers:
            current += 1
            max_depth = max(max_depth, current)
        elif ch in closers:
            current -= 1
    return max_depth

def bracket_pairs(text):
    stack = []
    result = []
    openers = {"(": ")", "[": "]", "{": "}"}
    for i, ch in enumerate(text):
        if ch in openers:
            stack.append(i)
        elif ch in openers.values():
            if stack:
                result.append((stack.pop(), i))
    return result
''',
    "levenshtein_distance": '''def edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i-1] == b[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(dp[j], dp[j-1], prev)
            prev = temp
    return dp[n]

def similarity_ratio(a, b):
    if not a and not b:
        return 1.0
    dist = edit_distance(a, b)
    max_len = max(len(a), len(b))
    return 1.0 - dist / max_len

def find_closest(word, candidates, max_distance=3):
    results = []
    for candidate in candidates:
        dist = edit_distance(word, candidate)
        if dist <= max_distance:
            results.append((candidate, dist))
    results.sort(key=lambda x: x[1])
    return results

def is_one_edit_away(a, b):
    return edit_distance(a, b) <= 1
''',
    "text_wrapper": '''def wrap_text(text, width=72):
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    for word in words:
        if current_length + len(word) + len(current_line) > width:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
        else:
            current_line.append(word)
            current_length += len(word)
    if current_line:
        lines.append(" ".join(current_line))
    return lines

def center_text(text, width):
    if len(text) >= width:
        return text
    padding = width - len(text)
    left = padding // 2
    return " " * left + text

def indent_text(text, spaces=4):
    prefix = " " * spaces
    lines = text.split("\\n")
    return "\\n".join(prefix + line for line in lines)

def justify_text(text, width):
    words = text.split()
    if len(words) <= 1:
        return text
    total_chars = sum(len(w) for w in words)
    total_spaces = width - total_chars
    gaps = len(words) - 1
    if gaps <= 0:
        return text
    space_per_gap = total_spaces // gaps
    extra = total_spaces % gaps
    result = []
    for i, word in enumerate(words):
        result.append(word)
        if i < gaps:
            spaces = space_per_gap + (1 if i < extra else 0)
            result.append(" " * spaces)
    return "".join(result)
''',
    "binary_operations": '''def count_bits(n):
    count = 0
    n = abs(n)
    while n:
        count += n & 1
        n >>= 1
    return count

def is_power_of_two(n):
    if n <= 0:
        return False
    return (n & (n - 1)) == 0

def next_power_of_two(n):
    if n <= 0:
        return 1
    n -= 1
    n |= n >> 1
    n |= n >> 2
    n |= n >> 4
    n |= n >> 8
    n |= n >> 16
    return n + 1

def reverse_bits(n, num_bits=32):
    result = 0
    for _ in range(num_bits):
        result = (result << 1) | (n & 1)
        n >>= 1
    return result

def swap_bits(n, i, j):
    bit_i = (n >> i) & 1
    bit_j = (n >> j) & 1
    if bit_i != bit_j:
        n ^= (1 << i) | (1 << j)
    return n

def hamming_distance(a, b):
    xor = a ^ b
    return count_bits(xor)
''',
    "interval_operations": '''def merge_intervals(intervals):
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [list(sorted_intervals[0])]
    for start, end in sorted_intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged

def intervals_overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]

def interval_intersection(intervals_a, intervals_b):
    result = []
    i = j = 0
    while i < len(intervals_a) and j < len(intervals_b):
        lo = max(intervals_a[i][0], intervals_b[j][0])
        hi = min(intervals_a[i][1], intervals_b[j][1])
        if lo < hi:
            result.append([lo, hi])
        if intervals_a[i][1] < intervals_b[j][1]:
            i += 1
        else:
            j += 1
    return result

def total_coverage(intervals):
    merged = merge_intervals(intervals)
    return sum(end - start for start, end in merged)

def find_gaps(intervals, start, end):
    merged = merge_intervals(intervals)
    gaps = []
    current = start
    for s, e in merged:
        if s > current:
            gaps.append([current, s])
        current = max(current, e)
    if current < end:
        gaps.append([current, end])
    return gaps
''',
    "money_calculator": '''class Money:
    def __init__(self, amount, currency="USD"):
        self._cents = round(amount * 100)
        self._currency = currency

    def amount(self):
        return self._cents / 100.0

    def currency(self):
        return self._currency

    def add(self, other):
        if self._currency != other._currency:
            raise ValueError("Currency mismatch")
        return Money((self._cents + other._cents) / 100.0, self._currency)

    def subtract(self, other):
        if self._currency != other._currency:
            raise ValueError("Currency mismatch")
        return Money((self._cents - other._cents) / 100.0, self._currency)

    def multiply(self, factor):
        return Money(round(self._cents * factor) / 100.0, self._currency)

    def split(self, n):
        if n <= 0:
            return []
        base = self._cents // n
        remainder = self._cents % n
        parts = []
        for i in range(n):
            extra = 1 if i < remainder else 0
            parts.append(Money((base + extra) / 100.0, self._currency))
        return parts

    def __repr__(self):
        return "{} {:.2f}".format(self._currency, self.amount())

    def is_zero(self):
        return self._cents == 0

    def is_negative(self):
        return self._cents < 0
''',
    "cron_parser": '''def parse_cron_field(field, min_val, max_val):
    if field == "*":
        return list(range(min_val, max_val + 1))
    result = set()
    for part in field.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                start = min_val
            else:
                start = int(base)
            for v in range(start, max_val + 1, step):
                result.add(v)
        elif "-" in part:
            lo, hi = part.split("-", 1)
            for v in range(int(lo), int(hi) + 1):
                result.add(v)
        else:
            result.add(int(part))
    return sorted(result)

def parse_cron(expression):
    parts = expression.strip().split()
    if len(parts) != 5:
        return None
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    names = ["minute", "hour", "day", "month", "weekday"]
    result = {}
    for i, (mn, mx) in enumerate(ranges):
        result[names[i]] = parse_cron_field(parts[i], mn, mx)
    return result

def is_valid_cron(expression):
    return parse_cron(expression) is not None

def describe_cron(expression):
    parsed = parse_cron(expression)
    if not parsed:
        return "invalid"
    parts = []
    if len(parsed["minute"]) == 60:
        parts.append("every minute")
    elif len(parsed["minute"]) == 1:
        parts.append("at minute {}".format(parsed["minute"][0]))
    return ", ".join(parts) if parts else "custom schedule"
''',
    "semver_parser": '''def parse_semver(version):
    version = version.lstrip("v")
    pre = ""
    if "-" in version:
        version, pre = version.split("-", 1)
    parts = version.split(".")
    if len(parts) != 3:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2])
    except ValueError:
        return None
    return {"major": major, "minor": minor, "patch": patch, "pre": pre}

def compare_semver(a, b):
    pa = parse_semver(a)
    pb = parse_semver(b)
    if pa is None or pb is None:
        return 0
    for key in ("major", "minor", "patch"):
        if pa[key] < pb[key]:
            return -1
        if pa[key] > pb[key]:
            return 1
    if pa["pre"] and not pb["pre"]:
        return -1
    if not pa["pre"] and pb["pre"]:
        return 1
    return 0

def bump_version(version, level="patch"):
    parsed = parse_semver(version)
    if parsed is None:
        return version
    if level == "major":
        parsed["major"] += 1
        parsed["minor"] = 0
        parsed["patch"] = 0
    elif level == "minor":
        parsed["minor"] += 1
        parsed["patch"] = 0
    else:
        parsed["patch"] += 1
    parsed["pre"] = ""
    return "{}.{}.{}".format(parsed["major"], parsed["minor"], parsed["patch"])

def is_compatible(current, required):
    return compare_semver(current, required) >= 0
''',
    "weighted_random": '''import random as _wr_random

def weighted_choice(items, weights):
    total = sum(weights)
    r = _wr_random.random() * total
    cumulative = 0.0
    for item, weight in zip(items, weights):
        cumulative += weight
        if r <= cumulative:
            return item
    return items[-1]

def weighted_sample(items, weights, k):
    result = []
    remaining_items = list(items)
    remaining_weights = list(weights)
    for _ in range(min(k, len(items))):
        choice = weighted_choice(remaining_items, remaining_weights)
        idx = remaining_items.index(choice)
        result.append(choice)
        remaining_items.pop(idx)
        remaining_weights.pop(idx)
    return result

def normalize_weights(weights):
    total = sum(weights)
    if total == 0:
        return [0.0] * len(weights)
    return [w / total for w in weights]

def cumulative_weights(weights):
    result = []
    total = 0.0
    for w in weights:
        total += w
        result.append(total)
    return result

def reservoir_sample(stream, k):
    result = []
    for i, item in enumerate(stream):
        if i < k:
            result.append(item)
        else:
            j = _wr_random.randint(0, i)
            if j < k:
                result[j] = item
    return result
''',
    "bloom_filter": '''class BloomFilter:
    def __init__(self, size=1024, num_hashes=3):
        self._size = size
        self._hashes = num_hashes
        self._bits = [False] * size
        self._count = 0

    def _hash(self, item, seed):
        h = seed
        for ch in str(item):
            h = (h * 31 + ord(ch)) % self._size
        return h

    def add(self, item):
        for i in range(self._hashes):
            idx = self._hash(item, i * 7 + 1)
            self._bits[idx] = True
        self._count += 1

    def might_contain(self, item):
        for i in range(self._hashes):
            idx = self._hash(item, i * 7 + 1)
            if not self._bits[idx]:
                return False
        return True

    def count(self):
        return self._count

    def fill_ratio(self):
        return sum(1 for b in self._bits if b) / self._size

    def false_positive_rate(self):
        r = self.fill_ratio()
        return r ** self._hashes
''',
    "consistent_hash": '''class ConsistentHash:
    def __init__(self, replicas=100):
        self._replicas = replicas
        self._ring = {}
        self._sorted_keys = []
        self._nodes = set()

    def _hash(self, key):
        h = 0
        for ch in str(key):
            h = (h * 31 + ord(ch)) % (2 ** 32)
        return h

    def add_node(self, node):
        self._nodes.add(node)
        for i in range(self._replicas):
            key = self._hash("{}-{}".format(node, i))
            self._ring[key] = node
        self._sorted_keys = sorted(self._ring.keys())

    def remove_node(self, node):
        self._nodes.discard(node)
        for i in range(self._replicas):
            key = self._hash("{}-{}".format(node, i))
            if key in self._ring:
                del self._ring[key]
        self._sorted_keys = sorted(self._ring.keys())

    def get_node(self, item):
        if not self._sorted_keys:
            return None
        h = self._hash(str(item))
        for key in self._sorted_keys:
            if h <= key:
                return self._ring[key]
        return self._ring[self._sorted_keys[0]]

    def node_count(self):
        return len(self._nodes)
''',
    "lcs_algorithm": '''def lcs_length(a, b):
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    return dp[m][n]

def lcs_string(a, b):
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    result = []
    i, j = m, n
    while i > 0 and j > 0:
        if a[i-1] == b[j-1]:
            result.append(a[i-1])
            i -= 1
            j -= 1
        elif dp[i-1][j] > dp[i][j-1]:
            i -= 1
        else:
            j -= 1
    return "".join(reversed(result))

def lcs_ratio(a, b):
    if not a and not b:
        return 1.0
    length = lcs_length(a, b)
    return length / max(len(a), len(b))
''',
    "knapsack": '''def knapsack_01(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i-1][w]
            if weights[i-1] <= w:
                dp[i][w] = max(dp[i][w],
                               dp[i-1][w - weights[i-1]] + values[i-1])
    return dp[n][capacity]

def knapsack_items(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i-1][w]
            if weights[i-1] <= w:
                dp[i][w] = max(dp[i][w],
                               dp[i-1][w - weights[i-1]] + values[i-1])
    items = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i-1][w]:
            items.append(i - 1)
            w -= weights[i-1]
    return list(reversed(items))

def fractional_knapsack(weights, values, capacity):
    items = sorted(range(len(weights)),
                   key=lambda i: values[i] / weights[i], reverse=True)
    total = 0.0
    remaining = capacity
    for i in items:
        if remaining <= 0:
            break
        take = min(weights[i], remaining)
        total += take * (values[i] / weights[i])
        remaining -= take
    return round(total, 2)
''',
    "coin_change": '''def min_coins(coins, amount):
    dp = [float("inf")] * (amount + 1)
    dp[0] = 0
    for i in range(1, amount + 1):
        for coin in coins:
            if coin <= i and dp[i - coin] + 1 < dp[i]:
                dp[i] = dp[i - coin] + 1
    return dp[amount] if dp[amount] != float("inf") else -1

def count_ways(coins, amount):
    dp = [0] * (amount + 1)
    dp[0] = 1
    for coin in coins:
        for i in range(coin, amount + 1):
            dp[i] += dp[i - coin]
    return dp[amount]

def coin_combination(coins, amount):
    dp = [None] * (amount + 1)
    dp[0] = []
    for i in range(1, amount + 1):
        for coin in coins:
            if coin <= i and dp[i - coin] is not None:
                candidate = dp[i - coin] + [coin]
                if dp[i] is None or len(candidate) < len(dp[i]):
                    dp[i] = candidate
    return dp[amount]

def can_make_change(coins, amount):
    return min_coins(coins, amount) >= 0
''',
    "sudoku_validator": '''def is_valid_sudoku(board):
    for i in range(9):
        if not is_valid_group(board[i]):
            return False
    for j in range(9):
        col = [board[i][j] for i in range(9)]
        if not is_valid_group(col):
            return False
    for box_r in range(3):
        for box_c in range(3):
            box = []
            for i in range(3):
                for j in range(3):
                    box.append(board[box_r*3 + i][box_c*3 + j])
            if not is_valid_group(box):
                return False
    return True

def is_valid_group(group):
    seen = set()
    for val in group:
        if val == 0:
            continue
        if val < 1 or val > 9:
            return False
        if val in seen:
            return False
        seen.add(val)
    return True

def count_filled(board):
    count = 0
    for row in board:
        for cell in row:
            if cell != 0:
                count += 1
    return count

def find_empty(board):
    for i in range(9):
        for j in range(9):
            if board[i][j] == 0:
                return (i, j)
    return None
''',
    "spiral_matrix": '''def spiral_order(matrix):
    if not matrix:
        return []
    result = []
    top, bottom = 0, len(matrix) - 1
    left, right = 0, len(matrix[0]) - 1
    while top <= bottom and left <= right:
        for j in range(left, right + 1):
            result.append(matrix[top][j])
        top += 1
        for i in range(top, bottom + 1):
            result.append(matrix[i][right])
        right -= 1
        if top <= bottom:
            for j in range(right, left - 1, -1):
                result.append(matrix[bottom][j])
            bottom -= 1
        if left <= right:
            for i in range(bottom, top - 1, -1):
                result.append(matrix[i][left])
            left += 1
    return result

def generate_spiral(n):
    matrix = [[0] * n for _ in range(n)]
    top, bottom, left, right = 0, n - 1, 0, n - 1
    num = 1
    while top <= bottom and left <= right:
        for j in range(left, right + 1):
            matrix[top][j] = num
            num += 1
        top += 1
        for i in range(top, bottom + 1):
            matrix[i][right] = num
            num += 1
        right -= 1
        if top <= bottom:
            for j in range(right, left - 1, -1):
                matrix[bottom][j] = num
                num += 1
            bottom -= 1
        if left <= right:
            for i in range(bottom, top - 1, -1):
                matrix[i][left] = num
                num += 1
            left += 1
    return matrix
''',
    "moving_average": '''class MovingAverage:
    def __init__(self, window_size):
        self._size = window_size
        self._values = []
        self._sum = 0.0

    def add(self, value):
        self._values.append(value)
        self._sum += value
        if len(self._values) > self._size:
            self._sum -= self._values.pop(0)

    def average(self):
        if not self._values:
            return 0.0
        return self._sum / len(self._values)

    def is_full(self):
        return len(self._values) >= self._size

    def values(self):
        return list(self._values)

    def min_value(self):
        return min(self._values) if self._values else 0.0

    def max_value(self):
        return max(self._values) if self._values else 0.0

    def count(self):
        return len(self._values)

def compute_moving_averages(data, window):
    ma = MovingAverage(window)
    results = []
    for value in data:
        ma.add(value)
        results.append(round(ma.average(), 4))
    return results
''',
    "frequency_counter": '''class FrequencyCounter:
    def __init__(self):
        self._counts = {}
        self._total = 0

    def add(self, item):
        self._counts[item] = self._counts.get(item, 0) + 1
        self._total += 1

    def add_many(self, items):
        for item in items:
            self.add(item)

    def count(self, item):
        return self._counts.get(item, 0)

    def frequency(self, item):
        if self._total == 0:
            return 0.0
        return self._counts.get(item, 0) / self._total

    def most_common(self, n=10):
        sorted_items = sorted(
            self._counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:n]

    def least_common(self, n=10):
        sorted_items = sorted(
            self._counts.items(), key=lambda x: x[1])
        return sorted_items[:n]

    def unique_count(self):
        return len(self._counts)

    def total(self):
        return self._total

    def items(self):
        return dict(self._counts)
''',
    "ring_buffer": '''class RingBuffer:
    def __init__(self, capacity):
        self._capacity = capacity
        self._buffer = [None] * capacity
        self._head = 0
        self._tail = 0
        self._count = 0

    def write(self, item):
        self._buffer[self._tail] = item
        self._tail = (self._tail + 1) % self._capacity
        if self._count < self._capacity:
            self._count += 1
        else:
            self._head = (self._head + 1) % self._capacity

    def read(self):
        if self._count == 0:
            return None
        item = self._buffer[self._head]
        self._head = (self._head + 1) % self._capacity
        self._count -= 1
        return item

    def peek(self):
        if self._count == 0:
            return None
        return self._buffer[self._head]

    def is_empty(self):
        return self._count == 0

    def is_full(self):
        return self._count == self._capacity

    def size(self):
        return self._count

    def capacity(self):
        return self._capacity

    def to_list(self):
        result = []
        idx = self._head
        for _ in range(self._count):
            result.append(self._buffer[idx])
            idx = (idx + 1) % self._capacity
        return result
''',
    "bit_array": '''class BitArray:
    def __init__(self, size):
        self._size = size
        self._data = [0] * ((size + 7) // 8)

    def set_bit(self, idx):
        if 0 <= idx < self._size:
            self._data[idx // 8] |= (1 << (idx % 8))

    def clear_bit(self, idx):
        if 0 <= idx < self._size:
            self._data[idx // 8] &= ~(1 << (idx % 8))

    def get_bit(self, idx):
        if 0 <= idx < self._size:
            return (self._data[idx // 8] >> (idx % 8)) & 1
        return 0

    def toggle_bit(self, idx):
        if 0 <= idx < self._size:
            self._data[idx // 8] ^= (1 << (idx % 8))

    def count_set(self):
        total = 0
        for byte in self._data:
            while byte:
                total += byte & 1
                byte >>= 1
        return total

    def count_clear(self):
        return self._size - self.count_set()

    def size(self):
        return self._size

    def to_binary_string(self):
        bits = []
        for i in range(self._size):
            bits.append(str(self.get_bit(i)))
        return "".join(bits)
''',
    "sparse_matrix": '''class SparseMatrix:
    def __init__(self, rows, cols):
        self._rows = rows
        self._cols = cols
        self._data = {}

    def set(self, row, col, value):
        if value == 0:
            self._data.pop((row, col), None)
        else:
            self._data[(row, col)] = value

    def get(self, row, col):
        return self._data.get((row, col), 0)

    def rows(self):
        return self._rows

    def cols(self):
        return self._cols

    def nnz(self):
        return len(self._data)

    def density(self):
        total = self._rows * self._cols
        return len(self._data) / total if total > 0 else 0.0

    def add(self, other):
        result = SparseMatrix(self._rows, self._cols)
        for (r, c), v in self._data.items():
            result.set(r, c, v)
        for (r, c), v in other._data.items():
            result.set(r, c, result.get(r, c) + v)
        return result

    def to_dense(self):
        matrix = [[0] * self._cols for _ in range(self._rows)]
        for (r, c), v in self._data.items():
            matrix[r][c] = v
        return matrix

    def transpose(self):
        result = SparseMatrix(self._cols, self._rows)
        for (r, c), v in self._data.items():
            result.set(c, r, v)
        return result
''',
    "graph_coloring": '''def greedy_coloring(graph):
    colors = {}
    for node in sorted(graph.keys()):
        neighbor_colors = set()
        for neighbor in graph[node]:
            if neighbor in colors:
                neighbor_colors.add(colors[neighbor])
        color = 0
        while color in neighbor_colors:
            color += 1
        colors[node] = color
    return colors

def chromatic_bound(graph):
    colors = greedy_coloring(graph)
    return max(colors.values()) + 1 if colors else 0

def is_valid_coloring(graph, colors):
    for node, color in colors.items():
        for neighbor in graph.get(node, []):
            if neighbor in colors and colors[neighbor] == color:
                return False
    return True

def max_degree(graph):
    return max(len(neighbors) for neighbors in graph.values()) if graph else 0

def color_histogram(colors):
    hist = {}
    for color in colors.values():
        hist[color] = hist.get(color, 0) + 1
    return hist
''',
    "polynomial": '''class Polynomial:
    def __init__(self, coeffs):
        self._coeffs = list(coeffs)
        while len(self._coeffs) > 1 and self._coeffs[-1] == 0:
            self._coeffs.pop()

    def degree(self):
        return len(self._coeffs) - 1

    def evaluate(self, x):
        result = 0
        for i, c in enumerate(self._coeffs):
            result += c * (x ** i)
        return result

    def add(self, other):
        n = max(len(self._coeffs), len(other._coeffs))
        result = [0] * n
        for i in range(len(self._coeffs)):
            result[i] += self._coeffs[i]
        for i in range(len(other._coeffs)):
            result[i] += other._coeffs[i]
        return Polynomial(result)

    def multiply(self, other):
        n = len(self._coeffs) + len(other._coeffs) - 1
        result = [0] * n
        for i, a in enumerate(self._coeffs):
            for j, b in enumerate(other._coeffs):
                result[i + j] += a * b
        return Polynomial(result)

    def derivative(self):
        if len(self._coeffs) <= 1:
            return Polynomial([0])
        result = []
        for i in range(1, len(self._coeffs)):
            result.append(self._coeffs[i] * i)
        return Polynomial(result)

    def coefficients(self):
        return list(self._coeffs)
''',
    "regex_like_matcher": '''def match(pattern, text):
    if not pattern:
        return not text
    if len(pattern) > 1 and pattern[1] == "*":
        if match(pattern[2:], text):
            return True
        if text and (pattern[0] == "." or pattern[0] == text[0]):
            return match(pattern, text[1:])
        return False
    if text and (pattern[0] == "." or pattern[0] == text[0]):
        return match(pattern[1:], text[1:])
    return False

def match_wildcard(pattern, text):
    m, n = len(pattern), len(text)
    dp = [[False] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = True
    for i in range(1, m + 1):
        if pattern[i-1] == "*":
            dp[i][0] = dp[i-1][0]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if pattern[i-1] == "*":
                dp[i][j] = dp[i-1][j] or dp[i][j-1]
            elif pattern[i-1] == "?" or pattern[i-1] == text[j-1]:
                dp[i][j] = dp[i-1][j-1]
    return dp[m][n]

def count_matches(pattern, texts):
    return sum(1 for t in texts if match(pattern, t))

def find_matching(pattern, texts):
    return [t for t in texts if match(pattern, t)]
''',
    "data_pipeline": '''class Pipeline:
    def __init__(self):
        self._steps = []

    def add_step(self, name, func):
        self._steps.append({"name": name, "func": func})
        return self

    def execute(self, data):
        result = data
        log = []
        for step in self._steps:
            try:
                result = step["func"](result)
                log.append({"step": step["name"], "status": "ok"})
            except Exception as e:
                log.append({"step": step["name"], "status": "error",
                            "error": str(e)})
                break
        return {"result": result, "log": log}

    def step_count(self):
        return len(self._steps)

    def step_names(self):
        return [s["name"] for s in self._steps]

def filter_step(predicate):
    def step(data):
        return [item for item in data if predicate(item)]
    return step

def map_step(transform):
    def step(data):
        return [transform(item) for item in data]
    return step

def sort_step(key=None, reverse=False):
    def step(data):
        return sorted(data, key=key, reverse=reverse)
    return step
''',
    "connection_pool": '''class ConnectionPool:
    def __init__(self, max_size=10):
        self._max_size = max_size
        self._available = []
        self._in_use = set()
        self._next_id = 0

    def _create(self):
        conn_id = self._next_id
        self._next_id += 1
        return {"id": conn_id, "status": "open"}

    def acquire(self):
        if self._available:
            conn = self._available.pop()
            self._in_use.add(conn["id"])
            return conn
        if len(self._in_use) < self._max_size:
            conn = self._create()
            self._in_use.add(conn["id"])
            return conn
        return None

    def release(self, conn):
        if conn["id"] in self._in_use:
            self._in_use.remove(conn["id"])
            self._available.append(conn)

    def available_count(self):
        return len(self._available)

    def in_use_count(self):
        return len(self._in_use)

    def total_created(self):
        return self._next_id

    def is_exhausted(self):
        return (len(self._in_use) >= self._max_size and
                len(self._available) == 0)

    def stats(self):
        return {
            "available": self.available_count(),
            "in_use": self.in_use_count(),
            "max_size": self._max_size,
            "total_created": self._next_id,
        }
''',
    "task_queue": '''class TaskQueue:
    def __init__(self):
        self._pending = []
        self._completed = []
        self._failed = []

    def enqueue(self, task_id, payload):
        self._pending.append({
            "id": task_id,
            "payload": payload,
            "status": "pending",
        })

    def dequeue(self):
        if not self._pending:
            return None
        return self._pending.pop(0)

    def complete(self, task_id, result=None):
        self._completed.append({
            "id": task_id,
            "result": result,
        })

    def fail(self, task_id, error=None):
        self._failed.append({
            "id": task_id,
            "error": error,
        })

    def pending_count(self):
        return len(self._pending)

    def completed_count(self):
        return len(self._completed)

    def failed_count(self):
        return len(self._failed)

    def success_rate(self):
        total = self.completed_count() + self.failed_count()
        if total == 0:
            return 0.0
        return self.completed_count() / total

    def peek(self):
        if self._pending:
            return self._pending[0]
        return None
''',
    "metric_aggregator": '''class MetricAggregator:
    def __init__(self):
        self._metrics = {}

    def record(self, name, value):
        if name not in self._metrics:
            self._metrics[name] = {
                "values": [], "sum": 0.0,
                "min": float("inf"), "max": float("-inf"),
            }
        m = self._metrics[name]
        m["values"].append(value)
        m["sum"] += value
        m["min"] = min(m["min"], value)
        m["max"] = max(m["max"], value)

    def mean(self, name):
        m = self._metrics.get(name)
        if not m or not m["values"]:
            return 0.0
        return m["sum"] / len(m["values"])

    def min_val(self, name):
        m = self._metrics.get(name)
        return m["min"] if m else 0.0

    def max_val(self, name):
        m = self._metrics.get(name)
        return m["max"] if m else 0.0

    def count(self, name):
        m = self._metrics.get(name)
        return len(m["values"]) if m else 0

    def summary(self, name):
        m = self._metrics.get(name)
        if not m:
            return {}
        return {
            "count": len(m["values"]),
            "sum": m["sum"],
            "mean": round(self.mean(name), 4),
            "min": m["min"],
            "max": m["max"],
        }

    def all_metrics(self):
        return list(self._metrics.keys())
''',
    "topk_tracker": '''class TopKTracker:
    def __init__(self, k):
        self._k = k
        self._items = []
        self._total_seen = 0

    def add(self, item, score):
        self._total_seen += 1
        if len(self._items) < self._k:
            self._items.append((score, item))
            self._items.sort(reverse=True)
        elif score > self._items[-1][0]:
            self._items[-1] = (score, item)
            self._items.sort(reverse=True)

    def top_k(self):
        return [(item, score) for score, item in self._items]

    def min_score(self):
        if not self._items:
            return 0.0
        return self._items[-1][0]

    def max_score(self):
        if not self._items:
            return 0.0
        return self._items[0][0]

    def is_full(self):
        return len(self._items) >= self._k

    def total_seen(self):
        return self._total_seen

    def would_qualify(self, score):
        if len(self._items) < self._k:
            return True
        return score > self._items[-1][0]

    def size(self):
        return len(self._items)
''',
    "histogram": '''class Histogram:
    def __init__(self, bin_width=1.0, min_val=0.0):
        self._bin_width = bin_width
        self._min_val = min_val
        self._bins = {}
        self._count = 0

    def add(self, value):
        bin_idx = int((value - self._min_val) / self._bin_width)
        self._bins[bin_idx] = self._bins.get(bin_idx, 0) + 1
        self._count += 1

    def add_many(self, values):
        for v in values:
            self.add(v)

    def bin_count(self, bin_idx):
        return self._bins.get(bin_idx, 0)

    def bin_range(self, bin_idx):
        lo = self._min_val + bin_idx * self._bin_width
        hi = lo + self._bin_width
        return (lo, hi)

    def total(self):
        return self._count

    def num_bins(self):
        return len(self._bins)

    def mode_bin(self):
        if not self._bins:
            return None
        return max(self._bins, key=self._bins.get)

    def to_dict(self):
        result = {}
        for idx, count in sorted(self._bins.items()):
            lo, hi = self.bin_range(idx)
            result["{:.1f}-{:.1f}".format(lo, hi)] = count
        return result

    def max_count(self):
        return max(self._bins.values()) if self._bins else 0
''',
    "leaky_bucket": '''class LeakyBucket:
    def __init__(self, capacity, leak_rate):
        self._capacity = capacity
        self._leak_rate = leak_rate
        self._water = 0.0
        self._last_time = 0.0

    def _leak(self, now):
        if self._last_time == 0:
            self._last_time = now
            return
        elapsed = now - self._last_time
        leaked = elapsed * self._leak_rate
        self._water = max(0.0, self._water - leaked)
        self._last_time = now

    def add(self, amount, now=0.0):
        self._leak(now)
        if self._water + amount > self._capacity:
            return False
        self._water += amount
        return True

    def current_level(self, now=0.0):
        self._leak(now)
        return self._water

    def available_capacity(self, now=0.0):
        self._leak(now)
        return self._capacity - self._water

    def is_full(self, now=0.0):
        self._leak(now)
        return self._water >= self._capacity

    def is_empty(self, now=0.0):
        self._leak(now)
        return self._water <= 0.0

    def capacity(self):
        return self._capacity

    def drain_time(self, now=0.0):
        level = self.current_level(now)
        if self._leak_rate <= 0:
            return float("inf")
        return level / self._leak_rate
''',
    "bounded_counter": '''class BoundedCounter:
    def __init__(self, min_val=0, max_val=100, initial=0):
        self._min = min_val
        self._max = max_val
        self._value = max(min_val, min(max_val, initial))
        self._overflow_count = 0
        self._underflow_count = 0

    def increment(self, amount=1):
        new_val = self._value + amount
        if new_val > self._max:
            self._overflow_count += 1
            self._value = self._max
            return False
        self._value = new_val
        return True

    def decrement(self, amount=1):
        new_val = self._value - amount
        if new_val < self._min:
            self._underflow_count += 1
            self._value = self._min
            return False
        self._value = new_val
        return True

    def value(self):
        return self._value

    def reset(self):
        self._value = self._min

    def is_at_max(self):
        return self._value == self._max

    def is_at_min(self):
        return self._value == self._min

    def percentage(self):
        range_size = self._max - self._min
        if range_size == 0:
            return 100.0
        return ((self._value - self._min) / range_size) * 100.0

    def overflow_count(self):
        return self._overflow_count

    def underflow_count(self):
        return self._underflow_count
''',
    "circular_buffer_stats": '''class CircularStats:
    def __init__(self, window):
        self._window = window
        self._data = []
        self._sum = 0.0
        self._sum_sq = 0.0

    def add(self, value):
        if len(self._data) >= self._window:
            old = self._data.pop(0)
            self._sum -= old
            self._sum_sq -= old * old
        self._data.append(value)
        self._sum += value
        self._sum_sq += value * value

    def mean(self):
        n = len(self._data)
        return self._sum / n if n > 0 else 0.0

    def variance(self):
        n = len(self._data)
        if n < 2:
            return 0.0
        mean = self._sum / n
        return (self._sum_sq / n) - (mean * mean)

    def std_dev(self):
        v = self.variance()
        return v ** 0.5 if v > 0 else 0.0

    def min_val(self):
        return min(self._data) if self._data else 0.0

    def max_val(self):
        return max(self._data) if self._data else 0.0

    def count(self):
        return len(self._data)

    def is_full(self):
        return len(self._data) >= self._window

    def values(self):
        return list(self._data)
''',
    "simple_cache": '''class SimpleCache:
    def __init__(self, max_size=100):
        self._data = {}
        self._order = []
        self._max = max_size
        self._hits = 0
        self._misses = 0

    def get(self, key):
        if key in self._data:
            self._hits += 1
            self._order.remove(key)
            self._order.append(key)
            return self._data[key]
        self._misses += 1
        return None

    def put(self, key, value):
        if key in self._data:
            self._order.remove(key)
        elif len(self._data) >= self._max:
            oldest = self._order.pop(0)
            del self._data[oldest]
        self._data[key] = value
        self._order.append(key)

    def remove(self, key):
        if key in self._data:
            del self._data[key]
            self._order.remove(key)

    def size(self):
        return len(self._data)

    def hit_rate(self):
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def clear(self):
        self._data.clear()
        self._order.clear()
        self._hits = 0
        self._misses = 0

    def keys(self):
        return list(self._order)
''',
    "weighted_graph": '''class WeightedGraph:
    def __init__(self):
        self._adj = {}

    def add_edge(self, u, v, weight=1.0):
        if u not in self._adj:
            self._adj[u] = []
        if v not in self._adj:
            self._adj[v] = []
        self._adj[u].append((v, weight))
        self._adj[v].append((u, weight))

    def neighbors(self, node):
        return [(n, w) for n, w in self._adj.get(node, [])]

    def nodes(self):
        return list(self._adj.keys())

    def edge_count(self):
        total = sum(len(edges) for edges in self._adj.values())
        return total // 2

    def total_weight(self):
        total = 0.0
        seen = set()
        for u in self._adj:
            for v, w in self._adj[u]:
                edge = (min(u, v), max(u, v))
                if edge not in seen:
                    seen.add(edge)
                    total += w
        return total

    def degree(self, node):
        return len(self._adj.get(node, []))

    def has_edge(self, u, v):
        return any(n == v for n, w in self._adj.get(u, []))

    def remove_edge(self, u, v):
        if u in self._adj:
            self._adj[u] = [(n, w) for n, w in self._adj[u] if n != v]
        if v in self._adj:
            self._adj[v] = [(n, w) for n, w in self._adj[v] if n != u]
''',
    "string_builder": '''class StringBuilder:
    def __init__(self):
        self._parts = []
        self._length = 0

    def append(self, text):
        text = str(text)
        self._parts.append(text)
        self._length += len(text)
        return self

    def append_line(self, text=""):
        self.append(text)
        self._parts.append("\\n")
        self._length += 1
        return self

    def prepend(self, text):
        text = str(text)
        self._parts.insert(0, text)
        self._length += len(text)
        return self

    def insert(self, index, text):
        current = self.build()
        result = current[:index] + str(text) + current[index:]
        self._parts = [result]
        self._length = len(result)
        return self

    def build(self):
        return "".join(self._parts)

    def length(self):
        return self._length

    def clear(self):
        self._parts.clear()
        self._length = 0
        return self

    def replace(self, old, new):
        text = self.build()
        text = text.replace(old, new)
        self._parts = [text]
        self._length = len(text)
        return self

    def is_empty(self):
        return self._length == 0
''',
    "scheduler": '''class Scheduler:
    def __init__(self):
        self._tasks = []
        self._executed = []

    def schedule(self, task_id, priority, duration):
        self._tasks.append({
            "id": task_id,
            "priority": priority,
            "duration": duration,
            "status": "pending",
        })

    def run_next(self):
        pending = [t for t in self._tasks if t["status"] == "pending"]
        if not pending:
            return None
        pending.sort(key=lambda t: t["priority"])
        task = pending[0]
        task["status"] = "running"
        return task

    def complete(self, task_id):
        for task in self._tasks:
            if task["id"] == task_id:
                task["status"] = "done"
                self._executed.append(task_id)
                return True
        return False

    def pending_count(self):
        return sum(1 for t in self._tasks if t["status"] == "pending")

    def completed_count(self):
        return len(self._executed)

    def total_duration(self):
        return sum(t["duration"] for t in self._tasks)

    def remaining_duration(self):
        return sum(t["duration"] for t in self._tasks if t["status"] == "pending")

    def execution_order(self):
        return list(self._executed)
''',
    "base_converter": '''def to_base(number, base):
    if number == 0:
        return "0"
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    negative = number < 0
    number = abs(number)
    result = []
    while number > 0:
        result.append(digits[number % base])
        number //= base
    if negative:
        result.append("-")
    return "".join(reversed(result))

def from_base(text, base):
    text = text.strip().upper()
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = 0
    for ch in text:
        result = result * base + digits.index(ch)
    return -result if negative else result

def convert_base(text, from_base_val, to_base_val):
    decimal = from_base(text, from_base_val)
    return to_base(decimal, to_base_val)

def is_valid_for_base(text, base):
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    valid = set(digits[:base].lower() + digits[:base].upper())
    text = text.lstrip("-")
    return all(ch in valid for ch in text)
''',
    "fibonacci_matrix": '''def matrix_mult_2x2(a, b):
    return [
        [a[0][0]*b[0][0] + a[0][1]*b[1][0],
         a[0][0]*b[0][1] + a[0][1]*b[1][1]],
        [a[1][0]*b[0][0] + a[1][1]*b[1][0],
         a[1][0]*b[0][1] + a[1][1]*b[1][1]],
    ]

def matrix_pow_2x2(mat, n):
    result = [[1, 0], [0, 1]]
    base = [row[:] for row in mat]
    while n > 0:
        if n % 2 == 1:
            result = matrix_mult_2x2(result, base)
        base = matrix_mult_2x2(base, base)
        n //= 2
    return result

def fibonacci_fast(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    fib_matrix = [[1, 1], [1, 0]]
    result = matrix_pow_2x2(fib_matrix, n - 1)
    return result[0][0]

def fibonacci_range(start, end):
    return [fibonacci_fast(i) for i in range(start, end)]

def is_fibonacci(n):
    if n < 0:
        return False
    a, b = 0, 1
    while b < n:
        a, b = b, a + b
    return b == n or n == 0
''',
}


# -- Measurement helpers ---------------------------------------------------

def measure_ast_baseline(source):
    """Measure pure AST parse time as baseline (microseconds)."""
    t0 = time.perf_counter()
    ast.parse(source)
    return (time.perf_counter() - t0) * 1e6


def extract_file_info(prove_objs):
    """Extract file info from prove JSON output."""
    if prove_objs and isinstance(prove_objs[0], dict):
        for finfo in prove_objs[0].get("files", []):
            return finfo
    return {}


def measure_program(name, source):
    """Measure scaffold overhead for a single program via jugeo prove."""
    source_bytes = len(source.encode("utf-8"))
    source_lines = len(source.strip().splitlines())
    tmp = write_temp_py(source)

    try:
        # Initial verification
        t0 = time.perf_counter()
        prove1 = run_jugeo("prove", tmp)
        initial_time = time.perf_counter() - t0

        # Re-verification (second run)
        t1 = time.perf_counter()
        prove2 = run_jugeo("prove", tmp)
        reverify_time = time.perf_counter() - t1

        # AST baseline
        ast_us = measure_ast_baseline(source)

        # Extract data
        finfo = extract_file_info(prove1)
        verdict = finfo.get("verdict", "unknown")
        trust = finfo.get("trust", "unknown")
        coords = len(finfo.get("coordinates", []))
        props_total = finfo.get("propositions_total", 0)
        props_ok = finfo.get("propositions_ok", 0)
        obstructions = len(finfo.get("obstructions", []))
        certificate = finfo.get("certificate", {})

        # Certificate size = JSON bytes of prove output
        cert_json = json.dumps(prove1, default=str)
        cert_bytes = len(cert_json.encode("utf-8"))
        overhead_ratio = cert_bytes / source_bytes if source_bytes > 0 else 0.0

        # Strategy comparison
        strategy_times = {}
        for strat in ["eager", "exhaustive", "iterative"]:
            ts = time.perf_counter()
            run_jugeo("prove", tmp, "--strategy", strat)
            strategy_times[strat] = round(time.perf_counter() - ts, 4)

        return {
            "name": name,
            "source_bytes": source_bytes,
            "source_lines": source_lines,
            "certificate_bytes": cert_bytes,
            "overhead_ratio": round(overhead_ratio, 3),
            "initial_time_s": round(initial_time, 4),
            "reverify_time_s": round(reverify_time, 4),
            "speedup": round(initial_time / reverify_time, 2) if reverify_time > 0 else 0,
            "ast_baseline_us": round(ast_us, 1),
            "overhead_vs_ast": round((initial_time * 1e6) / ast_us, 1) if ast_us > 0 else 0,
            "verdict": verdict,
            "trust": trust,
            "coordinates": coords,
            "propositions_total": props_total,
            "propositions_ok": props_ok,
            "obstructions": obstructions,
            "certificate_hash": certificate.get("hash", ""),
            "strategy_times_s": strategy_times,
        }
    finally:
        try: os.unlink(tmp)
        except OSError: pass


def main():
    print("=" * 72)
    print("Paper 9: Verification Certificates That Ship With Code")
    print("=" * 72)

    # Validate all programs parse
    for name, source in PROGRAMS.items():
        ast.parse(source)
    print(f"Validated {len(PROGRAMS)} programs")

    results = {"programs": [], "literature_baselines": LITERATURE_BASELINES}
    all_overhead = []
    all_initial = []
    all_reverify = []

    for name, source in PROGRAMS.items():
        m = measure_program(name, source)
        results["programs"].append(m)
        all_overhead.append(m["overhead_ratio"])
        all_initial.append(m["initial_time_s"])
        all_reverify.append(m["reverify_time_s"])

        print(f"\n  {m['name']}:")
        print(f"    Source: {m['source_bytes']} bytes ({m['source_lines']} lines)")
        print(f"    Cert: {m['certificate_bytes']} bytes (overhead {m['overhead_ratio']:.3f}x)")
        print(f"    Coords: {m['coordinates']}, Props: {m['propositions_ok']}/{m['propositions_total']}")
        print(f"    Initial: {m['initial_time_s']:.4f}s, Re-verify: {m['reverify_time_s']:.4f}s "
              f"(speedup {m['speedup']:.2f}x)")
        print(f"    Verdict: {m['verdict']}")

    # Aggregate stats
    summary = {
        "total_programs": len(PROGRAMS),
        "mean_overhead": round(statistics.mean(all_overhead), 3) if all_overhead else 0,
        "median_overhead": round(statistics.median(all_overhead), 3) if all_overhead else 0,
        "min_overhead": round(min(all_overhead), 3) if all_overhead else 0,
        "max_overhead": round(max(all_overhead), 3) if all_overhead else 0,
        "mean_initial_s": round(statistics.mean(all_initial), 4) if all_initial else 0,
        "mean_reverify_s": round(statistics.mean(all_reverify), 4) if all_reverify else 0,
        "total_source_bytes": sum(m["source_bytes"] for m in results["programs"]),
        "total_cert_bytes": sum(m["certificate_bytes"] for m in results["programs"]),
        "note": "Scaffold = jugeo prove JSON output -- the proof certificate",
    }
    results["summary"] = summary

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Programs:           {summary['total_programs']}")
    print(f"  Total source:       {summary['total_source_bytes']} bytes")
    print(f"  Total certs:        {summary['total_cert_bytes']} bytes")
    print(f"  Overhead (mean):    {summary['mean_overhead']:.3f}x")
    print(f"  Overhead (median):  {summary['median_overhead']:.3f}x")
    print(f"  Overhead (min):     {summary['min_overhead']:.3f}x")
    print(f"  Overhead (max):     {summary['max_overhead']:.3f}x")
    print(f"  Avg initial:        {summary['mean_initial_s']:.4f}s")
    print(f"  Avg re-verify:      {summary['mean_reverify_s']:.4f}s")
    print()
    print("  LITERATURE BASELINES (not measured by this script):")
    for key, bl in LITERATURE_BASELINES.items():
        print(f"    {key}: overhead ~{bl['overhead_ratio_loc']}x LOC")
        print(f"      cite: {bl['cite']}")

    out = os.path.join(os.path.dirname(__file__), "results_paper09.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
