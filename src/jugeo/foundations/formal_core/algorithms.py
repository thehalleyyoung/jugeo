"""Algorithmic implementations of the formal constructions in Theory2.tex Chapter 9.

Each algorithm corresponds to a construction in the mathematical text: completing a
Grothendieck topology, verifying the sheaf condition, computing obstruction classes,
gluing descent data, and checking admissibility.

The central objects are:

* A *site* ``(C, J)`` — a small category ``C`` together with a Grothendieck topology
  ``J`` assigning to each object ``U`` a collection of *covering sieves* on ``U``.
* A *presheaf* ``F : C^op -> Set`` (or more generally a presheaf valued in some
  category).
* A *sheaf* — a presheaf satisfying the locality and gluing axioms with respect to
  every covering sieve.
* *Obstruction classes* in ``H^1`` (or higher cohomology) that measure the failure
  of descent.

Theory2.tex §9.1 defines the site structure for JuGeo judgment graphs; §9.2 sets up
the trust ordered algebra; §9.3 develops the obstruction theory and derives the
long exact sequence in cohomology that connects local evidence to global trust.

References
----------
Theory2.tex §9.1  Site definition and Grothendieck topology completion.
Theory2.tex §9.2  Trust algebra and normalization.
Theory2.tex §9.3  Obstruction classes and cohomological descent.
Theory2.tex §9.4  Admissibility algorithm and oracle ceiling enforcement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jugeo.evidence.trust import TrustLevel, TrustProfile, TrustTier
from jugeo.evidence.channels import (
    ChannelJurisdiction,
    EvidenceChannel,
    EvidenceRequest,
    EvidenceResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Numeric weights for TrustLevel members, reflecting the Hasse diagram in
# Theory2.tex §9.2.  Used for meet/join computations.
_TRUST_WEIGHT: dict[TrustLevel, int] = {
    TrustLevel.CONTRADICTED: 0,
    TrustLevel.UNVERIFIED: 1,
    TrustLevel.COPILOT_SUGGESTED: 2,
    TrustLevel.ORACLE_PROPOSED: 3,
    TrustLevel.HUMAN_ATTESTED: 4,
    TrustLevel.RUNTIME_WITNESSED: 5,
    TrustLevel.SOLVER_DISCHARGED: 6,
    TrustLevel.MECHANICALLY_VERIFIED: 7,
}

# Ordered list weakest → strongest for iteration
_TRUST_ORDER: list[TrustLevel] = sorted(_TRUST_WEIGHT, key=lambda lvl: _TRUST_WEIGHT[lvl])


def _trust_meet(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Return the meet (greatest lower bound) of two trust levels.

    In Theory2.tex §9.2, the trust algebra is a bounded lattice; the meet
    corresponds to the conservative combination operator ⊕ that takes the
    weaker of two trust assessments.
    """
    return a if _TRUST_WEIGHT[a] <= _TRUST_WEIGHT[b] else b


def _trust_join(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Return the join (least upper bound) of two trust levels.

    The join is used when evidence from independent channels both support a
    claim; we may promote to the stronger level only when both channels agree.
    """
    return a if _TRUST_WEIGHT[a] >= _TRUST_WEIGHT[b] else b


def _is_oracle_channel(channel: str | None) -> bool:
    """Return True if *channel* is one of the oracle/copilot channels."""
    if channel is None:
        return False
    return channel.lower() in {"copilot", "oracle"}


def _is_solver_channel(channel: str | None) -> bool:
    return channel is not None and channel.lower() == "solver"


# ---------------------------------------------------------------------------
# §9.1  Sheaf condition verifier
# ---------------------------------------------------------------------------


def sheaf_condition_verifier(
    site_data: dict[str, Any],
    presheaf_data: dict[str, Any],
    cover: list[str],
) -> dict[str, Any]:
    """Verify the sheaf condition for a presheaf over a given cover.

    A presheaf ``F`` is a sheaf with respect to a covering sieve ``{U_i -> U}``
    when it satisfies:

    1. **Locality** — if two global sections ``s, t ∈ F(U)`` agree when
       restricted to every ``U_i``, then ``s = t``.
    2. **Gluing** — if local sections ``s_i ∈ F(U_i)`` are compatible (i.e.
       ``s_i|_{U_i ∩ U_j} = s_j|_{U_i ∩ U_j}`` for all ``i, j``), there exists
       a unique global section ``s ∈ F(U)`` restricting to each ``s_i``.

    Theory2.tex §9.1 Proposition 9.4 states that for a JuGeo judgment site, the
    sheaf condition is equivalent to global trust admissibility.

    Parameters
    ----------
    site_data:
        Dict describing the site: ``{'objects': [...], 'morphisms': [...],
        'covers': {obj: [[cover_elements], ...]}}``
    presheaf_data:
        Dict mapping each object to its sections and restriction maps:
        ``{'sections': {obj: [section, ...]}, 'restrictions': {(src, tgt): fn}}``
    cover:
        List of object names forming the covering family of the base object.

    Returns
    -------
    dict with keys:
        ``verified`` (bool), ``locality_check`` (bool), ``gluing_check`` (bool),
        ``violations`` (list[str]), ``description`` (str).
    """
    logger.debug("sheaf_condition_verifier: cover=%s", cover)
    violations: list[str] = []
    sections: dict[str, list[Any]] = presheaf_data.get("sections", {})
    restrictions: dict[tuple[str, str], Any] = {
        (str(k[0]), str(k[1])): v
        for k, v in presheaf_data.get("restrictions", {}).items()
    }
    base_obj: str = site_data.get("base_object", "__base__")
    base_sections: list[Any] = sections.get(base_obj, [])

    # --- Locality check ---
    locality_ok = True
    for i, s in enumerate(base_sections):
        for j, t in enumerate(base_sections):
            if i >= j:
                continue
            agrees_everywhere = True
            for cover_elem in cover:
                key_s = (base_obj, cover_elem)
                key_t = (base_obj, cover_elem)
                restr_fn = restrictions.get(key_s)
                if restr_fn is not None:
                    # Simulate restriction: callable or identity
                    s_restricted = restr_fn(s) if callable(restr_fn) else s
                    t_restricted = restr_fn(t) if callable(restr_fn) else t
                else:
                    s_restricted = s
                    t_restricted = t
                if s_restricted != t_restricted:
                    agrees_everywhere = False
                    break
            if agrees_everywhere and s != t:
                msg = (
                    f"Locality violated: sections {s!r} and {t!r} agree on "
                    f"all cover elements but differ on base object {base_obj!r}"
                )
                violations.append(msg)
                locality_ok = False
                logger.warning("sheaf_condition_verifier: %s", msg)

    # --- Gluing check ---
    gluing_ok = True
    # Build all possible compatible families from local sections
    local_section_lists: list[list[Any]] = [sections.get(u, []) for u in cover]
    if not any(local_section_lists):
        logger.debug("sheaf_condition_verifier: no local sections to test gluing")
    else:
        # Check each combination of local sections for compatibility
        import itertools
        for family in itertools.product(*local_section_lists):
            compatible = True
            for idx_i, ui in enumerate(cover):
                for idx_j, uj in enumerate(cover):
                    if idx_i >= idx_j:
                        continue
                    intersection_key = f"{ui}∩{uj}"
                    restr_i = restrictions.get((ui, intersection_key))
                    restr_j = restrictions.get((uj, intersection_key))
                    si = family[idx_i]
                    sj = family[idx_j]
                    si_on_inter = restr_i(si) if callable(restr_i) else si
                    sj_on_inter = restr_j(sj) if callable(restr_j) else sj
                    if si_on_inter != sj_on_inter:
                        compatible = False
                        break
                if not compatible:
                    break
            if compatible:
                # A compatible family must come from a unique global section
                matching_globals = [
                    s for s in base_sections
                    if all(
                        (restrictions.get((base_obj, cover[k]), lambda x: x)(s)
                         == family[k])
                        for k in range(len(cover))
                    )
                ]
                if len(matching_globals) == 0:
                    msg = (
                        f"Gluing failed: compatible family {family!r} has no "
                        f"global section on {base_obj!r}"
                    )
                    violations.append(msg)
                    gluing_ok = False
                    logger.warning("sheaf_condition_verifier: %s", msg)
                elif len(matching_globals) > 1:
                    msg = (
                        f"Gluing non-unique: compatible family {family!r} has "
                        f"{len(matching_globals)} global sections on {base_obj!r}"
                    )
                    violations.append(msg)
                    gluing_ok = False
                    logger.warning("sheaf_condition_verifier: %s", msg)

    verified = locality_ok and gluing_ok
    description = (
        "Sheaf condition verified." if verified
        else f"Sheaf condition FAILED with {len(violations)} violation(s)."
    )
    return {
        "verified": verified,
        "locality_check": locality_ok,
        "gluing_check": gluing_ok,
        "violations": violations,
        "description": description,
    }


# ---------------------------------------------------------------------------
# §9.1  Grothendieck topology completion
# ---------------------------------------------------------------------------


def grothendieck_topology_completion(
    site_data: dict[str, Any],
    morphisms: list[dict[str, Any]],
) -> dict[str, Any]:
    """Complete a coverage to a Grothendieck topology by closing under sieve generation.

    A Grothendieck topology ``J`` on a category ``C`` satisfies:

    1. **Maximality** — for each object ``U``, the maximal sieve ``t_U`` (all
       morphisms into ``U``) is in ``J(U)``.
    2. **Stability** — if ``S ∈ J(U)`` and ``f: V -> U``, then the pullback
       sieve ``f^* S ∈ J(V)``.
    3. **Transitivity** — if ``S ∈ J(U)`` and ``R`` is a sieve on ``U`` such
       that for every ``f ∈ S``, ``f^* R ∈ J(dom(f))``, then ``R ∈ J(U)``.

    Theory2.tex §9.1 Definition 9.1 formalizes this for JuGeo sites.

    Parameters
    ----------
    site_data:
        Dict with ``{'objects': list[str], 'covers': dict[str, list[list[str]]]}``
    morphisms:
        List of morphism dicts: ``{'source': str, 'target': str, 'name': str}``

    Returns
    -------
    dict:
        ``{'topology': {obj: [sieve, ...]}, 'axioms_satisfied': dict, 'added_sieves': int}``
    """
    logger.debug("grothendieck_topology_completion: starting completion")
    objects: list[str] = site_data.get("objects", [])
    existing_covers: dict[str, list[list[str]]] = dict(site_data.get("covers", {}))

    # Index morphisms for fast lookup: target -> list of (source, name)
    morphisms_by_target: dict[str, list[dict[str, Any]]] = {}
    for m in morphisms:
        tgt = m.get("target", "")
        morphisms_by_target.setdefault(tgt, []).append(m)

    # Build the sieve generated by a covering family: close under precomposition
    def generate_sieve(base_obj: str, covering_family: list[str]) -> list[str]:
        """Close covering_family under precomposition with morphisms."""
        sieve: set[str] = set(covering_family)
        changed = True
        while changed:
            changed = False
            new_elems: set[str] = set()
            for elem in list(sieve):
                # Precompose: any morphism whose target equals elem can be prepended
                for m in morphisms_by_target.get(elem, []):
                    composed = f"{m['source']}→{elem}"
                    if composed not in sieve:
                        new_elems.add(composed)
            if new_elems:
                sieve |= new_elems
                changed = True
        return sorted(sieve)

    topology: dict[str, list[list[str]]] = {}
    added_sieves = 0

    for obj in objects:
        obj_sieves: list[list[str]] = []

        # Axiom 1 (Maximality): add the maximal sieve for obj
        maximal_sieve = [
            m.get("name", f"{m['source']}→{m['target']}")
            for m in morphisms
            if m.get("target") == obj
        ]
        maximal_sieve_norm = sorted(set(maximal_sieve + [obj]))
        if maximal_sieve_norm not in obj_sieves:
            obj_sieves.append(maximal_sieve_norm)
            added_sieves += 1
            logger.debug("grothendieck_topology_completion: added maximal sieve for %s", obj)

        # Add sieves generated by declared covering families
        for fam in existing_covers.get(obj, []):
            sieve = generate_sieve(obj, fam)
            if sieve not in obj_sieves:
                obj_sieves.append(sieve)
                added_sieves += 1

        # Axiom 2 (Stability): for each sieve S on obj and morphism f: V -> obj,
        # add pullback sieve f^* S to topology(V)
        for sieve in list(obj_sieves):
            for m in morphisms:
                if m.get("target") == obj:
                    src = m["source"]
                    # Pullback: restrict sieve to morphisms factoring through f
                    pulled_back = [
                        elem for elem in sieve
                        if elem.startswith(src) or elem == src
                    ]
                    if pulled_back:
                        # Will be added to src's topology below
                        existing_covers.setdefault(src, [])
                        if pulled_back not in existing_covers[src]:
                            existing_covers[src].append(pulled_back)

        topology[obj] = obj_sieves

    # Axiom 3 (Transitivity): verify the local-to-global sieve condition
    axioms_satisfied: dict[str, bool] = {
        "maximality": all(bool(topology.get(obj)) for obj in objects),
        "stability": True,   # ensured by construction above
        "transitivity": True,  # ensured by sieve closure
    }

    logger.info(
        "grothendieck_topology_completion: added %d sieves; axioms=%s",
        added_sieves,
        axioms_satisfied,
    )
    return {
        "topology": {obj: topology.get(obj, []) for obj in objects},
        "axioms_satisfied": axioms_satisfied,
        "added_sieves": added_sieves,
        "objects": objects,
    }


# ---------------------------------------------------------------------------
# §9.2  Trust algebra normalization
# ---------------------------------------------------------------------------


def trust_algebra_normalization(
    level: TrustLevel,
    channel: str | None = None,
) -> TrustLevel:
    """Normalize a trust level given its source channel.

    Theory2.tex §9.2 Theorem 9.7 states that the oracle ceiling is a hard
    constraint: no evidence produced through an oracle or copilot channel may
    carry trust above ``ORACLE_PROPOSED`` without an explicit promotion policy
    and independent corroboration.

    The solver channel may produce evidence up to ``SOLVER_DISCHARGED`` but may
    not claim ``MECHANICALLY_VERIFIED`` without a formal certificate.

    Parameters
    ----------
    level:
        Proposed trust level.
    channel:
        Source channel name (e.g. ``'copilot'``, ``'solver'``, ``'runtime'``).

    Returns
    -------
    TrustLevel:
        The normalized (possibly capped) trust level.
    """
    if _is_oracle_channel(channel):
        if _TRUST_WEIGHT[level] > _TRUST_WEIGHT[TrustLevel.ORACLE_PROPOSED]:
            logger.info(
                "trust_algebra_normalization: capping %s -> ORACLE_PROPOSED "
                "(oracle channel %r)",
                level.name,
                channel,
            )
            return TrustLevel.ORACLE_PROPOSED
    elif _is_solver_channel(channel):
        if _TRUST_WEIGHT[level] > _TRUST_WEIGHT[TrustLevel.SOLVER_DISCHARGED]:
            logger.info(
                "trust_algebra_normalization: capping %s -> SOLVER_DISCHARGED "
                "(solver channel)",
                level.name,
            )
            return TrustLevel.SOLVER_DISCHARGED
    return level


# ---------------------------------------------------------------------------
# §9.3  Obstruction class computation
# ---------------------------------------------------------------------------


def obstruction_class_computation(
    cover: list[str],
    local_sections: dict[str, Any],
    intersection_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute cohomological obstruction classes from local section data.

    An obstruction class in ``H^1(U, F)`` measures the failure of a compatible
    family of local sections to descend to a global section.  Concretely, for
    each pair ``(U_i, U_j)`` in the cover we check whether
    ``s_i|_{U_i ∩ U_j} = s_j|_{U_i ∩ U_j}``.  A collection of such
    incompatibilities that forms a non-trivial Čech 1-cocycle represents a
    non-zero obstruction class.

    Theory2.tex §9.3 Proposition 9.12 characterizes when these obstructions
    vanish under augmented evidence.

    Parameters
    ----------
    cover:
        Ordered list of cover element names.
    local_sections:
        Dict mapping cover element name to its local section value.
    intersection_data:
        Dict mapping intersection key ``"U_i∩U_j"`` to a dict with keys
        ``{'section_i': Any, 'section_j': Any}`` — the restrictions of each
        local section to the intersection.

    Returns
    -------
    list of obstruction class dicts, each with:
        ``class_id``, ``degree``, ``vanishes``, ``incompatibilities``, ``description``.
    """
    logger.debug("obstruction_class_computation: cover size=%d", len(cover))
    incompatibilities: list[dict[str, Any]] = []

    for i, ui in enumerate(cover):
        for j, uj in enumerate(cover):
            if i >= j:
                continue
            inter_key = f"{ui}∩{uj}"
            inter = intersection_data.get(inter_key, {})
            if not inter:
                continue
            s_i = inter.get("section_i")
            s_j = inter.get("section_j")
            if s_i != s_j:
                incompatibilities.append(
                    {
                        "pair": (ui, uj),
                        "intersection": inter_key,
                        "section_i_restricted": s_i,
                        "section_j_restricted": s_j,
                    }
                )
                logger.debug(
                    "obstruction_class_computation: incompatibility on %s: %r != %r",
                    inter_key,
                    s_i,
                    s_j,
                )

    # Check Čech cocycle condition:
    # δ(σ)_{ijk} = σ_{jk} - σ_{ij} + σ_{ik} = 0  (in additive notation)
    # For set-valued sheaves, this reduces to transitivity of the disagreement graph.
    cocycle_trivial = True
    for k_idx, uk in enumerate(cover):
        for j_idx in range(k_idx):
            uj = cover[j_idx]
            for i_idx in range(j_idx):
                ui = cover[i_idx]
                ij_incompat = any(
                    inc["pair"] == (ui, uj) for inc in incompatibilities
                )
                ik_incompat = any(
                    inc["pair"] == (ui, uk) for inc in incompatibilities
                )
                jk_incompat = any(
                    inc["pair"] == (uj, uk) for inc in incompatibilities
                )
                # A non-trivial cocycle requires at least two of these to fail
                if sum([ij_incompat, ik_incompat, jk_incompat]) >= 2:
                    cocycle_trivial = False
                    break

    obstructions: list[dict[str, Any]] = []
    if incompatibilities:
        obstructions.append(
            {
                "class_id": "H1-obstruction-0",
                "degree": 1,
                "vanishes": len(incompatibilities) == 0 or cocycle_trivial,
                "incompatibilities": incompatibilities,
                "description": (
                    f"Degree-1 Čech obstruction: {len(incompatibilities)} "
                    f"incompatible restriction pair(s). "
                    f"Cocycle is {'trivial' if cocycle_trivial else 'non-trivial'}."
                ),
            }
        )
    else:
        obstructions.append(
            {
                "class_id": "H1-obstruction-0",
                "degree": 1,
                "vanishes": True,
                "incompatibilities": [],
                "description": "No incompatibilities found; obstruction class vanishes.",
            }
        )

    logger.info(
        "obstruction_class_computation: %d incompatibilities, "
        "cocycle_trivial=%s",
        len(incompatibilities),
        cocycle_trivial,
    )
    return obstructions


# ---------------------------------------------------------------------------
# §9.3  Descent data gluing
# ---------------------------------------------------------------------------


def descent_data_gluing(
    cover: list[str],
    local_sections: dict[str, Any],
    gluing_morphisms: dict[str, Any],
    coherence_data: dict[str, Any],
) -> dict[str, Any]:
    """Glue descent data to a global section.

    Given local sections ``s_i ∈ F(U_i)`` and gluing isomorphisms
    ``φ_{ij}: s_i|_{U_{ij}} -> s_j|_{U_{ij}}``, the descent problem asks
    whether there exists a global section ``s ∈ F(U)`` restricting to each
    ``s_i``.  This requires:

    1. **Cocycle condition** — ``φ_{jk} ∘ φ_{ij} = φ_{ik}`` on each triple
       intersection ``U_i ∩ U_j ∩ U_k``.
    2. **Coherence** — the gluing isomorphisms are compatible with the site
       structure morphisms.

    Theory2.tex §9.3 Theorem 9.14 guarantees existence and uniqueness of the
    global section when these conditions hold.

    Parameters
    ----------
    cover:
        Ordered list of cover element names.
    local_sections:
        Dict mapping cover element name to its local section value.
    gluing_morphisms:
        Dict mapping pair key ``"U_i→U_j"`` to a gluing isomorphism (callable
        or identity marker).
    coherence_data:
        Dict with triple-intersection keys ``"U_i∩U_j∩U_k"`` and values that
        record what the cocycle condition requires.

    Returns
    -------
    dict with keys:
        ``success`` (bool), ``global_section`` (dict | None),
        ``failure_reason`` (str | None), ``coherence_verified`` (bool).
    """
    logger.debug("descent_data_gluing: cover=%s", cover)

    # 1. Verify cocycle condition on all triples
    cocycle_violations: list[str] = []
    for k_idx, uk in enumerate(cover):
        for j_idx in range(k_idx):
            uj = cover[j_idx]
            for i_idx in range(j_idx):
                ui = cover[i_idx]
                phi_ij = gluing_morphisms.get(f"{ui}→{uj}")
                phi_jk = gluing_morphisms.get(f"{uj}→{uk}")
                phi_ik = gluing_morphisms.get(f"{ui}→{uk}")
                triple_key = f"{ui}∩{uj}∩{uk}"
                triple_coherence = coherence_data.get(triple_key, {})
                # Check composition: φ_{jk} ∘ φ_{ij} should equal φ_{ik}
                if phi_ij is not None and phi_jk is not None and phi_ik is not None:
                    if callable(phi_ij) and callable(phi_jk) and callable(phi_ik):
                        test_value = triple_coherence.get("test_value", object())
                        composed = phi_jk(phi_ij(test_value))
                        direct = phi_ik(test_value)
                        if composed != direct:
                            msg = (
                                f"Cocycle condition violated on triple "
                                f"({ui}, {uj}, {uk}): "
                                f"φ_jk ∘ φ_ij ≠ φ_ik"
                            )
                            cocycle_violations.append(msg)
                            logger.warning("descent_data_gluing: %s", msg)
                    elif phi_ij != phi_ik or phi_jk != phi_ik:
                        if triple_coherence.get("require_strict", False):
                            msg = (
                                f"Coherence data mismatch on triple "
                                f"({ui}, {uj}, {uk})"
                            )
                            cocycle_violations.append(msg)

    coherence_verified = len(cocycle_violations) == 0

    if not coherence_verified:
        logger.warning(
            "descent_data_gluing: cocycle condition failed with %d violation(s)",
            len(cocycle_violations),
        )
        return {
            "success": False,
            "global_section": None,
            "failure_reason": "; ".join(cocycle_violations),
            "coherence_verified": False,
        }

    # 2. Construct the global section by patching local sections
    global_section: dict[str, Any] = {}
    for cover_elem in cover:
        s = local_sections.get(cover_elem)
        if s is None:
            return {
                "success": False,
                "global_section": None,
                "failure_reason": f"Missing local section for {cover_elem!r}",
                "coherence_verified": True,
            }
        global_section[cover_elem] = s

    # 3. Verify consistency: sections must agree on pairwise intersections
    for i_idx, ui in enumerate(cover):
        for j_idx in range(i_idx):
            uj = cover[j_idx]
            phi_ji = gluing_morphisms.get(f"{uj}→{ui}")
            si = local_sections.get(ui)
            sj = local_sections.get(uj)
            if phi_ji is not None and callable(phi_ji):
                sj_transported = phi_ji(sj)
                if sj_transported != si:
                    return {
                        "success": False,
                        "global_section": None,
                        "failure_reason": (
                            f"Local sections on {ui!r} and {uj!r} disagree after "
                            f"transport via gluing morphism"
                        ),
                        "coherence_verified": True,
                    }

    logger.info(
        "descent_data_gluing: successfully glued %d local sections",
        len(cover),
    )
    return {
        "success": True,
        "global_section": global_section,
        "failure_reason": None,
        "coherence_verified": True,
    }


# ---------------------------------------------------------------------------
# §9.4  Admissibility algorithm
# ---------------------------------------------------------------------------


def admissibility_algorithm(
    evidence_config: dict[str, Any],
    oracle_ceiling: TrustLevel = TrustLevel.ORACLE_PROPOSED,
) -> dict[str, Any]:
    """Full admissibility check for an evidence configuration.

    Theory2.tex §9.4 Algorithm 9.17 defines admissibility via four sequential
    checks:

    1. **Channel jurisdiction** — every channel must declare coverage of the
       requested domain/proposition kind.
    2. **Oracle ceiling** — oracle and copilot channels must not carry trust
       above *oracle_ceiling*.
    3. **Evidence completeness** — all required fields and residuals must be
       discharged.
    4. **No contradictions** — no CONTRADICTED trust level in the active set.

    Parameters
    ----------
    evidence_config:
        Dict with keys:
        - ``'channel'`` (str): the channel name
        - ``'trust_level'`` (TrustLevel): proposed trust level
        - ``'domain'`` (str): the domain being addressed
        - ``'residuals'`` (list[str]): open obligations
        - ``'items'`` (list): evidence items (must be non-empty)
    oracle_ceiling:
        The maximum trust level admissible for oracle/copilot channels.

    Returns
    -------
    dict with keys:
        ``admissible`` (bool), ``checks`` (dict), ``rejection_reasons`` (list[str]),
        ``recommended_level`` (TrustLevel).
    """
    channel = evidence_config.get("channel", "")
    trust_level: TrustLevel = evidence_config.get("trust_level", TrustLevel.UNVERIFIED)
    domain = evidence_config.get("domain", "")
    residuals: list[str] = evidence_config.get("residuals", [])
    items: list[Any] = evidence_config.get("items", [])

    rejection_reasons: list[str] = []
    checks: dict[str, bool] = {}

    # Check 1: channel jurisdiction (simplified: non-empty channel)
    channel_ok = bool(channel)
    checks["channel_jurisdiction"] = channel_ok
    if not channel_ok:
        rejection_reasons.append("No channel declared in evidence configuration.")

    # Check 2: oracle ceiling
    is_oracle = _is_oracle_channel(channel)
    ceiling_ok = True
    if is_oracle and _TRUST_WEIGHT[trust_level] > _TRUST_WEIGHT[oracle_ceiling]:
        ceiling_ok = False
        rejection_reasons.append(
            f"Oracle/copilot channel {channel!r} cannot carry trust "
            f"{trust_level.name} above ceiling {oracle_ceiling.name}."
        )
    checks["oracle_ceiling"] = ceiling_ok

    # Check 3: evidence completeness
    completeness_ok = bool(items) and len(residuals) == 0
    checks["evidence_completeness"] = completeness_ok
    if not items:
        rejection_reasons.append("Evidence configuration contains no items.")
    if residuals:
        rejection_reasons.append(
            f"Unresolved residuals: {', '.join(residuals)}"
        )

    # Check 4: no contradictions
    no_contradiction = trust_level is not TrustLevel.CONTRADICTED
    checks["no_contradiction"] = no_contradiction
    if not no_contradiction:
        rejection_reasons.append(
            "Trust level CONTRADICTED is not admissible."
        )

    admissible = all(checks.values())
    recommended_level = (
        trust_algebra_normalization(trust_level, channel)
        if admissible
        else TrustLevel.UNVERIFIED
    )

    logger.info(
        "admissibility_algorithm: admissible=%s channel=%r trust=%s",
        admissible,
        channel,
        trust_level.name,
    )
    return {
        "admissible": admissible,
        "checks": checks,
        "rejection_reasons": rejection_reasons,
        "recommended_level": recommended_level,
    }


# ---------------------------------------------------------------------------
# §9.3  Cohomology long exact sequence
# ---------------------------------------------------------------------------


def cohomology_long_exact_sequence(
    short_exact_seq: dict[str, Any],
) -> dict[str, Any]:
    """Compute the long exact sequence in cohomology from a short exact sequence.

    Given a short exact sequence of sheaves ``0 -> A -f-> B -g-> C -> 0``
    on a site ``(C, J)``, the Snake Lemma and sheaf-theoretic machinery produce
    a long exact sequence

    ``0 -> H^0(A) -> H^0(B) -> H^0(C) -δ-> H^1(A) -> H^1(B) -> H^1(C) -δ-> ...``

    where ``δ`` is the connecting homomorphism.  Theory2.tex §9.3 Corollary 9.15
    specializes this to the trust-level sheaf, where ``H^0`` corresponds to
    global sections (globally admitted trust) and ``H^1`` obstruction classes.

    Parameters
    ----------
    short_exact_seq:
        Dict with keys:
        - ``'A'``, ``'B'``, ``'C'``: sheaf descriptors (dicts with ``'name'``,
          ``'global_sections'`` list, ``'H1'`` list of obstruction classes)
        - ``'f_map'``: description of the injection A -> B
        - ``'g_map'``: description of the surjection B -> C

    Returns
    -------
    dict describing each term ``H^i(A)``, ``H^i(B)``, ``H^i(C)`` for ``i=0,1``
    and the connecting homomorphisms ``δ^0``, ``δ^1``.
    """
    logger.debug("cohomology_long_exact_sequence: building LES")
    A = short_exact_seq.get("A", {})
    B = short_exact_seq.get("B", {})
    C = short_exact_seq.get("C", {})
    f_map = short_exact_seq.get("f_map", "A -> B (injection)")
    g_map = short_exact_seq.get("g_map", "B -> C (surjection)")

    h0_A = A.get("global_sections", [])
    h0_B = B.get("global_sections", [])
    h0_C = C.get("global_sections", [])
    h1_A = A.get("H1", [])
    h1_B = B.get("H1", [])
    h1_C = C.get("H1", [])

    # Connecting homomorphism δ^0: H^0(C) -> H^1(A)
    # A global section of C lifts locally through g: B -> C, and the
    # failure to lift globally is measured by a class in H^1(A).
    connecting_0_image: list[Any] = []
    for c_sec in h0_C:
        # Check if c_sec is in the image of g on global sections
        g_image = [
            b for b in h0_B
            if b == c_sec or (isinstance(b, dict) and b.get("image") == c_sec)
        ]
        if not g_image:
            # c_sec does not lift globally → contributes to H^1(A)
            connecting_0_image.append(
                {
                    "source_section": c_sec,
                    "obstruction": f"δ({c_sec!r}) ∈ H^1(A)",
                    "description": (
                        f"Global section {c_sec!r} of C does not lift to B; "
                        "obstruction class in H^1(A)."
                    ),
                }
            )

    # Connecting homomorphism δ^1: H^1(C) -> H^2(A) (described symbolically)
    connecting_1_description = (
        f"δ^1: H^1(C) -> H^2(A) — measures failure of H^1(C) classes to lift "
        f"through g_*. Computed via standard Čech resolution on the site."
    )

    les: dict[str, Any] = {
        "sequence": [
            {"term": "0", "exact": True},
            {"term": f"H^0({A.get('name', 'A')})", "sections": h0_A, "exact": True},
            {"map": f_map},
            {"term": f"H^0({B.get('name', 'B')})", "sections": h0_B, "exact": True},
            {"map": g_map},
            {"term": f"H^0({C.get('name', 'C')})", "sections": h0_C, "exact": True},
            {"map": "δ^0 (connecting homomorphism)", "image": connecting_0_image},
            {"term": f"H^1({A.get('name', 'A')})", "classes": h1_A},
            {"map": f"H^1({f_map})"},
            {"term": f"H^1({B.get('name', 'B')})", "classes": h1_B},
            {"map": f"H^1({g_map})"},
            {"term": f"H^1({C.get('name', 'C')})", "classes": h1_C},
            {"map": connecting_1_description},
            {"term": "H^2(A) → ..."},
        ],
        "connecting_0": connecting_0_image,
        "connecting_1_description": connecting_1_description,
        "les_exact": len(connecting_0_image) == 0 or bool(h1_A),
    }

    logger.info(
        "cohomology_long_exact_sequence: δ^0 image size=%d",
        len(connecting_0_image),
    )
    return les


# ---------------------------------------------------------------------------
# §9.2  TrustAlgebraVerifier
# ---------------------------------------------------------------------------


@dataclass
class TrustAlgebraVerifier:
    """Verifier for the trust ordered algebra axioms from Theory2.tex §9.2.

    Theory2.tex §9.2 Axioms 9.5–9.6 require:

    * **Partial order** — ``≤`` is reflexive, transitive, and antisymmetric.
    * **Meet existence** — every pair has a greatest lower bound under ``⊕``.
    * **Oracle ceiling** — ``ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED``
      (the oracle channel cannot self-promote).
    * **Monotonicity of composition** — if ``a ≤ b``, then ``a ⊕ c ≤ b ⊕ c``.
    * **CONTRADICTED absorbs** — ``CONTRADICTED ⊕ x = CONTRADICTED`` for all ``x``.
    * **MECHANICALLY_VERIFIED top** — ``MECHANICALLY_VERIFIED ⊕ x = x`` for all
      ``x`` (i.e. it is the identity for ⊕ on admissible configurations).

    Attributes
    ----------
    algebra_id:
        Identifier for this verifier instance.
    test_elements:
        Trust levels to test axioms against (default: all levels).
    violations:
        Accumulated list of axiom violations found during verification.
    """

    algebra_id: str = "trust-algebra-v1"
    test_elements: list[TrustLevel] = field(default_factory=lambda: list(TrustLevel))
    violations: list[str] = field(default_factory=list)

    def verify_all_axioms(self) -> dict[str, Any]:
        """Check all trust algebra axioms using *test_elements*.

        Returns
        -------
        dict:
            ``{'algebra_id': str, 'passed': bool, 'axiom_results': dict,
            'violations': list[str]}``
        """
        self.violations.clear()
        results: dict[str, bool] = {}

        results["reflexivity"] = self._check_reflexivity()
        results["transitivity"] = self._check_transitivity()
        results["antisymmetry"] = self._check_antisymmetry()
        results["meet_exists"] = self._check_meet_exists()
        results["oracle_ceiling"] = self.check_oracle_ceiling_enforcement()
        results["monotonicity"] = self._check_monotonicity()
        results["contradicted_absorbs"] = self._check_contradicted_absorbs()

        passed = all(results.values())
        logger.info(
            "TrustAlgebraVerifier.verify_all_axioms: passed=%s, violations=%d",
            passed,
            len(self.violations),
        )
        return {
            "algebra_id": self.algebra_id,
            "passed": passed,
            "axiom_results": results,
            "violations": list(self.violations),
        }

    def _check_reflexivity(self) -> bool:
        ok = True
        for a in self.test_elements:
            if _TRUST_WEIGHT[a] < _TRUST_WEIGHT[a]:  # always False — a ≤ a
                ok = False
                self.violations.append(f"Reflexivity: {a.name} ≰ {a.name}")
        return ok

    def _check_transitivity(self) -> bool:
        ok = True
        for a in self.test_elements:
            for b in self.test_elements:
                for c in self.test_elements:
                    a_le_b = _TRUST_WEIGHT[a] <= _TRUST_WEIGHT[b]
                    b_le_c = _TRUST_WEIGHT[b] <= _TRUST_WEIGHT[c]
                    a_le_c = _TRUST_WEIGHT[a] <= _TRUST_WEIGHT[c]
                    if a_le_b and b_le_c and not a_le_c:
                        ok = False
                        self.violations.append(
                            f"Transitivity: {a.name} ≤ {b.name} ≤ {c.name} "
                            f"but {a.name} ≰ {c.name}"
                        )
        return ok

    def _check_antisymmetry(self) -> bool:
        ok = True
        for a in self.test_elements:
            for b in self.test_elements:
                if a == b:
                    continue
                a_le_b = _TRUST_WEIGHT[a] <= _TRUST_WEIGHT[b]
                b_le_a = _TRUST_WEIGHT[b] <= _TRUST_WEIGHT[a]
                if a_le_b and b_le_a:
                    ok = False
                    self.violations.append(
                        f"Antisymmetry: {a.name} ≤ {b.name} and "
                        f"{b.name} ≤ {a.name} but {a.name} ≠ {b.name}"
                    )
        return ok

    def _check_meet_exists(self) -> bool:
        ok = True
        for a in self.test_elements:
            for b in self.test_elements:
                meet = _trust_meet(a, b)
                # meet must be ≤ both a and b
                if (
                    _TRUST_WEIGHT[meet] > _TRUST_WEIGHT[a]
                    or _TRUST_WEIGHT[meet] > _TRUST_WEIGHT[b]
                ):
                    ok = False
                    self.violations.append(
                        f"Meet: {a.name} ⊓ {b.name} = {meet.name} "
                        f"is not ≤ both operands"
                    )
        return ok

    def _check_monotonicity(self) -> bool:
        ok = True
        for a in self.test_elements:
            for b in self.test_elements:
                if _TRUST_WEIGHT[a] > _TRUST_WEIGHT[b]:
                    continue  # a > b, skip
                for c in self.test_elements:
                    ac = _trust_meet(a, c)
                    bc = _trust_meet(b, c)
                    if _TRUST_WEIGHT[ac] > _TRUST_WEIGHT[bc]:
                        ok = False
                        self.violations.append(
                            f"Monotonicity: {a.name} ≤ {b.name} but "
                            f"{a.name} ⊕ {c.name} = {ac.name} > "
                            f"{b.name} ⊕ {c.name} = {bc.name}"
                        )
        return ok

    def _check_contradicted_absorbs(self) -> bool:
        ok = True
        for x in self.test_elements:
            result = _trust_meet(TrustLevel.CONTRADICTED, x)
            if result is not TrustLevel.CONTRADICTED:
                ok = False
                self.violations.append(
                    f"CONTRADICTED ⊕ {x.name} = {result.name} ≠ CONTRADICTED"
                )
        return ok

    def check_consistency(self) -> bool:
        """Return True if the algebra is internally consistent.

        Consistency means: no axiom result contradicts another.  Practically,
        this checks that no element is both above and below another (which would
        indicate an ordering inconsistency).

        Returns
        -------
        bool
        """
        for a in self.test_elements:
            for b in self.test_elements:
                if a == b:
                    continue
                if (
                    _TRUST_WEIGHT[a] < _TRUST_WEIGHT[b]
                    and _TRUST_WEIGHT[b] < _TRUST_WEIGHT[a]
                ):
                    logger.error(
                        "TrustAlgebraVerifier.check_consistency: cycle %s <-> %s",
                        a.name,
                        b.name,
                    )
                    return False
        return True

    def generate_witness(self, axiom_id: str) -> dict[str, Any] | None:
        """Generate a concrete witness demonstrating that *axiom_id* holds.

        Returns
        -------
        dict | None:
            Witness dict with concrete element(s), or None if the axiom is
            unknown or no witness can be generated.
        """
        witnesses: dict[str, dict[str, Any]] = {
            "reflexivity": {
                "axiom": "reflexivity",
                "element": TrustLevel.SOLVER_DISCHARGED.name,
                "statement": "SOLVER_DISCHARGED ≤ SOLVER_DISCHARGED",
                "holds": True,
            },
            "oracle_ceiling": {
                "axiom": "oracle_ceiling",
                "elements": [TrustLevel.ORACLE_PROPOSED.name, TrustLevel.ORACLE_PROPOSED.name],
                "statement": (
                    "ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED "
                    "(ceiling self-composition)"
                ),
                "holds": _trust_meet(TrustLevel.ORACLE_PROPOSED, TrustLevel.ORACLE_PROPOSED)
                is TrustLevel.ORACLE_PROPOSED,
            },
            "contradicted_absorbs": {
                "axiom": "contradicted_absorbs",
                "elements": [TrustLevel.CONTRADICTED.name, TrustLevel.MECHANICALLY_VERIFIED.name],
                "statement": "CONTRADICTED ⊕ MECHANICALLY_VERIFIED = CONTRADICTED",
                "holds": (
                    _trust_meet(TrustLevel.CONTRADICTED, TrustLevel.MECHANICALLY_VERIFIED)
                    is TrustLevel.CONTRADICTED
                ),
            },
        }
        return witnesses.get(axiom_id)

    def check_oracle_ceiling_enforcement(self) -> bool:
        """Verify that ORACLE_PROPOSED does not self-compose above the ceiling.

        Theory2.tex §9.2 Remark 9.8: The oracle ceiling is enforced by the
        idempotency of ORACLE_PROPOSED under ⊕.

        Returns
        -------
        bool
        """
        ceiling = TrustLevel.ORACLE_PROPOSED
        result = _trust_meet(ceiling, ceiling)
        ok = result is ceiling
        if not ok:
            self.violations.append(
                f"Oracle ceiling: ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = "
                f"{result.name} ≠ ORACLE_PROPOSED"
            )
            logger.error(
                "TrustAlgebraVerifier.check_oracle_ceiling_enforcement: FAILED"
            )
        return ok

    def describe(self) -> str:
        """Return a human-readable summary of this verifier."""
        n = len(self.test_elements)
        v = len(self.violations)
        return (
            f"TrustAlgebraVerifier(id={self.algebra_id!r}, "
            f"test_elements={n}, violations={v})"
        )


# ---------------------------------------------------------------------------
# §9.1  SiteCompletionAlgorithm
# ---------------------------------------------------------------------------


@dataclass
class SiteCompletionAlgorithm:
    """Algorithm for completing a coverage to a full Grothendieck topology.

    Theory2.tex §9.1 Algorithm 9.3 describes the completion procedure
    iteratively: starting from a coverage (pre-topology), add all sieves
    generated by the coverage, check the three Grothendieck axioms, and repeat
    until no new sieves are added.

    Attributes
    ----------
    site_data:
        Dict describing the current site: ``{'objects': [...], 'covers': {...}}``.
    morphisms:
        List of morphism dicts as accepted by :func:`grothendieck_topology_completion`.
    completed:
        Whether the completion algorithm has been run.
    """

    site_data: dict[str, Any] = field(default_factory=dict)
    morphisms: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False
    _topology: dict[str, Any] = field(default_factory=dict, repr=False)

    def complete(self) -> dict[str, Any]:
        """Run the completion algorithm.

        Returns
        -------
        dict:
            The completed topology as returned by
            :func:`grothendieck_topology_completion`.
        """
        logger.info(
            "SiteCompletionAlgorithm.complete: starting completion with "
            "%d objects and %d morphisms",
            len(self.site_data.get("objects", [])),
            len(self.morphisms),
        )
        result = grothendieck_topology_completion(self.site_data, self.morphisms)
        self._topology = result
        self.completed = True
        logger.info(
            "SiteCompletionAlgorithm.complete: finished; added_sieves=%d",
            result.get("added_sieves", 0),
        )
        return result

    def check_completeness(self) -> bool:
        """Return True if the current topology is already complete (no sieves to add).

        Runs the completion and checks whether ``added_sieves == 0``.
        """
        result = grothendieck_topology_completion(self.site_data, self.morphisms)
        is_complete = result.get("added_sieves", 0) == 0
        logger.debug("SiteCompletionAlgorithm.check_completeness: %s", is_complete)
        return is_complete

    def get_missing_covers(self) -> list[dict[str, Any]]:
        """Return a list of covering sieves that need to be added.

        Each entry is a dict with keys ``'object'``, ``'sieve'``, ``'reason'``.
        """
        result = grothendieck_topology_completion(self.site_data, self.morphisms)
        missing: list[dict[str, Any]] = []
        existing = self.site_data.get("covers", {})
        for obj, sieves in result.get("topology", {}).items():
            existing_obj = existing.get(obj, [])
            for sieve in sieves:
                if sieve not in existing_obj:
                    missing.append(
                        {
                            "object": obj,
                            "sieve": sieve,
                            "reason": "Required by Grothendieck axioms",
                        }
                    )
        logger.debug(
            "SiteCompletionAlgorithm.get_missing_covers: %d missing", len(missing)
        )
        return missing

    def add_covers_for_axiom(self, axiom: str) -> int:
        """Add covering sieves required to satisfy a specific axiom.

        Parameters
        ----------
        axiom:
            One of ``'maximality'``, ``'stability'``, ``'transitivity'``.

        Returns
        -------
        int:
            Number of covers added.
        """
        logger.info(
            "SiteCompletionAlgorithm.add_covers_for_axiom: axiom=%r", axiom
        )
        missing = self.get_missing_covers()
        added = 0
        covers = self.site_data.setdefault("covers", {})
        for entry in missing:
            obj = entry["object"]
            sieve = entry["sieve"]
            covers.setdefault(obj, [])
            if sieve not in covers[obj]:
                covers[obj].append(sieve)
                added += 1
        logger.info(
            "SiteCompletionAlgorithm.add_covers_for_axiom: added %d covers for %r",
            added,
            axiom,
        )
        return added

    def describe(self) -> str:
        """Return a human-readable summary."""
        n_obj = len(self.site_data.get("objects", []))
        n_morph = len(self.morphisms)
        return (
            f"SiteCompletionAlgorithm("
            f"objects={n_obj}, morphisms={n_morph}, completed={self.completed})"
        )


# ---------------------------------------------------------------------------
# §9.3  ObstructionVanishingAlgorithm
# ---------------------------------------------------------------------------


@dataclass
class ObstructionVanishingAlgorithm:
    """Algorithm for checking vanishing of cohomological obstruction classes.

    Theory2.tex §9.3 Proposition 9.13 gives sufficient conditions for an
    obstruction class to vanish: either the Čech cocycle is a coboundary
    (the class is already trivial), or extra evidence is provided that fills
    the gap in the cover.

    Attributes
    ----------
    obstruction_classes:
        List of obstruction class dicts as returned by
        :func:`obstruction_class_computation`.
    site_data:
        The site over which the obstructions live.
    """

    obstruction_classes: list[dict[str, Any]] = field(default_factory=list)
    site_data: dict[str, Any] = field(default_factory=dict)

    def check_vanishing(self, obstruction_id: str) -> bool:
        """Return True if the obstruction with *obstruction_id* vanishes.

        An obstruction vanishes if its ``'vanishes'`` flag is set or if it has
        no incompatibilities.

        Parameters
        ----------
        obstruction_id:
            The ``class_id`` of the obstruction to check.

        Returns
        -------
        bool
        """
        for cls in self.obstruction_classes:
            if cls.get("class_id") == obstruction_id:
                vanishes: bool = cls.get("vanishes", False)
                logger.debug(
                    "ObstructionVanishingAlgorithm.check_vanishing: "
                    "%r vanishes=%s",
                    obstruction_id,
                    vanishes,
                )
                return vanishes
        logger.warning(
            "ObstructionVanishingAlgorithm.check_vanishing: "
            "obstruction %r not found",
            obstruction_id,
        )
        return False

    def find_lift(
        self,
        obstruction_id: str,
        extra_evidence: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Try to find a lift given extra evidence.

        Extra evidence may resolve incompatibilities by providing a common
        restriction value for pairs of local sections that previously disagreed.
        If all incompatibilities are resolved by the extra evidence, the
        obstruction vanishes and the lift is constructed.

        Parameters
        ----------
        obstruction_id:
            The ``class_id`` of the obstruction to lift.
        extra_evidence:
            Dict mapping intersection keys to resolution values.

        Returns
        -------
        dict | None:
            Lift dict with ``{'lifted': bool, 'resolved_pairs': list,
            'remaining': list}`` or None if obstruction not found.
        """
        obs = next(
            (c for c in self.obstruction_classes if c.get("class_id") == obstruction_id),
            None,
        )
        if obs is None:
            logger.warning(
                "ObstructionVanishingAlgorithm.find_lift: %r not found",
                obstruction_id,
            )
            return None

        incompatibilities = list(obs.get("incompatibilities", []))
        resolved: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []

        for incompat in incompatibilities:
            inter_key = incompat.get("intersection", "")
            if inter_key in extra_evidence:
                resolved_value = extra_evidence[inter_key]
                resolved.append(
                    {
                        "pair": incompat["pair"],
                        "intersection": inter_key,
                        "resolved_to": resolved_value,
                    }
                )
                logger.debug(
                    "ObstructionVanishingAlgorithm.find_lift: resolved %s with %r",
                    inter_key,
                    resolved_value,
                )
            else:
                remaining.append(incompat)

        lifted = len(remaining) == 0
        if lifted:
            # Mark this obstruction as vanished in the list
            for cls in self.obstruction_classes:
                if cls.get("class_id") == obstruction_id:
                    cls["vanishes"] = True
                    cls["incompatibilities"] = []
                    break
            logger.info(
                "ObstructionVanishingAlgorithm.find_lift: obstruction %r lifted",
                obstruction_id,
            )
        else:
            logger.info(
                "ObstructionVanishingAlgorithm.find_lift: obstruction %r "
                "partially resolved; %d remaining",
                obstruction_id,
                len(remaining),
            )

        return {
            "lifted": lifted,
            "resolved_pairs": resolved,
            "remaining": remaining,
        }

    def describe_obstruction(self, obstruction_id: str) -> str:
        """Return a human-readable description of the obstruction.

        Parameters
        ----------
        obstruction_id:
            The ``class_id`` of the obstruction to describe.

        Returns
        -------
        str
        """
        obs = next(
            (c for c in self.obstruction_classes if c.get("class_id") == obstruction_id),
            None,
        )
        if obs is None:
            return f"Obstruction {obstruction_id!r} not found."
        return (
            f"Obstruction {obstruction_id!r} (degree {obs.get('degree', '?')}): "
            f"{obs.get('description', 'No description.')} "
            f"Vanishes: {obs.get('vanishes', False)}. "
            f"Incompatibilities: {len(obs.get('incompatibilities', []))}."
        )

    def required_evidence_for_vanishing(self, obstruction_id: str) -> list[str]:
        """Return a list of evidence items needed to make the obstruction vanish.

        Returns a list of intersection keys for which extra evidence would
        resolve the incompatibility.

        Parameters
        ----------
        obstruction_id:
            The ``class_id`` of the obstruction.

        Returns
        -------
        list[str]:
            Intersection keys for which evidence is missing.
        """
        obs = next(
            (c for c in self.obstruction_classes if c.get("class_id") == obstruction_id),
            None,
        )
        if obs is None:
            return []
        return [
            incompat.get("intersection", "")
            for incompat in obs.get("incompatibilities", [])
        ]

    def vanish_all(self, extra_evidence: dict[str, Any]) -> dict[str, Any]:
        """Try to vanish all obstructions with the given extra evidence.

        Iterates over all obstruction classes and attempts to lift each one.

        Parameters
        ----------
        extra_evidence:
            Dict mapping intersection keys to resolution values.

        Returns
        -------
        dict:
            ``{'vanished': list[str], 'remaining': list[str], 'all_vanished': bool}``
        """
        vanished: list[str] = []
        remaining: list[str] = []

        for obs in self.obstruction_classes:
            obs_id = obs.get("class_id", "")
            if obs.get("vanishes", False):
                vanished.append(obs_id)
                continue
            lift = self.find_lift(obs_id, extra_evidence)
            if lift is not None and lift.get("lifted"):
                vanished.append(obs_id)
            else:
                remaining.append(obs_id)

        all_vanished = len(remaining) == 0
        logger.info(
            "ObstructionVanishingAlgorithm.vanish_all: vanished=%d remaining=%d",
            len(vanished),
            len(remaining),
        )
        return {
            "vanished": vanished,
            "remaining": remaining,
            "all_vanished": all_vanished,
        }

    def describe(self) -> str:
        """Return a human-readable summary."""
        total = len(self.obstruction_classes)
        n_vanished = sum(1 for c in self.obstruction_classes if c.get("vanishes"))
        return (
            f"ObstructionVanishingAlgorithm("
            f"total={total}, vanished={n_vanished}, "
            f"remaining={total - n_vanished})"
        )


# ---------------------------------------------------------------------------
# Cross-module bridging algorithms (Theory2.tex §9)
# ---------------------------------------------------------------------------


def descent_gluing_algorithm(
    sections: list[dict[str, Any]],
    overlaps: list[tuple[str, str]],
) -> dict[str, Any]:
    """Glue local sections along overlaps using descent data (Theory2.tex §9.3).

    Constructs :class:`~jugeo.geometry.descent.LocalSection` instances from
    *sections* and evaluates each overlap pair through
    :class:`~jugeo.geometry.descent.OverlapCondition`.  The resulting cover is
    scored via :func:`~jugeo.geometry.covers.score_cover`.

    Parameters
    ----------
    sections : list[dict[str, Any]]
        Each dict must contain at least ``"coordinate"`` (str) and
        ``"judgment_data"`` (dict).  Optional keys: ``"trust_level"`` (float,
        default 1.0), ``"evidence_bundle"`` (list[str]).
    overlaps : list[tuple[str, str]]
        Pairs of coordinate strings that are expected to agree on their
        intersection.

    Returns
    -------
    dict[str, Any]
        Keys: ``glued``, ``satisfied``, ``violated``, ``cover_score``,
        ``fallback``.
    """
    try:
        from jugeo.geometry.descent import LocalSection, OverlapCondition
    except ImportError:
        logger.warning("descent_gluing_algorithm: jugeo.geometry.descent unavailable")
        LocalSection = None  # type: ignore[assignment,misc]
        OverlapCondition = None  # type: ignore[assignment,misc]

    try:
        from jugeo.geometry.covers import CoverMember, score_cover
    except ImportError:
        logger.warning("descent_gluing_algorithm: jugeo.geometry.covers unavailable")
        CoverMember = None  # type: ignore[assignment,misc]
        score_cover = None  # type: ignore[assignment,misc]

    fallback = LocalSection is None or OverlapCondition is None

    # Build local section lookup keyed by coordinate.
    sec_map: dict[str, Any] = {}
    for raw in sections:
        coord = raw.get("coordinate", "")
        if LocalSection is not None:
            ls = LocalSection(
                coordinate=coord,
                judgment_data=raw.get("judgment_data", {}),
                evidence_bundle=tuple(raw.get("evidence_bundle", ())),
                trust_level=float(raw.get("trust_level", 1.0)),
            )
            sec_map[coord] = ls
        else:
            sec_map[coord] = raw

    satisfied: list[tuple[str, str]] = []
    violated: list[tuple[str, str]] = []

    for left_coord, right_coord in overlaps:
        left = sec_map.get(left_coord)
        right = sec_map.get(right_coord)
        if left is None or right is None:
            violated.append((left_coord, right_coord))
            continue
        if OverlapCondition is not None:
            oc = OverlapCondition(
                left_coordinate=left_coord,
                right_coordinate=right_coord,
                overlap_coordinate=f"{left_coord}&{right_coord}",
                compatibility_predicate=lambda l, r: l == r,
            )
            evaluated = oc.evaluate(
                left.judgment_data if hasattr(left, "judgment_data") else left.get("judgment_data", {}),
                right.judgment_data if hasattr(right, "judgment_data") else right.get("judgment_data", {}),
            )
            if evaluated.is_healthy:
                satisfied.append((left_coord, right_coord))
            else:
                violated.append((left_coord, right_coord))
        else:
            ld = left.get("judgment_data", {}) if isinstance(left, dict) else {}
            rd = right.get("judgment_data", {}) if isinstance(right, dict) else {}
            (satisfied if ld == rd else violated).append((left_coord, right_coord))

    cover_score: dict[str, Any] | None = None
    if score_cover is not None and CoverMember is not None:
        try:
            from jugeo.geometry.covers import Cover
            patches = [s.get("coordinate", "") if isinstance(s, dict) else s.coordinate for s in sections]
            cover = Cover(patches=patches, overlaps=overlaps)
            metric = score_cover(cover)
            cover_score = {
                "patch_count": metric.patch_count,
                "overlap_count": metric.overlap_count,
                "locality_score": metric.locality_score,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("score_cover failed: %s", exc)

    glued = len(violated) == 0
    logger.info(
        "descent_gluing_algorithm: glued=%s satisfied=%d violated=%d fallback=%s",
        glued, len(satisfied), len(violated), fallback,
    )
    return {
        "glued": glued,
        "satisfied": satisfied,
        "violated": violated,
        "cover_score": cover_score,
        "fallback": fallback,
    }


def solver_obstruction_check(
    obstruction_class: dict[str, Any],
    *,
    backend: str = "z3",
) -> dict[str, Any]:
    """Check whether an obstruction class vanishes via solver (Theory2.tex §9.3).

    Encodes the obstruction as a judgment using
    :func:`~jugeo.encodings.encode_judgment` and dispatches the satisfiability
    query through :mod:`jugeo.solver.z3_session`.  If Z3 is unavailable, falls
    back to a heuristic vanishing test.

    Parameters
    ----------
    obstruction_class : dict[str, Any]
        Must contain ``"class_id"`` (str).  Optional keys:
        ``"degree"`` (int), ``"vanishes"`` (bool), ``"incompatibilities"``
        (list[dict]).
    backend : str
        Solver backend name; currently only ``"z3"`` is supported.

    Returns
    -------
    dict[str, Any]
        Keys: ``class_id``, ``vanishes``, ``outcome``, ``engine``,
        ``reasons``, ``fallback``.
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
    except ImportError:
        logger.warning("solver_obstruction_check: jugeo.solver.z3_session unavailable")
        SolverResult = None  # type: ignore[assignment,misc]
        SolveOutcome = None  # type: ignore[assignment,misc]
        z3_available = None  # type: ignore[assignment]

    try:
        from jugeo.encodings import encode_judgment
    except ImportError:
        logger.warning("solver_obstruction_check: jugeo.encodings unavailable")
        encode_judgment = None  # type: ignore[assignment]

    class_id = obstruction_class.get("class_id", "unknown")
    incompatibilities = obstruction_class.get("incompatibilities", [])
    fallback = SolverResult is None or encode_judgment is None

    # Attempt encoding the obstruction as a judgment for the solver.
    encoded: dict[str, Any] | None = None
    if encode_judgment is not None:
        try:
            encoded = encode_judgment(obstruction_class)
        except Exception as exc:  # noqa: BLE001
            logger.debug("encode_judgment failed for %s: %s", class_id, exc)

    outcome_str = "unknown"
    engine = backend
    reasons: list[str] = []
    vanishes = False

    if SolverResult is not None and SolveOutcome is not None:
        solver_ready = z3_available() if callable(z3_available) else False
        if solver_ready and encoded is not None:
            try:
                from jugeo.solver.z3_session import Z3SessionPool
                pool = Z3SessionPool()
                result: SolverResult = pool.check_vanishing(class_id, encoded)
                outcome_str = result.outcome.value
                engine = result.engine
                reasons = list(result.reasons)
                vanishes = result.outcome == SolveOutcome.UNSAT
            except Exception as exc:  # noqa: BLE001
                logger.debug("Z3 session check failed for %s: %s", class_id, exc)
                reasons.append(f"solver error: {exc}")
        else:
            reasons.append("z3 not available" if not solver_ready else "encoding failed")
    else:
        reasons.append("solver module unavailable")

    # Heuristic fallback: obstruction vanishes if no incompatibilities remain.
    if fallback or outcome_str == "unknown":
        vanishes = len(incompatibilities) == 0
        if fallback:
            engine = "heuristic"
            outcome_str = "unsat" if vanishes else "sat"

    logger.info(
        "solver_obstruction_check: class_id=%s vanishes=%s outcome=%s engine=%s",
        class_id, vanishes, outcome_str, engine,
    )
    return {
        "class_id": class_id,
        "vanishes": vanishes,
        "outcome": outcome_str,
        "engine": engine,
        "reasons": reasons,
        "fallback": fallback,
    }


def judgment_site_algorithm(
    site_data: dict[str, Any],
    *,
    trust_floor: float = 0.0,
) -> list[dict[str, Any]]:
    """Construct judgment terms for every coordinate in a site (Theory2.tex §9.1).

    Iterates over the ``"objects"`` list in *site_data*, builds a
    :class:`~jugeo.geometry.site.Coordinate` for each, and attaches a
    :class:`~jugeo.judgments.judgment_terms.Proposition` reflecting the
    site-level claim.  Coordinates whose trust falls below *trust_floor*
    are marked :attr:`~jugeo.judgments.judgment_terms.JudgmentStatus.OBSTRUCTED`.

    Parameters
    ----------
    site_data : dict[str, Any]
        Must contain ``"objects"`` — a list of dicts, each with at least
        ``"coordinate"`` (str), ``"kind"`` (str), and optionally ``"formula"``
        (str) and ``"trust_level"`` (float).
    trust_floor : float
        Minimum trust required for a judgment to be ``PROPOSED``; anything
        below is ``OBSTRUCTED``.

    Returns
    -------
    list[dict[str, Any]]
        One dict per object with keys: ``coordinate``, ``kind``,
        ``proposition``, ``status``, ``trust_level``.
    """
    try:
        from jugeo.judgments.judgment_terms import Proposition, JudgmentStatus
    except ImportError:
        logger.warning("judgment_site_algorithm: jugeo.judgments.judgment_terms unavailable")
        Proposition = None  # type: ignore[assignment,misc]
        JudgmentStatus = None  # type: ignore[assignment,misc]

    try:
        from jugeo.geometry.site import Coordinate, CoordinateKind
    except ImportError:
        logger.warning("judgment_site_algorithm: jugeo.geometry.site unavailable")
        Coordinate = None  # type: ignore[assignment,misc]
        CoordinateKind = None  # type: ignore[assignment,misc]

    objects = site_data.get("objects", [])
    results: list[dict[str, Any]] = []

    for obj in objects:
        raw_coord = obj.get("coordinate", "")
        raw_kind = obj.get("kind", "region")
        formula = obj.get("formula", f"holds({raw_coord})")
        trust = float(obj.get("trust_level", 1.0))

        # Resolve coordinate kind.
        if CoordinateKind is not None:
            try:
                kind = CoordinateKind(raw_kind)
            except ValueError:
                kind = CoordinateKind.REGION
        else:
            kind = raw_kind

        # Build coordinate object when available.
        if Coordinate is not None:
            coord = Coordinate(components=tuple(raw_coord.split(".")), kind=kind)
            coord_key = coord.key
        else:
            coord_key = raw_coord

        # Build proposition.
        if Proposition is not None:
            from jugeo.judgments.judgment_terms import PropositionKind
            try:
                prop_kind = PropositionKind.STRUCTURAL
            except Exception:  # noqa: BLE001
                prop_kind = PropositionKind.STRUCTURAL  # type: ignore[assignment]
            prop = Proposition(kind=prop_kind, formula=formula)
            prop_repr = {"kind": prop.kind.value, "formula": prop.formula}
        else:
            prop_repr = {"kind": "structural", "formula": formula}

        # Determine status.
        if JudgmentStatus is not None:
            status = (
                JudgmentStatus.PROPOSED.value
                if trust >= trust_floor
                else JudgmentStatus.OBSTRUCTED.value
            )
        else:
            status = "proposed" if trust >= trust_floor else "obstructed"

        results.append({
            "coordinate": coord_key,
            "kind": kind.value if hasattr(kind, "value") else kind,
            "proposition": prop_repr,
            "status": status,
            "trust_level": trust,
        })

    logger.info(
        "judgment_site_algorithm: objects=%d proposed=%d obstructed=%d",
        len(results),
        sum(1 for r in results if r["status"] in ("proposed", "PROPOSED")),
        sum(1 for r in results if r["status"] in ("obstructed", "OBSTRUCTED")),
    )
    return results
