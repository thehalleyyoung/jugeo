"""Tests for the webapp trust module."""
from __future__ import annotations

import time
import pytest

from jugeo.webapp.trust.models import (
    TrustBoundary,
    TrustTransport,
    TrustPolicy,
    TrustRule,
    TrustReport,
    TRUST_ORDER,
)
from jugeo.webapp.trust.web_trust import (
    WebTrustTopology,
    NeverTrustClientChecker,
    TrustPolicyEngine,
)
from jugeo.webapp.trust.trust_transport import (
    TrustAlgebra,
    TrustCertificate,
    CertificateEmitter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def topo() -> WebTrustTopology:
    return WebTrustTopology()


@pytest.fixture
def checker() -> NeverTrustClientChecker:
    return NeverTrustClientChecker()


@pytest.fixture
def policy_engine() -> TrustPolicyEngine:
    return TrustPolicyEngine()


@pytest.fixture
def emitter() -> CertificateEmitter:
    return CertificateEmitter()


@pytest.fixture
def sample_boundary() -> TrustBoundary:
    return TrustBoundary(
        name="client_server",
        source_layers=["browser", "javascript"],
        target_layers=["python", "database"],
        boundary_type="client_server",
        requires_revalidation=True,
        description="Client-to-server boundary",
    )


@pytest.fixture
def sample_transport() -> TrustTransport:
    return TrustTransport(
        morphism_kind="API_CONTRACT",
        source_trust="USER_INPUT",
        target_trust="API_CONTRACT_TESTED",
        trust_change=7,
        valid=True,
        reason="normal promotion",
    )


@pytest.fixture
def sample_rule() -> TrustRule:
    return TrustRule(
        condition="crosses_client_server_boundary",
        action="demote",
        trust_floor="USER_INPUT",
        trust_ceiling="CLIENT_VALIDATED",
        description="Demote across boundary",
    )


@pytest.fixture
def sample_policy(sample_rule) -> TrustPolicy:
    return TrustPolicy(
        name="test_policy",
        rules=[sample_rule],
        default_action="allow",
        description="Test policy",
    )


# ===================================================================
# TrustBoundary tests
# ===================================================================

class TestTrustBoundary:

    def test_trust_boundary_creation(self, sample_boundary):
        assert sample_boundary.name == "client_server"
        assert sample_boundary.boundary_type == "client_server"
        assert sample_boundary.requires_revalidation is True

    def test_trust_boundary_to_dict(self, sample_boundary):
        d = sample_boundary.to_dict()
        assert d["name"] == "client_server"
        assert "source_layers" in d
        assert "target_layers" in d
        assert "boundary_type" in d
        assert "requires_revalidation" in d

    def test_trust_boundary_from_dict_roundtrip(self, sample_boundary):
        d = sample_boundary.to_dict()
        restored = TrustBoundary.from_dict(d)
        assert restored.name == sample_boundary.name
        assert restored.source_layers == sample_boundary.source_layers
        assert restored.target_layers == sample_boundary.target_layers
        assert restored.boundary_type == sample_boundary.boundary_type
        assert restored.requires_revalidation == sample_boundary.requires_revalidation

    def test_client_server_boundary_fields(self):
        boundary = TrustBoundary(
            name="cs",
            source_layers=["browser"],
            target_layers=["server"],
            boundary_type="client_server",
            requires_revalidation=True,
        )
        assert boundary.requires_revalidation is True


# ===================================================================
# TrustTransport tests
# ===================================================================

class TestTrustTransport:

    def test_trust_transport_creation(self, sample_transport):
        assert sample_transport.morphism_kind == "API_CONTRACT"
        assert sample_transport.source_trust == "USER_INPUT"
        assert sample_transport.target_trust == "API_CONTRACT_TESTED"

    def test_trust_transport_to_dict(self, sample_transport):
        d = sample_transport.to_dict()
        assert d["morphism_kind"] == "API_CONTRACT"
        assert d["trust_change"] == 7
        assert "valid" in d

    def test_trust_transport_from_dict_roundtrip(self, sample_transport):
        d = sample_transport.to_dict()
        restored = TrustTransport.from_dict(d)
        assert restored.morphism_kind == sample_transport.morphism_kind
        assert restored.source_trust == sample_transport.source_trust
        assert restored.target_trust == sample_transport.target_trust
        assert restored.trust_change == sample_transport.trust_change
        assert restored.valid == sample_transport.valid

    def test_trust_transport_valid_flag(self):
        t = TrustTransport(
            morphism_kind="X",
            source_trust="USER_INPUT",
            target_trust="USER_INPUT",
            trust_change=0,
            valid=False,
            reason="invalid promotion",
        )
        assert t.valid is False
        assert t.reason == "invalid promotion"


# ===================================================================
# TrustPolicy tests
# ===================================================================

class TestTrustPolicy:

    def test_trust_policy_creation(self, sample_policy):
        assert sample_policy.name == "test_policy"
        assert len(sample_policy.rules) == 1

    def test_trust_policy_to_dict_serializes_rules(self, sample_policy):
        d = sample_policy.to_dict()
        assert d["name"] == "test_policy"
        assert isinstance(d["rules"], list)
        assert len(d["rules"]) == 1
        assert d["rules"][0]["condition"] == "crosses_client_server_boundary"

    def test_trust_policy_from_dict_roundtrip(self, sample_policy):
        d = sample_policy.to_dict()
        restored = TrustPolicy.from_dict(d)
        assert restored.name == sample_policy.name
        assert len(restored.rules) == len(sample_policy.rules)
        assert restored.default_action == sample_policy.default_action

    def test_trust_policy_default_action(self):
        p = TrustPolicy(name="empty")
        assert p.default_action == "allow"
        assert p.rules == []


# ===================================================================
# TrustRule tests
# ===================================================================

class TestTrustRule:

    def test_trust_rule_creation(self, sample_rule):
        assert sample_rule.condition == "crosses_client_server_boundary"
        assert sample_rule.action == "demote"
        assert sample_rule.trust_floor == "USER_INPUT"
        assert sample_rule.trust_ceiling == "CLIENT_VALIDATED"

    def test_trust_rule_to_dict(self, sample_rule):
        d = sample_rule.to_dict()
        assert d["condition"] == "crosses_client_server_boundary"
        assert d["action"] == "demote"

    def test_trust_rule_from_dict_roundtrip(self, sample_rule):
        d = sample_rule.to_dict()
        restored = TrustRule.from_dict(d)
        assert restored.condition == sample_rule.condition
        assert restored.action == sample_rule.action
        assert restored.trust_floor == sample_rule.trust_floor
        assert restored.trust_ceiling == sample_rule.trust_ceiling


# ===================================================================
# TrustReport tests
# ===================================================================

class TestTrustReport:

    def test_trust_report_creation(self):
        report = TrustReport(
            boundaries=[],
            violations=[{"location": "step[0]", "violation": "bad", "severity": "error"}],
            transports=[],
            policy_applied="test",
            overall_trust="USER_INPUT",
            passed=False,
        )
        assert report.passed is False
        assert len(report.violations) == 1

    def test_trust_report_to_dict(self):
        report = TrustReport(
            policy_applied="test",
            overall_trust="SERVER_VALIDATED",
            passed=True,
        )
        d = report.to_dict()
        assert d["policy_applied"] == "test"
        assert d["overall_trust"] == "SERVER_VALIDATED"
        assert d["passed"] is True

    def test_trust_report_from_dict_roundtrip(self):
        report = TrustReport(
            boundaries=[TrustBoundary(
                name="b1",
                source_layers=["browser"],
                target_layers=["server"],
                boundary_type="client_server",
            )],
            violations=[],
            transports=[TrustTransport(
                morphism_kind="API",
                source_trust="USER_INPUT",
                target_trust="USER_INPUT",
                trust_change=0,
                valid=True,
            )],
            policy_applied="p",
            overall_trust="USER_INPUT",
            passed=True,
        )
        d = report.to_dict()
        restored = TrustReport.from_dict(d)
        assert restored.passed == report.passed
        assert restored.overall_trust == report.overall_trust
        assert len(restored.boundaries) == 1
        assert len(restored.transports) == 1

    def test_trust_report_passed_default_true(self):
        report = TrustReport()
        assert report.passed is True


# ===================================================================
# WebTrustTopology tests
# ===================================================================

class TestWebTrustTopology:

    def test_client_server_boundary_exists(self):
        assert WebTrustTopology.CLIENT_SERVER_BOUNDARY is not None
        assert isinstance(WebTrustTopology.CLIENT_SERVER_BOUNDARY, TrustBoundary)

    def test_max_trust_for_client_layer(self, topo):
        assert topo.max_trust_for_layer("javascript") == "CLIENT_VALIDATED"

    def test_max_trust_for_browser_layer(self, topo):
        assert topo.max_trust_for_layer("browser") == "CLIENT_VALIDATED"

    def test_max_trust_for_server_layer(self, topo):
        assert topo.max_trust_for_layer("python") == "SERVER_VALIDATED"

    def test_max_trust_for_database_layer(self, topo):
        assert topo.max_trust_for_layer("database") == "DB_CONSTRAINT_ENFORCED"

    def test_max_trust_for_formal_layer(self, topo):
        assert topo.max_trust_for_layer("formal") == "MECHANICALLY_VERIFIED"

    def test_can_promote_same_fiber(self, topo):
        result = topo.can_promote("USER_INPUT", "SERVER_VALIDATED", crosses_boundary=False)
        assert result is True

    def test_can_promote_crosses_boundary_demotes(self, topo):
        # Promoting past CLIENT_VALIDATED across boundary is not allowed
        result = topo.can_promote("USER_INPUT", "SERVER_VALIDATED", crosses_boundary=True)
        assert result is False

    def test_can_promote_crosses_boundary_within_limit(self, topo):
        result = topo.can_promote("USER_INPUT", "CLIENT_VALIDATED", crosses_boundary=True)
        assert result is True

    def test_trust_after_transport_api_contract(self, topo):
        result = topo.trust_after_transport("API_CONTRACT", "USER_INPUT")
        assert result == "API_CONTRACT_TESTED"

    def test_trust_after_transport_orm_mapping(self, topo):
        result = topo.trust_after_transport("ORM_MAPPING", "USER_INPUT")
        assert result == "ORM_TYPE_CHECKED"

    def test_trust_after_transport_default(self, topo):
        result = topo.trust_after_transport("UNKNOWN_KIND", "SERVER_VALIDATED")
        assert result == "SERVER_VALIDATED"

    def test_validate_trust_chain_clean(self):
        chain = [
            {
                "from": "USER_INPUT",
                "to": "CLIENT_VALIDATED",
                "morphism": "client_check",
                "crosses_boundary": False,
            },
            {
                "from": "CLIENT_VALIDATED",
                "to": "SERVER_VALIDATED",
                "morphism": "server_check",
                "crosses_boundary": False,
            },
        ]
        report = WebTrustTopology.validate_trust_chain(chain)
        assert isinstance(report, TrustReport)
        assert report.passed is True
        assert len(report.violations) == 0

    def test_validate_trust_chain_with_violation(self):
        chain = [
            {
                "from": "USER_INPUT",
                "to": "SERVER_VALIDATED",
                "morphism": "illegal_hop",
                "crosses_boundary": True,
            },
        ]
        report = WebTrustTopology.validate_trust_chain(chain)
        assert len(report.violations) >= 1
        assert report.passed is False


# ===================================================================
# NeverTrustClientChecker tests
# ===================================================================

class TestNeverTrustClientChecker:

    def test_check_empty_project(self, checker):
        result = checker.check({})
        assert isinstance(result, list)

    def test_check_finds_client_only_validation(self, checker):
        project = {
            "forms": [
                {"action": "/submit", "method": "POST", "has_client_validation": True}
            ],
            "routes": [
                {"path": "/submit", "methods": ["POST"], "has_server_validation": False}
            ],
            "js_files": {},
        }
        result = checker.check(project)
        assert len(result) >= 1
        assert any("Client-only" in i.get("issue", "") for i in result)

    def test_check_clean_project(self, checker):
        project = {
            "forms": [
                {"action": "/submit", "method": "POST", "has_client_validation": True}
            ],
            "routes": [
                {"path": "/submit", "methods": ["POST"], "has_server_validation": True}
            ],
            "js_files": {},
        }
        result = checker.check(project)
        assert len(result) == 0

    def test_find_client_only_validation(self):
        checker = NeverTrustClientChecker()
        forms = [{"action": "/x", "method": "POST", "has_client_validation": True}]
        routes = [{"path": "/x", "methods": ["POST"], "has_server_validation": False}]
        issues = checker._find_client_only_validation(forms, routes)
        assert len(issues) >= 1

    def test_find_js_auth_without_server(self):
        checker = NeverTrustClientChecker()
        js_files = {"app.js": {"has_auth_check": True}}
        routes = [
            {"path": "/admin", "methods": ["GET"], "requires_auth": True, "has_server_validation": False}
        ]
        issues = checker._find_js_auth_without_server(js_files, routes)
        assert len(issues) >= 1


# ===================================================================
# TrustPolicyEngine tests
# ===================================================================

class TestTrustPolicyEngine:

    def test_default_web_policy_exists(self):
        assert TrustPolicyEngine.DEFAULT_WEB_POLICY is not None
        assert isinstance(TrustPolicyEngine.DEFAULT_WEB_POLICY, TrustPolicy)

    def test_apply_policy_empty_evidence(self, policy_engine):
        policy = TrustPolicyEngine.DEFAULT_WEB_POLICY
        result = policy_engine.apply_policy(policy, [])
        assert result == []

    def test_apply_policy_filters_low_trust(self, policy_engine):
        policy = TrustPolicy(
            name="strict",
            rules=[TrustRule(
                condition="requires_server_validation",
                action="deny",
                trust_floor="SERVER_VALIDATED",
            )],
            default_action="allow",
        )
        evidence = [
            {"trust_level": "USER_INPUT", "condition": "requires_server_validation"},
        ]
        result = policy_engine.apply_policy(policy, evidence)
        # Denied items are removed from the result
        assert len(result) == 0

    def test_apply_policy_allows_high_trust(self, policy_engine):
        policy = TrustPolicy(
            name="strict",
            rules=[TrustRule(
                condition="requires_server_validation",
                action="deny",
                trust_floor="SERVER_VALIDATED",
            )],
            default_action="allow",
        )
        evidence = [
            {"trust_level": "MECHANICALLY_VERIFIED", "condition": "other"},
        ]
        result = policy_engine.apply_policy(policy, evidence)
        assert len(result) == 1
        assert result[0]["action_taken"] == "allow"


# ===================================================================
# TrustAlgebra tests
# ===================================================================

class TestTrustAlgebra:

    def test_join_same_levels(self):
        assert TrustAlgebra.join("USER_INPUT", "USER_INPUT") == "USER_INPUT"

    def test_join_different_levels(self):
        result = TrustAlgebra.join("USER_INPUT", "SERVER_VALIDATED")
        assert result == "SERVER_VALIDATED"

    def test_join_commutativity(self):
        pairs = [
            ("USER_INPUT", "SERVER_VALIDATED"),
            ("CSS_LINTED", "ORM_TYPE_CHECKED"),
            ("BROWSER_TESTED", "MECHANICALLY_VERIFIED"),
        ]
        for a, b in pairs:
            assert TrustAlgebra.join(a, b) == TrustAlgebra.join(b, a)

    def test_meet_same_levels(self):
        assert TrustAlgebra.meet("SERVER_VALIDATED", "SERVER_VALIDATED") == "SERVER_VALIDATED"

    def test_meet_different_levels(self):
        result = TrustAlgebra.meet("USER_INPUT", "SERVER_VALIDATED")
        assert result == "USER_INPUT"

    def test_meet_commutativity(self):
        pairs = [
            ("USER_INPUT", "SERVER_VALIDATED"),
            ("CSS_LINTED", "MECHANICALLY_VERIFIED"),
            ("ORM_TYPE_CHECKED", "BROWSER_TESTED"),
        ]
        for a, b in pairs:
            assert TrustAlgebra.meet(a, b) == TrustAlgebra.meet(b, a)

    def test_transport_api_contract(self):
        result = TrustAlgebra.transport("SERVER_VALIDATED", "API_CONTRACT")
        assert result == "API_CONTRACT_TESTED"

    def test_transport_default(self):
        result = TrustAlgebra.transport("SERVER_VALIDATED", "UNKNOWN_KIND")
        assert result == "SERVER_VALIDATED"

    def test_transport_orm_mapping_with_server_validated(self):
        result = TrustAlgebra.transport("SERVER_VALIDATED", "ORM_MAPPING")
        assert result == "ORM_TYPE_CHECKED"

    def test_transport_orm_mapping_low_trust(self):
        result = TrustAlgebra.transport("USER_INPUT", "ORM_MAPPING")
        assert result == "USER_INPUT"

    def test_transport_db_constraint(self):
        result = TrustAlgebra.transport("USER_INPUT", "DB_CONSTRAINT")
        assert result == "DB_CONSTRAINT_ENFORCED"

    def test_compose_single_morphism(self):
        result = TrustAlgebra.compose(["DB_CONSTRAINT"])
        assert result == "DB_CONSTRAINT_ENFORCED"

    def test_compose_multiple_morphisms(self):
        result = TrustAlgebra.compose(["API_CONTRACT", "DB_CONSTRAINT"])
        # Starting from USER_INPUT -> API_CONTRACT -> API_CONTRACT_TESTED
        # Then DB_CONSTRAINT -> DB_CONSTRAINT_ENFORCED
        assert result == "DB_CONSTRAINT_ENFORCED"

    def test_compose_empty(self):
        result = TrustAlgebra.compose([])
        assert result == "USER_INPUT"


# ===================================================================
# TrustCertificate tests
# ===================================================================

class TestTrustCertificate:

    def test_trust_certificate_creation(self):
        cert = TrustCertificate(
            claim="route /index is server_validated",
            trust_level="SERVER_VALIDATED",
            evidence_ids=["e1", "e2"],
            transport_chain=["API_CONTRACT", "ORM_MAPPING"],
            valid_until=time.time() + 3600,
        )
        assert cert.claim == "route /index is server_validated"
        assert cert.trust_level == "SERVER_VALIDATED"
        assert len(cert.evidence_ids) == 2

    def test_trust_certificate_to_dict(self):
        cert = TrustCertificate(
            claim="test",
            trust_level="USER_INPUT",
            evidence_ids=["e1"],
            transport_chain=["API_CONTRACT"],
            valid_until=time.time() + 3600,
        )
        d = cert.to_dict()
        assert "claim" in d
        assert "trust_level" in d
        assert "evidence_ids" in d
        assert "transport_chain" in d
        assert "valid_until" in d
        assert "issued_at" in d
        assert "cert_id" in d

    def test_trust_certificate_from_dict_roundtrip(self):
        cert = TrustCertificate(
            claim="test",
            trust_level="SERVER_VALIDATED",
            evidence_ids=["e1", "e2"],
            transport_chain=["API_CONTRACT"],
            valid_until=time.time() + 3600,
        )
        d = cert.to_dict()
        restored = TrustCertificate.from_dict(d)
        assert restored.claim == cert.claim
        assert restored.trust_level == cert.trust_level
        assert restored.evidence_ids == cert.evidence_ids
        assert restored.transport_chain == cert.transport_chain

    def test_trust_certificate_has_cert_id(self):
        cert = TrustCertificate(
            claim="x",
            trust_level="USER_INPUT",
            evidence_ids=[],
            transport_chain=[],
            valid_until=time.time() + 100,
        )
        assert cert.cert_id is not None
        assert len(cert.cert_id) > 0

    def test_trust_certificate_has_issued_at(self):
        before = time.time()
        cert = TrustCertificate(
            claim="x",
            trust_level="USER_INPUT",
            evidence_ids=[],
            transport_chain=[],
            valid_until=time.time() + 100,
        )
        after = time.time()
        assert before <= cert.issued_at <= after


# ===================================================================
# CertificateEmitter tests
# ===================================================================

class TestCertificateEmitter:

    def test_emit_returns_certificate(self, emitter):
        cert = emitter.emit(
            "test claim",
            {"combined_trust": "SERVER_VALIDATED", "evidence_items": [{"id": "e1"}]},
            ["ORM_MAPPING"],
        )
        assert isinstance(cert, TrustCertificate)

    def test_emit_cert_trust_level(self, emitter):
        cert = emitter.emit(
            "claim",
            {"combined_trust": "SERVER_VALIDATED", "evidence_items": [{"id": "e1"}]},
            ["API_CONTRACT"],
        )
        assert cert.trust_level == "SERVER_VALIDATED"

    def test_verify_valid_cert(self, emitter):
        cert = emitter.emit(
            "claim",
            {"combined_trust": "SERVER_VALIDATED", "evidence_items": [{"id": "e1"}]},
            ["API_CONTRACT"],
        )
        current = [{"id": "e1"}]
        assert emitter.verify(cert, current) is True

    def test_verify_expired_cert(self, emitter):
        cert = TrustCertificate(
            claim="expired",
            trust_level="USER_INPUT",
            evidence_ids=["e1"],
            transport_chain=[],
            valid_until=time.time() - 100,
        )
        assert emitter.verify(cert, [{"id": "e1"}]) is False

    def test_verify_missing_evidence(self, emitter):
        cert = emitter.emit(
            "claim",
            {"combined_trust": "SERVER_VALIDATED", "evidence_items": [{"id": "e1"}, {"id": "e2"}]},
            [],
        )
        # Only e1 is available, e2 is missing
        assert emitter.verify(cert, [{"id": "e1"}]) is False
