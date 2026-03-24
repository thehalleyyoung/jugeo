"""Geometry of ideation — the full cross-domain synthesis pipeline.

This is the master ideation module that orchestrates the entire process from
§9 of concept-ideation.html:

    1. Decompose the prompt into a primary domain + problem
    2. Build the domain site (sub-domains, morphisms, topology)
    3. Identify the problem locus (which sub-domains contain the problem)
    4. Select a partner domain with high excess novelty
    5. Discover analogy morphisms between domains
    6. Compute the Cech complex (cross-domain overlaps)
    7. Search H^1 for novel sections with nonzero germ at p
    8. Evaluate usefulness (relevance filtration)
    9. Run a tournament over candidate approaches
    10. Select the best approach for the research loop

The ideation engine uses the JuGeo ideate command (``jg ideate``) as a
sub-oracle for mathematical ideation, and wraps it with the full domain-site
/ solution-presheaf / demand-sheaf machinery from the concept-ideation page.
"""

from __future__ import annotations

import json
import random
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    DomainSite,
    SubDomain,
    MethodologicalTranslation,
    BridgeProposition,
    UsefulNoveltyScore,
    IdeationResult,
    ExcessNoveltyFraction,
    ProductivePairingCriterion,
    RelevanceFiltrationLevel,
    DemandSection,
    HAS_EASY,
    HAS_GEOMETRY,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json
from jugeo.directed_research._domain_site import (
    decompose_domain,
    identify_problem_locus,
    build_demand_sections,
)

if HAS_EASY:
    from jugeo.easy import ideate as jg_ideate

if HAS_GEOMETRY:
    from jugeo.geometry.site import Site, SiteBuilder, Coordinate
    from jugeo.geometry.descent import DescentEngine, DescentStrategy


# ═══════════════════════════════════════════════════════════════════════
#  Partner domain selection
# ═══════════════════════════════════════════════════════════════════════

# Pre-defined partner domain catalog (domains known to produce good pairings)
PARTNER_DOMAIN_CATALOG: dict[str, str] = {
    "ecology": "Population dynamics, ecosystem management, conservation biology, succession theory",
    "control_theory": "Feedback loops, PID controllers, stability analysis, optimal control",
    "statistical_mechanics": "Partition functions, phase transitions, entropy, Boltzmann distributions",
    "information_theory": "Shannon entropy, channel capacity, coding theory, mutual information",
    "topology": "Persistent homology, Betti numbers, simplicial complexes, covering spaces",
    "category_theory": "Functors, natural transformations, adjunctions, limits and colimits",
    "game_theory": "Nash equilibria, mechanism design, evolutionary games, cooperative games",
    "dynamical_systems": "Attractors, bifurcations, Lyapunov stability, chaos theory",
    "algebraic_geometry": "Varieties, sheaves, schemes, cohomology, moduli spaces",
    "differential_geometry": "Manifolds, connections, curvature, fiber bundles, Riemannian metrics",
    "number_theory": "Prime distributions, modular forms, L-functions, arithmetic geometry",
    "combinatorics": "Graph theory, matroids, generating functions, Ramsey theory",
    "probability_theory": "Measure theory, stochastic processes, martingales, large deviations",
    "optimization": "Convex optimization, gradient descent, LP/SDP, variational methods",
    "signal_processing": "Fourier analysis, wavelets, compressed sensing, filter design",
    "neuroscience": "Neural coding, synaptic plasticity, cortical circuits, decision-making",
    "linguistics": "Formal grammars, semantics, pragmatics, computational linguistics",
    "epidemiology": "SIR models, contact tracing, herd immunity, epidemic forecasting",
    "materials_science": "Crystal structures, phase diagrams, defects, mechanical properties",
    "quantum_computing": "Qubits, entanglement, quantum error correction, quantum algorithms",
}


def select_partner_domains(
    primary_domain: DomainSite,
    problem: str,
    problem_locus: list[SubDomain],
    *,
    n_candidates: int = 5,
    verbose: bool = False,
) -> list[tuple[str, ExcessNoveltyFraction]]:
    """Select partner domains with high excess novelty fraction.

    Uses an agent to estimate the ENF for each candidate partner domain
    (§9.4): ENF = dim(overlap - im(res_1) - im(res_2)) / dim(overlap).

    Returns top-n candidates sorted by ENF.
    """
    locus_names = [sd.name for sd in problem_locus]
    locus_desc = {sd.name: sd.description for sd in problem_locus}

    prompt = f"""I need to find partner domains for cross-domain ideation.

Primary domain: {primary_domain.name}
Problem: {problem}
Problem locus (relevant sub-domains): {json.dumps(locus_desc, indent=2)}

For each of these candidate partner domains, estimate:
1. avg_morphism_strength: how faithfully techniques translate (0.0-1.0)
2. semantic_distance: how culturally distant (1-10 scale)
3. enf: excess novelty fraction (0.0-1.0) — what fraction of the overlap
   contains genuinely novel ideas not obtainable from either domain alone

Candidate partners:
{json.dumps(PARTNER_DOMAIN_CATALOG, indent=2)}

Respond as JSON:
{{
    "rankings": [
        {{"domain": "...", "avg_strength": 0.7, "distance": 5, "enf": 0.6,
          "verdict": "productive|routine|aspirational|too_distant",
          "reasoning": "brief explanation"}}
    ]
}}

Sort by ENF descending. The sweet spot is ENF in [0.4, 0.8] with
strength >= 0.5 and distance >= 3."""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate="ideation.partner_selection",
    )

    results = []
    for r in data.get("rankings", [])[:n_candidates]:
        enf = ExcessNoveltyFraction(
            domain_1=primary_domain.name,
            domain_2=r.get("domain", "unknown"),
            enf=float(r.get("enf", 0.5)),
            avg_morphism_strength=float(r.get("avg_strength", 0.5)),
            semantic_distance=float(r.get("distance", 5)),
            verdict=r.get("verdict", "productive"),
        )
        results.append((r.get("domain", "unknown"), enf))

    # Sort by ENF, preferring the productive sweet spot
    results.sort(key=lambda x: x[1].enf if x[1].is_productive else x[1].enf * 0.5,
                 reverse=True)
    return results[:n_candidates]


# ═══════════════════════════════════════════════════════════════════════
#  Cross-domain morphism discovery
# ═══════════════════════════════════════════════════════════════════════

def discover_cross_domain_morphisms(
    primary: DomainSite,
    partner_name: str,
    partner_description: str,
    problem_locus: list[SubDomain],
) -> list[MethodologicalTranslation]:
    """Discover analogy morphisms between the primary domain and partner.

    These are the methodological translations of Definition 9.1a: maps that
    carry techniques from one domain's sub-domains to the other's, preserving
    operative structure.
    """
    locus_info = {sd.name: {"description": sd.description,
                            "concepts": sd.key_concepts,
                            "techniques": sd.known_techniques}
                  for sd in problem_locus}

    prompt = f"""Discover methodological translations between these two domains:

Primary domain: {primary.name}
Primary sub-domains (problem locus):
{json.dumps(locus_info, indent=2)}

Partner domain: {partner_name}
Partner description: {partner_description}

For each meaningful analogy, identify:
- source: sub-domain in the primary domain
- target: concept cluster in the partner domain
- concept_map: how 2-4 specific concepts translate
- strength: faithfulness of the translation (0.0-1.0)
- kind: ANALOGY (loose), EMBEDDING (faithful one-way), ISOMORPHISM (bilateral)
- description: what the translation looks like concretely

Find 4-8 morphisms. Focus on translations that are structurally faithful
(the mathematical/methodological machinery transfers) but semantically
surprising (the fields don't usually talk to each other).

Respond as JSON:
{{
    "morphisms": [
        {{"source": "...", "target": "...",
          "concept_map": {{"primary_concept": "partner_concept", ...}},
          "strength": 0.7, "kind": "ANALOGY",
          "description": "..."}}
    ]
}}"""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.morphisms.{primary.name}_{partner_name}",
    )

    morphisms = []
    for m in data.get("morphisms", []):
        morphisms.append(MethodologicalTranslation(
            source=m.get("source", ""),
            target=m.get("target", ""),
            concept_map=m.get("concept_map", {}),
            strength=float(m.get("strength", 0.5)),
            kind=m.get("kind", "ANALOGY"),
            description=m.get("description", ""),
        ))
    return morphisms


# ═══════════════════════════════════════════════════════════════════════
#  H^1 search — finding novel useful ideas at the overlap
# ═══════════════════════════════════════════════════════════════════════

def search_h1_fiber(
    primary: DomainSite,
    partner_name: str,
    partner_description: str,
    morphisms: list[MethodologicalTranslation],
    problem: str,
    problem_locus: list[SubDomain],
    *,
    n_propositions: int = 5,
) -> list[BridgeProposition]:
    """Search H^1 in the fiber over the problem for useful novel ideas.

    This is the core of Theorem 9.1: find sections sigma in S(U_12) that
    satisfy BOTH:
    - [sigma] != 0 in H^1 (novelty: not in image of either restriction)
    - sigma_p != 0 in S_p (usefulness: nonzero germ at the problem)

    The agent is prompted with the specific geometric constraints:
    - "cannot be expressed in either domain alone" → H^1 condition
    - "bear on problem p" → stalk condition
    - Concrete analogy morphisms → overlap structure
    """
    morphism_descriptions = [
        f"  {m.source} → {m.target} ({m.kind}, s={m.strength:.2f}): {m.description}"
        for m in morphisms
    ]
    locus_names = [sd.name for sd in problem_locus]

    # Also use jg ideate if available for mathematical depth
    jg_ideation = ""
    if HAS_EASY:
        try:
            topic = f"{primary.name} meets {partner_name} for {problem}"
            result = jg_ideate(topic, n=3)
            if result.theorems:
                jg_ideation = "\n\nJuGeo ideation engine suggests:\n" + "\n".join(
                    f"  - {t.statement} (novelty={t.novelty_score:.2f})"
                    for t in result.theorems
                )
        except Exception:
            pass

    prompt = f"""Search for useful novel ideas at the intersection of two domains.

PRIMARY DOMAIN: {primary.name}
PARTNER DOMAIN: {partner_name} — {partner_description}
PROBLEM: {problem}
PROBLEM LOCUS: {', '.join(locus_names)}

ANALOGY MORPHISMS (the structural connections between domains):
{chr(10).join(morphism_descriptions)}
{jg_ideation}

Find {n_propositions} BRIDGE PROPOSITIONS — ideas that:
1. CANNOT be expressed in either domain alone (novelty: not in the image
   of either restriction map — this is the H^1 condition)
2. BEAR DIRECTLY on the problem "{problem}" (usefulness: nonzero germ
   at the problem point)

For each proposition:
- title: short name
- description: 3-5 sentence description of the idea, how it connects both
  domains, and what it concretely proposes
- coordinate: which sub-domains overlap (e.g., "sub_domain_a × partner_concept")
- novelty_score: 0.0-1.0 (how far from either restriction map image)
- relevance_score: 0.0-1.0 (how directly it addresses the problem)
- relevance_level: 1=tangential, 2=partial, 3=direct, 4=transformative
- proof_sketch: key steps for validating this idea
- open_obligations: what needs to happen to confirm it works

Respond as JSON:
{{
    "bridge_propositions": [
        {{"title": "...", "description": "...",
          "coordinate": "sub_domain_a × partner_concept",
          "novelty_score": 0.85, "relevance_score": 0.80,
          "relevance_level": 3,
          "proof_sketch": "...",
          "open_obligations": ["obligation1", ...]}}
    ]
}}"""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.h1.{primary.name}_{partner_name}",
    )

    propositions = []
    for bp in data.get("bridge_propositions", []):
        level_val = int(bp.get("relevance_level", 3))
        level = RelevanceFiltrationLevel(min(4, max(1, level_val)))
        propositions.append(BridgeProposition(
            title=bp.get("title", "Untitled"),
            description=bp.get("description", ""),
            source_domain=primary.name,
            target_domain=partner_name,
            coordinate=bp.get("coordinate", f"{primary.name} × {partner_name}"),
            novelty_score=float(bp.get("novelty_score", 0.5)),
            relevance_score=float(bp.get("relevance_score", 0.5)),
            relevance_level=level,
            proof_sketch=bp.get("proof_sketch", ""),
            open_obligations=bp.get("open_obligations", []),
            trust=TRUST_COPILOT,
        ))

    return propositions


# ═══════════════════════════════════════════════════════════════════════
#  The full ideation pipeline
# ═══════════════════════════════════════════════════════════════════════

def run_ideation(
    prompt: str,
    *,
    n_partner_candidates: int = 5,
    n_propositions: int = 5,
    verbose: bool = False,
) -> IdeationResult:
    """Run the full geometry-of-ideation pipeline.

    This is the master function that orchestrates all of §9:
    1. Parse the prompt to extract domain + problem
    2. Decompose the domain into sub-domains
    3. Identify the problem locus
    4. Select partner domains
    5. Discover morphisms
    6. Search H^1 for bridge propositions
    7. Select the best approach

    Returns an IdeationResult with the full trace.
    """
    import time
    t0 = time.time()

    if verbose:
        print("IDEATION: Parsing prompt...", flush=True)

    # Step 1: Parse the prompt to extract domain and problem
    parse_data, _ = agent_json(
        f"""Parse this research prompt into a domain and a problem:

"{prompt}"

Respond as JSON:
{{
    "domain_name": "the primary domain (e.g., finance, medicine, education)",
    "domain_description": "1-2 sentence description of the domain",
    "problem": "the specific problem or goal to solve",
    "problem_description": "1-2 sentence elaboration of the problem"
}}""",
        surface=SurfaceKind.THEORY,
        coordinate="ideation.parse",
    )

    domain_name = parse_data.get("domain_name", "general")
    domain_desc = parse_data.get("domain_description", prompt)
    problem = parse_data.get("problem", prompt)

    # Step 2: Decompose domain
    if verbose:
        print(f"IDEATION: Decomposing domain '{domain_name}'...", flush=True)
    primary = decompose_domain(domain_name, domain_desc, verbose=verbose)

    # Step 3: Identify problem locus
    if verbose:
        print("IDEATION: Identifying problem locus...", flush=True)
    locus = identify_problem_locus(primary, problem)

    # Step 4: Select partner domains
    if verbose:
        print("IDEATION: Selecting partner domains...", flush=True)
    partner_candidates = select_partner_domains(
        primary, problem, locus, n_candidates=n_partner_candidates, verbose=verbose)

    best_partner_name = partner_candidates[0][0] if partner_candidates else "category_theory"
    best_enf = partner_candidates[0][1] if partner_candidates else None
    partner_desc = PARTNER_DOMAIN_CATALOG.get(best_partner_name, "")

    # Step 5: Discover morphisms
    if verbose:
        print(f"IDEATION: Discovering morphisms with '{best_partner_name}'...", flush=True)
    morphisms = discover_cross_domain_morphisms(
        primary, best_partner_name, partner_desc, locus)

    # Step 6: Search H^1
    if verbose:
        print("IDEATION: Searching H^1 for bridge propositions...", flush=True)
    propositions = search_h1_fiber(
        primary, best_partner_name, partner_desc,
        morphisms, problem, locus, n_propositions=n_propositions)

    # Step 7: Select best approach (highest UNS)
    if propositions:
        propositions.sort(key=lambda bp: bp.useful_novelty_score, reverse=True)
        selected = propositions[0]
    else:
        selected = None

    # Build productive pairing criterion
    avg_strength = (sum(m.strength for m in morphisms) / len(morphisms)
                   if morphisms else 0.0)
    pairing = ProductivePairingCriterion(
        source_domain=primary.name,
        target_domain=best_partner_name,
        avg_strength=avg_strength,
        semantic_distance=best_enf.semantic_distance if best_enf else 5.0,
    )

    elapsed = time.time() - t0
    return IdeationResult(
        prompt=prompt,
        primary_domain=primary,
        partner_domain=None,  # Could decompose partner too
        morphisms=morphisms,
        bridge_propositions=propositions,
        selected_approach=selected,
        enf=best_enf,
        pairing_criterion=pairing,
        elapsed=elapsed,
        metadata={
            "domain_name": domain_name,
            "problem": problem,
            "partner": best_partner_name,
            "locus": [sd.name for sd in locus],
        },
    )
