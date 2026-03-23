#!/usr/bin/env python3
"""
Experiment 07 — Verifying Effectful Python Without Leaving Python
=================================================================

Uses ``jugeo prove`` CLI command to verify programs with Python effects
(exceptions, I/O-like, mutation).  Compares coordinate/proposition counts
for effectful vs pure code and runs baseline AST analysis.

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
# AST-based effect detector (baseline)
# ---------------------------------------------------------------------------

EFFECT_FAMILIES = [
    "exception", "mutable_state", "async_await", "generator", "context_manager",
]


def detect_effects(source: str) -> dict:
    """Walk the AST and report which effect families are present."""
    tree = ast.parse(source)
    found = {f: [] for f in EFFECT_FAMILIES}

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            found["exception"].append({"node_type": "Try", "lineno": node.lineno})
        elif isinstance(node, ast.Raise):
            found["exception"].append({"node_type": "Raise", "lineno": node.lineno})
        elif isinstance(node, ast.AugAssign):
            found["mutable_state"].append({"node_type": "AugAssign", "lineno": node.lineno})
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    found["mutable_state"].append({"node_type": "SubscriptAssign", "lineno": node.lineno})
                elif isinstance(target, ast.Attribute):
                    found["mutable_state"].append({"node_type": "AttributeAssign", "lineno": node.lineno})
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("append", "extend", "insert", "pop",
                                       "remove", "clear", "update", "add",
                                       "discard", "sort", "reverse"):
                    found["mutable_state"].append(
                        {"node_type": f"MethodCall_{node.func.attr}", "lineno": node.lineno})
        elif isinstance(node, ast.AsyncFunctionDef):
            found["async_await"].append({"node_type": "AsyncFunctionDef", "lineno": node.lineno})
        elif isinstance(node, ast.Await):
            found["async_await"].append({"node_type": "Await", "lineno": node.lineno})
        elif isinstance(node, ast.AsyncWith):
            found["async_await"].append({"node_type": "AsyncWith", "lineno": node.lineno})
        elif isinstance(node, ast.AsyncFor):
            found["async_await"].append({"node_type": "AsyncFor", "lineno": node.lineno})
        elif isinstance(node, ast.Yield):
            found["generator"].append({"node_type": "Yield", "lineno": node.lineno})
        elif isinstance(node, ast.YieldFrom):
            found["generator"].append({"node_type": "YieldFrom", "lineno": node.lineno})
        elif isinstance(node, ast.With):
            found["context_manager"].append({"node_type": "With", "lineno": node.lineno})

    return found


# ---------------------------------------------------------------------------
# 100 benchmark programs — 25 per category
# ---------------------------------------------------------------------------

PROGRAMS = {
    # ── pure functions (25) ───────────────────────────────────────────────

    "pure_001_gcd_lcm": {
        "source": """\
def gcd(a, b):
    # Euclidean algorithm for greatest common divisor
    if a < 0:
        a = -a
    if b < 0:
        b = -b
    while b != 0:
        a, b = b, a % b
    return a


def lcm(a, b):
    # Least common multiple via GCD
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def are_coprime(a, b):
    # Two numbers are coprime if their GCD is 1
    return gcd(a, b) == 1


def gcd_of_list(numbers):
    # GCD across a list of integers
    result = numbers[0]
    for n in numbers[1:]:
        result = gcd(result, n)
    return result
""",
        "category": "pure",
    },

    "pure_002_binary_search": {
        "source": """\
def binary_search(arr, target):
    # Standard binary search returning index or -1
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1


def binary_search_leftmost(arr, target):
    # Find leftmost occurrence of target
    low = 0
    high = len(arr)
    while low < high:
        mid = (low + high) // 2
        if arr[mid] < target:
            low = mid + 1
        else:
            high = mid
    return low


def binary_search_rightmost(arr, target):
    # Find rightmost occurrence of target
    low = 0
    high = len(arr)
    while low < high:
        mid = (low + high) // 2
        if arr[mid] <= target:
            low = mid + 1
        else:
            high = mid
    return low - 1
""",
        "category": "pure",
    },

    "pure_003_matrix_multiply": {
        "source": """\
def matrix_zeros(rows, cols):
    # Create a zero matrix of given dimensions
    result = []
    for i in range(rows):
        row = []
        for j in range(cols):
            row = row + [0]
        result = result + [row]
    return result


def matrix_multiply(a, b):
    # Multiply two matrices a and b
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = matrix_zeros(rows_a, cols_b)
    for i in range(rows_a):
        for j in range(cols_b):
            total = 0
            for k in range(cols_a):
                total = total + a[i][k] * b[k][j]
            result[i][j] = total
    return result


def matrix_transpose(m):
    # Transpose a matrix
    rows = len(m)
    cols = len(m[0])
    result = matrix_zeros(cols, rows)
    for i in range(rows):
        for j in range(cols):
            result[j][i] = m[i][j]
    return result
""",
        "category": "pure",
    },

    "pure_004_polynomial_eval": {
        "source": """\
def polynomial_eval(coeffs, x):
    # Evaluate polynomial using Horner's method
    # coeffs[0] is the highest degree coefficient
    result = 0
    for coeff in coeffs:
        result = result * x + coeff
    return result


def polynomial_add(p1, p2):
    # Add two polynomials represented as coefficient lists
    len1 = len(p1)
    len2 = len(p2)
    max_len = max(len1, len2)
    result = []
    for i in range(max_len):
        c1 = p1[i] if i < len1 else 0
        c2 = p2[i] if i < len2 else 0
        result = result + [c1 + c2]
    return result


def polynomial_derivative(coeffs):
    # Compute derivative of polynomial
    # coeffs[i] is the coefficient for x^i
    if len(coeffs) <= 1:
        return [0]
    result = []
    for i in range(1, len(coeffs)):
        result = result + [coeffs[i] * i]
    return result


def polynomial_multiply(p1, p2):
    # Multiply two polynomials
    result_len = len(p1) + len(p2) - 1
    result = [0] * result_len
    for i in range(len(p1)):
        for j in range(len(p2)):
            result[i + j] = result[i + j] + p1[i] * p2[j]
    return result
""",
        "category": "pure",
    },

    "pure_005_permutations": {
        "source": """\
def swap_elements(arr, i, j):
    # Swap two elements in a list copy
    result = list(arr)
    temp = result[i]
    result[i] = result[j]
    result[j] = temp
    return result


def permutations_helper(arr, start, results):
    # Recursive helper for generating permutations
    if start == len(arr) - 1:
        return results + [list(arr)]
    current = results
    for i in range(start, len(arr)):
        swapped = swap_elements(arr, start, i)
        current = permutations_helper(swapped, start + 1, current)
    return current


def get_permutations(arr):
    # Generate all permutations of a list
    return permutations_helper(list(arr), 0, [])


def count_permutations(n, r):
    # Count permutations: n! / (n-r)!
    if r > n or r < 0:
        return 0
    result = 1
    for i in range(n, n - r, -1):
        result = result * i
    return result


def is_permutation(a, b):
    # Check if b is a permutation of a
    if len(a) != len(b):
        return False
    sorted_a = sorted(a)
    sorted_b = sorted(b)
    return sorted_a == sorted_b
""",
        "category": "pure",
    },

    "pure_006_combination_count": {
        "source": """\
def factorial(n):
    # Compute n! iteratively
    if n < 0:
        return 0
    result = 1
    for i in range(2, n + 1):
        result = result * i
    return result


def binomial(n, k):
    # Compute binomial coefficient C(n, k)
    if k < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    k = min(k, n - k)
    result = 1
    for i in range(k):
        result = result * (n - i) // (i + 1)
    return result


def pascal_triangle_row(n):
    # Compute the nth row of Pascal's triangle
    row = [1]
    for i in range(1, n + 1):
        val = row[i - 1] * (n - i + 1) // i
        row = row + [val]
    return row


def catalan_number(n):
    # Compute the nth Catalan number
    return binomial(2 * n, n) // (n + 1)


def stirling_second(n, k):
    # Stirling number of the second kind via inclusion-exclusion
    if n == 0 and k == 0:
        return 1
    if n == 0 or k == 0:
        return 0
    total = 0
    for i in range(k + 1):
        sign = 1 if (k - i) % 2 == 0 else -1
        total = total + sign * binomial(k, i) * (i ** n)
    return total // factorial(k)
""",
        "category": "pure",
    },

    "pure_007_prime_factorization": {
        "source": """\
def is_prime(n):
    # Check if n is prime using trial division
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
        i = i + 6
    return True


def prime_factors(n):
    # Return sorted list of prime factors with multiplicity
    factors = []
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors = factors + [d]
            n = n // d
        d = d + 1
    if n > 1:
        factors = factors + [n]
    return factors


def sieve_of_eratosthenes(limit):
    # Return all primes up to limit
    is_p = [True] * (limit + 1)
    is_p[0] = False
    is_p[1] = False
    p = 2
    while p * p <= limit:
        if is_p[p]:
            multiple = p * p
            while multiple <= limit:
                is_p[multiple] = False
                multiple = multiple + p
        p = p + 1
    result = []
    for i in range(2, limit + 1):
        if is_p[i]:
            result = result + [i]
    return result
""",
        "category": "pure",
    },

    "pure_008_merge_sorted": {
        "source": """\
def merge_two_sorted(a, b):
    # Merge two sorted lists into one sorted list
    result = []
    i = 0
    j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            result = result + [a[i]]
            i = i + 1
        else:
            result = result + [b[j]]
            j = j + 1
    while i < len(a):
        result = result + [a[i]]
        i = i + 1
    while j < len(b):
        result = result + [b[j]]
        j = j + 1
    return result


def merge_sort(arr):
    # Merge sort implementation
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge_two_sorted(left, right)


def is_sorted(arr):
    # Check if list is sorted in non-decreasing order
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True
""",
        "category": "pure",
    },
    "pure_009_quickselect": {
        "source": """\
def partition(arr, low, high):
    # Partition array around pivot (last element)
    pivot = arr[high]
    i = low - 1
    for j in range(low, high):
        if arr[j] <= pivot:
            i = i + 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def quickselect(arr, k):
    # Find kth smallest element (0-indexed)
    working = list(arr)
    low = 0
    high = len(working) - 1
    while low <= high:
        pivot_idx = partition(working, low, high)
        if pivot_idx == k:
            return working[pivot_idx]
        elif pivot_idx < k:
            low = pivot_idx + 1
        else:
            high = pivot_idx - 1
    return None


def find_median(arr):
    # Find median using quickselect
    n = len(arr)
    if n % 2 == 1:
        return quickselect(arr, n // 2)
    left = quickselect(arr, n // 2 - 1)
    right = quickselect(arr, n // 2)
    return (left + right) / 2.0
""",
        "category": "pure",
    },

    "pure_010_run_length": {
        "source": """\
def run_length_encode(data):
    # Encode data using run-length encoding
    if not data:
        return []
    result = []
    current = data[0]
    count = 1
    for i in range(1, len(data)):
        if data[i] == current:
            count = count + 1
        else:
            result = result + [(current, count)]
            current = data[i]
            count = 1
    result = result + [(current, count)]
    return result


def run_length_decode(encoded):
    # Decode run-length encoded data
    result = []
    for value, count in encoded:
        for _ in range(count):
            result = result + [value]
    return result


def compress_string(s):
    # Compress string using run-length encoding
    if not s:
        return ""
    result = ""
    current = s[0]
    count = 1
    for i in range(1, len(s)):
        if s[i] == current:
            count = count + 1
        else:
            result = result + current + str(count)
            current = s[i]
            count = 1
    result = result + current + str(count)
    if len(result) >= len(s):
        return s
    return result
""",
        "category": "pure",
    },

    "pure_011_caesar_cipher": {
        "source": """\
def caesar_encrypt_char(ch, shift):
    # Encrypt a single character with Caesar cipher
    if ch.isalpha():
        base = ord('A') if ch.isupper() else ord('a')
        shifted = (ord(ch) - base + shift) % 26
        return chr(base + shifted)
    return ch


def caesar_encrypt(text, shift):
    # Encrypt text using Caesar cipher
    result = ""
    for ch in text:
        result = result + caesar_encrypt_char(ch, shift)
    return result


def caesar_decrypt(text, shift):
    # Decrypt Caesar cipher text
    return caesar_encrypt(text, -shift)


def rot13(text):
    # Apply ROT13 transformation
    return caesar_encrypt(text, 13)


def brute_force_caesar(ciphertext):
    # Try all 26 shifts and return all possibilities
    results = []
    for shift in range(26):
        decrypted = caesar_decrypt(ciphertext, shift)
        results = results + [(shift, decrypted)]
    return results


def caesar_frequency_score(text):
    # Score decrypted text by English letter frequency
    english_freq = [8.2, 1.5, 2.8, 4.3, 13.0, 2.2, 2.0, 6.1, 7.0,
                    0.15, 0.77, 4.0, 2.4, 6.7, 7.5, 1.9, 0.095,
                    6.0, 6.3, 9.1, 2.8, 0.98, 2.4, 0.15, 2.0, 0.074]
    score = 0.0
    for ch in text.lower():
        if ch.isalpha():
            idx = ord(ch) - ord('a')
            score = score + english_freq[idx]
    return score
""",
        "category": "pure",
    },

    "pure_012_luhn_checksum": {
        "source": """\
def luhn_checksum(number_str):
    # Compute the Luhn checksum of a number string
    digits = [int(d) for d in number_str if d.isdigit()]
    total = 0
    reverse_digits = digits[::-1]
    for i in range(len(reverse_digits)):
        d = reverse_digits[i]
        if i % 2 == 1:
            d = d * 2
            if d > 9:
                d = d - 9
        total = total + d
    return total % 10


def luhn_valid(number_str):
    # Check if a number passes the Luhn check
    return luhn_checksum(number_str) == 0


def generate_check_digit(partial):
    # Generate the check digit for a partial number
    check = luhn_checksum(partial + "0")
    if check == 0:
        return 0
    return 10 - check


def mask_card_number(card_number):
    # Mask all but last 4 digits of a card number
    clean = "".join(c for c in card_number if c.isdigit())
    if len(clean) <= 4:
        return clean
    masked = "*" * (len(clean) - 4) + clean[-4:]
    return masked


def format_card_number(card_number):
    # Format card number in groups of 4
    clean = "".join(c for c in card_number if c.isdigit())
    groups = []
    for i in range(0, len(clean), 4):
        groups = groups + [clean[i:i+4]]
    return " ".join(groups)
""",
        "category": "pure",
    },

    "pure_013_isbn_validator": {
        "source": """\
def isbn10_check_digit(isbn_str):
    # Compute ISBN-10 check digit
    digits = [int(c) for c in isbn_str[:9] if c.isdigit()]
    total = 0
    for i in range(9):
        total = total + digits[i] * (10 - i)
    remainder = total % 11
    check = 11 - remainder
    if check == 10:
        return 'X'
    if check == 11:
        return '0'
    return str(check)


def isbn10_valid(isbn_str):
    # Validate an ISBN-10 string
    clean = isbn_str.replace("-", "").replace(" ", "")
    if len(clean) != 10:
        return False
    total = 0
    for i in range(9):
        if not clean[i].isdigit():
            return False
        total = total + int(clean[i]) * (10 - i)
    last = clean[9]
    if last == 'X' or last == 'x':
        total = total + 10
    elif last.isdigit():
        total = total + int(last)
    else:
        return False
    return total % 11 == 0


def isbn13_check_digit(isbn_str):
    # Compute ISBN-13 check digit
    digits = [int(c) for c in isbn_str[:12] if c.isdigit()]
    total = 0
    for i in range(12):
        weight = 1 if i % 2 == 0 else 3
        total = total + digits[i] * weight
    check = (10 - (total % 10)) % 10
    return str(check)


def isbn13_valid(isbn_str):
    # Validate an ISBN-13 string
    clean = isbn_str.replace("-", "").replace(" ", "")
    if len(clean) != 13:
        return False
    total = 0
    for i in range(13):
        if not clean[i].isdigit():
            return False
        weight = 1 if i % 2 == 0 else 3
        total = total + int(clean[i]) * weight
    return total % 10 == 0
""",
        "category": "pure",
    },

    "pure_014_levenshtein": {
        "source": """\
def levenshtein_distance(s1, s2):
    # Compute Levenshtein edit distance between two strings
    m = len(s1)
    n = len(s2)
    prev_row = list(range(n + 1))
    for i in range(1, m + 1):
        curr_row = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr_row[j] = min(
                curr_row[j - 1] + 1,
                prev_row[j] + 1,
                prev_row[j - 1] + cost
            )
        prev_row = curr_row
    return prev_row[n]


def levenshtein_ratio(s1, s2):
    # Compute similarity ratio from Levenshtein distance
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    dist = levenshtein_distance(s1, s2)
    return 1.0 - dist / max_len


def hamming_distance(s1, s2):
    # Compute Hamming distance between equal-length strings
    if len(s1) != len(s2):
        return -1
    distance = 0
    for i in range(len(s1)):
        if s1[i] != s2[i]:
            distance = distance + 1
    return distance


def longest_common_prefix(strings):
    # Find longest common prefix of a list of strings
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix
""",
        "category": "pure",
    },

    "pure_015_longest_increasing": {
        "source": """\
def lis_length(arr):
    # Length of longest increasing subsequence using DP
    n = len(arr)
    if n == 0:
        return 0
    dp = [1] * n
    for i in range(1, n):
        for j in range(i):
            if arr[j] < arr[i]:
                if dp[j] + 1 > dp[i]:
                    dp[i] = dp[j] + 1
    return max(dp)


def lis_sequence(arr):
    # Reconstruct the actual LIS
    n = len(arr)
    if n == 0:
        return []
    dp = [1] * n
    parent = [-1] * n
    for i in range(1, n):
        for j in range(i):
            if arr[j] < arr[i] and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                parent[i] = j
    max_len = max(dp)
    max_idx = dp.index(max_len)
    result = []
    idx = max_idx
    while idx != -1:
        result = [arr[idx]] + result
        idx = parent[idx]
    return result


def lis_length_binary(arr):
    # O(n log n) LIS length using patience sorting
    tails = []
    for val in arr:
        lo = 0
        hi = len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < val:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails = tails + [val]
        else:
            tails[lo] = val
    return len(tails)
""",
        "category": "pure",
    },

    "pure_016_knapsack": {
        "source": """\
def knapsack_01(weights, values, capacity):
    # 0-1 knapsack problem via dynamic programming
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                candidate = dp[i - 1][w - weights[i - 1]] + values[i - 1]
                if candidate > dp[i][w]:
                    dp[i][w] = candidate
    return dp[n][capacity]


def knapsack_items(weights, values, capacity):
    # Return which items to include in optimal knapsack
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                candidate = dp[i - 1][w - weights[i - 1]] + values[i - 1]
                if candidate > dp[i][w]:
                    dp[i][w] = candidate
    items = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i - 1][w]:
            items = [i - 1] + items
            w = w - weights[i - 1]
    return items


def fractional_knapsack(weights, values, capacity):
    # Fractional knapsack (greedy)
    items = []
    for i in range(len(weights)):
        items = items + [(values[i] / weights[i], weights[i], values[i])]
    items = sorted(items, reverse=True)
    total_value = 0.0
    remaining = capacity
    for ratio, w, v in items:
        if remaining <= 0:
            break
        if w <= remaining:
            total_value = total_value + v
            remaining = remaining - w
        else:
            total_value = total_value + ratio * remaining
            remaining = 0
    return total_value
""",
        "category": "pure",
    },
    "pure_017_coin_change": {
        "source": """\
def coin_change_count(coins, amount):
    # Count number of ways to make change
    dp = [0] * (amount + 1)
    dp[0] = 1
    for coin in coins:
        for a in range(coin, amount + 1):
            dp[a] = dp[a] + dp[a - coin]
    return dp[amount]


def coin_change_min(coins, amount):
    # Minimum number of coins to make amount
    dp = [amount + 1] * (amount + 1)
    dp[0] = 0
    for a in range(1, amount + 1):
        for coin in coins:
            if coin <= a:
                candidate = dp[a - coin] + 1
                if candidate < dp[a]:
                    dp[a] = candidate
    if dp[amount] > amount:
        return -1
    return dp[amount]


def coin_change_coins_used(coins, amount):
    # Return actual coins used in minimum change
    dp = [amount + 1] * (amount + 1)
    dp[0] = 0
    last_coin = [-1] * (amount + 1)
    for a in range(1, amount + 1):
        for coin in coins:
            if coin <= a and dp[a - coin] + 1 < dp[a]:
                dp[a] = dp[a - coin] + 1
                last_coin[a] = coin
    if dp[amount] > amount:
        return []
    result = []
    remaining = amount
    while remaining > 0:
        result = result + [last_coin[remaining]]
        remaining = remaining - last_coin[remaining]
    return result
""",
        "category": "pure",
    },

    "pure_018_flood_fill": {
        "source": """\
def is_valid_cell(grid, row, col):
    # Check if cell coordinates are within grid bounds
    if row < 0 or row >= len(grid):
        return False
    if col < 0 or col >= len(grid[0]):
        return False
    return True


def flood_fill(grid, start_row, start_col, new_color):
    # Flood fill algorithm using iterative BFS
    rows = len(grid)
    cols = len(grid[0])
    original = grid[start_row][start_col]
    if original == new_color:
        return grid
    result = [list(row) for row in grid]
    queue = [(start_row, start_col)]
    result[start_row][start_col] = new_color
    while queue:
        r, c = queue[0]
        queue = queue[1:]
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr = r + dr
            nc = c + dc
            if is_valid_cell(result, nr, nc) and result[nr][nc] == original:
                result[nr][nc] = new_color
                queue = queue + [(nr, nc)]
    return result


def count_regions(grid):
    # Count distinct connected regions in a grid
    rows = len(grid)
    cols = len(grid[0])
    visited = [[False] * cols for _ in range(rows)]
    count = 0
    for r in range(rows):
        for c in range(cols):
            if not visited[r][c]:
                count = count + 1
                stack = [(r, c)]
                while stack:
                    cr, cc = stack[-1]
                    stack = stack[:-1]
                    if visited[cr][cc]:
                        continue
                    visited[cr][cc] = True
                    color = grid[cr][cc]
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr = cr + dr
                        nc = cc + dc
                        if is_valid_cell(grid, nr, nc) and not visited[nr][nc]:
                            if grid[nr][nc] == color:
                                stack = stack + [(nr, nc)]
    return count
""",
        "category": "pure",
    },

    "pure_019_convex_hull": {
        "source": """\
def cross_product(o, a, b):
    # Compute cross product of vectors OA and OB
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def distance_squared(a, b):
    # Squared Euclidean distance between two points
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def convex_hull(points):
    # Gift wrapping (Jarvis march) algorithm
    n = len(points)
    if n < 3:
        return list(points)
    leftmost = 0
    for i in range(1, n):
        if points[i][0] < points[leftmost][0]:
            leftmost = i
        elif points[i][0] == points[leftmost][0]:
            if points[i][1] < points[leftmost][1]:
                leftmost = i
    hull = []
    current = leftmost
    while True:
        hull = hull + [points[current]]
        candidate = 0
        for i in range(1, n):
            if candidate == current:
                candidate = i
                continue
            cp = cross_product(points[current], points[candidate], points[i])
            if cp < 0:
                candidate = i
            elif cp == 0:
                dist_cand = distance_squared(points[current], points[candidate])
                dist_i = distance_squared(points[current], points[i])
                if dist_i > dist_cand:
                    candidate = i
        current = candidate
        if current == leftmost:
            break
    return hull


def polygon_area(vertices):
    # Shoelace formula for polygon area
    n = len(vertices)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area = area + vertices[i][0] * vertices[j][1]
        area = area - vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0
""",
        "category": "pure",
    },

    "pure_020_topological_sort": {
        "source": """\
def build_in_degree(num_nodes, edges):
    # Compute in-degree for each node
    in_deg = [0] * num_nodes
    for src, dst in edges:
        in_deg[dst] = in_deg[dst] + 1
    return in_deg


def build_adjacency(num_nodes, edges):
    # Build adjacency list from edge list
    adj = [[] for _ in range(num_nodes)]
    for src, dst in edges:
        adj[src] = adj[src] + [dst]
    return adj


def topological_sort(num_nodes, edges):
    # Kahn's algorithm for topological sorting
    in_deg = build_in_degree(num_nodes, edges)
    adj = build_adjacency(num_nodes, edges)
    queue = []
    for i in range(num_nodes):
        if in_deg[i] == 0:
            queue = queue + [i]
    result = []
    while queue:
        node = queue[0]
        queue = queue[1:]
        result = result + [node]
        for neighbor in adj[node]:
            in_deg[neighbor] = in_deg[neighbor] - 1
            if in_deg[neighbor] == 0:
                queue = queue + [neighbor]
    if len(result) != num_nodes:
        return None
    return result


def has_cycle(num_nodes, edges):
    # Check if a directed graph has a cycle
    result = topological_sort(num_nodes, edges)
    return result is None


def all_topological_orders(num_nodes, edges):
    # Count the number of valid topological orderings
    in_deg = build_in_degree(num_nodes, edges)
    adj = build_adjacency(num_nodes, edges)
    visited = [False] * num_nodes
    count = [0]
    def backtrack(path_len):
        if path_len == num_nodes:
            count[0] = count[0] + 1
            return
        for i in range(num_nodes):
            if not visited[i] and in_deg[i] == 0:
                visited[i] = True
                for nb in adj[i]:
                    in_deg[nb] = in_deg[nb] - 1
                backtrack(path_len + 1)
                visited[i] = False
                for nb in adj[i]:
                    in_deg[nb] = in_deg[nb] + 1
    backtrack(0)
    return count[0]
""",
        "category": "pure",
    },

    "pure_021_huffman_freq": {
        "source": """\
def count_frequencies(text):
    # Count character frequencies in text
    freq = {}
    for ch in text:
        if ch in freq:
            freq[ch] = freq[ch] + 1
        else:
            freq[ch] = 1
    return freq


def build_sorted_freq_list(freq):
    # Build sorted list of (char, frequency) pairs
    items = []
    for ch, count in freq.items():
        items = items + [(ch, count)]
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            if items[j][1] < items[i][1]:
                items[i], items[j] = items[j], items[i]
    return items


def build_huffman_tree(freq_list):
    # Build Huffman tree as nested tuples
    nodes = [(f, c) for c, f in freq_list]
    while len(nodes) > 1:
        nodes = sorted(nodes, key=lambda x: x[0])
        left = nodes[0]
        right = nodes[1]
        merged = (left[0] + right[0], (left, right))
        nodes = [merged] + nodes[2:]
    return nodes[0] if nodes else None


def extract_codes(tree, prefix=""):
    # Extract Huffman codes from tree
    if tree is None:
        return {}
    freq, data = tree
    if isinstance(data, str):
        return {data: prefix or "0"}
    left, right = data
    codes = {}
    left_codes = extract_codes(left, prefix + "0")
    right_codes = extract_codes(right, prefix + "1")
    for k, v in left_codes.items():
        codes[k] = v
    for k, v in right_codes.items():
        codes[k] = v
    return codes


def huffman_encode_length(text):
    # Compute total encoded bit length for text
    freq = count_frequencies(text)
    freq_list = build_sorted_freq_list(freq)
    tree = build_huffman_tree(freq_list)
    codes = extract_codes(tree)
    total_bits = 0
    for ch in text:
        total_bits = total_bits + len(codes[ch])
    return total_bits
""",
        "category": "pure",
    },

    "pure_022_median_of_medians": {
        "source": """\
def chunks_of_five(arr):
    # Split array into chunks of 5
    result = []
    for i in range(0, len(arr), 5):
        result = result + [arr[i:i+5]]
    return result


def insertion_sort(arr):
    # Simple insertion sort for small arrays
    result = list(arr)
    for i in range(1, len(result)):
        key = result[i]
        j = i - 1
        while j >= 0 and result[j] > key:
            result[j + 1] = result[j]
            j = j - 1
        result[j + 1] = key
    return result


def get_median(arr):
    # Get median of a small sorted array
    sorted_arr = insertion_sort(arr)
    return sorted_arr[len(sorted_arr) // 2]


def median_of_medians(arr):
    # Select approximate median using median of medians
    if len(arr) <= 5:
        return get_median(arr)
    groups = chunks_of_five(arr)
    medians = []
    for group in groups:
        medians = medians + [get_median(group)]
    return median_of_medians(medians)


def partition_around(arr, pivot):
    # Partition array into elements < pivot, == pivot, > pivot
    less = []
    equal = []
    greater = []
    for x in arr:
        if x < pivot:
            less = less + [x]
        elif x == pivot:
            equal = equal + [x]
        else:
            greater = greater + [x]
    return less, equal, greater


def deterministic_select(arr, k):
    # Select kth smallest using median of medians
    if len(arr) <= 5:
        return insertion_sort(arr)[k]
    pivot = median_of_medians(arr)
    less, equal, greater = partition_around(arr, pivot)
    if k < len(less):
        return deterministic_select(less, k)
    elif k < len(less) + len(equal):
        return pivot
    else:
        return deterministic_select(greater, k - len(less) - len(equal))
""",
        "category": "pure",
    },

    "pure_023_kmp_table": {
        "source": """\
def build_kmp_table(pattern):
    # Build KMP failure function table
    m = len(pattern)
    table = [0] * m
    length = 0
    i = 1
    while i < m:
        if pattern[i] == pattern[length]:
            length = length + 1
            table[i] = length
            i = i + 1
        else:
            if length != 0:
                length = table[length - 1]
            else:
                table[i] = 0
                i = i + 1
    return table


def kmp_search(text, pattern):
    # Find first occurrence of pattern in text using KMP
    n = len(text)
    m = len(pattern)
    if m == 0:
        return 0
    table = build_kmp_table(pattern)
    i = 0
    j = 0
    while i < n:
        if text[i] == pattern[j]:
            i = i + 1
            j = j + 1
            if j == m:
                return i - j
        else:
            if j != 0:
                j = table[j - 1]
            else:
                i = i + 1
    return -1


def kmp_find_all(text, pattern):
    # Find all occurrences of pattern in text
    n = len(text)
    m = len(pattern)
    if m == 0:
        return []
    table = build_kmp_table(pattern)
    results = []
    i = 0
    j = 0
    while i < n:
        if text[i] == pattern[j]:
            i = i + 1
            j = j + 1
            if j == m:
                results = results + [i - j]
                j = table[j - 1]
        else:
            if j != 0:
                j = table[j - 1]
            else:
                i = i + 1
    return results
""",
        "category": "pure",
    },

    "pure_024_balanced_parens": {
        "source": """\
def is_balanced(s):
    # Check if parentheses/brackets/braces are balanced
    stack = []
    matching = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in '([{':
            stack = stack + [ch]
        elif ch in ')]}':
            if not stack:
                return False
            if stack[-1] != matching[ch]:
                return False
            stack = stack[:-1]
    return len(stack) == 0


def max_nesting_depth(s):
    # Find maximum nesting depth of parentheses
    max_depth = 0
    current = 0
    for ch in s:
        if ch == '(':
            current = current + 1
            if current > max_depth:
                max_depth = current
        elif ch == ')':
            current = current - 1
    return max_depth


def find_mismatch(s):
    # Find position of first mismatched bracket, or -1
    stack = []
    matching = {')': '(', ']': '[', '}': '{'}
    for i in range(len(s)):
        ch = s[i]
        if ch in '([{':
            stack = stack + [(ch, i)]
        elif ch in ')]}':
            if not stack:
                return i
            if stack[-1][0] != matching[ch]:
                return i
            stack = stack[:-1]
    if stack:
        return stack[0][1]
    return -1


def generate_balanced(n):
    # Generate all balanced parentheses strings of length 2n
    results = []
    def backtrack(current, open_count, close_count):
        if len(current) == 2 * n:
            results.append(current)
            return
        if open_count < n:
            backtrack(current + "(", open_count + 1, close_count)
        if close_count < open_count:
            backtrack(current + ")", open_count, close_count + 1)
    backtrack("", 0, 0)
    return results
""",
        "category": "pure",
    },

    "pure_025_roman_numeral": {
        "source": """\
def roman_to_int(s):
    # Convert Roman numeral string to integer
    values = {
        'I': 1, 'V': 5, 'X': 10, 'L': 50,
        'C': 100, 'D': 500, 'M': 1000
    }
    result = 0
    prev = 0
    for ch in reversed(s):
        curr = values.get(ch, 0)
        if curr < prev:
            result = result - curr
        else:
            result = result + curr
        prev = curr
    return result


def int_to_roman(num):
    # Convert integer to Roman numeral string
    mappings = [
        (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
        (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
        (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')
    ]
    result = ""
    for value, numeral in mappings:
        while num >= value:
            result = result + numeral
            num = num - value
    return result


def is_valid_roman(s):
    # Validate a Roman numeral string
    if not s:
        return False
    valid_chars = set('IVXLCDM')
    for ch in s:
        if ch not in valid_chars:
            return False
    converted = roman_to_int(s)
    if converted <= 0 or converted > 3999:
        return False
    reconverted = int_to_roman(converted)
    return reconverted == s


def roman_add(a, b):
    # Add two Roman numeral strings
    val_a = roman_to_int(a)
    val_b = roman_to_int(b)
    total = val_a + val_b
    return int_to_roman(total)
""",
        "category": "pure",
    },
    # ── exception handling (25) ───────────────────────────────────────────

    "exc_001_safe_json_parser": {
        "source": """\
class ParseError(Exception):
    def __init__(self, message, position):
        super().__init__(message)
        self.position = position


def safe_parse_int(text):
    try:
        return int(text.strip()), None
    except ValueError:
        return None, "not an integer"
    except AttributeError:
        raise ParseError("expected string input", 0)


def safe_parse_float(text):
    try:
        return float(text.strip()), None
    except ValueError:
        return None, "not a float"
    except AttributeError:
        raise ParseError("expected string input", 0)


def parse_json_value(text):
    stripped = text.strip()
    if stripped == "true":
        return True
    if stripped == "false":
        return False
    if stripped == "null":
        return None
    val, err = safe_parse_float(stripped)
    if err is None:
        return val
    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped[1:-1]
    raise ParseError("unrecognized value", 0)


def parse_key_value_pairs(text):
    result = {}
    pairs = text.split(",")
    for idx, pair in enumerate(pairs):
        parts = pair.split(":", 1)
        if len(parts) != 2:
            raise ParseError("missing colon in pair", idx)
        key = parts[0].strip().strip('"')
        value = parse_json_value(parts[1])
        result[key] = value
    return result
""",
        "category": "exception",
    },

    "exc_002_config_loader": {
        "source": """\
class ConfigError(Exception):
    def __init__(self, key, reason):
        super().__init__(f"Config error for '{key}': {reason}")
        self.key = key
        self.reason = reason


def load_config_value(config, key, expected_type):
    try:
        value = config[key]
    except KeyError:
        raise ConfigError(key, "missing key")
    if not isinstance(value, expected_type):
        raise ConfigError(key, f"expected {expected_type.__name__}")
    return value


def load_config_with_default(config, key, default, expected_type):
    try:
        value = load_config_value(config, key, expected_type)
        return value
    except ConfigError:
        return default


def load_config_chain(configs, key, expected_type):
    # Try loading from multiple config sources in order
    errors = []
    for i, config in enumerate(configs):
        try:
            return load_config_value(config, key, expected_type)
        except ConfigError as e:
            errors.append((i, str(e)))
    raise ConfigError(key, f"not found in any of {len(configs)} sources")


def validate_config(config, schema):
    # schema maps key -> expected_type
    errors = []
    for key, expected_type in schema.items():
        try:
            load_config_value(config, key, expected_type)
        except ConfigError as e:
            errors.append(str(e))
    if errors:
        raise ConfigError("_schema", "; ".join(errors))
    return True
""",
        "category": "exception",
    },

    "exc_003_db_retry": {
        "source": """\
class ConnectionError(Exception):
    def __init__(self, host, attempt):
        super().__init__(f"Failed to connect to {host}")
        self.host = host
        self.attempt = attempt


class TimeoutError(Exception):
    def __init__(self, operation, elapsed):
        super().__init__(f"Timeout during {operation}")
        self.operation = operation
        self.elapsed = elapsed


def simulate_connect(host, fail_count, attempt):
    if attempt <= fail_count:
        raise ConnectionError(host, attempt)
    return {"host": host, "status": "connected", "attempt": attempt}


def retry_connect(host, max_retries, fail_count):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            conn = simulate_connect(host, fail_count, attempt)
            return conn
        except ConnectionError as e:
            last_error = e
            wait_time = 2 ** attempt
            continue
    raise last_error


def connect_with_timeout(host, max_retries, fail_count, timeout):
    elapsed = 0
    for attempt in range(1, max_retries + 1):
        try:
            conn = simulate_connect(host, fail_count, attempt)
            return conn
        except ConnectionError:
            elapsed = elapsed + 2 ** attempt
            if elapsed > timeout:
                raise TimeoutError("connect", elapsed)
    raise ConnectionError(host, max_retries)
""",
        "category": "exception",
    },

    "exc_004_input_validator": {
        "source": """\
class ValidationError(Exception):
    def __init__(self, field, message):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def validate_non_empty(field, value):
    if not value or not value.strip():
        raise ValidationError(field, "must not be empty")
    return value.strip()


def validate_int_range(field, value, min_val, max_val):
    try:
        num = int(value)
    except (ValueError, TypeError):
        raise ValidationError(field, "must be an integer")
    if num < min_val or num > max_val:
        raise ValidationError(field, f"must be between {min_val} and {max_val}")
    return num


def validate_email_format(field, value):
    stripped = validate_non_empty(field, value)
    at_pos = stripped.find("@")
    if at_pos < 1:
        raise ValidationError(field, "missing @ symbol")
    domain = stripped[at_pos + 1:]
    if "." not in domain:
        raise ValidationError(field, "invalid domain")
    if domain.startswith(".") or domain.endswith("."):
        raise ValidationError(field, "invalid domain format")
    return stripped


def validate_form(data, rules):
    errors = []
    cleaned = {}
    for field, rule_type, *args in rules:
        try:
            value = data.get(field, "")
            if rule_type == "non_empty":
                cleaned[field] = validate_non_empty(field, value)
            elif rule_type == "int_range":
                cleaned[field] = validate_int_range(field, value, *args)
            elif rule_type == "email":
                cleaned[field] = validate_email_format(field, value)
        except ValidationError as e:
            errors.append(str(e))
    if errors:
        raise ValidationError("_form", "; ".join(errors))
    return cleaned
""",
        "category": "exception",
    },

    "exc_005_safe_coercion": {
        "source": """\
class CoercionError(Exception):
    def __init__(self, value, target_type):
        super().__init__(f"Cannot coerce {repr(value)} to {target_type}")
        self.value = value
        self.target_type = target_type


def safe_to_int(value):
    try:
        if isinstance(value, bool):
            raise CoercionError(value, "int")
        return int(value)
    except (ValueError, TypeError):
        raise CoercionError(value, "int")


def safe_to_float(value):
    try:
        if isinstance(value, bool):
            raise CoercionError(value, "float")
        return float(value)
    except (ValueError, TypeError):
        raise CoercionError(value, "float")


def safe_to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.lower().strip()
        if lower in ("true", "1", "yes", "on"):
            return True
        if lower in ("false", "0", "no", "off"):
            return False
    try:
        return bool(int(value))
    except (ValueError, TypeError):
        raise CoercionError(value, "bool")


def coerce_record(record, schema):
    # schema: dict of field_name -> target_type_name
    result = {}
    errors = []
    coercers = {"int": safe_to_int, "float": safe_to_float, "bool": safe_to_bool}
    for field, type_name in schema.items():
        try:
            raw = record.get(field)
            if raw is None:
                raise CoercionError(raw, type_name)
            coercer = coercers.get(type_name)
            if coercer is None:
                raise CoercionError(raw, type_name)
            result[field] = coercer(raw)
        except CoercionError as e:
            errors.append(str(e))
    if errors:
        raise CoercionError(record, "record: " + "; ".join(errors))
    return result
""",
        "category": "exception",
    },

    "exc_006_http_status": {
        "source": """\
class HttpError(Exception):
    def __init__(self, status_code, message):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class NotFoundError(HttpError):
    def __init__(self, resource):
        super().__init__(404, f"{resource} not found")
        self.resource = resource


class UnauthorizedError(HttpError):
    def __init__(self):
        super().__init__(401, "Authentication required")


class RateLimitError(HttpError):
    def __init__(self, retry_after):
        super().__init__(429, f"Rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def handle_response(status_code, body, resource):
    if status_code == 200:
        return body
    elif status_code == 404:
        raise NotFoundError(resource)
    elif status_code == 401:
        raise UnauthorizedError()
    elif status_code == 429:
        retry = body.get("retry_after", 60) if isinstance(body, dict) else 60
        raise RateLimitError(retry)
    else:
        raise HttpError(status_code, "unexpected status")


def safe_request(status_code, body, resource, default=None):
    try:
        return handle_response(status_code, body, resource)
    except NotFoundError:
        return default
    except RateLimitError as e:
        return {"error": "rate_limited", "retry_after": e.retry_after}
    except HttpError as e:
        return {"error": e.message, "status": e.status_code}
""",
        "category": "exception",
    },

    "exc_007_arithmetic_eval": {
        "source": """\
class EvalError(Exception):
    def __init__(self, expression, reason):
        super().__init__(f"Error evaluating '{expression}': {reason}")
        self.expression = expression
        self.reason = reason


def safe_divide(a, b, expr=""):
    try:
        if b == 0:
            raise ZeroDivisionError()
        return a / b
    except ZeroDivisionError:
        raise EvalError(expr, "division by zero")


def safe_power(base, exp, expr=""):
    try:
        if exp < 0 and base == 0:
            raise EvalError(expr, "zero to negative power")
        result = base ** exp
        if isinstance(result, complex):
            raise EvalError(expr, "complex result")
        return result
    except OverflowError:
        raise EvalError(expr, "overflow")


def evaluate_rpn(tokens):
    # Evaluate reverse Polish notation expression
    stack = []
    for token in tokens:
        if token in ("+", "-", "*", "/", "**"):
            if len(stack) < 2:
                raise EvalError(" ".join(tokens), "insufficient operands")
            b = stack[-1]
            a = stack[-2]
            stack = stack[:-2]
            if token == "+":
                stack = stack + [a + b]
            elif token == "-":
                stack = stack + [a - b]
            elif token == "*":
                stack = stack + [a * b]
            elif token == "/":
                stack = stack + [safe_divide(a, b, " ".join(tokens))]
            elif token == "**":
                stack = stack + [safe_power(a, b, " ".join(tokens))]
        else:
            try:
                stack = stack + [float(token)]
            except ValueError:
                raise EvalError(" ".join(tokens), f"invalid token: {token}")
    if len(stack) != 1:
        raise EvalError(" ".join(tokens), "invalid expression")
    return stack[0]
""",
        "category": "exception",
    },

    "exc_008_resource_cleanup": {
        "source": """\
class ResourceError(Exception):
    def __init__(self, resource_name, action):
        super().__init__(f"Resource '{resource_name}' failed during {action}")
        self.resource_name = resource_name
        self.action = action


class ResourceHandle:
    def __init__(self, name, should_fail_close=False):
        self.name = name
        self.opened = False
        self.should_fail_close = should_fail_close


def open_resource(handle):
    try:
        handle.opened = True
        return handle
    except Exception:
        raise ResourceError(handle.name, "open")


def close_resource(handle):
    try:
        if handle.should_fail_close:
            raise ResourceError(handle.name, "close")
        handle.opened = False
    except ResourceError:
        raise
    except Exception:
        raise ResourceError(handle.name, "close")


def use_resource(handle, operation):
    try:
        if not handle.opened:
            raise ResourceError(handle.name, "use: not open")
        return operation(handle)
    except ResourceError:
        raise
    except Exception as e:
        raise ResourceError(handle.name, f"use: {e}")


def safe_resource_pipeline(names, operation):
    opened = []
    results = []
    errors = []
    for name in names:
        handle = ResourceHandle(name)
        try:
            open_resource(handle)
            opened.append(handle)
            result = use_resource(handle, operation)
            results.append((name, result))
        except ResourceError as e:
            errors.append(str(e))
    for handle in reversed(opened):
        try:
            close_resource(handle)
        except ResourceError as e:
            errors.append(str(e))
    if errors:
        raise ResourceError("_pipeline", "; ".join(errors))
    return results
""",
        "category": "exception",
    },
    "exc_009_nested_exception": {
        "source": """\
class LayerError(Exception):
    def __init__(self, layer, cause):
        super().__init__(f"Error at layer {layer}")
        self.layer = layer
        self.cause = cause


def layer_three(value):
    if value < 0:
        raise ValueError("negative value")
    if value == 0:
        raise ZeroDivisionError("zero value")
    return 100 // value


def layer_two(value):
    try:
        result = layer_three(value)
        return result * 2
    except ValueError as e:
        raise LayerError(2, e)
    except ZeroDivisionError as e:
        raise LayerError(2, e)


def layer_one(value):
    try:
        result = layer_two(value)
        return result + 10
    except LayerError as e:
        if isinstance(e.cause, ValueError):
            return -1
        raise LayerError(1, e)


def process_values(values):
    results = []
    errors = []
    for i, v in enumerate(values):
        try:
            result = layer_one(v)
            results.append((i, result))
        except LayerError as e:
            errors.append((i, e.layer, str(e.cause)))
    return results, errors


def unwrap_nested(error):
    chain = []
    current = error
    while hasattr(current, 'cause') and current.cause is not None:
        chain.append((current.layer, str(current)))
        current = current.cause
    chain.append((-1, str(current)))
    return chain
""",
        "category": "exception",
    },

    "exc_010_batch_processor": {
        "source": """\
class BatchError(Exception):
    def __init__(self, item_index, reason):
        super().__init__(f"Item {item_index}: {reason}")
        self.item_index = item_index
        self.reason = reason


def process_item(item, index):
    if not isinstance(item, dict):
        raise BatchError(index, "expected dict")
    if "value" not in item:
        raise BatchError(index, "missing 'value' key")
    try:
        num = float(item["value"])
    except (ValueError, TypeError):
        raise BatchError(index, "value not numeric")
    if num < 0:
        raise BatchError(index, "negative value")
    return num * 2


def batch_process(items, stop_on_error=False):
    results = []
    errors = []
    for i, item in enumerate(items):
        try:
            result = process_item(item, i)
            results.append({"index": i, "result": result})
        except BatchError as e:
            errors.append({"index": i, "error": str(e)})
            if stop_on_error:
                break
    return {"results": results, "errors": errors}


def batch_process_chunked(items, chunk_size):
    all_results = []
    all_errors = []
    for start in range(0, len(items), chunk_size):
        chunk = items[start:start + chunk_size]
        try:
            outcome = batch_process(chunk)
            all_results.extend(outcome["results"])
            all_errors.extend(outcome["errors"])
        except Exception as e:
            all_errors.append({"index": start, "error": f"chunk failed: {e}"})
    return {"results": all_results, "errors": all_errors, "total": len(items)}
""",
        "category": "exception",
    },

    "exc_011_xml_validator": {
        "source": """\
class XmlError(Exception):
    def __init__(self, message, line):
        super().__init__(f"Line {line}: {message}")
        self.line = line


def extract_tag_name(tag_str):
    # Extract tag name from <tag> or </tag> or <tag attr="val">
    cleaned = tag_str.strip()
    if cleaned.startswith("</"):
        name = cleaned[2:].rstrip(">").strip()
    elif cleaned.startswith("<"):
        name = cleaned[1:].rstrip(">").split()[0]
    else:
        raise ValueError("not a tag")
    return name


def validate_xml_tags(text):
    stack = []
    lines = text.split("\\n")
    for line_num, line in enumerate(lines, 1):
        pos = 0
        while pos < len(line):
            start = line.find("<", pos)
            if start == -1:
                break
            end = line.find(">", start)
            if end == -1:
                raise XmlError("unclosed angle bracket", line_num)
            tag = line[start:end + 1]
            pos = end + 1
            if tag.startswith("<?") or tag.startswith("<!"):
                continue
            if tag.endswith("/>"):
                continue
            try:
                name = extract_tag_name(tag)
            except ValueError:
                raise XmlError(f"invalid tag: {tag}", line_num)
            if tag.startswith("</"):
                if not stack:
                    raise XmlError(f"unexpected closing tag: {name}", line_num)
                expected = stack[-1]
                stack = stack[:-1]
                if expected != name:
                    raise XmlError(
                        f"mismatched tag: expected </{expected}>, got </{name}>",
                        line_num
                    )
            else:
                stack.append(name)
    if stack:
        raise XmlError(f"unclosed tags: {', '.join(stack)}", len(lines))
    return True
""",
        "category": "exception",
    },

    "exc_012_schema_validation": {
        "source": """\
class SchemaError(Exception):
    def __init__(self, path, message):
        super().__init__(f"At {path}: {message}")
        self.path = path


def validate_type(value, expected, path):
    type_map = {"string": str, "integer": int, "float": float,
                "boolean": bool, "list": list, "dict": dict}
    expected_type = type_map.get(expected)
    if expected_type is None:
        raise SchemaError(path, f"unknown type: {expected}")
    if not isinstance(value, expected_type):
        actual = type(value).__name__
        raise SchemaError(path, f"expected {expected}, got {actual}")


def validate_required(data, required_keys, path):
    missing = []
    for key in required_keys:
        if key not in data:
            missing.append(key)
    if missing:
        raise SchemaError(path, f"missing required keys: {', '.join(missing)}")


def validate_schema(data, schema, path="root"):
    if "type" in schema:
        validate_type(data, schema["type"], path)
    if "required" in schema and isinstance(data, dict):
        validate_required(data, schema["required"], path)
    if "properties" in schema and isinstance(data, dict):
        for key, sub_schema in schema["properties"].items():
            if key in data:
                try:
                    validate_schema(data[key], sub_schema, f"{path}.{key}")
                except SchemaError:
                    raise
    if "items" in schema and isinstance(data, list):
        for i, item in enumerate(data):
            try:
                validate_schema(item, schema["items"], f"{path}[{i}]")
            except SchemaError:
                raise
    if "min_length" in schema:
        if isinstance(data, (str, list)) and len(data) < schema["min_length"]:
            raise SchemaError(path, f"length {len(data)} < {schema['min_length']}")
    return True
""",
        "category": "exception",
    },

    "exc_013_safe_indexing": {
        "source": """\
class IndexError_(Exception):
    def __init__(self, container_type, index, length):
        super().__init__(
            f"{container_type} index {index} out of range (length {length})"
        )
        self.container_type = container_type
        self.index = index
        self.length = length


def safe_list_get(lst, index, default=None):
    try:
        if index < 0:
            index = len(lst) + index
        if index < 0 or index >= len(lst):
            raise IndexError_(  "list", index, len(lst))
        return lst[index]
    except IndexError_:
        return default


def safe_dict_get(d, key_path, default=None):
    # key_path is dot-separated, e.g. "a.b.c"
    keys = key_path.split(".")
    current = d
    for key in keys:
        try:
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, list):
                idx = int(key)
                current = current[idx]
            else:
                return default
        except (KeyError, IndexError, ValueError):
            return default
    return current


def safe_slice(lst, start, end, step=1):
    try:
        n = len(lst)
        if start < -n or start >= n:
            raise IndexError_("list", start, n)
        if end < -n or end > n:
            raise IndexError_("list", end, n)
        return lst[start:end:step]
    except IndexError_:
        return []


def multi_index(data, indices):
    results = []
    errors = []
    for idx in indices:
        try:
            result = safe_list_get(data, idx)
            if result is None:
                raise IndexError_("list", idx, len(data))
            results.append((idx, result))
        except IndexError_ as e:
            errors.append((idx, str(e)))
    return results, errors
""",
        "category": "exception",
    },

    "exc_014_division_chain": {
        "source": """\
class DivisionChainError(Exception):
    def __init__(self, step, numerator, denominator):
        super().__init__(f"Step {step}: {numerator}/{denominator} failed")
        self.step = step


def safe_divide_step(num, den, step):
    if den == 0:
        raise DivisionChainError(step, num, den)
    return num / den


def chain_divide(initial, divisors):
    result = initial
    for i, d in enumerate(divisors):
        try:
            result = safe_divide_step(result, d, i)
        except DivisionChainError:
            raise
    return result


def chain_divide_with_recovery(initial, divisors, recovery_value):
    result = initial
    recovered = []
    for i, d in enumerate(divisors):
        try:
            result = safe_divide_step(result, d, i)
        except DivisionChainError:
            recovered.append(i)
            result = recovery_value
    return result, recovered


def parallel_chains(initials, divisor_lists):
    results = {}
    errors = {}
    for i, (initial, divisors) in enumerate(zip(initials, divisor_lists)):
        try:
            results[i] = chain_divide(initial, divisors)
        except DivisionChainError as e:
            errors[i] = {"step": e.step, "message": str(e)}
    return results, errors


def accumulate_divisions(values):
    if len(values) < 2:
        raise DivisionChainError(0, 0, 0)
    result = values[0]
    steps = []
    for i in range(1, len(values)):
        try:
            result = safe_divide_step(result, values[i], i)
            steps.append({"step": i, "result": result})
        except DivisionChainError as e:
            steps.append({"step": i, "error": str(e)})
            result = 0
    return steps
""",
        "category": "exception",
    },

    "exc_015_path_resolver": {
        "source": """\
class PathError(Exception):
    def __init__(self, path, reason):
        super().__init__(f"Path error '{path}': {reason}")
        self.path = path
        self.reason = reason


def normalize_path(path):
    if not path:
        raise PathError(path, "empty path")
    parts = path.replace("\\\\", "/").split("/")
    normalized = []
    for part in parts:
        if part == "." or part == "":
            continue
        elif part == "..":
            if not normalized:
                raise PathError(path, "cannot go above root")
            normalized = normalized[:-1]
        else:
            normalized.append(part)
    return "/".join(normalized)


def resolve_relative(base, relative):
    try:
        if relative.startswith("/"):
            return normalize_path(relative)
        combined = base.rstrip("/") + "/" + relative
        return normalize_path(combined)
    except PathError:
        raise PathError(relative, f"cannot resolve against {base}")


def validate_path_chars(path):
    invalid_chars = '<>"|?*'
    for i, ch in enumerate(path):
        if ch in invalid_chars:
            raise PathError(path, f"invalid character '{ch}' at position {i}")
    if ".." in path.split("/"):
        pass
    return True


def find_common_root(paths):
    if not paths:
        raise PathError("", "no paths provided")
    try:
        normalized = [normalize_path(p) for p in paths]
    except PathError:
        raise
    split_paths = [p.split("/") for p in normalized]
    common = []
    for parts in zip(*split_paths):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break
    if not common:
        raise PathError(str(paths), "no common root")
    return "/".join(common)
""",
        "category": "exception",
    },

    "exc_016_timeout_handler": {
        "source": """\
class OperationTimeout(Exception):
    def __init__(self, operation, elapsed, limit):
        super().__init__(f"{operation} timed out ({elapsed}s > {limit}s)")
        self.operation = operation
        self.elapsed = elapsed
        self.limit = limit


class RetryExhausted(Exception):
    def __init__(self, operation, attempts):
        super().__init__(f"{operation} failed after {attempts} attempts")
        self.attempts = attempts


def simulate_operation(name, cost, budget):
    if cost > budget:
        raise OperationTimeout(name, cost, budget)
    return {"name": name, "elapsed": cost}


def run_with_timeout(operations, total_budget):
    results = []
    remaining = total_budget
    for name, cost in operations:
        try:
            result = simulate_operation(name, cost, remaining)
            results.append(result)
            remaining = remaining - cost
        except OperationTimeout:
            raise OperationTimeout(name, total_budget - remaining + cost, total_budget)
    return results


def run_with_retry(operation_name, costs_per_attempt, timeout):
    for attempt, cost in enumerate(costs_per_attempt, 1):
        try:
            result = simulate_operation(operation_name, cost, timeout)
            return result
        except OperationTimeout:
            if attempt == len(costs_per_attempt):
                raise RetryExhausted(operation_name, attempt)
    raise RetryExhausted(operation_name, len(costs_per_attempt))


def run_pipeline_with_fallback(primary_ops, fallback_ops, budget):
    try:
        return run_with_timeout(primary_ops, budget)
    except OperationTimeout:
        try:
            return run_with_timeout(fallback_ops, budget)
        except OperationTimeout as e:
            raise OperationTimeout("pipeline_fallback", e.elapsed, budget)
""",
        "category": "exception",
    },

    "exc_017_permission_checker": {
        "source": """\
class PermissionDenied(Exception):
    def __init__(self, user, action, resource):
        super().__init__(f"User '{user}' cannot {action} on '{resource}'")
        self.user = user
        self.action = action
        self.resource = resource


class InvalidRole(Exception):
    def __init__(self, role):
        super().__init__(f"Unknown role: {role}")
        self.role = role


ROLE_PERMISSIONS = {
    "admin": {"read", "write", "delete", "manage"},
    "editor": {"read", "write"},
    "viewer": {"read"},
    "guest": set(),
}


def get_permissions(role):
    if role not in ROLE_PERMISSIONS:
        raise InvalidRole(role)
    return ROLE_PERMISSIONS[role]


def check_permission(user, role, action, resource):
    try:
        perms = get_permissions(role)
    except InvalidRole:
        raise PermissionDenied(user, action, resource)
    if action not in perms:
        raise PermissionDenied(user, action, resource)
    return True


def check_any_permission(user, roles, action, resource):
    errors = []
    for role in roles:
        try:
            check_permission(user, role, action, resource)
            return True
        except PermissionDenied as e:
            errors.append(str(e))
    raise PermissionDenied(user, action, resource)


def audit_access(requests):
    # requests: list of (user, role, action, resource) tuples
    allowed = []
    denied = []
    for user, role, action, resource in requests:
        try:
            check_permission(user, role, action, resource)
            allowed.append((user, action, resource))
        except (PermissionDenied, InvalidRole) as e:
            denied.append((user, action, resource, str(e)))
    return {"allowed": allowed, "denied": denied}
""",
        "category": "exception",
    },
    "exc_018_conversion_pipeline": {
        "source": """\
class ConversionError(Exception):
    def __init__(self, stage, value, reason):
        super().__init__(f"Stage '{stage}': cannot convert {repr(value)} - {reason}")
        self.stage = stage
        self.value = value


def convert_temperature(value, from_unit, to_unit):
    try:
        v = float(value)
    except (ValueError, TypeError):
        raise ConversionError("temperature", value, "not numeric")
    if from_unit == "C" and to_unit == "F":
        return v * 9.0 / 5.0 + 32.0
    elif from_unit == "F" and to_unit == "C":
        return (v - 32.0) * 5.0 / 9.0
    elif from_unit == "C" and to_unit == "K":
        result = v + 273.15
        if result < 0:
            raise ConversionError("temperature", value, "below absolute zero")
        return result
    elif from_unit == to_unit:
        return v
    raise ConversionError("temperature", value, f"unknown: {from_unit}->{to_unit}")


def convert_distance(value, from_unit, to_unit):
    try:
        v = float(value)
    except (ValueError, TypeError):
        raise ConversionError("distance", value, "not numeric")
    to_meters = {"m": 1.0, "km": 1000.0, "mi": 1609.34, "ft": 0.3048}
    if from_unit not in to_meters or to_unit not in to_meters:
        raise ConversionError("distance", value, "unknown unit")
    meters = v * to_meters[from_unit]
    return meters / to_meters[to_unit]


def pipeline_convert(records, conversions):
    results = []
    errors = []
    converters = {"temperature": convert_temperature, "distance": convert_distance}
    for i, (record, conv) in enumerate(zip(records, conversions)):
        try:
            kind = conv["kind"]
            converter = converters.get(kind)
            if converter is None:
                raise ConversionError(kind, record, "unknown kind")
            result = converter(record, conv["from"], conv["to"])
            results.append({"index": i, "result": result})
        except ConversionError as e:
            errors.append({"index": i, "error": str(e)})
    return results, errors
""",
        "category": "exception",
    },

    "exc_019_recursive_parser": {
        "source": """\
class ParseError(Exception):
    def __init__(self, message, position):
        super().__init__(f"Parse error at {position}: {message}")
        self.position = position


def parse_number(tokens, pos):
    if pos >= len(tokens):
        raise ParseError("unexpected end of input", pos)
    token = tokens[pos]
    try:
        return float(token), pos + 1
    except ValueError:
        raise ParseError(f"expected number, got '{token}'", pos)


def parse_atom(tokens, pos):
    if pos >= len(tokens):
        raise ParseError("unexpected end of input", pos)
    if tokens[pos] == "(":
        value, new_pos = parse_expression(tokens, pos + 1)
        if new_pos >= len(tokens) or tokens[new_pos] != ")":
            raise ParseError("expected closing parenthesis", new_pos)
        return value, new_pos + 1
    return parse_number(tokens, pos)


def parse_term(tokens, pos):
    left, pos = parse_atom(tokens, pos)
    while pos < len(tokens) and tokens[pos] in ("*", "/"):
        op = tokens[pos]
        right, pos = parse_atom(tokens, pos + 1)
        if op == "*":
            left = left * right
        elif op == "/":
            if right == 0:
                raise ParseError("division by zero", pos - 1)
            left = left / right
    return left, pos


def parse_expression(tokens, pos):
    left, pos = parse_term(tokens, pos)
    while pos < len(tokens) and tokens[pos] in ("+", "-"):
        op = tokens[pos]
        right, pos = parse_term(tokens, pos + 1)
        if op == "+":
            left = left + right
        else:
            left = left - right
    return left, pos


def evaluate_expression(text):
    tokens = text.replace("(", " ( ").replace(")", " ) ").split()
    try:
        result, pos = parse_expression(tokens, 0)
        if pos != len(tokens):
            raise ParseError("unexpected tokens", pos)
        return result
    except ParseError:
        raise
""",
        "category": "exception",
    },

    "exc_020_connection_pool": {
        "source": """\
class PoolExhausted(Exception):
    def __init__(self, pool_size, waiting):
        super().__init__(f"Pool exhausted: {pool_size} connections, {waiting} waiting")
        self.pool_size = pool_size
        self.waiting = waiting


class ConnectionFailed(Exception):
    def __init__(self, host, port, reason):
        super().__init__(f"Cannot connect to {host}:{port} - {reason}")
        self.host = host
        self.port = port


def create_connection(host, port, healthy_ports):
    if port not in healthy_ports:
        raise ConnectionFailed(host, port, "port not available")
    return {"host": host, "port": port, "status": "active"}


def get_from_pool(pool, max_size):
    available = [c for c in pool if c["status"] == "idle"]
    if available:
        conn = available[0]
        conn["status"] = "active"
        return conn
    if len(pool) >= max_size:
        raise PoolExhausted(max_size, 0)
    return None


def acquire_connection(pool, max_size, host, port, healthy_ports):
    try:
        existing = get_from_pool(pool, max_size)
        if existing is not None:
            return existing
        conn = create_connection(host, port, healthy_ports)
        pool.append(conn)
        return conn
    except PoolExhausted:
        raise
    except ConnectionFailed:
        raise


def release_connection(pool, conn):
    for c in pool:
        if c is conn:
            c["status"] = "idle"
            return
    raise ConnectionFailed(conn.get("host", "?"), conn.get("port", 0), "not in pool")


def pool_health_check(pool, healthy_ports):
    healthy = []
    failed = []
    for conn in pool:
        try:
            if conn["port"] not in healthy_ports:
                raise ConnectionFailed(conn["host"], conn["port"], "unhealthy")
            healthy.append(conn)
        except ConnectionFailed as e:
            failed.append({"conn": conn, "error": str(e)})
    return healthy, failed
""",
        "category": "exception",
    },

    "exc_021_rate_limiter": {
        "source": """\
class RateLimitExceeded(Exception):
    def __init__(self, client_id, limit, window):
        super().__init__(f"Client {client_id} exceeded {limit} requests in {window}s")
        self.client_id = client_id
        self.limit = limit


class QuotaExhausted(Exception):
    def __init__(self, client_id, quota_type):
        super().__init__(f"Client {client_id}: {quota_type} quota exhausted")
        self.client_id = client_id


def check_rate(request_counts, client_id, limit):
    count = request_counts.get(client_id, 0)
    if count >= limit:
        raise RateLimitExceeded(client_id, limit, 60)
    return count + 1


def check_quota(quotas, client_id, quota_type):
    client_quotas = quotas.get(client_id, {})
    remaining = client_quotas.get(quota_type, 0)
    if remaining <= 0:
        raise QuotaExhausted(client_id, quota_type)
    return remaining - 1


def process_request(request_counts, quotas, client_id, quota_type, limit):
    try:
        new_count = check_rate(request_counts, client_id, limit)
        request_counts[client_id] = new_count
    except RateLimitExceeded:
        raise
    try:
        new_remaining = check_quota(quotas, client_id, quota_type)
        if client_id not in quotas:
            quotas[client_id] = {}
        quotas[client_id][quota_type] = new_remaining
    except QuotaExhausted:
        raise
    return {"client": client_id, "count": new_count, "remaining": new_remaining}


def batch_check_rates(request_counts, client_ids, limit):
    allowed = []
    denied = []
    for cid in client_ids:
        try:
            new_count = check_rate(request_counts, cid, limit)
            request_counts[cid] = new_count
            allowed.append(cid)
        except RateLimitExceeded as e:
            denied.append({"client": cid, "error": str(e)})
    return allowed, denied
""",
        "category": "exception",
    },

    "exc_022_command_dispatcher": {
        "source": """\
class UnknownCommand(Exception):
    def __init__(self, command):
        super().__init__(f"Unknown command: {command}")
        self.command = command


class InvalidArguments(Exception):
    def __init__(self, command, expected, got):
        super().__init__(f"{command}: expected {expected} args, got {got}")
        self.command = command


def cmd_add(args):
    if len(args) != 2:
        raise InvalidArguments("add", 2, len(args))
    try:
        return float(args[0]) + float(args[1])
    except ValueError:
        raise InvalidArguments("add", "numeric", "non-numeric")


def cmd_concat(args):
    if len(args) < 1:
        raise InvalidArguments("concat", "1+", len(args))
    return " ".join(str(a) for a in args)


def cmd_repeat(args):
    if len(args) != 2:
        raise InvalidArguments("repeat", 2, len(args))
    try:
        count = int(args[1])
    except ValueError:
        raise InvalidArguments("repeat", "int", args[1])
    return str(args[0]) * count


COMMANDS = {"add": cmd_add, "concat": cmd_concat, "repeat": cmd_repeat}


def dispatch(command, args):
    handler = COMMANDS.get(command)
    if handler is None:
        raise UnknownCommand(command)
    try:
        return handler(args)
    except (InvalidArguments, UnknownCommand):
        raise
    except Exception as e:
        raise InvalidArguments(command, "valid", str(e))


def dispatch_batch(command_list):
    results = []
    errors = []
    for cmd, args in command_list:
        try:
            result = dispatch(cmd, args)
            results.append({"command": cmd, "result": result})
        except (UnknownCommand, InvalidArguments) as e:
            errors.append({"command": cmd, "error": str(e)})
    return results, errors
""",
        "category": "exception",
    },

    "exc_023_circular_import": {
        "source": """\
class CircularDependency(Exception):
    def __init__(self, chain):
        super().__init__(f"Circular dependency: {' -> '.join(chain)}")
        self.chain = chain


class MissingModule(Exception):
    def __init__(self, module):
        super().__init__(f"Module not found: {module}")
        self.module = module


def check_circular(deps, start, visited, path):
    if start in visited:
        cycle_start = path.index(start)
        raise CircularDependency(path[cycle_start:] + [start])
    visited_new = set(visited)
    visited_new.add(start)
    path_new = path + [start]
    for dep in deps.get(start, []):
        if dep not in deps and dep not in visited_new:
            continue
        check_circular(deps, dep, visited_new, path_new)


def resolve_order(deps):
    resolved = []
    visited = set()
    in_progress = set()
    def visit(module):
        if module in resolved:
            return
        if module in in_progress:
            raise CircularDependency([module])
        in_progress.add(module)
        for dep in deps.get(module, []):
            if dep not in deps:
                raise MissingModule(dep)
            visit(dep)
        in_progress.discard(module)
        visited.add(module)
        resolved.append(module)
    for module in deps:
        try:
            visit(module)
        except (CircularDependency, MissingModule):
            raise
    return resolved


def find_all_cycles(deps):
    cycles = []
    for module in deps:
        try:
            check_circular(deps, module, set(), [])
        except CircularDependency as e:
            cycle_key = tuple(sorted(e.chain[:-1]))
            if cycle_key not in [tuple(sorted(c[:-1])) for c in cycles]:
                cycles.append(e.chain)
    return cycles
""",
        "category": "exception",
    },

    "exc_024_deprecation_handler": {
        "source": """\
class DeprecationWarning_(Exception):
    def __init__(self, feature, version, replacement):
        super().__init__(
            f"'{feature}' deprecated since v{version}, use '{replacement}'"
        )
        self.feature = feature
        self.version = version
        self.replacement = replacement


class RemovedFeatureError(Exception):
    def __init__(self, feature, removed_in):
        super().__init__(f"'{feature}' was removed in v{removed_in}")
        self.feature = feature
        self.removed_in = removed_in


DEPRECATIONS = {
    "old_parse": {"version": "2.0", "replacement": "new_parse", "removed": "3.0"},
    "legacy_format": {"version": "1.5", "replacement": "modern_format", "removed": None},
    "sync_call": {"version": "2.5", "replacement": "async_call", "removed": "4.0"},
}


def check_deprecation(feature, current_version):
    info = DEPRECATIONS.get(feature)
    if info is None:
        return None
    removed = info.get("removed")
    if removed is not None and current_version >= removed:
        raise RemovedFeatureError(feature, removed)
    if current_version >= info["version"]:
        raise DeprecationWarning_(feature, info["version"], info["replacement"])
    return None


def use_feature_safe(feature, current_version):
    try:
        check_deprecation(feature, current_version)
        return {"feature": feature, "status": "ok"}
    except RemovedFeatureError as e:
        return {"feature": feature, "status": "removed", "error": str(e)}
    except DeprecationWarning_ as e:
        return {"feature": feature, "status": "deprecated", "warning": str(e)}


def audit_features(features, current_version):
    report = {"ok": [], "deprecated": [], "removed": [], "unknown": []}
    for feature in features:
        try:
            check_deprecation(feature, current_version)
            if feature in DEPRECATIONS:
                report["ok"].append(feature)
            else:
                report["unknown"].append(feature)
        except DeprecationWarning_:
            report["deprecated"].append(feature)
        except RemovedFeatureError:
            report["removed"].append(feature)
    return report
""",
        "category": "exception",
    },

    "exc_025_contract_checker": {
        "source": """\
class PreconditionError(Exception):
    def __init__(self, func_name, condition, value):
        super().__init__(f"{func_name}: precondition '{condition}' failed for {value}")
        self.func_name = func_name
        self.condition = condition


class PostconditionError(Exception):
    def __init__(self, func_name, condition, result):
        super().__init__(f"{func_name}: postcondition '{condition}' failed, got {result}")
        self.func_name = func_name


class InvariantError(Exception):
    def __init__(self, invariant, context):
        super().__init__(f"Invariant '{invariant}' violated: {context}")
        self.invariant = invariant


def require_positive(func_name, value):
    if not isinstance(value, (int, float)):
        raise PreconditionError(func_name, "is_numeric", value)
    if value <= 0:
        raise PreconditionError(func_name, "positive", value)


def ensure_in_range(func_name, result, low, high):
    if result < low or result > high:
        raise PostconditionError(func_name, f"in_range({low},{high})", result)


def safe_sqrt(value):
    require_positive("safe_sqrt", value)
    guess = value / 2.0
    for _ in range(100):
        next_guess = (guess + value / guess) / 2.0
        if abs(next_guess - guess) < 1e-10:
            guess = next_guess
            break
        guess = next_guess
    ensure_in_range("safe_sqrt", guess, 0, value)
    return guess


def checked_divide(a, b):
    require_positive("checked_divide", b)
    result = a / b
    return result


def check_sorted_invariant(arr, context):
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            raise InvariantError("sorted", f"{context}: {arr[i]} > {arr[i+1]}")


def contract_pipeline(values, operations):
    results = []
    for i, (op_name, op_func, precond, postcond) in enumerate(operations):
        for v in values:
            try:
                precond(op_name, v)
                result = op_func(v)
                postcond(op_name, result)
                results.append({"op": op_name, "input": v, "result": result})
            except (PreconditionError, PostconditionError) as e:
                results.append({"op": op_name, "input": v, "error": str(e)})
    return results
""",
        "category": "exception",
    },
    # ── stateful classes (25) ─────────────────────────────────────────────

    "state_001_min_stack": {
        "source": """\
class MinStack:
    def __init__(self):
        self._stack = []
        self._min_stack = []
        self._size = 0

    def push(self, value):
        self._stack.append(value)
        self._size += 1
        if not self._min_stack or value <= self._min_stack[-1]:
            self._min_stack.append(value)

    def pop(self):
        if self._size == 0:
            return None
        value = self._stack.pop()
        self._size -= 1
        if value == self._min_stack[-1]:
            self._min_stack.pop()
        return value

    def get_min(self):
        if not self._min_stack:
            return None
        return self._min_stack[-1]

    def peek(self):
        if not self._stack:
            return None
        return self._stack[-1]

    def get_size(self):
        return self._size

    def is_empty(self):
        return self._size == 0
""",
        "category": "stateful",
    },

    "state_002_lru_cache": {
        "source": """\
class LRUCache:
    def __init__(self, capacity):
        self._capacity = capacity
        self._cache = {}
        self._order = []
        self._hits = 0
        self._misses = 0

    def _move_to_end(self, key):
        self._order.remove(key)
        self._order.append(key)

    def get(self, key):
        if key in self._cache:
            self._hits += 1
            self._move_to_end(key)
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, key, value):
        if key in self._cache:
            self._cache[key] = value
            self._move_to_end(key)
        else:
            if len(self._cache) >= self._capacity:
                oldest = self._order[0]
                self._order.pop(0)
                del self._cache[oldest]
            self._cache[key] = value
            self._order.append(key)

    def evict(self, key):
        if key in self._cache:
            del self._cache[key]
            self._order.remove(key)

    def get_stats(self):
        total = self._hits + self._misses
        ratio = self._hits / total if total > 0 else 0.0
        return {"hits": self._hits, "misses": self._misses, "ratio": ratio}

    def keys(self):
        return list(self._order)
""",
        "category": "stateful",
    },

    "state_003_circular_buffer": {
        "source": """\
class CircularBuffer:
    def __init__(self, capacity):
        self._buffer = [None] * capacity
        self._capacity = capacity
        self._head = 0
        self._tail = 0
        self._count = 0

    def enqueue(self, item):
        if self._count == self._capacity:
            self._head = (self._head + 1) % self._capacity
        else:
            self._count += 1
        self._buffer[self._tail] = item
        self._tail = (self._tail + 1) % self._capacity

    def dequeue(self):
        if self._count == 0:
            return None
        item = self._buffer[self._head]
        self._buffer[self._head] = None
        self._head = (self._head + 1) % self._capacity
        self._count -= 1
        return item

    def peek(self):
        if self._count == 0:
            return None
        return self._buffer[self._head]

    def is_full(self):
        return self._count == self._capacity

    def is_empty(self):
        return self._count == 0

    def size(self):
        return self._count

    def to_list(self):
        result = []
        idx = self._head
        for _ in range(self._count):
            result.append(self._buffer[idx])
            idx = (idx + 1) % self._capacity
        return result
""",
        "category": "stateful",
    },

    "state_004_traffic_light": {
        "source": """\
class TrafficLight:
    STATES = ["red", "green", "yellow"]
    DURATIONS = {"red": 30, "green": 25, "yellow": 5}

    def __init__(self):
        self._state = "red"
        self._time_in_state = 0
        self._transition_count = 0
        self._log = []

    def _next_state(self):
        idx = self.STATES.index(self._state)
        return self.STATES[(idx + 1) % len(self.STATES)]

    def tick(self, seconds=1):
        self._time_in_state += seconds
        duration = self.DURATIONS[self._state]
        if self._time_in_state >= duration:
            old = self._state
            self._state = self._next_state()
            self._log.append((old, self._state, self._transition_count))
            self._transition_count += 1
            self._time_in_state = 0

    def get_state(self):
        return self._state

    def get_remaining(self):
        return self.DURATIONS[self._state] - self._time_in_state

    def force_state(self, state):
        if state in self.STATES:
            self._log.append((self._state, state, self._transition_count))
            self._state = state
            self._time_in_state = 0
            self._transition_count += 1

    def get_log(self):
        return list(self._log)

    def get_transition_count(self):
        return self._transition_count
""",
        "category": "stateful",
    },

    "state_005_shopping_cart": {
        "source": """\
class ShoppingCart:
    def __init__(self, tax_rate=0.0):
        self._items = {}
        self._tax_rate = tax_rate
        self._discount_code = None
        self._discount_pct = 0.0

    def add_item(self, name, price, quantity=1):
        if name in self._items:
            self._items[name]["quantity"] += quantity
        else:
            self._items[name] = {"price": price, "quantity": quantity}

    def remove_item(self, name):
        if name in self._items:
            del self._items[name]

    def update_quantity(self, name, quantity):
        if name in self._items:
            if quantity <= 0:
                del self._items[name]
            else:
                self._items[name]["quantity"] = quantity

    def apply_discount(self, code, percent):
        self._discount_code = code
        self._discount_pct = percent

    def subtotal(self):
        total = 0.0
        for info in self._items.values():
            total += info["price"] * info["quantity"]
        return total

    def total(self):
        sub = self.subtotal()
        discounted = sub * (1.0 - self._discount_pct / 100.0)
        with_tax = discounted * (1.0 + self._tax_rate / 100.0)
        return round(with_tax, 2)

    def item_count(self):
        count = 0
        for info in self._items.values():
            count += info["quantity"]
        return count

    def get_items(self):
        return dict(self._items)
""",
        "category": "stateful",
    },

    "state_006_bank_account": {
        "source": """\
class BankAccount:
    def __init__(self, owner, initial_balance=0.0):
        self._owner = owner
        self._balance = initial_balance
        self._transactions = []
        self._frozen = False

    def deposit(self, amount):
        if amount <= 0:
            return False
        if self._frozen:
            return False
        self._balance += amount
        self._transactions.append(("deposit", amount, self._balance))
        return True

    def withdraw(self, amount):
        if amount <= 0:
            return False
        if self._frozen:
            return False
        if amount > self._balance:
            return False
        self._balance -= amount
        self._transactions.append(("withdraw", amount, self._balance))
        return True

    def transfer_to(self, other, amount):
        if self.withdraw(amount):
            other.deposit(amount)
            return True
        return False

    def freeze(self):
        self._frozen = True

    def unfreeze(self):
        self._frozen = False

    def get_balance(self):
        return self._balance

    def get_statement(self):
        return list(self._transactions)

    def get_owner(self):
        return self._owner

    def is_frozen(self):
        return self._frozen
""",
        "category": "stateful",
    },

    "state_007_undo_redo": {
        "source": """\
class UndoRedoManager:
    def __init__(self):
        self._state = ""
        self._undo_stack = []
        self._redo_stack = []
        self._save_points = {}

    def execute(self, new_state):
        self._undo_stack.append(self._state)
        self._state = new_state
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return False
        self._redo_stack.append(self._state)
        self._state = self._undo_stack.pop()
        return True

    def redo(self):
        if not self._redo_stack:
            return False
        self._undo_stack.append(self._state)
        self._state = self._redo_stack.pop()
        return True

    def save_point(self, name):
        self._save_points[name] = self._state

    def restore_point(self, name):
        if name in self._save_points:
            self._undo_stack.append(self._state)
            self._state = self._save_points[name]
            self._redo_stack.clear()
            return True
        return False

    def get_state(self):
        return self._state

    def can_undo(self):
        return len(self._undo_stack) > 0

    def can_redo(self):
        return len(self._redo_stack) > 0

    def history_depth(self):
        return len(self._undo_stack)
""",
        "category": "stateful",
    },

    "state_008_event_dispatcher": {
        "source": """\
class EventDispatcher:
    def __init__(self):
        self._handlers = {}
        self._event_log = []
        self._handler_count = 0

    def on(self, event_name, handler):
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler)
        self._handler_count += 1

    def off(self, event_name, handler):
        if event_name in self._handlers:
            handlers = self._handlers[event_name]
            if handler in handlers:
                handlers.remove(handler)
                self._handler_count -= 1

    def emit(self, event_name, data=None):
        self._event_log.append({"event": event_name, "data": data})
        handlers = self._handlers.get(event_name, [])
        results = []
        for handler in handlers:
            result = handler(data)
            results.append(result)
        return results

    def clear(self, event_name=None):
        if event_name:
            count = len(self._handlers.get(event_name, []))
            self._handlers[event_name] = []
            self._handler_count -= count
        else:
            self._handlers.clear()
            self._handler_count = 0

    def get_event_log(self):
        return list(self._event_log)

    def get_handler_count(self):
        return self._handler_count

    def list_events(self):
        return list(self._handlers.keys())
""",
        "category": "stateful",
    },

    "state_009_sliding_window": {
        "source": """\
class SlidingWindowStats:
    def __init__(self, window_size):
        self._window_size = window_size
        self._values = []
        self._total = 0.0
        self._count = 0

    def add(self, value):
        self._values.append(value)
        self._total += value
        self._count += 1
        if self._count > self._window_size:
            removed = self._values.pop(0)
            self._total -= removed
            self._count -= 1

    def mean(self):
        if self._count == 0:
            return 0.0
        return self._total / self._count

    def minimum(self):
        if not self._values:
            return None
        result = self._values[0]
        for v in self._values[1:]:
            if v < result:
                result = v
        return result

    def maximum(self):
        if not self._values:
            return None
        result = self._values[0]
        for v in self._values[1:]:
            if v > result:
                result = v
        return result

    def variance(self):
        if self._count < 2:
            return 0.0
        m = self.mean()
        total = 0.0
        for v in self._values:
            total += (v - m) ** 2
        return total / self._count

    def get_values(self):
        return list(self._values)

    def size(self):
        return self._count
""",
        "category": "stateful",
    },
    "state_010_connection_pool": {
        "source": """\
class ConnectionPool:
    def __init__(self, max_size, host, port):
        self._max_size = max_size
        self._host = host
        self._port = port
        self._available = []
        self._in_use = []
        self._created = 0

    def _create_connection(self):
        self._created += 1
        conn_id = self._created
        return {"id": conn_id, "host": self._host, "port": self._port}

    def acquire(self):
        if self._available:
            conn = self._available.pop(0)
            self._in_use.append(conn)
            return conn
        if len(self._in_use) < self._max_size:
            conn = self._create_connection()
            self._in_use.append(conn)
            return conn
        return None

    def release(self, conn):
        if conn in self._in_use:
            self._in_use.remove(conn)
            self._available.append(conn)

    def destroy(self, conn):
        if conn in self._in_use:
            self._in_use.remove(conn)
        elif conn in self._available:
            self._available.remove(conn)

    def drain(self):
        self._available.clear()
        self._in_use.clear()

    def stats(self):
        return {
            "available": len(self._available),
            "in_use": len(self._in_use),
            "total_created": self._created,
            "max_size": self._max_size,
        }
""",
        "category": "stateful",
    },

    "state_011_job_queue": {
        "source": """\
class JobQueue:
    def __init__(self):
        self._jobs = []
        self._completed = []
        self._failed = []
        self._next_id = 1

    def enqueue(self, name, priority=0):
        job = {
            "id": self._next_id,
            "name": name,
            "priority": priority,
            "status": "pending",
        }
        self._next_id += 1
        self._jobs.append(job)
        self._jobs.sort(key=lambda j: -j["priority"])
        return job["id"]

    def dequeue(self):
        if not self._jobs:
            return None
        job = self._jobs.pop(0)
        job["status"] = "running"
        return job

    def complete(self, job, result=None):
        job["status"] = "completed"
        job["result"] = result
        self._completed.append(job)

    def fail(self, job, error=None):
        job["status"] = "failed"
        job["error"] = error
        self._failed.append(job)

    def retry(self, job):
        job["status"] = "pending"
        self._jobs.append(job)
        self._jobs.sort(key=lambda j: -j["priority"])

    def pending_count(self):
        return len(self._jobs)

    def completed_count(self):
        return len(self._completed)

    def failed_count(self):
        return len(self._failed)

    def get_stats(self):
        return {
            "pending": self.pending_count(),
            "completed": self.completed_count(),
            "failed": self.failed_count(),
        }
""",
        "category": "stateful",
    },

    "state_012_token_bucket": {
        "source": """\
class TokenBucket:
    def __init__(self, capacity, refill_rate):
        self._capacity = capacity
        self._tokens = capacity
        self._refill_rate = refill_rate
        self._last_refill = 0.0
        self._total_consumed = 0
        self._total_rejected = 0

    def refill(self, current_time):
        elapsed = current_time - self._last_refill
        if elapsed > 0:
            added = elapsed * self._refill_rate
            self._tokens = min(self._capacity, self._tokens + added)
            self._last_refill = current_time

    def consume(self, tokens, current_time):
        self.refill(current_time)
        if tokens <= self._tokens:
            self._tokens -= tokens
            self._total_consumed += tokens
            return True
        self._total_rejected += tokens
        return False

    def available(self):
        return self._tokens

    def get_capacity(self):
        return self._capacity

    def get_stats(self):
        return {
            "capacity": self._capacity,
            "available": self._tokens,
            "total_consumed": self._total_consumed,
            "total_rejected": self._total_rejected,
            "refill_rate": self._refill_rate,
        }

    def reset(self):
        self._tokens = self._capacity
        self._total_consumed = 0
        self._total_rejected = 0
        self._last_refill = 0.0
""",
        "category": "stateful",
    },

    "state_013_scoreboard": {
        "source": """\
class Scoreboard:
    def __init__(self, max_entries=10):
        self._scores = {}
        self._max_entries = max_entries
        self._history = []

    def add_score(self, player, score):
        old = self._scores.get(player, 0)
        if score > old:
            self._scores[player] = score
            self._history.append(("update", player, old, score))

    def remove_player(self, player):
        if player in self._scores:
            old = self._scores.pop(player)
            self._history.append(("remove", player, old, 0))

    def get_score(self, player):
        return self._scores.get(player, 0)

    def top_n(self, n=None):
        if n is None:
            n = self._max_entries
        items = list(self._scores.items())
        items.sort(key=lambda x: -x[1])
        return items[:n]

    def get_rank(self, player):
        if player not in self._scores:
            return -1
        target = self._scores[player]
        rank = 1
        for p, s in self._scores.items():
            if s > target:
                rank += 1
        return rank

    def average_score(self):
        if not self._scores:
            return 0.0
        total = sum(self._scores.values())
        return total / len(self._scores)

    def player_count(self):
        return len(self._scores)

    def get_history(self):
        return list(self._history)
""",
        "category": "stateful",
    },

    "state_014_inventory": {
        "source": """\
class Inventory:
    def __init__(self):
        self._items = {}
        self._reorder_levels = {}
        self._transaction_log = []

    def add_item(self, sku, name, quantity, reorder_level=5):
        self._items[sku] = {"name": name, "quantity": quantity}
        self._reorder_levels[sku] = reorder_level
        self._transaction_log.append(("add", sku, quantity))

    def restock(self, sku, quantity):
        if sku in self._items:
            self._items[sku]["quantity"] += quantity
            self._transaction_log.append(("restock", sku, quantity))

    def sell(self, sku, quantity):
        if sku not in self._items:
            return False
        if self._items[sku]["quantity"] < quantity:
            return False
        self._items[sku]["quantity"] -= quantity
        self._transaction_log.append(("sell", sku, quantity))
        return True

    def check_reorder(self):
        needs_reorder = []
        for sku, info in self._items.items():
            level = self._reorder_levels.get(sku, 0)
            if info["quantity"] <= level:
                needs_reorder.append(sku)
        return needs_reorder

    def get_quantity(self, sku):
        if sku in self._items:
            return self._items[sku]["quantity"]
        return 0

    def total_value(self, prices):
        total = 0.0
        for sku, info in self._items.items():
            price = prices.get(sku, 0.0)
            total += price * info["quantity"]
        return total

    def get_log(self):
        return list(self._transaction_log)
""",
        "category": "stateful",
    },

    "state_015_timer_laps": {
        "source": """\
class LapTimer:
    def __init__(self):
        self._start_time = None
        self._laps = []
        self._running = False
        self._paused_elapsed = 0.0

    def start(self, timestamp):
        self._start_time = timestamp
        self._running = True
        self._paused_elapsed = 0.0
        self._laps.clear()

    def lap(self, timestamp):
        if not self._running:
            return None
        elapsed = timestamp - self._start_time - self._paused_elapsed
        self._laps.append(elapsed)
        return elapsed

    def stop(self, timestamp):
        if not self._running:
            return None
        elapsed = timestamp - self._start_time - self._paused_elapsed
        self._running = False
        return elapsed

    def get_laps(self):
        return list(self._laps)

    def get_split_times(self):
        splits = []
        for i in range(len(self._laps)):
            if i == 0:
                splits.append(self._laps[i])
            else:
                splits.append(self._laps[i] - self._laps[i - 1])
        return splits

    def fastest_lap(self):
        splits = self.get_split_times()
        if not splits:
            return None
        best = splits[0]
        for s in splits[1:]:
            if s < best:
                best = s
        return best

    def average_lap(self):
        splits = self.get_split_times()
        if not splits:
            return 0.0
        return sum(splits) / len(splits)

    def total_laps(self):
        return len(self._laps)
""",
        "category": "stateful",
    },

    "state_016_chat_room": {
        "source": """\
class ChatRoom:
    def __init__(self, name, max_members=50):
        self._name = name
        self._max_members = max_members
        self._members = set()
        self._messages = []
        self._banned = set()

    def join(self, user):
        if user in self._banned:
            return False
        if len(self._members) >= self._max_members:
            return False
        self._members.add(user)
        self._messages.append({"type": "system", "text": f"{user} joined"})
        return True

    def leave(self, user):
        if user in self._members:
            self._members.discard(user)
            self._messages.append({"type": "system", "text": f"{user} left"})

    def send_message(self, user, text):
        if user not in self._members:
            return False
        self._messages.append({"type": "message", "user": user, "text": text})
        return True

    def ban(self, user):
        self._banned.add(user)
        self._members.discard(user)
        self._messages.append({"type": "system", "text": f"{user} was banned"})

    def get_members(self):
        return sorted(self._members)

    def get_messages(self, limit=20):
        return self._messages[-limit:]

    def member_count(self):
        return len(self._members)

    def message_count(self):
        return len(self._messages)

    def is_member(self, user):
        return user in self._members
""",
        "category": "stateful",
    },

    "state_017_ttl_cache": {
        "source": """\
class TTLCache:
    def __init__(self, default_ttl=60):
        self._store = {}
        self._expiry = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def put(self, key, value, ttl=None, current_time=0):
        if ttl is None:
            ttl = self._default_ttl
        self._store[key] = value
        self._expiry[key] = current_time + ttl

    def get(self, key, current_time=0):
        if key not in self._store:
            self._misses += 1
            return None
        if current_time >= self._expiry.get(key, 0):
            del self._store[key]
            del self._expiry[key]
            self._misses += 1
            return None
        self._hits += 1
        return self._store[key]

    def evict_expired(self, current_time):
        expired_keys = []
        for key, exp_time in self._expiry.items():
            if current_time >= exp_time:
                expired_keys.append(key)
        for key in expired_keys:
            del self._store[key]
            del self._expiry[key]
        return len(expired_keys)

    def size(self):
        return len(self._store)

    def clear(self):
        self._store.clear()
        self._expiry.clear()
        self._hits = 0
        self._misses = 0

    def get_stats(self):
        total = self._hits + self._misses
        ratio = self._hits / total if total > 0 else 0.0
        return {"hits": self._hits, "misses": self._misses, "hit_ratio": ratio}
""",
        "category": "stateful",
    },
    "state_018_pagination": {
        "source": """\
class PaginationCursor:
    def __init__(self, items, page_size=10):
        self._items = list(items)
        self._page_size = page_size
        self._current_page = 0
        self._total_pages = max(1, (len(items) + page_size - 1) // page_size)
        self._visited_pages = set()

    def current(self):
        start = self._current_page * self._page_size
        end = start + self._page_size
        self._visited_pages.add(self._current_page)
        return self._items[start:end]

    def next_page(self):
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
        return self.current()

    def prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
        return self.current()

    def go_to_page(self, page):
        if 0 <= page < self._total_pages:
            self._current_page = page
        return self.current()

    def has_next(self):
        return self._current_page < self._total_pages - 1

    def has_prev(self):
        return self._current_page > 0

    def get_page_info(self):
        return {
            "current": self._current_page,
            "total_pages": self._total_pages,
            "page_size": self._page_size,
            "total_items": len(self._items),
            "visited": len(self._visited_pages),
        }
""",
        "category": "stateful",
    },

    "state_019_stream_processor": {
        "source": """\
class StreamProcessor:
    def __init__(self, buffer_size=100):
        self._buffer = []
        self._buffer_size = buffer_size
        self._processed_count = 0
        self._dropped_count = 0
        self._filters = []

    def add_filter(self, predicate):
        self._filters.append(predicate)

    def ingest(self, item):
        for pred in self._filters:
            if not pred(item):
                self._dropped_count += 1
                return False
        if len(self._buffer) >= self._buffer_size:
            self._buffer.pop(0)
            self._dropped_count += 1
        self._buffer.append(item)
        return True

    def flush(self):
        items = list(self._buffer)
        self._processed_count += len(items)
        self._buffer.clear()
        return items

    def peek(self, count=5):
        return self._buffer[:count]

    def buffer_usage(self):
        if self._buffer_size == 0:
            return 0.0
        return len(self._buffer) / self._buffer_size

    def get_stats(self):
        return {
            "buffer_size": len(self._buffer),
            "buffer_capacity": self._buffer_size,
            "processed": self._processed_count,
            "dropped": self._dropped_count,
            "filters": len(self._filters),
        }

    def reset(self):
        self._buffer.clear()
        self._processed_count = 0
        self._dropped_count = 0
        self._filters.clear()
""",
        "category": "stateful",
    },

    "state_020_graph_builder": {
        "source": """\
class GraphBuilder:
    def __init__(self, directed=True):
        self._adjacency = {}
        self._weights = {}
        self._directed = directed
        self._node_data = {}

    def add_node(self, node, data=None):
        if node not in self._adjacency:
            self._adjacency[node] = []
        if data is not None:
            self._node_data[node] = data

    def add_edge(self, src, dst, weight=1.0):
        self.add_node(src)
        self.add_node(dst)
        self._adjacency[src].append(dst)
        self._weights[(src, dst)] = weight
        if not self._directed:
            self._adjacency[dst].append(src)
            self._weights[(dst, src)] = weight

    def remove_edge(self, src, dst):
        if src in self._adjacency and dst in self._adjacency[src]:
            self._adjacency[src].remove(dst)
            self._weights.pop((src, dst), None)
        if not self._directed:
            if dst in self._adjacency and src in self._adjacency[dst]:
                self._adjacency[dst].remove(src)
                self._weights.pop((dst, src), None)

    def neighbors(self, node):
        return list(self._adjacency.get(node, []))

    def node_count(self):
        return len(self._adjacency)

    def edge_count(self):
        total = sum(len(v) for v in self._adjacency.values())
        if not self._directed:
            total = total // 2
        return total

    def has_edge(self, src, dst):
        return dst in self._adjacency.get(src, [])

    def get_weight(self, src, dst):
        return self._weights.get((src, dst), None)
""",
        "category": "stateful",
    },

    "state_021_trie": {
        "source": """\
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end = False
        self.prefix_count = 0


class Trie:
    def __init__(self):
        self._root = TrieNode()
        self._word_count = 0

    def insert(self, word):
        node = self._root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
            node.prefix_count += 1
        if not node.is_end:
            node.is_end = True
            self._word_count += 1

    def search(self, word):
        node = self._root
        for ch in word:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return node.is_end

    def starts_with(self, prefix):
        node = self._root
        for ch in prefix:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return True

    def count_prefix(self, prefix):
        node = self._root
        for ch in prefix:
            if ch not in node.children:
                return 0
            node = node.children[ch]
        return node.prefix_count

    def word_count(self):
        return self._word_count

    def _collect(self, node, prefix, results):
        if node.is_end:
            results.append(prefix)
        for ch, child in sorted(node.children.items()):
            self._collect(child, prefix + ch, results)

    def all_words(self):
        results = []
        self._collect(self._root, "", results)
        return results
""",
        "category": "stateful",
    },

    "state_022_ring_buffer_logger": {
        "source": """\
class RingBufferLogger:
    def __init__(self, capacity=1000):
        self._buffer = [None] * capacity
        self._capacity = capacity
        self._write_pos = 0
        self._count = 0
        self._level_counts = {"DEBUG": 0, "INFO": 0, "WARN": 0, "ERROR": 0}

    def log(self, level, message, timestamp=0):
        entry = {"level": level, "message": message, "timestamp": timestamp}
        self._buffer[self._write_pos] = entry
        self._write_pos = (self._write_pos + 1) % self._capacity
        if self._count < self._capacity:
            self._count += 1
        if level in self._level_counts:
            self._level_counts[level] += 1

    def get_recent(self, count=10):
        count = min(count, self._count)
        result = []
        pos = (self._write_pos - count) % self._capacity
        for _ in range(count):
            result.append(self._buffer[pos])
            pos = (pos + 1) % self._capacity
        return result

    def filter_by_level(self, level, count=10):
        entries = self.get_recent(self._count)
        filtered = []
        for entry in entries:
            if entry and entry["level"] == level:
                filtered.append(entry)
        return filtered[-count:]

    def get_stats(self):
        return {
            "total_logged": sum(self._level_counts.values()),
            "buffer_usage": self._count,
            "capacity": self._capacity,
            "level_counts": dict(self._level_counts),
        }

    def clear(self):
        self._buffer = [None] * self._capacity
        self._write_pos = 0
        self._count = 0
        self._level_counts = {"DEBUG": 0, "INFO": 0, "WARN": 0, "ERROR": 0}
""",
        "category": "stateful",
    },

    "state_023_histogram": {
        "source": """\
class Histogram:
    def __init__(self, bin_width=1.0, min_val=0.0):
        self._bins = {}
        self._bin_width = bin_width
        self._min_val = min_val
        self._count = 0
        self._sum = 0.0
        self._sum_sq = 0.0

    def _get_bin(self, value):
        return int((value - self._min_val) // self._bin_width)

    def add(self, value):
        bin_idx = self._get_bin(value)
        if bin_idx not in self._bins:
            self._bins[bin_idx] = 0
        self._bins[bin_idx] += 1
        self._count += 1
        self._sum += value
        self._sum_sq += value * value

    def add_many(self, values):
        for v in values:
            self.add(v)

    def get_count(self, bin_idx):
        return self._bins.get(bin_idx, 0)

    def mean(self):
        if self._count == 0:
            return 0.0
        return self._sum / self._count

    def variance(self):
        if self._count < 2:
            return 0.0
        mean = self.mean()
        return self._sum_sq / self._count - mean * mean

    def percentile(self, p):
        if self._count == 0:
            return 0.0
        target = self._count * p / 100.0
        running = 0
        for bin_idx in sorted(self._bins.keys()):
            running += self._bins[bin_idx]
            if running >= target:
                return self._min_val + bin_idx * self._bin_width
        return self._min_val

    def total_count(self):
        return self._count

    def num_bins(self):
        return len(self._bins)
""",
        "category": "stateful",
    },

    "state_024_task_tracker": {
        "source": """\
class TaskTracker:
    def __init__(self):
        self._tasks = {}
        self._deps = {}
        self._next_id = 1
        self._completed = set()

    def add_task(self, title, depends_on=None):
        task_id = self._next_id
        self._next_id += 1
        self._tasks[task_id] = {
            "title": title,
            "status": "pending",
        }
        self._deps[task_id] = list(depends_on or [])
        return task_id

    def complete_task(self, task_id):
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = "done"
            self._completed.add(task_id)

    def is_ready(self, task_id):
        if task_id not in self._deps:
            return False
        for dep in self._deps[task_id]:
            if dep not in self._completed:
                return False
        return True

    def get_ready_tasks(self):
        ready = []
        for task_id, info in self._tasks.items():
            if info["status"] == "pending" and self.is_ready(task_id):
                ready.append((task_id, info["title"]))
        return ready

    def get_blocked_tasks(self):
        blocked = []
        for task_id, info in self._tasks.items():
            if info["status"] == "pending" and not self.is_ready(task_id):
                missing = [d for d in self._deps[task_id] if d not in self._completed]
                blocked.append((task_id, info["title"], missing))
        return blocked

    def completion_rate(self):
        if not self._tasks:
            return 0.0
        return len(self._completed) / len(self._tasks)

    def get_task(self, task_id):
        return self._tasks.get(task_id)
""",
        "category": "stateful",
    },

    "state_025_finite_automaton": {
        "source": """\
class FiniteAutomaton:
    def __init__(self, initial_state):
        self._current = initial_state
        self._initial = initial_state
        self._transitions = {}
        self._accept_states = set()
        self._history = []

    def add_transition(self, from_state, symbol, to_state):
        if from_state not in self._transitions:
            self._transitions[from_state] = {}
        self._transitions[from_state][symbol] = to_state

    def add_accept_state(self, state):
        self._accept_states.add(state)

    def step(self, symbol):
        state_transitions = self._transitions.get(self._current, {})
        if symbol in state_transitions:
            old = self._current
            self._current = state_transitions[symbol]
            self._history.append((old, symbol, self._current))
            return True
        return False

    def run(self, symbols):
        self.reset()
        for sym in symbols:
            if not self.step(sym):
                return False
        return self.is_accepting()

    def is_accepting(self):
        return self._current in self._accept_states

    def reset(self):
        self._current = self._initial
        self._history.clear()

    def get_state(self):
        return self._current

    def get_history(self):
        return list(self._history)

    def reachable_states(self):
        visited = set()
        queue = [self._initial]
        while queue:
            state = queue.pop(0)
            if state in visited:
                continue
            visited.add(state)
            for sym, target in self._transitions.get(state, {}).items():
                if target not in visited:
                    queue.append(target)
        return visited
""",
        "category": "stateful",
    },
    # ── io_like (25) ──────────────────────────────────────────────────────

    "io_001_html_table": {
        "source": """\
def html_escape(text):
    result = str(text)
    result = result.replace("&", "&amp;")
    result = result.replace("<", "&lt;")
    result = result.replace(">", "&gt;")
    result = result.replace('"', "&quot;")
    return result


def html_tag(tag, content, attrs=None):
    attr_str = ""
    if attrs:
        parts = []
        for key, value in attrs.items():
            parts.append(f'{key}="{html_escape(value)}"')
        attr_str = " " + " ".join(parts)
    return f"<{tag}{attr_str}>{content}</{tag}>"


def html_table_row(cells, tag="td"):
    cells_html = ""
    for cell in cells:
        cells_html += html_tag(tag, html_escape(cell))
    return html_tag("tr", cells_html)


def build_html_table(headers, rows, table_class="data-table"):
    header_row = html_table_row(headers, tag="th")
    thead = html_tag("thead", header_row)
    body_rows = ""
    for row in rows:
        body_rows += html_table_row(row)
    tbody = html_tag("tbody", body_rows)
    attrs = {"class": table_class}
    return html_tag("table", thead + tbody, attrs)


def build_html_page(title, table_html):
    head = html_tag("head", html_tag("title", html_escape(title)))
    body = html_tag("body", html_tag("h1", html_escape(title)) + table_html)
    return "<!DOCTYPE html>\\n" + html_tag("html", head + body)
""",
        "category": "io_like",
    },

    "io_002_csv_formatter": {
        "source": """\
def csv_escape_field(field):
    text = str(field)
    needs_quoting = False
    if ',' in text or '"' in text or '\\n' in text:
        needs_quoting = True
    if needs_quoting:
        escaped = text.replace('"', '""')
        return f'"{escaped}"'
    return text


def format_csv_row(fields, delimiter=","):
    escaped = []
    for field in fields:
        escaped.append(csv_escape_field(field))
    return delimiter.join(escaped)


def format_csv(headers, rows, delimiter=","):
    lines = []
    lines.append(format_csv_row(headers, delimiter))
    for row in rows:
        lines.append(format_csv_row(row, delimiter))
    return "\\n".join(lines)


def format_tsv(headers, rows):
    return format_csv(headers, rows, delimiter="\\t")


def csv_to_fixed_width(headers, rows, padding=2):
    all_rows = [headers] + rows
    widths = [0] * len(headers)
    for row in all_rows:
        for i, cell in enumerate(row):
            cell_len = len(str(cell))
            if cell_len > widths[i]:
                widths[i] = cell_len
    lines = []
    for row in all_rows:
        parts = []
        for i, cell in enumerate(row):
            parts.append(str(cell).ljust(widths[i] + padding))
        lines.append("".join(parts).rstrip())
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_003_log_formatter": {
        "source": """\
def format_timestamp(hours, minutes, seconds, millis=0):
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_log_level(level):
    level_str = level.upper()
    padded = level_str.ljust(5)
    return padded


def format_log_entry(timestamp, level, module, message):
    ts = format_timestamp(*timestamp)
    lvl = format_log_level(level)
    return f"[{ts}] {lvl} [{module}] {message}"


def format_log_with_context(timestamp, level, module, message, context):
    base = format_log_entry(timestamp, level, module, message)
    if not context:
        return base
    ctx_parts = []
    for key, value in context.items():
        ctx_parts.append(f"{key}={value}")
    ctx_str = " ".join(ctx_parts)
    return f"{base} | {ctx_str}"


def format_log_batch(entries):
    lines = []
    for entry in entries:
        ts = entry.get("timestamp", (0, 0, 0, 0))
        level = entry.get("level", "INFO")
        module = entry.get("module", "main")
        message = entry.get("message", "")
        context = entry.get("context", {})
        line = format_log_with_context(ts, level, module, message, context)
        lines.append(line)
    return "\\n".join(lines)


def format_error_log(error_type, message, stack_frames):
    lines = []
    lines.append(f"ERROR: {error_type}: {message}")
    lines.append("Stack trace:")
    for i, frame in enumerate(stack_frames):
        file_name = frame.get("file", "unknown")
        line_no = frame.get("line", 0)
        func = frame.get("function", "unknown")
        lines.append(f"  #{i} {file_name}:{line_no} in {func}")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_004_json_pretty": {
        "source": """\
def indent_str(level, indent_size=2):
    return " " * (level * indent_size)


def pretty_string(value):
    escaped = str(value).replace('\\\\', '\\\\\\\\').replace('"', '\\\\"')
    return f'"{escaped}"'


def pretty_format(value, level=0, indent_size=2):
    ind = indent_str(level, indent_size)
    ind_next = indent_str(level + 1, indent_size)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return pretty_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        items = []
        for item in value:
            items.append(ind_next + pretty_format(item, level + 1, indent_size))
        return "[\\n" + ",\\n".join(items) + "\\n" + ind + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = []
        for k, v in value.items():
            key_str = pretty_string(k)
            val_str = pretty_format(v, level + 1, indent_size)
            items.append(f"{ind_next}{key_str}: {val_str}")
        return "{\\n" + ",\\n".join(items) + "\\n" + ind + "}"
    return pretty_string(str(value))


def pretty_print_compact(value, max_line_length=80):
    single_line = compact_format(value)
    if len(single_line) <= max_line_length:
        return single_line
    return pretty_format(value)


def compact_format(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return pretty_string(value)
    if isinstance(value, list):
        items = [compact_format(v) for v in value]
        return "[" + ", ".join(items) + "]"
    if isinstance(value, dict):
        items = [f"{pretty_string(k)}: {compact_format(v)}" for k, v in value.items()]
        return "{" + ", ".join(items) + "}"
    return pretty_string(str(value))
""",
        "category": "io_like",
    },

    "io_005_progress_bar": {
        "source": """\
def progress_bar(current, total, width=40, fill_char="=", empty_char=" "):
    if total == 0:
        percent = 100.0
    else:
        percent = (current / total) * 100.0
    filled = int(width * current / total) if total > 0 else width
    bar = fill_char * filled + empty_char * (width - filled)
    return f"[{bar}] {percent:5.1f}%"


def progress_with_eta(current, total, elapsed_seconds, width=40):
    bar = progress_bar(current, total, width)
    if current > 0 and current < total:
        rate = elapsed_seconds / current
        remaining = rate * (total - current)
        eta = format_duration(remaining)
    elif current >= total:
        eta = "done"
    else:
        eta = "calculating..."
    return f"{bar}  ETA: {eta}"


def format_duration(seconds):
    hours = int(seconds) // 3600
    minutes = (int(seconds) % 3600) // 60
    secs = int(seconds) % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def multi_progress(tasks, width=30):
    lines = []
    for name, current, total in tasks:
        bar = progress_bar(current, total, width)
        lines.append(f"  {name:20s} {bar}  ({current}/{total})")
    total_current = sum(c for _, c, _ in tasks)
    total_all = sum(t for _, _, t in tasks)
    overall = progress_bar(total_current, total_all, width)
    lines.append(f"  {'OVERALL':20s} {overall}  ({total_current}/{total_all})")
    return "\\n".join(lines)


def spinner_frame(frame_number, style="dots"):
    frames = {
        "dots": [".  ", ".. ", "...", " ..", "  .", "   "],
        "bar": ["|", "/", "-", "\\\\"],
        "arrows": ["<", "^", ">", "v"],
    }
    chars = frames.get(style, frames["dots"])
    return chars[frame_number % len(chars)]
""",
        "category": "io_like",
    },

    "io_006_bar_chart": {
        "source": """\
def normalize_values(values, max_width):
    if not values:
        return []
    max_val = max(values)
    if max_val == 0:
        return [0] * len(values)
    result = []
    for v in values:
        result.append(int(v / max_val * max_width))
    return result


def horizontal_bar(label, value, bar_width, bar_char="*"):
    bar = bar_char * bar_width
    return f"{label:15s} | {bar} ({value})"


def build_bar_chart(data, max_width=40, bar_char="*", title=""):
    labels = [d[0] for d in data]
    values = [d[1] for d in data]
    normalized = normalize_values(values, max_width)
    lines = []
    if title:
        lines.append(title)
        lines.append("=" * (max_width + 25))
    for label, norm, val in zip(labels, normalized, values):
        lines.append(horizontal_bar(label, val, norm, bar_char))
    lines.append("-" * (max_width + 25))
    total = sum(values)
    avg = total / len(values) if values else 0
    lines.append(f"Total: {total}  Average: {avg:.1f}")
    return "\\n".join(lines)


def vertical_bar_chart(data, height=10, bar_char="#"):
    labels = [d[0] for d in data]
    values = [d[1] for d in data]
    max_val = max(values) if values else 1
    lines = []
    for row in range(height, 0, -1):
        threshold = max_val * row / height
        line = ""
        for v in values:
            if v >= threshold:
                line += f" {bar_char}  "
            else:
                line += "    "
        lines.append(f"{threshold:6.0f} |{line}")
    lines.append("       +" + "----" * len(values))
    label_line = "        "
    for label in labels:
        label_line += f"{label[:3]:4s}"
    lines.append(label_line)
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_007_markdown_list": {
        "source": """\
def markdown_heading(text, level=1):
    prefix = "#" * level
    return f"{prefix} {text}"


def markdown_bullet_list(items, indent=0):
    lines = []
    prefix = "  " * indent
    for item in items:
        if isinstance(item, tuple) and len(item) == 2:
            lines.append(f"{prefix}- {item[0]}")
            sub_items = item[1]
            sub_text = markdown_bullet_list(sub_items, indent + 1)
            lines.append(sub_text)
        else:
            lines.append(f"{prefix}- {item}")
    return "\\n".join(lines)


def markdown_numbered_list(items, start=1):
    lines = []
    for i, item in enumerate(items, start):
        lines.append(f"{i}. {item}")
    return "\\n".join(lines)


def markdown_checkbox_list(items):
    lines = []
    for text, checked in items:
        mark = "x" if checked else " "
        lines.append(f"- [{mark}] {text}")
    return "\\n".join(lines)


def markdown_link(text, url, title=None):
    if title:
        return "[" + text + "](" + url + ' "' + title + '")'
    return f"[{text}]({url})"


def markdown_table(headers, rows, alignment=None):
    if alignment is None:
        alignment = ["left"] * len(headers)
    header_line = "| " + " | ".join(headers) + " |"
    sep_parts = []
    for align in alignment:
        if align == "center":
            sep_parts.append(":---:")
        elif align == "right":
            sep_parts.append("---:")
        else:
            sep_parts.append("---")
    sep_line = "| " + " | ".join(sep_parts) + " |"
    lines = [header_line, sep_line]
    for row in rows:
        row_strs = [str(cell) for cell in row]
        lines.append("| " + " | ".join(row_strs) + " |")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_008_email_template": {
        "source": """\
def build_greeting(name, formal=True):
    if formal:
        return f"Dear {name},"
    return f"Hi {name},"


def build_signature(sender_name, title, company, phone=None):
    lines = []
    lines.append("--")
    lines.append(f"{sender_name}")
    lines.append(f"{title}")
    lines.append(f"{company}")
    if phone:
        lines.append(f"Phone: {phone}")
    return "\\n".join(lines)


def wrap_text(text, width=72):
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        if current_line and len(current_line) + 1 + len(word) > width:
            lines.append(current_line)
            current_line = word
        elif current_line:
            current_line = current_line + " " + word
        else:
            current_line = word
    if current_line:
        lines.append(current_line)
    return "\\n".join(lines)


def build_email(to, subject, body, sender_name, sender_title, company):
    lines = []
    lines.append(f"To: {to}")
    lines.append(f"Subject: {subject}")
    lines.append("")
    greeting = build_greeting(to.split("@")[0].replace(".", " ").title())
    lines.append(greeting)
    lines.append("")
    wrapped = wrap_text(body)
    lines.append(wrapped)
    lines.append("")
    sig = build_signature(sender_name, sender_title, company)
    lines.append(sig)
    return "\\n".join(lines)


def build_bulk_email(recipients, subject, body_template, sender_info):
    emails = []
    for recipient in recipients:
        name = recipient.get("name", "User")
        email = recipient.get("email", "")
        body = body_template.replace("{name}", name)
        msg = build_email(email, subject, body, *sender_info)
        emails.append(msg)
    return emails
""",
        "category": "io_like",
    },

    "io_009_sql_builder": {
        "source": """\
def quote_identifier(name):
    return f'"{name}"'


def quote_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def build_select(table, columns=None, where=None, order_by=None, limit=None):
    col_str = ", ".join(columns) if columns else "*"
    query = f"SELECT {col_str} FROM {quote_identifier(table)}"
    if where:
        conditions = []
        for col, op, val in where:
            conditions.append(f"{col} {op} {quote_value(val)}")
        query += " WHERE " + " AND ".join(conditions)
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit is not None:
        query += f" LIMIT {limit}"
    return query + ";"


def build_insert(table, data):
    columns = list(data.keys())
    values = [quote_value(data[c]) for c in columns]
    col_str = ", ".join(quote_identifier(c) for c in columns)
    val_str = ", ".join(values)
    return f"INSERT INTO {quote_identifier(table)} ({col_str}) VALUES ({val_str});"


def build_update(table, data, where):
    set_parts = []
    for col, val in data.items():
        set_parts.append(f"{quote_identifier(col)} = {quote_value(val)}")
    query = f"UPDATE {quote_identifier(table)} SET {', '.join(set_parts)}"
    if where:
        conditions = []
        for col, op, val in where:
            conditions.append(f"{col} {op} {quote_value(val)}")
        query += " WHERE " + " AND ".join(conditions)
    return query + ";"


def build_delete(table, where):
    query = f"DELETE FROM {quote_identifier(table)}"
    if where:
        conditions = []
        for col, op, val in where:
            conditions.append(f"{col} {op} {quote_value(val)}")
        query += " WHERE " + " AND ".join(conditions)
    return query + ";"
""",
        "category": "io_like",
    },
    "io_010_error_report": {
        "source": """\
def format_error_header(error_type, message):
    line = "!" * 60
    return f"{line}\\n  ERROR: {error_type}\\n  {message}\\n{line}"


def format_context_line(line_num, line_text, is_error=False):
    marker = ">>>" if is_error else "   "
    return f"  {marker} {line_num:4d} | {line_text}"


def format_code_context(lines, error_line, context=3):
    result = []
    start = max(0, error_line - context)
    end = min(len(lines), error_line + context + 1)
    for i in range(start, end):
        is_err = (i == error_line)
        result.append(format_context_line(i + 1, lines[i], is_err))
    return "\\n".join(result)


def format_error_report(error_type, message, file_path, source_lines, error_line):
    parts = []
    parts.append(format_error_header(error_type, message))
    parts.append("")
    parts.append(f"  File: {file_path}")
    parts.append(f"  Line: {error_line + 1}")
    parts.append("")
    parts.append(format_code_context(source_lines, error_line))
    parts.append("")
    suggestion = suggest_fix(error_type, message)
    if suggestion:
        parts.append(f"  Suggestion: {suggestion}")
    return "\\n".join(parts)


def suggest_fix(error_type, message):
    suggestions = {
        "NameError": "Check variable spelling or ensure it is defined before use",
        "TypeError": "Verify argument types match the function signature",
        "IndexError": "Check list bounds before accessing elements",
        "KeyError": "Use .get() with a default or check key existence first",
        "AttributeError": "Verify the object has the expected attribute",
    }
    return suggestions.get(error_type, None)


def format_multi_error_report(errors):
    reports = []
    for err in errors:
        report = format_error_report(
            err["type"], err["message"],
            err["file"], err["lines"], err["line"]
        )
        reports.append(report)
    summary = f"\\n{'='*60}\\n  Total errors: {len(errors)}\\n{'='*60}"
    return "\\n\\n".join(reports) + summary
""",
        "category": "io_like",
    },

    "io_011_invoice": {
        "source": """\
def format_currency(amount, symbol="$"):
    if amount < 0:
        return f"-{symbol}{abs(amount):,.2f}"
    return f"{symbol}{amount:,.2f}"


def format_invoice_line(item, quantity, unit_price):
    total = quantity * unit_price
    name_col = item.ljust(30)
    qty_col = str(quantity).rjust(5)
    price_col = format_currency(unit_price).rjust(12)
    total_col = format_currency(total).rjust(12)
    return f"  {name_col} {qty_col} {price_col} {total_col}"


def format_invoice_header():
    name_col = "Item".ljust(30)
    qty_col = "Qty".rjust(5)
    price_col = "Unit Price".rjust(12)
    total_col = "Total".rjust(12)
    header = f"  {name_col} {qty_col} {price_col} {total_col}"
    separator = "  " + "-" * 63
    return header + "\\n" + separator


def build_invoice(invoice_num, customer, items, tax_rate=0.0, discount=0.0):
    lines = []
    lines.append("=" * 67)
    lines.append(f"  INVOICE #{invoice_num}")
    lines.append(f"  Customer: {customer}")
    lines.append("=" * 67)
    lines.append("")
    lines.append(format_invoice_header())
    subtotal = 0.0
    for item, qty, price in items:
        lines.append(format_invoice_line(item, qty, price))
        subtotal += qty * price
    lines.append("  " + "-" * 63)
    lines.append(f"  {'Subtotal':>49s} {format_currency(subtotal):>12s}")
    if discount > 0:
        disc_amount = subtotal * discount / 100.0
        lines.append(f"  {'Discount (' + str(discount) + '%)':>49s} {format_currency(-disc_amount):>12s}")
        subtotal -= disc_amount
    if tax_rate > 0:
        tax = subtotal * tax_rate / 100.0
        lines.append(f"  {'Tax (' + str(tax_rate) + '%)':>49s} {format_currency(tax):>12s}")
        subtotal += tax
    lines.append("  " + "=" * 63)
    lines.append(f"  {'TOTAL':>49s} {format_currency(subtotal):>12s}")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_012_diff_formatter": {
        "source": """\
def compute_diff_lines(old_lines, new_lines):
    # Simple line-by-line diff
    result = []
    max_len = max(len(old_lines), len(new_lines))
    for i in range(max_len):
        old = old_lines[i] if i < len(old_lines) else None
        new = new_lines[i] if i < len(new_lines) else None
        if old == new:
            result.append((" ", old))
        elif old is None:
            result.append(("+", new))
        elif new is None:
            result.append(("-", old))
        else:
            result.append(("-", old))
            result.append(("+", new))
    return result


def format_unified_diff(old_name, new_name, diff_lines):
    lines = []
    lines.append(f"--- {old_name}")
    lines.append(f"+++ {new_name}")
    added = sum(1 for op, _ in diff_lines if op == "+")
    removed = sum(1 for op, _ in diff_lines if op == "-")
    lines.append(f"@@ -{removed} +{added} @@")
    for op, text in diff_lines:
        if op == " ":
            lines.append(f" {text}")
        elif op == "+":
            lines.append(f"+{text}")
        elif op == "-":
            lines.append(f"-{text}")
    return "\\n".join(lines)


def format_side_by_side(old_lines, new_lines, width=40):
    lines = []
    separator = " | "
    header = "OLD".center(width) + separator + "NEW".center(width)
    lines.append(header)
    lines.append("-" * width + "-+-" + "-" * width)
    max_len = max(len(old_lines), len(new_lines))
    for i in range(max_len):
        old = old_lines[i] if i < len(old_lines) else ""
        new = new_lines[i] if i < len(new_lines) else ""
        old_display = old[:width].ljust(width)
        new_display = new[:width].ljust(width)
        lines.append(f"{old_display}{separator}{new_display}")
    return "\\n".join(lines)


def diff_summary(diff_lines):
    added = sum(1 for op, _ in diff_lines if op == "+")
    removed = sum(1 for op, _ in diff_lines if op == "-")
    unchanged = sum(1 for op, _ in diff_lines if op == " ")
    total = added + removed + unchanged
    return f"{added} additions, {removed} deletions, {unchanged} unchanged ({total} total)"
""",
        "category": "io_like",
    },

    "io_013_tree_view": {
        "source": """\
def tree_prefix(depth, is_last_at_depth):
    if depth == 0:
        return ""
    parts = []
    for i in range(depth - 1):
        if i < len(is_last_at_depth) and is_last_at_depth[i]:
            parts.append("    ")
        else:
            parts.append("|   ")
    if is_last_at_depth[depth - 1] if depth - 1 < len(is_last_at_depth) else False:
        parts.append("+-- ")
    else:
        parts.append("|-- ")
    return "".join(parts)


def render_tree_node(name, depth, is_last_at_depth, node_type="file"):
    prefix = tree_prefix(depth, is_last_at_depth)
    icon = "[D]" if node_type == "dir" else "[F]"
    return f"{prefix}{icon} {name}"


def render_tree(tree, depth=0, is_last_at_depth=None):
    if is_last_at_depth is None:
        is_last_at_depth = []
    lines = []
    name = tree.get("name", "")
    node_type = tree.get("type", "file")
    lines.append(render_tree_node(name, depth, is_last_at_depth, node_type))
    children = tree.get("children", [])
    for i, child in enumerate(children):
        is_last = (i == len(children) - 1)
        new_depth_flags = is_last_at_depth + [is_last]
        child_lines = render_tree(child, depth + 1, new_depth_flags)
        lines.extend(child_lines)
    return lines


def build_tree_view(tree, title=None):
    lines = []
    if title:
        lines.append(title)
        lines.append("=" * len(title))
    rendered = render_tree(tree)
    lines.extend(rendered)
    file_count = count_nodes(tree, "file")
    dir_count = count_nodes(tree, "dir")
    lines.append("")
    lines.append(f"{dir_count} directories, {file_count} files")
    return "\\n".join(lines)


def count_nodes(tree, node_type):
    count = 1 if tree.get("type") == node_type else 0
    for child in tree.get("children", []):
        count += count_nodes(child, node_type)
    return count
""",
        "category": "io_like",
    },

    "io_014_toc_builder": {
        "source": """\
def extract_headings(text):
    headings = []
    for line in text.split("\\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = 0
            while level < len(stripped) and stripped[level] == "#":
                level += 1
            title = stripped[level:].strip()
            headings.append((level, title))
    return headings


def heading_to_anchor(title):
    anchor = title.lower()
    anchor = anchor.replace(" ", "-")
    result = ""
    for ch in anchor:
        if ch.isalnum() or ch == "-":
            result += ch
    return result


def build_toc(headings, min_level=1, max_level=4):
    lines = []
    for level, title in headings:
        if level < min_level or level > max_level:
            continue
        indent = "  " * (level - min_level)
        anchor = heading_to_anchor(title)
        lines.append(f"{indent}- [{title}](#{anchor})")
    return "\\n".join(lines)


def build_toc_from_text(text, title="Table of Contents"):
    headings = extract_headings(text)
    lines = []
    lines.append(f"## {title}")
    lines.append("")
    toc = build_toc(headings, min_level=2)
    lines.append(toc)
    return "\\n".join(lines)


def build_numbered_toc(headings, min_level=1):
    counters = [0] * 6
    lines = []
    for level, title in headings:
        if level < min_level:
            continue
        idx = level - min_level
        counters[idx] += 1
        for i in range(idx + 1, len(counters)):
            counters[i] = 0
        num_parts = []
        for i in range(idx + 1):
            num_parts.append(str(counters[i]))
        num_str = ".".join(num_parts)
        indent = "  " * idx
        lines.append(f"{indent}{num_str} {title}")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_015_changelog": {
        "source": """\
def format_version_header(version, date):
    return f"## [{version}] - {date}"


def format_change_entry(change_type, description):
    return f"- **{change_type}**: {description}"


def format_changelog_section(version, date, changes):
    lines = []
    lines.append(format_version_header(version, date))
    lines.append("")
    grouped = {}
    for change_type, description in changes:
        if change_type not in grouped:
            grouped[change_type] = []
        grouped[change_type].append(description)
    order = ["Added", "Changed", "Fixed", "Removed", "Deprecated", "Security"]
    for category in order:
        if category in grouped:
            lines.append(f"### {category}")
            for desc in grouped[category]:
                lines.append(f"- {desc}")
            lines.append("")
    return "\\n".join(lines)


def build_full_changelog(releases, project_name):
    lines = []
    lines.append(f"# Changelog - {project_name}")
    lines.append("")
    lines.append("All notable changes to this project will be documented here.")
    lines.append("")
    for version, date, changes in releases:
        section = format_changelog_section(version, date, changes)
        lines.append(section)
    return "\\n".join(lines)


def compare_versions(v1, v2):
    parts1 = [int(x) for x in v1.split(".")]
    parts2 = [int(x) for x in v2.split(".")]
    max_len = max(len(parts1), len(parts2))
    for i in range(max_len):
        p1 = parts1[i] if i < len(parts1) else 0
        p2 = parts2[i] if i < len(parts2) else 0
        if p1 < p2:
            return -1
        if p1 > p2:
            return 1
    return 0
""",
        "category": "io_like",
    },

    "io_016_badge_builder": {
        "source": """\
def url_encode(text):
    result = ""
    safe_chars = set("abcdefghijklmnopqrstuvwxyz"
                     "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "0123456789-_.")
    for ch in text:
        if ch in safe_chars:
            result += ch
        elif ch == " ":
            result += "%20"
        else:
            hex_val = format(ord(ch), "02X")
            result += f"%{hex_val}"
    return result


def build_shield_url(label, message, color="blue", style="flat"):
    encoded_label = url_encode(label)
    encoded_message = url_encode(message)
    base = "https://img.shields.io/badge"
    url = f"{base}/{encoded_label}-{encoded_message}-{color}"
    if style != "flat":
        url += f"?style={style}"
    return url


def build_badge_markdown(label, message, color="blue", link=None):
    url = build_shield_url(label, message, color)
    img = f"![{label}]({url})"
    if link:
        return f"[{img}]({link})"
    return img


def build_badge_collection(badges):
    parts = []
    for badge in badges:
        label = badge.get("label", "")
        message = badge.get("message", "")
        color = badge.get("color", "blue")
        link = badge.get("link")
        md = build_badge_markdown(label, message, color, link)
        parts.append(md)
    return " ".join(parts)


def build_status_badges(statuses):
    color_map = {"passing": "green", "failing": "red",
                 "pending": "yellow", "unknown": "grey"}
    badges = []
    for name, status in statuses.items():
        color = color_map.get(status, "grey")
        badges.append({"label": name, "message": status, "color": color})
    return build_badge_collection(badges)
""",
        "category": "io_like",
    },

    "io_017_config_writer": {
        "source": """\
def format_ini_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if " " in text or "=" in text or "#" in text:
        return f'"{text}"'
    return text


def build_ini_section(section_name, entries):
    lines = []
    lines.append(f"[{section_name}]")
    for key, value in entries.items():
        formatted = format_ini_value(value)
        lines.append(f"{key} = {formatted}")
    return "\\n".join(lines)


def build_ini_file(sections, header_comment=None):
    parts = []
    if header_comment:
        for line in header_comment.split("\\n"):
            parts.append(f"; {line}")
        parts.append("")
    for name, entries in sections.items():
        parts.append(build_ini_section(name, entries))
        parts.append("")
    return "\\n".join(parts)


def build_env_file(variables, comments=None):
    lines = []
    if comments:
        for comment in comments:
            lines.append(f"# {comment}")
        lines.append("")
    for key, value in variables.items():
        text = str(value)
        if " " in text or "'" in text:
            text = f'"{text}"'
        lines.append(f"{key}={text}")
    return "\\n".join(lines)


def build_yaml_like(data, indent=0):
    lines = []
    prefix = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(build_yaml_like(value, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(build_yaml_like(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },
    "io_018_test_report": {
        "source": """\
def format_test_status(status):
    icons = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "error": "ERR!"}
    return icons.get(status, "????")


def format_test_result(name, status, duration_ms, message=None):
    icon = format_test_status(status)
    line = f"  [{icon}] {name:50s} ({duration_ms:6.1f}ms)"
    if message and status != "pass":
        line += f"\\n         {message}"
    return line


def format_test_suite(suite_name, results):
    lines = []
    lines.append(f"\\n  Suite: {suite_name}")
    lines.append("  " + "-" * 70)
    passed = 0
    failed = 0
    skipped = 0
    total_ms = 0.0
    for result in results:
        name = result["name"]
        status = result["status"]
        duration = result.get("duration_ms", 0.0)
        message = result.get("message")
        lines.append(format_test_result(name, status, duration, message))
        total_ms += duration
        if status == "pass":
            passed += 1
        elif status == "fail" or status == "error":
            failed += 1
        else:
            skipped += 1
    lines.append("  " + "-" * 70)
    total = passed + failed + skipped
    lines.append(f"  {passed}/{total} passed, {failed} failed, {skipped} skipped")
    lines.append(f"  Total time: {total_ms:.1f}ms")
    return "\\n".join(lines)


def build_test_report(suites, report_title="Test Report"):
    lines = []
    lines.append("=" * 74)
    lines.append(f"  {report_title}")
    lines.append("=" * 74)
    all_passed = 0
    all_failed = 0
    all_skipped = 0
    for suite_name, results in suites:
        lines.append(format_test_suite(suite_name, results))
        for r in results:
            if r["status"] == "pass":
                all_passed += 1
            elif r["status"] in ("fail", "error"):
                all_failed += 1
            else:
                all_skipped += 1
    lines.append("")
    lines.append("=" * 74)
    total = all_passed + all_failed + all_skipped
    lines.append(f"  TOTAL: {all_passed}/{total} passed, {all_failed} failed")
    verdict = "PASS" if all_failed == 0 else "FAIL"
    lines.append(f"  Verdict: {verdict}")
    lines.append("=" * 74)
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_019_api_docs": {
        "source": """\
def format_param(name, param_type, required, description):
    req_str = "required" if required else "optional"
    return f"  - `{name}` ({param_type}, {req_str}): {description}"


def format_endpoint(method, path, summary, params=None, response_example=None):
    lines = []
    lines.append(f"### {method.upper()} `{path}`")
    lines.append("")
    lines.append(summary)
    lines.append("")
    if params:
        lines.append("**Parameters:**")
        lines.append("")
        for param in params:
            lines.append(format_param(
                param["name"], param["type"],
                param.get("required", False), param["description"]
            ))
        lines.append("")
    if response_example:
        lines.append("**Response:**")
        lines.append("")
        lines.append("```json")
        lines.append(response_example)
        lines.append("```")
        lines.append("")
    return "\\n".join(lines)


def build_api_docs(title, base_url, endpoints, version="1.0"):
    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"Base URL: `{base_url}`")
    lines.append(f"Version: {version}")
    lines.append("")
    lines.append("---")
    lines.append("")
    grouped = {}
    for ep in endpoints:
        tag = ep.get("tag", "General")
        if tag not in grouped:
            grouped[tag] = []
        grouped[tag].append(ep)
    for tag, eps in grouped.items():
        lines.append(f"## {tag}")
        lines.append("")
        for ep in eps:
            section = format_endpoint(
                ep["method"], ep["path"], ep["summary"],
                ep.get("params"), ep.get("response_example")
            )
            lines.append(section)
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_020_sitemap_xml": {
        "source": """\
def xml_escape(text):
    result = str(text)
    result = result.replace("&", "&amp;")
    result = result.replace("<", "&lt;")
    result = result.replace(">", "&gt;")
    result = result.replace("'", "&apos;")
    result = result.replace('"', "&quot;")
    return result


def build_url_entry(loc, lastmod=None, changefreq=None, priority=None):
    lines = []
    lines.append("  <url>")
    lines.append(f"    <loc>{xml_escape(loc)}</loc>")
    if lastmod:
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
    if changefreq:
        lines.append(f"    <changefreq>{changefreq}</changefreq>")
    if priority is not None:
        lines.append(f"    <priority>{priority:.1f}</priority>")
    lines.append("  </url>")
    return "\\n".join(lines)


def build_sitemap(urls, xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"):
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<urlset xmlns="{xmlns}">')
    for url_data in urls:
        loc = url_data["loc"]
        lastmod = url_data.get("lastmod")
        changefreq = url_data.get("changefreq")
        priority = url_data.get("priority")
        entry = build_url_entry(loc, lastmod, changefreq, priority)
        lines.append(entry)
    lines.append("</urlset>")
    return "\\n".join(lines)


def build_sitemap_index(sitemaps, xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"):
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<sitemapindex xmlns="{xmlns}">')
    for sm in sitemaps:
        lines.append("  <sitemap>")
        lines.append(f"    <loc>{xml_escape(sm['loc'])}</loc>")
        if "lastmod" in sm:
            lines.append(f"    <lastmod>{sm['lastmod']}</lastmod>")
        lines.append("  </sitemap>")
    lines.append("</sitemapindex>")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_021_rss_feed": {
        "source": """\
def rss_escape(text):
    result = str(text)
    result = result.replace("&", "&amp;")
    result = result.replace("<", "&lt;")
    result = result.replace(">", "&gt;")
    return result


def build_rss_item(title, link, description, pub_date=None, guid=None):
    lines = []
    lines.append("    <item>")
    lines.append(f"      <title>{rss_escape(title)}</title>")
    lines.append(f"      <link>{rss_escape(link)}</link>")
    lines.append(f"      <description>{rss_escape(description)}</description>")
    if pub_date:
        lines.append(f"      <pubDate>{pub_date}</pubDate>")
    if guid:
        lines.append(f"      <guid>{rss_escape(guid)}</guid>")
    else:
        lines.append(f"      <guid>{rss_escape(link)}</guid>")
    lines.append("    </item>")
    return "\\n".join(lines)


def build_rss_channel(title, link, description, items, language="en"):
    lines = []
    lines.append("    <channel>")
    lines.append(f"      <title>{rss_escape(title)}</title>")
    lines.append(f"      <link>{rss_escape(link)}</link>")
    lines.append(f"      <description>{rss_escape(description)}</description>")
    lines.append(f"      <language>{language}</language>")
    for item in items:
        item_xml = build_rss_item(
            item["title"], item["link"], item["description"],
            item.get("pub_date"), item.get("guid")
        )
        lines.append(item_xml)
    lines.append("    </channel>")
    return "\\n".join(lines)


def build_rss_feed(channel_info, items):
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<rss version="2.0">')
    channel = build_rss_channel(
        channel_info["title"], channel_info["link"],
        channel_info["description"], items,
        channel_info.get("language", "en")
    )
    lines.append(channel)
    lines.append("</rss>")
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_022_calendar": {
        "source": """\
def days_in_month(year, month):
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    if year % 400 == 0:
        return 29
    if year % 100 == 0:
        return 28
    if year % 4 == 0:
        return 29
    return 28


def day_of_week(year, month, day):
    # Zeller's congruence (0=Saturday, 1=Sunday, ... 6=Friday)
    if month < 3:
        month += 12
        year -= 1
    k = year % 100
    j = year // 100
    h = (day + (13 * (month + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7
    # Convert to 0=Monday ... 6=Sunday
    return (h + 5) % 7


def format_month_calendar(year, month):
    month_names = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
    lines = []
    title = f"{month_names[month]} {year}"
    lines.append(title.center(20))
    lines.append("Mo Tu We Th Fr Sa Su")
    first_day = day_of_week(year, month, 1)
    num_days = days_in_month(year, month)
    line = "   " * first_day
    current_weekday = first_day
    for day in range(1, num_days + 1):
        line += f"{day:2d} "
        current_weekday += 1
        if current_weekday == 7:
            lines.append(line.rstrip())
            line = ""
            current_weekday = 0
    if line.strip():
        lines.append(line.rstrip())
    return "\\n".join(lines)


def format_year_calendar(year):
    lines = []
    lines.append(f"Calendar for {year}")
    lines.append("=" * 20)
    for month in range(1, 13):
        lines.append("")
        lines.append(format_month_calendar(year, month))
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_023_receipt": {
        "source": """\
def format_receipt_line(name, price, quantity=1):
    total = price * quantity
    if quantity > 1:
        name_part = f"{name} x{quantity}"
    else:
        name_part = name
    price_str = f"${total:.2f}"
    padding = 40 - len(name_part) - len(price_str)
    if padding < 1:
        padding = 1
    return name_part + "." * padding + price_str


def format_receipt_separator(width=40, char="-"):
    return char * width


def build_receipt(store_name, items, tax_rate=0.0, payment_method="Cash"):
    lines = []
    lines.append(store_name.center(40))
    lines.append(format_receipt_separator(40, "="))
    lines.append("")
    subtotal = 0.0
    for item in items:
        name = item["name"]
        price = item["price"]
        qty = item.get("quantity", 1)
        lines.append(format_receipt_line(name, price, qty))
        subtotal += price * qty
    lines.append("")
    lines.append(format_receipt_separator())
    lines.append(format_receipt_line("Subtotal", subtotal))
    if tax_rate > 0:
        tax = subtotal * tax_rate / 100.0
        lines.append(format_receipt_line(f"Tax ({tax_rate}%)", tax))
        total = subtotal + tax
    else:
        total = subtotal
    lines.append(format_receipt_separator(40, "="))
    lines.append(format_receipt_line("TOTAL", total))
    lines.append("")
    lines.append(f"Payment: {payment_method}")
    lines.append("")
    lines.append("Thank you for your purchase!".center(40))
    return "\\n".join(lines)


def build_receipt_with_discounts(store_name, items, discounts, tax_rate):
    lines = []
    lines.append(store_name.center(40))
    lines.append(format_receipt_separator(40, "="))
    subtotal = 0.0
    for item in items:
        name = item["name"]
        price = item["price"]
        qty = item.get("quantity", 1)
        lines.append(format_receipt_line(name, price, qty))
        subtotal += price * qty
    discount_total = 0.0
    for disc in discounts:
        desc = disc["description"]
        amount = disc["amount"]
        discount_total += amount
        lines.append(format_receipt_line(f"  DISCOUNT: {desc}", -amount))
    after_discount = subtotal - discount_total
    tax = after_discount * tax_rate / 100.0
    total = after_discount + tax
    lines.append(format_receipt_separator(40, "="))
    lines.append(format_receipt_line("TOTAL", total))
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_024_status_dashboard": {
        "source": """\
def status_indicator(status):
    indicators = {
        "healthy": "[OK]",
        "degraded": "[!!]",
        "down": "[XX]",
        "unknown": "[??]",
    }
    return indicators.get(status, "[??]")


def format_service_status(name, status, latency_ms=None, uptime_pct=None):
    indicator = status_indicator(status)
    line = f"  {indicator} {name:25s}"
    if latency_ms is not None:
        line += f"  {latency_ms:6.0f}ms"
    if uptime_pct is not None:
        line += f"  {uptime_pct:5.1f}%"
    return line


def build_dashboard_section(title, services):
    lines = []
    lines.append(f"  {title}")
    lines.append("  " + "-" * 55)
    for svc in services:
        line = format_service_status(
            svc["name"], svc["status"],
            svc.get("latency_ms"), svc.get("uptime_pct")
        )
        lines.append(line)
    return "\\n".join(lines)


def build_status_dashboard(sections, timestamp=None):
    lines = []
    lines.append("=" * 59)
    lines.append("  STATUS DASHBOARD")
    if timestamp:
        lines.append(f"  Last updated: {timestamp}")
    lines.append("=" * 59)
    lines.append("")
    total_services = 0
    healthy_count = 0
    for title, services in sections:
        section = build_dashboard_section(title, services)
        lines.append(section)
        lines.append("")
        for svc in services:
            total_services += 1
            if svc["status"] == "healthy":
                healthy_count += 1
    lines.append("=" * 59)
    health_pct = (healthy_count / total_services * 100) if total_services else 0
    lines.append(f"  Overall: {healthy_count}/{total_services} healthy ({health_pct:.0f}%)")
    overall = "HEALTHY" if healthy_count == total_services else "DEGRADED"
    lines.append(f"  Status: {overall}")
    lines.append("=" * 59)
    return "\\n".join(lines)
""",
        "category": "io_like",
    },

    "io_025_metric_summary": {
        "source": """\
def format_metric_value(value, unit=""):
    if isinstance(value, float):
        formatted = f"{value:,.2f}"
    else:
        formatted = f"{value:,}"
    if unit:
        return f"{formatted} {unit}"
    return formatted


def format_metric_row(name, value, unit="", trend=None):
    val_str = format_metric_value(value, unit)
    line = f"  {name:30s} {val_str:>15s}"
    if trend is not None:
        if trend > 0:
            line += f"  (+{trend:.1f}%)"
        elif trend < 0:
            line += f"  ({trend:.1f}%)"
        else:
            line += "  (  0.0%)"
    return line


def build_metric_group(title, metrics):
    lines = []
    lines.append(f"  {title}")
    lines.append("  " + "-" * 60)
    for metric in metrics:
        line = format_metric_row(
            metric["name"], metric["value"],
            metric.get("unit", ""), metric.get("trend")
        )
        lines.append(line)
    return "\\n".join(lines)


def build_metric_summary(title, groups, period="Last 30 days"):
    lines = []
    lines.append("=" * 64)
    lines.append(f"  {title}")
    lines.append(f"  Period: {period}")
    lines.append("=" * 64)
    lines.append("")
    for group_title, metrics in groups:
        group = build_metric_group(group_title, metrics)
        lines.append(group)
        lines.append("")
    total_metrics = sum(len(m) for _, m in groups)
    lines.append("=" * 64)
    lines.append(f"  {total_metrics} metrics reported")
    lines.append("=" * 64)
    return "\\n".join(lines)


def format_sparkline(values, width=20):
    if not values:
        return ""
    min_val = min(values)
    max_val = max(values)
    span = max_val - min_val if max_val != min_val else 1
    chars = " _.-~*"
    result = ""
    for v in values[-width:]:
        idx = int((v - min_val) / span * (len(chars) - 1))
        result += chars[idx]
    return result
""",
        "category": "io_like",
    },
}

# ---------------------------------------------------------------------------
# Validate all 100 programs
# ---------------------------------------------------------------------------

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

assert len(PROGRAMS) == 100, f"Expected 100 programs, got {len(PROGRAMS)}"
for _cat in ("pure", "exception", "stateful", "io_like"):
    _count = sum(1 for p in PROGRAMS.values() if p["category"] == _cat)
    assert _count == 25, f"Expected 25 {_cat} programs, got {_count}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


# ---------------------------------------------------------------------------
# §1  AST-based effect detection (baseline) on all 100 programs
# ---------------------------------------------------------------------------

def experiment_1_ast_detection(results: dict) -> dict:
    section("§1  AST-based effect detection (baseline)")

    EXPECTED_EFFECTS = {
        "pure": set(),
        "exception": {"exception"},
        "stateful": {"mutable_state"},
        "io_like": set(),  # string formatting is not an AST-detectable effect
    }

    total = 0
    correct = 0
    rows = []

    for pname, pdata in PROGRAMS.items():
        source = pdata["source"]
        category = pdata["category"]
        expected = EXPECTED_EFFECTS[category]

        detected = detect_effects(source)
        detected_set = {f for f in EFFECT_FAMILIES if detected[f]}

        # Check if expected effects are present
        match = expected <= detected_set  # expected is subset of detected
        total += 1
        if match:
            correct += 1

        rows.append({
            "program": pname,
            "category": category,
            "expected_effects": sorted(expected),
            "detected_effects": sorted(detected_set),
            "match": match,
        })

        status = "OK" if match else "MISMATCH"
        print(f"  {pname:40s}  [{category:10s}]  {status}  detected={sorted(detected_set)}")

    accuracy = correct / total if total else 0

    print(f"\n  Detection accuracy: {correct}/{total} = {accuracy:.1%}")

    results["§1_ast_detection"] = {
        "programs": rows,
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "total": total,
    }
    return {r["program"]: r for r in rows}


# ---------------------------------------------------------------------------
# §2  jugeo prove on all 100 programs
# ---------------------------------------------------------------------------

def experiment_2_prove_all(results: dict, temp_files: list) -> dict:
    section("§2  jugeo prove on all 100 programs")

    prove_rows = {}
    i = 0
    for pname, pdata in PROGRAMS.items():
        i += 1
        path = write_temp_py(pdata["source"])
        temp_files.append(path)

        t0 = time.perf_counter()
        objs = run_jugeo("prove", path)
        elapsed = time.perf_counter() - t0

        prove = objs[0] if objs else {}
        files = prove.get("files", [{}])
        f0 = files[0] if files else {}

        row = {
            "category": pdata["category"],
            "verdict": f0.get("verdict", "unknown"),
            "trust": f0.get("trust", "unknown"),
            "coordinates": f0.get("coordinates", 0),
            "propositions_total": f0.get("propositions_total", 0),
            "propositions_ok": f0.get("propositions_ok", 0),
            "obstructions": len(f0.get("obstructions", [])),
            "elapsed_s": round(elapsed, 4),
        }
        prove_rows[pname] = row

        print(f"  [{i:3d}/100] {pname:40s}  verdict={row['verdict']:8s}  "
              f"coords={row['coordinates']:3d}  props={row['propositions_total']:3d}  "
              f"[{pdata['category']}]")

    results["§2_prove_all"] = prove_rows
    return prove_rows


# ---------------------------------------------------------------------------
# §3  Effect comparison — pure vs effectful
# ---------------------------------------------------------------------------

def experiment_3_effect_comparison(results: dict, prove_rows: dict) -> None:
    section("§3  Effect comparison — pure vs effectful")

    categories = ["pure", "exception", "stateful", "io_like"]
    stats = {}

    for cat in categories:
        programs_in_cat = {k: v for k, v in prove_rows.items() if v["category"] == cat}
        coords = [v["coordinates"] for v in programs_in_cat.values()]
        props = [v["propositions_total"] for v in programs_in_cat.values()]

        avg_coords = sum(coords) / len(coords) if coords else 0
        avg_props = sum(props) / len(props) if props else 0

        stats[cat] = {
            "count": len(programs_in_cat),
            "avg_coordinates": round(avg_coords, 2),
            "avg_propositions": round(avg_props, 2),
            "min_coordinates": min(coords) if coords else 0,
            "max_coordinates": max(coords) if coords else 0,
            "min_propositions": min(props) if props else 0,
            "max_propositions": max(props) if props else 0,
        }

    # Print comparison table
    print(f"\n  {'Category':12s}  {'Count':>5s}  {'Avg Coords':>11s}  {'Avg Props':>10s}  {'Ratio vs Pure':>14s}")
    print(f"  {'-'*12}  {'-'*5}  {'-'*11}  {'-'*10}  {'-'*14}")

    pure_coords = stats["pure"]["avg_coordinates"]
    pure_props = stats["pure"]["avg_propositions"]

    for cat in categories:
        s = stats[cat]
        if pure_coords > 0:
            ratio = f"{s['avg_coordinates'] / pure_coords:.2f}x"
        else:
            ratio = "N/A"
        print(f"  {cat:12s}  {s['count']:5d}  {s['avg_coordinates']:11.2f}  "
              f"{s['avg_propositions']:10.2f}  {ratio:>14s}")

    # Verify claim: effectful code generates more coordinates than pure code
    effectful_cats = ["exception", "stateful", "io_like"]
    effectful_coords = []
    for cat in effectful_cats:
        programs_in_cat = {k: v for k, v in prove_rows.items() if v["category"] == cat}
        effectful_coords.extend([v["coordinates"] for v in programs_in_cat.values()])

    avg_effectful = sum(effectful_coords) / len(effectful_coords) if effectful_coords else 0
    claim_holds = avg_effectful > pure_coords

    print(f"\n  Pure avg coordinates:     {pure_coords:.2f}")
    print(f"  Effectful avg coordinates: {avg_effectful:.2f}")
    print(f"  Claim (effectful > pure):  {claim_holds}")

    results["§3_effect_comparison"] = {
        "per_category": stats,
        "pure_avg_coordinates": round(pure_coords, 2),
        "effectful_avg_coordinates": round(avg_effectful, 2),
        "claim_effectful_gt_pure": claim_holds,
    }


# ---------------------------------------------------------------------------
# §4  Aggregate statistics
# ---------------------------------------------------------------------------

def experiment_4_aggregate(results: dict, prove_rows: dict) -> None:
    section("§4  Aggregate statistics")

    all_coords = [v["coordinates"] for v in prove_rows.values()]
    all_props = [v["propositions_total"] for v in prove_rows.values()]
    all_ok = [v["propositions_ok"] for v in prove_rows.values()]

    overall = {
        "total_programs": len(prove_rows),
        "avg_coordinates": round(sum(all_coords) / len(all_coords), 2) if all_coords else 0,
        "avg_propositions": round(sum(all_props) / len(all_props), 2) if all_props else 0,
        "avg_propositions_ok": round(sum(all_ok) / len(all_ok), 2) if all_ok else 0,
        "total_coordinates": sum(all_coords),
        "total_propositions": sum(all_props),
        "min_coordinates": min(all_coords) if all_coords else 0,
        "max_coordinates": max(all_coords) if all_coords else 0,
    }

    # Verdict distribution
    verdict_dist = defaultdict(int)
    for v in prove_rows.values():
        verdict_dist[v["verdict"]] += 1

    # Which category has the most geometric complexity?
    categories = ["pure", "exception", "stateful", "io_like"]
    category_complexity = {}
    for cat in categories:
        cat_coords = [v["coordinates"] for v in prove_rows.values() if v["category"] == cat]
        cat_props = [v["propositions_total"] for v in prove_rows.values() if v["category"] == cat]
        total = sum(cat_coords)
        category_complexity[cat] = {
            "total_coordinates": total,
            "total_propositions": sum(cat_props),
            "avg_coordinates": round(total / len(cat_coords), 2) if cat_coords else 0,
        }

    most_complex = max(category_complexity.items(), key=lambda x: x[1]["total_coordinates"])

    print(f"  Overall averages:")
    print(f"    coordinates:    {overall['avg_coordinates']:.2f}")
    print(f"    propositions:   {overall['avg_propositions']:.2f}")
    print(f"    props OK:       {overall['avg_propositions_ok']:.2f}")
    print(f"  Total coordinates: {overall['total_coordinates']}")
    print(f"  Total propositions: {overall['total_propositions']}")
    print(f"\n  Verdict distribution: {dict(verdict_dist)}")
    print(f"\n  Most geometrically complex category: {most_complex[0]}")
    print(f"    total coords: {most_complex[1]['total_coordinates']}")

    results["§4_aggregate"] = {
        "overall": overall,
        "verdict_distribution": dict(verdict_dist),
        "category_complexity": category_complexity,
        "most_complex_category": most_complex[0],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("Experiment 07 — Verifying Effectful Python Without Leaving Python")
    print(f"  100 programs: 25 pure, 25 exception, 25 stateful, 25 io_like")
    print("=" * 72)

    t0 = time.perf_counter()
    results = {}
    temp_files = []

    try:
        experiment_1_ast_detection(results)
        prove_rows = experiment_2_prove_all(results, temp_files)
        experiment_3_effect_comparison(results, prove_rows)
        experiment_4_aggregate(results, prove_rows)

        elapsed = time.perf_counter() - t0

        output = {
            "experiment": "python_effects",
            "paper": 7,
            "note": (
                "All JuGeo numbers from CLI calls (jugeo prove).  "
                "Effect detection uses Python ast on 100 real programs.  "
                "Categories: pure, exception, stateful, io_like (25 each)."
            ),
            "random_seed": 42,
            "total_elapsed_s": round(elapsed, 4),
            **results,
        }

        outpath = os.path.join(os.path.dirname(__file__), "results_paper07.json")
        with open(outpath, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  total elapsed: {elapsed:.3f}s")
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
