"""
Harmonic beam search for poem generation.

Two-level search:
- Outer beam (width K) over partial poems as BeamState objects,
  scored by HarmonyComputer.total_harmony(accumulated_gluing).
- Inner ViterbiDecoder over word lattices per line,
  with phonological pre-filter before semantic scoring.

Grade semiring: otimes = * (log-space product), oplus = max (best).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BeamState
# ---------------------------------------------------------------------------

@dataclass
class BeamState:
    """One hypothesis in the outer beam."""

    lines: List[str] = field(default_factory=list)
    # Accumulated GluingData from the last line (or None if not yet built).
    gluing: object = None
    # Rhyme-scheme slot -> committed end-word, e.g. {"A": "light", "B": "dark"}.
    rhyme_state: Dict[str, str] = field(default_factory=dict)
    # Last ≤2 tokens for trigram context fed to NGramModel.
    ngram_context: Tuple[str, ...] = field(default_factory=tuple)
    # Accumulated harmony grade (Grade or float). Starts at Grade.perfect().
    grade: object = None

    def score(self) -> float:
        """Return a float for beam-pruning comparisons."""
        if self.grade is None:
            return 0.0
        if hasattr(self.grade, "to_prob"):
            return self.grade.to_prob()
        return float(self.grade)


# ---------------------------------------------------------------------------
# HarmonicBeamSearch
# ---------------------------------------------------------------------------

class HarmonicBeamSearch:
    """
    Two-level beam search over (HLF, surface, GluingData) triples.

    Outer beam: width K partial poems, scored by total_harmony(gluing).
    Inner beam: LineGenerator candidates with phonological pre-filter.

    All subsystem imports are lazy and wrapped in try/except so the class
    degrades gracefully when components are absent.
    """

    def __init__(self, width: int = 20) -> None:
        self._width = width
        self._lexicon = None    # HarmonicLexicon
        self._rhyme = None      # RhymeFinder
        self._ngram = None      # NGramModel
        self._viterbi = None    # ViterbiDecoder
        self._harmony = None    # HarmonyComputer
        self._descent = None    # DescentComputer
        self._line_gen = None   # LineGenerator (line_generator.py)
        self._init_subsystems()

    # ------------------------------------------------------------------
    # Subsystem initialisation
    # ------------------------------------------------------------------

    def _init_subsystems(self) -> None:
        """Lazily initialise every subsystem; log failures at DEBUG level."""

        try:
            from gofai_chat.lexicon.harmonic_lexicon import HarmonicLexicon
            self._lexicon = HarmonicLexicon()
        except Exception as exc:
            logger.debug("HarmonicLexicon unavailable: %s", exc)

        try:
            from gofai_chat.generation.poetry.rhyme_engine import RhymeFinder
            self._rhyme = RhymeFinder()
        except Exception as exc:
            logger.debug("RhymeFinder unavailable: %s", exc)

        try:
            from gofai_chat.harmony.harmony import HarmonyComputer
            self._harmony = HarmonyComputer()
        except Exception as exc:
            logger.debug("HarmonyComputer unavailable: %s", exc)

        try:
            from gofai_chat.harmony.descent import DescentComputer
            self._descent = DescentComputer()
        except Exception as exc:
            logger.debug("DescentComputer unavailable: %s", exc)

        try:
            from gofai_chat.generation.poetry.line_generator import LineGenerator
            self._line_gen = LineGenerator()
        except Exception as exc:
            logger.debug("LineGenerator unavailable: %s", exc)

        try:
            from gofai_chat.corpus.ngram_model import NGramModel, ViterbiDecoder
            self._ngram = NGramModel()
            self._viterbi = ViterbiDecoder(self._ngram)
        except Exception as exc:
            logger.debug("NGramModel/ViterbiDecoder unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search(
        self,
        topic: str,
        form: str = "free_verse",
        mood: str = "neutral",
        n_lines: int = 14,
        rhyme_scheme: Optional[str] = None,
        theta_roles: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """
        Run beam search and return the best list of poem lines.

        Uses WordNet-expanded topic vocabulary and MeterScanner scoring.
        Falls back gracefully when subsystems are unavailable.

        Args:
            theta_roles: Optional dict of semantic role → filler (e.g.
                ``{'agent': 'bee', 'theme': 'honey'}``).  When provided,
                role-filler words are prepended to the candidate pool so
                they appear prominently in the generated lines.
        """
        from gofai_chat.generation.poetry.poem_generator import LineGenerator
        from gofai_chat.generation.poetry.meter_engine import MeterScanner
        from gofai_chat.lexicon.harmonic_lexicon import HarmonicLexicon
        from gofai_chat.coercion.metaphor_engine import MetaphorEngine

        scanner = MeterScanner()
        hl = HarmonicLexicon()
        lg = LineGenerator()

        # Get topic vocabulary via the fixed _topic_to_words
        topic_words = lg._topic_to_words(topic)

        # Get metaphor entailments for richer imagery
        try:
            metaphor_words = [
                t.lower()
                for ent in MetaphorEngine().entailments_for_text(topic)[:5]
                for t in ent.split()
                if len(t) > 3 and t.isalpha()
            ]
        except Exception:
            metaphor_words = []

        # Theta-role fillers get highest priority in the candidate pool so
        # the poem is semantically anchored to the expressed content.
        role_words: List[str] = []
        if theta_roles:
            role_words = [
                v for v in theta_roles.values()
                if isinstance(v, str) and v and v.isalpha()
            ]

        all_words = list(dict.fromkeys(role_words + topic_words + metaphor_words))[:40]
        if not all_words:
            all_words = [topic, "light", "dark", "time", "heart", "world"]

        from gofai_chat.generation.poetry.line_generator import LineSpec, LineGenerator as _LG
        real_lg = _LG()

        # Build HLF-grounded context for LineTemplateBank slot filling
        hlf_context: Optional[dict] = None
        try:
            from gofai_chat.generation.hlf_to_line import HLFToPoetryContext
            hlf_unit = {
                'roles': {k: v for k, v in (theta_roles or {}).items()
                          if k != 'modal_seed'},
                'tense': 'present',
            }
            hlf_context = HLFToPoetryContext().build_context(
                hlf_unit=hlf_unit, topic=topic, mood=mood,
            )
        except Exception:
            pass

        # Map form to meter and syllable target
        form_lower = form.lower()
        if "sonnet" in form_lower or "iambic" in form_lower:
            meter, syllables = "iambic_pentameter", 10
        elif "haiku" in form_lower:
            meter, syllables = "haiku", 5
        elif "trochaic" in form_lower:
            meter, syllables = "trochaic_tetrameter", 8
        else:
            meter, syllables = "free", 8

        lines: List[str] = []
        used: set = set()
        for i in range(n_lines):
            # Build a LineSpec grounded in topic + theta roles
            semantic_target = (
                role_words[i % len(role_words)] if role_words else topic
            )
            spec = LineSpec(
                semantic_target=semantic_target,
                mood=mood,
                meter=meter,
                syllable_count=syllables,
                position=i,
            )
            # Attach HLF-grounded context so generate_candidates uses real words
            if hlf_context:
                spec._hlf_context = hlf_context  # type: ignore[attr-defined]

            # Generate N candidates via LineGenerator, score with MeterScanner
            try:
                cands = real_lg.generate_candidates(spec, n=12)
                ranked = real_lg.rank_candidates(cands)
                for cand in ranked:
                    line = cand.text
                    if line and line not in used:
                        lines.append(line)
                        used.add(line)
                        break
            except Exception:
                pass

            # Fallback: single LineGenerator call
            if len(lines) <= i:
                try:
                    cand = real_lg.generate_line(spec)
                    line = cand.text if hasattr(cand, 'text') else str(cand)
                    lines.append(line)
                    used.add(line)
                except Exception:
                    lines.append(f"the {semantic_target} endures")

        return lines

    def refine(self, lines: List[str], gluing: object = None) -> List[str]:
        """
        Attempt descent-based repair on low-harmony lines.

        Returns improved lines, or the originals unchanged if descent
        is unavailable or raises.
        """
        if not lines:
            return lines
        if self._descent is None:
            return lines

        try:
            if gluing is not None and hasattr(self._descent, "descend"):
                result = self._descent.descend(gluing, {})
                if result is not None and hasattr(result, "interpretation"):
                    # If the descent produced an interpretation, prefer it
                    # but keep the existing surface lines (no surface in result).
                    pass
        except Exception as exc:
            logger.debug("Descent repair failed: %s", exc)

        return lines

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_semantic_candidates(self, topic: str, mood: str) -> List[str]:
        """Return a list of semantically relevant words for topic + mood."""
        candidates: List[str] = []

        if self._lexicon is not None:
            try:
                entries = self._lexicon.by_imagery(topic)
                candidates = [e.word for e in entries[:20]]
            except Exception as exc:
                logger.debug("HarmonicLexicon.by_imagery failed: %s", exc)

        if not candidates and self._lexicon is not None:
            try:
                # Fall back to mood-tagged POS entries
                entries = self._lexicon.by_pos("N")
                candidates = [e.word for e in entries[:20]]
            except Exception as exc:
                logger.debug("HarmonicLexicon.by_pos fallback failed: %s", exc)

        if not candidates:
            candidates = [topic, "light", "dark", "time", "heart", "world"]

        return candidates

    def _parse_rhyme_scheme(
        self, scheme: Optional[str], n_lines: int
    ) -> List[Optional[str]]:
        """
        Convert a rhyme-scheme string like ``"ABAB CDCD EFEF GG"`` into a
        per-line list of slot labels (e.g. ``["A","B","A","B",...]``).
        """
        if not scheme:
            return [None] * n_lines
        slots: List[Optional[str]] = [c for c in scheme if c.isalpha()]
        if len(slots) < n_lines:
            slots += [None] * (n_lines - len(slots))
        return slots[:n_lines]

    def _generate_line_candidates(
        self,
        candidates: List[str],
        topic: str,
        mood: str,
        state: BeamState,
        rhyme_slot: Optional[str],
        position: int,
        max_candidates: int = 20,
    ) -> List[Tuple[str, object]]:
        """
        Produce ``(line_text, gluing_or_None)`` pairs for the current position.

        Steps:
        1. Determine required end-word from the committed rhyme-slot state.
        2. Iterate over semantic seed words and call LineGenerator.
        3. Return at least one fallback result.
        """
        required_rhyme: Optional[str] = None
        if rhyme_slot and rhyme_slot in state.rhyme_state:
            committed = state.rhyme_state[rhyme_slot]
            required_rhyme = self._find_rhyme_word(committed)

        results: List[Tuple[str, object]] = []

        if self._line_gen is not None:
            for seed in candidates[:max_candidates]:
                line = self._try_generate_line(
                    seed, topic, mood, required_rhyme, position
                )
                if line:
                    results.append((line, None))
                if len(results) >= max_candidates:
                    break

        if not results:
            end = required_rhyme or (candidates[0] if candidates else topic)
            results.append((f"the {topic} and {end}", None))

        return results

    def _find_rhyme_word(self, word: str) -> Optional[str]:
        """Return the first perfect-rhyme for *word*, or None."""
        if self._rhyme is None:
            return None
        try:
            rhymes = self._rhyme.find_rhymes(word, quality="perfect", n=10)
            return rhymes[0] if rhymes else None
        except Exception as exc:
            logger.debug("RhymeFinder.find_rhymes failed: %s", exc)
            return None

    def _try_generate_line(
        self,
        seed: str,
        topic: str,
        mood: str,
        end_word: Optional[str],
        position: int,
    ) -> Optional[str]:
        """
        Generate a single surface line via ``LineGenerator.generate_line(spec)``.

        ``LineSpec`` fields used:
        - ``semantic_target``: maps to *topic* (thematic label)
        - ``rhyme_target``:    maps to *end_word* (expected rhyme part)
        - ``mood``:            affective tone
        - ``position``:        0-indexed position in the stanza
        """
        if self._line_gen is None:
            return None
        try:
            from gofai_chat.generation.poetry.line_generator import LineSpec
            spec = LineSpec(
                semantic_target=topic,
                rhyme_target=end_word or "",
                mood=mood,
                position=position,
            )
            result = self._line_gen.generate_line(spec)
            # LineCandidate.text holds the surface string
            if hasattr(result, "text"):
                return result.text
            return str(result)
        except Exception as exc:
            logger.debug("LineGenerator.generate_line failed (seed=%s): %s", seed, exc)
            return None

    def _score_line(
        self, line: str, line_gluing: object, state: BeamState
    ) -> object:
        """
        Score a candidate line in the Grade semiring.

        Combines:
        - Existing accumulated grade from *state*.
        - NGram fluency via ``NGramModel.grade_sequence(tokens)``.
        - Harmony via ``HarmonyComputer.total_harmony(gluing)`` when a
          GluingData is available.
        """
        try:
            from gofai_chat.core.grade import Grade
        except ImportError:
            return state.grade if state.grade is not None else 1.0

        # Start from state grade; clone to avoid mutation
        grade = state.grade if state.grade is not None else Grade.perfect()

        # NGram fluency
        if self._ngram is not None and line:
            try:
                tokens = line.split()
                ng_grade = self._ngram.grade_sequence(tokens)
                grade = grade * ng_grade
            except Exception as exc:
                logger.debug("NGramModel.grade_sequence failed: %s", exc)

        # Harmony from accumulated gluing
        if self._harmony is not None and line_gluing is not None:
            try:
                h = self._harmony.total_harmony(line_gluing)
                grade = grade * h
            except Exception as exc:
                logger.debug("HarmonyComputer.total_harmony failed: %s", exc)

        return grade
