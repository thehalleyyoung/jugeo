"""
# copilot: Normalized text environment – canonical form for text comparison in Judgment Geometry

the_normalized_text_environment — Canonical form for text comparisons in Judgment Geometry.

Theory background (theory2.tex Ch28 §2):
  In Judgment Geometry, judgments are tuples (c, φ, A, E, O, B, T, Π) where:
    c  = content / claim
    φ  = force / illocutionary operator
    A  = agent / asserter
    E  = environment / context
    O  = obstructions  (Čech cohomology H¹ of the obstruction sheaf)
    B  = background / domain knowledge
    T  = trust tier (ordered algebra)
    Π  = proof / justification term

  Two text fragments are "the same" for judgment purposes iff they map to the
  same section of the *text sheaf* after normalization.  Normalization induces an
  equivalence relation on raw strings; the equivalence classes are the points of
  the canonical text space.

  This module implements:
    • The normalization pipeline (TextNormalization config + normalize_text)
    • TextCanonicalForm – the canonical representative of an equivalence class
    • NormalizedTextEnv – the full E-component carrying canonical text context
    • NormalizationObligation – a dischargeable obligation to normalize
    • TextEquivalenceClass – explicit record of an equivalence class
    • NormalizationTrace / NormStep – audit trail for normalization steps

  TrustTier ordered algebra:  0 ≤ trust_level ≤ 9, with 0 = untrusted ground
  and 9 = fully verified.  Canonicalization only "collapses" texts that agree
  after normalization *at the same trust tier or higher*.

  Obstructions = Čech H¹:  When two environments cannot be merged (e.g., their
  normalization configs are incompatible or their canonical forms conflict), the
  obstruction class in H¹ records the failure.  The merge_text_environments
  function raises ObstructionError and attaches the Čech cocycle data when this
  happens.

  Part of the JuGeo judgment-geometry framework.
"""

from __future__ import annotations

import hashlib
import logging
import re
import string
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Final, Iterator, Optional

# ---------------------------------------------------------------------------
# Optional jugeo imports – graceful fallback stubs so the module is usable
# even outside the full jugeo installation.
# ---------------------------------------------------------------------------

try:
    from jugeo.kernel.trust import TrustTier  # type: ignore
    from jugeo.geometry.supports import SupportRegion  # type: ignore
    from jugeo.judgments.base import JudgmentComponent  # type: ignore
    _JUGEO_AVAILABLE = True
except ImportError:
    _JUGEO_AVAILABLE = False

    class TrustTier:  # type: ignore  # stub
        """Stub TrustTier when jugeo.kernel is unavailable."""
        MIN: int = 0
        MAX: int = 9

    class SupportRegion:  # type: ignore  # stub
        """Stub SupportRegion when jugeo.geometry is unavailable."""

    class JudgmentComponent:  # type: ignore  # stub
        """Stub JudgmentComponent when jugeo.judgments is unavailable."""

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

TRUST_MIN: Final[int] = 0
TRUST_MAX: Final[int] = 9

#: Default separator used when joining tokens back into a normalized string.
TOKEN_JOIN_SEP: Final[str] = " "

#: Contraction expansion table (English, non-exhaustive but representative).
CONTRACTION_MAP: Final[dict[str, str]] = {
    "can't": "cannot",
    "won't": "will not",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "hasn't": "has not",
    "haven't": "have not",
    "hadn't": "had not",
    "i'm": "i am",
    "i've": "i have",
    "i'll": "i will",
    "i'd": "i would",
    "you're": "you are",
    "you've": "you have",
    "you'll": "you will",
    "you'd": "you would",
    "he's": "he is",
    "she's": "she is",
    "it's": "it is",
    "we're": "we are",
    "we've": "we have",
    "we'll": "we will",
    "we'd": "we would",
    "they're": "they are",
    "they've": "they have",
    "they'll": "they will",
    "they'd": "they would",
    "that's": "that is",
    "there's": "there is",
    "here's": "here is",
    "what's": "what is",
    "who's": "who is",
    "how's": "how is",
    "let's": "let us",
    "could've": "could have",
    "would've": "would have",
    "should've": "should have",
    "might've": "might have",
    "must've": "must have",
}

#: Punctuation characters removed when remove_punctuation=True.
_PUNCT_TABLE: Final = str.maketrans("", "", string.punctuation)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NormalizationError(Exception):
    """Raised when normalization cannot be completed for a text fragment."""


class ObstructionError(Exception):
    """
    Raised when two environments cannot be merged because of a Čech H¹
    obstruction.  The *cocycle* attribute carries the offending pair of
    canonical forms whose equivalence classes conflict.
    """

    def __init__(self, message: str, cocycle: tuple[str, str] | None = None) -> None:
        super().__init__(message)
        self.cocycle = cocycle


class TrustViolationError(Exception):
    """Raised when a trust-tier ordering constraint is violated."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormStep:
    """
    One atomic step in the normalization pipeline.

    Each normalization operation (e.g., lowercase, strip whitespace, Unicode
    NFC) is recorded as a NormStep so that the full transformation can be
    audited and replayed.  In Judgment Geometry this audit trail is part of
    the proof term Π: it witnesses that the canonical form was obtained by
    a legitimate sequence of normalization operations.

    Fields
    ------
    step_id : str
        Unique identifier for this step (UUID4 by default).
    operation : str
        Human-readable name of the normalization operation, e.g.
        ``"unicode_nfc"``, ``"lowercase"``, ``"strip_whitespace"``.
    before : str
        The text *before* this step was applied.
    after : str
        The text *after* this step was applied.
    """

    step_id: str
    operation: str
    before: str
    after: str


@dataclass(frozen=True)
class NormalizationTrace:
    """
    Complete audit trail of all normalization steps applied to a single text.

    The trace records the full sequence of NormStep objects that transformed
    *source* into *result*.  Two texts with identical *result* fields belong
    to the same equivalence class.

    Fields
    ------
    trace_id : str
        Unique identifier for this trace.
    steps : tuple[NormStep, ...]
        Ordered sequence of normalization steps.
    source : str
        The original, unnormalized text.
    result : str
        The final normalized text after all steps.
    """

    trace_id: str
    steps: tuple[NormStep, ...]
    source: str
    result: str


@dataclass(frozen=True)
class TextNormalization:
    """
    Configuration object controlling how raw text is normalized.

    In Judgment Geometry, the normalization configuration is part of the
    environment component E.  Two texts are judgment-equivalent iff they
    produce the same TextCanonicalForm under the *same* TextNormalization.

    Fields
    ------
    norm_id : str
        Unique identifier for this normalization configuration.
    lowercase : bool
        If True, fold all characters to lower case.
    strip_whitespace : bool
        If True, strip leading/trailing whitespace and collapse internal
        runs of whitespace to a single space.
    unicode_nfc : bool
        If True, apply Unicode NFC normalization (Canonical Decomposition
        followed by Canonical Composition).  This is the default form used
        in JuGeo because it is idempotent and consistent with Python's
        default string comparison.
    remove_punctuation : bool
        If True, remove all ASCII punctuation characters.
    expand_contractions : bool
        If True, expand common English contractions (e.g., "don't" →
        "do not") before other normalization steps.
    custom_rules : tuple[str, ...]
        Additional regex-based replacement rules in the format
        ``"PATTERN::REPLACEMENT"``.  Rules are applied left-to-right after
        all other normalization steps.
    """

    norm_id: str
    lowercase: bool = True
    strip_whitespace: bool = True
    unicode_nfc: bool = True
    remove_punctuation: bool = False
    expand_contractions: bool = False
    custom_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class TextCanonicalForm:
    """
    The canonical representative of a raw text string under normalization.

    After normalization, every raw string maps to exactly one
    TextCanonicalForm (modulo the normalization configuration).  Two raw
    strings that produce the same TextCanonicalForm are *judgment-equivalent*
    in the E component.

    Fields
    ------
    canonical_id : str
        Unique identifier for this canonical form (UUID4).
    original_hash : str
        SHA-256 hex digest of the *original* (pre-normalization) text,
        encoded as UTF-8.  Used to detect duplicate raw inputs.
    normalized : str
        The normalized text string.
    tokens : tuple[str, ...]
        Whitespace-delimited tokens of the normalized text.  Token-level
        operations (distance, overlap) use this field.
    trust_level : int
        Trust tier in [0..9] for this canonical form.  Inherits from the
        environment that produced it.
    is_ground : bool
        True iff this canonical form is a *ground* term – i.e., it was
        produced directly from a verified source rather than inferred or
        interpolated.
    """

    canonical_id: str
    original_hash: str
    normalized: str
    tokens: tuple[str, ...]
    trust_level: int
    is_ground: bool


@dataclass(frozen=True)
class NormalizedTextEnv:
    """
    The normalized text environment – the E component of a judgment tuple.

    A NormalizedTextEnv bundles together:
      • A collection of TextCanonicalForm objects (the "text sheaf sections")
      • The TextNormalization config that produced them
      • A trust tier controlling which operations are permitted
      • A version counter for optimistic concurrency

    In Judgment Geometry (c, φ, A, E, O, B, T, Π):
      E = NormalizedTextEnv
      O = obstructions detected during merge (Čech H¹ classes)
      T = trust_level (TrustTier ordered algebra)

    Two environments can be merged (via merge_text_environments) iff their
    normalization configs are compatible and no canonical forms conflict.
    Incompatible merges yield an ObstructionError carrying the Čech cocycle.

    Fields
    ------
    env_id : str
        Unique identifier for this environment.
    canonical_texts : tuple[TextCanonicalForm, ...]
        All canonical text forms registered in this environment.
    normalization_config : TextNormalization
        The normalization configuration used to build this environment.
    trust_level : int
        Minimum trust level required to assert new texts into this
        environment (TrustTier ordered algebra: 0 = untrusted, 9 = verified).
    version : int
        Monotonically increasing version counter.  Incremented on each
        logical update (environments are immutable; a "new version" is a
        new NormalizedTextEnv object with version+1).
    """

    env_id: str
    canonical_texts: tuple[TextCanonicalForm, ...]
    normalization_config: TextNormalization
    trust_level: int
    version: int


@dataclass(frozen=True)
class NormalizationObligation:
    """
    A dischargeable obligation to normalize a specific text fragment.

    Obligations model the *deontological* layer of Judgment Geometry: an
    agent is *obligated* to normalize a text before asserting it into an
    environment above a given trust tier.  Discharging the obligation means
    supplying evidence (the canonical form's ID) that normalization was
    actually performed.

    Fields
    ------
    obligation_id : str
        Unique identifier for this obligation.
    text_id : str
        Identifier of the raw text that must be normalized.
    required_normalization : str
        The norm_id of the TextNormalization that must be applied.
    trust_required : int
        Minimum trust tier at which this obligation must be discharged.
    is_discharged : bool
        True iff the obligation has been discharged (evidence provided).
    discharge_evidence : str
        The canonical_id of the TextCanonicalForm that discharges this
        obligation, or an empty string if not yet discharged.
    """

    obligation_id: str
    text_id: str
    required_normalization: str
    trust_required: int
    is_discharged: bool
    discharge_evidence: str


@dataclass(frozen=True)
class TextEquivalenceClass:
    """
    An explicit record of an equivalence class of texts under normalization.

    Under a fixed TextNormalization config, the set of all raw strings forms
    a partition into equivalence classes; each class has a canonical
    representative (the normalized form).  This dataclass materializes one
    such class.

    In sheaf-theoretic terms, the equivalence class is the fiber of the text
    sheaf over the point *representative* in the canonical text space.

    Fields
    ------
    class_id : str
        Unique identifier for this equivalence class.
    representative : str
        The canonical (normalized) representative string.
    members : frozenset[str]
        All known raw strings that normalize to *representative*.
    equivalence_relation : str
        Human-readable description of the equivalence relation, e.g.
        ``"unicode_nfc+lowercase+strip_whitespace"``.
    """

    class_id: str
    representative: str
    members: frozenset
    equivalence_relation: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fresh_id(prefix: str = "") -> str:
    """Return a fresh UUID4-based identifier with an optional prefix."""
    uid = str(uuid.uuid4())
    return f"{prefix}{uid}" if prefix else uid


def _sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of *text* encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).digest().hex()


def _validate_trust(level: int) -> int:
    """
    Validate and clamp a trust level to [TRUST_MIN, TRUST_MAX].

    Raises TrustViolationError if level is not an integer or is out of the
    allowed range (rather than silently clamping), so callers are forced to
    handle trust-tier violations explicitly.
    """
    if not isinstance(level, int):
        raise TrustViolationError(
            f"Trust level must be an integer, got {type(level).__name__!r}"
        )
    if level < TRUST_MIN or level > TRUST_MAX:
        raise TrustViolationError(
            f"Trust level {level} is outside the valid range "
            f"[{TRUST_MIN}, {TRUST_MAX}]"
        )
    return level


def _apply_contraction_expansion(text: str) -> str:
    """
    Expand common English contractions in *text*.

    Applies CONTRACTION_MAP left-to-right using whole-word matching so that
    substrings are not accidentally replaced (e.g., "it's" inside "bits" is
    not touched).

    Parameters
    ----------
    text:
        Raw input text, possibly already lowercased.

    Returns
    -------
    str
        Text with contractions expanded.
    """
    result = text
    for contraction, expansion in CONTRACTION_MAP.items():
        # Use word-boundary regex to avoid partial matches.
        pattern = re.compile(r"\b" + re.escape(contraction) + r"\b", re.IGNORECASE)
        result = pattern.sub(expansion, result)
    return result


def _apply_custom_rules(text: str, rules: tuple[str, ...]) -> str:
    """
    Apply a sequence of custom regex replacement rules to *text*.

    Each rule must be a string in the form ``"PATTERN::REPLACEMENT"``.
    Rules are applied sequentially; the output of one rule is the input
    to the next.

    Parameters
    ----------
    text:
        The text to transform.
    rules:
        Tuple of rule strings, each of the form ``"PATTERN::REPLACEMENT"``.

    Returns
    -------
    str
        The text after all rules have been applied.

    Raises
    ------
    NormalizationError
        If a rule is malformed (does not contain ``::``) or the regex
        pattern is invalid.
    """
    result = text
    for rule in rules:
        if "::" not in rule:
            raise NormalizationError(
                f"Custom normalization rule {rule!r} is malformed; "
                "expected format 'PATTERN::REPLACEMENT'"
            )
        pattern_str, replacement = rule.split("::", maxsplit=1)
        try:
            compiled = re.compile(pattern_str)
        except re.error as exc:
            raise NormalizationError(
                f"Invalid regex pattern in custom rule {rule!r}: {exc}"
            ) from exc
        result = compiled.sub(replacement, result)
    return result


def _tokenize(text: str) -> tuple[str, ...]:
    """
    Split *text* into whitespace-delimited tokens, discarding empties.

    This is intentionally a simple tokenizer.  More sophisticated tokenizers
    (e.g., sentence-piece, BPE) can be wired in via custom_rules.
    """
    return tuple(tok for tok in text.split() if tok)


def _equivalence_relation_label(config: TextNormalization) -> str:
    """
    Build a human-readable label describing the equivalence relation
    induced by *config*.

    The label is a ``+``-separated list of active normalization operations,
    sorted for stability.  It is used as the ``equivalence_relation`` field
    of TextEquivalenceClass objects.
    """
    parts: list[str] = []
    if config.unicode_nfc:
        parts.append("unicode_nfc")
    if config.lowercase:
        parts.append("lowercase")
    if config.strip_whitespace:
        parts.append("strip_whitespace")
    if config.remove_punctuation:
        parts.append("remove_punctuation")
    if config.expand_contractions:
        parts.append("expand_contractions")
    for i, rule in enumerate(config.custom_rules):
        parts.append(f"custom_rule_{i}")
    return "+".join(parts) if parts else "identity"


# ---------------------------------------------------------------------------
# Core public functions
# ---------------------------------------------------------------------------


def canonical_text_hash(text: str) -> str:
    """
    Return a stable, collision-resistant hash of *text*.

    The hash is a SHA-256 hex digest of the UTF-8 encoding of *text*.  It is
    used as the ``original_hash`` field of TextCanonicalForm objects and as
    the primary key for deduplication.

    Note: this hashes the *raw* text before normalization, so two raw texts
    that differ only in normalization will have different hashes here, even
    though they will produce the same TextCanonicalForm.normalized value.
    This is intentional: original_hash records provenance, not identity.

    Parameters
    ----------
    text:
        The raw text string.

    Returns
    -------
    str
        64-character lowercase hex string (SHA-256).

    Examples
    --------
    >>> h = canonical_text_hash("Hello, World!")
    >>> len(h)
    64
    >>> canonical_text_hash("Hello, World!") == canonical_text_hash("Hello, World!")
    True
    """
    return _sha256_hex(text)


def normalize_text(
    text: str,
    config: TextNormalization,
    trust_level: int = TRUST_MIN,
    is_ground: bool = False,
) -> TextCanonicalForm:
    """
    Normalize *text* according to *config* and return a TextCanonicalForm.

    This is the central operation of the normalized text environment.
    Normalizing a text creates a canonical representative for its equivalence
    class.  In Judgment Geometry, two text fragments are "the same" for
    judgment purposes iff they produce the same TextCanonicalForm.normalized
    value under the same TextNormalization configuration.

    The normalization pipeline applies operations in the following order:
      1. Unicode NFC (if ``config.unicode_nfc``)
      2. Contraction expansion (if ``config.expand_contractions``)
      3. Lowercase (if ``config.lowercase``)
      4. Remove punctuation (if ``config.remove_punctuation``)
      5. Strip / collapse whitespace (if ``config.strip_whitespace``)
      6. Custom rules (applied left-to-right)

    This order is canonical: changing it would change the equivalence
    relation and break backward compatibility.

    Parameters
    ----------
    text:
        The raw text string to normalize.
    config:
        Normalization configuration.
    trust_level:
        Trust tier to assign to the produced canonical form.
    is_ground:
        Whether to mark the canonical form as a ground term.

    Returns
    -------
    TextCanonicalForm
        The canonical form of *text* under *config*.

    Raises
    ------
    TrustViolationError
        If *trust_level* is outside [TRUST_MIN, TRUST_MAX].
    NormalizationError
        If any custom rule is malformed or has an invalid regex.

    Notes
    -----
    The function is deterministic: the same (text, config) pair always
    produces the same normalized string (though a fresh UUID4 is assigned
    to canonical_id each time).
    """
    _validate_trust(trust_level)
    original_hash = _sha256_hex(text)
    current = text

    # 1. Unicode NFC
    if config.unicode_nfc:
        current = unicodedata.normalize("NFC", current)

    # 2. Contraction expansion (before lowercase so the map matches)
    if config.expand_contractions:
        current = _apply_contraction_expansion(current)

    # 3. Lowercase
    if config.lowercase:
        current = current.lower()

    # 4. Remove punctuation
    if config.remove_punctuation:
        current = current.translate(_PUNCT_TABLE)

    # 5. Strip / collapse whitespace
    if config.strip_whitespace:
        current = current.strip()
        current = re.sub(r"\s+", " ", current)

    # 6. Custom rules
    if config.custom_rules:
        current = _apply_custom_rules(current, config.custom_rules)

    tokens = _tokenize(current)
    canonical_id = _fresh_id("cf-")
    return TextCanonicalForm(
        canonical_id=canonical_id,
        original_hash=original_hash,
        normalized=current,
        tokens=tokens,
        trust_level=trust_level,
        is_ground=is_ground,
    )


def build_text_environment(
    texts: list[str],
    config: TextNormalization,
    trust_level: int = TRUST_MIN,
    env_id: str | None = None,
) -> NormalizedTextEnv:
    """
    Build a NormalizedTextEnv from a list of raw text strings.

    Each string in *texts* is normalized via ``normalize_text`` and added to
    the environment.  Duplicate raw strings (same original_hash) are silently
    deduplicated: only the first occurrence is retained.

    The resulting environment is the E component of a judgment tuple
    (c, φ, A, E, O, B, T, Π).  Its normalization_config determines the
    equivalence relation on texts; its trust_level gates which agents may
    assert new texts.

    Parameters
    ----------
    texts:
        List of raw text strings to include in the environment.
    config:
        Normalization configuration to apply to each text.
    trust_level:
        Trust tier for the environment and all its canonical forms.
    env_id:
        Optional explicit environment identifier.  If not provided a fresh
        UUID4 is generated.

    Returns
    -------
    NormalizedTextEnv
        A freshly constructed normalized text environment.

    Raises
    ------
    TrustViolationError
        If *trust_level* is out of range.
    NormalizationError
        If any custom rule in *config* is malformed.

    Notes
    -----
    The function logs a warning if any two distinct raw texts normalize to
    the same canonical form, since this represents a potentially surprising
    equivalence collapse.
    """
    _validate_trust(trust_level)
    seen_hashes: set[str] = set()
    canonical_forms: list[TextCanonicalForm] = []

    for raw in texts:
        oh = _sha256_hex(raw)
        if oh in seen_hashes:
            _LOGGER.debug("Skipping duplicate raw text (hash=%s)", oh[:12])
            continue
        seen_hashes.add(oh)
        cf = normalize_text(raw, config, trust_level=trust_level, is_ground=True)
        canonical_forms.append(cf)

    # Warn on normalization-level duplicates (distinct raws, same normalized).
    normalized_seen: dict[str, str] = {}
    for cf in canonical_forms:
        if cf.normalized in normalized_seen:
            _LOGGER.warning(
                "Two distinct raw texts normalize to the same canonical form %r "
                "(hashes %s and %s).  They will be treated as equivalent.",
                cf.normalized,
                normalized_seen[cf.normalized][:12],
                cf.original_hash[:12],
            )
        else:
            normalized_seen[cf.normalized] = cf.original_hash

    eid = env_id if env_id is not None else _fresh_id("env-")
    return NormalizedTextEnv(
        env_id=eid,
        canonical_texts=tuple(canonical_forms),
        normalization_config=config,
        trust_level=trust_level,
        version=1,
    )


def check_text_equivalence(
    t1: str,
    t2: str,
    env: NormalizedTextEnv,
) -> bool:
    """
    Check whether *t1* and *t2* are equivalent under *env*'s normalization.

    Two texts are equivalent in Judgment Geometry iff they map to the same
    section of the text sheaf after normalization – i.e., they have the same
    ``normalized`` field under the environment's TextNormalization config.

    This function normalizes both texts on-the-fly using the environment's
    config and compares the results.  It does *not* require either text to
    already be present in the environment's ``canonical_texts``.

    Parameters
    ----------
    t1, t2:
        Raw text strings to compare.
    env:
        The normalized text environment whose config governs equivalence.

    Returns
    -------
    bool
        True iff t1 and t2 normalize to the same canonical string.

    Notes
    -----
    Equivalence is determined solely by the normalized *string*; token
    ordering matters.  "foo bar" and "bar foo" are *not* equivalent even
    though they share the same token multiset.
    """
    cf1 = normalize_text(t1, env.normalization_config, trust_level=env.trust_level)
    cf2 = normalize_text(t2, env.normalization_config, trust_level=env.trust_level)
    are_equal = cf1.normalized == cf2.normalized
    _LOGGER.debug(
        "Equivalence check: %r ~ %r → %s", t1[:40], t2[:40], are_equal
    )
    return are_equal


def compute_text_distance(
    cf1: TextCanonicalForm,
    cf2: TextCanonicalForm,
) -> float:
    """
    Compute a normalized edit-distance-like score between two canonical forms.

    The distance is defined as:

        d(cf1, cf2) = 1 – |tokens(cf1) ∩ tokens(cf2)| / |tokens(cf1) ∪ tokens(cf2)|

    This is the *Jaccard distance* between the token sets.  It ranges in
    [0.0, 1.0] where 0.0 means the texts share exactly the same token set
    and 1.0 means they share no tokens at all.

    Note: Jaccard distance is a *set*-based metric; it ignores token
    multiplicity and ordering.  For sequence-aware distance, convert the
    token tuples to strings and apply a proper edit distance algorithm.

    Parameters
    ----------
    cf1, cf2:
        The two canonical forms to compare.

    Returns
    -------
    float
        Jaccard distance in [0.0, 1.0].  Returns 0.0 if both forms have
        no tokens (i.e., both normalized to the empty string).

    Examples
    --------
    >>> config = TextNormalization(norm_id="test", lowercase=True,
    ...     strip_whitespace=True, unicode_nfc=True)
    >>> cf_a = normalize_text("the quick brown fox", config)
    >>> cf_b = normalize_text("the slow brown fox", config)
    >>> d = compute_text_distance(cf_a, cf_b)
    >>> 0.0 < d < 1.0
    True
    """
    set1 = set(cf1.tokens)
    set2 = set(cf2.tokens)
    union = set1 | set2
    if not union:
        return 0.0
    intersection = set1 & set2
    return 1.0 - len(intersection) / len(union)


def find_equivalence_class(
    text: str,
    env: NormalizedTextEnv,
) -> TextEquivalenceClass | None:
    """
    Find the equivalence class in *env* that *text* belongs to, if any.

    The equivalence class is determined by normalizing *text* and checking
    whether any canonical form in the environment has the same
    ``normalized`` field.  If a match is found, all members of the
    environment that share that normalized form are gathered into a
    TextEquivalenceClass.

    Parameters
    ----------
    text:
        The raw text to look up.
    env:
        The normalized text environment to search.

    Returns
    -------
    TextEquivalenceClass or None
        The equivalence class containing *text*, or None if *text* does not
        belong to any class in *env* (i.e., no existing canonical form
        normalizes to the same string).

    Notes
    -----
    Because TextCanonicalForm only stores the normalized string and not the
    original raw text, the ``members`` frozenset of the returned
    TextEquivalenceClass will contain *text* plus the normalized
    representative itself, but *not* other raw texts that happened to
    normalize to the same canonical form (unless they were added as
    members separately).
    """
    query_cf = normalize_text(text, env.normalization_config, trust_level=env.trust_level)
    target_normalized = query_cf.normalized

    matching: list[TextCanonicalForm] = [
        cf for cf in env.canonical_texts if cf.normalized == target_normalized
    ]

    if not matching:
        return None

    # The representative is the normalized form itself.
    representative = target_normalized
    # Members include the raw query text plus all original hashes we can recover
    # (we only have hashes, not originals, so we include the normalized form).
    member_strings: set[str] = {representative, text}

    rel_label = _equivalence_relation_label(env.normalization_config)
    return TextEquivalenceClass(
        class_id=_fresh_id("ec-"),
        representative=representative,
        members=frozenset(member_strings),
        equivalence_relation=rel_label,
    )


def merge_text_environments(
    e1: NormalizedTextEnv,
    e2: NormalizedTextEnv,
) -> NormalizedTextEnv:
    """
    Merge two normalized text environments into a single environment.

    Merging corresponds to taking the union of two text sheaf sections.
    The merged environment contains all canonical forms from both *e1* and
    *e2*.  Merging is only valid when:

      1. Both environments use the same normalization configuration (same
         norm_id).  If the configs differ, a Čech H¹ obstruction is raised.

      2. No canonical form in e1 and e2 has the same original_hash but a
         different normalized form (this would mean the normalization is
         non-deterministic or configs changed mid-flight).  Such conflicts
         also raise ObstructionError with the conflicting pair as the cocycle.

    When merging succeeds:
      • The merged env_id is freshly generated.
      • The trust_level of the merged environment is the *minimum* of the
        two input trust levels (the conservative choice: trust is not
        amplified by merging).
      • The version is max(e1.version, e2.version) + 1.

    Parameters
    ----------
    e1, e2:
        The environments to merge.

    Returns
    -------
    NormalizedTextEnv
        A new environment containing all canonical forms from both inputs.

    Raises
    ------
    ObstructionError
        If the normalization configs are incompatible (Čech H¹ obstruction)
        or if a canonical form hash conflict is detected.

    Notes
    -----
    This implements the "restriction maps agree on overlaps" condition for
    sheaf sections.  When it fails, the obstruction class in H¹ records
    precisely which pair of sections disagrees.
    """
    # Check 1: config compatibility (norm_id must match).
    if e1.normalization_config.norm_id != e2.normalization_config.norm_id:
        raise ObstructionError(
            f"Cannot merge environments: normalization configs differ "
            f"(norm_id {e1.normalization_config.norm_id!r} vs "
            f"{e2.normalization_config.norm_id!r}).  "
            "This is a Čech H¹ obstruction on the text sheaf.",
            cocycle=(e1.env_id, e2.env_id),
        )

    # Build index of e1 canonical forms by original_hash.
    e1_index: dict[str, TextCanonicalForm] = {
        cf.original_hash: cf for cf in e1.canonical_texts
    }

    merged: list[TextCanonicalForm] = list(e1.canonical_texts)
    seen_hashes: set[str] = {cf.original_hash for cf in e1.canonical_texts}

    for cf2 in e2.canonical_texts:
        if cf2.original_hash in seen_hashes:
            cf1_existing = e1_index[cf2.original_hash]
            # Check 2: same hash → must have same normalized form.
            if cf1_existing.normalized != cf2.normalized:
                raise ObstructionError(
                    f"Conflict: hash {cf2.original_hash[:12]}... normalizes to "
                    f"{cf1_existing.normalized!r} in e1 but {cf2.normalized!r} in e2.  "
                    "Non-deterministic normalization detected (Čech H¹ obstruction).",
                    cocycle=(cf1_existing.canonical_id, cf2.canonical_id),
                )
            # Consistent duplicate – skip.
            continue
        seen_hashes.add(cf2.original_hash)
        merged.append(cf2)

    merged_trust = min(e1.trust_level, e2.trust_level)
    merged_version = max(e1.version, e2.version) + 1
    return NormalizedTextEnv(
        env_id=_fresh_id("env-"),
        canonical_texts=tuple(merged),
        normalization_config=e1.normalization_config,
        trust_level=merged_trust,
        version=merged_version,
    )


def validate_normalization_obligation(
    obl: NormalizationObligation,
    env: NormalizedTextEnv,
) -> bool:
    """
    Validate whether *obl* is correctly discharged within *env*.

    An obligation is validly discharged iff:
      1. ``obl.is_discharged`` is True.
      2. ``obl.discharge_evidence`` is the canonical_id of a TextCanonicalForm
         present in *env*.
      3. The normalization config of *env* matches ``obl.required_normalization``
         (the norm_id).
      4. The trust level of *env* is ≥ ``obl.trust_required``.

    Parameters
    ----------
    obl:
        The obligation to validate.
    env:
        The normalized text environment acting as the witness.

    Returns
    -------
    bool
        True iff the obligation is validly discharged in *env*.

    Notes
    -----
    An undischarged obligation (is_discharged=False) always returns False
    without checking the other conditions, so callers can use this function
    as a quick gate before attempting normalization.
    """
    if not obl.is_discharged:
        _LOGGER.debug(
            "Obligation %s is not yet discharged.", obl.obligation_id
        )
        return False

    # Check norm_id.
    if env.normalization_config.norm_id != obl.required_normalization:
        _LOGGER.warning(
            "Obligation %s requires norm_id=%r but env uses norm_id=%r.",
            obl.obligation_id,
            obl.required_normalization,
            env.normalization_config.norm_id,
        )
        return False

    # Check trust level.
    if env.trust_level < obl.trust_required:
        _LOGGER.warning(
            "Obligation %s requires trust_level≥%d but env has trust_level=%d.",
            obl.obligation_id,
            obl.trust_required,
            env.trust_level,
        )
        return False

    # Check that evidence canonical_id exists in env.
    env_ids = {cf.canonical_id for cf in env.canonical_texts}
    if obl.discharge_evidence not in env_ids:
        _LOGGER.warning(
            "Obligation %s discharge_evidence %r not found in env %s.",
            obl.obligation_id,
            obl.discharge_evidence[:12],
            env.env_id,
        )
        return False

    _LOGGER.debug(
        "Obligation %s is validly discharged (evidence=%s).",
        obl.obligation_id,
        obl.discharge_evidence[:12],
    )
    return True


def trace_normalization(
    text: str,
    config: TextNormalization,
    trust_level: int = TRUST_MIN,
) -> tuple[TextCanonicalForm, NormalizationTrace]:
    """
    Normalize *text* and return both the canonical form and a full audit trace.

    This is a diagnostic variant of ``normalize_text`` that records every
    intermediate state as a NormStep.  The resulting NormalizationTrace can
    be attached to a judgment's proof term Π to certify that normalization
    was performed correctly.

    Parameters
    ----------
    text:
        The raw text string to normalize.
    config:
        Normalization configuration.
    trust_level:
        Trust tier for the produced canonical form.

    Returns
    -------
    (TextCanonicalForm, NormalizationTrace)
        The canonical form and its full normalization trace.
    """
    _validate_trust(trust_level)
    steps: list[NormStep] = []
    current = text

    def _record(op: str, before: str, after: str) -> None:
        steps.append(NormStep(
            step_id=_fresh_id("step-"),
            operation=op,
            before=before,
            after=after,
        ))

    # 1. Unicode NFC
    if config.unicode_nfc:
        prev = current
        current = unicodedata.normalize("NFC", current)
        _record("unicode_nfc", prev, current)

    # 2. Contraction expansion
    if config.expand_contractions:
        prev = current
        current = _apply_contraction_expansion(current)
        _record("expand_contractions", prev, current)

    # 3. Lowercase
    if config.lowercase:
        prev = current
        current = current.lower()
        _record("lowercase", prev, current)

    # 4. Remove punctuation
    if config.remove_punctuation:
        prev = current
        current = current.translate(_PUNCT_TABLE)
        _record("remove_punctuation", prev, current)

    # 5. Strip / collapse whitespace
    if config.strip_whitespace:
        prev = current
        current = current.strip()
        current = re.sub(r"\s+", " ", current)
        _record("strip_whitespace", prev, current)

    # 6. Custom rules
    for i, rule in enumerate(config.custom_rules):
        prev = current
        current = _apply_custom_rules(current, (rule,))
        _record(f"custom_rule_{i}", prev, current)

    tokens = _tokenize(current)
    cf = TextCanonicalForm(
        canonical_id=_fresh_id("cf-"),
        original_hash=_sha256_hex(text),
        normalized=current,
        tokens=tokens,
        trust_level=trust_level,
        is_ground=False,
    )
    trace = NormalizationTrace(
        trace_id=_fresh_id("trace-"),
        steps=tuple(steps),
        source=text,
        result=current,
    )
    return cf, trace


def iter_canonical_texts(env: NormalizedTextEnv) -> Iterator[TextCanonicalForm]:
    """
    Iterate over the canonical texts in *env* in registration order.

    This is a convenience generator for consumers that prefer iteration
    over direct tuple access.  Yields each TextCanonicalForm once.
    """
    yield from env.canonical_texts


def discharge_obligation(
    obl: NormalizationObligation,
    env: NormalizedTextEnv,
    text: str,
) -> tuple[NormalizationObligation, NormalizedTextEnv]:
    """
    Attempt to discharge *obl* by normalizing *text* and adding it to *env*.

    This function:
      1. Normalizes *text* using *env*'s config.
      2. Creates a new NormalizedTextEnv with the canonical form added.
      3. Returns a discharged version of the obligation with
         ``discharge_evidence`` set to the new canonical form's ID.

    If *obl* is already discharged, the function returns it unchanged
    along with the original *env*.

    Parameters
    ----------
    obl:
        The obligation to discharge.
    env:
        The environment to add the canonical form to.
    text:
        The raw text whose normalization discharges the obligation.

    Returns
    -------
    (NormalizationObligation, NormalizedTextEnv)
        The discharged obligation and the updated environment.

    Raises
    ------
    TrustViolationError
        If the environment's trust level is below obl.trust_required.
    """
    if obl.is_discharged:
        _LOGGER.debug("Obligation %s is already discharged.", obl.obligation_id)
        return obl, env

    if env.trust_level < obl.trust_required:
        raise TrustViolationError(
            f"Cannot discharge obligation {obl.obligation_id}: "
            f"environment trust_level={env.trust_level} < "
            f"required {obl.trust_required}"
        )

    cf = normalize_text(text, env.normalization_config, trust_level=env.trust_level)

    # Build new environment with cf added.
    new_env = NormalizedTextEnv(
        env_id=env.env_id,
        canonical_texts=env.canonical_texts + (cf,),
        normalization_config=env.normalization_config,
        trust_level=env.trust_level,
        version=env.version + 1,
    )

    discharged_obl = NormalizationObligation(
        obligation_id=obl.obligation_id,
        text_id=obl.text_id,
        required_normalization=obl.required_normalization,
        trust_required=obl.trust_required,
        is_discharged=True,
        discharge_evidence=cf.canonical_id,
    )
    return discharged_obl, new_env


def summarize_environment(env: NormalizedTextEnv) -> dict[str, object]:
    """
    Return a plain-dict summary of *env* suitable for logging or serialization.

    The summary includes:
      - env_id, version, trust_level
      - norm_id of the normalization config
      - Number of canonical texts
      - First 5 normalized strings (truncated to 60 chars each)

    Parameters
    ----------
    env:
        The environment to summarize.

    Returns
    -------
    dict
        A JSON-serializable summary dictionary.
    """
    sample = [
        cf.normalized[:60] for cf in env.canonical_texts[:5]
    ]
    return {
        "env_id": env.env_id,
        "version": env.version,
        "trust_level": env.trust_level,
        "norm_id": env.normalization_config.norm_id,
        "num_canonical_texts": len(env.canonical_texts),
        "sample_normalized": sample,
    }


# ---------------------------------------------------------------------------
# Default configuration factory
# ---------------------------------------------------------------------------


def default_normalization_config(norm_id: str = "default") -> TextNormalization:
    """
    Return the default TextNormalization configuration used by JuGeo.

    The default configuration applies Unicode NFC normalization, lowercasing,
    and whitespace collapsing.  It does not remove punctuation or expand
    contractions by default, as these operations may be too aggressive for
    general use.

    Parameters
    ----------
    norm_id:
        Identifier for the returned configuration object.

    Returns
    -------
    TextNormalization
        The default normalization configuration.
    """
    return TextNormalization(
        norm_id=norm_id,
        lowercase=True,
        strip_whitespace=True,
        unicode_nfc=True,
        remove_punctuation=False,
        expand_contractions=False,
        custom_rules=(),
    )


def strict_normalization_config(norm_id: str = "strict") -> TextNormalization:
    """
    Return a strict TextNormalization that removes punctuation and expands
    contractions in addition to the default operations.

    This is useful when comparing informal text where punctuation and
    contractions should be ignored for judgment purposes.

    Parameters
    ----------
    norm_id:
        Identifier for the returned configuration object.

    Returns
    -------
    TextNormalization
        A strict normalization configuration.
    """
    return TextNormalization(
        norm_id=norm_id,
        lowercase=True,
        strip_whitespace=True,
        unicode_nfc=True,
        remove_punctuation=True,
        expand_contractions=True,
        custom_rules=(),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("Smoke test: the_normalized_text_environment.py")
    print("=" * 70)

    # 1. Build a default normalization config.
    cfg = default_normalization_config(norm_id="smoke-default")
    print(f"\n[1] Default config: {cfg.norm_id!r}")
    print(f"    lowercase={cfg.lowercase}, strip_whitespace={cfg.strip_whitespace}, "
          f"unicode_nfc={cfg.unicode_nfc}")

    # 2. Normalize a handful of texts and inspect canonical forms.
    raw_texts = [
        "  Hello, World!  ",
        "hello, world!",
        "HELLO, WORLD!",
        "The quick brown fox jumps over the lazy dog.",
        "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG.",
        "  don't you know?  ",
        "Don't you know?",
        "Unicode café",
        "Unicode cafe\u0301",  # 'é' as combining accent – NFC should unify
    ]

    print(f"\n[2] Normalizing {len(raw_texts)} texts:")
    canonical_forms: list[TextCanonicalForm] = []
    for raw in raw_texts:
        cf = normalize_text(raw, cfg, trust_level=3, is_ground=True)
        canonical_forms.append(cf)
        print(f"    {raw!r:45s} → {cf.normalized!r}")

    # 3. Build a NormalizedTextEnv.
    print("\n[3] Building NormalizedTextEnv …")
    env = build_text_environment(raw_texts, cfg, trust_level=3)
    summary = summarize_environment(env)
    print(f"    env_id        : {summary['env_id']}")
    print(f"    num_canonical : {summary['num_canonical_texts']}")
    print(f"    trust_level   : {summary['trust_level']}")
    print(f"    version       : {summary['version']}")

    # 4. Check equivalences.
    print("\n[4] Equivalence checks:")
    pairs = [
        ("  Hello, World!  ", "hello, world!"),
        ("HELLO, WORLD!", "hello, world!"),
        ("Unicode café", "Unicode cafe\u0301"),
        ("foo bar", "baz qux"),
    ]
    for a, b in pairs:
        eq = check_text_equivalence(a, b, env)
        marker = "≡" if eq else "≢"
        print(f"    {a!r:30s} {marker} {b!r}")

    # 5. Compute text distances.
    print("\n[5] Text distances (Jaccard):")
    cf_fox = normalize_text("the quick brown fox", cfg)
    cf_dog = normalize_text("the lazy brown dog", cfg)
    cf_unrelated = normalize_text("completely different sentence here", cfg)
    d1 = compute_text_distance(cf_fox, cf_dog)
    d2 = compute_text_distance(cf_fox, cf_unrelated)
    d3 = compute_text_distance(cf_fox, cf_fox)
    print(f"    fox vs dog       : {d1:.4f}")
    print(f"    fox vs unrelated : {d2:.4f}")
    print(f"    fox vs fox       : {d3:.4f}")
    assert d3 == 0.0, "Identical texts must have distance 0"

    # 6. Find equivalence classes.
    print("\n[6] Finding equivalence classes:")
    ec = find_equivalence_class("HELLO, WORLD!", env)
    if ec:
        print(f"    class_id            : {ec.class_id}")
        print(f"    representative      : {ec.representative!r}")
        print(f"    members             : {ec.members}")
        print(f"    equivalence_relation: {ec.equivalence_relation}")
    else:
        print("    (no class found – unexpected)")

    not_ec = find_equivalence_class("completely absent text xyz 999", env)
    print(f"    'absent text' found class: {not_ec is not None} (expected False)")

    # 7. Merge two environments.
    print("\n[7] Merging environments:")
    extra_texts = ["A brand new text fragment.", "Another fresh fragment."]
    env2 = build_text_environment(extra_texts, cfg, trust_level=5)
    merged = merge_text_environments(env, env2)
    print(f"    env1 size    : {len(env.canonical_texts)}")
    print(f"    env2 size    : {len(env2.canonical_texts)}")
    print(f"    merged size  : {len(merged.canonical_texts)}")
    print(f"    merged trust : {merged.trust_level} (min of {env.trust_level}, {env2.trust_level})")
    print(f"    merged ver   : {merged.version}")

    # 8. Obstruction test – incompatible configs should raise.
    print("\n[8] Obstruction test (incompatible configs):")
    cfg_alt = default_normalization_config(norm_id="different-norm-id")
    env_alt = build_text_environment(["some text"], cfg_alt, trust_level=2)
    try:
        merge_text_environments(env, env_alt)
        print("    ERROR: expected ObstructionError was not raised!")
        sys.exit(1)
    except ObstructionError as exc:
        print(f"    ObstructionError raised as expected: {exc}")
        print(f"    cocycle: {exc.cocycle}")

    # 9. NormalizationObligation discharge.
    print("\n[9] Obligation discharge:")
    obl = NormalizationObligation(
        obligation_id=_fresh_id("obl-"),
        text_id="text-42",
        required_normalization=cfg.norm_id,
        trust_required=2,
        is_discharged=False,
        discharge_evidence="",
    )
    print(f"    Before discharge: is_discharged={obl.is_discharged}")
    valid_before = validate_normalization_obligation(obl, env)
    print(f"    validate (before): {valid_before}")

    discharged_obl, updated_env = discharge_obligation(
        obl, env, "Newly discharged text fragment."
    )
    print(f"    After discharge : is_discharged={discharged_obl.is_discharged}")
    valid_after = validate_normalization_obligation(discharged_obl, updated_env)
    print(f"    validate (after): {valid_after}")
    assert valid_after, "Discharged obligation must validate"

    # 10. Full normalization trace.
    print("\n[10] Normalization trace:")
    strict_cfg = strict_normalization_config(norm_id="strict-smoke")
    cf_traced, trace = trace_normalization(
        "  Don't PANIC!  ", strict_cfg, trust_level=4
    )
    print(f"    source : {trace.source!r}")
    print(f"    result : {trace.result!r}")
    print(f"    steps  : {len(trace.steps)}")
    for step in trace.steps:
        changed = "✓" if step.before != step.after else "–"
        print(f"      [{changed}] {step.operation}: {step.before!r} → {step.after!r}")

    # 11. canonical_text_hash sanity check.
    print("\n[11] canonical_text_hash:")
    h1 = canonical_text_hash("Hello")
    h2 = canonical_text_hash("Hello")
    h3 = canonical_text_hash("hello")
    assert h1 == h2, "Same input must produce same hash"
    assert h1 != h3, "Different inputs must produce different hashes"
    print(f"    hash('Hello')  = {h1[:16]}…")
    print(f"    hash('hello')  = {h3[:16]}…  (differs as expected)")

    # 12. Trust violation.
    print("\n[12] Trust violation:")
    try:
        normalize_text("test", cfg, trust_level=99)
        print("    ERROR: expected TrustViolationError was not raised!")
        sys.exit(1)
    except TrustViolationError as exc:
        print(f"    TrustViolationError raised as expected: {exc}")

    print("\n" + "=" * 70)
    print("All smoke tests passed.")
    print("=" * 70)
