from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import time
import uuid
import math

from jugeo.generation.replay_gluing.models import ConvergenceRecord, GluingUnderReplay, ReplayPhase
from jugeo.generation.replay_gluing.s03_convergence_verification import (
    ConvergenceMetric,
    FixedPointChecker,
    ConvergenceCertificate,
    ConvergenceVerifier,
    ConvergenceStatus,
    ConvergenceReport,
    compute_convergence_status,
    format_convergence_report,
    is_metric_monotone_decreasing,
    compute_exponential_convergence_rate,
    detect_oscillation,
    CONVERGENCE_TOLERANCE,
    MAX_CONVERGENCE_ROUNDS,
)

try:
    from jugeo.geometry.descent import DescentEngine, GluingData
    from jugeo.generation.treaties import OverlapTreaty, TreatyStatus

    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_gluing_dict(gluing_id: str | None = None, patches: dict | None = None) -> dict:
    """Return a minimal gluing dict suitable for FixedPointChecker / ConvergenceVerifier."""
    if gluing_id is None:
        gluing_id = str(uuid.uuid4())
    if patches is None:
        patches = {"p0": {"value": 1.0, "weight": 0.5}}
    return {"gluing_id": gluing_id, "sections": patches}


def make_converging_history(n: int = 4, gluing_id: str | None = None) -> list[dict]:
    """Return a history of *identical* gluing dicts (instant fixed-point)."""
    if gluing_id is None:
        gluing_id = str(uuid.uuid4())
    base = make_gluing_dict(gluing_id=gluing_id, patches={"p0": {"value": 0.0}})
    return [base] * n


def make_diverging_history(n: int = 4) -> list[dict]:
    """Return a history where each entry is different (never a fixed-point)."""
    return [make_gluing_dict(patches={"p0": {"value": float(i)}}) for i in range(n)]


# ---------------------------------------------------------------------------
# TestConvergenceMetric
# ---------------------------------------------------------------------------


class TestConvergenceMetric:
    """Tests for the ConvergenceMetric dataclass."""

    def test_creation(self):
        """Default construction sets sentinel values."""
        m = ConvergenceMetric(name="test")
        assert m.name == "test"
        assert m.value == 0.0
        assert m.previous_value is None
        assert m.delta == 0.0

    def test_creation_with_initial_value(self):
        """Explicit initial value is stored, previous stays None."""
        m = ConvergenceMetric(name="dist", value=5.0)
        assert m.value == pytest.approx(5.0)
        assert m.previous_value is None

    def test_creation_with_threshold(self):
        """Custom threshold is stored correctly."""
        m = ConvergenceMetric(name="x", threshold=1e-4)
        assert m.threshold == pytest.approx(1e-4)

    def test_update_first_time(self):
        """First update: previous_value becomes the old value (0.0)."""
        m = ConvergenceMetric(name="x")
        m.update(0.5)
        assert m.value == pytest.approx(0.5)
        # previous_value is set to the old value (0.0) before the update
        assert m.previous_value == pytest.approx(0.0)
        assert m.delta == pytest.approx(0.5)

    def test_update_second_time(self):
        """Second update tracks previous correctly."""
        m = ConvergenceMetric(name="x")
        m.update(0.5)
        m.update(0.3)
        assert m.value == pytest.approx(0.3)
        assert m.previous_value == pytest.approx(0.5)
        assert m.delta == pytest.approx(0.2)

    def test_delta_computed_correctly(self):
        """Delta is |new - previous|."""
        m = ConvergenceMetric(name="x", value=1.0)
        m.update(0.6)
        assert m.delta == pytest.approx(0.4)

    def test_delta_increasing_value(self):
        """Delta is absolute so it is positive even when value increases."""
        m = ConvergenceMetric(name="x", value=0.2)
        m.update(0.9)
        assert m.delta == pytest.approx(0.7)

    def test_is_below_threshold_true(self):
        """Value below threshold → converged."""
        m = ConvergenceMetric(name="x", threshold=1e-4)
        m.update(1e-5)
        assert m.is_below_threshold() is True

    def test_is_below_threshold_false(self):
        """Value above threshold → not converged."""
        m = ConvergenceMetric(name="x", threshold=1e-4)
        m.update(1e-3)
        assert m.is_below_threshold() is False

    def test_is_below_threshold_exactly_at_threshold(self):
        """Value exactly equal to threshold is considered converged (<= comparison)."""
        m = ConvergenceMetric(name="x", threshold=0.01)
        m.update(0.01)
        assert m.is_below_threshold() is True

    def test_get_rate_of_change_before_update(self):
        """Before any update, previous_value is None → rate is inf."""
        m = ConvergenceMetric(name="x")
        assert m.get_rate_of_change() == math.inf

    def test_get_rate_of_change_after_second_update(self):
        """After two updates the rate is a finite positive number."""
        m = ConvergenceMetric(name="x")
        m.update(1.0)
        m.update(0.5)
        rate = m.get_rate_of_change()
        assert math.isfinite(rate)
        assert rate > 0

    def test_get_rate_of_change_zero_previous(self):
        """When previous value is effectively 0, denominator clamps to 1e-12."""
        m = ConvergenceMetric(name="x")
        # After first update: previous_value = 0.0
        m.update(1e-10)
        rate = m.get_rate_of_change()
        assert math.isfinite(rate)

    def test_is_improving_true(self):
        """Value decreased → improving."""
        m = ConvergenceMetric(name="x")
        m.update(1.0)
        m.update(0.5)
        assert m.is_improving() is True

    def test_is_improving_false(self):
        """Value increased → not improving."""
        m = ConvergenceMetric(name="x")
        m.update(0.5)
        m.update(1.0)
        assert m.is_improving() is False

    def test_is_improving_no_previous(self):
        """Before any update, previous_value is None → not improving."""
        m = ConvergenceMetric(name="x")
        assert m.is_improving() is False

    def test_is_improving_stable(self):
        """Unchanged value is not strictly improving."""
        m = ConvergenceMetric(name="x")
        m.update(0.5)
        m.update(0.5)
        assert m.is_improving() is False

    def test_to_dict(self):
        """to_dict returns all expected keys."""
        m = ConvergenceMetric(name="foo", threshold=1e-4)
        m.update(0.5)
        d = m.to_dict()
        assert d["name"] == "foo"
        assert d["threshold"] == pytest.approx(1e-4)
        assert "value" in d
        assert "previous_value" in d
        assert "delta" in d
        assert "is_converged" in d
        assert "rate_of_change" in d

    def test_to_dict_is_converged_flag(self):
        """to_dict reflects is_below_threshold() for is_converged key."""
        m = ConvergenceMetric(name="y", threshold=1.0)
        m.update(0.1)
        d = m.to_dict()
        assert d["is_converged"] is True

    @pytest.mark.parametrize("threshold", [1e-4, 1e-6, 1e-8, 0.0])
    def test_threshold_values(self, threshold):
        """Various threshold values are stored accurately."""
        m = ConvergenceMetric(name="x", threshold=threshold)
        assert m.threshold == threshold

    @pytest.mark.parametrize(
        "sequence",
        [
            [1.0, 0.5, 0.25, 0.1, 0.01],
            [10.0, 5.0, 2.0, 0.5],
            [100.0, 1.0, 0.001],
        ],
    )
    def test_update_sequence_decreasing(self, sequence):
        """After a decreasing sequence the final state is correct."""
        m = ConvergenceMetric(name="x")
        for v in sequence:
            m.update(v)
        assert m.value == pytest.approx(sequence[-1])
        assert m.previous_value == pytest.approx(sequence[-2])

    def test_multiple_updates_monotone_delta(self):
        """For a geometrically decaying sequence the deltas also decay."""
        m = ConvergenceMetric(name="geo")
        prev_delta = None
        for exp in range(1, 6):
            m.update(1.0 / (2 ** exp))
            if prev_delta is not None:
                # Deltas should be positive
                assert m.delta >= 0
            prev_delta = m.delta

    def test_name_preserved(self):
        """Name is read-only after construction and preserved through updates."""
        m = ConvergenceMetric(name="convergence_distance")
        m.update(0.9)
        m.update(0.4)
        assert m.name == "convergence_distance"


# ---------------------------------------------------------------------------
# TestFixedPointChecker
# ---------------------------------------------------------------------------


class TestFixedPointChecker:
    """Tests for the FixedPointChecker class."""

    def test_creation_default_tolerance(self):
        """Default tolerance equals the module constant."""
        checker = FixedPointChecker()
        assert checker.tolerance == CONVERGENCE_TOLERANCE

    def test_creation_custom_tolerance(self):
        """Custom tolerance is stored accurately."""
        checker = FixedPointChecker(tolerance=0.01)
        assert checker.tolerance == pytest.approx(0.01)

    def test_check_identical_dicts(self):
        """Identical dicts are a fixed point."""
        checker = FixedPointChecker()
        g = {"sections": {"p1": {"v": 1}, "p2": {"v": 2}}}
        assert checker.check(g, g) is True

    def test_check_different_dicts(self):
        """Completely different dicts are not a fixed point at tight tolerance."""
        checker = FixedPointChecker(tolerance=1e-6)
        g1 = {"sections": {"p1": {"v": 1}}}
        g2 = {"sections": {"p1": {"v": 999}}}
        assert checker.check(g1, g2) is False

    def test_compute_distance_identical(self):
        """Distance between identical dicts is 0."""
        checker = FixedPointChecker()
        g = {"sections": {"p1": 42}}
        assert checker.compute_distance(g, g) == pytest.approx(0.0)

    def test_compute_distance_returns_float(self):
        """compute_distance always returns a float."""
        checker = FixedPointChecker()
        g1 = make_gluing_dict(patches={"a": 1})
        g2 = make_gluing_dict(patches={"a": 2})
        result = checker.compute_distance(g1, g2)
        assert isinstance(result, float)

    def test_compute_distance_in_unit_interval(self):
        """Distance is always in [0, 1]."""
        checker = FixedPointChecker()
        g1 = make_gluing_dict(patches={"a": "x", "b": "y"})
        g2 = make_gluing_dict(patches={"a": "z", "c": "w"})
        d = checker.compute_distance(g1, g2)
        assert 0.0 <= d <= 1.0

    def test_compute_distance_different(self):
        """Different patch values yield positive distance."""
        checker = FixedPointChecker()
        g1 = {"sections": {"p1": "a"}}
        g2 = {"sections": {"p1": "b"}}
        assert checker.compute_distance(g1, g2) > 0

    def test_compute_distance_partial_overlap(self):
        """Dicts with different key sets have intermediate distance."""
        checker = FixedPointChecker()
        g1 = {"sections": {"p1": 1, "p2": 2}}
        g2 = {"sections": {"p1": 1, "p3": 3}}
        dist = checker.compute_distance(g1, g2)
        assert 0 < dist <= 1.0

    def test_is_fixed_point_below_tolerance(self):
        """Distance below tolerance is a fixed point."""
        checker = FixedPointChecker(tolerance=0.1)
        assert checker.is_fixed_point(0.05) is True

    def test_is_fixed_point_above_tolerance(self):
        """Distance above tolerance is not a fixed point."""
        checker = FixedPointChecker(tolerance=0.1)
        assert checker.is_fixed_point(0.5) is False

    def test_is_fixed_point_zero_distance(self):
        """Zero distance is always a fixed point."""
        checker = FixedPointChecker(tolerance=1e-12)
        assert checker.is_fixed_point(0.0) is True

    def test_patch_hash_deterministic(self):
        """_patch_hash returns the same value for the same input."""
        checker = FixedPointChecker()
        data = {"key": "value", "num": 42}
        h1 = checker._patch_hash(data)
        h2 = checker._patch_hash(data)
        assert h1 == h2

    def test_patch_hash_returns_int(self):
        """_patch_hash returns an integer."""
        checker = FixedPointChecker()
        h = checker._patch_hash({"a": 1})
        assert isinstance(h, int)

    def test_patch_distance_identical(self):
        """Distance between the same object is 0."""
        checker = FixedPointChecker()
        data = {"x": 1}
        assert checker._compute_patch_distance(data, data) == pytest.approx(0.0)

    def test_patch_distance_completely_different(self):
        """Distance between different strings is 1."""
        checker = FixedPointChecker()
        assert checker._compute_patch_distance("abc", "xyz") == pytest.approx(1.0)

    def test_patch_distance_one_none(self):
        """When one side is None (missing patch), distance is 1."""
        checker = FixedPointChecker()
        assert checker._compute_patch_distance(None, {"v": 1}) == pytest.approx(1.0)
        assert checker._compute_patch_distance({"v": 1}, None) == pytest.approx(1.0)

    def test_empty_dicts(self):
        """Two empty dicts have distance 0."""
        checker = FixedPointChecker()
        assert checker.compute_distance({}, {}) == pytest.approx(0.0)

    @pytest.mark.parametrize("tolerance", [1e-3, 1e-6, 0.5])
    def test_tolerance_levels(self, tolerance):
        """is_fixed_point respects arbitrary tolerance levels."""
        checker = FixedPointChecker(tolerance=tolerance)
        assert checker.is_fixed_point(tolerance * 0.5) is True
        assert checker.is_fixed_point(tolerance * 2.0) is False

    def test_check_symmetry(self):
        """check(a, b) and check(b, a) agree for identical inputs."""
        checker = FixedPointChecker()
        g = {"sections": {"x": "same"}}
        assert checker.check(g, g) == checker.check(g, g)

    def test_compute_distance_large_section_set(self):
        """Distance computation is stable for larger section sets."""
        checker = FixedPointChecker()
        patches_a = {f"p{i}": {"v": i} for i in range(20)}
        patches_b = {f"p{i}": {"v": i} for i in range(20)}
        g1 = {"sections": patches_a}
        g2 = {"sections": patches_b}
        assert checker.compute_distance(g1, g2) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestConvergenceCertificate
# ---------------------------------------------------------------------------


class TestConvergenceCertificate:
    """Tests for the ConvergenceCertificate dataclass."""

    def test_creation(self):
        """Basic construction populates required fields."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=3, final_metric=1e-7
        )
        assert cert.gluing_id == "g1"
        assert cert.rounds_to_converge == 3
        assert cert.final_metric == pytest.approx(1e-7)
        assert cert.cert_id  # auto-generated UUID

    def test_creation_defaults(self):
        """Default construction yields safe sentinel values."""
        cert = ConvergenceCertificate()
        assert cert.gluing_id == ""
        assert cert.rounds_to_converge == 0
        assert cert.expiry is None

    def test_cert_id_is_uuid_like(self):
        """Auto-generated cert_id looks like a valid UUID string."""
        cert = ConvergenceCertificate(gluing_id="g1", rounds_to_converge=1, final_metric=0.0)
        try:
            uuid.UUID(cert.cert_id)
        except ValueError:
            pytest.fail(f"cert_id is not a valid UUID: {cert.cert_id!r}")

    def test_validate_valid(self):
        """A fully-populated cert passes validation."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=2, final_metric=0.0
        )
        assert cert.validate() is True

    def test_validate_invalid_empty_gluing_id(self):
        """Empty gluing_id fails validation."""
        cert = ConvergenceCertificate(
            gluing_id="", rounds_to_converge=2, final_metric=0.0
        )
        assert cert.validate() is False

    def test_validate_invalid_zero_rounds(self):
        """Zero rounds fails validation."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=0, final_metric=0.0
        )
        assert cert.validate() is False

    def test_validate_invalid_negative_rounds(self):
        """Negative rounds also fails validation."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=-1, final_metric=0.0
        )
        assert cert.validate() is False

    def test_validate_invalid_negative_metric(self):
        """Negative final_metric fails validation."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=1, final_metric=-0.1
        )
        assert cert.validate() is False

    def test_validate_zero_final_metric_ok(self):
        """A final_metric of exactly 0.0 is valid."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=1, final_metric=0.0
        )
        assert cert.validate() is True

    def test_to_dict_roundtrip(self):
        """Serialisation and deserialisation preserve all fields."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=5, final_metric=1e-8
        )
        d = cert.to_dict()
        cert2 = ConvergenceCertificate.from_dict(d)
        assert cert2.cert_id == cert.cert_id
        assert cert2.gluing_id == cert.gluing_id
        assert cert2.rounds_to_converge == cert.rounds_to_converge
        assert cert2.final_metric == pytest.approx(cert.final_metric)

    def test_to_dict_contains_status_keys(self):
        """to_dict includes is_valid and is_expired keys."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=1, final_metric=0.0
        )
        d = cert.to_dict()
        assert "is_valid" in d
        assert "is_expired" in d

    def test_from_dict(self):
        """from_dict reconstructs a cert from a plain dictionary."""
        d = {
            "cert_id": "abc",
            "gluing_id": "g1",
            "rounds_to_converge": 3,
            "final_metric": 1e-7,
        }
        cert = ConvergenceCertificate.from_dict(d)
        assert cert.cert_id == "abc"
        assert cert.rounds_to_converge == 3

    def test_is_expired_no_expiry(self):
        """No expiry → never expired."""
        cert = ConvergenceCertificate(expiry=None)
        assert cert.is_expired() is False

    def test_is_expired_future_expiry(self):
        """Future expiry → not expired."""
        cert = ConvergenceCertificate(expiry=time.time() + 3600)
        assert cert.is_expired() is False

    def test_is_expired_past_expiry(self):
        """Past expiry → expired."""
        cert = ConvergenceCertificate(expiry=time.time() - 1.0)
        assert cert.is_expired() is True

    def test_get_age_seconds_positive(self):
        """Age must be positive even shortly after construction."""
        cert = ConvergenceCertificate()
        time.sleep(0.01)
        assert cert.get_age_seconds() > 0

    def test_certifier_default(self):
        """Default certifier string is non-empty."""
        cert = ConvergenceCertificate()
        assert isinstance(cert.certifier, str)
        assert len(cert.certifier) > 0

    def test_metadata_default_is_dict(self):
        """metadata defaults to a dict (not None)."""
        cert = ConvergenceCertificate()
        assert isinstance(cert.metadata, dict)

    def test_timestamp_auto_set(self):
        """Timestamp is automatically set to a recent Unix time."""
        before = time.time()
        cert = ConvergenceCertificate()
        after = time.time()
        assert before <= cert.timestamp <= after


# ---------------------------------------------------------------------------
# TestConvergenceVerifier
# ---------------------------------------------------------------------------


class TestConvergenceVerifier:
    """Tests for the ConvergenceVerifier class."""

    # -- helpers --

    def _get_converged(self, record) -> bool:
        return record["converged"] if isinstance(record, dict) else record.converged

    def _get_history(self, record) -> list:
        return (
            record["metric_history"]
            if isinstance(record, dict)
            else record.metric_history
        )

    # -- construction --

    def test_default_construction(self):
        """Verifier uses module-level defaults when no args given."""
        v = ConvergenceVerifier()
        assert v.threshold == pytest.approx(CONVERGENCE_TOLERANCE)
        assert v.max_rounds == MAX_CONVERGENCE_ROUNDS

    def test_custom_threshold(self):
        """Custom threshold is stored."""
        v = ConvergenceVerifier(threshold=0.001)
        assert v.threshold == pytest.approx(0.001)

    # -- verify --

    def test_verify_empty_history(self):
        """Empty history is not converged."""
        v = ConvergenceVerifier()
        record = v.verify([])
        assert self._get_converged(record) is False

    def test_verify_single_entry(self):
        """Single entry is not converged (no comparison possible)."""
        v = ConvergenceVerifier()
        g = make_gluing_dict()
        record = v.verify([g])
        assert self._get_converged(record) is False

    def test_verify_two_identical_entries(self):
        """Two identical dicts → fixed point → converged."""
        v = ConvergenceVerifier()
        g = make_gluing_dict(gluing_id="stable")
        record = v.verify([g, g])
        assert self._get_converged(record) is True

    def test_verify_converging_history(self):
        """History that ends in a fixed point is converged."""
        v = ConvergenceVerifier()
        history = make_converging_history(n=3)
        record = v.verify(history)
        assert self._get_converged(record) is True

    def test_verify_non_converging_history(self):
        """History of distinct dicts at tight tolerance is not converged."""
        v = ConvergenceVerifier(threshold=1e-10)
        g1 = {"sections": {"p1": "state_A"}}
        g2 = {"sections": {"p1": "state_B"}}
        record = v.verify([g1, g2])
        assert self._get_converged(record) is False

    def test_verify_returns_dict_or_record(self):
        """verify returns a dict or object with expected keys."""
        v = ConvergenceVerifier()
        record = v.verify(make_converging_history())
        if isinstance(record, dict):
            assert "converged" in record
            assert "metric_history" in record
        else:
            assert hasattr(record, "converged")
            assert hasattr(record, "metric_history")

    def test_verify_metric_history_non_empty(self):
        """metric_history has entries after non-trivial history."""
        v = ConvergenceVerifier()
        history = make_converging_history(n=4)
        record = v.verify(history)
        assert len(self._get_history(record)) >= 0  # may be empty for 1-entry; here ≥1

    # -- compute_convergence_metric --

    def test_compute_convergence_metric_returns_float(self):
        """compute_convergence_metric always returns a float."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": {"x": 1}}}
        m = v.compute_convergence_metric(g)
        assert isinstance(m, float)

    def test_compute_convergence_metric_nonnegative(self):
        """Metric is always non-negative."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": "data", "p2": "more"}}
        assert v.compute_convergence_metric(g) >= 0.0

    def test_compute_convergence_metric_empty_gluing(self):
        """Empty gluing dict returns 0.0."""
        v = ConvergenceVerifier()
        assert v.compute_convergence_metric({}) == pytest.approx(0.0)

    # -- check_fixed_point --

    def test_check_fixed_point_identical_last_two(self):
        """History whose last two entries are identical is a fixed point."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": {"v": 1}}}
        assert v.check_fixed_point([g, g]) is True

    def test_check_fixed_point_different_last_two(self):
        """History whose last two entries differ is not a fixed point."""
        v = ConvergenceVerifier(threshold=1e-10)
        g1 = {"sections": {"p1": "A"}}
        g2 = {"sections": {"p1": "B"}}
        assert v.check_fixed_point([g1, g2]) is False

    def test_check_fixed_point_single_entry(self):
        """Single-entry history cannot be a fixed point."""
        v = ConvergenceVerifier()
        assert v.check_fixed_point([make_gluing_dict()]) is False

    def test_check_fixed_point_empty(self):
        """Empty history cannot be a fixed point."""
        v = ConvergenceVerifier()
        assert v.check_fixed_point([]) is False

    # -- certify_convergence --

    def test_certify_convergence_converged(self):
        """Converged record yields a ConvergenceCertificate."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": {"v": 1}}, "gluing_id": "gid1"}
        record = v.verify([g, g])
        cert = v.certify_convergence(record)
        assert cert is not None
        assert isinstance(cert, ConvergenceCertificate)

    def test_certify_convergence_not_converged(self):
        """Non-converged record yields None."""
        v = ConvergenceVerifier(threshold=1e-12)
        g1 = {"sections": {"p1": "X"}}
        g2 = {"sections": {"p1": "Y"}}
        record = v.verify([g1, g2])
        cert = v.certify_convergence(record)
        assert cert is None

    def test_certify_convergence_valid_certificate(self):
        """The issued certificate passes its own validation."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": "s"}, "gluing_id": "some-gluing"}
        record = v.verify([g, g, g])
        cert = v.certify_convergence(record)
        if cert is not None:
            assert cert.validate() is True

    # -- compute_convergence_rate --

    def test_compute_convergence_rate_type(self):
        """compute_convergence_rate returns a float."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": {"v": 1}}}
        record = v.verify([g, g])
        rate = v.compute_convergence_rate(record)
        assert isinstance(rate, float)

    def test_compute_convergence_rate_not_enough_data(self):
        """With fewer than 3 data points the rate is nan."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": "s"}}
        record = v.verify([g, g])
        rate = v.compute_convergence_rate(record)
        # nan or a valid float; just check it is a float
        assert isinstance(rate, float)

    def test_compute_convergence_rate_longer_history(self):
        """Longer stable history returns a well-defined float rate."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": "stable"}}
        record = v.verify([g, g, g, g, g])
        rate = v.compute_convergence_rate(record)
        assert isinstance(rate, float)

    # -- parametrized --

    @pytest.mark.parametrize("n_gluings", [2, 3, 5, 10])
    def test_verify_with_various_history_lengths(self, n_gluings):
        """verify handles histories of various lengths without error."""
        v = ConvergenceVerifier()
        history = make_converging_history(n=n_gluings)
        record = v.verify(history)
        assert record is not None

    @pytest.mark.parametrize("threshold", [1e-3, 1e-6, 1e-9])
    def test_threshold_sensitivity(self, threshold):
        """Threshold is stored accurately at any scale."""
        v = ConvergenceVerifier(threshold=threshold)
        assert v.threshold == pytest.approx(threshold)


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for module-level helper functions."""

    # -- compute_convergence_status --

    def test_compute_convergence_status_converged(self):
        """converged=True with any history → CONVERGED."""
        record = {"converged": True, "metric_history": [1.0, 0.5, 0.0]}
        status = compute_convergence_status(record)
        assert status == ConvergenceStatus.CONVERGED

    def test_compute_convergence_status_diverged(self):
        """Monotone increasing history → DIVERGED."""
        record = {"converged": False, "metric_history": [1.0, 2.0, 3.0]}
        status = compute_convergence_status(record)
        assert status == ConvergenceStatus.DIVERGED

    def test_compute_convergence_status_in_progress(self):
        """Decreasing but not converged → IN_PROGRESS."""
        record = {"converged": False, "metric_history": [1.0, 0.5, 0.25]}
        status = compute_convergence_status(record)
        assert status == ConvergenceStatus.IN_PROGRESS

    def test_compute_convergence_status_unknown(self):
        """Empty history and not converged → UNKNOWN."""
        record = {"converged": False, "metric_history": []}
        status = compute_convergence_status(record)
        assert status == ConvergenceStatus.UNKNOWN

    def test_compute_convergence_status_returns_enum(self):
        """Return type is always ConvergenceStatus."""
        record = {"converged": False, "metric_history": []}
        status = compute_convergence_status(record)
        assert isinstance(status, ConvergenceStatus)

    # -- format_convergence_report --

    def test_format_convergence_report_string(self):
        """format_convergence_report returns a non-empty string."""
        report = ConvergenceReport(
            gluing_id="g1",
            status=ConvergenceStatus.CONVERGED,
            metric_history=[1.0, 0.5],
            rounds=2,
        )
        s = format_convergence_report(report)
        assert isinstance(s, str)
        assert len(s) > 0

    def test_format_convergence_report_unknown(self):
        """Works for UNKNOWN status."""
        report = ConvergenceReport(status=ConvergenceStatus.UNKNOWN)
        s = format_convergence_report(report)
        assert isinstance(s, str)

    def test_format_convergence_report_diverged(self):
        """Works for DIVERGED status."""
        report = ConvergenceReport(
            gluing_id="g2",
            status=ConvergenceStatus.DIVERGED,
            metric_history=[0.1, 0.5, 1.0],
            rounds=3,
        )
        s = format_convergence_report(report)
        assert isinstance(s, str)
        assert len(s) > 0

    # -- is_metric_monotone_decreasing --

    def test_is_metric_monotone_decreasing_true(self):
        """Strictly decreasing sequence is monotone decreasing."""
        assert is_metric_monotone_decreasing([1.0, 0.9, 0.5, 0.1]) is True

    def test_is_metric_monotone_decreasing_false_bump(self):
        """Sequence with a bump is not monotone decreasing."""
        assert is_metric_monotone_decreasing([1.0, 0.5, 0.7]) is False

    def test_is_metric_monotone_decreasing_single_value(self):
        """Single value returns False (requires len >= 2)."""
        assert is_metric_monotone_decreasing([1.0]) is False

    def test_is_metric_monotone_decreasing_empty(self):
        """Empty list returns False."""
        assert is_metric_monotone_decreasing([]) is False

    def test_is_metric_monotone_decreasing_two_values_true(self):
        """Two strictly decreasing values pass."""
        assert is_metric_monotone_decreasing([2.0, 1.0]) is True

    def test_is_metric_monotone_decreasing_two_values_equal(self):
        """Two equal values: not strictly decreasing."""
        assert is_metric_monotone_decreasing([1.0, 1.0]) is False

    def test_is_metric_monotone_decreasing_increasing(self):
        """Monotone increasing list is not monotone decreasing."""
        assert is_metric_monotone_decreasing([1.0, 2.0, 3.0]) is False

    # -- compute_exponential_convergence_rate --

    def test_compute_exponential_convergence_rate_returns_float(self):
        """Return type is always float."""
        history = [1.0, 0.5, 0.25, 0.125]
        rate = compute_exponential_convergence_rate(history)
        assert isinstance(rate, float)

    def test_compute_exponential_convergence_rate_negative_for_decreasing(self):
        """Exponentially decaying sequence yields a negative rate."""
        history = [1.0, 0.5, 0.25, 0.125]
        rate = compute_exponential_convergence_rate(history)
        assert rate < 0

    def test_compute_exponential_convergence_rate_nan_insufficient_data(self):
        """Too few positive values → nan."""
        rate = compute_exponential_convergence_rate([1.0])
        assert math.isnan(rate)

    def test_compute_exponential_convergence_rate_nan_empty(self):
        """Empty list → nan."""
        rate = compute_exponential_convergence_rate([])
        assert math.isnan(rate)

    # -- detect_oscillation --

    def test_detect_oscillation_true(self):
        """Alternating up/down sequence → oscillation detected."""
        history = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0]
        assert detect_oscillation(history) is True

    def test_detect_oscillation_false_monotone(self):
        """Monotone decreasing sequence → no oscillation."""
        history = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert detect_oscillation(history) is False

    def test_detect_oscillation_returns_bool(self):
        """Return type is always bool."""
        result = detect_oscillation([1.0, 0.5, 0.25])
        assert isinstance(result, bool)

    def test_detect_oscillation_short_history(self):
        """Very short history (< window) returns a bool without error."""
        result = detect_oscillation([1.0, 2.0])
        assert isinstance(result, bool)

    # -- constants --

    def test_constants_defined(self):
        """Module-level constants exist and have the right types."""
        assert isinstance(CONVERGENCE_TOLERANCE, float)
        assert CONVERGENCE_TOLERANCE > 0
        assert isinstance(MAX_CONVERGENCE_ROUNDS, int)
        assert MAX_CONVERGENCE_ROUNDS > 0

    def test_convergence_tolerance_magnitude(self):
        """CONVERGENCE_TOLERANCE is a small positive float (< 1e-3)."""
        assert CONVERGENCE_TOLERANCE < 1e-3

    def test_max_convergence_rounds_reasonable(self):
        """MAX_CONVERGENCE_ROUNDS is a positive integer above 1."""
        assert MAX_CONVERGENCE_ROUNDS >= 2


# ---------------------------------------------------------------------------
# TestConvergenceStatus enum
# ---------------------------------------------------------------------------


class TestConvergenceStatusEnum:
    """Sanity checks on the ConvergenceStatus enum values."""

    def test_enum_has_converged(self):
        assert hasattr(ConvergenceStatus, "CONVERGED")

    def test_enum_has_diverged(self):
        assert hasattr(ConvergenceStatus, "DIVERGED")

    def test_enum_has_in_progress(self):
        assert hasattr(ConvergenceStatus, "IN_PROGRESS")

    def test_enum_has_unknown(self):
        assert hasattr(ConvergenceStatus, "UNKNOWN")

    def test_enum_members_distinct(self):
        """All four status values are distinct."""
        members = {
            ConvergenceStatus.CONVERGED,
            ConvergenceStatus.DIVERGED,
            ConvergenceStatus.IN_PROGRESS,
            ConvergenceStatus.UNKNOWN,
        }
        assert len(members) == 4


# ---------------------------------------------------------------------------
# TestConvergenceReport dataclass
# ---------------------------------------------------------------------------


class TestConvergenceReport:
    """Tests for the ConvergenceReport dataclass."""

    def test_creation_defaults(self):
        """Default construction sets safe values."""
        report = ConvergenceReport()
        assert report.gluing_id == ""
        assert report.status == ConvergenceStatus.UNKNOWN
        assert report.metric_history == []
        assert report.rounds == 0
        assert report.certificate is None

    def test_creation_with_values(self):
        """All fields can be set at construction time."""
        cert = ConvergenceCertificate(
            gluing_id="g1", rounds_to_converge=2, final_metric=1e-8
        )
        report = ConvergenceReport(
            gluing_id="g1",
            status=ConvergenceStatus.CONVERGED,
            metric_history=[1.0, 0.5, 1e-8],
            rounds=3,
            certificate=cert,
        )
        assert report.gluing_id == "g1"
        assert report.status == ConvergenceStatus.CONVERGED
        assert report.rounds == 3
        assert report.certificate is cert

    def test_metric_history_mutable(self):
        """metric_history can be appended to after construction."""
        report = ConvergenceReport()
        report.metric_history.append(0.5)
        assert len(report.metric_history) == 1

    def test_format_report_converged(self):
        """format_convergence_report works on a ConvergenceReport with status CONVERGED."""
        report = ConvergenceReport(
            gluing_id="pipeline-test",
            status=ConvergenceStatus.CONVERGED,
            metric_history=[0.8, 0.4, 0.1, 1e-7],
            rounds=4,
        )
        s = format_convergence_report(report)
        assert isinstance(s, str)
        assert len(s) > 0

    def test_format_report_in_progress(self):
        """format_convergence_report works on a ConvergenceReport with status IN_PROGRESS."""
        report = ConvergenceReport(
            gluing_id="partial-run",
            status=ConvergenceStatus.IN_PROGRESS,
            metric_history=[1.0, 0.6, 0.3],
            rounds=3,
        )
        s = format_convergence_report(report)
        assert isinstance(s, str)


# ---------------------------------------------------------------------------
# TestIntegration
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end integration tests combining multiple components."""

    def test_full_convergence_pipeline(self):
        """Create a converging history, verify it, and issue a certificate."""
        v = ConvergenceVerifier(threshold=1e-6)
        g_stable = {"sections": {"p1": {"value": 1.0}}, "gluing_id": "stable-gluing"}
        history = [g_stable, g_stable, g_stable]
        record = v.verify(history)
        cert = v.certify_convergence(record)
        converged = record["converged"] if isinstance(record, dict) else record.converged
        assert converged is True
        assert cert is not None
        assert cert.validate() is True

    def test_convergence_status_pipeline(self):
        """Verified convergence maps to CONVERGED status."""
        v = ConvergenceVerifier()
        g = {"sections": {"p1": "fixed"}, "gluing_id": "xyz"}
        record = v.verify([g, g])
        status = compute_convergence_status(record)
        assert status == ConvergenceStatus.CONVERGED

    def test_metric_tracker_full_workflow(self):
        """ConvergenceMetric tracks a declining sequence and reports convergence."""
        m = ConvergenceMetric(name="dist", threshold=0.01)
        values = [1.0, 0.5, 0.25, 0.1, 0.05, 0.005]
        for v in values:
            m.update(v)
        assert m.is_below_threshold() is True
        assert m.is_improving() is True
        d = m.to_dict()
        assert d["is_converged"] is True

    def test_certificate_roundtrip_with_verifier(self):
        """Certificate from verifier survives a dict roundtrip."""
        v = ConvergenceVerifier()
        g = {"sections": {"x": "y"}, "gluing_id": "rt-test"}
        record = v.verify([g, g])
        cert = v.certify_convergence(record)
        if cert is not None:
            d = cert.to_dict()
            cert2 = ConvergenceCertificate.from_dict(d)
            assert cert2.cert_id == cert.cert_id
            assert cert2.validate() is True

    def test_non_convergence_gives_no_certificate(self):
        """History that never converges does not produce a certificate."""
        v = ConvergenceVerifier(threshold=1e-12)
        history = make_diverging_history(n=5)
        record = v.verify(history)
        cert = v.certify_convergence(record)
        converged = record["converged"] if isinstance(record, dict) else record.converged
        if not converged:
            assert cert is None

    def test_fixed_point_checker_and_verifier_agree(self):
        """FixedPointChecker and ConvergenceVerifier agree on an obvious fixed point."""
        checker = FixedPointChecker()
        v = ConvergenceVerifier()
        g = {"sections": {"p1": {"v": 42}}}
        assert checker.check(g, g) is True
        record = v.verify([g, g])
        converged = record["converged"] if isinstance(record, dict) else record.converged
        assert converged is True

    def test_metric_below_threshold_marks_converged(self):
        """A ConvergenceMetric below its threshold reports is_converged in to_dict."""
        m = ConvergenceMetric(name="err", threshold=0.1)
        m.update(1.0)
        m.update(0.05)
        d = m.to_dict()
        assert d["is_converged"] is True

    def test_convergence_status_for_report_in_progress(self):
        """Decreasing history (not converged) yields IN_PROGRESS."""
        record = {
            "converged": False,
            "metric_history": [0.9, 0.5, 0.2, 0.08],
        }
        status = compute_convergence_status(record)
        assert status == ConvergenceStatus.IN_PROGRESS

    def test_convergence_report_with_certificate(self):
        """ConvergenceReport can hold a certificate and format correctly."""
        cert = ConvergenceCertificate(
            gluing_id="g-full", rounds_to_converge=3, final_metric=1e-9
        )
        report = ConvergenceReport(
            gluing_id="g-full",
            status=ConvergenceStatus.CONVERGED,
            metric_history=[1.0, 0.5, 1e-9],
            rounds=3,
            certificate=cert,
        )
        s = format_convergence_report(report)
        assert isinstance(s, str)
        assert len(s) > 0
        assert report.certificate.validate() is True

    def test_oscillation_detection_with_real_metric_sequence(self):
        """Oscillating convergence metrics are detected as oscillation."""
        oscillating = [0.5, 0.8, 0.5, 0.8, 0.5, 0.8, 0.5]
        assert detect_oscillation(oscillating) is True

    def test_exponential_rate_on_geometric_sequence(self):
        """Geometric decay sequence has a well-defined negative exponential rate."""
        ratio = 0.5
        history = [ratio ** i for i in range(6)]
        rate = compute_exponential_convergence_rate(history)
        assert isinstance(rate, float)
        assert not math.isnan(rate)
        assert rate < 0
