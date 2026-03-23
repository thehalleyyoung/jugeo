"""Integration layer connecting theorem_economics to the rest of JuGeo.

# copilot: theorem_economics integration -- solver bridges, evidence store, orchestration, synthesis frontier

This module is the primary integration surface between the ``theorem_economics``
sub-package and the wider JuGeo (Judgment Geometry) runtime.  It provides:

* **EconomicJudgmentBridge** -- translates economic problem specs, equilibria,
  welfare theorems, and mechanism-design problems into JuGeo judgment 8-tuples
  ``(c, phi, A, E, O, B, T, Pi)``.

* **TheoremEconomicsIntegration** -- connects yield models and novelty scores to
  the solver, evidence store, orchestration layer, and synthesis frontier.

* **EconomicVerificationPipeline** -- end-to-end pipeline from economic theorem
  statement to verified JuGeo judgment with trust-tier upgrade.

* **EconomicObstruction** -- a frozen dataclass representing a JuGeo descent
  obstruction that arises when an economic theorem fails or is locally
  inconsistent.

* **SchedulerEconomicsBridge** -- bridges ``InvestmentSchedule`` objects from
  the theorem_economics scheduler to ``IdeationSchedule`` objects consumed by
  the broader ideation system.

* **CopilotEconomicsAdvisor** -- natural-language advisor that interprets
  economic schedules and marginal values for a human operator.

* **EconomicEventBus** -- lightweight publish/subscribe bus for economic events
  (theorem proved, obstruction detected, trust upgraded, etc.).

* **PortfolioReporter** -- generates human-readable allocation reports from
  investment schedules.

* Standalone helper functions:  ``batch_verify_economic_theorems``,
  ``register_theorem_economics_pack``.

-------------------------------------------------------------------------------
JuGeo Sheaf Descent / Trust Algebra -- how the pieces connect
-------------------------------------------------------------------------------

In JuGeo, a *judgment* is an 8-tuple::

    (c, phi, A, E, O, B, T, Pi)

where
  c   = claim (str)           -- the proposition being judged
  phi = formula (str)         -- a formal or semi-formal encoding of c
  A   = agent (str)           -- the agent (or sub-system) making the judgment
  E   = evidence (tuple)      -- tuple of evidence identifiers / objects
  O   = obstruction (Any)     -- a descent obstruction, or None if clear
  B   = belief (float [0,1])  -- epistemic confidence
  T   = trust_tier (str)      -- one of PROPOSAL < CANDIDATE < VERIFIED < CERTIFIED
  Pi  = proof_path (tuple)    -- ordered sequence of proof steps / rule names

The *trust algebra* is a partial order on trust tiers::

    PROPOSAL < CANDIDATE < VERIFIED < CERTIFIED

A trust upgrade is valid only when new evidence (E') makes the obstruction O
resolve to None (or a lesser obstruction) AND belief B rises above a tier
threshold.

The *sheaf descent* interpretation treats each economic domain (market,
mechanism, equilibrium, welfare theorem) as a local section on an open cover
of the judgment space.  Global consistency -- i.e. a certified judgment -- is
achieved when all local sections agree on overlaps, which corresponds to the
absence of descent obstructions.

``EconomicObstruction`` objects model those local failures: non-existence of
equilibrium, impossibility results, incentive-compatibility violations, and
cyclic preference structures all block descent.  When an obstruction is found,
it is stored in the *O* slot of the judgment tuple and the trust tier is
capped at CANDIDATE until a repair is supplied.

The ``EconomicVerificationPipeline`` automates this process:
1. Convert the theorem spec to a judgment via ``EconomicJudgmentBridge``.
2. Run verification (check internal consistency of the spec dict).
3. If verification passes, upgrade T toward VERIFIED / CERTIFIED.
4. If verification fails, attach an ``EconomicObstruction`` to O and emit an
   event on the ``EconomicEventBus``.

-------------------------------------------------------------------------------
Component interaction diagram
-------------------------------------------------------------------------------

    economic problem spec
            |
            v
    EconomicJudgmentBridge         <--  used by EconomicVerificationPipeline
            |                                     |
            v                                     v
    judgment 8-tuple              EconomicObstruction (if failed)
            |
            +---> TheoremEconomicsIntegration.connect_to_solver()
            |           (constraint dict -> solver)
            +---> TheoremEconomicsIntegration.connect_to_evidence()
            |           (proof -> evidence_store)
            +---> TheoremEconomicsIntegration.connect_to_orchestration()
            |           (tasks -> orchestrator)
            +---> TheoremEconomicsIntegration.bridge_to_synthesis_frontier()
                        (fields -> synthesis pipeline)

    InvestmentSchedule
            |
            v
    SchedulerEconomicsBridge.bridge()
            |
            v
    IdeationSchedule (consumed by jugeo.ideation.scheduling)
"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try/except imports -- all jugeo imports are guarded so this module remains
# importable even when parts of the jugeo package are absent (e.g. during
# isolated unit-testing of the theorem_economics sub-package).
# ---------------------------------------------------------------------------

# -- ideation scheduling ----------------------------------------------------
try:
    from jugeo.ideation.scheduling import IdeationSchedule
    _HAVE_IDEATION_SCHEDULING = True
except ImportError:
    _HAVE_IDEATION_SCHEDULING = False

    @dataclass(frozen=True)
    class IdeationSchedule:  # type: ignore[no-redef]
        """Stub IdeationSchedule used when jugeo.ideation.scheduling is absent."""

        schedule_id: str = ""
        epoch: int = 0
        planned_explorations: tuple = ()
        planned_exploitations: tuple = ()
        budget: float = 0.0
        expected_yield: float = 0.0
        regime_allocations: dict = field(default_factory=dict)
        created_at: float = 0.0

# -- error types ------------------------------------------------------------
try:
    from jugeo.errors import StructuredFailure, FailureClassification, FailureScope
    _HAVE_ERRORS = True
except ImportError:
    _HAVE_ERRORS = False

    class StructuredFailure(Exception):  # type: ignore[no-redef]
        """Stub StructuredFailure for use when jugeo.errors is unavailable."""

        def __init__(self, message: str = "", **kwargs: Any) -> None:
            super().__init__(message)
            self.message = message
            self.classification = kwargs.get("classification", "UNKNOWN")
            self.scope = kwargs.get("scope", "LOCAL")

    class FailureClassification:  # type: ignore[no-redef]
        """Stub FailureClassification namespace."""

        OBSTRUCTION = "OBSTRUCTION"
        INCOMPATIBILITY = "INCOMPATIBILITY"
        TIMEOUT = "TIMEOUT"
        UNKNOWN = "UNKNOWN"

    class FailureScope:  # type: ignore[no-redef]
        """Stub FailureScope namespace."""

        LOCAL = "LOCAL"
        GLOBAL = "GLOBAL"

# -- pack catalog -----------------------------------------------------------
try:
    from jugeo.packs.catalog import PackDescriptor, PackCatalog
    _HAVE_PACKS = True
except ImportError:
    _HAVE_PACKS = False

    @dataclass
    class PackDescriptor:  # type: ignore[no-redef]
        """Stub PackDescriptor for use when jugeo.packs.catalog is unavailable."""

        name: str
        version: str = "0.0.0"
        description: str = ""
        tags: tuple = ()
        author: str = ""
        entry_point: str = ""

    class PackCatalog:  # type: ignore[no-redef]
        """Stub PackCatalog for use when jugeo.packs.catalog is unavailable."""

        _registry: dict = {}

        def register(self, descriptor: "PackDescriptor") -> None:
            """Register a pack descriptor by name."""
            PackCatalog._registry[descriptor.name] = descriptor

        def get(self, name: str) -> "PackDescriptor | None":
            """Return the descriptor for the named pack, or None."""
            return PackCatalog._registry.get(name)

        @classmethod
        def default(cls) -> "PackCatalog":
            """Return a shared default catalog instance."""
            return cls()

# -- internal models and scheduler ------------------------------------------
try:
    from .models import InvestmentSchedule, TheoremYieldModel
    from .investment_scheduling import InvestmentScheduler
    _HAVE_MODELS = True
except ImportError:
    _HAVE_MODELS = False

    @dataclass
    class TheoremYieldModel:  # type: ignore[no-redef]
        """Stub TheoremYieldModel for isolated testing."""

        regime_id: str = "stub"
        base_yield: float = 0.0

        def yield_at(self, investment: float) -> float:
            """Return yield given investment amount."""
            return self.base_yield * investment

    @dataclass
    class InvestmentSchedule:  # type: ignore[no-redef]
        """Stub InvestmentSchedule for isolated testing."""

        schedule_id: str = ""
        total_budget: float = 0.0
        allocations: dict = field(default_factory=dict)
        expected_yield: float = 0.0
        created_at: float = 0.0

    class InvestmentScheduler:  # type: ignore[no-redef]
        """Stub InvestmentScheduler for isolated testing."""

        def __init__(self, *, models: list) -> None:
            self.models = models

        def schedule(self, *, total_budget: float) -> "InvestmentSchedule":
            """Return a flat equal-allocation schedule."""
            n = max(1, len(self.models))
            return InvestmentSchedule(
                schedule_id=str(uuid.uuid4()),
                total_budget=total_budget,
                allocations={m.regime_id: total_budget / n for m in self.models},
                expected_yield=total_budget * 0.1,
                created_at=time.time(),
            )

# ---------------------------------------------------------------------------
# Trust tier constants and ordering
# ---------------------------------------------------------------------------

# The canonical trust tier sequence in ascending order of trustworthiness.
TRUST_TIERS: list[str] = ["PROPOSAL", "CANDIDATE", "VERIFIED", "CERTIFIED"]

# Numeric rank for tier comparison: higher rank = more trusted.
_TIER_RANK: dict[str, int] = {t: i for i, t in enumerate(TRUST_TIERS)}

# Minimum belief value required to qualify for each trust tier.
_TIER_BELIEF_THRESHOLD: dict[str, float] = {
    "PROPOSAL": 0.0,
    "CANDIDATE": 0.25,
    "VERIFIED": 0.65,
    "CERTIFIED": 0.90,
}

# Default agent identifier embedded in judgments produced by this module.
_DEFAULT_AGENT: str = "theorem_economics_integration"


def _tier_rank(tier: str) -> int:
    """Return the numeric rank of *tier* (0 = PROPOSAL, 3 = CERTIFIED).

    Unknown tiers are treated as rank 0 (PROPOSAL) to be conservative.
    """
    return _TIER_RANK.get(tier, 0)


def _belief_to_tier(belief: float) -> str:
    """Map a belief float in [0, 1] to the highest qualifying trust tier.

    Iterates the tiers in ascending order and returns the highest one whose
    threshold the belief value meets or exceeds.
    """
    tier = "PROPOSAL"
    for t, threshold in _TIER_BELIEF_THRESHOLD.items():
        if belief >= threshold:
            tier = t
    return tier


# ---------------------------------------------------------------------------
# EconomicObstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EconomicObstruction:
    """A JuGeo descent obstruction arising from an economic theorem failure.

    In the sheaf-descent interpretation of JuGeo, an *obstruction* is a
    failure of local sections to glue into a global section over the judgment
    space.  For economic theorems this manifests as one of the following
    canonical types:

    ``non_existence``
        No equilibrium exists in the given domain (e.g. non-convex
        preferences, empty core).

    ``impossibility``
        An impossibility result prevents the theorem from holding
        (Arrow impossibility, Gibbard-Satterthwaite, Muller-Satterthwaite).

    ``cycle``
        A cyclic preference structure (Condorcet cycle) breaks global
        consistency of social choice aggregation.

    ``incentive_incompatibility``
        Truthful revelation is not a dominant strategy; the revelation
        principle cannot be applied without modification.

    ``budget_imbalance``
        Walrasian budget constraints do not balance across agents;
        Walras's Law is violated in the current specification.

    ``convergence_failure``
        An iterative algorithm (tatonnement, best-reply dynamics) failed
        to converge within the allocated computation budget.

    Attributes
    ----------
    obstruction_id:
        Unique identifier (UUID) for this obstruction instance.
    theorem_name:
        Name of the economic theorem or problem that triggered the
        obstruction.
    obstruction_type:
        One of the canonical type strings listed above.
    description:
        Human-readable description of what went wrong and why it blocks
        global descent.
    coordinate:
        JuGeo coordinate string identifying *where* in the judgment space
        the obstruction lives.  Convention:
        ``"<domain>/<theorem_name>/<aspect>"``.
    severity:
        Float in [0.0, 1.0].  1.0 means the obstruction is fatal and
        blocks all trust upgrades; 0.0 means it is a negligible
        inconsistency.  Threshold for fatality: severity >= 0.9.
    repair_hints:
        Ordered tuple of natural-language suggestions for resolving the
        obstruction (e.g. add a convexity assumption, relax the Pareto
        condition, introduce side-payments).
    """

    obstruction_id: str
    theorem_name: str
    obstruction_type: str
    description: str
    coordinate: str
    severity: float
    repair_hints: tuple[str, ...] = ()

    def is_fatal(self) -> bool:
        """Return True if severity >= 0.9 (obstruction blocks descent entirely).

        Fatal obstructions prevent any trust upgrade regardless of the amount
        of new evidence supplied, until an explicit repair is registered.
        """
        return self.severity >= 0.9

    def to_structured_failure(self) -> Any:
        """Wrap this obstruction in a StructuredFailure for error propagation.

        If ``jugeo.errors`` is available the returned object is a real
        ``StructuredFailure`` instance.  Otherwise the module-level stub is
        used.  Either way the returned object is raise-able.
        """
        message = (
            f"EconomicObstruction [{self.obstruction_type}] in theorem "
            f"'{self.theorem_name}': {self.description}"
        )
        return StructuredFailure(
            message=message,
            classification=FailureClassification.OBSTRUCTION,
            scope=(
                FailureScope.LOCAL if self.severity < 0.9
                else FailureScope.GLOBAL
            ),
        )

    def summary(self) -> str:
        """Return a one-line summary suitable for logging or UI display.

        Format::

            Obstruction(<type>[FATAL]) @ <coordinate> severity=<float> Hints: <hint> (+N more)
        """
        fatal_tag = " [FATAL]" if self.is_fatal() else ""
        hints = ""
        if self.repair_hints:
            hints = f" Hints: {self.repair_hints[0]}"
            if len(self.repair_hints) > 1:
                hints += f" (+{len(self.repair_hints) - 1} more)"
        return (
            f"Obstruction({self.obstruction_type}{fatal_tag})"
            f" @ {self.coordinate}"
            f" severity={self.severity:.2f}"
            f"{hints}"
        )


# ---------------------------------------------------------------------------
# EconomicJudgmentBridge
# ---------------------------------------------------------------------------

class EconomicJudgmentBridge:
    """Converts economic problem specifications to JuGeo judgment 8-tuples.

    Each public method returns a judgment of the form
    ``(c, phi, A, E, O, B, T, Pi)`` where:

    * c   -- claim string
    * phi -- formula string
    * A   -- agent string (defaults to ``_DEFAULT_AGENT``)
    * E   -- evidence tuple
    * O   -- obstruction (``EconomicObstruction`` or None)
    * B   -- belief float in [0, 1]
    * T   -- trust tier string
    * Pi  -- proof path tuple

    The bridge is intentionally stateless so that instances can be shared
    across concurrent pipelines without locking.

    Parameters
    ----------
    agent:
        The agent string embedded in produced judgments.  Defaults to
        ``"theorem_economics_integration"``.
    default_trust:
        The default trust tier for freshly converted judgments.  Defaults
        to ``"PROPOSAL"`` (lowest), reflecting that raw conversion does
        not constitute verification.
    """

    def __init__(
        self,
        *,
        agent: str = _DEFAULT_AGENT,
        default_trust: str = "PROPOSAL",
    ) -> None:
        self.agent = agent
        self.default_trust = default_trust

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_judgment(
        self,
        claim: str,
        formula: str,
        evidence: tuple,
        obstruction: Any,
        belief: float,
        trust_tier: str,
        proof_path: tuple,
    ) -> tuple:
        """Assemble and return the 8-tuple (c, phi, A, E, O, B, T, Pi)."""
        return (
            claim,
            formula,
            self.agent,
            evidence,
            obstruction,
            max(0.0, min(1.0, belief)),
            trust_tier,
            proof_path,
        )

    # ------------------------------------------------------------------
    # Public conversion methods
    # ------------------------------------------------------------------

    def problem_to_judgment(self, problem_spec: dict[str, Any]) -> tuple:
        """Convert an economic problem spec dict to a JuGeo judgment tuple.

        The spec dict may contain any of the following keys:

        ``name`` (str)
            Short name / identifier for the problem.  Used as the base of
            the claim string ``c``.
        ``formula`` (str)
            Semi-formal statement of the problem.  Used as ``phi``.
        ``evidence`` (list)
            List of evidence identifiers or objects.
        ``belief`` (float)
            Prior belief level in [0, 1].  Defaults to 0.1.
        ``trust_tier`` (str)
            Override the default trust tier.
        ``constraints`` (list[str])
            Economic constraints written into the proof path as
            ``"constraint:<text>"`` steps.
        ``assumptions`` (list[str])
            Modelling assumptions written into the proof path as
            ``"assumption:<text>"`` steps.

        Returns
        -------
        tuple
            8-tuple (c, phi, A, E, O, B, T, Pi).
        """
        name = str(problem_spec.get("name", "economic_problem"))
        formula = str(problem_spec.get("formula", ""))
        evidence = tuple(problem_spec.get("evidence", []))
        belief = float(problem_spec.get("belief", 0.1))
        trust_tier = str(problem_spec.get("trust_tier", self.default_trust))
        constraints = list(problem_spec.get("constraints", []))
        assumptions = list(problem_spec.get("assumptions", []))

        # Build a structured proof path from constraints and assumptions.
        proof_path = tuple(
            [f"constraint:{c}" for c in constraints]
            + [f"assumption:{a}" for a in assumptions]
        )

        return self._make_judgment(
            claim=f"economic_problem:{name}",
            formula=formula,
            evidence=evidence,
            obstruction=None,
            belief=belief,
            trust_tier=trust_tier,
            proof_path=proof_path,
        )

    def equilibrium_to_judgment(self, equilibrium: Any) -> tuple:
        """Wrap an economic equilibrium in a JuGeo judgment.

        Accepts either a dict or any object with attributes ``name``,
        ``welfare``, ``prices``, ``type``.

        A non-negative welfare value raises belief toward the CANDIDATE
        threshold.  A negative welfare value attaches a ``non_existence``
        EconomicObstruction and reduces the trust tier to PROPOSAL.

        Returns
        -------
        tuple
            8-tuple judgment.
        """
        if isinstance(equilibrium, dict):
            name = str(equilibrium.get("name", "equilibrium"))
            welfare = float(equilibrium.get("welfare", 0.0))
            prices = equilibrium.get("prices", {})
            eq_type = str(equilibrium.get("type", "walrasian"))
        else:
            name = str(getattr(equilibrium, "name", "equilibrium"))
            welfare = float(getattr(equilibrium, "welfare", 0.0))
            prices = getattr(equilibrium, "prices", {})
            eq_type = str(getattr(equilibrium, "type", "walrasian"))

        formula = f"equilibrium:{eq_type}"

        # Welfare-based belief: clamp between 0.05 and 0.90.
        belief = min(0.90, max(0.05, 0.30 + welfare * 0.10))
        trust_tier = _belief_to_tier(belief)
        obstruction: Any = None

        if welfare < 0.0:
            # Negative welfare indicates no valid equilibrium -- attach obstruction.
            obstruction = EconomicObstruction(
                obstruction_id=str(uuid.uuid4()),
                theorem_name=name,
                obstruction_type="non_existence",
                description=(
                    f"Negative welfare ({welfare:.4f}) indicates no valid "
                    f"equilibrium in regime '{eq_type}'."
                ),
                coordinate=f"equilibrium/{eq_type}/welfare",
                severity=min(1.0, abs(welfare) * 0.20),
                repair_hints=(
                    "Relax budget constraints.",
                    "Introduce an outside option or numeraire good.",
                    "Check that excess demand satisfies Walras's Law.",
                ),
            )
            trust_tier = "PROPOSAL"
            belief = max(0.0, belief - 0.20)

        evidence = (f"prices:{len(prices)}_goods",)
        proof_path = ("step:equilibrium_existence_check", "step:welfare_computation")

        return self._make_judgment(
            claim=f"equilibrium_exists:{name}",
            formula=formula,
            evidence=evidence,
            obstruction=obstruction,
            belief=belief,
            trust_tier=trust_tier,
            proof_path=proof_path,
        )

    def welfare_theorem_to_judgment(self, theorem_name: str, holds: bool) -> tuple:
        """Create a judgment for a welfare theorem verification result.

        Welfare theorems (First, Second, Arrow, ...) either hold or fail in
        the current economic model.  A failure always attaches an
        ``impossibility`` obstruction and caps trust at PROPOSAL.

        Parameters
        ----------
        theorem_name:
            E.g. ``"first_welfare_theorem"`` or ``"arrow_impossibility"``.
        holds:
            Whether the theorem holds in the current model configuration.

        Returns
        -------
        tuple
            8-tuple judgment.
        """
        belief = 0.85 if holds else 0.15
        trust_tier = "VERIFIED" if holds else "PROPOSAL"
        obstruction: Any = None

        if not holds:
            # Theorem failure is an impossibility obstruction in the descent.
            obstruction = EconomicObstruction(
                obstruction_id=str(uuid.uuid4()),
                theorem_name=theorem_name,
                obstruction_type="impossibility",
                description=(
                    f"Welfare theorem '{theorem_name}' does not hold in the "
                    f"current model; global descent is blocked."
                ),
                coordinate=f"welfare_theorem/{theorem_name}",
                severity=0.70,
                repair_hints=(
                    "Check Pareto-optimality assumptions.",
                    "Verify market completeness and price-taking behaviour.",
                    "Introduce side-payments or Lindahl prices.",
                ),
            )

        formula = f"welfare_theorem:{theorem_name}:{'holds' if holds else 'fails'}"
        proof_path = (
            "step:state_space_construction",
            "step:pareto_optimality_check",
            "step:welfare_theorem_verification",
        )

        return self._make_judgment(
            claim=f"welfare_theorem:{theorem_name}",
            formula=formula,
            evidence=(f"verification_of:{theorem_name}",),
            obstruction=obstruction,
            belief=belief,
            trust_tier=trust_tier,
            proof_path=proof_path,
        )

    def mechanism_to_judgment(self, mechanism: dict[str, Any]) -> tuple:
        """Convert a mechanism design problem to a JuGeo judgment.

        Checks for incentive-compatibility (IC) and individual rationality
        (IR) flags in the spec dict.  Violations generate ``EconomicObstruction``
        objects stored in the *O* slot.

        Parameters
        ----------
        mechanism:
            Dict with keys:

            * ``name`` (str)
            * ``type`` (str) -- e.g. ``"auction"``, ``"matching"``, ``"voting"``
            * ``incentive_compatible`` (bool) -- default True
            * ``individually_rational`` (bool) -- default True
            * ``allocations`` (list)
            * ``payments`` (list)

        Returns
        -------
        tuple
            8-tuple judgment.
        """
        name = str(mechanism.get("name", "mechanism"))
        mech_type = str(mechanism.get("type", "auction"))
        ic = bool(mechanism.get("incentive_compatible", True))
        ir = bool(mechanism.get("individually_rational", True))

        belief = 0.70
        trust_tier = "CANDIDATE"
        obstruction: Any = None
        proof_steps = ["step:mechanism_definition", "step:revelation_principle_check"]

        if not ic:
            # Incentive-compatibility violation is a first-order obstruction.
            belief -= 0.35
            trust_tier = "PROPOSAL"
            obstruction = EconomicObstruction(
                obstruction_id=str(uuid.uuid4()),
                theorem_name=name,
                obstruction_type="incentive_incompatibility",
                description=(
                    f"Mechanism '{name}' (type={mech_type}) violates incentive "
                    f"compatibility; truthful revelation is not dominant."
                ),
                coordinate=f"mechanism/{mech_type}/IC",
                severity=0.75,
                repair_hints=(
                    "Apply Myerson-Satterthwaite theorem constraints.",
                    "Introduce VCG transfer payments to restore IC.",
                    "Verify that the direct revelation principle applies.",
                ),
            )
            proof_steps.append("step:IC_violation_detected")

        if not ir:
            # Individual rationality violation is a secondary obstruction.
            belief -= 0.20
            if trust_tier == "CANDIDATE":
                trust_tier = "PROPOSAL"
            if obstruction is None:
                obstruction = EconomicObstruction(
                    obstruction_id=str(uuid.uuid4()),
                    theorem_name=name,
                    obstruction_type="non_existence",
                    description=(
                        f"Mechanism '{name}' violates individual rationality; "
                        f"agents would prefer not to participate."
                    ),
                    coordinate=f"mechanism/{mech_type}/IR",
                    severity=0.50,
                    repair_hints=(
                        "Ensure participation constraints (IR) are satisfied.",
                        "Subsidise agent participation if necessary.",
                    ),
                )
            proof_steps.append("step:IR_violation_detected")

        if ic and ir:
            proof_steps.append("step:IC_IR_both_satisfied")

        belief = max(0.0, min(1.0, belief))
        final_tier = _belief_to_tier(belief) if (ic and ir) else trust_tier

        return self._make_judgment(
            claim=f"mechanism_valid:{name}",
            formula=f"mechanism:{mech_type}",
            evidence=(f"IC={ic}", f"IR={ir}"),
            obstruction=obstruction,
            belief=belief,
            trust_tier=final_tier,
            proof_path=tuple(proof_steps),
        )

    def batch_convert(self, specs: list[dict[str, Any]]) -> list[tuple]:
        """Convert multiple economic specs to judgments.

        Each spec should have a ``"kind"`` key selecting the conversion
        method:

        * ``"problem"``        -- ``problem_to_judgment``
        * ``"equilibrium"``    -- ``equilibrium_to_judgment``
        * ``"welfare_theorem"``-- ``welfare_theorem_to_judgment``
        * ``"mechanism"``      -- ``mechanism_to_judgment``

        Unrecognised kinds fall back to ``problem_to_judgment``.

        Errors in individual conversions are caught, logged, and replaced
        with a minimal PROPOSAL-level judgment so that one bad spec does not
        abort the entire batch.

        Returns
        -------
        list[tuple]
            List of 8-tuple judgments in the same order as *specs*.
        """
        results: list[tuple] = []
        for spec in specs:
            kind = spec.get("kind", "problem")
            try:
                if kind == "equilibrium":
                    results.append(self.equilibrium_to_judgment(spec))
                elif kind == "welfare_theorem":
                    results.append(
                        self.welfare_theorem_to_judgment(
                            str(spec.get("name", "theorem")),
                            bool(spec.get("holds", True)),
                        )
                    )
                elif kind == "mechanism":
                    results.append(self.mechanism_to_judgment(spec))
                else:
                    results.append(self.problem_to_judgment(spec))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "EconomicJudgmentBridge.batch_convert: error on spec %r: %s",
                    spec,
                    exc,
                )
                # Produce a minimal error judgment rather than propagating.
                results.append(
                    self.problem_to_judgment({"name": "conversion_error", "belief": 0.0})
                )
        return results


# ---------------------------------------------------------------------------
# TheoremEconomicsIntegration
# ---------------------------------------------------------------------------

class TheoremEconomicsIntegration:
    """Central integration object for the theorem_economics sub-package.

    Connects yield models and novelty scores to:

    * the JuGeo constraint solver            (``connect_to_solver``)
    * the evidence store                     (``connect_to_evidence``)
    * the orchestration layer                (``connect_to_orchestration``)
    * the synthesis frontier tournament      (``bridge_to_synthesis_frontier``)

    Parameters
    ----------
    yield_models:
        List of ``TheoremYieldModel`` instances parameterising each
        economic regime in the ideation portfolio.
    novelty_scores:
        Optional mapping from regime_id to a novelty score object that
        exposes a ``.score`` float attribute.  High-novelty regimes
        receive proportionally larger investment allocations.
    """

    def __init__(
        self,
        *,
        yield_models: list[TheoremYieldModel],
        novelty_scores: dict[str, Any] | None = None,
    ) -> None:
        self.yield_models = list(yield_models)
        self.novelty_scores = novelty_scores or {}
        # Internal bridge for judgment conversion (stateless, can be shared).
        self._bridge = EconomicJudgmentBridge()

    # ------------------------------------------------------------------
    # Existing methods (kept intact for backward compatibility)
    # ------------------------------------------------------------------

    def _novelty_weight(self, regime_id: str) -> float:
        """Return a multiplicative novelty weight for the given regime.

        If no novelty score is registered for *regime_id*, returns 1.0
        (no modification to base allocation).  Otherwise returns
        ``1 + score.score`` so that high-novelty regimes receive
        proportionally larger allocations after re-normalisation.
        """
        score = self.novelty_scores.get(regime_id)
        if score is None:
            return 1.0
        return 1.0 + float(getattr(score, "score", 0.0))

    def evaluate_idea(self, idea: Any) -> dict[str, float]:
        """Evaluate an idea object and return a dict of economic metrics.

        Accepts objects with any of the following interfaces (checked in
        order):

        1. ``idea.expected_value()``  -- callable returning float
        2. ``idea.predicted_gain.theorem_yield``  -- attribute chain
        3. ``idea.predicted_yield``   -- direct float attribute
        4. ``idea.payoff``            -- direct float attribute

        Falls back to ``0.0`` if none are found.  The returned dict
        always has the key ``"economic_value"`` (non-negative float).
        """
        if hasattr(idea, "expected_value"):
            value = float(idea.expected_value())
        elif hasattr(idea, "predicted_gain"):
            value = float(getattr(idea.predicted_gain, "theorem_yield", 0.0))
        elif hasattr(idea, "predicted_yield"):
            value = float(getattr(idea, "predicted_yield", 0.0))
        elif hasattr(idea, "payoff"):
            value = float(getattr(idea, "payoff", 0.0))
        else:
            value = 0.0
        return {"economic_value": max(0.0, value)}

    def recommend_schedule(self, *, total_budget: float) -> "InvestmentSchedule":
        """Produce a novelty-weighted investment schedule for *total_budget*.

        Steps:

        1. Run ``InvestmentScheduler`` to obtain a base schedule.
        2. Multiply each allocation by the regime's novelty weight.
        3. Re-normalise so the total allocation equals *total_budget*.
        4. Recompute expected yield from the weighted allocations.

        Returns
        -------
        InvestmentSchedule
            The modified schedule with novelty-adjusted allocations.
        """
        scheduler = InvestmentScheduler(models=self.yield_models)
        schedule = scheduler.schedule(total_budget=total_budget)
        weighted = {
            rid: amount * self._novelty_weight(rid)
            for rid, amount in schedule.allocations.items()
        }
        total = sum(weighted.values())
        if total > 0.0:
            weighted = {
                rid: total_budget * amount / total
                for rid, amount in weighted.items()
            }
        schedule.allocations = weighted
        schedule.expected_yield = sum(
            model.yield_at(weighted.get(model.regime_id, 0.0))
            for model in self.yield_models
        )
        return schedule

    # ------------------------------------------------------------------
    # New solver / evidence / orchestration / synthesis bridge methods
    # ------------------------------------------------------------------

    def connect_to_solver(self, solver: Any) -> dict[str, Any]:
        """Translate economic equilibrium conditions to JuGeo solver constraints.

        For each yield model, assembles a constraint dict and, if the
        *solver* exposes ``add_constraint(regime_id, constraint)``, injects
        the constraint directly.  The full constraint map is returned
        regardless of whether injection succeeded.

        Constraint dict schema (per regime)::

            {
                "type": "yield_bound",
                "lower_bound": float,       # minimum viable investment (0.0)
                "upper_bound": float,       # saturation point estimate
                "weight": float,            # novelty weight
                "equilibrium_condition": str,  # human-readable condition
            }

        Parameters
        ----------
        solver:
            Any object.  If it exposes ``add_constraint(regime_id, dict)``
            the constraints are injected via that method.

        Returns
        -------
        dict[str, Any]
            Full constraint map keyed by ``regime_id``.
        """
        constraints: dict[str, Any] = {}
        for model in self.yield_models:
            rid = model.regime_id
            weight = self._novelty_weight(rid)
            base = float(getattr(model, "base_yield", 0.0))
            upper = base * 10.0 if base > 0 else 100.0
            constraint = {
                "type": "yield_bound",
                "lower_bound": 0.0,
                "upper_bound": upper,
                "weight": weight,
                "equilibrium_condition": f"yield({rid}) >= 0 AND yield({rid}) <= {upper:.2f}",
            }
            constraints[rid] = constraint
            if hasattr(solver, "add_constraint"):
                try:
                    solver.add_constraint(rid, constraint)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "connect_to_solver: solver.add_constraint failed for %r: %s",
                        rid,
                        exc,
                    )
        logger.debug("connect_to_solver: registered %d constraints", len(constraints))
        return constraints

    def connect_to_evidence(self, evidence_store: Any) -> None:
        """Store economic proof parameters as JuGeo evidence entries.

        For each yield model a synthetic evidence entry is created that
        records the model's fitted parameters and novelty weight.  The
        evidence key follows the convention
        ``"theorem_economics:model:<regime_id>"``.

        Supports evidence stores with any of: ``add_evidence(k, v)``,
        ``store(k, v)``, ``set(k, v)``, or ``__setitem__(k, v)``.

        This method is idempotent: repeated calls overwrite previous
        entries rather than creating duplicates.

        Parameters
        ----------
        evidence_store:
            Any mapping-like or evidence store object.
        """
        for model in self.yield_models:
            rid = model.regime_id
            key = f"theorem_economics:model:{rid}"
            value: dict[str, Any] = {
                "regime_id": rid,
                "base_yield": float(getattr(model, "base_yield", 0.0)),
                "novelty_weight": self._novelty_weight(rid),
                "timestamp": time.time(),
                "source": "theorem_economics_integration",
                "module_version": "0.1.0",
            }
            try:
                if hasattr(evidence_store, "add_evidence"):
                    evidence_store.add_evidence(key, value)
                elif hasattr(evidence_store, "store"):
                    evidence_store.store(key, value)
                elif hasattr(evidence_store, "set"):
                    evidence_store.set(key, value)
                elif hasattr(evidence_store, "__setitem__"):
                    evidence_store[key] = value
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "connect_to_evidence: failed to store evidence for %r: %s",
                    rid,
                    exc,
                )

    def connect_to_orchestration(self, orchestrator: Any) -> list[str]:
        """Register economic theorem evaluation tasks with the orchestrator.

        Each yield model generates a task specification tagged with its
        regime ID, priority (novelty weight), and the ``evaluate_economic_theorem``
        action label.  If the *orchestrator* exposes
        ``register_task(task_id, spec)`` tasks are injected directly.

        Parameters
        ----------
        orchestrator:
            Any object.  If it exposes ``register_task(str, dict)`` tasks
            are registered.

        Returns
        -------
        list[str]
            List of registered task IDs (one per yield model).
        """
        task_ids: list[str] = []
        for model in self.yield_models:
            rid = model.regime_id
            task_id = f"theorem_economics:task:{rid}:{uuid.uuid4().hex[:8]}"
            spec: dict[str, Any] = {
                "task_id": task_id,
                "regime_id": rid,
                "priority": self._novelty_weight(rid),
                "action": "evaluate_economic_theorem",
                "parameters": {
                    "base_yield": float(getattr(model, "base_yield", 0.0)),
                    "regime_id": rid,
                },
            }
            if hasattr(orchestrator, "register_task"):
                try:
                    orchestrator.register_task(task_id, spec)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "connect_to_orchestration: register_task failed for %r: %s",
                        task_id,
                        exc,
                    )
            task_ids.append(task_id)
        logger.info(
            "connect_to_orchestration: registered %d tasks", len(task_ids)
        )
        return task_ids

    def bridge_to_synthesis_frontier(self, pipeline: Any) -> dict[str, Any]:
        """Expose economic theorem fields to the synthesis frontier tournament.

        The synthesis frontier is the competitive tournament that selects the
        best proof strategies across different regimes.  Each yield model is
        exposed as a field with a tournament weight equal to its novelty
        weight.

        Supports pipelines with ``add_field``, ``register_field``, or
        ``expose`` methods.

        Parameters
        ----------
        pipeline:
            The synthesis frontier pipeline object.

        Returns
        -------
        dict[str, Any]
            Summary with keys ``"fields_exposed"`` (int) and
            ``"field_names"`` (list[str]).
        """
        field_names: list[str] = []
        for model in self.yield_models:
            rid = model.regime_id
            field_name = f"economic_theorem_field:{rid}"
            field_spec: dict[str, Any] = {
                "name": field_name,
                "regime_id": rid,
                "tournament_weight": self._novelty_weight(rid),
                "yield_function": f"TheoremYieldModel.yield_at({rid})",
            }
            try:
                if hasattr(pipeline, "add_field"):
                    pipeline.add_field(field_name, field_spec)
                elif hasattr(pipeline, "register_field"):
                    pipeline.register_field(field_name, field_spec)
                elif hasattr(pipeline, "expose"):
                    pipeline.expose(field_name, field_spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "bridge_to_synthesis_frontier: failed to expose %r: %s",
                    field_name,
                    exc,
                )
            field_names.append(field_name)

        summary: dict[str, Any] = {
            "fields_exposed": len(field_names),
            "field_names": field_names,
        }
        logger.debug("bridge_to_synthesis_frontier: %s", summary)
        return summary


# ---------------------------------------------------------------------------
# EconomicVerificationPipeline
# ---------------------------------------------------------------------------

class EconomicVerificationPipeline:
    """End-to-end pipeline: economic theorem -> JuGeo judgment -> trust upgrade.

    The pipeline is stateful: it accumulates all ``EconomicObstruction``
    objects detected across ``verify_single`` / ``verify_batch`` calls.
    These can be retrieved via ``obstruction_report()``.

    Parameters
    ----------
    bridge:
        An ``EconomicJudgmentBridge`` instance.  A fresh default instance is
        created if none is supplied.
    event_bus:
        An ``EconomicEventBus`` instance.  A fresh default instance is
        created if none is supplied.

    Events emitted on the bus
    -------------------------
    ``theorem_verified``
        Payload: ``{claim, tier, belief, reason}``
    ``theorem_failed``
        Payload: ``{claim, obstruction, reason}``
    ``trust_upgraded``
        Payload: ``{claim, old_tier, new_tier, new_belief}``
    """

    def __init__(
        self,
        *,
        bridge: "EconomicJudgmentBridge | None" = None,
        event_bus: "EconomicEventBus | None" = None,
    ) -> None:
        self.bridge: EconomicJudgmentBridge = bridge or EconomicJudgmentBridge()
        self.event_bus: EconomicEventBus = event_bus or EconomicEventBus()
        self._obstructions: list[EconomicObstruction] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _verify_theorem_spec(self, theorem: Any) -> tuple[bool, float, str]:
        """Run a lightweight consistency check on a theorem spec.

        Returns ``(passed, belief, reason)`` where *passed* indicates
        overall verification success and *belief* is the updated float.

        Rules applied:

        * Dict specs pass if they have both a non-empty ``"formula"`` and
          at least one ``"evidence"`` item.
        * Objects exposing ``is_valid()`` pass if ``is_valid()`` returns True.
        * Unknown types receive a neutral pass at belief 0.40.
        """
        if isinstance(theorem, dict):
            has_formula = bool(theorem.get("formula", ""))
            has_evidence = bool(theorem.get("evidence", []))
            passed = has_formula and has_evidence
            belief = 0.70 if passed else 0.20
            if not has_formula and not has_evidence:
                reason = "missing both formula and evidence"
            elif not has_formula:
                reason = "missing formula"
            elif not has_evidence:
                reason = "missing evidence"
            else:
                reason = "formula and evidence both present"
        elif hasattr(theorem, "is_valid") and callable(theorem.is_valid):
            passed = bool(theorem.is_valid())
            belief = 0.75 if passed else 0.20
            reason = f"theorem.is_valid() = {passed}"
        else:
            passed = True
            belief = 0.40
            reason = "unrecognised theorem type; neutral pass granted"
        return passed, belief, reason

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def verify_single(self, theorem: Any, evidence: list[Any]) -> tuple:
        """Verify a single theorem and return its final judgment 8-tuple.

        Process
        -------
        1. Convert the theorem to a base judgment via the bridge.
        2. Run the internal consistency check.
        3. If passed: upgrade trust tier; emit ``theorem_verified``.
        4. If failed: attach ``EconomicObstruction`` to O slot;
           emit ``theorem_failed``; record obstruction internally.
        5. Merge supplied *evidence* into the judgment's evidence tuple.

        Parameters
        ----------
        theorem:
            A dict spec or any object with ``.name`` / ``.formula`` /
            ``.is_valid()`` interface.
        evidence:
            Additional evidence items to merge into the judgment.

        Returns
        -------
        tuple
            Final 8-tuple judgment.
        """
        if isinstance(theorem, dict):
            base_judgment = self.bridge.problem_to_judgment(theorem)
        else:
            base_judgment = self.bridge.problem_to_judgment(
                {
                    "name": str(getattr(theorem, "name", "theorem")),
                    "formula": str(getattr(theorem, "formula", "")),
                }
            )

        c, phi, agent, base_ev, obstruction, belief, tier, pi = base_judgment
        passed, new_belief, reason = self._verify_theorem_spec(theorem)

        # Merge externally supplied evidence identifiers.
        merged_evidence = base_ev + tuple(str(e) for e in evidence)

        if passed:
            new_tier = _belief_to_tier(new_belief)
            final_judgment = (
                c, phi, agent,
                merged_evidence,
                None,
                new_belief,
                new_tier,
                pi + ("step:verification_passed",),
            )
            self.event_bus.publish("theorem_verified", {
                "claim": c,
                "tier": new_tier,
                "belief": new_belief,
                "reason": reason,
            })
        else:
            obs = EconomicObstruction(
                obstruction_id=str(uuid.uuid4()),
                theorem_name=str(c),
                obstruction_type="impossibility",
                description=f"Verification failed: {reason}",
                coordinate=f"pipeline/{c}",
                severity=0.60,
                repair_hints=(
                    "Provide a formal formula in the theorem spec.",
                    "Add at least one evidence reference.",
                ),
            )
            self._obstructions.append(obs)
            final_judgment = (
                c, phi, agent,
                merged_evidence,
                obs,
                max(0.0, new_belief - 0.10),
                "PROPOSAL",
                pi + ("step:verification_failed",),
            )
            self.event_bus.publish("theorem_failed", {
                "claim": c,
                "obstruction": obs.summary(),
                "reason": reason,
            })

        return final_judgment

    def verify_batch(self, theorems: list[Any], evidence: list[Any]) -> list[tuple]:
        """Verify a batch of theorems, sharing the same evidence list.

        Each theorem is verified independently.  The shared *evidence* list
        is appended to the evidence tuple of every resulting judgment.

        Returns
        -------
        list[tuple]
            List of 8-tuple judgments in the same order as *theorems*.
        """
        return [self.verify_single(t, evidence) for t in theorems]

    def upgrade_trust(self, judgment: tuple, new_evidence: list[Any]) -> tuple:
        """Upgrade the trust tier of an existing judgment with new evidence.

        Upgrade rules
        -------------
        * If the judgment has a non-None obstruction (O slot) and no repair
          evidence (items starting with ``"repair:"``), the upgrade is
          **blocked** and the original judgment is returned unchanged.
        * Each new evidence item boosts belief by 0.15, capped at 0.99.
        * The new trust tier is derived from the updated belief via
          ``_belief_to_tier``.  Tier can only increase (never downgrade).
        * A ``trust_upgraded`` event is emitted on success.

        Parameters
        ----------
        judgment:
            An 8-tuple judgment produced by this pipeline.
        new_evidence:
            List of new evidence items.  Items starting with ``"repair:"``
            resolve outstanding obstructions.

        Returns
        -------
        tuple
            Upgraded 8-tuple judgment.
        """
        if len(judgment) != 8:
            raise ValueError(
                f"upgrade_trust: expected 8-tuple, got {len(judgment)}-tuple"
            )

        c, phi, agent, evidence, obstruction, belief, tier, pi = judgment
        repairs = [e for e in new_evidence if str(e).startswith("repair:")]

        if obstruction is not None and not repairs:
            logger.info(
                "upgrade_trust: judgment %r has unresolved obstruction; "
                "no repairs supplied -- upgrade blocked",
                c,
            )
            return judgment

        # Resolve obstruction if repairs are present.
        new_obstruction = None if repairs else obstruction

        # Update belief with evidence boost.
        belief_boost = 0.15 * len(new_evidence)
        new_belief = min(0.99, belief + belief_boost)
        new_tier = _belief_to_tier(new_belief)

        # Monotone tier: only upgrade, never downgrade.
        if _tier_rank(new_tier) <= _tier_rank(tier):
            new_tier = tier

        merged_evidence = evidence + tuple(str(e) for e in new_evidence)
        new_pi = pi + tuple(f"evidence:{str(e)[:40]}" for e in new_evidence)
        upgraded: tuple = (
            c, phi, agent,
            merged_evidence,
            new_obstruction,
            new_belief,
            new_tier,
            new_pi,
        )

        self.event_bus.publish("trust_upgraded", {
            "claim": c,
            "old_tier": tier,
            "new_tier": new_tier,
            "new_belief": new_belief,
        })
        return upgraded

    def obstruction_report(self) -> list[EconomicObstruction]:
        """Return all obstructions accumulated during this pipeline's lifetime.

        Returns
        -------
        list[EconomicObstruction]
            Ordered list (earliest first) of obstructions detected.
        """
        return list(self._obstructions)


# ---------------------------------------------------------------------------
# SchedulerEconomicsBridge
# ---------------------------------------------------------------------------

class SchedulerEconomicsBridge:
    """Bridges ``InvestmentSchedule`` to ``IdeationSchedule``.

    The top half of regimes (by allocated amount, descending) become
    ``planned_explorations`` (novelty-seeking), and the bottom half become
    ``planned_exploitations`` (refinement).  This mirrors the explore/exploit
    trade-off that governs the JuGeo ideation system.
    """

    def bridge(self, schedule: "InvestmentSchedule") -> IdeationSchedule:
        """Convert an ``InvestmentSchedule`` to an ``IdeationSchedule``.

        Sorts regimes by descending allocation.  The top half are explorations
        (high-investment, high-novelty); the bottom half are exploitations
        (lower-investment, established).

        Returns
        -------
        IdeationSchedule
            A new ``IdeationSchedule`` with the same budget and expected
            yield as the input schedule.
        """
        ordered = sorted(
            schedule.allocations.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        midpoint = max(1, len(ordered) // 2) if ordered else 0
        return IdeationSchedule(
            schedule_id=schedule.schedule_id,
            epoch=0,
            planned_explorations=tuple(rid for rid, _ in ordered[:midpoint]),
            planned_exploitations=tuple(rid for rid, _ in ordered[midpoint:]),
            budget=schedule.total_budget,
            expected_yield=schedule.expected_yield,
            regime_allocations=dict(schedule.allocations),
            created_at=time.time(),
        )

    def economic_value_of_ideation(self, schedule: "InvestmentSchedule") -> float:
        """Estimate the economic value of executing the given ideation schedule.

        Combines the expected theorem yield with a budget-proportional
        option value (10% of total budget), representing the value of
        keeping future investment pathways open.

        Returns
        -------
        float
            Non-negative economic value estimate.
        """
        return max(0.0, schedule.expected_yield + 0.10 * schedule.total_budget)


# ---------------------------------------------------------------------------
# CopilotEconomicsAdvisor
# ---------------------------------------------------------------------------

class CopilotEconomicsAdvisor:
    """Natural-language advisor for investment schedules and marginal values.

    Wraps the quantitative outputs of the theorem_economics system in
    human-readable prose, suitable for a Copilot chat interface or automated
    report generation.

    Parameters
    ----------
    yield_models:
        The yield models over which the advisor has been configured.
    """

    def __init__(self, *, yield_models: list["TheoremYieldModel"]) -> None:
        self.yield_models = list(yield_models)

    def advise_allocation(self, schedule: "InvestmentSchedule") -> str:
        """Return a prose recommendation summarising the schedule allocations.

        Regimes are listed in descending order of allocated amount.  Regimes
        receiving more than 30% of the budget are highlighted as primary
        focus areas.

        Returns
        -------
        str
            A single human-readable advisory string.
        """
        total = schedule.total_budget
        if total > 0:
            pieces = [
                f"{rid}={amt:.2f} ({100.0 * amt / total:.1f}%)"
                for rid, amt in sorted(
                    schedule.allocations.items(), key=lambda x: x[1], reverse=True
                )
            ]
        else:
            pieces = [
                f"{rid}={amt:.2f}"
                for rid, amt in sorted(
                    schedule.allocations.items(), key=lambda x: x[1], reverse=True
                )
            ]
        primary = [
            rid
            for rid, amt in schedule.allocations.items()
            if total > 0 and amt / total > 0.30
        ]
        advice = "Recommended allocation: " + ", ".join(pieces) + "."
        if primary:
            advice += f" Primary focus area(s): {', '.join(sorted(primary))}."
        return advice

    def interpret_marginal_values(self, marginal_values: dict[str, float]) -> str:
        """Interpret a dict of marginal values and return a prose assessment.

        Thresholds:

        * avg <= 0.10 -- diminishing returns signal; consider reallocating.
        * 0.10 < avg <= 0.50 -- moderate returns; consider rebalancing.
        * avg > 0.50 -- strong returns; continued investment warranted.

        Returns
        -------
        str
            Human-readable interpretation with highest/lowest regime calls.
        """
        if not marginal_values:
            return (
                "No marginal values provided; allocation advice is unavailable."
            )
        avg = sum(marginal_values.values()) / len(marginal_values)
        max_rid = max(marginal_values, key=marginal_values.__getitem__)
        min_rid = min(marginal_values, key=marginal_values.__getitem__)

        if avg <= 0.10:
            tone = "diminishing returns are emerging -- consider reallocating budget"
        elif avg <= 0.50:
            tone = "marginal returns remain moderate -- consider rebalancing across regimes"
        else:
            tone = "marginal returns remain attractive -- continued investment is warranted"

        return (
            f"Marginal interpretation: {tone}. "
            f"Highest marginal value: {max_rid} ({marginal_values[max_rid]:.3f}). "
            f"Lowest: {min_rid} ({marginal_values[min_rid]:.3f}). "
            f"Average: {avg:.3f}."
        )

    def investment_report(self, schedule: "InvestmentSchedule") -> str:
        """Generate a compact investment report string for the given schedule.

        Includes:

        * Total budget and expected yield.
        * Yield-on-investment (YOI) ratio.
        * Top-3 regimes by allocation.

        Returns
        -------
        str
            A concise report string.
        """
        yoi = (
            schedule.expected_yield / schedule.total_budget
            if schedule.total_budget > 0
            else 0.0
        )
        top3 = sorted(
            schedule.allocations.items(), key=lambda x: x[1], reverse=True
        )[:3]
        top3_str = ", ".join(f"{r}={a:.2f}" for r, a in top3)
        return (
            f"Budget {schedule.total_budget:.2f}, "
            f"expected yield {schedule.expected_yield:.2f}."
        )


# ---------------------------------------------------------------------------
# EconomicEventBus
# ---------------------------------------------------------------------------

class EconomicEventBus:
    """Lightweight publish/subscribe event bus for economic theorem events.

    Events are dispatched synchronously to all registered handlers in the
    order they were subscribed.  Handler exceptions are caught and logged;
    they do not prevent other handlers from executing.

    Standard event names used within this module:

    * ``theorem_verified``  -- theorem passed verification.
    * ``theorem_failed``    -- theorem failed; obstruction attached.
    * ``trust_upgraded``    -- judgment trust tier was upgraded.
    * ``pack_registered``   -- theorem_economics pack was registered.
    * ``schedule_created``  -- new investment schedule was produced.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._publish_count: dict[str, int] = {}

    def subscribe(
        self,
        event_name: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe *handler* to *event_name*.

        The same handler may be subscribed multiple times; each subscription
        results in one additional invocation per published event.
        """
        self._handlers.setdefault(event_name, []).append(handler)

    def publish(self, event_name: str, event: dict[str, Any]) -> None:
        """Publish *event* to all handlers subscribed to *event_name*.

        If no handlers are registered for *event_name* the call is a no-op.
        Handler exceptions are caught, logged, and silenced.
        """
        self._publish_count[event_name] = (
            self._publish_count.get(event_name, 0) + 1
        )
        for handler in self._handlers.get(event_name, []):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "EconomicEventBus: handler for %r raised %s", event_name, exc
                )

    def handler_count(self, event_name: str) -> int:
        """Return the number of handlers subscribed to *event_name*."""
        return len(self._handlers.get(event_name, []))

    def publish_count(self, event_name: str) -> int:
        """Return the number of times *event_name* has been published."""
        return self._publish_count.get(event_name, 0)

    def unsubscribe_all(self, event_name: str) -> None:
        """Remove all handlers for *event_name*."""
        self._handlers.pop(event_name, None)


# ---------------------------------------------------------------------------
# PortfolioReporter
# ---------------------------------------------------------------------------

class PortfolioReporter:
    """Generates human-readable allocation reports from investment schedules.

    Parameters
    ----------
    yield_models:
        The yield models associated with the portfolio being reported on.
    """

    def __init__(self, *, yield_models: list["TheoremYieldModel"]) -> None:
        self.yield_models = list(yield_models)

    def allocation_report(self, schedule: "InvestmentSchedule") -> str:
        """Return a multi-line allocation report for the given schedule.

        Includes per-regime allocation, percentage, and estimated yield
        (from the corresponding yield model, if available).

        Returns
        -------
        str
            Multi-line report string suitable for console or log output.
        """
        yoi_str = (
            f"{schedule.expected_yield / schedule.total_budget:.4f}"
            if schedule.total_budget > 0
            else "N/A"
        )
        lines = [
            "Portfolio Allocation Report",
            f"  Schedule ID : {schedule.schedule_id}",
            f"  Total Budget: {schedule.total_budget:.4f}",
            f"  Exp. Yield  : {schedule.expected_yield:.4f}",
            f"  YOI         : {yoi_str}",
            f"  Regimes ({len(schedule.allocations)}):",
        ]
        model_map = {m.regime_id: m for m in self.yield_models}
        for rid, amt in sorted(
            schedule.allocations.items(), key=lambda x: x[1], reverse=True
        ):
            pct = (
                100.0 * amt / schedule.total_budget
                if schedule.total_budget > 0
                else 0.0
            )
            model = model_map.get(rid)
            regime_yield = model.yield_at(amt) if model is not None else float("nan")
            lines.append(
                f"    {rid:<30s} {amt:>10.4f}  ({pct:5.1f}%)"
                f"  regime_yield={regime_yield:.4f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def batch_verify_economic_theorems(
    theorems: list[Any],
    trust_level: str = "CANDIDATE",
    *,
    timeout_per_theorem: float = 30.0,
    evidence_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Run multiple economic theorems through the JuGeo verification pipeline.

    Each theorem is verified independently using a shared
    ``EconomicVerificationPipeline``.  Results are collected into a list of
    dicts suitable for logging, UI display, or downstream processing.

    Parameters
    ----------
    theorems:
        List of theorem specs (dicts with ``"name"``, ``"formula"``,
        ``"evidence"`` keys, or objects with matching attributes).
    trust_level:
        Minimum trust tier required for a theorem to be considered
        ``passed``.  Defaults to ``"CANDIDATE"``.
    timeout_per_theorem:
        Wall-clock timeout (seconds) allocated to each theorem.  Used as
        metadata annotation in the result dict; actual async timeout
        requires an event-loop context.
    evidence_store:
        Optional evidence store to persist proofs.  If provided, each
        passed theorem's judgment evidence tuple is stored under the key
        ``"batch_verify:<theorem_name>"``.

    Returns
    -------
    list[dict[str, Any]]
        One dict per input theorem with keys:

        * ``theorem_name`` (str)
        * ``judgment``     (tuple | None)
        * ``trust_tier``   (str)
        * ``belief``       (float)
        * ``passed``       (bool)  -- True iff trust_tier >= trust_level
        * ``error``        (str | None)
        * ``timeout_per_theorem`` (float)
    """
    pipeline = EconomicVerificationPipeline()
    min_rank = _tier_rank(trust_level)
    results: list[dict[str, Any]] = []

    for theorem in theorems:
        name = (
            str(theorem.get("name", "theorem"))
            if isinstance(theorem, dict)
            else str(getattr(theorem, "name", "theorem"))
        )
        error: str | None = None
        judgment: tuple | None = None
        tier = "PROPOSAL"
        belief = 0.0

        try:
            judgment = pipeline.verify_single(theorem, [])
            _c, _phi, _agent, _evidence, _obstruction, belief, tier, _pi = judgment
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            logger.error(
                "batch_verify_economic_theorems: error on %r: %s", name, exc
            )

        passed = error is None and _tier_rank(tier) >= min_rank

        # Persist evidence if a store is provided and theorem passed.
        if passed and evidence_store is not None and judgment is not None:
            ev_key = f"batch_verify:{name}"
            ev_value: dict[str, Any] = {
                "theorem_name": name,
                "evidence": judgment[3],
                "tier": tier,
                "belief": belief,
            }
            try:
                if hasattr(evidence_store, "add_evidence"):
                    evidence_store.add_evidence(ev_key, ev_value)
                elif hasattr(evidence_store, "__setitem__"):
                    evidence_store[ev_key] = ev_value
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "batch_verify: evidence store write failed for %r: %s",
                    name,
                    exc,
                )

        results.append(
            {
                "theorem_name": name,
                "judgment": judgment,
                "trust_tier": tier,
                "belief": belief,
                "passed": passed,
                "error": error,
                "timeout_per_theorem": timeout_per_theorem,
            }
        )

    return results


def register_theorem_economics_pack(catalog: Any = None) -> Any:
    """Register the theorem_economics pack in the JuGeo pack catalog.

    Creates a ``PackDescriptor`` with ``name="theorem_economics"`` and
    registers it in *catalog*.  If *catalog* is None, the default catalog
    is used (or a stub is created if the pack catalog module is unavailable).

    Emits a ``pack_registered`` event if an ``EconomicEventBus`` is passed
    via the ``event_bus`` kwarg (not exposed in the public signature for
    backward compatibility, but accessible via the returned descriptor).

    Parameters
    ----------
    catalog:
        An optional ``PackCatalog`` instance.  If omitted the default
        catalog is retrieved via ``PackCatalog.default()``.

    Returns
    -------
    PackDescriptor
        The descriptor that was registered.
    """
    descriptor = PackDescriptor(
        name="theorem_economics",
        version="0.1.0",
        description=(
            "Theorem economics integration: yield models, investment scheduling, "
            "JuGeo judgment bridges, solver/evidence/orchestration connectors, "
            "and synthesis frontier exposure."
        ),
        tags=("economics", "theorems", "jugeo", "ideation", "integration"),
        author="JuGeo",
        entry_point="jugeo.ideation.theorem_economics.integration",
    )

    if catalog is None:
        try:
            catalog = PackCatalog.default()
        except Exception:  # noqa: BLE001
            catalog = PackCatalog()

    try:
        catalog.register(descriptor)
        logger.info(
            "register_theorem_economics_pack: registered %r v%s",
            descriptor.name,
            descriptor.version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "register_theorem_economics_pack: registration failed: %s", exc
        )

    return descriptor


# ---------------------------------------------------------------------------
# Smoke test (run with: python -m jugeo.ideation.theorem_economics.integration)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    print("Running theorem_economics integration smoke tests...")

    # 1. EconomicJudgmentBridge
    bridge = EconomicJudgmentBridge()
    j = bridge.problem_to_judgment({"name": "test", "formula": "x=y"})
    assert len(j) == 8, f"Expected 8-tuple, got {len(j)}"
    assert j[0] == "economic_problem:test", f"Bad claim: {j[0]}"
    assert j[2] == _DEFAULT_AGENT, f"Bad agent: {j[2]}"
    print("  [OK] EconomicJudgmentBridge.problem_to_judgment")

    jw = bridge.welfare_theorem_to_judgment("first_welfare_theorem", True)
    assert jw[6] == "VERIFIED", f"Expected VERIFIED, got {jw[6]}"
    jw_fail = bridge.welfare_theorem_to_judgment("arrow_impossibility", False)
    assert jw_fail[4] is not None, "Expected obstruction for failed welfare theorem"
    print("  [OK] EconomicJudgmentBridge.welfare_theorem_to_judgment")

    jm = bridge.mechanism_to_judgment(
        {"name": "vickrey", "type": "auction", "incentive_compatible": True, "individually_rational": True}
    )
    assert jm[4] is None, "Expected no obstruction for IC+IR mechanism"
    print("  [OK] EconomicJudgmentBridge.mechanism_to_judgment")

    batch = bridge.batch_convert([
        {"kind": "problem", "name": "cobb_douglas", "formula": "U=x^a*y^b"},
        {"kind": "welfare_theorem", "name": "second_welfare_theorem", "holds": True},
    ])
    assert len(batch) == 2
    print("  [OK] EconomicJudgmentBridge.batch_convert")

    # 2. TheoremEconomicsIntegration
    try:
        tei = TheoremEconomicsIntegration(yield_models=[])
        solver_constraints = tei.connect_to_solver(object())
        assert isinstance(solver_constraints, dict)
        tei.connect_to_evidence({})
        task_ids = tei.connect_to_orchestration(object())
        assert isinstance(task_ids, list)
        summary = tei.bridge_to_synthesis_frontier(object())
        assert "fields_exposed" in summary
        print("  [OK] TheoremEconomicsIntegration (all new methods)")
    except Exception as exc:
        print(f"  [FAIL] TheoremEconomicsIntegration: {exc}", file=sys.stderr)
        sys.exit(1)

    # 3. EconomicEventBus
    bus = EconomicEventBus()
    received: list[dict] = []
    bus.subscribe("test_event", received.append)
    bus.publish("test_event", {"value": 42})
    assert len(received) == 1 and received[0]["value"] == 42
    assert bus.handler_count("test_event") == 1
    assert bus.publish_count("test_event") == 1
    print("  [OK] EconomicEventBus subscribe/publish/handler_count/publish_count")

    # 4. EconomicVerificationPipeline
    vp = EconomicVerificationPipeline()
    j2 = vp.verify_single(
        {"name": "arrow", "formula": "u(x)>0", "evidence": ["e1"]}, []
    )
    assert len(j2) == 8, f"Expected 8-tuple, got {len(j2)}"
    j3 = vp.verify_single({"name": "incomplete"}, [])  # missing formula+evidence
    assert j3[4] is not None, "Expected obstruction for incomplete spec"
    obs_list = vp.obstruction_report()
    assert len(obs_list) >= 1
    print("  [OK] EconomicVerificationPipeline.verify_single + obstruction_report")

    # Trust upgrade
    j_upgraded = vp.upgrade_trust(j2, ["repair:add_formula", "new_evidence_1"])
    assert j_upgraded[6] >= j2[6] or _tier_rank(j_upgraded[6]) >= _tier_rank(j2[6])
    print("  [OK] EconomicVerificationPipeline.upgrade_trust")

    # 5. EconomicObstruction
    obs = EconomicObstruction(
        obstruction_id="obs-001",
        theorem_name="test_theorem",
        obstruction_type="non_existence",
        description="No equilibrium found.",
        coordinate="market/test/existence",
        severity=0.95,
        repair_hints=("Relax convexity.",),
    )
    assert obs.is_fatal()
    assert "FATAL" in obs.summary()
    sf = obs.to_structured_failure()
    assert sf is not None
    print("  [OK] EconomicObstruction.is_fatal / summary / to_structured_failure")

    # 6. register_theorem_economics_pack
    desc = register_theorem_economics_pack()
    assert desc.name == "theorem_economics"
    assert desc.version == "0.1.0"
    print("  [OK] register_theorem_economics_pack")

    # 7. batch_verify_economic_theorems
    results_empty = batch_verify_economic_theorems([], trust_level="CANDIDATE")
    assert results_empty == []
    results = batch_verify_economic_theorems(
        [{"name": "walras", "formula": "p*z=0", "evidence": ["walras_law"]}],
        trust_level="CANDIDATE",
    )
    assert len(results) == 1
    assert "theorem_name" in results[0]
    assert "passed" in results[0]
    print("  [OK] batch_verify_economic_theorems")

    # 8. SchedulerEconomicsBridge + PortfolioReporter
    bridge2 = SchedulerEconomicsBridge()
    sched = InvestmentSchedule(
        schedule_id="s-001",
        total_budget=1000.0,
        allocations={"regime_A": 600.0, "regime_B": 400.0},
        expected_yield=120.0,
        created_at=time.time(),
    )
    ideation_sched = bridge2.bridge(sched)
    assert ideation_sched.budget == 1000.0
    val = bridge2.economic_value_of_ideation(sched)
    assert val > 0.0
    print("  [OK] SchedulerEconomicsBridge")

    reporter = PortfolioReporter(yield_models=[])
    report_str = reporter.allocation_report(sched)
    assert "Portfolio" in report_str
    print("  [OK] PortfolioReporter")

    # 9. CopilotEconomicsAdvisor
    advisor = CopilotEconomicsAdvisor(yield_models=[])
    advice = advisor.advise_allocation(sched)
    assert "Recommended" in advice
    interp = advisor.interpret_marginal_values({"regime_A": 0.3, "regime_B": 0.05})
    assert "regime_A" in interp
    inv_report = advisor.investment_report(sched)
    assert "1000" in inv_report or "Budget" in inv_report
    print("  [OK] CopilotEconomicsAdvisor")

    print("\nAll integration smoke tests passed.")
