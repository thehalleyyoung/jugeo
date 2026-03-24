"""Multi-channel evidence engine, combiner, and gap analyser.

This module coordinates evidence collection across all verification
channels, combines per-coordinate evidence into bundles, and identifies
gaps in the evidence landscape.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from .models import (
    TRUST_ORDER,
    ChannelCapability,
    CHANNEL_CAPABILITIES,
    EvidenceBundle,
    EvidenceGap,
    WebEvidence,
    WebEvidenceChannel,
    WebTrustLevel,
    compare_trust,
    trust_level_index,
)


# ---------------------------------------------------------------------------
# Total number of evidence channels (used for convergence computation).
# ---------------------------------------------------------------------------
_TOTAL_CHANNELS = len(WebEvidenceChannel)


# ---------------------------------------------------------------------------
# MultiChannelEvidenceEngine
# ---------------------------------------------------------------------------

class MultiChannelEvidenceEngine:
    """Collects evidence from every available channel.

    ``project_data`` is a dictionary with optional keys:

    * ``py_files``       – ``dict[str, str]``  (filename → source)
    * ``template_files`` – ``dict[str, str]``
    * ``css_files``      – ``dict[str, str]``
    * ``html_files``     – ``dict[str, str]``
    * ``js_files``       – ``dict[str, str]``
    * ``sql_files``      – ``dict[str, str]``
    """

    def collect_evidence(self, project_data: dict) -> list[WebEvidence]:
        """Run all applicable channels and return the combined evidence list."""
        evidence: list[WebEvidence] = []

        py_files = project_data.get("py_files", {})
        template_files = project_data.get("template_files", {})
        css_files = project_data.get("css_files", {})
        html_files = project_data.get("html_files", {})
        js_files = project_data.get("js_files", {})
        sql_files = project_data.get("sql_files", {})

        if py_files:
            evidence.extend(self._run_python_type_check(py_files))
        if template_files:
            evidence.extend(self._run_jinja2_lint(template_files))
        if css_files:
            evidence.extend(self._run_css_lint(css_files))
        if html_files:
            evidence.extend(self._run_html_validate(html_files))
        if py_files or template_files or js_files or html_files or css_files:
            evidence.extend(self._run_cross_language_static(project_data))
        if py_files or template_files:
            evidence.extend(self._run_security_scan(project_data))

        return evidence

    # ------------------------------------------------------------------
    # Channel runners
    # ------------------------------------------------------------------

    def _run_python_type_check(self, py_files: dict[str, str]) -> list[WebEvidence]:
        """Simulate mypy-style type checking on Python sources."""
        results: list[WebEvidence] = []

        for filename, source in py_files.items():
            issues: list[dict] = []
            lines = source.splitlines()

            for line_no, line in enumerate(lines, start=1):
                # Detect function definitions missing return-type annotations.
                m = re.match(r"^\s*def\s+\w+\(.*\)\s*:", line)
                if m and "->" not in line:
                    issues.append({
                        "line": line_no,
                        "issue": "missing return-type annotation",
                        "text": line.strip(),
                    })

                # Detect bare ``except:`` blocks.
                if re.match(r"^\s*except\s*:", line):
                    issues.append({
                        "line": line_no,
                        "issue": "bare except clause",
                        "text": line.strip(),
                    })

                # Detect usage of undefined-looking variables (heuristic).
                undef = re.findall(r"\bNone\b\.\w+", line)
                if undef:
                    issues.append({
                        "line": line_no,
                        "issue": "possible attribute access on None",
                        "text": line.strip(),
                    })

            if issues:
                for iss in issues:
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.PYTHON_TYPE_CHECK,
                        claim=f"python_type_check: {filename} issue — {iss['issue']}",
                        coordinate_id=f"py:{filename}",
                        trust_level=WebTrustLevel.ORM_TYPE_CHECKED,
                        timestamp=time.time(),
                        details={"issue": iss["issue"], "text": iss["text"]},
                        file_path=filename,
                        line_number=iss["line"],
                    ))
            else:
                results.append(WebEvidence(
                    id=str(uuid.uuid4()),
                    channel=WebEvidenceChannel.PYTHON_TYPE_CHECK,
                    claim=f"python_type_check: {filename} passes type checking",
                    coordinate_id=f"py:{filename}",
                    trust_level=WebTrustLevel.ORM_TYPE_CHECKED,
                    timestamp=time.time(),
                    details={"status": "pass"},
                    file_path=filename,
                    line_number=0,
                ))

        return results

    def _run_jinja2_lint(self, template_files: dict[str, str]) -> list[WebEvidence]:
        """Check Jinja2 templates for unclosed blocks and unknown filters."""
        results: list[WebEvidence] = []

        for filename, source in template_files.items():
            issues: list[dict] = []
            lines = source.splitlines()

            # Track block-level nesting.
            block_stack: list[str] = []

            for line_no, line in enumerate(lines, start=1):
                # Opening block tags.
                for m in re.finditer(r"\{%[-\s]*(\w+)", line):
                    tag = m.group(1)
                    if tag in ("block", "for", "if", "macro", "call", "filter"):
                        block_stack.append(tag)
                    elif tag.startswith("end"):
                        expected = tag[3:]
                        if block_stack and block_stack[-1] == expected:
                            block_stack.pop()
                        elif block_stack:
                            issues.append({
                                "line": line_no,
                                "issue": (
                                    f"unexpected {{% {tag} %}} — "
                                    f"expected {{% end{block_stack[-1]} %}}"
                                ),
                                "text": line.strip(),
                            })
                        else:
                            issues.append({
                                "line": line_no,
                                "issue": f"unexpected {{% {tag} %}} with no open block",
                                "text": line.strip(),
                            })

                # Check for undefined-looking filters (heuristic).
                for fm in re.finditer(r"\|\s*(\w+)", line):
                    filt = fm.group(1)
                    known_filters = {
                        "safe", "escape", "e", "upper", "lower", "title",
                        "capitalize", "trim", "striptags", "default", "d",
                        "join", "length", "int", "float", "string",
                        "list", "sort", "reverse", "first", "last",
                        "random", "batch", "slice", "round", "abs",
                        "truncate", "wordcount", "replace", "urlencode",
                        "tojson", "indent", "center", "format",
                        "filesizeformat", "pprint", "groupby", "map",
                        "select", "reject", "selectattr", "rejectattr",
                        "unique", "min", "max", "sum", "wordwrap",
                        "xmlattr",
                    }
                    if filt not in known_filters:
                        issues.append({
                            "line": line_no,
                            "issue": f"unknown Jinja2 filter '{filt}'",
                            "text": line.strip(),
                        })

            # Unclosed blocks at end of file.
            for tag in block_stack:
                issues.append({
                    "line": len(lines),
                    "issue": f"unclosed {{% {tag} %}} block",
                    "text": "",
                })

            if issues:
                for iss in issues:
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.JINJA2_LINT,
                        claim=f"jinja2_lint: {filename} issue — {iss['issue']}",
                        coordinate_id=f"template:{filename}",
                        trust_level=WebTrustLevel.TEMPLATE_TYPE_CHECKED,
                        timestamp=time.time(),
                        details={"issue": iss["issue"], "text": iss["text"]},
                        file_path=filename,
                        line_number=iss["line"],
                    ))
            else:
                results.append(WebEvidence(
                    id=str(uuid.uuid4()),
                    channel=WebEvidenceChannel.JINJA2_LINT,
                    claim=f"jinja2_lint: {filename} passes linting",
                    coordinate_id=f"template:{filename}",
                    trust_level=WebTrustLevel.TEMPLATE_TYPE_CHECKED,
                    timestamp=time.time(),
                    details={"status": "pass"},
                    file_path=filename,
                    line_number=0,
                ))

        return results

    def _run_css_lint(self, css_files: dict[str, str]) -> list[WebEvidence]:
        """Check CSS selector syntax and property names."""
        results: list[WebEvidence] = []

        known_properties = {
            "color", "background", "background-color", "background-image",
            "margin", "margin-top", "margin-right", "margin-bottom",
            "margin-left", "padding", "padding-top", "padding-right",
            "padding-bottom", "padding-left", "border", "border-radius",
            "width", "height", "min-width", "min-height", "max-width",
            "max-height", "display", "position", "top", "right", "bottom",
            "left", "float", "clear", "overflow", "z-index", "font",
            "font-size", "font-weight", "font-family", "font-style",
            "text-align", "text-decoration", "text-transform",
            "line-height", "letter-spacing", "word-spacing", "white-space",
            "vertical-align", "list-style", "list-style-type",
            "opacity", "visibility", "cursor", "box-shadow",
            "text-shadow", "transition", "transform", "animation",
            "flex", "flex-direction", "flex-wrap", "justify-content",
            "align-items", "align-content", "align-self", "order",
            "flex-grow", "flex-shrink", "flex-basis", "gap",
            "grid", "grid-template-columns", "grid-template-rows",
            "grid-column", "grid-row", "grid-area", "grid-gap",
            "content", "box-sizing", "outline", "resize", "appearance",
            "user-select", "pointer-events", "object-fit", "object-position",
            "overflow-x", "overflow-y", "word-break", "overflow-wrap",
        }

        for filename, source in css_files.items():
            issues: list[dict] = []
            lines = source.splitlines()

            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("/*") or stripped.startswith("//"):
                    continue

                # Check property names inside rules.
                prop_match = re.match(r"^\s*([\w-]+)\s*:", stripped)
                if prop_match:
                    prop = prop_match.group(1)
                    if prop.startswith("--"):
                        continue  # CSS custom property
                    if prop not in known_properties:
                        issues.append({
                            "line": line_no,
                            "issue": f"unknown CSS property '{prop}'",
                            "text": stripped,
                        })

                # Check for malformed selectors (very basic).
                if stripped.endswith("{"):
                    selector = stripped[:-1].strip()
                    if re.search(r"[^a-zA-Z0-9_\-#.*:>,\[\]=~|^$@\s()+\"']", selector):
                        issues.append({
                            "line": line_no,
                            "issue": f"possibly malformed selector '{selector}'",
                            "text": stripped,
                        })

            if issues:
                for iss in issues:
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.CSS_LINT,
                        claim=f"css_lint: {filename} issue — {iss['issue']}",
                        coordinate_id=f"css:{filename}",
                        trust_level=WebTrustLevel.CSS_LINTED,
                        timestamp=time.time(),
                        details={"issue": iss["issue"], "text": iss["text"]},
                        file_path=filename,
                        line_number=iss["line"],
                    ))
            else:
                results.append(WebEvidence(
                    id=str(uuid.uuid4()),
                    channel=WebEvidenceChannel.CSS_LINT,
                    claim=f"css_lint: {filename} passes linting",
                    coordinate_id=f"css:{filename}",
                    trust_level=WebTrustLevel.CSS_LINTED,
                    timestamp=time.time(),
                    details={"status": "pass"},
                    file_path=filename,
                    line_number=0,
                ))

        return results

    def _run_html_validate(self, html_files: dict[str, str]) -> list[WebEvidence]:
        """Check HTML well-formedness, missing alts, and duplicate ids."""
        results: list[WebEvidence] = []

        for filename, source in html_files.items():
            issues: list[dict] = []
            lines = source.splitlines()
            seen_ids: dict[str, int] = {}

            for line_no, line in enumerate(lines, start=1):
                # Missing alt on <img> tags.
                if re.search(r"<img\b", line, re.IGNORECASE):
                    if "alt=" not in line.lower():
                        issues.append({
                            "line": line_no,
                            "issue": "<img> missing alt attribute",
                            "text": line.strip(),
                        })

                # Duplicate id detection.
                for m in re.finditer(r'id=["\']([^"\']+)["\']', line):
                    eid = m.group(1)
                    if eid in seen_ids:
                        issues.append({
                            "line": line_no,
                            "issue": f"duplicate id '{eid}' (first at line {seen_ids[eid]})",
                            "text": line.strip(),
                        })
                    else:
                        seen_ids[eid] = line_no

                # Unclosed void elements used as containers (heuristic).
                void_elements = {
                    "area", "base", "br", "col", "embed", "hr",
                    "img", "input", "link", "meta", "param",
                    "source", "track", "wbr",
                }
                for m in re.finditer(r"<(/?)(\w+)", line):
                    is_close = m.group(1) == "/"
                    tag = m.group(2).lower()
                    if is_close and tag in void_elements:
                        issues.append({
                            "line": line_no,
                            "issue": f"closing tag for void element <{tag}>",
                            "text": line.strip(),
                        })

            if issues:
                for iss in issues:
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.HTML_VALIDATE,
                        claim=f"html_validate: {filename} issue — {iss['issue']}",
                        coordinate_id=f"html:{filename}",
                        trust_level=WebTrustLevel.CLIENT_VALIDATED,
                        timestamp=time.time(),
                        details={"issue": iss["issue"], "text": iss["text"]},
                        file_path=filename,
                        line_number=iss["line"],
                    ))
            else:
                results.append(WebEvidence(
                    id=str(uuid.uuid4()),
                    channel=WebEvidenceChannel.HTML_VALIDATE,
                    claim=f"html_validate: {filename} passes validation",
                    coordinate_id=f"html:{filename}",
                    trust_level=WebTrustLevel.CLIENT_VALIDATED,
                    timestamp=time.time(),
                    details={"status": "pass"},
                    file_path=filename,
                    line_number=0,
                ))

        return results

    def _run_cross_language_static(
        self, project_data: dict
    ) -> list[WebEvidence]:
        """Cross-language static analysis across template vars, DOM ids, CSS classes."""
        results: list[WebEvidence] = []

        template_files = project_data.get("template_files", {})
        py_files = project_data.get("py_files", {})
        js_files = project_data.get("js_files", {})
        html_files = project_data.get("html_files", {})
        css_files = project_data.get("css_files", {})

        # Template variable cross-check.
        for tpl_name, tpl_src in template_files.items():
            tpl_vars = set(re.findall(r"\{\{\s*(\w+)", tpl_src))

            provided_vars: set[str] = set()
            for py_name, py_src in py_files.items():
                for m in re.finditer(
                    r"render_template\s*\(\s*['\"]" + re.escape(tpl_name) + r"['\"]"
                    r"\s*,\s*(.+?)\)",
                    py_src,
                    re.DOTALL,
                ):
                    kwargs = m.group(1)
                    provided_vars.update(re.findall(r"(\w+)\s*=", kwargs))

            missing = tpl_vars - provided_vars - {"self", "loop", "request", "config", "g", "session", "url_for", "get_flashed_messages"}
            for var in sorted(missing):
                results.append(WebEvidence(
                    id=str(uuid.uuid4()),
                    channel=WebEvidenceChannel.CROSS_LANGUAGE_STATIC,
                    claim=f"cross_language: template '{tpl_name}' uses "
                          f"variable '{var}' not provided by any route",
                    coordinate_id=f"template:{tpl_name}",
                    trust_level=WebTrustLevel.SERVER_VALIDATED,
                    timestamp=time.time(),
                    details={"missing_var": var, "template": tpl_name},
                    file_path=tpl_name,
                    line_number=0,
                ))

        # DOM id cross-check (JS vs HTML).
        html_ids: set[str] = set()
        for _fname, html_src in html_files.items():
            html_ids.update(re.findall(r'id=["\']([^"\']+)["\']', html_src))
        for _fname, tpl_src in template_files.items():
            html_ids.update(re.findall(r'id=["\']([^"\']+)["\']', tpl_src))

        for js_name, js_src in js_files.items():
            referenced_ids = set(
                re.findall(r"getElementById\s*\(\s*['\"](\w+)['\"]\s*\)", js_src)
            )
            missing_ids = referenced_ids - html_ids
            for mid in sorted(missing_ids):
                results.append(WebEvidence(
                    id=str(uuid.uuid4()),
                    channel=WebEvidenceChannel.CROSS_LANGUAGE_STATIC,
                    claim=f"cross_language: JS '{js_name}' references "
                          f"DOM id '{mid}' not found in HTML/templates",
                    coordinate_id=f"js:{js_name}",
                    trust_level=WebTrustLevel.SERVER_VALIDATED,
                    timestamp=time.time(),
                    details={"missing_id": mid, "js_file": js_name},
                    file_path=js_name,
                    line_number=0,
                ))

        # CSS class cross-check.
        css_classes: set[str] = set()
        for _fname, css_src in css_files.items():
            css_classes.update(re.findall(r"\.([\w-]+)\s*\{", css_src))

        html_classes: set[str] = set()
        for _fname, html_src in {**html_files, **template_files}.items():
            for m in re.finditer(r'class=["\']([^"\']+)["\']', html_src):
                html_classes.update(m.group(1).split())

        used_not_defined = html_classes - css_classes
        for cls_name in sorted(used_not_defined):
            results.append(WebEvidence(
                id=str(uuid.uuid4()),
                channel=WebEvidenceChannel.CROSS_LANGUAGE_STATIC,
                claim=f"cross_language: CSS class '{cls_name}' used in "
                      f"HTML/templates but not defined in any CSS file",
                coordinate_id=f"css_class:{cls_name}",
                trust_level=WebTrustLevel.SERVER_VALIDATED,
                timestamp=time.time(),
                details={"missing_class": cls_name},
                file_path="",
                line_number=0,
            ))

        # Emit pass evidence when no issues found.
        if not results:
            results.append(WebEvidence(
                id=str(uuid.uuid4()),
                channel=WebEvidenceChannel.CROSS_LANGUAGE_STATIC,
                claim="cross_language: no cross-language inconsistencies found",
                coordinate_id="project",
                trust_level=WebTrustLevel.SERVER_VALIDATED,
                timestamp=time.time(),
                details={"status": "pass"},
                file_path="",
                line_number=0,
            ))

        return results

    def _run_security_scan(self, project_data: dict) -> list[WebEvidence]:
        """Basic security pattern scan for XSS, CSRF, and SQL-injection."""
        results: list[WebEvidence] = []

        template_files = project_data.get("template_files", {})
        py_files = project_data.get("py_files", {})

        # XSS: |safe in templates.
        for tpl_name, tpl_src in template_files.items():
            for line_no, line in enumerate(tpl_src.splitlines(), start=1):
                if re.search(r"\|\s*safe\b", line):
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.SECURITY_SCAN,
                        claim=f"security_scan: potential XSS in {tpl_name} "
                              f"(|safe filter at line {line_no})",
                        coordinate_id=f"template:{tpl_name}",
                        trust_level=WebTrustLevel.SERVER_VALIDATED,
                        timestamp=time.time(),
                        details={"issue": "xss_safe_filter", "text": line.strip()},
                        file_path=tpl_name,
                        line_number=line_no,
                    ))

        # SQL injection: f-string SQL.
        for py_name, py_src in py_files.items():
            for line_no, line in enumerate(py_src.splitlines(), start=1):
                if re.search(
                    r'f["\'](?:SELECT|INSERT|UPDATE|DELETE)', line, re.IGNORECASE
                ):
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.SECURITY_SCAN,
                        claim=f"security_scan: potential SQL injection in {py_name} "
                              f"(f-string SQL at line {line_no})",
                        coordinate_id=f"py:{py_name}",
                        trust_level=WebTrustLevel.SERVER_VALIDATED,
                        timestamp=time.time(),
                        details={"issue": "sql_injection", "text": line.strip()},
                        file_path=py_name,
                        line_number=line_no,
                    ))

        # CSRF: POST routes without csrf protection.
        for py_name, py_src in py_files.items():
            has_csrf_import = "csrf" in py_src.lower()
            for m in re.finditer(
                r"@\w+\.route\([^)]*methods\s*=\s*\[[^\]]*['\"]POST['\"][^\]]*\]",
                py_src,
            ):
                if not has_csrf_import:
                    line_no = py_src[: m.start()].count("\n") + 1
                    results.append(WebEvidence(
                        id=str(uuid.uuid4()),
                        channel=WebEvidenceChannel.SECURITY_SCAN,
                        claim=f"security_scan: POST route in {py_name} "
                              f"without CSRF protection (line {line_no})",
                        coordinate_id=f"py:{py_name}",
                        trust_level=WebTrustLevel.SERVER_VALIDATED,
                        timestamp=time.time(),
                        details={"issue": "csrf_missing", "text": m.group(0)},
                        file_path=py_name,
                        line_number=line_no,
                    ))

        if not results:
            results.append(WebEvidence(
                id=str(uuid.uuid4()),
                channel=WebEvidenceChannel.SECURITY_SCAN,
                claim="security_scan: no security issues detected",
                coordinate_id="project",
                trust_level=WebTrustLevel.SERVER_VALIDATED,
                timestamp=time.time(),
                details={"status": "pass"},
                file_path="",
                line_number=0,
            ))

        return results


# ---------------------------------------------------------------------------
# EvidenceCombiner
# ---------------------------------------------------------------------------

class EvidenceCombiner:
    """Combine evidence items for the same coordinate into a bundle."""

    def combine(self, evidence_items: list[WebEvidence]) -> EvidenceBundle:
        """Create an ``EvidenceBundle`` from a list of evidence items.

        The *combined_trust* is the join (maximum) of all individual trust
        levels.  The *convergence_score* measures how many distinct channels
        contributed evidence.
        """
        if not evidence_items:
            return EvidenceBundle(
                coordinate_id="",
                evidence_items=[],
                combined_trust=WebTrustLevel.USER_INPUT.value,
                convergence_score=0.0,
            )

        coordinate_id = evidence_items[0].coordinate_id

        combined = evidence_items[0].trust_level.value
        for ev in evidence_items[1:]:
            combined = self._trust_join(combined, ev.trust_level.value)

        convergence = self._convergence_score(evidence_items)

        return EvidenceBundle(
            coordinate_id=coordinate_id,
            evidence_items=list(evidence_items),
            combined_trust=combined,
            convergence_score=convergence,
        )

    @staticmethod
    def _trust_join(t1: str, t2: str) -> str:
        """Return the *maximum* (join) of two trust levels."""
        i1 = trust_level_index(t1)
        i2 = trust_level_index(t2)
        return t1 if i1 >= i2 else t2

    @staticmethod
    def _convergence_score(items: list[WebEvidence]) -> float:
        """Fraction of distinct channels that contributed evidence."""
        distinct = {ev.channel for ev in items}
        return len(distinct) / float(_TOTAL_CHANNELS)


# ---------------------------------------------------------------------------
# EvidenceGapAnalyzer
# ---------------------------------------------------------------------------

class EvidenceGapAnalyzer:
    """Identify coordinates with incomplete evidence coverage."""

    def find_gaps(
        self,
        bundles: list[EvidenceBundle],
        all_coordinates: list[str],
    ) -> list[EvidenceGap]:
        """Return an ``EvidenceGap`` for every under-covered coordinate.

        A coordinate is considered to have a gap if:
        * no bundle exists for it, **or**
        * the bundle's convergence score is below 1.0
        """
        bundle_map: dict[str, EvidenceBundle] = {
            b.coordinate_id: b for b in bundles
        }

        all_channels = {ch.value for ch in WebEvidenceChannel}
        gaps: list[EvidenceGap] = []

        for coord in all_coordinates:
            bundle = bundle_map.get(coord)

            if bundle is None:
                gaps.append(EvidenceGap(
                    coordinate_id=coord,
                    missing_channels=sorted(all_channels),
                    min_trust_achieved=WebTrustLevel.USER_INPUT.value,
                    max_trust_possible=WebTrustLevel.MECHANICALLY_VERIFIED.value,
                    recommendation=(
                        f"No evidence collected for '{coord}'. "
                        "Run all applicable channels."
                    ),
                ))
                continue

            present_channels = {ev.channel.value for ev in bundle.evidence_items}
            missing = sorted(all_channels - present_channels)

            if not missing:
                continue

            trust_indices = [
                trust_level_index(ev.trust_level.value)
                for ev in bundle.evidence_items
            ]
            min_trust = TRUST_ORDER[min(trust_indices)] if trust_indices else WebTrustLevel.USER_INPUT.value
            max_trust = TRUST_ORDER[max(trust_indices)] if trust_indices else WebTrustLevel.USER_INPUT.value

            recommended = self.recommend_channels(
                EvidenceGap(
                    coordinate_id=coord,
                    missing_channels=missing,
                    min_trust_achieved=min_trust,
                    max_trust_possible=max_trust,
                )
            )

            gaps.append(EvidenceGap(
                coordinate_id=coord,
                missing_channels=missing,
                min_trust_achieved=min_trust,
                max_trust_possible=max_trust,
                recommendation=(
                    f"Add channels: {', '.join(recommended[:5])} "
                    f"to raise trust above '{min_trust}'."
                ),
            ))

        return gaps

    def recommend_channels(self, gap: EvidenceGap) -> list[str]:
        """Suggest the most impactful missing channels for a gap.

        The heuristic prioritises channels whose trust range upper-bound is
        highest, so we get maximum trust improvement per added channel.
        """
        scored: list[tuple[int, str]] = []
        for ch_val in gap.missing_channels:
            try:
                ch = WebEvidenceChannel(ch_val)
            except ValueError:
                continue
            cap = CHANNEL_CAPABILITIES.get(ch)
            if cap and cap.trust_range:
                upper = trust_level_index(cap.trust_range[-1])
            else:
                upper = 0
            scored.append((upper, ch_val))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [ch for _, ch in scored]
