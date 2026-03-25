from __future__ import annotations

"""Functor gap analysis: Python ↔ JavaScript numeric type systems."""

__all__ = [
    "PyNumericKind",
    "JSNumericKind",
    "NumericTrap",
    "NUMERIC_TRAPS",
    "SafeArithmeticPatterns",
    "NumberCoercionTable",
]

import math
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# 1. Python numeric kinds
# ---------------------------------------------------------------------------

class PyNumericKind(str, Enum):
    INT = "int"
    FLOAT = "float"
    COMPLEX = "complex"
    DECIMAL = "decimal.Decimal"
    FRACTION = "fractions.Fraction"
    BOOL = "bool"

    def is_exact(self) -> bool:
        """Return True when the type can represent integers without rounding."""
        return self in (
            PyNumericKind.INT,
            PyNumericKind.DECIMAL,
            PyNumericKind.FRACTION,
            PyNumericKind.BOOL,
        )


# ---------------------------------------------------------------------------
# 2. JavaScript numeric kinds
# ---------------------------------------------------------------------------

class JSNumericKind(str, Enum):
    NUMBER = "number"        # IEEE 754 double-precision
    BIGINT = "bigint"
    BOOLEAN = "boolean"

    def is_exact(self) -> bool:
        """Return True when the type can represent integers without rounding."""
        return self is JSNumericKind.BIGINT


# ---------------------------------------------------------------------------
# 3. NumericTrap dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NumericTrap:
    trap_id: str
    description: str
    python_expr: str
    python_result: str
    js_expr: str
    js_result: str
    is_silent: bool
    safe_js_alternative: str


# ---------------------------------------------------------------------------
# 4. NUMERIC_TRAPS catalogue
# ---------------------------------------------------------------------------

NUMERIC_TRAPS: list[NumericTrap] = [
    NumericTrap(
        trap_id="integer_precision_loss",
        description=(
            "Python int is arbitrary-precision; JS Number (IEEE 754 double) "
            "has only 53 significant bits. Integers beyond 2**53 are silently "
            "rounded to the nearest representable double."
        ),
        python_expr="2**53 + 1",
        python_result="9007199254740993",
        js_expr="2**53 + 1",
        js_result="9007199254740992",
        is_silent=True,
        safe_js_alternative="BigInt(2)**BigInt(53) + BigInt(1)  // → 9007199254740993n",
    ),
    NumericTrap(
        trap_id="integer_division",
        description=(
            "Python // is floor division and always returns an int. "
            "JS has no integer-division operator; / always returns a float."
        ),
        python_expr="7 // 2",
        python_result="3",
        js_expr="7 / 2",
        js_result="3.5",
        is_silent=False,
        safe_js_alternative="Math.trunc(7 / 2)  // → 3",
    ),
    NumericTrap(
        trap_id="negative_modulo",
        description=(
            "Python % follows the sign of the divisor (always non-negative "
            "when divisor is positive). JS % follows the sign of the dividend."
        ),
        python_expr="(-7) % 3",
        python_result="2",
        js_expr="-7 % 3",
        js_result="-1",
        is_silent=True,
        safe_js_alternative="((-7 % 3) + 3) % 3  // → 2",
    ),
    NumericTrap(
        trap_id="float_to_int_truncation",
        description=(
            "Python int() truncates toward zero and works for any magnitude. "
            "JS `x | 0` bitwise-or coerces to a signed 32-bit integer first, "
            "silently wrapping large values; use Math.trunc instead."
        ),
        python_expr="int(3.9)",
        python_result="3",
        js_expr="3.9 | 0",
        js_result="3  (but 2147483648.9 | 0 → -2147483648, silent wrap!)",
        is_silent=True,
        safe_js_alternative="Math.trunc(3.9)  // → 3, safe for any magnitude",
    ),
    NumericTrap(
        trap_id="string_to_number",
        description=(
            "Python int()/float() are strict: they raise ValueError on "
            "whitespace-only strings, empty strings, or non-numeric content. "
            "JS Number() silently coerces: empty/whitespace → 0, invalid → NaN."
        ),
        python_expr='int("42 ")',
        python_result="ValueError: invalid literal for int()",
        js_expr='Number("42 ")',
        js_result='42  (also: Number("") → 0, Number("  ") → 0, Number("abc") → NaN)',
        is_silent=True,
        safe_js_alternative=(
            "Number.isFinite(Number(x)) ? Number(x) : null  "
            "// explicit guard against NaN / ±Infinity"
        ),
    ),
    NumericTrap(
        trap_id="number_plus_string",
        description=(
            "Python raises TypeError when adding int and str. "
            "JS silently converts the number to a string and concatenates."
        ),
        python_expr='1 + "2"',
        python_result='TypeError: unsupported operand type(s)',
        js_expr='1 + "2"',
        js_result='"12"',
        is_silent=True,
        safe_js_alternative='Number(1) + Number("2")  // → 3',
    ),
    NumericTrap(
        trap_id="nan_equality",
        description=(
            "NaN !== NaN in both languages — equality checks are always False. "
            "The subtlety: JS isNaN() coerces its argument first "
            "(isNaN('abc') → true), whereas Number.isNaN() does not coerce "
            "(Number.isNaN('abc') → false). Python math.isnan() only accepts "
            "float-like values and raises TypeError on strings."
        ),
        python_expr="float('nan') == float('nan')",
        python_result="False",
        js_expr="NaN === NaN",
        js_result="false  (isNaN('abc') → true; Number.isNaN('abc') → false)",
        is_silent=False,
        safe_js_alternative="Number.isNaN(x)  // strict, no coercion",
    ),
    NumericTrap(
        trap_id="infinity_arithmetic",
        description=(
            "Both Python and JS produce NaN from Inf - Inf, "
            "so behavior is consistent here."
        ),
        python_expr="float('inf') - float('inf')",
        python_result="nan",
        js_expr="Infinity - Infinity",
        js_result="NaN",
        is_silent=False,
        safe_js_alternative=(
            "Number.isFinite(a) && Number.isFinite(b) ? a - b : null"
        ),
    ),
    NumericTrap(
        trap_id="zero_division",
        description=(
            "Python raises ZeroDivisionError on 1/0. "
            "JS silently returns Infinity (or -Infinity / NaN for 0/0)."
        ),
        python_expr="1 / 0",
        python_result="ZeroDivisionError: division by zero",
        js_expr="1 / 0",
        js_result="Infinity",
        is_silent=True,
        safe_js_alternative="b !== 0 ? a / b : null",
    ),
    NumericTrap(
        trap_id="integer_zero_division",
        description=(
            "Python raises ZeroDivisionError on integer floor division by zero. "
            "JS has no native integer floor division; / returns Infinity silently."
        ),
        python_expr="1 // 0",
        python_result="ZeroDivisionError: integer division or modulo by zero",
        js_expr="1 / 0  // (no // operator in JS)",
        js_result="Infinity",
        is_silent=True,
        safe_js_alternative="b !== 0 ? Math.trunc(a / b) : null",
    ),
    NumericTrap(
        trap_id="bitwise_32bit",
        description=(
            "Python bitwise shifts work on arbitrary-precision integers. "
            "JS bitwise operators coerce operands to signed 32-bit integers "
            "first, silently discarding high bits."
        ),
        python_expr="1 << 33",
        python_result="8589934592",
        js_expr="1 << 33",
        js_result="2  // 33 mod 32 = 1, so 1 << 1",
        is_silent=True,
        safe_js_alternative="BigInt(1) << BigInt(33)  // → 8589934592n",
    ),
    NumericTrap(
        trap_id="right_shift_unsigned",
        description=(
            "Python has no unsigned right shift; >> always sign-extends. "
            "JS >>> fills with zeros regardless of sign, treating the value "
            "as unsigned 32-bit. -1 >>> 0 gives the max uint32."
        ),
        python_expr="-1 >> 0",
        python_result="-1",
        js_expr="-1 >>> 0",
        js_result="4294967295",
        is_silent=False,
        safe_js_alternative=(
            "Use >>> only when uint32 semantics are intended; "
            "prefer BigInt for large unsigned values."
        ),
    ),
    NumericTrap(
        trap_id="floor_vs_trunc_negative",
        description=(
            "NOT A TRAP for floor or trunc in isolation: "
            "math.floor(-2.5) == Math.floor(-2.5) == -3 (same). "
            "int(-2.5) == Math.trunc(-2.5) == -2 (same). "
            "The real trap is confusing floor with trunc: Python int() truncates "
            "toward zero while Python // floors toward negative infinity."
        ),
        python_expr="math.floor(-2.5)  # also: int(-2.5) → -2 (truncation)",
        python_result="-3  (floor)  /  -2  (int/trunc)",
        js_expr="Math.floor(-2.5)  // also: Math.trunc(-2.5) → -2",
        js_result="-3  (floor)  /  -2  (trunc)",
        is_silent=False,
        safe_js_alternative=(
            "Be explicit: use Math.floor() for floor semantics, "
            "Math.trunc() for truncation semantics."
        ),
    ),
    NumericTrap(
        trap_id="number_parsing_radix",
        description=(
            "Python int(s, base) requires an explicit base. "
            "JS parseInt() auto-detects 0x prefix but historically treated "
            "leading-zero strings as octal; always pass the radix argument."
        ),
        python_expr='int("0x1f", 16)',
        python_result="31",
        js_expr='parseInt("0x1f", 16)',
        js_result='31  (but parseInt("08") was 0 in old engines without radix)',
        is_silent=True,
        safe_js_alternative='parseInt("0x1f", 16)  // always supply the radix',
    ),
    NumericTrap(
        trap_id="power_operator",
        description=(
            "Python ** raises complex numbers for fractional powers of negatives. "
            "JS ** (ES2016+) returns NaN for the same expression instead of "
            "computing the complex root."
        ),
        python_expr="(-1) ** 0.5",
        python_result="(6.123233995736766e-17+1j)  # complex result",
        js_expr="(-1) ** 0.5",
        js_result="NaN",
        is_silent=True,
        safe_js_alternative=(
            "Check sign before exponentiation; use a complex-number library "
            "if complex results are needed."
        ),
    ),
    NumericTrap(
        trap_id="max_safe_integer_check",
        description=(
            "Python integers have no upper bound. "
            "JS Number can only safely represent integers in "
            "[-Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER] "
            "(i.e., ±2^53 - 1). Operations outside this range may silently "
            "produce wrong results."
        ),
        python_expr="2**53 - 1 + 2",
        python_result="9007199254740993",
        js_expr="Number.MAX_SAFE_INTEGER + 2",
        js_result="9007199254740994  // off by one due to double rounding",
        is_silent=True,
        safe_js_alternative=(
            "Number.isSafeInteger(x)  // guard before arithmetic; "
            "use BigInt for IDs and large counters"
        ),
    ),
]


# ---------------------------------------------------------------------------
# 5. SafeArithmeticPatterns
# ---------------------------------------------------------------------------

class SafeArithmeticPatterns:
    """Factory for safe JS arithmetic expressions that mirror Python semantics."""

    def safe_division(self, numerator: str, denominator: str, language: str) -> str:
        """
        Return a safe division expression.

        Python simply uses `/`; JS guards against zero to avoid silent Infinity.
        """
        if language.lower() == "python":
            return f"{numerator} / {denominator}"
        return f"{denominator} !== 0 ? {numerator} / {denominator} : null"

    def safe_integer_division(self, a: str, b: str) -> str:
        """Return a JS expression that truncates toward zero, matching Python int //."""
        return f"Math.trunc({a} / {b})"

    def safe_modulo(self, a: str, b: str) -> str:
        """
        Return a JS modulo expression that always returns a non-negative result
        when the divisor is positive, matching Python's % behaviour.
        """
        return f"(({a} % {b}) + {b}) % {b}"

    def bigint_required(self, value_description: str) -> bool:
        """
        Heuristic: return True when a value described by *value_description*
        is likely to exceed 2^53 and therefore requires BigInt in JS.
        """
        lower = value_description.lower()
        keywords = ("id", "timestamp", "unix_time", "user_id")
        return any(kw in lower for kw in keywords)


# ---------------------------------------------------------------------------
# 6. NumberCoercionTable
# ---------------------------------------------------------------------------

class NumberCoercionTable:
    """
    Documents the output of the JS `Number()` coercion function for common
    input values.  The table is consulted at class level; no instance needed.
    """

    _TABLE: dict[str, str] = {
        '""': "0",
        '" "': "0",
        '"3"': "3",
        '"3.5"': "3.5",
        '"abc"': "NaN",
        '"0x1f"': "31",
        "true": "1",
        "false": "0",
        "null": "0",
        "undefined": "NaN",
        "[]": "0",
        "[1]": "1",
        "[1,2]": "NaN",
        "{}": "NaN",
    }

    @classmethod
    def coerce_to_number(cls, js_value: str) -> str:
        """
        Return the string representation of what ``Number(js_value)`` produces
        in JavaScript.  Raises KeyError for unknown inputs.
        """
        return cls._TABLE[js_value]
