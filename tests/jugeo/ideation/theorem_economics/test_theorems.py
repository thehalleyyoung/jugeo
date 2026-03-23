from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.ideation.theorem_economics.theorems import (
    TheoremStatus,
    ProofMethod,
    EconomicTheorem,
    TheoremCatalog,
    TheoremVerifier,
    default_catalog,
    T52_1,
    T52_2,
    T52_3,
    T52_4,
    T52_5,
    T52_6,
    T52_7,
    T52_8,
    T52_9,
    T52_10,
    T52_11,
    T52_12,
    T52_13,
    T52_14,
    T52_15,
)
from jugeo.ideation.theorem_economics.models import (
    TheoremYieldModel,
    CompoundingEffect,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_yield_model(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.4,
) -> TheoremYieldModel:
    return TheoremYieldModel(
        model_id=f"model-{regime_id}",
        regime_id=regime_id,
        saturation_yield=saturation,
        growth_rate=rate,
        current_budget=0.0,
        empirical_data=[],
    )


def _make_theorem(
    theorem_id: str = "T-test",
    name: str = "Test Theorem",
    status: TheoremStatus = TheoremStatus.PROVED,
    method: ProofMethod = ProofMethod.ANALYTICAL,
) -> EconomicTheorem:
    return EconomicTheorem(
        theorem_id=theorem_id,
        name=name,
        statement="The yield function Y(B) is concave.",
        proof_sketch="By taking the second derivative of Y(B) = S(1-exp(-rB)), "
                     "we get Y''(B) = -Sr^2 exp(-rB) < 0.",
        status=status,
        proof_method=method,
        dependencies=[],
    )


def _make_compounding_effect(
    base_yield: float = 5.0,
    derived_theorems: int = 3,
    chain_depth: int = 2,
) -> CompoundingEffect:
    return CompoundingEffect(
        base_theorem_id="t0",
        base_yield=base_yield,
        derived_theorems=derived_theorems,
        chain_depth=chain_depth,
        compounding_factor=1.2,
    )


# ---------------------------------------------------------------------------
# TheoremStatus enum tests
# ---------------------------------------------------------------------------

def test_theorem_status_proved_exists() -> None:
    assert TheoremStatus.PROVED is not None


def test_theorem_status_conjectured_exists() -> None:
    assert TheoremStatus.CONJECTURED is not None


def test_theorem_status_has_at_least_two_values() -> None:
    assert len(list(TheoremStatus)) >= 2


def test_theorem_status_values_are_defined() -> None:
    for s in TheoremStatus:
        assert s.value is not None


# ---------------------------------------------------------------------------
# ProofMethod enum tests
# ---------------------------------------------------------------------------

def test_proof_method_analytical_exists() -> None:
    assert ProofMethod.ANALYTICAL is not None


def test_proof_method_has_at_least_two_values() -> None:
    assert len(list(ProofMethod)) >= 2


def test_proof_method_values_are_strings_or_ints() -> None:
    for m in ProofMethod:
        assert m.value is not None


# ---------------------------------------------------------------------------
# EconomicTheorem tests
# ---------------------------------------------------------------------------

def test_economic_theorem_is_proved_true_for_proved() -> None:
    t = _make_theorem(status=TheoremStatus.PROVED)
    assert t.is_proved() is True


def test_economic_theorem_is_proved_false_for_conjectured() -> None:
    t = _make_theorem(status=TheoremStatus.CONJECTURED)
    assert t.is_proved() is False


def test_economic_theorem_summary_contains_theorem_id() -> None:
    t = _make_theorem(theorem_id="T52-1")
    summary = t.summary()
    assert "T52-1" in summary


def test_economic_theorem_summary_contains_name() -> None:
    t = _make_theorem(name="Concavity Theorem")
    summary = t.summary()
    assert "Concavity Theorem" in summary


def test_economic_theorem_summary_is_non_empty_string() -> None:
    t = _make_theorem()
    summary = t.summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_economic_theorem_statement_stored() -> None:
    t = _make_theorem()
    assert "concave" in t.statement.lower() or len(t.statement) > 0


def test_economic_theorem_proof_sketch_stored() -> None:
    t = _make_theorem()
    assert len(t.proof_sketch) > 0


def test_economic_theorem_proof_method_stored() -> None:
    t = _make_theorem(method=ProofMethod.ANALYTICAL)
    assert t.proof_method == ProofMethod.ANALYTICAL


def test_economic_theorem_dependencies_stored() -> None:
    t = EconomicTheorem(
        theorem_id="T-dep",
        name="Dep theorem",
        statement="statement",
        proof_sketch="sketch",
        status=TheoremStatus.PROVED,
        proof_method=ProofMethod.ANALYTICAL,
        dependencies=["T52-1", "T52-2"],
    )
    assert "T52-1" in t.dependencies


# ---------------------------------------------------------------------------
# TheoremCatalog tests
# ---------------------------------------------------------------------------

def test_theorem_catalog_add_and_get() -> None:
    catalog = TheoremCatalog()
    t = _make_theorem("cat-test-1")
    catalog.add(t)
    retrieved = catalog.get("cat-test-1")
    assert retrieved is not None
    assert retrieved.theorem_id == "cat-test-1"


def test_theorem_catalog_get_returns_none_for_missing() -> None:
    catalog = TheoremCatalog()
    assert catalog.get("nonexistent") is None


def test_theorem_catalog_list_proved_returns_only_proved() -> None:
    catalog = TheoremCatalog()
    proved = _make_theorem("pv-1", status=TheoremStatus.PROVED)
    conjectured = _make_theorem("cj-1", status=TheoremStatus.CONJECTURED)
    catalog.add(proved)
    catalog.add(conjectured)
    proved_list = catalog.list_proved()
    ids = [t.theorem_id for t in proved_list]
    assert "pv-1" in ids
    assert "cj-1" not in ids


def test_theorem_catalog_list_by_method_filters_correctly() -> None:
    catalog = TheoremCatalog()
    analytical = _make_theorem("a-1", method=ProofMethod.ANALYTICAL)
    for pm in ProofMethod:
        if pm != ProofMethod.ANALYTICAL:
            other_method = pm
            break
    else:
        other_method = ProofMethod.ANALYTICAL

    other = _make_theorem("other-1", method=other_method)
    catalog.add(analytical)
    catalog.add(other)
    by_method = catalog.list_by_method(ProofMethod.ANALYTICAL)
    ids = [t.theorem_id for t in by_method]
    assert "a-1" in ids


def test_theorem_catalog_all_returns_all_added() -> None:
    catalog = TheoremCatalog()
    for i in range(5):
        catalog.add(_make_theorem(f"bulk-{i}"))
    all_theorems = catalog.all()
    assert len(all_theorems) >= 5


def test_theorem_catalog_does_not_duplicate() -> None:
    catalog = TheoremCatalog()
    t = _make_theorem("dup-test")
    catalog.add(t)
    catalog.add(t)
    all_t = catalog.all()
    ids = [th.theorem_id for th in all_t]
    assert ids.count("dup-test") == 1


# ---------------------------------------------------------------------------
# TheoremVerifier tests
# ---------------------------------------------------------------------------

def test_theorem_verifier_verify_concavity_true_for_saturating_model() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.5)
    verifier = TheoremVerifier()
    assert verifier.verify_concavity(model) is True


def test_theorem_verifier_verify_concavity_false_for_linear_model() -> None:
    from jugeo.ideation.theorem_economics.models import LinearYieldModel
    linear_model = LinearYieldModel(
        model_id="linear-1",
        regime_id="r-linear",
        slope=1.0,
        current_budget=0.0,
    )
    verifier = TheoremVerifier()
    assert verifier.verify_concavity(linear_model) is False


def test_theorem_verifier_verify_optimal_allocation_true_for_equimarginal() -> None:
    m1 = _make_yield_model("e1", saturation=10.0, rate=0.5)
    m2 = _make_yield_model("e2", saturation=10.0, rate=0.5)
    allocs = {"e1": 5.0, "e2": 5.0}
    verifier = TheoremVerifier()
    assert verifier.verify_optimal_allocation([m1, m2], allocs, total_budget=10.0) is True


def test_theorem_verifier_verify_optimal_allocation_false_for_skewed() -> None:
    m1 = _make_yield_model("s1", saturation=10.0, rate=0.5)
    m2 = _make_yield_model("s2", saturation=10.0, rate=0.5)
    skewed_allocs = {"s1": 9.5, "s2": 0.5}
    verifier = TheoremVerifier()
    assert verifier.verify_optimal_allocation(
        [m1, m2], skewed_allocs, total_budget=10.0, tolerance=0.1
    ) is False


def test_theorem_verifier_verify_compounding_true_for_valid_effect() -> None:
    ce = _make_compounding_effect(base_yield=5.0, derived_theorems=3, chain_depth=2)
    verifier = TheoremVerifier()
    assert verifier.verify_compounding(ce) is True


def test_theorem_verifier_verify_compounding_false_for_no_derived() -> None:
    ce = CompoundingEffect(
        base_theorem_id="lone",
        base_yield=5.0,
        derived_theorems=0,
        chain_depth=0,
        compounding_factor=1.0,
    )
    verifier = TheoremVerifier()
    assert verifier.verify_compounding(ce) is False


def test_theorem_verifier_verification_report_non_empty() -> None:
    model = _make_yield_model()
    verifier = TheoremVerifier()
    report = verifier.verification_report(model)
    assert isinstance(report, str)
    assert len(report) > 0


# ---------------------------------------------------------------------------
# default_catalog tests
# ---------------------------------------------------------------------------

def test_default_catalog_returns_theorem_catalog() -> None:
    catalog = default_catalog()
    assert isinstance(catalog, TheoremCatalog)


def test_default_catalog_has_15_or_more_theorems() -> None:
    catalog = default_catalog()
    assert len(catalog.all()) >= 15


def test_default_catalog_all_theorems_have_nonempty_statement() -> None:
    catalog = default_catalog()
    for t in catalog.all():
        assert len(t.statement) > 0


def test_default_catalog_all_theorems_have_nonempty_proof_sketch() -> None:
    catalog = default_catalog()
    for t in catalog.all():
        assert len(t.proof_sketch) > 0


def test_default_catalog_all_theorems_proved() -> None:
    catalog = default_catalog()
    for t in catalog.all():
        assert t.is_proved() is True


# ---------------------------------------------------------------------------
# T52_1 through T52_15 constant tests
# ---------------------------------------------------------------------------

def test_T52_1_is_economic_theorem() -> None:
    assert isinstance(T52_1, EconomicTheorem)


def test_T52_1_is_proved() -> None:
    assert T52_1.is_proved() is True


def test_T52_2_is_proved() -> None:
    assert T52_2.is_proved() is True


def test_T52_3_is_proved() -> None:
    assert T52_3.is_proved() is True


def test_T52_4_is_proved() -> None:
    assert T52_4.is_proved() is True


def test_T52_5_is_proved() -> None:
    assert T52_5.is_proved() is True


def test_T52_6_is_proved() -> None:
    assert T52_6.is_proved() is True


def test_T52_7_is_proved() -> None:
    assert T52_7.is_proved() is True


def test_T52_8_is_proved() -> None:
    assert T52_8.is_proved() is True


def test_T52_9_is_proved() -> None:
    assert T52_9.is_proved() is True


def test_T52_10_is_proved() -> None:
    assert T52_10.is_proved() is True


def test_T52_11_is_proved() -> None:
    assert T52_11.is_proved() is True


def test_T52_12_is_proved() -> None:
    assert T52_12.is_proved() is True


def test_T52_13_is_proved() -> None:
    assert T52_13.is_proved() is True


def test_T52_14_is_proved() -> None:
    assert T52_14.is_proved() is True


def test_T52_15_is_proved() -> None:
    assert T52_15.is_proved() is True


def test_all_T52_have_unique_ids() -> None:
    theorems = [T52_1, T52_2, T52_3, T52_4, T52_5,
                T52_6, T52_7, T52_8, T52_9, T52_10,
                T52_11, T52_12, T52_13, T52_14, T52_15]
    ids = [t.theorem_id for t in theorems]
    assert len(ids) == len(set(ids))


def test_all_T52_have_non_empty_names() -> None:
    theorems = [T52_1, T52_2, T52_3, T52_4, T52_5,
                T52_6, T52_7, T52_8, T52_9, T52_10,
                T52_11, T52_12, T52_13, T52_14, T52_15]
    for t in theorems:
        assert len(t.name) > 0


def test_default_catalog_all_returns_15_theorems() -> None:
    catalog = default_catalog()
    assert len(catalog.all()) == 15


def test_theorem_catalog_list_proved_from_default_catalog() -> None:
    catalog = default_catalog()
    proved = catalog.list_proved()
    assert len(proved) == 15
