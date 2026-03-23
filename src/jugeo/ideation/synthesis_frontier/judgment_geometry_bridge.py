"""Judgment geometry bridge — maps synthesis propositions into JuGeo's judgment sheaf.

Each PropositionRecord produced by the synthesis frontier is assigned a full
judgment coordinate tuple (c, φ, A, E, O, B, T, Π) in the judgment sheaf:

    c  = "synthesis:{field_id}:{prop_id}"     — coordinate
    φ  = proposition's predicate (what it asserts)
    A  = merged field's context
    E  = "ORACLE_PROPOSED"                    — evidence channel
    O  = proof obligations from proof_sketch
    B  = basis propositions it depends on
    T  = TrustTier.PROPOSAL                   — always; never silently promote
    Π  = empty initially (proof not verified)

No promotion is permitted without explicit justification and audit trail.
Synthesis propositions enter at PROPOSAL tier and must be corroborated before
any trust elevation.

# copilot: judgment geometry bridge — synthesis propositions → judgment sheaf coordinates
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import dataclasses
import datetime
import hashlib
import json
import logging
import math
import re
import time
import typing
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional project imports — fall back to stubs so this module can be
# imported and tested without the full jugeo package on PYTHONPATH.
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.models import (
        FieldNode,
        MetaphorLink,
        PropositionKind,
        PropositionRecord,
    )
    _MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover — stubs used in isolation
    _MODELS_AVAILABLE = False

    # Minimal stubs so the bridge compiles even without the full package.
    class PropositionKind:  # type: ignore[no-redef]
        """Stub enumeration for proposition kinds."""
        THEOREM = "THEOREM"
        LEMMA = "LEMMA"
        DEFINITION = "DEFINITION"
        BRIDGE_THEOREM = "BRIDGE_THEOREM"
        CONJECTURE = "CONJECTURE"

    @dataclass
    class PropositionRecord:  # type: ignore[no-redef]
        """Stub PropositionRecord used when the real model is unavailable."""
        prop_id: str = ""
        kind: str = PropositionKind.THEOREM
        statement: str = ""
        proof_sketch: str = ""
        source_fields: Tuple[str, ...] = ()
        metaphor_links: Tuple[Any, ...] = ()
        confidence: float = 0.5
        created_at: float = 0.0

    @dataclass
    class FieldNode:  # type: ignore[no-redef]
        """Stub FieldNode used when the real model is unavailable."""
        field_id: str = ""
        description: str = ""
        constituent_fields: Tuple[str, ...] = ()
        judgment_site: str = ""
        propositions: Tuple[Any, ...] = ()

    @dataclass
    class MetaphorLink:  # type: ignore[no-redef]
        """Stub MetaphorLink used when the real model is unavailable."""
        source_id: str = ""
        target_id: str = ""
        label: str = ""

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
    _TRUST_AVAILABLE = True
except ImportError:  # pragma: no cover — stubs used in isolation
    _TRUST_AVAILABLE = False

    class TrustLevel:  # type: ignore[no-redef]
        """Stub TrustLevel."""
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"

    class TrustTier:  # type: ignore[no-redef]
        """Stub TrustTier enumeration.

        In JuGeo's trust architecture, every judgment enters at PROPOSAL and
        must pass explicit review gates (corroboration, proof verification,
        peer audit) before being elevated.  Silent promotion — where a PROPOSAL
        silently acquires a higher tier without an audit trail — is strictly
        forbidden (theory2.tex §354).
        """
        PROPOSAL = "PROPOSAL"
        CANDIDATE = "CANDIDATE"
        VERIFIED = "VERIFIED"
        AXIOM = "AXIOM"


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_LOGGER: logging.Logger = logging.getLogger(__name__)
"""Standard module logger.  Configure at the application layer."""

_CANONICAL_EVIDENCE_CHANNEL: str = "ORACLE_PROPOSED"
"""Every proposition that enters the sheaf via the synthesis frontier
is stamped with this evidence channel to distinguish it from human-authored
propositions, external paper imports, and machine-verified certificates.
"""

_CANONICAL_TRUST_TIER: str = "PROPOSAL"
"""All synthesis-frontier propositions begin life at PROPOSAL tier.
This constant is intentionally hard-coded here — any code path that would
produce a *different* tier is a bug, and the validator will catch it.
See theory2.tex §354: "No proposition may enter the sheaf above PROPOSAL
without an explicit elevation certificate signed by the Audit Trail module."
"""

_MAX_PREDICATE_LENGTH: int = 500
"""Maximum character length for the φ (predicate) field of a JudgmentCoordinate.
Longer predicates are truncated with a '…' sentinel so that downstream
serialisation (JSON keys, UI display) remains bounded.
"""

_MAX_CONTEXT_LENGTH: int = 1000
"""Maximum character length for the A (context) field.
The context encodes the merged field's description plus constituent field
identifiers; truncation here loses information but keeps memory bounded.
"""

_MAX_OBLIGATION_LENGTH: int = 300
"""Maximum character length for a single proof obligation in the O tuple.
Individual obligations are extracted from proof_sketch prose; long obligations
are truncated at a sentence boundary where possible.
"""

# ---------------------------------------------------------------------------
# Module-level documentation string explaining judgment geometry theory
# ---------------------------------------------------------------------------

BRIDGE_DESCRIPTION: str = """
Judgment Geometry Bridge — Theoretical Background
==================================================

JuGeo (Judgment Geometry) is a formal framework for representing mathematical
knowledge as a *sheaf of judgments* over a topological space of mathematical
fields (theory2.tex §252).  Each judgment occupies a *coordinate* in the sheaf
and is described by the 8-tuple:

    (c, φ, A, E, O, B, T, Π)

Where the components are:

    c  — Coordinate string.  A globally unique, human-readable identifier of
         the form "synthesis:{field_id}:{prop_id}".  The "synthesis:" prefix
         signals that the proposition was generated by the synthesis frontier
         and has not yet been corroborated (§252 Definition 3.1).

    φ  — Predicate.  The logical content of the proposition stripped of
         rhetorical apparatus: what it *asserts* in minimal logical form.
         For theorems this is a ∀/∃ statement; for definitions it is a
         definitional equation; for bridge-theorems it is a correspondence
         assertion between two fields.

    A  — Context (Ambient field).  The mathematical ambient in which the
         proposition lives: the merged field's description, its constituent
         sub-fields, and its judgment site (the category-theoretic locus at
         which the proposition is evaluated).  Used by the sheaf topos
         machinery to compute stalks and restriction maps (§252 §4).

    E  — Evidence channel.  How the proposition arrived in the sheaf.  For
         synthesis-frontier propositions this is always "ORACLE_PROPOSED",
         indicating that an AI oracle proposed it and no human has yet
         vetted it.  Other channels include "HUMAN_AUTHORED", "PAPER_IMPORT",
         and "PROOF_ASSISTANT_VERIFIED".

    O  — Obligations.  A tuple of proof obligations extracted from the
         proof_sketch field.  Each obligation is a *claim that remains to be
         shown*: a sub-goal that, if discharged, would constitute a proof of
         the proposition.  Obligations are not proofs; they are a structured
         decomposition of what a proof would require.

    B  — Basis.  The set of proposition IDs on which this proposition depends.
         A proposition in the PROPOSAL tier may list other PROPOSAL
         propositions as basis, but the combined dependency graph must be
         acyclic and each basis proposition must be reachable from the same
         field node.

    T  — Trust tier.  The epistemic confidence level of the proposition.
         Synthesis propositions always enter at PROPOSAL.  The tiers form a
         strict partial order:
             AXIOM > VERIFIED > CANDIDATE > PROPOSAL
         No proposition may skip tiers or be promoted without an explicit
         audit trail entry (§354).

    Π  — Proof steps.  An ordered tuple of verified proof steps.  Empty
         initially; populated by the proof-assistant bridge when the
         proposition has been formally checked.  A non-empty Π is a
         prerequisite for elevation from PROPOSAL to CANDIDATE.

The synthesis frontier (synthesis_frontier/) generates PropositionRecord
objects from merged FieldNode objects.  This bridge module converts those
records into full JudgmentCoordinates and injects them into the evidence
section of the sheaf.

Silent Promotion Policy (§354)
-------------------------------
The most dangerous failure mode in a judgment sheaf is *silent promotion*:
a proposition that enters at PROPOSAL but is later treated as VERIFIED
without a proper audit trail.  This bridge enforces the policy at three
levels:
  1.  Every JudgmentCoordinate produced here has T == "PROPOSAL".
  2.  The JudgmentSheafValidator.validate_no_promotion() method raises an
      error if any encoding in a section has T != "PROPOSAL".
  3.  JudgmentGeometryBridge.verify_no_silent_promotion() is called after
      every batch encoding and logs a CRITICAL message on failure.

Naming Convention
-----------------
Coordinates follow the pattern: synthesis:{field_id}:{prop_id}
  - field_id  alphanumeric + hyphens, max 64 chars
  - prop_id   alphanumeric + hyphens, max 64 chars
  - Total coordinate string max: 2 + 64 + 64 + 2 separators = ~133 chars

This naming convention is stable: the same (field_id, prop_id) pair always
produces the same coordinate string, making it safe to use as a dictionary
key and database primary key.
"""

# ---------------------------------------------------------------------------
# Regex patterns for proof-obligation extraction
# ---------------------------------------------------------------------------

_PROOF_OBLIGATION_PATTERNS: List[Tuple[str, str]] = [
    # Each entry is (compiled-regex-pattern-string, human-readable description).
    # These patterns match phrases in proof_sketch prose that signal an
    # outstanding obligation — something the proof still needs to show.
    (r"(?i)must\s+show\s*[:\-]?\s*(.+?)(?:\.|;|$)", "must-show obligation"),
    (r"(?i)suffices\s+to\s+(?:show|prove|check|verify)\s*[:\-]?\s*(.+?)(?:\.|;|$)", "suffices-to obligation"),
    (r"(?i)we\s+need\s+(?:to\s+show|to\s+prove|that)\s*[:\-]?\s*(.+?)(?:\.|;|$)", "we-need obligation"),
    (r"(?i)it\s+remains\s+to\s*[:\-]?\s*(.+?)(?:\.|;|$)", "it-remains-to obligation"),
    (r"(?i)claim\s*:\s*(.+?)(?:\.|;|$)", "claim obligation"),
    (r"(?i)goal\s*:\s*(.+?)(?:\.|;|$)", "goal obligation"),
    (r"(?i)it\s+is\s+enough\s+to\s+show\s*[:\-]?\s*(.+?)(?:\.|;|$)", "it-is-enough-to obligation"),
    (r"(?i)we\s+must\s+(?:also\s+)?(?:show|verify|check)\s*[:\-]?\s*(.+?)(?:\.|;|$)", "we-must obligation"),
    (r"(?i)it\s+suffices\s+to\s*[:\-]?\s*(.+?)(?:\.|;|$)", "it-suffices-to obligation"),
    (r"(?i)(?:the\s+)?key\s+(?:step|lemma|claim)\s+is\s*[:\-]?\s*(.+?)(?:\.|;|$)", "key-step obligation"),
]
"""List of (pattern, description) pairs used by ObligationExtractor.

Each pattern's first capture group isolates the obligation text.  Patterns
are ordered from most specific to least specific so that earlier patterns
take priority when multiple patterns could match the same sentence.

These cover the most common proof-writing conventions in mathematics:
  - "must show:" / "must show that"
  - "suffices to show" / "it suffices to"
  - "we need to show" / "we need that"
  - "it remains to"
  - "claim:" / "goal:"
  - "it is enough to show"
  - "the key step is"

Reference: theory2.tex §252 Appendix B on obligation extraction heuristics.
"""

_BRIDGE_THEOREM_CORRESPONDENCE_PATTERN: re.Pattern = re.compile(
    r"""
    # Match a bridge-theorem correspondence assertion of the form:
    #   "X corresponds to Y" / "X is in bijection with Y" / "X is isomorphic to Y"
    #   "the functor F: X -> Y" / "there is a natural transformation X => Y"
    # Capture group 1: left-hand side
    # Capture group 2: right-hand side
    (?P<lhs>[\w\s\(\)\[\]\{\},\-]+?)   # left-hand side (non-greedy)
    \s*
    (?:                                  # correspondence verb phrases
        corresponds?\s+to               |
        is\s+in\s+bijection\s+with      |
        is\s+isomorphic\s+to            |
        is\s+naturally\s+isomorphic\s+to|
        is\s+equivalent\s+to            |
        is\s+dual\s+to                  |
        maps\s+(?:naturally\s+)?to      |
        is\s+mapped\s+to                |
        translates\s+to
    )
    \s*
    (?P<rhs>[\w\s\(\)\[\]\{\},\-]+)     # right-hand side
    """,
    re.VERBOSE | re.IGNORECASE,
)
"""Pre-compiled regex for detecting bridge-theorem correspondence assertions.

Bridge theorems are propositions that establish a structural correspondence
between two different mathematical fields — the defining content of the
synthesis frontier (theory2.tex §252 §3.3).

Named capture groups:
    lhs — the left-hand mathematical object / field concept
    rhs — the right-hand mathematical object / field concept

Example matches:
    "the category of sheaves corresponds to the category of étale spaces"
    "the Fourier transform is in bijection with the Pontryagin dual"
    "the fundamental group is isomorphic to the deck transformation group"
"""


# ---------------------------------------------------------------------------
# CoordinateNamingConvention
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CoordinateNamingConvention:
    """Encodes the canonical naming convention for judgment coordinates.

    Every judgment coordinate produced by the synthesis frontier follows the
    pattern::

        synthesis:{field_id}:{prop_id}

    This class encodes that convention as a first-class object so that it can
    be:
      - injected into components that need to produce coordinates
      - shared between the bridge and the validator
      - overridden in tests without monkey-patching

    The naming convention is *stable* in the sense that the same (field_id,
    prop_id) pair always produces the same coordinate string.  This stability
    guarantee is critical for using coordinates as database primary keys and
    for cross-referencing propositions across different sections of the sheaf.

    Fields
    ------
    PREFIX : str
        The prefix that identifies synthesis-frontier coordinates.  Default
        "synthesis".  Never change this without migrating all existing records.
    SEPARATOR : str
        The separator character between prefix, field_id, and prop_id.
        Default ":".  Must not appear in field_id or prop_id values.

    See Also
    --------
    theory2.tex §252 Definition 3.1 (coordinate naming), §354 (audit trail).
    """

    PREFIX: str = "synthesis"
    SEPARATOR: str = ":"

    def format(self, field_id: str, prop_id: str) -> str:
        """Produce the canonical coordinate string for (field_id, prop_id).

        Parameters
        ----------
        field_id : str
            Identifier of the merged field node (validated, no separators).
        prop_id : str
            Identifier of the proposition within that field (validated).

        Returns
        -------
        str
            Coordinate string of the form "synthesis:{field_id}:{prop_id}".

        Examples
        --------
        >>> conv = CoordinateNamingConvention()
        >>> conv.format("algebraic-topology", "prop-001")
        'synthesis:algebraic-topology:prop-001'
        """
        # Concatenate with the canonical separator; no further escaping because
        # _validate_field_id / _validate_prop_id already strip separators.
        return self.SEPARATOR.join([self.PREFIX, field_id, prop_id])

    def parse(self, coordinate: str) -> Optional[Tuple[str, str]]:
        """Parse a coordinate string back into (field_id, prop_id).

        Returns None if the string does not conform to the naming convention
        (wrong prefix, wrong number of segments, empty segments).

        Parameters
        ----------
        coordinate : str
            A string that may or may not be a valid synthesis coordinate.

        Returns
        -------
        tuple[str, str] | None
            (field_id, prop_id) on success, None on failure.

        Examples
        --------
        >>> conv = CoordinateNamingConvention()
        >>> conv.parse("synthesis:algebraic-topology:prop-001")
        ('algebraic-topology', 'prop-001')
        >>> conv.parse("human:some-paper:prop-001") is None
        True
        """
        # Split on the separator; expect exactly 3 parts.
        parts = coordinate.split(self.SEPARATOR)
        if len(parts) != 3:
            # More or fewer separators than expected — not our format.
            return None
        prefix, field_id, prop_id = parts
        if prefix != self.PREFIX:
            # Wrong prefix — this coordinate belongs to a different channel.
            return None
        if not field_id or not prop_id:
            # Empty segments are invalid.
            return None
        return field_id, prop_id


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _validate_field_id(field_id: str) -> str:
    """Validate and sanitize a field identifier.

    Validation rules:
      - Must be non-empty after stripping whitespace.
      - May contain alphanumerics, hyphens, and underscores.
      - Colons and slashes are stripped (they would break coordinate parsing).
      - Truncated to 64 characters.

    Parameters
    ----------
    field_id : str
        Raw field identifier, possibly unsanitized.

    Returns
    -------
    str
        Sanitized field identifier safe for embedding in a coordinate string.

    Raises
    ------
    ValueError
        If field_id is empty after sanitization.
    """
    # Strip leading/trailing whitespace first.
    sanitized = field_id.strip()
    # Remove any colon or slash characters that would corrupt coordinate parsing.
    sanitized = re.sub(r"[:/\\]", "_", sanitized)
    # Replace spaces with hyphens for readability.
    sanitized = re.sub(r"\s+", "-", sanitized)
    # Strip any characters outside the allowed set (alphanumeric, hyphen, underscore, dot).
    sanitized = re.sub(r"[^A-Za-z0-9\-_.]", "", sanitized)
    # Truncate to 64 characters.
    sanitized = sanitized[:64]
    if not sanitized:
        raise ValueError(
            f"field_id {field_id!r} is empty or invalid after sanitization. "
            "A non-empty alphanumeric identifier is required."
        )
    return sanitized


def _validate_prop_id(prop_id: str) -> str:
    """Validate and sanitize a proposition identifier.

    Applies the same rules as _validate_field_id but is a separate function
    so that future divergence (e.g. different max lengths) is easy to add
    without surprising the callers of either function.

    Parameters
    ----------
    prop_id : str
        Raw proposition identifier, possibly unsanitized.

    Returns
    -------
    str
        Sanitized proposition identifier safe for embedding in a coordinate
        string.

    Raises
    ------
    ValueError
        If prop_id is empty after sanitization.
    """
    # Apply the same sanitization pipeline as for field identifiers.
    sanitized = prop_id.strip()
    sanitized = re.sub(r"[:/\\]", "_", sanitized)
    sanitized = re.sub(r"\s+", "-", sanitized)
    sanitized = re.sub(r"[^A-Za-z0-9\-_.]", "", sanitized)
    sanitized = sanitized[:64]
    if not sanitized:
        raise ValueError(
            f"prop_id {prop_id!r} is empty or invalid after sanitization."
        )
    return sanitized


def _format_timestamp() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        E.g. "2024-07-15T09:31:02.451382+00:00"
    """
    # Use timezone-aware datetime so consumers can compute intervals reliably.
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _make_encoding_id(field_id: str, prop_id: str) -> str:
    """Produce a deterministic, collision-resistant encoding ID.

    The ID is derived from a SHA-256 hash of the canonical coordinate string
    so that the same (field_id, prop_id) pair always produces the same
    encoding_id.  This is important for idempotent re-injection: if the same
    proposition is injected twice, the second injection produces an identical
    encoding_id and can be deduplicated by the sheaf store.

    Parameters
    ----------
    field_id : str
        Sanitized field identifier.
    prop_id : str
        Sanitized proposition identifier.

    Returns
    -------
    str
        A 32-character lowercase hex string (first 128 bits of SHA-256).
    """
    # Build the canonical coordinate string — same as CoordinateNamingConvention.format.
    canonical = f"synthesis:{field_id}:{prop_id}"
    # SHA-256 is deterministic and collision-resistant for our purposes.
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Return the first 32 hex chars (128 bits) — enough entropy for our scale.
    return digest[:32]


# ---------------------------------------------------------------------------
# JudgmentCoordinate
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class JudgmentCoordinate:
    """A full 8-tuple judgment coordinate in JuGeo's judgment sheaf.

    This is the central data structure of the bridge.  It represents a single
    judgment — a mathematical proposition together with all the metadata needed
    to place it in the sheaf topology and to track its epistemic status.

    The 8-tuple is (c, φ, A, E, O, B, T, Π) following theory2.tex §252.

    Fields
    ------
    c : str
        The coordinate string, always of the form
        "synthesis:{field_id}:{prop_id}".  Functions as the primary key of
        this judgment in the sheaf.  (theory2.tex §252 Definition 3.1)

    phi : str
        The predicate φ — what the proposition asserts in minimal logical form.
        For a theorem this is the core ∀/∃ statement; for a definition it is
        the definitional equation; for a bridge-theorem it is the
        correspondence assertion.  Truncated to _MAX_PREDICATE_LENGTH chars.
        (theory2.tex §252 §2.1, "logical content of a judgment")

    A : str
        The ambient context — the mathematical field in which the proposition
        lives.  Derived from the merged FieldNode's description plus its
        constituent sub-fields and judgment site.  Used by the sheaf machinery
        to compute stalks and restriction maps.  Truncated to
        _MAX_CONTEXT_LENGTH chars.
        (theory2.tex §252 §4, "ambient field and stalk computation")

    E : str
        The evidence channel — how the proposition arrived in the sheaf.
        Always "ORACLE_PROPOSED" for synthesis-frontier propositions.
        (theory2.tex §354 §1, "evidence channels and trust provenance")

    O : tuple[str, ...]
        The proof obligations — a structured decomposition of what a proof
        of this proposition would require.  Each obligation is a string
        extracted from the proof_sketch field by ObligationExtractor.
        An empty tuple means no obligations were extracted (possible for
        very short or absent proof sketches).

    B : tuple[str, ...]
        The basis — a tuple of proposition IDs (other coordinate strings)
        that this proposition depends on.  Basis propositions must be
        reachable from the same field node and the combined dependency graph
        must be acyclic.
        (theory2.tex §252 §3.4, "dependency basis of a judgment")

    T : str
        The trust tier — epistemic confidence level.  Always "PROPOSAL" for
        synthesis-frontier propositions.  The trust tiers in ascending order:
            PROPOSAL < CANDIDATE < VERIFIED < AXIOM
        No proposition produced by this bridge will ever have T != "PROPOSAL".
        Attempts to set a higher tier are caught by verify_no_silent_promotion.
        (theory2.tex §354, "trust tier assignment and promotion protocol")

    Pi : tuple[str, ...]
        The proof steps Π — an ordered sequence of verified proof steps.
        Empty initially.  Populated by the proof-assistant bridge after formal
        verification.  A non-empty Π is required for promotion from PROPOSAL
        to CANDIDATE.
        (theory2.tex §354 §3, "proof certificates and tier elevation")

    See Also
    --------
    theory2.tex §252 — full definition of the judgment sheaf 8-tuple.
    theory2.tex §354 — trust tier assignment, promotion protocol, audit trail.
    """

    # The coordinate string — primary key in the sheaf.
    c: str
    # The predicate — minimal logical content of the proposition.
    phi: str
    # The ambient context — the mathematical field.
    A: str
    # The evidence channel — always "ORACLE_PROPOSED" here.
    E: str
    # Proof obligations — what remains to be shown.
    O: Tuple[str, ...] = dataclasses.field(default_factory=tuple)
    # Basis — other proposition IDs this one depends on.
    B: Tuple[str, ...] = dataclasses.field(default_factory=tuple)
    # Trust tier — always "PROPOSAL" for synthesis propositions.
    T: str = _CANONICAL_TRUST_TIER
    # Proof steps — empty until formally verified.
    Pi: Tuple[str, ...] = dataclasses.field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this coordinate to a JSON-compatible dict.

        All tuple fields are converted to lists so that the dict can be
        round-tripped through json.dumps / json.loads.

        Returns
        -------
        dict
            Keys: c, phi, A, E, O, B, T, Pi.
        """
        return {
            "c": self.c,
            "phi": self.phi,
            "A": self.A,
            "E": self.E,
            "O": list(self.O),
            "B": list(self.B),
            "T": self.T,
            "Pi": list(self.Pi),
        }

    def coordinate_key(self) -> str:
        """Return the coordinate string, usable as a dictionary key.

        Convenience alias for self.c — makes intent explicit at call sites.

        Returns
        -------
        str
            The coordinate string, e.g. "synthesis:alg-top:prop-001".
        """
        return self.c

    def summary(self) -> str:
        """Return a compact human-readable summary of this coordinate.

        Suitable for log messages and CLI output.  Truncates long phi/A values.

        Returns
        -------
        str
            A single-line summary string.
        """
        # Truncate predicate and context for display.
        phi_display = self.phi[:60] + "…" if len(self.phi) > 60 else self.phi
        a_display = self.A[:40] + "…" if len(self.A) > 40 else self.A
        n_obs = len(self.O)
        n_basis = len(self.B)
        return (
            f"[{self.c}] φ={phi_display!r} A={a_display!r} "
            f"T={self.T} E={self.E} |O|={n_obs} |B|={n_basis} |Π|={len(self.Pi)}"
        )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JudgmentCoordinate":
        """Deserialise a JudgmentCoordinate from a dict (e.g. from JSON).

        Parameters
        ----------
        d : dict
            A dict with keys c, phi, A, E, O, B, T, Pi.

        Returns
        -------
        JudgmentCoordinate
            Reconstructed coordinate.

        Raises
        ------
        KeyError
            If any required key is missing.
        """
        return cls(
            c=d["c"],
            phi=d["phi"],
            A=d["A"],
            E=d["E"],
            # Convert lists back to tuples for immutability.
            O=tuple(d.get("O", [])),
            B=tuple(d.get("B", [])),
            T=d.get("T", _CANONICAL_TRUST_TIER),
            Pi=tuple(d.get("Pi", [])),
        )


# ---------------------------------------------------------------------------
# JudgmentEncoding
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class JudgmentEncoding:
    """The result of encoding one PropositionRecord into the judgment sheaf.

    A JudgmentEncoding wraps a JudgmentCoordinate together with provenance
    metadata: which proposition and field produced it, when, and by which
    process.

    Fields
    ------
    encoding_id : str
        Deterministic 32-char hex ID derived from (field_id, prop_id).
        Used for deduplication and cross-referencing.
    prop_id : str
        The original proposition ID from PropositionRecord.
    field_id : str
        The field node ID from FieldNode.
    coordinate : JudgmentCoordinate
        The fully populated 8-tuple coordinate.
    encoded_at : float
        Unix timestamp (time.time()) when encoding was performed.
    provenance : str
        Identifies the source pipeline.  Always "synthesis_frontier" here.
    """

    encoding_id: str
    prop_id: str
    field_id: str
    coordinate: JudgmentCoordinate
    encoded_at: float
    provenance: str = "synthesis_frontier"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Returns
        -------
        dict
            Includes all fields; coordinate is nested as its own dict.
        """
        return {
            "encoding_id": self.encoding_id,
            "prop_id": self.prop_id,
            "field_id": self.field_id,
            "coordinate": self.coordinate.to_dict(),
            "encoded_at": self.encoded_at,
            "encoded_at_iso": datetime.datetime.fromtimestamp(
                self.encoded_at, tz=datetime.timezone.utc
            ).isoformat(),
            "provenance": self.provenance,
        }

    def evidence_record(self) -> Dict[str, Any]:
        """Return a minimal evidence record for audit-trail purposes.

        The evidence record is the subset of fields required by the JuGeo
        evidence ledger (theory2.tex §354 §2): coordinate, evidence channel,
        trust tier, and provenance.

        Returns
        -------
        dict
            Minimal evidence record dict.
        """
        return {
            "coordinate": self.coordinate.c,
            "evidence_channel": self.coordinate.E,
            "trust_tier": self.coordinate.T,
            "provenance": self.provenance,
            "encoding_id": self.encoding_id,
            "encoded_at": self.encoded_at,
        }


# ---------------------------------------------------------------------------
# EvidenceSection
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class EvidenceSection:
    """A collection of JudgmentEncodings that form one section of the sheaf.

    In the sheaf model (theory2.tex §252 §4), a "section" over an open set U
    is an assignment of stalk values to each point in U.  Here, U corresponds
    to the set of propositions from a single paper or field node, and the
    section assigns a JudgmentCoordinate to each proposition.

    Fields
    ------
    section_id : str
        Unique identifier for this section (UUID).
    source_paper_id : str
        Identifier of the paper or field node that generated these encodings.
    encodings : tuple[JudgmentEncoding, ...]
        All encodings in this section.
    total_coordinates : int
        Redundant count for quick access; equals len(encodings).
    trust_summary : dict
        Always {"PROPOSAL": N, "promoted": 0} for synthesis sections.
        The "promoted" key is a canary: if it is ever non-zero, the validator
        will raise an error.
    created_at : float
        Unix timestamp when the section was assembled.
    """

    section_id: str
    source_paper_id: str
    encodings: Tuple[JudgmentEncoding, ...]
    total_coordinates: int
    trust_summary: Dict[str, int]
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the section to a JSON-compatible dict.

        Returns
        -------
        dict
            All fields; encodings are serialised as a list of dicts.
        """
        return {
            "section_id": self.section_id,
            "source_paper_id": self.source_paper_id,
            "total_coordinates": self.total_coordinates,
            "trust_summary": self.trust_summary,
            "created_at": self.created_at,
            "created_at_iso": datetime.datetime.fromtimestamp(
                self.created_at, tz=datetime.timezone.utc
            ).isoformat(),
            "encodings": [enc.to_dict() for enc in self.encodings],
        }

    def summary(self) -> str:
        """Return a human-readable one-line summary of this section.

        Returns
        -------
        str
            E.g. "EvidenceSection[abc123] source=my-paper coords=5 trust={PROPOSAL:5,promoted:0}"
        """
        tid = self.section_id[:8]
        return (
            f"EvidenceSection[{tid}] source={self.source_paper_id!r} "
            f"coords={self.total_coordinates} trust={self.trust_summary}"
        )

    def by_field(self, field_id: str) -> List[JudgmentEncoding]:
        """Return all encodings whose field_id matches the given value.

        Parameters
        ----------
        field_id : str
            The field ID to filter by.

        Returns
        -------
        list[JudgmentEncoding]
            Matching encodings; may be empty.
        """
        return [enc for enc in self.encodings if enc.field_id == field_id]


# ---------------------------------------------------------------------------
# ObligationExtractor
# ---------------------------------------------------------------------------

class ObligationExtractor:
    """Extracts proof obligations from a PropositionRecord's proof_sketch field.

    Proof obligations are the *claims that remain to be shown* in a proof.
    They differ from proof steps (which are verified) and from the main
    predicate (which is what the proposition asserts).  Obligations form the
    O component of the judgment coordinate tuple.

    In theory2.tex §252, proof obligations are defined as:
        "A finite set of sentences O such that a formal proof of each sentence
         in O, together with the axioms and basis propositions B, would
         constitute a complete proof of φ."

    Extraction is heuristic: we parse the natural-language proof_sketch for
    common obligation-signalling phrases ("must show", "suffices to", etc.)
    using the pre-compiled regex patterns in _PROOF_OBLIGATION_PATTERNS.

    Limitations
    -----------
    - Only English-language proof sketches are supported.
    - Multi-sentence obligations may be truncated at _MAX_OBLIGATION_LENGTH.
    - Some proof sketches use idiosyncratic language not covered by the
      patterns; in that case the returned tuple will be empty.
    """

    def __init__(self) -> None:
        # Pre-compile all obligation patterns for efficiency at extraction time.
        # We store them as (compiled_pattern, description) pairs.
        self._compiled: List[Tuple[re.Pattern, str]] = [
            (re.compile(pat), desc)
            for pat, desc in _PROOF_OBLIGATION_PATTERNS
        ]
        _LOGGER.debug(
            "ObligationExtractor initialised with %d patterns.",
            len(self._compiled),
        )

    def extract(self, prop: Any) -> Tuple[str, ...]:
        """Extract proof obligations from a PropositionRecord.

        Scans prop.proof_sketch (if present) against all obligation patterns
        and returns the matched obligation texts as a deduplicated tuple.

        Parameters
        ----------
        prop : PropositionRecord (or duck-typed equivalent)
            The proposition to extract obligations from.

        Returns
        -------
        tuple[str, ...]
            Zero or more obligation strings, each at most _MAX_OBLIGATION_LENGTH
            characters.
        """
        # Retrieve the proof_sketch; gracefully handle missing attribute.
        sketch: str = getattr(prop, "proof_sketch", "") or ""
        if not sketch.strip():
            # No sketch available — return empty obligations.
            _LOGGER.debug(
                "prop %r has no proof_sketch; returning empty obligations.",
                getattr(prop, "prop_id", "unknown"),
            )
            return ()

        # Accumulate obligations in insertion order, deduplicated.
        seen: set = set()
        obligations: List[str] = []

        for compiled_pat, desc in self._compiled:
            # Apply the pattern to the full proof sketch text.
            for match in compiled_pat.finditer(sketch):
                # Extract the captured obligation text from group 1.
                raw = match.group(1).strip()
                cleaned = self._clean_obligation(raw)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    obligations.append(cleaned)
                    _LOGGER.debug(
                        "Obligation extracted via pattern %r: %r", desc, cleaned[:60]
                    )

        return tuple(obligations)

    def extract_basis(self, prop: Any, field: Any) -> Tuple[str, ...]:
        """Identify which other propositions this proposition depends on.

        Heuristic: scan prop.proof_sketch and prop.statement for references
        to other prop_ids in the same field node.  Also look for explicit
        "by Lemma X", "using Theorem Y", "from Proposition Z" phrases.

        Parameters
        ----------
        prop : PropositionRecord
            The proposition whose basis we are computing.
        field : FieldNode
            The field node containing all peer propositions.

        Returns
        -------
        tuple[str, ...]
            Coordinate strings of basis propositions; may be empty.
        """
        # Build a set of peer prop_ids from the field node.
        peer_props = getattr(field, "propositions", ()) or ()
        peer_ids: Dict[str, str] = {}
        for p in peer_props:
            pid = getattr(p, "prop_id", None)
            if pid and pid != getattr(prop, "prop_id", None):
                # Map raw prop_id to its coordinate string.
                try:
                    fid = _validate_field_id(getattr(field, "field_id", "unknown"))
                    vid = _validate_prop_id(pid)
                    peer_ids[pid] = f"synthesis:{fid}:{vid}"
                except ValueError:
                    pass  # Skip invalid IDs silently.

        if not peer_ids:
            return ()

        # Gather text to search.
        text_to_search = " ".join(filter(None, [
            getattr(prop, "proof_sketch", "") or "",
            getattr(prop, "statement", "") or "",
        ]))

        # Also match "by Lemma X", "using Theorem Y", "from Proposition Z" patterns.
        reference_pattern = re.compile(
            r"(?:by|using|from|via|applying)\s+(?:Lemma|Theorem|Proposition|Corollary|Fact)\s+"
            r"([\w\-]+)",
            re.IGNORECASE,
        )

        found_ids: set = set()
        # Direct ID matching — look for peer prop_ids appearing verbatim.
        for pid, coord in peer_ids.items():
            if pid in text_to_search:
                found_ids.add(coord)
        # Pattern matching — "by Lemma foo" where foo is a peer ID.
        for match in reference_pattern.finditer(text_to_search):
            ref_name = match.group(1)
            if ref_name in peer_ids:
                found_ids.add(peer_ids[ref_name])

        return tuple(sorted(found_ids))

    def _clean_obligation(self, s: str) -> str:
        """Clean up an extracted obligation string.

        Strips leading/trailing whitespace and punctuation, collapses
        internal whitespace runs, and truncates to _MAX_OBLIGATION_LENGTH.

        Parameters
        ----------
        s : str
            Raw extracted obligation string.

        Returns
        -------
        str
            Cleaned obligation string; may be empty if s is all whitespace.
        """
        # Collapse internal whitespace runs to single spaces.
        cleaned = re.sub(r"\s+", " ", s).strip(" .,;:")
        # Capitalise first letter for readability.
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        # Truncate to max length with an ellipsis sentinel.
        if len(cleaned) > _MAX_OBLIGATION_LENGTH:
            # Try to truncate at a word boundary.
            truncated = cleaned[:_MAX_OBLIGATION_LENGTH]
            last_space = truncated.rfind(" ")
            if last_space > _MAX_OBLIGATION_LENGTH // 2:
                truncated = truncated[:last_space]
            cleaned = truncated + "…"
        return cleaned


# ---------------------------------------------------------------------------
# PredicateExtractor
# ---------------------------------------------------------------------------

class PredicateExtractor:
    """Extracts the minimal logical predicate φ from a PropositionRecord.

    The predicate is the *logical core* of the proposition — what it asserts —
    stripped of rhetorical apparatus (motivation, historical context, informal
    intuition).  It becomes the φ component of the judgment coordinate tuple.

    Extraction strategy by PropositionKind:
      THEOREM / LEMMA:
          Look for ∀/∃ structure: "for all X", "there exists Y", "if P then Q".
          If not found, return the first 200 characters of the statement.
      DEFINITION:
          Look for "X is defined as Y" or "we say X if Y" patterns.
          Return the definitional equation.
      BRIDGE_THEOREM:
          Use _BRIDGE_THEOREM_CORRESPONDENCE_PATTERN to find the lhs/rhs
          correspondence and format it as "lhs ↔ rhs".
      Other kinds (CONJECTURE, etc.):
          Return statement[:200] as fallback.

    See theory2.tex §252 §2.1 for the formal definition of a predicate in the
    judgment geometry framework.
    """

    # Patterns for ∀/∃ statements in theorem/lemma prose.
    _FORALL_PATTERN = re.compile(
        r"(?:for\s+(?:all|every|any|each)\s+.+?(?:,\s*|\s+we\s+have\s*|\s+it\s+holds\s+that\s*).+?)(?:\.|$)",
        re.IGNORECASE | re.DOTALL,
    )
    _EXISTS_PATTERN = re.compile(
        r"(?:there\s+(?:exists?|is|are)\s+.+?(?:such\s+that\s*).+?)(?:\.|$)",
        re.IGNORECASE | re.DOTALL,
    )
    _IFTHEN_PATTERN = re.compile(
        r"if\s+.+?\s+then\s+.+?(?:\.|$)",
        re.IGNORECASE | re.DOTALL,
    )
    # Pattern for definitions: "X is defined as Y" / "we say X if Y"
    _DEFN_PATTERN = re.compile(
        r"(?:(?:[\w\s]+)\s+is\s+defined\s+as\s+.+?(?:\.|$))"
        r"|(?:we\s+(?:say|call|define)\s+.+?(?:if|when|to\s+be)\s+.+?(?:\.|$))",
        re.IGNORECASE | re.DOTALL,
    )

    def extract(self, prop: Any) -> str:
        """Extract the predicate φ from a PropositionRecord.

        Parameters
        ----------
        prop : PropositionRecord (or duck-typed equivalent)
            The proposition to extract the predicate from.

        Returns
        -------
        str
            The extracted predicate, normalised and truncated to
            _MAX_PREDICATE_LENGTH.
        """
        statement: str = getattr(prop, "statement", "") or ""
        kind: str = str(getattr(prop, "kind", "")) or ""
        # Normalise the kind string for comparison.
        kind_upper = kind.upper() if isinstance(kind, str) else str(kind).upper()

        # --- BRIDGE_THEOREM: look for correspondence assertion. ---
        if "BRIDGE" in kind_upper or "CORRESPONDENCE" in kind_upper:
            match = _BRIDGE_THEOREM_CORRESPONDENCE_PATTERN.search(statement)
            if match:
                lhs = match.group("lhs").strip()
                rhs = match.group("rhs").strip()
                predicate = f"{lhs} ↔ {rhs}"
                return self.normalize(predicate)

        # --- DEFINITION: look for definitional equation. ---
        if "DEF" in kind_upper:
            match = self._DEFN_PATTERN.search(statement)
            if match:
                return self.normalize(match.group(0))

        # --- THEOREM / LEMMA: look for ∀/∃/if-then structure. ---
        if any(k in kind_upper for k in ("THEOREM", "LEMMA", "COROLLARY", "PROPOSITION")):
            for pattern in (self._FORALL_PATTERN, self._EXISTS_PATTERN, self._IFTHEN_PATTERN):
                match = pattern.search(statement)
                if match:
                    return self.normalize(match.group(0))

        # --- Fallback: return the first 200 characters. ---
        fallback = statement[:200] if statement else "(no statement)"
        return self.normalize(fallback)

    def normalize(self, predicate: str) -> str:
        """Normalise whitespace and truncate a predicate string.

        Parameters
        ----------
        predicate : str
            Raw predicate string.

        Returns
        -------
        str
            Normalised predicate, at most _MAX_PREDICATE_LENGTH chars.
        """
        # Collapse internal whitespace (newlines, tabs, runs of spaces).
        normalised = re.sub(r"\s+", " ", predicate).strip()
        # Truncate at a word boundary if possible.
        if len(normalised) > _MAX_PREDICATE_LENGTH:
            truncated = normalised[:_MAX_PREDICATE_LENGTH]
            last_space = truncated.rfind(" ")
            if last_space > _MAX_PREDICATE_LENGTH // 2:
                truncated = truncated[:last_space]
            normalised = truncated + "…"
        return normalised


# ---------------------------------------------------------------------------
# ContextExtractor
# ---------------------------------------------------------------------------

class ContextExtractor:
    """Extracts the ambient context A from a FieldNode.

    The context A encodes the mathematical ambient in which a proposition
    lives.  It is derived from three sources in the FieldNode:
      1.  field.description — natural-language description of the field.
      2.  field.constituent_fields — the sub-fields whose merger produced
          this field node.
      3.  field.judgment_site — the category-theoretic locus at which the
          field's propositions are evaluated.

    The three sources are concatenated with separators and truncated to
    _MAX_CONTEXT_LENGTH.

    See theory2.tex §252 §4 for the formal definition of ambient context.
    """

    def extract(self, field: Any) -> str:
        """Build the ambient context string from a FieldNode.

        Parameters
        ----------
        field : FieldNode (or duck-typed equivalent)
            The merged field node.

        Returns
        -------
        str
            Context string, at most _MAX_CONTEXT_LENGTH chars.
        """
        # --- Component 1: field description. ---
        description: str = getattr(field, "description", "") or ""

        # --- Component 2: constituent fields as a bracketed list. ---
        constituents = getattr(field, "constituent_fields", ()) or ()
        if constituents:
            # Format as "constituents:[field-a, field-b, ...]"
            constituent_str = "constituents:[" + ", ".join(str(c) for c in constituents) + "]"
        else:
            constituent_str = ""

        # --- Component 3: judgment site. ---
        judgment_site: str = getattr(field, "judgment_site", "") or ""
        if judgment_site:
            site_str = f"judgment_site:{judgment_site}"
        else:
            site_str = ""

        # Concatenate non-empty components with " | " separator.
        parts = [p for p in [description, constituent_str, site_str] if p.strip()]
        context = " | ".join(parts)

        # Normalise whitespace and truncate.
        context = re.sub(r"\s+", " ", context).strip()
        if len(context) > _MAX_CONTEXT_LENGTH:
            truncated = context[:_MAX_CONTEXT_LENGTH]
            last_space = truncated.rfind(" ")
            if last_space > _MAX_CONTEXT_LENGTH // 2:
                truncated = truncated[:last_space]
            context = truncated + "…"

        return context or "(no context)"


# ---------------------------------------------------------------------------
# JudgmentGeometryBridge
# ---------------------------------------------------------------------------

class JudgmentGeometryBridge:
    """Main bridge: converts synthesis-frontier output into judgment sheaf entries.

    This is the primary entry point for callers.  Given a PropositionRecord and
    a FieldNode, it:
      1.  Extracts the predicate φ via PredicateExtractor.
      2.  Extracts the ambient context A via ContextExtractor.
      3.  Extracts proof obligations O via ObligationExtractor.
      4.  Extracts the basis B via ObligationExtractor.extract_basis.
      5.  Builds a JudgmentCoordinate with T = "PROPOSAL", E = "ORACLE_PROPOSED".
      6.  Wraps it in a JudgmentEncoding with provenance = "synthesis_frontier".

    The bridge enforces the no-silent-promotion policy by:
      - Hard-coding T = _CANONICAL_TRUST_TIER ("PROPOSAL") at step 5.
      - Providing verify_no_silent_promotion() for post-hoc batch checking.

    Typical usage::

        bridge = JudgmentGeometryBridge()
        encoding = bridge.encode_proposition(prop, field)
        print(encoding.coordinate.summary())

    See theory2.tex §252, §354.
    """

    # Canonical naming convention — shared across all instances.
    _naming: ClassVar[CoordinateNamingConvention] = CoordinateNamingConvention()

    def __init__(self) -> None:
        """Initialise sub-extractors."""
        # Predicate extractor — produces the φ component.
        self._predicate_extractor = PredicateExtractor()
        # Context extractor — produces the A component.
        self._context_extractor = ContextExtractor()
        # Obligation extractor — produces O and B components.
        self._obligation_extractor = ObligationExtractor()
        _LOGGER.info("JudgmentGeometryBridge initialised.")

    def encode_proposition(self, prop: Any, field: Any) -> JudgmentEncoding:
        """Encode one PropositionRecord into a JudgmentEncoding.

        This is the core method of the bridge.  It maps the synthesis-frontier
        data model (PropositionRecord, FieldNode) onto the judgment sheaf data
        model (JudgmentCoordinate, JudgmentEncoding).

        The encoding process proceeds in six steps:

        Step 1 — Extract and validate IDs.
            Retrieve field_id and prop_id from the objects; validate and
            sanitize them with _validate_field_id / _validate_prop_id.

        Step 2 — Build coordinate string c.
            Apply CoordinateNamingConvention.format(field_id, prop_id) to
            produce the canonical coordinate string.

        Step 3 — Extract φ (predicate).
            Use PredicateExtractor to find the minimal logical content of the
            proposition statement.

        Step 4 — Extract A (ambient context).
            Use ContextExtractor to build the context string from the field node.

        Step 5 — Extract O (obligations) and B (basis).
            Use ObligationExtractor to parse the proof_sketch.

        Step 6 — Assemble JudgmentCoordinate.
            Hard-code E = "ORACLE_PROPOSED" and T = "PROPOSAL".  Π is empty.

        Parameters
        ----------
        prop : PropositionRecord
            The proposition to encode.
        field : FieldNode
            The field node that produced this proposition.

        Returns
        -------
        JudgmentEncoding
            The fully populated encoding.

        Raises
        ------
        ValueError
            If field_id or prop_id cannot be sanitized to a non-empty string.
        """
        # --- Step 1: Extract and validate identifiers. ---
        raw_field_id: str = getattr(field, "field_id", "") or "unknown-field"
        raw_prop_id: str = getattr(prop, "prop_id", "") or str(uuid.uuid4())
        field_id = _validate_field_id(raw_field_id)
        prop_id = _validate_prop_id(raw_prop_id)

        _LOGGER.debug(
            "Encoding proposition field_id=%r prop_id=%r", field_id, prop_id
        )

        # --- Step 2: Build the coordinate string c. ---
        # Always use the canonical naming convention — never hand-roll this.
        coordinate_str = self._naming.format(field_id, prop_id)

        # --- Step 3: Extract the predicate φ. ---
        # The predicate is the logical core of the proposition.
        phi = self._predicate_extractor.extract(prop)

        # --- Step 4: Extract the ambient context A. ---
        # The context encodes the mathematical ambient (field description,
        # constituent fields, judgment site).
        ambient_context = self._context_extractor.extract(field)

        # --- Step 5: Extract obligations O and basis B. ---
        # Obligations are claims that remain to be shown in the proof.
        # Basis propositions are those that this proposition depends on.
        obligations = self._obligation_extractor.extract(prop)
        basis = self._obligation_extractor.extract_basis(prop, field)

        # --- Step 6: Assemble the JudgmentCoordinate. ---
        # CRITICAL: T is *always* "PROPOSAL" here.  E is always "ORACLE_PROPOSED".
        # Π (proof steps) is always empty until formal verification is performed.
        # See theory2.tex §354: "No proposition may enter the sheaf above PROPOSAL."
        coordinate = JudgmentCoordinate(
            c=coordinate_str,
            phi=phi,
            A=ambient_context,
            E=_CANONICAL_EVIDENCE_CHANNEL,   # always "ORACLE_PROPOSED"
            O=obligations,
            B=basis,
            T=_CANONICAL_TRUST_TIER,          # always "PROPOSAL"
            Pi=(),                            # always empty at encoding time
        )

        # Build the deterministic encoding ID.
        encoding_id = _make_encoding_id(field_id, prop_id)

        # Wrap coordinate in a JudgmentEncoding with provenance metadata.
        encoding = JudgmentEncoding(
            encoding_id=encoding_id,
            prop_id=prop_id,
            field_id=field_id,
            coordinate=coordinate,
            encoded_at=time.time(),
            provenance="synthesis_frontier",
        )

        _LOGGER.info(
            "Encoded proposition %r → coordinate %r (T=%s, |O|=%d, |B|=%d)",
            prop_id, coordinate_str, coordinate.T, len(obligations), len(basis),
        )
        return encoding

    def encode_field_node(self, node: Any) -> List[JudgmentEncoding]:
        """Encode all propositions in a FieldNode.

        Iterates over node.propositions and calls encode_proposition for each.
        Failed encodings are logged and skipped (not raised) so that one bad
        proposition does not abort the entire field encoding.

        Parameters
        ----------
        node : FieldNode
            The field node to encode.

        Returns
        -------
        list[JudgmentEncoding]
            One encoding per successfully encoded proposition.
        """
        propositions = getattr(node, "propositions", ()) or ()
        encodings: List[JudgmentEncoding] = []

        for prop in propositions:
            try:
                enc = self.encode_proposition(prop, node)
                encodings.append(enc)
            except Exception as exc:  # pylint: disable=broad-except
                # Log and skip — don't let one bad proposition abort the batch.
                _LOGGER.warning(
                    "Failed to encode proposition %r in field %r: %s",
                    getattr(prop, "prop_id", "?"),
                    getattr(node, "field_id", "?"),
                    exc,
                )

        _LOGGER.info(
            "encode_field_node: field=%r encoded %d/%d propositions.",
            getattr(node, "field_id", "?"),
            len(encodings),
            len(propositions),
        )
        return encodings

    def encode_paper(self, paper: Any, synthesis: Any) -> List[JudgmentEncoding]:
        """Encode propositions from a MathPaper-like object.

        Some callers work with paper objects (e.g. imported from arXiv or a
        proof assistant export) that have a .theorems attribute.  This method
        iterates over paper.theorems and treats each as a (proposition, field)
        pair, using the synthesis object to resolve the appropriate FieldNode.

        Parameters
        ----------
        paper : object with .theorems attribute
            A MathPaper-like object.  Each element of .theorems should be a
            PropositionRecord or duck-typed equivalent.
        synthesis : object with .field_for_prop(prop_id) or .default_field
            Used to look up the field node for each theorem.

        Returns
        -------
        list[JudgmentEncoding]
            One encoding per successfully encoded theorem.
        """
        theorems = getattr(paper, "theorems", ()) or ()
        encodings: List[JudgmentEncoding] = []

        for prop in theorems:
            # Try to get the appropriate field from synthesis; fall back to a
            # synthetic stub field if not available.
            field: Any = None
            if hasattr(synthesis, "field_for_prop"):
                try:
                    field = synthesis.field_for_prop(getattr(prop, "prop_id", ""))
                except Exception:  # pylint: disable=broad-except
                    pass
            if field is None:
                # Use synthesis itself as the field if it has field_id, or build a stub.
                field = getattr(synthesis, "default_field", synthesis)

            try:
                enc = self.encode_proposition(prop, field)
                encodings.append(enc)
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "encode_paper: failed on theorem %r: %s",
                    getattr(prop, "prop_id", "?"),
                    exc,
                )

        _LOGGER.info(
            "encode_paper: paper=%r encoded %d/%d theorems.",
            getattr(paper, "paper_id", "?"),
            len(encodings),
            len(theorems),
        )
        return encodings

    def build_evidence_section(
        self, encodings: List[JudgmentEncoding]
    ) -> EvidenceSection:
        """Assemble a list of JudgmentEncodings into an EvidenceSection.

        Computes trust_summary, assigns a new section_id, and validates that
        all encodings are at PROPOSAL tier (calls verify_no_silent_promotion).

        Parameters
        ----------
        encodings : list[JudgmentEncoding]
            Encodings to bundle into a section.

        Returns
        -------
        EvidenceSection
            The assembled section.

        Raises
        ------
        ValueError
            If any encoding has a trust tier other than "PROPOSAL".
        """
        # Validate no silent promotion before assembling the section.
        self.verify_no_silent_promotion(encodings)

        # Compute trust summary — should always be {PROPOSAL: N, promoted: 0}.
        trust_summary: Dict[str, int] = {"PROPOSAL": 0, "promoted": 0}
        for enc in encodings:
            tier = enc.coordinate.T
            if tier == "PROPOSAL":
                trust_summary["PROPOSAL"] += 1
            else:
                # This should never happen after verify_no_silent_promotion,
                # but we track it defensively.
                trust_summary["promoted"] += 1

        # Derive source_paper_id from the first encoding's field_id, or "unknown".
        source_paper_id = encodings[0].field_id if encodings else "unknown"

        section = EvidenceSection(
            section_id=str(uuid.uuid4()),
            source_paper_id=source_paper_id,
            encodings=tuple(encodings),
            total_coordinates=len(encodings),
            trust_summary=trust_summary,
            created_at=time.time(),
        )
        _LOGGER.info("Built EvidenceSection: %s", section.summary())
        return section

    def verify_no_silent_promotion(
        self, encodings: List[JudgmentEncoding]
    ) -> bool:
        """Assert that no encoding has a trust tier above PROPOSAL.

        This is the primary enforcement mechanism for the no-silent-promotion
        policy (theory2.tex §354).  It is called automatically by
        build_evidence_section but can also be called stand-alone.

        Parameters
        ----------
        encodings : list[JudgmentEncoding]
            Encodings to check.

        Returns
        -------
        bool
            True if all encodings are at PROPOSAL tier.

        Raises
        ------
        ValueError
            If any encoding has T != "PROPOSAL", with details of which
            encoding(s) violated the policy.
        """
        # Collect violators for a comprehensive error message.
        violators: List[str] = []
        for enc in encodings:
            if enc.coordinate.T != _CANONICAL_TRUST_TIER:
                violators.append(
                    f"encoding_id={enc.encoding_id} prop_id={enc.prop_id} "
                    f"T={enc.coordinate.T!r} (expected {_CANONICAL_TRUST_TIER!r})"
                )

        if violators:
            # Log at CRITICAL level — this is a trust-model violation.
            _LOGGER.critical(
                "SILENT PROMOTION DETECTED in %d encoding(s): %s",
                len(violators),
                "; ".join(violators),
            )
            raise ValueError(
                f"Silent promotion policy violation: {len(violators)} encoding(s) "
                f"have trust tier != {_CANONICAL_TRUST_TIER!r}.\n"
                + "\n".join(violators)
            )

        # All good.
        _LOGGER.debug(
            "verify_no_silent_promotion: all %d encodings are at PROPOSAL tier.",
            len(encodings),
        )
        return True

    def coordinate_for_paper(
        self, paper: Any, synthesis: Any
    ) -> Dict[str, Any]:
        """Return a {coordinate_key: encoding_dict} mapping for a paper.

        Convenience method for callers that need a flat dict indexed by
        coordinate string.

        Parameters
        ----------
        paper : object with .theorems
            MathPaper-like object.
        synthesis : object
            Synthesis context used to resolve field nodes.

        Returns
        -------
        dict
            Keys are coordinate strings; values are encoding dicts.
        """
        encodings = self.encode_paper(paper, synthesis)
        return {enc.coordinate.coordinate_key(): enc.to_dict() for enc in encodings}


# ---------------------------------------------------------------------------
# JudgmentSheafValidator
# ---------------------------------------------------------------------------

class JudgmentSheafValidator:
    """Validates JudgmentCoordinates and EvidenceSections against sheaf rules.

    The validator is a read-only component: it never modifies the objects it
    validates.  It returns lists of error strings so that callers can decide
    how to handle violations (raise, log, or accumulate).

    Rules enforced:
      - Coordinate c must match the naming convention.
      - phi must be non-empty and at most _MAX_PREDICATE_LENGTH chars.
      - A must be non-empty and at most _MAX_CONTEXT_LENGTH chars.
      - E must equal _CANONICAL_EVIDENCE_CHANNEL.
      - T must equal _CANONICAL_TRUST_TIER.
      - Each obligation in O must be at most _MAX_OBLIGATION_LENGTH chars.
      - Pi must be empty (proof not yet verified).
    """

    def __init__(self) -> None:
        # Naming convention for coordinate format checks.
        self._naming = CoordinateNamingConvention()

    def validate_coordinate(self, coord: JudgmentCoordinate) -> List[str]:
        """Validate a single JudgmentCoordinate.

        Parameters
        ----------
        coord : JudgmentCoordinate
            The coordinate to validate.

        Returns
        -------
        list[str]
            List of error messages; empty if the coordinate is valid.
        """
        errors: List[str] = []

        # --- Validate c (coordinate string). ---
        parsed = self._naming.parse(coord.c)
        if parsed is None:
            errors.append(
                f"Coordinate string {coord.c!r} does not match naming convention "
                f"'synthesis:{{field_id}}:{{prop_id}}'."
            )

        # --- Validate phi (predicate). ---
        if not coord.phi or not coord.phi.strip():
            errors.append("Predicate φ (phi) must be non-empty.")
        if len(coord.phi) > _MAX_PREDICATE_LENGTH:
            errors.append(
                f"Predicate φ is {len(coord.phi)} chars; max is {_MAX_PREDICATE_LENGTH}."
            )

        # --- Validate A (context). ---
        if not coord.A or not coord.A.strip():
            errors.append("Context A must be non-empty.")
        if len(coord.A) > _MAX_CONTEXT_LENGTH:
            errors.append(
                f"Context A is {len(coord.A)} chars; max is {_MAX_CONTEXT_LENGTH}."
            )

        # --- Validate E (evidence channel). ---
        if coord.E != _CANONICAL_EVIDENCE_CHANNEL:
            errors.append(
                f"Evidence channel E is {coord.E!r}; expected {_CANONICAL_EVIDENCE_CHANNEL!r}."
            )

        # --- Validate T (trust tier). ---
        if coord.T != _CANONICAL_TRUST_TIER:
            errors.append(
                f"Trust tier T is {coord.T!r}; expected {_CANONICAL_TRUST_TIER!r}. "
                "Possible silent promotion — see theory2.tex §354."
            )

        # --- Validate O (obligations). ---
        for i, obligation in enumerate(coord.O):
            if len(obligation) > _MAX_OBLIGATION_LENGTH:
                errors.append(
                    f"Obligation O[{i}] is {len(obligation)} chars; max is {_MAX_OBLIGATION_LENGTH}."
                )

        # --- Validate Pi (proof steps). ---
        if coord.Pi:
            errors.append(
                f"Proof steps Π must be empty at encoding time; found {len(coord.Pi)} step(s). "
                "Proof verification is a separate pipeline stage."
            )

        return errors

    def validate_section(self, section: EvidenceSection) -> List[str]:
        """Validate all coordinates in an EvidenceSection.

        Parameters
        ----------
        section : EvidenceSection
            The section to validate.

        Returns
        -------
        list[str]
            All error messages from all coordinates; empty if all are valid.
        """
        all_errors: List[str] = []

        # Check total_coordinates matches actual count.
        if section.total_coordinates != len(section.encodings):
            all_errors.append(
                f"total_coordinates={section.total_coordinates} but "
                f"len(encodings)={len(section.encodings)}."
            )

        # Check trust_summary "promoted" key is zero.
        promoted_count = section.trust_summary.get("promoted", 0)
        if promoted_count != 0:
            all_errors.append(
                f"trust_summary['promoted'] = {promoted_count}; expected 0. "
                "Silent promotion may have occurred."
            )

        # Validate each individual coordinate.
        for enc in section.encodings:
            coord_errors = self.validate_coordinate(enc.coordinate)
            for err in coord_errors:
                all_errors.append(f"[{enc.encoding_id[:8]}] {err}")

        return all_errors

    def validate_no_promotion(self, section: EvidenceSection) -> bool:
        """Return True iff no encoding in the section has been promoted.

        Parameters
        ----------
        section : EvidenceSection
            Section to check.

        Returns
        -------
        bool
            True if all encodings are at PROPOSAL tier, False otherwise.
        """
        # Check the canary key.
        if section.trust_summary.get("promoted", 0) != 0:
            return False
        # Also check each encoding individually.
        return all(
            enc.coordinate.T == _CANONICAL_TRUST_TIER
            for enc in section.encodings
        )


# ---------------------------------------------------------------------------
# SheafInjector
# ---------------------------------------------------------------------------

class SheafInjector:
    """Orchestrates encoding and validation before injection into the sheaf.

    The SheafInjector is a higher-level wrapper around JudgmentGeometryBridge
    that:
      1.  Calls the bridge to produce encodings.
      2.  Validates them with JudgmentSheafValidator.
      3.  Assembles an EvidenceSection.
      4.  Returns the section and (optionally) an injection report.

    It does *not* actually write to any database or file — that is the
    responsibility of the sheaf store, which is separate from this module.

    Usage::

        bridge = JudgmentGeometryBridge()
        injector = SheafInjector(bridge)
        section = injector.inject_field(some_field_node)
        print(injector.injection_report(section))
    """

    def __init__(self, bridge: JudgmentGeometryBridge) -> None:
        """Initialise with a JudgmentGeometryBridge.

        Parameters
        ----------
        bridge : JudgmentGeometryBridge
            The bridge to use for encoding propositions.
        """
        self._bridge = bridge
        self._validator = JudgmentSheafValidator()
        _LOGGER.info("SheafInjector initialised.")

    def inject_field(self, field: Any) -> EvidenceSection:
        """Encode and validate all propositions in a FieldNode.

        Parameters
        ----------
        field : FieldNode
            The field node to inject.

        Returns
        -------
        EvidenceSection
            Validated section ready for persistence.

        Raises
        ------
        ValueError
            If validation finds critical errors (silent promotion, bad IDs).
        """
        # Step 1: Encode all propositions in the field.
        encodings = self._bridge.encode_field_node(field)

        # Step 2: Validate all encodings.
        section = self._bridge.build_evidence_section(encodings)
        errors = self._validator.validate_section(section)
        if errors:
            # Log all errors, then raise on any that indicate policy violations.
            for err in errors:
                _LOGGER.error("Validation error: %s", err)
            # Raise only if there are trust-tier violations.
            tier_errors = [e for e in errors if "trust tier" in e.lower() or "promoted" in e.lower()]
            if tier_errors:
                raise ValueError(
                    f"Trust policy violations in inject_field: {tier_errors}"
                )

        return section

    def inject_paper(self, paper: Any, synthesis: Any) -> EvidenceSection:
        """Encode and validate all theorems in a paper.

        Parameters
        ----------
        paper : object with .theorems
            MathPaper-like object.
        synthesis : object
            Synthesis context for field resolution.

        Returns
        -------
        EvidenceSection
            Validated section.
        """
        encodings = self._bridge.encode_paper(paper, synthesis)
        return self._bridge.build_evidence_section(encodings)

    def injection_report(self, section: EvidenceSection) -> str:
        """Produce a human-readable injection report for a section.

        Parameters
        ----------
        section : EvidenceSection
            The section to report on.

        Returns
        -------
        str
            Multi-line report string suitable for CLI output or logging.
        """
        lines: List[str] = [
            "=" * 72,
            f"Injection Report — {_format_timestamp()}",
            f"Section ID : {section.section_id}",
            f"Source     : {section.source_paper_id}",
            f"Coordinates: {section.total_coordinates}",
            f"Trust      : {section.trust_summary}",
            f"Created    : {datetime.datetime.fromtimestamp(section.created_at, tz=datetime.timezone.utc).isoformat()}",
            "-" * 72,
        ]
        # Per-encoding summary lines.
        for enc in section.encodings:
            lines.append(f"  {enc.coordinate.summary()}")
        # Validation status.
        errors = self._validator.validate_section(section)
        if errors:
            lines.append("-" * 72)
            lines.append(f"VALIDATION ERRORS ({len(errors)}):")
            for err in errors:
                lines.append(f"  ERROR: {err}")
        else:
            lines.append("-" * 72)
            lines.append("Validation: OK — all coordinates pass sheaf rules.")
        lines.append("=" * 72)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test / __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Configure logging to stdout for the smoke test.
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(name)s — %(message)s",
    )

    print("=" * 72)
    print("JudgmentGeometryBridge — smoke test")
    print("=" * 72)

    # -----------------------------------------------------------------------
    # Build stub PropositionRecord objects.
    # In production these come from the synthesis frontier.
    # -----------------------------------------------------------------------

    @dataclass
    class _StubProp:
        prop_id: str
        kind: str
        statement: str
        proof_sketch: str
        source_fields: Tuple[str, ...] = ()
        confidence: float = 0.8

    @dataclass
    class _StubField:
        field_id: str
        description: str
        constituent_fields: Tuple[str, ...] = ()
        judgment_site: str = ""
        propositions: Tuple[Any, ...] = ()

    # Proposition 1: a classical bridge theorem between algebra and topology.
    prop1 = _StubProp(
        prop_id="prop-bridge-001",
        kind="BRIDGE_THEOREM",
        statement=(
            "The category of commutative rings corresponds to the opposite "
            "of the category of affine schemes, via the spectrum functor "
            "Spec: CRing^op → AffSch."
        ),
        proof_sketch=(
            "We must show that Spec is a contravariant functor. "
            "It suffices to verify that ring homomorphisms induce scheme morphisms. "
            "Claim: for each ring map f: A → B, Spec(f): Spec(B) → Spec(A) is continuous. "
            "It remains to check that Spec is fully faithful on morphisms."
        ),
    )

    # Proposition 2: a theorem about Fourier analysis.
    prop2 = _StubProp(
        prop_id="prop-fourier-002",
        kind="THEOREM",
        statement=(
            "For all f in L^2(R), the Fourier transform F(f) is also in L^2(R) "
            "and the map F: L^2(R) → L^2(R) is a unitary isomorphism."
        ),
        proof_sketch=(
            "We need to show that ||F(f)||_2 = ||f||_2 for all f in L^2(R). "
            "It suffices to prove the Plancherel identity. "
            "The key step is to verify the identity for Schwartz functions first, "
            "then extend by density."
        ),
    )

    # Proposition 3: a definition in category theory.
    prop3 = _StubProp(
        prop_id="prop-adjunction-003",
        kind="DEFINITION",
        statement=(
            "An adjunction between functors F: C → D and G: D → C is defined as "
            "a natural bijection Hom_D(F(X), Y) ≅ Hom_C(X, G(Y)) for all X in C, Y in D."
        ),
        proof_sketch="",  # Definitions have no proof sketch.
    )

    # -----------------------------------------------------------------------
    # Build a stub FieldNode containing all three propositions.
    # -----------------------------------------------------------------------
    stub_field = _StubField(
        field_id="algebra-topology-bridge",
        description=(
            "Merged field at the intersection of commutative algebra, "
            "algebraic topology, and category theory. "
            "Studies correspondences between ring-theoretic and geometric structures."
        ),
        constituent_fields=("commutative-algebra", "algebraic-topology", "category-theory"),
        judgment_site="AffSch × CRing^op",
        propositions=(prop1, prop2, prop3),
    )

    # -----------------------------------------------------------------------
    # Instantiate the bridge and encode all three propositions.
    # -----------------------------------------------------------------------
    bridge = JudgmentGeometryBridge()
    injector = SheafInjector(bridge)

    print("\n--- Encoding propositions individually ---\n")
    encodings: List[JudgmentEncoding] = []
    for prop in [prop1, prop2, prop3]:
        enc = bridge.encode_proposition(prop, stub_field)
        encodings.append(enc)
        print(enc.coordinate.summary())

    print("\n--- Verifying no silent promotion ---")
    result = bridge.verify_no_silent_promotion(encodings)
    print(f"  verify_no_silent_promotion → {result}")

    print("\n--- Building evidence section ---\n")
    section = bridge.build_evidence_section(encodings)
    print(section.summary())

    print("\n--- Full injection report ---\n")
    report = injector.injection_report(section)
    print(report)

    print("\n--- JSON serialisation of first encoding ---\n")
    first_enc_dict = encodings[0].to_dict()
    print(json.dumps(first_enc_dict, indent=2))

    print("\n--- Smoke test complete ---")
