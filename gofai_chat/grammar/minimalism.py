"""Minimalist Program syntax: Merge, Agree, phases, feature checking.

After:
  Chomsky (1995) *The Minimalist Program*
  Chomsky (2000) "Minimalist Inquiries: The Framework"
  Chomsky (2001) "Derivation by Phase"
  Chomsky (2008) "On Phases"
  Bošković (2007) "On the Locality and Motivation of Move and Agree"

Grade semiring interpretation:
  - Hard constraints (Full Interpretation, Inclusiveness) → Grade ∈ {0,1}
  - Soft/gradable constraints (economy, anti-locality) → Grade ∈ [0,1]
  - AGREE probe-goal matching → Grade = feature-match Grade
  - MERGE grade = Grade(syntactic type compatibility)

Every syntactic object (SO) carries a Grade reflecting the wellformedness
of the derivation that built it.  The final Grade of a derivation is the
product of all Merge, Agree, and phase-transfer grades.
"""
from __future__ import annotations

__all__ = [
    "SyntacticObject",
    "MergeResult",
    "Merge",
    "Agree",
    "PhaseEngine",
    "CATEGORY_LABELS",
    "build_clause",
]

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

from gofai_chat.core.grade import Grade
from gofai_chat.grammar.features import (
    Feature,
    FeatureBundle,
    PhiFeatures,
    CaseFeature,
    CASE_NOMINATIVE,
    CASE_ACCUSATIVE,
    CASE_DATIVE,
)


# ══════════════════════════════════════════════════════════════════════════
# Category labels used in the extended projection
# ══════════════════════════════════════════════════════════════════════════

CATEGORY_LABELS: Dict[str, str] = {
    "C": "Complementiser",
    "T": "Tense",
    "v": "little-v (light verb)",
    "v*": "little-v* (transitive, phase head)",
    "V": "Verb (lexical)",
    "D": "Determiner (phase head)",
    "N": "Noun",
    "A": "Adjective",
    "P": "Preposition",
    "Adv": "Adverb",
    "Neg": "Negation",
    "Top": "Topic",
    "Foc": "Focus",
    "Fin": "Finiteness",
    "Force": "Force (clause type)",
}


# ══════════════════════════════════════════════════════════════════════════
# SyntacticObject — atoms and derived structures
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class SyntacticObject:
    """A syntactic object (SO) — the atoms and derived structures of Merge.

    Atoms: lexical items with feature bundles (leaves of the tree).
    Derived: {SO1, SO2} from Merge(SO1, SO2) with a projecting head.

    After Chomsky (1995) ch. 4: a syntactic object is either a lexical
    item LI or a set {α, β} formed by Merge.
    """

    label: str = ""
    features: FeatureBundle = field(default_factory=FeatureBundle)
    grade: Grade = field(default_factory=Grade.perfect)
    children: List["SyntacticObject"] = field(default_factory=list)
    head: Optional["SyntacticObject"] = None
    is_phase_head: bool = False
    word: str = ""  # surface lexical material (for terminals)
    moved_from: Optional["SyntacticObject"] = None  # copy theory trace

    @property
    def is_terminal(self) -> bool:
        return len(self.children) == 0

    @property
    def is_maximal(self) -> bool:
        """A maximal projection cannot project further."""
        return self.label.endswith("P") or self.label in ("S", "CP", "TP", "vP", "VP", "DP", "NP", "PP", "AP", "AdvP")

    def specifier(self) -> Optional["SyntacticObject"]:
        """The specifier (non-head daughter of a binary-branching node)."""
        if len(self.children) == 2 and self.head:
            for c in self.children:
                if c is not self.head:
                    return c
        return None

    def complement(self) -> Optional["SyntacticObject"]:
        """The complement (sister of the head)."""
        if len(self.children) >= 1 and self.head:
            for c in self.children:
                if c is not self.head and c != self.specifier():
                    return c
            # Binary: complement = non-spec child if head projects through one child
            if len(self.children) == 2:
                for c in self.children:
                    if c is not self.head:
                        return c
        return None

    def c_commands(self, target: "SyntacticObject") -> bool:
        """Check if self c-commands target (first branching node dominating
        self also dominates target)."""
        # Simplified: check if target is in the subtree rooted at self's sister
        return target in self._all_descendants_of_sister()

    def _all_descendants_of_sister(self) -> Set["SyntacticObject"]:
        """All nodes in the subtree of self's sister."""
        # This requires parent info; simplified version
        return set()

    def terminals(self) -> List["SyntacticObject"]:
        """Yield all terminal nodes in left-to-right order."""
        if self.is_terminal:
            return [self]
        result: List[SyntacticObject] = []
        for child in self.children:
            result.extend(child.terminals())
        return result

    def depth(self) -> int:
        if self.is_terminal:
            return 0
        return 1 + max((c.depth() for c in self.children), default=0)

    def __repr__(self) -> str:
        if self.is_terminal:
            return f"[{self.label} {self.word}]" if self.word else f"[{self.label}]"
        kids = " ".join(repr(c) for c in self.children)
        return f"[{self.label} {kids}]"


# ══════════════════════════════════════════════════════════════════════════
# MergeResult
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class MergeResult:
    """Result of a Merge operation with its Grade."""

    so: SyntacticObject
    grade: Grade = field(default_factory=Grade.perfect)
    operation: str = "external_merge"


# ══════════════════════════════════════════════════════════════════════════
# Merge — External and Internal
# ══════════════════════════════════════════════════════════════════════════


class Merge:
    """External and Internal Merge (Chomsky 1995, 2000).

    External Merge: combines two independent SOs → new SO (base generation).
        Grade = selectional compatibility between head and complement.
    Internal Merge: takes a subterm and merges it to the edge → movement.
        Grade = EPP/case-driven movement legitimacy.

    Selectional restrictions:
      V selects DP complement → Grade.perfect()
      V selects PP complement → Grade.perfect() if specified
      T selects vP/VP → Grade.perfect()
      C selects TP → Grade.perfect()
      Type mismatch → attenuated Grade
    """

    # Selectional compatibility: head_label → set of acceptable complement labels
    _SELECTION: Dict[str, Set[str]] = {
        "V": {"DP", "NP", "PP", "CP", "AP", "AdvP", "S", "N", "D"},
        "v": {"VP", "V"},
        "v*": {"VP", "V"},
        "T": {"vP", "VP", "v", "v*", "NegP", "Neg"},
        "C": {"TP", "T", "FinP", "Fin"},
        "D": {"NP", "N", "AP", "A"},
        "N": {"PP", "CP", "P", "C"},
        "A": {"PP", "DP", "NP", "CP", "P", "D", "N", "C"},
        "P": {"DP", "NP", "D", "N", "CP", "C"},
        "Neg": {"vP", "VP", "TP", "v", "v*", "V", "T"},
        "Adv": {"VP", "AP", "AdvP", "V", "A", "Adv"},
        "Top": {"TP", "CP", "T", "C"},
        "Foc": {"TP", "CP", "T", "C"},
    }

    def external_merge(
        self, head: SyntacticObject, complement: SyntacticObject
    ) -> MergeResult:
        """Build XP from head and complement (or specifier).

        The label of the new SO is the label of the head (labelling algorithm).
        Grade reflects selectional match.
        """
        sel_grade = self._selectional_grade(head, complement)

        projected_label = self._project_label(head)
        new_so = SyntacticObject(
            label=projected_label,
            features=head.features.copy(),
            grade=head.grade * complement.grade * sel_grade,
            children=[head, complement],
            head=head,
            is_phase_head=head.is_phase_head,
        )
        return MergeResult(so=new_so, grade=sel_grade, operation="external_merge")

    def internal_merge(
        self, so: SyntacticObject, mover: SyntacticObject,
        landing_label: str = "",
    ) -> MergeResult:
        """Displace mover to the edge of so (A-/A'-movement, copy theory).

        After Chomsky (2000): Internal Merge is feature-driven — a probe
        on so triggers movement of a goal (mover) to [Spec, so].

        Grade = Grade(EPP satisfaction) * Grade(locality).
        Longer movement → lower Grade (Relativized Minimality penalty).
        """
        movement_distance = so.depth()
        locality_penalty = max(0.5, 1.0 - movement_distance * 0.05)

        # Create a copy of the mover (copy theory of movement)
        copy = SyntacticObject(
            label=mover.label,
            features=mover.features.copy(),
            grade=mover.grade,
            children=mover.children,
            head=mover.head,
            word=mover.word,
            moved_from=mover,
        )

        projected = landing_label or self._project_label(so)
        new_so = SyntacticObject(
            label=projected,
            features=so.features.copy(),
            grade=so.grade * Grade.from_prob(locality_penalty),
            children=[copy, so],
            head=so.head or so,
            is_phase_head=so.is_phase_head,
        )
        move_grade = Grade.from_prob(locality_penalty)
        return MergeResult(so=new_so, grade=move_grade, operation="internal_merge")

    def _selectional_grade(
        self, head: SyntacticObject, complement: SyntacticObject
    ) -> Grade:
        """Grade of selectional compatibility between head and complement."""
        acceptable = self._SELECTION.get(head.label, set())
        if not acceptable:
            return Grade.from_prob(0.7)  # unknown head: mild penalty
        if complement.label in acceptable:
            return Grade.perfect()
        # Partial match (e.g. bare N where DP expected)
        return Grade.from_prob(0.6)

    @staticmethod
    def _project_label(head: SyntacticObject) -> str:
        """Determine the label of the projection.

        X → X' (intermediate) → XP (maximal).
        Simplified: terminals project to XP directly.
        """
        lbl = head.label
        if lbl.endswith("P") or lbl.endswith("'"):
            return lbl
        return lbl + "P"


# ══════════════════════════════════════════════════════════════════════════
# Agree — probe-goal feature valuation
# ══════════════════════════════════════════════════════════════════════════


class Agree:
    """AGREE operation: probe-goal feature valuation (Chomsky 2000, 2001).

    A probe P with unvalued feature [uF] searches its c-command domain
    for a goal G with valued [F:val].

    Applications:
      - Subject-verb agreement: T[uφ] agrees with DP[φ:3sg]
      - Case assignment: T assigns Nominative to [Spec,TP]
      - Object agreement: v* assigns Accusative to complement DP

    Grade(Agree(P,G)) = Grade(feature_match(P.uF, G.F)) — how well the
    probe's requirements are satisfied by the goal.
    """

    def agree(
        self, probe: SyntacticObject, goal: SyntacticObject
    ) -> Tuple[FeatureBundle, Grade]:
        """Value uninterpretable features on probe from goal's features.

        Returns (valued_features, agreement_grade).
        The probe's uninterpretable features get valued by the goal's
        interpretable features.
        """
        valued = probe.features.copy()
        agree_grade = Grade.perfect()

        for name, feat in probe.features.features.items():
            if not feat.interpretable:
                # Look for valued feature on goal
                goal_feat = goal.features.get(name)
                if goal_feat and goal_feat.interpretable:
                    valued.set(name, goal_feat.value, goal_feat.grade, True)
                    agree_grade = agree_grade * goal_feat.grade
                else:
                    # Feature not valued → penalty
                    agree_grade = agree_grade * Grade.from_prob(0.3)

        # φ-feature agreement (Person, Number, Gender)
        phi_probe = PhiFeatures.from_bundle(probe.features)
        phi_goal = PhiFeatures.from_bundle(goal.features)
        phi_grade = phi_probe.agrees_with(phi_goal)
        agree_grade = agree_grade * phi_grade

        return valued, agree_grade

    def find_goal(
        self, probe: SyntacticObject, domain: SyntacticObject,
        feature_name: str = "",
    ) -> Optional[Tuple[SyntacticObject, Grade]]:
        """Grade-based goal search in c-command domain.

        Searches the complement domain of the probe for the closest
        goal bearing a valued instance of the required feature.
        Grade = proximity × feature-match.
        """
        candidates: List[Tuple[SyntacticObject, Grade, int]] = []
        self._search_domain(domain, feature_name, candidates, depth=0)

        if not candidates:
            return None

        # Closest goal wins (Relativized Minimality)
        candidates.sort(key=lambda x: (x[2], -x[1].to_prob()))
        best = candidates[0]
        return best[0], best[1]

    def _search_domain(
        self,
        so: SyntacticObject,
        feature_name: str,
        results: List[Tuple[SyntacticObject, Grade, int]],
        depth: int,
    ) -> None:
        """Recursively search for goals in the c-command domain."""
        if so.is_terminal:
            if feature_name:
                feat = so.features.get(feature_name)
                if feat and feat.interpretable:
                    proximity = max(0.5, 1.0 - depth * 0.05)
                    results.append((so, feat.grade * Grade.from_prob(proximity), depth))
            else:
                # Any DP/NP is a potential goal
                if so.label in ("D", "N", "DP", "NP"):
                    proximity = max(0.5, 1.0 - depth * 0.05)
                    results.append((so, Grade.from_prob(proximity), depth))
            return

        for child in so.children:
            self._search_domain(child, feature_name, results, depth + 1)

    def assign_case(
        self, assigner: SyntacticObject, dp: SyntacticObject,
        case: CaseFeature,
    ) -> Grade:
        """Assign structural or inherent case to a DP.

        T assigns Nominative to its specifier (subject).
        v* assigns Accusative to its complement (object).
        V can assign inherent case (dative/locative) to oblique arguments.

        Returns Grade of the case assignment.
        """
        dp.features.set("Case", case.case_type, case.grade, False)
        return case.grade


# ══════════════════════════════════════════════════════════════════════════
# Phase theory
# ══════════════════════════════════════════════════════════════════════════


class PhaseEngine:
    """Phase theory (Chomsky 2001): vP and CP are phases.

    Phase Impenetrability Condition (PIC): once a phase head H is merged,
    the complement of H is transferred to PF/LF and becomes inaccessible.

    Grade version: PIC violation = Grade penalty proportional to depth
    of extraction from within a phase.
    """

    PHASE_HEADS: frozenset = frozenset({"C", "v*", "D"})

    def is_phase_head(self, so: SyntacticObject) -> bool:
        return so.label in self.PHASE_HEADS or so.is_phase_head

    def spell_out(
        self, phase: SyntacticObject
    ) -> Tuple[List[SyntacticObject], Grade]:
        """Transfer phase complement to interfaces.

        Returns (transferred_terminals, transfer_grade).
        The edge (specifier + head) remains accessible; the complement
        domain is transferred.
        """
        comp = phase.complement()
        if comp is None:
            return [], Grade.perfect()

        transferred = comp.terminals()
        # Transfer grade: perfect if all features are checked
        unchecked = sum(
            1 for t in transferred
            for f in t.features.features.values()
            if not f.interpretable and f.value == "_"
        )
        if unchecked:
            transfer_grade = Grade.from_prob(max(0.3, 1.0 - unchecked * 0.1))
        else:
            transfer_grade = Grade.perfect()

        return transferred, transfer_grade

    def pic_violation_grade(
        self, mover: SyntacticObject, source_phase: SyntacticObject
    ) -> Grade:
        """Grade of PIC violation when extracting from a phase.

        Extraction from the complement of a phase head violates PIC.
        Grade penalty scales with the depth of the extraction.
        """
        depth = source_phase.depth()
        penalty = max(0.1, 1.0 - depth * 0.15)
        return Grade.from_prob(penalty)


# ══════════════════════════════════════════════════════════════════════════
# Convenience: build a simple clause derivation
# ══════════════════════════════════════════════════════════════════════════


def build_clause(
    verb: str,
    subject: str,
    obj: Optional[str] = None,
    tense: str = "present",
    person: str = "3",
    number: str = "sg",
) -> Tuple[SyntacticObject, Grade]:
    """Build a basic clause derivation: [CP [TP subj [T' T [vP [v' v [VP V obj]]]]]]

    Returns (clause_SO, derivation_grade).
    """
    merger = Merge()
    agree_op = Agree()

    # Build lexical items
    v_so = SyntacticObject(
        label="V", word=verb,
        features=FeatureBundle({
            "Cat": Feature("Cat", "V", Grade.perfect(), True),
        }),
    )

    subj_so = SyntacticObject(
        label="D", word=subject,
        features=FeatureBundle({
            "Cat": Feature("Cat", "D", Grade.perfect(), True),
            "Person": Feature("Person", person, Grade.perfect(), True),
            "Number": Feature("Number", number, Grade.perfect(), True),
        }),
    )

    overall = Grade.perfect()

    # VP: V + obj (if transitive)
    if obj:
        obj_so = SyntacticObject(
            label="D", word=obj,
            features=FeatureBundle({
                "Cat": Feature("Cat", "D", Grade.perfect(), True),
                "Person": Feature("Person", "3", Grade.perfect(), True),
                "Number": Feature("Number", "sg", Grade.perfect(), True),
            }),
        )
        vp_result = merger.external_merge(v_so, obj_so)
        overall = overall * vp_result.grade
        vp = vp_result.so
        # v* assigns Accusative to object
        agree_op.assign_case(v_so, obj_so, CASE_ACCUSATIVE)
    else:
        vp = SyntacticObject(label="VP", children=[v_so], head=v_so)

    # v*P: v* + VP
    little_v = SyntacticObject(
        label="v*", is_phase_head=True,
        features=FeatureBundle({
            "Cat": Feature("Cat", "v*", Grade.perfect(), True),
        }),
    )
    vp_merge = merger.external_merge(little_v, vp)
    overall = overall * vp_merge.grade

    # TP: T + vP
    t_so = SyntacticObject(
        label="T",
        features=FeatureBundle({
            "Cat": Feature("Cat", "T", Grade.perfect(), True),
            "Tense": Feature("Tense", tense, Grade.perfect(), True),
            "Person": Feature("Person", "_", Grade.perfect(), False),
            "Number": Feature("Number", "_", Grade.perfect(), False),
        }),
    )
    tp_merge = merger.external_merge(t_so, vp_merge.so)
    overall = overall * tp_merge.grade

    # Agree: T probes vP for φ-features on subject
    _, agree_grade = agree_op.agree(t_so, subj_so)
    overall = overall * agree_grade

    # Assign Nominative to subject
    agree_op.assign_case(t_so, subj_so, CASE_NOMINATIVE)

    # Internal Merge: subject moves to [Spec,TP]
    tp_with_subj = merger.internal_merge(tp_merge.so, subj_so, "TP")
    overall = overall * tp_with_subj.grade

    # CP: C + TP
    c_so = SyntacticObject(
        label="C", is_phase_head=True,
        features=FeatureBundle({
            "Cat": Feature("Cat", "C", Grade.perfect(), True),
        }),
    )
    cp_merge = merger.external_merge(c_so, tp_with_subj.so)
    overall = overall * cp_merge.grade

    return cp_merge.so, overall
