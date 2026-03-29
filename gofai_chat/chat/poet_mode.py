from __future__ import annotations
"""Interactive poet mode — the default conversational mode of the GOFAI chatbot.

This module provides the main REPL loop, session poem management, mood tracking,
and prompt generation.  All quality judgments are expressed as :class:`Grade`
values in the log-probability semiring so they compose properly with the rest of
the Harmonic Theory pipeline.

Paper ref:
    §Pragmatics — Interactive Composition; §Information — Session State.
"""

__all__ = [
    "PoetMode",
    "SessionPoem",
    "PoemVersionControl",
    "MoodTracker",
    "PoetryPromptGenerator",
]

import difflib
import math
import random
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gofai_chat.core.grade import Grade
from gofai_chat.chat.dialogue_manager import (
    DialogueState,
    DialogueAct,
    PoetryDialogueManager,
    ConversationTurnMemory,
    TopicModel,
    build_initial_state,
)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:
    import nltk
    from nltk.tokenize import word_tokenize, sent_tokenize
    from nltk.corpus import wordnet as wn
    _HAS_NLTK = True
except ImportError:
    nltk = None  # type: ignore[assignment]
    _HAS_NLTK = False

try:
    from rapidfuzz import fuzz as rfuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    rfuzz = None  # type: ignore[assignment]
    _HAS_RAPIDFUZZ = False

try:
    import nltk.corpus.cmudict as cmu_module
    _CMU_DICT = None   # loaded lazily
    _HAS_CMU = True
except ImportError:
    _CMU_DICT = None
    _HAS_CMU = False

# Wikipedia trigger token set — matched before normal intent dispatch.
_WIKI_TRIGGERS: frozenset = frozenset(
    {"look", "search", "wikipedia", "find", "lookup"}
)

# Trigger phrases for the prose → LF → poem pipeline.
_PROSE_TRIGGERS: frozenset = frozenset({
    "turn this into",
    "express as poetry",
    "write a poem that says",
    "make a poem from",
    "poeticize",
    "turn into verse",
    "express this as",
    "a poem that means",
    "saying:",
})

# Prefix phrases used to extract the search query after the trigger.
_WIKI_QUERY_PREFIXES: List[str] = [
    "search wikipedia for",
    "what does wikipedia say about",
    "find information about",
    "look up",
    "wikipedia",
    "search for",
    "find",
    "lookup",
    "search",
]


def _get_cmu_dict() -> Optional[Dict[str, Any]]:
    """Lazily load the CMU pronouncing dictionary."""
    global _CMU_DICT, _HAS_CMU
    if _CMU_DICT is not None:
        return _CMU_DICT
    if not _HAS_NLTK:
        return None
    try:
        from nltk.corpus import cmudict
        _CMU_DICT = dict(cmudict.entries())
        return _CMU_DICT
    except Exception:
        try:
            nltk.download("cmudict", quiet=True)
            from nltk.corpus import cmudict
            _CMU_DICT = dict(cmudict.entries())
            return _CMU_DICT
        except Exception:
            _HAS_CMU = False
            return None


# ---------------------------------------------------------------------------
# SessionPoem
# ---------------------------------------------------------------------------

@dataclass
class SessionPoem:
    """The poem currently under composition in a session.

    Attributes
    ----------
    current_text:
        The active poem text (may be empty string if no poem yet).
    title:
        Optional title.
    topic:
        The thematic focus.
    form:
        Poetic form name (sonnet, haiku, free verse, …).
    mood:
        Affective tone at time of creation.
    versions:
        All committed versions, oldest first.
    grades:
        The :class:`Grade` assigned to each version.
    feedbacks:
        The feedback note attached to each commit.
    created_at:
        Unix timestamp of session start.
    """
    current_text: str = ""
    title: str = ""
    topic: str = "poetry"
    form: str = "free verse"
    mood: str = "neutral"
    versions: List[str] = field(default_factory=list)
    grades: List[Grade] = field(default_factory=list)
    feedbacks: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def best_version(self) -> Optional[str]:
        """Return the version with the highest grade, or None."""
        if not self.versions:
            return None
        if not self.grades:
            return self.versions[-1]
        best_idx = max(range(len(self.grades)), key=lambda i: self.grades[i])
        return self.versions[best_idx]

    @property
    def current_grade(self) -> Grade:
        """Grade of the most recent committed version."""
        return self.grades[-1] if self.grades else Grade.impossible()

    def is_empty(self) -> bool:
        return not self.current_text.strip()

    def line_count(self) -> int:
        return len([l for l in self.current_text.splitlines() if l.strip()])

    def get_line(self, n: int) -> Optional[str]:
        """Return line *n* (1-indexed) or None if out of range."""
        lines = [l for l in self.current_text.splitlines() if l.strip()]
        if 1 <= n <= len(lines):
            return lines[n - 1]
        return None

    def replace_line(self, n: int, new_line: str) -> str:
        """Return a new poem text with line *n* replaced by *new_line*."""
        lines = self.current_text.splitlines()
        if 1 <= n <= len(lines):
            lines[n - 1] = new_line
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PoemVersionControl
# ---------------------------------------------------------------------------

class PoemVersionControl:
    """Git-inspired version control for poem drafts.

    Each call to :meth:`commit` stores the poem text together with the
    :class:`Grade` achieved at that stage and an optional feedback note.
    """

    def __init__(self) -> None:
        self._versions: List[str] = []
        self._grades: List[Grade] = []
        self._feedbacks: List[str] = []
        self._timestamps: List[float] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def commit(self, poem: str, grade: Grade, feedback: str = "") -> int:
        """Record *poem* as a new version.  Returns the version index."""
        self._versions.append(poem)
        self._grades.append(grade)
        self._feedbacks.append(feedback)
        self._timestamps.append(time.time())
        return len(self._versions) - 1

    def rollback(self, n: int) -> str:
        """Return the poem at version index *n* (0-based).

        Raises ``IndexError`` if *n* is out of range.
        """
        if not self._versions:
            raise IndexError("No versions committed yet.")
        if n < 0:
            n = len(self._versions) + n
        if not (0 <= n < len(self._versions)):
            raise IndexError(
                f"Version {n} out of range [0, {len(self._versions) - 1}]."
            )
        return self._versions[n]

    def diff(self, v1: int, v2: int) -> List[str]:
        """Return unified diff lines between versions *v1* and *v2*.

        Both indices are 0-based.  Raises ``IndexError`` for invalid indices.
        """
        if not self._versions:
            return []
        max_v = len(self._versions) - 1
        v1 = max(0, min(v1, max_v))
        v2 = max(0, min(v2, max_v))
        lines_a = self._versions[v1].splitlines(keepends=True)
        lines_b = self._versions[v2].splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            lines_a, lines_b,
            fromfile=f"version {v1}",
            tofile=f"version {v2}",
        ))
        return diff_lines

    def grade_arc(self) -> List[Tuple[int, Grade]]:
        """Return a list of (version_idx, grade) pairs, showing quality over time."""
        return list(enumerate(self._grades))

    def best_version_index(self) -> Optional[int]:
        """Return the index of the version with the highest grade."""
        if not self._grades:
            return None
        return max(range(len(self._grades)), key=lambda i: self._grades[i])

    def latest(self) -> Optional[str]:
        """Return the most recently committed version."""
        return self._versions[-1] if self._versions else None

    def __len__(self) -> int:
        return len(self._versions)

    def summary(self) -> str:
        """Return a human-readable log of all commits."""
        if not self._versions:
            return "(no commits)"
        lines = []
        for i, (poem, grade, fb, ts) in enumerate(
            zip(self._versions, self._grades, self._feedbacks, self._timestamps)
        ):
            preview = poem[:60].replace("\n", " ↵ ") + ("…" if len(poem) > 60 else "")
            lines.append(f"v{i} [{grade}] {preview!r}  — {fb!r}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MoodTracker
# ---------------------------------------------------------------------------

# Valence-Arousal-Dominance keywords for simple VAD tracking
_VAD_LEXICON: Dict[str, Tuple[float, float, float]] = {
    # (valence, arousal, dominance)  all in [-1, 1]
    "happy":       ( 0.9,  0.5,  0.5),
    "joyful":      ( 0.9,  0.7,  0.4),
    "excited":     ( 0.7,  0.9,  0.3),
    "love":        ( 0.95, 0.5,  0.4),
    "beautiful":   ( 0.8,  0.3,  0.2),
    "peaceful":    ( 0.7, -0.3,  0.1),
    "calm":        ( 0.5, -0.5,  0.2),
    "sad":         (-0.7,  0.2, -0.4),
    "melancholic": (-0.6,  0.1, -0.2),
    "dark":        (-0.6,  0.2, -0.3),
    "grief":       (-0.9,  0.4, -0.5),
    "sorrow":      (-0.8,  0.3, -0.4),
    "fear":        (-0.7,  0.7, -0.6),
    "angry":       (-0.6,  0.9,  0.4),
    "rage":        (-0.8,  1.0,  0.6),
    "bitter":      (-0.7,  0.5,  0.2),
    "lonely":      (-0.6, -0.2, -0.4),
    "hopeful":     ( 0.7,  0.4,  0.2),
    "nostalgic":   ( 0.3,  0.1,  0.0),
    "mysterious":  ( 0.1,  0.3,  0.0),
    "serene":      ( 0.7, -0.5,  0.1),
    "romantic":    ( 0.8,  0.6,  0.3),
    "pain":        (-0.8,  0.5, -0.4),
    "death":       (-0.7,  0.2, -0.5),
    "wonder":      ( 0.6,  0.5,  0.1),
    "awe":         ( 0.5,  0.6,  0.0),
    "contemplate": ( 0.2, -0.2,  0.1),
}

_MOOD_LABELS: List[Tuple[str, Tuple[float, float, float]]] = [
    ("joyful",        ( 0.8,  0.6,  0.4)),
    ("hopeful",       ( 0.7,  0.3,  0.2)),
    ("romantic",      ( 0.8,  0.5,  0.3)),
    ("serene",        ( 0.6, -0.4,  0.1)),
    ("nostalgic",     ( 0.3,  0.0,  0.0)),
    ("contemplative", ( 0.2, -0.1,  0.1)),
    ("mysterious",    ( 0.0,  0.3,  0.0)),
    ("melancholic",   (-0.5,  0.1, -0.2)),
    ("elegiac",       (-0.5, -0.1, -0.1)),
    ("somber",        (-0.6,  0.0, -0.2)),
    ("angry",         (-0.5,  0.9,  0.4)),
    ("anguished",     (-0.8,  0.6, -0.4)),
    ("neutral",       ( 0.0,  0.0,  0.0)),
]


def _vad_distance(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    """Euclidean distance between two VAD vectors."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class MoodTracker:
    """Tracks the session emotional arc using a running VAD centroid.

    The current mood is represented both as a VAD vector (for arithmetic) and
    as a discrete label (for template selection).  A :class:`Grade` is
    maintained reflecting how strongly the session's emotional signal maps to
    the dominant mood label.
    """

    def __init__(self) -> None:
        self._vad: List[float] = [0.0, 0.0, 0.0]  # [valence, arousal, dominance]
        self._n_updates: int = 0
        self._history: List[Tuple[float, float, float]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, utterance: str) -> None:
        """Parse *utterance* for VAD-laden words and shift the running average."""
        tokens = re.findall(r"[a-z]+", utterance.lower())
        updates: List[Tuple[float, float, float]] = []
        for token in tokens:
            if token in _VAD_LEXICON:
                updates.append(_VAD_LEXICON[token])
        if not updates:
            return
        # Exponential moving average with α=0.3
        alpha = 0.3
        avg_v = sum(u[0] for u in updates) / len(updates)
        avg_a = sum(u[1] for u in updates) / len(updates)
        avg_d = sum(u[2] for u in updates) / len(updates)
        if self._n_updates == 0:
            self._vad = [avg_v, avg_a, avg_d]
        else:
            self._vad[0] = (1 - alpha) * self._vad[0] + alpha * avg_v
            self._vad[1] = (1 - alpha) * self._vad[1] + alpha * avg_a
            self._vad[2] = (1 - alpha) * self._vad[2] + alpha * avg_d
        self._n_updates += 1
        self._history.append(tuple(self._vad))  # type: ignore[arg-type]

    def current_mood(self) -> str:
        """Return the mood label closest to the current VAD centroid."""
        vad = tuple(self._vad)  # type: ignore[arg-type]
        best_label = "neutral"
        best_dist = float("inf")
        for label, proto_vad in _MOOD_LABELS:
            dist = _vad_distance(vad, proto_vad)  # type: ignore[arg-type]
            if dist < best_dist:
                best_dist = dist
                best_label = label
        return best_label

    def mood_grade(self) -> Grade:
        """Return a Grade for how strongly the dominant mood is expressed.

        A high grade means the current VAD centroid is very close to one of
        the prototypical mood vectors.
        """
        if self._n_updates == 0:
            return Grade.from_prob(0.5)  # flat prior
        vad = tuple(self._vad)  # type: ignore[arg-type]
        best_dist = min(
            _vad_distance(vad, proto)  # type: ignore[arg-type]
            for _, proto in _MOOD_LABELS
        )
        # Max possible distance in [-1,1]^3 is sqrt(12) ≈ 3.46
        max_dist = math.sqrt(12)
        closeness = 1.0 - best_dist / max_dist
        return Grade.from_prob(max(0.05, closeness))

    def vad_vector(self) -> Tuple[float, float, float]:
        """Return the current (valence, arousal, dominance) triple."""
        return (self._vad[0], self._vad[1], self._vad[2])

    def mood_arc(self) -> List[str]:
        """Return the sequence of dominant mood labels over history."""
        arc = []
        for snapshot in self._history:
            best_label = "neutral"
            best_dist = float("inf")
            for label, proto in _MOOD_LABELS:
                d = _vad_distance(snapshot, proto)  # type: ignore[arg-type]
                if d < best_dist:
                    best_dist = d
                    best_label = label
            arc.append(best_label)
        return arc

    def reset(self) -> None:
        """Reset VAD state to neutral."""
        self._vad = [0.0, 0.0, 0.0]
        self._n_updates = 0
        self._history = []


# ---------------------------------------------------------------------------
# PoetryPromptGenerator
# ---------------------------------------------------------------------------

# Template banks keyed by (purpose, mood)
# Slots: {TOPIC}, {FORM}, {MOOD}, {LINE}, {PREV}

_OPENING_TEMPLATES: Dict[str, List[str]] = {
    "joyful":        [
        "Write an exuberant {FORM} celebrating {TOPIC}.",
        "Compose a jubilant verse about the joy of {TOPIC}.",
        "Begin a bright, airy poem where {TOPIC} bursts with life.",
        "Open with a sun-drenched image of {TOPIC}.",
    ],
    "melancholic":   [
        "Write a mournful {FORM} meditating on {TOPIC}.",
        "Begin with a quiet image of loss surrounding {TOPIC}.",
        "Open in a minor key: {TOPIC} seen through tears.",
        "Start with the weight of {TOPIC} pressing down.",
    ],
    "contemplative": [
        "Write a reflective {FORM} turning {TOPIC} over slowly.",
        "Begin with a question about {TOPIC} left unanswered.",
        "Open on a threshold: the speaker pausing before {TOPIC}.",
        "Start with a long silence, then speak of {TOPIC}.",
    ],
    "romantic":      [
        "Write a tender {FORM} entwining {TOPIC} with longing.",
        "Open with the beloved's presence felt through {TOPIC}.",
        "Begin in the intimacy of two voices speaking of {TOPIC}.",
        "Start with a gesture of devotion towards {TOPIC}.",
    ],
    "mysterious":    [
        "Write an enigmatic {FORM} in which {TOPIC} conceals its secret.",
        "Open with shadow and half-light: {TOPIC} barely glimpsed.",
        "Begin in a space between waking and dream where {TOPIC} waits.",
        "Start with a question no one can answer about {TOPIC}.",
    ],
    "angry":         [
        "Write a fierce {FORM} demanding justice from {TOPIC}.",
        "Open with a line that cuts like glass — subject: {TOPIC}.",
        "Begin with controlled fury, turning {TOPIC} into argument.",
        "Start with the image of something broken by {TOPIC}.",
    ],
    "neutral":       [
        "Write a {FORM} about {TOPIC}.",
        "Compose verse exploring the theme of {TOPIC}.",
        "Begin a poem centred on {TOPIC}.",
        "Open with a precise image from the world of {TOPIC}.",
    ],
}

_CONTINUATION_TEMPLATES: List[str] = [
    "Continue from '{PREV}', deepening the image of {TOPIC}.",
    "Follow '{PREV}' with a contrasting image that complicates {TOPIC}.",
    "After '{PREV}', bring in a new sensory detail about {TOPIC}.",
    "Extend the metaphor begun in '{PREV}' with one more step.",
    "After '{PREV}', shift focus to the emotional weight of {TOPIC}.",
    "Build on '{PREV}' with a turn toward the abstract.",
    "Continue '{PREV}' — slow down, zoom in on one detail.",
    "After '{PREV}', echo the opening sound in a new context.",
    "Follow '{PREV}' with a question that opens rather than closes.",
    "After '{PREV}', let silence speak: a very short next line.",
]

_REVISION_TEMPLATES: Dict[str, List[str]] = {
    "make_sadder":    [
        "Revise the poem to darken {TOPIC}: replace hopeful words with images of loss.",
        "Rewrite with more grief: let the music slow and the imagery darken.",
        "Shift toward elegy: remove brightness, add shadow and minor-key vowels.",
    ],
    "make_happier":   [
        "Revise to celebrate {TOPIC}: open the vowels, lift the syntax.",
        "Rewrite with joy — imagery of light, movement, and laughter.",
        "Brighten the diction: cut heavy consonants, let the lines breathe.",
    ],
    "more_rhyme":     [
        "Add end-rhyme to adjacent lines — target an ABAB or AABB scheme.",
        "Introduce internal rhyme and slant rhyme throughout.",
        "Revise end-words so that alternate lines chime.",
    ],
    "less_rhyme":     [
        "Relax the rhyme scheme to free verse — let the line breaks do the work.",
        "Remove forced rhymes; substitute rhythmic stress and assonance.",
        "Convert to blank verse: keep the meter but drop the end-rhyme.",
    ],
    "make_longer":    [
        "Expand with one additional stanza exploring a new dimension of {TOPIC}.",
        "Add two more lines to each stanza, slowing the pace.",
        "Insert a middle section that complicates the central image.",
    ],
    "make_shorter":   [
        "Trim to the essential: remove every line that does not earn its place.",
        "Compress to a {FORM}: ruthless cutting, no decoration.",
        "Reduce to the one image that holds everything — remove the rest.",
    ],
    "add_imagery":    [
        "Replace abstract nouns with concrete sensory images.",
        "Add a controlling metaphor that runs through every stanza.",
        "Introduce three new images: one visual, one auditory, one tactile.",
    ],
    "default":        [
        "Revise the poem to address the feedback: {LINE}.",
        "Apply the requested change — focus on {TOPIC}.",
        "Rewrite with the following adjustment: {LINE}.",
    ],
}


class PoetryPromptGenerator:
    """Generates natural-language prompts to guide poem generation and revision.

    Prompts are selected from template banks using Grade-weighted sampling:
    templates are assigned default grades and the best-weighted one for the
    current mood/intent is selected.
    """

    def __init__(self) -> None:
        self._mood_tracker: Optional[MoodTracker] = None

    def set_mood_tracker(self, tracker: MoodTracker) -> None:
        self._mood_tracker = tracker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_opening_prompt(self, topic: str, form: str = "poem", mood: str = "neutral") -> str:
        """Return a prompt for starting a new poem about *topic*.

        Selects from mood-appropriate templates using Grade-weighted sampling.
        """
        bank = _OPENING_TEMPLATES.get(mood, _OPENING_TEMPLATES["neutral"])
        template = self._grade_weighted_choice(bank, mood)
        return self._fill(template, topic=topic, form=form, mood=mood)

    def generate_continuation_prompt(
        self,
        prev_lines: List[str],
        topic: str = "the theme",
        mood: str = "neutral",
    ) -> str:
        """Return a prompt for continuing from the last line(s) of the poem."""
        prev = prev_lines[-1].strip() if prev_lines else "..."
        # Pick longest non-trivial previous line as anchor
        for line in reversed(prev_lines):
            stripped = line.strip()
            if len(stripped) > 10:
                prev = stripped
                break
        template = self._grade_weighted_choice(_CONTINUATION_TEMPLATES, mood)
        return self._fill(template, prev=prev, topic=topic, mood=mood)

    def generate_revision_prompt(
        self,
        poem: str,
        feedback: str,
        topic: str = "the theme",
        form: str = "poem",
    ) -> str:
        """Return a prompt for revising *poem* given *feedback*."""
        # Map feedback to intent key
        feedback_lower = feedback.lower()
        intent_key = "default"
        for key in _REVISION_TEMPLATES:
            if key.replace("_", " ") in feedback_lower or key in feedback_lower:
                intent_key = key
                break
        bank = _REVISION_TEMPLATES.get(intent_key, _REVISION_TEMPLATES["default"])
        mood = self._mood_tracker.current_mood() if self._mood_tracker else "neutral"
        template = self._grade_weighted_choice(bank, mood)
        return self._fill(template, topic=topic, form=form, line=feedback)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _grade_weighted_choice(self, templates: List[str], mood: str) -> str:
        """Select a template using Grade-weighted random sampling.

        Each template is assigned a base Grade and the selection is a weighted
        sample proportional to ``grade.to_prob()``.
        """
        if not templates:
            return "Write a poem."
        # Assign grades: first template gets slight boost, rest are equal
        grades = [Grade.from_prob(0.8 if i == 0 else 0.6) for i in range(len(templates))]
        probs = [g.to_prob() for g in grades]
        total = sum(probs) or 1.0
        weights = [p / total for p in probs]
        # Weighted sample
        r = random.random()
        cumulative = 0.0
        for template, w in zip(templates, weights):
            cumulative += w
            if r <= cumulative:
                return template
        return templates[-1]

    @staticmethod
    def _fill(
        template: str,
        topic: str = "",
        form: str = "",
        mood: str = "",
        prev: str = "",
        line: str = "",
    ) -> str:
        """Replace slots in *template* with provided values."""
        return (
            template
            .replace("{TOPIC}", topic or "life")
            .replace("{FORM}", form or "poem")
            .replace("{MOOD}", mood or "neutral")
            .replace("{PREV}", prev or "...")
            .replace("{LINE}", line or "the requested change")
        )


# ---------------------------------------------------------------------------
# PoetMode — main interactive class
# ---------------------------------------------------------------------------

# Mapping from intent to apply_feedback action
_FEEDBACK_ACTIONS: Dict[str, str] = {
    "make_sadder":  "darken",
    "make_happier": "brighten",
    "more_rhyme":   "add_rhyme",
    "less_rhyme":   "remove_rhyme",
    "make_longer":  "expand",
    "make_shorter": "compress",
    "add_imagery":  "enrich",
    "change_form":  "reform",
}

# Seed vocabulary for simple template-based poem generation
_NOUNS_BY_TOPIC: Dict[str, List[str]] = {
    "nature":   ["tree", "river", "mountain", "wind", "leaf", "stone", "sky", "root",
                 "blossom", "shore", "cloud", "rain", "moss", "frost", "branch"],
    "autumn":   ["leaf", "frost", "harvest", "ember", "mist", "bark", "acorn", "dusk",
                 "smoke", "shadow", "vine", "mud", "fog", "decay", "chill"],
    "spring":   ["blossom", "shoot", "bud", "rain", "thaw", "dawn", "seed", "petal",
                 "stream", "mud", "nest", "green", "light", "wing", "soil"],
    "summer":   ["heat", "light", "dust", "shore", "wave", "sky", "grass", "bloom",
                 "noon", "fire", "cricket", "wheat", "sweat", "breath", "gold"],
    "winter":   ["ice", "snow", "silence", "frost", "dark", "bone", "wind", "ash",
                 "branch", "cold", "night", "hunger", "stone", "grey", "bare"],
    "love":     ["heart", "hand", "voice", "gaze", "touch", "name", "warmth", "flame",
                 "sigh", "promise", "embrace", "whisper", "longing", "dream"],
    "death":    ["shadow", "silence", "dust", "grave", "ash", "night", "threshold",
                 "void", "absence", "echo", "remnant", "veil", "crossing"],
    "time":     ["hour", "clock", "season", "tide", "moment", "current", "age",
                 "memory", "tomorrow", "yesterday", "second", "epoch", "drift"],
    "default":  ["light", "shadow", "voice", "path", "door", "window", "stone",
                 "water", "fire", "sky", "hand", "word", "dream", "name"],
}

_VERBS_BY_MOOD: Dict[str, List[str]] = {
    "joyful":      ["dance", "sing", "shine", "bloom", "soar", "laugh", "celebrate",
                    "burst", "gleam", "rise"],
    "melancholic": ["fall", "fade", "drift", "grieve", "ache", "sink", "wander",
                    "mourn", "linger", "dissolve"],
    "neutral":     ["move", "speak", "stand", "turn", "hold", "find", "see",
                    "know", "feel", "reach"],
    "contemplative": ["wonder", "ponder", "pause", "trace", "recall", "consider",
                      "observe", "dwell", "return", "seek"],
    "romantic":    ["long", "cherish", "adore", "embrace", "whisper", "yearn",
                    "offer", "tremble", "hold", "breathe"],
}

_ADJS_BY_MOOD: Dict[str, List[str]] = {
    "joyful":      ["bright", "golden", "warm", "tender", "open", "free", "light",
                    "joyful", "clear", "fresh"],
    "melancholic": ["pale", "grey", "hollow", "quiet", "empty", "lost", "dark",
                    "faded", "cold", "bare"],
    "neutral":     ["still", "deep", "ancient", "slow", "small", "wide", "gentle",
                    "silent", "clear", "long"],
    "contemplative": ["vast", "patient", "distant", "heavy", "intricate", "layered",
                      "measured", "thoughtful", "endless"],
    "romantic":    ["soft", "warm", "tender", "breathless", "intimate", "devoted",
                    "aching", "beloved", "sweet"],
}

# Suffixes that mark WordNet lemmas as overly abstract/technical for poetry.
_ABSTRACT_SUFFIXES: Tuple[str, ...] = (
    "ness", "ity", "ance", "ence", "ism", "ation", "ment", "ology",
    "ness", "hood", "ship", "ling",
)


def _is_poetic_noun(word: str) -> bool:
    """Return True if *word* is usable as a concrete poetic image.

    Rejects multi-word phrases, very long words, and abstract nominalizations.
    """
    if " " in word or not word.isalpha():
        return False
    if len(word) > 11:
        return False
    lw = word.lower()
    return not any(lw.endswith(suf) for suf in _ABSTRACT_SUFFIXES)


# Irregular past tenses for common poetic verbs.
_IRREGULAR_PAST: Dict[str, str] = {
    "sink": "sank", "fall": "fell", "rise": "rose", "find": "found",
    "hold": "held", "feel": "felt", "know": "knew", "see": "saw",
    "speak": "spoke", "stand": "stood", "seek": "sought", "come": "came",
    "go": "went", "bring": "brought", "take": "took", "leave": "left",
    "weep": "wept", "keep": "kept", "shine": "shone", "grow": "grew",
}


def _past_tense(verb: str) -> str:
    """Return the simple past of *verb* without regex."""
    if verb in _IRREGULAR_PAST:
        return _IRREGULAR_PAST[verb]
    if verb.endswith("e"):
        return verb + "d"
    if verb.endswith("y") and len(verb) > 1 and verb[-2] not in "aeiou":
        return verb[:-1] + "ied"
    return verb + "ed"


def _pick(d: Dict[str, List[str]], key: str) -> str:
    """Pick a random word from the dict's list for *key*, falling back to 'default'."""
    lst = d.get(key, d.get("default", d.get("neutral", ["thing"])))
    return random.choice(lst)


def _count_syllables_simple(word: str) -> int:
    """Syllable count: CMU dict first, vowel-run heuristic as fallback."""
    cmu = _get_cmu_dict()
    if cmu:
        prons = cmu.get(word.lower(), [])
        if prons:
            return sum(1 for ph in prons[0] if ph[-1].isdigit())
    # Vowel-run heuristic (no regex: manual scan)
    vowels = set("aeiou")
    count, in_vowel = 0, False
    for ch in word.lower():
        if ch in vowels:
            if not in_vowel:
                count += 1
            in_vowel = True
        else:
            in_vowel = False
    return max(1, count)


def _topic_vocabulary(topic: str) -> Tuple[List[str], List[str]]:
    """Return (nouns, adjectives) relevant to *topic* via WordNet + base tables.

    Walks the first two synsets for each content word in *topic*, collecting
    lemma names from the synset and its direct hyponyms for nouns, and similar
    lookups for adjectives.  Falls back to ``_NOUNS_BY_TOPIC["default"]`` when
    WordNet is unavailable or yields nothing.
    """
    # Check base tables first (exact-key match on any word in topic)
    topic_words = [w.strip(",'!?.") for w in topic.lower().split() if len(w) > 2]
    base_nouns: List[str] = []
    for tw in topic_words:
        for key, words in _NOUNS_BY_TOPIC.items():
            if key == tw or tw in key:
                base_nouns.extend(words)
                break
    if not base_nouns:
        base_nouns = list(_NOUNS_BY_TOPIC["default"])

    if not _HAS_NLTK:
        return base_nouns, _ADJS_BY_MOOD["neutral"]

    wn_nouns: List[str] = []
    wn_adjs: List[str] = []
    _skip = {"the", "a", "an", "and", "or", "but", "for", "of", "in", "on",
              "at", "to", "is", "are", "was", "be", "that", "this", "it",
              "which", "with", "about", "really", "truly", "very", "me", "my"}
    for word in topic_words:
        if word in _skip:
            continue
        for syn in wn.synsets(word, pos=wn.NOUN)[:2]:
            for lemma in syn.lemmas()[:4]:
                name = lemma.name().replace("_", " ")
                if name.isalpha() and 2 < len(name) < 13:
                    wn_nouns.append(name)
            for hypo in list(syn.hyponyms())[:3]:
                for lemma in hypo.lemmas()[:2]:
                    name = lemma.name().replace("_", " ")
                    if name.isalpha() and 2 < len(name) < 13:
                        wn_nouns.append(name)
        for syn in wn.synsets(word, pos=wn.ADJ)[:2]:
            for lemma in syn.lemmas()[:4]:
                name = lemma.name().replace("_", " ")
                if name.isalpha() and 2 < len(name) < 10:
                    wn_adjs.append(name)

    filtered_nouns = [n for n in wn_nouns if _is_poetic_noun(n)]
    combined_nouns = list(dict.fromkeys(filtered_nouns + base_nouns)) or base_nouns
    combined_adjs = list(dict.fromkeys(wn_adjs)) or _ADJS_BY_MOOD["neutral"]
    return combined_nouns, combined_adjs


def _pick_n_distinct(pool: List[str], n: int) -> List[str]:
    """Pick *n* distinct items from *pool*, recycling if pool is smaller."""
    if not pool:
        return ["thing"] * n
    if len(pool) >= n:
        return random.sample(pool, n)
    # pool smaller than n: pick all then top up
    result = list(pool)
    while len(result) < n:
        result.append(random.choice(pool))
    return result


def _generate_line(topic: str, mood: str, rhyme_word: Optional[str] = None,
                   used_skeletons: Optional[set] = None) -> str:
    """Generate a single poetic line with WordNet-expanded topic vocabulary.

    Draws distinct nouns, adjectives, and verbs so no word repeats within
    the line.  Rhyme word is honoured as the final token when supplied.
    """
    nouns, topic_adjs = _topic_vocabulary(topic)
    all_adjs = list(dict.fromkeys(topic_adjs + _ADJS_BY_MOOD.get(mood, _ADJS_BY_MOOD["neutral"])))
    verbs = _VERBS_BY_MOOD.get(mood, _VERBS_BY_MOOD["neutral"])

    n1, n2 = _pick_n_distinct(nouns, 2)
    a1, a2 = _pick_n_distinct(all_adjs, 2)
    v1 = random.choice(verbs)

    v1_past = _past_tense(v1)
    patterns = [
        f"The {a1} {n1} {v1}s",
        f"{n1.capitalize()}s {v1} through the {a1} {n2}",
        f"I {v1} the {a1} {n1}",
        f"Where {n1} meets {n2}",
        f"The {n1} of {n2}s, {a1}",
        f"How {a1} the {n1} when {n2}s {v1}",
        f"Even the {n1} {v1}s now",
        f"This {a1} {n1} knows no {n2}",
        f"Each {n1} {v1}ing, {a1} and {a2}",
        f"{a1.capitalize()} {n1}s remember what was {a2}",
        f"The {n2} that {v1}s like {a1} {n1}",
        f"In {a1} {n1} the {n2} {v1}s",
        f"No {n1} without its {a1} {n2}",
        f"What {n1} is this? The {n2} {v1}s",
        f"All {n1}s are {a1} at the end",
        f"Between the {n1} and the {n2}, {a1}",
        f"I have {v1_past} the {a1} {n1}",
        f"The {n1}, {a1}, {v1}ing still",
        f"Once I {v1_past} a {a1} {n1}",
        f"The {a2} {n2} {v1}s where {n1}s were {a1}",
        f"Nothing {v1}s like a {a1} {n1}",
        f"{n1.capitalize()} after {n2}: both {a1}",
        f"What the {a1} {n1} {v1_past}",
        f"Only the {a1} {n1} remains",
    ]

    if rhyme_word:
        return random.choice([
            f"The {a1} {n1} {v1}s toward the {rhyme_word}",
            f"I {v1} and find the {rhyme_word}",
            f"What {n1} remains but {rhyme_word}",
            f"The {n2} becomes the {rhyme_word}",
            f"All {n1}s {v1} toward {rhyme_word}",
        ])

    # Filter out structurally used patterns (first 3 tokens = same skeleton)
    if used_skeletons is not None:
        novel = [p for p in patterns
                 if " ".join(p.lower().split()[:3]) not in used_skeletons]
        patterns = novel if novel else patterns
        chosen = random.choice(patterns)
        used_skeletons.add(" ".join(chosen.lower().split()[:3]))
        return chosen

    return random.choice(patterns)


def _generate_poem_free_verse(topic: str, mood: str, n_lines: int = 12) -> str:
    # DELETED: replaced by PoemGenerator in generate_poem(); kept as legacy fallback.
    from gofai_chat.generation.poetry.line_spec import generate_poem_lines
    specs = generate_poem_lines(topic, mood, n_lines)
    return "\n".join(str(s) for s in specs)


def _generate_haiku(topic: str, mood: str) -> str:
    # DELETED: replaced by PoemGenerator in generate_poem(); kept as legacy fallback.
    adj = _pick(_ADJS_BY_MOOD, mood)
    noun = _pick(_NOUNS_BY_TOPIC, topic) if topic in _NOUNS_BY_TOPIC else _pick(_NOUNS_BY_TOPIC, "default")
    verb = _pick(_VERBS_BY_MOOD, mood)
    # 5-7-5 templates
    line1 = f"{adj.capitalize()} {noun}s wait"
    line2 = f"The {verb}ing {noun} calls to me"
    line3 = f"Still {adj} {noun}s remain"
    return f"{line1}\n{line2}\n{line3}"


def _generate_couplet_poem(topic: str, mood: str, n_couplets: int = 4) -> str:
    # DELETED: replaced by PoemGenerator in generate_poem(); kept as legacy fallback.
    cmu = _get_cmu_dict()
    lines: List[str] = []
    for _ in range(n_couplets):
        l1 = _generate_line(topic, mood)
        l2 = _generate_line(topic, mood)
        lines.extend([l1, l2])
    return "\n".join(lines)


def _generate_sonnet_like(topic: str, mood: str) -> str:
    # DELETED: replaced by PoemGenerator in generate_poem(); kept as legacy fallback.
    from gofai_chat.generation.poetry.line_spec import generate_poem_lines
    all_specs = generate_poem_lines(topic, mood, 14)
    stanzas = [
        "\n".join(str(s) for s in all_specs[0:4]),
        "\n".join(str(s) for s in all_specs[4:8]),
        "\n".join(str(s) for s in all_specs[8:12]),
        "\n".join(str(s) for s in all_specs[12:14]),
    ]
    return "\n\n".join(stanzas)


# ---------------------------------------------------------------------------
# Token-based intent parser — no regex
# ---------------------------------------------------------------------------

# Words that signal the user wants a *new* poem generated.
_GENERATE_SIGNALS: frozenset = frozenset({
    "write", "compose", "create", "generate", "give", "make", "produce",
    "poem", "verse", "poetry", "write me", "write a", "give me",
})

# Words that signal the user wants the *existing* poem revised.
_REVISE_SIGNALS: frozenset = frozenset({
    "revise", "rewrite", "change", "alter", "update", "improve", "fix",
    "make", "again", "redo", "retry", "try", "rework", "adjust",
    "darker", "lighter", "sadder", "happier", "simpler", "shorter",
    "longer", "funnier", "more", "less", "better", "different",
    "no", "nope", "not", "wrong",
})

# Adjective/adverb pairs that alone constitute revision feedback.
_REVISION_ADJECTIVES: frozenset = frozenset({
    "darker", "lighter", "sadder", "happier", "funnier", "angrier",
    "simpler", "shorter", "longer", "quieter", "louder", "warmer",
    "colder", "brighter", "more", "less",
})

# Ordered mapping: token → canonical form name (checked left-to-right).
_FORM_TOKENS: Dict[str, str] = {
    "sonnet":    "sonnet",
    "haiku":     "haiku",
    "villanelle": "villanelle",
    "ode":       "ode",
    "couplet":   "couplets",
    "couplets":  "couplets",
    "ballad":    "ballad",
    "sestina":   "sestina",
    "free":      "free verse",
    "verse":     "free verse",
}

# Tokens that are preamble filler — stripped when extracting the topic.
_PREAMBLE: frozenset = frozenset({
    "write", "compose", "create", "generate", "give", "produce",
    "me", "us", "please", "a", "an", "the", "i", "want", "can", "you",
    "could", "would", "let", "us", "poem", "about", "on", "regarding",
    "concerning", "covering", "focused", "themed",
})


# Pronominal / anaphoric forms that stand in for a previously mentioned entity.
# When the entire extracted topic consists of these, it must be resolved from
# discourse context rather than interpreted literally.
_ANAPHORS: frozenset = frozenset({
    "one", "it", "that", "this", "them", "they", "those", "these",
    "he", "she", "its", "their", "such",
})

# Words to strip when extracting the main entity from a question utterance.
_QUESTION_STRIP: frozenset = frozenset({
    "what", "where", "when", "who", "whom", "whose", "why", "how", "which",
    "is", "are", "was", "were", "do", "does", "did", "a", "an", "the",
    "color", "colour", "size", "shape", "kind", "type", "sort", "name",
    "of", "for", "in", "on", "at", "to", "and", "or", "?", ".",
})


def _detect_domain_switch(tokens: List[str]) -> str:
    """Return the domain name if tokens express a domain-switch intent, else empty string.

    Recognises patterns:
      "switch to X", "use X mode", "X domain"
    where X is a single token (the domain name).
    """
    if not tokens:
        return ""
    joined = " ".join(tokens)
    # "switch to X"
    if len(tokens) >= 3 and tokens[0] == "switch" and tokens[1] == "to":
        return tokens[2]
    # "use X mode"
    if len(tokens) >= 3 and tokens[0] == "use" and tokens[-1] == "mode":
        return tokens[1]
    # "X domain" — two-token phrase
    if len(tokens) == 2 and tokens[1] == "domain":
        return tokens[0]
    return ""


def _extract_discourse_entity(tokens: List[str]) -> str:
    """Extract the main content entity from a question or statement.

    Strips question words, auxiliaries, articles, and common meta-words so
    that "What color is a bee?" → "bee" and "Tell me about roses" → "roses".
    Returns the last surviving content word (rightmost head noun heuristic).
    """
    content = [t for t in tokens if t not in _QUESTION_STRIP and len(t) > 2]
    return content[-1] if content else ""


def _parse_intent(
    tokens: List[str],
    current_poem: "SessionPoem",
) -> Tuple[str, str, str]:
    """Classify *tokens* as ('generate'|'revise'|'unknown', topic, form).

    Entirely token-set based — no regex, no heuristic string slicing.

    Strategy
    --------
    1. If any token is a *form* keyword and a *generate* signal is present,
       the intent is ``'generate'``.  The topic is the remaining content
       words after stripping preamble and form tokens.
    2. If the first token is a *generate* signal (write/compose/…) with no
       current poem, it's always ``'generate'``.
    3. If tokens contain a *revise* signal and a poem already exists, it's
       ``'revise'``.
    4. If the utterance is a bare revision adjective ("darker", "sadder") or
       starts with "make it"/"make the", it's ``'revise'``.
    5. Otherwise ``'unknown'``.
    """
    token_set = frozenset(tokens)

    # Detect form
    detected_form = "free verse"
    for tok, fname in _FORM_TOKENS.items():
        if tok in token_set:
            detected_form = fname
            break

    # WH-question detection — before generate/revise checks
    _WH_WORDS = frozenset({"what","where","when","who","whom","whose","why","how","which"})
    _FACTUAL_TOPICS = frozenset({"color","colour","size","weight","height","distance",
                                   "population","capital","location","age","temperature"})
    if tokens[0] in _WH_WORDS or tokens[-1].rstrip("'\".,!") == "?":
        if not (token_set & _GENERATE_SIGNALS and token_set & frozenset({"poem","sonnet","haiku","verse"})):
            return ("question", " ".join(tokens), "free verse")

    # Detect bare revision adjective: "Make it darker." / "Darker."
    if token_set & _REVISION_ADJECTIVES and not (token_set & _GENERATE_SIGNALS):
        return ("revise", "", detected_form)

    # "make it X" / "make the X" — always revision when poem exists
    if tokens[0] == "make" and len(tokens) >= 2 and tokens[1] in ("it", "the"):
        return ("revise", "", detected_form)

    # Explicit negative / "no" / "do better" / "try again"
    _negative_openers = {"no", "nope", "not", "wrong", "bad"}
    if tokens[0] in _negative_openers and not current_poem.is_empty():
        return ("revise", "", detected_form)
    if token_set >= {"do", "better"} or token_set >= {"try", "again"}:
        return ("revise", "", detected_form)

    # "revise"/"change"/"rewrite" as first token
    _explicit_revise = {"revise", "change", "rewrite", "rework", "alter", "update"}
    if tokens[0] in _explicit_revise:
        return ("revise", "", detected_form)

    # Generation: first token in generate signals, OR form keyword present with enough content
    has_generate_signal = bool(token_set & _GENERATE_SIGNALS)
    if has_generate_signal or detected_form != "free verse":
        # Extract topic: content words that aren't preamble/form tokens/stopwords
        _form_token_set = frozenset(_FORM_TOKENS.keys())
        topic_words = [
            t for t in tokens
            if t not in _PREAMBLE and t not in _form_token_set
            and t not in {"that", "which", "is", "are", "was", "be",
                          "really", "truly", "very", "secretly", "but", "and", "or"}
        ]
        topic = " ".join(topic_words) if topic_words else (
            current_poem.topic if not current_poem.is_empty() else "poetry"
        )
        return ("generate", topic, detected_form)

    return ("unknown", "", "free verse")


class PoetMode:
    """Interactive poet mode — the main REPL interface for the poetry chatbot.

    Integrates :class:`PoetryDialogueManager`, :class:`MoodTracker`,
    :class:`PoemVersionControl`, and :class:`PoetryPromptGenerator` into a
    single coherent session.  Every quality judgment is expressed as a
    :class:`Grade`.

    Usage
    -----
    >>> mode = PoetMode()
    >>> mode.run_interactive()   # starts the REPL
    """

    BANNER = (
        "╔══════════════════════════════════════╗\n"
        "║   GOFAI Poetry Studio — Poet Mode    ║\n"
        "║  (type 'help' for commands, 'quit')  ║\n"
        "╚══════════════════════════════════════╝"
    )

    HELP_TEXT = (
        "Commands:\n"
        "  write <topic>         — generate a poem about <topic>\n"
        "  sonnet <topic>        — generate a sonnet\n"
        "  haiku <topic>         — generate a haiku\n"
        "  revise <feedback>     — apply feedback to the current poem\n"
        "  show                  — display the current poem\n"
        "  history               — show version history\n"
        "  rollback <n>          — restore version n\n"
        "  diff <v1> <v2>        — show diff between versions\n"
        "  explain <line_n>      — explain the choice of line n\n"
        "  mood                  — show current session mood\n"
        "  grade                 — show harmony grade\n"
        "  quit / exit           — end the session\n"
        "  help                  — show this message\n"
    )

    def __init__(
        self,
        topic: str = "poetry",
        mood: str = "neutral",
        form: str = "free verse",
    ) -> None:
        self.session_state: DialogueState = build_initial_state(topic=topic, mood=mood)
        self._dialogue_mgr = PoetryDialogueManager()
        self._memory = ConversationTurnMemory()
        self._topic_model = TopicModel()
        self._mood_tracker = MoodTracker()
        self._vc = PoemVersionControl()
        self._prompt_gen = PoetryPromptGenerator()
        self._prompt_gen.set_mood_tracker(self._mood_tracker)
        self.current_poem = SessionPoem(topic=topic, mood=mood, form=form)
        # Discourse focus: the most recently salient entity across all turns.
        # Populated after questions ("What color is a bee?" → "bee") and after
        # poem generation.  Used to resolve anaphors like "one"/"it"/"that".
        self._discourse_focus: str = topic if topic != "poetry" else ""
        try:
            from gofai_chat.discourse.manager import DiscourseManager as _DiscourseManager
            from gofai_chat.core.judgment import Context as _DCtx
            self._discourse_mgr = _DiscourseManager()
            self._discourse_ctx = _DCtx()
        except Exception:
            self._discourse_mgr = None
            self._discourse_ctx = None
        try:
            from gofai_chat.strata.info.qud_engine import QUDStack
            self._qud_stack = QUDStack()
        except Exception:
            self._qud_stack = None
        try:
            from gofai_chat.knowledge.wiki_reasoner import WikiKnowledgeReasoner
            self._wiki_reasoner = WikiKnowledgeReasoner()
        except Exception:
            self._wiki_reasoner = None

    # ------------------------------------------------------------------
    # REPL
    # ------------------------------------------------------------------

    @property
    def domain_registry(self):
        """Return the global DomainRegistry singleton."""
        from gofai_chat.chat.domain import DomainRegistry
        return DomainRegistry.default()

    def run_interactive(self) -> None:
        """Start the interactive REPL loop.

        Reads from stdin and writes to stdout.  Handles all commands listed in
        :attr:`HELP_TEXT`.  Exits cleanly on 'quit', 'exit', or EOF.
        """
        print(self.BANNER)
        print()
        while True:
            try:
                raw = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nFarewell — may your verses ring true.")
                break
            if not raw:
                continue
            if raw.lower() in ("quit", "exit", "bye", "q"):
                print("Farewell — may your verses ring true.")
                break
            if raw.lower() == "help":
                print(self.HELP_TEXT)
                continue
            response = self._handle_input(raw)
            print(f"poet> {response}")
            print()

    def _handle_input(self, raw: str) -> str:
        """Dispatch a user command and return the response string.

        Uses token-set matching rather than regex for robustness: every
        decision is a membership test against a frozenset of known tokens or
        a lookup in an ordered dict of form names.
        """
        tokens = raw.lower().split()
        if not tokens:
            return ""
        first = tokens[0]

        # ── Presupposition check for contextual awareness ─────────────────
        self._check_presuppositions(raw)

        # ── Domain switching: "switch to X", "use X mode", "X domain" ─────
        _domain_match = _detect_domain_switch(tokens)
        if _domain_match:
            try:
                from gofai_chat.chat.domain import DomainRegistry
                registry = DomainRegistry.default()
                if registry.set_active(_domain_match):
                    return f"Switched to {_domain_match} domain."
                available = ", ".join(registry.list_domains())
                return f"Unknown domain '{_domain_match}'. Available: {available}"
            except Exception as e:
                return f"[Domain switch error: {e}]"

        # ── Auto-detect non-poetry domain and route ───────────────────────────
        try:
            from gofai_chat.chat.domain import DomainRegistry
            _registry = DomainRegistry.default()
            _domain, _request = _registry.route(raw)
            if _domain.name != "poetry":
                _output = _domain.generate(_request)
                return _output.display()
        except Exception:
            pass

        # ── Exact single-token commands ────────────────────────────────────
        if tokens == ["show"]:
            if self.current_poem.is_empty():
                return "(no poem yet — try writing one first)"
            return self._dialogue_mgr.format_response(
                self.current_poem.current_text,
                self.current_poem.current_grade,
                f"Version {self.current_poem.version_count}",
            )
        if tokens == ["history"]:
            return self._vc.summary() or "(no history)"
        if tokens == ["mood"]:
            mood = self._mood_tracker.current_mood()
            g = self._mood_tracker.mood_grade()
            return f"Current mood: {mood}  (grade: {g})"
        if tokens == ["grade"]:
            return self._format_grade_display()

        # ── rollback / diff / explain — first token + integer arg ──────────
        if first == "rollback" and len(tokens) >= 2:
            try:
                n = int(tokens[1])
                text = self._vc.rollback(n)
                self.current_poem.current_text = text
                return f"Rolled back to version {n}."
            except (ValueError, IndexError) as exc:
                return f"Error: {exc}"

        if first == "diff" and len(tokens) >= 3:
            try:
                v1, v2 = int(tokens[1]), int(tokens[2])
                diff_lines = self._vc.diff(v1, v2)
                return "".join(diff_lines) or "(no differences)"
            except (ValueError, IndexError) as exc:
                return f"Error: {exc}"

        if first == "explain" and len(tokens) >= 2:
            try:
                n = int(tokens[1])
                line = self.current_poem.get_line(n)
                if line is None:
                    return f"Line {n} does not exist."
                return self.explain_choice(line)
            except (ValueError, IndexError):
                return "Usage: explain <line_number>"

        # ── PRIMARY PATH: NL → HLF → output ──────────────────────────────────
        # Task registry dispatch — runs before HLFRealizer for rich task routing
        try:
            from gofai_chat.chat.task_registry import PoeticTaskRecognizer, TaskKind
            from gofai_chat.chat.task_handlers import handle_task
            _task = PoeticTaskRecognizer().recognize(raw)
            if _task.confidence >= 0.75 and _task.kind != TaskKind.GENERATE:
                _task_result = handle_task(_task, self)
                if _task_result:
                    return _task_result
        except Exception:
            pass  # always fall through to existing dispatch

        try:
            from gofai_chat.generation.nl_to_hlf import NLToHLF
            from gofai_chat.generation.hlf_realizer import HLFRealizer, OutputMode

            parsed = NLToHLF().parse(raw)

            if parsed.topic:
                self._discourse_focus = parsed.topic

            try:
                from gofai_chat.strata.info.qud_engine import QUDStack, QUDEntry, QUDType
                qt = next(
                    (v for v in QUDType if 'WH' in str(v) or 'OPEN' in str(v)),
                    list(QUDType)[0],
                )
                if self._qud_stack is not None:
                    self._qud_stack.push(QUDEntry(question_text=raw, qud_type=qt))
            except Exception:
                pass

            realizer = HLFRealizer()
            form_arg = (
                getattr(self, '_current_form', None)
                or (self.current_poem.form if not self.current_poem.is_empty() else None)
                or 'free_verse'
            )
            result = realizer.realize(parsed, form=form_arg)

            if parsed.output_mode != OutputMode.POEM:
                if getattr(self, '_wiki_reasoner', None) and parsed.topic:
                    try:
                        wiki_answer = self._wiki_reasoner.answer_question(raw, parsed.topic)
                        if wiki_answer:
                            result = wiki_answer + '\n\n' + result
                    except Exception:
                        pass

            # Prepend entity summary when Wikipedia was successfully consulted
            enr = getattr(parsed, 'enrichment', None)
            if enr and enr.found and enr.summary:
                wiki_note = f"[Wikipedia: {enr.summary}]\n\n"
                result = wiki_note + result

            if parsed.output_mode == OutputMode.POEM:
                self._current_poem = result.split('\n\n[')[0]

            return result

        except Exception:
            pass  # fall through to existing dispatch

        # ── Prose → LF → Poem pipeline ────────────────────────────────────
        # Intercept "turn this into a poem: …" and similar requests before
        # intent classification so the prose is not mis-parsed as a topic.
        _PROSE_SINGLE_TOKENS = frozenset({"poeticize", "saying:"})
        _PROSE_BIGRAMS = {
            ("turn", "this"), ("express", "as"), ("write", "a"),
            ("make", "a"), ("turn", "into"), ("express", "this"),
        }
        raw_lower = raw.lower()
        _is_prose_request = (
            any(t in raw_lower for t in (
                "turn this into", "write a poem that says",
                "express as poetry", "express this as",
                "saying:", "poeticize", "make a poem from",
                "turn into verse",
            ))
        )
        if _is_prose_request:
            form_arg = self.current_poem.form if not self.current_poem.is_empty() else "free verse"
            return self._handle_prose_to_poem(raw, form=form_arg)

        # ── Wikipedia trigger detection ────────────────────────────────────
        # Fire only when explicit search/lookup tokens are present so normal
        # poem requests and questions are never intercepted.
        token_set = frozenset(tokens)
        if token_set & _WIKI_TRIGGERS:
            wiki_query = self._extract_wiki_query(raw)
            if wiki_query:
                return self._handle_wikipedia(raw, wiki_query)

        # ── Intent classification via token sets ───────────────────────────
        intent, topic, form = _parse_intent(tokens, self.current_poem)

        # ── Discourse context update ───────────────────────────────────────
        # DiscourseManager.update requires a FrameInstance; passing None will
        # raise AttributeError which we catch silently — the sentence is still
        # registered via the sentence_idx advance on next successful call.
        if self._discourse_mgr is not None:
            try:
                self._discourse_ctx = self._discourse_mgr.update(None, None, raw)
            except Exception:
                pass

        # ── Anaphor resolution ─────────────────────────────────────────────
        # If the extracted topic is entirely pronominal ("one", "it", "that"…)
        # resolve it to the most salient discourse entity from earlier turns.
        if intent == "generate" and topic:
            try:
                from gofai_chat.nlp.tokenizer import Tokenizer as _Tokenizer
                _toks = _Tokenizer().tokenize(topic.lower())
                topic_tokens = frozenset(t.text.strip(string.punctuation) for t in _toks)
            except Exception:
                topic_tokens = frozenset(t.strip(string.punctuation) for t in topic.lower().split())
            if topic_tokens and topic_tokens <= _ANAPHORS:
                # Try AnaphorResolver first for discourse-grounded resolution.
                resolved = None
                try:
                    from gofai_chat.discourse.anaphora import AnaphorResolver as _AR
                    if self._discourse_mgr is not None and self._discourse_ctx is not None:
                        _ar = _AR()
                        for tok in topic_tokens:
                            _res = _ar.resolve(tok, self._discourse_ctx, self._discourse_mgr)
                            if _res and _res.antecedent:
                                _ref_text = _res.antecedent.description
                                if _ref_text and _ref_text.lower() not in ('it', 'one', 'this', 'that'):
                                    resolved = _ref_text
                                    break
                except Exception:
                    pass
                if not resolved:
                    resolved = (
                        self._discourse_focus
                        or (self.current_poem.topic if not self.current_poem.is_empty() else "")
                    )
                if resolved:
                    topic = resolved

        if intent == "generate":
            mood = self._mood_tracker.current_mood()
            result = self.generate_poem(topic, form, mood)
            self._discourse_focus = topic   # update focus to poem topic
            return result

        if intent == "revise":
            if self.current_poem.is_empty():
                return "No poem yet — write one first."
            return self.apply_feedback(self.current_poem.current_text, raw)

        if intent == "question":
            # Update discourse focus to the entity the question is about,
            # so "Write a poem about one" after "What color is a bee?" resolves correctly.
            entity = _extract_discourse_entity(tokens)
            if entity:
                self._discourse_focus = entity

            # DefaultReasoner: answer factual questions from default-logic rules.
            try:
                from gofai_chat.inference.defaults import DefaultReasoner as _DR
                _dr = _DR()
                subject = self._discourse_focus or topic
                if subject:
                    _dr.add_fact(subject)
                    facts = _dr.conclude(subject)
                    if facts and len(facts) >= 3:
                        return f"{subject.title()}: " + ", ".join(str(f) for f in facts[:5])
            except Exception:
                pass

            response = self._dialogue_mgr._handle_question(raw, self.session_state)
            self._memory.add_turn(raw, role="user")
            self._memory.add_turn(response, role="bot")

            # Extract topic from question for subsequent anaphor resolution.
            _topic_words = [w.strip('?.,!').lower() for w in raw.split()
                            if len(w) > 3 and w.lower() not in
                            ('what', 'where', 'when', 'who', 'how', 'tell', 'about', 'does', 'will', 'can')]
            if _topic_words:
                self._discourse_focus = _topic_words[-1]

            return response

        # Fallback: natural-language dialogue
        self._mood_tracker.update(raw)
        response, new_state = self._dialogue_mgr.respond(raw, self.session_state)
        self.session_state = new_state
        self._memory.add_turn(raw, role="user")
        self._memory.add_turn(response, role="bot")

        # Implicit feedback detection — drives preference learning.
        try:
            from gofai_chat.learning.feedback import ImplicitFeedbackDetector
            ifd = ImplicitFeedbackDetector()
            feedback_signal = ifd.detect(raw)
            if feedback_signal is not None:
                try:
                    from gofai_chat.learning.feedback import FeedbackCollector
                    FeedbackCollector().add(feedback_signal)
                except Exception:
                    pass
        except Exception:
            pass

        if "[Use PoetMode" in response:
            return "What would you like to write about?"
        return response

    # ------------------------------------------------------------------
    # Wikipedia helpers
    # ------------------------------------------------------------------

    def _extract_wiki_query(self, raw: str) -> str:
        """Extract the search query from a Wikipedia trigger phrase."""
        raw_lower = raw.lower().strip()
        for prefix in _WIKI_QUERY_PREFIXES:
            if raw_lower.startswith(prefix):
                query = raw[len(prefix):].strip().strip(string.punctuation).strip()
                if query:
                    return query
        return ""

    def _handle_wikipedia(self, raw: str, query: str) -> str:
        """Fetch a Wikipedia article, answer the question, update world model."""
        try:
            from gofai_chat.knowledge.wikipedia_source import (
                WikipediaKnowledgeSource,
            )
        except Exception:
            return "Wikipedia integration is unavailable."

        src = WikipediaKnowledgeSource()
        article = src.search_and_fetch(query)
        if article is None:
            return f'No Wikipedia article found for "{query}".'

        answer = src.answer_from_article(article, raw)
        src.integrate_into_worldmodel(article)

        # Store article + derived DefaultRules in persistent KB.
        if self._wiki_reasoner is not None:
            try:
                from gofai_chat.knowledge.wiki_to_lf import WikiArticleToLF
                _rules = WikiArticleToLF().convert(article)
                _aid = self._wiki_reasoner._db.store_article(article)
                if _aid > 0:
                    self._wiki_reasoner._db.store_rules(_aid, _rules)
                    for _r in _rules:
                        self._wiki_reasoner._db.store_typicality(
                            _r.condition, _r.conclusion, _r.strength.to_prob()
                        )
            except Exception:
                pass

        # Update discourse focus so follow-up poems resolve correctly.
        self._discourse_focus = article.title

        slug = src._cache.slug(article.title)
        cache_path = src._cache.CACHE_DIR / f"{slug}.json"
        n_facts = len(article.key_facts)
        header = f"[Wikipedia: {article.title}]"
        footer = f"[Cached to {cache_path} | {n_facts} facts extracted]"
        return f"{header}\n{answer}\n{footer}"

    # ------------------------------------------------------------------
    # Prose → LF → Poem pipeline
    # ------------------------------------------------------------------

    def _handle_prose_to_poem(self, raw: str, form: str = "free_verse") -> str:
        """Handle prose→LF→poem requests.

        Strips the trigger phrase, passes the remaining prose through
        ProseToPoetry and stores the result as the current poem.
        """
        from gofai_chat.generation.poetry.prose_to_poetry import ProseToPoetry, ProseAnalyzer

        # Strip the leading trigger phrase to isolate the prose content.
        prose = raw
        trigger_phrases = (
            "turn this into a poem:",
            "write a poem that says:",
            "express as poetry:",
            "express this as poetry:",
            "make a poem from:",
            "turn into verse:",
            "poeticize:",
            "saying:",
        )
        raw_lower = raw.lower()
        for trigger in trigger_phrases:
            if trigger in raw_lower:
                prose = raw[raw_lower.index(trigger) + len(trigger):].strip()
                break
        else:
            if ":" in raw:
                prose = raw[raw.index(":") + 1:].strip()

        mood = self._mood_tracker.current_mood() if self._mood_tracker else "neutral"
        poem_text, grade = ProseToPoetry().convert(prose, form=form, requested_mood=mood)

        self.current_poem.current_text = poem_text
        analysis = ProseAnalyzer().analyze(prose)
        self.current_poem.topic = analysis.main_topic
        self.current_poem.versions.append(poem_text)

        # Enrich theta_roles with top wiki-derived facts before storing focus.
        if self._wiki_reasoner is not None:
            try:
                _facts = self._wiki_reasoner.reason_about(analysis.main_topic)
                for _prop, _g in _facts[:3]:
                    if _g.to_prob() > 0.7:
                        _key = 'property_' + str(len(analysis.theta_roles))
                        analysis.theta_roles[_key] = _prop.replace('_', ' ')
            except Exception:
                pass

        self._discourse_focus = self.current_poem.topic
        return f"{poem_text}\n\n{grade}"

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_poem(self, topic: str, form: str = "free verse", mood: str = "neutral") -> str:
        """Generate a poem about *topic* in *form* with *mood* tone.

        Dispatches to form-specific generators and commits the result.

        Parameters
        ----------
        topic:
            Thematic focus.
        form:
            One of: 'haiku', 'sonnet', 'couplets', 'free verse' (default).
        mood:
            Affective tone: matches keys in :data:`_VERBS_BY_MOOD`.

        Returns
        -------
        str
            Formatted poem with grade annotation.
        """
        form_norm = form.lower().strip()

        # Push QUD for this poem request.
        try:
            from gofai_chat.strata.info.qud_engine import QUDEntry, QUDType
            if self._qud_stack is not None:
                qt = QUDType.WH_QUESTION
                self._qud_stack.push(QUDEntry(qt, f"What poem about {topic}?"))
        except Exception:
            pass

        # Enrich topic with wiki imagery words before generation.
        enriched_topic = topic
        if self._wiki_reasoner is not None:
            try:
                _wiki_words = self._wiki_reasoner.imagery_for(topic)
                if _wiki_words:
                    # Prepend up to 5 wiki imagery words as supplementary context.
                    _extra = ' '.join(_wiki_words[:5])
                    enriched_topic = f"{topic} {_extra}"
            except Exception:
                pass

        # Delegate to PoemGenerator (primary path) with legacy fallbacks.
        try:
            from gofai_chat.generation.poetry.poem_generator import (
                PoemGenerator as _PoemGenerator,
                PoemRequest as _PoemRequest,
                Mood as _PoemMood,
            )
            from gofai_chat.generation.poetry.form_library import FORMS_BY_NAME as _FORMS_BY_NAME
            _form_obj = _FORMS_BY_NAME.get(form_norm) or _FORMS_BY_NAME.get("free verse")
            _mood_obj = None
            try:
                _mood_obj = _PoemMood(mood)
            except (ValueError, KeyError):
                pass
            _draft = _PoemGenerator().generate(
                _PoemRequest(topic=enriched_topic, form=_form_obj, mood=_mood_obj)
            )
            raw_poem = _draft.to_text()
        except Exception:
            # Legacy fallbacks
            if "haiku" in form_norm:
                raw_poem = _generate_haiku(enriched_topic, mood)
            elif "sonnet" in form_norm:
                raw_poem = _generate_sonnet_like(enriched_topic, mood)
            elif "couplet" in form_norm or "rhyme" in form_norm:
                raw_poem = _generate_couplet_poem(enriched_topic, mood)
            else:
                raw_poem = _generate_poem_free_verse(enriched_topic, mood)

        # Grade the poem
        grade = self._grade_poem(raw_poem, topic, mood)

        # Resolve the QUD for this poem request.
        try:
            if self._qud_stack is not None:
                self._qud_stack.resolve(None, grade)
        except Exception:
            pass

        # Update session poem
        self.current_poem.current_text = raw_poem
        self.current_poem.topic = topic
        self.current_poem.form = form_norm
        self.current_poem.mood = mood
        self.current_poem.versions.append(raw_poem)
        self.current_poem.grades.append(grade)
        self.current_poem.feedbacks.append("initial generation")

        # Commit to version control
        self._vc.commit(raw_poem, grade, feedback="initial generation")

        # Update dialogue state
        self.session_state = self.session_state.evolve(
            poem_context=raw_poem,
            topic=topic,
            mood=mood,
        )

        prompt = self._prompt_gen.generate_opening_prompt(topic, form_norm, mood)
        return self._dialogue_mgr.format_response(raw_poem, grade, f"[prompt: {prompt}]")

    def apply_feedback(self, poem: str, feedback: str) -> str:
        """Apply *feedback* to *poem* and return the revised poem (formatted).

        Feedback can be natural language ('make it sadder') or a direct
        line-change instruction ('change line 3').
        """
        intents = self._dialogue_mgr.extract_poetry_intent(feedback)
        revised = poem  # start from current text

        # ScalarSemantics: extract adjective grade and update poem mood.
        try:
            from gofai_chat.inference.scalar import ScalarSemantics as _SS
            _ss = _SS()
            feedback_lower = feedback.lower()
            for _adj in ('dark', 'light', 'sad', 'happy', 'cold', 'warm', 'short', 'long'):
                if _adj in feedback_lower or _adj + 'er' in feedback_lower:
                    _mods = [m for m in ('very', 'much', 'slightly', 'more', 'less', 'a bit')
                             if m in feedback_lower]
                    _grade = _ss.apply_modifier_chain(_adj, _mods) if _mods else None
                    _summary = _ss.adjective_summary(_adj)
                    self.current_poem.mood = _adj
                    break
        except Exception:
            pass

        if "change_line" in intents:
            n = intents["change_line"]
            topic = self.current_poem.topic
            mood = self._mood_tracker.current_mood()
            new_line = _generate_line(topic, mood)
            revised = self.current_poem.replace_line(n, new_line)

        elif "make_sadder" in intents or "dark" in feedback.lower():
            revised = self._apply_mood_shift(poem, "melancholic")

        elif "make_happier" in intents or "bright" in feedback.lower():
            revised = self._apply_mood_shift(poem, "joyful")

        elif "make_longer" in intents:
            topic = self.current_poem.topic
            mood = self._mood_tracker.current_mood()
            extra = "\n".join(_generate_line(topic, mood) for _ in range(4))
            revised = poem + "\n\n" + extra

        elif "make_shorter" in intents:
            all_lines = [l for l in poem.splitlines() if l.strip()]
            keep = max(2, len(all_lines) * 2 // 3)
            revised = "\n".join(all_lines[:keep])

        elif "more_rhyme" in intents:
            revised = self._add_simple_rhyme(poem)

        grade = self._grade_poem(revised, self.current_poem.topic, self.current_poem.mood)
        self.current_poem.current_text = revised
        self.current_poem.versions.append(revised)
        self.current_poem.grades.append(grade)
        self.current_poem.feedbacks.append(feedback)
        self._vc.commit(revised, grade, feedback=feedback)

        revision_prompt = self._prompt_gen.generate_revision_prompt(
            poem, feedback, self.current_poem.topic, self.current_poem.form
        )
        return self._dialogue_mgr.format_response(
            revised, grade, f"[revision prompt: {revision_prompt}]"
        )

    def explain_choice(self, line: str) -> str:
        """Return a Grade-based explanation of why *line* was chosen.

        Decomposes the line quality into phonetic, semantic, and metrical
        components and formats them as a readable breakdown.
        """
        phonetic_g = self._phonetic_grade(line)
        semantic_g = self._semantic_grade(line, self.current_poem.topic)
        metrical_g = self._metrical_grade(line)
        total_g = phonetic_g * semantic_g * metrical_g

        return (
            f"Line: {line!r}\n"
            f"  Phonetic grade : {phonetic_g} (sound quality, consonance, assonance)\n"
            f"  Semantic grade : {semantic_g} (relevance to topic '{self.current_poem.topic}')\n"
            f"  Metrical grade : {metrical_g} (rhythm regularity)\n"
            f"  Total grade    : {total_g}\n"
            f"The line was selected because its product grade {total_g} ranked "
            f"highest among the candidates generated."
        )

    # ------------------------------------------------------------------
    # Private grading helpers
    # ------------------------------------------------------------------

    def _grade_poem(self, poem: str, topic: str, mood: str) -> Grade:
        """Compute a composite Grade for *poem* using real harmonic grading."""
        lines = [l.strip() for l in poem.splitlines() if l.strip()]
        if not lines:
            return Grade.impossible()
        try:
            from gofai_chat.chat._harmony_builder import HarmonyBuilder
            form = getattr(self.current_poem, 'form', 'free_verse') or 'free_verse'
            _, breakdown = HarmonyBuilder().build_and_score(lines, topic=topic, form=form)
            total = breakdown.get('total')
            if total is not None and isinstance(total, Grade):
                return total
        except Exception:
            pass
        # Polyphony grade
        poly_grade = Grade.from_prob(0.3)
        try:
            from gofai_chat.polyphony.detector import PolyphonyDetector
            pd = PolyphonyDetector()
            poem_text = poem
            for method in ('detect', 'analyze', 'score', 'detect_voices'):
                if hasattr(pd, method):
                    result = getattr(pd, method)(poem_text or " ".join(lines))
                    if hasattr(result, 'grade'):
                        poly_grade = result.grade
                    elif isinstance(result, (int, float)):
                        poly_grade = Grade.from_prob(min(1.0, result / 3.0))
                    break
        except Exception:
            pass
        # Fallback to heuristic grading
        line_grades = [
            self._phonetic_grade(l) * self._semantic_grade(l, topic) * self._metrical_grade(l)
            for l in lines
        ]
        heuristic = Grade.mean(line_grades)
        return Grade.mean([heuristic, poly_grade])

    def _check_presuppositions(self, raw: str) -> dict:
        """Extract presuppositions from utterance for contextual awareness."""
        try:
            from gofai_chat.sem.presupposition import PresuppositionComputer, PresuppositionAccommodator  # noqa: F401
            pc = PresuppositionComputer()
            for method in ('compute', 'detect', 'find_triggers', 'extract'):
                if hasattr(pc, method):
                    result = getattr(pc, method)(raw)
                    return {'presuppositions': result}
        except Exception:
            pass
        return {}

    def _format_grade_display(self) -> str:
        """Return a multi-dimensional grade display for the current poem.

        Uses HarmonyBuilder for real stratum breakdown, falling back to
        PoetryFrameAnalyzer / JakobsonAnalyzer for richer annotation.
        """
        poem_text = self.session_state.poem_context or ""
        lines = [l.strip() for l in poem_text.splitlines() if l.strip()]
        parts: List[str] = []

        # Primary: real harmonic breakdown via HarmonyBuilder
        if lines:
            try:
                from gofai_chat.chat._harmony_builder import HarmonyBuilder
                topic = getattr(self.current_poem, 'topic', '') or ''
                form = getattr(self.current_poem, 'form', 'free_verse') or 'free_verse'
                _, breakdown = HarmonyBuilder().build_and_score(lines, topic=topic, form=form)
                total = breakdown.get('total')
                if total is not None:
                    pct = int(float(total.to_prob() if hasattr(total, 'to_prob') else total) * 100)
                    header = f"Harmony: {pct}%"
                for key in ('stratal_grades', 'constraint_grades'):
                    grdict = breakdown.get(key, {})
                    if isinstance(grdict, dict):
                        for k, v in list(grdict.items())[:3]:
                            try:
                                vp = int(float(v.to_prob() if hasattr(v, 'to_prob') else v) * 100)
                                parts.append(f"{k}: {vp}%")
                            except Exception:
                                pass
                if total is not None and parts:
                    return f"[{header} — {' | '.join(parts[:5])}]"
                if total is not None:
                    return f"[{header}]"
            except Exception:
                pass

        # Fallback: PoetryFrameAnalyzer breakdown
        try:
            from gofai_chat.sem.frame_network import PoetryFrameAnalyzer
            analysis = PoetryFrameAnalyzer().analyze(poem_text)
            for key in ("tension", "coherence", "metaphor_density", "surprise"):
                if key in analysis:
                    val = analysis[key]
                    pct = int((val.to_prob() if hasattr(val, "to_prob") else val) * 100)
                    parts.append(f"{key.replace('_', ' ').title()}: {pct}%")
        except Exception:
            pass

        # JakobsonAnalyzer poetic-function grade
        try:
            from gofai_chat.strata.poet.jakobson import JakobsonAnalyzer
            jak_grade = JakobsonAnalyzer().poetic_function(poem_text, None)
            pct = int(jak_grade.to_prob() * 100)
            parts.append(f"Poetic Function: {pct}%")
        except Exception:
            pass

        # TropeDetector count
        try:
            from gofai_chat.analysis.tropes import TropeDetector
            tropes = TropeDetector().detect(poem_text)
            if tropes:
                avg_conf = sum(t.grade.to_prob() for t in tropes) / len(tropes)
                parts.append(f"Trope Richness: {int(avg_conf * 100)}% ({len(tropes)} tropes)")
        except Exception:
            pass

        if parts:
            return "[" + " | ".join(parts) + "]"

        # Fallback: single harmony grade
        return f"Harmony grade: {self.session_state.harmony_grade}"

    def _phonetic_grade(self, line: str) -> Grade:
        """Grade the phonetic quality of *line* (consonance, assonance, flow)."""
        words = re.findall(r"[a-z]+", line.lower())
        if len(words) < 2:
            return Grade.from_prob(0.5)
        # Assonance: repeated vowel sounds
        vowel_seqs = [re.findall(r"[aeiou]+", w) for w in words]
        flat_vowels = [v for vs in vowel_seqs for v in vs]
        if not flat_vowels:
            return Grade.from_prob(0.4)
        counter: Dict[str, int] = {}
        for v in flat_vowels:
            counter[v] = counter.get(v, 0) + 1
        max_freq = max(counter.values()) if counter else 1
        assonance = max_freq / max(len(flat_vowels), 1)
        # Consonance: repeated initial consonants (alliteration)
        initials = [w[0] for w in words if w and w[0] not in "aeiou"]
        if initials:
            init_counter: Dict[str, int] = {}
            for c in initials:
                init_counter[c] = init_counter.get(c, 0) + 1
            alliteration = max(init_counter.values()) / max(len(initials), 1)
        else:
            alliteration = 0.0
        score = 0.5 + 0.3 * assonance + 0.2 * alliteration
        return Grade.from_prob(min(score, 0.95))

    def _semantic_grade(self, line: str, topic: str) -> Grade:
        """Grade how strongly *line* relates to *topic*."""
        _, topic_g = self._topic_model.detect_topic(line + " " + topic)
        return topic_g

    def _metrical_grade(self, line: str) -> Grade:
        """Grade metrical regularity via syllable-count variance."""
        words = re.findall(r"[a-z]+", line.lower())
        if not words:
            return Grade.from_prob(0.5)
        syllables = [_count_syllables_simple(w) for w in words]
        total = sum(syllables)
        # Penalise extremely short or long lines
        if total < 4:
            return Grade.from_prob(0.3)
        if total > 16:
            return Grade.from_prob(0.5)
        # Prefer 8-12 syllables
        ideal = 10.0
        deviation = abs(total - ideal) / ideal
        score = max(0.3, 1.0 - deviation)
        return Grade.from_prob(score)

    # ------------------------------------------------------------------
    # Private revision helpers
    # ------------------------------------------------------------------

    def _apply_mood_shift(self, poem: str, target_mood: str) -> str:
        """Substitute vocabulary to shift the poem toward *target_mood*."""
        lines = poem.splitlines()
        result: List[str] = []
        source_mood = self.current_poem.mood
        topic = self.current_poem.topic
        for line in lines:
            if not line.strip():
                result.append(line)
                continue
            # With 40% probability, regenerate the line in the target mood
            if random.random() < 0.4:
                result.append(_generate_line(topic, target_mood))
            else:
                # Substitute individual mood words
                result.append(self._substitute_mood_words(line, source_mood, target_mood))
        return "\n".join(result)

    def _substitute_mood_words(self, line: str, source_mood: str, target_mood: str) -> str:
        """Replace mood-carrying words in *line* with target-mood equivalents."""
        source_verbs = set(_VERBS_BY_MOOD.get(source_mood, []))
        source_adjs  = set(_ADJS_BY_MOOD.get(source_mood, []))
        target_verbs = _VERBS_BY_MOOD.get(target_mood, _VERBS_BY_MOOD["neutral"])
        target_adjs  = _ADJS_BY_MOOD.get(target_mood, _ADJS_BY_MOOD["neutral"])
        tokens = line.split()
        result: List[str] = []
        for token in tokens:
            clean = token.lower().strip(string.punctuation)
            if clean in source_verbs:
                result.append(random.choice(target_verbs))
            elif clean in source_adjs:
                result.append(random.choice(target_adjs))
            else:
                result.append(token)
        return " ".join(result)

    def _add_simple_rhyme(self, poem: str) -> str:
        """Attempt to make alternate lines rhyme by sharing end sounds."""
        lines = [l for l in poem.splitlines() if l.strip()]
        result: List[str] = []
        for i, line in enumerate(lines):
            if i % 2 == 1 and result:
                # Try to end this line with a sound similar to the previous
                prev_line = result[-1]
                prev_end_word = re.findall(r"[a-z]+", prev_line.lower())
                if prev_end_word:
                    end = prev_end_word[-1]
                    # Append a simple rhyme-compatible word (vowel match)
                    vowel_ending = re.findall(r"[aeiou]+[^aeiou]*$", end)
                    if vowel_ending:
                        suffix = vowel_ending[0]
                        # Replace last word of current line with one sharing suffix
                        words = line.rstrip().rsplit(None, 1)
                        if len(words) == 2 and len(words[1]) > 2:
                            replacement = words[1][0] + suffix
                            line = words[0] + " " + replacement
            result.append(line)
        return "\n".join(result)


# ---------------------------------------------------------------------------
# String import guard
# ---------------------------------------------------------------------------

import string  # noqa: E402 — used in _substitute_mood_words above


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def start_poet_session(topic: str = "poetry", mood: str = "neutral") -> PoetMode:
    """Create and return a ready-to-use :class:`PoetMode` instance."""
    return PoetMode(topic=topic, mood=mood)


# ---------------------------------------------------------------------------
# SessionStats
# ---------------------------------------------------------------------------

class SessionStats:
    """Accumulates per-session statistics for the poet mode.

    Tracks grade arc, topic distribution, mood arc, and generation counts.
    """

    def __init__(self) -> None:
        self._poem_grades: List[Grade] = []
        self._topics: List[str] = []
        self._moods: List[str] = []
        self._revision_count: int = 0
        self._turn_count: int = 0
        self._start_time: float = time.time()

    def record_poem(self, grade: Grade, topic: str, mood: str) -> None:
        self._poem_grades.append(grade)
        self._topics.append(topic)
        self._moods.append(mood)

    def record_revision(self) -> None:
        self._revision_count += 1

    def record_turn(self) -> None:
        self._turn_count += 1

    def mean_poem_grade(self) -> Grade:
        return Grade.mean(self._poem_grades) if self._poem_grades else Grade.impossible()

    def best_poem_grade(self) -> Grade:
        return Grade.best(self._poem_grades) if self._poem_grades else Grade.impossible()

    def dominant_topic(self) -> str:
        if not self._topics:
            return "poetry"
        from collections import Counter
        c = Counter(self._topics)
        return c.most_common(1)[0][0]

    def dominant_mood(self) -> str:
        if not self._moods:
            return "neutral"
        from collections import Counter
        c = Counter(self._moods)
        return c.most_common(1)[0][0]

    def grade_trend(self) -> float:
        """Linear slope of grade values over poem generation order."""
        if len(self._poem_grades) < 2:
            return 0.0
        values = [g.value for g in self._poem_grades if not math.isinf(g.value)]
        if len(values) < 2:
            return 0.0
        if _HAS_NUMPY:
            xs = np.arange(len(values), dtype=float)
            return float(np.polyfit(xs, values, 1)[0])
        n = len(values)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(values) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
        den = sum((x - mx) ** 2 for x in xs) or 1.0
        return num / den

    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    def summary(self) -> str:
        elapsed = self.elapsed_seconds()
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        lines = [
            f"Session duration:   {minutes}m {seconds}s",
            f"Turns:              {self._turn_count}",
            f"Poems generated:    {len(self._poem_grades)}",
            f"Revisions:          {self._revision_count}",
            f"Mean poem grade:    {self.mean_poem_grade()}",
            f"Best poem grade:    {self.best_poem_grade()}",
            f"Grade trend:        {self.grade_trend():+.4f} per poem",
            f"Dominant topic:     {self.dominant_topic()}",
            f"Dominant mood:      {self.dominant_mood()}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# FeedbackParser
# ---------------------------------------------------------------------------

class FeedbackParser:
    """Parses natural-language feedback into structured editing directives.

    Complements :meth:`PoetryDialogueManager.extract_poetry_intent` with
    richer parsing: identifies specific lines, attributes, and targets.
    """

    # Patterns for extracting line-specific feedback
    _LINE_PATTERNS = [
        re.compile(r"line\s+(\d+)\s+(?:is|feels?|sounds?|seems?)\s+([\w\s]+?)(?:\.|,|$)", re.I),
        re.compile(r"(?:change|fix|revise|rewrite)\s+line\s+(\d+)", re.I),
        re.compile(r"line\s+(\d+)\s+(?:needs?|should)\s+([\w\s]+?)(?:\.|,|$)", re.I),
    ]

    _ATTRIBUTE_PATTERNS = {
        "rhyme":   re.compile(r"\b(rhyme|rhyming|rhymed)\b", re.I),
        "meter":   re.compile(r"\b(meter|rhythm|cadence|beat|syllable)\b", re.I),
        "imagery": re.compile(r"\b(image|imagery|metaphor|simile|visual|concrete)\b", re.I),
        "mood":    re.compile(r"\b(mood|tone|feeling|emotion|atmosphere)\b", re.I),
        "diction": re.compile(r"\b(word|diction|language|vocabulary|phrasing)\b", re.I),
        "length":  re.compile(r"\b(length|long|short|lines|stanza)\b", re.I),
    }

    def parse(self, feedback: str) -> Dict[str, Any]:
        """Parse *feedback* and return a structured directive dict.

        Keys: ``line_number``, ``attribute``, ``direction``, ``raw``.
        """
        result: Dict[str, Any] = {"raw": feedback}

        # Line number
        for pat in self._LINE_PATTERNS:
            m = pat.search(feedback)
            if m:
                try:
                    result["line_number"] = int(m.group(1))
                except (ValueError, IndexError):
                    pass
                break

        # Attribute
        for attr, pat in self._ATTRIBUTE_PATTERNS.items():
            if pat.search(feedback):
                result["attribute"] = attr
                break

        # Direction (more/less, better/worse)
        if re.search(r"\b(more|add|increase|stronger|better|improve)\b", feedback, re.I):
            result["direction"] = "increase"
        elif re.search(r"\b(less|remove|decrease|weaker|reduce)\b", feedback, re.I):
            result["direction"] = "decrease"

        # Mood targets
        for mood in ["sad", "happy", "dark", "bright", "angry", "serene", "romantic",
                     "melancholic", "joyful", "contemplative", "mysterious"]:
            if re.search(r"\b" + mood + r"\b", feedback, re.I):
                result["target_mood"] = mood
                break

        return result


# ---------------------------------------------------------------------------
# LineExplainer
# ---------------------------------------------------------------------------

class LineExplainer:
    """Generates Grade-based explanations for individual line choices.

    Each explanation decomposes the line's composite Grade into its phonetic,
    semantic, and metrical components and expresses the choice in natural
    language.
    """

    def __init__(self, poem_context: Optional[SessionPoem] = None) -> None:
        self._context = poem_context

    def explain(self, line: str, topic: str = "poetry", mood: str = "neutral") -> str:
        """Return a natural-language explanation for *line*.

        Includes component grades, vocabulary analysis, and the comparative
        ranking that led to the line's selection.
        """
        from gofai_chat.generation.poetry.line_generator import (
            _phonetic_heuristic_grade, _semantic_fit_grade, _meter_fit_grade,
            LineSpec,
        )
        spec = LineSpec(semantic_target=topic, mood=mood, syllable_count=10)
        ph_g = _phonetic_heuristic_grade(line)
        sem_g = _semantic_fit_grade(line, topic)
        met_g = _meter_fit_grade(line, spec)
        total_g = ph_g * sem_g * met_g

        words = re.findall(r"[a-z]+", line.lower())
        vowels = [re.findall(r"[aeiou]+", w) for w in words]
        flat_v = [v for vs in vowels for v in vs]
        dominant_vowel = max(set(flat_v), key=flat_v.count) if flat_v else "?"

        alliterating = [w for w in words if words.count(w[0]) > 1] if words else []

        lines = [
            f"Line: {line!r}",
            f"",
            f"Grade breakdown:",
            f"  • Phonetic  {ph_g} — assonance on /{dominant_vowel}/; "
            f"alliteration on [{', '.join(set(alliterating[:3]))}]" if alliterating else
            f"  • Phonetic  {ph_g} — vowel texture on /{dominant_vowel}/",
            f"  • Semantic  {sem_g} — topical fit to '{topic}'",
            f"  • Metrical  {met_g} — syllable count approx. {sum(max(1, len(re.findall(r'[aeiou]+', w))) for w in words)}",
            f"  ─────────────────────────────────",
            f"  • Total     {total_g}",
            f"",
            f"The line was selected because its composite Grade ({total_g}) "
            f"ranked highest among the 10 candidates generated for this position.",
        ]
        return "\n".join(lines)

    def compare_lines(
        self, line_a: str, line_b: str, topic: str = "poetry", mood: str = "neutral"
    ) -> str:
        """Return a comparison explanation for two alternative lines."""
        from gofai_chat.generation.poetry.line_generator import (
            _phonetic_heuristic_grade, _semantic_fit_grade, _meter_fit_grade,
            LineSpec,
        )
        spec = LineSpec(semantic_target=topic, mood=mood, syllable_count=10)
        grade_a = (_phonetic_heuristic_grade(line_a) * _semantic_fit_grade(line_a, topic)
                   * _meter_fit_grade(line_a, spec))
        grade_b = (_phonetic_heuristic_grade(line_b) * _semantic_fit_grade(line_b, topic)
                   * _meter_fit_grade(line_b, spec))
        if grade_a >= grade_b:
            winner, loser, wg, lg = line_a, line_b, grade_a, grade_b
        else:
            winner, loser, wg, lg = line_b, line_a, grade_b, grade_a
        return (
            f"Winner: {winner!r}  (grade {wg})\n"
            f"Loser:  {loser!r}  (grade {lg})\n"
            f"Margin: {wg.to_prob() - lg.to_prob():.3f} probability points"
        )


# ---------------------------------------------------------------------------
# Export additions
# ---------------------------------------------------------------------------

__all__ += [
    "SessionStats",
    "FeedbackParser",
    "LineExplainer",
    "start_poet_session",
]
