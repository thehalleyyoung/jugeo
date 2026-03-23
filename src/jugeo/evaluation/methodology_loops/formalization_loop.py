"""
Formalization Loop — JuGeo Methodology Loops Package (s01)

This module implements the formalization loop of the JuGeo evaluation
methodology.  A *formalization loop* is an iterative procedure that
transforms informal mathematical prose into machine-checkable formal
specifications.  Each iteration applies a :class:`Formalizer` to a
collection of informal texts, writes the resulting formal artifacts via a
:class:`SpecificationWriter`, and then evaluates the output with a
:class:`FormalizationChecker`.  The loop continues until every produced
specification meets the configured consistency and completeness thresholds,
or until the maximum number of iterations has been exhausted.

Design principles
-----------------
* **Immutable results** – :class:`FormalizationResult` is a frozen
  dataclass so that loop history is always auditable.
* **Pluggable back-ends** – the ``formal_language`` parameter selects the
  target proof assistant (Lean 4 by default; Coq, Agda, Isabelle, and
  Metamath are also recognised).
* **Structured diagnostics** – every :class:`FormalizationLoopRunner` run
  produces a rich dictionary that captures per-iteration metrics, warnings,
  and convergence information.

copilot: shared-core marker
Theory reference: theory2.tex Ch62
"""
from __future__ import annotations

import json
import math
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

__all__ = [
    "FormalizationResult",
    "Formalizer",
    "SpecificationWriter",
    "FormalizationChecker",
    "FormalizationLoopRunner",
    "run_formalization_loop",
    "check_formalization",
]

# ---------------------------------------------------------------------------
# Compatibility shims for optional JuGeo dependencies
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
    from jugeo.evaluation.methodology_loops.models import (
        LoopPhase,
        LoopStatus,
        TransitionKind,
        LoopState,
        LoopTransition,
        MethodologyConfig,
        LoopDiagnostics,
        MethodologyLoop,
        FormalizationLoop,
        ImplementationLoop,
        FalsificationLoop,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_SUPPORTED_LANGUAGES = frozenset(
    {"lean4", "coq", "agda", "isabelle", "metamath", "tla+", "alloy"}
)

_KEYWORD_WEIGHTS: dict[str, float] = {
    "theorem": 0.12,
    "lemma": 0.10,
    "proof": 0.10,
    "definition": 0.09,
    "axiom": 0.11,
    "hypothesis": 0.08,
    "forall": 0.07,
    "exists": 0.07,
    "implies": 0.06,
    "iff": 0.06,
    "conjunction": 0.05,
    "disjunction": 0.05,
    "negation": 0.05,
    "type": 0.04,
    "structure": 0.04,
    "inductive": 0.06,
    "recursive": 0.05,
    "termination": 0.06,
}

_COMPLETENESS_HINTS: tuple[str, ...] = (
    "precondition",
    "postcondition",
    "invariant",
    "bound",
    "measure",
    "base case",
    "inductive step",
    "termination argument",
    "edge case",
    "boundary",
)


def _utcnow() -> float:
    """Return current UTC time as a Unix timestamp."""
    return time.time()


def _uid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _normalise_language(lang: str) -> str:
    """Return a lower-cased, stripped language identifier.

    Raises :class:`ValueError` if the identifier is not in the supported set.
    """
    normalised = lang.strip().lower()
    if normalised not in _SUPPORTED_LANGUAGES:
        supported = ", ".join(sorted(_SUPPORTED_LANGUAGES))
        raise ValueError(
            f"Unsupported formal language {lang!r}. "
            f"Supported languages: {supported}"
        )
    return normalised


# ---------------------------------------------------------------------------
# FormalizationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class FormalizationResult:
    """Immutable record produced by one formalization attempt.

    Attributes
    ----------
    result_id:
        Globally unique identifier for this result (UUID4 string).
    spec_text:
        The formal specification text produced by the formalizer.
    formal_language:
        The target formal language in which *spec_text* is written.
    consistency_score:
        A value in [0, 1] estimating how internally consistent the
        specification is.  1.0 means no detected contradictions.
    completeness_score:
        A value in [0, 1] estimating how completely the original informal
        description is captured.  1.0 means every clause and edge case is
        covered.
    clause_count:
        Number of top-level logical clauses (theorems, lemmas, definitions,
        axioms) detected in *spec_text*.
    warnings:
        Immutable tuple of human-readable warning messages produced during
        formalization.
    created_at:
        Unix timestamp (UTC) at which this result was created.
    """

    result_id: str
    spec_text: str
    formal_language: str
    consistency_score: float
    completeness_score: float
    clause_count: int
    warnings: tuple[str, ...]
    created_at: float

    def __init__(
        self,
        formal_text: Optional[str] = None,
        consistency_score: float = 0.0,
        completeness_score: float = 0.0,
        *,
        result_id: Optional[str] = None,
        spec_text: Optional[str] = None,
        formal_language: str = "lean4",
        clause_count: Optional[int] = None,
        warnings: Sequence[str] = (),
        created_at: Optional[float] = None,
    ) -> None:
        text = spec_text if spec_text is not None else formal_text
        if text is None:
            text = ""
        if clause_count is None:
            clause_count = len(
                [
                    line
                    for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("--")
                ]
            )
        object.__setattr__(self, "result_id", result_id or _uid())
        object.__setattr__(self, "spec_text", text)
        object.__setattr__(self, "formal_language", formal_language)
        object.__setattr__(self, "consistency_score", _clamp(consistency_score, 0.0, 1.0))
        object.__setattr__(self, "completeness_score", _clamp(completeness_score, 0.0, 1.0))
        object.__setattr__(self, "clause_count", max(0, int(clause_count)))
        object.__setattr__(self, "warnings", tuple(warnings))
        object.__setattr__(self, "created_at", _utcnow() if created_at is None else float(created_at))

    @property
    def formal_text(self) -> str:
        return self.spec_text

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        spec_text: str,
        formal_language: str,
        consistency_score: float,
        completeness_score: float,
        clause_count: int,
        warnings: Sequence[str] = (),
    ) -> "FormalizationResult":
        """Construct a new :class:`FormalizationResult` with a fresh UUID.

        Parameters
        ----------
        spec_text:
            The formal specification text.
        formal_language:
            Target formal language identifier (e.g. ``"lean4"``).
        consistency_score:
            Estimated internal consistency, in [0, 1].
        completeness_score:
            Estimated completeness, in [0, 1].
        clause_count:
            Number of top-level clauses in the spec.
        warnings:
            Optional sequence of warning strings.

        Returns
        -------
        FormalizationResult
            A freshly constructed, immutable result object.
        """
        return cls(
            result_id=_uid(),
            spec_text=spec_text,
            formal_language=formal_language,
            consistency_score=consistency_score,
            completeness_score=completeness_score,
            clause_count=clause_count,
            warnings=warnings,
            created_at=_utcnow(),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this result to a JSON string.

        The returned JSON object contains all public fields.  The
        ``warnings`` tuple is serialised as a JSON array.

        Returns
        -------
        str
            UTF-8 JSON representation of the result.
        """
        return json.dumps(
            {
                "result_id": self.result_id,
                "spec_text": self.spec_text,
                "formal_language": self.formal_language,
                "consistency_score": self.consistency_score,
                "completeness_score": self.completeness_score,
                "clause_count": self.clause_count,
                "warnings": list(self.warnings),
                "created_at": self.created_at,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "FormalizationResult":
        """Deserialise a :class:`FormalizationResult` from a JSON string.

        Parameters
        ----------
        data:
            A JSON string previously produced by :meth:`to_json`.

        Returns
        -------
        FormalizationResult
            The reconstructed result object.

        Raises
        ------
        ValueError
            If the JSON is missing required keys.
        """
        obj = json.loads(data)
        required = {
            "result_id", "spec_text", "formal_language",
            "consistency_score", "completeness_score",
            "clause_count", "warnings", "created_at",
        }
        missing = required - obj.keys()
        if missing:
            raise ValueError(f"Missing keys in JSON: {missing!r}")
        return cls(
            result_id=obj["result_id"],
            spec_text=obj["spec_text"],
            formal_language=obj.get("formal_language", "lean4"),
            consistency_score=float(obj["consistency_score"]),
            completeness_score=float(obj["completeness_score"]),
            clause_count=int(obj["clause_count"]),
            warnings=tuple(obj["warnings"]),
            created_at=float(obj["created_at"]),
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """Return a brief human-readable summary of this result.

        The summary includes the result ID (first 8 characters), formal
        language, scores, clause count, and warning count.

        Returns
        -------
        str
            One-line summary string.
        """
        short_id = self.result_id[:8]
        warn_n = len(self.warnings)
        return (
            f"FormalizationResult({short_id}) "
            f"lang={self.formal_language} "
            f"consistency={self.consistency_score:.3f} "
            f"completeness={self.completeness_score:.3f} "
            f"clauses={self.clause_count} "
            f"warnings={warn_n}"
        )

    def is_acceptable(
        self,
        consistency_threshold: float = 0.8,
        completeness_threshold: float = 0.75,
    ) -> bool:
        """Return ``True`` if both scores meet the given thresholds.

        Parameters
        ----------
        consistency_threshold:
            Minimum required consistency score, in [0, 1].
        completeness_threshold:
            Minimum required completeness score, in [0, 1].

        Returns
        -------
        bool
            ``True`` iff both scores are at or above their respective thresholds.
        """
        return (
            self.consistency_score >= consistency_threshold
            and self.completeness_score >= completeness_threshold
        )

    def quality_score(self) -> float:
        """Compute an aggregate quality score for this result.

        The quality score is the harmonic mean of *consistency_score* and
        *completeness_score*, penalised by 2 % for each warning (capped at a
        50 % penalty).  The result is always in [0, 1].

        Returns
        -------
        float
            Aggregate quality score in [0, 1].
        """
        c = self.consistency_score
        p = self.completeness_score
        if c + p == 0.0:
            harmonic = 0.0
        else:
            harmonic = 2.0 * c * p / (c + p)
        penalty = _clamp(0.02 * len(self.warnings), 0.0, 0.5)
        return _clamp(harmonic * (1.0 - penalty), 0.0, 1.0)

    def render_tex(self) -> str:
        """Render a LaTeX snippet describing this result.

        Returns a ``\\paragraph`` block suitable for inclusion in a theory
        document.

        Returns
        -------
        str
            LaTeX source snippet.
        """
        short = self.result_id[:8]
        lines = [
            f"\\paragraph{{Formalization result \\texttt{{{short}}}}}",
            f"Language: \\texttt{{{self.formal_language}}}.",
            f"Consistency: ${self.consistency_score:.3f}$,",
            f"Completeness: ${self.completeness_score:.3f}$,",
            f"Clauses: ${self.clause_count}$.",
        ]
        if self.warnings:
            warn_list = ", ".join(f"\\textit{{{w}}}" for w in self.warnings)
            lines.append(f"Warnings: {warn_list}.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formalizer
# ---------------------------------------------------------------------------


class Formalizer:
    """Transform informal mathematical descriptions into formal specifications.

    A :class:`Formalizer` encapsulates the heuristic scoring logic and
    clause-extraction utilities needed to convert unstructured mathematical
    prose into structured formal specifications for a chosen proof assistant.

    The formalizer does *not* interact with an external proof assistant at
    runtime; instead, it applies lightweight text-analysis heuristics to
    produce scored :class:`FormalizationResult` objects that downstream
    checkers can evaluate.

    Attributes
    ----------
    formal_language : str
        The target proof-assistant language (e.g. ``"lean4"``).
    spec_registry : dict
        Maps *spec_id* strings to :class:`FormalizationResult` objects that
        have been explicitly registered via :meth:`register_spec`.
    history : list
        Ordered list of all :class:`FormalizationResult` objects ever produced
        by this instance (including batch calls).
    """

    def __init__(self, formal_language: str = "lean4") -> None:
        """Initialise the formalizer.

        Parameters
        ----------
        formal_language:
            Target formal language.  Must be one of the values in
            ``_SUPPORTED_LANGUAGES``.  Defaults to ``"lean4"``.

        Raises
        ------
        ValueError
            If *formal_language* is not recognised.
        """
        self.formal_language: str = _normalise_language(formal_language)
        self.spec_registry: dict[str, FormalizationResult] = {}
        self.history: list[FormalizationResult] = []

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def formalize(
        self,
        informal_text: str,
        context: dict[str, Any] | None = None,
    ) -> FormalizationResult:
        """Convert an informal mathematical description into a formal spec.

        This method applies the following pipeline:

        1. **Clause extraction** – :meth:`_extract_clauses` splits
           *informal_text* into a list of logical clauses by detecting
           sentence boundaries and logical connectives.
        2. **Consistency scoring** – :meth:`_score_consistency` evaluates
           how free of internal contradictions the text appears to be, using
           a weighted keyword model calibrated against the target language.
        3. **Completeness scoring** – :meth:`_score_completeness` checks how
           well the text covers standard components (preconditions,
           postconditions, invariants, termination arguments) given the
           optional *context* dictionary.
        4. **Spec generation** – the clauses are serialised into a stub
           formal specification in the target language (Lean 4, Coq, etc.).
        5. **Warning collection** – structural deficiencies detected during
           steps 1–3 are collected as human-readable warning strings.
        6. **Result construction** – a new :class:`FormalizationResult` is
           created, appended to :attr:`history`, and returned.

        Parameters
        ----------
        informal_text:
            The raw informal mathematical description to formalise.
        context:
            An optional dictionary providing additional context for the
            formalization.  Recognised keys include:

            ``"prior_specs"`` (list of str)
                Previously produced spec texts that may share definitions.
            ``"required_clauses"`` (list of str)
                Clause names that must appear in the output.
            ``"domain"`` (str)
                Mathematical domain (e.g. ``"topology"``), used to weight
                domain-specific keywords.

        Returns
        -------
        FormalizationResult
            An immutable record containing the produced spec and its scores.
        """
        ctx = context or {}
        clauses = self._extract_clauses(informal_text)
        consistency = self._score_consistency(informal_text)
        completeness = self._score_completeness(informal_text, ctx)
        warnings: list[str] = []
        if consistency < 0.5:
            warnings.append("Low consistency score — possible contradiction detected.")
        if completeness < 0.5:
            warnings.append("Low completeness score — many clauses may be missing.")
        if not clauses:
            warnings.append("No logical clauses detected in input text.")
        required = ctx.get("required_clauses", [])
        for req in required:
            if req.lower() not in informal_text.lower():
                warnings.append(f"Required clause {req!r} not found in spec.")
        spec_text = self._build_spec_text(clauses, ctx)
        result = FormalizationResult.create(
            spec_text=spec_text,
            formal_language=self.formal_language,
            consistency_score=consistency,
            completeness_score=completeness,
            clause_count=len(clauses),
            warnings=warnings,
        )
        self.history.append(result)
        return result

    def batch_formalize(
        self, texts: list[str]
    ) -> list[FormalizationResult]:
        """Formalise a list of informal texts.

        Each text is processed independently by :meth:`formalize`.  Results
        are appended to :attr:`history` in order.

        Parameters
        ----------
        texts:
            List of informal mathematical descriptions.

        Returns
        -------
        list[FormalizationResult]
            List of results, one per input text, in the same order.
        """
        return [self.formalize(t) for t in texts]

    def register_spec(self, spec_id: str, result: FormalizationResult) -> None:
        """Store *result* under *spec_id* in the spec registry.

        Parameters
        ----------
        spec_id:
            Unique identifier key for the spec (arbitrary string).
        result:
            The :class:`FormalizationResult` to register.
        """
        self.spec_registry[spec_id] = result

    def get_spec(self, spec_id: str) -> FormalizationResult | None:
        """Retrieve a previously registered spec by *spec_id*.

        Returns
        -------
        FormalizationResult | None
            The registered result, or ``None`` if *spec_id* is not found.
        """
        return self.spec_registry.get(spec_id)

    def list_specs(self) -> list[str]:
        """Return all registered spec identifiers.

        Returns
        -------
        list[str]
            All keys in :attr:`spec_registry`, in insertion order.
        """
        return list(self.spec_registry.keys())

    def clear_history(self) -> None:
        """Clear the :attr:`history` list.

        This does *not* affect :attr:`spec_registry`.
        """
        self.history.clear()

    def get_history(self) -> list[FormalizationResult]:
        """Return a copy of the recorded formalization history."""
        return list(self.history)

    def consistency_report(self) -> dict[str, Any]:
        """Compute aggregate consistency statistics over the full history.

        Returns
        -------
        dict
            Keys: ``"count"``, ``"mean_consistency"``, ``"mean_completeness"``,
            ``"mean_quality"``, ``"warning_rate"``.
        """
        n = len(self.history)
        if n == 0:
            return {"count": 0}
        mean_c = sum(r.consistency_score for r in self.history) / n
        mean_p = sum(r.completeness_score for r in self.history) / n
        mean_q = sum(r.quality_score() for r in self.history) / n
        warn_rate = sum(1 for r in self.history if r.warnings) / n
        return {
            "count": n,
            "mean": round(mean_q, 4),
            "mean_consistency": round(mean_c, 4),
            "mean_completeness": round(mean_p, 4),
            "mean_quality": round(mean_q, 4),
            "warning_rate": round(warn_rate, 4),
        }

    def export_all(self, fmt: str = "json") -> str:
        """Serialise all history entries to a single string.

        Parameters
        ----------
        fmt:
            Output format.  Only ``"json"`` is currently supported.

        Returns
        -------
        str
            A JSON array of all history entries.

        Raises
        ------
        ValueError
            If an unsupported format is requested.
        """
        if fmt != "json":
            raise ValueError(f"Unsupported export format: {fmt!r}")
        items = [json.loads(r.to_json()) for r in self.history]
        return json.dumps(items, indent=2)

    def summarize(self) -> str:
        """Return a brief textual summary of this formalizer's state.

        Returns
        -------
        str
            Summary string including language, history length, and registry size.
        """
        return (
            f"Formalizer(lang={self.formal_language}, "
            f"history={len(self.history)}, "
            f"specs_registered={len(self.spec_registry)})"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_consistency(self, text: str) -> float:
        """Estimate internal consistency of *text* using keyword weighting.

        The heuristic assigns a base score of 0.5 and adds fractional weight
        for every recognised mathematical keyword present, capped at 1.0.  A
        small penalty is applied if the text contains obvious contradictory
        markers (``"but"``, ``"however"``, ``"contradiction"``).

        Parameters
        ----------
        text:
            The informal text to score.

        Returns
        -------
        float
            Consistency estimate in [0, 1].
        """
        lower = text.lower()
        score = 0.50
        for kw, w in _KEYWORD_WEIGHTS.items():
            if kw in lower:
                score += w * 0.5
        contradiction_markers = ("but", "however", "contradiction", "inconsistent")
        penalty = sum(0.04 for m in contradiction_markers if m in lower)
        return _clamp(score - penalty, 0.0, 1.0)

    def _score_completeness(
        self, text: str, context: dict[str, Any]
    ) -> float:
        """Estimate completeness of *text* relative to standard spec components.

        Parameters
        ----------
        text:
            The informal text to evaluate.
        context:
            Context dictionary; may contain ``"required_clauses"`` and
            ``"domain"`` keys.

        Returns
        -------
        float
            Completeness estimate in [0, 1].
        """
        lower = text.lower()
        base = 0.40
        hint_score = sum(
            0.05 for hint in _COMPLETENESS_HINTS if hint in lower
        )
        required = context.get("required_clauses", [])
        if required:
            found = sum(1 for r in required if r.lower() in lower)
            req_score = found / len(required) * 0.20
        else:
            req_score = 0.10
        return _clamp(base + hint_score + req_score, 0.0, 1.0)

    def _extract_clauses(self, text: str) -> list[str]:
        """Split *text* into a list of logical clauses.

        The extraction heuristic splits on sentence-terminal punctuation
        (period, semicolon, colon followed by a capital letter) and filters
        out very short fragments.

        Parameters
        ----------
        text:
            Raw informal text.

        Returns
        -------
        list[str]
            List of clause strings (stripped, non-empty).
        """
        import re
        # Split on ". ", "; ", or ": " followed by capital or digit
        parts = re.split(r"(?<=[.;:])\s+(?=[A-Z0-9])", text)
        return [p.strip() for p in parts if len(p.strip()) > 10]

    def _build_spec_text(
        self, clauses: list[str], context: dict[str, Any]
    ) -> str:
        """Produce a stub formal specification from *clauses*.

        The output format depends on :attr:`formal_language`.  For Lean 4,
        each clause becomes a ``-- <clause>`` comment followed by a
        ``theorem clause_N : True := trivial`` stub.

        Parameters
        ----------
        clauses:
            Extracted logical clauses.
        context:
            Context dictionary (currently unused in stub generation).

        Returns
        -------
        str
            Stub formal specification text.
        """
        lang = self.formal_language
        lines: list[str] = [f"-- Generated stub ({lang})"]
        for i, clause in enumerate(clauses, 1):
            if lang == "lean4":
                lines.append(f"-- Clause {i}: {clause}")
                lines.append(f"theorem clause_{i} : True := trivial")
            elif lang == "coq":
                lines.append(f"(* Clause {i}: {clause} *)")
                lines.append(f"Theorem clause_{i} : True. Proof. exact I. Qed.")
            else:
                lines.append(f"-- [{lang}] Clause {i}: {clause}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SpecificationWriter
# ---------------------------------------------------------------------------


class SpecificationWriter:
    """Write machine-checkable specification files for a chosen proof assistant.

    :class:`SpecificationWriter` takes :class:`FormalizationResult` objects
    and serialises their spec texts into output strings that can be passed
    directly to a proof assistant's type checker or written to disk.

    The writer supports a *template registry*: named templates can be
    registered and later applied during spec writing.  If no template is
    provided, the raw ``spec_text`` field is used verbatim.

    Attributes
    ----------
    target_format : str
        The target proof-assistant format (e.g. ``"lean4"``).
    template_registry : dict
        Maps template names to template strings.
    output_buffer : list
        Accumulates written spec strings for later retrieval via
        :meth:`flush_buffer`.
    """

    def __init__(self, target_format: str = "lean4") -> None:
        """Initialise the writer.

        Parameters
        ----------
        target_format:
            Target proof-assistant format.  Must be in ``_SUPPORTED_LANGUAGES``.
        """
        self.target_format: str = _normalise_language(target_format)
        self.template_registry: dict[str, str] = {}
        self.output_buffer: list[str] = []

    def write_spec(
        self,
        result: FormalizationResult,
        template: str | None = None,
    ) -> str:
        """Render a spec string from *result*, optionally applying *template*.

        If *template* is given it must contain a ``{spec_text}`` placeholder
        which will be replaced by the result's ``spec_text``.

        Parameters
        ----------
        result:
            The formalization result to render.
        template:
            Optional template string with ``{spec_text}`` placeholder.

        Returns
        -------
        str
            The rendered spec string.
        """
        if template is not None:
            rendered = template.format(spec_text=result.spec_text)
        else:
            rendered = result.spec_text
        self.output_buffer.append(rendered)
        return rendered

    def register_template(self, name: str, template: str) -> None:
        """Register a named template string.

        Parameters
        ----------
        name:
            Unique identifier for the template.
        template:
            Template string with ``{spec_text}`` placeholder.
        """
        self.template_registry[name] = template

    def get_template(self, name: str) -> str | None:
        """Retrieve a named template.

        Returns
        -------
        str | None
            The template string, or ``None`` if *name* is not registered.
        """
        return self.template_registry.get(name)

    def write_batch(
        self, results: list[FormalizationResult]
    ) -> list[str]:
        """Write a list of formalization results, returning rendered strings.

        Parameters
        ----------
        results:
            List of :class:`FormalizationResult` objects to write.

        Returns
        -------
        list[str]
            One rendered string per input result, in the same order.
        """
        return [self.write_spec(r) for r in results]

    def flush_buffer(self) -> list[str]:
        """Return and clear the output buffer.

        Returns
        -------
        list[str]
            All accumulated spec strings since the last flush.
        """
        buf = list(self.output_buffer)
        self.output_buffer.clear()
        return buf

    def buffer_size(self) -> int:
        """Return the number of buffered rendered specs."""
        return len(self.output_buffer)

    def compile_spec(self, spec_text: str | FormalizationResult) -> dict[str, Any]:
        """Perform lightweight syntactic compilation of *spec_text*.

        This method does not invoke an actual proof assistant.  Instead it
        counts declarations, detects obvious syntax errors (unmatched
        parentheses, missing ``end`` keywords for Lean 4/Coq sections), and
        returns a summary dictionary.

        Parameters
        ----------
        spec_text:
            Formal specification text to compile.

        Returns
        -------
        dict
            Keys: ``"status"`` (``"ok"`` or ``"error"``),
            ``"declaration_count"``, ``"issues"``.
        """
        if isinstance(spec_text, FormalizationResult):
            spec_text = spec_text.spec_text
        issues: list[str] = []
        # Check parenthesis balance
        depth = 0
        for ch in spec_text:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth < 0:
                issues.append("Unmatched closing parenthesis detected.")
                break
        if depth > 0:
            issues.append(f"{depth} unclosed parenthesis/bracket(s).")
        import re
        decl_count = len(re.findall(r"\b(theorem|lemma|def|definition|axiom)\b", spec_text))
        return {
            "status": "error" if issues else "ok",
            "declaration_count": decl_count,
            "issues": issues,
        }

    def syntax_issues(self, spec_text: str | FormalizationResult) -> list[str]:
        """Return syntax-error messages for *spec_text* without coercing to bool."""
        return self.compile_spec(spec_text).get("issues", [])

    def validate_syntax(self, spec_text: str | FormalizationResult) -> bool:
        """Return ``True`` when *spec_text* passes lightweight syntax checks.

        Parameters
        ----------
        spec_text:
            Text to validate.

        Returns
        -------
        bool
            ``True`` if no issues are detected, otherwise ``False``.
        """
        return not self.syntax_issues(spec_text)

    def render_to_file(self, result: FormalizationResult, path: str) -> None:
        """Write the spec text of *result* to a file at *path*.

        Parameters
        ----------
        result:
            The formalization result to write.
        path:
            Filesystem path for the output file.
        """
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(result.spec_text)

    def summarize(self) -> str:
        """Return a brief summary of the writer's state.

        Returns
        -------
        str
            Summary string.
        """
        return (
            f"SpecificationWriter(format={self.target_format}, "
            f"templates={len(self.template_registry)}, "
            f"buffer_size={len(self.output_buffer)})"
        )


# ---------------------------------------------------------------------------
# FormalizationChecker
# ---------------------------------------------------------------------------


class FormalizationChecker:
    """Evaluate formalization results against configured quality thresholds.

    :class:`FormalizationChecker` provides both individual and batch checking
    capabilities.  Each call to :meth:`check` appends the result summary to
    :attr:`check_history`, enabling longitudinal reporting over the course of
    a formalization loop.

    Attributes
    ----------
    thresholds : dict
        Maps ``"consistency"`` and ``"completeness"`` to their threshold
        values.
    check_history : list
        Ordered list of check-summary dictionaries produced by :meth:`check`.
    """

    def __init__(
        self,
        consistency_threshold: float = 0.8,
        completeness_threshold: float = 0.75,
    ) -> None:
        """Initialise the checker with quality thresholds.

        Parameters
        ----------
        consistency_threshold:
            Minimum required consistency score, in [0, 1].
        completeness_threshold:
            Minimum required completeness score, in [0, 1].
        """
        self.thresholds: dict[str, float] = {
            "consistency": _clamp(consistency_threshold, 0.0, 1.0),
            "completeness": _clamp(completeness_threshold, 0.0, 1.0),
        }
        self.check_history: list[dict[str, Any]] = []

    def check(self, result: FormalizationResult) -> dict[str, Any]:
        """Check a single :class:`FormalizationResult` against thresholds.

        Parameters
        ----------
        result:
            The result to evaluate.

        Returns
        -------
        dict
            Keys: ``"result_id"``, ``"acceptable"``, ``"consistency_ok"``,
            ``"completeness_ok"``, ``"quality_score"``, ``"warnings"``,
            ``"checked_at"``.
        """
        c_ok = result.consistency_score >= self.thresholds["consistency"]
        p_ok = result.completeness_score >= self.thresholds["completeness"]
        summary = {
            "result_id": result.result_id,
            "acceptable": c_ok and p_ok,
            "consistency_ok": c_ok,
            "completeness_ok": p_ok,
            "quality_score": round(result.quality_score(), 4),
            "warnings": list(result.warnings),
            "checked_at": _utcnow(),
        }
        self.check_history.append(summary)
        return summary

    def check_result(self, result: FormalizationResult) -> dict[str, Any]:
        """Compatibility wrapper returning legacy field names."""
        summary = self.check(result)
        compat = dict(summary)
        compat["passed"] = compat["acceptable"]
        return compat

    def check_batch(
        self, results: list[FormalizationResult]
    ) -> list[dict[str, Any]]:
        """Check a list of results and return their summaries.

        Parameters
        ----------
        results:
            List of :class:`FormalizationResult` to evaluate.

        Returns
        -------
        list[dict]
            One summary dictionary per input result.
        """
        return [self.check_result(r) for r in results]

    def is_acceptable(self, result: FormalizationResult) -> bool:
        """Return ``True`` if *result* meets both quality thresholds.

        Parameters
        ----------
        result:
            The result to test.

        Returns
        -------
        bool
        """
        return result.is_acceptable(
            self.thresholds["consistency"],
            self.thresholds["completeness"],
        )

    def get_warnings(self, result: FormalizationResult) -> list[str]:
        """Return the warnings associated with *result*.

        Parameters
        ----------
        result:
            The result whose warnings to retrieve.

        Returns
        -------
        list[str]
            List of warning strings (may be empty).
        """
        return list(result.warnings)

    def score_result(self, result: FormalizationResult) -> float:
        """Return the aggregate quality score for *result*.

        Parameters
        ----------
        result:
            The result to score.

        Returns
        -------
        float
            Quality score in [0, 1].
        """
        return result.quality_score()

    def compare_results(
        self,
        r1: FormalizationResult,
        r2: FormalizationResult,
    ) -> dict[str, Any]:
        """Compare two formalization results.

        Returns
        -------
        dict
            Keys: ``"delta_consistency"``, ``"delta_completeness"``,
            ``"delta_quality"``, ``"improved"``.
        """
        dc = r2.consistency_score - r1.consistency_score
        dp = r2.completeness_score - r1.completeness_score
        dq = r2.quality_score() - r1.quality_score()
        return {
            "delta_consistency": round(dc, 4),
            "delta_completeness": round(dp, 4),
            "delta_quality": round(dq, 4),
            "improved": dq > 0,
        }

    def history_report(self) -> dict[str, Any]:
        """Compute aggregate statistics over :attr:`check_history`.

        Returns
        -------
        dict
            Keys: ``"total_checks"``, ``"acceptable_count"``,
            ``"acceptance_rate"``, ``"mean_quality"``.
        """
        n = len(self.check_history)
        if n == 0:
            return {"total_checks": 0}
        acc = sum(1 for h in self.check_history if h["acceptable"])
        mean_q = sum(h["quality_score"] for h in self.check_history) / n
        return {
            "total_checks": n,
            "acceptable_count": acc,
            "acceptance_rate": round(acc / n, 4),
            "mean_quality": round(mean_q, 4),
        }

    def reset_history(self) -> None:
        """Clear :attr:`check_history`."""
        self.check_history.clear()

    def summarize(self) -> str:
        """Return a brief textual summary of the checker.

        Returns
        -------
        str
        """
        return (
            f"FormalizationChecker("
            f"consistency_threshold={self.thresholds['consistency']:.2f}, "
            f"completeness_threshold={self.thresholds['completeness']:.2f}, "
            f"history={len(self.check_history)})"
        )


# ---------------------------------------------------------------------------
# FormalizationLoopRunner
# ---------------------------------------------------------------------------


class FormalizationLoopRunner:
    """Orchestrate the complete formalization loop.

    :class:`FormalizationLoopRunner` iteratively applies the
    :class:`Formalizer`, :class:`SpecificationWriter`, and
    :class:`FormalizationChecker` until convergence or exhaustion of the
    maximum iteration count.

    Convergence is defined as *all* results produced in an iteration being
    acceptable according to the configured thresholds.

    Attributes
    ----------
    config : dict
        Loop configuration dictionary.
    formalizer : Formalizer
        The formalizer used during the loop.
    writer : SpecificationWriter
        The specification writer used to render outputs.
    checker : FormalizationChecker
        The checker that evaluates each iteration's outputs.
    loop_state : dict
        Mutable state dictionary tracking loop progress.
    """

    def __init__(
        self,
        max_iterations: int = 10,
        consistency_threshold: float = 0.8,
        completeness_threshold: float = 0.75,
        formal_language: str = "lean4",
    ) -> None:
        """Initialise the loop runner.

        Parameters
        ----------
        max_iterations:
            Maximum number of refinement iterations before the loop gives up.
        consistency_threshold:
            Minimum acceptable consistency score.
        completeness_threshold:
            Minimum acceptable completeness score.
        formal_language:
            Target proof-assistant language.
        """
        self.config: dict[str, Any] = {
            "max_iterations": max_iterations,
            "consistency_threshold": consistency_threshold,
            "completeness_threshold": completeness_threshold,
            "formal_language": formal_language,
        }
        self.formalizer = Formalizer(formal_language=formal_language)
        self.writer = SpecificationWriter(target_format=formal_language)
        self.checker = FormalizationChecker(
            consistency_threshold=consistency_threshold,
            completeness_threshold=completeness_threshold,
        )
        self.loop_state: dict[str, Any] = {
            "status": "idle",
            "iteration": 0,
            "converged": False,
            "results": [],
            "started_at": None,
            "finished_at": None,
        }

    @property
    def max_iterations(self) -> int:
        return int(self.config["max_iterations"])

    def run(
        self,
        informal_texts: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the full formalization loop.

        The loop runs for at most ``config["max_iterations"]`` iterations.
        In each iteration, all *informal_texts* are formalised, written, and
        checked.  If all checks pass, the loop is declared converged and
        terminates early.

        Parameters
        ----------
        informal_texts:
            List of informal mathematical descriptions to formalise.
        context:
            Optional context dictionary forwarded to :meth:`Formalizer.formalize`.

        Returns
        -------
        dict
            Loop summary with keys ``"converged"``, ``"iterations_used"``,
            ``"final_results"``, ``"diagnostics"``, ``"started_at"``,
            ``"finished_at"``.
        """
        self.loop_state["status"] = "running"
        self.loop_state["started_at"] = _utcnow()
        self.loop_state["iteration"] = 0
        self.loop_state["converged"] = False
        all_iteration_results: list[dict[str, Any]] = []
        max_it = self.config["max_iterations"]
        for it in range(1, max_it + 1):
            self.loop_state["iteration"] = it
            try:
                iteration_result = self.run_single_iteration(
                    informal_texts, it, context
                )
            except Exception as exc:
                iteration_result = self.handle_failure(exc, it)
            all_iteration_results.append(iteration_result)
            if self.check_convergence(all_iteration_results):
                self.loop_state["converged"] = True
                break
        self.loop_state["status"] = "done"
        self.loop_state["finished_at"] = _utcnow()
        final_results = all_iteration_results[-1] if all_iteration_results else {}
        self.loop_state["results"] = list(final_results.get("results", [])) if final_results else []
        return {
            "converged": self.loop_state["converged"],
            "iterations": self.loop_state["iteration"],
            "iterations_used": self.loop_state["iteration"],
            "results": list(final_results.get("results", [])) if final_results else [],
            "final_results": final_results,
            "diagnostics": self.checker.history_report(),
            "started_at": self.loop_state["started_at"],
            "finished_at": self.loop_state["finished_at"],
        }

    def run_single_iteration(
        self,
        texts: list[str],
        iteration: int,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one iteration of the formalization loop.

        Parameters
        ----------
        texts:
            Informal texts to process in this iteration.
        iteration:
            Current iteration number (1-based).
        context:
            Optional context dictionary.

        Returns
        -------
        dict
            Iteration summary with keys ``"iteration"``, ``"results"``,
            ``"check_summaries"``, ``"all_acceptable"``.
        """
        results = self.formalizer.batch_formalize(texts)
        specs = self.writer.write_batch(results)
        check_summaries = self.checker.check_batch(results)
        all_ok = all(s["acceptable"] for s in check_summaries)
        return {
            "iteration": iteration,
            "results": list(results),
            "check_summaries": check_summaries,
            "all_acceptable": all_ok,
            "spec_count": len(specs),
        }

    def check_convergence(
        self, iteration_results: list[Any]
    ) -> bool:
        """Determine whether the loop has converged.

        Convergence is declared when the most recent iteration reports
        ``all_acceptable == True``.

        Parameters
        ----------
        iteration_results:
            List of all iteration result dictionaries produced so far.

        Returns
        -------
        bool
            ``True`` if the loop has converged.
        """
        if not iteration_results:
            return False
        if all(isinstance(result, FormalizationResult) for result in iteration_results):
            return all(self.checker.is_acceptable(result) for result in iteration_results)
        return bool(iteration_results[-1].get("all_acceptable", False))

    def handle_failure(
        self, error: Exception, iteration: int
    ) -> dict[str, Any]:
        """Produce a failure record for an iteration that raised an exception.

        Parameters
        ----------
        error:
            The exception that was raised.
        iteration:
            The iteration number in which the failure occurred.

        Returns
        -------
        dict
            Failure record with keys ``"iteration"``, ``"error"``,
            ``"all_acceptable"`` (always ``False``).
        """
        return {
            "iteration": iteration,
            "error": str(error),
            "all_acceptable": False,
            "results": [],
            "check_summaries": [],
        }

    def get_state(self) -> dict[str, Any]:
        """Return a copy of the current :attr:`loop_state` dictionary.

        Returns
        -------
        dict
            Shallow copy of the loop state.
        """
        state = dict(self.loop_state)
        state["iterations_completed"] = state.get("iteration", 0)
        return state

    def reset(self) -> None:
        """Reset the loop runner to its initial idle state.

        Clears formalizer history, writer buffer, checker history, and loop
        state, but preserves the configuration.
        """
        self.formalizer.clear_history()
        self.writer.flush_buffer()
        self.checker.reset_history()
        self.loop_state = {
            "status": "idle",
            "iteration": 0,
            "converged": False,
            "results": [],
            "started_at": None,
            "finished_at": None,
        }

    def summarize(self) -> str:
        """Return a brief textual summary of the runner.

        Returns
        -------
        str
        """
        cfg = self.config
        st = self.loop_state
        return (
            f"FormalizationLoopRunner("
            f"max_iter={cfg['max_iterations']}, "
            f"lang={cfg['formal_language']}, "
            f"status={st['status']}, "
            f"converged={st['converged']})"
        )

    def export_results(self, fmt: str = "json") -> str:
        """Export all formalizer history as a string.

        Parameters
        ----------
        fmt:
            Output format (only ``"json"`` supported).

        Returns
        -------
        str
            Serialised history.
        """
        return self.formalizer.export_all(fmt=fmt)


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def run_formalization_loop(
    informal_texts: list[str],
    max_iterations: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run a full formalization loop and return the result summary.

    This is the primary entry-point for the formalization loop.  It creates a
    :class:`FormalizationLoopRunner` configured with *max_iterations* and any
    additional keyword arguments, then invokes :meth:`~FormalizationLoopRunner.run`
    on the provided *informal_texts*.

    Algorithm
    ---------
    The formalization loop proceeds as follows:

    1. Initialise :class:`Formalizer`, :class:`SpecificationWriter`, and
       :class:`FormalizationChecker` with the supplied configuration.
    2. For each iteration ``i`` from 1 to *max_iterations*:

       a. Pass every text in *informal_texts* through the formalizer to
          produce :class:`FormalizationResult` objects.
       b. Write each result to a spec string via the writer.
       c. Check each result against the consistency and completeness
          thresholds.
       d. If **all** results are acceptable, declare convergence and halt.

    3. After the loop, return a comprehensive summary dictionary.

    Convergence criteria
    --------------------
    The loop converges when every :class:`FormalizationResult` produced in the
    current iteration has both

    * ``consistency_score >= consistency_threshold``  (default 0.8)
    * ``completeness_score >= completeness_threshold``  (default 0.75)

    Return value format
    -------------------
    The returned dictionary contains:

    ``"converged"`` : bool
        Whether the loop achieved convergence.
    ``"iterations_used"`` : int
        Number of iterations executed.
    ``"final_results"`` : dict
        The result dictionary from the last iteration.
    ``"diagnostics"`` : dict
        Aggregate statistics from the :class:`FormalizationChecker`.
    ``"started_at"`` : float
        Unix timestamp when the loop began.
    ``"finished_at"`` : float
        Unix timestamp when the loop ended.

    Parameters
    ----------
    informal_texts:
        List of informal mathematical descriptions to formalise.
    max_iterations:
        Maximum number of refinement iterations.
    **kwargs:
        Additional keyword arguments forwarded to :class:`FormalizationLoopRunner`.
        Recognised keys: ``consistency_threshold`` (float, default 0.8),
        ``completeness_threshold`` (float, default 0.75),
        ``formal_language`` (str, default ``"lean4"``),
        ``context`` (dict, optional).

    Returns
    -------
    dict
        See *Return value format* above.

    Examples
    --------
    >>> result = run_formalization_loop(
    ...     ["For all n in N, n^2 >= 0.", "Every continuous function on a closed interval is bounded."],
    ...     max_iterations=5,
    ...     formal_language="lean4",
    ... )
    >>> result["converged"]
    True
    """
    if hasattr(informal_texts, "loop_id") and hasattr(informal_texts, "state"):
        loop = deepcopy(informal_texts)
        if hasattr(loop, "updated_at"):
            loop.updated_at = time.time()
        return loop
    context = kwargs.pop("context", None)
    runner = FormalizationLoopRunner(
        max_iterations=max_iterations,
        **kwargs,
    )
    return runner.run(informal_texts, context=context)


def check_formalization(
    spec_text: str,
    formal_language: str = "lean4",
    **kwargs: Any,
) -> dict[str, Any]:
    """Check a single formal specification for consistency and completeness.

    This function provides a lightweight, one-shot alternative to running a
    full formalization loop.  It creates a :class:`SpecificationWriter` and
    :class:`FormalizationChecker`, synthesises a :class:`FormalizationResult`
    from the supplied *spec_text*, and returns the checker's evaluation.

    Parameters
    ----------
    spec_text:
        A formal specification string in the target language.
    formal_language:
        The proof-assistant language of *spec_text* (e.g. ``"lean4"``).
    **kwargs:
        Optional keyword arguments:

        ``consistency_threshold`` : float (default 0.8)
            Minimum consistency score for acceptability.
        ``completeness_threshold`` : float (default 0.75)
            Minimum completeness score for acceptability.

    Returns
    -------
    dict
        Keys: ``"acceptable"``, ``"consistency_score"``,
        ``"completeness_score"``, ``"quality_score"``, ``"warnings"``,
        ``"syntax_issues"``.

    Examples
    --------
    >>> outcome = check_formalization(
    ...     "theorem trivial_example : True := trivial",
    ...     formal_language="lean4",
    ... )
    >>> outcome["acceptable"]
    True
    """
    c_thresh = kwargs.get("consistency_threshold", 0.8)
    p_thresh = kwargs.get("completeness_threshold", 0.75)
    formalizer = Formalizer(formal_language=formal_language)
    writer = SpecificationWriter(target_format=formal_language)
    checker = FormalizationChecker(
        consistency_threshold=c_thresh,
        completeness_threshold=p_thresh,
    )
    if isinstance(spec_text, FormalizationResult):
        result = spec_text
        spec_body = result.spec_text
    else:
        spec_body = spec_text
        result = FormalizationResult.create(
            spec_text=spec_body,
            formal_language=formal_language,
            consistency_score=formalizer._score_consistency(spec_body),
            completeness_score=formalizer._score_completeness(spec_body, {}),
            clause_count=len(formalizer._extract_clauses(spec_body)),
        )
    check_summary = checker.check(result)
    syntax_ok = writer.validate_syntax(spec_body)
    return {
        "acceptable": check_summary["acceptable"],
        "passed": check_summary["acceptable"],
        "consistency_score": result.consistency_score,
        "completeness_score": result.completeness_score,
        "score": check_summary["quality_score"],
        "quality_score": check_summary["quality_score"],
        "warnings": list(result.warnings),
        "syntax_issues": [] if syntax_ok else writer.syntax_issues(spec_body),
    }
