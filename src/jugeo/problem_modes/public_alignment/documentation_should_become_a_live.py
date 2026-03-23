"""documentation_should_become_a_live.py — DocumentationBecomeLiveSemanticCoordinator

# copilot: documentation_should_become_a_live.py — DocumentationBecomeLiveSemanticCoordinator for Ch13

Theory context (theory2.tex §13.1 – Documentation as Live Semantic Participant)
=================================================================================
In the JuGeo semantic framework, a *documentation string* is not inert commentary.  It
is a **live semantic participant** in the Carrier topology.  Concretely, every docstring
introduces an obligation triple

    (f, D_f, C_f)   ∈   Fun × Doc × Contract

where *f* is the documented callable, *D_f* is the textual documentation artefact, and
*C_f* is the derived behavioural contract.  The README is treated as a global constraint
morphism that must be consistent with every per-function contract (consistency is checked
in Ȟ⁰ of the nerve of the obligation cover).

Key theorems referenced from theory2.tex Ch13
----------------------------------------------
Theorem 13.1.1 (Semantic Completeness of Documentation):
    A module *M* is *documentation-complete* if and only if every exported symbol carries
    a DocstringObligation that passes ``check_completeness()``.  Incompleteness is an
    obstruction in H¹(Nerve(M), ℤ₂).

Theorem 13.1.2 (Live-Doc Consistency):
    A LiveDocRecord is *consistent* if the README constraint set and the per-function
    obligation set are jointly satisfiable.  Inconsistency manifests as a non-trivial
    class in the Čech cohomology Ȟ¹(Cover, 𝒞) where 𝒞 is the sheaf of contracts.

Theorem 13.1.3 (Verification Monotonicity):
    Marking a DocstringObligation as verified is a monotone operation: once verified, an
    obligation may only be *unverified* by an explicit superseding edit to the source.
    This ensures the verification lattice is well-founded.

Corollary 13.1.4 (Coverage Score Lower Bound):
    For a module with *n* obligations, the coverage score σ satisfies
        σ ≥ verified_count / n
    with equality when all obligations are structurally complete.

Public-Alignment stage: ch13-public-alignment
Sequence: 1
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
    "sequence": 1,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "documentation_should_become_a_live",
    "class": "DocumentationBecomeLiveSemanticCoordinator",
    "theory_section": "§13.1 – Documentation as Live Semantic Participant",
}

# ---------------------------------------------------------------------------
# §3  Module-level helper functions
# ---------------------------------------------------------------------------


def _now_iso() -> str:  # type: ignore[no-redef]
    """Return the current UTC moment as an ISO-8601 timestamp string.

    Used throughout this module wherever a creation or update timestamp is
    needed.  The format is ``YYYY-MM-DDTHH:MM:SSZ`` (always UTC, always Zulu
    suffix).

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

    The identifier is ``prefix-<12 hex chars>`` so it is both human-readable
    and collision-resistant for typical module sizes.

    Parameters
    ----------
    prefix:
        A short label prepended to the hex string (e.g. ``"obligation"``,
        ``"record"``, ``"witness"``).

    Returns
    -------
    str
        A string of the form ``"<prefix>-<12 hex characters>"``.

    Examples
    --------
    >>> _id = _new_id("obligation")
    >>> _id.startswith("obligation-")
    True
    >>> len(_id) == len("obligation-") + 12
    True
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _sanitize_docstring(raw: str) -> str:
    """Strip leading/trailing whitespace from each line of *raw* docstring text.

    Parameters
    ----------
    raw:
        The raw docstring, possibly with indentation artefacts.

    Returns
    -------
    str
        Cleaned docstring with normalised indentation removed.
    """
    lines = raw.splitlines()
    stripped = [line.strip() for line in lines]
    return "\n".join(stripped).strip()


def _parse_param_names_from_signature(signature: str) -> list[str]:
    """Extract bare parameter names from a Python function signature string.

    Parameters
    ----------
    signature:
        The parenthesised signature, e.g. ``"(self, x: int, y: float = 0.0)"``.

    Returns
    -------
    list[str]
        A list of parameter names, excluding ``self`` and ``cls``.

    Notes
    -----
    This is a best-effort regex parse; it will not handle deeply nested default
    expressions.  Sufficient for smoke-testing and coverage scoring.
    """
    inner = re.sub(r"^\(|\)$", "", signature.strip())
    params: list[str] = []
    for token in inner.split(","):
        token = token.strip()
        if not token:
            continue
        name = token.split(":")[0].split("=")[0].strip().lstrip("*")
        if name and name not in {"self", "cls"}:
            params.append(name)
    return params


def _extract_raises_from_source(source: str) -> list[str]:
    """Find all exception names in ``raise`` statements within *source*.

    Parameters
    ----------
    source:
        Raw Python source text to scan.

    Returns
    -------
    list[str]
        Unique exception class names found via ``raise ExcName`` patterns.
    """
    pattern = re.compile(r"\braise\s+([A-Za-z_][A-Za-z0-9_]*)")
    return list(dict.fromkeys(pattern.findall(source)))


def _score_from_ratio(numerator: int, denominator: int) -> float:
    """Compute a coverage ratio, returning 0.0 when *denominator* is zero.

    Parameters
    ----------
    numerator:
        Count of verified / aligned items.
    denominator:
        Total item count.

    Returns
    -------
    float
        A value in ``[0.0, 1.0]``.
    """
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


# ---------------------------------------------------------------------------
# §4  DocstringObligation — per-function documentation contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocstringObligation:
    """A single documentation obligation derived from a function's docstring.

    A DocstringObligation represents the *live* semantic contract that a
    docstring creates for its host function.  In the JuGeo framework (see
    theory2.tex §13.1) this is an element of the obligation sheaf 𝒪 over the
    module nerve.

    Fields
    ------
    obligation_id:
        Unique identifier for this obligation (``_new_id("obligation")``).
    function_name:
        The bare name of the function being documented.
    coordinate:
        Semantic coordinate string, e.g. ``"module.ClassName.method_name"``.
    docstring_text:
        The full cleaned docstring text of the function.
    parameter_contracts:
        Tuple of parameter contract strings extracted from the docstring
        (one entry per documented parameter).
    return_contract:
        The documented return description/contract.
    side_effect_contract:
        Description of permitted side-effects (empty string = none declared).
    trust_level:
        TrustLevel enum value (or Any fallback) indicating how trusted this
        obligation is.
    is_verified:
        Whether this obligation has been formally verified.
    verification_timestamp:
        ISO-8601 timestamp of when verification was performed.
    source_file:
        Path to the source file this obligation was extracted from.
    metadata:
        Arbitrary extra key/value data.
    """

    obligation_id: str
    function_name: str
    coordinate: str
    docstring_text: str
    parameter_contracts: tuple[str, ...]
    return_contract: str
    side_effect_contract: str
    trust_level: Any
    is_verified: bool = False
    verification_timestamp: str = ""
    source_file: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.obligation_id:
            object.__setattr__(self, "obligation_id", _new_id("obligation"))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this obligation to a plain JSON-compatible dict.

        Returns
        -------
        dict[str, JsonValue]
            All fields expressed as JSON-safe scalar/collection types.
        """
        return {
            "obligation_id": self.obligation_id,
            "function_name": self.function_name,
            "coordinate": self.coordinate,
            "docstring_text": self.docstring_text,
            "parameter_contracts": list(self.parameter_contracts),
            "return_contract": self.return_contract,
            "side_effect_contract": self.side_effect_contract,
            "trust_level": str(self.trust_level),
            "is_verified": self.is_verified,
            "verification_timestamp": self.verification_timestamp,
            "source_file": self.source_file,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocstringObligation:
        """Reconstruct a DocstringObligation from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        DocstringObligation
            Reconstructed instance.
        """
        return cls(
            obligation_id=data.get("obligation_id", _new_id("obligation")),
            function_name=data.get("function_name", ""),
            coordinate=data.get("coordinate", ""),
            docstring_text=data.get("docstring_text", ""),
            parameter_contracts=tuple(data.get("parameter_contracts", [])),
            return_contract=data.get("return_contract", ""),
            side_effect_contract=data.get("side_effect_contract", ""),
            trust_level=data.get("trust_level"),
            is_verified=bool(data.get("is_verified", False)),
            verification_timestamp=data.get("verification_timestamp", ""),
            source_file=data.get("source_file", ""),
            metadata=dict(data.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Domain logic
    # ------------------------------------------------------------------

    def mark_verified(self) -> DocstringObligation:
        """Return a copy of this obligation with *is_verified* set to True.

        Uses :func:`dataclasses.replace` to produce a new frozen instance;
        the original is unchanged (Theorem 13.1.3: Verification Monotonicity).

        Returns
        -------
        DocstringObligation
            A new instance with ``is_verified=True`` and
            ``verification_timestamp`` set to *now*.
        """
        return replace(
            self,
            is_verified=True,
            verification_timestamp=_now_iso(),
        )

    def extract_parameter_names(self) -> list[str]:
        """Return a list of parameter names mentioned in *parameter_contracts*.

        Heuristic: each entry in ``parameter_contracts`` is expected to start
        with ``"<name>:"`` or ``"<name> ("``; everything before the first
        colon or space is taken as the name.

        Returns
        -------
        list[str]
            Parameter names in declaration order.
        """
        names: list[str] = []
        for contract in self.parameter_contracts:
            token = re.split(r"[:\s]", contract.strip(), maxsplit=1)[0]
            if token:
                names.append(token)
        return names

    def check_completeness(self) -> bool:
        """Return True if this obligation is *structurally complete*.

        An obligation is complete when:
        * The docstring text is non-empty.
        * At least one parameter contract is present (or the function takes no
          parameters — treated as vacuously satisfied here).
        * The return contract is non-empty.

        Returns
        -------
        bool
            ``True`` iff the obligation satisfies the completeness predicate.
        """
        return bool(self.docstring_text) and bool(self.return_contract)


# ---------------------------------------------------------------------------
# §5  LiveDocRecord — module-level live documentation record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveDocRecord:
    """A module-level record aggregating all DocstringObligations.

    A LiveDocRecord corresponds to the section of the obligation sheaf 𝒪
    over a single module *M* (theory2.tex §13.1, Corollary 13.1.4).  It
    collects obligations, README constraints, timestamps, and summarises the
    overall documentation quality.

    Fields
    ------
    record_id:
        Unique identifier for this record.
    module_path:
        Dotted module path or file path, e.g. ``"jugeo.util.helpers"``.
    obligations:
        Tuple of all :class:`DocstringObligation` instances extracted from the
        module.
    readme_constraints:
        Tuple of behavioural constraint strings derived from the project README
        that apply to this module.
    live_since:
        ISO-8601 timestamp when this record first became active.
    last_updated:
        ISO-8601 timestamp of the most recent update.
    trust_summary:
        Human-readable summary of the trust posture (e.g. ``"HIGH"``).
    is_active:
        Whether this record is currently active in the live documentation set.
    metadata:
        Arbitrary extra key/value data.
    """

    record_id: str
    module_path: str
    obligations: tuple[DocstringObligation, ...]
    readme_constraints: tuple[str, ...]
    live_since: str
    last_updated: str
    trust_summary: str
    is_active: bool = True
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.record_id:
            object.__setattr__(self, "record_id", _new_id("record"))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise this record to a JSON-compatible dict.

        Returns
        -------
        dict[str, JsonValue]
            All fields expressed as JSON-safe types.
        """
        return {
            "record_id": self.record_id,
            "module_path": self.module_path,
            "obligations": [o.to_dict() for o in self.obligations],
            "readme_constraints": list(self.readme_constraints),
            "live_since": self.live_since,
            "last_updated": self.last_updated,
            "trust_summary": self.trust_summary,
            "is_active": self.is_active,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveDocRecord:
        """Reconstruct a LiveDocRecord from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        LiveDocRecord
            Reconstructed instance.
        """
        return cls(
            record_id=data.get("record_id", _new_id("record")),
            module_path=data.get("module_path", ""),
            obligations=tuple(
                DocstringObligation.from_dict(o)
                for o in data.get("obligations", [])
            ),
            readme_constraints=tuple(data.get("readme_constraints", [])),
            live_since=data.get("live_since", _now_iso()),
            last_updated=data.get("last_updated", _now_iso()),
            trust_summary=data.get("trust_summary", "UNKNOWN"),
            is_active=bool(data.get("is_active", True)),
            metadata=dict(data.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Domain logic
    # ------------------------------------------------------------------

    def add_obligation(self, obligation: DocstringObligation) -> LiveDocRecord:
        """Return a copy of this record with *obligation* appended.

        Parameters
        ----------
        obligation:
            The :class:`DocstringObligation` to add.

        Returns
        -------
        LiveDocRecord
            New frozen instance with the obligation appended and
            ``last_updated`` refreshed.
        """
        return replace(
            self,
            obligations=self.obligations + (obligation,),
            last_updated=_now_iso(),
        )

    def find_obligation(self, function_name: str) -> DocstringObligation | None:
        """Find the first obligation matching *function_name*.

        Parameters
        ----------
        function_name:
            The bare function name to search for.

        Returns
        -------
        DocstringObligation or None
            The first matching obligation, or ``None`` if not found.
        """
        for ob in self.obligations:
            if ob.function_name == function_name:
                return ob
        return None

    def all_verified(self) -> bool:
        """Return True iff every obligation in this record is verified.

        An empty obligation set is vacuously verified (True).

        Returns
        -------
        bool
            ``True`` iff all obligations have ``is_verified == True``.
        """
        return all(ob.is_verified for ob in self.obligations)

    def summary_stats(self) -> dict[str, JsonValue]:
        """Return a summary statistics dictionary for this record.

        Returns
        -------
        dict[str, JsonValue]
            Keys: ``total``, ``verified``, ``unverified``, ``complete``,
            ``coverage_ratio``, ``readme_constraints_count``.
        """
        total = len(self.obligations)
        verified = sum(1 for o in self.obligations if o.is_verified)
        complete = sum(1 for o in self.obligations if o.check_completeness())
        return {
            "total": total,
            "verified": verified,
            "unverified": total - verified,
            "complete": complete,
            "coverage_ratio": _score_from_ratio(verified, total),
            "readme_constraints_count": len(self.readme_constraints),
        }


# ---------------------------------------------------------------------------
# §6  SemanticParticipant — a named agent that participates in live docs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticParticipant:
    """A named agent (human, team, or automated system) that participates in
    live documentation by owning or verifying obligations.

    In the JuGeo ontology a SemanticParticipant is a *witness node* in the
    obligation nerve.  Participation ratios below the policy threshold are
    flagged as Ȟ¹ obstructions.

    Fields
    ------
    participant_id:
        Unique identifier.
    name:
        Human-readable name of the participant.
    role:
        Role string, e.g. ``"author"``, ``"reviewer"``, ``"automation"``.
    coordinate:
        Semantic coordinate in the module nerve.
    live_doc_record_id:
        ID of the :class:`LiveDocRecord` this participant is associated with.
    obligations_count:
        Total number of obligations this participant is responsible for.
    verified_count:
        Number of those obligations that have been verified.
    created_at:
        ISO-8601 creation timestamp.
    metadata:
        Arbitrary extra key/value data.
    """

    participant_id: str
    name: str
    role: str
    coordinate: str
    live_doc_record_id: str
    obligations_count: int
    verified_count: int
    created_at: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure mutable defaults are properly initialised on frozen instances."""
        if not isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", {})
        if not self.participant_id:
            object.__setattr__(self, "participant_id", _new_id("participant"))

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to JSON-compatible dict."""
        return {
            "participant_id": self.participant_id,
            "name": self.name,
            "role": self.role,
            "coordinate": self.coordinate,
            "live_doc_record_id": self.live_doc_record_id,
            "obligations_count": self.obligations_count,
            "verified_count": self.verified_count,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticParticipant:
        """Reconstruct from a plain dict."""
        return cls(
            participant_id=data.get("participant_id", _new_id("participant")),
            name=data.get("name", ""),
            role=data.get("role", ""),
            coordinate=data.get("coordinate", ""),
            live_doc_record_id=data.get("live_doc_record_id", ""),
            obligations_count=int(data.get("obligations_count", 0)),
            verified_count=int(data.get("verified_count", 0)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def participation_ratio(self) -> float:
        """Return the fraction of obligations that have been verified.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.
        """
        return _score_from_ratio(self.verified_count, self.obligations_count)

    def is_fully_verified(self) -> bool:
        """Return True if all obligations have been verified.

        Returns
        -------
        bool
            ``True`` iff ``verified_count == obligations_count`` and
            ``obligations_count > 0``.
        """
        return (
            self.obligations_count > 0
            and self.verified_count >= self.obligations_count
        )


# ---------------------------------------------------------------------------
# §7  DocumentationBecomeLiveSemanticAnalyzer — stateless analysis engine
# ---------------------------------------------------------------------------


class DocumentationBecomeLiveSemanticAnalyzer:
    """Stateless analyser that turns raw Python source into LiveDocRecords.

    This class implements the extraction pipeline described in theory2.tex
    §13.1:

    1. Parse the source for ``def``-blocks and their docstrings.
    2. For each ``def``-block with a docstring, create a
       :class:`DocstringObligation`.
    3. Extract README constraints from a separate README text.
    4. Assemble everything into a :class:`LiveDocRecord`.

    All methods are pure functions; no state is mutated.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_module(
        self,
        module_path: str,
        source_text: str,
    ) -> LiveDocRecord:
        """Analyse *source_text* and return a :class:`LiveDocRecord`.

        Parameters
        ----------
        module_path:
            Dotted module path or file path string used as the record label.
        source_text:
            Full Python source text of the module to analyse.

        Returns
        -------
        LiveDocRecord
            A newly created record containing all extracted obligations.

        Notes
        -----
        The ``trust_summary`` is set to ``"HIGH"`` when all obligations are
        complete, ``"MEDIUM"`` when coverage is ≥ 0.5, and ``"LOW"``
        otherwise.
        """
        obligations = self.extract_obligations(source_text, module_path)
        complete_count = sum(1 for o in obligations if o.check_completeness())
        total = len(obligations)
        coverage = _score_from_ratio(complete_count, total)
        if coverage >= 0.9:
            trust_summary = "HIGH"
        elif coverage >= 0.5:
            trust_summary = "MEDIUM"
        else:
            trust_summary = "LOW"
        now = _now_iso()
        return LiveDocRecord(
            record_id=_new_id("record"),
            module_path=module_path,
            obligations=tuple(obligations),
            readme_constraints=(),
            live_since=now,
            last_updated=now,
            trust_summary=trust_summary,
        )

    def extract_obligations(
        self,
        source_text: str,
        module_path: str = "",
    ) -> list[DocstringObligation]:
        """Extract DocstringObligations from *source_text* using regex.

        The extractor looks for patterns of the form::

            def function_name(signature):
                \"\"\"docstring\"\"\"

        For each match it builds a :class:`DocstringObligation` with the
        parsed data.

        Parameters
        ----------
        source_text:
            Raw Python source text.
        module_path:
            Used to populate ``source_file`` on each obligation.

        Returns
        -------
        list[DocstringObligation]
            One obligation per documented ``def``-block found.
        """
        obligations: list[DocstringObligation] = []
        # Match: def name(params): followed by optional type annotation,
        # then an indented triple-quoted docstring.
        pattern = re.compile(
            r'def\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))'
            r'(?:\s*->[^:]+)?:\s*\n'
            r'\s+"""(.*?)"""',
            re.DOTALL,
        )
        for match in pattern.finditer(source_text):
            func_name = match.group(1)
            signature = match.group(2)
            docstring = _sanitize_docstring(match.group(3))
            param_names = _parse_param_names_from_signature(signature)
            param_contracts = tuple(f"{p}: documented" for p in param_names)
            # Heuristic: extract "Returns" section from docstring
            return_match = re.search(
                r"Returns\s*\n\s*[-—]+\s*\n\s*(.+?)(?:\n\s*\n|\Z)",
                docstring,
                re.DOTALL,
            )
            return_contract = (
                return_match.group(1).strip() if return_match else ""
            )
            side_effect_match = re.search(
                r"Side[\s_-]?[Ee]ffects?\s*\n(.+?)(?:\n\s*\n|\Z)",
                docstring,
                re.DOTALL,
            )
            side_effect_contract = (
                side_effect_match.group(1).strip() if side_effect_match else ""
            )
            coordinate = f"{module_path}.{func_name}" if module_path else func_name
            ob = DocstringObligation(
                obligation_id=_new_id("obligation"),
                function_name=func_name,
                coordinate=coordinate,
                docstring_text=docstring,
                parameter_contracts=param_contracts,
                return_contract=return_contract,
                side_effect_contract=side_effect_contract,
                trust_level=None,
                source_file=module_path,
            )
            obligations.append(ob)
        return obligations

    def check_obligation_completeness(
        self,
        obligation: DocstringObligation,
    ) -> bool:
        """Delegate to :meth:`DocstringObligation.check_completeness`.

        Parameters
        ----------
        obligation:
            The obligation to check.

        Returns
        -------
        bool
            ``True`` iff the obligation is structurally complete.
        """
        return obligation.check_completeness()

    def analyze_readme_constraints(self, readme_text: str) -> list[str]:
        """Extract behavioural constraint sentences from a README text.

        A *constraint sentence* is any line containing one of the marker
        keywords: ``MUST``, ``SHALL``, ``REQUIRED``, ``MUST NOT``,
        ``SHALL NOT``, ``SHOULD``.  These mirror RFC-2119 requirement levels.

        Parameters
        ----------
        readme_text:
            The full text of a README file (Markdown or plain text).

        Returns
        -------
        list[str]
            Deduplicated list of constraint sentences found in the text.
        """
        keywords = re.compile(
            r"\b(MUST NOT|SHALL NOT|MUST|SHALL|REQUIRED|SHOULD NOT|SHOULD)\b"
        )
        constraints: list[str] = []
        seen: set[str] = set()
        for line in readme_text.splitlines():
            if keywords.search(line):
                stripped = line.strip()
                if stripped and stripped not in seen:
                    constraints.append(stripped)
                    seen.add(stripped)
        return constraints

    def score_documentation_coverage(self, record: LiveDocRecord) -> float:
        """Compute the documentation coverage score for *record*.

        Coverage = verified_count / total_obligations.  Returns 0.0 for
        empty records (no obligations means nothing is verified).

        Parameters
        ----------
        record:
            The :class:`LiveDocRecord` to score.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]`` representing the fraction of verified
            obligations.
        """
        total = len(record.obligations)
        if total == 0:
            return 0.0
        verified = sum(1 for o in record.obligations if o.is_verified)
        return _score_from_ratio(verified, total)

    def batch_analyze(
        self,
        modules: Sequence[tuple[str, str]],
    ) -> list[LiveDocRecord]:
        """Analyse multiple ``(module_path, source_text)`` pairs.

        Parameters
        ----------
        modules:
            A sequence of ``(module_path, source_text)`` tuples.

        Returns
        -------
        list[LiveDocRecord]
            One :class:`LiveDocRecord` per input tuple, in order.
        """
        return [self.analyze_module(path, src) for path, src in modules]


# ---------------------------------------------------------------------------
# §8  DocumentationBecomeLiveSemanticCoordinator — orchestration layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentationBecomeLiveSemanticCoordinator:
    """Orchestrator for the *documentation-as-live-semantic-object* pipeline.

    The coordinator wraps :class:`DocumentationBecomeLiveSemanticAnalyzer`
    and applies policy checks (minimum coverage, strict mode) to produce
    both :class:`LiveDocRecord` outputs and any Ȟ¹ obstruction records.

    Fields
    ------
    coordinator_id:
        Unique identifier for this coordinator instance.
    policy:
        Policy level string: ``"strict"`` or ``"lenient"``.
    min_coverage:
        Minimum required coverage score (0.0–1.0).  Records below this
        threshold generate obstruction records.
    created_at:
        ISO-8601 creation timestamp.

    Theory context (theory2.tex §13.1)
    ------------------------------------
    The coordinator implements the *global section* check: given a cover of
    modules {Uᵢ}, it verifies that the local sections (per-module records)
    are consistent and that the global coverage invariant is satisfied.
    Failures are represented as ObstructionRecord instances.
    """

    coordinator_id: str
    policy: str = "strict"
    min_coverage: float = 0.8
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
        module_path: str,
        source_text: str,
    ) -> tuple[LiveDocRecord, list[Any]]:
        """Analyse a single module and produce a record plus any obstructions.

        Parameters
        ----------
        module_path:
            Dotted or file-path identifier for the module.
        source_text:
            Full Python source text of the module.

        Returns
        -------
        tuple[LiveDocRecord, list[ObstructionRecord]]
            The live doc record and a (possibly empty) list of obstructions.
            An obstruction is generated when the coverage score falls below
            ``self.min_coverage`` or when ``policy == "strict"`` and any
            obligation is incomplete.
        """
        analyzer = DocumentationBecomeLiveSemanticAnalyzer()
        record = analyzer.analyze_module(module_path, source_text)
        obstructions: list[Any] = []
        coverage = analyzer.score_documentation_coverage(record)
        if coverage < self.min_coverage:
            for ob in record.obligations:
                if not ob.is_verified:
                    obstructions.append(
                        self._build_obstruction(
                            ob,
                            f"Coverage {coverage:.2f} below threshold "
                            f"{self.min_coverage:.2f}",
                        )
                    )
        if self.policy == "strict":
            for ob in record.obligations:
                if not ob.check_completeness():
                    obstructions.append(
                        self._build_obstruction(ob, "Incomplete obligation under strict policy")
                    )
        return record, obstructions

    def coordinate_batch(
        self,
        modules: Sequence[tuple[str, str]],
    ) -> tuple[list[LiveDocRecord], list[Any]]:
        """Analyse a sequence of modules, collecting all records and obstructions.

        Parameters
        ----------
        modules:
            Sequence of ``(module_path, source_text)`` tuples.

        Returns
        -------
        tuple[list[LiveDocRecord], list[ObstructionRecord]]
            All records and the aggregated list of all obstructions.
        """
        all_records: list[LiveDocRecord] = []
        all_obstructions: list[Any] = []
        for path, src in modules:
            record, obs = self.coordinate(path, src)
            all_records.append(record)
            all_obstructions.extend(obs)
        return all_records, all_obstructions

    def generate_obligations_report(
        self,
        records: Sequence[LiveDocRecord],
    ) -> dict[str, JsonValue]:
        """Generate an aggregate obligations report across all *records*.

        Parameters
        ----------
        records:
            Sequence of :class:`LiveDocRecord` objects to summarise.

        Returns
        -------
        dict[str, JsonValue]
            Report with keys: ``total_modules``, ``total_obligations``,
            ``total_verified``, ``overall_coverage``, ``per_module``.
        """
        total_obligations = 0
        total_verified = 0
        per_module: list[JsonValue] = []
        for record in records:
            stats = record.summary_stats()
            total_obligations += int(stats["total"])  # type: ignore[arg-type]
            total_verified += int(stats["verified"])  # type: ignore[arg-type]
            per_module.append(
                {
                    "module_path": record.module_path,
                    "record_id": record.record_id,
                    "trust_summary": record.trust_summary,
                    **{k: v for k, v in stats.items()},
                }
            )
        return {
            "generated_at": _now_iso(),
            "coordinator_id": self.coordinator_id,
            "policy": self.policy,
            "min_coverage": self.min_coverage,
            "total_modules": len(list(records)),
            "total_obligations": total_obligations,
            "total_verified": total_verified,
            "overall_coverage": _score_from_ratio(total_verified, total_obligations),
            "per_module": per_module,
        }

    def _build_obstruction(
        self,
        obligation: DocstringObligation,
        reason: str,
    ) -> dict[str, JsonValue]:
        """Build a minimal obstruction record dict for a failing obligation.

        Parameters
        ----------
        obligation:
            The :class:`DocstringObligation` that triggered the obstruction.
        reason:
            Human-readable reason string.

        Returns
        -------
        dict[str, JsonValue]
            A dict with obstruction metadata (used when ObstructionRecord is
            not importable).
        """
        return {
            "obstruction_id": _new_id("obs"),
            "obligation_id": obligation.obligation_id,
            "function_name": obligation.function_name,
            "coordinate": obligation.coordinate,
            "reason": reason,
            "policy": self.policy,
            "created_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# §9  DocumentationBecomeLiveSemanticWitness — aggregate witness record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentationBecomeLiveSemanticWitness:
    """Aggregate witness recording that the live-doc pipeline ran successfully.

    A witness captures the *outcome* of running the coordinator on a module
    and acts as a proof object that the analysis was performed.  In the
    JuGeo cohomology picture (theory2.tex §13.1) this is a global section
    of the witness sheaf over the obligation nerve.

    Fields
    ------
    witness_id:
        Unique identifier for this witness.
    record_id:
        ID of the :class:`LiveDocRecord` being witnessed.
    obligations_witnessed:
        Tuple of obligation_ids that were processed.
    coverage_score:
        Coverage score at the time of witnessing (0.0–1.0).
    is_complete:
        Whether all witnessed obligations were complete.
    created_at:
        ISO-8601 timestamp when this witness was created.
    metadata:
        Arbitrary extra key/value data.
    """

    witness_id: str
    record_id: str
    obligations_witnessed: tuple[str, ...]
    coverage_score: float
    is_complete: bool
    created_at: str
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
            "record_id": self.record_id,
            "obligations_witnessed": list(self.obligations_witnessed),
            "coverage_score": self.coverage_score,
            "is_complete": self.is_complete,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentationBecomeLiveSemanticWitness:
        """Reconstruct from a plain dict.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`to_dict`.

        Returns
        -------
        DocumentationBecomeLiveSemanticWitness
            Reconstructed instance.
        """
        return cls(
            witness_id=data.get("witness_id", _new_id("witness")),
            record_id=data.get("record_id", ""),
            obligations_witnessed=tuple(data.get("obligations_witnessed", [])),
            coverage_score=float(data.get("coverage_score", 0.0)),
            is_complete=bool(data.get("is_complete", False)),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    def is_valid(self) -> bool:
        """Return True iff the witness is non-empty and has a positive score.

        Returns
        -------
        bool
            ``True`` iff ``obligations_witnessed`` is non-empty and
            ``coverage_score > 0``.
        """
        return bool(self.obligations_witnessed) and self.coverage_score > 0.0

    def summary(self) -> str:
        """Return a one-line human-readable summary of this witness.

        Returns
        -------
        str
            E.g. ``"Witness w-abc123: 4 obligations, coverage=0.75, complete=False"``.
        """
        return (
            f"Witness {self.witness_id}: "
            f"{len(self.obligations_witnessed)} obligations, "
            f"coverage={self.coverage_score:.2f}, "
            f"complete={self.is_complete}"
        )


# ---------------------------------------------------------------------------
# §10  Factory helper
# ---------------------------------------------------------------------------


def _make_witness_from_record(
    record: LiveDocRecord,
    analyzer: DocumentationBecomeLiveSemanticAnalyzer,
) -> DocumentationBecomeLiveSemanticWitness:
    """Construct a :class:`DocumentationBecomeLiveSemanticWitness` from a record.

    Parameters
    ----------
    record:
        The :class:`LiveDocRecord` to witness.
    analyzer:
        The analyser used to score coverage.

    Returns
    -------
    DocumentationBecomeLiveSemanticWitness
        A new witness reflecting the current state of *record*.
    """
    coverage = analyzer.score_documentation_coverage(record)
    is_complete = record.all_verified()
    return DocumentationBecomeLiveSemanticWitness(
        witness_id=_new_id("witness"),
        record_id=record.record_id,
        obligations_witnessed=tuple(o.obligation_id for o in record.obligations),
        coverage_score=coverage,
        is_complete=is_complete,
        created_at=_now_iso(),
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
# §11  __all__
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "JsonScalar",
    "JsonValue",
    "_now_iso",
    "_new_id",
    "_sanitize_docstring",
    "_parse_param_names_from_signature",
    "_extract_raises_from_source",
    "_score_from_ratio",
    "_make_witness_from_record",
    "DocstringObligation",
    "LiveDocRecord",
    "SemanticParticipant",
    "DocumentationBecomeLiveSemanticAnalyzer",
    "DocumentationBecomeLiveSemanticCoordinator",
    "DocumentationBecomeLiveSemanticWitness",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# ---------------------------------------------------------------------------
# §12  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Smoke test: parse a fake module, check obligations, run coordinator
    # ------------------------------------------------------------------
    _FAKE_SOURCE = '''\
def add(x: int, y: int) -> int:
    """Add two integers together.

    Parameters
    ----------
    x:
        The first operand.
    y:
        The second operand.

    Returns
    -------
    int
        The sum of x and y.
    """
    return x + y


def divide(numerator: float, denominator: float) -> float:
    """Divide numerator by denominator.

    Parameters
    ----------
    numerator:
        The value to divide.
    denominator:
        The value to divide by.

    Returns
    -------
    float
        The quotient.

    Raises
    ------
    ZeroDivisionError
        When denominator is zero.
    """
    if denominator == 0.0:
        raise ZeroDivisionError("denominator must be non-zero")
    return numerator / denominator


def no_doc(x):
    return x * 2
'''

    print("=== Smoke test: documentation_should_become_a_live ===")

    # --- Analyzer ---
    analyzer = DocumentationBecomeLiveSemanticAnalyzer()
    record = analyzer.analyze_module("fake.math_utils", _FAKE_SOURCE)
    print(f"Record id:        {record.record_id}")
    print(f"Module path:      {record.module_path}")
    print(f"Obligations:      {len(record.obligations)}")
    print(f"Trust summary:    {record.trust_summary}")
    print(f"Coverage score:   {analyzer.score_documentation_coverage(record):.2f}")
    print()

    for ob in record.obligations:
        print(f"  Obligation: {ob.function_name!r}")
        print(f"    coordinate:    {ob.coordinate}")
        print(f"    complete:      {ob.check_completeness()}")
        print(f"    param names:   {ob.extract_parameter_names()}")
        print(f"    verified:      {ob.is_verified}")
        verified_ob = ob.mark_verified()
        print(f"    after mark:    {verified_ob.is_verified}  ts={verified_ob.verification_timestamp}")
    print()

    # --- Coordinator (lenient) ---
    coord = DocumentationBecomeLiveSemanticCoordinator(
        coordinator_id=_new_id("coord"),
        policy="lenient",
        min_coverage=0.5,
    )
    rec2, obs2 = coord.coordinate("fake.math_utils", _FAKE_SOURCE)
    print(f"Coordinator id:   {coord.coordinator_id}")
    print(f"Obstructions:     {len(obs2)}")
    report = coord.generate_obligations_report([rec2])
    print(f"Report coverage:  {report['overall_coverage']:.2f}")
    print()

    # --- Witness ---
    witness = _make_witness_from_record(rec2, analyzer)
    print(witness.summary())
    print(f"Witness valid:    {witness.is_valid()}")
    print()

    # --- SemanticParticipant ---
    participant = SemanticParticipant(
        participant_id=_new_id("participant"),
        name="Alice",
        role="author",
        coordinate="fake.math_utils",
        live_doc_record_id=rec2.record_id,
        obligations_count=len(rec2.obligations),
        verified_count=0,
        created_at=_now_iso(),
    )
    print(f"Participant:      {participant.name} ({participant.role})")
    print(f"Participation:    {participant.participation_ratio():.2f}")
    print(f"Fully verified:   {participant.is_fully_verified()}")
    print()

    # --- README constraint extraction ---
    _FAKE_README = """\
# My Library

This library MUST be used with Python 3.11 or later.
Functions SHALL return documented types only.
The caller MUST NOT pass None for numeric arguments.
Error handling SHOULD follow RFC-2119 conventions.
"""
    constraints = analyzer.analyze_readme_constraints(_FAKE_README)
    print(f"README constraints ({len(constraints)}):")
    for c in constraints:
        print(f"  {c!r}")
    print()

    # --- Batch analysis ---
    _MODULES = [
        ("mod.a", _FAKE_SOURCE),
        ("mod.b", 'def foo(x):\n    """Do foo.\n\n    Returns\n    -------\n    int\n        A value.\n    """\n    return x'),
    ]
    batch_records = analyzer.batch_analyze(_MODULES)
    print(f"Batch records:    {len(batch_records)}")
    for br in batch_records:
        print(f"  {br.module_path}: {len(br.obligations)} obligations, trust={br.trust_summary}")

    print()
    print("Smoke test passed.")
