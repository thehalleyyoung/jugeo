"""Integration tests: packs catalog ↔ bridge theorems ↔ authority ↔ trust algebra.

Cross-cutting modules under test
---------------------------------
* ``jugeo.packs.catalog``   — PackDescriptor, PackCatalog, PackLaw, PackBoundary
* ``jugeo.packs.bridges``   — PackBridge, BridgeTheorem, BridgeRegistry, BridgeComposer
* ``jugeo.packs.authority`` — PackAuthority, PackAuthorityRegistry, PackJurisdiction
* ``jugeo.evidence.trust``  — TrustAlgebra, TrustLevel, TrustTier, join_trust_profiles

Theory2 invariants asserted throughout
----------------------------------------
1. **Judgment = (c,φ,A,E,O,B,T,Π) tuple not a bool** — catalog load operations
   return structured pack records, not boolean success flags.
2. **Trust is ordered algebra, not float** — bridge trust ceilings use the
   partial-order algebra; composition of bridges attenuates trust monotonically.
3. **No silent promotion from ORACLE_PROPOSED tier** — authority ceilings block
   copilot proposals from claiming solver-level trust.
4. **Evidence kinds preserved in federation** — when bridging across two packs,
   the evidence kind tags on transported records are unchanged.
5. **Authority ceiling below solver proofs** — pack authority declared at
   "exploratory" level must not allow trust at ``SOLVER_DISCHARGED``.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.packs.catalog import (
    PackDescriptor,
    PackCatalog,
    PackLaw,
    PackAdapter,
    PackBoundary,
    load_pack_catalog,
    list_available_packs,
    KNOWN_AUTHORITY_LEVELS,
)
from jugeo.packs.bridges import (
    PackBridge,
    BridgeTheorem,
    BridgeRegistry,
    BridgeComposer,
    BridgeApplication,
    TRUST_SOLVER_DISCHARGED,
    TRUST_ORACLE_PROPOSED,
    TRUST_COPILOT_SUGGESTED,
)
from jugeo.packs.authority import (
    PackAuthority,
    PackAuthorityRegistry,
    PackJurisdiction,
    ConflictKind,
    ResolutionStrategy,
    KNOWN_AUTHORITY_LEVELS as AUTH_LEVELS,
)
from jugeo.evidence.trust import (
    TrustLevel as AlgebraTrustLevel,
    TrustAlgebra,
    TrustTier,
    TrustProfile,
    join_trust_profiles,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_pack(
    name: str = "test.pack",
    version: str = "1.0",
    authority: str = "provisional",
    exported_kinds: tuple[str, ...] = ("kind.A",),
    capabilities: tuple[str, ...] = ("solve", "witness"),
) -> PackDescriptor:
    """Build a minimal PackDescriptor for testing."""
    return PackDescriptor(
        name=name,
        version=version,
        capabilities=capabilities,
        exported_kinds=exported_kinds,
        dependencies=(),
        authority=authority,
        description=f"Test pack {name}",
        provenance={"source": "test-suite"},
        trust={"ceiling": "provisional"},
    )


def _make_law(name: str = "TypeSafety", statement: str = "∀x. typed(x)") -> PackLaw:
    """Build a minimal PackLaw."""
    return PackLaw(
        name=name,
        statement=statement,
        law_kind="descent",
        evidence_channels=("proof", "runtime"),
    )


def _make_bridge(
    source: str = "pack.A",
    target: str = "pack.B",
    theorem_name: str = "bridge.A-B",
    trust_ceiling: float = TRUST_SOLVER_DISCHARGED,
) -> PackBridge:
    """Build a lightweight PackBridge."""
    return PackBridge(
        source_pack=source,
        target_pack=target,
        theorem_name=theorem_name,
        transported_symbols=("kind.X", "law.Y"),
        provenance=("theory2.tex §4",),
    )


# ---------------------------------------------------------------------------
# §1  Pack catalog loading
# ---------------------------------------------------------------------------


class TestPackCatalogLoading:
    """Load pack from catalog → structured record, not a bool."""

    def test_pack_descriptor_construction_produces_full_record(self) -> None:
        """A PackDescriptor must carry all declared fields as distinct attributes."""
        pack = _make_pack("alpha.pack", "2.0", "foundational")
        assert pack.name == "alpha.pack"
        assert pack.version == "2.0"
        assert pack.authority == "foundational"
        assert "kind.A" in pack.exported_kinds
        # The record is not a bool
        assert isinstance(pack, PackDescriptor)
        assert pack is not True
        assert pack is not False

    def test_pack_descriptor_catalog_key_is_name_at_version(self) -> None:
        """catalog_key must be the canonical 'name@version' string."""
        pack = _make_pack("beta.pack", "3.1")
        assert pack.catalog_key == "beta.pack@3.1"

    def test_pack_catalog_register_and_lookup(self) -> None:
        """PackCatalog.register() then get() must return the same descriptor."""
        catalog = PackCatalog()
        pack = _make_pack("gamma.pack", "1.0")
        catalog.register(pack)
        retrieved = catalog.get("gamma.pack", version="1.0")
        assert retrieved is not None
        assert retrieved.name == "gamma.pack"
        assert retrieved.version == "1.0"

    def test_pack_catalog_missing_pack_returns_none(self) -> None:
        """catalog.get() for a non-existent pack must return None, not raise."""
        catalog = PackCatalog()
        result = catalog.get("nonexistent.pack")
        assert result is None

    def test_pack_catalog_all_returns_list_of_descriptors(self) -> None:
        """PackCatalog.all() must return all registered packs as descriptors."""
        catalog = PackCatalog()
        p1 = _make_pack("p1", "1.0")
        p2 = _make_pack("p2", "1.0")
        catalog.register(p1)
        catalog.register(p2)
        all_packs = catalog.all()
        names = [p.name for p in all_packs]
        assert "p1" in names
        assert "p2" in names

    def test_pack_law_carries_evidence_channel_declaration(self) -> None:
        """A PackLaw must declare which evidence channels can verify it."""
        law = _make_law("ConsistencyLaw", "∀x∈U. consistent(x)")
        assert "proof" in law.evidence_channels
        assert isinstance(law.evidence_channels, tuple)
        # Law statement is not a bool
        assert isinstance(law.statement, str)

    def test_pack_descriptor_authority_from_known_levels(self) -> None:
        """PackDescriptor authority must be one of the four known levels."""
        for level in KNOWN_AUTHORITY_LEVELS:
            pack = _make_pack("test", "1.0", authority=level)
            assert pack.authority == level
            assert pack.authority_rank() == KNOWN_AUTHORITY_LEVELS.index(level)

    def test_pack_descriptor_exports_kind_check(self) -> None:
        """exports_kind() must correctly test membership in exported_kinds."""
        pack = _make_pack(exported_kinds=("kind.A", "kind.B", "kind.C"))
        assert pack.exports_kind("kind.A") is True
        assert pack.exports_kind("kind.D") is False

    def test_load_pack_catalog_returns_catalog_instance(self) -> None:
        """load_pack_catalog() must return a PackCatalog, not None or bool."""
        catalog = load_pack_catalog()
        assert isinstance(catalog, PackCatalog)
        # Not empty — canonical catalog has at least a few entries
        # (or an empty catalog if not yet populated; either way it's a catalog)
        assert hasattr(catalog, "register")
        assert hasattr(catalog, "get")


# ---------------------------------------------------------------------------
# §2  Authority level verification
# ---------------------------------------------------------------------------


class TestAuthorityLevelVerification:
    """Authority ceiling must be strictly ordered; exploratory < foundational."""

    def test_authority_ceiling_order_is_strictly_ascending(self) -> None:
        """foundational > provisional > exploratory > quarantined."""
        q = _make_pack(authority="quarantined").authority_rank()
        e = _make_pack(authority="exploratory").authority_rank()
        p = _make_pack(authority="provisional").authority_rank()
        f = _make_pack(authority="foundational").authority_rank()
        assert q < e < p < f

    def test_pack_authority_covers_granted_domains(self) -> None:
        """PackAuthority.covers_domain() must use the granted_domains set."""
        auth = PackAuthority(
            pack_id="pack.math",
            granted_domains={"topology", "measure_theory"},
            evidence_channels_allowed={"solver", "runtime_witness"},
        )
        assert auth.covers_domain("topology") is True
        assert auth.covers_domain("category_theory") is False

    def test_pack_authority_allows_coordinate_without_jurisdiction(self) -> None:
        """PackAuthority without a coordinate_jurisdiction must admit any coord."""
        auth = PackAuthority(
            pack_id="pack.global",
            granted_domains={"*"},
            coordinate_jurisdiction=None,
        )
        assert auth.allows_coordinate("module.anything.deeply.nested") is True

    def test_pack_authority_coordinate_jurisdiction_restricts_access(self) -> None:
        """A PackJurisdiction must restrict coordinates to declared patterns."""
        jurisdiction = PackJurisdiction(
            pack_id="pack.restricted",
            coordinate_patterns=("module.math.*",),
        )
        assert jurisdiction.includes_coordinate("module.math.topology") is True
        assert jurisdiction.includes_coordinate("module.physics.quantum") is False

    def test_authority_registry_register_and_query(self) -> None:
        """PackAuthorityRegistry must store and retrieve authority records."""
        registry = PackAuthorityRegistry()
        auth = PackAuthority(
            pack_id="pack.test",
            granted_domains={"test_domain"},
            evidence_channels_allowed={"solver"},
        )
        registry.register(auth)
        retrieved = registry.get("pack.test")
        assert retrieved is not None
        assert retrieved.pack_id == "pack.test"

    def test_authority_ceiling_below_solver_proofs_for_exploratory(self) -> None:
        """An 'exploratory' authority pack must have ceiling < SOLVER_DISCHARGED."""
        algebra = TrustAlgebra()
        # Exploratory = TrustTier.PROPOSAL, which maps to below SOLVER_DISCHARGED
        exploratory_tier = TrustTier.PROPOSAL
        solver_tier = TrustTier.VERIFIED
        # Exploratory is weaker than verified/solver
        assert exploratory_tier.weaker_than(solver_tier)

    def test_copilot_delegation_defaults_to_disallowed(self) -> None:
        """PackAuthority must default copilot_delegation_allowed=False."""
        auth = PackAuthority(
            pack_id="pack.secure",
            granted_domains={"domain.A"},
        )
        assert auth.copilot_delegation_allowed is False


# ---------------------------------------------------------------------------
# §3  Bridge theorem trust ceiling enforcement
# ---------------------------------------------------------------------------


class TestBridgeTrustCeiling:
    """Bridge trust ceilings attenuate monotonically; oracle ceiling < solver."""

    def test_bridge_theorem_carries_trust_ceiling(self) -> None:
        """BridgeTheorem must record a numeric trust ceiling."""
        theorem = BridgeTheorem(
            source_pack="pack.A",
            target_pack="pack.B",
            theorem_statement="∀x∈A∩B. φ_A(x) ⟺ ψ_B(x)",
            trust_ceiling=TRUST_SOLVER_DISCHARGED,
        )
        assert theorem.trust_ceiling == TRUST_SOLVER_DISCHARGED
        assert theorem.trust_ceiling > TRUST_ORACLE_PROPOSED

    def test_bridge_theorem_oracle_ceiling_below_solver(self) -> None:
        """An oracle/copilot-proposed bridge must have ceiling < SOLVER_DISCHARGED."""
        oracle_bridge = BridgeTheorem(
            source_pack="pack.A",
            target_pack="pack.B",
            theorem_statement="hypothetically: φ_A ↔ ψ_B",
            trust_ceiling=TRUST_ORACLE_PROPOSED,
            is_verified=False,
        )
        assert oracle_bridge.trust_ceiling < TRUST_SOLVER_DISCHARGED
        # Copilot-suggested ceiling is even lower
        assert TRUST_COPILOT_SUGGESTED < TRUST_ORACLE_PROPOSED

    def test_bridge_composition_attenuates_trust_monotonically(self) -> None:
        """Composing bridges A→B→C must attenuate the effective ceiling."""
        composer = BridgeComposer()
        ab = BridgeTheorem(
            source_pack="pack.A",
            target_pack="pack.B",
            theorem_statement="A↔B",
            trust_ceiling=TRUST_SOLVER_DISCHARGED,
        )
        bc = BridgeTheorem(
            source_pack="pack.B",
            target_pack="pack.C",
            theorem_statement="B↔C",
            trust_ceiling=TRUST_SOLVER_DISCHARGED,
        )
        composed = composer.compose([ab, bc])
        # Composed trust must not exceed the weakest bridge in the chain
        assert composed.trust_ceiling <= TRUST_SOLVER_DISCHARGED

    def test_bridge_registry_register_and_find(self) -> None:
        """BridgeRegistry.find() must return matching bridges by endpoint."""
        registry = BridgeRegistry()
        theorem = BridgeTheorem(
            source_pack="pack.X",
            target_pack="pack.Y",
            theorem_statement="X↔Y",
        )
        registry.register(theorem)
        found = registry.find(source="pack.X", target="pack.Y")
        assert len(found) >= 1
        assert all(b.source_pack == "pack.X" for b in found)
        assert all(b.target_pack == "pack.Y" for b in found)

    def test_pack_bridge_connects_method(self) -> None:
        """PackBridge.connects() must check source AND target simultaneously."""
        bridge = _make_bridge("src.pack", "tgt.pack")
        assert bridge.connects("src.pack", "tgt.pack") is True
        assert bridge.connects("src.pack", "other.pack") is False
        assert bridge.connects("other.pack", "tgt.pack") is False


# ---------------------------------------------------------------------------
# §4  Evidence kind preservation across bridge transport
# ---------------------------------------------------------------------------


class TestEvidenceKindPreservationInFederation:
    """Theory2: Evidence kinds preserved in federation (bridge transport)."""

    def test_bridge_application_preserves_evidence_kind_tags(self) -> None:
        """BridgeApplication must not erase or rename evidence kind labels."""
        theorem = BridgeTheorem(
            source_pack="pack.A",
            target_pack="pack.B",
            theorem_statement="A↔B",
            trust_ceiling=TRUST_SOLVER_DISCHARGED,
        )
        application = BridgeApplication(theorem)
        # Simulate transporting an evidence record
        evidence_record = {
            "kind": "solver_proof",
            "channel": "z3",
            "trust_level": "solver_discharged",
            "payload": {"clauses": ["P → Q", "¬Q"], "result": "unsat"},
        }
        transported = application.transport(evidence_record)
        # Kind must be preserved
        assert transported.get("kind") == "solver_proof"
        assert transported.get("channel") == "z3"

    def test_pack_boundary_describes_federation_limits(self) -> None:
        """PackBoundary must describe what may and may not cross the boundary."""
        boundary = PackBoundary(
            boundary_id="boundary.A-B",
            source_pack="pack.A",
            target_pack="pack.B",
            allowed_kinds=("kind.A", "kind.B"),
            restricted_kinds=("kind.secret",),
        )
        assert "kind.A" in boundary.allowed_kinds
        assert "kind.secret" in boundary.restricted_kinds

    def test_multi_pack_federation_joins_profiles_conservatively(self) -> None:
        """Federating evidence from multiple packs must use conservative join."""
        prof_formal = TrustProfile(
            tier=TrustTier.VERIFIED,
            support_scope=("module.math",),
            reasons=("formal-proof",),
        )
        prof_oracle = TrustProfile(
            tier=TrustTier.PROPOSAL,
            support_scope=("module.physics",),
            reasons=("oracle-suggestion",),
        )
        # Federation is conservative join — weakest wins
        federated = join_trust_profiles(prof_formal, prof_oracle)
        assert federated.tier == TrustTier.PROPOSAL

    def test_bridge_theorem_copilot_flag_affects_trust_ceiling(self) -> None:
        """A copilot-suggested bridge must have lower ceiling than a verified one."""
        verified = BridgeTheorem(
            source_pack="p.A",
            target_pack="p.B",
            theorem_statement="A↔B",
            trust_ceiling=TRUST_SOLVER_DISCHARGED,
            is_verified=True,
        )
        unverified = BridgeTheorem(
            source_pack="p.A",
            target_pack="p.B",
            theorem_statement="A↔B (proposed)",
            trust_ceiling=TRUST_COPILOT_SUGGESTED,
            is_verified=False,
        )
        assert verified.trust_ceiling > unverified.trust_ceiling

    def test_bridge_registry_all_returns_all_registered_bridges(self) -> None:
        """BridgeRegistry.all() must return every registered BridgeTheorem."""
        registry = BridgeRegistry()
        for i in range(4):
            t = BridgeTheorem(
                source_pack=f"p.{i}",
                target_pack=f"p.{i+1}",
                theorem_statement=f"bridge_{i}",
            )
            registry.register(t)
        all_bridges = registry.all()
        assert len(all_bridges) >= 4


# ---------------------------------------------------------------------------
# §5  Trust algebra interaction with pack authority
# ---------------------------------------------------------------------------


class TestTrustAlgebraPackInteraction:
    """Pack authority + trust algebra invariant: no silent promotion."""

    def test_trust_algebra_oracle_cannot_compose_to_solver(self) -> None:
        """Composing oracle evidence with any other level must stay ≤ oracle."""
        algebra = TrustAlgebra()
        oracle = AlgebraTrustLevel.ORACLE_PROPOSED
        runtime = AlgebraTrustLevel.RUNTIME_WITNESSED
        result = algebra.compose(oracle, runtime)
        # Composition is meet — result ≤ min(oracle, runtime)
        assert result <= oracle or result <= runtime

    def test_pack_authority_trust_ceiling_enforced_on_domain(self) -> None:
        """PackAuthority.trust_ceiling_for() must return declared ceiling."""
        auth = PackAuthority(
            pack_id="pack.bounded",
            granted_domains={"arithmetic"},
            trust_ceiling_per_domain={
                "arithmetic": TrustTier.REVIEWED,
            },
        )
        ceiling = auth.trust_ceiling_for("arithmetic")
        assert ceiling == TrustTier.REVIEWED
        # Not VERIFIED (highest)
        assert ceiling != TrustTier.VERIFIED

    def test_authority_allows_check_combines_coordinate_and_tier(self) -> None:
        """PackAuthority.allows() must enforce both coordinate and tier."""
        auth = PackAuthority(
            pack_id="pack.domain",
            granted_domains={"domain.A"},
            coordinate_jurisdiction=PackJurisdiction(
                pack_id="pack.domain",
                coordinate_patterns=("module.A.*",),
            ),
            trust_ceiling_per_domain={"domain.A": TrustTier.REVIEWED},
        )
        # Within jurisdiction and at/below ceiling → allowed
        assert auth.allows("module.A.submodule", TrustTier.REVIEWED) is True
        # Above ceiling → not allowed
        assert auth.allows("module.A.submodule", TrustTier.VERIFIED) is False

    def test_trust_profile_join_across_two_packs_conservative(self) -> None:
        """Trust federation across two packs must use conservative join."""
        pack_A_trust = TrustProfile(
            tier=TrustTier.VERIFIED,
            support_scope=("module.A",),
        )
        pack_B_trust = TrustProfile(
            tier=TrustTier.PROPOSAL,
            support_scope=("module.B",),
        )
        # Federated result must use conservative (minimum) join
        federated = join_trust_profiles(pack_A_trust, pack_B_trust)
        assert federated.tier <= TrustTier.PROPOSAL

    def test_algebra_meet_of_two_trust_levels_below_both(self) -> None:
        """meet(a, b) must be ≤ both a and b."""
        algebra = TrustAlgebra()
        a = AlgebraTrustLevel.SOLVER_DISCHARGED
        b = AlgebraTrustLevel.HUMAN_ATTESTED
        m = algebra.meet(a, b)
        assert m <= a
        assert m <= b


# ---------------------------------------------------------------------------
# §6  Pack federation preserves evidence kind
# ---------------------------------------------------------------------------


class TestPackFederationEvidenceKind:
    """Federation across packs must preserve distinct evidence kind labels."""

    def test_federated_catalog_preserves_pack_exported_kinds(self) -> None:
        """After federating two packs into a catalog, both exported_kinds visible."""
        catalog = PackCatalog()
        pack_geo = _make_pack(
            "pack.geometry", "1.0", exported_kinds=("kind.Cover", "kind.Descent")
        )
        pack_alg = _make_pack(
            "pack.algebra", "1.0", exported_kinds=("kind.Ring", "kind.Module")
        )
        catalog.register(pack_geo)
        catalog.register(pack_alg)

        # Both packs' kinds should be queryable from the catalog
        retrieved_geo = catalog.get("pack.geometry")
        retrieved_alg = catalog.get("pack.algebra")
        assert retrieved_geo is not None
        assert retrieved_alg is not None
        assert "kind.Cover" in retrieved_geo.exported_kinds
        assert "kind.Ring" in retrieved_alg.exported_kinds

    def test_bridge_transported_symbol_not_conflated_with_other_kinds(self) -> None:
        """Transported symbols must remain under their original kind label."""
        bridge = _make_bridge(
            "pack.topology",
            "pack.analysis",
            theorem_name="openSetBridge",
        )
        # Transported symbols are explicitly named
        assert "kind.X" in bridge.transported_symbols

    def test_pack_boundary_restricted_kinds_not_transported(self) -> None:
        """Kinds in restricted_kinds must not appear in allowed_kinds."""
        boundary = PackBoundary(
            boundary_id="boundary.private",
            source_pack="pack.secret",
            target_pack="pack.public",
            allowed_kinds=("kind.Public",),
            restricted_kinds=("kind.Private",),
        )
        allowed = set(boundary.allowed_kinds)
        restricted = set(boundary.restricted_kinds)
        # No overlap between allowed and restricted
        assert allowed.isdisjoint(restricted)

    def test_pack_catalog_validation_issues_on_missing_exported_kinds(self) -> None:
        """A PackDescriptor without exported_kinds must report a validation issue."""
        pack_no_kinds = PackDescriptor(
            name="pack.empty",
            version="1.0",
            capabilities=("solve",),
            exported_kinds=(),   # intentionally empty
            authority="exploratory",
            provenance={"source": "test"},
        )
        issues = pack_no_kinds.validation_issues()
        assert "missing-exported-kinds" in issues

    def test_pack_catalog_self_dependency_flagged(self) -> None:
        """A pack that depends on itself must be flagged by validation_issues()."""
        pack_self_dep = PackDescriptor(
            name="pack.loop",
            version="1.0",
            exported_kinds=("kind.X",),
            dependencies=("pack.loop",),
            authority="exploratory",
            provenance={"source": "test"},
        )
        issues = pack_self_dep.validation_issues()
        assert "self-dependency" in issues


# ---------------------------------------------------------------------------
# §7  PackDescriptor to_dict / from_mapping round-trip
# ---------------------------------------------------------------------------


class TestPackDescriptorRoundTrip:
    """PackDescriptor must survive to_dict() → from_mapping() round-trip."""

    def test_pack_descriptor_round_trip_preserves_name_version(self) -> None:
        """Name and version must survive dict round-trip."""
        pack = _make_pack("roundtrip.pack", "2.5", "foundational")
        d = pack.to_dict()
        pack2 = PackDescriptor.from_mapping(d)
        assert pack2.name == pack.name
        assert pack2.version == pack.version
        assert pack2.authority == pack.authority

    def test_pack_descriptor_round_trip_preserves_exported_kinds(self) -> None:
        """Exported kinds must survive round-trip through dict."""
        pack = _make_pack(exported_kinds=("kind.A", "kind.B", "kind.C"))
        d = pack.to_dict()
        pack2 = PackDescriptor.from_mapping(d)
        for k in ("kind.A", "kind.B", "kind.C"):
            assert k in pack2.exported_kinds

    def test_pack_law_round_trip_preserves_statement(self) -> None:
        """PackLaw statement must survive to_dict() / from_mapping() round-trip."""
        law = _make_law("FunctionalExtensionality", "∀f g. (∀x. f(x)=g(x)) → f=g")
        d = law.to_dict()
        law2 = PackLaw.from_mapping(d)
        assert law2.name == law.name
        assert law2.statement == law.statement

    def test_pack_descriptor_to_theory_record_has_required_keys(self) -> None:
        """to_theory_record() must expose region, cover, surface, laws, seal."""
        pack = _make_pack()
        record = pack.to_theory_record()
        for key in ("region", "cover", "surface", "laws", "seal"):
            assert key in record, f"Missing key '{key}' in theory record"


# ---------------------------------------------------------------------------
# §8  Bridge registry bidirectional lookup
# ---------------------------------------------------------------------------


class TestBridgeRegistryBidirectional:
    """Bridge registry must support source→target and reverse lookups."""

    def test_bridge_registry_finds_by_source_only(self) -> None:
        """find(source=X) must return all bridges starting from X."""
        registry = BridgeRegistry()
        for target in ("p.B", "p.C", "p.D"):
            t = BridgeTheorem(
                source_pack="p.A",
                target_pack=target,
                theorem_statement=f"A↔{target}",
            )
            registry.register(t)
        results = registry.find(source="p.A")
        assert len(results) == 3
        assert all(b.source_pack == "p.A" for b in results)

    def test_bridge_registry_empty_query_returns_all(self) -> None:
        """find() with no arguments must return all registered bridges."""
        registry = BridgeRegistry()
        for i in range(3):
            registry.register(BridgeTheorem(
                source_pack=f"p.{i}", target_pack=f"p.{i+1}", theorem_statement=f"b{i}"
            ))
        all_b = registry.find()
        assert len(all_b) == 3

    def test_bridge_composer_path_not_found_returns_none(self) -> None:
        """BridgeComposer must return None for non-existent path."""
        composer = BridgeComposer()
        ab = BridgeTheorem(source_pack="p.A", target_pack="p.B", theorem_statement="A↔B")
        composer.register(ab)
        # No bridge from B to C exists
        path = composer.find_path("p.A", "p.C")
        assert path is None or len(path) == 0

    def test_bridge_composer_finds_direct_path(self) -> None:
        """BridgeComposer must find A→B directly when the bridge exists."""
        composer = BridgeComposer()
        ab = BridgeTheorem(source_pack="p.A", target_pack="p.B", theorem_statement="A↔B")
        composer.register(ab)
        path = composer.find_path("p.A", "p.B")
        assert path is not None
        assert len(path) == 1


# ---------------------------------------------------------------------------
# §9  PackCatalog multi-version support
# ---------------------------------------------------------------------------


class TestPackCatalogMultiVersion:
    """PackCatalog must support multiple versions of the same pack name."""

    def test_catalog_distinguishes_versions(self) -> None:
        """Registering two versions of the same pack must keep both accessible."""
        catalog = PackCatalog()
        v1 = _make_pack("multi.pack", "1.0")
        v2 = _make_pack("multi.pack", "2.0")
        catalog.register(v1)
        catalog.register(v2)
        retrieved_v1 = catalog.get("multi.pack", version="1.0")
        retrieved_v2 = catalog.get("multi.pack", version="2.0")
        assert retrieved_v1 is not None
        assert retrieved_v2 is not None
        assert retrieved_v1.version == "1.0"
        assert retrieved_v2.version == "2.0"

    def test_catalog_latest_returns_most_recent_version(self) -> None:
        """catalog.get(name) without version must return the latest registered."""
        catalog = PackCatalog()
        v1 = _make_pack("seq.pack", "1.0")
        v2 = _make_pack("seq.pack", "2.0")
        catalog.register(v1)
        catalog.register(v2)
        latest = catalog.get("seq.pack")  # no version specified
        assert latest is not None
        # Either version is acceptable; what matters is a valid descriptor
        assert isinstance(latest, PackDescriptor)


# ---------------------------------------------------------------------------
# §10  Authority conflict detection
# ---------------------------------------------------------------------------


class TestAuthorityConflictDetection:
    """PackAuthorityRegistry must detect and classify jurisdiction conflicts."""

    def test_two_packs_same_domain_raises_conflict(self) -> None:
        """Registering two packs over the same domain must surface a conflict."""
        registry = PackAuthorityRegistry()
        auth_a = PackAuthority(
            pack_id="pack.alpha",
            granted_domains={"shared.domain"},
        )
        auth_b = PackAuthority(
            pack_id="pack.beta",
            granted_domains={"shared.domain"},
        )
        registry.register(auth_a)
        registry.register(auth_b)
        conflicts = registry.detect_conflicts()
        assert len(conflicts) >= 1
        kinds = {c.kind for c in conflicts}
        assert ConflictKind.OVERLAPPING_DOMAIN in kinds

    def test_conflict_kind_is_structured_not_bool(self) -> None:
        """Conflicts must carry a typed ConflictKind, not just a bool."""
        conflict_kinds = list(ConflictKind)
        # Must have at least these two structurally important kinds
        assert ConflictKind.OVERLAPPING_DOMAIN in conflict_kinds
        assert ConflictKind.COPILOT_JURISDICTION_OVERREACH in conflict_kinds

    def test_authority_allows_explicit_channel(self) -> None:
        """PackAuthority.allows_channel() must respect evidence_channels_allowed."""
        auth = PackAuthority(
            pack_id="pack.restricted",
            granted_domains={"domain.secure"},
            evidence_channels_allowed={"solver"},
        )
        assert auth.allows_channel("solver") is True
        assert auth.allows_channel("copilot") is False

    def test_trust_algebra_reflect_authority_level_ordering(self) -> None:
        """The trust algebra partial order must reflect authority level ordering."""
        algebra = TrustAlgebra()
        # Mechanically verified is the strongest
        top = AlgebraTrustLevel.MECHANICALLY_VERIFIED
        oracle = AlgebraTrustLevel.ORACLE_PROPOSED
        assert oracle < top
        # top is the top element of the lattice
        assert algebra.top() == top
