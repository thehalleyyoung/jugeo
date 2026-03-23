"""documentation_alignment.py — DocumentationAlignmentCoordinator

# copilot: documentation_alignment.py — DocumentationAlignmentCoordinator for Ch13

Theory context (theory2.tex §13.2 – Documentation Alignment Invariants)
========================================================================
In the JuGeo framework, *documentation alignment* is the property that a
function's docstring and its implementation are mutually consistent.  Formally,
given a docstring specification 𝒟_f and an implementation Impl_f, we define the
*alignment gap set*

    Γ(f) = {γ | γ is an observable discrepancy between 𝒟_f and Impl_f}

The alignment is *sound* iff Γ(f) = ∅.  Each element of Γ(f) is an
obstruction in the Čech cohomology group Ȟ¹(Cover_f, 𝒞) where 𝒞 is the
sheaf of declared contracts over the nerve of the function's signature cover.

Key theorems referenced from theory2.tex Ch13
----------------------------------------------
Theorem 13.2.1 (Alignment Gap as Obstruction):
    Every alignment gap γ ∈ Γ(f) corresponds to a non-trivial cohomology
    class [γ] ∈ Ȟ¹(Cover_f, 𝒞).  Resolving the gap (updating the docstring
    or the implementation) collapses [γ] to zero.

Theorem 13.2.2 (Monotone Alignment Score):
    The alignment score α(f) ∈ [0,1] is monotone-decreasing in |Γ(f)|:
        α(f) = max(0, 1 − Σ_γ w(γ))
    where w(γ) is the severity weight of gap γ.  CRITICAL gaps have w = 0.5,
    HIGH have w = 0.2, MEDIUM have w = 0.1, LOW have w = 0.05.

Theorem 13.2.3 (Module Alignment Decomposition):
    A module M is *aligned* iff every exported function is aligned.  The
    module-level alignment score is the arithmetic mean of per-function scores,
    and the module witness is the colimit of per-function witnesses in the
    category of alignment records.

Corollary 13.2.4 (Grade Monotonicity):
    The letter grade assigned by alignment_grade() is anti-monotone in the gap
    count: more gaps ⟹ lower grade.  Grades are: A (≥0.9), B (≥0.75),
    C (≥0.6), D (≥0.4), F (<0.4).

Public-Alignment stage: ch13-public-alignment
Sequence: 2
Semantic source: preliminaries/theory2.tex
"""
from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# §0  Conditional jugeo imports (try/except pattern required by codebase)
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        TrustLevel,
        Proposition,
        Carrier,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        ResidualObligation,
        Obstruction,
        TrustAnnotation,
        Provenance,
        ProvenanceSource,
        JudgmentAlgebra,
        JudgmentStatus,
    )
except ImportError:
    Judgment = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    Proposition = Any  # type: ignore[assignment,misc]
    Carrier = Any  # type: ignore[assignment,misc]
    EvidenceBundle = Any  # type: ignore[assignment,misc]
    EvidenceItem = Any  # type: ignore[assignment,misc]
    EvidenceItemKind = Any  # type: ignore[assignment,misc]
    ResidualObligation = Any  # type: ignore[assignment,misc]
    Obstruction = Any  # type: ignore[assignment,misc]
    TrustAnnotation = Any  # type: ignore[assignment,misc]
    Provenance = Any  # type: ignore[assignment,misc]
    ProvenanceSource = Any  # type: ignore[assignment,misc]
    JudgmentAlgebra = Any  # type: ignore[assignment,misc]
    JudgmentStatus = Any  # type: ignore[assignment,misc]

try:
    from jugeo.errors import (
        StructuredFailure,
        JuGeoError,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        FailureChain,
        as_failure_payload,
        raise_with_scope,
    )
except ImportError:
    StructuredFailure = Any  # type: ignore[assignment,misc]
    JuGeoError = Any  # type: ignore[assignment,misc]
    FailureScope = Any  # type: ignore[assignment,misc]
    FailureClassification = Any  # type: ignore[assignment,misc]
    EvidenceFamily = Any  # type: ignore[assignment,misc]
    ObstructionRecord = Any  # type: ignore[assignment,misc]
    RepairHint = Any  # type: ignore[assignment,misc]
    RepairPriority = Any  # type: ignore[assignment,misc]
    FailureChain = Any  # type: ignore[assignment,misc]
    as_failure_payload = Any  # type: ignore[assignment,misc]
    raise_with_scope = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.public_alignment.models import (
        PublicClaim,
        HonestProjection,
        DocumentationSection,
        MigrationPlan,
        _now_iso,
        _new_id,
    )
except ImportError:
    PublicClaim = Any  # type: ignore[assignment,misc]
    HonestProjection = Any  # type: ignore[assignment,misc]
    DocumentationSection = Any  # type: ignore[assignment,misc]
    MigrationPlan = Any  # type: ignore[assignment,misc]

    def _now_iso() -> str:
        """Return current UTC time as ISO-8601 string (fallback implementation)."""
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _new_id(prefix: str = "id") -> str:
        """Return a short random identifier with *prefix* (fallback implementation)."""
        import uuid
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

# ---------------------------------------------------------------------------
# §1  Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# §2  Module-level provenance constant
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch13-public-alignment",
    "sequence": 2,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "documentation_alignment",
    "class": "DocumentationAlignmentCoordinator",
    "theory_section": "§13.2 – Documentation Alignment Invariants",
}

# ---------------------------------------------------------------------------
# §3  Severity weights (used in alignment score calculation per Theorem 13.2.2)
# ---------------------------------------------------------------------------

_SEVERITY_WEIGHTS: dict[str, float] = {
    "CRITICAL": 0.5,
    "HIGH": 0.2,
    "MEDIUM": 0.1,
    "LOW": 0.05,
}

# ---------------------------------------------------------------------------
# §4  Module-level helper functions
# ---------------------------------------------------------------------------


def _now_iso() -> str:  # type: ignore[no-redef]
    """Return the current UTC moment as an ISO-8601 timestamp string.

    Used throughout this module wherever a creation or update timestamp is
    needed.  The format is ``YYYY-MM-DDTHH:MM:SSZ`` (always UTC, Zulu suffix).

    Returns
    -------
    str
        Current UTC time formatted as ``%Y-%m-%dT%H:%M:%SZ``.

    Examples
    --------
    >>> ts = _now_iso()
    >>> ts.endswith("Z")
    True
    """
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str = "id") -> str:  # type: ignore[no-redef]
    """Generate a short random identifier with an optional *prefix*.

    The identifier is ``prefix-<12 hex chars>`` so it is human-readable and
    collision-resistant for typical module sizes.

    Parameters
    ----------
    prefix:
        A short label prepended to the hex string (e.g. ``"gap"``,
        ``"spec"``, ``"witness"``).

    Returns
    -------
    str
        A string of the form ``"<prefix>-<12 hex characters>"``.

    Examples
    --------
    >>> _id = _new_id("gap")
    >>> _id.startswith("gap-")
    True
    >>> len(_id) == len("gap-") + 12
    True
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _severity_weight(severity: str) -> float:
    """Return the numeric weight for a severity string.

    Parameters
    ----------
    severity:
        One of ``"CRITICAL"``, ``"HIGH"``, ``"MEDIUM"``, ``"LOW"``.
        Unknown values are treated as ``"LOW"``.

    Returns
    -------
    float
        The weight value as per Theorem 13.2.2 severity table.
    """
    return _SEVERITY_WEIGHTS.get(severity.upper(), 0.05)


def _compute_alignment_score(gaps: list[Any]) -> float:
    """Compute the alignment score given a list of AlignmentGap instances.

    Implements the formula from Theorem 13.2.2:

        α = max(0, 1 − Σ_γ w(γ))

    Parameters
    ----------
    gaps:
        List of :class:`AlignmentGap` instances (or dicts with ``severity``).

    Returns
    -------
    float
        A value in ``[0.0, 1.0]``.
    """
    total_weight = 0.0
    for gap in gaps:
        if hasattr(gap, "severity"):
            total_weight += _severity_weight(gap.severity)
        elif isinstance(gap, dict):
            total_weight += _severity_weight(str(gap.get("severity", "LOW")))
    return max(0.0, 1.0 - total_weight)


def _grade_from_score(score: float) -> str:
    """Map a float alignment score to a letter grade.

    Implements the grade monotonicity from Corollary 13.2.4:
    - A: score ≥ 0.90
    - B: score ≥ 0.75
    - C: score ≥ 0.60
    - D: score ≥ 0.40
    - F: score < 0.40

    Parameters
    ----------
    score:
        A float in ``[0.0, 1.0]``.

    Returns
    -------
    str
        One of ``"A"``, ``"B"``, ``"C"``, ``"D"``, ``"F"``.
    """
    if score >= 0.90:
        return "A"
    if score >= 0.75:
        return "B"
    if score >= 0.60:
        return "C"
    if score >= 0.40:
        return "D"
    return "F"


def _normalise_param_name(name: str) -> str:
    """Strip leading asterisks and whitespace from a parameter name.

    Parameters
    ----------
    name:
        Raw parameter token, possibly ``"*args"`` or ``"**kwargs"``.

    Returns
    -------
    str
        Cleaned name suitable for set membership tests.
    """
    return name.strip().lstrip("*")


def _parse_def_signature(source: str, function_name: str) -> list[str]:
    """Extract parameter names from the *function_name* ``def`` statement.

    Searches *source* for the first ``def function_name(...)`` and parses
    its parameters using a best-effort regex.

    Parameters
    ----------
    source:
        Python source text to search.
    function_name:
        The exact function name to look for.

    Returns
    -------
    list[str]
        Parameter names, excluding ``self`` and ``cls``.
    """
    pattern = re.compile(
        r"def\s+" + re.escape(function_name) + r"\s*\(([^)]*)\)",
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return []
    raw = match.group(1)
    params: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        # strip type annotations and defaults
        name = token.split(":")[0].split("=")[0].strip()
        name = _normalise_param_name(name)
        if name and name not in {"self", "cls"}:
            params.append(name)
    return params


def _find_raise_names(source: str) -> list[str]:
    """Find all exception names raised in *source*.

    Uses a regex to find ``raise ExcName`` and ``raise ExcName(`` patterns.

    Parameters
    ----------
    source:
        Python source text.

    Returns
    -------
    list[str]
        Unique exception class names in order of first appearance.
    """
    pattern = re.compile(r"\braise\s+([A-Za-z_][A-Za-z0-9_]*)")
    return list(dict.fromkeys(pattern.findall(source)))


def _has_return_statement(source: str) -> bool:
    """Return True if *source* contains at least one ``return <value>`` statement.

    Parameters
    ----------
    source:
        Python source text.

    Returns
    -------
    bool
        ``True`` iff a ``return <expr>`` is found (bare ``return`` excluded).
    """
    return bool(re.search(r"\breturn\s+\S", source))


# ---------------------------------------------------------------------------
# §5  DocstringSpec — the declared contract of a function
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocstringSpec:
    """The declared specification extracted from a function's docstring.

    A DocstringSpec is the *left-hand side* of the alignment relation — it
    captures everything that the author *claims* the function does.  The
    analyser compares this against the actual implementation (right-hand side)
    to produce an :class:`AlignmentWitness`.

    In the Čech cohomology picture (theory2.tex §13.2), DocstringSpec is the
    local section over the open set U_f = {f} in the module nerve.

    Fields
    ------
    spec_id:
        Unique identifier for this spec.
    function_name:
        The bare function name.
    coordinate:
        Semantic coordinate, e.g. ``"module.Class.method"``.
    declared_params:
        Tuple of parameter names mentioned in the docstring.
    declared_returns:
        The documented return description.
    declared_raises:
        Tuple of exception names mentioned under ``Raises``.
    declared_side_effects:
        Tuple of documented side-effect descriptions.
    trust_level:
        TrustLevel (or Any fallback) for this specification.
    docstring_text:
        The full cleaned docstring text.
    source_file:
        Path to the source file.
    created_at:
        ISO-8601 creation timestamp.
    metadata:
        Arbitrary extra key/value data.
    """

    spec_id: str
    function_name: str
    coordinate: str
    declared_params: tuple[str, ...]
    declared_returns: str
    declared_raises: tuple[str, ...]
    declared_side_effects: tuple[str, ...]
    trust_level: Any
    docstring_text: str
    source_file: str = ""
    created_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.spec_id:
            object.__setattr__(self, "spec_id", _new_id("spec"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this spec to a JSON-compatible dict.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "spec_id": self.spec_id,
            "function_name": self.function_name,
            "coordinate": self.coordinate,
            "declared_params": list(self.declared_params),
            "declared_returns": self.declared_returns,
            "declared_raises": list(self.declared_raises),
            "declared_side_effects": list(self.declared_side_effects),
            "trust_level": str(self.trust_level),
            "docstring_text": self.docstring_text,
            "source_file": self.source_file,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocstringSpec:
        """Reconstruct a DocstringSpec from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        DocstringSpec
            Reconstructed instance.
        """
        return cls(
            spec_id=data.get("spec_id", _new_id("spec")),
            function_name=data.get("function_name", ""),
            coordinate=data.get("coordinate", ""),
            declared_params=tuple(data.get("declared_params", [])),
            declared_returns=data.get("declared_returns", ""),
            declared_raises=tuple(data.get("declared_raises", [])),
            declared_side_effects=tuple(data.get("declared_side_effects", [])),
            trust_level=data.get("trust_level"),
            docstring_text=data.get("docstring_text", ""),
            source_file=data.get("source_file", ""),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Domain logic
    # ------------------------------------------------------------------

    def param_count(self) -> int:
        """Return the number of declared parameters.

        Returns
        -------
        int
            ``len(self.declared_params)``.
        """
        return len(self.declared_params)

    def has_return_doc(self) -> bool:
        """Return True if a non-empty return contract is declared.

        Returns
        -------
        bool
            ``True`` iff ``self.declared_returns`` is a non-empty string.
        """
        return bool(self.declared_returns.strip())


# ---------------------------------------------------------------------------
# §6  AlignmentGap — a single discrepancy between spec and implementation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlignmentGap:
    """A single observable discrepancy between a docstring spec and its implementation.

    Each AlignmentGap is an element of the set Γ(f) from Theorem 13.2.1.  It
    carries a severity weight that feeds into the overall alignment score
    calculation.

    Gap kind strings and their semantics:
    - ``"MISSING_PARAM"``        — param in impl but absent from docstring
    - ``"EXTRA_PARAM"``          — param in docstring but absent from impl
    - ``"RETURN_MISMATCH"``      — impl has return but doc says nothing (or vice versa)
    - ``"RAISES_UNDOCUMENTED"``  — impl raises an exception not documented
    - ``"SIDE_EFFECT_HIDDEN"``   — impl has side effects not declared in spec

    Fields
    ------
    gap_id:
        Unique identifier for this gap.
    function_name:
        Name of the function where the gap was found.
    coordinate:
        Semantic coordinate string.
    gap_kind:
        One of the gap kind strings listed above.
    description:
        Human-readable explanation of the discrepancy.
    severity:
        One of ``"LOW"``, ``"MEDIUM"``, ``"HIGH"``, ``"CRITICAL"``.
    spec_value:
        The value/text on the spec (docstring) side.
    impl_value:
        The value/text on the implementation side.
    created_at:
        ISO-8601 timestamp.
    metadata:
        Arbitrary extra key/value data.
    """

    gap_id: str
    function_name: str
    coordinate: str
    gap_kind: str
    description: str
    severity: str
    spec_value: str
    impl_value: str
    created_at: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.gap_id:
            object.__setattr__(self, "gap_id", _new_id("gap"))

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this gap to a JSON-compatible dict.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "gap_id": self.gap_id,
            "function_name": self.function_name,
            "coordinate": self.coordinate,
            "gap_kind": self.gap_kind,
            "description": self.description,
            "severity": self.severity,
            "spec_value": self.spec_value,
            "impl_value": self.impl_value,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlignmentGap:
        """Reconstruct an AlignmentGap from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        AlignmentGap
            Reconstructed instance.
        """
        return cls(
            gap_id=data.get("gap_id", _new_id("gap")),
            function_name=data.get("function_name", ""),
            coordinate=data.get("coordinate", ""),
            gap_kind=data.get("gap_kind", ""),
            description=data.get("description", ""),
            severity=data.get("severity", "LOW"),
            spec_value=data.get("spec_value", ""),
            impl_value=data.get("impl_value", ""),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def is_critical(self) -> bool:
        """Return True iff this gap has CRITICAL severity.

        Returns
        -------
        bool
            ``True`` iff ``self.severity == "CRITICAL"``.
        """
        return self.severity.upper() == "CRITICAL"

    def to_obstruction(self) -> dict[str, JsonValue]:
        """Convert this gap to an obstruction record dict.

        Used when :class:`ObstructionRecord` is not importable.

        Returns
        -------
        dict[str, JsonValue]
            A dict with fields: ``obstruction_id``, ``gap_id``,
            ``function_name``, ``coordinate``, ``gap_kind``, ``severity``,
            ``description``, ``created_at``.
        """
        return {
            "obstruction_id": _new_id("obs"),
            "gap_id": self.gap_id,
            "function_name": self.function_name,
            "coordinate": self.coordinate,
            "gap_kind": self.gap_kind,
            "severity": self.severity,
            "description": self.description,
            "created_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# §7  AlignmentWitness — per-function alignment outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlignmentWitness:
    """Per-function witness recording the outcome of an alignment check.

    An AlignmentWitness is the proof object generated by
    :class:`DocumentationAlignmentAnalyzer` after comparing a
    :class:`DocstringSpec` against an implementation.  Together the witnesses
    for all functions in a module form the module-level
    :class:`DocumentationAlignmentWitness`.

    Fields
    ------
    witness_id:
        Unique identifier for this witness.
    function_name:
        Name of the function analysed.
    coordinate:
        Semantic coordinate string.
    spec_id:
        ID of the :class:`DocstringSpec` that was compared.
    gaps:
        Tuple of :class:`AlignmentGap` instances found.
    is_aligned:
        ``True`` iff no gaps were found.
    alignment_score:
        Numeric alignment score in ``[0.0, 1.0]`` (Theorem 13.2.2).
    checked_at:
        ISO-8601 timestamp of when the check was performed.
    metadata:
        Arbitrary extra key/value data.
    """

    witness_id: str
    function_name: str
    coordinate: str
    spec_id: str
    gaps: tuple[AlignmentGap, ...]
    is_aligned: bool
    alignment_score: float
    checked_at: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.witness_id:
            object.__setattr__(self, "witness_id", _new_id("witness"))

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to JSON-compatible dict.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "witness_id": self.witness_id,
            "function_name": self.function_name,
            "coordinate": self.coordinate,
            "spec_id": self.spec_id,
            "gaps": [g.to_dict() for g in self.gaps],
            "is_aligned": self.is_aligned,
            "alignment_score": self.alignment_score,
            "checked_at": self.checked_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlignmentWitness:
        """Reconstruct an AlignmentWitness from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        AlignmentWitness
            Reconstructed instance.
        """
        return cls(
            witness_id=data.get("witness_id", _new_id("witness")),
            function_name=data.get("function_name", ""),
            coordinate=data.get("coordinate", ""),
            spec_id=data.get("spec_id", ""),
            gaps=tuple(AlignmentGap.from_dict(g) for g in data.get("gaps", [])),
            is_aligned=bool(data.get("is_aligned", True)),
            alignment_score=float(data.get("alignment_score", 1.0)),
            checked_at=data.get("checked_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def critical_gaps(self) -> list[AlignmentGap]:
        """Return all gaps with CRITICAL severity.

        Returns
        -------
        list[AlignmentGap]
            Subset of ``self.gaps`` where ``gap.severity == "CRITICAL"``.
        """
        return [g for g in self.gaps if g.is_critical()]

    def alignment_grade(self) -> str:
        """Return a letter grade for this witness's alignment score.

        Implements the grading from Corollary 13.2.4:
        - A: score ≥ 0.90
        - B: score ≥ 0.75
        - C: score ≥ 0.60
        - D: score ≥ 0.40
        - F: score < 0.40

        Returns
        -------
        str
            One of ``"A"``, ``"B"``, ``"C"``, ``"D"``, ``"F"``.
        """
        return _grade_from_score(self.alignment_score)


# ---------------------------------------------------------------------------
# §8  DocumentationAlignmentAnalyzer — stateless alignment engine
# ---------------------------------------------------------------------------


class DocumentationAlignmentAnalyzer:
    """Stateless analyser that checks docstring specs against implementations.

    This class implements the alignment pipeline described in theory2.tex §13.2:

    1. Parse the implementation source to extract parameters, raises, and
       return presence.
    2. Compare each extracted fact against the :class:`DocstringSpec`.
    3. Emit an :class:`AlignmentGap` for each discrepancy.
    4. Compute the alignment score and emit an :class:`AlignmentWitness`.

    All methods are pure functions; no state is mutated.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_function(
        self,
        spec: DocstringSpec,
        impl_source: str,
    ) -> AlignmentWitness:
        """Analyse a single function and return an AlignmentWitness.

        Parameters
        ----------
        spec:
            The :class:`DocstringSpec` representing the docstring contract.
        impl_source:
            Python source text containing the function implementation.

        Returns
        -------
        AlignmentWitness
            A witness recording all gaps found and the overall alignment score.
        """
        impl_params = self.extract_impl_params(impl_source, spec.function_name)
        impl_raises = self.extract_impl_raises(impl_source)
        gaps: list[AlignmentGap] = []
        gaps.extend(self.check_param_alignment(spec, impl_params))
        gaps.extend(self.check_return_alignment(spec, impl_source))
        gaps.extend(self.check_raises_alignment(spec, impl_raises))
        score = self.score_alignment(gaps)
        return AlignmentWitness(
            witness_id=_new_id("witness"),
            function_name=spec.function_name,
            coordinate=spec.coordinate,
            spec_id=spec.spec_id,
            gaps=tuple(gaps),
            is_aligned=len(gaps) == 0,
            alignment_score=score,
            checked_at=_now_iso(),
        )

    def extract_impl_params(
        self,
        impl_source: str,
        function_name: str,
    ) -> list[str]:
        """Parse the implementation parameter list for *function_name*.

        Parameters
        ----------
        impl_source:
            Python source text to search.
        function_name:
            The function whose signature is extracted.

        Returns
        -------
        list[str]
            Parameter names (excluding ``self`` / ``cls``).
        """
        return _parse_def_signature(impl_source, function_name)

    def extract_impl_raises(self, impl_source: str) -> list[str]:
        """Find all exception names raised in *impl_source*.

        Parameters
        ----------
        impl_source:
            Python source text.

        Returns
        -------
        list[str]
            Unique exception class names in order of first appearance.
        """
        return _find_raise_names(impl_source)

    def check_param_alignment(
        self,
        spec: DocstringSpec,
        impl_params: list[str],
    ) -> list[AlignmentGap]:
        """Detect parameter discrepancies between spec and implementation.

        Generates:
        - ``MISSING_PARAM`` — param in impl but absent from docstring
        - ``EXTRA_PARAM``   — param in docstring but absent from impl

        Parameters
        ----------
        spec:
            The :class:`DocstringSpec` to compare against.
        impl_params:
            List of parameter names from the implementation.

        Returns
        -------
        list[AlignmentGap]
            One gap per discrepancy found.
        """
        gaps: list[AlignmentGap] = []
        spec_set = set(spec.declared_params)
        impl_set = set(impl_params)
        now = _now_iso()
        for missing in impl_set - spec_set:
            gaps.append(
                AlignmentGap(
                    gap_id=_new_id("gap"),
                    function_name=spec.function_name,
                    coordinate=spec.coordinate,
                    gap_kind="MISSING_PARAM",
                    description=(
                        f"Parameter '{missing}' is present in the implementation "
                        f"but not documented in the docstring."
                    ),
                    severity="MEDIUM",
                    spec_value="",
                    impl_value=missing,
                    created_at=now,
                )
            )
        for extra in spec_set - impl_set:
            gaps.append(
                AlignmentGap(
                    gap_id=_new_id("gap"),
                    function_name=spec.function_name,
                    coordinate=spec.coordinate,
                    gap_kind="EXTRA_PARAM",
                    description=(
                        f"Parameter '{extra}' is documented in the docstring "
                        f"but not present in the implementation signature."
                    ),
                    severity="HIGH",
                    spec_value=extra,
                    impl_value="",
                    created_at=now,
                )
            )
        return gaps

    def check_return_alignment(
        self,
        spec: DocstringSpec,
        impl_source: str,
    ) -> list[AlignmentGap]:
        """Detect return value discrepancies.

        Generates ``RETURN_MISMATCH`` when the implementation returns a value
        but the docstring does not document one, or vice versa.

        Parameters
        ----------
        spec:
            The :class:`DocstringSpec` to compare against.
        impl_source:
            Python source text of the implementation.

        Returns
        -------
        list[AlignmentGap]
            Zero or one gap.
        """
        gaps: list[AlignmentGap] = []
        impl_has_return = _has_return_statement(impl_source)
        spec_has_return = spec.has_return_doc()
        if impl_has_return and not spec_has_return:
            gaps.append(
                AlignmentGap(
                    gap_id=_new_id("gap"),
                    function_name=spec.function_name,
                    coordinate=spec.coordinate,
                    gap_kind="RETURN_MISMATCH",
                    description=(
                        "Implementation returns a value but the docstring "
                        "does not document a return value."
                    ),
                    severity="HIGH",
                    spec_value="(no return documented)",
                    impl_value="return <value>",
                    created_at=_now_iso(),
                )
            )
        elif not impl_has_return and spec_has_return:
            gaps.append(
                AlignmentGap(
                    gap_id=_new_id("gap"),
                    function_name=spec.function_name,
                    coordinate=spec.coordinate,
                    gap_kind="RETURN_MISMATCH",
                    description=(
                        "Docstring documents a return value but the "
                        "implementation has no return statement."
                    ),
                    severity="MEDIUM",
                    spec_value=spec.declared_returns,
                    impl_value="(no return statement)",
                    created_at=_now_iso(),
                )
            )
        return gaps

    def check_raises_alignment(
        self,
        spec: DocstringSpec,
        impl_raises: list[str],
    ) -> list[AlignmentGap]:
        """Detect undocumented exception raises.

        Generates ``RAISES_UNDOCUMENTED`` for each exception raised by the
        implementation that is not mentioned in the docstring's ``Raises``
        section.

        Parameters
        ----------
        spec:
            The :class:`DocstringSpec` to compare against.
        impl_raises:
            List of exception class names found in the implementation.

        Returns
        -------
        list[AlignmentGap]
            One gap per undocumented raise.
        """
        gaps: list[AlignmentGap] = []
        spec_raises = set(spec.declared_raises)
        now = _now_iso()
        for exc in impl_raises:
            if exc not in spec_raises:
                gaps.append(
                    AlignmentGap(
                        gap_id=_new_id("gap"),
                        function_name=spec.function_name,
                        coordinate=spec.coordinate,
                        gap_kind="RAISES_UNDOCUMENTED",
                        description=(
                            f"Exception '{exc}' is raised by the implementation "
                            f"but not documented in the docstring Raises section."
                        ),
                        severity="HIGH",
                        spec_value="",
                        impl_value=exc,
                        created_at=now,
                    )
                )
        return gaps

    def score_alignment(self, gaps: list[AlignmentGap]) -> float:
        """Compute the numeric alignment score from a list of gaps.

        Parameters
        ----------
        gaps:
            List of :class:`AlignmentGap` instances.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]`` per Theorem 13.2.2.
        """
        return _compute_alignment_score(gaps)

    def batch_analyze(
        self,
        specs_and_impls: Sequence[tuple[DocstringSpec, str]],
    ) -> list[AlignmentWitness]:
        """Analyse multiple ``(spec, impl_source)`` pairs.

        Parameters
        ----------
        specs_and_impls:
            Sequence of ``(DocstringSpec, impl_source)`` tuples.

        Returns
        -------
        list[AlignmentWitness]
            One witness per input tuple, in order.
        """
        return [
            self.analyze_function(spec, impl_src)
            for spec, impl_src in specs_and_impls
        ]


# ---------------------------------------------------------------------------
# §9  DocumentationAlignmentCoordinator — orchestration and policy layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentationAlignmentCoordinator:
    """Coordinator that applies policy to per-function alignment results.

    The coordinator wraps :class:`DocumentationAlignmentAnalyzer`, applies
    a minimum alignment score threshold, and converts failing gaps into
    obstruction records.

    Fields
    ------
    coordinator_id:
        Unique identifier.
    strict_mode:
        When ``True``, any CRITICAL gap causes an obstruction even if the
        overall score meets the threshold.
    min_alignment_score:
        Minimum acceptable alignment score.  Functions below this threshold
        produce obstruction records.
    created_at:
        ISO-8601 creation timestamp.

    Theory context (theory2.tex §13.2)
    ------------------------------------
    The coordinator checks the *global consistency condition*: for the module
    cover {U_f}, all local alignment witnesses must have scores above
    ``min_alignment_score``.  Violations are Ȟ¹ obstructions.
    """

    coordinator_id: str
    strict_mode: bool = False
    min_alignment_score: float = 0.75
    created_at: str = ""

    def __post_init__(self) -> None:
        """Set default *coordinator_id* and *created_at* on frozen instance."""
        if not self.coordinator_id:
            object.__setattr__(self, "coordinator_id", _new_id("coordinator"))
        if not self.created_at:
            object.__setattr__(self, "created_at", _now_iso())

    # ------------------------------------------------------------------
    # Core coordination
    # ------------------------------------------------------------------

    def coordinate(
        self,
        spec: DocstringSpec,
        impl_source: str,
    ) -> tuple[AlignmentWitness, list[dict[str, JsonValue]]]:
        """Align a single function spec against its implementation.

        Parameters
        ----------
        spec:
            The :class:`DocstringSpec` for the function.
        impl_source:
            Source text containing the function implementation.

        Returns
        -------
        tuple[AlignmentWitness, list[dict]]
            The witness and a (possibly empty) list of obstruction dicts.
            Obstructions are generated when the score is below
            ``min_alignment_score`` or when ``strict_mode`` is True and
            CRITICAL gaps are present.
        """
        analyzer = DocumentationAlignmentAnalyzer()
        witness = analyzer.analyze_function(spec, impl_source)
        obstructions: list[dict[str, JsonValue]] = []
        if witness.alignment_score < self.min_alignment_score:
            for gap in witness.gaps:
                obstructions.append(self._gap_to_obstruction(gap))
        if self.strict_mode:
            for gap in witness.critical_gaps():
                obs = self._gap_to_obstruction(gap)
                if obs not in obstructions:
                    obstructions.append(obs)
        return witness, obstructions

    def coordinate_module(
        self,
        specs: Sequence[DocstringSpec],
        module_source: str,
    ) -> tuple[list[AlignmentWitness], list[dict[str, JsonValue]]]:
        """Coordinate alignment for all functions in a module.

        Parameters
        ----------
        specs:
            Sequence of :class:`DocstringSpec` objects for the module's
            exported functions.
        module_source:
            Full source text of the module.

        Returns
        -------
        tuple[list[AlignmentWitness], list[dict]]
            All witnesses and the aggregated list of all obstructions.
        """
        all_witnesses: list[AlignmentWitness] = []
        all_obstructions: list[dict[str, JsonValue]] = []
        for spec in specs:
            witness, obs = self.coordinate(spec, module_source)
            all_witnesses.append(witness)
            all_obstructions.extend(obs)
        return all_witnesses, all_obstructions

    def generate_alignment_report(
        self,
        witnesses: Sequence[AlignmentWitness],
    ) -> dict[str, JsonValue]:
        """Generate an aggregate alignment report.

        Parameters
        ----------
        witnesses:
            Sequence of :class:`AlignmentWitness` objects to summarise.

        Returns
        -------
        dict[str, JsonValue]
            Keys: ``generated_at``, ``coordinator_id``, ``strict_mode``,
            ``min_alignment_score``, ``total_functions``, ``aligned_count``,
            ``gap_count``, ``overall_score``, ``per_function``.
        """
        total = 0
        aligned = 0
        gap_total = 0
        score_sum = 0.0
        per_function: list[JsonValue] = []
        for w in witnesses:
            total += 1
            if w.is_aligned:
                aligned += 1
            gap_total += len(w.gaps)
            score_sum += w.alignment_score
            per_function.append(
                {
                    "function_name": w.function_name,
                    "coordinate": w.coordinate,
                    "is_aligned": w.is_aligned,
                    "alignment_score": w.alignment_score,
                    "grade": w.alignment_grade(),
                    "gap_count": len(w.gaps),
                    "critical_gaps": len(w.critical_gaps()),
                }
            )
        overall = score_sum / total if total else 0.0
        return {
            "generated_at": _now_iso(),
            "coordinator_id": self.coordinator_id,
            "strict_mode": self.strict_mode,
            "min_alignment_score": self.min_alignment_score,
            "total_functions": total,
            "aligned_count": aligned,
            "gap_count": gap_total,
            "overall_score": overall,
            "overall_grade": _grade_from_score(overall),
            "per_function": per_function,
        }

    def _gap_to_obstruction(self, gap: AlignmentGap) -> dict[str, JsonValue]:
        """Convert an :class:`AlignmentGap` to an obstruction record dict.

        Parameters
        ----------
        gap:
            The gap to convert.

        Returns
        -------
        dict[str, JsonValue]
            Obstruction record compatible with the ObstructionRecord schema.
        """
        return {
            "obstruction_id": _new_id("obs"),
            "gap_id": gap.gap_id,
            "function_name": gap.function_name,
            "coordinate": gap.coordinate,
            "gap_kind": gap.gap_kind,
            "severity": gap.severity,
            "description": gap.description,
            "coordinator_id": self.coordinator_id,
            "created_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# §10  DocumentationAlignmentWitness — module-level aggregate witness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentationAlignmentWitness:
    """Module-level aggregate witness for the documentation alignment pipeline.

    This is the colimit of all per-function :class:`AlignmentWitness` objects
    in the category of alignment records (Theorem 13.2.3).  It records the
    overall module alignment status and allows quick queries about the worst
    offenders.

    Fields
    ------
    witness_id:
        Unique identifier.
    module_path:
        Dotted module path or file path.
    function_witnesses:
        Tuple of all :class:`AlignmentWitness` objects for this module.
    total_functions:
        Total number of functions analysed.
    aligned_count:
        Number of fully aligned functions.
    gap_count:
        Total number of alignment gaps across all functions.
    overall_score:
        Arithmetic mean of per-function alignment scores.
    is_module_aligned:
        ``True`` iff all functions are aligned.
    created_at:
        ISO-8601 timestamp.
    metadata:
        Arbitrary extra key/value data.
    """

    witness_id: str
    module_path: str
    function_witnesses: tuple[AlignmentWitness, ...]
    total_functions: int
    aligned_count: int
    gap_count: int
    overall_score: float
    is_module_aligned: bool
    created_at: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.witness_id:
            object.__setattr__(self, "witness_id", _new_id("modwitness"))

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to JSON-compatible dict.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "witness_id": self.witness_id,
            "module_path": self.module_path,
            "function_witnesses": [w.to_dict() for w in self.function_witnesses],
            "total_functions": self.total_functions,
            "aligned_count": self.aligned_count,
            "gap_count": self.gap_count,
            "overall_score": self.overall_score,
            "is_module_aligned": self.is_module_aligned,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentationAlignmentWitness:
        """Reconstruct from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        DocumentationAlignmentWitness
            Reconstructed instance.
        """
        return cls(
            witness_id=data.get("witness_id", _new_id("modwitness")),
            module_path=data.get("module_path", ""),
            function_witnesses=tuple(
                AlignmentWitness.from_dict(w)
                for w in data.get("function_witnesses", [])
            ),
            total_functions=int(data.get("total_functions", 0)),
            aligned_count=int(data.get("aligned_count", 0)),
            gap_count=int(data.get("gap_count", 0)),
            overall_score=float(data.get("overall_score", 0.0)),
            is_module_aligned=bool(data.get("is_module_aligned", False)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def summary(self) -> str:
        """Return a one-line human-readable summary of this module witness.

        Returns
        -------
        str
            E.g. ``"Module fake.math: 3 functions, 2 aligned, score=0.85 (B)"``.
        """
        grade = _grade_from_score(self.overall_score)
        return (
            f"Module {self.module_path}: "
            f"{self.total_functions} functions, "
            f"{self.aligned_count} aligned, "
            f"score={self.overall_score:.2f} ({grade})"
        )

    def worst_functions(self) -> list[str]:
        """Return function names with below-average alignment scores.

        Returns
        -------
        list[str]
            Names of functions whose ``alignment_score`` is strictly below
            ``self.overall_score``.
        """
        return [
            w.function_name
            for w in self.function_witnesses
            if w.alignment_score < self.overall_score
        ]


# ---------------------------------------------------------------------------
# §11  Factory helper
# ---------------------------------------------------------------------------


def _make_module_witness(
    module_path: str,
    function_witnesses: list[AlignmentWitness],
) -> DocumentationAlignmentWitness:
    """Construct a :class:`DocumentationAlignmentWitness` from per-function witnesses.

    Parameters
    ----------
    module_path:
        The module path label.
    function_witnesses:
        Per-function :class:`AlignmentWitness` objects.

    Returns
    -------
    DocumentationAlignmentWitness
        Aggregate module-level witness.
    """
    total = len(function_witnesses)
    aligned = sum(1 for w in function_witnesses if w.is_aligned)
    gaps = sum(len(w.gaps) for w in function_witnesses)
    overall = sum(w.alignment_score for w in function_witnesses) / total if total else 0.0
    return DocumentationAlignmentWitness(
        witness_id=_new_id("modwitness"),
        module_path=module_path,
        function_witnesses=tuple(function_witnesses),
        total_functions=total,
        aligned_count=aligned,
        gap_count=gaps,
        overall_score=overall,
        is_module_aligned=aligned == total,
        created_at=_now_iso(),
    )


def _spec_from_docstring(
    function_name: str,
    coordinate: str,
    docstring_text: str,
    trust_level: Any = None,
) -> DocstringSpec:
    """Build a :class:`DocstringSpec` by parsing a raw docstring.

    Extracts ``Parameters``, ``Returns``, and ``Raises`` sections using
    simple regex heuristics.  Sufficient for smoke tests and coverage checks.

    Parameters
    ----------
    function_name:
        Bare function name.
    coordinate:
        Semantic coordinate string.
    docstring_text:
        The raw docstring text.
    trust_level:
        Optional trust level value.

    Returns
    -------
    DocstringSpec
        A newly constructed spec.
    """
    # Extract parameter names from "Parameters" section
    param_section = re.search(
        r"Parameters\s*\n\s*[-—]+\s*\n(.*?)(?:\n\s*\n[A-Z]|\Z)",
        docstring_text,
        re.DOTALL,
    )
    declared_params: tuple[str, ...] = ()
    if param_section:
        raw_params = param_section.group(1)
        names = re.findall(r"^\s{0,8}([a-zA-Z_]\w*)\s*[:*]", raw_params, re.MULTILINE)
        declared_params = tuple(names)
    # Extract returns description
    return_section = re.search(
        r"Returns\s*\n\s*[-—]+\s*\n\s*(.+?)(?:\n\s*\n|\Z)",
        docstring_text,
        re.DOTALL,
    )
    declared_returns = return_section.group(1).strip() if return_section else ""
    # Extract raises
    raises_section = re.search(
        r"Raises\s*\n\s*[-—]+\s*\n(.*?)(?:\n\s*\n[A-Z]|\Z)",
        docstring_text,
        re.DOTALL,
    )
    declared_raises: tuple[str, ...] = ()
    if raises_section:
        exc_names = re.findall(
            r"^\s{0,8}([A-Z][a-zA-Z0-9_]*(?:Error|Exception|Warning))\b",
            raises_section.group(1),
            re.MULTILINE,
        )
        declared_raises = tuple(exc_names)
    return DocstringSpec(
        spec_id=_new_id("spec"),
        function_name=function_name,
        coordinate=coordinate,
        declared_params=declared_params,
        declared_returns=declared_returns,
        declared_raises=declared_raises,
        declared_side_effects=(),
        trust_level=trust_level,
        docstring_text=docstring_text,
    )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.evidence, jugeo.judgments)
# ---------------------------------------------------------------------------


def alignment_trust_check(claim: Any) -> dict[str, Any]:
    """Check trust alignment between a public claim and internal evidence.

    Trust checking verifies that the declared trust level of a public
    claim does not exceed the trust actually supported by evidence.

    Parameters
    ----------
    claim : Any
        A PublicClaim object or dict with claim data.

    Returns
    -------
    dict[str, Any]
        Trust check result with ``honest``, ``declared_trust``,
        ``actual_trust``, ``gap``, and ``trust_obj`` keys.
    """
    try:
        from jugeo.evidence.trust import TrustLevel, compare_trust, compute_trust_gap
    except ImportError:
        TrustLevel = None
        compare_trust = None
        compute_trust_gap = None

    declared = getattr(claim, "declared_trust_level", None) or (
        claim.get("declared_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )
    actual = getattr(claim, "internal_trust_level", None) or (
        claim.get("internal_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )

    result: dict[str, Any] = {
        "declared_trust": str(declared),
        "actual_trust": str(actual),
        "honest": str(declared) <= str(actual),
        "gap": None,
        "trust_obj": None,
    }

    if compare_trust is not None:
        try:
            cmp = compare_trust(declared, actual)
            result["honest"] = getattr(cmp, "honest", result["honest"])
        except Exception:
            pass

    if compute_trust_gap is not None:
        try:
            result["gap"] = compute_trust_gap(declared, actual)
        except Exception:
            pass

    return result


def alignment_judgment(claim: Any, reality: Any) -> dict[str, Any]:
    """Construct a judgment comparing a public claim against internal reality.

    The alignment judgment captures the relationship between what is
    publicly stated and what the internal evidence actually supports.

    Parameters
    ----------
    claim : Any
        The public claim or documentation assertion.
    reality : Any
        The internal judgment, evidence, or code state.

    Returns
    -------
    dict[str, Any]
        Judgment record with ``aligned``, ``claim_summary``,
        ``reality_summary``, ``discrepancies``, and ``judgment_obj`` keys.
    """
    try:
        from jugeo.judgments import Judgment, build_comparison_judgment
    except ImportError:
        Judgment = None
        build_comparison_judgment = None

    claim_str = getattr(claim, "summary", None) or (
        claim.get("summary") if isinstance(claim, dict) else str(claim)[:120]
    )
    reality_str = getattr(reality, "summary", None) or (
        reality.get("summary") if isinstance(reality, dict) else str(reality)[:120]
    )

    judgment: dict[str, Any] = {
        "aligned": claim_str == reality_str,
        "claim_summary": claim_str,
        "reality_summary": reality_str,
        "discrepancies": [],
        "judgment_obj": None,
    }

    if build_comparison_judgment is not None:
        try:
            j = build_comparison_judgment(claim=claim, reality=reality)
            judgment["aligned"] = getattr(j, "aligned", judgment["aligned"])
            judgment["discrepancies"] = getattr(j, "discrepancies", [])
            judgment["judgment_obj"] = j
        except Exception:
            pass

    return judgment


def alignment_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for an alignment check result.

    The alignment certificate records whether a public claim was found
    to be honest, the evidence used, and the trust level.

    Parameters
    ----------
    result : Any
        An alignment check result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``aligned``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    aligned = getattr(result, "aligned", None) or getattr(result, "honest", None)
    if aligned is None and isinstance(result, dict):
        aligned = result.get("aligned", result.get("honest", False))

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "aligned": bool(aligned) if aligned is not None else False,
        "trust_level": "ALIGNED" if aligned else "MISALIGNED",
        "certificate_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim="public_alignment", satisfied=aligned, source="public_alignment"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# §12  __all__
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "JsonScalar",
    "JsonValue",
    "_now_iso",
    "_new_id",
    "_severity_weight",
    "_compute_alignment_score",
    "_grade_from_score",
    "_normalise_param_name",
    "_parse_def_signature",
    "_find_raise_names",
    "_has_return_statement",
    "_make_module_witness",
    "_spec_from_docstring",
    "DocstringSpec",
    "AlignmentGap",
    "AlignmentWitness",
    "DocumentationAlignmentAnalyzer",
    "DocumentationAlignmentCoordinator",
    "DocumentationAlignmentWitness",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# ---------------------------------------------------------------------------
# §13  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Smoke test: build specs, compare against implementations, check
    # the coordinator output.
    # ------------------------------------------------------------------

    _IMPL_GOOD = '''\
def add(x: int, y: int) -> int:
    """Add two integers.

    Parameters
    ----------
    x:
        First operand.
    y:
        Second operand.

    Returns
    -------
    int
        The sum.
    """
    return x + y
'''

    _IMPL_BAD = '''\
def divide(numerator: float, denominator: float) -> float:
    # No docstring — spec supplied separately
    if denominator == 0.0:
        raise ZeroDivisionError("zero denom")
    return numerator / denominator
'''

    _SPEC_ADD_DOCSTRING = '''\
Add two integers.

Parameters
----------
x:
    First operand.
y:
    Second operand.

Returns
-------
int
    The sum.
'''

    _SPEC_DIVIDE_DOCSTRING = '''\
Divide numerator by denominator.

Parameters
----------
numerator:
    The dividend.
denominator:
    The divisor.

Returns
-------
float
    The quotient.
'''

    print("=== Smoke test: documentation_alignment ===")

    # Build specs manually
    spec_add = _spec_from_docstring(
        function_name="add",
        coordinate="fake.math.add",
        docstring_text=_SPEC_ADD_DOCSTRING,
    )
    spec_divide = _spec_from_docstring(
        function_name="divide",
        coordinate="fake.math.divide",
        docstring_text=_SPEC_DIVIDE_DOCSTRING,
    )

    print(f"Spec 'add':     params={spec_add.declared_params}, returns={spec_add.has_return_doc()}")
    print(f"Spec 'divide':  params={spec_divide.declared_params}, raises={spec_divide.declared_raises}")
    print()

    # --- Analyzer ---
    analyzer = DocumentationAlignmentAnalyzer()

    # 'add' — should be well-aligned
    witness_add = analyzer.analyze_function(spec_add, _IMPL_GOOD)
    print(f"Witness 'add':  gaps={len(witness_add.gaps)}, score={witness_add.alignment_score:.2f}, grade={witness_add.alignment_grade()}, aligned={witness_add.is_aligned}")

    # 'divide' — implementation raises ZeroDivisionError which is undocumented in spec
    witness_divide = analyzer.analyze_function(spec_divide, _IMPL_BAD)
    print(f"Witness 'divide': gaps={len(witness_divide.gaps)}, score={witness_divide.alignment_score:.2f}, grade={witness_divide.alignment_grade()}")
    for g in witness_divide.gaps:
        print(f"  gap: {g.gap_kind} [{g.severity}] — {g.description}")
    print()

    # --- Coordinator ---
    coord = DocumentationAlignmentCoordinator(
        coordinator_id=_new_id("coord"),
        strict_mode=False,
        min_alignment_score=0.7,
    )
    w1, obs1 = coord.coordinate(spec_add, _IMPL_GOOD)
    w2, obs2 = coord.coordinate(spec_divide, _IMPL_BAD)
    print(f"Coord 'add':    obstructions={len(obs1)}")
    print(f"Coord 'divide': obstructions={len(obs2)}")
    print()

    # --- Batch analyze ---
    witnesses = analyzer.batch_analyze([
        (spec_add, _IMPL_GOOD),
        (spec_divide, _IMPL_BAD),
    ])
    report = coord.generate_alignment_report(witnesses)
    print(f"Report: total={report['total_functions']}, aligned={report['aligned_count']}, overall={report['overall_score']:.2f} ({report['overall_grade']})")
    print()

    # --- Module witness ---
    mod_witness = _make_module_witness("fake.math", witnesses)
    print(mod_witness.summary())
    print(f"Worst functions: {mod_witness.worst_functions()}")
    print()

    # --- AlignmentGap serialisation round-trip ---
    for g in witness_divide.gaps:
        obs_dict = g.to_obstruction()
        g2 = AlignmentGap.from_dict(g.to_dict())
        assert g2.gap_id == g.gap_id, "Round-trip gap_id mismatch"
    print("AlignmentGap round-trip OK")

    # --- DocstringSpec serialisation ---
    d = spec_divide.to_dict()
    spec_back = DocstringSpec.from_dict(d)
    assert spec_back.function_name == spec_divide.function_name
    print("DocstringSpec round-trip OK")

    # --- DocumentationAlignmentWitness round-trip ---
    mw_d = mod_witness.to_dict()
    mw_back = DocumentationAlignmentWitness.from_dict(mw_d)
    assert mw_back.module_path == mod_witness.module_path
    print("DocumentationAlignmentWitness round-trip OK")
    print()

    print("Smoke test passed.")
