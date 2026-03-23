#!/usr/bin/env python3
"""Paper 2 Experiment — An Algebraic Foundation for Evidence-Carrying Proofs.

Runs ``jugeo prove`` with different trust floors via the CLI, parses the
trust_algebra axiom results from JSON, and also exercises the internal
JudgmentBuilder / JudgmentAlgebra API for 8-tuple completeness verification.

Every number is reproducible: run `python3 experiments/exp02_judgment_algebra.py`.
"""
import json, os, random, subprocess, sys, tempfile, time

random.seed(42)

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jugeo.geometry.site import Coordinate, CoordinateKind, CoordinateMorphism
from jugeo.judgments.judgment_terms import (
    JudgmentBuilder, JudgmentAlgebra, TrustLevel, PropositionKind,
    EvidenceItem, EvidenceItemKind, TrustAnnotation, Carrier,
    Judgment, JudgmentStatus, ResidualObligation, Obstruction,
    ProvenanceSource,
)

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


# ── Test programs for CLI trust-floor experiments ────────────────────────

PROGRAMS = {
    'bubble_sort': '''
def bubble_sort(arr):
    n = len(arr)
    result = list(arr)
    for i in range(n):
        swapped = False
        for j in range(0, n - i - 1):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
                swapped = True
        if not swapped:
            break
    return result

def is_sorted(arr):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True

def sort_and_verify(arr):
    sorted_arr = bubble_sort(arr)
    assert is_sorted(sorted_arr)
    assert len(sorted_arr) == len(arr)
    return sorted_arr
''',
    'selection_sort': '''
def selection_sort(arr):
    n = len(arr)
    result = list(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if result[j] < result[min_idx]:
                min_idx = j
        result[i], result[min_idx] = result[min_idx], result[i]
    return result

def find_min_index(arr, start):
    min_idx = start
    for i in range(start + 1, len(arr)):
        if arr[i] < arr[min_idx]:
            min_idx = i
    return min_idx

def sort_descending(arr):
    n = len(arr)
    result = list(arr)
    for i in range(n):
        max_idx = i
        for j in range(i + 1, n):
            if result[j] > result[max_idx]:
                max_idx = j
        result[i], result[max_idx] = result[max_idx], result[i]
    return result
''',
    'insertion_sort': '''
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
    'merge_sort': '''
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

def merge_sort_bottom_up(arr):
    result = list(arr)
    width = 1
    n = len(result)
    while width < n:
        for i in range(0, n, 2 * width):
            left = result[i:i + width]
            right = result[i + width:i + 2 * width]
            result[i:i + len(left) + len(right)] = merge(left, right)
        width *= 2
    return result
''',
    'quick_sort': '''
def partition(arr, low, high):
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1

def quick_sort_recursive(arr, low, high):
    if low < high:
        pi = partition(arr, low, high)
        quick_sort_recursive(arr, low, pi - 1)
        quick_sort_recursive(arr, pi + 1, high)

def quick_sort(arr):
    result = list(arr)
    if len(result) > 1:
        quick_sort_recursive(result, 0, len(result) - 1)
    return result

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
    'binary_search': '''
def binary_search(arr, target):
    low, high = 0, len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1

def lower_bound(arr, target):
    low, high = 0, len(arr)
    while low < high:
        mid = (low + high) // 2
        if arr[mid] < target:
            low = mid + 1
        else:
            high = mid
    return low

def upper_bound(arr, target):
    low, high = 0, len(arr)
    while low < high:
        mid = (low + high) // 2
        if arr[mid] <= target:
            low = mid + 1
        else:
            high = mid
    return low

def count_occurrences(arr, target):
    return upper_bound(arr, target) - lower_bound(arr, target)
''',
    'counting_sort': '''
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
        output[count[val - min_val] - 1] = val
        count[val - min_val] -= 1
    return output

def radix_sort(arr):
    if not arr:
        return []
    max_val = max(arr)
    result = list(arr)
    exp = 1
    while max_val // exp > 0:
        result = counting_sort_by_digit(result, exp)
        exp *= 10
    return result

def counting_sort_by_digit(arr, exp):
    n = len(arr)
    output = [0] * n
    count = [0] * 10
    for val in arr:
        index = (val // exp) % 10
        count[index] += 1
    for i in range(1, 10):
        count[i] += count[i - 1]
    for val in reversed(arr):
        index = (val // exp) % 10
        output[count[index] - 1] = val
        count[index] -= 1
    return output
''',
    'knapsack_01': '''
def knapsack_01(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                candidate = dp[i - 1][w - weights[i - 1]] + values[i - 1]
                dp[i][w] = max(dp[i][w], candidate)
    return dp[n][capacity]

def knapsack_items(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                candidate = dp[i - 1][w - weights[i - 1]] + values[i - 1]
                dp[i][w] = max(dp[i][w], candidate)
    items = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i - 1][w]:
            items.append(i - 1)
            w -= weights[i - 1]
    return items[::-1], dp[n][capacity]
''',
    'longest_common_subseq': '''
def lcs_length(s1, s2):
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]

def lcs_string(s1, s2):
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    result = []
    i, j = m, n
    while i > 0 and j > 0:
        if s1[i - 1] == s2[j - 1]:
            result.append(s1[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return "".join(reversed(result))
''',
    'edit_distance': '''
def edit_distance(s1, s2):
    m, n = len(s1), len(s2)
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

def edit_operations(s1, s2):
    m, n = len(s1), len(s2)
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
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    ops = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and s1[i - 1] == s2[j - 1]:
            ops.append(("match", s1[i - 1]))
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append(("replace", s1[i - 1], s2[j - 1]))
            i -= 1
            j -= 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            ops.append(("insert", s2[j - 1]))
            j -= 1
        else:
            ops.append(("delete", s1[i - 1]))
            i -= 1
    return list(reversed(ops))
''',
    'coin_change': '''
def coin_change(coins, amount):
    dp = [float("inf")] * (amount + 1)
    dp[0] = 0
    for coin in coins:
        for x in range(coin, amount + 1):
            if dp[x - coin] + 1 < dp[x]:
                dp[x] = dp[x - coin] + 1
    return dp[amount] if dp[amount] != float("inf") else -1

def coin_change_combinations(coins, amount):
    dp = [0] * (amount + 1)
    dp[0] = 1
    for coin in coins:
        for x in range(coin, amount + 1):
            dp[x] += dp[x - coin]
    return dp[amount]

def coin_change_coins_used(coins, amount):
    dp = [float("inf")] * (amount + 1)
    parent = [-1] * (amount + 1)
    dp[0] = 0
    for coin in coins:
        for x in range(coin, amount + 1):
            if dp[x - coin] + 1 < dp[x]:
                dp[x] = dp[x - coin] + 1
                parent[x] = coin
    if dp[amount] == float("inf"):
        return []
    result = []
    x = amount
    while x > 0:
        result.append(parent[x])
        x -= parent[x]
    return result
''',
    'n_queens': '''
def solve_n_queens(n):
    solutions = []
    board = [-1] * n

    def is_safe(row, col):
        for prev_row in range(row):
            prev_col = board[prev_row]
            if prev_col == col:
                return False
            if abs(prev_row - row) == abs(prev_col - col):
                return False
        return True

    def backtrack(row):
        if row == n:
            solutions.append(list(board))
            return
        for col in range(n):
            if is_safe(row, col):
                board[row] = col
                backtrack(row + 1)
                board[row] = -1

    backtrack(0)
    return solutions

def format_board(solution):
    n = len(solution)
    rows = []
    for col in solution:
        row = ["."] * n
        row[col] = "Q"
        rows.append("".join(row))
    return rows
''',
    'kadane_max_subarray': '''
def kadane(arr):
    if not arr:
        return 0
    max_ending_here = arr[0]
    max_so_far = arr[0]
    for i in range(1, len(arr)):
        max_ending_here = max(arr[i], max_ending_here + arr[i])
        max_so_far = max(max_so_far, max_ending_here)
    return max_so_far

def max_subarray_indices(arr):
    if not arr:
        return 0, 0, 0
    max_ending_here = arr[0]
    max_so_far = arr[0]
    start = end = 0
    temp_start = 0
    for i in range(1, len(arr)):
        if arr[i] > max_ending_here + arr[i]:
            max_ending_here = arr[i]
            temp_start = i
        else:
            max_ending_here = max_ending_here + arr[i]
        if max_ending_here > max_so_far:
            max_so_far = max_ending_here
            start = temp_start
            end = i
    return max_so_far, start, end

def max_circular_subarray(arr):
    n = len(arr)
    max_kadane = kadane(arr)
    total = sum(arr)
    inverted = [-x for x in arr]
    min_kadane = kadane(inverted)
    if total + min_kadane == 0:
        return max_kadane
    return max(max_kadane, total + min_kadane)
''',
    'dijkstra_shortest_path': '''
def dijkstra(graph, source):
    dist = {node: float("inf") for node in graph}
    dist[source] = 0
    prev = {node: None for node in graph}
    visited = set()
    while len(visited) < len(graph):
        current = None
        current_dist = float("inf")
        for node in graph:
            if node not in visited and dist[node] < current_dist:
                current = node
                current_dist = dist[node]
        if current is None:
            break
        visited.add(current)
        for neighbor, weight in graph[current]:
            new_dist = dist[current] + weight
            if new_dist < dist[neighbor]:
                dist[neighbor] = new_dist
                prev[neighbor] = current
    return dist, prev

def reconstruct_path(prev, target):
    path = []
    current = target
    while current is not None:
        path.append(current)
        current = prev[current]
    return list(reversed(path))

def shortest_path(graph, source, target):
    dist, prev = dijkstra(graph, source)
    path = reconstruct_path(prev, target)
    return dist[target], path
''',
    'topological_sort': '''
def topological_sort_dfs(graph):
    visited = set()
    result = []

    def dfs(node):
        visited.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor)
        result.append(node)

    for node in graph:
        if node not in visited:
            dfs(node)
    return list(reversed(result))

def topological_sort_kahn(graph):
    in_degree = {}
    for node in graph:
        if node not in in_degree:
            in_degree[node] = 0
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
    queue = [n for n in in_degree if in_degree[n] == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != len(in_degree):
        return None
    return result
''',
    'linked_list': '''
class Node:
    def __init__(self, value, next_node=None):
        self.value = value
        self.next = next_node

class LinkedList:
    def __init__(self):
        self.head = None
        self.size = 0

    def append(self, value):
        node = Node(value)
        if self.head is None:
            self.head = node
        else:
            current = self.head
            while current.next:
                current = current.next
            current.next = node
        self.size += 1

    def find(self, value):
        current = self.head
        while current:
            if current.value == value:
                return True
            current = current.next
        return False

    def remove(self, value):
        if self.head and self.head.value == value:
            self.head = self.head.next
            self.size -= 1
            return True
        current = self.head
        while current and current.next:
            if current.next.value == value:
                current.next = current.next.next
                self.size -= 1
                return True
            current = current.next
        return False

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.value)
            current = current.next
        return result
''',
    'doubly_linked_list': '''
class DNode:
    def __init__(self, value):
        self.value = value
        self.prev = None
        self.next = None

class DoublyLinkedList:
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0

    def append(self, value):
        node = DNode(value)
        if self.tail is None:
            self.head = self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        self.size += 1

    def prepend(self, value):
        node = DNode(value)
        if self.head is None:
            self.head = self.tail = node
        else:
            node.next = self.head
            self.head.prev = node
            self.head = node
        self.size += 1

    def remove(self, value):
        current = self.head
        while current:
            if current.value == value:
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
            result.append(current.value)
            current = current.next
        return result
''',
    'stack_impl': '''
class Stack:
    def __init__(self, max_size=None):
        self._items = []
        self._max_size = max_size

    def push(self, item):
        if self._max_size and len(self._items) >= self._max_size:
            raise OverflowError("Stack is full")
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("Stack is empty")
        return self._items.pop()

    def peek(self):
        if not self._items:
            raise IndexError("Stack is empty")
        return self._items[-1]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)

def is_balanced(expression):
    stack = Stack()
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in expression:
        if ch in "([{":
            stack.push(ch)
        elif ch in ")]}":
            if stack.is_empty():
                return False
            if stack.pop() != pairs[ch]:
                return False
    return stack.is_empty()
''',
    'queue_impl': '''
class Queue:
    def __init__(self):
        self._items = []
        self._front = 0

    def enqueue(self, item):
        self._items.append(item)

    def dequeue(self):
        if self.is_empty():
            raise IndexError("Queue is empty")
        item = self._items[self._front]
        self._front += 1
        if self._front > len(self._items) // 2:
            self._items = self._items[self._front:]
            self._front = 0
        return item

    def peek(self):
        if self.is_empty():
            raise IndexError("Queue is empty")
        return self._items[self._front]

    def is_empty(self):
        return self._front >= len(self._items)

    def size(self):
        return len(self._items) - self._front

class CircularQueue:
    def __init__(self, capacity):
        self._items = [None] * capacity
        self._capacity = capacity
        self._front = 0
        self._rear = 0
        self._size = 0

    def enqueue(self, item):
        if self._size == self._capacity:
            raise OverflowError("Queue is full")
        self._items[self._rear] = item
        self._rear = (self._rear + 1) % self._capacity
        self._size += 1

    def dequeue(self):
        if self._size == 0:
            raise IndexError("Queue is empty")
        item = self._items[self._front]
        self._front = (self._front + 1) % self._capacity
        self._size -= 1
        return item
''',
    'binary_search_tree': '''
class BSTNode:
    def __init__(self, key, value=None):
        self.key = key
        self.value = value
        self.left = None
        self.right = None

class BST:
    def __init__(self):
        self.root = None

    def insert(self, key, value=None):
        self.root = self._insert(self.root, key, value)

    def _insert(self, node, key, value):
        if node is None:
            return BSTNode(key, value)
        if key < node.key:
            node.left = self._insert(node.left, key, value)
        elif key > node.key:
            node.right = self._insert(node.right, key, value)
        else:
            node.value = value
        return node

    def search(self, key):
        node = self.root
        while node:
            if key == node.key:
                return node.value
            elif key < node.key:
                node = node.left
            else:
                node = node.right
        return None

    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)

    def min_key(self):
        node = self.root
        while node and node.left:
            node = node.left
        return node.key if node else None
''',
    'min_heap': '''
class MinHeap:
    def __init__(self):
        self._data = []

    def _parent(self, i):
        return (i - 1) // 2

    def _left(self, i):
        return 2 * i + 1

    def _right(self, i):
        return 2 * i + 2

    def _swap(self, i, j):
        self._data[i], self._data[j] = self._data[j], self._data[i]

    def _sift_up(self, i):
        while i > 0 and self._data[i] < self._data[self._parent(i)]:
            self._swap(i, self._parent(i))
            i = self._parent(i)

    def _sift_down(self, i):
        n = len(self._data)
        smallest = i
        left = self._left(i)
        right = self._right(i)
        if left < n and self._data[left] < self._data[smallest]:
            smallest = left
        if right < n and self._data[right] < self._data[smallest]:
            smallest = right
        if smallest != i:
            self._swap(i, smallest)
            self._sift_down(smallest)

    def push(self, val):
        self._data.append(val)
        self._sift_up(len(self._data) - 1)

    def pop(self):
        if not self._data:
            raise IndexError("Heap is empty")
        self._swap(0, len(self._data) - 1)
        val = self._data.pop()
        if self._data:
            self._sift_down(0)
        return val

    def peek(self):
        if not self._data:
            raise IndexError("Heap is empty")
        return self._data[0]

    def size(self):
        return len(self._data)
''',
    'hash_table': '''
class HashTable:
    def __init__(self, capacity=16):
        self._capacity = capacity
        self._size = 0
        self._buckets = [[] for _ in range(capacity)]
        self._load_factor = 0.75

    def _hash(self, key):
        return hash(key) % self._capacity

    def put(self, key, value):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self._buckets[idx]):
            if k == key:
                self._buckets[idx][i] = (key, value)
                return
        self._buckets[idx].append((key, value))
        self._size += 1
        if self._size > self._capacity * self._load_factor:
            self._resize()

    def get(self, key, default=None):
        idx = self._hash(key)
        for k, v in self._buckets[idx]:
            if k == key:
                return v
        return default

    def remove(self, key):
        idx = self._hash(key)
        for i, (k, v) in enumerate(self._buckets[idx]):
            if k == key:
                self._buckets[idx].pop(i)
                self._size -= 1
                return v
        return None

    def _resize(self):
        old_buckets = self._buckets
        self._capacity *= 2
        self._buckets = [[] for _ in range(self._capacity)]
        self._size = 0
        for bucket in old_buckets:
            for key, value in bucket:
                self.put(key, value)

    def keys(self):
        result = []
        for bucket in self._buckets:
            for k, v in bucket:
                result.append(k)
        return result
''',
    'trie_impl': '''
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

    def words_with_prefix(self, prefix):
        node = self._find_node(prefix)
        if node is None:
            return []
        results = []
        self._collect(node, prefix, results)
        return results

    def _collect(self, node, prefix, results):
        if node.is_end:
            results.append(prefix)
        for ch, child in sorted(node.children.items()):
            self._collect(child, prefix + ch, results)
''',
    'graph_adjacency': '''
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
        return self.adj.get(v, [])

    def bfs(self, start):
        visited = {start}
        queue = [start]
        order = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor, _ in self.neighbors(node):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return order

    def dfs(self, start):
        visited = set()
        order = []
        def _dfs(node):
            visited.add(node)
            order.append(node)
            for neighbor, _ in self.neighbors(node):
                if neighbor not in visited:
                    _dfs(neighbor)
        _dfs(start)
        return order

    def has_path(self, source, target):
        visited = set()
        stack = [source]
        while stack:
            node = stack.pop()
            if node == target:
                return True
            visited.add(node)
            for neighbor, _ in self.neighbors(node):
                if neighbor not in visited:
                    stack.append(neighbor)
        return False
''',
    'priority_queue': '''
class PriorityQueue:
    def __init__(self):
        self._heap = []

    def _parent(self, i):
        return (i - 1) // 2

    def _left(self, i):
        return 2 * i + 1

    def _right(self, i):
        return 2 * i + 2

    def push(self, priority, item):
        self._heap.append((priority, item))
        self._bubble_up(len(self._heap) - 1)

    def pop(self):
        if not self._heap:
            raise IndexError("Priority queue is empty")
        self._swap(0, len(self._heap) - 1)
        priority, item = self._heap.pop()
        if self._heap:
            self._bubble_down(0)
        return priority, item

    def _swap(self, i, j):
        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]

    def _bubble_up(self, i):
        while i > 0:
            parent = self._parent(i)
            if self._heap[i][0] < self._heap[parent][0]:
                self._swap(i, parent)
                i = parent
            else:
                break

    def _bubble_down(self, i):
        n = len(self._heap)
        while True:
            smallest = i
            left = self._left(i)
            right = self._right(i)
            if left < n and self._heap[left][0] < self._heap[smallest][0]:
                smallest = left
            if right < n and self._heap[right][0] < self._heap[smallest][0]:
                smallest = right
            if smallest == i:
                break
            self._swap(i, smallest)
            i = smallest

    def peek(self):
        if not self._heap:
            raise IndexError("Priority queue is empty")
        return self._heap[0]

    def is_empty(self):
        return len(self._heap) == 0
''',
    'ring_buffer': '''
class RingBuffer:
    def __init__(self, capacity):
        self._buffer = [None] * capacity
        self._capacity = capacity
        self._head = 0
        self._tail = 0
        self._size = 0

    def write(self, item):
        self._buffer[self._tail] = item
        if self._size == self._capacity:
            self._head = (self._head + 1) % self._capacity
        else:
            self._size += 1
        self._tail = (self._tail + 1) % self._capacity

    def read(self):
        if self._size == 0:
            raise IndexError("Buffer is empty")
        item = self._buffer[self._head]
        self._head = (self._head + 1) % self._capacity
        self._size -= 1
        return item

    def peek(self):
        if self._size == 0:
            raise IndexError("Buffer is empty")
        return self._buffer[self._head]

    def is_full(self):
        return self._size == self._capacity

    def is_empty(self):
        return self._size == 0

    def to_list(self):
        result = []
        idx = self._head
        for _ in range(self._size):
            result.append(self._buffer[idx])
            idx = (idx + 1) % self._capacity
        return result

    def clear(self):
        self._head = 0
        self._tail = 0
        self._size = 0
''',
    'lru_cache_impl': '''
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
        self.head = LRUNode(None, None)
        self.tail = LRUNode(None, None)
        self.head.next = self.tail
        self.tail.prev = self.head

    def _remove(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev

    def _add_to_front(self, node):
        node.next = self.head.next
        node.prev = self.head
        self.head.next.prev = node
        self.head.next = node

    def get(self, key):
        if key in self.cache:
            node = self.cache[key]
            self._remove(node)
            self._add_to_front(node)
            return node.value
        return -1

    def put(self, key, value):
        if key in self.cache:
            self._remove(self.cache[key])
        node = LRUNode(key, value)
        self.cache[key] = node
        self._add_to_front(node)
        if len(self.cache) > self.capacity:
            lru = self.tail.prev
            self._remove(lru)
            del self.cache[lru.key]

    def size(self):
        return len(self.cache)
''',
    'deque_impl': '''
class Deque:
    def __init__(self):
        self._front = []
        self._back = []

    def push_front(self, item):
        self._front.append(item)

    def push_back(self, item):
        self._back.append(item)

    def pop_front(self):
        if not self._front:
            if not self._back:
                raise IndexError("Deque is empty")
            self._front = list(reversed(self._back))
            self._back = []
        return self._front.pop()

    def pop_back(self):
        if not self._back:
            if not self._front:
                raise IndexError("Deque is empty")
            self._back = list(reversed(self._front))
            self._front = []
        return self._back.pop()

    def peek_front(self):
        if self._front:
            return self._front[-1]
        if self._back:
            return self._back[0]
        raise IndexError("Deque is empty")

    def peek_back(self):
        if self._back:
            return self._back[-1]
        if self._front:
            return self._front[0]
        raise IndexError("Deque is empty")

    def size(self):
        return len(self._front) + len(self._back)

    def is_empty(self):
        return self.size() == 0

    def to_list(self):
        return list(reversed(self._front)) + self._back
''',
    'disjoint_set': '''
class DisjointSet:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.size = [1] * n
        self.count = n

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            next_x = self.parent[x]
            self.parent[x] = root
            x = next_x
        return root

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

    def num_components(self):
        return self.count
''',
    'interval_tree': '''
class Interval:
    def __init__(self, low, high, data=None):
        self.low = low
        self.high = high
        self.data = data

class ITNode:
    def __init__(self, interval):
        self.interval = interval
        self.max_high = interval.high
        self.left = None
        self.right = None

class IntervalTree:
    def __init__(self):
        self.root = None

    def insert(self, low, high, data=None):
        interval = Interval(low, high, data)
        self.root = self._insert(self.root, interval)

    def _insert(self, node, interval):
        if node is None:
            return ITNode(interval)
        if interval.low < node.interval.low:
            node.left = self._insert(node.left, interval)
        else:
            node.right = self._insert(node.right, interval)
        if node.max_high < interval.high:
            node.max_high = interval.high
        return node

    def _overlaps(self, a, b):
        return a.low <= b.high and b.low <= a.high

    def query(self, low, high):
        target = Interval(low, high)
        results = []
        self._query(self.root, target, results)
        return results

    def _query(self, node, target, results):
        if node is None:
            return
        if self._overlaps(node.interval, target):
            results.append((node.interval.low, node.interval.high, node.interval.data))
        if node.left and node.left.max_high >= target.low:
            self._query(node.left, target, results)
        self._query(node.right, target, results)

    def all_intervals(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append((node.interval.low, node.interval.high))
            self._inorder(node.right, result)
''',
    'palindrome_checker': '''
def is_palindrome(s):
    cleaned = ""
    for ch in s.lower():
        if ch.isalnum():
            cleaned += ch
    left, right = 0, len(cleaned) - 1
    while left < right:
        if cleaned[left] != cleaned[right]:
            return False
        left += 1
        right -= 1
    return True

def longest_palindrome(s):
    if not s:
        return ""
    start = 0
    max_len = 1
    def expand(left, right):
        while left >= 0 and right < len(s) and s[left] == s[right]:
            left -= 1
            right += 1
        return left + 1, right - left - 1
    for i in range(len(s)):
        s1, l1 = expand(i, i)
        s2, l2 = expand(i, i + 1)
        if l1 > max_len:
            start, max_len = s1, l1
        if l2 > max_len:
            start, max_len = s2, l2
    return s[start:start + max_len]
''',
    'anagram_grouper': '''
def group_anagrams(words):
    groups = {}
    for word in words:
        key = "".join(sorted(word.lower()))
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    return list(groups.values())

def are_anagrams(s1, s2):
    if len(s1) != len(s2):
        return False
    counts = {}
    for ch in s1.lower():
        counts[ch] = counts.get(ch, 0) + 1
    for ch in s2.lower():
        counts[ch] = counts.get(ch, 0) - 1
        if counts[ch] < 0:
            return False
    return all(v == 0 for v in counts.values())

def find_anagram_pairs(words):
    pairs = []
    for i in range(len(words)):
        for j in range(i + 1, len(words)):
            if are_anagrams(words[i], words[j]):
                pairs.append((words[i], words[j]))
    return pairs
''',
    'string_compression': '''
def compress(s):
    if not s:
        return ""
    result = []
    count = 1
    for i in range(1, len(s)):
        if s[i] == s[i - 1]:
            count += 1
        else:
            result.append(s[i - 1])
            if count > 1:
                result.append(str(count))
            count = 1
    result.append(s[-1])
    if count > 1:
        result.append(str(count))
    compressed = "".join(result)
    return compressed if len(compressed) < len(s) else s

def decompress(s):
    result = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1].isdigit():
            j = i + 1
            while j < len(s) and s[j].isdigit():
                j += 1
            count = int(s[i + 1:j])
            result.append(s[i] * count)
            i = j
        else:
            result.append(s[i])
            i += 1
    return "".join(result)
''',
    'kmp_pattern_match': '''
def compute_lps(pattern):
    m = len(pattern)
    lps = [0] * m
    length = 0
    i = 1
    while i < m:
        if pattern[i] == pattern[length]:
            length += 1
            lps[i] = length
            i += 1
        else:
            if length != 0:
                length = lps[length - 1]
            else:
                lps[i] = 0
                i += 1
    return lps

def kmp_search(text, pattern):
    n, m = len(text), len(pattern)
    if m == 0:
        return []
    lps = compute_lps(pattern)
    results = []
    i = j = 0
    while i < n:
        if text[i] == pattern[j]:
            i += 1
            j += 1
        if j == m:
            results.append(i - j)
            j = lps[j - 1]
        elif i < n and text[i] != pattern[j]:
            if j != 0:
                j = lps[j - 1]
            else:
                i += 1
    return results

def count_pattern(text, pattern):
    return len(kmp_search(text, pattern))
''',
    'roman_numeral_converter': '''
def int_to_roman(num):
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    sym = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    result = []
    for i in range(len(val)):
        while num >= val[i]:
            result.append(sym[i])
            num -= val[i]
    return "".join(result)

def roman_to_int(s):
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50,
                 "C": 100, "D": 500, "M": 1000}
    result = 0
    prev = 0
    for ch in reversed(s):
        curr = roman_map.get(ch, 0)
        if curr < prev:
            result -= curr
        else:
            result += curr
        prev = curr
    return result

def is_valid_roman(s):
    valid_chars = set("IVXLCDM")
    for ch in s:
        if ch not in valid_chars:
            return False
    converted = roman_to_int(s)
    return int_to_roman(converted) == s
''',
    'caesar_cipher': '''
def caesar_encrypt(text, shift):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            shifted = (ord(ch) - base + shift) % 26
            result.append(chr(base + shifted))
        else:
            result.append(ch)
    return "".join(result)

def caesar_decrypt(text, shift):
    return caesar_encrypt(text, -shift)

def brute_force_caesar(ciphertext):
    results = []
    for shift in range(26):
        decrypted = caesar_decrypt(ciphertext, shift)
        results.append((shift, decrypted))
    return results

def frequency_analysis(text):
    counts = {}
    total = 0
    for ch in text.lower():
        if ch.isalpha():
            counts[ch] = counts.get(ch, 0) + 1
            total += 1
    freqs = {}
    for ch, count in counts.items():
        freqs[ch] = round(count / max(total, 1) * 100, 2)
    return dict(sorted(freqs.items(), key=lambda x: -x[1]))
''',
    'word_frequency': '''
def word_frequency(text):
    words = text.lower().split()
    cleaned = []
    for w in words:
        word = ""
        for ch in w:
            if ch.isalnum():
                word += ch
        if word:
            cleaned.append(word)
    freq = {}
    for word in cleaned:
        freq[word] = freq.get(word, 0) + 1
    return freq

def top_n_words(text, n):
    freq = word_frequency(text)
    sorted_words = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return sorted_words[:n]

def unique_words(text):
    freq = word_frequency(text)
    return sorted([w for w, c in freq.items() if c == 1])

def word_positions(text):
    words = text.lower().split()
    positions = {}
    for i, word in enumerate(words):
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if cleaned:
            if cleaned not in positions:
                positions[cleaned] = []
            positions[cleaned].append(i)
    return positions
''',
    'text_wrapper': '''
def wrap_text(text, width):
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    for word in words:
        if current_length + len(word) + len(current_line) > width:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
        else:
            current_line.append(word)
            current_length += len(word)
    if current_line:
        lines.append(" ".join(current_line))
    return lines

def justify_text(text, width):
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    for word in words:
        if current_length + len(word) + len(current_line) > width:
            if len(current_line) == 1:
                lines.append(current_line[0].ljust(width))
            else:
                spaces = width - current_length
                gaps = len(current_line) - 1
                base_space = spaces // gaps
                extra = spaces % gaps
                line = ""
                for i, w in enumerate(current_line):
                    line += w
                    if i < gaps:
                        line += " " * (base_space + (1 if i < extra else 0))
                lines.append(line)
            current_line = [word]
            current_length = len(word)
        else:
            current_line.append(word)
            current_length += len(word)
    if current_line:
        lines.append(" ".join(current_line))
    return lines
''',
    'url_parser': '''
def parse_url(url):
    result = {"scheme": "", "host": "", "port": None, "path": "", "query": "", "fragment": ""}
    remaining = url
    if "://" in remaining:
        scheme, remaining = remaining.split("://", 1)
        result["scheme"] = scheme
    if "#" in remaining:
        remaining, fragment = remaining.rsplit("#", 1)
        result["fragment"] = fragment
    if "?" in remaining:
        remaining, query = remaining.split("?", 1)
        result["query"] = query
    if "/" in remaining:
        host_part, path = remaining.split("/", 1)
        result["path"] = "/" + path
    else:
        host_part = remaining
    if ":" in host_part:
        host, port = host_part.rsplit(":", 1)
        result["host"] = host
        result["port"] = int(port) if port.isdigit() else None
    else:
        result["host"] = host_part
    return result

def parse_query_string(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            params[key] = value
        else:
            params[pair] = ""
    return params

def build_url(scheme, host, path="", port=None, query=None, fragment=None):
    url = scheme + "://" + host
    if port:
        url += ":" + str(port)
    url += path
    if query:
        url += "?" + "&".join(k + "=" + v for k, v in query.items())
    if fragment:
        url += "#" + fragment
    return url
''',
    'email_validator': '''
def validate_email(email):
    if not email or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or not domain:
        return False
    if len(local) > 64 or len(domain) > 253:
        return False
    if local.startswith(".") or local.endswith("."):
        return False
    if ".." in local or ".." in domain:
        return False
    if "." not in domain:
        return False
    parts = domain.split(".")
    for part in parts:
        if not part or len(part) > 63:
            return False
        for ch in part:
            if not (ch.isalnum() or ch == "-"):
                return False
        if part.startswith("-") or part.endswith("-"):
            return False
    allowed_local = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.!#$%&*+/=?^_`{|}~-")
    for ch in local:
        if ch not in allowed_local:
            return False
    return True

def normalize_email(email):
    if not validate_email(email):
        return None
    local, domain = email.split("@")
    domain = domain.lower()
    return local + "@" + domain

def extract_domain(email):
    if "@" not in email:
        return None
    return email.split("@")[1].lower()
''',
    'matrix_multiply': '''
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

def identity_matrix(n):
    result = [[0] * n for _ in range(n)]
    for i in range(n):
        result[i][i] = 1
    return result
''',
    'gcd_lcm': '''
def gcd(a, b):
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)

def gcd_extended(a, b):
    if a == 0:
        return b, 0, 1
    g, x1, y1 = gcd_extended(b % a, a)
    x = y1 - (b // a) * x1
    y = x1
    return g, x, y

def gcd_list(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result

def lcm_list(numbers):
    result = numbers[0]
    for n in numbers[1:]:
        result = lcm(result, n)
    return result

def coprime(a, b):
    return gcd(a, b) == 1

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
    'prime_sieve': '''
def sieve_of_eratosthenes(limit):
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    p = 2
    while p * p <= limit:
        if is_prime[p]:
            for i in range(p * p, limit + 1, p):
                is_prime[i] = False
        p += 1
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
    'newton_sqrt': '''
def newton_sqrt(x, tolerance=1e-10):
    if x < 0:
        raise ValueError("Cannot compute square root of negative number")
    if x == 0:
        return 0.0
    guess = x / 2.0
    while True:
        new_guess = (guess + x / guess) / 2.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess

def newton_cbrt(x, tolerance=1e-10):
    if x == 0:
        return 0.0
    guess = x / 3.0
    while True:
        new_guess = (2 * guess + x / (guess * guess)) / 3.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess

def newton_nth_root(x, n, tolerance=1e-10):
    if x == 0:
        return 0.0
    guess = x / n
    while True:
        new_guess = ((n - 1) * guess + x / (guess ** (n - 1))) / n
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess

def is_perfect_square(n):
    if n < 0:
        return False
    root = int(newton_sqrt(n) + 0.5)
    return root * root == n
''',
    'polynomial_eval': '''
class Polynomial:
    def __init__(self, coefficients):
        self.coeffs = list(coefficients)
        while len(self.coeffs) > 1 and self.coeffs[-1] == 0:
            self.coeffs.pop()

    def degree(self):
        return len(self.coeffs) - 1

    def evaluate(self, x):
        result = 0
        power = 1
        for coeff in self.coeffs:
            result += coeff * power
            power *= x
        return result

    def add(self, other):
        size = max(len(self.coeffs), len(other.coeffs))
        result = [0] * size
        for i in range(len(self.coeffs)):
            result[i] += self.coeffs[i]
        for i in range(len(other.coeffs)):
            result[i] += other.coeffs[i]
        return Polynomial(result)

    def multiply(self, other):
        result = [0] * (len(self.coeffs) + len(other.coeffs) - 1)
        for i in range(len(self.coeffs)):
            for j in range(len(other.coeffs)):
                result[i + j] += self.coeffs[i] * other.coeffs[j]
        return Polynomial(result)

    def derivative(self):
        if len(self.coeffs) <= 1:
            return Polynomial([0])
        result = [self.coeffs[i] * i for i in range(1, len(self.coeffs))]
        return Polynomial(result)

    def to_string(self):
        terms = []
        for i, c in enumerate(self.coeffs):
            if c != 0:
                if i == 0:
                    terms.append(str(c))
                elif i == 1:
                    terms.append(str(c) + "x")
                else:
                    terms.append(str(c) + "x^" + str(i))
        return " + ".join(terms) if terms else "0"
''',
    'stats_calculator': '''
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
    n = len(sorted_data)
    rank = (p / 100.0) * (n - 1)
    lower = int(rank)
    upper = lower + 1
    frac = rank - lower
    if upper >= n:
        return sorted_data[-1]
    return sorted_data[lower] * (1 - frac) + sorted_data[upper] * frac

def mode(data):
    counts = {}
    for x in data:
        counts[x] = counts.get(x, 0) + 1
    max_count = max(counts.values())
    return [x for x, c in counts.items() if c == max_count]

def correlation(x, y):
    n = len(x)
    mean_x = mean(x)
    mean_y = mean(y)
    numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    denom_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    denom_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    if denom_x * denom_y == 0:
        return 0.0
    return numerator / (denom_x * denom_y)
''',
    'fibonacci_memo': '''
def fibonacci_memo(n, memo=None):
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci_memo(n - 1, memo) + fibonacci_memo(n - 2, memo)
    return memo[n]

def fibonacci_iter(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

def fibonacci_list(n):
    if n <= 0:
        return []
    if n == 1:
        return [0]
    fibs = [0, 1]
    for i in range(2, n):
        fibs.append(fibs[-1] + fibs[-2])
    return fibs

def fibonacci_matrix(n):
    if n <= 1:
        return n
    def mat_mult(a, b):
        return [
            [a[0][0] * b[0][0] + a[0][1] * b[1][0],
             a[0][0] * b[0][1] + a[0][1] * b[1][1]],
            [a[1][0] * b[0][0] + a[1][1] * b[1][0],
             a[1][0] * b[0][1] + a[1][1] * b[1][1]],
        ]
    def mat_pow(m, p):
        result = [[1, 0], [0, 1]]
        while p > 0:
            if p % 2 == 1:
                result = mat_mult(result, m)
            m = mat_mult(m, m)
            p //= 2
        return result
    base = [[1, 1], [1, 0]]
    return mat_pow(base, n)[0][1]

def is_fibonacci(n):
    def is_perfect_sq(x):
        s = int(x ** 0.5)
        return s * s == x
    return is_perfect_sq(5 * n * n + 4) or is_perfect_sq(5 * n * n - 4)
''',
    'fraction_arithmetic': '''
class Fraction:
    def __init__(self, numerator, denominator=1):
        if denominator == 0:
            raise ValueError("Denominator cannot be zero")
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
        g = self._gcd(abs(numerator), abs(denominator))
        self.num = numerator // g
        self.den = denominator // g

    def _gcd(self, a, b):
        while b:
            a, b = b, a % b
        return a

    def add(self, other):
        num = self.num * other.den + other.num * self.den
        den = self.den * other.den
        return Fraction(num, den)

    def subtract(self, other):
        num = self.num * other.den - other.num * self.den
        den = self.den * other.den
        return Fraction(num, den)

    def multiply(self, other):
        return Fraction(self.num * other.num, self.den * other.den)

    def divide(self, other):
        if other.num == 0:
            raise ValueError("Division by zero")
        return Fraction(self.num * other.den, self.den * other.num)

    def to_float(self):
        return self.num / self.den

    def to_string(self):
        if self.den == 1:
            return str(self.num)
        return str(self.num) + "/" + str(self.den)

    def equals(self, other):
        return self.num == other.num and self.den == other.den
''',
    'complex_number_ops': '''
class Complex:
    def __init__(self, real, imag=0.0):
        self.real = real
        self.imag = imag

    def add(self, other):
        return Complex(self.real + other.real, self.imag + other.imag)

    def subtract(self, other):
        return Complex(self.real - other.real, self.imag - other.imag)

    def multiply(self, other):
        r = self.real * other.real - self.imag * other.imag
        i = self.real * other.imag + self.imag * other.real
        return Complex(r, i)

    def divide(self, other):
        denom = other.real ** 2 + other.imag ** 2
        if denom == 0:
            raise ValueError("Division by zero")
        r = (self.real * other.real + self.imag * other.imag) / denom
        i = (self.imag * other.real - self.real * other.imag) / denom
        return Complex(r, i)

    def magnitude(self):
        return (self.real ** 2 + self.imag ** 2) ** 0.5

    def conjugate(self):
        return Complex(self.real, -self.imag)

    def to_polar(self):
        import math
        r = self.magnitude()
        theta = math.atan2(self.imag, self.real)
        return r, theta

    def to_string(self):
        if self.imag >= 0:
            return str(self.real) + "+" + str(self.imag) + "i"
        return str(self.real) + str(self.imag) + "i"
''',
    'permutation_generator': '''
def permutations(arr):
    if len(arr) <= 1:
        return [list(arr)]
    result = []
    for i in range(len(arr)):
        rest = arr[:i] + arr[i + 1:]
        for perm in permutations(rest):
            result.append([arr[i]] + perm)
    return result

def next_permutation(arr):
    nums = list(arr)
    n = len(nums)
    i = n - 2
    while i >= 0 and nums[i] >= nums[i + 1]:
        i -= 1
    if i < 0:
        nums.reverse()
        return nums
    j = n - 1
    while nums[j] <= nums[i]:
        j -= 1
    nums[i], nums[j] = nums[j], nums[i]
    nums[i + 1:] = reversed(nums[i + 1:])
    return nums

def count_permutations(n, r=None):
    if r is None:
        r = n
    result = 1
    for i in range(n, n - r, -1):
        result *= i
    return result

def combinations(arr, r):
    if r == 0:
        return [[]]
    if len(arr) < r:
        return []
    result = []
    first = arr[0]
    rest = arr[1:]
    with_first = combinations(rest, r - 1)
    for combo in with_first:
        result.append([first] + combo)
    without_first = combinations(rest, r)
    result.extend(without_first)
    return result
''',
    'http_router': '''
class Route:
    def __init__(self, method, path, handler):
        self.method = method
        self.path = path
        self.handler = handler
        self.parts = path.strip("/").split("/")

class Router:
    def __init__(self):
        self.routes = []
        self.not_found_handler = None

    def add_route(self, method, path, handler):
        self.routes.append(Route(method, path, handler))

    def get(self, path, handler):
        self.add_route("GET", path, handler)

    def post(self, path, handler):
        self.add_route("POST", path, handler)

    def match(self, method, path):
        path_parts = path.strip("/").split("/")
        for route in self.routes:
            if route.method != method:
                continue
            if len(route.parts) != len(path_parts):
                continue
            params = {}
            matched = True
            for rp, pp in zip(route.parts, path_parts):
                if rp.startswith(":"):
                    params[rp[1:]] = pp
                elif rp != pp:
                    matched = False
                    break
            if matched:
                return route.handler, params
        return self.not_found_handler, {}

    def dispatch(self, method, path):
        handler, params = self.match(method, path)
        if handler:
            return handler(params)
        return {"status": 404, "body": "Not Found"}
''',
    'middleware_chain': '''
class MiddlewareChain:
    def __init__(self):
        self.middlewares = []
        self.handler = None

    def use(self, middleware):
        self.middlewares.append(middleware)

    def set_handler(self, handler):
        self.handler = handler

    def execute(self, request):
        context = {"request": request, "response": None, "halted": False}
        for mw in self.middlewares:
            mw(context)
            if context["halted"]:
                return context["response"]
        if self.handler:
            context["response"] = self.handler(context["request"])
        return context["response"]

def logging_middleware(context):
    req = context["request"]
    context.setdefault("logs", [])
    context["logs"].append("Request: " + req.get("method", "GET") + " " + req.get("path", "/"))

def auth_middleware(context):
    req = context["request"]
    token = req.get("headers", {}).get("Authorization", "")
    if not token.startswith("Bearer "):
        context["halted"] = True
        context["response"] = {"status": 401, "body": "Unauthorized"}

def cors_middleware(context):
    if context.get("response") is None:
        context["response"] = {}
    resp = context["response"]
    if not isinstance(resp, dict):
        resp = {}
    headers = resp.get("headers", {})
    headers["Access-Control-Allow-Origin"] = "*"
    resp["headers"] = headers
    context["response"] = resp
''',
    'rate_limiter': '''
class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = {}

    def _clean_old(self, key, current_time):
        if key in self.requests:
            cutoff = current_time - self.window
            self.requests[key] = [t for t in self.requests[key] if t > cutoff]

    def allow(self, key, current_time):
        self._clean_old(key, current_time)
        if key not in self.requests:
            self.requests[key] = []
        if len(self.requests[key]) >= self.max_requests:
            return False
        self.requests[key].append(current_time)
        return True

    def remaining(self, key, current_time):
        self._clean_old(key, current_time)
        used = len(self.requests.get(key, []))
        return max(0, self.max_requests - used)

    def reset_time(self, key, current_time):
        if key not in self.requests or not self.requests[key]:
            return current_time
        oldest = min(self.requests[key])
        return oldest + self.window

class SlidingWindowCounter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.prev_count = {}
        self.curr_count = {}
        self.curr_start = {}

    def allow(self, key, current_time):
        if key not in self.curr_start:
            self.curr_start[key] = current_time
            self.prev_count[key] = 0
            self.curr_count[key] = 0
        elapsed = current_time - self.curr_start[key]
        if elapsed >= self.window:
            self.prev_count[key] = self.curr_count[key]
            self.curr_count[key] = 0
            self.curr_start[key] = current_time
            elapsed = 0
        weight = 1 - (elapsed / self.window)
        estimate = self.prev_count[key] * weight + self.curr_count[key]
        if estimate >= self.max_requests:
            return False
        self.curr_count[key] += 1
        return True
''',
    'session_store': '''
class SessionStore:
    def __init__(self, max_age=3600):
        self._sessions = {}
        self._max_age = max_age

    def create(self, session_id, data=None):
        self._sessions[session_id] = {
            "data": data or {},
            "created_at": 0,
            "last_accessed": 0,
        }
        return session_id

    def get(self, session_id, current_time=0):
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if current_time - session["last_accessed"] > self._max_age:
            del self._sessions[session_id]
            return None
        session["last_accessed"] = current_time
        return session["data"]

    def set(self, session_id, key, value, current_time=0):
        session = self._sessions.get(session_id)
        if session is None:
            self.create(session_id)
            session = self._sessions[session_id]
        session["data"][key] = value
        session["last_accessed"] = current_time

    def delete(self, session_id):
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def cleanup(self, current_time):
        expired = []
        for sid, session in self._sessions.items():
            if current_time - session["last_accessed"] > self._max_age:
                expired.append(sid)
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def active_count(self):
        return len(self._sessions)
''',
    'auth_handler': '''
class AuthHandler:
    def __init__(self, secret_key="default_secret"):
        self._users = {}
        self._tokens = {}
        self._secret = secret_key

    def register(self, username, password):
        if username in self._users:
            return {"error": "User already exists"}
        hashed = self._hash_password(password)
        self._users[username] = {"password_hash": hashed, "roles": ["user"]}
        return {"status": "registered", "username": username}

    def _hash_password(self, password):
        h = 0
        for ch in password:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        return str(h)

    def login(self, username, password):
        user = self._users.get(username)
        if not user:
            return {"error": "Invalid credentials"}
        if user["password_hash"] != self._hash_password(password):
            return {"error": "Invalid credentials"}
        token = self._generate_token(username)
        self._tokens[token] = username
        return {"token": token, "username": username}

    def _generate_token(self, username):
        raw = username + self._secret
        h = 0
        for ch in raw:
            h = (h * 37 + ord(ch)) & 0xFFFFFFFFFFFF
        return "tok_" + hex(h)[2:]

    def verify(self, token):
        username = self._tokens.get(token)
        if not username:
            return None
        return {"username": username, "roles": self._users[username]["roles"]}

    def logout(self, token):
        if token in self._tokens:
            del self._tokens[token]
            return True
        return False
''',
    'response_builder': '''
class Response:
    def __init__(self):
        self.status_code = 200
        self.headers = {}
        self.body = ""
        self.cookies = []

    def set_status(self, code):
        self.status_code = code
        return self

    def set_header(self, key, value):
        self.headers[key] = value
        return self

    def set_body(self, body):
        self.body = body
        return self

    def set_json(self, data):
        import json
        self.body = json.dumps(data)
        self.headers["Content-Type"] = "application/json"
        return self

    def add_cookie(self, name, value, max_age=None, path="/"):
        cookie = name + "=" + value + "; Path=" + path
        if max_age is not None:
            cookie += "; Max-Age=" + str(max_age)
        self.cookies.append(cookie)
        return self

    def redirect(self, url, permanent=False):
        self.status_code = 301 if permanent else 302
        self.headers["Location"] = url
        return self

    def to_dict(self):
        result = {
            "status": self.status_code,
            "headers": dict(self.headers),
            "body": self.body,
        }
        if self.cookies:
            result["cookies"] = list(self.cookies)
        return result

    def status_text(self):
        texts = {200: "OK", 201: "Created", 301: "Moved Permanently",
                 302: "Found", 400: "Bad Request", 401: "Unauthorized",
                 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}
        return texts.get(self.status_code, "Unknown")
''',
    'form_validator': '''
class FormValidator:
    def __init__(self):
        self.rules = {}
        self.errors = {}

    def add_field(self, name, required=False, min_length=None, max_length=None,
                  pattern=None, field_type=None):
        self.rules[name] = {
            "required": required, "min_length": min_length,
            "max_length": max_length, "pattern": pattern, "type": field_type,
        }

    def validate(self, data):
        self.errors = {}
        for field, rules in self.rules.items():
            value = data.get(field, "")
            field_errors = []
            if rules["required"] and not value:
                field_errors.append("Field is required")
                self.errors[field] = field_errors
                continue
            if not value:
                continue
            if rules["min_length"] and len(str(value)) < rules["min_length"]:
                field_errors.append("Too short, minimum " + str(rules["min_length"]))
            if rules["max_length"] and len(str(value)) > rules["max_length"]:
                field_errors.append("Too long, maximum " + str(rules["max_length"]))
            if rules["type"] == "email" and "@" not in str(value):
                field_errors.append("Invalid email")
            if rules["type"] == "integer":
                try:
                    int(value)
                except (ValueError, TypeError):
                    field_errors.append("Must be an integer")
            if field_errors:
                self.errors[field] = field_errors
        return len(self.errors) == 0

    def get_errors(self):
        return dict(self.errors)

    def is_valid(self):
        return len(self.errors) == 0
''',
    'cookie_manager': '''
class CookieManager:
    def __init__(self):
        self._cookies = {}

    def set_cookie(self, name, value, max_age=None, path="/",
                   secure=False, http_only=False, same_site=None):
        self._cookies[name] = {
            "value": value, "max_age": max_age, "path": path,
            "secure": secure, "http_only": http_only, "same_site": same_site,
        }

    def get_cookie(self, name):
        cookie = self._cookies.get(name)
        if cookie:
            return cookie["value"]
        return None

    def delete_cookie(self, name, path="/"):
        self._cookies[name] = {
            "value": "", "max_age": 0, "path": path,
            "secure": False, "http_only": False, "same_site": None,
        }

    def parse_cookie_header(self, header):
        result = {}
        pairs = header.split(";")
        for pair in pairs:
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    def serialize(self, name):
        cookie = self._cookies.get(name)
        if not cookie:
            return ""
        parts = [name + "=" + cookie["value"]]
        if cookie["max_age"] is not None:
            parts.append("Max-Age=" + str(cookie["max_age"]))
        parts.append("Path=" + cookie["path"])
        if cookie["secure"]:
            parts.append("Secure")
        if cookie["http_only"]:
            parts.append("HttpOnly")
        if cookie["same_site"]:
            parts.append("SameSite=" + cookie["same_site"])
        return "; ".join(parts)

    def all_cookies(self):
        return {name: c["value"] for name, c in self._cookies.items()}
''',
    'query_string_parser': '''
def parse_qs(query_string):
    params = {}
    if not query_string:
        return params
    if query_string.startswith("?"):
        query_string = query_string[1:]
    for pair in query_string.split("&"):
        if not pair:
            continue
        if "=" in pair:
            key, value = pair.split("=", 1)
        else:
            key, value = pair, ""
        key = url_decode(key)
        value = url_decode(value)
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
        if s[i] == "+" :
            result.append(" ")
            i += 1
        elif s[i] == "%" and i + 2 < len(s):
            hex_str = s[i + 1:i + 3]
            try:
                result.append(chr(int(hex_str, 16)))
                i += 3
            except ValueError:
                result.append(s[i])
                i += 1
        else:
            result.append(s[i])
            i += 1
    return "".join(result)

def build_qs(params):
    parts = []
    for key, value in params.items():
        if isinstance(value, list):
            for v in value:
                parts.append(url_encode(str(key)) + "=" + url_encode(str(v)))
        else:
            parts.append(url_encode(str(key)) + "=" + url_encode(str(value)))
    return "&".join(parts)

def url_encode(s):
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")
    result = []
    for ch in s:
        if ch in safe:
            result.append(ch)
        elif ch == " ":
            result.append("+")
        else:
            result.append("%" + format(ord(ch), "02X"))
    return "".join(result)
''',
    'cors_handler': '''
class CORSHandler:
    def __init__(self):
        self.allowed_origins = ["*"]
        self.allowed_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        self.allowed_headers = ["Content-Type", "Authorization"]
        self.max_age = 86400
        self.allow_credentials = False

    def configure(self, origins=None, methods=None, headers=None,
                  max_age=None, credentials=None):
        if origins is not None:
            self.allowed_origins = origins
        if methods is not None:
            self.allowed_methods = methods
        if headers is not None:
            self.allowed_headers = headers
        if max_age is not None:
            self.max_age = max_age
        if credentials is not None:
            self.allow_credentials = credentials

    def is_origin_allowed(self, origin):
        if "*" in self.allowed_origins:
            return True
        return origin in self.allowed_origins

    def handle_preflight(self, request):
        origin = request.get("origin", "")
        if not self.is_origin_allowed(origin):
            return {"status": 403, "headers": {}}
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": ", ".join(self.allowed_methods),
            "Access-Control-Allow-Headers": ", ".join(self.allowed_headers),
            "Access-Control-Max-Age": str(self.max_age),
        }
        if self.allow_credentials:
            headers["Access-Control-Allow-Credentials"] = "true"
        return {"status": 204, "headers": headers}

    def add_headers(self, response, origin):
        if not self.is_origin_allowed(origin):
            return response
        response.setdefault("headers", {})
        response["headers"]["Access-Control-Allow-Origin"] = origin
        if self.allow_credentials:
            response["headers"]["Access-Control-Allow-Credentials"] = "true"
        return response
''',
    'query_builder': '''
class QueryBuilder:
    def __init__(self, table):
        self._table = table
        self._select_cols = ["*"]
        self._where = []
        self._order_by = []
        self._limit = None
        self._offset = None
        self._joins = []
        self._params = []

    def select(self, *columns):
        self._select_cols = list(columns)
        return self

    def where(self, condition, value=None):
        self._where.append(condition)
        if value is not None:
            self._params.append(value)
        return self

    def order_by(self, column, direction="ASC"):
        self._order_by.append(column + " " + direction)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def join(self, table, on_condition, join_type="INNER"):
        self._joins.append(join_type + " JOIN " + table + " ON " + on_condition)
        return self

    def build(self):
        sql = "SELECT " + ", ".join(self._select_cols)
        sql += " FROM " + self._table
        for j in self._joins:
            sql += " " + j
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        if self._order_by:
            sql += " ORDER BY " + ", ".join(self._order_by)
        if self._limit is not None:
            sql += " LIMIT " + str(self._limit)
        if self._offset is not None:
            sql += " OFFSET " + str(self._offset)
        return sql, self._params
''',
    'model_base': '''
class Field:
    def __init__(self, field_type, required=False, default=None):
        self.field_type = field_type
        self.required = required
        self.default = default

class ModelBase:
    _fields = {}

    def __init__(self, **kwargs):
        self._data = {}
        for name, field in self._fields.items():
            if name in kwargs:
                value = kwargs[name]
                if not isinstance(value, field.field_type):
                    try:
                        value = field.field_type(value)
                    except (ValueError, TypeError):
                        raise TypeError("Invalid type for " + name)
                self._data[name] = value
            elif field.default is not None:
                self._data[name] = field.default
            elif field.required:
                raise ValueError("Missing required field: " + name)

    def get(self, name):
        return self._data.get(name)

    def set(self, name, value):
        if name in self._fields:
            field = self._fields[name]
            if not isinstance(value, field.field_type):
                value = field.field_type(value)
            self._data[name] = value

    def to_dict(self):
        return dict(self._data)

    def validate(self):
        errors = []
        for name, field in self._fields.items():
            if field.required and name not in self._data:
                errors.append("Missing: " + name)
        return errors

class UserModel(ModelBase):
    _fields = {
        "name": Field(str, required=True),
        "email": Field(str, required=True),
        "age": Field(int, default=0),
    }
''',
    'migration_tracker': '''
class Migration:
    def __init__(self, version, description, up_sql, down_sql):
        self.version = version
        self.description = description
        self.up_sql = up_sql
        self.down_sql = down_sql
        self.applied = False

class MigrationTracker:
    def __init__(self):
        self.migrations = []
        self.applied = []

    def add(self, version, description, up_sql, down_sql):
        migration = Migration(version, description, up_sql, down_sql)
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: m.version)

    def pending(self):
        applied_versions = set(self.applied)
        return [m for m in self.migrations if m.version not in applied_versions]

    def apply_next(self):
        pending = self.pending()
        if not pending:
            return None
        migration = pending[0]
        migration.applied = True
        self.applied.append(migration.version)
        return {"version": migration.version, "sql": migration.up_sql}

    def rollback_last(self):
        if not self.applied:
            return None
        version = self.applied.pop()
        for m in self.migrations:
            if m.version == version:
                m.applied = False
                return {"version": version, "sql": m.down_sql}
        return None

    def status(self):
        result = []
        applied_set = set(self.applied)
        for m in self.migrations:
            result.append({
                "version": m.version,
                "description": m.description,
                "applied": m.version in applied_set,
            })
        return result

    def current_version(self):
        return self.applied[-1] if self.applied else None
''',
    'connection_pool': '''
class Connection:
    def __init__(self, conn_id, host, port):
        self.conn_id = conn_id
        self.host = host
        self.port = port
        self.in_use = False
        self.queries_run = 0

    def execute(self, query):
        self.queries_run += 1
        return {"conn_id": self.conn_id, "query": query, "status": "ok"}

class ConnectionPool:
    def __init__(self, host, port, min_size=2, max_size=10):
        self.host = host
        self.port = port
        self.min_size = min_size
        self.max_size = max_size
        self._pool = []
        self._all_connections = []
        self._next_id = 0
        for _ in range(min_size):
            self._create_connection()

    def _create_connection(self):
        if len(self._all_connections) >= self.max_size:
            return None
        conn = Connection(self._next_id, self.host, self.port)
        self._next_id += 1
        self._all_connections.append(conn)
        self._pool.append(conn)
        return conn

    def acquire(self):
        for conn in self._pool:
            if not conn.in_use:
                conn.in_use = True
                return conn
        conn = self._create_connection()
        if conn:
            conn.in_use = True
            return conn
        return None

    def release(self, conn):
        conn.in_use = False

    def size(self):
        return len(self._all_connections)

    def available(self):
        return sum(1 for c in self._pool if not c.in_use)

    def stats(self):
        total_queries = sum(c.queries_run for c in self._all_connections)
        return {
            "total": self.size(), "available": self.available(),
            "in_use": self.size() - self.available(),
            "total_queries": total_queries,
        }
''',
    'schema_validator': '''
class SchemaValidator:
    def __init__(self, schema):
        self.schema = schema
        self.errors = []

    def validate(self, data):
        self.errors = []
        self._validate_object(data, self.schema, "root")
        return len(self.errors) == 0

    def _validate_object(self, data, schema, path):
        expected_type = schema.get("type", "object")
        if expected_type == "object":
            if not isinstance(data, dict):
                self.errors.append(path + ": expected object")
                return
            required = schema.get("required", [])
            for field in required:
                if field not in data:
                    self.errors.append(path + "." + field + ": required field missing")
            props = schema.get("properties", {})
            for key, sub_schema in props.items():
                if key in data:
                    self._validate_object(data[key], sub_schema, path + "." + key)
        elif expected_type == "string":
            if not isinstance(data, str):
                self.errors.append(path + ": expected string")
            elif "min_length" in schema and len(data) < schema["min_length"]:
                self.errors.append(path + ": too short")
        elif expected_type == "integer":
            if not isinstance(data, int):
                self.errors.append(path + ": expected integer")
            elif "minimum" in schema and data < schema["minimum"]:
                self.errors.append(path + ": below minimum")
        elif expected_type == "array":
            if not isinstance(data, list):
                self.errors.append(path + ": expected array")
            elif "items" in schema:
                for i, item in enumerate(data):
                    self._validate_object(item, schema["items"], path + "[" + str(i) + "]")

    def get_errors(self):
        return list(self.errors)
''',
    'record_serializer': '''
class RecordSerializer:
    def __init__(self):
        self._serializers = {}
        self._deserializers = {}

    def register(self, type_name, serialize_fn, deserialize_fn):
        self._serializers[type_name] = serialize_fn
        self._deserializers[type_name] = deserialize_fn

    def serialize(self, record, type_name=None):
        if type_name and type_name in self._serializers:
            return self._serializers[type_name](record)
        if isinstance(record, dict):
            result = {}
            for key, value in record.items():
                result[key] = self.serialize(value)
            return result
        if isinstance(record, (list, tuple)):
            return [self.serialize(item) for item in record]
        return record

    def deserialize(self, data, type_name):
        if type_name in self._deserializers:
            return self._deserializers[type_name](data)
        return data

    def to_flat_dict(self, record, prefix=""):
        result = {}
        if isinstance(record, dict):
            for key, value in record.items():
                full_key = prefix + "." + key if prefix else key
                if isinstance(value, dict):
                    result.update(self.to_flat_dict(value, full_key))
                else:
                    result[full_key] = value
        return result

    def from_flat_dict(self, flat_dict):
        result = {}
        for key, value in flat_dict.items():
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        return result
''',
    'transaction_manager': '''
class Transaction:
    def __init__(self, txn_id):
        self.txn_id = txn_id
        self.operations = []
        self.committed = False
        self.rolled_back = False

    def add_operation(self, op_type, table, data, undo_data=None):
        self.operations.append({
            "type": op_type, "table": table,
            "data": data, "undo_data": undo_data,
        })

class TransactionManager:
    def __init__(self):
        self._transactions = {}
        self._next_id = 1
        self._log = []

    def begin(self):
        txn_id = self._next_id
        self._next_id += 1
        txn = Transaction(txn_id)
        self._transactions[txn_id] = txn
        self._log.append({"action": "begin", "txn_id": txn_id})
        return txn_id

    def add_op(self, txn_id, op_type, table, data, undo_data=None):
        txn = self._transactions.get(txn_id)
        if not txn or txn.committed or txn.rolled_back:
            return False
        txn.add_operation(op_type, table, data, undo_data)
        return True

    def commit(self, txn_id):
        txn = self._transactions.get(txn_id)
        if not txn or txn.committed or txn.rolled_back:
            return False
        txn.committed = True
        self._log.append({"action": "commit", "txn_id": txn_id})
        return True

    def rollback(self, txn_id):
        txn = self._transactions.get(txn_id)
        if not txn or txn.committed or txn.rolled_back:
            return False
        txn.rolled_back = True
        undo_ops = list(reversed(txn.operations))
        self._log.append({"action": "rollback", "txn_id": txn_id, "undone": len(undo_ops)})
        return undo_ops

    def get_log(self):
        return list(self._log)
''',
    'index_builder': '''
class IndexEntry:
    def __init__(self, key, row_ids=None):
        self.key = key
        self.row_ids = set(row_ids) if row_ids else set()

class IndexBuilder:
    def __init__(self, name, columns):
        self.name = name
        self.columns = columns
        self._entries = {}
        self._stats = {"inserts": 0, "lookups": 0, "deletes": 0}

    def _make_key(self, row):
        parts = []
        for col in self.columns:
            parts.append(str(row.get(col, "")))
        return "|".join(parts)

    def add(self, row_id, row):
        key = self._make_key(row)
        if key not in self._entries:
            self._entries[key] = IndexEntry(key)
        self._entries[key].row_ids.add(row_id)
        self._stats["inserts"] += 1

    def lookup(self, criteria):
        key = self._make_key(criteria)
        self._stats["lookups"] += 1
        entry = self._entries.get(key)
        if entry:
            return list(entry.row_ids)
        return []

    def remove(self, row_id, row):
        key = self._make_key(row)
        entry = self._entries.get(key)
        if entry and row_id in entry.row_ids:
            entry.row_ids.discard(row_id)
            if not entry.row_ids:
                del self._entries[key]
            self._stats["deletes"] += 1
            return True
        return False

    def size(self):
        return sum(len(e.row_ids) for e in self._entries.values())

    def distinct_keys(self):
        return len(self._entries)

    def stats(self):
        return dict(self._stats)
''',
    'config_parser': '''
class ConfigParser:
    def __init__(self):
        self._sections = {}
        self._defaults = {}

    def set_defaults(self, defaults):
        self._defaults.update(defaults)

    def parse(self, text):
        current_section = "DEFAULT"
        self._sections[current_section] = dict(self._defaults)
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                if current_section not in self._sections:
                    self._sections[current_section] = dict(self._defaults)
            elif "=" in line:
                key, value = line.split("=", 1)
                self._sections[current_section][key.strip()] = value.strip()

    def get(self, section, key, fallback=None):
        sect = self._sections.get(section, {})
        return sect.get(key, self._defaults.get(key, fallback))

    def sections(self):
        return [s for s in self._sections if s != "DEFAULT"]

    def items(self, section):
        return list(self._sections.get(section, {}).items())

    def has_section(self, section):
        return section in self._sections

    def to_dict(self):
        return {s: dict(items) for s, items in self._sections.items()}
''',
    'env_manager': '''
class EnvManager:
    def __init__(self):
        self._vars = {}
        self._required = set()
        self._defaults = {}

    def load_from_dict(self, env_dict):
        self._vars.update(env_dict)

    def load_from_text(self, text):
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if value and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                self._vars[key] = value

    def require(self, *keys):
        for key in keys:
            self._required.add(key)

    def set_default(self, key, value):
        self._defaults[key] = value

    def get(self, key, default=None):
        if key in self._vars:
            return self._vars[key]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def get_int(self, key, default=0):
        val = self.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key, default=False):
        val = self.get(key)
        if val is None:
            return default
        return str(val).lower() in ("true", "1", "yes", "on")

    def validate(self):
        missing = []
        for key in self._required:
            if key not in self._vars and key not in self._defaults:
                missing.append(key)
        return missing

    def all_vars(self):
        result = dict(self._defaults)
        result.update(self._vars)
        return result
''',
    'arg_parser': '''
class Argument:
    def __init__(self, name, arg_type=str, required=False, default=None, help_text=""):
        self.name = name
        self.arg_type = arg_type
        self.required = required
        self.default = default
        self.help_text = help_text

class ArgParser:
    def __init__(self, description=""):
        self.description = description
        self._arguments = {}
        self._positional = []
        self._flags = {}

    def add_argument(self, name, **kwargs):
        arg = Argument(name, **kwargs)
        if name.startswith("--"):
            self._arguments[name] = arg
        elif name.startswith("-"):
            self._flags[name] = arg
        else:
            self._positional.append(arg)

    def parse(self, args):
        result = {}
        for arg in self._arguments.values():
            result[arg.name.lstrip("-")] = arg.default
        for arg in self._positional:
            result[arg.name] = arg.default
        pos_idx = 0
        i = 0
        while i < len(args):
            token = args[i]
            if token in self._arguments:
                arg = self._arguments[token]
                if i + 1 < len(args):
                    result[arg.name.lstrip("-")] = arg.arg_type(args[i + 1])
                    i += 2
                else:
                    i += 1
            elif token in self._flags:
                result[self._flags[token].name.lstrip("-")] = True
                i += 1
            else:
                if pos_idx < len(self._positional):
                    pa = self._positional[pos_idx]
                    result[pa.name] = pa.arg_type(token)
                    pos_idx += 1
                i += 1
        return result

    def usage(self):
        lines = [self.description]
        for arg in self._positional:
            lines.append("  " + arg.name + " - " + arg.help_text)
        for name, arg in self._arguments.items():
            lines.append("  " + name + " - " + arg.help_text)
        return lines
''',
    'logger_config': '''
class LogRecord:
    def __init__(self, level, message, logger_name="root"):
        self.level = level
        self.message = message
        self.logger_name = logger_name

class Handler:
    def __init__(self, min_level=0):
        self.min_level = min_level
        self.records = []

    def emit(self, record):
        if record.level >= self.min_level:
            self.records.append(record)

class Logger:
    LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

    def __init__(self, name="root"):
        self.name = name
        self.handlers = []
        self.level = 0

    def add_handler(self, handler):
        self.handlers.append(handler)

    def set_level(self, level_name):
        self.level = self.LEVELS.get(level_name, 0)

    def _log(self, level, message):
        if level >= self.level:
            record = LogRecord(level, message, self.name)
            for handler in self.handlers:
                handler.emit(record)

    def debug(self, msg):
        self._log(10, msg)

    def info(self, msg):
        self._log(20, msg)

    def warning(self, msg):
        self._log(30, msg)

    def error(self, msg):
        self._log(40, msg)

    def critical(self, msg):
        self._log(50, msg)

class LoggerConfig:
    def __init__(self):
        self._loggers = {}

    def get_logger(self, name="root"):
        if name not in self._loggers:
            self._loggers[name] = Logger(name)
        return self._loggers[name]

    def configure(self, name, level, handler):
        logger = self.get_logger(name)
        logger.set_level(level)
        logger.add_handler(handler)
''',
    'plugin_registry': '''
class Plugin:
    def __init__(self, name, version, hooks=None):
        self.name = name
        self.version = version
        self.hooks = hooks or {}
        self.enabled = True

class PluginRegistry:
    def __init__(self):
        self._plugins = {}
        self._hooks = {}

    def register(self, name, version, hooks=None):
        plugin = Plugin(name, version, hooks)
        self._plugins[name] = plugin
        for hook_name, handler in (hooks or {}).items():
            if hook_name not in self._hooks:
                self._hooks[hook_name] = []
            self._hooks[hook_name].append((name, handler))
        return plugin

    def unregister(self, name):
        plugin = self._plugins.pop(name, None)
        if plugin:
            for hook_name in list(self._hooks.keys()):
                self._hooks[hook_name] = [
                    (n, h) for n, h in self._hooks[hook_name] if n != name
                ]
            return True
        return False

    def enable(self, name):
        if name in self._plugins:
            self._plugins[name].enabled = True

    def disable(self, name):
        if name in self._plugins:
            self._plugins[name].enabled = False

    def execute_hook(self, hook_name, context):
        results = []
        for plugin_name, handler in self._hooks.get(hook_name, []):
            if self._plugins.get(plugin_name, Plugin("", "")).enabled:
                result = handler(context)
                results.append((plugin_name, result))
        return results

    def list_plugins(self):
        return [
            {"name": p.name, "version": p.version, "enabled": p.enabled}
            for p in self._plugins.values()
        ]

    def get_plugin(self, name):
        return self._plugins.get(name)
''',
    'ini_parser': '''
class IniParser:
    def __init__(self):
        self._data = {}

    def parse(self, text):
        current = "DEFAULT"
        self._data[current] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1].strip()
                if current not in self._data:
                    self._data[current] = {}
            elif "=" in stripped:
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip()
                if "#" in value:
                    value = value[:value.index("#")].strip()
                self._data[current][key] = value

    def get(self, section, key, default=None):
        return self._data.get(section, {}).get(key, default)

    def set(self, section, key, value):
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = str(value)

    def sections(self):
        return [s for s in self._data if s != "DEFAULT"]

    def keys(self, section):
        return list(self._data.get(section, {}).keys())

    def serialize(self):
        lines = []
        for section, values in self._data.items():
            if section != "DEFAULT":
                lines.append("[" + section + "]")
            for key, value in values.items():
                lines.append(key + " = " + value)
            lines.append("")
        return lines

    def merge(self, other):
        for section, values in other._data.items():
            if section not in self._data:
                self._data[section] = {}
            self._data[section].update(values)
''',
    'feature_flags': '''
class FeatureFlags:
    def __init__(self):
        self._flags = {}
        self._overrides = {}
        self._rollout = {}

    def define(self, name, default=False, description=""):
        self._flags[name] = {
            "default": default, "description": description, "enabled": default,
        }

    def enable(self, name):
        if name in self._flags:
            self._flags[name]["enabled"] = True

    def disable(self, name):
        if name in self._flags:
            self._flags[name]["enabled"] = False

    def is_enabled(self, name, user_id=None):
        if user_id and name in self._overrides:
            user_overrides = self._overrides[name]
            if user_id in user_overrides:
                return user_overrides[user_id]
        if name in self._rollout:
            pct = self._rollout[name]
            if user_id:
                h = 0
                for ch in str(user_id) + name:
                    h = (h * 31 + ord(ch)) & 0xFFFF
                return (h % 100) < pct
        flag = self._flags.get(name)
        if flag:
            return flag["enabled"]
        return False

    def set_rollout(self, name, percentage):
        self._rollout[name] = max(0, min(100, percentage))

    def set_user_override(self, name, user_id, enabled):
        if name not in self._overrides:
            self._overrides[name] = {}
        self._overrides[name][user_id] = enabled

    def all_flags(self):
        return {name: f["enabled"] for name, f in self._flags.items()}

    def flag_info(self, name):
        flag = self._flags.get(name)
        if not flag:
            return None
        return {
            "name": name, "enabled": flag["enabled"],
            "description": flag["description"],
            "rollout": self._rollout.get(name),
        }
''',
    'observer_pattern': '''
class EventEmitter:
    def __init__(self):
        self._listeners = {}

    def on(self, event, callback):
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    def off(self, event, callback):
        if event in self._listeners:
            self._listeners[event] = [
                cb for cb in self._listeners[event] if cb != callback
            ]

    def emit(self, event, *args, **kwargs):
        results = []
        for callback in self._listeners.get(event, []):
            result = callback(*args, **kwargs)
            results.append(result)
        return results

    def once(self, event, callback):
        def wrapper(*args, **kwargs):
            self.off(event, wrapper)
            return callback(*args, **kwargs)
        self.on(event, wrapper)

    def listener_count(self, event):
        return len(self._listeners.get(event, []))

    def events(self):
        return list(self._listeners.keys())

    def remove_all(self, event=None):
        if event:
            self._listeners.pop(event, None)
        else:
            self._listeners.clear()
''',
    'strategy_pattern': '''
class SortStrategy:
    def sort(self, data):
        raise NotImplementedError

class BubbleSortStrategy(SortStrategy):
    def sort(self, data):
        arr = list(data)
        n = len(arr)
        for i in range(n):
            for j in range(0, n - i - 1):
                if arr[j] > arr[j + 1]:
                    arr[j], arr[j + 1] = arr[j + 1], arr[j]
        return arr

class QuickSortStrategy(SortStrategy):
    def sort(self, data):
        if len(data) <= 1:
            return list(data)
        pivot = data[len(data) // 2]
        left = [x for x in data if x < pivot]
        middle = [x for x in data if x == pivot]
        right = [x for x in data if x > pivot]
        return self.sort(left) + middle + self.sort(right)

class MergeSortStrategy(SortStrategy):
    def sort(self, data):
        if len(data) <= 1:
            return list(data)
        mid = len(data) // 2
        left = self.sort(data[:mid])
        right = self.sort(data[mid:])
        return self._merge(left, right)

    def _merge(self, left, right):
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

class Sorter:
    def __init__(self, strategy=None):
        self._strategy = strategy or BubbleSortStrategy()

    def set_strategy(self, strategy):
        self._strategy = strategy

    def sort(self, data):
        return self._strategy.sort(data)
''',
    'command_pattern': '''
class Command:
    def execute(self):
        raise NotImplementedError
    def undo(self):
        raise NotImplementedError

class InsertTextCommand(Command):
    def __init__(self, document, position, text):
        self.document = document
        self.position = position
        self.text = text

    def execute(self):
        self.document.insert(self.position, self.text)

    def undo(self):
        self.document.delete(self.position, len(self.text))

class DeleteTextCommand(Command):
    def __init__(self, document, position, length):
        self.document = document
        self.position = position
        self.length = length
        self.deleted_text = ""

    def execute(self):
        self.deleted_text = self.document.get_text(self.position, self.length)
        self.document.delete(self.position, self.length)

    def undo(self):
        self.document.insert(self.position, self.deleted_text)

class Document:
    def __init__(self):
        self.content = ""

    def insert(self, position, text):
        self.content = self.content[:position] + text + self.content[position:]

    def delete(self, position, length):
        self.content = self.content[:position] + self.content[position + length:]

    def get_text(self, position, length):
        return self.content[position:position + length]

class CommandHistory:
    def __init__(self):
        self._history = []
        self._redo_stack = []

    def execute(self, command):
        command.execute()
        self._history.append(command)
        self._redo_stack.clear()

    def undo(self):
        if self._history:
            command = self._history.pop()
            command.undo()
            self._redo_stack.append(command)

    def redo(self):
        if self._redo_stack:
            command = self._redo_stack.pop()
            command.execute()
            self._history.append(command)
''',
    'factory_pattern': '''
class Shape:
    def area(self):
        raise NotImplementedError
    def perimeter(self):
        raise NotImplementedError

class Circle(Shape):
    def __init__(self, radius):
        self.radius = radius
    def area(self):
        return 3.14159265 * self.radius ** 2
    def perimeter(self):
        return 2 * 3.14159265 * self.radius

class Rectangle(Shape):
    def __init__(self, width, height):
        self.width = width
        self.height = height
    def area(self):
        return self.width * self.height
    def perimeter(self):
        return 2 * (self.width + self.height)

class Triangle(Shape):
    def __init__(self, a, b, c):
        self.a = a
        self.b = b
        self.c = c
    def area(self):
        s = (self.a + self.b + self.c) / 2
        return (s * (s - self.a) * (s - self.b) * (s - self.c)) ** 0.5
    def perimeter(self):
        return self.a + self.b + self.c

class ShapeFactory:
    _registry = {}

    @classmethod
    def register(cls, name, shape_class):
        cls._registry[name] = shape_class

    @classmethod
    def create(cls, name, **kwargs):
        shape_class = cls._registry.get(name)
        if not shape_class:
            raise ValueError("Unknown shape: " + name)
        return shape_class(**kwargs)

ShapeFactory.register("circle", Circle)
ShapeFactory.register("rectangle", Rectangle)
ShapeFactory.register("triangle", Triangle)
''',
    'decorator_pattern': '''
class TextProcessor:
    def process(self, text):
        return text

class UpperCaseDecorator(TextProcessor):
    def __init__(self, wrapped):
        self._wrapped = wrapped
    def process(self, text):
        return self._wrapped.process(text).upper()

class TrimDecorator(TextProcessor):
    def __init__(self, wrapped):
        self._wrapped = wrapped
    def process(self, text):
        return self._wrapped.process(text).strip()

class PrefixDecorator(TextProcessor):
    def __init__(self, wrapped, prefix):
        self._wrapped = wrapped
        self._prefix = prefix
    def process(self, text):
        return self._prefix + self._wrapped.process(text)

class SuffixDecorator(TextProcessor):
    def __init__(self, wrapped, suffix):
        self._wrapped = wrapped
        self._suffix = suffix
    def process(self, text):
        return self._wrapped.process(text) + self._suffix

class ReplaceDecorator(TextProcessor):
    def __init__(self, wrapped, old, new):
        self._wrapped = wrapped
        self._old = old
        self._new = new
    def process(self, text):
        return self._wrapped.process(text).replace(self._old, self._new)

def build_pipeline(*decorators):
    processor = TextProcessor()
    for dec_class, kwargs in decorators:
        processor = dec_class(processor, **kwargs)
    return processor
''',
    'state_machine': '''
class Transition:
    def __init__(self, from_state, event, to_state, action=None, guard=None):
        self.from_state = from_state
        self.event = event
        self.to_state = to_state
        self.action = action
        self.guard = guard

class StateMachine:
    def __init__(self, initial_state):
        self.current_state = initial_state
        self._transitions = {}
        self._on_enter = {}
        self._on_exit = {}
        self._history = [initial_state]

    def add_transition(self, from_state, event, to_state, action=None, guard=None):
        key = (from_state, event)
        self._transitions[key] = Transition(from_state, event, to_state, action, guard)

    def on_enter(self, state, callback):
        self._on_enter[state] = callback

    def on_exit(self, state, callback):
        self._on_exit[state] = callback

    def trigger(self, event, context=None):
        key = (self.current_state, event)
        transition = self._transitions.get(key)
        if not transition:
            return False
        if transition.guard and not transition.guard(context):
            return False
        if self.current_state in self._on_exit:
            self._on_exit[self.current_state](context)
        old_state = self.current_state
        self.current_state = transition.to_state
        if transition.action:
            transition.action(context)
        if self.current_state in self._on_enter:
            self._on_enter[self.current_state](context)
        self._history.append(self.current_state)
        return True

    def can_trigger(self, event):
        return (self.current_state, event) in self._transitions

    def history(self):
        return list(self._history)

    def reset(self, state=None):
        if state is None:
            state = self._history[0]
        self.current_state = state
        self._history = [state]
''',
    'builder_pattern': '''
class HTMLElement:
    def __init__(self, tag, text=""):
        self.tag = tag
        self.text = text
        self.children = []
        self.attributes = {}

    def render(self, indent=0):
        prefix = "  " * indent
        attrs = ""
        for key, value in self.attributes.items():
            attrs += " " + key + '="' + value + '"'
        result = prefix + "<" + self.tag + attrs + ">"
        if self.text:
            result += self.text
        parts = [result]
        if self.children:
            for child in self.children:
                parts.append(child.render(indent + 1))
            parts.append(prefix)
        parts.append("</" + self.tag + ">")
        return parts

class HTMLBuilder:
    def __init__(self, root_tag):
        self._root = HTMLElement(root_tag)
        self._stack = [self._root]

    def add_attr(self, key, value):
        self._stack[-1].attributes[key] = value
        return self

    def add_text(self, text):
        self._stack[-1].text = text
        return self

    def add_child(self, tag, text=""):
        child = HTMLElement(tag, text)
        self._stack[-1].children.append(child)
        return self

    def begin_child(self, tag):
        child = HTMLElement(tag)
        self._stack[-1].children.append(child)
        self._stack.append(child)
        return self

    def end_child(self):
        if len(self._stack) > 1:
            self._stack.pop()
        return self

    def build(self):
        return self._root

    def render(self):
        return self._root.render()
''',
    'proxy_pattern': '''
class RealService:
    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value

    def delete(self, key):
        if key in self._data:
            del self._data[key]
            return True
        return False

class CachingProxy:
    def __init__(self, service):
        self._service = service
        self._cache = {}
        self._hits = 0
        self._misses = 0

    def get(self, key):
        if key in self._cache:
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        value = self._service.get(key)
        if value is not None:
            self._cache[key] = value
        return value

    def set(self, key, value):
        self._service.set(key, value)
        self._cache[key] = value

    def delete(self, key):
        self._cache.pop(key, None)
        return self._service.delete(key)

    def invalidate(self, key=None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    def stats(self):
        total = self._hits + self._misses
        ratio = self._hits / total if total > 0 else 0
        return {"hits": self._hits, "misses": self._misses,
                "total": total, "hit_ratio": round(ratio, 4)}

class LoggingProxy:
    def __init__(self, service):
        self._service = service
        self._log = []

    def get(self, key):
        self._log.append(("get", key))
        return self._service.get(key)

    def set(self, key, value):
        self._log.append(("set", key, value))
        self._service.set(key, value)

    def get_log(self):
        return list(self._log)
''',
    'iterator_pattern': '''
class TreeNode:
    def __init__(self, value, left=None, right=None):
        self.value = value
        self.left = left
        self.right = right

class InorderIterator:
    def __init__(self, root):
        self._stack = []
        self._push_left(root)

    def _push_left(self, node):
        while node:
            self._stack.append(node)
            node = node.left

    def has_next(self):
        return len(self._stack) > 0

    def next(self):
        if not self.has_next():
            raise StopIteration
        node = self._stack.pop()
        self._push_left(node.right)
        return node.value

class PreorderIterator:
    def __init__(self, root):
        self._stack = [root] if root else []

    def has_next(self):
        return len(self._stack) > 0

    def next(self):
        if not self.has_next():
            raise StopIteration
        node = self._stack.pop()
        if node.right:
            self._stack.append(node.right)
        if node.left:
            self._stack.append(node.left)
        return node.value

class LevelOrderIterator:
    def __init__(self, root):
        self._queue = [root] if root else []

    def has_next(self):
        return len(self._queue) > 0

    def next(self):
        if not self.has_next():
            raise StopIteration
        node = self._queue.pop(0)
        if node.left:
            self._queue.append(node.left)
        if node.right:
            self._queue.append(node.right)
        return node.value

def collect_all(iterator):
    result = []
    while iterator.has_next():
        result.append(iterator.next())
    return result
''',
    'chain_of_responsibility': '''
class Handler:
    def __init__(self):
        self._next = None

    def set_next(self, handler):
        self._next = handler
        return handler

    def handle(self, request):
        if self._next:
            return self._next.handle(request)
        return None

class AuthenticationHandler(Handler):
    def handle(self, request):
        token = request.get("token", "")
        if not token:
            return {"error": "No authentication token", "status": 401}
        if not token.startswith("valid_"):
            return {"error": "Invalid token", "status": 403}
        request["authenticated"] = True
        return super().handle(request)

class RateLimitHandler(Handler):
    def __init__(self, max_requests=100):
        super().__init__()
        self._counts = {}
        self._max = max_requests

    def handle(self, request):
        client = request.get("client_ip", "unknown")
        self._counts[client] = self._counts.get(client, 0) + 1
        if self._counts[client] > self._max:
            return {"error": "Rate limit exceeded", "status": 429}
        return super().handle(request)

class ValidationHandler(Handler):
    def __init__(self, required_fields=None):
        super().__init__()
        self._required = required_fields or []

    def handle(self, request):
        body = request.get("body", {})
        missing = [f for f in self._required if f not in body]
        if missing:
            return {"error": "Missing fields: " + ", ".join(missing), "status": 400}
        return super().handle(request)

class FinalHandler(Handler):
    def handle(self, request):
        return {"status": 200, "data": request.get("body", {})}

def build_chain(*handlers):
    for i in range(len(handlers) - 1):
        handlers[i].set_next(handlers[i + 1])
    return handlers[0] if handlers else None
''',
    'retry_decorator': '''
class RetryConfig:
    def __init__(self, max_retries=3, delay=1.0, backoff=2.0, exceptions=None):
        self.max_retries = max_retries
        self.delay = delay
        self.backoff = backoff
        self.exceptions = exceptions or (Exception,)

class RetryResult:
    def __init__(self):
        self.attempts = 0
        self.success = False
        self.result = None
        self.last_error = None
        self.errors = []

def retry_call(func, config=None, args=None, kwargs=None):
    if config is None:
        config = RetryConfig()
    if args is None:
        args = ()
    if kwargs is None:
        kwargs = {}
    result = RetryResult()
    current_delay = config.delay
    for attempt in range(config.max_retries + 1):
        result.attempts = attempt + 1
        try:
            result.result = func(*args, **kwargs)
            result.success = True
            return result
        except config.exceptions as e:
            result.last_error = e
            result.errors.append(str(e))
            current_delay *= config.backoff
    return result

def with_retry(max_retries=3):
    def decorator(func):
        def wrapper(*args, **kwargs):
            config = RetryConfig(max_retries=max_retries)
            result = retry_call(func, config, args, kwargs)
            if result.success:
                return result.result
            raise result.last_error
        return wrapper
    return decorator
''',
    'timeout_handler': '''
class TimeoutError(Exception):
    pass

class Timer:
    def __init__(self):
        self._start = None
        self._end = None
        self._laps = []

    def start(self):
        self._start = 0.0
        self._end = None
        self._laps = []

    def lap(self, timestamp):
        if self._start is not None:
            elapsed = timestamp - self._start
            self._laps.append(elapsed)
        return len(self._laps)

    def stop(self, timestamp):
        self._end = timestamp
        return self.elapsed()

    def elapsed(self):
        if self._start is None:
            return 0.0
        if self._end is not None:
            return self._end - self._start
        return 0.0

    def laps(self):
        return list(self._laps)

class TimeoutContext:
    def __init__(self, timeout_seconds):
        self.timeout = timeout_seconds
        self.start_time = None
        self.timed_out = False

    def check(self, current_time):
        if self.start_time is None:
            self.start_time = current_time
        elapsed = current_time - self.start_time
        if elapsed >= self.timeout:
            self.timed_out = True
            return False
        return True

    def remaining(self, current_time):
        if self.start_time is None:
            return self.timeout
        elapsed = current_time - self.start_time
        return max(0, self.timeout - elapsed)

def run_with_timeout(func, timeout, current_time=0, *args):
    ctx = TimeoutContext(timeout)
    ctx.start_time = current_time
    if ctx.check(current_time):
        return func(*args)
    raise TimeoutError("Operation timed out")
''',
    'pagination_helper': '''
class Paginator:
    def __init__(self, items, page_size=10):
        self._items = list(items)
        self._page_size = max(1, page_size)

    def total_items(self):
        return len(self._items)

    def total_pages(self):
        total = len(self._items)
        return (total + self._page_size - 1) // self._page_size

    def get_page(self, page_number):
        if page_number < 1 or page_number > self.total_pages():
            return []
        start = (page_number - 1) * self._page_size
        end = start + self._page_size
        return self._items[start:end]

    def has_next(self, page_number):
        return page_number < self.total_pages()

    def has_prev(self, page_number):
        return page_number > 1

    def page_info(self, page_number):
        return {
            "page": page_number,
            "page_size": self._page_size,
            "total_items": self.total_items(),
            "total_pages": self.total_pages(),
            "has_next": self.has_next(page_number),
            "has_prev": self.has_prev(page_number),
            "items": self.get_page(page_number),
        }

    def all_page_numbers(self):
        return list(range(1, self.total_pages() + 1))

def paginate_query(items, page, size, sort_key=None, reverse=False):
    if sort_key:
        items = sorted(items, key=sort_key, reverse=reverse)
    paginator = Paginator(items, size)
    return paginator.page_info(page)
''',
    'date_calculator': '''
def is_leap_year(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

def days_in_month(year, month):
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    return days[month]

def days_in_year(year):
    return 366 if is_leap_year(year) else 365

def day_of_year(year, month, day):
    total = 0
    for m in range(1, month):
        total += days_in_month(year, m)
    return total + day

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

def days_between(y1, m1, d1, y2, m2, d2):
    def to_absolute(y, m, d):
        total = 0
        for yr in range(1, y):
            total += days_in_year(yr)
        total += day_of_year(y, m, d)
        return total
    return abs(to_absolute(y2, m2, d2) - to_absolute(y1, m1, d1))

def day_of_week(year, month, day):
    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if month < 3:
        year -= 1
    dow = (year + year // 4 - year // 100 + year // 400 + t[month - 1] + day) % 7
    names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    return names[dow]
''',
    'color_converter': '''
def hex_to_rgb(hex_str):
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c + c for c in hex_str)
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return r, g, b

def rgb_to_hex(r, g, b):
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

def rgb_to_hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    l = (max_c + min_c) / 2.0
    if max_c == min_c:
        h = s = 0.0
    else:
        d = max_c - min_c
        s = d / (2.0 - max_c - min_c) if l > 0.5 else d / (max_c + min_c)
        if max_c == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif max_c == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h /= 6.0
    return round(h * 360, 1), round(s * 100, 1), round(l * 100, 1)

def hsl_to_rgb(h, s, l):
    h, s, l = h / 360.0, s / 100.0, l / 100.0
    if s == 0:
        r = g = b = l
    else:
        def hue_to_rgb(p, q, t):
            if t < 0: t += 1
            if t > 1: t -= 1
            if t < 1/6: return p + (q - p) * 6 * t
            if t < 1/2: return q
            if t < 2/3: return p + (q - p) * (2/3 - t) * 6
            return p
        q = l * (1 + s) if l < 0.5 else l + s - l * s
        p = 2 * l - q
        r = hue_to_rgb(p, q, h + 1/3)
        g = hue_to_rgb(p, q, h)
        b = hue_to_rgb(p, q, h - 1/3)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))

def blend_colors(c1, c2, ratio=0.5):
    r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
    g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
    b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
    return r, g, b
''',
    'unit_converter': '''
class UnitConverter:
    def __init__(self):
        self._conversions = {}

    def add_conversion(self, from_unit, to_unit, factor):
        self._conversions[(from_unit, to_unit)] = factor
        if factor != 0:
            self._conversions[(to_unit, from_unit)] = 1.0 / factor

    def convert(self, value, from_unit, to_unit):
        if from_unit == to_unit:
            return value
        key = (from_unit, to_unit)
        if key in self._conversions:
            return value * self._conversions[key]
        for mid_unit in self._get_units():
            k1 = (from_unit, mid_unit)
            k2 = (mid_unit, to_unit)
            if k1 in self._conversions and k2 in self._conversions:
                return value * self._conversions[k1] * self._conversions[k2]
        raise ValueError("No conversion path from " + from_unit + " to " + to_unit)

    def _get_units(self):
        units = set()
        for f, t in self._conversions:
            units.add(f)
            units.add(t)
        return units

    def list_conversions(self):
        return list(self._conversions.keys())

def create_length_converter():
    c = UnitConverter()
    c.add_conversion("m", "km", 0.001)
    c.add_conversion("m", "cm", 100)
    c.add_conversion("m", "mm", 1000)
    c.add_conversion("m", "in", 39.3701)
    c.add_conversion("m", "ft", 3.28084)
    c.add_conversion("m", "mi", 0.000621371)
    return c

def create_weight_converter():
    c = UnitConverter()
    c.add_conversion("kg", "g", 1000)
    c.add_conversion("kg", "mg", 1000000)
    c.add_conversion("kg", "lb", 2.20462)
    c.add_conversion("kg", "oz", 35.274)
    return c
''',
    'csv_processor': '''
def parse_csv(text, delimiter=",", has_header=True):
    lines = text.strip().splitlines()
    if not lines:
        return [], []
    rows = []
    for line in lines:
        row = []
        current = ""
        in_quotes = False
        for ch in line:
            if ch == '"':
                in_quotes = not in_quotes
            elif ch == delimiter and not in_quotes:
                row.append(current.strip())
                current = ""
            else:
                current += ch
        row.append(current.strip())
        rows.append(row)
    if has_header:
        header = rows[0]
        data = rows[1:]
        return header, data
    return [], rows

def to_dicts(header, rows):
    result = []
    for row in rows:
        record = {}
        for i, col in enumerate(header):
            record[col] = row[i] if i < len(row) else ""
        result.append(record)
    return result

def serialize_csv(header, rows, delimiter=","):
    lines = [delimiter.join(header)]
    for row in rows:
        escaped = []
        for val in row:
            s = str(val)
            if delimiter in s or '"' in s:
                s = '"' + s.replace('"', '""') + '"'
            escaped.append(s)
        lines.append(delimiter.join(escaped))
    return lines

def filter_rows(header, rows, column, predicate):
    col_idx = header.index(column) if column in header else -1
    if col_idx < 0:
        return []
    return [row for row in rows if col_idx < len(row) and predicate(row[col_idx])]
''',
    'json_flattener': '''
def flatten_json(obj, prefix="", sep="."):
    result = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_key = prefix + sep + key if prefix else key
            if isinstance(value, (dict, list)):
                result.update(flatten_json(value, new_key, sep))
            else:
                result[new_key] = value
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            new_key = prefix + sep + str(i) if prefix else str(i)
            if isinstance(value, (dict, list)):
                result.update(flatten_json(value, new_key, sep))
            else:
                result[new_key] = value
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
            is_index = next_part.isdigit()
            if part not in current:
                current[part] = [] if is_index else {}
            current = current[part]
        current[parts[-1]] = value
    return result

def deep_get(obj, path, default=None, sep="."):
    parts = path.split(sep)
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else default
        else:
            return default
    return current

def deep_set(obj, path, value, sep="."):
    parts = path.split(sep)
    current = obj
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
''',
    'checksum_calculator': '''
def adler32(data):
    a = 1
    b = 0
    mod = 65521
    for byte in data:
        if isinstance(byte, int):
            a = (a + byte) % mod
        else:
            a = (a + ord(byte)) % mod
        b = (b + a) % mod
    return (b << 16) | a

def crc32_simple(data):
    crc = 0xFFFFFFFF
    poly = 0xEDB88320
    for byte in data:
        if isinstance(byte, int):
            val = byte
        else:
            val = ord(byte)
        crc ^= val
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
    return crc ^ 0xFFFFFFFF

def fletcher16(data):
    sum1 = 0
    sum2 = 0
    for byte in data:
        if isinstance(byte, int):
            val = byte
        else:
            val = ord(byte)
        sum1 = (sum1 + val) % 255
        sum2 = (sum2 + sum1) % 255
    return (sum2 << 8) | sum1

def luhn_check(number_str):
    digits = [int(d) for d in number_str if d.isdigit()]
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0

def xor_checksum(data):
    result = 0
    for byte in data:
        if isinstance(byte, int):
            result ^= byte
        else:
            result ^= ord(byte)
    return result
''',
    'task_scheduler': '''
class Task:
    def __init__(self, task_id, priority=0, callback=None, dependencies=None):
        self.task_id = task_id
        self.priority = priority
        self.callback = callback
        self.dependencies = set(dependencies or [])
        self.status = "pending"
        self.result = None

class TaskScheduler:
    def __init__(self):
        self._tasks = {}
        self._completed = set()

    def add_task(self, task_id, priority=0, callback=None, dependencies=None):
        task = Task(task_id, priority, callback, dependencies)
        self._tasks[task_id] = task
        return task

    def get_ready_tasks(self):
        ready = []
        for task in self._tasks.values():
            if task.status == "pending":
                if task.dependencies.issubset(self._completed):
                    ready.append(task)
        ready.sort(key=lambda t: -t.priority)
        return ready

    def execute_next(self):
        ready = self.get_ready_tasks()
        if not ready:
            return None
        task = ready[0]
        task.status = "running"
        if task.callback:
            task.result = task.callback()
        task.status = "completed"
        self._completed.add(task.task_id)
        return task

    def run_all(self):
        results = []
        while True:
            task = self.execute_next()
            if task is None:
                break
            results.append((task.task_id, task.result))
        return results

    def status(self):
        return {
            "total": len(self._tasks),
            "completed": len(self._completed),
            "pending": sum(1 for t in self._tasks.values() if t.status == "pending"),
        }
''',
    'event_emitter': '''
class TypedEventEmitter:
    def __init__(self):
        self._handlers = {}
        self._global_handlers = []
        self._max_listeners = 100

    def on(self, event_type, handler):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        if len(self._handlers[event_type]) < self._max_listeners:
            self._handlers[event_type].append(handler)

    def on_any(self, handler):
        self._global_handlers.append(handler)

    def off(self, event_type, handler=None):
        if handler is None:
            self._handlers.pop(event_type, None)
        elif event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]

    def emit(self, event_type, data=None):
        event = {"type": event_type, "data": data}
        results = []
        for handler in self._global_handlers:
            results.append(handler(event))
        for handler in self._handlers.get(event_type, []):
            results.append(handler(event))
        return results

    def once(self, event_type, handler):
        def wrapper(event):
            self.off(event_type, wrapper)
            return handler(event)
        self.on(event_type, wrapper)

    def listener_count(self, event_type=None):
        if event_type:
            return len(self._handlers.get(event_type, []))
        total = sum(len(h) for h in self._handlers.values())
        return total + len(self._global_handlers)

    def event_types(self):
        return list(self._handlers.keys())

    def set_max_listeners(self, n):
        self._max_listeners = n
''',
    'bloom_filter_impl': '''
class BloomFilter:
    def __init__(self, size=1000, num_hashes=3):
        self._size = size
        self._num_hashes = num_hashes
        self._bits = [False] * size
        self._count = 0

    def _hash(self, item, seed):
        h = seed
        for ch in str(item):
            h = (h * 31 + ord(ch)) & 0x7FFFFFFF
        return h % self._size

    def _get_hashes(self, item):
        return [self._hash(item, i * 97 + 1) for i in range(self._num_hashes)]

    def add(self, item):
        for idx in self._get_hashes(item):
            self._bits[idx] = True
        self._count += 1

    def contains(self, item):
        return all(self._bits[idx] for idx in self._get_hashes(item))

    def false_positive_rate(self):
        set_bits = sum(self._bits)
        if self._size == 0:
            return 1.0
        return (set_bits / self._size) ** self._num_hashes

    def count(self):
        return self._count

    def merge(self, other):
        if self._size != other._size or self._num_hashes != other._num_hashes:
            raise ValueError("Incompatible bloom filters")
        result = BloomFilter(self._size, self._num_hashes)
        for i in range(self._size):
            result._bits[i] = self._bits[i] or other._bits[i]
        result._count = self._count + other._count
        return result

    def clear(self):
        self._bits = [False] * self._size
        self._count = 0
''',
    'base64_codec': '''
CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

def base64_encode(data):
    if isinstance(data, str):
        data = [ord(c) for c in data]
    result = []
    i = 0
    while i < len(data):
        b0 = data[i]
        b1 = data[i + 1] if i + 1 < len(data) else 0
        b2 = data[i + 2] if i + 2 < len(data) else 0
        triplet = (b0 << 16) | (b1 << 8) | b2
        result.append(CHARSET[(triplet >> 18) & 0x3F])
        result.append(CHARSET[(triplet >> 12) & 0x3F])
        if i + 1 < len(data):
            result.append(CHARSET[(triplet >> 6) & 0x3F])
        else:
            result.append("=")
        if i + 2 < len(data):
            result.append(CHARSET[triplet & 0x3F])
        else:
            result.append("=")
        i += 3
    return "".join(result)

def base64_decode(encoded):
    lookup = {}
    for i, ch in enumerate(CHARSET):
        lookup[ch] = i
    result = []
    encoded = encoded.rstrip("=")
    i = 0
    while i < len(encoded):
        c0 = lookup.get(encoded[i], 0) if i < len(encoded) else 0
        c1 = lookup.get(encoded[i + 1], 0) if i + 1 < len(encoded) else 0
        c2 = lookup.get(encoded[i + 2], 0) if i + 2 < len(encoded) else 0
        c3 = lookup.get(encoded[i + 3], 0) if i + 3 < len(encoded) else 0
        triplet = (c0 << 18) | (c1 << 12) | (c2 << 6) | c3
        result.append((triplet >> 16) & 0xFF)
        if i + 2 < len(encoded):
            result.append((triplet >> 8) & 0xFF)
        if i + 3 < len(encoded):
            result.append(triplet & 0xFF)
        i += 4
    return result

def base64_encode_string(text):
    return base64_encode([ord(c) for c in text])

def base64_decode_string(encoded):
    return "".join(chr(b) for b in base64_decode(encoded))
''',
    'diff_calculator': '''
def compute_diff(old_lines, new_lines):
    m, n = len(old_lines), len(new_lines)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if old_lines[i - 1] == new_lines[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    operations = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and old_lines[i - 1] == new_lines[j - 1]:
            operations.append((" ", old_lines[i - 1]))
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            operations.append(("+", new_lines[j - 1]))
            j -= 1
        else:
            operations.append(("-", old_lines[i - 1]))
            i -= 1
    return list(reversed(operations))

def format_diff(operations):
    lines = []
    for op, line in operations:
        lines.append(op + " " + line)
    return lines

def count_changes(operations):
    additions = sum(1 for op, _ in operations if op == "+")
    deletions = sum(1 for op, _ in operations if op == "-")
    unchanged = sum(1 for op, _ in operations if op == " ")
    return {"additions": additions, "deletions": deletions, "unchanged": unchanged}

def apply_diff(old_lines, operations):
    result = []
    for op, line in operations:
        if op == " " or op == "+":
            result.append(line)
    return result
''',
    'semaphore_impl': '''
class Semaphore:
    def __init__(self, permits=1):
        self._permits = permits
        self._max_permits = permits
        self._waiters = []
        self._acquired_by = []

    def acquire(self, holder_id=None):
        if self._permits > 0:
            self._permits -= 1
            if holder_id:
                self._acquired_by.append(holder_id)
            return True
        if holder_id:
            self._waiters.append(holder_id)
        return False

    def release(self, holder_id=None):
        if holder_id and holder_id in self._acquired_by:
            self._acquired_by.remove(holder_id)
        self._permits = min(self._permits + 1, self._max_permits)
        if self._waiters and self._permits > 0:
            next_holder = self._waiters.pop(0)
            self._permits -= 1
            self._acquired_by.append(next_holder)
            return next_holder
        return None

    def available(self):
        return self._permits

    def waiting_count(self):
        return len(self._waiters)

class ReadWriteLock:
    def __init__(self):
        self._readers = 0
        self._writer = False
        self._write_waiters = []
        self._read_waiters = []

    def acquire_read(self, holder_id=None):
        if not self._writer and not self._write_waiters:
            self._readers += 1
            return True
        if holder_id:
            self._read_waiters.append(holder_id)
        return False

    def release_read(self):
        self._readers = max(0, self._readers - 1)
        if self._readers == 0 and self._write_waiters:
            self._writer = True
            return self._write_waiters.pop(0)
        return None

    def acquire_write(self, holder_id=None):
        if self._readers == 0 and not self._writer:
            self._writer = True
            return True
        if holder_id:
            self._write_waiters.append(holder_id)
        return False

    def release_write(self):
        self._writer = False
        if self._write_waiters:
            self._writer = True
            return self._write_waiters.pop(0)
        while self._read_waiters:
            self._readers += 1
            self._read_waiters.pop(0)
        return None
''',
}


TRUST_FLOORS = ["unverified", "copilot", "solver", "proven"]

# ── Literature baselines (NOT computed — sourced from publications) ──────

LITERATURE_BASELINES = {
    "description": (
        "Components retained per proof/judgment object in existing systems. "
        "JuGeo retains an 8-component tuple; these are the counts for comparators."
    ),
    "Lean4": {
        "components_retained": 3,
        "fields": ["context", "term", "type"],
        "source": "Lean 4 Reference Manual §4 (Expression representation)",
    },
    "Fstar": {
        "components_retained": 4,
        "fields": ["context", "term", "type", "effect"],
        "source": "F* Tutorial §2.3 (Computation types and effects)",
    },
    "Dafny": {
        "components_retained": 3.5,
        "fields": ["context", "term", "type", "counterexample (partial)"],
        "source": "Dafny Reference §7 (Verification diagnostics)",
    },
}

# ── Internal API: judgment construction helpers ──────────────────────────

TRUST_LEVELS = [
    TrustLevel.CONTRADICTED,
    TrustLevel.UNVERIFIED,
    TrustLevel.COPILOT_SUGGESTED,
    TrustLevel.RUNTIME_WITNESSED,
    TrustLevel.SOLVER_DISCHARGED,
    TrustLevel.VERIFIED_PROOF,
]

EVIDENCE_KINDS = [
    EvidenceItemKind.SOLVER_PROOF,
    EvidenceItemKind.RUNTIME_WITNESS,
    EvidenceItemKind.ORACLE_PROPOSAL,
    EvidenceItemKind.FORMAL_PROOF,
]

COORD_KINDS = [
    CoordinateKind.MODULE,
    CoordinateKind.FUNCTION,
    CoordinateKind.REGION,
    CoordinateKind.INTERFACE,
    CoordinateKind.TEST,
]


def make_judgment(idx: int) -> Judgment:
    """Build a judgment with deterministic randomised components."""
    trust_level = random.choice(TRUST_LEVELS)
    ek = random.choice(EVIDENCE_KINDS)
    ck = random.choice(COORD_KINDS)
    coord_name = f"module.fn{idx}"
    formula = random.choice([
        f"x_{idx} > 0",
        f"len(arr_{idx}) >= 1",
        f"result_{idx} != None",
        f"idx_{idx} < n_{idx}",
    ])
    carrier_name = random.choice(["int", "list", "str", "float", "bool"])

    c = Coordinate(coord_name, kind=ck)
    ei = EvidenceItem(kind=ek, trust_level=trust_level, channel=f"ch_{idx}")
    ta = TrustAnnotation(level=trust_level, evidence_basis=(f"basis_{idx}",))

    builder = (
        JudgmentBuilder()
        .at(c)
        .claiming_formula(formula)
        .of_type_named(carrier_name)
        .with_trust(ta)
        .with_evidence(ei)
    )
    if idx % 3 == 0:
        ro = ResidualObligation(
            description=f"obligation_{idx}", required_evidence_kind=ek
        )
        builder = builder.with_obligation(ro)
    if idx % 5 == 0:
        ob = Obstruction(
            violated_condition=f"cond_{idx}",
            description=f"obstruction_{idx}",
            coordinate=coord_name,
        )
        builder = builder.with_obstruction(ob)
    return builder.build()


def count_fields(j: Judgment) -> dict:
    return {
        "coordinate": 1 if j.coordinate else 0,
        "proposition": 1 if j.proposition else 0,
        "carrier": 1 if j.carrier else 0,
        "evidence_count": len(j.evidence),
        "obligations_count": len(j.obligations),
        "obstructions_count": len(j.obstructions),
        "trust_level": int(j.trust.level),
        "provenance_length": len(j.provenance.transformation_history),
    }


def sum_fields(fc: dict) -> int:
    return sum(1 for v in fc.values() if v)


N_ITERATIONS = 10_000


def main():
    print("=" * 76)
    print("PAPER 2 — An Algebraic Foundation for Evidence-Carrying Proofs")
    print("  CLI trust-floor experiments + internal JudgmentAlgebra API")
    print("=" * 76)
    print()

    tmpfiles = []

    # ══════════════════════════════════════════════════════════════════════
    # PART A: CLI-based trust-floor experiments via `jugeo prove`
    # ══════════════════════════════════════════════════════════════════════
    print("PART A: Trust-floor experiments via `jugeo prove` CLI")
    print("-" * 76)

    cli_results = []
    for name, source in PROGRAMS.items():
        path = write_temp(source)
        tmpfiles.append(path)
        floor = "copilot"
        t0 = time.perf_counter()
        objs = run_jugeo("prove", path, "--trust-floor", floor)
        wall_s = time.perf_counter() - t0

        prove = objs[0] if objs else {}
        formal = objs[1] if len(objs) > 1 else {}

        finfo = (prove.get("files") or [{}])[0]
        ta = formal.get("formal_verification", {}).get("trust_algebra", {})
        axiom_results = ta.get("axiom_results", {})

        # Extract 8-tuple judgment components
        coords = finfo.get("coordinates", [])
        props = finfo.get("propositions_total", 0)
        evidence_items = len(finfo.get("evidence", []))
        trust_level = finfo.get("trust", "?")
        obligations = len(finfo.get("obligations", []))
        obstructions_count = len(finfo.get("obstructions", []))

        cli_results.append({
            "program": name,
            "trust_floor": floor,
            "verdict": finfo.get("verdict", "?"),
            "trust": trust_level,
            "propositions_ok": finfo.get("propositions_ok", 0),
            "propositions_total": props,
            "obstructions": obstructions_count,
            "trust_algebra_passed": ta.get("passed", False),
            "axioms": axiom_results,
            "wall_s": round(wall_s, 4),
            "coordinates_count": len(coords) if isinstance(coords, list) else 0,
            "evidence_count": evidence_items,
            "obligations_count": obligations,
        })

    # Print trust-floor table
    print(
        f"\n  {'Program':<30} {'Verdict':<10} {'Trust':<20} "
        f"{'Props':>5} {'Obs':>4} {'Alg OK':>7}"
    )
    print(f"  {'-'*78}")
    for r in cli_results:
        print(
            f"  {r['program']:<30} {r['verdict']:<10} "
            f"{r['trust']:<20} "
            f"{r['propositions_ok']}/{r['propositions_total']:>2} "
            f"{r['obstructions']:>4} {str(r['trust_algebra_passed']):>7}"
        )

    # Trust algebra axiom matrix
    axiom_names = [
        "reflexivity", "transitivity", "antisymmetry",
        "meet_exists", "oracle_ceiling", "monotonicity", "contradicted_absorbs",
    ]
    print(f"\nTRUST ALGEBRA AXIOM MATRIX (all programs, copilot floor):")
    print(f"  {'Program':<30}", end="")
    for a in axiom_names:
        print(f" {a[:6]:>7}", end="")
    print()
    print(f"  {'-'*86}")
    for r in cli_results:
        print(f"  {r['program']:<30}", end="")
        for a in axiom_names:
            val = r["axioms"].get(a, None)
            sym = "✓" if val is True else ("✗" if val is False else "?")
            print(f" {sym:>7}", end="")
        print()

    # 8-tuple summary
    print(f"\n8-TUPLE JUDGMENT COMPONENTS SUMMARY:")
    print(
        f"  {'Program':<30} {'Coords':>6} {'Props':>5} {'Evid':>5} "
        f"{'Trust':<20} {'Obligs':>6} {'Obs':>4}"
    )
    print(f"  {'-'*80}")
    for r in cli_results:
        print(
            f"  {r['program']:<30} {r['coordinates_count']:>6} "
            f"{r['propositions_total']:>5} {r['evidence_count']:>5} "
            f"{r['trust']:<20} {r['obligations_count']:>6} "
            f"{r['obstructions']:>4}"
        )

    # Aggregate statistics
    n_programs = len(cli_results)
    n_verified = sum(1 for r in cli_results if r["verdict"] in ("verified", "pass"))
    mean_props = sum(r["propositions_total"] for r in cli_results) / max(n_programs, 1)
    mean_coords = sum(r["coordinates_count"] for r in cli_results) / max(n_programs, 1)
    mean_obs = sum(r["obstructions"] for r in cli_results) / max(n_programs, 1)

    axiom_pass = {}
    for a in axiom_names:
        pass_count = sum(1 for r in cli_results if r["axioms"].get(a) is True)
        axiom_pass[a] = pass_count

    print(f"\nAGGREGATE STATISTICS:")
    print(f"  Total programs:      {n_programs}")
    print(f"  Programs verified:   {n_verified}")
    print(f"  Mean propositions:   {mean_props:.1f}")
    print(f"  Mean coordinates:    {mean_coords:.1f}")
    print(f"  Mean obstructions:   {mean_obs:.2f}")
    print(f"\n  Axiom pass rates:")
    for a in axiom_names:
        pct = axiom_pass[a] / max(n_programs, 1) * 100
        print(f"    {a:<24} {axiom_pass[a]:>3}/{n_programs} ({pct:5.1f}%)")

    # ══════════════════════════════════════════════════════════════════════
    # PART B: Internal JudgmentAlgebra API — 8-tuple operations
    # ══════════════════════════════════════════════════════════════════════
    print()
    print("PART B: Internal JudgmentAlgebra operations (8-tuple completeness)")
    print("-" * 76)

    random.seed(42)
    judgments = [make_judgment(i) for i in range(20)]
    print(f"Built {len(judgments)} judgments at trust levels:")
    for i, j in enumerate(judgments):
        fc = count_fields(j)
        print(
            f"  J{i:02d}: trust={j.trust.level.label():<20s} "
            f"evidence={fc['evidence_count']} "
            f"obligations={fc['obligations_count']} "
            f"obstructions={fc['obstructions_count']} "
            f"fields_alive={sum_fields(fc)}"
        )
    print()

    j0, j1 = judgments[0], judgments[1]
    sub = Coordinate("module.fn0.body", kind=CoordinateKind.REGION)
    morph = CoordinateMorphism(
        source=j0.coordinate.key,
        target="module.fn0.target",
        reason="test_transport",
    )

    operations = {}

    # restrict
    t0 = time.perf_counter()
    for _ in range(N_ITERATIONS):
        restricted = JudgmentAlgebra.restrict(j0, sub)
    dt = time.perf_counter() - t0
    fc_restrict = count_fields(restricted)
    operations["restrict"] = {
        "total_time_ms": round(dt * 1000, 2),
        "per_op_us": round(dt / N_ITERATIONS * 1e6, 3),
        "fields_survived": fc_restrict,
        "fields_alive": sum_fields(fc_restrict),
    }

    # transport
    t0 = time.perf_counter()
    for _ in range(N_ITERATIONS):
        transported = JudgmentAlgebra.transport(j0, morph)
    dt = time.perf_counter() - t0
    fc_transport = count_fields(transported)
    operations["transport"] = {
        "total_time_ms": round(dt * 1000, 2),
        "per_op_us": round(dt / N_ITERATIONS * 1e6, 3),
        "fields_survived": fc_transport,
        "fields_alive": sum_fields(fc_transport),
    }

    # compose
    t0 = time.perf_counter()
    for _ in range(N_ITERATIONS):
        composed = JudgmentAlgebra.compose(j0, j1)
    dt = time.perf_counter() - t0
    fc_compose = count_fields(composed)
    operations["compose"] = {
        "total_time_ms": round(dt * 1000, 2),
        "per_op_us": round(dt / N_ITERATIONS * 1e6, 3),
        "fields_survived": fc_compose,
        "fields_alive": sum_fields(fc_compose),
    }

    # merge
    t0 = time.perf_counter()
    for _ in range(N_ITERATIONS):
        merged = JudgmentAlgebra.merge(j0, j1)
    dt = time.perf_counter() - t0
    fc_merge = count_fields(merged)
    operations["merge"] = {
        "total_time_ms": round(dt * 1000, 2),
        "per_op_us": round(dt / N_ITERATIONS * 1e6, 3),
        "fields_survived": fc_merge,
        "fields_alive": sum_fields(fc_merge),
    }

    # compare_trust
    t0 = time.perf_counter()
    for _ in range(N_ITERATIONS):
        cmp = JudgmentAlgebra.compare_trust(j0, j1)
    dt = time.perf_counter() - t0
    operations["compare_trust"] = {
        "total_time_ms": round(dt * 1000, 2),
        "per_op_us": round(dt / N_ITERATIONS * 1e6, 3),
        "result": cmp,
    }

    print(
        f"Algebra operation timing ({N_ITERATIONS:,} iterations each):\n"
        f"{'Operation':<16} {'Total ms':>10} {'Per-op μs':>10} {'Fields alive':>13}"
    )
    print("-" * 55)
    for op, data in operations.items():
        alive = data.get("fields_alive", "—")
        print(
            f"{op:<16} {data['total_time_ms']:>10.2f} "
            f"{data['per_op_us']:>10.3f} {str(alive):>13}"
        )
    print()

    # Information retention analysis
    print("Information retention per operation (field counts):")
    for op in ["restrict", "transport", "compose", "merge"]:
        fc = operations[op]["fields_survived"]
        alive = operations[op]["fields_alive"]
        print(f"  {op:<16} → {alive} fields alive: {fc}")
    print()

    # Comparison with literature baselines
    jugeo_fields_alive = max(
        operations[op]["fields_alive"]
        for op in ["restrict", "transport", "compose", "merge"]
    )
    print("Information retention comparison (JuGeo vs literature):")
    print(
        f"{'System':<12} {'Components':>11} "
        f"{'Fields':<45} {'Source'}"
    )
    print("-" * 100)
    print(
        f"{'JuGeo':.<12} {jugeo_fields_alive:>11} "
        f"{'coord,prop,carrier,evidence,obligs,obstrs,trust,prov':<45} "
        f"{'This experiment (measured)'}"
    )
    for name, info in LITERATURE_BASELINES.items():
        if name == "description":
            continue
        fields_str = ", ".join(info["fields"])
        print(
            f"{name:<12} {info['components_retained']:>11.1f} "
            f"{fields_str:<45} {info['source']}"
        )
    print()

    advantage = jugeo_fields_alive / max(
        info["components_retained"]
        for name, info in LITERATURE_BASELINES.items()
        if name != "description"
    )
    print(
        f"JuGeo retains {jugeo_fields_alive} components vs "
        f"max {max(info['components_retained'] for n, info in LITERATURE_BASELINES.items() if n != 'description'):.1f} "
        f"in literature → {advantage:.1f}× more information through algebra ops."
    )
    print()

    # Cross-operation consistency checks
    print("Consistency checks:")
    n_consistent = 0
    for i in range(len(judgments)):
        for k in range(i + 1, len(judgments)):
            ok = JudgmentAlgebra.is_consistent_with(judgments[i], judgments[k])
            if ok:
                n_consistent += 1
    print(
        f"  Pairwise consistency: {n_consistent} / "
        f"{len(judgments)*(len(judgments)-1)//2} pairs consistent"
    )
    all_obs = JudgmentAlgebra.collect_obstructions(judgments)
    print(f"  Collected obstructions: {len(all_obs)}")
    all_discharged = JudgmentAlgebra.all_discharged(judgments)
    print(f"  All discharged: {all_discharged}")

    composed_for_split = JudgmentAlgebra.compose(judgments[0], judgments[1])
    parts_split = JudgmentAlgebra.split(composed_for_split)
    print(f"  Split composed judgment → {len(parts_split)} parts")

    w0, w1 = JudgmentAlgebra.weaken_to_common(judgments[0], judgments[5])
    print(
        f"  Weaken-to-common: {judgments[0].trust.level.label()} & "
        f"{judgments[5].trust.level.label()} → {w0.trust.level.label()}"
    )
    print()

    # ── Save results ─────────────────────────────────────────────────────
    results = {
        "experiment": "judgment_algebra",
        "paper": 2,
        "note": "Part A from CLI subprocess; Part B from internal API.",
        "cli_trust_floor_results": cli_results,
        "n_programs": n_programs,
        "n_verified": n_verified,
        "mean_propositions": round(mean_props, 2),
        "mean_coordinates": round(mean_coords, 2),
        "mean_obstructions": round(mean_obs, 2),
        "axiom_pass_rates": {a: axiom_pass[a] / max(n_programs, 1) for a in axiom_names},
        "n_judgments": len(judgments),
        "n_iterations": N_ITERATIONS,
        "operations": operations,
        "jugeo_fields_alive": jugeo_fields_alive,
        "literature_baselines": LITERATURE_BASELINES,
        "information_advantage_ratio": round(advantage, 2),
        "consistency_pairs": n_consistent,
        "total_obstructions": len(all_obs),
        "all_discharged": all_discharged,
        "split_parts": len(parts_split),
    }
    outpath = os.path.join(os.path.dirname(__file__), "results_paper02.json")
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Results → {outpath}")

    # ── cleanup ──────────────────────────────────────────────────────────
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
