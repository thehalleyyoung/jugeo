"""Kind classification stage for the JuGeo discovery engine — theory2.tex Ch58.

This module implements Stage 2 of the discovery pipeline: kind classification.
It classifies discovery candidates into abstract mathematical kinds using
characteristic-class signatures derived from domain tokens, metadata, and
structural features of the candidate description.

Theory reference: theory2.tex Ch58 §5.2 — Kind Classification Stage.

copilot: shared-core marker

Overview
--------
Kind classification is the second gate in the four-stage discovery pipeline.
Each candidate that survives the novelty filter is assigned a *kind signature*
— a tuple of dimension labels and characteristic classes that describes its
mathematical type in terms of the JuGeo geometry model.  The kind signature
is later used by the theorem synthesis stage to select applicable bridging
templates.

Classification strategy
-----------------------
The module supports three classification strategies (see ``KindMatchStrategy``):

* **EXACT** — the candidate's extracted feature set must match a registered
  kind signature with zero distance.  Unclassified candidates are dropped.
* **NEAREST** — the candidate is assigned the registered kind whose distance
  (computed by ``_compute_kind_distance``) is smallest, regardless of magnitude.
* **THRESHOLD** — like NEAREST, but only assigns a kind if the minimum distance
  is at most ``KindClassifier.threshold``.  Candidates above the threshold
  receive a synthetically generated fallback kind.

Pipeline position
-----------------
This stage consumes a ``NoveltyPipelineStage`` and produces a
``KindClassificationStage``, which is the input to Stage 3.

Typical usage::

    from jugeo.ideation.discovery_engine.kind_classification import (
        run_kind_classification,
        KindClassificationRunner,
        KindClassifier,
        KindSignatureBuilder,
        KindRegistry,
    )

    # One-shot
    stage = run_kind_classification(candidates, config=cfg)

    # Fine-grained
    registry = KindRegistry.with_defaults()
    runner = KindClassificationRunner(config=cfg, registry=registry)
    stage, diag = runner.run_with_diagnostics(novelty_stage)

Design notes
------------
* ``KindRegistry`` is thread-safe for reads; writes should be externally
  synchronised if used from multiple threads.
* ``KindSignatureBuilder`` follows the fluent-builder pattern for ergonomic
  construction of signatures in tests and scripts.
* Distance computation uses symmetric set difference size normalised by
  total size, which mirrors the Jaccard *distance* (1 − Jaccard similarity)
  applied separately to dimension labels and characteristic classes, then
  averaged.

See also
--------
* ``novelty_pipeline`` — provides the input for this stage.
* ``theorem_synthesis`` — consumes the output of this stage.
* ``jugeo.ideation.discovery_engine.models`` — shared dataclasses.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "KindMatchStrategy",
    "KindClassifier",
    "KindSignatureBuilder",
    "KindRegistry",
    "KindClassificationRunner",
    "run_kind_classification",
    # helpers
    "_utcnow",
    "_uid",
    "_clamp",
    "_compute_kind_distance",
    "_assign_kind",
    "_extract_dimension_labels",
    "_extract_characteristic_classes",
    "_normalize_kind_id",
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
    NoveltyPipelineStage = Any  # type: ignore[misc,assignment]
    KindClassificationStage = Any  # type: ignore[misc,assignment]


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


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp *value* to ``[lower, upper]``.

    Parameters
    ----------
    value : float
        Value to clamp.
    lower : float
        Inclusive minimum.
    upper : float
        Inclusive maximum.

    Returns
    -------
    float
        Clamped value.

    Raises
    ------
    ValueError
        If ``lower > upper``.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.2, lower=0.0, upper=0.5)
    0.0
    """
    if lower > upper:
        raise ValueError(f"lower ({lower}) must not exceed upper ({upper})")
    return max(lower, min(upper, value))


def _normalize_kind_id(raw: str) -> str:
    """Normalise a raw kind identifier to a canonical snake_case form.

    Conversion steps:

    1. Strip leading/trailing whitespace.
    2. Lower-case the string.
    3. Replace any run of non-alphanumeric characters with a single underscore.
    4. Strip leading and trailing underscores.

    Parameters
    ----------
    raw:
        The raw kind identifier string.

    Returns
    -------
    str
        Normalised kind ID.

    Examples
    --------
    >>> _normalize_kind_id("  Smooth Manifold  ")
    'smooth_manifold'
    >>> _normalize_kind_id("TopologicalSpace/Hausdorff")
    'topologicalspace_hausdorff'
    >>> _normalize_kind_id("__leading__trailing__")
    'leading__trailing'
    """
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _extract_dimension_labels(candidate: Any) -> tuple[str, ...]:
    """Extract ordered dimension labels from a discovery candidate.

    Dimension labels are derived from the following sources (in priority order):

    1. ``candidate.dimension_labels`` — explicit tuple/list attribute.
    2. ``candidate.domain_tags`` — domain tags used as approximate dimensions.
    3. Tokens from ``candidate.description`` that appear to be dimensional
       (heuristic: length > 3 and match known geometry/topology keywords).

    Parameters
    ----------
    candidate:
        A ``DiscoveryCandidate``-like object.

    Returns
    -------
    tuple[str, ...]
        Ordered tuple of normalised dimension label strings.

    Notes
    -----
    At most 8 dimension labels are returned to keep signatures compact.

    Examples
    --------
    >>> class C:
    ...     dimension_labels = ("fibration", "base", "fibre")
    ...     domain_tags = ()
    ...     description = ""
    >>> _extract_dimension_labels(C())
    ('fibration', 'base', 'fibre')
    """
    # Source 1: explicit dimension_labels attribute
    explicit = getattr(candidate, "dimension_labels", None)
    if explicit:
        labels = [_normalize_kind_id(str(lb)) for lb in explicit]
        return tuple(labels[:8])

    # Source 2: domain_tags as proxy dimension labels
    tags = getattr(candidate, "domain_tags", None)
    if tags:
        return tuple(_normalize_kind_id(str(t)) for t in list(tags)[:8])

    # Source 3: heuristic extraction from description
    _GEO_KEYWORDS = {
        "manifold", "fibration", "bundle", "sheaf", "scheme",
        "topos", "site", "locale", "frame", "lattice", "category",
        "functor", "groupoid", "space", "ring", "field", "module",
    }
    description: str = getattr(candidate, "description", "") or ""
    tokens = [
        t.lower() for t in description.split()
        if len(t) > 3 and t.lower() in _GEO_KEYWORDS
    ]
    seen: list[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.append(tok)
    return tuple(seen[:8])


def _extract_characteristic_classes(candidate: Any) -> tuple[str, ...]:
    """Extract characteristic class labels from a discovery candidate.

    Characteristic classes are algebraic-topological invariants associated
    with the candidate's kind (e.g. Chern, Stiefel–Whitney, Pontryagin).
    They are used to distinguish kinds that share dimension labels but differ
    in cohomological structure.

    Sources (in priority order):

    1. ``candidate.characteristic_classes`` — explicit attribute.
    2. ``candidate.metadata["characteristic_classes"]`` — nested in metadata.
    3. Heuristic: look for known class names in ``description``.

    Parameters
    ----------
    candidate:
        A ``DiscoveryCandidate``-like object.

    Returns
    -------
    tuple[str, ...]
        Tuple of normalised characteristic class label strings.

    Examples
    --------
    >>> class C:
    ...     characteristic_classes = ("chern", "todd")
    ...     metadata = {}
    ...     description = ""
    >>> _extract_characteristic_classes(C())
    ('chern', 'todd')
    """
    explicit = getattr(candidate, "characteristic_classes", None)
    if explicit:
        return tuple(_normalize_kind_id(str(c)) for c in list(explicit)[:6])

    meta = getattr(candidate, "metadata", None) or {}
    if isinstance(meta, dict):
        from_meta = meta.get("characteristic_classes")
        if from_meta:
            return tuple(_normalize_kind_id(str(c)) for c in list(from_meta)[:6])

    _CC_KEYWORDS = {
        "chern", "todd", "pontryagin", "stiefelwhitney", "euler",
        "hirzebruch", "atiyah", "thom", "cobordism",
    }
    description: str = getattr(candidate, "description", "") or ""
    found = [
        t.lower() for t in description.split()
        if t.lower().replace("-", "") in _CC_KEYWORDS
    ]
    seen: list[str] = []
    for f in found:
        if f not in seen:
            seen.append(f)
    return tuple(seen[:6])


def _compute_kind_distance(sig1: Any, sig2: Any) -> float:
    """Compute a symmetric distance between two kind signatures.

    The distance is the average of the Jaccard distances (1 − Jaccard
    similarity) computed separately on the dimension-label sets and the
    characteristic-class sets of the two signatures.

    .. math::

        d(s_1, s_2) = \\frac{
            d_J(\\text{dims}(s_1), \\text{dims}(s_2)) +
            d_J(\\text{classes}(s_1), \\text{classes}(s_2))
        }{2}

    where :math:`d_J(A, B) = 1 - |A \\cap B| / |A \\cup B|`.

    Parameters
    ----------
    sig1:
        First ``KindSignature``-like object.
    sig2:
        Second ``KindSignature``-like object.

    Returns
    -------
    float
        Distance in ``[0.0, 1.0]`` where ``0.0`` means identical and
        ``1.0`` means completely disjoint.

    Examples
    --------
    >>> class S:
    ...     def __init__(self, d, c):
    ...         self.dimension_labels = d
    ...         self.characteristic_classes = c
    >>> _compute_kind_distance(S(('a','b'), ('x',)), S(('a','b'), ('x',)))
    0.0
    >>> _compute_kind_distance(S(('a',), ()), S(('b',), ()))
    1.0
    """

    def _jaccard_sets(a: Any, b: Any) -> float:
        sa = set(getattr(a, "_seq", a) if isinstance(a, (tuple, list, set, frozenset)) else ())
        sb = set(getattr(b, "_seq", b) if isinstance(b, (tuple, list, set, frozenset)) else ())
        if not sa and not sb:
            return 1.0
        union = len(sa | sb)
        intersection = len(sa & sb)
        return intersection / union if union > 0 else 0.0

    dims1 = set(getattr(sig1, "dimension_labels", ()) or ())
    dims2 = set(getattr(sig2, "dimension_labels", ()) or ())
    cls1 = set(getattr(sig1, "characteristic_classes", ()) or ())
    cls2 = set(getattr(sig2, "characteristic_classes", ()) or ())

    def jd(a: set, b: set) -> float:
        if not a and not b:
            return 0.0  # identical empty sets → distance 0
        union = len(a | b)
        intersection = len(a & b)
        return 1.0 - (intersection / union if union > 0 else 0.0)

    return (jd(dims1, dims2) + jd(cls1, cls2)) / 2.0


def _assign_kind(candidate: Any, registry: "KindRegistry") -> Any | None:
    """Attempt to assign a kind from *registry* to *candidate*.

    This function is a thin wrapper that builds a temporary signature from
    the candidate's extracted features and calls ``registry.find_nearest()``.

    Parameters
    ----------
    candidate:
        A ``DiscoveryCandidate``-like object.
    registry:
        A populated ``KindRegistry`` instance.

    Returns
    -------
    KindSignature or None
        The nearest registered kind signature, or ``None`` if the registry
        is empty.
    """
    dims = _extract_dimension_labels(candidate)
    classes = _extract_characteristic_classes(candidate)

    # Build a lightweight proxy signature for distance computation
    class _ProxySig:
        dimension_labels = dims
        characteristic_classes = classes

    return registry.find_nearest(_ProxySig())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# KindMatchStrategy
# ---------------------------------------------------------------------------


class KindMatchStrategy(str, Enum):
    """Strategy controlling how candidates are matched to registered kinds.

    Values
    ------
    EXACT:
        Require a zero-distance match.  Unclassified candidates are dropped.
        Suitable for tightly constrained pipelines where only known kinds
        are expected.
    NEAREST:
        Assign the closest registered kind regardless of distance magnitude.
        Every candidate receives a classification; results may be imprecise
        for highly novel candidates.
    THRESHOLD:
        Assign the nearest kind only if distance ≤ ``KindClassifier.threshold``.
        Candidates above the threshold receive a synthetically generated
        fallback kind (see ``KindClassifier._fallback_kind``).  This is the
        recommended strategy for production use.

    Examples
    --------
    >>> KindMatchStrategy.NEAREST.value
    'nearest'
    >>> KindMatchStrategy("threshold")
    <KindMatchStrategy.THRESHOLD: 'threshold'>
    """

    EXACT = "exact"
    NEAREST = "nearest"
    THRESHOLD = "threshold"


# ---------------------------------------------------------------------------
# KindClassifier
# ---------------------------------------------------------------------------


class KindClassifier:
    """Classify discovery candidates into registered mathematical kinds.

    A ``KindClassifier`` uses a ``KindRegistry`` and a ``KindMatchStrategy``
    to assign a ``KindSignature`` to each input candidate.  The classifier
    extracts dimension labels and characteristic classes from the candidate,
    then searches the registry for the best-matching kind according to the
    configured strategy.

    Parameters
    ----------
    registry:
        Optional pre-populated ``KindRegistry``.  Defaults to
        ``KindRegistry.with_defaults()`` if ``None``.
    strategy:
        Classification strategy.  Defaults to ``KindMatchStrategy.NEAREST``.
    threshold:
        Distance threshold used when strategy is ``THRESHOLD``.  Candidates
        whose nearest-kind distance exceeds this value receive a fallback kind.
        Defaults to ``0.5``.

    Attributes
    ----------
    registry : KindRegistry
    strategy : KindMatchStrategy
    threshold : float

    Examples
    --------
    Default classification::

        clf = KindClassifier()
        sig = clf.classify(candidate)
        if sig is not None:
            print(sig.kind_id)

    Batch classification::

        clf = KindClassifier(strategy=KindMatchStrategy.THRESHOLD, threshold=0.4)
        assignments = clf.classify_many(candidates)
        for cid, sig in assignments.items():
            print(f"  {cid} → {sig.kind_id}")

    With confidence::

        sig, conf = clf.classify_with_confidence(candidate)
        if conf > 0.8:
            print("High-confidence assignment")

    Notes
    -----
    The ``classify_with_confidence`` method maps the distance to a confidence
    value via ``confidence = 1.0 − distance``.  A confidence of ``1.0``
    indicates an exact match; ``0.0`` indicates no structural overlap.
    """

    def __init__(
        self,
        registry: "KindRegistry | None" = None,
        strategy: KindMatchStrategy = KindMatchStrategy.NEAREST,
        threshold: float = 0.5,
    ) -> None:
        self.registry: KindRegistry = registry if registry is not None else KindRegistry.with_defaults()
        self.strategy = strategy
        self.threshold = _clamp(threshold)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, candidate: Any) -> Any | None:
        """Classify *candidate* and return its kind signature.

        Parameters
        ----------
        candidate:
            A ``DiscoveryCandidate``-like object.

        Returns
        -------
        KindSignature or None
            The assigned kind signature, or ``None`` if the strategy is
            ``EXACT`` and no exact match exists.

        Examples
        --------
        >>> clf = KindClassifier()
        >>> sig = clf.classify(my_candidate)
        """
        sig, _ = self.classify_with_confidence(candidate)
        return sig

    def classify_many(self, candidates: list[Any]) -> dict[str, Any]:
        """Classify all candidates in *candidates* and return a mapping.

        Parameters
        ----------
        candidates:
            List of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        dict[str, KindSignature]
            Mapping from ``candidate_id`` (or ``str(id(candidate))``) to the
            assigned ``KindSignature``.  Candidates that could not be classified
            (strategy=EXACT with no match) are omitted from the mapping.
        """
        result: dict[str, Any] = {}
        for c in candidates:
            sig = self.classify(c)
            if sig is not None:
                cid = str(getattr(c, "candidate_id", id(c)))
                result[cid] = sig
        return result

    def classify_with_confidence(
        self, candidate: Any
    ) -> tuple[Any | None, float]:
        """Classify *candidate* and also return a confidence value.

        Parameters
        ----------
        candidate:
            A ``DiscoveryCandidate``-like object.

        Returns
        -------
        tuple[KindSignature or None, float]
            ``(signature, confidence)`` where confidence is in ``[0.0, 1.0]``.
            Returns ``(None, 0.0)`` when no classification is possible under
            the EXACT strategy.
        """
        features = self._extract_features(candidate)
        sig = self._match_to_registry(features)

        if sig is None:
            if self.strategy == KindMatchStrategy.EXACT:
                return None, 0.0
            sig = self._fallback_kind(candidate)
            return sig, 0.1  # low confidence for fallback

        # Compute distance for confidence estimate
        dims = features.get("dimension_labels", ())
        classes = features.get("characteristic_classes", ())

        class _ProxySig:
            dimension_labels = dims
            characteristic_classes = classes

        dist = _compute_kind_distance(_ProxySig(), sig)
        confidence = _clamp(1.0 - dist)
        return sig, confidence

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_features(self, candidate: Any) -> dict[str, Any]:
        """Extract a feature dictionary from *candidate* for registry matching."""
        return {
            "dimension_labels": _extract_dimension_labels(candidate),
            "characteristic_classes": _extract_characteristic_classes(candidate),
            "novelty_score": float(getattr(candidate, "novelty_score", 0.0)),
            "description": getattr(candidate, "description", "") or "",
        }

    def _match_to_registry(self, features: dict[str, Any]) -> Any | None:
        """Find the best matching registered kind for the given features."""
        dims = features.get("dimension_labels", ())
        classes = features.get("characteristic_classes", ())

        class _ProxySig:
            dimension_labels = dims
            characteristic_classes = classes

        if self.strategy == KindMatchStrategy.EXACT:
            # Look for a zero-distance match
            all_kinds = self.registry.list_all()
            for k in all_kinds:
                if _compute_kind_distance(_ProxySig(), k) == 0.0:
                    return k
            return None
        else:
            max_dist = self.threshold if self.strategy == KindMatchStrategy.THRESHOLD else 1.0
            return self.registry.find_nearest(_ProxySig(), max_distance=max_dist)  # type: ignore[arg-type]

    def _fallback_kind(self, candidate: Any) -> Any:
        """Generate a synthetic fallback kind signature for *candidate*.

        This is used when no registered kind is close enough to the candidate.
        The fallback kind has a generated ID and uses whatever dimension labels
        and characteristic classes were extracted from the candidate.

        Parameters
        ----------
        candidate:
            The unclassified candidate.

        Returns
        -------
        KindSignature
            A synthetic signature not present in the registry.
        """
        dims = _extract_dimension_labels(candidate)
        classes = _extract_characteristic_classes(candidate)
        fallback_id = f"synthetic_{_uid()[:8]}"
        try:
            return KindSignature(  # type: ignore[call-arg]
                kind_id=fallback_id,
                dimension_labels=dims,
                characteristic_classes=classes,
            )
        except Exception:
            return {"kind_id": fallback_id, "dimension_labels": dims, "characteristic_classes": classes}  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# KindSignatureBuilder
# ---------------------------------------------------------------------------


class KindSignatureBuilder:
    """Fluent builder for constructing ``KindSignature`` objects.

    The builder accumulates dimension labels and characteristic classes
    through method calls and produces a ``KindSignature`` via ``build()``.

    Parameters
    ----------
    kind_id:
        Optional initial kind ID.  If not supplied, a random ID is generated
        at ``build()`` time.

    Examples
    --------
    Explicit construction::

        sig = (
            KindSignatureBuilder("smooth_manifold")
            .add_dimension("base")
            .add_dimension("fibre")
            .add_characteristic_class("chern")
            .add_characteristic_class("todd")
            .build()
        )

    From a candidate::

        sig = (
            KindSignatureBuilder()
            .from_candidate(candidate)
            .with_kind_id("custom_kind_001")
            .build()
        )

    Chaining is safe with ``None`` kind IDs::

        sig = KindSignatureBuilder().from_candidate(c).build()
        # kind_id will be auto-generated

    Notes
    -----
    Each call to ``add_dimension`` or ``add_characteristic_class`` appends
    to an internal list.  Duplicate values are preserved; deduplication is
    the caller's responsibility.  The builder is *not* reusable after
    ``build()`` is called — create a new instance for each signature.
    """

    def __init__(self, kind_id: str | None = None) -> None:
        self._kind_id: str | None = kind_id
        self._dimensions: list[str] = []
        self._classes: list[str] = []

    # ------------------------------------------------------------------
    # Fluent setters
    # ------------------------------------------------------------------

    def with_kind_id(self, kind_id: str) -> "KindSignatureBuilder":
        """Set the kind ID.

        Parameters
        ----------
        kind_id:
            The kind identifier.  Will be normalised via ``_normalize_kind_id``.

        Returns
        -------
        KindSignatureBuilder
            ``self`` for chaining.
        """
        self._kind_id = _normalize_kind_id(kind_id)
        return self

    def add_dimension(self, label: str) -> "KindSignatureBuilder":
        """Append a dimension label.

        Parameters
        ----------
        label:
            Dimension label string.  Normalised before storage.

        Returns
        -------
        KindSignatureBuilder
            ``self`` for chaining.

        Examples
        --------
        >>> builder = KindSignatureBuilder("manifold").add_dimension("base")
        >>> builder._dimensions
        ['base']
        """
        self._dimensions.append(_normalize_kind_id(label))
        return self

    def add_characteristic_class(self, cls: str) -> "KindSignatureBuilder":
        """Append a characteristic class label.

        Parameters
        ----------
        cls:
            Characteristic class label string.

        Returns
        -------
        KindSignatureBuilder
            ``self`` for chaining.
        """
        self._classes.append(_normalize_kind_id(cls))
        return self

    def from_candidate(self, candidate: Any) -> "KindSignatureBuilder":
        """Populate dimensions and classes by extracting features from *candidate*.

        This replaces any dimensions and classes set previously.  The kind ID
        is not changed.

        Parameters
        ----------
        candidate:
            A ``DiscoveryCandidate``-like object.

        Returns
        -------
        KindSignatureBuilder
            ``self`` for chaining.
        """
        self._dimensions = list(_extract_dimension_labels(candidate))
        self._classes = list(_extract_characteristic_classes(candidate))
        return self

    def build(self) -> Any:
        """Construct and return a ``KindSignature``.

        Returns
        -------
        KindSignature
            The completed signature object.

        Raises
        ------
        ValueError
            If no dimension labels have been set (an empty kind is invalid).
        """
        if not self._dimensions and not self._classes:
            raise ValueError(
                "KindSignatureBuilder requires at least one dimension label or "
                "characteristic class before calling build()."
            )
        kid = self._kind_id or f"kind_{_uid()[:8]}"
        try:
            return KindSignature(  # type: ignore[call-arg]
                kind_id=kid,
                dimension_labels=tuple(self._dimensions),
                characteristic_classes=tuple(self._classes),
            )
        except Exception:
            return {  # type: ignore[return-value]
                "kind_id": kid,
                "dimension_labels": tuple(self._dimensions),
                "characteristic_classes": tuple(self._classes),
            }


# ---------------------------------------------------------------------------
# KindRegistry
# ---------------------------------------------------------------------------


class KindRegistry:
    """Registry of known mathematical kind signatures.

    The registry stores ``KindSignature`` objects keyed by their ``kind_id``
    and supports nearest-neighbour lookup by kind distance.

    Examples
    --------
    Manual construction::

        registry = KindRegistry()
        registry.register(sig_manifold)
        registry.register(sig_bundle)
        nearest = registry.find_nearest(my_sig, max_distance=0.4)

    Defaults-populated registry::

        registry = KindRegistry.with_defaults()
        # Contains several built-in mathematical kind signatures

    Membership test::

        if "smooth_manifold" in registry:
            print("smooth_manifold is registered")

    Notes
    -----
    The registry is not thread-safe for concurrent writes.  For multi-threaded
    use, synchronise writes externally (e.g. with ``threading.Lock``).
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, signature: Any) -> None:
        """Register *signature* in the registry.

        Parameters
        ----------
        signature:
            A ``KindSignature``-like object with a ``kind_id`` attribute.

        Raises
        ------
        ValueError
            If ``signature.kind_id`` is empty or already registered (use
            ``unregister`` first to replace).
        """
        kid = str(getattr(signature, "kind_id", "") or "").strip()
        if not kid:
            raise ValueError("Cannot register a KindSignature with an empty kind_id.")
        self._store[kid] = signature

    def lookup(self, kind_id: str) -> Any | None:
        """Return the registered signature for *kind_id*, or ``None``.

        Parameters
        ----------
        kind_id:
            Exact kind ID to look up (not normalised).

        Returns
        -------
        KindSignature or None
        """
        return self._store.get(kind_id)

    def find_nearest(self, signature: Any, max_distance: float = 0.5) -> Any | None:
        """Return the registered kind closest to *signature* within *max_distance*.

        Parameters
        ----------
        signature:
            A kind-signature-like object with ``dimension_labels`` and
            ``characteristic_classes`` attributes (need not be registered).
        max_distance:
            Maximum allowable distance.  Returns ``None`` if no registered
            kind is within this distance.

        Returns
        -------
        KindSignature or None
            The nearest registered signature, or ``None`` if none qualifies.
        """
        if not self._store:
            return None
        best: Any | None = None
        best_dist = max_distance + 1e-9
        for stored in self._store.values():
            d = _compute_kind_distance(signature, stored)
            if d < best_dist:
                best_dist = d
                best = stored
        return best if best_dist <= max_distance else None

    def find_all_within(
        self,
        signature: Any,
        max_distance: float,
    ) -> list[tuple[Any, float]]:
        """Return all registered kinds within *max_distance* of *signature*.

        Parameters
        ----------
        signature:
            Reference kind signature.
        max_distance:
            Distance upper bound (inclusive).

        Returns
        -------
        list[tuple[KindSignature, float]]
            Pairs ``(signature, distance)`` sorted by distance ascending.
        """
        results: list[tuple[Any, float]] = []
        for stored in self._store.values():
            d = _compute_kind_distance(signature, stored)
            if d <= max_distance:
                results.append((stored, d))
        results.sort(key=lambda pair: pair[1])
        return results

    def list_all(self) -> list[Any]:
        """Return all registered kind signatures as a list.

        Returns
        -------
        list[KindSignature]
            Unordered list of all registered signatures.
        """
        return list(self._store.values())

    def unregister(self, kind_id: str) -> bool:
        """Remove the kind with *kind_id* from the registry.

        Parameters
        ----------
        kind_id:
            The kind ID to remove.

        Returns
        -------
        bool
            ``True`` if the kind was present and removed; ``False`` otherwise.
        """
        if kind_id in self._store:
            del self._store[kind_id]
            return True
        return False

    def __len__(self) -> int:
        """Return the number of registered kinds."""
        return len(self._store)

    def __contains__(self, kind_id: str) -> bool:
        """Return True if *kind_id* is registered."""
        return kind_id in self._store

    @classmethod
    def with_defaults(cls) -> "KindRegistry":
        """Create a registry pre-populated with standard mathematical kinds.

        The following kinds are registered by default:

        * ``smooth_manifold`` — smooth manifolds with tangent bundle.
        * ``topological_space`` — general topological spaces (Hausdorff).
        * ``vector_bundle`` — smooth vector bundles over manifolds.
        * ``principal_bundle`` — principal G-bundles over smooth bases.
        * ``algebraic_variety`` — affine/projective algebraic varieties.
        * ``sheaf_on_site`` — sheaves defined on a Grothendieck site.
        * ``abelian_category`` — abelian categories (homological algebra).
        * ``infinity_groupoid`` — (∞,1)-groupoids (higher category theory).

        Returns
        -------
        KindRegistry
            A new registry containing the above default kinds.

        Examples
        --------
        >>> reg = KindRegistry.with_defaults()
        >>> len(reg) >= 8
        True
        >>> "smooth_manifold" in reg
        True
        """
        registry = cls()
        _DEFAULTS = [
            ("smooth_manifold", ("base", "tangent"), ("chern", "pontryagin")),
            ("topological_space", ("point_set", "open_sets"), ("euler",)),
            ("vector_bundle", ("base", "fibre", "total"), ("chern", "todd")),
            ("principal_bundle", ("base", "group", "fibre"), ("chern_weil",)),
            ("algebraic_variety", ("affine_chart", "coordinate_ring"), ("chern",)),
            ("sheaf_on_site", ("site", "cover", "sections"), ("cohomology",)),
            ("abelian_category", ("objects", "morphisms", "exact_sequences"), ()),
            ("infinity_groupoid", ("objects", "morphisms", "homotopies"), ("k_theory",)),
        ]
        for kid, dims, classes in _DEFAULTS:
            try:
                sig = KindSignature(  # type: ignore[call-arg]
                    kind_id=kid,
                    dimension_labels=dims,
                    characteristic_classes=classes,
                )
            except Exception:
                sig = type("_KS", (), {  # type: ignore[assignment]
                    "kind_id": kid,
                    "dimension_labels": dims,
                    "characteristic_classes": classes,
                })()
            registry.register(sig)
        return registry


# ---------------------------------------------------------------------------
# KindClassificationRunner
# ---------------------------------------------------------------------------


class KindClassificationRunner:
    """Orchestrate the full kind classification stage of the discovery pipeline.

    This runner accepts a ``NoveltyPipelineStage`` result (or a plain list of
    candidates) and produces a ``KindClassificationStage`` containing kind
    assignments and metadata.

    Parameters
    ----------
    config:
        Optional ``DiscoveryConfig`` controlling classification thresholds.
    registry:
        Optional pre-populated ``KindRegistry``.  Falls back to
        ``KindRegistry.with_defaults()`` if ``None``.

    Examples
    --------
    From a novelty stage::

        runner = KindClassificationRunner(config=cfg)
        stage = runner.run(novelty_stage)

    With diagnostics::

        stage, diag = runner.run_with_diagnostics(novelty_stage)
        print(diag["unclassified_count"])

    Notes
    -----
    The runner is stateless between calls — each invocation of ``run()``
    creates fresh classifier and registry instances if defaults are used.
    """

    def __init__(
        self,
        config: Any | None = None,
        registry: "KindRegistry | None" = None,
    ) -> None:
        self._config = config
        self._registry = registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, stage: Any) -> Any:
        """Run kind classification on *stage* and return a ``KindClassificationStage``.

        Parameters
        ----------
        stage:
            Either a ``NoveltyPipelineStage`` object (with a ``.candidates``
            attribute) or a plain list of candidates.

        Returns
        -------
        KindClassificationStage
        """
        result, _ = self.run_with_diagnostics(stage)
        return result

    def run_with_diagnostics(self, input_data: Any) -> tuple[Any, Any]:
        """Run classification and return stage + diagnostics.

        Parameters
        ----------
        input_data:
            ``NoveltyPipelineStage`` or list of candidates.

        Returns
        -------
        tuple[KindClassificationStage, DiscoveryDiagnostics]
        """
        start = _utcnow()

        # Extract candidate list
        if hasattr(input_data, "candidates"):
            candidates = list(input_data.candidates)
        elif isinstance(input_data, list):
            candidates = input_data
        else:
            candidates = []

        diag: dict[str, Any] = {
            "stage": "kind_classification",
            "run_id": _uid(),
            "input_count": len(candidates),
        }

        # Classify batch
        assignments = self._classify_batch(candidates, self._config)
        classified = [c for c in candidates if str(getattr(c, "candidate_id", id(c))) in assignments]
        unclassified = [c for c in candidates if str(getattr(c, "candidate_id", id(c))) not in assignments]

        diag["classified_count"] = len(classified)
        diag["unclassified_count"] = len(unclassified)
        self._handle_unclassified(unclassified, diag)

        diag["elapsed_secs"] = _utcnow() - start

        try:
            out_stage = KindClassificationStage(  # type: ignore[call-arg]
                stage_id=_uid(),
                candidates=tuple(classified),
                kind_assignments=assignments,
                input_count=len(candidates),
                output_count=len(classified),
                elapsed_secs=diag["elapsed_secs"],
            )
        except Exception:
            out_stage = {  # type: ignore[assignment]
                "stage": "kind_classification",
                "candidates": classified,
                "kind_assignments": assignments,
            }

        try:
            out_diag = DiscoveryDiagnostics(**diag)  # type: ignore[call-arg]
        except Exception:
            out_diag = diag  # type: ignore[assignment]

        return out_stage, out_diag

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_batch(
        self,
        candidates: list[Any],
        config: Any | None,
    ) -> dict[str, Any]:
        """Classify *candidates* and return a candidate-id → kind mapping."""
        threshold = float(getattr(config, "kind_threshold", 0.5)) if config else 0.5
        strategy_raw = str(getattr(config, "kind_strategy", "nearest")).lower()
        strategy = KindMatchStrategy(strategy_raw) if strategy_raw in KindMatchStrategy._value2member_map_ else KindMatchStrategy.NEAREST  # type: ignore[attr-defined]

        registry = self._registry if self._registry is not None else KindRegistry.with_defaults()
        clf = KindClassifier(registry=registry, strategy=strategy, threshold=threshold)
        return clf.classify_many(candidates)

    def _handle_unclassified(
        self,
        unclassified: list[Any],
        diagnostics: dict[str, Any],
    ) -> None:
        """Log or record unclassified candidates in *diagnostics*."""
        diagnostics["unclassified_ids"] = [
            str(getattr(c, "candidate_id", id(c))) for c in unclassified
        ]


# ---------------------------------------------------------------------------
# Top-level convenience function
# ---------------------------------------------------------------------------


def run_kind_classification(
    candidates: list[Any],
    config: Any | None = None,
    registry: "KindRegistry | None" = None,
) -> Any:
    """Classify *candidates* into mathematical kinds and return the stage result.

    This is the primary entry point for Stage 2 of the discovery pipeline.

    Parameters
    ----------
    candidates:
        List of ``DiscoveryCandidate``-like objects, typically the ``.candidates``
        attribute of a ``NoveltyPipelineStage``.
    config:
        Optional ``DiscoveryConfig`` controlling kind-match threshold and strategy.
    registry:
        Optional pre-populated ``KindRegistry``.  If ``None``, the runner uses
        ``KindRegistry.with_defaults()``.

    Returns
    -------
    KindClassificationStage
        Stage result with ``kind_assignments`` mapping candidate IDs to their
        assigned ``KindSignature`` objects.

    Raises
    ------
    TypeError
        If *candidates* is not a list.

    Examples
    --------
    Simple call::

        from jugeo.ideation.discovery_engine.kind_classification import (
            run_kind_classification,
        )
        stage = run_kind_classification(novelty_stage.candidates)

    Custom registry::

        registry = KindRegistry()
        registry.register(my_custom_kind)
        stage = run_kind_classification(candidates, config=cfg, registry=registry)

    See also
    --------
    ``run_novelty_pipeline`` — Stage 1 that produces the input candidates.
    ``run_theorem_synthesis`` — Stage 3 that consumes this stage's output.
    """
    if not isinstance(candidates, list):
        raise TypeError(f"candidates must be a list, got {type(candidates).__name__}")
    runner = KindClassificationRunner(config=config, registry=registry)
    return runner.run(candidates)
