"""LFG: c-structure, f-structure, and the correspondence function φ.

After:
  Kaplan & Bresnan (1982) "Lexical-Functional Grammar: A Formal System
      for Grammatical Representation"
  Bresnan (2001) *Lexical-Functional Syntax*
  Dalrymple (2001) *Lexical Functional Grammar*

Why LFG for HLF generation?
  f-structures map cleanly to EventTerm roles:
    PRED    → frame_type_name
    SUBJ    → agent role (Const)
    OBJ     → theme role (Const)
    OBJ-TH  → goal/recipient role
    ADJUNCTS → manner/location/temporal modifiers
    TENSE   → TenseTerm wrapper

  The φ: c-structure → f-structure correspondence gives us:
    analytic:  parse tree → f-structure → EventTerm
    generative: EventTerm → f-structure → CCG derivation → surface
"""
from __future__ import annotations

__all__ = [
    "FStructure",
    "PhiCorrespondence",
    "GrammaticalFunction",
    "SUBCATEGORISATION_FRAMES",
]

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gofai_chat.core.grade import Grade
from gofai_chat.core.terms import (
    HLF,
    Const,
    EventTerm,
    TenseTerm,
    AspectTerm,
    NegTerm,
    Var,
)
from gofai_chat.grammar.features import (
    Feature,
    FeatureBundle,
    PhiFeatures,
    TenseFeature,
    make_verb_features,
)


# ══════════════════════════════════════════════════════════════════════════
# Grammatical functions
# ══════════════════════════════════════════════════════════════════════════


class GrammaticalFunction:
    """LFG grammatical function constants.

    Core (subcategorisable): SUBJ, OBJ, OBJ-TH, OBL-θ, COMP, XCOMP
    Non-core (adjuncts): ADJ, POSS, SPEC, APP
    """

    SUBJ = "SUBJ"
    OBJ = "OBJ"
    OBJ_TH = "OBJ-TH"
    OBL = "OBL"
    COMP = "COMP"
    XCOMP = "XCOMP"
    ADJ = "ADJ"
    POSS = "POSS"
    SPEC = "SPEC"
    APP = "APP"
    TOPIC = "TOPIC"
    FOCUS = "FOCUS"

    CORE = frozenset({SUBJ, OBJ, OBJ_TH, OBL, COMP, XCOMP})
    NON_CORE = frozenset({ADJ, POSS, SPEC, APP, TOPIC, FOCUS})


# ══════════════════════════════════════════════════════════════════════════
# Subcategorisation frames (PRED value → required GFs)
# ══════════════════════════════════════════════════════════════════════════

SUBCATEGORISATION_FRAMES: Dict[str, Dict[str, Any]] = {
    # Transitive: PRED 'V〈SUBJ, OBJ〉'
    "transitive": {
        "required": {GrammaticalFunction.SUBJ, GrammaticalFunction.OBJ},
        "optional": {GrammaticalFunction.ADJ, GrammaticalFunction.OBL},
    },
    # Intransitive: PRED 'V〈SUBJ〉'
    "intransitive": {
        "required": {GrammaticalFunction.SUBJ},
        "optional": {GrammaticalFunction.ADJ, GrammaticalFunction.OBL},
    },
    # Ditransitive: PRED 'V〈SUBJ, OBJ, OBJ-TH〉'
    "ditransitive": {
        "required": {
            GrammaticalFunction.SUBJ,
            GrammaticalFunction.OBJ,
            GrammaticalFunction.OBJ_TH,
        },
        "optional": {GrammaticalFunction.ADJ},
    },
    # Subject-raising: PRED 'V〈XCOMP〉SUBJ'
    "raising": {
        "required": {GrammaticalFunction.SUBJ, GrammaticalFunction.XCOMP},
        "optional": set(),
    },
    # Control: PRED 'V〈SUBJ, XCOMP〉'
    "control": {
        "required": {GrammaticalFunction.SUBJ, GrammaticalFunction.XCOMP},
        "optional": {GrammaticalFunction.OBJ},
    },
    # Copular: PRED 'V〈SUBJ, XCOMP〉'
    "copular": {
        "required": {GrammaticalFunction.SUBJ, GrammaticalFunction.XCOMP},
        "optional": set(),
    },
    # Unaccusative: PRED 'V〈SUBJ〉' (theme subject)
    "unaccusative": {
        "required": {GrammaticalFunction.SUBJ},
        "optional": {GrammaticalFunction.OBL, GrammaticalFunction.ADJ},
    },
}


# ══════════════════════════════════════════════════════════════════════════
# FStructure — the functional core
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class FStructure:
    """An f-structure: a set of attribute-value pairs with Grade.

    Core grammatical functions: SUBJ, OBJ, OBJ-TH, OBL-θ, COMP, XCOMP
    Non-core: ADJ (adjuncts), POSS, SPEC
    Feature attributes: TENSE, ASPECT, MOOD, CASE, NUM, PERS, GEND
    PRED: semantic form predicate

    After Bresnan (2001): f-structures are attribute-value matrices (AVMs)
    that represent abstract grammatical relations independent of c-structure
    (phrase structure) configuration.
    """

    pred: Optional[str] = None
    subj: Optional["FStructure"] = None
    obj: Optional["FStructure"] = None
    obj_th: Optional["FStructure"] = None
    obliques: Dict[str, "FStructure"] = field(default_factory=dict)
    adjuncts: List["FStructure"] = field(default_factory=list)
    xcomp: Optional["FStructure"] = None
    comp: Optional["FStructure"] = None
    features: FeatureBundle = field(default_factory=FeatureBundle)
    grade: Grade = field(default_factory=Grade.perfect)
    # For NP f-structures
    referent: Optional[str] = None
    definiteness: str = "indefinite"
    # Frame-role mapping context
    _role_mapping: Dict[str, str] = field(default_factory=dict)

    # ── Wellformedness conditions ────────────────────────────────────────

    def completeness_grade(self) -> Grade:
        """Completeness: all subcategorised GFs are present.

        After Bresnan (2001): every argument subcategorised by PRED must
        have a corresponding GF in the f-structure.
        Grade < 1 if any required GF is missing.
        """
        if not self.pred:
            return Grade.from_prob(0.5)

        subcat = self._guess_subcat()
        required = subcat.get("required", set())
        present: set = set()
        if self.subj:
            present.add(GrammaticalFunction.SUBJ)
        if self.obj:
            present.add(GrammaticalFunction.OBJ)
        if self.obj_th:
            present.add(GrammaticalFunction.OBJ_TH)
        if self.xcomp:
            present.add(GrammaticalFunction.XCOMP)
        if self.comp:
            present.add(GrammaticalFunction.COMP)
        if self.obliques:
            present.add(GrammaticalFunction.OBL)

        missing = required - present
        if not missing:
            return Grade.perfect()
        penalty = len(missing) / max(len(required), 1)
        return Grade.from_prob(max(0.1, 1.0 - penalty))

    def coherence_grade(self) -> Grade:
        """Coherence: no extra unrequired GFs.

        After Bresnan (2001): every GF in the f-structure must be
        subcategorised by PRED.  Adjuncts are always coherent.
        Grade < 1 if spurious core GF present.
        """
        if not self.pred:
            return Grade.from_prob(0.7)

        subcat = self._guess_subcat()
        allowed = subcat.get("required", set()) | subcat.get("optional", set())

        spurious = 0
        if self.subj and GrammaticalFunction.SUBJ not in allowed:
            spurious += 1
        if self.obj and GrammaticalFunction.OBJ not in allowed:
            spurious += 1
        if self.obj_th and GrammaticalFunction.OBJ_TH not in allowed:
            spurious += 1

        if spurious == 0:
            return Grade.perfect()
        return Grade.from_prob(max(0.2, 1.0 - spurious * 0.3))

    def wellformedness_grade(self) -> Grade:
        """Overall f-structure wellformedness.

        = completeness_grade ⊗ coherence_grade ⊗ feature consistency.
        """
        return self.completeness_grade() * self.coherence_grade() * self.grade

    # ── EventTerm conversion ─────────────────────────────────────────────

    def to_event_term(self) -> EventTerm:
        """Convert f-structure to EventTerm (analytic direction output).

        PRED → frame_type_name
        SUBJ → agent/experiencer/theme role
        OBJ  → patient/theme role
        TENSE → TenseTerm wrapper
        ADJ → manner/location modifiers
        """
        frame_name = self.pred or "Unknown"
        # Clean up predicate name to frame format
        frame_name = frame_name.replace(" ", "_").title()
        if not frame_name.endswith("ing") and frame_name[0].isupper():
            pass  # keep as-is

        roles: Dict[str, HLF] = {}

        # Map GFs to roles
        if self.subj:
            role_name = self._role_mapping.get("SUBJ", "agent")
            roles[role_name] = self._fstruct_to_hlf(self.subj)

        if self.obj:
            role_name = self._role_mapping.get("OBJ", "theme")
            roles[role_name] = self._fstruct_to_hlf(self.obj)

        if self.obj_th:
            role_name = self._role_mapping.get("OBJ-TH", "recipient")
            roles[role_name] = self._fstruct_to_hlf(self.obj_th)

        for obl_role, obl_fs in self.obliques.items():
            roles[obl_role] = self._fstruct_to_hlf(obl_fs)

        for adj_fs in self.adjuncts:
            if adj_fs.pred:
                adj_role = adj_fs._role_mapping.get("role", adj_fs.pred)
                roles[adj_role] = self._fstruct_to_hlf(adj_fs)

        event_var = Var("e")
        et = EventTerm(
            frame_type_name=frame_name,
            event_var=event_var,
            roles=roles,
            grade=self.wellformedness_grade(),
        )

        # Wrap in tense if specified
        tense_val = self.features.value("Tense")
        if tense_val and tense_val != "present":
            return TenseTerm(tense=tense_val, body=et, grade=self.grade)

        return et

    @classmethod
    def from_event_term(cls, et: HLF) -> "FStructure":
        """Build f-structure from EventTerm (generative direction input).

        Inverse of to_event_term: maps EventTerm roles back to GFs.
        """
        # Unwrap tense
        tense_val = "present"
        aspect_val = "simple"
        inner = et

        while True:
            if isinstance(inner, TenseTerm):
                tense_val = inner.tense
                inner = inner.body
            elif isinstance(inner, AspectTerm):
                aspect_val = inner.aspect
                inner = inner.body
            elif isinstance(inner, NegTerm):
                inner = inner.body
            else:
                break

        if not isinstance(inner, EventTerm):
            # Wrap non-event in minimal f-structure
            fs = cls(pred=str(inner), grade=inner.grade)
            fs.features.set("Tense", tense_val)
            return fs

        fs = cls(
            pred=inner.frame_type_name,
            grade=inner.grade,
            features=FeatureBundle({
                "Tense": Feature("Tense", tense_val, Grade.perfect(), True),
                "Aspect": Feature("Aspect", aspect_val, Grade.perfect(), True),
            }),
        )

        # Map roles to GFs
        role_to_gf = _infer_role_to_gf(inner.roles)

        for role_name, hlf_val in inner.roles.items():
            gf = role_to_gf.get(role_name, "ADJ")
            child_fs = cls._hlf_to_fstruct(hlf_val)
            child_fs._role_mapping["role"] = role_name

            if gf == GrammaticalFunction.SUBJ:
                fs.subj = child_fs
                fs._role_mapping["SUBJ"] = role_name
            elif gf == GrammaticalFunction.OBJ:
                fs.obj = child_fs
                fs._role_mapping["OBJ"] = role_name
            elif gf == GrammaticalFunction.OBJ_TH:
                fs.obj_th = child_fs
                fs._role_mapping["OBJ-TH"] = role_name
            elif gf == GrammaticalFunction.OBL:
                fs.obliques[role_name] = child_fs
            else:
                child_fs._role_mapping["role"] = role_name
                fs.adjuncts.append(child_fs)

        return fs

    # ── Private helpers ──────────────────────────────────────────────────

    def _guess_subcat(self) -> Dict[str, Any]:
        """Heuristically determine subcategorisation frame from pred name."""
        if not self.pred:
            return SUBCATEGORISATION_FRAMES["intransitive"]
        p = self.pred.lower()
        if self.obj_th:
            return SUBCATEGORISATION_FRAMES["ditransitive"]
        if self.obj:
            return SUBCATEGORISATION_FRAMES["transitive"]
        if self.xcomp:
            return SUBCATEGORISATION_FRAMES["control"]
        return SUBCATEGORISATION_FRAMES["intransitive"]

    @staticmethod
    def _fstruct_to_hlf(fs: "FStructure") -> HLF:
        """Convert a sub-f-structure to an HLF term."""
        if fs.referent:
            return Const(name=fs.referent, grade=fs.grade)
        if fs.pred:
            # Recursive: embedded clause
            return fs.to_event_term()
        return Const(name="?", grade=Grade.from_prob(0.3))

    @classmethod
    def _hlf_to_fstruct(cls, hlf: HLF) -> "FStructure":
        """Convert an HLF value to a sub-f-structure."""
        if isinstance(hlf, Const):
            return cls(referent=hlf.name, grade=hlf.grade)
        if isinstance(hlf, Var):
            return cls(referent=hlf.name, grade=hlf.grade)
        if isinstance(hlf, EventTerm):
            return cls.from_event_term(hlf)
        return cls(referent=str(hlf), grade=hlf.grade)

    # ── Display ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        parts = []
        if self.pred:
            parts.append(f"PRED='{self.pred}'")
        if self.subj:
            parts.append(f"SUBJ={self.subj!r}")
        if self.obj:
            parts.append(f"OBJ={self.obj!r}")
        if self.obj_th:
            parts.append(f"OBJ-TH={self.obj_th!r}")
        for k, v in self.obliques.items():
            parts.append(f"OBL-{k}={v!r}")
        if self.adjuncts:
            parts.append(f"ADJ={self.adjuncts!r}")
        tense = self.features.value("Tense")
        if tense:
            parts.append(f"TENSE={tense}")
        return f"f[{', '.join(parts)}]"

    def compact(self) -> str:
        """Short human-readable string."""
        parts = []
        if self.pred:
            parts.append(self.pred)
        if self.subj and self.subj.referent:
            parts.append(f"subj={self.subj.referent}")
        if self.obj and self.obj.referent:
            parts.append(f"obj={self.obj.referent}")
        return f"({', '.join(parts)})"

    def gf_map(self) -> Dict[str, Optional["FStructure"]]:
        """All grammatical functions as a dict."""
        result: Dict[str, Optional[FStructure]] = {
            "SUBJ": self.subj,
            "OBJ": self.obj,
            "OBJ-TH": self.obj_th,
            "XCOMP": self.xcomp,
            "COMP": self.comp,
        }
        for k, v in self.obliques.items():
            result[f"OBL-{k}"] = v
        return result


# ══════════════════════════════════════════════════════════════════════════
# φ correspondence
# ══════════════════════════════════════════════════════════════════════════


class PhiCorrespondence:
    """The φ function: c-structure node → f-structure.

    In LFG, c-structure trees are annotated with functional equations
    that determine how each node contributes to f-structure.
    Grade = equation satisfaction grade.
    """

    def apply(
        self, parse_tree: Any, lexicon: Optional[Dict] = None
    ) -> Tuple[FStructure, Grade]:
        """Apply all annotated equations to build f-structure from parse tree.

        This is a simplified version that works with dict-based parse trees
        or SyntacticObject trees from the minimalism module.
        """
        from gofai_chat.grammar.minimalism import SyntacticObject

        if isinstance(parse_tree, SyntacticObject):
            return self._from_syntactic_object(parse_tree)

        # Dict-based parse tree
        if isinstance(parse_tree, dict):
            return self._from_dict_tree(parse_tree, lexicon or {})

        return FStructure(grade=Grade.from_prob(0.3)), Grade.from_prob(0.3)

    def _from_syntactic_object(
        self, so: "SyntacticObject"
    ) -> Tuple[FStructure, Grade]:
        """Convert a SyntacticObject tree to f-structure."""
        from gofai_chat.grammar.minimalism import SyntacticObject

        if so.is_terminal:
            fs = FStructure(
                referent=so.word if so.word else None,
                features=so.features.copy(),
                grade=so.grade,
            )
            if so.label == "V":
                fs.pred = so.word
            return fs, so.grade

        # Recursive: process children and assemble
        child_fstructs = []
        for child in so.children:
            cfs, cg = self._from_syntactic_object(child)
            child_fstructs.append((cfs, cg, child))

        # Build f-structure from children
        fs = FStructure(grade=so.grade)

        for cfs, cg, child_so in child_fstructs:
            if child_so.label in ("V", "VP"):
                fs.pred = cfs.pred or cfs.referent
                fs.features = cfs.features.copy()
            elif child_so.label in ("D", "DP", "NP", "N"):
                # Determine GF from position
                if so.head and child_so is not so.head:
                    spec = so.specifier()
                    if spec and child_so is spec:
                        fs.subj = cfs
                    else:
                        if not fs.obj:
                            fs.obj = cfs
                        else:
                            fs.obj_th = cfs
            elif child_so.label in ("PP",):
                role = cfs.pred or "location"
                fs.obliques[role] = cfs
            elif child_so.label in ("TP", "CP", "vP", "v*P"):
                # Inherit from clausal child
                if cfs.pred and not fs.pred:
                    fs.pred = cfs.pred
                if cfs.subj and not fs.subj:
                    fs.subj = cfs.subj
                if cfs.obj and not fs.obj:
                    fs.obj = cfs.obj
                if cfs.features.features:
                    for k, v in cfs.features.features.items():
                        if not fs.features.has(k):
                            fs.features.features[k] = v

        overall = Grade.product([cg for _, cg, _ in child_fstructs])
        fs.grade = overall
        return fs, overall

    def _from_dict_tree(
        self, tree: Dict, lexicon: Dict
    ) -> Tuple[FStructure, Grade]:
        """Convert dict-based parse tree to f-structure."""
        fs = FStructure(grade=Grade.from_prob(0.8))
        if "word" in tree:
            fs.referent = tree["word"]
            fs.pred = tree.get("pred", tree["word"])
        return fs, Grade.from_prob(0.8)


# ══════════════════════════════════════════════════════════════════════════
# Role-to-GF inference
# ══════════════════════════════════════════════════════════════════════════

_SUBJECT_ROLES = frozenset({
    "agent", "experiencer", "cognizer", "perceiver",
    "student", "author", "leader", "resident", "child",
    "founder", "winner", "teacher", "protagonist",
    "speaker", "sender", "creator", "owner", "possessor",
    "observer", "worker", "performer", "actor",
    "writer", "reader", "buyer", "seller",
    "builder", "maker", "solver", "knower",
    "thinker", "feeler", "doer", "follower",
    "causer",
})

_OBJECT_ROLES = frozenset({
    "theme", "patient", "phenomenon", "content",
    "topic", "subject_matter", "goal", "attribute",
    "product", "result", "entity", "effect",
    "stimulus", "target", "object", "message",
    "text", "work", "idea", "concept", "material",
    "substance",
})

_OBLIQUE_ROLES = frozenset({
    "location", "source", "path", "direction",
    "instrument", "cause", "purpose", "beneficiary",
    "manner", "time", "duration", "frequency",
    "degree", "medium", "standard", "comitative",
})

_RECIPIENT_ROLES = frozenset({
    "recipient", "addressee", "beneficiary", "goal",
})


def _infer_role_to_gf(roles: Dict[str, Any]) -> Dict[str, str]:
    """Infer which GF each EventTerm role maps to."""
    mapping: Dict[str, str] = {}
    subj_assigned = False
    obj_assigned = False

    for role_name in roles:
        if role_name in _SUBJECT_ROLES and not subj_assigned:
            mapping[role_name] = GrammaticalFunction.SUBJ
            subj_assigned = True
        elif role_name in _OBJECT_ROLES and not obj_assigned:
            mapping[role_name] = GrammaticalFunction.OBJ
            obj_assigned = True
        elif role_name in _RECIPIENT_ROLES:
            mapping[role_name] = GrammaticalFunction.OBJ_TH
        elif role_name in _OBLIQUE_ROLES:
            mapping[role_name] = GrammaticalFunction.OBL
        else:
            mapping[role_name] = GrammaticalFunction.ADJ

    return mapping
