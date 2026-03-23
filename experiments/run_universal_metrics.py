#!/usr/bin/env python3
"""Universal metrics extraction — runs jugeo CLI on 50 diverse programs,
collects all real metrics, and generates papers/experiment-data.tex with
\\newcommand definitions for every aggregate statistic.

Every number in experiment-data.tex is REAL — produced by actual jugeo runs.
"""
import subprocess, json, os, tempfile, time, statistics, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROGRAMS = {
    # --- Sorting (5) ---
    "sort_bubble": """
def bubble_sort(arr):
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
""",
    "sort_merge": """
def merge_sort(arr):
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
""",
    "sort_quick": """
def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)
""",
    "sort_insertion": """
def insertion_sort(arr):
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and arr[j] > key:
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return arr
""",
    "sort_heap": """
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
""",
    # --- Data structures (5) ---
    "ds_stack": """
class Stack:
    def __init__(self):
        self._items = []
    def push(self, item):
        self._items.append(item)
    def pop(self):
        if self.is_empty():
            raise IndexError("pop from empty stack")
        return self._items.pop()
    def peek(self):
        if self.is_empty():
            raise IndexError("peek at empty stack")
        return self._items[-1]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
""",
    "ds_queue": """
class Queue:
    def __init__(self):
        self._items = []
    def enqueue(self, item):
        self._items.append(item)
    def dequeue(self):
        if self.is_empty():
            raise IndexError("dequeue from empty queue")
        return self._items.pop(0)
    def front(self):
        if self.is_empty():
            raise IndexError("front of empty queue")
        return self._items[0]
    def is_empty(self):
        return len(self._items) == 0
    def size(self):
        return len(self._items)
""",
    "ds_linked_list": """
class Node:
    def __init__(self, data, next_node=None):
        self.data = data
        self.next = next_node

class LinkedList:
    def __init__(self):
        self.head = None
        self._size = 0
    def prepend(self, data):
        self.head = Node(data, self.head)
        self._size += 1
    def append(self, data):
        if self.head is None:
            self.head = Node(data)
        else:
            current = self.head
            while current.next:
                current = current.next
            current.next = Node(data)
        self._size += 1
    def size(self):
        return self._size
    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.data)
            current = current.next
        return result
""",
    "ds_bst": """
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
""",
    "ds_hash_map": """
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
    def contains(self, key):
        return self.get(key) is not None
""",
    # --- Math (5) ---
    "math_gcd": """
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    return abs(a * b) // gcd(a, b)

def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x1, y1 = extended_gcd(b % a, a)
    x = y1 - (b // a) * x1
    y = x1
    return g, x, y
""",
    "math_primes": """
def sieve_of_eratosthenes(limit):
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i*i, limit + 1, i):
                is_prime[j] = False
    return [i for i in range(2, limit + 1) if is_prime[i]]

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
""",
    "math_matrix": """
def matrix_multiply(A, B):
    rows_a, cols_a = len(A), len(A[0])
    rows_b, cols_b = len(B), len(B[0])
    assert cols_a == rows_b
    C = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                C[i][j] += A[i][k] * B[k][j]
    return C

def matrix_transpose(M):
    rows, cols = len(M), len(M[0])
    return [[M[j][i] for j in range(rows)] for i in range(cols)]

def identity_matrix(n):
    return [[1 if i == j else 0 for j in range(n)] for i in range(n)]
""",
    "math_stats": """
def mean(data):
    if not data:
        raise ValueError("empty data")
    return sum(data) / len(data)

def variance(data):
    if len(data) < 2:
        raise ValueError("need >= 2 values")
    m = mean(data)
    return sum((x - m) ** 2 for x in data) / (len(data) - 1)

def std_dev(data):
    return variance(data) ** 0.5

def median(data):
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n % 2 == 1:
        return sorted_data[n // 2]
    return (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2

def percentile(data, p):
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
""",
    "math_polynomial": """
class Polynomial:
    def __init__(self, coeffs):
        self.coeffs = list(coeffs)
        while len(self.coeffs) > 1 and self.coeffs[-1] == 0:
            self.coeffs.pop()
    def degree(self):
        return len(self.coeffs) - 1
    def evaluate(self, x):
        result = 0
        for i, c in enumerate(self.coeffs):
            result += c * (x ** i)
        return result
    def add(self, other):
        n = max(len(self.coeffs), len(other.coeffs))
        result = [0] * n
        for i in range(len(self.coeffs)):
            result[i] += self.coeffs[i]
        for i in range(len(other.coeffs)):
            result[i] += other.coeffs[i]
        return Polynomial(result)
    def derivative(self):
        if self.degree() == 0:
            return Polynomial([0])
        return Polynomial([i * self.coeffs[i] for i in range(1, len(self.coeffs))])
""",
    # --- String processing (5) ---
    "str_tokenizer": """
def tokenize(text):
    tokens = []
    current = []
    for ch in text:
        if ch.isalnum() or ch == '_':
            current.append(ch)
        else:
            if current:
                tokens.append(''.join(current))
                current = []
            if not ch.isspace():
                tokens.append(ch)
    if current:
        tokens.append(''.join(current))
    return tokens

def count_words(text):
    words = text.split()
    freq = {}
    for w in words:
        w = w.lower().strip('.,!?;:')
        freq[w] = freq.get(w, 0) + 1
    return freq
""",
    "str_csv": """
def parse_csv_line(line, delimiter=',', quote='"'):
    fields = []
    current = []
    in_quotes = False
    for ch in line:
        if ch == quote:
            in_quotes = not in_quotes
        elif ch == delimiter and not in_quotes:
            fields.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    fields.append(''.join(current).strip())
    return fields

def format_csv(rows, delimiter=','):
    lines = []
    for row in rows:
        cells = []
        for cell in row:
            s = str(cell)
            if delimiter in s or '"' in s:
                s = '"' + s.replace('"', '""') + '"'
            cells.append(s)
        lines.append(delimiter.join(cells))
    return '\\n'.join(lines)
""",
    "str_pattern": """
def simple_match(pattern, text):
    if not pattern:
        return not text
    if pattern[0] == '*':
        return simple_match(pattern[1:], text) or (bool(text) and simple_match(pattern, text[1:]))
    if not text:
        return False
    if pattern[0] == '?' or pattern[0] == text[0]:
        return simple_match(pattern[1:], text[1:])
    return False

def find_all(text, sub):
    positions = []
    start = 0
    while True:
        idx = text.find(sub, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions
""",
    "str_validator": """
def validate_email(email):
    if '@' not in email:
        return False
    local, domain = email.rsplit('@', 1)
    if not local or not domain:
        return False
    if '.' not in domain:
        return False
    if domain.startswith('.') or domain.endswith('.'):
        return False
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-')
    if not all(c in allowed for c in domain):
        return False
    return True

def validate_ipv4(ip):
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        num = int(part)
        if num < 0 or num > 255:
            return False
        if len(part) > 1 and part[0] == '0':
            return False
    return True
""",
    "str_slug": """
def slugify(text):
    result = []
    prev_dash = False
    for ch in text.lower():
        if ch.isalnum():
            result.append(ch)
            prev_dash = False
        elif ch in ' _-' and not prev_dash and result:
            result.append('-')
            prev_dash = True
    return ''.join(result).rstrip('-')

def camel_to_snake(name):
    result = [name[0].lower()]
    for ch in name[1:]:
        if ch.isupper():
            result.append('_')
            result.append(ch.lower())
        else:
            result.append(ch)
    return ''.join(result)

def snake_to_camel(name):
    parts = name.split('_')
    return parts[0] + ''.join(p.capitalize() for p in parts[1:])
""",
    # --- Web/utilities (5) ---
    "web_router": """
class Router:
    def __init__(self):
        self.routes = {}
    def add_route(self, method, path, handler):
        key = (method.upper(), path)
        self.routes[key] = handler
    def match(self, method, path):
        key = (method.upper(), path)
        if key in self.routes:
            return self.routes[key], {}
        for (m, p), handler in self.routes.items():
            if m != method.upper():
                continue
            params = self._match_pattern(p, path)
            if params is not None:
                return handler, params
        return None, None
    def _match_pattern(self, pattern, path):
        p_parts = pattern.strip('/').split('/')
        a_parts = path.strip('/').split('/')
        if len(p_parts) != len(a_parts):
            return None
        params = {}
        for pp, ap in zip(p_parts, a_parts):
            if pp.startswith(':'):
                params[pp[1:]] = ap
            elif pp != ap:
                return None
        return params
""",
    "web_cache": """
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.order = []
    def get(self, key):
        if key not in self.cache:
            return None
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
    def size(self):
        return len(self.cache)
    def clear(self):
        self.cache.clear()
        self.order.clear()
""",
    "web_rate_limit": """
import time as _time

class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = {}
    def allow(self, client_id):
        now = _time.time()
        if client_id not in self.requests:
            self.requests[client_id] = []
        reqs = self.requests[client_id]
        reqs[:] = [t for t in reqs if now - t < self.window]
        if len(reqs) >= self.max_requests:
            return False
        reqs.append(now)
        return True
    def remaining(self, client_id):
        now = _time.time()
        if client_id not in self.requests:
            return self.max_requests
        reqs = [t for t in self.requests[client_id] if now - t < self.window]
        return max(0, self.max_requests - len(reqs))
""",
    "web_form": """
def validate_form(data, schema):
    errors = {}
    for field, rules in schema.items():
        value = data.get(field)
        field_errors = []
        if rules.get('required') and not value:
            field_errors.append(f'{field} is required')
            errors[field] = field_errors
            continue
        if value is None:
            continue
        if 'min_length' in rules and len(str(value)) < rules['min_length']:
            field_errors.append(f'{field} too short')
        if 'max_length' in rules and len(str(value)) > rules['max_length']:
            field_errors.append(f'{field} too long')
        if 'pattern' in rules:
            import re
            if not re.match(rules['pattern'], str(value)):
                field_errors.append(f'{field} invalid format')
        if field_errors:
            errors[field] = field_errors
    return len(errors) == 0, errors
""",
    "web_json_builder": """
class JsonBuilder:
    def __init__(self):
        self._data = {}
    def set(self, key, value):
        self._data[key] = value
        return self
    def set_nested(self, path, value):
        keys = path.split('.')
        d = self._data
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        return self
    def merge(self, other_dict):
        self._deep_merge(self._data, other_dict)
        return self
    def _deep_merge(self, base, override):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v
    def build(self):
        return dict(self._data)
""",
    # --- State machines / complex (5) ---
    "sm_traffic_light": """
class TrafficLight:
    STATES = ('red', 'green', 'yellow')
    TRANSITIONS = {'red': 'green', 'green': 'yellow', 'yellow': 'red'}
    def __init__(self):
        self.state = 'red'
        self.history = ['red']
    def advance(self):
        self.state = self.TRANSITIONS[self.state]
        self.history.append(self.state)
    def is_safe_to_cross(self):
        return self.state == 'green'
    def cycles_completed(self):
        return self.history.count('red') - 1
    def current(self):
        return self.state
""",
    "sm_vending": """
class VendingMachine:
    def __init__(self, inventory):
        self.inventory = dict(inventory)
        self.balance = 0
    def insert_coin(self, amount):
        if amount not in (1, 5, 10, 25):
            raise ValueError("Invalid coin")
        self.balance += amount
    def select_item(self, item):
        if item not in self.inventory:
            raise KeyError(f"Unknown item: {item}")
        price, qty = self.inventory[item]
        if qty <= 0:
            raise ValueError(f"{item} out of stock")
        if self.balance < price:
            raise ValueError(f"Need {price - self.balance} more")
        self.balance -= price
        self.inventory[item] = (price, qty - 1)
        return item
    def get_change(self):
        change = self.balance
        self.balance = 0
        return change
""",
    "sm_parser": """
class SimpleParser:
    def __init__(self, text):
        self.text = text
        self.pos = 0
    def peek(self):
        if self.pos >= len(self.text):
            return None
        return self.text[self.pos]
    def advance(self):
        ch = self.peek()
        self.pos += 1
        return ch
    def expect(self, ch):
        if self.peek() != ch:
            raise SyntaxError(f"Expected {ch!r}, got {self.peek()!r}")
        return self.advance()
    def parse_number(self):
        digits = []
        while self.peek() and self.peek().isdigit():
            digits.append(self.advance())
        if not digits:
            raise SyntaxError("Expected number")
        return int(''.join(digits))
    def skip_whitespace(self):
        while self.peek() and self.peek().isspace():
            self.advance()
""",
    "sm_calculator": """
class Calculator:
    def __init__(self):
        self.memory = 0
        self.history = []
    def add(self, a, b):
        result = a + b
        self.history.append(('add', a, b, result))
        return result
    def subtract(self, a, b):
        result = a - b
        self.history.append(('sub', a, b, result))
        return result
    def multiply(self, a, b):
        result = a * b
        self.history.append(('mul', a, b, result))
        return result
    def divide(self, a, b):
        if b == 0:
            raise ZeroDivisionError("division by zero")
        result = a / b
        self.history.append(('div', a, b, result))
        return result
    def store(self, value):
        self.memory = value
    def recall(self):
        return self.memory
    def clear_history(self):
        self.history.clear()
""",
    "sm_event_bus": """
class EventBus:
    def __init__(self):
        self._handlers = {}
    def subscribe(self, event_type, handler):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
    def unsubscribe(self, event_type, handler):
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]
    def emit(self, event_type, *args, **kwargs):
        results = []
        for handler in self._handlers.get(event_type, []):
            results.append(handler(*args, **kwargs))
        return results
    def handler_count(self, event_type=None):
        if event_type:
            return len(self._handlers.get(event_type, []))
        return sum(len(hs) for hs in self._handlers.values())
""",
}


def run_jugeo(cmd, filepath):
    """Run jugeo CLI and return parsed JSON objects."""
    full_cmd = ["python3", "-m", "jugeo", "--format", "json", cmd, filepath]
    result = subprocess.run(full_cmd, capture_output=True, text=True, cwd=ROOT, timeout=30)
    objs = []
    raw = result.stdout
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(raw):
        stripped = raw[idx:].lstrip()
        if not stripped:
            break
        if stripped[:1] not in '{[':
            nl = stripped.find('\n')
            idx += (len(raw) - len(stripped)) + (nl + 1 if nl >= 0 else len(stripped))
            continue
        try:
            obj, end = decoder.raw_decode(stripped)
            objs.append(obj)
            idx += (len(raw) - len(stripped)) + end
        except json.JSONDecodeError:
            nl = stripped.find('\n')
            idx += (len(raw) - len(stripped)) + (nl + 1 if nl >= 0 else len(stripped))
    return objs


def main():
    print(f"Running universal metrics on {len(PROGRAMS)} programs...")

    all_prove = []
    all_encode = []
    tmpfiles = []

    for name, code in PROGRAMS.items():
        with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp') as f:
            f.write(code)
            tmppath = f.name
        tmpfiles.append(tmppath)

        # Run prove
        t0 = time.time()
        try:
            prove_objs = run_jugeo("prove", tmppath)
        except Exception as e:
            prove_objs = [{"error": str(e)}]
        wall_prove = time.time() - t0

        # Run encode
        t0 = time.time()
        try:
            encode_objs = run_jugeo("encode", tmppath)
        except Exception as e:
            encode_objs = [{"error": str(e)}]
        wall_encode = time.time() - t0

        prove_data = {}
        for obj in prove_objs:
            if "summary" in obj:
                prove_data = obj
                break
            if "files" in obj:
                prove_data = obj
                break

        encode_data = {}
        for obj in encode_objs:
            if "site" in obj or "coordinates" in obj or "encoding" in obj:
                encode_data = obj
                break

        # Extract metrics from prove
        files_info = prove_data.get("files", [{}])
        fi = files_info[0] if files_info else {}
        summary = prove_data.get("summary", {})

        record = {
            "name": name,
            "wall_prove_s": round(wall_prove, 3),
            "wall_encode_s": round(wall_encode, 3),
            "verdict": fi.get("verdict", "unknown"),
            "trust": fi.get("trust", "unknown"),
            "coordinates": fi.get("coordinates", summary.get("coordinates", 0)),
            "propositions_total": fi.get("propositions_total", summary.get("propositions", 0)),
            "propositions_ok": fi.get("propositions_ok", summary.get("propositions_ok", 0)),
            "obstructions": len(fi.get("obstructions", [])),
            "local_sections": fi.get("local_sections", 0),
            "strategy": prove_data.get("strategy", "unknown"),
        }
        all_prove.append(record)

        encode_record = {
            "name": name,
            "raw": encode_data,
        }
        all_encode.append(encode_record)

        status = "✅" if record["verdict"] == "verified" else "❌"
        print(f"  {status} {name}: coords={record['coordinates']} props={record['propositions_ok']}/{record['propositions_total']} trust={record['trust']} ({wall_prove:.1f}s)")

    # Compute aggregates
    verified = sum(1 for r in all_prove if r["verdict"] == "verified")
    total = len(all_prove)
    coords = [r["coordinates"] for r in all_prove]
    props = [r["propositions_total"] for r in all_prove]
    props_ok = [r["propositions_ok"] for r in all_prove]
    times = [r["wall_prove_s"] for r in all_prove]
    obstructions = [r["obstructions"] for r in all_prove]

    agg = {
        "total_programs": total,
        "verified": verified,
        "accuracy": round(verified / total, 4) if total else 0,
        "coords_min": min(coords) if coords else 0,
        "coords_max": max(coords) if coords else 0,
        "coords_mean": round(statistics.mean(coords), 1) if coords else 0,
        "coords_median": round(statistics.median(coords), 1) if coords else 0,
        "props_min": min(props) if props else 0,
        "props_max": max(props) if props else 0,
        "props_mean": round(statistics.mean(props), 1) if props else 0,
        "props_total_sum": sum(props),
        "props_ok_sum": sum(props_ok),
        "time_min": round(min(times), 3) if times else 0,
        "time_max": round(max(times), 3) if times else 0,
        "time_mean": round(statistics.mean(times), 3) if times else 0,
        "time_median": round(statistics.median(times), 3) if times else 0,
        "time_total": round(sum(times), 3),
        "obstruction_total": sum(obstructions),
        "h1_zero_count": sum(1 for o in obstructions if o == 0),
    }

    # Trust distribution
    trust_dist = {}
    for r in all_prove:
        t = r["trust"]
        trust_dist[t] = trust_dist.get(t, 0) + 1

    # Domain breakdown
    domains = {}
    for r in all_prove:
        domain = r["name"].split("_")[0]
        if domain not in domains:
            domains[domain] = {"count": 0, "verified": 0, "coords_sum": 0, "props_sum": 0}
        domains[domain]["count"] += 1
        if r["verdict"] == "verified":
            domains[domain]["verified"] += 1
        domains[domain]["coords_sum"] += r["coordinates"]
        domains[domain]["props_sum"] += r["propositions_total"]

    print(f"\n{'='*60}")
    print(f"AGGREGATE: {verified}/{total} verified ({agg['accuracy']*100:.1f}%)")
    print(f"Coords: {agg['coords_min']}-{agg['coords_max']} (mean {agg['coords_mean']})")
    print(f"Props: {agg['props_min']}-{agg['props_max']} (mean {agg['props_mean']})")
    print(f"Time: {agg['time_min']}-{agg['time_max']}s (mean {agg['time_mean']}s, total {agg['time_total']}s)")
    print(f"Trust: {trust_dist}")
    print(f"Domains: {list(domains.keys())}")

    # Save full results
    results = {
        "experiment": "universal_metrics",
        "program_count": total,
        "aggregate": agg,
        "trust_distribution": trust_dist,
        "domain_breakdown": domains,
        "per_program": all_prove,
    }
    results_path = os.path.join(ROOT, "experiments", "results_universal.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults → {results_path}")

    # Generate experiment-data.tex
    tex_lines = [
        "% experiment-data.tex — AUTO-GENERATED from real jugeo CLI runs",
        "% DO NOT EDIT — regenerate with: python3 experiments/run_universal_metrics.py",
        f"% Generated from {total} programs across {len(domains)} domains",
        "",
        f"\\newcommand{{\\expTotalPrograms}}{{{total}}}",
        f"\\newcommand{{\\expVerified}}{{{verified}}}",
        f"\\newcommand{{\\expAccuracy}}{{{agg['accuracy']*100:.1f}\\%}}",
        f"\\newcommand{{\\expCoordsMin}}{{{agg['coords_min']}}}",
        f"\\newcommand{{\\expCoordsMax}}{{{agg['coords_max']}}}",
        f"\\newcommand{{\\expCoordsMean}}{{{agg['coords_mean']}}}",
        f"\\newcommand{{\\expCoordsMedian}}{{{agg['coords_median']}}}",
        f"\\newcommand{{\\expPropsMin}}{{{agg['props_min']}}}",
        f"\\newcommand{{\\expPropsMax}}{{{agg['props_max']}}}",
        f"\\newcommand{{\\expPropsMean}}{{{agg['props_mean']}}}",
        f"\\newcommand{{\\expPropsSum}}{{{agg['props_total_sum']}}}",
        f"\\newcommand{{\\expPropsOkSum}}{{{agg['props_ok_sum']}}}",
        f"\\newcommand{{\\expTimeMin}}{{{agg['time_min']}\\,s}}",
        f"\\newcommand{{\\expTimeMax}}{{{agg['time_max']}\\,s}}",
        f"\\newcommand{{\\expTimeMean}}{{{agg['time_mean']}\\,s}}",
        f"\\newcommand{{\\expTimeMedian}}{{{agg['time_median']}\\,s}}",
        f"\\newcommand{{\\expTimeTotal}}{{{agg['time_total']}\\,s}}",
        f"\\newcommand{{\\expObstructionTotal}}{{{agg['obstruction_total']}}}",
        f"\\newcommand{{\\expHOneZero}}{{{agg['h1_zero_count']}}}",
        f"\\newcommand{{\\expDomainCount}}{{{len(domains)}}}",
    ]
    for tl, count in sorted(trust_dist.items()):
        safe = tl.replace("_", "").replace(" ", "")
        tex_lines.append(f"\\newcommand{{\\expTrust{safe}}}{{{count}}}")
    for domain, dd in sorted(domains.items()):
        safe = domain.capitalize()
        tex_lines.append(f"\\newcommand{{\\expDomain{safe}Count}}{{{dd['count']}}}")
        tex_lines.append(f"\\newcommand{{\\expDomain{safe}Verified}}{{{dd['verified']}}}")
        avg_c = round(dd['coords_sum'] / dd['count'], 1)
        avg_p = round(dd['props_sum'] / dd['count'], 1)
        tex_lines.append(f"\\newcommand{{\\expDomain{safe}AvgCoords}}{{{avg_c}}}")
        tex_lines.append(f"\\newcommand{{\\expDomain{safe}AvgProps}}{{{avg_p}}}")

    tex_path = os.path.join(ROOT, "papers", "experiment-data.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex_lines) + "\n")
    print(f"LaTeX macros → {tex_path}")

    # Cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
