from __future__ import annotations

__all__ = [
    "CoercedTo",
    "TruthinessResult",
    "TRUTHINESS_TABLE",
    "AbstractEqualityRule",
    "ABSTRACT_EQUALITY_RULES",
    "CoercionAnalyzer",
    "NullishCoalescingGuide",
]

from dataclasses import dataclass, field
from enum import Enum


class CoercedTo(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    UNDEFINED = "undefined"


@dataclass(frozen=True)
class TruthinessResult:
    python_value_repr: str
    python_bool: bool
    js_bool: bool
    is_trap: bool  # True when they differ
    explanation: str


TRUTHINESS_TABLE: list[TruthinessResult] = [
    TruthinessResult(
        python_value_repr="[]",
        python_bool=False,
        js_bool=True,
        is_trap=True,
        explanation=(
            "Python: empty list is falsy. "
            "JS: any object (including []) is truthy — object reference exists."
        ),
    ),
    TruthinessResult(
        python_value_repr="{}",
        python_bool=False,
        js_bool=True,
        is_trap=True,
        explanation=(
            "Python: empty dict is falsy. "
            "JS: empty object {} is truthy — it is a live object reference."
        ),
    ),
    TruthinessResult(
        python_value_repr="0",
        python_bool=False,
        js_bool=False,
        is_trap=False,
        explanation="Both Python and JS treat the integer 0 as falsy.",
    ),
    TruthinessResult(
        python_value_repr='""',
        python_bool=False,
        js_bool=False,
        is_trap=False,
        explanation="Both Python and JS treat the empty string as falsy.",
    ),
    TruthinessResult(
        python_value_repr='"0"',
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation=(
            'Both Python and JS treat the non-empty string "0" as truthy. '
            'A common mistake is to assume "0" is falsy because the number 0 is.'
        ),
    ),
    TruthinessResult(
        python_value_repr="None",
        python_bool=False,
        js_bool=False,
        is_trap=False,
        explanation="Python None and JS null are both falsy.",
    ),
    TruthinessResult(
        python_value_repr="undefined (JS only)",
        python_bool=False,
        js_bool=False,
        is_trap=False,
        explanation=(
            "JS undefined is falsy. Python has no direct equivalent; "
            "None is the closest analogue (also falsy)."
        ),
    ),
    TruthinessResult(
        python_value_repr="float('nan')",
        python_bool=True,
        js_bool=False,
        is_trap=True,
        explanation=(
            "Python: NaN is a non-zero float, so bool(float('nan')) is True. "
            "JS: NaN is falsy — it is explicitly listed as a falsy value."
        ),
    ),
    TruthinessResult(
        python_value_repr="float('inf')",
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation="Both Python and JS treat Infinity as truthy (non-zero, non-NaN number).",
    ),
    TruthinessResult(
        python_value_repr="set()",
        python_bool=False,
        js_bool=True,
        is_trap=True,
        explanation=(
            "Python: empty set() is falsy. "
            "JS has no native set literal; a JS Set object (new Set()) is always truthy "
            "regardless of size because it is an object reference. N/A for direct mapping."
        ),
    ),
    TruthinessResult(
        python_value_repr="0.0",
        python_bool=False,
        js_bool=False,
        is_trap=False,
        explanation="Both Python and JS treat 0.0 as falsy (equivalent to numeric zero).",
    ),
    TruthinessResult(
        python_value_repr='b""',
        python_bool=False,
        js_bool=True,
        is_trap=True,
        explanation=(
            "Python: empty bytes b'' is falsy. "
            "JS has no bytes type; the nearest analogue is Uint8Array(0), "
            "which is an object and therefore truthy."
        ),
    ),
    TruthinessResult(
        python_value_repr="()",
        python_bool=False,
        js_bool=True,
        is_trap=True,
        explanation=(
            "Python: empty tuple () is falsy. "
            "JS has no tuple type; the nearest equivalent is a frozen array [], "
            "which is an object and always truthy. Conceptual trap when porting code."
        ),
    ),
    TruthinessResult(
        python_value_repr="[0]",
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation="Both Python and JS treat a non-empty array/list as truthy.",
    ),
    TruthinessResult(
        python_value_repr='{"a": 1}',
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation="Both Python and JS treat a non-empty dict/object as truthy.",
    ),
    TruthinessResult(
        python_value_repr="False",
        python_bool=False,
        js_bool=False,
        is_trap=False,
        explanation="Boolean False is falsy in both Python and JS.",
    ),
    TruthinessResult(
        python_value_repr="True",
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation="Boolean True is truthy in both Python and JS.",
    ),
    TruthinessResult(
        python_value_repr="1",
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation="Non-zero integer 1 is truthy in both Python and JS.",
    ),
    TruthinessResult(
        python_value_repr='"false"',
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation=(
            'The string "false" is a non-empty string and therefore truthy in both '
            "Python and JS. This surprises developers who expect it to behave like "
            "the boolean false."
        ),
    ),
    TruthinessResult(
        python_value_repr="object()",
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation=(
            "Any plain object instance is truthy in both Python (non-None object) "
            "and JS ({} object reference). They agree here."
        ),
    ),
    TruthinessResult(
        python_value_repr="-1",
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation="Any non-zero number, including negatives, is truthy in both Python and JS.",
    ),
    TruthinessResult(
        python_value_repr='" "',
        python_bool=True,
        js_bool=True,
        is_trap=False,
        explanation=(
            "A whitespace-only string is non-empty and truthy in both Python and JS. "
            "JS does not strip whitespace for truthiness evaluation."
        ),
    ),
]


@dataclass(frozen=True)
class AbstractEqualityRule:
    left_type: str
    right_type: str
    coercion_applied: str
    example: str
    result: str


ABSTRACT_EQUALITY_RULES: list[AbstractEqualityRule] = [
    AbstractEqualityRule(
        left_type="null",
        right_type="undefined",
        coercion_applied="none — special case: null and undefined are mutually equal",
        example="null == undefined  // true",
        result="True",
    ),
    AbstractEqualityRule(
        left_type="null",
        right_type="number",
        coercion_applied="none — null only equals null or undefined, nothing else",
        example="null == 0  // false",
        result="False",
    ),
    AbstractEqualityRule(
        left_type="string",
        right_type="number",
        coercion_applied="string is coerced to number via ToNumber()",
        example='"1" == 1  // true, ToNumber("1") === 1',
        result="True",
    ),
    AbstractEqualityRule(
        left_type="boolean",
        right_type="number",
        coercion_applied="boolean is coerced to number first: true→1, false→0",
        example="true == 1  // true, ToNumber(true) === 1",
        result="True",
    ),
    AbstractEqualityRule(
        left_type="boolean",
        right_type="number",
        coercion_applied="false coerced to 0 via ToNumber(false)",
        example="false == 0  // true",
        result="True",
    ),
    AbstractEqualityRule(
        left_type="string",
        right_type="boolean",
        coercion_applied=(
            'boolean→number (false→0), then string→number ("" → 0); both become 0'
        ),
        example='"" == false  // true, ToNumber("") === 0, ToNumber(false) === 0',
        result="True",
    ),
    AbstractEqualityRule(
        left_type="array",
        right_type="boolean",
        coercion_applied=(
            "boolean→number (false→0); array→primitive via ToPrimitive: "
            '[]→"" via toString(), then ""→0 via ToNumber()'
        ),
        example="[] == false  // true; [] → '' → 0, false → 0",
        result="True",
    ),
    AbstractEqualityRule(
        left_type="array",
        right_type="number",
        coercion_applied=(
            'array→primitive: []→"" via toString(), then ""→0 via ToNumber()'
        ),
        example="[] == 0  // true; [] → '' → 0",
        result="True",
    ),
    AbstractEqualityRule(
        left_type="array",
        right_type="number",
        coercion_applied=(
            '[1]→"1" via toString(), then "1"→1 via ToNumber()'
        ),
        example="[1] == 1  // true; [1] → '1' → 1",
        result="True",
    ),
    AbstractEqualityRule(
        left_type="null",
        right_type="boolean",
        coercion_applied=(
            "none applicable — null is not coerced; it only equals null/undefined"
        ),
        example="null == false  // false! null never equals false",
        result="False",
    ),
    AbstractEqualityRule(
        left_type="number (NaN)",
        right_type="number (NaN)",
        coercion_applied="none — NaN is never equal to anything, including itself",
        example="NaN == NaN  // false (always)",
        result="False",
    ),
    AbstractEqualityRule(
        left_type="string",
        right_type="boolean",
        coercion_applied=(
            'boolean→number (false→0), then "0"→0 via ToNumber(); both become 0'
        ),
        example='"0" == false  // true; "0"→0, false→0',
        result="True",
    ),
    AbstractEqualityRule(
        left_type="object",
        right_type="string",
        coercion_applied=(
            "object→primitive via ToPrimitive (toString/valueOf); "
            'e.g., new String("x") == "x"'
        ),
        example='new String("x") == "x"  // true',
        result="True",
    ),
    AbstractEqualityRule(
        left_type="undefined",
        right_type="number",
        coercion_applied="none — undefined only equals null/undefined",
        example="undefined == 0  // false",
        result="False",
    ),
]


class CoercionAnalyzer:
    """Utility for detecting JS == traps and recommending safe alternatives."""

    def is_trap_comparison(self, left: str, right: str) -> bool:
        """Return True if this JS == comparison is a known semantic trap.

        A comparison is a trap when == produces a non-obvious result due to
        implicit type coercion — i.e. the result would surprise a developer
        coming from Python.
        """
        query = frozenset({left, right})
        for rule in ABSTRACT_EQUALITY_RULES:
            # Extract operands from the example string: everything before the
            # first '//' comment, then split on '=='.
            code_part = rule.example.split("//")[0]
            tokens = [t.strip() for t in code_part.split("==") if t.strip()]
            if len(tokens) == 2 and frozenset(tokens) == query:
                return _is_surprising(rule.left_type, rule.right_type)
        # Fallback: if either operand appears in a known-trap rule, flag it.
        return False

    def safe_equality(self, left: str, right: str) -> str:
        """Return the safe strict-equality JS === form."""
        return f"{left} === {right}"

    def python_to_js_bool(self, python_repr: str) -> TruthinessResult | None:
        """Look up a TruthinessResult by its python_value_repr."""
        for entry in TRUTHINESS_TABLE:
            if entry.python_value_repr == python_repr:
                return entry
        return None


def _is_surprising(left_type: str, right_type: str) -> bool:
    """Heuristic: a comparison is a 'trap' when the types differ non-trivially."""
    same = {
        frozenset({"number", "number"}),
        frozenset({"boolean", "boolean"}),
        frozenset({"string", "string"}),
    }
    return frozenset({left_type.split()[0], right_type.split()[0]}) not in same


class NullishCoalescingGuide:
    """Guidance on when to prefer ?? (nullish coalescing) over || (logical OR)."""

    def nullish_vs_or(self, value_repr: str) -> str:
        """
        Explain when ?? vs || is correct for a given value.

        ?? only short-circuits on null/undefined.
        || short-circuits on any falsy value (0, "", false, NaN, null, undefined).
        """
        falsy_but_not_nullish = {
            "0",
            "0.0",
            "-0",
            '""',
            "''",
            "``",
            "false",
            "NaN",
        }
        nullish = {"null", "undefined"}

        if value_repr in nullish:
            return (
                f"'{value_repr}' is nullish, so BOTH ?? and || will fall through to "
                f"the right-hand side. They behave identically here. "
                f"Prefer ?? to signal intent (you only want to guard against "
                f"missing values, not all falsy values)."
            )
        elif value_repr in falsy_but_not_nullish:
            return (
                f"'{value_repr}' is falsy but NOT nullish. "
                f"Use ?? if you want to KEEP this value (it is a valid, intentional "
                f"falsy value such as 0 meaning 'zero items'). "
                f"Use || only if you want to replace it with a default — but beware: "
                f"|| treats 0, \"\", false, and NaN as 'missing', which is often wrong."
            )
        else:
            return (
                f"'{value_repr}' is truthy, so neither ?? nor || will fall through — "
                f"the left-hand side is returned as-is in both cases."
            )

    @classmethod
    def examples(cls) -> list[str]:
        """Five examples showing when ?? is safer than ||."""
        return [
            # 1 — port number defaulting
            (
                "const port = config.port ?? 3000;  // SAFE: uses 3000 only if "
                "config.port is null/undefined. "
                "If config.port is 0, it is preserved. "
                "|| would wrongly replace 0 with 3000."
            ),
            # 2 — user-supplied count
            (
                "const retries = options.retries ?? 1;  // SAFE: 0 retries is a "
                "deliberate choice. "
                "options.retries || 1 would silently upgrade 0 → 1."
            ),
            # 3 — empty-string label
            (
                'const label = props.label ?? "Untitled";  // SAFE: an empty string '
                'label "" is intentional (no label). '
                'props.label || "Untitled" would replace "" with "Untitled".'
            ),
            # 4 — boolean flag
            (
                "const enabled = settings.enabled ?? true;  // SAFE: false means "
                "explicitly disabled. "
                "settings.enabled || true would always return true, ignoring false."
            ),
            # 5 — nested optional property
            (
                "const timeout = response?.timeout ?? 5000;  // SAFE: combines "
                "optional chaining with nullish coalescing. "
                "If timeout is 0 (immediate), it is kept. "
                "|| 5000 would replace a deliberate 0 with the fallback."
            ),
        ]
