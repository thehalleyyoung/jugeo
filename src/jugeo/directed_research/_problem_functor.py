"""Problem functor — how problems reshape the search space (§9.11, Theorem 9.3)."""

from __future__ import annotations

from jugeo.directed_research._types import DomainSite, SubDomain


def localize_search_space(
    domain: DomainSite,
    problem_locus: list[SubDomain],
) -> int:
    """Compute the dimension of the localized search space.

    Phi_p(D_2) = H^1(Loc(p) × D_2, S) ∩ {sigma_p != 0}

    The problem functor is contravariant in p (more specific problem →
    smaller search space) and covariant in D_2 (richer partner → larger).
    """
    return len(problem_locus) * len(domain.sub_domains)
