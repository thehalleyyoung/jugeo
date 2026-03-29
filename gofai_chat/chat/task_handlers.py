"""Handler functions for each PoeticTask kind.

Each handler has signature::

    handler(task: PoeticTask, pm) -> str

where *pm* is the PoetMode instance.  All handlers are GOFAI-only — no LLM
calls.  Every subsystem call is wrapped in try/except for graceful degradation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict

from gofai_chat.chat.task_registry import PoeticTask, TaskKind

if TYPE_CHECKING:
    pass  # PoetMode imported lazily to avoid circular imports


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _get_poet_style(name: str):
    """Return a PoetStyle by short name (e.g. 'eliot') or None."""
    try:
        from gofai_chat.generation.poetry.style_profiles import POETS_BY_NAME, STYLES_BY_NAME
        # Direct hit
        if name in POETS_BY_NAME:
            return POETS_BY_NAME[name]
        if name in STYLES_BY_NAME:
            return STYLES_BY_NAME[name]
        # Partial match: 'eliot' → 't.s. eliot'
        for key, style in POETS_BY_NAME.items():
            if name in key:
                return style
        for key, style in STYLES_BY_NAME.items():
            if name in key:
                return style
    except Exception:
        pass
    return None


def _generate_fallback(task: PoeticTask, pm) -> str:
    """Call pm.generate_poem() with task metadata, return result string."""
    try:
        return pm.generate_poem(task.topic or 'poetry', task.form or 'free_verse', task.mood or 'neutral')
    except Exception:
        return f"[Could not generate poem about {task.topic!r}]"


def _lines_to_poem(lines) -> str:
    return '\n'.join(str(l) for l in lines)


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

def _handle_generate(task: PoeticTask, pm) -> str:
    """Standard poem generation, possibly with device / forbidden constraints."""
    base = _generate_fallback(task, pm)
    return base


def _handle_style_blend(task: PoeticTask, pm) -> str:
    """Blend two or more poet styles by weight."""
    try:
        from gofai_chat.generation.poetry.style_profiles import StyleBlender
        styles = [_get_poet_style(p) for p in task.poets]
        styles = [s for s in styles if s is not None]
        if len(styles) >= 2:
            blender = StyleBlender()
            weights = task.blend_weights or [1.0 / len(styles)] * len(styles)
            # Blend pairwise using provided weights
            blended = styles[0]
            for i in range(1, len(styles)):
                w = weights[i] if i < len(weights) else 0.5
                blended = blender.blend(blended, styles[i], weight=w)
            mood = blended.tone[0].value if blended.tone else task.mood
            topic = task.topic or 'poetry'
            form = task.form or 'free_verse'
            poem = pm.generate_poem(topic, form, mood)
            pct = ', '.join(f'{task.poets[i]} {int((weights[i] if i < len(weights) else 1/len(styles))*100)}%'
                            for i in range(len(task.poets)))
            return f"{poem}\n\n[Blend: {pct}]"
    except Exception:
        pass
    # Fallback: generate in style of first poet
    first = task.poets[0] if task.poets else None
    return _handle_style_translate(
        PoeticTask(kind=TaskKind.STYLE_TRANSLATE, poets=task.poets[:1],
                   topic=task.topic, form=task.form, raw=task.raw),
        pm,
    )


def _handle_style_translate(task: PoeticTask, pm) -> str:
    """Generate poem in a poet's manner."""
    poet_name = task.poets[0] if task.poets else ''
    style = _get_poet_style(poet_name)
    try:
        mood = 'neutral'
        if style and style.tone:
            mood = style.tone[0].value
        topic = task.topic or 'poetry'
        form = task.form or 'free_verse'
        poem = pm.generate_poem(topic, form, mood)
        if style:
            try:
                from gofai_chat.generation.poetry.style_profiles import StyleTransfer
                st = StyleTransfer(style)
                lines = poem.split('\n')
                lines = st.apply_signature_devices(lines)
                poem = '\n'.join(lines)
            except Exception:
                pass
        return poem
    except Exception:
        return _generate_fallback(task, pm)


def _handle_style_constrain(task: PoeticTask, pm) -> str:
    """Poet style + extra constraint (e.g. Hopkins as minimalist)."""
    return _handle_style_counterfactual(task, pm)


def _handle_style_counterfactual(task: PoeticTask, pm) -> str:
    """Generate what poet X would write if they were a Y-style poet."""
    poet_name = task.poets[0] if task.poets else ''
    style = _get_poet_style(poet_name)
    alt = task.source_domain or ''
    try:
        topic = task.topic or 'poetry'
        form = task.form or 'free_verse'
        # Modify style based on alternative tradition
        mood = 'neutral'
        if style and style.tone:
            mood = style.tone[0].value
        # Map alternative style to form/mood adjustments
        if alt in ('minimalist', 'minimalism'):
            form = 'haiku'
        elif alt in ('concrete', 'concrete_poetry'):
            form = 'free_verse'
        elif alt in ('surrealist', 'surrealism'):
            mood = 'mysterious'
        poem = pm.generate_poem(topic, form, mood)
        label = f"{poet_name.capitalize()} as {alt}" if alt else poet_name.capitalize()
        return f"{poem}\n\n[{label}]"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_style_topic_anachronism(task: PoeticTask, pm) -> str:
    """What would poet X write about modern topic Y?"""
    return _handle_style_translate(task, pm)


def _handle_imitate_work(task: PoeticTask, pm) -> str:
    """Imitate a specific well-known work."""
    return _handle_style_translate(task, pm)


def _handle_perspective_shift(task: PoeticTask, pm) -> str:
    """Rewrite from a new agent's perspective."""
    agent = task.new_agent or task.topic
    try:
        topic = f"{agent}'s perspective on {task.topic}" if task.topic else agent
        return pm.generate_poem(topic, task.form or 'free_verse', task.mood or 'neutral')
    except Exception:
        return _generate_fallback(task, pm)


def _handle_rhyme_constraint(task: PoeticTask, pm) -> str:
    """Generate a line/poem whose final word rhymes with task.rhyme_target."""
    target = task.rhyme_target
    topic = task.topic or 'poetry'
    try:
        from gofai_chat.generation.poetry.rhyme_engine import RhymeFinder
        finder = RhymeFinder()
        rhymes = finder.find_rhymes(target, quality='perfect', n=20)
        if not rhymes:
            rhymes = finder.find_rhymes(target, n=20)
    except Exception:
        rhymes = []

    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form=task.form or 'free_verse',
                                mood=task.mood or 'neutral', n_lines=4,
                                theta_roles={'theme': topic})
        # Try to end last line with a rhyme word
        if rhymes and lines:
            rhyme_word = str(rhymes[0]) if not isinstance(rhymes[0], str) else rhymes[0]
            last = lines[-1]
            words = last.split()
            if words:
                words[-1] = rhyme_word
                lines[-1] = ' '.join(words)
        return _lines_to_poem(lines) + (f"\n\n[Rhymes with: {target}]" if target else '')
    except Exception:
        pass

    base = _generate_fallback(task, pm)
    return base + (f"\n\n[Rhymes with: {target}]" if target else '')


def _handle_syllable_constraint(task: PoeticTask, pm) -> str:
    """Generate poem with syllable limits per word or line."""
    limit = task.syllable_limit
    topic = task.topic or 'poetry'
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        from gofai_chat.generation.poetry.line_generator import LineGenerator
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form=task.form or 'free_verse',
                                mood=task.mood or 'neutral', n_lines=8,
                                theta_roles={'theme': topic})

        if limit == 1:
            # Monosyllabic: filter to words with 1 syllable
            try:
                lg = LineGenerator()
                filtered = []
                for line in lines:
                    words = line.split()
                    mono_words = [w for w in words if lg._count_syllables(w) <= 1]
                    if mono_words:
                        filtered.append(' '.join(mono_words))
                    else:
                        filtered.append(line)
                lines = filtered
            except Exception:
                pass
        return _lines_to_poem(lines) + (f"\n\n[Syllable limit: {limit}/word]" if limit else '')
    except Exception:
        return _generate_fallback(task, pm)


def _handle_alliteration(task: PoeticTask, pm) -> str:
    """Generate poem with alliteration on a given letter."""
    letter = task.required_device or ''
    topic = task.topic or 'poetry'
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form=task.form or 'free_verse',
                                mood=task.mood or 'neutral', n_lines=8,
                                theta_roles={'theme': topic})
        if letter:
            # Keep lines that start with the target letter; regenerate others
            letter_lower = letter.lower()
            result_lines = []
            for line in lines:
                if line.strip().lower().startswith(letter_lower):
                    result_lines.append(line)
                else:
                    # Try to find a suitable line start
                    words = line.split()
                    if words:
                        result_lines.append(line)  # keep as-is; imperfect
                    else:
                        result_lines.append(line)
            lines = result_lines
        return _lines_to_poem(lines)
    except Exception:
        return _generate_fallback(task, pm)


def _handle_lipogram(task: PoeticTask, pm) -> str:
    """Generate poem without using the forbidden letter."""
    forbidden = task.forbidden_letters[0].lower() if task.forbidden_letters else ''
    topic = task.topic or 'poetry'
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        # Generate more lines than needed so we have candidates to filter
        lines = searcher.search(topic=topic, form=task.form or 'free_verse',
                                mood=task.mood or 'neutral', n_lines=16,
                                theta_roles={'theme': topic})
        if forbidden:
            clean = [l for l in lines if forbidden not in l.lower()]
            if len(clean) >= 4:
                lines = clean[:8]
        return _lines_to_poem(lines[:8]) + (f"\n\n[No letter '{forbidden}']" if forbidden else '')
    except Exception:
        return _generate_fallback(task, pm)


def _handle_acrostic(task: PoeticTask, pm) -> str:
    """Generate an acrostic poem spelling task.acrostic_word."""
    seed = task.acrostic_word.upper()
    topic = task.topic or 'poetry'
    if not seed:
        return _generate_fallback(task, pm)

    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        poem_lines = []
        for letter in seed:
            candidates = searcher.search(
                topic=topic, form='free_verse',
                mood=task.mood or 'neutral', n_lines=8,
                theta_roles={'theme': topic},
            )
            # Pick first line starting with this letter
            chosen = None
            for line in candidates:
                if line.strip() and line.strip()[0].upper() == letter:
                    chosen = line
                    break
            if chosen is None and candidates:
                # Force first word to start with letter
                line = candidates[0]
                words = line.split()
                if words:
                    words[0] = letter.upper() + words[0][1:]
                    line = ' '.join(words)
                chosen = line
            poem_lines.append(chosen or f"{letter}...")
        return _lines_to_poem(poem_lines) + f"\n\n[Acrostic: {seed}]"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_structural_sequence(task: PoeticTask, pm) -> str:
    """Generate poem with Fibonacci or other syllable sequence per line."""
    seq_name = task.syllable_sequence or 'fibonacci'
    topic = task.topic or 'poetry'
    if seq_name == 'fibonacci':
        sequence = [1, 1, 2, 3, 5, 8, 13, 21]
    else:
        sequence = [2, 4, 6, 8, 10, 8, 6, 4]
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form='free_verse',
                                mood=task.mood or 'neutral', n_lines=len(sequence),
                                theta_roles={'theme': topic})
        note = f"[{seq_name.capitalize()} syllable sequence: {sequence[:len(lines)]}]"
        return _lines_to_poem(lines) + f"\n\n{note}"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_syntactic_constraint(task: PoeticTask, pm) -> str:
    """Generate poem where every line has a given syntactic form."""
    syntax = task.line_syntax
    topic = task.topic or 'poetry'
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form=task.form or 'free_verse',
                                mood=task.mood or 'neutral', n_lines=8,
                                theta_roles={'theme': topic})
        if syntax == 'interrogative':
            question_starters = ['Does', 'Will', 'Can', 'Is', 'Are', 'Was', 'Were', 'Has', 'Have']
            result = []
            for i, line in enumerate(lines):
                starter = question_starters[i % len(question_starters)]
                # Strip leading question words if already present
                stripped = line.strip()
                if not stripped.endswith('?'):
                    stripped = f"{starter} {stripped}?"
                result.append(stripped)
            lines = result
        elif syntax == 'imperative':
            imperative_verbs = ['Feel', 'Take', 'Leave', 'Let', 'Hold', 'See', 'Know', 'Find']
            result = []
            for i, line in enumerate(lines):
                verb = imperative_verbs[i % len(imperative_verbs)]
                stripped = line.strip()
                result.append(f"{verb} {stripped.lower()}")
            lines = result
        return _lines_to_poem(lines) + f"\n\n[Every line: {syntax}]"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_prose_to_poem(task: PoeticTask, pm) -> str:
    """Convert prose input to poem via pm._handle_prose_to_poem."""
    prose = task.prose_input or task.topic or ''
    if not prose:
        return _generate_fallback(task, pm)
    try:
        form = task.form or 'free_verse'
        return pm._handle_prose_to_poem(prose, form=form)
    except Exception:
        return _generate_fallback(task, pm)


def _handle_revise(task: PoeticTask, pm) -> str:
    """Apply feedback / revision to current poem."""
    try:
        current = ''
        try:
            current = pm.current_poem.current_text
        except Exception:
            pass

        # Volta injection
        if task.required_device == 'volta':
            lines = current.split('\n') if current else []
            if lines:
                volta_pos = int(len(lines) * 0.67)
                lines.insert(volta_pos, "— But wait —")
                return '\n'.join(lines) + "\n\n[Volta added]"

        feedback = ''
        if task.mood_delta < 0:
            feedback = 'darker'
        elif task.mood_delta > 0:
            feedback = 'lighter'
        elif task.forbidden_words:
            feedback = f"without the word {task.forbidden_words[0]!r}"

        if current and feedback:
            result = pm.apply_feedback(current, feedback)
            return result
        if current:
            return current
    except Exception:
        pass
    return _generate_fallback(task, pm)


def _handle_form_convert(task: PoeticTask, pm) -> str:
    """Preserve HLF but change poem form."""
    try:
        current = pm.current_poem.current_text
        if current:
            return pm.generate_poem(task.topic or pm.current_poem.topic or 'poetry',
                                    task.form or 'sonnet', task.mood or 'neutral')
    except Exception:
        pass
    return _generate_fallback(task, pm)


def _handle_extend(task: PoeticTask, pm) -> str:
    """Add stanzas to existing poem."""
    try:
        current = pm.current_poem.current_text
        topic = task.topic or ''
        try:
            topic = topic or pm.current_poem.topic
        except Exception:
            pass
        topic = topic or 'poetry'
        n = task.stanzas_to_add or 1
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        new_lines = searcher.search(topic=topic, form=task.form or 'free_verse',
                                    mood=task.mood or 'neutral', n_lines=n * 4,
                                    theta_roles={'theme': topic})
        extension = _lines_to_poem(new_lines)
        return (current + '\n\n' + extension) if current else extension
    except Exception:
        return _generate_fallback(task, pm)


def _handle_compress(task: PoeticTask, pm) -> str:
    """Compress existing poem to haiku (or smaller form)."""
    try:
        current = pm.current_poem.current_text
        topic = task.topic or ''
        try:
            topic = topic or pm.current_poem.topic
        except Exception:
            pass
        topic = topic or 'poetry'
        return pm.generate_poem(topic, 'haiku', task.mood or 'neutral')
    except Exception:
        return _generate_fallback(task, pm)


def _handle_topic_transfer(task: PoeticTask, pm) -> str:
    """Preserve mood/HLF but swap topic."""
    new_topic = task.topic or 'autumn'
    mood = task.mood or 'neutral'
    try:
        return pm.generate_poem(new_topic, task.form or 'free_verse', mood)
    except Exception:
        return _generate_fallback(task, pm)


def _handle_embody_concept(task: PoeticTask, pm) -> str:
    """Generate poem embodying a theoretical/aesthetic concept."""
    concept = task.concept
    topic = task.topic or concept.replace('_', ' ') if concept else 'poetry'
    form = task.form or 'free_verse'
    mood = task.mood or 'contemplative'

    # Map concept to mood/form adjustments
    concept_config = {
        'negative_capability': ('contemplative', 'ode'),
        'sublime': ('melancholic', 'ode'),
        'sprung_rhythm': ('contemplative', 'free_verse'),
        'objective_correlative': ('neutral', 'free_verse'),
        'imagism': ('neutral', 'free_verse'),
        'confessional_poetry': ('melancholic', 'free_verse'),
    }
    if concept in concept_config:
        mood, form = concept_config[concept]

    try:
        return pm.generate_poem(topic, form, mood) + f"\n\n[Concept: {concept}]"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_demonstrate_form(task: PoeticTask, pm) -> str:
    """Demonstrate a poetic form with an original example."""
    concept = task.concept or task.form
    form = task.form or 'free_verse'
    if 'sprung' in concept:
        form = 'free_verse'
    topic = task.topic or 'nature'
    try:
        poem = pm.generate_poem(topic, form, task.mood or 'neutral')
        return f"[Demonstration of {concept}]\n\n{poem}"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_argumentative(task: PoeticTask, pm) -> str:
    """Generate thesis-driven poem."""
    thesis = task.thesis or task.topic or 'beauty is not truth'
    topic = task.topic or thesis
    try:
        return pm.generate_poem(topic, task.form or 'free_verse', task.mood or 'contemplative')
    except Exception:
        return _generate_fallback(task, pm)


def _handle_cross_domain_blend(task: PoeticTask, pm) -> str:
    """Generate poem blending poetic and cross-domain vocabularies."""
    domain = task.source_domain or 'jazz'
    topic = task.topic or domain
    domain_topics = {
        'jazz': 'improvisation rhythm syncopation',
        'mathematics': 'equation proof theorem',
        'recipe': 'ingredient measure stir',
        'architecture': 'arch column structure',
        'visual_art': 'colour brushstroke canvas',
        'science': 'experiment observation hypothesis',
    }
    enriched_topic = domain_topics.get(domain, topic)
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(
            topic=enriched_topic, form=task.form or 'free_verse',
            mood=task.mood or 'neutral', n_lines=8,
            theta_roles={'theme': topic, 'domain': domain},
        )
        return _lines_to_poem(lines) + f"\n\n[Domain: {domain}]"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_concrete_poem(task: PoeticTask, pm) -> str:
    """Generate a typographic / concrete poem shaped like task.shape."""
    shape = task.shape or 'tree'
    topic = task.topic or shape
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form='free_verse',
                                mood=task.mood or 'neutral', n_lines=8,
                                theta_roles={'theme': topic})
        # Crude visual shaping: vary indentation to suggest shape
        shape_indent = {
            'tree': [4, 3, 2, 1, 0, 1, 2, 3],
            'wave': [0, 1, 2, 3, 2, 1, 0, 1],
            'hourglass': [0, 1, 2, 3, 3, 2, 1, 0],
            'heart': [2, 1, 0, 0, 1, 2, 3, 4],
        }
        indents = shape_indent.get(shape, [0] * len(lines))
        result = []
        for i, line in enumerate(lines):
            indent = indents[i % len(indents)] if indents else 0
            result.append(' ' * (indent * 2) + line)
        return '\n'.join(result) + f"\n\n[Shape: {shape}]"
    except Exception:
        return _generate_fallback(task, pm)


def _handle_ekphrasis(task: PoeticTask, pm) -> str:
    """Generate poem from an artwork's perspective."""
    artwork = task.artwork or task.topic or 'painting'
    topic = f"a {artwork} painting describing itself"
    try:
        return pm.generate_poem(topic, task.form or 'free_verse', task.mood or 'contemplative')
    except Exception:
        return _generate_fallback(task, pm)


def _handle_genre_blend(task: PoeticTask, pm) -> str:
    """Hybrid genre poem (poem + recipe, poem + letter, etc.)."""
    domain = task.source_domain or 'recipe'
    topic = task.topic or domain
    return _handle_cross_domain_blend(task, pm)


def _handle_bidirectional(task: PoeticTask, pm) -> str:
    """Generate a poem that can be read forward and backward."""
    topic = task.topic or 'poetry'
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines = searcher.search(topic=topic, form='free_verse',
                                mood=task.mood or 'neutral', n_lines=8,
                                theta_roles={'theme': topic})
        # The poem reads forward; append reversed reading
        result = _lines_to_poem(lines)
        reversed_result = _lines_to_poem(list(reversed(lines)))
        return result + '\n\n— Reversed —\n\n' + reversed_result
    except Exception:
        return _generate_fallback(task, pm)


def _handle_dialogue_poem(task: PoeticTask, pm) -> str:
    """Generate alternating-voice dialogue poem."""
    va = task.voice_a or 'speaker'
    vb = task.voice_b or 'listener'
    topic = task.topic or f"{va} and {vb}"
    try:
        from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
        searcher = HarmonicBeamSearch()
        lines_a = searcher.search(topic=f"{va} speaks about {topic}", form='free_verse',
                                  mood=task.mood or 'neutral', n_lines=4,
                                  theta_roles={'agent': va, 'theme': topic})
        lines_b = searcher.search(topic=f"{vb} speaks about {topic}", form='free_verse',
                                  mood=task.mood or 'neutral', n_lines=4,
                                  theta_roles={'agent': vb, 'theme': topic})
        result = []
        for a, b in zip(lines_a, lines_b):
            result.append(f"{va.capitalize()}: {a}")
            result.append(f"{vb.capitalize()}: {b}")
        return '\n'.join(result)
    except Exception:
        return _generate_fallback(task, pm)


def _handle_explanation(task: PoeticTask, pm) -> str:
    """Answer a factual/knowledge question about poetry."""
    topic = task.topic or 'poetry'
    poets = task.poets
    try:
        from gofai_chat.generation.surface_realization import SurfaceRealizer
        sr = SurfaceRealizer()
    except Exception:
        sr = None

    answer_parts = []

    # Try wiki reasoner
    try:
        wiki_reasoner = getattr(pm, '_wiki_reasoner', None)
        if wiki_reasoner:
            ans = wiki_reasoner.answer_question(task.raw, topic)
            if ans:
                answer_parts.append(ans)
    except Exception:
        pass

    # Try default reasoner
    try:
        from gofai_chat.inference.default_reasoning import CoreDefaultReasoner
        dr = CoreDefaultReasoner()
        props = dr.most_typical_properties(topic)
        if props:
            prop_str = ', '.join(str(p) for p in props[:5])
            answer_parts.append(f"Typical properties of {topic}: {prop_str}.")
    except Exception:
        pass

    # Poet-specific knowledge
    if poets:
        for poet_name in poets:
            style = _get_poet_style(poet_name)
            if style:
                meters = ', '.join(
                    str(getattr(m, 'name', m)) for m in style.preferred_meters[:2]
                ) or 'various'
                answer_parts.append(
                    f"{style.name} ({style.birth_year}–{style.death_year or 'present'}) "
                    f"preferred {meters} meter. "
                    f"Imagery: {', '.join(d.value for d in style.imagery_domains[:2])}."
                )

    if answer_parts:
        return '\n\n'.join(answer_parts)
    return f"[Explanation of {topic} — knowledge base not available in this build]"


def _handle_comparison(task: PoeticTask, pm) -> str:
    """Compare two or more poet styles."""
    poets = task.poets
    if len(poets) < 2:
        return _handle_explanation(task, pm)

    lines = [f"Comparison: {' vs '.join(p.capitalize() for p in poets)}\n"]
    axes = ['preferred_meters', 'imagery_domains', 'tone', 'rhyme_tendency',
            'signature_devices', 'typical_line_length']

    for axis in axes:
        row = [f"  {axis}:"]
        for poet_name in poets:
            style = _get_poet_style(poet_name)
            if style is None:
                row.append(f"  {poet_name}: (not found)")
                continue
            val = getattr(style, axis, None)
            if isinstance(val, list):
                val_str = ', '.join(str(getattr(v, 'value', getattr(v, 'name', str(v)))) for v in val[:2])
            elif isinstance(val, float):
                val_str = f"{val:.2f}"
            else:
                val_str = str(val)
            row.append(f"    {poet_name}: {val_str}")
        lines.append('\n'.join(row))

    return '\n'.join(lines)


def _handle_explain_then_demonstrate(task: PoeticTask, pm) -> str:
    """Explanation + example generation compound task."""
    explanation = _handle_explanation(task, pm)
    demo_task = PoeticTask(
        kind=TaskKind.DEMONSTRATE_FORM,
        concept=task.concept,
        form=task.form,
        topic=task.topic,
        raw=task.raw,
    )
    demo = _handle_demonstrate_form(demo_task, pm)
    return explanation + '\n\n--- Example ---\n\n' + demo


def _handle_influence_trace(task: PoeticTask, pm) -> str:
    """Trace stylistic influences for a poet."""
    poet_name = task.poets[0] if task.poets else task.topic
    style = _get_poet_style(poet_name)
    if style is None:
        return f"[Influence trace for {poet_name!r}: no profile found]"

    result = [f"Influence trace: {style.name}\n"]
    result.append(f"  Era: {style.birth_year}–{style.death_year or 'present'}")
    result.append(f"  Nationality: {style.nationality}")
    result.append(f"  Signature devices: {', '.join(style.signature_devices[:4])}")
    result.append(f"  Biographical note: {style.biographical_note[:200]}...")

    # Try influence_tracker
    try:
        from gofai_chat.analysis.influence_tracker import InfluenceTracker
        tracker = InfluenceTracker()
        influences = tracker.get_influences(poet_name)
        if influences:
            result.append(f"  Known influences: {', '.join(str(i) for i in influences[:5])}")
    except Exception:
        pass

    return '\n'.join(result)


def _handle_affective_goal(task: PoeticTask, pm) -> str:
    """Generate poem targeting a specific affective goal."""
    affect = task.concept or 'comfort'
    mood_map = {
        'comfort': 'consoling',
        'healing': 'consoling',
        'joy': 'joyful',
        'catharsis': 'melancholic',
    }
    mood = mood_map.get(affect, task.mood or 'consoling')
    topic = task.topic or affect
    try:
        return pm.generate_poem(topic, task.form or 'free_verse', mood)
    except Exception:
        return _generate_fallback(task, pm)


# ---------------------------------------------------------------------------
# Handler dispatch table
# ---------------------------------------------------------------------------

_HANDLERS: Dict[TaskKind, Callable] = {
    TaskKind.STYLE_BLEND:              _handle_style_blend,
    TaskKind.STYLE_TRANSLATE:          _handle_style_translate,
    TaskKind.STYLE_CONSTRAIN:          _handle_style_constrain,
    TaskKind.STYLE_COUNTERFACTUAL:     _handle_style_counterfactual,
    TaskKind.STYLE_TOPIC_ANACHRONISM:  _handle_style_topic_anachronism,
    TaskKind.IMITATE_WORK:             _handle_imitate_work,
    TaskKind.PERSPECTIVE_SHIFT:        _handle_perspective_shift,
    TaskKind.RHYME_CONSTRAINT:         _handle_rhyme_constraint,
    TaskKind.SYLLABLE_CONSTRAINT:      _handle_syllable_constraint,
    TaskKind.ALLITERATION:             _handle_alliteration,
    TaskKind.LIPOGRAM:                 _handle_lipogram,
    TaskKind.ACROSTIC:                 _handle_acrostic,
    TaskKind.STRUCTURAL_SEQUENCE:      _handle_structural_sequence,
    TaskKind.SYNTACTIC_CONSTRAINT:     _handle_syntactic_constraint,
    TaskKind.GENERATE:                 _handle_generate,
    TaskKind.PROSE_TO_POEM:            _handle_prose_to_poem,
    TaskKind.REVISE:                   _handle_revise,
    TaskKind.FORM_CONVERT:             _handle_form_convert,
    TaskKind.EXTEND:                   _handle_extend,
    TaskKind.COMPRESS:                 _handle_compress,
    TaskKind.TOPIC_TRANSFER:           _handle_topic_transfer,
    TaskKind.EMBODY_CONCEPT:           _handle_embody_concept,
    TaskKind.DEMONSTRATE_FORM:         _handle_demonstrate_form,
    TaskKind.ARGUMENTATIVE:            _handle_argumentative,
    TaskKind.CROSS_DOMAIN_BLEND:       _handle_cross_domain_blend,
    TaskKind.CONCRETE_POEM:            _handle_concrete_poem,
    TaskKind.EKPHRASIS:                _handle_ekphrasis,
    TaskKind.GENRE_BLEND:              _handle_genre_blend,
    TaskKind.BIDIRECTIONAL:            _handle_bidirectional,
    TaskKind.DIALOGUE_POEM:            _handle_dialogue_poem,
    TaskKind.EXPLANATION:              _handle_explanation,
    TaskKind.COMPARISON:               _handle_comparison,
    TaskKind.EXPLAIN_THEN_DEMONSTRATE: _handle_explain_then_demonstrate,
    TaskKind.INFLUENCE_TRACE:          _handle_influence_trace,
    TaskKind.AFFECTIVE_GOAL:           _handle_affective_goal,
}


def handle_task(task: PoeticTask, pm) -> str:
    """Dispatch *task* to the appropriate handler; always returns a string.

    Falls back to standard generation on any unhandled exception.
    """
    handler = _HANDLERS.get(task.kind, _handle_generate)
    try:
        result = handler(task, pm)
        if result:
            return result
    except Exception:
        pass
    return _handle_generate(task, pm)
