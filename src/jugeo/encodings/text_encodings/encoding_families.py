"""
Encoding families – subword, char, word, and embedding-based text encodings.

# copilot: Encoding families – subword, char, word, and embedding-based text encodings

This module implements families of text encodings used in Jugeo's Judgment Geometry
framework.  Different encoding families carve the text space differently:

  - **Char-level** encodings (``CharEncoding``) provide high coverage — every
    Unicode character is handled — but individual tokens carry little semantics.
    The open cover of the text sheaf is maximally fine-grained.

  - **Subword** encodings (``SubwordEncoding``, BPE / WordPiece) create a
    middle ground: better OOV handling than word-level, richer semantics than
    char-level.  The open cover is adaptive to corpus statistics.

  - **Word-level** encodings capture high semantics per token but suffer from
    poor out-of-vocabulary (OOV) handling on unseen or morphologically complex
    forms.

  - **Embedding-based** encodings (``EmbeddingEncoding``) map tokens into a
    dense metric space; the choice of metric (cosine, dot, L2) determines the
    geometry of the resulting sheaf.

In Judgment Geometry, a Judgment is a tuple

    J = (c, φ, A, E, O, B, T, Π)

where *c* is the claim, *φ* is the framing, *A* is the agent, *E* is the
evidence tuple, *O* is the obstruction class in Čech H¹, *B* is the belief
state, *T* is the TrustTier (an ordered algebra element), and *Π* is the
prior distribution.

The choice of encoding family determines which open cover ``𝒰`` we use when
building the text sheaf ``ℱ``.  The Čech complex ``Č(𝒰, ℱ)`` — and hence the
obstruction cohomology class *O* ∈ H¹ — depends critically on this choice:

  - Fine covers (char-level) give a denser Čech complex with more potential
    cocycles.
  - Coarse covers (word-level) collapse neighbouring intersections, potentially
    missing fine-grained obstructions.
  - Subword covers balance expressiveness against computational cost.

TrustTier forms an ordered algebra (ℤ/n, ≤) where higher tiers require
strictly stronger evidence to advance a claim.  The ``EncodingSelector``
enforces a minimum trust level before committing to a higher-semantic encoding.

Obstructions are computed as Čech H¹ classes: a 1-cocycle on the nerve of the
cover that fails to be a coboundary witnesses a genuine inconsistency in the
evidence assignment.
"""

from __future__ import annotations

import math
import hashlib
import itertools
import collections
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Graceful fallback: jugeo core imports
# ---------------------------------------------------------------------------
try:
    from jugeo.core.trust import TrustTier  # type: ignore
    from jugeo.core.judgment import Judgment  # type: ignore
    _JUGEO_AVAILABLE = True
except Exception:  # pragma: no cover
    _JUGEO_AVAILABLE = False

    class TrustTier:  # type: ignore  # noqa: D101
        """Stub TrustTier when jugeo.core is not installed."""

        LEVELS: Tuple[str, ...] = ("UNTRUSTED", "LOW", "MEDIUM", "HIGH", "VERIFIED")

        def __init__(self, level: int = 0) -> None:
            self.level = max(0, min(level, len(self.LEVELS) - 1))

        def __le__(self, other: "TrustTier") -> bool:  # noqa: D105
            return self.level <= other.level

        def __lt__(self, other: "TrustTier") -> bool:  # noqa: D105
            return self.level < other.level

        def __repr__(self) -> str:  # noqa: D105
            return f"TrustTier({self.LEVELS[self.level]})"

    class Judgment:  # type: ignore  # noqa: D101
        """Stub Judgment tuple (c, φ, A, E, O, B, T, Π)."""

        def __init__(
            self,
            claim: str = "",
            framing: str = "",
            agent: str = "",
            evidence: Tuple[Any, ...] = (),
            obstruction: Any = None,
            belief: float = 0.5,
            trust: Optional[TrustTier] = None,
            prior: Optional[Dict[str, float]] = None,
        ) -> None:
            self.claim = claim
            self.framing = framing
            self.agent = agent
            self.evidence = evidence
            self.obstruction = obstruction
            self.belief = belief
            self.trust = trust or TrustTier(0)
            self.prior = prior or {}

        def __repr__(self) -> str:  # noqa: D105
            return (
                f"Judgment(claim={self.claim!r}, trust={self.trust!r}, "
                f"belief={self.belief:.3f})"
            )

try:
    from jugeo.encodings.base import BaseEncoding  # type: ignore
    _BASE_ENCODING_AVAILABLE = True
except Exception:  # pragma: no cover
    _BASE_ENCODING_AVAILABLE = False

    class BaseEncoding:  # type: ignore  # noqa: D101
        """Stub base class when jugeo.encodings.base is absent."""

        def encode(self, text: str) -> List[int]:  # noqa: D102
            return []

        def decode(self, ids: List[int]) -> str:  # noqa: D102
            return ""


# ---------------------------------------------------------------------------
# Granularity constants
# ---------------------------------------------------------------------------

GRANULARITY_CHAR = "CHAR"
GRANULARITY_SUBWORD = "SUBWORD"
GRANULARITY_WORD = "WORD"
GRANULARITY_EMBEDDING = "EMBEDDING"

_VALID_GRANULARITIES = frozenset(
    {GRANULARITY_CHAR, GRANULARITY_SUBWORD, GRANULARITY_WORD, GRANULARITY_EMBEDDING}
)

_VALID_METRICS = frozenset({"cosine", "dot", "l2"})


# ---------------------------------------------------------------------------
# Core frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EncodingMember:
    """One specific encoding within a :class:`TextEncodingFamily`.

    Attributes
    ----------
    member_id:
        Unique identifier for this encoding member (e.g. ``"bpe-32k"``).
    name:
        Human-readable display name.
    vocab_size:
        Number of tokens in the vocabulary.
    dim:
        Dimensionality of the embedding space (0 for non-embedding encodings).
    algorithm:
        Short algorithm tag, e.g. ``"BPE"``, ``"WordPiece"``, ``"Unigram"``,
        ``"GloVe"``, ``"fastText"``.
    """

    member_id: str
    name: str
    vocab_size: int
    dim: int
    algorithm: str

    def __post_init__(self) -> None:
        if self.vocab_size < 0:
            raise ValueError(f"vocab_size must be non-negative, got {self.vocab_size}")
        if self.dim < 0:
            raise ValueError(f"dim must be non-negative, got {self.dim}")


@dataclass(frozen=True)
class TextEncodingFamily:
    """A family of related text encodings sharing the same granularity level.

    In Judgment Geometry this corresponds to a choice of open cover 𝒰 for the
    text sheaf.  Members of the same family share the same Čech complex
    topology but may differ in vocabulary size or training corpus.

    Attributes
    ----------
    family_id:
        Unique identifier for this family (e.g. ``"subword-bpe"``).
    name:
        Human-readable name (e.g. ``"BPE Subword Family"``).
    members:
        Frozen tuple of :class:`EncodingMember` instances belonging to the
        family.
    granularity:
        One of ``"CHAR"``, ``"SUBWORD"``, ``"WORD"``, or ``"EMBEDDING"``.
    trust_level:
        Minimum :class:`TrustTier` level required to employ this family.
        Higher trust is demanded for semantically richer encodings.
    """

    family_id: str
    name: str
    members: Tuple[EncodingMember, ...]
    granularity: str
    trust_level: int

    def __post_init__(self) -> None:
        if self.granularity not in _VALID_GRANULARITIES:
            raise ValueError(
                f"granularity must be one of {_VALID_GRANULARITIES}, "
                f"got {self.granularity!r}"
            )
        if self.trust_level < 0:
            raise ValueError(f"trust_level must be ≥ 0, got {self.trust_level}")

    # ------------------------------------------------------------------
    # Convenience helpers (non-mutating)
    # ------------------------------------------------------------------

    def member_by_id(self, member_id: str) -> Optional[EncodingMember]:
        """Return the member with the given *member_id*, or ``None``."""
        for m in self.members:
            if m.member_id == member_id:
                return m
        return None

    def largest_vocab_member(self) -> Optional[EncodingMember]:
        """Return the member with the largest vocabulary, or ``None`` if empty."""
        if not self.members:
            return None
        return max(self.members, key=lambda m: m.vocab_size)

    def smallest_vocab_member(self) -> Optional[EncodingMember]:
        """Return the member with the smallest vocabulary, or ``None`` if empty."""
        if not self.members:
            return None
        return min(self.members, key=lambda m: m.vocab_size)


@dataclass(frozen=True)
class SubwordEncoding:
    """A BPE or WordPiece subword encoding.

    Subword encodings represent a key compromise in the coverage vs. semantics
    trade-off.  BPE iteratively merges frequent byte pairs; WordPiece instead
    maximises the language-model likelihood of the merged token.

    In sheaf terms, each subword token corresponds to an open set in the cover
    of the corpus manifold.  Overlaps between tokens at morpheme boundaries
    give rise to intersection data, and a consistent assignment of local
    sections defines a global section (i.e. a coherent parse of the text).
    An obstruction in Čech H¹ witnesses a *morphological inconsistency*: no
    global tokenisation exists that respects all local assignments simultaneously.

    Attributes
    ----------
    encoding_id:
        Unique identifier.
    vocab:
        Frozen tuple of vocabulary tokens in insertion order.
    merges:
        Sequence of BPE merge rules ``(left, right)`` defining the merge order.
    unk_token:
        Token used for out-of-vocabulary items.
    max_length:
        Maximum sequence length in tokens.
    """

    encoding_id: str
    vocab: Tuple[str, ...]
    merges: Tuple[Tuple[str, str], ...]
    unk_token: str
    max_length: int

    def __post_init__(self) -> None:
        if self.max_length <= 0:
            raise ValueError(f"max_length must be positive, got {self.max_length}")

    # ------------------------------------------------------------------

    def vocab_index(self) -> Dict[str, int]:
        """Return a mapping from token string to integer index."""
        return {tok: idx for idx, tok in enumerate(self.vocab)}

    def tokenise(self, text: str) -> List[str]:
        """Naïve character-split tokeniser (reference implementation).

        This is *not* a full BPE implementation — it simply splits the text
        into characters, applies known vocab membership, and falls back to
        ``unk_token`` for missing characters.  A production system would run
        the merge rules iteratively.
        """
        idx = self.vocab_index()
        tokens: List[str] = []
        for ch in text:
            tokens.append(ch if ch in idx else self.unk_token)
        return tokens[: self.max_length]

    def tokenise_ids(self, text: str) -> List[int]:
        """Return integer token IDs for *text*."""
        idx = self.vocab_index()
        unk_id = idx.get(self.unk_token, 0)
        return [idx.get(tok, unk_id) for tok in self.tokenise(text)]


@dataclass(frozen=True)
class CharEncoding:
    """A character-level encoding.

    Character-level encodings offer maximal coverage: every code-point in
    ``charset`` is handled directly, and OOV is limited to characters not
    present in the charset.  However, individual characters carry minimal
    semantic content, so longer context windows are typically required.

    In sheaf terms the open cover consists of single-character intervals on
    the string, giving the finest possible Čech complex.  Every 1-cocycle
    trivially cobounds because the nerve of a fine enough cover is
    contractible — meaning char-level encodings have *zero* obstruction in
    the limit, at the cost of high computational complexity.

    Attributes
    ----------
    encoding_id:
        Unique identifier.
    charset:
        Frozenset of characters handled by this encoding.
    pad_char:
        Padding character used to fill sequences to a fixed length.
    unk_char:
        Replacement for characters outside ``charset``.
    case_sensitive:
        Whether the encoding distinguishes upper- and lower-case letters.
    """

    encoding_id: str
    charset: frozenset  # frozenset[str]
    pad_char: str
    unk_char: str
    case_sensitive: bool

    # ------------------------------------------------------------------

    def normalise(self, text: str) -> str:
        """Return *text* after case normalisation (if not case-sensitive)."""
        return text if self.case_sensitive else text.lower()

    def encode_text(self, text: str) -> List[int]:
        """Encode *text* into a list of integer character codes.

        Characters in ``charset`` are mapped to their ordinal; characters
        outside ``charset`` are replaced by the ordinal of ``unk_char``.
        """
        normalised = self.normalise(text)
        unk_ord = ord(self.unk_char)
        result: List[int] = []
        for ch in normalised:
            if ch in self.charset:
                result.append(ord(ch))
            else:
                result.append(unk_ord)
        return result

    def coverage(self, text: str) -> float:
        """Return the fraction of *text*'s characters present in ``charset``."""
        normalised = self.normalise(text)
        if not normalised:
            return 1.0
        covered = sum(1 for ch in normalised if ch in self.charset)
        return covered / len(normalised)


@dataclass(frozen=True)
class EmbeddingEncoding:
    """A dense vector embedding encoding.

    Embedding encodings map tokens into a continuous metric space ℝ^d.  The
    choice of metric determines the geometry of that space:

    - ``"cosine"``: direction matters, not magnitude; popular for semantic
      similarity tasks (e.g. sentence transformers, word2vec).
    - ``"dot"``: magnitude-sensitive inner product; used in transformer
      attention and recommendation systems.
    - ``"l2"``: Euclidean distance; natural for clustering and nearest-neighbour
      retrieval.

    In Judgment Geometry, an embedding encoding induces a *continuous* sheaf
    over the text manifold.  The Čech H¹ obstruction measures whether local
    embeddings (computed in overlapping context windows) can be coherently
    glued into a global embedding.  Contextual models (``is_contextual=True``)
    make this gluing non-trivial because the same token has different local
    sections depending on its neighbourhood.

    Attributes
    ----------
    encoding_id:
        Unique identifier.
    dim:
        Embedding dimensionality.
    metric:
        One of ``"cosine"``, ``"dot"``, or ``"l2"``.
    vocab_size:
        Number of tokens in the underlying discrete vocabulary.
    is_contextual:
        ``True`` for contextual models (BERT, GPT-family) where the embedding
        of a token depends on its context; ``False`` for static models
        (word2vec, GloVe).
    """

    encoding_id: str
    dim: int
    metric: str
    vocab_size: int
    is_contextual: bool

    def __post_init__(self) -> None:
        if self.metric not in _VALID_METRICS:
            raise ValueError(
                f"metric must be one of {_VALID_METRICS}, got {self.metric!r}"
            )
        if self.dim <= 0:
            raise ValueError(f"dim must be positive, got {self.dim}")
        if self.vocab_size < 0:
            raise ValueError(f"vocab_size must be non-negative, got {self.vocab_size}")

    def zero_vector(self) -> Tuple[float, ...]:
        """Return a zero vector of the appropriate dimensionality."""
        return tuple(0.0 for _ in range(self.dim))

    def random_unit_vector(self, seed: int = 0) -> Tuple[float, ...]:
        """Return a deterministic pseudo-random unit vector (for testing)."""
        import random as _random
        rng = _random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(self.dim)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return tuple(v / norm for v in raw)


@dataclass(frozen=True)
class SelectionCriterion:
    """One criterion used by an :class:`EncodingSelector` when choosing a family.

    Criteria are combined as a weighted sum over normalised property scores.
    The *comparator* determines how the criterion interacts with candidate
    family properties.

    Attributes
    ----------
    criterion_id:
        Unique identifier for this criterion.
    property_name:
        The property of a :class:`TextEncodingFamily` to evaluate, e.g.
        ``"granularity"``, ``"trust_level"``, ``"vocab_size"``.
    weight:
        Non-negative importance weight for this criterion in the overall score.
    comparator:
        Comparison operator string: ``"eq"``, ``"ne"``, ``"lt"``, ``"le"``,
        ``"gt"``, ``"ge"``.
    """

    criterion_id: str
    property_name: str
    weight: float
    comparator: str

    def __post_init__(self) -> None:
        if self.weight < 0.0:
            raise ValueError(f"weight must be ≥ 0, got {self.weight}")
        valid_comparators = {"eq", "ne", "lt", "le", "gt", "ge"}
        if self.comparator not in valid_comparators:
            raise ValueError(
                f"comparator must be one of {valid_comparators}, "
                f"got {self.comparator!r}"
            )


@dataclass(frozen=True)
class EncodingSelector:
    """Selects an appropriate :class:`TextEncodingFamily` for a given task.

    The selector evaluates each candidate family against its ordered list of
    :class:`SelectionCriterion` objects, computes a weighted score, and returns
    the family with the highest score that also satisfies the minimum
    ``trust_required`` level.

    In Judgment Geometry the selector corresponds to a *sheaf morphism*: it
    maps the task context (framing φ) to a choice of cover 𝒰, which in turn
    determines which Čech complex we use to compute obstructions.

    Attributes
    ----------
    selector_id:
        Unique identifier.
    criteria:
        Ordered tuple of selection criteria.
    default_family:
        ``family_id`` of the family to use when no candidate passes all
        filters.
    trust_required:
        Minimum TrustTier level required before the selector will recommend
        any family above the default.
    """

    selector_id: str
    criteria: Tuple[SelectionCriterion, ...]
    default_family: str
    trust_required: int

    def __post_init__(self) -> None:
        if self.trust_required < 0:
            raise ValueError(
                f"trust_required must be ≥ 0, got {self.trust_required}"
            )


@dataclass(frozen=True)
class CrossEncodingAlignment:
    """An alignment between two different :class:`TextEncodingFamily` objects.

    When translating evidence across encoding families — for example when
    merging char-level features with subword-level features — we need an
    explicit alignment map.  This alignment is itself a *morphism of sheaves*:
    it defines how local sections in the source cover 𝒰₁ map to local sections
    in the target cover 𝒰₂.

    The confidence score measures how well the mapping preserves the Čech
    cohomology: a confidence of 1.0 means the mapping is a quasi-isomorphism
    (it induces an isomorphism on H¹), while lower confidence indicates that
    some obstruction classes may be created or destroyed by the translation.

    Attributes
    ----------
    alignment_id:
        Unique identifier.
    source_family:
        ``family_id`` of the source :class:`TextEncodingFamily`.
    target_family:
        ``family_id`` of the target :class:`TextEncodingFamily`.
    mapping:
        Frozen tuple of ``(source_token, target_token)`` pairs.
    confidence:
        Alignment confidence in [0, 1].
    """

    alignment_id: str
    source_family: str
    target_family: str
    mapping: Tuple[Tuple[str, str], ...]
    confidence: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )

    def source_tokens(self) -> Tuple[str, ...]:
        """Return the tuple of source tokens in the alignment."""
        return tuple(src for src, _ in self.mapping)

    def target_tokens(self) -> Tuple[str, ...]:
        """Return the tuple of target tokens in the alignment."""
        return tuple(tgt for _, tgt in self.mapping)

    def as_dict(self) -> Dict[str, str]:
        """Return the mapping as a plain dictionary ``{source: target}``."""
        return dict(self.mapping)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def build_encoding_family(
    name: str,
    granularity: str,
    members: Sequence[EncodingMember],
    *,
    family_id: Optional[str] = None,
    trust_level: int = 0,
) -> TextEncodingFamily:
    """Construct a :class:`TextEncodingFamily` from a list of members.

    Different encoding families carve the text space differently — subword
    encodings create a middle ground between char-level (high coverage, low
    semantics) and word-level (high semantics, poor OOV handling).  In Judgment
    Geometry, the choice of encoding family determines which open cover we use
    for the text sheaf, and hence which Čech complex we compute.

    Parameters
    ----------
    name:
        Human-readable family name.
    granularity:
        One of ``"CHAR"``, ``"SUBWORD"``, ``"WORD"``, ``"EMBEDDING"``.
    members:
        Sequence of :class:`EncodingMember` instances to include.
    family_id:
        Optional explicit family ID.  If omitted, a stable hash of *name* is
        used.
    trust_level:
        Minimum TrustTier level required to employ this family.

    Returns
    -------
    TextEncodingFamily
        A frozen dataclass instance representing the family.

    Raises
    ------
    ValueError
        If *granularity* is not a recognised value or *trust_level* < 0.

    Examples
    --------
    >>> m = EncodingMember("bpe-32k", "BPE 32k", 32000, 0, "BPE")
    >>> fam = build_encoding_family("BPE Subword", "SUBWORD", [m])
    >>> fam.granularity
    'SUBWORD'
    """
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            f"granularity must be one of {_VALID_GRANULARITIES}, "
            f"got {granularity!r}"
        )
    if trust_level < 0:
        raise ValueError(f"trust_level must be ≥ 0, got {trust_level}")

    if family_id is None:
        # Derive a stable, human-readable ID from the name and granularity.
        raw = f"{name}::{granularity}".encode("utf-8")
        short_hash = hashlib.sha1(raw).hexdigest()[:8]
        family_id = f"{granularity.lower()}-{short_hash}"

    frozen_members: Tuple[EncodingMember, ...] = tuple(members)
    return TextEncodingFamily(
        family_id=family_id,
        name=name,
        members=frozen_members,
        granularity=granularity,
        trust_level=trust_level,
    )


def select_encoding(
    text: str,
    selector: EncodingSelector,
    task_hint: str,
    candidates: Optional[Sequence[TextEncodingFamily]] = None,
) -> Optional[TextEncodingFamily]:
    """Select the most appropriate :class:`TextEncodingFamily` for *text* and *task_hint*.

    Different encoding families carve the text space differently — subword
    encodings create a middle ground between char-level (high coverage, low
    semantics) and word-level (high semantics, poor OOV handling).  In Judgment
    Geometry, the choice of encoding family determines which open cover we use
    for the text sheaf, and hence which Čech complex we compute.

    The selection algorithm:

    1. Filter candidates to those whose ``trust_level`` ≤ ``selector.trust_required``.
    2. Score each remaining candidate against each :class:`SelectionCriterion`.
    3. Return the highest-scoring candidate, or the family whose ``family_id``
       matches ``selector.default_family`` if no candidate passes.

    Parameters
    ----------
    text:
        The input text that will be encoded.
    selector:
        The :class:`EncodingSelector` containing the selection criteria.
    task_hint:
        A short string hinting at the downstream task, e.g. ``"classification"``,
        ``"retrieval"``, ``"generation"``.  Used to bias scoring.
    candidates:
        Optional explicit list of :class:`TextEncodingFamily` candidates.  If
        omitted a small built-in set of default families is used.

    Returns
    -------
    TextEncodingFamily or None
        The selected family, or ``None`` if no candidate is available.
    """
    if candidates is None:
        candidates = _default_candidate_families()

    # Step 1: trust filter
    eligible = [
        fam for fam in candidates
        if fam.trust_level <= selector.trust_required
    ]
    if not eligible:
        # Fall back to any family matching the default ID
        for fam in candidates:
            if fam.family_id == selector.default_family:
                return fam
        return None

    # Step 2: score each eligible family
    task_hint_lower = task_hint.lower()
    scores: Dict[str, float] = {}

    for fam in eligible:
        score = 0.0
        for criterion in selector.criteria:
            prop_val = getattr(fam, criterion.property_name, None)
            if prop_val is None:
                continue

            # Task-hint biases: retrieval / similarity benefits from embeddings
            if task_hint_lower in ("retrieval", "similarity", "semantic"):
                if fam.granularity == GRANULARITY_EMBEDDING:
                    score += criterion.weight * 2.0
                    continue
            # Generation / translation benefits from subword
            if task_hint_lower in ("generation", "translation", "summarisation"):
                if fam.granularity == GRANULARITY_SUBWORD:
                    score += criterion.weight * 1.5
                    continue
            # Classification is neutral; prefer subword over char
            if task_hint_lower == "classification":
                if fam.granularity in (GRANULARITY_SUBWORD, GRANULARITY_WORD):
                    score += criterion.weight * 1.2
                    continue

            # Generic scoring: reward lower trust_level (simpler is more general)
            if criterion.property_name == "trust_level":
                score += criterion.weight * max(0, 5 - fam.trust_level)
            elif criterion.property_name == "granularity":
                # Prefer subword by default
                granularity_scores = {
                    GRANULARITY_SUBWORD: 4,
                    GRANULARITY_WORD: 3,
                    GRANULARITY_EMBEDDING: 2,
                    GRANULARITY_CHAR: 1,
                }
                score += criterion.weight * granularity_scores.get(
                    fam.granularity, 0
                )
            else:
                score += criterion.weight

        scores[fam.family_id] = score

    if not scores:
        return None

    best_id = max(scores, key=lambda fid: scores[fid])
    for fam in eligible:
        if fam.family_id == best_id:
            return fam
    return None


def encode_with_subword(
    text: str,
    encoding: SubwordEncoding,
    *,
    add_special_tokens: bool = False,
) -> List[int]:
    """Encode *text* using a :class:`SubwordEncoding`.

    In Judgment Geometry, the tokenisation produced here corresponds to a
    choice of *local section* on the text sheaf: each subword token is an open
    set in the cover, and the sequence of token IDs is the section over the
    text string.

    Parameters
    ----------
    text:
        Input text to tokenise.
    encoding:
        The :class:`SubwordEncoding` to use.
    add_special_tokens:
        If ``True``, prepend a ``[CLS]``-style start token (id=1) and append
        a ``[SEP]``-style end token (id=2).  These are conventional in
        transformer pre-training but may be omitted for other tasks.

    Returns
    -------
    list[int]
        Sequence of integer token IDs, truncated to ``encoding.max_length``.
    """
    ids = encoding.tokenise_ids(text)
    if add_special_tokens:
        ids = [1] + ids + [2]
        ids = ids[: encoding.max_length]
    return ids


def encode_with_chars(
    text: str,
    encoding: CharEncoding,
    *,
    pad_to: Optional[int] = None,
) -> List[int]:
    """Encode *text* using a :class:`CharEncoding`.

    Char-level encoding gives a maximally fine-grained open cover: each
    character in the text is its own local section.  This yields zero Čech H¹
    obstruction in the limit because the nerve of sufficiently fine covers is
    contractible, but at the cost of very long sequences.

    Parameters
    ----------
    text:
        Input text.
    encoding:
        The :class:`CharEncoding` to apply.
    pad_to:
        If specified, pad the output to this length using the ordinal of
        ``encoding.pad_char``.

    Returns
    -------
    list[int]
        Sequence of integer character codes.
    """
    ids = encoding.encode_text(text)
    if pad_to is not None and len(ids) < pad_to:
        pad_id = ord(encoding.pad_char)
        ids = ids + [pad_id] * (pad_to - len(ids))
    return ids


def align_encoding_families(
    fam1: TextEncodingFamily,
    fam2: TextEncodingFamily,
    *,
    alignment_id: Optional[str] = None,
    reference_vocab: Optional[Sequence[str]] = None,
) -> CrossEncodingAlignment:
    """Compute an alignment between two :class:`TextEncodingFamily` objects.

    The alignment is built by matching members with the same ``algorithm``
    attribute across the two families, and if no algorithmic match is found,
    by pairing members positionally.  The confidence score is computed as:

        confidence = (matched_pairs / max(|fam1.members|, |fam2.members|))^0.5

    This square-root penalty means that an alignment covering only half the
    members has confidence ≈ 0.71, reflecting the non-trivial sheaf morphism
    quality.

    Parameters
    ----------
    fam1:
        Source encoding family.
    fam2:
        Target encoding family.
    alignment_id:
        Optional explicit alignment ID.  Derived from a hash if omitted.
    reference_vocab:
        Optional sequence of reference vocabulary items used to bias token
        pairing toward semantically equivalent pairs.

    Returns
    -------
    CrossEncodingAlignment
        A frozen alignment instance.
    """
    if alignment_id is None:
        raw = f"{fam1.family_id}::{fam2.family_id}".encode("utf-8")
        alignment_id = "align-" + hashlib.sha1(raw).hexdigest()[:10]

    # Build algorithm → member index for each family
    algo_to_m1: Dict[str, EncodingMember] = {}
    for m in fam1.members:
        algo_to_m1.setdefault(m.algorithm, m)

    algo_to_m2: Dict[str, EncodingMember] = {}
    for m in fam2.members:
        algo_to_m2.setdefault(m.algorithm, m)

    mapping_pairs: List[Tuple[str, str]] = []

    # 1. Algorithm-matched pairs
    matched_algos = set(algo_to_m1.keys()) & set(algo_to_m2.keys())
    for algo in sorted(matched_algos):
        m1 = algo_to_m1[algo]
        m2 = algo_to_m2[algo]
        mapping_pairs.append((m1.member_id, m2.member_id))

    # 2. Positional fallback for unmatched members
    unmatched_m1 = [
        m for m in fam1.members if m.algorithm not in matched_algos
    ]
    unmatched_m2 = [
        m for m in fam2.members if m.algorithm not in matched_algos
    ]
    for m1, m2 in zip(unmatched_m1, unmatched_m2):
        mapping_pairs.append((m1.member_id, m2.member_id))

    # If reference_vocab provided, annotate pairs with a token-level alignment
    if reference_vocab:
        ref_pairs = list(
            zip(reference_vocab[::2], reference_vocab[1::2])
        )
        mapping_pairs.extend(
            (str(a), str(b)) for a, b in ref_pairs[:10]
        )

    n_possible = max(len(fam1.members), len(fam2.members), 1)
    n_matched = len(matched_algos)
    confidence = math.sqrt(n_matched / n_possible) if n_matched > 0 else 0.0
    confidence = min(confidence, 1.0)

    return CrossEncodingAlignment(
        alignment_id=alignment_id,
        source_family=fam1.family_id,
        target_family=fam2.family_id,
        mapping=tuple(mapping_pairs),
        confidence=round(confidence, 6),
    )


def cross_encoding_comparison(
    text: str,
    fam1: TextEncodingFamily,
    fam2: TextEncodingFamily,
) -> Dict[str, Any]:
    """Compare how two encoding families represent *text*.

    Different encoding families carve the text space differently — subword
    encodings create a middle ground between char-level (high coverage, low
    semantics) and word-level (high semantics, poor OOV handling).  In Judgment
    Geometry, the choice of encoding family determines which open cover we use
    for the text sheaf, and hence which Čech complex we compute.

    This function computes:

    - Token count under each family's dominant member.
    - Character-level coverage under each family.
    - Estimated Čech complexity (number of non-empty intersections in the nerve).
    - A raw alignment between the two families' vocabularies.
    - A divergence score measuring how differently the two families segment *text*.

    Parameters
    ----------
    text:
        Input text to compare.
    fam1:
        First encoding family.
    fam2:
        Second encoding family.

    Returns
    -------
    dict
        A dictionary with keys:
        ``family1_id``, ``family2_id``, ``text_length``,
        ``fam1_token_count``, ``fam2_token_count``,
        ``fam1_cech_complexity``, ``fam2_cech_complexity``,
        ``alignment``, ``divergence``.
    """
    text_len = len(text)

    # Estimate token counts based on granularity heuristics
    def _estimate_tokens(fam: TextEncodingFamily) -> int:
        if fam.granularity == GRANULARITY_CHAR:
            return text_len
        elif fam.granularity == GRANULARITY_SUBWORD:
            # Typical subword compression ratio: ~4 chars per token
            return max(1, text_len // 4)
        elif fam.granularity == GRANULARITY_WORD:
            return max(1, len(text.split()))
        elif fam.granularity == GRANULARITY_EMBEDDING:
            # Embedding models typically use subword tokenisation internally
            return max(1, text_len // 4)
        return text_len

    count1 = _estimate_tokens(fam1)
    count2 = _estimate_tokens(fam2)

    # Čech complexity: number of non-empty pairwise intersections in the nerve.
    # For a cover of n tokens, the nerve has C(n, 2) potential 1-simplices.
    # We estimate the fraction that are non-empty using overlap probability.
    def _cech_complexity(n_tokens: int, granularity: str) -> int:
        if n_tokens <= 1:
            return 0
        if granularity == GRANULARITY_CHAR:
            # Adjacent-only overlaps
            return n_tokens - 1
        elif granularity == GRANULARITY_SUBWORD:
            # Subword tokens share boundaries; estimate ~30% of pairs overlap
            return int(math.comb(min(n_tokens, 20), 2) * 0.3)
        elif granularity == GRANULARITY_WORD:
            # Minimal overlaps (compound words, multi-word expressions)
            return max(0, n_tokens // 5)
        elif granularity == GRANULARITY_EMBEDDING:
            # Dense vector space: every token potentially neighbours others
            return int(math.comb(min(n_tokens, 15), 2) * 0.8)
        return 0

    cech1 = _cech_complexity(count1, fam1.granularity)
    cech2 = _cech_complexity(count2, fam2.granularity)

    # Alignment between the two families
    alignment = align_encoding_families(fam1, fam2)

    # Divergence: normalised difference in token count, weighted by Čech gap
    if count1 + count2 == 0:
        divergence = 0.0
    else:
        token_divergence = abs(count1 - count2) / (count1 + count2)
        cech_divergence = abs(cech1 - cech2) / (max(cech1, cech2) + 1)
        divergence = round(0.6 * token_divergence + 0.4 * cech_divergence, 6)

    return {
        "family1_id": fam1.family_id,
        "family2_id": fam2.family_id,
        "text_length": text_len,
        "fam1_token_count": count1,
        "fam2_token_count": count2,
        "fam1_cech_complexity": cech1,
        "fam2_cech_complexity": cech2,
        "alignment": alignment,
        "divergence": divergence,
    }


def encoding_coverage_score(
    text: str,
    encoding: CharEncoding,
) -> float:
    """Compute the fraction of *text* characters covered by *encoding*'s charset.

    Coverage is a key quality metric for char-level encodings.  A coverage of
    1.0 means every character in *text* is in the charset, so no ``unk_char``
    substitutions will occur; a coverage below 0.9 typically indicates that the
    encoding is not well matched to the text's script or language.

    In Judgment Geometry, low coverage corresponds to a *sparse* open cover:
    many characters fall outside every open set, creating genuine holes in the
    text sheaf.  These holes manifest as non-trivial obstruction classes in
    Čech H¹.

    Parameters
    ----------
    text:
        Input text whose coverage is to be measured.
    encoding:
        The :class:`CharEncoding` to evaluate.

    Returns
    -------
    float
        Coverage score in [0, 1].
    """
    if not text:
        return 1.0
    normalised = encoding.normalise(text)
    covered = sum(1 for ch in normalised if ch in encoding.charset)
    return covered / len(normalised)


def compute_encoding_entropy(
    text: str,
    encoding: CharEncoding,
) -> float:
    """Compute the Shannon entropy of the character distribution in *text* under *encoding*.

    Entropy measures how evenly the encoding's tokens are used to represent
    *text*.  A high entropy indicates the text makes diverse use of the
    charset; a low entropy suggests the text is repetitive or that the charset
    is mismatched to the language.

    In Judgment Geometry, entropy is a proxy for the *complexity* of the local
    sections: high-entropy texts produce more varied local sections on the
    sheaf, which increases the chance of non-trivial 1-cocycles (obstructions).

    Formally, given token frequencies ``f_i`` with ``Σ f_i = N``, the entropy
    is:

        H = -Σ (f_i / N) log₂(f_i / N)

    Parameters
    ----------
    text:
        Input text.
    encoding:
        The :class:`CharEncoding` whose token set to use.

    Returns
    -------
    float
        Shannon entropy in bits (log base 2).
    """
    if not text:
        return 0.0
    normalised = encoding.normalise(text)
    counts: Dict[str, int] = collections.Counter(normalised)  # type: ignore[assignment]
    total = sum(counts.values())
    entropy = 0.0
    for ch, cnt in counts.items():
        if ch not in encoding.charset:
            ch = encoding.unk_char
        p = cnt / total
        if p > 0.0:
            entropy -= p * math.log2(p)
    return round(entropy, 6)


# ---------------------------------------------------------------------------
# Internal helpers / default factories
# ---------------------------------------------------------------------------


def _default_char_encoding() -> CharEncoding:
    """Return a basic ASCII + common punctuation CharEncoding."""
    ascii_charset = frozenset(chr(i) for i in range(32, 127))
    return CharEncoding(
        encoding_id="char-ascii",
        charset=ascii_charset,
        pad_char="\x00",
        unk_char="\ufffd",
        case_sensitive=False,
    )


def _default_subword_encoding() -> SubwordEncoding:
    """Return a minimal BPE SubwordEncoding for smoke-testing."""
    base_chars = tuple(chr(i) for i in range(32, 127))
    common_merges: Tuple[Tuple[str, str], ...] = (
        ("t", "h"),
        ("th", "e"),
        ("i", "n"),
        ("in", "g"),
        ("o", "n"),
        ("a", "n"),
        ("e", "r"),
        ("er", "s"),
    )
    return SubwordEncoding(
        encoding_id="subword-bpe-mini",
        vocab=base_chars + ("th", "the", "in", "ing", "on", "an", "er", "ers"),
        merges=common_merges,
        unk_token="\ufffd",
        max_length=512,
    )


def _default_embedding_encoding() -> EmbeddingEncoding:
    """Return a stub 128-dim cosine EmbeddingEncoding."""
    return EmbeddingEncoding(
        encoding_id="emb-128-cosine",
        dim=128,
        metric="cosine",
        vocab_size=30000,
        is_contextual=False,
    )


def _default_candidate_families() -> List[TextEncodingFamily]:
    """Build a small set of default candidate families for :func:`select_encoding`."""
    char_member = EncodingMember(
        member_id="char-ascii-m",
        name="ASCII Char Member",
        vocab_size=95,
        dim=0,
        algorithm="CharSplit",
    )
    subword_member_bpe = EncodingMember(
        member_id="bpe-8k",
        name="BPE 8k",
        vocab_size=8000,
        dim=0,
        algorithm="BPE",
    )
    subword_member_wp = EncodingMember(
        member_id="wordpiece-32k",
        name="WordPiece 32k",
        vocab_size=32000,
        dim=0,
        algorithm="WordPiece",
    )
    word_member = EncodingMember(
        member_id="word-100k",
        name="Word 100k",
        vocab_size=100000,
        dim=0,
        algorithm="WhitespaceSplit",
    )
    emb_member = EncodingMember(
        member_id="glove-300",
        name="GloVe 300d",
        vocab_size=400000,
        dim=300,
        algorithm="GloVe",
    )

    char_family = TextEncodingFamily(
        family_id="char-default",
        name="Default Char Family",
        members=(char_member,),
        granularity=GRANULARITY_CHAR,
        trust_level=0,
    )
    subword_family = TextEncodingFamily(
        family_id="subword-default",
        name="Default Subword Family",
        members=(subword_member_bpe, subword_member_wp),
        granularity=GRANULARITY_SUBWORD,
        trust_level=1,
    )
    word_family = TextEncodingFamily(
        family_id="word-default",
        name="Default Word Family",
        members=(word_member,),
        granularity=GRANULARITY_WORD,
        trust_level=1,
    )
    emb_family = TextEncodingFamily(
        family_id="emb-default",
        name="Default Embedding Family",
        members=(emb_member,),
        granularity=GRANULARITY_EMBEDDING,
        trust_level=2,
    )
    return [char_family, subword_family, word_family, emb_family]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 72)
    print("encoding_families.py — smoke test")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 1. Build encoding families
    # ------------------------------------------------------------------
    print("\n[1] Building encoding families...")

    bpe_member = EncodingMember(
        member_id="bpe-16k",
        name="BPE 16k vocab",
        vocab_size=16_000,
        dim=0,
        algorithm="BPE",
    )
    wp_member = EncodingMember(
        member_id="wp-32k",
        name="WordPiece 32k vocab",
        vocab_size=32_000,
        dim=0,
        algorithm="WordPiece",
    )
    subword_family = build_encoding_family(
        name="Subword BPE/WP Family",
        granularity=GRANULARITY_SUBWORD,
        members=[bpe_member, wp_member],
        trust_level=1,
    )
    print(f"  Subword family: {subword_family.family_id!r}  "
          f"members={len(subword_family.members)}")

    char_member = EncodingMember(
        member_id="char-unicode",
        name="Unicode Char",
        vocab_size=65536,
        dim=0,
        algorithm="CharSplit",
    )
    char_family = build_encoding_family(
        name="Unicode Char Family",
        granularity=GRANULARITY_CHAR,
        members=[char_member],
        trust_level=0,
    )
    print(f"  Char family:    {char_family.family_id!r}  "
          f"members={len(char_family.members)}")

    bert_member = EncodingMember(
        member_id="bert-base",
        name="BERT Base 768d",
        vocab_size=30_522,
        dim=768,
        algorithm="WordPiece",
    )
    emb_family = build_encoding_family(
        name="BERT Contextual Embedding Family",
        granularity=GRANULARITY_EMBEDDING,
        members=[bert_member],
        trust_level=2,
    )
    print(f"  Embedding family: {emb_family.family_id!r}  "
          f"members={len(emb_family.members)}")

    # ------------------------------------------------------------------
    # 2. Construct encodings
    # ------------------------------------------------------------------
    print("\n[2] Constructing concrete encodings...")

    char_enc = _default_char_encoding()
    print(f"  CharEncoding id={char_enc.encoding_id!r}  "
          f"charset_size={len(char_enc.charset)}  "
          f"case_sensitive={char_enc.case_sensitive}")

    subword_enc = _default_subword_encoding()
    print(f"  SubwordEncoding id={subword_enc.encoding_id!r}  "
          f"vocab_size={len(subword_enc.vocab)}  "
          f"merges={len(subword_enc.merges)}")

    emb_enc = _default_embedding_encoding()
    print(f"  EmbeddingEncoding id={emb_enc.encoding_id!r}  "
          f"dim={emb_enc.dim}  metric={emb_enc.metric!r}  "
          f"contextual={emb_enc.is_contextual}")

    # ------------------------------------------------------------------
    # 3. Encode sample text
    # ------------------------------------------------------------------
    sample = "The quick brown fox jumps over the lazy dog."
    print(f"\n[3] Encoding sample text: {sample!r}")

    char_ids = encode_with_chars(sample, char_enc, pad_to=64)
    print(f"  char IDs (first 10): {char_ids[:10]}  total={len(char_ids)}")

    subword_ids = encode_with_subword(sample, subword_enc, add_special_tokens=True)
    print(f"  subword IDs (first 10): {subword_ids[:10]}  total={len(subword_ids)}")

    # ------------------------------------------------------------------
    # 4. Coverage and entropy
    # ------------------------------------------------------------------
    print("\n[4] Coverage and entropy metrics...")

    cov = encoding_coverage_score(sample, char_enc)
    print(f"  Coverage (ASCII): {cov:.4f}")

    ent = compute_encoding_entropy(sample, char_enc)
    print(f"  Shannon entropy:  {ent:.4f} bits")

    multilang = "Héllo wörld — こんにちは — 你好"
    cov_ml = encoding_coverage_score(multilang, char_enc)
    ent_ml = compute_encoding_entropy(multilang, char_enc)
    print(f"  Multi-lang coverage: {cov_ml:.4f}  entropy: {ent_ml:.4f} bits")

    # ------------------------------------------------------------------
    # 5. Select encoding via EncodingSelector
    # ------------------------------------------------------------------
    print("\n[5] Selecting encodings via EncodingSelector...")

    criteria = (
        SelectionCriterion(
            criterion_id="crit-granularity",
            property_name="granularity",
            weight=2.0,
            comparator="eq",
        ),
        SelectionCriterion(
            criterion_id="crit-trust",
            property_name="trust_level",
            weight=1.0,
            comparator="le",
        ),
    )
    selector = EncodingSelector(
        selector_id="sel-001",
        criteria=criteria,
        default_family="char-default",
        trust_required=2,
    )

    for task in ("classification", "retrieval", "generation"):
        selected = select_encoding(sample, selector, task_hint=task)
        if selected:
            print(f"  task={task!r:15s} → family={selected.family_id!r}  "
                  f"granularity={selected.granularity}")
        else:
            print(f"  task={task!r:15s} → no family selected")

    # ------------------------------------------------------------------
    # 6. Cross-encoding comparison
    # ------------------------------------------------------------------
    print("\n[6] Cross-encoding comparison...")

    comparison = cross_encoding_comparison(sample, char_family, subword_family)
    print(f"  text_length={comparison['text_length']}")
    print(f"  char   tokens={comparison['fam1_token_count']}  "
          f"čech={comparison['fam1_cech_complexity']}")
    print(f"  subword tokens={comparison['fam2_token_count']}  "
          f"čech={comparison['fam2_cech_complexity']}")
    print(f"  alignment confidence={comparison['alignment'].confidence:.4f}")
    print(f"  divergence={comparison['divergence']:.4f}")

    comparison2 = cross_encoding_comparison(sample, subword_family, emb_family)
    print(f"\n  subword vs embedding:")
    print(f"  subword tokens={comparison2['fam1_token_count']}  "
          f"čech={comparison2['fam1_cech_complexity']}")
    print(f"  embedding tokens={comparison2['fam2_token_count']}  "
          f"čech={comparison2['fam2_cech_complexity']}")
    print(f"  divergence={comparison2['divergence']:.4f}")

    # ------------------------------------------------------------------
    # 7. Explicit cross-family alignment
    # ------------------------------------------------------------------
    print("\n[7] Align encoding families...")

    alignment = align_encoding_families(char_family, subword_family)
    print(f"  alignment_id={alignment.alignment_id!r}")
    print(f"  {alignment.source_family!r} → {alignment.target_family!r}")
    print(f"  pairs={len(alignment.mapping)}  confidence={alignment.confidence:.4f}")

    alignment2 = align_encoding_families(subword_family, emb_family)
    print(f"  subword→embedding: pairs={len(alignment2.mapping)}  "
          f"confidence={alignment2.confidence:.4f}")

    # ------------------------------------------------------------------
    # 8. Judgment Geometry stub
    # ------------------------------------------------------------------
    print("\n[8] Judgment Geometry integration (stub)...")

    trust = TrustTier(2)
    judgment = Judgment(
        claim="The encoding family is appropriate for this task.",
        framing="encoding_selection",
        agent="smoke_test",
        evidence=(sample, selected),
        obstruction=None,
        belief=0.85,
        trust=trust,
        prior={"subword": 0.6, "char": 0.2, "embedding": 0.2},
    )
    print(f"  Judgment: {judgment!r}")
    print(f"  TrustTier: {trust!r}")
    print(f"  jugeo available: {_JUGEO_AVAILABLE}")

    # ------------------------------------------------------------------
    # 9. EmbeddingEncoding helpers
    # ------------------------------------------------------------------
    print("\n[9] EmbeddingEncoding helpers...")
    zero_vec = emb_enc.zero_vector()
    print(f"  zero_vector dim={len(zero_vec)}  first5={list(zero_vec[:5])}")
    unit_vec = emb_enc.random_unit_vector(seed=42)
    norm = math.sqrt(sum(v * v for v in unit_vec))
    print(f"  unit_vector dim={len(unit_vec)}  ‖v‖={norm:.6f}")

    # ------------------------------------------------------------------
    # 10. Family member lookups
    # ------------------------------------------------------------------
    print("\n[10] Family member lookups...")
    largest = subword_family.largest_vocab_member()
    smallest = subword_family.smallest_vocab_member()
    print(f"  largest vocab member: {largest.name if largest else 'none'}  "
          f"({largest.vocab_size if largest else 0} tokens)")
    print(f"  smallest vocab member: {smallest.name if smallest else 'none'}  "
          f"({smallest.vocab_size if smallest else 0} tokens)")
    found = subword_family.member_by_id("bpe-16k")
    print(f"  member_by_id('bpe-16k'): {found.name if found else 'not found'}")

    print("\n" + "=" * 72)
    print("Smoke test PASSED ✓")
    print("=" * 72)
