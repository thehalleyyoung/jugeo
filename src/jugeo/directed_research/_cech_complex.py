"""Cech complex for cross-domain overlap computation (§9.4)."""

from __future__ import annotations

from jugeo.directed_research._types import DomainSite, SubDomain


def compute_overlap_count(
    locus: list[SubDomain],
    partner_sub_domains: int,
) -> int:
    """Compute the number of overlap terms in the localized Cech complex."""
    return len(locus) * partner_sub_domains
