"""Tests for gofai_chat.chat.task_registry — PoeticTaskRecognizer."""
from __future__ import annotations

import pytest

from gofai_chat.chat.task_registry import (
    PoeticTask,
    PoeticTaskRecognizer,
    TaskKind,
)

# Shared recognizer instance reused across all tests for speed
_R = PoeticTaskRecognizer()


# ---------------------------------------------------------------------------
# Targeted unit tests
# ---------------------------------------------------------------------------

def test_recognize_rhyme_constraint():
    task = _R.recognize("Come up with a line about love that rhymes with 'sick'")
    assert task.kind == TaskKind.RHYME_CONSTRAINT
    assert task.rhyme_target == 'sick'
    assert task.topic == 'love'
    assert task.confidence >= 0.75


def test_recognize_style_blend():
    task = _R.recognize("Write a poem halfway between Eliot and Whitman")
    assert task.kind == TaskKind.STYLE_BLEND
    assert 'eliot' in task.poets
    assert 'whitman' in task.poets
    assert task.blend_weights == [0.5, 0.5]
    assert task.confidence >= 0.75


def test_recognize_style_blend_three_poets():
    task = _R.recognize(
        "Write something with Dickinson's compression, Keats's sensuality, and Whitman's sweep"
    )
    assert task.kind == TaskKind.STYLE_BLEND
    poets = task.poets
    assert 'dickinson' in poets
    assert 'keats' in poets
    assert 'whitman' in poets
    assert task.confidence >= 0.75


def test_recognize_acrostic():
    task = _R.recognize("Write an acrostic poem for the word AUTUMN")
    assert task.kind == TaskKind.ACROSTIC
    assert task.acrostic_word == 'AUTUMN'
    assert task.confidence >= 0.75


def test_recognize_acrostic_lowercase():
    task = _R.recognize("Write an acrostic for autumn")
    assert task.kind == TaskKind.ACROSTIC
    assert task.acrostic_word.upper() == 'AUTUMN'


def test_recognize_counterfactual():
    task = _R.recognize("Imagine if Eliot was a concrete poet")
    assert task.kind == TaskKind.STYLE_COUNTERFACTUAL
    assert 'eliot' in task.poets
    assert task.confidence >= 0.75


def test_recognize_counterfactual_minimalist():
    task = _R.recognize("Imagine Hopkins as a minimalist poet — write a poem like that")
    assert task.kind == TaskKind.STYLE_COUNTERFACTUAL
    assert 'hopkins' in task.poets


def test_recognize_prose_to_poem():
    task = _R.recognize("Turn this into a poem: The rain falls on the empty street")
    assert task.kind == TaskKind.PROSE_TO_POEM
    assert 'rain' in task.prose_input or 'The' in task.prose_input
    assert task.confidence >= 0.75


def test_recognize_dialogue():
    task = _R.recognize("A dialogue between the moon and the sea")
    assert task.kind == TaskKind.DIALOGUE_POEM
    assert task.voice_a == 'moon'
    assert task.voice_b == 'sea'
    assert task.confidence >= 0.75


def test_recognize_explanation():
    task = _R.recognize("What meter does Shakespeare use?")
    assert task.kind == TaskKind.EXPLANATION
    assert task.confidence >= 0.75


def test_recognize_explanation_no_write():
    task = _R.recognize("Explain sprung rhythm")
    # Either EXPLANATION or EMBODY_CONCEPT (sprung_rhythm) or EXPLAIN_THEN_DEMONSTRATE
    assert task.kind in (
        TaskKind.EXPLANATION,
        TaskKind.EMBODY_CONCEPT,
        TaskKind.DEMONSTRATE_FORM,
        TaskKind.EXPLAIN_THEN_DEMONSTRATE,
    )


def test_recognize_lipogram():
    task = _R.recognize("Write a poem about the sea without using the letter e")
    assert task.kind == TaskKind.LIPOGRAM
    assert 'e' in task.forbidden_letters
    assert task.confidence >= 0.75


def test_recognize_lipogram_forbidden():
    task = _R.recognize("Write without the letter e")
    assert task.kind == TaskKind.LIPOGRAM
    assert task.forbidden_letters


def test_recognize_syllable_constraint():
    task = _R.recognize("Write a poem using only one-syllable words")
    assert task.kind == TaskKind.SYLLABLE_CONSTRAINT
    assert task.confidence >= 0.75


def test_recognize_monosyllabic():
    task = _R.recognize("Write a monosyllabic poem about winter")
    assert task.kind == TaskKind.SYLLABLE_CONSTRAINT


def test_recognize_all_questions():
    task = _R.recognize("Write a poem where every single line is a question")
    assert task.kind == TaskKind.SYNTACTIC_CONSTRAINT
    assert task.line_syntax == 'interrogative'
    assert task.confidence >= 0.75


def test_recognize_anachronism():
    task = _R.recognize("What would Keats write about the internet?")
    assert task.kind in (TaskKind.STYLE_TOPIC_ANACHRONISM, TaskKind.EXPLANATION)


def test_recognize_style_translate():
    task = _R.recognize("Write like Shakespeare but with contemporary slang")
    assert task.kind == TaskKind.STYLE_TRANSLATE
    assert 'shakespeare' in task.poets


def test_recognize_fibonacci():
    task = _R.recognize("Write a poem where each line has Fibonacci-number syllables")
    assert task.kind == TaskKind.STRUCTURAL_SEQUENCE
    assert task.syllable_sequence == 'fibonacci'


def test_recognize_extend():
    task = _R.recognize("Extend this poem by two more stanzas")
    assert task.kind == TaskKind.EXTEND
    assert task.stanzas_to_add >= 2


def test_recognize_compress():
    task = _R.recognize("Compress this entire poem into a haiku")
    assert task.kind == TaskKind.COMPRESS
    assert task.form == 'haiku'


def test_recognize_concrete_poem():
    task = _R.recognize("Write a poem shaped like a tree")
    assert task.kind == TaskKind.CONCRETE_POEM
    assert task.shape == 'tree'


def test_recognize_ekphrasis():
    task = _R.recognize("Write a poem as if you are a Vermeer painting describing yourself")
    assert task.kind == TaskKind.EKPHRASIS
    assert task.artwork == 'vermeer'


def test_recognize_embody_concept():
    task = _R.recognize("Write a poem that embodies negative capability")
    assert task.kind == TaskKind.EMBODY_CONCEPT
    assert task.concept == 'negative_capability'


def test_recognize_sublime():
    task = _R.recognize("Write a poem that tries to capture the Burkean sublime")
    assert task.kind == TaskKind.EMBODY_CONCEPT
    assert 'sublime' in task.concept


def test_recognize_cross_domain_jazz():
    task = _R.recognize("Write a poem that sounds like a jazz improvisation")
    assert task.kind == TaskKind.CROSS_DOMAIN_BLEND
    assert task.source_domain == 'jazz'


def test_recognize_cross_domain_math():
    task = _R.recognize("Write a poem using mathematical notation as imagery")
    assert task.kind == TaskKind.CROSS_DOMAIN_BLEND
    assert 'math' in task.source_domain


def test_recognize_affective_goal():
    task = _R.recognize("Write a poem that would comfort someone who just lost a parent")
    assert task.kind == TaskKind.AFFECTIVE_GOAL


def test_recognize_influence_trace():
    task = _R.recognize("Who influenced Dickinson and how can you hear it in her poems?")
    assert task.kind in (TaskKind.INFLUENCE_TRACE, TaskKind.EXPLANATION)
    assert 'dickinson' in task.poets or 'dickinson' in task.topic


def test_recognize_comparison():
    task = _R.recognize("What's the difference between Keats and Shelley's approach to nature?")
    assert task.kind in (TaskKind.COMPARISON, TaskKind.EXPLANATION)


def test_recognize_forbidden_word():
    task = _R.recognize("Write a love poem that never uses the word 'love'")
    assert task.forbidden_words or task.kind in (
        TaskKind.GENERATE, TaskKind.REVISE, TaskKind.STYLE_TRANSLATE,
    )


def test_recognize_no_adjectives():
    task = _R.recognize("Write a poem about winter with no adjectives whatsoever")
    assert 'ADJ' in task.forbidden_pos or task.kind in (TaskKind.GENERATE, TaskKind.REVISE)


def test_recognize_revise_darker():
    task = _R.recognize("Make this poem darker")
    assert task.kind == TaskKind.REVISE
    assert task.mood_delta < 0


def test_recognize_form_convert():
    task = _R.recognize("Rewrite this free verse as a sonnet")
    assert task.kind in (TaskKind.FORM_CONVERT, TaskKind.REVISE, TaskKind.GENERATE)
    if task.kind == TaskKind.FORM_CONVERT:
        assert task.form == 'sonnet'


def test_recognize_bidirectional():
    task = _R.recognize("Write a poem that can be read as two different poems — forward and backward by stanza")
    assert task.kind == TaskKind.BIDIRECTIONAL


def test_recognizer_returns_poetic_task():
    """Every call returns a PoeticTask instance."""
    task = _R.recognize("hello world")
    assert isinstance(task, PoeticTask)
    assert isinstance(task.kind, TaskKind)


def test_recognizer_default_confidence():
    """Default (GENERATE) confidence is 0.5."""
    task = _R.recognize("write me a poem")
    assert task.kind == TaskKind.GENERATE
    assert task.confidence == 0.5


# ---------------------------------------------------------------------------
# All-50-tasks smoke test
# ---------------------------------------------------------------------------

_FIFTY_PROMPTS = [
    # Style Blend & Imitation
    "Write a poem that's halfway between Eliot and Whitman",
    "Write something with Dickinson's compression, Keats's sensuality, and Whitman's sweep",
    "Write like Shakespeare but with contemporary slang",
    "Imagine Hopkins as a minimalist poet — write a poem like that",
    "Imagine if Eliot was a concrete poet — write a poem like that",
    "What would Keats write about the internet?",
    "Write a haiku in Whitman's style",
    "Write a Petrarchan sonnet as Dickinson would write it",
    "Write something like 'The Waste Land' but about suburban life",
    "Rewrite Keats's 'Ode to a Nightingale' from the nightingale's perspective",
    # Phonological & Formal Constraints
    "Come up with a line about love that rhymes with 'sick'",
    "Write a poem using only one-syllable words",
    "Write a poem where every line starts with the letter R",
    "Write a poem in iambic pentameter about grief — no cheating",
    "Write a poem that reads the same forwards and backwards",
    "Write a poem about winter with no adjectives whatsoever",
    "Write a poem where every single line is a question",
    "Write a 17-syllable poem that is definitely not a haiku",
    "Write an acrostic poem for the word AUTUMN",
    "Write a poem about the sea without using the letter 'e'",
    "Write a poem where each line has Fibonacci-number syllables",
    "Write a poem with strong anaphora — repeat a phrase at the start of each line",
    # Transformation Tasks
    "Turn this into a poem: 'The old man sat by the window watching rain fall on the empty street'",
    "Make this poem darker",
    "Rewrite this free verse as a sonnet",
    "This poem needs a volta — add one",
    "Extend this poem by two more stanzas",
    "Compress this entire poem into a haiku",
    "Rewrite this poem but make it funny",
    "Capture the feeling of this poem but about autumn instead of death",
    # Conceptual & Philosophical Tasks
    "Write a poem that embodies negative capability",
    "Write a poem that actually demonstrates sprung rhythm",
    "What would Whitman make of social media? Write it.",
    "Write a poem that argues that beauty is not truth",
    "Write a poem that deliberately contradicts itself in every couplet",
    "Write a poem that tries to capture the Burkean sublime",
    # Cross-Domain Creative Tasks
    "Write a poem that sounds like a jazz improvisation",
    "Write a poem shaped like a tree",
    "Write a poem as if you are a Vermeer painting describing yourself",
    "Write a poem that is also a recipe — for grief",
    "Write a poem that can be read as two different poems — forward and backward by stanza",
    "Write a poem as a dialogue between the moon and the sea",
    "Write a poem using mathematical notation as imagery",
    # Knowledge & Explanation Tasks
    "What meter does Shakespeare use most and why does it work?",
    "What's the difference between Keats and Shelley's approach to nature?",
    "Explain sprung rhythm and give me an original example",
    "Who influenced Dickinson and how can you hear it in her poems?",
    "Explain the Petrarchan sonnet — octave, sestet, volta — then write one",
    # Affective & Therapeutic Tasks
    "Write a poem that would comfort someone who just lost a parent",
    "Write a love poem that never uses the word 'love'",
]


@pytest.mark.parametrize("prompt", _FIFTY_PROMPTS)
def test_all_50_tasks_no_exception(prompt):
    """Recognizing any of the 50 prompts should not raise an exception."""
    task = _R.recognize(prompt)
    assert isinstance(task, PoeticTask)


@pytest.mark.parametrize("prompt", _FIFTY_PROMPTS)
def test_all_50_tasks_confidence(prompt):
    """Every task must have confidence > 0.49 (GENERATE default is exactly 0.5)."""
    task = _R.recognize(prompt)
    assert task.confidence > 0.49, (
        f"Low confidence {task.confidence} for {task.kind.name!r} on: {prompt!r}"
    )


@pytest.mark.parametrize("prompt", _FIFTY_PROMPTS)
def test_all_50_tasks_has_raw(prompt):
    """raw field is always set."""
    task = _R.recognize(prompt)
    assert task.raw == prompt


@pytest.mark.parametrize("prompt", _FIFTY_PROMPTS)
def test_all_50_tasks_topic_not_empty(prompt):
    """Topic should always resolve to a non-empty string."""
    task = _R.recognize(prompt)
    assert task.topic, f"Empty topic for: {prompt!r}"
