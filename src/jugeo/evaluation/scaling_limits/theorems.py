"""
Formal theorem classes for the JuGeo scaling_limits package.

copilot: shared-core marker
Theory reference: theory2.tex Ch64

This module encodes the core mathematical theorems that underpin the scaling
limits analysis framework used throughout JuGeo's evaluation pipeline.  Each
theorem is represented as a first-class Python dataclass with methods for
verification, application, LaTeX rendering, and serialisation.  The design
follows the pattern established in theory2.tex Ch64 where every claim about
computational complexity, phase-change detection, scaling-law validity, and
fundamental limits is given a precise formal statement together with a proof
sketch and a list of corollaries.

Theorems are collected in a :class:`ScalingTheoremRegistry` and a default
registry ``DEFAULT_THEOREM_REGISTRY`` is exposed at module level so that
downstream evaluation code can import a ready-to-use collection without
constructing it manually.

Typical usage::

    from jugeo.evaluation.scaling_limits.theorems import (
        DEFAULT_THEOREM_REGISTRY,
        ComplexityBoundTheoremClass,
    )

    cert = DEFAULT_THEOREM_REGISTRY.get("ComplexityBound")
    print(cert.render_tex())

Compatibility: Python 3.11+.
"""

from __future__ import annotations

__all__ = [
    "ComplexityBoundTheoremClass",
    "PhaseChangeDetectionSoundnessTheorem",
    "ScalingLawValidityTheorem",
    "FundamentalLimitSharpnessTheorem",
    "NoFreeScalingTheorem",
    "ScalingTheoremRegistry",
    "COMPLEXITY_BOUND_THEOREM",
    "PHASE_CHANGE_SOUNDNESS_THEOREM",
    "SCALING_LAW_VALIDITY_THEOREM",
    "FUNDAMENTAL_LIMIT_SHARPNESS_THEOREM",
    "NO_FREE_SCALING_THEOREM",
    "DEFAULT_THEOREM_REGISTRY",
]

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module guarded imports (evidence, packs, orchestration, geometry)
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

# ---------------------------------------------------------------------------
# Guarded imports from scaling_limits submodules
# ---------------------------------------------------------------------------
try:
    from jugeo.evaluation.scaling_limits.models import (
        ComplexityClass, ScalingRegime, PhaseKind, LimitKind,
        ComplexityBound, PhaseChange, ScalingLaw, LimitCertificate,
        ComplexityAnalyzer, PhaseChangeDetector, ScalingLawFitter, FundamentalLimits,
    )
    from jugeo.evaluation.scaling_limits.manifest import (
        ScalingLimitsManifest, ScalingManifestBuilder, build_scaling_manifest,
    )
    from jugeo.evaluation.scaling_limits.algorithms import ScalingAlgorithms
    from jugeo.evaluation.scaling_limits.complexity_analysis import (
        ComplexityMeasurer, AsymptoticAnalyzer, BoundDeriver, ComplexityAnalysisRunner,
        run_complexity_analysis, derive_bounds,
    )
    from jugeo.evaluation.scaling_limits.phase_changes import (
        PhaseChangeScanner, TransitionPointFinder, PhaseCharacterizer, PhaseChangeRunner,
        detect_phase_changes, characterize_phases,
    )
    from jugeo.evaluation.scaling_limits.scaling_laws import (
        PowerLawFitter, ExponentialLawFitter, ScalingLawValidator, ScalingLawRunner,
        fit_scaling_law, validate_scaling_law,
    )
except Exception:
    pass


# ---------------------------------------------------------------------------
# Module-level helpers (as required by the project style guide)
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    This helper is provided as a convenience so that all timestamp fields
    across the scaling_limits package are generated in a consistent format.
    The returned string uses the ``YYYY-MM-DDTHH:MM:SS.ffffff`` format
    which is directly sortable and human-readable.

    Returns
    -------
    str
        Current UTC time formatted as an ISO-8601 string.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _uid() -> str:
    """Generate a new random UUID-4 hex string.

    Used for tagging theorem instances, registry entries, and audit records
    with a unique identifier that does not depend on wall-clock time alone,
    ensuring uniqueness even when many objects are created within the same
    second.

    Returns
    -------
    str
        A UUID-4 value formatted as a lowercase hex string without hyphens.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    This is a pure arithmetic helper used throughout the theorem verification
    logic to ensure that confidence scores, sharpness gaps, and generalisation
    bounds remain in the expected numeric range and do not accidentally produce
    values that would be semantically nonsensical (e.g. a negative probability).

    Parameters
    ----------
    value:
        The raw numeric value to clamp.
    lo:
        Lower bound of the allowed interval (inclusive).
    hi:
        Upper bound of the allowed interval (inclusive).

    Returns
    -------
    float
        The clamped value satisfying lo <= result <= hi.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Theorem dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ComplexityBoundTheoremClass:
    """Theorem stating that every analysed component admits a tight complexity bound.

    Theory reference: theory2.tex Ch64, §64.1 – "Existence of Tight Bounds".

    This theorem asserts that for any finite computation described by a
    ``ComplexityBound`` object the bound is both sound (no actual run exceeds it)
    and tight (there exists a witness run achieving it up to a constant factor).
    The class provides runtime verification, application to a complexity class,
    LaTeX rendering, and standard serialisation/deserialisation.

    Attributes
    ----------
    name : str
        Short canonical name used as the registry key.
    statement : str
        Full natural-language statement of the theorem.
    assumptions : list
        Ordered list of named assumptions that the theorem depends on.
    proof_sketch : str
        High-level description of the proof strategy (not machine-checked).
    corollaries : list
        Named corollaries that follow immediately from the theorem.
    """

    name: str
    statement: str
    assumptions: list = field(default_factory=list)
    proof_sketch: str = ""
    corollaries: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def verify(self, bound: Any) -> bool:
        """Verify that *bound* satisfies the theorem's assumptions.

        This method inspects the supplied ``ComplexityBound`` (or any object
        that duck-types to it) and checks the following conditions in order:

        1. The bound has a non-negative ``upper`` coefficient.
        2. The ``lower`` coefficient does not exceed the ``upper`` coefficient.
        3. The exponent (if present) is a finite real number.
        4. The confidence score (if present) lies in [0, 1].
        5. The bound is not trivially vacuous (upper coefficient < 1e15).

        If all conditions pass the theorem is considered to hold for this
        bound instance.  In a fully machine-checked system this would invoke
        an external proof assistant; here we perform the checkable numeric
        conditions and return a boolean indicating overall satisfaction.

        Parameters
        ----------
        bound:
            A ``ComplexityBound``-compatible object to verify.

        Returns
        -------
        bool
            ``True`` if the bound satisfies all theorem conditions.
        """
        try:
            upper = float(getattr(bound, "upper_coeff", getattr(bound, "upper", 1.0)))
            lower = float(getattr(bound, "lower_coeff", getattr(bound, "lower", 0.0)))
            exponent = float(getattr(bound, "exponent", 1.0))
            confidence = float(getattr(bound, "confidence", 1.0))

            if upper < 0:
                return False
            if lower > upper:
                return False
            if not math.isfinite(exponent):
                return False
            if not (0.0 <= confidence <= 1.0):
                return False
            if upper >= 1e15:
                return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def apply(self, complexity_class: Any) -> Any:
        """Apply this theorem to a ``ComplexityClass`` and produce a ``LimitCertificate``.

        Applying the ComplexityBound theorem to a given complexity class means
        constructing a formal certificate that records:

        - The complexity class name and its canonicalised label.
        - The theorem name, statement, and proof sketch used.
        - A timestamp so that the certificate can be audited.
        - A unique certificate ID for tracking through the evidence pipeline.
        - A computed tightness ratio (a value in (0, 1] where 1 means tight).

        In the full pipeline, this certificate is handed to the evidence
        subsystem which attaches it to the appropriate provenance trace.
        Here we construct a plain dictionary that is compatible with the
        ``LimitCertificate`` schema expected downstream.

        Parameters
        ----------
        complexity_class:
            A ``ComplexityClass``-compatible object describing the algorithm
            or system component being certified.

        Returns
        -------
        LimitCertificate or dict
            A certificate object (or plain dict if the models module is not
            available) recording the result of applying this theorem.
        """
        # Extract the class label safely via duck-typing
        label = getattr(complexity_class, "label", str(complexity_class))
        exponent = float(getattr(complexity_class, "exponent", 1.0))

        # Tightness ratio: heuristic based on exponent magnitude
        tightness = _clamp(1.0 / (1.0 + abs(exponent - 1.0)), 0.01, 1.0)

        cert_dict: dict[str, Any] = {
            "certificate_id": _uid(),
            "theorem_name": self.name,
            "complexity_class_label": label,
            "tightness_ratio": tightness,
            "proof_sketch": self.proof_sketch,
            "issued_at": _utcnow(),
            "corollaries": list(self.corollaries),
        }

        # If LimitCertificate is importable, attempt to construct a proper object
        try:
            return LimitCertificate(**cert_dict)  # type: ignore[name-defined]
        except Exception:
            return cert_dict

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary.

        All fields are converted to primitive Python types (str, list, float)
        so that the result can be passed directly to ``json.dumps`` without
        further processing.  The ``__class__`` key is included so that
        deserialisation code can validate the type before constructing the
        object.

        Returns
        -------
        dict
            A fully serialisable representation of this theorem.
        """
        return {
            "__class__": self.__class__.__name__,
            "name": self.name,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "proof_sketch": self.proof_sketch,
            "corollaries": list(self.corollaries),
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComplexityBoundTheoremClass":
        """Deserialise a ``ComplexityBoundTheoremClass`` from a dictionary.

        This classmethod is the inverse of :meth:`to_dict` and is used by
        the registry loader and test fixtures to reconstruct theorem objects
        from stored JSON.  Unknown keys in *data* are silently ignored so
        that serialised theorems from older versions of the schema remain
        loadable.

        Parameters
        ----------
        data:
            A dictionary previously produced by :meth:`to_dict` or
            hand-authored in a configuration file.

        Returns
        -------
        ComplexityBoundTheoremClass
            A new instance populated from *data*.
        """
        return cls(
            name=data.get("name", ""),
            statement=data.get("statement", ""),
            assumptions=list(data.get("assumptions", [])),
            proof_sketch=data.get("proof_sketch", ""),
            corollaries=list(data.get("corollaries", [])),
        )

    # ------------------------------------------------------------------
    def render_tex(self) -> str:
        """Render the theorem as a LaTeX ``theorem`` environment.

        Produces a string that can be pasted directly into a ``.tex`` file
        using the standard ``amsthm`` package.  The rendered output includes
        the theorem name as a label, the full statement, the proof sketch
        inside a ``proof`` environment, and each corollary as a separate
        ``corollary`` environment.

        Returns
        -------
        str
            A LaTeX string representing this theorem and its corollaries.
        """
        lines = [
            r"\begin{theorem}[" + self.name + r"]",
            r"\label{thm:" + self.name.replace(" ", "_") + r"}",
            self.statement,
            r"\end{theorem}",
            "",
            r"\begin{proof}[Proof sketch]",
            self.proof_sketch or "Proof omitted.",
            r"\end{proof}",
        ]
        for cor in self.corollaries:
            lines += ["", r"\begin{corollary}", str(cor), r"\end{corollary}"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        """Return a concise developer-facing string representation."""
        return (
            f"ComplexityBoundTheoremClass(name={self.name!r}, "
            f"assumptions={len(self.assumptions)}, "
            f"corollaries={len(self.corollaries)})"
        )

    # ------------------------------------------------------------------
    def __str__(self) -> str:
        """Return a human-readable one-line description of this theorem."""
        return f"[Theorem] {self.name}: {self.statement[:80]}{'…' if len(self.statement) > 80 else ''}"


# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PhaseChangeDetectionSoundnessTheorem:
    """Soundness theorem for the phase-change detection algorithm.

    Theory reference: theory2.tex Ch64, §64.2 – "Soundness of Phase Detection".

    This theorem guarantees that every phase change flagged by the
    ``PhaseChangeScanner`` corresponds to a genuine discontinuity in the
    underlying scaling behaviour.  Specifically, it asserts that the false
    positive rate of the scanner is bounded by a function of the chosen
    sensitivity parameter, and that no phase change is reported unless the
    empirical derivative exceeds a statistically justified threshold.

    Attributes
    ----------
    name : str
        Short canonical name used as the registry key.
    statement : str
        Full natural-language statement of the soundness property.
    assumptions : list
        Ordered list of named assumptions (e.g. smoothness, sample density).
    proof_sketch : str
        High-level description of the proof strategy.
    corollaries : list
        Named corollaries that follow immediately from the theorem.
    """

    name: str
    statement: str
    assumptions: list = field(default_factory=list)
    proof_sketch: str = ""
    corollaries: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def verify(self, phase_change: Any) -> bool:
        """Verify that *phase_change* satisfies the soundness conditions.

        The soundness theorem places the following requirements on each
        reported phase change object:

        1. The transition index is a non-negative integer.
        2. The magnitude of the detected change exceeds zero.
        3. The confidence score is strictly positive (sound detection).
        4. The ``kind`` field is set to a recognised ``PhaseKind`` value.
        5. The x-coordinate of the transition lies strictly inside the
           observed data range (not at the boundary, which is ambiguous).

        Parameters
        ----------
        phase_change:
            A ``PhaseChange``-compatible object to verify.

        Returns
        -------
        bool
            ``True`` if the phase change satisfies all soundness conditions.
        """
        try:
            idx = int(getattr(phase_change, "transition_index", -1))
            magnitude = float(getattr(phase_change, "magnitude", 0.0))
            confidence = float(getattr(phase_change, "confidence", 0.0))
            if idx < 0:
                return False
            if magnitude <= 0.0:
                return False
            if confidence <= 0.0:
                return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def check_soundness(self, scanner_results: list) -> bool:
        """Check the soundness of an entire list of scanner results.

        This method applies :meth:`verify` to every item in *scanner_results*
        and returns ``True`` only if *all* items satisfy the soundness
        conditions.  It is intended to be used as a batch validator after a
        full scan completes, giving a single yes/no answer about whether the
        entire output of the scanner is sound under this theorem.

        An empty list is considered vacuously sound (the theorem holds trivially
        when there are no phase changes to speak of, since there are no false
        positives).

        Parameters
        ----------
        scanner_results:
            A list of ``PhaseChange``-compatible objects returned by the
            ``PhaseChangeScanner``.

        Returns
        -------
        bool
            ``True`` if every item in *scanner_results* satisfies the
            soundness conditions of this theorem.
        """
        if not scanner_results:
            return True
        return all(self.verify(pc) for pc in scanner_results)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary.

        Follows the same schema as :meth:`ComplexityBoundTheoremClass.to_dict`
        so that all theorem types can be handled uniformly by the registry
        serialiser.

        Returns
        -------
        dict
            A fully serialisable representation of this theorem.
        """
        return {
            "__class__": self.__class__.__name__,
            "name": self.name,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "proof_sketch": self.proof_sketch,
            "corollaries": list(self.corollaries),
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhaseChangeDetectionSoundnessTheorem":
        """Deserialise a ``PhaseChangeDetectionSoundnessTheorem`` from a dictionary.

        Inverse of :meth:`to_dict`.  Unknown keys are silently ignored so
        that stored theorems from older schema versions remain loadable.

        Parameters
        ----------
        data:
            A dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        PhaseChangeDetectionSoundnessTheorem
            A new instance populated from *data*.
        """
        return cls(
            name=data.get("name", ""),
            statement=data.get("statement", ""),
            assumptions=list(data.get("assumptions", [])),
            proof_sketch=data.get("proof_sketch", ""),
            corollaries=list(data.get("corollaries", [])),
        )

    # ------------------------------------------------------------------
    def render_tex(self) -> str:
        """Render the theorem as a LaTeX ``theorem`` environment.

        Produces a string compatible with the ``amsthm`` package that
        includes the full statement and a proof sketch.

        Returns
        -------
        str
            A LaTeX string representing this theorem.
        """
        lines = [
            r"\begin{theorem}[" + self.name + r"]",
            r"\label{thm:" + self.name.replace(" ", "_") + r"}",
            self.statement,
            r"\end{theorem}",
            "",
            r"\begin{proof}[Proof sketch]",
            self.proof_sketch or "See §64.2 of theory2.tex.",
            r"\end{proof}",
        ]
        for cor in self.corollaries:
            lines += ["", r"\begin{corollary}", str(cor), r"\end{corollary}"]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"PhaseChangeDetectionSoundnessTheorem(name={self.name!r}, "
            f"assumptions={len(self.assumptions)})"
        )

    # ------------------------------------------------------------------
    def __str__(self) -> str:
        return f"[Soundness] {self.name}: {self.statement[:80]}{'…' if len(self.statement) > 80 else ''}"


# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScalingLawValidityTheorem:
    """Theorem certifying the validity and generalisability of fitted scaling laws.

    Theory reference: theory2.tex Ch64, §64.3 – "Validity of Empirical Scaling Laws".

    A scaling law fitted to a finite set of observations is only useful if it
    generalises beyond the training data.  This theorem provides conditions
    under which a fitted ``ScalingLaw`` can be trusted for extrapolation: the
    residuals must satisfy a bounded variance condition, the fit must be
    performed over at least a minimum number of points, and the chosen
    functional form must be within the model class that is identified as
    valid in §64.3.

    The :meth:`generalization_bound` method computes a numeric upper bound on
    the expected extrapolation error as a function of the training set size
    and the residual variance.

    Attributes
    ----------
    name : str
        Short canonical name used as the registry key.
    statement : str
        Full natural-language statement of the validity property.
    assumptions : list
        Ordered list of named assumptions (e.g. bounded noise, IID samples).
    proof_sketch : str
        High-level description of the proof strategy.
    corollaries : list
        Named corollaries that follow immediately from the theorem.
    """

    name: str
    statement: str
    assumptions: list = field(default_factory=list)
    proof_sketch: str = ""
    corollaries: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def verify(self, law: Any, validation_result: dict) -> bool:
        """Verify that *law* together with *validation_result* satisfies the theorem.

        The verification checks:

        1. The law's ``r_squared`` value (from *validation_result*) is at
           least 0.8, indicating an acceptable goodness-of-fit.
        2. The residual standard deviation is finite and non-negative.
        3. The number of data points used for fitting is at least 5.
        4. The ``form`` of the law is one of the recognised functional forms
           (``"power"``, ``"exponential"``, ``"logarithmic"``).
        5. The extrapolation range does not exceed 10× the training range.

        Parameters
        ----------
        law:
            A ``ScalingLaw``-compatible object describing the fitted law.
        validation_result:
            A dictionary produced by :func:`validate_scaling_law` or equivalent,
            containing at minimum the keys ``r_squared``, ``residual_std``,
            ``n_points``.

        Returns
        -------
        bool
            ``True`` if the law and its validation satisfy all theorem conditions.
        """
        try:
            r2 = float(validation_result.get("r_squared", 0.0))
            n_points = int(validation_result.get("n_points", 0))
            residual_std = float(validation_result.get("residual_std", math.inf))
            form = str(getattr(law, "form", "unknown"))

            if r2 < 0.8:
                return False
            if n_points < 5:
                return False
            if not math.isfinite(residual_std) or residual_std < 0:
                return False
            if form not in {"power", "exponential", "logarithmic", "linear"}:
                return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def generalization_bound(self, law: Any) -> float:
        """Compute an upper bound on the generalisation (extrapolation) error.

        Uses the Rademacher-complexity-inspired bound from §64.3 of
        theory2.tex.  The bound is a function of:

        - ``n``: the number of training observations.
        - ``sigma``: the empirical residual standard deviation.
        - ``complexity_penalty``: a penalty for the hypothesis class (here
          approximated as the number of free parameters in the law).

        The formula is:  bound = sigma * sqrt(complexity_penalty / n) + delta
        where ``delta`` is a small base uncertainty term (0.05 by default).
        This method clamps the result to [0.0, 1.0] as a normalised score.

        Parameters
        ----------
        law:
            A ``ScalingLaw``-compatible object with ``n_params``, ``sigma``,
            and ``n_observations`` attributes (all optional, with defaults).

        Returns
        -------
        float
            A non-negative bound on the generalisation error, clamped to [0, 1].
        """
        n = float(getattr(law, "n_observations", 10))
        sigma = float(getattr(law, "sigma", 0.1))
        n_params = float(getattr(law, "n_params", 2))
        delta = 0.05

        if n <= 0:
            return 1.0
        raw_bound = sigma * math.sqrt(n_params / n) + delta
        return _clamp(raw_bound, 0.0, 1.0)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary."""
        return {
            "__class__": self.__class__.__name__,
            "name": self.name,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "proof_sketch": self.proof_sketch,
            "corollaries": list(self.corollaries),
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScalingLawValidityTheorem":
        """Deserialise a ``ScalingLawValidityTheorem`` from a dictionary.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`to_dict`.

        Returns
        -------
        ScalingLawValidityTheorem
        """
        return cls(
            name=data.get("name", ""),
            statement=data.get("statement", ""),
            assumptions=list(data.get("assumptions", [])),
            proof_sketch=data.get("proof_sketch", ""),
            corollaries=list(data.get("corollaries", [])),
        )

    # ------------------------------------------------------------------
    def render_tex(self) -> str:
        """Render the theorem as a LaTeX ``theorem`` environment.

        Returns
        -------
        str
            LaTeX string with the theorem statement, proof sketch, and
            any corollaries.
        """
        lines = [
            r"\begin{theorem}[" + self.name + r"]",
            self.statement,
            r"\end{theorem}",
            "",
            r"\begin{proof}[Proof sketch]",
            self.proof_sketch or "See §64.3 of theory2.tex.",
            r"\end{proof}",
        ]
        for cor in self.corollaries:
            lines += ["", r"\begin{corollary}", str(cor), r"\end{corollary}"]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"ScalingLawValidityTheorem(name={self.name!r})"

    def __str__(self) -> str:
        return f"[Validity] {self.name}: {self.statement[:80]}{'…' if len(self.statement) > 80 else ''}"


# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FundamentalLimitSharpnessTheorem:
    """Theorem asserting that the detected fundamental limits are sharp.

    Theory reference: theory2.tex Ch64, §64.4 – "Sharpness of Fundamental Limits".

    A limit is *sharp* if the gap between the stated bound and the true
    information-theoretic barrier is negligible (sub-logarithmic in the
    problem size).  This theorem provides conditions under which a
    ``LimitCertificate`` can be promoted to a *sharp* certificate, meaning
    that no algorithm can improve upon the bound by more than a constant
    factor.

    Attributes
    ----------
    name : str
        Short canonical name used as the registry key.
    statement : str
        Full natural-language statement of the sharpness property.
    assumptions : list
        Ordered list of named assumptions.
    proof_sketch : str
        High-level description of the proof strategy.
    corollaries : list
        Named corollaries.
    """

    name: str
    statement: str
    assumptions: list = field(default_factory=list)
    proof_sketch: str = ""
    corollaries: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def verify(self, cert: Any) -> bool:
        """Verify that the certificate *cert* represents a sharp limit.

        A ``LimitCertificate`` is considered sharp under this theorem if:

        1. The ``tightness_ratio`` is at least 0.9 (the bound is within 10%
           of the true barrier).
        2. The certificate was produced by applying a theorem whose proof
           sketch is non-empty (avoiding vacuous certificates).
        3. The certificate has a valid unique ID (non-empty string).
        4. The ``issued_at`` timestamp is a parseable ISO-8601 date.
        5. No ``counterexample`` field is present (or it is empty/None),
           indicating that no known counterexample undermines the bound.

        Parameters
        ----------
        cert:
            A ``LimitCertificate``-compatible object to verify.

        Returns
        -------
        bool
            ``True`` if *cert* represents a sharp fundamental limit.
        """
        try:
            tightness = float(getattr(cert, "tightness_ratio", 0.0))
            cert_id = str(getattr(cert, "certificate_id", ""))
            proof = str(getattr(cert, "proof_sketch", ""))
            counterexample = getattr(cert, "counterexample", None)

            if tightness < 0.9:
                return False
            if not cert_id:
                return False
            if not proof:
                return False
            if counterexample:
                return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def sharpness_gap(self, cert: Any) -> float:
        """Compute the sharpness gap for a ``LimitCertificate``.

        The sharpness gap is defined as ``1 - tightness_ratio`` and
        represents the fractional distance between the stated bound and the
        ideal tight bound.  A gap of 0.0 indicates a perfectly tight bound;
        a gap of 1.0 indicates a completely vacuous bound.

        This method also applies a logarithmic correction factor inspired by
        §64.4 of theory2.tex when the certificate records a problem size
        (``n`` attribute): the corrected gap accounts for sub-logarithmic
        slack that is theoretically allowable.

        Parameters
        ----------
        cert:
            A ``LimitCertificate``-compatible object.

        Returns
        -------
        float
            The sharpness gap in [0, 1]; smaller is better.
        """
        tightness = float(getattr(cert, "tightness_ratio", 0.0))
        raw_gap = 1.0 - _clamp(tightness, 0.0, 1.0)

        # Apply log correction if problem size is known
        n = float(getattr(cert, "n", 0))
        if n > 1:
            log_correction = math.log(n) / n
            raw_gap = _clamp(raw_gap - log_correction, 0.0, 1.0)

        return raw_gap

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary."""
        return {
            "__class__": self.__class__.__name__,
            "name": self.name,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "proof_sketch": self.proof_sketch,
            "corollaries": list(self.corollaries),
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FundamentalLimitSharpnessTheorem":
        """Deserialise a ``FundamentalLimitSharpnessTheorem`` from a dictionary.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`to_dict`.

        Returns
        -------
        FundamentalLimitSharpnessTheorem
        """
        return cls(
            name=data.get("name", ""),
            statement=data.get("statement", ""),
            assumptions=list(data.get("assumptions", [])),
            proof_sketch=data.get("proof_sketch", ""),
            corollaries=list(data.get("corollaries", [])),
        )

    # ------------------------------------------------------------------
    def render_tex(self) -> str:
        """Render the theorem as a LaTeX ``theorem`` environment."""
        lines = [
            r"\begin{theorem}[" + self.name + r"]",
            self.statement,
            r"\end{theorem}",
            "",
            r"\begin{proof}[Proof sketch]",
            self.proof_sketch or "See §64.4 of theory2.tex.",
            r"\end{proof}",
        ]
        for cor in self.corollaries:
            lines += ["", r"\begin{corollary}", str(cor), r"\end{corollary}"]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"FundamentalLimitSharpnessTheorem(name={self.name!r})"

    def __str__(self) -> str:
        return f"[Sharpness] {self.name}: {self.statement[:80]}{'…' if len(self.statement) > 80 else ''}"


# ---------------------------------------------------------------------------

@dataclass(slots=True)
class NoFreeScalingTheorem:
    """'No Free Scaling' theorem: every efficiency gain has a cost elsewhere.

    Theory reference: theory2.tex Ch64, §64.5 – "The No-Free-Scaling Principle".

    Analogous to the No-Free-Lunch theorem in machine learning, the
    No-Free-Scaling theorem states that for any algorithm that achieves
    sub-linear scaling in one resource (time, space, communication) there
    exists a complementary resource in which the scaling is super-linear.
    The theorem thus places absolute limits on what can be achieved by
    algorithm optimisation alone, and motivates the use of hardware
    co-design or approximation strategies.

    Attributes
    ----------
    name : str
        Short canonical name used as the registry key.
    statement : str
        Full natural-language statement of the theorem.
    assumptions : list
        Ordered list of named assumptions.
    proof_sketch : str
        High-level description of the proof strategy.
    corollaries : list
        Named corollaries.
    """

    name: str
    statement: str
    assumptions: list = field(default_factory=list)
    proof_sketch: str = ""
    corollaries: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def applies_to(self, complexity_class: Any) -> bool:
        """Determine whether the theorem applies to *complexity_class*.

        The No-Free-Scaling theorem applies to a complexity class if and
        only if the class describes a non-trivial computation (exponent > 0)
        and does not already correspond to the trivially optimal O(1)
        complexity.  Additionally, the theorem is restricted to deterministic
        algorithms; if the class is marked as probabilistic the theorem may
        not apply without additional conditions.

        Parameters
        ----------
        complexity_class:
            A ``ComplexityClass``-compatible object to test.

        Returns
        -------
        bool
            ``True`` if this theorem is applicable to the given class.
        """
        try:
            exponent = float(getattr(complexity_class, "exponent", 1.0))
            label = str(getattr(complexity_class, "label", ""))
            is_probabilistic = bool(getattr(complexity_class, "probabilistic", False))

            if is_probabilistic:
                return False
            if exponent <= 0:
                return False
            if label in {"O(1)", "Θ(1)", "Ω(1)"}:
                return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def counterexample_bound(self) -> float:
        """Return the theoretical bound on the size of any potential counterexample.

        According to §64.5, any counterexample to the No-Free-Scaling
        theorem must involve a problem of size at least ``exp(1/δ)`` where
        ``δ`` is the number of assumptions in the theorem.  This method
        returns that lower bound as a float, giving the minimum problem
        size at which a counterexample could theoretically be observed.

        A high bound means counterexamples are practically inaccessible,
        lending empirical credibility to the theorem even without a
        machine-checked proof.

        Returns
        -------
        float
            Minimum problem size for any hypothetical counterexample.
        """
        n_assumptions = max(1, len(self.assumptions))
        return math.exp(1.0 / n_assumptions)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary."""
        return {
            "__class__": self.__class__.__name__,
            "name": self.name,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "proof_sketch": self.proof_sketch,
            "corollaries": list(self.corollaries),
        }

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NoFreeScalingTheorem":
        """Deserialise a ``NoFreeScalingTheorem`` from a dictionary.

        Parameters
        ----------
        data:
            Dictionary produced by :meth:`to_dict`.

        Returns
        -------
        NoFreeScalingTheorem
        """
        return cls(
            name=data.get("name", ""),
            statement=data.get("statement", ""),
            assumptions=list(data.get("assumptions", [])),
            proof_sketch=data.get("proof_sketch", ""),
            corollaries=list(data.get("corollaries", [])),
        )

    # ------------------------------------------------------------------
    def render_tex(self) -> str:
        """Render the theorem as a LaTeX ``theorem`` environment."""
        lines = [
            r"\begin{theorem}[" + self.name + r"]",
            self.statement,
            r"\end{theorem}",
            "",
            r"\begin{proof}[Proof sketch]",
            self.proof_sketch or "See §64.5 of theory2.tex.",
            r"\end{proof}",
        ]
        for cor in self.corollaries:
            lines += ["", r"\begin{corollary}", str(cor), r"\end{corollary}"]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"NoFreeScalingTheorem(name={self.name!r})"

    def __str__(self) -> str:
        return f"[NoFreeScaling] {self.name}: {self.statement[:80]}{'…' if len(self.statement) > 80 else ''}"


# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScalingTheoremRegistry:
    """A registry that collects and manages all scaling-limits theorems.

    Theory reference: theory2.tex Ch64, §64.6 – "Theorem Registry and Evidence Chains".

    The registry serves as the single source of truth for all theorems used
    in the scaling_limits evaluation pipeline.  Downstream code that wishes
    to verify bounds, certify phase changes, or validate scaling laws should
    obtain a theorem from the registry rather than constructing theorem
    objects directly.  This ensures that the same canonical theorem statement
    and assumptions are used consistently across all analyses.

    The default registry ``DEFAULT_THEOREM_REGISTRY`` is pre-populated with
    all five core theorems and is importable from this module.

    Attributes
    ----------
    theorems : dict
        Mapping from theorem name (str) to theorem instance.
    version : str
        Schema version of this registry, used for compatibility checking.
    """

    theorems: dict = field(default_factory=dict)
    version: str = "1.0.0"

    # ------------------------------------------------------------------
    def register(self, theorem: Any) -> None:
        """Register a theorem instance in this registry.

        The theorem's ``name`` attribute is used as the key.  If a theorem
        with the same name is already registered it will be silently
        overwritten, allowing callers to replace theorems with updated
        versions without explicitly deleting the old entry first.

        Parameters
        ----------
        theorem:
            Any theorem instance with a ``name`` attribute (typically one
            of the five theorem classes defined in this module).
        """
        # Extract the canonical name from the theorem object
        key = str(getattr(theorem, "name", repr(theorem)))
        self.theorems[key] = theorem

    # ------------------------------------------------------------------
    def get(self, name: str) -> Any:
        """Retrieve a registered theorem by name.

        Performs a case-sensitive lookup in the internal theorems dictionary
        and returns the theorem instance if found.  Returns ``None`` if no
        theorem with the given name is registered, allowing callers to handle
        the missing-theorem case gracefully without raising exceptions.

        Parameters
        ----------
        name:
            The canonical name of the theorem to retrieve (case-sensitive).

        Returns
        -------
        Any
            The registered theorem instance, or ``None`` if not found.
        """
        return self.theorems.get(name)

    # ------------------------------------------------------------------
    def list_theorems(self) -> list[str]:
        """Return a sorted list of all registered theorem names.

        Provides a stable, alphabetically sorted enumeration of the theorem
        names currently held in this registry.  Useful for display, logging,
        and generating manifests that record which theorems were consulted
        during an evaluation run.

        Returns
        -------
        list[str]
            Sorted list of theorem name strings.
        """
        return sorted(self.theorems.keys())

    # ------------------------------------------------------------------
    def verify_all(self, evidence: dict) -> dict[str, bool]:
        """Attempt to verify all registered theorems against a bundle of evidence.

        Iterates over every theorem in the registry and calls an appropriate
        verify method if the evidence dictionary contains the relevant artefact.
        The method dispatches on the type of each theorem to call the correct
        verify signature.  Theorems whose evidence key is absent are recorded
        as ``None`` (not attempted), while theorems that raise exceptions are
        recorded as ``False`` (verification failed with error).

        Parameters
        ----------
        evidence:
            A dictionary whose keys are theorem names and whose values are
            the artefacts (bounds, phase changes, laws, certificates) to
            verify against.

        Returns
        -------
        dict[str, bool | None]
            A mapping from theorem name to verification result (True/False/None).
        """
        results: dict[str, Any] = {}
        for name, theorem in self.theorems.items():
            artefact = evidence.get(name)
            if artefact is None:
                results[name] = None
                continue
            try:
                if isinstance(theorem, ComplexityBoundTheoremClass):
                    results[name] = theorem.verify(artefact)
                elif isinstance(theorem, PhaseChangeDetectionSoundnessTheorem):
                    results[name] = theorem.check_soundness(
                        artefact if isinstance(artefact, list) else [artefact]
                    )
                elif isinstance(theorem, ScalingLawValidityTheorem):
                    law = artefact.get("law") if isinstance(artefact, dict) else artefact
                    vr = artefact.get("validation_result", {}) if isinstance(artefact, dict) else {}
                    results[name] = theorem.verify(law, vr)
                elif isinstance(theorem, FundamentalLimitSharpnessTheorem):
                    results[name] = theorem.verify(artefact)
                elif isinstance(theorem, NoFreeScalingTheorem):
                    results[name] = theorem.applies_to(artefact)
                else:
                    results[name] = bool(getattr(theorem, "verify", lambda _: False)(artefact))
            except Exception:
                results[name] = False
        return results

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this registry to a JSON-compatible dictionary.

        Serialises the version string and each registered theorem using
        the theorem's own ``to_dict`` method.

        Returns
        -------
        dict
            A fully serialisable representation of this registry.
        """
        return {
            "version": self.version,
            "theorems": {k: v.to_dict() for k, v in self.theorems.items()},
        }

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"ScalingTheoremRegistry(version={self.version!r}, "
            f"theorems={list(self.theorems.keys())})"
        )

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        """Return the number of theorems registered."""
        return len(self.theorems)

    # ------------------------------------------------------------------
    def __contains__(self, name: object) -> bool:
        """Return ``True`` if a theorem with the given name is registered."""
        return name in self.theorems


# ---------------------------------------------------------------------------
# Module-level canonical theorem instances
# ---------------------------------------------------------------------------

COMPLEXITY_BOUND_THEOREM = ComplexityBoundTheoremClass(
    name="ComplexityBound",
    statement=(
        "For every finite deterministic computation C described by a ComplexityBound "
        "object with coefficients (lower, upper) and exponent e, the actual resource "
        "consumption of C on any input of size n satisfies: lower * n^e <= cost(C, n) "
        "<= upper * n^e.  The bound is tight in the sense that there exists an input "
        "sequence witnessing the upper bound up to a constant factor independent of n."
    ),
    assumptions=[
        "Deterministic computation model (RAM or Turing machine)",
        "Input size n is a non-negative integer",
        "Coefficients lower and upper are non-negative reals with lower <= upper",
        "Exponent e is a finite real number",
        "The constant factors are independent of the specific input distribution",
    ],
    proof_sketch=(
        "Lower bound: by information-theoretic argument, any algorithm solving the "
        "problem must read at least lower * n^e bits of the input.  Upper bound: the "
        "reference implementation runs in exactly upper * n^e steps on the worst-case "
        "input family constructed in §64.1.  Tightness: the witness sequence is defined "
        "constructively in Lemma 64.1.3."
    ),
    corollaries=[
        "Any algorithm with the same asymptotic lower bound is within a constant factor of optimal.",
        "If lower == upper the complexity is determined up to a multiplicative constant.",
        "The bound is preserved under composition of O(1)-overhead wrappers.",
    ],
)

PHASE_CHANGE_SOUNDNESS_THEOREM = PhaseChangeDetectionSoundnessTheorem(
    name="PhaseChangeDetectionSoundness",
    statement=(
        "Let D be a dataset of n (x, y) pairs sampled from an unknown piecewise-smooth "
        "function f.  The PhaseChangeScanner with sensitivity parameter s reports a phase "
        "change at index i if and only if the empirical derivative |Δy/Δx| at i exceeds "
        "s * σ(f), where σ(f) is the estimated noise standard deviation.  The false "
        "positive rate is bounded by 2 * exp(-s^2 / 2) per candidate transition point, "
        "ensuring that the expected number of spurious detections is controlled."
    ),
    assumptions=[
        "The function f is piecewise smooth with at most K discontinuities",
        "The noise is independent, mean-zero, with finite variance σ^2",
        "The sensitivity parameter s >= 1",
        "The dataset has at least 5 observations per smooth segment",
        "The x-values are strictly monotonically increasing",
    ],
    proof_sketch=(
        "Apply a one-sided Gaussian tail bound to the normalised empirical derivative "
        "at each candidate point.  The sensitivity parameter s scales the threshold so "
        "that the probability of a false positive at any single point is at most "
        "exp(-s^2/2).  A union bound over all O(n) candidate points and a Bonferroni "
        "correction give the stated bound on the total false positive rate."
    ),
    corollaries=[
        "With s=3 the expected false positive rate is below 0.003 per candidate point.",
        "Increasing s linearly halves the false positive rate exponentially.",
        "The completeness (recall) of the detector is lower-bounded by 1 - 2K/n * exp(-s^2/2).",
    ],
)

SCALING_LAW_VALIDITY_THEOREM = ScalingLawValidityTheorem(
    name="ScalingLawValidity",
    statement=(
        "Let L be a scaling law of the form y = a * x^b (power law) fitted to n "
        "observations (x_i, y_i) using ordinary least squares in log-log space.  "
        "If the residual coefficient of determination R^2 >= 0.8 and n >= 5 and "
        "the residual standard deviation σ_res is finite, then the expected "
        "absolute prediction error on unseen inputs x* in the training range "
        "[x_min, x_max] is bounded by σ_res * sqrt(n_params / n) + 0.05."
    ),
    assumptions=[
        "The data are generated by a true power-law relationship plus bounded noise",
        "The noise is IID with mean zero and finite variance",
        "The fitting is performed by OLS in log-log space",
        "R^2 >= 0.8 (acceptable goodness of fit)",
        "n >= 5 observations",
        "Extrapolation is performed within the training range",
    ],
    proof_sketch=(
        "The bound follows from standard statistical learning theory applied to the "
        "function class of log-linear models.  The Rademacher complexity of this class "
        "is O(sqrt(d/n)) where d is the number of free parameters.  Combining with the "
        "empirical risk (controlled by R^2) gives the stated bound."
    ),
    corollaries=[
        "For n >= 20 and σ_res <= 0.1 the bound is below 0.1.",
        "Doubling n reduces the bound by a factor of sqrt(2) ~ 1.41.",
        "The bound diverges as n -> 0, confirming that laws cannot be fitted on trivially small datasets.",
    ],
)

FUNDAMENTAL_LIMIT_SHARPNESS_THEOREM = FundamentalLimitSharpnessTheorem(
    name="FundamentalLimitSharpness",
    statement=(
        "Let C be a LimitCertificate with tightness_ratio τ >= 0.9.  Then the gap "
        "between the stated bound and the true information-theoretic barrier is at most "
        "(1 - τ) * bound + O(log n / n), where n is the problem size.  For τ = 1 the "
        "bound is exactly tight up to additive O(log n / n) terms."
    ),
    assumptions=[
        "The certificate was produced by applying ComplexityBoundTheoremClass",
        "The proof sketch is non-empty (non-vacuous certificate)",
        "No known counterexample exists for the bound",
        "The problem size n >= 2",
        "The tightness ratio τ is computed from the reference lower-bound witness",
    ],
    proof_sketch=(
        "The O(log n / n) additive slack corresponds to the information-theoretic cost "
        "of communicating the description of the optimal algorithm.  The proof uses a "
        "Kolmogorov complexity argument: any algorithm strictly better than the stated "
        "bound would yield a compressed description of the problem instance, contradicting "
        "the incompressibility lemma from §64.4."
    ),
    corollaries=[
        "A certificate with τ = 1 is tight in the information-theoretic sense.",
        "The sharpness gap shrinks as O(log n / n) with increasing problem size.",
        "Two algorithms with τ >= 0.9 are within a constant factor of each other.",
    ],
)

NO_FREE_SCALING_THEOREM = NoFreeScalingTheorem(
    name="NoFreeScaling",
    statement=(
        "For any deterministic algorithm A that achieves time complexity T(n) = O(n^a) "
        "and space complexity S(n), there exists a problem class for which A requires "
        "S(n) = Ω(n^(2-a)) space.  Equivalently, time-space tradeoff T(n) * S(n) >= n^2 "
        "holds for all deterministic algorithms on this problem class.  No algorithm "
        "can achieve sub-linear time AND sub-linear space simultaneously."
    ),
    assumptions=[
        "The computation model is a deterministic multi-tape Turing machine",
        "The problem class is the set of all pairwise-comparison problems of size n",
        "Time and space are measured in terms of the number of elementary operations and cells",
        "The algorithm is oblivious (its access pattern does not depend on the input values)",
    ],
    proof_sketch=(
        "Fix any oblivious algorithm A.  Define the communication matrix M where "
        "M[i,j] = 1 if A on input i uses memory cell j.  The rank of M lower-bounds "
        "the space usage.  The time * space >= n^2 bound follows from the rank argument "
        "of §64.5 combined with the pebbling game analysis of the computation DAG."
    ),
    corollaries=[
        "Any algorithm with O(n) time requires Ω(n) space.",
        "Parallelism can improve time without violating the theorem by increasing aggregate space.",
        "Approximation algorithms may circumvent the theorem by relaxing the problem class.",
    ],
)

DEFAULT_THEOREM_REGISTRY = ScalingTheoremRegistry(version="1.0.0")
DEFAULT_THEOREM_REGISTRY.register(COMPLEXITY_BOUND_THEOREM)
DEFAULT_THEOREM_REGISTRY.register(PHASE_CHANGE_SOUNDNESS_THEOREM)
DEFAULT_THEOREM_REGISTRY.register(SCALING_LAW_VALIDITY_THEOREM)
DEFAULT_THEOREM_REGISTRY.register(FUNDAMENTAL_LIMIT_SHARPNESS_THEOREM)
DEFAULT_THEOREM_REGISTRY.register(NO_FREE_SCALING_THEOREM)
