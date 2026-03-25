from __future__ import annotations

"""JavaScript scoping rules modelled as a formal gap relative to Python's LEGB rule."""

__all__ = [
    "ScopeKind",
    "DeclarationKind",
    "ScopingTrap",
    "SCOPING_TRAPS",
    "PythonScopeComparison",
    "PYTHON_SCOPE_COMPARISON",
    "ScopingAnalyzer",
]

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# 1. ScopeKind
# ---------------------------------------------------------------------------


class ScopeKind(str, Enum):
    GLOBAL = "global"
    FUNCTION = "function"
    BLOCK = "block"
    MODULE = "module"
    EVAL = "eval"

    def is_hoisted(self) -> bool:
        """Return True for scope kinds that receive hoisted var/function declarations."""
        return self in (ScopeKind.FUNCTION, ScopeKind.GLOBAL)


# ---------------------------------------------------------------------------
# 2. DeclarationKind
# ---------------------------------------------------------------------------


class DeclarationKind(str, Enum):
    VAR = "var"
    LET = "let"
    CONST = "const"
    FUNCTION_DECL = "function_decl"
    CLASS_DECL = "class_decl"
    IMPORT = "import"

    def has_temporal_dead_zone(self) -> bool:
        """Return True if the declaration exhibits a Temporal Dead Zone (TDZ)."""
        return self in (DeclarationKind.LET, DeclarationKind.CONST, DeclarationKind.CLASS_DECL)

    def is_reassignable(self) -> bool:
        """Return True if the binding can be rebound to a different value."""
        return self in (DeclarationKind.VAR, DeclarationKind.LET)

    def hoists_to(self) -> ScopeKind:
        """Return the scope to which this declaration is hoisted."""
        if self in (DeclarationKind.VAR, DeclarationKind.FUNCTION_DECL):
            return ScopeKind.FUNCTION
        return ScopeKind.BLOCK


# ---------------------------------------------------------------------------
# 3. ScopingTrap dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopingTrap:
    trap_id: str
    name: str
    python_analogy: str
    js_behavior: str
    example_code: str
    consequence: str
    fix: str


# ---------------------------------------------------------------------------
# 4. SCOPING_TRAPS
# ---------------------------------------------------------------------------


SCOPING_TRAPS: list[ScopingTrap] = [
    ScopingTrap(
        trap_id="var_hoisting",
        name="var hoisting — undefined before assignment",
        python_analogy=(
            "Python raises UnboundLocalError if you read a local variable before the "
            "assignment that creates it; JS silently gives undefined."
        ),
        js_behavior=(
            "var declarations are moved (hoisted) to the top of the enclosing function "
            "scope.  Only the declaration is hoisted, not the initialiser, so reading the "
            "variable before the assignment expression evaluates yields undefined, not a "
            "ReferenceError."
        ),
        example_code=(
            "function f() {\n"
            "  console.log(x); // undefined, not ReferenceError\n"
            "  var x = 42;\n"
            "  console.log(x); // 42\n"
            "}"
        ),
        consequence=(
            "Silent logic errors: code that looks like it reads an unset variable is "
            "actually reading a hoisted-but-uninitialised one."
        ),
        fix="Use let or const; enable 'use strict' and a linter (ESLint no-use-before-define).",
    ),
    ScopingTrap(
        trap_id="var_in_loop",
        name="var in loop — shared loop variable across closures",
        python_analogy=(
            "Python's loop variable is also shared across closures created in the loop "
            "(same late-binding issue), but var additionally leaks out of the block entirely."
        ),
        js_behavior=(
            "var has function scope, not block scope.  A single binding is shared by "
            "every closure created in the loop body, so all callbacks capture the same "
            "reference and observe the final value of the loop variable."
        ),
        example_code=(
            "for (var i = 0; i < 3; i++) {\n"
            "  setTimeout(() => console.log(i), 0);\n"
            "}\n"
            "// prints: 3  3  3 — not 0  1  2"
        ),
        consequence=(
            "Async callbacks or event handlers all see the post-loop value, causing "
            "hard-to-diagnose bugs in UI code and promise chains."
        ),
        fix=(
            "Replace var with let (creates a fresh binding per iteration) or wrap the "
            "callback in an IIFE that captures the current value."
        ),
    ),
    ScopingTrap(
        trap_id="let_tdz",
        name="let / const Temporal Dead Zone (TDZ)",
        python_analogy=(
            "Python raises UnboundLocalError for the same pattern; JS raises "
            "ReferenceError with the explicit message 'Cannot access X before initialization'."
        ),
        js_behavior=(
            "let and const bindings exist in scope from the start of the block, but are "
            "in a 'Temporal Dead Zone' until the declaration is evaluated.  Any read or "
            "write before that point throws a ReferenceError, even though the name is "
            "technically in scope."
        ),
        example_code=(
            "{\n"
            "  console.log(x); // ReferenceError: Cannot access 'x' before initialization\n"
            "  let x = 10;\n"
            "}"
        ),
        consequence=(
            "typeof — normally safe for undeclared variables — also throws inside the TDZ, "
            "surprising developers who use typeof as an existence check."
        ),
        fix="Always declare let/const at the top of the block before first use.",
    ),
    ScopingTrap(
        trap_id="function_hoisting",
        name="function declaration hoisted in full vs. var-assigned function",
        python_analogy=(
            "Python does not hoist function definitions; a def statement is executed at "
            "the point it appears."
        ),
        js_behavior=(
            "A function declaration (function f() {}) is hoisted with its entire body, "
            "making it callable anywhere in the enclosing function/global scope.  A "
            "function expression assigned to var (var f = function() {}) only hoists the "
            "var f binding (as undefined), so calling f() before the assignment throws "
            "TypeError: f is not a function."
        ),
        example_code=(
            "hello(); // works — 'Hello!'\n"
            "function hello() { console.log('Hello!'); }\n\n"
            "world(); // TypeError: world is not a function\n"
            "var world = function() { console.log('World!'); };"
        ),
        consequence=(
            "Inconsistent behaviour depending on whether the developer used a declaration "
            "or an expression, causing confusing runtime errors."
        ),
        fix=(
            "Prefer const f = () => {} (arrow function expression with const) to make "
            "call-before-define a clear error."
        ),
    ),
    ScopingTrap(
        trap_id="global_leak",
        name="undeclared assignment creates implicit global",
        python_analogy=(
            "Python requires an explicit 'global x' statement inside a function before "
            "assigning to a global; unqualified assignment always creates a local."
        ),
        js_behavior=(
            "In sloppy mode (no 'use strict'), assigning to an undeclared identifier "
            "silently creates a property on the global object (window/globalThis).  This "
            "pollutes the global namespace and can cause cross-module state sharing."
        ),
        example_code=(
            "function f() {\n"
            "  leaked = 99; // no var/let/const — sloppy mode creates a global\n"
            "}\n"
            "f();\n"
            "console.log(window.leaked); // 99"
        ),
        consequence=(
            "Hard-to-track state contamination, especially in large codebases or when "
            "multiple scripts share the same global scope."
        ),
        fix=(
            "Always use 'use strict'; at the top of files/functions.  Use a module "
            "system (ESM/CommonJS) — module scope is strict by default."
        ),
    ),
    ScopingTrap(
        trap_id="closure_shared_mutable",
        name="closures share the same live binding (intended but surprising)",
        python_analogy=(
            "Python closures also capture by reference (the cell object), so "
            "mutating the variable is visible to all closures — same semantics."
        ),
        js_behavior=(
            "All closures over a let/const/var variable share the same live binding.  "
            "Mutating the variable after creating a closure changes what every closure "
            "sees.  This is correct and by design but surprises developers expecting "
            "value capture."
        ),
        example_code=(
            "let count = 0;\n"
            "const inc = () => ++count;\n"
            "const get = () => count;\n"
            "inc(); inc();\n"
            "console.log(get()); // 2 — shared binding"
        ),
        consequence=(
            "Closures intended to snapshot a value actually track mutations.  For loops "
            "with var this combines with hoisting to produce the classic 3-3-3 bug."
        ),
        fix=(
            "Capture the current value explicitly: const snap = count; "
            "or use let inside the block (per-iteration binding)."
        ),
    ),
    ScopingTrap(
        trap_id="block_scope_catch",
        name="catch clause creates its own block scope for the error binding",
        python_analogy=(
            "Python 3 deletes the exception variable from the enclosing scope after the "
            "except block; JS simply limits it to the catch block without deleting outer "
            "variables of the same name."
        ),
        js_behavior=(
            "The identifier declared in catch(e) is scoped to the catch block only.  "
            "An outer variable with the same name is temporarily shadowed inside the "
            "block but restored afterwards.  Accessing e outside the catch block throws "
            "ReferenceError (or resolves to an outer e if one exists)."
        ),
        example_code=(
            "let e = 'outer';\n"
            "try {\n"
            "  throw new Error('boom');\n"
            "} catch (e) {\n"
            "  console.log(e.message); // 'boom'\n"
            "}\n"
            "console.log(e); // 'outer' — catch binding gone, outer e restored"
        ),
        consequence=(
            "Developers sometimes try to use the caught error object after the catch "
            "block and get undefined or the wrong value."
        ),
        fix="Extract needed information from the error inside the catch block.",
    ),
    ScopingTrap(
        trap_id="class_not_hoisted",
        name="class declarations have TDZ — not hoisted like function declarations",
        python_analogy=(
            "Python class definitions are executed statements; using a class before its "
            "definition raises NameError, same as JS's ReferenceError here."
        ),
        js_behavior=(
            "Despite looking like a declaration, a class statement is NOT fully hoisted. "
            "The binding is in TDZ from the start of the block until the class statement "
            "is evaluated, matching let/const semantics, not function declaration semantics."
        ),
        example_code=(
            "const obj = new Animal(); // ReferenceError: Cannot access 'Animal' before initialization\n"
            "class Animal {\n"
            "  constructor() { this.legs = 4; }\n"
            "}"
        ),
        consequence=(
            "Developers who rely on function-declaration hoisting and apply the same "
            "pattern to classes get a confusing ReferenceError."
        ),
        fix="Always place class definitions before their first use.",
    ),
    ScopingTrap(
        trap_id="const_object_mutable",
        name="const prevents rebinding but not mutation of objects/arrays",
        python_analogy=(
            "Python's tuple is deeply immutable in the sense that you cannot replace "
            "elements; const is more like a final reference — the object it points to "
            "can still be mutated."
        ),
        js_behavior=(
            "const creates an immutable binding — the variable cannot be reassigned to a "
            "different value.  However, if the value is an object or array, its properties "
            "and elements can still be mutated freely.  Object.freeze() is needed for "
            "shallow immutability."
        ),
        example_code=(
            "const obj = { x: 1 };\n"
            "obj.x = 99;        // valid — mutation, not rebinding\n"
            "obj.y = 100;       // valid\n"
            "obj = {};          // TypeError: Assignment to constant variable"
        ),
        consequence=(
            "Developers assume const guarantees immutability and are surprised when "
            "function callers mutate the object they passed in."
        ),
        fix=(
            "Use Object.freeze(obj) for shallow immutability; for deep immutability use "
            "a library (immer, immutable-js) or structuredClone + freeze recursively."
        ),
    ),
    ScopingTrap(
        trap_id="module_scope_singleton",
        name="ES module scope is a singleton — re-import gives the same instance",
        python_analogy=(
            "Python module imports are also cached in sys.modules; re-importing returns "
            "the same module object — identical semantics."
        ),
        js_behavior=(
            "The ES module system evaluates each module only once regardless of how many "
            "times it is imported.  All importers share the same live bindings exported "
            "from the module.  This makes module-level variables effectively singletons."
        ),
        example_code=(
            "// counter.js\n"
            "export let count = 0;\n"
            "export const inc = () => ++count;\n\n"
            "// a.js\n"
            "import { inc, count } from './counter.js';\n"
            "inc(); // count is now 1 everywhere\n\n"
            "// b.js\n"
            "import { count } from './counter.js';\n"
            "console.log(count); // 1 — same module instance"
        ),
        consequence=(
            "Mutable module-level state is shared across the entire application, which "
            "can cause surprising action-at-a-distance bugs and makes unit testing harder."
        ),
        fix=(
            "Prefer exporting factory functions or classes rather than mutable module-level "
            "state.  Reset state explicitly in test teardown."
        ),
    ),
    ScopingTrap(
        trap_id="with_statement_scope",
        name="with statement injects an object's properties into the scope chain",
        python_analogy=(
            "Python's 'with' statement is a context manager protocol — it has no effect "
            "on the name-lookup scope chain."
        ),
        js_behavior=(
            "The with statement prepends an object to the scope chain, so unqualified "
            "identifiers are first looked up as properties of that object.  This makes "
            "static analysis impossible and is forbidden in strict mode."
        ),
        example_code=(
            "const obj = { x: 10, y: 20 };\n"
            "with (obj) {\n"
            "  console.log(x + y); // 30 — x and y resolved from obj\n"
            "}"
        ),
        consequence=(
            "Variable resolution becomes dynamic and unpredictable; optimising engines "
            "must pessimise all scope lookups inside the block.  Forbidden in strict mode."
        ),
        fix="Never use with.  Use destructuring assignment instead: const { x, y } = obj.",
    ),
    ScopingTrap(
        trap_id="eval_scope",
        name="eval in sloppy mode can introduce new bindings into the calling scope",
        python_analogy=(
            "Python's eval() with a default globals/locals dict does not permanently "
            "inject names into the calling function's local scope."
        ),
        js_behavior=(
            "Direct eval() in sloppy mode can declare new variables via var that become "
            "visible in the enclosing function scope after eval returns.  Strict mode "
            "eval has its own scope and cannot leak bindings."
        ),
        example_code=(
            "function f() {\n"
            "  eval('var secret = 42;');\n"
            "  console.log(secret); // 42 — sloppy mode only\n"
            "}"
        ),
        consequence=(
            "Dynamic code injection can pollute function scopes in unpredictable ways; "
            "also a security vector if user-supplied strings are eval'd."
        ),
        fix=(
            "Use 'use strict'; — strict eval has its own scope.  Avoid eval entirely; "
            "use JSON.parse, Function constructor, or dynamic import for legitimate needs."
        ),
    ),
]


# ---------------------------------------------------------------------------
# 5. PythonScopeComparison
# ---------------------------------------------------------------------------


@dataclass
class PythonScopeComparison:
    py_rule: str
    js_rule: str
    key_differences: list[str]


PYTHON_SCOPE_COMPARISON = PythonScopeComparison(
    py_rule="LEGB: Local, Enclosing, Global, Builtin",
    js_rule=(
        "Scope chain: block → function → module → global; "
        "prototype chain is separate and only applies to property lookup, not variable resolution"
    ),
    key_differences=[
        (
            "Block scope: JS let/const are block-scoped; Python has no block scope — "
            "if/for/while bodies share the enclosing function's local scope."
        ),
        (
            "Hoisting: JS var declarations are hoisted to function scope with value "
            "undefined; Python names are simply absent (UnboundLocalError) until assigned."
        ),
        (
            "Temporal Dead Zone: JS let/const/class have a TDZ where access throws "
            "ReferenceError; Python raises UnboundLocalError for a similar pattern but "
            "there is no formal TDZ concept."
        ),
        (
            "Global mutation: JS sloppy mode allows implicit global creation via bare "
            "assignment; Python requires an explicit 'global x' declaration in the "
            "function before a bare assignment reaches the module global."
        ),
        (
            "Function hoisting: JS function declarations are fully hoisted (body included); "
            "Python def statements execute in order and are not hoisted."
        ),
        (
            "Enclosing scope mutation: Python requires 'nonlocal x' to rebind a variable "
            "in an enclosing (non-global) scope; JS closures can rebind enclosing let/var "
            "without any special declaration."
        ),
        (
            "Module scope: Both Python and JS treat module scope as a singleton cached "
            "after first import/evaluation.  In JS, exported bindings are live references; "
            "in Python, module attributes are ordinary object attributes."
        ),
        (
            "eval scope: JS sloppy eval can inject new var bindings into the calling "
            "function's scope; Python eval does not permanently modify the calling "
            "frame's locals."
        ),
    ],
)


# ---------------------------------------------------------------------------
# 6. ScopingAnalyzer
# ---------------------------------------------------------------------------


class ScopingAnalyzer:
    """Static-analysis helpers that detect common JS scoping traps in source code."""

    # Patterns used across methods
    _RE_FOR_LINE = re.compile(r"\bfor\s*\(")
    _RE_WHILE_LINE = re.compile(r"\bwhile\s*\(")
    _RE_VAR_DECL = re.compile(r"\bvar\s+([A-Za-z_$][\w$]*)")
    _RE_LOOP_OPEN = re.compile(r"\b(?:for|while)\s*\(")
    _RE_BLOCK_CLOSE = re.compile(r"\}")
    _RE_IMPLICIT_ASSIGN = re.compile(
        r"(?<![=!<>])(?<!\+\+)(?<!--)(?<![+\-*/&|^~!])(?<![A-Za-z_$\w]\.)"
        r"\b([A-Za-z_$][\w$]*)\s*="
        r"(?!=)"
    )
    _RE_DECLARATION = re.compile(
        r"\b(?:var|let|const|function|class|import)\s+([A-Za-z_$][\w$]*)"
    )
    _RE_LET_CONST_DECL = re.compile(r"\b(?:let|const)\s+([A-Za-z_$][\w$]*)")
    _RE_IDENTIFIER_USE = re.compile(r"\b([A-Za-z_$][\w$]*)\b")

    # Keywords that look like identifiers but are not user variables
    _JS_KEYWORDS: frozenset[str] = frozenset(
        {
            "break", "case", "catch", "class", "const", "continue", "debugger",
            "default", "delete", "do", "else", "export", "extends", "finally",
            "for", "function", "if", "import", "in", "instanceof", "let", "new",
            "of", "return", "static", "super", "switch", "this", "throw", "try",
            "typeof", "var", "void", "while", "with", "yield", "async", "await",
            "null", "undefined", "true", "false", "NaN", "Infinity",
            "console", "window", "document", "globalThis", "process",
            "Object", "Array", "Function", "Number", "String", "Boolean",
            "Symbol", "BigInt", "Math", "JSON", "Promise", "Error",
            "setTimeout", "setInterval", "clearTimeout", "clearInterval",
            "parseInt", "parseFloat", "isNaN", "isFinite",
            "arguments", "eval", "prototype", "constructor",
        }
    )

    # ------------------------------------------------------------------
    # detect_var_in_loop
    # ------------------------------------------------------------------

    def detect_var_in_loop(self, js_code: str) -> list[int]:
        """Return 1-based line numbers of ``var`` declarations inside for/while loops.

        The implementation tracks brace depth to determine when we are inside a
        loop body.  It handles simple single-statement loops (no braces) by
        treating the immediately following statement as the loop body.
        """
        lines = js_code.splitlines()
        result: list[int] = []

        # Stack of (depth_at_loop_open).  We push when we enter a loop header
        # and pop when the matching closing brace is found.
        loop_depths: list[int] = []
        brace_depth: int = 0
        inside_loop: bool = False

        # We do a line-by-line scan accumulating brace depth.
        for lineno, line in enumerate(lines, start=1):
            # Check for a loop header on this line.
            loop_match = self._RE_LOOP_OPEN.search(line)

            # Count braces on this line to update depth.
            open_braces = line.count("{")
            close_braces = line.count("}")

            # If a loop starts on this line, record the depth *before* any
            # opening braces on the same line so we know when we have exited
            # the loop body.
            if loop_match:
                loop_depths.append(brace_depth + open_braces)

            brace_depth += open_braces - close_braces

            # Pop finished loops.
            while loop_depths and brace_depth < loop_depths[-1]:
                loop_depths.pop()

            inside_loop = len(loop_depths) > 0

            if inside_loop and self._RE_VAR_DECL.search(line):
                result.append(lineno)

        return result

    # ------------------------------------------------------------------
    # detect_global_leak
    # ------------------------------------------------------------------

    def detect_global_leak(self, js_code: str) -> list[str]:
        """Return variable names assigned without ``var``/``let``/``const`` in sloppy mode.

        Heuristic: find bare ``name =`` patterns that are not preceded by a
        declaration keyword on the same line and whose name has not been declared
        anywhere in the file.
        """
        lines = js_code.splitlines()

        # Collect all explicitly declared names in the file.
        declared: set[str] = set()
        for line in lines:
            for m in self._RE_DECLARATION.finditer(line):
                declared.add(m.group(1))

        leaks: list[str] = []
        seen: set[str] = set()

        for line in lines:
            stripped = line.strip()
            # Skip lines that are declarations themselves.
            if self._RE_DECLARATION.search(stripped):
                continue
            # Skip comments (simple heuristic).
            if stripped.startswith("//") or stripped.startswith("*"):
                continue

            for m in self._RE_IMPLICIT_ASSIGN.finditer(stripped):
                name = m.group(1)
                if (
                    name not in self._JS_KEYWORDS
                    and name not in declared
                    and name not in seen
                    # Ignore property access (obj.prop = ...) — already excluded
                    # by negative lookbehind in the regex, but double-check.
                    and not re.search(r"\." + re.escape(name) + r"\s*=", stripped)
                ):
                    leaks.append(name)
                    seen.add(name)

        return leaks

    # ------------------------------------------------------------------
    # detect_tdz_risk
    # ------------------------------------------------------------------

    def detect_tdz_risk(self, js_code: str) -> list[str]:
        """Return ``let``/``const`` variable names referenced before their declaration.

        Works by tracking the first line on which each ``let``/``const`` name is
        declared, then scanning for identifier uses on earlier lines.
        """
        lines = js_code.splitlines()

        # Map name → 1-based line number of its let/const declaration.
        decl_line: dict[str, int] = {}
        for lineno, line in enumerate(lines, start=1):
            for m in self._RE_LET_CONST_DECL.finditer(line):
                name = m.group(1)
                if name not in decl_line:
                    decl_line[name] = lineno

        if not decl_line:
            return []

        at_risk: list[str] = []
        seen: set[str] = set()

        for lineno, line in enumerate(lines, start=1):
            # Skip comments.
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue

            for m in self._RE_IDENTIFIER_USE.finditer(line):
                name = m.group(1)
                if (
                    name in decl_line
                    and lineno < decl_line[name]
                    and name not in seen
                    and name not in self._JS_KEYWORDS
                ):
                    at_risk.append(name)
                    seen.add(name)

        return at_risk
