"""Natural language → Harmonic Logical Form (HLF) parser.

Builds the richest possible HLF from a user utterance, using:
- FrameActivator for theta-role grid
- TAMEngine for tense/aspect/mood
- VendlerClassifier for event structure
- ForceDynamicAnalyzer for causal/force relations
- Polyphony detection for multi-voice (PolyTerm)
- Conditional detection for ImplTerm
- Modality detection for ModalTerm
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List

from gofai_chat.core.grade import Grade


@dataclass
class ParsedHLF:
    hlf: object                         # the main HLF expression
    tam: Optional[object]               # TAMBundle or None
    force_dynamics: List[object]        # list of ForceDynamic
    presuppositions: List[object]       # list of Presupposition
    event_class: str                    # Vendler class string
    output_mode: "OutputMode"           # detected from request
    topic: str                          # main topic noun
    theta_roles: dict                   # {role: filler_str}
    grade: Grade                        # overall parse confidence
    task: Optional[object] = None       # PoeticTask from task_registry, or None
    enrichment: Optional[object] = None  # EntityEnrichment if Wikipedia was fetched


class NLToHLF:
    """Full natural-language → HLF pipeline."""

    def parse(self, utterance: str) -> ParsedHLF:
        """Parse *utterance* into the richest possible HLF."""
        # ── Step 1: Tokenize and POS-tag ──────────────────────────────
        try:
            from nltk import word_tokenize, pos_tag
            tokens = word_tokenize(utterance)
            tagged = pos_tag(tokens)
        except Exception:
            tokens = utterance.split()
            tagged = [(t, 'NN') for t in tokens]

        toks_lower = [t.lower() for t in tokens]

        # ── Step 2: Detect output mode ────────────────────────────────
        from gofai_chat.generation.hlf_realizer import OutputMode
        utt_lower = utterance.lower()
        toks_set = frozenset(toks_lower)
        _POEM_FORMS = frozenset({'poem', 'sonnet', 'haiku', 'verse', 'ode', 'lyric',
                                  'ballad', 'elegy', 'villanelle', 'sestina', 'acrostic',
                                  'limerick', 'couplet', 'stanza', 'rhyme', 'quatrain'})
        if (any(w in utt_lower for w in ('compose', 'in verse', 'poetic', 'write a poem')) or
                (('write' in toks_lower or 'create' in toks_lower) and
                 bool(toks_set & _POEM_FORMS))):
            mode = OutputMode.POEM
        elif any(w in utt_lower for w in (
                'explain', 'what is', 'what are', 'define', 'describe', 'tell me about')):
            mode = OutputMode.EXPLANATION
        elif any(w in utt_lower for w in (
                'write a story', 'narrative', 'once upon', 'tell a story')):
            mode = OutputMode.NARRATIVE
        elif any(w in utt_lower for w in (
                'write', 'create', 'express', 'say it as', 'rewrite')):
            mode = OutputMode.PROSE
        else:
            mode = OutputMode.POEM  # default for the poetry chatbot

        # ── Step 3: Extract main frame via FrameActivator ────────────
        try:
            from gofai_chat.frames.activation import FrameActivator
            fa = FrameActivator()
            word_list = [t for t, _ in tagged]
            pos_list = [p for _, p in tagged]
            frame_instances = fa.activate(word_list, pos_list)
        except Exception:
            frame_instances = []

        # ── Step 4: Build EventTerm from frame instances ──────────────
        from gofai_chat.core.terms import EventTerm, Const

        event_terms = []
        theta_roles: dict = {}
        for fi in frame_instances[:3]:
            frame_name = getattr(fi, 'frame_type_name',
                                 getattr(fi, 'frame_type', 'Event'))
            if hasattr(frame_name, 'name'):
                frame_name = frame_name.name  # unwrap enum

            # fillers is the canonical field; roles is a property alias
            roles_raw = getattr(fi, 'roles', getattr(fi, 'fillers', {}))
            grade_val = getattr(fi, 'total_grade', Grade.from_prob(0.7))
            if not isinstance(grade_val, Grade):
                grade_val = Grade.from_prob(float(grade_val) if grade_val else 0.7)

            role_hlfs: dict = {}
            for role_name, filler in roles_raw.items():
                # filler is a RoleFiller; .head is the head word string
                filler_str = str(getattr(filler, 'head',
                                         getattr(filler, 'value', filler)))
                if not filler_str or filler_str == 'None':
                    filler_str = str(filler)
                role_hlfs[str(role_name)] = Const(filler_str, grade_val)
                theta_roles[str(role_name)] = filler_str

            et = EventTerm(
                grade=grade_val,
                frame_type_name=str(frame_name),
                event_var=f"e{len(event_terms) + 1}",
                roles=role_hlfs,
            )
            event_terms.append(et)

        # ── Step 5: Detect structural operators from syntax ───────────
        conditional_markers = {
            'if', 'although', 'though', 'while', 'whereas', 'unless', 'until',
        }
        contrast_markers = {
            'but', 'however', 'yet', 'still', 'nonetheless', 'despite',
        }
        deontic_modals = {'must', 'should', 'ought', 'shall'}
        epistemic_modals = {'might', 'may', 'could', 'perhaps', 'possibly', 'probably'}
        bouletic_modals = {'want', 'wish', 'desire', 'hope'}

        has_conditional = any(t in toks_lower for t in conditional_markers)
        has_contrast = any(t in toks_lower for t in contrast_markers)
        has_negation = any(t in toks_lower for t in ('not', 'never', 'no', 'neither', 'nor'))
        modal_kind = None
        if any(t in toks_lower for t in deontic_modals):
            modal_kind = 'deontic'
        elif any(t in toks_lower for t in epistemic_modals):
            modal_kind = 'epistemic'
        elif any(t in toks_lower for t in bouletic_modals):
            modal_kind = 'bouletic'

        # ── Step 6: Detect tense and aspect ──────────────────────────
        past_markers = {
            'was', 'were', 'had', 'did', 'went', 'came', 'said', 'took', 'gave',
            'made', 'found',
        }
        future_markers = {'will', 'shall', 'would', 'going'}
        habitual_markers = {'always', 'usually', 'often', 'typically', 'generally', 'every', 'each'}
        progressive_markers = {'is', 'are', 'am', 'was', 'were'}
        perfect_markers = {'has', 'have', 'had'}

        tense = (
            'past' if any(t in toks_lower for t in past_markers) else
            'future' if any(t in toks_lower for t in future_markers) else
            'present'
        )
        aspect = (
            'habitual' if any(t in toks_lower for t in habitual_markers) else
            'progressive' if (
                any(t in toks_lower for t in progressive_markers)
                and any(t.endswith('ing') for t in toks_lower)
            ) else
            'perfect' if any(t in toks_lower for t in perfect_markers) else
            'simple'
        )

        # ── Step 7: Get TAMBundle ─────────────────────────────────────
        tam = None
        try:
            from gofai_chat.sem.tam_engine import TAMEngine
            from gofai_chat.core.judgment import Context
            tam_features = {'tense': tense, 'aspect': aspect}
            tam = TAMEngine().compute_tam(tam_features, Context())
        except Exception:
            pass

        # ── Step 8: Get VendlerClass ──────────────────────────────────
        event_class = 'activity'
        main_verbs = [t for t, p in tagged if p.startswith('VB')]
        if main_verbs:
            try:
                from gofai_chat.sem.events import VendlerClassifier
                ec = VendlerClassifier().classify(main_verbs[0])
                event_class = ec.value if hasattr(ec, 'value') else str(ec).split('.')[-1].lower()
            except Exception:
                pass

        # ── Step 9: Get ForceDynamics ─────────────────────────────────
        force_dynamics: list = []
        if event_terms and theta_roles:
            try:
                from gofai_chat.inference.force_dynamics import ForceDynamicAnalyzer
                fda = ForceDynamicAnalyzer()
                for fi in frame_instances[:1]:
                    fd = fda.analyze(fi, utterance)
                    if fd:
                        force_dynamics.append(fd)
            except Exception:
                pass

        # ── Step 10: Assemble the full HLF ───────────────────────────
        from gofai_chat.core.terms import (
            TenseTerm, AspectTerm, ModalTerm, NegTerm,
            ImplTerm, ConjTerm, PolyTerm,
        )

        if not event_terms:
            nouns = [t.lower() for t, p in tagged if p.startswith('NN')]
            topic_word = nouns[0] if nouns else utterance.split()[0].lower()
            agent = Const(topic_word, Grade.from_prob(0.6))
            event_terms = [EventTerm(Grade.from_prob(0.6), 'Describing', 'e1', {'Theme': agent})]

        base = (
            event_terms[0]
            if len(event_terms) == 1
            else ConjTerm(event_terms, Grade.from_prob(0.8))
        )

        if aspect != 'simple':
            base = AspectTerm(Grade.from_prob(0.85), aspect, base)

        base = TenseTerm(Grade.from_prob(0.85), tense, base)

        if modal_kind:
            base = ModalTerm(Grade.from_prob(0.8), modal_kind, base)

        if has_negation:
            base = NegTerm(Grade.from_prob(0.75), base)

        # PolyTerm for contrast ("X but Y") — two voices
        if has_contrast and len(event_terms) >= 2:
            voice1 = TenseTerm(Grade.from_prob(0.85), tense, event_terms[0])
            voice2 = TenseTerm(Grade.from_prob(0.85), tense, event_terms[1])
            base = PolyTerm(Grade.from_prob(0.8), [voice1, voice2])

        # ImplTerm for conditional ("if X then Y")
        if has_conditional and len(event_terms) >= 2:
            base = ImplTerm(
                Grade.from_prob(0.8),
                event_terms[0],
                event_terms[1] if len(event_terms) > 1 else base,
            )

        # ── Step 11: Extract presuppositions ──────────────────────────
        presuppositions: list = []
        try:
            from gofai_chat.sem.presupposition import Presupposition, PresuppositionTrigger
            definite_articles = {'the', 'this', 'that', 'these', 'those'}
            for i, (t, p) in enumerate(tagged):
                if p.startswith('NN') and i > 0:
                    prev = toks_lower[i - 1]
                    if prev in definite_articles:
                        noun = t.lower()
                        presup_content = Const(noun, Grade.from_prob(0.9))
                        presuppositions.append(Presupposition(
                            trigger=PresuppositionTrigger.DEFINITE,
                            content=presup_content,
                            description=f"'{noun}' presupposes existence of {noun}",
                        ))
                        if len(presuppositions) >= 3:
                            break
        except Exception:
            pass

        # ── Step 12: Extract topic and return ─────────────────────────
        stop = {'poem', 'verse', 'sonnet', 'haiku', 'ode', 'lyric', 'ballad', 'acrostic',
                'story', 'please', 'about', 'write', 'make', 'tell', 'explain', 'what',
                'this', 'prose', 'narrative', 'text', 'way', 'thing', 'something', 'anything'}

        # Join consecutive NNP/NNPS tokens as multi-word proper nouns (Bug 5)
        proper_nouns = []
        proper_nouns_original_case = []  # preserved for entity enrichment lookup
        i = 0
        while i < len(tagged):
            t, p = tagged[i]
            if p in ('NNP', 'NNPS') and t[0].isupper():
                group = [t]
                j = i + 1
                while j < len(tagged) and tagged[j][1] in ('NNP', 'NNPS') and tagged[j][0][0].isupper():
                    group.append(tagged[j][0])
                    j += 1
                proper_nouns.append(' '.join(group).lower())
                proper_nouns_original_case.append(' '.join(group))
                i = j
            else:
                i += 1

        nouns = [t.lower() for t, p in tagged if p.startswith('NN') and len(t) > 2]
        topic_candidates = proper_nouns + [n for n in nouns if n not in stop]
        topic = next((n for n in topic_candidates if n not in stop), nouns[0] if nouns else 'life')

        # Mark pure feedback commands so callers can substitute discourse topic (Bug 3)
        _PURE_FEEDBACK = frozenset({'darker', 'lighter', 'brighter', 'shorter', 'longer',
                                      'sadder', 'happier', 'simpler', 'deeper', 'softer'})
        if toks_set & _PURE_FEEDBACK and topic in ('life', 'thing', 'way', 'make'):
            topic = '__revision__'

        # ── Step 2b: PoeticTaskRecognizer for richer task metadata ────
        parsed_task = None
        try:
            from gofai_chat.chat.task_registry import PoeticTaskRecognizer, TaskKind
            parsed_task = PoeticTaskRecognizer().recognize(utterance)
            # Override output_mode based on task kind
            if parsed_task.kind in (TaskKind.EXPLANATION, TaskKind.COMPARISON,
                                     TaskKind.INFLUENCE_TRACE):
                mode = OutputMode.EXPLANATION
        except Exception:
            parsed_task = None

        # ── Step 13: Wikipedia entity enrichment for unknown named entities ──
        enrichment = None
        try:
            from gofai_chat.knowledge.entity_enricher import EntityEnricher
            enricher = EntityEnricher()
            # Use original-case proper nouns for lookup so capitalisation heuristics fire
            lookup_candidates = proper_nouns_original_case or ([topic] if topic else [])
            lookup_query = next(
                (pn for pn in lookup_candidates if enricher.needs_lookup(pn)),
                None,
            )
            if lookup_query is None and enricher.needs_lookup(topic):
                lookup_query = topic
            if lookup_query:
                enrichment = enricher.enrich(lookup_query)
                if enrichment.found:
                    # Merge Wikipedia theta_roles with existing (existing wins on conflict)
                    theta_roles = {**enrichment.theta_roles, **theta_roles}
                    # Combine Wikipedia HLF with the utterance HLF into a PolyTerm
                    if enrichment.hlf is not None:
                        from gofai_chat.core.terms import PolyTerm
                        try:
                            base = (
                                PolyTerm(voices=[enrichment.hlf, base])
                                if base is not None else enrichment.hlf
                            )
                        except Exception:
                            base = enrichment.hlf
        except Exception:
            pass  # enrichment failure must never break parsing

        return ParsedHLF(
            hlf=base,
            tam=tam,
            force_dynamics=force_dynamics,
            presuppositions=presuppositions,
            event_class=event_class,
            output_mode=mode,
            topic=topic,
            theta_roles=theta_roles,
            grade=Grade.from_prob(0.7 + 0.1 * min(3, len(event_terms))),
            task=parsed_task,
            enrichment=enrichment,
        )
