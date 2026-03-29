"""PoeticRealizer: HLF EventTerm → poetically varied surface forms.

Every poem line must express an actual HLF proposition.  This module
applies systematic linguistic transformations to EventTerms—
each grounded in classical NLG theory—producing grammatically
correct variant surface forms that the Viterbi decoder then scores
for meter and rhyme fitness.

Transformations (each linguistically motivated):
  CANONICAL     — canonical SVO: "Mishra works at NYU"
  TOPICALIZED   — PP- or NP-initial: "At NYU, Mishra works"
  THEME_INITIAL — object-fronted: "Algorithms, Mishra studied"
  PASSIVE       — patient-subject: "algorithms were studied by Mishra"
  NOMINALIZED   — deverbal NP: "Mishra's discovery of radioactivity"
  RELATIVE      — agent-gap relative: "who works at NYU"
  PARTICIPIAL   — gerundive VP: "studying algorithms at NYU"
  INVERTED      — V-initial (poetic): "Works Mishra, in the city"

These are not templates: they are compositional operations over the
Grade semiring-scored EventTerm structure, realized via SurfaceRealizer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from gofai_chat.core.grade import Grade
from gofai_chat.core.terms import EventTerm


class PoeticTransform(Enum):
    CANONICAL = "canonical"
    TOPICALIZED = "topicalized"
    THEME_INITIAL = "theme_initial"
    PASSIVE = "passive"
    NOMINALIZED = "nominalized"
    RELATIVE = "relative"
    PARTICIPIAL = "participial"
    INVERTED = "inverted"


@dataclass
class PoeticCandidate:
    surface: str
    event_term: EventTerm
    transform: PoeticTransform
    syllable_count: int
    grade: Grade = field(default_factory=lambda: Grade.from_prob(0.7))


# ── Role taxonomy ──────────────────────────────────────────────────────────

_SUBJECT_ROLES = (
    "agent", "experiencer", "cognizer", "perceiver",
    "student", "author", "leader", "resident", "child",
    "founder", "winner", "teacher", "protagonist",
)
_OBJECT_ROLES = (
    "theme", "patient", "phenomenon", "content",
    "topic", "subject_matter", "goal", "attribute",
    "product", "result", "entity", "effect",
)
_PREP_FOR_ROLE = {
    "location":    "in",
    "source":      "from",
    "goal":        "to",
    "manner":      "with",
    "purpose":     "for",
    "path":        "along",
    "recipient":   "to",
    "beneficiary": "for",
    "instrument":  "with",
    "cause":       "from",
}

# Verb → deverbal noun for nominalization
_DEVERBAL: dict[str, str] = {
    "discover": "discovery",   "create": "creation",     "know": "knowledge",
    "write": "writing",        "live": "life",            "study": "study",
    "work": "work",            "found": "founding",       "research": "research",
    "teach": "teaching",       "think": "thought",        "feel": "feeling",
    "see": "sight",            "love": "love",            "hate": "hatred",
    "fear": "fear",            "hope": "hope",            "dream": "dream",
    "remember": "memory",      "forget": "forgetting",    "build": "building",
    "make": "making",          "break": "breaking",       "fall": "fall",
    "rise": "rise",            "grow": "growth",          "change": "change",
    "move": "movement",        "speak": "speech",         "sing": "song",
    "hear": "hearing",         "watch": "watching",       "seek": "search",
    "find": "finding",         "leave": "leaving",        "return": "return",
    "fly": "flight",           "run": "running",          "walk": "walk",
    "die": "death",            "begin": "beginning",      "end": "end",
    "open": "opening",         "close": "closing",        "give": "gift",
    "take": "taking",          "bring": "bringing",       "lose": "loss",
    "win": "victory",          "develop": "development",  "publish": "publication",
    "contribute": "contribution", "produce": "production", "receive": "reception",
    "explore": "exploration",  "investigate": "investigation",
    "analyze": "analysis",     "design": "design",        "prove": "proof",
    "solve": "solution",       "apply": "application",    "extend": "extension",
}


class PoeticRealizer:
    """Generate poetically varied surface forms of HLF EventTerms.

    Uses SurfaceRealizer for grammatical base, then applies systematic
    linguistic transformations producing variant surface forms for
    scoring by the Viterbi decoder's meter/rhyme constraints.

    All internal state is lazy-loaded to avoid circular imports.
    """

    def __init__(self) -> None:
        self._sr = None
        self._morph = None
        self._dm = None           # DM engine (new)
        self._ftv = None          # FRAME_TO_VERB dict
        self._meter = None
        self._Context = None

    def _ensure(self) -> None:
        if self._sr is not None:
            return
        from gofai_chat.generation.surface_realization import SurfaceRealizer, FRAME_TO_VERB
        from gofai_chat.core.judgment import Context
        self._sr = SurfaceRealizer()
        self._morph = self._sr._morph
        self._dm = self._sr._dm  # DM engine from SurfaceRealizer
        self._ftv = FRAME_TO_VERB
        self._Context = Context

    # ── Public API ─────────────────────────────────────────────────────────

    def realize_candidates(
        self,
        event_terms: List[EventTerm],
        target_syllables: int = 10,
        tense: str = "present",
        n_per_term: int = 6,
    ) -> List[PoeticCandidate]:
        """Return poetically varied surface forms for all propositions.

        Each EventTerm yields up to ``n_per_term`` candidates via different
        linguistic transformations. Results are sorted by syllable proximity
        to ``target_syllables``; callers apply further meter/rhyme scoring.

        Pipeline (when DM available):
          1. EventTerm → FStructure.from_event_term()
          2. FStructure → linguistic transformations
          3. Each transform → SurfaceRealizer.realize()
          4. Grade = G_sem ⊗ G_syn ⊗ G_morph
        """
        self._ensure()
        candidates: List[PoeticCandidate] = []
        for et in event_terms:
            try:
                candidates.extend(self._candidates_for(et, tense, n_per_term))
            except Exception:
                pass
        # Sort by proximity to target syllable count
        candidates.sort(key=lambda c: abs(c.syllable_count - target_syllables))
        return candidates

    def realize_from_fstructure(
        self,
        fs: "FStructure",
        target_syllables: int = 10,
        tense: str = "present",
        n_per_term: int = 6,
    ) -> List[PoeticCandidate]:
        """Generate poetic candidates from an LFG FStructure.

        Converts FStructure to EventTerm and routes through the
        standard poetic transformation pipeline.
        """
        self._ensure()
        et = fs.to_event_term()
        if isinstance(et, EventTerm):
            return self._candidates_for(et, tense, n_per_term)
        return []

    # ── Private: generate all transforms for one EventTerm ─────────────────

    def _candidates_for(
        self,
        et: EventTerm,
        tense: str,
        max_n: int,
    ) -> List[PoeticCandidate]:
        ctx = self._Context()
        pairs = [
            (PoeticTransform.CANONICAL,    self._canonical(et, ctx, tense)),
            (PoeticTransform.TOPICALIZED,  self._topicalized(et, ctx, tense)),
            (PoeticTransform.THEME_INITIAL, self._theme_initial(et, ctx, tense)),
            (PoeticTransform.PASSIVE,      self._passive(et, ctx, tense)),
            (PoeticTransform.NOMINALIZED,  self._nominalized(et, ctx)),
            (PoeticTransform.RELATIVE,     self._relative(et, ctx, tense)),
            (PoeticTransform.PARTICIPIAL,  self._participial(et, ctx)),
            (PoeticTransform.INVERTED,     self._inverted(et, ctx, tense)),
        ]
        results = []
        for transform, surface in pairs:
            if surface and len(surface.split()) >= 2:
                results.append(PoeticCandidate(
                    surface=surface.strip(),
                    event_term=et,
                    transform=transform,
                    syllable_count=self._syllables(surface),
                ))
            if len(results) >= max_n:
                break
        return results

    # ── Transformation implementations ─────────────────────────────────────

    def _canonical(self, et: EventTerm, ctx, tense: str) -> Optional[str]:
        """SVO: 'Mishra works at NYU'"""
        try:
            from gofai_chat.core.terms import TenseTerm
            wrapped = TenseTerm(tense=tense, body=et) if tense != "present" else et
            return self._sr.realize(wrapped, ctx)
        except Exception:
            return None

    def _topicalized(self, et: EventTerm, ctx, tense: str) -> Optional[str]:
        """PP-initial: 'At NYU, Mishra works' / 'From darkness, light comes'"""
        roles = et.roles
        # Front an adjunct PP role
        for role_name in ("location", "source", "goal", "manner", "path"):
            if role_name not in roles:
                continue
            prep = _PREP_FOR_ROLE.get(role_name, "at")
            loc_str = self._sr._realize_np(roles[role_name], ctx, role_name=role_name)
            rest_et = EventTerm(
                frame_type_name=et.frame_type_name,
                event_var=et.event_var,
                roles={k: v for k, v in roles.items() if k != role_name},
                grade=et.grade,
            )
            try:
                rest_str = self._sr.realize(rest_et, ctx)
                return f"{prep} {loc_str}, {rest_str}"
            except Exception:
                pass
        # Theme-topicalization fallback
        if "theme" in roles and any(r in roles for r in _SUBJECT_ROLES):
            try:
                theme_str = self._sr._realize_np(roles["theme"], ctx, role_name="theme")
                rest_et = EventTerm(
                    frame_type_name=et.frame_type_name,
                    event_var=et.event_var,
                    roles={k: v for k, v in roles.items() if k != "theme"},
                    grade=et.grade,
                )
                rest_str = self._sr.realize(rest_et, ctx)
                return f"{theme_str}, {rest_str}"
            except Exception:
                pass
        return None

    def _theme_initial(self, et: EventTerm, ctx, tense: str) -> Optional[str]:
        """Object-fronted: 'Algorithms, Mishra studied'"""
        roles = et.roles
        obj_role = next((r for r in _OBJECT_ROLES if r in roles), None)
        subj_role = next((r for r in _SUBJECT_ROLES if r in roles), None)
        if not (obj_role and subj_role):
            return None
        try:
            from gofai_chat.generation.morphology import MorphFeatures
            obj_str = self._sr._realize_np(roles[obj_role], ctx, role_name=obj_role)
            subj_str = self._sr._realize_np(roles[subj_role], ctx, role_name=subj_role)
            spec = self._ftv.get(et.frame_type_name, {})
            verb_base = spec.get("verb", et.frame_type_name.lower()).split()[0]
            morph = self._sr._np_morph_features(roles[subj_role], ctx)
            verb_str = self._morph.conjugate(
                verb_base,
                MorphFeatures(tense=tense, aspect="simple", person=morph.person, number=morph.number)
            )
            return f"{obj_str}, {subj_str} {verb_str}"
        except Exception:
            return None

    def _passive(self, et: EventTerm, ctx, tense: str) -> Optional[str]:
        """Passive: 'Algorithms were studied by Mishra'"""
        roles = et.roles
        obj_role = next((r for r in _OBJECT_ROLES if r in roles), None)
        if not obj_role:
            return None
        try:
            spec = self._ftv.get(et.frame_type_name, {})
            verb_base = spec.get("verb", et.frame_type_name.lower()).split()[0]
            from gofai_chat.generation.morphology import IRREGULAR_VERBS
            irr = IRREGULAR_VERBS.get(verb_base, {})
            pp = self._morph._past_participle(verb_base, irr)
            obj_str = self._sr._realize_np(roles[obj_role], ctx, role_name=obj_role)
            be = "was" if tense == "past" else "is"
            result = f"{obj_str} {be} {pp}"
            subj_role = next((r for r in _SUBJECT_ROLES if r in roles), None)
            if subj_role:
                agent_str = self._sr._realize_np(roles[subj_role], ctx, role_name=subj_role)
                result += f" by {agent_str}"
            return result
        except Exception:
            return None

    def _nominalized(self, et: EventTerm, ctx) -> Optional[str]:
        """NP form: 'Mishra's discovery of radioactivity'"""
        roles = et.roles
        spec = self._ftv.get(et.frame_type_name, {})
        verb_base = spec.get("verb", et.frame_type_name.lower()).split()[0]
        # If verb_base is already a noun (nominalization), use it directly.
        _NOUN_SUFFIXES = ("tion", "sion", "ment", "ness", "ity", "ance", "ence", "ism")
        if any(verb_base.endswith(s) for s in _NOUN_SUFFIXES) and len(verb_base) > 6:
            noun_form = verb_base
        else:
            noun_form = _DEVERBAL.get(verb_base, verb_base + "ing")
        subj_role = next((r for r in _SUBJECT_ROLES if r in roles), None)
        obj_role = next((r for r in _OBJECT_ROLES if r in roles), None)
        try:
            if subj_role:
                agent_str = self._sr._realize_np(roles[subj_role], ctx, role_name=subj_role)
                result = f"{agent_str}'s {noun_form}"
            else:
                result = f"the {noun_form}"
            if obj_role:
                obj_str = self._sr._realize_np(roles[obj_role], ctx, role_name=obj_role)
                result += f" of {obj_str}"
            elif "location" in roles:
                loc_str = self._sr._realize_np(roles["location"], ctx, role_name="location")
                result += f" at {loc_str}"
            return result
        except Exception:
            return None

    def _relative(self, et: EventTerm, ctx, tense: str) -> Optional[str]:
        """Agent-gap relative clause: 'who works at NYU'"""
        roles = et.roles
        subj_role = next((r for r in _SUBJECT_ROLES if r in roles), None)
        if not subj_role:
            return None
        try:
            rest_et = EventTerm(
                frame_type_name=et.frame_type_name,
                event_var=et.event_var,
                roles={k: v for k, v in roles.items() if k != subj_role},
                grade=et.grade,
            )
            # Force 3sg agreement since 'who' is singular
            from gofai_chat.generation.morphology import MorphFeatures
            spec = self._ftv.get(et.frame_type_name, {})
            verb_base = spec.get("verb", et.frame_type_name.lower()).split()[0]
            self._sr.set_perspective(3, "singular")
            rest_str = self._sr.realize(rest_et, ctx)
            self._sr.set_perspective(3, "singular")  # restore default
            return f"who {rest_str}"
        except Exception:
            return None

    def _participial(self, et: EventTerm, ctx) -> Optional[str]:
        """Gerundive VP (no subject): 'studying algorithms at NYU'"""
        roles = et.roles
        spec = self._ftv.get(et.frame_type_name, {})
        verb_base = spec.get("verb", et.frame_type_name.lower()).split()[0]
        # If the frame lookup failed and we got back a nominalization (noun ending),
        # it cannot be conjugated as a verb — return None rather than produce garbage.
        _NOUN_SUFFIXES = ("tion", "sion", "ment", "ness", "ity", "ance", "ence",
                          "ism", "ist", "ness", "ing")
        if any(verb_base.endswith(s) for s in _NOUN_SUFFIXES) and len(verb_base) > 6:
            return None
        try:
            from gofai_chat.generation.morphology import IRREGULAR_VERBS, MorphFeatures
            irr = IRREGULAR_VERBS.get(verb_base, {})
            pres_part = irr.get("present_participle")
            if not pres_part:
                progressive = self._morph.conjugate(
                    verb_base,
                    MorphFeatures(tense="present", aspect="progressive", person=3, number="singular")
                )
                # strip "is " prefix from "is working" → "working"
                pres_part = re.sub(r"^(is|are|am)\s+", "", progressive)
            parts = [pres_part]
            obj_role = next((r for r in _OBJECT_ROLES if r in roles), None)
            if obj_role:
                obj_str = self._sr._realize_np(roles[obj_role], ctx, role_name=obj_role)
                parts.append(obj_str)
            for rname in ("location", "source", "goal", "manner"):
                if rname in roles:
                    prep = _PREP_FOR_ROLE.get(rname, "at")
                    loc_str = self._sr._realize_np(roles[rname], ctx, role_name=rname)
                    parts.append(f"{prep} {loc_str}")
            return " ".join(parts) if len(parts) > 1 else None
        except Exception:
            return None

    def _inverted(self, et: EventTerm, ctx, tense: str) -> Optional[str]:
        """V-initial (poetic inversion): 'Works Mishra, in the city'"""
        roles = et.roles
        subj_role = next((r for r in _SUBJECT_ROLES if r in roles), None)
        if not subj_role:
            return None
        spec = self._ftv.get(et.frame_type_name, {})
        verb_base = spec.get("verb", et.frame_type_name.lower()).split()[0]
        try:
            from gofai_chat.generation.morphology import MorphFeatures
            morph = self._sr._np_morph_features(roles[subj_role], ctx)
            verb_str = self._morph.conjugate(
                verb_base,
                MorphFeatures(tense=tense, aspect="simple", person=morph.person, number=morph.number)
            )
            subj_str = self._sr._realize_np(roles[subj_role], ctx, role_name=subj_role)
            parts = [verb_str, subj_str]
            obj_role = next((r for r in _OBJECT_ROLES if r in roles), None)
            if obj_role:
                obj_str = self._sr._realize_np(roles[obj_role], ctx, role_name=obj_role)
                parts.append(obj_str)
            for rname in ("location", "source", "goal"):
                if rname in roles:
                    prep = _PREP_FOR_ROLE.get(rname, "in")
                    loc_str = self._sr._realize_np(roles[rname], ctx, role_name=rname)
                    parts.append(f"{prep} {loc_str}")
            return " ".join(parts)
        except Exception:
            return None

    # ── Utilities ──────────────────────────────────────────────────────────

    def _syllables(self, text: str) -> int:
        """Syllable count, falling back to vowel cluster counting."""
        try:
            if self._meter is None:
                from gofai_chat.generation.poetry.meter_engine import MeterScanner
                self._meter = MeterScanner()
            return self._meter.count_syllables(text)
        except Exception:
            return len(re.findall(r"[aeiouAEIOU]+", text))
