"""Claim Extraction & Contradiction Detection.

Two extraction backends:

* **NLPClaimExtractor** — dependency-parse-based extraction via spaCy.
  Finds subject-verb-object triples, NER entities, nummod-linked
  quantities, temporal expressions, and comparative structures.
  Activated automatically when spaCy is importable.

* **RegexClaimExtractor** — lightweight fallback using hand-tuned regex
  patterns for the same claim families.

Both implement the ``ClaimExtractor`` protocol.  The module-level factory
``make_extractor()`` picks the best available backend.

Contradiction detection uses **semantic field matching**: claims are
grouped into *fields* (numeric, temporal, entity, directional) and
compared within each field using field-specific logic — not just
string comparison.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, ClassVar, Sequence

from jugeo_agents.types import (
    ClaimExtractor,
    Contradiction,
    ContradictionDetector,
    FactualClaim,
    ObstructionKind,
    TrustLevel,
)

__all__ = [
    "NLPClaimExtractor",
    "RegexClaimExtractor",
    "HeuristicContradictionDetector",
    "SubjectMatcher",
    "ClaimNormalizer",
    "ClaimFingerprint",
    "make_extractor",
    "make_detector",
]

# ---------------------------------------------------------------------------
# spaCy availability
# ---------------------------------------------------------------------------

try:
    import spacy
    from spacy.tokens import Doc, Span, Token  # type: ignore[import-untyped]

    _HAS_SPACY = True
except Exception:  # pragma: no cover
    _HAS_SPACY = False
    spacy = None  # type: ignore[assignment]
    Doc = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STOP_WORDS: frozenset[str] = frozenset(
    "a an the is was were are been be being has had have do does did "
    "will would shall should may might can could of in on at to for "
    "with by from and or but not this that these those it its".split()
)

_MAGNITUDE: dict[str, float] = {
    "k": 1_000, "m": 1_000_000, "mm": 1_000_000, "mn": 1_000_000,
    "b": 1_000_000_000, "bn": 1_000_000_000, "t": 1_000_000_000_000,
    "tn": 1_000_000_000_000, "million": 1_000_000, "billion": 1_000_000_000,
    "thousand": 1_000, "trillion": 1_000_000_000_000,
}

_DIRECTIONAL_POS = frozenset({
    "grew", "increased", "rose", "surged", "expanded", "gained",
    "climbed", "soared", "rallied", "accelerated", "improved",
    "growth", "increase", "rise", "surge", "expansion", "gain",
    "up", "higher", "leads", "outperforms", "outperformed",
})

_DIRECTIONAL_NEG = frozenset({
    "fell", "declined", "dropped", "shrank", "decreased", "slipped",
    "plunged", "contracted", "lost", "tumbled", "slumped", "weakened",
    "decline", "decrease", "drop", "fall", "loss", "shrink",
    "down", "lower", "trailing", "underperforms",
})

_FOUNDING_LEMMAS = frozenset({
    "found", "establish", "create", "incorporate", "start", "launch",
    "begin", "open", "form",
})

_FUNDING_LEMMAS = frozenset({
    "raise", "secure", "close", "receive", "attract", "obtain",
})

# ---------------------------------------------------------------------------
# Number parsing (shared)
# ---------------------------------------------------------------------------

def parse_number(text: str) -> float | None:
    """Try to parse a numeric value from *text*, handling currency & suffixes."""
    text = text.strip().lstrip("$\u20ac\u00a3\u00a5 ").replace(",", "")
    m = re.match(r"^([\d.]+)\s*([A-Za-z]*)", text)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    suffix = m.group(2).lower().rstrip(".")
    if suffix in _MAGNITUDE:
        val *= _MAGNITUDE[suffix]
    return val


def is_year(n: float) -> bool:
    return n == int(n) and 1800 <= n <= 2200


# ===================================================================
# 1.  NLP CLAIM EXTRACTOR  (spaCy dependency-parse backend)
# ===================================================================

class NLPClaimExtractor:
    """Extract claims using spaCy dependency parsing and NER.

    For every sentence the extractor runs five extraction passes:

    1. **SVO triples** -- walk the dependency tree from each ROOT verb
       to find (subject, verb, object/complement) triples.
    2. **Numeric facts** -- find every ``nummod`` dependency and trace
       upward to its governing noun and the noun's subject.
    3. **Named entity dates** -- DATE entities attached to subjects via
       dependency arcs produce temporal claims.
    4. **Monetary / quantity entities** -- MONEY, CARDINAL, PERCENT ents.
    5. **Comparative / directional** -- verbs in the directional lexicons.
    """

    def __init__(
        self,
        model: str = "en_core_web_sm",
        default_trust: TrustLevel = TrustLevel.WEAK_MODEL_GENERATED,
    ) -> None:
        if not _HAS_SPACY:
            raise ImportError("spaCy is required: pip install jugeo-agents[nlp]")
        self._nlp = spacy.load(model)
        self._default_trust = default_trust

    def extract(self, text: str, agent_id: str = "") -> list[FactualClaim]:
        doc = self._nlp(text)
        claims: list[FactualClaim] = []
        seen: set[str] = set()

        for sent in doc.sents:
            for claim in self._extract_from_sent(sent, agent_id, text):
                fp = _fingerprint(claim)
                if fp not in seen:
                    seen.add(fp)
                    claims.append(claim)

        return claims

    # ---- per-sentence passes -------------------------------------------

    def _extract_from_sent(
        self, sent: Span, agent_id: str, full_text: str,
    ) -> list[FactualClaim]:
        results: list[FactualClaim] = []
        span = (sent.start_char, sent.end_char)

        results.extend(self._svo_pass(sent, agent_id, span))
        results.extend(self._numeric_pass(sent, agent_id, span))
        results.extend(self._date_pass(sent, agent_id, span))
        results.extend(self._money_pass(sent, agent_id, span))
        results.extend(self._directional_pass(sent, agent_id, span))

        return results

    # -- Pass 1: SVO triples ----------------------------------------------

    def _svo_pass(
        self, sent: Span, agent_id: str, span: tuple[int, int],
    ) -> list[FactualClaim]:
        claims: list[FactualClaim] = []
        for tok in sent:
            if tok.dep_ != "ROOT" or tok.pos_ != "VERB":
                continue

            subj = self._find_subject(tok)
            if not subj:
                continue

            obj_text, predicate = self._find_object_and_predicate(tok)
            if not obj_text:
                continue

            claims.append(FactualClaim(
                text=sent.text.strip(),
                subject=subj,
                predicate=predicate,
                value=obj_text,
                source_agent=agent_id,
                source_text_span=span,
                trust=self._default_trust,
            ))
        return claims

    def _find_subject(self, verb: Token) -> str:
        """Walk from *verb* to find the nominal subject."""
        for child in verb.children:
            if child.dep_ in ("nsubj", "nsubjpass"):
                return self._expand_noun(child)
        if verb.dep_ in ("relcl", "advcl", "acl"):
            return self._expand_noun(verb.head)
        return ""

    def _expand_noun(self, tok: Token) -> str:
        """Expand a token into its full noun phrase."""
        lefts = [c for c in tok.lefts
                 if c.dep_ in ("compound", "flat", "amod", "det")]
        phrase_tokens = sorted(lefts + [tok], key=lambda t: t.i)
        text = " ".join(t.text for t in phrase_tokens)
        for det in ("the ", "a ", "an ", "The ", "A ", "An "):
            if text.startswith(det):
                text = text[len(det):]
                break
        return text.strip()

    def _find_object_and_predicate(self, verb: Token) -> tuple[str, str]:
        """Find the direct object or prepositional complement."""
        lemma = verb.lemma_.lower()

        for child in verb.children:
            if child.dep_ in ("dobj", "attr"):
                obj = self._expand_noun_with_numbers(child)
                return obj, lemma

        for child in verb.children:
            if child.dep_ == "prep":
                for pobj in child.children:
                    if pobj.dep_ == "pobj":
                        val = self._expand_noun_with_numbers(pobj)
                        return val, f"{lemma}_{child.text}"

        for child in verb.children:
            if child.dep_ == "agent":
                for pobj in child.children:
                    if pobj.dep_ == "pobj":
                        return self._expand_noun(pobj), f"{lemma}_by"

        return "", lemma

    def _expand_noun_with_numbers(self, tok: Token) -> str:
        """Expand noun phrase including nummod children."""
        parts: list[Token] = []
        for child in tok.subtree:
            if child.dep_ in ("compound", "flat", "amod", "nummod", "quantmod",
                               "det", "prep", "pobj", "punct"):
                parts.append(child)
            elif child == tok:
                parts.append(child)
        parts = sorted(set(parts), key=lambda t: t.i)
        return " ".join(t.text for t in parts).strip()

    # -- Pass 2: numeric facts (nummod) -----------------------------------

    def _numeric_pass(
        self, sent: Span, agent_id: str, span: tuple[int, int],
    ) -> list[FactualClaim]:
        claims: list[FactualClaim] = []
        for tok in sent:
            if tok.dep_ != "nummod":
                continue

            number_text = tok.text
            head_noun = tok.head

            subject = self._subject_for_noun(head_noun, sent)
            if not subject:
                continue

            predicate = head_noun.lemma_.lower()
            verb = self._governing_verb(head_noun)
            if verb:
                predicate = f"{verb.lemma_.lower()}_{predicate}"

            val = f"{number_text} {head_noun.text}"
            parsed = parse_number(number_text)

            claims.append(FactualClaim(
                text=sent.text.strip(),
                subject=subject,
                predicate=predicate,
                value=val.strip(),
                source_agent=agent_id,
                source_text_span=span,
                trust=self._default_trust,
                metadata={"parsed_number": parsed} if parsed is not None else {},
            ))
        return claims

    def _subject_for_noun(self, noun: Token, sent: Span) -> str:
        current = noun
        for _ in range(10):
            if current.dep_ in ("nsubj", "nsubjpass"):
                return self._expand_noun(current)
            if current.dep_ == "ROOT":
                return self._find_subject(current) or self._expand_noun(current)
            if current.head == current:
                break
            current = current.head

        for ent in sent.ents:
            if ent.label_ in ("ORG", "PERSON", "GPE"):
                return ent.text
        return ""

    def _governing_verb(self, tok: Token) -> Token | None:
        current = tok
        for _ in range(10):
            if current.pos_ == "VERB":
                return current
            if current.head == current:
                break
            current = current.head
        return None

    # -- Pass 3: DATE entities --------------------------------------------

    def _date_pass(
        self, sent: Span, agent_id: str, span: tuple[int, int],
    ) -> list[FactualClaim]:
        claims: list[FactualClaim] = []
        for ent in sent.ents:
            if ent.label_ != "DATE":
                continue

            subject = ""
            root_tok = ent.root
            verb = self._governing_verb(root_tok)
            if verb:
                subject = self._find_subject(verb)

            if not subject:
                for other_ent in sent.ents:
                    if other_ent.label_ in ("ORG", "PERSON", "GPE"):
                        subject = other_ent.text
                        break

            if not subject:
                continue

            predicate = "date"
            if verb:
                lemma = verb.lemma_.lower()
                if lemma in _FOUNDING_LEMMAS:
                    predicate = "founded_in"
                elif lemma in _FUNDING_LEMMAS:
                    predicate = "funding_date"
                else:
                    predicate = f"{lemma}_date"

            claims.append(FactualClaim(
                text=sent.text.strip(),
                subject=subject,
                predicate=predicate,
                value=ent.text,
                source_agent=agent_id,
                source_text_span=span,
                trust=self._default_trust,
            ))
        return claims

    # -- Pass 4: MONEY / CARDINAL entities --------------------------------

    def _money_pass(
        self, sent: Span, agent_id: str, span: tuple[int, int],
    ) -> list[FactualClaim]:
        claims: list[FactualClaim] = []
        for ent in sent.ents:
            if ent.label_ not in ("MONEY", "CARDINAL", "PERCENT", "QUANTITY"):
                continue
            parsed = parse_number(ent.text)
            if parsed is not None and is_year(parsed):
                continue

            subject = ""
            verb = self._governing_verb(ent.root)
            if verb:
                subject = self._find_subject(verb)
            if not subject:
                for other_ent in sent.ents:
                    if other_ent.label_ in ("ORG", "PERSON", "GPE"):
                        subject = other_ent.text
                        break
            if not subject:
                continue

            predicate = "amount"
            if verb:
                predicate = verb.lemma_.lower()

            claims.append(FactualClaim(
                text=sent.text.strip(),
                subject=subject,
                predicate=predicate,
                value=ent.text,
                source_agent=agent_id,
                source_text_span=span,
                trust=self._default_trust,
                metadata={"parsed_number": parsed} if parsed is not None else {},
            ))
        return claims

    # -- Pass 5: directional claims ---------------------------------------

    def _directional_pass(
        self, sent: Span, agent_id: str, span: tuple[int, int],
    ) -> list[FactualClaim]:
        claims: list[FactualClaim] = []
        for tok in sent:
            if tok.pos_ != "VERB":
                continue
            lemma = tok.lemma_.lower()
            text_lower = tok.text.lower()

            direction = None
            if lemma in _DIRECTIONAL_POS or text_lower in _DIRECTIONAL_POS:
                direction = "positive"
            elif lemma in _DIRECTIONAL_NEG or text_lower in _DIRECTIONAL_NEG:
                direction = "negative"

            if direction is None:
                continue

            subject = self._find_subject(tok)
            if not subject:
                continue

            claims.append(FactualClaim(
                text=sent.text.strip(),
                subject=subject,
                predicate=f"direction_{direction}",
                value=tok.text,
                source_agent=agent_id,
                source_text_span=span,
                trust=self._default_trust,
                metadata={"direction": direction},
            ))
        return claims


# ===================================================================
# 2.  REGEX CLAIM EXTRACTOR  (fallback when spaCy unavailable)
# ===================================================================

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class _Pat:
    regex: re.Pattern[str]
    predicate: str
    subject_group: int = 1
    value_group: int = 2


_REGEX_PATTERNS: list[_Pat] = [
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)(?:'s)?\s+"
        r"(?:total\s+|annual\s+|quarterly\s+)?"
        r"(?:revenue|sales|income|profit|earnings|turnover)"
        r"\s+(?:was|is|were|reached|totaled|stood at|amounted to)\s+"
        r"(?P<v>[\$\u20ac\u00a3\u00a5]?\s?[\d,]+(?:\.\d+)?(?:\s?[A-Za-z]+)?)",
        re.I), "revenue", 1, 2),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+"
        r"(?:has|had|employs|employed|with|have|having)\s+"
        r"(?P<v>[\d,]+)\s+(?:employees|staff|workers|people)", re.I),
        "employee_count"),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+(?:has\s+)?(?:grown|grew|expanded)\s+"
        r"to\s+(?P<v>[\d,]+)\s+(?:employees|staff|workers|people)", re.I),
        "employee_count"),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+"
        r"(?:was|is|were)\s+(?:valued at|worth)\s+"
        r"(?P<v>[\$\u20ac\u00a3\u00a5]?\s?[\d,]+(?:\.\d+)?(?:\s?[A-Za-z]+)?)",
        re.I), "valuation"),
    _Pat(re.compile(
        r"(?:the\s+)?(?P<s>[\w ]{1,40}?market)\s+"
        r"(?:was|is|were)\s+(?:valued at|worth|estimated at)\s+"
        r"(?P<v>[\$\u20ac\u00a3\u00a5]?\s?[\d,]+(?:\.\d+)?(?:\s?[A-Za-z]+)?)",
        re.I), "valuation"),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+(?:has\s+)?"
        r"(?:raised|secured|closed)\s+"
        r"(?P<v>[\$\u20ac\u00a3\u00a5]?\s?[\d,]+(?:\.\d+)?(?:\s?[A-Za-z]+)?)"
        r"(?:\s+in)?\s*(?:total\s+)?(?:funding|investment|round|financing)?",
        re.I), "funding_raised"),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?),?\s+"
        r"(?:was\s+)?founded\s+in\s+(?P<v>\d{4})", re.I),
        "founded_in"),
    _Pat(re.compile(
        r"(?:since|after)\s+(?:its|their)\s+founding\s+in\s+(?P<v>\d{4})"
        r",?\s+(?P<s>[A-Z][\w&' ]{0,60}?)\s", re.I),
        "founded_in", 2, 1),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+"
        r"(?:grew|increased|rose|declined|fell|dropped)\s+"
        r"(?:by\s+)?(?P<v>\d+(?:\.\d+)?%)", re.I),
        "percentage_change"),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+.*?"
        r"market\s+cap(?:italization)?\s+(?:of|was|is)\s+"
        r"(?P<v>[\$\u20ac\u00a3\u00a5]?\s?[\d,]+(?:\.\d+)?(?:\s?[A-Za-z]+)?)",
        re.I), "market_cap"),
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+"
        r"(?:achieved|reached|attained|hit|surpassed)\s+"
        r"(?P<v>[\d,]+(?:\.\d+)?)\s+(?:\w+)", re.I),
        "achieved"),
    # founded by
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+"
        r"(?:was\s+)?founded\s+by\s+(?P<v>[A-Z][\w ]{1,60})", re.I),
        "founded_by"),
    # executive / CEO / CTO
    _Pat(re.compile(
        r"(?P<v>[A-Z][\w ]{1,40}?)\s+is\s+(?:the\s+)?(?:CEO|CTO|CFO|COO|President|Chairman)"
        r"\s+of\s+(?P<s>[A-Z][\w&' ]{0,60}?)\b", re.I),
        "has_executive", 2, 1),
    # comparative
    _Pat(re.compile(
        r"(?P<s>[A-Z][\w&' ]{0,60}?)\s+is\s+"
        r"(?P<v>(?:larger|smaller|bigger|faster|slower|more|less|better|worse)"
        r"(?:\s+than\s+\w[\w ]{0,40})?)", re.I),
        "comparative"),
]


class RegexClaimExtractor:
    """Fallback claim extractor using regex patterns."""

    def __init__(
        self, default_trust: TrustLevel = TrustLevel.WEAK_MODEL_GENERATED,
    ) -> None:
        self._default_trust = default_trust

    def extract(self, text: str, agent_id: str = "") -> list[FactualClaim]:
        sentences = _split_sentences(text)
        claims: list[FactualClaim] = []
        seen: set[str] = set()
        offset = 0

        for sentence in sentences:
            start = text.find(sentence, offset)
            if start == -1:
                start = offset
            end = start + len(sentence)

            for pat in _REGEX_PATTERNS:
                m = pat.regex.search(sentence)
                if m is None:
                    continue
                try:
                    subj = m.group(pat.subject_group).strip()
                except (IndexError, AttributeError):
                    continue
                try:
                    val = m.group(pat.value_group).strip()
                except (IndexError, AttributeError):
                    continue

                claim = FactualClaim(
                    text=sentence.strip(),
                    subject=subj,
                    predicate=pat.predicate,
                    value=val,
                    source_agent=agent_id,
                    source_text_span=(start, end),
                    trust=self._default_trust,
                )
                fp = _fingerprint(claim)
                if fp not in seen:
                    seen.add(fp)
                    claims.append(claim)

            # Always extract raw numeric facts as fallback
            claims.extend(
                self._extract_raw_numbers(sentence, agent_id, (start, end), seen)
            )
            offset = end
        return claims

    def _extract_raw_numbers(
        self, sentence: str, agent_id: str, span: tuple[int, int],
        seen: set[str],
    ) -> list[FactualClaim]:
        results: list[FactualClaim] = []
        for m in re.finditer(
            r"([\$\u20ac\u00a3\u00a5]?\s?[\d,]+(?:\.\d+)?)\s*([A-Za-z]*)",
            sentence,
        ):
            raw = m.group(1).strip().lstrip("$\u20ac\u00a3\u00a5 ")
            suffix = m.group(2).lower().rstrip(".")
            try:
                val = float(raw.replace(",", ""))
            except ValueError:
                continue
            if suffix in _MAGNITUDE:
                val *= _MAGNITUDE[suffix]
            if val == 0:
                continue

            claim = FactualClaim(
                text=sentence.strip(),
                subject="",
                predicate="numeric_raw",
                value=str(val),
                source_agent=agent_id,
                source_text_span=span,
                trust=self._default_trust,
                metadata={"parsed_number": val},
            )
            fp = f"rawnum_{val}_{span[0]}"
            if fp not in seen:
                seen.add(fp)
                results.append(claim)
        return results


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text.strip())
    result: list[str] = []
    for part in parts:
        for sub in re.split(r"\n+", part):
            sub = sub.strip()
            if sub:
                result.append(sub)
    return result


# ===================================================================
# 3.  CLAIM NORMALIZER
# ===================================================================

class ClaimNormalizer:
    """Normalise claim values for comparison."""

    @staticmethod
    def normalize(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def normalize_number(text: str) -> float | None:
        return parse_number(text)


# ===================================================================
# 4.  CLAIM FINGERPRINT
# ===================================================================

class ClaimFingerprint:
    @staticmethod
    def fingerprint(claim: FactualClaim) -> str:
        return _fingerprint(claim)


def _fingerprint(claim: FactualClaim) -> str:
    subj = ClaimNormalizer.normalize(claim.subject)
    pred = ClaimNormalizer.normalize(claim.predicate)
    val = ClaimNormalizer.normalize(claim.value)
    key = f"{subj}||{pred}||{val}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ===================================================================
# 5.  SUBJECT MATCHER
# ===================================================================

class SubjectMatcher:
    """Match claims across two lists by subject similarity."""

    def __init__(
        self, keyword_threshold: float = 0.4, sequence_threshold: float = 0.7,
    ) -> None:
        self.keyword_threshold = keyword_threshold
        self.sequence_threshold = sequence_threshold

    def match(
        self, claims_a: list[FactualClaim], claims_b: list[FactualClaim],
    ) -> list[tuple[FactualClaim, FactualClaim]]:
        pairs: list[tuple[FactualClaim, FactualClaim]] = []
        for ca in claims_a:
            for cb in claims_b:
                if self._subjects_match(ca, cb):
                    pairs.append((ca, cb))
        return pairs

    def _subjects_match(self, a: FactualClaim, b: FactualClaim) -> bool:
        sa = ClaimNormalizer.normalize(a.subject)
        sb = ClaimNormalizer.normalize(b.subject)

        if not sa and not sb:
            return _sentence_overlap(a.text, b.text) > 0.3
        if not sa or not sb:
            full = sa or sb
            other_text = ClaimNormalizer.normalize(b.text if sa else a.text)
            return full in other_text

        if sa == sb:
            return True
        if sa in sb or sb in sa:
            return True
        if self._keyword_overlap(sa, sb) >= self.keyword_threshold:
            return True
        if SequenceMatcher(None, sa, sb).ratio() >= self.sequence_threshold:
            return True
        return False

    @staticmethod
    def _keyword_overlap(a: str, b: str) -> float:
        ta = {w for w in a.split() if w not in _STOP_WORDS}
        tb = {w for w in b.split() if w not in _STOP_WORDS}
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)


def _sentence_overlap(a: str, b: str) -> float:
    wa = set(ClaimNormalizer.normalize(a).split())
    wb = set(ClaimNormalizer.normalize(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ===================================================================
# 6.  CONTRADICTION DETECTOR
# ===================================================================

class HeuristicContradictionDetector:
    """Detect contradictions using semantic-field-aware comparison.

    Instead of requiring predicate compatibility, this detector:
    1. Matches claims by subject.
    2. Extracts ALL numbers from both claims' text.
    3. Classifies the numbers (years vs quantities) and compares.
    4. Checks directional sentiment.
    5. Checks entity contradictions.
    """

    def __init__(
        self,
        *,
        subject_matcher: SubjectMatcher | None = None,
        numeric_tolerance: float = 0.05,
    ) -> None:
        self._matcher = subject_matcher or SubjectMatcher()
        self._numeric_tolerance = numeric_tolerance

    def detect(
        self, claims_a: list[FactualClaim], claims_b: list[FactualClaim],
    ) -> list[Contradiction]:
        pairs = self._matcher.match(claims_a, claims_b)
        contradictions: list[Contradiction] = []
        seen: set[str] = set()

        for ca, cb in pairs:
            for c in self._compare_pair(ca, cb):
                # Content-based dedup: same kind + same explanation = same contradiction
                key = f"{c.kind.name}::{c.explanation}"
                if key not in seen:
                    seen.add(key)
                    contradictions.append(c)

        return contradictions

    def _compare_pair(
        self, ca: FactualClaim, cb: FactualClaim,
    ) -> list[Contradiction]:
        results: list[Contradiction] = []

        c = self._check_years(ca, cb)
        if c:
            results.append(c)

        c = self._check_quantities(ca, cb)
        if c:
            results.append(c)

        c = self._check_direction(ca, cb)
        if c:
            results.append(c)

        c = self._check_entities(ca, cb)
        if c:
            results.append(c)

        return results

    # -- Year comparison --------------------------------------------------

    def _check_years(
        self, ca: FactualClaim, cb: FactualClaim,
    ) -> Contradiction | None:
        years_a = _extract_years(ca.text)
        years_b = _extract_years(cb.text)
        if not years_a or not years_b or years_a == years_b:
            return None

        context_a = _temporal_context(ca)
        context_b = _temporal_context(cb)
        if context_a and context_b and context_a == context_b:
            return Contradiction(
                claim_a=ca, claim_b=cb,
                agent_a=ca.source_agent, agent_b=cb.source_agent,
                kind=ObstructionKind.TEMPORAL_CONTRADICTION,
                confidence=0.9,
                explanation=(
                    f"Year mismatch for '{ca.subject}' ({context_a}): "
                    f"{sorted(years_a)} vs {sorted(years_b)}."
                ),
                repair_hint=(
                    f"Verify the {context_a} year for '{ca.subject}'. "
                    f"Agent '{ca.source_agent}' says {sorted(years_a)}, "
                    f"agent '{cb.source_agent}' says {sorted(years_b)}."
                ),
            )
        return None

    # -- Quantity comparison -----------------------------------------------

    def _check_quantities(
        self, ca: FactualClaim, cb: FactualClaim,
    ) -> Contradiction | None:
        nums_a = _extract_quantities(ca)
        nums_b = _extract_quantities(cb)
        if not nums_a or not nums_b:
            return None

        for na, ctx_a in nums_a:
            for nb, ctx_b in nums_b:
                if not _contexts_comparable(ctx_a, ctx_b):
                    continue
                denom = max(abs(na), abs(nb))
                if denom == 0:
                    continue
                rel_diff = abs(na - nb) / denom
                if rel_diff > self._numeric_tolerance:
                    return Contradiction(
                        claim_a=ca, claim_b=cb,
                        agent_a=ca.source_agent, agent_b=cb.source_agent,
                        kind=ObstructionKind.QUANTITATIVE_CONTRADICTION,
                        confidence=min(1.0, rel_diff),
                        explanation=(
                            f"Numeric mismatch for '{ca.subject}' ({ctx_a}): "
                            f"{na:g} vs {nb:g} (diff {rel_diff:.0%})."
                        ),
                        repair_hint=(
                            f"Verify the {ctx_a} for '{ca.subject}'. "
                            f"Agent '{ca.source_agent}' says {na:g}, "
                            f"agent '{cb.source_agent}' says {nb:g}."
                        ),
                    )
        return None

    # -- Directional comparison -------------------------------------------

    def _check_direction(
        self, ca: FactualClaim, cb: FactualClaim,
    ) -> Contradiction | None:
        dir_a = _sentence_direction(ca.text)
        dir_b = _sentence_direction(cb.text)
        if dir_a and dir_b and dir_a != dir_b:
            return Contradiction(
                claim_a=ca, claim_b=cb,
                agent_a=ca.source_agent, agent_b=cb.source_agent,
                kind=ObstructionKind.DIRECTIONAL_CONTRADICTION,
                confidence=0.75,
                explanation=(
                    f"Directional mismatch for '{ca.subject}': "
                    f"'{ca.source_agent}' says {dir_a}, "
                    f"'{cb.source_agent}' says {dir_b}."
                ),
                repair_hint=f"Check trend direction for '{ca.subject}'.",
            )
        return None

    # -- Entity comparison ------------------------------------------------

    def _check_entities(
        self, ca: FactualClaim, cb: FactualClaim,
    ) -> Contradiction | None:
        if ca.predicate == cb.predicate and ca.predicate not in (
            "has", "is_a", "numeric_raw", "amount", "date", "quantity",
        ):
            va = ClaimNormalizer.normalize(ca.value)
            vb = ClaimNormalizer.normalize(cb.value)
            if va and vb and va != vb:
                sim = SequenceMatcher(None, va, vb).ratio()
                if sim < 0.7:
                    return Contradiction(
                        claim_a=ca, claim_b=cb,
                        agent_a=ca.source_agent, agent_b=cb.source_agent,
                        kind=ObstructionKind.ENTITY_CONTRADICTION,
                        confidence=round(1.0 - sim, 2),
                        explanation=(
                            f"Value mismatch for '{ca.subject}' "
                            f"({ca.predicate}): '{ca.value}' vs '{cb.value}'."
                        ),
                        repair_hint=f"Verify '{ca.predicate}' for '{ca.subject}'.",
                    )
        return None


# ---------------------------------------------------------------------------
# Extraction helpers for the detector
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"\b(1[89]\d{2}|2[01]\d{2})\b")
_NUMBER_RE = re.compile(r"[\$\u20ac\u00a3\u00a5]?\s*([\d,]+(?:\.\d+)?)\s*([A-Za-z]*)")


def _extract_years(text: str) -> set[int]:
    return {int(m.group(1)) for m in _YEAR_RE.finditer(text)}


def _temporal_context(claim: FactualClaim) -> str:
    low = claim.text.lower()
    for kw in ("founded", "founding", "established", "incorporated", "started"):
        if kw in low:
            return "founding"
    for kw in ("launched", "released", "introduced", "announced"):
        if kw in low:
            return "launch"
    if claim.predicate and "found" in claim.predicate:
        return "founding"
    return "event"


def _extract_quantities(claim: FactualClaim) -> list[tuple[float, str]]:
    """Extract (number, context_word) pairs, excluding years."""
    results: list[tuple[float, str]] = []

    if "parsed_number" in claim.metadata and claim.metadata["parsed_number"] is not None:
        pn = claim.metadata["parsed_number"]
        if not is_year(pn):
            ctx = _quantity_context(claim.text, pn)
            results.append((pn, ctx))

    parsed = parse_number(claim.value)
    if parsed is not None and not is_year(parsed):
        ctx = _quantity_context(claim.text, parsed)
        if not any(abs(r[0] - parsed) < 0.01 for r in results):
            results.append((parsed, ctx))

    for m in _NUMBER_RE.finditer(claim.text):
        raw = m.group(1).replace(",", "")
        suffix = m.group(2).lower().rstrip(".")
        try:
            val = float(raw)
        except ValueError:
            continue
        if suffix in _MAGNITUDE:
            val *= _MAGNITUDE[suffix]
        if is_year(val) or val == 0:
            continue
        ctx = _quantity_context(claim.text, val)
        if not any(abs(r[0] - val) < 0.01 for r in results):
            results.append((val, ctx))

    # Post-filter: remove raw base numbers that are scale-factors of
    # another already-extracted magnitude (e.g., 3.8 when 3.8e6 exists).
    if len(results) > 1:
        magnitudes = set(_MAGNITUDE.values())
        filtered: list[tuple[float, str]] = []
        values = {r[0] for r in results}
        for val, ctx in results:
            is_base = any(
                abs(val * mag - other) < 0.01
                for mag in magnitudes
                for other in values
                if other != val
            )
            if not is_base:
                filtered.append((val, ctx))
        results = filtered or results  # keep at least one

    return results


def _quantity_context(text: str, number: float) -> str:
    low = text.lower()
    if any(w in low for w in ("employee", "staff", "worker", "people", "headcount")):
        return "employee_count"
    if any(w in low for w in ("revenue", "sales", "income", "profit", "earnings")):
        return "revenue"
    if any(w in low for w in ("funding", "raised", "investment", "round")):
        return "funding"
    if any(w in low for w in ("market cap", "capitalization", "valuation", "valued", "worth")):
        return "valuation"
    if any(w in low for w in ("qubit", "processor", "chip")):
        return "technical_spec"
    if any(w in low for w in ("market", "tam", "addressable")):
        return "market_size"
    if "%" in text or "percent" in low:
        return "percentage"
    return "quantity"


def _contexts_comparable(a: str, b: str) -> bool:
    if a == b:
        return True
    if a == "quantity" or b == "quantity":
        return True
    return False


def _sentence_direction(text: str) -> str | None:
    words = set(text.lower().split())
    has_pos = bool(words & _DIRECTIONAL_POS)
    has_neg = bool(words & _DIRECTIONAL_NEG)
    if has_pos and not has_neg:
        return "positive"
    if has_neg and not has_pos:
        return "negative"
    return None


# ===================================================================
# 7.  FACTORY FUNCTIONS
# ===================================================================

def make_extractor(
    prefer_nlp: bool = True,
    spacy_model: str = "en_core_web_sm",
    default_trust: TrustLevel = TrustLevel.WEAK_MODEL_GENERATED,
) -> NLPClaimExtractor | RegexClaimExtractor:
    """Create the best available claim extractor.

    Uses spaCy NLP backend when available, falls back to regex.
    """
    if prefer_nlp and _HAS_SPACY:
        try:
            return NLPClaimExtractor(model=spacy_model, default_trust=default_trust)
        except OSError:
            pass  # model not installed
    return RegexClaimExtractor(default_trust=default_trust)


def make_detector(
    numeric_tolerance: float = 0.05,
) -> HeuristicContradictionDetector:
    return HeuristicContradictionDetector(numeric_tolerance=numeric_tolerance)
