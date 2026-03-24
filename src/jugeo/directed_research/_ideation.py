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

# No pre-defined partner catalog. The agent proposes partner domains from
# scratch, conditional on the specific prompt. Then we descend on whether
# those pairings actually hold up (the morphisms are real, the techniques
# actually transfer, and the result would materially beat existing tools).


def _compute_pairing_score(enf: ExcessNoveltyFraction) -> float:
    """Score a pairing by the judgment-geometry criterion.

    The score is the product of three factors from Proposition 9.2:
      practical_impact * morphism_strength * enf

    This encodes: the techniques must actually transfer (strength), the
    overlap must contain un-tried ideas (enf), and the result must
    materially improve outcomes (impact). All three must be nonzero.

    We also penalize extreme semantic distance (> 7) because very distant
    pairings produce morphisms too weak to build real software on, and
    penalize very low distance (< 2) because those are already well-explored.
    """
    base = enf.enf * enf.avg_morphism_strength
    # Distance penalty: bell curve centered at distance 3-5
    dist = enf.semantic_distance
    if dist < 2:
        base *= 0.5   # too close — probably already exists
    elif dist > 7:
        base *= 0.3   # too far — morphisms likely too weak
    return base


def _validate_pairing_via_descent(
    primary: DomainSite,
    candidate_name: str,
    candidate_desc: str,
    problem: str,
    problem_locus: list[SubDomain],
) -> tuple[bool, list[MethodologicalTranslation], str]:
    """Validate a candidate pairing by attempting to find real morphisms.

    This is descent on the pairing: we ask the agent to produce concrete
    morphisms, then check whether they are real (the concepts actually map,
    the strength is justified) by running a second agent call that tries
    to FALSIFY each morphism. If the falsification attempt fails (can't
    find a counterexample to the analogy), the morphism survives descent.

    Returns (valid, morphisms, reason).
    """
    # Step 1: discover morphisms
    morphisms = discover_cross_domain_morphisms(
        primary, candidate_name, candidate_desc, problem_locus)

    if not morphisms:
        return False, [], "no morphisms found"

    # Step 2: filter to strong morphisms only (strength >= 0.5)
    strong = [m for m in morphisms if m.strength >= 0.5]
    if not strong:
        return False, morphisms, f"all {len(morphisms)} morphisms too weak (< 0.5)"

    # Step 3: validate via descent — ask agent to falsify the strongest morphism
    best = max(strong, key=lambda m: m.strength)
    validation_data, _ = agent_json(
        f"""I claim there is a strong structural analogy between
"{best.source}" (in {primary.name}) and "{best.target}" (in {candidate_name}):

Concept map: {json.dumps(best.concept_map)}
Claimed strength: {best.strength}
Description: {best.description}

Try to FALSIFY this claim. Find a concrete reason why this analogy breaks down
— a key property of the source that has NO counterpart in the target, or a
technique that simply cannot transfer.

Respond as JSON:
{{
    "falsified": true/false,
    "reason": "why the analogy breaks down (or why it holds)",
    "adjusted_strength": 0.0-1.0
}}""",
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.validate.{primary.name}_{candidate_name}",
    )

    falsified = validation_data.get("falsified", False)
    adjusted = float(validation_data.get("adjusted_strength", best.strength))

    if falsified and adjusted < 0.4:
        return False, strong, validation_data.get("reason", "falsified by descent")

    # Update strength with the descent-adjusted value
    best.strength = adjusted
    return True, strong, "survived descent"


def select_partner_domains(
    primary_domain: DomainSite,
    problem: str,
    problem_locus: list[SubDomain],
    *,
    n_candidates: int = 5,
    verbose: bool = False,
) -> list[tuple[str, ExcessNoveltyFraction]]:
    """Select partner domains — agent-proposed, descent-validated.

    The agent proposes partner domains from scratch (no pre-defined catalog),
    conditional on the specific prompt. Then each candidate is validated by
    attempting to construct real morphisms and falsifying the weakest ones.
    Only candidates that survive descent are returned.

    The scoring criterion (Proposition 9.2 adapted for practical utility):
      score = practical_impact * morphism_strength * enf
    prioritizes: "no one's built exactly this, but practitioners would
    immediately see why it should be materially better."
    """
    locus_desc = {sd.name: sd.description for sd in problem_locus}

    # Step 1: Agent proposes candidates from scratch
    data, _ = agent_json(
        f"""Given this specific problem, propose 5-8 mathematical or computational
fields whose techniques could be applied to MATERIALLY IMPROVE outcomes.

Primary domain: {primary_domain.name}
Problem: {problem}
Relevant sub-areas: {json.dumps(locus_desc, indent=2)}

For each proposed field:
- Think about what CONCRETE techniques from that field would transfer.
- Estimate how faithfully they transfer (avg_strength: 0.0-1.0).
- Estimate semantic distance (1-10): how culturally separate the fields are.
- Estimate what fraction of the combined approach is genuinely un-tried (enf: 0.0-1.0).
- Most importantly: WHY would a practitioner pay money for this combination?

CRITICAL: Do NOT propose fields just because they sound impressive. Propose
fields where the techniques ACTUALLY solve a computational problem in the
primary domain better than what currently exists. A well-applied Kalman filter
beats a poorly-applied sheaf every time.

Respond as JSON:
{{
    "candidates": [
        {{"name": "field_name", "description": "what it is and its key techniques",
          "avg_strength": 0.75, "semantic_distance": 4, "enf": 0.5,
          "concrete_techniques": ["technique_1 → how it applies", ...],
          "why_practitioners_would_pay": "specific reason"}}
    ]
}}""",
        surface=SurfaceKind.THEORY,
        coordinate="ideation.partner_proposal",
    )

    candidates = data.get("candidates", [])
    if verbose:
        print(f"  Agent proposed {len(candidates)} candidate partner domains", flush=True)

    # Step 2: Score each candidate
    scored: list[tuple[str, str, ExcessNoveltyFraction]] = []
    for c in candidates:
        enf = ExcessNoveltyFraction(
            domain_1=primary_domain.name,
            domain_2=c.get("name", "unknown"),
            enf=float(c.get("enf", 0.5)),
            avg_morphism_strength=float(c.get("avg_strength", 0.5)),
            semantic_distance=float(c.get("semantic_distance", 5)),
            verdict="productive" if float(c.get("avg_strength", 0)) >= 0.5 else "aspirational",
        )
        score = _compute_pairing_score(enf)
        scored.append((c.get("name", "unknown"), c.get("description", ""), enf))

    # Sort by pairing score
    scored.sort(key=lambda x: _compute_pairing_score(x[2]), reverse=True)

    # Step 3: Validate top candidates via descent (try to falsify morphisms)
    validated: list[tuple[str, ExcessNoveltyFraction]] = []
    for name, desc, enf in scored[:n_candidates + 2]:  # try a couple extra
        if verbose:
            print(f"  Validating pairing: {primary_domain.name} × {name}...", flush=True)
        valid, morphisms, reason = _validate_pairing_via_descent(
            primary_domain, name, desc, problem, problem_locus)
        if valid:
            # Update strength based on actual discovered morphisms
            if morphisms:
                actual_strength = sum(m.strength for m in morphisms) / len(morphisms)
                enf = ExcessNoveltyFraction(
                    domain_1=enf.domain_1, domain_2=enf.domain_2,
                    enf=enf.enf, avg_morphism_strength=actual_strength,
                    semantic_distance=enf.semantic_distance, verdict="productive",
                )
            validated.append((name, enf))
            if verbose:
                print(f"    ✓ SURVIVED descent (strength={enf.avg_morphism_strength:.2f})", flush=True)
            if len(validated) >= n_candidates:
                break
        else:
            if verbose:
                print(f"    ✗ FAILED descent: {reason}", flush=True)

    # If no candidates survived, fall back to the top-scored unvalidated one
    if not validated and scored:
        name, desc, enf = scored[0]
        validated.append((name, enf))

    return validated


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

Find {n_propositions} BRIDGE PROPOSITIONS — concrete tool/algorithm ideas that:
1. Combine techniques from BOTH domains in a way nobody has packaged into
   a single tool before (the H^1 condition — not in the image of either
   restriction map, meaning you can't get this from either field alone)
2. Would MATERIALLY improve outcomes for the problem "{problem}" — a
   practitioner would immediately see why this is better than existing tools
3. Are BUILDABLE — not just theoretically interesting, but something a team
   could implement in Python with real data in weeks, not years

The sweet spot is: "practitioners would say 'why hasn't anyone done exactly
this?' — not 'that's a cute analogy'" . Think engineering utility, not
intellectual elegance.

For each proposition:
- title: short name (name it like a product, not a theorem)
- description: 3-5 sentences — what the tool DOES, how it combines both
  domains, what specific improvement it delivers over existing tools
- coordinate: which sub-domains overlap (e.g., "sub_domain_a × partner_concept")
- novelty_score: 0.0-1.0 (how much of this is genuinely un-tried vs already
  exists under another name)
- relevance_score: 0.0-1.0 (how directly it improves outcomes for the problem)
- relevance_level: 1=tangential, 2=partial, 3=direct, 4=transformative
- existing_near_misses: what's the closest existing tool and why does it falls short?
- concrete_improvement: what specific metric would improve by roughly how much?
- covering_dimension: how many INDEPENDENT, NON-TRIVIAL components does this idea
  need? Each component is a distinct module/subsystem that addresses a different
  aspect of the overlap. A small tweak has covdim 1-2. A substantial system has
  covdim 6-12. List the components. We need covdim >= 10 for a 15K+ LOC system
  where every line is justified by a distinct region of the idea.
- components: list of the independent components (each becomes a module)
- proof_sketch: key steps for validating that this actually works
- open_obligations: what needs to happen to confirm it works

Respond as JSON:
{{
    "bridge_propositions": [
        {{"title": "...", "description": "...",
          "coordinate": "sub_domain_a × partner_concept",
          "novelty_score": 0.7, "relevance_score": 0.85,
          "relevance_level": 3,
          "existing_near_misses": "closest existing tool and why it falls short",
          "concrete_improvement": "metric X improves by ~Y%",
          "covering_dimension": 10,
          "components": ["component_1: what it does", "component_2: what it does", ...],
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
        covdim = int(bp.get("covering_dimension", 1))
        propositions.append(BridgeProposition(
            title=bp.get("title", "Untitled"),
            description=bp.get("description", ""),
            source_domain=primary.name,
            target_domain=partner_name,
            coordinate=bp.get("coordinate", f"{primary.name} × {partner_name}"),
            novelty_score=float(bp.get("novelty_score", 0.5)),
            relevance_score=float(bp.get("relevance_score", 0.5)),
            relevance_level=level,
            covering_dimension=covdim,
            components=bp.get("components", []),
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

    best_partner_name = partner_candidates[0][0] if partner_candidates else "optimization"
    best_enf = partner_candidates[0][1] if partner_candidates else None
    # Get description from the candidate data (already proposed by the agent)
    partner_desc = best_enf.domain_2 if best_enf else best_partner_name

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

    # Step 7: Select best approach (Theorem 10.4: useful, novel, AND substantial)
    # Score = UNS * min(covdim, 15) — scale matters, but caps at 15 to avoid
    # rewarding artificial inflation of components
    if propositions:
        propositions.sort(
            key=lambda bp: bp.useful_novelty_score * min(bp.covering_dimension, 15),
            reverse=True)
        selected = propositions[0]
        if verbose:
            for bp in propositions[:3]:
                print(f"  Candidate: {bp.title} "
                      f"(UNS={bp.useful_novelty_score:.2f}, "
                      f"covdim={bp.covering_dimension}, "
                      f"~{bp.estimated_loc} LOC)", flush=True)
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
