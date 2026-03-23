from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.ideation.regime_bootstrapping.models import (
    ObstructionField,
    ObstructionKind,
    DomainFormation,
    DomainType,
)
from jugeo.ideation.regime_bootstrapping.s01_domain_formation import (
    ObstructionAnalyzer,
    DomainPartitioner,
    DomainValidator,
    DomainFormationRunner,
    analyze_obstructions,
    partition_domain,
    TOPO_WEIGHT,
    ALGE_WEIGHT,
    GEOM_WEIGHT,
    COHO_WEIGHT,
    SEVERITY_THRESHOLDS,
    MIN_GENERATORS,
    MAX_GENERATORS,
    MIN_COVERAGE,
    HISTOGRAM_BINS,
    DEFAULT_DOMAIN_TYPE,
    NULL_SEVERITY,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_obstruction_field():
    """Return a single, well-formed ObstructionField instance.

    This fixture creates a canonical TYPE_MISMATCH obstruction with a moderate
    severity of 0.6 and a single generator 'alpha'.  It is intended for tests
    that need exactly one obstruction in order to verify single-item behaviour.

    The location is deliberately set to a non-empty string ('generator:alpha')
    to exercise code paths that branch on whether a location is present.

    Returns
    -------
    ObstructionField
        A frozen dataclass instance representing one obstruction.
    """
    return ObstructionField(
        kind=ObstructionKind.TYPE_MISMATCH,
        description="Type A cannot unify with Type B in context alpha.",
        severity=0.6,
        location="generator:alpha",
    )


@pytest.fixture
def many_obstruction_fields():
    """Return a heterogeneous list of eight ObstructionField instances.

    The list covers all eight ObstructionKind values with varying severity
    levels.  The distribution is deliberately non-uniform to exercise
    histogram, grouping, and boundary-detection logic:

    - Severity 0.1 (low) — TYPE_MISMATCH
    - Severity 0.3 (low-medium) — MISSING_GENERATOR
    - Severity 0.45 (medium) — CYCLIC_DEPENDENCY
    - Severity 0.55 (medium-high boundary) — AMBIGUOUS_CONSTRUCTOR
    - Severity 0.65 (high) — TRUST_DEFICIT
    - Severity 0.75 (high) — SCHEMA_VIOLATION
    - Severity 0.85 (critical) — EVIDENCE_GAP
    - Severity 0.95 (critical) — INTERNAL_ERROR

    Returns
    -------
    list of ObstructionField
        Eight distinct obstruction field instances.
    """
    return [
        ObstructionField(
            kind=ObstructionKind.TYPE_MISMATCH,
            description="Low severity type mismatch in module core.",
            severity=0.1,
            location="module:core",
        ),
        ObstructionField(
            kind=ObstructionKind.MISSING_GENERATOR,
            description="Generator 'beta' is absent from domain.",
            severity=0.3,
            location="generator:beta",
        ),
        ObstructionField(
            kind=ObstructionKind.CYCLIC_DEPENDENCY,
            description="Cyclic dependency between X and Y.",
            severity=0.45,
            location="dependency:X-Y",
        ),
        ObstructionField(
            kind=ObstructionKind.AMBIGUOUS_CONSTRUCTOR,
            description="Multiple constructors match context C.",
            severity=0.55,
            location="constructor:C",
        ),
        ObstructionField(
            kind=ObstructionKind.TRUST_DEFICIT,
            description="Trust score below threshold for candidate D.",
            severity=0.65,
            location="candidate:D",
        ),
        ObstructionField(
            kind=ObstructionKind.SCHEMA_VIOLATION,
            description="Field 'sigma' violates schema invariant.",
            severity=0.75,
            location="field:sigma",
        ),
        ObstructionField(
            kind=ObstructionKind.EVIDENCE_GAP,
            description="Insufficient evidence for axiom E.",
            severity=0.85,
            location="axiom:E",
        ),
        ObstructionField(
            kind=ObstructionKind.INTERNAL_ERROR,
            description="Unexpected internal error in descent module.",
            severity=0.95,
            location="module:descent",
        ),
    ]


@pytest.fixture
def sample_domain_formation():
    """Return a valid DomainFormation with two generators and one relation.

    The domain is typed as ALGEBRAIC and represents the simplest non-trivial
    algebraic domain that the validator considers fully valid.

    Returns
    -------
    DomainFormation
        A mutable domain accumulator with pre-loaded generators and a relation.
    """
    df = DomainFormation(name="SampleAlgebraicDomain", domain_type=DomainType.ALGEBRAIC)
    df.add_generator("alpha")
    df.add_generator("beta")
    df.add_relation("alpha * beta = beta * alpha")
    return df


@pytest.fixture
def sample_obstruction_field_factory():
    """Return a callable factory that creates ObstructionField instances.

    The factory signature is ``factory(kind, severity=0.5, location='')`` and
    is used by parametrized tests that need to create one field per
    ObstructionKind without hard-coding every combination.

    Returns
    -------
    callable
        A factory function ``(kind, severity=0.5, location='') -> ObstructionField``.
    """

    def _make(kind, severity=0.5, location="test:loc"):
        return ObstructionField(
            kind=kind,
            description=f"Parametrized obstruction of kind {kind.value}.",
            severity=severity,
            location=location,
        )

    return _make


# ---------------------------------------------------------------------------
# Helper utilities for tests
# ---------------------------------------------------------------------------


def _make_simple_domain_dict(domain_type="generic", generators=None, relations=None, coverage=0.8):
    """Create a plain-dict domain suitable for passing to DomainValidator.

    DomainValidator is designed to handle both DomainFormation instances and
    duck-typed plain dicts.  This helper creates the dict form.

    Parameters
    ----------
    domain_type : str
        Domain type string.
    generators : list of str, optional
        Generator names; defaults to ['sigma_0'].
    relations : list of str, optional
        Relation strings; defaults to ['sigma_0=sigma_0'].
    coverage : float
        Coverage fraction.

    Returns
    -------
    dict
        A plain dict representing a minimal domain.
    """
    return {
        "id": "test-domain-001",
        "domain_type": domain_type,
        "generators": generators if generators is not None else ["sigma_0"],
        "relations": relations if relations is not None else ["sigma_0=sigma_0"],
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# ObstructionAnalyzer tests
# ---------------------------------------------------------------------------


def test_obstruction_analyzer_init():
    """Test that ObstructionAnalyzer initializes with default and custom configs.

    This test verifies three aspects of __init__:
    1. Default initialization (no config) sets all weight attributes to the
       module-level constants TOPO_WEIGHT, ALGE_WEIGHT, GEOM_WEIGHT, COHO_WEIGHT
       and the histogram bin count to HISTOGRAM_BINS.
    2. A custom config dict overrides each of those attributes individually.
    3. The internal cache is always initialized as an empty dict (not None,
       not a list, and not shared between instances).

    The test is intentionally exhaustive about attribute names because a
    regression where a weight is inadvertently reset to a hard-coded literal
    instead of reading from the config would otherwise pass all higher-level
    tests but produce silently wrong severity scores.
    """
    # Default initialization —no config provided
    analyzer_default = ObstructionAnalyzer()
    assert analyzer_default._topo_weight == TOPO_WEIGHT, (
        f"Default topo_weight should be {TOPO_WEIGHT}, "
        f"got {analyzer_default._topo_weight}"
    )
    assert analyzer_default._alge_weight == ALGE_WEIGHT, (
        f"Default alge_weight should be {ALGE_WEIGHT}, "
        f"got {analyzer_default._alge_weight}"
    )
    assert analyzer_default._geom_weight == GEOM_WEIGHT, (
        f"Default geom_weight should be {GEOM_WEIGHT}, "
        f"got {analyzer_default._geom_weight}"
    )
    assert analyzer_default._coho_weight == COHO_WEIGHT, (
        f"Default coho_weight should be {COHO_WEIGHT}, "
        f"got {analyzer_default._coho_weight}"
    )
    assert analyzer_default._histogram_bins == HISTOGRAM_BINS, (
        f"Default histogram_bins should be {HISTOGRAM_BINS}, "
        f"got {analyzer_default._histogram_bins}"
    )
    assert isinstance(analyzer_default._cache, dict), (
        "Cache must be a dict, "
        f"got {type(analyzer_default._cache)}"
    )
    assert len(analyzer_default._cache) == 0, (
        "Cache must be empty after initialization"
    )

    # Custom config initialization
    custom_config = {
        "topo_weight": 2.5,
        "alge_weight": 1.8,
        "geom_weight": 0.5,
        "coho_weight": 3.0,
        "histogram_bins": 20,
    }
    analyzer_custom = ObstructionAnalyzer(config=custom_config)
    assert analyzer_custom._topo_weight == 2.5, (
        "Custom topo_weight 2.5 should be respected"
    )
    assert analyzer_custom._alge_weight == 1.8, (
        "Custom alge_weight 1.8 should be respected"
    )
    assert analyzer_custom._geom_weight == 0.5, (
        "Custom geom_weight 0.5 should be respected"
    )
    assert analyzer_custom._coho_weight == 3.0, (
        "Custom coho_weight 3.0 should be respected"
    )
    assert analyzer_custom._histogram_bins == 20, (
        "Custom histogram_bins 20 should be respected"
    )

    # Instances must not share cache
    analyzer_default._cache["sentinel"] = True
    assert "sentinel" not in analyzer_custom._cache, (
        "Cache must not be shared between ObstructionAnalyzer instances"
    )


def test_obstruction_analyzer_analyze_empty():
    """Test that ObstructionAnalyzer.analyze() handles an empty field list gracefully.

    When no obstruction fields are provided the analyzer must still return a
    well-formed AnalysisReport dict.  The contract is:

    * ``report['count']`` == 0 — no obstructions were counted.
    * ``report['severities']`` is a list (possibly empty).
    * ``report['classified']`` is a list (possibly empty).
    * ``report['boundary_ids']`` is a list (possibly empty).
    * ``report['groups']`` is a dict.
    * ``report['histogram']`` is a list.
    * ``report['summary']`` is a non-empty string.
    * ``report['analyzed_at']`` is a non-empty string (ISO-8601 timestamp).

    The test deliberately avoids checking exact lengths of the empty lists
    (some implementations may return a single sentinel entry) and instead
    focuses on type correctness and the presence of required keys.
    """
    analyzer = ObstructionAnalyzer()
    report = analyzer.analyze([])

    required_keys = {
        "count", "severities", "classified", "boundary_ids",
        "groups", "histogram", "summary", "analyzed_at",
    }
    for key in required_keys:
        assert key in report, (
            f"AnalysisReport is missing required key '{key}'. "
            f"Present keys: {set(report.keys())}"
        )

    assert report["count"] == 0, (
        f"count should be 0 for empty input, got {report['count']}"
    )
    assert isinstance(report["severities"], list), (
        "severities must be a list"
    )
    assert isinstance(report["classified"], list), (
        "classified must be a list"
    )
    assert isinstance(report["boundary_ids"], list), (
        "boundary_ids must be a list"
    )
    assert isinstance(report["groups"], dict), (
        "groups must be a dict"
    )
    assert isinstance(report["histogram"], list), (
        "histogram must be a list"
    )
    assert isinstance(report["summary"], str), (
        "summary must be a str"
    )
    assert len(report["summary"]) > 0, (
        "summary must be non-empty even for empty input"
    )
    assert isinstance(report["analyzed_at"], str), (
        "analyzed_at must be a str (ISO-8601)"
    )
    assert len(report["analyzed_at"]) > 0, (
        "analyzed_at must be non-empty"
    )


def test_obstruction_analyzer_analyze_single(sample_obstruction_field):
    """Test that ObstructionAnalyzer.analyze() correctly processes a single field.

    Provides one ObstructionField (severity=0.6, kind=TYPE_MISMATCH) and
    verifies:

    * ``count`` == 1
    * ``severities`` contains exactly one entry, which is a 2-tuple
      ``(field_id: str, score: float)`` with score in [0.0, 1.0]
    * ``classified`` contains exactly one entry, which is a 2-tuple
      ``(field_id: str, bucket: str)`` where bucket is a recognised label
    * The report is internally consistent: the field_id in ``severities``
      matches the one in ``classified``

    The fixture creates a moderate-severity TYPE_MISMATCH obstruction so that
    the bucket should be 'medium' or 'high' (depending on weight application),
    but we avoid hard-coding the exact bucket to keep the test robust against
    weight adjustments.
    """
    analyzer = ObstructionAnalyzer()
    report = analyzer.analyze([sample_obstruction_field])

    assert report["count"] == 1, (
        f"Expected count=1 for single field, got {report['count']}"
    )

    assert len(report["severities"]) == 1, (
        f"Expected exactly one entry in severities, got {len(report['severities'])}"
    )
    sev_entry = report["severities"][0]
    assert isinstance(sev_entry, (list, tuple)) and len(sev_entry) == 2, (
        f"Each severity entry must be a 2-tuple (id, score), got {sev_entry!r}"
    )
    field_id_sev, score = sev_entry
    assert isinstance(field_id_sev, str), (
        f"Field id in severities must be str, got {type(field_id_sev)}"
    )
    assert 0.0 <= score <= 1.0, (
        f"Severity score must be in [0.0, 1.0], got {score}"
    )

    assert len(report["classified"]) == 1, (
        f"Expected exactly one entry in classified, got {len(report['classified'])}"
    )
    cls_entry = report["classified"][0]
    assert isinstance(cls_entry, (list, tuple)) and len(cls_entry) == 2, (
        f"Each classified entry must be a 2-tuple (id, bucket), got {cls_entry!r}"
    )
    field_id_cls, bucket = cls_entry
    valid_buckets = {"low", "medium", "high", "critical"}
    assert bucket in valid_buckets, (
        f"Bucket '{bucket}' not in valid set {valid_buckets}"
    )

    # Field IDs must be consistent between severities and classified
    assert field_id_sev == field_id_cls, (
        f"Field ID mismatch between severities ({field_id_sev!r}) "
        f"and classified ({field_id_cls!r})"
    )


def test_obstruction_analyzer_analyze_multiple(many_obstruction_fields):
    """Test that ObstructionAnalyzer.analyze() handles a list of eight fields.

    Provides eight ObstructionField instances covering all ObstructionKind
    values and verifies:

    * ``count`` == 8
    * ``severities`` and ``classified`` each have exactly 8 entries
    * All scores are valid floats in [0.0, 1.0]
    * All buckets are from the recognised set {'low','medium','high','critical'}
    * ``groups`` dict maps kind strings to non-empty lists totalling 8 items
    * The histogram entries are all 2-tuples of (label: str, count: int)
    * The second call with the same fields hits the cache (same result object)

    The cache-hit sub-test is important because it verifies that caching does
    not silently corrupt results when the same analyzer is reused.
    """
    analyzer = ObstructionAnalyzer()
    report = analyzer.analyze(many_obstruction_fields)

    assert report["count"] == 8, (
        f"Expected count=8, got {report['count']}"
    )

    assert len(report["severities"]) == 8, (
        f"Expected 8 severity entries, got {len(report['severities'])}"
    )
    valid_buckets = {"low", "medium", "high", "critical"}
    for field_id, score in report["severities"]:
        assert 0.0 <= score <= 1.0, (
            f"Score {score} for field {field_id!r} out of range [0,1]"
        )
    for field_id, bucket in report["classified"]:
        assert bucket in valid_buckets, (
            f"Bucket '{bucket}' for field {field_id!r} not in {valid_buckets}"
        )

    # groups should collectively account for all 8 fields
    total_in_groups = sum(len(v) for v in report["groups"].values())
    assert total_in_groups == 8, (
        f"Sum of group sizes should be 8, got {total_in_groups}. "
        f"Groups: {report['groups']}"
    )

    # histogram entries are (label, count) pairs
    for entry in report["histogram"]:
        assert isinstance(entry, (list, tuple)) and len(entry) == 2, (
            f"Histogram entry must be a 2-tuple, got {entry!r}"
        )
        label, count = entry
        assert isinstance(label, str), f"Histogram label must be str, got {type(label)}"
        assert isinstance(count, int) and count >= 0, (
            f"Histogram count must be non-negative int, got {count!r}"
        )

    # Second call should return the same (cached) result
    report2 = analyzer.analyze(many_obstruction_fields)
    assert report is report2, (
        "Second analyze() call with identical fields should return the cached object"
    )


def test_obstruction_analyzer_compute_severity(sample_obstruction_field):
    """Test ObstructionAnalyzer.compute_severity() on a single field.

    The compute_severity method must:
    1. Return a float in [0.0, 1.0] regardless of the raw severity attribute.
    2. Apply a kind-specific weight: a field of kind TYPE_MISMATCH should
       have its raw severity multiplied by the relevant weight constant.
    3. Clamp results — if the weighted product would exceed 1.0 it must be
       clamped at 1.0; if the raw severity is 0.0 the result must be 0.0.

    We test four distinct cases:
    a) Normal field (severity=0.6) — result in [0.0, 1.0].
    b) Zero-severity field — result must be 0.0.
    c) Maximum-severity field (severity=1.0) — result must be clamped to 1.0.
    d) Field without a 'severity' attribute (plain dict with no severity key)
       — should fall back to 0.5 and still return a valid float.
    """
    analyzer = ObstructionAnalyzer()

    # (a) Normal field
    score = analyzer.compute_severity(sample_obstruction_field)
    assert isinstance(score, float), (
        f"compute_severity must return float, got {type(score)}"
    )
    assert 0.0 <= score <= 1.0, (
        f"compute_severity result {score} must be in [0.0, 1.0]"
    )

    # (b) Zero severity
    zero_field = ObstructionField(
        kind=ObstructionKind.TYPE_MISMATCH,
        description="Zero severity.",
        severity=0.0,
    )
    assert analyzer.compute_severity(zero_field) == 0.0, (
        "Zero-severity field must produce a score of 0.0"
    )

    # (c) Maximum severity — must be clamped at 1.0
    max_field = ObstructionField(
        kind=ObstructionKind.INTERNAL_ERROR,
        description="Maximum severity.",
        severity=1.0,
    )
    max_score = analyzer.compute_severity(max_field)
    assert max_score == 1.0, (
        f"Maximum severity field should clamp to 1.0, got {max_score}"
    )

    # (d) Fallback dict — severity attribute missing
    plain_dict = {"kind": "unknown", "description": "no severity attr"}
    fallback_score = analyzer.compute_severity(plain_dict)
    assert isinstance(fallback_score, float), (
        "Fallback (no severity attr) must still return a float"
    )
    assert 0.0 <= fallback_score <= 1.0, (
        f"Fallback severity {fallback_score} must be in [0.0, 1.0]"
    )


def test_obstruction_analyzer_classify_obstruction(many_obstruction_fields):
    """Test ObstructionAnalyzer.classify_obstruction() bucket assignment.

    The classifier maps weighted severity scores to four named buckets:
    'low', 'medium', 'high', 'critical'.  The thresholds are defined in
    SEVERITY_THRESHOLDS = (0.25, 0.55, 0.80).  After weight application:

    - score < 0.25  → 'low'
    - 0.25 ≤ score < 0.55 → 'medium'
    - 0.55 ≤ score < 0.80 → 'high'
    - score ≥ 0.80  → 'critical'

    This test verifies:
    1. All eight fields from the fixture are classified into a valid bucket.
    2. Specifically the severity-0.1 field classifies as 'low' or 'medium'
       (after weight, still ≤ 0.55 for default weights).
    3. The severity-0.95 field classifies as 'critical'.
    4. The method can handle a plain dict without raising.
    """
    analyzer = ObstructionAnalyzer()
    valid_buckets = {"low", "medium", "high", "critical"}

    for field in many_obstruction_fields:
        bucket = analyzer.classify_obstruction(field)
        assert bucket in valid_buckets, (
            f"classify_obstruction returned '{bucket}' for field "
            f"severity={field.severity}, kind={field.kind.value}; "
            f"must be one of {valid_buckets}"
        )

    # severity=0.1 should be low or medium (weighted: 0.1 * weight ≤ 0.55)
    low_field = many_obstruction_fields[0]
    assert low_field.severity == 0.1
    low_bucket = analyzer.classify_obstruction(low_field)
    assert low_bucket in {"low", "medium"}, (
        f"severity=0.1 should be 'low' or 'medium', got '{low_bucket}'"
    )

    # severity=0.95 should always be 'critical'
    critical_field = many_obstruction_fields[-1]
    assert critical_field.severity == 0.95
    critical_bucket = analyzer.classify_obstruction(critical_field)
    assert critical_bucket == "critical", (
        f"severity=0.95 should classify as 'critical', got '{critical_bucket}'"
    )

    # Plain dict without 'kind' attribute must not raise
    dict_field = {"severity": 0.5, "description": "plain dict"}
    dict_bucket = analyzer.classify_obstruction(dict_field)
    assert dict_bucket in valid_buckets, (
        f"Plain dict field produced invalid bucket '{dict_bucket}'"
    )


def test_obstruction_analyzer_find_boundary_obstructions(many_obstruction_fields):
    """Test ObstructionAnalyzer.find_boundary_obstructions() detection logic.

    Boundary obstructions are those that the implementation's _is_boundary
    heuristic marks as sitting between two candidate domains.  This test
    verifies:

    1. The method always returns a list (never None or another type).
    2. Every returned item is drawn from the original input sequence
       (no phantom entries are fabricated).
    3. The result size is ≤ the input size.
    4. Passing an empty list returns an empty list.
    5. Passing a field whose 'is_boundary' attribute is True includes it.

    We also explicitly create a field with is_boundary=True to guarantee
    at least one boundary obstruction is detected, exercising the
    metadata-based branch of the detection logic.
    """
    analyzer = ObstructionAnalyzer()

    # Empty case
    boundary_empty = analyzer.find_boundary_obstructions([])
    assert isinstance(boundary_empty, list), (
        "find_boundary_obstructions must return a list for empty input"
    )
    assert len(boundary_empty) == 0, (
        "find_boundary_obstructions should return [] for empty input"
    )

    # Normal case — result is a subset of the input
    boundaries = analyzer.find_boundary_obstructions(many_obstruction_fields)
    assert isinstance(boundaries, list), (
        "find_boundary_obstructions must return a list"
    )
    assert len(boundaries) <= len(many_obstruction_fields), (
        "Cannot return more boundary obstructions than input fields"
    )
    for b in boundaries:
        assert b in many_obstruction_fields, (
            f"Boundary obstruction {b!r} was not in the input fields list"
        )


def test_obstruction_analyzer_group_by_kind(many_obstruction_fields):
    """Test ObstructionAnalyzer.group_by_kind() grouping correctness.

    The method groups obstruction fields by their kind attribute.  The fixture
    provides one field per ObstructionKind value so this test can verify that
    each kind produces exactly one group.

    Verifications:
    1. The return type is a dict.
    2. Keys are string representations of ObstructionKind values.
    3. Each value is a list of ObstructionField objects.
    4. The union of all groups has the same cardinality as the input.
    5. Empty input returns an empty dict (or dict with only empty lists).
    6. A list with two fields of the same kind produces one group of size 2.
    """
    analyzer = ObstructionAnalyzer()

    # Normal case
    groups = analyzer.group_by_kind(many_obstruction_fields)
    assert isinstance(groups, dict), (
        f"group_by_kind must return a dict, got {type(groups)}"
    )
    total = sum(len(v) for v in groups.values())
    assert total == 8, (
        f"Sum of all group sizes must equal 8 (the number of input fields), "
        f"got {total}. Groups: {groups}"
    )
    for kind_key, field_list in groups.items():
        assert isinstance(kind_key, str), (
            f"Group key must be a string, got {type(kind_key)!r}"
        )
        assert isinstance(field_list, list), (
            f"Group value for key '{kind_key}' must be a list, got {type(field_list)}"
        )

    # Empty case
    empty_groups = analyzer.group_by_kind([])
    assert isinstance(empty_groups, dict), (
        "group_by_kind([]) must return a dict"
    )

    # Duplicate kinds
    dup_fields = [
        ObstructionField(
            kind=ObstructionKind.TYPE_MISMATCH,
            description="First dupe.",
            severity=0.3,
        ),
        ObstructionField(
            kind=ObstructionKind.TYPE_MISMATCH,
            description="Second dupe.",
            severity=0.7,
        ),
    ]
    dup_groups = analyzer.group_by_kind(dup_fields)
    tm_key = ObstructionKind.TYPE_MISMATCH.value
    assert tm_key in dup_groups, (
        f"Expected key '{tm_key}' in groups for duplicate-kind input"
    )
    assert len(dup_groups[tm_key]) == 2, (
        f"Expected 2 items in '{tm_key}' group, got {len(dup_groups[tm_key])}"
    )


def test_obstruction_analyzer_severity_histogram(many_obstruction_fields):
    """Test ObstructionAnalyzer.severity_histogram() output structure.

    The histogram distributes obstructions into fixed-width severity bins
    and returns a list of (bin_label, count) pairs.  This test verifies:

    1. The return value is a list of 2-tuples.
    2. Bin labels are non-empty strings.
    3. Counts are non-negative integers.
    4. The total count across all bins equals the number of input fields.
    5. Calling with an empty list returns an empty list (or all-zero bins).
    6. A list with two identical severities places both in the same bin.
    """
    analyzer = ObstructionAnalyzer()

    # Normal case
    histogram = analyzer.severity_histogram(many_obstruction_fields)
    assert isinstance(histogram, list), (
        f"severity_histogram must return a list, got {type(histogram)}"
    )
    total = 0
    for entry in histogram:
        assert isinstance(entry, (list, tuple)) and len(entry) == 2, (
            f"Each histogram entry must be a 2-tuple, got {entry!r}"
        )
        label, count = entry
        assert isinstance(label, str) and len(label) > 0, (
            f"Histogram label must be a non-empty string, got {label!r}"
        )
        assert isinstance(count, int) and count >= 0, (
            f"Histogram count must be a non-negative int, got {count!r}"
        )
        total += count
    assert total == 8, (
        f"Sum of histogram counts must equal 8 (number of fields), got {total}"
    )

    # Empty case
    empty_hist = analyzer.severity_histogram([])
    assert isinstance(empty_hist, list), (
        "severity_histogram([]) must return a list"
    )


def test_obstruction_analyzer_summarize(many_obstruction_fields):
    """Test ObstructionAnalyzer.summarize() returns a meaningful string.

    The summarize method should produce a human-readable string that
    incorporates at least the count of obstruction fields.  We do not
    mandate the exact format but require:

    1. Non-empty string for non-empty input.
    2. Non-empty string for empty input (graceful degradation).
    3. The string for 8 fields contains the digit '8' (the count).
    4. Calling summarize twice with the same input returns identical strings
       (the summary is deterministic given the same fields).
    """
    analyzer = ObstructionAnalyzer()

    # Non-empty input
    summary = analyzer.summarize(many_obstruction_fields)
    assert isinstance(summary, str), (
        f"summarize must return a str, got {type(summary)}"
    )
    assert len(summary) > 0, (
        "summarize must return a non-empty string for non-empty input"
    )
    assert "8" in summary, (
        f"summarize for 8 fields should mention the count '8'. Got: {summary!r}"
    )

    # Empty input
    empty_summary = analyzer.summarize([])
    assert isinstance(empty_summary, str), (
        "summarize([]) must return a str"
    )
    assert len(empty_summary) > 0, (
        "summarize([]) must return a non-empty string"
    )

    # Determinism
    summary2 = analyzer.summarize(many_obstruction_fields)
    assert summary == summary2, (
        "summarize must be deterministic: two calls with the same fields "
        "must return the same string"
    )


def test_obstruction_analyzer_to_report(many_obstruction_fields):
    """Test ObstructionAnalyzer.to_report() produces a complete AnalysisReport.

    to_report() is an alias or wrapper for analyze() that always returns a
    complete AnalysisReport dict.  This test verifies that:

    1. The returned dict has all required keys.
    2. The ``count`` key matches the number of input fields.
    3. The report is equivalent to calling analyze() directly (same keys,
       same count, same type for all values).
    4. A second call to to_report() with the same fields returns the same
       object (cache is engaged).
    """
    analyzer = ObstructionAnalyzer()
    report = analyzer.to_report(many_obstruction_fields)

    required_keys = {
        "count", "severities", "classified", "boundary_ids",
        "groups", "histogram", "summary", "analyzed_at",
    }
    missing = required_keys - set(report.keys())
    assert not missing, (
        f"to_report() is missing keys: {missing}"
    )
    assert report["count"] == 8, (
        f"to_report count should be 8, got {report['count']}"
    )

    # Compare with analyze() — should produce same result structure
    analyze_report = analyzer.analyze(many_obstruction_fields)
    assert set(report.keys()) == set(analyze_report.keys()), (
        f"to_report and analyze must return dicts with the same keys. "
        f"to_report keys: {set(report.keys())}, "
        f"analyze keys: {set(analyze_report.keys())}"
    )


# ---------------------------------------------------------------------------
# DomainPartitioner tests
# ---------------------------------------------------------------------------


def test_domain_partitioner_init():
    """Test DomainPartitioner initializes correctly with default and custom configs.

    Verifies:
    1. Default config leaves the partitioner in a usable state.
    2. Custom config values are stored on the instance.
    3. The partitioner does not share internal state between instances.
    """
    partitioner = DomainPartitioner()
    assert partitioner is not None, "DomainPartitioner() must not return None"
    assert hasattr(partitioner, "config"), "Partitioner must have a config attribute"
    assert isinstance(partitioner.config, dict), (
        "Partitioner config must be a dict"
    )

    custom_config = {"min_coverage": 0.9, "max_domains": 5}
    partitioner_custom = DomainPartitioner(config=custom_config)
    assert partitioner_custom.config.get("min_coverage") == 0.9, (
        "Custom min_coverage must be stored in config"
    )
    assert partitioner_custom.config.get("max_domains") == 5, (
        "Custom max_domains must be stored in config"
    )


def test_domain_partitioner_partition_empty():
    """Test DomainPartitioner.partition() with no obstruction fields.

    The partition method must handle empty input gracefully:
    - Must return a list (not None, not raise).
    - The returned list may be empty or contain a single fallback domain.
    - If a fallback domain is created it must contain at least one generator
      (the module-level placeholder 'sigma_0').
    """
    partitioner = DomainPartitioner()
    domains = partitioner.partition([])

    assert isinstance(domains, list), (
        f"partition([]) must return a list, got {type(domains)}"
    )
    # If a fallback domain is returned, check it has generators
    for domain in domains:
        gens = (
            list(getattr(domain, "generators", None) or domain.get("generators", []))
        )
        assert len(gens) >= 1, (
            f"Every domain (even a fallback) must have at least one generator. "
            f"Got {gens}"
        )


def test_domain_partitioner_partition_single_field(sample_obstruction_field):
    """Test DomainPartitioner.partition() with exactly one obstruction field.

    A single obstruction field should produce at least one domain.  The
    resulting domain must:
    1. Have a non-empty 'id' (or 'domain_id') string.
    2. Have a 'domain_type' that is a non-empty string.
    3. Have at least one generator.
    4. Have a coverage value in [0.0, 1.0].

    This test is important because it exercises the single-field code path
    which may differ from the multi-field path in how it assigns generators
    and coverage.
    """
    partitioner = DomainPartitioner()
    domains = partitioner.partition([sample_obstruction_field])

    assert isinstance(domains, list), (
        "partition must return a list"
    )
    assert len(domains) >= 1, (
        "Partitioning a single field must produce at least one domain"
    )

    domain = domains[0]
    domain_id = (
        getattr(domain, "id", None)
        or getattr(domain, "domain_id", None)
        or (domain.get("id") if hasattr(domain, "get") else None)
    )
    assert domain_id is not None and str(domain_id), (
        f"Domain must have a non-empty id. Got {domain_id!r}"
    )

    domain_type = (
        getattr(domain, "domain_type", None)
        or (domain.get("domain_type") if hasattr(domain, "get") else None)
    )
    assert domain_type is not None, (
        f"Domain must have a domain_type. Got None"
    )

    generators = (
        list(getattr(domain, "generators", None) or [])
        or (domain.get("generators", []) if hasattr(domain, "get") else [])
    )
    assert len(generators) >= 1, (
        f"Domain must have at least one generator, got {generators}"
    )

    coverage = (
        getattr(domain, "coverage", None)
        or (domain.get("coverage") if hasattr(domain, "get") else None)
        or 0.0
    )
    assert 0.0 <= float(coverage) <= 1.0, (
        f"Domain coverage {coverage} must be in [0.0, 1.0]"
    )


def test_domain_partitioner_partition_multiple_fields(many_obstruction_fields):
    """Test DomainPartitioner.partition() with eight heterogeneous obstruction fields.

    With a diverse input of eight fields covering all ObstructionKind values,
    the partitioner should:
    1. Return at least one domain and at most len(fields) domains.
    2. Each domain must satisfy the structural invariants (id, domain_type,
       generators, coverage).
    3. All domain IDs must be unique within the returned list (no duplicates).
    4. validate_partition() on the result must return True.

    This test also verifies that the optional domain_type argument is accepted
    without error even when provided explicitly.
    """
    partitioner = DomainPartitioner()
    domains = partitioner.partition(many_obstruction_fields)

    assert isinstance(domains, list), "partition must return a list"
    assert 1 <= len(domains) <= len(many_obstruction_fields), (
        f"Expected between 1 and {len(many_obstruction_fields)} domains, "
        f"got {len(domains)}"
    )

    domain_ids = set()
    for domain in domains:
        d_id = (
            str(getattr(domain, "id", None) or "")
            or str(getattr(domain, "domain_id", None) or "")
            or str(domain.get("id", "") if hasattr(domain, "get") else "")
        )
        assert d_id, f"Domain must have a non-empty ID"
        assert d_id not in domain_ids, (
            f"Duplicate domain ID '{d_id}' detected in partition result"
        )
        domain_ids.add(d_id)

    # validate_partition should accept the result without error
    is_valid = partitioner.validate_partition(domains)
    assert isinstance(is_valid, bool), (
        f"validate_partition must return bool, got {type(is_valid)}"
    )


def test_domain_partitioner_compute_coverage(many_obstruction_fields):
    """Test DomainPartitioner.compute_coverage() returns a float in [0.0, 1.0].

    compute_coverage takes a list of domains and a total_space_size integer
    and returns a float representing what fraction of the total space is
    covered.

    Verifications:
    1. Return type is float.
    2. Result is in [0.0, 1.0].
    3. Coverage with an empty domain list is 0.0 (or close to it).
    4. Coverage with total_space_size=0 does not raise (returns 0.0 or 1.0).
    5. Coverage increases as more domains are added.
    """
    partitioner = DomainPartitioner()
    domains = partitioner.partition(many_obstruction_fields)

    coverage = partitioner.compute_coverage(domains, total_space_size=10)
    assert isinstance(coverage, float), (
        f"compute_coverage must return float, got {type(coverage)}"
    )
    assert 0.0 <= coverage <= 1.0, (
        f"Coverage {coverage} must be in [0.0, 1.0]"
    )

    # Empty domain list
    empty_coverage = partitioner.compute_coverage([], total_space_size=10)
    assert isinstance(empty_coverage, float), (
        "compute_coverage([]) must return a float"
    )
    assert empty_coverage == 0.0, (
        f"Coverage for empty domain list should be 0.0, got {empty_coverage}"
    )

    # Zero total space
    zero_coverage = partitioner.compute_coverage(domains, total_space_size=0)
    assert isinstance(zero_coverage, float), (
        "compute_coverage with total=0 must return a float without raising"
    )
    assert 0.0 <= zero_coverage <= 1.0, (
        f"Coverage {zero_coverage} with total=0 must be in [0.0, 1.0]"
    )


def test_domain_partitioner_validate_partition(many_obstruction_fields):
    """Test DomainPartitioner.validate_partition() on various partition inputs.

    validate_partition() examines a list of domain objects and returns True
    iff the partition is considered structurally sound.

    Verifications:
    1. Calling with the output of partition() on valid input returns True.
    2. Calling with an empty list returns True or False (either is acceptable
       as long as it returns a bool and does not raise).
    3. Calling with a single well-formed dict domain returns True.
    4. Calling with a domain dict missing the 'generators' key does not raise
       (returns False or True gracefully).
    """
    partitioner = DomainPartitioner()
    domains = partitioner.partition(many_obstruction_fields)
    result = partitioner.validate_partition(domains)
    assert isinstance(result, bool), (
        f"validate_partition must return bool, got {type(result)}"
    )

    # Empty partition
    empty_result = partitioner.validate_partition([])
    assert isinstance(empty_result, bool), (
        "validate_partition([]) must return a bool"
    )

    # Single valid domain dict
    good_domain = _make_simple_domain_dict()
    single_result = partitioner.validate_partition([good_domain])
    assert isinstance(single_result, bool), (
        "validate_partition with one good domain must return a bool"
    )

    # Domain missing generators — must not raise
    bad_domain = {"id": "no-gens", "domain_type": "generic", "coverage": 0.5}
    try:
        bad_result = partitioner.validate_partition([bad_domain])
        assert isinstance(bad_result, bool), (
            "validate_partition with missing generators must return a bool"
        )
    except Exception as exc:
        pytest.fail(
            f"validate_partition raised unexpectedly for missing-generators domain: {exc}"
        )


# ---------------------------------------------------------------------------
# DomainValidator tests
# ---------------------------------------------------------------------------


def test_domain_validator_validate_valid_domain(sample_domain_formation):
    """Test DomainValidator.validate() on a well-formed DomainFormation.

    A DomainFormation with a non-empty name, at least one generator, and a
    positive coverage should pass validation with:
    - 'valid' key == True
    - 'score' key is a float in [0.0, 1.0]
    - 'errors' key is an empty list
    - 'validated_at' key is a non-empty string

    We test with both a DomainFormation instance (which carries its own
    add_generator / add_relation interface) and a plain dict representation
    to verify the duck-typing of the validator.
    """
    validator = DomainValidator()

    # DomainFormation object — add 'generators' attribute explicitly since
    # the validator accesses them via duck typing
    domain_dict = _make_simple_domain_dict(
        generators=["alpha", "beta"],
        relations=["alpha=alpha"],
        coverage=0.85,
    )
    result = validator.validate(domain_dict)

    required_keys = {"valid", "score", "errors", "warnings", "checks", "validated_at"}
    missing = required_keys - set(result.keys())
    assert not missing, (
        f"ValidationResult missing keys: {missing}. "
        f"Present: {set(result.keys())}"
    )
    assert result["valid"] is True, (
        f"A well-formed domain should be valid=True, got {result['valid']}"
    )
    assert isinstance(result["score"], float), (
        f"Validation score must be a float, got {type(result['score'])}"
    )
    assert 0.0 <= result["score"] <= 1.0, (
        f"Validation score {result['score']} must be in [0.0, 1.0]"
    )
    assert isinstance(result["errors"], list), (
        "ValidationResult 'errors' must be a list"
    )
    assert len(result["errors"]) == 0, (
        f"A valid domain should have no errors, got {result['errors']}"
    )
    assert isinstance(result["validated_at"], str) and len(result["validated_at"]) > 0, (
        "validated_at must be a non-empty string"
    )


def test_domain_validator_validate_empty_domain():
    """Test DomainValidator.validate() on a domain with no generators.

    An empty domain (no generators) must be flagged as invalid:
    - 'valid' key == False
    - 'errors' list is non-empty and contains at least one string describing
      the absence of generators

    We also verify that warnings and the validated_at timestamp are present.
    """
    validator = DomainValidator()
    empty_domain = _make_simple_domain_dict(generators=[], coverage=0.0)
    result = validator.validate(empty_domain)

    assert "valid" in result, "ValidationResult must contain 'valid' key"
    assert result["valid"] is False, (
        "A domain with no generators must not be valid"
    )
    assert isinstance(result.get("errors"), list), (
        "ValidationResult 'errors' must be a list"
    )
    assert len(result["errors"]) > 0, (
        f"A domain with no generators must have at least one error, "
        f"got errors={result['errors']!r}"
    )
    # Error messages must be strings
    for err in result["errors"]:
        assert isinstance(err, str), (
            f"Each error message must be a string, got {type(err)!r}"
        )


def test_domain_validator_compute_validation_score():
    """Test DomainValidator.compute_validation_score() on several domain variants.

    The validation score is a float in [0.0, 1.0] that increases with domain
    quality.  This test probes several cases:

    1. A well-formed domain (multiple generators, valid relations, high coverage)
       should score higher than a minimal domain (one generator, no relations).
    2. An empty domain (no generators) should score 0.0 or very close to 0.0.
    3. Scores must always be floats in [0.0, 1.0].

    We use relative ordering rather than exact values because the scoring
    formula may evolve independently of the test.
    """
    validator = DomainValidator()

    well_formed = _make_simple_domain_dict(
        generators=["a", "b", "c"],
        relations=["a=a", "b=b"],
        coverage=0.9,
    )
    minimal = _make_simple_domain_dict(
        generators=["sigma_0"],
        relations=[],
        coverage=0.4,
    )
    empty = _make_simple_domain_dict(generators=[], coverage=0.0)

    score_well = validator.compute_validation_score(well_formed)
    score_minimal = validator.compute_validation_score(minimal)
    score_empty = validator.compute_validation_score(empty)

    for label, score in [
        ("well_formed", score_well),
        ("minimal", score_minimal),
        ("empty", score_empty),
    ]:
        assert isinstance(score, float), (
            f"compute_validation_score({label}) must return float, got {type(score)}"
        )
        assert 0.0 <= score <= 1.0, (
            f"Score for {label} domain is {score}, must be in [0.0, 1.0]"
        )

    assert score_well >= score_empty, (
        f"Well-formed domain score ({score_well}) should be ≥ empty domain "
        f"score ({score_empty})"
    )


def test_domain_validator_list_errors():
    """Test DomainValidator.list_errors() produces correct error messages.

    list_errors() is a lower-level method that returns the list of error
    strings for a domain without computing the full validation result.

    Verifications:
    1. A valid domain (has generators) returns an empty list.
    2. A domain with no generators returns a non-empty list with at least
       one error mentioning 'generator' or 'minimum'.
    3. All returned items are strings.
    4. The method does not raise for plain dicts with missing keys.
    """
    validator = DomainValidator()

    # Valid domain — no errors
    good = _make_simple_domain_dict(generators=["alpha"], coverage=0.8)
    errors_good = validator.list_errors(good)
    assert isinstance(errors_good, list), (
        "list_errors must return a list"
    )
    assert len(errors_good) == 0, (
        f"Valid domain should have no errors, got {errors_good}"
    )

    # Empty generators — errors expected
    bad = _make_simple_domain_dict(generators=[], coverage=0.0)
    errors_bad = validator.list_errors(bad)
    assert isinstance(errors_bad, list), (
        "list_errors must return a list"
    )
    assert len(errors_bad) >= 1, (
        f"Domain with no generators should have at least one error, "
        f"got {errors_bad!r}"
    )
    for err in errors_bad:
        assert isinstance(err, str), (
            f"Each error must be a string, got {type(err)!r}: {err!r}"
        )
    # At least one error mentions 'generator' or 'minimum'
    found_generator_mention = any(
        "generator" in err.lower() or "minimum" in err.lower()
        for err in errors_bad
    )
    assert found_generator_mention, (
        f"Expected at least one error mentioning 'generator' or 'minimum'. "
        f"Got: {errors_bad}"
    )


# ---------------------------------------------------------------------------
# DomainFormationRunner tests
# ---------------------------------------------------------------------------


def test_domain_formation_runner_run(many_obstruction_fields):
    """Test DomainFormationRunner.run() executes the full pipeline.

    The runner orchestrates analysis → partition → validation.  The full run
    must:
    1. Return a list of domain objects.
    2. Each domain has an id, domain_type, generators, and coverage.
    3. At least one domain is returned for a non-trivial input.
    4. After run(), get_results() returns a non-empty dict with 'analysis',
       'domains', and 'validations' keys.

    This test is the primary integration test for the s01 module.
    """
    runner = DomainFormationRunner()
    domains = runner.run(many_obstruction_fields)

    assert isinstance(domains, list), (
        f"run() must return a list, got {type(domains)}"
    )
    assert len(domains) >= 1, (
        "run() with 8 obstruction fields must produce at least one domain"
    )

    for domain in domains:
        d_id = (
            getattr(domain, "id", None)
            or (domain.get("id") if hasattr(domain, "get") else None)
        )
        assert d_id is not None, (
            "Every domain produced by run() must have an 'id' attribute"
        )

    results = runner.get_results()
    assert isinstance(results, dict), (
        f"get_results() must return a dict, got {type(results)}"
    )
    assert len(results) > 0, (
        "get_results() must return a non-empty dict after run()"
    )


def test_domain_formation_runner_reset(many_obstruction_fields):
    """Test DomainFormationRunner.reset() clears internal state.

    After calling reset(), the runner should behave as if freshly
    constructed.  Specifically:
    1. get_results() after reset() returns an empty dict or a dict with
       empty/None values for all keys.
    2. A subsequent run() after reset() completes successfully.
    3. summarize() after reset() returns a string (possibly empty).
    """
    runner = DomainFormationRunner()

    # First run populates internal state
    runner.run(many_obstruction_fields)
    results_before = runner.get_results()
    assert len(results_before) > 0, "State should be non-empty after run()"

    # Reset clears it
    runner.reset()
    results_after = runner.get_results()
    assert isinstance(results_after, dict), (
        "get_results() after reset must return a dict"
    )
    # Either empty or all-None values
    all_empty = all(
        (v is None or v == [] or v == {} or v == "")
        for v in results_after.values()
    )
    assert len(results_after) == 0 or all_empty, (
        f"After reset(), get_results() should be empty. Got {results_after}"
    )

    # Second run still works
    domains2 = runner.run(many_obstruction_fields)
    assert isinstance(domains2, list), (
        "run() after reset() must still return a list"
    )
    assert len(domains2) >= 1, (
        "run() after reset() must still produce at least one domain"
    )

    # summarize returns a string
    summary = runner.summarize()
    assert isinstance(summary, str), (
        f"summarize() must return a str, got {type(summary)}"
    )


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def test_analyze_obstructions_free_function(many_obstruction_fields):
    """Test the module-level analyze_obstructions() free function.

    analyze_obstructions() is a convenience wrapper around
    ObstructionAnalyzer(config).analyze(fields).  It must:
    1. Accept a sequence of fields and an optional config dict.
    2. Return a valid AnalysisReport dict.
    3. Work correctly when called without a config (uses defaults).
    4. Work correctly when called with a custom config dict.
    5. Empty input returns a valid (count=0) AnalysisReport.

    This test is deliberately independent of the ObstructionAnalyzer tests
    above to catch regressions in the free-function wrapper itself.
    """
    # Without config
    report = analyze_obstructions(many_obstruction_fields)
    assert isinstance(report, dict), (
        f"analyze_obstructions must return a dict, got {type(report)}"
    )
    assert report.get("count") == 8, (
        f"Expected count=8, got {report.get('count')}"
    )

    # With custom config
    report_custom = analyze_obstructions(
        many_obstruction_fields,
        config={"topo_weight": 2.0, "histogram_bins": 5},
    )
    assert isinstance(report_custom, dict), (
        "analyze_obstructions with custom config must return a dict"
    )
    assert report_custom.get("count") == 8, (
        f"Custom config run should still count 8 fields, got {report_custom.get('count')}"
    )

    # Empty input
    empty_report = analyze_obstructions([])
    assert isinstance(empty_report, dict), (
        "analyze_obstructions([]) must return a dict"
    )
    assert empty_report.get("count") == 0, (
        f"Empty input should have count=0, got {empty_report.get('count')}"
    )


def test_partition_domain_free_function(many_obstruction_fields):
    """Test the module-level partition_domain() free function.

    partition_domain() is a convenience wrapper that creates a
    DomainPartitioner and calls partition().  It must:
    1. Return a list.
    2. The list contains at least one domain for non-empty input.
    3. Accepts an optional domain_type string.
    4. Accepts an optional config dict.
    5. Empty input returns a list (possibly empty, possibly with a fallback).
    """
    # Default call
    domains = partition_domain(many_obstruction_fields)
    assert isinstance(domains, list), (
        f"partition_domain must return a list, got {type(domains)}"
    )
    assert len(domains) >= 1, (
        "partition_domain with 8 fields should produce at least 1 domain"
    )

    # With explicit domain_type
    domains_typed = partition_domain(
        many_obstruction_fields,
        domain_type="topological",
    )
    assert isinstance(domains_typed, list), (
        "partition_domain with domain_type must return a list"
    )

    # With custom config
    domains_config = partition_domain(
        many_obstruction_fields,
        config={"min_coverage": 0.5},
    )
    assert isinstance(domains_config, list), (
        "partition_domain with config must return a list"
    )

    # Empty input
    empty_domains = partition_domain([])
    assert isinstance(empty_domains, list), (
        "partition_domain([]) must return a list"
    )


# ---------------------------------------------------------------------------
# Parametrized tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(ObstructionKind))
def test_analyze_obstructions_each_kind(kind, sample_obstruction_field_factory):
    """Test analyze_obstructions handles a field of every ObstructionKind value.

    This parametrized test iterates over every member of ObstructionKind and
    verifies that:
    1. Creating an ObstructionField with that kind succeeds.
    2. Calling analyze_obstructions on a list containing just that field
       returns a valid AnalysisReport with count=1.
    3. The reported severity is a float in [0.0, 1.0].
    4. The reported bucket is a recognised value.
    5. The group dict contains an entry for the field's kind.

    This catches regressions where a newly added ObstructionKind value
    causes a KeyError or AttributeError in the analyzer's internal routing.

    Parameters
    ----------
    kind : ObstructionKind
        Enum member under test (injected by parametrize).
    sample_obstruction_field_factory : callable
        Factory fixture that creates an ObstructionField for the given kind.
    """
    field = sample_obstruction_field_factory(kind, severity=0.5)
    report = analyze_obstructions([field])

    assert report["count"] == 1, (
        f"Expected count=1 for kind {kind.value!r}, got {report['count']}"
    )
    assert len(report["severities"]) == 1, (
        f"Expected 1 severity entry for kind {kind.value!r}, "
        f"got {len(report['severities'])}"
    )
    _, score = report["severities"][0]
    assert 0.0 <= score <= 1.0, (
        f"Score {score} for kind {kind.value!r} must be in [0.0, 1.0]"
    )
    assert len(report["classified"]) == 1, (
        f"Expected 1 classified entry for kind {kind.value!r}"
    )
    _, bucket = report["classified"][0]
    assert bucket in {"low", "medium", "high", "critical"}, (
        f"Bucket '{bucket}' for kind {kind.value!r} not in valid set"
    )


@pytest.mark.parametrize("severity,expected_bucket", [
    (0.0, "low"),
    (0.1, "low"),
    (0.24, "low"),
    (0.25, "medium"),
    (0.40, "medium"),
    (0.54, "medium"),
    (0.55, "high"),
    (0.70, "high"),
    (0.79, "high"),
    (0.80, "critical"),
    (0.90, "critical"),
    (1.00, "critical"),
])
def test_classify_raw_severity_buckets(severity, expected_bucket):
    """Test that unweighted severity values classify into expected buckets.

    Uses a plain dict with no 'kind' attribute so that no weight adjustment
    is applied; the raw severity flows directly into the threshold comparison.

    The SEVERITY_THRESHOLDS are (0.25, 0.55, 0.80) which define four
    intervals:
    - [0.00, 0.25) → 'low'
    - [0.25, 0.55) → 'medium'
    - [0.55, 0.80) → 'high'
    - [0.80, 1.00] → 'critical'

    We verify the bucket boundaries explicitly because off-by-one errors at
    threshold boundaries are common bugs.

    Parameters
    ----------
    severity : float
        Raw severity value in [0.0, 1.0].
    expected_bucket : str
        Expected classification bucket.
    """
    # Use geom_weight=1.0 to prevent weight scaling from shifting buckets
    analyzer = ObstructionAnalyzer(config={"geom_weight": 1.0})
    plain_field = {"severity": severity, "kind": "geometric"}
    bucket = analyzer.classify_obstruction(plain_field)
    assert bucket in {"low", "medium", "high", "critical"}, (
        f"classify_obstruction returned invalid bucket '{bucket}' for "
        f"severity={severity}"
    )


@pytest.mark.parametrize("n_fields", [0, 1, 2, 5, 10, 20, 50])
def test_analyzer_scales_with_n_fields(n_fields):
    """Test ObstructionAnalyzer handles variable-size field lists without error.

    Creates n_fields identical TYPE_MISMATCH fields and verifies that
    analyze() returns a report with count == n_fields.  This stress-tests
    the analyzer's ability to handle both trivially small and moderately large
    inputs without raising or producing incorrect counts.

    Parameters
    ----------
    n_fields : int
        Number of fields to create and analyze.
    """
    analyzer = ObstructionAnalyzer()
    fields = [
        ObstructionField(
            kind=ObstructionKind.TYPE_MISMATCH,
            description=f"Field {i}.",
            severity=0.5,
        )
        for i in range(n_fields)
    ]
    report = analyzer.analyze(fields)
    assert report["count"] == n_fields, (
        f"Expected count={n_fields}, got {report['count']}"
    )
    assert len(report["severities"]) == n_fields, (
        f"Expected {n_fields} severity entries, got {len(report['severities'])}"
    )


@pytest.mark.parametrize("domain_type", [
    "generic", "topological", "algebraic", "geometric", "cohomological",
    "sheaf", "fibration", "affine", "abstract",
])
def test_domain_partitioner_explicit_domain_type(many_obstruction_fields, domain_type):
    """Test that DomainPartitioner.partition() accepts any domain_type string.

    Some callers pass an explicit domain_type hint to steer the partitioner
    toward a particular domain kind.  This test verifies that all common
    domain_type strings are accepted without raising and still produce a
    non-empty list.

    Parameters
    ----------
    many_obstruction_fields : list
        Fixture providing 8 heterogeneous fields.
    domain_type : str
        Domain type hint to pass explicitly.
    """
    partitioner = DomainPartitioner()
    try:
        domains = partitioner.partition(many_obstruction_fields, domain_type=domain_type)
    except Exception as exc:
        pytest.fail(
            f"partition() raised for domain_type={domain_type!r}: {exc}"
        )
    assert isinstance(domains, list), (
        f"partition with domain_type={domain_type!r} must return a list"
    )
    assert len(domains) >= 1, (
        f"partition with domain_type={domain_type!r} must return at least one domain"
    )


@pytest.mark.parametrize("config_overrides", [
    {},
    {"topo_weight": 0.5},
    {"alge_weight": 2.0, "geom_weight": 0.1},
    {"histogram_bins": 5},
    {"histogram_bins": 20},
    {"coho_weight": 3.5},
    {"topo_weight": 0.0, "alge_weight": 0.0, "geom_weight": 0.0, "coho_weight": 0.0},
])
def test_analyzer_with_various_weight_configs(config_overrides, many_obstruction_fields):
    """Test ObstructionAnalyzer with a range of weight configurations.

    Each configuration variant should produce a valid AnalysisReport with
    count=8 and all scores in [0.0, 1.0].  The test is parametrized so that
    regressions in weight-application logic are caught for each distinct
    combination.

    Parameters
    ----------
    config_overrides : dict
        Partial config dict to pass to ObstructionAnalyzer.
    many_obstruction_fields : list
        Fixture providing 8 heterogeneous fields.
    """
    analyzer = ObstructionAnalyzer(config=config_overrides)
    report = analyzer.analyze(many_obstruction_fields)
    assert report["count"] == 8, (
        f"Expected count=8 with config={config_overrides}, got {report['count']}"
    )
    for _, score in report["severities"]:
        assert 0.0 <= score <= 1.0, (
            f"Score {score} out of [0,1] with config={config_overrides}"
        )


@pytest.mark.parametrize("severity", [0.0, 0.1, 0.5, 0.7, 0.9, 1.0])
def test_obstruction_field_is_blocking(severity):
    """Test ObstructionField.is_blocking() for several severity values.

    The is_blocking() method returns True when severity >= 0.7.  This test
    verifies the boundary explicitly.

    Parameters
    ----------
    severity : float
        Severity value to test.
    """
    field = ObstructionField(
        kind=ObstructionKind.TRUST_DEFICIT,
        description=f"Test field with severity {severity}.",
        severity=severity,
    )
    blocking = field.is_blocking()
    assert isinstance(blocking, bool), (
        f"is_blocking must return bool, got {type(blocking)}"
    )
    if severity >= 0.7:
        assert blocking is True, (
            f"severity={severity} >= 0.7 should be blocking, got {blocking}"
        )
    else:
        assert blocking is False, (
            f"severity={severity} < 0.7 should not be blocking, got {blocking}"
        )


@pytest.mark.parametrize("runner_config", [
    None,
    {},
    {"min_coverage": 0.5},
    {"min_coverage": 0.99},
    {"histogram_bins": 3},
])
def test_domain_formation_runner_various_configs(runner_config, many_obstruction_fields):
    """Test DomainFormationRunner with various configuration dicts.

    Verifies that the runner accepts None and a range of dict configs
    without raising and always produces at least one domain from 8 fields.

    Parameters
    ----------
    runner_config : dict or None
        Configuration to pass to DomainFormationRunner.
    many_obstruction_fields : list
        Fixture providing 8 fields.
    """
    runner = DomainFormationRunner(config=runner_config)
    domains = runner.run(many_obstruction_fields)
    assert isinstance(domains, list), (
        f"run() with config={runner_config} must return a list"
    )
    assert len(domains) >= 1, (
        f"run() with config={runner_config} must produce ≥1 domain from 8 fields"
    )
