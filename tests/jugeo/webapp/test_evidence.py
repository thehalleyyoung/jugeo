"""Tests for the webapp evidence module."""
from __future__ import annotations

import time
import uuid
import pytest

from jugeo.webapp.evidence.models import (
    WebEvidenceChannel,
    WebTrustLevel,
    WebEvidence,
    EvidenceBundle,
    ChannelCapability,
    EvidenceGap,
    TRUST_ORDER,
    trust_level_index,
    compare_trust,
)
from jugeo.webapp.evidence.models import CHANNEL_CAPABILITIES
from jugeo.webapp.evidence.multi_channel import (
    MultiChannelEvidenceEngine,
    EvidenceCombiner,
    EvidenceGapAnalyzer,
)
from jugeo.webapp.evidence.static_analysis import CrossLanguageStaticAnalyzer
from jugeo.webapp.evidence.security_scanner import WebSecurityScanner, SecuritySeverity
from jugeo.webapp.evidence.integration import WebEvidenceCollector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_evidence() -> WebEvidence:
    return WebEvidence(
        id="ev-001",
        channel=WebEvidenceChannel.PYTHON_TYPE_CHECK,
        claim="type check passed",
        coordinate_id="py:app.py",
        trust_level=WebTrustLevel.ORM_TYPE_CHECKED,
        timestamp=1000.0,
        details={"status": "pass"},
        file_path="app.py",
        line_number=10,
    )


@pytest.fixture
def engine() -> MultiChannelEvidenceEngine:
    return MultiChannelEvidenceEngine()


@pytest.fixture
def combiner() -> EvidenceCombiner:
    return EvidenceCombiner()


@pytest.fixture
def gap_analyzer() -> EvidenceGapAnalyzer:
    return EvidenceGapAnalyzer()


@pytest.fixture
def static_analyzer() -> CrossLanguageStaticAnalyzer:
    return CrossLanguageStaticAnalyzer()


@pytest.fixture
def security_scanner() -> WebSecurityScanner:
    return WebSecurityScanner()


@pytest.fixture
def evidence_collector() -> WebEvidenceCollector:
    return WebEvidenceCollector()


# ===================================================================
# WebEvidenceChannel enum tests
# ===================================================================

class TestWebEvidenceChannel:

    def test_all_channels_are_strings(self):
        for ch in WebEvidenceChannel:
            assert isinstance(ch.value, str)

    def test_channel_values(self):
        assert WebEvidenceChannel.PYTHON_TYPE_CHECK.value == "python_type_check"
        assert WebEvidenceChannel.CSS_LINT.value == "css_lint"
        assert WebEvidenceChannel.SECURITY_SCAN.value == "security_scan"

    def test_channel_count(self):
        assert len(list(WebEvidenceChannel)) == 13


# ===================================================================
# WebTrustLevel enum tests
# ===================================================================

class TestWebTrustLevel:

    def test_all_levels_are_strings(self):
        for lvl in WebTrustLevel:
            assert isinstance(lvl.value, str)

    def test_trust_order_length(self):
        assert len(TRUST_ORDER) == 14

    def test_trust_order_starts_with_user_input(self):
        assert TRUST_ORDER[0] == WebTrustLevel.USER_INPUT.value

    def test_trust_order_ends_with_mechanically_verified(self):
        assert TRUST_ORDER[-1] == WebTrustLevel.MECHANICALLY_VERIFIED.value

    def test_trust_level_index_user_input(self):
        assert trust_level_index(WebTrustLevel.USER_INPUT.value) == 0

    def test_trust_level_index_mechanically_verified(self):
        assert trust_level_index(WebTrustLevel.MECHANICALLY_VERIFIED.value) == 13

    def test_trust_level_index_unknown(self):
        assert trust_level_index("NONEXISTENT") == -1

    def test_compare_trust_equal(self):
        assert compare_trust(
            WebTrustLevel.SERVER_VALIDATED.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        ) == 0

    def test_compare_trust_lower(self):
        assert compare_trust(
            WebTrustLevel.USER_INPUT.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        ) == -1

    def test_compare_trust_higher(self):
        assert compare_trust(
            WebTrustLevel.MECHANICALLY_VERIFIED.value,
            WebTrustLevel.USER_INPUT.value,
        ) == 1


# ===================================================================
# WebEvidence tests
# ===================================================================

class TestWebEvidence:

    def test_web_evidence_creation(self, sample_evidence):
        ev = sample_evidence
        assert ev.id == "ev-001"
        assert ev.channel == WebEvidenceChannel.PYTHON_TYPE_CHECK
        assert ev.claim == "type check passed"
        assert ev.coordinate_id == "py:app.py"
        assert ev.trust_level == WebTrustLevel.ORM_TYPE_CHECKED
        assert ev.timestamp == 1000.0
        assert ev.file_path == "app.py"
        assert ev.line_number == 10

    def test_web_evidence_to_dict(self, sample_evidence):
        d = sample_evidence.to_dict()
        assert d["id"] == "ev-001"
        assert d["channel"] == "python_type_check"
        assert d["trust_level"] == "orm_type_checked"
        assert "claim" in d
        assert "coordinate_id" in d
        assert "timestamp" in d
        assert "details" in d
        assert "file_path" in d
        assert "line_number" in d

    def test_web_evidence_from_dict_roundtrip(self, sample_evidence):
        d = sample_evidence.to_dict()
        restored = WebEvidence.from_dict(d)
        assert restored.id == sample_evidence.id
        assert restored.channel == sample_evidence.channel
        assert restored.claim == sample_evidence.claim
        assert restored.coordinate_id == sample_evidence.coordinate_id
        assert restored.trust_level == sample_evidence.trust_level
        assert restored.timestamp == sample_evidence.timestamp
        assert restored.file_path == sample_evidence.file_path
        assert restored.line_number == sample_evidence.line_number

    def test_web_evidence_defaults(self):
        ev = WebEvidence(
            id="ev-def",
            channel=WebEvidenceChannel.CSS_LINT,
            claim="css ok",
            coordinate_id="css:style.css",
            trust_level=WebTrustLevel.CSS_LINTED,
            timestamp=0.0,
        )
        assert ev.file_path == ""
        assert ev.line_number == 0

    def test_web_evidence_details_default_empty(self):
        ev = WebEvidence(
            id="ev-det",
            channel=WebEvidenceChannel.CSS_LINT,
            claim="lint",
            coordinate_id="css:x",
            trust_level=WebTrustLevel.CSS_LINTED,
            timestamp=0.0,
        )
        assert ev.details == {}


# ===================================================================
# EvidenceBundle tests
# ===================================================================

class TestEvidenceBundle:

    def test_evidence_bundle_creation(self, sample_evidence):
        bundle = EvidenceBundle(
            coordinate_id="py:app.py",
            evidence_items=[sample_evidence],
            combined_trust="orm_type_checked",
            convergence_score=0.5,
        )
        assert bundle.coordinate_id == "py:app.py"
        assert len(bundle.evidence_items) == 1
        assert bundle.combined_trust == "orm_type_checked"
        assert bundle.convergence_score == 0.5

    def test_evidence_bundle_to_dict_includes_evidence_items(self, sample_evidence):
        bundle = EvidenceBundle(
            coordinate_id="py:app.py",
            evidence_items=[sample_evidence],
            combined_trust="orm_type_checked",
            convergence_score=0.5,
        )
        d = bundle.to_dict()
        assert "evidence_items" in d
        assert len(d["evidence_items"]) == 1
        assert d["evidence_items"][0]["id"] == "ev-001"

    def test_evidence_bundle_from_dict_roundtrip(self, sample_evidence):
        bundle = EvidenceBundle(
            coordinate_id="py:app.py",
            evidence_items=[sample_evidence],
            combined_trust="orm_type_checked",
            convergence_score=0.5,
        )
        d = bundle.to_dict()
        restored = EvidenceBundle.from_dict(d)
        assert restored.coordinate_id == bundle.coordinate_id
        assert len(restored.evidence_items) == 1
        assert restored.combined_trust == bundle.combined_trust
        assert restored.convergence_score == bundle.convergence_score

    def test_evidence_bundle_empty_items(self):
        bundle = EvidenceBundle(coordinate_id="empty")
        assert bundle.evidence_items == []
        assert bundle.combined_trust == ""
        assert bundle.convergence_score == 0.0


# ===================================================================
# ChannelCapability tests
# ===================================================================

class TestChannelCapability:

    def test_channel_capability_creation(self):
        cap = ChannelCapability(
            channel=WebEvidenceChannel.PYTHON_TYPE_CHECK,
            languages_checked=["python"],
            trust_range=["orm_type_checked", "server_validated"],
            tooling="mypy",
        )
        assert cap.channel == WebEvidenceChannel.PYTHON_TYPE_CHECK
        assert cap.languages_checked == ["python"]

    def test_channel_capability_to_dict(self):
        cap = ChannelCapability(
            channel=WebEvidenceChannel.CSS_LINT,
            languages_checked=["css"],
            trust_range=["css_linted"],
            tooling="stylelint",
            pixel_involvement="indirect",
        )
        d = cap.to_dict()
        assert d["channel"] == "css_lint"
        assert "languages_checked" in d
        assert "tooling" in d

    def test_channel_capability_from_dict_roundtrip(self):
        cap = ChannelCapability(
            channel=WebEvidenceChannel.HTML_VALIDATE,
            languages_checked=["html"],
            trust_range=["client_validated"],
            tooling="vnu",
        )
        d = cap.to_dict()
        restored = ChannelCapability.from_dict(d)
        assert restored.channel == cap.channel
        assert restored.languages_checked == cap.languages_checked
        assert restored.tooling == cap.tooling

    def test_channel_capabilities_dict_has_all_channels(self):
        for ch in WebEvidenceChannel:
            assert ch in CHANNEL_CAPABILITIES, f"Missing capability for {ch}"


# ===================================================================
# EvidenceGap tests
# ===================================================================

class TestEvidenceGap:

    def test_evidence_gap_creation(self):
        gap = EvidenceGap(
            coordinate_id="py:app.py",
            missing_channels=["css_lint", "html_validate"],
            min_trust_achieved="user_input",
            max_trust_possible="server_validated",
            recommendation="Run CSS and HTML channels.",
        )
        assert gap.coordinate_id == "py:app.py"
        assert len(gap.missing_channels) == 2

    def test_evidence_gap_to_dict(self):
        gap = EvidenceGap(
            coordinate_id="c1",
            missing_channels=["css_lint"],
        )
        d = gap.to_dict()
        assert d["coordinate_id"] == "c1"
        assert "missing_channels" in d
        assert "recommendation" in d

    def test_evidence_gap_from_dict_roundtrip(self):
        gap = EvidenceGap(
            coordinate_id="coord-1",
            missing_channels=["python_type_check"],
            min_trust_achieved="user_input",
            max_trust_possible="mechanically_verified",
            recommendation="Add type checker.",
        )
        d = gap.to_dict()
        restored = EvidenceGap.from_dict(d)
        assert restored.coordinate_id == gap.coordinate_id
        assert restored.missing_channels == gap.missing_channels
        assert restored.recommendation == gap.recommendation


# ===================================================================
# MultiChannelEvidenceEngine tests
# ===================================================================

class TestMultiChannelEvidenceEngine:

    def test_collect_evidence_empty_project(self, engine):
        result = engine.collect_evidence({})
        assert isinstance(result, list)
        assert len(result) == 0

    def test_collect_evidence_with_py_files(self, engine):
        result = engine.collect_evidence({
            "py_files": {"app.py": "def index(): pass"},
        })
        assert isinstance(result, list)
        assert len(result) > 0

    def test_run_python_type_check_returns_evidence(self, engine):
        result = engine._run_python_type_check({"app.py": "x: int = 1"})
        assert isinstance(result, list)
        assert all(isinstance(e, WebEvidence) for e in result)

    def test_run_python_type_check_trust_level(self, engine):
        result = engine._run_python_type_check({"app.py": "x: int = 1"})
        for ev in result:
            assert ev.trust_level == WebTrustLevel.ORM_TYPE_CHECKED

    def test_run_jinja2_lint(self, engine):
        result = engine._run_jinja2_lint({
            "base.html": "{% block content %}{% endblock %}",
        })
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_run_css_lint(self, engine):
        result = engine._run_css_lint({
            "style.css": ".foo { color: red; }",
        })
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_run_html_validate(self, engine):
        result = engine._run_html_validate({
            "index.html": "<html><body><h1>Test</h1></body></html>",
        })
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_run_cross_language_static(self, engine):
        result = engine._run_cross_language_static({
            "py_files": {},
            "template_files": {},
        })
        assert isinstance(result, list)

    def test_run_security_scan(self, engine):
        result = engine._run_security_scan({
            "py_files": {},
            "template_files": {},
        })
        assert isinstance(result, list)

    def test_collect_evidence_all_channels(self, engine):
        result = engine.collect_evidence({
            "py_files": {"app.py": "def index(): pass"},
            "template_files": {"base.html": "{% block content %}{% endblock %}"},
            "css_files": {"style.css": ".foo { color: red; }"},
            "html_files": {"index.html": "<html><body></body></html>"},
        })
        channels_present = {e.channel for e in result}
        assert len(channels_present) >= 1

    def test_python_type_check_detects_missing_annotation(self, engine):
        result = engine._run_python_type_check({
            "app.py": "def index(x):\n    return x",
        })
        claims = [e.claim for e in result]
        assert any("missing return-type annotation" in c for c in claims)


# ===================================================================
# EvidenceCombiner tests
# ===================================================================

class TestEvidenceCombiner:

    def _make_evidence(self, channel, trust, coord="coord-1"):
        return WebEvidence(
            id=str(uuid.uuid4()),
            channel=channel,
            claim="test claim",
            coordinate_id=coord,
            trust_level=trust,
            timestamp=time.time(),
        )

    def test_combine_single_item(self, combiner):
        ev = self._make_evidence(
            WebEvidenceChannel.PYTHON_TYPE_CHECK,
            WebTrustLevel.ORM_TYPE_CHECKED,
        )
        bundle = combiner.combine([ev])
        assert isinstance(bundle, EvidenceBundle)
        assert bundle.coordinate_id == "coord-1"
        assert len(bundle.evidence_items) == 1

    def test_combine_multiple_channels(self, combiner):
        items = [
            self._make_evidence(WebEvidenceChannel.PYTHON_TYPE_CHECK, WebTrustLevel.ORM_TYPE_CHECKED),
            self._make_evidence(WebEvidenceChannel.CSS_LINT, WebTrustLevel.CSS_LINTED),
            self._make_evidence(WebEvidenceChannel.HTML_VALIDATE, WebTrustLevel.CLIENT_VALIDATED),
        ]
        bundle = combiner.combine(items)
        assert len(bundle.evidence_items) == 3
        assert bundle.convergence_score > 0.0

    def test_convergence_score_single(self, combiner):
        ev = self._make_evidence(
            WebEvidenceChannel.PYTHON_TYPE_CHECK,
            WebTrustLevel.ORM_TYPE_CHECKED,
        )
        bundle = combiner.combine([ev])
        assert bundle.convergence_score == pytest.approx(1.0 / 13.0)

    def test_convergence_score_multiple(self, combiner):
        items = [
            self._make_evidence(WebEvidenceChannel.PYTHON_TYPE_CHECK, WebTrustLevel.ORM_TYPE_CHECKED),
            self._make_evidence(WebEvidenceChannel.CSS_LINT, WebTrustLevel.CSS_LINTED),
            self._make_evidence(WebEvidenceChannel.HTML_VALIDATE, WebTrustLevel.CLIENT_VALIDATED),
        ]
        bundle = combiner.combine(items)
        assert bundle.convergence_score == pytest.approx(3.0 / 13.0)

    def test_trust_join_same_level(self):
        result = EvidenceCombiner._trust_join(
            WebTrustLevel.USER_INPUT.value,
            WebTrustLevel.USER_INPUT.value,
        )
        assert result == WebTrustLevel.USER_INPUT.value

    def test_trust_join_different_levels(self):
        result = EvidenceCombiner._trust_join(
            WebTrustLevel.USER_INPUT.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        )
        assert result == WebTrustLevel.SERVER_VALIDATED.value

    def test_trust_join_commutativity(self):
        a = WebTrustLevel.CSS_LINTED.value
        b = WebTrustLevel.ORM_TYPE_CHECKED.value
        assert EvidenceCombiner._trust_join(a, b) == EvidenceCombiner._trust_join(b, a)

    def test_combine_returns_highest_trust(self, combiner):
        items = [
            self._make_evidence(WebEvidenceChannel.CSS_LINT, WebTrustLevel.CSS_LINTED),
            self._make_evidence(WebEvidenceChannel.PYTHON_TYPE_CHECK, WebTrustLevel.SERVER_VALIDATED),
            self._make_evidence(WebEvidenceChannel.HTML_VALIDATE, WebTrustLevel.USER_INPUT),
        ]
        bundle = combiner.combine(items)
        assert bundle.combined_trust == WebTrustLevel.SERVER_VALIDATED.value


# ===================================================================
# EvidenceGapAnalyzer tests
# ===================================================================

class TestEvidenceGapAnalyzer:

    def test_find_gaps_with_no_bundles(self, gap_analyzer):
        gaps = gap_analyzer.find_gaps([], ["coord1", "coord2"])
        assert len(gaps) == 2
        assert all(isinstance(g, EvidenceGap) for g in gaps)

    def test_find_gaps_with_complete_coverage(self, gap_analyzer):
        ev_list = [
            WebEvidence(
                id=str(uuid.uuid4()),
                channel=ch,
                claim="claim",
                coordinate_id="coord1",
                trust_level=WebTrustLevel.SERVER_VALIDATED,
                timestamp=time.time(),
            )
            for ch in WebEvidenceChannel
        ]
        bundle = EvidenceBundle(
            coordinate_id="coord1",
            evidence_items=ev_list,
            combined_trust=WebTrustLevel.SERVER_VALIDATED.value,
            convergence_score=1.0,
        )
        gaps = gap_analyzer.find_gaps([bundle], ["coord1"])
        assert len(gaps) == 0

    def test_recommend_channels_returns_list(self, gap_analyzer):
        gap = EvidenceGap(
            coordinate_id="coord-1",
            missing_channels=[
                WebEvidenceChannel.PYTHON_TYPE_CHECK.value,
                WebEvidenceChannel.CSS_LINT.value,
            ],
        )
        result = gap_analyzer.recommend_channels(gap)
        assert isinstance(result, list)
        assert len(result) > 0


# ===================================================================
# CrossLanguageStaticAnalyzer tests
# ===================================================================

class TestCrossLanguageStaticAnalyzer:

    def test_check_template_context_clean(self, static_analyzer):
        route = "return render_template('template.html', title=page_title)"
        template = "<h1>{{ title }}</h1>"
        issues = static_analyzer.check_template_context(route, template)
        assert len(issues) == 0

    def test_check_template_context_missing_var(self, static_analyzer):
        route = "render_template('base.html', title=title)"
        template = "<h1>{{ title }}</h1><p>{{ user_name }}</p>"
        issues = static_analyzer.check_template_context(route, template)
        assert len(issues) >= 1
        assert any("user_name" in i["issue"] for i in issues)

    def test_check_dom_references_clean(self, static_analyzer):
        js = 'document.getElementById("main")'
        html = '<div id="main">content</div>'
        issues = static_analyzer.check_dom_references(js, html)
        assert len(issues) == 0

    def test_check_dom_references_missing(self, static_analyzer):
        js = 'document.getElementById("sidebar")'
        html = '<div id="main">content</div>'
        issues = static_analyzer.check_dom_references(js, html)
        assert len(issues) >= 1
        assert any("sidebar" in i["issue"] for i in issues)

    def test_check_css_references_clean(self, static_analyzer):
        html = '<div class="card">content</div>'
        css = ".card { color: red; }"
        issues = static_analyzer.check_css_references(html, "", css)
        assert len(issues) == 0

    def test_check_css_references_missing(self, static_analyzer):
        html = '<div class="hero-banner">content</div>'
        css = ".card { color: red; }"
        issues = static_analyzer.check_css_references(html, "", css)
        assert len(issues) >= 1

    def test_check_form_consistency_matching(self, static_analyzer):
        py = "@app.route('/submit', methods=['POST'])\ndef submit():\n    pass"
        html = '<form action="/submit" method="POST"><input type="submit"></form>'
        issues = static_analyzer.check_form_consistency(py, html)
        assert len(issues) == 0

    def test_check_api_consistency_clean(self, static_analyzer):
        py = "return jsonify({'name': user.name, 'email': user.email})"
        js = "data.name\ndata.email"
        issues = static_analyzer.check_api_consistency(py, js)
        assert len(issues) == 0

    def test_check_url_consistency_clean(self, static_analyzer):
        py = "@app.route('/home')\ndef home():\n    pass"
        template = "{{ url_for('home') }}"
        issues = static_analyzer.check_url_consistency(py, template)
        assert len(issues) == 0


# ===================================================================
# WebSecurityScanner tests
# ===================================================================

class TestWebSecurityScanner:

    def test_scan_xss_clean(self, security_scanner):
        templates = {"base.html": "<h1>{{ title }}</h1>"}
        result = security_scanner.scan_xss(templates)
        assert len(result) == 0

    def test_scan_xss_detects_safe_filter(self, security_scanner):
        templates = {"base.html": "<div>{{ user_html | safe }}</div>"}
        result = security_scanner.scan_xss(templates)
        assert len(result) >= 1
        assert any("XSS" in f["issue"] or "safe" in f["issue"] for f in result)

    def test_scan_csrf_detects_missing_token(self, security_scanner):
        routes = {"app.py": "@app.route('/submit', methods=['POST'])\ndef submit():\n    pass"}
        templates = {
            "form.html": '<form action="/submit" method="POST"><input type="submit"></form>'
        }
        result = security_scanner.scan_csrf(routes, templates)
        assert len(result) >= 1

    def test_scan_sql_injection_clean(self, security_scanner):
        py = {"app.py": "cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))"}
        result = security_scanner.scan_sql_injection(py)
        assert len(result) == 0

    def test_scan_sql_injection_detects_format(self, security_scanner):
        py = {"app.py": 'query = f"SELECT * FROM users WHERE id = {user_id}"'}
        result = security_scanner.scan_sql_injection(py)
        assert len(result) >= 1
        assert any("SQL injection" in f["issue"] for f in result)

    def test_scan_secret_exposure_detects_hardcoded(self, security_scanner):
        src = {"config.py": 'SECRET_KEY = "mysecretkey12345678"'}
        result = security_scanner.scan_secret_exposure(src)
        assert len(result) >= 1

    def test_scan_secret_exposure_clean(self, security_scanner):
        src = {"config.py": "SECRET_KEY = os.environ.get('SECRET_KEY')"}
        result = security_scanner.scan_secret_exposure(src)
        assert len(result) == 0

    def test_scan_auth_bypass_detects_admin(self, security_scanner):
        routes = {
            "admin.py": "@app.route('/admin')\ndef admin_panel():\n    return 'admin'"
        }
        result = security_scanner.scan_auth_bypass(routes)
        assert len(result) >= 1
        assert any("admin" in f["issue"].lower() for f in result)

    def test_scan_all_returns_combined_results(self, security_scanner):
        project = {
            "templates": {"t.html": "<div>{{ x | safe }}</div>"},
            "routes": {},
            "python": {},
            "all": {},
        }
        result = security_scanner.scan_all(project)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_security_severity_is_string_enum(self):
        for sev in SecuritySeverity:
            assert isinstance(sev.value, str)

    def test_security_severity_values(self):
        assert SecuritySeverity.CRITICAL.value == "critical"
        assert SecuritySeverity.HIGH.value == "high"
        assert SecuritySeverity.MEDIUM.value == "medium"
        assert SecuritySeverity.LOW.value == "low"
        assert SecuritySeverity.INFO.value == "info"


# ===================================================================
# WebEvidenceCollector tests
# ===================================================================

class TestWebEvidenceCollector:

    def test_collect_and_analyze_nonexistent_dir(self, evidence_collector):
        result = evidence_collector.collect_and_analyze("/nonexistent/path/xyz")
        assert isinstance(result, dict)
        assert "evidence" in result
        assert "bundles" in result
        assert "gaps" in result
        assert "summary" in result

    def test_generate_evidence_report_empty(self, evidence_collector):
        report = evidence_collector.generate_evidence_report([], [])
        assert isinstance(report, dict)
        assert "total_evidence" in report
        assert report["total_evidence"] == 0
        assert "total_bundles" in report
        assert report["total_bundles"] == 0

    def test_trust_summary_empty(self, evidence_collector):
        summary = evidence_collector.trust_summary([])
        assert isinstance(summary, dict)
        assert "highest_trust" in summary
        assert "lowest_trust" in summary
        assert "level_counts" in summary
        assert "avg_trust_index" in summary

    def test_coverage_summary_empty(self, evidence_collector):
        summary = evidence_collector.coverage_summary([], [])
        assert isinstance(summary, dict)
        assert "total_coords" in summary
        assert "covered_coords" in summary
        assert "coverage_pct" in summary
        assert summary["total_coords"] == 0

    def test_coverage_summary_with_data(self, evidence_collector):
        bundle = EvidenceBundle(coordinate_id="c1")
        summary = evidence_collector.coverage_summary([bundle], ["c1", "c2"])
        assert summary["total_coords"] == 2
        assert summary["covered_coords"] == 1
        assert summary["coverage_pct"] == 50.0

    def test_trust_summary_with_bundles(self, evidence_collector):
        b1 = EvidenceBundle(
            coordinate_id="c1",
            combined_trust=WebTrustLevel.SERVER_VALIDATED.value,
        )
        b2 = EvidenceBundle(
            coordinate_id="c2",
            combined_trust=WebTrustLevel.CSS_LINTED.value,
        )
        summary = evidence_collector.trust_summary([b1, b2])
        assert summary["highest_trust"] == WebTrustLevel.SERVER_VALIDATED.value
        assert summary["lowest_trust"] == WebTrustLevel.CSS_LINTED.value
