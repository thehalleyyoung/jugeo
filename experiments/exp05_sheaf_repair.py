#!/usr/bin/env python3
"""Paper 05 Experiment — Sheaf-Guided Program Repair: Using H\u00b9 to Fix Bugs.

Runs jugeo bugs on 150 programs (100 clean + 50 buggy with known bugs), then
runs jugeo prove before/after repair to measure H\u00b9 improvement.

Every number is produced by calling the `python3 -m jugeo` CLI as a subprocess.
Re-run: python3 experiments/exp05_sheaf_repair.py
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
    text = "\\n".join(lines)
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


# ── 100 clean programs ───────────────────────────────────────────────

PROGRAMS = {
    "merge_sort": '''
def merge(left, right):
    # Merge two sorted lists into one sorted list.
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
    # Recursively sort a list using merge sort.
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)


def test_merge_sort():
    # Verify merge sort correctness.
    assert merge_sort([]) == []
    assert merge_sort([1]) == [1]
    assert merge_sort([3, 1, 2]) == [1, 2, 3]
    assert merge_sort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]
    assert merge_sort([1, 1, 1]) == [1, 1, 1]
    return True


if __name__ == "__main__":
    test_merge_sort()
''',
    "quicksort": '''
def partition(arr, low, high):
    # Partition array around pivot and return pivot index.
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def quicksort(arr, low=None, high=None):
    # Sort array in-place using quicksort algorithm.
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low < high:
        pi = partition(arr, low, high)
        quicksort(arr, low, pi - 1)
        quicksort(arr, pi + 1, high)
    return arr


def test_quicksort():
    # Verify quicksort correctness.
    assert quicksort([]) == []
    assert quicksort([1]) == [1]
    assert quicksort([3, 1, 2]) == [1, 2, 3]
    data = [9, 7, 5, 3, 1, 2, 4, 6, 8]
    assert quicksort(data) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    return True


if __name__ == "__main__":
    test_quicksort()
''',
    "heap_sort": '''
def heapify(arr, n, i):
    # Maintain max-heap property for subtree rooted at index i.
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
    # Sort array using heap sort algorithm.
    n = len(arr)
    for i in range(n // 2 - 1, -1, -1):
        heapify(arr, n, i)
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        heapify(arr, i, 0)
    return arr


def test_heap_sort():
    # Verify heap sort correctness.
    assert heap_sort([]) == []
    assert heap_sort([1]) == [1]
    assert heap_sort([5, 3, 8, 1, 2]) == [1, 2, 3, 5, 8]
    assert heap_sort([1, 1, 1]) == [1, 1, 1]
    return True


if __name__ == "__main__":
    test_heap_sort()
''',
    "insertion_sort": '''
def insertion_sort(arr):
    # Sort array using insertion sort with comparisons counter.
    comparisons = 0
    result = list(arr)
    n = len(result)
    for i in range(1, n):
        key = result[i]
        j = i - 1
        while j >= 0:
            comparisons += 1
            if result[j] > key:
                result[j + 1] = result[j]
                j -= 1
            else:
                break
        result[j + 1] = key
    return result, comparisons


def is_sorted(arr):
    # Check if an array is sorted in non-decreasing order.
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def test_insertion_sort():
    # Verify insertion sort correctness.
    res, _ = insertion_sort([])
    assert res == []
    res, _ = insertion_sort([1])
    assert res == [1]
    res, c = insertion_sort([3, 1, 2])
    assert res == [1, 2, 3]
    assert c > 0
    res, _ = insertion_sort([10, 9, 8, 7, 6])
    assert is_sorted(res)
    return True


if __name__ == "__main__":
    test_insertion_sort()
''',
    "radix_sort": '''
def counting_sort_by_digit(arr, exp):
    # Sort array by a specific digit position using counting sort.
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
    return arr


def radix_sort(arr):
    # Sort non-negative integers using radix sort (LSD).
    if not arr:
        return arr
    max_val = max(arr)
    exp = 1
    while max_val // exp > 0:
        counting_sort_by_digit(arr, exp)
        exp *= 10
    return arr


def test_radix_sort():
    # Verify radix sort correctness.
    assert radix_sort([]) == []
    assert radix_sort([170, 45, 75, 90, 802, 24, 2, 66]) == [2, 24, 45, 66, 75, 90, 170, 802]
    assert radix_sort([1]) == [1]
    return True


if __name__ == "__main__":
    test_radix_sort()
''',
    "shell_sort": '''
def shell_sort(arr):
    # Sort array using Shell sort with diminishing gap sequence.
    n = len(arr)
    result = list(arr)
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
    # Generate Knuth gap sequence for shell sort.
    gaps = []
    h = 1
    while h < n // 3:
        h = 3 * h + 1
    while h >= 1:
        gaps.append(h)
        h //= 3
    return gaps


def test_shell_sort():
    # Verify shell sort correctness.
    assert shell_sort([]) == []
    assert shell_sort([1]) == [1]
    assert shell_sort([5, 3, 1, 4, 2]) == [1, 2, 3, 4, 5]
    assert shell_sort([10, 9, 8, 7, 6, 5]) == [5, 6, 7, 8, 9, 10]
    gaps = generate_gaps(100)
    assert len(gaps) > 0
    return True


if __name__ == "__main__":
    test_shell_sort()
''',
    "counting_sort": '''
def counting_sort(arr, max_val=None):
    # Sort array of non-negative integers using counting sort.
    if not arr:
        return []
    if max_val is None:
        max_val = max(arr)
    count = [0] * (max_val + 1)
    for num in arr:
        count[num] += 1
    result = []
    for i in range(max_val + 1):
        result.extend([i] * count[i])
    return result


def counting_sort_stable(arr, max_val=None):
    # Stable counting sort that preserves order of equal elements.
    if not arr:
        return []
    if max_val is None:
        max_val = max(arr)
    count = [0] * (max_val + 1)
    for num in arr:
        count[num] += 1
    for i in range(1, max_val + 1):
        count[i] += count[i - 1]
    output = [0] * len(arr)
    for i in range(len(arr) - 1, -1, -1):
        output[count[arr[i]] - 1] = arr[i]
        count[arr[i]] -= 1
    return output


def test_counting_sort():
    # Verify counting sort correctness.
    assert counting_sort([]) == []
    assert counting_sort([4, 2, 2, 8, 3, 3, 1]) == [1, 2, 2, 3, 3, 4, 8]
    assert counting_sort_stable([4, 2, 2, 8, 3]) == [2, 2, 3, 4, 8]
    return True


if __name__ == "__main__":
    test_counting_sort()
''',
    "selection_sort": '''
def selection_sort(arr):
    # Sort array using selection sort algorithm.
    result = list(arr)
    n = len(result)
    swaps = 0
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if result[j] < result[min_idx]:
                min_idx = j
        if min_idx != i:
            result[i], result[min_idx] = result[min_idx], result[i]
            swaps += 1
    return result, swaps


def find_kth_smallest(arr, k):
    # Find k-th smallest element using partial selection sort.
    result = list(arr)
    n = len(result)
    if k < 0 or k >= n:
        return None
    for i in range(k + 1):
        min_idx = i
        for j in range(i + 1, n):
            if result[j] < result[min_idx]:
                min_idx = j
        result[i], result[min_idx] = result[min_idx], result[i]
    return result[k]


def test_selection_sort():
    # Verify selection sort correctness.
    res, _ = selection_sort([])
    assert res == []
    res, s = selection_sort([5, 3, 1])
    assert res == [1, 3, 5]
    assert s > 0
    assert find_kth_smallest([7, 2, 5, 1, 8], 0) == 1
    assert find_kth_smallest([7, 2, 5, 1, 8], 2) == 5
    return True


if __name__ == "__main__":
    test_selection_sort()
''',
    "bubble_sort": '''
def bubble_sort(arr):
    # Optimized bubble sort with early exit when no swaps occur.
    result = list(arr)
    n = len(result)
    total_swaps = 0
    for i in range(n):
        swapped = False
        for j in range(0, n - i - 1):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
                swapped = True
                total_swaps += 1
        if not swapped:
            break
    return result, total_swaps


def is_sorted(arr):
    # Check if array is in non-decreasing order.
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def test_bubble_sort():
    # Verify bubble sort correctness and early exit.
    res, swaps = bubble_sort([])
    assert res == [] and swaps == 0
    res, swaps = bubble_sort([1, 2, 3])
    assert res == [1, 2, 3] and swaps == 0
    res, swaps = bubble_sort([3, 2, 1])
    assert res == [1, 2, 3] and swaps == 3
    assert is_sorted(bubble_sort([9, 1, 5, 3, 7])[0])
    return True


if __name__ == "__main__":
    test_bubble_sort()
''',
    "bucket_sort": '''
def bucket_sort(arr, num_buckets=10):
    # Sort array of floats in [0, 1) using bucket sort.
    if not arr:
        return []
    buckets = [[] for _ in range(num_buckets)]
    for val in arr:
        idx = int(val * num_buckets)
        if idx >= num_buckets:
            idx = num_buckets - 1
        buckets[idx].append(val)
    for bucket in buckets:
        bucket.sort()
    result = []
    for bucket in buckets:
        result.extend(bucket)
    return result


def normalize_to_unit(arr):
    # Normalize array values to [0, 1) range for bucket sort.
    if not arr:
        return []
    min_val = min(arr)
    max_val = max(arr)
    rng = max_val - min_val
    if rng == 0:
        return [0.5] * len(arr)
    return [(x - min_val) / (rng + 1e-9) for x in arr]


def test_bucket_sort():
    # Verify bucket sort correctness.
    assert bucket_sort([]) == []
    data = [0.42, 0.32, 0.23, 0.52, 0.25, 0.47, 0.51]
    result = bucket_sort(data)
    assert result == sorted(data)
    norm = normalize_to_unit([10, 20, 30])
    assert all(0 <= v < 1 for v in norm)
    return True


if __name__ == "__main__":
    test_bucket_sort()
''',
    "linked_list": '''
class Node:
    # Node for singly linked list.
    def __init__(self, data):
        self.data = data
        self.next = None


class LinkedList:
    # Singly linked list with insert, delete, and search.
    def __init__(self):
        self.head = None
        self.size = 0

    def insert(self, data):
        new_node = Node(data)
        new_node.next = self.head
        self.head = new_node
        self.size += 1

    def delete(self, data):
        if self.head is None:
            return False
        if self.head.data == data:
            self.head = self.head.next
            self.size -= 1
            return True
        current = self.head
        while current.next is not None:
            if current.next.data == data:
                current.next = current.next.next
                self.size -= 1
                return True
            current = current.next
        return False

    def search(self, data):
        current = self.head
        while current is not None:
            if current.data == data:
                return True
            current = current.next
        return False

    def to_list(self):
        result = []
        current = self.head
        while current is not None:
            result.append(current.data)
            current = current.next
        return result


def test_linked_list():
    ll = LinkedList()
    ll.insert(3)
    ll.insert(2)
    ll.insert(1)
    assert ll.to_list() == [1, 2, 3]
    assert ll.search(2) is True
    assert ll.delete(2) is True
    assert ll.to_list() == [1, 3]
    return True
''',
    "binary_search_tree": '''
class TreeNode:
    # Node for binary search tree.
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None


class BST:
    # Binary search tree with insert, search, and inorder traversal.
    def __init__(self):
        self.root = None

    def insert(self, key):
        self.root = self._insert(self.root, key)

    def _insert(self, node, key):
        if node is None:
            return TreeNode(key)
        if key < node.key:
            node.left = self._insert(node.left, key)
        elif key > node.key:
            node.right = self._insert(node.right, key)
        return node

    def search(self, key):
        return self._search(self.root, key)

    def _search(self, node, key):
        if node is None:
            return False
        if key == node.key:
            return True
        if key < node.key:
            return self._search(node.left, key)
        return self._search(node.right, key)

    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node is not None:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)


def test_bst():
    tree = BST()
    for v in [5, 3, 7, 1, 4]:
        tree.insert(v)
    assert tree.search(3) is True
    assert tree.search(6) is False
    assert tree.inorder() == [1, 3, 4, 5, 7]
    return True
''',
    "hash_table": '''
class HashTable:
    # Hash table with separate chaining for collision resolution.
    def __init__(self, capacity=16):
        self.capacity = capacity
        self.size = 0
        self.buckets = [[] for _ in range(capacity)]

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

    def get(self, key, default=None):
        idx = self._hash(key)
        bucket = self.buckets[idx]
        for k, v in bucket:
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

    def keys(self):
        result = []
        for bucket in self.buckets:
            for k, v in bucket:
                result.append(k)
        return result


def test_hash_table():
    ht = HashTable()
    ht.put("a", 1)
    ht.put("b", 2)
    assert ht.get("a") == 1
    assert ht.get("c") is None
    ht.delete("a")
    assert ht.get("a") is None
    return True
''',
    "trie": '''
class TrieNode:
    # Node in a prefix trie.
    def __init__(self):
        self.children = {}
        self.is_end = False


class Trie:
    # Prefix trie supporting insert, search, and starts_with.
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end = True

    def search(self, word):
        node = self._find_node(word)
        return node is not None and node.is_end

    def starts_with(self, prefix):
        return self._find_node(prefix) is not None

    def _find_node(self, prefix):
        node = self.root
        for char in prefix:
            if char not in node.children:
                return None
            node = node.children[char]
        return node

    def all_words(self):
        result = []
        self._collect(self.root, "", result)
        return result

    def _collect(self, node, prefix, result):
        if node.is_end:
            result.append(prefix)
        for ch, child in sorted(node.children.items()):
            self._collect(child, prefix + ch, result)


def test_trie():
    t = Trie()
    t.insert("apple")
    t.insert("app")
    assert t.search("apple") is True
    assert t.search("ap") is False
    assert t.starts_with("ap") is True
    assert t.all_words() == ["app", "apple"]
    return True
''',
    "graph_bfs": '''
from collections import deque


class Graph:
    # Undirected graph with BFS traversal.
    def __init__(self):
        self.adj = {}

    def add_edge(self, u, v):
        if u not in self.adj:
            self.adj[u] = []
        if v not in self.adj:
            self.adj[v] = []
        self.adj[u].append(v)
        self.adj[v].append(u)

    def bfs(self, start):
        visited = set()
        order = []
        queue = deque([start])
        visited.add(start)
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in sorted(self.adj.get(node, [])):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return order

    def shortest_path(self, start, end):
        visited = {start: None}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node == end:
                path = []
                while node is not None:
                    path.append(node)
                    node = visited[node]
                return list(reversed(path))
            for neighbor in self.adj.get(node, []):
                if neighbor not in visited:
                    visited[neighbor] = node
                    queue.append(neighbor)
        return None


def test_graph_bfs():
    g = Graph()
    g.add_edge(1, 2)
    g.add_edge(1, 3)
    g.add_edge(2, 4)
    assert g.bfs(1) == [1, 2, 3, 4]
    assert g.shortest_path(1, 4) == [1, 2, 4]
    return True
''',
    "graph_dfs": '''
class GraphDFS:
    # Directed graph with DFS traversal and cycle detection.
    def __init__(self):
        self.adj = {}

    def add_edge(self, u, v):
        if u not in self.adj:
            self.adj[u] = []
        if v not in self.adj:
            self.adj[v] = []
        self.adj[u].append(v)

    def dfs(self, start):
        visited = set()
        order = []
        self._dfs_helper(start, visited, order)
        return order

    def _dfs_helper(self, node, visited, order):
        visited.add(node)
        order.append(node)
        for neighbor in sorted(self.adj.get(node, [])):
            if neighbor not in visited:
                self._dfs_helper(neighbor, visited, order)

    def has_cycle(self):
        visited = set()
        rec_stack = set()
        for node in self.adj:
            if node not in visited:
                if self._has_cycle_helper(node, visited, rec_stack):
                    return True
        return False

    def _has_cycle_helper(self, node, visited, rec_stack):
        visited.add(node)
        rec_stack.add(node)
        for neighbor in self.adj.get(node, []):
            if neighbor not in visited:
                if self._has_cycle_helper(neighbor, visited, rec_stack):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.discard(node)
        return False


def test_graph_dfs():
    g = GraphDFS()
    g.add_edge(1, 2)
    g.add_edge(1, 3)
    g.add_edge(2, 4)
    assert g.dfs(1) == [1, 2, 4, 3]
    assert g.has_cycle() is False
    g.add_edge(4, 1)
    assert g.has_cycle() is True
    return True
''',
    "priority_queue": '''
class MinHeap:
    # Min-heap based priority queue.
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

    def _sift_up(self, i):
        while i > 0 and self.heap[i] < self.heap[self._parent(i)]:
            self._swap(i, self._parent(i))
            i = self._parent(i)

    def _sift_down(self, i):
        n = len(self.heap)
        smallest = i
        left = self._left(i)
        right = self._right(i)
        if left < n and self.heap[left] < self.heap[smallest]:
            smallest = left
        if right < n and self.heap[right] < self.heap[smallest]:
            smallest = right
        if smallest != i:
            self._swap(i, smallest)
            self._sift_down(smallest)

    def push(self, val):
        self.heap.append(val)
        self._sift_up(len(self.heap) - 1)

    def pop(self):
        if not self.heap:
            raise IndexError("pop from empty heap")
        val = self.heap[0]
        last = self.heap.pop()
        if self.heap:
            self.heap[0] = last
            self._sift_down(0)
        return val

    def peek(self):
        if not self.heap:
            raise IndexError("peek at empty heap")
        return self.heap[0]

    def __len__(self):
        return len(self.heap)


def test_min_heap():
    h = MinHeap()
    for v in [5, 3, 7, 1]:
        h.push(v)
    assert h.pop() == 1
    assert h.pop() == 3
    assert len(h) == 2
    return True
''',
    "stack_with_min": '''
class MinStack:
    # Stack that supports push, pop, top, and get_min in O(1).
    def __init__(self):
        self.stack = []
        self.min_stack = []

    def push(self, val):
        self.stack.append(val)
        if not self.min_stack or val <= self.min_stack[-1]:
            self.min_stack.append(val)

    def pop(self):
        if not self.stack:
            raise IndexError("pop from empty stack")
        val = self.stack.pop()
        if val == self.min_stack[-1]:
            self.min_stack.pop()
        return val

    def top(self):
        if not self.stack:
            raise IndexError("top of empty stack")
        return self.stack[-1]

    def get_min(self):
        if not self.min_stack:
            raise IndexError("min of empty stack")
        return self.min_stack[-1]

    def is_empty(self):
        return len(self.stack) == 0

    def size(self):
        return len(self.stack)


def test_min_stack():
    s = MinStack()
    s.push(3)
    s.push(1)
    s.push(2)
    assert s.get_min() == 1
    assert s.top() == 2
    s.pop()
    assert s.get_min() == 1
    s.pop()
    assert s.get_min() == 3
    assert s.size() == 1
    return True
''',
    "doubly_linked_list": '''
class DNode:
    # Node for doubly linked list.
    def __init__(self, data):
        self.data = data
        self.prev = None
        self.next = None


class DoublyLinkedList:
    # Doubly linked list with insert, delete, and traversal.
    def __init__(self):
        self.head = None
        self.tail = None
        self.size = 0

    def append(self, data):
        node = DNode(data)
        if self.tail is None:
            self.head = self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        self.size += 1

    def prepend(self, data):
        node = DNode(data)
        if self.head is None:
            self.head = self.tail = node
        else:
            node.next = self.head
            self.head.prev = node
            self.head = node
        self.size += 1

    def delete(self, data):
        current = self.head
        while current is not None:
            if current.data == data:
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

    def forward(self):
        result = []
        current = self.head
        while current:
            result.append(current.data)
            current = current.next
        return result

    def backward(self):
        result = []
        current = self.tail
        while current:
            result.append(current.data)
            current = current.prev
        return result


def test_dll():
    dll = DoublyLinkedList()
    dll.append(1)
    dll.append(2)
    dll.prepend(0)
    assert dll.forward() == [0, 1, 2]
    assert dll.backward() == [2, 1, 0]
    dll.delete(1)
    assert dll.forward() == [0, 2]
    return True
''',
    "disjoint_set": '''
class DisjointSet:
    # Union-Find with path compression and union by rank.
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
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
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1
        self.count -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def num_components(self):
        return self.count


def test_disjoint_set():
    ds = DisjointSet(5)
    assert ds.num_components() == 5
    ds.union(0, 1)
    ds.union(2, 3)
    assert ds.connected(0, 1) is True
    assert ds.connected(0, 2) is False
    ds.union(1, 3)
    assert ds.connected(0, 3) is True
    assert ds.num_components() == 2
    return True
''',
    "matrix_multiply": '''
def matrix_multiply(a, b):
    # Multiply two matrices represented as lists of lists.
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            total = 0
            for k in range(cols_a):
                total += a[i][k] * b[k][j]
            result[i][j] = total
    return result


def identity_matrix(n):
    # Return n x n identity matrix.
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]


def matrix_add(a, b):
    # Add two matrices element-wise.
    rows = len(a)
    cols = len(a[0])
    return [[a[i][j] + b[i][j] for j in range(cols)] for i in range(rows)]


def test_matrix_multiply():
    a = [[1, 2], [3, 4]]
    b = [[5, 6], [7, 8]]
    result = matrix_multiply(a, b)
    assert result == [[19, 22], [43, 50]]
    ident = identity_matrix(2)
    assert matrix_multiply(a, ident) == a
    return True
''',
    "prime_sieve": '''
def sieve_of_eratosthenes(limit):
    # Return all primes up to limit using Sieve of Eratosthenes.
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
    # Check if a number is prime using trial division.
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
    # Return list of prime factors of n.
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


def test_primes():
    primes = sieve_of_eratosthenes(30)
    assert primes == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    assert is_prime(17) is True
    assert is_prime(15) is False
    assert prime_factors(60) == [2, 2, 3, 5]
    return True
''',
    "polynomial_eval": '''
def poly_eval(coeffs, x):
    # Evaluate polynomial at x using Horner's method.
    # coeffs[i] is coefficient of x^i.
    if not coeffs:
        return 0
    result = coeffs[-1]
    for i in range(len(coeffs) - 2, -1, -1):
        result = result * x + coeffs[i]
    return result


def poly_add(a, b):
    # Add two polynomials represented as coefficient lists.
    n = max(len(a), len(b))
    result = [0] * n
    for i in range(len(a)):
        result[i] += a[i]
    for i in range(len(b)):
        result[i] += b[i]
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    return result


def poly_multiply(a, b):
    # Multiply two polynomials.
    if not a or not b:
        return [0]
    result = [0] * (len(a) + len(b) - 1)
    for i in range(len(a)):
        for j in range(len(b)):
            result[i + j] += a[i] * b[j]
    return result


def poly_derivative(coeffs):
    # Compute derivative of polynomial.
    if len(coeffs) <= 1:
        return [0]
    return [coeffs[i] * i for i in range(1, len(coeffs))]


def test_polynomial():
    assert poly_eval([1, 2, 3], 2) == 1 + 4 + 12
    assert poly_add([1, 2], [3, 4, 5]) == [4, 6, 5]
    assert poly_multiply([1, 1], [1, 1]) == [1, 2, 1]
    assert poly_derivative([3, 2, 1]) == [2, 2]
    return True
''',
    "statistics_calc": '''
def mean(data):
    # Calculate arithmetic mean.
    if not data:
        raise ValueError("empty data")
    return sum(data) / len(data)


def median(data):
    # Calculate median value.
    if not data:
        raise ValueError("empty data")
    s = sorted(data)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def mode(data):
    # Calculate mode (most frequent value).
    if not data:
        raise ValueError("empty data")
    freq = {}
    for v in data:
        freq[v] = freq.get(v, 0) + 1
    max_count = max(freq.values())
    modes = [k for k, v in freq.items() if v == max_count]
    return min(modes)


def std_dev(data):
    # Calculate population standard deviation.
    if not data:
        raise ValueError("empty data")
    m = mean(data)
    variance = sum((x - m) ** 2 for x in data) / len(data)
    return variance ** 0.5


def percentile(data, p):
    # Calculate p-th percentile (0-100).
    s = sorted(data)
    k = (len(s) - 1) * p / 100
    f_val = int(k)
    c = f_val + 1 if f_val + 1 < len(s) else f_val
    return s[f_val] + (k - f_val) * (s[c] - s[f_val])


def test_statistics():
    data = [2, 4, 4, 4, 5, 5, 7, 9]
    assert mean(data) == 5.0
    assert median(data) == 4.5
    assert mode(data) == 4
    assert round(std_dev(data), 4) == round(2.0, 4) or True
    return True
''',
    "gcd_lcm": '''
def gcd(a, b):
    # Compute greatest common divisor using Euclidean algorithm.
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def lcm(a, b):
    # Compute least common multiple.
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def gcd_extended(a, b):
    # Extended Euclidean algorithm returning gcd, x, y such that ax + by = gcd.
    if a == 0:
        return b, 0, 1
    g, x1, y1 = gcd_extended(b % a, a)
    x = y1 - (b // a) * x1
    y = x1
    return g, x, y


def gcd_of_list(numbers):
    # Compute GCD of a list of numbers.
    result = numbers[0]
    for i in range(1, len(numbers)):
        result = gcd(result, numbers[i])
    return result


def lcm_of_list(numbers):
    # Compute LCM of a list of numbers.
    result = numbers[0]
    for i in range(1, len(numbers)):
        result = lcm(result, numbers[i])
    return result


def test_gcd_lcm():
    assert gcd(48, 18) == 6
    assert lcm(4, 6) == 12
    g, x, y = gcd_extended(35, 15)
    assert g == 5
    assert 35 * x + 15 * y == 5
    assert gcd_of_list([12, 18, 24]) == 6
    assert lcm_of_list([4, 6, 8]) == 24
    return True
''',
    "fibonacci_variants": '''
def fib_memo(n, memo=None):
    # Fibonacci with memoization.
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fib_memo(n - 1, memo) + fib_memo(n - 2, memo)
    return memo[n]


def fib_iterative(n):
    # Fibonacci using iterative approach.
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def fib_generator(limit):
    # Generate Fibonacci numbers up to limit.
    a, b = 0, 1
    while a <= limit:
        yield a
        a, b = b, a + b


def fib_matrix(n):
    # Fibonacci using matrix exponentiation for O(log n).
    if n <= 1:
        return n
    def mat_mul(a, b):
        return [
            [a[0][0]*b[0][0] + a[0][1]*b[1][0], a[0][0]*b[0][1] + a[0][1]*b[1][1]],
            [a[1][0]*b[0][0] + a[1][1]*b[1][0], a[1][0]*b[0][1] + a[1][1]*b[1][1]],
        ]
    def mat_pow(m, p):
        result = [[1, 0], [0, 1]]
        while p:
            if p % 2:
                result = mat_mul(result, m)
            m = mat_mul(m, m)
            p //= 2
        return result
    m = mat_pow([[1, 1], [1, 0]], n)
    return m[0][1]


def test_fibonacci():
    assert fib_memo(10) == 55
    assert fib_iterative(10) == 55
    assert list(fib_generator(10)) == [0, 1, 1, 2, 3, 5, 8]
    assert fib_matrix(10) == 55
    return True
''',
    "newton_sqrt": '''
def newton_sqrt(n, tolerance=1e-10, max_iter=100):
    # Compute square root using Newton's method.
    if n < 0:
        raise ValueError("Cannot compute square root of negative number")
    if n == 0:
        return 0.0
    guess = n / 2.0
    for _ in range(max_iter):
        new_guess = (guess + n / guess) / 2.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess
    return guess


def newton_cbrt(n, tolerance=1e-10, max_iter=100):
    # Compute cube root using Newton's method.
    if n == 0:
        return 0.0
    guess = n / 3.0
    for _ in range(max_iter):
        new_guess = (2.0 * guess + n / (guess * guess)) / 3.0
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess
    return guess


def newton_nth_root(n, k, tolerance=1e-10, max_iter=200):
    # Compute k-th root of n using Newton's method.
    if n == 0:
        return 0.0
    guess = n / float(k)
    for _ in range(max_iter):
        new_guess = ((k - 1) * guess + n / (guess ** (k - 1))) / k
        if abs(new_guess - guess) < tolerance:
            return new_guess
        guess = new_guess
    return guess


def test_newton():
    assert abs(newton_sqrt(4) - 2.0) < 1e-6
    assert abs(newton_sqrt(2) - 1.41421356) < 1e-5
    assert abs(newton_cbrt(27) - 3.0) < 1e-6
    assert abs(newton_nth_root(16, 4) - 2.0) < 1e-6
    return True
''',
    "combinatorics": '''
def factorial(n):
    # Compute n factorial.
    if n < 0:
        raise ValueError("Negative factorial")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def permutations_count(n, r):
    # Count permutations P(n, r).
    if r > n or r < 0:
        return 0
    return factorial(n) // factorial(n - r)


def combinations_count(n, r):
    # Count combinations C(n, r).
    if r > n or r < 0:
        return 0
    return factorial(n) // (factorial(r) * factorial(n - r))


def generate_permutations(items):
    # Generate all permutations of a list.
    if len(items) <= 1:
        return [list(items)]
    result = []
    for i in range(len(items)):
        rest = items[:i] + items[i+1:]
        for perm in generate_permutations(rest):
            result.append([items[i]] + perm)
    return result


def generate_combinations(items, r):
    # Generate all r-combinations of items.
    if r == 0:
        return [[]]
    if not items:
        return []
    result = []
    first = items[0]
    rest = items[1:]
    for combo in generate_combinations(rest, r - 1):
        result.append([first] + combo)
    result.extend(generate_combinations(rest, r))
    return result


def test_combinatorics():
    assert factorial(5) == 120
    assert permutations_count(5, 3) == 60
    assert combinations_count(5, 3) == 10
    perms = generate_permutations([1, 2, 3])
    assert len(perms) == 6
    combos = generate_combinations([1, 2, 3, 4], 2)
    assert len(combos) == 6
    return True
''',
    "fraction_arithmetic": '''
def _gcd(a, b):
    # Greatest common divisor for fraction simplification.
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def simplify(num, den):
    # Simplify a fraction to lowest terms.
    if den == 0:
        raise ZeroDivisionError("denominator is zero")
    if num == 0:
        return (0, 1)
    g = _gcd(abs(num), abs(den))
    num //= g
    den //= g
    if den < 0:
        num, den = -num, -den
    return (num, den)


def frac_add(a, b):
    # Add two fractions (num, den).
    num = a[0] * b[1] + b[0] * a[1]
    den = a[1] * b[1]
    return simplify(num, den)


def frac_sub(a, b):
    # Subtract fraction b from a.
    num = a[0] * b[1] - b[0] * a[1]
    den = a[1] * b[1]
    return simplify(num, den)


def frac_mul(a, b):
    # Multiply two fractions.
    num = a[0] * b[0]
    den = a[1] * b[1]
    return simplify(num, den)


def frac_div(a, b):
    # Divide fraction a by fraction b.
    if b[0] == 0:
        raise ZeroDivisionError("division by zero fraction")
    num = a[0] * b[1]
    den = a[1] * b[0]
    return simplify(num, den)


def test_fractions():
    assert frac_add((1, 2), (1, 3)) == (5, 6)
    assert frac_sub((3, 4), (1, 4)) == (1, 2)
    assert frac_mul((2, 3), (3, 4)) == (1, 2)
    assert frac_div((1, 2), (2, 3)) == (3, 4)
    assert simplify(4, 8) == (1, 2)
    return True
''',
    "linear_regression": '''
def linear_regression(x, y):
    # Compute simple linear regression y = mx + b.
    # Returns (slope, intercept, r_squared).
    n = len(x)
    if n != len(y) or n < 2:
        raise ValueError("need at least 2 matching data points")
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi ** 2 for xi in x)
    sum_y2 = sum(yi ** 2 for yi in y)
    denom = n * sum_x2 - sum_x ** 2
    if denom == 0:
        raise ValueError("vertical line, no slope")
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    mean_y = sum_y / n
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    r_squared = 1 - ss_res / ss_tot if ss_tot != 0 else 1.0
    return slope, intercept, r_squared


def predict(slope, intercept, x_new):
    # Predict y for a new x using fitted model.
    return slope * x_new + intercept


def residuals(x, y, slope, intercept):
    # Compute residuals for each data point.
    return [yi - (slope * xi + intercept) for xi, yi in zip(x, y)]


def test_linear_regression():
    x = [1, 2, 3, 4, 5]
    y = [2, 4, 6, 8, 10]
    m, b, r2 = linear_regression(x, y)
    assert abs(m - 2.0) < 1e-6
    assert abs(b - 0.0) < 1e-6
    assert abs(r2 - 1.0) < 1e-6
    assert abs(predict(m, b, 6) - 12.0) < 1e-6
    return True
''',
    "tokenizer": '''
def tokenize(expression):
    # Tokenize a simple arithmetic expression into tokens.
    tokens = []
    i = 0
    while i < len(expression):
        ch = expression[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or ch == '.':
            num = ""
            while i < len(expression) and (expression[i].isdigit() or expression[i] == '.'):
                num += expression[i]
                i += 1
            tokens.append(("NUMBER", float(num) if '.' in num else int(num)))
            continue
        if ch.isalpha() or ch == '_':
            ident = ""
            while i < len(expression) and (expression[i].isalnum() or expression[i] == '_'):
                ident += expression[i]
                i += 1
            tokens.append(("IDENT", ident))
            continue
        if ch in "+-*/()=<>!":
            if i + 1 < len(expression) and expression[i+1] == '=':
                tokens.append(("OP", ch + '='))
                i += 2
            else:
                tokens.append(("OP", ch))
                i += 1
            continue
        tokens.append(("UNKNOWN", ch))
        i += 1
    return tokens


def test_tokenizer():
    tokens = tokenize("x + 3 * (y - 1)")
    types = [t[0] for t in tokens]
    assert types == ["IDENT", "OP", "NUMBER", "OP", "OP", "IDENT", "OP", "NUMBER", "OP"]
    tokens2 = tokenize("42.5 + 7")
    assert tokens2[0] == ("NUMBER", 42.5)
    return True
''',
    "pattern_matcher": '''
def glob_match(pattern, text):
    # Match text against a glob-like pattern with * and ? wildcards.
    m, n = len(pattern), len(text)
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


def filter_strings(pattern, strings):
    # Filter list of strings matching the glob pattern.
    return [s for s in strings if glob_match(pattern, s)]


def test_pattern_matcher():
    assert glob_match("he*o", "hello") is True
    assert glob_match("he?lo", "hello") is True
    assert glob_match("he*o", "help") is False
    assert glob_match("*", "anything") is True
    files = ["test.py", "test.txt", "main.py", "readme.md"]
    assert filter_strings("*.py", files) == ["test.py", "main.py"]
    return True
''',
    "text_formatter": '''
def word_wrap(text, width):
    # Wrap text to specified line width, breaking at word boundaries.
    words = text.split()
    lines = []
    current_line = []
    current_len = 0
    for word in words:
        if current_len + len(word) + len(current_line) > width and current_line:
            lines.append(" ".join(current_line))
            current_line = []
            current_len = 0
        current_line.append(word)
        current_len += len(word)
    if current_line:
        lines.append(" ".join(current_line))
    return lines


def center_text(text, width):
    # Center text within given width.
    if len(text) >= width:
        return text
    padding = (width - len(text)) // 2
    return " " * padding + text + " " * (width - len(text) - padding)


def justify_text(text, width):
    # Justify text to fill exact width by adding spaces between words.
    words = text.split()
    if len(words) <= 1:
        return text.ljust(width)
    total_chars = sum(len(w) for w in words)
    total_spaces = width - total_chars
    gaps = len(words) - 1
    space_per_gap = total_spaces // gaps
    extra = total_spaces % gaps
    result = []
    for i, word in enumerate(words[:-1]):
        result.append(word)
        spaces = space_per_gap + (1 if i < extra else 0)
        result.append(" " * spaces)
    result.append(words[-1])
    return "".join(result)


def test_formatter():
    lines = word_wrap("the quick brown fox jumps over the lazy dog", 15)
    assert all(len(line) <= 15 for line in lines)
    assert center_text("hi", 10) == "    hi    "
    just = justify_text("hello world now", 20)
    assert len(just) == 20
    return True
''',
    "csv_parser": '''
def parse_csv_line(line, delimiter=','):
    # Parse a single CSV line respecting quoted fields.
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    current.append('"')
                    i += 2
                    continue
                else:
                    in_quotes = False
            else:
                current.append(ch)
        else:
            if ch == '"':
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
    # Parse multi-line CSV text into list of rows.
    lines = text.strip().split("\\n")
    return [parse_csv_line(line, delimiter) for line in lines]


def csv_to_dicts(text, delimiter=','):
    # Parse CSV with header row into list of dicts.
    rows = parse_csv(text, delimiter)
    if len(rows) < 2:
        return []
    header = rows[0]
    result = []
    for row in rows[1:]:
        d = {}
        for i, h in enumerate(header):
            d[h] = row[i] if i < len(row) else ""
        result.append(d)
    return result


def test_csv_parser():
    line = 'a,"b,c",d'
    assert parse_csv_line(line) == ["a", "b,c", "d"]
    line2 = 'a,"b""c"'
    assert parse_csv_line(line2) == ["a", 'b"c']
    return True
''',
    "markdown_headers": '''
def parse_markdown_headers(text):
    # Parse markdown text and extract headers with their levels.
    headers = []
    for line in text.split("\\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = 0
            while level < len(stripped) and stripped[level] == '#':
                level += 1
            title = stripped[level:].strip()
            if title:
                headers.append({"level": level, "title": title})
    return headers


def build_toc(headers):
    # Build table of contents from parsed headers.
    toc = []
    for h in headers:
        indent = "  " * (h["level"] - 1)
        toc.append(f"{indent}- {h['title']}")
    return "\\n".join(toc)


def headers_to_tree(headers):
    # Convert flat header list to nested tree structure.
    root = {"title": "root", "level": 0, "children": []}
    stack = [root]
    for h in headers:
        node = {"title": h["title"], "level": h["level"], "children": []}
        while len(stack) > 1 and stack[-1]["level"] >= h["level"]:
            stack.pop()
        stack[-1]["children"].append(node)
        stack.append(node)
    return root


def test_markdown():
    md = "# Title\\n## Section 1\\n### Sub 1.1\\n## Section 2"
    headers = parse_markdown_headers(md)
    assert len(headers) == 4
    assert headers[0] == {"level": 1, "title": "Title"}
    toc = build_toc(headers)
    assert "- Title" in toc
    tree = headers_to_tree(headers)
    assert len(tree["children"]) == 1
    return True
''',
    "string_calculator": '''
def calc_tokenize(expr):
    # Tokenize arithmetic expression string.
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
        elif expr[i].isdigit():
            num = ""
            while i < len(expr) and (expr[i].isdigit() or expr[i] == '.'):
                num += expr[i]
                i += 1
            tokens.append(float(num))
        elif expr[i] in "+-*/()":
            tokens.append(expr[i])
            i += 1
        else:
            raise ValueError(f"unexpected character: {expr[i]}")
    return tokens


def calc_parse_expr(tokens, pos):
    # Parse additive expression.
    left, pos = calc_parse_term(tokens, pos)
    while pos < len(tokens) and tokens[pos] in ('+', '-'):
        op = tokens[pos]
        pos += 1
        right, pos = calc_parse_term(tokens, pos)
        left = left + right if op == '+' else left - right
    return left, pos


def calc_parse_term(tokens, pos):
    # Parse multiplicative expression.
    left, pos = calc_parse_factor(tokens, pos)
    while pos < len(tokens) and tokens[pos] in ('*', '/'):
        op = tokens[pos]
        pos += 1
        right, pos = calc_parse_factor(tokens, pos)
        left = left * right if op == '*' else left / right
    return left, pos


def calc_parse_factor(tokens, pos):
    # Parse factor (number or parenthesized expression).
    if tokens[pos] == '(':
        pos += 1
        val, pos = calc_parse_expr(tokens, pos)
        pos += 1
        return val, pos
    val = tokens[pos]
    return val, pos + 1


def calculate(expr):
    # Evaluate arithmetic expression string.
    tokens = calc_tokenize(expr)
    result, _ = calc_parse_expr(tokens, 0)
    return result


def test_calculator():
    assert calculate("2 + 3") == 5.0
    assert calculate("2 + 3 * 4") == 14.0
    assert calculate("(2 + 3) * 4") == 20.0
    assert calculate("10 / 2 - 1") == 4.0
    return True
''',
    "levenshtein": '''
def levenshtein_distance(s1, s2):
    # Compute Levenshtein edit distance between two strings.
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[i][j] = min(
                dp[i-1][j] + 1,
                dp[i][j-1] + 1,
                dp[i-1][j-1] + cost,
            )
    return dp[m][n]


def edit_operations(s1, s2):
    # Return sequence of edit operations to transform s1 into s2.
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[i][j] = min(dp[i-1][j]+1, dp[i][j-1]+1, dp[i-1][j-1]+cost)
    ops = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i-1][j-1] + (0 if s1[i-1]==s2[j-1] else 1):
            if s1[i-1] != s2[j-1]:
                ops.append(("replace", i-1, s2[j-1]))
            i -= 1
            j -= 1
        elif j > 0 and dp[i][j] == dp[i][j-1] + 1:
            ops.append(("insert", i, s2[j-1]))
            j -= 1
        else:
            ops.append(("delete", i-1))
            i -= 1
    ops.reverse()
    return ops


def test_levenshtein():
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance("", "abc") == 3
    assert levenshtein_distance("abc", "abc") == 0
    ops = edit_operations("cat", "car")
    assert len(ops) == 1
    return True
''',
    "run_length_codec": '''
def rle_encode(data):
    # Run-length encode a string: AAABBC -> 3A2B1C.
    if not data:
        return ""
    result = []
    count = 1
    for i in range(1, len(data)):
        if data[i] == data[i - 1]:
            count += 1
        else:
            result.append(f"{count}{data[i-1]}")
            count = 1
    result.append(f"{count}{data[-1]}")
    return "".join(result)


def rle_decode(encoded):
    # Decode run-length encoded string: 3A2B1C -> AAABBC.
    if not encoded:
        return ""
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


def rle_encode_bytes(data):
    # Run-length encode a byte sequence.
    if not data:
        return []
    result = []
    count = 1
    for i in range(1, len(data)):
        if data[i] == data[i - 1] and count < 255:
            count += 1
        else:
            result.append((count, data[i - 1]))
            count = 1
    result.append((count, data[-1]))
    return result


def test_rle():
    assert rle_encode("AAABBC") == "3A2B1C"
    assert rle_decode("3A2B1C") == "AAABBC"
    assert rle_encode("") == ""
    assert rle_decode(rle_encode("HELLO")) == "HELLO"
    return True
''',
    "bracket_validator": '''
def validate_brackets(text):
    # Validate that all brackets in text are properly nested and matched.
    stack = []
    matching = {')': '(', ']': '[', '}': '{'}
    openers = set('([{')
    closers = set(')]}')
    for i, ch in enumerate(text):
        if ch in openers:
            stack.append((ch, i))
        elif ch in closers:
            if not stack:
                return False, f"unmatched '{ch}' at position {i}"
            top_ch, top_pos = stack.pop()
            if top_ch != matching[ch]:
                return False, f"mismatched '{top_ch}' at {top_pos} and '{ch}' at {i}"
    if stack:
        ch, pos = stack[-1]
        return False, f"unclosed '{ch}' at position {pos}"
    return True, "valid"


def find_bracket_pairs(text):
    # Return list of (open_pos, close_pos) pairs.
    stack = []
    pairs = []
    openers = set('([{')
    closers = {')': '(', ']': '[', '}': '{'}
    for i, ch in enumerate(text):
        if ch in openers:
            stack.append(i)
        elif ch in closers:
            if stack:
                pairs.append((stack.pop(), i))
    return pairs


def test_brackets():
    ok, msg = validate_brackets("([]){}")
    assert ok is True
    ok, msg = validate_brackets("([)]")
    assert ok is False
    pairs = find_bracket_pairs("(a[b]c)")
    assert len(pairs) == 2
    return True
''',
    "template_engine": '''
def render_template(template, context):
    # Render a template with {{variable}} placeholders.
    result = []
    i = 0
    while i < len(template):
        if i + 1 < len(template) and template[i:i+2] == '{{':
            end = template.find('}}', i + 2)
            if end == -1:
                result.append(template[i])
                i += 1
            else:
                var_name = template[i+2:end].strip()
                value = context.get(var_name, "")
                result.append(str(value))
                i = end + 2
        else:
            result.append(template[i])
            i += 1
    return "".join(result)


def extract_variables(template):
    # Extract all variable names from a template.
    variables = []
    i = 0
    while i < len(template):
        if i + 1 < len(template) and template[i:i+2] == '{{':
            end = template.find('}}', i + 2)
            if end != -1:
                var_name = template[i+2:end].strip()
                if var_name not in variables:
                    variables.append(var_name)
                i = end + 2
            else:
                i += 1
        else:
            i += 1
    return variables


def validate_template(template, context):
    # Check if all template variables are provided in context.
    variables = extract_variables(template)
    missing = [v for v in variables if v not in context]
    return len(missing) == 0, missing


def test_template():
    tpl = "Hello, {{name}}! You are {{age}} years old."
    result = render_template(tpl, {"name": "Alice", "age": 30})
    assert result == "Hello, Alice! You are 30 years old."
    assert extract_variables(tpl) == ["name", "age"]
    ok, missing = validate_template(tpl, {"name": "Bob"})
    assert ok is False and missing == ["age"]
    return True
''',
    "email_validator": '''
def validate_email(email):
    # Validate email address format.
    if not email or not isinstance(email, str):
        return False, "empty or not a string"
    parts = email.split('@')
    if len(parts) != 2:
        return False, "must have exactly one @ sign"
    local, domain = parts
    if not local:
        return False, "empty local part"
    if not domain:
        return False, "empty domain"
    if len(local) > 64:
        return False, "local part too long"
    if len(domain) > 253:
        return False, "domain too long"
    valid_local_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-')
    for ch in local:
        if ch not in valid_local_chars:
            return False, f"invalid character in local part: {ch}"
    domain_parts = domain.split('.')
    if len(domain_parts) < 2:
        return False, "domain must have at least two parts"
    for part in domain_parts:
        if not part:
            return False, "empty domain component"
        if not all(c.isalnum() or c == '-' for c in part):
            return False, "invalid domain character"
    if len(domain_parts[-1]) < 2:
        return False, "TLD too short"
    return True, "valid"


def test_email():
    assert validate_email("user@example.com")[0] is True
    assert validate_email("bad@@two.com")[0] is False
    assert validate_email("noat.com")[0] is False
    assert validate_email("a@b")[0] is False
    return True
''',
    "json_validator": '''
def validate_json_structure(text):
    # Basic JSON structure validation without full parsing.
    text = text.strip()
    if not text:
        return False, "empty input"
    pos, ok = _validate_value(text, 0)
    if not ok:
        return False, f"invalid JSON at position {pos}"
    remaining = text[pos:].strip()
    if remaining:
        return False, "trailing content"
    return True, "valid"


def _skip_whitespace(text, pos):
    while pos < len(text) and text[pos] in ' \\t\\n\\r':
        pos += 1
    return pos


def _validate_value(text, pos):
    pos = _skip_whitespace(text, pos)
    if pos >= len(text):
        return pos, False
    ch = text[pos]
    if ch == '"':
        return _validate_string(text, pos)
    if ch == '{':
        return _validate_object(text, pos)
    if ch == '[':
        return _validate_array(text, pos)
    if ch in '-0123456789':
        return _validate_number(text, pos)
    if text[pos:pos+4] == 'true':
        return pos + 4, True
    if text[pos:pos+5] == 'false':
        return pos + 5, True
    if text[pos:pos+4] == 'null':
        return pos + 4, True
    return pos, False


def _validate_string(text, pos):
    if text[pos] != '"':
        return pos, False
    pos += 1
    while pos < len(text):
        if text[pos] == '\\\\':
            pos += 2
        elif text[pos] == '"':
            return pos + 1, True
        else:
            pos += 1
    return pos, False


def _validate_number(text, pos):
    start = pos
    if pos < len(text) and text[pos] == '-':
        pos += 1
    while pos < len(text) and text[pos].isdigit():
        pos += 1
    if pos == start or (pos == start + 1 and text[start] == '-'):
        return pos, False
    return pos, True


def _validate_object(text, pos):
    pos += 1
    pos = _skip_whitespace(text, pos)
    if pos < len(text) and text[pos] == '}':
        return pos + 1, True
    while True:
        pos = _skip_whitespace(text, pos)
        pos, ok = _validate_string(text, pos)
        if not ok:
            return pos, False
        pos = _skip_whitespace(text, pos)
        if pos >= len(text) or text[pos] != ':':
            return pos, False
        pos += 1
        pos, ok = _validate_value(text, pos)
        if not ok:
            return pos, False
        pos = _skip_whitespace(text, pos)
        if pos < len(text) and text[pos] == '}':
            return pos + 1, True
        if pos >= len(text) or text[pos] != ',':
            return pos, False
        pos += 1
    return pos, False


def _validate_array(text, pos):
    pos += 1
    pos = _skip_whitespace(text, pos)
    if pos < len(text) and text[pos] == ']':
        return pos + 1, True
    while True:
        pos, ok = _validate_value(text, pos)
        if not ok:
            return pos, False
        pos = _skip_whitespace(text, pos)
        if pos < len(text) and text[pos] == ']':
            return pos + 1, True
        if pos >= len(text) or text[pos] != ',':
            return pos, False
        pos += 1
    return pos, False


def test_json_validator():
    assert validate_json_structure('{"a": 1}')[0] is True
    assert validate_json_structure('[1, 2, 3]')[0] is True
    assert validate_json_structure('{')[0] is False
    assert validate_json_structure('true')[0] is True
    return True
''',
    "date_validator": '''
def is_leap_year(year):
    # Check if a year is a leap year.
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def days_in_month(year, month):
    # Return the number of days in a given month.
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    return days[month]


def validate_date(date_str):
    # Validate a date string in YYYY-MM-DD format.
    if not date_str or not isinstance(date_str, str):
        return False, "empty input"
    parts = date_str.split('-')
    if len(parts) != 3:
        return False, "expected YYYY-MM-DD format"
    try:
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
    except ValueError:
        return False, "non-numeric components"
    if year < 1 or year > 9999:
        return False, "year out of range"
    if month < 1 or month > 12:
        return False, "month out of range"
    max_day = days_in_month(year, month)
    if day < 1 or day > max_day:
        return False, f"day out of range for month {month}"
    return True, "valid"


def day_of_year(year, month, day):
    # Compute the day number within the year (1-366).
    total = 0
    for m in range(1, month):
        total += days_in_month(year, m)
    total += day
    return total


def test_date_validator():
    assert validate_date("2024-02-29")[0] is True
    assert validate_date("2023-02-29")[0] is False
    assert validate_date("2024-13-01")[0] is False
    assert day_of_year(2024, 3, 1) == 61
    return True
''',
    "ipv4_validator": '''
def validate_ipv4(address):
    # Validate an IPv4 address string.
    if not address or not isinstance(address, str):
        return False, "empty or not string"
    parts = address.split('.')
    if len(parts) != 4:
        return False, "must have exactly 4 octets"
    for part in parts:
        if not part:
            return False, "empty octet"
        if not part.isdigit():
            return False, f"non-numeric octet: {part}"
        if len(part) > 1 and part[0] == '0':
            return False, "leading zero"
        val = int(part)
        if val < 0 or val > 255:
            return False, f"octet out of range: {val}"
    return True, "valid"


def is_private_ip(address):
    # Check if an IPv4 address is in a private range.
    parts = [int(p) for p in address.split('.')]
    if parts[0] == 10:
        return True
    if parts[0] == 172 and 16 <= parts[1] <= 31:
        return True
    if parts[0] == 192 and parts[1] == 168:
        return True
    return False


def ip_to_int(address):
    # Convert IPv4 address string to integer.
    parts = [int(p) for p in address.split('.')]
    return (parts[0] << 24) + (parts[1] << 16) + (parts[2] << 8) + parts[3]


def int_to_ip(num):
    # Convert integer to IPv4 address string.
    return f"{(num >> 24) & 255}.{(num >> 16) & 255}.{(num >> 8) & 255}.{num & 255}"


def test_ipv4():
    assert validate_ipv4("192.168.1.1")[0] is True
    assert validate_ipv4("256.1.1.1")[0] is False
    assert validate_ipv4("01.1.1.1")[0] is False
    assert is_private_ip("192.168.1.1") is True
    assert ip_to_int("192.168.1.1") == 3232235777
    return True
''',
    "credit_card_validator": '''
def luhn_check(number_str):
    # Validate a number using the Luhn algorithm.
    digits = [int(d) for d in number_str if d.isdigit()]
    if len(digits) < 2:
        return False
    total = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def identify_card_type(number_str):
    # Identify credit card type by prefix.
    clean = number_str.replace(" ", "").replace("-", "")
    if not clean.isdigit():
        return "unknown"
    if clean.startswith("4") and len(clean) in (13, 16):
        return "visa"
    if clean[:2] in ("51", "52", "53", "54", "55") and len(clean) == 16:
        return "mastercard"
    if clean[:2] in ("34", "37") and len(clean) == 15:
        return "amex"
    if clean[:4] == "6011" and len(clean) == 16:
        return "discover"
    return "unknown"


def validate_credit_card(number_str):
    # Validate credit card number format and checksum.
    clean = number_str.replace(" ", "").replace("-", "")
    if not clean.isdigit():
        return False, "non-numeric"
    if len(clean) < 13 or len(clean) > 19:
        return False, "invalid length"
    if not luhn_check(clean):
        return False, "failed Luhn check"
    return True, identify_card_type(number_str)


def test_credit_card():
    assert luhn_check("4539578763621486") is True
    assert luhn_check("1234567890123456") is False
    assert identify_card_type("4539578763621486") == "visa"
    ok, _ = validate_credit_card("4539578763621486")
    assert ok is True
    return True
''',
    "password_validator": '''
def check_password_strength(password):
    # Check password strength and return score (0-5) with feedback.
    if not password:
        return 0, ["password is empty"]
    score = 0
    feedback = []
    if len(password) >= 8:
        score += 1
    else:
        feedback.append("at least 8 characters required")
    if len(password) >= 12:
        score += 1
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in "!@#$%^&*()-_=+[]{}|;:,.<>?" for c in password)
    if has_upper:
        score += 1
    else:
        feedback.append("add uppercase letter")
    if has_lower and has_digit:
        score += 1
    else:
        if not has_lower:
            feedback.append("add lowercase letter")
        if not has_digit:
            feedback.append("add digit")
    if has_special:
        score += 1
    else:
        feedback.append("add special character")
    return score, feedback


def has_common_patterns(password):
    # Check if password contains common weak patterns.
    common = ["password", "123456", "qwerty", "abc123", "admin", "letmein"]
    lower = password.lower()
    for p in common:
        if p in lower:
            return True
    if lower == lower[0] * len(lower):
        return True
    return False


def test_password():
    score, _ = check_password_strength("Str0ng!Pass")
    assert score >= 4
    score, feedback = check_password_strength("weak")
    assert score < 3
    assert has_common_patterns("mypassword123") is True
    assert has_common_patterns("xK9#mZ2!") is False
    return True
''',
    "url_validator": '''
def validate_url(url):
    # Validate URL format.
    if not url or not isinstance(url, str):
        return False, "empty or not string"
    valid_schemes = ("http://", "https://", "ftp://")
    scheme = None
    for s in valid_schemes:
        if url.startswith(s):
            scheme = s
            break
    if scheme is None:
        return False, "invalid scheme"
    rest = url[len(scheme):]
    if not rest:
        return False, "empty after scheme"
    if '/' in rest:
        host_port = rest[:rest.index('/')]
    else:
        host_port = rest
    if ':' in host_port:
        host, port_str = host_port.rsplit(':', 1)
        if not port_str.isdigit():
            return False, "invalid port"
        port = int(port_str)
        if port < 1 or port > 65535:
            return False, "port out of range"
    else:
        host = host_port
    if not host:
        return False, "empty host"
    parts = host.split('.')
    if len(parts) < 2:
        return False, "host needs domain and TLD"
    for part in parts:
        if not part:
            return False, "empty host component"
        if not all(c.isalnum() or c == '-' for c in part):
            return False, "invalid host character"
    return True, "valid"


def test_url():
    assert validate_url("https://example.com")[0] is True
    assert validate_url("http://a.b:8080/path")[0] is True
    assert validate_url("ftp://server.org")[0] is True
    assert validate_url("noscheme.com")[0] is False
    assert validate_url("http://")[0] is False
    return True
''',
    "hex_color_validator": '''
def validate_hex_color(color):
    # Validate a hex color code (#RGB or #RRGGBB).
    if not color or not isinstance(color, str):
        return False, "empty or not string"
    if not color.startswith('#'):
        return False, "must start with #"
    hex_part = color[1:]
    if len(hex_part) not in (3, 6):
        return False, "must be 3 or 6 hex digits"
    valid_hex = set('0123456789abcdefABCDEF')
    for ch in hex_part:
        if ch not in valid_hex:
            return False, f"invalid hex character: {ch}"
    return True, "valid"


def hex_to_rgb(color):
    # Convert hex color to (r, g, b) tuple.
    hex_part = color.lstrip('#')
    if len(hex_part) == 3:
        hex_part = ''.join(c * 2 for c in hex_part)
    r = int(hex_part[0:2], 16)
    g = int(hex_part[2:4], 16)
    b = int(hex_part[4:6], 16)
    return (r, g, b)


def rgb_to_hex(r, g, b):
    # Convert (r, g, b) to hex color string.
    return f"#{r:02x}{g:02x}{b:02x}"


def color_brightness(color):
    # Calculate perceived brightness of a hex color (0-255).
    r, g, b = hex_to_rgb(color)
    return int(0.299 * r + 0.587 * g + 0.114 * b)


def test_hex_color():
    assert validate_hex_color("#ff0000")[0] is True
    assert validate_hex_color("#abc")[0] is True
    assert validate_hex_color("#xyz")[0] is False
    assert hex_to_rgb("#ff0000") == (255, 0, 0)
    assert rgb_to_hex(255, 0, 0) == "#ff0000"
    return True
''',
    "phone_validator": '''
def normalize_phone(phone):
    # Strip non-digit characters from phone number.
    return "".join(c for c in phone if c.isdigit())


def validate_phone(phone):
    # Validate phone number format (US-style).
    if not phone or not isinstance(phone, str):
        return False, "empty or not string"
    digits = normalize_phone(phone)
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    if len(digits) != 10:
        return False, f"expected 10 digits, got {len(digits)}"
    area_code = digits[:3]
    if area_code[0] in ('0', '1'):
        return False, "area code cannot start with 0 or 1"
    exchange = digits[3:6]
    if exchange[0] in ('0', '1'):
        return False, "exchange cannot start with 0 or 1"
    return True, "valid"


def format_phone(phone):
    # Format phone number as (XXX) XXX-XXXX.
    digits = normalize_phone(phone)
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    if len(digits) != 10:
        return phone
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def test_phone():
    assert validate_phone("(555) 234-5678")[0] is True
    assert validate_phone("1-555-234-5678")[0] is True
    assert validate_phone("123")[0] is False
    assert format_phone("5552345678") == "(555) 234-5678"
    return True
''',
    "schema_validator": '''
def validate_schema(data, schema):
    # Validate a dict against a schema definition.
    # Schema format: {field: {"type": type_name, "required": bool}}
    errors = []
    if not isinstance(data, dict):
        return False, ["data must be a dict"]
    for field, rules in schema.items():
        required = rules.get("required", False)
        if field not in data:
            if required:
                errors.append(f"missing required field: {field}")
            continue
        value = data[field]
        expected_type = rules.get("type")
        if expected_type == "str" and not isinstance(value, str):
            errors.append(f"{field}: expected str, got {type(value).__name__}")
        elif expected_type == "int" and not isinstance(value, int):
            errors.append(f"{field}: expected int, got {type(value).__name__}")
        elif expected_type == "float" and not isinstance(value, (int, float)):
            errors.append(f"{field}: expected float, got {type(value).__name__}")
        elif expected_type == "list" and not isinstance(value, list):
            errors.append(f"{field}: expected list, got {type(value).__name__}")
        min_val = rules.get("min")
        max_val = rules.get("max")
        if min_val is not None and isinstance(value, (int, float)):
            if value < min_val:
                errors.append(f"{field}: value {value} below min {min_val}")
        if max_val is not None and isinstance(value, (int, float)):
            if value > max_val:
                errors.append(f"{field}: value {value} above max {max_val}")
    for key in data:
        if key not in schema:
            errors.append(f"unexpected field: {key}")
    return len(errors) == 0, errors


def test_schema():
    schema = {
        "name": {"type": "str", "required": True},
        "age": {"type": "int", "required": True, "min": 0, "max": 150},
        "email": {"type": "str", "required": False},
    }
    ok, errs = validate_schema({"name": "Alice", "age": 30}, schema)
    assert ok is True
    ok, errs = validate_schema({"age": 30}, schema)
    assert ok is False
    ok, errs = validate_schema({"name": "Bob", "age": -1}, schema)
    assert ok is False
    return True
''',
    "vending_machine": '''
class VendingMachine:
    # Simple vending machine state machine.
    STATES = ("idle", "accepting_coins", "dispensing", "returning_change")

    def __init__(self):
        self.state = "idle"
        self.balance = 0
        self.products = {"cola": 150, "chips": 100, "candy": 75}

    def insert_coin(self, amount):
        if self.state == "idle":
            self.state = "accepting_coins"
        if self.state != "accepting_coins":
            return False, "cannot accept coins now"
        if amount not in (5, 10, 25, 100):
            return False, "invalid coin"
        self.balance += amount
        return True, f"balance: {self.balance}"

    def select_product(self, product):
        if self.state != "accepting_coins":
            return False, "insert coins first"
        if product not in self.products:
            return False, "unknown product"
        price = self.products[product]
        if self.balance < price:
            return False, f"need {price - self.balance} more"
        self.state = "dispensing"
        change = self.balance - price
        self.balance = 0
        self.state = "idle"
        return True, f"dispensed {product}, change: {change}"

    def cancel(self):
        returned = self.balance
        self.balance = 0
        self.state = "idle"
        return returned


def test_vending():
    vm = VendingMachine()
    vm.insert_coin(100)
    vm.insert_coin(100)
    ok, msg = vm.select_product("cola")
    assert ok is True
    assert "change: 50" in msg
    return True
''',
    "traffic_light": '''
class TrafficLight:
    # Traffic light controller with timed state transitions.
    STATES = ("red", "green", "yellow")
    DURATIONS = {"red": 30, "green": 25, "yellow": 5}

    def __init__(self):
        self.state = "red"
        self.timer = 0
        self.cycle_count = 0

    def tick(self, seconds=1):
        self.timer += seconds
        duration = self.DURATIONS[self.state]
        transitions = 0
        while self.timer >= duration:
            self.timer -= duration
            self._transition()
            transitions += 1
            duration = self.DURATIONS[self.state]
        return transitions

    def _transition(self):
        if self.state == "red":
            self.state = "green"
        elif self.state == "green":
            self.state = "yellow"
        elif self.state == "yellow":
            self.state = "red"
            self.cycle_count += 1

    def get_state(self):
        return self.state

    def time_remaining(self):
        return self.DURATIONS[self.state] - self.timer

    def reset(self):
        self.state = "red"
        self.timer = 0
        self.cycle_count = 0


def test_traffic_light():
    tl = TrafficLight()
    assert tl.get_state() == "red"
    tl.tick(30)
    assert tl.get_state() == "green"
    tl.tick(25)
    assert tl.get_state() == "yellow"
    tl.tick(5)
    assert tl.get_state() == "red"
    assert tl.cycle_count == 1
    return True
''',
    "protocol_handler": '''
class ProtocolHandler:
    # Simple request/response protocol state machine.
    STATES = ("idle", "waiting_header", "waiting_body", "processing", "done", "error")

    def __init__(self):
        self.state = "idle"
        self.header = None
        self.body = None
        self.response = None

    def receive_header(self, header):
        if self.state != "idle":
            self.state = "error"
            return False, "unexpected header"
        if not isinstance(header, dict):
            self.state = "error"
            return False, "header must be dict"
        self.header = header
        self.state = "waiting_body"
        return True, "header accepted"

    def receive_body(self, body):
        if self.state != "waiting_body":
            self.state = "error"
            return False, "unexpected body"
        self.body = body
        self.state = "processing"
        return True, "body accepted"

    def process(self):
        if self.state != "processing":
            return False, "nothing to process"
        method = self.header.get("method", "GET")
        path = self.header.get("path", "/")
        self.response = {
            "status": 200,
            "method": method,
            "path": path,
            "body_length": len(str(self.body)) if self.body else 0,
        }
        self.state = "done"
        return True, self.response

    def reset(self):
        self.state = "idle"
        self.header = None
        self.body = None
        self.response = None


def test_protocol():
    ph = ProtocolHandler()
    ph.receive_header({"method": "POST", "path": "/api"})
    ph.receive_body({"data": "hello"})
    ok, resp = ph.process()
    assert ok is True
    assert resp["status"] == 200
    ph.reset()
    assert ph.state == "idle"
    return True
''',
    "elevator_controller": '''
class Elevator:
    # Elevator state machine with floor queue management.
    def __init__(self, min_floor=1, max_floor=10):
        self.current_floor = 1
        self.direction = "idle"
        self.min_floor = min_floor
        self.max_floor = max_floor
        self.requests = set()
        self.door_open = False

    def request_floor(self, floor):
        if floor < self.min_floor or floor > self.max_floor:
            return False, "floor out of range"
        self.requests.add(floor)
        if self.direction == "idle":
            if floor > self.current_floor:
                self.direction = "up"
            elif floor < self.current_floor:
                self.direction = "down"
        return True, f"floor {floor} queued"

    def step(self):
        if not self.requests:
            self.direction = "idle"
            return "idle"
        if self.current_floor in self.requests:
            self.requests.discard(self.current_floor)
            self.door_open = True
            self.door_open = False
            if not self.requests:
                self.direction = "idle"
            return f"stopped at {self.current_floor}"
        if self.direction == "up":
            if any(f > self.current_floor for f in self.requests):
                self.current_floor += 1
            else:
                self.direction = "down"
        elif self.direction == "down":
            if any(f < self.current_floor for f in self.requests):
                self.current_floor -= 1
            else:
                self.direction = "up"
        return f"moving {self.direction} at {self.current_floor}"

    def status(self):
        return {
            "floor": self.current_floor,
            "direction": self.direction,
            "pending": sorted(self.requests),
        }


def test_elevator():
    e = Elevator()
    e.request_floor(5)
    for _ in range(10):
        e.step()
    assert e.current_floor == 5
    assert e.direction == "idle"
    return True
''',
    "turnstile": '''
class Turnstile:
    # Turnstile state machine: locked/unlocked states with coin and push events.
    def __init__(self):
        self.state = "locked"
        self.coins_collected = 0
        self.entries = 0
        self.pushes_blocked = 0
        self.log = []

    def coin(self):
        if self.state == "locked":
            self.state = "unlocked"
            self.coins_collected += 1
            self.log.append("coin: locked -> unlocked")
            return "unlocked"
        else:
            self.coins_collected += 1
            self.log.append("coin: already unlocked")
            return "unlocked"

    def push(self):
        if self.state == "unlocked":
            self.state = "locked"
            self.entries += 1
            self.log.append("push: unlocked -> locked")
            return "entered"
        else:
            self.pushes_blocked += 1
            self.log.append("push: blocked (locked)")
            return "blocked"

    def stats(self):
        return {
            "state": self.state,
            "coins": self.coins_collected,
            "entries": self.entries,
            "blocked": self.pushes_blocked,
        }

    def reset(self):
        self.state = "locked"
        self.log.clear()


def test_turnstile():
    t = Turnstile()
    assert t.push() == "blocked"
    assert t.coin() == "unlocked"
    assert t.push() == "entered"
    assert t.state == "locked"
    s = t.stats()
    assert s["entries"] == 1
    assert s["blocked"] == 1
    return True
''',
    "expression_evaluator": '''
def eval_tokenize(expr):
    # Tokenize an infix expression into numbers and operators.
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
        elif expr[i].isdigit() or expr[i] == '.':
            num = ""
            while i < len(expr) and (expr[i].isdigit() or expr[i] == '.'):
                num += expr[i]
                i += 1
            tokens.append(float(num))
        elif expr[i] in "+-*/^()":
            tokens.append(expr[i])
            i += 1
        else:
            raise ValueError(f"bad char: {expr[i]}")
    return tokens


def infix_to_postfix(tokens):
    # Convert infix tokens to postfix using shunting-yard algorithm.
    prec = {'+': 1, '-': 1, '*': 2, '/': 2, '^': 3}
    right_assoc = {'^'}
    output = []
    ops = []
    for tok in tokens:
        if isinstance(tok, float):
            output.append(tok)
        elif tok in prec:
            while (ops and ops[-1] != '(' and ops[-1] in prec and
                   (prec[ops[-1]] > prec[tok] or
                    (prec[ops[-1]] == prec[tok] and tok not in right_assoc))):
                output.append(ops.pop())
            ops.append(tok)
        elif tok == '(':
            ops.append(tok)
        elif tok == ')':
            while ops and ops[-1] != '(':
                output.append(ops.pop())
            if ops:
                ops.pop()
    while ops:
        output.append(ops.pop())
    return output


def eval_postfix(postfix):
    # Evaluate a postfix expression.
    stack = []
    for tok in postfix:
        if isinstance(tok, float):
            stack.append(tok)
        else:
            b = stack.pop()
            a = stack.pop()
            if tok == '+': stack.append(a + b)
            elif tok == '-': stack.append(a - b)
            elif tok == '*': stack.append(a * b)
            elif tok == '/': stack.append(a / b)
            elif tok == '^': stack.append(a ** b)
    return stack[0]


def evaluate(expr):
    tokens = eval_tokenize(expr)
    postfix = infix_to_postfix(tokens)
    return eval_postfix(postfix)


def test_evaluator():
    assert evaluate("2 + 3 * 4") == 14.0
    assert evaluate("(2 + 3) * 4") == 20.0
    assert evaluate("2 ^ 3") == 8.0
    return True
''',
    "unit_converter": '''
CONVERSIONS = {
    ("km", "miles"): 0.621371,
    ("miles", "km"): 1.60934,
    ("kg", "lbs"): 2.20462,
    ("lbs", "kg"): 0.453592,
    ("celsius", "fahrenheit"): lambda c: c * 9.0 / 5.0 + 32,
    ("fahrenheit", "celsius"): lambda f: (f - 32) * 5.0 / 9.0,
    ("meters", "feet"): 3.28084,
    ("feet", "meters"): 0.3048,
    ("liters", "gallons"): 0.264172,
    ("gallons", "liters"): 3.78541,
}


def convert(value, from_unit, to_unit):
    # Convert a value from one unit to another.
    if from_unit == to_unit:
        return value
    key = (from_unit, to_unit)
    if key not in CONVERSIONS:
        raise ValueError(f"no conversion from {from_unit} to {to_unit}")
    factor = CONVERSIONS[key]
    if callable(factor):
        return factor(value)
    return value * factor


def convert_batch(values, from_unit, to_unit):
    # Convert a list of values.
    return [convert(v, from_unit, to_unit) for v in values]


def available_conversions():
    # List all available unit conversion pairs.
    return list(CONVERSIONS.keys())


def test_converter():
    assert abs(convert(1, "km", "miles") - 0.621371) < 0.001
    assert abs(convert(100, "celsius", "fahrenheit") - 212.0) < 0.01
    assert abs(convert(32, "fahrenheit", "celsius") - 0.0) < 0.01
    batch = convert_batch([1, 2, 3], "kg", "lbs")
    assert len(batch) == 3
    return True
''',
    "mortgage_calculator": '''
def monthly_payment(principal, annual_rate, years):
    # Calculate monthly mortgage payment.
    if annual_rate == 0:
        return principal / (years * 12)
    monthly_rate = annual_rate / 100 / 12
    num_payments = years * 12
    payment = principal * (monthly_rate * (1 + monthly_rate) ** num_payments) / \
              ((1 + monthly_rate) ** num_payments - 1)
    return round(payment, 2)


def amortization_schedule(principal, annual_rate, years):
    # Generate full amortization schedule.
    payment = monthly_payment(principal, annual_rate, years)
    monthly_rate = annual_rate / 100 / 12
    balance = principal
    schedule = []
    for month in range(1, years * 12 + 1):
        interest = round(balance * monthly_rate, 2)
        principal_paid = round(payment - interest, 2)
        balance = round(balance - principal_paid, 2)
        if balance < 0:
            balance = 0
        schedule.append({
            "month": month,
            "payment": payment,
            "interest": interest,
            "principal": principal_paid,
            "balance": balance,
        })
    return schedule


def total_interest(principal, annual_rate, years):
    # Calculate total interest paid over loan lifetime.
    payment = monthly_payment(principal, annual_rate, years)
    total_paid = payment * years * 12
    return round(total_paid - principal, 2)


def test_mortgage():
    pmt = monthly_payment(200000, 5.0, 30)
    assert 1000 < pmt < 1200
    ti = total_interest(200000, 5.0, 30)
    assert ti > 0
    sched = amortization_schedule(200000, 5.0, 30)
    assert len(sched) == 360
    return True
''',
    "tax_calculator": '''
TAX_BRACKETS = [
    (10000, 0.10),
    (30000, 0.15),
    (60000, 0.25),
    (100000, 0.30),
    (float("inf"), 0.35),
]


def calculate_tax(income):
    # Calculate progressive tax for given income.
    if income <= 0:
        return 0.0
    tax = 0.0
    prev_limit = 0
    for limit, rate in TAX_BRACKETS:
        if income <= prev_limit:
            break
        taxable = min(income, limit) - prev_limit
        tax += taxable * rate
        prev_limit = limit
    return round(tax, 2)


def effective_rate(income):
    # Calculate effective tax rate.
    if income <= 0:
        return 0.0
    return round(calculate_tax(income) / income * 100, 2)


def marginal_rate(income):
    # Determine marginal tax rate for given income.
    prev_limit = 0
    for limit, rate in TAX_BRACKETS:
        if income <= limit:
            return rate
        prev_limit = limit
    return TAX_BRACKETS[-1][1]


def tax_breakdown(income):
    # Return detailed breakdown by bracket.
    breakdown = []
    prev_limit = 0
    for limit, rate in TAX_BRACKETS:
        if income <= prev_limit:
            break
        taxable = min(income, limit) - prev_limit
        breakdown.append({"bracket": rate, "taxable": taxable, "tax": round(taxable * rate, 2)})
        prev_limit = limit
    return breakdown


def test_tax():
    assert calculate_tax(0) == 0.0
    assert calculate_tax(5000) == 500.0
    assert effective_rate(5000) == 10.0
    bd = tax_breakdown(50000)
    assert len(bd) == 3
    return True
''',
    "bmi_calculator": '''
def calculate_bmi(weight_kg, height_m):
    # Calculate Body Mass Index.
    if height_m <= 0:
        raise ValueError("height must be positive")
    if weight_kg <= 0:
        raise ValueError("weight must be positive")
    return round(weight_kg / (height_m ** 2), 1)


def bmi_category(bmi):
    # Classify BMI into health category.
    if bmi < 16:
        return "severely underweight"
    if bmi < 18.5:
        return "underweight"
    if bmi < 25:
        return "normal"
    if bmi < 30:
        return "overweight"
    if bmi < 35:
        return "obese class I"
    if bmi < 40:
        return "obese class II"
    return "obese class III"


def healthy_weight_range(height_m):
    # Calculate healthy weight range for given height.
    low = round(18.5 * height_m ** 2, 1)
    high = round(24.9 * height_m ** 2, 1)
    return low, high


def weight_to_target_bmi(current_weight, height_m, target_bmi):
    # Calculate weight change needed to reach target BMI.
    target_weight = target_bmi * height_m ** 2
    return round(target_weight - current_weight, 1)


def test_bmi():
    bmi = calculate_bmi(70, 1.75)
    assert 22 < bmi < 24
    assert bmi_category(22) == "normal"
    low, high = healthy_weight_range(1.75)
    assert low < 70 < high
    return True
''',
    "tip_calculator": '''
def calculate_tip(bill_amount, tip_percent):
    # Calculate tip amount from bill and tip percentage.
    if bill_amount < 0:
        raise ValueError("bill cannot be negative")
    if tip_percent < 0:
        raise ValueError("tip percent cannot be negative")
    tip = bill_amount * tip_percent / 100
    return round(tip, 2)


def split_bill(bill_amount, tip_percent, num_people):
    # Split bill with tip among people.
    if num_people <= 0:
        raise ValueError("need at least one person")
    tip = calculate_tip(bill_amount, tip_percent)
    total = bill_amount + tip
    per_person = total / num_people
    return {
        "bill": round(bill_amount, 2),
        "tip": round(tip, 2),
        "total": round(total, 2),
        "per_person": round(per_person, 2),
        "num_people": num_people,
    }


def suggested_tips(bill_amount):
    # Calculate suggested tip amounts at common percentages.
    percentages = [15, 18, 20, 25]
    return {p: calculate_tip(bill_amount, p) for p in percentages}


def round_up_total(bill_amount, tip_percent):
    # Round total up to nearest dollar.
    import math
    tip = calculate_tip(bill_amount, tip_percent)
    total = bill_amount + tip
    rounded = math.ceil(total)
    actual_tip = rounded - bill_amount
    return {"total": rounded, "tip": round(actual_tip, 2)}


def test_tip():
    assert calculate_tip(100, 20) == 20.0
    result = split_bill(100, 20, 4)
    assert result["per_person"] == 30.0
    tips = suggested_tips(50)
    assert tips[20] == 10.0
    return True
''',
    "currency_converter": '''
EXCHANGE_RATES = {
    "USD": 1.0,
    "EUR": 0.85,
    "GBP": 0.73,
    "JPY": 110.0,
    "CAD": 1.25,
    "AUD": 1.35,
    "CHF": 0.92,
    "CNY": 6.45,
}


def convert_currency(amount, from_currency, to_currency, rates=None):
    # Convert amount between currencies using exchange rates.
    if rates is None:
        rates = EXCHANGE_RATES
    if from_currency not in rates:
        raise ValueError(f"unknown currency: {from_currency}")
    if to_currency not in rates:
        raise ValueError(f"unknown currency: {to_currency}")
    usd_amount = amount / rates[from_currency]
    result = usd_amount * rates[to_currency]
    return round(result, 2)


def exchange_table(amount, base_currency, rates=None):
    # Generate exchange table from base currency.
    if rates is None:
        rates = EXCHANGE_RATES
    table = {}
    for currency in rates:
        if currency != base_currency:
            table[currency] = convert_currency(amount, base_currency, currency, rates)
    return table


def supported_currencies():
    # List all supported currencies.
    return sorted(EXCHANGE_RATES.keys())


def test_currency():
    result = convert_currency(100, "USD", "EUR")
    assert result == 85.0
    result = convert_currency(100, "EUR", "USD")
    assert abs(result - 117.65) < 0.1
    table = exchange_table(100, "USD")
    assert "EUR" in table
    return True
''',
    "age_calculator": '''
def parse_date(date_str):
    # Parse YYYY-MM-DD string to (year, month, day) tuple.
    parts = date_str.split("-")
    return int(parts[0]), int(parts[1]), int(parts[2])


def is_leap_year_age(year):
    # Check if year is a leap year.
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def days_in_month_age(year, month):
    # Days in a given month.
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year_age(year):
        return 29
    return days[month]


def calculate_age(birth_date, current_date):
    # Calculate age in years, months, and days.
    by, bm, bd = parse_date(birth_date)
    cy, cm, cd = parse_date(current_date)
    years = cy - by
    months = cm - bm
    days = cd - bd
    if days < 0:
        months -= 1
        prev_month = cm - 1 if cm > 1 else 12
        prev_year = cy if cm > 1 else cy - 1
        days += days_in_month_age(prev_year, prev_month)
    if months < 0:
        years -= 1
        months += 12
    return {"years": years, "months": months, "days": days}


def days_until_birthday(birth_date, current_date):
    # Calculate days until next birthday.
    by, bm, bd = parse_date(birth_date)
    cy, cm, cd = parse_date(current_date)
    next_bday_year = cy if (bm, bd) >= (cm, cd) else cy + 1
    total = 0
    y, m, d = cy, cm, cd
    while (y, m, d) != (next_bday_year, bm, bd):
        dim = days_in_month_age(y, m)
        d += 1
        if d > dim:
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
        total += 1
        if total > 366:
            break
    return total


def test_age():
    age = calculate_age("1990-06-15", "2024-01-10")
    assert age["years"] == 33
    assert age["months"] == 6
    return True
''',
    "compound_interest": '''
def compound_interest(principal, rate, years, n=12):
    # Calculate compound interest.
    # n = compounding frequency per year.
    if principal < 0 or rate < 0 or years < 0:
        raise ValueError("values must be non-negative")
    amount = principal * (1 + rate / 100 / n) ** (n * years)
    return round(amount, 2)


def simple_interest(principal, rate, years):
    # Calculate simple interest.
    return round(principal * (1 + rate / 100 * years), 2)


def interest_schedule(principal, rate, years, n=12):
    # Generate year-by-year compound interest schedule.
    schedule = []
    for year in range(1, years + 1):
        amount = compound_interest(principal, rate, year, n)
        interest_earned = round(amount - principal, 2)
        schedule.append({
            "year": year,
            "balance": amount,
            "interest_earned": interest_earned,
        })
    return schedule


def doubling_time(rate, n=12):
    # Estimate years to double investment (Rule of 72 and exact).
    import math
    if rate <= 0:
        return float("inf")
    rule_of_72 = 72 / rate
    exact = math.log(2) / (n * math.log(1 + rate / 100 / n))
    return {"rule_of_72": round(rule_of_72, 2), "exact": round(exact, 2)}


def test_compound():
    result = compound_interest(1000, 5, 10)
    assert result > 1000
    si = simple_interest(1000, 5, 10)
    assert si == 1500.0
    sched = interest_schedule(1000, 5, 3)
    assert len(sched) == 3
    dt = doubling_time(7)
    assert dt["rule_of_72"] == 10.29 or True
    return True
''',
    "grade_calculator": '''
def weighted_average(grades, weights):
    # Calculate weighted average of grades.
    if len(grades) != len(weights):
        raise ValueError("grades and weights must have same length")
    total_weight = sum(weights)
    if total_weight == 0:
        raise ValueError("total weight is zero")
    weighted_sum = sum(g * w for g, w in zip(grades, weights))
    return round(weighted_sum / total_weight, 2)


def letter_grade(score):
    # Convert numeric score to letter grade.
    if score >= 93: return "A"
    if score >= 90: return "A-"
    if score >= 87: return "B+"
    if score >= 83: return "B"
    if score >= 80: return "B-"
    if score >= 77: return "C+"
    if score >= 73: return "C"
    if score >= 70: return "C-"
    if score >= 67: return "D+"
    if score >= 60: return "D"
    return "F"


def gpa_from_letter(letter):
    # Convert letter grade to GPA points.
    scale = {"A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7,
             "C+": 2.3, "C": 2.0, "C-": 1.7, "D+": 1.3, "D": 1.0, "F": 0.0}
    return scale.get(letter, 0.0)


def calculate_gpa(grades, credits):
    # Calculate GPA from grades and credit hours.
    total_points = sum(gpa_from_letter(letter_grade(g)) * c for g, c in zip(grades, credits))
    total_credits = sum(credits)
    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)


def class_statistics(scores):
    # Calculate class statistics from a list of scores.
    if not scores:
        return {}
    s = sorted(scores)
    n = len(s)
    return {
        "mean": round(sum(s) / n, 2),
        "median": s[n // 2] if n % 2 else (s[n//2 - 1] + s[n//2]) / 2,
        "min": s[0],
        "max": s[-1],
        "count": n,
    }


def test_grades():
    avg = weighted_average([90, 80, 70], [0.3, 0.3, 0.4])
    assert avg == 79.0
    assert letter_grade(95) == "A"
    assert letter_grade(72) == "C-"
    gpa = calculate_gpa([95, 85, 75], [3, 3, 4])
    assert gpa > 0
    return True
''',
    "config_parser": '''
def parse_ini(text):
    # Parse INI-style configuration text into nested dict.
    config = {}
    current_section = None
    for line in text.split("\\n"):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current_section = line[1:-1].strip()
            if current_section not in config:
                config[current_section] = {}
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.isdigit():
                value = int(value)
            elif value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            if current_section:
                config[current_section][key] = value
            else:
                config[key] = value
    return config


def get_value(config, section, key, default=None):
    # Get a value from parsed config with default.
    return config.get(section, {}).get(key, default)


def config_to_ini(config):
    # Convert config dict back to INI format string.
    lines = []
    for section, values in config.items():
        if isinstance(values, dict):
            lines.append(f"[{section}]")
            for k, v in values.items():
                lines.append(f"{k} = {v}")
            lines.append("")
    return "\\n".join(lines)


def test_config():
    text = "[server]\\nhost = localhost\\nport = 8080\\n[db]\\nname = mydb"
    config = parse_ini(text)
    assert config["server"]["host"] == "localhost"
    assert config["server"]["port"] == 8080
    assert get_value(config, "db", "name") == "mydb"
    return True
''',
    "log_analyzer": '''
def parse_log_line(line):
    # Parse a log line in format: LEVEL [timestamp] message.
    parts = line.strip().split(" ", 2)
    if len(parts) < 3:
        return None
    level = parts[0]
    timestamp = parts[1].strip("[]")
    message = parts[2]
    return {"level": level, "timestamp": timestamp, "message": message}


def analyze_logs(log_text):
    # Analyze log text and return summary statistics.
    lines = log_text.strip().split("\\n")
    entries = []
    for line in lines:
        parsed = parse_log_line(line)
        if parsed:
            entries.append(parsed)
    level_counts = {}
    for entry in entries:
        lvl = entry["level"]
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
    return {
        "total": len(entries),
        "level_counts": level_counts,
        "errors": [e for e in entries if e["level"] == "ERROR"],
    }


def filter_by_level(entries, level):
    # Filter log entries by level.
    return [e for e in entries if e["level"] == level]


def search_logs(entries, keyword):
    # Search log entries for keyword in message.
    keyword_lower = keyword.lower()
    return [e for e in entries if keyword_lower in e["message"].lower()]


def test_log_analyzer():
    logs = "INFO [2024-01-01] started\\nERROR [2024-01-01] failed\\nINFO [2024-01-01] done"
    result = analyze_logs(logs)
    assert result["total"] == 3
    assert result["level_counts"]["ERROR"] == 1
    assert len(result["errors"]) == 1
    return True
''',
    "csv_writer": '''
def escape_csv_field(field, delimiter=','):
    # Escape a CSV field, quoting if necessary.
    s = str(field)
    needs_quote = delimiter in s or '"' in s or '\\n' in s
    if needs_quote:
        s = s.replace('"', '""')
        return f'"{s}"'
    return s


def write_csv_row(fields, delimiter=','):
    # Write a single CSV row from a list of fields.
    return delimiter.join(escape_csv_field(f, delimiter) for f in fields)


def write_csv(rows, headers=None, delimiter=','):
    # Write complete CSV content from rows (list of lists/dicts).
    lines = []
    if headers:
        lines.append(write_csv_row(headers, delimiter))
    for row in rows:
        if isinstance(row, dict) and headers:
            values = [row.get(h, "") for h in headers]
        elif isinstance(row, (list, tuple)):
            values = row
        else:
            values = [row]
        lines.append(write_csv_row(values, delimiter))
    return "\\n".join(lines)


def dicts_to_csv(data):
    # Convert list of dicts to CSV string.
    if not data:
        return ""
    headers = list(data[0].keys())
    return write_csv(data, headers)


def test_csv_writer():
    rows = [["Alice", 30, "NY"], ["Bob", 25, "LA"]]
    csv = write_csv(rows, ["name", "age", "city"])
    assert "Alice" in csv
    field = escape_csv_field('has,comma')
    assert field == '"has,comma"'
    return True
''',
    "inventory_manager": '''
class InventoryManager:
    # Track inventory items with add, remove, and query operations.
    def __init__(self):
        self.items = {}

    def add_item(self, name, quantity, price):
        if name in self.items:
            self.items[name]["quantity"] += quantity
            self.items[name]["price"] = price
        else:
            self.items[name] = {"quantity": quantity, "price": price}

    def remove_item(self, name, quantity):
        if name not in self.items:
            return False, "item not found"
        if self.items[name]["quantity"] < quantity:
            return False, "insufficient quantity"
        self.items[name]["quantity"] -= quantity
        if self.items[name]["quantity"] == 0:
            del self.items[name]
        return True, "removed"

    def get_stock(self, name):
        if name not in self.items:
            return 0
        return self.items[name]["quantity"]

    def total_value(self):
        total = 0.0
        for item in self.items.values():
            total += item["quantity"] * item["price"]
        return round(total, 2)

    def low_stock(self, threshold=5):
        return {k: v for k, v in self.items.items() if v["quantity"] <= threshold}

    def report(self):
        lines = []
        for name, info in sorted(self.items.items()):
            val = info["quantity"] * info["price"]
            lines.append(f"{name}: qty={info['quantity']}, price={info['price']}, value={val}")
        return "\\n".join(lines)


def test_inventory():
    inv = InventoryManager()
    inv.add_item("widget", 100, 2.50)
    inv.add_item("gadget", 50, 5.00)
    assert inv.get_stock("widget") == 100
    assert inv.total_value() == 500.0
    inv.remove_item("widget", 10)
    assert inv.get_stock("widget") == 90
    return True
''',
    "student_records": '''
class StudentRecords:
    # Manage student grades and academic records.
    def __init__(self):
        self.students = {}

    def add_student(self, student_id, name):
        self.students[student_id] = {"name": name, "courses": {}}

    def add_grade(self, student_id, course, grade):
        if student_id not in self.students:
            return False
        self.students[student_id]["courses"][course] = grade
        return True

    def get_gpa(self, student_id):
        if student_id not in self.students:
            return None
        courses = self.students[student_id]["courses"]
        if not courses:
            return 0.0
        return round(sum(courses.values()) / len(courses), 2)

    def get_transcript(self, student_id):
        if student_id not in self.students:
            return None
        student = self.students[student_id]
        lines = [f"Student: {student['name']} ({student_id})"]
        for course, grade in sorted(student["courses"].items()):
            lines.append(f"  {course}: {grade}")
        gpa = self.get_gpa(student_id)
        lines.append(f"  GPA: {gpa}")
        return "\\n".join(lines)

    def top_students(self, n=5):
        ranked = []
        for sid, info in self.students.items():
            gpa = self.get_gpa(sid)
            if gpa is not None:
                ranked.append((sid, info["name"], gpa))
        ranked.sort(key=lambda x: -x[2])
        return ranked[:n]

    def course_average(self, course):
        grades = []
        for info in self.students.values():
            if course in info["courses"]:
                grades.append(info["courses"][course])
        return round(sum(grades) / len(grades), 2) if grades else None


def test_students():
    sr = StudentRecords()
    sr.add_student("S001", "Alice")
    sr.add_grade("S001", "Math", 95)
    sr.add_grade("S001", "English", 88)
    assert sr.get_gpa("S001") == 91.5
    assert sr.course_average("Math") == 95.0
    return True
''',
    "task_scheduler": '''
class TaskScheduler:
    # Priority-based task scheduler.
    def __init__(self):
        self.tasks = []
        self.completed = []
        self.next_id = 1

    def add_task(self, name, priority=0, duration=1):
        task = {
            "id": self.next_id,
            "name": name,
            "priority": priority,
            "duration": duration,
            "status": "pending",
        }
        self.next_id += 1
        self.tasks.append(task)
        return task["id"]

    def run_next(self):
        pending = [t for t in self.tasks if t["status"] == "pending"]
        if not pending:
            return None
        pending.sort(key=lambda t: -t["priority"])
        task = pending[0]
        task["status"] = "running"
        task["status"] = "completed"
        self.completed.append(task)
        self.tasks.remove(task)
        return task

    def run_all(self):
        results = []
        while True:
            task = self.run_next()
            if task is None:
                break
            results.append(task)
        return results

    def pending_count(self):
        return sum(1 for t in self.tasks if t["status"] == "pending")

    def stats(self):
        return {
            "pending": self.pending_count(),
            "completed": len(self.completed),
            "total_duration": sum(t["duration"] for t in self.completed),
        }


def test_scheduler():
    s = TaskScheduler()
    s.add_task("low", priority=1)
    s.add_task("high", priority=10)
    s.add_task("med", priority=5)
    first = s.run_next()
    assert first["name"] == "high"
    s.run_all()
    assert s.pending_count() == 0
    return True
''',
    "cache_lru": '''
class LRUCache:
    # Least Recently Used cache with O(1) get and put.
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key not in self.cache:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            oldest = self.order.pop(0)
            del self.cache[oldest]
        self.cache[key] = value
        self.order.append(key)

    def contains(self, key):
        return key in self.cache

    def size(self):
        return len(self.cache)

    def clear(self):
        self.cache.clear()
        self.order.clear()

    def keys(self):
        return list(self.order)

    def most_recent(self):
        if not self.order:
            return None
        return self.order[-1]

    def least_recent(self):
        if not self.order:
            return None
        return self.order[0]


def test_lru():
    cache = LRUCache(3)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a") == 1
    cache.put("d", 4)
    assert cache.get("b") == -1
    assert cache.size() == 3
    return True
''',
    "event_emitter": '''
class EventEmitter:
    # Event listener/emitter pattern implementation.
    def __init__(self):
        self.listeners = {}
        self.event_log = []

    def on(self, event, callback):
        if event not in self.listeners:
            self.listeners[event] = []
        self.listeners[event].append(callback)

    def off(self, event, callback=None):
        if event not in self.listeners:
            return
        if callback is None:
            del self.listeners[event]
        else:
            self.listeners[event] = [cb for cb in self.listeners[event] if cb != callback]

    def emit(self, event, *args, **kwargs):
        self.event_log.append({"event": event, "args": args})
        if event not in self.listeners:
            return 0
        count = 0
        for callback in self.listeners[event]:
            callback(*args, **kwargs)
            count += 1
        return count

    def once(self, event, callback):
        def wrapper(*args, **kwargs):
            callback(*args, **kwargs)
            self.off(event, wrapper)
        self.on(event, wrapper)

    def listener_count(self, event=None):
        if event:
            return len(self.listeners.get(event, []))
        return sum(len(cbs) for cbs in self.listeners.values())

    def events(self):
        return list(self.listeners.keys())


def test_emitter():
    results = []
    em = EventEmitter()
    em.on("click", lambda x: results.append(x))
    em.emit("click", "button1")
    assert results == ["button1"]
    em.emit("click", "button2")
    assert len(results) == 2
    assert em.listener_count("click") == 1
    return True
''',
    "ring_buffer": '''
class RingBuffer:
    # Fixed-size circular buffer.
    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = [None] * capacity
        self.head = 0
        self.tail = 0
        self.size = 0

    def push(self, item):
        self.buffer[self.tail] = item
        self.tail = (self.tail + 1) % self.capacity
        if self.size == self.capacity:
            self.head = (self.head + 1) % self.capacity
        else:
            self.size += 1

    def pop(self):
        if self.size == 0:
            raise IndexError("pop from empty buffer")
        item = self.buffer[self.head]
        self.buffer[self.head] = None
        self.head = (self.head + 1) % self.capacity
        self.size -= 1
        return item

    def peek(self):
        if self.size == 0:
            raise IndexError("peek at empty buffer")
        return self.buffer[self.head]

    def is_empty(self):
        return self.size == 0

    def is_full(self):
        return self.size == self.capacity

    def to_list(self):
        result = []
        idx = self.head
        for _ in range(self.size):
            result.append(self.buffer[idx])
            idx = (idx + 1) % self.capacity
        return result

    def __len__(self):
        return self.size


def test_ring_buffer():
    rb = RingBuffer(3)
    rb.push(1)
    rb.push(2)
    rb.push(3)
    assert rb.is_full()
    rb.push(4)
    assert rb.to_list() == [2, 3, 4]
    assert rb.pop() == 2
    return True
''',
    "bloom_filter": '''
class BloomFilter:
    # Simple Bloom filter for approximate set membership testing.
    def __init__(self, size=1000, num_hashes=3):
        self.size = size
        self.num_hashes = num_hashes
        self.bits = [False] * size
        self.count = 0

    def _hashes(self, item):
        indices = []
        s = str(item)
        for i in range(self.num_hashes):
            h = hash(s + str(i)) % self.size
            indices.append(abs(h))
        return indices

    def add(self, item):
        for idx in self._hashes(item):
            self.bits[idx] = True
        self.count += 1

    def might_contain(self, item):
        return all(self.bits[idx] for idx in self._hashes(item))

    def false_positive_rate(self):
        set_bits = sum(self.bits)
        if self.size == 0:
            return 0.0
        ratio = set_bits / self.size
        return ratio ** self.num_hashes

    def reset(self):
        self.bits = [False] * self.size
        self.count = 0

    def fill_ratio(self):
        return sum(self.bits) / self.size


def test_bloom():
    bf = BloomFilter(1000, 3)
    bf.add("apple")
    bf.add("banana")
    assert bf.might_contain("apple") is True
    assert bf.might_contain("banana") is True
    assert bf.count == 2
    assert bf.fill_ratio() > 0
    return True
''',
    "url_parser": '''
def parse_url(url):
    # Parse URL into components: scheme, host, port, path, query, fragment.
    result = {"scheme": "", "host": "", "port": None, "path": "/", "query": "", "fragment": ""}
    rest = url
    if '#' in rest:
        rest, result["fragment"] = rest.rsplit('#', 1)
    if '?' in rest:
        rest, result["query"] = rest.split('?', 1)
    if '://' in rest:
        result["scheme"], rest = rest.split('://', 1)
    if '/' in rest:
        host_part, result["path"] = rest.split('/', 1)
        result["path"] = '/' + result["path"]
    else:
        host_part = rest
    if ':' in host_part:
        result["host"], port_str = host_part.rsplit(':', 1)
        if port_str.isdigit():
            result["port"] = int(port_str)
    else:
        result["host"] = host_part
    return result


def parse_query_string(query):
    # Parse query string into dict of key-value pairs.
    if not query:
        return {}
    params = {}
    for pair in query.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)
            params[key] = value
        else:
            params[pair] = ""
    return params


def build_url(components):
    # Build URL from component dict.
    url = ""
    if components.get("scheme"):
        url += components["scheme"] + "://"
    url += components.get("host", "")
    if components.get("port"):
        url += f":{components['port']}"
    url += components.get("path", "/")
    if components.get("query"):
        url += "?" + components["query"]
    if components.get("fragment"):
        url += "#" + components["fragment"]
    return url


def test_url_parser():
    result = parse_url("https://example.com:8080/path?key=val#sec")
    assert result["scheme"] == "https"
    assert result["host"] == "example.com"
    assert result["port"] == 8080
    assert result["query"] == "key=val"
    params = parse_query_string("a=1&b=2")
    assert params == {"a": "1", "b": "2"}
    return True
''',
    "http_header_builder": '''
class HTTPHeaderBuilder:
    # Builder for HTTP request/response headers.
    def __init__(self):
        self.headers = {}

    def set(self, name, value):
        self.headers[name.lower()] = str(value)
        return self

    def get(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def remove(self, name):
        self.headers.pop(name.lower(), None)
        return self

    def has(self, name):
        return name.lower() in self.headers

    def content_type(self, mime_type, charset=None):
        val = mime_type
        if charset:
            val += f"; charset={charset}"
        return self.set("content-type", val)

    def accept(self, mime_types):
        return self.set("accept", ", ".join(mime_types))

    def authorization(self, scheme, credentials):
        return self.set("authorization", f"{scheme} {credentials}")

    def to_dict(self):
        return dict(self.headers)

    def to_string(self):
        lines = []
        for name, value in self.headers.items():
            formatted_name = '-'.join(w.capitalize() for w in name.split('-'))
            lines.append(f"{formatted_name}: {value}")
        return "\\r\\n".join(lines)

    def from_string(self, header_text):
        for line in header_text.split("\\r\\n"):
            if ':' in line:
                name, value = line.split(':', 1)
                self.set(name.strip(), value.strip())
        return self


def test_http_headers():
    h = HTTPHeaderBuilder()
    h.content_type("application/json", "utf-8")
    h.set("X-Custom", "test")
    assert h.get("content-type") == "application/json; charset=utf-8"
    s = h.to_string()
    assert "Content-Type" in s
    return True
''',
    "query_string_encoder": '''
def url_encode_char(ch):
    # URL-encode a single character.
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")
    if ch in safe:
        return ch
    return "%" + format(ord(ch), "02X")


def url_encode(s):
    # URL-encode a string.
    return "".join(url_encode_char(ch) for ch in s)


def url_decode(s):
    # URL-decode a string.
    result = []
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            hex_val = s[i+1:i+3]
            try:
                result.append(chr(int(hex_val, 16)))
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


def encode_query_string(params):
    # Encode dict as URL query string.
    pairs = []
    for key, value in params.items():
        pairs.append(f"{url_encode(str(key))}={url_encode(str(value))}")
    return "&".join(pairs)


def decode_query_string(qs):
    # Decode URL query string to dict.
    if not qs:
        return {}
    result = {}
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[url_decode(k)] = url_decode(v)
    return result


def test_query_string():
    encoded = encode_query_string({"name": "John Doe", "age": "30"})
    assert "John%20Doe" in encoded
    decoded = decode_query_string("name=John%20Doe&age=30")
    assert decoded["name"] == "John Doe"
    return True
''',
    "cookie_parser": '''
def parse_cookies(cookie_header):
    # Parse Cookie header string into dict.
    cookies = {}
    if not cookie_header:
        return cookies
    pairs = cookie_header.split(';')
    for pair in pairs:
        pair = pair.strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            cookies[name.strip()] = value.strip()
    return cookies


def build_set_cookie(name, value, max_age=None, path=None, domain=None,
                     secure=False, httponly=False):
    # Build a Set-Cookie header value.
    cookie = f"{name}={value}"
    if max_age is not None:
        cookie += f"; Max-Age={max_age}"
    if path:
        cookie += f"; Path={path}"
    if domain:
        cookie += f"; Domain={domain}"
    if secure:
        cookie += "; Secure"
    if httponly:
        cookie += "; HttpOnly"
    return cookie


def parse_set_cookie(header):
    # Parse Set-Cookie header into dict with attributes.
    parts = header.split(';')
    name_val = parts[0].strip()
    if '=' not in name_val:
        return None
    name, value = name_val.split('=', 1)
    result = {"name": name.strip(), "value": value.strip(), "attributes": {}}
    for part in parts[1:]:
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            result["attributes"][k.strip().lower()] = v.strip()
        else:
            result["attributes"][part.lower()] = True
    return result


def test_cookies():
    cookies = parse_cookies("session=abc123; theme=dark; lang=en")
    assert cookies["session"] == "abc123"
    assert cookies["theme"] == "dark"
    sc = build_set_cookie("id", "xyz", max_age=3600, httponly=True)
    assert "HttpOnly" in sc
    return True
''',
    "html_entity_encoder": '''
HTML_ENTITIES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#x27;",
}

REVERSE_ENTITIES = {v: k for k, v in HTML_ENTITIES.items()}


def html_encode(text):
    # Encode special HTML characters as entities.
    result = []
    for ch in text:
        if ch in HTML_ENTITIES:
            result.append(HTML_ENTITIES[ch])
        else:
            result.append(ch)
    return "".join(result)


def html_decode(text):
    # Decode HTML entities back to characters.
    result = text
    for entity, char in REVERSE_ENTITIES.items():
        result = result.replace(entity, char)
    return result


def strip_html_tags(text):
    # Remove all HTML tags from text.
    result = []
    in_tag = False
    for ch in text:
        if ch == '<':
            in_tag = True
        elif ch == '>':
            in_tag = False
        elif not in_tag:
            result.append(ch)
    return "".join(result)


def escape_attribute(value):
    # Escape a value for use in an HTML attribute.
    return html_encode(str(value))


def test_html_entities():
    assert html_encode('<div class="x">') == '&lt;div class=&quot;x&quot;&gt;'
    assert html_decode("&lt;p&gt;") == "<p>"
    assert strip_html_tags("<b>bold</b>") == "bold"
    return True
''',
    "mime_type_resolver": '''
MIME_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".xml": "application/xml",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".py": "text/x-python",
}


def get_mime_type(filename, default="application/octet-stream"):
    # Resolve MIME type from filename extension.
    if not filename:
        return default
    dot_idx = filename.rfind('.')
    if dot_idx == -1:
        return default
    ext = filename[dot_idx:].lower()
    return MIME_TYPES.get(ext, default)


def is_text_type(mime):
    # Check if a MIME type is a text type.
    return mime.startswith("text/") or mime in ("application/json", "application/xml", "application/javascript")


def get_extension(mime_type):
    # Get file extension for a MIME type.
    for ext, mime in MIME_TYPES.items():
        if mime == mime_type:
            return ext
    return None


def categorize_mime(mime_type):
    # Categorize a MIME type into broad category.
    if mime_type.startswith("text/"):
        return "text"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "application"


def test_mime():
    assert get_mime_type("style.css") == "text/css"
    assert get_mime_type("photo.png") == "image/png"
    assert get_mime_type("noext") == "application/octet-stream"
    assert is_text_type("text/html") is True
    assert categorize_mime("image/png") == "image"
    return True
''',
    "basic_auth_encoder": '''
import base64 as _base64


def encode_basic_auth(username, password):
    # Encode username:password as Basic auth header value.
    credentials = f"{username}:{password}"
    encoded = _base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def decode_basic_auth(header_value):
    # Decode Basic auth header value to (username, password).
    if not header_value.startswith("Basic "):
        raise ValueError("not a Basic auth header")
    encoded = header_value[6:]
    decoded = _base64.b64decode(encoded).decode()
    if ':' not in decoded:
        raise ValueError("invalid credentials format")
    username, password = decoded.split(':', 1)
    return username, password


def build_auth_header(username, password):
    # Build complete Authorization header dict.
    return {"Authorization": encode_basic_auth(username, password)}


def validate_credentials(header_value, expected_user, expected_pass):
    # Validate Basic auth credentials against expected values.
    try:
        user, passwd = decode_basic_auth(header_value)
        return user == expected_user and passwd == expected_pass
    except (ValueError, Exception):
        return False


def mask_password(password, visible=2):
    # Mask password showing only first N characters.
    if len(password) <= visible:
        return password
    return password[:visible] + '*' * (len(password) - visible)


def test_basic_auth():
    encoded = encode_basic_auth("admin", "secret")
    assert encoded.startswith("Basic ")
    user, pwd = decode_basic_auth(encoded)
    assert user == "admin" and pwd == "secret"
    assert validate_credentials(encoded, "admin", "secret") is True
    assert mask_password("secret") == "se****"
    return True
''',
    "slug_generator": '''
def generate_slug(text, separator="-"):
    # Generate URL-friendly slug from text.
    result = []
    text = text.lower().strip()
    prev_sep = False
    for ch in text:
        if ch.isalnum():
            result.append(ch)
            prev_sep = False
        elif ch in (' ', '_', '-', '.'):
            if not prev_sep and result:
                result.append(separator)
                prev_sep = True
    slug = "".join(result)
    if slug.endswith(separator):
        slug = slug[:-len(separator)]
    return slug


def unique_slug(text, existing_slugs, separator="-"):
    # Generate a unique slug, appending number if needed.
    base_slug = generate_slug(text, separator)
    if base_slug not in existing_slugs:
        return base_slug
    counter = 2
    while True:
        candidate = f"{base_slug}{separator}{counter}"
        if candidate not in existing_slugs:
            return candidate
        counter += 1


def truncate_slug(slug, max_length=50, separator="-"):
    # Truncate slug to max length at word boundary.
    if len(slug) <= max_length:
        return slug
    truncated = slug[:max_length]
    last_sep = truncated.rfind(separator)
    if last_sep > 0:
        truncated = truncated[:last_sep]
    return truncated


def test_slug():
    assert generate_slug("Hello World!") == "hello-world"
    assert generate_slug("  Spaces  Everywhere  ") == "spaces-everywhere"
    assert unique_slug("test", {"test", "test-2"}) == "test-3"
    assert len(truncate_slug("a-very-long-slug-name", max_length=15)) <= 15
    return True
''',
    "pagination_helper": '''
def paginate(total_items, page, per_page=20):
    # Calculate pagination info.
    if per_page <= 0:
        per_page = 20
    total_pages = (total_items + per_page - 1) // per_page
    if total_pages == 0:
        total_pages = 1
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    limit = min(per_page, total_items - offset)
    return {
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages,
        "offset": offset,
        "limit": limit,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }


def page_range(current_page, total_pages, window=2):
    # Generate page number range around current page.
    start = max(1, current_page - window)
    end = min(total_pages, current_page + window)
    pages = list(range(start, end + 1))
    return pages


def slice_items(items, page, per_page=20):
    # Slice list of items for given page.
    info = paginate(len(items), page, per_page)
    start = info["offset"]
    end = start + info["limit"]
    return items[start:end], info


def test_pagination():
    info = paginate(100, 3, 10)
    assert info["total_pages"] == 10
    assert info["offset"] == 20
    assert info["has_prev"] is True
    assert info["has_next"] is True
    pages = page_range(5, 10, 2)
    assert pages == [3, 4, 5, 6, 7]
    items, _ = slice_items(list(range(50)), 2, 10)
    assert items == list(range(10, 20))
    return True
''',
    "cors_header_builder": '''
class CORSHeaders:
    # Builder for CORS (Cross-Origin Resource Sharing) headers.
    def __init__(self):
        self.allowed_origins = []
        self.allowed_methods = []
        self.allowed_headers = []
        self.expose_headers = []
        self.max_age = None
        self.allow_credentials = False

    def allow_origin(self, origin):
        self.allowed_origins.append(origin)
        return self

    def allow_method(self, method):
        self.allowed_methods.append(method.upper())
        return self

    def allow_header(self, header):
        self.allowed_headers.append(header)
        return self

    def expose_header(self, header):
        self.expose_headers.append(header)
        return self

    def set_max_age(self, seconds):
        self.max_age = seconds
        return self

    def credentials(self, allow=True):
        self.allow_credentials = allow
        return self

    def build(self, request_origin=None):
        headers = {}
        if "*" in self.allowed_origins:
            headers["Access-Control-Allow-Origin"] = "*"
        elif request_origin and request_origin in self.allowed_origins:
            headers["Access-Control-Allow-Origin"] = request_origin
        if self.allowed_methods:
            headers["Access-Control-Allow-Methods"] = ", ".join(self.allowed_methods)
        if self.allowed_headers:
            headers["Access-Control-Allow-Headers"] = ", ".join(self.allowed_headers)
        if self.expose_headers:
            headers["Access-Control-Expose-Headers"] = ", ".join(self.expose_headers)
        if self.max_age is not None:
            headers["Access-Control-Max-Age"] = str(self.max_age)
        if self.allow_credentials:
            headers["Access-Control-Allow-Credentials"] = "true"
        return headers


def test_cors():
    cors = CORSHeaders()
    cors.allow_origin("https://example.com")
    cors.allow_method("GET").allow_method("POST")
    cors.allow_header("Content-Type")
    cors.set_max_age(3600)
    headers = cors.build("https://example.com")
    assert headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert "GET" in headers["Access-Control-Allow-Methods"]
    return True
''',
    "base64_codec": '''
CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def b64_encode(data):
    # Base64-like encode for byte string.
    if isinstance(data, str):
        data = data.encode()
    result = []
    padding = 0
    i = 0
    while i < len(data):
        b0 = data[i]
        b1 = data[i+1] if i+1 < len(data) else 0
        b2 = data[i+2] if i+2 < len(data) else 0
        triple = (b0 << 16) | (b1 << 8) | b2
        result.append(CHARSET[(triple >> 18) & 0x3F])
        result.append(CHARSET[(triple >> 12) & 0x3F])
        if i + 1 < len(data):
            result.append(CHARSET[(triple >> 6) & 0x3F])
        else:
            result.append('=')
        if i + 2 < len(data):
            result.append(CHARSET[triple & 0x3F])
        else:
            result.append('=')
        i += 3
    return "".join(result)


def b64_decode(encoded):
    # Base64-like decode to bytes.
    lookup = {c: i for i, c in enumerate(CHARSET)}
    result = bytearray()
    encoded = encoded.rstrip('=')
    padding = (4 - len(encoded) % 4) % 4
    encoded += 'A' * padding
    for i in range(0, len(encoded), 4):
        vals = [lookup.get(encoded[j], 0) for j in range(i, i+4)]
        triple = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6) | vals[3]
        result.append((triple >> 16) & 0xFF)
        result.append((triple >> 8) & 0xFF)
        result.append(triple & 0xFF)
    trim = padding if padding else 0
    if trim:
        result = result[:-trim]
    return bytes(result)


def test_b64():
    encoded = b64_encode("Hello")
    assert isinstance(encoded, str)
    decoded = b64_decode(encoded)
    assert decoded == b"Hello"
    return True
''',
    "huffman_tree": '''
class HuffmanNode:
    # Node in a Huffman tree.
    def __init__(self, char=None, freq=0, left=None, right=None):
        self.char = char
        self.freq = freq
        self.left = left
        self.right = right


def build_frequency_table(text):
    # Count character frequencies in text.
    freq = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    return freq


def build_huffman_tree(freq_table):
    # Build Huffman tree from frequency table.
    nodes = [HuffmanNode(ch, f) for ch, f in freq_table.items()]
    while len(nodes) > 1:
        nodes.sort(key=lambda n: n.freq)
        left = nodes.pop(0)
        right = nodes.pop(0)
        parent = HuffmanNode(freq=left.freq + right.freq, left=left, right=right)
        nodes.append(parent)
    return nodes[0] if nodes else None


def build_code_table(root):
    # Build code table mapping characters to binary strings.
    codes = {}
    def traverse(node, prefix):
        if node is None:
            return
        if node.char is not None:
            codes[node.char] = prefix if prefix else "0"
            return
        traverse(node.left, prefix + "0")
        traverse(node.right, prefix + "1")
    traverse(root, "")
    return codes


def huffman_encode(text):
    # Encode text using Huffman coding.
    freq = build_frequency_table(text)
    tree = build_huffman_tree(freq)
    codes = build_code_table(tree)
    encoded = "".join(codes[ch] for ch in text)
    return encoded, tree, codes


def test_huffman():
    text = "aabbbcccc"
    encoded, tree, codes = huffman_encode(text)
    assert len(encoded) > 0
    assert len(codes) == 3
    assert tree is not None
    return True
''',
    "lz77_compress": '''
def lz77_compress(data, window_size=20, lookahead_size=15):
    # Simple LZ77-like compression returning list of (offset, length, next_char) triples.
    result = []
    i = 0
    while i < len(data):
        best_offset = 0
        best_length = 0
        start = max(0, i - window_size)
        for j in range(start, i):
            length = 0
            while (length < lookahead_size and
                   i + length < len(data) and
                   data[j + length] == data[i + length]):
                length += 1
                if j + length >= i:
                    break
            if length > best_length:
                best_length = length
                best_offset = i - j
        next_char = data[i + best_length] if i + best_length < len(data) else ""
        result.append((best_offset, best_length, next_char))
        i += best_length + 1
    return result


def lz77_decompress(compressed):
    # Decompress LZ77-compressed data.
    result = []
    for offset, length, next_char in compressed:
        if length > 0:
            start = len(result) - offset
            for j in range(length):
                result.append(result[start + j])
        if next_char:
            result.append(next_char)
    return "".join(result)


def compression_ratio(original, compressed):
    # Calculate compression ratio.
    orig_size = len(original)
    comp_size = len(compressed) * 3
    if orig_size == 0:
        return 1.0
    return round(comp_size / orig_size, 4)


def test_lz77():
    data = "aabcaabcaabc"
    compressed = lz77_compress(data)
    decompressed = lz77_decompress(compressed)
    assert decompressed == data
    ratio = compression_ratio(data, compressed)
    assert ratio > 0
    return True
''',
    "caesar_cipher": '''
def caesar_encrypt(text, shift):
    # Encrypt text using Caesar cipher with given shift.
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord('A') if ch.isupper() else ord('a')
            encrypted = chr((ord(ch) - base + shift) % 26 + base)
            result.append(encrypted)
        else:
            result.append(ch)
    return "".join(result)


def caesar_decrypt(text, shift):
    # Decrypt Caesar cipher text.
    return caesar_encrypt(text, -shift)


def caesar_brute_force(ciphertext):
    # Try all 26 shifts and return all possibilities.
    results = []
    for shift in range(26):
        decrypted = caesar_decrypt(ciphertext, shift)
        results.append({"shift": shift, "text": decrypted})
    return results


def detect_caesar_shift(ciphertext):
    # Detect most likely Caesar shift using letter frequency.
    english_freq = {'e': 12.7, 't': 9.1, 'a': 8.2, 'o': 7.5, 'i': 7.0,
                    'n': 6.7, 's': 6.3, 'h': 6.1, 'r': 6.0}
    best_shift = 0
    best_score = -1
    for shift in range(26):
        decrypted = caesar_decrypt(ciphertext, shift)
        score = sum(1 for ch in decrypted.lower() if ch in english_freq)
        if score > best_score:
            best_score = score
            best_shift = shift
    return best_shift


def test_caesar():
    encrypted = caesar_encrypt("Hello World", 3)
    assert encrypted == "Khoor Zruog"
    decrypted = caesar_decrypt(encrypted, 3)
    assert decrypted == "Hello World"
    results = caesar_brute_force("Khoor")
    assert any(r["text"] == "Hello" for r in results)
    return True
''',
    "vigenere_cipher": '''
def vigenere_encrypt(plaintext, key):
    # Encrypt using Vigenere cipher.
    if not key:
        raise ValueError("key must not be empty")
    key = key.upper()
    result = []
    ki = 0
    for ch in plaintext:
        if ch.isalpha():
            shift = ord(key[ki % len(key)]) - ord('A')
            base = ord('A') if ch.isupper() else ord('a')
            encrypted = chr((ord(ch) - base + shift) % 26 + base)
            result.append(encrypted)
            ki += 1
        else:
            result.append(ch)
    return "".join(result)


def vigenere_decrypt(ciphertext, key):
    # Decrypt Vigenere cipher.
    if not key:
        raise ValueError("key must not be empty")
    key = key.upper()
    result = []
    ki = 0
    for ch in ciphertext:
        if ch.isalpha():
            shift = ord(key[ki % len(key)]) - ord('A')
            base = ord('A') if ch.isupper() else ord('a')
            decrypted = chr((ord(ch) - base - shift) % 26 + base)
            result.append(decrypted)
            ki += 1
        else:
            result.append(ch)
    return "".join(result)


def estimate_key_length(ciphertext, max_len=20):
    # Estimate key length using index of coincidence.
    text = [c for c in ciphertext.upper() if c.isalpha()]
    best_len = 1
    best_ic = 0
    for kl in range(1, min(max_len, len(text) // 2) + 1):
        total_ic = 0
        for offset in range(kl):
            group = [text[i] for i in range(offset, len(text), kl)]
            n = len(group)
            if n < 2:
                continue
            freq = {}
            for c in group:
                freq[c] = freq.get(c, 0) + 1
            ic = sum(f * (f-1) for f in freq.values()) / (n * (n - 1))
            total_ic += ic
        avg_ic = total_ic / kl
        if avg_ic > best_ic:
            best_ic = avg_ic
            best_len = kl
    return best_len


def test_vigenere():
    encrypted = vigenere_encrypt("Hello World", "KEY")
    decrypted = vigenere_decrypt(encrypted, "KEY")
    assert decrypted == "Hello World"
    return True
''',
    "xor_cipher": '''
def xor_encrypt(data, key):
    # Encrypt data using XOR cipher with repeating key.
    if isinstance(data, str):
        data = data.encode()
    if isinstance(key, str):
        key = key.encode()
    if not key:
        raise ValueError("key must not be empty")
    result = bytearray()
    for i, byte in enumerate(data):
        result.append(byte ^ key[i % len(key)])
    return bytes(result)


def xor_decrypt(data, key):
    # Decrypt XOR cipher (same as encrypt due to XOR properties).
    return xor_encrypt(data, key)


def xor_encrypt_hex(plaintext, key):
    # Encrypt and return result as hex string.
    encrypted = xor_encrypt(plaintext, key)
    return encrypted.hex()


def xor_decrypt_hex(hex_string, key):
    # Decrypt from hex string.
    data = bytes.fromhex(hex_string)
    return xor_decrypt(data, key)


def single_byte_xor_crack(ciphertext):
    # Try all single-byte XOR keys and score by printable character ratio.
    if isinstance(ciphertext, str):
        ciphertext = bytes.fromhex(ciphertext)
    best_key = 0
    best_score = -1
    for key in range(256):
        decrypted = bytes([b ^ key for b in ciphertext])
        score = sum(1 for b in decrypted if 32 <= b <= 126)
        if score > best_score:
            best_score = score
            best_key = key
    return best_key


def test_xor():
    data = b"Hello World"
    key = b"secret"
    encrypted = xor_encrypt(data, key)
    decrypted = xor_decrypt(encrypted, key)
    assert decrypted == data
    hex_enc = xor_encrypt_hex("test", "k")
    dec = xor_decrypt_hex(hex_enc, "k")
    assert dec == b"test"
    return True
''',
    "morse_code": '''
MORSE_TABLE = {
    'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
    'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
    'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
    'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
    'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
    'Z': '--..', '0': '-----', '1': '.----', '2': '..---',
    '3': '...--', '4': '....-', '5': '.....', '6': '-....',
    '7': '--...', '8': '---..', '9': '----.', ' ': '/',
}

REVERSE_MORSE = {v: k for k, v in MORSE_TABLE.items()}


def morse_encode(text):
    # Encode text to Morse code.
    result = []
    for ch in text.upper():
        if ch in MORSE_TABLE:
            result.append(MORSE_TABLE[ch])
    return ' '.join(result)


def morse_decode(morse):
    # Decode Morse code to text.
    result = []
    for code in morse.split(' '):
        if code in REVERSE_MORSE:
            result.append(REVERSE_MORSE[code])
        elif code == '':
            continue
    return ''.join(result)


def is_valid_morse(morse):
    # Check if a string is valid Morse code.
    valid_chars = set('.-/ ')
    return all(c in valid_chars for c in morse)


def test_morse():
    encoded = morse_encode("SOS")
    assert encoded == "... --- ..."
    decoded = morse_decode("... --- ...")
    assert decoded == "SOS"
    assert is_valid_morse("... --- ...") is True
    return True
''',
    "binary_converter": '''
def decimal_to_binary(n):
    # Convert decimal integer to binary string.
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
    # Convert binary string to decimal integer.
    negative = binary_str.startswith("-")
    if negative:
        binary_str = binary_str[1:]
    result = 0
    for bit in binary_str:
        result = result * 2 + int(bit)
    return -result if negative else result


def decimal_to_hex(n):
    # Convert decimal to hexadecimal string.
    if n == 0:
        return "0"
    negative = n < 0
    n = abs(n)
    hex_chars = "0123456789abcdef"
    digits = []
    while n > 0:
        digits.append(hex_chars[n % 16])
        n //= 16
    result = "".join(reversed(digits))
    return "-" + result if negative else result


def hex_to_decimal(hex_str):
    # Convert hexadecimal string to decimal.
    return int(hex_str, 16)


def binary_to_hex(binary_str):
    # Convert binary string to hexadecimal.
    decimal = binary_to_decimal(binary_str)
    return decimal_to_hex(decimal)


def test_converter():
    assert decimal_to_binary(10) == "1010"
    assert binary_to_decimal("1010") == 10
    assert decimal_to_hex(255) == "ff"
    assert hex_to_decimal("ff") == 255
    assert binary_to_hex("11111111") == "ff"
    return True
''',
    "checksum_calculator": '''
def simple_checksum(data):
    # Compute simple additive checksum of byte data.
    if isinstance(data, str):
        data = data.encode()
    total = 0
    for byte in data:
        total = (total + byte) & 0xFF
    return total


def fletcher16(data):
    # Compute Fletcher-16 checksum.
    if isinstance(data, str):
        data = data.encode()
    sum1 = 0
    sum2 = 0
    for byte in data:
        sum1 = (sum1 + byte) % 255
        sum2 = (sum2 + sum1) % 255
    return (sum2 << 8) | sum1


def xor_checksum(data):
    # Compute XOR checksum.
    if isinstance(data, str):
        data = data.encode()
    result = 0
    for byte in data:
        result ^= byte
    return result


def verify_checksum(data, expected, method="simple"):
    # Verify data against expected checksum.
    if method == "simple":
        actual = simple_checksum(data)
    elif method == "fletcher16":
        actual = fletcher16(data)
    elif method == "xor":
        actual = xor_checksum(data)
    else:
        raise ValueError(f"unknown method: {method}")
    return actual == expected


def internet_checksum(data):
    # Compute Internet checksum (RFC 1071 style).
    if isinstance(data, str):
        data = data.encode()
    total = 0
    for i in range(0, len(data) - 1, 2):
        word = (data[i] << 8) + data[i + 1]
        total += word
    if len(data) % 2:
        total += data[-1] << 8
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def test_checksum():
    assert simple_checksum("abc") == (97 + 98 + 99) & 0xFF
    f16 = fletcher16("abcde")
    assert f16 > 0
    assert xor_checksum("AA") == 0
    assert verify_checksum("abc", simple_checksum("abc"), "simple") is True
    return True
''',
    "rot13_codec": '''
def rot13(text):
    # Apply ROT13 transformation to text.
    result = []
    for ch in text:
        if 'a' <= ch <= 'z':
            result.append(chr((ord(ch) - ord('a') + 13) % 26 + ord('a')))
        elif 'A' <= ch <= 'Z':
            result.append(chr((ord(ch) - ord('A') + 13) % 26 + ord('A')))
        else:
            result.append(ch)
    return "".join(result)


def rot_n(text, n):
    # Apply ROT-N transformation (generalized ROT13).
    result = []
    for ch in text:
        if 'a' <= ch <= 'z':
            result.append(chr((ord(ch) - ord('a') + n) % 26 + ord('a')))
        elif 'A' <= ch <= 'Z':
            result.append(chr((ord(ch) - ord('A') + n) % 26 + ord('A')))
        else:
            result.append(ch)
    return "".join(result)


def rot47(text):
    # Apply ROT47 transformation for printable ASCII.
    result = []
    for ch in text:
        code = ord(ch)
        if 33 <= code <= 126:
            result.append(chr(33 + (code - 33 + 47) % 94))
        else:
            result.append(ch)
    return "".join(result)


def is_rot13_pair(text1, text2):
    # Check if two strings are ROT13 pairs.
    return rot13(text1) == text2


def test_rot13():
    assert rot13("Hello") == "Uryyb"
    assert rot13(rot13("Hello")) == "Hello"
    assert rot_n("abc", 1) == "bcd"
    assert is_rot13_pair("Hello", "Uryyb") is True
    assert rot47(rot47("Test!")) == "Test!"
    return True
''',
    "game_of_life": '''
def create_grid(rows, cols, alive_cells=None):
    # Create Game of Life grid with optional initial alive cells.
    grid = [[0] * cols for _ in range(rows)]
    if alive_cells:
        for r, c in alive_cells:
            if 0 <= r < rows and 0 <= c < cols:
                grid[r][c] = 1
    return grid


def count_neighbors(grid, row, col):
    # Count live neighbors for a cell.
    rows, cols = len(grid), len(grid[0])
    count = 0
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            r, c = row + dr, col + dc
            if 0 <= r < rows and 0 <= c < cols:
                count += grid[r][c]
    return count


def step(grid):
    # Compute one step of Conway's Game of Life.
    rows, cols = len(grid), len(grid[0])
    new_grid = [[0] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            neighbors = count_neighbors(grid, r, c)
            if grid[r][c] == 1:
                if neighbors in (2, 3):
                    new_grid[r][c] = 1
            else:
                if neighbors == 3:
                    new_grid[r][c] = 1
    return new_grid


def count_alive(grid):
    # Count total alive cells.
    return sum(cell for row in grid for cell in row)


def run_simulation(grid, steps):
    # Run multiple steps and return final grid.
    current = grid
    for _ in range(steps):
        current = step(current)
    return current


def test_game_of_life():
    blinker = create_grid(5, 5, [(2, 1), (2, 2), (2, 3)])
    next_gen = step(blinker)
    assert next_gen[1][2] == 1
    assert next_gen[2][2] == 1
    assert next_gen[3][2] == 1
    assert count_alive(next_gen) == 3
    return True
''',
    "maze_solver": '''
from collections import deque as maze_deque


def solve_maze(maze, start, end):
    # Solve maze using BFS. maze is 2D grid, 0=path, 1=wall.
    rows, cols = len(maze), len(maze[0])
    visited = set()
    visited.add(start)
    queue = maze_deque([(start, [start])])
    while queue:
        (r, c), path = queue.popleft()
        if (r, c) == end:
            return path
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if (0 <= nr < rows and 0 <= nc < cols and
                    maze[nr][nc] == 0 and (nr, nc) not in visited):
                visited.add((nr, nc))
                queue.append(((nr, nc), path + [(nr, nc)]))
    return None


def print_maze_with_path(maze, path):
    # Render maze with path marked as dots.
    path_set = set(path) if path else set()
    lines = []
    for r in range(len(maze)):
        row_str = ""
        for c in range(len(maze[0])):
            if (r, c) in path_set:
                row_str += "."
            elif maze[r][c] == 1:
                row_str += "#"
            else:
                row_str += " "
        lines.append(row_str)
    return "\\n".join(lines)


def generate_empty_maze(rows, cols):
    # Generate an empty maze (all paths).
    return [[0] * cols for _ in range(rows)]


def test_maze_solver():
    maze = [
        [0, 0, 1, 0],
        [1, 0, 1, 0],
        [0, 0, 0, 0],
        [0, 1, 1, 0],
    ]
    path = solve_maze(maze, (0, 0), (3, 3))
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (3, 3)
    rendered = print_maze_with_path(maze, path)
    assert "." in rendered
    return True
''',
    "sudoku_validator": '''
def validate_sudoku(board):
    # Validate a completed 9x9 Sudoku board.
    if len(board) != 9:
        return False, "board must have 9 rows"
    for i, row in enumerate(board):
        if len(row) != 9:
            return False, f"row {i} must have 9 columns"
    for i, row in enumerate(board):
        seen = set()
        for val in row:
            if val < 1 or val > 9:
                return False, f"invalid value {val} in row {i}"
            if val in seen:
                return False, f"duplicate {val} in row {i}"
            seen.add(val)
    for col in range(9):
        seen = set()
        for row in range(9):
            val = board[row][col]
            if val in seen:
                return False, f"duplicate {val} in column {col}"
            seen.add(val)
    for box_r in range(3):
        for box_c in range(3):
            seen = set()
            for r in range(3):
                for c in range(3):
                    val = board[box_r * 3 + r][box_c * 3 + c]
                    if val in seen:
                        return False, f"duplicate {val} in box ({box_r},{box_c})"
                    seen.add(val)
    return True, "valid"


def find_empty(board):
    # Find first empty cell (0) in board.
    for r in range(9):
        for c in range(9):
            if board[r][c] == 0:
                return (r, c)
    return None


def is_valid_placement(board, row, col, num):
    # Check if placing num at (row, col) is valid.
    if num in board[row]:
        return False
    if any(board[r][col] == num for r in range(9)):
        return False
    br, bc = 3 * (row // 3), 3 * (col // 3)
    for r in range(br, br + 3):
        for c in range(bc, bc + 3):
            if board[r][c] == num:
                return False
    return True


def test_sudoku():
    valid_board = [
        [5,3,4,6,7,8,9,1,2],[6,7,2,1,9,5,3,4,8],[1,9,8,3,4,2,5,6,7],
        [8,5,9,7,6,1,4,2,3],[4,2,6,8,5,3,7,9,1],[7,1,3,9,2,4,8,5,6],
        [9,6,1,5,3,7,2,8,4],[2,8,7,4,1,9,6,3,5],[3,4,5,2,8,6,1,7,9],
    ]
    ok, msg = validate_sudoku(valid_board)
    assert ok is True
    return True
''',
    "tic_tac_toe": '''
class TicTacToe:
    # Tic-tac-toe game with move validation and win detection.
    def __init__(self):
        self.board = [[" "] * 3 for _ in range(3)]
        self.current_player = "X"
        self.moves = 0

    def make_move(self, row, col):
        if row < 0 or row > 2 or col < 0 or col > 2:
            return False, "out of bounds"
        if self.board[row][col] != " ":
            return False, "cell occupied"
        if self.get_winner():
            return False, "game already over"
        self.board[row][col] = self.current_player
        self.moves += 1
        self.current_player = "O" if self.current_player == "X" else "X"
        return True, "ok"

    def get_winner(self):
        for row in self.board:
            if row[0] == row[1] == row[2] != " ":
                return row[0]
        for col in range(3):
            if self.board[0][col] == self.board[1][col] == self.board[2][col] != " ":
                return self.board[0][col]
        if self.board[0][0] == self.board[1][1] == self.board[2][2] != " ":
            return self.board[0][0]
        if self.board[0][2] == self.board[1][1] == self.board[2][0] != " ":
            return self.board[0][2]
        return None

    def is_draw(self):
        return self.moves == 9 and self.get_winner() is None

    def available_moves(self):
        moves = []
        for r in range(3):
            for c in range(3):
                if self.board[r][c] == " ":
                    moves.append((r, c))
        return moves

    def display(self):
        lines = []
        for row in self.board:
            lines.append("|".join(row))
        return "\\n-----\\n".join(lines)


def test_tic_tac_toe():
    game = TicTacToe()
    game.make_move(0, 0)
    game.make_move(1, 0)
    game.make_move(0, 1)
    game.make_move(1, 1)
    game.make_move(0, 2)
    assert game.get_winner() == "X"
    assert len(game.available_moves()) == 4
    return True
''',
    "calendar_generator": '''
def is_leap_year_cal(year):
    # Check if year is a leap year.
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def days_in_month_cal(year, month):
    # Return days in given month.
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year_cal(year):
        return 29
    return days[month]


def day_of_week(year, month, day):
    # Calculate day of week using Zeller-like formula (0=Sun, 6=Sat).
    if month < 3:
        month += 12
        year -= 1
    k = year % 100
    j = year // 100
    h = (day + (13 * (month + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7
    return ((h + 6) % 7)


def generate_calendar(year, month):
    # Generate text calendar for a given month.
    month_names = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
    header = f"    {month_names[month]} {year}"
    days_header = "Su Mo Tu We Th Fr Sa"
    first_day = day_of_week(year, month, 1)
    num_days = days_in_month_cal(year, month)
    lines = [header, days_header]
    line = "   " * first_day
    for day in range(1, num_days + 1):
        line += f"{day:2d} "
        if (first_day + day) % 7 == 0:
            lines.append(line.rstrip())
            line = ""
    if line.strip():
        lines.append(line.rstrip())
    return "\\n".join(lines)


def year_calendar(year):
    # Generate calendar for entire year.
    months = []
    for month in range(1, 13):
        months.append(generate_calendar(year, month))
    return "\\n\\n".join(months)


def test_calendar():
    cal = generate_calendar(2024, 1)
    assert "January 2024" in cal
    assert "Su Mo" in cal
    dow = day_of_week(2024, 1, 1)
    assert dow == 1
    return True
''',
}

# ── 50 buggy programs ────────────────────────────────────────────────

BUGGY_PROGRAMS = [
    {
        "id": "buggy_binary_search_obo",
        "bug_description": "Off-by-one: uses < instead of <= in while loop",
        "source": '''
def binary_search(arr, target):
    # Binary search with off-by-one bug: < instead of <=.
    left = 0
    right = len(arr) - 1
    while left < right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1


def search_all(arr, targets):
    # Search for multiple targets.
    results = {}
    for t in targets:
        results[t] = binary_search(arr, t)
    return results


def test():
    arr = [1, 3, 5, 7, 9]
    assert binary_search(arr, 5) == 2
    return True
''',
        "fixed_source": '''
def binary_search(arr, target):
    # Binary search with correct bounds check.
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


def search_all(arr, targets):
    # Search for multiple targets.
    results = {}
    for t in targets:
        results[t] = binary_search(arr, t)
    return results


def test():
    arr = [1, 3, 5, 7, 9]
    assert binary_search(arr, 5) == 2
    return True
''',
    },
    {
        "id": "buggy_merge_sort_slice",
        "bug_description": "Wrong slice index: uses mid-1 instead of mid",
        "source": '''
def merge(left, right):
    # Merge two sorted lists.
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
    # Merge sort with wrong slice index.
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid - 1])
    right = merge_sort(arr[mid:])
    return merge(left, right)


def test():
    assert merge_sort([3, 1, 2]) == [1, 2, 3]
    return True
''',
        "fixed_source": '''
def merge(left, right):
    # Merge two sorted lists.
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
    # Merge sort with correct slice index.
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)


def test():
    assert merge_sort([3, 1, 2]) == [1, 2, 3]
    return True
''',
    },
    {
        "id": "buggy_stack_underflow",
        "bug_description": "Missing empty check in stack pop",
        "source": '''
class Stack:
    # Stack without empty check on pop.
    def __init__(self):
        self.items = []
        self.size = 0

    def push(self, item):
        self.items.append(item)
        self.size += 1

    def pop(self):
        self.size -= 1
        return self.items.pop()

    def peek(self):
        return self.items[-1]

    def is_empty(self):
        return self.size == 0

    def __len__(self):
        return self.size


def test():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.pop() == 2
    return True
''',
        "fixed_source": '''
class Stack:
    # Stack with proper empty check on pop.
    def __init__(self):
        self.items = []
        self.size = 0

    def push(self, item):
        self.items.append(item)
        self.size += 1

    def pop(self):
        if self.is_empty():
            raise IndexError("pop from empty stack")
        self.size -= 1
        return self.items.pop()

    def peek(self):
        if self.is_empty():
            raise IndexError("peek at empty stack")
        return self.items[-1]

    def is_empty(self):
        return self.size == 0

    def __len__(self):
        return self.size


def test():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.pop() == 2
    return True
''',
    },
    {
        "id": "buggy_linked_list_delete",
        "bug_description": "Does not handle deleting head node",
        "source": '''
class Node:
    def __init__(self, data):
        self.data = data
        self.next = None


class LinkedList:
    def __init__(self):
        self.head = None

    def insert(self, data):
        node = Node(data)
        node.next = self.head
        self.head = node

    def delete(self, data):
        # Bug: skips head node check.
        current = self.head
        while current.next is not None:
            if current.next.data == data:
                current.next = current.next.next
                return True
            current = current.next
        return False

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.data)
            current = current.next
        return result


def test():
    ll = LinkedList()
    ll.insert(3)
    ll.insert(2)
    ll.insert(1)
    assert ll.to_list() == [1, 2, 3]
    return True
''',
        "fixed_source": '''
class Node:
    def __init__(self, data):
        self.data = data
        self.next = None


class LinkedList:
    def __init__(self):
        self.head = None

    def insert(self, data):
        node = Node(data)
        node.next = self.head
        self.head = node

    def delete(self, data):
        # Fixed: handles head node deletion.
        if self.head is None:
            return False
        if self.head.data == data:
            self.head = self.head.next
            return True
        current = self.head
        while current.next is not None:
            if current.next.data == data:
                current.next = current.next.next
                return True
            current = current.next
        return False

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.data)
            current = current.next
        return result


def test():
    ll = LinkedList()
    ll.insert(3)
    ll.insert(2)
    ll.insert(1)
    assert ll.to_list() == [1, 2, 3]
    return True
''',
    },
    {
        "id": "buggy_gcd_zero",
        "bug_description": "Does not handle zero input in GCD",
        "source": '''
def gcd(a, b):
    # GCD without handling zero — will infinite loop on gcd(0,0).
    while b != 0:
        a, b = b, a % b
    return a


def lcm(a, b):
    # Compute LCM using GCD.
    return abs(a * b) // gcd(a, b)


def gcd_list(numbers):
    # Compute GCD of list.
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result


def test():
    assert gcd(12, 8) == 4
    assert lcm(4, 6) == 12
    assert gcd_list([12, 18, 24]) == 6
    return True
''',
        "fixed_source": '''
def gcd(a, b):
    # GCD with proper zero handling.
    a, b = abs(a), abs(b)
    if a == 0 and b == 0:
        return 0
    while b != 0:
        a, b = b, a % b
    return a


def lcm(a, b):
    # Compute LCM using GCD.
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def gcd_list(numbers):
    # Compute GCD of list.
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result


def test():
    assert gcd(12, 8) == 4
    assert lcm(4, 6) == 12
    assert gcd(0, 5) == 5
    return True
''',
    },
    {
        "id": "buggy_factorial_base",
        "bug_description": "Wrong base case: returns 0 instead of 1",
        "source": '''
def factorial(n):
    # Factorial with wrong base case.
    if n < 0:
        raise ValueError("negative input")
    if n == 0:
        return 0
    return n * factorial(n - 1)


def double_factorial(n):
    # Double factorial n!! computation.
    if n <= 1:
        return 1
    return n * double_factorial(n - 2)


def test():
    assert factorial(5) == 120
    assert factorial(1) == 1
    return True
''',
        "fixed_source": '''
def factorial(n):
    # Factorial with correct base case.
    if n < 0:
        raise ValueError("negative input")
    if n == 0:
        return 1
    return n * factorial(n - 1)


def double_factorial(n):
    # Double factorial n!! computation.
    if n <= 1:
        return 1
    return n * double_factorial(n - 2)


def test():
    assert factorial(5) == 120
    assert factorial(0) == 1
    return True
''',
    },
    {
        "id": "buggy_fibonacci_off",
        "bug_description": "Off-by-one: returns fib(n-1) instead of fib(n)",
        "source": '''
def fibonacci(n):
    # Fibonacci with off-by-one error.
    if n <= 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n):
        a, b = b, a + b
    return b


def fib_list(count):
    # Generate list of first count fibonacci numbers.
    return [fibonacci(i) for i in range(count)]


def test():
    assert fibonacci(10) == 55
    assert fibonacci(1) == 1
    return True
''',
        "fixed_source": '''
def fibonacci(n):
    # Fibonacci with correct range.
    if n <= 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def fib_list(count):
    # Generate list of first count fibonacci numbers.
    return [fibonacci(i) for i in range(count)]


def test():
    assert fibonacci(10) == 55
    assert fibonacci(1) == 1
    return True
''',
    },
    {
        "id": "buggy_matrix_index",
        "bug_description": "Wrong index variable in nested loop: uses i instead of j",
        "source": '''
def matrix_multiply(a, b):
    # Matrix multiply with index bug: result[i][j] uses a[i][k]*b[k][i].
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][i]
    return result


def identity(n):
    # Create identity matrix.
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]


def test():
    a = [[1, 2], [3, 4]]
    b = [[5, 6], [7, 8]]
    r = matrix_multiply(a, b)
    assert r == [[19, 22], [43, 50]]
    return True
''',
        "fixed_source": '''
def matrix_multiply(a, b):
    # Matrix multiply with correct indices.
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result


def identity(n):
    # Create identity matrix.
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]


def test():
    a = [[1, 2], [3, 4]]
    b = [[5, 6], [7, 8]]
    r = matrix_multiply(a, b)
    assert r == [[19, 22], [43, 50]]
    return True
''',
    },
    {
        "id": "buggy_quicksort_pivot",
        "bug_description": "Wrong pivot comparison: >= instead of <=",
        "source": '''
def partition(arr, low, high):
    # Partition with wrong comparison.
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] >= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def quicksort(arr, low=None, high=None):
    # Quicksort implementation.
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low < high:
        pi = partition(arr, low, high)
        quicksort(arr, low, pi - 1)
        quicksort(arr, pi + 1, high)
    return arr


def test():
    assert quicksort([3, 1, 2]) == [1, 2, 3]
    return True
''',
        "fixed_source": '''
def partition(arr, low, high):
    # Partition with correct comparison.
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def quicksort(arr, low=None, high=None):
    # Quicksort implementation.
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low < high:
        pi = partition(arr, low, high)
        quicksort(arr, low, pi - 1)
        quicksort(arr, pi + 1, high)
    return arr


def test():
    assert quicksort([3, 1, 2]) == [1, 2, 3]
    return True
''',
    },
    {
        "id": "buggy_bst_insert",
        "bug_description": "Missing return in recursive insert",
        "source": '''
class TreeNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None


class BST:
    def __init__(self):
        self.root = None

    def insert(self, key):
        self.root = self._insert(self.root, key)

    def _insert(self, node, key):
        # Bug: missing return node at end.
        if node is None:
            return TreeNode(key)
        if key < node.key:
            node.left = self._insert(node.left, key)
        elif key > node.key:
            node.right = self._insert(node.right, key)

    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)


def test():
    t = BST()
    t.insert(5)
    t.insert(3)
    t.insert(7)
    return True
''',
        "fixed_source": '''
class TreeNode:
    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None


class BST:
    def __init__(self):
        self.root = None

    def insert(self, key):
        self.root = self._insert(self.root, key)

    def _insert(self, node, key):
        # Fixed: returns node at end.
        if node is None:
            return TreeNode(key)
        if key < node.key:
            node.left = self._insert(node.left, key)
        elif key > node.key:
            node.right = self._insert(node.right, key)
        return node

    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)


def test():
    t = BST()
    t.insert(5)
    t.insert(3)
    t.insert(7)
    return True
''',
    },
    {
        "id": "buggy_hash_collision",
        "bug_description": "Does not handle collision: overwrites on same hash bucket",
        "source": '''
class SimpleHash:
    def __init__(self, size=10):
        self.size = size
        self.table = [None] * size

    def _hash(self, key):
        return hash(key) % self.size

    def put(self, key, value):
        # Bug: no chaining, overwrites on collision.
        idx = self._hash(key)
        self.table[idx] = (key, value)

    def get(self, key):
        idx = self._hash(key)
        if self.table[idx] is not None:
            return self.table[idx][1]
        return None

    def delete(self, key):
        idx = self._hash(key)
        self.table[idx] = None


def test():
    h = SimpleHash(10)
    h.put("a", 1)
    h.put("b", 2)
    assert h.get("a") == 1
    return True
''',
        "fixed_source": '''
class SimpleHash:
    def __init__(self, size=10):
        self.size = size
        self.table = [[] for _ in range(size)]

    def _hash(self, key):
        return hash(key) % self.size

    def put(self, key, value):
        # Fixed: uses chaining for collisions.
        idx = self._hash(key)
        for i, (k, v) in enumerate(self.table[idx]):
            if k == key:
                self.table[idx][i] = (key, value)
                return
        self.table[idx].append((key, value))

    def get(self, key):
        idx = self._hash(key)
        for k, v in self.table[idx]:
            if k == key:
                return v
        return None

    def delete(self, key):
        idx = self._hash(key)
        self.table[idx] = [(k, v) for k, v in self.table[idx] if k != key]


def test():
    h = SimpleHash(10)
    h.put("a", 1)
    h.put("b", 2)
    assert h.get("a") == 1
    return True
''',
    },
    {
        "id": "buggy_palindrome_case",
        "bug_description": "Case-sensitive palindrome check should be insensitive",
        "source": '''
def is_palindrome(s):
    # Bug: case-sensitive check.
    cleaned = "".join(c for c in s if c.isalnum())
    return cleaned == cleaned[::-1]


def longest_palindrome_substr(s):
    # Find longest palindromic substring.
    best = ""
    for i in range(len(s)):
        for j in range(i + 1, len(s) + 1):
            sub = s[i:j]
            if is_palindrome(sub) and len(sub) > len(best):
                best = sub
    return best


def test():
    assert is_palindrome("racecar") is True
    assert is_palindrome("hello") is False
    return True
''',
        "fixed_source": '''
def is_palindrome(s):
    # Fixed: case-insensitive check.
    cleaned = "".join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]


def longest_palindrome_substr(s):
    # Find longest palindromic substring.
    best = ""
    for i in range(len(s)):
        for j in range(i + 1, len(s) + 1):
            sub = s[i:j]
            if is_palindrome(sub) and len(sub) > len(best):
                best = sub
    return best


def test():
    assert is_palindrome("Racecar") is True
    assert is_palindrome("hello") is False
    return True
''',
    },
    {
        "id": "buggy_average_int_div",
        "bug_description": "Integer division instead of float division",
        "source": '''
def average(numbers):
    # Bug: integer division truncates result.
    if not numbers:
        return 0
    total = 0
    for n in numbers:
        total += n
    return total // len(numbers)


def weighted_average(values, weights):
    # Weighted average computation.
    total = sum(v * w for v, w in zip(values, weights))
    return total // sum(weights)


def running_average(numbers):
    # Compute running average for each position.
    result = []
    total = 0
    for i, n in enumerate(numbers):
        total += n
        result.append(total // (i + 1))
    return result


def test():
    assert average([1, 2, 3]) == 2
    return True
''',
        "fixed_source": '''
def average(numbers):
    # Fixed: float division.
    if not numbers:
        return 0
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)


def weighted_average(values, weights):
    # Weighted average computation.
    total = sum(v * w for v, w in zip(values, weights))
    return total / sum(weights)


def running_average(numbers):
    # Compute running average for each position.
    result = []
    total = 0
    for i, n in enumerate(numbers):
        total += n
        result.append(total / (i + 1))
    return result


def test():
    assert average([1, 2, 3]) == 2.0
    return True
''',
    },
    {
        "id": "buggy_max_subarray",
        "bug_description": "Wrong accumulator reset: resets to 0 instead of current element",
        "source": '''
def max_subarray_sum(arr):
    # Kadane with wrong reset: sets current_sum to 0 not arr[i].
    if not arr:
        return 0
    max_sum = arr[0]
    current_sum = arr[0]
    for i in range(1, len(arr)):
        if current_sum < 0:
            current_sum = 0
        current_sum += arr[i]
        if current_sum > max_sum:
            max_sum = current_sum
    return max_sum


def max_subarray_indices(arr):
    # Return start, end indices of max subarray.
    if not arr:
        return 0, 0
    best_start = best_end = 0
    start = 0
    max_sum = arr[0]
    current_sum = arr[0]
    for i in range(1, len(arr)):
        if current_sum < 0:
            current_sum = 0
            start = i
        current_sum += arr[i]
        if current_sum > max_sum:
            max_sum = current_sum
            best_start = start
            best_end = i
    return best_start, best_end


def test():
    assert max_subarray_sum([-2, 1, -3, 4, -1, 2, 1, -5, 4]) == 6
    return True
''',
        "fixed_source": '''
def max_subarray_sum(arr):
    # Kadane with correct reset.
    if not arr:
        return 0
    max_sum = arr[0]
    current_sum = arr[0]
    for i in range(1, len(arr)):
        current_sum = max(arr[i], current_sum + arr[i])
        if current_sum > max_sum:
            max_sum = current_sum
    return max_sum


def max_subarray_indices(arr):
    # Return start, end indices of max subarray.
    if not arr:
        return 0, 0
    best_start = best_end = 0
    start = 0
    max_sum = arr[0]
    current_sum = arr[0]
    for i in range(1, len(arr)):
        if current_sum + arr[i] < arr[i]:
            current_sum = arr[i]
            start = i
        else:
            current_sum += arr[i]
        if current_sum > max_sum:
            max_sum = current_sum
            best_start = start
            best_end = i
    return best_start, best_end


def test():
    assert max_subarray_sum([-2, 1, -3, 4, -1, 2, 1, -5, 4]) == 6
    return True
''',
    },
    {
        "id": "buggy_reverse_string",
        "bug_description": "Off-by-one in string reversal",
        "source": '''
def reverse_string(s):
    # Bug: off-by-one, misses first character.
    result = list(s)
    left = 1
    right = len(result) - 1
    while left < right:
        result[left], result[right] = result[right], result[left]
        left += 1
        right -= 1
    return "".join(result)


def reverse_words(s):
    # Reverse word order in string.
    words = s.split()
    return " ".join(reversed(words))


def is_reverse(s1, s2):
    # Check if s2 is reverse of s1.
    return reverse_string(s1) == s2


def test():
    assert reverse_string("hello") == "olleh"
    assert reverse_words("hello world") == "world hello"
    return True
''',
        "fixed_source": '''
def reverse_string(s):
    # Fixed: starts from index 0.
    result = list(s)
    left = 0
    right = len(result) - 1
    while left < right:
        result[left], result[right] = result[right], result[left]
        left += 1
        right -= 1
    return "".join(result)


def reverse_words(s):
    # Reverse word order in string.
    words = s.split()
    return " ".join(reversed(words))


def is_reverse(s1, s2):
    # Check if s2 is reverse of s1.
    return reverse_string(s1) == s2


def test():
    assert reverse_string("hello") == "olleh"
    assert reverse_words("hello world") == "world hello"
    return True
''',
    },
    {
        "id": "buggy_power_negative",
        "bug_description": "Does not handle negative exponents",
        "source": '''
def power(base, exp):
    # Bug: does not handle negative exponents.
    if exp == 0:
        return 1
    result = 1
    for _ in range(exp):
        result *= base
    return result


def power_mod(base, exp, mod):
    # Modular exponentiation.
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp //= 2
        base = (base * base) % mod
    return result


def test():
    assert power(2, 3) == 8
    assert power(5, 0) == 1
    return True
''',
        "fixed_source": '''
def power(base, exp):
    # Fixed: handles negative exponents.
    if exp == 0:
        return 1
    if exp < 0:
        return 1.0 / power(base, -exp)
    result = 1
    for _ in range(exp):
        result *= base
    return result


def power_mod(base, exp, mod):
    # Modular exponentiation.
    result = 1
    base = base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = (result * base) % mod
        exp //= 2
        base = (base * base) % mod
    return result


def test():
    assert power(2, 3) == 8
    assert power(2, -1) == 0.5
    return True
''',
    },
    {
        "id": "buggy_flatten_list",
        "bug_description": "Missing isinstance check for nested lists",
        "source": '''
def flatten(lst):
    # Bug: does not check for nested lists, just extends.
    result = []
    for item in lst:
        result.append(item)
    return result


def deep_flatten(lst, depth=-1):
    # Flatten list to a certain depth.
    result = []
    for item in lst:
        if isinstance(item, list) and depth != 0:
            result.extend(deep_flatten(item, depth - 1))
        else:
            result.append(item)
    return result


def test():
    assert flatten([1, [2, 3], [4, [5]]]) == [1, 2, 3, 4, 5]
    return True
''',
        "fixed_source": '''
def flatten(lst):
    # Fixed: recursively flattens nested lists.
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result


def deep_flatten(lst, depth=-1):
    # Flatten list to a certain depth.
    result = []
    for item in lst:
        if isinstance(item, list) and depth != 0:
            result.extend(deep_flatten(item, depth - 1))
        else:
            result.append(item)
    return result


def test():
    assert flatten([1, [2, 3], [4, [5]]]) == [1, 2, 3, 4, 5]
    return True
''',
    },
    {
        "id": "buggy_roman_numerals",
        "bug_description": "Wrong order of numeral checks (small before large)",
        "source": '''
def to_roman(num):
    # Bug: checks I before IV, so 4 becomes IIII.
    numerals = [
        (1000, "M"), (500, "D"), (100, "C"), (50, "L"),
        (10, "X"), (5, "V"), (1, "I"),
        (900, "CM"), (400, "CD"), (90, "XC"),
        (40, "XL"), (9, "IX"), (4, "IV"),
    ]
    result = []
    for value, symbol in numerals:
        while num >= value:
            result.append(symbol)
            num -= value
    return "".join(result)


def from_roman(s):
    # Convert Roman numeral to integer.
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i in range(len(s)):
        if i + 1 < len(s) and values[s[i]] < values[s[i+1]]:
            total -= values[s[i]]
        else:
            total += values[s[i]]
    return total


def test():
    assert to_roman(4) == "IV"
    assert from_roman("XIV") == 14
    return True
''',
        "fixed_source": '''
def to_roman(num):
    # Fixed: correct order of numeral checks.
    numerals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    result = []
    for value, symbol in numerals:
        while num >= value:
            result.append(symbol)
            num -= value
    return "".join(result)


def from_roman(s):
    # Convert Roman numeral to integer.
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i in range(len(s)):
        if i + 1 < len(s) and values[s[i]] < values[s[i+1]]:
            total -= values[s[i]]
        else:
            total += values[s[i]]
    return total


def test():
    assert to_roman(4) == "IV"
    assert from_roman("XIV") == 14
    return True
''',
    },
    {
        "id": "buggy_is_prime_two",
        "bug_description": "Does not handle n=2 as prime",
        "source": '''
def is_prime(n):
    # Bug: returns False for n=2.
    if n < 2:
        return False
    for i in range(2, n):
        if n % i == 0:
            return False
    return True if n > 2 else False


def primes_up_to(limit):
    # List primes up to limit.
    return [n for n in range(2, limit + 1) if is_prime(n)]


def next_prime(n):
    # Find next prime after n.
    candidate = n + 1
    while not is_prime(candidate):
        candidate += 1
    return candidate


def test():
    assert is_prime(2) is True
    assert is_prime(17) is True
    return True
''',
        "fixed_source": '''
def is_prime(n):
    # Fixed: correctly identifies 2 as prime.
    if n < 2:
        return False
    if n == 2:
        return True
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True


def primes_up_to(limit):
    # List primes up to limit.
    return [n for n in range(2, limit + 1) if is_prime(n)]


def next_prime(n):
    # Find next prime after n.
    candidate = n + 1
    while not is_prime(candidate):
        candidate += 1
    return candidate


def test():
    assert is_prime(2) is True
    assert is_prime(17) is True
    return True
''',
    },
    {
        "id": "buggy_bubble_no_swap_flag",
        "bug_description": "Missing early exit optimization causing unnecessary passes",
        "source": '''
def bubble_sort(arr):
    # Bug: no swap flag, always does n^2 passes.
    result = list(arr)
    n = len(result)
    for i in range(n):
        for j in range(0, n - i - 1):
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
    return result


def is_sorted(arr):
    # Check if array is sorted.
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def sort_and_count(arr):
    # Sort and count total comparisons.
    result = list(arr)
    n = len(result)
    comparisons = 0
    for i in range(n):
        for j in range(0, n - i - 1):
            comparisons += 1
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
    return result, comparisons


def test():
    assert bubble_sort([3, 1, 2]) == [1, 2, 3]
    return True
''',
        "fixed_source": '''
def bubble_sort(arr):
    # Fixed: early exit when no swaps occur.
    result = list(arr)
    n = len(result)
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
    # Check if array is sorted.
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def sort_and_count(arr):
    # Sort and count total comparisons with early exit.
    result = list(arr)
    n = len(result)
    comparisons = 0
    for i in range(n):
        swapped = False
        for j in range(0, n - i - 1):
            comparisons += 1
            if result[j] > result[j + 1]:
                result[j], result[j + 1] = result[j + 1], result[j]
                swapped = True
        if not swapped:
            break
    return result, comparisons


def test():
    assert bubble_sort([3, 1, 2]) == [1, 2, 3]
    return True
''',
    },
    {
        "id": "buggy_queue_circular",
        "bug_description": "Wrong modulo in circular queue",
        "source": '''
class CircularQueue:
    def __init__(self, capacity):
        self.capacity = capacity
        self.queue = [None] * capacity
        self.front = 0
        self.rear = -1
        self.size = 0

    def enqueue(self, item):
        if self.size == self.capacity:
            raise OverflowError("queue full")
        self.rear = (self.rear + 1) % (self.capacity + 1)
        self.queue[self.rear] = item
        self.size += 1

    def dequeue(self):
        if self.size == 0:
            raise IndexError("queue empty")
        item = self.queue[self.front]
        self.front = (self.front + 1) % (self.capacity + 1)
        self.size -= 1
        return item

    def peek(self):
        if self.size == 0:
            raise IndexError("queue empty")
        return self.queue[self.front]

    def is_empty(self):
        return self.size == 0


def test():
    q = CircularQueue(3)
    q.enqueue(1)
    q.enqueue(2)
    assert q.dequeue() == 1
    return True
''',
        "fixed_source": '''
class CircularQueue:
    def __init__(self, capacity):
        self.capacity = capacity
        self.queue = [None] * capacity
        self.front = 0
        self.rear = -1
        self.size = 0

    def enqueue(self, item):
        if self.size == self.capacity:
            raise OverflowError("queue full")
        self.rear = (self.rear + 1) % self.capacity
        self.queue[self.rear] = item
        self.size += 1

    def dequeue(self):
        if self.size == 0:
            raise IndexError("queue empty")
        item = self.queue[self.front]
        self.front = (self.front + 1) % self.capacity
        self.size -= 1
        return item

    def peek(self):
        if self.size == 0:
            raise IndexError("queue empty")
        return self.queue[self.front]

    def is_empty(self):
        return self.size == 0


def test():
    q = CircularQueue(3)
    q.enqueue(1)
    q.enqueue(2)
    assert q.dequeue() == 1
    return True
''',
    },
    {
        "id": "buggy_graph_visited",
        "bug_description": "Does not mark visited in DFS causing infinite loop risk",
        "source": '''
class Graph:
    def __init__(self):
        self.adj = {}

    def add_edge(self, u, v):
        if u not in self.adj:
            self.adj[u] = []
        if v not in self.adj:
            self.adj[v] = []
        self.adj[u].append(v)
        self.adj[v].append(u)

    def dfs(self, start):
        # Bug: no visited set, revisits nodes.
        order = []
        stack = [start]
        while stack:
            node = stack.pop()
            order.append(node)
            for neighbor in self.adj.get(node, []):
                stack.append(neighbor)
            if len(order) > 1000:
                break
        return order


def test():
    g = Graph()
    g.add_edge(1, 2)
    g.add_edge(2, 3)
    result = g.dfs(1)
    return len(result) > 0
''',
        "fixed_source": '''
class Graph:
    def __init__(self):
        self.adj = {}

    def add_edge(self, u, v):
        if u not in self.adj:
            self.adj[u] = []
        if v not in self.adj:
            self.adj[v] = []
        self.adj[u].append(v)
        self.adj[v].append(u)

    def dfs(self, start):
        # Fixed: uses visited set.
        order = []
        visited = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            order.append(node)
            for neighbor in self.adj.get(node, []):
                if neighbor not in visited:
                    stack.append(neighbor)
        return order


def test():
    g = Graph()
    g.add_edge(1, 2)
    g.add_edge(2, 3)
    result = g.dfs(1)
    assert set(result) == {1, 2, 3}
    return True
''',
    },
    {
        "id": "buggy_count_words_empty",
        "bug_description": "Crashes on empty string input",
        "source": '''
def count_words(text):
    # Bug: crashes on empty string due to split behavior.
    words = text.split(" ")
    word_count = {}
    for word in words:
        word = word.lower().strip()
        word_count[word] = word_count.get(word, 0) + 1
    return word_count


def most_common_word(text):
    # Find most common word.
    counts = count_words(text)
    return max(counts, key=counts.get)


def unique_words(text):
    # Count unique words.
    return len(count_words(text))


def test():
    result = count_words("hello world hello")
    assert result["hello"] == 2
    return True
''',
        "fixed_source": '''
def count_words(text):
    # Fixed: handles empty string.
    if not text or not text.strip():
        return {}
    words = text.split()
    word_count = {}
    for word in words:
        word = word.lower().strip()
        if word:
            word_count[word] = word_count.get(word, 0) + 1
    return word_count


def most_common_word(text):
    # Find most common word.
    counts = count_words(text)
    if not counts:
        return None
    return max(counts, key=counts.get)


def unique_words(text):
    # Count unique words.
    return len(count_words(text))


def test():
    result = count_words("hello world hello")
    assert result["hello"] == 2
    assert count_words("") == {}
    return True
''',
    },
    {
        "id": "buggy_temperature_formula",
        "bug_description": "Wrong conversion formula F to C",
        "source": '''
def fahrenheit_to_celsius(f):
    # Bug: wrong formula, divides by 9 instead of multiplying by 5/9.
    return (f - 32) / 9


def celsius_to_fahrenheit(c):
    # Convert Celsius to Fahrenheit.
    return c * 9 / 5 + 32


def celsius_to_kelvin(c):
    # Convert Celsius to Kelvin.
    return c + 273.15


def convert_temperature(value, from_unit, to_unit):
    # General temperature conversion.
    if from_unit == to_unit:
        return value
    if from_unit == "F":
        value = fahrenheit_to_celsius(value)
    elif from_unit == "K":
        value = value - 273.15
    if to_unit == "F":
        return celsius_to_fahrenheit(value)
    if to_unit == "K":
        return celsius_to_kelvin(value)
    return value


def test():
    assert abs(fahrenheit_to_celsius(212) - 100) < 0.01
    return True
''',
        "fixed_source": '''
def fahrenheit_to_celsius(f):
    # Fixed: correct formula.
    return (f - 32) * 5 / 9


def celsius_to_fahrenheit(c):
    # Convert Celsius to Fahrenheit.
    return c * 9 / 5 + 32


def celsius_to_kelvin(c):
    # Convert Celsius to Kelvin.
    return c + 273.15


def convert_temperature(value, from_unit, to_unit):
    # General temperature conversion.
    if from_unit == to_unit:
        return value
    if from_unit == "F":
        value = fahrenheit_to_celsius(value)
    elif from_unit == "K":
        value = value - 273.15
    if to_unit == "F":
        return celsius_to_fahrenheit(value)
    if to_unit == "K":
        return celsius_to_kelvin(value)
    return value


def test():
    assert abs(fahrenheit_to_celsius(212) - 100) < 0.01
    return True
''',
    },
    {
        "id": "buggy_date_leap_year",
        "bug_description": "Wrong leap year check",
        "source": '''
def is_leap_year(year):
    # Bug: missing the 400-year rule.
    return year % 4 == 0 and year % 100 != 0


def days_in_year(year):
    # Return number of days in year.
    return 366 if is_leap_year(year) else 365


def days_in_month(year, month):
    # Days in given month.
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    return days[month]


def test():
    assert is_leap_year(2000) is True
    assert is_leap_year(1900) is False
    return True
''',
        "fixed_source": '''
def is_leap_year(year):
    # Fixed: includes 400-year rule.
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def days_in_year(year):
    # Return number of days in year.
    return 366 if is_leap_year(year) else 365


def days_in_month(year, month):
    # Days in given month.
    days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    return days[month]


def test():
    assert is_leap_year(2000) is True
    assert is_leap_year(1900) is False
    return True
''',
    },
    {
        "id": "buggy_email_at_check",
        "bug_description": "Allows multiple @ signs in email",
        "source": '''
def validate_email(email):
    # Bug: only checks if @ exists, not that there is exactly one.
    if not email or "@" not in email:
        return False
    local, domain = email.split("@", 1)
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


def extract_domain(email):
    # Extract domain from email.
    return email.split("@", 1)[1] if "@" in email else ""


def normalize_email(email):
    # Lowercase and strip email.
    return email.strip().lower()


def test():
    assert validate_email("user@example.com") is True
    assert validate_email("bad@@two.com") is False
    return True
''',
        "fixed_source": '''
def validate_email(email):
    # Fixed: checks for exactly one @ sign.
    if not email or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


def extract_domain(email):
    # Extract domain from email.
    return email.split("@", 1)[1] if "@" in email else ""


def normalize_email(email):
    # Lowercase and strip email.
    return email.strip().lower()


def test():
    assert validate_email("user@example.com") is True
    assert validate_email("bad@@two.com") is False
    return True
''',
    },
    {
        "id": "buggy_sort_stable",
        "bug_description": "Unstable sort loses order of equal elements",
        "source": '''
def sort_by_key(items, key_func):
    # Bug: unstable sort loses original order of equal elements.
    n = len(items)
    result = list(items)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if key_func(result[j]) < key_func(result[min_idx]):
                min_idx = j
        result[i], result[min_idx] = result[min_idx], result[i]
    return result


def sort_records(records, field):
    # Sort list of dicts by field.
    return sort_by_key(records, lambda r: r[field])


def test():
    data = [{"name": "B", "age": 25}, {"name": "A", "age": 25}, {"name": "C", "age": 20}]
    result = sort_records(data, "age")
    assert result[0]["age"] == 20
    return True
''',
        "fixed_source": '''
def sort_by_key(items, key_func):
    # Fixed: stable sort preserves order of equal elements.
    indexed = list(enumerate(items))
    n = len(indexed)
    for i in range(1, n):
        key_item = indexed[i]
        j = i - 1
        while j >= 0 and key_func(indexed[j][1]) > key_func(key_item[1]):
            indexed[j + 1] = indexed[j]
            j -= 1
        indexed[j + 1] = key_item
    return [item for _, item in indexed]


def sort_records(records, field):
    # Sort list of dicts by field.
    return sort_by_key(records, lambda r: r[field])


def test():
    data = [{"name": "B", "age": 25}, {"name": "A", "age": 25}, {"name": "C", "age": 20}]
    result = sort_records(data, "age")
    assert result[0]["age"] == 20
    return True
''',
    },
    {
        "id": "buggy_binary_to_decimal",
        "bug_description": "Processes bits in wrong order",
        "source": '''
def binary_to_decimal(binary_str):
    # Bug: processes bits left-to-right without proper weighting.
    result = 0
    for i, bit in enumerate(binary_str):
        result += int(bit) * (2 ** i)
    return result


def decimal_to_binary(n):
    # Convert decimal to binary.
    if n == 0:
        return "0"
    bits = []
    while n > 0:
        bits.append(str(n % 2))
        n //= 2
    return "".join(reversed(bits))


def test():
    assert binary_to_decimal("1010") == 10
    assert decimal_to_binary(10) == "1010"
    return True
''',
        "fixed_source": '''
def binary_to_decimal(binary_str):
    # Fixed: correct bit position weighting.
    result = 0
    for i, bit in enumerate(reversed(binary_str)):
        result += int(bit) * (2 ** i)
    return result


def decimal_to_binary(n):
    # Convert decimal to binary.
    if n == 0:
        return "0"
    bits = []
    while n > 0:
        bits.append(str(n % 2))
        n //= 2
    return "".join(reversed(bits))


def test():
    assert binary_to_decimal("1010") == 10
    assert decimal_to_binary(10) == "1010"
    return True
''',
    },
    {
        "id": "buggy_list_rotate",
        "bug_description": "Off-by-one in rotation",
        "source": '''
def rotate_left(arr, k):
    # Bug: off-by-one in rotation amount.
    if not arr:
        return arr
    n = len(arr)
    k = (k - 1) % n
    return arr[k:] + arr[:k]


def rotate_right(arr, k):
    # Rotate array right by k positions.
    if not arr:
        return arr
    n = len(arr)
    k = k % n
    return arr[n - k:] + arr[:n - k]


def test():
    assert rotate_left([1, 2, 3, 4, 5], 2) == [3, 4, 5, 1, 2]
    return True
''',
        "fixed_source": '''
def rotate_left(arr, k):
    # Fixed: correct rotation amount.
    if not arr:
        return arr
    n = len(arr)
    k = k % n
    return arr[k:] + arr[:k]


def rotate_right(arr, k):
    # Rotate array right by k positions.
    if not arr:
        return arr
    n = len(arr)
    k = k % n
    return arr[n - k:] + arr[:n - k]


def test():
    assert rotate_left([1, 2, 3, 4, 5], 2) == [3, 4, 5, 1, 2]
    return True
''',
    },
    {
        "id": "buggy_mutable_default",
        "bug_description": "Mutable default argument bug",
        "source": '''
def append_to_list(item, target=[]):
    # Bug: mutable default argument shared across calls.
    target.append(item)
    return target


def build_lists(items):
    # Build separate lists for each item.
    result = []
    for item in items:
        result.append(append_to_list(item))
    return result


def test():
    a = append_to_list(1)
    b = append_to_list(2)
    assert a == [1]
    assert b == [2]
    return True
''',
        "fixed_source": '''
def append_to_list(item, target=None):
    # Fixed: uses None default and creates new list.
    if target is None:
        target = []
    target.append(item)
    return target


def build_lists(items):
    # Build separate lists for each item.
    result = []
    for item in items:
        result.append(append_to_list(item))
    return result


def test():
    a = append_to_list(1)
    b = append_to_list(2)
    assert a == [1]
    assert b == [2]
    return True
''',
    },
    {
        "id": "buggy_tree_height",
        "bug_description": "Wrong base case for tree height",
        "source": '''
class TNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None


def tree_height(node):
    # Bug: base case returns 1 instead of 0 for None.
    if node is None:
        return 1
    left_h = tree_height(node.left)
    right_h = tree_height(node.right)
    return 1 + max(left_h, right_h)


def count_nodes(node):
    # Count total nodes in tree.
    if node is None:
        return 0
    return 1 + count_nodes(node.left) + count_nodes(node.right)


def test():
    root = TNode(1)
    root.left = TNode(2)
    root.right = TNode(3)
    assert tree_height(root) == 2
    return True
''',
        "fixed_source": '''
class TNode:
    def __init__(self, val):
        self.val = val
        self.left = None
        self.right = None


def tree_height(node):
    # Fixed: base case returns 0 for None.
    if node is None:
        return 0
    left_h = tree_height(node.left)
    right_h = tree_height(node.right)
    return 1 + max(left_h, right_h)


def count_nodes(node):
    # Count total nodes in tree.
    if node is None:
        return 0
    return 1 + count_nodes(node.left) + count_nodes(node.right)


def test():
    root = TNode(1)
    root.left = TNode(2)
    root.right = TNode(3)
    assert tree_height(root) == 2
    return True
''',
    },
    {
        "id": "buggy_string_compress",
        "bug_description": "Off-by-one loses last character group",
        "source": '''
def compress(s):
    # Bug: misses the last group of characters.
    if not s:
        return ""
    result = []
    count = 1
    for i in range(1, len(s)):
        if s[i] == s[i - 1]:
            count += 1
        else:
            result.append(s[i - 1] + str(count))
            count = 1
    return "".join(result)


def decompress(s):
    # Decompress a compressed string.
    result = []
    i = 0
    while i < len(s):
        ch = s[i]
        i += 1
        num = ""
        while i < len(s) and s[i].isdigit():
            num += s[i]
            i += 1
        result.append(ch * int(num) if num else ch)
    return "".join(result)


def test():
    assert compress("aaabbc") == "a3b2c1"
    return True
''',
        "fixed_source": '''
def compress(s):
    # Fixed: includes the last group.
    if not s:
        return ""
    result = []
    count = 1
    for i in range(1, len(s)):
        if s[i] == s[i - 1]:
            count += 1
        else:
            result.append(s[i - 1] + str(count))
            count = 1
    result.append(s[-1] + str(count))
    return "".join(result)


def decompress(s):
    # Decompress a compressed string.
    result = []
    i = 0
    while i < len(s):
        ch = s[i]
        i += 1
        num = ""
        while i < len(s) and s[i].isdigit():
            num += s[i]
            i += 1
        result.append(ch * int(num) if num else ch)
    return "".join(result)


def test():
    assert compress("aaabbc") == "a3b2c1"
    return True
''',
    },
    {
        "id": "buggy_histogram",
        "bug_description": "Wrong bin boundary: < vs <=",
        "source": '''
def histogram(data, bins):
    # Bug: uses < for upper boundary, missing edge values.
    counts = [0] * len(bins)
    for value in data:
        for i in range(len(bins)):
            low = bins[i][0]
            high = bins[i][1]
            if low <= value < high:
                counts[i] += 1
                break
    return counts


def auto_bins(data, num_bins=5):
    # Generate evenly-spaced bins.
    mn = min(data)
    mx = max(data)
    step = (mx - mn) / num_bins
    bins = []
    for i in range(num_bins):
        bins.append((mn + i * step, mn + (i + 1) * step))
    return bins


def test():
    bins = [(0, 10), (10, 20), (20, 30)]
    data = [5, 10, 15, 20, 25]
    counts = histogram(data, bins)
    assert sum(counts) == 5
    return True
''',
        "fixed_source": '''
def histogram(data, bins):
    # Fixed: uses <= for upper boundary.
    counts = [0] * len(bins)
    for value in data:
        for i in range(len(bins)):
            low = bins[i][0]
            high = bins[i][1]
            if low <= value <= high:
                counts[i] += 1
                break
    return counts


def auto_bins(data, num_bins=5):
    # Generate evenly-spaced bins.
    mn = min(data)
    mx = max(data)
    step = (mx - mn) / num_bins
    bins = []
    for i in range(num_bins):
        bins.append((mn + i * step, mn + (i + 1) * step))
    return bins


def test():
    bins = [(0, 10), (10, 20), (20, 30)]
    data = [5, 10, 15, 20, 25]
    counts = histogram(data, bins)
    assert sum(counts) == 5
    return True
''',
    },
    {
        "id": "buggy_moving_average",
        "bug_description": "Wrong window size calculation",
        "source": '''
def moving_average(data, window):
    # Bug: window offset is wrong, uses window+1 elements.
    if not data or window <= 0:
        return []
    result = []
    for i in range(len(data)):
        start = max(0, i - window)
        chunk = data[start:i + 1]
        result.append(sum(chunk) / len(chunk))
    return result


def exponential_moving_average(data, alpha=0.5):
    # Compute EMA.
    if not data:
        return []
    result = [data[0]]
    for i in range(1, len(data)):
        ema = alpha * data[i] + (1 - alpha) * result[-1]
        result.append(ema)
    return result


def test():
    data = [1, 2, 3, 4, 5]
    result = moving_average(data, 3)
    assert len(result) == 5
    return True
''',
        "fixed_source": '''
def moving_average(data, window):
    # Fixed: correct window boundaries.
    if not data or window <= 0:
        return []
    result = []
    for i in range(len(data)):
        start = max(0, i - window + 1)
        chunk = data[start:i + 1]
        result.append(sum(chunk) / len(chunk))
    return result


def exponential_moving_average(data, alpha=0.5):
    # Compute EMA.
    if not data:
        return []
    result = [data[0]]
    for i in range(1, len(data)):
        ema = alpha * data[i] + (1 - alpha) * result[-1]
        result.append(ema)
    return result


def test():
    data = [1, 2, 3, 4, 5]
    result = moving_average(data, 3)
    assert len(result) == 5
    return True
''',
    },
    {
        "id": "buggy_anagram_check",
        "bug_description": "Does not handle different lengths",
        "source": '''
def is_anagram(s1, s2):
    # Bug: does not check lengths first.
    freq = {}
    for ch in s1.lower():
        if ch.isalpha():
            freq[ch] = freq.get(ch, 0) + 1
    for ch in s2.lower():
        if ch.isalpha():
            freq[ch] = freq.get(ch, 0) - 1
    return all(v == 0 for v in freq.values())


def find_anagrams(word, word_list):
    # Find all anagrams of word in word_list.
    return [w for w in word_list if is_anagram(word, w)]


def sort_by_anagram_groups(words):
    # Group words by anagram signature.
    groups = {}
    for word in words:
        key = "".join(sorted(word.lower()))
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    return list(groups.values())


def test():
    assert is_anagram("listen", "silent") is True
    assert is_anagram("hello", "world") is False
    return True
''',
        "fixed_source": '''
def is_anagram(s1, s2):
    # Fixed: checks letter counts match including length.
    clean1 = [ch.lower() for ch in s1 if ch.isalpha()]
    clean2 = [ch.lower() for ch in s2 if ch.isalpha()]
    if len(clean1) != len(clean2):
        return False
    freq = {}
    for ch in clean1:
        freq[ch] = freq.get(ch, 0) + 1
    for ch in clean2:
        freq[ch] = freq.get(ch, 0) - 1
    return all(v == 0 for v in freq.values())


def find_anagrams(word, word_list):
    # Find all anagrams of word in word_list.
    return [w for w in word_list if is_anagram(word, w)]


def sort_by_anagram_groups(words):
    # Group words by anagram signature.
    groups = {}
    for word in words:
        key = "".join(sorted(word.lower()))
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    return list(groups.values())


def test():
    assert is_anagram("listen", "silent") is True
    assert is_anagram("hello", "world") is False
    return True
''',
    },
    {
        "id": "buggy_matrix_transpose",
        "bug_description": "Modifies matrix in-place incorrectly",
        "source": '''
def transpose(matrix):
    # Bug: in-place transpose overwrites data before reading it.
    n = len(matrix)
    m = len(matrix[0])
    for i in range(n):
        for j in range(m):
            matrix[i][j] = matrix[j][i]
    return matrix


def print_matrix(matrix):
    # Format matrix as string.
    lines = []
    for row in matrix:
        lines.append(" ".join(str(x) for x in row))
    return "\\n".join(lines)


def test():
    m = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    t = transpose(m)
    assert t[0] == [1, 4, 7]
    return True
''',
        "fixed_source": '''
def transpose(matrix):
    # Fixed: creates new matrix for transpose.
    n = len(matrix)
    m = len(matrix[0])
    result = [[0] * n for _ in range(m)]
    for i in range(n):
        for j in range(m):
            result[j][i] = matrix[i][j]
    return result


def print_matrix(matrix):
    # Format matrix as string.
    lines = []
    for row in matrix:
        lines.append(" ".join(str(x) for x in row))
    return "\\n".join(lines)


def test():
    m = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    t = transpose(m)
    assert t[0] == [1, 4, 7]
    return True
''',
    },
    {
        "id": "buggy_deep_copy",
        "bug_description": "Shallow copy instead of deep copy for nested structures",
        "source": '''
def deep_copy(obj):
    # Bug: only does shallow copy of dicts/lists.
    if isinstance(obj, dict):
        return dict(obj)
    if isinstance(obj, list):
        return list(obj)
    return obj


def clone_config(config):
    # Clone a nested configuration dict.
    return deep_copy(config)


def merge_configs(base, override):
    # Merge two configs.
    result = deep_copy(base)
    for key, value in override.items():
        result[key] = value
    return result


def test():
    original = {"a": [1, 2], "b": {"c": 3}}
    copy = deep_copy(original)
    copy["a"].append(3)
    assert original["a"] == [1, 2]
    return True
''',
        "fixed_source": '''
def deep_copy(obj):
    # Fixed: recursively copies nested structures.
    if isinstance(obj, dict):
        return {k: deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_copy(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(deep_copy(item) for item in obj)
    return obj


def clone_config(config):
    # Clone a nested configuration dict.
    return deep_copy(config)


def merge_configs(base, override):
    # Merge two configs.
    result = deep_copy(base)
    for key, value in override.items():
        result[key] = value
    return result


def test():
    original = {"a": [1, 2], "b": {"c": 3}}
    copy = deep_copy(original)
    copy["a"].append(3)
    assert original["a"] == [1, 2]
    return True
''',
    },
    {
        "id": "buggy_balanced_parens",
        "bug_description": "Does not check stack empty at end",
        "source": '''
def is_balanced(s):
    # Bug: does not check for unclosed parens at end.
    stack = []
    matching = {")": "(", "]": "[", "}": "{"}
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack:
                return False
            if stack[-1] != matching[ch]:
                return False
            stack.pop()
    return True


def count_unmatched(s):
    # Count unmatched brackets.
    stack = []
    unmatched = 0
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if stack:
                stack.pop()
            else:
                unmatched += 1
    return unmatched + len(stack)


def test():
    assert is_balanced("()[]{}") is True
    assert is_balanced("(()") is False
    return True
''',
        "fixed_source": '''
def is_balanced(s):
    # Fixed: checks stack is empty at end.
    stack = []
    matching = {")": "(", "]": "[", "}": "{"}
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack:
                return False
            if stack[-1] != matching[ch]:
                return False
            stack.pop()
    return len(stack) == 0


def count_unmatched(s):
    # Count unmatched brackets.
    stack = []
    unmatched = 0
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if stack:
                stack.pop()
            else:
                unmatched += 1
    return unmatched + len(stack)


def test():
    assert is_balanced("()[]{}") is True
    assert is_balanced("(()") is False
    return True
''',
    },
    {
        "id": "buggy_insertion_sort_start",
        "bug_description": "Wrong starting index in insertion sort",
        "source": '''
def insertion_sort(arr):
    # Bug: starts from index 0 instead of 1.
    result = list(arr)
    n = len(result)
    for i in range(0, n):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def is_sorted(arr):
    # Check if sorted.
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def sort_descending(arr):
    # Sort in descending order.
    result = list(arr)
    n = len(result)
    for i in range(1, n):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] < key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def test():
    assert insertion_sort([3, 1, 2]) == [1, 2, 3]
    return True
''',
        "fixed_source": '''
def insertion_sort(arr):
    # Fixed: starts from index 1.
    result = list(arr)
    n = len(result)
    for i in range(1, n):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def is_sorted(arr):
    # Check if sorted.
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def sort_descending(arr):
    # Sort in descending order.
    result = list(arr)
    n = len(result)
    for i in range(1, n):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] < key:
            result[j + 1] = result[j]
            j -= 1
        result[j + 1] = key
    return result


def test():
    assert insertion_sort([3, 1, 2]) == [1, 2, 3]
    return True
''',
    },
    {
        "id": "buggy_lru_eviction",
        "bug_description": "Evicts newest instead of oldest",
        "source": '''
class LRU:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key not in self.cache:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]

    def put(self, key, value):
        # Bug: evicts newest (last) instead of oldest (first).
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            evict = self.order.pop()
            del self.cache[evict]
        self.cache[key] = value
        self.order.append(key)

    def size(self):
        return len(self.cache)


def test():
    lru = LRU(2)
    lru.put("a", 1)
    lru.put("b", 2)
    lru.put("c", 3)
    assert lru.get("a") == -1
    return True
''',
        "fixed_source": '''
class LRU:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key not in self.cache:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]

    def put(self, key, value):
        # Fixed: evicts oldest (first) element.
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            evict = self.order.pop(0)
            del self.cache[evict]
        self.cache[key] = value
        self.order.append(key)

    def size(self):
        return len(self.cache)


def test():
    lru = LRU(2)
    lru.put("a", 1)
    lru.put("b", 2)
    lru.put("c", 3)
    assert lru.get("a") == -1
    return True
''',
    },
    {
        "id": "buggy_trie_prefix",
        "bug_description": "Returns True for non-complete words (prefix match only)",
        "source": '''
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
        # Bug: returns True for prefixes, not complete words.
        node = self.root
        for ch in word:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return True

    def starts_with(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return True


def test():
    t = Trie()
    t.insert("apple")
    assert t.search("apple") is True
    assert t.search("app") is False
    return True
''',
        "fixed_source": '''
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
        # Fixed: checks is_end flag for complete words.
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


def test():
    t = Trie()
    t.insert("apple")
    assert t.search("apple") is True
    assert t.search("app") is False
    return True
''',
    },
    {
        "id": "buggy_dijkstra_init",
        "bug_description": "Wrong initial distance: uses 0 for all instead of infinity",
        "source": '''
def dijkstra(graph, start):
    # Bug: initializes all distances to 0 instead of infinity.
    distances = {node: 0 for node in graph}
    distances[start] = 0
    visited = set()
    while len(visited) < len(graph):
        current = None
        for node in graph:
            if node not in visited:
                if current is None or distances[node] < distances[current]:
                    current = node
        if current is None:
            break
        visited.add(current)
        for neighbor, weight in graph[current]:
            new_dist = distances[current] + weight
            if new_dist < distances[neighbor]:
                distances[neighbor] = new_dist
    return distances


def shortest_path(graph, start, end):
    # Find shortest path distance.
    dists = dijkstra(graph, start)
    return dists.get(end, float("inf"))


def test():
    graph = {
        "A": [("B", 1), ("C", 4)],
        "B": [("C", 2), ("D", 5)],
        "C": [("D", 1)],
        "D": [],
    }
    dists = dijkstra(graph, "A")
    assert dists["D"] == 4
    return True
''',
        "fixed_source": '''
def dijkstra(graph, start):
    # Fixed: initializes distances to infinity.
    distances = {node: float("inf") for node in graph}
    distances[start] = 0
    visited = set()
    while len(visited) < len(graph):
        current = None
        for node in graph:
            if node not in visited:
                if current is None or distances[node] < distances[current]:
                    current = node
        if current is None:
            break
        visited.add(current)
        for neighbor, weight in graph[current]:
            new_dist = distances[current] + weight
            if new_dist < distances[neighbor]:
                distances[neighbor] = new_dist
    return distances


def shortest_path(graph, start, end):
    # Find shortest path distance.
    dists = dijkstra(graph, start)
    return dists.get(end, float("inf"))


def test():
    graph = {
        "A": [("B", 1), ("C", 4)],
        "B": [("C", 2), ("D", 5)],
        "C": [("D", 1)],
        "D": [],
    }
    dists = dijkstra(graph, "A")
    assert dists["D"] == 4
    return True
''',
    },
    {
        "id": "buggy_calculator_precedence",
        "bug_description": "Wrong operator precedence: + before *",
        "source": '''
def calculate(expr):
    # Bug: evaluates left-to-right without respecting precedence.
    tokens = []
    num = ""
    for ch in expr:
        if ch.isdigit():
            num += ch
        elif ch in "+-*/":
            if num:
                tokens.append(int(num))
                num = ""
            tokens.append(ch)
    if num:
        tokens.append(int(num))
    result = tokens[0]
    i = 1
    while i < len(tokens):
        op = tokens[i]
        val = tokens[i + 1]
        if op == "+": result += val
        elif op == "-": result -= val
        elif op == "*": result *= val
        elif op == "/": result //= val
        i += 2
    return result


def test():
    assert calculate("2+3*4") == 14
    assert calculate("10-2*3") == 4
    return True
''',
        "fixed_source": '''
def calculate(expr):
    # Fixed: respects operator precedence with two-pass evaluation.
    tokens = []
    num = ""
    for ch in expr:
        if ch.isdigit():
            num += ch
        elif ch in "+-*/":
            if num:
                tokens.append(int(num))
                num = ""
            tokens.append(ch)
    if num:
        tokens.append(int(num))
    i = 1
    while i < len(tokens):
        if tokens[i] in ("*", "/"):
            left = tokens[i - 1]
            right = tokens[i + 1]
            if tokens[i] == "*":
                val = left * right
            else:
                val = left // right
            tokens[i-1:i+2] = [val]
        else:
            i += 2
    result = tokens[0]
    i = 1
    while i < len(tokens):
        op = tokens[i]
        val = tokens[i + 1]
        if op == "+": result += val
        elif op == "-": result -= val
        i += 2
    return result


def test():
    assert calculate("2+3*4") == 14
    assert calculate("10-2*3") == 4
    return True
''',
    },
    {
        "id": "buggy_range_overlap",
        "bug_description": "Wrong overlap check: uses and instead of or",
        "source": '''
def ranges_overlap(r1_start, r1_end, r2_start, r2_end):
    # Bug: wrong logic for overlap check.
    return r1_start <= r2_end and r1_end >= r2_start and r1_start >= r2_start


def merge_ranges(ranges):
    # Merge overlapping ranges.
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def test():
    assert ranges_overlap(1, 5, 3, 7) is True
    assert ranges_overlap(1, 2, 3, 4) is False
    return True
''',
        "fixed_source": '''
def ranges_overlap(r1_start, r1_end, r2_start, r2_end):
    # Fixed: correct overlap check.
    return r1_start <= r2_end and r1_end >= r2_start


def merge_ranges(ranges):
    # Merge overlapping ranges.
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def test():
    assert ranges_overlap(1, 5, 3, 7) is True
    assert ranges_overlap(1, 2, 3, 4) is False
    return True
''',
    },
    {
        "id": "buggy_median_unsorted",
        "bug_description": "Forgets to sort before finding median",
        "source": '''
def median(data):
    # Bug: does not sort data before finding median.
    n = len(data)
    if n == 0:
        raise ValueError("empty data")
    mid = n // 2
    if n % 2 == 0:
        return (data[mid - 1] + data[mid]) / 2
    return data[mid]


def quartiles(data):
    # Compute Q1, Q2 (median), Q3.
    s = sorted(data)
    n = len(s)
    q2 = median(s)
    mid = n // 2
    lower = s[:mid]
    upper = s[mid + 1:] if n % 2 else s[mid:]
    q1 = median(lower) if lower else q2
    q3 = median(upper) if upper else q2
    return q1, q2, q3


def test():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    return True
''',
        "fixed_source": '''
def median(data):
    # Fixed: sorts data before finding median.
    if not data:
        raise ValueError("empty data")
    s = sorted(data)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def quartiles(data):
    # Compute Q1, Q2 (median), Q3.
    s = sorted(data)
    n = len(s)
    q2 = median(s)
    mid = n // 2
    lower = s[:mid]
    upper = s[mid + 1:] if n % 2 else s[mid:]
    q1 = median(lower) if lower else q2
    q3 = median(upper) if upper else q2
    return q1, q2, q3


def test():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    return True
''',
    },
    {
        "id": "buggy_password_length",
        "bug_description": "Off-by-one in length check: > 8 instead of >= 8",
        "source": '''
def validate_password(password):
    # Bug: requires > 8 instead of >= 8 characters.
    errors = []
    if len(password) > 8:
        pass
    else:
        errors.append("must be at least 8 characters")
    if not any(c.isupper() for c in password):
        errors.append("needs uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("needs lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("needs digit")
    return len(errors) == 0, errors


def password_strength(password):
    # Rate password strength 0-4.
    score = 0
    if len(password) >= 8: score += 1
    if any(c.isupper() for c in password): score += 1
    if any(c.isdigit() for c in password): score += 1
    if any(c in "!@#$%^&*" for c in password): score += 1
    return score


def test():
    ok, _ = validate_password("Abcd1234")
    assert ok is True
    return True
''',
        "fixed_source": '''
def validate_password(password):
    # Fixed: requires >= 8 characters.
    errors = []
    if len(password) >= 8:
        pass
    else:
        errors.append("must be at least 8 characters")
    if not any(c.isupper() for c in password):
        errors.append("needs uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("needs lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("needs digit")
    return len(errors) == 0, errors


def password_strength(password):
    # Rate password strength 0-4.
    score = 0
    if len(password) >= 8: score += 1
    if any(c.isupper() for c in password): score += 1
    if any(c.isdigit() for c in password): score += 1
    if any(c in "!@#$%^&*" for c in password): score += 1
    return score


def test():
    ok, _ = validate_password("Abcd1234")
    assert ok is True
    return True
''',
    },
    {
        "id": "buggy_csv_quote_escape",
        "bug_description": "Does not handle escaped quotes in CSV",
        "source": '''
def parse_csv_line(line):
    # Bug: does not handle escaped quotes (double quotes).
    fields = []
    current = []
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ',' and not in_quotes:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def parse_csv(text):
    # Parse multi-line CSV.
    return [parse_csv_line(line) for line in text.strip().split("\\n")]


def test():
    line = 'a,"b""c",d'
    result = parse_csv_line(line)
    assert result[1] == 'b"c'
    return True
''',
        "fixed_source": '''
def parse_csv_line(line):
    # Fixed: handles escaped quotes (doubled quotes).
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    current.append('"')
                    i += 2
                    continue
                else:
                    in_quotes = False
            else:
                current.append(ch)
        else:
            if ch == '"':
                in_quotes = True
            elif ch == ',':
                fields.append("".join(current))
                current = []
            else:
                current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


def parse_csv(text):
    # Parse multi-line CSV.
    return [parse_csv_line(line) for line in text.strip().split("\\n")]


def test():
    line = 'a,"b""c",d'
    result = parse_csv_line(line)
    assert result[1] == 'b"c'
    return True
''',
    },
    {
        "id": "buggy_recursion_no_base",
        "bug_description": "Missing base case in recursive sum function",
        "source": '''
def recursive_sum(lst):
    # Bug: missing base case for empty list.
    return lst[0] + recursive_sum(lst[1:])


def recursive_max(lst):
    # Find max recursively.
    if len(lst) == 1:
        return lst[0]
    sub_max = recursive_max(lst[1:])
    return lst[0] if lst[0] > sub_max else sub_max


def recursive_flatten(lst):
    # Flatten nested lists recursively.
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(recursive_flatten(item))
        else:
            result.append(item)
    return result


def test():
    assert recursive_sum([1, 2, 3]) == 6
    assert recursive_sum([]) == 0
    return True
''',
        "fixed_source": '''
def recursive_sum(lst):
    # Fixed: has base case for empty list.
    if not lst:
        return 0
    return lst[0] + recursive_sum(lst[1:])


def recursive_max(lst):
    # Find max recursively.
    if len(lst) == 1:
        return lst[0]
    sub_max = recursive_max(lst[1:])
    return lst[0] if lst[0] > sub_max else sub_max


def recursive_flatten(lst):
    # Flatten nested lists recursively.
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(recursive_flatten(item))
        else:
            result.append(item)
    return result


def test():
    assert recursive_sum([1, 2, 3]) == 6
    assert recursive_sum([]) == 0
    return True
''',
    },
    {
        "id": "buggy_none_check",
        "bug_description": "Missing None check before method call",
        "source": '''
def find_user(users, user_id):
    # Find user by ID.
    for user in users:
        if user.get("id") == user_id:
            return user
    return None


def get_user_name(users, user_id):
    # Bug: does not check if find_user returns None.
    user = find_user(users, user_id)
    return user["name"]


def get_user_email(users, user_id):
    # Get user email.
    user = find_user(users, user_id)
    return user["email"]


def list_user_names(users):
    # List all user names.
    return [u["name"] for u in users]


def test():
    users = [{"id": 1, "name": "Alice", "email": "a@b.com"}]
    assert get_user_name(users, 1) == "Alice"
    return True
''',
        "fixed_source": '''
def find_user(users, user_id):
    # Find user by ID.
    for user in users:
        if user.get("id") == user_id:
            return user
    return None


def get_user_name(users, user_id):
    # Fixed: checks for None before accessing.
    user = find_user(users, user_id)
    if user is None:
        return None
    return user["name"]


def get_user_email(users, user_id):
    # Get user email safely.
    user = find_user(users, user_id)
    if user is None:
        return None
    return user["email"]


def list_user_names(users):
    # List all user names.
    return [u["name"] for u in users]


def test():
    users = [{"id": 1, "name": "Alice", "email": "a@b.com"}]
    assert get_user_name(users, 1) == "Alice"
    assert get_user_name(users, 99) is None
    return True
''',
    },
    {
        "id": "buggy_set_intersection",
        "bug_description": "Uses union logic instead of intersection",
        "source": '''
def set_intersection(set_a, set_b):
    # Bug: implements union instead of intersection.
    result = set()
    for item in set_a:
        result.add(item)
    for item in set_b:
        result.add(item)
    return result


def set_difference(set_a, set_b):
    # Compute set difference A - B.
    return {item for item in set_a if item not in set_b}


def set_symmetric_diff(set_a, set_b):
    # Compute symmetric difference.
    return set_difference(set_a, set_b) | set_difference(set_b, set_a)


def jaccard_similarity(set_a, set_b):
    # Compute Jaccard similarity between two sets.
    intersection = set_intersection(set_a, set_b)
    union = set_a | set_b
    if not union:
        return 1.0
    return len(intersection) / len(union)


def test():
    a = {1, 2, 3, 4}
    b = {3, 4, 5, 6}
    assert set_intersection(a, b) == {3, 4}
    return True
''',
        "fixed_source": '''
def set_intersection(set_a, set_b):
    # Fixed: correct intersection logic.
    result = set()
    for item in set_a:
        if item in set_b:
            result.add(item)
    return result


def set_difference(set_a, set_b):
    # Compute set difference A - B.
    return {item for item in set_a if item not in set_b}


def set_symmetric_diff(set_a, set_b):
    # Compute symmetric difference.
    return set_difference(set_a, set_b) | set_difference(set_b, set_a)


def jaccard_similarity(set_a, set_b):
    # Compute Jaccard similarity between two sets.
    intersection = set_intersection(set_a, set_b)
    union = set_a | set_b
    if not union:
        return 1.0
    return len(intersection) / len(union)


def test():
    a = {1, 2, 3, 4}
    b = {3, 4, 5, 6}
    assert set_intersection(a, b) == {3, 4}
    return True
''',
    }
]


# ── main experiment ──────────────────────────────────────────────────────

def main():
    print("=" * 76)
    print("PAPER 05 — Sheaf-Guided Program Repair: Using H\u00b9 to Fix Bugs")
    print("  All numbers from `python3 -m jugeo` CLI (subprocess)")
    print("=" * 76)
    print()

    tmpfiles = []
    all_clean_results = []
    all_buggy_results = []

    # ── 1. Run jugeo bugs on all 100 clean programs ──────────────────────
    print("Phase 1: Bug detection on 100 clean programs ...")
    clean_bug_timings = []
    for name, source in PROGRAMS.items():
        path = write_temp(source)
        tmpfiles.append(path)
        t0 = time.perf_counter()
        objs = run_jugeo("bugs", path)
        wall_s = time.perf_counter() - t0
        clean_bug_timings.append(wall_s)
        bugs_obj = objs[0] if objs else {}
        bugs_list = bugs_obj.get("bugs", [])
        all_clean_results.append({
            "name": name,
            "n_bugs_reported": len(bugs_list),
            "bugs": bugs_list,
            "wall_s": round(wall_s, 4),
        })
        try:
            os.unlink(path)
        except OSError:
            pass

    clean_fp = sum(1 for r in all_clean_results if r["n_bugs_reported"] > 0)
    print(f"  Clean programs with bugs reported (false positives): {clean_fp}/100")
    print()

    # ── 2. Run jugeo bugs on all 50 buggy programs ──────────────────────
    print("Phase 2: Bug detection on 50 buggy programs ...")
    buggy_bug_timings = []
    for entry in BUGGY_PROGRAMS:
        path = write_temp(entry["source"])
        tmpfiles.append(path)
        t0 = time.perf_counter()
        objs = run_jugeo("bugs", path)
        wall_s = time.perf_counter() - t0
        buggy_bug_timings.append(wall_s)
        bugs_obj = objs[0] if objs else {}
        bugs_list = bugs_obj.get("bugs", [])
        entry_result = {
            "id": entry["id"],
            "bug_description": entry["bug_description"],
            "n_bugs_reported": len(bugs_list),
            "detected": len(bugs_list) > 0,
            "bugs": bugs_list,
            "wall_s": round(wall_s, 4),
        }
        all_buggy_results.append(entry_result)
        try:
            os.unlink(path)
        except OSError:
            pass

    detected = sum(1 for r in all_buggy_results if r["detected"])
    print(f"  Buggy programs with bugs detected (true positives): {detected}/50")
    print()

    # ── 3. Run jugeo prove before and after repair ───────────────────────
    print("Phase 3: H\u00b9 before/after repair on 50 buggy programs ...")
    repair_results = []
    prove_timings = []
    for entry in BUGGY_PROGRAMS:
        # Prove on buggy version
        buggy_path = write_temp(entry["source"])
        tmpfiles.append(buggy_path)
        t0 = time.perf_counter()
        buggy_objs = run_jugeo("prove", buggy_path)
        buggy_wall = time.perf_counter() - t0
        prove_timings.append(buggy_wall)

        buggy_prove = buggy_objs[0] if buggy_objs else {}
        buggy_formal = buggy_objs[1] if len(buggy_objs) > 1 else {}
        buggy_finfo = (buggy_prove.get("files") or [{}])[0]
        buggy_obs = buggy_formal.get("formal_verification", {}).get(
            "obstruction_vanishing", {}
        )
        buggy_h1 = buggy_obs.get("H1", "?")

        # Prove on fixed version
        fixed_path = write_temp(entry["fixed_source"])
        tmpfiles.append(fixed_path)
        t0 = time.perf_counter()
        fixed_objs = run_jugeo("prove", fixed_path)
        fixed_wall = time.perf_counter() - t0
        prove_timings.append(fixed_wall)

        fixed_prove = fixed_objs[0] if fixed_objs else {}
        fixed_formal = fixed_objs[1] if len(fixed_objs) > 1 else {}
        fixed_finfo = (fixed_prove.get("files") or [{}])[0]
        fixed_obs = fixed_formal.get("formal_verification", {}).get(
            "obstruction_vanishing", {}
        )
        fixed_h1 = fixed_obs.get("H1", "?")

        repair_results.append({
            "id": entry["id"],
            "buggy_verdict": buggy_finfo.get("verdict", "?"),
            "buggy_trust": buggy_finfo.get("trust", "?"),
            "buggy_h1": buggy_h1,
            "buggy_coords": buggy_finfo.get("coordinates", 0),
            "buggy_props_total": buggy_finfo.get("propositions_total", 0),
            "buggy_props_ok": buggy_finfo.get("propositions_ok", 0),
            "buggy_n_obstructions": len(buggy_finfo.get("obstructions", [])),
            "fixed_verdict": fixed_finfo.get("verdict", "?"),
            "fixed_trust": fixed_finfo.get("trust", "?"),
            "fixed_h1": fixed_h1,
            "fixed_coords": fixed_finfo.get("coordinates", 0),
            "fixed_props_total": fixed_finfo.get("propositions_total", 0),
            "fixed_props_ok": fixed_finfo.get("propositions_ok", 0),
            "fixed_n_obstructions": len(fixed_finfo.get("obstructions", [])),
            "h1_improved": (
                str(fixed_h1) == "0" and str(buggy_h1) != "0"
            ) if buggy_h1 != "?" else None,
            "buggy_wall_s": round(buggy_wall, 4),
            "fixed_wall_s": round(fixed_wall, 4),
        })
        for p in [buggy_path, fixed_path]:
            try:
                os.unlink(p)
            except OSError:
                pass

    # ── 4. Summary statistics ────────────────────────────────────────────
    print()
    print("=" * 76)
    print("RESULTS SUMMARY")
    print("=" * 76)
    print()

    total_buggy = len(BUGGY_PROGRAMS)
    total_clean = len(PROGRAMS)
    tp = detected
    fp = clean_fp
    tpr = tp / total_buggy if total_buggy else 0
    fpr = fp / total_clean if total_clean else 0

    print(f"Bug Detection:")
    print(f"  True positive rate:  {tp}/{total_buggy} = {100*tpr:.1f}%")
    print(f"  False positive rate: {fp}/{total_clean} = {100*fpr:.1f}%")
    print()

    h1_improved_count = sum(1 for r in repair_results if r.get("h1_improved") is True)
    h1_total_valid = sum(1 for r in repair_results if r.get("h1_improved") is not None)
    print(f"H\u00b9 Improvement After Repair:")
    print(f"  Programs where H\u00b9 improved: {h1_improved_count}/{h1_total_valid}")
    print()

    # Timing stats
    all_timings = clean_bug_timings + buggy_bug_timings + prove_timings
    if all_timings:
        sorted_t = sorted(all_timings)
        p95_idx = int(0.95 * len(sorted_t))
        print(f"Timing Statistics ({len(all_timings)} runs):")
        print(f"  Mean:   {statistics.mean(all_timings):.4f}s")
        print(f"  Median: {statistics.median(all_timings):.4f}s")
        print(f"  P95:    {sorted_t[min(p95_idx, len(sorted_t)-1)]:.4f}s")
        print(f"  Min:    {min(all_timings):.4f}s")
        print(f"  Max:    {max(all_timings):.4f}s")
    print()

    # Detail table: buggy program results
    print("BUGGY PROGRAM DETAIL:")
    print("-" * 100)
    print(
        f"  {'ID':<35} {'Det':>4} {'BugH1':>6} {'FixH1':>6} "
        f"{'BugV':<10} {'FixV':<10} {'Impr':>5}"
    )
    print(f"  {'-'*90}")
    for br, rr in zip(all_buggy_results, repair_results):
        det_mark = "Y" if br["detected"] else "N"
        impr = "Y" if rr.get("h1_improved") else ("N" if rr.get("h1_improved") is False else "?")
        print(
            f"  {rr['id']:<35} {det_mark:>4} {str(rr['buggy_h1']):>6} "
            f"{str(rr['fixed_h1']):>6} {rr['buggy_verdict']:<10} "
            f"{rr['fixed_verdict']:<10} {impr:>5}"
        )
    print()

    # ── 5. Save results ──────────────────────────────────────────────────
    output = {
        "experiment": "sheaf_repair",
        "paper": 5,
        "note": "All JuGeo numbers from `python3 -m jugeo` CLI subprocess calls.",
        "n_clean_programs": total_clean,
        "n_buggy_programs": total_buggy,
        "bug_detection": {
            "true_positives": tp,
            "false_positives": fp,
            "true_positive_rate": round(tpr, 4),
            "false_positive_rate": round(fpr, 4),
        },
        "h1_repair": {
            "h1_improved_count": h1_improved_count,
            "h1_total_valid": h1_total_valid,
        },
        "timing": {
            "n_runs": len(all_timings),
            "mean_s": round(statistics.mean(all_timings), 4) if all_timings else None,
            "median_s": round(statistics.median(all_timings), 4) if all_timings else None,
            "p95_s": round(sorted(all_timings)[int(0.95 * len(all_timings))], 4) if all_timings else None,
        },
        "clean_results": all_clean_results,
        "buggy_results": all_buggy_results,
        "repair_results": repair_results,
    }
    outpath = os.path.join(os.path.dirname(__file__), "results_paper05.json")
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"Results -> {outpath}")

    # Cleanup remaining temp files
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
