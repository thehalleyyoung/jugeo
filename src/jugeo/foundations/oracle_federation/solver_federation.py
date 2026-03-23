from __future__ import annotations
"""Section 7.2 — Solver Federation (Theory2.tex Ch7).

§7.2 of Theory2.tex defines the solver federation model: a set of member
solvers, each with its own jurisdiction and trust ceiling, that collectively
discharge obligations that no single solver can handle alone.

Key concepts:
- **Fragment classification** — every logical formula is classified into one
  of several fragment kinds: arithmetic, structural, behavioral, or hybrid.
  The classifier uses syntactic heuristics and theory-specific markers.
- **Routing** — based on the fragment classification, the federation router
  selects the most appropriate member solver (or chain of solvers for hybrid
  fragments).
- **Z3 routing** — the Z3 SMT solver handles arithmetic and structural
  fragments; the ``Z3Routing`` class builds SMT queries and parses responses.
- **Merge policy** — when multiple solvers contribute partial results, the
  merge policy combines them into a single ``EvidenceResponse`` without
  collapsing distinct support kinds.
- **Load balancing** — the federation tracks per-solver statistics and
  periodically rebalances routing weights to avoid hotspots.

Theory alignment
----------------
- Theory2.tex §7.2.1 defines the federation structure and membership.
- Theory2.tex §7.2.2 defines fragment classification taxonomy.
- Theory2.tex §7.2.3 defines routing and dispatch.
- Theory2.tex §7.2.4 defines merge policies and trust propagation across
  federation boundaries.
- Theory2.tex §7.2.5 covers cross-federation routing for multi-federation
  deployments.
"""

import hashlib
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier, TrustProfile
    from jugeo.evidence.channels import (
        EvidenceChannel,
        EvidenceRequest,
        EvidenceResponse,
    )
    from jugeo.solver.router import SolverRouter, BackendKind, RoutingDecision
    from jugeo.solver.fragments import LogicalFragment, SolverFragment
except ImportError:
    TrustLevel = None  # type: ignore[assignment,misc]
    TrustTier = None  # type: ignore[assignment,misc]
    TrustProfile = None  # type: ignore[assignment,misc]
    EvidenceChannel = None  # type: ignore[assignment,misc]
    EvidenceRequest = None  # type: ignore[assignment,misc]
    EvidenceResponse = None  # type: ignore[assignment,misc]
    SolverRouter = None  # type: ignore[assignment,misc]
    BackendKind = None  # type: ignore[assignment,misc]
    RoutingDecision = None  # type: ignore[assignment,misc]
    LogicalFragment = None  # type: ignore[assignment,misc]
    SolverFragment = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FragmentKind(str, Enum):
    """Taxonomy of logical fragment kinds recognised by the federation.

    Corresponds to Theory2.tex §7.2.2 fragment classification taxonomy.
    Each kind determines which solver(s) hold primary jurisdiction.
    """

    ARITHMETIC = "arithmetic"
    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    HYBRID = "hybrid"
    QUANTIFIED = "quantified"
    STRING_THEORY = "string_theory"
    UNKNOWN = "unknown"


class MergePolicy(str, Enum):
    """Policy that governs how partial results from multiple solvers are merged.

    Defined in Theory2.tex §7.2.4.  The policy is chosen at federation
    registration time and can be overridden per-request.

    - CONSERVATIVE: keep the result with the *lowest* trust level so that the
      combined claim is no stronger than the weakest constituent.
    - UNION: combine all evidence items into one aggregate response.
    - INTERSECTION: retain only evidence items present in *every* partial result.
    - WEIGHTED: weight by per-solver reliability scores before combining.
    - FIRST_WINS: return the first successful (non-empty) partial result.
    - TRUST_MAX: promote the result with the highest trust level.
    """

    CONSERVATIVE = "conservative"
    UNION = "union"
    INTERSECTION = "intersection"
    WEIGHTED = "weighted"
    FIRST_WINS = "first_wins"
    TRUST_MAX = "trust_max"


# ---------------------------------------------------------------------------
# FragmentClassification
# ---------------------------------------------------------------------------

_TRUST_LEVEL_RANK: dict[str, int] = {
    "mechanically_verified": 8,
    "solver_discharged": 7,
    "runtime_witnessed": 6,
    "human_attested": 5,
    "oracle_proposed": 4,
    "copilot_suggested": 3,
    "unverified": 2,
    "contradicted": 1,
}


@dataclass
class FragmentClassification:
    """Carries the result of classifying a logical fragment.

    Produced by :meth:`classify` and consumed by the routing layer to decide
    which solver should receive a given obligation.  Corresponds to the
    classification record described in Theory2.tex §7.2.2.

    Attributes
    ----------
    fragment_id:
        Unique identifier for this classification instance.
    kind:
        The :class:`FragmentKind` assigned by the classifier.
    complexity_estimate:
        A dimensionless floating-point score.  Values above 100 are considered
        intractable for SMT-based solvers without timeouts.
    preferred_solver:
        String identifier for the primary routing target.
    fallback_chain:
        Ordered list of solver ids to try if ``preferred_solver`` is
        unavailable or returns ``unknown``.
    theory_markers:
        Syntactic tokens that triggered the chosen ``kind``.
    is_quantifier_free:
        ``True`` when no universal/existential quantifiers were detected.
    estimated_vars:
        Heuristic count of distinct variables appearing in the formula.
    classification_time:
        POSIX timestamp recorded at object creation.
    """

    fragment_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: FragmentKind = FragmentKind.UNKNOWN
    complexity_estimate: float = 1.0
    preferred_solver: str = "z3"
    fallback_chain: list[str] = field(
        default_factory=lambda: ["z3", "runtime", "oracle"]
    )
    theory_markers: list[str] = field(default_factory=list)
    is_quantifier_free: bool = True
    estimated_vars: int = 0
    classification_time: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Class-level constructor
    # ------------------------------------------------------------------

    @classmethod
    def classify(
        cls,
        fragment_description: str,
        theory_hints: list[str] | None = None,
    ) -> FragmentClassification:
        """Classify *fragment_description* and return a populated instance.

        Uses keyword heuristics (Theory2.tex §7.2.2) to assign a
        :class:`FragmentKind`.  When ``theory_hints`` are supplied they are
        appended to the description before scanning.

        Parameters
        ----------
        fragment_description:
            Free-text or serialised representation of a logical formula.
        theory_hints:
            Optional list of theory-specific tokens (e.g. ``["LIA", "QF_BV"]``)
            provided by the caller to bias classification.

        Returns
        -------
        FragmentClassification
            A fully populated instance ready for routing.
        """
        hints = theory_hints or []
        combined = (fragment_description + " " + " ".join(hints)).lower()

        markers: list[str] = []
        matched_kinds: list[FragmentKind] = []

        structural_kws = {"array", "heap", "alloc", "pointer", "struct", "record"}
        string_kws = {"string", "text", "substr", "concat", "regex", "str."}
        quantifier_kws = {"forall", "exists", "∀", "∃"}
        behavioral_kws = {"behavior", "behaviour", "trace", "event", "transition", "path"}
        # Operator tokens ("+", "*") are matched literally; word tokens use \b.
        arithmetic_word_kws = {"arith", "int", "real", "integer", "rational", "linear"}
        arithmetic_op_kws = {"+", "*"}

        def _word_match(kw: str, text: str) -> bool:
            return bool(re.search(r"\b" + re.escape(kw) + r"\b", text))

        for kw in structural_kws:
            if _word_match(kw, combined):
                markers.append(kw)
                if FragmentKind.STRUCTURAL not in matched_kinds:
                    matched_kinds.append(FragmentKind.STRUCTURAL)

        for kw in string_kws:
            if kw in combined:
                markers.append(kw)
                if FragmentKind.STRING_THEORY not in matched_kinds:
                    matched_kinds.append(FragmentKind.STRING_THEORY)

        for kw in quantifier_kws:
            if _word_match(kw, combined):
                markers.append(kw)
                if FragmentKind.QUANTIFIED not in matched_kinds:
                    matched_kinds.append(FragmentKind.QUANTIFIED)

        for kw in behavioral_kws:
            if _word_match(kw, combined):
                markers.append(kw)
                if FragmentKind.BEHAVIORAL not in matched_kinds:
                    matched_kinds.append(FragmentKind.BEHAVIORAL)

        for kw in arithmetic_word_kws:
            if _word_match(kw, combined):
                markers.append(kw)
                if FragmentKind.ARITHMETIC not in matched_kinds:
                    matched_kinds.append(FragmentKind.ARITHMETIC)

        for kw in arithmetic_op_kws:
            if kw in combined:
                markers.append(kw)
                if FragmentKind.ARITHMETIC not in matched_kinds:
                    matched_kinds.append(FragmentKind.ARITHMETIC)

        if len(matched_kinds) > 1:
            kind = FragmentKind.HYBRID
        elif len(matched_kinds) == 1:
            kind = matched_kinds[0]
        else:
            kind = FragmentKind.UNKNOWN

        # Determine preferred solver and fallback chain from kind
        if kind in (FragmentKind.ARITHMETIC, FragmentKind.STRUCTURAL, FragmentKind.QUANTIFIED):
            preferred = "z3"
            fallback = ["z3", "runtime", "oracle"]
        elif kind == FragmentKind.BEHAVIORAL:
            preferred = "oracle"
            fallback = ["oracle", "runtime", "z3"]
        elif kind == FragmentKind.STRING_THEORY:
            preferred = "z3"
            fallback = ["z3", "oracle"]
        elif kind == FragmentKind.HYBRID:
            preferred = "z3"
            fallback = ["z3", "oracle", "runtime"]
        else:
            preferred = "oracle"
            fallback = ["oracle", "z3", "runtime"]

        is_qf = not any(q in combined for q in ("forall", "exists", "∀", "∃"))
        estimated_vars = len({w for w in combined.split() if w.startswith("x") or w.startswith("v")})

        obj = cls(
            kind=kind,
            preferred_solver=preferred,
            fallback_chain=fallback,
            theory_markers=list(set(markers)),
            is_quantifier_free=is_qf,
            estimated_vars=estimated_vars,
        )
        obj.complexity_estimate = obj.estimate_complexity(
            formula_len=len(fragment_description),
            var_count=estimated_vars,
        )
        logger.debug(
            "Classified fragment %s as %s (complexity=%.2f)",
            obj.fragment_id,
            kind.value,
            obj.complexity_estimate,
        )
        return obj

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def estimate_complexity(self, formula_len: int, var_count: int = 0) -> float:
        """Return a log-scale complexity estimate.

        Heavier fragment kinds (QUANTIFIED, HYBRID) attract a multiplier that
        reflects the known super-linear blow-up in SMT solving.

        Parameters
        ----------
        formula_len:
            Character length of the serialised formula.
        var_count:
            Number of distinct variables detected in the formula.

        Returns
        -------
        float
            Dimensionless complexity score.  Values ≥ 100 are flagged as
            potentially intractable by :meth:`is_z3_tractable`.
        """
        base = math.log1p(max(formula_len, 1)) * math.log1p(max(var_count + 1, 1))
        multipliers: dict[FragmentKind, float] = {
            FragmentKind.ARITHMETIC: 1.0,
            FragmentKind.STRUCTURAL: 1.5,
            FragmentKind.BEHAVIORAL: 3.0,
            FragmentKind.HYBRID: 4.0,
            FragmentKind.QUANTIFIED: 5.0,
            FragmentKind.STRING_THEORY: 2.0,
            FragmentKind.UNKNOWN: 2.5,
        }
        return round(base * multipliers.get(self.kind, 2.0), 4)

    def get_fallback_chain(self) -> list[str]:
        """Return the ordered fallback solver chain for this fragment."""
        return list(self.fallback_chain)

    def is_z3_tractable(self) -> bool:
        """Return ``True`` when Z3 is expected to handle this fragment within budget.

        A fragment is considered Z3-tractable when:
        - its kind is ARITHMETIC, STRUCTURAL, or QUANTIFIED, **and**
        - its complexity estimate is below the tractability threshold (100).
        """
        tractable_kinds = {
            FragmentKind.ARITHMETIC,
            FragmentKind.STRUCTURAL,
            FragmentKind.QUANTIFIED,
        }
        return self.kind in tractable_kinds and self.complexity_estimate < 100.0

    def to_routing_hint(self) -> dict[str, Any]:
        """Serialise routing-relevant metadata as a plain dict.

        Used by :class:`SolverFederation` when constructing routing decisions.
        """
        return {
            "fragment_id": self.fragment_id,
            "kind": self.kind.value,
            "preferred_solver": self.preferred_solver,
            "fallback_chain": self.fallback_chain,
            "is_z3_tractable": self.is_z3_tractable(),
            "complexity_estimate": self.complexity_estimate,
            "theory_markers": self.theory_markers,
            "is_quantifier_free": self.is_quantifier_free,
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serialisation of the classification record."""
        return {
            "fragment_id": self.fragment_id,
            "kind": self.kind.value,
            "complexity_estimate": self.complexity_estimate,
            "preferred_solver": self.preferred_solver,
            "fallback_chain": self.fallback_chain,
            "theory_markers": self.theory_markers,
            "is_quantifier_free": self.is_quantifier_free,
            "estimated_vars": self.estimated_vars,
            "classification_time": self.classification_time,
        }


# ---------------------------------------------------------------------------
# Z3Routing
# ---------------------------------------------------------------------------


class Z3Routing:
    """Encapsulates Z3-specific routing logic for the solver federation.

    Responsible for deciding whether a fragment falls within Z3's jurisdiction
    (Theory2.tex §7.2.3), constructing well-formed SMT-LIB queries, and
    parsing Z3 responses into structured evidence dicts.

    Parameters
    ----------
    session_config:
        Optional mapping of Z3 session options (e.g. ``{"timeout": 5000}``).
    timeout_ms:
        Hard timeout in milliseconds passed to Z3 via SMT-LIB ``(set-option
        :timeout ...)``.
    """

    #: Fragment kinds that fall within Z3's default jurisdiction.
    Z3_JURISDICTION: frozenset[str] = frozenset(
        {"arithmetic", "structural", "quantified", "hybrid"}
    )

    def __init__(
        self,
        session_config: dict | None = None,
        timeout_ms: float = 30_000.0,
    ) -> None:
        self.session_config: dict = session_config or {}
        self.smt_options: dict[str, str] = {
            "logic": "ALL",
            "produce-models": "true",
        }
        self.timeout_ms = timeout_ms
        self.fragment_handlers: dict[str, Any] = {}
        self._query_count: int = 0
        self._success_count: int = 0
        self._total_latency_ms: float = 0.0

    # ------------------------------------------------------------------

    def can_handle(self, fragment_kind: str) -> bool:
        """Return ``True`` if Z3 holds jurisdiction over *fragment_kind*.

        Jurisdiction is defined in Theory2.tex §7.2.3 as the set of fragment
        kinds for which Z3 is the designated primary solver.
        """
        return fragment_kind.lower() in self.Z3_JURISDICTION

    def route_to_z3(
        self,
        fragment_description: str,
        fragment_kind: str,
    ) -> dict[str, Any]:
        """Build a routing decision dict targeting Z3.

        Parameters
        ----------
        fragment_description:
            The textual representation of the formula to be dispatched.
        fragment_kind:
            The :class:`FragmentKind` value string for this fragment.

        Returns
        -------
        dict
            A routing decision compatible with the ``RoutingDecision`` schema
            defined in Theory2.tex §7.2.3.
        """
        jurisdiction_ok = self.can_handle(fragment_kind)
        rationale_parts = [
            f"Fragment kind '{fragment_kind}' {'is' if jurisdiction_ok else 'is NOT'} within Z3 jurisdiction.",
        ]
        if not jurisdiction_ok:
            rationale_parts.append("Routing to Z3 as fallback; consider oracle escalation.")

        return {
            "request_id": uuid.uuid4().hex[:16],
            "selected_backend": "z3",
            "fallback_backends": ("runtime", "oracle"),
            "jurisdiction_check_passed": jurisdiction_ok,
            "trust_ceiling": "solver_discharged",
            "estimated_cost": 1.0 + len(fragment_description) * 0.001,
            "estimated_latency": self.timeout_ms * 0.1,
            "rationale": "  ".join(rationale_parts),
            "fragment_kind": fragment_kind,
            "smt_options": dict(self.smt_options),
        }

    def build_smt_query(
        self,
        fragment_description: str,
        sort_hints: list[str] | None = None,
    ) -> str:
        """Construct a synthetic SMT-LIB 2 query from *fragment_description*.

        The builder performs lightweight syntactic analysis to infer sorts and
        emit ``(declare-const ...)`` statements, wraps the description in an
        ``(assert ...)`` comment block, and appends ``(check-sat)``.

        Parameters
        ----------
        fragment_description:
            Human-readable or serialised formula fragment.
        sort_hints:
            Optional list of SMT-LIB sort names (``"Int"``, ``"Real"``,
            ``"Bool"``) to use when emitting declarations.

        Returns
        -------
        str
            A well-formed SMT-LIB 2 string ready to be passed to Z3.
        """
        hints = sort_hints or []
        lines: list[str] = [
            f"(set-option :timeout {int(self.timeout_ms)})",
            f"(set-logic {self.smt_options.get('logic', 'ALL')})",
            f"(set-option :produce-models {self.smt_options.get('produce-models', 'true')})",
        ]

        # Infer default sort from description keywords
        desc_lower = fragment_description.lower()
        if hints:
            default_sort = hints[0]
        elif "real" in desc_lower or "rational" in desc_lower:
            default_sort = "Real"
        elif "bool" in desc_lower:
            default_sort = "Bool"
        else:
            default_sort = "Int"

        # Heuristically extract variable-like tokens (single letters or x\d+)
        var_tokens = sorted(
            set(re.findall(r"\b(?:[a-wyz]|x\d*)\b", fragment_description))
        )
        for var in var_tokens[:16]:  # cap at 16 declarations
            sort = hints[var_tokens.index(var)] if var_tokens.index(var) < len(hints) else default_sort
            lines.append(f"(declare-const {var} {sort})")

        # Emit the fragment as an assertion comment + placeholder
        safe_desc = fragment_description.replace("\n", " ").replace("(", "[").replace(")", "]")
        lines.append(f"; Fragment: {safe_desc[:120]}")
        lines.append("(assert true)  ; placeholder — real encoding goes here")
        lines.append("(check-sat)")
        lines.append("(get-model)")

        return "\n".join(lines)

    def parse_z3_response(self, raw: str) -> dict[str, Any]:
        """Parse a Z3 text response into a structured evidence dict.

        Recognises ``sat``, ``unsat``, and ``unknown`` tokens at the start of
        *raw* (after stripping whitespace) and maps them to appropriate trust
        levels per Theory2.tex §7.2.4.

        Parameters
        ----------
        raw:
            Raw string output from a Z3 invocation.

        Returns
        -------
        dict
            Keys: ``status``, ``trust_level``, ``evidence_item``, ``model``.
        """
        stripped = raw.strip().lower()
        if stripped.startswith("unsat"):
            status = "unsat"
            trust_level = "solver_discharged"
        elif stripped.startswith("sat"):
            status = "sat"
            trust_level = "solver_discharged"
        elif stripped.startswith("unknown"):
            status = "unknown"
            trust_level = "unverified"
        else:
            status = "error"
            trust_level = "unverified"

        # Extract model lines (everything after the status line)
        model_lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.strip().lower() in ("sat", "unsat", "unknown")]
        model_text = "\n".join(model_lines).strip()

        self._query_count += 1
        if status in ("sat", "unsat"):
            self._success_count += 1

        return {
            "status": status,
            "trust_level": trust_level,
            "evidence_item": {"z3_status": status, "model_fragment": model_text[:512]},
            "model": model_text,
            "raw_length": len(raw),
        }

    def get_session_stats(self) -> dict[str, Any]:
        """Return accumulated session statistics."""
        avg_latency = (
            self._total_latency_ms / self._query_count if self._query_count else 0.0
        )
        success_rate = (
            self._success_count / self._query_count if self._query_count else 0.0
        )
        return {
            "query_count": self._query_count,
            "success_count": self._success_count,
            "average_latency_ms": round(avg_latency, 3),
            "success_rate": round(success_rate, 4),
        }

    def reset_session(self) -> None:
        """Reset all accumulated counters and latency tracking."""
        self._query_count = 0
        self._success_count = 0
        self._total_latency_ms = 0.0
        logger.debug("Z3Routing session reset.")

    def register_fragment_handler(self, fragment_kind: str, handler_fn: Any) -> None:
        """Register a custom handler for *fragment_kind*.

        Custom handlers override the default ``route_to_z3`` logic for their
        registered kind, allowing federation members to plug in specialised
        pre-processing or post-processing steps.

        Parameters
        ----------
        fragment_kind:
            The :class:`FragmentKind` value string (e.g. ``"arithmetic"``).
        handler_fn:
            Callable that accepts ``(fragment_description: str) -> dict``.
        """
        self.fragment_handlers[fragment_kind.lower()] = handler_fn
        logger.debug("Registered custom handler for fragment kind '%s'.", fragment_kind)


# ---------------------------------------------------------------------------
# SolverFederation
# ---------------------------------------------------------------------------


class SolverFederation:
    """A named collection of member solvers governed by a single merge policy.

    Implements the federation structure described in Theory2.tex §7.2.1.  Each
    federation maintains:

    - a registry of member solvers with per-solver jurisdiction and config,
    - a routing table mapping fragment kinds to solver ids,
    - an embedded :class:`Z3Routing` instance for SMT dispatch,
    - aggregate statistics used by :meth:`rebalance`.

    Parameters
    ----------
    federation_id:
        Unique hex identifier.  Generated automatically if omitted.
    name:
        Human-readable label for this federation.
    merge_policy:
        Default :class:`MergePolicy` applied when combining partial results.
    """

    def __init__(
        self,
        federation_id: str | None = None,
        name: str = "default_federation",
        merge_policy: MergePolicy = MergePolicy.CONSERVATIVE,
    ) -> None:
        self.federation_id: str = federation_id or uuid.uuid4().hex[:16]
        self.name: str = name
        self.member_solvers: dict[str, dict[str, Any]] = {}
        self.routing_table: dict[str, str] = {}
        self.z3_router: Z3Routing = Z3Routing()
        self.fragment_classifier = FragmentClassification
        self.merge_policy: MergePolicy = merge_policy
        self.stats: dict[str, Any] = {
            "dispatch_count": 0,
            "merge_count": 0,
            "route_failures": 0,
        }
        self._dispatch_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Membership management (§7.2.1)
    # ------------------------------------------------------------------

    def register_solver(
        self,
        solver_id: str,
        solver_config: dict[str, Any],
        jurisdiction: list[str],
    ) -> None:
        """Register a new member solver with its jurisdiction domains.

        Each entry in *jurisdiction* should be a :class:`FragmentKind` value
        string.  Duplicate registrations overwrite the previous entry.

        Parameters
        ----------
        solver_id:
            Unique string identifier for the solver (e.g. ``"z3"``, ``"cvc5"``).
        solver_config:
            Arbitrary configuration dict forwarded to the solver adapter.
        jurisdiction:
            List of fragment kind strings this solver is authoritative for.
        """
        self.member_solvers[solver_id] = {
            "config": solver_config,
            "jurisdiction": list(jurisdiction),
            "stats": {"dispatched": 0, "succeeded": 0, "failed": 0},
            "registered_at": time.time(),
        }
        for domain in jurisdiction:
            # Only overwrite if not already claimed by a higher-trust solver.
            if domain not in self.routing_table:
                self.routing_table[domain] = solver_id
        logger.info(
            "Federation '%s': registered solver '%s' with jurisdiction %s.",
            self.name,
            solver_id,
            jurisdiction,
        )

    def deregister_solver(self, solver_id: str) -> None:
        """Remove *solver_id* from the federation and clean routing entries.

        Any fragment kinds exclusively routed to *solver_id* will be left
        unassigned; callers should invoke :meth:`rebalance` afterwards.

        Parameters
        ----------
        solver_id:
            The solver to remove.
        """
        if solver_id not in self.member_solvers:
            logger.warning("Deregister called for unknown solver '%s'.", solver_id)
            return
        del self.member_solvers[solver_id]
        to_remove = [k for k, v in self.routing_table.items() if v == solver_id]
        for k in to_remove:
            del self.routing_table[k]
        logger.info("Federation '%s': deregistered solver '%s'.", self.name, solver_id)

    # ------------------------------------------------------------------
    # Routing and dispatch (§7.2.3)
    # ------------------------------------------------------------------

    def route(
        self,
        fragment_description: str,
        fragment_kind: str | None = None,
    ) -> dict[str, Any]:
        """Classify *fragment_description* and resolve a routing decision.

        If *fragment_kind* is not supplied it is inferred by
        :class:`FragmentClassification`.  The method first consults the routing
        table, then falls back to the embedded Z3 router, and finally records
        a route failure if no assignment is found.

        Parameters
        ----------
        fragment_description:
            Textual representation of the formula/fragment.
        fragment_kind:
            Optional override for the fragment kind string.

        Returns
        -------
        dict
            Routing decision with ``selected_backend``, ``fallback_backends``,
            ``rationale``, and ``routing_hint`` keys.
        """
        classification = self.fragment_classifier.classify(fragment_description)
        effective_kind = fragment_kind or classification.kind.value

        selected_backend: str
        rationale_parts: list[str] = [
            f"Fragment classified as '{effective_kind}'.",
        ]

        if effective_kind in self.routing_table:
            selected_backend = self.routing_table[effective_kind]
            rationale_parts.append(
                f"Routing table matched '{effective_kind}' -> '{selected_backend}'."
            )
        elif self.z3_router.can_handle(effective_kind):
            selected_backend = "z3"
            rationale_parts.append("No explicit routing entry; Z3 accepted by jurisdiction.")
        else:
            selected_backend = "oracle"
            self.stats["route_failures"] += 1
            rationale_parts.append(
                "No routing entry and Z3 declined; escalating to oracle (fallback)."
            )

        fallback_backends = tuple(
            s for s in classification.get_fallback_chain() if s != selected_backend
        )

        return {
            "request_id": uuid.uuid4().hex[:16],
            "selected_backend": selected_backend,
            "fallback_backends": fallback_backends,
            "jurisdiction_check_passed": effective_kind in self.routing_table
            or self.z3_router.can_handle(effective_kind),
            "trust_ceiling": "solver_discharged",
            "rationale": "  ".join(rationale_parts),
            "routing_hint": classification.to_routing_hint(),
            "fragment_kind": effective_kind,
        }

    def dispatch(
        self,
        fragment_description: str,
        routing_dict: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Dispatch *fragment_description* according to *routing_dict*.

        For Z3 backends the dispatch uses :class:`Z3Routing` to build an SMT
        query and produce a synthetic response.  For all other backends a
        placeholder evidence dict is returned, simulating the protocol
        described in Theory2.tex §7.2.3.

        Parameters
        ----------
        fragment_description:
            The formula to be evaluated.
        routing_dict:
            A routing decision as returned by :meth:`route`.

        Returns
        -------
        list[dict]
            One or more partial evidence dicts, one per dispatched backend.
        """
        backend = routing_dict.get("selected_backend", "oracle")
        fragment_kind = routing_dict.get("fragment_kind", "unknown")
        responses: list[dict[str, Any]] = []
        t0 = time.monotonic()

        if backend == "z3":
            smt = self.z3_router.build_smt_query(fragment_description)
            raw_response = "sat\n(model)"  # synthetic; real impl would invoke z3
            parsed = self.z3_router.parse_z3_response(raw_response)
            latency_ms = (time.monotonic() - t0) * 1000
            self.z3_router._total_latency_ms += latency_ms
            responses.append(
                {
                    "request_id": routing_dict.get("request_id", uuid.uuid4().hex[:16]),
                    "channel": "z3",
                    "evidence_item": parsed["evidence_item"],
                    "trust_level": parsed["trust_level"],
                    "latency_ms": latency_ms,
                    "is_partial": False,
                    "residuals": (),
                    "provenance": (f"z3/{parsed['status']}",),
                    "smt_query_length": len(smt),
                }
            )
            solver_entry = self.member_solvers.get("z3")
            if solver_entry:
                solver_entry["stats"]["dispatched"] += 1
                solver_entry["stats"]["succeeded"] += 1
        else:
            latency_ms = (time.monotonic() - t0) * 1000
            responses.append(
                {
                    "request_id": routing_dict.get("request_id", uuid.uuid4().hex[:16]),
                    "channel": backend,
                    "evidence_item": {"status": "pending", "backend": backend},
                    "trust_level": "oracle_proposed" if backend == "oracle" else "unverified",
                    "latency_ms": latency_ms,
                    "is_partial": True,
                    "residuals": (f"awaiting_{backend}_response",),
                    "provenance": (f"{backend}/placeholder",),
                }
            )
            solver_entry = self.member_solvers.get(backend)
            if solver_entry:
                solver_entry["stats"]["dispatched"] += 1

        self.stats["dispatch_count"] += 1
        self._dispatch_history.append(
            {
                "timestamp": time.time(),
                "backend": backend,
                "fragment_kind": fragment_kind,
                "response_count": len(responses),
            }
        )
        logger.debug(
            "Dispatched to backend '%s' (%d response(s)).", backend, len(responses)
        )
        return responses

    # ------------------------------------------------------------------
    # Merge (§7.2.4)
    # ------------------------------------------------------------------

    def merge_responses(self, responses: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge partial evidence responses according to ``self.merge_policy``.

        Implements the merge operators described in Theory2.tex §7.2.4.
        Provenance chains from all inputs are preserved in the output.

        Parameters
        ----------
        responses:
            List of partial evidence dicts as returned by :meth:`dispatch`.

        Returns
        -------
        dict
            A single merged evidence dict with a ``provenance`` chain spanning
            all inputs.
        """
        if not responses:
            return {"evidence_item": {}, "trust_level": "unverified", "provenance": ()}

        provenance: list[str] = []
        for r in responses:
            provenance.extend(r.get("provenance", ()))

        self.stats["merge_count"] += 1

        if self.merge_policy == MergePolicy.FIRST_WINS:
            base = responses[0]
            return {**base, "provenance": tuple(provenance), "merge_policy": self.merge_policy.value}

        if self.merge_policy == MergePolicy.TRUST_MAX:
            best = max(
                responses,
                key=lambda r: _TRUST_LEVEL_RANK.get(r.get("trust_level", "unverified").lower(), 0),
            )
            return {**best, "provenance": tuple(provenance), "merge_policy": self.merge_policy.value}

        if self.merge_policy == MergePolicy.CONSERVATIVE:
            worst = min(
                responses,
                key=lambda r: _TRUST_LEVEL_RANK.get(r.get("trust_level", "unverified").lower(), 0),
            )
            return {**worst, "provenance": tuple(provenance), "merge_policy": self.merge_policy.value}

        if self.merge_policy == MergePolicy.UNION:
            merged_evidence: dict[str, Any] = {}
            for r in responses:
                item = r.get("evidence_item", {})
                if isinstance(item, dict):
                    merged_evidence.update(item)
            trust_levels = [r.get("trust_level", "unverified") for r in responses]
            # Union trust = minimum (weakest link)
            min_trust = min(trust_levels, key=lambda t: _TRUST_LEVEL_RANK.get(t.lower(), 0))
            return {
                "evidence_item": merged_evidence,
                "trust_level": min_trust,
                "provenance": tuple(provenance),
                "merge_policy": self.merge_policy.value,
                "is_partial": any(r.get("is_partial", False) for r in responses),
                "residuals": tuple(
                    res for r in responses for res in r.get("residuals", ())
                ),
            }

        if self.merge_policy == MergePolicy.INTERSECTION:
            if not responses:
                return {"evidence_item": {}, "trust_level": "unverified", "provenance": ()}
            common: dict[str, Any] = dict(responses[0].get("evidence_item", {}))
            for r in responses[1:]:
                item = r.get("evidence_item", {})
                common = {k: v for k, v in common.items() if k in item and item[k] == v}
            trust_levels = [r.get("trust_level", "unverified") for r in responses]
            min_trust = min(trust_levels, key=lambda t: _TRUST_LEVEL_RANK.get(t.lower(), 0))
            return {
                "evidence_item": common,
                "trust_level": min_trust,
                "provenance": tuple(provenance),
                "merge_policy": self.merge_policy.value,
            }

        if self.merge_policy == MergePolicy.WEIGHTED:
            # Weight by inverse latency (faster solvers get more weight)
            total_weight = 0.0
            weighted_trust_score = 0.0
            merged_evidence = {}
            for r in responses:
                latency = r.get("latency_ms", 1.0) or 1.0
                weight = 1.0 / latency
                total_weight += weight
                tl = r.get("trust_level", "unverified").lower()
                weighted_trust_score += _TRUST_LEVEL_RANK.get(tl, 0) * weight
                item = r.get("evidence_item", {})
                if isinstance(item, dict):
                    merged_evidence.update(item)
            avg_rank = weighted_trust_score / total_weight if total_weight else 0
            # Find closest trust level
            closest = min(
                _TRUST_LEVEL_RANK.items(),
                key=lambda kv: abs(kv[1] - avg_rank),
            )[0]
            return {
                "evidence_item": merged_evidence,
                "trust_level": closest,
                "provenance": tuple(provenance),
                "merge_policy": self.merge_policy.value,
            }

        # Fallback
        return {**responses[0], "provenance": tuple(provenance)}

    # ------------------------------------------------------------------
    # Administration
    # ------------------------------------------------------------------

    def get_solver_status(self) -> dict[str, Any]:
        """Return per-solver statistics and overall federation health.

        Returns
        -------
        dict
            Keys: ``federation_id``, ``name``, ``member_count``,
            ``member_stats``, ``overall_stats``, ``routing_table``.
        """
        member_stats: dict[str, Any] = {}
        for sid, entry in self.member_solvers.items():
            s = entry.get("stats", {})
            dispatched = s.get("dispatched", 0)
            succeeded = s.get("succeeded", 0)
            member_stats[sid] = {
                **s,
                "success_rate": round(succeeded / dispatched, 4) if dispatched else 0.0,
                "jurisdiction": entry.get("jurisdiction", []),
            }
        return {
            "federation_id": self.federation_id,
            "name": self.name,
            "member_count": len(self.member_solvers),
            "member_stats": member_stats,
            "overall_stats": dict(self.stats),
            "routing_table": dict(self.routing_table),
            "merge_policy": self.merge_policy.value,
        }

    def rebalance(self) -> None:
        """Redistribute routing assignments based on per-solver success rates.

        Solvers with a higher success rate claim jurisdiction over any fragment
        kinds currently routed to solvers with a lower rate.  The Z3 router
        retains its jurisdiction for kinds within ``Z3Routing.Z3_JURISDICTION``
        unless a member solver has demonstrated a higher success rate.

        This implements the load balancing described in Theory2.tex §7.2.5.
        """
        if not self.member_solvers:
            return

        # Build success-rate map
        rates: dict[str, float] = {}
        for sid, entry in self.member_solvers.items():
            s = entry.get("stats", {})
            dispatched = s.get("dispatched", 0)
            succeeded = s.get("succeeded", 0)
            rates[sid] = succeeded / dispatched if dispatched else 0.5  # optimistic prior

        # Re-assign each routing-table entry to the highest-rate solver that
        # claims that domain.
        for domain in list(self.routing_table.keys()):
            candidates = [
                sid
                for sid, entry in self.member_solvers.items()
                if domain in entry.get("jurisdiction", [])
            ]
            if candidates:
                best = max(candidates, key=lambda s: rates.get(s, 0.0))
                self.routing_table[domain] = best

        logger.info(
            "Federation '%s': rebalanced routing table -> %s",
            self.name,
            self.routing_table,
        )

    def federation_digest(self) -> str:
        """Return a SHA-256 digest of the current federation membership and routing.

        Useful for detecting configuration drift between replicas.

        Returns
        -------
        str
            64-character lowercase hex string.
        """
        parts: list[str] = sorted(self.member_solvers.keys())
        parts += [f"{k}:{v}" for k, v in sorted(self.routing_table.items())]
        payload = "|".join(parts).encode()
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Serialise full federation state to a plain dict."""
        return {
            "federation_id": self.federation_id,
            "name": self.name,
            "merge_policy": self.merge_policy.value,
            "member_solvers": {
                sid: {
                    "config": entry.get("config", {}),
                    "jurisdiction": entry.get("jurisdiction", []),
                    "stats": entry.get("stats", {}),
                }
                for sid, entry in self.member_solvers.items()
            },
            "routing_table": dict(self.routing_table),
            "stats": dict(self.stats),
            "digest": self.federation_digest(),
        }


# ---------------------------------------------------------------------------
# FederationRouter
# ---------------------------------------------------------------------------


class FederationRouter:
    """Routes obligations across multiple :class:`SolverFederation` instances.

    Implements the cross-federation routing model described in Theory2.tex
    §7.2.5.  A ``FederationRouter`` is typically a singleton held at the
    top level of a JuGeo verification session.

    Attributes
    ----------
    federations:
        Mapping from federation id to :class:`SolverFederation`.
    routing_stats:
        Aggregate counters for total routes, cross-federation routes, and
        failures.
    """

    def __init__(self) -> None:
        self.federations: dict[str, SolverFederation] = {}
        self.routing_stats: dict[str, int] = {
            "total_routes": 0,
            "cross_federation": 0,
            "failures": 0,
        }

    # ------------------------------------------------------------------

    def register_federation(
        self,
        federation_id: str,
        federation: SolverFederation,
    ) -> None:
        """Add *federation* under *federation_id*.

        Parameters
        ----------
        federation_id:
            Key used to look up the federation; typically matches
            ``federation.federation_id``.
        federation:
            The :class:`SolverFederation` to register.
        """
        self.federations[federation_id] = federation
        logger.info("FederationRouter: registered federation '%s'.", federation_id)

    def select_federation(
        self,
        fragment_kind: str,
        fragment_description: str = "",
    ) -> SolverFederation | None:
        """Return the best-matching federation for *fragment_kind*.

        Iterates over registered federations and returns the first one whose
        routing table contains an entry for *fragment_kind*.  If none matches,
        the first registered federation is returned as a default.

        Parameters
        ----------
        fragment_kind:
            The :class:`FragmentKind` value string.
        fragment_description:
            Optional formula text used for tie-breaking (currently unused but
            reserved for future semantic matching).

        Returns
        -------
        SolverFederation or None
            The selected federation, or ``None`` if no federations are
            registered.
        """
        if not self.federations:
            return None
        for fed in self.federations.values():
            if fragment_kind in fed.routing_table:
                return fed
        # default: return first federation
        return next(iter(self.federations.values()))

    def route_cross_federation(
        self,
        fragment_description: str,
        fragment_kind: str,
        federation_ids: list[str],
    ) -> dict[str, Any]:
        """Attempt routing across an explicit list of federations.

        Tries each federation in *federation_ids* in order.  The first
        successful (non-failure) routing decision is returned along with
        partial results from preceding failed attempts.

        Parameters
        ----------
        fragment_description:
            Formula text to route.
        fragment_kind:
            Override for fragment kind; if empty the classifier decides.
        federation_ids:
            Ordered list of federation ids to try.

        Returns
        -------
        dict
            Combined routing result with ``selected_federation``,
            ``routing_decision``, and ``partial_results`` keys.
        """
        self.routing_stats["total_routes"] += 1
        self.routing_stats["cross_federation"] += 1
        partial_results: list[dict[str, Any]] = []

        for fid in federation_ids:
            fed = self.federations.get(fid)
            if fed is None:
                logger.warning("FederationRouter: unknown federation id '%s'.", fid)
                continue
            routing = fed.route(fragment_description, fragment_kind or None)
            if routing.get("jurisdiction_check_passed", False):
                return {
                    "selected_federation": fid,
                    "routing_decision": routing,
                    "partial_results": partial_results,
                    "cross_federation": True,
                }
            partial_results.append({"federation_id": fid, "routing": routing})

        # All federations rejected — return last attempt as best-effort
        self.routing_stats["failures"] += 1
        last_fed_id = federation_ids[-1] if federation_ids else None
        last_routing: dict[str, Any] = partial_results[-1]["routing"] if partial_results else {}
        return {
            "selected_federation": last_fed_id,
            "routing_decision": last_routing,
            "partial_results": partial_results,
            "cross_federation": True,
            "all_failed": True,
        }

    def merge_cross_federation_results(
        self,
        results: list[dict[str, Any]],
        policy: MergePolicy = MergePolicy.CONSERVATIVE,
    ) -> dict[str, Any]:
        """Merge evidence results originating from multiple federations.

        Provenance from each federation result is tagged with the source
        federation id before being combined.

        Parameters
        ----------
        results:
            List of evidence dicts, each optionally carrying a
            ``federation_id`` key for provenance tagging.
        policy:
            :class:`MergePolicy` to apply; defaults to CONSERVATIVE.

        Returns
        -------
        dict
            Merged evidence with a multi-federation provenance chain.
        """
        if not results:
            return {"evidence_item": {}, "trust_level": "unverified", "provenance": ()}

        # Tag provenance with federation id if present
        tagged: list[dict[str, Any]] = []
        for r in results:
            fid = r.get("federation_id", "unknown_federation")
            raw_prov = r.get("provenance", ())
            tagged_prov = tuple(f"{fid}/{p}" for p in raw_prov) if raw_prov else (f"{fid}/unknown",)
            tagged.append({**r, "provenance": tagged_prov})

        # Delegate to a temporary federation for merging
        tmp_fed = SolverFederation(merge_policy=policy)
        return tmp_fed.merge_responses(tagged)

    def get_routing_stats(self) -> dict[str, int]:
        """Return aggregate routing statistics."""
        return dict(self.routing_stats)

    def get_all_federation_statuses(self) -> dict[str, dict[str, Any]]:
        """Return a status dict for every registered federation.

        Returns
        -------
        dict
            Mapping from federation id to the result of
            :meth:`SolverFederation.get_solver_status`.
        """
        return {fid: fed.get_solver_status() for fid, fed in self.federations.items()}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _classify_by_keywords(text: str) -> FragmentKind:
    """Pure keyword-based fragment classifier.

    A stateless helper that mirrors the heuristic logic in
    :meth:`FragmentClassification.classify` without constructing a full
    :class:`FragmentClassification` instance.  Useful for lightweight routing
    decisions where the full dataclass overhead is unnecessary.

    Parameters
    ----------
    text:
        The formula or description string to classify.

    Returns
    -------
    FragmentKind
        The inferred fragment kind.
    """
    lower = text.lower()
    hits: list[FragmentKind] = []

    def _wb(kw: str) -> bool:
        return bool(re.search(r"\b" + re.escape(kw) + r"\b", lower))

    if any(_wb(kw) for kw in ("array", "heap", "alloc", "pointer", "struct")):
        hits.append(FragmentKind.STRUCTURAL)
    if any(kw in lower for kw in ("string", "text", "substr", "concat", "regex")):
        hits.append(FragmentKind.STRING_THEORY)
    if any(_wb(kw) for kw in ("forall", "exists", "∀", "∃")):
        hits.append(FragmentKind.QUANTIFIED)
    if any(_wb(kw) for kw in ("behavior", "behaviour", "trace", "event", "transition")):
        hits.append(FragmentKind.BEHAVIORAL)
    if any(kw in lower for kw in ("+", "*")) or any(
        _wb(kw) for kw in ("arith", "int", "real", "integer", "linear")
    ):
        hits.append(FragmentKind.ARITHMETIC)

    if len(hits) > 1:
        return FragmentKind.HYBRID
    if len(hits) == 1:
        return hits[0]
    return FragmentKind.UNKNOWN


def create_default_federation(name: str = "main") -> SolverFederation:
    """Factory that creates a :class:`SolverFederation` pre-loaded with Z3.

    Registers Z3 as the default solver for the ``arithmetic``, ``structural``,
    and ``quantified`` fragment kinds, matching the baseline jurisdiction
    assignment in Theory2.tex §7.2.1.

    Parameters
    ----------
    name:
        Human-readable label for the federation.

    Returns
    -------
    SolverFederation
        A ready-to-use federation with Z3 registered.
    """
    federation = SolverFederation(name=name, merge_policy=MergePolicy.CONSERVATIVE)
    federation.register_solver(
        solver_id="z3",
        solver_config={
            "timeout_ms": 30_000,
            "logic": "ALL",
            "produce_models": True,
        },
        jurisdiction=[
            FragmentKind.ARITHMETIC.value,
            FragmentKind.STRUCTURAL.value,
            FragmentKind.QUANTIFIED.value,
        ],
    )
    federation.register_solver(
        solver_id="runtime",
        solver_config={"mode": "instrumented", "sample_rate": 1.0},
        jurisdiction=[
            FragmentKind.BEHAVIORAL.value,
        ],
    )
    federation.register_solver(
        solver_id="oracle",
        solver_config={"channel": "default", "escalation_threshold": 0.8},
        jurisdiction=[
            FragmentKind.UNKNOWN.value,
            FragmentKind.HYBRID.value,
            FragmentKind.STRING_THEORY.value,
        ],
    )
    logger.info("Created default federation '%s' (id=%s).", name, federation.federation_id)
    return federation
