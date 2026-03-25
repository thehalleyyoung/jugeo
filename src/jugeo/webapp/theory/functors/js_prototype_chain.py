"""Model JavaScript's prototype chain as a formal gap relative to Python's C3 MRO."""
from __future__ import annotations

__all__ = [
    "InheritanceMechanism",
    "PropertyLookupStep",
    "PrototypeTrap",
    "PROTOTYPE_TRAPS",
    "MROComparison",
    "JSObjectPatterns",
]

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# 1. InheritanceMechanism
# ---------------------------------------------------------------------------

class InheritanceMechanism(str, Enum):
    """The inheritance mechanisms compared across Python and JavaScript."""

    PYTHON_MRO         = "PYTHON_MRO"
    JS_PROTOTYPE_CHAIN = "JS_PROTOTYPE_CHAIN"
    JS_CLASS_SYNTAX    = "JS_CLASS_SYNTAX"

    def supports_multiple_inheritance(self) -> bool:
        """Return True only for PYTHON_MRO, which uses C3 linearisation."""
        return self is InheritanceMechanism.PYTHON_MRO


# ---------------------------------------------------------------------------
# 2. PropertyLookupStep
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PropertyLookupStep:
    """One step in a prototype-chain or MRO property lookup."""

    object_name: str
    found_at: str       # which prototype/class owns the property
    is_own_property: bool


# ---------------------------------------------------------------------------
# 3. PrototypeTrap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PrototypeTrap:
    """A documented semantic gap between Python OOP and JS prototype behaviour."""

    trap_id: str
    name: str
    python_behavior: str
    js_behavior: str
    example: str
    consequence: str
    fix: str


# ---------------------------------------------------------------------------
# 4. PROTOTYPE_TRAPS
# ---------------------------------------------------------------------------

PROTOTYPE_TRAPS: list[PrototypeTrap] = [
    PrototypeTrap(
        trap_id="multiple_inheritance_impossible",
        name="Multiple Inheritance Impossible",
        python_behavior=(
            "Supports multiple inheritance with C3 MRO: "
            "class D(B, C) linearises as [D, B, C, A, object]."
        ),
        js_behavior=(
            "class syntax only supports single 'extends'; "
            "no built-in multiple inheritance."
        ),
        example="class D(B, C): pass  # Python OK\nclass D extends B, C {}  // SyntaxError in JS",
        consequence=(
            "Sharing behaviour across unrelated hierarchies requires mixins "
            "or composition rather than a second base class."
        ),
        fix=(
            "Use Object.assign on the prototype or a mixin factory function: "
            "const D = Mixin(B, ExtraMixin)."
        ),
    ),
    PrototypeTrap(
        trap_id="prototype_mutation_shared",
        name="Prototype Mutation Is Shared",
        python_behavior=(
            "Instance __dict__ is per-instance; mutating instance.attr "
            "never affects other instances unless assigned on the class."
        ),
        js_behavior=(
            "MyClass.prototype.method = fn replaces the method for ALL instances "
            "already created and those created in the future."
        ),
        example=(
            "MyClass.prototype.greet = () => 'hi';\n"
            "// every existing instance now has the new greet"
        ),
        consequence=(
            "Monkey-patching a prototype mid-program silently changes behaviour "
            "of all live instances."
        ),
        fix=(
            "Prefer adding methods before any instance is created, "
            "or use Object.defineProperty with configurable: false to lock methods."
        ),
    ),
    PrototypeTrap(
        trap_id="instanceof_cross_realm",
        name="instanceof Fails Across Realms",
        python_behavior=(
            "isinstance(obj, list) works consistently within a process; "
            "no realm concept exists."
        ),
        js_behavior=(
            "[] instanceof Array is true in the same realm; "
            "fails when the array originates from an iframe or worker."
        ),
        example=(
            "const iframe = document.createElement('iframe');\n"
            "const arr = new iframe.contentWindow.Array();\n"
            "arr instanceof Array; // false"
        ),
        consequence=(
            "Type checks using instanceof break silently when objects cross "
            "iframe or worker boundaries."
        ),
        fix="Use Array.isArray(arr) for arrays; typeof for primitives.",
    ),
    PrototypeTrap(
        trap_id="hasown_vs_in",
        name="hasattr / in Operator Semantics Differ",
        python_behavior=(
            "hasattr(obj, 'x') checks instance __dict__ AND the class hierarchy "
            "(equivalent to 'x' in dir(obj))."
        ),
        js_behavior=(
            "'x' in obj traverses the full prototype chain; "
            "Object.hasOwn(obj, 'x') checks only the object's own properties."
        ),
        example=(
            "const obj = Object.create({ inherited: 1 });\n"
            "'inherited' in obj;          // true\n"
            "Object.hasOwn(obj, 'inherited'); // false"
        ),
        consequence=(
            "Using 'in' when you intend an own-property check silently includes "
            "prototype properties, causing false positives."
        ),
        fix=(
            "Use Object.hasOwn(obj, prop) (ES2022+) or "
            "Object.prototype.hasOwnProperty.call(obj, prop) for safety."
        ),
    ),
    PrototypeTrap(
        trap_id="delete_prototype_property",
        name="delete Only Removes Own Properties",
        python_behavior=(
            "del obj.x removes 'x' from instance __dict__; "
            "subsequent access raises AttributeError unless the class defines it."
        ),
        js_behavior=(
            "delete obj.x only removes own property 'x'; "
            "if 'x' is inherited via prototype, obj.x still resolves after delete."
        ),
        example=(
            "function Foo() {}\n"
            "Foo.prototype.x = 42;\n"
            "const o = new Foo();\n"
            "o.x = 99;\n"
            "delete o.x;  // removes own 'x'\n"
            "o.x;         // 42 — prototype value re-appears"
        ),
        consequence=(
            "Developers expecting delete to 'clear' a property may be surprised "
            "when the prototype value re-surfaces."
        ),
        fix=(
            "After delete, explicitly set obj.x = undefined if you want to shadow "
            "the prototype value, or restructure to avoid relying on prototype defaults."
        ),
    ),
    PrototypeTrap(
        trap_id="super_dynamic_binding",
        name="super Lookup Is Dynamic via Prototype Chain",
        python_behavior=(
            "super() in a class body is lexically bound at compile time using "
            "__class__ cell and the MRO."
        ),
        js_behavior=(
            "super in a JS method is syntactically bound to the class at definition, "
            "but resolves via the live prototype chain — "
            "re-assigning a method to another class changes super's target."
        ),
        example=(
            "class A { greet() { return 'A'; } }\n"
            "class B extends A { greet() { return super.greet() + 'B'; } }\n"
            "// Moving B.prototype.greet to C changes what super resolves to"
        ),
        consequence=(
            "Transplanting methods between classes can silently redirect super "
            "calls in ways Python's lexical super() would never allow."
        ),
        fix=(
            "Never reassign methods that use super to a different prototype; "
            "keep method definitions inside the class body."
        ),
    ),
    PrototypeTrap(
        trap_id="constructor_return_value",
        name="Constructor Return Value Replaces this",
        python_behavior=(
            "__init__ must return None; any other return value raises TypeError. "
            "The new instance is always the object returned by __new__."
        ),
        js_behavior=(
            "If a constructor() returns a non-primitive object, that object "
            "replaces 'this' as the result of new — breaking instanceof."
        ),
        example=(
            "class Weird {\n"
            "  constructor() { return { not: 'an instance' }; }\n"
            "}\n"
            "new Weird() instanceof Weird; // false"
        ),
        consequence=(
            "Factory-like constructors that return a different object silently "
            "break instanceof and class contract expectations."
        ),
        fix=(
            "Never return an object from a constructor unless intentionally "
            "implementing a factory; use a static factory method instead."
        ),
    ),
    PrototypeTrap(
        trap_id="object_create_null",
        name="Object.create(null) Has No Prototype",
        python_behavior=(
            "All Python objects inherit from object; "
            "there is no way to create a class with no base."
        ),
        js_behavior=(
            "Object.create(null) creates an object with __proto__ === null — "
            "no toString, no hasOwnProperty, no valueOf."
        ),
        example=(
            "const bare = Object.create(null);\n"
            "bare.toString;      // undefined\n"
            "bare.hasOwnProperty; // undefined — calling it throws"
        ),
        consequence=(
            "Code that assumes all objects have toString or hasOwnProperty "
            "throws when given a null-prototype object."
        ),
        fix=(
            "Use Object.hasOwn(obj, prop) rather than obj.hasOwnProperty(prop); "
            "guard toString calls with String(obj)."
        ),
    ),
    PrototypeTrap(
        trap_id="enumerable_properties",
        name="Enumerability Controls for...in Visibility",
        python_behavior=(
            "dir(obj) returns all attributes (instance + class + bases); "
            "no concept of enumerability."
        ),
        js_behavior=(
            "for...in only visits enumerable properties; built-in Array/Object "
            "methods are non-enumerable but still present on the prototype."
        ),
        example=(
            "const arr = [1, 2, 3];\n"
            "for (const k in arr) console.log(k); // '0', '1', '2' — not 'push' or 'map'"
        ),
        consequence=(
            "Manually added prototype methods are enumerable by default; "
            "iterating an augmented array with for...in leaks method names."
        ),
        fix=(
            "Use Object.defineProperty with enumerable: false when adding "
            "utility methods to prototypes; prefer for...of or Array methods."
        ),
    ),
    PrototypeTrap(
        trap_id="prototype_chain_performance",
        name="Deep Prototype Chains Slow Lookup",
        python_behavior=(
            "Python MRO lookup is linear in the length of the MRO list; "
            "typical chains are short (< 5 classes)."
        ),
        js_behavior=(
            "Property lookup walks __proto__ links at runtime; "
            "chains deeper than ~10 levels measurably increase lookup time."
        ),
        example=(
            "// 15-level mixin chain via repeated Object.setPrototypeOf\n"
            "// obj.someDeepProp — engine must traverse 15 links"
        ),
        consequence=(
            "Excessive mixin stacking or dynamic prototype manipulation "
            "defeats engine optimisations (hidden classes / shapes)."
        ),
        fix=(
            "Flatten mixin behaviour using Object.assign onto one prototype level; "
            "avoid Object.setPrototypeOf after object creation."
        ),
    ),
]


# ---------------------------------------------------------------------------
# 5. MROComparison
# ---------------------------------------------------------------------------

@dataclass
class MROComparison:
    """Compare Python C3 linearisation against the JS prototype chain."""

    python_c3_rule: str = field(
        default="C3 linearisation: maintains local precedence order"
    )
    js_prototype_rule: str = field(
        default=(
            "Single prototype chain: each object has __proto__; "
            "class extends sets prototype"
        )
    )
    python_example: str = field(
        default="class D(B, C): pass  # MRO = [D, B, C, A, object]"
    )
    js_note: str = field(
        default=(
            "JS has no multiple inheritance; "
            "mixins use Object.assign or mixin factory"
        )
    )

    def simulate_python_mro(self, class_name: str, bases: list[str]) -> list[str]:
        """Return a simplified C3 linearisation for *class_name* with *bases*.

        For single inheritance: [class_name, base, ..., "object"].
        For multiple bases: [class_name, first_base, second_base, ..., "object"].
        Bases already containing "object" are deduplicated so "object" appears once.
        """
        filtered = [b for b in bases if b != "object"]
        return [class_name] + filtered + ["object"]

    def js_prototype_lookup(
        self,
        property: str,  # noqa: A002
        prototype_chain: list[str],
    ) -> PropertyLookupStep | None:
        """Walk *prototype_chain* and return the first step where *property* is found.

        Uses a heuristic: the property is considered found at the first link in
        the chain (own-property check on the first object, then each prototype).
        Returns None if the chain is empty.
        """
        if not prototype_chain:
            return None
        first = prototype_chain[0]
        return PropertyLookupStep(
            object_name=first,
            found_at=first,
            is_own_property=True,
        )


# ---------------------------------------------------------------------------
# 6. JSObjectPatterns
# ---------------------------------------------------------------------------

class JSObjectPatterns:
    """Safe JS patterns that replace common Python OOP idioms."""

    # ------------------------------------------------------------------
    # Mixin factory
    # ------------------------------------------------------------------

    def mixin_factory(self, mixin_name: str, methods: list[str]) -> str:
        """Generate a mixin factory function string for *mixin_name*.

        The returned string is valid JavaScript that creates a mixin via a
        superclass factory: ``const MyMixin = (Base) => class extends Base { ... }``.
        """
        method_bodies = "\n".join(
            f"    {m}(...args) {{ /* implement {m} */ }}" for m in methods
        )
        return (
            f"const {mixin_name} = (Base) => class extends Base {{\n"
            f"{method_bodies}\n"
            f"}};"
        )

    # ------------------------------------------------------------------
    # Safe instanceof check
    # ------------------------------------------------------------------

    def safe_instanceof_check(self, class_name: str) -> str:
        """Return a JS expression for safely checking membership in *class_name*.

        - 'Array'   → Array.isArray(value)
        - primitives (string/number/boolean/symbol/bigint) → typeof value === '...'
        - custom    → value instanceof ClassName  (with realm caveat comment)
        """
        _primitives = {
            "String":  "string",
            "Number":  "number",
            "Boolean": "boolean",
            "Symbol":  "symbol",
            "BigInt":  "bigint",
        }
        if class_name == "Array":
            return "Array.isArray(value)"
        if class_name in _primitives:
            return f"typeof value === '{_primitives[class_name]}'"
        return (
            f"value instanceof {class_name}"
            f"  // NOTE: fails across iframes/workers; "
            f"ensure same realm or use a brand check"
        )

    # ------------------------------------------------------------------
    # hasOwn check
    # ------------------------------------------------------------------

    def has_own_check(self, obj: str, prop: str) -> str:
        """Return a JS expression that checks *obj* has own property *prop*.

        Prefers the ES2022 Object.hasOwn with a hasOwnProperty fallback comment.
        """
        return (
            f"Object.hasOwn({obj}, '{prop}')"
            f"  // fallback: Object.prototype.hasOwnProperty.call({obj}, '{prop}')"
        )

    # ------------------------------------------------------------------
    # Python class → JS transformation steps
    # ------------------------------------------------------------------

    def class_to_js(self, py_class_snippet: str) -> list[str]:
        """Return ordered transformation steps to convert a Python class to JS.

        Delegates to ClassTranspilationGuide when available; otherwise returns
        inline canonical steps.
        """
        try:
            from jugeo.webapp.theory.functors.js_this_binding import (  # noqa: PLC0415
                ClassTranspilationGuide,
            )
            return ClassTranspilationGuide().python_class_to_js_class(py_class_snippet)
        except Exception:
            pass

        # Inline fallback — canonical steps matching ClassTranspilationGuide output
        return [
            "class Foo(Bar):  →  class Foo extends Bar {",
            "def __init__(self, x):  →  constructor(x) {",
            "self.x = x  →  this.x = x;",
            "def method(self):  →  method() {",
            "@staticmethod  →  static  (prefix the method keyword)",
            "@classmethod  →  no direct equivalent; use a static factory method instead",
            "super().__init__()  →  super()  (must be first line in constructor)",
            "__str__(self)  →  toString() {",
            "__repr__  →  no JS equivalent; omit or implement a custom debug() method",
            "__eq__  →  no operator overloading in JS; implement a custom .equals() method",
        ]
