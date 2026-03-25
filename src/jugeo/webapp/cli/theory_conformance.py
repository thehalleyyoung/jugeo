"""Theory-conformance layer for the jugeo Flask CLI generation pipeline.

Uses real jugeo geometry objects — Sites, Presheaves, DescentEngine,
LocalSections, ObligationPresheaves and CoveringFamilies — to validate every
aspect of a webapp spec before and after code generation.

References: theory2.tex §3 (Descent and Gluing), §3.2 (Obstruction Classes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "JUGEO_AVAILABLE",
    "WebappObligationKind",
    "WebappObligation",
    "WebappSpecSite",
    "WebappObligationPresheaf",
    "SpecDescentChecker",
    "GeneratedAppDescentChecker",
    "TheoryDrivenVerifier",
]

# ---------------------------------------------------------------------------
# Optional jugeo import
# ---------------------------------------------------------------------------

JUGEO_AVAILABLE: bool = False

try:
    from jugeo.geometry.site import (  # type: ignore[import-untyped]
        CoveringFamily,
        GrothendieckTopology,
        Coordinate,
        CoordinateKind,
        Morphism,
        MorphismKind,
        Site,
        SiteBuilder,
    )
    from jugeo.geometry.descent import (  # type: ignore[import-untyped]
        DescentConfiguration,
        DescentEngine,
        DescentObstruction,
        DescentResult,
        DescentStrategy,
        GlobalSection,
        LocalSection,
        OverlapCondition,
        OverlapStatus,
    )

    JUGEO_AVAILABLE = True
except ImportError:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# 1. WebappObligationKind
# ---------------------------------------------------------------------------

class WebappObligationKind(str, Enum):
    """Enumeration of obligation types that govern a webapp spec."""

    ROUTE_HAS_HANDLER = "ROUTE_HAS_HANDLER"
    ROUTE_NO_CONFLICT = "ROUTE_NO_CONFLICT"
    MODEL_HAS_PK = "MODEL_HAS_PK"
    FK_REFERENCES_EXIST = "FK_REFERENCES_EXIST"
    AUTH_MIDDLEWARE_PRESENT = "AUTH_MIDDLEWARE_PRESENT"
    BLUEPRINT_PREFIX_UNIQUE = "BLUEPRINT_PREFIX_UNIQUE"
    CSRF_POST_PROTECTED = "CSRF_POST_PROTECTED"
    TEMPLATE_VARS_COHERENT = "TEMPLATE_VARS_COHERENT"
    PORT_VALID = "PORT_VALID"
    APP_NAME_VALID = "APP_NAME_VALID"
    ROUTE_PATH_VALID = "ROUTE_PATH_VALID"
    MODEL_FIELD_TYPES_VALID = "MODEL_FIELD_TYPES_VALID"


# ---------------------------------------------------------------------------
# 2. WebappObligation
# ---------------------------------------------------------------------------

_KNOWN_FIELD_TYPES = (
    "Integer", "String", "Text", "Boolean", "Float", "DateTime", "Date",
    "JSON", "LargeBinary", "Enum", "ForeignKey", "BigInteger", "Numeric",
)


@dataclass
class WebappObligation:
    """A single theory obligation attached to a coordinate in the spec site."""

    kind: WebappObligationKind
    coordinate: str
    description: str
    is_satisfied: bool = False
    obstruction_detail: str = ""
    severity: str = "error"


# ---------------------------------------------------------------------------
# 3. WebappSpecSite
# ---------------------------------------------------------------------------

class WebappSpecSite:
    """Builds a jugeo Site whose coordinates mirror the webapp spec structure.

    Coordinates
    -----------
    * ``app``                — root coordinate for the application
    * ``routes.{handler}``  — one coordinate per route
    * ``models.{Name}``     — one coordinate per model
    * ``blueprints.{name}`` — one coordinate per blueprint

    Morphisms
    ---------
    * RESTRICTION: route/model/blueprint → app
    * INCLUSION: route → blueprint when ``route.path`` starts with blueprint's
      ``url_prefix``

    Covering families
    -----------------
    * All routes cover the app's URL space.
    * All models cover the data layer (separate covering family).
    """

    def __init__(self, spec: dict) -> None:
        self._spec = spec

    def build(self) -> "Site | dict":  # dict fallback when jugeo unavailable
        if not JUGEO_AVAILABLE:
            return {"label": self._spec.get("name", "app"), "unavailable": True}

        spec = self._spec
        app_name = spec.get("name", "app")

        builder = SiteBuilder(label=app_name)

        # Root coordinate
        app_coord = Coordinate(
            components=(app_name,),
            kind=CoordinateKind.MODULE,
        )
        builder.add_coordinate(app_coord)

        route_morphisms: list[Morphism] = []
        model_morphisms: list[Morphism] = []

        # Route coordinates
        for route in spec.get("routes", []):
            handler = route.get("handler") or f"route_{route.get('path','').lstrip('/')}"
            coord_name = f"routes.{handler}"
            r_coord = Coordinate(
                components=("routes", handler),
                kind=CoordinateKind.FUNCTION,
            )
            builder.add_coordinate(r_coord)

            # RESTRICTION: route → app
            restriction = Morphism(
                source=r_coord,
                target=app_coord,
                kind=MorphismKind.RESTRICTION,
            )
            builder.add_morphism(restriction)
            route_morphisms.append(restriction)

            # INCLUSION: route → blueprint when path starts with url_prefix
            for bp in spec.get("blueprints", []):
                prefix = bp.get("url_prefix", "")
                bp_name = bp.get("name", "bp")
                if prefix and route.get("path", "").startswith(prefix):
                    bp_coord = Coordinate(
                        components=("blueprints", bp_name),
                        kind=CoordinateKind.MODULE,
                    )
                    inclusion = Morphism(
                        source=r_coord,
                        target=bp_coord,
                        kind=MorphismKind.INCLUSION,
                    )
                    builder.add_morphism(inclusion)

        # Model coordinates
        for model in spec.get("models", []):
            model_name = model.get("name", "Model")
            m_coord = Coordinate(
                components=("models", model_name),
                kind=CoordinateKind.INTERFACE,
            )
            builder.add_coordinate(m_coord)

            restriction = Morphism(
                source=m_coord,
                target=app_coord,
                kind=MorphismKind.RESTRICTION,
            )
            builder.add_morphism(restriction)
            model_morphisms.append(restriction)

        # Blueprint coordinates
        for bp in spec.get("blueprints", []):
            bp_name = bp.get("name", "bp")
            bp_coord = Coordinate(
                components=("blueprints", bp_name),
                kind=CoordinateKind.MODULE,
            )
            builder.add_coordinate(bp_coord)

            restriction = Morphism(
                source=bp_coord,
                target=app_coord,
                kind=MorphismKind.RESTRICTION,
            )
            builder.add_morphism(restriction)

        # Covering family: all routes cover the app's URL space
        if route_morphisms:
            url_cover = CoveringFamily(
                base=app_coord,
                members=route_morphisms,
            )
            builder.add_covering_family(url_cover)

        # Covering family: all models cover the data layer
        if model_morphisms:
            data_cover = CoveringFamily(
                base=app_coord,
                members=model_morphisms,
            )
            builder.add_covering_family(data_cover)

        return builder.build()


# ---------------------------------------------------------------------------
# 4. WebappObligationPresheaf
# ---------------------------------------------------------------------------

class WebappObligationPresheaf:
    """Assigns and evaluates theory obligations for every coordinate in the site.

    The presheaf structure means each coordinate gets a set of obligations
    that are local to that part of the spec.  ``check_all()`` evaluates
    every obligation and returns the full annotated list.
    """

    def __init__(self, spec: dict, site: Any) -> None:
        self._spec = spec
        self._site = site
        self._obligations: list[WebappObligation] = []
        self._checked: bool = False

    # -- public API ----------------------------------------------------------

    def all_obligations(self) -> list[WebappObligation]:
        """Return the current obligation list (may be unchecked)."""
        if not self._obligations:
            self._build_obligations()
        return list(self._obligations)

    def obligations_for(self, coord_name: str) -> list[WebappObligation]:
        """Return obligations whose coordinate matches *coord_name*."""
        return [o for o in self.all_obligations() if o.coordinate == coord_name]

    def check_all(self) -> list[WebappObligation]:
        """Evaluate every obligation against the spec and return annotated list."""
        self._build_obligations()
        self._run_checks()
        self._checked = True
        return list(self._obligations)

    # -- obligation construction ---------------------------------------------

    def _build_obligations(self) -> None:
        """Construct the full obligation list from the spec (without checking)."""
        self._obligations = []
        spec = self._spec
        app_name = spec.get("name", "")

        # APP-level obligations
        self._obligations.append(WebappObligation(
            kind=WebappObligationKind.APP_NAME_VALID,
            coordinate="app",
            description="App name must be a valid Python identifier",
        ))
        self._obligations.append(WebappObligation(
            kind=WebappObligationKind.PORT_VALID,
            coordinate="app",
            description="Port must be in the range 1024–65535",
        ))

        # AUTH obligation
        if spec.get("auth"):
            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.AUTH_MIDDLEWARE_PRESENT,
                coordinate="app",
                description="Auth=True requires a /login route to be defined",
            ))

        # CSRF obligation (applies globally)
        self._obligations.append(WebappObligation(
            kind=WebappObligationKind.CSRF_POST_PROTECTED,
            coordinate="app",
            description="POST routes require CSRF protection (auth or CSRF noted in spec)",
            severity="warning",
        ))

        # Blueprint obligations
        bp_names = [bp.get("name", "") for bp in spec.get("blueprints", [])]
        if bp_names:
            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.BLUEPRINT_PREFIX_UNIQUE,
                coordinate="app",
                description="All blueprint url_prefix values must be unique",
            ))

        # Route-level obligations
        seen_method_path: set[tuple[str, str]] = set()
        for idx, route in enumerate(spec.get("routes", [])):
            handler = route.get("handler") or ""
            coord = f"routes.{handler}" if handler else f"routes.{idx}"

            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.ROUTE_HAS_HANDLER,
                coordinate=coord,
                description=f"Route {route.get('path', '?')} must have a valid handler identifier",
            ))
            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.ROUTE_PATH_VALID,
                coordinate=coord,
                description=f"Route path '{route.get('path', '')}' must start with /",
            ))
            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.TEMPLATE_VARS_COHERENT,
                coordinate=coord,
                description=f"Handler '{handler}' must be a valid Python identifier",
            ))

            mp = (route.get("method", "GET"), route.get("path", ""))
            if mp in seen_method_path:
                self._obligations.append(WebappObligation(
                    kind=WebappObligationKind.ROUTE_NO_CONFLICT,
                    coordinate=coord,
                    description=f"Duplicate route: {mp[0]} {mp[1]}",
                ))
            seen_method_path.add(mp)

        # Global ROUTE_NO_CONFLICT obligation
        self._obligations.append(WebappObligation(
            kind=WebappObligationKind.ROUTE_NO_CONFLICT,
            coordinate="app",
            description="No two routes may share the same method+path combination",
        ))

        # Model-level obligations
        model_names = {m.get("name", "") for m in spec.get("models", [])}
        for model in spec.get("models", []):
            model_name = model.get("name", "Model")
            coord = f"models.{model_name}"

            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.MODEL_HAS_PK,
                coordinate=coord,
                description=f"Model '{model_name}' must have exactly one primary_key field",
            ))
            self._obligations.append(WebappObligation(
                kind=WebappObligationKind.MODEL_FIELD_TYPES_VALID,
                coordinate=coord,
                description=f"All fields in model '{model_name}' must use known SQLAlchemy types",
            ))

            for f in model.get("fields", []):
                fname = f.get("name", "")
                ftype = f.get("type", "")
                if fname.endswith("_id") or (ftype == "Integer" and fname.endswith("_id")):
                    self._obligations.append(WebappObligation(
                        kind=WebappObligationKind.FK_REFERENCES_EXIST,
                        coordinate=coord,
                        description=(
                            f"FK field '{fname}' on '{model_name}' must reference an existing model"
                        ),
                    ))

    # -- check runners -------------------------------------------------------

    def _run_checks(self) -> None:
        spec = self._spec
        app_name = spec.get("name", "")
        port = spec.get("port", 0)
        routes = spec.get("routes", [])
        models = spec.get("models", [])
        blueprints = spec.get("blueprints", [])
        model_names_lower = {m.get("name", "").lower() for m in models}

        for obl in self._obligations:
            kind = obl.kind

            if kind == WebappObligationKind.APP_NAME_VALID:
                if app_name and app_name.isidentifier():
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = f"'{app_name}' is not a valid Python identifier"

            elif kind == WebappObligationKind.PORT_VALID:
                if 1024 <= port <= 65535:
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = f"port {port} is outside 1024–65535"

            elif kind == WebappObligationKind.AUTH_MIDDLEWARE_PRESENT:
                login_paths = {r.get("path", "") for r in routes}
                if "/login" in login_paths:
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = "No /login route found; auth=True requires login route"

            elif kind == WebappObligationKind.CSRF_POST_PROTECTED:
                has_post = any(r.get("method", "GET") == "POST" for r in routes)
                if not has_post or spec.get("auth") or spec.get("csrf"):
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = (
                        "POST routes present but no auth or CSRF protection noted in spec"
                    )

            elif kind == WebappObligationKind.BLUEPRINT_PREFIX_UNIQUE:
                prefixes = [bp.get("url_prefix", "") for bp in blueprints]
                if len(prefixes) == len(set(prefixes)):
                    obl.is_satisfied = True
                else:
                    dupes = {p for p in prefixes if prefixes.count(p) > 1}
                    obl.obstruction_detail = f"Duplicate url_prefix values: {dupes}"

            elif kind == WebappObligationKind.ROUTE_NO_CONFLICT:
                mp_pairs = [(r.get("method", "GET"), r.get("path", "")) for r in routes]
                if len(mp_pairs) == len(set(mp_pairs)):
                    obl.is_satisfied = True
                else:
                    dupes = {p for p in mp_pairs if mp_pairs.count(p) > 1}
                    obl.obstruction_detail = f"Conflicting routes: {dupes}"

            elif kind == WebappObligationKind.ROUTE_HAS_HANDLER:
                # Derive route from coordinate
                handler_name = obl.coordinate.removeprefix("routes.")
                route = next(
                    (r for r in routes if r.get("handler") == handler_name),
                    None,
                )
                if route is None:
                    # Could not identify; mark as potential violation
                    obl.is_satisfied = False
                    obl.obstruction_detail = f"No route found for handler '{handler_name}'"
                    continue
                h = route.get("handler", "")
                if h and isinstance(h, str) and h.isidentifier():
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = (
                        f"handler '{h}' is missing or not a valid identifier"
                    )

            elif kind == WebappObligationKind.ROUTE_PATH_VALID:
                handler_name = obl.coordinate.removeprefix("routes.")
                route = next(
                    (r for r in routes if r.get("handler") == handler_name),
                    None,
                )
                if route is None:
                    obl.is_satisfied = True  # no handler = handled elsewhere
                    continue
                path = route.get("path", "")
                if path.startswith("/"):
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = f"path '{path}' does not start with /"

            elif kind == WebappObligationKind.TEMPLATE_VARS_COHERENT:
                handler_name = obl.coordinate.removeprefix("routes.")
                route = next(
                    (r for r in routes if r.get("handler") == handler_name),
                    None,
                )
                if route is None:
                    obl.is_satisfied = True
                    continue
                h = route.get("handler", "")
                if h and isinstance(h, str) and h.isidentifier():
                    obl.is_satisfied = True
                else:
                    obl.obstruction_detail = f"handler '{h}' is not a valid Python identifier"

            elif kind == WebappObligationKind.MODEL_HAS_PK:
                model_name = obl.coordinate.removeprefix("models.")
                model = next((m for m in models if m.get("name") == model_name), None)
                if model is None:
                    obl.is_satisfied = True
                    continue
                pk_count = sum(1 for f in model.get("fields", []) if f.get("primary_key"))
                if pk_count == 1:
                    obl.is_satisfied = True
                elif pk_count == 0:
                    obl.obstruction_detail = f"Model '{model_name}' has no primary_key field"
                else:
                    obl.obstruction_detail = (
                        f"Model '{model_name}' has {pk_count} primary_key fields (expected 1)"
                    )

            elif kind == WebappObligationKind.MODEL_FIELD_TYPES_VALID:
                model_name = obl.coordinate.removeprefix("models.")
                model = next((m for m in models if m.get("name") == model_name), None)
                if model is None:
                    obl.is_satisfied = True
                    continue
                bad_fields = [
                    f.get("name", "?")
                    for f in model.get("fields", [])
                    if not any(
                        str(f.get("type", "")).startswith(t) for t in _KNOWN_FIELD_TYPES
                    )
                ]
                if bad_fields:
                    obl.obstruction_detail = (
                        f"Unknown field type(s) in '{model_name}': {bad_fields}"
                    )
                else:
                    obl.is_satisfied = True

            elif kind == WebappObligationKind.FK_REFERENCES_EXIST:
                model_name = obl.coordinate.removeprefix("models.")
                model = next((m for m in models if m.get("name") == model_name), None)
                if model is None:
                    obl.is_satisfied = True
                    continue
                bad_fks: list[str] = []
                for f in model.get("fields", []):
                    fname = f.get("name", "")
                    if not fname.endswith("_id"):
                        continue
                    ref_name = fname[:-3].lower()  # strip _id suffix
                    if ref_name not in model_names_lower:
                        bad_fks.append(fname)
                if bad_fks:
                    obl.obstruction_detail = (
                        f"FK fields {bad_fks} in '{model_name}' reference non-existent models"
                    )
                else:
                    obl.is_satisfied = True


# ---------------------------------------------------------------------------
# 5. SpecDescentChecker
# ---------------------------------------------------------------------------

class SpecDescentChecker:
    """Converts obligation results into a jugeo DescentResult.

    Workflow::

        checker = SpecDescentChecker(spec)
        result  = checker.check_descent()
        report  = checker.obstruction_report()
    """

    def __init__(self, spec: dict) -> None:
        self._spec = spec
        self._site_builder = WebappSpecSite(spec)
        self._site: Any = None
        self._obligations: list[WebappObligation] = []

    def build_site(self) -> Any:
        """Build and cache the jugeo Site from the spec."""
        self._site = self._site_builder.build()
        return self._site

    def local_sections(self) -> list[Any]:
        """Return LocalSection objects corresponding to checked obligations."""
        if not self._obligations:
            self.check_descent()
        if not JUGEO_AVAILABLE:
            return []
        sections = []
        for obl in self._obligations:
            if obl.is_satisfied:
                sections.append(LocalSection(
                    coordinate=obl.coordinate,
                    judgment_data={
                        "obligation": obl.kind.value,
                        "satisfied": True,
                        "coord": obl.coordinate,
                    },
                    trust_level=1.0,
                ))
            else:
                sections.append(LocalSection(
                    coordinate=obl.coordinate,
                    judgment_data={
                        "obligation": obl.kind.value,
                        "detail": obl.obstruction_detail,
                        "coord": obl.coordinate,
                    },
                    trust_level=0.0,
                    is_partial=True,
                    residual_obligations=[obl.kind.value],
                ))
        return sections

    def check_descent(self) -> Any:
        """Run obligation checks and return a DescentResult (or fallback dict)."""
        site = self.build_site()
        presheaf = WebappObligationPresheaf(self._spec, site)
        self._obligations = presheaf.check_all()

        if not JUGEO_AVAILABLE:
            failed = [o for o in self._obligations if not o.is_satisfied]
            return {
                "is_success": len(failed) == 0,
                "obligations": self._obligations,
                "violations": failed,
            }

        violations = [o for o in self._obligations if not o.is_satisfied]
        app_name = self._spec.get("name", "app")

        if not violations:
            global_sec = GlobalSection(
                coordinate=app_name,
                merged_judgment={"all_obligations_satisfied": True},
                constituent_sections=tuple(o.coordinate for o in self._obligations),
                trust_floor=1.0,
            )
            return DescentResult.success(global_sec)

        # Build OverlapConditions for each pair of conflicting violations
        violated_overlaps: list[Any] = []
        for i, obl in enumerate(violations):
            # Each unsatisfied obligation creates a "conflict" with the app root
            overlap = OverlapCondition(
                left_coordinate=obl.coordinate,
                right_coordinate=app_name,
                overlap_coordinate=f"{obl.coordinate}∩{app_name}",
                status=OverlapStatus.VIOLATED,
            )
            violated_overlaps.append(overlap)

        obstruction = DescentObstruction(
            coordinate=app_name,
            violated_overlaps=tuple(violated_overlaps),
            partial_section={
                "satisfied_count": len(self._obligations) - len(violations),
                "total": len(self._obligations),
            },
        )
        return DescentResult.failure(obstruction)

    def obstruction_report(self) -> dict:
        """Return a structured obstruction report dict."""
        result = self.check_descent()

        errors = [
            o for o in self._obligations
            if not o.is_satisfied and o.severity == "error"
        ]
        warnings = [
            o for o in self._obligations
            if not o.is_satisfied and o.severity == "warning"
        ]
        violations_all = errors + warnings

        if JUGEO_AVAILABLE:
            descent_passed = result.is_success
        else:
            descent_passed = result.get("is_success", False)  # type: ignore[union-attr]

        total = len(self._obligations)
        satisfied = total - len(violations_all)

        return {
            "total_obligations": total,
            "satisfied": satisfied,
            "violations": [
                {
                    "kind": o.kind.value,
                    "coord": o.coordinate,
                    "detail": o.obstruction_detail,
                    "severity": o.severity,
                }
                for o in errors
            ],
            "warnings": [
                {
                    "kind": o.kind.value,
                    "coord": o.coordinate,
                    "detail": o.obstruction_detail,
                    "severity": o.severity,
                }
                for o in warnings
            ],
            "descent_passed": descent_passed,
            "summary": (
                f"{total} obligations checked: {satisfied} satisfied, "
                f"{len(violations_all)} violated "
                f"({len(errors)} errors, {len(warnings)} warnings)"
            ),
        }


# ---------------------------------------------------------------------------
# 6. GeneratedAppDescentChecker
# ---------------------------------------------------------------------------

class GeneratedAppDescentChecker:
    """Checks generated app.py text against the spec using jugeo theory.

    Each missing item (handler, model, blueprint, route path, import)
    becomes an obstruction in the returned DescentResult.
    """

    def check_app_py(self, app_py_text: str, spec: dict) -> Any:
        """Verify that *app_py_text* faithfully implements *spec*.

        Returns a DescentResult (or plain dict when jugeo is unavailable).
        """
        issues: list[dict] = []
        app_name = spec.get("name", "app")

        # Handler presence
        for route in spec.get("routes", []):
            handler = route.get("handler", "")
            if handler and not re.search(
                rf"\bdef\s+{re.escape(handler)}\s*\(", app_py_text
            ):
                issues.append({
                    "coord": f"routes.{handler}",
                    "detail": f"Handler function 'def {handler}()' not found in app.py",
                    "severity": "error",
                })

        # Model presence
        for model in spec.get("models", []):
            model_name = model.get("name", "")
            if model_name and not re.search(
                rf"\bclass\s+{re.escape(model_name)}\b", app_py_text
            ):
                issues.append({
                    "coord": f"models.{model_name}",
                    "detail": f"Model class '{model_name}' not found in app.py",
                    "severity": "error",
                })

        # Blueprint presence
        for bp in spec.get("blueprints", []):
            bp_name = bp.get("name", "")
            if bp_name and not (
                re.search(rf"Blueprint\s*\(", app_py_text)
                or re.search(rf"\.register_blueprint\s*\(", app_py_text)
            ):
                issues.append({
                    "coord": f"blueprints.{bp_name}",
                    "detail": f"Blueprint '{bp_name}' not registered in app.py",
                    "severity": "error",
                })

        # Route path presence
        for route in spec.get("routes", []):
            path = route.get("path", "")
            if path and not re.search(
                rf"""@(?:app|\w+)\.route\s*\(\s*['\"]{re.escape(path)}['\"']""",
                app_py_text,
            ):
                issues.append({
                    "coord": f"routes.{route.get('handler', path)}",
                    "detail": f"Route path '{path}' not found in @app.route / @bp.route",
                    "severity": "warning",
                })

        # Flask import
        if "from flask import" not in app_py_text and "import flask" not in app_py_text:
            issues.append({
                "coord": "app",
                "detail": "Flask is not imported in app.py",
                "severity": "error",
            })

        # SQLAlchemy import when models are present
        if spec.get("models") and "SQLAlchemy" not in app_py_text:
            issues.append({
                "coord": "app",
                "detail": "SQLAlchemy not imported but models are defined in spec",
                "severity": "error",
            })

        # flask_login / session when auth=True
        if spec.get("auth") and not (
            "flask_login" in app_py_text or "session" in app_py_text
        ):
            issues.append({
                "coord": "app",
                "detail": "auth=True but neither flask_login nor session found in app.py",
                "severity": "warning",
            })

        if not JUGEO_AVAILABLE:
            return {
                "is_success": len([i for i in issues if i["severity"] == "error"]) == 0,
                "issues": issues,
            }

        if not issues:
            global_sec = GlobalSection(
                coordinate=app_name,
                merged_judgment={"generated_code_valid": True},
                constituent_sections=("app_py",),
                trust_floor=1.0,
            )
            return DescentResult.success(global_sec)

        violated_overlaps = [
            OverlapCondition(
                left_coordinate=issue["coord"],
                right_coordinate=app_name,
                overlap_coordinate=f"{issue['coord']}∩{app_name}",
                status=OverlapStatus.VIOLATED,
            )
            for issue in issues
            if issue["severity"] == "error"
        ]

        if violated_overlaps:
            obstruction = DescentObstruction(
                coordinate=app_name,
                violated_overlaps=tuple(violated_overlaps),
                partial_section={"issues": issues},
            )
            return DescentResult.failure(obstruction)

        # Only warnings — consider it a success with reduced trust
        global_sec = GlobalSection(
            coordinate=app_name,
            merged_judgment={"generated_code_valid": True, "warnings": len(issues)},
            constituent_sections=("app_py",),
            trust_floor=0.9,
        )
        return DescentResult.success(global_sec)


# ---------------------------------------------------------------------------
# 7. TheoryDrivenVerifier
# ---------------------------------------------------------------------------

class TheoryDrivenVerifier:
    """Top-level entry point combining spec and generated-code descent checks.

    Usage::

        verifier = TheoryDrivenVerifier()
        spec_report = verifier.verify_spec(spec)
        gen_report  = verifier.verify_generated(spec, app_py_text)
        full_report = verifier.full_verify(spec, app_py_text)
    """

    def verify_spec(self, spec: dict) -> dict:
        """Run theory-based checks on the spec before generation.

        Returns an obstruction_report dict with keys: total_obligations,
        satisfied, violations, warnings, descent_passed, summary.
        """
        checker = SpecDescentChecker(spec)
        return checker.obstruction_report()

    def verify_generated(self, spec: dict, app_py_text: str) -> dict:
        """Run theory-based checks on the generated app.py.

        Returns an obstruction_report dict mirroring the spec report format.
        """
        checker = GeneratedAppDescentChecker()
        result = checker.check_app_py(app_py_text, spec)

        if not JUGEO_AVAILABLE:
            issues = result.get("issues", [])  # type: ignore[union-attr]
            errors = [i for i in issues if i["severity"] == "error"]
            warnings = [i for i in issues if i["severity"] == "warning"]
            total = len(issues)
            satisfied = 0  # all issues represent unsatisfied checks
            return {
                "total_obligations": total,
                "satisfied": satisfied,
                "violations": errors,
                "warnings": warnings,
                "descent_passed": result.get("is_success", False),  # type: ignore[union-attr]
                "summary": (
                    f"{total} generated-code checks: {satisfied} satisfied, "
                    f"{total} violated ({len(errors)} errors, {len(warnings)} warnings)"
                ),
            }

        if result.is_success:
            trust = result.section.trust_floor if result.section else 1.0
            return {
                "total_obligations": 1,
                "satisfied": 1,
                "violations": [],
                "warnings": [],
                "descent_passed": True,
                "summary": f"1 generated-code check passed (trust={trust:.2f})",
            }

        # Failure: unpack obstruction
        obstruction = result.obstruction
        issues_raw: list[dict] = []
        if obstruction and obstruction.partial_section:
            issues_raw = obstruction.partial_section.get("issues", [])
        errors = [i for i in issues_raw if i.get("severity") == "error"]
        warnings = [i for i in issues_raw if i.get("severity") == "warning"]
        total = len(issues_raw) or 1
        satisfied = total - len(errors) - len(warnings)
        return {
            "total_obligations": total,
            "satisfied": max(0, satisfied),
            "violations": errors,
            "warnings": warnings,
            "descent_passed": False,
            "summary": (
                f"{total} generated-code checks: {max(0, satisfied)} satisfied, "
                f"{len(errors) + len(warnings)} violated "
                f"({len(errors)} errors, {len(warnings)} warnings)"
            ),
        }

    def full_verify(self, spec: dict, app_py_text: str) -> dict:
        """Run both spec-level and generated-code checks, returning a combined report."""
        spec_report = self.verify_spec(spec)
        gen_report = self.verify_generated(spec, app_py_text)

        total = spec_report["total_obligations"] + gen_report["total_obligations"]
        satisfied = spec_report["satisfied"] + gen_report["satisfied"]
        violations = spec_report["violations"] + gen_report["violations"]
        warnings = spec_report["warnings"] + gen_report["warnings"]
        descent_passed = spec_report["descent_passed"] and gen_report["descent_passed"]

        errors = len(violations)
        warns = len(warnings)
        return {
            "total_obligations": total,
            "satisfied": satisfied,
            "violations": violations,
            "warnings": warnings,
            "descent_passed": descent_passed,
            "spec_report": spec_report,
            "generated_report": gen_report,
            "summary": (
                f"{total} obligations checked: {satisfied} satisfied, "
                f"{errors + warns} violated ({errors} errors, {warns} warnings)"
            ),
        }
