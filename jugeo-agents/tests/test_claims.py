"""Tests for jugeo_agents.core.claims — RegexClaimExtractor, HeuristicContradictionDetector."""

import pytest

from jugeo_agents.types import FactualClaim, ObstructionKind, TrustLevel
from jugeo_agents.core.claims import (
    ClaimNormalizer,
    HeuristicContradictionDetector,
    RegexClaimExtractor,
)


# ---------------------------------------------------------------------------
# RegexClaimExtractor
# ---------------------------------------------------------------------------


def test_extract_revenue_claim():
    extractor = RegexClaimExtractor()
    text = "Acme Corp revenue was $4.2B in 2024."
    claims = extractor.extract(text, agent_id="a1")
    assert len(claims) >= 1
    revenue_claims = [c for c in claims if "revenue" in c.predicate or "numeric" in c.predicate]
    assert len(revenue_claims) >= 1
    assert revenue_claims[0].source_agent == "a1"


def test_extract_founded_year():
    extractor = RegexClaimExtractor()
    text = "Tesla was founded in 2003."
    claims = extractor.extract(text, agent_id="researcher")
    founded = [c for c in claims if c.predicate == "founded_in"]
    assert len(founded) >= 1
    assert "2003" in founded[0].value


def test_extract_employee_count():
    extractor = RegexClaimExtractor()
    text = "Google has 180,000 employees worldwide."
    claims = extractor.extract(text, agent_id="a1")
    emp_claims = [c for c in claims if c.predicate == "employee_count"]
    assert len(emp_claims) >= 1
    assert "180,000" in emp_claims[0].value


def test_extract_percentage_change():
    extractor = RegexClaimExtractor()
    text = "Apple grew by 15% last quarter."
    claims = extractor.extract(text, agent_id="a1")
    pct_claims = [c for c in claims if c.predicate == "percentage_change"]
    assert len(pct_claims) >= 1
    assert "15%" in pct_claims[0].value


def test_extract_entity_founded_by():
    extractor = RegexClaimExtractor()
    text = "Amazon was founded by Jeff Bezos."
    claims = extractor.extract(text, agent_id="a1")
    founder = [c for c in claims if c.predicate == "founded_by"]
    assert len(founder) >= 1
    assert "Jeff Bezos" in founder[0].value


def test_extract_executive():
    extractor = RegexClaimExtractor()
    text = "Tim Cook is the CEO of Apple."
    claims = extractor.extract(text, agent_id="a1")
    exec_claims = [c for c in claims if c.predicate == "has_executive"]
    assert len(exec_claims) >= 1


def test_extract_comparative():
    extractor = RegexClaimExtractor()
    text = "Google is larger than Microsoft."
    claims = extractor.extract(text, agent_id="a1")
    comp = [c for c in claims if c.predicate == "comparative"]
    assert len(comp) >= 1


def test_extract_empty_text():
    extractor = RegexClaimExtractor()
    claims = extractor.extract("")
    assert claims == []


def test_extract_no_claims_text():
    extractor = RegexClaimExtractor()
    # Lowercase starting words won't match patterns that require [A-Z]
    claims = extractor.extract("this is a simple sentence without named entities.")
    # May or may not produce claims depending on patterns, but should not crash
    assert isinstance(claims, list)


# ---------------------------------------------------------------------------
# ClaimNormalizer
# ---------------------------------------------------------------------------


def test_normalize_currency():
    result = ClaimNormalizer.normalize("$4.2M revenue")
    # normalize is text-only (lowercase/strip), not a number parser
    assert result == "$4.2m revenue"


def test_normalize_quarter():
    result = ClaimNormalizer.normalize("Q3 2024 earnings")
    assert result == "q3 2024 earnings"


def test_normalize_number():
    assert ClaimNormalizer.normalize_number("$4.2M") == pytest.approx(4_200_000.0)
    assert ClaimNormalizer.normalize_number("180,000") == pytest.approx(180_000.0)
    assert ClaimNormalizer.normalize_number("15%") == pytest.approx(15.0)
    assert ClaimNormalizer.normalize_number("junk") is None


# ---------------------------------------------------------------------------
# HeuristicContradictionDetector — quantitative
# ---------------------------------------------------------------------------


def test_detect_quantitative_contradiction():
    detector = HeuristicContradictionDetector()
    claims_a = [
        FactualClaim(
            text="Acme Corp revenue was $4.2B",
            subject="Acme Corp",
            predicate="has_revenue",
            value="$4.2B",
            source_agent="a1",
        )
    ]
    claims_b = [
        FactualClaim(
            text="Acme Corp revenue was $2.1B",
            subject="Acme Corp",
            predicate="has_revenue",
            value="$2.1B",
            source_agent="a2",
        )
    ]
    contradictions = detector.detect(claims_a, claims_b)
    assert len(contradictions) >= 1
    assert contradictions[0].kind == ObstructionKind.QUANTITATIVE_CONTRADICTION


def test_no_contradiction_when_numbers_agree():
    detector = HeuristicContradictionDetector()
    claims_a = [
        FactualClaim(
            text="Revenue was $5B",
            subject="MegaCorp",
            predicate="has_revenue",
            value="$5B",
            source_agent="a1",
        )
    ]
    claims_b = [
        FactualClaim(
            text="Revenue was $5B",
            subject="MegaCorp",
            predicate="has_revenue",
            value="$5B",
            source_agent="a2",
        )
    ]
    contradictions = detector.detect(claims_a, claims_b)
    assert len(contradictions) == 0


# ---------------------------------------------------------------------------
# HeuristicContradictionDetector — temporal
# ---------------------------------------------------------------------------


def test_detect_temporal_contradiction():
    detector = HeuristicContradictionDetector()
    claims_a = [
        FactualClaim(
            text="Tesla was founded in 2003",
            subject="Tesla",
            predicate="founded_in",
            value="2003",
            source_agent="a1",
        )
    ]
    claims_b = [
        FactualClaim(
            text="Tesla was founded in 2005",
            subject="Tesla",
            predicate="founded_in",
            value="2005",
            source_agent="a2",
        )
    ]
    contradictions = detector.detect(claims_a, claims_b)
    assert len(contradictions) >= 1
    assert contradictions[0].kind == ObstructionKind.TEMPORAL_CONTRADICTION


# ---------------------------------------------------------------------------
# HeuristicContradictionDetector — directional
# ---------------------------------------------------------------------------


def test_detect_directional_contradiction():
    detector = HeuristicContradictionDetector()
    # Both claims share the same subject and a compatible predicate, but use
    # opposing directional language (grew vs declined).
    claims_a = [
        FactualClaim(
            text="Acme Corp revenue grew by 15% in 2024",
            subject="Acme Corp",
            predicate="has_revenue",
            value="$4B",
            source_agent="a1",
        )
    ]
    claims_b = [
        FactualClaim(
            text="Acme Corp revenue declined by 10% in 2024",
            subject="Acme Corp",
            predicate="has_revenue",
            value="$3B",
            source_agent="a2",
        )
    ]
    contradictions = detector.detect(claims_a, claims_b)
    assert len(contradictions) >= 1
    # Should detect at least a directional or quantitative contradiction
    kinds = {c.kind for c in contradictions}
    assert (
        ObstructionKind.DIRECTIONAL_CONTRADICTION in kinds
        or ObstructionKind.QUANTITATIVE_CONTRADICTION in kinds
    )


# ---------------------------------------------------------------------------
# Empty / no-overlap cases
# ---------------------------------------------------------------------------


def test_detect_no_contradictions_different_subjects():
    detector = HeuristicContradictionDetector()
    claims_a = [
        FactualClaim(
            text="Apple revenue was $100B",
            subject="Apple",
            predicate="has_revenue",
            value="$100B",
            source_agent="a1",
        )
    ]
    claims_b = [
        FactualClaim(
            text="Google revenue was $200B",
            subject="Google",
            predicate="has_revenue",
            value="$200B",
            source_agent="a2",
        )
    ]
    contradictions = detector.detect(claims_a, claims_b)
    assert len(contradictions) == 0


def test_detect_empty_claims():
    detector = HeuristicContradictionDetector()
    assert detector.detect([], []) == []
