"""
Web descent engine — the main driver for descent checking.

Orchestrates overlap checking, trust boundary verification, cohomology
classification, and repair prioritisation across all language layers.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from jugeo.webapp.cross_language.models import OverlapKind, OverlapViolation
from jugeo.webapp.cross_language.overlap_checker import OverlapChecker
from jugeo.webapp.cross_language.trust_topology import WebTrustChecker
from jugeo.webapp.descent.models import (
    CohomologyClass,
    DescentConfiguration,
    DescentResult,
    DescentStrategy,
    WebObstruction,
)


__all__ = [
    "WebDescentEngine",
    "IncrementalDescentEngine",
]


# ---------------------------------------------------------------------------
# Layer metadata
# ---------------------------------------------------------------------------

ALL_LAYERS: list[str] = ["python", "template", "js", "css", "html", "sql", "orm"]

# Map each OverlapKind to the (left, right) layer pair it connects.
OVERLAP_LAYER_MAP: dict[str, tuple[str, str]] = {
    OverlapKind.ROUTE_TEMPLATE.value: ("python", "template"),
    OverlapKind.ROUTE_JS_FETCH.value: ("python", "js"),
    OverlapKind.MODEL_DB_SCHEMA.value: ("orm", "sql"),
    OverlapKind.JS_DOM_HTML.value: ("js", "html"),
    OverlapKind.JS_CLASS_CSS.value: ("js", "css"),
    OverlapKind.FORM_ROUTE.value: ("html", "python"),
    OverlapKind.TEMPLATE_CSS.value: ("template", "css"),
    OverlapKind.AUTH_SESSION.value: ("python", "python"),
    OverlapKind.DB_CONSTRAINT_HANDLER.value: ("sql", "python"),
    OverlapKind.ERROR_HANDLER_JS.value: ("python", "js"),
}

# File extension → layer mapping
_EXT_LAYER: dict[str, str] = {
    ".py": "python",
    ".html": "template",
    ".jinja2": "template",
    ".jinja": "template",
    ".js": "js",
    ".mjs": "js",
    ".jsx": "js",
    ".ts": "js",
    ".tsx": "js",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
    ".sql": "sql",
}

# Severity ordering (lower index = more severe)
_SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _vid(*parts: str) -> str:
    """Deterministic violation/obstruction id."""
    raw = ":".join(parts)
    return "obs-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _violation_to_obstruction(
    violation: OverlapViolation,
    cohomology_class: CohomologyClass,
) -> WebObstruction:
    """Convert a cross-language OverlapViolation into a descent WebObstruction."""
    kind_value = violation.kind.value if isinstance(violation.kind, OverlapKind) else str(violation.kind)
    layers = OVERLAP_LAYER_MAP.get(kind_value, ("unknown", "unknown"))
    return WebObstruction(
        id=violation.id,
        cohomology_class=cohomology_class,
        overlap_kind=kind_value,
        description=violation.message,
        coordinates=[
            f"{layers[0]}:{violation.left_detail}",
            f"{layers[1]}:{violation.right_detail}",
        ],
        severity=violation.severity,
        repair_hint=violation.repair_hint,
        evidence={
            "condition_id": violation.condition_id,
            "left_detail": violation.left_detail,
            "right_detail": violation.right_detail,
            "file_path": violation.file_path,
            "line_number": violation.line_number,
        },
    )


# ---------------------------------------------------------------------------
# Overlap conditions: which site_data keys each check needs
# ---------------------------------------------------------------------------

_CHECK_DISPATCH: dict[str, dict[str, Any]] = {
    OverlapKind.ROUTE_TEMPLATE.value: {
        "method": "check_route_template",
        "args_keys": ("routes", "templates"),
    },
    OverlapKind.ROUTE_JS_FETCH.value: {
        "method": "check_route_js_fetch",
        "args_keys": ("routes", "fetch_calls"),
    },
    OverlapKind.MODEL_DB_SCHEMA.value: {
        "method": "check_model_db_schema",
        "args_keys": ("models", "tables"),
    },
    OverlapKind.JS_DOM_HTML.value: {
        "method": "check_js_dom_html",
        "args_keys": ("js_dom_refs", "html_ids"),
    },
    OverlapKind.JS_CLASS_CSS.value: {
        "method": "check_js_class_css",
        "args_keys": ("js_classes", "css_classes"),
    },
    OverlapKind.FORM_ROUTE.value: {
        "method": "check_form_route",
        "args_keys": ("forms", "routes"),
    },
    OverlapKind.TEMPLATE_CSS.value: {
        "method": "check_template_css",
        "args_keys": ("template_classes", "css_classes"),
    },
    OverlapKind.AUTH_SESSION.value: {
        "method": "check_auth_session",
        "args_keys": ("auth_decorators", "session_checks"),
    },
    OverlapKind.DB_CONSTRAINT_HANDLER.value: {
        "method": "check_db_constraint_handler",
        "args_keys": ("constraints", "handlers"),
    },
    OverlapKind.ERROR_HANDLER_JS.value: {
        "method": "check_error_handler_js",
        "args_keys": ("error_handlers", "js_catch"),
    },
}


def _data_for_key(site_data: dict, key: str) -> Any:
    """Retrieve a value from site_data, returning an appropriate empty default."""
    val = site_data.get(key)
    if val is not None:
        return val
    # Keys that expect sets rather than lists
    if key in ("html_ids", "js_classes", "css_classes", "template_classes",
               "used_classes", "defined_classes"):
        return set()
    return []


# ---------------------------------------------------------------------------
# WebDescentEngine
# ---------------------------------------------------------------------------

class WebDescentEngine:
    """
    Main web descent engine.

    Checks all language-layer overlaps according to the supplied
    :class:`DescentConfiguration`, classifies each violation into a Čech
    cohomology class, and computes a prioritised repair frontier.
    """

    def __init__(self) -> None:
        self._overlap_checker = OverlapChecker()
        self._trust_checker = WebTrustChecker()

    # -- public API ----------------------------------------------------------

    def run_descent(
        self,
        site_data: dict,
        config: DescentConfiguration,
    ) -> DescentResult:
        """
        Run descent checking according to *config*.

        Parameters
        ----------
        site_data : dict
            All parsed project layer data (routes, templates, models, etc.).
        config : DescentConfiguration
            Strategy, layer scope, timeout, and trust threshold.

        Returns
        -------
        DescentResult
        """
        t0 = time.monotonic()
        layers = config.effective_layers
        obstructions: list[WebObstruction] = []
        checked = 0
        passed = 0

        strategy = config.strategy

        if strategy == DescentStrategy.TRUST_BOUNDARY_ONLY:
            trust_obs = self._check_trust_boundaries(site_data)
            obstructions.extend(trust_obs)
            checked = 1
            passed = 0 if trust_obs else 1
        else:
            # Determine which overlap kinds to check
            kinds_to_check = self._overlap_kinds_for_layers(layers, strategy)

            for kind_value in kinds_to_check:
                dispatch = _CHECK_DISPATCH.get(kind_value)
                if dispatch is None:
                    continue

                # Check timeout
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                if elapsed_ms > config.timeout_ms:
                    break

                method_name: str = dispatch["method"]
                args_keys: tuple[str, ...] = dispatch["args_keys"]
                args = [_data_for_key(site_data, k) for k in args_keys]

                method = getattr(self._overlap_checker, method_name)
                violations: list[OverlapViolation] = method(*args)

                checked += 1
                if not violations:
                    passed += 1
                else:
                    for v in violations:
                        cls = self._classify_obstruction(v)
                        obstructions.append(_violation_to_obstruction(v, cls))

            # If full or layer_boundary, also check trust
            if strategy in (DescentStrategy.FULL_CHECK, DescentStrategy.LAYER_BOUNDARY_ONLY):
                trust_obs = self._check_trust_boundaries(site_data)
                checked += 1
                if not trust_obs:
                    passed += 1
                else:
                    obstructions.extend(trust_obs)

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        total_possible = len(_CHECK_DISPATCH) + 1  # +1 for trust
        coverage = checked / total_possible if total_possible > 0 else 0.0

        return DescentResult(
            strategy=strategy,
            obstructions=obstructions,
            checked_conditions=checked,
            passed_conditions=passed,
            coverage_score=coverage,
            timing_ms=round(elapsed_ms, 2),
        )

    # -- layer overlap checking ----------------------------------------------

    def _overlap_kinds_for_layers(
        self,
        layers: list[str],
        strategy: DescentStrategy,
    ) -> list[str]:
        """
        Return the overlap kinds relevant for the given layers and strategy.

        For LAYER_BOUNDARY_ONLY, only include kinds where the two layers
        are distinct.  Otherwise include all kinds where both layers are
        in the requested set.
        """
        layer_set = set(layers)
        result: list[str] = []

        for kind_value, (left, right) in OVERLAP_LAYER_MAP.items():
            if left not in layer_set and right not in layer_set:
                continue
            if strategy == DescentStrategy.LAYER_BOUNDARY_ONLY and left == right:
                continue
            result.append(kind_value)

        return result

    def _check_layer_overlaps(
        self,
        site_data: dict,
        layers: list[str],
    ) -> list[WebObstruction]:
        """Check all overlap conditions between the specified layers."""
        kinds = self._overlap_kinds_for_layers(layers, DescentStrategy.FULL_CHECK)
        obstructions: list[WebObstruction] = []

        for kind_value in kinds:
            dispatch = _CHECK_DISPATCH.get(kind_value)
            if dispatch is None:
                continue
            method_name: str = dispatch["method"]
            args_keys: tuple[str, ...] = dispatch["args_keys"]
            args = [_data_for_key(site_data, k) for k in args_keys]

            method = getattr(self._overlap_checker, method_name)
            violations: list[OverlapViolation] = method(*args)

            for v in violations:
                cls = self._classify_obstruction(v)
                obstructions.append(_violation_to_obstruction(v, cls))

        return obstructions

    # -- trust boundary checking ---------------------------------------------

    def _check_trust_boundaries(
        self,
        site_data: dict,
    ) -> list[WebObstruction]:
        """
        Check trust boundary violations.

        Uses :meth:`WebTrustChecker.check_never_trust_client` to find
        routes that rely solely on client-side validation.
        """
        violations = self._trust_checker.check_never_trust_client(site_data)
        obstructions: list[WebObstruction] = []
        for v in violations:
            obstructions.append(_violation_to_obstruction(
                v, CohomologyClass.H1_OVERLAP_OBSTRUCTION,
            ))
        return obstructions

    # -- classification ------------------------------------------------------

    def _classify_obstruction(
        self,
        violation: OverlapViolation,
    ) -> CohomologyClass:
        """
        Classify a violation into a cohomology class.

        * H¹ for pairwise overlap failures (the standard case).
        * H² if the violation's evidence indicates a triple overlap
          (``"triple_overlap"`` present in condition_id or message).
        """
        msg_lower = violation.message.lower()
        cid_lower = violation.condition_id.lower()

        if "triple_overlap" in cid_lower or "triple_overlap" in msg_lower:
            return CohomologyClass.H2_TRIPLE_OBSTRUCTION

        # Also classify as H2 if the message mentions three distinct layers
        mentioned_layers = set()
        for layer in ALL_LAYERS:
            if layer in msg_lower:
                mentioned_layers.add(layer)
        if len(mentioned_layers) >= 3:
            return CohomologyClass.H2_TRIPLE_OBSTRUCTION

        return CohomologyClass.H1_OVERLAP_OBSTRUCTION

    # -- repair frontier -----------------------------------------------------

    def _compute_repair_frontier(
        self,
        obstructions: list[WebObstruction],
    ) -> list[dict]:
        """
        Compute the minimal set of repairs needed.

        Each repair dict contains:
        ``{"obstruction_id": str, "hint": str, "priority": int,
          "severity": str, "affected_layers": list[str]}``.
        """
        frontier: list[dict] = []
        seen_hints: set[str] = set()

        for obs in obstructions:
            # Deduplicate repairs with the same hint
            hint_key = obs.repair_hint.strip().lower()
            if hint_key in seen_hints:
                continue
            seen_hints.add(hint_key)

            priority = _SEVERITY_RANK.get(obs.severity, 3)
            frontier.append({
                "obstruction_id": obs.id,
                "hint": obs.repair_hint,
                "priority": priority,
                "severity": obs.severity,
                "affected_layers": sorted(obs.affected_layers),
            })

        return self._prioritize_repairs(frontier)

    def _prioritize_repairs(self, frontier: list[dict]) -> list[dict]:
        """
        Order repairs by blast radius.

        Sort by (severity priority ascending, number of affected layers
        descending) so that critical issues affecting many layers come first.
        """
        return sorted(
            frontier,
            key=lambda r: (r["priority"], -len(r["affected_layers"])),
        )


# ---------------------------------------------------------------------------
# IncrementalDescentEngine
# ---------------------------------------------------------------------------

class IncrementalDescentEngine:
    """
    Incremental descent engine.

    Only checks overlap conditions affected by recently changed files,
    making it suitable for editor integrations and CI incremental checks.
    """

    def __init__(self) -> None:
        self._engine = WebDescentEngine()

    # -- public API ----------------------------------------------------------

    def check_changed_files(
        self,
        changed_files: list[str],
        site_data: dict,
    ) -> DescentResult:
        """
        Only check descent conditions affected by *changed_files*.

        Parameters
        ----------
        changed_files : list[str]
            Paths to files that have changed (relative or absolute).
        site_data : dict
            Full site data (only the relevant slices will be checked).

        Returns
        -------
        DescentResult
        """
        t0 = time.monotonic()

        affected = self._affected_layers(changed_files)
        if not affected:
            elapsed = (time.monotonic() - t0) * 1000.0
            return DescentResult(
                strategy=DescentStrategy.INCREMENTAL,
                obstructions=[],
                checked_conditions=0,
                passed_conditions=0,
                coverage_score=0.0,
                timing_ms=round(elapsed, 2),
            )

        # Determine which overlap kinds involve the affected layers
        kinds_to_check = self._affected_overlap_kinds(affected)
        obstructions: list[WebObstruction] = []
        checked = 0
        passed = 0

        checker = self._engine._overlap_checker

        for kind_value in kinds_to_check:
            dispatch = _CHECK_DISPATCH.get(kind_value)
            if dispatch is None:
                continue

            method_name: str = dispatch["method"]
            args_keys: tuple[str, ...] = dispatch["args_keys"]
            args = [_data_for_key(site_data, k) for k in args_keys]

            method = getattr(checker, method_name)
            violations: list[OverlapViolation] = method(*args)

            checked += 1
            if not violations:
                passed += 1
            else:
                for v in violations:
                    cls = self._engine._classify_obstruction(v)
                    obstructions.append(_violation_to_obstruction(v, cls))

        # Also check trust if python layer is affected
        if "python" in affected:
            trust_obs = self._engine._check_trust_boundaries(site_data)
            checked += 1
            if not trust_obs:
                passed += 1
            else:
                obstructions.extend(trust_obs)

        elapsed = (time.monotonic() - t0) * 1000.0
        total_possible = len(_CHECK_DISPATCH) + 1
        coverage = checked / total_possible if total_possible > 0 else 0.0

        return DescentResult(
            strategy=DescentStrategy.INCREMENTAL,
            obstructions=obstructions,
            checked_conditions=checked,
            passed_conditions=passed,
            coverage_score=coverage,
            timing_ms=round(elapsed, 2),
        )

    # -- internal ------------------------------------------------------------

    def _affected_layers(self, changed_files: list[str]) -> set[str]:
        """
        Determine which language layers are affected by changed files.

        Uses file extensions to map files to layers:
        ``.py`` → python, ``.html``/``.jinja2`` → template,
        ``.js`` → js, ``.css`` → css, ``.sql`` → sql.
        """
        layers: set[str] = set()
        for fpath in changed_files:
            # Extract the extension (handle e.g. "foo.jinja2")
            lower = fpath.lower()
            for ext, layer in _EXT_LAYER.items():
                if lower.endswith(ext):
                    layers.add(layer)
                    break
        return layers

    def _affected_overlap_kinds(self, layers: set[str]) -> list[str]:
        """Return overlap kinds that involve at least one of the given layers."""
        result: list[str] = []
        for kind_value, (left, right) in OVERLAP_LAYER_MAP.items():
            if left in layers or right in layers:
                result.append(kind_value)
        return result

    def _affected_conditions(
        self,
        layers: set[str],
        all_conditions: list,
    ) -> list:
        """
        Filter conditions to those involving affected layers.

        Each condition is expected to be a dict with ``"kind"`` or
        ``"overlap_kind"`` key.
        """
        result: list = []
        for cond in all_conditions:
            kind_value = cond.get("kind", cond.get("overlap_kind", ""))
            pair = OVERLAP_LAYER_MAP.get(kind_value)
            if pair is None:
                continue
            if pair[0] in layers or pair[1] in layers:
                result.append(cond)
        return result
