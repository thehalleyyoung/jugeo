r"""pack_federation — encoding of pack federation as sheaves, morphisms, and descent protocols.

Theory (theory2.tex §35):
    Pack federation is the process of combining multiple local semantic packs
    into a globally consistent semantic structure.  This sub-package encodes
    the mathematical machinery underlying federation:

    - Each pack is modelled as an open set in a topological space;
      the federation is the sheaf F that assigns local sections to each pack
      and restriction maps across bridge theorems (§35.1).
    - Bridge theorems are functors between pack vocabularies, carrying trust
      ceilings and morphism types (§35.2).
    - The federation protocol operationalises descent across pack boundaries,
      threading evidence through the bridge sequence while enforcing trust
      monotonicity (Lemma 35.7) and kind preservation (§35.3).
    - Core algorithms implement sheaf condition checking, shortest-path bridge
      finding, and quality scoring (§35.4).
    - The integration layer wires together encoding, sheaf, and engine (§35.5).
    - Eight theorem-verification functions discharge the key claims of §35 (§35.6).

copilot: pack-federation-encoding-init
"""
from __future__ import annotations

from .models import (
    PackFederationEncoding,
    BridgeTheoremEncoding,
    FederationProtocol,
    PackBoundary,
)
from .pack_federation_as_sheaf import PackFederationAsSheaf
from .bridge_theorems_as_morphisms import BridgeTheoremAsMorphism
from .federation_protocol import FederationProtocolEngine
from .algorithms import (
    compute_sheaf_condition,
    find_minimal_bridge_path,
    compute_federation_trust_ceiling,
    validate_overlap_laws,
    assemble_federation_result,
)
from .integration import PackFederationEncodingIntegration
from .theorems import (
    verify_sheaf_condition_soundness,
    verify_bridge_morphism_laws,
    verify_federation_kind_preservation,
    verify_trust_ceiling_monotonicity,
    verify_overlap_law_consistency,
    verify_descent_completeness,
    verify_pack_boundary_coherence,
    verify_federation_protocol_correctness,
)

__all__ = [
    "PackFederationEncoding",
    "BridgeTheoremEncoding",
    "FederationProtocol",
    "PackBoundary",
    "PackFederationAsSheaf",
    "BridgeTheoremAsMorphism",
    "FederationProtocolEngine",
    "compute_sheaf_condition",
    "find_minimal_bridge_path",
    "compute_federation_trust_ceiling",
    "validate_overlap_laws",
    "assemble_federation_result",
    "PackFederationEncodingIntegration",
    "verify_sheaf_condition_soundness",
    "verify_bridge_morphism_laws",
    "verify_federation_kind_preservation",
    "verify_trust_ceiling_monotonicity",
    "verify_overlap_law_consistency",
    "verify_descent_completeness",
    "verify_pack_boundary_coherence",
    "verify_federation_protocol_correctness",
]


# --- auto-registered submodules ---
try:
    from . import manifest
except Exception:
    pass
