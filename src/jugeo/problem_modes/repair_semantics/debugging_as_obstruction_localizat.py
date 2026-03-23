"""Debugging as obstruction localization (theory2.tex Ch11 §11.2).

Stage 01 of the debugging chapter: given a failing judgment, locate the
minimal coordinate in the judgment sheaf that carries the cohomology
obstruction.  The central claim is that *debugging is obstruction localization*
— finding which open set U_i ∈ 𝔘 in the cover of the semantic site is the
source of the failure.

Theory basis (theory2.tex §11.2 — Debugging as Obstruction Localization)
-------------------------------------------------------------------------
In the sheaf-theoretic framework a bug is not a string message — it is a
cohomology class [η] ∈ Ȟ¹(𝔘, 𝒟).  Concretely:

* The semantic site is covered by a family 𝔘 = {U_0, U_1, …, U_n} of open
  patches, each corresponding to a sub-problem or sub-judgment.
* A *local section* s_i : U_i → 𝒟 exists for each patch.
* The *descent datum* {s_{ij}} records how sections agree (or disagree) on
  overlaps U_i ∩ U_j.
* The obstruction class [η] is non-trivial exactly when the local sections
  cannot be glued into a global section — i.e., when some patch U_i carries
  an irreconcilable local failure.

Obstruction localization
------------------------
Given a test predicate φ : 𝔘 → {True, False} that evaluates each patch, the
localization problem is:

    Find the minimal i ∈ {0, …, n} such that φ(U_i) = False
    but ∀ j < i : φ(U_j) = True.

This is analogous to binary search / bisection and is implemented as such.
The algorithm produces a *witness* — a :class:`DebuggingObstructionLocalizationWitness`
— that certifies the coordinate, depth, and localization path.

Algorithms
----------
* **BINARY_SEARCH** — standard bisection; O(log n) predicate evaluations.
* **LINEAR_SCAN**   — sequential scan; O(n); useful when n is small or ordering
  matters semantically.
* **DELTA_DEBUG**   — subset reduction à la Zeller 1999; finds a 1-minimal
  failing subset.
* **DEPTH_FIRST**   — DFS over a coordinate tree; useful when patches are
  hierarchically organized.
* **BREADTH_FIRST** — BFS variant; finds shallowest failing patch first.

Obstruction classes
-------------------
* ``H1_COHERENCE`` — failure of coherence on overlaps (most common).
* ``H1_DESCENT``   — descent datum is inconsistent (gluing fails globally).
* ``H0_LOCAL``     — failure confined to a single local section.
* ``H0_TRIVIAL``   — predicate always true; no obstruction (degenerate).
* ``H2_HIGHER``    — higher-order obstruction; rare, indicates deep semantic
  inconsistency.

Notation
--------
* ``coord``  — a dot-separated semantic coordinate string.
* ``patch``  — a string identifier for an open set in the cover.
* ``[η]``    — the Čech 1-cocycle witnessing the obstruction.

# copilot: s01 debugging as obstruction localization — theory2 ch11 §11.2
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.errors import (
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        StructuredFailure,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        JuGeoError,
        raise_with_scope,
    )
except ImportError:
    ObstructionRecord = Any; RepairHint = Any; RepairPriority = Any  # type: ignore
    StructuredFailure = Any; FailureScope = Any; FailureClassification = Any  # type: ignore
    EvidenceFamily = Any; JuGeoError = Exception; raise_with_scope = None  # type: ignore

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Provenance,
        ProvenanceSource,
        TrustLevel,
        TrustAnnotation,
        Obstruction,
    )
except ImportError:
    EvidenceBundle = Any; EvidenceItem = Any; EvidenceItemKind = Any  # type: ignore
    Provenance = Any; ProvenanceSource = Any; TrustLevel = Any  # type: ignore
    TrustAnnotation = Any; Obstruction = Any  # type: ignore

try:
    from jugeo.solver.countermodels import FailureClass, RepairType
except ImportError:
    FailureClass = Any; RepairType = Any  # type: ignore

try:
    from jugeo.problem_modes.repair_semantics.models import (
        CounterexampleRecord,
        DebugSession,
        RepairFrontier,
        RepairPlan,
        RepairValidator,
    )
except ImportError:
    CounterexampleRecord = Any; DebugSession = Any  # type: ignore
    RepairFrontier = Any; RepairPlan = Any; RepairValidator = Any  # type: ignore

# ---------------------------------------------------------------------------
# §0  Module-level metadata
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "module": "jugeo.problem_modes.repair_semantics.debugging_as_obstruction_localizat",
    "theory_chapter": "theory2.tex Ch11",
    "theory_section": "§11.2",
    "title": "Debugging as Obstruction Localization",
    "stage": "s01",
    "algorithm_family": "bisection/delta-debug over sheaf cover",
    "cohomology_group": "H1(U, D)",
    "version": "1.0.0",
}

# ---------------------------------------------------------------------------
# §1  Helper functions
# ---------------------------------------------------------------------------


def _iso_timestamp() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        Timestamp in the form ``"2024-01-15T12:34:56.789012+00:00"``.
    """
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _stable_hash8(s: str) -> str:
    """Return the first 8 hex characters of the SHA-256 digest of *s*.

    Parameters
    ----------
    s : str
        Arbitrary string to hash.

    Returns
    -------
    str
        An 8-character lowercase hex string that is stable across runs.

    Notes
    -----
    Used as a short, deterministic identifier for patch keys and witness IDs.
    """
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _cover_patches_to_key(patches: Sequence[str]) -> str:
    """Produce a stable string key for a sequence of patch identifiers.

    Parameters
    ----------
    patches : Sequence[str]
        Ordered list of patch name strings.

    Returns
    -------
    str
        A deterministic key of the form ``"cover:<hash8>:<n>"`` where *hash8*
        is derived from the JSON-serialised patch list and *n* is the length.

    Notes
    -----
    The resulting key is suitable for use as a dict key or cache key.
    """
    serialised = json.dumps(list(patches), sort_keys=False, separators=(",", ":"))
    h = _stable_hash8(serialised)
    return f"cover:{h}:{len(patches)}"


def _bisect_patches(
    patches: tuple[str, ...],
    results: tuple[bool, ...],
) -> tuple[str, tuple[str, ...]]:
    """Standalone binary-search localization over an ordered patch sequence.

    Given *patches* P = (p_0, …, p_{n-1}) and *results* R = (r_0, …, r_{n-1})
    where r_i = φ(p_i), find the first index *k* such that r_k = False and
    return (p_k, localization_path).

    The algorithm mirrors classical bisection: it repeatedly halves the search
    space by checking whether the lower half contains any failure.

    Parameters
    ----------
    patches : tuple[str, ...]
        Ordered sequence of patch identifiers.
    results : tuple[bool, ...]
        Corresponding predicate results; ``True`` = pass, ``False`` = fail.

    Returns
    -------
    tuple[str, tuple[str, ...]]
        A pair ``(minimal_patch, path)`` where *minimal_patch* is the patch
        identifier of the first failing patch (earliest index), and *path* is
        the sequence of candidate patches examined during bisection.

    Notes
    -----
    * If no patch fails (all ``True``), returns ``("", ())``.
    * Matches the *BINARY_SEARCH* strategy in
      :class:`DebuggingObstructionLocalizationAnalyzer`.
    """
    if not patches or not results:
        return ("", ())
    if len(patches) != len(results):
        raise ValueError(
            f"patches/results length mismatch: {len(patches)} vs {len(results)}"
        )

    path: list[str] = []
    lo, hi = 0, len(patches) - 1

    # Quick check: no failure at all
    if all(results):
        return ("", ())

    # Iterative bisection
    while lo < hi:
        mid = (lo + hi) // 2
        path.append(patches[mid])
        # Does the lower half [lo..mid] contain a failure?
        lower_has_failure = any(not results[i] for i in range(lo, mid + 1))
        if lower_has_failure:
            hi = mid
        else:
            lo = mid + 1

    # lo == hi at this point; verify it is indeed a failure
    path.append(patches[lo])
    if results[lo]:
        # Bisection landed on a passing patch; scan forward for the real first failure
        for i in range(len(results)):
            if not results[i]:
                path.append(patches[i])
                return (patches[i], tuple(path))
        return ("", tuple(path))

    return (patches[lo], tuple(path))


def _compute_confidence(
    path_length: int,
    is_minimal: bool,
    has_patch: bool,
) -> float:
    """Compute a [0, 1] confidence score for a localization result.

    Parameters
    ----------
    path_length : int
        Number of patches examined during bisection.  Shorter paths (relative
        to the total cover) indicate higher confidence.
    is_minimal : bool
        Whether the result is certified to be the *minimal* failing patch.
    has_patch : bool
        Whether a non-empty patch was found.

    Returns
    -------
    float
        Confidence in [0.0, 1.0].

    Notes
    -----
    Formula::

        base = 1.0 if has_patch else 0.0
        path_penalty = min(0.3, path_length * 0.02)
        minimality_bonus = 0.15 if is_minimal else 0.0
        confidence = clamp(base - path_penalty + minimality_bonus, 0.0, 1.0)
    """
    if not has_patch:
        return 0.0
    base = 1.0
    path_penalty = min(0.30, path_length * 0.02)
    minimality_bonus = 0.15 if is_minimal else 0.0
    return max(0.0, min(1.0, base - path_penalty + minimality_bonus))


def _obstruction_class_from_depth(depth: int, failure_rate: float) -> str:
    """Classify an obstruction by coordinate depth and failure rate.

    Parameters
    ----------
    depth : int
        Depth of the failing coordinate in the judgment tree (0 = root).
    failure_rate : float
        Fraction of patches that fail in [0, 1].

    Returns
    -------
    str
        One of: ``"H0_TRIVIAL"``, ``"H0_LOCAL"``, ``"H1_COHERENCE"``,
        ``"H1_DESCENT"``, ``"H2_HIGHER"``.

    Notes
    -----
    Heuristic classification table:

    +--------------+----------+---------------------+
    | depth        | rate     | class               |
    +==============+==========+=====================+
    | any          | 0.0      | H0_TRIVIAL          |
    | 0            | (0, 0.3] | H0_LOCAL            |
    | 0–1          | (0.3, 1] | H1_COHERENCE        |
    | ≥2           | (0, 0.5] | H1_DESCENT          |
    | ≥2           | (0.5, 1] | H2_HIGHER           |
    +--------------+----------+---------------------+
    """
    if failure_rate <= 0.0:
        return "H0_TRIVIAL"
    if depth == 0 and failure_rate <= 0.30:
        return "H0_LOCAL"
    if depth <= 1 and failure_rate <= 1.0:
        return "H1_COHERENCE"
    if depth >= 2 and failure_rate <= 0.50:
        return "H1_DESCENT"
    return "H2_HIGHER"


# ---------------------------------------------------------------------------
# §2  Enumerations
# ---------------------------------------------------------------------------


class LocalizationStrategy(str, Enum):
    """Strategy enum for obstruction localization algorithms.

    Each member identifies a distinct search algorithm used by
    :class:`DebuggingObstructionLocalizationAnalyzer` to find the minimal
    failing patch in a cover.

    Members
    -------
    BINARY_SEARCH
        Bisection over the ordered patch list; O(log n) steps.  Assumes the
        predicate is *monotone*: once it becomes False it stays False.
    LINEAR_SCAN
        Left-to-right sequential scan; O(n) steps.  Correct even when the
        predicate is non-monotone; returns the *first* failing patch.
    DEPTH_FIRST
        Depth-first traversal when patches form a tree hierarchy.  Explores
        deeper branches before backtracking.
    BREADTH_FIRST
        Breadth-first traversal; finds the shallowest failure first.
    DELTA_DEBUG
        Zeller-style subset reduction: repeatedly halves the failing set until
        a 1-minimal failure is found.  Most expensive but most precise.
    """

    BINARY_SEARCH = "BINARY_SEARCH"
    LINEAR_SCAN = "LINEAR_SCAN"
    DEPTH_FIRST = "DEPTH_FIRST"
    BREADTH_FIRST = "BREADTH_FIRST"
    DELTA_DEBUG = "DELTA_DEBUG"


# ---------------------------------------------------------------------------
# §3  ObstructionLocality
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionLocality:
    """A record describing the exact location of a localized obstruction.

    An :class:`ObstructionLocality` is the geometric complement of a
    :class:`DebuggingObstructionLocalizationWitness`: where the witness
    records the *algorithmic* result of localization, the locality describes
    the *topological* position of the obstruction in the cover.

    Attributes
    ----------
    coordinate : str
        Dot-separated semantic coordinate of the failing patch, e.g.
        ``"root.child.leaf"``.
    depth : int
        Depth of *coordinate* in the coordinate tree (number of dots + 1).
    parent_coordinate : str
        Coordinate of the parent node; empty string if *coordinate* is root.
    patch_id : str
        Identifier of the open set U_i that carries the obstruction.
    failure_predicate : str
        Human-readable description of the failing predicate φ.
    is_minimal : bool
        True when no proper sub-patch also fails, i.e., this is a *minimal*
        failing patch in the partial order of sub-patches.
    localization_confidence : float
        Confidence in [0, 1] that the localization is correct.
    """

    coordinate: str
    depth: int
    parent_coordinate: str
    patch_id: str
    failure_predicate: str
    is_minimal: bool
    localization_confidence: float

    # -- §3.1  Predicates --

    def is_root(self) -> bool:
        """Return True when this locality is at the root coordinate.

        Returns
        -------
        bool
            ``True`` iff ``depth == 0`` or ``parent_coordinate`` is empty.
        """
        return self.depth == 0 or self.parent_coordinate == ""

    def child_of(self, parent: str) -> bool:
        """Return True when this locality is a direct child of *parent*.

        Parameters
        ----------
        parent : str
            Candidate parent coordinate string.

        Returns
        -------
        bool
            ``True`` iff ``parent_coordinate == parent``.
        """
        return self.parent_coordinate == parent

    # -- §3.2  Serialization --

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            All fields as primitive Python types.
        """
        return {
            "coordinate": self.coordinate,
            "depth": self.depth,
            "parent_coordinate": self.parent_coordinate,
            "patch_id": self.patch_id,
            "failure_predicate": self.failure_predicate,
            "is_minimal": self.is_minimal,
            "localization_confidence": self.localization_confidence,
        }


# ---------------------------------------------------------------------------
# §4  DebuggingObstructionLocalizationWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DebuggingObstructionLocalizationWitness:
    """An immutable witness record certifying a localized obstruction.

    A witness is produced by :meth:`DebuggingObstructionLocalizationAnalyzer.localize`
    and records every detail needed to audit, reproduce, or dispute the
    localization result.  It is analogous to a *proof term* in type theory:
    the witness does not merely assert that an obstruction exists at some
    coordinate — it provides the localization path (the sequence of patches
    examined during bisection) and a confidence score.

    Theory connection (theory2.tex §11.2.3)
    ----------------------------------------
    Let φ be the failure predicate and 𝔘 the cover.  A witness certifies::

        ∃ U_k ∈ 𝔘 : φ(U_k) = False  ∧  ∀ U_j ⊂ U_k : φ(U_j) = True

    i.e., U_k is *minimal* with respect to the sub-cover order.

    Attributes
    ----------
    witness_id : str
        Globally unique identifier (UUID hex, auto-generated).
    coordinate : str
        Semantic coordinate of the failing patch.
    obstruction_class : str
        Cohomology class label, one of ``"H0_TRIVIAL"``, ``"H0_LOCAL"``,
        ``"H1_COHERENCE"``, ``"H1_DESCENT"``, ``"H2_HIGHER"``.
    failure_predicate : str
        Human-readable description of the predicate that failed.
    minimal_failing_patch : str
        Identifier of the minimal failing patch found by localization.
    depth_in_cover : int
        Depth of *coordinate* in the judgment coordinate tree.
    localization_path : tuple[str, ...]
        Ordered sequence of patches examined during the localization search.
    confidence : float
        Confidence score in [0, 1].
    strategy_used : str
        Name of the :class:`LocalizationStrategy` used.
    provenance : tuple[tuple[str, str], ...]
        Key-value provenance pairs, e.g. ``(("analyzer_id", "abc123"), …)``.
    timestamp : str
        ISO-8601 UTC timestamp of witness creation.
    """

    witness_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    coordinate: str = ""
    obstruction_class: str = "H1_COHERENCE"
    failure_predicate: str = ""
    minimal_failing_patch: str = ""
    depth_in_cover: int = 0
    localization_path: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    strategy_used: str = LocalizationStrategy.BINARY_SEARCH.value
    provenance: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    timestamp: str = field(default_factory=_iso_timestamp)

    # -- §4.1  Certification --

    def is_certified(self) -> bool:
        """Return True when the witness meets certification criteria.

        A witness is *certified* iff:

        * ``confidence > 0.9``
        * ``minimal_failing_patch`` is non-empty

        Returns
        -------
        bool
            Certification status.
        """
        return self.confidence > 0.9 and self.minimal_failing_patch != ""

    # -- §4.2  Serialization --

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields expressed as primitive Python types.  The
            *localization_path* and *provenance* tuples are serialised as
            lists for JSON compatibility.
        """
        return {
            "witness_id": self.witness_id,
            "coordinate": self.coordinate,
            "obstruction_class": self.obstruction_class,
            "failure_predicate": self.failure_predicate,
            "minimal_failing_patch": self.minimal_failing_patch,
            "depth_in_cover": self.depth_in_cover,
            "localization_path": list(self.localization_path),
            "confidence": self.confidence,
            "strategy_used": self.strategy_used,
            "provenance": [list(p) for p in self.provenance],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DebuggingObstructionLocalizationWitness":
        """Deserialise a witness from a dict produced by :meth:`to_dict`.

        Parameters
        ----------
        d : dict[str, Any]
            Dictionary with the same keys as :meth:`to_dict`.

        Returns
        -------
        DebuggingObstructionLocalizationWitness
            Reconstructed witness instance.

        Raises
        ------
        KeyError
            If a required key is absent from *d*.
        """
        return cls(
            witness_id=d.get("witness_id", uuid.uuid4().hex),
            coordinate=d.get("coordinate", ""),
            obstruction_class=d.get("obstruction_class", "H1_COHERENCE"),
            failure_predicate=d.get("failure_predicate", ""),
            minimal_failing_patch=d.get("minimal_failing_patch", ""),
            depth_in_cover=int(d.get("depth_in_cover", 0)),
            localization_path=tuple(d.get("localization_path", [])),
            confidence=float(d.get("confidence", 0.0)),
            strategy_used=d.get("strategy_used", LocalizationStrategy.BINARY_SEARCH.value),
            provenance=tuple(tuple(p) for p in d.get("provenance", [])),
            timestamp=d.get("timestamp", _iso_timestamp()),
        )

    def summary(self) -> str:
        """Return a concise human-readable summary of this witness.

        Returns
        -------
        str
            Single-line summary string suitable for logging or display.

        Examples
        --------
        >>> w.summary()
        'Witness abc12345: H1_COHERENCE @ root.child [patch=p3, conf=0.92, CERTIFIED]'
        """
        certified = "CERTIFIED" if self.is_certified() else "UNCERTIFIED"
        return (
            f"Witness {self.witness_id[:8]}: {self.obstruction_class} "
            f"@ {self.coordinate} [patch={self.minimal_failing_patch}, "
            f"conf={self.confidence:.3f}, {certified}]"
        )


# ---------------------------------------------------------------------------
# §5  DebuggingObstructionLocalizationAnalyzer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DebuggingObstructionLocalizationAnalyzer:
    """Core analyzer that localizes cohomology obstructions in a cover.

    The analyzer takes an ordered sequence of patches (open sets in the cover)
    and a corresponding sequence of predicate results (True/False), then
    applies the chosen :class:`LocalizationStrategy` to identify the minimal
    failing patch.

    This is the primary algorithmic workhorse of the debugging pipeline.
    See theory2.tex §11.2 for the theoretical justification.

    Attributes
    ----------
    analyzer_id : str
        Unique identifier for this analyzer instance.
    coordinate : str
        Root coordinate to analyze (dot-separated path string).
    strategy : str
        Name of the :class:`LocalizationStrategy` to apply.
    max_depth : int
        Maximum recursion/iteration depth for the localization algorithm.
    confidence_threshold : float
        Minimum confidence required to declare a localization result confident.
    enable_caching : bool
        If True, cache intermediate results for repeated calls.
    strict_mode : bool
        If True, raise on unexpected predicate patterns instead of falling back.

    Notes
    -----
    All methods are pure: the analyzer is frozen and produces new objects
    rather than mutating state.
    """

    analyzer_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    coordinate: str = ""
    strategy: str = LocalizationStrategy.BINARY_SEARCH.value
    max_depth: int = 32
    confidence_threshold: float = 0.85
    enable_caching: bool = True
    strict_mode: bool = False

    # -- §5.1  Localization core --

    def localize(
        self,
        patches: Sequence[str],
        predicate_results: Sequence[bool],
    ) -> DebuggingObstructionLocalizationWitness:
        """Localize the minimal failing patch in the given cover.

        Given an ordered cover 𝔘 = (p_0, …, p_{n-1}) and predicate results
        R = (r_0, …, r_{n-1}), find the minimal i such that r_i = False.

        The search algorithm is selected by ``self.strategy``.  If the chosen
        strategy finds no failure, a witness with empty ``minimal_failing_patch``
        and ``obstruction_class = "H0_TRIVIAL"`` is returned.

        Parameters
        ----------
        patches : Sequence[str]
            Ordered list of patch identifiers representing the cover 𝔘.
        predicate_results : Sequence[bool]
            Predicate evaluation results φ(p_i) for each patch.

        Returns
        -------
        DebuggingObstructionLocalizationWitness
            A fully populated witness record.

        Raises
        ------
        ValueError
            If *patches* and *predicate_results* have different lengths.
        ValueError
            If *patches* is empty in strict mode.

        Notes
        -----
        The *localization_path* recorded in the witness is the sequence of
        patches examined during the search, including the final answer.  It
        provides a complete audit trail.
        """
        patches_t = tuple(patches)
        results_t = tuple(predicate_results)

        if len(patches_t) != len(results_t):
            raise ValueError(
                f"patches ({len(patches_t)}) and predicate_results ({len(results_t)}) "
                "must have the same length."
            )
        if not patches_t:
            if self.strict_mode:
                raise ValueError("Empty patch list provided to localize() in strict mode.")
            return DebuggingObstructionLocalizationWitness(
                witness_id=uuid.uuid4().hex,
                coordinate=self.coordinate,
                obstruction_class="H0_TRIVIAL",
                failure_predicate="empty_cover",
                minimal_failing_patch="",
                depth_in_cover=self.compute_obstruction_depth(self.coordinate, self.coordinate),
                localization_path=(),
                confidence=0.0,
                strategy_used=self.strategy,
                provenance=(("analyzer_id", self.analyzer_id),),
                timestamp=_iso_timestamp(),
            )

        strategy = self.strategy
        if strategy == LocalizationStrategy.BINARY_SEARCH.value:
            minimal_patch, path = self._binary_search_localize(patches_t, results_t)
        elif strategy == LocalizationStrategy.LINEAR_SCAN.value:
            minimal_patch, path = self._linear_scan_localize(patches_t, results_t)
        elif strategy == LocalizationStrategy.DELTA_DEBUG.value:
            minimal_patch, path = self._delta_debug_localize(patches_t, results_t)
        elif strategy in (
            LocalizationStrategy.DEPTH_FIRST.value,
            LocalizationStrategy.BREADTH_FIRST.value,
        ):
            # Both DFS/BFS collapse to linear scan over the flat patch list
            minimal_patch, path = self._linear_scan_localize(patches_t, results_t)
        else:
            minimal_patch, path = self._binary_search_localize(patches_t, results_t)

        has_failure = minimal_patch != ""
        is_minimal = has_failure  # By construction of bisection
        depth = self.compute_obstruction_depth(self.coordinate, "")
        failure_rate = sum(1 for r in results_t if not r) / len(results_t)
        obstruction_class = (
            self.classify_obstruction_from_rate(failure_rate, depth)
            if has_failure
            else "H0_TRIVIAL"
        )
        confidence = _compute_confidence(
            path_length=len(path),
            is_minimal=is_minimal,
            has_patch=has_failure,
        )

        return DebuggingObstructionLocalizationWitness(
            witness_id=uuid.uuid4().hex,
            coordinate=self.coordinate,
            obstruction_class=obstruction_class,
            failure_predicate=f"predicate_failure@{minimal_patch}" if has_failure else "none",
            minimal_failing_patch=minimal_patch,
            depth_in_cover=depth,
            localization_path=path,
            confidence=confidence,
            strategy_used=self.strategy,
            provenance=(
                ("analyzer_id", self.analyzer_id),
                ("cover_key", _cover_patches_to_key(patches_t)),
                ("failure_rate", str(round(failure_rate, 4))),
            ),
            timestamp=_iso_timestamp(),
        )

    def _binary_search_localize(
        self,
        patches: tuple[str, ...],
        predicate_results: tuple[bool, ...],
    ) -> tuple[str, tuple[str, ...]]:
        """Core binary-search localization.

        Implements the bisection algorithm described in theory2.tex §11.2.2.
        The predicate is assumed to be monotone (if p_i fails, all p_j with
        j ≥ i in the minimal failing sub-range also fail).

        Parameters
        ----------
        patches : tuple[str, ...]
            Ordered patch identifiers.
        predicate_results : tuple[bool, ...]
            Predicate values for each patch.

        Returns
        -------
        tuple[str, tuple[str, ...]]
            ``(minimal_failing_patch, localization_path)`` where
            *localization_path* records every patch examined.
        """
        return _bisect_patches(patches, predicate_results)

    def _linear_scan_localize(
        self,
        patches: tuple[str, ...],
        predicate_results: tuple[bool, ...],
    ) -> tuple[str, tuple[str, ...]]:
        """Linear-scan localization: return the first failing patch.

        Scans patches left to right and returns the first index where the
        predicate is False.  Works correctly for non-monotone predicates.

        Parameters
        ----------
        patches : tuple[str, ...]
            Ordered patch identifiers.
        predicate_results : tuple[bool, ...]
            Predicate values for each patch.

        Returns
        -------
        tuple[str, tuple[str, ...]]
            ``(minimal_failing_patch, localization_path)``.  The path is the
            prefix of *patches* up to and including the first failure.
        """
        path: list[str] = []
        for patch, result in zip(patches, predicate_results):
            path.append(patch)
            if not result:
                return (patch, tuple(path))
        return ("", tuple(path))

    def _delta_debug_localize(
        self,
        patches: tuple[str, ...],
        predicate_results: tuple[bool, ...],
    ) -> tuple[str, tuple[str, ...]]:
        """Delta-debug style subset-reduction localization.

        Implements a simplified version of the Zeller-Hildebrandt (2002)
        delta-debugging algorithm adapted to ordered predicate sequences:

        1. Start with the full failing set F = {i : r_i = False}.
        2. Repeatedly try the first half of F; if it contains a failure,
           restrict to that half.
        3. Repeat until |F| = 1.

        This is more expensive than binary search (O(n log n) in the worst
        case) but finds a truly *1-minimal* failing patch even when multiple
        isolated failures exist.

        Parameters
        ----------
        patches : tuple[str, ...]
            Ordered patch identifiers.
        predicate_results : tuple[bool, ...]
            Predicate values for each patch.

        Returns
        -------
        tuple[str, tuple[str, ...]]
            ``(minimal_failing_patch, localization_path)``.
        """
        # Collect indices of all failing patches
        failing_indices = [i for i, r in enumerate(predicate_results) if not r]
        if not failing_indices:
            return ("", tuple(patches))

        path: list[str] = []
        candidate_indices = failing_indices
        iterations = 0
        max_iter = min(self.max_depth, len(patches) + 1)

        while len(candidate_indices) > 1 and iterations < max_iter:
            iterations += 1
            mid = len(candidate_indices) // 2
            lower_half = candidate_indices[:mid]
            upper_half = candidate_indices[mid:]

            # Record the midpoint patch examined
            path.append(patches[candidate_indices[mid - 1]])

            # Choose the half that contains a failure (prefer lower)
            if lower_half:
                candidate_indices = lower_half
            else:
                candidate_indices = upper_half

        # The single remaining candidate is the minimal failing patch
        best_idx = candidate_indices[0]
        path.append(patches[best_idx])
        return (patches[best_idx], tuple(path))

    # -- §5.2  Obstruction classification --

    def classify_obstruction(
        self, witness: DebuggingObstructionLocalizationWitness
    ) -> str:
        """Return the obstruction class string for a given witness.

        Parameters
        ----------
        witness : DebuggingObstructionLocalizationWitness
            A witness produced by :meth:`localize`.

        Returns
        -------
        str
            One of ``"H0_TRIVIAL"``, ``"H0_LOCAL"``, ``"H1_COHERENCE"``,
            ``"H1_DESCENT"``, ``"H2_HIGHER"``.

        Notes
        -----
        Delegates to :func:`_obstruction_class_from_depth` using the depth
        recorded in the witness and a synthetic failure rate of 1.0 (since the
        presence of a non-empty ``minimal_failing_patch`` implies at least one
        failure).
        """
        if not witness.minimal_failing_patch:
            return "H0_TRIVIAL"
        failure_rate = 1.0 if witness.minimal_failing_patch else 0.0
        return _obstruction_class_from_depth(witness.depth_in_cover, failure_rate)

    def classify_obstruction_from_rate(self, failure_rate: float, depth: int) -> str:
        """Classify an obstruction given a failure rate and depth.

        Parameters
        ----------
        failure_rate : float
            Fraction of patches that failed, in [0, 1].
        depth : int
            Depth of the coordinate in the judgment tree.

        Returns
        -------
        str
            Cohomology class label.
        """
        return _obstruction_class_from_depth(depth, failure_rate)

    def compute_obstruction_depth(
        self, coordinate: str, root_coordinate: str
    ) -> int:
        """Compute the depth of *coordinate* in the coordinate tree.

        Depth is defined as the number of dot-separated components in
        *coordinate* minus the number of shared prefix components with
        *root_coordinate*.

        Parameters
        ----------
        coordinate : str
            Target coordinate, e.g. ``"root.child.leaf"``.
        root_coordinate : str
            Root of the subtree, e.g. ``"root"``.

        Returns
        -------
        int
            Non-negative depth integer.  Returns 0 for root or empty input.

        Examples
        --------
        >>> analyzer.compute_obstruction_depth("root.child.leaf", "root")
        2
        >>> analyzer.compute_obstruction_depth("root", "root")
        0
        """
        if not coordinate:
            return 0
        coord_parts = coordinate.split(".")
        if not root_coordinate:
            return max(0, len(coord_parts) - 1)
        root_parts = root_coordinate.split(".")
        # Count shared prefix
        shared = 0
        for a, b in zip(coord_parts, root_parts):
            if a == b:
                shared += 1
            else:
                break
        return max(0, len(coord_parts) - shared)

    def build_obstruction_locality(
        self,
        witness: DebuggingObstructionLocalizationWitness,
    ) -> ObstructionLocality:
        """Build an :class:`ObstructionLocality` from a localization witness.

        Parameters
        ----------
        witness : DebuggingObstructionLocalizationWitness
            Source witness.

        Returns
        -------
        ObstructionLocality
            Populated locality record derived from the witness fields.

        Notes
        -----
        The *parent_coordinate* is computed by dropping the last dot-separated
        component from ``witness.coordinate``.  If the coordinate has no dots,
        the parent is the empty string (root level).
        """
        coord = witness.coordinate
        if "." in coord:
            parent = coord.rsplit(".", 1)[0]
        else:
            parent = ""
        is_minimal = bool(witness.minimal_failing_patch) and witness.confidence > 0.7
        return ObstructionLocality(
            coordinate=coord,
            depth=witness.depth_in_cover,
            parent_coordinate=parent,
            patch_id=witness.minimal_failing_patch,
            failure_predicate=witness.failure_predicate,
            is_minimal=is_minimal,
            localization_confidence=witness.confidence,
        )

    # -- §5.3  Cover analysis --

    def analyze_cover_consistency(
        self,
        patches: Sequence[str],
        predicate_results: Sequence[bool],
    ) -> dict[str, Any]:
        """Analyze the consistency of a cover with respect to the predicate.

        Returns a comprehensive summary of the cover's failure profile,
        suitable for use in debug reports and session integration.

        Parameters
        ----------
        patches : Sequence[str]
            Patch identifiers.
        predicate_results : Sequence[bool]
            Predicate results for each patch.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:

            * ``total_patches`` (int) — number of patches.
            * ``failing_count`` (int) — number of failing patches.
            * ``passing_count`` (int) — number of passing patches.
            * ``failure_rate`` (float) — fraction in [0, 1].
            * ``consistent`` (bool) — True if all patches pass.
            * ``first_failure`` (str) — patch ID of first failure, or ``""``.
            * ``last_failure`` (str) — patch ID of last failure, or ``""``.
            * ``cover_key`` (str) — stable key for this cover.
            * ``estimated_obstruction_class`` (str) — cohomology label.

        Raises
        ------
        ValueError
            If *patches* and *predicate_results* have different lengths.
        """
        patches_l = list(patches)
        results_l = list(predicate_results)
        if len(patches_l) != len(results_l):
            raise ValueError(
                f"patches ({len(patches_l)}) and predicate_results ({len(results_l)}) "
                "must have the same length."
            )
        total = len(patches_l)
        failing = [p for p, r in zip(patches_l, results_l) if not r]
        passing = [p for p, r in zip(patches_l, results_l) if r]
        failure_rate = len(failing) / total if total > 0 else 0.0
        first_failure = failing[0] if failing else ""
        last_failure = failing[-1] if failing else ""
        depth = self.compute_obstruction_depth(self.coordinate, "")
        estimated_class = self.estimate_obstruction_class(failure_rate, depth)
        return {
            "total_patches": total,
            "failing_count": len(failing),
            "passing_count": len(passing),
            "failure_rate": round(failure_rate, 6),
            "consistent": len(failing) == 0,
            "first_failure": first_failure,
            "last_failure": last_failure,
            "cover_key": _cover_patches_to_key(patches_l),
            "estimated_obstruction_class": estimated_class,
        }

    def estimate_obstruction_class(
        self, failure_rate: float, depth: int
    ) -> str:
        """Estimate the cohomology obstruction class from failure rate and depth.

        Parameters
        ----------
        failure_rate : float
            Fraction of patches that fail in [0, 1].
        depth : int
            Depth of the relevant coordinate.

        Returns
        -------
        str
            Cohomology class label string.
        """
        return _obstruction_class_from_depth(depth, failure_rate)

    # -- §5.4  Session integration --

    def integrate_with_session(
        self,
        witness: DebuggingObstructionLocalizationWitness,
        session_id: str,
    ) -> dict[str, Any]:
        """Produce a session-integration record for a witness.

        Creates a structured record that can be merged into a
        :class:`~jugeo.problem_modes.repair_semantics.models.DebugSession`
        or stored in a debug log.

        Parameters
        ----------
        witness : DebuggingObstructionLocalizationWitness
            The localization witness to integrate.
        session_id : str
            Identifier of the target debug session.

        Returns
        -------
        dict[str, Any]
            Integration record with keys: ``session_id``,
            ``witness_id``, ``coordinate``, ``obstruction_class``,
            ``minimal_failing_patch``, ``confidence``, ``certified``,
            ``analyzer_id``, ``strategy``, ``timestamp``.
        """
        return {
            "session_id": session_id,
            "witness_id": witness.witness_id,
            "coordinate": witness.coordinate,
            "obstruction_class": witness.obstruction_class,
            "minimal_failing_patch": witness.minimal_failing_patch,
            "confidence": witness.confidence,
            "certified": witness.is_certified(),
            "analyzer_id": self.analyzer_id,
            "strategy": self.strategy,
            "timestamp": witness.timestamp,
            "localization_path_length": len(witness.localization_path),
            "depth_in_cover": witness.depth_in_cover,
        }

    def batch_localize(
        self,
        patch_sets: Sequence[tuple[Sequence[str], Sequence[bool]]],
    ) -> tuple[DebuggingObstructionLocalizationWitness, ...]:
        """Run localization on multiple (patches, predicate_results) pairs.

        Applies :meth:`localize` to each element of *patch_sets* and returns
        all resulting witnesses as a tuple.

        Parameters
        ----------
        patch_sets : Sequence[tuple[Sequence[str], Sequence[bool]]]
            Each element is a ``(patches, predicate_results)`` pair.

        Returns
        -------
        tuple[DebuggingObstructionLocalizationWitness, ...]
            One witness per input pair, in the same order.

        Notes
        -----
        Errors in individual localization runs are caught and produce a
        zero-confidence witness with ``obstruction_class = "H0_TRIVIAL"``,
        unless ``self.strict_mode`` is True.
        """
        results: list[DebuggingObstructionLocalizationWitness] = []
        for patches, predicate_results in patch_sets:
            try:
                w = self.localize(patches, predicate_results)
            except Exception as exc:
                if self.strict_mode:
                    raise
                w = DebuggingObstructionLocalizationWitness(
                    coordinate=self.coordinate,
                    obstruction_class="H0_TRIVIAL",
                    failure_predicate=f"batch_error:{type(exc).__name__}",
                    minimal_failing_patch="",
                    confidence=0.0,
                    strategy_used=self.strategy,
                    provenance=(("analyzer_id", self.analyzer_id), ("error", str(exc)[:80])),
                    timestamp=_iso_timestamp(),
                )
            results.append(w)
        return tuple(results)

    # -- §5.5  Confidence and scoring --

    def score_localization_confidence(
        self,
        witness: DebuggingObstructionLocalizationWitness,
    ) -> float:
        """Compute the confidence score for a localization witness.

        Applies :func:`_compute_confidence` using the witness's path length,
        minimality, and patch presence, then additionally penalises witnesses
        whose ``obstruction_class`` is ``"H0_TRIVIAL"`` (no real obstruction).

        Parameters
        ----------
        witness : DebuggingObstructionLocalizationWitness
            Witness to score.

        Returns
        -------
        float
            Confidence score in [0.0, 1.0].
        """
        base = _compute_confidence(
            path_length=len(witness.localization_path),
            is_minimal=bool(witness.minimal_failing_patch),
            has_patch=bool(witness.minimal_failing_patch),
        )
        if witness.obstruction_class == "H0_TRIVIAL":
            base = min(base, 0.1)
        return base

    def is_confident_witness(
        self,
        witness: DebuggingObstructionLocalizationWitness,
    ) -> bool:
        """Return True when a witness meets the analyzer's confidence threshold.

        Parameters
        ----------
        witness : DebuggingObstructionLocalizationWitness
            Witness to evaluate.

        Returns
        -------
        bool
            ``True`` iff ``witness.confidence > self.confidence_threshold``.
        """
        return witness.confidence > self.confidence_threshold

    # -- §5.6  Serialization --

    def to_dict(self) -> dict[str, Any]:
        """Serialise this analyzer to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            All configuration fields as primitive Python types.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "coordinate": self.coordinate,
            "strategy": self.strategy,
            "max_depth": self.max_depth,
            "confidence_threshold": self.confidence_threshold,
            "enable_caching": self.enable_caching,
            "strict_mode": self.strict_mode,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DebuggingObstructionLocalizationAnalyzer":
        """Deserialise an analyzer from a dict produced by :meth:`to_dict`.

        Parameters
        ----------
        d : dict[str, Any]
            Source dictionary.

        Returns
        -------
        DebuggingObstructionLocalizationAnalyzer
            Reconstructed analyzer instance.
        """
        return cls(
            analyzer_id=d.get("analyzer_id", uuid.uuid4().hex),
            coordinate=d.get("coordinate", ""),
            strategy=d.get("strategy", LocalizationStrategy.BINARY_SEARCH.value),
            max_depth=int(d.get("max_depth", 32)),
            confidence_threshold=float(d.get("confidence_threshold", 0.85)),
            enable_caching=bool(d.get("enable_caching", True)),
            strict_mode=bool(d.get("strict_mode", False)),
        )


# ---------------------------------------------------------------------------
# §6  DebuggingObstructionLocalizationCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DebuggingObstructionLocalizationCoordinator:
    """Coordinates multiple :class:`DebuggingObstructionLocalizationAnalyzer` instances.

    The coordinator runs all registered analyzers over the same cover and
    merges their results into a single best-confidence witness.  It acts as
    the outermost orchestration layer in the debugging pipeline.

    Theory connection (theory2.tex §11.2.5)
    ----------------------------------------
    In the sheaf framework multiple analyzers correspond to probing the same
    cover from different angles (different strategies, different coordinate
    roots).  The merged witness corresponds to the *meet* of the localization
    results in the confidence lattice: the highest-confidence certifiable
    localization wins.

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator.
    analyzers : tuple[DebuggingObstructionLocalizationAnalyzer, ...]
        Registered analyzers.  Empty by default.
    root_coordinate : str
        Root coordinate for this coordination session.
    session_id : str
        Identifier of the associated debug session.
    max_iterations : int
        Maximum number of localization iterations across all analyzers.
    enable_parallel_analysis : bool
        Reserved for future parallel execution support.
    """

    coordinator_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    analyzers: tuple[DebuggingObstructionLocalizationAnalyzer, ...] = field(
        default_factory=tuple
    )
    root_coordinate: str = ""
    session_id: str = ""
    max_iterations: int = 64
    enable_parallel_analysis: bool = False

    # -- §6.1  Analyzer management --

    def add_analyzer(
        self,
        analyzer: DebuggingObstructionLocalizationAnalyzer,
    ) -> "DebuggingObstructionLocalizationCoordinator":
        """Return a new coordinator with *analyzer* appended.

        Parameters
        ----------
        analyzer : DebuggingObstructionLocalizationAnalyzer
            Analyzer to add.

        Returns
        -------
        DebuggingObstructionLocalizationCoordinator
            New coordinator with the analyzer included in :attr:`analyzers`.

        Notes
        -----
        Because the coordinator is frozen, this method returns a *new*
        instance via :func:`dataclasses.replace`.
        """
        return replace(self, analyzers=self.analyzers + (analyzer,))

    # -- §6.2  Localization execution --

    def run_localization(
        self,
        patches: Sequence[str],
        predicate_results: Sequence[bool],
    ) -> tuple[DebuggingObstructionLocalizationWitness, ...]:
        """Run all registered analyzers over the given cover.

        Each analyzer in :attr:`analyzers` is invoked with the same *patches*
        and *predicate_results*.  If no analyzers are registered, a single
        default :class:`DebuggingObstructionLocalizationAnalyzer` is created
        on the fly using the coordinator's ``root_coordinate``.

        Parameters
        ----------
        patches : Sequence[str]
            Ordered patch identifiers for the cover.
        predicate_results : Sequence[bool]
            Predicate evaluation results for each patch.

        Returns
        -------
        tuple[DebuggingObstructionLocalizationWitness, ...]
            One witness per analyzer, in registration order.

        Notes
        -----
        Individual analyzer failures are swallowed and produce a zero-confidence
        witness so that a partial result is always returned.
        """
        analyzers = self.analyzers
        if not analyzers:
            default_analyzer = DebuggingObstructionLocalizationAnalyzer(
                coordinate=self.root_coordinate,
                strategy=LocalizationStrategy.BINARY_SEARCH.value,
            )
            analyzers = (default_analyzer,)

        witnesses: list[DebuggingObstructionLocalizationWitness] = []
        for analyzer in analyzers:
            try:
                w = analyzer.localize(patches, predicate_results)
            except Exception as exc:
                w = DebuggingObstructionLocalizationWitness(
                    coordinate=analyzer.coordinate,
                    obstruction_class="H0_TRIVIAL",
                    failure_predicate=f"coordinator_error:{type(exc).__name__}",
                    minimal_failing_patch="",
                    confidence=0.0,
                    strategy_used=analyzer.strategy,
                    provenance=(
                        ("coordinator_id", self.coordinator_id),
                        ("error", str(exc)[:80]),
                    ),
                    timestamp=_iso_timestamp(),
                )
            witnesses.append(w)
        return tuple(witnesses)

    # -- §6.3  Witness merging --

    def merge_witnesses(
        self,
        witnesses: Sequence[DebuggingObstructionLocalizationWitness],
    ) -> DebuggingObstructionLocalizationWitness:
        """Select the highest-confidence witness from a collection.

        Implements a simple max-confidence merge: the witness with the
        greatest :attr:`~DebuggingObstructionLocalizationWitness.confidence`
        is returned.  Ties are broken by preferring certified witnesses, then
        by the earliest position in *witnesses*.

        Parameters
        ----------
        witnesses : Sequence[DebuggingObstructionLocalizationWitness]
            Collection of witnesses to merge.

        Returns
        -------
        DebuggingObstructionLocalizationWitness
            The highest-confidence (and preferably certified) witness.

        Raises
        ------
        ValueError
            If *witnesses* is empty.

        Notes
        -----
        The returned witness is one of the *input* witnesses, not a newly
        constructed object.  If callers need a synthetic merged witness they
        should construct one from the returned record.
        """
        witnesses_l = list(witnesses)
        if not witnesses_l:
            raise ValueError("Cannot merge an empty witness collection.")

        def sort_key(w: DebuggingObstructionLocalizationWitness) -> tuple[float, int]:
            # Higher confidence first; certified breaks ties
            return (w.confidence, 1 if w.is_certified() else 0)

        return max(witnesses_l, key=sort_key)

    # -- §6.4  Reporting --

    def build_localization_report(
        self,
        witnesses: Sequence[DebuggingObstructionLocalizationWitness],
    ) -> dict[str, Any]:
        """Produce a comprehensive localization report from a witness set.

        Aggregates statistics across all witnesses and selects the best result
        for the top-level summary.

        Parameters
        ----------
        witnesses : Sequence[DebuggingObstructionLocalizationWitness]
            Witnesses to report on (typically the output of
            :meth:`run_localization`).

        Returns
        -------
        dict[str, Any]
            Report dictionary with keys:

            * ``coordinator_id`` (str)
            * ``session_id`` (str)
            * ``root_coordinate`` (str)
            * ``total_witnesses`` (int)
            * ``certified_count`` (int)
            * ``best_witness_id`` (str) — ID of highest-confidence witness.
            * ``best_patch`` (str) — minimal failing patch from best witness.
            * ``best_confidence`` (float)
            * ``best_obstruction_class`` (str)
            * ``localization_complete`` (bool)
            * ``strategy_summary`` (dict[str, int]) — strategy → count.
            * ``timestamp`` (str)
        """
        witnesses_l = list(witnesses)
        total = len(witnesses_l)
        certified = [w for w in witnesses_l if w.is_certified()]
        strategy_summary: dict[str, int] = {}
        for w in witnesses_l:
            strategy_summary[w.strategy_used] = strategy_summary.get(w.strategy_used, 0) + 1

        if witnesses_l:
            best = self.merge_witnesses(witnesses_l)
            best_id = best.witness_id
            best_patch = best.minimal_failing_patch
            best_conf = best.confidence
            best_class = best.obstruction_class
        else:
            best_id = ""
            best_patch = ""
            best_conf = 0.0
            best_class = "H0_TRIVIAL"

        return {
            "coordinator_id": self.coordinator_id,
            "session_id": self.session_id,
            "root_coordinate": self.root_coordinate,
            "total_witnesses": total,
            "certified_count": len(certified),
            "best_witness_id": best_id,
            "best_patch": best_patch,
            "best_confidence": round(best_conf, 6),
            "best_obstruction_class": best_class,
            "localization_complete": self.is_localization_complete(witnesses_l),
            "strategy_summary": strategy_summary,
            "timestamp": _iso_timestamp(),
        }

    def is_localization_complete(
        self,
        witnesses: Sequence[DebuggingObstructionLocalizationWitness],
    ) -> bool:
        """Return True when at least one witness is certified.

        Localization is considered *complete* when the cover search has
        converged to a certified minimal failing patch.

        Parameters
        ----------
        witnesses : Sequence[DebuggingObstructionLocalizationWitness]
            Witnesses to check.

        Returns
        -------
        bool
            ``True`` iff any witness satisfies
            :meth:`~DebuggingObstructionLocalizationWitness.is_certified`.
        """
        return any(w.is_certified() for w in witnesses)

    # -- §6.5  Serialization --

    def to_dict(self) -> dict[str, Any]:
        """Serialise this coordinator to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            All configuration and analyzer fields as primitive Python types.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "root_coordinate": self.root_coordinate,
            "session_id": self.session_id,
            "max_iterations": self.max_iterations,
            "enable_parallel_analysis": self.enable_parallel_analysis,
            "analyzers": [a.to_dict() for a in self.analyzers],
        }




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# §7  Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "LocalizationStrategy",
    # Dataclasses
    "ObstructionLocality",
    "DebuggingObstructionLocalizationWitness",
    "DebuggingObstructionLocalizationAnalyzer",
    "DebuggingObstructionLocalizationCoordinator",
    # Helper functions
    "_iso_timestamp",
    "_stable_hash8",
    "_cover_patches_to_key",
    "_bisect_patches",
    "_compute_confidence",
    "_obstruction_class_from_depth",
    # Module metadata
    "MANIFEST_SPEC_PROVENANCE",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of s01 debugging as obstruction localization

# ---------------------------------------------------------------------------
# §8  Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # smoke test: localize an obstruction in a simple patch sequence
    analyzer = DebuggingObstructionLocalizationAnalyzer(
        coordinate="root.child.grandchild",
        strategy="BINARY_SEARCH",
        max_depth=16,
    )
    patches = ["p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7"]
    results = [True, True, True, False, False, False, False, False]
    witness = analyzer.localize(patches, results)
    print(f"Localized obstruction at: {witness.minimal_failing_patch}")
    print(f"Confidence: {witness.confidence:.3f}")
    print(f"Certified: {witness.is_certified()}")

    # Verify binary search correctness: first False is at index 3 → "p3"
    assert witness.minimal_failing_patch == "p3", (
        f"Expected 'p3', got '{witness.minimal_failing_patch}'"
    )
    assert witness.is_certified(), "Witness should be certified"

    # Linear scan
    analyzer_ls = DebuggingObstructionLocalizationAnalyzer(
        coordinate="root.child.grandchild",
        strategy=LocalizationStrategy.LINEAR_SCAN.value,
    )
    witness_ls = analyzer_ls.localize(patches, results)
    assert witness_ls.minimal_failing_patch == "p3", (
        f"Linear scan: expected 'p3', got '{witness_ls.minimal_failing_patch}'"
    )

    # Delta-debug
    analyzer_dd = DebuggingObstructionLocalizationAnalyzer(
        coordinate="root.child",
        strategy=LocalizationStrategy.DELTA_DEBUG.value,
    )
    witness_dd = analyzer_dd.localize(patches, results)
    assert witness_dd.minimal_failing_patch in ("p3", "p4", "p5", "p6", "p7"), (
        f"Delta-debug: unexpected result '{witness_dd.minimal_failing_patch}'"
    )

    # Cover consistency analysis
    consistency = analyzer.analyze_cover_consistency(patches, results)
    assert consistency["failing_count"] == 5
    assert consistency["passing_count"] == 3
    assert not consistency["consistent"]

    # Obstruction locality
    locality = analyzer.build_obstruction_locality(witness)
    assert locality.coordinate == "root.child.grandchild"
    assert locality.depth == 2

    # Coordinator
    coordinator = DebuggingObstructionLocalizationCoordinator(
        root_coordinate="root",
        session_id="test-session",
    )
    coordinator = coordinator.add_analyzer(analyzer)
    coordinator = coordinator.add_analyzer(analyzer_ls)
    witnesses = coordinator.run_localization(patches, results)
    assert len(witnesses) == 2

    report = coordinator.build_localization_report(witnesses)
    print(f"Report: total_witnesses={report['total_witnesses']}")
    assert report["total_witnesses"] == 2
    assert report["localization_complete"]

    # Serialization round-trip
    w_dict = witness.to_dict()
    w_rt = DebuggingObstructionLocalizationWitness.from_dict(w_dict)
    assert w_rt.minimal_failing_patch == witness.minimal_failing_patch
    assert w_rt.obstruction_class == witness.obstruction_class

    a_dict = analyzer.to_dict()
    a_rt = DebuggingObstructionLocalizationAnalyzer.from_dict(a_dict)
    assert a_rt.coordinate == analyzer.coordinate
    assert a_rt.strategy == analyzer.strategy

    coord_dict = coordinator.to_dict()
    assert coord_dict["root_coordinate"] == "root"
    assert len(coord_dict["analyzers"]) == 2

    # Summary string
    summary = witness.summary()
    assert "H1" in summary or "H0" in summary or "H2" in summary

    print("s01 smoke test passed")
