"""Domain site construction — decomposing a domain into sub-domains with topology.

Implements Definition 9.1 from concept-ideation.html: a domain like "finance"
is not a single object but an entire site — a category of sub-domains with
methodological translation morphisms and a Grothendieck topology.

This module provides:
    - decompose_domain(): use an agent to decompose a domain into sub-domains
    - build_domain_site(): construct a DomainSite with JuGeo geometry backing
    - identify_problem_locus(): find which sub-domains contain the problem
    - compute_domain_filtration(): hierarchical decomposition (Definition 9.2)

Each sub-domain becomes a Coordinate in a JuGeo Site. Each methodological
translation becomes a Morphism. The covering families define the topology.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import (
    DomainSite,
    SubDomain,
    MethodologicalTranslation,
    DomainFiltrationLevel,
    DemandSection,
    HAS_GEOMETRY,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json

if HAS_GEOMETRY:
    from jugeo.geometry.site import (
        Site,
        SiteBuilder,
        Coordinate,
        CoordinateKind,
        Morphism,
        MorphismKind,
        CoveringFamily,
    )


def decompose_domain(
    domain_name: str,
    domain_description: str,
    *,
    level: DomainFiltrationLevel = DomainFiltrationLevel.MAJOR,
    verbose: bool = False,
) -> DomainSite:
    """Decompose a domain into sub-domains using an agent.

    The agent is asked to identify:
    1. The major sub-domains (level 1)
    2. The methodological translations between them
    3. The covering families (which sub-domains jointly cover others)
    4. Key concepts and techniques in each sub-domain

    This is the concrete realization of Definition 9.1: the agent constructs
    the objects and morphisms of the domain category.
    """
    prompt = f"""Decompose the domain "{domain_name}" into its constituent sub-domains
for the purpose of cross-domain ideation.

Domain description: {domain_description}

Identify 6-12 major sub-domains (areas, methodologies, or problem clusters).
For each sub-domain, list:
- name: short identifier (snake_case)
- description: what this sub-domain covers (1-2 sentences)
- key_concepts: 3-5 core concepts
- known_techniques: 3-5 established techniques/methods
- known_problems: 2-3 open problems or challenges

Then identify 8-15 methodological translations between sub-domains. For each:
- source: source sub-domain name
- target: target sub-domain name
- concept_map: how 2-3 key concepts translate
- strength: how faithfully techniques transfer (0.0-1.0)
- kind: ANALOGY (loose), EMBEDDING (faithful one-way), or ISOMORPHISM (bilateral)
- description: what the translation looks like in practice

Finally identify covering families: which collections of sub-domains
jointly explain all the knowledge in a given sub-domain.

Respond as JSON:
{{
    "sub_domains": [
        {{"name": "...", "description": "...", "key_concepts": [...],
          "known_techniques": [...], "known_problems": [...]}}
    ],
    "morphisms": [
        {{"source": "...", "target": "...", "concept_map": {{"concept_a": "concept_b"}},
          "strength": 0.7, "kind": "ANALOGY", "description": "..."}}
    ],
    "covering_families": {{
        "sub_domain_name": ["covering_sd_1", "covering_sd_2"]
    }}
}}"""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.domain.{domain_name}",
    )

    # Parse sub-domains
    sub_domains = []
    for sd_data in data.get("sub_domains", []):
        sub_domains.append(SubDomain(
            name=sd_data.get("name", "unknown"),
            description=sd_data.get("description", ""),
            level=level,
            known_techniques=sd_data.get("known_techniques", []),
            known_problems=sd_data.get("known_problems", []),
            key_concepts=sd_data.get("key_concepts", []),
        ))

    # Parse morphisms
    morphisms = []
    for m_data in data.get("morphisms", []):
        morphisms.append(MethodologicalTranslation(
            source=m_data.get("source", ""),
            target=m_data.get("target", ""),
            concept_map=m_data.get("concept_map", {}),
            strength=float(m_data.get("strength", 0.5)),
            kind=m_data.get("kind", "ANALOGY"),
            description=m_data.get("description", ""),
        ))

    site = DomainSite(
        name=domain_name,
        description=domain_description,
        sub_domains=sub_domains,
        morphisms=morphisms,
        covering_families=data.get("covering_families", {}),
    )

    # Build the JuGeo site if geometry is available
    site.build_site()

    return site


def identify_problem_locus(
    domain_site: DomainSite,
    problem: str,
) -> list[SubDomain]:
    """Identify which sub-domains contain the problem (Definition 9.4).

    The problem's sub-domain locus is Loc(p) = {D in D_1 : p|_D != 0} —
    the set of all sub-domains where the problem has nonzero projection.

    Uses an agent to determine which sub-domains are relevant to the problem.
    """
    sd_names = [sd.name for sd in domain_site.sub_domains]
    sd_descriptions = {sd.name: sd.description for sd in domain_site.sub_domains}

    prompt = f"""Given this problem within the domain "{domain_site.name}":

Problem: {problem}

And these sub-domains:
{json.dumps(sd_descriptions, indent=2)}

Which sub-domains have nonzero relevance to this problem? A sub-domain is
relevant if the problem touches its concepts, methods, or open questions.

Respond as JSON:
{{
    "relevant_sub_domains": ["sd_name_1", "sd_name_2", ...],
    "irrelevant_sub_domains": ["sd_name_3", ...],
    "reasoning": "brief explanation"
}}"""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.locus.{domain_site.name}",
    )

    relevant_names = set(data.get("relevant_sub_domains", sd_names[:3]))
    return [sd for sd in domain_site.sub_domains if sd.name in relevant_names]


def build_demand_sections(
    domain_site: DomainSite,
    problem: str,
    problem_locus: list[SubDomain],
) -> list[DemandSection]:
    """Build demand sections (Definition 9.5) for the problem.

    For each sub-domain in the problem locus, create a DemandSection
    representing the projection of the problem onto that sub-domain.
    """
    sections = []
    for sd in problem_locus:
        sections.append(DemandSection(
            sub_domain=sd.name,
            problem_name=problem[:100],
            description=f"Projection of '{problem}' onto {sd.name}: "
                       f"the aspects of the problem that touch {sd.description}",
            constraints=[],
            success_criteria=[],
        ))
    return sections
