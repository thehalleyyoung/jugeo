"""
# copilot: Text deserves its own structure – sheaf-theoretic encoding of tokenized text

Text is not merely a flat sequence of bytes.  It is a presheaf on the interval
[0, N] where N = len(tokens): every open sub-interval U ⊆ [0, N] gets an
assignment of tokens, embeddings, and local semantics.  Consistency of this
presheaf—the condition that restrictions from overlapping windows agree on
their shared tokens—is precisely the sheaf condition.

When that condition fails, Čech cohomology detects the obstruction.
H¹ = 0 means no global inconsistency; H¹ ≠ 0 reveals a genuine semantic
conflict such as coreference ambiguity, scope inversion, or discourse
incoherence.  This module turns those abstract ideas into concrete data
structures and algorithms.

Theory recap
------------
A *judgment* in jugeo is a tuple  (c, φ, A, E, O, B, T, Π)  where

  c  – claim / proposition
  φ  – formula / encoding of c
  A  – agent / author
  E  – evidence base
  O  – ontological commitment
  B  – background theory
  T  – trust tier  (ordered algebra, see TrustTier below)
  Π  – proof / justification token

A *TrustTier* is an element of a totally-ordered monoid  (ℤ, +, ≤).  Trust
propagates through restriction maps: if section s₁ has trust t₁ and its
restriction to s₁ ∩ s₂ is compatible with s₂, the glued section inherits
min(t₁, t₂).

Obstructions live in Čech cohomology H¹(𝒰, ℱ) for the cover 𝒰 and the
sheaf of "consistent token assignments" ℱ.  A non-trivial cocycle witnesses
the impossibility of gluing local sections into a global one.

Usage
-----
>>> enc = encode_text_as_sheaf("The cat sat on the mat.", section_size=3)
>>> check_sheaf_consistency(enc)
True
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import string
import textwrap
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional jugeo imports with graceful fallback stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.core.judgment import Judgment  # type: ignore
    from jugeo.core.trust import TrustTier  # type: ignore
    from jugeo.encodings.base import BaseEncoding  # type: ignore
    _JUGEO_AVAILABLE = True
except ImportError:  # pragma: no cover – stubs used when jugeo not installed
    _JUGEO_AVAILABLE = False

    class Judgment:  # type: ignore
        """Stub: jugeo.core.judgment.Judgment."""

        def __init__(self, claim="", formula="", agent="", evidence=(),
                     ontology="", background="", trust=0, proof=""):
            self.claim = claim
            self.formula = formula
            self.agent = agent
            self.evidence = evidence
            self.ontology = ontology
            self.background = background
            self.trust = trust
            self.proof = proof

        def as_tuple(self):
            return (self.claim, self.formula, self.agent, self.evidence,
                    self.ontology, self.background, self.trust, self.proof)

    class TrustTier:  # type: ignore
        """Stub: ordered-algebra TrustTier  (ℤ, +, ≤)."""

        def __init__(self, level: int = 0):
            self.level = int(level)

        # Ordered-algebra operations
        def __add__(self, other: "TrustTier") -> "TrustTier":
            return TrustTier(self.level + other.level)

        def __le__(self, other: "TrustTier") -> bool:
            return self.level <= other.level

        def __lt__(self, other: "TrustTier") -> bool:
            return self.level < other.level

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, TrustTier):
                return NotImplemented
            return self.level == other.level

        def __repr__(self) -> str:
            return f"TrustTier({self.level})"

        @classmethod
        def meet(cls, a: "TrustTier", b: "TrustTier") -> "TrustTier":
            """Lattice meet = min trust."""
            return cls(min(a.level, b.level))

        @classmethod
        def join(cls, a: "TrustTier", b: "TrustTier") -> "TrustTier":
            """Lattice join = max trust."""
            return cls(max(a.level, b.level))

    class BaseEncoding:  # type: ignore
        """Stub: jugeo.encodings.base.BaseEncoding."""
        pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODULE_VERSION = "0.1.0"
DEFAULT_SECTION_SIZE = 5          # tokens per section window
DEFAULT_OVERLAP = 2               # token overlap between adjacent sections
DEFAULT_ENCODING_NAME = "simple-whitespace"
_PUNCT_RE = re.compile(r"([" + re.escape(string.punctuation) + r"])")


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenizedText:
    """Text split into tokens with absolute character offsets.

    The *offset* for token i is a half-open interval [start, end) measured in
    characters from the beginning of the source string.  This lets us map
    token indices back to spans in the original text.

    Attributes
    ----------
    text_id : str
        Stable identifier (SHA-256 prefix of the source text).
    tokens : tuple[str, ...]
        Ordered sequence of surface-form tokens.
    offsets : tuple[tuple[int, int], ...]
        Parallel sequence of (start, end) character offsets.
    vocab_size : int
        Number of unique token types observed.
    encoding_name : str
        Name of the tokenisation strategy used.
    """

    text_id: str
    tokens: Tuple[str, ...]
    offsets: Tuple[Tuple[int, int], ...]
    vocab_size: int
    encoding_name: str

    def __post_init__(self) -> None:
        if len(self.tokens) != len(self.offsets):
            raise ValueError(
                f"tokens ({len(self.tokens)}) and offsets ({len(self.offsets)}) "
                "must have the same length."
            )

    @property
    def n_tokens(self) -> int:
        """Total number of tokens (including repetitions)."""
        return len(self.tokens)

    def window(self, start: int, end: int) -> "TokenizedText":
        """Return a sub-sequence TokenizedText for token indices [start, end)."""
        sliced_tokens = self.tokens[start:end]
        sliced_offsets = self.offsets[start:end]
        new_id = f"{self.text_id}[{start}:{end}]"
        new_vocab = len(set(sliced_tokens))
        return TokenizedText(
            text_id=new_id,
            tokens=sliced_tokens,
            offsets=sliced_offsets,
            vocab_size=new_vocab,
            encoding_name=self.encoding_name,
        )


@dataclass(frozen=True)
class TextSection:
    """A local section of the text sheaf over one open set U ⊆ [0, N].

    In sheaf language, ℱ(U) is the set of consistent token assignments over
    the open interval U.  A TextSection holds one such element together with
    its trust annotation.

    Attributes
    ----------
    section_id : str
        Unique identifier for this section.
    open_set : str
        Human-readable label for the open interval, e.g. ``"[3, 8)"``.
    local_data : str
        The text content of this window (reconstructed from tokens).
    token_ids : tuple[int, ...]
        Token *indices* (positions in the source TokenizedText) covered.
    trust_level : int
        TrustTier level for this section's data (higher = more reliable).
    """

    section_id: str
    open_set: str
    local_data: str
    token_ids: Tuple[int, ...]
    trust_level: int

    @property
    def span_start(self) -> int:
        """First token index covered by this section."""
        return min(self.token_ids) if self.token_ids else 0

    @property
    def span_end(self) -> int:
        """One-past the last token index covered by this section."""
        return max(self.token_ids) + 1 if self.token_ids else 0

    @property
    def trust(self) -> TrustTier:
        """Trust as a TrustTier algebra element."""
        return TrustTier(self.trust_level)


@dataclass(frozen=True)
class TokenObservation:
    """A single probabilistic observation of a token in its local context.

    Token probability is assigned by any language model or frequency count
    available at ingestion time.  When no model is available it defaults to
    the uniform probability 1 / vocab_size.

    Attributes
    ----------
    obs_id : str
        Unique observation identifier.
    token : str
        Surface form of the observed token.
    position : int
        Absolute position (index) in the TokenizedText.
    context_left : str
        Concatenated tokens to the left of this position (up to window).
    context_right : str
        Concatenated tokens to the right of this position (up to window).
    probability : float
        P(token | context)  ∈ (0, 1].
    trust : int
        TrustTier level of the source that produced this probability estimate.
    """

    obs_id: str
    token: str
    position: int
    context_left: str
    context_right: str
    probability: float
    trust: int

    def __post_init__(self) -> None:
        if not (0.0 < self.probability <= 1.0):
            raise ValueError(
                f"probability must be in (0, 1]; got {self.probability}"
            )

    @property
    def surprisal(self) -> float:
        """Shannon surprisal  -log₂ P(token | context) in bits."""
        return -math.log2(self.probability)

    @property
    def trust_tier(self) -> TrustTier:
        return TrustTier(self.trust)


@dataclass(frozen=True)
class TextRestriction:
    """Restriction map ρ_{UV} : ℱ(U) → ℱ(V) for V ⊆ U.

    A restriction selects the tokens in V from those assigned to U.  For a
    genuine sheaf the restriction maps must satisfy the *cocycle condition*:
    for W ⊆ V ⊆ U,  ρ_{UW} = ρ_{VW} ∘ ρ_{UV}.

    The ``mapping`` field encodes ρ as a list of (source_token_id,
    target_token_id) pairs—both expressed as absolute positions.

    Attributes
    ----------
    restriction_id : str
        Unique identifier.
    source_section : str
        section_id of U (the larger open set).
    target_section : str
        section_id of V (the smaller open set, V ⊆ U).
    mapping : tuple[tuple[int, int], ...]
        Pairs (pos_in_U, pos_in_V) recording which source token maps to
        which target token.
    is_compatible : bool
        True when the restriction is compatible with an adjacent section's
        restriction (i.e., the cocycle condition holds locally).
    """

    restriction_id: str
    source_section: str
    target_section: str
    mapping: Tuple[Tuple[int, int], ...]
    is_compatible: bool

    @property
    def n_mapped_tokens(self) -> int:
        return len(self.mapping)

    def as_dict(self) -> Dict[int, int]:
        """Return mapping as a plain dict {source_pos: target_pos}."""
        return dict(self.mapping)


@dataclass(frozen=True)
class TextCovering:
    """An open cover 𝒰 = {U_α} of the text interval [0, N].

    The cover is *good* when every pairwise intersection U_α ∩ U_β is either
    empty or contractible (a sub-interval).  Under this assumption Čech
    cohomology H^n(𝒰, ℱ) converges to the sheaf cohomology H^n([0,N], ℱ).

    Attributes
    ----------
    cover_id : str
        Unique identifier for this covering.
    open_sets : tuple[str, ...]
        Labels of the open sets U_α in the covering.
    sections : tuple[TextSection, ...]
        The local sections ℱ(U_α) for each U_α.
    """

    cover_id: str
    open_sets: Tuple[str, ...]
    sections: Tuple[TextSection, ...]

    def __post_init__(self) -> None:
        if len(self.open_sets) != len(self.sections):
            raise ValueError(
                "open_sets and sections must have the same length; "
                f"got {len(self.open_sets)} vs {len(self.sections)}."
            )

    @property
    def n_sets(self) -> int:
        return len(self.open_sets)


@dataclass(frozen=True)
class TextSheaf:
    """The text sheaf ℱ over the base space [0, N].

    Collecting all local sections and their restriction maps gives a
    (pre)sheaf.  The ``is_consistent`` flag records whether the gluing axiom
    has been verified: for every pair of sections whose open sets overlap, the
    restrictions to the intersection agree.

    Attributes
    ----------
    sheaf_id : str
        Unique identifier.
    base_space : str
        Description of the base topological space (e.g. ``"[0, 23]"``).
    sections : tuple[TextSection, ...]
        All local sections.
    restrictions : tuple[TextRestriction, ...]
        All restriction maps between pairs of overlapping sections.
    is_consistent : bool
        True iff every restriction pair satisfies the cocycle condition.
    """

    sheaf_id: str
    base_space: str
    sections: Tuple[TextSection, ...]
    restrictions: Tuple[TextRestriction, ...]
    is_consistent: bool

    @property
    def n_sections(self) -> int:
        return len(self.sections)

    @property
    def n_restrictions(self) -> int:
        return len(self.restrictions)

    def section_by_id(self, section_id: str) -> Optional[TextSection]:
        for s in self.sections:
            if s.section_id == section_id:
                return s
        return None


@dataclass(frozen=True)
class TextEncoding:
    """Full sheaf-theoretic encoding of a text document.

    This is the top-level object produced by :func:`encode_text_as_sheaf`.
    It bundles the source text, its tokenisation, the covering, and the
    resulting sheaf into a single immutable record.

    Attributes
    ----------
    encoding_id : str
        Stable, deterministic identifier derived from source content.
    source_text : str
        The original input string.
    tokens : tuple[str, ...]
        Flat token sequence (same as ``sheaf``'s sections combined).
    sections : tuple[TextSection, ...]
        The sections of the sheaf (mirrored from ``sheaf.sections``).
    sheaf : TextSheaf
        The full sheaf object including restriction maps.
    trust_level : int
        Overall trust level (meet of all section trust levels).
    """

    encoding_id: str
    source_text: str
    tokens: Tuple[str, ...]
    sections: Tuple[TextSection, ...]
    sheaf: TextSheaf
    trust_level: int

    @property
    def is_consistent(self) -> bool:
        """Delegates to the underlying sheaf."""
        return self.sheaf.is_consistent

    @property
    def n_tokens(self) -> int:
        return len(self.tokens)

    @property
    def n_sections(self) -> int:
        return len(self.sections)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha_prefix(text: str, length: int = 12) -> str:
    """Return the first ``length`` hex characters of SHA-256(text)."""
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _simple_tokenize(text: str) -> List[Tuple[str, int, int]]:
    """Whitespace-and-punctuation tokeniser.

    Returns a list of (token, start, end) triples where start/end are
    character offsets into *text*.  Punctuation characters are split off as
    their own tokens.
    """
    # Insert spaces around punctuation so we can split uniformly.
    spaced = _PUNCT_RE.sub(r" \1 ", text)
    tokens_with_offsets: List[Tuple[str, int, int]] = []
    # Walk the spaced string, but track offsets back into the *original* text.
    # We use a simple re-alignment by scanning the original string.
    pos_orig = 0
    for raw_tok in spaced.split():
        if not raw_tok:
            continue
        # Find this token in the original text starting from pos_orig.
        idx = text.find(raw_tok, pos_orig)
        if idx == -1:
            # Punctuation may have been altered; just advance.
            idx = pos_orig
        end = idx + len(raw_tok)
        tokens_with_offsets.append((raw_tok, idx, end))
        pos_orig = end
    return tokens_with_offsets


def _build_tokenized_text(text: str, encoding_name: str) -> TokenizedText:
    """Build a :class:`TokenizedText` from a raw string."""
    raw = _simple_tokenize(text)
    tokens = tuple(t for t, _, _ in raw)
    offsets = tuple((s, e) for _, s, e in raw)
    vocab_size = len(set(tokens))
    text_id = _sha_prefix(text)
    return TokenizedText(
        text_id=text_id,
        tokens=tokens,
        offsets=offsets,
        vocab_size=vocab_size,
        encoding_name=encoding_name,
    )


def _section_id(text_id: str, start: int, end: int) -> str:
    return f"sec:{text_id}[{start}:{end}]"


def _restriction_id(src_id: str, tgt_id: str) -> str:
    return f"restr:{src_id}->{tgt_id}"


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def tokenize_to_sections(
    text: str,
    section_size: int = DEFAULT_SECTION_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    encoding_name: str = DEFAULT_ENCODING_NAME,
    trust_level: int = 5,
) -> Tuple[TokenizedText, Tuple[TextSection, ...]]:
    """Tokenise *text* and partition it into overlapping :class:`TextSection` windows.

    The open sets U_α of the covering are sliding windows of *section_size*
    tokens with *overlap* tokens shared between adjacent windows.  This gives
    a cover of the token interval [0, N) where N = total number of tokens.

    Overlap is crucial: without it, adjacent sections have empty intersection
    and cannot be glued.  With overlap ≥ 1, every consecutive pair U_i, U_{i+1}
    shares at least one token, making their intersection non-empty.

    Parameters
    ----------
    text : str
        Raw input text.
    section_size : int
        Number of tokens per window (default: 5).
    overlap : int
        Number of tokens shared between consecutive windows (default: 2).
    encoding_name : str
        Name of the tokenisation strategy.
    trust_level : int
        Initial trust level assigned to every section.

    Returns
    -------
    tokenized : TokenizedText
        The flat tokenisation of the entire text.
    sections : tuple[TextSection, ...]
        The local sections covering the token sequence.
    """
    if section_size < 1:
        raise ValueError(f"section_size must be ≥ 1; got {section_size}")
    if overlap < 0 or overlap >= section_size:
        raise ValueError(
            f"overlap must be in [0, section_size); got overlap={overlap}, "
            f"section_size={section_size}"
        )

    tokenized = _build_tokenized_text(text, encoding_name)
    n = tokenized.n_tokens

    if n == 0:
        return tokenized, ()

    stride = section_size - overlap
    sections: List[TextSection] = []
    start = 0
    section_index = 0

    while start < n:
        end = min(start + section_size, n)
        token_ids = tuple(range(start, end))
        window_tokens = tokenized.tokens[start:end]
        local_data = " ".join(window_tokens)
        open_set_label = f"[{start}, {end})"
        sec_id = _section_id(tokenized.text_id, start, end)
        sections.append(
            TextSection(
                section_id=sec_id,
                open_set=open_set_label,
                local_data=local_data,
                token_ids=token_ids,
                trust_level=trust_level,
            )
        )
        section_index += 1
        if end == n:
            break
        start += stride

    return tokenized, tuple(sections)


def find_section_overlap(s1: TextSection, s2: TextSection) -> Tuple[int, ...]:
    """Return the token positions that appear in both *s1* and *s2*.

    In sheaf language this computes the open set U_1 ∩ U_2 at the level of
    token positions.  An empty result means the two sections have disjoint
    support and cannot influence each other's consistency.

    Parameters
    ----------
    s1, s2 : TextSection
        Two sections of the same sheaf.

    Returns
    -------
    tuple[int, ...]
        Sorted tuple of token positions in the intersection.
    """
    ids1 = frozenset(s1.token_ids)
    ids2 = frozenset(s2.token_ids)
    intersection = ids1 & ids2
    return tuple(sorted(intersection))


def text_restriction(
    sheaf: TextSheaf,
    source_id: str,
    target_id: str,
) -> Optional[TextRestriction]:
    """Compute the restriction map ρ_{UV} : ℱ(U) → ℱ(V) for V ⊆ U.

    Given two sections identified by *source_id* (the larger open set U)
    and *target_id* (the smaller open set V), this function builds the
    restriction map by finding the token positions shared between them and
    recording the identity correspondence.

    If V is not contained in U (i.e., the intersection of their token indices
    is not equal to V's full token set), the restriction is still computed on
    the intersection, and ``is_compatible`` is set accordingly.

    Parameters
    ----------
    sheaf : TextSheaf
        The sheaf providing both sections.
    source_id : str
        section_id of the source section U.
    target_id : str
        section_id of the target section V.

    Returns
    -------
    TextRestriction or None
        The restriction map, or None if either section cannot be found.
    """
    src = sheaf.section_by_id(source_id)
    tgt = sheaf.section_by_id(target_id)
    if src is None or tgt is None:
        return None

    src_ids = frozenset(src.token_ids)
    tgt_ids = frozenset(tgt.token_ids)
    shared = src_ids & tgt_ids

    if not shared:
        # Disjoint open sets: restriction is the empty map (trivially compatible).
        return TextRestriction(
            restriction_id=_restriction_id(source_id, target_id),
            source_section=source_id,
            target_section=target_id,
            mapping=(),
            is_compatible=True,
        )

    # Build a mapping from each shared position to itself (identity on overlap).
    # In a richer implementation this would project embeddings or probability
    # distributions; here we track integer positions.
    mapping = tuple((p, p) for p in sorted(shared))

    # Compatibility: check that the tokens at shared positions match.
    src_token_map = dict(zip(src.token_ids, src.local_data.split()))
    tgt_token_map = dict(zip(tgt.token_ids, tgt.local_data.split()))

    compatible = all(
        src_token_map.get(p) == tgt_token_map.get(p)
        for p in shared
    )

    return TextRestriction(
        restriction_id=_restriction_id(source_id, target_id),
        source_section=source_id,
        target_section=target_id,
        mapping=mapping,
        is_compatible=compatible,
    )


def check_sheaf_consistency(sheaf_or_encoding: "TextSheaf | TextEncoding") -> bool:
    """Verify the gluing axiom for every overlapping pair of sections.

    The sheaf condition states: for any two open sets U, V with U ∩ V ≠ ∅,
    and for any s ∈ ℱ(U) and t ∈ ℱ(V) with  ρ_{U,U∩V}(s) = ρ_{V,U∩V}(t),
    there exists a unique global section that restricts to s on U and to t on V.

    This function checks the *necessary* condition: all restriction maps that
    have already been computed must be marked compatible.  It also checks every
    pair of sections for overlap and verifies their shared tokens match.

    Parameters
    ----------
    sheaf_or_encoding : TextSheaf or TextEncoding
        The sheaf (or an encoding whose ``.sheaf`` is checked).

    Returns
    -------
    bool
        True if all overlapping pairs agree on shared tokens.
    """
    if isinstance(sheaf_or_encoding, TextEncoding):
        sheaf = sheaf_or_encoding.sheaf
    else:
        sheaf = sheaf_or_encoding

    # 1. All precomputed restrictions must be compatible.
    for restr in sheaf.restrictions:
        if not restr.is_compatible:
            return False

    # 2. For every pair of sections, verify shared-token agreement.
    sections = sheaf.sections
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            s1, s2 = sections[i], sections[j]
            shared_positions = find_section_overlap(s1, s2)
            if not shared_positions:
                continue
            # Reconstruct per-position token maps.
            toks1 = s1.local_data.split()
            toks2 = s2.local_data.split()
            map1 = {pos: toks1[k] for k, pos in enumerate(s1.token_ids)
                    if k < len(toks1)}
            map2 = {pos: toks2[k] for k, pos in enumerate(s2.token_ids)
                    if k < len(toks2)}
            for pos in shared_positions:
                if map1.get(pos) != map2.get(pos):
                    return False

    return True


def compute_cech_complex_for_text(
    sheaf: TextSheaf,
) -> Dict[str, object]:
    """Compute the Čech cochain complex C•(𝒰, ℱ) for the text sheaf.

    Given a cover 𝒰 = {U_0, …, U_{n-1}} of the text interval, the Čech
    complex is

        C⁰ = ∏ ℱ(U_α)
        C¹ = ∏_{α < β} ℱ(U_α ∩ U_β)
        C² = ∏_{α < β < γ} ℱ(U_α ∩ U_β ∩ U_γ)

    with coboundary maps δ⁰ : C⁰ → C¹ given by  (δ⁰ s)_{αβ} = s_β|_{αβ} − s_α|_{αβ}.
    The obstruction to gluing lives in H¹ = ker δ¹ / im δ⁰.

    For text, "ℱ(U)" is the set of token strings assigned to U, so the
    arithmetic is over string equality rather than a module.  A non-trivial
    element of H¹ witnesses two sections that cannot be glued consistently—
    a genuine semantic obstruction such as a coreference or scope ambiguity.

    This implementation computes a *discrete* version:

    * C0 : list of section local_data strings
    * C1 : list of (pair_label, intersection_tokens) for overlapping pairs
    * cocycles : pairs where both restrictions are compatible (δ⁰ s = 0)
    * coboundaries : pairs where they are not (im δ⁰ ≠ 0  →  H¹ ≠ 0)
    * H1_trivial : bool, True when H¹ = 0 (no obstructions)

    Parameters
    ----------
    sheaf : TextSheaf

    Returns
    -------
    dict
        Keys: ``"C0"``, ``"C1"``, ``"cocycles"``, ``"coboundaries"``,
        ``"H1_trivial"``, ``"obstruction_count"``.
    """
    sections = sheaf.sections
    n = len(sections)

    # C⁰: one term per section.
    C0 = [s.local_data for s in sections]

    # C¹: one term per overlapping pair.
    C1: List[Tuple[str, Tuple[str, ...]]] = []
    cocycles: List[str] = []
    coboundaries: List[str] = []

    for i in range(n):
        for j in range(i + 1, n):
            s_i = sections[i]
            s_j = sections[j]
            shared_positions = find_section_overlap(s_i, s_j)
            if not shared_positions:
                continue

            pair_label = f"{s_i.section_id}∩{s_j.section_id}"

            # Extract intersection tokens from each section.
            toks_i = s_i.local_data.split()
            toks_j = s_j.local_data.split()
            map_i = {pos: toks_i[k]
                     for k, pos in enumerate(s_i.token_ids) if k < len(toks_i)}
            map_j = {pos: toks_j[k]
                     for k, pos in enumerate(s_j.token_ids) if k < len(toks_j)}

            tokens_from_i = tuple(map_i.get(p, "") for p in shared_positions)
            tokens_from_j = tuple(map_j.get(p, "") for p in shared_positions)

            C1.append((pair_label, tokens_from_i))

            # δ⁰ s = 0  ⟺  restrictions agree on intersection.
            if tokens_from_i == tokens_from_j:
                cocycles.append(pair_label)
            else:
                coboundaries.append(pair_label)

    H1_trivial = len(coboundaries) == 0

    return {
        "C0": C0,
        "C1": C1,
        "cocycles": cocycles,
        "coboundaries": coboundaries,
        "H1_trivial": H1_trivial,
        "obstruction_count": len(coboundaries),
    }


def glue_sections(
    sections: Sequence[TextSection],
    cover: TextCovering,
) -> Optional[TextSection]:
    """Attempt to glue local sections into a single global section.

    In sheaf theory, if all restriction maps agree on overlaps (H¹ = 0), there
    exists a unique global section whose restriction to each U_α equals s_α.
    This function constructs that global section by merging the token sequences,
    checking that shared positions are consistent, and computing the meet of
    all trust levels.

    If the sections are *not* consistent (H¹ ≠ 0), returns None to signal
    that no global gluing is possible.

    Parameters
    ----------
    sections : sequence of TextSection
        The local sections to be glued (must cover a connected interval).
    cover : TextCovering
        The open covering providing context.

    Returns
    -------
    TextSection or None
        The glued global section, or None if sections are inconsistent.
    """
    if not sections:
        return None

    # Collect all token positions and their assigned tokens.
    global_map: Dict[int, str] = {}
    trust_levels: List[int] = []
    conflict_detected = False

    for sec in sections:
        toks = sec.local_data.split()
        trust_levels.append(sec.trust_level)
        for k, pos in enumerate(sec.token_ids):
            if k >= len(toks):
                continue
            tok = toks[k]
            if pos in global_map:
                if global_map[pos] != tok:
                    # Conflict: two sections assign different tokens to same position.
                    conflict_detected = True
                    break
            else:
                global_map[pos] = tok
        if conflict_detected:
            break

    if conflict_detected:
        return None

    # Build the global section.
    sorted_positions = sorted(global_map.keys())
    global_tokens = tuple(global_map[p] for p in sorted_positions)
    global_trust = min(trust_levels) if trust_levels else 0
    start = sorted_positions[0] if sorted_positions else 0
    end = sorted_positions[-1] + 1 if sorted_positions else 0
    glued_id = f"glued:{cover.cover_id}[{start}:{end}]"

    return TextSection(
        section_id=glued_id,
        open_set=f"[{start}, {end})",
        local_data=" ".join(global_tokens),
        token_ids=tuple(sorted_positions),
        trust_level=global_trust,
    )


def text_trust_score(encoding: TextEncoding) -> float:
    """Compute an aggregate trust score for a TextEncoding.

    The trust score is the arithmetic mean of per-section trust levels,
    weighted by section length (number of tokens).  A section with zero tokens
    contributes nothing to the average.

    Additionally, if the sheaf is inconsistent, the score is penalised by a
    factor of  1 / (1 + obstruction_count)  where obstruction_count is the
    number of non-trivial Čech coboundaries.

    Parameters
    ----------
    encoding : TextEncoding

    Returns
    -------
    float
        Trust score in [0.0, ∞).  Typical range: [0.0, 10.0] for trust levels
        in {0, …, 10}.
    """
    sections = encoding.sections
    if not sections:
        return 0.0

    total_weight = 0
    weighted_trust = 0.0
    for sec in sections:
        weight = len(sec.token_ids)
        weighted_trust += sec.trust_level * weight
        total_weight += weight

    base_score = weighted_trust / total_weight if total_weight > 0 else 0.0

    # Penalty for Čech obstructions.
    if not encoding.is_consistent:
        cech = compute_cech_complex_for_text(encoding.sheaf)
        n_obs = cech["obstruction_count"]
        penalty = 1.0 / (1.0 + int(n_obs))
        base_score *= penalty

    return base_score


def _build_restrictions(
    sections: Tuple[TextSection, ...],
    sheaf_id: str,
) -> Tuple[TextRestriction, ...]:
    """Build restriction maps for all overlapping adjacent pairs of sections.

    For efficiency we only compute restrictions between consecutive sections
    (neighbours in the sliding-window cover).  A full implementation would
    compute all pairs, but the Čech complex is dominated by the adjacent terms
    for a 1-D base space.

    Parameters
    ----------
    sections : tuple[TextSection, ...]
        The ordered sections of the cover.
    sheaf_id : str
        The sheaf identifier (used as a stub sheaf for the restriction helper).

    Returns
    -------
    tuple[TextRestriction, ...]
        All non-empty restriction maps.
    """
    # Build a minimal TextSheaf stub so we can reuse text_restriction().
    stub_sheaf = TextSheaf(
        sheaf_id=sheaf_id,
        base_space="stub",
        sections=sections,
        restrictions=(),
        is_consistent=False,
    )

    restrictions: List[TextRestriction] = []
    n = len(sections)
    for i in range(n):
        for j in range(i + 1, min(i + 3, n)):  # at most 2 ahead
            src = sections[i]
            tgt = sections[j]
            shared = find_section_overlap(src, tgt)
            if not shared:
                continue
            restr = text_restriction(stub_sheaf, src.section_id, tgt.section_id)
            if restr is not None:
                restrictions.append(restr)

    return tuple(restrictions)


def encode_text_as_sheaf(
    text: str,
    section_size: int = DEFAULT_SECTION_SIZE,
    encoding_name: str = DEFAULT_ENCODING_NAME,
    overlap: int = DEFAULT_OVERLAP,
    trust_level: int = 5,
) -> TextEncoding:
    """Encode a text document as a sheaf-theoretic :class:`TextEncoding`.

    This is the primary entry point for the module.  It performs the following
    pipeline:

    1. **Tokenise**: split *text* into tokens with character offsets.
    2. **Cover**: partition the token sequence into overlapping windows (the
       open cover 𝒰 = {U_0, …, U_k}).
    3. **Assign sections**: each U_α gets a :class:`TextSection` ℱ(U_α)
       holding the local token string and trust annotation.
    4. **Compute restrictions**: for every overlapping pair (U_α, U_β) with
       α < β, compute the restriction map ρ_{α,β} and check compatibility.
    5. **Check consistency**: verify the full sheaf condition (H¹ = 0).
    6. **Wrap**: pack everything into a :class:`TextEncoding`.

    Text as a presheaf
    ~~~~~~~~~~~~~~~~~~
    The base space is the discrete interval {0, 1, …, N-1} of token positions,
    topologised by its connected sub-intervals (open sets).  The presheaf
    assignment  U ↦ ℱ(U)  returns the string of tokens at positions in U.
    Consistency = sheaf condition = H¹ = 0.  Any mismatch on overlapping
    windows (e.g., two language-model decodings that disagree on a shared
    token) lifts to a non-trivial Čech 1-cocycle.

    Parameters
    ----------
    text : str
        Raw input text to encode.
    section_size : int
        Tokens per window (default: 5).
    encoding_name : str
        Name of the tokenisation strategy (for metadata).
    overlap : int
        Token overlap between adjacent windows (default: 2).
    trust_level : int
        Initial trust level for all sections (default: 5, range 0-10).

    Returns
    -------
    TextEncoding
        The complete sheaf-theoretic encoding.

    Examples
    --------
    >>> enc = encode_text_as_sheaf("Hello world, this is a test.")
    >>> enc.is_consistent
    True
    >>> enc.n_tokens > 0
    True
    """
    tokenized, sections = tokenize_to_sections(
        text=text,
        section_size=section_size,
        overlap=overlap,
        encoding_name=encoding_name,
        trust_level=trust_level,
    )

    sheaf_id = f"sheaf:{tokenized.text_id}"
    base_space = f"[0, {tokenized.n_tokens})"

    restrictions = _build_restrictions(sections, sheaf_id)

    # Preliminary consistency (will be recomputed once we have the real sheaf).
    preliminary_consistent = all(r.is_compatible for r in restrictions)

    sheaf = TextSheaf(
        sheaf_id=sheaf_id,
        base_space=base_space,
        sections=sections,
        restrictions=restrictions,
        is_consistent=preliminary_consistent,
    )

    # Full consistency check (also verifies token-level agreement).
    is_consistent = check_sheaf_consistency(sheaf)

    # Rebuild sheaf with correct is_consistent flag.
    if is_consistent != preliminary_consistent:
        sheaf = TextSheaf(
            sheaf_id=sheaf_id,
            base_space=base_space,
            sections=sections,
            restrictions=restrictions,
            is_consistent=is_consistent,
        )

    overall_trust = min(
        (s.trust_level for s in sections), default=trust_level
    )
    encoding_id = f"enc:{tokenized.text_id}"

    return TextEncoding(
        encoding_id=encoding_id,
        source_text=text,
        tokens=tokenized.tokens,
        sections=sections,
        sheaf=sheaf,
        trust_level=overall_trust,
    )


# ---------------------------------------------------------------------------
# Utility: pretty-print a TextEncoding for debugging
# ---------------------------------------------------------------------------


def _fmt_section(sec: TextSection, width: int = 70) -> str:
    """Format a single TextSection for display."""
    tokens_preview = textwrap.shorten(sec.local_data, width=width - 20)
    return (
        f"  Section {sec.section_id[-20:]}\n"
        f"    open_set   : {sec.open_set}\n"
        f"    local_data : {tokens_preview!r}\n"
        f"    token_ids  : {sec.token_ids}\n"
        f"    trust      : {sec.trust_level}\n"
    )


def summarise_encoding(encoding: TextEncoding) -> str:
    """Return a human-readable summary of a :class:`TextEncoding`."""
    lines = [
        f"TextEncoding  id={encoding.encoding_id}",
        f"  source_text : {encoding.source_text[:60]!r}",
        f"  n_tokens    : {encoding.n_tokens}",
        f"  n_sections  : {encoding.n_sections}",
        f"  trust_level : {encoding.trust_level}",
        f"  consistent  : {encoding.is_consistent}",
        "",
        f"Sheaf  id={encoding.sheaf.sheaf_id}",
        f"  base_space  : {encoding.sheaf.base_space}",
        f"  n_sections  : {encoding.sheaf.n_sections}",
        f"  n_restrict  : {encoding.sheaf.n_restrictions}",
        "",
        "Sections:",
    ]
    for sec in encoding.sections:
        lines.append(_fmt_section(sec))

    # Čech summary.
    cech = compute_cech_complex_for_text(encoding.sheaf)
    lines += [
        "Čech Complex:",
        f"  |C⁰| = {len(cech['C0'])}  (one term per section)",
        f"  |C¹| = {len(cech['C1'])}  (one term per overlapping pair)",
        f"  cocycles    : {len(cech['cocycles'])}",
        f"  coboundaries: {len(cech['coboundaries'])}",
        f"  H¹ trivial  : {cech['H1_trivial']}",
    ]
    if not cech["H1_trivial"]:
        lines.append("  Obstructions:")
        for ob in cech["coboundaries"]:
            lines.append(f"    - {ob}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Judgment factory: wrap an encoding as a jugeo Judgment
# ---------------------------------------------------------------------------


def encoding_to_judgment(
    encoding: TextEncoding,
    agent: str = "text_encoder",
    background: str = "sheaf-theoretic text encoding",
) -> Judgment:
    """Wrap a :class:`TextEncoding` as a :class:`Judgment` tuple.

    The judgment  (c, φ, A, E, O, B, T, Π)  is populated as follows:

    * c  = the source text (the claim being encoded)
    * φ  = the encoding_id (a compressed formula)
    * A  = *agent* parameter
    * E  = tuple of section ids (evidence base)
    * O  = ``"text/token-interval"``  (ontological commitment)
    * B  = *background* parameter
    * T  = ``TrustTier(encoding.trust_level)``
    * Π  = ``"sheaf-consistency:True/False"``

    Parameters
    ----------
    encoding : TextEncoding
    agent : str
    background : str

    Returns
    -------
    Judgment
    """
    evidence = tuple(s.section_id for s in encoding.sections)
    proof_token = f"sheaf-consistency:{encoding.is_consistent}"
    return Judgment(
        claim=encoding.source_text,
        formula=encoding.encoding_id,
        agent=agent,
        evidence=evidence,
        ontology="text/token-interval",
        background=background,
        trust=encoding.trust_level,
        proof=proof_token,
    )


# ---------------------------------------------------------------------------
# Token-level observation builder
# ---------------------------------------------------------------------------


def build_token_observations(
    tokenized: TokenizedText,
    trust: int = 5,
    context_window: int = 3,
) -> Tuple[TokenObservation, ...]:
    """Build a :class:`TokenObservation` for every token position.

    Without a language model, probability is estimated as the inverse
    document frequency of the token type: p(t) = 1 / count(t) normalised
    so that the most frequent token has probability 1 / total_types.

    Parameters
    ----------
    tokenized : TokenizedText
    trust : int
        Trust level assigned to each observation.
    context_window : int
        Number of tokens on each side to include as context.

    Returns
    -------
    tuple[TokenObservation, ...]
    """
    tokens = tokenized.tokens
    n = len(tokens)

    # Frequency table for rudimentary probability estimate.
    freq: Dict[str, int] = {}
    for tok in tokens:
        freq[tok] = freq.get(tok, 0) + 1
    total = n if n > 0 else 1
    # Laplace-smoothed: p(t) = freq(t) / total
    def prob(t: str) -> float:
        return freq.get(t, 1) / total

    observations: List[TokenObservation] = []
    for i, tok in enumerate(tokens):
        left_start = max(0, i - context_window)
        right_end = min(n, i + context_window + 1)
        ctx_left = " ".join(tokens[left_start:i])
        ctx_right = " ".join(tokens[i + 1:right_end])
        p = prob(tok)
        if p <= 0.0:
            p = 1e-9
        obs_id = f"obs:{tokenized.text_id}@{i}"
        observations.append(
            TokenObservation(
                obs_id=obs_id,
                token=tok,
                position=i,
                context_left=ctx_left,
                context_right=ctx_right,
                probability=p,
                trust=trust,
            )
        )

    return tuple(observations)


# ---------------------------------------------------------------------------
# Covering builder (standalone)
# ---------------------------------------------------------------------------


def build_text_covering(
    sections: Tuple[TextSection, ...],
    cover_id: Optional[str] = None,
) -> TextCovering:
    """Wrap a sequence of sections in a :class:`TextCovering`.

    Parameters
    ----------
    sections : tuple[TextSection, ...]
    cover_id : str, optional
        If None, an id is derived from the section ids.

    Returns
    -------
    TextCovering
    """
    if cover_id is None:
        raw = "|".join(s.section_id for s in sections)
        cover_id = f"cover:{_sha_prefix(raw)}"
    open_sets = tuple(s.open_set for s in sections)
    return TextCovering(
        cover_id=cover_id,
        open_sets=open_sets,
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Smoke test / __main__
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Smoke test: encode a short paragraph as a text sheaf and print a
    # full diagnostic summary including Čech cohomology.
    # ------------------------------------------------------------------

    SAMPLE_TEXT = (
        "The quick brown fox jumps over the lazy dog. "
        "A fast auburn canid leaps across a sleepy hound. "
        "Sheaf-theoretic text encoding captures semantic structure "
        "by treating each overlapping window as an open set in a cover "
        "of the token interval, and gluing conditions ensure global consistency."
    )

    print("=" * 72)
    print("Sheaf-Theoretic Text Encoding — Smoke Test")
    print(f"jugeo available: {_JUGEO_AVAILABLE}")
    print("=" * 72)
    print()

    # 1. Encode.
    enc = encode_text_as_sheaf(
        SAMPLE_TEXT,
        section_size=6,
        overlap=2,
        encoding_name="simple-whitespace",
        trust_level=7,
    )

    # 2. Print summary.
    print(summarise_encoding(enc))
    print()

    # 3. Token observations.
    tokenized = _build_tokenized_text(SAMPLE_TEXT, "simple-whitespace")
    obs = build_token_observations(tokenized, trust=7, context_window=2)
    print(f"Token observations: {len(obs)}")
    if obs:
        o = obs[0]
        print(
            f"  First obs: token={o.token!r}  p={o.probability:.4f}  "
            f"surprisal={o.surprisal:.2f} bits  trust={o.trust}"
        )
    print()

    # 4. Trust score.
    score = text_trust_score(enc)
    print(f"Trust score: {score:.3f}")
    print()

    # 5. Judgment.
    j = encoding_to_judgment(enc, agent="smoke_test")
    tup = j.as_tuple()
    print(f"Judgment tuple (c, φ, A, E, O, B, T, Π):")
    print(f"  c = {str(tup[0])[:50]!r}")
    print(f"  φ = {tup[1]}")
    print(f"  A = {tup[2]}")
    print(f"  |E| = {len(tup[3])} sections")
    print(f"  O = {tup[4]}")
    print(f"  B = {tup[5]}")
    print(f"  T = {tup[6]}")
    print(f"  Π = {tup[7]}")
    print()

    # 6. Gluing test.
    covering = build_text_covering(enc.sections)
    glued = glue_sections(enc.sections, covering)
    if glued is not None:
        print(
            f"Glued section: trust={glued.trust_level}  "
            f"n_tokens={len(glued.token_ids)}"
        )
        preview = textwrap.shorten(glued.local_data, width=60)
        print(f"  preview: {preview!r}")
    else:
        print("Gluing FAILED — inconsistent sections detected.")
    print()

    # 7. Consistency assertion.
    consistent = check_sheaf_consistency(enc)
    assert consistent, "Smoke test FAILED: sheaf is inconsistent!"
    print(f"Consistency check: PASSED (H¹ = 0)  ✓")
    print()

    # 8. Edge cases.
    empty_enc = encode_text_as_sheaf("")
    assert empty_enc.n_tokens == 0
    assert empty_enc.is_consistent
    print("Edge case (empty string): PASSED  ✓")

    single_enc = encode_text_as_sheaf("hello")
    assert single_enc.n_tokens == 1
    print("Edge case (single token): PASSED  ✓")

    print()
    print("All smoke tests passed.")
