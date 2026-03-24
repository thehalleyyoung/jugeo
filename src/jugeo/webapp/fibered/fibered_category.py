"""WebFiberedCategory – the fibered category over a web application.

The **total category** is the whole web site; the **base category** has
objects {python, javascript, html, css, sql, template} connected by
inter-language morphisms (e.g. ORM_MAPPING from python to sql).  Each
**fiber** is the per-language sub-site.

This module provides methods to:

* enumerate fibers and the base category,
* construct the total category from site data,
* project a coordinate down to its fiber,
* restrict site data to a single fiber,
* compute cartesian lifts and change-of-fiber functors.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import (
    CartesianLift,
    FiberedCoordinate,
    LanguageFiber,
)


# ---------------------------------------------------------------------------
# Fiber definitions (class-level constants)
# ---------------------------------------------------------------------------

_LANGUAGE_FIBERS: dict[str, LanguageFiber] = {
    "python": LanguageFiber(
        name="python",
        coordinate_kinds=[
            "ROUTE_HANDLER",
            "VIEW_FUNCTION",
            "MODEL_CLASS",
            "FORM_CLASS",
            "MIDDLEWARE",
            "BLUEPRINT",
            "CONFIG_KEY",
        ],
        morphism_kinds=[
            "FUNCTION_CALL",
            "CLASS_INHERITANCE",
            "IMPORT",
            "DECORATOR",
        ],
        internal_topology={"type": "grothendieck", "coverage": "open_cover"},
        description="Server-side Python code (Flask / Django / FastAPI).",
    ),
    "javascript": LanguageFiber(
        name="javascript",
        coordinate_kinds=[
            "JS_MODULE",
            "JS_FUNCTION",
            "JS_EVENT_HANDLER",
            "JS_FETCH_CALL",
            "JS_DOM_MANIPULATION",
            "JS_STATE_VARIABLE",
        ],
        morphism_kinds=[
            "FUNCTION_CALL",
            "EVENT_BINDING",
            "FETCH_REQUEST",
            "DOM_QUERY",
            "MODULE_IMPORT",
        ],
        internal_topology={"type": "grothendieck", "coverage": "open_cover"},
        description="Client-side JavaScript / TypeScript code.",
    ),
    "html": LanguageFiber(
        name="html",
        coordinate_kinds=[
            "HTML_ELEMENT",
            "HTML_ATTRIBUTE",
            "HTML_FORM",
            "HTML_LINK",
        ],
        morphism_kinds=[
            "ELEMENT_CONTAINMENT",
            "ATTRIBUTE_BINDING",
            "HREF_REFERENCE",
            "SRC_REFERENCE",
        ],
        internal_topology={"type": "grothendieck", "coverage": "dom_tree"},
        description="HTML document structure.",
    ),
    "css": LanguageFiber(
        name="css",
        coordinate_kinds=[
            "CSS_STYLESHEET",
            "CSS_RULE",
            "CSS_PROPERTY",
            "CSS_MEDIA_QUERY",
        ],
        morphism_kinds=[
            "RULE_CASCADE",
            "SELECTOR_SPECIFICITY",
            "MEDIA_QUERY_APPLICATION",
        ],
        internal_topology={"type": "grothendieck", "coverage": "cascade"},
        description="CSS stylesheets and rules.",
    ),
    "sql": LanguageFiber(
        name="sql",
        coordinate_kinds=[
            "DB_TABLE",
            "DB_COLUMN",
            "DB_CONSTRAINT",
            "DB_INDEX",
        ],
        morphism_kinds=[
            "FOREIGN_KEY",
            "INDEX_ON_COLUMN",
            "CONSTRAINT_ON_COLUMN",
            "VIEW_DEFINITION",
        ],
        internal_topology={"type": "grothendieck", "coverage": "schema"},
        description="SQL database schema and migrations.",
    ),
    "template": LanguageFiber(
        name="template",
        coordinate_kinds=[
            "TEMPLATE_FILE",
            "TEMPLATE_BLOCK",
            "TEMPLATE_VARIABLE",
            "TEMPLATE_MACRO",
        ],
        morphism_kinds=[
            "BLOCK_INHERITANCE",
            "MACRO_CALL",
            "INCLUDE_REFERENCE",
            "VARIABLE_ACCESS",
        ],
        internal_topology={"type": "grothendieck", "coverage": "template_tree"},
        description="Jinja2 / Django template layer.",
    ),
}

# Prefix-to-fiber mapping for lightweight projection without full site data.
_PREFIX_MAP: dict[str, str] = {
    "py_": "python",
    "js_": "javascript",
    "html_": "html",
    "css_": "css",
    "sql_": "sql",
    "tpl_": "template",
    "tmpl_": "template",
}

# Base-category morphisms (inter-language edges).
_BASE_MORPHISMS: list[dict] = [
    {
        "id": "python_to_template",
        "source": "python",
        "target": "template",
        "kind": "CONTEXT_PROVISION",
    },
    {
        "id": "python_to_sql",
        "source": "python",
        "target": "sql",
        "kind": "ORM_MAPPING",
    },
    {
        "id": "python_to_js",
        "source": "python",
        "target": "javascript",
        "kind": "API_CONTRACT",
    },
    {
        "id": "html_to_css",
        "source": "html",
        "target": "css",
        "kind": "CLASS_REFERENCE",
    },
    {
        "id": "js_to_html",
        "source": "javascript",
        "target": "html",
        "kind": "DOM_MANIPULATION",
    },
    {
        "id": "template_to_html",
        "source": "template",
        "target": "html",
        "kind": "TEMPLATE_RENDERING",
    },
]


# ---------------------------------------------------------------------------
# WebFiberedCategory
# ---------------------------------------------------------------------------

class WebFiberedCategory:
    """The fibered category whose total category is a web application.

    Provides the categorical operations: projection, fiber restriction,
    cartesian lifts, and change-of-fiber functors.
    """

    LANGUAGE_FIBERS: dict[str, LanguageFiber] = _LANGUAGE_FIBERS

    # -- fibers & base category ---------------------------------------------

    def fiber(self, name: str) -> LanguageFiber:
        """Return the ``LanguageFiber`` for *name*.

        Raises ``KeyError`` if *name* is not a recognised language.
        """
        return self.LANGUAGE_FIBERS[name]

    def base_category(self) -> dict:
        """Return the base category as a plain dict.

        The base category has one object per language and morphisms
        representing inter-language relationships (ORM mapping, template
        rendering, etc.).
        """
        return {
            "objects": list(self.LANGUAGE_FIBERS.keys()),
            "morphisms": [dict(m) for m in _BASE_MORPHISMS],
        }

    # -- total category -----------------------------------------------------

    def total_category(self, site_data: dict) -> dict:
        """Construct the total category from *site_data*.

        *site_data* should contain:

        * ``"coordinates"`` – list of ``FiberedCoordinate`` dicts.
        * ``"morphisms"``  – list of ``CartesianLift`` dicts.

        Returns a dict combining base, fibers, coordinates, and morphisms.
        """
        return {
            "base": self.base_category(),
            "fibers": {
                name: fib.to_dict()
                for name, fib in self.LANGUAGE_FIBERS.items()
            },
            "coordinates": list(site_data.get("coordinates", [])),
            "morphisms": list(site_data.get("morphisms", [])),
        }

    # -- projection functor -------------------------------------------------

    def projection(
        self,
        coordinate_id: str,
        site_data: dict | None = None,
    ) -> str:
        """Project a coordinate down to its fiber name.

        If *site_data* is provided, the coordinate is looked up by id.
        Otherwise the fiber is inferred from the coordinate id prefix
        (e.g. ``"py_"`` → ``"python"``).

        Raises ``ValueError`` when the fiber cannot be determined.
        """
        if site_data is not None:
            for coord in site_data.get("coordinates", []):
                cid = (
                    coord.get("coordinate_id")
                    if isinstance(coord, dict)
                    else getattr(coord, "coordinate_id", None)
                )
                if cid == coordinate_id:
                    return (
                        coord["fiber_name"]
                        if isinstance(coord, dict)
                        else coord.fiber_name
                    )

        # Prefix-based inference.
        for prefix, fiber_name in _PREFIX_MAP.items():
            if coordinate_id.startswith(prefix):
                return fiber_name

        raise ValueError(
            f"Cannot determine fiber for coordinate {coordinate_id!r}"
        )

    # -- fiber restriction --------------------------------------------------

    def fiber_over(self, fiber_name: str, site_data: dict) -> dict:
        """Restrict *site_data* to the fiber identified by *fiber_name*.

        Returns a dict with the fiber definition, the coordinates belonging
        to that fiber, and only the internal morphisms (both source and
        target in the same fiber).
        """
        fib = self.LANGUAGE_FIBERS[fiber_name]

        coords = [
            c for c in site_data.get("coordinates", [])
            if _coord_fiber(c) == fiber_name
        ]
        coord_ids = {_coord_id(c) for c in coords}

        morphisms = [
            m for m in site_data.get("morphisms", [])
            if (
                _morph_field(m, "source_fiber") == fiber_name
                and _morph_field(m, "target_fiber") == fiber_name
            )
        ]

        return {
            "fiber": fib.to_dict(),
            "coordinates": coords,
            "morphisms": morphisms,
        }

    # -- cartesian lifts ----------------------------------------------------

    def cartesian_lifts(
        self,
        morphism_id: str,
        site_data: dict,
    ) -> list[CartesianLift]:
        """Return all cartesian lifts matching *morphism_id*.

        A lift matches if its own ``morphism_id`` equals *morphism_id*,
        or if *morphism_id* is a substring of the lift's id (to allow
        base-morphism-level queries such as ``"python_to_sql"``).
        """
        results: list[CartesianLift] = []
        for m in site_data.get("morphisms", []):
            mid = _morph_field(m, "morphism_id")
            if mid == morphism_id or morphism_id in mid:
                if isinstance(m, dict):
                    results.append(CartesianLift.from_dict(m))
                else:
                    results.append(m)
        return results

    # -- change of fiber ----------------------------------------------------

    def change_of_fiber(
        self,
        source_fiber: str,
        target_fiber: str,
        site_data: dict,
    ) -> dict:
        """Compute the change-of-fiber functor image.

        Finds all coordinates in *source_fiber* and, via inter-fiber
        morphisms, identifies their corresponding coordinates in
        *target_fiber*.

        Returns ``{"mappings": [...], "unmapped_source": [...],
        "unmapped_target": [...]}``.
        """
        source_coords = {
            _coord_id(c): c
            for c in site_data.get("coordinates", [])
            if _coord_fiber(c) == source_fiber
        }
        target_coords = {
            _coord_id(c): c
            for c in site_data.get("coordinates", [])
            if _coord_fiber(c) == target_fiber
        }

        mappings: list[dict] = []
        mapped_source: set[str] = set()
        mapped_target: set[str] = set()

        for m in site_data.get("morphisms", []):
            sf = _morph_field(m, "source_fiber")
            tf = _morph_field(m, "target_fiber")
            if sf == source_fiber and tf == target_fiber:
                sc = _morph_field(m, "source_coord")
                tc = _morph_field(m, "target_coord")
                mappings.append({
                    "morphism_id": _morph_field(m, "morphism_id"),
                    "source_coord": sc,
                    "target_coord": tc,
                    "lift_type": _morph_field(m, "lift_type"),
                })
                mapped_source.add(sc)
                mapped_target.add(tc)

        return {
            "mappings": mappings,
            "unmapped_source": sorted(
                set(source_coords.keys()) - mapped_source
            ),
            "unmapped_target": sorted(
                set(target_coords.keys()) - mapped_target
            ),
        }


# ---------------------------------------------------------------------------
# Helpers (duck-typed for both dicts and dataclass instances)
# ---------------------------------------------------------------------------

def _coord_fiber(c: dict | FiberedCoordinate) -> str:
    if isinstance(c, dict):
        return c.get("fiber_name", "")
    return c.fiber_name


def _coord_id(c: dict | FiberedCoordinate) -> str:
    if isinstance(c, dict):
        return c.get("coordinate_id", "")
    return c.coordinate_id


def _morph_field(m: dict | CartesianLift, field: str) -> str:
    if isinstance(m, dict):
        return m.get(field, "")
    return getattr(m, field, "")


# ═══════════════════════════════════════════════════════════════════════
#  Fibered Bundle Diagnostics (Judgment Fiber Bundle integration)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FiberTransportStep:
    """A single step of trust transport between language fibers."""
    source_fiber: str  # language name
    target_fiber: str
    morphism_kind: str  # e.g. "API_CONTRACT", "ORM_MAPPING"
    trust_delta: float = 0.0
    is_cross_fiber: bool = True


@dataclass
class FiberHolonomy:
    """Holonomy around a loop through language fibers.
    
    When data flows through multiple language fibers and returns
    to its origin, the total trust shift is the holonomy.
    Non-trivial holonomy detects cross-language trust defects.
    """
    loop: tuple[str, ...]
    total_shift: float
    steps: list[FiberTransportStep]
    
    @property
    def is_trivial(self) -> bool:
        return abs(self.total_shift) < 1e-9
    
    @property
    def cross_fiber_steps(self) -> int:
        return sum(1 for s in self.steps if s.is_cross_fiber)


class FiberedBundleDiagnostics:
    """Bundle diagnostics for the web fibered category.
    
    Computes trust transport between language fibers and detects
    structural trust defects in the cross-language architecture.
    
    The key insight: bugs in web apps live at fiber boundaries
    (JS↔Python, Python↔SQL, etc.). The curvature at these
    boundaries measures the likelihood of cross-language bugs.
    """
    
    # Standard inter-fiber morphisms and their typical trust effects
    INTER_FIBER_MORPHISMS = {
        ("javascript", "python"): {
            "kind": "API_CONTRACT",
            "typical_delta": -2,  # trust drops at client→server boundary
            "description": "AJAX/fetch call crossing client-server boundary",
        },
        ("python", "javascript"): {
            "kind": "TEMPLATE_RENDER",
            "typical_delta": -1,  # template rendering loses some type safety
            "description": "Server rendering data into client template",
        },
        ("python", "sql"): {
            "kind": "ORM_MAPPING",
            "typical_delta": +1,  # ORM adds type checking
            "description": "Python model to SQL schema mapping",
        },
        ("sql", "python"): {
            "kind": "QUERY_RESULT",
            "typical_delta": -1,  # query results need validation
            "description": "SQL query results back to Python",
        },
        ("html", "javascript"): {
            "kind": "DOM_BINDING",
            "typical_delta": 0,  # same trust level typically
            "description": "HTML element referenced by JavaScript",
        },
        ("html", "css"): {
            "kind": "STYLE_APPLICATION",
            "typical_delta": 0,
            "description": "CSS styles applied to HTML elements",
        },
        ("python", "html"): {
            "kind": "TEMPLATE_CONTEXT",
            "typical_delta": -1,
            "description": "Python context variables passed to Jinja2 template",
        },
    }
    
    def __init__(self):
        self._observations: dict[tuple[str, str], list[float]] = {}
        # (source_fiber, target_fiber) -> list of observed trust deltas
    
    def observe_transport(self, source_fiber: str, target_fiber: str,
                         trust_delta: float) -> None:
        """Record an observed trust change at a fiber boundary."""
        key = (source_fiber, target_fiber)
        self._observations.setdefault(key, []).append(trust_delta)
    
    def use_typical_deltas(self) -> None:
        """Load typical trust deltas from the standard inter-fiber morphism table."""
        for (src, tgt), info in self.INTER_FIBER_MORPHISMS.items():
            self.observe_transport(src, tgt, info["typical_delta"])
    
    def average_delta(self, src: str, tgt: str) -> float:
        deltas = self._observations.get((src, tgt), [])
        return sum(deltas) / len(deltas) if deltas else 0.0
    
    def curvature(self, f1: str, f2: str, f3: str) -> float:
        """Curvature at a triple of language fibers."""
        return (self.average_delta(f1, f2) +
                self.average_delta(f2, f3) +
                self.average_delta(f3, f1))
    
    def request_lifecycle_holonomy(self) -> FiberHolonomy:
        """Compute holonomy around the standard request lifecycle.
        
        The standard web request passes through fibers:
        javascript → python → sql → python → html → javascript
        (browser → server → database → server → template → browser)
        """
        loop = ["javascript", "python", "sql", "python", "html", "javascript"]
        steps = []
        for i in range(len(loop) - 1):
            src, tgt = loop[i], loop[i+1]
            delta = self.average_delta(src, tgt)
            is_cross = src != tgt
            morphism_info = self.INTER_FIBER_MORPHISMS.get((src, tgt), {})
            steps.append(FiberTransportStep(
                source_fiber=src,
                target_fiber=tgt,
                morphism_kind=morphism_info.get("kind", "UNKNOWN"),
                trust_delta=delta,
                is_cross_fiber=is_cross,
            ))
        total = sum(s.trust_delta for s in steps)
        return FiberHolonomy(loop=tuple(loop), total_shift=total, steps=steps)
    
    def all_fiber_curvatures(self) -> dict[tuple[str, str, str], float]:
        """Compute curvature at all fiber triples."""
        fibers = sorted({f for pair in self._observations for f in pair})
        from itertools import combinations
        result = {}
        for triple in combinations(fibers, 3):
            c = self.curvature(*triple)
            if abs(c) > 1e-9:
                result[triple] = c
        return result
    
    def first_chern_class(self) -> float:
        fibers = sorted({f for pair in self._observations for f in pair})
        from itertools import combinations
        triples = list(combinations(fibers, 3))
        if not triples:
            return 0.0
        return sum(self.curvature(*t) for t in triples) / len(triples)
    
    def diagnose(self) -> dict:
        hol = self.request_lifecycle_holonomy()
        curvatures = self.all_fiber_curvatures()
        c1 = self.first_chern_class()
        return {
            'fibers': sorted({f for pair in self._observations for f in pair}),
            'lifecycle_holonomy': {
                'loop': hol.loop,
                'total_shift': hol.total_shift,
                'trivial': hol.is_trivial,
                'cross_fiber_steps': hol.cross_fiber_steps,
            },
            'first_chern_class': c1,
            'non_flat_curvatures': {
                str(k): v for k, v in curvatures.items()
            },
            'bundle_is_flat': abs(c1) < 1e-9 and hol.is_trivial,
        }
