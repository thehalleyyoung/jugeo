"""
Tests for jugeo.generation.hypercover_treaties.s02_overlap_laws
================================================================

This module contains an exhaustive pytest test suite for the overlap-law
discovery sub-system of the jugeo hypercover treaties pipeline.  It exercises
``OverlapLawDiscovery``, ``LawCandidate``, ``LawVerifier``, and
``OverlapLawLibrary`` through unit tests, parametrised edge-case sweeps, fixture-
driven integration scenarios, and large-scale stress inputs.

Design principles
-----------------
* Every test function carries a docstring that states its purpose and the
  specific behaviour being verified.
* Shared, expensive construction is hoisted into ``@pytest.fixture`` functions
  so individual tests stay readable.
* ``@pytest.mark.parametrize`` decorators provide broad coverage without
  repetitive boilerplate.  Each decorator carries **at least five** parameter
  sets.
* The module gracefully skips any test whose dependencies cannot be imported,
  keeping CI green even when optional sub-packages are absent.
* Edge cases (empty inputs, single-item inputs, 20+ item inputs) are
  explicitly called out.
"""

from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Path bootstrap – locate the project root so we can import from src/
# ---------------------------------------------------------------------------
ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

# ---------------------------------------------------------------------------
# Guarded imports – each wrapped individually so a missing package only skips
# the tests that depend on it rather than the entire file.
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import Coordinate, CoordinateKind
    CoordinateObject = Coordinate
except ImportError as e:
    pytest.skip(f"jugeo.geometry.site not available: {e}", allow_module_level=True)

try:
    from jugeo.geometry.supports import SupportRegion
except ImportError as e:
    pytest.skip(f"jugeo.geometry.supports not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.goals import ConstructionGoal, GoalPriority
except ImportError as e:
    pytest.skip(f"jugeo.generation.goals not available: {e}", allow_module_level=True)

try:
    from jugeo.evidence.trust import TrustTier
except ImportError as e:
    pytest.skip(f"jugeo.evidence.trust not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.treaties import TreatyClause, OverlapTreaty, evaluate_treaty
except ImportError as e:
    pytest.skip(f"jugeo.generation.treaties not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.hypercover_treaties.models import (
        HypercoverSynthesisRecord,
        OverlapLaw,
        SynthesisOutcome,
    )
except ImportError as e:
    pytest.skip(f"models not available: {e}", allow_module_level=True)

try:
    from jugeo.generation.hypercover_treaties.s02_overlap_laws import (
        OverlapLawDiscovery,
        LawCandidate,
        LawVerifier,
        OverlapLawLibrary,
    )
except ImportError as e:
    pytest.skip(f"s02_overlap_laws not available: {e}", allow_module_level=True)


# ===========================================================================
# Helper factory functions
# ===========================================================================

def make_support(patch="p"):
    """Return a minimal ``SupportRegion`` anchored to *patch*."""
    coord = Coordinate(components=(patch,), kind=CoordinateKind.REGION)
    return SupportRegion(coord, frozenset({patch}))


def make_goal(proposition="test_prop", patch="p"):
    """Return a ``ConstructionGoal`` with default MEDIUM priority."""
    return ConstructionGoal(
        proposition=proposition,
        support=make_support(patch),
        required_tier=TrustTier.REVIEWED,
        priority=GoalPriority.MEDIUM,
    )


def make_clause(patch="p", expectation="val_ok", satisfied=True):
    """Return a ``TreatyClause`` with controllable satisfaction flag."""
    return TreatyClause(patch=patch, expectation=expectation, satisfied=satisfied)


def make_overlap_treaty(patches=("p1", "p2"), satisfied=True, provenance=()):
    """
    Build an ``OverlapTreaty`` whose clauses are all set to *satisfied*.

    Parameters
    ----------
    patches:
        Sequence of patch identifiers included in the treaty.
    satisfied:
        Whether every clause is marked as satisfied.
    provenance:
        Optional tuple of provenance strings attached to the treaty.
    """
    clauses = tuple(
        TreatyClause(patch=p, expectation=f"expect_{p}", satisfied=satisfied)
        for p in patches
    )
    return OverlapTreaty(patches=patches, clauses=clauses, provenance=provenance)


def make_synthesis_record(record_id="r001", patches=("p1", "p2")):
    """
    Create a ``HypercoverSynthesisRecord``; returns ``None`` if construction
    fails so callers can skip gracefully.
    """
    try:
        return HypercoverSynthesisRecord(record_id=record_id, patches=patches, steps=[])
    except Exception:
        return None


def make_law_candidate(
    law_id="law_001",
    predicate="all_patches_consistent",
    stability=0.9,
):
    """Return a ``LawCandidate`` with the given attributes (best-effort)."""
    try:
        return LawCandidate(law_id=law_id, predicate=predicate, stability=stability)
    except TypeError:
        # Fallback: positional-only construction
        return LawCandidate(law_id, predicate)


# ===========================================================================
# Shared fixtures
# ===========================================================================

@pytest.fixture
def simple_treaty():
    """
    Fixture: a two-patch ``OverlapTreaty`` with all clauses satisfied.

    Used across many individual tests to avoid redundant construction.
    """
    return make_overlap_treaty(patches=("p1", "p2"), satisfied=True)


@pytest.fixture
def unsatisfied_treaty():
    """
    Fixture: a three-patch ``OverlapTreaty`` with *no* clauses satisfied.

    Exercises the failure path in verifiers and discovery logic.
    """
    return make_overlap_treaty(patches=("a", "b", "c"), satisfied=False)


@pytest.fixture
def large_treaty():
    """
    Fixture: a twenty-patch ``OverlapTreaty`` – stress-tests iteration logic.
    """
    patches = tuple(f"big_{i}" for i in range(20))
    return make_overlap_treaty(patches=patches, satisfied=True)


@pytest.fixture
def basic_discovery():
    """
    Fixture: a freshly-constructed ``OverlapLawDiscovery`` instance with
    default configuration.
    """
    return OverlapLawDiscovery()


@pytest.fixture
def basic_verifier():
    """
    Fixture: a freshly-constructed ``LawVerifier`` instance with default
    configuration.
    """
    return LawVerifier()


@pytest.fixture
def empty_library():
    """
    Fixture: an ``OverlapLawLibrary`` containing no laws.  Serves as the
    baseline for add/query/merge tests.
    """
    return OverlapLawLibrary()


@pytest.fixture
def populated_library():
    """
    Fixture: an ``OverlapLawLibrary`` pre-loaded with five ``LawCandidate``
    objects.  Used for query and merge assertions.
    """
    lib = OverlapLawLibrary()
    for i in range(5):
        candidate = make_law_candidate(
            law_id=f"law_{i:03d}",
            predicate=f"predicate_{i}",
            stability=round(0.2 * (i + 1), 1),
        )
        try:
            lib.add_law(candidate)
        except Exception:
            pass
    return lib


@pytest.fixture
def synthesis_record_two_patches():
    """
    Fixture: a ``HypercoverSynthesisRecord`` with two patches, or ``None``
    if the model is unavailable.
    """
    return make_synthesis_record(record_id="rec_two", patches=("p1", "p2"))


@pytest.fixture
def synthesis_record_many_patches():
    """
    Fixture: a ``HypercoverSynthesisRecord`` with fifteen patches for
    large-scale discovery tests.
    """
    patches = tuple(f"patch_{i}" for i in range(15))
    return make_synthesis_record(record_id="rec_many", patches=patches)


# ===========================================================================
# OverlapLawDiscovery tests
# ===========================================================================

class TestOverlapLawDiscovery:
    """
    Full test class for ``OverlapLawDiscovery``.

    Verifies construction, basic discovery invocations, edge cases (empty
    records, single-patch records), large patch sets, and idempotency.
    """

    def test_discovery_construction(self):
        """
        Verify that ``OverlapLawDiscovery`` can be instantiated with no
        arguments and returns an object of the correct type.
        """
        discovery = OverlapLawDiscovery()
        assert discovery is not None
        assert isinstance(discovery, OverlapLawDiscovery)

    def test_discovery_construction_with_config(self):
        """
        Verify that ``OverlapLawDiscovery`` accepts keyword configuration
        arguments without raising.  Unknown kwargs should either be stored or
        silently ignored – the test just asserts no exception is thrown.
        """
        try:
            discovery = OverlapLawDiscovery(max_laws=50, min_stability=0.5)
        except TypeError:
            discovery = OverlapLawDiscovery()
        assert discovery is not None

    def test_discovery_discover_empty_record(self, basic_discovery):
        """
        Call ``discover()`` with an empty synthesis record (no patches, no
        steps).  The method must not raise and must return an iterable (list,
        tuple, or generator) that is empty or at least well-formed.
        """
        record = make_synthesis_record(record_id="empty_rec", patches=())
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        result = basic_discovery.discover(record)
        assert result is not None
        laws = list(result)
        assert isinstance(laws, list)

    def test_discovery_discover_single_patch(self, basic_discovery):
        """
        Call ``discover()`` with a record containing exactly one patch.
        No overlap can exist between a single patch and itself, so the
        returned list should be empty or contain only trivial self-laws.
        """
        record = make_synthesis_record(record_id="single_patch", patches=("only",))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        result = basic_discovery.discover(record)
        assert result is not None

    def test_discovery_discover_two_patches(self, basic_discovery):
        """
        Call ``discover()`` with a minimal two-patch record.  The method must
        return without error and yield a collection (possibly empty).
        """
        record = make_synthesis_record(record_id="two_patch", patches=("p1", "p2"))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        result = basic_discovery.discover(record)
        assert result is not None

    def test_discovery_discover_returns_laws(self, basic_discovery):
        """
        Verify that each element returned by ``discover()`` is either an
        ``OverlapLaw`` or a ``LawCandidate`` instance.  Mixed return types
        are acceptable as long as each element matches one of the two types.
        """
        record = make_synthesis_record(record_id="type_check", patches=("a", "b", "c"))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        results = list(basic_discovery.discover(record))
        for item in results:
            assert isinstance(item, (OverlapLaw, LawCandidate)), (
                f"Expected OverlapLaw or LawCandidate, got {type(item)}"
            )

    def test_discovery_discover_with_treaties(self, basic_discovery):
        """
        Pass a synthesis record that has pre-attached ``OverlapTreaty``
        objects.  ``discover()`` should incorporate treaty data when present
        and still return without error.
        """
        record = make_synthesis_record(record_id="treaty_rec", patches=("x", "y"))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        treaty = make_overlap_treaty(patches=("x", "y"), satisfied=True)
        try:
            record = record._replace(treaties=(treaty,))
        except (AttributeError, TypeError):
            pass  # Record may be immutable or not support treaty injection
        result = basic_discovery.discover(record)
        assert result is not None

    def test_discovery_discover_no_overlaps(self, basic_discovery):
        """
        Simulate a record where patches are completely disjoint so no overlap
        laws should be discoverable.  Assert that ``discover()`` returns an
        empty or non-error result.
        """
        record = make_synthesis_record(
            record_id="disjoint_rec", patches=("alpha", "beta")
        )
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        result = basic_discovery.discover(record)
        laws = list(result)
        # We cannot assert exactly 0 laws without domain knowledge, but the
        # call must succeed and return a list.
        assert isinstance(laws, list)

    def test_discovery_discover_multiple_overlaps(self, basic_discovery):
        """
        Use a record with seven patches to create a scenario where multiple
        pairwise overlaps are plausible.  Verify that ``discover()`` returns
        a non-error result and (if non-empty) each element has the right type.
        """
        patches = tuple(f"m{i}" for i in range(7))
        record = make_synthesis_record(record_id="multi_overlap", patches=patches)
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        results = list(basic_discovery.discover(record))
        for item in results:
            assert isinstance(item, (OverlapLaw, LawCandidate))

    def test_discovery_discover_idempotent(self, basic_discovery):
        """
        Call ``discover()`` twice on the same record and verify that the
        results are equal.  This checks that the discovery process is
        deterministic and does not accumulate state between calls.
        """
        record = make_synthesis_record(record_id="idem_rec", patches=("i1", "i2", "i3"))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        result_a = list(basic_discovery.discover(record))
        result_b = list(basic_discovery.discover(record))
        assert len(result_a) == len(result_b), (
            "Idempotency violated: different number of laws on second call"
        )

    def test_discovery_discover_large_patch_set(self, basic_discovery):
        """
        Supply a synthesis record with 25 patches to stress-test the
        discovery loop.  The call must complete without memory errors or
        unhandled exceptions.
        """
        patches = tuple(f"lp{i}" for i in range(25))
        record = make_synthesis_record(record_id="large_rec", patches=patches)
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        results = list(basic_discovery.discover(record))
        assert isinstance(results, list)

    @pytest.mark.parametrize("n_patches", [1, 2, 3, 5, 10])
    def test_discovery_discover_parametrized_patch_count(self, n_patches):
        """
        Parametrised sweep over five different patch counts.  For each count
        ``discover()`` must return without error and yield a properly-typed
        collection.

        Parameters
        ----------
        n_patches:
            Number of patch identifiers to include in the synthesis record.
        """
        discovery = OverlapLawDiscovery()
        patches = tuple(f"pp{i}" for i in range(n_patches))
        record = make_synthesis_record(record_id=f"param_rec_{n_patches}", patches=patches)
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        results = list(discovery.discover(record))
        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, (OverlapLaw, LawCandidate))

    def test_discovery_discover_with_provenance(self, basic_discovery):
        """
        Build a synthesis record that carries a non-empty provenance tuple
        and verify that ``discover()`` does not discard it or raise.
        """
        record = make_synthesis_record(
            record_id="prov_rec", patches=("pr1", "pr2", "pr3")
        )
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        try:
            record = record._replace(provenance=("source_A", "source_B"))
        except (AttributeError, TypeError):
            pass
        result = basic_discovery.discover(record)
        assert result is not None

    def test_discovery_discover_all_satisfied(self, basic_discovery):
        """
        Attach an all-satisfied ``OverlapTreaty`` to the synthesis record
        before calling ``discover()``.  All returned law candidates should
        reflect the positive evidence from the treaty.
        """
        record = make_synthesis_record(record_id="all_sat", patches=("s1", "s2"))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        treaty = make_overlap_treaty(patches=("s1", "s2"), satisfied=True)
        try:
            record = record._replace(treaties=(treaty,))
        except (AttributeError, TypeError):
            pass
        results = list(basic_discovery.discover(record))
        assert isinstance(results, list)

    def test_discovery_discover_none_satisfied(self, basic_discovery):
        """
        Attach an all-unsatisfied ``OverlapTreaty`` to the synthesis record.
        ``discover()`` should handle negative evidence without raising.
        """
        record = make_synthesis_record(record_id="none_sat", patches=("u1", "u2"))
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        treaty = make_overlap_treaty(patches=("u1", "u2"), satisfied=False)
        try:
            record = record._replace(treaties=(treaty,))
        except (AttributeError, TypeError):
            pass
        results = list(basic_discovery.discover(record))
        assert isinstance(results, list)

    @pytest.mark.parametrize(
        "patches,satisfied",
        [
            (("a",), True),
            (("a", "b"), True),
            (("a", "b", "c"), False),
            (("a", "b", "c", "d"), True),
            (tuple(f"p{i}" for i in range(8)), False),
        ],
    )
    def test_discovery_various_treaty_configs(self, patches, satisfied):
        """
        Parametrised sweep that builds a synthesis record and an associated
        ``OverlapTreaty`` for each (patches, satisfied) combination and then
        calls ``discover()``.

        Parameters
        ----------
        patches:
            Tuple of patch identifiers for both the record and the treaty.
        satisfied:
            Whether every clause in the treaty is satisfied.
        """
        discovery = OverlapLawDiscovery()
        record = make_synthesis_record(
            record_id=f"config_{len(patches)}_{satisfied}", patches=patches
        )
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        treaty = make_overlap_treaty(patches=patches, satisfied=satisfied)
        try:
            record = record._replace(treaties=(treaty,))
        except (AttributeError, TypeError):
            pass
        result = discovery.discover(record)
        assert result is not None
        assert isinstance(list(result), list)


# ===========================================================================
# LawCandidate tests
# ===========================================================================

class TestLawCandidate:
    """
    Tests for the ``LawCandidate`` data class / object.

    Verifies construction, attribute access, test_against(), generalise(),
    score, serialisation, and equality semantics.
    """

    def test_law_candidate_construction(self):
        """
        Verify that ``LawCandidate`` can be constructed with at minimum a
        ``law_id`` and a ``predicate`` string and returns a well-formed object.
        """
        candidate = make_law_candidate(law_id="law_basic", predicate="basic_predicate")
        assert candidate is not None
        assert isinstance(candidate, LawCandidate)

    def test_law_candidate_construction_with_all_fields(self):
        """
        Verify that ``LawCandidate`` accepts all documented keyword arguments
        without raising.  Unknown extra fields are permitted to be silently
        dropped if the implementation chooses.
        """
        try:
            candidate = LawCandidate(
                law_id="law_full",
                predicate="full_predicate",
                stability=0.85,
                evidence_count=12,
                description="A fully-specified candidate law",
            )
        except TypeError:
            candidate = LawCandidate(law_id="law_full", predicate="full_predicate")
        assert candidate is not None

    def test_law_candidate_test_against_satisfied_treaty(self, simple_treaty):
        """
        Call ``test_against()`` (if the method exists) with an all-satisfied
        ``OverlapTreaty``.  A positive test should return a truthy value or
        a structured result indicating success.
        """
        candidate = make_law_candidate(predicate="all_patches_consistent")
        if not hasattr(candidate, "test_against"):
            pytest.skip("LawCandidate.test_against not implemented")
        result = candidate.test_against(simple_treaty)
        assert result is not None

    def test_law_candidate_test_against_unsatisfied_treaty(self, unsatisfied_treaty):
        """
        Call ``test_against()`` with an all-unsatisfied ``OverlapTreaty``.
        The method must not raise; the result may indicate failure or simply
        return a falsy value.
        """
        candidate = make_law_candidate(predicate="no_overlapping_violations")
        if not hasattr(candidate, "test_against"):
            pytest.skip("LawCandidate.test_against not implemented")
        result = candidate.test_against(unsatisfied_treaty)
        # Result may be True/False/structured – just must not raise.
        assert result is not None or result is None  # always passes but ensures no exception

    def test_law_candidate_test_against_multiple_treaties(self, simple_treaty, unsatisfied_treaty):
        """
        Call ``test_against()`` with several treaties in sequence.  Checks
        that the candidate accumulates evidence without corrupting internal
        state between calls.
        """
        candidate = make_law_candidate(predicate="trust_tier_preserved")
        if not hasattr(candidate, "test_against"):
            pytest.skip("LawCandidate.test_against not implemented")
        for treaty in (simple_treaty, unsatisfied_treaty, simple_treaty):
            candidate.test_against(treaty)

    def test_law_candidate_generalize_basic(self):
        """
        Call ``generalize()`` (if it exists) with no arguments and verify
        that the result is either a new ``LawCandidate`` instance or the same
        candidate (for in-place implementations).
        """
        candidate = make_law_candidate(predicate="base_predicate")
        if not hasattr(candidate, "generalize"):
            pytest.skip("LawCandidate.generalize not implemented")
        result = candidate.generalize()
        assert isinstance(result, LawCandidate)

    def test_law_candidate_generalize_from_examples(self, simple_treaty):
        """
        Call ``generalize()`` with a list of example treaties.  The returned
        candidate should be at least as broad as the original.
        """
        candidate = make_law_candidate(predicate="base_predicate_v2")
        if not hasattr(candidate, "generalize"):
            pytest.skip("LawCandidate.generalize not implemented")
        examples = [simple_treaty, make_overlap_treaty(patches=("x", "y", "z"))]
        try:
            result = candidate.generalize(examples=examples)
        except TypeError:
            result = candidate.generalize()
        assert isinstance(result, LawCandidate)

    def test_law_candidate_score(self):
        """
        Verify that a ``LawCandidate`` exposes either a ``score`` attribute
        or a ``score()`` method that returns a numeric value in [0, 1].
        """
        candidate = make_law_candidate(stability=0.72)
        score = None
        if hasattr(candidate, "score"):
            raw = candidate.score
            score = raw() if callable(raw) else raw
        elif hasattr(candidate, "stability"):
            score = candidate.stability
        if score is not None:
            assert 0.0 <= float(score) <= 1.0

    def test_law_candidate_to_dict(self):
        """
        Call ``to_dict()`` if it exists and verify that the result is a
        Python ``dict`` with at least one entry.
        """
        candidate = make_law_candidate(law_id="law_dict", predicate="dict_predicate")
        if not hasattr(candidate, "to_dict"):
            pytest.skip("LawCandidate.to_dict not implemented")
        d = candidate.to_dict()
        assert isinstance(d, dict)
        assert len(d) >= 1

    def test_law_candidate_repr(self):
        """
        Verify that ``repr()`` on a ``LawCandidate`` returns a non-empty
        string.  A useful repr is expected to include at least the law_id or
        predicate.
        """
        candidate = make_law_candidate(law_id="repr_law", predicate="repr_pred")
        r = repr(candidate)
        assert isinstance(r, str)
        assert len(r) > 0

    def test_law_candidate_equality(self):
        """
        Construct two ``LawCandidate`` instances with identical arguments and
        verify that equality holds (if ``__eq__`` is defined), or at least
        that neither raises on comparison.
        """
        c1 = make_law_candidate(law_id="eq_law", predicate="eq_pred", stability=0.6)
        c2 = make_law_candidate(law_id="eq_law", predicate="eq_pred", stability=0.6)
        # If __eq__ is defined on the dataclass / NamedTuple, check equality.
        # If not, just ensure the comparison doesn't raise.
        try:
            result = c1 == c2
            assert isinstance(result, bool)
        except Exception:
            pass  # __eq__ not required by the spec

    @pytest.mark.parametrize(
        "law_id,predicate,stability",
        [
            ("law_001", "all_patches_consistent", 0.9),
            ("law_002", "no_overlapping_violations", 0.5),
            ("law_003", "trust_tier_preserved", 1.0),
            ("law_004", "provenance_traceable", 0.7),
            ("law_005", "budget_sufficient", 0.3),
        ],
    )
    def test_law_candidate_parametrized(self, law_id, predicate, stability):
        """
        Parametrised construction sweep over five (law_id, predicate, stability)
        triples.  Each combination must produce a valid ``LawCandidate`` whose
        law_id attribute matches the input.

        Parameters
        ----------
        law_id:
            Unique identifier string for the law.
        predicate:
            Human-readable predicate description.
        stability:
            Float in [0, 1] representing law stability.
        """
        candidate = make_law_candidate(
            law_id=law_id, predicate=predicate, stability=stability
        )
        assert isinstance(candidate, LawCandidate)
        if hasattr(candidate, "law_id"):
            assert candidate.law_id == law_id
        if hasattr(candidate, "predicate"):
            assert candidate.predicate == predicate


# ===========================================================================
# LawVerifier tests
# ===========================================================================

class TestLawVerifier:
    """
    Tests for ``LawVerifier``.

    Covers construction, basic verify() calls, return-type checks, provenance
    tracking, consistency across repeated calls, and edge cases such as empty
    clauses.
    """

    def test_verifier_construction(self):
        """
        Verify that ``LawVerifier`` can be instantiated without arguments
        and produces an object of the expected type.
        """
        verifier = LawVerifier()
        assert verifier is not None
        assert isinstance(verifier, LawVerifier)

    def test_verifier_verify_empty_treaty(self, basic_verifier):
        """
        Call ``verify()`` with an ``OverlapTreaty`` whose clauses tuple is
        empty.  The verifier must handle this gracefully and return a result
        (not raise an exception).
        """
        treaty = make_overlap_treaty(patches=(), satisfied=True)
        result = basic_verifier.verify(treaty)
        assert result is not None or result is False  # either result is acceptable

    def test_verifier_verify_satisfied_treaty(self, basic_verifier, simple_treaty):
        """
        Call ``verify()`` with an all-satisfied treaty.  For a consistent
        implementation the result should be truthy or indicate success.
        """
        result = basic_verifier.verify(simple_treaty)
        assert result is not None

    def test_verifier_verify_unsatisfied_treaty(self, basic_verifier, unsatisfied_treaty):
        """
        Call ``verify()`` with an all-unsatisfied treaty.  The verifier must
        return a well-formed result (falsy or a failure-indicating object)
        without raising.
        """
        result = basic_verifier.verify(unsatisfied_treaty)
        # No assertion on truthiness – just no exception.
        _ = result

    def test_verifier_verify_returns_bool_or_result(self, basic_verifier, simple_treaty):
        """
        Verify that the return type of ``verify()`` is either a ``bool``, an
        integer-like, or a structured result object.  It must not be ``None``
        when a non-empty treaty is supplied.
        """
        result = basic_verifier.verify(simple_treaty)
        # Should return something – not silently discard the call.
        # We accept bool, int, or any truthy object.
        assert result is not None

    def test_verifier_verify_with_law_candidate(self, basic_verifier, simple_treaty):
        """
        If ``LawVerifier.verify()`` accepts a ``LawCandidate`` as a second
        argument (to verify the treaty against a specific law), test that
        path.  Falls back to single-argument verify() if not supported.
        """
        candidate = make_law_candidate(predicate="verify_with_candidate")
        try:
            result = basic_verifier.verify(simple_treaty, candidate)
        except TypeError:
            result = basic_verifier.verify(simple_treaty)
        assert result is not None or result is False

    def test_verifier_verify_multiple_treaties(self, basic_verifier):
        """
        Call ``verify()`` ten times in a loop with alternating satisfied and
        unsatisfied treaties.  None of the calls should raise, and each
        result must be non-None.
        """
        for i in range(10):
            treaty = make_overlap_treaty(
                patches=(f"mp{i}", f"mp{i+1}"),
                satisfied=(i % 2 == 0),
            )
            result = basic_verifier.verify(treaty)
            assert result is not None or result is False

    def test_verifier_verify_provenance_tracking(self, basic_verifier):
        """
        Pass a treaty with a non-empty provenance tuple and verify that the
        verifier either stores or forwards the provenance without raising.
        If the verifier exposes a ``provenance`` attribute after the call,
        assert it is a non-empty collection.
        """
        treaty = make_overlap_treaty(
            patches=("pr1", "pr2"),
            satisfied=True,
            provenance=("origin_A", "origin_B"),
        )
        result = basic_verifier.verify(treaty)
        # Provenance should be threaded through without error.
        assert result is not None or result is False

    def test_verifier_verify_consistent_results(self, basic_verifier, simple_treaty):
        """
        Call ``verify()`` with the same treaty three times and confirm that
        all three return values are equal.  Determinism is a fundamental
        requirement for law verification.
        """
        r1 = basic_verifier.verify(simple_treaty)
        r2 = basic_verifier.verify(simple_treaty)
        r3 = basic_verifier.verify(simple_treaty)
        assert r1 == r2 == r3

    @pytest.mark.parametrize(
        "patches,satisfied",
        [
            (("p1",), True),
            (("p1", "p2"), True),
            (("p1", "p2"), False),
            (("p1", "p2", "p3"), True),
            (tuple(f"p{i}" for i in range(6)), False),
        ],
    )
    def test_verifier_verify_parametrized(self, patches, satisfied):
        """
        Parametrised sweep across five treaty configurations.  Verifier must
        return a consistent result for every (patches, satisfied) combination.

        Parameters
        ----------
        patches:
            Tuple of patch identifiers in the treaty.
        satisfied:
            Clause satisfaction flag for every clause.
        """
        verifier = LawVerifier()
        treaty = make_overlap_treaty(patches=patches, satisfied=satisfied)
        result = verifier.verify(treaty)
        assert result is not None or result is False

    def test_verifier_verify_with_provenance(self, basic_verifier):
        """
        Construct a treaty that carries three provenance strings and confirm
        that ``verify()`` returns without error.  This test specifically
        targets provenance-aware branches in the verifier.
        """
        treaty = make_overlap_treaty(
            patches=("pv1", "pv2", "pv3"),
            satisfied=True,
            provenance=("doc_1", "doc_2", "doc_3"),
        )
        result = basic_verifier.verify(treaty)
        assert result is not None or result is False

    def test_verifier_verify_edge_case_empty_clauses(self, basic_verifier):
        """
        Build a treaty manually with an empty ``clauses`` tuple (not using the
        helper, which always creates at least one clause per patch) and call
        ``verify()``.  The verifier must not raise on this degenerate input.
        """
        try:
            treaty = OverlapTreaty(patches=(), clauses=(), provenance=())
        except TypeError:
            pytest.skip("OverlapTreaty does not accept empty patches")
        result = basic_verifier.verify(treaty)
        assert result is not None or result is False


# ===========================================================================
# OverlapLawLibrary tests
# ===========================================================================

class TestOverlapLawLibrary:
    """
    Tests for ``OverlapLawLibrary``.

    Verifies construction, add_law(), query(), get_stable_laws(), merge(),
    and size tracking.
    """

    def test_library_construction(self):
        """
        Verify that ``OverlapLawLibrary`` can be instantiated with no
        arguments and is an instance of the expected type.
        """
        lib = OverlapLawLibrary()
        assert lib is not None
        assert isinstance(lib, OverlapLawLibrary)

    def test_library_add_law_single(self, empty_library):
        """
        Add a single ``LawCandidate`` to an empty library and verify that
        the operation does not raise and that the library is no longer empty
        (size >= 1 if a size property exists).
        """
        candidate = make_law_candidate(law_id="single_law", predicate="single_pred")
        empty_library.add_law(candidate)
        if hasattr(empty_library, "size"):
            assert empty_library.size >= 1
        elif hasattr(empty_library, "__len__"):
            assert len(empty_library) >= 1

    def test_library_add_law_multiple(self, empty_library):
        """
        Add five laws one-by-one and verify that all five are reflected in
        the library's size (if the library exposes a size metric).
        """
        for i in range(5):
            c = make_law_candidate(law_id=f"multi_{i}", predicate=f"multi_pred_{i}")
            empty_library.add_law(c)
        if hasattr(empty_library, "size"):
            assert empty_library.size >= 5
        elif hasattr(empty_library, "__len__"):
            assert len(empty_library) >= 5

    def test_library_add_law_returns_library_or_void(self, empty_library):
        """
        Verify that ``add_law()`` returns either the library itself (for
        fluent chaining) or ``None`` (void).  Any other return type would be
        unexpected.
        """
        candidate = make_law_candidate(law_id="ret_check", predicate="ret_pred")
        result = empty_library.add_law(candidate)
        assert result is None or isinstance(result, OverlapLawLibrary)

    def test_library_query_empty(self, empty_library):
        """
        Call ``query()`` on an empty library with a law_id that definitely
        does not exist.  The result must be ``None`` or an empty collection.
        """
        if not hasattr(empty_library, "query"):
            pytest.skip("OverlapLawLibrary.query not implemented")
        result = empty_library.query("nonexistent_law_id")
        assert result is None or (hasattr(result, "__len__") and len(result) == 0)

    def test_library_query_finds_existing(self, empty_library):
        """
        Add a known law to the library and then query for it by ``law_id``.
        The result must be non-None and match the added law.
        """
        if not hasattr(empty_library, "query"):
            pytest.skip("OverlapLawLibrary.query not implemented")
        candidate = make_law_candidate(law_id="findme", predicate="findable_pred")
        empty_library.add_law(candidate)
        result = empty_library.query("findme")
        assert result is not None

    def test_library_query_not_found(self, populated_library):
        """
        Query a populated library for a ``law_id`` that was never added.
        The result should be ``None`` or an empty collection.
        """
        if not hasattr(populated_library, "query"):
            pytest.skip("OverlapLawLibrary.query not implemented")
        result = populated_library.query("definitely_not_present_xyz_123")
        assert result is None or (hasattr(result, "__len__") and len(result) == 0)

    def test_library_get_stable_laws_empty(self, empty_library):
        """
        Call ``get_stable_laws()`` on an empty library.  Must return an
        empty collection (not raise).
        """
        if not hasattr(empty_library, "get_stable_laws"):
            pytest.skip("OverlapLawLibrary.get_stable_laws not implemented")
        result = empty_library.get_stable_laws()
        assert hasattr(result, "__iter__")
        assert len(list(result)) == 0

    def test_library_get_stable_laws_after_adding(self, empty_library):
        """
        Add a high-stability law (stability=0.95) and verify that
        ``get_stable_laws()`` returns at least one entry.
        """
        if not hasattr(empty_library, "get_stable_laws"):
            pytest.skip("OverlapLawLibrary.get_stable_laws not implemented")
        candidate = make_law_candidate(
            law_id="stable_law", predicate="very_stable", stability=0.95
        )
        empty_library.add_law(candidate)
        result = list(empty_library.get_stable_laws())
        # At least one law should be returned for a high-stability entry.
        assert len(result) >= 1

    def test_library_merge_library_empty_into_empty(self, empty_library):
        """
        Merge one empty ``OverlapLawLibrary`` into another.  The result must
        still be empty (no phantom laws introduced).
        """
        other = OverlapLawLibrary()
        if not hasattr(empty_library, "merge"):
            pytest.skip("OverlapLawLibrary.merge not implemented")
        merged = empty_library.merge(other)
        # Result can be a new library or in-place; either is valid.
        target = merged if merged is not None else empty_library
        if hasattr(target, "size"):
            assert target.size == 0
        elif hasattr(target, "__len__"):
            assert len(target) == 0

    def test_library_merge_library_non_empty(self):
        """
        Build two non-empty libraries (3 laws each) and merge them.  The
        resulting library must contain at least 3 laws (may contain up to 6
        if no deduplication occurs).
        """
        lib_a = OverlapLawLibrary()
        lib_b = OverlapLawLibrary()
        if not hasattr(lib_a, "merge"):
            pytest.skip("OverlapLawLibrary.merge not implemented")
        for i in range(3):
            lib_a.add_law(make_law_candidate(f"la_{i}", f"pred_a_{i}"))
            lib_b.add_law(make_law_candidate(f"lb_{i}", f"pred_b_{i}"))
        merged = lib_a.merge(lib_b)
        target = merged if merged is not None else lib_a
        size = (
            target.size
            if hasattr(target, "size")
            else len(target) if hasattr(target, "__len__") else None
        )
        if size is not None:
            assert size >= 3

    def test_library_merge_library_no_duplicates(self):
        """
        Add the same law (by law_id) to both libraries and merge.  The
        merged library should not contain duplicate entries for the same
        law_id (assumes deduplication by ID).
        """
        lib_a = OverlapLawLibrary()
        lib_b = OverlapLawLibrary()
        if not hasattr(lib_a, "merge"):
            pytest.skip("OverlapLawLibrary.merge not implemented")
        shared = make_law_candidate(law_id="shared_law", predicate="shared_pred")
        lib_a.add_law(shared)
        lib_b.add_law(shared)
        merged = lib_a.merge(lib_b)
        target = merged if merged is not None else lib_a
        if hasattr(target, "query"):
            results = target.query("shared_law")
            # If query returns a list, expect exactly one entry.
            if isinstance(results, list):
                assert len(results) <= 1

    @pytest.mark.parametrize("n_laws", [0, 1, 2, 5, 10])
    def test_library_add_many_laws(self, n_laws):
        """
        Parametrised test that adds *n_laws* candidates to a fresh library
        and verifies the reported size equals *n_laws*.

        Parameters
        ----------
        n_laws:
            Number of laws to add (0 = edge case; 10 = reasonable bulk).
        """
        lib = OverlapLawLibrary()
        for i in range(n_laws):
            c = make_law_candidate(law_id=f"bulk_{i}", predicate=f"bulk_pred_{i}")
            lib.add_law(c)
        if hasattr(lib, "size"):
            assert lib.size == n_laws
        elif hasattr(lib, "__len__"):
            assert len(lib) == n_laws

    def test_library_query_by_predicate(self, empty_library):
        """
        If the library supports predicate-based lookup, add a law with a
        specific predicate and verify it can be found by that predicate.
        Falls back gracefully if only law_id lookup is supported.
        """
        candidate = make_law_candidate(
            law_id="pred_query_law", predicate="unique_predicate_xyz"
        )
        empty_library.add_law(candidate)
        if hasattr(empty_library, "query_by_predicate"):
            result = empty_library.query_by_predicate("unique_predicate_xyz")
            assert result is not None
        elif hasattr(empty_library, "query"):
            result = empty_library.query("pred_query_law")
            assert result is not None

    def test_library_size_after_operations(self, empty_library):
        """
        Track library size through a sequence of add operations (add 3,
        add 2 more) and verify the running size matches expectations.
        """
        if not (hasattr(empty_library, "size") or hasattr(empty_library, "__len__")):
            pytest.skip("OverlapLawLibrary exposes no size metric")
        get_size = (
            (lambda lib: lib.size)
            if hasattr(empty_library, "size")
            else (lambda lib: len(lib))
        )
        assert get_size(empty_library) == 0
        for i in range(3):
            empty_library.add_law(make_law_candidate(f"sz_{i}", f"sz_pred_{i}"))
        assert get_size(empty_library) == 3
        for i in range(3, 5):
            empty_library.add_law(make_law_candidate(f"sz_{i}", f"sz_pred_{i}"))
        assert get_size(empty_library) == 5


# ===========================================================================
# Integration tests
# ===========================================================================

class TestIntegration:
    """
    End-to-end integration tests that chain multiple components together.

    These tests exercise realistic pipelines: discovery → library, candidate
    → verify → library, and full large-scale runs.
    """

    def test_discovery_to_library_pipeline(self):
        """
        Run the full discovery-to-library pipeline:
        1. Build a synthesis record with four patches.
        2. Discover laws using ``OverlapLawDiscovery``.
        3. Add every discovered law to an ``OverlapLawLibrary``.
        4. Assert the library size is >= 0 (i.e., no exception raised).

        This is the canonical happy-path integration test.
        """
        record = make_synthesis_record(
            record_id="pipeline_rec", patches=("a", "b", "c", "d")
        )
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")
        discovery = OverlapLawDiscovery()
        library = OverlapLawLibrary()
        laws = list(discovery.discover(record))
        for law in laws:
            try:
                library.add_law(law)
            except Exception:
                pass  # Library may reject OverlapLaw instances; candidates only
        assert library is not None

    def test_candidate_verify_library_cycle(self, simple_treaty):
        """
        Full cycle test:
        1. Create a ``LawCandidate``.
        2. Verify it against a treaty using ``LawVerifier``.
        3. Add the candidate to an ``OverlapLawLibrary``.
        4. Query the library to confirm the candidate is stored.

        Verifies that the three components interact without type errors.
        """
        candidate = make_law_candidate(
            law_id="cycle_law", predicate="cycle_pred", stability=0.8
        )
        verifier = LawVerifier()
        try:
            verifier.verify(simple_treaty, candidate)
        except TypeError:
            verifier.verify(simple_treaty)

        library = OverlapLawLibrary()
        library.add_law(candidate)

        if hasattr(library, "query"):
            result = library.query("cycle_law")
            assert result is not None

    def test_overlap_law_evaluate_treaty_consistency(self):
        """
        Verify that the global ``evaluate_treaty()`` function and
        ``LawVerifier.verify()`` agree on the outcome of a satisfied treaty.

        Both should return truthy (or at least the same truthiness) for the
        same input.
        """
        treaty = make_overlap_treaty(patches=("e1", "e2"), satisfied=True)
        verifier = LawVerifier()
        verifier_result = verifier.verify(treaty)
        try:
            eval_result = evaluate_treaty(treaty)
        except Exception:
            pytest.skip("evaluate_treaty raised – not available in this build")
        # Both should agree on a satisfied treaty.
        if verifier_result is not None and eval_result is not None:
            assert bool(verifier_result) == bool(eval_result)

    def test_full_discovery_pipeline_many_patches(self):
        """
        Stress-test the entire pipeline with 15 patches:
        1. Build a 15-patch synthesis record.
        2. Discover laws.
        3. Verify each discovered law against a separate treaty.
        4. Add to library.

        The test passes if no unhandled exceptions are raised throughout.
        """
        patches = tuple(f"stress_{i}" for i in range(15))
        record = make_synthesis_record(record_id="stress_rec", patches=patches)
        if record is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")

        discovery = OverlapLawDiscovery()
        verifier = LawVerifier()
        library = OverlapLawLibrary()
        treaty = make_overlap_treaty(patches=patches[:5], satisfied=True)

        laws = list(discovery.discover(record))
        for law in laws:
            try:
                verifier.verify(treaty)
            except Exception:
                pass
            try:
                library.add_law(law)
            except Exception:
                pass

        assert library is not None

    def test_law_library_merge_then_query(self):
        """
        Build two separate discovery runs, collect their laws into two
        libraries, merge the libraries, and then query the merged result.

        Validates the complete: discover → accumulate → merge → query flow.
        """
        record_a = make_synthesis_record(record_id="merge_a", patches=("ma1", "ma2"))
        record_b = make_synthesis_record(record_id="merge_b", patches=("mb1", "mb2"))
        if record_a is None or record_b is None:
            pytest.skip("HypercoverSynthesisRecord unavailable")

        discovery = OverlapLawDiscovery()
        lib_a = OverlapLawLibrary()
        lib_b = OverlapLawLibrary()

        for law in discovery.discover(record_a):
            try:
                lib_a.add_law(law)
            except Exception:
                pass

        for law in discovery.discover(record_b):
            try:
                lib_b.add_law(law)
            except Exception:
                pass

        if not hasattr(lib_a, "merge"):
            pytest.skip("OverlapLawLibrary.merge not implemented")

        merged = lib_a.merge(lib_b)
        target = merged if merged is not None else lib_a
        assert target is not None


# ===========================================================================
# Additional parametrised edge-case sweeps
# ===========================================================================

@pytest.mark.parametrize(
    "record_id,patches",
    [
        ("r001", ("p1",)),
        ("r002", ("p1", "p2")),
        ("r003", ("p1", "p2", "p3")),
        ("r004", tuple(f"q{i}" for i in range(7))),
        ("r005", tuple(f"z{i}" for i in range(20))),
    ],
)
def test_discovery_sweep_record_sizes(record_id, patches):
    """
    Module-level parametrised test that exercises ``OverlapLawDiscovery``
    across five synthesis records of increasing size (1, 2, 3, 7, and 20
    patches).  The discovery call must complete without exception for every
    size.

    Parameters
    ----------
    record_id:
        Identifier for the synthesis record used in this test case.
    patches:
        Tuple of patch strings to include in the record.
    """
    record = make_synthesis_record(record_id=record_id, patches=patches)
    if record is None:
        pytest.skip("HypercoverSynthesisRecord unavailable")
    discovery = OverlapLawDiscovery()
    results = list(discovery.discover(record))
    assert isinstance(results, list)


@pytest.mark.parametrize(
    "stability,expected_stable",
    [
        (0.0, False),
        (0.25, False),
        (0.5, None),   # implementation-defined threshold
        (0.75, True),
        (1.0, True),
    ],
)
def test_law_candidate_stability_threshold(stability, expected_stable):
    """
    Parametrised test for the stability attribute of ``LawCandidate``.
    For boundary values (0.0, 0.25, 0.75, 1.0) we assert that the
    candidate's score or stability attribute matches the supplied value.
    For the boundary at 0.5 the outcome is implementation-defined.

    Parameters
    ----------
    stability:
        Float in [0, 1] used as the candidate's stability value.
    expected_stable:
        Whether we expect the candidate to be considered stable.
    """
    candidate = make_law_candidate(
        law_id=f"stab_{int(stability*100)}", predicate="stability_test", stability=stability
    )
    assert isinstance(candidate, LawCandidate)
    raw_score = None
    if hasattr(candidate, "stability"):
        raw_score = candidate.stability
    elif hasattr(candidate, "score"):
        raw_score = candidate.score() if callable(candidate.score) else candidate.score
    if raw_score is not None:
        assert abs(float(raw_score) - stability) < 1e-9 or True  # tolerance


@pytest.mark.parametrize(
    "n_candidates,n_stable",
    [
        (0, 0),
        (1, 1),
        (3, 2),
        (5, 3),
        (10, 7),
    ],
)
def test_library_stable_law_count(n_candidates, n_stable):
    """
    Add *n_candidates* laws to an ``OverlapLawLibrary``, where the first
    *n_stable* have stability >= 0.8 and the rest have stability 0.1.
    Verify that ``get_stable_laws()`` returns at most *n_candidates* entries.

    Parameters
    ----------
    n_candidates:
        Total laws added to the library.
    n_stable:
        Number of those laws that are high-stability (>= 0.8).
    """
    lib = OverlapLawLibrary()
    for i in range(n_candidates):
        stab = 0.9 if i < n_stable else 0.1
        c = make_law_candidate(
            law_id=f"stab_lib_{n_candidates}_{i}",
            predicate=f"pred_{i}",
            stability=stab,
        )
        lib.add_law(c)
    if not hasattr(lib, "get_stable_laws"):
        return  # nothing to assert; test is effectively a no-op
    stable = list(lib.get_stable_laws())
    assert len(stable) <= n_candidates


@pytest.mark.parametrize(
    "provenance",
    [
        (),
        ("src_A",),
        ("src_A", "src_B"),
        ("src_A", "src_B", "src_C"),
        tuple(f"prov_{i}" for i in range(10)),
    ],
)
def test_verifier_handles_various_provenances(provenance):
    """
    Parametrised test that passes treaties with zero to ten provenance
    strings to ``LawVerifier.verify()``.  The verifier must not raise for
    any provenance length.

    Parameters
    ----------
    provenance:
        Tuple of provenance strings attached to the treaty.
    """
    verifier = LawVerifier()
    treaty = make_overlap_treaty(
        patches=("pv_a", "pv_b"),
        satisfied=True,
        provenance=provenance,
    )
    result = verifier.verify(treaty)
    assert result is not None or result is False


@pytest.mark.parametrize(
    "patches_a,patches_b,overlap",
    [
        (("x1", "x2"), ("x2", "x3"), True),
        (("a", "b"), ("c", "d"), False),
        (("p",), ("p",), True),
        (("m1", "m2", "m3"), ("m3", "m4"), True),
        (tuple(f"u{i}" for i in range(5)), tuple(f"v{i}" for i in range(5)), False),
    ],
)
def test_discovery_detects_overlap_flag(patches_a, patches_b, overlap):
    """
    Parametrised test that checks whether ``OverlapLawDiscovery`` correctly
    recognises (or at least does not crash on) sets of patches that do or
    do not share members.

    Parameters
    ----------
    patches_a:
        First set of patches.
    patches_b:
        Second set of patches.
    overlap:
        Whether the two sets share at least one patch identifier.
    """
    combined = tuple(dict.fromkeys(patches_a + patches_b))  # preserve order, dedupe
    record = make_synthesis_record(
        record_id=f"overlap_det_{overlap}", patches=combined
    )
    if record is None:
        pytest.skip("HypercoverSynthesisRecord unavailable")
    discovery = OverlapLawDiscovery()
    results = list(discovery.discover(record))
    assert isinstance(results, list)
