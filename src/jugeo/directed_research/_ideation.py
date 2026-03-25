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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    # Step 3: Validate top candidates via descent IN PARALLEL.
    # Each validation is independent — discovering morphisms and attempting
    # falsification for one pairing doesn't affect another.
    # We CACHE the morphisms found during validation to avoid re-discovering
    # them in the exploration phase (saves 1 agent call per surviving partner).
    to_validate = scored[:n_candidates]  # validate exactly top N

    def _validate_one(
        name: str, desc: str, enf: ExcessNoveltyFraction,
    ) -> tuple[str, ExcessNoveltyFraction, bool, str, list[MethodologicalTranslation]]:
        if verbose:
            print(f"  Validating pairing: {primary_domain.name} × {name}...", flush=True)
        valid, morphisms, reason = _validate_pairing_via_descent(
            primary_domain, name, desc, problem, problem_locus)
        if valid and morphisms:
            actual_strength = sum(m.strength for m in morphisms) / len(morphisms)
            enf = ExcessNoveltyFraction(
                domain_1=enf.domain_1, domain_2=enf.domain_2,
                enf=enf.enf, avg_morphism_strength=actual_strength,
                semantic_distance=enf.semantic_distance, verdict="productive",
            )
        return name, enf, valid, reason, morphisms

    validated: list[tuple[str, ExcessNoveltyFraction]] = []
    cached_morphisms: dict[str, list[MethodologicalTranslation]] = {}
    with ThreadPoolExecutor(max_workers=min(len(to_validate), 4)) as pool:
        futures = [
            pool.submit(_validate_one, name, desc, enf)
            for name, desc, enf in to_validate
        ]
        for future in as_completed(futures):
            try:
                name, enf, valid, reason, morphisms = future.result()
                if valid:
                    validated.append((name, enf))
                    cached_morphisms[name] = morphisms
                    if verbose:
                        print(f"    ✓ {name} SURVIVED descent "
                              f"(strength={enf.avg_morphism_strength:.2f})", flush=True)
                else:
                    if verbose:
                        print(f"    ✗ {name} FAILED descent: {reason}", flush=True)
            except Exception as exc:
                if verbose:
                    print(f"    ✗ Validation error: {exc}", flush=True)

    # Re-sort validated by pairing score (parallel completion order is arbitrary)
    validated.sort(key=lambda x: _compute_pairing_score(x[1]), reverse=True)
    validated = validated[:n_candidates]

    # If no candidates survived, fall back to the top-scored unvalidated one
    if not validated and scored:
        name, desc, enf = scored[0]
        validated.append((name, enf))

    return validated, cached_morphisms


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
#  §10 Compositional tower — n-fold domain composition
# ═══════════════════════════════════════════════════════════════════════

def _search_compositional_overlap(
    primary: DomainSite,
    composed_domains: list[str],
    new_domain_name: str,
    new_domain_desc: str,
    existing_bridge: BridgeProposition,
    problem: str,
    problem_locus: list[SubDomain],
    *,
    n_propositions: int = 3,
) -> list[BridgeProposition]:
    """Search H^n on the (n+1)-fold overlap for ideas requiring ALL domains.

    Per Definition 10.1: the n-fold fiber product has sections invisible at
    lower levels. A section σ ∈ H^{n-1}({D₁,...,D_n}, S) requires all n
    domains — it collapses to zero if ANY domain is removed.

    The agent is prompted with the existing bridge (from level n-1) and asked:
    "what NEW components become possible when you add domain D_{n+1} that were
    impossible with only the previous domains?"
    """
    domain_list = " × ".join(composed_domains)
    locus_names = [sd.name for sd in problem_locus]
    level = len(composed_domains) + 1

    prompt = f"""You have an existing bridge idea from composing {len(composed_domains)} domains:

COMPOSED DOMAINS: {domain_list}
EXISTING BRIDGE: "{existing_bridge.title}"
  {existing_bridge.description}
  Components: {', '.join(existing_bridge.components[:5])}
  Covering dimension: {existing_bridge.covering_dimension}

NEW DOMAIN TO ADD: {new_domain_name} — {new_domain_desc}
PROBLEM: {problem}
PROBLEM LOCUS: {', '.join(locus_names)}

This is LEVEL {level} of the compositional tower (Definition 10.1).
Search H^{level-1} on the {level}-fold overlap {domain_list} × {new_domain_name}.

Find {n_propositions} ENRICHED bridge propositions that:
1. REQUIRE ALL {level} DOMAINS — removing any one domain makes the idea collapse.
   This is the H^{level-1} condition: the section is not in the image of any
   face map (restriction that drops one domain).
2. Have HIGHER covering dimension than the existing bridge (covdim > {existing_bridge.covering_dimension}).
   The new domain should add genuinely new independent components.
3. Each new component should arise specifically from the {level}-fold interaction —
   it needs techniques from the new domain AND at least 2 of the existing domains
   simultaneously.

For each enriched proposition:
- title: name for the enriched system
- description: what it does, emphasizing what the new domain enables
- new_components: components that ONLY exist because of the {level}-fold composition
- inherited_components: components from the existing bridge that survive
- covering_dimension: total (inherited + new)
- novelty_score, relevance_score, relevance_level: as before
- why_all_domains_needed: concise argument for why dropping any domain kills the idea

Respond as JSON:
{{
    "enriched_propositions": [
        {{"title": "...", "description": "...",
          "new_components": ["comp: what it does (requires domains X, Y, Z)"],
          "inherited_components": ["comp: inherited from level {level-1}"],
          "covering_dimension": 14,
          "novelty_score": 0.8, "relevance_score": 0.85,
          "relevance_level": 3,
          "why_all_domains_needed": "..."}}
    ]
}}"""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.h{level-1}.{'_'.join(d[:10] for d in composed_domains)}_{new_domain_name[:10]}",
    )

    propositions = []
    for bp in data.get("enriched_propositions", []):
        level_val = int(bp.get("relevance_level", 3))
        rel_level = RelevanceFiltrationLevel(min(4, max(1, level_val)))
        new_comps = bp.get("new_components", [])
        inherited = bp.get("inherited_components", existing_bridge.components)
        all_comps = inherited + new_comps
        covdim = int(bp.get("covering_dimension", len(all_comps)))

        propositions.append(BridgeProposition(
            title=bp.get("title", existing_bridge.title),
            description=bp.get("description", ""),
            source_domain=primary.name,
            target_domain=" × ".join(composed_domains + [new_domain_name]),
            coordinate=f"{primary.name} × " + " × ".join(composed_domains[1:] + [new_domain_name]),
            novelty_score=float(bp.get("novelty_score", 0.5)),
            relevance_score=float(bp.get("relevance_score", 0.5)),
            relevance_level=rel_level,
            covering_dimension=covdim,
            components=all_comps,
            proof_sketch=bp.get("why_all_domains_needed", ""),
            open_obligations=existing_bridge.open_obligations,
            trust=TRUST_COPILOT,
            metadata={
                "composition_level": level,
                "domains": composed_domains + [new_domain_name],
                "new_components_count": len(new_comps),
                "inherited_components_count": len(inherited),
            },
        ))

    return propositions


def _propose_enriching_domains(
    primary: DomainSite,
    composed_domains: list[str],
    existing_bridge: BridgeProposition,
    problem: str,
    *,
    n_candidates: int = 4,
) -> list[tuple[str, str, float]]:
    """Propose domains that could enrich an existing bridge at the next level.

    Per Theorem 10.3 (Compositional Saturation), the new domain should increase
    covering dimension. We ask: "given the current composition, what domain
    would add the most NEW independent components?"

    Returns: list of (name, description, estimated_marginal_covdim).
    """
    domain_list = " × ".join(composed_domains)
    prompt = f"""Given an existing cross-domain idea from composing {len(composed_domains)} domains:

COMPOSED DOMAINS: {domain_list}
BRIDGE IDEA: "{existing_bridge.title}" (covdim={existing_bridge.covering_dimension})
  {existing_bridge.description}
PROBLEM: {problem}

Propose {n_candidates} additional domains whose techniques would ADD genuinely
new independent components to this idea. The new domain should:
1. Have strong analogy morphisms with at LEAST 2 of the existing domains
2. Enable components that are impossible without the new domain
3. Not be a sub-field of any existing domain (that would be redundant)

For each candidate, estimate how many NEW independent components it would add
(marginal covering dimension gain). Per Theorem 10.3, composition saturates
when marginal gain < 2.

Respond as JSON:
{{
    "enriching_domains": [
        {{"name": "...", "description": "key techniques and why they enrich",
          "marginal_covdim": 4,
          "morphisms_to_existing": ["connection to domain X", "connection to domain Y"]}}
    ]
}}"""

    data, _ = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.enrich_proposal.level{len(composed_domains)+1}",
    )

    results = []
    for d in data.get("enriching_domains", []):
        results.append((
            d.get("name", "unknown"),
            d.get("description", ""),
            float(d.get("marginal_covdim", 0)),
        ))
    # Sort by expected marginal gain
    results.sort(key=lambda x: x[2], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════
#  Bridge elaboration — §9.5–9.8: restriction to stalks, demand pairing,
#  covering verification, obstruction detection, filtration grading
# ═══════════════════════════════════════════════════════════════════════

def _elaborate_bridge(
    bridge: BridgeProposition,
    problem: str,
    problem_locus: list[SubDomain],
) -> BridgeProposition:
    """Elaborate a bridge proposition per the full §9 procedure.

    A raw H¹ search returns a *cohomology class* — a sketch that lives on the
    overlap U₁₂.  Elaboration is the geometric process of turning that class
    into a concrete, validated, implementation-ready section by:

    1. **Restriction to stalks** (Def 9.4): for each D ∈ Loc(p), compute the
       germ σ|_D — how the bridge manifests in that sub-domain.
    2. **Demand pairing** (Def 9.5): for each D ∈ Loc(p), compute ⟨σ|_D, p|_D⟩
       — how much of that sub-domain's demand the bridge addresses.  The pairing
       is monotone under restriction: focusing on a facet can only increase it.
    3. **Covering verification** (Def 10.2): check that the components actually
       cover Supp(σ) — that no region of the overlap is left unaddressed.
       Adjust covering_dimension to match the verified count.
    4. **Obstruction detection**: find where local restrictions fail to glue —
       internal contradictions, under-specified interfaces, data gaps.
    5. **Relevance filtration** (Def 9.6): assign filtration level (1=tangential
       → 4=transformative) per germ, take max over all stalks.

    The agent is prompted with the geometric constraints directly so that its
    output respects the sheaf conditions.
    """
    locus_names = [sd.name for sd in problem_locus]
    component_list = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(bridge.components))

    prompt = f"""You previously proposed the bridge idea "{bridge.title}":

{bridge.description}

It lives at the overlap of "{bridge.source_domain}" and "{bridge.target_domain}".
The problem is: "{problem}"
The problem locus (sub-domains where p ≠ 0) is: {', '.join(locus_names)}

The proposed components (covering elements) are:
{component_list}

Now ELABORATE this bridge by performing the following geometric operations.
Each operation corresponds to a precise sheaf-theoretic step.

── STEP 1: RESTRICTION TO STALKS (Definition 9.4) ──
For EACH sub-domain D in the problem locus, compute the germ σ|_D:
how does this bridge idea look when restricted to that specific facet?
What concrete technique or module does it become in the language of D?
A germ is zero if the bridge has nothing to say about that sub-domain.

── STEP 2: DEMAND PAIRING (Definition 9.5) ──
For each sub-domain D, compute ⟨σ|_D, p|_D⟩ ∈ [0,1]: how much of
sub-domain D's unmet need does this bridge address? The pairing is
local — judge each facet independently. 0 = irrelevant, 1 = fully
resolves that facet of the problem.

── STEP 3: COVERING VERIFICATION (Definition 10.2) ──
Check that the components (covering elements) genuinely cover Supp(σ):
- Are there regions of the overlap that NO component addresses? (gaps)
- Are there components that are redundant (cover the same region)? (excess)
- Should any component be split into two independent pieces?
- Should any two components be merged?
Return the VERIFIED component list and the true covering dimension.

── STEP 4: OBSTRUCTION DETECTION ──
Find places where local restrictions fail to glue:
- Do any two components assume incompatible interfaces?
- Are there data requirements that contradict each other?
- Are there algorithmic assumptions in one component that break another?
These are the descent obstructions — they must be resolved before
implementation.

── STEP 5: RELEVANCE FILTRATION (Definition 9.6) ──
For each germ σ|_D, classify its filtration level:
  1 = tangentially relevant (same area, different problem)
  2 = partially relevant (addresses one aspect of p)
  3 = directly relevant (addresses p as stated)
  4 = transformatively relevant (reframes p, dissolving the tension)
The overall filtration level is the maximum over all stalks.

── STEP 6: REFINED DESCRIPTION ──
Based on the above analysis, write a refined description of the bridge
that incorporates the stalk analysis, acknowledges the obstructions,
and specifies the verified architecture.

Respond as JSON:
{{
    "stalks": [
        {{"sub_domain": "...", "germ": "what the bridge becomes in this sub-domain",
          "is_zero": false, "demand_pairing": 0.75,
          "filtration_level": 3}}
    ],
    "verified_components": [
        "component_1: what it does (covers region X of the overlap)",
        "component_2: ..."
    ],
    "gaps": ["any uncovered regions of the overlap"],
    "redundancies": ["any redundant components"],
    "obstructions": [
        {{"location": "between component X and Y",
          "description": "what fails to glue",
          "severity": "high/medium/low",
          "repair": "how to fix it"}}
    ],
    "covering_dimension": 10,
    "max_filtration_level": 3,
    "overall_demand_pairing": 0.7,
    "refined_description": "...",
    "refined_proof_sketch": "...",
    "refined_obligations": ["obligation1", ...]
}}"""

    data, section = agent_json(
        prompt,
        surface=SurfaceKind.THEORY,
        coordinate=f"ideation.elaborate.{bridge.source_domain}_{bridge.target_domain}",
    )

    # ── Apply elaboration results to the bridge ──────────────────────
    stalks = data.get("stalks", [])
    verified_components = data.get("verified_components", bridge.components)
    obstructions = data.get("obstructions", [])

    # Covering dimension from verified components
    new_covdim = int(data.get("covering_dimension", len(verified_components)))

    # Max filtration level across all non-zero germs (Def 9.6)
    max_level = int(data.get("max_filtration_level", bridge.relevance_level.value))
    if stalks:
        stalk_levels = [s.get("filtration_level", 1) for s in stalks
                        if not s.get("is_zero", False)]
        if stalk_levels:
            max_level = max(max_level, max(stalk_levels))
    max_level = min(4, max(1, max_level))

    # Overall demand pairing — average of non-zero germs' pairings
    if stalks:
        pairings = [s.get("demand_pairing", 0.0) for s in stalks
                    if not s.get("is_zero", False)]
        avg_pairing = sum(pairings) / len(pairings) if pairings else 0.0
    else:
        avg_pairing = bridge.relevance_score

    # Refined description
    refined_desc = data.get("refined_description", bridge.description)
    refined_proof = data.get("refined_proof_sketch", bridge.proof_sketch)
    refined_obligations = data.get("refined_obligations", bridge.open_obligations)

    # Obstruction data for metadata
    obstruction_summary = [
        f"{o.get('location', '?')}: {o.get('description', '?')} "
        f"[{o.get('severity', '?')}]"
        for o in obstructions
    ]

    # Stalk summary for metadata
    stalk_summary = {
        s.get("sub_domain", "?"): {
            "germ": s.get("germ", ""),
            "is_zero": s.get("is_zero", False),
            "demand_pairing": s.get("demand_pairing", 0.0),
            "filtration_level": s.get("filtration_level", 1),
        }
        for s in stalks
    }

    # Build the elaborated bridge with updated scores
    elaborated = BridgeProposition(
        title=bridge.title,
        description=refined_desc,
        source_domain=bridge.source_domain,
        target_domain=bridge.target_domain,
        coordinate=bridge.coordinate,
        assertion_type=bridge.assertion_type,
        novelty_score=bridge.novelty_score,  # novelty doesn't change
        relevance_score=avg_pairing,  # updated from demand pairing
        relevance_level=RelevanceFiltrationLevel(max_level),
        covering_dimension=new_covdim,
        components=verified_components,
        proof_sketch=refined_proof,
        open_obligations=refined_obligations,
        trust=bridge.trust,
        metadata={
            **bridge.metadata,
            "elaborated": True,
            "stalks": stalk_summary,
            "obstructions": obstruction_summary,
            "gaps": data.get("gaps", []),
            "redundancies": data.get("redundancies", []),
            "pre_elaboration": {
                "relevance_score": bridge.relevance_score,
                "relevance_level": bridge.relevance_level.value,
                "covering_dimension": bridge.covering_dimension,
                "UNS": bridge.useful_novelty_score,
            },
        },
    )

    return elaborated


# ═══════════════════════════════════════════════════════════════════════
#  The full ideation pipeline
# ═══════════════════════════════════════════════════════════════════════

def run_ideation(
    prompt: str,
    *,
    n_partner_candidates: int = 3,
    n_propositions: int = 5,
    n_idea_sources: int = 3,
    verbose: bool = False,
) -> IdeationResult:
    """Run the full geometry-of-ideation pipeline (§9–§10).

    This is the master function that orchestrates:
      §9: Pairwise cross-domain synthesis (H¹ on D₁ × D₂)
      §10: Compositional tower (H^{n-1} on D₁ × … × D_n)

    The n_idea_sources parameter controls composition depth per §10:
      n=2 → pairwise only (classical cross-domain ideation)
      n=3 → triple composition (default — Theorem 10.3 sweet spot)
      n=4+ → deeper composition (diminishing returns past n=4)

    At each level, a new domain is added only if it increases covering
    dimension (Theorem 10.3 — Compositional Saturation).
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

    # Step 4: Select partner domains (descent-validated)
    if verbose:
        print("IDEATION: Selecting partner domains...", flush=True)
    partner_candidates, cached_morphisms = select_partner_domains(
        primary, problem, locus, n_candidates=n_partner_candidates, verbose=verbose)

    if not partner_candidates:
        partner_candidates = [("optimization", ExcessNoveltyFraction(
            domain_1=primary.name, domain_2="optimization",
            enf=0.5, avg_morphism_strength=0.5,
            semantic_distance=3.0, verdict="fallback"))]
        cached_morphisms = {}

    # Step 5–6: For EACH surviving partner, search H^1 (reusing cached morphisms).
    # Morphisms were already discovered during validation — no need to re-discover.
    # Only the H¹ search is new. This halves the per-partner agent call count.
    #
    # Geometric justification for parallelism: each partner's search space
    # Φ_p(D_2) = H¹(Loc(p) × D_2, S) ∩ {σ_p ≠ 0} is independent — the
    # fiber products don't interact until the tournament (Theorem 9.3).
    all_propositions: list[BridgeProposition] = []
    all_morphisms: list[MethodologicalTranslation] = []
    partner_morphism_map: dict[str, list[MethodologicalTranslation]] = {}
    n_explore = min(len(partner_candidates), 3)  # explore top 3

    def _explore_partner(
        idx: int,
        partner_name: str,
        partner_enf: ExcessNoveltyFraction,
    ) -> tuple[str, list[MethodologicalTranslation], list[BridgeProposition]]:
        """Explore one partner: reuse cached morphisms → H¹ search."""
        partner_desc = partner_enf.domain_2
        if verbose:
            print(f"IDEATION: [{idx+1}/{n_explore}] Exploring partner "
                  f"'{partner_name}' (ENF={partner_enf.enf:.2f}, "
                  f"strength={partner_enf.avg_morphism_strength:.2f})...",
                  flush=True)

        # Reuse morphisms from validation; only re-discover if cache miss
        morphisms = cached_morphisms.get(partner_name)
        if morphisms is None:
            morphisms = discover_cross_domain_morphisms(
                primary, partner_name, partner_desc, locus)

        if not morphisms:
            if verbose:
                print(f"  ✗ No morphisms found for {partner_name}", flush=True)
            return partner_name, [], []

        if verbose:
            print(f"  [{partner_name}] {len(morphisms)} morphisms "
                  f"(avg strength={sum(m.strength for m in morphisms)/len(morphisms):.2f})",
                  flush=True)

        propositions = search_h1_fiber(
            primary, partner_name, partner_desc,
            morphisms, problem, locus, n_propositions=n_propositions)

        if verbose:
            for bp in propositions[:2]:
                print(f"  [{partner_name}] Bridge: {bp.title} "
                      f"(UNS={bp.useful_novelty_score:.2f}, "
                      f"covdim={bp.covering_dimension})", flush=True)

        return partner_name, morphisms, propositions

    with ThreadPoolExecutor(max_workers=n_explore) as pool:
        futures = {
            pool.submit(_explore_partner, i, name, enf): name
            for i, (name, enf) in enumerate(partner_candidates[:n_explore])
        }
        for future in as_completed(futures):
            partner_name = futures[future]
            try:
                name, morphisms, propositions = future.result()
                partner_morphism_map[name] = morphisms
                all_morphisms.extend(morphisms)
                all_propositions.extend(propositions)
            except Exception as exc:
                if verbose:
                    print(f"  ✗ Partner '{partner_name}' failed: {exc}", flush=True)

    # Step 7: Tournament — select the best approach across ALL partners.
    # Scoring: UNS * min(covdim, 15) (Theorem 10.4: useful, novel, AND substantial).
    # This ensures we pick the globally best idea, not just the best from
    # the highest-ENF partner.
    if all_propositions:
        all_propositions.sort(
            key=lambda bp: bp.useful_novelty_score * min(bp.covering_dimension, 15),
            reverse=True)
        selected = all_propositions[0]
        if verbose:
            print(f"\nIDEATION: Tournament results ({len(all_propositions)} candidates "
                  f"from {n_explore} partners):", flush=True)
            for bp in all_propositions[:5]:
                score = bp.useful_novelty_score * min(bp.covering_dimension, 15)
                print(f"  {'→' if bp is selected else ' '} {bp.title} "
                      f"(UNS={bp.useful_novelty_score:.2f}, "
                      f"covdim={bp.covering_dimension}, "
                      f"score={score:.2f}, ~{bp.estimated_loc} LOC)",
                      flush=True)
    else:
        selected = None

    # ═══════════════════════════════════════════════════════════════════
    #  Steps 7b–7n: Compositional tower (§10, Theorem 10.3)
    #
    #  If n_idea_sources > 2, we climb the tower of domain compositions:
    #    Level 1 (done above): H¹(D₁ × D₂, S) — pairwise
    #    Level 2: H²(D₁ × D₂ × D₃, S) — triple, adds components
    #             impossible without all 3 domains
    #    Level k: H^k(D₁ × … × D_{k+1}, S) — k-fold
    #
    #  At each level: propose enriching domains → search H^n on the
    #  (n+1)-fold overlap → accept if covdim increases (Compositional
    #  Saturation: stop when marginal gain < SATURATION_THRESHOLD).
    # ═══════════════════════════════════════════════════════════════════
    SATURATION_THRESHOLD = 2  # min marginal covdim gain to justify a new domain

    if selected is not None and n_idea_sources > 2:
        # Track which domains are currently composed
        composed_domains = [primary.name, selected.target_domain]
        current_bridge = selected

        for level in range(2, n_idea_sources):
            if verbose:
                print(f"\nIDEATION: §10 compositional tower — level {level+1} "
                      f"({len(composed_domains)} domains, covdim={current_bridge.covering_dimension})",
                      flush=True)

            # Propose domains that could enrich the composition
            enriching = _propose_enriching_domains(
                primary, composed_domains, current_bridge, problem,
                n_candidates=4)

            if not enriching:
                if verbose:
                    print(f"  No enriching domains found — tower saturated.", flush=True)
                break

            # Try top candidates in parallel
            best_enriched = None
            best_covdim_gain = 0

            def _try_enrichment(
                d_name: str, d_desc: str, est_gain: float,
            ) -> tuple[str, str, list[BridgeProposition]]:
                return d_name, d_desc, _search_compositional_overlap(
                    primary, composed_domains, d_name, d_desc,
                    current_bridge, problem, locus, n_propositions=3)

            with ThreadPoolExecutor(max_workers=min(len(enriching), 3)) as pool:
                futures = {
                    pool.submit(_try_enrichment, name, desc, gain): name
                    for name, desc, gain in enriching[:3]
                }
                for future in as_completed(futures):
                    try:
                        d_name, d_desc, enriched_props = future.result()
                        for ep in enriched_props:
                            gain = ep.covering_dimension - current_bridge.covering_dimension
                            score = ep.useful_novelty_score * min(ep.covering_dimension, 20)
                            if gain > best_covdim_gain:
                                best_covdim_gain = gain
                                best_enriched = ep
                            if verbose:
                                print(f"  [{d_name}] {ep.title}: "
                                      f"covdim {current_bridge.covering_dimension}→{ep.covering_dimension} "
                                      f"(+{gain}), score={score:.2f}", flush=True)
                    except Exception as exc:
                        if verbose:
                            print(f"  ✗ Enrichment failed: {exc}", flush=True)

            # Compositional Saturation check (Theorem 10.3)
            if best_enriched is None or best_covdim_gain < SATURATION_THRESHOLD:
                if verbose:
                    gain_str = f"+{best_covdim_gain}" if best_enriched else "none"
                    print(f"  Marginal gain ({gain_str}) < threshold ({SATURATION_THRESHOLD}) "
                          f"— tower saturated at level {level}.", flush=True)
                break

            # Accept the enrichment
            new_domain = best_enriched.metadata.get("domains", composed_domains)[-1]
            composed_domains.append(new_domain)
            current_bridge = best_enriched
            selected = best_enriched
            all_propositions.append(best_enriched)

            if verbose:
                print(f"  ✓ Accepted {new_domain} — covdim now {best_enriched.covering_dimension} "
                      f"(+{best_covdim_gain}), {len(composed_domains)} domains composed",
                      flush=True)

        if verbose and len(composed_domains) > 2:
            print(f"\nIDEATION: Final composition: {' × '.join(composed_domains)} "
                  f"(covdim={selected.covering_dimension}, "
                  f"~{selected.estimated_loc} LOC)", flush=True)

    # Step 8: Elaborate the selected bridge via full §9 procedure.
    # This is NOT just "flesh out the idea." It is the geometric process of:
    #   (a) restricting the H¹ class to stalks at each D ∈ Loc(p)  (Def 9.4)
    #   (b) pairing each germ with the demand section ⟨σ|_D, p|_D⟩  (Def 9.5)
    #   (c) verifying covering dimension matches Supp(σ)  (Def 10.2)
    #   (d) detecting descent obstructions (where germs fail to glue)
    #   (e) grading by relevance filtration (1→4)  (Def 9.6)
    # The elaborated bridge has updated relevance_score (from demand pairing),
    # relevance_level (from filtration), covering_dimension (from verification),
    # and metadata recording all stalks and obstructions.
    if selected is not None:
        if verbose:
            print(f"\nIDEATION: Elaborating winner '{selected.title}'...", flush=True)
        selected = _elaborate_bridge(selected, problem, locus)
        if verbose:
            pre = selected.metadata.get("pre_elaboration", {})
            print(f"  Elaborated: relevance {pre.get('relevance_score', '?'):.2f} → "
                  f"{selected.relevance_score:.2f}, "
                  f"filtration {pre.get('relevance_level', '?')} → "
                  f"{selected.relevance_level.value}, "
                  f"covdim {pre.get('covering_dimension', '?')} → "
                  f"{selected.covering_dimension}", flush=True)
            obs = selected.metadata.get("obstructions", [])
            if obs:
                print(f"  ⚠ {len(obs)} obstruction(s) detected:", flush=True)
                for o in obs[:3]:
                    print(f"    - {o}", flush=True)
            gaps = selected.metadata.get("gaps", [])
            if gaps:
                print(f"  ⚠ {len(gaps)} covering gap(s): {', '.join(gaps[:3])}", flush=True)

    # Build productive pairing criterion.
    # For composed bridges, target_domain is "D₂ × D₃ × ..." — extract the
    # primary partner (first after primary domain) for backward compatibility.
    if selected:
        td = selected.target_domain
        # For composed bridges, target_domain looks like "D₁ × D₂ × D₃"
        _parts = [p.strip() for p in td.split("×")] if "×" in td else [td]
        best_partner_name = _parts[0] if _parts else td
    else:
        best_partner_name = partner_candidates[0][0]
    best_enf = next(
        (enf for name, enf in partner_candidates if name == best_partner_name),
        partner_candidates[0][1])
    best_morphisms = partner_morphism_map.get(best_partner_name, all_morphisms)
    avg_strength = (sum(m.strength for m in best_morphisms) / len(best_morphisms)
                   if best_morphisms else 0.0)
    pairing = ProductivePairingCriterion(
        source_domain=primary.name,
        target_domain=best_partner_name,
        avg_strength=avg_strength,
        semantic_distance=best_enf.semantic_distance if best_enf else 5.0,
    )

    # Determine composition level for metadata
    composition_level = 1
    composed = [primary.name]
    if selected and "composition_level" in selected.metadata:
        composition_level = selected.metadata["composition_level"]
        composed = selected.metadata.get("domains", composed)
    elif selected:
        composed = [primary.name, selected.target_domain]
        composition_level = 2

    elapsed = time.time() - t0
    return IdeationResult(
        prompt=prompt,
        primary_domain=primary,
        partner_domain=None,
        morphisms=all_morphisms,
        bridge_propositions=all_propositions,
        selected_approach=selected,
        enf=best_enf,
        pairing_criterion=pairing,
        elapsed=elapsed,
        metadata={
            "domain_name": domain_name,
            "problem": problem,
            "partner": best_partner_name,
            "partners_explored": [name for name, _ in partner_candidates[:n_explore]],
            "tournament_size": len(all_propositions),
            "locus": [sd.name for sd in locus],
            "n_idea_sources": n_idea_sources,
            "composition_level": composition_level,
            "composed_domains": composed,
        },
    )
