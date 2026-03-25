from __future__ import annotations

__all__ = [
    "ErrorKind",
    "ErrorMappingEntry",
    "ERROR_MAPPINGS",
    "ErrorHandlingTrap",
    "ERROR_HANDLING_TRAPS",
    "SafeErrorPatterns",
    "ErrorCodeAnalyzer",
]

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# 1. ErrorKind
# ---------------------------------------------------------------------------

class ErrorKind(str, Enum):
    PY_EXCEPTION = "PY_EXCEPTION"
    PY_BASE_EXCEPTION = "PY_BASE_EXCEPTION"
    JS_ERROR = "JS_ERROR"
    JS_TYPE_ERROR = "JS_TYPE_ERROR"
    JS_REFERENCE_ERROR = "JS_REFERENCE_ERROR"
    JS_RANGE_ERROR = "JS_RANGE_ERROR"
    JS_SYNTAX_ERROR = "JS_SYNTAX_ERROR"
    JS_URI_ERROR = "JS_URI_ERROR"
    JS_EVAL_ERROR = "JS_EVAL_ERROR"

    def is_js(self) -> bool:
        return self.value.startswith("JS_")

    def is_python(self) -> bool:
        return self.value.startswith("PY_")


# ---------------------------------------------------------------------------
# 2. ErrorMappingEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorMappingEntry:
    py_exception: str
    js_equivalent: str
    example_py: str
    example_js: str
    notes: str


# ---------------------------------------------------------------------------
# 3. ERROR_MAPPINGS
# ---------------------------------------------------------------------------

ERROR_MAPPINGS: list[ErrorMappingEntry] = [
    ErrorMappingEntry(
        py_exception="AttributeError",
        js_equivalent="TypeError",
        example_py="obj.missing_method()",
        example_js="obj.missing();  // undefined is not a function → TypeError",
        notes=(
            "Accessing a missing property in JS returns undefined silently. "
            "The TypeError only surfaces when you *use* that undefined as a function "
            "or with an operator that requires a concrete value."
        ),
    ),
    ErrorMappingEntry(
        py_exception="KeyError",
        js_equivalent="undefined (no error)",
        example_py="d['missing']",
        example_js="obj['missing']  // → undefined",
        notes=(
            "Python dicts raise KeyError on missing keys; JS objects silently return "
            "undefined. Use `'key' in obj` or optional chaining `obj?.key` to guard."
        ),
    ),
    ErrorMappingEntry(
        py_exception="IndexError",
        js_equivalent="undefined (no error)",
        example_py="lst[100]  # IndexError",
        example_js="arr[100]  // → undefined",
        notes=(
            "Out-of-bounds array access in JS yields undefined instead of raising. "
            "Downstream operations on the undefined value may then throw TypeError."
        ),
    ),
    ErrorMappingEntry(
        py_exception="TypeError",
        js_equivalent="TypeError",
        example_py="'hello' + 5  # in strict typing contexts",
        example_js="null.property  // → TypeError",
        notes=(
            "Both languages have TypeError, but JS coerces types aggressively "
            "('hello' + 5 → 'hello5') so many Python TypeErrors become silent "
            "coercions in JS. The JS TypeError mainly fires on null/undefined access."
        ),
    ),
    ErrorMappingEntry(
        py_exception="ValueError",
        js_equivalent="RangeError or TypeError",
        example_py="int('abc')  # ValueError",
        example_js="new Array(-1)  // RangeError; Number('abc') → NaN (no error)",
        notes=(
            "JS rarely raises for bad values — Number('abc') returns NaN silently. "
            "RangeError appears for out-of-range numeric arguments (e.g. Array length). "
            "TypeError covers wrong-type-for-operation cases."
        ),
    ),
    ErrorMappingEntry(
        py_exception="ZeroDivisionError",
        js_equivalent="none (Infinity or NaN)",
        example_py="1 / 0  # ZeroDivisionError",
        example_js="1 / 0  // → Infinity; 0 / 0 → NaN",
        notes=(
            "JS follows IEEE 754 strictly: division by zero yields Infinity or NaN "
            "rather than raising. Silent numeric corruption can propagate far."
        ),
    ),
    ErrorMappingEntry(
        py_exception="ImportError",
        js_equivalent="none at runtime / network error for dynamic imports",
        example_py="import missing_module  # ImportError",
        example_js="import('./missing.js')  // rejected Promise (network / 404)",
        notes=(
            "Static ES module failures surface as parse-time errors before execution. "
            "Dynamic import() returns a rejected Promise — must be caught with .catch()."
        ),
    ),
    ErrorMappingEntry(
        py_exception="StopIteration",
        js_equivalent="{done: true} in iterator protocol",
        example_py="next(iter([]))  # StopIteration",
        example_js="iter.next()  // → { value: undefined, done: true }",
        notes=(
            "Python signals iterator exhaustion by raising StopIteration; JS uses a "
            "sentinel value object. The JS iterator protocol never throws for exhaustion."
        ),
    ),
    ErrorMappingEntry(
        py_exception="RecursionError",
        js_equivalent="RangeError: Maximum call stack size exceeded",
        example_py="def f(): f()\nf()  # RecursionError",
        example_js="function f() { f(); } f();  // RangeError",
        notes=(
            "Both environments cap stack depth, but the limit and error type differ. "
            "JS RangeError for stack overflow is catchable, as is Python RecursionError."
        ),
    ),
    ErrorMappingEntry(
        py_exception="NameError",
        js_equivalent="ReferenceError: x is not defined",
        example_py="print(undefined_var)  # NameError",
        example_js="console.log(undeclaredVar);  // ReferenceError",
        notes=(
            "Accessing an undeclared variable raises ReferenceError in JS (strict mode "
            "and ES6+ always). In sloppy mode, reading undeclared vars also raises; "
            "writing them creates implicit globals (a notorious trap)."
        ),
    ),
    ErrorMappingEntry(
        py_exception="FileNotFoundError",
        js_equivalent="DOMException or Response.ok == false",
        example_py="open('missing.txt')  # FileNotFoundError",
        example_js="fetch('/missing')  // Response with .ok === false (status 404)",
        notes=(
            "Browser JS has no filesystem; file-like ops go through fetch or File API. "
            "fetch() only rejects on network failure, not HTTP errors — check response.ok. "
            "Node.js fs operations throw ENOENT errors."
        ),
    ),
    ErrorMappingEntry(
        py_exception="OverflowError",
        js_equivalent="none (Infinity)",
        example_py="2 ** 10000  # Python int is unbounded, but float overflows",
        example_js="Number.MAX_VALUE * 2  // → Infinity",
        notes=(
            "Python integers are arbitrary-precision; floats raise OverflowError. "
            "JS numbers are IEEE 754 doubles that saturate to Infinity silently."
        ),
    ),
    ErrorMappingEntry(
        py_exception="UnicodeDecodeError",
        js_equivalent="none / replacement characters",
        example_py="b'\\xff'.decode('utf-8')  # UnicodeDecodeError",
        example_js="new TextDecoder().decode(new Uint8Array([0xff]))  // replacement char",
        notes=(
            "The Web TextDecoder API silently replaces undecodable bytes with U+FFFD "
            "by default. Python raises explicitly, requiring error= parameter to relax."
        ),
    ),
]


# ---------------------------------------------------------------------------
# 4. ErrorHandlingTrap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorHandlingTrap:
    trap_id: str
    description: str
    python_code: str
    js_code: str
    consequence: str
    fix: str


# ---------------------------------------------------------------------------
# 5. ERROR_HANDLING_TRAPS
# ---------------------------------------------------------------------------

ERROR_HANDLING_TRAPS: list[ErrorHandlingTrap] = [
    ErrorHandlingTrap(
        trap_id="catch_all_loses_type",
        description=(
            "Python `except Exception as e` always gives an Exception subclass; "
            "JS `catch(e)` receives *anything* — including plain strings and numbers."
        ),
        python_code=(
            "try:\n"
            "    risky()\n"
            "except Exception as e:\n"
            "    print(type(e).__name__)  # always a class name"
        ),
        js_code=(
            "try {\n"
            "  risky();\n"
            "} catch (e) {\n"
            "  console.log(e.message);  // TRAP: e might not have .message\n"
            "}"
        ),
        consequence=(
            "If e is a string or number, accessing .message yields undefined. "
            "Code that throws `throw 'oops'` or `throw 42` is valid JS."
        ),
        fix=(
            "Guard with `if (e instanceof Error) { ... } else { String(e) }` "
            "before accessing any Error-specific properties."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="property_access_silent",
        description=(
            "Accessing a missing attribute in Python raises AttributeError immediately; "
            "in JS the access itself is silent and returns undefined."
        ),
        python_code="obj.missing  # → AttributeError raised here",
        js_code=(
            "const x = obj.missing;  // → undefined, no error\n"
            "x();                    // → TypeError: x is not a function"
        ),
        consequence=(
            "The error site is displaced from the root cause. Stack traces point to "
            "the call site rather than the missing property access."
        ),
        fix=(
            "Use optional chaining: `obj?.missing?.()` or add explicit existence "
            "checks before performing operations on potentially-absent properties."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="array_oob_silent",
        description=(
            "Python list out-of-bounds raises IndexError; JS array out-of-bounds "
            "returns undefined with no error."
        ),
        python_code="lst[100]  # → IndexError: list index out of range",
        js_code="arr[100]  // → undefined",
        consequence=(
            "Logic bugs from wrong indices propagate silently as undefined values, "
            "which then cause misleading TypeErrors or incorrect computations downstream."
        ),
        fix=(
            "Bounds-check explicitly: `if (i < arr.length) arr[i]` or use "
            "`arr.at(i)` combined with a nullish check."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="json_stringify_silent",
        description=(
            "Python json.dumps raises TypeError for non-serialisable values; "
            "JS JSON.stringify silently drops functions, undefined, and Symbol values."
        ),
        python_code="import json\njson.dumps({'fn': lambda x: x})  # → TypeError",
        js_code=(
            "JSON.stringify({ fn: () => {}, val: undefined });\n"
            "// → '{}' — both keys silently omitted"
        ),
        consequence=(
            "Data loss without any exception. API payloads or persisted state can "
            "arrive empty on the receiving end with no indication something was dropped."
        ),
        fix=(
            "Use a replacer function to detect and log dropped keys, or validate "
            "the output length / shape before sending."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="promise_rejection_silent",
        description=(
            "An unhandled rejected Promise in JS is silent unless a global "
            "`unhandledrejection` handler is installed; Python async raises in the loop."
        ),
        python_code=(
            "import asyncio\n"
            "async def bad(): raise RuntimeError('oops')\n"
            "asyncio.run(bad())  # → RuntimeError propagates immediately"
        ),
        js_code=(
            "async function bad() { throw new Error('oops'); }\n"
            "bad();  // TRAP: rejection ignored silently in some environments"
        ),
        consequence=(
            "Errors vanish. In Node <15 this was only a warning; modern Node exits "
            "with code 1, but browser environments still swallow them by default."
        ),
        fix=(
            "Always attach `.catch()` or use `await` inside try/catch. "
            "Install `process.on('unhandledRejection', ...)` in Node for global coverage."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="finally_return_swallows",
        description=(
            "A `return` (or `throw`) inside a `finally` block suppresses any pending "
            "exception — present in both Python and JS but especially dangerous in "
            "async JS where finally blocks appear in more contexts."
        ),
        python_code=(
            "def f():\n"
            "    try:\n"
            "        raise ValueError('original')\n"
            "    finally:\n"
            "        return 42  # suppresses ValueError — Python warns, JS does not"
        ),
        js_code=(
            "function f() {\n"
            "  try { throw new Error('original'); }\n"
            "  finally { return 42; }  // Error silently swallowed\n"
            "}"
        ),
        consequence=(
            "The original exception is lost. Callers see a normal return value and "
            "have no indication an error occurred."
        ),
        fix=(
            "Never use return/throw in finally for control flow. "
            "Reserve finally for cleanup (close handles, reset state) only."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="error_stack_differs",
        description=(
            "Python's traceback module produces a standardised, portable stack string; "
            "JS `err.stack` format varies across engines and is not standardised."
        ),
        python_code=(
            "import traceback\n"
            "try:\n"
            "    risky()\n"
            "except Exception:\n"
            "    tb = traceback.format_exc()  # portable, always the same format"
        ),
        js_code=(
            "try { risky(); } catch (e) {\n"
            "  // V8:   'Error: msg\\n    at fn (file:line:col)'\n"
            "  // SpiderMonkey: 'fn@file:line:col'\n"
            "  // JavaScriptCore: 'fn@file:line:col' (different detail)\n"
            "  console.log(e.stack);\n"
            "}"
        ),
        consequence=(
            "Parsing or logging stack traces in a cross-engine way is brittle. "
            "Source-map tools must handle all three formats."
        ),
        fix=(
            "Use a dedicated error-reporting library (e.g. Sentry, error-stack-parser) "
            "that normalises stack frames across engines."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="custom_error_inheritance",
        description=(
            "Python custom exceptions work uniformly everywhere; JS class-based error "
            "subclasses fail `instanceof` checks across realm boundaries "
            "(iframes, Workers, vm.runInNewContext)."
        ),
        python_code=(
            "class MyError(Exception): pass\n"
            "raise MyError('x')  # isinstance(e, MyError) always True"
        ),
        js_code=(
            "class MyError extends Error {}\n"
            "// In another iframe:\n"
            "// e instanceof MyError  → false (different realm, different prototype chain)"
        ),
        consequence=(
            "Error type checks fail silently in cross-realm scenarios such as "
            "postMessage handlers, sandboxed iframes, or Node vm modules."
        ),
        fix=(
            "Add a `name` property to custom errors and check `e.name === 'MyError'` "
            "instead of `instanceof` when cross-realm behaviour is possible."
        ),
    ),
    ErrorHandlingTrap(
        trap_id="async_foreach_swallows",
        description=(
            "Using `async` callbacks inside Array.forEach does not propagate "
            "rejections to the outer async function — a JS-only trap with no Python "
            "parallel."
        ),
        python_code=(
            "# Python equivalent would be explicit loop — no hidden trap\n"
            "for item in items:\n"
            "    await process(item)  # errors propagate normally"
        ),
        js_code=(
            "// TRAP:\n"
            "items.forEach(async (item) => {\n"
            "  await process(item);  // rejection silently ignored\n"
            "});\n"
            "// No await on forEach return value — it's always undefined"
        ),
        consequence=(
            "Async errors inside forEach are unhandled Promise rejections. "
            "The outer function continues as if nothing went wrong."
        ),
        fix=(
            "Use `for...of` with await, or `await Promise.all(items.map(async ...))`,"
            " which propagates the first rejection to the caller."
        ),
    ),
]


# ---------------------------------------------------------------------------
# 6. SafeErrorPatterns
# ---------------------------------------------------------------------------

@dataclass
class SafeErrorPatterns:
    """Returns safe JS code strings that avoid common error-handling traps."""

    def safe_catch_block(self, error_var: str = "err") -> str:
        return (
            f"// [js_error_handling: check instanceof Error before using .message]\n"
            f"if ({error_var} instanceof Error) {{\n"
            f"  console.error({error_var}.message);\n"
            f"}} else {{\n"
            f"  console.error('Unknown error:', String({error_var}));\n"
            f"}}"
        )

    def safe_property_access(self, obj: str, prop: str) -> str:
        return (
            f"// [js_error_handling: optional chaining avoids silent undefined trap]\n"
            f"{obj}?.{prop} ?? defaultValue"
        )

    def safe_json_stringify(self, value: str) -> str:
        return (
            f"// [js_error_handling: JSON.stringify can throw for circular refs]\n"
            f"try {{\n"
            f"  return JSON.stringify({value});\n"
            f"}} catch {{\n"
            f"  return null;\n"
            f"}}"
        )

    def safe_fetch_with_error(self, url: str, options: str = "{}") -> str:
        return (
            f"// [js_error_handling: fetch only rejects on network failure;\n"
            f"//  HTTP errors (4xx/5xx) require an explicit response.ok check]\n"
            f"async function safeFetch(url, options) {{\n"
            f"  try {{\n"
            f"    const response = await fetch({url!r}, {options});\n"
            f"    if (!response.ok) {{\n"
            f"      throw new Error(\n"
            f"        `HTTP error: ${{response.status}} ${{response.statusText}}`\n"
            f"      );\n"
            f"    }}\n"
            f"    return await response.json();\n"
            f"  }} catch (err) {{\n"
            f"    if (err instanceof Error) {{\n"
            f"      console.error('Fetch failed:', err.message);\n"
            f"    }} else {{\n"
            f"      console.error('Fetch failed with unknown error:', String(err));\n"
            f"    }}\n"
            f"    return null;\n"
            f"  }}\n"
            f"}}"
        )


# ---------------------------------------------------------------------------
# 7. ErrorCodeAnalyzer
# ---------------------------------------------------------------------------

class ErrorCodeAnalyzer:
    """Static analysis helpers that detect common JS error-handling gaps."""

    # Matches `word.word(` or `word.word[` without a preceding `?` (optional chain)
    _BARE_ACCESS_RE = re.compile(r"(?<!\?)(?<!\w\?)(\b\w+)\.(\w+)(?=[\(\[])")

    # Matches JSON.parse( not preceded by a `try` in the prior 3 lines
    _JSON_PARSE_RE = re.compile(r"JSON\.parse\(")

    def detect_bare_property_access(
        self, js_code: str
    ) -> list[tuple[int, str]]:
        """
        Return (line_number, line_text) pairs where a method call or
        property+index chain is made without optional chaining (?.).

        Heuristic: `word.word(` or `word.word[` without a preceding `?.`.
        False positives are possible; this is a lint-style hint only.
        """
        results: list[tuple[int, str]] = []
        for lineno, line in enumerate(js_code.splitlines(), start=1):
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if self._BARE_ACCESS_RE.search(line):
                results.append((lineno, line.rstrip()))
        return results

    def detect_unchecked_json_parse(
        self, js_code: str
    ) -> list[tuple[int, str]]:
        """
        Return (line_number, line_text) pairs where JSON.parse( appears
        without a `try` in the preceding three lines — a rough heuristic
        for unchecked parse calls.
        """
        results: list[tuple[int, str]] = []
        lines = js_code.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if not self._JSON_PARSE_RE.search(line):
                continue
            # Look back up to 3 lines for a `try` keyword
            window_start = max(0, lineno - 4)  # lineno is 1-based
            window = lines[window_start : lineno - 1]
            if not any("try" in prev_line for prev_line in window):
                results.append((lineno, line.rstrip()))
        return results
