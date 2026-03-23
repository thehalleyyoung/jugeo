"""Core algorithms for the Unified Problem Atlas — Theory2.tex Ch14 §14.5.

copilot: atlas algorithm implementations for lookup, matching, and routing.

This module provides the computational algorithms that power the problem atlas:

  atlas_lookup_algorithm         — Map a problem description to a ProblemClass
  signature_matching_algorithm   — Find compatible classes for a signature
  evidence_routing_algorithm     — Route evidence contributions to requirements
  class_lattice_traversal        — Traverse the problem class lattice
  optimal_evidence_strategy      — Compute the optimal channel acquisition order
  cross_class_unification        — Unify two problem classes into a common parent
  requirement_satisfaction_check — Check whether an evidence set satisfies requirements

These algorithms are the backbone of the atlas's classification and routing
machinery, referenced from the integration layer and the orchestration engine.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        ProblemClass,
        SemanticSignature,
        EvidenceRequirement,
        AtlasCatalog,
        ProblemCategory,
        DifficultyLevel,
        ConjunctionMode,
    )
except ImportError:
    ProblemClass = object  # type: ignore[assignment,misc]
    SemanticSignature = object  # type: ignore[assignment,misc]
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    AtlasCatalog = object  # type: ignore[assignment,misc]
    ProblemCategory = None  # type: ignore[assignment]
    DifficultyLevel = None  # type: ignore[assignment]
    ConjunctionMode = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.problem_atlas.evidence_channels import (
        ChannelContribution,
        ChannelRegistry,
        TrustLevelComputer,
        ChannelDescriptor,
    )
except ImportError:
    ChannelContribution = object  # type: ignore[assignment,misc]
    ChannelRegistry = object  # type: ignore[assignment,misc]
    TrustLevelComputer = object  # type: ignore[assignment,misc]
    ChannelDescriptor = object  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.problem_atlas.trust_requirements import (
        RequirementChecker,
        RequirementCheckResult,
        TrustGap,
        GapAnalyzer,
    )
except ImportError:
    RequirementChecker = object  # type: ignore[assignment,misc]
    RequirementCheckResult = object  # type: ignore[assignment,misc]
    TrustGap = object  # type: ignore[assignment,misc]
    GapAnalyzer = object  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ClassId: TypeAlias = str
ChannelId: TypeAlias = str
TrustScore: TypeAlias = float
TokenSet: TypeAlias = frozenset[str]

# ---------------------------------------------------------------------------
# §14.5.1  TraversalDirection
# ---------------------------------------------------------------------------


class TraversalDirection(str, Enum):
    """Direction to traverse the problem class lattice.

    UP means moving towards the more general superclasses; DOWN means moving
    towards more specific subclasses.  BOTH traverses in both directions from
    the start node.

    Used by :func:`class_lattice_traversal` and :func:`cross_class_unification`.
    """

    UP = "up"
    DOWN = "down"
    BOTH = "both"

    def includes_up(self) -> bool:
        """Return True if this direction includes upward (superclass) traversal.

        Returns:
            True for UP and BOTH, False for DOWN.
        """
        return self in (TraversalDirection.UP, TraversalDirection.BOTH)

    def includes_down(self) -> bool:
        """Return True if this direction includes downward (subclass) traversal.

        Returns:
            True for DOWN and BOTH, False for UP.
        """
        return self in (TraversalDirection.DOWN, TraversalDirection.BOTH)


# ---------------------------------------------------------------------------
# §14.5.2  LookupStrategy
# ---------------------------------------------------------------------------


class LookupStrategy(str, Enum):
    """Strategy controlling how atlas_lookup_algorithm matches a description.

    EXACT requires the problem description to precisely match a class name.
    FUZZY uses token-overlap scoring.
    SEMANTIC uses Jaccard similarity on token sets from description and class.
    CATEGORY_FIRST pre-filters by category before scoring.
    DIFFICULTY_FIRST pre-filters by inferred difficulty before scoring.
    """

    EXACT = "exact"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"
    CATEGORY_FIRST = "category_first"
    DIFFICULTY_FIRST = "difficulty_first"

    def requires_signature(self) -> bool:
        """Return True if this strategy requires a SemanticSignature to operate.

        The SEMANTIC strategy optionally uses signature token information when
        available.  All other strategies operate on plain text.

        Returns:
            True only for SEMANTIC strategy.
        """
        return self == LookupStrategy.SEMANTIC


# ---------------------------------------------------------------------------
# §14.5.3  LookupResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LookupResult:
    """Result returned by :func:`atlas_lookup_algorithm`.

    Captures the matched class, confidence level, ranked alternatives, and
    diagnostic information from a single lookup call.

    Attributes:
        matched_class: The class_id of the best matching ProblemClass, or None
            if no match exceeded the minimum scoring threshold.
        confidence: Normalised confidence score in [0.0, 1.0].  Scores above
            0.9 are considered definitive (see :meth:`is_definitive`).
        alternatives: Tuple of class_ids for runner-up matches in descending
            score order, capped at 5.
        lookup_strategy: The LookupStrategy used to produce this result.
        evidence_requirement_id: ID of the primary EvidenceRequirement attached
            to the matched class, or None.
        diagnostic_notes: Human-readable notes explaining the match decision.
    """

    matched_class: str | None
    confidence: float
    alternatives: tuple[str, ...]
    lookup_strategy: LookupStrategy
    evidence_requirement_id: str | None
    diagnostic_notes: str

    def is_definitive(self) -> bool:
        """Return True if the confidence score is above the definitive threshold.

        A definitive match (confidence > 0.9) means the algorithm is highly
        confident and downstream consumers may skip manual review.

        Returns:
            True when confidence > 0.9 and matched_class is not None.
        """
        return self.matched_class is not None and self.confidence > 0.9

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain Python dictionary.

        Returns:
            Dictionary with all fields serialized to JSON-compatible types.
        """
        return {
            "matched_class": self.matched_class,
            "confidence": self.confidence,
            "alternatives": list(self.alternatives),
            "lookup_strategy": self.lookup_strategy.value,
            "evidence_requirement_id": self.evidence_requirement_id,
            "diagnostic_notes": self.diagnostic_notes,
            "is_definitive": self.is_definitive(),
        }


# ---------------------------------------------------------------------------
# §14.5.4  RoutingResult
# ---------------------------------------------------------------------------


@dataclass
class RoutingResult:
    """Result returned by :func:`evidence_routing_algorithm`.

    Describes how a set of evidence contributions was distributed across the
    channels expected by a requirement.  Uses a regular (mutable) dataclass
    because dict fields are not hashable and mutation during routing is needed.

    Attributes:
        routed_contributions: Mapping from channel_id to the normalised trust
            score that was routed to that channel.
        requirement_id: The ID of the EvidenceRequirement this routing targets.
        coverage_score: Fraction of required channels that received evidence,
            in [0.0, 1.0].
        missing_channels: Channels listed in the requirement that received no
            evidence.
        surplus_channels: Evidence channels provided that are not listed in the
            requirement.
    """

    routed_contributions: dict[str, float] = field(default_factory=dict)
    requirement_id: str = ""
    coverage_score: float = 0.0
    missing_channels: tuple[str, ...] = ()
    surplus_channels: tuple[str, ...] = ()

    def is_fully_covered(self) -> bool:
        """Return True if every required channel received at least some evidence.

        A fully covered routing has coverage_score == 1.0 and no missing
        channels.

        Returns:
            True when coverage_score >= 1.0 and missing_channels is empty.
        """
        return self.coverage_score >= 1.0 and len(self.missing_channels) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain Python dictionary.

        Returns:
            Dictionary with all fields serialized to JSON-compatible types.
        """
        return {
            "routed_contributions": dict(self.routed_contributions),
            "requirement_id": self.requirement_id,
            "coverage_score": self.coverage_score,
            "missing_channels": list(self.missing_channels),
            "surplus_channels": list(self.surplus_channels),
            "is_fully_covered": self.is_fully_covered(),
        }


# ---------------------------------------------------------------------------
# §14.5.5  UnificationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnificationResult:
    """Result of :func:`cross_class_unification`.

    Describes whether two problem classes could be unified into a common
    superclass, and what that superclass is.

    Attributes:
        class_a_id: The ID of the first input class.
        class_b_id: The ID of the second input class.
        unified_class_id: The class_id of the least upper bound, or None if
            unification failed.
        unification_mode: One of ``"direct_ancestor"``, ``"shared_ancestor"``,
            ``"universal_top"``, or ``"failed"``.
        is_trivial: True when one class is an ancestor of the other (the more
            general class is trivially the LUB).
        notes: Human-readable explanation of the unification decision.
    """

    class_a_id: str
    class_b_id: str
    unified_class_id: str | None
    unification_mode: str
    is_trivial: bool
    notes: str

    def succeeded(self) -> bool:
        """Return True if the unification produced a valid unified class.

        Returns:
            True when unified_class_id is not None.
        """
        return self.unified_class_id is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a plain Python dictionary.

        Returns:
            Dictionary with all fields serialized to JSON-compatible types.
        """
        return {
            "class_a_id": self.class_a_id,
            "class_b_id": self.class_b_id,
            "unified_class_id": self.unified_class_id,
            "unification_mode": self.unification_mode,
            "is_trivial": self.is_trivial,
            "notes": self.notes,
            "succeeded": self.succeeded(),
        }


# ---------------------------------------------------------------------------
# §14.5.6  SatisfactionReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SatisfactionReport:
    """Report produced by :func:`requirement_satisfaction_check`.

    Summarises whether a given evidence set satisfies a single
    EvidenceRequirement, along with per-channel scores and gap information.

    Attributes:
        requirement_id: The ID of the EvidenceRequirement being checked.
        satisfied: True if the aggregate trust meets or exceeds the threshold.
        aggregate_trust: Weighted aggregate of all channel trust scores.
        channel_scores: Per-channel trust scores as a tuple of (channel_id, score)
            pairs.
        gaps: Channel IDs that fell below the required trust level.
        verdict: Short human-readable verdict string (e.g. ``"PASS"`` or
            ``"FAIL – 2 gaps"``).
    """

    requirement_id: str
    satisfied: bool
    aggregate_trust: float
    channel_scores: tuple[tuple[str, float], ...]
    gaps: tuple[str, ...]
    verdict: str

    def summary(self) -> str:
        """Return a one-line human-readable summary of this report.

        Returns:
            String combining requirement_id, verdict, and aggregate trust score.

        Examples:
            >>> report.summary()
            'req-abc123 → PASS (trust=0.92, 0 gaps)'
        """
        gap_count = len(self.gaps)
        trust_str = f"{self.aggregate_trust:.3f}"
        return (
            f"{self.requirement_id} → {self.verdict} "
            f"(trust={trust_str}, {gap_count} gap{'s' if gap_count != 1 else ''})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this report to a plain Python dictionary.

        Returns:
            Dictionary with all fields serialized to JSON-compatible types.
        """
        return {
            "requirement_id": self.requirement_id,
            "satisfied": self.satisfied,
            "aggregate_trust": self.aggregate_trust,
            "channel_scores": [
                {"channel_id": cid, "score": s} for cid, s in self.channel_scores
            ],
            "gaps": list(self.gaps),
            "verdict": self.verdict,
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# §14.5.7  Internal helper functions
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Tokenize a string into lowercase alphabetic/numeric words.

    Splits on any character that is not alphanumeric, strips empty tokens, and
    returns all remaining lowercase words.  Used throughout the lookup and
    matching algorithms to normalise free-form text before comparison.

    Args:
        text: The input string to tokenize.

    Returns:
        List of lowercase word tokens.  May be empty if text contains only
        punctuation or whitespace.

    Examples:
        >>> _tokenize("Graph coloring: find a valid 3-coloring.")
        ['graph', 'coloring', 'find', 'a', 'valid', '3', 'coloring']
    """
    import re

    raw = re.split(r"[^a-zA-Z0-9]+", text)
    return [tok.lower() for tok in raw if tok]


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard similarity coefficient between two token sets.

    Jaccard similarity is defined as the size of the intersection divided by
    the size of the union.  Returns 0.0 when both sets are empty.

    Args:
        a: First token set.
        b: Second token set.

    Returns:
        Float in [0.0, 1.0].  Returns 0.0 if both sets are empty.

    Examples:
        >>> _jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        0.5
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _score_class_match(description_tokens: list[str], pc: Any) -> float:
    """Score how well a problem class matches a tokenized description.

    Collects all text from the class's ``name``, ``description``, ``category``,
    and ``keywords`` attributes (using ``getattr`` for objects and ``dict.get``
    for dicts), tokenises them, then computes the fraction of description tokens
    that appear in the combined class token set.  The score is boosted by 0.1
    if the Jaccard similarity of the token sets is above 0.3.

    Args:
        description_tokens: Tokens extracted from the problem description.
        pc: A ProblemClass-like object or dict with optional string fields
            ``name``, ``description``, ``category``, and ``keywords``.

    Returns:
        Float score in [0.0, 1.0 + boost].  Returns 0.0 when there are no
        description tokens.
    """
    if not description_tokens:
        return 0.0

    def _pick(key: str) -> Any:
        if isinstance(pc, dict):
            return pc.get(key)
        return getattr(pc, key, None)

    class_text_parts: list[str] = []
    for attr in ("name", "description", "class_name", "class_description"):
        val = _pick(attr)
        if isinstance(val, str):
            class_text_parts.append(val)
    # Also support a keywords iterable
    kw = _pick("keywords")
    if isinstance(kw, (list, tuple, set, frozenset)):
        class_text_parts.extend(str(k) for k in kw)
    # Support category as string or enum
    cat = _pick("category")
    if cat is not None:
        class_text_parts.append(str(cat))

    class_tokens = set(_tokenize(" ".join(class_text_parts)))
    desc_set = set(description_tokens)

    if not class_tokens:
        return 0.0

    hits = sum(1 for t in description_tokens if t in class_tokens)
    base_score = hits / len(description_tokens)

    jaccard = _jaccard_similarity(desc_set, class_tokens)
    boost = 0.1 if jaccard > 0.3 else 0.0

    return min(1.0, base_score + boost)


def _bfs_lattice(
    start: str,
    get_neighbors: Callable[[str], list[str]],
    max_depth: int,
) -> list[str]:
    """Generic breadth-first search over a lattice.

    Starts from ``start`` and expands using ``get_neighbors``.  Visited nodes
    are tracked to prevent cycles.  The start node itself is not included in
    the output.

    Args:
        start: The node ID from which to begin traversal.
        get_neighbors: Callable mapping a node ID to its neighbor IDs.
        max_depth: Maximum number of hops from start.  Nodes beyond this depth
            are not expanded.

    Returns:
        List of visited node IDs (excluding ``start``) in BFS order.  Empty if
        ``start`` has no reachable neighbors within ``max_depth``.
    """
    visited: list[str] = []
    seen: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])

    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor in get_neighbors(node):
            if neighbor not in seen:
                seen.add(neighbor)
                visited.append(neighbor)
                queue.append((neighbor, depth + 1))

    return visited


def _get_class_id(pc: Any) -> str:
    """Extract the class identifier from a ProblemClass-like object or dict.

    Tries the attributes ``class_id``, ``id``, and ``name`` in order.  Falls
    back to the string representation if none are present.

    Args:
        pc: A ProblemClass-like object or dict.

    Returns:
        String identifier for the class.
    """
    if isinstance(pc, dict):
        for k in ("class_id", "id", "name", "class_name"):
            v = pc.get(k)
            if isinstance(v, str) and v:
                return v
        return str(id(pc))
    for attr in ("class_id", "id", "name", "class_name"):
        val = getattr(pc, attr, None)
        if isinstance(val, str) and val:
            return val
    return str(pc)


def _get_catalog_classes(catalog: Any) -> list[Any]:
    """Extract the list of ProblemClass objects from a catalog.

    Tries ``catalog.classes``, ``catalog.all_classes()``, and iteration over
    the catalog itself.  Returns an empty list if nothing works.

    Args:
        catalog: An AtlasCatalog-like object.

    Returns:
        List of ProblemClass-like objects.
    """
    # Try attribute first
    for attr in ("classes", "_classes", "problem_classes"):
        val = getattr(catalog, attr, None)
        if isinstance(val, (list, tuple)):
            return list(val)
        if isinstance(val, dict):
            return list(val.values())

    # Try callable methods
    for method in ("all_classes", "list_classes", "get_classes"):
        fn = getattr(catalog, method, None)
        if callable(fn):
            try:
                result = fn()
                if isinstance(result, (list, tuple)):
                    return list(result)
            except Exception:
                pass

    # Try iterating
    try:
        items = list(catalog)
        if items and not isinstance(items[0], str):
            return items
    except TypeError:
        pass

    return []


def _get_requirement_channels(req: Any) -> list[str]:
    """Extract the list of required channel IDs from an EvidenceRequirement or dict.

    Checks several common attribute and method patterns used across different
    versions of the requirement model.

    Args:
        req: An EvidenceRequirement-like object or dict.

    Returns:
        List of channel_id strings required by the requirement.
    """
    if isinstance(req, dict):
        val = (
            req.get("required_channels")
            or req.get("channels")
            or req.get("channel_ids", [])
        )
        if isinstance(val, (list, tuple)):
            return [str(c) for c in val]
        return []
    for attr in ("required_channels", "channels", "channel_ids"):
        val = getattr(req, attr, None)
        if isinstance(val, (list, tuple)):
            return [str(c) for c in val]
        if isinstance(val, dict):
            return list(val.keys())

    get_fn = getattr(req, "get_channels", None)
    if callable(get_fn):
        try:
            result = get_fn()
            if isinstance(result, (list, tuple)):
                return [str(c) for c in result]
        except Exception:
            pass

    return []


def _get_requirement_threshold(req: Any) -> float:
    """Extract the minimum trust threshold from an EvidenceRequirement or dict.

    Falls back to 0.7 if no threshold attribute is found.

    Args:
        req: An EvidenceRequirement-like object or dict.

    Returns:
        Minimum trust threshold as a float in [0.0, 1.0].
    """
    if isinstance(req, dict):
        val = req.get("threshold") or req.get("min_trust") or req.get("trust_threshold")
        if isinstance(val, (int, float)):
            return float(val)
        return 0.7
    for attr in ("threshold", "min_trust", "trust_threshold", "minimum_trust"):
        val = getattr(req, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.7


def _get_requirement_id(req: Any) -> str:
    """Extract the unique ID from an EvidenceRequirement or dict.

    Args:
        req: An EvidenceRequirement-like object or dict.

    Returns:
        String ID for the requirement.
    """
    if isinstance(req, dict):
        for k in ("requirement_id", "id", "req_id"):
            v = req.get(k)
            if isinstance(v, str) and v:
                return v
        return str(id(req))
    for attr in ("requirement_id", "id", "req_id"):
        val = getattr(req, attr, None)
        if isinstance(val, str) and val:
            return val
    return str(id(req))


def _get_parent_ids(pc: Any) -> list[str]:
    """Extract the parent class IDs from a ProblemClass or dict.

    Args:
        pc: A ProblemClass-like object or dict.

    Returns:
        List of parent class_id strings.
    """
    if isinstance(pc, dict):
        val = pc.get("parent_ids") or pc.get("parents") or pc.get("superclass_ids", [])
        if isinstance(val, (list, tuple)):
            return [str(p) for p in val]
        single = pc.get("parent_id") or pc.get("parent")
        if isinstance(single, str) and single:
            return [single]
        return []
    for attr in ("parent_ids", "parents", "superclass_ids", "parent_class_ids"):
        val = getattr(pc, attr, None)
        if isinstance(val, (list, tuple)):
            return [str(p) for p in val]
    single = getattr(pc, "parent_id", None) or getattr(pc, "parent", None)
    if isinstance(single, str) and single:
        return [single]
    return []


def _get_child_ids(pc: Any) -> list[str]:
    """Extract the child class IDs from a ProblemClass or dict.

    Args:
        pc: A ProblemClass-like object or dict.

    Returns:
        List of child class_id strings.
    """
    if isinstance(pc, dict):
        val = pc.get("child_ids") or pc.get("children") or pc.get("subclass_ids", [])
        if isinstance(val, (list, tuple)):
            return [str(c) for c in val]
        return []
    for attr in ("child_ids", "children", "subclass_ids", "child_class_ids"):
        val = getattr(pc, attr, None)
        if isinstance(val, (list, tuple)):
            return [str(c) for c in val]
    return []


def _find_class_by_id(class_id: str, catalog: Any) -> Any | None:
    """Look up a ProblemClass by its class_id in the catalog.

    Tries ``catalog.get(class_id)``, ``catalog.lookup(class_id)``, and a
    linear scan of all classes.

    Args:
        class_id: The class_id to search for.
        catalog: An AtlasCatalog-like object.

    Returns:
        The matching ProblemClass, or None if not found.
    """
    for method in ("get", "lookup", "lookup_by_id", "find"):
        fn = getattr(catalog, method, None)
        if callable(fn):
            try:
                result = fn(class_id)
                if result is not None:
                    return result
            except Exception:
                pass

    for pc in _get_catalog_classes(catalog):
        if _get_class_id(pc) == class_id:
            return pc

    return None


# ---------------------------------------------------------------------------
# §14.5.8  atlas_lookup_algorithm
# ---------------------------------------------------------------------------


def atlas_lookup_algorithm(
    problem_description: str,
    catalog: Any,
    *,
    strategy: LookupStrategy = LookupStrategy.FUZZY,
    category_hint: str | None = None,
) -> LookupResult:
    """Map a natural language problem description to a ProblemClass in the catalog.

    Uses keyword matching against class names and descriptions.  The FUZZY
    strategy scores each class by counting keyword overlaps between the
    description and the class name/description fields.  The EXACT strategy
    requires an exact name match.  CATEGORY_FIRST filters by category before
    scoring.  SEMANTIC uses Jaccard similarity on the full token sets.
    DIFFICULTY_FIRST attempts to infer difficulty from description keywords and
    pre-filters the catalog accordingly.

    The algorithm never raises on empty results — it always returns a
    LookupResult, possibly with ``matched_class=None`` and ``confidence=0.0``.

    Args:
        problem_description: Natural language description of the problem.
        catalog: The AtlasCatalog to search.  Must be non-empty.
        strategy: Lookup strategy controlling matching behavior.
        category_hint: Optional category name (string) to restrict the search.
            When provided with CATEGORY_FIRST strategy, only classes whose
            ``category`` attribute equals this value are scored.

    Returns:
        LookupResult with matched class, confidence score, and alternatives.

    Raises:
        ValueError: If the catalog is empty (no classes to search).

    Examples:
        >>> result = atlas_lookup_algorithm("find minimum spanning tree", catalog)
        >>> result.matched_class
        'COMPUTATIONAL_OPTIMIZATION'
        >>> result.confidence
        0.72
    """
    all_classes = _get_catalog_classes(catalog)
    if not all_classes:
        raise ValueError(
            "atlas_lookup_algorithm: catalog is empty — no classes to search."
        )

    desc_tokens = _tokenize(problem_description)
    desc_set = set(desc_tokens)

    # --- Strategy: EXACT ---
    if strategy == LookupStrategy.EXACT:
        desc_lower = problem_description.strip().lower()
        for pc in all_classes:
            name = getattr(pc, "name", None) or getattr(pc, "class_name", "")
            if isinstance(name, str) and name.lower() == desc_lower:
                cid = _get_class_id(pc)
                req_id = _get_first_requirement_id(pc)
                return LookupResult(
                    matched_class=cid,
                    confidence=1.0,
                    alternatives=(),
                    lookup_strategy=strategy,
                    evidence_requirement_id=req_id,
                    diagnostic_notes=(
                        f"Exact name match on class '{name}'."
                    ),
                )
        return LookupResult(
            matched_class=None,
            confidence=0.0,
            alternatives=(),
            lookup_strategy=strategy,
            evidence_requirement_id=None,
            diagnostic_notes=(
                f"No exact match found for description: '{problem_description[:60]}'."
            ),
        )

    # --- Filter candidates ---
    candidates = list(all_classes)

    if strategy == LookupStrategy.CATEGORY_FIRST and category_hint:
        filtered = [
            pc for pc in candidates
            if str(getattr(pc, "category", "")).lower() == category_hint.lower()
        ]
        if filtered:
            candidates = filtered

    if strategy == LookupStrategy.DIFFICULTY_FIRST:
        # Heuristic: description containing "hard", "np", "complex" → high difficulty
        hard_keywords = {"hard", "np", "complex", "intractable", "exponential"}
        easy_keywords = {"easy", "linear", "simple", "trivial", "constant"}
        is_hard = bool(desc_set & hard_keywords)
        is_easy = bool(desc_set & easy_keywords)
        if is_hard or is_easy:
            target_level = "hard" if is_hard else "easy"
            filtered = [
                pc for pc in candidates
                if str(getattr(pc, "difficulty", "")).lower() == target_level
            ]
            if filtered:
                candidates = filtered

    # --- Score all candidates ---
    if strategy == LookupStrategy.SEMANTIC:
        scored: list[tuple[float, Any]] = []
        for pc in candidates:
            class_text_parts: list[str] = []
            for attr in ("name", "description", "class_name", "class_description"):
                val = getattr(pc, attr, None)
                if isinstance(val, str):
                    class_text_parts.append(val)
            class_tokens = set(_tokenize(" ".join(class_text_parts)))
            score = _jaccard_similarity(desc_set, class_tokens)
            scored.append((score, pc))
    else:
        scored = [
            (_score_class_match(desc_tokens, pc), pc)
            for pc in candidates
        ]

    # Sort descending by score
    scored.sort(key=lambda t: t[0], reverse=True)

    if not scored or scored[0][0] == 0.0:
        return LookupResult(
            matched_class=None,
            confidence=0.0,
            alternatives=tuple(_get_class_id(pc) for _, pc in scored[:5]),
            lookup_strategy=strategy,
            evidence_requirement_id=None,
            diagnostic_notes=(
                f"No class scored above zero for strategy={strategy.value}."
            ),
        )

    best_score, best_pc = scored[0]
    best_id = _get_class_id(best_pc)

    # Normalise confidence against the best raw score
    max_possible = 1.1  # max from _score_class_match (base + boost)
    confidence = min(1.0, best_score / max_possible)

    alternatives = tuple(
        _get_class_id(pc)
        for score, pc in scored[1:6]
        if score > 0.0 and _get_class_id(pc) != best_id
    )

    req_id = _get_first_requirement_id(best_pc)

    notes = (
        f"Strategy={strategy.value}; raw_score={best_score:.4f}; "
        f"desc_tokens={len(desc_tokens)}; candidates_searched={len(candidates)}."
    )
    if category_hint and strategy == LookupStrategy.CATEGORY_FIRST:
        notes += f" Category filter applied: '{category_hint}'."

    return LookupResult(
        matched_class=best_id,
        confidence=confidence,
        alternatives=alternatives,
        lookup_strategy=strategy,
        evidence_requirement_id=req_id,
        diagnostic_notes=notes,
    )


def _get_first_requirement_id(pc: Any) -> str | None:
    """Extract the first EvidenceRequirement ID from a ProblemClass or dict.

    Checks ``requirements``, ``evidence_requirements``, and ``requirement_id``
    attributes.

    Args:
        pc: A ProblemClass-like object or dict.

    Returns:
        String requirement ID, or None if not found.
    """
    if isinstance(pc, dict):
        val = pc.get("requirements") or pc.get("evidence_requirements")
        if isinstance(val, (list, tuple)) and val:
            return _get_requirement_id(val[0])
        if isinstance(val, dict) and val:
            return str(next(iter(val)))
        single = pc.get("requirement_id")
        if isinstance(single, str) and single:
            return single
        return None
    for attr in ("requirements", "evidence_requirements"):
        val = getattr(pc, attr, None)
        if isinstance(val, (list, tuple)) and val:
            return _get_requirement_id(val[0])
        if isinstance(val, dict) and val:
            key = next(iter(val))
            return str(key)
    single = getattr(pc, "requirement_id", None)
    if isinstance(single, str) and single:
        return single
    return None


# ---------------------------------------------------------------------------
# §14.5.9  signature_matching_algorithm
# ---------------------------------------------------------------------------


def signature_matching_algorithm(
    signature: Any,
    catalog: Any,
    *,
    threshold: float = 0.5,
) -> list[Any]:
    """Find problem classes whose signatures are compatible with the given signature.

    Iterates all catalog entries, retrieves their SemanticSignature (if any),
    and scores compatibility by checking input/output schema key overlap.
    Returns classes with compatibility score above ``threshold``.

    The compatibility score is computed as the Jaccard similarity of the key
    sets of the input schemas plus the Jaccard similarity of the output schemas,
    averaged:  ``0.5 * (j_input + j_output)``.  When a class has no signature,
    a fallback text-based similarity is attempted.

    Args:
        signature: The SemanticSignature to match against.  Must have
            ``input_schema`` and ``output_schema`` attributes (dicts).
        catalog: The AtlasCatalog to search.
        threshold: Minimum compatibility score in [0.0, 1.0].  Classes scoring
            below this are excluded.  Default is 0.5.

    Returns:
        List of compatible ProblemClass objects sorted by compatibility score
        descending.  May be empty if no class exceeds the threshold.
    """
    all_classes = _get_catalog_classes(catalog)

    # Extract keys from the query signature
    query_input_keys = set(_extract_schema_keys(getattr(signature, "input_schema", {})))
    query_output_keys = set(_extract_schema_keys(getattr(signature, "output_schema", {})))
    query_name_tokens = set(
        _tokenize(str(getattr(signature, "name", "") or ""))
    )

    scored: list[tuple[float, Any]] = []

    for pc in all_classes:
        pc_sig = _get_class_signature(pc)

        if pc_sig is not None:
            cls_input_keys = set(_extract_schema_keys(getattr(pc_sig, "input_schema", {})))
            cls_output_keys = set(_extract_schema_keys(getattr(pc_sig, "output_schema", {})))

            j_in = _jaccard_similarity(query_input_keys, cls_input_keys)
            j_out = _jaccard_similarity(query_output_keys, cls_output_keys)
            compat_score = 0.5 * (j_in + j_out)
        else:
            # Fallback: text similarity on class name tokens
            cls_tokens = set(_tokenize(str(getattr(pc, "name", "") or "")))
            compat_score = _jaccard_similarity(query_name_tokens, cls_tokens) * 0.6

        if compat_score >= threshold:
            scored.append((compat_score, pc))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [pc for _, pc in scored]


def _extract_schema_keys(schema: Any) -> list[str]:
    """Extract all keys from a schema object.

    Handles dicts directly and objects with a ``keys()`` method.

    Args:
        schema: A dict-like schema object.

    Returns:
        List of string keys.
    """
    if isinstance(schema, dict):
        return [str(k) for k in schema.keys()]
    if hasattr(schema, "keys") and callable(schema.keys):
        try:
            return [str(k) for k in schema.keys()]
        except Exception:
            pass
    if isinstance(schema, (list, tuple)):
        return [str(item) for item in schema]
    return []


def _get_class_signature(pc: Any) -> Any | None:
    """Retrieve the SemanticSignature from a ProblemClass.

    Args:
        pc: A ProblemClass-like object.

    Returns:
        SemanticSignature object, or None.
    """
    for attr in ("signature", "semantic_signature", "sig"):
        val = getattr(pc, attr, None)
        if val is not None:
            return val
    get_fn = getattr(pc, "get_signature", None)
    if callable(get_fn):
        try:
            return get_fn()
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# §14.5.10  evidence_routing_algorithm
# ---------------------------------------------------------------------------


def evidence_routing_algorithm(
    evidence: dict[str, float],
    requirements: list[Any],
) -> dict[str, float]:
    """Route evidence contributions to the requirements they satisfy.

    For each requirement, inspects which of the provided evidence channels are
    listed as required.  Computes an aggregate trust score for the requirement
    using a weighted mean of the matching channels.  Channels not listed in the
    requirement are ignored; missing channels contribute 0.0 to the aggregate.

    The aggregate trust for a requirement is:

        aggregate = sum(evidence[ch] for ch in req_channels if ch in evidence)
                    / max(1, len(req_channels))

    This gives a value in [0.0, 1.0] that represents what fraction of the
    required trust is actually provided.

    Args:
        evidence: Mapping from channel_id to trust score in [0.0, 1.0].
        requirements: List of EvidenceRequirement objects to route evidence to.

    Returns:
        Dict mapping requirement_id to aggregate trust score.

    Examples:
        >>> routing = evidence_routing_algorithm(
        ...     {"proof": 0.9, "test": 0.7},
        ...     [req_formal, req_informal],
        ... )
        >>> routing["req_formal"]
        0.9
    """
    result: dict[str, float] = {}

    for req in requirements:
        req_id = _get_requirement_id(req)
        req_channels = _get_requirement_channels(req)

        if not req_channels:
            # Requirement specifies no channels — vacuously satisfied at max trust
            result[req_id] = 1.0
            continue

        total = 0.0
        for ch in req_channels:
            total += evidence.get(ch, 0.0)

        aggregate = total / len(req_channels)
        result[req_id] = min(1.0, max(0.0, aggregate))

    return result


# ---------------------------------------------------------------------------
# §14.5.11  class_lattice_traversal
# ---------------------------------------------------------------------------


def class_lattice_traversal(
    start_class: str,
    direction: TraversalDirection,
    catalog: Any,
    *,
    max_depth: int = 10,
) -> list[str]:
    """Traverse the problem class lattice from a start class in the given direction.

    Uses breadth-first search.  UP traversal visits parent (superclass) nodes;
    DOWN traversal visits child (subclass) nodes; BOTH does a bidirectional BFS.

    The catalog is used to resolve class_ids to ProblemClass objects so that
    parent/child relationships can be followed.  If a class_id cannot be
    resolved (e.g., it refers to an external class not in this catalog), it is
    still included in the output but not further expanded.

    Args:
        start_class: class_id or name of the starting class.
        direction: TraversalDirection.UP, DOWN, or BOTH.
        catalog: The AtlasCatalog containing the lattice.
        max_depth: Maximum BFS depth.  Traversal stops at this depth.
            Default is 10.

    Returns:
        List of class_ids visited (excluding ``start_class``), in BFS order.
        May be empty if the start class has no reachable neighbours.

    Raises:
        ValueError: If max_depth < 1.
    """
    if max_depth < 1:
        raise ValueError(
            f"class_lattice_traversal: max_depth must be >= 1, got {max_depth}."
        )

    # Build neighbor function depending on direction
    def get_up_neighbors(cid: str) -> list[str]:
        pc = _find_class_by_id(cid, catalog)
        if pc is None:
            return []
        return _get_parent_ids(pc)

    def get_down_neighbors(cid: str) -> list[str]:
        pc = _find_class_by_id(cid, catalog)
        if pc is None:
            return []
        return _get_child_ids(pc)

    def get_both_neighbors(cid: str) -> list[str]:
        return get_up_neighbors(cid) + get_down_neighbors(cid)

    if direction.includes_up() and direction.includes_down():
        neighbor_fn = get_both_neighbors
    elif direction.includes_up():
        neighbor_fn = get_up_neighbors
    else:
        neighbor_fn = get_down_neighbors

    return _bfs_lattice(start_class, neighbor_fn, max_depth)


# ---------------------------------------------------------------------------
# §14.5.12  optimal_evidence_strategy
# ---------------------------------------------------------------------------


def optimal_evidence_strategy(
    problem_class: Any,
    available_channels: list[str],
    requirement: Any | None = None,
    *,
    budget_limit: int = 5,
) -> list[str]:
    """Compute the optimal channel acquisition order for a problem class.

    Given a problem class and a set of available evidence channels, returns the
    ordered list of channels to acquire that maximises trust gain per unit of
    effort, subject to ``budget_limit``.

    The algorithm assigns a priority score to each available channel:

    1. Required channels (listed in the requirement) that are available receive
       a high base priority of 1.0.
    2. Channels matching keywords in the class name/description receive a bonus
       of 0.2.
    3. Required channels are ranked first; surplus channels last.
    4. Among required channels, shorter channel IDs are given a slight
       tie-breaking preference (proxy for simpler/cheaper channels).

    Args:
        problem_class: The problem class to verify.
        available_channels: List of channel IDs that can be acquired.
        requirement: Optional EvidenceRequirement.  If None, the first
            requirement attached to ``problem_class`` is used.  If
            ``problem_class`` has no requirements, all available channels are
            returned up to ``budget_limit``.
        budget_limit: Maximum number of channels to return.  Must be >= 1.

    Returns:
        Ordered list of channel_ids to acquire, highest value first, at most
        ``budget_limit`` entries.

    Raises:
        ValueError: If budget_limit < 1.
    """
    if budget_limit < 1:
        raise ValueError(
            f"optimal_evidence_strategy: budget_limit must be >= 1, got {budget_limit}."
        )

    # Resolve requirement
    if requirement is None:
        req_list: list[Any] = []
        for attr in ("requirements", "evidence_requirements"):
            val = getattr(problem_class, attr, None)
            if isinstance(val, (list, tuple)) and val:
                req_list = list(val)
                break
            if isinstance(val, dict) and val:
                req_list = list(val.values())
                break
        effective_req = req_list[0] if req_list else None
    else:
        effective_req = requirement

    required_channels: set[str] = set()
    if effective_req is not None:
        required_channels = set(_get_requirement_channels(effective_req))

    # Build keyword set from class text for bonus scoring
    class_text = " ".join(
        str(getattr(problem_class, attr, "") or "")
        for attr in ("name", "description", "class_name")
    )
    class_keywords = set(_tokenize(class_text))

    available_set = set(available_channels)

    def _channel_priority(ch_id: str) -> float:
        score = 0.0
        if ch_id in required_channels:
            score += 1.0
        # Keyword affinity bonus
        ch_tokens = set(_tokenize(ch_id))
        if ch_tokens & class_keywords:
            score += 0.2
        # Prefer shorter IDs as a proxy for simplicity (tie-breaker)
        score += max(0.0, (20 - len(ch_id)) / 200.0)
        return score

    # Rank all available channels
    ranked = sorted(available_channels, key=_channel_priority, reverse=True)

    # Apply budget
    return ranked[:budget_limit]


# ---------------------------------------------------------------------------
# §14.5.13  cross_class_unification
# ---------------------------------------------------------------------------


def cross_class_unification(
    class_a_id: str,
    class_b_id: str,
    catalog: Any,
) -> UnificationResult:
    """Unify two problem classes into their least common super-class (LUB).

    Computes the least upper bound of ``class_a`` and ``class_b`` in the class
    lattice using the following procedure:

    1. If ``class_a_id == class_b_id``, return trivial unification.
    2. Build the set of ancestors of ``class_a`` (including itself) via UP-BFS.
    3. Traverse UP from ``class_b`` and return the first ancestor found in
       class_a's ancestor set — that is the LUB.
    4. If no shared ancestor exists, check whether a UNIVERSAL top-level class
       (a class with no parents) exists and return it.
    5. If all of the above fail, return a failed UnificationResult.

    Args:
        class_a_id: ID of the first problem class.
        class_b_id: ID of the second problem class.
        catalog: The AtlasCatalog containing the lattice.

    Returns:
        UnificationResult describing the unified class and the mode used.
    """
    if class_a_id == class_b_id:
        return UnificationResult(
            class_a_id=class_a_id,
            class_b_id=class_b_id,
            unified_class_id=class_a_id,
            unification_mode="trivial_equal",
            is_trivial=True,
            notes="Both class IDs are identical; unification is trivially the class itself.",
        )

    # Build ancestor set for class_a (includes class_a itself)
    a_ancestors: set[str] = {class_a_id}
    a_ancestors.update(class_lattice_traversal(class_a_id, TraversalDirection.UP, catalog))

    # Check if class_b is an ancestor of class_a (a is more specific)
    if class_b_id in a_ancestors:
        return UnificationResult(
            class_a_id=class_a_id,
            class_b_id=class_b_id,
            unified_class_id=class_b_id,
            unification_mode="direct_ancestor",
            is_trivial=True,
            notes=(
                f"'{class_b_id}' is an ancestor of '{class_a_id}'; "
                "LUB is the more general class."
            ),
        )

    # Build ancestor set for class_b (includes class_b itself)
    b_ancestors: set[str] = {class_b_id}
    b_ancestors.update(class_lattice_traversal(class_b_id, TraversalDirection.UP, catalog))

    # Check if class_a is an ancestor of class_b
    if class_a_id in b_ancestors:
        return UnificationResult(
            class_a_id=class_a_id,
            class_b_id=class_b_id,
            unified_class_id=class_a_id,
            unification_mode="direct_ancestor",
            is_trivial=True,
            notes=(
                f"'{class_a_id}' is an ancestor of '{class_b_id}'; "
                "LUB is the more general class."
            ),
        )

    # Find shared ancestor: BFS up from class_b, find first in a_ancestors
    shared: str | None = None
    b_up_path = class_lattice_traversal(class_b_id, TraversalDirection.UP, catalog)
    for cid in b_up_path:
        if cid in a_ancestors:
            shared = cid
            break

    if shared is not None:
        return UnificationResult(
            class_a_id=class_a_id,
            class_b_id=class_b_id,
            unified_class_id=shared,
            unification_mode="shared_ancestor",
            is_trivial=False,
            notes=(
                f"LUB computed as first shared ancestor '{shared}' found during "
                f"BFS from '{class_b_id}' intersecting ancestors of '{class_a_id}'."
            ),
        )

    # Fallback: find a UNIVERSAL top class (no parents)
    universal = _find_universal_top(catalog)
    if universal is not None:
        return UnificationResult(
            class_a_id=class_a_id,
            class_b_id=class_b_id,
            unified_class_id=universal,
            unification_mode="universal_top",
            is_trivial=False,
            notes=(
                f"No shared ancestor found; using universal top class '{universal}'."
            ),
        )

    return UnificationResult(
        class_a_id=class_a_id,
        class_b_id=class_b_id,
        unified_class_id=None,
        unification_mode="failed",
        is_trivial=False,
        notes=(
            f"Unification of '{class_a_id}' and '{class_b_id}' failed: "
            "no shared ancestor and no universal top class found in catalog."
        ),
    )


def _find_universal_top(catalog: Any) -> str | None:
    """Find a universal top class in the catalog (a class with no parents).

    When multiple parentless classes exist, returns the one named 'UNIVERSAL',
    'TOP', or 'ROOT' (case-insensitive), otherwise the first found.

    Args:
        catalog: An AtlasCatalog-like object.

    Returns:
        class_id of the universal top class, or None.
    """
    candidates: list[tuple[str, bool]] = []  # (class_id, is_named_top)
    preferred_names = {"universal", "top", "root", "any", "all"}

    for pc in _get_catalog_classes(catalog):
        if not _get_parent_ids(pc):
            cid = _get_class_id(pc)
            name_lower = cid.lower()
            is_preferred = any(p in name_lower for p in preferred_names)
            candidates.append((cid, is_preferred))

    if not candidates:
        return None

    # Prefer named top classes
    for cid, is_preferred in candidates:
        if is_preferred:
            return cid

    return candidates[0][0]


# ---------------------------------------------------------------------------
# §14.5.14  requirement_satisfaction_check
# ---------------------------------------------------------------------------


def requirement_satisfaction_check(
    evidence_set: dict[str, float],
    requirements: list[Any],
) -> dict[str, SatisfactionReport]:
    """Check whether an evidence set satisfies a list of requirements.

    For each requirement, computes per-channel trust scores, the aggregate
    trust, identifies gaps (channels below the threshold), and produces a
    verdict.  Returns one SatisfactionReport per requirement.

    A requirement is satisfied when:

        aggregate_trust >= threshold  AND  no channel trust < threshold

    Args:
        evidence_set: Mapping from channel_id to trust score in [0.0, 1.0].
        requirements: List of EvidenceRequirement objects to check.

    Returns:
        Dict mapping requirement_id to SatisfactionReport.  Each report
        contains full per-channel details and a human-readable verdict.

    Examples:
        >>> reports = requirement_satisfaction_check(
        ...     {"proof": 0.95, "review": 0.8},
        ...     [req_formal],
        ... )
        >>> reports["req_formal"].satisfied
        True
    """
    reports: dict[str, SatisfactionReport] = {}

    for req in requirements:
        req_id = _get_requirement_id(req)
        req_channels = _get_requirement_channels(req)
        threshold = _get_requirement_threshold(req)

        if not req_channels:
            # Vacuously satisfied
            reports[req_id] = SatisfactionReport(
                requirement_id=req_id,
                satisfied=True,
                aggregate_trust=1.0,
                channel_scores=(),
                gaps=(),
                verdict="PASS (no channels required)",
            )
            continue

        channel_scores_list: list[tuple[str, float]] = []
        gaps: list[str] = []

        for ch in req_channels:
            score = evidence_set.get(ch, 0.0)
            score = min(1.0, max(0.0, float(score)))
            channel_scores_list.append((ch, score))
            if score < threshold:
                gaps.append(ch)

        aggregate = sum(s for _, s in channel_scores_list) / len(channel_scores_list)
        satisfied = aggregate >= threshold and len(gaps) == 0

        if satisfied:
            verdict = "PASS"
        elif gaps:
            verdict = f"FAIL – {len(gaps)} gap{'s' if len(gaps) != 1 else ''}"
        else:
            verdict = f"FAIL – aggregate trust {aggregate:.3f} < threshold {threshold:.3f}"

        reports[req_id] = SatisfactionReport(
            requirement_id=req_id,
            satisfied=satisfied,
            aggregate_trust=round(aggregate, 6),
            channel_scores=tuple(channel_scores_list),
            gaps=tuple(gaps),
            verdict=verdict,
        )

    return reports


# ---------------------------------------------------------------------------
# §14.5.15  compute_algorithm_metrics
# ---------------------------------------------------------------------------


def compute_algorithm_metrics(results: list[LookupResult]) -> dict[str, float]:
    """Compute precision, recall, and confidence statistics over a batch of lookup results.

    Treats results with ``confidence > 0.5`` as positive matches.  Computes:

    - ``precision``: Fraction of positive matches that are definitive (confidence > 0.9).
    - ``mean_confidence``: Average confidence across all results.
    - ``hit_rate``: Fraction of results where ``matched_class`` is not None.
    - ``definitive_rate``: Fraction of results where ``is_definitive()`` is True.
    - ``std_confidence``: Standard deviation of confidence scores.

    Args:
        results: List of LookupResult objects from one or more lookup calls.

    Returns:
        Dict of metric names to float values.  Returns a dict of zeros when
        ``results`` is empty.

    Examples:
        >>> metrics = compute_algorithm_metrics(results)
        >>> metrics["hit_rate"]
        0.85
    """
    if not results:
        return {
            "precision": 0.0,
            "mean_confidence": 0.0,
            "hit_rate": 0.0,
            "definitive_rate": 0.0,
            "std_confidence": 0.0,
            "count": 0.0,
        }

    n = len(results)
    confidences = [r.confidence for r in results]
    hits = sum(1 for r in results if r.matched_class is not None)
    definitives = sum(1 for r in results if r.is_definitive())
    positives = [r for r in results if r.confidence > 0.5]
    precise = sum(1 for r in positives if r.is_definitive())

    mean_conf = sum(confidences) / n
    variance = sum((c - mean_conf) ** 2 for c in confidences) / n
    std_conf = math.sqrt(variance)
    precision = precise / len(positives) if positives else 0.0

    return {
        "precision": round(precision, 6),
        "mean_confidence": round(mean_conf, 6),
        "hit_rate": round(hits / n, 6),
        "definitive_rate": round(definitives / n, 6),
        "std_confidence": round(std_conf, 6),
        "count": float(n),
    }




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "TraversalDirection",
    "LookupStrategy",
    # Dataclasses
    "LookupResult",
    "RoutingResult",
    "UnificationResult",
    "SatisfactionReport",
    # Main algorithm functions
    "atlas_lookup_algorithm",
    "signature_matching_algorithm",
    "evidence_routing_algorithm",
    "class_lattice_traversal",
    "optimal_evidence_strategy",
    "cross_class_unification",
    "requirement_satisfaction_check",
    # Helper / utility
    "compute_algorithm_metrics",
    # Internal helpers exposed for testing
    "_tokenize",
    "_jaccard_similarity",
    "_score_class_match",
    "_bfs_lattice",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
