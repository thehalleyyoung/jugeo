#!/usr/bin/env python3
"""Paper 3 Experiment — Cohomological Diagnostics: Classifying Why Proofs Fail.

Writes Python programs that intentionally trigger different obstruction
classes (H0/H1/H2/Hinf), runs ``jugeo prove``, ``jugeo descend``, and
``jugeo bugs`` via the CLI, and analyses obstruction_vanishing /
obstruction_field results from JSON.

Every number is reproducible: run `python3 experiments/exp03_descent_obstructions.py`.
"""
import json, os, random, subprocess, sys, tempfile, time

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


def write_temp(source: str, suffix=".py") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False)
    f.write(source)
    f.close()
    return f.name


# ── Literature baselines (NOT computed — sourced from publications) ──────

LITERATURE_BASELINES = {
    "description": (
        "Diagnostic richness when reporting the same class of verification "
        "failure.  JuGeo classifies failures into cohomological dimensions; "
        "existing tools provide less structured information."
    ),
    "Lean4": {
        "diagnostic_fields": 2,
        "fields": ["error location", "error message"],
        "failure_classes": 1,
        "source": "Lean Community FAQ (Error Messages section)",
    },
    "Fstar": {
        "diagnostic_fields": 3,
        "fields": ["error location", "error message", "precondition context"],
        "failure_classes": 2,
        "source": "F* Wiki: Error Messages",
    },
    "Dafny": {
        "diagnostic_fields": 4,
        "fields": ["error location", "error message", "counterexample", "related location"],
        "failure_classes": 3,
        "source": "Dafny FAQ: Verification Errors",
    },
    "mypy": {
        "diagnostic_fields": 2,
        "fields": ["error location", "error message"],
        "failure_classes": 1,
        "source": "mypy documentation: Error Codes",
    },
    "Pyright": {
        "diagnostic_fields": 3,
        "fields": ["error location", "error message", "expected vs actual type"],
        "failure_classes": 1,
        "source": "Pyright documentation: Configuration",
    },
}

# ── Test programs designed to trigger different obstruction classes ───────
# H0: correct programs -> obstructions vanish, verified
# H1: programs with local-but-not-global properties (partial verdicts)
# H2: programs with conflicting types / structural issues
# Hinf: genuinely unverifiable programs

H0_PROGRAMS = {
    'clean_merge_sort': '''
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
        return arr[:]
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)

def is_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True
''',

    'clean_binary_search': '''
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    iterations = 0
    while low <= high:
        iterations += 1
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid, iterations
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1, iterations

def search_range(arr, target):
    left = binary_search_left(arr, target)
    right = binary_search_right(arr, target)
    return (left, right)

def binary_search_left(arr, target):
    low, high = 0, len(arr)
    while low < high:
        mid = (low + high) // 2
        if arr[mid] < target:
            low = mid + 1
        else:
            high = mid
    return low

def binary_search_right(arr, target):
    low, high = 0, len(arr)
    while low < high:
        mid = (low + high) // 2
        if arr[mid] <= target:
            low = mid + 1
        else:
            high = mid
    return low
''',

    'clean_stack': '''
class Stack:
    def __init__(self, capacity=100):
        self.items = []
        self.capacity = capacity
        self.min_stack = []

    def push(self, item):
        if len(self.items) >= self.capacity:
            raise OverflowError("Stack is full")
        self.items.append(item)
        if not self.min_stack or item <= self.min_stack[-1]:
            self.min_stack.append(item)
        return self

    def pop(self):
        if not self.items:
            raise IndexError("Stack is empty")
        item = self.items.pop()
        if item == self.min_stack[-1]:
            self.min_stack.pop()
        return item

    def peek(self):
        if not self.items:
            raise IndexError("Stack is empty")
        return self.items[-1]

    def get_min(self):
        if not self.min_stack:
            raise IndexError("Stack is empty")
        return self.min_stack[-1]

    def is_empty(self):
        return len(self.items) == 0

    def size(self):
        return len(self.items)
''',

    'clean_queue': '''
class CircularQueue:
    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = [None] * capacity
        self.head = 0
        self.tail = 0
        self.count = 0

    def enqueue(self, item):
        if self.count == self.capacity:
            raise OverflowError("Queue is full")
        self.buffer[self.tail] = item
        self.tail = (self.tail + 1) % self.capacity
        self.count += 1
        return True

    def dequeue(self):
        if self.count == 0:
            raise IndexError("Queue is empty")
        item = self.buffer[self.head]
        self.buffer[self.head] = None
        self.head = (self.head + 1) % self.capacity
        self.count -= 1
        return item

    def front(self):
        if self.count == 0:
            raise IndexError("Queue is empty")
        return self.buffer[self.head]

    def is_empty(self):
        return self.count == 0

    def is_full(self):
        return self.count == self.capacity

    def __len__(self):
        return self.count
''',

    'clean_linked_list': '''
class ListNode:
    def __init__(self, val=0, next_node=None):
        self.val = val
        self.next = next_node

class SinglyLinkedList:
    def __init__(self):
        self.head = None
        self.length = 0

    def prepend(self, val):
        node = ListNode(val, self.head)
        self.head = node
        self.length += 1
        return self

    def append(self, val):
        node = ListNode(val)
        if self.head is None:
            self.head = node
        else:
            curr = self.head
            while curr.next is not None:
                curr = curr.next
            curr.next = node
        self.length += 1
        return self

    def remove(self, val):
        if self.head is None:
            return False
        if self.head.val == val:
            self.head = self.head.next
            self.length -= 1
            return True
        curr = self.head
        while curr.next is not None:
            if curr.next.val == val:
                curr.next = curr.next.next
                self.length -= 1
                return True
            curr = curr.next
        return False

    def to_list(self):
        result = []
        curr = self.head
        while curr is not None:
            result.append(curr.val)
            curr = curr.next
        return result
''',

    'clean_hash_map': '''
class HashMap:
    def __init__(self, initial_capacity=16):
        self.capacity = initial_capacity
        self.size = 0
        self.buckets = [[] for _ in range(self.capacity)]
        self.load_factor = 0.75

    def _hash(self, key):
        return hash(key) % self.capacity

    def put(self, key, value):
        idx = self._hash(key)
        bucket = self.buckets[idx]
        for i, (k, v) in enumerate(bucket):
            if k == key:
                bucket[i] = (key, value)
                return
        bucket.append((key, value))
        self.size += 1
        if self.size > self.capacity * self.load_factor:
            self._resize()

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
                del bucket[i]
                self.size -= 1
                return True
        return False

    def _resize(self):
        old_buckets = self.buckets
        self.capacity *= 2
        self.buckets = [[] for _ in range(self.capacity)]
        self.size = 0
        for bucket in old_buckets:
            for key, value in bucket:
                self.put(key, value)

    def keys(self):
        result = []
        for bucket in self.buckets:
            for key, value in bucket:
                result.append(key)
        return result
''',

    'clean_tree_traversal': '''
class TreeNode:
    def __init__(self, val, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def inorder(root):
    result = []
    stack = []
    current = root
    while current is not None or stack:
        while current is not None:
            stack.append(current)
            current = current.left
        current = stack.pop()
        result.append(current.val)
        current = current.right
    return result

def preorder(root):
    if root is None:
        return []
    result = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node.val)
        if node.right is not None:
            stack.append(node.right)
        if node.left is not None:
            stack.append(node.left)
    return result

def postorder(root):
    if root is None:
        return []
    result = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node.val)
        if node.left is not None:
            stack.append(node.left)
        if node.right is not None:
            stack.append(node.right)
    return result[::-1]

def tree_height(root):
    if root is None:
        return 0
    return 1 + max(tree_height(root.left), tree_height(root.right))
''',

    'clean_graph_bfs': '''
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
''',

    'clean_matrix_add': '''
def matrix_add(a, b):
    rows = len(a)
    cols = len(a[0])
    result = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            result[i][j] = a[i][j] + b[i][j]
    return result

def matrix_subtract(a, b):
    rows = len(a)
    cols = len(a[0])
    result = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            result[i][j] = a[i][j] - b[i][j]
    return result

def matrix_scalar_multiply(matrix, scalar):
    rows = len(matrix)
    cols = len(matrix[0])
    result = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            result[i][j] = matrix[i][j] * scalar
    return result

def matrix_transpose(matrix):
    rows = len(matrix)
    cols = len(matrix[0])
    result = [[0] * rows for _ in range(cols)]
    for i in range(rows):
        for j in range(cols):
            result[j][i] = matrix[i][j]
    return result

def identity_matrix(n):
    result = [[0] * n for _ in range(n)]
    for i in range(n):
        result[i][i] = 1
    return result
''',

    'clean_gcd_euclid': '''
def gcd(a, b):
    a = abs(a)
    b = abs(b)
    while b != 0:
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

def mod_inverse(a, m):
    g, x, _ = extended_gcd(a % m, m)
    if g != 1:
        return None
    return x % m

def gcd_of_list(numbers):
    result = numbers[0]
    for num in numbers[1:]:
        result = gcd(result, num)
        if result == 1:
            return 1
    return result

def lcm_of_list(numbers):
    result = numbers[0]
    for num in numbers[1:]:
        result = lcm(result, num)
    return result
''',

    'clean_prime_check': '''
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

def sieve_of_eratosthenes(limit):
    if limit < 2:
        return []
    is_prime_arr = [True] * (limit + 1)
    is_prime_arr[0] = is_prime_arr[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime_arr[i]:
            for j in range(i * i, limit + 1, i):
                is_prime_arr[j] = False
    return [i for i, v in enumerate(is_prime_arr) if v]

def next_prime(n):
    candidate = n + 1
    while not is_prime(candidate):
        candidate += 1
    return candidate
''',

    'clean_fibonacci': '''
def fibonacci_iterative(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    prev, curr = 0, 1
    for _ in range(2, n + 1):
        prev, curr = curr, prev + curr
    return curr

def fibonacci_sequence(n):
    sequence = []
    a, b = 0, 1
    for _ in range(n):
        sequence.append(a)
        a, b = b, a + b
    return sequence

def fibonacci_matrix(n):
    if n <= 0:
        return 0
    if n == 1:
        return 1
    mat = [[1, 1], [1, 0]]
    result = matrix_power(mat, n - 1)
    return result[0][0]

def matrix_power(mat, p):
    result = [[1, 0], [0, 1]]
    base = [row[:] for row in mat]
    while p > 0:
        if p % 2 == 1:
            result = mat_mult(result, base)
        base = mat_mult(base, base)
        p //= 2
    return result

def mat_mult(a, b):
    return [
        [a[0][0]*b[0][0] + a[0][1]*b[1][0], a[0][0]*b[0][1] + a[0][1]*b[1][1]],
        [a[1][0]*b[0][0] + a[1][1]*b[1][0], a[1][0]*b[0][1] + a[1][1]*b[1][1]],
    ]
''',

    'clean_factorial': '''
def factorial_iterative(n):
    if n < 0:
        raise ValueError("Negative input")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

def factorial_recursive(n):
    if n < 0:
        raise ValueError("Negative input")
    if n <= 1:
        return 1
    return n * factorial_recursive(n - 1)

def double_factorial(n):
    if n <= 0:
        return 1
    result = 1
    current = n
    while current > 0:
        result *= current
        current -= 2
    return result

def falling_factorial(n, k):
    result = 1
    for i in range(k):
        result *= (n - i)
    return result

def rising_factorial(n, k):
    result = 1
    for i in range(k):
        result *= (n + i)
    return result

def binomial_coefficient(n, k):
    if k < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    k = min(k, n - k)
    result = 1
    for i in range(k):
        result = result * (n - i) // (i + 1)
    return result
''',

    'clean_string_reverse': '''
def reverse_string(s):
    chars = list(s)
    left = 0
    right = len(chars) - 1
    while left < right:
        chars[left], chars[right] = chars[right], chars[left]
        left += 1
        right -= 1
    return "".join(chars)

def reverse_words(sentence):
    words = sentence.split()
    reversed_words = []
    for i in range(len(words) - 1, -1, -1):
        reversed_words.append(words[i])
    return " ".join(reversed_words)

def reverse_vowels(s):
    vowels = set("aeiouAEIOU")
    chars = list(s)
    left = 0
    right = len(chars) - 1
    while left < right:
        while left < right and chars[left] not in vowels:
            left += 1
        while left < right and chars[right] not in vowels:
            right -= 1
        chars[left], chars[right] = chars[right], chars[left]
        left += 1
        right -= 1
    return "".join(chars)

def is_rotation(s1, s2):
    if len(s1) != len(s2):
        return False
    doubled = s1 + s1
    return s2 in doubled
''',

    'clean_palindrome': '''
def is_palindrome(s):
    cleaned = ""
    for ch in s:
        if ch.isalnum():
            cleaned += ch.lower()
    left = 0
    right = len(cleaned) - 1
    while left < right:
        if cleaned[left] != cleaned[right]:
            return False
        left += 1
        right -= 1
    return True

def longest_palindrome_substring(s):
    if len(s) < 2:
        return s
    start = 0
    max_len = 1
    for i in range(len(s)):
        l1, r1 = expand_around_center(s, i, i)
        l2, r2 = expand_around_center(s, i, i + 1)
        if r1 - l1 > max_len:
            start = l1
            max_len = r1 - l1
        if r2 - l2 > max_len:
            start = l2
            max_len = r2 - l2
    return s[start:start + max_len]

def expand_around_center(s, left, right):
    while left >= 0 and right < len(s) and s[left] == s[right]:
        left -= 1
        right += 1
    return left + 1, right - left - 1
''',

    'clean_counter': '''
class FrequencyCounter:
    def __init__(self):
        self.counts = {}
        self.total = 0

    def add(self, item):
        if item in self.counts:
            self.counts[item] += 1
        else:
            self.counts[item] = 1
        self.total += 1
        return self

    def remove(self, item):
        if item not in self.counts:
            return False
        self.counts[item] -= 1
        if self.counts[item] == 0:
            del self.counts[item]
        self.total -= 1
        return True

    def get_count(self, item):
        return self.counts.get(item, 0)

    def most_common(self, n=10):
        items = list(self.counts.items())
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:n]

    def least_common(self, n=10):
        items = list(self.counts.items())
        items.sort(key=lambda x: x[1])
        return items[:n]

    def unique_count(self):
        return len(self.counts)
''',

    'clean_accumulator': '''
class Accumulator:
    def __init__(self):
        self.values = []
        self.running_sum = 0.0
        self.running_min = None
        self.running_max = None

    def add(self, value):
        self.values.append(value)
        self.running_sum += value
        if self.running_min is None or value < self.running_min:
            self.running_min = value
        if self.running_max is None or value > self.running_max:
            self.running_max = value
        return self

    def mean(self):
        if not self.values:
            return 0.0
        return self.running_sum / len(self.values)

    def variance(self):
        if len(self.values) < 2:
            return 0.0
        m = self.mean()
        total = 0.0
        for v in self.values:
            total += (v - m) ** 2
        return total / (len(self.values) - 1)

    def std_dev(self):
        return self.variance() ** 0.5

    def count(self):
        return len(self.values)

    def range(self):
        if not self.values:
            return 0.0
        return self.running_max - self.running_min

    def reset(self):
        self.values = []
        self.running_sum = 0.0
        self.running_min = None
        self.running_max = None
''',

    'clean_pair_sum': '''
def two_sum(nums, target):
    seen = {}
    results = []
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            results.append((seen[complement], i))
        seen[num] = i
    return results

def three_sum(nums, target=0):
    nums_sorted = sorted(nums)
    results = []
    n = len(nums_sorted)
    for i in range(n - 2):
        if i > 0 and nums_sorted[i] == nums_sorted[i - 1]:
            continue
        left = i + 1
        right = n - 1
        while left < right:
            total = nums_sorted[i] + nums_sorted[left] + nums_sorted[right]
            if total == target:
                results.append((nums_sorted[i], nums_sorted[left], nums_sorted[right]))
                while left < right and nums_sorted[left] == nums_sorted[left + 1]:
                    left += 1
                while left < right and nums_sorted[right] == nums_sorted[right - 1]:
                    right -= 1
                left += 1
                right -= 1
            elif total < target:
                left += 1
            else:
                right -= 1
    return results

def pair_sum_count(nums, target):
    count = 0
    seen = {}
    for num in nums:
        complement = target - num
        count += seen.get(complement, 0)
        seen[num] = seen.get(num, 0) + 1
    return count
''',

    'clean_max_finder': '''
def find_max(arr):
    if not arr:
        raise ValueError("Empty array")
    current_max = arr[0]
    for val in arr[1:]:
        if val > current_max:
            current_max = val
    return current_max

def find_kth_largest(arr, k):
    if k < 1 or k > len(arr):
        raise ValueError("k out of range")
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x > pivot]
    mid = [x for x in arr if x == pivot]
    right = [x for x in arr if x < pivot]
    if k <= len(left):
        return find_kth_largest(left, k)
    elif k <= len(left) + len(mid):
        return pivot
    else:
        return find_kth_largest(right, k - len(left) - len(mid))

def find_second_max(arr):
    if len(arr) < 2:
        raise ValueError("Need at least two elements")
    first = second = float("-inf")
    for val in arr:
        if val > first:
            second = first
            first = val
        elif val > second and val != first:
            second = val
    if second == float("-inf"):
        raise ValueError("No second maximum")
    return second

def max_sliding_window(arr, k):
    if not arr or k <= 0:
        return []
    result = []
    window = []
    for i in range(len(arr)):
        while window and window[0] < i - k + 1:
            window.pop(0)
        while window and arr[window[-1]] < arr[i]:
            window.pop()
        window.append(i)
        if i >= k - 1:
            result.append(arr[window[0]])
    return result
''',

    'clean_min_finder': '''
def find_min_max(arr):
    if not arr:
        raise ValueError("Empty array")
    lo = arr[0]
    hi = arr[0]
    for val in arr[1:]:
        if val < lo:
            lo = val
        if val > hi:
            hi = val
    return lo, hi

def min_of_rotated_sorted(arr):
    left = 0
    right = len(arr) - 1
    while left < right:
        mid = (left + right) // 2
        if arr[mid] > arr[right]:
            left = mid + 1
        else:
            right = mid
    return arr[left]

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

def minimum_difference_pair(arr):
    sorted_arr = sorted(arr)
    min_diff = float("inf")
    pair = (sorted_arr[0], sorted_arr[1])
    for i in range(len(sorted_arr) - 1):
        diff = sorted_arr[i + 1] - sorted_arr[i]
        if diff < min_diff:
            min_diff = diff
            pair = (sorted_arr[i], sorted_arr[i + 1])
    return pair, min_diff
''',

    'clean_flatten_list': '''
def flatten(nested):
    result = []
    stack = list(reversed(nested))
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            for sub in reversed(item):
                stack.append(sub)
        else:
            result.append(item)
    return result

def flatten_depth(nested, depth=1):
    if depth <= 0:
        return nested[:]
    result = []
    for item in nested:
        if isinstance(item, list) and depth > 0:
            result.extend(flatten_depth(item, depth - 1))
        else:
            result.append(item)
    return result

def group_by(items, key_func):
    groups = {}
    for item in items:
        key = key_func(item)
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
    return groups

def chunk_list(lst, size):
    chunks = []
    for i in range(0, len(lst), size):
        chunks.append(lst[i:i + size])
    return chunks

def interleave(list1, list2):
    result = []
    i = j = 0
    while i < len(list1) and j < len(list2):
        result.append(list1[i])
        result.append(list2[j])
        i += 1
        j += 1
    result.extend(list1[i:])
    result.extend(list2[j:])
    return result
''',

    'clean_zip_lists': '''
def zip_with(func, list1, list2):
    result = []
    length = min(len(list1), len(list2))
    for i in range(length):
        result.append(func(list1[i], list2[i]))
    return result

def unzip(pairs):
    if not pairs:
        return [], []
    firsts = []
    seconds = []
    for a, b in pairs:
        firsts.append(a)
        seconds.append(b)
    return firsts, seconds

def zip_longest(list1, list2, fillvalue=None):
    result = []
    length = max(len(list1), len(list2))
    for i in range(length):
        a = list1[i] if i < len(list1) else fillvalue
        b = list2[i] if i < len(list2) else fillvalue
        result.append((a, b))
    return result

def enumerate_from(iterable, start=0):
    result = []
    index = start
    for item in iterable:
        result.append((index, item))
        index += 1
    return result

def sliding_window(seq, size):
    windows = []
    for i in range(len(seq) - size + 1):
        windows.append(tuple(seq[i:i + size]))
    return windows
''',

    'clean_range_sum': '''
class PrefixSumArray:
    def __init__(self, arr):
        self.prefix = [0] * (len(arr) + 1)
        for i in range(len(arr)):
            self.prefix[i + 1] = self.prefix[i] + arr[i]
        self.original = arr[:]

    def range_sum(self, left, right):
        if left < 0 or right >= len(self.original):
            raise IndexError("Out of range")
        return self.prefix[right + 1] - self.prefix[left]

    def total_sum(self):
        return self.prefix[-1]

    def average(self, left, right):
        count = right - left + 1
        if count <= 0:
            return 0.0
        return self.range_sum(left, right) / count

    def find_subarray_with_sum(self, target):
        seen = {0: -1}
        for i in range(len(self.original)):
            prefix_val = self.prefix[i + 1]
            if prefix_val - target in seen:
                return (seen[prefix_val - target] + 1, i)
            seen[prefix_val] = i
        return None

    def max_subarray_sum(self):
        max_sum = self.original[0]
        current = self.original[0]
        for val in self.original[1:]:
            current = max(val, current + val)
            max_sum = max(max_sum, current)
        return max_sum
''',

    'clean_power_mod': '''
def power_mod(base, exp, mod):
    if mod == 1:
        return 0
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp = exp >> 1
        base = (base * base) % mod
    return result

def is_fermat_probable_prime(n, k=10):
    if n < 2:
        return False
    if n < 4:
        return True
    for a in range(2, min(k + 2, n)):
        if power_mod(a, n - 1, n) != 1:
            return False
    return True

def modular_multiply(a, b, mod):
    result = 0
    a = a % mod
    while b > 0:
        if b % 2 == 1:
            result = (result + a) % mod
        a = (a * 2) % mod
        b //= 2
    return result

def euler_totient(n):
    result = n
    p = 2
    temp = n
    while p * p <= temp:
        if temp % p == 0:
            while temp % p == 0:
                temp //= p
            result -= result // p
        p += 1
    if temp > 1:
        result -= result // temp
    return result
''',

    'clean_binary_convert': '''
def int_to_binary(n):
    if n == 0:
        return "0"
    negative = n < 0
    n = abs(n)
    bits = []
    while n > 0:
        bits.append(str(n % 2))
        n //= 2
    bits.reverse()
    result = "".join(bits)
    if negative:
        result = "-" + result
    return result

def binary_to_int(binary_str):
    negative = binary_str.startswith("-")
    if negative:
        binary_str = binary_str[1:]
    result = 0
    for bit in binary_str:
        result = result * 2 + int(bit)
    if negative:
        result = -result
    return result

def binary_add(a, b):
    max_len = max(len(a), len(b))
    a = a.zfill(max_len)
    b = b.zfill(max_len)
    carry = 0
    result = []
    for i in range(max_len - 1, -1, -1):
        total = int(a[i]) + int(b[i]) + carry
        result.append(str(total % 2))
        carry = total // 2
    if carry:
        result.append("1")
    result.reverse()
    return "".join(result)

def count_set_bits(n):
    count = 0
    while n:
        count += n & 1
        n >>= 1
    return count
''',

    'clean_hex_convert': '''
def int_to_hex(n):
    if n == 0:
        return "0"
    hex_chars = "0123456789abcdef"
    negative = n < 0
    n = abs(n)
    digits = []
    while n > 0:
        digits.append(hex_chars[n % 16])
        n //= 16
    digits.reverse()
    result = "".join(digits)
    if negative:
        result = "-" + result
    return result

def hex_to_int(hex_str):
    hex_str = hex_str.lower()
    negative = hex_str.startswith("-")
    if negative:
        hex_str = hex_str[1:]
    if hex_str.startswith("0x"):
        hex_str = hex_str[2:]
    result = 0
    for ch in hex_str:
        result *= 16
        if ch.isdigit():
            result += int(ch)
        else:
            result += ord(ch) - ord("a") + 10
    if negative:
        result = -result
    return result

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b)

def rgb_to_hex(r, g, b):
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return "#{:02x}{:02x}{:02x}".format(r, g, b)
''',

    'clean_char_count': '''
def char_frequency(text):
    freq = {}
    for ch in text:
        if ch in freq:
            freq[ch] += 1
        else:
            freq[ch] = 1
    return freq

def most_frequent_char(text):
    freq = char_frequency(text)
    best_char = None
    best_count = 0
    for ch, count in freq.items():
        if count > best_count:
            best_count = count
            best_char = ch
    return best_char, best_count

def unique_chars(text):
    seen = set()
    result = []
    for ch in text:
        if ch not in seen:
            seen.add(ch)
            result.append(ch)
    return result

def char_positions(text, target):
    positions = []
    for i, ch in enumerate(text):
        if ch == target:
            positions.append(i)
    return positions

def replace_chars(text, mapping):
    result = []
    for ch in text:
        if ch in mapping:
            result.append(mapping[ch])
        else:
            result.append(ch)
    return "".join(result)
''',

    'clean_word_split': '''
def split_words(text, delimiter=" "):
    words = []
    current = []
    for ch in text:
        if ch == delimiter:
            if current:
                words.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        words.append("".join(current))
    return words

def count_words(text):
    words = split_words(text)
    return len(words)

def title_case(text):
    words = split_words(text)
    titled = []
    for word in words:
        if word:
            titled.append(word[0].upper() + word[1:].lower())
    return " ".join(titled)

def camel_to_snake(name):
    result = [name[0].lower()]
    for ch in name[1:]:
        if ch.isupper():
            result.append("_")
            result.append(ch.lower())
        else:
            result.append(ch)
    return "".join(result)

def snake_to_camel(name):
    parts = name.split("_")
    result = parts[0]
    for part in parts[1:]:
        if part:
            result += part[0].upper() + part[1:]
    return result
''',

    'clean_list_dedup': '''
def deduplicate(lst):
    seen = set()
    result = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

def deduplicate_sorted(lst):
    if not lst:
        return []
    result = [lst[0]]
    for i in range(1, len(lst)):
        if lst[i] != lst[i - 1]:
            result.append(lst[i])
    return result

def find_duplicates(lst):
    seen = set()
    duplicates = set()
    for item in lst:
        if item in seen:
            duplicates.add(item)
        else:
            seen.add(item)
    return list(duplicates)

def remove_all_occurrences(lst, value):
    result = []
    for item in lst:
        if item != value:
            result.append(item)
    return result

def count_duplicates(lst):
    freq = {}
    for item in lst:
        freq[item] = freq.get(item, 0) + 1
    count = 0
    for v in freq.values():
        if v > 1:
            count += 1
    return count
''',

    'clean_interval_merge': '''
def merge_intervals(intervals):
    if not intervals:
        return []
    sorted_iv = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def insert_interval(intervals, new_interval):
    result = []
    i = 0
    n = len(intervals)
    while i < n and intervals[i][1] < new_interval[0]:
        result.append(intervals[i])
        i += 1
    while i < n and intervals[i][0] <= new_interval[1]:
        new_interval = (
            min(new_interval[0], intervals[i][0]),
            max(new_interval[1], intervals[i][1]),
        )
        i += 1
    result.append(new_interval)
    while i < n:
        result.append(intervals[i])
        i += 1
    return result

def interval_intersection(a_list, b_list):
    result = []
    i = j = 0
    while i < len(a_list) and j < len(b_list):
        lo = max(a_list[i][0], b_list[j][0])
        hi = min(a_list[i][1], b_list[j][1])
        if lo <= hi:
            result.append((lo, hi))
        if a_list[i][1] < b_list[j][1]:
            i += 1
        else:
            j += 1
    return result
''',

    'clean_roman_to_int': '''
def roman_to_int(s):
    values = {
        "I": 1, "V": 5, "X": 10, "L": 50,
        "C": 100, "D": 500, "M": 1000,
    }
    result = 0
    prev = 0
    for ch in reversed(s):
        curr = values.get(ch, 0)
        if curr < prev:
            result -= curr
        else:
            result += curr
        prev = curr
    return result

def int_to_roman(num):
    mappings = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    parts = []
    for value, symbol in mappings:
        while num >= value:
            parts.append(symbol)
            num -= value
    return "".join(parts)

def is_valid_roman(s):
    valid_chars = set("IVXLCDM")
    for ch in s:
        if ch not in valid_chars:
            return False
    converted = roman_to_int(s)
    back = int_to_roman(converted)
    return back == s
''',

    'clean_temperature_convert': '''
def celsius_to_fahrenheit(celsius):
    return celsius * 9.0 / 5.0 + 32.0

def fahrenheit_to_celsius(fahrenheit):
    return (fahrenheit - 32.0) * 5.0 / 9.0

def celsius_to_kelvin(celsius):
    return celsius + 273.15

def kelvin_to_celsius(kelvin):
    return kelvin - 273.15

def fahrenheit_to_kelvin(fahrenheit):
    celsius = fahrenheit_to_celsius(fahrenheit)
    return celsius_to_kelvin(celsius)

def kelvin_to_fahrenheit(kelvin):
    celsius = kelvin_to_celsius(kelvin)
    return celsius_to_fahrenheit(celsius)

def convert_temperature(value, from_unit, to_unit):
    from_unit = from_unit.upper()
    to_unit = to_unit.upper()
    if from_unit == to_unit:
        return value
    if from_unit == "C":
        if to_unit == "F":
            return celsius_to_fahrenheit(value)
        elif to_unit == "K":
            return celsius_to_kelvin(value)
    elif from_unit == "F":
        if to_unit == "C":
            return fahrenheit_to_celsius(value)
        elif to_unit == "K":
            return fahrenheit_to_kelvin(value)
    elif from_unit == "K":
        if to_unit == "C":
            return kelvin_to_celsius(value)
        elif to_unit == "F":
            return kelvin_to_fahrenheit(value)
    raise ValueError("Unknown units")
''',

    'clean_distance_calc': '''
import math

def euclidean_distance(p1, p2):
    total = 0.0
    for a, b in zip(p1, p2):
        total += (a - b) ** 2
    return math.sqrt(total)

def manhattan_distance(p1, p2):
    total = 0.0
    for a, b in zip(p1, p2):
        total += abs(a - b)
    return total

def chebyshev_distance(p1, p2):
    max_diff = 0.0
    for a, b in zip(p1, p2):
        diff = abs(a - b)
        if diff > max_diff:
            max_diff = diff
    return max_diff

def haversine_distance(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c

def cosine_similarity(v1, v2):
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)
''',

    'clean_bmi_calculator': '''
class HealthCalculator:
    def __init__(self, weight_kg, height_m, age, gender):
        self.weight = weight_kg
        self.height = height_m
        self.age = age
        self.gender = gender.lower()

    def bmi(self):
        if self.height <= 0:
            raise ValueError("Height must be positive")
        return self.weight / (self.height ** 2)

    def bmi_category(self):
        val = self.bmi()
        if val < 18.5:
            return "underweight"
        elif val < 25.0:
            return "normal"
        elif val < 30.0:
            return "overweight"
        else:
            return "obese"

    def bmr(self):
        if self.gender == "male":
            return (10 * self.weight + 6.25 * self.height * 100
                    - 5 * self.age + 5)
        else:
            return (10 * self.weight + 6.25 * self.height * 100
                    - 5 * self.age - 161)

    def daily_calories(self, activity_factor=1.55):
        return self.bmr() * activity_factor

    def ideal_weight_range(self):
        low = 18.5 * (self.height ** 2)
        high = 24.9 * (self.height ** 2)
        return round(low, 1), round(high, 1)

    def summary(self):
        return {
            "bmi": round(self.bmi(), 1),
            "category": self.bmi_category(),
            "bmr": round(self.bmr(), 0),
            "ideal_range": self.ideal_weight_range(),
        }
''',

    'clean_grade_calculator': '''
class GradeCalculator:
    def __init__(self):
        self.assignments = []
        self.weights = {}

    def add_category(self, name, weight):
        self.weights[name] = weight
        return self

    def add_score(self, category, score, max_score):
        self.assignments.append({
            "category": category,
            "score": score,
            "max_score": max_score,
        })
        return self

    def category_average(self, category):
        scores = [a for a in self.assignments if a["category"] == category]
        if not scores:
            return 0.0
        total_earned = sum(a["score"] for a in scores)
        total_possible = sum(a["max_score"] for a in scores)
        if total_possible == 0:
            return 0.0
        return total_earned / total_possible * 100

    def weighted_average(self):
        total_weight = 0.0
        weighted_sum = 0.0
        for category, weight in self.weights.items():
            avg = self.category_average(category)
            weighted_sum += avg * weight
            total_weight += weight
        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    def letter_grade(self):
        avg = self.weighted_average()
        if avg >= 93:
            return "A"
        elif avg >= 90:
            return "A-"
        elif avg >= 87:
            return "B+"
        elif avg >= 83:
            return "B"
        elif avg >= 80:
            return "B-"
        elif avg >= 77:
            return "C+"
        elif avg >= 73:
            return "C"
        elif avg >= 70:
            return "C-"
        elif avg >= 60:
            return "D"
        else:
            return "F"

    def summary(self):
        result = {}
        for cat in self.weights:
            result[cat] = round(self.category_average(cat), 1)
        result["overall"] = round(self.weighted_average(), 1)
        result["letter"] = self.letter_grade()
        return result
''',

}

H1_PROGRAMS = {
    'partial_search': '''
def search_collection(items, predicate):
    results = []
    for item in items:
        if predicate(item):
            results.append(item)
    if results:
        return results

def find_first(items, predicate):
    for i, item in enumerate(items):
        if predicate(item):
            return i, item

def find_last(items, predicate):
    last_idx = -1
    last_item = None
    for i, item in enumerate(items):
        if predicate(item):
            last_idx = i
            last_item = item
    if last_idx >= 0:
        return last_idx, last_item

def binary_search_approx(arr, target, tolerance):
    low, high = 0, len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if abs(arr[mid] - target) <= tolerance:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
''',

    'partial_validator': '''
class InputValidator:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def validate_email(self, email):
        if "@" in email and "." in email.split("@")[-1]:
            return True

    def validate_phone(self, phone):
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) == 10:
            return digits
        elif len(digits) == 11 and digits[0] == "1":
            return digits[1:]

    def validate_age(self, age):
        if isinstance(age, int) and 0 < age < 150:
            return True
        self.errors.append("Invalid age")

    def validate_name(self, name):
        cleaned = name.strip()
        if len(cleaned) >= 2:
            return cleaned
        self.errors.append("Name too short")

    def validate_postal_code(self, code):
        code = str(code).strip()
        if len(code) == 5 and code.isdigit():
            return code
        elif len(code) == 10 and code[5] == "-":
            return code

    def is_valid(self):
        return len(self.errors) == 0
''',

    'optional_lookup': '''
class Registry:
    def __init__(self):
        self.items = {}
        self.aliases = {}
        self.metadata = {}

    def register(self, key, value, alias=None):
        self.items[key] = value
        if alias:
            self.aliases[alias] = key

    def lookup(self, key):
        if key in self.items:
            return self.items[key]
        if key in self.aliases:
            real_key = self.aliases[key]
            return self.items.get(real_key)

    def lookup_with_metadata(self, key):
        value = self.lookup(key)
        if value is not None:
            meta = self.metadata.get(key, {})
            return {"value": value, "metadata": meta}

    def find_by_prefix(self, prefix):
        matches = []
        for key in self.items:
            if key.startswith(prefix):
                matches.append(key)
        if matches:
            return matches

    def unregister(self, key):
        if key in self.items:
            del self.items[key]
            return True
''',

    'missing_else_handler': '''
def process_request(method, path, body):
    response = {"status": 200, "body": ""}
    if method == "GET":
        response["body"] = handle_get(path)
        return response
    elif method == "POST":
        response["body"] = handle_post(path, body)
        response["status"] = 201
        return response
    elif method == "DELETE":
        handle_delete(path)
        response["status"] = 204
        return response

def handle_get(path):
    parts = path.strip("/").split("/")
    if len(parts) == 1:
        return "index of " + parts[0]
    elif len(parts) == 2:
        return "item " + parts[1] + " in " + parts[0]

def handle_post(path, body):
    if body:
        return "created at " + path

def handle_delete(path):
    parts = path.strip("/").split("/")
    if len(parts) >= 2:
        return "deleted " + parts[-1]
''',

    'mixed_return_types': '''
def parse_value(text):
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text.replace(".", "", 1).isdigit():
        return float(text)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1]
        return inner.split(",")
    return text

def coerce_type(value, target_type):
    if target_type == "int":
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    elif target_type == "float":
        if isinstance(value, (int, float)):
            return float(value)
    elif target_type == "str":
        return str(value)
    elif target_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")

def safe_divide(a, b):
    if b == 0:
        return float("inf")
    if isinstance(a, int) and isinstance(b, int):
        if a % b == 0:
            return a // b
        return a / b
    return float(a) / float(b)
''',

    'fallthrough_loop': '''
def find_pattern(text, patterns):
    for pattern in patterns:
        idx = text.find(pattern)
        if idx >= 0:
            return pattern, idx

def accumulate_until(values, threshold):
    total = 0
    count = 0
    for val in values:
        total += val
        count += 1
        if total >= threshold:
            return total, count

def first_match(records, filters):
    for record in records:
        match = True
        for key, expected in filters.items():
            if record.get(key) != expected:
                match = False
                break
        if match:
            return record

def extract_between(text, start_marker, end_marker):
    start_idx = text.find(start_marker)
    if start_idx >= 0:
        start_idx += len(start_marker)
        end_idx = text.find(end_marker, start_idx)
        if end_idx >= 0:
            return text[start_idx:end_idx]

def scan_for_anomaly(data, threshold):
    window_size = 5
    for i in range(len(data) - window_size + 1):
        window = data[i:i + window_size]
        avg = sum(window) / window_size
        for val in window:
            if abs(val - avg) > threshold:
                return i, val
''',

    'implicit_none_return': '''
class TaskProcessor:
    def __init__(self):
        self.tasks = []
        self.completed = []
        self.failed = []

    def add_task(self, task):
        self.tasks.append(task)

    def process_next(self):
        if self.tasks:
            task = self.tasks.pop(0)
            try:
                result = self._execute(task)
                self.completed.append(task)
                return result
            except Exception:
                self.failed.append(task)

    def _execute(self, task):
        action = task.get("action")
        if action == "compute":
            return task.get("value", 0) * 2
        elif action == "transform":
            data = task.get("data", "")
            return data.upper()
        elif action == "validate":
            data = task.get("data")
            if data and len(str(data)) > 0:
                return True

    def get_status(self):
        return {
            "pending": len(self.tasks),
            "completed": len(self.completed),
            "failed": len(self.failed),
        }

    def retry_failed(self):
        if self.failed:
            task = self.failed.pop(0)
            self.tasks.append(task)
            return task
''',

    'conditional_init': '''
class Connection:
    def __init__(self, config):
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 5432)
        self.connected = False
        self.cursor = None

    def connect(self):
        if not self.connected:
            self.connected = True
            return True

    def execute(self, query, params=None):
        if self.connected:
            result = {"query": query, "params": params, "rows": []}
            return result

    def fetch_one(self, query):
        result = self.execute(query)
        if result and result.get("rows"):
            return result["rows"][0]

    def fetch_all(self, query):
        result = self.execute(query)
        if result:
            return result.get("rows", [])

    def close(self):
        if self.connected:
            self.connected = False
            self.cursor = None
            return True

    def transaction(self, queries):
        results = []
        for q in queries:
            r = self.execute(q)
            if r is not None:
                results.append(r)
            else:
                return None
        return results
''',

    'maybe_transform': '''
def transform_record(record, schema):
    output = {}
    for field, spec in schema.items():
        raw = record.get(field)
        if raw is None and spec.get("required"):
            return None
        if raw is not None:
            converter = spec.get("type")
            if converter == "int":
                output[field] = int(raw)
            elif converter == "str":
                output[field] = str(raw)
            elif converter == "float":
                output[field] = float(raw)
            elif converter == "bool":
                output[field] = bool(raw)
            else:
                output[field] = raw
    return output

def batch_transform(records, schema):
    results = []
    errors = []
    for i, rec in enumerate(records):
        transformed = transform_record(rec, schema)
        if transformed is not None:
            results.append(transformed)
        else:
            errors.append(i)
    if results:
        return results, errors

def validate_schema(schema):
    valid_types = {"int", "str", "float", "bool", "list", "dict"}
    for field, spec in schema.items():
        if spec.get("type") not in valid_types:
            return False, field
    return True, None
''',

    'unguarded_access': '''
def get_nested(data, path, separator="."):
    keys = path.split(separator)
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and key.isdigit():
            idx = int(key)
            if idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current

def set_nested(data, path, value, separator="."):
    keys = path.split(separator)
    current = data
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value

def merge_dicts(base, override):
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result

def pluck(dicts, key):
    results = []
    for d in dicts:
        val = d.get(key)
        if val is not None:
            results.append(val)
    return results if results else None
''',

    'sparse_handler': '''
class EventDispatcher:
    def __init__(self):
        self.handlers = {}
        self.middleware = []

    def on(self, event_type, handler):
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)

    def use(self, middleware_fn):
        self.middleware.append(middleware_fn)

    def emit(self, event_type, data=None):
        for mw in self.middleware:
            data = mw(event_type, data)
            if data is None:
                return

        if event_type in self.handlers:
            results = []
            for handler in self.handlers[event_type]:
                result = handler(data)
                if result is not None:
                    results.append(result)
            if results:
                return results

    def remove_handler(self, event_type, handler):
        if event_type in self.handlers:
            self.handlers[event_type] = [
                h for h in self.handlers[event_type] if h != handler
            ]
            return True

    def clear(self, event_type=None):
        if event_type:
            self.handlers.pop(event_type, None)
        else:
            self.handlers.clear()
''',

    'half_implemented_api': '''
class CrudApi:
    def __init__(self):
        self.store = {}
        self.next_id = 1

    def create(self, data):
        item_id = self.next_id
        self.next_id += 1
        self.store[item_id] = {
            "id": item_id,
            "data": data,
            "created": True,
        }
        return item_id

    def read(self, item_id):
        if item_id in self.store:
            return self.store[item_id]

    def update(self, item_id, data):
        if item_id in self.store:
            self.store[item_id]["data"] = data
            return self.store[item_id]

    def delete(self, item_id):
        if item_id in self.store:
            item = self.store.pop(item_id)
            return item

    def list_all(self, page=1, per_page=10):
        items = list(self.store.values())
        start = (page - 1) * per_page
        end = start + per_page
        if start < len(items):
            return items[start:end]

    def search(self, predicate):
        matches = [v for v in self.store.values() if predicate(v)]
        if matches:
            return matches
''',

    'optional_config': '''
class AppConfig:
    def __init__(self, defaults=None):
        self.data = dict(defaults) if defaults else {}
        self.overrides = {}

    def set(self, key, value):
        self.overrides[key] = value

    def get(self, key, default=None):
        if key in self.overrides:
            return self.overrides[key]
        if key in self.data:
            return self.data[key]
        return default

    def get_int(self, key):
        val = self.get(key)
        if val is not None:
            return int(val)

    def get_bool(self, key):
        val = self.get(key)
        if val is not None:
            if isinstance(val, bool):
                return val
            return str(val).lower() in ("true", "1", "yes")

    def get_list(self, key, separator=","):
        val = self.get(key)
        if val is not None:
            if isinstance(val, list):
                return val
            return str(val).split(separator)

    def merge(self, other_config):
        for key, value in other_config.items():
            if key not in self.overrides:
                self.data[key] = value

    def to_dict(self):
        result = dict(self.data)
        result.update(self.overrides)
        return result
''',

    'partial_parser': '''
def parse_csv_line(line, delimiter=","):
    fields = []
    current = []
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == delimiter and not in_quotes:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    fields.append("".join(current).strip())
    return fields

def parse_key_value(text, pair_sep="&", kv_sep="="):
    result = {}
    pairs = text.split(pair_sep)
    for pair in pairs:
        if kv_sep in pair:
            key, value = pair.split(kv_sep, 1)
            result[key.strip()] = value.strip()
    if result:
        return result

def parse_headers(raw_headers):
    headers = {}
    for line in raw_headers.split("\n"):
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key.strip()] = value.strip()
    if headers:
        return headers

def parse_int_safe(text):
    text = text.strip()
    if text.lstrip("-").isdigit():
        return int(text)

def parse_float_safe(text):
    text = text.strip()
    try:
        return float(text)
    except (ValueError, TypeError):
        pass
''',

    'lazy_evaluator': '''
class LazySequence:
    def __init__(self, generator_fn):
        self.generator_fn = generator_fn
        self.cache = []
        self.exhausted = False

    def get(self, index):
        while len(self.cache) <= index and not self.exhausted:
            try:
                value = self.generator_fn(len(self.cache))
                self.cache.append(value)
            except StopIteration:
                self.exhausted = True
        if index < len(self.cache):
            return self.cache[index]

    def take(self, n):
        results = []
        for i in range(n):
            val = self.get(i)
            if val is not None:
                results.append(val)
        return results if results else None

    def find(self, predicate, max_search=1000):
        for i in range(max_search):
            val = self.get(i)
            if val is not None and predicate(val):
                return val

    def take_while(self, predicate):
        results = []
        i = 0
        while True:
            val = self.get(i)
            if val is None or not predicate(val):
                break
            results.append(val)
            i += 1
        return results if results else None
''',

    'incomplete_builder': '''
class QueryBuilder:
    def __init__(self, table):
        self.table = table
        self.conditions = []
        self.order = None
        self.limit_val = None
        self.fields = ["*"]

    def select(self, *fields):
        self.fields = list(fields)
        return self

    def where(self, condition):
        self.conditions.append(condition)
        return self

    def order_by(self, field, direction="ASC"):
        self.order = (field, direction)
        return self

    def limit(self, n):
        self.limit_val = n
        return self

    def build(self):
        parts = ["SELECT " + ", ".join(self.fields)]
        parts.append("FROM " + self.table)
        if self.conditions:
            parts.append("WHERE " + " AND ".join(self.conditions))
        if self.order:
            parts.append("ORDER BY " + self.order[0] + " " + self.order[1])
        if self.limit_val:
            parts.append("LIMIT " + str(self.limit_val))
        return " ".join(parts)

    def build_count(self):
        parts = ["SELECT COUNT(*)"]
        parts.append("FROM " + self.table)
        if self.conditions:
            parts.append("WHERE " + " AND ".join(self.conditions))
        return " ".join(parts)

    def build_delete(self):
        if self.conditions:
            return "DELETE FROM " + self.table + " WHERE " + " AND ".join(self.conditions)
''',

    'soft_matcher': '''
def fuzzy_match(text, pattern, threshold=0.6):
    text_lower = text.lower()
    pattern_lower = pattern.lower()
    if pattern_lower in text_lower:
        return 1.0
    score = _similarity_score(text_lower, pattern_lower)
    if score >= threshold:
        return score

def _similarity_score(a, b):
    if not a or not b:
        return 0.0
    matches = 0
    a_set = set(a)
    b_set = set(b)
    common = a_set & b_set
    if not common:
        return 0.0
    for ch in common:
        matches += min(a.count(ch), b.count(ch))
    max_len = max(len(a), len(b))
    return matches / max_len

def multi_match(text, patterns, threshold=0.6):
    results = []
    for pattern in patterns:
        score = fuzzy_match(text, pattern, threshold)
        if score is not None:
            results.append((pattern, score))
    if results:
        results.sort(key=lambda x: x[1], reverse=True)
        return results

def best_match(candidates, query, threshold=0.5):
    best = None
    best_score = threshold
    for candidate in candidates:
        score = fuzzy_match(candidate, query, threshold)
        if score is not None and score > best_score:
            best = candidate
            best_score = score
    if best is not None:
        return best, best_score
''',

    'approximate_search': '''
def levenshtein_distance(s1, s2):
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[m][n]

def find_closest(word, dictionary, max_distance=2):
    candidates = []
    for entry in dictionary:
        dist = levenshtein_distance(word, entry)
        if dist <= max_distance:
            candidates.append((entry, dist))
    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates

def autocomplete(prefix, words, limit=5):
    matches = []
    for word in words:
        if word.startswith(prefix):
            matches.append(word)
            if len(matches) >= limit:
                return matches
    if matches:
        return matches
''',

    'best_effort_decode': '''
def decode_utf8_lossy(data):
    result = []
    i = 0
    while i < len(data):
        byte = data[i]
        if byte < 0x80:
            result.append(chr(byte))
            i += 1
        elif byte < 0xC0:
            result.append("?")
            i += 1
        elif byte < 0xE0:
            if i + 1 < len(data):
                codepoint = ((byte & 0x1F) << 6) | (data[i + 1] & 0x3F)
                result.append(chr(codepoint))
                i += 2
            else:
                result.append("?")
                i += 1
        elif byte < 0xF0:
            if i + 2 < len(data):
                codepoint = (((byte & 0x0F) << 12) |
                             ((data[i + 1] & 0x3F) << 6) |
                             (data[i + 2] & 0x3F))
                result.append(chr(codepoint))
                i += 3
            else:
                result.append("?")
                i += 1
        else:
            result.append("?")
            i += 1
    return "".join(result)

def detect_encoding(data):
    if data[:3] == b"\xef\xbb\xbf":
        return "utf-8-bom"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    for b in data[:100]:
        if b > 127:
            return "unknown"
    return "ascii"
''',

    'partial_zip': '''
def partial_zip(iterables, strict=False):
    if strict:
        lengths = [len(it) for it in iterables]
        if len(set(lengths)) > 1:
            return None
    result = []
    min_len = min(len(it) for it in iterables) if iterables else 0
    for i in range(min_len):
        row = tuple(it[i] for it in iterables)
        result.append(row)
    return result

def zip_dict(keys, values):
    if len(keys) != len(values):
        return None
    result = {}
    for k, v in zip(keys, values):
        result[k] = v
    return result

def cartesian_product(list1, list2):
    result = []
    for a in list1:
        for b in list2:
            result.append((a, b))
    if result:
        return result

def transpose_matrix(matrix):
    if not matrix:
        return []
    rows = len(matrix)
    cols = len(matrix[0])
    result = []
    for j in range(cols):
        row = []
        for i in range(rows):
            if j < len(matrix[i]):
                row.append(matrix[i][j])
        if row:
            result.append(row)
    return result if result else None
''',

    'optional_merge': '''
def merge_sorted_lists(list1, list2):
    result = []
    i = j = 0
    while i < len(list1) and j < len(list2):
        if list1[i] <= list2[j]:
            result.append(list1[i])
            i += 1
        else:
            result.append(list2[j])
            j += 1
    result.extend(list1[i:])
    result.extend(list2[j:])
    return result

def merge_dicts_deep(a, b):
    result = dict(a)
    for key, val in b.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = merge_dicts_deep(result[key], val)
            elif isinstance(result[key], list) and isinstance(val, list):
                result[key] = result[key] + val
            else:
                result[key] = val
        else:
            result[key] = val
    return result

def merge_intervals_safe(intervals):
    if not intervals:
        return None
    sorted_iv = sorted(intervals, key=lambda x: x[0])
    merged = [list(sorted_iv[0])]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if len(merged) < len(intervals):
        return merged
''',

    'missing_default': '''
class TypeConverter:
    def __init__(self):
        self.converters = {}

    def register(self, type_name, converter_fn):
        self.converters[type_name] = converter_fn

    def convert(self, value, target_type):
        if target_type in self.converters:
            try:
                return self.converters[target_type](value)
            except (ValueError, TypeError):
                pass

    def convert_dict(self, data, schema):
        result = {}
        for key, target_type in schema.items():
            if key in data:
                converted = self.convert(data[key], target_type)
                if converted is not None:
                    result[key] = converted
                else:
                    result[key] = data[key]
        return result

    def batch_convert(self, values, target_type):
        results = []
        for v in values:
            converted = self.convert(v, target_type)
            if converted is not None:
                results.append(converted)
        if results:
            return results

    def can_convert(self, value, target_type):
        result = self.convert(value, target_type)
        return result is not None
''',

    'loose_converter': '''
def to_number(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.lstrip("-").replace(".", "", 1).isdigit():
            if "." in cleaned:
                return float(cleaned)
            return int(cleaned)

def to_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("true", "yes", "1", "on"):
            return True
        if lower in ("false", "no", "0", "off"):
            return False

def to_list(value, separator=None):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and separator:
        return [item.strip() for item in value.split(separator)]
    if isinstance(value, dict):
        return list(value.values())

def safe_cast(value, type_fn, default=None):
    try:
        return type_fn(value)
    except (ValueError, TypeError):
        return default

def normalize_whitespace(text):
    parts = text.split()
    if parts:
        return " ".join(parts)
''',

    'nullable_chain': '''
class Maybe:
    def __init__(self, value):
        self._value = value

    def map(self, func):
        if self._value is not None:
            try:
                result = func(self._value)
                return Maybe(result)
            except Exception:
                return Maybe(None)
        return Maybe(None)

    def flat_map(self, func):
        if self._value is not None:
            result = func(self._value)
            if isinstance(result, Maybe):
                return result
            return Maybe(result)
        return Maybe(None)

    def or_else(self, default):
        if self._value is not None:
            return self._value
        return default

    def filter(self, predicate):
        if self._value is not None and predicate(self._value):
            return Maybe(self._value)
        return Maybe(None)

    def is_present(self):
        return self._value is not None

    def if_present(self, consumer):
        if self._value is not None:
            consumer(self._value)
            return True
        return False
''',

    'partial_reduce': '''
def reduce_safe(values, func, initial=None):
    if not values:
        return initial
    if initial is not None:
        acc = initial
        start = 0
    else:
        acc = values[0]
        start = 1
    for i in range(start, len(values)):
        acc = func(acc, values[i])
    return acc

def reduce_while(values, func, predicate):
    if not values:
        return None
    acc = values[0]
    for val in values[1:]:
        if not predicate(acc):
            return acc
        acc = func(acc, val)
    return acc

def scan(values, func, initial=None):
    results = []
    if initial is not None:
        acc = initial
        results.append(acc)
    elif values:
        acc = values[0]
        results.append(acc)
        values = values[1:]
    else:
        return None
    for val in values:
        acc = func(acc, val)
        results.append(acc)
    return results

def group_reduce(items, key_func, reduce_func, initial=None):
    groups = {}
    for item in items:
        key = key_func(item)
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
    result = {}
    for key, group in groups.items():
        reduced = reduce_safe(group, reduce_func, initial)
        if reduced is not None:
            result[key] = reduced
    if result:
        return result
''',

}

H2_PROGRAMS = {
    'type_conflict_add': '''
def process_data(items):
    total = 0
    for item in items:
        if isinstance(item, str):
            total = str(total) + item
        else:
            total = total + item
    return total

def combine_values(a, b):
    result = a + b
    result = str(result)
    return result + 1

def accumulate(values):
    acc = 0
    for val in values:
        acc += val
    formatted = "Total: " + acc
    return formatted

def mixed_arithmetic(x, y, mode):
    if mode == "add":
        return x + y
    elif mode == "concat":
        return str(x) + str(y)
    result = x * y
    return result + " items"
''',

    'reassign_type_var': '''
def transform_input(data):
    result = []
    for item in data:
        result.append(item * 2)
    result = len(result)
    result = "Count: " + str(result)
    result = result.split(":")
    result = int(result[1])
    return result + 10

def process_pipeline(value):
    stage1 = value * 3
    stage1 = str(stage1)
    stage1 = list(stage1)
    stage1 = len(stage1)
    stage1 = stage1 > 2
    stage1 = stage1 + 1
    return stage1

def mutate_and_use(config):
    port = config.get("port", 8080)
    port = str(port)
    port = ":" + port
    port = port.split(":")
    port = port[-1]
    port = int(port) + 1
    connection = "localhost" + port
    return connection
''',

    'unreachable_after_return': '''
def calculate(x, y, op):
    if op == "add":
        return x + y
        result = x + y
        print("Added")
    elif op == "sub":
        return x - y
        print("Subtracted")
    elif op == "mul":
        return x * y
    else:
        return 0
    final = x ** y
    return final

def validate_and_process(data):
    if not data:
        return {"error": "empty"}
        log_error("empty data")
    if len(data) > 100:
        return {"error": "too large"}
        cleanup(data)
    processed = []
    for item in data:
        processed.append(item * 2)
    return {"result": processed}
    cache_result(processed)

def log_error(msg):
    pass

def cleanup(data):
    pass

def cache_result(data):
    pass
''',

    'contradictory_branches': '''
def classify_value(x):
    if x > 0:
        category = "positive"
        value = x * 2
    elif x < 0:
        category = "negative"
        value = abs(x)
    else:
        category = 0
        value = "zero"
    return category + ": " + str(value)

def handle_response(status_code, body):
    if status_code == 200:
        result = body
    elif status_code == 404:
        result = None
    else:
        result = status_code
    return len(result)

def process_input(value, mode):
    if mode == "number":
        output = int(value)
    elif mode == "text":
        output = str(value)
    else:
        output = [value]
    return output + output

def merge_results(a, b):
    if isinstance(a, list):
        combined = a + b
    elif isinstance(a, dict):
        combined = {**a, **b}
    else:
        combined = a + b
    return combined.upper()
''',

    'shadowed_variable': '''
def compute(data):
    result = 0
    for item in data:
        result = item * 2
        for result in range(item):
            pass
        final = result + 1
    return final

def process_list(items):
    items = len(items)
    items = str(items)
    items = items.split()
    for items in items:
        pass
    return items + 1

def nested_shadow(x):
    total = x
    for i in range(x):
        total = i
        for total in range(i):
            pass
        x = total + x
    return x

def accumulate_shadow(values):
    acc = 0
    for val in values:
        acc += val
        for acc in range(val):
            pass
    return acc * 2

def shadow_param(n):
    results = []
    for n in range(n):
        results.append(n)
        n = n + 1
    return n
''',

    'inconsistent_collection': '''
def build_collection(items, mode):
    if mode == "list":
        collection = []
        for item in items:
            collection.append(item)
    elif mode == "dict":
        collection = {}
        for i, item in enumerate(items):
            collection[i] = item
    elif mode == "set":
        collection = set()
        for item in items:
            collection.add(item)
    else:
        collection = tuple(items)
    collection.append("extra")
    return collection

def polymorphic_container(data_type, values):
    if data_type == "list":
        container = list(values)
    elif data_type == "tuple":
        container = tuple(values)
    elif data_type == "str":
        container = str(values)
    else:
        container = values
    container[0] = "modified"
    return container

def extend_collection(base, extra):
    if isinstance(base, list):
        base.extend(extra)
    elif isinstance(base, dict):
        base.update(extra)
    elif isinstance(base, set):
        base.update(extra)
    return base + extra
''',

    'broken_chain': '''
def pipeline(data):
    step1 = data.strip()
    step2 = step1.split(",")
    step3 = step2.sort()
    step4 = step3.join("-")
    return step4

def fluent_build(config):
    result = {}
    result["host"] = config.get("host")
    result["port"] = int(config.get("port"))
    result = result.items()
    result = dict(result)
    result = result.values()
    result = list(result)
    result = result.append("extra")
    return result.pop()

def transform_chain(text):
    words = text.split()
    lengths = words.map(len)
    total = lengths.reduce(lambda a, b: a + b)
    average = total / len(words)
    return round(average, 2)

def process_records(records):
    filtered = [r for r in records if r.get("active")]
    names = filtered.map(lambda r: r["name"])
    sorted_names = names.sort()
    return sorted_names[0]
''',

    'dead_code_block': '''
def compute_value(x, y):
    if True:
        return x + y
    result = x * y
    result = result ** 2
    formatted = "Result: {}".format(result)
    return formatted

def always_returns_early(data):
    if data:
        return data[0]
    else:
        return None
    processed = []
    for item in data:
        processed.append(item * 2)
    total = sum(processed)
    average = total / len(processed)
    return {"total": total, "average": average}

def unreachable_logic(flag):
    return flag
    if flag:
        x = 10
    else:
        x = 20
    y = x * 3
    z = y + flag
    return z

def redundant_check(value):
    if value is None:
        return 0
    if value is not None:
        return value * 2
    return value + 100
''',

    'conflicting_defaults': '''
def create_config(host="localhost", port="8080", debug=True):
    config = {
        "host": host,
        "port": port + 1,
        "debug": debug,
        "url": host + ":" + port,
    }
    return config

def init_connection(timeout=30, retry="3", max_connections=10.5):
    settings = {}
    settings["timeout_ms"] = timeout * 1000
    settings["retries"] = retry + 1
    settings["pool_size"] = max_connections // 2
    settings["label"] = "pool-" + max_connections
    return settings

def make_request(method="GET", body=None, headers=None):
    if headers is None:
        headers = {}
    request = {
        "method": method,
        "body": body,
        "headers": headers,
        "content_length": len(body),
    }
    return request

def format_output(data, indent=2, prefix=None):
    lines = []
    for key, value in data:
        line = " " * indent + prefix + str(key) + ": " + str(value)
        lines.append(line)
    return "\n".join(lines)
''',

    'mismatched_unpack': '''
def process_pairs(data):
    results = []
    for item in data:
        a, b, c = item
        results.append(a + b)
    return results

def parse_coordinates(text):
    parts = text.split(",")
    x, y = parts
    return float(x), float(y)

def unpack_config(config_tuple):
    host, port, db, user, password = config_tuple
    return {
        "host": host,
        "port": int(port),
        "database": db,
        "user": user,
        "password": password,
    }

def split_and_assign(line):
    name, age, email = line.split("|")
    age = int(age)
    return {"name": name, "age": age, "email": email}

def multi_return():
    data = {"a": 1, "b": 2}
    x, y, z = data.values()
    return x + y + z

def swap_values(a, b, c):
    a, b = b, c, a
    return a, b, c
''',

    'overwritten_result': '''
def compute_stats(values):
    result = sum(values) / len(values)
    result = max(values) - min(values)
    result = sorted(values)
    result = len(result)
    return result * result

def build_message(template, data):
    message = template
    for key, value in data.items():
        message = message.replace("{" + key + "}", value)
    message = len(message)
    message = message > 100
    return "Message: " + message

def process_sequence(seq):
    output = list(seq)
    output = [x * 2 for x in output]
    output = sum(output)
    output = str(output)
    output = output.split()
    output = int(output[0])
    output = output + " complete"
    return output

def calculate_score(grades):
    total = sum(grades)
    average = total / len(grades)
    average = str(average)
    average = round(average, 2)
    return average
''',

    'double_return_type': '''
def fetch_data(source, key):
    if source == "cache":
        return {"key": key, "value": 42, "source": "cache"}
    elif source == "db":
        return [key, 42, "db"]
    elif source == "api":
        return (key, 42)
    else:
        return key + ":42"

def process_item(item, strict=False):
    if strict:
        if not isinstance(item, dict):
            return -1
        return item.get("value", 0) * 2
    return str(item)

def compute_result(mode, data):
    if mode == "sum":
        return sum(data)
    elif mode == "join":
        return ",".join(data)
    elif mode == "count":
        return {v: data.count(v) for v in set(data)}
    result = data[0] + data[-1]
    return result

def aggregate(items, method):
    if method == "first":
        return items[0]
    elif method == "all":
        return items
    elif method == "count":
        return len(items)
    elif method == "summary":
        return "items=" + str(len(items))
''',

    'impossible_except': '''
def safe_operations(x, y):
    try:
        result = x + y
    except MemoryError:
        result = 0
    try:
        formatted = str(result)
    except UnicodeDecodeError:
        formatted = "error"
    return formatted

def over_handled(data):
    try:
        value = int(data)
    except ValueError:
        value = 0
    except TypeError:
        value = -1
    except OverflowError:
        value = float("inf")
    except AttributeError:
        value = None
    except IndexError:
        value = []
    except KeyError:
        value = {}
    return value

def nested_handlers(path):
    try:
        try:
            try:
                parts = path.split("/")
                result = parts[2]
                return int(result)
            except ValueError:
                return result
        except IndexError:
            return path
    except AttributeError:
        return None
    except Exception:
        return False
''',

    'stale_reference': '''
class DataStore:
    def __init__(self):
        self.data = {}
        self.cache = {}

    def set(self, key, value):
        self.data[key] = value
        self.cache[key] = value

    def update(self, key, value):
        old = self.cache.get(key)
        self.data[key] = value
        return old + value

    def delete(self, key):
        del self.data[key]
        cached = self.cache[key]
        del self.cache[key]
        return cached.upper()

    def get_or_compute(self, key, compute_fn):
        if key in self.cache:
            return self.cache[key]
        value = compute_fn(key)
        self.data[key] = value
        stale = self.cache[key]
        self.cache[key] = value
        return stale + value

    def clear(self):
        old_data = self.data
        self.data = {}
        self.cache = {}
        return old_data.values().sort()

    def keys(self):
        return self.data.keys() + self.cache.keys()
''',

    'wrong_operator_type': '''
def compare_items(a, b, mode):
    if mode == "equal":
        return a == b
    elif mode == "greater":
        return a > b
    elif mode == "contains":
        return a in b
    elif mode == "match":
        return a & b
    elif mode == "concat":
        return a | b
    result = a + b > 0
    return result

def bitwise_on_strings(text1, text2):
    result = text1 ^ text2
    masked = result & 0xFF
    return str(masked)

def arithmetic_mismatch(values, operator):
    if operator == "sum":
        total = 0
        for v in values:
            total += v
        return total
    elif operator == "product":
        total = 1
        for v in values:
            total *= v
        return total
    elif operator == "concat":
        result = ""
        for v in values:
            result += v
        return result - len(values)
    return values / len(values)
''',

    'circular_assignment': '''
def resolve_dependencies(graph):
    order = []
    visited = set()
    for node in graph:
        if node not in visited:
            _visit(node, graph, visited, order)
    return order

def _visit(node, graph, visited, order):
    visited.add(node)
    for dep in graph.get(node, []):
        if dep not in visited:
            _visit(dep, graph, visited, order)
    order.append(node)

def compute_circular(values):
    a = values.get("a", 0)
    b = a + values.get("b", 0)
    a = b + values.get("c", 0)
    b = a + b
    result = a + b
    result = result + a
    return result

def self_referential_config(base):
    config = dict(base)
    config["derived"] = config["base_url"] + config["path"]
    config["full"] = config["derived"] + "?" + config["query"]
    config["canonical"] = config["full"].replace(config["base_url"], "")
    config["normalized"] = config["canonical"].lower()
    config["hash"] = hash(config["normalized"])
    config["id"] = str(config["hash"])[:8]
    return config
''',

    'index_type_mismatch': '''
def access_collection(collection, key):
    if isinstance(key, int):
        return collection[key]
    elif isinstance(key, str):
        return collection[key]
    elif isinstance(key, slice):
        return collection[key]
    return collection[key.index]

def multi_index(data, indices):
    results = []
    for idx in indices:
        value = data[idx]
        results.append(value)
    return results

def matrix_access(matrix, row, col):
    value = matrix[row][col]
    neighbor_sum = (matrix[row - 1][col] + matrix[row + 1][col] +
                    matrix[row][col - 1] + matrix[row][col + 1])
    return value, neighbor_sum / 4

def dict_or_list(container, key):
    try:
        return container[key]
    except (KeyError, IndexError, TypeError):
        return None

def negative_index_math(arr, offset):
    last = arr[-1]
    target = arr[-offset]
    between = arr[-offset:-1]
    total = last + target
    total = total + sum(between)
    total = arr[total % len(arr)]
    return total
''',

    'format_string_error': '''
def format_report(data):
    header = "Report for {name} ({date})".format(**data)
    lines = []
    for item in data.get("items", []):
        line = "  - {name}: ${price:.2f} x {quantity}".format(**item)
        lines.append(line)
    total = "Total: ${:.2f}".format(data["total"])
    return header + "\n" + "\n".join(lines) + "\n" + total

def build_url(template, params):
    url = template
    for key, value in params.items():
        url = url.replace("{" + key + "}", value)
    return url

def interpolate(template, context):
    result = template
    for key in context:
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(context[key]))
    missing = result.count("{{")
    if missing > 0:
        return result + " [" + str(missing) + " unresolved]"
    return result

def log_message(level, msg, *args):
    formatted = msg % args
    timestamp = "2024-01-01"
    return "[{}] {}: {}".format(timestamp, level, formatted)
''',

    'nested_type_conflict': '''
def deep_process(data):
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            result[key] = deep_process(value)
        return result
    elif isinstance(data, list):
        return [deep_process(item) for item in data]
    elif isinstance(data, str):
        return len(data)
    elif isinstance(data, (int, float)):
        return str(data)
    return data

def flatten_and_sum(nested):
    total = 0
    for item in nested:
        if isinstance(item, list):
            total += flatten_and_sum(item)
        elif isinstance(item, str):
            total += item
        else:
            total += item
    return total

def recursive_merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        result = {}
        for key in set(list(a.keys()) + list(b.keys())):
            if key in a and key in b:
                result[key] = recursive_merge(a[key], b[key])
            elif key in a:
                result[key] = a[key]
            else:
                result[key] = b[key]
        return result
    return a + b
''',

    'mutation_after_freeze': '''
class FrozenConfig:
    def __init__(self, data):
        self._data = dict(data)
        self._frozen = False

    def freeze(self):
        self._frozen = True
        return self

    def set(self, key, value):
        self._data[key] = value
        return self

    def get(self, key):
        return self._data.get(key)

    def update(self, updates):
        for key, value in updates.items():
            self._data[key] = value
        return self

    def delete(self, key):
        del self._data[key]
        return self

    def to_dict(self):
        return dict(self._data)

    def keys(self):
        return list(self._data.keys())

    def merge_with(self, other):
        for key in other.keys():
            self._data[key] = other.get(key)
        return self
''',

    'incompatible_merge': '''
def merge_results(results):
    combined = results[0]
    for result in results[1:]:
        if isinstance(combined, dict) and isinstance(result, dict):
            combined.update(result)
        elif isinstance(combined, list) and isinstance(result, list):
            combined.extend(result)
        else:
            combined = combined + result
    return combined

def aggregate_metrics(metrics):
    totals = {}
    for metric in metrics:
        for key, value in metric.items():
            if key in totals:
                totals[key] = totals[key] + value
            else:
                totals[key] = value
    return totals

def join_outputs(outputs, separator):
    if not outputs:
        return ""
    first = outputs[0]
    for output in outputs[1:]:
        first = first + separator + output
    return first

def combine_configs(configs):
    base = configs[0].copy()
    for config in configs[1:]:
        for key, value in config.items():
            existing = base.get(key)
            if existing is not None:
                base[key] = existing + value
            else:
                base[key] = value
    return base
''',

    'conflicting_protocols': '''
class Serializable:
    def serialize(self):
        return str(self.__dict__)

class Comparable:
    def compare_to(self, other):
        return str(self) > str(other)

class DataRecord(Serializable, Comparable):
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def serialize(self):
        return self.name + ":" + self.value

    def compare_to(self, other):
        return self.value - other.value

    def merge(self, other):
        return DataRecord(
            self.name + "_" + other.name,
            self.value + other.value,
        )

    def transform(self):
        new_value = self.value * 2
        new_name = self.name.upper()
        return DataRecord(new_name, new_value)

    def validate(self):
        if len(self.name) > 0 and self.value >= 0:
            return True
        return False

    def to_tuple(self):
        return (self.name, self.value)
''',

    'broken_invariant': '''
class SortedList:
    def __init__(self):
        self.items = []

    def insert(self, value):
        self.items.append(value)
        return self

    def insert_at(self, index, value):
        self.items.insert(index, value)
        return self

    def remove(self, value):
        if value in self.items:
            self.items.remove(value)
        return self

    def get_min(self):
        if self.items:
            return self.items[0]
        return None

    def get_max(self):
        if self.items:
            return self.items[-1]
        return None

    def contains(self, value):
        lo, hi = 0, len(self.items) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.items[mid] == value:
                return True
            elif self.items[mid] < value:
                lo = mid + 1
            else:
                hi = mid - 1
        return False

    def get_range(self, low, high):
        return [x for x in self.items if low <= x <= high]

    def __len__(self):
        return len(self.items)
''',

    'redundant_conversion': '''
def over_convert(value):
    result = str(value)
    result = int(result)
    result = float(result)
    result = str(result)
    result = float(result)
    result = int(result)
    return result

def round_trip(data):
    text = str(data)
    parsed = eval(text)
    text2 = repr(parsed)
    final = eval(text2)
    return final

def type_juggle(x, y):
    a = str(x)
    b = str(y)
    c = int(a) + int(b)
    d = str(c)
    e = list(d)
    f = "".join(e)
    g = int(f)
    return g

def format_roundtrip(number):
    formatted = "{:.2f}".format(number)
    parsed = float(formatted)
    as_int = int(parsed)
    back = float(as_int)
    formatted2 = str(back)
    return float(formatted2)

def chain_conversions(items):
    result = list(items)
    result = tuple(result)
    result = list(result)
    result = set(result)
    result = list(result)
    result = sorted(result)
    return result
''',

    'split_personality_func': '''
def flexible_handler(data, mode="auto"):
    if mode == "auto":
        if isinstance(data, str):
            mode = "text"
        elif isinstance(data, list):
            mode = "list"
        elif isinstance(data, dict):
            mode = "dict"
        else:
            mode = "raw"

    if mode == "text":
        return data.upper().split()
    elif mode == "list":
        return {i: v for i, v in enumerate(data)}
    elif mode == "dict":
        return list(data.values())
    elif mode == "raw":
        return [data]
    return data

def process_any(value):
    result = value
    if hasattr(result, "strip"):
        result = result.strip().split(",")
    if hasattr(result, "append"):
        result.append("processed")
    if hasattr(result, "keys"):
        result = list(result.keys())
    result = str(result) + " done"
    return len(result)

def dynamic_dispatch(obj, method_name, *args):
    method = getattr(obj, method_name, None)
    if method is not None:
        return method(*args)
    return str(obj) + "." + method_name + "()"
''',

}

HINF_PROGRAMS = {
    'random_oracle': '''
import random

def random_partition(items, num_groups):
    groups = [[] for _ in range(num_groups)]
    for item in items:
        idx = random.randint(0, num_groups - 1)
        groups[idx].append(item)
    return groups

def monte_carlo_pi(num_samples):
    inside = 0
    for _ in range(num_samples):
        x = random.random()
        y = random.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return 4.0 * inside / num_samples

def random_walk(steps):
    position = 0
    path = [position]
    for _ in range(steps):
        direction = random.choice([-1, 1])
        position += direction
        path.append(position)
    return path

def shuffle_array(arr):
    result = arr[:]
    n = len(result)
    for i in range(n - 1, 0, -1):
        j = random.randint(0, i)
        result[i], result[j] = result[j], result[i]
    return result
''',

    'external_file_read': '''
import os

def read_config_file(path):
    if not os.path.exists(path):
        return {}
    config = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config

def list_directory(path, extension=None):
    if not os.path.isdir(path):
        return []
    entries = os.listdir(path)
    if extension:
        entries = [e for e in entries if e.endswith(extension)]
    return sorted(entries)

def file_stats(path):
    if not os.path.exists(path):
        return None
    stat = os.stat(path)
    return {
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "is_file": os.path.isfile(path),
        "is_dir": os.path.isdir(path),
    }

def read_lines(path, skip_empty=True):
    lines = []
    with open(path, "r") as f:
        for line in f:
            stripped = line.rstrip("\n")
            if skip_empty and not stripped.strip():
                continue
            lines.append(stripped)
    return lines
''',

    'dynamic_eval_exec': '''
def evaluate_expression(expr_str, variables=None):
    if variables is None:
        variables = {}
    safe_globals = {"__builtins__": {}}
    safe_globals.update(variables)
    result = eval(expr_str, safe_globals)
    return result

def build_and_execute(operations):
    results = []
    for op in operations:
        code = op.get("code", "")
        context = op.get("context", {})
        local_ns = dict(context)
        exec(code, {"__builtins__": {}}, local_ns)
        results.append(local_ns.get("result"))
    return results

def dynamic_class(name, fields):
    field_str = ", ".join(fields)
    init_params = ", ".join(["self"] + fields)
    init_body = "\n        ".join(
        [""] + ["self.{f} = {f}".format(f=f) for f in fields]
    )
    code = "class {name}:\n    def __init__({params}):{body}".format(
        name=name, params=init_params, body=init_body,
    )
    namespace = {}
    exec(code, namespace)
    return namespace[name]

def math_evaluator(formula, **kwargs):
    import math
    safe_ns = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    safe_ns.update(kwargs)
    return eval(formula, {"__builtins__": {}}, safe_ns)
''',

    'network_dependent': '''
import socket

def check_port(host, port, timeout=2):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
        return result == 0
    except socket.error:
        return False
    finally:
        sock.close()

def resolve_hostname(hostname):
    try:
        ip_address = socket.gethostbyname(hostname)
        return ip_address
    except socket.gaierror:
        return None

def scan_ports(host, start_port, end_port):
    open_ports = []
    for port in range(start_port, end_port + 1):
        if check_port(host, port, timeout=0.5):
            open_ports.append(port)
    return open_ports

def get_local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"

def create_echo_handler(data):
    response = data.upper()
    length = len(response)
    header = "Content-Length: {}\n".format(length)
    return header + response
''',

    'os_env_dependent': '''
import os

def get_env_config():
    config = {
        "debug": os.environ.get("DEBUG", "false").lower() == "true",
        "port": int(os.environ.get("PORT", "8080")),
        "host": os.environ.get("HOST", "0.0.0.0"),
        "workers": int(os.environ.get("WORKERS", "4")),
        "log_level": os.environ.get("LOG_LEVEL", "INFO"),
    }
    return config

def expand_path(path):
    expanded = os.path.expanduser(path)
    expanded = os.path.expandvars(expanded)
    expanded = os.path.abspath(expanded)
    return expanded

def ensure_directory(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        return True
    return os.path.isdir(path)

def temp_workspace():
    base = os.environ.get("TMPDIR", "/tmp")
    workspace = os.path.join(base, "workspace_" + str(os.getpid()))
    os.makedirs(workspace, exist_ok=True)
    return workspace

def platform_info():
    return {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "user": os.environ.get("USER", "unknown"),
        "home": os.path.expanduser("~"),
        "path_sep": os.sep,
    }
''',

    'time_dependent': '''
import time

def rate_limiter(max_calls, window_seconds):
    timestamps = []

    def is_allowed():
        now = time.time()
        cutoff = now - window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)
        if len(timestamps) < max_calls:
            timestamps.append(now)
            return True
        return False

    return is_allowed

def measure_execution(func, *args, **kwargs):
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return result, elapsed

def retry_with_backoff(func, max_retries=3, base_delay=1.0):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)
    return None

def create_timer():
    start_time = time.time()

    def elapsed():
        return time.time() - start_time

    def reset():
        nonlocal start_time
        start_time = time.time()

    return elapsed, reset
''',

    'stdin_reader': '''
import sys

def read_input_lines(prompt=""):
    lines = []
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    for line in sys.stdin:
        stripped = line.rstrip("\n")
        if stripped == "":
            break
        lines.append(stripped)
    return lines

def interactive_menu(options):
    for i, option in enumerate(options, 1):
        sys.stdout.write("{}. {}\n".format(i, option))
    sys.stdout.write("Choose: ")
    sys.stdout.flush()
    choice = sys.stdin.readline().strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return options[idx]
    return None

def progress_bar(current, total, width=40):
    fraction = current / total
    filled = int(width * fraction)
    bar = "#" * filled + "-" * (width - filled)
    pct = fraction * 100
    sys.stdout.write("\r[{}] {:.1f}%".format(bar, pct))
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")

def read_password():
    sys.stdout.write("Password: ")
    sys.stdout.flush()
    password = []
    for ch in sys.stdin.readline().rstrip("\n"):
        password.append(ch)
    return "".join(password)
''',

    'subprocess_caller': '''
import subprocess

def run_command(cmd, timeout=30):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "timeout", "returncode": -1}

def get_system_info():
    info = {}
    uname = run_command("uname -a")
    info["uname"] = uname.get("stdout", "").strip()
    df = run_command("df -h /")
    info["disk"] = df.get("stdout", "").strip()
    return info

def compile_and_run(source_file, language="python"):
    if language == "python":
        cmd = "python3 " + source_file
    elif language == "c":
        cmd = "gcc -o /tmp/a.out " + source_file + " && /tmp/a.out"
    else:
        return {"error": "unsupported language"}
    return run_command(cmd)

def pipe_commands(commands):
    full_cmd = " | ".join(commands)
    return run_command(full_cmd)
''',

    'global_state_mutation': '''
_registry = {}
_counter = [0]
_hooks = []

def register_handler(name, handler):
    global _registry
    _registry[name] = handler
    _counter[0] += 1
    return _counter[0]

def get_handler(name):
    return _registry.get(name)

def add_hook(hook_fn):
    _hooks.append(hook_fn)

def run_hooks(event, data):
    results = []
    for hook in _hooks:
        result = hook(event, data)
        results.append(result)
    return results

def reset_state():
    global _registry
    _registry = {}
    _counter[0] = 0
    _hooks.clear()

def get_stats():
    return {
        "handlers": len(_registry),
        "total_registered": _counter[0],
        "hooks": len(_hooks),
    }

def dispatch(name, *args, **kwargs):
    handler = get_handler(name)
    if handler is not None:
        run_hooks("before_" + name, args)
        result = handler(*args, **kwargs)
        run_hooks("after_" + name, result)
        return result
    return None
''',

    'monkey_patch': '''
def patch_method(cls, method_name, new_method):
    original = getattr(cls, method_name, None)
    setattr(cls, method_name, new_method)
    return original

def patch_attribute(obj, attr_name, new_value):
    original = getattr(obj, attr_name, None)
    setattr(obj, attr_name, new_value)
    return original

class Patchable:
    def __init__(self, data):
        self.data = data
        self._patches = {}

    def apply_patch(self, attr, value):
        self._patches[attr] = getattr(self, attr, None)
        setattr(self, attr, value)

    def revert_patch(self, attr):
        if attr in self._patches:
            setattr(self, attr, self._patches[attr])
            del self._patches[attr]
            return True
        return False

    def revert_all(self):
        for attr, original in self._patches.items():
            setattr(self, attr, original)
        self._patches.clear()

    def active_patches(self):
        return list(self._patches.keys())

    def snapshot(self):
        return dict(self.__dict__)
''',

    'dynamic_import': '''
import importlib

def load_module(module_name):
    try:
        module = importlib.import_module(module_name)
        return module
    except ImportError:
        return None

def get_attribute(module_name, attr_name):
    module = load_module(module_name)
    if module is not None:
        return getattr(module, attr_name, None)
    return None

def call_function(module_name, func_name, *args, **kwargs):
    func = get_attribute(module_name, func_name)
    if callable(func):
        return func(*args, **kwargs)
    return None

def list_module_contents(module_name):
    module = load_module(module_name)
    if module is None:
        return []
    contents = []
    for name in dir(module):
        if not name.startswith("_"):
            obj = getattr(module, name)
            contents.append({
                "name": name,
                "type": type(obj).__name__,
                "callable": callable(obj),
            })
    return contents

def reload_module(module_name):
    module = load_module(module_name)
    if module is not None:
        return importlib.reload(module)
    return None
''',

    'reflection_based': '''
def inspect_object(obj):
    info = {
        "type": type(obj).__name__,
        "id": id(obj),
        "size": 0,
    }
    attrs = []
    for name in dir(obj):
        if not name.startswith("_"):
            value = getattr(obj, name)
            attrs.append({
                "name": name,
                "type": type(value).__name__,
                "callable": callable(value),
            })
    info["attributes"] = attrs
    info["attr_count"] = len(attrs)
    return info

def call_by_name(obj, method_name, *args):
    method = getattr(obj, method_name, None)
    if method is not None and callable(method):
        return method(*args)
    raise AttributeError("No method: " + method_name)

def create_instance(class_name, bases, attrs):
    cls = type(class_name, bases, attrs)
    return cls()

def deep_copy_manual(obj):
    if isinstance(obj, dict):
        return {k: deep_copy_manual(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [deep_copy_manual(item) for item in obj]
    elif isinstance(obj, set):
        return {deep_copy_manual(item) for item in obj}
    elif isinstance(obj, tuple):
        return tuple(deep_copy_manual(item) for item in obj)
    return obj

def get_class_hierarchy(cls):
    hierarchy = []
    for base in cls.__mro__:
        hierarchy.append(base.__name__)
    return hierarchy
''',

    'pickle_loader': '''
import pickle
import io

def serialize_object(obj):
    buffer = io.BytesIO()
    pickle.dump(obj, buffer)
    data = buffer.getvalue()
    return data

def deserialize_object(data):
    buffer = io.BytesIO(data)
    obj = pickle.load(buffer)
    return obj

def round_trip(obj):
    data = serialize_object(obj)
    restored = deserialize_object(data)
    return restored

def serialize_to_file(obj, filepath):
    with open(filepath, "wb") as f:
        pickle.dump(obj, f)
    return True

def deserialize_from_file(filepath):
    with open(filepath, "rb") as f:
        obj = pickle.load(f)
    return obj

def clone_deep(obj):
    data = pickle.dumps(obj)
    return pickle.loads(data)

def batch_serialize(objects):
    results = []
    for obj in objects:
        try:
            data = pickle.dumps(obj)
            results.append({"success": True, "size": len(data)})
        except (pickle.PicklingError, TypeError):
            results.append({"success": False, "size": 0})
    return results
''',

    'signal_handler': '''
import signal
import sys

class GracefulShutdown:
    def __init__(self):
        self.shutdown_requested = False
        self.handlers = []
        self.original_sigint = None
        self.original_sigterm = None

    def register(self):
        self.original_sigint = signal.getsignal(signal.SIGINT)
        self.original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        self.shutdown_requested = True
        for handler in self.handlers:
            handler(signum)

    def add_cleanup(self, handler):
        self.handlers.append(handler)

    def unregister(self):
        if self.original_sigint:
            signal.signal(signal.SIGINT, self.original_sigint)
        if self.original_sigterm:
            signal.signal(signal.SIGTERM, self.original_sigterm)

    def should_stop(self):
        return self.shutdown_requested

    def run_until_stopped(self, tick_fn, interval=1.0):
        import time
        while not self.shutdown_requested:
            tick_fn()
            time.sleep(interval)
''',

    'thread_race': '''
import threading

class SharedCounter:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()

    def increment(self):
        with self.lock:
            self.value += 1

    def decrement(self):
        with self.lock:
            self.value -= 1

    def get(self):
        return self.value

class ThreadSafeQueue:
    def __init__(self, maxsize=0):
        self.items = []
        self.lock = threading.Lock()
        self.not_empty = threading.Condition(self.lock)
        self.maxsize = maxsize

    def put(self, item):
        with self.lock:
            self.items.append(item)
            self.not_empty.notify()

    def get(self, timeout=None):
        with self.not_empty:
            while not self.items:
                self.not_empty.wait(timeout=timeout)
                if not self.items:
                    return None
            return self.items.pop(0)

    def size(self):
        with self.lock:
            return len(self.items)

    def is_empty(self):
        with self.lock:
            return len(self.items) == 0
''',

}


def classify_from_prove(prove_obj, formal_obj):
    """Heuristic classification of obstruction class from CLI JSON output."""
    finfo = (prove_obj.get("files") or [{}])[0]
    verdict = finfo.get("verdict", "?")
    obs_list = finfo.get("obstructions", [])
    trust = finfo.get("trust", "UNVERIFIED")

    obs_van = formal_obj.get("formal_verification", {}).get(
        "obstruction_vanishing", {}
    )
    h1_val = obs_van.get("H1", "?")
    all_vanish = obs_van.get("all_vanish", False)

    desc = formal_obj.get("descent_locality", {})
    eff_desc = desc.get("effective_descent", {})
    obs_field = desc.get("obstruction_field", {})
    obs_field_trivial = obs_field.get("trivial", None)

    if verdict == "verified" and all_vanish and not obs_list:
        return "H0"
    elif verdict == "partial" and not obs_list:
        return "H1"
    elif obs_list:
        return "H2"
    else:
        return "Hinf"


def run_scenario(name, source, expected_class):
    """Run prove + descend on a program and collect results."""
    path = write_temp(source)

    # jugeo prove
    t0 = time.perf_counter()
    prove_objs = run_jugeo("prove", path)
    prove_wall = time.perf_counter() - t0
    prove = prove_objs[0] if prove_objs else {}
    formal = prove_objs[1] if len(prove_objs) > 1 else {}

    # jugeo descend
    t0 = time.perf_counter()
    descend_objs = run_jugeo("descend", path)
    descend_wall = time.perf_counter() - t0
    descend = descend_objs[0] if descend_objs else {}

    finfo = (prove.get("files") or [{}])[0]
    obs_van = formal.get("formal_verification", {}).get(
        "obstruction_vanishing", {}
    )
    desc_loc = formal.get("descent_locality", {})
    eff_desc = desc_loc.get("effective_descent", {})
    obs_field = desc_loc.get("obstruction_field", {})

    classified = classify_from_prove(prove, formal)

    result = {
        "name": name,
        "expected_class": expected_class,
        "classified_class": classified,
        "correct": classified == expected_class,
        "verdict": finfo.get("verdict", "?"),
        "trust": finfo.get("trust", "?"),
        "coordinates": finfo.get("coordinates", 0),
        "propositions_total": finfo.get("propositions_total", 0),
        "propositions_ok": finfo.get("propositions_ok", 0),
        "n_obstructions": len(finfo.get("obstructions", [])),
        "H1": obs_van.get("H1", "?"),
        "all_vanish": obs_van.get("all_vanish", None),
        "obs_field_trivial": obs_field.get("trivial", None),
        "effective_descent_ok": eff_desc.get("all_effective", None),
        "effective_descent_count": eff_desc.get("verified", 0),
        "descent_verdict": descend.get("verdict", "?"),
        "descent_trust": descend.get("trust", "?"),
        "descent_local_sections": descend.get("local_sections", 0),
        "descent_obstructions": len(descend.get("obstructions", [])),
        "prove_wall_s": round(prove_wall, 4),
        "descend_wall_s": round(descend_wall, 4),
    }

    try:
        os.unlink(path)
    except OSError:
        pass

    return result


def main():
    print("=" * 76)
    print("PAPER 3 \u2014 Cohomological Diagnostics: Classifying Why Proofs Fail")
    print("  All numbers from `python3 -m jugeo` CLI (subprocess)")
    print("=" * 76)
    print()

    all_results = []
    results_by_class = {"H0": [], "H1": [], "H2": [], "Hinf": []}

    # Run all scenarios
    scenario_groups = [
        (H0_PROGRAMS, "H0"),
        (H1_PROGRAMS, "H1"),
        (H2_PROGRAMS, "H2"),
        (HINF_PROGRAMS, "Hinf"),
    ]
    for programs, expected in scenario_groups:
        print(f"Running {expected} scenarios ({len(programs)} programs)...")
        for name, source in programs.items():
            r = run_scenario(name, source, expected)
            all_results.append(r)
            results_by_class[r["classified_class"]].append(r)

    total = len(all_results)
    print(f"\nRan {total} scenarios:")
    for cls in ["H0", "H1", "H2", "Hinf"]:
        expected_count = sum(
            1 for r in all_results if r["expected_class"] == cls
        )
        actual_count = len(results_by_class[cls])
        print(f"  Expected {cls}: {expected_count}  |  Classified as {cls}: {actual_count}")
    print()

    # Per-scenario detail table
    print("PER-SCENARIO RESULTS:")
    print("-" * 110)
    print(
        f"  {'Name':<28} {'Exp':>4} {'Got':>4} {'Verdict':<10} {'Trust':<20} "
        f"{'Coords':>6} {'Obs':>4} {'H\u00b9':>4} {'prove(s)':>9}"
    )
    print(f"  {'-'*100}")
    for r in all_results:
        mark = "\u2713" if r["correct"] else "\u2717"
        print(
            f"  {r['name']:<28} {r['expected_class']:>4} {r['classified_class']:>4}{mark} "
            f"{r['verdict']:<10} {r['trust']:<20} "
            f"{r['coordinates']:>6} {r['n_obstructions']:>4} "
            f"{str(r['H1']):>4} {r['prove_wall_s']:>9.4f}"
        )
    print()

    # Classification accuracy
    correct = sum(1 for r in all_results if r["correct"])
    print(f"Classification accuracy: {correct}/{total} ({100*correct/total:.1f}%)")
    mismatches = [
        (r["name"], r["expected_class"], r["classified_class"])
        for r in all_results
        if not r["correct"]
    ]
    if mismatches:
        print(f"  Mismatches ({len(mismatches)}):")
        for name, exp, got in mismatches[:20]:
            print(f"    {name}: expected {exp}, got {got}")
    print()

    # Per-class aggregated stats
    print("PER-CLASS AGGREGATE STATS:")
    print(
        f"  {'Class':<6} {'Count':>6} {'Avg coords':>11} {'Avg props':>10} "
        f"{'Avg obs':>8} {'Avg H1':>7} {'Avg prove(s)':>13} {'Avg desc(s)':>12}"
    )
    print(f"  {'-'*80}")
    for cls in ["H0", "H1", "H2", "Hinf"]:
        items = [r for r in all_results if r["expected_class"] == cls]
        if not items:
            continue
        avg_h1_vals = []
        for r in items:
            try:
                avg_h1_vals.append(float(r["H1"]))
            except (ValueError, TypeError):
                pass
        avg_h1 = sum(avg_h1_vals) / len(avg_h1_vals) if avg_h1_vals else 0
        print(
            f"  {cls:<6} {len(items):>6} "
            f"{sum(r['coordinates'] for r in items)/len(items):>11.1f} "
            f"{sum(r['propositions_total'] for r in items)/len(items):>10.1f} "
            f"{sum(r['n_obstructions'] for r in items)/len(items):>8.1f} "
            f"{avg_h1:>7.2f} "
            f"{sum(r['prove_wall_s'] for r in items)/len(items):>13.4f} "
            f"{sum(r['descend_wall_s'] for r in items)/len(items):>12.4f}"
        )
    print()

    # Descent analysis
    print("DESCENT ANALYSIS (from `jugeo descend`):")
    print(
        f"  {'Name':<28} {'Desc verdict':<14} {'Desc trust':<20} "
        f"{'Sections':>8} {'Desc obs':>9} {'Eff desc':>9}"
    )
    print(f"  {'-'*92}")
    for r in all_results:
        print(
            f"  {r['name']:<28} {r['descent_verdict']:<14} "
            f"{r['descent_trust']:<20} "
            f"{r['descent_local_sections']:>8} {r['descent_obstructions']:>9} "
            f"{str(r['effective_descent_ok']):>9}"
        )
    print()

    # H1 value distribution
    print("H1 VALUE DISTRIBUTION:")
    h1_by_class = {}
    for cls in ["H0", "H1", "H2", "Hinf"]:
        vals = []
        for r in all_results:
            if r["expected_class"] == cls:
                try:
                    vals.append(float(r["H1"]))
                except (ValueError, TypeError):
                    pass
        h1_by_class[cls] = vals
        if vals:
            print(
                f"  {cls}: min={min(vals):.2f} max={max(vals):.2f} "
                f"mean={sum(vals)/len(vals):.2f} count={len(vals)}"
            )
    print()

    # Effective descent analysis
    print("EFFECTIVE DESCENT SUMMARY:")
    for cls in ["H0", "H1", "H2", "Hinf"]:
        items = [r for r in all_results if r["expected_class"] == cls]
        eff_ok = sum(1 for r in items if r["effective_descent_ok"] is True)
        total_desc = sum(r["effective_descent_count"] for r in items)
        print(
            f"  {cls}: {eff_ok}/{len(items)} programs with all-effective descent, "
            f"total effective sections: {total_desc}"
        )
    print()

    # Bug detection on subset (H2 + Hinf)
    print("BUG DETECTION (from `jugeo bugs`) on H2 and Hinf subset:")
    print("-" * 76)
    bug_programs = list(H2_PROGRAMS.items())[:10] + list(HINF_PROGRAMS.items())[:10]
    bug_results = []
    for name, source in bug_programs:
        path = write_temp(source)
        objs = run_jugeo("bugs", path)
        bug_obj = objs[0] if objs else {}
        bugs_found = bug_obj.get("bugs", [])
        br = {
            "name": name,
            "bug_count": len(bugs_found),
            "severity_counts": {},
        }
        for bug in bugs_found:
            sev = bug.get("severity", "unknown")
            br["severity_counts"][sev] = br["severity_counts"].get(sev, 0) + 1
        bug_results.append(br)
        print(
            f"  {name:<28} bugs={br['bug_count']:>3} "
            f"severities={br['severity_counts']}"
        )
        try:
            os.unlink(path)
        except OSError:
            pass
    print()

    total_bugs = sum(br["bug_count"] for br in bug_results)
    print(f"Total bugs found across {len(bug_programs)} programs: {total_bugs}")
    print()

    # Diagnostic richness comparison
    jugeo_diagnostic_fields = 7
    jugeo_failure_classes = 4

    print("DIAGNOSTIC RICHNESS COMPARISON (JuGeo vs literature):")
    print(
        f"  {'System':<12} {'Diag fields':>12} {'Failure classes':>15} {'Source'}"
    )
    print("-" * 85)
    print(
        f"  {'JuGeo':<12} {jugeo_diagnostic_fields:>12} "
        f"{jugeo_failure_classes:>15} {'This experiment (measured)'}"
    )
    for name, info in LITERATURE_BASELINES.items():
        if name == "description":
            continue
        print(
            f"  {name:<12} {info['diagnostic_fields']:>12} "
            f"{info['failure_classes']:>15} {info['source']}"
        )
    print()

    max_lit_fields = max(
        info["diagnostic_fields"]
        for n, info in LITERATURE_BASELINES.items()
        if n != "description"
    )
    max_lit_classes = max(
        info["failure_classes"]
        for n, info in LITERATURE_BASELINES.items()
        if n != "description"
    )
    print(
        f"JuGeo provides {jugeo_diagnostic_fields} diagnostic fields "
        f"(vs max {max_lit_fields} in literature) \u2192 "
        f"{jugeo_diagnostic_fields/max_lit_fields:.1f}\u00d7 richer"
    )
    print(
        f"JuGeo distinguishes {jugeo_failure_classes} failure classes "
        f"(vs max {max_lit_classes} in literature) \u2192 "
        f"{jugeo_failure_classes/max_lit_classes:.1f}\u00d7 more granular"
    )
    print()

    # ── Save results ─────────────────────────────────────────────────────
    output = {
        "experiment": "descent_obstructions",
        "paper": 3,
        "note": "All JuGeo numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_scenarios": total,
        "classification_accuracy": round(correct / total, 4),
        "class_counts": {
            cls: sum(1 for r in all_results if r["expected_class"] == cls)
            for cls in ["H0", "H1", "H2", "Hinf"]
        },
        "classified_counts": {
            cls: len(results_by_class[cls])
            for cls in ["H0", "H1", "H2", "Hinf"]
        },
        "h1_distribution": {
            cls: {
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "mean": sum(vals)/len(vals) if vals else None,
                "count": len(vals),
            }
            for cls, vals in h1_by_class.items()
        },
        "bug_detection": {
            "programs_tested": len(bug_programs),
            "total_bugs": total_bugs,
            "per_program": bug_results,
        },
        "literature_baselines": LITERATURE_BASELINES,
        "jugeo_diagnostic_fields": jugeo_diagnostic_fields,
        "jugeo_failure_classes": jugeo_failure_classes,
        "all_results": all_results,
    }
    outpath = os.path.join(os.path.dirname(__file__), "results_paper03.json")
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"Results \u2192 {outpath}")


if __name__ == "__main__":
    main()
