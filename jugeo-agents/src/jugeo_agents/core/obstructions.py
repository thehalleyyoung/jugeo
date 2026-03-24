"""Obstruction Classifier — classifying multi-agent failures into cohomology classes.

Detects and classifies obstructions that arise when multiple LLM agents
produce contradictory, incomplete, or ungrounded outputs.  Each obstruction
is assigned a cohomology class (H0 / H1 / H2 / PHANTOM) that determines
the appropriate repair strategy.
"""

from __future__ import annotations

import itertools
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from jugeo_agents.types import (
    AgentOutput,
    CohomologyClass,
    Contradiction,
    FactualClaim,
    Obstruction,
    ObstructionKind,
    TrustLevel,
)

# ---------------------------------------------------------------------------
# Severity weights per cohomology class (higher = worse)
# ---------------------------------------------------------------------------

_CLASS_SEVERITY: dict[CohomologyClass, float] = {
    CohomologyClass.H0: 0.3,
    CohomologyClass.H1: 0.6,
    CohomologyClass.H2: 0.85,
    CohomologyClass.PHANTOM: 0.95,
}

_KIND_SEVERITY: dict[ObstructionKind, float] = {
    ObstructionKind.SECTION_INCOMPLETE: 0.2,
    ObstructionKind.COVER_GAP: 0.25,
    ObstructionKind.TEMPORAL_CONTRADICTION: 0.5,
    ObstructionKind.QUANTITATIVE_CONTRADICTION: 0.55,
    ObstructionKind.DIRECTIONAL_CONTRADICTION: 0.6,
    ObstructionKind.ENTITY_CONTRADICTION: 0.65,
    ObstructionKind.LOGICAL_CONTRADICTION: 0.7,
    ObstructionKind.DEPENDENCY_CONTRADICTION: 0.6,
    ObstructionKind.TYPE_MISMATCH: 0.45,
    ObstructionKind.TRUST_BOUNDARY_VIOLATION: 0.5,
    ObstructionKind.CASCADING_HALLUCINATION: 0.85,
    ObstructionKind.PHANTOM_GLOBAL_SECTION: 0.9,
    ObstructionKind.CONTEXT_OVERFLOW: 0.35,
    ObstructionKind.INFINITE_LOOP: 0.4,
    ObstructionKind.TOOL_HALLUCINATION: 0.75,
}

# ObstructionKind → default CohomologyClass mapping
_KIND_TO_CLASS: dict[ObstructionKind, CohomologyClass] = {
    ObstructionKind.SECTION_INCOMPLETE: CohomologyClass.H0,
    ObstructionKind.COVER_GAP: CohomologyClass.H0,
    ObstructionKind.CONTEXT_OVERFLOW: CohomologyClass.H0,
    ObstructionKind.INFINITE_LOOP: CohomologyClass.H0,
    ObstructionKind.TEMPORAL_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.QUANTITATIVE_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.DIRECTIONAL_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.ENTITY_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.LOGICAL_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.DEPENDENCY_CONTRADICTION: CohomologyClass.H1,
    ObstructionKind.TYPE_MISMATCH: CohomologyClass.H1,
    ObstructionKind.TRUST_BOUNDARY_VIOLATION: CohomologyClass.H1,
    ObstructionKind.CASCADING_HALLUCINATION: CohomologyClass.H2,
    ObstructionKind.TOOL_HALLUCINATION: CohomologyClass.H2,
    ObstructionKind.PHANTOM_GLOBAL_SECTION: CohomologyClass.PHANTOM,
}

# Repair suggestion templates keyed by cohomology class
_REPAIR_TEMPLATES: dict[CohomologyClass, list[str]] = {
    CohomologyClass.H0: [
        "Re-query missing agents to fill coverage gaps.",
        "Split the task into smaller subtasks to avoid context overflow.",
    ],
    CohomologyClass.H1: [
        "Introduce a debate round between conflicting agents.",
        "Escalate contradictions to a higher-trust evidence source.",
        "Apply trust-weighted majority voting on conflicting claims.",
    ],
    CohomologyClass.H2: [
        "Trace the hallucination chain back to the root ungrounded claim.",
        "Require tool-verified evidence for every claim in the cascade.",
        "Re-run the pipeline with grounded-only constraints.",
    ],
    CohomologyClass.PHANTOM: [
        "Require at least one RAG-grounded or tool-verified source.",
        "Challenge the global section with adversarial counter-queries.",
        "Introduce an external evidence check before accepting output.",
    ],
}


# =========================================================================
# ObstructionClassifier
# =========================================================================


class ObstructionClassifier:
    """Classify detected issues into cohomology classes.

    The classifier maps raw contradictions and agent sections into the four
    cohomology classes of multi-agent failure:

    * **H0** — section incompleteness (missing agent outputs)
    * **H1** — pairwise contradictions between agents
    * **H2** — cascading hallucinations (locally consistent, globally fabricated)
    * **PHANTOM** — phantom global sections (everything consistent but ungrounded)
    """

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def classify(
        self,
        contradictions: list[Contradiction],
        sections: list[AgentOutput],
        trust_info: dict[str, Any],
    ) -> list[Obstruction]:
        """Run full classification pipeline and return all obstructions."""
        expected_agents: set[str] = set(trust_info.get("expected_agents", set()))
        obstructions: list[Obstruction] = []

        obstructions.extend(self.detect_h0(sections, expected_agents))
        obstructions.extend(self.detect_h1(contradictions))
        obstructions.extend(self.detect_h2(sections, contradictions))
        obstructions.extend(self.detect_phantom(sections))

        return obstructions

    def classify_single(self, contradiction: Contradiction) -> CohomologyClass:
        """Classify a single contradiction into its cohomology class."""
        return _KIND_TO_CLASS.get(contradiction.kind, CohomologyClass.H1)

    # --------------------------------------------------------------------- #
    # H0 — section incompleteness
    # --------------------------------------------------------------------- #

    def detect_h0(
        self,
        sections: list[AgentOutput],
        expected_agents: set[str],
    ) -> list[Obstruction]:
        """Detect agents that were expected but did not produce output."""
        present = {s.agent_id for s in sections}
        missing = expected_agents - present

        obstructions: list[Obstruction] = []
        for agent_id in sorted(missing):
            obstructions.append(
                Obstruction(
                    kind=ObstructionKind.SECTION_INCOMPLETE,
                    cohomology=CohomologyClass.H0,
                    agents_involved=[agent_id],
                    description=f"Agent '{agent_id}' did not produce output.",
                    repair_frontier=[
                        f"Re-query agent '{agent_id}' or assign its subtask to another agent.",
                    ],
                )
            )

        # Also detect empty outputs
        for section in sections:
            if not section.output_text.strip():
                obstructions.append(
                    Obstruction(
                        kind=ObstructionKind.SECTION_INCOMPLETE,
                        cohomology=CohomologyClass.H0,
                        agents_involved=[section.agent_id],
                        description=(
                            f"Agent '{section.agent_id}' produced an empty output."
                        ),
                        repair_frontier=[
                            f"Re-run agent '{section.agent_id}' with a refined prompt.",
                        ],
                    )
                )

        return obstructions

    # --------------------------------------------------------------------- #
    # H1 — pairwise contradictions
    # --------------------------------------------------------------------- #

    def detect_h1(
        self,
        contradictions: list[Contradiction],
    ) -> list[Obstruction]:
        """Group pairwise contradictions into H1 obstructions.

        Contradictions sharing a common agent or common claim subject are
        grouped together so that downstream repair can address them as a
        single coherent conflict.
        """
        # Build an adjacency structure: group contradictions by shared subject
        subject_groups: dict[str, list[Contradiction]] = defaultdict(list)
        ungrouped: list[Contradiction] = []

        for c in contradictions:
            cohom = self.classify_single(c)
            if cohom != CohomologyClass.H1:
                continue
            subject = c.claim_a.subject or c.claim_b.subject
            if subject:
                subject_groups[subject].append(c)
            else:
                ungrouped.append(c)

        obstructions: list[Obstruction] = []

        # Grouped by subject
        for subject, group in subject_groups.items():
            agents = sorted(
                {a for c in group for a in (c.agent_a, c.agent_b)}
            )
            hints = [c.repair_hint for c in group if c.repair_hint]
            obstructions.append(
                Obstruction(
                    kind=group[0].kind,
                    cohomology=CohomologyClass.H1,
                    agents_involved=agents,
                    contradictions=list(group),
                    description=(
                        f"{len(group)} contradiction(s) on subject '{subject}' "
                        f"involving agents: {', '.join(agents)}."
                    ),
                    repair_frontier=hints
                    or [f"Resolve conflicting claims about '{subject}'."],
                )
            )

        # Ungrouped — one obstruction each
        for c in ungrouped:
            obstructions.append(
                Obstruction(
                    kind=c.kind,
                    cohomology=CohomologyClass.H1,
                    agents_involved=sorted({c.agent_a, c.agent_b}),
                    contradictions=[c],
                    description=(
                        c.explanation
                        or f"Contradiction between '{c.agent_a}' and '{c.agent_b}'."
                    ),
                    repair_frontier=[c.repair_hint] if c.repair_hint else [],
                )
            )

        return obstructions

    # --------------------------------------------------------------------- #
    # H2 — cascading hallucinations
    # --------------------------------------------------------------------- #

    def detect_h2(
        self,
        sections: list[AgentOutput],
        contradictions: list[Contradiction],
    ) -> list[Obstruction]:
        """Detect cascading hallucinations.

        A cascading hallucination occurs when pairwise overlaps between
        agents are locally consistent, but following the chain of claims
        reveals a fabrication — no claim in the chain is grounded.

        Algorithm:
        1.  Build a claim graph: nodes are claims, edges link claims that
            share a subject across different agents.
        2.  For each connected component, check whether *any* claim in the
            component is grounded (trust >= CROSS_AGENT_CONFIRMED).
        3.  Components that are entirely ungrounded *and* span ≥ 2 agents
            are cascading hallucinations.
        4.  Additionally, look for H2-classified contradictions.
        """
        obstructions: list[Obstruction] = []

        # --- Contradiction-based H2 ---
        h2_contradictions = [
            c for c in contradictions
            if self.classify_single(c) == CohomologyClass.H2
        ]
        if h2_contradictions:
            agents = sorted(
                {a for c in h2_contradictions for a in (c.agent_a, c.agent_b)}
            )
            obstructions.append(
                Obstruction(
                    kind=ObstructionKind.CASCADING_HALLUCINATION,
                    cohomology=CohomologyClass.H2,
                    agents_involved=agents,
                    contradictions=h2_contradictions,
                    description=(
                        f"{len(h2_contradictions)} cascading hallucination(s) "
                        f"detected across agents: {', '.join(agents)}."
                    ),
                    repair_frontier=[
                        "Trace fabrication chain to root ungrounded claim.",
                        "Require tool-verified evidence for each claim.",
                    ],
                )
            )

        # --- Claim-chain analysis ---
        obstructions.extend(self._trace_claim_chains(sections))

        return obstructions

    def _trace_claim_chains(
        self,
        sections: list[AgentOutput],
    ) -> list[Obstruction]:
        """Build claim graph and detect ungrounded multi-agent chains."""
        # Collect all claims with their source agents
        claims_by_subject: dict[str, list[tuple[str, FactualClaim]]] = defaultdict(list)
        for section in sections:
            for claim in section.claims:
                subject = claim.subject or claim.text[:40]
                claims_by_subject[subject].append((section.agent_id, claim))

        # Build adjacency list for union-find over (agent_id, claim_id)
        node_to_idx: dict[str, int] = {}
        nodes: list[tuple[str, FactualClaim]] = []

        for subject, agent_claims in claims_by_subject.items():
            if len({ac[0] for ac in agent_claims}) < 2:
                continue  # need at least 2 agents
            for agent_id, claim in agent_claims:
                key = f"{agent_id}:{claim.claim_id}"
                if key not in node_to_idx:
                    node_to_idx[key] = len(nodes)
                    nodes.append((agent_id, claim))

        if not nodes:
            return []

        # Union-Find to group connected claims
        parent = list(range(len(nodes)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Connect claims that share a subject across different agents
        for _subject, agent_claims in claims_by_subject.items():
            indices = []
            for agent_id, claim in agent_claims:
                key = f"{agent_id}:{claim.claim_id}"
                if key in node_to_idx:
                    indices.append(node_to_idx[key])
            for i in range(len(indices) - 1):
                union(indices[i], indices[i + 1])

        # Collect components
        components: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(nodes)):
            components[find(idx)].append(idx)

        obstructions: list[Obstruction] = []
        for members in components.values():
            agents_in_chain = {nodes[m][0] for m in members}
            if len(agents_in_chain) < 2:
                continue

            claims_in_chain = [nodes[m][1] for m in members]
            any_grounded = any(c.trust.is_grounded for c in claims_in_chain)

            if any_grounded:
                continue  # at least one anchor — not a cascade

            agent_list = sorted(agents_in_chain)
            subjects = sorted(
                {c.subject for c in claims_in_chain if c.subject}
            )
            obstructions.append(
                Obstruction(
                    kind=ObstructionKind.CASCADING_HALLUCINATION,
                    cohomology=CohomologyClass.H2,
                    agents_involved=agent_list,
                    description=(
                        f"Ungrounded claim chain spanning {len(agent_list)} agents "
                        f"on subjects: {', '.join(subjects) or '(unlabeled)'}. "
                        f"None of the {len(claims_in_chain)} claims are grounded."
                    ),
                    repair_frontier=[
                        "Ground at least one claim with tool execution or RAG.",
                        f"Agents to re-query: {', '.join(agent_list)}.",
                    ],
                )
            )

        return obstructions

    # --------------------------------------------------------------------- #
    # PHANTOM — globally consistent but ungrounded
    # --------------------------------------------------------------------- #

    def detect_phantom(
        self,
        sections: list[AgentOutput],
    ) -> list[Obstruction]:
        """Detect phantom global sections.

        A phantom global section is produced when all agents agree (no
        contradictions) but *none* of them have grounded evidence.  The
        output looks correct but is entirely fabricated.
        """
        if not sections:
            return []

        all_claims: list[FactualClaim] = []
        for section in sections:
            all_claims.extend(section.claims)

        if not all_claims:
            return []

        any_grounded = any(c.trust.is_grounded for c in all_claims)
        if any_grounded:
            return []

        # Every agent contributed ungrounded claims — phantom section
        all_ungrounded_trust = all(
            section.trust < TrustLevel.CROSS_AGENT_CONFIRMED
            for section in sections
        )
        if not all_ungrounded_trust:
            return []

        agents = sorted({s.agent_id for s in sections})
        return [
            Obstruction(
                kind=ObstructionKind.PHANTOM_GLOBAL_SECTION,
                cohomology=CohomologyClass.PHANTOM,
                agents_involved=agents,
                description=(
                    f"All {len(agents)} agents agree, but no claim is grounded. "
                    f"Total ungrounded claims: {len(all_claims)}."
                ),
                repair_frontier=[
                    "Introduce external evidence verification.",
                    "Challenge the consensus with adversarial queries.",
                ],
            )
        ]

    # --------------------------------------------------------------------- #
    # Severity scoring
    # --------------------------------------------------------------------- #

    def severity_score(self, obstruction: Obstruction) -> float:
        """Score severity on [0, 1].

        Factors:
        * base weight from cohomology class
        * kind-specific weight
        * number of agents involved (more agents → higher severity)
        * number of contradictions (more contradictions → higher severity)
        """
        class_weight = _CLASS_SEVERITY.get(obstruction.cohomology, 0.5)
        kind_weight = _KIND_SEVERITY.get(obstruction.kind, 0.5)

        agent_factor = min(len(obstruction.agents_involved) / 5.0, 1.0)
        contradiction_factor = min(len(obstruction.contradictions) / 10.0, 1.0)

        raw = (
            0.35 * class_weight
            + 0.30 * kind_weight
            + 0.20 * agent_factor
            + 0.15 * contradiction_factor
        )
        return round(min(max(raw, 0.0), 1.0), 4)


# =========================================================================
# ObstructionAggregator
# =========================================================================


class ObstructionAggregator:
    """Aggregate and deduplicate obstructions."""

    def __init__(self, classifier: ObstructionClassifier | None = None) -> None:
        self._classifier = classifier or ObstructionClassifier()

    def aggregate(self, obstructions: list[Obstruction]) -> list[Obstruction]:
        """Merge overlapping obstructions and sort by severity (desc)."""
        deduped = self.deduplicate(obstructions)
        merged = self._merge_overlapping(deduped)
        merged.sort(
            key=lambda o: self._classifier.severity_score(o),
            reverse=True,
        )
        return merged

    def deduplicate(self, obstructions: list[Obstruction]) -> list[Obstruction]:
        """Remove redundant obstructions.

        Two obstructions are considered redundant if they share the same
        kind, cohomology class, and the same set of agents involved.
        In that case the one with more contradictions is kept.
        """
        seen: dict[tuple[ObstructionKind, CohomologyClass, tuple[str, ...]], Obstruction] = {}
        for obs in obstructions:
            key = (obs.kind, obs.cohomology, tuple(sorted(obs.agents_involved)))
            existing = seen.get(key)
            if existing is None or len(obs.contradictions) > len(existing.contradictions):
                seen[key] = obs
        return list(seen.values())

    def _merge_overlapping(
        self, obstructions: list[Obstruction]
    ) -> list[Obstruction]:
        """Merge obstructions of the same class that share agents."""
        by_class: dict[CohomologyClass, list[Obstruction]] = defaultdict(list)
        for obs in obstructions:
            by_class[obs.cohomology].append(obs)

        result: list[Obstruction] = []
        for cohom, group in by_class.items():
            if len(group) <= 1:
                result.extend(group)
                continue
            result.extend(self._merge_group(group))
        return result

    def _merge_group(self, group: list[Obstruction]) -> list[Obstruction]:
        """Within a single cohomology class, merge obstructions with overlapping agents."""
        # Use union-find on agent sets
        parent = list(range(len(group)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, j in itertools.combinations(range(len(group)), 2):
            agents_i = set(group[i].agents_involved)
            agents_j = set(group[j].agents_involved)
            if agents_i & agents_j:
                union(i, j)

        components: dict[int, list[int]] = defaultdict(list)
        for idx in range(len(group)):
            components[find(idx)].append(idx)

        merged: list[Obstruction] = []
        for members in components.values():
            if len(members) == 1:
                merged.append(group[members[0]])
                continue

            items = [group[m] for m in members]
            all_agents = sorted({a for o in items for a in o.agents_involved})
            all_contradictions = []
            seen_ids: set[str] = set()
            for o in items:
                for c in o.contradictions:
                    if c.contradiction_id not in seen_ids:
                        seen_ids.add(c.contradiction_id)
                        all_contradictions.append(c)
            descriptions = [o.description for o in items if o.description]
            repairs = list(
                dict.fromkeys(r for o in items for r in o.repair_frontier)
            )
            merged.append(
                Obstruction(
                    kind=items[0].kind,
                    cohomology=items[0].cohomology,
                    agents_involved=all_agents,
                    contradictions=all_contradictions,
                    description=" | ".join(descriptions),
                    repair_frontier=repairs,
                )
            )
        return merged


# =========================================================================
# ObstructionReport
# =========================================================================


@dataclass(slots=True)
class ObstructionReport:
    """Structured summary of all obstructions found in a verification run."""

    counts_by_class: dict[CohomologyClass, int] = field(default_factory=dict)
    counts_by_kind: dict[ObstructionKind, int] = field(default_factory=dict)
    most_severe: Obstruction | None = None
    total_agents_affected: int = 0
    repair_suggestions: list[str] = field(default_factory=list)


# =========================================================================
# ObstructionReporter
# =========================================================================


class ObstructionReporter:
    """Generate human-readable reports from obstruction lists."""

    def __init__(self, classifier: ObstructionClassifier | None = None) -> None:
        self._classifier = classifier or ObstructionClassifier()

    def report(self, obstructions: list[Obstruction]) -> ObstructionReport:
        """Build an :class:`ObstructionReport` from a list of obstructions."""
        counts_by_class: Counter[CohomologyClass] = Counter()
        counts_by_kind: Counter[ObstructionKind] = Counter()
        all_agents: set[str] = set()
        suggestions: list[str] = []
        seen_classes: set[CohomologyClass] = set()

        most_severe: Obstruction | None = None
        highest_score = -1.0

        for obs in obstructions:
            counts_by_class[obs.cohomology] += 1
            counts_by_kind[obs.kind] += 1
            all_agents.update(obs.agents_involved)

            score = self._classifier.severity_score(obs)
            if score > highest_score:
                highest_score = score
                most_severe = obs

            if obs.cohomology not in seen_classes:
                seen_classes.add(obs.cohomology)
                suggestions.extend(
                    _REPAIR_TEMPLATES.get(obs.cohomology, [])
                )

        # Add obstruction-specific repair suggestions
        for obs in obstructions:
            for hint in obs.repair_frontier:
                if hint not in suggestions:
                    suggestions.append(hint)

        return ObstructionReport(
            counts_by_class=dict(counts_by_class),
            counts_by_kind=dict(counts_by_kind),
            most_severe=most_severe,
            total_agents_affected=len(all_agents),
            repair_suggestions=suggestions,
        )

    def format_text(self, report: ObstructionReport) -> str:
        """Render an :class:`ObstructionReport` as human-readable text."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("  OBSTRUCTION REPORT")
        lines.append("=" * 60)

        total = sum(report.counts_by_class.values())
        lines.append(f"\nTotal obstructions: {total}")
        lines.append(f"Agents affected:    {report.total_agents_affected}")

        if report.counts_by_class:
            lines.append("\n--- By Cohomology Class ---")
            for cls in CohomologyClass:
                count = report.counts_by_class.get(cls, 0)
                if count:
                    lines.append(f"  {cls.value:>8s}: {count}")

        if report.counts_by_kind:
            lines.append("\n--- By Kind ---")
            for kind, count in sorted(
                report.counts_by_kind.items(),
                key=lambda kv: kv[1],
                reverse=True,
            ):
                lines.append(f"  {kind.name:<30s}: {count}")

        if report.most_severe is not None:
            sev = self._classifier.severity_score(report.most_severe)
            lines.append(f"\n--- Most Severe (score={sev:.4f}) ---")
            lines.append(f"  Class:  {report.most_severe.cohomology.value}")
            lines.append(f"  Kind:   {report.most_severe.kind.name}")
            lines.append(f"  Agents: {', '.join(report.most_severe.agents_involved)}")
            if report.most_severe.description:
                lines.append(f"  Desc:   {report.most_severe.description}")

        if report.repair_suggestions:
            lines.append("\n--- Repair Suggestions ---")
            for i, suggestion in enumerate(report.repair_suggestions, 1):
                lines.append(f"  {i}. {suggestion}")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
