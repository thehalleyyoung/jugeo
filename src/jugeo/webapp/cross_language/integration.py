"""
Integration module — main façade for cross-language analysis (§4.2).

``CrossLanguageAnalyzer`` orchestrates all sub-analyses and produces a
``DescentReport``.  ``QuickCheck`` offers lightweight one-shot helpers
for fast feedback (e.g. in an editor plugin).
"""
from __future__ import annotations

import re
from typing import Any

from jugeo.webapp.cross_language.models import (
    CrossReference,
    DescentReport,
    OverlapViolation,
)
from jugeo.webapp.cross_language.reference_resolver import CrossReferenceResolver
from jugeo.webapp.cross_language.overlap_checker import OverlapChecker
from jugeo.webapp.cross_language.morphism_builder import (
    CrossLanguageMorphismBuilder,
    MorphismGraph,
)
from jugeo.webapp.cross_language.contract_checker import APIContractChecker
from jugeo.webapp.cross_language.trust_topology import WebTrustChecker


__all__ = [
    "CrossLanguageAnalyzer",
    "QuickCheck",
]


# ---------------------------------------------------------------------------
# Main façade
# ---------------------------------------------------------------------------

class CrossLanguageAnalyzer:
    """
    Main façade for cross-language analysis (§4.2).

    Orchestrates reference resolution, overlap checking, morphism
    construction, trust verification, and report generation.
    """

    def __init__(self) -> None:
        self._resolver = CrossReferenceResolver()
        self._overlap_checker = OverlapChecker()
        self._morphism_builder = CrossLanguageMorphismBuilder()
        self._trust_checker = WebTrustChecker()

    def analyze(self, project_data: dict) -> DescentReport:
        """
        Run full cross-language analysis on *project_data*.

        Returns a ``DescentReport`` summarising all findings.
        """
        cross_refs = self._resolve_references(project_data)
        violations = self._check_overlaps(project_data)
        morphisms = self._build_morphisms(cross_refs)
        trust_violations = self._check_trust(project_data, morphisms)
        violations.extend(trust_violations)
        return self._generate_report(violations, cross_refs, morphisms)

    # -- pipeline steps ------------------------------------------------------

    def _resolve_references(
        self,
        project_data: dict,
    ) -> list[CrossReference]:
        """Use ``CrossReferenceResolver`` to resolve all references."""
        return self._resolver.resolve_all(project_data)

    def _check_overlaps(
        self,
        project_data: dict,
    ) -> list[OverlapViolation]:
        """Use ``OverlapChecker`` to run all 10 overlap checks."""
        return self._overlap_checker.check_all(project_data)

    def _build_morphisms(
        self,
        cross_refs: list[CrossReference],
    ) -> list[dict]:
        """Use ``CrossLanguageMorphismBuilder``."""
        return self._morphism_builder.build_morphisms(cross_refs)

    def _check_trust(
        self,
        project_data: dict,
        morphisms: list[dict],
    ) -> list[OverlapViolation]:
        """Use ``WebTrustChecker`` for never-trust-client checks."""
        return self._trust_checker.check_never_trust_client(project_data)

    def _generate_report(
        self,
        violations: list[OverlapViolation],
        cross_refs: list[CrossReference],
        morphisms: list[dict],
    ) -> DescentReport:
        """Build ``DescentReport`` from analysis results."""
        # Compute coverage: fraction of references resolved
        total = len(cross_refs)
        resolved = sum(1 for r in cross_refs if r.resolved)
        coverage = resolved / total if total > 0 else 1.0

        # Compute layer connectivity from morphisms
        connectivity: dict[str, set[str]] = {}
        for m in morphisms:
            src_layer = m["source"].split(":")[0]
            tgt_layer = m["target"].split(":")[0]
            connectivity.setdefault(src_layer, set()).add(tgt_layer)
            connectivity.setdefault(tgt_layer, set()).add(src_layer)
        layer_connectivity = {
            k: sorted(v) for k, v in connectivity.items()
        }

        # Build summary
        error_count = sum(1 for v in violations if v.severity == "error")
        warning_count = sum(1 for v in violations if v.severity == "warning")
        summary_parts: list[str] = []
        summary_parts.append(
            f"{len(violations)} violations ({error_count} errors, "
            f"{warning_count} warnings)"
        )
        summary_parts.append(
            f"{resolved}/{total} cross-references resolved "
            f"({coverage:.0%} coverage)"
        )
        summary_parts.append(
            f"{len(morphisms)} morphisms across "
            f"{len(layer_connectivity)} layers"
        )

        return DescentReport(
            violations=violations,
            cross_references=cross_refs,
            coverage_score=coverage,
            layer_connectivity=layer_connectivity,
            summary="; ".join(summary_parts),
        )


# ---------------------------------------------------------------------------
# Quick checks
# ---------------------------------------------------------------------------

class QuickCheck:
    """
    Lightweight one-shot checks for fast feedback.

    These use simple regex parsing on source strings — no AST analysis.
    Suitable for editor-plugin hot-path checks.
    """

    # -- regex patterns ------------------------------------------------------

    # render_template('name.html', key1=val, key2=val)
    _RENDER_CALL_RE = re.compile(
        r"render_template\s*\(\s*['\"]([^'\"]+)['\"]\s*"
        r"(?:,\s*(\w+)\s*=.*?)*\)",
        re.DOTALL,
    )
    _RENDER_KWARG_RE = re.compile(
        r",\s*(\w+)\s*=",
    )

    # {{ variable }} — including {{ variable|filter }} and {{ variable.attr }}
    _TEMPLATE_VAR_RE = re.compile(
        r"\{\{\s*(\w+)(?:\.\w+|\|[^}]+)?\s*\}\}",
    )

    # document.getElementById('id') or document.getElementById("id")
    _GET_ELEMENT_RE = re.compile(
        r"getElementById\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
    )

    # document.querySelector('#id')
    _QUERY_SELECTOR_ID_RE = re.compile(
        r"querySelector\s*\(\s*['\"]#([^'\"]+)['\"]\s*\)",
    )

    # HTML id="value"
    _HTML_ID_RE = re.compile(
        r'id\s*=\s*["\']([^"\']+)["\']',
    )

    # HTML class="cls1 cls2"
    _HTML_CLASS_RE = re.compile(
        r'class\s*=\s*["\']([^"\']+)["\']',
    )

    # CSS .classname { or .classname,
    _CSS_CLASS_RE = re.compile(
        r"\.([a-zA-Z_][\w-]*)\s*[{,:\s]",
    )

    # -- checks --------------------------------------------------------------

    @staticmethod
    def check_template_vars(
        py_source: str,
        template_source: str,
    ) -> list[str]:
        """
        One-shot template variable check.

        Parse *py_source* for ``render_template(...)`` calls to extract
        kwargs.  Parse *template_source* for ``{{ var }}`` usages.

        Returns list of variable names used in the template but not
        passed by any render_template call.
        """
        # Extract kwargs from all render_template calls
        passed_kwargs: set[str] = set()
        for call_match in QuickCheck._RENDER_CALL_RE.finditer(py_source):
            call_text = call_match.group(0)
            for kw_match in QuickCheck._RENDER_KWARG_RE.finditer(call_text):
                passed_kwargs.add(kw_match.group(1))

        # Extract template variables
        template_vars: set[str] = set()
        for m in QuickCheck._TEMPLATE_VAR_RE.finditer(template_source):
            var = m.group(1)
            # Exclude Jinja2 built-ins
            if var not in {
                "loop", "self", "super", "caller",
                "true", "false", "none", "True", "False", "None",
                "range", "lipsum", "dict", "cycler", "joiner", "namespace",
                "config", "request", "session", "g",
            }:
                template_vars.add(var)

        missing = sorted(template_vars - passed_kwargs)
        return missing

    @staticmethod
    def check_dom_refs(
        js_source: str,
        html_source: str,
    ) -> list[str]:
        """
        One-shot DOM reference check.

        Parse *js_source* for ``getElementById('id')`` and
        ``querySelector('#id')``.  Parse *html_source* for ``id="..."``
        attributes.

        Returns list of element ids referenced in JS but not in HTML.
        """
        # Extract JS references
        js_ids: set[str] = set()
        for m in QuickCheck._GET_ELEMENT_RE.finditer(js_source):
            js_ids.add(m.group(1))
        for m in QuickCheck._QUERY_SELECTOR_ID_RE.finditer(js_source):
            js_ids.add(m.group(1))

        # Extract HTML ids
        html_ids: set[str] = set()
        for m in QuickCheck._HTML_ID_RE.finditer(html_source):
            html_ids.add(m.group(1))

        missing = sorted(js_ids - html_ids)
        return missing

    @staticmethod
    def check_css_classes(
        html_source: str,
        css_source: str,
    ) -> list[str]:
        """
        One-shot CSS class check.

        Parse *html_source* for ``class="..."`` attributes.  Parse
        *css_source* for ``.classname`` definitions.

        Returns list of class names used in HTML but not defined in CSS.
        """
        # Extract classes used in HTML
        used_classes: set[str] = set()
        for m in QuickCheck._HTML_CLASS_RE.finditer(html_source):
            classes_str = m.group(1)
            for cls_name in classes_str.split():
                cls_name = cls_name.strip()
                if cls_name:
                    used_classes.add(cls_name)

        # Extract classes defined in CSS
        defined_classes: set[str] = set()
        for m in QuickCheck._CSS_CLASS_RE.finditer(css_source):
            defined_classes.add(m.group(1))

        missing = sorted(used_classes - defined_classes)
        return missing
