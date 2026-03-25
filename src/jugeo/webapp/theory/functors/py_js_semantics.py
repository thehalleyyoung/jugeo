"""py_js_semantics.py — Comprehensive formal treatment of every semantic difference
between Python and JavaScript that matters for transpilation confidence.

Every trap is documented as a SemanticTrap dataclass so that code generators can
systematically avoid them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind
from jugeo.geometry.descent import LocalSection, DescentResult, DescentObstruction

__all__ = [
    # Section 1
    "SemanticDomain",
    # Section 2
    "SemanticTrap",
    # Section 3
    "ALL_TRAPS",
    # Section 4
    "TranspilationHazardScanner",
    # Section 5
    "TrapIndex",
    # Section 6
    "JSEquivalent",
    "JS_EQUIVALENTS",
]


# ---------------------------------------------------------------------------
# Section 1 — SemanticDomain
# ---------------------------------------------------------------------------


class SemanticDomain(str, Enum):
    """High-level semantic domains where Python and JavaScript diverge."""

    TYPING = "typing"
    SCOPING = "scoping"
    EXECUTION_MODEL = "execution_model"
    COERCION = "coercion"
    TRUTHINESS = "truthiness"
    NUMERIC = "numeric"
    OBJECT_MODEL = "object_model"
    ASYNC_MODEL = "async_model"
    ERROR_HANDLING = "error_handling"
    MODULE_SYSTEM = "module_system"
    ITERATION = "iteration"
    OPERATORS = "operators"
    STRINGS = "strings"
    REGEX = "regex"
    CLOSURES = "closures"


# ---------------------------------------------------------------------------
# Section 2 — SemanticTrap dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticTrap:
    """Documents a single semantic divergence between Python and JavaScript.

    Attributes:
        trap_id: Unique snake_case identifier for this trap.
        domain: The SemanticDomain this trap belongs to.
        name: Short human-readable name.
        python_behavior: Description of what Python does.
        js_behavior: Description of what JavaScript does.
        example_python: Illustrative Python code snippet.
        example_js: Illustrative JavaScript code snippet.
        is_silent_failure: True if JS produces wrong result without throwing.
        severity: "critical" | "major" | "minor".
    """

    trap_id: str
    domain: SemanticDomain
    name: str
    python_behavior: str
    js_behavior: str
    example_python: str
    example_js: str
    is_silent_failure: bool
    severity: str


# ---------------------------------------------------------------------------
# Section 3 — ALL_TRAPS
# ---------------------------------------------------------------------------

ALL_TRAPS: list[SemanticTrap] = [
    # ------------------------------------------------------------------
    # TRUTHINESS — 8 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="empty_list_falsy",
        domain=SemanticDomain.TRUTHINESS,
        name="Empty list truthiness",
        python_behavior="bool([]) == False — empty list is falsy.",
        js_behavior="!![] === true — every object (including an empty array) is truthy in JS.",
        example_python="if not []:  # truthy check — this branch IS taken",
        example_js="if ([]) { /* this branch IS taken — [] is truthy in JS */ }",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="empty_dict_falsy",
        domain=SemanticDomain.TRUTHINESS,
        name="Empty dict/object truthiness",
        python_behavior="bool({}) == False — empty dict is falsy.",
        js_behavior="!!{} === true — plain objects are always truthy, regardless of contents.",
        example_python="if not {}:  # this branch IS taken in Python",
        example_js="if ({}) { /* this branch IS taken in JS — {} is truthy */ }",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="empty_string_falsy",
        domain=SemanticDomain.TRUTHINESS,
        name="Empty string truthiness — NOT a trap",
        python_behavior='bool("") == False — empty string is falsy.',
        js_behavior='Boolean("") === false — empty string is also falsy in JS.',
        example_python='if not "": pass  # taken in Python',
        example_js='if (!"") { /* also taken in JS */ }',
        is_silent_failure=False,
        severity="minor",
        # Not a real trap: both languages agree. Documented for completeness so
        # that scanners can skip it rather than raising a false positive.
    ),
    SemanticTrap(
        trap_id="zero_falsy",
        domain=SemanticDomain.TRUTHINESS,
        name="Zero (0) truthiness — NOT a trap",
        python_behavior="bool(0) == False — zero integer is falsy.",
        js_behavior="Boolean(0) === false — zero is also falsy in JS.",
        example_python="if not 0: pass  # taken",
        example_js="if (!0) { /* also taken */ }",
        is_silent_failure=False,
        severity="minor",
        # Not a real trap: behaviour is identical. Documented to avoid
        # over-reporting by automated scanners.
    ),
    SemanticTrap(
        trap_id="nan_falsy_js_only",
        domain=SemanticDomain.TRUTHINESS,
        name="NaN truthiness divergence",
        python_behavior="float('nan') is truthy in Python — bool(float('nan')) == True.",
        js_behavior="NaN is falsy in JS — Boolean(NaN) === false.",
        example_python="if float('nan'):  # True — branch IS taken",
        example_js="if (NaN) { /* NOT taken — NaN is falsy in JS */ }",
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="none_vs_null_undefined",
        domain=SemanticDomain.TRUTHINESS,
        name="None vs null vs undefined",
        python_behavior=(
            "Python has ONE bottom value: None. "
            "Unassigned names raise NameError; dict misses raise KeyError."
        ),
        js_behavior=(
            "JS has TWO bottom values: null (explicit absence) and undefined "
            "(uninitialized variable, missing property). Both are falsy."
        ),
        example_python="x = None; bool(x)  # False",
        example_js=(
            "let x;  // x is undefined — not null\n"
            "let y = null;  // y is null\n"
            "Boolean(x) === false; Boolean(y) === false"
        ),
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="zero_string",
        domain=SemanticDomain.TRUTHINESS,
        name='String "0" truthiness vs numeric coercion',
        python_behavior='"0" is truthy (non-empty string). int("0") == 0.',
        js_behavior=(
            '"0" is truthy (Boolean("0") === true), '
            "but Number(\"0\") === 0 which is falsy. "
            'The expression `"0" == false` is true due to Abstract Equality!'
        ),
        example_python='"0" == False  # False — no coercion',
        example_js='"0" == false  // true (Abstract Equality coerces both to 0)',
        is_silent_failure=True,
        severity="minor",
    ),
    SemanticTrap(
        trap_id="negative_zero",
        domain=SemanticDomain.TRUTHINESS,
        name="Negative zero",
        python_behavior="Python integers have no -0; -0 == 0 and bool(-0) == False.",
        js_behavior=(
            "JS has -0 for floats: -0 === 0 is true, but "
            "Object.is(-0, 0) is false. String(-0) === '0'."
        ),
        example_python="-0 == 0  # True; bool(-0) == False",
        example_js="-0 === 0  // true; Object.is(-0, 0) === false; String(-0) === '0'",
        is_silent_failure=True,
        severity="minor",
    ),
    # ------------------------------------------------------------------
    # NUMERIC — 7 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="integer_overflow",
        domain=SemanticDomain.NUMERIC,
        name="Integer overflow / arbitrary precision",
        python_behavior="Python integers are arbitrary-precision; no overflow.",
        js_behavior=(
            "JS numbers are IEEE 754 double-precision floats. "
            "Max safe integer is 2^53 - 1 (Number.MAX_SAFE_INTEGER = 9007199254740991). "
            "Beyond that, integer arithmetic silently loses precision."
        ),
        example_python="2 ** 53  # 9007199254740992 — exact",
        example_js="2 ** 53 + 1  // === 9007199254740992 — wrong, silent precision loss",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="integer_division",
        domain=SemanticDomain.NUMERIC,
        name="Integer (floor) division",
        python_behavior="5 // 2 == 2 — floor division truncates toward negative infinity.",
        js_behavior=(
            "5 / 2 === 2.5 in JS — no floor division operator. "
            "Use Math.trunc(5/2) for truncation toward zero, "
            "or Math.floor(5/2) for floor behavior. "
            "Note: Math.trunc and // differ for negative operands."
        ),
        example_python="5 // 2  # 2;  -7 // 2  # -4 (floor)",
        example_js="Math.trunc(5 / 2)  // 2;  Math.trunc(-7 / 2)  // -3 (NOT -4!)",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="modulo_negative",
        domain=SemanticDomain.NUMERIC,
        name="Modulo with negative operands",
        python_behavior="(-7) % 3 == 2 — result always has the sign of the divisor.",
        js_behavior="(-7) % 3 === -1 — result has the sign of the dividend (C-style).",
        example_python="(-7) % 3  # 2",
        example_js="(-7) % 3  // -1",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="float_nan_comparison",
        domain=SemanticDomain.NUMERIC,
        name="NaN comparison and detection",
        python_behavior=(
            "float('nan') == float('nan') is False. "
            "Use math.isnan(x) to detect NaN."
        ),
        js_behavior=(
            "NaN !== NaN — same. But isNaN() coerces its argument first "
            "(isNaN('foo') === true). Use Number.isNaN(x) for strict check."
        ),
        example_python="import math; math.isnan(float('nan'))  # True",
        example_js=(
            "isNaN('foo')  // true (coerces!) — use Number.isNaN('foo')  // false"
        ),
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="float_to_string",
        domain=SemanticDomain.NUMERIC,
        name="Float-to-string representation",
        python_behavior='str(1.0) → "1.0" — Python preserves the decimal point.',
        js_behavior='String(1.0) → "1" — JS drops trailing .0 for whole floats.',
        example_python='str(1.0)  # "1.0"',
        example_js="String(1.0)  // '1'",
        is_silent_failure=True,
        severity="minor",
    ),
    SemanticTrap(
        trap_id="number_string_coercion",
        domain=SemanticDomain.NUMERIC,
        name="Number + string coercion",
        python_behavior='1 + "2" raises TypeError — Python never implicitly coerces.',
        js_behavior=(
            '1 + "2" === "12" — when either operand of + is a string, '
            "JS converts the other to a string and concatenates."
        ),
        example_python='1 + "2"  # TypeError: unsupported operand type(s)',
        example_js='1 + "2"  // "12" — silent coercion',
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="unary_plus",
        domain=SemanticDomain.NUMERIC,
        name="Unary plus coercion",
        python_behavior="+x is identity for numeric types; raises TypeError for strings.",
        js_behavior=(
            "Unary + coerces to number: "
            '+"3" → 3, +true → 1, +null → 0, +[] → 0, +{} → NaN.'
        ),
        example_python='+"3"  # TypeError',
        example_js='+"3"  // 3;  +[]  // 0;  +{}  // NaN',
        is_silent_failure=True,
        severity="major",
    ),
    # ------------------------------------------------------------------
    # SCOPING — 6 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="var_hoisting",
        domain=SemanticDomain.SCOPING,
        name="var hoisting",
        python_behavior=(
            "No hoisting. Reading a name before assignment raises NameError."
        ),
        js_behavior=(
            "var declarations are hoisted to the top of their function scope. "
            "Reading before the assignment gives undefined (not ReferenceError)."
        ),
        example_python="print(x)  # NameError\nx = 1",
        example_js="console.log(x);  // undefined (not error)\nvar x = 1;",
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="let_tdz",
        domain=SemanticDomain.SCOPING,
        name="let/const Temporal Dead Zone",
        python_behavior="Variables cannot be accessed before assignment.",
        js_behavior=(
            "let and const have a Temporal Dead Zone (TDZ): accessing them before "
            "their declaration within the same block throws ReferenceError."
        ),
        example_python="# N/A — Python NameError is analogous",
        example_js=(
            "{ console.log(x); let x = 1; }  "
            "// ReferenceError: Cannot access 'x' before initialization"
        ),
        is_silent_failure=False,
        severity="major",
    ),
    SemanticTrap(
        trap_id="closure_late_binding_loop_var",
        domain=SemanticDomain.SCOPING,
        name="Closure late-binding in loops",
        python_behavior=(
            "Loop variable is captured by reference in Python closures too. "
            "All closures see the final value of the loop variable."
        ),
        js_behavior=(
            "var in a for loop shares one binding across iterations. "
            "let creates a fresh binding per iteration — closures capture different values."
        ),
        example_python=(
            "fns = [lambda: i for i in range(3)]\n"
            "[f() for f in fns]  # [2, 2, 2] — late binding"
        ),
        example_js=(
            "const fns = [];\n"
            "for (var i = 0; i < 3; i++) fns.push(() => i);\n"
            "fns.map(f => f())  // [3, 3, 3] with var\n"
            "// Use let: [0, 1, 2] — each iteration gets its own binding"
        ),
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="global_assignment",
        domain=SemanticDomain.SCOPING,
        name="Implicit global creation",
        python_behavior=(
            "Assignment inside a function creates a local variable. "
            "global keyword required to write to a global."
        ),
        js_behavior=(
            "Assignment without let/const/var creates (or overwrites) a property "
            "on the global object silently (in non-strict mode)."
        ),
        example_python=(
            "x = 0\ndef f():\n    x = 1  # local — does NOT affect global x"
        ),
        example_js=(
            "// non-strict mode:\n"
            "function f() { x = 1; }  // silently creates/writes window.x"
        ),
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="legb_vs_scope_chain",
        domain=SemanticDomain.SCOPING,
        name="LEGB vs scope chain",
        python_behavior=(
            "Python lookup order: Local → Enclosing → Global → Builtin (LEGB). "
            "No prototype involved."
        ),
        js_behavior=(
            "JS lookup walks the lexical scope chain. "
            "Global scope is the global object; prototype chain is separate from scope chain."
        ),
        example_python="len = 10  # shadows builtin len; restoring needs del len",
        example_js=(
            "// Shadowing is similar, but the global object (window/global) "
            "participates in scope chain differently."
        ),
        is_silent_failure=False,
        severity="minor",
    ),
    SemanticTrap(
        trap_id="delete_operator",
        domain=SemanticDomain.SCOPING,
        name="del vs delete semantics",
        python_behavior=(
            "del x removes the name binding from the current namespace. "
            "del d[k] removes a key from a dict."
        ),
        js_behavior=(
            "delete obj.prop removes an own enumerable property from an object. "
            "delete cannot remove local variables (returns false in strict mode)."
        ),
        example_python="x = 1; del x; x  # NameError",
        example_js=(
            "let obj = {a: 1}; delete obj.a;  // obj is now {}\n"
            "let x = 1; delete x;  // false in strict mode — x still exists"
        ),
        is_silent_failure=False,
        severity="major",
    ),
    # ------------------------------------------------------------------
    # OBJECT_MODEL — 6 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="prototype_vs_mro",
        domain=SemanticDomain.OBJECT_MODEL,
        name="MRO vs prototype chain",
        python_behavior=(
            "Python uses C3 linearization for Method Resolution Order. "
            "Multiple inheritance is fully supported."
        ),
        js_behavior=(
            "JS uses a single-linked prototype chain. "
            "Multiple inheritance is impossible without mixins."
        ),
        example_python="class C(A, B): pass  # MRO: C → A → B → object",
        example_js=(
            "// JS class syntax only allows one extends clause. "
            "Mixins must be composed manually."
        ),
        is_silent_failure=False,
        severity="major",
    ),
    SemanticTrap(
        trap_id="this_dynamic_binding",
        domain=SemanticDomain.OBJECT_MODEL,
        name="this dynamic binding",
        python_behavior=(
            "self is always the explicit first parameter, bound at method definition. "
            "Passing obj.method as a callback retains the reference correctly."
        ),
        js_behavior=(
            "this is determined at call site, not at definition. "
            "Passing obj.method as a bare callback loses the intended this binding."
        ),
        example_python="callback = obj.method; callback()  # self is obj — works",
        example_js=(
            "const cb = obj.method; cb();  // this is undefined (strict) or global\n"
            "// Fix: const cb = obj.method.bind(obj); or use arrow functions"
        ),
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="property_any_key",
        domain=SemanticDomain.OBJECT_MODEL,
        name="Object key types",
        python_behavior=(
            "Python dict keys can be any hashable object: int, tuple, frozenset, etc."
        ),
        js_behavior=(
            "JS object keys are coerced to strings (or Symbols). "
            "Numeric keys become strings: obj[1] === obj['1']. "
            "Use Map for non-string keys."
        ),
        example_python="d = {(1, 2): 'tuple key'}  # valid",
        example_js=(
            "const obj = {}; obj[1] = 'x'; Object.keys(obj)  // ['1'] — coerced"
        ),
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="delete_nonexistent",
        domain=SemanticDomain.OBJECT_MODEL,
        name="Deleting nonexistent key",
        python_behavior="del d['missing'] raises KeyError.",
        js_behavior=(
            "delete obj.nonExistent silently returns true (the property just wasn't there)."
        ),
        example_python="del d['missing']  # KeyError",
        example_js="delete obj.nonExistent  // true — no error",
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="getter_setter_descriptor",
        domain=SemanticDomain.OBJECT_MODEL,
        name="Descriptor protocol vs Object.defineProperty",
        python_behavior=(
            "Python descriptor protocol (__get__/__set__/__delete__) is class-level "
            "and participates in attribute lookup automatically."
        ),
        js_behavior=(
            "JS uses Object.defineProperty with get/set. "
            "There is no equivalent to data descriptors that intercept via class inheritance."
        ),
        example_python="class C:\n    @property\n    def x(self): return self._x",
        example_js=(
            "Object.defineProperty(obj, 'x', { get() { return this._x; } });"
        ),
        is_silent_failure=False,
        severity="minor",
    ),
    SemanticTrap(
        trap_id="frozen_object",
        domain=SemanticDomain.OBJECT_MODEL,
        name="Immutability: tuple vs const",
        python_behavior="tuple is deeply immutable — elements cannot be reassigned.",
        js_behavior=(
            "const prevents variable reassignment, but the object/array it points to "
            "remains mutable. Use Object.freeze() for shallow immutability."
        ),
        example_python="t = (1, [2, 3]); t[1].append(4)  # t is still (1, [2,3,4]) — nested mutability",
        example_js=(
            "const arr = [1, 2]; arr.push(3);  // valid — arr is [1,2,3]\n"
            "// arr = [] would throw TypeError"
        ),
        is_silent_failure=True,
        severity="major",
    ),
    # ------------------------------------------------------------------
    # ASYNC_MODEL — 5 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="promise_rejection_swallowing",
        domain=SemanticDomain.ASYNC_MODEL,
        name="Unhandled Promise rejection",
        python_behavior=(
            "Unhandled exceptions in coroutines produce 'coroutine was never awaited' "
            "RuntimeWarning and are not silently lost."
        ),
        js_behavior=(
            "Historically, unhandled Promise rejections were silently swallowed. "
            "Modern engines emit an 'unhandledRejection' event, but this is async "
            "and may not surface in all environments."
        ),
        example_python="async def f(): raise ValueError('!')\nasyncio.run(f())  # propagates",
        example_js=(
            "async function f() { throw new Error('!'); }\n"
            "f();  // rejection may be silent if not awaited/caught"
        ),
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="async_return_type",
        domain=SemanticDomain.ASYNC_MODEL,
        name="async function return type",
        python_behavior=(
            "async def f() returns a coroutine object when called. "
            "Must be awaited or passed to an event loop."
        ),
        js_behavior=(
            "async function f() returns a Promise when called. "
            "The Promise resolves with the return value."
        ),
        example_python="import asyncio; coro = f(); asyncio.run(coro)",
        example_js="const p = f();  // Promise object\nawait f();  // resolves value",
        is_silent_failure=False,
        severity="major",
    ),
    SemanticTrap(
        trap_id="event_loop_blocking",
        domain=SemanticDomain.ASYNC_MODEL,
        name="Event loop blocking",
        python_behavior=(
            "asyncio event loop can be blocked by CPU-bound synchronous code. "
            "GIL exists; True parallelism needs multiprocessing."
        ),
        js_behavior=(
            "JS event loop IS the runtime — blocking it freezes the entire environment. "
            "No GIL concept; no true threads (Web Workers are separate contexts)."
        ),
        example_python=(
            "async def bad():\n"
            "    time.sleep(5)  # blocks asyncio loop — but at least it's obvious"
        ),
        example_js=(
            "async function bad() {\n"
            "  while (true) {}  // freezes browser/Node entirely\n"
            "}"
        ),
        is_silent_failure=False,
        severity="major",
    ),
    SemanticTrap(
        trap_id="cancel_semantics",
        domain=SemanticDomain.ASYNC_MODEL,
        name="Task/Promise cancellation",
        python_behavior=(
            "asyncio.Task.cancel() injects CancelledError into the coroutine at the "
            "next await point, allowing cleanup via try/except CancelledError."
        ),
        js_behavior=(
            "Promises have no native cancellation mechanism. "
            "AbortController cancels fetch requests only. "
            "Libraries (e.g. p-cancelable) provide wrappers."
        ),
        example_python="task.cancel(); await asyncio.gather(task, return_exceptions=True)",
        example_js=(
            "const controller = new AbortController();\n"
            "fetch(url, { signal: controller.signal });\n"
            "controller.abort();  // only works for fetch"
        ),
        is_silent_failure=False,
        severity="major",
    ),
    SemanticTrap(
        trap_id="generator_send",
        domain=SemanticDomain.ASYNC_MODEL,
        name="Generator send/throw/return semantics",
        python_behavior=(
            "Python generators support gen.send(value) and gen.throw(exc). "
            "return inside a generator raises StopIteration(value)."
        ),
        js_behavior=(
            "JS generators also support gen.next(value) and gen.throw(err). "
            "return inside a generator produces {value: x, done: true} and ends iteration."
        ),
        example_python="def g():\n    x = yield 1\n    return x\ngen = g(); next(gen); gen.send(42)",
        example_js=(
            "function* g() { const x = yield 1; return x; }\n"
            "const gen = g(); gen.next(); gen.next(42);  // {value: 42, done: true}"
        ),
        is_silent_failure=False,
        severity="minor",
    ),
    # ------------------------------------------------------------------
    # ERROR_HANDLING — 3 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="silent_json_fail",
        domain=SemanticDomain.ERROR_HANDLING,
        name="JSON serialization of non-serializable values",
        python_behavior="json.dumps(x) raises TypeError for non-serializable values.",
        js_behavior=(
            "JSON.stringify(fn) returns undefined (not a string). "
            "Functions inside objects are silently omitted from the output."
        ),
        example_python="import json; json.dumps(lambda x: x)  # TypeError",
        example_js=(
            "JSON.stringify(() => {})  // undefined\n"
            "JSON.stringify({a: 1, b: () => {}})  // '{\"a\":1}' — b silently dropped"
        ),
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="attribute_error_vs_undefined",
        domain=SemanticDomain.ERROR_HANDLING,
        name="Missing attribute/property access",
        python_behavior="obj.missing raises AttributeError.",
        js_behavior=(
            "obj.missing returns undefined — no error. "
            "Chaining further access like obj.missing.deep throws TypeError."
        ),
        example_python="obj.missing  # AttributeError",
        example_js=(
            "obj.missing  // undefined — no error\n"
            "obj.missing.deep  // TypeError: Cannot read properties of undefined"
        ),
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="exception_chaining",
        domain=SemanticDomain.ERROR_HANDLING,
        name="Exception chaining",
        python_behavior=(
            "raise X from Y sets X.__cause__ = Y and X.__context__. "
            "Chained exceptions are displayed in tracebacks."
        ),
        js_behavior=(
            "JS Error objects have no native chaining mechanism. "
            "Some libraries add a cause property; ES2022 adds Error({cause: err}) "
            "but it is not structurally enforced."
        ),
        example_python=(
            "try:\n    risky()\nexcept ValueError as e:\n    raise RuntimeError('wrap') from e"
        ),
        example_js=(
            "try { risky(); } catch (e) {\n"
            "  throw new Error('wrap', { cause: e });  // ES2022+\n"
            "}"
        ),
        is_silent_failure=False,
        severity="minor",
    ),
    # ------------------------------------------------------------------
    # OPERATORS — 3 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="equality_coercion",
        domain=SemanticDomain.OPERATORS,
        name="Abstract Equality (==) coercion in JS",
        python_behavior=(
            "== always dispatches to __eq__; no cross-type numeric coercion. "
            "'1' == 1 is False."
        ),
        js_behavior=(
            "== performs Abstract Equality Comparison with coercion. "
            "'1' == 1 is true. Always use === in JS."
        ),
        example_python="'1' == 1  # False",
        example_js="'1' == 1  // true — use '1' === 1  // false",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="in_operator_semantics",
        domain=SemanticDomain.OPERATORS,
        name="in operator: value membership vs property existence",
        python_behavior=(
            "x in lst checks for value membership in sequences. "
            "x in d checks key membership in dicts."
        ),
        js_behavior=(
            "'prop' in obj tests for property existence (own or inherited), not value. "
            "For array values, use Array.prototype.includes()."
        ),
        example_python="3 in [1, 2, 3]  # True — value membership",
        example_js=(
            "3 in [1, 2, 3]  // false (tests index '3', not value 3)\n"
            "[1, 2, 3].includes(3)  // true"
        ),
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="bitwise_truncation",
        domain=SemanticDomain.OPERATORS,
        name="Bitwise ops and 32-bit truncation",
        python_behavior=(
            "Python bitwise operators work on arbitrary-precision integers. "
            "x >> 32 shifts a large integer correctly."
        ),
        js_behavior=(
            "JS bitwise operators apply ToInt32 first, converting to signed 32-bit int. "
            "Large values are silently truncated before the operation."
        ),
        example_python="(2**40) >> 32  # 256 — correct",
        example_js="(2**40) >> 32  // 0 — ToInt32 truncates 2**40 to 0 first",
        is_silent_failure=True,
        severity="major",
    ),
    # ------------------------------------------------------------------
    # STRINGS — 3 traps
    # ------------------------------------------------------------------
    SemanticTrap(
        trap_id="negative_indexing",
        domain=SemanticDomain.STRINGS,
        name="Negative string/array indexing",
        python_behavior="s[-1] returns the last character/element.",
        js_behavior=(
            "s[-1] returns undefined (arrays/strings have no negative index syntax). "
            "Use s.at(-1) (ES2022+) or s[s.length - 1]."
        ),
        example_python="'hello'[-1]  # 'o'",
        example_js="'hello'[-1]  // undefined — use 'hello'.at(-1)  // 'o'",
        is_silent_failure=True,
        severity="critical",
    ),
    SemanticTrap(
        trap_id="string_multiplication",
        domain=SemanticDomain.STRINGS,
        name="String repetition operator",
        python_behavior='"x" * 3 → "xxx" — str * int repeats the string.',
        js_behavior='"x" * 3 → NaN — * is always numeric multiplication in JS.',
        example_python='"x" * 3  # "xxx"',
        example_js='"x" * 3  // NaN — use "x".repeat(3)  // "xxx"',
        is_silent_failure=True,
        severity="major",
    ),
    SemanticTrap(
        trap_id="regex_flag_global_state",
        domain=SemanticDomain.REGEX,
        name="RegExp global flag stateful lastIndex",
        python_behavior=(
            "re.match/re.search/re.findall are stateless — each call starts from scratch."
        ),
        js_behavior=(
            "A RegExp compiled with the g flag maintains a lastIndex property. "
            "Re-using the same /pattern/g literal or object in a loop causes exec() "
            "to resume from where it left off — unexpected skips or null results."
        ),
        example_python="import re; re.findall(r'\\d+', '1 2 3')  # ['1', '2', '3'] always",
        example_js=(
            "const re = /\\d+/g;\n"
            "re.exec('1 2 3');  // {index:0, ...}\n"
            "re.exec('1 2 3');  // {index:2, ...} — resumes from lastIndex!"
        ),
        is_silent_failure=True,
        severity="major",
    ),
]


# ---------------------------------------------------------------------------
# Section 4 — TranspilationHazardScanner
# ---------------------------------------------------------------------------


class TranspilationHazardScanner:
    """Scans Python source code for patterns that are hazardous to transpile to JS."""

    # Lookup helpers built once at class level for efficiency
    _TRAP_BY_ID: dict[str, SemanticTrap] = {}

    @classmethod
    def _trap(cls, trap_id: str) -> SemanticTrap:
        if not cls._TRAP_BY_ID:
            cls._TRAP_BY_ID = {t.trap_id: t for t in ALL_TRAPS}
        return cls._TRAP_BY_ID[trap_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, python_source: str) -> list[tuple[int, str, SemanticTrap]]:
        """Return list of (line_number, matched_snippet, trap) for detected hazards."""
        lines = python_source.splitlines()
        results: list[tuple[int, str, SemanticTrap]] = []
        results.extend(self._check_truthiness_containers(lines))
        results.extend(self._check_integer_division(lines))
        results.extend(self._check_negative_modulo(lines))
        results.extend(self._check_negative_indexing(lines))
        results.extend(self._check_in_list(lines))
        results.extend(self._check_string_multiplication(lines))
        return results

    # ------------------------------------------------------------------
    # Private checkers
    # ------------------------------------------------------------------

    def _check_truthiness_containers(
        self, lines: list[str]
    ) -> list[tuple[int, str, SemanticTrap]]:
        """Detect: `if some_name:` or `while some_name:` where name looks like a
        container — truthy in JS even when empty."""
        results = []
        # Match: if/while <identifier>: — excludes literals and calls
        pattern = re.compile(r"\b(if|while|elif)\s+([A-Za-z_]\w*)\s*:")
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            m = pattern.search(line)
            if m:
                snippet = m.group(0)
                # Heuristic: variable names ending in common container suffixes
                name = m.group(2)
                if re.search(
                    r"(list|dict|arr|items|keys|values|data|collection|set|queue|stack)$",
                    name,
                    re.IGNORECASE,
                ) or len(name) <= 8:
                    trap = self._trap("empty_list_falsy")
                    results.append((lineno, snippet, trap))
        return results

    def _check_integer_division(
        self, lines: list[str]
    ) -> list[tuple[int, str, SemanticTrap]]:
        """Detect: `//` operator usage (floor division)."""
        results = []
        pattern = re.compile(r"(?<![:/])//" )
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Exclude string literals (simplified: skip lines that are all string)
            m = pattern.search(line)
            if m:
                snippet = line.strip()
                trap = self._trap("integer_division")
                results.append((lineno, snippet, trap))
        return results

    def _check_negative_modulo(
        self, lines: list[str]
    ) -> list[tuple[int, str, SemanticTrap]]:
        """Detect: `%` with a negative literal or unary-minus expression as left operand."""
        results = []
        # Match: (-<expr>) % or -<name> %
        pattern = re.compile(r"(-\s*\d+|\(-[^)]+\))\s*%")
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            m = pattern.search(line)
            if m:
                snippet = m.group(0)
                trap = self._trap("modulo_negative")
                results.append((lineno, snippet, trap))
        return results

    def _check_negative_indexing(
        self, lines: list[str]
    ) -> list[tuple[int, str, SemanticTrap]]:
        """Detect: `[-1]`, `[-2]`, etc."""
        results = []
        pattern = re.compile(r"\[-\d+\]")
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for m in pattern.finditer(line):
                snippet = m.group(0)
                trap = self._trap("negative_indexing")
                results.append((lineno, snippet, trap))
        return results

    def _check_in_list(
        self, lines: list[str]
    ) -> list[tuple[int, str, SemanticTrap]]:
        """Detect: `x in [...]` — JS `in` does NOT do value membership for arrays."""
        results = []
        # Match: <expr> in [
        pattern = re.compile(r"\bin\s*\[")
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Skip 'for x in [' — that's iteration, not membership test
            if re.search(r"\bfor\b", line):
                continue
            m = pattern.search(line)
            if m:
                snippet = line.strip()
                trap = self._trap("in_operator_semantics")
                results.append((lineno, snippet, trap))
        return results

    def _check_string_multiplication(
        self, lines: list[str]
    ) -> list[tuple[int, str, SemanticTrap]]:
        """Detect: `"..." * n` or `n * "..."`."""
        results = []
        # Match string literal followed by * or preceded by *
        pattern = re.compile(r'(["\'].*?["\'])\s*\*|\*\s*(["\'].*?["\'])')
        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            m = pattern.search(line)
            if m:
                snippet = m.group(0)
                trap = self._trap("string_multiplication")
                results.append((lineno, snippet, trap))
        return results

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self, results: list[tuple[int, str, SemanticTrap]]) -> str:
        """Return a formatted summary grouped by severity."""
        if not results:
            return "No transpilation hazards detected."

        by_severity: dict[str, list[tuple[int, str, SemanticTrap]]] = {
            "critical": [],
            "major": [],
            "minor": [],
        }
        for item in results:
            sev = item[2].severity
            by_severity.setdefault(sev, []).append(item)

        lines_out: list[str] = [
            f"Transpilation hazard scan — {len(results)} issue(s) found",
            "=" * 60,
        ]
        for sev in ("critical", "major", "minor"):
            items = by_severity.get(sev, [])
            if not items:
                continue
            lines_out.append(f"\n[{sev.upper()}] — {len(items)} issue(s)")
            for lineno, snippet, trap in items:
                lines_out.append(f"  line {lineno:>4}: {trap.name}")
                lines_out.append(f"            snippet : {snippet!r}")
                lines_out.append(f"            trap_id : {trap.trap_id}")
        return "\n".join(lines_out)


# ---------------------------------------------------------------------------
# Section 5 — TrapIndex
# ---------------------------------------------------------------------------


@dataclass
class TrapIndex:
    """Index of all semantic traps, queryable by domain and severity."""

    by_domain: dict[SemanticDomain, list[SemanticTrap]]
    by_severity: dict[str, list[SemanticTrap]]
    critical_traps: list[SemanticTrap]

    @classmethod
    def build(cls) -> TrapIndex:
        """Build a TrapIndex from the module-level ALL_TRAPS list."""
        by_domain: dict[SemanticDomain, list[SemanticTrap]] = {}
        by_severity: dict[str, list[SemanticTrap]] = {}

        for trap in ALL_TRAPS:
            by_domain.setdefault(trap.domain, []).append(trap)
            by_severity.setdefault(trap.severity, []).append(trap)

        return cls(
            by_domain=by_domain,
            by_severity=by_severity,
            critical_traps=by_severity.get("critical", []),
        )

    def lookup(self, trap_id: str) -> Optional[SemanticTrap]:
        """Return the SemanticTrap with the given trap_id, or None."""
        for trap in ALL_TRAPS:
            if trap.trap_id == trap_id:
                return trap
        return None


# ---------------------------------------------------------------------------
# Section 6 — JSEquivalent and JS_EQUIVALENTS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JSEquivalent:
    """Maps a Python expression to its safe JavaScript equivalent."""

    python_expr: str
    js_safe_expr: str
    caveat: Optional[str]


JS_EQUIVALENTS: list[JSEquivalent] = [
    JSEquivalent(
        python_expr="bool(x)",
        js_safe_expr="Boolean(x)",
        caveat="Boolean([]) is true in JS, not false — containers are always truthy.",
    ),
    JSEquivalent(
        python_expr="x // y",
        js_safe_expr="Math.trunc(x / y)",
        caveat=(
            "Math.trunc truncates toward zero; Python // floors toward -∞. "
            "They differ for negative operands: Python: -7//2==-4, JS: Math.trunc(-7/2)==-3."
        ),
    ),
    JSEquivalent(
        python_expr="s[-1]  # or lst[-1]",
        js_safe_expr="s.at(-1)",
        caveat=(
            "at() requires modern JS (ES2022 / Node 16.6+). "
            "Fallback: s[s.length - 1]"
        ),
    ),
    JSEquivalent(
        python_expr="s * n",
        js_safe_expr="s.repeat(n)",
        caveat="s.repeat(n) requires n >= 0 and n < Infinity, otherwise throws RangeError.",
    ),
    JSEquivalent(
        python_expr="x in lst",
        js_safe_expr="lst.includes(x)",
        caveat=(
            "Only reliable for primitive values. NaN: [NaN].includes(NaN) is true; "
            "[NaN].indexOf(NaN) is -1."
        ),
    ),
    JSEquivalent(
        python_expr="json.dumps(x)",
        js_safe_expr="JSON.stringify(x)",
        caveat=(
            "Functions and undefined values are silently omitted from objects; "
            "JSON.stringify(undefined) returns undefined (not a string)."
        ),
    ),
    JSEquivalent(
        python_expr="isinstance(x, list)",
        js_safe_expr="Array.isArray(x)",
        caveat=None,
    ),
    JSEquivalent(
        python_expr="isinstance(x, dict)",
        js_safe_expr="typeof x === 'object' && x !== null && !Array.isArray(x)",
        caveat=(
            "This check also passes for class instances and other objects. "
            "Use a dedicated type tag if strict dict-only check is needed."
        ),
    ),
    JSEquivalent(
        python_expr="x is None",
        js_safe_expr="x == null",
        caveat=(
            "== null catches both null AND undefined via Abstract Equality. "
            "Use === null if you need to distinguish null from undefined."
        ),
    ),
    JSEquivalent(
        python_expr="x is not None",
        js_safe_expr="x != null",
        caveat="Same as above — != null is true for any value that is neither null nor undefined.",
    ),
    JSEquivalent(
        python_expr="len(s)  # or len(lst)",
        js_safe_expr="s.length",
        caveat=(
            "length is a property, not a function call. "
            "For Map/Set, use map.size / set.size instead."
        ),
    ),
    JSEquivalent(
        python_expr="d.get(k, default)",
        js_safe_expr="obj[k] ?? default",
        caveat=(
            "?? (nullish coalescing) only falls through on null/undefined, not falsy. "
            "obj[k] || default falls through on any falsy value (0, '', false)."
        ),
    ),
    JSEquivalent(
        python_expr="x ** y",
        js_safe_expr="x ** y",
        caveat=(
            "** is ES2016+. Fallback: Math.pow(x, y). "
            "Operator precedence differs: -2**2 is a SyntaxError in JS without parens."
        ),
    ),
    JSEquivalent(
        python_expr="a = b = 0",
        js_safe_expr="let a = 0, b = 0;",
        caveat=(
            "JS chained assignment (let a = b = 0) initialises only a with let; "
            "b becomes an implicit global in non-strict mode."
        ),
    ),
    JSEquivalent(
        python_expr="float('inf')",
        js_safe_expr="Infinity",
        caveat=None,
    ),
    JSEquivalent(
        python_expr="float('nan')",
        js_safe_expr="NaN",
        caveat=(
            "NaN !== NaN — always use Number.isNaN(x) to test, not x === NaN. "
            "The legacy isNaN() coerces its argument first."
        ),
    ),
    JSEquivalent(
        python_expr="any(pred(x) for x in iterable)",
        js_safe_expr="arr.some(x => pred(x))",
        caveat="arr must be a real Array; use Array.from(iterable).some(...) for iterables.",
    ),
    JSEquivalent(
        python_expr="all(pred(x) for x in iterable)",
        js_safe_expr="arr.every(x => pred(x))",
        caveat="Same iterable-to-Array caveat as some().",
    ),
    JSEquivalent(
        python_expr="list(range(n))",
        js_safe_expr="Array.from({length: n}, (_, i) => i)",
        caveat="Alternative: [...Array(n).keys()] — both require n >= 0.",
    ),
]
