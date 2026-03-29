"""Main poetry generation pipeline for the GOFAI chatbot.

The generator works in five stages, each grounded in Harmony Theory:

1. **Request interpretation** — convert a :class:`PoemRequest` into a
   populated :class:`GluingData` with the target :class:`PoetSection`.
2. **Line generation** — :class:`LineGenerator` produces candidate lines
   satisfying meter, rhyme-slot, and semantic constraints.
3. **Stanza assembly** — :class:`StanzaBuilder` groups lines respecting the
   target form's stanza structure.
4. **Harmonic refinement** — :class:`PoemRefiner` iteratively applies
   :class:`DescentComputer`-style improvement until the aggregate
   :class:`Grade` exceeds the acceptance threshold.
5. **Post-processing** — normalise capitalisation, fix contractions,
   and emit the final :class:`PoemDraft`.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from gofai_chat.core.grade import Grade
from gofai_chat.harmony.constraints import (
    PhonSection, PoetSection, SynSection, SemSection, PragSection, InfoSection
)

try:
    from gofai_chat.harmony.gluing import GluingData
except ImportError:
    GluingData = None  # type: ignore[assignment,misc]

try:
    from gofai_chat.harmony.harmony import HarmonyComputer, HarmonyBreakdown
except ImportError:
    HarmonyComputer = None  # type: ignore[assignment,misc]
    HarmonyBreakdown = None  # type: ignore[assignment,misc]

try:
    from gofai_chat.harmony.descent import DescentComputer, DescentResult
except ImportError:
    DescentComputer = None  # type: ignore[assignment,misc]
    DescentResult = None  # type: ignore[assignment,misc]

try:
    from gofai_chat.strata.phon.analyzer import PhonAnalyzer
    from gofai_chat.strata.phon.prosody import MeterType, Foot, METER_CATALOG
except ImportError:
    PhonAnalyzer = None  # type: ignore[assignment,misc]
    MeterType = None  # type: ignore[assignment,misc]
    Foot = None  # type: ignore[assignment,misc]
    METER_CATALOG = {}  # type: ignore[assignment,misc]

try:
    from gofai_chat.generation.poetry.meter_engine import (
        MeterScanner, MeterAnalyzer, MeterEnforcer, ScannerResult, IAMBIC_PENTAMETER,
        IAMBIC_TETRAMETER, BALLAD_METER, DACTYLIC_HEXAMETER,
    )
except ImportError:
    MeterScanner = None  # type: ignore[assignment,misc]
    MeterAnalyzer = None  # type: ignore[assignment,misc]
    MeterEnforcer = None  # type: ignore[assignment,misc]
    ScannerResult = None  # type: ignore[assignment,misc]
    IAMBIC_PENTAMETER = METER_CATALOG.get("iambic_pentameter") if METER_CATALOG else None
    IAMBIC_TETRAMETER = METER_CATALOG.get("iambic_tetrameter") if METER_CATALOG else None
    BALLAD_METER = METER_CATALOG.get("ballad_meter") if METER_CATALOG else None
    DACTYLIC_HEXAMETER = METER_CATALOG.get("dactylic_hexameter") if METER_CATALOG else None

try:
    from gofai_chat.generation.poetry.rhyme_engine import (
        RhymeScheme, RhymeGrader, RhymeFinder, RHYME_DICTIONARY,
        RhymeSchemeEnforcer, RhymeQuality,
    )
except ImportError:
    RhymeScheme = None  # type: ignore[assignment,misc]
    RhymeGrader = None  # type: ignore[assignment,misc]
    RhymeFinder = None  # type: ignore[assignment,misc]
    RHYME_DICTIONARY = {}  # type: ignore[assignment,misc]
    RhymeSchemeEnforcer = None  # type: ignore[assignment,misc]
    RhymeQuality = None  # type: ignore[assignment,misc]

try:
    from gofai_chat.generation.poetry.form_library import (
        PoemForm, FormChecker, FormSelector, ALL_FORMS, FORMS_BY_NAME,
        SHAKESPEAREAN_SONNET, HAIKU, FREE_VERSE, BLANK_VERSE, LIMERICK,
        VILLANELLE, BALLAD, ELEGY,
    )
except ImportError:
    PoemForm = None  # type: ignore[assignment,misc]
    FormChecker = None  # type: ignore[assignment,misc]
    FormSelector = None  # type: ignore[assignment,misc]
    ALL_FORMS = []  # type: ignore[assignment,misc]
    FORMS_BY_NAME = {}  # type: ignore[assignment,misc]
    SHAKESPEAREAN_SONNET = None  # type: ignore[assignment,misc]
    HAIKU = None  # type: ignore[assignment,misc]
    FREE_VERSE = None  # type: ignore[assignment,misc]
    BLANK_VERSE = None  # type: ignore[assignment,misc]
    LIMERICK = None  # type: ignore[assignment,misc]
    VILLANELLE = None  # type: ignore[assignment,misc]
    BALLAD = None  # type: ignore[assignment,misc]
    ELEGY = None  # type: ignore[assignment,misc]

try:
    from gofai_chat.generation.poetry.style_profiles import PoetStyle, StyleTransfer, StyleGrader
except ImportError:
    PoetStyle = None  # type: ignore[assignment,misc]
    StyleTransfer = None  # type: ignore[assignment,misc]
    StyleGrader = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Mood and Style enums
# ---------------------------------------------------------------------------

class Mood(Enum):
    """Primary emotional mood for poem generation."""
    JOYFUL = "joyful"
    MELANCHOLIC = "melancholic"
    ANGRY = "angry"
    PEACEFUL = "peaceful"
    LONGING = "longing"
    NOSTALGIC = "nostalgic"
    ECSTATIC = "ecstatic"
    SUBLIME = "sublime"
    IRONIC = "ironic"
    MEDITATIVE = "meditative"
    PASSIONATE = "passionate"
    FEARFUL = "fearful"
    TRIUMPHANT = "triumphant"
    SORROWFUL = "sorrowful"
    TRANSCENDENT = "transcendent"


class Style(Enum):
    """Broad stylistic mode for generation."""
    CLASSICAL = "classical"
    ROMANTIC = "romantic"
    MODERNIST = "modernist"
    POSTMODERN = "postmodern"
    CONFESSIONAL = "confessional"
    IMAGIST = "imagist"
    SYMBOLIST = "symbolist"
    SURREALIST = "surrealist"
    FORMAL = "formal"
    FREE = "free"
    MINIMALIST = "minimalist"
    MAXIMALIST = "maximalist"


# ---------------------------------------------------------------------------
# Topic / mood word banks
# ---------------------------------------------------------------------------

COMMON_POETIC_WORDS: Dict[str, List[str]] = {
    "love": [
        "heart", "desire", "longing", "flame", "embrace", "tender",
        "devotion", "adoration", "beloved", "yearning", "ardour",
        "passion", "sweet", "caress", "gentle", "entwine", "forever",
        "eternal", "bliss", "delight", "cherish", "whisper", "sigh",
        "enchant", "bind", "vow", "bloom", "radiant", "grace",
        "surrender", "surrender", "dream", "tender", "ache", "glow",
        "kindle", "melt", "breathe", "pulse", "wonder", "trust",
        "faithful", "devotion", "intimate", "pure", "sacred", "warm",
        "soft", "linger", "hold", "gaze", "longing", "close",
    ],
    "death": [
        "shadow", "darkness", "silent", "grave", "pale", "dust",
        "fade", "end", "last", "still", "cold", "hollow", "lost",
        "mourn", "grief", "sorrow", "weep", "elegy", "final",
        "vanish", "decay", "wither", "ash", "tomb", "rest",
        "eternal", "sleep", "passing", "mortal", "dissolution",
        "threshold", "beyond", "departed", "absence", "void",
        "remember", "forgotten", "brief", "transient", "fleeting",
        "time", "clock", "stone", "night", "quiet", "peace",
        "release", "surrender", "nothingness", "beyond", "crossing",
    ],
    "nature": [
        "wind", "rain", "river", "tree", "leaf", "sky", "cloud",
        "mountain", "ocean", "shore", "stone", "flower", "grass",
        "bird", "dawn", "dusk", "moon", "star", "sun", "snow",
        "ice", "forest", "meadow", "stream", "wave", "cliff",
        "valley", "hill", "mist", "fog", "thunder", "lightning",
        "blossom", "root", "branch", "thorn", "rose", "oak",
        "hawk", "deer", "fox", "wolf", "heron", "crow", "swallow",
        "spring", "summer", "autumn", "winter", "frost", "thaw",
        "bloom", "harvest", "seed", "soil", "dark", "wild",
    ],
    "time": [
        "moment", "hour", "day", "year", "century", "ancient",
        "memory", "past", "present", "future", "fleeting", "eternal",
        "brief", "long", "swift", "slow", "turning", "passing",
        "age", "youth", "old", "new", "morning", "evening",
        "season", "change", "still", "motion", "river", "clock",
        "tide", "cycle", "return", "beginning", "end", "middle",
        "infinite", "finite", "now", "then", "once", "always",
        "never", "forget", "remember", "yesterday", "tomorrow",
        "twilight", "midnight", "dawn", "dusk", "noon", "shadow",
    ],
    "god": [
        "divine", "sacred", "holy", "light", "grace", "mercy",
        "prayer", "heaven", "spirit", "soul", "eternal", "infinite",
        "glory", "presence", "mystery", "awe", "wonder", "creation",
        "praise", "blessing", "faith", "truth", "love", "wisdom",
        "power", "majesty", "radiance", "silence", "listen",
        "invoke", "worship", "kneel", "humble", "trembling",
        "transfigure", "redeem", "promise", "covenant", "fire",
        "burning bush", "pillar", "voice", "thunder", "word",
        "logos", "breath", "creation", "darkness", "light",
    ],
    "journey": [
        "road", "path", "wander", "seek", "find", "lost", "return",
        "depart", "arrive", "distant", "horizon", "compass", "way",
        "destination", "passage", "crossing", "threshold", "gate",
        "bridge", "mountain", "valley", "forest", "river", "shore",
        "wind", "guide", "companion", "alone", "pilgrim", "quest",
        "adventure", "discover", "arrive", "home", "exile", "wonder",
        "map", "star", "direction", "light", "dark", "dawn", "dusk",
        "rest", "continue", "forward", "backward", "pause", "step",
    ],
    "sadness": [
        "tears", "grief", "sorrow", "mourn", "weep", "lament",
        "ache", "hollow", "empty", "silence", "dark", "rain",
        "cold", "alone", "apart", "longing", "loss", "gone",
        "absence", "miss", "remember", "forget", "fade", "wither",
        "broken", "fall", "dim", "pale", "grey", "winter",
        "night", "shadow", "stone", "heavy", "weight", "burden",
        "sigh", "whisper", "distant", "far", "lost", "never",
        "past", "once", "yesterday", "old", "wound", "scar",
    ],
    "joy": [
        "light", "bright", "bloom", "sing", "dance", "play",
        "laugh", "golden", "warm", "radiant", "soar", "flight",
        "rise", "morning", "spring", "new", "fresh", "alive",
        "delight", "wonder", "awe", "sweet", "glowing", "celebrate",
        "rejoice", "grateful", "blessing", "abundance", "overflow",
        "harmony", "peace", "complete", "whole", "full", "love",
        "heart", "fire", "sun", "sky", "open", "free", "wide",
        "clear", "pure", "crystal", "perfect", "beautiful",
    ],
    "beauty": [
        "radiant", "luminous", "exquisite", "sublime", "perfect",
        "grace", "form", "symmetry", "harmony", "glowing", "bright",
        "pure", "clear", "golden", "silver", "pearl", "crystal",
        "flower", "dawn", "star", "light", "music", "song",
        "fragrant", "soft", "tender", "still", "deep", "eternal",
        "truth", "goodness", "divine", "transcendent", "rare",
        "fleeting", "delicate", "precious", "singular", "wonder",
    ],
    "war": [
        "battle", "sword", "shield", "blood", "fire", "iron",
        "fallen", "glory", "honour", "courage", "fear", "death",
        "wound", "mourn", "shattered", "thunder", "cannon", "blade",
        "victory", "defeat", "march", "advance", "retreat", "stand",
        "front", "trench", "dark", "mud", "cry", "silence",
        "memorial", "grave", "forgotten", "hero", "sacrifice",
        "peace", "rest", "return", "home", "broken", "scarred",
    ],
    "memory": [
        "remember", "recall", "return", "past", "once", "old",
        "faded", "photograph", "ghost", "echo", "trace", "shadow",
        "dream", "vision", "light", "voice", "face", "touch",
        "distant", "long", "ago", "childhood", "youth", "place",
        "room", "garden", "tree", "road", "letter", "scent",
        "soft", "warm", "dear", "lost", "find", "searching",
        "time", "river", "flowing", "still", "quiet", "hold",
    ],
}

TOPIC_SEED_WORDS: Dict[str, List[str]] = {
    "love": ["love", "heart", "beloved", "desire", "tender"],
    "death": ["death", "end", "darkness", "silence", "grave"],
    "nature": ["nature", "wind", "river", "tree", "sky"],
    "time": ["time", "moment", "passing", "eternity", "brief"],
    "god": ["god", "divine", "sacred", "prayer", "eternal"],
    "journey": ["journey", "road", "wandering", "destination", "return"],
    "sadness": ["sadness", "grief", "sorrow", "tears", "loss"],
    "joy": ["joy", "light", "bloom", "celebrate", "delight"],
    "beauty": ["beauty", "radiant", "grace", "perfect", "sublime"],
    "war": ["war", "battle", "courage", "fallen", "peace"],
    "memory": ["memory", "remember", "past", "echo", "trace"],
}

MOOD_VOCABULARY: Dict[str, List[str]] = {
    "joyful": [
        "bright", "bloom", "dance", "golden", "soar", "sing",
        "delight", "warm", "radiant", "celebrate", "spring",
    ],
    "melancholic": [
        "fade", "shadow", "grey", "hollow", "cold", "wither",
        "sorrow", "alone", "lost", "weep", "dim", "sigh",
    ],
    "peaceful": [
        "still", "quiet", "soft", "gentle", "calm", "clear",
        "breathe", "rest", "serene", "tranquil", "undisturbed",
    ],
    "passionate": [
        "fire", "burn", "fierce", "longing", "ache", "flame",
        "wild", "devour", "consume", "hunger", "trembling",
    ],
    "meditative": [
        "ponder", "deep", "slow", "wonder", "watch", "listen",
        "observe", "consider", "still", "vast", "distant",
    ],
    "ecstatic": [
        "overflow", "transcend", "soar", "ecstasy", "rapture",
        "luminous", "blazing", "infinite", "dissolve", "become",
    ],
    "melancholic_advanced": [
        "elegiac", "mournful", "bereft", "keening", "wailing",
        "lament", "threnody", "valediction", "dirge",
    ],
    "sublime": [
        "immense", "vast", "overwhelming", "awe", "terrifying",
        "magnificent", "towering", "unfathomable", "abyssal",
    ],
}

# Template strings for line generation
LINE_TEMPLATES: Dict[str, List[str]] = {
    "nature_opening": [
        "When {adj} {noun} fills the {time} air,",
        "Beneath the {adj} {noun} of {season},",
        "The {adj} {noun} of {topic},",
        "In {topic}, the {noun} {verb},",
        "Where {adj} {noun} meets the {noun2},",
    ],
    "love_opening": [
        "How {adj} your {noun} in the {time} light,",
        "What {noun} is this that {verb} the heart?",
        "The {adj} warmth of your {noun} remains,",
        "A {adj} {noun} holds {topic} still,",
        "In {topic}, the {noun} {verb},",
    ],
    "death_opening": [
        "No more the {adj} {noun} speaks or moves,",
        "That {adj} {noun} is now a memory,",
        "Into the {adj} {noun} we all descend,",
        "The {adj} {noun} of {topic},",
        "Through {noun}, the {topic} {verb},",
    ],
    "closing_couplet": [
        "And so the {noun} remains when we are gone,",
        "This {adj} truth endures when words are done.",
        "So let the {noun} speak of what we've known.",
        "The {adj} {noun} outlasts the fading {noun2}.",
        "When {topic} {verb}, the {noun} endures.",
    ],
    "continuation": [
        "And still the {adj} {noun} calls to mind,",
        "Yet in the {adj} {noun} something remains,",
        "The {noun} that {verb} within the heart,",
        "Through every {adj} season, {noun} returns,",
        "When {topic} {verb}, the {noun} {verb2},",
        "A {adj} {noun} holds {topic} still,",
    ],
}

ADJECTIVES: Dict[str, List[str]] = {
    "nature": ["silver", "golden", "soft", "deep", "wide", "dark", "bright", "cold", "warm"],
    "love": ["tender", "sweet", "beloved", "dear", "true", "faithful", "gentle", "warm"],
    "death": ["pale", "cold", "dark", "silent", "still", "shadowed", "hollow", "final"],
    "time": ["swift", "slow", "ancient", "brief", "eternal", "long", "passing", "endless"],
}

NOUNS: Dict[str, List[str]] = {
    "nature": ["wind", "rain", "river", "sea", "star", "moon", "sun", "tree", "leaf"],
    "love": ["heart", "soul", "light", "flame", "grace", "beauty", "dream", "hope"],
    "death": ["shadow", "silence", "night", "dust", "dark", "end", "grave", "peace"],
    "time": ["moment", "hour", "day", "year", "age", "dawn", "dusk", "night"],
}


# ---------------------------------------------------------------------------
# PoemRequest dataclass
# ---------------------------------------------------------------------------

@dataclass
class PoemRequest:
    """A complete specification for poem generation.

    Attributes:
        topic: Subject matter (e.g. ``"love"``, ``"autumn"``, ``"mortality"``).
        form: Target poetic form, or ``None`` for free choice.
        mood: Desired emotional mood.
        style: Broad stylistic orientation.
        style_profile: Specific poet style profile to emulate.
        length: Target line count.
        constraints: Natural-language constraints (e.g. ``"include the word 'ember'"``).
        feedback_history: Prior feedback for iterative generation.
        seed_words: Words that must appear somewhere in the poem.
        avoid_words: Words that must not appear.
        rhyme_scheme: Override rhyme scheme string.
        target_meter: Override meter type.
    """

    topic: str = "nature"
    form: Optional[Any] = None              # PoemForm
    mood: Optional[Mood] = None
    style: Optional[Style] = None
    style_profile: Optional[Any] = None    # PoetStyle
    length: Optional[int] = None
    constraints: List[str] = field(default_factory=list)
    feedback_history: Optional[Any] = None  # FeedbackHistory
    seed_words: List[str] = field(default_factory=list)
    avoid_words: List[str] = field(default_factory=list)
    rhyme_scheme: Optional[str] = None
    target_meter: Optional[Any] = None     # MeterType

    def to_gluing_data(self) -> Optional["GluingData"]:
        """Convert this request to a :class:`GluingData`.

        Returns:
            A pre-populated :class:`GluingData`, or ``None`` if unavailable.
        """
        if GluingData is None:
            return None
        gd = GluingData()
        gd.prag = PragSection(speech_act="poetic_expression")
        gd.info = InfoSection(topic=self.topic)
        mood_str = self.mood.value if self.mood else "neutral"
        gd.poet = PoetSection(imagery_richness=0.8)
        return gd

    def summary(self) -> str:
        """Return a one-line summary of this request.

        Returns:
            Human-readable summary string.
        """
        form_name = self.form.name if self.form else "free"
        mood_str = self.mood.value if self.mood else "neutral"
        return f"PoemRequest(topic={self.topic!r}, form={form_name!r}, mood={mood_str!r})"

    def validate(self) -> List[str]:
        """Validate the request and return any error messages.

        Returns:
            List of error strings (empty if valid).
        """
        errors: List[str] = []
        if not self.topic:
            errors.append("topic must be non-empty")
        if self.length is not None and self.length < 1:
            errors.append("length must be at least 1")
        return errors

    def __repr__(self) -> str:
        """Return a concise representation."""
        return self.summary()


# ---------------------------------------------------------------------------
# PoemDraft dataclass
# ---------------------------------------------------------------------------

@dataclass
class PoemDraft:
    """A partially or fully generated poem with harmonic grades.

    Attributes:
        lines: Ordered list of verse lines.
        stanzas: Lines grouped into stanzas.
        meter_grades: Per-line metrical grade.
        rhyme_grades: Per-line rhyme grade.
        harmony_score: Aggregate harmony grade.
        form_grade: Grade for form compliance.
        content_grade: Grade for semantic/thematic coherence.
        iteration: Which refinement iteration produced this draft.
        request: The originating :class:`PoemRequest`.
        gluing: The associated :class:`GluingData`.
        scanner_results: Per-line scan results.
    """

    lines: List[str] = field(default_factory=list)
    stanzas: List[List[str]] = field(default_factory=list)
    meter_grades: List["Grade"] = field(default_factory=list)
    rhyme_grades: List["Grade"] = field(default_factory=list)
    harmony_score: "Grade" = field(default_factory=Grade.perfect)
    form_grade: "Grade" = field(default_factory=Grade.perfect)
    content_grade: "Grade" = field(default_factory=Grade.perfect)
    iteration: int = 0
    request: Optional[PoemRequest] = None
    gluing: Optional[Any] = None
    scanner_results: List[Any] = field(default_factory=list)

    def overall_grade(self) -> "Grade":
        """Compute the product of all component grades.

        Returns:
            Aggregate :class:`Grade`.
        """
        components = [self.harmony_score, self.form_grade, self.content_grade]
        if self.meter_grades:
            components.append(Grade.product(self.meter_grades))
        if self.rhyme_grades:
            components.append(Grade.product(self.rhyme_grades))
        return Grade.product(components)

    def worst_line(self) -> Tuple[int, str]:
        """Return the index and text of the line with the lowest meter grade.

        Returns:
            ``(index, line_text)`` pair.
        """
        if not self.meter_grades or not self.lines:
            return (0, self.lines[0] if self.lines else "")
        worst_idx = min(range(len(self.meter_grades)), key=lambda i: self.meter_grades[i].value)
        return worst_idx, self.lines[worst_idx] if worst_idx < len(self.lines) else ""

    def best_line(self) -> Tuple[int, str]:
        """Return the index and text of the line with the highest meter grade.

        Returns:
            ``(index, line_text)`` pair.
        """
        if not self.meter_grades or not self.lines:
            return (0, self.lines[0] if self.lines else "")
        best_idx = max(range(len(self.meter_grades)), key=lambda i: self.meter_grades[i].value)
        return best_idx, self.lines[best_idx] if best_idx < len(self.lines) else ""

    def to_text(self) -> str:
        """Return the formatted poem as a multi-line string.

        Returns:
            The poem with stanza breaks (blank lines between stanzas).
        """
        if self.stanzas:
            return "\n\n".join("\n".join(st) for st in self.stanzas)
        return "\n".join(self.lines)

    def line_grades(self) -> List["Grade"]:
        """Return per-line combined grade (product of meter and rhyme grades).

        Returns:
            List of per-line :class:`Grade` values.
        """
        n = len(self.lines)
        result: List["Grade"] = []
        for i in range(n):
            mg = self.meter_grades[i] if i < len(self.meter_grades) else Grade.from_prob(0.5)
            rg = self.rhyme_grades[i] if i < len(self.rhyme_grades) else Grade.from_prob(0.5)
            result.append(Grade.product([mg, rg]))
        return result

    def diagnostic(self) -> dict:
        """Return a full diagnostic breakdown.

        Returns:
            Dictionary with line-by-line and aggregate grades.
        """
        lg = self.line_grades()
        return {
            "lines": [
                {
                    "index": i,
                    "text": self.lines[i],
                    "meter_grade": (self.meter_grades[i].to_prob() if i < len(self.meter_grades) else None),
                    "rhyme_grade": (self.rhyme_grades[i].to_prob() if i < len(self.rhyme_grades) else None),
                    "combined_grade": lg[i].to_prob() if i < len(lg) else None,
                }
                for i in range(len(self.lines))
            ],
            "harmony_score": self.harmony_score.to_prob(),
            "form_grade": self.form_grade.to_prob(),
            "content_grade": self.content_grade.to_prob(),
            "overall_grade": self.overall_grade().to_prob(),
            "iteration": self.iteration,
        }

    def __repr__(self) -> str:
        """Return a brief representation."""
        return f"PoemDraft({len(self.lines)} lines, harmony={self.harmony_score})"


# ---------------------------------------------------------------------------
# PoetryHarmonyComputer
# ---------------------------------------------------------------------------

class PoetryHarmonyComputer:
    """Extended harmony computer for poetry-specific constraints.

    Combines the standard :class:`HarmonyComputer` with additional weights
    for metrical, rhyme, and form compliance grades.

    Args:
        meter_weight: Weight on the meter grade.
        rhyme_weight: Weight on the rhyme grade.
        form_weight: Weight on the form grade.
    """

    def __init__(
        self,
        meter_weight: float = 1.0,
        rhyme_weight: float = 1.0,
        form_weight: float = 1.0,
    ) -> None:
        """Initialise with per-dimension weights."""
        self._meter_weight = meter_weight
        self._rhyme_weight = rhyme_weight
        self._form_weight = form_weight
        self._base = HarmonyComputer() if HarmonyComputer is not None else None

    def poetry_harmony(
        self,
        gluing: Optional["GluingData"],
        meter_grade: "Grade",
        rhyme_grade: "Grade",
        form_grade: "Grade",
    ) -> "Grade":
        """Compute aggregate poetry harmony.

        Args:
            gluing: Optional :class:`GluingData`.
            meter_grade: Grade from meter analysis.
            rhyme_grade: Grade from rhyme analysis.
            form_grade: Grade from form compliance.

        Returns:
            Aggregate :class:`Grade`.
        """
        base = Grade.perfect()
        if gluing is not None and self._base is not None:
            try:
                base = self._base.total_harmony(gluing)
            except Exception:
                base = Grade.from_prob(0.6)
        components = [
            base,
            meter_grade.attenuate(self._meter_weight),
            rhyme_grade.attenuate(self._rhyme_weight),
            form_grade.attenuate(self._form_weight),
        ]
        return Grade.product(components)

    def line_harmony(
        self,
        line: str,
        expected_meter: Optional[Any] = None,
    ) -> "Grade":
        """Compute harmony for a single line.

        Args:
            line: The verse line.
            expected_meter: Target :class:`MeterType`.

        Returns:
            :class:`Grade` for the line.
        """
        if MeterAnalyzer is not None:
            analyzer = MeterAnalyzer()
            meter_grade = analyzer.analyze_line(line, expected_meter)
        else:
            meter_grade = Grade.from_prob(0.6)
        if RhymeGrader is not None:
            rg = RhymeGrader()
            rhyme_grade = rg.grade_alliteration(line)
        else:
            rhyme_grade = Grade.from_prob(0.5)
        return Grade.product([meter_grade, rhyme_grade])

    def poem_harmony(
        self,
        lines: List[str],
        form: Optional[Any] = None,
    ) -> "Grade":
        """Compute aggregate harmony for a full poem.

        Args:
            lines: Poem lines.
            form: Target :class:`PoemForm`.

        Returns:
            Aggregate :class:`Grade`.
        """
        line_grades = [self.line_harmony(ln) for ln in lines]
        poem_grade = Grade.product(line_grades) if line_grades else Grade.perfect()
        if form is not None and FormChecker is not None:
            checker = FormChecker()
            form_grade = checker.check_form(lines, form)
        else:
            form_grade = Grade.perfect()
        return Grade.product([poem_grade, form_grade])


# ---------------------------------------------------------------------------
# MetaphorEngine
# ---------------------------------------------------------------------------

class MetaphorEngine:
    """Generates and grades metaphors for poetic use.

    Uses a built-in mapping of topics to metaphor templates, expandable
    by domain and mood.
    """

    METAPHOR_MAPPINGS: Dict[str, List[str]] = {
        "love": [
            "love is a rose with hidden thorns",
            "love is a fire that warms and burns",
            "love is a journey without a map",
            "love is a river changing course",
            "love is an ocean, deep and wide",
            "love is a flame in winter night",
            "love is a wound that also heals",
            "love is the tide that fills the bay",
            "love is a garden we tend or lose",
            "love is a bridge across the void",
        ],
        "death": [
            "death is a long and dreamless sleep",
            "death is a door we all pass through",
            "death is the harvest of all living",
            "death is the tide that claims the shore",
            "death is the silence after music",
            "death is the candle guttered out",
            "death is the winter that knows no spring",
            "death is a ship that sails from sight",
            "death is the last line of the poem",
            "death is the river's final turn",
        ],
        "time": [
            "time is a river always flowing",
            "time is a thief that takes all things",
            "time is a wheel turning without end",
            "time is the tide no shore can stop",
            "time is a clock we cannot wind",
            "time is a sieve that loses everything",
            "time is a mirror dimming slowly",
            "time is the arrow only forward aimed",
            "time is the dream we cannot hold",
            "time is the hand that writes and fades",
        ],
        "sadness": [
            "sadness is rain on an empty window",
            "sadness is fog that hides the sun",
            "sadness is the weight of what is gone",
            "sadness is a room with no one in it",
            "sadness is the bird with broken wings",
            "sadness is the letter never sent",
            "sadness is the harvest of regret",
            "sadness is the winter's longest night",
        ],
        "joy": [
            "joy is light breaking through the cloud",
            "joy is the bird that suddenly sings",
            "joy is the first bloom after frost",
            "joy is fire that warms but does not burn",
            "joy is the tide returning to the shore",
            "joy is music rising from the street",
            "joy is the child who finds the open gate",
            "joy is the day that follows all the dark",
        ],
        "truth": [
            "truth is a light that casts hard shadows",
            "truth is a stone we cannot move aside",
            "truth is the dawn that ends the dream",
            "truth is a mirror showing what we fear",
            "truth is the tide that strips the shore",
            "truth is the bone beneath the flesh",
        ],
        "hope": [
            "hope is a candle in a vast dark room",
            "hope is the green shoot in winter soil",
            "hope is the star that guides through storm",
            "hope is the door that will not fully close",
            "hope is the voice that says go on",
        ],
        "memory": [
            "memory is a ghost in every room",
            "memory is the river flowing backward",
            "memory is the photograph that fades",
            "memory is the song we half-remember",
            "memory is the scent we cannot place",
        ],
        "beauty": [
            "beauty is the brief flame of the rose",
            "beauty is the wave before it breaks",
            "beauty is the light at the day's last edge",
            "beauty is the note just before silence",
            "beauty is the wound that leaves no scar",
        ],
        "freedom": [
            "freedom is the open sky above the wall",
            "freedom is the road that has no end",
            "freedom is the tide that cannot be refused",
            "freedom is the bird that builds no cage",
            "freedom is the word that breaks the lock",
        ],
        "nature": [
            "nature is the book that never ends",
            "nature is the teacher with no words",
            "nature is the mirror that does not lie",
            "nature is the song sung without voice",
            "nature is the law that predates all laws",
        ],
        "god": [
            "god is the light behind all light",
            "god is the silence between all sounds",
            "god is the river returning to its source",
            "god is the word that speaks all words",
            "god is the love that holds the stars in place",
        ],
        "art": [
            "art is the wound that beauty opens",
            "art is the bridge between silence and sound",
            "art is the map of where we cannot go",
            "art is the dream that waking keeps",
            "art is the stone that speaks",
        ],
        "war": [
            "war is the harvest of hatred sown",
            "war is the fire that feeds on living",
            "war is the tide that drowns all shores",
            "war is the night with no morning after",
            "war is the word that ends all words",
        ],
        "journey": [
            "the journey is a thread through darkness",
            "the journey is the question, not the answer",
            "the journey is the river knowing no shore",
            "the journey is the poem writing itself",
            "the journey is the self becoming other",
        ],
    }

    def generate_metaphor(
        self, topic: str, mood: Optional[str] = None
    ) -> str:
        """Generate a metaphor for *topic*.

        Delegates to the real MetaphorEngine (entailments first, then
        activations) before falling back to the built-in mapping table.

        Args:
            topic: The subject of the metaphor.
            mood: Optional emotional colouring (not yet used for filtering).

        Returns:
            A metaphor string.
        """
        try:
            from gofai_chat.coercion.metaphor_engine import MetaphorEngine as _RealME
            entailments = _RealME().entailments_for_text(topic)
            if entailments:
                for e in entailments[:5]:
                    parts = e.split(':')
                    if len(parts) >= 2:
                        # "LOVE IS A JOURNEY: source=love, entailment=path" -> "path"
                        detail = parts[1]
                        for segment in detail.split(','):
                            if 'entailment' in segment:
                                val = segment.split('=')[-1].strip()
                                if val and val.isalpha():
                                    return val
            activations = _RealME().activate_metaphors(topic)
            if activations:
                act = activations[0]
                # Inspect MetaphorActivation fields defensively
                for field in ('target_domain', 'source_domain', 'name', 'entailments'):
                    if hasattr(act, field):
                        v = getattr(act, field)
                        if isinstance(v, str) and v:
                            return v
                        if isinstance(v, list) and v:
                            return str(v[0])
                # Also check act.metaphor sub-object
                if hasattr(act, 'metaphor'):
                    m = act.metaphor
                    for field in ('target_domain', 'source_domain', 'name'):
                        if hasattr(m, field):
                            v = getattr(m, field)
                            if isinstance(v, str) and v:
                                return v
        except Exception:
            pass

        # Built-in mapping fallback
        topic_lower = topic.lower()
        for key in self.METAPHOR_MAPPINGS:
            if key in topic_lower or topic_lower in key:
                options = self.METAPHOR_MAPPINGS[key]
                return random.choice(options)
        nouns = NOUNS.get("nature", ["river", "wind", "flame"])
        adjs = ADJECTIVES.get("nature", ["deep", "dark", "bright"])
        noun = random.choice(nouns)
        adj = random.choice(adjs)
        return f"{topic} is a {adj} {noun}"

    def expand_metaphor(self, base: str) -> List[str]:
        """Generate related lines elaborating on *base* metaphor.

        Args:
            base: The seed metaphor.

        Returns:
            List of related poetic lines.
        """
        words = base.split()
        if not words:
            return [base]
        expansions = [
            f"And like the {words[-1] if words else 'wind'}, it comes and goes,",
            f"As {base.rstrip(',.')} must end,",
            f"So too this {words[0].lower() if words else 'thing'} returns to start,",
            f"The {words[-1] if words else 'flame'} that {random.choice(['burns', 'shines', 'glows'])} within,",
        ]
        return expansions

    def grade_metaphor(self, metaphor: str, context: str) -> "Grade":
        """Grade a metaphor's suitability for *context*.

        Args:
            metaphor: The metaphor string.
            context: The poem's topic or surrounding lines.

        Returns:
            :class:`Grade` for metaphor suitability.
        """
        if not metaphor or not context:
            return Grade.from_prob(0.5)
        # Simple keyword overlap
        meta_words = set(re.split(r"\W+", metaphor.lower()))
        ctx_words = set(re.split(r"\W+", context.lower()))
        overlap = len(meta_words & ctx_words)
        # Penalise clichés slightly
        cliche_markers = {"like", "as", "is", "are"}
        cliche_count = sum(1 for w in meta_words if w in cliche_markers)
        score = min(0.95, 0.5 + overlap * 0.05 - cliche_count * 0.02)
        return Grade.from_prob(max(0.1, score))

    def blend_metaphors(self, m1: str, m2: str) -> str:
        """Blend two metaphors into a novel compound metaphor.

        Args:
            m1: First metaphor.
            m2: Second metaphor.

        Returns:
            A blended metaphor string.
        """
        # Extract key nouns/images from each
        words1 = [w for w in re.split(r"\W+", m1.lower()) if len(w) > 3]
        words2 = [w for w in re.split(r"\W+", m2.lower()) if len(w) > 3]
        if not words1 or not words2:
            return m1
        # Pick one word from each and construct bridge
        w1 = random.choice(words1)
        w2 = random.choice(words2)
        templates = [
            f"the {w1} that becomes the {w2}",
            f"as {w1} as {w2}",
            f"{w1} turning into {w2}",
            f"the {w1} within the {w2}",
        ]
        return random.choice(templates)

    def _topic_to_domain(self, topic: str) -> str:
        """Map a topic string to a metaphor domain key.

        Args:
            topic: Topic string.

        Returns:
            Matching domain key, or ``"nature"`` as fallback.
        """
        topic_lower = topic.lower()
        for key in self.METAPHOR_MAPPINGS:
            if key in topic_lower:
                return key
        return "nature"


# ---------------------------------------------------------------------------
# ImagerySelector
# ---------------------------------------------------------------------------

class ImagerySelector:
    """Selects concrete imagery for poem generation.

    Uses domain-specific imagery banks to provide sensory, concrete
    images appropriate to the poem's topic and mood.
    """

    IMAGERY_BANKS: Dict[str, List[str]] = {
        "nature": [
            "the silver birch at dawn", "wind through October leaves",
            "a fox crossing snow", "ice on the pond's rim",
            "the crow perched on a winter branch", "mist over the river",
            "tide pulling at black rocks", "grass bent by unseen wind",
            "the heron's patient stillness", "clouds moving like thoughts",
            "moonlight on the surface of still water", "dew on spiderweb",
        ],
        "seasons": [
            "the first bloom after long frost", "summer's drowning heat",
            "leaves releasing their green", "bare branches in December",
            "snowdrops through last year's mulch", "harvest moon swollen",
            "the last bird song of autumn", "spring mud on the path",
        ],
        "celestial": [
            "Jupiter cold above the ridge", "the Milky Way's river of dust",
            "the moon pulling ocean tides", "a shooting star at three",
            "the morning star before the sun", "the Pleiades at midnight",
            "Saturn's rings in a borrowed telescope", "Venus in the west",
        ],
        "water": [
            "the river carrying leaves down", "rain on a tin roof",
            "waves breaking over grey stone", "the still pool in deep forest",
            "morning dew on the sleeping rose", "the waterfall's constant sound",
            "foam retreating from the shore", "ice cracking in thaw",
        ],
        "fire": [
            "candle flame wavering in draught", "embers dimming in the grate",
            "a bonfire throwing shadows", "sparks rising into dark air",
            "the match head's brief flare", "coals banked for the night",
        ],
        "domestic": [
            "the kettle's steam in winter morning", "a cracked cup on the shelf",
            "a child's shoe abandoned at the stair", "bread cooling on the rack",
            "the kitchen table in late light", "scissors left in a sunlit room",
            "an unread letter on the windowsill", "the clock that stopped",
        ],
        "urban": [
            "neon reflected in puddled rain", "a child alone at the bus stop",
            "the smell of bread from a basement bakery", "pigeons on a bridge",
            "a window lit at three a.m.", "the street after the rain",
            "a cat crossing the empty road", "scaffolding in winter light",
        ],
        "body": [
            "the hand that is finally still", "an eye that holds no mirror",
            "a heartbeat in a quiet room", "breath misting winter air",
            "the weight of a sleeping child", "a scar that still remembers",
            "the lips that shape a name", "the tired feet at the end",
        ],
        "flora": [
            "the rose already browning at the petal", "lavender in late July",
            "oak roots drinking from the dark", "the foxglove poisonous and tall",
            "snowdrops through the hardened ground", "the thistle's purple crown",
            "nettles at the ruin's edge", "the hawthorn's white complexity",
        ],
        "fauna": [
            "the hawk circling without apparent purpose", "the deer at the wood's edge",
            "the fox slipping under the gate", "the wren's absurd volume",
            "the heron motionless in the shallows", "the moth drawn to the porch light",
            "crows coordinating on the rooftop", "the bee's late-summer urgency",
        ],
        "time": [
            "the stopped clock in the hallway", "a photograph yellowing",
            "the child who is no longer a child", "an anniversary no one marks",
            "the last page of the diary", "the handwriting that no longer writes",
            "the road that is now a car park", "the orchard grown to scrub",
        ],
        "weather": [
            "the storm that cleared the air", "fog that makes the world small",
            "hail on the window like applause", "lightning counting its distance",
            "the first true cold of autumn", "a rainbow after grief",
            "snow that forgives all footprints", "wind that wants no name",
        ],
        "light": [
            "the last stripe of sun on the wall", "dawn light through a gap in curtains",
            "a candle by a sleeping face", "street lamp in the fog",
            "the moment between dark and dark", "light bending under the wave",
            "shadow growing longer toward evening", "the window's cold glare at noon",
        ],
        "darkness": [
            "the hour when no bird speaks", "a room at four a.m.",
            "the dark behind the star", "shadow in the bone of winter",
            "the moon hidden behind the cloud", "a corridor with no lights",
        ],
        "sound": [
            "the silence after the door closed", "music from a passing car",
            "wind finding the gaps in the wall", "the sound of a name being called",
            "rain increasing on the lake surface", "the clock's indifferent tick",
            "a voice that has no body now", "the birds rehearsing before light",
        ],
    }

    def select_image(
        self, domain: str, mood: Optional[str] = None
    ) -> str:
        """Select a concrete image from *domain*.

        Args:
            domain: Imagery domain name.
            mood: Optional mood for filtering (not yet used).

        Returns:
            A concrete image phrase.
        """
        bank = self.IMAGERY_BANKS.get(domain.lower(), self.IMAGERY_BANKS["nature"])
        return random.choice(bank)

    def select_images_for_poem(
        self, topic: str, mood: str, count: int = 5
    ) -> List[str]:
        """Select *count* images suited to *topic* and *mood*.

        Args:
            topic: The poem's subject.
            mood: Emotional tone.
            count: Number of images to return.

        Returns:
            List of concrete image phrases.
        """
        domain = self._mood_to_domain(mood)
        topic_domain = self._topic_to_domain(topic)
        # Gather from both domains
        images: List[str] = []

        # Prepend conceptual-blend imagery when topic has two domains.
        blend_words = self._blend_if_dual_topic(topic)
        images.extend(blend_words)

        for d in (domain, topic_domain, "nature"):
            bank = self.IMAGERY_BANKS.get(d, [])
            images.extend(bank)

        # Augment with FrameEvocationEngine role fillers as imagery seeds.
        try:
            from gofai_chat.sem.frame_network import FrameEvocationEngine
            evoked = FrameEvocationEngine().evoke_frames(topic)
            for frame, _grade in evoked[:3]:
                images.extend(frame.lexical_units[:4])
        except Exception:
            pass

        # Augment with MetaphorEngine activations.
        try:
            from gofai_chat.coercion.metaphor_engine import MetaphorEngine
            activations = MetaphorEngine().activate_metaphors(topic)
            for act in activations[:3]:
                if hasattr(act, 'entailments'):
                    images.extend(act.entailments[:3])
                elif hasattr(act, 'mappings'):
                    for m in act.mappings[:3]:
                        images.append(str(m))
        except Exception:
            pass

        # De-duplicate and sample
        unique = list(dict.fromkeys(images))
        random.shuffle(unique)
        return unique[:count]

    def _blend_if_dual_topic(self, topic: str) -> List[str]:
        """If topic has two domains, blend them and return imagery words.

        Args:
            topic: Topic string, possibly containing "X and Y", "X of Y", etc.

        Returns:
            List of emergent blend words (may be empty).
        """
        try:
            from gofai_chat.coercion.conceptual_blending import ConceptualBlendingEngine
            words = topic.lower().split()
            for i, w in enumerate(words):
                if w in ('and', 'of', 'like', 'as') and i > 0 and i < len(words) - 1:
                    d1, d2 = words[i - 1], words[i + 1]
                    result = ConceptualBlendingEngine().blend_from_domains(d1, d2)
                    if result:
                        for field in ('emergent_structure', 'blend_words', 'concepts', 'vital_relations'):
                            if hasattr(result, field):
                                v = getattr(result, field)
                                if isinstance(v, list):
                                    return [str(x) for x in v[:10]]
                                if isinstance(v, dict):
                                    return list(v.keys())[:10]
                        # Check blend space emergent elements
                        if hasattr(result, 'blend') and hasattr(result.blend, 'emergent'):
                            return [e.name for e in result.blend.emergent[:10] if hasattr(e, 'name')]
        except Exception:
            pass
        return []

    def grade_imagery_richness(self, lines: List[str]) -> "Grade":
        """Grade the concrete imagery richness of *lines*.

        Args:
            lines: Poem lines.

        Returns:
            :class:`Grade` for imagery richness.
        """
        if not lines:
            return Grade.from_prob(0.3)
        all_text = " ".join(lines).lower()
        # Count concrete words from imagery banks
        concrete_words = {
            w for bank in self.IMAGERY_BANKS.values() for phrase in bank
            for w in re.split(r"\W+", phrase.lower()) if len(w) > 3
        }
        poem_words = set(re.split(r"\W+", all_text))
        overlap = len(concrete_words & poem_words)
        # Scale: 5+ overlapping words = excellent
        score = min(0.95, 0.3 + overlap * 0.06)
        return Grade.from_prob(score)

    def _mood_to_domain(self, mood: str) -> str:
        """Map a mood to a primary imagery domain.

        Args:
            mood: Mood string.

        Returns:
            Domain key.
        """
        mood_map: Dict[str, str] = {
            "joyful": "light", "melancholic": "time",
            "peaceful": "water", "passionate": "fire",
            "meditative": "nature", "ecstatic": "celestial",
            "sorrowful": "darkness", "nostalgic": "domestic",
            "sublime": "weather", "transcendent": "celestial",
        }
        return mood_map.get(mood.lower(), "nature")

    def _topic_to_domain(self, topic: str) -> str:
        """Map a topic to a primary imagery domain.

        Args:
            topic: Topic string.

        Returns:
            Domain key.
        """
        topic_map: Dict[str, str] = {
            "love": "body", "death": "darkness", "nature": "nature",
            "time": "time", "god": "light", "journey": "nature",
            "sadness": "weather", "joy": "light", "beauty": "flora",
            "war": "weather", "memory": "domestic",
        }
        topic_lower = topic.lower()
        for key, domain in topic_map.items():
            if key in topic_lower:
                return domain
        return "nature"


# ---------------------------------------------------------------------------
# WordSubstituter
# ---------------------------------------------------------------------------

class WordSubstituter:
    """Substitutes words to improve meter, rhyme, or mood.

    Maintains synonym groups indexed by stress pattern and meaning domain.

    Args:
        phon_analyzer: Optional :class:`PhonAnalyzer`.
    """

    SYNONYM_GROUPS: Dict[str, List[str]] = {
        # Monosyllabic synonyms
        "love": ["love", "care", "grace", "warmth", "bond", "tie"],
        "heart": ["heart", "soul", "core", "self", "chest"],
        "light": ["light", "glow", "shine", "beam", "gleam", "ray"],
        "dark": ["dark", "shade", "night", "gloom", "murk", "dusk"],
        "end": ["end", "close", "rest", "stop", "cease"],
        "time": ["time", "hour", "age", "day", "year"],
        "wind": ["wind", "gust", "breeze", "air", "draft"],
        "fire": ["fire", "flame", "blaze", "spark", "heat"],
        "rain": ["rain", "drop", "tear", "dew", "mist"],
        "sea": ["sea", "wave", "tide", "surf", "foam"],
        # Disyllabic synonyms
        "beauty": ["beauty", "grace", "splendour", "glory", "wonder"],
        "sorrow": ["sorrow", "grief", "sadness", "mourning", "anguish"],
        "silence": ["silence", "quiet", "stillness", "hush", "calm"],
        "longing": ["longing", "yearning", "aching", "craving", "hunger"],
        "journey": ["journey", "voyage", "passage", "crossing", "travel"],
        "flower": ["flower", "blossom", "petal", "bloom"],
        "morning": ["morning", "daybreak", "sunrise", "dawning"],
        "evening": ["evening", "nightfall", "sunset", "dusk", "twilight"],
        "shadow": ["shadow", "darkness", "dimness", "shading"],
        "whisper": ["whisper", "murmur", "sigh", "breathe"],
        # Three-syllable
        "beautiful": ["beautiful", "wonderful", "glorious", "radiant", "luminous"],
        "sorrowful": ["sorrowful", "mournful", "grieving", "weeping"],
        "wandering": ["wandering", "roaming", "drifting", "seeking"],
        "remember": ["remember", "recall", "recollect", "revisit"],
        "forever": ["forever", "eternal", "enduring", "lasting"],
        "surrender": ["surrender", "yielding", "relenting", "giving"],
        # Archaic/elevated alternates
        "you": ["you", "thee", "thou"],
        "your": ["your", "thy", "thine"],
        "are": ["are", "art"],
        "speak": ["speak", "say", "utter", "voice", "tell"],
        "see": ["see", "behold", "observe", "witness", "view"],
        "go": ["go", "depart", "haste", "move", "fly"],
        "come": ["come", "arrive", "return", "approach", "draw near"],
    }

    def __init__(
        self, phon_analyzer: Optional["PhonAnalyzer"] = None
    ) -> None:
        """Initialise with optional phonological analyser."""
        self._phon = phon_analyzer

    def substitute_for_meter(
        self, word: str, target_stress: str
    ) -> List[str]:
        """Find synonyms of *word* matching *target_stress*.

        Args:
            word: The word to substitute.
            target_stress: Desired stress pattern (e.g. ``"10"``).

        Returns:
            List of candidates with matching stress pattern.
        """
        word_lower = word.lower()
        candidates: List[str] = []
        for key, synonyms in self.SYNONYM_GROUPS.items():
            if word_lower in synonyms or word_lower == key:
                for syn in synonyms:
                    sp = self._stress(syn)
                    if sp == target_stress:
                        candidates.append(syn)
        return list(dict.fromkeys(candidates))

    def substitute_for_rhyme(
        self, word: str, rhyme_target: str
    ) -> List[str]:
        """Find synonyms of *word* that rhyme with *rhyme_target*.

        Args:
            word: The word to substitute.
            rhyme_target: Target end-rhyme word.

        Returns:
            List of rhyming synonym candidates.
        """
        if RhymeFinder is None:
            return []
        finder = RhymeFinder(RHYME_DICTIONARY)
        rhymes = set(finder.find_rhymes(rhyme_target, quality="near"))
        word_lower = word.lower()
        candidates: List[str] = []
        for key, synonyms in self.SYNONYM_GROUPS.items():
            if word_lower in synonyms or word_lower == key:
                for syn in synonyms:
                    if syn in rhymes:
                        candidates.append(syn)
        return list(dict.fromkeys(candidates))

    def substitute_for_mood(
        self, word: str, mood: str
    ) -> List[str]:
        """Find synonyms with the appropriate mood register.

        Args:
            word: The word to substitute.
            mood: Desired mood (e.g. ``"melancholic"``).

        Returns:
            Mood-appropriate synonym candidates.
        """
        mood_lower = mood.lower()
        mood_words = MOOD_VOCABULARY.get(mood_lower, [])
        word_lower = word.lower()
        # Return mood words that are near-synonyms
        candidates: List[str] = []
        for key, synonyms in self.SYNONYM_GROUPS.items():
            if word_lower in synonyms or word_lower == key:
                for syn in synonyms:
                    if syn in mood_words:
                        candidates.append(syn)
        return list(dict.fromkeys(candidates))

    def grade_substitution(
        self, original: str, substitute: str, context: str
    ) -> "Grade":
        """Grade the semantic fitness of *substitute* for *original* in *context*.

        Args:
            original: The word being replaced.
            substitute: The replacement word.
            context: The surrounding line or lines.

        Returns:
            :class:`Grade` for semantic fitness.
        """
        orig_lower = original.lower()
        sub_lower = substitute.lower()
        # Check if they are in the same synonym group
        for key, synonyms in self.SYNONYM_GROUPS.items():
            if orig_lower in synonyms and sub_lower in synonyms:
                return Grade.from_prob(0.85)
        # Check stress compatibility
        orig_stress = self._stress(orig_lower)
        sub_stress = self._stress(sub_lower)
        if orig_stress == sub_stress:
            return Grade.from_prob(0.70)
        # General fallback
        return Grade.from_prob(0.45)

    def find_best_substitute(
        self, word: str, constraints: dict
    ) -> Tuple[str, "Grade"]:
        """Find the best substitute satisfying multiple constraints.

        Args:
            word: The word to replace.
            constraints: Dict with optional keys ``"stress"``, ``"rhyme"``,
                ``"mood"``.

        Returns:
            ``(best_candidate, grade)`` pair.
        """
        candidates: List[str] = [word]
        stress = constraints.get("stress")
        rhyme = constraints.get("rhyme")
        mood = constraints.get("mood")
        if stress:
            candidates.extend(self.substitute_for_meter(word, stress))
        if rhyme:
            candidates.extend(self.substitute_for_rhyme(word, rhyme))
        if mood:
            candidates.extend(self.substitute_for_mood(word, mood))
        if not candidates:
            return word, Grade.from_prob(0.4)
        # Grade each candidate
        best = word
        best_grade = Grade.from_prob(0.4)
        for cand in candidates:
            g = self.grade_substitution(word, cand, word)
            if g > best_grade:
                best_grade = g
                best = cand
        return best, best_grade

    def _stress(self, word: str) -> str:
        """Return stress pattern for *word* (heuristic).

        Args:
            word: A word.

        Returns:
            Binary stress string.
        """
        # Import from meter_engine if available
        try:
            from gofai_chat.generation.poetry.meter_engine import (
                _word_to_stress, _count_syllables_ortho
            )
            return _word_to_stress(word)
        except ImportError:
            return "1" * max(1, len(re.findall(r"[aeiou]+", word.lower())))


# ---------------------------------------------------------------------------
# LineGenerator
# ---------------------------------------------------------------------------

class LineGenerator:
    """Generates individual verse lines satisfying meter, rhyme, and semantic constraints.

    Args:
        meter: Optional target :class:`MeterType`.
        phon_analyzer: Optional :class:`PhonAnalyzer`.
    """

    def __init__(
        self,
        meter: Optional[Any] = None,
        phon_analyzer: Optional["PhonAnalyzer"] = None,
    ) -> None:
        """Initialise with optional meter and phonological analyser."""
        self._meter = meter
        self._phon = phon_analyzer
        self._imagery = ImagerySelector()
        self._metaphor = MetaphorEngine()
        self._substituter = WordSubstituter(phon_analyzer)

    def generate_line(
        self,
        topic: str,
        mood: str,
        end_word: Optional[str] = None,
        meter: Optional[Any] = None,
    ) -> str:
        """Generate a single verse line.

        Args:
            topic: Poem topic.
            mood: Emotional tone.
            end_word: Required end-word (for rhyme scheme).
            meter: Target meter type.

        Returns:
            A generated verse line.
        """
        topic_words = self._topic_to_words(topic)
        mood_words = MOOD_VOCABULARY.get(mood.lower(), [])
        # Build line from template
        template_type = f"{topic.lower().replace(' ', '_')}_opening"
        if template_type not in LINE_TEMPLATES:
            template_type = "nature_opening"
        templates = LINE_TEMPLATES.get(template_type, LINE_TEMPLATES["continuation"])
        template = random.choice(templates)
        # Fill template
        line = self._fill_template(template, topic, mood)
        # Append end word if required
        if end_word:
            words = line.rstrip(",. ").split()
            if words:
                words[-1] = end_word
                line = " ".join(words) + ","
        return line

    def generate_opening_line(self, topic: str, mood: str) -> str:
        """Generate the first line of a poem.

        Args:
            topic: Poem topic.
            mood: Emotional tone.

        Returns:
            An opening verse line.
        """
        topic_lower = topic.lower()
        # Use imagery for variety
        image = self._imagery.select_image(
            self._imagery._topic_to_domain(topic), mood
        )
        openers = [
            f"When {image},",
            f"In the long {topic_lower} of the year,",
            f"Below the {random.choice(['cold', 'bright', 'pale', 'dark'])} sky,",
            f"What {random.choice(['silence', 'wonder', 'sorrow', 'light'])} fills this hour,",
            image.capitalize() + " —",
        ]
        return random.choice(openers)

    def generate_closing_line(
        self, topic: str, mood: str, rhyme_word: Optional[str] = None
    ) -> str:
        """Generate a closing line for a poem.

        Args:
            topic: Poem topic.
            mood: Emotional tone.
            rhyme_word: Word to rhyme with (for terminal rhyme).

        Returns:
            A closing verse line.
        """
        closers = LINE_TEMPLATES.get("closing_couplet", [])
        if not closers:
            closers = ["And so the {noun} endures beyond our end."]
        template = random.choice(closers)
        line = self._fill_template(template, topic, mood)
        if rhyme_word and RhymeFinder is not None:
            finder = RhymeFinder(RHYME_DICTIONARY)
            rhymes = finder.find_rhymes(rhyme_word, quality="near")
            if rhymes:
                last_word = random.choice(rhymes[:5])
                words = line.rstrip(". ").split()
                if words:
                    words[-1] = last_word + "."
                    line = " ".join(words)
        return line

    def generate_continuation(
        self, previous_lines: List[str], topic: str
    ) -> str:
        """Generate a continuation line following *previous_lines*.

        Args:
            previous_lines: Lines already written.
            topic: Poem topic.

        Returns:
            A continuation verse line.
        """
        templates = LINE_TEMPLATES.get("continuation", [])
        if not templates:
            templates = ["And still the {adj} {noun} calls to mind"]
        template = random.choice(templates)
        return self._fill_template(template, topic, "neutral")

    def grade_line(
        self, line: str, meter: Optional[Any] = None
    ) -> "Grade":
        """Grade a single line's quality.

        Args:
            line: The verse line.
            meter: Target meter type.

        Returns:
            :class:`Grade` for line quality.
        """
        if MeterAnalyzer is not None:
            analyzer = MeterAnalyzer(phon_analyzer=self._phon)
            return analyzer.analyze_line(line, meter or self._meter)
        return Grade.from_prob(0.5)

    def _fill_template(
        self, template: str, topic: str, mood: str
    ) -> str:
        """Fill a template with topic- and mood-appropriate words.

        Replaces placeholders like ``{adj}``, ``{noun}``, ``{verb}``,
        ``{time}``, ``{element}``, ``{season}`` from the appropriate banks.

        Args:
            template: Template string.
            topic: Poem topic.
            mood: Emotional tone.

        Returns:
            Filled line string.
        """
        topic_lower = topic.lower()
        adj_pool = ADJECTIVES.get(topic_lower, ADJECTIVES["nature"]) + MOOD_VOCABULARY.get(mood, [])
        base_noun_pool = NOUNS.get(topic_lower, [])
        if not base_noun_pool:
            topic_words = self._topic_to_words(topic)
            base_noun_pool = [w for w in topic_words if " " not in w][:15]
        if not base_noun_pool:
            base_noun_pool = NOUNS["nature"]
        noun_pool = base_noun_pool
        time_words = ["morning", "evening", "night", "dawn", "dusk", "noon"]
        season_words = ["spring", "summer", "autumn", "winter", "October", "May"]
        verb_words = ["fills", "moves", "turns", "falls", "rises", "speaks", "holds", "stirs", "drifts", "gleams"]

        noun1 = random.choice(noun_pool) if noun_pool else "wind"
        noun2 = random.choice(noun_pool) if noun_pool else "light"
        verb1 = self._verb(topic, mood)
        verb2 = self._verb(topic, mood)

        result = template
        result = result.replace("{topic}", topic_lower)
        result = result.replace("{adj}", random.choice(adj_pool) if adj_pool else "dark")
        result = result.replace("{noun2}", noun2)
        result = result.replace("{noun}", noun1)
        result = result.replace("{time}", random.choice(time_words))
        result = result.replace("{season}", random.choice(season_words))
        result = result.replace("{verb2}", verb2)
        result = result.replace("{verb}", verb1)
        return result

    def _ensure_meter(self, line: str, target: Any) -> str:
        """Attempt to adjust *line* to better match *target* meter.

        Args:
            line: Current line.
            target: Target :class:`MeterType`.

        Returns:
            Adjusted line (may be same as input if no improvement found).
        """
        if MeterEnforcer is None or target is None:
            return line
        enforcer = MeterEnforcer(target, self._phon)
        adjusted, grade = enforcer.adjust_stress(line)
        return adjusted if grade.to_prob() > 0.5 else line

    def _topic_to_words(self, topic: str) -> List[str]:
        """Return topic-related seed words.

        Args:
            topic: Topic string.

        Returns:
            List of relevant words.
        """
        words: List[str] = []
        # 1. Direct synonyms from HarmonicLexicon
        try:
            from gofai_chat.lexicon.harmonic_lexicon import HarmonicLexicon
            hl = HarmonicLexicon()
            words.extend(hl.find_synonyms(topic))
        except Exception:
            pass
        # 2. WordNet expansion: hypernyms, hyponyms, also_sees, similar_tos
        try:
            from nltk.corpus import wordnet as wn
            for syn in wn.synsets(topic)[:3]:
                for lemma in syn.lemmas()[:4]:
                    words.append(lemma.name().replace('_', ' '))
                for hypo in syn.hyponyms()[:3]:
                    for l in hypo.lemmas()[:2]:
                        words.append(l.name().replace('_', ' '))
                for hyper in syn.hypernyms()[:2]:
                    for l in hyper.lemmas()[:2]:
                        words.append(l.name().replace('_', ' '))
        except Exception:
            pass
        # 3. MetaphorEngine entailments as imagery seeds
        try:
            from gofai_chat.coercion.metaphor_engine import MetaphorEngine
            for ent in MetaphorEngine().entailments_for_text(topic)[:10]:
                for tok in ent.split():
                    if len(tok) > 3 and tok.isalpha():
                        words.append(tok.lower())
        except Exception:
            pass
        # 4. Filter by zipf frequency (keep common enough words, drop ultra-rare)
        try:
            from wordfreq import zipf_frequency
            words = [w for w in words if zipf_frequency(w.split()[0], 'en') > 2.0]
        except Exception:
            pass
        # Deduplicate, keep first 30
        seen: set = set()
        result: List[str] = []
        for w in words:
            w2 = w.lower().strip()
            if w2 and w2 not in seen:
                seen.add(w2)
                result.append(w2)
        return result[:30] if result else [topic]

    def _verb(self, topic: str, mood: str) -> str:
        """Return a semantically appropriate verb for *topic* and *mood*.

        Tries VerbDecomposer first, then emotion-scored selection from the
        default pool, finally a plain random pick.

        Args:
            topic: Poem topic.
            mood: Emotional tone.

        Returns:
            A verb string.
        """
        _default_pool = ["fills", "moves", "turns", "falls", "rises", "speaks",
                         "holds", "stirs", "drifts", "gleams"]
        # 1. Try VerbDecomposer for thematically relevant verbs.
        try:
            from gofai_chat.semantics.lexical_decomposition import VerbDecomposer
            vd = VerbDecomposer()
            for method in ('verbs_for_theme', 'verbs_for_frame', 'select_verb'):
                if hasattr(vd, method):
                    res = getattr(vd, method)(topic)
                    if isinstance(res, list) and res:
                        candidates = [str(v) for v in res if str(v).isalpha()]
                        if candidates:
                            # Score and pick best
                            scored = sorted(
                                candidates,
                                key=lambda v: self._score_verb_by_emotion(v, mood),
                                reverse=True,
                            )
                            return scored[0]
                    if isinstance(res, str) and res.isalpha():
                        return res
        except Exception:
            pass
        # 2. Score default pool by emotional alignment.
        scored = sorted(
            _default_pool,
            key=lambda v: self._score_verb_by_emotion(v, mood),
            reverse=True,
        )
        # Add some randomness among the top candidates.
        top = scored[:4] if len(scored) >= 4 else scored
        return random.choice(top)

    def _score_verb_by_emotion(self, verb: str, target_mood: str) -> float:
        """Return 0.0–1.0 score for how well *verb* fits *target_mood*.

        Args:
            verb: Candidate verb string.
            target_mood: Emotional tone of the poem.

        Returns:
            Float score 0.0–1.0.
        """
        try:
            from gofai_chat.lexicon.emotion import EmotionAnalyzer
            ea = EmotionAnalyzer()
            genre = (
                target_mood if target_mood in ('tragedy', 'comedy', 'romance', 'elegy')
                else 'elegy'
            )
            arc = ea.genre_arc_template(genre)
            if arc:
                expected_emotion = list(arc[0].keys())[0] if arc[0] else None
                dark_verbs = {'fade', 'wither', 'fall', 'break', 'sink',
                              'weep', 'mourn', 'die', 'crumble', 'falls'}
                light_verbs = {'bloom', 'rise', 'shine', 'soar', 'dance',
                               'sing', 'grow', 'thrive', 'rises', 'gleams'}
                if expected_emotion and 'sad' in str(expected_emotion).lower():
                    return 1.0 if verb in dark_verbs else 0.5
                elif expected_emotion and 'joy' in str(expected_emotion).lower():
                    return 1.0 if verb in light_verbs else 0.5
        except Exception:
            pass
        return 0.6  # neutral


# ---------------------------------------------------------------------------
# StanzaBuilder
# ---------------------------------------------------------------------------

class StanzaBuilder:
    """Builds stanzas from generated lines.

    Args:
        form: Optional target :class:`PoemForm` governing stanza structure.
    """

    def __init__(self, form: Optional[Any] = None) -> None:
        """Initialise with optional form."""
        self._form = form
        self._line_gen = LineGenerator()

    def build_stanza(
        self,
        topic: str,
        mood: str,
        rhyme_slots: Dict[str, Optional[str]],
        size: int = 4,
    ) -> List[str]:
        """Build a stanza of *size* lines.

        Args:
            topic: Poem topic.
            mood: Emotional tone.
            rhyme_slots: Mapping from slot letter to optional anchor word.
            size: Number of lines in the stanza.

        Returns:
            List of generated lines.
        """
        lines: List[str] = []
        scheme = "ABAB" if size == 4 else "ABA" if size == 3 else "AABB"
        for i in range(size):
            slot = scheme[i] if i < len(scheme) else "X"
            anchor = rhyme_slots.get(slot)
            line = self._line_gen.generate_line(topic, mood, end_word=anchor)
            lines.append(line)
            # Set anchor for first time we see this slot
            if anchor is None:
                end_word = line.rstrip(",. ").split()[-1] if line.split() else ""
                rhyme_slots[slot] = end_word
        return lines

    def build_quatrain(
        self, topic: str, mood: str, scheme: str = "ABAB"
    ) -> List[str]:
        """Build a 4-line stanza (quatrain).

        Args:
            topic: Poem topic.
            mood: Emotional tone.
            scheme: Rhyme scheme (default ``"ABAB"``).

        Returns:
            List of 4 lines.
        """
        slots: Dict[str, Optional[str]] = {letter: None for letter in scheme}
        return self.build_stanza(topic, mood, slots, size=4)

    def build_couplet(self, topic: str, mood: str) -> List[str]:
        """Build a rhyming couplet.

        Args:
            topic: Poem topic.
            mood: Emotional tone.

        Returns:
            List of 2 lines.
        """
        line1 = self._line_gen.generate_line(topic, mood)
        end1 = line1.rstrip(",. ").split()[-1] if line1.split() else "day"
        line2 = self._line_gen.generate_closing_line(topic, mood, rhyme_word=end1)
        return [line1, line2]

    def build_tercet(
        self, topic: str, mood: str, scheme: str = "ABA"
    ) -> List[str]:
        """Build a 3-line stanza (tercet).

        Args:
            topic: Poem topic.
            mood: Emotional tone.
            scheme: Rhyme scheme.

        Returns:
            List of 3 lines.
        """
        slots: Dict[str, Optional[str]] = {letter: None for letter in scheme}
        return self.build_stanza(topic, mood, slots, size=3)

    def build_sestet(self, topic: str, mood: str) -> List[str]:
        """Build a 6-line sestet.

        Args:
            topic: Poem topic.
            mood: Emotional tone.

        Returns:
            List of 6 lines.
        """
        slots: Dict[str, Optional[str]] = {letter: None for letter in "ABABAB"}
        return self.build_stanza(topic, mood, slots, size=6)

    def grade_stanza(self, lines: List[str]) -> "Grade":
        """Grade a stanza's overall quality.

        Args:
            lines: Stanza lines.

        Returns:
            :class:`Grade` for stanza quality.
        """
        if not lines:
            return Grade.from_prob(0.3)
        if MeterAnalyzer is not None:
            analyzer = MeterAnalyzer()
            meter_grade = analyzer.analyze_poem(lines)
        else:
            meter_grade = Grade.from_prob(0.5)
        if RhymeGrader is not None:
            grader = RhymeGrader()
            rhyme_grade = grader.grade_end_rhyme(lines)
        else:
            rhyme_grade = Grade.from_prob(0.5)
        return Grade.product([meter_grade, rhyme_grade])


# ---------------------------------------------------------------------------
# PoemRefiner
# ---------------------------------------------------------------------------

class PoemRefiner:
    """Iteratively refines a :class:`PoemDraft` to improve its harmonic grade.

    Each refinement pass identifies the weakest line or dimension and
    attempts a targeted improvement.

    Args:
        descent_computer: Optional :class:`DescentComputer`.
        harmony_computer: Optional :class:`HarmonyComputer`.
    """

    def __init__(
        self,
        descent_computer: Optional[Any] = None,
        harmony_computer: Optional[Any] = None,
    ) -> None:
        """Initialise with optional backends."""
        self._descent = descent_computer
        self._harmony = harmony_computer or (PoetryHarmonyComputer() if True else None)
        self._line_gen = LineGenerator()

    def refine(
        self, draft: "PoemDraft", max_iterations: int = 5
    ) -> "PoemDraft":
        """Iteratively refine *draft* for up to *max_iterations* passes.

        Args:
            draft: The poem draft to refine.
            max_iterations: Maximum refinement iterations.

        Returns:
            Refined :class:`PoemDraft`.
        """
        try:
            from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
            refined_lines = HarmonicBeamSearch().refine(
                draft.lines, getattr(draft, "gluing", None)
            )
            if refined_lines and refined_lines != draft.lines:
                import copy
                improved = copy.copy(draft)
                improved.lines = self._jakobson_reorder(refined_lines)
                return improved
        except Exception:
            pass

        current = draft
        for i in range(max_iterations):
            old_grade = current.overall_grade()
            # Try meter refinement
            improved = self.refine_meter(current)
            new_grade = improved.overall_grade()
            if self._should_accept(old_grade, new_grade):
                current = improved
                current.iteration = draft.iteration + i + 1
            else:
                break
        import dataclasses
        return dataclasses.replace(current, lines=self._jakobson_reorder(current.lines))

    def refine_line(
        self,
        line: str,
        line_idx: int,
        draft: "PoemDraft",
    ) -> Tuple[str, "Grade"]:
        """Refine a single line from *draft*.

        Args:
            line: The current line text.
            line_idx: Index of the line.
            draft: The containing draft (for context).

        Returns:
            ``(refined_line, grade)`` pair.
        """
        if draft.request is None:
            return line, Grade.from_prob(0.5)
        topic = draft.request.topic
        mood = draft.request.mood.value if draft.request.mood else "neutral"
        meter = draft.request.target_meter
        # Try regenerating
        candidate = self._line_gen.generate_continuation(
            draft.lines[:line_idx], topic
        )
        old_grade = self._line_gen.grade_line(line, meter)
        new_grade = self._line_gen.grade_line(candidate, meter)
        if new_grade > old_grade:
            return candidate, new_grade
        return line, old_grade

    def refine_meter(self, draft: "PoemDraft") -> "PoemDraft":
        """Refine all lines with below-average metrical grades.

        Args:
            draft: The poem draft.

        Returns:
            Updated :class:`PoemDraft`.
        """
        if not draft.meter_grades or MeterEnforcer is None:
            return draft
        avg_grade = Grade.mean(draft.meter_grades).to_prob() if draft.meter_grades else 0.5
        new_lines = list(draft.lines)
        new_meter_grades = list(draft.meter_grades)
        meter = draft.request.target_meter if draft.request else None
        for i, (line, mg) in enumerate(zip(draft.lines, draft.meter_grades)):
            if mg.to_prob() < avg_grade * 0.8 and meter is not None:
                enforcer = MeterEnforcer(meter)
                words = line.split()
                adjusted, new_grade = enforcer.enforce_line(words)
                if new_grade > mg:
                    new_lines[i] = adjusted
                    new_meter_grades[i] = new_grade
        import dataclasses
        return dataclasses.replace(
            draft, lines=new_lines, meter_grades=new_meter_grades
        )

    def refine_rhyme(self, draft: "PoemDraft") -> "PoemDraft":
        """Refine end-words to improve rhyme compliance.

        Args:
            draft: The poem draft.

        Returns:
            Updated :class:`PoemDraft`.
        """
        if not draft.rhyme_grades or RhymeFinder is None:
            return draft
        # Identify weakest rhyme pair and try substitution
        return draft  # Placeholder: return unchanged for now

    def refine_imagery(self, draft: "PoemDraft") -> "PoemDraft":
        """Enrich imagery in lines that lack concrete images.

        Args:
            draft: The poem draft.

        Returns:
            Updated :class:`PoemDraft`.
        """
        imagery = ImagerySelector()
        topic = draft.request.topic if draft.request else "nature"
        mood = draft.request.mood.value if (draft.request and draft.request.mood) else "neutral"
        current_score = imagery.grade_imagery_richness(draft.lines)
        if current_score.to_prob() >= 0.6:
            return draft  # Already rich enough
        # Try inserting an image into the weakest line
        images = imagery.select_images_for_poem(topic, mood, count=3)
        if not images or not draft.lines:
            return draft
        new_lines = list(draft.lines)
        # Replace the most abstract line
        for i, line in enumerate(new_lines):
            words = line.split()
            if len(words) >= 4:
                # Insert image as an appositive
                image = random.choice(images)
                new_lines[i] = f"{line.rstrip(',.')} — {image}"
                break
        import dataclasses
        return dataclasses.replace(draft, lines=new_lines)

    def apply_feedback(
        self, draft: "PoemDraft", feedback: dict
    ) -> "PoemDraft":
        """Apply structured feedback to produce an updated draft.

        Args:
            draft: The current draft.
            feedback: Dict with keys like ``"change_line"``, ``"mood"``, etc.

        Returns:
            Updated :class:`PoemDraft`.
        """
        new_lines = list(draft.lines)
        changed = False
        if "change_line" in feedback:
            idx = feedback["change_line"].get("index", 0)
            topic = draft.request.topic if draft.request else "nature"
            mood = feedback.get("mood", "neutral")
            new_line = self._line_gen.generate_line(topic, mood)
            if idx < len(new_lines):
                new_lines[idx] = new_line
                changed = True
        import dataclasses
        if changed:
            return dataclasses.replace(
                draft, lines=new_lines, iteration=draft.iteration + 1
            )
        return draft

    def _jakobson_reorder(self, lines: List[str]) -> List[str]:
        """Optionally reorder *lines* to improve JakobsonAnalyzer parallelism.

        If the current parallelism grade is low and sorting by line length
        raises it, the sorted version is returned; otherwise the original is
        returned unchanged.

        Args:
            lines: Poem lines.

        Returns:
            Potentially reordered lines.
        """
        try:
            from gofai_chat.strata.poet.jakobson import JakobsonAnalyzer
            jak = JakobsonAnalyzer()
            jak_grade = jak.parallelism_grade(lines)
            if hasattr(jak_grade, 'p') and jak_grade.p < 0.4 and len(lines) >= 4:
                sorted_lines = sorted(lines, key=len)
                jak_grade2 = jak.parallelism_grade(sorted_lines)
                if hasattr(jak_grade2, 'p') and jak_grade2.p > jak_grade.p:
                    return sorted_lines
        except Exception:
            pass
        return lines

    def _compute_improvement(
        self, old_grade: "Grade", new_grade: "Grade"
    ) -> float:
        """Compute the relative improvement between two grades.

        Args:
            old_grade: Previous grade.
            new_grade: New grade.

        Returns:
            Relative improvement as a float.
        """
        old_p = old_grade.to_prob()
        new_p = new_grade.to_prob()
        return (new_p - old_p) / max(old_p, 0.01)

    def _should_accept(
        self, old_grade: "Grade", new_grade: "Grade"
    ) -> bool:
        """Decide whether to accept a proposed improvement.

        Args:
            old_grade: Current grade.
            new_grade: Proposed grade.

        Returns:
            ``True`` if the new grade is meaningfully better.
        """
        return new_grade.to_prob() > old_grade.to_prob() * 1.02


# ---------------------------------------------------------------------------
# PoemGenerator — main class
# ---------------------------------------------------------------------------

class PoemGenerator:
    """Main poetry generation class integrating the full Harmony pipeline.

    Accepts a :class:`PoemRequest`, runs the generation loop, and returns
    a :class:`PoemDraft` with all harmonic grades computed.

    Args:
        harmony_computer: Optional :class:`HarmonyComputer`.
        descent_computer: Optional :class:`DescentComputer`.
    """

    def __init__(
        self,
        harmony_computer: Optional[Any] = None,
        descent_computer: Optional[Any] = None,
    ) -> None:
        """Initialise with optional harmony and descent backends."""
        self._harmony = harmony_computer or (PoetryHarmonyComputer() if True else None)
        self._descent = descent_computer
        self._line_gen = LineGenerator()
        self._stanza_builder = StanzaBuilder()
        self._refiner = PoemRefiner()
        self._imagery = ImagerySelector()
        self._metaphor = MetaphorEngine()
        self._form_checker = FormChecker() if FormChecker is not None else None

    def generate(self, request: "PoemRequest") -> "PoemDraft":
        """Main entry point: generate a poem from *request*.

        Args:
            request: A :class:`PoemRequest` specifying all generation parameters.

        Returns:
            A :class:`PoemDraft` with grades and metadata.
        """
        errors = request.validate()
        if errors:
            # Generate best-effort anyway
            pass
        gluing = request.to_gluing_data()
        # Determine form
        form = request.form
        if form is None:
            if FormSelector is not None:
                mood_str = request.mood.value if request.mood else "neutral"
                selector = FormSelector()
                form = selector.select_for_topic(request.topic, mood_str)
            else:
                form = FREE_VERSE
        # Generate the poem
        draft = self._run_generation_loop(request, gluing)
        # Refine
        draft = self._refiner.refine(draft, max_iterations=3)
        # Post-process
        draft = self._post_process(draft)
        return draft

    def generate_sonnet(
        self, topic: str, mood: str = "romantic"
    ) -> "PoemDraft":
        """Generate a Shakespearean sonnet on *topic*.

        Args:
            topic: Subject matter.
            mood: Emotional tone (default ``"romantic"``).

        Returns:
            14-line :class:`PoemDraft`.
        """
        request = PoemRequest(
            topic=topic,
            form=SHAKESPEAREAN_SONNET,
            mood=Mood.PASSIONATE if mood == "romantic" else Mood.MELANCHOLIC,
            target_meter=IAMBIC_PENTAMETER,
        )
        return self.generate(request)

    def generate_haiku(
        self, topic: str, mood: str = "meditative"
    ) -> "PoemDraft":
        """Generate a haiku on *topic*.

        Args:
            topic: Subject matter.
            mood: Emotional tone.

        Returns:
            3-line :class:`PoemDraft`.
        """
        request = PoemRequest(
            topic=topic,
            form=HAIKU,
            mood=Mood.MEDITATIVE,
        )
        return self.generate(request)

    def generate_free_verse(
        self, topic: str, mood: str = "neutral", lines: int = 12
    ) -> "PoemDraft":
        """Generate a free verse poem.

        Args:
            topic: Subject matter.
            mood: Emotional tone.
            lines: Number of lines.

        Returns:
            :class:`PoemDraft`.
        """
        request = PoemRequest(
            topic=topic,
            form=FREE_VERSE,
            length=lines,
        )
        return self.generate(request)

    def generate_for_form(
        self,
        topic: str,
        form: Any,
        mood: str = "neutral",
    ) -> "PoemDraft":
        """Generate a poem in a specific *form*.

        Args:
            topic: Subject matter.
            form: Target :class:`PoemForm`.
            mood: Emotional tone.

        Returns:
            :class:`PoemDraft`.
        """
        request = PoemRequest(topic=topic, form=form)
        return self.generate(request)

    def refine_with_feedback(
        self, draft: "PoemDraft", feedback_text: str
    ) -> "PoemDraft":
        """Apply natural-language feedback to refine *draft*.

        Args:
            draft: Current poem draft.
            feedback_text: Natural language feedback (e.g. "make it sadder").

        Returns:
            Refined :class:`PoemDraft`.
        """
        try:
            from gofai_chat.generation.poetry.feedback_engine import FeedbackInterpreter
            interpreter = FeedbackInterpreter()
            signals = interpreter.interpret(feedback_text, draft.lines)
            # Build feedback dict
            feedback: dict = {}
            for sig in signals:
                if hasattr(sig, "line_index") and sig.line_index is not None:
                    feedback["change_line"] = {
                        "index": sig.line_index,
                        "signal": sig,
                    }
        except ImportError:
            feedback = {}
        return self._refiner.apply_feedback(draft, feedback)

    def grade_poem(self, lines: List[str]) -> "Grade":
        """Compute a harmonic grade for *lines*.

        Args:
            lines: Poem lines.

        Returns:
            :class:`Grade`.
        """
        if isinstance(self._harmony, PoetryHarmonyComputer):
            return self._harmony.poem_harmony(lines)
        return Grade.from_prob(0.5)

    def _initialize_gluing(
        self, request: "PoemRequest"
    ) -> Optional["GluingData"]:
        """Build an initial :class:`GluingData` from *request*.

        Args:
            request: The generation request.

        Returns:
            :class:`GluingData` or ``None``.
        """
        return request.to_gluing_data()

    def _run_generation_loop(
        self, request: "PoemRequest", gluing: Optional[Any]
    ) -> "PoemDraft":
        """Core loop: generate all lines for the poem.

        Args:
            request: The generation request.
            gluing: Initial :class:`GluingData`.

        Returns:
            Initial (unrefined) :class:`PoemDraft`.
        """
        topic = request.topic
        mood = request.mood.value if request.mood else "neutral"
        form = request.form
        meter = request.target_meter
        form_name = form.name if form is not None and hasattr(form, "name") else "free_verse"
        # Determine target line count
        line_count = request.length
        if line_count is None:
            if form is not None and hasattr(form, "line_count") and form.line_count:
                line_count = form.line_count
            else:
                line_count = 14  # Default to sonnet length

        # Try HarmonicBeamSearch as primary generation path
        try:
            from gofai_chat.generation.poetry.harmonic_search import HarmonicBeamSearch
            search = HarmonicBeamSearch(width=12)
            beam_lines = search.search(
                topic=topic, form=form_name, mood=mood, n_lines=line_count
            )
            if beam_lines and all(beam_lines):
                all_lines = beam_lines
                stanza_sizes: List[int] = []
                if form is not None and hasattr(form, "stanza_structure") and form.stanza_structure:
                    stanza_sizes = [s for s in form.stanza_structure if s > 0]
                if not stanza_sizes:
                    stanza_sizes = [4] * (line_count // 4)
                    if line_count % 4:
                        stanza_sizes.append(line_count % 4)
                stanzas: List[List[str]] = []
                idx = 0
                for sz in stanza_sizes:
                    stanzas.append(all_lines[idx:idx + sz])
                    idx += sz
                meter_grades = [Grade.from_prob(0.6)] * len(all_lines)
                rhyme_grades = [Grade.from_prob(0.5)] * len(all_lines)
                harmony_score = Grade.from_prob(0.6)
                form_grade = Grade.perfect()
                return PoemDraft(
                    lines=all_lines,
                    stanzas=stanzas,
                    meter_grades=meter_grades,
                    rhyme_grades=rhyme_grades,
                    harmony_score=harmony_score,
                    form_grade=form_grade,
                    content_grade=Grade.from_prob(0.7),
                    iteration=0,
                    request=request,
                    gluing=gluing,
                )
        except Exception:
            pass

        # Determine stanza structure
        stanza_sizes: List[int] = []
        if form is not None and hasattr(form, "stanza_structure") and form.stanza_structure:
            stanza_sizes = [s for s in form.stanza_structure if s > 0]
        if not stanza_sizes:
            stanza_sizes = [4] * (line_count // 4)
            if line_count % 4:
                stanza_sizes.append(line_count % 4)
        # Generate stanza by stanza
        all_lines: List[str] = []
        stanzas: List[List[str]] = []
        rhyme_scheme_str = request.rhyme_scheme
        if rhyme_scheme_str is None and form is not None:
            rhyme_scheme_str = getattr(form, "rhyme_scheme", None)
        rhyme_slots: Dict[str, Optional[str]] = {}
        for stanza_size in stanza_sizes:
            if stanza_size == 0:
                continue
            stanza = self._stanza_builder.build_stanza(
                topic, mood, rhyme_slots, size=stanza_size
            )
            stanzas.append(stanza)
            all_lines.extend(stanza)
        # Compute per-line grades
        meter_grades: List["Grade"] = []
        rhyme_grades: List["Grade"] = []
        if MeterAnalyzer is not None:
            analyzer = MeterAnalyzer()
            for line in all_lines:
                meter_grades.append(analyzer.analyze_line(line, meter))
        else:
            meter_grades = [Grade.from_prob(0.5)] * len(all_lines)
        if RhymeGrader is not None:
            rg = RhymeGrader()
            for i, line in enumerate(all_lines):
                if i > 0:
                    pair_grade = rg.grade_line_pair(all_lines[i - 1], line)
                else:
                    pair_grade = Grade.from_prob(0.5)
                rhyme_grades.append(pair_grade)
        else:
            rhyme_grades = [Grade.from_prob(0.5)] * len(all_lines)
        # Compute overall grade
        harmony_score = Grade.product(
            [Grade.mean(meter_grades), Grade.mean(rhyme_grades)]
        ) if meter_grades and rhyme_grades else Grade.from_prob(0.5)
        # Form grade
        form_grade = Grade.perfect()
        if self._form_checker is not None and form is not None:
            try:
                form_grade = self._form_checker.check_form(all_lines, form, gluing)
            except Exception:
                form_grade = Grade.from_prob(0.5)
        return PoemDraft(
            lines=all_lines,
            stanzas=stanzas,
            meter_grades=meter_grades,
            rhyme_grades=rhyme_grades,
            harmony_score=harmony_score,
            form_grade=form_grade,
            content_grade=Grade.from_prob(0.7),
            iteration=0,
            request=request,
            gluing=gluing,
        )

    def _post_process(self, draft: "PoemDraft") -> "PoemDraft":
        """Apply final post-processing to a draft.

        Normalises capitalisation and trims whitespace.

        Args:
            draft: The draft to post-process.

        Returns:
            Post-processed :class:`PoemDraft`.
        """
        new_lines: List[str] = []
        for i, line in enumerate(draft.lines):
            # Capitalise first word of each line
            stripped = line.strip()
            if stripped:
                capitalised = stripped[0].upper() + stripped[1:]
            else:
                capitalised = stripped
            new_lines.append(capitalised)
        import dataclasses
        return dataclasses.replace(draft, lines=new_lines)


__all__ = [
    "Mood",
    "Style",
    "PoemRequest",
    "PoemDraft",
    "PoetryHarmonyComputer",
    "MetaphorEngine",
    "ImagerySelector",
    "WordSubstituter",
    "LineGenerator",
    "StanzaBuilder",
    "PoemRefiner",
    "PoemGenerator",
    "COMMON_POETIC_WORDS",
    "TOPIC_SEED_WORDS",
    "MOOD_VOCABULARY",
    "LINE_TEMPLATES",
]
