"""HLF → Output realizer.

Takes a ParsedHLF and generates output as:
- POEM: meter-constrained verse, one stanza per HLF clause
- PROSE: coherent paragraph expressing the HLF content
- EXPLANATION: factual answer derived from the HLF + DefaultReasoner
- NARRATIVE: story arc driven by ImplTerm/ForceDynamic structure

The HLF is the semantic contract — the Grade semiring scores how well
each output candidate satisfies it via the HarmonyComputer.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from gofai_chat.core.grade import Grade


class OutputMode(Enum):
    POEM = "poem"
    PROSE = "prose"
    EXPLANATION = "explanation"
    NARRATIVE = "narrative"


class HLFWalker:
    """Traverses an HLF tree and extracts semantic content units."""

    def extract_units(self, hlf) -> List[dict]:
        """Walk HLF tree; return list of content-unit dicts."""
        units: List[dict] = []
        self._walk(hlf, units, {})
        return units

    def _walk(self, node, units: list, ctx: dict) -> None:
        from gofai_chat.core.terms import (
            EventTerm, TenseTerm, AspectTerm, ModalTerm,
            NegTerm, ImplTerm, ConjTerm, PolyTerm,
            Const, CoerceTerm, FrameIntro,
        )

        if isinstance(node, EventTerm):
            roles_str = {
                k: (v.name if isinstance(v, Const) else str(v))
                for k, v in node.roles.items()
            }
            grade_raw = node.grade
            grade_val = (
                grade_raw.value if hasattr(grade_raw, 'value') else float(grade_raw)
            )
            units.append({
                'frame': node.frame_type_name,
                'roles': roles_str,
                'grade': grade_val * ctx.get('grade_modifier', 1.0),
                'tense': ctx.get('tense', 'present'),
                'aspect': ctx.get('aspect', 'simple'),
                'modal': ctx.get('modal', None),
                'negated': ctx.get('negated', False),
                'structure': ctx.get('structure', 'main'),
            })
        elif isinstance(node, TenseTerm):
            self._walk(node.body, units, {**ctx, 'tense': node.tense})
        elif isinstance(node, AspectTerm):
            self._walk(node.body, units, {**ctx, 'aspect': node.aspect})
        elif isinstance(node, ModalTerm):
            self._walk(node.body, units, {**ctx, 'modal': node.modal_kind})
        elif isinstance(node, NegTerm):
            self._walk(node.body, units, {**ctx, 'negated': True, 'grade_modifier': 0.8})
        elif isinstance(node, ImplTerm):
            self._walk(node.antecedent, units, {**ctx, 'structure': 'antecedent'})
            self._walk(node.consequent, units, {**ctx, 'structure': 'consequent'})
        elif isinstance(node, PolyTerm):
            for i, voice in enumerate(node.voices):
                self._walk(voice, units, {**ctx, 'structure': f'voice_{i}'})
        elif isinstance(node, ConjTerm):
            for c in (node.conjuncts if hasattr(node, 'conjuncts') else []):
                self._walk(c, units, {**ctx})
        elif isinstance(node, CoerceTerm):
            self._walk(node.body, units, {**ctx, 'grade_modifier': 0.9})
        elif isinstance(node, FrameIntro):
            roles_str = {
                k: (v.name if isinstance(v, Const) else str(v))
                for k, v in node.role_fillers.items()
            }
            units.append({
                'frame': node.frame_type_name,
                'roles': roles_str,
                'grade': 0.7,
                'tense': ctx.get('tense', 'present'),
                'aspect': ctx.get('aspect', 'simple'),
                'modal': ctx.get('modal', None),
                'negated': ctx.get('negated', False),
                'structure': ctx.get('structure', 'main'),
            })
        # Const, Var: no event content — skip


class HLFRealizer:
    """Realizes an HLF as poem, prose, explanation, or narrative."""

    def __init__(self) -> None:
        self._walker = HLFWalker()

    def realize(self, parsed_hlf: "ParsedHLF", form: str = 'free_verse') -> str:  # noqa: F821
        """Main entry point. Returns realized text."""
        units = self._walker.extract_units(parsed_hlf.hlf)
        mode = parsed_hlf.output_mode

        if mode == OutputMode.POEM:
            return self._realize_poem(units, parsed_hlf, form)
        elif mode == OutputMode.EXPLANATION:
            return self._realize_explanation(units, parsed_hlf)
        elif mode == OutputMode.NARRATIVE:
            return self._realize_narrative(units, parsed_hlf)
        else:
            return self._realize_prose(units, parsed_hlf)

    # ── POEM ──────────────────────────────────────────────────────────

    def _realize_poem(self, units: list, parsed: "ParsedHLF", form: str) -> str:  # noqa: F821
        """Generate poem: each HLF unit → stanza; structural ops → form."""
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        from gofai_chat.generation.poetry.form_library import FORMS_BY_NAME

        form_key = form.lower().replace(' ', '_')
        form_obj = FORMS_BY_NAME.get(form_key, FORMS_BY_NAME.get('free_verse'))
        total_lines = getattr(form_obj, 'line_count', 8) if form_obj else 8

        if not units:
            units = [{
                'frame': 'Describing', 'roles': {'Theme': parsed.topic},
                'tense': 'present', 'aspect': 'simple', 'modal': None,
                'negated': False, 'structure': 'main', 'grade': 0.7,
            }]

        lines_per_unit = max(2, total_lines // len(units))
        searcher = HarmonicBeamSearch(width=16)

        stanzas: List[List[str]] = []
        for unit in units:
            mood = self._unit_to_mood(unit, parsed)
            roles = unit.get('roles', {})
            topic = (
                roles.get('Agent') or roles.get('Theme')
                or roles.get('theme') or roles.get('agent')
                or parsed.topic
            )
            theta_roles = {k: str(v) for k, v in roles.items() if v}

            modal_seed = self._modal_to_seed(unit.get('modal'))
            if modal_seed:
                theta_roles['modal_seed'] = modal_seed

            # Inject Wikipedia enrichment imagery and entity context
            enr = getattr(parsed, 'enrichment', None)
            if enr and enr.found:
                # Use entity name as the primary topic for specificity
                topic = enr.entity if enr.entity else topic
                # Add entity summary as a description role
                if enr.summary:
                    theta_roles.setdefault('description', enr.summary[:60])
                # Inject imagery words so HarmonicBeamSearch picks them up
                for i_img, img_word in enumerate(enr.imagery_words[:5]):
                    theta_roles.setdefault(f'imagery_{i_img}', img_word)
                # Merge Wikipedia theta_roles (parsed.theta_roles already merged; apply here too)
                for role_k, role_v in enr.theta_roles.items():
                    theta_roles.setdefault(role_k, role_v)

            try:
                lines = searcher.search(
                    topic=str(topic),
                    form=form_key,
                    mood=mood,
                    n_lines=lines_per_unit,
                    theta_roles=theta_roles or None,
                )
            except Exception:
                lines = [f"the {topic} remains", "in stillness and in time"][:lines_per_unit]

            lines = self._apply_structure(lines, unit['structure'], unit.get('negated', False))
            stanzas.append(lines)

        poem_lines: List[str] = []
        for i, stanza in enumerate(stanzas):
            poem_lines.extend(stanza)
            if i < len(stanzas) - 1:
                poem_lines.append('')

        poem_text = '\n'.join(poem_lines)
        grade_tag = self._score(poem_text, parsed)
        return f"{poem_text}\n\n{grade_tag}"

    # ── EXPLANATION ───────────────────────────────────────────────────

    def _realize_explanation(self, units: list, parsed: "ParsedHLF") -> str:  # noqa: F821
        """Factual explanation: WikiKnowledgeReasoner + DefaultReasoner + SurfaceRealizer."""
        topic = parsed.topic or 'that'
        parts: List[str] = []

        # WikiKnowledgeReasoner — rich formatted answer
        try:
            from gofai_chat.knowledge.wiki_reasoner import WikiKnowledgeReasoner
            wkr = WikiKnowledgeReasoner()
            wiki_answer = wkr.answer_question(topic, topic)
            if wiki_answer:
                parts.append(wiki_answer)
        except Exception:
            pass

        # DefaultReasoner properties (supplement when wiki answer is sparse)
        if not parts:
            try:
                from gofai_chat.inference.defaults import CoreDefaultReasoner
                props = CoreDefaultReasoner().most_typical_properties(topic, top_k=5)
                if props:
                    prop_strs = [p.replace('_', ' ') for p, _g in props]
                    parts.append(
                        f"{topic.title()} typically: {', '.join(prop_strs)}."
                    )
            except Exception:
                pass

        # WikiKnowledgeReasoner graded facts as fallback detail
        if not parts:
            try:
                from gofai_chat.knowledge.wiki_reasoner import WikiKnowledgeReasoner
                facts = WikiKnowledgeReasoner().reason_about(topic)
                fact_strs = [
                    p.replace('_', ' ')
                    for p, g in facts[:4]
                    if (g.to_prob() if hasattr(g, 'to_prob') else float(g)) > 0.4
                ]
                if fact_strs:
                    parts.append(f"{topic.title()}: {'; '.join(fact_strs)}.")
            except Exception:
                pass

        return '\n'.join(parts) if parts else f"I don't have specific information about {topic}."

    # ── PROSE ─────────────────────────────────────────────────────────

    def _realize_prose(self, units: list, parsed: "ParsedHLF") -> str:  # noqa: F821
        """Realize HLF as natural language prose using SurfaceRealizer + DefaultReasoner."""
        from gofai_chat.generation.surface_realization import SurfaceRealizer
        from gofai_chat.core.judgment import Context

        sr = SurfaceRealizer()
        ctx = Context()
        sentences: List[str] = []

        # Realize the full HLF as a surface sentence
        try:
            sentence = sr.realize(parsed.hlf, ctx)
            if sentence:
                sentences.append(sentence.strip().capitalize() + '.')
        except Exception:
            pass

        # Enrich with sentence-level unit realizations as additional sentences
        for unit in units[:3]:
            s = self._unit_to_sentence(unit)
            if s and s not in sentences:
                sentences.append(s)

        # Enrich with DefaultReasoner facts about the topic
        if parsed.topic:
            try:
                from gofai_chat.inference.defaults import CoreDefaultReasoner
                props = CoreDefaultReasoner().most_typical_properties(
                    parsed.topic, top_k=3
                )
                if props:
                    fact = (
                        f"{parsed.topic.title()} "
                        f"{props[0][0].replace('_', ' ')}s."
                    )
                    if fact not in sentences:
                        sentences.append(fact)
            except Exception:
                pass

        return ' '.join(sentences) if sentences else f"Regarding {parsed.topic}."

    # ── NARRATIVE ─────────────────────────────────────────────────────

    def _realize_narrative(self, units: list, parsed: "ParsedHLF") -> str:  # noqa: F821
        """Generate narrative: ForceDynamic structure skeleton from HLF units."""
        parts: List[str] = []
        for unit in units:
            roles = unit.get('roles', {})
            agent = roles.get('Agent', roles.get('agent', parsed.topic))
            theme = roles.get('Theme', roles.get('theme', ''))
            structure = unit.get('structure', 'main')
            if structure == 'antecedent':
                parts.append(f"When {agent} {self._frame_to_verb(unit['frame'])}.")
            elif structure == 'consequent':
                parts.append(f"And so {theme or agent} came to be.")
            else:
                parts.append(
                    f"{str(agent).title()} {self._frame_to_verb(unit['frame'])} {theme}."
                    .strip()
                )
        return ' '.join(parts) if parts else f"A story about {parsed.topic}."

    # ── Helpers ───────────────────────────────────────────────────────

    def _units_to_context(self, units: list, parsed: "ParsedHLF") -> str:  # noqa: F821
        """Summarise HLF units as a human-readable context string."""
        parts: List[str] = []
        for u in units[:3]:
            frame = u.get('frame', '')
            roles = u.get('roles', {})
            tense = u.get('tense', 'present')
            modal = u.get('modal', '')
            role_str = ', '.join(f"{k}={v}" for k, v in roles.items() if v)
            modal_part = f'/{modal}' if modal else ''
            parts.append(f"{frame}({role_str})[{tense}{modal_part}]")
        return '; '.join(parts)

    def _unit_to_mood(self, unit: dict, parsed: "ParsedHLF") -> str:  # noqa: F821
        if unit.get('negated'):
            return 'melancholic'
        modal = unit.get('modal')
        if modal == 'deontic':
            return 'urgent'
        if modal == 'epistemic':
            return 'contemplative'
        if modal == 'bouletic':
            return 'yearning'
        tense = unit.get('tense', 'present')
        if tense == 'past':
            return 'elegiac'
        structure = unit.get('structure', 'main')
        if structure == 'consequent':
            return 'resolved'
        if structure == 'antecedent':
            return 'tense'
        return 'neutral'

    def _modal_to_seed(self, modal_kind: Optional[str]) -> Optional[str]:
        return {'deontic': 'must', 'epistemic': 'perhaps', 'bouletic': 'longing'}.get(
            modal_kind  # type: ignore[arg-type]
        )

    def _apply_structure(self, lines: list, structure: str, negated: bool) -> list:
        if not lines:
            return lines
        if negated:
            first = lines[0]
            if not any(first.lower().startswith(w) for w in ('no ', 'not ', 'never ', 'without ')):
                lines = ['without ' + first] + lines[1:]
        if structure == 'antecedent':
            lines = ['when ' + lines[0].lstrip()] + lines[1:]
        elif structure == 'consequent':
            lines = ['then ' + lines[0].lstrip()] + lines[1:]
        return lines

    def _unit_to_sentence(self, unit: dict) -> str:
        roles = unit.get('roles', {})
        agent = roles.get('Agent', roles.get('agent', ''))
        theme = roles.get('Theme', roles.get('theme', ''))
        verb = self._frame_to_verb(unit.get('frame', 'Describing'))
        neg = 'does not ' if unit.get('negated') else ''
        modal = {'deontic': 'must', 'epistemic': 'might', 'bouletic': 'wants to'}.get(
            unit.get('modal'), ''  # type: ignore[arg-type]
        )
        parts = [p for p in [agent, modal, neg + verb, theme] if p]
        sentence = ' '.join(parts).strip()
        return sentence.capitalize() + '.' if sentence else ''

    def _frame_to_verb(self, frame_name: str) -> str:
        mapping = {
            'Producing': 'produces', 'Working': 'works', 'Taking': 'takes',
            'Moving': 'moves', 'Existing': 'exists', 'Describing': 'is',
            'Creating': 'creates', 'Destroying': 'destroys', 'Giving': 'gives',
            'Receiving': 'receives', 'Knowing': 'knows', 'Feeling': 'feels',
            'Causing': 'causes', 'Becoming': 'becomes', 'Having': 'has',
            'Flying': 'flies', 'Dying': 'dies', 'Changing': 'changes',
        }
        return mapping.get(frame_name,
                           frame_name.lower().rstrip('_event').rstrip('_activity'))

    def _score(self, poem_text: str, parsed: "ParsedHLF") -> str:  # noqa: F821
        """Score poem against HLF using HarmonyComputer."""
        try:
            from gofai_chat.core.gluing import GluingData
            from gofai_chat.harmony.constraints import SemSection
            from gofai_chat.harmony.harmony import HarmonyComputer

            sem = SemSection(lf=parsed.hlf)
            gluing = GluingData()
            try:
                gluing.set_section('sem', sem, Grade.from_prob(0.85))
            except TypeError:
                gluing.set_section('sem', sem)

            total = HarmonyComputer().total_harmony(gluing)
            pct = int(
                (total.to_prob() if hasattr(total, 'to_prob') else float(total)) * 100
            )
            return f"[Harmony: {pct}% | HLF: {type(parsed.hlf).__name__}({parsed.event_class})]"
        except Exception:
            return f"[HLF: {type(parsed.hlf).__name__}({parsed.event_class})]"
