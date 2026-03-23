#!/usr/bin/env python3
"""
Experiment 06 — Semantic Moves: Morphism Counts Across 100 Programs
====================================================================

Benchmark experiment for JuGeo (Judgment Geometry) that measures morphism
counts and types across 100 Python programs spanning four categories:
pure_function, multi_function, class, and module_with_deps.

Uses ``jugeo prove`` and ``jugeo encode`` CLI commands to analyse each
program's categorical structure, then computes morphism statistics and
cross-category comparisons.

Every number is produced by calling real JuGeo CLI commands.
Reproducibility: random.seed(42).
"""

import ast, subprocess, json, os, random, sys, time, tempfile
from collections import defaultdict

random.seed(42)

ROOT = os.path.join(os.path.dirname(__file__), "..")

# ── helpers ────────────────────────────────────────────────────────────────

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



# ---------------------------------------------------------------------------
# 100 benchmark programs
# ---------------------------------------------------------------------------

PROGRAMS = {
    "class_001_stack": {
        "source": """\
class Stack:
    # A basic stack data structure with push, pop, and peek operations
    def __init__(self, max_size=None):
        self._items = []
        self._max_size = max_size

    def push(self, item):
        if self._max_size is not None and len(self._items) >= self._max_size:
            raise OverflowError("stack is full")
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        if not self._items:
            raise IndexError("peek at empty stack")
        return self._items[-1]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)

    def clear(self):
        self._items = []

    def to_list(self):
        return list(self._items)
""",
        "category": "class",
    },

    "class_002_queue": {
        "source": """\
class Queue:
    # A FIFO queue implemented with a list
    def __init__(self):
        self._items = []
        self._total_enqueued = 0
        self._total_dequeued = 0

    def enqueue(self, item):
        self._items.append(item)
        self._total_enqueued += 1

    def dequeue(self):
        if not self._items:
            raise IndexError("dequeue from empty queue")
        self._total_dequeued += 1
        return self._items.pop(0)

    def front(self):
        if not self._items:
            raise IndexError("front of empty queue")
        return self._items[0]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)

    def stats(self):
        return {
            "current_size": len(self._items),
            "total_enqueued": self._total_enqueued,
            "total_dequeued": self._total_dequeued,
        }
""",
        "category": "class",
    },

    "class_003_lru_cache": {
        "source": """\
class LRUCache:
    # Least Recently Used cache with a fixed capacity
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._cache = {}
        self._order = []
        self._hits = 0
        self._misses = 0

    def get(self, key):
        if key in self._cache:
            self._hits += 1
            self._order.remove(key)
            self._order.append(key)
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, key, value):
        if key in self._cache:
            self._order.remove(key)
        elif len(self._cache) >= self._capacity:
            evicted = self._order.pop(0)
            del self._cache[evicted]
        self._cache[key] = value
        self._order.append(key)

    def size(self):
        return len(self._cache)

    def stats(self):
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {"hits": self._hits, "misses": self._misses, "hit_rate": round(hit_rate, 4)}
""",
        "category": "class",
    },

    "class_004_linked_list": {
        "source": """\
class LinkedList:
    # A singly linked list with basic operations
    def __init__(self):
        self._head = None
        self._size = 0

    def _make_node(self, value):
        return {"value": value, "next": None}

    def append(self, value):
        new_node = self._make_node(value)
        if self._head is None:
            self._head = new_node
        else:
            current = self._head
            while current["next"] is not None:
                current = current["next"]
            current["next"] = new_node
        self._size += 1

    def prepend(self, value):
        new_node = self._make_node(value)
        new_node["next"] = self._head
        self._head = new_node
        self._size += 1

    def remove(self, value):
        if self._head is None:
            return False
        if self._head["value"] == value:
            self._head = self._head["next"]
            self._size -= 1
            return True
        current = self._head
        while current["next"] is not None:
            if current["next"]["value"] == value:
                current["next"] = current["next"]["next"]
                self._size -= 1
                return True
            current = current["next"]
        return False

    def to_list(self):
        result = []
        current = self._head
        while current is not None:
            result.append(current["value"])
            current = current["next"]
        return result

    def __len__(self):
        return self._size
""",
        "category": "class",
    },

    "class_005_bst": {
        "source": """\
class BinarySearchTree:
    # A binary search tree for ordered data storage
    def __init__(self):
        self._root = None
        self._count = 0

    def _make_node(self, value):
        return {"value": value, "left": None, "right": None}

    def insert(self, value):
        if self._root is None:
            self._root = self._make_node(value)
            self._count += 1
            return
        current = self._root
        while True:
            if value < current["value"]:
                if current["left"] is None:
                    current["left"] = self._make_node(value)
                    self._count += 1
                    return
                current = current["left"]
            elif value > current["value"]:
                if current["right"] is None:
                    current["right"] = self._make_node(value)
                    self._count += 1
                    return
                current = current["right"]
            else:
                return

    def contains(self, value):
        current = self._root
        while current is not None:
            if value == current["value"]:
                return True
            elif value < current["value"]:
                current = current["left"]
            else:
                current = current["right"]
        return False

    def inorder(self):
        result = []
        stack = []
        current = self._root
        while current is not None or stack:
            while current is not None:
                stack.append(current)
                current = current["left"]
            current = stack.pop()
            result.append(current["value"])
            current = current["right"]
        return result

    def size(self):
        return self._count
""",
        "category": "class",
    },

    "class_006_circular_buffer": {
        "source": """\
class CircularBuffer:
    # Fixed-size circular buffer that overwrites oldest data when full
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._buffer = [None] * capacity
        self._capacity = capacity
        self._head = 0
        self._tail = 0
        self._size = 0

    def write(self, item):
        self._buffer[self._tail] = item
        self._tail = (self._tail + 1) % self._capacity
        if self._size == self._capacity:
            self._head = (self._head + 1) % self._capacity
        else:
            self._size += 1

    def read(self):
        if self._size == 0:
            raise IndexError("read from empty buffer")
        item = self._buffer[self._head]
        self._head = (self._head + 1) % self._capacity
        self._size -= 1
        return item

    def is_full(self):
        return self._size == self._capacity

    def is_empty(self):
        return self._size == 0

    def __len__(self):
        return self._size

    def to_list(self):
        result = []
        idx = self._head
        for _ in range(self._size):
            result.append(self._buffer[idx])
            idx = (idx + 1) % self._capacity
        return result
""",
        "category": "class",
    },

    "class_007_priority_queue": {
        "source": """\
class PriorityQueue:
    # A min-heap based priority queue
    def __init__(self):
        self._heap = []

    def push(self, priority, item):
        self._heap.append((priority, item))
        self._sift_up(len(self._heap) - 1)

    def pop(self):
        if not self._heap:
            raise IndexError("pop from empty priority queue")
        self._swap(0, len(self._heap) - 1)
        priority, item = self._heap.pop()
        if self._heap:
            self._sift_down(0)
        return (priority, item)

    def peek(self):
        if not self._heap:
            raise IndexError("peek at empty priority queue")
        return self._heap[0]

    def _sift_up(self, idx):
        while idx > 0:
            parent = (idx - 1) // 2
            if self._heap[idx][0] < self._heap[parent][0]:
                self._swap(idx, parent)
                idx = parent
            else:
                break

    def _sift_down(self, idx):
        size = len(self._heap)
        while True:
            smallest = idx
            left = 2 * idx + 1
            right = 2 * idx + 2
            if left < size and self._heap[left][0] < self._heap[smallest][0]:
                smallest = left
            if right < size and self._heap[right][0] < self._heap[smallest][0]:
                smallest = right
            if smallest != idx:
                self._swap(idx, smallest)
                idx = smallest
            else:
                break

    def _swap(self, i, j):
        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]

    def __len__(self):
        return len(self._heap)
""",
        "category": "class",
    },

    "class_008_rate_limiter": {
        "source": """\
class TokenBucketRateLimiter:
    # Token bucket rate limiter for controlling request rates
    def __init__(self, capacity, refill_rate):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._tokens = float(capacity)
        self._last_refill_time = 0.0
        self._total_allowed = 0
        self._total_denied = 0

    def refill(self, current_time):
        elapsed = current_time - self._last_refill_time
        if elapsed > 0:
            new_tokens = elapsed * self._refill_rate
            self._tokens = min(self._capacity, self._tokens + new_tokens)
            self._last_refill_time = current_time

    def allow_request(self, current_time, cost=1.0):
        self.refill(current_time)
        if self._tokens >= cost:
            self._tokens -= cost
            self._total_allowed += 1
            return True
        self._total_denied += 1
        return False

    def tokens_available(self):
        return self._tokens

    def stats(self):
        return {
            "allowed": self._total_allowed,
            "denied": self._total_denied,
            "tokens": round(self._tokens, 2),
        }
""",
        "category": "class",
    },

    "class_009_state_machine": {
        "source": """\
class StateMachine:
    # A simple finite state machine
    def __init__(self, initial_state):
        self._state = initial_state
        self._transitions = {}
        self._history = [initial_state]
        self._on_enter = {}
        self._on_exit = {}

    def add_transition(self, from_state, event, to_state):
        key = (from_state, event)
        self._transitions[key] = to_state

    def set_on_enter(self, state, callback):
        self._on_enter[state] = callback

    def set_on_exit(self, state, callback):
        self._on_exit[state] = callback

    def trigger(self, event):
        key = (self._state, event)
        if key not in self._transitions:
            raise ValueError("no transition from %s on event %s" % (self._state, event))
        old_state = self._state
        new_state = self._transitions[key]
        if old_state in self._on_exit:
            self._on_exit[old_state](old_state, event)
        self._state = new_state
        self._history.append(new_state)
        if new_state in self._on_enter:
            self._on_enter[new_state](new_state, event)
        return new_state

    def current_state(self):
        return self._state

    def get_history(self):
        return list(self._history)
""",
        "category": "class",
    },

    "class_010_shopping_cart": {
        "source": """\
class ShoppingCart:
    # A shopping cart with item management and total calculation
    def __init__(self):
        self._items = {}
        self._discount_rate = 0.0

    def add_item(self, name, price, quantity=1):
        if price < 0:
            raise ValueError("price must be non-negative")
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        if name in self._items:
            self._items[name]["quantity"] += quantity
        else:
            self._items[name] = {"price": price, "quantity": quantity}

    def remove_item(self, name):
        if name not in self._items:
            raise KeyError("item not in cart: " + name)
        del self._items[name]

    def update_quantity(self, name, quantity):
        if name not in self._items:
            raise KeyError("item not in cart: " + name)
        if quantity < 1:
            self.remove_item(name)
        else:
            self._items[name]["quantity"] = quantity

    def set_discount(self, rate):
        if rate < 0 or rate > 1:
            raise ValueError("discount rate must be between 0 and 1")
        self._discount_rate = rate

    def subtotal(self):
        total = 0.0
        for item in self._items.values():
            total += item["price"] * item["quantity"]
        return round(total, 2)

    def total(self):
        sub = self.subtotal()
        discount = sub * self._discount_rate
        return round(sub - discount, 2)

    def item_count(self):
        return sum(item["quantity"] for item in self._items.values())
""",
        "category": "class",
    },

    "class_011_bank_account": {
        "source": """\
class BankAccount:
    # A bank account with deposit, withdraw, and transaction history
    def __init__(self, owner, initial_balance=0.0):
        if initial_balance < 0:
            raise ValueError("initial balance must be non-negative")
        self._owner = owner
        self._balance = initial_balance
        self._transactions = []
        self._is_frozen = False

    def deposit(self, amount):
        if self._is_frozen:
            raise RuntimeError("account is frozen")
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        self._balance += amount
        self._transactions.append({"type": "deposit", "amount": amount})
        return self._balance

    def withdraw(self, amount):
        if self._is_frozen:
            raise RuntimeError("account is frozen")
        if amount <= 0:
            raise ValueError("withdrawal amount must be positive")
        if amount > self._balance:
            raise ValueError("insufficient funds")
        self._balance -= amount
        self._transactions.append({"type": "withdrawal", "amount": amount})
        return self._balance

    def freeze(self):
        self._is_frozen = True

    def unfreeze(self):
        self._is_frozen = False

    def get_balance(self):
        return round(self._balance, 2)

    def get_statement(self):
        return {
            "owner": self._owner,
            "balance": self.get_balance(),
            "transactions": len(self._transactions),
            "frozen": self._is_frozen,
        }
""",
        "category": "class",
    },

    "class_012_inventory": {
        "source": """\
class InventoryTracker:
    # Track inventory items with quantities and thresholds
    def __init__(self):
        self._items = {}
        self._low_threshold = 10

    def add_product(self, sku, name, quantity=0):
        if sku in self._items:
            raise ValueError("product already exists: " + sku)
        self._items[sku] = {"name": name, "quantity": quantity, "reserved": 0}

    def restock(self, sku, quantity):
        if sku not in self._items:
            raise KeyError("unknown product: " + sku)
        if quantity < 1:
            raise ValueError("quantity must be positive")
        self._items[sku]["quantity"] += quantity

    def reserve(self, sku, quantity):
        if sku not in self._items:
            raise KeyError("unknown product: " + sku)
        available = self._items[sku]["quantity"] - self._items[sku]["reserved"]
        if quantity > available:
            raise ValueError("insufficient stock")
        self._items[sku]["reserved"] += quantity

    def fulfill(self, sku, quantity):
        if sku not in self._items:
            raise KeyError("unknown product: " + sku)
        item = self._items[sku]
        if quantity > item["reserved"]:
            raise ValueError("cannot fulfill more than reserved")
        item["reserved"] -= quantity
        item["quantity"] -= quantity

    def get_low_stock(self):
        low = []
        for sku, item in self._items.items():
            available = item["quantity"] - item["reserved"]
            if available <= self._low_threshold:
                low.append({"sku": sku, "name": item["name"], "available": available})
        return low

    def set_low_threshold(self, threshold):
        self._low_threshold = threshold
""",
        "category": "class",
    },

    "class_013_stopwatch": {
        "source": """\
class Stopwatch:
    # A stopwatch that tracks elapsed time and lap times
    def __init__(self):
        self._start_time = None
        self._stop_time = None
        self._laps = []
        self._is_running = False
        self._accumulated = 0.0

    def start(self, current_time):
        if self._is_running:
            raise RuntimeError("stopwatch is already running")
        self._start_time = current_time
        self._is_running = True

    def stop(self, current_time):
        if not self._is_running:
            raise RuntimeError("stopwatch is not running")
        self._stop_time = current_time
        elapsed = current_time - self._start_time
        self._accumulated += elapsed
        self._is_running = False

    def lap(self, current_time):
        if not self._is_running:
            raise RuntimeError("stopwatch is not running")
        elapsed = current_time - self._start_time
        total = self._accumulated + elapsed
        self._laps.append(round(total, 4))
        return round(total, 4)

    def reset(self):
        self._start_time = None
        self._stop_time = None
        self._laps = []
        self._is_running = False
        self._accumulated = 0.0

    def get_elapsed(self, current_time=None):
        if self._is_running and current_time is not None:
            return round(self._accumulated + (current_time - self._start_time), 4)
        return round(self._accumulated, 4)

    def get_laps(self):
        return list(self._laps)
""",
        "category": "class",
    },

    "class_014_histogram_builder": {
        "source": """\
class HistogramBuilder:
    # Incrementally build a frequency histogram
    def __init__(self):
        self._counts = {}
        self._total = 0
        self._min_val = None
        self._max_val = None

    def add(self, value):
        self._counts[value] = self._counts.get(value, 0) + 1
        self._total += 1
        if self._min_val is None or value < self._min_val:
            self._min_val = value
        if self._max_val is None or value > self._max_val:
            self._max_val = value

    def add_many(self, values):
        for v in values:
            self.add(v)

    def get_count(self, value):
        return self._counts.get(value, 0)

    def get_frequency(self, value):
        if self._total == 0:
            return 0.0
        return self._counts.get(value, 0) / self._total

    def most_common(self, n=5):
        items = list(self._counts.items())
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:n]

    def summary(self):
        return {
            "total": self._total,
            "unique": len(self._counts),
            "min": self._min_val,
            "max": self._max_val,
        }
""",
        "category": "class",
    },

    "class_015_moving_window": {
        "source": """\
class MovingWindow:
    # A fixed-size sliding window over a stream of values
    def __init__(self, window_size):
        if window_size < 1:
            raise ValueError("window_size must be at least 1")
        self._size = window_size
        self._data = []
        self._sum = 0.0

    def add(self, value):
        self._data.append(value)
        self._sum += value
        if len(self._data) > self._size:
            removed = self._data.pop(0)
            self._sum -= removed

    def mean(self):
        if not self._data:
            return 0.0
        return self._sum / len(self._data)

    def current_min(self):
        if not self._data:
            raise ValueError("window is empty")
        return min(self._data)

    def current_max(self):
        if not self._data:
            raise ValueError("window is empty")
        return max(self._data)

    def is_full(self):
        return len(self._data) >= self._size

    def values(self):
        return list(self._data)

    def variance(self):
        if len(self._data) < 2:
            return 0.0
        m = self.mean()
        return sum((x - m) ** 2 for x in self._data) / len(self._data)
""",
        "category": "class",
    },

    "class_016_matrix": {
        "source": """\
class Matrix:
    # A matrix class supporting basic linear algebra operations
    def __init__(self, data):
        if not data or not data[0]:
            raise ValueError("matrix data must be non-empty")
        self._rows = len(data)
        self._cols = len(data[0])
        self._data = []
        for row in data:
            if len(row) != self._cols:
                raise ValueError("all rows must have same length")
            self._data.append(list(row))

    def get(self, row, col):
        return self._data[row][col]

    def set(self, row, col, value):
        self._data[row][col] = value

    def shape(self):
        return (self._rows, self._cols)

    def transpose(self):
        result = []
        for j in range(self._cols):
            new_row = []
            for i in range(self._rows):
                new_row.append(self._data[i][j])
            result.append(new_row)
        return Matrix(result)

    def add(self, other):
        if self._rows != other._rows or self._cols != other._cols:
            raise ValueError("matrices must have same dimensions")
        result = []
        for i in range(self._rows):
            row = []
            for j in range(self._cols):
                row.append(self._data[i][j] + other._data[i][j])
            result.append(row)
        return Matrix(result)

    def scalar_multiply(self, scalar):
        result = []
        for i in range(self._rows):
            row = []
            for j in range(self._cols):
                row.append(self._data[i][j] * scalar)
            result.append(row)
        return Matrix(result)

    def to_list(self):
        return [list(row) for row in self._data]
""",
        "category": "class",
    },

    "class_017_polynomial": {
        "source": """\
class Polynomial:
    # A polynomial class with arithmetic operations
    def __init__(self, coefficients):
        # coefficients[i] is the coefficient for x^i
        self._coeffs = list(coefficients) if coefficients else [0]
        self._trim()

    def _trim(self):
        while len(self._coeffs) > 1 and self._coeffs[-1] == 0:
            self._coeffs.pop()

    def degree(self):
        if len(self._coeffs) == 1 and self._coeffs[0] == 0:
            return -1
        return len(self._coeffs) - 1

    def evaluate(self, x):
        result = 0
        power = 1
        for coeff in self._coeffs:
            result += coeff * power
            power *= x
        return result

    def add(self, other):
        max_len = max(len(self._coeffs), len(other._coeffs))
        result = []
        for i in range(max_len):
            a = self._coeffs[i] if i < len(self._coeffs) else 0
            b = other._coeffs[i] if i < len(other._coeffs) else 0
            result.append(a + b)
        return Polynomial(result)

    def multiply(self, other):
        result = [0] * (len(self._coeffs) + len(other._coeffs) - 1)
        for i, a in enumerate(self._coeffs):
            for j, b in enumerate(other._coeffs):
                result[i + j] += a * b
        return Polynomial(result)

    def coefficients(self):
        return list(self._coeffs)
""",
        "category": "class",
    },

    "class_018_fraction": {
        "source": """\
class Fraction:
    # An exact fraction class with arithmetic operations
    def __init__(self, numerator, denominator=1):
        if denominator == 0:
            raise ZeroDivisionError("denominator cannot be zero")
        if denominator < 0:
            numerator = -numerator
            denominator = -denominator
        common = self._gcd(abs(numerator), denominator)
        self._num = numerator // common
        self._den = denominator // common

    def _gcd(self, a, b):
        while b:
            a, b = b, a % b
        return a

    def add(self, other):
        num = self._num * other._den + other._num * self._den
        den = self._den * other._den
        return Fraction(num, den)

    def subtract(self, other):
        num = self._num * other._den - other._num * self._den
        den = self._den * other._den
        return Fraction(num, den)

    def multiply(self, other):
        return Fraction(self._num * other._num, self._den * other._den)

    def divide(self, other):
        if other._num == 0:
            raise ZeroDivisionError("cannot divide by zero")
        return Fraction(self._num * other._den, self._den * other._num)

    def to_float(self):
        return self._num / self._den

    def numerator(self):
        return self._num

    def denominator(self):
        return self._den

    def is_whole(self):
        return self._den == 1
""",
        "category": "class",
    },

    "class_019_range_set": {
        "source": """\
class RangeSet:
    # A set of non-overlapping integer ranges with merge support
    def __init__(self):
        self._ranges = []

    def add(self, start, end):
        if start > end:
            raise ValueError("start must be <= end")
        new_ranges = []
        inserted = False
        new_start = start
        new_end = end
        for rng_start, rng_end in self._ranges:
            if rng_end < new_start - 1:
                new_ranges.append((rng_start, rng_end))
            elif rng_start > new_end + 1:
                if not inserted:
                    new_ranges.append((new_start, new_end))
                    inserted = True
                new_ranges.append((rng_start, rng_end))
            else:
                new_start = min(new_start, rng_start)
                new_end = max(new_end, rng_end)
        if not inserted:
            new_ranges.append((new_start, new_end))
        self._ranges = new_ranges

    def contains(self, value):
        for start, end in self._ranges:
            if start <= value <= end:
                return True
        return False

    def total_covered(self):
        total = 0
        for start, end in self._ranges:
            total += end - start + 1
        return total

    def get_ranges(self):
        return list(self._ranges)

    def num_ranges(self):
        return len(self._ranges)
""",
        "category": "class",
    },

    "class_020_counter": {
        "source": """\
class Counter:
    # A versatile counter and accumulator with named channels
    def __init__(self):
        self._channels = {}
        self._default = 0

    def increment(self, channel="default", amount=1):
        if channel not in self._channels:
            self._channels[channel] = 0
        self._channels[channel] += amount

    def decrement(self, channel="default", amount=1):
        if channel not in self._channels:
            self._channels[channel] = 0
        self._channels[channel] -= amount

    def get(self, channel="default"):
        return self._channels.get(channel, 0)

    def reset(self, channel=None):
        if channel is None:
            self._channels.clear()
        elif channel in self._channels:
            self._channels[channel] = 0

    def total(self):
        return sum(self._channels.values())

    def channels(self):
        return list(self._channels.keys())

    def snapshot(self):
        result = {}
        for key, value in self._channels.items():
            result[key] = value
        result["_total"] = self.total()
        return result

    def merge(self, other):
        for channel in other.channels():
            self.increment(channel, other.get(channel))
""",
        "category": "class",
    },

    "class_021_event_emitter": {
        "source": """\
class EventEmitter:
    # A simple event emitter supporting subscribe, unsubscribe, and emit
    def __init__(self):
        self._listeners = {}
        self._once_listeners = {}
        self._emit_count = 0

    def on(self, event, callback):
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    def once(self, event, callback):
        if event not in self._once_listeners:
            self._once_listeners[event] = []
        self._once_listeners[event].append(callback)

    def off(self, event, callback):
        if event in self._listeners:
            listeners = self._listeners[event]
            self._listeners[event] = [cb for cb in listeners if cb is not callback]

    def emit(self, event, *args):
        self._emit_count += 1
        results = []
        if event in self._listeners:
            for callback in self._listeners[event]:
                result = callback(*args)
                results.append(result)
        if event in self._once_listeners:
            for callback in self._once_listeners[event]:
                result = callback(*args)
                results.append(result)
            del self._once_listeners[event]
        return results

    def listener_count(self, event):
        regular = len(self._listeners.get(event, []))
        once = len(self._once_listeners.get(event, []))
        return regular + once

    def event_names(self):
        names = set(self._listeners.keys())
        names.update(self._once_listeners.keys())
        return sorted(names)
""",
        "category": "class",
    },

    "class_022_trie": {
        "source": """\
class Trie:
    # A trie (prefix tree) for efficient string prefix operations
    def __init__(self):
        self._root = {"children": {}, "is_end": False}
        self._word_count = 0

    def insert(self, word):
        node = self._root
        for char in word:
            if char not in node["children"]:
                node["children"][char] = {"children": {}, "is_end": False}
            node = node["children"][char]
        if not node["is_end"]:
            node["is_end"] = True
            self._word_count += 1

    def search(self, word):
        node = self._find_node(word)
        return node is not None and node["is_end"]

    def starts_with(self, prefix):
        return self._find_node(prefix) is not None

    def _find_node(self, prefix):
        node = self._root
        for char in prefix:
            if char not in node["children"]:
                return None
            node = node["children"][char]
        return node

    def words_with_prefix(self, prefix):
        node = self._find_node(prefix)
        if node is None:
            return []
        results = []
        stack = [(node, prefix)]
        while stack:
            current, path = stack.pop()
            if current["is_end"]:
                results.append(path)
            for char in sorted(current["children"].keys()):
                stack.append((current["children"][char], path + char))
        return results

    def word_count(self):
        return self._word_count
""",
        "category": "class",
    },

    "class_023_bloom_filter": {
        "source": """\
class BloomFilter:
    # A probabilistic set membership data structure
    def __init__(self, size, num_hash_functions):
        if size < 1:
            raise ValueError("size must be at least 1")
        if num_hash_functions < 1:
            raise ValueError("num_hash_functions must be at least 1")
        self._size = size
        self._num_hashes = num_hash_functions
        self._bits = [False] * size
        self._count = 0

    def _get_hashes(self, item):
        hashes = []
        item_str = str(item)
        for i in range(self._num_hashes):
            h = 0
            for ch in item_str:
                h = (h * 31 + ord(ch) + i * 37) % self._size
            hashes.append(h)
        return hashes

    def add(self, item):
        hashes = self._get_hashes(item)
        for h in hashes:
            self._bits[h] = True
        self._count += 1

    def might_contain(self, item):
        hashes = self._get_hashes(item)
        for h in hashes:
            if not self._bits[h]:
                return False
        return True

    def fill_ratio(self):
        filled = sum(1 for b in self._bits if b)
        return round(filled / self._size, 4)

    def items_added(self):
        return self._count

    def estimated_false_positive_rate(self):
        ratio = self.fill_ratio()
        return round(ratio ** self._num_hashes, 6)
""",
        "category": "class",
    },

    "class_024_interval_tree": {
        "source": """\
class IntervalTree:
    # A simple interval tree for range overlap queries
    def __init__(self):
        self._intervals = []

    def insert(self, start, end, data=None):
        if start > end:
            raise ValueError("start must be <= end")
        self._intervals.append({"start": start, "end": end, "data": data})
        self._intervals.sort(key=lambda x: x["start"])

    def query_point(self, point):
        results = []
        for interval in self._intervals:
            if interval["start"] <= point <= interval["end"]:
                results.append(interval)
        return results

    def query_overlap(self, start, end):
        if start > end:
            raise ValueError("start must be <= end")
        results = []
        for interval in self._intervals:
            if interval["start"] <= end and interval["end"] >= start:
                results.append(interval)
        return results

    def remove(self, start, end):
        original_count = len(self._intervals)
        self._intervals = [
            iv for iv in self._intervals
            if not (iv["start"] == start and iv["end"] == end)
        ]
        return len(self._intervals) < original_count

    def all_intervals(self):
        return list(self._intervals)

    def count(self):
        return len(self._intervals)

    def span(self):
        if not self._intervals:
            return None
        min_start = self._intervals[0]["start"]
        max_end = self._intervals[0]["end"]
        for iv in self._intervals:
            if iv["start"] < min_start:
                min_start = iv["start"]
            if iv["end"] > max_end:
                max_end = iv["end"]
        return (min_start, max_end)
""",
        "category": "class",
    },

    "class_025_disjoint_set": {
        "source": """\
class DisjointSet:
    # Union-Find data structure with path compression and union by rank
    def __init__(self):
        self._parent = {}
        self._rank = {}
        self._set_count = 0

    def make_set(self, x):
        if x in self._parent:
            return
        self._parent[x] = x
        self._rank[x] = 0
        self._set_count += 1

    def find(self, x):
        if x not in self._parent:
            raise KeyError("element not in any set: " + str(x))
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        current = x
        while current != root:
            next_parent = self._parent[current]
            self._parent[current] = root
            current = next_parent
        return root

    def union(self, x, y):
        root_x = self.find(x)
        root_y = self.find(y)
        if root_x == root_y:
            return False
        if self._rank[root_x] < self._rank[root_y]:
            self._parent[root_x] = root_y
        elif self._rank[root_x] > self._rank[root_y]:
            self._parent[root_y] = root_x
        else:
            self._parent[root_y] = root_x
            self._rank[root_x] += 1
        self._set_count -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)

    def num_sets(self):
        return self._set_count

    def elements(self):
        return list(self._parent.keys())
""",
        "category": "class",
    },

    "deps_001_data_pipeline": {
        "source": """\
def sanitize_field(raw):
    # Strip whitespace and normalize empty strings to None
    if not isinstance(raw, str):
        return raw
    cleaned = raw.strip()
    if len(cleaned) == 0:
        return None
    return cleaned


def parse_record(raw_line, delimiter=","):
    # Parse a raw text line into a list of sanitized fields
    fields = raw_line.split(delimiter)
    sanitized = []
    for field in fields:
        sanitized.append(sanitize_field(field))
    return sanitized


def validate_record(fields, required_count):
    # Validate a parsed record has the right field count and no None values
    errors = []
    if len(fields) != required_count:
        errors.append("expected %d fields, got %d" % (required_count, len(fields)))
    for i, field in enumerate(fields):
        if field is None:
            errors.append("missing value at index %d" % i)
    return {"valid": len(errors) == 0, "errors": errors}


def transform_record(fields, transformations):
    # Apply a list of transformation functions to corresponding fields
    result = []
    for i, field in enumerate(fields):
        if i < len(transformations) and transformations[i] is not None:
            result.append(transformations[i](field))
        else:
            result.append(field)
    return result


def process_pipeline(raw_lines, delimiter, required_count, transformations):
    # Run the full pipeline: parse, validate, transform
    output = []
    error_lines = []
    for line_num, line in enumerate(raw_lines):
        fields = parse_record(line, delimiter)
        validation = validate_record(fields, required_count)
        if not validation["valid"]:
            error_lines.append({"line": line_num, "errors": validation["errors"]})
            continue
        transformed = transform_record(fields, transformations)
        output.append(transformed)
    return {"records": output, "errors": error_lines}
""",
        "category": "module_with_deps",
    },

    "deps_002_auth_flow": {
        "source": """\
def hash_password(password, salt="default_salt"):
    # Simple hash for demonstration: sum of char codes mixed with salt
    h = 0
    combined = password + salt
    for i, ch in enumerate(combined):
        h = (h * 31 + ord(ch) + i) % (2 ** 32)
    return h


def create_user(username, password, user_store):
    # Create a new user with hashed password
    if username in user_store:
        return {"success": False, "error": "user already exists"}
    if len(password) < 6:
        return {"success": False, "error": "password too short"}
    hashed = hash_password(password)
    user_store[username] = {"password_hash": hashed, "active": True}
    return {"success": True, "username": username}


def authenticate(username, password, user_store):
    # Authenticate a user by checking password hash
    if username not in user_store:
        return {"authenticated": False, "reason": "unknown user"}
    user = user_store[username]
    if not user["active"]:
        return {"authenticated": False, "reason": "account disabled"}
    hashed = hash_password(password)
    if hashed != user["password_hash"]:
        return {"authenticated": False, "reason": "wrong password"}
    return {"authenticated": True, "username": username}


def create_session(auth_result, session_store, session_counter):
    # Create a session token for an authenticated user
    if not auth_result["authenticated"]:
        return None
    session_id = "sess_%d_%s" % (session_counter, auth_result["username"])
    session_store[session_id] = {
        "username": auth_result["username"],
        "active": True,
    }
    return session_id


def logout(session_id, session_store):
    # Invalidate a session
    if session_id not in session_store:
        return False
    session_store[session_id]["active"] = False
    return True
""",
        "category": "module_with_deps",
    },

    "deps_003_order_processing": {
        "source": """\
def validate_order_item(item):
    # Validate a single order item has required fields and valid values
    errors = []
    if "name" not in item or not item["name"]:
        errors.append("missing item name")
    if "price" not in item or item.get("price", 0) < 0:
        errors.append("invalid price")
    if "quantity" not in item or item.get("quantity", 0) < 1:
        errors.append("invalid quantity")
    return errors


def calculate_line_total(item):
    # Calculate the total for a single order line item
    price = item.get("price", 0)
    quantity = item.get("quantity", 0)
    discount = item.get("discount", 0)
    subtotal = price * quantity
    discount_amount = subtotal * discount
    return round(subtotal - discount_amount, 2)


def calculate_order_total(items, tax_rate=0.0):
    # Calculate the total for all items including tax
    subtotal = 0.0
    for item in items:
        subtotal += calculate_line_total(item)
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    return {"subtotal": subtotal, "tax": tax, "total": total}


def process_order(items, tax_rate=0.0):
    # Validate and process a complete order
    all_errors = []
    valid_items = []
    for i, item in enumerate(items):
        item_errors = validate_order_item(item)
        if item_errors:
            all_errors.append({"index": i, "errors": item_errors})
        else:
            valid_items.append(item)
    if all_errors:
        return {"status": "error", "errors": all_errors}
    totals = calculate_order_total(valid_items, tax_rate)
    return {"status": "ok", "items": len(valid_items), "totals": totals}
""",
        "category": "module_with_deps",
    },

    "deps_004_report_generator": {
        "source": """\
def aggregate_data(records, group_key, value_key):
    # Group records by a key and sum the values
    groups = {}
    for record in records:
        key = record.get(group_key, "unknown")
        value = record.get(value_key, 0)
        if key not in groups:
            groups[key] = {"sum": 0, "count": 0, "values": []}
        groups[key]["sum"] += value
        groups[key]["count"] += 1
        groups[key]["values"].append(value)
    return groups


def compute_summary(aggregated):
    # Compute summary statistics for each group
    summary = {}
    for key, data in aggregated.items():
        avg = data["sum"] / data["count"] if data["count"] > 0 else 0
        sorted_vals = sorted(data["values"])
        n = len(sorted_vals)
        median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        summary[key] = {
            "sum": data["sum"],
            "count": data["count"],
            "average": round(avg, 2),
            "median": median,
            "min": sorted_vals[0] if sorted_vals else 0,
            "max": sorted_vals[-1] if sorted_vals else 0,
        }
    return summary


def format_report(summary, title):
    # Format the summary into a report structure
    rows = []
    for key in sorted(summary.keys()):
        row = {"group": key}
        row.update(summary[key])
        rows.append(row)
    return {"title": title, "rows": rows, "group_count": len(rows)}


def generate_report(records, group_key, value_key, title):
    # Full pipeline: aggregate, summarize, format
    aggregated = aggregate_data(records, group_key, value_key)
    summary = compute_summary(aggregated)
    report = format_report(summary, title)
    return report
""",
        "category": "module_with_deps",
    },

    "deps_005_etl_pipeline": {
        "source": """\
def extract_fields(raw_record, field_map):
    # Extract and rename fields from a raw record
    extracted = {}
    for source_key, target_key in field_map.items():
        if source_key in raw_record:
            extracted[target_key] = raw_record[source_key]
        else:
            extracted[target_key] = None
    return extracted


def transform_types(record, type_map):
    # Cast record fields to specified types
    transformed = {}
    for key, value in record.items():
        if key in type_map and value is not None:
            try:
                transformed[key] = type_map[key](value)
            except (ValueError, TypeError):
                transformed[key] = None
        else:
            transformed[key] = value
    return transformed


def filter_records(records, predicate):
    # Filter records using a predicate function
    passed = []
    rejected = []
    for record in records:
        if predicate(record):
            passed.append(record)
        else:
            rejected.append(record)
    return {"passed": passed, "rejected": rejected}


def load_batch(records, batch_size=100):
    # Split records into batches for loading
    batches = []
    current_batch = []
    for record in records:
        current_batch.append(record)
        if len(current_batch) >= batch_size:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)
    return batches


def run_etl(raw_records, field_map, type_map, predicate, batch_size=100):
    # Run the full ETL pipeline
    extracted = [extract_fields(r, field_map) for r in raw_records]
    transformed = [transform_types(r, type_map) for r in extracted]
    filtered = filter_records(transformed, predicate)
    batches = load_batch(filtered["passed"], batch_size)
    return {"batches": batches, "rejected": len(filtered["rejected"])}
""",
        "category": "module_with_deps",
    },

    "deps_006_search_engine": {
        "source": """\
def tokenize(text):
    # Split text into lowercase tokens removing punctuation
    tokens = []
    current = []
    for ch in text.lower():
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return tokens


def build_index(documents):
    # Build an inverted index from a list of (doc_id, text) pairs
    index = {}
    for doc_id, text in documents:
        tokens = tokenize(text)
        seen = set()
        for position, token in enumerate(tokens):
            if token not in index:
                index[token] = {}
            if doc_id not in index[token]:
                index[token][doc_id] = []
            index[token][doc_id].append(position)
            seen.add(token)
    return index


def search_query(index, query_text):
    # Search the index for documents matching query tokens
    tokens = tokenize(query_text)
    if not tokens:
        return []
    doc_scores = {}
    for token in tokens:
        if token in index:
            for doc_id, positions in index[token].items():
                doc_scores[doc_id] = doc_scores.get(doc_id, 0) + len(positions)
    return doc_scores


def rank_results(doc_scores, limit=10):
    # Rank search results by score in descending order
    ranked = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for doc_id, score in ranked[:limit]:
        results.append({"doc_id": doc_id, "score": score})
    return results


def search(documents, query_text, limit=10):
    # Full search pipeline: index, query, rank
    index = build_index(documents)
    scores = search_query(index, query_text)
    return rank_results(scores, limit)
""",
        "category": "module_with_deps",
    },

    "deps_007_notification": {
        "source": """\
def create_notification(recipient, message, priority="normal"):
    # Create a notification record
    valid_priorities = ("low", "normal", "high", "urgent")
    if priority not in valid_priorities:
        raise ValueError("invalid priority: " + priority)
    return {
        "recipient": recipient,
        "message": message,
        "priority": priority,
        "status": "pending",
        "attempts": 0,
    }


def filter_by_priority(notifications, min_priority):
    # Filter notifications by minimum priority level
    levels = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
    threshold = levels.get(min_priority, 0)
    filtered = []
    for notif in notifications:
        if levels.get(notif["priority"], 0) >= threshold:
            filtered.append(notif)
    return filtered


def batch_notifications(notifications, batch_size):
    # Group notifications into batches for sending
    batches = []
    current = []
    for notif in notifications:
        current.append(notif)
        if len(current) >= batch_size:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def send_batch(batch, sender_fn):
    # Send a batch of notifications using the provided sender function
    results = []
    for notif in batch:
        notif["attempts"] += 1
        success = sender_fn(notif)
        if success:
            notif["status"] = "sent"
        else:
            notif["status"] = "failed"
        results.append({"recipient": notif["recipient"], "status": notif["status"]})
    return results


def process_notifications(notifications, min_priority, batch_size, sender_fn):
    # Full pipeline: filter, batch, send
    filtered = filter_by_priority(notifications, min_priority)
    batches = batch_notifications(filtered, batch_size)
    all_results = []
    for batch in batches:
        all_results.extend(send_batch(batch, sender_fn))
    return all_results
""",
        "category": "module_with_deps",
    },

    "deps_008_cache_eviction": {
        "source": """\
def create_cache(max_size):
    # Create a cache data structure with max size
    return {"data": {}, "order": [], "max_size": max_size, "hits": 0, "misses": 0}


def cache_get(cache, key):
    # Get a value from cache, updating access order
    if key in cache["data"]:
        cache["hits"] += 1
        cache["order"].remove(key)
        cache["order"].append(key)
        return cache["data"][key]
    cache["misses"] += 1
    return None


def cache_put(cache, key, value):
    # Put a value in cache, evicting oldest if necessary
    if key in cache["data"]:
        cache["order"].remove(key)
    elif len(cache["data"]) >= cache["max_size"]:
        evict_oldest(cache)
    cache["data"][key] = value
    cache["order"].append(key)


def evict_oldest(cache):
    # Evict the least recently used entry
    if not cache["order"]:
        return None
    oldest_key = cache["order"].pop(0)
    evicted_value = cache["data"].pop(oldest_key, None)
    return (oldest_key, evicted_value)


def cache_stats(cache):
    # Return cache statistics
    total = cache["hits"] + cache["misses"]
    hit_rate = cache["hits"] / total if total > 0 else 0.0
    return {
        "size": len(cache["data"]),
        "max_size": cache["max_size"],
        "hits": cache["hits"],
        "misses": cache["misses"],
        "hit_rate": round(hit_rate, 4),
    }


def cached_lookup(cache, key, loader_fn):
    # Look up a value in cache; on miss, load it using loader_fn
    value = cache_get(cache, key)
    if value is not None:
        return value
    value = loader_fn(key)
    cache_put(cache, key, value)
    return value
""",
        "category": "module_with_deps",
    },

    "deps_009_workflow_engine": {
        "source": """\
def create_workflow(name, steps):
    # Create a workflow with named steps
    return {
        "name": name,
        "steps": steps,
        "current_step": 0,
        "status": "pending",
        "results": [],
    }


def validate_workflow(workflow):
    # Validate workflow structure before execution
    errors = []
    if not workflow.get("name"):
        errors.append("workflow must have a name")
    steps = workflow.get("steps", [])
    if len(steps) == 0:
        errors.append("workflow must have at least one step")
    for i, step in enumerate(steps):
        if "action" not in step:
            errors.append("step %d missing action" % i)
    return {"valid": len(errors) == 0, "errors": errors}


def execute_step(workflow, context):
    # Execute the current step of the workflow
    idx = workflow["current_step"]
    if idx >= len(workflow["steps"]):
        workflow["status"] = "completed"
        return None
    step = workflow["steps"][idx]
    action = step["action"]
    result = action(context)
    workflow["results"].append({"step": idx, "result": result})
    workflow["current_step"] = idx + 1
    if workflow["current_step"] >= len(workflow["steps"]):
        workflow["status"] = "completed"
    else:
        workflow["status"] = "in_progress"
    return result


def run_workflow(workflow, context):
    # Run all steps of a workflow sequentially
    validation = validate_workflow(workflow)
    if not validation["valid"]:
        return {"status": "error", "errors": validation["errors"]}
    workflow["status"] = "in_progress"
    while workflow["status"] == "in_progress":
        execute_step(workflow, context)
    return {
        "status": workflow["status"],
        "results": workflow["results"],
        "steps_completed": len(workflow["results"]),
    }
""",
        "category": "module_with_deps",
    },

    "deps_010_data_migration": {
        "source": """\
def read_source_records(source_data, format_type="dict"):
    # Read records from source data in the given format
    if format_type == "dict":
        return list(source_data)
    elif format_type == "list":
        if not source_data:
            return []
        headers = source_data[0]
        records = []
        for row in source_data[1:]:
            record = {}
            for i, header in enumerate(headers):
                record[header] = row[i] if i < len(row) else None
            records.append(record)
        return records
    return []


def map_fields(record, field_mapping):
    # Map old field names to new field names
    mapped = {}
    for old_key, new_key in field_mapping.items():
        if old_key in record:
            mapped[new_key] = record[old_key]
    return mapped


def validate_migrated(record, required_fields):
    # Validate that a migrated record has all required fields
    missing = []
    for field in required_fields:
        if field not in record or record[field] is None:
            missing.append(field)
    return {"valid": len(missing) == 0, "missing": missing}


def write_destination(records, destination):
    # Write records to destination storage
    written = 0
    for record in records:
        destination.append(record)
        written += 1
    return written


def migrate(source_data, format_type, field_mapping, required_fields, destination):
    # Full migration pipeline: read, map, validate, write
    source_records = read_source_records(source_data, format_type)
    valid_records = []
    errors = []
    for i, record in enumerate(source_records):
        mapped = map_fields(record, field_mapping)
        validation = validate_migrated(mapped, required_fields)
        if validation["valid"]:
            valid_records.append(mapped)
        else:
            errors.append({"index": i, "missing": validation["missing"]})
    written = write_destination(valid_records, destination)
    return {"written": written, "errors": len(errors), "total": len(source_records)}
""",
        "category": "module_with_deps",
    },

    "deps_011_api_builder": {
        "source": """\
def build_url(base_url, path, query_params=None):
    # Build a complete URL from base, path, and query parameters
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    if query_params:
        pairs = []
        for key in sorted(query_params.keys()):
            pairs.append("%s=%s" % (key, query_params[key]))
        url = url + "?" + "&".join(pairs)
    return url


def build_headers(auth_token=None, content_type="application/json", extra=None):
    # Build request headers
    headers = {"Content-Type": content_type}
    if auth_token:
        headers["Authorization"] = "Bearer " + auth_token
    if extra:
        headers.update(extra)
    return headers


def build_request(method, base_url, path, query_params=None,
                  body=None, auth_token=None):
    # Build a complete API request structure
    url = build_url(base_url, path, query_params)
    headers = build_headers(auth_token)
    request = {
        "method": method.upper(),
        "url": url,
        "headers": headers,
        "body": body,
    }
    return validate_request(request)


def validate_request(request):
    # Validate that a request has all required fields
    valid_methods = ("GET", "POST", "PUT", "DELETE", "PATCH")
    errors = []
    if request.get("method") not in valid_methods:
        errors.append("invalid method: " + str(request.get("method")))
    if not request.get("url"):
        errors.append("missing url")
    request["valid"] = len(errors) == 0
    request["errors"] = errors
    return request
""",
        "category": "module_with_deps",
    },

    "deps_012_test_runner": {
        "source": """\
def discover_tests(test_registry):
    # Discover all test functions from a registry
    discovered = []
    for name, func in test_registry.items():
        if name.startswith("test_"):
            discovered.append({"name": name, "func": func, "status": "pending"})
    return discovered


def run_single_test(test_entry):
    # Run a single test and capture the result
    name = test_entry["name"]
    func = test_entry["func"]
    try:
        func()
        test_entry["status"] = "passed"
        test_entry["error"] = None
    except AssertionError as e:
        test_entry["status"] = "failed"
        test_entry["error"] = str(e) if str(e) else "assertion failed"
    except Exception as e:
        test_entry["status"] = "error"
        test_entry["error"] = str(e)
    return test_entry


def run_all_tests(test_entries):
    # Run all discovered tests and collect results
    results = []
    for entry in test_entries:
        result = run_single_test(entry)
        results.append(result)
    return results


def summarize_results(results):
    # Summarize test results into counts and status
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    errors = sum(1 for r in results if r["status"] == "error")
    total = len(results)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "success_rate": round(passed / total, 4) if total > 0 else 0.0,
    }


def run_test_suite(test_registry):
    # Full pipeline: discover, run, summarize
    tests = discover_tests(test_registry)
    results = run_all_tests(tests)
    summary = summarize_results(results)
    return {"summary": summary, "results": results}
""",
        "category": "module_with_deps",
    },

    "deps_013_schema_validator": {
        "source": """\
def validate_type(value, expected_type):
    # Validate that a value matches the expected type name
    type_checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)),
        "boolean": lambda v: isinstance(v, bool),
        "list": lambda v: isinstance(v, list),
        "dict": lambda v: isinstance(v, dict),
    }
    checker = type_checks.get(expected_type)
    if checker is None:
        return False
    return checker(value)


def validate_field(value, field_schema):
    # Validate a single field against its schema
    errors = []
    if "type" in field_schema:
        if not validate_type(value, field_schema["type"]):
            errors.append("expected type %s" % field_schema["type"])
    if "min" in field_schema and isinstance(value, (int, float)):
        if value < field_schema["min"]:
            errors.append("value below minimum %s" % field_schema["min"])
    if "max" in field_schema and isinstance(value, (int, float)):
        if value > field_schema["max"]:
            errors.append("value above maximum %s" % field_schema["max"])
    if "choices" in field_schema:
        if value not in field_schema["choices"]:
            errors.append("value not in allowed choices")
    return errors


def validate_record(record, schema):
    # Validate a complete record against a schema
    all_errors = {}
    for field_name, field_schema in schema.items():
        required = field_schema.get("required", False)
        if field_name not in record:
            if required:
                all_errors[field_name] = ["required field missing"]
            continue
        field_errors = validate_field(record[field_name], field_schema)
        if field_errors:
            all_errors[field_name] = field_errors
    return {"valid": len(all_errors) == 0, "errors": all_errors}


def validate_batch(records, schema):
    # Validate a batch of records and return summary
    valid_count = 0
    invalid_records = []
    for i, record in enumerate(records):
        result = validate_record(record, schema)
        if result["valid"]:
            valid_count += 1
        else:
            invalid_records.append({"index": i, "errors": result["errors"]})
    return {"valid": valid_count, "invalid": len(invalid_records), "details": invalid_records}
""",
        "category": "module_with_deps",
    },

    "deps_014_metric_collector": {
        "source": """\
def create_metric(name, metric_type="counter"):
    # Create a new metric record
    return {
        "name": name,
        "type": metric_type,
        "values": [],
        "tags": {},
    }


def record_value(metric, value, timestamp=0):
    # Record a new value for a metric
    metric["values"].append({"value": value, "timestamp": timestamp})


def add_tags(metric, tags):
    # Add tags to a metric for categorization
    for key, value in tags.items():
        metric["tags"][key] = value


def aggregate_metric(metric):
    # Compute aggregations for a metric
    values = [v["value"] for v in metric["values"]]
    if not values:
        return {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0}
    total = sum(values)
    count = len(values)
    avg = total / count
    return {
        "count": count,
        "sum": total,
        "avg": round(avg, 4),
        "min": min(values),
        "max": max(values),
    }


def collect_metrics(metric_definitions, data_points):
    # Create metrics, record data points, and aggregate
    metrics = {}
    for defn in metric_definitions:
        name = defn["name"]
        m = create_metric(name, defn.get("type", "counter"))
        if "tags" in defn:
            add_tags(m, defn["tags"])
        metrics[name] = m
    for point in data_points:
        name = point["metric"]
        if name in metrics:
            record_value(metrics[name], point["value"], point.get("timestamp", 0))
    results = {}
    for name, metric in metrics.items():
        results[name] = {
            "tags": metric["tags"],
            "aggregation": aggregate_metric(metric),
        }
    return results
""",
        "category": "module_with_deps",
    },

    "deps_015_log_aggregator": {
        "source": """\
def parse_log_entry(raw_line):
    # Parse a raw log line into structured fields
    parts = raw_line.split(" ", 3)
    entry = {"raw": raw_line, "level": "INFO", "source": "", "message": ""}
    if len(parts) >= 1:
        entry["timestamp"] = parts[0]
    if len(parts) >= 2:
        entry["level"] = parts[1].strip("[]").upper()
    if len(parts) >= 3:
        entry["source"] = parts[2].strip(":")
    if len(parts) >= 4:
        entry["message"] = parts[3]
    return entry


def group_by_source(entries):
    # Group parsed log entries by their source
    groups = {}
    for entry in entries:
        source = entry.get("source", "unknown")
        if source not in groups:
            groups[source] = []
        groups[source].append(entry)
    return groups


def count_by_level(entries):
    # Count log entries by level
    counts = {}
    for entry in entries:
        level = entry.get("level", "UNKNOWN")
        counts[level] = counts.get(level, 0) + 1
    return counts


def filter_errors(entries):
    # Extract only ERROR and CRITICAL entries
    error_levels = {"ERROR", "CRITICAL", "FATAL"}
    return [e for e in entries if e.get("level") in error_levels]


def aggregate_logs(raw_lines):
    # Full pipeline: parse all lines, group, count, filter errors
    entries = [parse_log_entry(line) for line in raw_lines]
    grouped = group_by_source(entries)
    level_counts = count_by_level(entries)
    errors = filter_errors(entries)
    return {
        "total_entries": len(entries),
        "sources": list(grouped.keys()),
        "level_counts": level_counts,
        "error_count": len(errors),
        "errors": errors,
    }
""",
        "category": "module_with_deps",
    },

    "deps_016_config_loader": {
        "source": """\
def load_defaults():
    # Return default configuration values
    return {
        "debug": False,
        "log_level": "INFO",
        "max_retries": 3,
        "timeout": 30,
        "host": "localhost",
        "port": 8080,
    }


def load_from_dict(config_dict, defaults):
    # Override defaults with values from a config dictionary
    merged = dict(defaults)
    for key, value in config_dict.items():
        if value is not None:
            merged[key] = value
    return merged


def validate_config(config):
    # Validate configuration values
    errors = []
    if not isinstance(config.get("port"), int):
        errors.append("port must be an integer")
    elif config["port"] < 1 or config["port"] > 65535:
        errors.append("port must be between 1 and 65535")
    if config.get("max_retries", 0) < 0:
        errors.append("max_retries must be non-negative")
    if config.get("timeout", 0) <= 0:
        errors.append("timeout must be positive")
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
    if config.get("log_level") not in valid_levels:
        errors.append("invalid log_level")
    return {"valid": len(errors) == 0, "errors": errors}


def resolve_config(overrides=None):
    # Full config chain: defaults -> overrides -> validate
    defaults = load_defaults()
    if overrides:
        config = load_from_dict(overrides, defaults)
    else:
        config = defaults
    validation = validate_config(config)
    if not validation["valid"]:
        return {"config": config, "errors": validation["errors"], "ok": False}
    return {"config": config, "errors": [], "ok": True}
""",
        "category": "module_with_deps",
    },

    "deps_017_task_scheduler": {
        "source": """\
def create_task(task_id, priority, action):
    # Create a schedulable task
    return {
        "id": task_id,
        "priority": priority,
        "action": action,
        "status": "pending",
        "result": None,
    }


def sort_by_priority(tasks):
    # Sort tasks by priority (lower number = higher priority)
    return sorted(tasks, key=lambda t: t["priority"])


def check_dependencies(task, completed_ids):
    # Check if task dependencies are all satisfied
    deps = task.get("depends_on", [])
    for dep_id in deps:
        if dep_id not in completed_ids:
            return False
    return True


def execute_task(task):
    # Execute a single task and update its status
    try:
        result = task["action"]()
        task["status"] = "completed"
        task["result"] = result
    except Exception as e:
        task["status"] = "failed"
        task["result"] = str(e)
    return task


def run_scheduler(tasks):
    # Schedule and run all tasks respecting priority and dependencies
    sorted_tasks = sort_by_priority(tasks)
    completed_ids = set()
    results = []
    remaining = list(sorted_tasks)
    max_iterations = len(remaining) * 2
    iteration = 0
    while remaining and iteration < max_iterations:
        iteration += 1
        progress = False
        next_remaining = []
        for task in remaining:
            if check_dependencies(task, completed_ids):
                execute_task(task)
                completed_ids.add(task["id"])
                results.append(task)
                progress = True
            else:
                next_remaining.append(task)
        remaining = next_remaining
        if not progress:
            break
    for task in remaining:
        task["status"] = "blocked"
        results.append(task)
    return results
""",
        "category": "module_with_deps",
    },

    "deps_018_form_validator": {
        "source": """\
def sanitize_input(value):
    # Sanitize a form input value
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    dangerous = ["<script", "javascript:", "onclick"]
    lower = cleaned.lower()
    for pattern in dangerous:
        if pattern in lower:
            cleaned = ""
            break
    return cleaned


def validate_required(value, field_name):
    # Validate that a required field is not empty
    if value is None or (isinstance(value, str) and len(value.strip()) == 0):
        return {"valid": False, "error": "%s is required" % field_name}
    return {"valid": True, "error": None}


def validate_length(value, field_name, min_len=0, max_len=1000):
    # Validate string length constraints
    if not isinstance(value, str):
        return {"valid": True, "error": None}
    if len(value) < min_len:
        return {"valid": False, "error": "%s too short (min %d)" % (field_name, min_len)}
    if len(value) > max_len:
        return {"valid": False, "error": "%s too long (max %d)" % (field_name, max_len)}
    return {"valid": True, "error": None}


def validate_field(value, field_name, rules):
    # Validate a single field against a set of rules
    sanitized = sanitize_input(value)
    errors = []
    if rules.get("required"):
        result = validate_required(sanitized, field_name)
        if not result["valid"]:
            errors.append(result["error"])
    if "min_length" in rules or "max_length" in rules:
        result = validate_length(
            sanitized, field_name,
            rules.get("min_length", 0),
            rules.get("max_length", 1000)
        )
        if not result["valid"]:
            errors.append(result["error"])
    return {"field": field_name, "value": sanitized, "errors": errors}


def validate_form(form_data, schema):
    # Validate an entire form against a schema
    results = {}
    all_valid = True
    for field_name, rules in schema.items():
        value = form_data.get(field_name, "")
        field_result = validate_field(value, field_name, rules)
        results[field_name] = field_result
        if field_result["errors"]:
            all_valid = False
    return {"valid": all_valid, "fields": results}
""",
        "category": "module_with_deps",
    },

    "deps_019_file_processor": {
        "source": """\
def read_lines(content):
    # Split content into lines and strip trailing whitespace
    lines = content.split("\\n")
    result = []
    for line in lines:
        result.append(line.rstrip())
    return result


def filter_lines(lines, predicate):
    # Filter lines using a predicate function
    filtered = []
    for line in lines:
        if predicate(line):
            filtered.append(line)
    return filtered


def transform_lines(lines, transformer):
    # Apply a transformation function to each line
    transformed = []
    for line in lines:
        transformed.append(transformer(line))
    return transformed


def number_lines(lines, start=1):
    # Add line numbers to each line
    numbered = []
    for i, line in enumerate(lines, start):
        numbered.append("%d: %s" % (i, line))
    return numbered


def join_output(lines, separator="\\n"):
    # Join processed lines back into a single string
    return separator.join(lines)


def process_file(content, filters=None, transformers=None, add_numbers=False):
    # Full pipeline: read, filter, transform, number, join
    lines = read_lines(content)
    if filters:
        for pred in filters:
            lines = filter_lines(lines, pred)
    if transformers:
        for trans in transformers:
            lines = transform_lines(lines, trans)
    if add_numbers:
        lines = number_lines(lines)
    return join_output(lines)
""",
        "category": "module_with_deps",
    },

    "deps_020_serializer_chain": {
        "source": """\
def serialize_to_pairs(data, prefix=""):
    # Serialize a nested dict into flat key=value pairs
    pairs = []
    for key, value in data.items():
        full_key = prefix + "." + key if prefix else key
        if isinstance(value, dict):
            pairs.extend(serialize_to_pairs(value, full_key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                item_key = "%s[%d]" % (full_key, i)
                if isinstance(item, dict):
                    pairs.extend(serialize_to_pairs(item, item_key))
                else:
                    pairs.append((item_key, str(item)))
        else:
            pairs.append((full_key, str(value)))
    return pairs


def pairs_to_string(pairs, separator="&", assignment="="):
    # Convert key-value pairs to a query-string-like format
    parts = []
    for key, value in pairs:
        parts.append("%s%s%s" % (key, assignment, value))
    return separator.join(parts)


def deserialize_pairs(encoded, separator="&", assignment="="):
    # Parse a serialized string back into key-value pairs
    pairs = []
    parts = encoded.split(separator)
    for part in parts:
        if assignment in part:
            key, value = part.split(assignment, 1)
            pairs.append((key.strip(), value.strip()))
    return pairs


def round_trip(data, separator="&", assignment="="):
    # Serialize and deserialize, returning both forms
    pairs = serialize_to_pairs(data)
    encoded = pairs_to_string(pairs, separator, assignment)
    decoded = deserialize_pairs(encoded, separator, assignment)
    return {"pairs": pairs, "encoded": encoded, "decoded": decoded}
""",
        "category": "module_with_deps",
    },

    "deps_021_message_queue": {
        "source": """\
def create_queue(name, max_size=1000):
    # Create a message queue
    return {"name": name, "messages": [], "max_size": max_size,
            "processed": 0, "dead_letters": []}


def enqueue_message(queue, message, priority=0):
    # Add a message to the queue
    if len(queue["messages"]) >= queue["max_size"]:
        raise OverflowError("queue is full")
    entry = {"payload": message, "priority": priority, "retries": 0}
    queue["messages"].append(entry)
    queue["messages"].sort(key=lambda m: m["priority"])


def dequeue_message(queue):
    # Remove and return the highest priority message
    if not queue["messages"]:
        return None
    return queue["messages"].pop(0)


def process_message(message, handler):
    # Process a single message using a handler function
    try:
        result = handler(message["payload"])
        return {"success": True, "result": result}
    except Exception as e:
        message["retries"] += 1
        return {"success": False, "error": str(e), "retries": message["retries"]}


def process_queue(queue, handler, max_retries=3):
    # Process all messages in the queue
    results = []
    retry_messages = []
    while queue["messages"]:
        message = dequeue_message(queue)
        result = process_message(message, handler)
        if result["success"]:
            queue["processed"] += 1
            results.append(result)
        elif message["retries"] < max_retries:
            retry_messages.append(message)
        else:
            queue["dead_letters"].append(message)
    for msg in retry_messages:
        queue["messages"].append(msg)
    return {"processed": len(results), "retried": len(retry_messages),
            "dead": len(queue["dead_letters"])}
""",
        "category": "module_with_deps",
    },

    "deps_022_http_chain": {
        "source": """\
def build_request_url(base, endpoint, params=None):
    # Build request URL from base, endpoint, and optional params
    url = base.rstrip("/") + "/" + endpoint.lstrip("/")
    if params:
        query_parts = []
        for key in sorted(params.keys()):
            query_parts.append("%s=%s" % (key, params[key]))
        url += "?" + "&".join(query_parts)
    return url


def add_auth_header(headers, token):
    # Add authentication header to request headers
    updated = dict(headers)
    updated["Authorization"] = "Bearer " + token
    return updated


def apply_middleware(request, middleware_list):
    # Apply a chain of middleware functions to a request
    current = dict(request)
    for middleware in middleware_list:
        current = middleware(current)
        if current is None:
            return None
    return current


def validate_response(response):
    # Validate an HTTP response structure
    status = response.get("status", 0)
    is_success = 200 <= status < 300
    is_redirect = 300 <= status < 400
    is_error = status >= 400
    return {
        "status": status,
        "is_success": is_success,
        "is_redirect": is_redirect,
        "is_error": is_error,
        "body": response.get("body"),
    }


def execute_request_chain(base, endpoint, params, token, middleware_list):
    # Build and execute a full request through the middleware chain
    url = build_request_url(base, endpoint, params)
    headers = add_auth_header({"Content-Type": "application/json"}, token)
    request = {"url": url, "headers": headers, "method": "GET"}
    processed = apply_middleware(request, middleware_list)
    if processed is None:
        return {"error": "request blocked by middleware"}
    return processed
""",
        "category": "module_with_deps",
    },

    "deps_023_template_renderer": {
        "source": """\
def find_placeholders(template):
    # Find all {{placeholder}} patterns in a template string
    placeholders = []
    i = 0
    while i < len(template) - 1:
        if template[i] == "{" and template[i + 1] == "{":
            j = i + 2
            while j < len(template) - 1:
                if template[j] == "}" and template[j + 1] == "}":
                    name = template[i + 2:j].strip()
                    placeholders.append({"name": name, "start": i, "end": j + 2})
                    i = j + 2
                    break
                j += 1
            else:
                i += 1
        else:
            i += 1
    return placeholders


def resolve_value(context, key):
    # Resolve a dotted key path against a context dictionary
    parts = key.split(".")
    current = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def apply_filters(value, filter_name):
    # Apply a named filter to a value
    if filter_name == "upper":
        return str(value).upper()
    elif filter_name == "lower":
        return str(value).lower()
    elif filter_name == "strip":
        return str(value).strip()
    elif filter_name == "default":
        return value if value is not None else ""
    return str(value)


def render_template(template, context, default_filter="default"):
    # Render a template by replacing placeholders with context values
    placeholders = find_placeholders(template)
    result = template
    offset = 0
    for ph in placeholders:
        name = ph["name"]
        filter_name = default_filter
        if "|" in name:
            parts = name.split("|")
            name = parts[0].strip()
            filter_name = parts[1].strip()
        value = resolve_value(context, name)
        rendered = apply_filters(value, filter_name)
        start = ph["start"] + offset
        end = ph["end"] + offset
        result = result[:start] + str(rendered) + result[end:]
        offset += len(str(rendered)) - (ph["end"] - ph["start"])
    return result
""",
        "category": "module_with_deps",
    },

    "deps_024_query_builder": {
        "source": """\
def select_fields(fields):
    # Build a SELECT clause from a list of field names
    if not fields:
        return "SELECT *"
    return "SELECT " + ", ".join(fields)


def from_table(table_name, alias=None):
    # Build a FROM clause
    if alias:
        return "FROM %s AS %s" % (table_name, alias)
    return "FROM %s" % table_name


def where_conditions(conditions):
    # Build a WHERE clause from a list of condition strings
    if not conditions:
        return ""
    return "WHERE " + " AND ".join(conditions)


def order_by_clause(fields, directions=None):
    # Build an ORDER BY clause
    if not fields:
        return ""
    parts = []
    for i, field in enumerate(fields):
        direction = "ASC"
        if directions and i < len(directions):
            direction = directions[i]
        parts.append("%s %s" % (field, direction))
    return "ORDER BY " + ", ".join(parts)


def limit_offset(limit=None, offset=None):
    # Build LIMIT and OFFSET clauses
    parts = []
    if limit is not None:
        parts.append("LIMIT %d" % limit)
    if offset is not None:
        parts.append("OFFSET %d" % offset)
    return " ".join(parts)


def build_query(table, fields=None, conditions=None,
                sort_fields=None, sort_dirs=None,
                limit=None, offset=None, alias=None):
    # Build a complete SQL query from components
    parts = [
        select_fields(fields),
        from_table(table, alias),
        where_conditions(conditions),
        order_by_clause(sort_fields, sort_dirs),
        limit_offset(limit, offset),
    ]
    query = " ".join(p for p in parts if p)
    return query
""",
        "category": "module_with_deps",
    },

    "deps_025_rule_engine": {
        "source": """\
def create_rule(name, condition, action, priority=0):
    # Create a rule with a condition function, action function, and priority
    return {
        "name": name,
        "condition": condition,
        "action": action,
        "priority": priority,
        "fired_count": 0,
    }


def evaluate_condition(rule, context):
    # Evaluate whether a rule's condition is met
    try:
        return rule["condition"](context)
    except Exception:
        return False


def execute_action(rule, context):
    # Execute a rule's action and track firing
    result = rule["action"](context)
    rule["fired_count"] += 1
    return result


def sort_rules_by_priority(rules):
    # Sort rules by priority (lower number = higher priority)
    return sorted(rules, key=lambda r: r["priority"])


def evaluate_rules(rules, context, stop_on_first=False):
    # Evaluate all rules against a context and collect results
    sorted_rules = sort_rules_by_priority(rules)
    results = []
    for rule in sorted_rules:
        if evaluate_condition(rule, context):
            result = execute_action(rule, context)
            results.append({"rule": rule["name"], "result": result})
            if stop_on_first:
                break
    return results


def run_rule_engine(rules, contexts, stop_on_first=False):
    # Run the rule engine over multiple contexts
    all_results = []
    for i, context in enumerate(contexts):
        context_results = evaluate_rules(rules, context, stop_on_first)
        all_results.append({
            "context_index": i,
            "rules_fired": len(context_results),
            "results": context_results,
        })
    return all_results
""",
        "category": "module_with_deps",
    },

    "multi_001_csv_parser": {
        "source": """\
def parse_csv_line(line, delimiter=","):
    # Parse a single CSV line into a list of fields
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        char = line[i]
        if in_quotes:
            if char == '"' and i + 1 < len(line) and line[i + 1] == '"':
                current.append('"')
                i += 2
                continue
            elif char == '"':
                in_quotes = False
            else:
                current.append(char)
        else:
            if char == '"':
                in_quotes = True
            elif char == delimiter:
                fields.append("".join(current))
                current = []
            else:
                current.append(char)
        i += 1
    fields.append("".join(current))
    return fields


def validate_csv_row(row, expected_columns):
    # Validate that a parsed CSV row has the correct number of columns
    errors = []
    if len(row) != expected_columns:
        errors.append("column count mismatch: got %d expected %d" % (len(row), expected_columns))
    for idx, field in enumerate(row):
        if field is None:
            errors.append("null field at index %d" % idx)
    return {"valid": len(errors) == 0, "errors": errors}
""",
        "category": "multi_function",
    },

    "multi_002_unit_converter": {
        "source": """\
def convert_length(value, from_unit, to_unit):
    # Convert between length units: m, km, mi, ft, in
    to_meters = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "ft": 0.3048, "in": 0.0254}
    if from_unit not in to_meters:
        raise ValueError("unknown from_unit: " + from_unit)
    if to_unit not in to_meters:
        raise ValueError("unknown to_unit: " + to_unit)
    meters = value * to_meters[from_unit]
    result = meters / to_meters[to_unit]
    return round(result, 6)


def convert_weight(value, from_unit, to_unit):
    # Convert between weight units: kg, g, lb, oz
    to_grams = {"kg": 1000.0, "g": 1.0, "lb": 453.592, "oz": 28.3495}
    if from_unit not in to_grams:
        raise ValueError("unknown from_unit: " + from_unit)
    if to_unit not in to_grams:
        raise ValueError("unknown to_unit: " + to_unit)
    grams = value * to_grams[from_unit]
    result = grams / to_grams[to_unit]
    return round(result, 6)


def convert_volume(value, from_unit, to_unit):
    # Convert between volume units: l, ml, gal, qt
    to_ml = {"l": 1000.0, "ml": 1.0, "gal": 3785.41, "qt": 946.353}
    if from_unit not in to_ml:
        raise ValueError("unknown from_unit: " + from_unit)
    if to_unit not in to_ml:
        raise ValueError("unknown to_unit: " + to_unit)
    ml = value * to_ml[from_unit]
    result = ml / to_ml[to_unit]
    return round(result, 6)
""",
        "category": "multi_function",
    },

    "multi_003_color_converter": {
        "source": """\
def hex_to_rgb(hex_str):
    # Convert a hex color string like '#FF8800' to an RGB tuple
    cleaned = hex_str.lstrip("#")
    if len(cleaned) != 6:
        raise ValueError("hex string must be 6 characters")
    r = int(cleaned[0:2], 16)
    g = int(cleaned[2:4], 16)
    b = int(cleaned[4:6], 16)
    return (r, g, b)


def rgb_to_hex(r, g, b):
    # Convert RGB values (0-255) to a hex color string
    for val in (r, g, b):
        if val < 0 or val > 255:
            raise ValueError("RGB values must be 0-255")
    return "#%02X%02X%02X" % (r, g, b)


def rgb_to_hsl(r, g, b):
    # Convert RGB (0-255) to HSL (h: 0-360, s: 0-1, l: 0-1)
    r_norm = r / 255.0
    g_norm = g / 255.0
    b_norm = b / 255.0
    c_max = max(r_norm, g_norm, b_norm)
    c_min = min(r_norm, g_norm, b_norm)
    delta = c_max - c_min
    lightness = (c_max + c_min) / 2.0
    if delta == 0:
        hue = 0.0
        saturation = 0.0
    else:
        if lightness < 0.5:
            saturation = delta / (c_max + c_min)
        else:
            saturation = delta / (2.0 - c_max - c_min)
        if c_max == r_norm:
            hue = 60.0 * (((g_norm - b_norm) / delta) % 6)
        elif c_max == g_norm:
            hue = 60.0 * (((b_norm - r_norm) / delta) + 2)
        else:
            hue = 60.0 * (((r_norm - g_norm) / delta) + 4)
    return (round(hue, 2), round(saturation, 4), round(lightness, 4))
""",
        "category": "multi_function",
    },

    "multi_004_url_parser": {
        "source": """\
def parse_url(url):
    # Parse a URL into its components: scheme, host, port, path, query
    result = {"scheme": "", "host": "", "port": None, "path": "", "query": ""}
    rest = url
    if "://" in rest:
        idx = rest.index("://")
        result["scheme"] = rest[:idx]
        rest = rest[idx + 3:]
    if "/" in rest:
        idx = rest.index("/")
        host_part = rest[:idx]
        rest = rest[idx:]
    else:
        host_part = rest
        rest = ""
    if ":" in host_part:
        parts = host_part.split(":")
        result["host"] = parts[0]
        result["port"] = int(parts[1])
    else:
        result["host"] = host_part
    if "?" in rest:
        idx = rest.index("?")
        result["path"] = rest[:idx]
        result["query"] = rest[idx + 1:]
    else:
        result["path"] = rest
    return result


def parse_query_string(query):
    # Parse a URL query string into a dictionary of key-value pairs
    params = {}
    if not query:
        return params
    pairs = query.split("&")
    for pair in pairs:
        if "=" in pair:
            key, value = pair.split("=", 1)
            params[key] = value
        else:
            params[pair] = ""
    return params
""",
        "category": "multi_function",
    },

    "multi_005_markdown_headings": {
        "source": """\
def extract_headings(markdown_text):
    # Extract all headings from markdown text with their levels
    headings = []
    lines = markdown_text.split("\\n")
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = 0
        for ch in stripped:
            if ch == "#":
                level += 1
            else:
                break
        if level > 6:
            continue
        text = stripped[level:].strip()
        if text:
            headings.append({"level": level, "text": text, "line": line_num})
    return headings


def build_table_of_contents(headings):
    # Build a nested table of contents from a list of headings
    toc = []
    for heading in headings:
        indent = "  " * (heading["level"] - 1)
        slug = heading["text"].lower().replace(" ", "-")
        cleaned_slug = ""
        for ch in slug:
            if ch.isalnum() or ch == "-":
                cleaned_slug += ch
        entry = {
            "text": heading["text"],
            "slug": cleaned_slug,
            "indent": indent,
            "level": heading["level"],
        }
        toc.append(entry)
    return toc
""",
        "category": "multi_function",
    },

    "multi_006_email_validator": {
        "source": """\
def validate_email_format(email):
    # Validate basic email format: local@domain
    if not isinstance(email, str):
        return {"valid": False, "reason": "not a string"}
    if email.count("@") != 1:
        return {"valid": False, "reason": "must contain exactly one @"}
    local, domain = email.split("@")
    if len(local) == 0:
        return {"valid": False, "reason": "empty local part"}
    if len(domain) == 0:
        return {"valid": False, "reason": "empty domain part"}
    if "." not in domain:
        return {"valid": False, "reason": "domain must contain a dot"}
    if domain.startswith(".") or domain.endswith("."):
        return {"valid": False, "reason": "domain cannot start or end with dot"}
    return {"valid": True, "local": local, "domain": domain}


def normalize_email(email):
    # Normalize an email address to lowercase with trimmed whitespace
    cleaned = email.strip().lower()
    parts = cleaned.split("@")
    if len(parts) != 2:
        return cleaned
    local = parts[0]
    domain = parts[1]
    if "+" in local:
        local = local[:local.index("+")]
    return local + "@" + domain


def extract_domain(email):
    # Extract the domain part from an email address
    if "@" not in email:
        return ""
    parts = email.split("@")
    domain = parts[-1].strip().lower()
    return domain
""",
        "category": "multi_function",
    },

    "multi_007_phone_formatter": {
        "source": """\
def extract_digits(phone_str):
    # Extract only digit characters from a phone string
    digits = []
    for ch in phone_str:
        if ch.isdigit():
            digits.append(ch)
    return "".join(digits)


def format_us_phone(phone_str):
    # Format a US phone number as (XXX) XXX-XXXX
    digits = extract_digits(phone_str)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return {"valid": False, "formatted": "", "digits": digits}
    area = digits[0:3]
    prefix = digits[3:6]
    line = digits[6:10]
    formatted = "(%s) %s-%s" % (area, prefix, line)
    return {"valid": True, "formatted": formatted, "digits": digits}


def format_international(phone_str, country_code):
    # Format a phone number with international country code prefix
    digits = extract_digits(phone_str)
    if not digits:
        return {"valid": False, "formatted": ""}
    if digits.startswith(country_code):
        digits = digits[len(country_code):]
    formatted = "+" + country_code + " " + digits
    return {"valid": True, "formatted": formatted}
""",
        "category": "multi_function",
    },

    "multi_008_ip_address": {
        "source": """\
def parse_ipv4(ip_str):
    # Parse and validate an IPv4 address string
    parts = ip_str.strip().split(".")
    if len(parts) != 4:
        return {"valid": False, "reason": "must have 4 octets"}
    octets = []
    for part in parts:
        if not part.isdigit():
            return {"valid": False, "reason": "non-numeric octet"}
        val = int(part)
        if val < 0 or val > 255:
            return {"valid": False, "reason": "octet out of range"}
        octets.append(val)
    return {"valid": True, "octets": octets}


def is_private_ip(ip_str):
    # Check if an IPv4 address is in a private range
    parsed = parse_ipv4(ip_str)
    if not parsed["valid"]:
        return False
    octets = parsed["octets"]
    if octets[0] == 10:
        return True
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return True
    if octets[0] == 192 and octets[1] == 168:
        return True
    return False


def ip_to_int(ip_str):
    # Convert an IPv4 address to its integer representation
    parsed = parse_ipv4(ip_str)
    if not parsed["valid"]:
        raise ValueError("invalid IP address")
    octets = parsed["octets"]
    result = 0
    for octet in octets:
        result = (result << 8) | octet
    return result
""",
        "category": "multi_function",
    },

    "multi_009_date_formatter": {
        "source": """\
def format_date_iso(year, month, day):
    # Format a date as ISO 8601: YYYY-MM-DD
    if month < 1 or month > 12:
        raise ValueError("month must be 1-12")
    if day < 1 or day > 31:
        raise ValueError("day must be 1-31")
    return "%04d-%02d-%02d" % (year, month, day)


def format_date_us(year, month, day):
    # Format a date as US style: MM/DD/YYYY
    if month < 1 or month > 12:
        raise ValueError("month must be 1-12")
    if day < 1 or day > 31:
        raise ValueError("day must be 1-31")
    return "%02d/%02d/%04d" % (month, day, year)


def format_date_long(year, month, day):
    # Format a date as long form: Month DD, YYYY
    month_names = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    if month < 1 or month > 12:
        raise ValueError("month must be 1-12")
    if day < 1 or day > 31:
        raise ValueError("day must be 1-31")
    return "%s %d, %04d" % (month_names[month], day, year)


def parse_iso_date(date_str):
    # Parse an ISO 8601 date string into year, month, day
    parts = date_str.strip().split("-")
    if len(parts) != 3:
        raise ValueError("invalid ISO date format")
    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])
    return {"year": year, "month": month, "day": day}
""",
        "category": "multi_function",
    },

    "multi_010_json_path": {
        "source": """\
def parse_json_path(path_str):
    # Parse a JSON path string like 'a.b[0].c' into tokens
    tokens = []
    current = []
    i = 0
    while i < len(path_str):
        ch = path_str[i]
        if ch == ".":
            if current:
                tokens.append({"type": "key", "value": "".join(current)})
                current = []
        elif ch == "[":
            if current:
                tokens.append({"type": "key", "value": "".join(current)})
                current = []
            j = i + 1
            while j < len(path_str) and path_str[j] != "]":
                j += 1
            index_str = path_str[i + 1:j]
            tokens.append({"type": "index", "value": int(index_str)})
            i = j
        else:
            current.append(ch)
        i += 1
    if current:
        tokens.append({"type": "key", "value": "".join(current)})
    return tokens


def extract_json_value(data, path_str):
    # Extract a value from nested dicts/lists using a JSON path
    tokens = parse_json_path(path_str)
    current = data
    for token in tokens:
        if token["type"] == "key":
            if not isinstance(current, dict):
                return None
            current = current.get(token["value"])
        elif token["type"] == "index":
            if not isinstance(current, list):
                return None
            idx = token["value"]
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        if current is None:
            return None
    return current
""",
        "category": "multi_function",
    },

    "multi_011_config_parser": {
        "source": """\
def parse_ini_section(text):
    # Parse an INI-style config section into key-value pairs
    result = {}
    current_section = None
    lines = text.strip().split("\\n")
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            if current_section not in result:
                result[current_section] = {}
        elif "=" in stripped and current_section is not None:
            key, value = stripped.split("=", 1)
            result[current_section][key.strip()] = value.strip()
    return result


def get_config_value(config, section, key, default=None):
    # Get a value from a parsed config, with optional default
    if section not in config:
        return default
    section_data = config[section]
    if key not in section_data:
        return default
    return section_data[key]


def merge_configs(base, override):
    # Merge two config dicts with override taking precedence
    merged = {}
    for section in base:
        merged[section] = dict(base[section])
    for section in override:
        if section not in merged:
            merged[section] = {}
        for key, value in override[section].items():
            merged[section][key] = value
    return merged
""",
        "category": "multi_function",
    },

    "multi_012_log_filter": {
        "source": """\
def parse_log_line(line):
    # Parse a log line into timestamp, level, and message
    parts = line.split(" ", 2)
    if len(parts) < 3:
        return {"timestamp": "", "level": "UNKNOWN", "message": line}
    timestamp = parts[0]
    level = parts[1].upper().strip("[]")
    message = parts[2]
    return {"timestamp": timestamp, "level": level, "message": message}


def filter_by_level(log_lines, min_level):
    # Filter log lines to only include entries at or above min_level
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    if min_level not in level_order:
        raise ValueError("unknown log level: " + min_level)
    threshold = level_order[min_level]
    filtered = []
    for line in log_lines:
        parsed = parse_log_line(line)
        line_level = parsed["level"]
        if line_level in level_order and level_order[line_level] >= threshold:
            filtered.append(parsed)
    return filtered


def count_by_level(log_lines):
    # Count log entries grouped by level
    counts = {}
    for line in log_lines:
        parsed = parse_log_line(line)
        level = parsed["level"]
        counts[level] = counts.get(level, 0) + 1
    return counts
""",
        "category": "multi_function",
    },

    "multi_013_math_tokenizer": {
        "source": """\
def tokenize_expression(expr):
    # Tokenize a math expression into numbers, operators, and parentheses
    tokens = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            start = i
            has_dot = ch == "."
            i += 1
            while i < len(expr) and (expr[i].isdigit() or (expr[i] == "." and not has_dot)):
                if expr[i] == ".":
                    has_dot = True
                i += 1
            tokens.append({"type": "number", "value": expr[start:i]})
            continue
        if ch in "+-*/^%":
            tokens.append({"type": "operator", "value": ch})
        elif ch == "(":
            tokens.append({"type": "lparen", "value": ch})
        elif ch == ")":
            tokens.append({"type": "rparen", "value": ch})
        else:
            tokens.append({"type": "unknown", "value": ch})
        i += 1
    return tokens


def validate_tokens(tokens):
    # Validate that tokenized expression has balanced parentheses and valid structure
    paren_depth = 0
    errors = []
    for idx, token in enumerate(tokens):
        if token["type"] == "lparen":
            paren_depth += 1
        elif token["type"] == "rparen":
            paren_depth -= 1
            if paren_depth < 0:
                errors.append("unmatched closing paren at token %d" % idx)
        elif token["type"] == "unknown":
            errors.append("unknown token at position %d: %s" % (idx, token["value"]))
    if paren_depth > 0:
        errors.append("unclosed parentheses: %d remaining" % paren_depth)
    return {"valid": len(errors) == 0, "errors": errors}
""",
        "category": "multi_function",
    },

    "multi_014_html_entities": {
        "source": """\
def encode_html_entities(text):
    # Encode special characters as HTML entities
    result = []
    entity_map = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
    }
    for ch in text:
        if ch in entity_map:
            result.append(entity_map[ch])
        else:
            result.append(ch)
    return "".join(result)


def decode_html_entities(text):
    # Decode HTML entities back to their original characters
    reverse_map = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&apos;": "'",
        "&#39;": "'",
    }
    result = text
    for entity, char in reverse_map.items():
        result = result.replace(entity, char)
    return result


def strip_html_tags(text):
    # Remove all HTML tags from a string
    result = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            result.append(ch)
    return "".join(result)
""",
        "category": "multi_function",
    },

    "multi_015_slug_generator": {
        "source": """\
def generate_slug(title):
    # Generate a URL-friendly slug from a title string
    lowered = title.lower().strip()
    slug_chars = []
    for ch in lowered:
        if ch.isalnum():
            slug_chars.append(ch)
        elif ch in " -_":
            slug_chars.append("-")
    raw_slug = "".join(slug_chars)
    while "--" in raw_slug:
        raw_slug = raw_slug.replace("--", "-")
    raw_slug = raw_slug.strip("-")
    return raw_slug


def truncate_slug(slug, max_length=50):
    # Truncate a slug to max_length without cutting words
    if len(slug) <= max_length:
        return slug
    truncated = slug[:max_length]
    last_dash = truncated.rfind("-")
    if last_dash > 0:
        truncated = truncated[:last_dash]
    return truncated


def ensure_unique_slug(slug, existing_slugs):
    # Ensure a slug is unique by appending a numeric suffix if needed
    if slug not in existing_slugs:
        return slug
    counter = 2
    while True:
        candidate = "%s-%d" % (slug, counter)
        if candidate not in existing_slugs:
            return candidate
        counter += 1
        if counter > 1000:
            raise RuntimeError("too many slug collisions")
""",
        "category": "multi_function",
    },

    "multi_016_pagination": {
        "source": """\
def compute_pagination(total_items, page_size, current_page):
    # Compute pagination metadata for a list of items
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    if current_page < 1:
        raise ValueError("current_page must be at least 1")
    total_pages = (total_items + page_size - 1) // page_size
    if total_pages == 0:
        total_pages = 1
    if current_page > total_pages:
        current_page = total_pages
    offset = (current_page - 1) * page_size
    has_prev = current_page > 1
    has_next = current_page < total_pages
    return {
        "total_items": total_items,
        "page_size": page_size,
        "current_page": current_page,
        "total_pages": total_pages,
        "offset": offset,
        "has_prev": has_prev,
        "has_next": has_next,
    }


def get_page_range(total_pages, current_page, window=2):
    # Return a list of page numbers to display around the current page
    start = max(1, current_page - window)
    end = min(total_pages, current_page + window)
    pages = list(range(start, end + 1))
    if start > 1:
        pages.insert(0, 1)
    if end < total_pages:
        pages.append(total_pages)
    return pages
""",
        "category": "multi_function",
    },

    "multi_017_version_compare": {
        "source": """\
def parse_version(version_str):
    # Parse a version string like '1.2.3' into a tuple of integers
    parts = version_str.strip().split(".")
    result = []
    for part in parts:
        cleaned = ""
        for ch in part:
            if ch.isdigit():
                cleaned += ch
            else:
                break
        if cleaned:
            result.append(int(cleaned))
        else:
            result.append(0)
    return tuple(result)


def compare_versions(version_a, version_b):
    # Compare two version strings; returns -1, 0, or 1
    parts_a = parse_version(version_a)
    parts_b = parse_version(version_b)
    max_len = max(len(parts_a), len(parts_b))
    for i in range(max_len):
        a = parts_a[i] if i < len(parts_a) else 0
        b = parts_b[i] if i < len(parts_b) else 0
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


def is_compatible(version, requirement):
    # Check if version satisfies a semver-compatible requirement like '>=1.2.0'
    if requirement.startswith(">="):
        req_ver = requirement[2:]
        return compare_versions(version, req_ver) >= 0
    elif requirement.startswith("<="):
        req_ver = requirement[2:]
        return compare_versions(version, req_ver) <= 0
    elif requirement.startswith("=="):
        req_ver = requirement[2:]
        return compare_versions(version, req_ver) == 0
    else:
        return compare_versions(version, requirement) == 0
""",
        "category": "multi_function",
    },

    "multi_018_checksum_suite": {
        "source": """\
def crc8(data):
    # Compute CRC-8 checksum for a list of byte values
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def adler32(data):
    # Compute Adler-32 checksum for a list of byte values
    mod_adler = 65521
    a = 1
    b = 0
    for byte in data:
        a = (a + byte) % mod_adler
        b = (b + a) % mod_adler
    return (b << 16) | a


def xor_checksum(data, block_size=4):
    # Compute XOR checksum over blocks of data
    if not data:
        return 0
    result = 0
    block = 0
    count = 0
    for byte in data:
        block = (block << 8) | byte
        count += 1
        if count == block_size:
            result ^= block
            block = 0
            count = 0
    if count > 0:
        result ^= block
    return result
""",
        "category": "multi_function",
    },

    "multi_019_coordinate_convert": {
        "source": """\
import math


def cartesian_to_polar(x, y):
    # Convert Cartesian coordinates to polar (r, theta in radians)
    r = math.sqrt(x * x + y * y)
    theta = math.atan2(y, x)
    return (round(r, 6), round(theta, 6))


def polar_to_cartesian(r, theta):
    # Convert polar coordinates to Cartesian
    x = r * math.cos(theta)
    y = r * math.sin(theta)
    return (round(x, 6), round(y, 6))


def degrees_to_radians(degrees):
    # Convert degrees to radians
    return degrees * math.pi / 180.0


def radians_to_degrees(radians):
    # Convert radians to degrees
    return radians * 180.0 / math.pi


def spherical_to_cartesian(r, theta, phi):
    # Convert spherical coordinates (r, theta, phi) to Cartesian (x, y, z)
    x = r * math.sin(phi) * math.cos(theta)
    y = r * math.sin(phi) * math.sin(theta)
    z = r * math.cos(phi)
    return (round(x, 6), round(y, 6), round(z, 6))
""",
        "category": "multi_function",
    },

    "multi_020_dice_roller": {
        "source": """\
import random as _random


def roll_dice(num_dice, num_sides, rng=None):
    # Roll num_dice dice each with num_sides sides
    if num_dice < 1:
        raise ValueError("num_dice must be at least 1")
    if num_sides < 2:
        raise ValueError("num_sides must be at least 2")
    gen = rng if rng is not None else _random
    rolls = []
    for _ in range(num_dice):
        rolls.append(gen.randint(1, num_sides))
    return rolls


def roll_statistics(rolls):
    # Compute statistics for a set of dice rolls
    if not rolls:
        return {"total": 0, "min": 0, "max": 0, "mean": 0.0}
    total = sum(rolls)
    minimum = min(rolls)
    maximum = max(rolls)
    mean = total / len(rolls)
    return {
        "total": total,
        "min": minimum,
        "max": maximum,
        "mean": round(mean, 2),
        "count": len(rolls),
    }


def parse_dice_notation(notation):
    # Parse dice notation like '2d6+3' into components
    bonus = 0
    base = notation.strip()
    if "+" in base:
        parts = base.split("+")
        base = parts[0]
        bonus = int(parts[1])
    elif "-" in base and base.index("-") > 0:
        parts = base.split("-")
        base = parts[0]
        bonus = -int(parts[1])
    d_idx = base.lower().index("d")
    num_dice = int(base[:d_idx])
    num_sides = int(base[d_idx + 1:])
    return {"num_dice": num_dice, "num_sides": num_sides, "bonus": bonus}
""",
        "category": "multi_function",
    },

    "multi_021_grade_calculator": {
        "source": """\
def compute_letter_grade(score, scale=None):
    # Convert a numeric score to a letter grade
    if scale is None:
        scale = [
            (90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F"),
        ]
    if score < 0 or score > 100:
        raise ValueError("score must be between 0 and 100")
    for threshold, grade in scale:
        if score >= threshold:
            return grade
    return "F"


def compute_gpa(grades):
    # Compute GPA from a list of (letter_grade, credit_hours) tuples
    grade_points = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}
    total_points = 0.0
    total_credits = 0.0
    for letter, credits in grades:
        upper = letter.upper()
        if upper not in grade_points:
            raise ValueError("unknown grade: " + letter)
        total_points += grade_points[upper] * credits
        total_credits += credits
    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)


def compute_weighted_average(scores, weights):
    # Compute a weighted average from scores and their weights
    if len(scores) != len(weights):
        raise ValueError("scores and weights must have same length")
    total_weight = sum(weights)
    if total_weight == 0:
        raise ValueError("total weight must be positive")
    weighted_sum = 0.0
    for i in range(len(scores)):
        weighted_sum += scores[i] * weights[i]
    return round(weighted_sum / total_weight, 2)
""",
        "category": "multi_function",
    },

    "multi_022_password_strength": {
        "source": """\
def check_password_strength(password):
    # Check password strength and return a score and feedback
    score = 0
    feedback = []
    if len(password) >= 8:
        score += 1
    else:
        feedback.append("too short (need 8+ characters)")
    if len(password) >= 12:
        score += 1
    has_upper = False
    has_lower = False
    has_digit = False
    has_special = False
    for ch in password:
        if ch.isupper():
            has_upper = True
        elif ch.islower():
            has_lower = True
        elif ch.isdigit():
            has_digit = True
        else:
            has_special = True
    if has_upper:
        score += 1
    else:
        feedback.append("add uppercase letters")
    if has_lower:
        score += 1
    else:
        feedback.append("add lowercase letters")
    if has_digit:
        score += 1
    else:
        feedback.append("add digits")
    if has_special:
        score += 1
    else:
        feedback.append("add special characters")
    strength = "weak"
    if score >= 5:
        strength = "strong"
    elif score >= 3:
        strength = "medium"
    return {"score": score, "max_score": 6, "strength": strength, "feedback": feedback}


def has_common_patterns(password):
    # Check if password contains common weak patterns
    common = ["password", "123456", "qwerty", "abc123", "letmein"]
    lower = password.lower()
    for pattern in common:
        if pattern in lower:
            return True
    return False
""",
        "category": "multi_function",
    },

    "multi_023_text_statistics": {
        "source": """\
def count_words(text):
    # Count the number of words in a text
    words = text.split()
    return len(words)


def count_sentences(text):
    # Count sentences by splitting on period, exclamation, or question mark
    count = 0
    for ch in text:
        if ch in ".!?":
            count += 1
    if count == 0 and len(text.strip()) > 0:
        count = 1
    return count


def count_paragraphs(text):
    # Count paragraphs separated by blank lines
    lines = text.split("\\n")
    in_paragraph = False
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped:
            if not in_paragraph:
                count += 1
                in_paragraph = True
        else:
            in_paragraph = False
    return count


def compute_readability(text):
    # Compute approximate readability metrics
    words = count_words(text)
    sentences = count_sentences(text)
    paragraphs = count_paragraphs(text)
    syllable_count = 0
    for word in text.split():
        cleaned = "".join(ch for ch in word.lower() if ch.isalpha())
        vowels = sum(1 for ch in cleaned if ch in "aeiou")
        syllable_count += max(1, vowels)
    avg_words_per_sentence = words / max(1, sentences)
    avg_syllables_per_word = syllable_count / max(1, words)
    return {
        "words": words,
        "sentences": sentences,
        "paragraphs": paragraphs,
        "avg_words_per_sentence": round(avg_words_per_sentence, 2),
        "avg_syllables_per_word": round(avg_syllables_per_word, 2),
    }
""",
        "category": "multi_function",
    },

    "multi_024_currency_formatter": {
        "source": """\
def format_currency(amount, currency_code="USD", locale="en"):
    # Format a numeric amount as a currency string
    symbols = {"USD": "$", "EUR": "E", "GBP": "P", "JPY": "Y"}
    symbol = symbols.get(currency_code, currency_code + " ")
    if currency_code == "JPY":
        formatted_number = format_number(round(amount), 0)
    else:
        formatted_number = format_number(amount, 2)
    if locale == "en":
        return symbol + formatted_number
    else:
        return formatted_number + " " + symbol


def format_number(value, decimal_places):
    # Format a number with thousands separators and decimal places
    is_negative = value < 0
    abs_val = abs(value)
    integer_part = int(abs_val)
    fractional = abs_val - integer_part
    int_str = str(integer_part)
    groups = []
    while len(int_str) > 3:
        groups.insert(0, int_str[-3:])
        int_str = int_str[:-3]
    groups.insert(0, int_str)
    formatted = ",".join(groups)
    if decimal_places > 0:
        frac_str = str(round(fractional, decimal_places))[2:]
        frac_str = frac_str.ljust(decimal_places, "0")
        formatted = formatted + "." + frac_str[:decimal_places]
    if is_negative:
        formatted = "-" + formatted
    return formatted
""",
        "category": "multi_function",
    },

    "multi_025_file_size_formatter": {
        "source": """\
def format_file_size(size_bytes, binary=True):
    # Format a file size in bytes as a human-readable string
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if binary:
        units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        divisor = 1024.0
    else:
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        divisor = 1000.0
    if size_bytes == 0:
        return "0 B"
    value = float(size_bytes)
    unit_index = 0
    while value >= divisor and unit_index < len(units) - 1:
        value /= divisor
        unit_index += 1
    if value == int(value):
        return "%d %s" % (int(value), units[unit_index])
    return "%.2f %s" % (value, units[unit_index])


def parse_file_size(size_str):
    # Parse a human-readable file size string back to bytes
    size_str = size_str.strip()
    multipliers = {
        "B": 1, "KB": 1000, "MB": 1000000, "GB": 1000000000,
        "KIB": 1024, "MIB": 1048576, "GIB": 1073741824,
    }
    number_part = []
    unit_part = []
    for ch in size_str:
        if ch.isdigit() or ch == ".":
            number_part.append(ch)
        elif ch.isalpha():
            unit_part.append(ch)
    number = float("".join(number_part)) if number_part else 0
    unit = "".join(unit_part).upper()
    if not unit:
        unit = "B"
    multiplier = multipliers.get(unit, 1)
    return int(number * multiplier)
""",
        "category": "multi_function",
    },

    "pure_001_fibonacci": {
        "source": """\
def fibonacci(n):
    # Compute the nth Fibonacci number using iteration
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return 0
    if n == 1:
        return 1
    previous = 0
    current = 1
    for step in range(2, n + 1):
        next_val = previous + current
        previous = current
        current = next_val
    phi = (1 + 5 ** 0.5) / 2
    approx = round(phi ** n / 5 ** 0.5)
    if abs(current - approx) > 1:
        raise RuntimeError("Binet approximation check failed")
    return current
""",
        "category": "pure_function",
    },

    "pure_002_gcd_lcm": {
        "source": """\
def gcd_lcm(a, b):
    # Compute GCD and LCM of two integers using Euclidean algorithm
    if not isinstance(a, int) or not isinstance(b, int):
        raise TypeError("both arguments must be integers")
    if a == 0 and b == 0:
        raise ValueError("gcd(0, 0) is undefined")
    original_a = abs(a)
    original_b = abs(b)
    x = original_a
    y = original_b
    while y != 0:
        remainder = x % y
        x = y
        y = remainder
    gcd_value = x
    if original_a == 0 or original_b == 0:
        lcm_value = 0
    else:
        lcm_value = (original_a * original_b) // gcd_value
    result = {
        "gcd": gcd_value,
        "lcm": lcm_value,
        "a": a,
        "b": b,
    }
    return result
""",
        "category": "pure_function",
    },

    "pure_003_matrix_multiply": {
        "source": """\
def matrix_multiply(mat_a, mat_b):
    # Multiply two matrices represented as lists of lists
    if not mat_a or not mat_b:
        raise ValueError("matrices must not be empty")
    rows_a = len(mat_a)
    cols_a = len(mat_a[0])
    rows_b = len(mat_b)
    cols_b = len(mat_b[0])
    if cols_a != rows_b:
        raise ValueError("incompatible dimensions for multiplication")
    for row in mat_a:
        if len(row) != cols_a:
            raise ValueError("matrix A has inconsistent row lengths")
    for row in mat_b:
        if len(row) != cols_b:
            raise ValueError("matrix B has inconsistent row lengths")
    result = []
    for i in range(rows_a):
        new_row = []
        for j in range(cols_b):
            total = 0
            for k in range(cols_a):
                total += mat_a[i][k] * mat_b[k][j]
            new_row.append(total)
        result.append(new_row)
    return result
""",
        "category": "pure_function",
    },

    "pure_004_prime_sieve": {
        "source": """\
def sieve_of_eratosthenes(limit):
    # Return all prime numbers up to limit using the Sieve of Eratosthenes
    if not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = False
    is_prime[1] = False
    current = 2
    while current * current <= limit:
        if is_prime[current]:
            multiple = current * current
            while multiple <= limit:
                is_prime[multiple] = False
                multiple += current
        current += 1
    primes = []
    for number in range(2, limit + 1):
        if is_prime[number]:
            primes.append(number)
    return primes
""",
        "category": "pure_function",
    },

    "pure_005_binary_search": {
        "source": """\
def binary_search(sorted_list, target):
    # Search for target in a sorted list, return index or -1
    if not isinstance(sorted_list, list):
        raise TypeError("first argument must be a list")
    if len(sorted_list) == 0:
        return -1
    low = 0
    high = len(sorted_list) - 1
    iterations = 0
    max_iterations = len(sorted_list) + 1
    while low <= high:
        iterations += 1
        if iterations > max_iterations:
            raise RuntimeError("search exceeded maximum iterations")
        mid = low + (high - low) // 2
        current = sorted_list[mid]
        if current == target:
            return mid
        elif current < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
""",
        "category": "pure_function",
    },

    "pure_006_string_reversal": {
        "source": """\
def reverse_words(text):
    # Reverse the order of words in a string while preserving spacing
    if not isinstance(text, str):
        raise TypeError("input must be a string")
    if len(text) == 0:
        return text
    words = []
    current_word = []
    in_word = False
    for char in text:
        if char == " ":
            if in_word:
                words.append("".join(current_word))
                current_word = []
                in_word = False
        else:
            current_word.append(char)
            in_word = True
    if current_word:
        words.append("".join(current_word))
    words.reverse()
    result = " ".join(words)
    return result
""",
        "category": "pure_function",
    },

    "pure_007_palindrome": {
        "source": """\
def is_palindrome(text):
    # Check if text is a palindrome ignoring case and non-alphanumeric chars
    if not isinstance(text, str):
        raise TypeError("input must be a string")
    cleaned = []
    for char in text:
        if char.isalnum():
            cleaned.append(char.lower())
    if len(cleaned) == 0:
        return True
    left = 0
    right = len(cleaned) - 1
    is_match = True
    while left < right:
        if cleaned[left] != cleaned[right]:
            is_match = False
            break
        left += 1
        right -= 1
    return is_match
""",
        "category": "pure_function",
    },

    "pure_008_caesar_cipher": {
        "source": """\
def caesar_cipher(text, shift):
    # Encrypt text using Caesar cipher with given shift
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(shift, int):
        raise TypeError("shift must be an integer")
    effective_shift = shift % 26
    if effective_shift == 0 and shift != 0:
        effective_shift = 26 if shift > 0 else 0
    result = []
    for char in text:
        if char.isalpha():
            base = ord("A") if char.isupper() else ord("a")
            code = ord(char) - base
            shifted = (code + effective_shift) % 26
            new_char = chr(base + shifted)
            result.append(new_char)
        else:
            result.append(char)
    encrypted = "".join(result)
    return encrypted
""",
        "category": "pure_function",
    },

    "pure_009_run_length_encode": {
        "source": """\
def run_length_encode(data):
    # Encode a string using run-length encoding
    if not isinstance(data, str):
        raise TypeError("input must be a string")
    if len(data) == 0:
        return []
    encoded = []
    current_char = data[0]
    count = 1
    for i in range(1, len(data)):
        if data[i] == current_char:
            count += 1
        else:
            encoded.append((current_char, count))
            current_char = data[i]
            count = 1
    encoded.append((current_char, count))
    total_chars = sum(c for _, c in encoded)
    if total_chars != len(data):
        raise RuntimeError("encoding length mismatch")
    return encoded
""",
        "category": "pure_function",
    },

    "pure_010_histogram": {
        "source": """\
def build_histogram(values, num_bins):
    # Build a frequency histogram from a list of numeric values
    if not values:
        raise ValueError("values must not be empty")
    if num_bins < 1:
        raise ValueError("num_bins must be at least 1")
    min_val = values[0]
    max_val = values[0]
    for v in values:
        if v < min_val:
            min_val = v
        if v > max_val:
            max_val = v
    bin_width = (max_val - min_val) / num_bins if max_val != min_val else 1.0
    bins = [0] * num_bins
    for v in values:
        if max_val == min_val:
            idx = 0
        else:
            idx = int((v - min_val) / bin_width)
            if idx >= num_bins:
                idx = num_bins - 1
        bins[idx] += 1
    result = {
        "min": min_val,
        "max": max_val,
        "bin_width": bin_width,
        "counts": bins,
    }
    return result
""",
        "category": "pure_function",
    },

    "pure_011_moving_average": {
        "source": """\
def moving_average(values, window_size):
    # Compute the simple moving average over a list of numbers
    if not isinstance(values, list):
        raise TypeError("values must be a list")
    if not isinstance(window_size, int):
        raise TypeError("window_size must be an integer")
    if window_size < 1:
        raise ValueError("window_size must be at least 1")
    if window_size > len(values):
        raise ValueError("window_size exceeds number of values")
    averages = []
    window_sum = 0.0
    for i in range(window_size):
        window_sum += values[i]
    averages.append(window_sum / window_size)
    for i in range(window_size, len(values)):
        window_sum += values[i]
        window_sum -= values[i - window_size]
        avg = window_sum / window_size
        averages.append(avg)
    return averages
""",
        "category": "pure_function",
    },

    "pure_012_polynomial_eval": {
        "source": """\
def evaluate_polynomial(coefficients, x):
    # Evaluate polynomial at x using Horner's method
    # coefficients[0] is the highest degree term
    if not isinstance(coefficients, list):
        raise TypeError("coefficients must be a list")
    if len(coefficients) == 0:
        return 0
    n = len(coefficients)
    result = coefficients[0]
    for i in range(1, n):
        result = result * x + coefficients[i]
    degree = n - 1
    derivative_coeffs = []
    for i in range(degree):
        power = degree - i
        derivative_coeffs.append(coefficients[i] * power)
    deriv_value = 0
    if derivative_coeffs:
        deriv_value = derivative_coeffs[0]
        for i in range(1, len(derivative_coeffs)):
            deriv_value = deriv_value * x + derivative_coeffs[i]
    return {"value": result, "derivative": deriv_value}
""",
        "category": "pure_function",
    },

    "pure_013_newtons_method": {
        "source": """\
def newtons_method(f, f_prime, x0, tolerance=1e-10, max_iter=100):
    # Find a root of f using Newton's method
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    if max_iter < 1:
        raise ValueError("max_iter must be at least 1")
    x = x0
    history = [x]
    converged = False
    for iteration in range(max_iter):
        fx = f(x)
        fpx = f_prime(x)
        if abs(fpx) < 1e-15:
            break
        x_new = x - fx / fpx
        history.append(x_new)
        if abs(x_new - x) < tolerance:
            converged = True
            x = x_new
            break
        x = x_new
    result = {
        "root": x,
        "converged": converged,
        "iterations": len(history) - 1,
        "history": history,
    }
    return result
""",
        "category": "pure_function",
    },

    "pure_014_roman_numeral": {
        "source": """\
def int_to_roman(num):
    # Convert an integer to a Roman numeral string
    if not isinstance(num, int):
        raise TypeError("input must be an integer")
    if num < 1 or num > 3999:
        raise ValueError("number must be between 1 and 3999")
    thousands = ["", "M", "MM", "MMM"]
    hundreds = ["", "C", "CC", "CCC", "CD", "D", "DC", "DCC", "DCCC", "CM"]
    tens = ["", "X", "XX", "XXX", "XL", "L", "LX", "LXX", "LXXX", "XC"]
    ones = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"]
    th = thousands[num // 1000]
    hu = hundreds[(num % 1000) // 100]
    te = tens[(num % 100) // 10]
    on = ones[num % 10]
    result = th + hu + te + on
    total = 0
    roman_values = {"I": 1, "V": 5, "X": 10, "L": 50,
                    "C": 100, "D": 500, "M": 1000}
    for i in range(len(result)):
        val = roman_values[result[i]]
        if i + 1 < len(result) and val < roman_values[result[i + 1]]:
            total -= val
        else:
            total += val
    assert total == num, "round-trip check failed"
    return result
""",
        "category": "pure_function",
    },

    "pure_015_temperature_convert": {
        "source": """\
def convert_temperature(value, from_unit, to_unit):
    # Convert temperature between Celsius, Fahrenheit, and Kelvin
    valid_units = ("C", "F", "K")
    if from_unit not in valid_units:
        raise ValueError("from_unit must be C, F, or K")
    if to_unit not in valid_units:
        raise ValueError("to_unit must be C, F, or K")
    if from_unit == to_unit:
        return round(value, 4)
    if from_unit == "C":
        celsius = value
    elif from_unit == "F":
        celsius = (value - 32) * 5.0 / 9.0
    else:
        celsius = value - 273.15
    if celsius < -273.15:
        raise ValueError("temperature below absolute zero")
    if to_unit == "C":
        result = celsius
    elif to_unit == "F":
        result = celsius * 9.0 / 5.0 + 32
    else:
        result = celsius + 273.15
    return round(result, 4)
""",
        "category": "pure_function",
    },

    "pure_016_distance_calc": {
        "source": """\
def compute_distances(point_a, point_b):
    # Compute Euclidean, Manhattan, and Chebyshev distances between two points
    if len(point_a) != len(point_b):
        raise ValueError("points must have the same dimension")
    if len(point_a) == 0:
        raise ValueError("points must have at least one dimension")
    dimensions = len(point_a)
    squared_sum = 0.0
    manhattan = 0.0
    chebyshev = 0.0
    for i in range(dimensions):
        diff = abs(point_a[i] - point_b[i])
        squared_sum += diff * diff
        manhattan += diff
        if diff > chebyshev:
            chebyshev = diff
    euclidean = squared_sum ** 0.5
    result = {
        "euclidean": round(euclidean, 6),
        "manhattan": round(manhattan, 6),
        "chebyshev": round(chebyshev, 6),
        "dimensions": dimensions,
    }
    return result
""",
        "category": "pure_function",
    },

    "pure_017_checksum": {
        "source": """\
def compute_checksum(data):
    # Compute multiple checksums for a list of integers (0-255)
    if not isinstance(data, (list, tuple)):
        raise TypeError("data must be a list or tuple")
    for byte in data:
        if not isinstance(byte, int) or byte < 0 or byte > 255:
            raise ValueError("each element must be an integer 0-255")
    simple_sum = 0
    xor_sum = 0
    fletcher_a = 0
    fletcher_b = 0
    for byte in data:
        simple_sum = (simple_sum + byte) & 0xFFFF
        xor_sum ^= byte
        fletcher_a = (fletcher_a + byte) % 255
        fletcher_b = (fletcher_b + fletcher_a) % 255
    fletcher = (fletcher_b << 8) | fletcher_a
    result = {
        "simple_sum": simple_sum,
        "xor": xor_sum,
        "fletcher16": fletcher,
        "length": len(data),
    }
    return result
""",
        "category": "pure_function",
    },

    "pure_018_base_conversion": {
        "source": """\
def convert_base(number_str, from_base, to_base):
    # Convert a number string from one base to another (bases 2-36)
    if from_base < 2 or from_base > 36:
        raise ValueError("from_base must be between 2 and 36")
    if to_base < 2 or to_base > 36:
        raise ValueError("to_base must be between 2 and 36")
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    upper_str = number_str.upper().strip()
    is_negative = upper_str.startswith("-")
    if is_negative:
        upper_str = upper_str[1:]
    decimal_value = 0
    for char in upper_str:
        digit_val = digits.index(char)
        if digit_val >= from_base:
            raise ValueError("invalid digit for the given base")
        decimal_value = decimal_value * from_base + digit_val
    if decimal_value == 0:
        return "0"
    result_digits = []
    temp = decimal_value
    while temp > 0:
        result_digits.append(digits[temp % to_base])
        temp //= to_base
    result_digits.reverse()
    result = "".join(result_digits)
    if is_negative:
        result = "-" + result
    return result
""",
        "category": "pure_function",
    },

    "pure_019_statistics": {
        "source": """\
def compute_statistics(values):
    # Compute mean, median, variance, and standard deviation
    if not isinstance(values, list) or len(values) == 0:
        raise ValueError("values must be a non-empty list")
    n = len(values)
    total = 0.0
    for v in values:
        total += v
    mean = total / n
    sorted_vals = sorted(values)
    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    variance_sum = 0.0
    for v in values:
        diff = v - mean
        variance_sum += diff * diff
    variance = variance_sum / n
    std_dev = variance ** 0.5
    result = {
        "mean": round(mean, 6),
        "median": round(median, 6),
        "variance": round(variance, 6),
        "std_dev": round(std_dev, 6),
        "count": n,
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
    }
    return result
""",
        "category": "pure_function",
    },

    "pure_020_levenshtein": {
        "source": """\
def levenshtein_distance(source, target):
    # Compute the Levenshtein edit distance between two strings
    if not isinstance(source, str) or not isinstance(target, str):
        raise TypeError("both arguments must be strings")
    len_s = len(source)
    len_t = len(target)
    if len_s == 0:
        return len_t
    if len_t == 0:
        return len_s
    previous_row = list(range(len_t + 1))
    current_row = [0] * (len_t + 1)
    for i in range(1, len_s + 1):
        current_row[0] = i
        for j in range(1, len_t + 1):
            cost = 0 if source[i - 1] == target[j - 1] else 1
            insert_cost = current_row[j - 1] + 1
            delete_cost = previous_row[j] + 1
            replace_cost = previous_row[j - 1] + cost
            current_row[j] = min(insert_cost, delete_cost, replace_cost)
        previous_row, current_row = current_row, previous_row
        current_row = [0] * (len_t + 1)
    return previous_row[len_t]
""",
        "category": "pure_function",
    },

    "pure_021_knapsack": {
        "source": """\
def knapsack_01(weights, values, capacity):
    # Solve 0/1 knapsack problem using dynamic programming
    if len(weights) != len(values):
        raise ValueError("weights and values must have same length")
    if capacity < 0:
        raise ValueError("capacity must be non-negative")
    n = len(weights)
    dp = []
    for i in range(n + 1):
        dp.append([0] * (capacity + 1))
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            if weights[i - 1] <= w:
                include = values[i - 1] + dp[i - 1][w - weights[i - 1]]
                exclude = dp[i - 1][w]
                dp[i][w] = max(include, exclude)
            else:
                dp[i][w] = dp[i - 1][w]
    selected = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i - 1][w]:
            selected.append(i - 1)
            w -= weights[i - 1]
    selected.reverse()
    return {"max_value": dp[n][capacity], "selected_items": selected}
""",
        "category": "pure_function",
    },

    "pure_022_coin_change": {
        "source": """\
def coin_change(coins, amount):
    # Find minimum number of coins to make the given amount
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not coins:
        raise ValueError("coins list must not be empty")
    for c in coins:
        if c <= 0:
            raise ValueError("coin values must be positive")
    max_val = amount + 1
    dp = [max_val] * (amount + 1)
    dp[0] = 0
    parent = [-1] * (amount + 1)
    for i in range(1, amount + 1):
        for coin in coins:
            if coin <= i and dp[i - coin] + 1 < dp[i]:
                dp[i] = dp[i - coin] + 1
                parent[i] = coin
    if dp[amount] > amount:
        return {"possible": False, "count": -1, "coins_used": []}
    used = []
    remaining = amount
    while remaining > 0:
        used.append(parent[remaining])
        remaining -= parent[remaining]
    return {"possible": True, "count": dp[amount], "coins_used": sorted(used)}
""",
        "category": "pure_function",
    },

    "pure_023_lcs": {
        "source": """\
def longest_common_subsequence(seq_a, seq_b):
    # Find the longest common subsequence of two sequences
    if not isinstance(seq_a, (str, list)) or not isinstance(seq_b, (str, list)):
        raise TypeError("arguments must be strings or lists")
    m = len(seq_a)
    n = len(seq_b)
    dp = []
    for i in range(m + 1):
        dp.append([0] * (n + 1))
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = []
    i = m
    j = n
    while i > 0 and j > 0:
        if seq_a[i - 1] == seq_b[j - 1]:
            lcs.append(seq_a[i - 1])
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    lcs.reverse()
    return {"length": dp[m][n], "subsequence": lcs}
""",
        "category": "pure_function",
    },

    "pure_024_anagram_detect": {
        "source": """\
def are_anagrams(word_a, word_b):
    # Determine if two words are anagrams of each other
    if not isinstance(word_a, str) or not isinstance(word_b, str):
        raise TypeError("both arguments must be strings")
    cleaned_a = word_a.lower().strip()
    cleaned_b = word_b.lower().strip()
    filtered_a = []
    for ch in cleaned_a:
        if ch.isalpha():
            filtered_a.append(ch)
    filtered_b = []
    for ch in cleaned_b:
        if ch.isalpha():
            filtered_b.append(ch)
    if len(filtered_a) != len(filtered_b):
        return False
    freq = {}
    for ch in filtered_a:
        freq[ch] = freq.get(ch, 0) + 1
    for ch in filtered_b:
        freq[ch] = freq.get(ch, 0) - 1
    for count in freq.values():
        if count != 0:
            return False
    return True
""",
        "category": "pure_function",
    },

    "pure_025_date_validation": {
        "source": """\
def is_valid_date(year, month, day):
    # Validate a calendar date including leap year handling
    if not isinstance(year, int) or not isinstance(month, int) or not isinstance(day, int):
        raise TypeError("year, month, and day must be integers")
    if year < 1 or year > 9999:
        return False
    if month < 1 or month > 12:
        return False
    if day < 1:
        return False
    days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    is_leap = False
    if year % 4 == 0:
        is_leap = True
        if year % 100 == 0:
            is_leap = False
            if year % 400 == 0:
                is_leap = True
    if is_leap:
        days_in_month[2] = 29
    max_day = days_in_month[month]
    if day > max_day:
        return False
    return True
""",
        "category": "pure_function",
    },

}


# ── validation ─────────────────────────────────────────────────────────────

for _name, _prog in PROGRAMS.items():
    try:
        ast.parse(_prog["source"])
    except SyntaxError as e:
        print(f"SYNTAX ERROR in {_name}: {e}")
        sys.exit(1)
    _lines = [l for l in _prog["source"].strip().splitlines() if l.strip()]
    if len(_lines) < 20:
        print(f"TOO SHORT: {_name} has {len(_lines)} lines (need 20+)")
        sys.exit(1)


# ── helpers ────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


# ---------------------------------------------------------------------------
# §1  Run jugeo prove and jugeo encode on all 100 programs
# ---------------------------------------------------------------------------

def experiment_1_prove_encode(results: dict, temp_files: list) -> dict:
    section("§1  jugeo prove + jugeo encode — all 100 programs")

    prove_encode_data = {}
    names = list(PROGRAMS.keys())
    for i, name in enumerate(names):
        prog = PROGRAMS[name]
        source = prog["source"]
        category = prog["category"]
        print(f"  [{i+1}/100] {name} ...", end="", flush=True)

        path = write_temp_py(source)
        temp_files.append(path)

        # jugeo prove
        t0 = time.perf_counter()
        prove_objs = run_jugeo("prove", path)
        prove_elapsed = time.perf_counter() - t0

        prove = prove_objs[0] if prove_objs else {}
        files = prove.get("files", [{}])
        f0 = files[0] if files else {}

        formal = None
        for o in prove_objs:
            if "formal_verification" in o:
                formal = o["formal_verification"]

        cat_struct = {}
        if formal:
            cat_struct = formal.get("category_structure", {})

        coordinates = f0.get("coordinates", 0)
        propositions_total = f0.get("propositions_total", 0)
        n_objects = cat_struct.get("n_objects", 0)
        n_morphisms = cat_struct.get("n_morphisms", 0)
        verdict = f0.get("verdict", "unknown")

        # jugeo encode
        t1 = time.perf_counter()
        encode_objs = run_jugeo("encode", path)
        encode_elapsed = time.perf_counter() - t1

        encode = encode_objs[0] if encode_objs else {}
        totals = encode.get("totals", {})
        encode_coordinates = totals.get("coordinates", 0)
        encode_declarations = totals.get("declarations", 0)

        prove_encode_data[name] = {
            "category": category,
            "coordinates": coordinates,
            "propositions_total": propositions_total,
            "n_objects": n_objects,
            "n_morphisms": n_morphisms,
            "verdict": verdict,
            "encode_coordinates": encode_coordinates,
            "encode_declarations": encode_declarations,
            "prove_elapsed_s": round(prove_elapsed, 4),
            "encode_elapsed_s": round(encode_elapsed, 4),
        }
        print(f" coords={coordinates} morph={n_morphisms} verdict={verdict}")

    results["§1_prove_encode"] = prove_encode_data
    return prove_encode_data


# ---------------------------------------------------------------------------
# §2  Morphism analysis
# ---------------------------------------------------------------------------

def experiment_2_morphism_analysis(results: dict, data: dict) -> None:
    section("§2  Morphism analysis by category")

    categories = ["pure_function", "multi_function", "class", "module_with_deps"]
    cat_stats = {}

    for cat in categories:
        entries = [v for v in data.values() if v["category"] == cat]
        if not entries:
            continue
        avg_morphisms = sum(e["n_morphisms"] for e in entries) / len(entries)
        avg_coordinates = sum(e["coordinates"] for e in entries) / len(entries)
        morphism_gt_coords = sum(
            1 for e in entries if e["n_morphisms"] > e["coordinates"]
        )
        ratio = avg_morphisms / avg_coordinates if avg_coordinates > 0 else 0.0
        cat_stats[cat] = {
            "count": len(entries),
            "avg_morphisms": round(avg_morphisms, 2),
            "avg_coordinates": round(avg_coordinates, 2),
            "morphism_to_coord_ratio": round(ratio, 4),
            "programs_morphisms_gt_coords": morphism_gt_coords,
        }

    # Print summary table
    print(f"\n  {'Category':<22s} {'Count':>6s} {'Avg Morph':>10s} "
          f"{'Avg Coord':>10s} {'Ratio':>8s} {'M>C':>5s}")
    print(f"  {'-'*22} {'-'*6} {'-'*10} {'-'*10} {'-'*8} {'-'*5}")
    for cat in categories:
        s = cat_stats.get(cat, {})
        print(f"  {cat:<22s} {s.get('count',0):6d} "
              f"{s.get('avg_morphisms',0):10.2f} "
              f"{s.get('avg_coordinates',0):10.2f} "
              f"{s.get('morphism_to_coord_ratio',0):8.4f} "
              f"{s.get('programs_morphisms_gt_coords',0):5d}")

    # Test claim: multi_function and module_with_deps should have morphisms > coordinates
    for cat in ["multi_function", "module_with_deps"]:
        s = cat_stats.get(cat, {})
        claim = s.get("avg_morphisms", 0) > s.get("avg_coordinates", 0)
        print(f"\n  Claim for {cat}: avg_morphisms > avg_coordinates = {claim}")

    results["§2_morphism_analysis"] = cat_stats


# ---------------------------------------------------------------------------
# §3  Cross-category comparison
# ---------------------------------------------------------------------------

def experiment_3_cross_category(results: dict, data: dict) -> None:
    section("§3  Cross-category comparison")

    categories = ["pure_function", "multi_function", "class", "module_with_deps"]
    ranked = []

    for cat in categories:
        entries = [v for v in data.values() if v["category"] == cat]
        if not entries:
            continue
        total_morphisms = sum(e["n_morphisms"] for e in entries)
        total_coordinates = sum(e["coordinates"] for e in entries)
        ratio = total_morphisms / total_coordinates if total_coordinates > 0 else 0.0
        total_objects = sum(e["n_objects"] for e in entries)
        avg_propositions = sum(e["propositions_total"] for e in entries) / len(entries)
        ranked.append({
            "category": cat,
            "total_morphisms": total_morphisms,
            "total_coordinates": total_coordinates,
            "morphisms_per_coordinate": round(ratio, 4),
            "total_objects": total_objects,
            "avg_propositions": round(avg_propositions, 2),
        })

    ranked.sort(key=lambda x: x["morphisms_per_coordinate"], reverse=True)

    print(f"\n  {'Rank':>4s} {'Category':<22s} {'Total Morph':>12s} "
          f"{'Total Coord':>12s} {'Morph/Coord':>12s}")
    print(f"  {'-'*4} {'-'*22} {'-'*12} {'-'*12} {'-'*12}")
    for rank, entry in enumerate(ranked, 1):
        print(f"  {rank:4d} {entry['category']:<22s} "
              f"{entry['total_morphisms']:12d} "
              f"{entry['total_coordinates']:12d} "
              f"{entry['morphisms_per_coordinate']:12.4f}")

    results["§3_cross_category"] = {"ranked": ranked}


# ---------------------------------------------------------------------------
# §4  Aggregate statistics
# ---------------------------------------------------------------------------

def experiment_4_aggregate(results: dict, data: dict) -> None:
    section("§4  Aggregate statistics")

    total_programs = len(data)
    total_morphisms = sum(v["n_morphisms"] for v in data.values())
    total_coordinates = sum(v["coordinates"] for v in data.values())
    total_objects = sum(v["n_objects"] for v in data.values())
    total_propositions = sum(v["propositions_total"] for v in data.values())

    morphisms_gt_coords = sum(
        1 for v in data.values() if v["n_morphisms"] > v["coordinates"]
    )
    pct = morphisms_gt_coords / total_programs * 100 if total_programs > 0 else 0

    overall_ratio = (
        total_morphisms / total_coordinates if total_coordinates > 0 else 0.0
    )

    verdicts = defaultdict(int)
    for v in data.values():
        verdicts[v["verdict"]] += 1

    agg = {
        "total_programs": total_programs,
        "total_morphisms": total_morphisms,
        "total_coordinates": total_coordinates,
        "total_objects": total_objects,
        "total_propositions": total_propositions,
        "programs_morphisms_gt_coordinates": morphisms_gt_coords,
        "pct_morphisms_gt_coordinates": round(pct, 2),
        "overall_morphism_coord_ratio": round(overall_ratio, 4),
        "verdicts": dict(verdicts),
    }

    print(f"  Total programs:            {total_programs}")
    print(f"  Total morphisms:           {total_morphisms}")
    print(f"  Total coordinates:         {total_coordinates}")
    print(f"  Total objects:             {total_objects}")
    print(f"  Total propositions:        {total_propositions}")
    print(f"  Programs M > C:            {morphisms_gt_coords} ({pct:.1f}%)")
    print(f"  Overall morph/coord ratio: {overall_ratio:.4f}")
    print(f"  Verdicts: {dict(verdicts)}")

    results["§4_aggregate"] = agg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("  Experiment 06 — Semantic Moves: Morphism Counts Across 100 Programs")
    print("=" * 72)

    t0 = time.perf_counter()
    results: dict = {}
    temp_files: list = []

    try:
        prove_encode_data = experiment_1_prove_encode(results, temp_files)
        experiment_2_morphism_analysis(results, prove_encode_data)
        experiment_3_cross_category(results, prove_encode_data)
        experiment_4_aggregate(results, prove_encode_data)

        elapsed = time.perf_counter() - t0

        output = {
            "experiment": "semantic_moves",
            "paper": 6,
            "note": (
                "Morphism counts and types across 100 Python programs.  "
                "All JuGeo numbers from CLI calls (jugeo prove / jugeo encode)."
            ),
            "random_seed": 42,
            "total_elapsed_s": round(elapsed, 4),
            "§1_prove_encode": results.get("§1_prove_encode", {}),
            "§2_morphism_analysis": results.get("§2_morphism_analysis", {}),
            "§3_cross_category": results.get("§3_cross_category", {}),
            "§4_aggregate": results.get("§4_aggregate", {}),
        }

        outpath = os.path.join(os.path.dirname(__file__), "results_paper06.json")
        with open(outpath, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Total elapsed: {elapsed:.3f}s")
        print(f"  Results -> {outpath}")
        print("=" * 72)
    finally:
        for p in temp_files:
            try:
                os.unlink(p)
            except OSError:
                pass


if __name__ == "__main__":
    main()
