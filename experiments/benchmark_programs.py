"""Benchmark programs for JuGeo testing."""


PROGRAMS = [
    {
        "id": 'arith-001',
        "category": 'pure_arithmetic',
        "lines": 16,
        "description": 'Overflow-safe integer arithmetic',
        "code": r'''







def safe_add(x, y, max_val=2**31 - 1, min_val=-(2**31)):
    """Add two numbers with overflow protection."""
    result = x + y
    if result > max_val:
        return max_val
    if result < min_val:
        return min_val
    return result


def safe_multiply(x, y, max_val=2**31 - 1, min_val=-(2**31)):
    """Multiply two numbers with overflow protection."""
    result = x * y
    if result > max_val:
        return max_val
    if result < min_val:
        return min_val
    return result







''',
    },
    {
        "id": 'arith-002',
        "category": 'pure_arithmetic',
        "lines": 15,
        "description": 'GCD and LCM',
        "code": r'''







def gcd(a, b):
    """Compute greatest common divisor using Euclidean algorithm."""
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def lcm(a, b):
    """Compute least common multiple."""
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def gcd_of_list(numbers):
    """Compute GCD of a list of numbers."""
    from functools import reduce
    return reduce(gcd, numbers)







''',
    },
    {
        "id": 'arith-003',
        "category": 'pure_arithmetic',
        "lines": 12,
        "description": 'Fast modular exponentiation',
        "code": r'''







def fast_pow(base, exp, mod=None):
    """Compute base**exp using binary exponentiation."""
    if exp < 0:
        raise ValueError("Negative exponent not supported")
    result = 1
    base = base if mod is None else base % mod
    while exp > 0:
        if exp % 2 == 1:
            result = result * base if mod is None else (result * base) % mod
        exp //= 2
        base = base * base if mod is None else (base * base) % mod
    return result







''',
    },
    {
        "id": 'arith-004',
        "category": 'pure_arithmetic',
        "lines": 18,
        "description": 'Fibonacci sequence generator',
        "code": r'''







def fibonacci(n):
    """Return first n Fibonacci numbers."""
    if n <= 0:
        return []
    if n == 1:
        return [0]
    fibs = [0, 1]
    for _ in range(2, n):
        fibs.append(fibs[-1] + fibs[-2])
    return fibs


def fib_nth(n):
    """Return the nth Fibonacci number (0-indexed)."""
    if n < 0:
        raise ValueError("Negative index")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a







''',
    },
    {
        "id": 'arith-005',
        "category": 'pure_arithmetic',
        "lines": 44,
        "description": 'Polynomial evaluation and operations',
        "code": r'''







def poly_eval(coeffs, x):
    """Evaluate polynomial using Horner's method. coeffs[i] is coeff of x^i."""
    result = 0
    for c in reversed(coeffs):
        result = result * x + c
    return result


def poly_add(a, b):
    """Add two polynomials represented as coefficient lists."""
    length = max(len(a), len(b))
    result = [0] * length
    for i in range(len(a)):
        result[i] += a[i]
    for i in range(len(b)):
        result[i] += b[i]
    return result


def poly_multiply(a, b):
    """Multiply two polynomials."""
    if not a or not b:
        return [0]
    result = [0] * (len(a) + len(b) - 1)
    for i, ca in enumerate(a):
        for j, cb in enumerate(b):
            result[i + j] += ca * cb
    return result


def poly_derivative(coeffs):
    """Compute derivative of a polynomial."""
    if len(coeffs) <= 1:
        return [0]
    return [i * coeffs[i] for i in range(1, len(coeffs))]


def poly_to_string(coeffs):
    """Convert polynomial to readable string."""
    if not coeffs:
        return "0"
    terms = []
    for i, c in enumerate(coeffs):
        if c == 0:
            continue
        if i == 0:
            terms.append(str(c))
        elif i == 1:
            terms.append(f"{c}x")
        else:
            terms.append(f"{c}x^{i}")
    return " + ".join(terms) if terms else "0"







''',
    },
    {
        "id": 'arith-006',
        "category": 'pure_arithmetic',
        "lines": 38,
        "description": 'Statistics calculator',
        "code": r'''







import math


def mean(data):
    """Compute arithmetic mean."""
    if not data:
        raise ValueError("Empty dataset")
    return sum(data) / len(data)


def median(data):
    """Compute median value."""
    if not data:
        raise ValueError("Empty dataset")
    sorted_data = sorted(data)
    n = len(sorted_data)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_data[mid - 1] + sorted_data[mid]) / 2.0
    return sorted_data[mid]


def variance(data, population=True):
    """Compute variance (population or sample)."""
    if len(data) < 2:
        raise ValueError("Need at least 2 data points")
    m = mean(data)
    sq_diff = sum((x - m) ** 2 for x in data)
    divisor = len(data) if population else len(data) - 1
    return sq_diff / divisor


def stdev(data, population=True):
    """Compute standard deviation."""
    return math.sqrt(variance(data, population))


def percentile(data, p):
    """Compute the p-th percentile (0-100)."""
    if not data:
        raise ValueError("Empty dataset")
    if not 0 <= p <= 100:
        raise ValueError("Percentile must be 0-100")
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])







''',
    },
    {
        "id": 'arith-007',
        "category": 'pure_arithmetic',
        "lines": 47,
        "description": 'Fraction arithmetic class',
        "code": r'''







def _gcd(a, b):
    """Compute GCD for fraction simplification."""
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


class Fraction:
    """Immutable fraction with arithmetic operations."""

    def __init__(self, numerator, denominator=1):
        if denominator == 0:
            raise ZeroDivisionError("Denominator cannot be zero")
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
        g = _gcd(abs(numerator), denominator)
        self.num = numerator // g
        self.den = denominator // g

    def __add__(self, other):
        if isinstance(other, int):
            other = Fraction(other)
        return Fraction(self.num * other.den + other.num * self.den,
                        self.den * other.den)

    def __sub__(self, other):
        if isinstance(other, int):
            other = Fraction(other)
        return Fraction(self.num * other.den - other.num * self.den,
                        self.den * other.den)

    def __mul__(self, other):
        if isinstance(other, int):
            other = Fraction(other)
        return Fraction(self.num * other.num, self.den * other.den)

    def __truediv__(self, other):
        if isinstance(other, int):
            other = Fraction(other)
        if other.num == 0:
            raise ZeroDivisionError("Division by zero fraction")
        return Fraction(self.num * other.den, self.den * other.num)

    def __eq__(self, other):
        if isinstance(other, int):
            other = Fraction(other)
        return self.num == other.num and self.den == other.den

    def __repr__(self):
        if self.den == 1:
            return f"Fraction({self.num})"
        return f"Fraction({self.num}, {self.den})"

    def to_float(self):
        """Convert to floating point."""
        return self.num / self.den







''',
    },
    {
        "id": 'arith-008',
        "category": 'pure_arithmetic',
        "lines": 71,
        "description": 'Matrix class with operations',
        "code": r'''







class Matrix:
    """Matrix class with basic linear algebra operations."""

    def __init__(self, data):
        """Initialize from list of lists."""
        self.data = [row[:] for row in data]
        self.rows = len(data)
        self.cols = len(data[0]) if data else 0

    def __getitem__(self, idx):
        return self.data[idx]

    def __repr__(self):
        rows_str = ", ".join(str(row) for row in self.data)
        return f"Matrix([{rows_str}])"

    @classmethod
    def identity(cls, n):
        """Create n x n identity matrix."""
        data = [[1 if i == j else 0 for j in range(n)] for i in range(n)]
        return cls(data)

    @classmethod
    def zeros(cls, rows, cols):
        """Create a zero matrix."""
        return cls([[0] * cols for _ in range(rows)])

    def transpose(self):
        """Return transposed matrix."""
        data = [[self.data[i][j] for i in range(self.rows)] for j in range(self.cols)]
        return Matrix(data)

    def __add__(self, other):
        """Add two matrices."""
        if self.rows != other.rows or self.cols != other.cols:
            raise ValueError("Matrix dimensions must match for addition")
        data = [[self.data[i][j] + other.data[i][j]
                 for j in range(self.cols)] for i in range(self.rows)]
        return Matrix(data)

    def __sub__(self, other):
        """Subtract two matrices."""
        if self.rows != other.rows or self.cols != other.cols:
            raise ValueError("Matrix dimensions must match for subtraction")
        data = [[self.data[i][j] - other.data[i][j]
                 for j in range(self.cols)] for i in range(self.rows)]
        return Matrix(data)

    def __mul__(self, other):
        """Multiply two matrices."""
        if isinstance(other, (int, float)):
            data = [[self.data[i][j] * other
                     for j in range(self.cols)] for i in range(self.rows)]
            return Matrix(data)
        if self.cols != other.rows:
            raise ValueError("Incompatible dimensions for multiplication")
        data = [[sum(self.data[i][k] * other.data[k][j] for k in range(self.cols))
                 for j in range(other.cols)] for i in range(self.rows)]
        return Matrix(data)

    def trace(self):
        """Compute trace (sum of diagonal elements)."""
        if self.rows != self.cols:
            raise ValueError("Trace is only defined for square matrices")
        return sum(self.data[i][i] for i in range(self.rows))

    def determinant(self):
        """Compute determinant using cofactor expansion."""
        if self.rows != self.cols:
            raise ValueError("Determinant is only defined for square matrices")
        n = self.rows
        if n == 1:
            return self.data[0][0]
        if n == 2:
            return self.data[0][0] * self.data[1][1] - self.data[0][1] * self.data[1][0]
        det = 0
        for j in range(n):
            minor = [[self.data[r][c] for c in range(n) if c != j]
                     for r in range(1, n)]
            cofactor = ((-1) ** j) * Matrix(minor).determinant()
            det += self.data[0][j] * cofactor
        return det







''',
    },
    {
        "id": 'arith-009',
        "category": 'pure_arithmetic',
        "lines": 58,
        "description": 'Number base conversion utilities',
        "code": r'''







def to_base(number, base):
    """Convert a non-negative integer to a given base (2-36)."""
    if base < 2 or base > 36:
        raise ValueError("Base must be between 2 and 36")
    if number == 0:
        return [0]
    digits = []
    is_negative = number < 0
    number = abs(number)
    while number > 0:
        digits.append(number % base)
        number //= base
    if is_negative:
        digits.append(-1)
    return list(reversed(digits))


def from_base(digits, base):
    """Convert a list of digits in a given base to an integer."""
    if base < 2 or base > 36:
        raise ValueError("Base must be between 2 and 36")
    result = 0
    for d in digits:
        if d < 0 or d >= base:
            raise ValueError(f"Invalid digit {d} for base {base}")
        result = result * base + d
    return result


DIGIT_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def to_string(number, base):
    """Convert number to string representation in given base."""
    if number == 0:
        return "0"
    is_negative = number < 0
    number = abs(number)
    chars = []
    while number > 0:
        chars.append(DIGIT_CHARS[number % base])
        number //= base
    if is_negative:
        chars.append("-")
    return "".join(reversed(chars))


def from_string(s, base):
    """Parse a string as a number in the given base."""
    s = s.strip().upper()
    if not s:
        raise ValueError("Empty string")
    is_negative = s[0] == "-"
    if is_negative:
        s = s[1:]
    result = 0
    for ch in s:
        idx = DIGIT_CHARS.index(ch)
        if idx >= base:
            raise ValueError(f"Invalid character '{ch}' for base {base}")
        result = result * base + idx
    return -result if is_negative else result


def convert_base(number_str, from_base_val, to_base_val):
    """Convert a number string from one base to another."""
    decimal = from_string(number_str, from_base_val)
    return to_string(decimal, to_base_val)







''',
    },
    {
        "id": 'arith-010',
        "category": 'pure_arithmetic',
        "lines": 100,
        "description": 'Numerical methods library',
        "code": r'''







def newton_sqrt(n, tol=1e-10):
    """Square root via Newton's method."""
    if n < 0:
        raise ValueError("Negative input")
    if n == 0:
        return 0.0
    x = max(n, 1.0)
    for _ in range(200):
        prev = x
        x = (x + n / x) / 2.0
        if abs(x - prev) < tol:
            break
    return x


def bisection(f, a, b, tol=1e-10, max_iter=200):
    """Find root of f in [a, b] using bisection."""
    if f(a) * f(b) > 0:
        raise ValueError("f(a) and f(b) must have different signs")
    for _ in range(max_iter):
        mid = (a + b) / 2.0
        fm = f(mid)
        if abs(fm) < tol or (b - a) / 2.0 < tol:
            return mid
        if f(a) * fm < 0:
            b = mid
        else:
            a = mid
    return (a + b) / 2.0


def trapezoidal(f, a, b, n=1000):
    """Numerical integration using trapezoidal rule."""
    h = (b - a) / n
    result = (f(a) + f(b)) / 2.0
    for i in range(1, n):
        result += f(a + i * h)
    return result * h


def simpson(f, a, b, n=1000):
    """Numerical integration using Simpson's rule."""
    if n % 2 != 0:
        n += 1
    h = (b - a) / n
    result = f(a) + f(b)
    for i in range(1, n):
        coeff = 4 if i % 2 != 0 else 2
        result += coeff * f(a + i * h)
    return result * h / 3.0


def euler_method(f, y0, t0, t_end, steps=100):
    """Solve ODE y' = f(t, y) using Euler's method."""
    h = (t_end - t0) / steps
    t, y = t0, y0
    points = [(t, y)]
    for _ in range(steps):
        y = y + h * f(t, y)
        t = t + h
        points.append((t, y))
    return points


def runge_kutta4(f, y0, t0, t_end, steps=100):
    """Solve ODE y' = f(t, y) using RK4."""
    h = (t_end - t0) / steps
    t, y = t0, y0
    points = [(t, y)]
    for _ in range(steps):
        k1 = h * f(t, y)
        k2 = h * f(t + h / 2, y + k1 / 2)
        k3 = h * f(t + h / 2, y + k2 / 2)
        k4 = h * f(t + h, y + k3)
        y = y + (k1 + 2 * k2 + 2 * k3 + k4) / 6
        t = t + h
        points.append((t, y))
    return points


def secant_method(f, x0, x1, tol=1e-10, max_iter=200):
    """Find root using secant method."""
    for _ in range(max_iter):
        fx0, fx1 = f(x0), f(x1)
        if abs(fx1) < tol:
            return x1
        denom = fx1 - fx0
        if abs(denom) < 1e-15:
            return x1
        x_new = x1 - fx1 * (x1 - x0) / denom
        x0, x1 = x1, x_new
    return x1


def lagrange_interpolation(points, x):
    """Evaluate interpolating polynomial at x."""
    n = len(points)
    result = 0.0
    for i in range(n):
        xi, yi = points[i]
        term = yi
        for j in range(n):
            if i != j:
                xj = points[j][0]
                term *= (x - xj) / (xi - xj)
        result += term
    return result


def numerical_derivative(f, x, h=1e-7):
    """Central difference numerical derivative."""
    return (f(x + h) - f(x - h)) / (2 * h)






def second_derivative(f, x, h=1e-5):
    """Compute second derivative using central differences."""
    return (f(x + h) - 2 * f(x) + f(x - h)) / (h * h)


DEFAULT_TOLERANCE = 1e-10
''',
    },
    {
        "id": 'str-001',
        "category": 'string_processing',
        "lines": 13,
        "description": 'Caesar cipher encode/decode',
        "code": r'''







def caesar_encode(text, shift):
    """Encode text using Caesar cipher with given shift."""
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            result.append(chr((ord(ch) - base + shift) % 26 + base))
        else:
            result.append(ch)
    return "".join(result)


def caesar_decode(text, shift):
    """Decode Caesar cipher text."""
    return caesar_encode(text, -shift)







''',
    },
    {
        "id": 'str-002',
        "category": 'string_processing',
        "lines": 13,
        "description": 'Word frequency counter',
        "code": r'''







def word_frequency(text):
    """Count word frequencies in text, case-insensitive."""
    words = text.lower().split()
    freq = {}
    for word in words:
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if cleaned:
            freq[cleaned] = freq.get(cleaned, 0) + 1
    return freq


def top_n_words(text, n=10):
    """Return the top n most frequent words."""
    freq = word_frequency(text)
    return sorted(freq.items(), key=lambda x: x[1], reverse=True)[:n]







''',
    },
    {
        "id": 'str-003',
        "category": 'string_processing',
        "lines": 14,
        "description": 'Anagram checker and grouper',
        "code": r'''







def is_anagram(s1, s2):
    """Check if two strings are anagrams."""
    clean1 = sorted(s1.lower().replace(" ", ""))
    clean2 = sorted(s2.lower().replace(" ", ""))
    return clean1 == clean2


def group_anagrams(words):
    """Group a list of words into anagram groups."""
    groups = {}
    for word in words:
        key = "".join(sorted(word.lower()))
        if key not in groups:
            groups[key] = []
        groups[key].append(word)
    return list(groups.values())







''',
    },
    {
        "id": 'str-004',
        "category": 'string_processing',
        "lines": 14,
        "description": 'Run-length encoding and decoding',
        "code": r'''







def rle_encode(text):
    """Encode string using run-length encoding."""
    if not text:
        return ""
    result = []
    count = 1
    for i in range(1, len(text)):
        if text[i] == text[i - 1]:
            count += 1
        else:
            result.append(f"{count}{text[i - 1]}")
            count = 1
    result.append(f"{count}{text[-1]}")
    return "".join(result)






''',
    },
    {
        "id": 'str-005',
        "category": 'string_processing',
        "lines": 33,
        "description": 'Simple tokenizer',
        "code": r'''







def tokenize(text):
    """Split text into typed tokens: word, number, symbol, whitespace."""
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isalpha() or text[i] == "_":
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append(("word", text[start:i]))
        elif text[i].isdigit():
            start = i
            while i < len(text) and (text[i].isdigit() or text[i] == "."):
                i += 1
            tokens.append(("number", text[start:i]))
        elif text[i].isspace():
            start = i
            while i < len(text) and text[i].isspace():
                i += 1
            tokens.append(("whitespace", text[start:i]))
        else:
            tokens.append(("symbol", text[i]))
            i += 1
    return tokens


def tokens_of_type(text, token_type):
    """Return only tokens of a given type."""
    return [val for typ, val in tokenize(text) if typ == token_type]


def count_tokens(text):
    """Count tokens by type."""
    counts = {}
    for typ, _ in tokenize(text):
        counts[typ] = counts.get(typ, 0) + 1
    return counts







''',
    },
    {
        "id": 'str-006',
        "category": 'string_processing',
        "lines": 36,
        "description": 'Template variable substitution engine',
        "code": r'''







def render_template(template, variables):
    """Substitute {{var}} placeholders with values from variables dict."""
    result = template
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value))
    return result


def find_variables(template):
    """Find all variable names referenced in template."""
    variables = []
    i = 0
    while i < len(template) - 1:
        if template[i:i+2] == "{{":
            end = template.find("}}", i + 2)
            if end != -1:
                var_name = template[i+2:end].strip()
                if var_name and var_name not in variables:
                    variables.append(var_name)
                i = end + 2
            else:
                i += 1
        else:
            i += 1
    return variables


def validate_template(template, available_vars):
    """Check that all template variables are available."""
    needed = find_variables(template)
    missing = [v for v in needed if v not in available_vars]
    return missing


def render_with_defaults(template, variables, defaults=None):
    """Render template with fallback defaults for missing vars."""
    if defaults is None:
        defaults = {}
    merged = dict(defaults)
    merged.update(variables)
    return render_template(template, merged)







''',
    },
    {
        "id": 'str-007',
        "category": 'string_processing',
        "lines": 43,
        "description": 'String alignment and padding utilities',
        "code": r'''







def pad_left(text, width, fill=" "):
    """Right-align text within given width."""
    if len(text) >= width:
        return text
    return fill * (width - len(text)) + text


def pad_right(text, width, fill=" "):
    """Left-align text within given width."""
    if len(text) >= width:
        return text
    return text + fill * (width - len(text))


def pad_center(text, width, fill=" "):
    """Center text within given width."""
    if len(text) >= width:
        return text
    total_pad = width - len(text)
    left = total_pad // 2
    right = total_pad - left
    return fill * left + text + fill * right


def format_table(rows, headers=None, align="left"):
    """Format a list of rows as an aligned text table."""
    all_rows = [headers] + rows if headers else rows
    if not all_rows:
        return ""
    num_cols = max(len(row) for row in all_rows)
    col_widths = [0] * num_cols
    for row in all_rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    lines = []
    for row in all_rows:
        cells = []
        for i in range(num_cols):
            val = str(row[i]) if i < len(row) else ""
            if align == "right":
                cells.append(pad_left(val, col_widths[i]))
            elif align == "center":
                cells.append(pad_center(val, col_widths[i]))
            else:
                cells.append(pad_right(val, col_widths[i]))
        lines.append(" | ".join(cells))
        if headers and row == headers:
            lines.append("-+-".join("-" * w for w in col_widths))
    return "\n".join(lines)







''',
    },
    {
        "id": 'str-008',
        "category": 'string_processing',
        "lines": 72,
        "description": 'CSV parser with quoting and escaping',
        "code": r'''







def parse_csv_line(line, delimiter=",", quote_char='"'):
    """Parse a single CSV line handling quoted fields and escaping."""
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
                    i += 2
                    continue
                else:
                    in_quotes = False
                    i += 1
                    continue
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


def parse_csv(text, delimiter=",", quote_char='"', has_header=True):
    """Parse CSV text into list of dicts (with header) or list of lists."""
    lines = text.strip().split("\n")
    if not lines:
        return []
    rows = [parse_csv_line(line, delimiter, quote_char) for line in lines]
    if has_header and len(rows) > 1:
        headers = rows[0]
        result = []
        for row in rows[1:]:
            record = {}
            for i, header in enumerate(headers):
                record[header] = row[i] if i < len(row) else ""
            result.append(record)
        return result
    return rows


def format_csv_field(value, delimiter=",", quote_char='"'):
    """Format a single field for CSV output."""
    s = str(value)
    needs_quoting = delimiter in s or quote_char in s or "\n" in s
    if needs_quoting:
        escaped = s.replace(quote_char, quote_char + quote_char)
        return quote_char + escaped + quote_char
    return s


def format_csv_row(fields, delimiter=",", quote_char='"'):
    """Format a row of fields as a CSV line."""
    return delimiter.join(
        format_csv_field(f, delimiter, quote_char) for f in fields
    )


def format_csv(rows, headers=None, delimiter=","):
    """Format data as CSV text."""
    lines = []
    if headers:
        lines.append(format_csv_row(headers, delimiter))
    for row in rows:
        if isinstance(row, dict) and headers:
            values = [row.get(h, "") for h in headers]
            lines.append(format_csv_row(values, delimiter))
        else:
            lines.append(format_csv_row(row, delimiter))
    return "\n".join(lines)







''',
    },
    {
        "id": 'str-009',
        "category": 'string_processing',
        "lines": 59,
        "description": 'Simple glob-like pattern matcher',
        "code": r'''






def glob_match(pattern, text):
    """Match text against a glob pattern with * and ? wildcards."""
    return _glob_match_impl(pattern, 0, text, 0, {})


def _glob_match_impl(pattern, pi, text, ti, memo):
    """Recursive glob matching with memoization."""
    key = (pi, ti)
    if key in memo:
        return memo[key]
    if pi == len(pattern):
        result = ti == len(text)
        memo[key] = result
        return result
    if pattern[pi] == "*":
        result = (_glob_match_impl(pattern, pi + 1, text, ti, memo) or
                  (ti < len(text) and
                   _glob_match_impl(pattern, pi, text, ti + 1, memo)))
        memo[key] = result
        return result
    if ti < len(text):
        if pattern[pi] == "?" or pattern[pi] == text[ti]:
            result = _glob_match_impl(pattern, pi + 1, text, ti + 1, memo)
            memo[key] = result
            return result
    memo[key] = False
    return False


def glob_filter(pattern, items):
    """Filter a list of strings by a glob pattern."""
    return [item for item in items if glob_match(pattern, item)]


def translate_glob_to_regex(pattern):
    """Convert a glob pattern to a regular expression string."""
    import re
    result = []
    for ch in pattern:
        if ch == "*":
            result.append(".*")
        elif ch == "?":
            result.append(".")
        elif ch in r".+^${}|()[]":
            result.append("\\" + ch)
        else:
            result.append(ch)
    return "^" + "".join(result) + "$"


def glob_match_regex(pattern, text):
    """Match using regex translation of glob pattern."""
    import re
    regex = translate_glob_to_regex(pattern)
    return bool(re.match(regex, text))


def multi_glob_filter(patterns, items):
    """Filter items matching any of the given glob patterns."""
    result = []
    for item in items:
        for pattern in patterns:
            if glob_match(pattern, item):
                result.append(item)
                break
    return result


def glob_match_case_insensitive(pattern, text):
    """Case-insensitive glob matching."""
    return glob_match(pattern.lower(), text.lower())






''',
    },
    {
        "id": 'str-010',
        "category": 'string_processing',
        "lines": 100,
        "description": 'Text processing pipeline',
        "code": r'''







class Token:
    """Represents a text token with type and value."""
    def __init__(self, token_type, value, position=0):
        self.token_type = token_type
        self.value = value
        self.position = position
    def __repr__(self):
        return f"Token({self.token_type!r}, {self.value!r})"
    def __eq__(self, other):
        if not isinstance(other, Token):
            return False
        return self.token_type == other.token_type and self.value == other.value


def tokenize_text(text):
    """Tokenize text into words, numbers, punctuation, and whitespace."""
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isalpha():
            start = i
            while i < len(text) and text[i].isalpha():
                i += 1
            tokens.append(Token("word", text[start:i], start))
        elif text[i].isdigit():
            start = i
            while i < len(text) and (text[i].isdigit() or text[i] == "."):
                i += 1
            tokens.append(Token("number", text[start:i], start))
        elif text[i].isspace():
            start = i
            while i < len(text) and text[i].isspace():
                i += 1
            tokens.append(Token("space", text[start:i], start))
        else:
            tokens.append(Token("punct", text[i], i))
            i += 1
    return tokens


def normalize_tokens(tokens):
    """Lowercase all word tokens."""
    result = []
    for t in tokens:
        if t.token_type == "word":
            result.append(Token(t.token_type, t.value.lower(), t.position))
        else:
            result.append(t)
    return result


STOP_WORDS = {"the", "a", "an", "is", "it", "of", "in", "to", "and", "or", "for"}


def filter_stop_words(tokens):
    """Remove common stop words from token list."""
    return [t for t in tokens if t.token_type != "word" or t.value.lower() not in STOP_WORDS]


def filter_by_type(tokens, keep_types):
    """Keep only tokens of specified types."""
    return [t for t in tokens if t.token_type in keep_types]


def transform_tokens(tokens, func):
    """Apply a transformation function to each token value."""
    return [Token(t.token_type, func(t.value), t.position) for t in tokens]


def join_tokens(tokens, separator=""):
    """Join token values into a single string."""
    return separator.join(t.value for t in tokens)


def count_by_type(tokens):
    """Count tokens grouped by type."""
    counts = {}
    for t in tokens:
        counts[t.token_type] = counts.get(t.token_type, 0) + 1
    return counts


def unique_values(tokens):
    """Get unique token values preserving order."""
    seen = set()
    result = []
    for t in tokens:
        if t.value not in seen:
            seen.add(t.value)
            result.append(t)
    return result


class TextPipeline:
    """Composable text processing pipeline."""
    def __init__(self):
        self.steps = []
    def add_step(self, name, func):
        """Add a processing step to the pipeline."""
        self.steps.append((name, func))
        return self
    def process(self, text):
        """Run text through all pipeline steps."""
        tokens = tokenize_text(text)
        for name, func in self.steps:
            tokens = func(tokens)
        return tokens
    def describe(self):
        """Return description of pipeline steps."""
        return [name for name, _ in self.steps]


def build_standard_pipeline():
    """Create a standard text normalization pipeline."""
    pipeline = TextPipeline()
    pipeline.add_step("normalize", normalize_tokens)
    pipeline.add_step("filter_stops", filter_stop_words)
    pipeline.add_step("unique", unique_values)
    return pipeline







def format_tokens(tokens, separator=" "):
    """Format tokens as a string with separator."""
    return separator.join(t.value for t in tokens)



''',
    },
    {
        "id": 'ds-001',
        "category": 'data_structures',
        "lines": 20,
        "description": 'Stack with push pop peek',
        "code": r'''







class Stack:
    """Stack data structure using a list."""

    def __init__(self):
        self._items = []

    def push(self, item):
        """Push item onto the stack."""
        self._items.append(item)

    def pop(self):
        """Remove and return top item."""
        if self.is_empty():
            raise IndexError("Pop from empty stack")
        return self._items.pop()

    def peek(self):
        """Return top item without removing."""
        if self.is_empty():
            raise IndexError("Peek at empty stack")
        return self._items[-1]

    def is_empty(self):
        """Check if stack is empty."""
        return len(self._items) == 0






''',
    },
    {
        "id": 'ds-002',
        "category": 'data_structures',
        "lines": 20,
        "description": 'Queue with enqueue dequeue peek',
        "code": r'''







class Queue:
    """Queue data structure using a list."""

    def __init__(self):
        self._items = []

    def enqueue(self, item):
        """Add item to the back."""
        self._items.append(item)

    def dequeue(self):
        """Remove and return front item."""
        if self.is_empty():
            raise IndexError("Dequeue from empty queue")
        return self._items.pop(0)

    def peek(self):
        """Return front item without removing."""
        if self.is_empty():
            raise IndexError("Peek at empty queue")
        return self._items[0]

    def is_empty(self):
        """Check if queue is empty."""
        return len(self._items) == 0






''',
    },
    {
        "id": 'ds-003',
        "category": 'data_structures',
        "lines": 20,
        "description": 'Doubly linked list',
        "code": r'''






class DLLNode:
    """Node for doubly linked list."""
    def __init__(self, value):
        self.value = value
        self.prev = None
        self.next = None


class DoublyLinkedList:
    """Simple doubly linked list."""
    def __init__(self):
        self.head = None
        self.tail = None
    def append(self, value):
        """Add value to the end."""
        node = DLLNode(value)
        if self.tail is None:
            self.head = self.tail = node
        else:
            node.prev = self.tail
            self.tail.next = node
            self.tail = node






''',
    },
    {
        "id": 'ds-004',
        "category": 'data_structures',
        "lines": 15,
        "description": 'Set operations utility',
        "code": r'''







def set_union(a, b):
    """Return union of two iterables as a set."""
    return set(a) | set(b)


def set_intersection(a, b):
    """Return intersection of two iterables as a set."""
    return set(a) & set(b)


def set_difference(a, b):
    """Return elements in a but not in b."""
    return set(a) - set(b)


def set_symmetric_difference(a, b):
    """Return elements in either but not both."""
    return set(a) ^ set(b)


def is_subset(a, b):
    """Check if a is a subset of b."""
    return set(a) <= set(b)







''',
    },
    {
        "id": 'ds-005',
        "category": 'data_structures',
        "lines": 48,
        "description": 'MinHeap with insert extract_min',
        "code": r'''







class MinHeap:
    """Min-heap data structure."""

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
        size = len(self._data)
        while True:
            smallest = i
            left = self._left(i)
            right = self._right(i)
            if left < size and self._data[left] < self._data[smallest]:
                smallest = left
            if right < size and self._data[right] < self._data[smallest]:
                smallest = right
            if smallest == i:
                break
            self._swap(i, smallest)
            i = smallest

    def insert(self, value):
        """Insert a value into the heap."""
        self._data.append(value)
        self._sift_up(len(self._data) - 1)

    def extract_min(self):
        """Remove and return the minimum value."""
        if not self._data:
            raise IndexError("Heap is empty")
        self._swap(0, len(self._data) - 1)
        value = self._data.pop()
        if self._data:
            self._sift_down(0)
        return value

    def peek(self):
        """Return minimum without removing."""
        if not self._data:
            raise IndexError("Heap is empty")
        return self._data[0]






''',
    },
    {
        "id": 'ds-006',
        "category": 'data_structures',
        "lines": 45,
        "description": 'HashMap with separate chaining',
        "code": r'''







class HashMap:
    """Hash map with separate chaining for collision resolution."""

    def __init__(self, capacity=16, load_factor=0.75):
        self._capacity = capacity
        self._load_factor = load_factor
        self._size = 0
        self._buckets = [[] for _ in range(capacity)]

    def _hash(self, key):
        return hash(key) % self._capacity

    def put(self, key, value):
        """Insert or update a key-value pair."""
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
        """Get value for key, or default if not found."""
        idx = self._hash(key)
        for k, v in self._buckets[idx]:
            if k == key:
                return v
        return default

    def remove(self, key):
        """Remove a key-value pair. Returns True if found."""
        idx = self._hash(key)
        for i, (k, v) in enumerate(self._buckets[idx]):
            if k == key:
                self._buckets[idx].pop(i)
                self._size -= 1
                return True
        return False

    def _resize(self):
        """Double the capacity and rehash all entries."""
        old_buckets = self._buckets
        self._capacity *= 2
        self._buckets = [[] for _ in range(self._capacity)]
        self._size = 0
        for bucket in old_buckets:
            for key, value in bucket:
                self.put(key, value)






''',
    },
    {
        "id": 'ds-007',
        "category": 'data_structures',
        "lines": 44,
        "description": 'LRU Cache class',
        "code": r'''







class _LRUNode:
    """Doubly-linked list node for LRU cache."""
    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class LRUCache:
    """Least Recently Used cache with O(1) get and put."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.head = _LRUNode(None, None)
        self.tail = _LRUNode(None, None)
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
        """Get value by key, marking as recently used."""
        if key in self.cache:
            node = self.cache[key]
            self._remove(node)
            self._add_to_front(node)
            return node.value
        return None

    def put(self, key, value):
        """Insert or update key-value pair."""
        if key in self.cache:
            self._remove(self.cache[key])
            del self.cache[key]
        node = _LRUNode(key, value)
        self._add_to_front(node)
        self.cache[key] = node
        if len(self.cache) > self.capacity:
            lru = self.tail.prev
            self._remove(lru)
            del self.cache[lru.key]







''',
    },
    {
        "id": 'ds-008',
        "category": 'data_structures',
        "lines": 75,
        "description": 'Binary search tree',
        "code": r'''





class BSTNode:
    """Node for binary search tree."""
    def __init__(self, key, value=None):
        self.key = key
        self.value = value
        self.left = None
        self.right = None


class BST:
    """Binary search tree with insert, search, delete."""

    def __init__(self):
        self.root = None

    def insert(self, key, value=None):
        """Insert a key-value pair."""
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
        """Search for a key and return its value."""
        node = self._search(self.root, key)
        return node.value if node else None

    def _search(self, node, key):
        if node is None or node.key == key:
            return node
        if key < node.key:
            return self._search(node.left, key)
        return self._search(node.right, key)

    def delete(self, key):
        """Delete a key from the tree."""
        self.root = self._delete(self.root, key)

    def _delete(self, node, key):
        if node is None:
            return None
        if key < node.key:
            node.left = self._delete(node.left, key)
        elif key > node.key:
            node.right = self._delete(node.right, key)
        else:
            if node.left is None:
                return node.right
            if node.right is None:
                return node.left
            successor = self._min_node(node.right)
            node.key = successor.key
            node.value = successor.value
            node.right = self._delete(node.right, successor.key)
        return node

    def _min_node(self, node):
        while node.left:
            node = node.left
        return node

    def inorder(self):
        """Return keys in sorted order."""
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)

    def height(self):
        """Return height of the tree."""
        return self._height(self.root)

    def _height(self, node):
        if node is None:
            return 0
        return 1 + max(self._height(node.left), self._height(node.right))





''',
    },
    {
        "id": 'ds-009',
        "category": 'data_structures',
        "lines": 68,
        "description": 'Trie prefix tree',
        "code": r'''







class TrieNode:
    """Node for trie data structure."""
    def __init__(self):
        self.children = {}
        self.is_end = False


class Trie:
    """Trie (prefix tree) for string storage and lookup."""

    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        """Insert a word into the trie."""
        node = self.root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.is_end = True

    def search(self, word):
        """Check if word exists in the trie."""
        node = self._find_node(word)
        return node is not None and node.is_end

    def starts_with(self, prefix):
        """Check if any word starts with the given prefix."""
        return self._find_node(prefix) is not None

    def _find_node(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return None
            node = node.children[ch]
        return node

    def all_words(self):
        """Return all words stored in the trie."""
        words = []
        self._collect(self.root, [], words)
        return words

    def _collect(self, node, path, words):
        if node.is_end:
            words.append("".join(path))
        for ch, child in sorted(node.children.items()):
            path.append(ch)
            self._collect(child, path, words)
            path.pop()

    def delete(self, word):
        """Delete a word from the trie."""
        self._delete(self.root, word, 0)

    def _delete(self, node, word, depth):
        if depth == len(word):
            if not node.is_end:
                return False
            node.is_end = False
            return len(node.children) == 0
        ch = word[depth]
        if ch not in node.children:
            return False
        should_delete = self._delete(node.children[ch], word, depth + 1)
        if should_delete:
            del node.children[ch]
            return not node.is_end and len(node.children) == 0
        return False

    def count_words(self):
        """Count total words in trie."""
        return self._count(self.root)

    def _count(self, node):
        count = 1 if node.is_end else 0
        for child in node.children.values():
            count += self._count(child)
        return count







''',
    },
    {
        "id": 'ds-010',
        "category": 'data_structures',
        "lines": 94,
        "description": 'Graph class with adjacency list',
        "code": r'''





from collections import deque


class Graph:
    """Graph with adjacency list representation."""

    def __init__(self, directed=False):
        self.directed = directed
        self.adj = {}

    def add_vertex(self, v):
        """Add a vertex to the graph."""
        if v not in self.adj:
            self.adj[v] = []

    def add_edge(self, u, v, weight=1):
        """Add an edge between u and v."""
        self.add_vertex(u)
        self.add_vertex(v)
        self.adj[u].append((v, weight))
        if not self.directed:
            self.adj[v].append((u, weight))

    def vertices(self):
        """Return all vertices."""
        return list(self.adj.keys())

    def neighbors(self, v):
        """Return neighbors of a vertex."""
        return [(u, w) for u, w in self.adj.get(v, [])]

    def bfs(self, start):
        """Breadth-first search from start vertex."""
        visited = set()
        order = []
        queue = deque([start])
        visited.add(start)
        while queue:
            v = queue.popleft()
            order.append(v)
            for neighbor, _ in self.adj.get(v, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return order

    def dfs(self, start):
        """Depth-first search from start vertex."""
        visited = set()
        order = []
        self._dfs_helper(start, visited, order)
        return order

    def _dfs_helper(self, v, visited, order):
        visited.add(v)
        order.append(v)
        for neighbor, _ in self.adj.get(v, []):
            if neighbor not in visited:
                self._dfs_helper(neighbor, visited, order)

    def has_cycle(self):
        """Detect if graph has a cycle (directed)."""
        visited = set()
        rec_stack = set()
        for v in self.adj:
            if v not in visited:
                if self._cycle_dfs(v, visited, rec_stack):
                    return True
        return False

    def _cycle_dfs(self, v, visited, rec_stack):
        visited.add(v)
        rec_stack.add(v)
        for neighbor, _ in self.adj.get(v, []):
            if neighbor not in visited:
                if self._cycle_dfs(neighbor, visited, rec_stack):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.discard(v)
        return False

    def connected_components(self):
        """Find all connected components."""
        visited = set()
        components = []
        for v in self.adj:
            if v not in visited:
                component = self.bfs(v)
                visited.update(component)
                components.append(component)
        return components

    def shortest_path(self, start, end):
        """BFS shortest path between two vertices."""
        if start == end:
            return [start]
        visited = {start}
        queue = deque([(start, [start])])
        while queue:
            v, path = queue.popleft()
            for neighbor, _ in self.adj.get(v, []):
                if neighbor == end:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return []





''',
    },
    {
        "id": 'cf-001',
        "category": 'control_flow',
        "lines": 12,
        "description": 'FizzBuzz with configurable rules',
        "code": r'''






def fizzbuzz(n, rules=None):
    """Generate FizzBuzz sequence with configurable rules."""
    if rules is None:
        rules = [(3, "Fizz"), (5, "Buzz")]
    result = []
    for i in range(1, n + 1):
        parts = [word for divisor, word in rules if i % divisor == 0]
        result.append("".join(parts) if parts else str(i))
    return result


def fizzbuzz_single(n, rules=None):
    """Return FizzBuzz value for a single number."""
    return fizzbuzz(n, rules)[-1]






''',
    },
    {
        "id": 'cf-002',
        "category": 'control_flow',
        "lines": 11,
        "description": 'Number classifier perfect abundant deficient',
        "code": r'''







def sum_of_divisors(n):
    """Compute sum of proper divisors of n."""
    if n <= 1:
        return 0
    total = 1
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            total += i
            if i != n // i:
                total += n // i
    return total






''',
    },
    {
        "id": 'cf-003',
        "category": 'control_flow',
        "lines": 18,
        "description": 'Temperature converter with validation',
        "code": r'''






def convert_temperature(value, from_unit, to_unit):
    """Convert temperature between Celsius, Fahrenheit, and Kelvin."""
    from_unit = from_unit.upper()
    to_unit = to_unit.upper()
    if from_unit == to_unit:
        return value
    if from_unit == "C":
        celsius = value
    elif from_unit == "F":
        celsius = (value - 32) * 5 / 9
    else:
        celsius = value - 273.15
    if to_unit == "C":
        return celsius
    elif to_unit == "F":
        return celsius * 9 / 5 + 32
    else:
        return celsius + 273.15






''',
    },
    {
        "id": 'cf-004',
        "category": 'control_flow',
        "lines": 20,
        "description": 'Collatz sequence with statistics',
        "code": r'''







def collatz_sequence(n):
    """Generate the Collatz sequence starting from n."""
    if n <= 0:
        raise ValueError("Must be a positive integer")
    seq = [n]
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        seq.append(n)
    return seq


def collatz_stats(n):
    """Return statistics about the Collatz sequence for n."""
    seq = collatz_sequence(n)
    return {
        "length": len(seq),
        "max_value": max(seq),
        "start": n,
    }







''',
    },
    {
        "id": 'cf-005',
        "category": 'control_flow',
        "lines": 31,
        "description": 'Simple state machine',
        "code": r'''







class StateMachine:
    """Simple finite state machine."""

    def __init__(self, initial_state):
        self.state = initial_state
        self.transitions = {}
        self.callbacks = {}

    def add_transition(self, from_state, event, to_state):
        """Register a state transition."""
        self.transitions[(from_state, event)] = to_state

    def add_callback(self, from_state, event, callback):
        """Register a callback for a transition."""
        self.callbacks[(from_state, event)] = callback

    def trigger(self, event):
        """Trigger an event, potentially changing state."""
        key = (self.state, event)
        if key not in self.transitions:
            raise ValueError(f"No transition from {self.state!r} on {event!r}")
        old_state = self.state
        self.state = self.transitions[key]
        if key in self.callbacks:
            self.callbacks[key](old_state, event, self.state)
        return self.state

    def get_available_events(self):
        """Return events available from current state."""
        return [event for (state, event) in self.transitions if state == self.state]

    def is_in_state(self, state):
        """Check if machine is in a given state."""
        return self.state == state

    def reset(self, state):
        """Reset to a given state."""
        self.state = state







''',
    },
    {
        "id": 'cf-006',
        "category": 'control_flow',
        "lines": 41,
        "description": 'Vending machine simulator',
        "code": r'''







class VendingMachine:
    """Simple vending machine simulator."""

    def __init__(self):
        self.balance = 0
        self.inventory = {}

    def add_product(self, name, price, quantity):
        """Stock a product in the machine."""
        self.inventory[name] = {"price": price, "quantity": quantity}

    def insert_coin(self, amount):
        """Insert money into the machine."""
        if amount <= 0:
            raise ValueError("Amount must be positive")
        self.balance += amount
        return self.balance

    def select_item(self, name):
        """Try to purchase an item."""
        if name not in self.inventory:
            return {"success": False, "error": "Item not found"}
        item = self.inventory[name]
        if item["quantity"] <= 0:
            return {"success": False, "error": "Out of stock"}
        if self.balance < item["price"]:
            return {"success": False, "error": "Insufficient funds",
                    "needed": item["price"] - self.balance}
        self.balance -= item["price"]
        item["quantity"] -= 1
        change = self.balance
        self.balance = 0
        return {"success": True, "item": name, "change": change}

    def get_balance(self):
        """Return current balance."""
        return self.balance

    def return_coins(self):
        """Return all inserted coins."""
        amount = self.balance
        self.balance = 0
        return amount

    def list_products(self):
        """List available products."""
        return {name: {"price": info["price"], "quantity": info["quantity"]}
                for name, info in self.inventory.items() if info["quantity"] > 0}







''',
    },
    {
        "id": 'cf-007',
        "category": 'control_flow',
        "lines": 44,
        "description": 'Calendar utilities',
        "code": r'''







def is_leap_year(year):
    """Check if a year is a leap year."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(year, month):
    """Return number of days in a given month."""
    if month < 1 or month > 12:
        raise ValueError("Month must be 1-12")
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and is_leap_year(year):
        return 29
    return days[month - 1]


def day_of_year(year, month, day):
    """Calculate day of the year for a given date."""
    total = 0
    for m in range(1, month):
        total += days_in_month(year, m)
    total += day
    return total


def day_of_week(year, month, day):
    """Calculate day of week (0=Monday) using Zeller-like formula."""
    if month < 3:
        month += 12
        year -= 1
    q = day
    k = year % 100
    j = year // 100
    h = (q + (13 * (month + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7
    return (h + 6) % 7


def add_days(year, month, day, delta):
    """Add delta days to a date and return (year, month, day)."""
    while delta > 0:
        dim = days_in_month(year, month)
        remaining = dim - day
        if delta <= remaining:
            day += delta
            delta = 0
        else:
            delta -= remaining + 1
            day = 1
            month += 1
            if month > 12:
                month = 1
                year += 1
    return year, month, day







''',
    },
    {
        "id": 'cf-008',
        "category": 'control_flow',
        "lines": 76,
        "description": 'Arithmetic expression evaluator',
        "code": r'''




def tokenize_expr(expr):
    """Tokenize an arithmetic expression."""
    tokens = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
        elif expr[i].isdigit() or expr[i] == ".":
            start = i
            while i < len(expr) and (expr[i].isdigit() or expr[i] == "."):
                i += 1
            tokens.append(("NUM", float(expr[start:i])))
        elif expr[i] in "+-*/":
            tokens.append(("OP", expr[i]))
            i += 1
        elif expr[i] == "(":
            tokens.append(("LPAREN", "("))
            i += 1
        elif expr[i] == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
        else:
            raise ValueError(f"Unexpected character: {expr[i]}")
    return tokens


class ExprParser:
    """Recursive descent parser for arithmetic expressions."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def parse(self):
        """Parse and evaluate the expression."""
        result = self._expr()
        if self.pos < len(self.tokens):
            raise ValueError("Unexpected token")
        return result

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _consume(self):
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _expr(self):
        """Parse addition and subtraction."""
        result = self._term()
        while self._peek() and self._peek()[0] == "OP" and self._peek()[1] in "+-":
            op = self._consume()[1]
            right = self._term()
            result = result + right if op == "+" else result - right
        return result

    def _term(self):
        """Parse multiplication and division."""
        result = self._factor()
        while self._peek() and self._peek()[0] == "OP" and self._peek()[1] in "*/":
            op = self._consume()[1]
            right = self._factor()
            result = result * right if op == "*" else result / right
        return result

    def _factor(self):
        """Parse numbers and parenthesized expressions."""
        token = self._peek()
        if token is None:
            raise ValueError("Unexpected end of expression")
        if token[0] == "NUM":
            self._consume()
            return token[1]
        if token[0] == "LPAREN":
            self._consume()
            result = self._expr()
            if not self._peek() or self._peek()[0] != "RPAREN":
                raise ValueError("Missing closing parenthesis")
            self._consume()
            return result
        raise ValueError(f"Unexpected token: {token}")


def evaluate(expr):
    """Evaluate an arithmetic expression string."""
    return ExprParser(tokenize_expr(expr)).parse()




''',
    },
    {
        "id": 'cf-009',
        "category": 'control_flow',
        "lines": 55,
        "description": "Conway's Game of Life step",
        "code": r'''







def make_grid(rows, cols, live_cells=None):
    """Create a grid for Game of Life."""
    grid = [[False] * cols for _ in range(rows)]
    if live_cells:
        for r, c in live_cells:
            if 0 <= r < rows and 0 <= c < cols:
                grid[r][c] = True
    return grid


def count_neighbors(grid, row, col):
    """Count live neighbors of a cell."""
    rows = len(grid)
    cols = len(grid[0])
    count = 0
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            r, c = row + dr, col + dc
            if 0 <= r < rows and 0 <= c < cols and grid[r][c]:
                count += 1
    return count


def step(grid):
    """Compute one step of Conway's Game of Life."""
    rows = len(grid)
    cols = len(grid[0])
    new_grid = [[False] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            neighbors = count_neighbors(grid, r, c)
            if grid[r][c]:
                new_grid[r][c] = neighbors in (2, 3)
            else:
                new_grid[r][c] = neighbors == 3
    return new_grid


def grid_to_string(grid, alive="#", dead="."):
    """Convert grid to a displayable string."""
    return "\n".join("".join(alive if cell else dead for cell in row) for row in grid)


def get_live_cells(grid):
    """Return set of (row, col) for all live cells."""
    cells = set()
    for r in range(len(grid)):
        for c in range(len(grid[0])):
            if grid[r][c]:
                cells.add((r, c))
    return cells


def run_simulation(grid, steps):
    """Run multiple steps of the simulation."""
    history = [grid]
    for _ in range(steps):
        grid = step(grid)
        history.append(grid)
    return history


def count_alive(grid):
    """Count total alive cells."""
    return sum(sum(1 for c in row if c) for row in grid)







''',
    },
    {
        "id": 'cf-010',
        "category": 'control_flow',
        "lines": 100,
        "description": 'Command interpreter with variables',
        "code": r'''







class CommandInterpreter:
    """Simple command interpreter with variables and built-in commands."""

    def __init__(self):
        self.variables = {}
        self.commands = {}
        self.history = []
        self.output = []
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in commands."""
        self.commands["set"] = self._cmd_set
        self.commands["get"] = self._cmd_get
        self.commands["del"] = self._cmd_del
        self.commands["echo"] = self._cmd_echo
        self.commands["help"] = self._cmd_help
        self.commands["list"] = self._cmd_list
        self.commands["calc"] = self._cmd_calc
        self.commands["history"] = self._cmd_history
        self.commands["clear"] = self._cmd_clear
        self.commands["len"] = self._cmd_len

    def _cmd_set(self, args):
        """Set a variable: set name value"""
        if len(args) < 2:
            return "Usage: set <name> <value>"
        name = args[0]
        value = " ".join(args[1:])
        self.variables[name] = value
        return f"{name} = {value}"

    def _cmd_get(self, args):
        """Get a variable value: get name"""
        if not args:
            return "Usage: get <name>"
        name = args[0]
        if name in self.variables:
            return str(self.variables[name])
        return f"Variable '{name}' not found"

    def _cmd_del(self, args):
        """Delete a variable: del name"""
        if not args:
            return "Usage: del <name>"
        name = args[0]
        if name in self.variables:
            del self.variables[name]
            return f"Deleted '{name}'"
        return f"Variable '{name}' not found"

    def _cmd_echo(self, args):
        """Echo arguments back, expanding $variables."""
        parts = []
        for arg in args:
            if arg.startswith("$") and arg[1:] in self.variables:
                parts.append(str(self.variables[arg[1:]]))
            else:
                parts.append(arg)
        return " ".join(parts)

    def _cmd_help(self, args):
        """Show available commands."""
        if args and args[0] in self.commands:
            doc = self.commands[args[0]].__doc__ or "No help available"
            return f"{args[0]}: {doc}"
        return "Commands: " + ", ".join(sorted(self.commands.keys()))

    def _cmd_list(self, args):
        """List all variables."""
        if not self.variables:
            return "No variables set"
        lines = [f"  {k} = {v}" for k, v in sorted(self.variables.items())]
        return "\n".join(lines)

    def _cmd_calc(self, args):
        """Evaluate a simple arithmetic expression."""
        if not args:
            return "Usage: calc <expression>"
        expr = " ".join(args)
        for var, val in self.variables.items():
            expr = expr.replace(f"${var}", str(val))
        try:
            allowed = set("0123456789+-*/(). ")
            if all(c in allowed for c in expr):
                return str(eval(expr))
            return "Invalid expression"
        except Exception as e:
            return f"Error: {e}"

    def _cmd_history(self, args):
        """Show command history."""
        if not self.history:
            return "No history"
        lines = [f"  {i+1}: {cmd}" for i, cmd in enumerate(self.history)]
        return "\n".join(lines)

    def _cmd_clear(self, args):
        """Clear all variables."""
        self.variables.clear()
        return "Variables cleared"

    def _cmd_len(self, args):
        """Get length of a variable value."""
        if not args:
            return "Usage: len <name>"
        name = args[0]
        if name in self.variables:
            return str(len(str(self.variables[name])))
        return f"Variable '{name}' not found"





    def get_variable(self, name):
        """Get a variable value directly."""



''',
    },
    {
        "id": 'exc-001',
        "category": 'exception_handling',
        "lines": 20,
        "description": 'Safe division with error handling',
        "code": r'''







def safe_divide(a, b, default=None):
    """Divide a by b, returning default on error."""
    try:
        return a / b
    except ZeroDivisionError:
        return default
    except TypeError:
        return default


def safe_int_divide(a, b, default=0):
    """Integer division with error handling."""
    try:
        return a // b
    except (ZeroDivisionError, TypeError):
        return default


def safe_modulo(a, b, default=0):
    """Modulo operation with error handling."""
    try:
        return a % b
    except (ZeroDivisionError, TypeError):
        return default







''',
    },
    {
        "id": 'exc-002',
        "category": 'exception_handling',
        "lines": 12,
        "description": 'Type-safe coercion functions',
        "code": r'''







def to_int(value, default=0):
    """Convert value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def to_float(value, default=0.0):
    """Convert value to float, returning default on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default






''',
    },
    {
        "id": 'exc-003',
        "category": 'exception_handling',
        "lines": 17,
        "description": 'Resource cleanup with try/finally',
        "code": r'''







class ManagedResource:
    """Resource that tracks open/close state."""

    def __init__(self, name):
        self.name = name
        self.is_open = False

    def open(self):
        """Open the resource."""
        self.is_open = True
        return self

    def close(self):
        """Close the resource."""
        self.is_open = False

    def read(self):
        """Read from resource, must be open."""
        if not self.is_open:
            raise RuntimeError(f"Resource '{self.name}' is not open")
        return f"data from {self.name}"






''',
    },
    {
        "id": 'exc-004',
        "category": 'exception_handling',
        "lines": 18,
        "description": 'Simple retry decorator',
        "code": r'''







import time


def retry(max_attempts=3, delay=0.1, exceptions=(Exception,)):
    """Decorator that retries a function on failure."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
            raise last_exception
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator







''',
    },
    {
        "id": 'exc-005',
        "category": 'exception_handling',
        "lines": 42,
        "description": 'Input validation framework with custom errors',
        "code": r'''







class ValidationError(Exception):
    """Raised when validation fails."""
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class ValidationResult:
    """Collects validation errors."""
    def __init__(self):
        self.errors = []
    def add_error(self, field, message):
        self.errors.append(ValidationError(field, message))
    def is_valid(self):
        return len(self.errors) == 0
    def get_messages(self):
        return {e.field: e.message for e in self.errors}


def validate_required(result, field, value):
    """Validate that a value is not None or empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        result.add_error(field, "is required")


def validate_range(result, field, value, min_val=None, max_val=None):
    """Validate that a numeric value is within range."""
    if value is None:
        return
    if min_val is not None and value < min_val:
        result.add_error(field, f"must be >= {min_val}")
    if max_val is not None and value > max_val:
        result.add_error(field, f"must be <= {max_val}")


def validate_length(result, field, value, min_len=None, max_len=None):
    """Validate string length."""
    if value is None:
        return
    length = len(value)
    if min_len is not None and length < min_len:
        result.add_error(field, f"must be at least {min_len} characters")
    if max_len is not None and length > max_len:
        result.add_error(field, f"must be at most {max_len} characters")


def validate_pattern(result, field, value, pattern):
    """Validate that a string matches a regex pattern."""
    import re
    if value is not None and not re.match(pattern, value):
        result.add_error(field, f"does not match pattern {pattern}")







''',
    },
    {
        "id": 'exc-006',
        "category": 'exception_handling',
        "lines": 41,
        "description": 'Custom exception hierarchy with context',
        "code": r'''







class AppError(Exception):
    """Base application error with context."""
    def __init__(self, message, code=None, context=None):
        super().__init__(message)
        self.code = code
        self.context = context or {}
    def to_dict(self):
        return {
            "error": self.__class__.__name__,
            "message": str(self),
            "code": self.code,
            "context": self.context,
        }


class NotFoundError(AppError):
    """Resource not found."""
    def __init__(self, resource_type, resource_id):
        super().__init__(
            f"{resource_type} '{resource_id}' not found",
            code="NOT_FOUND",
            context={"resource_type": resource_type, "resource_id": resource_id},
        )


class ValidationFailedError(AppError):
    """Validation failed with details."""
    def __init__(self, errors):
        super().__init__(
            f"Validation failed: {len(errors)} error(s)",
            code="VALIDATION_FAILED",
            context={"errors": errors},
        )


class AuthorizationError(AppError):
    """Insufficient permissions."""
    def __init__(self, action, resource=None):
        msg = f"Not authorized to {action}"
        if resource:
            msg += f" on {resource}"
        super().__init__(msg, code="UNAUTHORIZED", context={"action": action})


def handle_error(error):
    """Convert an exception to a standardized error dict."""
    if isinstance(error, AppError):
        return error.to_dict()
    return {"error": type(error).__name__, "message": str(error), "code": "UNKNOWN", "context": {}}







''',
    },
    {
        "id": 'exc-007',
        "category": 'exception_handling',
        "lines": 45,
        "description": 'Context manager for rollback-able transactions',
        "code": r'''







class Transaction:
    """Rollback-able transaction context manager."""

    def __init__(self):
        self.operations = []
        self.committed = False

    def add_operation(self, forward, rollback):
        """Add a forward operation and its rollback."""
        forward()
        self.operations.append(rollback)

    def commit(self):
        """Mark transaction as committed."""
        self.committed = True
        self.operations.clear()

    def rollback(self):
        """Rollback all operations in reverse order."""
        errors = []
        for op in reversed(self.operations):
            try:
                op()
            except Exception as e:
                errors.append(e)
        self.operations.clear()
        if errors:
            raise RuntimeError(f"Rollback had {len(errors)} error(s): {errors}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None or not self.committed:
            self.rollback()
        return False


class StateTracker:
    """Tracks state changes for transactional rollback."""

    def __init__(self, initial_state=None):
        self.state = initial_state or {}
        self.snapshots = []

    def snapshot(self):
        """Save current state."""
        self.snapshots.append(dict(self.state))

    def restore(self):
        """Restore last snapshot."""
        if self.snapshots:
            self.state = self.snapshots.pop()

    def update(self, key, value):
        """Update a state value."""
        self.state[key] = value







''',
    },
    {
        "id": 'exc-008',
        "category": 'exception_handling',
        "lines": 64,
        "description": 'Error recovery pipeline',
        "code": r'''







class RecoveryStrategy:
    """Base class for error recovery strategies."""
    def __init__(self, name):
        self.name = name
    def can_handle(self, error):
        """Check if this strategy can handle the error."""
        return True
    def recover(self, error, context):
        """Attempt to recover from the error."""
        raise NotImplementedError


class RetryStrategy(RecoveryStrategy):
    """Retry the operation."""
    def __init__(self, max_retries=3):
        super().__init__("retry")
        self.max_retries = max_retries
    def can_handle(self, error):
        return isinstance(error, (ConnectionError, TimeoutError, OSError))
    def recover(self, error, context):
        attempts = context.get("retry_count", 0)
        if attempts < self.max_retries:
            context["retry_count"] = attempts + 1
            return {"action": "retry", "attempts": attempts + 1}
        return {"action": "fail", "reason": "max retries exceeded"}


class DefaultValueStrategy(RecoveryStrategy):
    """Return a default value on error."""
    def __init__(self, default_value=None):
        super().__init__("default_value")
        self.default_value = default_value
    def can_handle(self, error):
        return isinstance(error, (ValueError, KeyError, TypeError))
    def recover(self, error, context):
        return {"action": "default", "value": self.default_value}


class LogAndSkipStrategy(RecoveryStrategy):
    """Log the error and skip the operation."""
    def __init__(self):
        super().__init__("log_and_skip")
    def recover(self, error, context):
        context.setdefault("skipped_errors", []).append(str(error))
        return {"action": "skip"}


class RecoveryPipeline:
    """Pipeline that tries multiple recovery strategies."""
    def __init__(self):
        self.strategies = []
        self.error_log = []
    def add_strategy(self, strategy):
        """Add a recovery strategy to the pipeline."""
        self.strategies.append(strategy)
    def handle_error(self, error, context=None):
        """Try each strategy until one handles the error."""
        if context is None:
            context = {}
        self.error_log.append({"error": str(error), "type": type(error).__name__})
        for strategy in self.strategies:
            if strategy.can_handle(error):
                result = strategy.recover(error, context)
                result["strategy"] = strategy.name
                return result
        return {"action": "fail", "reason": "no strategy could handle the error"}
    def get_error_log(self):
        """Return the error log."""
        return list(self.error_log)
    def clear_log(self):
        """Clear the error log."""
        self.error_log.clear()







''',
    },
    {
        "id": 'exc-009',
        "category": 'exception_handling',
        "lines": 79,
        "description": 'Circuit breaker pattern',
        "code": r'''







import time


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """Circuit breaker pattern implementation."""

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(self, failure_threshold=5, recovery_timeout=30, success_threshold=2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.state = self.STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.total_calls = 0
        self.total_failures = 0

    def can_execute(self):
        """Check if operation can be executed."""
        if self.state == self.STATE_CLOSED:
            return True
        if self.state == self.STATE_OPEN:
            if self.last_failure_time is None:
                return False
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = self.STATE_HALF_OPEN
                self.success_count = 0
                return True
            return False
        return True

    def record_success(self):
        """Record a successful operation."""
        self.total_calls += 1
        if self.state == self.STATE_HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = self.STATE_CLOSED
                self.failure_count = 0
        elif self.state == self.STATE_CLOSED:
            self.failure_count = 0

    def record_failure(self):
        """Record a failed operation."""
        self.total_calls += 1
        self.total_failures += 1
        self.last_failure_time = time.time()
        if self.state == self.STATE_HALF_OPEN:
            self.state = self.STATE_OPEN
        elif self.state == self.STATE_CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.state = self.STATE_OPEN

    def execute(self, func, *args, **kwargs):
        """Execute a function through the circuit breaker."""
        if not self.can_execute():
            raise CircuitBreakerError(f"Circuit breaker is {self.state}")
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise

    def get_state(self):
        """Return current state information."""
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
        }

    def reset(self):
        """Reset the circuit breaker."""
        self.state = self.STATE_CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None







''',
    },
    {
        "id": 'exc-010',
        "category": 'exception_handling',
        "lines": 96,
        "description": 'Robust data processor with comprehensive error handling',
        "code": r'''







class ProcessingError(Exception):
    """Error during data processing."""
    def __init__(self, message, record=None, stage=None):
        super().__init__(message)
        self.record = record
        self.stage = stage


class DataProcessor:
    """Robust data processor with validation, transformation, and error tracking."""

    def __init__(self, strict=False):
        self.strict = strict
        self.validators = []
        self.transformers = []
        self.error_log = []
        self.processed_count = 0
        self.error_count = 0

    def add_validator(self, name, func):
        """Add a validation function."""
        self.validators.append((name, func))

    def add_transformer(self, name, func):
        """Add a transformation function."""
        self.transformers.append((name, func))

    def validate_record(self, record):
        """Run all validators on a record."""
        errors = []
        for name, func in self.validators:
            try:
                if not func(record):
                    errors.append(f"Validation '{name}' failed")
            except Exception as e:
                errors.append(f"Validation '{name}' error: {e}")
        return errors

    def transform_record(self, record):
        """Apply all transformations to a record."""
        result = record
        for name, func in self.transformers:
            try:
                result = func(result)
            except Exception as e:
                raise ProcessingError(f"Transform '{name}' failed: {e}", record=record, stage=name)
        return result

    def process_record(self, record):
        """Process a single record through validation and transformation."""
        validation_errors = self.validate_record(record)
        if validation_errors:
            error = ProcessingError("; ".join(validation_errors), record=record, stage="validation")
            if self.strict:
                raise error
            self.error_log.append({"record": record, "errors": validation_errors, "stage": "validation"})
            self.error_count += 1
            return None
        try:
            result = self.transform_record(record)
            self.processed_count += 1
            return result
        except ProcessingError as e:
            if self.strict:
                raise
            self.error_log.append({"record": record, "errors": [str(e)], "stage": e.stage})
            self.error_count += 1
            return None

    def process_batch(self, records):
        """Process a batch of records, collecting results and errors."""
        results = []
        for record in records:
            try:
                result = self.process_record(record)
                if result is not None:
                    results.append(result)
            except ProcessingError:
                if self.strict:
                    raise
        return results

    def get_summary(self):
        """Return processing summary."""
        return {
            "processed": self.processed_count,
            "errors": self.error_count,
            "total": self.processed_count + self.error_count,
            "error_rate": self.error_count / max(1, self.processed_count + self.error_count),
        }

    def get_errors(self):
        """Return all logged errors."""
        return list(self.error_log)

    def clear(self):
        """Reset processor state."""
        self.error_log.clear()
        self.processed_count = 0
        self.error_count = 0


def build_processor(validators=None, transformers=None, strict=False):
    """Build a configured data processor."""
    proc = DataProcessor(strict=strict)
    for name, func in (validators or []):
        proc.add_validator(name, func)
    for name, func in (transformers or []):
        proc.add_transformer(name, func)
    return proc







''',
    },
    {
        "id": 'cls-001',
        "category": 'classes_oop',
        "lines": 18,
        "description": '2D Point class with distance and midpoint',
        "code": r'''







import math


class Point:
    """2D point with distance and midpoint operations."""

    def __init__(self, x=0.0, y=0.0):
        self.x = float(x)
        self.y = float(y)

    def distance_to(self, other):
        """Euclidean distance to another point."""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def midpoint(self, other):
        """Return midpoint between self and other."""
        return Point((self.x + other.x) / 2, (self.y + other.y) / 2)

    def __eq__(self, other):
        if not isinstance(other, Point):
            return False
        return self.x == other.x and self.y == other.y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"







''',
    },
    {
        "id": 'cls-002',
        "category": 'classes_oop',
        "lines": 14,
        "description": 'Counter class with increment decrement reset',
        "code": r'''







class Counter:
    """Thread-unsafe counter with bounds."""

    def __init__(self, initial=0, min_val=None, max_val=None):
        self.value = initial
        self.min_val = min_val
        self.max_val = max_val

    def increment(self, amount=1):
        """Increment counter by amount."""
        new_val = self.value + amount
        if self.max_val is not None and new_val > self.max_val:
            self.value = self.max_val
        else:
            self.value = new_val
        return self.value






''',
    },
    {
        "id": 'cls-003',
        "category": 'classes_oop',
        "lines": 18,
        "description": 'Stack with max tracking',
        "code": r'''







class MaxStack:
    """Stack that tracks the maximum element in O(1)."""

    def __init__(self):
        self._items = []
        self._max_stack = []

    def push(self, value):
        """Push value onto stack."""
        self._items.append(value)
        if not self._max_stack or value >= self._max_stack[-1]:
            self._max_stack.append(value)

    def pop(self):
        """Pop top value from stack."""
        if not self._items:
            raise IndexError("Pop from empty stack")
        val = self._items.pop()
        if val == self._max_stack[-1]:
            self._max_stack.pop()
        return val






''',
    },
    {
        "id": 'cls-004',
        "category": 'classes_oop',
        "lines": 19,
        "description": 'Pair class with comparison operators',
        "code": r'''







class Pair:
    """Ordered pair with comparison operations."""

    def __init__(self, first, second):
        self.first = first
        self.second = second

    def __eq__(self, other):
        if not isinstance(other, Pair):
            return NotImplemented
        return self.first == other.first and self.second == other.second

    def __lt__(self, other):
        if not isinstance(other, Pair):
            return NotImplemented
        if self.first != other.first:
            return self.first < other.first
        return self.second < other.second

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        return not self <= other






''',
    },
    {
        "id": 'cls-005',
        "category": 'classes_oop',
        "lines": 43,
        "description": 'Shape hierarchy with area and perimeter',
        "code": r'''







import math


class Shape:
    """Base class for geometric shapes."""
    def area(self):
        raise NotImplementedError
    def perimeter(self):
        raise NotImplementedError
    def describe(self):
        return f"{self.__class__.__name__}: area={self.area():.2f}, perimeter={self.perimeter():.2f}"


class Circle(Shape):
    """Circle defined by radius."""
    def __init__(self, radius):
        if radius < 0:
            raise ValueError("Radius must be non-negative")
        self.radius = radius
    def area(self):
        return math.pi * self.radius ** 2
    def perimeter(self):
        return 2 * math.pi * self.radius


class Rectangle(Shape):
    """Rectangle defined by width and height."""
    def __init__(self, width, height):
        if width < 0 or height < 0:
            raise ValueError("Dimensions must be non-negative")
        self.width = width
        self.height = height
    def area(self):
        return self.width * self.height
    def perimeter(self):
        return 2 * (self.width + self.height)


class Triangle(Shape):
    """Triangle defined by three sides."""
    def __init__(self, a, b, c):
        if a + b <= c or a + c <= b or b + c <= a:
            raise ValueError("Invalid triangle sides")
        self.a = a
        self.b = b
        self.c = c
    def area(self):
        s = self.perimeter() / 2
        return math.sqrt(s * (s - self.a) * (s - self.b) * (s - self.c))
    def perimeter(self):
        return self.a + self.b + self.c







''',
    },
    {
        "id": 'cls-006',
        "category": 'classes_oop',
        "lines": 44,
        "description": 'Bank account with transactions',
        "code": r'''







class InsufficientFundsError(Exception):
    """Raised when withdrawal exceeds balance."""
    pass


class Transaction:
    """Represents a bank transaction."""
    def __init__(self, txn_type, amount, balance_after):
        self.txn_type = txn_type
        self.amount = amount
        self.balance_after = balance_after
    def __repr__(self):
        return f"Transaction({self.txn_type}, {self.amount}, bal={self.balance_after})"


class BankAccount:
    """Bank account with transaction history."""

    def __init__(self, owner, initial_balance=0):
        self.owner = owner
        self._balance = initial_balance
        self._history = []

    @property
    def balance(self):
        return self._balance

    def deposit(self, amount):
        """Deposit funds."""
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        self._balance += amount
        self._history.append(Transaction("deposit", amount, self._balance))
        return self._balance

    def withdraw(self, amount):
        """Withdraw funds."""
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")
        if amount > self._balance:
            raise InsufficientFundsError(f"Cannot withdraw {amount}, balance is {self._balance}")
        self._balance -= amount
        self._history.append(Transaction("withdrawal", amount, self._balance))
        return self._balance

    def transfer(self, other, amount):
        """Transfer funds to another account."""
        self.withdraw(amount)
        other.deposit(amount)
        return self._balance

    def history(self):
        """Return transaction history."""
        return list(self._history)







''',
    },
    {
        "id": 'cls-007',
        "category": 'classes_oop',
        "lines": 48,
        "description": 'Linked list with full iteration support',
        "code": r'''





class ListNode:
    """Node for singly linked list."""
    def __init__(self, value, next_node=None):
        self.value = value
        self.next = next_node


class LinkedList:
    """Singly linked list with iteration."""

    def __init__(self):
        self.head = None
        self._size = 0

    def append(self, value):
        """Add value to end of list."""
        node = ListNode(value)
        if self.head is None:
            self.head = node
        else:
            current = self.head
            while current.next:
                current = current.next
            current.next = node
        self._size += 1

    def prepend(self, value):
        """Add value to beginning of list."""
        self.head = ListNode(value, self.head)
        self._size += 1

    def remove(self, value):
        """Remove first occurrence of value."""
        if self.head is None:
            return False
        if self.head.value == value:
            self.head = self.head.next
            self._size -= 1
            return True
        current = self.head
        while current.next:
            if current.next.value == value:
                current.next = current.next.next
                self._size -= 1
                return True
            current = current.next
        return False

    def __iter__(self):
        current = self.head
        while current:
            yield current.value
            current = current.next

    def __len__(self):
        return self._size





''',
    },
    {
        "id": 'cls-008',
        "category": 'classes_oop',
        "lines": 60,
        "description": 'Expression tree with evaluate and simplify',
        "code": r'''







class Expr:
    """Base class for expression tree nodes."""
    def evaluate(self, env=None):
        raise NotImplementedError
    def to_string(self):
        raise NotImplementedError
    def __repr__(self):
        return self.to_string()


class Num(Expr):
    """Numeric literal."""
    def __init__(self, value):
        self.value = value
    def evaluate(self, env=None):
        return self.value
    def to_string(self):
        return str(self.value)


class Var(Expr):
    """Variable reference."""
    def __init__(self, name):
        self.name = name
    def evaluate(self, env=None):
        if env is None or self.name not in env:
            raise NameError(f"Undefined variable: {self.name}")
        return env[self.name]
    def to_string(self):
        return self.name


class BinOp(Expr):
    """Binary operation."""
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right
    def evaluate(self, env=None):
        l = self.left.evaluate(env)
        r = self.right.evaluate(env)
        if self.op == "+":
            return l + r
        elif self.op == "-":
            return l - r
        elif self.op == "*":
            return l * r
        elif self.op == "/":
            if r == 0:
                raise ZeroDivisionError("Division by zero")
            return l / r
        raise ValueError(f"Unknown operator: {self.op}")
    def to_string(self):
        return f"({self.left.to_string()} {self.op} {self.right.to_string()})"


class UnaryOp(Expr):
    """Unary operation (negation)."""
    def __init__(self, op, operand):
        self.op = op
        self.operand = operand
    def evaluate(self, env=None):
        val = self.operand.evaluate(env)
        if self.op == "-":
            return -val
        raise ValueError(f"Unknown unary operator: {self.op}")
    def to_string(self):
        return f"({self.op}{self.operand.to_string()})"






''',
    },
    {
        "id": 'cls-009',
        "category": 'classes_oop',
        "lines": 55,
        "description": 'Observer pattern with event manager',
        "code": r'''







class Observer:
    """Base observer class."""
    def update(self, event, data=None):
        raise NotImplementedError


class FuncObserver(Observer):
    """Observer wrapping a callable."""
    def __init__(self, func):
        self.func = func
    def update(self, event, data=None):
        self.func(event, data)


class Subject:
    """Observable subject that notifies observers."""

    def __init__(self):
        self._observers = {}

    def subscribe(self, event, observer):
        """Subscribe an observer to an event."""
        if event not in self._observers:
            self._observers[event] = []
        self._observers[event].append(observer)

    def unsubscribe(self, event, observer):
        """Unsubscribe an observer from an event."""
        if event in self._observers:
            self._observers[event] = [o for o in self._observers[event] if o is not observer]

    def notify(self, event, data=None):
        """Notify all observers of an event."""
        for observer in self._observers.get(event, []):
            observer.update(event, data)


class EventManager:
    """Centralized event manager supporting multiple subjects."""

    def __init__(self):
        self._handlers = {}
        self._event_log = []

    def on(self, event, handler):
        """Register a handler function for an event."""
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    def off(self, event, handler=None):
        """Remove handler(s) for an event."""
        if handler is None:
            self._handlers.pop(event, None)
        elif event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def emit(self, event, data=None):
        """Emit an event to all registered handlers."""
        self._event_log.append({"event": event, "data": data})
        for handler in self._handlers.get(event, []):
            handler(data)

    def get_log(self):
        """Return event log."""
        return list(self._event_log)

    def clear(self):
        """Clear all handlers and log."""
        self._handlers.clear()
        self._event_log.clear()







''',
    },
    {
        "id": 'cls-010',
        "category": 'classes_oop',
        "lines": 96,
        "description": 'Plugin system with registry and hooks',
        "code": r'''





class PluginError(Exception):
    """Error related to plugin operations."""
    pass


class HookRegistry:
    """Manages hook points where plugins can attach behavior."""
    def __init__(self):
        self._hooks = {}
    def register_hook(self, name):
        """Create a new hook point."""
        if name not in self._hooks:
            self._hooks[name] = []
    def add_handler(self, hook_name, handler, priority=0):
        """Add a handler to a hook."""
        if hook_name not in self._hooks:
            self._hooks[hook_name] = []
        self._hooks[hook_name].append((priority, handler))
        self._hooks[hook_name].sort(key=lambda x: x[0])
    def call_hook(self, hook_name, *args, **kwargs):
        """Call all handlers for a hook."""
        results = []
        for priority, handler in self._hooks.get(hook_name, []):
            results.append(handler(*args, **kwargs))
        return results
    def get_hooks(self):
        """Return all registered hook names."""
        return list(self._hooks.keys())


class PluginBase:
    """Base class for all plugins."""
    name = "base_plugin"
    version = "0.0.0"
    description = "Base plugin"
    def __init__(self):
        self.enabled = True
        self.config = {}
    def activate(self, manager):
        """Called when plugin is activated."""
        pass
    def deactivate(self, manager):
        """Called when plugin is deactivated."""
        pass
    def configure(self, config):
        """Update plugin configuration."""
        self.config.update(config)


class PluginManager:
    """Manages plugin lifecycle and hook registration."""
    def __init__(self):
        self.plugins = {}
        self.hooks = HookRegistry()
        self._load_order = []
        self.hooks.register_hook("on_load")
        self.hooks.register_hook("on_unload")
    def register(self, plugin):
        """Register a plugin instance."""
        if plugin.name in self.plugins:
            raise PluginError(f"Plugin '{plugin.name}' already registered")
        self.plugins[plugin.name] = plugin
        self._load_order.append(plugin.name)
    def activate(self, name):
        """Activate a registered plugin."""
        if name not in self.plugins:
            raise PluginError(f"Plugin '{name}' not found")
        plugin = self.plugins[name]
        plugin.enabled = True
        plugin.activate(self)
        self.hooks.call_hook("on_load", plugin)
    def deactivate(self, name):
        """Deactivate a plugin."""
        if name not in self.plugins:
            raise PluginError(f"Plugin '{name}' not found")
        plugin = self.plugins[name]
        plugin.deactivate(self)
        plugin.enabled = False
        self.hooks.call_hook("on_unload", plugin)
    def get_plugin(self, name):
        """Get a plugin by name."""
        return self.plugins.get(name)
    def list_plugins(self):
        """List all registered plugins with status."""
        return [
            {"name": p.name, "version": p.version,
             "enabled": p.enabled, "description": p.description}
            for p in self.plugins.values()
        ]
    def activate_all(self):
        """Activate all registered plugins in load order."""
        for name in self._load_order:
            if not self.plugins[name].enabled:
                self.activate(name)
    def deactivate_all(self):
        """Deactivate all plugins in reverse load order."""
        for name in reversed(self._load_order):
            if self.plugins[name].enabled:
                self.deactivate(name)
    def get_active_plugins(self):
        """Return list of active plugin names."""
        return [name for name, p in self.plugins.items() if p.enabled]





''',
    },
    {
        "id": 'rec-001',
        "category": 'recursion',
        "lines": 19,
        "description": 'Factorial and fibonacci with memoization',
        "code": r'''







def factorial(n):
    """Compute factorial recursively."""
    if n < 0:
        raise ValueError("Negative input")
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def fibonacci_memo(n, memo=None):
    """Compute nth Fibonacci with memoization."""
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 0:
        return 0
    if n == 1:
        return 1
    memo[n] = fibonacci_memo(n - 1, memo) + fibonacci_memo(n - 2, memo)
    return memo[n]







''',
    },
    {
        "id": 'rec-002',
        "category": 'recursion',
        "lines": 15,
        "description": 'Recursive binary search',
        "code": r'''







def binary_search(arr, target, low=None, high=None):
    """Recursive binary search on sorted array."""
    if low is None:
        low = 0
    if high is None:
        high = len(arr) - 1
    if low > high:
        return -1
    mid = (low + high) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search(arr, target, mid + 1, high)
    else:
        return binary_search(arr, target, low, mid - 1)







''',
    },
    {
        "id": 'rec-003',
        "category": 'recursion',
        "lines": 13,
        "description": 'Tower of Hanoi solver',
        "code": r'''







def hanoi(n, source="A", target="C", auxiliary="B"):
    """Solve Tower of Hanoi, returning list of moves."""
    moves = []
    _hanoi(n, source, target, auxiliary, moves)
    return moves


def _hanoi(n, source, target, auxiliary, moves):
    """Recursive helper for Tower of Hanoi."""
    if n == 1:
        moves.append((source, target))
        return
    _hanoi(n - 1, source, auxiliary, target, moves)
    moves.append((source, target))
    _hanoi(n - 1, auxiliary, target, source, moves)







''',
    },
    {
        "id": 'rec-004',
        "category": 'recursion',
        "lines": 17,
        "description": 'Power set generation',
        "code": r'''







def power_set(items):
    """Generate all subsets of a set/list recursively."""
    if not items:
        return [[]]
    first = items[0]
    rest = items[1:]
    subsets_without = power_set(rest)
    subsets_with = [[first] + subset for subset in subsets_without]
    return subsets_without + subsets_with


def power_set_iterative(items):
    """Generate power set iteratively using bitmask."""
    n = len(items)
    result = []
    for i in range(2 ** n):
        subset = [items[j] for j in range(n) if i & (1 << j)]
        result.append(subset)
    return result







''',
    },
    {
        "id": 'rec-005',
        "category": 'recursion',
        "lines": 43,
        "description": 'Merge sort recursive',
        "code": r'''







def merge_sort(arr):
    """Sort an array using merge sort."""
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)


def merge(left, right):
    """Merge two sorted arrays."""
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


def merge_sort_with_count(arr):
    """Merge sort that also counts inversions."""
    if len(arr) <= 1:
        return arr, 0
    mid = len(arr) // 2
    left, left_inv = merge_sort_with_count(arr[:mid])
    right, right_inv = merge_sort_with_count(arr[mid:])
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
    },
    {
        "id": 'rec-006',
        "category": 'recursion',
        "lines": 30,
        "description": 'Flatten arbitrarily nested structure',
        "code": r'''







def flatten(nested):
    """Recursively flatten an arbitrarily nested list."""
    result = []
    for item in nested:
        if isinstance(item, (list, tuple)):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result


def flatten_with_depth(nested, max_depth=-1, current_depth=0):
    """Flatten with optional max depth limit."""
    result = []
    for item in nested:
        if isinstance(item, (list, tuple)) and (max_depth < 0 or current_depth < max_depth):
            result.extend(flatten_with_depth(item, max_depth, current_depth + 1))
        else:
            result.append(item)
    return result


def deep_map(nested, func):
    """Apply a function to all leaf values in a nested structure."""
    if isinstance(nested, (list, tuple)):
        return [deep_map(item, func) for item in nested]
    return func(nested)


def nested_depth(structure):
    """Calculate the maximum nesting depth."""
    if not isinstance(structure, (list, tuple)):
        return 0
    if not structure:
        return 1
    return 1 + max(nested_depth(item) for item in structure)







''',
    },
    {
        "id": 'rec-007',
        "category": 'recursion',
        "lines": 39,
        "description": 'Generate all permutations',
        "code": r'''







def permutations(items):
    """Generate all permutations of a list recursively."""
    if len(items) <= 1:
        return [list(items)]
    result = []
    for i, item in enumerate(items):
        rest = items[:i] + items[i + 1:]
        for perm in permutations(rest):
            result.append([item] + perm)
    return result


def permutations_unique(items):
    """Generate unique permutations (handles duplicates)."""
    if len(items) <= 1:
        return [list(items)]
    result = []
    seen = set()
    for i, item in enumerate(items):
        if item in seen:
            continue
        seen.add(item)
        rest = items[:i] + items[i + 1:]
        for perm in permutations_unique(rest):
            result.append([item] + perm)
    return result


def nth_permutation(items, n):
    """Find the nth permutation (0-indexed) without generating all."""
    items = list(items)
    result = []
    available = sorted(items)
    n_remaining = n
    for i in range(len(items)):
        fact = 1
        for j in range(1, len(available)):
            fact *= j
        idx = n_remaining // fact
        result.append(available[idx])
        available.pop(idx)
        n_remaining %= fact
    return result







''',
    },
    {
        "id": 'rec-008',
        "category": 'recursion',
        "lines": 78,
        "description": 'Recursive descent arithmetic parser',
        "code": r'''




class ParseError(Exception):
    """Parsing error."""
    pass


def lex(text):
    """Tokenize arithmetic expression."""
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text[i].isdigit():
            start = i
            while i < len(text) and (text[i].isdigit() or text[i] == "."):
                i += 1
            tokens.append(("NUM", float(text[start:i])))
        elif text[i] in "+-*/":
            tokens.append(("OP", text[i]))
            i += 1
        elif text[i] == "(":
            tokens.append(("LPAREN", text[i]))
            i += 1
        elif text[i] == ")":
            tokens.append(("RPAREN", text[i]))
            i += 1
        else:
            raise ParseError(f"Unexpected char: {text[i]}")
    return tokens


class RecursiveDescentParser:
    """Recursive descent parser for arithmetic."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def eat(self, kind=None):
        tok = self.peek()
        if tok is None:
            raise ParseError("Unexpected end")
        if kind and tok[0] != kind:
            raise ParseError(f"Expected {kind}")
        self.pos += 1
        return tok

    def parse_expr(self):
        """Parse addition and subtraction."""
        left = self.parse_term()
        while self.peek() and self.peek()[0] == "OP" and self.peek()[1] in "+-":
            op = self.eat()[1]
            right = self.parse_term()
            left = left + right if op == "+" else left - right
        return left

    def parse_term(self):
        """Parse multiplication and division."""
        left = self.parse_factor()
        while self.peek() and self.peek()[0] == "OP" and self.peek()[1] in "*/":
            op = self.eat()[1]
            right = self.parse_factor()
            left = left * right if op == "*" else left / right
        return left

    def parse_factor(self):
        """Parse factor: NUM | (expr) | -factor"""
        tok = self.peek()
        if tok is None:
            raise ParseError("Unexpected end")
        if tok[0] == "NUM":
            self.eat()
            return tok[1]
        if tok[0] == "LPAREN":
            self.eat()
            val = self.parse_expr()
            self.eat("RPAREN")
            return val
        if tok[0] == "OP" and tok[1] == "-":
            self.eat()
            return -self.parse_factor()
        raise ParseError(f"Unexpected: {tok[1]}")


def parse_and_eval(text):
    """Parse and evaluate an arithmetic expression."""
    return RecursiveDescentParser(lex(text)).parse_expr()




''',
    },
    {
        "id": 'rec-009',
        "category": 'recursion',
        "lines": 64,
        "description": 'Sudoku solver with backtracking',
        "code": r'''







def solve_sudoku(board):
    """Solve a 9x9 Sudoku puzzle in-place using backtracking."""
    empty = find_empty(board)
    if empty is None:
        return True
    row, col = empty
    for num in range(1, 10):
        if is_valid_placement(board, row, col, num):
            board[row][col] = num
            if solve_sudoku(board):
                return True
            board[row][col] = 0
    return False


def find_empty(board):
    """Find the next empty cell (value 0)."""
    for r in range(9):
        for c in range(9):
            if board[r][c] == 0:
                return (r, c)
    return None


def is_valid_placement(board, row, col, num):
    """Check if placing num at (row, col) is valid."""
    if num in board[row]:
        return False
    for r in range(9):
        if board[r][col] == num:
            return False
    box_r, box_c = 3 * (row // 3), 3 * (col // 3)
    for r in range(box_r, box_r + 3):
        for c in range(box_c, box_c + 3):
            if board[r][c] == num:
                return False
    return True


def print_board(board):
    """Return string representation of the board."""
    lines = []
    for r in range(9):
        row_str = ""
        for c in range(9):
            if c > 0 and c % 3 == 0:
                row_str += "| "
            row_str += str(board[r][c]) + " "
        lines.append(row_str)
        if r < 8 and (r + 1) % 3 == 0:
            lines.append("-" * 21)
    return "\n".join(lines)


def is_solved(board):
    """Check if the board is completely and validly solved."""
    for r in range(9):
        if sorted(board[r]) != list(range(1, 10)):
            return False
    for c in range(9):
        col = [board[r][c] for r in range(9)]
        if sorted(col) != list(range(1, 10)):
            return False
    for br in range(3):
        for bc in range(3):
            box = []
            for r in range(br * 3, br * 3 + 3):
                for c in range(bc * 3, bc * 3 + 3):
                    box.append(board[r][c])
            if sorted(box) != list(range(1, 10)):
                return False
    return True







''',
    },
    {
        "id": 'rec-010',
        "category": 'recursion',
        "lines": 100,
        "description": 'Tree operations with traversals and utilities',
        "code": r'''







class TreeNode:
    """Binary tree node."""
    def __init__(self, val, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right


def build_tree(values):
    """Build binary tree from level-order list (None for absent nodes)."""
    if not values or values[0] is None:
        return None
    root = TreeNode(values[0])
    queue = [root]
    i = 1
    while i < len(values) and queue:
        node = queue.pop(0)
        if i < len(values) and values[i] is not None:
            node.left = TreeNode(values[i])
            queue.append(node.left)
        i += 1
        if i < len(values) and values[i] is not None:
            node.right = TreeNode(values[i])
            queue.append(node.right)
        i += 1
    return root


def inorder(node):
    """In-order traversal."""
    if node is None:
        return []
    return inorder(node.left) + [node.val] + inorder(node.right)


def preorder(node):
    """Pre-order traversal."""
    if node is None:
        return []
    return [node.val] + preorder(node.left) + preorder(node.right)


def postorder(node):
    """Post-order traversal."""
    if node is None:
        return []
    return postorder(node.left) + postorder(node.right) + [node.val]


def level_order(root):
    """Level-order (BFS) traversal."""
    if root is None:
        return []
    result = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        result.append(node.val)
        if node.left:
            queue.append(node.left)
        if node.right:
            queue.append(node.right)
    return result


def height(node):
    """Compute tree height."""
    if node is None:
        return 0
    return 1 + max(height(node.left), height(node.right))


def mirror(node):
    """Create a mirror image of the tree."""
    if node is None:
        return None
    return TreeNode(node.val, mirror(node.right), mirror(node.left))


def diameter(node):
    """Compute diameter (longest path between any two nodes)."""
    if node is None:
        return 0
    left_h = height(node.left)
    right_h = height(node.right)
    left_d = diameter(node.left)
    right_d = diameter(node.right)
    return max(left_h + right_h, left_d, right_d)


def lca(root, p, q):
    """Find lowest common ancestor of nodes with values p and q."""
    if root is None:
        return None
    if root.val == p or root.val == q:
        return root
    left = lca(root.left, p, q)
    right = lca(root.right, p, q)
    if left and right:
        return root
    return left if left else right


def serialize(root):
    """Serialize tree to a list (level-order with None markers)."""
    if root is None:
        return []
    result = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node is None:
            result.append(None)
        else:
            result.append(node.val)
            queue.append(node.left)
            queue.append(node.right)
    while result and result[-1] is None:
        result.pop()
    return result






''',
    },
    {
        "id": 'alg-001',
        "category": 'algorithms',
        "lines": 12,
        "description": 'Binary search iterative',
        "code": r'''







def binary_search(arr, target):
    """Iterative binary search. Returns index or -1."""
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






''',
    },
    {
        "id": 'alg-002',
        "category": 'algorithms',
        "lines": 10,
        "description": 'Insertion sort',
        "code": r'''







def insertion_sort(arr):
    """Sort an array using insertion sort (in-place)."""
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and arr[j] > key:
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return arr






''',
    },
    {
        "id": 'alg-003',
        "category": 'algorithms',
        "lines": 12,
        "description": 'Quick sort with partition',
        "code": r'''







def quick_sort(arr):
    """Sort array using quicksort."""
    if len(arr) <= 1:
        return arr
    _quick_sort(arr, 0, len(arr) - 1)
    return arr


def _quick_sort(arr, low, high):
    """Recursive quicksort helper."""
    if low < high:
        pivot_idx = partition(arr, low, high)
        _quick_sort(arr, low, pivot_idx - 1)
        _quick_sort(arr, pivot_idx + 1, high)






''',
    },
    {
        "id": 'alg-004',
        "category": 'algorithms',
        "lines": 15,
        "description": 'BFS on adjacency list',
        "code": r'''







from collections import deque


def bfs(graph, start):
    """Breadth-first search on adjacency list. Returns visited order."""
    visited = set()
    order = []
    queue = deque([start])
    visited.add(start)
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return order






''',
    },
    {
        "id": 'alg-005',
        "category": 'algorithms',
        "lines": 33,
        "description": "Dijkstra's shortest path",
        "code": r'''







import heapq


def dijkstra(graph, start):
    """Dijkstra's shortest path from start to all vertices.
    graph: {node: [(neighbor, weight), ...]}
    Returns: {node: distance} and {node: predecessor}
    """
    distances = {start: 0}
    predecessors = {start: None}
    pq = [(0, start)]
    visited = set()
    while pq:
        dist, node = heapq.heappop(pq)
        if node in visited:
            continue
        visited.add(node)
        for neighbor, weight in graph.get(node, []):
            new_dist = dist + weight
            if neighbor not in distances or new_dist < distances[neighbor]:
                distances[neighbor] = new_dist
                predecessors[neighbor] = node
                heapq.heappush(pq, (new_dist, neighbor))
    return distances, predecessors


def shortest_path(graph, start, end):
    """Find shortest path between two nodes using Dijkstra."""
    distances, predecessors = dijkstra(graph, start)
    if end not in distances:
        return None, float("inf")
    path = []
    current = end
    while current is not None:
        path.append(current)
        current = predecessors[current]
    return list(reversed(path)), distances[end]







''',
    },
    {
        "id": 'alg-006',
        "category": 'algorithms',
        "lines": 30,
        "description": '0/1 Knapsack dynamic programming',
        "code": r'''







def knapsack(weights, values, capacity):
    """Solve 0/1 knapsack problem using dynamic programming.
    Returns maximum value achievable.
    """
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                val = dp[i - 1][w - weights[i - 1]] + values[i - 1]
                dp[i][w] = max(dp[i][w], val)
    return dp[n][capacity]


def knapsack_items(weights, values, capacity):
    """Solve knapsack and return selected item indices."""
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i - 1][w]
            if weights[i - 1] <= w:
                val = dp[i - 1][w - weights[i - 1]] + values[i - 1]
                dp[i][w] = max(dp[i][w], val)
    items = []
    w = capacity
    for i in range(n, 0, -1):
        if dp[i][w] != dp[i - 1][w]:
            items.append(i - 1)
            w -= weights[i - 1]
    return dp[n][capacity], list(reversed(items))







''',
    },
    {
        "id": 'alg-007',
        "category": 'algorithms',
        "lines": 50,
        "description": "Topological sort Kahn's algorithm",
        "code": r'''







from collections import deque


def topological_sort(graph):
    """Topological sort using Kahn's algorithm.
    graph: {node: [dependencies]}
    Returns sorted list or None if cycle exists.
    """
    in_degree = {}
    for node in graph:
        in_degree.setdefault(node, 0)
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
    queue = deque([n for n in in_degree if in_degree[n] == 0])
    result = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != len(in_degree):
        return None
    return result


def all_topological_sorts(graph):
    """Generate all valid topological orderings."""
    in_degree = {}
    for node in graph:
        in_degree.setdefault(node, 0)
        for neighbor in graph[node]:
            in_degree[neighbor] = in_degree.get(neighbor, 0) + 1
    result = []
    _all_topo(graph, in_degree, [], set(), result)
    return result


def _all_topo(graph, in_degree, path, visited, result):
    """Backtracking helper for all topological sorts."""
    found = False
    for node in sorted(in_degree.keys()):
        if in_degree[node] == 0 and node not in visited:
            found = True
            visited.add(node)
            path.append(node)
            for neighbor in graph.get(node, []):
                in_degree[neighbor] -= 1
            _all_topo(graph, in_degree, path, visited, result)
            for neighbor in graph.get(node, []):
                in_degree[neighbor] += 1
            path.pop()
            visited.remove(node)
    if not found:
        result.append(list(path))







''',
    },
    {
        "id": 'alg-008',
        "category": 'algorithms',
        "lines": 66,
        "description": 'A* pathfinding on grid',
        "code": r'''







import heapq


def heuristic(a, b):
    """Manhattan distance heuristic."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def astar(grid, start, goal):
    """A* pathfinding on a 2D grid.
    grid[r][c] == 0 means passable, 1 means blocked.
    Returns path as list of (row, col) or empty list if no path.
    """
    rows = len(grid)
    cols = len(grid[0])
    if grid[start[0]][start[1]] == 1 or grid[goal[0]][goal[1]] == 1:
        return []
    open_set = [(0 + heuristic(start, goal), 0, start)]
    came_from = {}
    g_score = {start: 0}
    closed_set = set()
    while open_set:
        f, g, current = heapq.heappop(open_set)
        if current == goal:
            return reconstruct_path(came_from, current)
        if current in closed_set:
            continue
        closed_set.add(current)
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = current[0] + dr, current[1] + dc
            neighbor = (nr, nc)
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0:
                tentative_g = g + 1
                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    g_score[neighbor] = tentative_g
                    f_score = tentative_g + heuristic(neighbor, goal)
                    heapq.heappush(open_set, (f_score, tentative_g, neighbor))
                    came_from[neighbor] = current
    return []


def reconstruct_path(came_from, current):
    """Reconstruct path from came_from map."""
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    return list(reversed(path))


def grid_neighbors(grid, pos, allow_diagonal=False):
    """Get valid neighbors for a grid position."""
    rows, cols = len(grid), len(grid[0])
    r, c = pos
    directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if allow_diagonal:
        directions += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    result = []
    for dr, dc in directions:
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0:
            result.append((nr, nc))
    return result


def print_path_on_grid(grid, path):
    """Create string representation of grid with path marked."""
    display = [["." if cell == 0 else "#" for cell in row] for row in grid]
    for r, c in path:
        display[r][c] = "*"
    if path:
        sr, sc = path[0]
        er, ec = path[-1]
        display[sr][sc] = "S"
        display[er][ec] = "E"
    return "\n".join("".join(row) for row in display)







''',
    },
    {
        "id": 'alg-009',
        "category": 'algorithms',
        "lines": 61,
        "description": 'KMP string matching algorithm',
        "code": r'''







def build_failure_table(pattern):
    """Build the KMP failure (partial match) table."""
    m = len(pattern)
    failure = [0] * m
    length = 0
    i = 1
    while i < m:
        if pattern[i] == pattern[length]:
            length += 1
            failure[i] = length
            i += 1
        else:
            if length != 0:
                length = failure[length - 1]
            else:
                failure[i] = 0
                i += 1
    return failure


def kmp_search(text, pattern):
    """Find all occurrences of pattern in text using KMP algorithm."""
    if not pattern:
        return []
    n = len(text)
    m = len(pattern)
    failure = build_failure_table(pattern)
    matches = []
    i = 0
    j = 0
    while i < n:
        if text[i] == pattern[j]:
            i += 1
            j += 1
            if j == m:
                matches.append(i - j)
                j = failure[j - 1]
        else:
            if j != 0:
                j = failure[j - 1]
            else:
                i += 1
    return matches


def kmp_contains(text, pattern):
    """Check if text contains pattern using KMP."""
    return len(kmp_search(text, pattern)) > 0


def count_occurrences(text, pattern):
    """Count non-overlapping occurrences of pattern."""
    if not pattern:
        return 0
    count = 0
    start = 0
    while True:
        matches = kmp_search(text[start:], pattern)
        if not matches:
            break
        count += 1
        start += matches[0] + len(pattern)
    return count


def first_occurrence(text, pattern):
    """Find first occurrence of pattern, or -1 if not found."""
    matches = kmp_search(text, pattern)
    return matches[0] if matches else -1







''',
    },
    {
        "id": 'alg-010',
        "category": 'algorithms',
        "lines": 100,
        "description": 'Comprehensive graph algorithms',
        "code": r'''





import heapq
from collections import deque


def prim_mst(graph):
    """Prim's algorithm for minimum spanning tree.
    graph: {node: [(neighbor, weight), ...]}
    Returns: list of (u, v, weight) edges in MST.
    """
    if not graph:
        return []
    start = next(iter(graph))
    visited = {start}
    edges = [(w, start, v) for v, w in graph.get(start, [])]
    heapq.heapify(edges)
    mst = []
    while edges and len(visited) < len(graph):
        weight, u, v = heapq.heappop(edges)
        if v in visited:
            continue
        visited.add(v)
        mst.append((u, v, weight))
        for neighbor, w in graph.get(v, []):
            if neighbor not in visited:
                heapq.heappush(edges, (w, v, neighbor))
    return mst


def bellman_ford(graph, source):
    """Bellman-Ford shortest path algorithm.
    graph: {node: [(neighbor, weight), ...]}
    Returns distances and predecessors.
    """
    nodes = set(graph.keys())
    for node in graph:
        for neighbor, _ in graph[node]:
            nodes.add(neighbor)
    dist = {n: float("inf") for n in nodes}
    pred = {n: None for n in nodes}
    dist[source] = 0
    for _ in range(len(nodes) - 1):
        for u in graph:
            for v, w in graph[u]:
                if dist[u] + w < dist[v]:
                    dist[v] = dist[u] + w
                    pred[v] = u
    for u in graph:
        for v, w in graph[u]:
            if dist[u] + w < dist[v]:
                raise ValueError("Negative weight cycle detected")
    return dist, pred


def floyd_warshall(n, edges):
    """Floyd-Warshall all-pairs shortest path.
    n: number of vertices (0 to n-1)
    edges: list of (u, v, weight)
    Returns: n x n distance matrix.
    """
    INF = float("inf")
    dist = [[INF] * n for _ in range(n)]
    for i in range(n):
        dist[i][i] = 0
    for u, v, w in edges:
        dist[u][v] = min(dist[u][v], w)
    for k in range(n):
        for i in range(n):
            for j in range(n):
                if dist[i][k] + dist[k][j] < dist[i][j]:
                    dist[i][j] = dist[i][k] + dist[k][j]
    return dist


def tarjan_scc(graph):
    """Find strongly connected components using Tarjan's algorithm."""
    index_counter = [0]
    stack = []
    lowlink = {}
    index = {}
    on_stack = set()
    sccs = []
    def strongconnect(v):
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, []):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            sccs.append(component)
    for v in graph:
        if v not in index:
            strongconnect(v)
    return sccs





def mst_weight(graph):
    """Compute total weight of minimum spanning tree."""



''',
    },
    {
        "id": 'fn-001',
        "category": 'functional',
        "lines": 19,
        "description": 'Map filter reduce implementations',
        "code": r'''







def my_map(func, iterable):
    """Apply func to each element of iterable."""
    return [func(x) for x in iterable]


def my_filter(predicate, iterable):
    """Return elements where predicate is true."""
    return [x for x in iterable if predicate(x)]


def my_reduce(func, iterable, initial=None):
    """Reduce iterable to a single value using func."""
    it = iter(iterable)
    if initial is None:
        try:
            result = next(it)
        except StopIteration:
            raise TypeError("reduce() of empty sequence with no initial value")
    else:
        result = initial
    for item in it:
        result = func(result, item)
    return result







''',
    },
    {
        "id": 'fn-002',
        "category": 'functional',
        "lines": 20,
        "description": 'Closure-based counter and accumulator',
        "code": r'''







def make_counter(start=0):
    """Create a counter using closures."""
    count = [start]
    def increment(n=1):
        count[0] += n
        return count[0]
    def get():
        return count[0]
    def reset():
        count[0] = start
    return increment, get, reset


def make_accumulator(initial=0):
    """Create an accumulator that sums added values."""
    total = [initial]
    def add(value):
        total[0] += value
        return total[0]
    def get():
        return total[0]
    return add, get







''',
    },
    {
        "id": 'fn-003',
        "category": 'functional',
        "lines": 13,
        "description": 'Decorator for timing and logging',
        "code": r'''







import time
import functools


def timer(func):
    """Decorator that measures execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        wrapper.last_elapsed = elapsed
        return result
    wrapper.last_elapsed = 0
    return wrapper






''',
    },
    {
        "id": 'fn-004',
        "category": 'functional',
        "lines": 16,
        "description": 'Partial application utility',
        "code": r'''







def partial(func, *partial_args, **partial_kwargs):
    """Create a partial application of a function."""
    def wrapper(*args, **kwargs):
        combined_kwargs = dict(partial_kwargs)
        combined_kwargs.update(kwargs)
        return func(*partial_args, *args, **combined_kwargs)
    wrapper.__name__ = f"partial({func.__name__})"
    return wrapper


def partial_right(func, *partial_args, **partial_kwargs):
    """Partial application with args appended to the right."""
    def wrapper(*args, **kwargs):
        combined_kwargs = dict(partial_kwargs)
        combined_kwargs.update(kwargs)
        return func(*args, *partial_args, **combined_kwargs)
    wrapper.__name__ = f"partial_right({func.__name__})"
    return wrapper







''',
    },
    {
        "id": 'fn-005',
        "category": 'functional',
        "lines": 37,
        "description": 'Pipeline builder composing functions',
        "code": r'''







class Pipeline:
    """Composable function pipeline (left-to-right)."""

    def __init__(self, *functions):
        self.functions = list(functions)

    def pipe(self, func):
        """Add a function to the pipeline."""
        return Pipeline(*self.functions, func)

    def __call__(self, value):
        """Execute the pipeline on a value."""
        result = value
        for func in self.functions:
            result = func(result)
        return result

    def __or__(self, other):
        """Compose two pipelines using | operator."""
        if callable(other):
            return self.pipe(other)
        return NotImplemented


def compose(*functions):
    """Compose functions right-to-left."""
    def composed(value):
        result = value
        for func in reversed(functions):
            result = func(result)
        return result
    return composed


def pipe(*functions):
    """Compose functions left-to-right."""
    def piped(value):
        result = value
        for func in functions:
            result = func(result)
        return result
    return piped


def identity(x):
    """Identity function."""
    return x







''',
    },
    {
        "id": 'fn-006',
        "category": 'functional',
        "lines": 36,
        "description": 'Memoization decorator with cache management',
        "code": r'''







import functools


def memoize(maxsize=None):
    """Memoization decorator with optional cache size limit."""
    def decorator(func):
        cache = {}
        access_order = []
        @functools.wraps(func)
        def wrapper(*args):
            if args in cache:
                return cache[args]
            result = func(*args)
            cache[args] = result
            access_order.append(args)
            if maxsize is not None and len(cache) > maxsize:
                oldest = access_order.pop(0)
                cache.pop(oldest, None)
            return result
        def cache_info():
            return {"size": len(cache), "maxsize": maxsize}
        def cache_clear():
            cache.clear()
            access_order.clear()
        wrapper.cache_info = cache_info
        wrapper.cache_clear = cache_clear
        return wrapper
    return decorator


def memoize_simple(func):
    """Simple memoization without size limit."""
    cache = {}
    @functools.wraps(func)
    def wrapper(*args):
        if args not in cache:
            cache[args] = func(*args)
        return cache[args]
    wrapper.cache = cache
    return wrapper







''',
    },
    {
        "id": 'fn-007',
        "category": 'functional',
        "lines": 40,
        "description": 'Currying utility',
        "code": r'''







def curry(func, arity=None):
    """Curry a function to accept arguments one at a time."""
    if arity is None:
        import inspect
        sig = inspect.signature(func)
        arity = len([
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
        ])
    def curried(*args):
        if len(args) >= arity:
            return func(*args[:arity])
        def inner(*more_args):
            return curried(*args, *more_args)
        return inner
    return curried


def uncurry(func):
    """Convert a curried function to accept all arguments at once."""
    def uncurried(*args):
        result = func
        for arg in args:
            result = result(arg)
        return result
    return uncurried


def curry2(func):
    """Curry a 2-argument function."""
    def outer(a):
        def inner(b):
            return func(a, b)
        return inner
    return outer


def curry3(func):
    """Curry a 3-argument function."""
    def outer(a):
        def middle(b):
            def inner(c):
                return func(a, b, c)
            return inner
        return middle
    return outer







''',
    },
    {
        "id": 'fn-008',
        "category": 'functional',
        "lines": 76,
        "description": 'Maybe and Result monadic types',
        "code": r'''







class Maybe:
    """Optional value container (Maybe monad)."""

    def __init__(self, value, is_nothing=False):
        self._value = value
        self._is_nothing = is_nothing

    @staticmethod
    def just(value):
        """Create a Maybe with a value."""
        return Maybe(value, is_nothing=False)

    @staticmethod
    def nothing():
        """Create an empty Maybe."""
        return Maybe(None, is_nothing=True)

    def is_just(self):
        return not self._is_nothing

    def is_nothing(self):
        return self._is_nothing

    def get(self, default=None):
        """Get the value or a default."""
        if self._is_nothing:
            return default
        return self._value

    def map(self, func):
        """Apply func to value if present."""
        if self._is_nothing:
            return Maybe.nothing()
        return Maybe.just(func(self._value))

    def flat_map(self, func):
        """Apply func that returns Maybe, flatten result."""
        if self._is_nothing:
            return Maybe.nothing()
        return func(self._value)

    def __repr__(self):
        if self._is_nothing:
            return "Nothing"
        return f"Just({self._value!r})"


class Result:
    """Result type representing success or failure."""

    def __init__(self, value=None, error=None, is_ok=True):
        self._value = value
        self._error = error
        self._is_ok = is_ok

    @staticmethod
    def ok(value):
        """Create a successful result."""
        return Result(value=value, is_ok=True)

    @staticmethod
    def err(error):
        """Create a failure result."""
        return Result(error=error, is_ok=False)

    def is_ok(self):
        return self._is_ok

    def is_err(self):
        return not self._is_ok

    def unwrap(self):
        """Get value or raise error."""
        if self._is_ok:
            return self._value
        raise ValueError(f"Called unwrap on Err: {self._error}")

    def unwrap_or(self, default):
        """Get value or return default."""
        return self._value if self._is_ok else default

    def map(self, func):
        """Apply func to value if Ok."""
        if self._is_ok:
            return Result.ok(func(self._value))
        return self

    def flat_map(self, func):
        """Apply func returning Result, flatten."""
        if self._is_ok:
            return func(self._value)
        return self

    def __repr__(self):
        if self._is_ok:
            return f"Ok({self._value!r})"
        return f"Err({self._error!r})"







''',
    },
    {
        "id": 'fn-009',
        "category": 'functional',
        "lines": 62,
        "description": 'Event system with handlers and filters',
        "code": r'''







class EventHandler:
    """Wraps a callable with optional event filter."""
    def __init__(self, func, event_filter=None, priority=0):
        self.func = func
        self.event_filter = event_filter
        self.priority = priority
    def should_handle(self, event):
        if self.event_filter is None:
            return True
        return self.event_filter(event)
    def __call__(self, event):
        return self.func(event)


class Event:
    """Event with name and data."""
    def __init__(self, name, data=None):
        self.name = name
        self.data = data or {}
        self.cancelled = False
    def cancel(self):
        self.cancelled = True


class EventSystem:
    """Event system with filtering and priority handling."""

    def __init__(self):
        self._handlers = {}
        self._global_handlers = []

    def on(self, event_name, func, priority=0, event_filter=None):
        """Register a handler for an event."""
        handler = EventHandler(func, event_filter, priority)
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler)
        self._handlers[event_name].sort(key=lambda h: h.priority)

    def on_any(self, func, priority=0, event_filter=None):
        """Register a handler for all events."""
        handler = EventHandler(func, event_filter, priority)
        self._global_handlers.append(handler)
        self._global_handlers.sort(key=lambda h: h.priority)

    def emit(self, event_name, data=None):
        """Emit an event, calling all matching handlers."""
        event = Event(event_name, data)
        results = []
        all_handlers = self._global_handlers + self._handlers.get(event_name, [])
        all_handlers.sort(key=lambda h: h.priority)
        for handler in all_handlers:
            if event.cancelled:
                break
            if handler.should_handle(event):
                result = handler(event)
                results.append(result)
        return results

    def off(self, event_name, func=None):
        """Remove handlers for an event."""
        if func is None:
            self._handlers.pop(event_name, None)
        elif event_name in self._handlers:
            self._handlers[event_name] = [
                h for h in self._handlers[event_name] if h.func is not func
            ]

    def clear(self):
        """Remove all handlers."""
        self._handlers.clear()
        self._global_handlers.clear()







''',
    },
    {
        "id": 'fn-010',
        "category": 'functional',
        "lines": 93,
        "description": 'Reactive stream processing',
        "code": r'''







class Stream:
    """Lazy stream with functional operations."""

    def __init__(self, iterable):
        self._data = list(iterable)

    def map(self, func):
        """Apply function to each element."""
        return Stream(func(x) for x in self._data)

    def filter(self, predicate):
        """Keep only elements matching predicate."""
        return Stream(x for x in self._data if predicate(x))

    def reduce(self, func, initial=None):
        """Reduce stream to a single value."""
        it = iter(self._data)
        if initial is None:
            try:
                result = next(it)
            except StopIteration:
                raise ValueError("Empty stream with no initial")
        else:
            result = initial
        for item in it:
            result = func(result, item)
        return result

    def take(self, n):
        """Take first n elements."""
        return Stream(self._data[:n])

    def skip(self, n):
        """Skip first n elements."""
        return Stream(self._data[n:])

    def zip_with(self, other):
        """Zip with another stream."""
        return Stream(zip(self._data, other._data))

    def chain(self, other):
        """Concatenate with another stream."""
        return Stream(self._data + other._data)

    def flat_map(self, func):
        """Map then flatten one level."""
        result = []
        for item in self._data:
            mapped = func(item)
            if hasattr(mapped, "__iter__"):
                result.extend(mapped)
            else:
                result.append(mapped)
        return Stream(result)

    def distinct(self):
        """Remove duplicates preserving order."""
        seen = set()
        result = []
        for x in self._data:
            key = x if not isinstance(x, list) else tuple(x)
            if key not in seen:
                seen.add(key)
                result.append(x)
        return Stream(result)

    def sorted(self, key=None, reverse=False):
        """Return sorted stream."""
        return Stream(sorted(self._data, key=key, reverse=reverse))

    def group_by(self, key_func):
        """Group elements by key function."""
        groups = {}
        for item in self._data:
            key = key_func(item)
            if key not in groups:
                groups[key] = []
            groups[key].append(item)
        return groups

    def for_each(self, func):
        """Apply function to each element (side effect)."""
        for item in self._data:
            func(item)

    def any(self, predicate):
        """Check if any element matches predicate."""
        return any(predicate(x) for x in self._data)

    def all(self, predicate):
        """Check if all elements match predicate."""
        return all(predicate(x) for x in self._data)

    def count(self):
        """Count elements."""
        return len(self._data)

    def first(self, default=None):
        """Get first element or default."""
        return self._data[0] if self._data else default

    def last(self, default=None):
        """Get last element or default."""
        return self._data[-1] if self._data else default

    def to_list(self):
        """Convert to Python list."""
        return list(self._data)

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)







''',
    },
    {
        "id": 'mm-001',
        "category": 'multi_module',
        "lines": 14,
        "description": 'Config loader and application bootstrap',
        "code": r'''







class Config:
    """Configuration store with dot-notation access."""
    def __init__(self, data=None):
        self._data = data or {}
    def get(self, key, default=None):
        """Get config value by dot-separated key."""
        parts = key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current






''',
    },
    {
        "id": 'mm-002',
        "category": 'multi_module',
        "lines": 20,
        "description": 'Logger with handlers',
        "code": r'''







class LogRecord:
    """Represents a single log entry."""
    def __init__(self, level, message, logger_name="root"):
        self.level = level
        self.message = message
        self.logger_name = logger_name


class Handler:
    """Base log handler."""
    def __init__(self, min_level=0):
        self.min_level = min_level
    def emit(self, record):
        raise NotImplementedError


class ConsoleHandler(Handler):
    """Handler that stores messages (simulates console)."""
    def __init__(self, min_level=0):
        super().__init__(min_level)
        self.messages = []
    def emit(self, record):
        if record.level >= self.min_level:
            self.messages.append(f"[{record.level}] {record.message}")






''',
    },
    {
        "id": 'mm-003',
        "category": 'multi_module',
        "lines": 16,
        "description": 'Repository pattern with in-memory storage',
        "code": r'''







class Repository:
    """In-memory repository with CRUD and query operations."""

    def __init__(self):
        self._store = {}
        self._next_id = 1

    def add(self, entity):
        """Add entity, assigning an ID if not present."""
        if "id" not in entity:
            entity["id"] = self._next_id
            self._next_id += 1
        self._store[entity["id"]] = dict(entity)
        return entity["id"]

    def get(self, entity_id):
        """Get entity by ID."""
        item = self._store.get(entity_id)
        return dict(item) if item else None






''',
    },
    {
        "id": 'mm-004',
        "category": 'multi_module',
        "lines": 18,
        "description": 'Factory pattern with registry',
        "code": r'''







class FactoryRegistry:
    """Factory pattern with type registry."""

    def __init__(self):
        self._creators = {}

    def register(self, type_name, creator):
        """Register a creator function for a type."""
        self._creators[type_name] = creator

    def create(self, type_name, *args, **kwargs):
        """Create an instance of the registered type."""
        if type_name not in self._creators:
            raise KeyError(f"Unknown type: {type_name}")
        return self._creators[type_name](*args, **kwargs)

    def registered_types(self):
        """Return list of registered type names."""
        return list(self._creators.keys())

    def is_registered(self, type_name):
        """Check if a type is registered."""
        return type_name in self._creators






''',
    },
    {
        "id": 'mm-005',
        "category": 'multi_module',
        "lines": 29,
        "description": 'MVC pattern for simple entity',
        "code": r'''







class Model:
    """Data model for entities."""
    def __init__(self):
        self._data = {}
        self._listeners = []
    def set(self, key, value):
        old = self._data.get(key)
        self._data[key] = value
        if old != value:
            self._notify(key, old, value)
    def get(self, key, default=None):
        return self._data.get(key, default)
    def to_dict(self):
        return dict(self._data)
    def add_listener(self, listener):
        self._listeners.append(listener)
    def _notify(self, key, old, new):
        for listener in self._listeners:
            listener(key, old, new)


class View:
    """View that renders model state."""
    def __init__(self):
        self.last_render = None
    def render(self, data):
        lines = []
        for key, value in sorted(data.items()):
            lines.append(f"{key}: {value}")
        self.last_render = "\n".join(lines)
        return self.last_render






''',
    },
    {
        "id": 'mm-006',
        "category": 'multi_module',
        "lines": 35,
        "description": 'Service layer with repository and validator',
        "code": r'''







class Validator:
    """Validates entity data."""
    def __init__(self, rules=None):
        self.rules = rules or {}
    def add_rule(self, field, check, message):
        """Add a validation rule for a field."""
        if field not in self.rules:
            self.rules[field] = []
        self.rules[field].append((check, message))
    def validate(self, data):
        """Validate data. Returns list of error messages."""
        errors = []
        for field, checks in self.rules.items():
            value = data.get(field)
            for check, message in checks:
                if not check(value):
                    errors.append(f"{field}: {message}")
        return errors


class InMemoryRepo:
    """Simple in-memory repository."""
    def __init__(self):
        self._store = {}
        self._next_id = 1
    def save(self, entity):
        if "id" not in entity:
            entity["id"] = self._next_id
            self._next_id += 1
        self._store[entity["id"]] = dict(entity)
        return entity["id"]
    def find_by_id(self, eid):
        return dict(self._store[eid]) if eid in self._store else None
    def find_all(self):
        return [dict(e) for e in self._store.values()]
    def delete(self, eid):
        return self._store.pop(eid, None) is not None






''',
    },
    {
        "id": 'mm-007',
        "category": 'multi_module',
        "lines": 44,
        "description": 'Event bus with publishers and subscribers',
        "code": r'''







class EventBus:
    """Central event bus for decoupled communication."""
    def __init__(self):
        self._subscribers = {}
        self._event_history = []
    def subscribe(self, event_type, handler):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
    def unsubscribe(self, event_type, handler):
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h is not handler
            ]
    def publish(self, event_type, data=None):
        self._event_history.append({"type": event_type, "data": data})
        for handler in self._subscribers.get(event_type, []):
            handler(data)
    def get_history(self):
        return list(self._event_history)
    def clear(self):
        self._subscribers.clear()
        self._event_history.clear()


class Publisher:
    """Publishes events to a bus."""
    def __init__(self, bus, source_name):
        self.bus = bus
        self.source_name = source_name
    def emit(self, event_type, data=None):
        payload = {"source": self.source_name}
        if data:
            payload.update(data)
        self.bus.publish(event_type, payload)


class Subscriber:
    """Subscribes to events from a bus."""
    def __init__(self, bus):
        self.bus = bus
        self.received = []
    def listen(self, event_type):
        self.bus.subscribe(event_type, self._handle)
    def _handle(self, data):
        self.received.append(data)
    def get_received(self):
        return list(self.received)







''',
    },
    {
        "id": 'mm-008',
        "category": 'multi_module',
        "lines": 61,
        "description": 'Middleware chain with request and response',
        "code": r'''







class Request:
    """HTTP-like request object."""
    def __init__(self, method="GET", path="/", headers=None, body=None):
        self.method = method
        self.path = path
        self.headers = headers or {}
        self.body = body
        self.context = {}


class Response:
    """HTTP-like response object."""
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}


class Middleware:
    """Base middleware class."""
    def process(self, request, next_handler):
        """Process request and call next handler."""
        return next_handler(request)


class LoggingMiddleware(Middleware):
    """Middleware that logs requests."""
    def __init__(self):
        self.log = []
    def process(self, request, next_handler):
        self.log.append(f"{request.method} {request.path}")
        return next_handler(request)


class AuthMiddleware(Middleware):
    """Middleware that checks for auth token."""
    def __init__(self, valid_tokens=None):
        self.valid_tokens = valid_tokens or set()
    def process(self, request, next_handler):
        token = request.headers.get("Authorization", "")
        if token not in self.valid_tokens and self.valid_tokens:
            return Response(status=401, body="Unauthorized")
        request.context["authenticated"] = True
        return next_handler(request)


class CorsMiddleware(Middleware):
    """Middleware that adds CORS headers."""
    def __init__(self, allowed_origins=None):
        self.allowed_origins = allowed_origins or ["*"]
    def process(self, request, next_handler):
        response = next_handler(request)
        response.headers["Access-Control-Allow-Origin"] = ", ".join(self.allowed_origins)
        return response


class MiddlewareChain:
    """Chains middlewares and a final handler."""
    def __init__(self, handler):
        self.handler = handler
        self.middlewares = []
    def add(self, middleware):
        """Add middleware to the chain."""
        self.middlewares.append(middleware)
    def run(self, request):
        """Run request through the chain."""
        def build_chain(index):
            if index >= len(self.middlewares):
                return self.handler
            def next_handler(req):
                return self.middlewares[index].process(req, build_chain(index + 1))
            return next_handler
        return build_chain(0)(request)







''',
    },
    {
        "id": 'mm-009',
        "category": 'multi_module',
        "lines": 53,
        "description": 'Job scheduler with workers and queue',
        "code": r'''







import time
from collections import deque


class Job:
    """Represents a schedulable job."""
    def __init__(self, job_id, name, func, priority=0):
        self.job_id = job_id
        self.name = name
        self.func = func
        self.priority = priority
        self.status = "pending"
        self.result = None
        self.error = None
    def execute(self):
        """Execute the job."""
        try:
            self.status = "running"
            self.result = self.func()
            self.status = "completed"
        except Exception as e:
            self.error = str(e)
            self.status = "failed"
    def __repr__(self):
        return f"Job({self.job_id}, {self.name!r}, status={self.status})"


class JobQueue:
    """Priority queue for jobs."""
    def __init__(self):
        self._queue = []
    def enqueue(self, job):
        self._queue.append(job)
        self._queue.sort(key=lambda j: j.priority)
    def dequeue(self):
        if not self._queue:
            return None
        return self._queue.pop(0)
    def is_empty(self):
        return len(self._queue) == 0
    def size(self):
        return len(self._queue)
    def peek(self):
        return self._queue[0] if self._queue else None


class Worker:
    """Processes jobs from a queue."""
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.jobs_processed = 0
        self.busy = False
    def process(self, job):
        """Process a single job."""
        self.busy = True
        job.execute()
        self.jobs_processed += 1
        self.busy = False
        return job






''',
    },
    {
        "id": 'mm-010',
        "category": 'multi_module',
        "lines": 100,
        "description": 'Mini web framework with routing',
        "code": r'''




class Request:
    """Represents an HTTP request."""
    def __init__(self, method, path, headers=None, body=None, query_params=None):
        self.method = method.upper()
        self.path = path
        self.headers = headers or {}
        self.body = body
        self.query_params = query_params or {}
        self.path_params = {}


class Response:
    """Represents an HTTP response."""
    def __init__(self, status=200, body="", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {"Content-Type": "text/plain"}
    @staticmethod
    def ok(body="OK"):
        return Response(200, body)
    @staticmethod
    def not_found(body="Not Found"):
        return Response(404, body)
    @staticmethod
    def error(body="Internal Server Error"):
        return Response(500, body)
    def __repr__(self):
        return f"Response(status={self.status})"


class Route:
    """Represents a URL route pattern."""
    def __init__(self, method, pattern, handler):
        self.method = method.upper()
        self.pattern = pattern
        self.handler = handler
        self.parts = pattern.strip("/").split("/") if pattern != "/" else []
    def match(self, method, path):
        """Check if request matches this route."""
        if method != self.method:
            return None
        path_parts = path.strip("/").split("/") if path != "/" else []
        if len(path_parts) != len(self.parts):
            return None
        params = {}
        for route_part, path_part in zip(self.parts, path_parts):
            if route_part.startswith(":"):
                params[route_part[1:]] = path_part
            elif route_part != path_part:
                return None
        return params


class Router:
    """URL router that matches requests to handlers."""
    def __init__(self):
        self.routes = []
    def add_route(self, method, pattern, handler):
        """Register a route."""
        self.routes.append(Route(method, pattern, handler))
    def get(self, pattern, handler):
        self.add_route("GET", pattern, handler)
    def post(self, pattern, handler):
        self.add_route("POST", pattern, handler)
    def resolve(self, method, path):
        """Find matching route."""
        for route in self.routes:
            params = route.match(method, path)
            if params is not None:
                return route.handler, params
        return None, None


class App:
    """Mini web application framework."""
    def __init__(self):
        self.router = Router()
        self.middlewares = []
    def route(self, method, pattern):
        """Decorator to register a route."""
        def decorator(func):
            self.router.add_route(method, pattern, func)
            return func
        return decorator
    def use(self, middleware):
        """Add middleware function."""
        self.middlewares.append(middleware)
    def handle(self, request):
        """Handle an incoming request through middleware chain."""
        def dispatch(req):
            handler, params = self.router.resolve(req.method, req.path)
            if handler is None:
                return Response.not_found()
            req.path_params = params
            try:
                return handler(req)
            except Exception as e:
                return Response.error(str(e))
        chain = dispatch
        for mw in reversed(self.middlewares):
            prev = chain
            def make_next(m, nxt):
                return lambda req: m(req, nxt)
            chain = make_next(mw, prev)
        return chain(request)



    def simulate(self, method, path, headers=None, body=None):
        """Simulate a request for testing."""
        req = Request(method, path, headers, body)



''',
    },
]
