"""
Python→JavaScript Functor Between Scope Sites
==============================================

This module models the translation from Python to JavaScript as a functor
between scope sites in the sense of jugeo's sheaf-theoretic geometry.

FUNCTOR FIDELITY STATEMENT
--------------------------
This functor is **NOT faithful**: the translation from Python to JavaScript
loses information in several well-characterised ways.

Preserved (natural transformations that commute):
  - Value identity for primitives within safe ranges
  - Control-flow structure (if/for/while/try)
  - Module-level composition (import ≈ import/require)
  - Basic OOP shapes (class with methods and inheritance)
  - String immutability
  - Boolean truthiness for primitive scalars (0, "", False/false)
  - async/await suspension structure (though cancellation semantics differ)

Lost (the functor is not faithful here):
  - Integer precision beyond 2^53 (Number vs arbitrary-precision int)
  - Immutability of tuples (no frozen arrays in JS)
  - Complex number arithmetic as a first-class type
  - Python keyword arguments (**kwargs) and their introspection
  - Python's MRO (C3 linearisation) vs JS prototype chain
  - Truthiness of empty containers: `[]` and `{}` are falsy in Python,
    truthy in JavaScript — this is one of the most dangerous divergences
  - frozenset immutability
  - Generator send() / throw() protocol differences
  - Python's native decimal/fraction types
  - Descriptor protocol (__get__/__set__/__delete__)
  - Metaclass system
  - Exception chaining (raise X from Y) / __cause__ / __context__
  - Type annotations at runtime (typing module)
  - sys.path / importlib introspection
"""

from __future__ import annotations

__all__ = [
    "PyTypeKind",
    "JSTypeKind",
    "TypeMapping",
    "TruthinessMapper",
    "ScopeTranslation",
    "TranslationFidelityChecker",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind
from jugeo.geometry.descent import LocalSection, DescentResult, DescentObstruction, GlobalSection


# ---------------------------------------------------------------------------
# 1. Type enumerations
# ---------------------------------------------------------------------------

class PyTypeKind(str, Enum):
    """Enumeration of Python type categories relevant to cross-language translation."""
    INT = "int"
    FLOAT = "float"
    COMPLEX = "complex"
    STR = "str"
    BYTES = "bytes"
    BOOL = "bool"
    NONE = "NoneType"
    LIST = "list"
    TUPLE = "tuple"
    DICT = "dict"
    SET = "set"
    FROZENSET = "frozenset"
    FUNCTION = "function"
    CLASS = "class"
    MODULE = "module"
    GENERATOR = "generator"
    COROUTINE = "coroutine"
    CALLABLE = "callable"


class JSTypeKind(str, Enum):
    """Enumeration of JavaScript type categories relevant to cross-language translation."""
    NUMBER = "number"
    BIGINT = "bigint"
    STRING = "string"
    BOOLEAN = "boolean"
    NULL = "null"
    UNDEFINED = "undefined"
    ARRAY = "Array"
    OBJECT = "Object"
    FUNCTION_JS = "function"         # JS function object
    CLASS_JS = "class"               # JS class (syntactic sugar over prototype)
    SYMBOL = "Symbol"
    PROMISE = "Promise"
    GENERATOR_JS = "GeneratorObject"
    PROXY = "Proxy"
    MAP = "Map"
    SET_JS = "Set"


# ---------------------------------------------------------------------------
# 2. TypeMapping — functor arrow between type objects
# ---------------------------------------------------------------------------

@dataclass
class TypeMapping:
    """
    Records how a single Python type maps to a JavaScript type, together
    with a fidelity annotation.

    A mapping is *lossless* if every Python value of ``py_type`` can be
    round-tripped through JavaScript without losing any semantically
    significant information.  Even lossless mappings may have API
    differences (e.g. ``bytes`` → ``Uint8Array``).
    """
    py_type: PyTypeKind
    js_type: JSTypeKind
    is_lossless: bool
    loss_description: str = ""
    notes: str = ""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def standard_mappings(cls) -> list[TypeMapping]:
        """
        Return the canonical set of Python→JS type mappings for this functor.

        Each mapping is annotated with whether it is lossless and, if not,
        a description of exactly what information is discarded.

        Returns
        -------
        list[TypeMapping]
            Approximately 20 mappings covering the core Python type system.
        """
        return [
            # ── Numeric types ──────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.INT,
                js_type=JSTypeKind.NUMBER,
                is_lossless=False,
                loss_description=(
                    "JavaScript Number is an IEEE-754 double, so integers "
                    "with |n| >= 2^53 lose precision.  Values in that range "
                    "should map to BigInt instead, but BigInt and Number are "
                    "incompatible types in JS (no implicit coercion)."
                ),
                notes=(
                    "For |n| < 2^53 the mapping is effectively lossless. "
                    "Python int is arbitrary-precision; no JS equivalent exists "
                    "without a library.  Use BigInt for large integers."
                ),
            ),
            cls(
                py_type=PyTypeKind.INT,
                js_type=JSTypeKind.BIGINT,
                is_lossless=True,
                loss_description="",
                notes=(
                    "BigInt preserves arbitrary-precision integer semantics, "
                    "but BigInt cannot be mixed with Number arithmetic without "
                    "explicit conversion.  Choose based on expected value range."
                ),
            ),
            cls(
                py_type=PyTypeKind.FLOAT,
                js_type=JSTypeKind.NUMBER,
                is_lossless=True,
                loss_description="",
                notes=(
                    "Both Python float and JS Number use IEEE-754 double. "
                    "NaN != NaN holds in both languages (same behaviour). "
                    "Infinity and -Infinity are also present in both. "
                    "Edge case: Python has math.nan; JS has Number.NaN / NaN. "
                    "Representation is identical; serialisation (JSON) loses NaN/Inf."
                ),
            ),
            cls(
                py_type=PyTypeKind.COMPLEX,
                js_type=JSTypeKind.OBJECT,
                is_lossless=False,
                loss_description=(
                    "JavaScript has no native complex number type. "
                    "Python complex(a, b) must be encoded as a plain object "
                    "{re: a, im: b}.  Arithmetic operators (+, *, /) do not "
                    "work on such objects without a helper library.  The "
                    ".real and .imag attributes become plain property accesses "
                    "but the mathematical identity (complex arithmetic closure) "
                    "is lost."
                ),
                notes="Consider using a JS complex-number library for numeric code.",
            ),
            # ── String / bytes ─────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.STR,
                js_type=JSTypeKind.STRING,
                is_lossless=True,
                loss_description="",
                notes=(
                    "Both Python str and JS string are Unicode sequences. "
                    "Python str is UCS-4 internally (full Unicode scalar values). "
                    "JS strings are UTF-16 sequences; surrogate pairs represent "
                    "code points above U+FFFF.  For most practical text this is "
                    "transparent, but length and indexing semantics differ for "
                    "emoji and supplementary-plane characters."
                ),
            ),
            cls(
                py_type=PyTypeKind.BYTES,
                js_type=JSTypeKind.OBJECT,
                is_lossless=True,
                loss_description="",
                notes=(
                    "Python bytes maps to Uint8Array (a TypedArray). "
                    "The binary content is fully preserved, but the API differs: "
                    "Python bytes is immutable and supports slicing with copies; "
                    "Uint8Array is mutable and shares a backing ArrayBuffer. "
                    "Uint8Array is categorised here as OBJECT since JS has no "
                    "dedicated TypedArray enum member — it is a specialised Object."
                ),
            ),
            # ── Boolean / None ─────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.BOOL,
                js_type=JSTypeKind.BOOLEAN,
                is_lossless=False,
                loss_description=(
                    "Python bool is a subclass of int (True == 1, False == 0). "
                    "Arithmetic on booleans is valid Python (True + True == 2). "
                    "JS boolean is not a subtype of number; true + true === 2 "
                    "only due to implicit coercion, not type hierarchy. "
                    "The subtype relationship is lost."
                ),
                notes=(
                    "For pure boolean logic the mapping is lossless. "
                    "The loss only manifests when boolean-as-integer arithmetic "
                    "is involved."
                ),
            ),
            cls(
                py_type=PyTypeKind.NONE,
                js_type=JSTypeKind.NULL,
                is_lossless=False,
                loss_description=(
                    "Python has a single bottom value: None. "
                    "JavaScript has two: null (intentional absence) and undefined "
                    "(uninitialised / missing property).  The functor maps None → null "
                    "but the recipient JS code may encounter undefined in contexts "
                    "where Python would raise AttributeError or return None. "
                    "The distinction between null and undefined is lost in the "
                    "reverse direction."
                ),
                notes=(
                    "undefined has no Python analogue. "
                    "Optional chaining (?.) in JS returns undefined, not null."
                ),
            ),
            # ── Sequence types ─────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.LIST,
                js_type=JSTypeKind.ARRAY,
                is_lossless=True,
                loss_description="",
                notes=(
                    "Python list and JS Array are both mutable, ordered, "
                    "dynamically-sized sequences.  Python lists are homogeneous "
                    "by convention (and typing) but not enforced; JS arrays are "
                    "heterogeneous by design.  The structural mapping is lossless; "
                    "typing/convention information is lost."
                ),
            ),
            cls(
                py_type=PyTypeKind.TUPLE,
                js_type=JSTypeKind.ARRAY,
                is_lossless=False,
                loss_description=(
                    "Python tuple is an immutable, fixed-length sequence. "
                    "JavaScript has no frozen array primitive.  The values are "
                    "preserved but the immutability constraint is dropped. "
                    "Object.freeze([...]) exists but is shallow and rarely used. "
                    "Named tuple fields (collections.namedtuple) are completely lost."
                ),
                notes=(
                    "For structural pattern matching (match statement with tuple "
                    "patterns), the fixed-arity information is also lost in JS."
                ),
            ),
            # ── Mapping types ───────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.DICT,
                js_type=JSTypeKind.OBJECT,
                is_lossless=False,
                loss_description=(
                    "Python dict (3.7+) preserves insertion order and supports "
                    "any hashable type as key. JS plain Object coerces all keys "
                    "to strings (or Symbols), losing non-string keys. "
                    "Integer keys are reordered by JS engines (ascending numeric "
                    "order before string keys), breaking insertion-order guarantee. "
                    "Use JS Map for a faithful translation."
                ),
                notes=(
                    "JS Map preserves insertion order and allows non-string keys, "
                    "making it a more faithful target than Object. "
                    "However, Map is not JSON-serialisable without custom logic."
                ),
            ),
            cls(
                py_type=PyTypeKind.DICT,
                js_type=JSTypeKind.MAP,
                is_lossless=True,
                loss_description="",
                notes=(
                    "Map preserves insertion order (ES2015+) and allows arbitrary "
                    "keys.  This is the most faithful JS equivalent for Python dict "
                    "when key types matter.  API is different: .get()/.set()/.has() "
                    "instead of [] subscript syntax."
                ),
            ),
            # ── Set types ───────────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.SET,
                js_type=JSTypeKind.SET_JS,
                is_lossless=False,
                loss_description=(
                    "Python set uses hash-based equality (__hash__ + __eq__). "
                    "JS Set uses SameValueZero equality (like ===, except NaN === NaN). "
                    "Custom __hash__ / __eq__ semantics are lost: JS Set cannot "
                    "deduplicate objects by value, only by reference identity."
                ),
                notes=(
                    "For sets of primitives (numbers, strings) the mapping is "
                    "effectively lossless.  For sets of objects the equality "
                    "semantics diverge."
                ),
            ),
            cls(
                py_type=PyTypeKind.FROZENSET,
                js_type=JSTypeKind.SET_JS,
                is_lossless=False,
                loss_description=(
                    "Python frozenset is immutable and hashable (can be used as "
                    "a dict key or placed inside another set). "
                    "JS Set is mutable; there is no frozen-set primitive. "
                    "The immutability and hashability of frozenset are both lost."
                ),
                notes=(
                    "Object.freeze(new Set([...])) prevents mutation but does not "
                    "make the Set hashable or usable as a Map key."
                ),
            ),
            # ── Callable types ──────────────────────────────────────────────
            cls(
                py_type=PyTypeKind.FUNCTION,
                js_type=JSTypeKind.FUNCTION_JS,
                is_lossless=False,
                loss_description=(
                    "Python functions support: positional-only parameters (/), "
                    "keyword-only parameters (*), *args, **kwargs, default values "
                    "evaluated at definition time, and full introspection via "
                    "inspect.signature().  JS functions have: positional parameters, "
                    "rest parameters (...args), default values, and the arguments "
                    "object (non-strict mode).  **kwargs has no direct equivalent: "
                    "the nearest idiom is an options object { key: value }.  "
                    "Introspection (parameter names, annotations) is lost."
                ),
                notes=(
                    "Arrow functions in JS do not have their own 'this', 'arguments', "
                    "or 'super'.  Python has no equivalent of JS 'this' context."
                ),
            ),
            cls(
                py_type=PyTypeKind.CLASS,
                js_type=JSTypeKind.CLASS_JS,
                is_lossless=False,
                loss_description=(
                    "Python uses C3 linearisation (MRO) for multiple inheritance; "
                    "JS prototype chain is singly-linked (no multiple inheritance "
                    "at the language level, only mixins by hand).  Python descriptors "
                    "(__get__/__set__/__delete__) have no direct JS equivalent. "
                    "Python metaclasses are entirely lost.  __slots__ optimisation "
                    "has no equivalent.  __init_subclass__ and __class_getitem__ "
                    "hooks are absent in JS."
                ),
                notes=(
                    "JS class syntax is syntactic sugar over prototype-based "
                    "delegation.  Private fields (#field) exist in modern JS "
                    "but differ from Python name-mangled __dunder attributes."
                ),
            ),
            cls(
                py_type=PyTypeKind.MODULE,
                js_type=JSTypeKind.OBJECT,
                is_lossless=False,
                loss_description=(
                    "Python modules are first-class objects supporting attribute "
                    "access, reloading (importlib.reload), and lazy submodule "
                    "loading.  JS ES modules are static (analysable at parse time): "
                    "named exports are live bindings, not object properties. "
                    "Dynamic import() exists but differs from importlib semantics. "
                    "Module __all__, __file__, __spec__ attributes are lost."
                ),
                notes=(
                    "CommonJS (require) is closer to Python's dynamic import model "
                    "but is deprecated in favour of ES modules."
                ),
            ),
            # ── Async / iterator types ──────────────────────────────────────
            cls(
                py_type=PyTypeKind.GENERATOR,
                js_type=JSTypeKind.GENERATOR_JS,
                is_lossless=False,
                loss_description=(
                    "Python generators support send(value) to inject a value and "
                    "throw(exc) to inject an exception at the yield point. "
                    "JS generators have the same .next(value) and .throw(error) "
                    "protocol, so the core semantics are preserved. "
                    "Loss: Python generator.close() triggers GeneratorExit which "
                    "can be caught; JS generator.return(value) forces return but "
                    "finally blocks still run.  Python generator expressions "
                    "are lazy; JS has no generator-expression syntax (use function*)."
                ),
                notes="yield from (Python) ≈ yield* (JS) — both delegate.",
            ),
            cls(
                py_type=PyTypeKind.COROUTINE,
                js_type=JSTypeKind.PROMISE,
                is_lossless=False,
                loss_description=(
                    "Python coroutines run on an explicit event loop "
                    "(asyncio.get_event_loop() / asyncio.run()).  JS Promises resolve "
                    "on the implicit microtask queue of the JS engine.  "
                    "Python Task.cancel() with CancelledError has no JS Promise "
                    "equivalent (AbortController is the closest idiom but requires "
                    "manual wiring).  Python allows awaiting the same coroutine "
                    "object only once; JS Promise can be .then()-chained many times. "
                    "asyncio.gather vs Promise.all have similar semantics but "
                    "error handling differs (gather can return_exceptions=True)."
                ),
                notes=(
                    "async def f() in Python returns a coroutine object; "
                    "async function f() in JS returns a Promise.  The caller-side "
                    "interface is similar but the runtime model differs fundamentally."
                ),
            ),
            cls(
                py_type=PyTypeKind.CALLABLE,
                js_type=JSTypeKind.FUNCTION_JS,
                is_lossless=False,
                loss_description=(
                    "Python callables include functions, lambdas, classes (via "
                    "__init__), and any object with __call__.  JS's callable "
                    "category is exactly functions (and classes as constructors). "
                    "__call__ objects (callable instances) must be converted to "
                    "wrapper functions, losing the original object identity and "
                    "any non-__call__ attributes."
                ),
                notes=(
                    "functools.partial ≈ Function.prototype.bind, but bind only "
                    "binds positional args left-to-right; partial supports "
                    "arbitrary positional and keyword partial application."
                ),
            ),
        ]


# ---------------------------------------------------------------------------
# 3. TruthinessMapper — the most dangerous divergence between the two sites
# ---------------------------------------------------------------------------

class TruthinessMapper:
    """
    Documents the truthiness (boolean coercion) rules for Python and
    JavaScript and explicitly lists the critical divergences that are
    the most common source of bugs when translating code between the two
    languages.

    This is **not** a runtime mapper — it documents the static rules.
    """

    # Rules that make a Python expression falsy
    PYTHON_FALSY_RULES: list[str] = [
        "False                     # the boolean literal",
        "0                         # integer zero",
        "0.0                       # float zero",
        "0j                        # complex zero",
        "''                        # empty string",
        "b''                       # empty bytes",
        "[]                        # empty list",
        "()                        # empty tuple",
        "{}                        # empty dict",
        "set()                     # empty set",
        "frozenset()               # empty frozenset",
        "None                      # the None singleton",
        "obj with __bool__->False  # custom bool protocol",
        "obj with __len__->0       # zero-length custom container",
    ]

    # Rules that make a JavaScript expression falsy
    JS_FALSY_RULES: list[str] = [
        "false                     // the boolean literal",
        "0                         // number zero",
        "-0                        // negative zero (IEEE-754)",
        "0n                        // BigInt zero",
        "''                        // empty string (also \"\" and ``)",
        "null                      // the null primitive",
        "undefined                 // the undefined primitive",
        "NaN                       // Not-a-Number",
    ]

    # Divergences that cause silent bugs when porting Python→JS
    CRITICAL_DIFFERENCES: list[str] = [
        (
            "EMPTY ARRAY: `[]` is FALSY in Python (empty list), "
            "but TRUTHY in JavaScript — `if ([]) { ... }` executes! "
            "This is the most common Python→JS porting bug."
        ),
        (
            "EMPTY OBJECT: `{}` is FALSY in Python (empty dict), "
            "but TRUTHY in JavaScript — `if ({}) { ... }` executes! "
            "Plain objects are always truthy regardless of content."
        ),
        (
            "STRING '0': `'0'` is TRUTHY in Python (non-empty string). "
            "`'0'` is also TRUTHY in JavaScript (non-empty string). "
            "Agreement here, but note: Number('0') === 0 is falsy in JS "
            "if coerced to number, e.g. in `if (+'0')` — careful with coercion."
        ),
        (
            "NONE vs NULL vs UNDEFINED: Python's `None` maps to JS `null`. "
            "But `undefined` (missing property, uninitialised variable) has "
            "no Python equivalent.  Both null and undefined are falsy in JS; "
            "only None is falsy in Python.  Strict equality: null !== undefined."
        ),
        (
            "NaN TRUTHINESS: `float('nan')` is TRUTHY in Python (non-zero float). "
            "`NaN` is FALSY in JavaScript.  Code that checks `if value:` in Python "
            "and expects NaN to pass will break when ported to JS."
        ),
        (
            "ZERO FLOAT: `0.0` is FALSY in Python. `0` (number) is FALSY in JS. "
            "These agree, but `-0` (negative zero) is FALSY in JS and has no "
            "Python counterpart (Python -0.0 == 0.0 and bool(-0.0) is False)."
        ),
        (
            "CUSTOM __bool__: Python objects can define __bool__ or __len__ to "
            "control truthiness.  JS has no equivalent hook — truthiness is always "
            "determined by the engine's built-in rules, not object methods."
        ),
    ]

    @staticmethod
    def py_is_truthy(value_description: str) -> bool | None:
        """
        Return the Python truthiness of a described value where statically
        determinable.

        Parameters
        ----------
        value_description:
            A string description of a Python value, e.g. ``"[]"``,
            ``"None"``, ``"42"``, ``"'hello'"``.

        Returns
        -------
        bool
            ``True`` or ``False`` when the truthiness can be determined
            from static rules alone.
        None
            When truthiness depends on a custom ``__bool__`` or ``__len__``
            implementation (e.g. an arbitrary object).
        """
        # Canonical falsy values — check exact descriptions
        _falsy_literals: set[str] = {
            "False", "0", "0.0", "0j", "''", '""', "b''", 'b""',
            "[]", "()", "{}", "set()", "frozenset()", "None",
        }
        stripped = value_description.strip()
        if stripped in _falsy_literals:
            return False
        # Non-empty strings, positive numbers, non-empty containers are truthy
        if stripped.startswith(('"', "'")) and len(stripped) > 2:
            return True
        if stripped.lstrip("-").replace(".", "", 1).replace("j", "", 1).isdigit():
            try:
                return complex(stripped) != 0
            except ValueError:
                pass
        if stripped.startswith(("[", "(", "{")) and len(stripped) > 2:
            return True
        if stripped == "True":
            return True
        # Cannot determine — depends on __bool__ / __len__
        return None


# ---------------------------------------------------------------------------
# 4. ScopeTranslation — translating syntactic constructs across the functor
# ---------------------------------------------------------------------------

class ScopeTranslation:
    """
    Provides translations for Python syntactic constructs into their
    nearest JavaScript equivalents, together with notes on fidelity.

    All methods are static and return documentation strings, not generated
    code — this is a *descriptor* of the translation, not a code generator.
    """

    @staticmethod
    def translate_decorator(py_decorator: str) -> str:
        """
        Return a description of the JavaScript equivalent of a Python decorator.

        Parameters
        ----------
        py_decorator:
            The decorator as a string, e.g. ``"@property"``,
            ``"@staticmethod"``, ``"@functools.lru_cache"``.

        Returns
        -------
        str
            Human-readable description of the JS equivalent and any
            fidelity losses.
        """
        _table: dict[str, str] = {
            "@property": (
                "JS getter accessor: `get propName() { return this._x; }`. "
                "The setter is a separate `set propName(v) { ... }`. "
                "Loss: Python @property.setter / @property.deleter are unified "
                "with the property object; JS get/set are separate syntax forms."
            ),
            "@staticmethod": (
                "JS `static` method keyword inside class body: "
                "`static methodName(...) { ... }`. Lossless for simple cases. "
                "Loss: Python staticmethod objects are introspectable "
                "(inspect.isfunction(MyClass.__dict__['m']) works); "
                "JS static methods are just properties on the constructor."
            ),
            "@classmethod": (
                "JS `static` method — but loss is significant: Python classmethods "
                "receive the actual class (or subclass) as first argument `cls`, "
                "enabling cooperative inheritance patterns.  In JS `static` methods "
                "use `this` which refers to the constructor, achieving similar "
                "behaviour, but only when called on the class, not an instance."
            ),
            "@abstractmethod": (
                "No native JS equivalent.  Convention: throw new Error('Not implemented') "
                "in the base class method body.  TypeScript `abstract` keyword is the "
                "closest faithful translation but requires TypeScript, not plain JS."
            ),
            "@functools.wraps": (
                "No direct equivalent in JS.  The nearest idiom is copying "
                "`wrapper.name = fn.name` and `wrapper.length = fn.length`. "
                "JS functions have .name and .length but no __wrapped__, "
                "__doc__, or __annotations__ attributes."
            ),
            "@functools.lru_cache": (
                "No stdlib equivalent.  Must be implemented manually or via a "
                "library.  Idiomatic pattern: `const cache = new Map(); "
                "function memoised(x) { if (cache.has(x)) return cache.get(x); "
                "const r = fn(x); cache.set(x, r); return r; }`.  "
                "Loss: Python lru_cache supports maxsize eviction (LRU policy) "
                "and cache_info(); manual Map has neither."
            ),
            "@dataclass": (
                "No direct equivalent in plain JS.  In modern JS, use a class with "
                "a constructor that assigns fields.  Loss: automatic __repr__, "
                "__eq__ by value, __hash__, __lt__ (with order=True), frozen=True, "
                "slots=True — all must be implemented manually."
            ),
            "@contextmanager": (
                "No JS equivalent for the with-statement protocol. "
                "The nearest pattern is try/finally or a resource-management "
                "helper function.  Symbol.asyncDispose (TC39 proposal) and "
                "`await using` exist in Stage 3 but are not universal."
            ),
            "@overload": (
                "TypeScript @overload decorator achieves similar documentation-level "
                "overloading.  Plain JS has no overloads; dispatch on argument types "
                "must be done manually inside the function body."
            ),
        }
        key = py_decorator.strip()
        return _table.get(key, (
            f"No standard mapping known for {py_decorator!r}. "
            "Consider: for class decorators that transform the class, "
            "JS class decorators (TC39 Stage 3) offer similar capabilities "
            "but with different timing and semantics."
        ))

    @staticmethod
    def translate_comprehension(kind: str) -> str:
        """
        Return the JavaScript equivalent of a Python comprehension.

        Parameters
        ----------
        kind:
            One of ``"list"``, ``"dict"``, ``"set"``, ``"generator"``.

        Returns
        -------
        str
            Description of the JS idiom and fidelity notes.
        """
        _table: dict[str, str] = {
            "list": (
                "[x for x in iterable if cond]  →  "
                "iterable.filter(cond).map(x => expr)  "
                "(or combined with a single .flatMap if projecting 1→0/n). "
                "Loss: Python list comprehensions support multiple for-clauses "
                "(nested iteration) in a single expression; JS requires chaining "
                ".flatMap() or nested loops.  Type is preserved (both return arrays)."
            ),
            "dict": (
                "{k: v for k, v in pairs}  →  "
                "Object.fromEntries(pairs.map(([k, v]) => [expr_k, expr_v]))  "
                "or new Map(pairs.map(...)). "
                "Loss: Object.fromEntries coerces keys to strings; use Map for "
                "non-string keys.  No filter shorthand — must .filter() before .map()."
            ),
            "set": (
                "{expr for x in iterable}  →  "
                "new Set(iterable.map(x => expr))  "
                "(filter: new Set([...iterable].filter(cond).map(x => expr))). "
                "Loss: JS Set equality is SameValueZero (reference equality for "
                "objects); Python set uses __hash__/__eq__ (value equality possible)."
            ),
            "generator": (
                "(expr for x in iterable)  →  "
                "No direct generator-expression syntax in JS. "
                "Closest: a generator function `function*(iterable) { for (const x of "
                "iterable) yield expr; }` invoked immediately, or Array.from(). "
                "Loss: Python generator expressions are lazy (O(1) memory); "
                "Array.from() is eager.  For laziness, explicit function* is required."
            ),
        }
        return _table.get(kind.lower(), (
            f"Unknown comprehension kind {kind!r}. "
            "Known kinds: list, dict, set, generator."
        ))

    @staticmethod
    def translate_exception(py_exception: str) -> str:
        """
        Return the nearest JavaScript error class for a Python exception name.

        Parameters
        ----------
        py_exception:
            Python exception class name, e.g. ``"ValueError"``,
            ``"KeyError"``, ``"AttributeError"``.

        Returns
        -------
        str
            JS error class name and notes on semantic differences.
        """
        _table: dict[str, str] = {
            "ValueError":      "TypeError (JS) or a custom ValueError subclass. "
                               "JS TypeError covers many 'wrong value' cases.",
            "TypeError":       "TypeError (JS) — good semantic match for wrong type.",
            "KeyError":        "No JS stdlib equivalent; plain Error or custom KeyError. "
                               "Map.get() returns undefined instead of throwing.",
            "AttributeError":  "TypeError (JS) — property access on null/undefined "
                               "throws TypeError; missing properties return undefined.",
            "IndexError":      "RangeError (JS) — for out-of-bounds numeric indices.",
            "OverflowError":   "RangeError (JS) — closest semantic match.",
            "ZeroDivisionError":"No JS equivalent — JS returns Infinity or NaN on "
                               "division by zero, never throws.",
            "StopIteration":   "Return { done: true } from iterator .next() — no "
                               "exception thrown in JS iteration protocol.",
            "RuntimeError":    "Error (JS) — base error class; no direct equivalent.",
            "NotImplementedError": "No JS stdlib equivalent. Custom class recommended: "
                               "class NotImplementedError extends Error {}",
            "AssertionError":  "No dedicated JS class; assert() in some environments, "
                               "or throw new Error('Assertion failed: ...').",
            "ImportError":     "No JS equivalent — ES module errors are SyntaxError "
                               "or dynamic import() rejection.",
            "NameError":       "ReferenceError (JS) — accessing undeclared variable.",
            "RecursionError":  "RangeError: Maximum call stack size exceeded (V8/SpiderMonkey).",
            "MemoryError":     "No JS equivalent — JS engines manage memory internally.",
            "OSError":         "No direct JS equivalent; Node.js uses SystemError "
                               "(err.code e.g. 'ENOENT', 'EPERM').",
            "TimeoutError":    "No JS stdlib class; AbortError (from AbortController) "
                               "or a custom TimeoutError extends Error.",
            "PermissionError": "No JS equivalent in browser; Node.js SystemError "
                               "with code 'EACCES'.",
            "FileNotFoundError": "Node.js SystemError with code 'ENOENT'.",
            "ConnectionError": "No JS stdlib class; custom NetworkError or fetch "
                               "rejection with type 'network'.",
            "UnicodeDecodeError": "No JS equivalent — JS strings are always UTF-16; "
                               "TextDecoder errors can be checked via fatal mode.",
        }
        return _table.get(py_exception, (
            f"No standard mapping for {py_exception!r}. "
            "Consider subclassing Error: class {py_exception} extends Error {{}}"
        ))

    @staticmethod
    def await_semantics_difference() -> list[str]:
        """
        Return a list of key semantic differences between Python and JavaScript
        async/await that are lost (or changed) by this functor.

        Returns
        -------
        list[str]
            At least three documented divergences.
        """
        return [
            (
                "CANCELLATION: Python asyncio.Task has .cancel() which injects "
                "CancelledError at the next await point; the coroutine can catch it "
                "and clean up.  JavaScript Promises have no built-in cancellation "
                "mechanism.  AbortController/AbortSignal is the idiomatic JS pattern "
                "but requires explicit wiring at every async call site — it is not "
                "propagated automatically through the await chain."
            ),
            (
                "FUTURE vs PROMISE: Python asyncio.Future is a write-once container "
                "that can be resolved or rejected from outside the coroutine "
                "(e.g. loop.call_soon(fut.set_result, value)).  JS Promise is "
                "resolved via the executor callback passed to `new Promise(resolve, reject)`. "
                "Python's Future.add_done_callback mirrors Promise.then; but "
                "Future supports .remove_done_callback() while Promise callbacks "
                "cannot be unregistered."
            ),
            (
                "EVENT LOOP: Python requires an explicit event loop — asyncio.run() "
                "creates and runs one; multiple loops can exist in a process "
                "(e.g. in different threads).  JS has a single implicit event loop "
                "per JavaScript realm (thread); there is no API to create or select "
                "a loop.  This means Python code that manipulates the loop directly "
                "(loop.run_until_complete, loop.stop, loop.create_task with a specific "
                "loop) has no JS equivalent."
            ),
            (
                "AWAITABILITY: In Python, only objects with __await__ are awaitable; "
                "await on a non-awaitable raises TypeError immediately.  In JS, "
                "`await nonPromise` is legal and simply wraps the value in "
                "Promise.resolve(nonPromise), returning it on the next microtask tick. "
                "This means `await 42` is valid JS but `await 42` is a TypeError in Python."
            ),
            (
                "EXCEPTION PROPAGATION: Python async for / async with have well-defined "
                "exception propagation through __aiter__ / __aenter__ / __aexit__. "
                "JS for-await-of uses the async iterable protocol but has no equivalent "
                "of __aexit__ for resource cleanup — try/finally must be used explicitly."
            ),
        ]


# ---------------------------------------------------------------------------
# 5. TranslationFidelityChecker — the functor's applicative check operator
# ---------------------------------------------------------------------------

class TranslationFidelityChecker:
    """
    Applies the Python→JavaScript functor to a specific type or value and
    returns a ``DescentResult`` encoding whether the translation is globally
    consistent (a section exists) or whether there is an obstruction.

    An *obstruction* arises whenever:
    - The Python type has no lossless JS equivalent, OR
    - The specific value has known semantics that are not preserved.

    The checker uses the ``DescentObstruction`` / ``DescentResult`` machinery
    from jugeo's descent theory to record these failures in a structured way.
    """

    def __init__(self) -> None:
        self._mappings: dict[PyTypeKind, list[TypeMapping]] = {}
        for m in TypeMapping.standard_mappings():
            self._mappings.setdefault(m.py_type, []).append(m)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_type_mapping(self, py_type: PyTypeKind) -> TypeMapping | None:
        """
        Return the *primary* (most commonly used) mapping for ``py_type``,
        or ``None`` if no mapping is defined.

        When multiple mappings exist (e.g. int → Number and int → BigInt),
        the first registered mapping is returned.  To retrieve all options,
        access ``self._mappings[py_type]``.

        Parameters
        ----------
        py_type:
            The Python type category to look up.

        Returns
        -------
        TypeMapping or None
        """
        options = self._mappings.get(py_type)
        if not options:
            return None
        return options[0]

    def check_value_translation(
        self,
        py_value_repr: str,
        py_type: PyTypeKind,
    ) -> DescentResult:
        """
        Assess whether a specific Python value has a faithful JS translation.

        Returns a ``DescentResult`` that is:
        - **success** if the value translates losslessly (with an empty
          section as witness), or
        - **failure** if there is a known obstruction (an information-loss
          site) specific to this value.

        The obstruction ``coordinate`` is the dotted path
        ``"python_js_functor.<py_type>.<py_value_repr>"``.

        Parameters
        ----------
        py_value_repr:
            Python repr-style description of the value, e.g. ``"[]"``,
            ``"None"``, ``"(1, 2, 3)"``, ``"float('nan')"``.
        py_type:
            The declared Python type category of the value.

        Returns
        -------
        DescentResult
        """
        coordinate = f"python_js_functor.{py_type.value}.{py_value_repr}"
        mapping = self.check_type_mapping(py_type)

        # No mapping at all → total obstruction
        if mapping is None:
            obs = DescentObstruction(
                coordinate=coordinate,
                partial_section={
                    "py_type": py_type.value,
                    "py_value_repr": py_value_repr,
                    "reason": f"No TypeMapping registered for PyTypeKind.{py_type.name}",
                },
            )
            return DescentResult.failure(obs)

        # Mapping is lossless for the type → check value-level obstructions
        if mapping.is_lossless:
            # Even in "lossless" mappings there are value-specific issues
            obstruction = self._check_value_level_obstruction(
                py_value_repr, py_type, coordinate
            )
            if obstruction is not None:
                return DescentResult.failure(obstruction)
            # Clean success — build a minimal GlobalSection as the witness
            global_section = GlobalSection(
                coordinate=coordinate,
                merged_judgment={
                    "py_type": py_type.value,
                    "js_type": mapping.js_type.value,
                    "is_lossless": True,
                    "value": py_value_repr,
                },
                trust_floor=1.0,
            )
            return DescentResult.success(global_section)

        # Mapping is lossy → record the obstruction with full context
        obs = DescentObstruction(
            coordinate=coordinate,
            partial_section={
                "py_type": py_type.value,
                "js_type": mapping.js_type.value,
                "py_value_repr": py_value_repr,
                "loss_description": mapping.loss_description,
                "notes": mapping.notes,
            },
        )
        return DescentResult.failure(obs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_value_level_obstruction(
        self,
        py_value_repr: str,
        py_type: PyTypeKind,
        coordinate: str,
    ) -> DescentObstruction | None:
        """
        Check for value-specific obstructions even when the type mapping
        is marked as lossless.

        Returns ``None`` if no obstruction is found.
        """
        stripped = py_value_repr.strip()

        # INT: values >= 2^53 lose precision in JS Number
        if py_type is PyTypeKind.INT:
            try:
                n = int(stripped)
                if abs(n) >= 2 ** 53:
                    return DescentObstruction(
                        coordinate=coordinate,
                        partial_section={
                            "py_value_repr": py_value_repr,
                            "reason": (
                                f"Integer {n!r} has |n| >= 2^53 = {2**53}. "
                                "JavaScript Number cannot represent this exactly. "
                                "Use BigInt for faithful translation."
                            ),
                            "safe_range": f"|n| < {2**53}",
                        },
                    )
            except ValueError:
                pass

        # FLOAT: NaN, Inf are structurally identical but JSON-unrepresentable
        if py_type is PyTypeKind.FLOAT:
            if stripped in ("float('nan')", "float('inf')", "float('-inf')",
                            "math.nan", "math.inf", "-math.inf",
                            "nan", "inf", "-inf", "NaN", "Infinity", "-Infinity"):
                return DescentObstruction(
                    coordinate=coordinate,
                    partial_section={
                        "py_value_repr": py_value_repr,
                        "reason": (
                            "NaN and Infinity have the same IEEE-754 semantics in "
                            "Python and JS, but JSON.stringify(NaN) === 'null' and "
                            "JSON.stringify(Infinity) === 'null' — they cannot be "
                            "round-tripped through JSON serialisation."
                        ),
                    },
                )

        # NONE: warn about undefined divergence
        if py_type is PyTypeKind.NONE and stripped == "None":
            # This is flagged as informational — it translates but loses undefined
            return DescentObstruction(
                coordinate=coordinate,
                partial_section={
                    "py_value_repr": py_value_repr,
                    "reason": (
                        "None translates to null, but JS code may produce undefined "
                        "in positions where Python would produce None. "
                        "The null/undefined distinction is a source of runtime bugs."
                    ),
                },
            )

        # STR: check for surrogate pairs (length semantics differ)
        if py_type is PyTypeKind.STR:
            # Heuristic: if the repr contains emoji or high-plane chars
            content = stripped.strip("'\"")
            if any(ord(c) > 0xFFFF for c in content):
                return DescentObstruction(
                    coordinate=coordinate,
                    partial_section={
                        "py_value_repr": py_value_repr,
                        "reason": (
                            "String contains Unicode code points above U+FFFF "
                            "(supplementary plane).  Python len() counts scalar "
                            "values; JS .length counts UTF-16 code units (surrogate "
                            "pairs count as 2).  Indexing and slicing semantics differ."
                        ),
                    },
                )

        return None
