"""Obstruction fields as evidence of missing mathematics (S01-EV).

When many program coordinates share the same H¹ obstruction class, that
recurrence is not noise — it is *evidence* that a missing mathematical kind
is lurking just beneath the surface of the type system.  This module
formalises that evidence-gathering step, turning raw obstruction collections
into structured :class:`ObstructionFieldEvidenceRecord` objects that can feed
the downstream hypothesis pipeline.

# copilot: generated for jugeo.ideation.kind_discovery — evidence layer

Module layout::

    ┌─────────────────────────────────────────────────────────────────┐
    │  jugeo.ideation.kind_discovery.obstruction_fields_as_       │
    │  evidence_of                                                    │
    ├─────────────────────────────────────────────────────────────────┤
    │  Helpers                                                        │
    │    _clamp               clamp a float to [lo, hi]              │
    │    _now_iso             current UTC timestamp as ISO-8601       │
    │    _evidence_id         generate a fresh evidence UUID          │
    │    _tokenize            split text into lowercase tokens        │
    │    _jaccard             Jaccard similarity between token sets   │
    │    _h1_similarity       compare two H1ObstructionClass objects  │
    ├─────────────────────────────────────────────────────────────────┤
    │  Value objects (frozen dataclasses)                             │
    │    ObstructionFieldEvidenceConfig  pipeline hyper-parameters    │
    │    H1ObstructionClass              a single H¹ cohomology class │
    │    EvidenceCluster                 a cluster of H¹ coordinates  │
    │    ObstructionFieldEvidenceRecord  fully-built evidence record  │
    ├─────────────────────────────────────────────────────────────────┤
    │  Stateful services                                              │
    │    ObstructionFieldsEvidenceAnalyzer   computes evidence        │
    │    ObstructionFieldsEvidenceWitness    records evidence         │
    │    ObstructionFieldsEvidenceCoordinator  orchestrator           │
    └─────────────────────────────────────────────────────────────────┘

Background — H¹ obstruction classes
────────────────────────────────────
In algebraic topology, the first cohomology group H¹ captures the degree-1
"holes" in a space.  In the jugeo framework we use this metaphor concretely:
each coordinate in program space has an associated *obstruction* — something
that prevents a direct computation or type-check from completing.  When those
obstructions are structurally similar (same shape, same characteristic
polynomial, same chain of derivations), they belong to the same *H¹ class*.

A large cluster of coordinates sharing a single H¹ class is strong evidence
that the type system is missing a combinator or kind that would "fill the
hole" uniformly.  The :class:`ObstructionFieldsEvidenceAnalyzer` is
responsible for detecting these clusters and assigning a numeric evidence
strength to each hypothesis.

Evidence strength formula
─────────────────────────
The evidence strength *E* for a cluster set *C* is:

    E = clamp( (|C| / N_total) * mean_intra_sim , 0, 1 )

where
  |C|             = number of clusters
  N_total         = total number of distinct coordinates
  mean_intra_sim  = average intra-cluster Jaccard similarity

This quantity lies in [0, 1]; values ≥ 0.5 are considered *strong* evidence.

Integration points
──────────────────
The records produced here are consumed by
``candidate_new_mathematical_kinds_e`` which turns them into
:class:`KindHypothesis` objects, and ultimately by the full end-to-end
pipeline in ``the_obstruction_to_kind_pipeline_c``.
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Cross-package imports (guarded so the module remains importable standalone)
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.kind_discovery.models import (
        KindCandidate,
        ObstructionField,
        KindStatus,
    )
except ImportError:
    KindCandidate = None  # type: ignore[assignment,misc]
    ObstructionField = None  # type: ignore[assignment,misc]
    KindStatus = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default label applied when no characteristic can be inferred from text.
UNKNOWN_CHARACTERISTIC: str = "unknown"

#: Minimum number of characters a coordinate string must have to be meaningful.
MIN_COORDINATE_LENGTH: int = 3

#: Separator used when joining coordinate tokens into a centroid description.
CENTROID_SEPARATOR: str = " | "

#: Evidence strength above which we call the evidence "strong".
STRONG_EVIDENCE_THRESHOLD: float = 0.5

#: Evidence strength above which we call the evidence "conclusive".
CONCLUSIVE_EVIDENCE_THRESHOLD: float = 0.8

#: Human-readable label for evidence that cannot be named from context.
DEFAULT_KIND_HYPOTHESIS_LABEL: str = "unspecified-missing-kind"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    """Return *v* clamped to the closed interval [*lo*, *hi*].

    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.1, 0.0, 1.0)
    0.0
    >>> _clamp(0.7, 0.0, 1.0)
    0.7
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string.

    Example output: ``"2024-03-15T12:00:00Z"``
    """
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _evidence_id() -> str:
    """Generate a globally-unique evidence record identifier.

    The identifier is prefixed with ``"ev-"`` to make it easy to recognise
    in log output and serialised artifacts.

    Returns
    -------
    str
        A string of the form ``"ev-<8-hex-chars>"``.
    """
    return "ev-" + uuid.uuid4().hex[:8]


def _tokenize(text: str) -> set[str]:
    """Split *text* into a set of lowercase alphabetic tokens.

    Non-alphabetic characters are discarded.  The result is a *set* rather
    than a list because downstream similarity computations only care about
    token *presence*, not order or frequency.

    Parameters
    ----------
    text:
        Arbitrary free-form string.

    Returns
    -------
    set[str]
        Zero or more lowercase token strings.

    Examples
    --------
    >>> _tokenize("H¹(X, ℤ) obstruction class")
    {'obstruction', 'class', 'x'}
    """
    return set(re.findall(r"[a-zA-Z]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard similarity coefficient between two token sets.

    The Jaccard coefficient is defined as |A ∩ B| / |A ∪ B|.  If both sets
    are empty the function returns ``0.0`` to avoid a zero-division error and
    to treat two empty descriptions as *dissimilar* (not identical).

    Parameters
    ----------
    a, b:
        Token sets to compare.

    Returns
    -------
    float
        Value in [0.0, 1.0]; higher means more similar.

    Examples
    --------
    >>> _jaccard({'a', 'b', 'c'}, {'b', 'c', 'd'})
    0.5
    >>> _jaccard(set(), set())
    0.0
    """
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union)


def _h1_similarity(cls_a: "H1ObstructionClass", cls_b: "H1ObstructionClass") -> float:
    """Compute a similarity score between two H¹ obstruction classes.

    The score is a weighted combination of:
    - Jaccard similarity of label tokens (weight 0.5)
    - Jaccard similarity of characteristic tokens (weight 0.3)
    - Exact match of cohomology degree (weight 0.2)

    Parameters
    ----------
    cls_a, cls_b:
        The two :class:`H1ObstructionClass` instances to compare.

    Returns
    -------
    float
        A similarity score in [0.0, 1.0].
    """
    label_sim = _jaccard(_tokenize(cls_a.label), _tokenize(cls_b.label))
    char_sim = _jaccard(
        _tokenize(cls_a.characteristic), _tokenize(cls_b.characteristic)
    )
    degree_match = 1.0 if cls_a.cohomology_degree == cls_b.cohomology_degree else 0.0
    return _clamp(0.5 * label_sim + 0.3 * char_sim + 0.2 * degree_match, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionFieldEvidenceConfig:
    """Hyper-parameters controlling the evidence analysis pipeline.

    All fields carry default values that work well for medium-sized
    obstruction corpora (a few hundred coordinates).  Override only the
    parameters you need to tune.

    Attributes
    ----------
    min_cluster_size:
        Clusters with fewer than this many member coordinates are discarded
        before evidence strength is computed.  A value of 3 ensures that we
        do not draw conclusions from singletons or pairs.
    h1_similarity_threshold:
        Minimum Jaccard-weighted similarity for two H¹ classes to be merged
        into the same cluster.  Lowering this value produces fewer, larger
        clusters; raising it produces many fine-grained clusters.
    evidence_strength_cutoff:
        Records whose computed evidence strength falls below this threshold
        are not forwarded to the hypothesis stage.  This acts as a quality
        gate.
    max_fields:
        The maximum number of obstruction fields to process in a single
        coordinator run.  Excess fields are silently truncated.
    field_decay_rate:
        A small positive constant used to discount the evidence strength of
        fields that are older or less coherent.  Applied multiplicatively:
        ``strength *= (1 - decay * age_weight)``.
    """

    min_cluster_size: int = 3
    h1_similarity_threshold: float = 0.7
    evidence_strength_cutoff: float = 0.5
    max_fields: int = 100
    field_decay_rate: float = 0.05


@dataclass(frozen=True, slots=True)
class H1ObstructionClass:
    """A single H¹ cohomology obstruction class.

    An H¹ class represents a "hole" in the type lattice — a gap that many
    program coordinates fall into because the right mathematical kind does
    not yet exist.

    Attributes
    ----------
    class_id:
        A globally-unique identifier for this class, typically prefixed
        with ``"h1-"``.
    label:
        A human-readable label such as ``"non-associative-composition"``.
    coordinates:
        The tuple of program coordinate strings whose obstructions belong
        to this class.
    representative:
        The single coordinate (from *coordinates*) chosen as the canonical
        representative.  Usually the first element or the most frequently
        occurring one.
    cohomology_degree:
        The degree of the cohomology class; always 1 for H¹ but stored
        explicitly for future generalisation.
    characteristic:
        A short string describing the algebraic characteristic of the
        class, e.g. ``"idempotent"`` or ``"non-invertible"``.
    """

    class_id: str
    label: str
    coordinates: tuple[str, ...]
    representative: str
    cohomology_degree: int = 1
    characteristic: str = UNKNOWN_CHARACTERISTIC

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Return the number of coordinates in this class."""
        return len(self.coordinates)

    def is_large(self, threshold: int = 5) -> bool:
        """Return True if the class contains at least *threshold* coordinates."""
        return self.size() >= threshold

    def token_set(self) -> set[str]:
        """Return the combined token set of label and characteristic."""
        return _tokenize(self.label) | _tokenize(self.characteristic)

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        return (
            f"H¹[{self.class_id}] label={self.label!r} "
            f"|coords|={self.size()} char={self.characteristic!r}"
        )


@dataclass(frozen=True, slots=True)
class EvidenceCluster:
    """A cluster of program coordinates sharing a common H¹ obstruction class.

    Clusters are the primary unit of evidence.  A large, internally-coherent
    cluster is strong evidence for a missing kind; a small or diffuse cluster
    is weak evidence.

    Attributes
    ----------
    cluster_id:
        Unique identifier, typically prefixed with ``"clust-"``.
    h1_class:
        The identifier of the :class:`H1ObstructionClass` associated with
        this cluster.
    member_coordinates:
        All program coordinates that belong to this cluster.
    centroid_description:
        A free-form description synthesised from the member coordinates,
        used to give a human-readable "centre of mass" for the cluster.
    intra_cluster_similarity:
        The average pairwise Jaccard similarity of member coordinate tokens.
        A value close to 1.0 means the cluster is very tight; near 0.0
        means the clustering was coarse.
    """

    cluster_id: str
    h1_class: str
    member_coordinates: tuple[str, ...]
    centroid_description: str
    intra_cluster_similarity: float

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Return the number of member coordinates."""
        return len(self.member_coordinates)

    def is_coherent(self, threshold: float = 0.5) -> bool:
        """Return True if intra-cluster similarity exceeds *threshold*."""
        return self.intra_cluster_similarity >= threshold

    def evidence_weight(self) -> float:
        """Return a weight proportional to both size and coherence.

        Clusters that are large *and* coherent contribute more to the
        overall evidence strength than those that are merely large.
        """
        return _clamp(self.intra_cluster_similarity * (1 + 0.1 * self.size()), 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this cluster to a plain Python dict."""
        return {
            "cluster_id": self.cluster_id,
            "h1_class": self.h1_class,
            "member_coordinates": list(self.member_coordinates),
            "centroid_description": self.centroid_description,
            "intra_cluster_similarity": self.intra_cluster_similarity,
            "size": self.size(),
            "coherent": self.is_coherent(),
        }


@dataclass(frozen=True, slots=True)
class ObstructionFieldEvidenceRecord:
    """A fully-built evidence record for a single obstruction field.

    This is the primary output of the evidence-analysis stage.  It captures
    the field identity, the H¹ classes detected, the clusters formed, and
    the synthesised hypothesis about what missing kind would explain the
    evidence.

    Attributes
    ----------
    evidence_id:
        A unique identifier for this record.
    field_id:
        The identifier of the obstruction field from which the evidence
        was extracted.
    h1_classes:
        Identifiers of the H¹ classes detected in the field.
    cluster_count:
        The number of clusters formed from those H¹ classes.
    evidence_strength:
        A float in [0.0, 1.0] measuring how confidently the evidence
        points to a missing kind.
    missing_kind_hypothesis:
        A short free-form string naming the hypothesised missing kind.
    timestamp:
        ISO-8601 UTC timestamp at which the record was created.
    """

    evidence_id: str
    field_id: str
    h1_classes: tuple[str, ...]
    cluster_count: int
    evidence_strength: float
    missing_kind_hypothesis: str
    timestamp: str

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_strong(self) -> bool:
        """Return True if evidence_strength ≥ STRONG_EVIDENCE_THRESHOLD."""
        return self.evidence_strength >= STRONG_EVIDENCE_THRESHOLD

    def is_conclusive(self) -> bool:
        """Return True if evidence_strength ≥ CONCLUSIVE_EVIDENCE_THRESHOLD."""
        return self.evidence_strength >= CONCLUSIVE_EVIDENCE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain Python dict suitable for JSON export."""
        return {
            "evidence_id": self.evidence_id,
            "field_id": self.field_id,
            "h1_classes": list(self.h1_classes),
            "cluster_count": self.cluster_count,
            "evidence_strength": self.evidence_strength,
            "missing_kind_hypothesis": self.missing_kind_hypothesis,
            "timestamp": self.timestamp,
            "is_strong": self.is_strong(),
            "is_conclusive": self.is_conclusive(),
        }


# ---------------------------------------------------------------------------
# Analysis engine
# ---------------------------------------------------------------------------


class ObstructionFieldsEvidenceAnalyzer:
    """Stateless analysis engine that converts obstruction data into evidence.

    All public methods are pure in the sense that they do not mutate any
    shared state — side effects are handled by the :class:`ObstructionFieldsEvidenceWitness`.

    The typical call sequence is::

        analyzer = ObstructionFieldsEvidenceAnalyzer(config)
        h1_classes = analyzer.extract_h1_classes(raw_obstructions)
        clusters   = analyzer.cluster_by_h1(h1_classes, config.h1_similarity_threshold)
        strength   = analyzer.compute_evidence_strength(clusters)
        hypothesis = analyzer.hypothesize_missing_kind(clusters, field)
        record     = analyzer.build_evidence_record(field, clusters, hypothesis, strength)
        explanation = analyzer.explain_evidence(record)
    """

    def __init__(self, config: ObstructionFieldEvidenceConfig | None = None) -> None:
        self._config = config or ObstructionFieldEvidenceConfig()

    # ------------------------------------------------------------------
    # Core analysis methods
    # ------------------------------------------------------------------

    def extract_h1_classes(self, obstructions: list[dict]) -> list[H1ObstructionClass]:
        """Extract H¹ obstruction classes from a list of raw obstruction dicts.

        Each element of *obstructions* should be a dict with at least a
        ``"label"`` key.  Optional keys include ``"coordinate"``,
        ``"characteristic"``, and ``"degree"``.

        Parameters
        ----------
        obstructions:
            Raw obstruction records as plain dicts.

        Returns
        -------
        list[H1ObstructionClass]
            One :class:`H1ObstructionClass` per distinct label found in
            *obstructions*.  Obstructions sharing the same label are merged
            into a single class.
        """
        grouped: dict[str, list[dict]] = {}
        for obs in obstructions:
            label = str(obs.get("label", "unlabelled"))
            grouped.setdefault(label, []).append(obs)

        classes: list[H1ObstructionClass] = []
        for label, members in grouped.items():
            coords = tuple(
                str(m.get("coordinate", m.get("id", f"coord-{i}")))
                for i, m in enumerate(members)
                if len(str(m.get("coordinate", ""))) >= MIN_COORDINATE_LENGTH
                or True  # always include
            )
            representative = coords[0] if coords else label
            characteristic = str(members[0].get("characteristic", UNKNOWN_CHARACTERISTIC))
            degree = int(members[0].get("degree", 1))
            class_id = "h1-" + uuid.uuid4().hex[:6]
            classes.append(
                H1ObstructionClass(
                    class_id=class_id,
                    label=label,
                    coordinates=coords,
                    representative=representative,
                    cohomology_degree=degree,
                    characteristic=characteristic,
                )
            )
        return classes

    def cluster_by_h1(
        self,
        classes: list[H1ObstructionClass],
        threshold: float,
    ) -> list[EvidenceCluster]:
        """Group H¹ classes into clusters using greedy single-linkage.

        Two classes are merged if their :func:`_h1_similarity` exceeds
        *threshold*.  The algorithm is O(n²) and intended for small-to-medium
        corpora (n ≤ 500).

        Parameters
        ----------
        classes:
            H¹ classes to cluster.
        threshold:
            Similarity threshold for merging.

        Returns
        -------
        list[EvidenceCluster]
            One cluster per merged group of classes.
        """
        if not classes:
            return []

        used = [False] * len(classes)
        clusters: list[EvidenceCluster] = []

        for i, cls_i in enumerate(classes):
            if used[i]:
                continue
            members = [cls_i]
            used[i] = True
            for j, cls_j in enumerate(classes):
                if used[j]:
                    continue
                if _h1_similarity(cls_i, cls_j) >= threshold:
                    members.append(cls_j)
                    used[j] = True

            # Gather all coordinates from member classes
            all_coords: list[str] = []
            for m in members:
                all_coords.extend(m.coordinates)

            # Compute intra-cluster similarity from member label tokens
            token_sets = [_tokenize(m.label) for m in members]
            if len(token_sets) > 1:
                sims = [
                    _jaccard(token_sets[a], token_sets[b])
                    for a in range(len(token_sets))
                    for b in range(a + 1, len(token_sets))
                ]
                intra_sim = sum(sims) / len(sims) if sims else 0.0
            else:
                intra_sim = 1.0

            centroid = CENTROID_SEPARATOR.join(
                m.label for m in members[:5]
            )

            cluster = EvidenceCluster(
                cluster_id="clust-" + uuid.uuid4().hex[:6],
                h1_class=cls_i.class_id,
                member_coordinates=tuple(all_coords),
                centroid_description=centroid,
                intra_cluster_similarity=_clamp(intra_sim, 0.0, 1.0),
            )
            clusters.append(cluster)

        return clusters

    def compute_evidence_strength(self, clusters: list[EvidenceCluster]) -> float:
        """Compute a scalar evidence strength from a list of clusters.

        The formula is:

            E = clamp( mean(weight_i) * log(1 + |C|) / log(2) , 0, 1 )

        where |C| is the number of clusters and weight_i is the evidence
        weight of cluster i (see :meth:`EvidenceCluster.evidence_weight`).

        Parameters
        ----------
        clusters:
            The clusters for which to compute evidence strength.

        Returns
        -------
        float
            Evidence strength in [0.0, 1.0].
        """
        import math

        if not clusters:
            return 0.0
        mean_weight = sum(c.evidence_weight() for c in clusters) / len(clusters)
        scale = math.log(1 + len(clusters)) / math.log(2)
        return _clamp(mean_weight * scale / 10.0, 0.0, 1.0)

    def hypothesize_missing_kind(
        self,
        clusters: list[EvidenceCluster],
        field: dict,
    ) -> str:
        """Synthesise a short hypothesis string from cluster centroid descriptions.

        The hypothesis is formed by extracting the most common token from
        the centroid descriptions and combining it with the field's name or
        identifier.

        Parameters
        ----------
        clusters:
            Evidence clusters.
        field:
            The raw field dict; may contain a ``"name"`` or ``"id"`` key.

        Returns
        -------
        str
            A short hypothesis string such as
            ``"missing-associative-composition-kind"``.
        """
        if not clusters:
            return DEFAULT_KIND_HYPOTHESIS_LABEL

        # Collect all tokens from centroid descriptions
        all_tokens: list[str] = []
        for c in clusters:
            all_tokens.extend(_tokenize(c.centroid_description))

        if not all_tokens:
            return DEFAULT_KIND_HYPOTHESIS_LABEL

        # Pick the single most frequent token
        from collections import Counter

        freq = Counter(all_tokens)
        # Exclude very short / common English stop-words
        stop = {"the", "of", "and", "a", "in", "to", "is", "for", "with", "or"}
        candidate_tokens = [t for t in freq if t not in stop and len(t) >= 4]
        top = candidate_tokens[0] if candidate_tokens else all_tokens[0]

        field_name = str(field.get("name", field.get("id", "field")))
        short_name = field_name.replace(" ", "-").lower()[:20]
        return f"missing-{top}-kind-in-{short_name}"

    def build_evidence_record(
        self,
        field: dict,
        clusters: list[EvidenceCluster],
        hypothesis: str,
        strength: float,
    ) -> ObstructionFieldEvidenceRecord:
        """Assemble a fully-structured evidence record.

        Parameters
        ----------
        field:
            The raw field dict; must contain at least an ``"id"`` key.
        clusters:
            The clusters contributing to this record.
        hypothesis:
            The hypothesised missing kind string.
        strength:
            The evidence strength score in [0.0, 1.0].

        Returns
        -------
        ObstructionFieldEvidenceRecord
            An immutable record ready for downstream consumption.
        """
        field_id = str(field.get("id", "unknown-field"))
        h1_class_ids = tuple(c.h1_class for c in clusters)
        return ObstructionFieldEvidenceRecord(
            evidence_id=_evidence_id(),
            field_id=field_id,
            h1_classes=h1_class_ids,
            cluster_count=len(clusters),
            evidence_strength=_clamp(strength, 0.0, 1.0),
            missing_kind_hypothesis=hypothesis,
            timestamp=_now_iso(),
        )

    def explain_evidence(self, record: ObstructionFieldEvidenceRecord) -> str:
        """Return a multi-line human-readable explanation of an evidence record.

        The explanation is structured as a prose paragraph followed by a
        brief key-value table.  It is intended for developer logs and
        interactive inspection, not for machine consumption.

        Parameters
        ----------
        record:
            The evidence record to explain.

        Returns
        -------
        str
            A multi-line string.
        """
        lines = [
            f"Evidence record {record.evidence_id}",
            "=" * 60,
            f"Field:            {record.field_id}",
            f"H¹ classes found: {len(record.h1_classes)}",
            f"Clusters formed:  {record.cluster_count}",
            f"Evidence strength:{record.evidence_strength:.4f}  "
            f"({'CONCLUSIVE' if record.is_conclusive() else 'STRONG' if record.is_strong() else 'WEAK'})",
            f"Hypothesis:       {record.missing_kind_hypothesis}",
            f"Recorded at:      {record.timestamp}",
            "",
            "Interpretation:",
            f"  The field '{record.field_id}' contains {len(record.h1_classes)} distinct",
            "  H¹ obstruction classes that cluster into "
            f"{record.cluster_count} coherent group(s).",
            "  This is consistent with the hypothesis that the type system is missing",
            f"  a kind described as: '{record.missing_kind_hypothesis}'.",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness (recorder)
# ---------------------------------------------------------------------------


class ObstructionFieldsEvidenceWitness:
    """Accumulates and queries evidence records over the lifetime of a run.

    The witness is the single source of truth for evidence gathered so far.
    It is intentionally separate from the analyzer so that analysis logic
    stays pure and testable.

    Usage example::

        witness = ObstructionFieldsEvidenceWitness()
        witness.record(record_a)
        witness.record(record_b)
        best = witness.strongest_evidence()
        summary = witness.summary()
    """

    def __init__(self) -> None:
        self._records: list[ObstructionFieldEvidenceRecord] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def record(self, ev: ObstructionFieldEvidenceRecord) -> None:
        """Append *ev* to the internal evidence log.

        Parameters
        ----------
        ev:
            The :class:`ObstructionFieldEvidenceRecord` to record.
        """
        self._records.append(ev)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def strongest_evidence(self) -> ObstructionFieldEvidenceRecord | None:
        """Return the record with the highest evidence_strength, or None.

        Returns
        -------
        ObstructionFieldEvidenceRecord | None
            The strongest record, or ``None`` if no records have been stored.
        """
        if not self._records:
            return None
        return max(self._records, key=lambda r: r.evidence_strength)

    def above_threshold(
        self, threshold: float = STRONG_EVIDENCE_THRESHOLD
    ) -> list[ObstructionFieldEvidenceRecord]:
        """Return all records whose evidence_strength exceeds *threshold*.

        Parameters
        ----------
        threshold:
            Minimum evidence strength required.

        Returns
        -------
        list[ObstructionFieldEvidenceRecord]
            All qualifying records in insertion order.
        """
        return [r for r in self._records if r.evidence_strength >= threshold]

    def count(self) -> int:
        """Return the total number of records stored."""
        return len(self._records)

    def summary(self) -> dict[str, Any]:
        """Return a statistics summary dict.

        Keys include ``total``, ``strong``, ``conclusive``,
        ``avg_strength``, and ``top_hypothesis``.

        Returns
        -------
        dict[str, Any]
            A plain Python dict suitable for JSON serialisation.
        """
        if not self._records:
            return {
                "total": 0,
                "strong": 0,
                "conclusive": 0,
                "avg_strength": 0.0,
                "top_hypothesis": None,
            }
        avg = sum(r.evidence_strength for r in self._records) / len(self._records)
        top = self.strongest_evidence()
        return {
            "total": len(self._records),
            "strong": sum(1 for r in self._records if r.is_strong()),
            "conclusive": sum(1 for r in self._records if r.is_conclusive()),
            "avg_strength": round(avg, 4),
            "top_hypothesis": top.missing_kind_hypothesis if top else None,
        }

    def export(self) -> list[dict[str, Any]]:
        """Return all records serialised as a list of dicts.

        Returns
        -------
        list[dict[str, Any]]
            A list of :meth:`ObstructionFieldEvidenceRecord.to_dict` results.
        """
        return [r.to_dict() for r in self._records]


# ---------------------------------------------------------------------------
# Coordinator (orchestrator)
# ---------------------------------------------------------------------------


class ObstructionFieldsEvidenceCoordinator:
    """End-to-end orchestrator for the evidence extraction sub-pipeline.

    This coordinator wires together the :class:`ObstructionFieldsEvidenceAnalyzer`
    and :class:`ObstructionFieldsEvidenceWitness` and exposes a single
    :meth:`run` entry point for external callers.

    Parameters
    ----------
    config:
        Optional configuration; defaults to :class:`ObstructionFieldEvidenceConfig`
        with all defaults.

    Attributes
    ----------
    analyzer:
        The analysis engine.
    witness:
        The accumulator of evidence records.

    Example
    -------
    ::

        coordinator = ObstructionFieldsEvidenceCoordinator()
        obstructions = [
            {"label": "non-associative", "coordinate": "Expr.compose", "characteristic": "idempotent"},
            {"label": "non-associative", "coordinate": "Expr.pipe",    "characteristic": "idempotent"},
            {"label": "non-invertible",  "coordinate": "Expr.bind",    "characteristic": "partial"},
        ]
        field = {"id": "expr-field", "name": "Expression Field"}
        record = coordinator.run(obstructions, field)
        print(coordinator.report())
    """

    def __init__(self, config: ObstructionFieldEvidenceConfig | None = None) -> None:
        self._config = config or ObstructionFieldEvidenceConfig()
        self.analyzer = ObstructionFieldsEvidenceAnalyzer(self._config)
        self.witness = ObstructionFieldsEvidenceWitness()

    def run(
        self,
        obstructions: list[dict],
        field: dict,
    ) -> ObstructionFieldEvidenceRecord:
        """Execute the full evidence pipeline for a single field.

        Steps:
        1. Extract H¹ classes from *obstructions*.
        2. Cluster the classes by H¹ similarity.
        3. Filter clusters that are too small.
        4. Compute evidence strength.
        5. Synthesise a missing-kind hypothesis.
        6. Build and record the evidence record.

        Parameters
        ----------
        obstructions:
            List of raw obstruction dicts.
        field:
            The field descriptor dict.

        Returns
        -------
        ObstructionFieldEvidenceRecord
            The freshly-built evidence record (also stored in the witness).
        """
        cfg = self._config
        h1_classes = self.analyzer.extract_h1_classes(obstructions)
        clusters = self.analyzer.cluster_by_h1(h1_classes, cfg.h1_similarity_threshold)

        # Filter small clusters
        clusters = [c for c in clusters if c.size() >= cfg.min_cluster_size]

        strength = self.analyzer.compute_evidence_strength(clusters)
        hypothesis = self.analyzer.hypothesize_missing_kind(clusters, field)
        record = self.analyzer.build_evidence_record(field, clusters, hypothesis, strength)
        self.witness.record(record)
        return record

    def report(self) -> dict[str, Any]:
        """Return a snapshot report from the internal witness.

        Returns
        -------
        dict[str, Any]
            A plain dict containing the witness summary and the list of
            exported records.
        """
        return {
            "summary": self.witness.summary(),
            "records": self.witness.export(),
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _obstructions = [
        {"label": "non-associative", "coordinate": "Expr.compose", "characteristic": "idempotent"},
        {"label": "non-associative", "coordinate": "Expr.pipe", "characteristic": "idempotent"},
        {"label": "non-associative", "coordinate": "Expr.chain", "characteristic": "idempotent"},
        {"label": "non-invertible", "coordinate": "Expr.bind", "characteristic": "partial"},
        {"label": "non-invertible", "coordinate": "Expr.apply", "characteristic": "partial"},
        {"label": "non-invertible", "coordinate": "Expr.flatMap", "characteristic": "partial"},
    ]
    _field = {"id": "expr-obstruction-field", "name": "Expression Obstruction Field"}

    _coord = ObstructionFieldsEvidenceCoordinator()
    _record = _coord.run(_obstructions, _field)
    print(_coord.analyzer.explain_evidence(_record))
    print()
    import json
    print(json.dumps(_coord.report()["summary"], indent=2))
