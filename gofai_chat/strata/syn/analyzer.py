from __future__ import annotations
"""Syntactic analyzer — dependency structure and construction identification."""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

from gofai_chat.core.grade import Grade
from gofai_chat.core.terms import HLF, EventTerm
from gofai_chat.frames.instances import FrameInstance, RoleFiller

__all__ = ["SynAnalyzer", "SynSection"]


@dataclass
class SynSection:
    """A bundle of syntactic properties derived from a :class:`FrameInstance`.

    Attributes:
        word_order: One of ``"SVO"``, ``"SOV"``, ``"VSO"``, ``"VOS"``,
            ``"OVS"``, ``"OSV"``, or ``"FREE"``.
        constructions: Detected syntactic constructions such as
            ``"PASSIVE"`` or ``"CLEFT"``.
        parse_quality: A :class:`~gofai_chat.core.grade.Grade` reflecting
            how complete and internally consistent the source frame is.
        head_words: The main predicate and any other prominent head tokens
            identified in the frame.
        dependency_depth: Estimated depth of the syntactic dependency tree
            (count of embedded :class:`FrameInstance` levels).
        fstructure: LFG f-structure (when available from SpacyBridge).
    """

    word_order: str
    constructions: list[str]
    parse_quality: Grade
    head_words: list[str]
    dependency_depth: int
    fstructure: object = None  # Optional[FStructure] — avoids circular import


class SynAnalyzer:
    """Analyse the syntactic properties of a :class:`FrameInstance` or raw text.

    Provides two analysis paths:
      1. Frame-based (legacy): heuristic analysis over FrameInstance.
      2. Text-based (new): uses SpacyBridge → f-structure → SynSection.

    All analysis is heuristic: it inspects role names, frame-type names,
    filler sources, and embedding depth rather than running a full parse.
    """

    def __init__(self):
        self._bridge = None

    def _ensure_bridge(self):
        """Lazy-init the SpacyBridge (avoids import cost when unused)."""
        if self._bridge is None:
            from gofai_chat.strata.syn.spacy_bridge import SpacyBridge
            self._bridge = SpacyBridge()

    def analyze(self, fi_or_text, context=None) -> SynSection:
        """Produce a :class:`SynSection` for a FrameInstance or raw text.

        Args:
            fi_or_text: Either a FrameInstance or a string of text.
            context: Optional context (unused for now).

        Returns:
            A :class:`SynSection` summarising word order, constructions,
            parse quality, head words, dependency depth, and f-structure.
        """
        if isinstance(fi_or_text, str):
            return self._analyze_text(fi_or_text)
        return self._analyze_frame(fi_or_text)

    def _analyze_text(self, text: str) -> SynSection:
        """Analyse raw text using SpacyBridge → FStructure."""
        self._ensure_bridge()
        fstructs = self._bridge.get_fstructures(text)

        if not fstructs:
            return SynSection(
                word_order="SVO",
                constructions=[],
                parse_quality=Grade.from_prob(0.3),
                head_words=[],
                dependency_depth=0,
                fstructure=None,
            )

        fs, grade = fstructs[0]  # primary sentence
        constructions = self._detect_constructions_from_fstruct(fs)
        head_words = [fs.pred] if fs.pred else []

        return SynSection(
            word_order=self._word_order_from_fstruct(fs),
            constructions=constructions,
            parse_quality=grade,
            head_words=head_words,
            dependency_depth=self._depth_from_fstruct(fs),
            fstructure=fs,
        )

    def _analyze_frame(self, fi: FrameInstance) -> SynSection:
        """Produce a :class:`SynSection` for the given frame instance.

        Args:
            fi: The frame instance to analyse.

        Returns:
            A :class:`SynSection` summarising word order, constructions,
            parse quality, head words, and dependency depth.
        """
        constructions = self.construction_identification(fi)
        return SynSection(
            word_order=self.word_order_type(fi),
            constructions=constructions,
            parse_quality=self.parse_quality_score(fi),
            head_words=self._collect_head_words(fi),
            dependency_depth=self._estimate_dependency_depth(fi),
        )

    def parse_quality_score(self, fi: FrameInstance) -> Grade:
        """Grade the parse quality based on how complete the frame is.

        A fully completed frame (all required roles filled) earns
        :meth:`Grade.perfect`.  Each missing required role reduces the
        score proportionally.

        Args:
            fi: The frame instance to grade.

        Returns:
            A :class:`~gofai_chat.core.grade.Grade` between
            :meth:`Grade.impossible` and :meth:`Grade.perfect`.
        """
        required = fi.frame_type.required_roles()
        if not required:
            # No required roles means we cannot penalise incompleteness.
            return Grade.from_prob(0.8)
        unfilled = len(fi.unfilled_required_roles)
        total = len(required)
        fraction_filled = (total - unfilled) / total
        return Grade.from_prob(fraction_filled)

    def construction_identification(self, fi: FrameInstance) -> list[str]:
        """Identify syntactic constructions present in the frame instance.

        Inspects the frame-type name and the set of filled role names to
        heuristically detect:

        * PASSIVE — patient is topicalised, agent absent or peripherally sourced
        * CLEFT — frame name contains "Cleft" or subject filler is "it"
        * THERE_EXISTENTIAL — frame name contains "Exist" or a filler contains "there"
        * TOUGH_MOVEMENT — frame name contains "Tough" or contains an infinitival "to"
        * TOPICALIZATION — a non-agent role precedes the predicate in linear order
        * VP_FRONTING — predicate role is front-filled
        * RAISING — frame name contains "Rais" or an embedded clause is promoted
        * CONTROL — frame name contains "Control" or PRO subject present
        * SMALL_CLAUSE — frame name contains "Small" or SC role present

        Args:
            fi: The frame instance to inspect.

        Returns:
            A list of construction names (strings) that were detected.
        """
        constructions: list[str] = []
        name = fi.frame_type.name
        fillers: dict[str, RoleFiller] = getattr(fi, "fillers", {})
        role_names = set(fillers.keys())

        # PASSIVE
        if "patient" in role_names:
            agent_filler = fillers.get("agent")
            if agent_filler is None or (
                hasattr(agent_filler, "source") and agent_filler.source == "absent"
            ):
                constructions.append("PASSIVE")

        # CLEFT
        if "Cleft" in name:
            constructions.append("CLEFT")
        elif "subject" in role_names:
            subj = fillers["subject"]
            val = str(getattr(subj, "value", "")).lower()
            if val in ("it", "it is", "it was"):
                constructions.append("CLEFT")

        # THERE_EXISTENTIAL
        if "Exist" in name or "Existential" in name:
            constructions.append("THERE_EXISTENTIAL")
        else:
            for rf in fillers.values():
                val = str(getattr(rf, "value", "")).lower()
                if val.startswith("there"):
                    constructions.append("THERE_EXISTENTIAL")
                    break

        # TOUGH_MOVEMENT
        if "Tough" in name or "tough" in name.lower():
            constructions.append("TOUGH_MOVEMENT")
        elif any("tough" in str(getattr(rf, "value", "")).lower() for rf in fillers.values()):
            constructions.append("TOUGH_MOVEMENT")

        # TOPICALIZATION — a role other than agent/subject is the only "topic" filler
        if "topic" in role_names and "agent" not in role_names:
            constructions.append("TOPICALIZATION")

        # VP_FRONTING
        if "VP_front" in name or "VPFront" in name:
            constructions.append("VP_FRONTING")

        # RAISING
        if "Rais" in name or "raising" in name.lower():
            constructions.append("RAISING")

        # CONTROL
        if "Control" in name or "PRO" in role_names:
            constructions.append("CONTROL")

        # SMALL_CLAUSE
        if "Small" in name or "SmallClause" in name or "SC" in role_names:
            constructions.append("SMALL_CLAUSE")

        return constructions

    def word_order_type(self, fi: FrameInstance) -> str:
        """Infer the canonical word-order type of the clause.

        Returns one of ``"SVO"``, ``"SOV"``, ``"VSO"``, ``"VOS"``,
        ``"OVS"``, ``"OSV"``, or ``"FREE"``.

        The heuristic defaults to ``"SVO"`` (English) and then adjusts
        based on construction information and role presence:

        * A detected PASSIVE construction inverts subject/object.
        * If the agent is absent but the patient is present as topic,
          the surface order resembles ``"OVS"``.
        * Frame-type names containing language hints (e.g. ``"SOV"``,
          ``"VSO"``) override the default.

        Args:
            fi: The frame instance to inspect.

        Returns:
            A word-order abbreviation string.
        """
        name = fi.frame_type.name
        # Explicit language/typology hint in the frame name
        for order in ("SVO", "SOV", "VSO", "VOS", "OVS", "OSV", "FREE"):
            if order in name:
                return order

        constructions = self.construction_identification(fi)
        fillers: dict[str, RoleFiller] = getattr(fi, "fillers", {})
        role_names = set(fillers.keys())

        if "PASSIVE" in constructions:
            # Patient is now surface subject → object has moved to front
            return "OVS"

        if "THERE_EXISTENTIAL" in constructions:
            return "VSO"

        if "agent" in role_names and "patient" in role_names:
            return "SVO"

        if "agent" in role_names:
            return "SVO"

        if "patient" in role_names:
            return "OVS"

        return "SVO"

    def _estimate_dependency_depth(self, fi: FrameInstance) -> int:
        """Estimate syntactic dependency depth by counting embedding levels.

        Each :class:`FrameInstance` nested as the ``value`` of a
        :class:`RoleFiller` adds one level of depth.  The method recurses
        to find the maximum depth across all role fillers.

        Args:
            fi: The root frame instance.

        Returns:
            An integer representing the maximum embedding depth (≥ 0).
        """
        fillers: dict[str, RoleFiller] = getattr(fi, "fillers", {})
        max_depth = 0
        for rf in fillers.values():
            val = getattr(rf, "value", None)
            if isinstance(val, FrameInstance):
                child_depth = 1 + self._estimate_dependency_depth(val)
                if child_depth > max_depth:
                    max_depth = child_depth
        return max_depth

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _collect_head_words(self, fi: FrameInstance) -> list[str]:
        """Gather the main predicate name and prominent head tokens.

        The frame-type name is always included.  Additionally, the ``head``
        attribute of any filled :class:`RoleFiller` (e.g. the head noun of
        the theme NP) is collected if non-empty.

        Args:
            fi: The frame instance to inspect.

        Returns:
            A de-duplicated list of head word strings.
        """
        heads: list[str] = [fi.frame_type.name]
        fillers: dict[str, RoleFiller] = getattr(fi, "fillers", {})
        for rf in fillers.values():
            head = getattr(rf, "head", "")
            if head and head not in heads:
                heads.append(head)
        return heads

    # ------------------------------------------------------------------
    # FStructure-based helpers (new)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_constructions_from_fstruct(fs) -> list[str]:
        """Detect constructions from f-structure properties."""
        constructions = []
        features = getattr(fs, "features", None)
        if features:
            voice = getattr(features, "value", lambda x: None)("Voice")
            if voice == "passive":
                constructions.append("PASSIVE")
            mood = getattr(features, "value", lambda x: None)("Mood")
            if mood == "imperative":
                constructions.append("IMPERATIVE")
            if mood == "subjunctive":
                constructions.append("SUBJUNCTIVE")
        if getattr(fs, "xcomp", None):
            constructions.append("CONTROL")
        if getattr(fs, "comp", None):
            constructions.append("CLAUSAL_COMPLEMENT")
        return constructions

    @staticmethod
    def _word_order_from_fstruct(fs) -> str:
        """Infer word order from f-structure — English default SVO."""
        features = getattr(fs, "features", None)
        if features:
            voice = getattr(features, "value", lambda x: None)("Voice")
            if voice == "passive":
                return "OVS"
        return "SVO"

    @staticmethod
    def _depth_from_fstruct(fs, depth: int = 0) -> int:
        """Estimate dependency depth from f-structure nesting."""
        max_d = depth
        for child in [
            getattr(fs, "subj", None),
            getattr(fs, "obj", None),
            getattr(fs, "obj_th", None),
            getattr(fs, "xcomp", None),
            getattr(fs, "comp", None),
        ]:
            if child is not None and hasattr(child, "pred") and child.pred:
                child_d = SynAnalyzer._depth_from_fstruct(child, depth + 1)
                max_d = max(max_d, child_d)
        return max_d
