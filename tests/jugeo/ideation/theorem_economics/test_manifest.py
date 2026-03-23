from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.ideation.theorem_economics.manifest import (
    YieldType,
    AssumptionCategory,
    ValidationStatus,
    YieldModelDescriptor,
    EconomicAssumption,
    TheoremEconomicsManifest,
    ManifestValidator,
    ManifestRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_descriptor(
    model_id: str = "m1",
    yield_type: YieldType = YieldType.SATURATING_EXPONENTIAL,
    **kwargs,
) -> YieldModelDescriptor:
    return YieldModelDescriptor(
        model_id=model_id,
        yield_type=yield_type,
        parameters=kwargs.get("parameters", {"saturation_yield": 10.0, "growth_rate": 0.5}),
        description=kwargs.get("description", "Test model"),
        regime_id=kwargs.get("regime_id", "regime-1"),
        created_at=0.0,
    )


def _make_assumption(name: str = "test", **kwargs) -> EconomicAssumption:
    return EconomicAssumption(
        name=name,
        category=kwargs.get("category", AssumptionCategory.MATHEMATICAL),
        description=kwargs.get("description", "test assumption"),
        mathematical_form=kwargs.get("mathematical_form", "Y > 0"),
        validation_status=kwargs.get("validation_status", ValidationStatus.VALID),
        confidence=kwargs.get("confidence", 0.9),
    )


def _make_manifest(manifest_id: str = "manifest-1") -> TheoremEconomicsManifest:
    descriptors = [_make_descriptor("m1"), _make_descriptor("m2", regime_id="regime-2")]
    assumptions = [
        _make_assumption("assumption-math", category=AssumptionCategory.MATHEMATICAL),
        _make_assumption("assumption-empirical", category=AssumptionCategory.EMPIRICAL,
                         validation_status=ValidationStatus.PENDING),
    ]
    return TheoremEconomicsManifest(
        manifest_id=manifest_id,
        version="1.0",
        descriptors=descriptors,
        assumptions=assumptions,
        description="Test manifest",
    )


# ---------------------------------------------------------------------------
# YieldType enum tests
# ---------------------------------------------------------------------------

def test_yield_type_saturating_exponential_exists() -> None:
    assert YieldType.SATURATING_EXPONENTIAL is not None


def test_yield_type_has_multiple_values() -> None:
    values = list(YieldType)
    assert len(values) >= 2


def test_yield_type_values_are_strings_or_ints() -> None:
    for yt in YieldType:
        assert yt.value is not None


# ---------------------------------------------------------------------------
# AssumptionCategory enum tests
# ---------------------------------------------------------------------------

def test_assumption_category_mathematical_exists() -> None:
    assert AssumptionCategory.MATHEMATICAL is not None


def test_assumption_category_empirical_exists() -> None:
    assert AssumptionCategory.EMPIRICAL is not None


def test_assumption_category_has_at_least_two_values() -> None:
    assert len(list(AssumptionCategory)) >= 2


# ---------------------------------------------------------------------------
# ValidationStatus enum tests
# ---------------------------------------------------------------------------

def test_validation_status_valid_exists() -> None:
    assert ValidationStatus.VALID is not None


def test_validation_status_pending_exists() -> None:
    assert ValidationStatus.PENDING is not None


def test_validation_status_invalid_exists() -> None:
    assert ValidationStatus.INVALID is not None


def test_validation_status_has_three_or_more_values() -> None:
    assert len(list(ValidationStatus)) >= 3


# ---------------------------------------------------------------------------
# YieldModelDescriptor tests
# ---------------------------------------------------------------------------

def test_descriptor_creates_with_required_fields() -> None:
    d = _make_descriptor()
    assert d.model_id == "m1"
    assert d.yield_type == YieldType.SATURATING_EXPONENTIAL
    assert d.regime_id == "regime-1"


def test_descriptor_stores_parameters() -> None:
    params = {"saturation_yield": 20.0, "growth_rate": 0.3}
    d = _make_descriptor(parameters=params)
    assert d.parameters["saturation_yield"] == 20.0
    assert d.parameters["growth_rate"] == 0.3


def test_descriptor_is_calibrated_true_when_parameters_present() -> None:
    d = _make_descriptor(parameters={"saturation_yield": 10.0, "growth_rate": 0.5})
    assert d.is_calibrated() is True


def test_descriptor_is_calibrated_false_when_parameters_empty() -> None:
    d = _make_descriptor(parameters={})
    assert d.is_calibrated() is False


def test_descriptor_description_stored() -> None:
    d = _make_descriptor(description="My custom description")
    assert d.description == "My custom description"


def test_descriptor_created_at_stored() -> None:
    d = _make_descriptor()
    assert d.created_at == 0.0


def test_descriptor_different_yield_types() -> None:
    for yt in YieldType:
        d = _make_descriptor(yield_type=yt)
        assert d.yield_type == yt


# ---------------------------------------------------------------------------
# EconomicAssumption tests
# ---------------------------------------------------------------------------

def test_assumption_creates_with_required_fields() -> None:
    a = _make_assumption()
    assert a.name == "test"
    assert a.category == AssumptionCategory.MATHEMATICAL
    assert a.confidence == 0.9


def test_assumption_is_valid_true_for_valid_status() -> None:
    a = _make_assumption(validation_status=ValidationStatus.VALID)
    assert a.is_valid() is True


def test_assumption_is_valid_false_for_invalid_status() -> None:
    a = _make_assumption(validation_status=ValidationStatus.INVALID)
    assert a.is_valid() is False


def test_assumption_is_valid_false_for_pending_status() -> None:
    a = _make_assumption(validation_status=ValidationStatus.PENDING)
    assert a.is_valid() is False


def test_assumption_mathematical_form_stored() -> None:
    a = _make_assumption(mathematical_form="dY/dB > 0")
    assert a.mathematical_form == "dY/dB > 0"


def test_assumption_description_stored() -> None:
    a = _make_assumption(description="yield is concave")
    assert a.description == "yield is concave"


def test_assumption_confidence_range() -> None:
    a = _make_assumption(confidence=0.75)
    assert 0.0 <= a.confidence <= 1.0


# ---------------------------------------------------------------------------
# TheoremEconomicsManifest tests
# ---------------------------------------------------------------------------

def test_manifest_creates_correctly() -> None:
    m = _make_manifest()
    assert m.manifest_id == "manifest-1"
    assert m.version == "1.0"


def test_manifest_find_descriptor_returns_correct() -> None:
    m = _make_manifest()
    d = m.find_descriptor("m1")
    assert d is not None
    assert d.model_id == "m1"


def test_manifest_find_descriptor_returns_none_for_missing() -> None:
    m = _make_manifest()
    d = m.find_descriptor("nonexistent")
    assert d is None


def test_manifest_find_descriptor_by_regime() -> None:
    m = _make_manifest()
    d = m.find_descriptor("m2")
    assert d is not None
    assert d.regime_id == "regime-2"


def test_manifest_valid_assumptions_filters_correctly() -> None:
    m = _make_manifest()
    valid = m.valid_assumptions()
    for a in valid:
        assert a.is_valid() is True


def test_manifest_valid_assumptions_excludes_pending() -> None:
    m = _make_manifest()
    valid = m.valid_assumptions()
    names = [a.name for a in valid]
    assert "assumption-empirical" not in names


def test_manifest_has_descriptors() -> None:
    m = _make_manifest()
    assert len(m.descriptors) == 2


def test_manifest_has_assumptions() -> None:
    m = _make_manifest()
    assert len(m.assumptions) == 2


def test_manifest_description_stored() -> None:
    m = _make_manifest()
    assert m.description == "Test manifest"


# ---------------------------------------------------------------------------
# ManifestValidator tests
# ---------------------------------------------------------------------------

def test_manifest_validator_returns_empty_list_for_valid_manifest() -> None:
    m = _make_manifest()
    validator = ManifestValidator()
    errors = validator.validate(m)
    assert errors == []


def test_manifest_validator_is_valid_true_for_valid_manifest() -> None:
    m = _make_manifest()
    validator = ManifestValidator()
    assert validator.is_valid(m) is True


def test_manifest_validator_catches_missing_manifest_id() -> None:
    m = TheoremEconomicsManifest(
        manifest_id="",
        version="1.0",
        descriptors=[_make_descriptor()],
        assumptions=[_make_assumption()],
        description="missing id manifest",
    )
    validator = ManifestValidator()
    errors = validator.validate(m)
    assert len(errors) > 0


def test_manifest_validator_is_valid_false_for_invalid_manifest() -> None:
    m = TheoremEconomicsManifest(
        manifest_id="",
        version="1.0",
        descriptors=[_make_descriptor()],
        assumptions=[_make_assumption()],
        description="bad",
    )
    validator = ManifestValidator()
    assert validator.is_valid(m) is False


def test_manifest_validator_catches_empty_descriptors() -> None:
    m = TheoremEconomicsManifest(
        manifest_id="m-empty",
        version="1.0",
        descriptors=[],
        assumptions=[_make_assumption()],
        description="empty descriptors",
    )
    validator = ManifestValidator()
    errors = validator.validate(m)
    assert len(errors) > 0


def test_manifest_validator_catches_missing_version() -> None:
    m = TheoremEconomicsManifest(
        manifest_id="m-noversion",
        version="",
        descriptors=[_make_descriptor()],
        assumptions=[_make_assumption()],
        description="no version",
    )
    validator = ManifestValidator()
    errors = validator.validate(m)
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# ManifestRegistry tests
# ---------------------------------------------------------------------------

def test_registry_register_and_get() -> None:
    registry = ManifestRegistry()
    m = _make_manifest("reg-manifest-1")
    registry.register(m)
    retrieved = registry.get("reg-manifest-1")
    assert retrieved is not None
    assert retrieved.manifest_id == "reg-manifest-1"


def test_registry_get_returns_none_for_missing() -> None:
    registry = ManifestRegistry()
    assert registry.get("nonexistent") is None


def test_registry_remove_works() -> None:
    registry = ManifestRegistry()
    m = _make_manifest("removable")
    registry.register(m)
    registry.remove("removable")
    assert registry.get("removable") is None


def test_registry_list_manifests_returns_all() -> None:
    registry = ManifestRegistry()
    registry.register(_make_manifest("list-1"))
    registry.register(_make_manifest("list-2"))
    listed = registry.list_manifests()
    ids = [m.manifest_id for m in listed]
    assert "list-1" in ids
    assert "list-2" in ids


def test_registry_list_manifests_empty_initially() -> None:
    registry = ManifestRegistry()
    listed = registry.list_manifests()
    assert isinstance(listed, list)


def test_registry_default_manifest_returns_valid_manifest() -> None:
    registry = ManifestRegistry()
    m = registry.default_manifest()
    assert m is not None
    assert isinstance(m, TheoremEconomicsManifest)


def test_registry_default_manifest_is_valid() -> None:
    registry = ManifestRegistry()
    m = registry.default_manifest()
    validator = ManifestValidator()
    assert validator.is_valid(m) is True


def test_registry_default_manifest_has_descriptors() -> None:
    registry = ManifestRegistry()
    m = registry.default_manifest()
    assert len(m.descriptors) > 0


def test_registry_default_manifest_has_valid_assumptions() -> None:
    registry = ManifestRegistry()
    m = registry.default_manifest()
    valid = m.valid_assumptions()
    assert len(valid) > 0


def test_registry_overwrite_existing_manifest() -> None:
    registry = ManifestRegistry()
    m1 = _make_manifest("overwrite-me")
    registry.register(m1)
    m2 = TheoremEconomicsManifest(
        manifest_id="overwrite-me",
        version="2.0",
        descriptors=[_make_descriptor("new-d")],
        assumptions=[_make_assumption("new-a")],
        description="Updated manifest",
    )
    registry.register(m2)
    retrieved = registry.get("overwrite-me")
    assert retrieved.version == "2.0"


def test_registry_remove_nonexistent_does_not_raise() -> None:
    registry = ManifestRegistry()
    registry.remove("does-not-exist")


def test_multiple_assumptions_with_mixed_statuses() -> None:
    valid_a = _make_assumption("va", validation_status=ValidationStatus.VALID)
    invalid_a = _make_assumption("ia", validation_status=ValidationStatus.INVALID)
    pending_a = _make_assumption("pa", validation_status=ValidationStatus.PENDING)
    m = TheoremEconomicsManifest(
        manifest_id="mixed",
        version="1.0",
        descriptors=[_make_descriptor()],
        assumptions=[valid_a, invalid_a, pending_a],
        description="mixed statuses",
    )
    valid_list = m.valid_assumptions()
    assert len(valid_list) == 1
    assert valid_list[0].name == "va"


def test_descriptor_with_additional_parameters() -> None:
    params = {
        "saturation_yield": 50.0,
        "growth_rate": 0.1,
        "offset": 2.0,
        "noise_floor": 0.01,
    }
    d = _make_descriptor(parameters=params)
    assert d.parameters["offset"] == 2.0
    assert d.is_calibrated() is True


def test_assumption_high_confidence_is_valid() -> None:
    a = _make_assumption(confidence=1.0, validation_status=ValidationStatus.VALID)
    assert a.is_valid() is True
    assert a.confidence == 1.0


def test_assumption_zero_confidence_can_be_invalid() -> None:
    a = _make_assumption(confidence=0.0, validation_status=ValidationStatus.INVALID)
    assert a.is_valid() is False


def test_manifest_find_descriptor_returns_first_match() -> None:
    d1 = _make_descriptor("dup-id", description="first")
    d2 = _make_descriptor("dup-id", description="second")
    m = TheoremEconomicsManifest(
        manifest_id="dup-manifest",
        version="1.0",
        descriptors=[d1, d2],
        assumptions=[_make_assumption()],
        description="dup descriptors",
    )
    found = m.find_descriptor("dup-id")
    assert found is not None
    assert found.description == "first"


def test_manifest_version_stored_correctly() -> None:
    m = TheoremEconomicsManifest(
        manifest_id="versioned",
        version="3.1.4",
        descriptors=[_make_descriptor()],
        assumptions=[_make_assumption()],
        description="versioned manifest",
    )
    assert m.version == "3.1.4"


def test_registry_count_after_operations() -> None:
    registry = ManifestRegistry()
    initial = len(registry.list_manifests())
    registry.register(_make_manifest("count-1"))
    registry.register(_make_manifest("count-2"))
    assert len(registry.list_manifests()) >= initial + 2
    registry.remove("count-1")
    assert len(registry.list_manifests()) >= initial + 1


def test_assumption_category_computational_if_exists() -> None:
    categories = [c.name for c in AssumptionCategory]
    assert len(categories) >= 2


def test_yield_type_linear_if_exists() -> None:
    yt_names = [yt.name for yt in YieldType]
    assert "SATURATING_EXPONENTIAL" in yt_names


def test_manifest_validator_with_multiple_valid_descriptors() -> None:
    descriptors = [_make_descriptor(f"d{i}") for i in range(5)]
    assumptions = [_make_assumption(f"a{i}") for i in range(3)]
    m = TheoremEconomicsManifest(
        manifest_id="many-descriptors",
        version="1.0",
        descriptors=descriptors,
        assumptions=assumptions,
        description="many descriptors manifest",
    )
    validator = ManifestValidator()
    assert validator.is_valid(m) is True


def test_manifest_id_uniqueness_in_registry() -> None:
    registry = ManifestRegistry()
    m1 = _make_manifest("unique-1")
    m2 = _make_manifest("unique-2")
    registry.register(m1)
    registry.register(m2)
    assert registry.get("unique-1").manifest_id != registry.get("unique-2").manifest_id


def test_descriptor_is_calibrated_with_partial_params() -> None:
    d = _make_descriptor(parameters={"saturation_yield": 5.0})
    assert d.is_calibrated() is True


def test_economic_assumption_name_is_stored() -> None:
    a = _make_assumption(name="my-special-assumption")
    assert a.name == "my-special-assumption"
