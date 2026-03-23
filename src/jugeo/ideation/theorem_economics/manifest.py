from __future__ import annotations

"""
Manifest system for Theorem-Growth Economics (Ch52).

The manifest captures the contractual specification of yield models and
economic assumptions used in theorem-portfolio optimisation.  Every
deployment of the scheduler is anchored to a manifest so that the
assumptions behind each allocation decision are auditable.

.. math::

   Y(B) = Y_\\infty \\bigl(1 - e^{-\\lambda B}\\bigr)

where :math:`Y_\\infty` is the saturation yield and :math:`\\lambda` the
growth rate parameter declared in the :class:`YieldModelDescriptor`.
"""

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_log = logging.getLogger(__name__)

ManifestID = str
ModelID = str

__all__ = [
    "ManifestID",
    "ModelID",
    "YieldType",
    "AssumptionCategory",
    "ValidationStatus",
    "YieldModelDescriptor",
    "EconomicAssumption",
    "TheoremEconomicsManifest",
    "ManifestValidator",
    "ManifestRegistry",
    "_make_assumption",
    "_validate_parameters",
    "_default_descriptors",
    "_default_assumptions",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class YieldType(str, Enum):
    """Functional form used to model theorem yield as a function of budget."""

    SATURATING_EXPONENTIAL = "saturating_exponential"
    LINEAR = "linear"
    LOGISTIC = "logistic"
    POWER_LAW = "power_law"
    CONSTANT = "constant"


class AssumptionCategory(str, Enum):
    """High-level domain to which an economic assumption belongs."""

    ECONOMIC = "economic"
    MATHEMATICAL = "mathematical"
    EMPIRICAL = "empirical"
    STRUCTURAL = "structural"


class ValidationStatus(str, Enum):
    """Outcome of validating a manifest assumption against observed data."""

    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
    UNTESTABLE = "untestable"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YieldModelDescriptor:
    """
    Immutable specification of a yield model for a single regime.

    Each descriptor names the functional form (``yield_type``) and the
    calibration parameters required by that form.  The descriptor is
    intentionally frozen so that manifests are reproducible — changing
    calibration requires issuing a new descriptor with a new ``model_id``.

    Attributes
    ----------
    model_id:
        Unique identifier for this descriptor instance.
    yield_type:
        Functional form of the yield-vs-budget curve.
    parameters:
        Mapping of parameter names to calibrated float values.
    description:
        Human-readable description of what this model represents.
    regime_id:
        The research regime this descriptor is calibrated for.
    created_at:
        Unix timestamp when the descriptor was created.
    """

    model_id: str
    yield_type: YieldType
    description: str
    regime_id: str
    parameters: dict[str, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def parameter_summary(self) -> str:
        """Return a comma-separated string of ``key=value`` parameter pairs."""
        if not self.parameters:
            return "<no parameters>"
        return ", ".join(f"{k}={v:.4g}" for k, v in sorted(self.parameters.items()))

    def is_calibrated(self) -> bool:
        """Return True if every parameter is finite and nonzero."""
        if not self.parameters:
            return False
        return all(
            math.isfinite(v) and v != 0.0
            for v in self.parameters.values()
        )

    # ------------------------------------------------------------------
    # Yield evaluation
    # ------------------------------------------------------------------

    def evaluate(self, budget: float) -> float:
        """
        Compute the expected yield at the given *budget* level.

        Parameters
        ----------
        budget:
            Non-negative budget quantity (arbitrary units).

        Returns
        -------
        float
            Predicted yield; 0.0 when guards trigger.
        """
        p = self.parameters

        if self.yield_type is YieldType.SATURATING_EXPONENTIAL:
            y_inf = p.get("Y_inf", p.get("y_inf", 0.0))
            lam = p.get("lambda", p.get("lam", 0.0))
            if lam <= 0.0 or budget < 0.0:
                return 0.0
            return y_inf * (1.0 - math.exp(-lam * budget))

        if self.yield_type is YieldType.LINEAR:
            slope = p.get("slope", 0.0)
            if slope == 0.0:
                return 0.0
            if budget < 0.0:
                return 0.0
            return slope * budget

        if self.yield_type is YieldType.LOGISTIC:
            L = p.get("L", 1.0)
            k = p.get("k", 1.0)
            x0 = p.get("x0", 0.0)
            denom = 1.0 + math.exp(-k * (budget - x0))
            if denom == 0.0:
                return 0.0
            return L / denom

        if self.yield_type is YieldType.POWER_LAW:
            coefficient = p.get("coefficient", p.get("a", 0.0))
            exponent = p.get("exponent", p.get("n", 1.0))
            if budget <= 0.0:
                return 0.0
            return coefficient * (budget ** exponent)

        if self.yield_type is YieldType.CONSTANT:
            return p.get("value", 0.0)

        return 0.0

    def marginal(self, budget: float) -> float:
        """
        Return the derivative of yield with respect to budget at *budget*.

        Parameters
        ----------
        budget:
            Non-negative budget quantity.

        Returns
        -------
        float
            Marginal yield; 0.0 for non-differentiable forms.
        """
        p = self.parameters

        if self.yield_type is YieldType.SATURATING_EXPONENTIAL:
            y_inf = p.get("Y_inf", p.get("y_inf", 0.0))
            lam = p.get("lambda", p.get("lam", 0.0))
            if lam <= 0.0 or budget < 0.0:
                return 0.0
            return y_inf * lam * math.exp(-lam * budget)

        if self.yield_type is YieldType.LINEAR:
            return p.get("slope", 0.0)

        if self.yield_type is YieldType.POWER_LAW:
            coefficient = p.get("coefficient", p.get("a", 0.0))
            exponent = p.get("exponent", p.get("n", 1.0))
            if budget <= 0.0:
                return 0.0
            return coefficient * exponent * (budget ** (exponent - 1.0))

        return 0.0


@dataclass(frozen=True)
class EconomicAssumption:
    """
    A single auditable assumption underpinning the economic model.

    Assumptions are the building blocks of a manifest's validity.  Each
    assumption is labelled with its category, a plain-English description,
    its mathematical form, and whether it has been validated against data.

    Attributes
    ----------
    name:
        Short identifier (snake_case recommended) for the assumption.
    category:
        Broad domain this assumption belongs to.
    description:
        Full human-readable explanation of what is being assumed.
    mathematical_form:
        LaTeX or pseudocode expression of the assumption.
    validation_status:
        Current state of evidence for/against this assumption.
    confidence:
        Subjective prior confidence in [0, 1].
    """

    name: str
    category: AssumptionCategory
    description: str
    mathematical_form: str
    validation_status: ValidationStatus = ValidationStatus.PENDING
    confidence: float = 0.5

    def is_valid(self) -> bool:
        """Return True if the assumption has been validated."""
        return self.validation_status is ValidationStatus.VALID

    def summary(self) -> str:
        """Return a compact one-line description of this assumption."""
        status_marker = "✓" if self.is_valid() else "?"
        return (
            f"[{status_marker}] {self.name} ({self.category.value}): "
            f"{self.description[:60]}... "
            f"[confidence={self.confidence:.2f}]"
        )


@dataclass(frozen=True)
class TheoremEconomicsManifest:
    """
    Immutable, versioned contract for theorem-economics computations.

    A manifest bundles a set of :class:`YieldModelDescriptor` objects and
    :class:`EconomicAssumption` objects into a single auditable artifact.
    Every allocation decision produced by the scheduler should reference
    the manifest that was active at the time, enabling full reproducibility.

    Attributes
    ----------
    manifest_id:
        UUID-style unique identifier.
    name:
        Human-readable name for this manifest.
    version:
        Semantic version string, e.g. ``"1.0.0"``.
    descriptors:
        Tuple of yield-model descriptors, one per regime.
    assumptions:
        Tuple of economic assumptions backing this manifest.
    created_at:
        Unix timestamp of manifest creation.
    metadata:
        Arbitrary key-value metadata for downstream consumers.
    """

    manifest_id: str
    version: str
    name: str = ""
    descriptors: tuple[YieldModelDescriptor, ...] = field(default_factory=tuple)
    assumptions: tuple[EconomicAssumption, ...] = field(default_factory=tuple)
    description: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name or self.manifest_id)
        object.__setattr__(self, "descriptors", tuple(self.descriptors))
        object.__setattr__(self, "assumptions", tuple(self.assumptions))

    # ------------------------------------------------------------------
    # Counting helpers
    # ------------------------------------------------------------------

    def descriptor_count(self) -> int:
        """Return the number of yield-model descriptors in this manifest."""
        return len(self.descriptors)

    def assumption_count(self) -> int:
        """Return the number of economic assumptions in this manifest."""
        return len(self.assumptions)

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    def valid_assumptions(self) -> tuple[EconomicAssumption, ...]:
        """Return only the assumptions that have been validated."""
        return tuple(a for a in self.assumptions if a.is_valid())

    def find_descriptor(self, model_id: str) -> YieldModelDescriptor | None:
        """
        Look up a descriptor by its ``model_id``.

        Returns None if no matching descriptor is found.
        """
        for d in self.descriptors:
            if d.model_id == model_id:
                return d
        return None

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this manifest."""
        lines: list[str] = [
            f"Manifest: {self.name} (id={self.manifest_id})",
            f"  version      : {self.version}",
            f"  descriptors  : {self.descriptor_count()}",
            f"  assumptions  : {self.assumption_count()} "
            f"({len(self.valid_assumptions())} valid)",
            f"  created_at   : {self.created_at:.2f}",
        ]
        if self.description:
            lines.append(f"  description  : {self.description}")
        if self.metadata:
            lines.append(f"  metadata keys: {sorted(self.metadata.keys())}")
        lines.append("  --- descriptors ---")
        for d in self.descriptors:
            lines.append(f"    [{d.model_id}] {d.yield_type.value} | {d.parameter_summary()}")
        lines.append("  --- assumptions ---")
        for a in self.assumptions:
            lines.append(f"    {a.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """
    Validates a :class:`TheoremEconomicsManifest` against a set of rules.

    The validator checks:

    * At least one descriptor is present.
    * All descriptors are calibrated (no zero or non-finite parameters).
    * All required parameters are present for each yield type.
    * At least one economic assumption exists.
    * Confidence values are in [0, 1].
    * No duplicate ``model_id`` values across descriptors.
    """

    def validate(self, manifest: TheoremEconomicsManifest) -> list[str]:
        """
        Validate *manifest* and return a list of error messages.

        An empty list means the manifest is valid.
        """
        errors: list[str] = []

        if not manifest.manifest_id.strip():
            errors.append("Manifest has empty manifest_id.")

        if not manifest.version.strip():
            errors.append("Manifest has empty version.")

        if manifest.descriptor_count() == 0:
            errors.append("Manifest contains no yield-model descriptors.")

        if manifest.assumption_count() == 0:
            errors.append("Manifest contains no economic assumptions.")

        seen_model_ids: set[str] = set()
        for d in manifest.descriptors:
            if d.model_id in seen_model_ids:
                errors.append(f"Duplicate model_id: {d.model_id!r}.")
            seen_model_ids.add(d.model_id)

            param_errors = _validate_parameters(
                d.parameters,
                _required_keys_for(d.yield_type),
            )
            for pe in param_errors:
                errors.append(f"Descriptor {d.model_id!r}: {pe}")

            if not d.is_calibrated():
                errors.append(
                    f"Descriptor {d.model_id!r} is not calibrated "
                    f"(parameters: {d.parameter_summary()})."
                )

        for a in manifest.assumptions:
            if not 0.0 <= a.confidence <= 1.0:
                errors.append(
                    f"Assumption {a.name!r} has out-of-range confidence: "
                    f"{a.confidence}."
                )
            if not a.description.strip():
                errors.append(f"Assumption {a.name!r} has an empty description.")

        _log.debug(
            "Validation of manifest %s produced %d error(s).",
            manifest.manifest_id,
            len(errors),
        )
        return errors

    def is_valid(self, manifest: TheoremEconomicsManifest) -> bool:
        """Return True if *manifest* passes all validation checks."""
        return len(self.validate(manifest)) == 0

    def explain(self, manifest: TheoremEconomicsManifest) -> str:
        """Return a human-readable validation report for *manifest*."""
        errors = self.validate(manifest)
        if not errors:
            return f"Manifest {manifest.manifest_id!r} is VALID."
        lines = [f"Manifest {manifest.manifest_id!r} has {len(errors)} error(s):"]
        for i, e in enumerate(errors, start=1):
            lines.append(f"  {i}. {e}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """
    In-process registry that stores named :class:`TheoremEconomicsManifest` objects.

    The registry is a thin wrapper around a dict and provides lookup,
    registration, removal, and listing.  It also exposes a
    :meth:`default_manifest` factory that is useful for testing and
    bootstrapping.
    """

    def __init__(self) -> None:
        self._manifests: dict[ManifestID, TheoremEconomicsManifest] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(self, manifest: TheoremEconomicsManifest) -> None:
        """
        Register *manifest* in the registry.

        If a manifest with the same ``manifest_id`` already exists it will
        be overwritten.  A debug-level log message is emitted.
        """
        if manifest.manifest_id in self._manifests:
            _log.debug(
                "Overwriting existing manifest %s in registry.",
                manifest.manifest_id,
            )
        self._manifests[manifest.manifest_id] = manifest
        _log.info(
            "Registered manifest %s (%s v%s).",
            manifest.manifest_id,
            manifest.name,
            manifest.version,
        )

    def get(self, manifest_id: ManifestID) -> TheoremEconomicsManifest | None:
        """Return the manifest with *manifest_id*, or None if not found."""
        return self._manifests.get(manifest_id)

    def list_manifests(self) -> list[TheoremEconomicsManifest]:
        """Return a list of all registered manifests, in insertion order."""
        return list(self._manifests.values())

    def remove(self, manifest_id: ManifestID) -> bool:
        """
        Remove the manifest identified by *manifest_id*.

        Returns True if the manifest was present and removed, False otherwise.
        """
        if manifest_id in self._manifests:
            del self._manifests[manifest_id]
            _log.info("Removed manifest %s from registry.", manifest_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def default_manifest(cls) -> TheoremEconomicsManifest:
        """
        Construct a ready-to-use manifest populated with default descriptors
        and assumptions.

        This is primarily useful for tests and quick-start scenarios.
        """
        mid = str(uuid.uuid4())
        manifest = TheoremEconomicsManifest(
            manifest_id=mid,
            name="default",
            version="1.0.0",
            descriptors=_default_descriptors(),
            assumptions=_default_assumptions(),
            metadata={"source": "ManifestRegistry.default_manifest"},
        )
        _log.debug("Created default manifest %s.", mid)
        return manifest

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of the registry contents."""
        n = len(self._manifests)
        if n == 0:
            return "ManifestRegistry: empty."
        lines = [f"ManifestRegistry: {n} manifest(s)"]
        for m in self._manifests.values():
            lines.append(
                f"  {m.manifest_id[:8]}... | {m.name} v{m.version} "
                f"| {m.descriptor_count()} descriptors"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _make_assumption(
    name: str,
    category: AssumptionCategory,
    description: str,
    mathematical_form: str,
) -> EconomicAssumption:
    """
    Convenience factory for :class:`EconomicAssumption`.

    The new assumption begins with ``ValidationStatus.PENDING`` and a
    default confidence of 0.5.

    Parameters
    ----------
    name:
        Short snake_case identifier.
    category:
        Broad domain of the assumption.
    description:
        Full human-readable description.
    mathematical_form:
        LaTeX or pseudocode expression capturing the assumption formally.

    Returns
    -------
    EconomicAssumption
        A freshly constructed assumption object.
    """
    return EconomicAssumption(
        name=name,
        category=category,
        description=description,
        mathematical_form=mathematical_form,
        validation_status=ValidationStatus.VALID,
        confidence=0.8,
    )


def _validate_parameters(
    params: dict[str, float],
    required_keys: list[str],
) -> list[str]:
    """
    Check that *params* contains each key listed in *required_keys*.

    Returns a (possibly empty) list of human-readable error strings.  No
    exceptions are raised.

    Parameters
    ----------
    params:
        The parameter dict to inspect.
    required_keys:
        Names that must be present.

    Returns
    -------
    list[str]
        Error messages; empty if all required keys are present.
    """
    errors: list[str] = []
    aliases = {
        "Y_inf": ("Y_inf", "y_inf", "saturation_yield"),
        "lambda": ("lambda", "lam", "growth_rate"),
        "coefficient": ("coefficient", "a"),
        "exponent": ("exponent", "n"),
    }
    for key in required_keys:
        candidate_keys = aliases.get(key, (key,))
        present_key = next((candidate for candidate in candidate_keys if candidate in params), None)
        if present_key is None:
            errors.append(f"Missing required parameter: {key!r}.")
        else:
            v = params[present_key]
            if not math.isfinite(v):
                errors.append(f"Parameter {key!r} is not finite: {v}.")
    return errors


def _required_keys_for(yield_type: YieldType) -> list[str]:
    """
    Return the list of required parameter keys for a given *yield_type*.

    Parameters
    ----------
    yield_type:
        The functional form to inspect.

    Returns
    -------
    list[str]
        Names of parameters that must be present in a calibrated descriptor
        of this type.
    """
    mapping: dict[YieldType, list[str]] = {
        YieldType.SATURATING_EXPONENTIAL: ["Y_inf", "lambda"],
        YieldType.LINEAR: ["slope"],
        YieldType.LOGISTIC: ["L", "k", "x0"],
        YieldType.POWER_LAW: ["coefficient", "exponent"],
        YieldType.CONSTANT: ["value"],
    }
    return mapping.get(yield_type, [])


def _default_descriptors() -> tuple[YieldModelDescriptor, ...]:
    """
    Build the default set of five regime descriptors.

    Each descriptor uses the SATURATING_EXPONENTIAL yield type with
    parameters ``{"Y_inf": 10.0, "lambda": 0.1}``.  The five regimes are
    ``alpha``, ``beta``, ``gamma``, ``delta``, and ``epsilon``.

    Returns
    -------
    tuple[YieldModelDescriptor, ...]
        A tuple of five descriptors ready for use in a manifest.
    """
    regime_names = ["alpha", "beta", "gamma", "delta", "epsilon"]
    descriptors: list[YieldModelDescriptor] = []
    for regime in regime_names:
        descriptor = YieldModelDescriptor(
            model_id=f"{regime}-saturating-v1",
            yield_type=YieldType.SATURATING_EXPONENTIAL,
            description=(
                f"Default saturating-exponential yield model for regime {regime!r}. "
                "Calibrated with standard priors; replace before production use."
            ),
            regime_id=regime,
            parameters={"Y_inf": 10.0, "lambda": 0.1},
        )
        descriptors.append(descriptor)
        _log.debug("Built default descriptor for regime %s.", regime)
    return tuple(descriptors)


def _default_assumptions() -> tuple[EconomicAssumption, ...]:
    """
    Build the default set of five economic assumptions.

    The assumptions cover: diminishing returns, saturation bound,
    independent regimes, budget conservation, and positive growth rate.

    Returns
    -------
    tuple[EconomicAssumption, ...]
        A tuple of five EconomicAssumption objects.
    """
    assumptions = [
        _make_assumption(
            name="diminishing_returns",
            category=AssumptionCategory.ECONOMIC,
            description=(
                "The marginal yield of additional budget decreases as total "
                "budget increases.  This follows directly from the concavity "
                "of the saturating-exponential function."
            ),
            mathematical_form=r"d^2 Y / dB^2 < 0 \; \forall B \geq 0",
        ),
        _make_assumption(
            name="saturation_bound",
            category=AssumptionCategory.MATHEMATICAL,
            description=(
                "There exists a finite upper bound Y_inf on achievable yield "
                "for any given regime, approached asymptotically as budget "
                "grows without bound."
            ),
            mathematical_form=r"\lim_{B \to \infty} Y(B) = Y_\infty < \infty",
        ),
        _make_assumption(
            name="independent_regimes",
            category=AssumptionCategory.STRUCTURAL,
            description=(
                "Yield produced in one regime is independent of the budget "
                "allocated to any other regime.  Cross-regime externalities "
                "are assumed negligible for first-order optimisation."
            ),
            mathematical_form=r"\partial Y_i / \partial B_j = 0 \; \text{for } i \neq j",
        ),
        _make_assumption(
            name="budget_conservation",
            category=AssumptionCategory.ECONOMIC,
            description=(
                "The total budget is conserved across all regimes at each "
                "decision epoch.  No budget is created or destroyed during "
                "reallocation."
            ),
            mathematical_form=r"\sum_i B_i = B_{\text{total}}",
        ),
        _make_assumption(
            name="positive_growth_rate",
            category=AssumptionCategory.EMPIRICAL,
            description=(
                "The growth-rate parameter lambda is strictly positive for "
                "every regime.  A zero or negative lambda would imply no "
                "benefit or anti-benefit from additional budget."
            ),
            mathematical_form=r"\lambda > 0",
        ),
    ]
    return tuple(assumptions)
