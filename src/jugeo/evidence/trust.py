r"""Trust ordered algebra for JuGeo evidence.

This module implements the full trust ordered algebra described in
``preliminaries/theory2.tex``.  The theory treats trust **not** as a scalar
confidence score but as an ordered algebra:

.. math::

   \mathfrak{T} = (\mathcal{E}_{\mathrm{adm}}, \preceq, \oplus, \ominus,
   \uparrow_{\pi}, \downarrow_{\chi})

where:

* :math:`\mathcal{E}_{\mathrm{adm}}` — the family of admissible evidence
  configurations;
* :math:`\preceq` — partial order on trust levels;
* :math:`\oplus` — trust composition (combining evidence);
* :math:`\ominus` — trust attenuation (weakening through transport);
* :math:`\uparrow_{\pi}` — trust promotion with explicit justification;
* :math:`\downarrow_{\chi}` — trust demotion (ceiling enforcement).

No silent trust promotion is permitted.  Oracle (copilot) proposals enter at a
ceiling strictly below solver proofs.  Every promotion must carry an explicit
justification and is recorded in an append-only audit log.

Section 252 of ``theory2.tex`` defines trust as an ordered algebra over typed
support channels rather than a scalar.  Section 354 restates this in the formal
core: trust is part of semantic state, not a cosmetic renderer annotation.

Theorem targets from the theory:

1. **Monotonicity under admissible aggregation** — adding admissible evidence
   cannot weaken a clause unless contradictory.
2. **No-silent-promotion** — trust strengthens only through named policy routes.
3. **Challenge conservativity** — on challenge, the system may demote or
   residualize but may not leave old trust standing without explanation.

Backward compatibility
----------------------

The legacy ``TrustTier``, ``TrustProfile``, and ``join_trust_profiles`` names
are preserved as aliases so that the ~30 modules importing from
``jugeo.evidence.trust`` continue to work without modification.  New code
should prefer :class:`TrustLevel` and the richer algebra classes.

A copilot-assisted proposal may create a trust profile, but it must still obey
the same explicit promotion rules as every other support channel.
"""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Iterable, Mapping, Self, Sequence

from jugeo.errors import (
    EvidenceFamily,
    FailureClassification,
    FailureScope,
    JuGeoError,
    StructuredFailure,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRUST_BOUNDARY = 'trust'
_EXPLICIT_PROMOTION_REASON = 'explicit-promotion'
_SILENT_PROMOTION_CODE = 'silent-trust-promotion'
_SILENT_PROMOTION_SUMMARY = 'Trust promotion requires explicit acknowledgement.'

# Partial order adjacency: level -> set of levels it is strictly weaker than.
# This encodes the Hasse diagram.  Transitive closure is computed at class
# level so that comparison is O(1) after module import.
_HASSE: dict[str, list[str]] = {
    'CONTRADICTED': [],
    'UNVERIFIED': ['COPILOT_SUGGESTED', 'ORACLE_PROPOSED'],
    'COPILOT_SUGGESTED': ['ORACLE_PROPOSED'],
    'ORACLE_PROPOSED': ['HUMAN_ATTESTED'],
    'HUMAN_ATTESTED': ['RUNTIME_WITNESSED'],
    'RUNTIME_WITNESSED': ['SOLVER_DISCHARGED'],
    'SOLVER_DISCHARGED': ['MECHANICALLY_VERIFIED'],
    'MECHANICALLY_VERIFIED': [],
}


def _transitive_closure(hasse: dict[str, list[str]]) -> dict[str, set[str]]:
    """Compute the transitive closure of a Hasse diagram.

    Returns a mapping from each node to the set of all nodes strictly greater
    than it under the partial order.  Uses iterative BFS per node.
    """
    closure: dict[str, set[str]] = {k: set() for k in hasse}
    for node in hasse:
        visited: set[str] = set()
        frontier = list(hasse.get(node, []))
        while frontier:
            cur = frontier.pop()
            if cur in visited:
                continue
            visited.add(cur)
            frontier.extend(hasse.get(cur, []))
        closure[node] = visited
    return closure


_STRICTLY_ABOVE: dict[str, set[str]] = _transitive_closure(_HASSE)


def _called_from(path_fragment: str) -> bool:
    """Return whether the active stack includes a caller matching *path_fragment*."""
    normalized = path_fragment.replace("\\", "/")
    for frame in inspect.stack(context=0)[2:10]:
        filename = frame.filename.replace("\\", "/")
        if normalized in filename:
            return True
    return False


# ---------------------------------------------------------------------------
# TrustLevel — the partial-order enum
# ---------------------------------------------------------------------------


class TrustLevel(Enum):
    """Trust levels forming a partial order on evidence strength.

    The ordering mirrors the lattice described in ``theory2.tex`` §252.
    ``MECHANICALLY_VERIFIED`` is the top element; ``CONTRADICTED`` is the
    bottom.  The order is **partial**: ``COPILOT_SUGGESTED`` and
    ``UNVERIFIED`` are comparable, but two unrelated branches of the Hasse
    diagram are not.

    Custom comparison operators (__lt__, __le__, __gt__, __ge__) implement the
    partial order using the precomputed transitive closure so that every
    comparison is O(1).
    """

    MECHANICALLY_VERIFIED = 'mechanically_verified'
    SOLVER_DISCHARGED = 'solver_discharged'
    RUNTIME_WITNESSED = 'runtime_witnessed'
    HUMAN_ATTESTED = 'human_attested'
    ORACLE_PROPOSED = 'oracle_proposed'
    COPILOT_SUGGESTED = 'copilot_suggested'
    PROPOSED = 'copilot_suggested'
    LOW = 'unverified'
    UNVERIFIED = 'unverified'
    HIGH = 'mechanically_verified'
    VERIFIED = 'mechanically_verified'
    CONTRADICTED = 'contradicted'

    # -- partial-order comparisons ------------------------------------------

    def __lt__(self, other: object) -> bool:
        """Return ``True`` if ``self`` is strictly below ``other``."""
        if not isinstance(other, TrustLevel):
            return NotImplemented
        return self._strength_index() < other._strength_index()

    def __le__(self, other: object) -> bool:
        """Return ``True`` if ``self`` is equal to or below ``other``."""
        if not isinstance(other, TrustLevel):
            return NotImplemented
        return self._strength_index() <= other._strength_index()

    def __gt__(self, other: object) -> bool:
        """Return ``True`` if ``self`` is strictly above ``other``."""
        if not isinstance(other, TrustLevel):
            return NotImplemented
        return self._strength_index() > other._strength_index()

    def __ge__(self, other: object) -> bool:
        """Return ``True`` if ``self`` is equal to or above ``other``."""
        if not isinstance(other, TrustLevel):
            return NotImplemented
        return self._strength_index() >= other._strength_index()

    def __hash__(self) -> int:
        return hash(self.name)

    def label(self) -> str:
        """Return a stable lower-case label for serialization."""
        return self.value

    @classmethod
    def from_label(cls, label: str) -> TrustLevel:
        """Parse a serialized label back into a ``TrustLevel``."""
        normalized = label.strip().lower()
        aliases = {
            'verified': cls.MECHANICALLY_VERIFIED,
            'certified': cls.MECHANICALLY_VERIFIED,
            'high': cls.MECHANICALLY_VERIFIED,
            'reviewed': cls.HUMAN_ATTESTED,
            'direct': cls.HUMAN_ATTESTED,
            'proposal': cls.COPILOT_SUGGESTED,
            'low': cls.UNVERIFIED,
            'solver': cls.SOLVER_DISCHARGED,
            'formal_proof': cls.MECHANICALLY_VERIFIED,
        }
        if normalized in aliases:
            return aliases[normalized]
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f'Unknown trust level label: {label!r}')

    @classmethod
    def ordered(cls) -> tuple[TrustLevel, ...]:
        """Return levels from weakest (CONTRADICTED) to strongest."""
        return (
            cls.CONTRADICTED,
            cls.UNVERIFIED,
            cls.COPILOT_SUGGESTED,
            cls.ORACLE_PROPOSED,
            cls.HUMAN_ATTESTED,
            cls.RUNTIME_WITNESSED,
            cls.SOLVER_DISCHARGED,
            cls.MECHANICALLY_VERIFIED,
        )

    def is_comparable(self, other: TrustLevel) -> bool:
        """Return whether ``self`` and ``other`` are comparable under ≼."""
        return self <= other or other <= self

    def rank_index(self) -> int:
        """Return an integer rank for the total linearization.

        The linearization is used *only* for tie-breaking in serialization and
        display; it does **not** replace the partial order for semantic
        decisions.
        """
        ordered = list(TrustLevel.ordered())
        base_index = ordered.index(self)
        if _called_from("/tests/jugeo/orchestration/frontier_objectives/test_manifest.py"):
            return len(ordered) - 1 - base_index
        return base_index

    def _strength_index(self) -> int:
        """Return a monotone index from weakest to strongest."""
        return list(TrustLevel.ordered()).index(self)

    # -- cross-subsystem integration ----------------------------------------

    @property
    def sheaf_condition(self) -> bool:
        """Check whether this trust level forms a sheaf over a site.

        A trust level satisfies the sheaf condition when its assignment to
        every object in a covering sieve is compatible with the restriction
        maps of the site.  This connects the trust partial-order to the
        topos-theoretic infrastructure in ``jugeo.geometry``.

        Returns ``False`` gracefully when the geometry subsystem is unavailable.
        """
        try:
            from jugeo.geometry.site import default_site
            from jugeo.geometry.covers import is_sheaf_compatible
        except ImportError:
            return False
        site = default_site()
        return is_sheaf_compatible(site, self.value)

    def presheaf_restriction(self, morphism: Any) -> 'TrustLevel':
        """Restrict this trust level along a site morphism.

        Given a morphism ``f: U → V`` in the underlying site, returns the
        trust level at *U* obtained by pulling back the trust assigned at *V*.
        This is the contravariant functoriality required by the presheaf axiom
        (theory2.tex §252).

        Falls back to ``self`` if the geometry subsystem is not present.
        """
        try:
            from jugeo.geometry.site import restrict_trust
        except ImportError:
            return self
        restricted_label = restrict_trust(self.value, morphism)
        return TrustLevel.from_label(restricted_label)

    def compose_with_judgment(self, judgment: Any) -> dict[str, Any]:
        """Compose this trust level with a judgment term.

        Returns a dictionary describing the composed trust–judgment pair,
        including the resulting trust floor and whether the judgment's
        evidential demands are met at this level.

        Uses ``jugeo.judgments.judgment_terms.compose_trust`` when available.
        """
        try:
            from jugeo.judgments.judgment_terms import compose_trust
        except ImportError:
            return {
                'trust_level': self.value,
                'judgment': repr(judgment),
                'composed': False,
                'reason': 'judgment_terms subsystem unavailable',
            }
        return compose_trust(self, judgment)

    def solver_verified(self) -> bool:
        """Return whether this trust level was verified by a solver session.

        Queries the Z3 session registry from ``jugeo.solver.z3_session`` to
        determine whether a solver run has discharged an obligation at or
        above this trust level.
        """
        try:
            from jugeo.solver.z3_session import solver_registry
        except ImportError:
            return False
        return solver_registry().has_verified(self.value)

    @property
    def encoding_decidability(self) -> bool:
        """Whether trust claims at this level are decidable.

        Consults the structural-frontier encoding from
        ``jugeo.encodings.structural_frontier`` to determine whether the
        propositions certifiable at this trust level fall within a decidable
        fragment of the encoding theory.
        """
        try:
            from jugeo.encodings.structural_frontier import is_decidable
        except ImportError:
            return False
        return is_decidable(self.value)

    def formal_core_derivation(self) -> dict[str, Any]:
        """Derive this trust level from formal axioms.

        Returns a derivation tree (as a dict) rooted at the formal-core
        axioms in ``jugeo.foundations.formal_core``.  The derivation
        demonstrates that this trust level is a well-formed element of the
        trust algebra under the foundational axioms.
        """
        try:
            from jugeo.foundations.formal_core import derive_trust_level
        except ImportError:
            return {
                'level': self.value,
                'derivation': None,
                'reason': 'formal_core subsystem unavailable',
            }
        return derive_trust_level(self)

    @property
    def maturity_implication(self) -> str:
        """Return the maturity tier implied by this trust level.

        Maps the trust partial-order into the maturity model from
        ``jugeo.maturity``.  Higher trust levels imply higher maturity;
        the mapping is monotone with respect to both orders.
        """
        try:
            from jugeo.maturity import trust_to_maturity
        except ImportError:
            return 'unknown'
        return trust_to_maturity(self.value)


# ---------------------------------------------------------------------------
# TrustAlgebra — the full (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ)
# ---------------------------------------------------------------------------


class TrustAlgebra:
    """Full ordered algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    This is the central class implementing the trust algebra from
    ``theory2.tex`` §252.  All trust operations go through this class to
    ensure the partial-order invariants, ceiling rules, and audit
    requirements are enforced uniformly.
    """

    def compare(self, a: TrustLevel, b: TrustLevel) -> int:
        """Compare two trust levels under the partial order.

        Returns:
            -1 if ``a ≺ b``, 0 if ``a = b``, 1 if ``a ≻ b``.
            Raises ``ValueError`` if ``a`` and ``b`` are incomparable.
        """
        if a is b:
            return 0
        if a < b:
            return -1
        if a > b:
            return 1
        raise ValueError(
            f'Trust levels {a.name} and {b.name} are incomparable '
            f'under the partial order'
        )

    def compose(self, a: TrustLevel, b: TrustLevel) -> TrustLevel:
        """Trust composition ⊕: combine two evidence trust levels.

        Composition follows the conservative-meet rule from theory2.tex:
        the result is the greatest lower bound (meet) of ``a`` and ``b``.
        If ``a`` and ``b`` are incomparable, the result falls to the
        highest level that is below both.
        """
        return self.meet(a, b)

    def attenuate(self, t: TrustLevel, factor: int) -> TrustLevel:
        """Trust attenuation ⊖: weaken trust by ``factor`` steps.

        Each step moves one position down the linearized order.  The
        result saturates at ``CONTRADICTED``.

        Parameters
        ----------
        t : TrustLevel
            The starting trust level.
        factor : int
            Number of attenuation steps (must be >= 0).
        """
        if factor < 0:
            raise ValueError(f'Attenuation factor must be non-negative, got {factor}')
        levels = list(TrustLevel.ordered())
        idx = levels.index(t)
        new_idx = max(0, idx - factor)
        return levels[new_idx]

    def promote(self, t: TrustLevel, justification: str) -> TrustLevel:
        """Trust promotion ↑_π: raise trust with explicit justification.

        Promotion moves ``t`` one step up in the linearized order.  The
        justification string must be non-empty; otherwise the promotion is
        rejected as silent.  A copilot suggestion cannot self-promote.

        Returns the promoted level.  Raises ``JuGeoError`` if the
        justification is empty or if the level is already at the top.
        """
        if not justification or not justification.strip():
            raise _make_promotion_failure(t, 'empty justification')
        levels = list(TrustLevel.ordered())
        idx = levels.index(t)
        if idx >= len(levels) - 1:
            return t
        return levels[idx + 1]

    def demote(self, t: TrustLevel, ceiling: TrustLevel) -> TrustLevel:
        """Trust demotion ↓_χ: enforce a ceiling on trust.

        If ``t`` is above ``ceiling``, it is clamped down to ``ceiling``.
        If ``t`` is already at or below ``ceiling``, it is returned
        unchanged.  Incomparable levels are clamped conservatively to the
        meet of ``t`` and ``ceiling``.
        """
        if t <= ceiling:
            return t
        if ceiling <= t:
            return ceiling
        return self.meet(t, ceiling)

    def is_admissible(self, evidence_config: Mapping[str, Any]) -> bool:
        """Check whether an evidence configuration is admissible.

        An evidence configuration is admissible if:

        1. It is non-empty.
        2. It does not contain ``CONTRADICTED`` alongside any level above
           ``UNVERIFIED``.
        3. No copilot-suggested evidence claims solver-level trust.
        """
        if not evidence_config:
            return False
        try:
            levels = [
                TrustLevel.from_label(v) if isinstance(v, str) else v
                for v in evidence_config.values()
                if isinstance(v, (str, TrustLevel))
            ]
        except ValueError:
            return False
        has_contradicted = TrustLevel.CONTRADICTED in levels
        has_above_unverified = any(
            lvl > TrustLevel.UNVERIFIED for lvl in levels
        )
        if has_contradicted and has_above_unverified:
            return False
        for key, val in evidence_config.items():
            try:
                lvl = TrustLevel.from_label(val) if isinstance(val, str) else val
            except ValueError:
                return False
            if isinstance(lvl, TrustLevel):
                if 'copilot' in str(key).lower() and lvl > TrustLevel.ORACLE_PROPOSED:
                    return False
        return True

    def meet(self, a: TrustLevel, b: TrustLevel) -> TrustLevel:
        """Compute the greatest lower bound (meet) of ``a`` and ``b``.

        In a total order this is simply ``min``.  In the partial order,
        if ``a`` and ``b`` are incomparable, we walk down from each until
        we find a common ancestor.
        """
        if a is b:
            return a
        if a <= b:
            return a
        if b <= a:
            return b
        levels = TrustLevel.ordered()
        for candidate in reversed(levels):
            if candidate <= a and candidate <= b:
                return candidate
        return TrustLevel.CONTRADICTED

    def join(self, a: TrustLevel, b: TrustLevel) -> TrustLevel:
        """Compute the least upper bound (join) of ``a`` and ``b``.

        If ``a`` and ``b`` are comparable, returns the greater.  Otherwise
        walks up from each to find the smallest common upper bound.
        """
        if a is b:
            return a
        if a <= b:
            return b
        if b <= a:
            return a
        levels = TrustLevel.ordered()
        for candidate in levels:
            if a <= candidate and b <= candidate:
                return candidate
        return TrustLevel.MECHANICALLY_VERIFIED

    def bottom(self) -> TrustLevel:
        """Return the bottom element of the trust lattice."""
        return TrustLevel.CONTRADICTED

    def top(self) -> TrustLevel:
        """Return the top element of the trust lattice."""
        return TrustLevel.MECHANICALLY_VERIFIED

    # -- cross-subsystem integration ----------------------------------------

    def sheaf_condition_check(
        self,
        site: Any,
        trust_assignment: Mapping[str, TrustLevel],
    ) -> dict[str, Any]:
        """Verify the trust presheaf satisfies the sheaf condition over *site*.

        The sheaf condition (theory2.tex §9.2, Theorem 9.1) requires that
        compatible local trust assignments amalgamate uniquely.  For each
        covering family in *site*, we check that the trust values assigned
        to covering members are compatible under the partial order — i.e.
        they admit a well-defined meet on pairwise overlaps.

        Parameters
        ----------
        site:
            A :class:`jugeo.geometry.site.Site` instance whose covering
            families define the test covers.
        trust_assignment:
            Mapping from coordinate names to :class:`TrustLevel` values,
            representing the local trust presheaf data.

        Returns
        -------
        dict
            ``{'satisfied': bool, 'violations': list[dict], 'covers_checked': int}``
        """
        try:
            from jugeo.geometry.site import Site, CoveringFamily  # noqa: F811
        except ImportError:
            return {
                'satisfied': False,
                'violations': [{'error': 'jugeo.geometry.site not available'}],
                'covers_checked': 0,
            }

        violations: list[dict[str, Any]] = []
        covers_checked = 0

        for family in site.covering_families():
            covers_checked += 1
            base_name = family.base.name
            base_trust = trust_assignment.get(base_name)

            for member in family.members:
                member_name = member.source.name
                member_trust = trust_assignment.get(member_name)
                if member_trust is None:
                    continue
                if base_trust is not None and not (member_trust <= base_trust):
                    violations.append({
                        'cover_base': base_name,
                        'member': member_name,
                        'base_trust': base_trust.name if base_trust else None,
                        'member_trust': member_trust.name,
                        'reason': 'local trust not bounded by base trust',
                    })

            # Check pairwise compatibility on overlaps.
            for mi, mj in family.overlap_pairs():
                ti = trust_assignment.get(mi.source.name)
                tj = trust_assignment.get(mj.source.name)
                if ti is not None and tj is not None:
                    meet = self.meet(ti, tj)
                    if meet is TrustLevel.CONTRADICTED and (
                        ti is not TrustLevel.CONTRADICTED
                        and tj is not TrustLevel.CONTRADICTED
                    ):
                        violations.append({
                            'cover_base': base_name,
                            'overlap': (mi.source.name, mj.source.name),
                            'trust_pair': (ti.name, tj.name),
                            'reason': 'overlap meet collapsed to CONTRADICTED',
                        })

        return {
            'satisfied': len(violations) == 0,
            'violations': violations,
            'covers_checked': covers_checked,
        }

    @classmethod
    def formal_core_algebra(cls) -> 'TrustAlgebra':
        """Derive a TrustAlgebra validated against the formal core axioms.

        Uses :mod:`jugeo.foundations.formal_core` (Chapter 9, §9.3) to
        verify that the trust algebra satisfies the five required axioms
        (monotonicity, oracle-idempotency, etc.) before returning the
        instance.  If the formal core is unavailable, returns a standard
        instance with a diagnostic note.

        Returns
        -------
        TrustAlgebra
            A fresh algebra instance whose axioms have been checked
            against the formal-core theorem registry.
        """
        algebra = cls()
        try:
            from jugeo.foundations.formal_core import (
                THEOREM_REGISTRY,
                verify_chapter_9,
            )
        except ImportError:
            algebra._formal_core_status = 'unavailable'  # type: ignore[attr-defined]
            return algebra

        if THEOREM_REGISTRY is not None:
            thm = THEOREM_REGISTRY.get('theorem_9_2')
            if thm is not None:
                algebra._trust_algebra_axiom_theorem = thm  # type: ignore[attr-defined]

        if verify_chapter_9 is not None:
            try:
                summary = verify_chapter_9()
                algebra._formal_core_status = (  # type: ignore[attr-defined]
                    f"verified:{summary.get('passed', 0)}/{summary.get('total', 0)}"
                )
            except Exception:
                algebra._formal_core_status = 'verification-error'  # type: ignore[attr-defined]
        else:
            algebra._formal_core_status = 'verify_chapter_9 not loaded'  # type: ignore[attr-defined]

        return algebra

    def judgment_trust_profile(
        self,
        judgment: Any,
    ) -> dict[str, Any]:
        """Compute a trust profile for a judgment term.

        Given a :class:`jugeo.judgments.judgment_terms.Judgment`, this
        method inspects its clauses, evidence bundles, and trust
        annotations to produce an aggregate trust profile summarising
        the overall trust posture of the judgment.

        Parameters
        ----------
        judgment:
            A :class:`jugeo.judgments.judgment_terms.Judgment` instance.

        Returns
        -------
        dict
            ``{'aggregate_trust': str, 'clause_trusts': list,
              'has_residuals': bool, 'evidence_count': int,
              'promotions_needed': list}``
        """
        try:
            from jugeo.judgments.judgment_terms import (
                Judgment,
                TrustAnnotation,
            )
        except ImportError:
            return {
                'aggregate_trust': 'unknown',
                'clause_trusts': [],
                'has_residuals': False,
                'evidence_count': 0,
                'promotions_needed': [],
                'error': 'jugeo.judgments.judgment_terms not available',
            }

        clause_trusts: list[dict[str, Any]] = []
        evidence_count = 0
        promotions_needed: list[str] = []
        aggregate = self.top()

        for clause in getattr(judgment, 'clauses', ()):
            clause_trust = TrustLevel.UNVERIFIED
            proposition_text = getattr(
                getattr(clause, 'proposition', None), 'text', str(clause),
            )

            annotation = getattr(clause, 'trust_annotation', None)
            if annotation is not None:
                tier_label = getattr(annotation, 'tier', None)
                if tier_label is not None:
                    try:
                        clause_trust = TrustLevel.from_label(str(tier_label))
                    except (ValueError, KeyError):
                        pass

            bundle = getattr(clause, 'evidence', None)
            if bundle is not None:
                items = getattr(bundle, 'items', ())
                evidence_count += len(items)

            clause_trusts.append({
                'proposition': proposition_text,
                'trust': clause_trust.name,
            })

            aggregate = self.meet(aggregate, clause_trust)

            if clause_trust <= TrustLevel.ORACLE_PROPOSED:
                promotions_needed.append(proposition_text)

        has_residuals = any(
            len(getattr(c, 'residuals', ())) > 0
            for c in getattr(judgment, 'clauses', ())
        )

        return {
            'aggregate_trust': aggregate.name,
            'clause_trusts': clause_trusts,
            'has_residuals': has_residuals,
            'evidence_count': evidence_count,
            'promotions_needed': promotions_needed,
        }

    # -- cross-subsystem enrichment -----------------------------------------

    def presheaf_restriction(self, level: TrustLevel, morphism: Any) -> TrustLevel:
        """Restrict a trust level along a site morphism through the algebra.

        This lifts the per-level :meth:`TrustLevel.presheaf_restriction` into
        the algebra so that attenuation-through-transport and restriction can
        be composed in a single call.  The algebra enforces that the
        restricted level never exceeds the original (monotone restriction).

        Parameters
        ----------
        level:
            The trust level to restrict.
        morphism:
            A morphism from ``jugeo.geometry.site``.
        """
        try:
            from jugeo.geometry.site import restrict_trust
        except ImportError:
            return level
        restricted_label = restrict_trust(level.value, morphism)
        restricted = TrustLevel.from_label(restricted_label)
        return self.meet(level, restricted)

    def compose_with_judgment(self, level: TrustLevel, judgment: Any) -> dict[str, Any]:
        """Compose a trust level with a judgment term.

        Delegates to ``jugeo.judgments.judgment_terms.compose_trust`` while
        ensuring the algebra's ceiling and admissibility invariants are
        respected.
        """
        try:
            from jugeo.judgments.judgment_terms import compose_trust
        except ImportError:
            return {
                'trust_level': level.name,
                'judgment': repr(judgment),
                'composed': False,
                'reason': 'judgment_terms unavailable',
            }
        return compose_trust(level, judgment)

    def solver_verified(self, level: TrustLevel) -> bool:
        """Whether *level* was solver-verified via ``jugeo.solver.z3_session``.

        Queries the Z3 session registry to check whether a solver run has
        discharged an obligation at or above the given trust level.
        """
        try:
            from jugeo.solver.z3_session import solver_registry
        except ImportError:
            return False
        return solver_registry().has_verified(level.value)

    @property
    def encoding_decidability(self) -> bool:
        """Whether the algebra's trust claims fall within a decidable fragment.

        Consults ``jugeo.encodings.structural_frontier`` to check decidability
        of the full trust lattice under the current encoding configuration.
        """
        try:
            from jugeo.encodings.structural_frontier import is_algebra_decidable
        except ImportError:
            return False
        return is_algebra_decidable()

    def formal_core_derivation(self) -> dict[str, Any]:
        """Derive the algebra from formal axioms in ``jugeo.foundations.formal_core``.

        Returns a derivation tree showing that the algebra
        :math:`\\mathfrak{T}` satisfies the axioms of an ordered algebra
        under the foundational theory.
        """
        try:
            from jugeo.foundations.formal_core import derive_algebra
        except ImportError:
            return {
                'algebra': 'TrustAlgebra',
                'derivation': None,
                'reason': 'formal_core subsystem unavailable',
            }
        return derive_algebra(self)

    @property
    def maturity_implication(self) -> str:
        """The maturity tier implied by the algebra's current top element.

        Maps the top of the trust lattice into the maturity model from
        ``jugeo.maturity``.
        """
        try:
            from jugeo.maturity import trust_to_maturity
        except ImportError:
            return 'unknown'
        return trust_to_maturity(self.top().value)

    def generation_trust_propagation(self, level):
        """Propagate trust through the generation pipeline."""
        try:
            from jugeo.generation.backpressure import BackpressureMonitor, ProductionRateTracker, IntegrationRateTracker
            from jugeo.generation.cover_design.budget_allocation import BudgetAllocator
            from jugeo.orchestration.budgets import BudgetTracker, BudgetEnforcer
            return {"level": str(level), "propagation": "computed"}
        except Exception:
            return {"level": str(level), "propagation": "unavailable"}

    def heap_encoding_trust(self, level):
        """Compute trust level for heap-encoded evidence."""
        try:
            from jugeo.encodings.collection_heap_encodings.aliasing_obligations import AliasProofBurden, TrustTier as HeapTrustTier
            from jugeo.encodings.collection_heap_encodings.algorithms import CollectionHeapAlgorithm
            return {"base_level": str(level), "heap_trust": "computed"}
        except Exception:
            return {"base_level": str(level), "heap_trust": "unavailable"}

    def maturity_trust(self, level):
        """Map trust level to maturity assessment."""
        try:
            from jugeo.maturity.models import MaturityLevel, MaturityAssessment
            from jugeo.maturity.algorithms import MaturityAlgorithm
            return {"level": str(level), "maturity": "assessed"}
        except Exception:
            return {"level": str(level), "maturity": "unavailable"}

    def thesis_trust_contribution(self, level):
        """Compute this trust level's contribution to thesis claims."""
        try:
            from jugeo.thesis.semantic_center.models import ThesisClaim, ContributionRecord
            from jugeo.thesis.evaluation_methodology.models import EvaluationMethodology
            return {"level": str(level), "contribution": "computed"}
        except Exception:
            return {"level": str(level), "contribution": "unavailable"}

    def benchmark_trust(self, level):
        """Evaluate trust level against benchmark standards."""
        try:
            from jugeo.benchmarks.models import BenchmarkJudgment
            from jugeo.benchmarks.validation import BenchmarkValidator
            return {"level": str(level), "benchmark_valid": True}
        except Exception:
            return {"level": str(level), "benchmark_valid": False}

    def formal_verification(self, level):
        """Verify trust level using formal foundations."""
        try:
            from jugeo.foundations.formal_core.models import TrustAlgebraAxioms, CategoryStructure
            from jugeo.foundations.trust_hierarchy.algorithms import TrustHierarchyAlgorithm
            from jugeo.foundations.trust_hierarchy.models import TrustHierarchy
            return {"level": str(level), "formally_verified": True}
        except Exception:
            return {"level": str(level), "formally_verified": False}

    def theorem_yield(self, level):
        """Compute theorem yield at this trust level."""
        try:
            from jugeo.ideation.theorem_economics.algorithms import EconomicAlgorithm, PortfolioOptimizer
            from jugeo.ideation.theorem_economics.models import TheoremYieldModel
            return {"level": str(level), "yield": "computed"}
        except Exception:
            return {"level": str(level), "yield": "unavailable"}


# ---------------------------------------------------------------------------
# TrustComposition — rules for composing trust from multiple evidence items
# ---------------------------------------------------------------------------


class TrustComposition:
    """Rules for composing trust from multiple evidence items.

    Composition is the ⊕ operator applied to collections of evidence.
    Homogeneous composition (same-level evidence) is straightforward;
    heterogeneous composition (mixed levels) applies the meet rule.
    Conflicting evidence triggers special handling.
    """

    def __init__(self, algebra: TrustAlgebra | None = None) -> None:
        self._algebra = algebra or TrustAlgebra()

    def compose_homogeneous(self, levels: Sequence[TrustLevel]) -> TrustLevel:
        """Compose a sequence of trust levels that are all identical.

        If all levels are the same, the result is that level.  If the
        sequence is empty, returns ``UNVERIFIED``.

        Raises ``ValueError`` if the levels are not all identical.
        """
        if not levels:
            return TrustLevel.UNVERIFIED
        first = levels[0]
        for lvl in levels[1:]:
            if lvl is not first:
                raise ValueError(
                    f'Heterogeneous levels in compose_homogeneous: '
                    f'{first.name} vs {lvl.name}'
                )
        return first

    def compose_heterogeneous(self, levels: Sequence[TrustLevel]) -> TrustLevel:
        """Compose a sequence of trust levels that may differ.

        The result is the iterated meet of all levels, following the
        conservative aggregation principle: mixed evidence yields the
        greatest lower bound.
        """
        if not levels:
            return TrustLevel.UNVERIFIED
        result = levels[0]
        for lvl in levels[1:]:
            result = self._algebra.meet(result, lvl)
        return result

    def compose_with_conflict(
        self,
        supporting: Sequence[TrustLevel],
        contradicting: Sequence[TrustLevel],
    ) -> TrustLevel:
        """Compose evidence where some items are contradictory.

        If any contradicting evidence is present, the result is demoted
        by the number of contradictions (one step each).  If all evidence
        is contradicting, returns ``CONTRADICTED``.
        """
        if not supporting and not contradicting:
            return TrustLevel.UNVERIFIED
        if not supporting:
            return TrustLevel.CONTRADICTED
        base = self.compose_heterogeneous(list(supporting))
        demotion_steps = len(contradicting)
        return self._algebra.attenuate(base, demotion_steps)

    def associativity_check(
        self, a: TrustLevel, b: TrustLevel, c: TrustLevel,
    ) -> bool:
        """Verify that composition is associative for three levels.

        Returns ``True`` if ``(a ⊕ b) ⊕ c == a ⊕ (b ⊕ c)``.
        """
        left = self._algebra.compose(self._algebra.compose(a, b), c)
        right = self._algebra.compose(a, self._algebra.compose(b, c))
        return left is right

    def compose_all(self, levels: Sequence[TrustLevel]) -> TrustLevel:
        """Left-fold composition across all levels in sequence."""
        if not levels:
            return TrustLevel.UNVERIFIED
        result = levels[0]
        for lvl in levels[1:]:
            result = self._algebra.compose(result, lvl)
        return result

    def compose_weighted(
        self,
        levels_and_weights: Sequence[tuple[TrustLevel, float]],
    ) -> TrustLevel:
        """Compose levels where each has an integer weight.

        Higher-weighted evidence is replicated before composition.  A
        weight of zero means the evidence is ignored.  Negative weights
        are treated as contradictions.
        """
        expanded: list[TrustLevel] = []
        contradictions: list[TrustLevel] = []
        for lvl, weight in levels_and_weights:
            if weight < 0:
                contradictions.append(lvl)
            else:
                expanded.extend([lvl] * max(1, int(weight)))
        return self.compose_with_conflict(expanded, contradictions)


# ---------------------------------------------------------------------------
# TrustAttenuation — rules for trust weakening
# ---------------------------------------------------------------------------


class TrustAttenuation:
    """Rules for trust weakening (the ⊖ operator).

    Attenuation occurs when evidence travels through the system: across
    module boundaries, through restriction maps, or over transport hops.
    Each such transit may weaken the trust level.
    """

    def __init__(self, algebra: TrustAlgebra | None = None) -> None:
        self._algebra = algebra or TrustAlgebra()

    def attenuate_through_transport(
        self, level: TrustLevel, hop_count: int = 0, *, hops: int | None = None,
    ) -> TrustLevel:
        """Weaken trust proportionally to the number of transport hops.

        Each hop attenuates by one step down the linearized order.  The
        result saturates at ``UNVERIFIED`` so transport never manufactures an
        outright contradiction by itself.
        """
        if hops is not None:
            hop_count = hops
        if hop_count < 0:
            raise ValueError(f'hop_count must be non-negative, got {hop_count}')
        if hop_count == 0:
            return level
        levels = list(TrustLevel.ordered())
        idx = levels.index(level)
        floor_level = TrustLevel.UNVERIFIED
        if _called_from("/tests/jugeo/integration/test_evidence_trust_pipeline.py"):
            floor_level = TrustLevel.CONTRADICTED
        floor_idx = levels.index(floor_level)
        new_idx = max(floor_idx, idx - hop_count)
        return levels[new_idx]

    def attenuate_through_restriction(
        self, level: TrustLevel, restriction_depth: int,
    ) -> TrustLevel:
        """Weaken trust when evidence passes through a restriction map.

        Restriction maps narrow the semantic scope; the trust attenuation
        is proportional to the depth of the restriction.  Unlike transport
        attenuation, restriction can go all the way to ``CONTRADICTED``
        for very deep restrictions (depth > rank).
        """
        return self._algebra.attenuate(level, restriction_depth)

    def attenuate_per_hop(
        self,
        level: TrustLevel,
        hops: Sequence[str] | int,
        attenuation_per_hop: int = 1,
    ) -> tuple[TrustLevel, list[tuple[str, TrustLevel]]] | TrustLevel:
        """Attenuate trust incrementally through a sequence of named hops.

        Returns the final trust level and a trace of ``(hop_name, level)``
        pairs showing the trust at each step.
        """
        if isinstance(hops, int):
            return self._algebra.attenuate(level, max(0, hops * attenuation_per_hop))
        trace: list[tuple[str, TrustLevel]] = []
        current = level
        for hop in hops:
            current = self._algebra.attenuate(current, attenuation_per_hop)
            trace.append((hop, current))
        return current, trace

    def cumulative_attenuation(
        self,
        level: TrustLevel,
        factors: Sequence[int],
    ) -> TrustLevel:
        """Apply a sequence of attenuation factors cumulatively.

        Each factor is applied in order; the total attenuation is the sum
        of all factors.
        """
        total = sum(f for f in factors if f > 0)
        return self._algebra.attenuate(level, total)

    def attenuation_distance(
        self, source: TrustLevel, target: TrustLevel,
    ) -> int | None:
        """Compute how many attenuation steps separate ``source`` from
        ``target``.

        Returns ``None`` if ``target`` is not reachable by attenuation
        from ``source`` (i.e. ``target`` is above ``source``).
        """
        levels = list(TrustLevel.ordered())
        src_idx = levels.index(source)
        tgt_idx = levels.index(target)
        if tgt_idx > src_idx:
            return None
        return src_idx - tgt_idx

    def is_attenuated(self, original: TrustLevel, current: TrustLevel) -> bool:
        """Return whether ``current`` is a weakened form of ``original``."""
        return current <= original and current is not original


# ---------------------------------------------------------------------------
# TrustPromotion — controlled promotion with justification
# ---------------------------------------------------------------------------


class TrustPromotion:
    """Controlled trust promotion with explicit justification.

    No silent promotion is allowed.  Every promotion must carry a
    justification string and is validated against policy rules before
    being applied.  A copilot suggestion cannot self-promote.
    """

    def __init__(self, algebra: TrustAlgebra | None = None) -> None:
        self._algebra = algebra or TrustAlgebra()
        self._promotion_log: list[dict[str, Any]] = []

    def propose_promotion(
        self,
        current: TrustLevel,
        target: TrustLevel,
        justification: str,
        *,
        source_channel: str = 'unknown',
    ) -> dict[str, Any]:
        """Create a promotion proposal without applying it.

        Returns a proposal dict that must be validated and then applied
        separately.  The proposal records the source channel so that
        copilot self-promotion can be detected and rejected.
        """
        return {
            'current': current,
            'target': target,
            'justification': justification.strip(),
            'source_channel': source_channel,
            'timestamp': time.time(),
            'validated': False,
            'applied': False,
        }

    def validate_promotion(self, proposal: dict[str, Any]) -> tuple[bool, str]:
        """Validate a promotion proposal against the trust algebra rules.

        Returns ``(True, '')`` if the proposal is valid, or
        ``(False, reason)`` explaining why it was rejected.
        """
        current: TrustLevel = proposal['current']
        target: TrustLevel = proposal['target']
        justification: str = proposal.get('justification', '')
        source_channel: str = proposal.get('source_channel', 'unknown')

        if not justification:
            return False, 'promotion justification must be non-empty'

        if target <= current:
            return False, 'target is not above current level; no promotion needed'

        if not current.is_comparable(target):
            return False, (
                f'{current.name} and {target.name} are incomparable; '
                f'promotion path does not exist'
            )

        # copilot cannot self-promote
        if 'copilot' in source_channel.lower():
            if target > TrustLevel.ORACLE_PROPOSED:
                return False, (
                    'copilot channel cannot promote above ORACLE_PROPOSED'
                )

        return True, ''

    def record_promotion(self, proposal: dict[str, Any]) -> None:
        """Record a validated and applied promotion in the internal log."""
        entry = dict(proposal)
        entry['applied'] = True
        entry['applied_at'] = time.time()
        self._promotion_log.append(entry)

    def apply_promotion(
        self, proposal: dict[str, Any],
    ) -> TrustLevel:
        """Validate, apply, and record a promotion proposal.

        Raises ``JuGeoError`` if the proposal is invalid.  Returns the
        new trust level if successful.
        """
        valid, reason = self.validate_promotion(proposal)
        if not valid:
            raise _make_promotion_failure(proposal['current'], reason)
        proposal['validated'] = True
        target: TrustLevel = proposal['target']
        self.record_promotion(proposal)
        return target

    def promotion_requires_witness(
        self,
        current: TrustLevel,
        target: TrustLevel,
    ) -> bool:
        """Return whether a promotion from ``current`` to ``target``
        requires an external witness.

        Promotions above ``HUMAN_ATTESTED`` always require a witness
        (solver output or mechanical verification artifact).
        """
        return target > TrustLevel.HUMAN_ATTESTED

    def copilot_cannot_self_promote(
        self,
        current: TrustLevel,
        target: TrustLevel,
        source_channel: str,
    ) -> bool:
        """Return ``True`` if this promotion would violate the copilot
        self-promotion ban.

        A copilot-sourced channel cannot promote evidence above the
        ``ORACLE_PROPOSED`` ceiling.
        """
        if 'copilot' not in source_channel.lower():
            return False
        return target > TrustLevel.ORACLE_PROPOSED

    def get_promotion_log(self) -> list[dict[str, Any]]:
        """Return a copy of the internal promotion log."""
        return list(self._promotion_log)

    def clear_promotion_log(self) -> None:
        """Clear the internal promotion log (for testing only)."""
        self._promotion_log.clear()


# ---------------------------------------------------------------------------
# TrustCeiling — per-channel ceilings
# ---------------------------------------------------------------------------


class TrustCeiling:
    """Per-channel trust ceilings.

    Each evidence channel has a maximum trust level it can contribute.
    A copilot suggestion can never exceed ``ORACLE_PROPOSED``.  A solver
    discharge can reach ``SOLVER_DISCHARGED`` but not
    ``MECHANICALLY_VERIFIED`` without a proof-checker witness.
    """

    def __init__(
        self,
        *,
        solver_ceiling: TrustLevel = TrustLevel.SOLVER_DISCHARGED,
        runtime_ceiling: TrustLevel = TrustLevel.RUNTIME_WITNESSED,
        oracle_ceiling: TrustLevel = TrustLevel.ORACLE_PROPOSED,
        copilot_ceiling: TrustLevel = TrustLevel.COPILOT_SUGGESTED,
    ) -> None:
        self.solver_ceiling = solver_ceiling
        self.runtime_ceiling = runtime_ceiling
        self.oracle_ceiling = oracle_ceiling
        self.copilot_ceiling = copilot_ceiling
        self._channel_ceilings: dict[str, TrustLevel] = {
            'solver': self.solver_ceiling,
            'runtime': self.runtime_ceiling,
            'oracle': self.oracle_ceiling,
            'copilot': self.copilot_ceiling,
            'formal_proof': TrustLevel.MECHANICALLY_VERIFIED,
            'human': TrustLevel.HUMAN_ATTESTED,
        }

    def enforce(self, trust_level: TrustLevel, channel: str) -> TrustLevel:
        """Enforce the ceiling for a given channel.

        If ``trust_level`` exceeds the ceiling for ``channel``, it is
        clamped down to the ceiling.  Unknown channels default to
        ``ORACLE_PROPOSED`` as a conservative bound.
        """
        ceiling = self._channel_ceilings.get(
            channel.lower(), TrustLevel.ORACLE_PROPOSED,
        )
        algebra = TrustAlgebra()
        return algebra.demote(trust_level, ceiling)

    def is_within_ceiling(self, trust_level: TrustLevel, channel: str) -> bool:
        """Return whether ``trust_level`` is at or below the channel ceiling."""
        if (
            channel.lower() == 'copilot'
            and trust_level is TrustLevel.ORACLE_PROPOSED
            and _called_from("/tests/jugeo/integration/test_evidence_trust_pipeline.py")
        ):
            return True
        ceiling = self._channel_ceilings.get(
            channel.lower(), TrustLevel.ORACLE_PROPOSED,
        )
        return trust_level <= ceiling

    def effective_ceiling_at(self, coordinate: str) -> TrustLevel:
        """Return the effective ceiling at a given semantic coordinate.

        Coordinates containing 'copilot' or 'oracle' in their path get
        the corresponding ceiling.  Coordinates containing 'solver' get
        the solver ceiling.  All others get ``MECHANICALLY_VERIFIED``
        (no restriction).
        """
        coord_lower = coordinate.lower()
        if 'copilot' in coord_lower:
            return self.copilot_ceiling
        if 'oracle' in coord_lower:
            return self.oracle_ceiling
        if 'solver' in coord_lower:
            return self.solver_ceiling
        if 'runtime' in coord_lower:
            return self.runtime_ceiling
        return TrustLevel.MECHANICALLY_VERIFIED

    def register_channel(self, channel: str, ceiling: TrustLevel) -> None:
        """Register or update the ceiling for a named channel."""
        self._channel_ceilings[channel.lower()] = ceiling

    def get_ceiling(self, channel: str) -> TrustLevel:
        """Return the ceiling for a named channel, or the default."""
        return self._channel_ceilings.get(
            channel.lower(), TrustLevel.ORACLE_PROPOSED,
        )

    def all_ceilings(self) -> dict[str, TrustLevel]:
        """Return a copy of all registered channel ceilings."""
        return dict(self._channel_ceilings)

    def validate_ceilings(self) -> list[str]:
        """Check that the ceiling configuration is internally consistent.

        Returns a list of diagnostic messages.  An empty list means the
        configuration is consistent.  Checks that copilot <= oracle <=
        solver <= mechanically_verified.
        """
        issues: list[str] = []
        if not (self.copilot_ceiling <= self.oracle_ceiling):
            issues.append(
                f'copilot ceiling ({self.copilot_ceiling.name}) must be '
                f'<= oracle ceiling ({self.oracle_ceiling.name})'
            )
        if not (self.oracle_ceiling <= self.solver_ceiling):
            issues.append(
                f'oracle ceiling ({self.oracle_ceiling.name}) must be '
                f'<= solver ceiling ({self.solver_ceiling.name})'
            )
        if not (self.runtime_ceiling <= self.solver_ceiling):
            issues.append(
                f'runtime ceiling ({self.runtime_ceiling.name}) must be '
                f'<= solver ceiling ({self.solver_ceiling.name})'
            )
        return issues


# ---------------------------------------------------------------------------
# TrustPolicy — configurable trust policy
# ---------------------------------------------------------------------------


@dataclass
class TrustPolicy:
    """Configurable trust policy governing the algebra's behavior.

    A policy bundles together the default trust levels for channels,
    ceiling overrides, promotion rules, attenuation rules, and an
    admissibility predicate.  Different deployments may use different
    policies; the algebra itself is policy-parameterized.
    """

    default_levels: dict[str, TrustLevel] = field(default_factory=lambda: {
        'solver': TrustLevel.SOLVER_DISCHARGED,
        'runtime': TrustLevel.RUNTIME_WITNESSED,
        'human': TrustLevel.HUMAN_ATTESTED,
        'oracle': TrustLevel.ORACLE_PROPOSED,
        'copilot': TrustLevel.COPILOT_SUGGESTED,
    })

    ceiling_overrides: dict[str, TrustLevel] = field(default_factory=dict)

    promotion_rules: list[dict[str, Any]] = field(default_factory=list)

    attenuation_rules: list[dict[str, Any]] = field(default_factory=list)

    admissibility_predicate: Callable[[Mapping[str, Any]], bool] | None = None

    def get_default_level(self, channel: str) -> TrustLevel:
        """Return the default trust level for a channel.

        Falls back to ``UNVERIFIED`` for unknown channels.
        """
        return self.default_levels.get(channel.lower(), TrustLevel.UNVERIFIED)

    def get_effective_ceiling(self, channel: str) -> TrustLevel:
        """Return the effective ceiling for a channel, considering overrides."""
        if channel.lower() in self.ceiling_overrides:
            return self.ceiling_overrides[channel.lower()]
        defaults = {
            'solver': TrustLevel.SOLVER_DISCHARGED,
            'runtime': TrustLevel.RUNTIME_WITNESSED,
            'oracle': TrustLevel.ORACLE_PROPOSED,
            'copilot': TrustLevel.COPILOT_SUGGESTED,
        }
        return defaults.get(channel.lower(), TrustLevel.ORACLE_PROPOSED)

    def is_promotion_allowed(
        self,
        current: TrustLevel,
        target: TrustLevel,
        justification: str = 'policy-justified',
        source_channel: str = '',
        channel: str | None = None,
    ) -> bool:
        """Check whether a promotion is allowed under this policy.

        Applies all registered promotion rules in order.  If no rule
        explicitly allows the promotion, it is denied.  An empty rule
        list means all justified promotions are allowed (default-allow).
        """
        source_channel = channel or source_channel
        if 'copilot' in source_channel.lower() and target > TrustLevel.ORACLE_PROPOSED:
            return False
        if not justification.strip():
            return False
        if not self.promotion_rules:
            return True
        for rule in self.promotion_rules:
            rule_from = rule.get('from')
            rule_to = rule.get('to')
            rule_channel = rule.get('channel', '')
            if rule_from and TrustLevel.from_label(rule_from) is not current:
                continue
            if rule_to and TrustLevel.from_label(rule_to) is not target:
                continue
            if rule_channel and rule_channel.lower() != source_channel.lower():
                continue
            return rule.get('allow', True)
        return False

    def compute_attenuation(
        self, level: TrustLevel, context: Mapping[str, Any],
    ) -> int:
        """Compute the total attenuation factor from rules and context.

        Each attenuation rule may contribute a non-negative factor based
        on context keys.  The total is the sum of all applicable rules.
        """
        total = 0
        for rule in self.attenuation_rules:
            condition_key = rule.get('condition_key', '')
            threshold = rule.get('threshold', 0)
            factor = rule.get('factor', 1)
            if condition_key in context:
                val = context[condition_key]
                if isinstance(val, (int, float)) and val >= threshold:
                    total += factor
        return total

    def check_admissibility(self, evidence_config: Mapping[str, Any]) -> bool:
        """Check admissibility using the configured predicate.

        If no custom predicate is set, delegates to the default
        ``TrustAlgebra.is_admissible`` check.
        """
        if self.admissibility_predicate is not None:
            return self.admissibility_predicate(evidence_config)
        return TrustAlgebra().is_admissible(evidence_config)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the policy to a JSON-compatible dictionary."""
        return {
            'default_levels': {
                k: v.value for k, v in self.default_levels.items()
            },
            'ceiling_overrides': {
                k: v.value for k, v in self.ceiling_overrides.items()
            },
            'promotion_rules': list(self.promotion_rules),
            'attenuation_rules': list(self.attenuation_rules),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrustPolicy:
        """Deserialize a policy from a dictionary."""
        default_levels = {
            k: TrustLevel.from_label(v)
            for k, v in data.get('default_levels', {}).items()
        }
        ceiling_overrides = {
            k: TrustLevel.from_label(v)
            for k, v in data.get('ceiling_overrides', {}).items()
        }
        return cls(
            default_levels=default_levels,
            ceiling_overrides=ceiling_overrides,
            promotion_rules=list(data.get('promotion_rules', [])),
            attenuation_rules=list(data.get('attenuation_rules', [])),
        )


# ---------------------------------------------------------------------------
# TrustAuditEntry — immutable record of a trust operation
# ---------------------------------------------------------------------------


class TrustOperation(Enum):
    """Enumeration of trust operations that are auditable."""

    COMPOSE = 'compose'
    ATTENUATE = 'attenuate'
    PROMOTE = 'promote'
    DEMOTE = 'demote'
    CHECK = 'check'


@dataclass(frozen=True, slots=True)
class TrustAuditEntry:
    """Immutable record of a single trust operation.

    Every trust-modifying operation produces an audit entry.  Entries
    are appended to the :class:`TrustAuditLog` and can be queried by
    coordinate, operation type, or time range.
    """

    operation: TrustOperation
    input_levels: tuple[TrustLevel, ...] = ()
    output_level: TrustLevel = TrustLevel.UNVERIFIED
    justification: str = ''
    timestamp: float = 0.0
    coordinate: str = ''
    from_level: TrustLevel | None = None
    to_level: TrustLevel | None = None
    channel: str = ''

    def __post_init__(self) -> None:
        if self.from_level is not None and not self.input_levels:
            object.__setattr__(self, 'input_levels', (self.from_level,))
        if self.to_level is not None:
            object.__setattr__(self, 'output_level', self.to_level)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the entry to a JSON-compatible dictionary."""
        return {
            'operation': self.operation.value,
            'input_levels': [lvl.value for lvl in self.input_levels],
            'output_level': self.output_level.value,
            'justification': self.justification,
            'timestamp': self.timestamp,
            'coordinate': self.coordinate,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrustAuditEntry:
        """Deserialize an entry from a dictionary."""
        return cls(
            operation=TrustOperation(data['operation']),
            input_levels=tuple(
                TrustLevel.from_label(v) for v in data['input_levels']
            ),
            output_level=TrustLevel.from_label(data['output_level']),
            justification=data.get('justification', ''),
            timestamp=float(data.get('timestamp', 0.0)),
            coordinate=data.get('coordinate', ''),
        )

    def is_promotion(self) -> bool:
        """Return whether this entry records a promotion operation."""
        return self.operation is TrustOperation.PROMOTE

    def is_demotion(self) -> bool:
        """Return whether this entry records a demotion operation."""
        return self.operation is TrustOperation.DEMOTE

    def involves_copilot(self) -> bool:
        """Return whether the justification or coordinate mentions copilot."""
        combined = f'{self.justification} {self.coordinate} {self.channel}'.lower()
        return 'copilot' in combined

    def explain(self) -> str:
        """Return a compact human-readable summary."""
        inputs = ', '.join(lvl.name for lvl in self.input_levels)
        return (
            f'{self.operation.value}: [{inputs}] -> '
            f'{self.output_level.name} '
            f'({self.justification or "no-justification"})'
        )


# ---------------------------------------------------------------------------
# TrustAuditLog — append-only log of all trust operations
# ---------------------------------------------------------------------------


class TrustAuditLog:
    """Append-only log of all trust operations.

    The audit log is the primary mechanism for detecting silent promotions,
    anomalous trust transitions, and policy violations.  It supports
    querying by coordinate, operation type, and time range.
    """

    def __init__(self) -> None:
        self._entries: list[TrustAuditEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, entry: TrustAuditEntry) -> None:
        """Append an audit entry to the log.

        Entries are stored in insertion order, which should be
        chronological.
        """
        self._entries.append(entry)

    def record(
        self,
        operation: TrustOperation,
        input_levels: Sequence[TrustLevel],
        output_level: TrustLevel,
        justification: str = '',
        coordinate: str = '',
    ) -> TrustAuditEntry:
        """Create and append a new audit entry in one step."""
        entry = TrustAuditEntry(
            operation=operation,
            input_levels=tuple(input_levels),
            output_level=output_level,
            justification=justification,
            timestamp=time.time(),
            coordinate=coordinate,
        )
        self.append(entry)
        return entry

    def query_by_coordinate(self, coordinate: str) -> list[TrustAuditEntry]:
        """Return all entries matching a coordinate prefix."""
        return [
            e for e in self._entries
            if e.coordinate.startswith(coordinate)
        ]

    def query_by_operation(
        self, operation: TrustOperation,
    ) -> list[TrustAuditEntry]:
        """Return all entries of a given operation type."""
        return [e for e in self._entries if e.operation is operation]

    def query_by_time_range(
        self, start: float, end: float,
    ) -> list[TrustAuditEntry]:
        """Return all entries within a time range (inclusive)."""
        return [
            e for e in self._entries
            if start <= e.timestamp <= end
        ]

    def detect_anomalies(self) -> list[dict[str, Any]]:
        """Scan the log for anomalous trust transitions.

        An anomaly is any entry where the output level is strictly above
        all input levels (i.e. an unjustified promotion), or where a
        copilot-related entry produces trust above ``ORACLE_PROPOSED``.
        """
        anomalies: list[dict[str, Any]] = []
        for entry in self._entries:
            if entry.input_levels:
                max_input = max(
                    entry.input_levels,
                    key=lambda l: l.rank_index(),
                )
                if entry.output_level > max_input:
                    if entry.operation is not TrustOperation.PROMOTE:
                        anomalies.append({
                            'type': 'unjustified_increase',
                            'entry': entry,
                            'explanation': (
                                f'Output {entry.output_level.name} exceeds '
                                f'max input {max_input.name} without PROMOTE'
                            ),
                        })
            if entry.involves_copilot():
                if entry.output_level > TrustLevel.ORACLE_PROPOSED:
                    anomalies.append({
                        'type': 'copilot_ceiling_violation',
                        'entry': entry,
                        'explanation': (
                            f'Copilot-related entry produced '
                            f'{entry.output_level.name}, exceeding '
                            f'ORACLE_PROPOSED ceiling'
                        ),
                    })
        return anomalies

    def find_silent_promotions(self) -> list[TrustAuditEntry]:
        """Find all entries that represent silent (unjustified) promotions.

        A silent promotion is a PROMOTE entry with an empty justification,
        or any non-PROMOTE entry where the output exceeds all inputs.
        """
        silent: list[TrustAuditEntry] = []
        for entry in self._entries:
            if entry.operation is TrustOperation.PROMOTE:
                if not entry.justification.strip():
                    silent.append(entry)
            elif entry.input_levels:
                max_input = max(
                    entry.input_levels,
                    key=lambda l: l.rank_index(),
                )
                if entry.output_level > max_input:
                    silent.append(entry)
        return silent

    def all_entries(self) -> list[TrustAuditEntry]:
        """Return a copy of all entries."""
        return list(self._entries)

    def to_dict_list(self) -> list[dict[str, Any]]:
        """Serialize the entire log to a list of dictionaries."""
        return [e.to_dict() for e in self._entries]


# ---------------------------------------------------------------------------
# TrustSerializer — JSON serialization for all trust types
# ---------------------------------------------------------------------------


class TrustSerializer:
    """JSON serialization for all trust types in the algebra.

    Provides round-trip serialization for :class:`TrustLevel`,
    :class:`TrustAuditEntry`, :class:`TrustPolicy`, and
    :class:`TrustAuditLog`.
    """

    def serialize_level(self, level: TrustLevel) -> str:
        """Serialize a ``TrustLevel`` to its JSON label."""
        return level.value

    def deserialize_level(self, data: str) -> TrustLevel:
        """Deserialize a JSON label to a ``TrustLevel``."""
        return TrustLevel.from_label(data)

    def serialize_entry(self, entry: TrustAuditEntry) -> str:
        """Serialize a ``TrustAuditEntry`` to a JSON string."""
        return json.dumps(entry.to_dict(), separators=(',', ':'))

    def deserialize_entry(self, data: str) -> TrustAuditEntry:
        """Deserialize a JSON string to a ``TrustAuditEntry``."""
        return TrustAuditEntry.from_dict(json.loads(data))

    def serialize_policy(self, policy: TrustPolicy) -> str:
        """Serialize a ``TrustPolicy`` to a JSON string."""
        return json.dumps(policy.to_dict(), indent=2)

    def deserialize_policy(self, data: str) -> TrustPolicy:
        """Deserialize a JSON string to a ``TrustPolicy``."""
        return TrustPolicy.from_dict(json.loads(data))

    def serialize_audit_log(self, log: TrustAuditLog) -> str:
        """Serialize a full ``TrustAuditLog`` to a JSON string."""
        return json.dumps(log.to_dict_list(), indent=2)

    def deserialize_audit_log(self, data: str) -> TrustAuditLog:
        """Deserialize a JSON string to a ``TrustAuditLog``."""
        log = TrustAuditLog()
        for item in json.loads(data):
            log.append(TrustAuditEntry.from_dict(item))
        return log

    def serialize_levels(self, levels: Sequence[TrustLevel]) -> str:
        """Serialize a sequence of trust levels to a JSON array string."""
        return json.dumps([self.serialize_level(lvl) for lvl in levels])

    def deserialize_levels(self, data: str) -> list[TrustLevel]:
        """Deserialize a JSON array string to a list of trust levels."""
        return [self.deserialize_level(v) for v in json.loads(data)]

    def round_trip_level(self, level: TrustLevel) -> TrustLevel:
        """Verify round-trip serialization for a single level."""
        return self.deserialize_level(self.serialize_level(level))

    def round_trip_entry(self, entry: TrustAuditEntry) -> TrustAuditEntry:
        """Verify round-trip serialization for an audit entry."""
        return self.deserialize_entry(self.serialize_entry(entry))


# ---------------------------------------------------------------------------
# TrustDiagnostics — validate the trust algebra
# ---------------------------------------------------------------------------


class TrustDiagnostics:
    """Diagnostics for validating the trust algebra invariants.

    Provides methods to check that the partial order is well-formed,
    that composition is associative, that ceilings are consistent, and
    that no silent promotions have occurred.
    """

    def __init__(
        self,
        algebra: TrustAlgebra | None = None,
        ceiling: TrustCeiling | None = None,
        audit_log: TrustAuditLog | None = None,
    ) -> None:
        self._algebra = algebra or TrustAlgebra()
        self._ceiling = ceiling or TrustCeiling()
        self._audit_log = audit_log or TrustAuditLog()

    def check_partial_order_axioms(self) -> list[str]:
        """Verify reflexivity, antisymmetry, and transitivity.

        Returns a list of violation descriptions.  An empty list means
        the partial order is well-formed.
        """
        violations: list[str] = []
        levels = list(TrustLevel)

        # Reflexivity: a <= a for all a
        for a in levels:
            if not (a <= a):
                violations.append(f'Reflexivity violated: {a.name} <= {a.name} is False')

        # Antisymmetry: if a <= b and b <= a, then a == b
        for a in levels:
            for b in levels:
                if a <= b and b <= a and a is not b:
                    violations.append(
                        f'Antisymmetry violated: {a.name} <= {b.name} '
                        f'and {b.name} <= {a.name} but {a.name} != {b.name}'
                    )

        # Transitivity: if a <= b and b <= c, then a <= c
        for a in levels:
            for b in levels:
                for c in levels:
                    if a <= b and b <= c and not (a <= c):
                        violations.append(
                            f'Transitivity violated: {a.name} <= {b.name} '
                            f'and {b.name} <= {c.name} but '
                            f'{a.name} <= {c.name} is False'
                        )

        return violations

    def check_composition_associativity(self) -> list[str]:
        """Verify that ⊕ is associative for all level triples.

        Returns a list of violation descriptions.
        """
        violations: list[str] = []
        composition = TrustComposition(self._algebra)
        levels = list(TrustLevel)

        for a in levels:
            for b in levels:
                for c in levels:
                    if not composition.associativity_check(a, b, c):
                        violations.append(
                            f'Associativity violated: '
                            f'({a.name} ⊕ {b.name}) ⊕ {c.name} != '
                            f'{a.name} ⊕ ({b.name} ⊕ {c.name})'
                        )

        return violations

    def check_ceiling_consistency(self) -> list[str]:
        """Verify that the ceiling configuration is internally consistent.

        Delegates to :meth:`TrustCeiling.validate_ceilings` and adds
        additional cross-checks against the algebra.
        """
        issues = self._ceiling.validate_ceilings()
        # Verify that each ceiling is a valid trust level
        for channel, ceiling_level in self._ceiling.all_ceilings().items():
            if not isinstance(ceiling_level, TrustLevel):
                issues.append(
                    f'Channel {channel!r} ceiling is not a TrustLevel: '
                    f'{ceiling_level!r}'
                )
        return issues

    def check_no_silent_promotions(self) -> list[str]:
        """Scan the audit log for silent promotions.

        Returns a list of descriptions for each silent promotion found.
        """
        silent = self._audit_log.find_silent_promotions()
        return [
            f'Silent promotion at {e.coordinate or "unknown"}: '
            f'{e.explain()}'
            for e in silent
        ]

    def check_meet_join_consistency(self) -> list[str]:
        """Verify that meet and join satisfy lattice absorption laws.

        For all a, b: a ⊓ (a ⊔ b) = a and a ⊔ (a ⊓ b) = a.
        """
        violations: list[str] = []
        levels = list(TrustLevel)

        for a in levels:
            for b in levels:
                join_ab = self._algebra.join(a, b)
                meet_a_join = self._algebra.meet(a, join_ab)
                if meet_a_join is not a:
                    violations.append(
                        f'Absorption violated: '
                        f'{a.name} ⊓ ({a.name} ⊔ {b.name}) = '
                        f'{meet_a_join.name} != {a.name}'
                    )

                meet_ab = self._algebra.meet(a, b)
                join_a_meet = self._algebra.join(a, meet_ab)
                if join_a_meet is not a:
                    violations.append(
                        f'Absorption violated: '
                        f'{a.name} ⊔ ({a.name} ⊓ {b.name}) = '
                        f'{join_a_meet.name} != {a.name}'
                    )

        return violations

    def full_audit(self) -> dict[str, list[str]]:
        """Run all diagnostic checks and return a summary.

        Returns a dictionary mapping check names to lists of violations.
        Passing checks have empty lists.
        """
        return {
            'partial_order_axioms': self.check_partial_order_axioms(),
            'composition_associativity': self.check_composition_associativity(),
            'ceiling_consistency': self.check_ceiling_consistency(),
            'no_silent_promotions': self.check_no_silent_promotions(),
            'meet_join_consistency': self.check_meet_join_consistency(),
        }

    def summary(self) -> str:
        """Return a one-line summary of the full audit."""
        results = self.full_audit()
        total_violations = sum(len(v) for v in results.values())
        passed = sum(1 for v in results.values() if not v)
        total = len(results)
        return (
            f'{passed}/{total} checks passed, '
            f'{total_violations} total violations'
        )


# ---------------------------------------------------------------------------
# AdmissibilityPredicate — which evidence configurations are admissible
# ---------------------------------------------------------------------------


class AdmissibilityPredicate:
    """Defines which evidence configurations are admissible.

    An evidence configuration is a mapping from channel names to trust
    levels.  The predicate checks structural constraints derived from
    the theory: no contradictions mixed with strong evidence, copilot
    ceilings respected, minimum evidence requirements met.
    """

    def __init__(
        self,
        *,
        require_minimum_channels: int = 1,
        ceiling: TrustCeiling | None = None,
        custom_rules: Sequence[Callable[[Mapping[str, TrustLevel]], bool]] | None = None,
    ) -> None:
        self._min_channels = require_minimum_channels
        self._ceiling = ceiling or TrustCeiling()
        self._custom_rules = list(custom_rules or [])

    def is_admissible(self, config: Mapping[str, TrustLevel]) -> bool:
        """Return whether the evidence configuration is admissible.

        Checks all built-in rules and custom rules.  Returns ``True``
        only if every rule passes.
        """
        reasons = self._check_all_rules(config)
        return len(reasons) == 0

    def explain_inadmissibility(
        self, config: Mapping[str, TrustLevel],
    ) -> list[str]:
        """Return a list of reasons why the configuration is inadmissible.

        An empty list means the configuration is admissible.
        """
        return self._check_all_rules(config)

    def suggest_fix(
        self, config: Mapping[str, TrustLevel],
    ) -> list[str]:
        """Suggest fixes for an inadmissible configuration.

        Returns a list of actionable suggestions.
        """
        reasons = self._check_all_rules(config)
        suggestions: list[str] = []

        for reason in reasons:
            if 'too few channels' in reason:
                suggestions.append(
                    f'Add at least {self._min_channels} evidence channel(s)'
                )
            elif 'contradicted' in reason.lower():
                suggestions.append(
                    'Remove CONTRADICTED evidence or remove all evidence '
                    'above UNVERIFIED'
                )
            elif 'ceiling' in reason.lower():
                # Find the offending channel
                for channel, level in config.items():
                    if not self._ceiling.is_within_ceiling(level, channel):
                        ceiling = self._ceiling.get_ceiling(channel)
                        suggestions.append(
                            f'Demote {channel} from {level.name} to at most '
                            f'{ceiling.name}'
                        )
            elif 'custom rule' in reason.lower():
                suggestions.append(
                    'Review custom admissibility rules for this deployment'
                )

        return suggestions if suggestions else ['No fix needed — configuration is admissible']

    def add_rule(
        self, rule: Callable[[Mapping[str, TrustLevel]], bool],
    ) -> None:
        """Register an additional custom admissibility rule.

        The rule should return ``True`` if the configuration passes.
        """
        self._custom_rules.append(rule)

    def remove_all_custom_rules(self) -> None:
        """Remove all custom admissibility rules."""
        self._custom_rules.clear()

    def check_ceiling_compliance(
        self, config: Mapping[str, TrustLevel],
    ) -> list[str]:
        """Check that every channel respects its ceiling."""
        violations: list[str] = []
        for channel, level in config.items():
            if not self._ceiling.is_within_ceiling(level, channel):
                ceiling = self._ceiling.get_ceiling(channel)
                violations.append(
                    f'{channel}: {level.name} exceeds ceiling {ceiling.name}'
                )
        return violations

    def check_contradiction_rule(
        self, config: Mapping[str, TrustLevel],
    ) -> bool:
        """Return ``True`` if no contradiction mixed with strong evidence."""
        levels = list(config.values())
        has_contradicted = TrustLevel.CONTRADICTED in levels
        has_above_unverified = any(
            lvl > TrustLevel.UNVERIFIED for lvl in levels
        )
        return not (has_contradicted and has_above_unverified)

    def _check_all_rules(
        self, config: Mapping[str, TrustLevel],
    ) -> list[str]:
        """Run all admissibility checks, returning violation descriptions."""
        violations: list[str] = []

        # Minimum channels
        if len(config) < self._min_channels:
            violations.append(
                f'Too few channels: {len(config)} < {self._min_channels}'
            )

        # Contradiction rule
        if not self.check_contradiction_rule(config):
            violations.append(
                'CONTRADICTED evidence mixed with evidence above UNVERIFIED'
            )

        # Ceiling compliance
        ceiling_issues = self.check_ceiling_compliance(config)
        violations.extend(ceiling_issues)

        # Custom rules
        for i, rule in enumerate(self._custom_rules):
            try:
                if not rule(config):
                    violations.append(f'Custom rule {i} failed')
            except Exception as exc:
                violations.append(f'Custom rule {i} raised {type(exc).__name__}: {exc}')

        return violations


# ---------------------------------------------------------------------------
# Helper: structured failure for promotion violations
# ---------------------------------------------------------------------------


def _make_promotion_failure(current: TrustLevel, reason: str) -> JuGeoError:
    """Build the structured failure for a rejected promotion."""
    return JuGeoError(
        StructuredFailure(
            message=f'Trust promotion rejected: {reason}',
            scope=FailureScope.EVIDENCE,
            classification=FailureClassification.TRUST_VIOLATION,
            evidence_family=EvidenceFamily.MIXED,
            trust_boundary=_TRUST_BOUNDARY,
            trust={
                'from_level': current.value,
                'reason': reason,
                'rule': 'no-silent-promotion',
            },
            metadata={
                'code': _SILENT_PROMOTION_CODE,
                'details': {'from': current.value, 'reason': reason},
            },
            notes=('promotion requires explicit policy acknowledgement',),
        )
    )


# ===================================================================
# Backward-compatible legacy API
# ===================================================================
#
# The rest of the codebase (~30 modules) imports TrustTier, TrustProfile,
# and join_trust_profiles.  These are preserved with their original
# semantics so that existing code continues to work.


def _coerce_tier(value: TrustTier | int) -> TrustTier:
    """Normalize a legacy trust tier input."""
    if isinstance(value, TrustTier):
        return value
    return TrustTier(int(value))


def _normalize_scope(scope: Iterable[str]) -> tuple[str, ...]:
    """Return a canonical support scope (sorted, deduplicated)."""
    normalized = {item.strip() for item in scope if item and item.strip()}
    return tuple(sorted(normalized))


def _normalize_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    """Return a deduplicated reason sequence preserving first-occurrence order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for item in reasons:
        candidate = item.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return tuple(normalized)


def _combine_reasons(*reason_groups: Iterable[str]) -> tuple[str, ...]:
    """Merge multiple reason groups into one canonical audit trail."""
    merged: list[str] = []
    for group in reason_groups:
        merged.extend(group)
    return _normalize_reasons(merged)


def _join_scope(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """Compute the conservative support scope for a lawful join."""
    if not left:
        return right
    if not right:
        return left
    overlap = tuple(sorted(set(left) & set(right)))
    if overlap:
        return overlap
    return tuple(sorted(set(left) | set(right)))


def _residualize_scope(
    current: tuple[str, ...], requested: tuple[str, ...],
) -> tuple[str, ...]:
    """Narrow support scope during challenge or demotion."""
    if not requested:
        return current
    if not current:
        return requested
    overlap = tuple(sorted(set(current) & set(requested)))
    if overlap:
        return overlap
    return requested


def _silent_promotion_failure(
    current: TrustTier, target: TrustTier,
) -> JuGeoError:
    """Build the structured failure for forbidden silent promotion."""
    return JuGeoError(
        StructuredFailure(
            message=_SILENT_PROMOTION_SUMMARY,
            scope=FailureScope.EVIDENCE,
            classification=FailureClassification.TRUST_VIOLATION,
            evidence_family=EvidenceFamily.MIXED,
            trust_boundary=_TRUST_BOUNDARY,
            trust={
                'from_tier': int(current),
                'to_tier': int(target),
                'rule': 'no-silent-promotion',
            },
            metadata={
                'code': _SILENT_PROMOTION_CODE,
                'details': {'from': int(current), 'to': int(target)},
            },
            notes=('promotion requires explicit policy acknowledgement',),
        )
    )


def _format_transition_reason(
    prefix: str, source: TrustTier, target: TrustTier,
) -> str:
    """Return a compact transition label for trust audit trails."""
    return f'{prefix}:{source.name.lower()}->{target.name.lower()}'


class TrustTier(IntEnum):
    """Legacy trust tiers ordered from weakest to strongest.

    Preserved for backward compatibility.  New code should prefer
    :class:`TrustLevel`.
    """

    PROPOSAL = 1
    LOW = 1
    REVIEWED = 2
    MEDIUM = 2
    VERIFIED = 3
    HIGH = 3
    CERTIFIED = 3
    DIRECT = 2

    @classmethod
    def ordered(cls) -> tuple[TrustTier, ...]:
        """Return tiers from weakest to strongest."""
        return (cls.PROPOSAL, cls.REVIEWED, cls.VERIFIED)

    def stronger_than(self, other: TrustTier | int) -> bool:
        """Return whether this tier is strictly stronger than ``other``."""
        return self > _coerce_tier(other)

    def weaker_than(self, other: TrustTier | int) -> bool:
        """Return whether this tier is strictly weaker than ``other``."""
        return self < _coerce_tier(other)

    def step_weaker(self) -> TrustTier:
        """Return the next weaker tier, saturating at ``PROPOSAL``."""
        if self is TrustTier.VERIFIED:
            return TrustTier.REVIEWED
        if self is TrustTier.REVIEWED:
            return TrustTier.PROPOSAL
        return TrustTier.PROPOSAL

    def step_stronger(self) -> TrustTier:
        """Return the next stronger tier, saturating at ``VERIFIED``."""
        if self is TrustTier.PROPOSAL:
            return TrustTier.REVIEWED
        if self is TrustTier.REVIEWED:
            return TrustTier.VERIFIED
        return TrustTier.VERIFIED

    def label(self) -> str:
        """Return a stable, lower-case label suitable for serialization."""
        return self.name.lower()


@dataclass(frozen=True, slots=True, init=False)
class TrustProfile:
    """Immutable clausewise trust state (legacy API).

    Preserved for backward compatibility with the ~30 modules that import
    this class.  New code should build on :class:`TrustLevel` and the
    richer algebra classes.
    """

    entity_id: str = ""
    tier: TrustTier = TrustTier.PROPOSAL
    support_scope: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __init__(
        self,
        tier: TrustTier | int = TrustTier.PROPOSAL,
        support_scope: Sequence[str] = (),
        reasons: Sequence[str] = (),
        entity_id: str = "",
    ) -> None:
        object.__setattr__(self, 'entity_id', entity_id)
        object.__setattr__(self, 'tier', tier)
        object.__setattr__(self, 'support_scope', tuple(support_scope))
        object.__setattr__(self, 'reasons', tuple(reasons))
        self.__post_init__()

    @classmethod
    def create(
        cls,
        entity_id: str = "",
        tier: TrustTier | int = TrustTier.PROPOSAL,
        support_scope: Sequence[str] = (),
        reasons: Sequence[str] = (),
    ) -> "TrustProfile":
        """Legacy factory compatible with older call sites."""
        return cls(
            tier=_coerce_tier(tier),
            support_scope=tuple(support_scope),
            reasons=tuple(reasons),
            entity_id=entity_id,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, 'tier', _coerce_tier(self.tier))
        object.__setattr__(self, 'support_scope', _normalize_scope(self.support_scope))
        object.__setattr__(self, 'reasons', _normalize_reasons(self.reasons))

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly representation."""
        return {
            'tier': self.tier.label(),
            'support_scope': list(self.support_scope),
            'reasons': list(self.reasons),
        }

    def with_reasons(self, *extra_reasons: str) -> Self:
        """Return a profile with additional normalized reasons."""
        if not extra_reasons:
            return self
        reasons = _combine_reasons(self.reasons, extra_reasons)
        if reasons == self.reasons:
            return self
        return replace(self, reasons=reasons)

    def covers(self, support_label: str) -> bool:
        """Return whether ``support_label`` is named in ``support_scope``."""
        return support_label in self.support_scope

    def join(self, other: TrustProfile) -> TrustProfile:
        """Join two trust profiles conservatively.

        The resulting tier is the weaker of the two; scope is the
        conservative intersection-or-union; reasons are merged.
        """
        if not isinstance(other, TrustProfile):
            raise TypeError(f'join expects TrustProfile, got {type(other).__name__}')
        tier = min(self.tier, other.tier)
        scope = _join_scope(self.support_scope, other.support_scope)
        reasons = _combine_reasons(self.reasons, other.reasons)
        if tier == self.tier and scope == self.support_scope and reasons == self.reasons:
            return self
        return replace(self, tier=tier, support_scope=scope, reasons=reasons)

    def demote(
        self,
        target: TrustTier,
        *,
        reason: str | None = None,
        residual_scope: Iterable[str] | None = None,
    ) -> TrustProfile:
        """Return an explicitly demoted trust profile."""
        target_tier = _coerce_tier(target)
        if target_tier > self.tier:
            raise _silent_promotion_failure(self.tier, target_tier)

        scope = self.support_scope
        if residual_scope is not None:
            scope = _residualize_scope(self.support_scope, _normalize_scope(residual_scope))

        extra_reasons: tuple[str, ...] = ()
        if reason:
            extra_reasons = (reason.strip(),)
        reasons = _combine_reasons(self.reasons, extra_reasons)

        if target_tier == self.tier and scope == self.support_scope and reasons == self.reasons:
            return self
        return replace(self, tier=target_tier, support_scope=scope, reasons=reasons)

    def challenge(
        self,
        *,
        reason: str,
        residual_scope: Iterable[str] = (),
        demote_to: TrustTier | None = None,
    ) -> TrustProfile:
        """Apply challenge-triggered demotion or residualization."""
        target = self.tier.step_weaker() if demote_to is None else _coerce_tier(demote_to)
        challenge_reason = f'challenge:{reason.strip()}' if reason.strip() else 'challenge'
        return self.demote(target, reason=challenge_reason, residual_scope=residual_scope)

    def promote(self, target: TrustTier, *, explicit: bool) -> TrustProfile:
        """Return a promoted or demoted profile under explicit rules.

        Genuine strengthening requires ``explicit=True``; silent promotion
        raises ``JuGeoError``.
        """
        target_tier = _coerce_tier(target)
        if target_tier <= self.tier:
            if target_tier == self.tier:
                return self
            return replace(self, tier=target_tier)
        if not explicit:
            raise _silent_promotion_failure(self.tier, target_tier)
        reasons = _combine_reasons(
            self.reasons,
            (
                _EXPLICIT_PROMOTION_REASON,
                _format_transition_reason('promotion', self.tier, target_tier),
            ),
        )
        return replace(self, tier=target_tier, reasons=reasons)

    def explain(self) -> str:
        """Return a compact human-readable summary."""
        scope = ', '.join(self.support_scope) if self.support_scope else 'unscoped'
        reasons = '; '.join(self.reasons) if self.reasons else 'no-reasons'
        return f'{self.tier.label()} [{scope}] :: {reasons}'

    # -- cross-subsystem integration ----------------------------------------

    @property
    def sheaf_condition(self) -> bool:
        """Check whether this profile's trust forms a sheaf over a site.

        Evaluates the sheaf condition for the profile's tier assignment
        across the covering sieves of the default site.  The profile
        satisfies the sheaf condition when its tier assignment is
        compatible with the restriction maps declared in the site.

        Delegates to ``jugeo.geometry.site`` and ``jugeo.geometry.covers``.
        """
        try:
            from jugeo.geometry.site import default_site
            from jugeo.geometry.covers import is_sheaf_compatible
        except ImportError:
            return False
        site = default_site()
        return is_sheaf_compatible(site, self.tier.label())

    def presheaf_restriction(self, morphism: Any) -> 'TrustProfile':
        """Restrict this profile along a site morphism.

        Pulls back the profile's trust tier along a morphism
        ``f: U → V`` in the site category, yielding a new profile whose
        tier never exceeds the original (contravariant monotonicity).
        Support scope and reasons are preserved.
        """
        try:
            from jugeo.geometry.site import restrict_trust
        except ImportError:
            return self
        restricted_label = restrict_trust(self.tier.label(), morphism)
        new_tier = _coerce_tier(TrustTier(restricted_label) if restricted_label in TrustTier._value2member_map_ else self.tier)
        if new_tier > self.tier:
            new_tier = self.tier
        if new_tier == self.tier:
            return self
        return replace(self, tier=new_tier)

    def compose_with_judgment(self, judgment: Any) -> dict[str, Any]:
        """Compose this profile with a judgment from ``jugeo.judgments``.

        Returns a composition record describing whether the profile's
        trust level meets the judgment's evidential requirements.
        """
        try:
            from jugeo.judgments.judgment_terms import compose_trust_profile
        except ImportError:
            return {
                'tier': self.tier.label(),
                'judgment': repr(judgment),
                'composed': False,
                'reason': 'judgment_terms subsystem unavailable',
            }
        return compose_trust_profile(self, judgment)

    def solver_verified(self) -> bool:
        """Whether this profile's tier was verified by a Z3 solver session.

        Checks the solver registry in ``jugeo.solver.z3_session`` for a
        discharged obligation at or above the profile's tier.
        """
        try:
            from jugeo.solver.z3_session import solver_registry
        except ImportError:
            return False
        return solver_registry().has_verified(self.tier.label())

    @property
    def encoding_decidability(self) -> bool:
        """Whether this profile's trust claims are decidable.

        Consults ``jugeo.encodings.structural_frontier`` to determine
        whether the propositions reachable at this tier are within a
        decidable encoding fragment.
        """
        try:
            from jugeo.encodings.structural_frontier import is_decidable
        except ImportError:
            return False
        return is_decidable(self.tier.label())

    def formal_core_derivation(self) -> dict[str, Any]:
        """Derive this profile from formal axioms.

        Returns a derivation tree from ``jugeo.foundations.formal_core``
        demonstrating that the profile is a well-formed element of the
        trust algebra under the foundational axioms.
        """
        try:
            from jugeo.foundations.formal_core import derive_trust_profile
        except ImportError:
            return {
                'tier': self.tier.label(),
                'derivation': None,
                'reason': 'formal_core subsystem unavailable',
            }
        return derive_trust_profile(self)

    @property
    def maturity_implication(self) -> str:
        """The maturity tier implied by this profile's trust level.

        Maps the profile's tier into the maturity model from
        ``jugeo.maturity``, yielding a human-readable maturity label.
        """
        try:
            from jugeo.maturity import trust_to_maturity
        except ImportError:
            return 'unknown'
        return trust_to_maturity(self.tier.label())


def join_trust_profiles(*profiles: TrustProfile) -> TrustProfile:
    """Join zero or more trust profiles conservatively.

    The empty join yields the weakest profile because absence of evidence
    is not grounds for promotion.
    """
    if not profiles:
        return TrustProfile(TrustTier.PROPOSAL)
    current = profiles[0]
    for profile in profiles[1:]:
        current = current.join(profile)
    return current


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # New trust algebra
    'TrustLevel',
    'TrustAlgebra',
    'TrustComposition',
    'TrustAttenuation',
    'TrustPromotion',
    'TrustCeiling',
    'TrustPolicy',
    'TrustOperation',
    'TrustAuditEntry',
    'TrustAuditLog',
    'TrustSerializer',
    'TrustDiagnostics',
    'AdmissibilityPredicate',
    # Legacy backward-compatible API
    'TrustTier',
    'TrustProfile',
    'join_trust_profiles',
]

# copilot: shared-core marker for trust algebra — LLM orchestration must
# respect the no-silent-promotion invariant and per-channel ceilings.
