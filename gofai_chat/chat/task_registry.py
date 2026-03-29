"""Poetic task recognition and routing registry.

Maps natural-language utterances to typed PoeticTask objects using
frozenset token-set matching — no regex in the core classify path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Dict, Any


class TaskKind(Enum):
    STYLE_BLEND              = auto()
    STYLE_TRANSLATE          = auto()
    STYLE_CONSTRAIN          = auto()
    STYLE_COUNTERFACTUAL     = auto()
    STYLE_TOPIC_ANACHRONISM  = auto()
    IMITATE_WORK             = auto()
    PERSPECTIVE_SHIFT        = auto()
    RHYME_CONSTRAINT         = auto()
    SYLLABLE_CONSTRAINT      = auto()
    ALLITERATION             = auto()
    LIPOGRAM                 = auto()
    ACROSTIC                 = auto()
    STRUCTURAL_SEQUENCE      = auto()
    SYNTACTIC_CONSTRAINT     = auto()
    GENERATE                 = auto()
    PROSE_TO_POEM            = auto()
    REVISE                   = auto()
    FORM_CONVERT             = auto()
    EXTEND                   = auto()
    COMPRESS                 = auto()
    TOPIC_TRANSFER           = auto()
    EMBODY_CONCEPT           = auto()
    DEMONSTRATE_FORM         = auto()
    ARGUMENTATIVE            = auto()
    CROSS_DOMAIN_BLEND       = auto()
    CONCRETE_POEM            = auto()
    EKPHRASIS                = auto()
    GENRE_BLEND              = auto()
    BIDIRECTIONAL            = auto()
    DIALOGUE_POEM            = auto()
    EXPLANATION              = auto()
    COMPARISON               = auto()
    EXPLAIN_THEN_DEMONSTRATE = auto()
    INFLUENCE_TRACE          = auto()
    AFFECTIVE_GOAL           = auto()


@dataclass
class PoeticTask:
    kind: TaskKind
    topic: str = ""
    form: str = "free_verse"
    mood: str = "neutral"
    poets: List[str] = field(default_factory=list)
    blend_weights: List[float] = field(default_factory=list)
    rhyme_target: str = ""
    forbidden_words: List[str] = field(default_factory=list)
    forbidden_letters: List[str] = field(default_factory=list)
    forbidden_pos: List[str] = field(default_factory=list)
    required_device: str = ""
    acrostic_word: str = ""
    syllable_limit: int = 0
    syllable_sequence: str = ""
    line_syntax: str = ""
    concept: str = ""
    source_domain: str = ""
    target_domain: str = ""
    shape: str = ""
    voice_a: str = ""
    voice_b: str = ""
    prose_input: str = ""
    mood_delta: float = 0.0
    new_agent: str = ""
    stanzas_to_add: int = 0
    thesis: str = ""
    artwork: str = ""
    constraint_weight_overrides: Dict[str, float] = field(default_factory=dict)
    raw: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Token-set matching constants
# ---------------------------------------------------------------------------

_POET_NAMES = frozenset({
    'shakespeare', 'dickinson', 'whitman', 'keats', 'eliot', 'hopkins',
    'neruda', 'rilke', 'plath', 'cummings', 'bishop', 'lowell', 'milton',
    'blake', 'yeats', 'shelley', 'byron', 'tennyson', 'browning', 'donne',
    'marvell', 'spenser', 'virgil', 'dante', 'sappho', 'rumi', 'hafiz',
    'frost', 'hughes', 'angelou', 'ginsberg', 'sexton', 'oliver', 'heaney',
    'walcott', 'rich', 'brooks', 'pope', 'lorca', 'neruda', 'borges',
})

_STYLE_BLEND_TRIGGERS     = frozenset({'halfway', 'between', 'blend', 'mix', 'combine', 'fuse', 'merge'})
_STYLE_LIKE_TRIGGERS      = frozenset({'like', 'style', 'manner', 'voice', 'way'})
_COUNTERFACTUAL_TRIGGERS  = frozenset({'if', 'imagine', 'what', 'were', 'was', 'had', 'been', 'suppose'})
_RHYME_TRIGGERS           = frozenset({'rhymes', 'rhyme', 'rhyming', 'rhymes'})
_CONSTRAINT_TRIGGERS      = frozenset({'only', 'without', 'no', 'never', 'using', 'forbidden', 'avoid'})
_SYLLABLE_TRIGGERS        = frozenset({'syllables', 'monosyllable', 'monosyllabic', 'syllable', 'monosyllabic'})
_FORM_TRIGGERS            = frozenset({
    'haiku', 'sonnet', 'villanelle', 'ode', 'elegy', 'ballad', 'limerick',
    'ghazal', 'pantoum', 'sestina', 'couplet', 'quatrain',
})
_PROSE_TRIGGERS           = frozenset({'turn', 'convert', 'poeticize', 'transform', 'express', 'make'})
_REVISE_TRIGGERS          = frozenset({
    'darker', 'lighter', 'funnier', 'sadder', 'angrier', 'calmer',
    'make', 'more', 'less', 'revise', 'change', 'alter', 'rewrite', 'redo',
})
_EXTEND_TRIGGERS          = frozenset({'extend', 'add', 'more', 'continue', 'another', 'stanza', 'stanzas', 'lines'})
_COMPRESS_TRIGGERS        = frozenset({'compress', 'shorten', 'condense', 'summarize', 'distill'})
_ACROSTIC_TRIGGERS        = frozenset({'acrostic', 'spells', 'spelling'})
_LIPOGRAM_TRIGGERS        = frozenset({'without', 'letter'})
_CONCRETE_TRIGGERS        = frozenset({'shaped', 'shape', 'visual', 'concrete', 'typographic'})
_DIALOGUE_TRIGGERS        = frozenset({'dialogue', 'conversation', 'talking', 'debate'})
_EKPHRASIS_TRIGGERS       = frozenset({'painting', 'picture', 'artwork', 'photograph', 'sculpture', 'vermeer', 'monet', 'picasso'})
_EXPLANATION_TRIGGERS     = frozenset({'what', 'explain', 'why', 'how', 'define', 'difference', 'influenced', 'influence', 'meter', 'anatomy'})
_COMPARISON_TRIGGERS      = frozenset({'difference', 'compare', 'versus', 'vs', 'contrast'})
_AFFECTIVE_TRIGGERS       = frozenset({'comfort', 'console', 'soothe', 'heal', 'therapeutic', 'grieve', 'grieving'})
_INFLUENCE_TRIGGERS       = frozenset({'influenced', 'influence', 'who influenced', 'lineage', 'tradition', 'history'})
_FIBONACCI_TRIGGERS       = frozenset({'fibonacci', 'sequence', 'arithmetic'})
_ANAPHORA_TRIGGERS        = frozenset({'anaphora', 'repeat', 'refrain', 'repeated'})
_PALINDROME_TRIGGERS      = frozenset({'palindrome', 'backwards', 'backward', 'same forwards', 'reversible'})
_VOLTA_TRIGGERS           = frozenset({'volta', 'turn', 'turning'})
_BIDIRECTIONAL_TRIGGERS   = frozenset({'bidirectional', 'two poems', 'forward', 'backward', 'reversible'})
_IMITATE_WORK_TRIGGERS    = frozenset({'waste', 'land', 'nightingale', 'ode', 'intimations', 'paradise', 'lost'})

_CONCEPT_MAP: Dict[str, str] = {
    'negative capability': 'negative_capability',
    'sprung rhythm': 'sprung_rhythm',
    'sublime': 'sublime',
    'objective correlative': 'objective_correlative',
    'concrete poetry': 'concrete_poetry',
    'imagism': 'imagism',
    'confessional': 'confessional_poetry',
    'projective verse': 'projective_verse',
    'negative capability': 'negative_capability',
    'burkean': 'sublime',
    'burkean sublime': 'sublime',
}

_CROSS_DOMAINS: Dict[str, str] = {
    'jazz': 'jazz',
    'music': 'music',
    'mathematics': 'mathematics',
    'mathematical': 'mathematics',
    'math': 'mathematics',
    'recipe': 'recipe',
    'cooking': 'cooking',
    'architecture': 'architecture',
    'painting': 'visual_art',
    'science': 'science',
    'physics': 'physics',
    'notation': 'mathematics',
    'improvisation': 'jazz',
}


# ---------------------------------------------------------------------------
# PoeticTaskRecognizer
# ---------------------------------------------------------------------------

class PoeticTaskRecognizer:
    """Classify a natural-language utterance into a PoeticTask.

    Uses frozenset intersection and ordered token matching only.
    No regex in the classification path.
    """

    def recognize(self, utterance: str) -> PoeticTask:
        """Parse *utterance* into a PoeticTask."""
        # Also tokenize with hyphen splitting so 'one-syllable' → 'one', 'syllable'
        raw_tokens = utterance.lower().split()
        tokens = []
        for t in raw_tokens:
            if '-' in t:
                tokens.extend(t.split('-'))
            else:
                tokens.append(t)
        tset = frozenset(tokens)

        poets_mentioned = self._extract_poets(tokens, utterance)
        topic = self._extract_topic(tokens, poets_mentioned)
        form = self._extract_form(tset, utterance.lower())
        mood = self._extract_mood(tset)

        # ── 1. Acrostic — very specific, check early ──────────────────────
        if tset & _ACROSTIC_TRIGGERS:
            seed = self._extract_acrostic_word(tokens, utterance)
            return PoeticTask(
                kind=TaskKind.ACROSTIC,
                acrostic_word=seed,
                topic=topic,
                raw=utterance,
                confidence=0.95,
            )

        # ── 2. Lipogram ───────────────────────────────────────────────────
        if 'letter' in tset and tset & frozenset({'without', 'no', 'avoid', 'forbidden', 'using'}):
            letter = self._extract_letter_for_lipogram(tokens)
            return PoeticTask(
                kind=TaskKind.LIPOGRAM,
                forbidden_letters=[letter] if letter else [],
                topic=topic,
                raw=utterance,
                confidence=0.9,
            )

        # ── 3. Rhyme constraint ───────────────────────────────────────────
        if tset & _RHYME_TRIGGERS:
            rhyme_target = self._extract_quoted_word(utterance) or self._extract_rhyme_target(tokens)
            return PoeticTask(
                kind=TaskKind.RHYME_CONSTRAINT,
                rhyme_target=rhyme_target,
                topic=topic,
                raw=utterance,
                confidence=0.92,
            )

        # ── 4. Fibonacci / structural sequence (before syllable check) ────
        if tset & _FIBONACCI_TRIGGERS:
            return PoeticTask(
                kind=TaskKind.STRUCTURAL_SEQUENCE,
                syllable_sequence='fibonacci',
                topic=topic,
                raw=utterance,
                confidence=0.9,
            )

        # ── 5. Palindrome ─────────────────────────────────────────────────
        if tset & _PALINDROME_TRIGGERS and 'poem' in tset:
            return PoeticTask(
                kind=TaskKind.BIDIRECTIONAL,
                topic=topic,
                raw=utterance,
                confidence=0.88,
            )

        # ── 6. Bidirectional poem ─────────────────────────────────────────
        if tset & _BIDIRECTIONAL_TRIGGERS and 'poem' in tset:
            return PoeticTask(
                kind=TaskKind.BIDIRECTIONAL,
                topic=topic,
                raw=utterance,
                confidence=0.85,
            )

        # ── 7. Ekphrasis ──────────────────────────────────────────────────
        if tset & _EKPHRASIS_TRIGGERS:
            artwork = self._extract_artwork(tokens)
            return PoeticTask(
                kind=TaskKind.EKPHRASIS,
                artwork=artwork,
                topic=topic,
                raw=utterance,
                confidence=0.85,
            )

        # ── 8. Affective / comfort goal ───────────────────────────────────
        if tset & _AFFECTIVE_TRIGGERS:
            affect = self._extract_affect(tset)
            return PoeticTask(
                kind=TaskKind.AFFECTIVE_GOAL,
                concept=affect,
                topic=topic,
                mood=mood,
                raw=utterance,
                confidence=0.82,
            )

        # ── 9. Influence trace ────────────────────────────────────────────
        if tset & _INFLUENCE_TRIGGERS and poets_mentioned and 'write' not in tset:
            return PoeticTask(
                kind=TaskKind.INFLUENCE_TRACE,
                poets=poets_mentioned,
                topic=topic,
                raw=utterance,
                confidence=0.85,
            )

        # ── 10. Style counterfactual — "imagine if X was a Y poet" ───────
        # Must check BEFORE concrete so "Eliot as a concrete poet" → counterfactual
        if tset & _COUNTERFACTUAL_TRIGGERS and poets_mentioned:
            alt = self._extract_alternative_style(tokens)
            # Trigger if: alt style present, OR 'poet' mentioned without 'write',
            # OR classic counterfactual markers with no 'write'
            if alt or ('poet' in tset and 'write' not in tset):
                return PoeticTask(
                    kind=TaskKind.STYLE_COUNTERFACTUAL,
                    poets=poets_mentioned,
                    source_domain=alt,
                    topic=topic,
                    raw=utterance,
                    confidence=0.85,
                )

        # ── 11. Concrete / visual poem ────────────────────────────────────
        # Only if no poets mentioned (would be counterfactual otherwise)
        if tset & _CONCRETE_TRIGGERS and not poets_mentioned:
            shape = self._extract_shape(tokens)
            return PoeticTask(
                kind=TaskKind.CONCRETE_POEM,
                shape=shape,
                topic=topic,
                raw=utterance,
                confidence=0.88,
            )

        # ── 12. Comparison — two poets (before style blend) ───────────────
        if tset & _COMPARISON_TRIGGERS and len(poets_mentioned) >= 2:
            return PoeticTask(
                kind=TaskKind.COMPARISON,
                poets=poets_mentioned,
                topic=topic,
                raw=utterance,
                confidence=0.87,
            )

        # ── 13. Dialogue poem ─────────────────────────────────────────────
        if tset & _DIALOGUE_TRIGGERS:
            va, vb = self._extract_dialogue_voices(tokens)
            return PoeticTask(
                kind=TaskKind.DIALOGUE_POEM,
                voice_a=va,
                voice_b=vb,
                topic=topic,
                raw=utterance,
                confidence=0.85,
            )
        # Also "between X and Y" with non-poet subjects can be dialogue
        if 'between' in tset and len(poets_mentioned) == 0:
            va, vb = self._extract_dialogue_voices(tokens)
            if va != 'speaker':
                return PoeticTask(
                    kind=TaskKind.DIALOGUE_POEM,
                    voice_a=va,
                    voice_b=vb,
                    topic=topic,
                    raw=utterance,
                    confidence=0.82,
                )

        # ── 14. Style blend — two+ poets ──────────────────────────────────
        # Three or more poets → always blend
        if len(poets_mentioned) >= 3:
            weights = self._extract_blend_weights(utterance.lower(), poets_mentioned)
            return PoeticTask(
                kind=TaskKind.STYLE_BLEND,
                poets=poets_mentioned,
                blend_weights=weights,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.88,
            )
        if len(poets_mentioned) >= 2 and (tset & _STYLE_BLEND_TRIGGERS or tset & _STYLE_LIKE_TRIGGERS):
            weights = self._extract_blend_weights(utterance.lower(), poets_mentioned)
            return PoeticTask(
                kind=TaskKind.STYLE_BLEND,
                poets=poets_mentioned,
                blend_weights=weights,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.9,
            )

        # ── 15. Anachronism — "what would X write about Y" ───────────────
        if poets_mentioned and ('would' in tset or 'make' in tset) and topic:
            return PoeticTask(
                kind=TaskKind.STYLE_TOPIC_ANACHRONISM,
                poets=poets_mentioned,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.82,
            )

        # ── 16. Imitate specific work ─────────────────────────────────────
        if tset & _IMITATE_WORK_TRIGGERS and poets_mentioned:
            work = self._extract_work_name(tokens)
            return PoeticTask(
                kind=TaskKind.IMITATE_WORK,
                poets=poets_mentioned,
                source_domain=work,
                topic=topic,
                raw=utterance,
                confidence=0.8,
            )

        # ── 17. Perspective shift — requires explicit perspective word ─────
        if tset & frozenset({'perspective', 'pov', 'point'}) and tset & frozenset({'from', 'through'}):
            agent = self._extract_agent(tokens)
            return PoeticTask(
                kind=TaskKind.PERSPECTIVE_SHIFT,
                new_agent=agent,
                poets=poets_mentioned,
                topic=topic,
                raw=utterance,
                confidence=0.82,
            )
        # "rewrite X from Y's perspective" variant
        if 'rewrite' in tset and tset & frozenset({'perspective', 'pov', 'nightingale', 'bird', 'sea', 'wind'}):
            agent = self._extract_agent(tokens)
            return PoeticTask(
                kind=TaskKind.PERSPECTIVE_SHIFT,
                new_agent=agent,
                poets=poets_mentioned,
                topic=topic,
                raw=utterance,
                confidence=0.82,
            )

        # ── 18. Form convert ─────────────────────────────────────────────
        # "rewrite as sonnet", "convert to villanelle" etc.
        if tset & frozenset({'rewrite', 'convert'}) and form != 'free_verse':
            return PoeticTask(
                kind=TaskKind.FORM_CONVERT,
                form=form,
                topic=topic,
                raw=utterance,
                confidence=0.82,
            )
        if tset & frozenset({'free', 'verse'}) and tset & frozenset({'rewrite', 'convert', 'into'}) and form != 'free_verse':
            return PoeticTask(
                kind=TaskKind.FORM_CONVERT,
                form=form,
                topic=topic,
                raw=utterance,
                confidence=0.82,
            )

        # ── 19. Style in poet's manner — single poet ──────────────────────
        if poets_mentioned and tset & (_STYLE_LIKE_TRIGGERS | frozenset({'write', 'poem', 'using', 'as', 'would'})):
            return PoeticTask(
                kind=TaskKind.STYLE_TRANSLATE,
                poets=poets_mentioned,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.8,
            )

        # ── 20. Syllable constraint ────────────────────────────────────────
        if tset & _SYLLABLE_TRIGGERS:
            limit = self._extract_number(tokens)
            return PoeticTask(
                kind=TaskKind.SYLLABLE_CONSTRAINT,
                syllable_limit=limit,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.88,
            )

        # ── 21. All-question / all-imperative syntactic constraint ─────────
        if 'every' in tset and tset & frozenset({'question', 'questions'}):
            return PoeticTask(
                kind=TaskKind.SYNTACTIC_CONSTRAINT,
                line_syntax='interrogative',
                topic=topic,
                raw=utterance,
                confidence=0.9,
            )
        if 'every' in tset and tset & frozenset({'imperative', 'command', 'commands'}):
            return PoeticTask(
                kind=TaskKind.SYNTACTIC_CONSTRAINT,
                line_syntax='imperative',
                topic=topic,
                raw=utterance,
                confidence=0.9,
            )

        # ── 22. Anaphora ──────────────────────────────────────────────────
        if tset & _ANAPHORA_TRIGGERS:
            return PoeticTask(
                kind=TaskKind.GENERATE,
                required_device='anaphora',
                topic=topic,
                form=form,
                mood=mood,
                raw=utterance,
                confidence=0.85,
            )

        # ── 23. Volta injection ───────────────────────────────────────────
        if tset & _VOLTA_TRIGGERS and tset & frozenset({'add', 'inject', 'needs', 'need'}):
            return PoeticTask(
                kind=TaskKind.REVISE,
                required_device='volta',
                topic=topic,
                raw=utterance,
                confidence=0.85,
            )

        # ── 24. Forbidden words / lexical constraint ──────────────────────
        if 'without' in tset and tset & frozenset({'word', 'words', 'adjectives', 'nouns', 'verbs'}):
            forbidden = self._extract_forbidden(tokens, utterance)
            forbidden_pos = self._extract_forbidden_pos(tokens)
            if forbidden_pos:
                return PoeticTask(
                    kind=TaskKind.GENERATE,
                    forbidden_pos=forbidden_pos,
                    topic=topic,
                    form=form,
                    raw=utterance,
                    confidence=0.85,
                )
            return PoeticTask(
                kind=TaskKind.GENERATE,
                forbidden_words=forbidden,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.82,
            )
        if 'no' in tset and tset & frozenset({'adjectives', 'nouns', 'verbs', 'adverbs'}):
            forbidden_pos = self._extract_forbidden_pos(tokens)
            return PoeticTask(
                kind=TaskKind.GENERATE,
                forbidden_pos=forbidden_pos,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.85,
            )

        # ── 25. Alliteration constraint ───────────────────────────────────
        if 'alliteration' in tset:
            letter = self._extract_letter(tokens)
            return PoeticTask(
                kind=TaskKind.ALLITERATION,
                required_device=letter,
                topic=topic,
                raw=utterance,
                confidence=0.88,
            )
        if 'every' in tset and 'line' in tset and 'starts' in tset:
            letter = self._extract_letter(tokens)
            return PoeticTask(
                kind=TaskKind.ALLITERATION,
                required_device=letter,
                topic=topic,
                raw=utterance,
                confidence=0.85,
            )

        # ── 26. Concept embodiment ────────────────────────────────────────
        concept = self._match_concept(utterance.lower())
        if concept:
            if 'demonstrate' in tset or 'example' in tset or 'show' in tset or 'actually' in tset:
                return PoeticTask(
                    kind=TaskKind.DEMONSTRATE_FORM,
                    concept=concept,
                    form=form,
                    topic=topic,
                    raw=utterance,
                    confidence=0.87,
                )
            return PoeticTask(
                kind=TaskKind.EMBODY_CONCEPT,
                concept=concept,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.85,
            )

        # ── 27. Cross-domain blend ────────────────────────────────────────
        cross_domain = self._detect_cross_domain(tset)
        if cross_domain:
            if 'also' in tset or 'genre' in tset:
                return PoeticTask(
                    kind=TaskKind.GENRE_BLEND,
                    source_domain=cross_domain,
                    topic=topic,
                    raw=utterance,
                    confidence=0.82,
                )
            return PoeticTask(
                kind=TaskKind.CROSS_DOMAIN_BLEND,
                source_domain=cross_domain,
                topic=topic,
                raw=utterance,
                confidence=0.82,
            )

        # ── 28. Argumentative poem ────────────────────────────────────────
        if tset & frozenset({'argues', 'argue', 'thesis', 'argument', 'claim'}):
            thesis = self._extract_thesis(tokens)
            return PoeticTask(
                kind=TaskKind.ARGUMENTATIVE,
                thesis=thesis,
                topic=topic,
                raw=utterance,
                confidence=0.83,
            )

        # ── 29. Explain-then-demonstrate ─────────────────────────────────
        # Only if no poets mentioned and has explicit example/demonstrate request
        if not poets_mentioned:
            if 'explain' in tset and ('example' in tset or 'write' in tset or 'then' in tset):
                concept = self._match_concept(utterance.lower())
                return PoeticTask(
                    kind=TaskKind.EXPLAIN_THEN_DEMONSTRATE,
                    concept=concept,
                    form=form,
                    topic=topic,
                    raw=utterance,
                    confidence=0.83,
                )

        # ── 30. Comparison (general) ──────────────────────────────────────
        if tset & _COMPARISON_TRIGGERS:
            return PoeticTask(
                kind=TaskKind.COMPARISON,
                poets=poets_mentioned,
                topic=topic,
                raw=utterance,
                confidence=0.8,
            )

        # ── 31. Explanation ───────────────────────────────────────────────
        if tset & _EXPLANATION_TRIGGERS and not (tset & frozenset({'write', 'compose'})):
            return PoeticTask(
                kind=TaskKind.EXPLANATION,
                topic=topic,
                poets=poets_mentioned,
                raw=utterance,
                confidence=0.8,
            )

        # ── 32. Prose to poem ─────────────────────────────────────────────
        colon_pos = utterance.find(':')
        if tset & _PROSE_TRIGGERS and colon_pos > 0:
            prose = utterance[colon_pos + 1:].strip()
            return PoeticTask(
                kind=TaskKind.PROSE_TO_POEM,
                prose_input=prose,
                topic=topic,
                form=form,
                raw=utterance,
                confidence=0.92,
            )

        # ── 33. Topic transfer ────────────────────────────────────────────
        if 'feeling' in tset and ('instead' in tset or 'but' in tset):
            return PoeticTask(
                kind=TaskKind.TOPIC_TRANSFER,
                topic=topic,
                mood=mood,
                raw=utterance,
                confidence=0.8,
            )

        # ── 34. Revise / feedback ─────────────────────────────────────────
        # "Make this poem darker" IS a revision even though 'poem' is present
        _MOOD_SHIFT_WORDS = frozenset({'darker', 'lighter', 'funnier', 'sadder', 'angrier',
                                       'calmer', 'gloomier', 'happier', 'brighter'})
        if tset & _MOOD_SHIFT_WORDS:
            delta = self._extract_mood_delta(tset)
            return PoeticTask(
                kind=TaskKind.REVISE,
                mood_delta=delta,
                topic=topic,
                raw=utterance,
                confidence=0.82,
            )
        if tset & frozenset({'revise', 'alter', 'redo'}) and not (tset & frozenset({'write', 'compose'})):
            delta = self._extract_mood_delta(tset)
            forbidden = self._extract_forbidden(tokens, utterance)
            return PoeticTask(
                kind=TaskKind.REVISE,
                mood_delta=delta,
                forbidden_words=forbidden,
                topic=topic,
                raw=utterance,
                confidence=0.75,
            )

        # ── 35. Extend ────────────────────────────────────────────────────
        if tset & _EXTEND_TRIGGERS:
            n = self._extract_number(tokens)
            return PoeticTask(
                kind=TaskKind.EXTEND,
                stanzas_to_add=max(1, n),
                topic=topic,
                raw=utterance,
                confidence=0.78,
            )

        # ── 36. Compress to haiku ─────────────────────────────────────────
        if tset & _COMPRESS_TRIGGERS:
            return PoeticTask(
                kind=TaskKind.COMPRESS,
                form='haiku',
                topic=topic,
                raw=utterance,
                confidence=0.8,
            )

        # ── Default: standard generation ──────────────────────────────────
        return PoeticTask(
            kind=TaskKind.GENERATE,
            topic=topic,
            form=form,
            mood=mood,
            raw=utterance,
            confidence=0.5,
        )

    # -----------------------------------------------------------------------
    # Private extraction helpers
    # -----------------------------------------------------------------------

    def _extract_poets(self, tokens: List[str], utterance: str) -> List[str]:
        utt_lower = utterance.lower()
        found = [p for p in _POET_NAMES if p in frozenset(tokens) or p in utt_lower]
        # deduplicate preserving order
        seen: set = set()
        result = []
        for p in found:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result

    def _extract_topic(self, tokens: List[str], exclude_poets: List[str]) -> str:
        stop = frozenset({
            'write', 'a', 'poem', 'about', 'the', 'an', 'in', 'of', 'with',
            'and', 'that', 'for', 'by', 'to', 'is', 'are', 'was', 'were',
            'like', 'as', 'from', 'into', 'style', 'manner', 'voice', 'way',
            'please', 'me', 'my', 'us', 'this', 'it', 'its', 'i', 'you',
            'can', 'could', 'would', 'should', 'will', 'do', 'does', 'did',
        })
        exclude = frozenset(exclude_poets)
        candidates = [
            t for t in tokens
            if t not in stop and t not in exclude
            and len(t) > 2 and t.isalpha()
        ]
        for i, t in enumerate(tokens):
            if t == 'about' and i + 1 < len(tokens) and tokens[i + 1].isalpha():
                return tokens[i + 1]
        return candidates[-1] if candidates else 'poetry'

    def _extract_form(self, tset: frozenset, utt_lower: str) -> str:
        for f in (
            'sonnet', 'haiku', 'villanelle', 'ode', 'elegy', 'ballad',
            'limerick', 'ghazal', 'pantoum', 'sestina',
        ):
            if f in tset:
                return f
        if 'free' in tset and 'verse' in tset:
            return 'free_verse'
        if 'blank' in tset and 'verse' in tset:
            return 'blank_verse'
        if 'terza' in tset and 'rima' in tset:
            return 'terza_rima'
        if 'petrarchan' in utt_lower:
            return 'petrarchan_sonnet'
        return 'free_verse'

    def _extract_mood(self, tset: frozenset) -> str:
        mood_map = {
            'dark': 'melancholic', 'darker': 'melancholic',
            'sad': 'melancholic', 'grief': 'melancholic', 'death': 'melancholic',
            'angry': 'angry', 'anger': 'angry',
            'joyful': 'joyful', 'happy': 'joyful', 'joy': 'joyful',
            'funny': 'humorous', 'humor': 'humorous', 'comic': 'humorous',
            'love': 'romantic', 'romantic': 'romantic',
            'mysterious': 'mysterious', 'mystery': 'mysterious',
            'contemplative': 'contemplative', 'meditative': 'contemplative',
            'comfort': 'consoling', 'consoling': 'consoling',
        }
        for t, mood_val in mood_map.items():
            if t in tset:
                return mood_val
        return 'neutral'

    def _extract_quoted_word(self, utterance: str) -> str:
        import re as _re
        m = _re.search(r'["\']([^"\']+)["\']', utterance)
        return m.group(1).strip() if m else ''

    def _extract_rhyme_target(self, tokens: List[str]) -> str:
        for i, t in enumerate(tokens):
            if t in ('rhymes', 'rhyme') and i + 2 < len(tokens) and tokens[i + 1] == 'with':
                return tokens[i + 2].strip("\"',.!")
            if t == 'in' and i > 0 and tokens[i - 1] == 'ending':
                if i + 1 < len(tokens):
                    return tokens[i + 1].strip("\"',.!")
        return ''

    def _extract_acrostic_word(self, tokens: List[str], utterance: str) -> str:
        # Look for ALL-CAPS word first (in original utterance, not lowercased)
        for t in utterance.split():
            if t.isupper() and len(t) > 2 and t.isalpha():
                return t
        # Word after 'acrostic', skipping common prepositions
        skip = frozenset({'for', 'of', 'the', 'a', 'an', 'poem', 'word'})
        for i, t in enumerate(tokens):
            if t == 'acrostic':
                # Find next non-skip word
                for j in range(i + 1, min(i + 4, len(tokens))):
                    candidate = tokens[j].strip("\"',")
                    if candidate.isalpha() and candidate not in skip:
                        return candidate.upper()
        # Word after 'for' when 'acrostic' is present
        if 'acrostic' in frozenset(tokens):
            for i, t in enumerate(tokens):
                if t == 'for' and i + 1 < len(tokens):
                    candidate = tokens[i + 1].strip("\"',")
                    if candidate.isalpha() and candidate not in skip:
                        return candidate.upper()
        return ''

    def _extract_letter_for_lipogram(self, tokens: List[str]) -> str:
        """Extract forbidden letter, preferring the word immediately after 'letter'."""
        _SKIP_LETTERS = frozenset({'a', 'i'})  # common articles/words
        # Word after 'letter'
        for i, t in enumerate(tokens):
            if t == 'letter' and i + 1 < len(tokens):
                candidate = tokens[i + 1].strip("\"',.!")
                if len(candidate) == 1 and candidate.isalpha():
                    return candidate
        # Single-char alpha tokens, skipping 'a' and 'i'
        for t in tokens:
            if len(t) == 1 and t.isalpha() and t not in _SKIP_LETTERS:
                return t
        # Fall back to any single-char
        for t in tokens:
            if len(t) == 1 and t.isalpha():
                return t
        return ''


    def _extract_letter(self, tokens: List[str]) -> str:
        """Extract a single letter, e.g. for alliteration constraint."""
        # Word after 'letter'
        for i, t in enumerate(tokens):
            if t == 'letter' and i + 1 < len(tokens):
                candidate = tokens[i + 1].strip("\"',.!")
                if len(candidate) == 1 and candidate.isalpha():
                    return candidate
        # Uppercase single-char
        for t in tokens:
            if len(t) == 1 and t.isupper():
                return t
        # Any single-char alpha, skipping 'a', 'i'
        for t in tokens:
            if len(t) == 1 and t.isalpha() and t not in ('a', 'i'):
                return t
        return ''

    def _extract_blend_weights(self, utterance_lower: str, poets: List[str]) -> List[float]:
        if 'halfway' in utterance_lower and len(poets) == 2:
            return [0.5, 0.5]
        # "X's compression, Y's sensuality, Z's sweep" — equal thirds etc.
        return [1.0 / len(poets)] * len(poets)

    def _extract_alternative_style(self, tokens: List[str]) -> str:
        style_words = frozenset({
            'minimalist', 'maximalist', 'concrete', 'surrealist', 'imagist',
            'confessional', 'romantic', 'neoclassical', 'modernist',
            'postmodernist', 'symbolist', 'futurist', 'expressionist',
        })
        for t in tokens:
            if t in style_words:
                return t
        return ''

    def _extract_number(self, tokens: List[str]) -> int:
        words = {
            'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'ten': 10, 'twelve': 12,
        }
        for t in tokens:
            if t.isdigit():
                return int(t)
            if t in words:
                return words[t]
        return 0

    def _extract_forbidden(self, tokens: List[str], utterance: str) -> List[str]:
        # Try quoted word first
        quoted = self._extract_quoted_word(utterance)
        if quoted:
            return [quoted]
        # Word before 'word' or 'words'
        for i, t in enumerate(tokens):
            if t in ('word', 'words') and i > 0:
                return [tokens[i - 1].strip("\"'")]
        return []

    def _extract_forbidden_pos(self, tokens: List[str]) -> List[str]:
        pos_map = {
            'adjectives': 'ADJ', 'adjective': 'ADJ',
            'nouns': 'NOUN', 'noun': 'NOUN',
            'verbs': 'VERB', 'verb': 'VERB',
            'adverbs': 'ADV', 'adverb': 'ADV',
        }
        return [pos_map[t] for t in tokens if t in pos_map]

    def _extract_dialogue_voices(self, tokens: List[str]) -> tuple:
        _ARTICLES = frozenset({'the', 'a', 'an'})
        for i, t in enumerate(tokens):
            if t == 'between' and i + 1 < len(tokens):
                # Skip articles to find first non-article word
                va_idx = i + 1
                while va_idx < len(tokens) and tokens[va_idx] in _ARTICLES:
                    va_idx += 1
                if va_idx >= len(tokens):
                    break
                va = tokens[va_idx]
                # Find 'and' after va
                for j in range(va_idx + 1, min(va_idx + 4, len(tokens))):
                    if tokens[j] == 'and' and j + 1 < len(tokens):
                        vb_idx = j + 1
                        while vb_idx < len(tokens) and tokens[vb_idx] in _ARTICLES:
                            vb_idx += 1
                        if vb_idx < len(tokens):
                            return va, tokens[vb_idx]
                return va, 'listener'
        return 'speaker', 'listener'

    def _extract_shape(self, tokens: List[str]) -> str:
        shapes = frozenset({
            'tree', 'bird', 'wave', 'mountain', 'cross', 'spiral',
            'circle', 'hourglass', 'star', 'heart', 'flower', 'flame',
        })
        for t in tokens:
            if t in shapes:
                return t
        return 'tree'

    def _extract_artwork(self, tokens: List[str]) -> str:
        artists = frozenset({
            'vermeer', 'monet', 'picasso', 'rembrandt', 'klimt',
            'turner', 'constable', 'caravaggio', 'raphael',
        })
        for t in tokens:
            if t in artists:
                return t
        return ''

    def _extract_affect(self, tset: frozenset) -> str:
        if tset & frozenset({'comfort', 'console', 'soothe'}):
            return 'comfort'
        if tset & frozenset({'heal', 'healing'}):
            return 'healing'
        return 'comfort'

    def _extract_work_name(self, tokens: List[str]) -> str:
        work_words = frozenset({'land', 'nightingale', 'intimations', 'ode', 'paradise'})
        found = [t for t in tokens if t in work_words]
        return '_'.join(found) if found else ''

    def _extract_agent(self, tokens: List[str]) -> str:
        for i, t in enumerate(tokens):
            if t in ('from', 'as', 'through') and i + 1 < len(tokens):
                candidate = tokens[i + 1].strip("'\",.!")
                if candidate.isalpha() and len(candidate) > 2:
                    return candidate
        return ''

    def _extract_thesis(self, tokens: List[str]) -> str:
        for i, t in enumerate(tokens):
            if t == 'that' and i + 1 < len(tokens):
                return ' '.join(tokens[i + 1:i + 6])
        return ''

    def _match_concept(self, text: str) -> str:
        for phrase, concept in _CONCEPT_MAP.items():
            if phrase in text:
                return concept
        return ''

    def _detect_cross_domain(self, tset: frozenset) -> str:
        for t, domain in _CROSS_DOMAINS.items():
            if t in tset:
                return domain
        return ''

    def _extract_mood_delta(self, tset: frozenset) -> float:
        if tset & frozenset({'darker', 'sadder', 'angrier', 'gloomier', 'darker'}):
            return -0.3
        if tset & frozenset({'lighter', 'happier', 'funnier', 'brighter', 'funnier'}):
            return 0.3
        return 0.0


# ---------------------------------------------------------------------------
# Convenience singleton accessor
# ---------------------------------------------------------------------------

_recognizer: Optional[PoeticTaskRecognizer] = None


def get_recognizer() -> PoeticTaskRecognizer:
    """Return a module-level singleton PoeticTaskRecognizer."""
    global _recognizer
    if _recognizer is None:
        _recognizer = PoeticTaskRecognizer()
    return _recognizer
