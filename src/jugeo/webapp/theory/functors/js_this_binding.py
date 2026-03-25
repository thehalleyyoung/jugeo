"""Model JavaScript's ``this`` binding as a formal gap relative to Python's ``self``."""
from __future__ import annotations

__all__ = [
    "ThisBindingRule",
    "ThisBindingAnalysis",
    "THIS_BINDING_ANALYSES",
    "PythonToJSMethodTranspiler",
    "ClassTranspilationGuide",
]

import re
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# 1. ThisBindingRule
# ---------------------------------------------------------------------------

class ThisBindingRule(str, Enum):
    """The five mechanisms by which JavaScript resolves ``this``."""

    DEFAULT  = "DEFAULT"
    IMPLICIT = "IMPLICIT"
    EXPLICIT = "EXPLICIT"
    NEW      = "NEW"
    ARROW    = "ARROW"

    @classmethod
    def description(cls, rule: ThisBindingRule) -> str:
        """Return a one-sentence explanation for *rule*."""
        _descriptions: dict[ThisBindingRule, str] = {
            cls.DEFAULT:  (
                "Sloppy mode: global object (window/global); strict mode: undefined"
            ),
            cls.IMPLICIT: (
                "The object left of the dot at call site: obj.method() → this=obj"
            ),
            cls.EXPLICIT: (
                "call()/apply()/bind() set this explicitly"
            ),
            cls.NEW: (
                "new Foo() sets this to a fresh object that inherits from Foo.prototype"
            ),
            cls.ARROW: (
                "Lexically captures enclosing this at definition, ignores call site"
            ),
        }
        return _descriptions[rule]


# ---------------------------------------------------------------------------
# 2. ThisBindingAnalysis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ThisBindingAnalysis:
    """A single JS ``this``-binding scenario with its Python mapping."""

    code_pattern: str
    """Representative JS code pattern, e.g. ``obj.method()``."""

    rule: ThisBindingRule
    """Which binding rule applies."""

    this_value: str
    """What ``this`` resolves to at runtime."""

    python_equivalent: str
    """The corresponding Python idiom."""

    is_trap: bool
    """True when JS behaviour surprises Python programmers."""

    safe_fix: str | None
    """How to avoid the trap, or ``None`` when there is no trap."""


# ---------------------------------------------------------------------------
# 3. THIS_BINDING_ANALYSES
# ---------------------------------------------------------------------------

THIS_BINDING_ANALYSES: list[ThisBindingAnalysis] = [
    # ------------------------------------------------------------------
    # 1. Normal dot-call — safe
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="obj.method()",
        rule=ThisBindingRule.IMPLICIT,
        this_value="obj",
        python_equivalent="obj.method()  # self=obj automatically",
        is_trap=False,
        safe_fix=None,
    ),

    # ------------------------------------------------------------------
    # 2. Detached reference — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="const f = obj.method; f()",
        rule=ThisBindingRule.DEFAULT,
        this_value="undefined (strict) / global (sloppy)",
        python_equivalent="f = obj.method; f()  # Python binds self, JS does NOT",
        is_trap=True,
        safe_fix="const f = obj.method.bind(obj);",
    ),

    # ------------------------------------------------------------------
    # 3. setTimeout callback — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="setTimeout(obj.method, 1000)",
        rule=ThisBindingRule.DEFAULT,
        this_value="undefined (strict) / global (sloppy)",
        python_equivalent="threading.Timer(1, obj.method).start()  # Python keeps self",
        is_trap=True,
        safe_fix="setTimeout(() => obj.method(), 1000)  // or .bind(obj)",
    ),

    # ------------------------------------------------------------------
    # 4. forEach callback — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="[1,2,3].forEach(obj.method)",
        rule=ThisBindingRule.DEFAULT,
        this_value="undefined (strict) / global (sloppy)",
        python_equivalent="list(map(obj.method, [1, 2, 3]))  # Python keeps self",
        is_trap=True,
        safe_fix="[1,2,3].forEach(x => obj.method(x))  // or .forEach(obj.method.bind(obj))",
    ),

    # ------------------------------------------------------------------
    # 5. Class instance method called normally — safe
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="class C { method() { return this; } }; new C().method()",
        rule=ThisBindingRule.IMPLICIT,
        this_value="the C instance",
        python_equivalent="class C:\n    def method(self): return self\nC().method()",
        is_trap=False,
        safe_fix=None,
    ),

    # ------------------------------------------------------------------
    # 6. Class field arrow method — always safe
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="class C { method = () => this; }",
        rule=ThisBindingRule.ARROW,
        this_value="the C instance (always, regardless of call site)",
        python_equivalent="# Python bound-method is always tied to the instance",
        is_trap=False,
        safe_fix=None,
    ),

    # ------------------------------------------------------------------
    # 7. Constructor called with new — safe
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="function Foo() { this.x = 1; }; new Foo()",
        rule=ThisBindingRule.NEW,
        this_value="fresh object inheriting from Foo.prototype",
        python_equivalent="class Foo:\n    def __init__(self): self.x = 1\nFoo()",
        is_trap=False,
        safe_fix=None,
    ),

    # ------------------------------------------------------------------
    # 8. Constructor called WITHOUT new — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="function Foo() { this.x = 1; }; Foo()  // no new",
        rule=ThisBindingRule.DEFAULT,
        this_value="global (sloppy) — sets window.x = 1",
        python_equivalent="# Python __init__ always requires an instance; no analogue",
        is_trap=True,
        safe_fix="Use class syntax: class Foo { constructor() { this.x = 1; } }",
    ),

    # ------------------------------------------------------------------
    # 9. Object-literal arrow method at module top level — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="const obj = { method: () => this };  // top-level module",
        rule=ThisBindingRule.ARROW,
        this_value="module-level this (undefined in strict / global in sloppy)",
        python_equivalent="# No direct equivalent; Python lambdas don't capture self",
        is_trap=True,
        safe_fix="Use a regular function: const obj = { method() { return this; } };",
    ),

    # ------------------------------------------------------------------
    # 10. Explicit binding via .call() — safe / intentional
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="obj.method.call(other)",
        rule=ThisBindingRule.EXPLICIT,
        this_value="other",
        python_equivalent="Foo.method(other)  # unbound call passing explicit self",
        is_trap=False,
        safe_fix=None,
    ),

    # ------------------------------------------------------------------
    # 11. super.method() inside subclass — safe
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="class Sub extends Base { method() { super.method(); } }",
        rule=ThisBindingRule.IMPLICIT,
        this_value="the Sub instance (this is forwarded through super)",
        python_equivalent="class Sub(Base):\n    def method(self): super().method()",
        is_trap=False,
        safe_fix=None,
    ),

    # ------------------------------------------------------------------
    # 12. bind(null) — subtle strict-mode behaviour
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="const bound = obj.method.bind(null); bound()",
        rule=ThisBindingRule.EXPLICIT,
        this_value="null → treated as undefined in strict mode",
        python_equivalent="# No equivalent; Python methods always have an instance",
        is_trap=True,
        safe_fix="Pass the actual receiver: obj.method.bind(obj)",
    ),

    # ------------------------------------------------------------------
    # 13. Event handler assigned as property — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="button.onclick = obj.handleClick",
        rule=ThisBindingRule.IMPLICIT,
        this_value="button element (not obj) — handler called as button.onclick()",
        python_equivalent="# Python GUI: widget.command = obj.handle  (still bound)",
        is_trap=True,
        safe_fix="button.onclick = () => obj.handleClick();",
    ),

    # ------------------------------------------------------------------
    # 14. Promise .then callback — TRAP
    # ------------------------------------------------------------------
    ThisBindingAnalysis(
        code_pattern="promise.then(obj.onSuccess)",
        rule=ThisBindingRule.DEFAULT,
        this_value="undefined (strict) — .then runs in non-method context",
        python_equivalent="future.add_done_callback(obj.on_success)  # Python keeps self",
        is_trap=True,
        safe_fix="promise.then(result => obj.onSuccess(result));",
    ),
]


# ---------------------------------------------------------------------------
# 4. PythonToJSMethodTranspiler
# ---------------------------------------------------------------------------

class PythonToJSMethodTranspiler:
    """Heuristic transpiler for single Python instance methods → JS method bodies."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transpile_instance_method(self, py_code: str, class_name: str) -> str:  # noqa: ARG002
        """Return a JS method string corresponding to *py_code*.

        Transformations applied:
        - ``def method(self, ...)`` → ``method(...) {``
        - ``self.attr`` → ``this.attr``
        - ``self.method(...)`` → ``this.method(...)``
        - Appends a warning comment when the method is assigned to a variable.
        """
        lines = py_code.splitlines(keepends=True)
        js_lines: list[str] = []
        warned = False

        for line in lines:
            js_line = self._transform_line(line)
            js_lines.append(js_line)

        result = "".join(js_lines)

        # Check for variable assignment pattern that would cause context loss.
        if re.search(r"\bconst\s+\w+\s*=\s*this\.\w+", result) and not warned:
            result += (
                "\n// ⚠ WARNING: Assigning a method to a variable loses `this`."
                " Use .bind(this) or an arrow wrapper.\n"
            )
        return result

    def is_callback_trap(self, py_code: str) -> bool:
        """Return True if *py_code* passes a bound method as a callback argument.

        Detects patterns like ``callback=self.handle`` or ``on_event=self.on_event``.
        """
        return bool(
            re.search(r"\b\w+\s*=\s*self\.\w+", py_code)
            or re.search(r"\(self\.\w+\)", py_code)
            or re.search(r",\s*self\.\w+[,)]", py_code)
        )

    def fix_callback_trap(self, py_code: str) -> str:
        """Replace ``callback=self.handle`` with the arrow-function JS equivalent.

        Returns the patched string with JS-style arrow wrappers.
        """
        def _replace_keyword_cb(match: re.Match) -> str:
            key = match.group("key")
            attr = match.group("attr")
            return f"{key}: () => this.{attr}()"

        def _replace_positional_cb(match: re.Match) -> str:
            attr = match.group("attr")
            return f"() => this.{attr}()"

        result = re.sub(
            r"(?P<key>\w+)\s*=\s*self\.(?P<attr>\w+)",
            _replace_keyword_cb,
            py_code,
        )
        result = re.sub(
            r"\bself\.(?P<attr>\w+)\b(?!\s*\()",
            _replace_positional_cb,
            result,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _transform_line(line: str) -> str:
        """Apply line-level Python→JS transformations."""
        # def method(self) → method() {
        # def method(self, a, b) → method(a, b) {
        line = re.sub(
            r"def\s+(\w+)\s*\(\s*self\s*(?:,\s*)?(.*?)\)\s*:",
            lambda m: (
                f"{m.group(1)}({m.group(2)}) {{"
            ),
            line,
        )
        # self.attr / self.method(...) → this.attr / this.method(...)
        line = re.sub(r"\bself\.", "this.", line)
        # return x → return x;  (simple heuristic, avoid double semicolons)
        if re.match(r"\s*return\b", line) and not line.rstrip().endswith(";"):
            line = line.rstrip("\n") + ";\n"
        return line


# ---------------------------------------------------------------------------
# 5. ClassTranspilationGuide
# ---------------------------------------------------------------------------

class ClassTranspilationGuide:
    """Produce ordered transformation steps for a Python class → JS class."""

    def python_class_to_js_class(self, py_class_snippet: str) -> list[str]:  # noqa: ARG002
        """Return a list of human-readable transformation steps.

        The steps are always returned in canonical order regardless of whether
        each construct actually appears in *py_class_snippet*.
        """
        return [
            # 1
            "class Foo(Bar):  →  class Foo extends Bar {",
            # 2
            "def __init__(self, x):  →  constructor(x) {",
            # 3
            "self.x = x  →  this.x = x;",
            # 4
            "def method(self):  →  method() {",
            # 5
            "@staticmethod  →  static  (prefix the method keyword)",
            # 6
            "@classmethod  →  no direct equivalent; use a static factory method instead",
            # 7
            "super().__init__()  →  super()  (must be first line in constructor)",
            # 8
            "__str__(self)  →  toString() {",
            # 9
            "__repr__  →  no JS equivalent; omit or implement a custom debug() method",
            # 10
            "__eq__  →  no operator overloading in JS; implement a custom .equals() method",
        ]
