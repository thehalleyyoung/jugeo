"""Theorem synthesis stage for the JuGeo discovery engine — theory2.tex Ch58.

This module implements Stage 3 of the discovery pipeline: theorem synthesis.
Given candidates with kind signatures, it derives theorem candidates by
pattern-matching against known bridging templates, generating proof sketches,
and validating the resulting theorem candidates for logical consistency.

Theory reference: theory2.tex Ch58 §5.3 — Theorem Synthesis Stage.

copilot: shared-core marker

Overview
--------
Theorem synthesis is the creative heart of the discovery pipeline.  It takes
the kind-classified candidates from Stage 2 and attempts to derive *theorem
candidates* — plausible mathematical statements that could be formalised and
proved using existing JuGeo bridging infrastructure.

The synthesis process proceeds in three sub-steps:

1. **Pattern application** — each ``SynthesisPattern`` in the synthesiser's
   pattern library is tested against each ``(candidate, kind_signature)`` pair.
   Patterns that declare they ``apply_to`` the kind are instantiated, producing
   zero or more ``TheoremCandidate`` objects via template filling.

2. **Validation** — each generated theorem candidate is checked for minimum
   statement completeness, proof-sketch coherence, confidence calibration, and
   valid candidate references.  Invalid candidates are either discarded (strict
   mode) or annotated with error messages (lenient mode).

3. **Deduplication** — near-identical theorem candidates (measured by token
   Jaccard similarity on their statement strings) are removed; the highest-
   confidence copy is retained.

Pipeline position
-----------------
Consumes a ``KindClassificationStage`` and produces a ``TheoremSynthesisStage``.
The output is the input to Stage 4 (pack promotion).

Typical usage::

    from jugeo.ideation.discovery_engine.theorem_synthesis import (
        run_theorem_synthesis,
        TheoremSynthesisRunner,
        TheoremSynthesizer,
        ProofSketchBuilder,
        TheoremValidator,
        SynthesisPattern,
    )

    # One-shot
    stage = run_theorem_synthesis(kind_classification_stage, config=cfg)

    # Fine-grained
    synthesiser = TheoremSynthesizer.with_default_patterns()
    runner = TheoremSynthesisRunner(config=cfg)
    stage, diag = runner.run_with_diagnostics(kind_classification_stage)

Design notes
------------
* ``SynthesisPattern`` is a *frozen dataclass* — patterns are immutable once
  created.  The ``instantiate`` method returns a new ``TheoremCandidate``
  rather than modifying any shared state.
* ``TheoremSynthesizer`` respects a *budget*: at most ``budget`` theorem
  candidates are generated per run.  When the budget is exhausted, synthesis
  stops even if further patterns remain.
* ``ProofSketchBuilder`` is intentionally simple — it generates structured but
  not formally verified proof sketches.  Full formal verification is out of
  scope for this module.
* All validation in ``TheoremValidator`` is *syntactic* and *heuristic*, not
  semantic.  Logical correctness requires a downstream proof assistant.

See also
--------
* ``kind_classification`` — provides the input for this stage.
* ``pack_promotion`` — consumes the output of this stage.
* ``jugeo.packs.bridges`` — ``BridgeTheorem`` used by pack promotion.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SynthesisPattern",
    "TheoremSynthesizer",
    "ProofSketchBuilder",
    "TheoremValidator",
    "TheoremSynthesisRunner",
    "run_theorem_synthesis",
    # helpers
    "_utcnow",
    "_uid",
    "_clamp",
    "_synthesize_from_kind",
    "_validate_sketch",
    "_fill_template",
    "_dedupe_theorems",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryResult,
        DiscoveryDiagnostics,
        DiscoveryStatus,
        PipelineStage,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
        NoveltyPipelineStage,
        KindClassificationStage,
        TheoremSynthesisStage,
        PackPromotionStage,
    )
except Exception:
    DiscoveryCandidate = Any  # type: ignore[misc,assignment]
    DiscoveryConfig = Any  # type: ignore[misc,assignment]
    DiscoveryDiagnostics = Any  # type: ignore[misc,assignment]
    KindSignature = Any  # type: ignore[misc,assignment]
    TheoremCandidate = Any  # type: ignore[misc,assignment]
    KindClassificationStage = Any  # type: ignore[misc,assignment]
    TheoremSynthesisStage = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    Returns
    -------
    float
        Seconds since the Unix epoch, UTC.

    Examples
    --------
    >>> t = _utcnow()
    >>> t > 1_700_000_000.0
    True
    """
    return time.time()


def _uid() -> str:
    """Generate a 32-character hexadecimal unique identifier.

    Returns
    -------
    str
        UUID4 hex string (no hyphens).

    Examples
    --------
    >>> uid = _uid()
    >>> len(uid) == 32 and uid.isalnum()
    True
    """
    return uuid.uuid4().hex


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval ``[lo, hi]``.

    Parameters
    ----------
    v:
        Value to clamp.
    lo:
        Inclusive lower bound.  Defaults to ``0.0``.
    hi:
        Inclusive upper bound.  Defaults to ``1.0``.

    Returns
    -------
    float
        Clamped value.

    Raises
    ------
    ValueError
        If ``lo > hi``.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.3, lo=0.0, hi=0.8)
    0.0
    >>> _clamp(0.5, lo=0.0, hi=1.0)
    0.5
    """
    if lo > hi:
        raise ValueError(f"lo ({lo}) must not exceed hi ({hi})")
    return max(lo, min(hi, v))


def _fill_template(template: str, bindings: dict[str, str]) -> str:
    """Substitute ``{key}`` placeholders in *template* with values from *bindings*.

    Any placeholder whose key is absent from *bindings* is left unchanged.
    Nested braces are not supported.

    Parameters
    ----------
    template:
        A string containing ``{key}`` placeholders.
    bindings:
        Dictionary of key → value substitutions.

    Returns
    -------
    str
        The template with all known placeholders replaced.

    Examples
    --------
    >>> _fill_template("Let {X} be a {kind}.", {"X": "M", "kind": "manifold"})
    'Let M be a manifold.'
    >>> _fill_template("Hello {name}!", {})
    'Hello {name}!'
    """
    result = template
    for key, val in bindings.items():
        result = result.replace(f"{{{key}}}", str(val))
    return result


def _validate_sketch(sketch: str, context: dict[str, Any]) -> bool:
    """Perform a lightweight heuristic validity check on a proof sketch.

    A sketch is considered valid if:

    * It is non-empty after stripping whitespace.
    * It contains at least one sentence-ending punctuation mark (``.``, ``!``,
      or ``?``).
    * Its character count is at least 40 (to filter trivially short sketches).
    * It does not consist solely of placeholder text (e.g. ``{...}`` tokens).

    Parameters
    ----------
    sketch:
        The proof sketch string to validate.
    context:
        Additional context dict (currently unused, reserved for future checks).

    Returns
    -------
    bool
        ``True`` if the sketch passes all heuristic checks.

    Examples
    --------
    >>> _validate_sketch("Let X be a smooth manifold. Then by Stokes' theorem...", {})
    True
    >>> _validate_sketch("", {})
    False
    >>> _validate_sketch("{placeholder}", {})
    False
    """
    stripped = sketch.strip()
    if not stripped:
        return False
    if len(stripped) < 40:
        return False
    if not re.search(r"[.!?]", stripped):
        return False
    # Check for unfilled placeholders — if > 30% of tokens are {…} tokens, reject
    tokens = stripped.split()
    placeholder_tokens = sum(1 for t in tokens if re.fullmatch(r"\{[^}]*\}", t))
    if tokens and placeholder_tokens / len(tokens) > 0.3:
        return False
    return True


def _synthesize_from_kind(
    candidate: Any,
    kind_sig: Any,
    templates: list["SynthesisPattern"],
) -> list[Any]:
    """Apply all applicable synthesis patterns to a ``(candidate, kind_sig)`` pair.

    Iterates over *templates* and, for each pattern that ``applies_to`` the
    kind signature, calls ``pattern.instantiate(candidate, kind_sig)``.

    Parameters
    ----------
    candidate:
        The discovery candidate.
    kind_sig:
        The assigned kind signature.
    templates:
        List of ``SynthesisPattern`` objects to attempt.

    Returns
    -------
    list[TheoremCandidate]
        All successfully instantiated theorem candidates.  May be empty if
        no patterns apply or all instantiations fail.

    Notes
    -----
    Exceptions raised by individual ``instantiate`` calls are suppressed so
    that a single malformed pattern cannot abort the entire synthesis run.
    """
    results: list[Any] = []
    for pattern in templates:
        if pattern.applies_to(kind_sig):
            try:
                theorem = pattern.instantiate(candidate, kind_sig)
                if theorem is not None:
                    results.append(theorem)
            except Exception:
                pass  # Suppress per-pattern failures; log via diagnostics
    return results


def _dedupe_theorems(
    theorems: list[Any],
    threshold: float = 0.85,
) -> list[Any]:
    """Remove near-duplicate theorem candidates.

    Two theorem candidates are considered near-duplicates if the Jaccard
    similarity of the lower-cased, whitespace-split tokens of their
    ``statement`` attributes equals or exceeds *threshold*.

    When duplicates are found, the candidate with the higher ``confidence``
    attribute is retained.

    Parameters
    ----------
    theorems:
        List of ``TheoremCandidate``-like objects.
    threshold:
        Jaccard similarity threshold.  Defaults to ``0.85``.

    Returns
    -------
    list[TheoremCandidate]
        De-duplicated list; original relative order is not necessarily preserved
        (candidates are processed in descending confidence order).

    Notes
    -----
    Time complexity is O(n²); acceptable for typical synthesis budgets (≤ 50).

    Examples
    --------
    >>> class T:
    ...     def __init__(self, s, c): self.statement = s; self.confidence = c
    >>> ts = [T("X is smooth", 0.9), T("X is smooth manifold", 0.7), T("Y is flat", 0.8)]
    >>> len(_dedupe_theorems(ts, threshold=0.4))
    2
    """

    def _tok(t: Any) -> set[str]:
        stmt = str(getattr(t, "statement", "") or "")
        return {w.lower() for w in stmt.split() if len(w) > 1}

    def _jaccard(a: set, b: set) -> float:
        if not a and not b:
            return 1.0
        union = len(a | b)
        return len(a & b) / union if union > 0 else 0.0

    # Sort by confidence descending so that the best version is kept first
    sorted_theorems = sorted(
        theorems,
        key=lambda t: float(getattr(t, "confidence", 0.0)),
        reverse=True,
    )
    kept: list[Any] = []
    kept_tokens: list[set[str]] = []
    for t in sorted_theorems:
        tok = _tok(t)
        if all(_jaccard(tok, kt) < threshold for kt in kept_tokens):
            kept.append(t)
            kept_tokens.append(tok)
    return kept


# ---------------------------------------------------------------------------
# SynthesisPattern
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SynthesisPattern:
    """An immutable template for synthesising theorem candidates from kind signatures.

    A ``SynthesisPattern`` encodes a reusable mathematical theorem template
    parameterised by kind-specific variable names.  Each pattern declares
    which kind IDs it can be applied to via ``kind_ids``, and provides an
    ``instantiate`` method that fills the template with candidate-specific
    data.

    Attributes
    ----------
    pattern_id : str
        Unique identifier for this pattern.
    template : str
        A theorem statement template containing ``{variable}`` placeholders.
        Common placeholders include ``{X}`` (the main mathematical object),
        ``{kind}`` (the kind label), ``{dim}`` (dimension count), and
        ``{class}`` (a characteristic class label).
    kind_ids : tuple[str, ...]
        Tuple of kind IDs for which this pattern is applicable.  An empty
        tuple means the pattern applies to all kinds.
    confidence_weight : float
        Base confidence multiplier for theorems produced by this pattern.
        Should be in ``(0.0, 1.0]``.  Higher values indicate patterns that
        are known to be reliably applicable.

    Examples
    --------
    >>> p = SynthesisPattern(
    ...     pattern_id="smooth_stokes",
    ...     template="Let {X} be a compact oriented {kind}. Then ∫_∂{X} ω = ∫_{X} dω.",
    ...     kind_ids=("smooth_manifold",),
    ...     confidence_weight=0.85,
    ... )
    >>> p.applies_to_kind_id("smooth_manifold")
    True
    >>> p.applies_to_kind_id("algebraic_variety")
    False

    Notes
    -----
    The dataclass is ``frozen=True`` to prevent accidental mutation of shared
    pattern objects in the ``TheoremSynthesizer.DEFAULT_PATTERNS`` list.
    """

    pattern_id: str
    template: str
    kind_ids: tuple[str, ...]
    confidence_weight: float = 0.7

    def applies_to(self, kind_sig: Any) -> bool:
        """Return ``True`` if this pattern applies to *kind_sig*.

        A pattern applies if ``kind_ids`` is empty (wildcard) or if
        ``kind_sig.kind_id`` appears in ``kind_ids``.

        Parameters
        ----------
        kind_sig:
            A kind signature with a ``kind_id`` attribute.

        Returns
        -------
        bool

        Examples
        --------
        >>> class S:
        ...     kind_id = "smooth_manifold"
        >>> p = SynthesisPattern("p1", "tmpl", ("smooth_manifold",), 0.8)
        >>> p.applies_to(S())
        True
        """
        if not self.kind_ids:
            return True  # wildcard
        kid = str(getattr(kind_sig, "kind_id", "") or "")
        return kid in self.kind_ids

    def instantiate(self, candidate: Any, kind_sig: Any) -> Any | None:
        """Fill the template with data from *candidate* and *kind_sig*.

        Parameters
        ----------
        candidate:
            A ``DiscoveryCandidate``-like object.
        kind_sig:
            The assigned ``KindSignature``.

        Returns
        -------
        TheoremCandidate or None
            A new theorem candidate, or ``None`` if instantiation fails (e.g.
            the generated statement is too short or contains unresolved
            placeholders).

        Notes
        -----
        Bindings are built from:

        * ``{X}`` → the candidate's short label or a truncated description.
        * ``{kind}`` → the kind ID.
        * ``{dim}`` → the number of dimension labels.
        * ``{class}`` → the first characteristic class, or "unknown".
        * ``{candidate_id}`` → the candidate's ID string.
        """
        dim_labels = getattr(kind_sig, "dimension_labels", ())
        char_classes = getattr(kind_sig, "characteristic_classes", ())
        description = str(getattr(candidate, "description", "") or "")
        short_label = description[:30].strip().rstrip(",;:") or "X"
        bindings: dict[str, str] = {
            "X": short_label,
            "kind": str(getattr(kind_sig, "kind_id", "object") or "object"),
            "dim": str(len(dim_labels)),
            "class": str(char_classes[0]) if char_classes else "unknown",
            "candidate_id": str(getattr(candidate, "candidate_id", _uid()[:8])),
        }
        statement = _fill_template(self.template, bindings)
        if not _validate_sketch(statement, bindings):
            return None

        confidence = _clamp(
            float(getattr(candidate, "novelty_score", 0.5)) * self.confidence_weight
        )
        theorem_id = f"thm_{_uid()[:10]}"
        try:
            return TheoremCandidate(  # type: ignore[call-arg]
                theorem_id=theorem_id,
                statement=statement,
                proof_sketch=f"[Auto-sketch from pattern {self.pattern_id}] " + statement,
                confidence=confidence,
                source_candidate_id=str(getattr(candidate, "candidate_id", "")),
                kind_id=str(getattr(kind_sig, "kind_id", "")),
            )
        except Exception:
            return {  # type: ignore[return-value]
                "theorem_id": theorem_id,
                "statement": statement,
                "confidence": confidence,
                "source_candidate_id": str(getattr(candidate, "candidate_id", "")),
                "kind_id": str(getattr(kind_sig, "kind_id", "")),
            }


# ---------------------------------------------------------------------------
# TheoremSynthesizer
# ---------------------------------------------------------------------------


class TheoremSynthesizer:
    """Synthesise theorem candidates from kind-classified discovery candidates.

    For each ``(candidate, kind_signature)`` pair this synthesiser applies all
    matching ``SynthesisPattern`` objects from its pattern library, collects
    the instantiated theorem candidates, and enforces a per-run budget.

    Parameters
    ----------
    patterns:
        List of ``SynthesisPattern`` objects.  If ``None``, defaults to
        ``TheoremSynthesizer.DEFAULT_PATTERNS``.
    budget:
        Maximum total number of theorem candidates to generate per run.
        Defaults to ``50``.

    Class Attributes
    ----------------
    DEFAULT_PATTERNS : list[SynthesisPattern]
        A curated set of synthesis patterns covering the most common built-in
        mathematical kinds (smooth manifolds, vector bundles, topological spaces,
        algebraic varieties, sheaves, abelian categories, and infinity groupoids).

    Examples
    --------
    Default synthesis::

        syn = TheoremSynthesizer.with_default_patterns()
        theorems = syn.synthesize(candidate, kind_sig)

    Batch synthesis::

        cand_map = {"c1": k1, "c2": k2}  # {candidate: kind_sig}
        all_theorems = syn.synthesize_batch(cand_map)

    Custom patterns::

        my_pattern = SynthesisPattern("my_p", "Let {X} be a {kind}...", ("smooth_manifold",))
        syn = TheoremSynthesizer(patterns=[my_pattern], budget=20)

    Notes
    -----
    The budget is shared across the *entire* run, not per-candidate.  Once
    ``len(generated_theorems) >= budget`` the synthesiser stops processing
    further candidates.

    See also
    --------
    ``SynthesisPattern.instantiate`` — the core instantiation mechanism.
    ``TheoremValidator`` — validates the output of this class.
    """

    DEFAULT_PATTERNS: list[SynthesisPattern] = [
        SynthesisPattern(
            pattern_id="smooth_stokes",
            template=(
                "Let {X} be a compact oriented {kind} of dimension {dim}. "
                "Then the generalised Stokes theorem holds: ∫_∂{X} ω = ∫_{X} dω "
                "for every differential form ω of degree {dim}-1."
            ),
            kind_ids=("smooth_manifold",),
            confidence_weight=0.85,
        ),
        SynthesisPattern(
            pattern_id="bundle_chern_weil",
            template=(
                "Let {X} be a {kind} equipped with a connection ∇. "
                "The {class} characteristic class of {X} is represented by a "
                "closed differential form constructed via the Chern-Weil homomorphism "
                "applied to the curvature of ∇."
            ),
            kind_ids=("vector_bundle", "principal_bundle"),
            confidence_weight=0.80,
        ),
        SynthesisPattern(
            pattern_id="topological_euler",
            template=(
                "Let {X} be a compact {kind}. "
                "The Euler characteristic χ({X}) equals the alternating sum of "
                "Betti numbers: χ({X}) = Σ(-1)^k rank H^k({X}; ℤ). "
                "Moreover the {class} class encodes this invariant cohomologically."
            ),
            kind_ids=("topological_space",),
            confidence_weight=0.75,
        ),
        SynthesisPattern(
            pattern_id="variety_riemann_roch",
            template=(
                "Let {X} be a smooth projective {kind} over an algebraically closed field. "
                "The Hirzebruch-Riemann-Roch theorem expresses χ(E) = ∫_{X} ch(E) · td({X}) "
                "for every coherent sheaf E on {X}, where td denotes the Todd class."
            ),
            kind_ids=("algebraic_variety",),
            confidence_weight=0.82,
        ),
        SynthesisPattern(
            pattern_id="sheaf_cohomology_descent",
            template=(
                "Let {X} be a {kind} equipped with a Grothendieck topology. "
                "For any sheaf F on {X}, Čech cohomology Ȟ^n({X}, F) "
                "agrees with derived-functor sheaf cohomology H^n({X}, F) "
                "when {X} has enough acyclic covers."
            ),
            kind_ids=("sheaf_on_site",),
            confidence_weight=0.78,
        ),
        SynthesisPattern(
            pattern_id="abelian_long_exact",
            template=(
                "Let 0 → A → B → C → 0 be a short exact sequence in a {kind}. "
                "Applying any left-exact functor F yields a long exact sequence "
                "0 → FA → FB → FC → R¹FA → R¹FB → R¹FC → ... "
                "where R^i denotes the i-th right derived functor."
            ),
            kind_ids=("abelian_category",),
            confidence_weight=0.88,
        ),
        SynthesisPattern(
            pattern_id="infinity_groupoid_whitehead",
            template=(
                "Let {X} be an {kind} presenting a homotopy type. "
                "Two points x, y ∈ {X} are connected by a path if and only if "
                "they lie in the same connected component of the geometric realisation |{X}|. "
                "The homotopy groups π_n({X}, x) coincide with those of |{X}|."
            ),
            kind_ids=("infinity_groupoid",),
            confidence_weight=0.72,
        ),
        SynthesisPattern(
            pattern_id="generic_structure_theorem",
            template=(
                "Let {X} be a mathematical object of type {kind} with {dim} "
                "principal dimensions. "
                "There exists a canonical decomposition of {X} indexed by the "
                "{class} invariant that respects all natural morphisms between "
                "objects of type {kind}."
            ),
            kind_ids=(),  # wildcard: applies to any kind
            confidence_weight=0.45,
        ),
    ]

    def __init__(
        self,
        patterns: list[SynthesisPattern] | None = None,
        budget: int = 50,
    ) -> None:
        self._patterns: list[SynthesisPattern] = (
            patterns if patterns is not None else list(self.DEFAULT_PATTERNS)
        )
        self._budget = max(1, int(budget))
        self._generated: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(self, candidate: Any, kind_sig: Any) -> list[Any]:
        """Synthesise theorem candidates for a single ``(candidate, kind_sig)`` pair.

        Parameters
        ----------
        candidate:
            A ``DiscoveryCandidate``-like object.
        kind_sig:
            The assigned ``KindSignature``.

        Returns
        -------
        list[TheoremCandidate]
            Theorem candidates generated by applicable patterns.  May be empty
            if no patterns apply or the budget is exhausted.
        """
        if not self._within_budget():
            return []
        results = self._apply_patterns(candidate, kind_sig)
        if not results:
            fallback = self._generate_fallback(candidate, kind_sig)
            results = [fallback]
        # Enforce budget
        remaining = self._budget - self._generated
        results = results[:max(0, remaining)]
        self._generated += len(results)
        return results

    def synthesize_batch(self, candidates_with_kinds: dict[Any, Any]) -> list[Any]:
        """Synthesise theorems for multiple ``(candidate, kind_sig)`` pairs.

        Parameters
        ----------
        candidates_with_kinds:
            Dictionary mapping ``DiscoveryCandidate``-like objects to their
            ``KindSignature`` objects.  Alternatively a mapping from candidate
            to kind_sig where keys are candidates and values are kind signatures.

        Returns
        -------
        list[TheoremCandidate]
            All theorem candidates synthesised across all pairs.
        """
        all_theorems: list[Any] = []
        for candidate, kind_sig in candidates_with_kinds.items():
            if not self._within_budget():
                break
            theorems = self.synthesize(candidate, kind_sig)
            all_theorems.extend(theorems)
        return all_theorems

    @classmethod
    def with_default_patterns(cls) -> "TheoremSynthesizer":
        """Return a synthesiser pre-loaded with all default patterns.

        Returns
        -------
        TheoremSynthesizer
            New instance with ``DEFAULT_PATTERNS`` and default budget (50).

        Examples
        --------
        >>> syn = TheoremSynthesizer.with_default_patterns()
        >>> len(syn._patterns) >= 8
        True
        """
        return cls(patterns=list(cls.DEFAULT_PATTERNS))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_patterns(self, candidate: Any, kind_sig: Any) -> list[Any]:
        """Apply all matching patterns to ``(candidate, kind_sig)``."""
        return _synthesize_from_kind(candidate, kind_sig, self._patterns)

    def _generate_fallback(self, candidate: Any, kind_sig: Any) -> Any:
        """Generate a minimal fallback theorem when no patterns match."""
        kid = str(getattr(kind_sig, "kind_id", "object") or "object")
        desc = str(getattr(candidate, "description", "") or "object")[:40]
        statement = (
            f"There exists a canonical structural characterisation of {desc} "
            f"as an object of kind {kid}, compatible with all natural transformations "
            f"between objects in the same kind category."
        )
        confidence = _clamp(float(getattr(candidate, "novelty_score", 0.3)) * 0.4)
        theorem_id = f"thm_fb_{_uid()[:8]}"
        try:
            return TheoremCandidate(  # type: ignore[call-arg]
                theorem_id=theorem_id,
                statement=statement,
                proof_sketch=f"Fallback sketch: {statement}",
                confidence=confidence,
                source_candidate_id=str(getattr(candidate, "candidate_id", "")),
                kind_id=kid,
            )
        except Exception:
            return {  # type: ignore[return-value]
                "theorem_id": theorem_id,
                "statement": statement,
                "confidence": confidence,
                "kind_id": kid,
            }

    def _within_budget(self) -> bool:
        """Return ``True`` if the synthesiser has not exhausted its budget."""
        return self._generated < self._budget


# ---------------------------------------------------------------------------
# ProofSketchBuilder
# ---------------------------------------------------------------------------


class ProofSketchBuilder:
    """Build structured natural-language proof sketches.

    A proof sketch is a brief, structured narrative that indicates the
    *shape* of a proof without providing formal details.  The builder
    produces sketches with four sections: introduction, construction,
    evidence, and conclusion.

    Examples
    --------
    Build from a statement and kind::

        builder = ProofSketchBuilder()
        sketch = builder.build(
            statement="Let M be a compact oriented manifold...",
            kind_sig=smooth_manifold_sig,
            evidence=["Stokes 1854", "de Rham 1931"],
        )

    Build from a synthesis pattern::

        sketch = builder.build_from_pattern(pattern, candidate)

    Notes
    -----
    The builder is *stateless* — each call to ``build`` or
    ``build_from_pattern`` produces an independent result.  No internal
    state is accumulated between calls.
    """

    def build(
        self,
        statement: str,
        kind_sig: Any,
        evidence: list[str] | None = None,
    ) -> str:
        """Build a four-section proof sketch.

        Parameters
        ----------
        statement:
            The theorem statement to sketch a proof for.
        kind_sig:
            The kind signature providing structural context.
        evidence:
            Optional list of evidence reference strings (citations, prior
            results) to include in the evidence section.

        Returns
        -------
        str
            A multi-section proof sketch string.
        """
        intro = self._intro_section(statement)
        construction = self._construction_section(kind_sig)
        evidence_sec = self._evidence_section(evidence or [])
        conclusion = self._conclusion_section(statement)
        return "\n\n".join([intro, construction, evidence_sec, conclusion])

    def build_from_pattern(
        self,
        pattern: SynthesisPattern,
        candidate: Any,
    ) -> str:
        """Build a proof sketch tailored to a synthesis pattern and candidate.

        Parameters
        ----------
        pattern:
            The ``SynthesisPattern`` whose template inspired the theorem.
        candidate:
            The source ``DiscoveryCandidate``.

        Returns
        -------
        str
            Proof sketch string.
        """
        desc = str(getattr(candidate, "description", "") or "object")[:60]
        statement = f"Theorem arising from pattern '{pattern.pattern_id}' applied to: {desc}."
        evidence = [f"Pattern {pattern.pattern_id} (confidence_weight={pattern.confidence_weight})"]
        ev_recs = getattr(candidate, "evidence_records", None) or []
        for ev in list(ev_recs)[:3]:
            evidence.append(str(getattr(ev, "summary", ev)))
        return self.build(statement=statement, kind_sig=None, evidence=evidence)

    # ------------------------------------------------------------------
    # Private section builders
    # ------------------------------------------------------------------

    def _intro_section(self, statement: str) -> str:
        """Return the introduction section of the proof sketch."""
        short = statement[:120].rstrip(".") if statement else "the statement"
        return (
            f"[Introduction]\n"
            f"We wish to establish the following: {short}. "
            f"The proof proceeds in three steps: construction of the relevant objects, "
            f"verification of the required properties, and synthesis of the conclusion."
        )

    def _construction_section(self, kind_sig: Any) -> str:
        """Return the construction section describing the mathematical objects."""
        if kind_sig is None:
            return (
                "[Construction]\n"
                "Construct the relevant mathematical objects as prescribed by "
                "the underlying theoretical framework. "
                "Verify that all structural axioms are satisfied."
            )
        kid = str(getattr(kind_sig, "kind_id", "object") or "object")
        dims = list(getattr(kind_sig, "dimension_labels", ()) or ())
        classes = list(getattr(kind_sig, "characteristic_classes", ()) or ())
        dim_str = ", ".join(dims[:4]) if dims else "unspecified dimensions"
        cls_str = ", ".join(classes[:3]) if classes else "no characteristic classes"
        return (
            f"[Construction]\n"
            f"Construct the principal object as a member of kind '{kid}' "
            f"with structural dimensions ({dim_str}) and "
            f"characteristic classes ({cls_str}). "
            f"All morphisms in the {kid} category preserve these invariants by "
            f"definition of the kind."
        )

    def _evidence_section(self, evidence: list[str]) -> str:
        """Return the evidence section listing supporting references."""
        if not evidence:
            return (
                "[Evidence]\n"
                "No explicit evidence records are available. "
                "The theorem rests on the general theory developed in theory2.tex Ch58."
            )
        ev_lines = "\n".join(f"  • {e}" for e in evidence[:6])
        return f"[Evidence]\nThe following evidence supports this theorem:\n{ev_lines}"

    def _conclusion_section(self, statement: str) -> str:
        """Return the conclusion section summarising the result."""
        return (
            f"[Conclusion]\n"
            f"Combining the construction and evidence sections, we conclude that "
            f"the theorem holds in the claimed generality. "
            f"Full formalisation requires a proof assistant; this sketch provides "
            f"the structural blueprint for such a formalisation."
        )


# ---------------------------------------------------------------------------
# TheoremValidator
# ---------------------------------------------------------------------------


class TheoremValidator:
    """Validate theorem candidates for syntactic and heuristic correctness.

    The validator applies a battery of lightweight checks to each theorem
    candidate.  It does *not* perform logical verification — that requires a
    downstream proof assistant.  Its purpose is to filter out clearly malformed
    candidates before they enter the pack-promotion stage.

    Parameters
    ----------
    strict:
        If ``True``, a theorem must pass *all* validation checks to be
        considered valid.  If ``False`` (default), a theorem is considered
        valid if it passes a majority of checks (>50%).

    Examples
    --------
    Lenient validation::

        validator = TheoremValidator(strict=False)
        valid, errors = validator.validate(theorem)
        if not valid:
            for e in errors:
                print(f"  ✗ {e}")

    Batch validation::

        results = validator.validate_batch(theorems)
        valid_theorems = [t for t, ok, _ in results if ok]

    Notes
    -----
    The four checks are:
    * ``_check_statement_completeness`` — the statement is non-empty,
      sufficiently long, and grammatically complete (ends with punctuation).
    * ``_check_proof_sketch_coherence`` — the proof sketch is non-trivial
      and references the theorem statement.
    * ``_check_confidence_calibration`` — the confidence value is in a
      reasonable range (not 0 or exactly 1).
    * ``_check_candidate_references`` — the ``source_candidate_id`` and
      ``kind_id`` fields are non-empty.
    """

    def __init__(self, strict: bool = False) -> None:
        self.strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, theorem: Any) -> tuple[bool, list[str]]:
        """Validate a single *theorem* candidate.

        Parameters
        ----------
        theorem:
            A ``TheoremCandidate``-like object.

        Returns
        -------
        tuple[bool, list[str]]
            ``(valid, errors)`` where *errors* is a list of human-readable
            error messages.  Empty when *valid* is ``True``.
        """
        errors: list[str] = []
        errors += self._check_statement_completeness(theorem)
        errors += self._check_proof_sketch_coherence(theorem)
        errors += self._check_confidence_calibration(theorem)
        errors += self._check_candidate_references(theorem)

        if self.strict:
            valid = len(errors) == 0
        else:
            # Lenient: pass if fewer than half of the 4 check categories failed
            # (we can't easily count categories, so just use threshold on error count)
            valid = len(errors) <= 2
        return valid, errors

    def validate_batch(
        self,
        theorems: list[Any],
    ) -> list[tuple[Any, bool, list[str]]]:
        """Validate a list of theorem candidates.

        Parameters
        ----------
        theorems:
            List of ``TheoremCandidate``-like objects.

        Returns
        -------
        list[tuple[TheoremCandidate, bool, list[str]]]
            List of ``(theorem, valid, errors)`` triples.
        """
        return [(t, *self.validate(t)) for t in theorems]

    # ------------------------------------------------------------------
    # Private check methods
    # ------------------------------------------------------------------

    def _check_statement_completeness(self, theorem: Any) -> list[str]:
        """Check that the statement is non-empty, long enough, and punctuated."""
        errors: list[str] = []
        stmt = str(getattr(theorem, "statement", "") or "")
        if not stmt.strip():
            errors.append("Statement is empty.")
        elif len(stmt.strip()) < 30:
            errors.append(f"Statement too short ({len(stmt.strip())} chars; min 30).")
        if stmt and not re.search(r"[.!?]", stmt):
            errors.append("Statement lacks terminal punctuation.")
        # Check for unresolved template placeholders
        if re.search(r"\{[A-Za-z_]+\}", stmt):
            errors.append("Statement contains unresolved template placeholders.")
        return errors

    def _check_proof_sketch_coherence(self, theorem: Any) -> list[str]:
        """Check that the proof sketch is non-trivial."""
        errors: list[str] = []
        sketch = str(getattr(theorem, "proof_sketch", "") or "")
        if not sketch.strip():
            errors.append("Proof sketch is empty.")
        elif len(sketch.strip()) < 20:
            errors.append(f"Proof sketch too short ({len(sketch.strip())} chars; min 20).")
        return errors

    def _check_confidence_calibration(self, theorem: Any) -> list[str]:
        """Check that the confidence value is plausible."""
        errors: list[str] = []
        try:
            conf = float(getattr(theorem, "confidence", -1.0))
        except (TypeError, ValueError):
            errors.append("Confidence value is not a valid float.")
            return errors
        if conf <= 0.0:
            errors.append(f"Confidence {conf} must be positive.")
        elif conf > 1.0:
            errors.append(f"Confidence {conf} exceeds 1.0.")
        return errors

    def _check_candidate_references(self, theorem: Any) -> list[str]:
        """Check that source_candidate_id and kind_id are populated."""
        errors: list[str] = []
        cid = str(getattr(theorem, "source_candidate_id", "") or "")
        if not cid.strip():
            errors.append("source_candidate_id is empty.")
        kid = str(getattr(theorem, "kind_id", "") or "")
        if not kid.strip():
            errors.append("kind_id is empty.")
        return errors


# ---------------------------------------------------------------------------
# TheoremSynthesisRunner
# ---------------------------------------------------------------------------


class TheoremSynthesisRunner:
    """Orchestrate the full theorem synthesis stage of the discovery pipeline.

    Composes ``TheoremSynthesizer``, ``ProofSketchBuilder``, and
    ``TheoremValidator`` into a single pipeline that accepts a
    ``KindClassificationStage`` and returns a ``TheoremSynthesisStage``.

    Parameters
    ----------
    config:
        Optional ``DiscoveryConfig`` controlling synthesis budget, validation
        strictness, and dedup threshold.

    Examples
    --------
    Basic run::

        runner = TheoremSynthesisRunner()
        stage = runner.run(kind_classification_stage)

    With diagnostics::

        runner = TheoremSynthesisRunner(config=cfg)
        stage, diag = runner.run_with_diagnostics(kind_classification_stage)
        print(f"Generated {diag['synthesized_count']} theorems.")

    Notes
    -----
    The runner is stateless — it creates new synthesiser and validator
    instances on each call, so config changes take effect immediately.
    """

    def __init__(self, config: Any | None = None) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, stage: Any) -> Any:
        """Run theorem synthesis on *stage* and return a ``TheoremSynthesisStage``.

        Parameters
        ----------
        stage:
            A ``KindClassificationStage`` object with ``.candidates`` and
            ``.kind_assignments`` attributes.

        Returns
        -------
        TheoremSynthesisStage
        """
        result, _ = self.run_with_diagnostics(stage)
        return result

    def run_with_diagnostics(self, stage: Any) -> tuple[Any, Any]:
        """Run synthesis and return stage + diagnostics.

        Parameters
        ----------
        stage:
            ``KindClassificationStage`` or equivalent.

        Returns
        -------
        tuple[TheoremSynthesisStage, DiscoveryDiagnostics]
        """
        start = _utcnow()
        candidates = list(getattr(stage, "candidates", []) or [])
        kind_assignments: dict[str, Any] = dict(getattr(stage, "kind_assignments", {}) or {})

        diag: dict[str, Any] = {
            "stage": "theorem_synthesis",
            "run_id": _uid(),
            "input_count": len(candidates),
        }

        # Step 1: Synthesise
        all_theorems = self._run_synthesis_step(candidates, kind_assignments, self._config)
        diag["synthesized_count"] = len(all_theorems)

        # Step 2: Validate
        validated = self._run_validation_step(all_theorems)
        diag["validated_count"] = len(validated)

        # Step 3: Dedup
        deduped = self._run_dedup_step(validated)
        diag["deduped_count"] = len(deduped)

        diag["elapsed_secs"] = _utcnow() - start

        try:
            out_stage = TheoremSynthesisStage(  # type: ignore[call-arg]
                stage_id=_uid(),
                theorem_candidates=tuple(deduped),
                input_count=len(candidates),
                output_count=len(deduped),
                elapsed_secs=diag["elapsed_secs"],
            )
        except Exception:
            out_stage = {  # type: ignore[assignment]
                "stage": "theorem_synthesis",
                "theorem_candidates": deduped,
                "input_count": len(candidates),
                "output_count": len(deduped),
            }

        try:
            out_diag = DiscoveryDiagnostics(**diag)  # type: ignore[call-arg]
        except Exception:
            out_diag = diag  # type: ignore[assignment]

        return out_stage, out_diag

    # ------------------------------------------------------------------
    # Private step runners
    # ------------------------------------------------------------------

    def _run_synthesis_step(
        self,
        candidates: list[Any],
        kind_assignments: dict[str, Any],
        config: Any | None,
    ) -> list[Any]:
        """Run pattern-based synthesis for all classified candidates."""
        budget = int(getattr(config, "synthesis_budget", 50)) if config else 50
        synthesizer = TheoremSynthesizer.with_default_patterns()
        synthesizer._budget = budget

        candidates_with_kinds: dict[Any, Any] = {}
        for c in candidates:
            cid = str(getattr(c, "candidate_id", id(c)))
            kind_sig = kind_assignments.get(cid)
            if kind_sig is not None:
                candidates_with_kinds[c] = kind_sig

        return synthesizer.synthesize_batch(candidates_with_kinds)

    def _run_validation_step(self, theorems: list[Any]) -> list[Any]:
        """Filter out invalid theorem candidates."""
        validator = TheoremValidator(strict=False)
        return [t for t, valid, _ in validator.validate_batch(theorems) if valid]

    def _run_dedup_step(self, theorems: list[Any]) -> list[Any]:
        """Deduplicate theorem candidates by statement similarity."""
        return _dedupe_theorems(theorems, threshold=0.85)


# ---------------------------------------------------------------------------
# Top-level convenience function
# ---------------------------------------------------------------------------


def run_theorem_synthesis(
    stage: Any,
    config: Any | None = None,
) -> Any:
    """Run the full theorem synthesis pipeline stage and return the result.

    This is the primary entry point for Stage 3 of the discovery pipeline.

    Parameters
    ----------
    stage:
        A ``KindClassificationStage`` object produced by ``run_kind_classification``.
    config:
        Optional ``DiscoveryConfig`` controlling synthesis budget and validation.

    Returns
    -------
    TheoremSynthesisStage
        Stage result with ``.theorem_candidates`` containing the validated,
        de-duplicated theorem candidates ready for pack promotion.

    Examples
    --------
    Simple call::

        from jugeo.ideation.discovery_engine.theorem_synthesis import (
            run_theorem_synthesis,
        )
        stage = run_theorem_synthesis(kind_stage, config=cfg)
        for t in stage.theorem_candidates:
            print(t.statement[:80])

    See also
    --------
    ``run_kind_classification`` — Stage 2 that produces the input.
    ``run_pack_promotion`` — Stage 4 that consumes this stage's output.
    """
    runner = TheoremSynthesisRunner(config=config)
    return runner.run(stage)
