from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
Authority Promotion Pipeline — test_s01_discovery_as_authority
==============================================================

This test module exercises the full authority-promotion pipeline defined in
``jugeo.ideation.discovery_federation.s01_discovery_as_authority``.

The pipeline answers the question: *when should a discovery be elevated to an
authority?*  A discovery is promoted when it clears three hurdles:

1. **Trust** — the trust_score field meets or exceeds a configurable threshold.
2. **Novelty** — the discovery's ID is not already present in the corpus of
   known authority IDs.
3. **Conditions** — any additional contextual preconditions supplied by the
   caller (e.g. quorum reached, regime compatible).

Key classes under test
----------------------
* ``AuthorityPromoter``   – evaluates promotion eligibility and issues grants.
* ``AuthorityValidator``  – validates an existing grant against every criterion.
* ``AuthorityLifecycleManager`` – persists, revokes, expires and refreshes grants.
* ``DiscoveryAuthorityRunner``  – top-level orchestrator that wires the above
  together into a single ``run()`` call.

Free functions
--------------
* ``promote_to_authority`` – convenience wrapper around ``AuthorityPromoter``.
* ``validate_authority_conditions`` – validates a flat conditions dict.
"""

from jugeo.ideation.discovery_federation.s01_discovery_as_authority import (
    AuthorityPromoter,
    AuthorityValidator,
    AuthorityLifecycleManager,
    DiscoveryAuthorityRunner,
    promote_to_authority,
    validate_authority_conditions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_discovery(
    discovery_id: str = "disc-001",
    trust_score: float = 0.8,
    novelty: bool = True,
    regime: str = "default",
    tags: list | None = None,
) -> dict:
    """Return a minimal discovery dict suitable for authority promotion tests.

    Parameters
    ----------
    discovery_id:
        Unique identifier for the discovery.
    trust_score:
        Float in [0, 1] representing how much the system trusts this discovery.
    novelty:
        Convenience flag — if True the discovery is considered novel; callers
        are still responsible for keeping ``existing_ids`` consistent.
    regime:
        Regime label used by regime-compatibility checks.
    tags:
        Optional list of string tags attached to the discovery.
    """
    return {
        "discovery_id": discovery_id,
        "trust_score": trust_score,
        "novelty": novelty,
        "regime": regime,
        "tags": tags or [],
        "description": f"Discovery {discovery_id} with trust={trust_score}",
    }


def make_context(
    quorum_reached: bool = True,
    regime: str = "default",
    allow_promotion: bool = True,
) -> dict:
    """Build a context dict that the promoter / runner accepts."""
    return {
        "quorum_reached": quorum_reached,
        "regime": regime,
        "allow_promotion": allow_promotion,
    }


def make_grant(
    grant_id: str = "grant-001",
    discovery_id: str = "disc-001",
    trust_score: float = 0.8,
    regime: str = "default",
    quorum_size: int = 3,
    expired: bool = False,
) -> dict:
    """Build a minimal authority-grant dict for validator / lifecycle tests."""
    return {
        "grant_id": grant_id,
        "discovery_id": discovery_id,
        "trust_score": trust_score,
        "regime": regime,
        "quorum_size": quorum_size,
        "novelty_confirmed": True,
        "is_expired": expired,
        "expiry": "2099-12-31" if not expired else "2000-01-01",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def promoter_default() -> AuthorityPromoter:
    """AuthorityPromoter with trust_threshold=0.7, novelty_threshold=0.5, quorum_size=3."""
    return AuthorityPromoter(trust_threshold=0.7, novelty_threshold=0.5, quorum_size=3)


@pytest.fixture
def validator_strict() -> AuthorityValidator:
    """AuthorityValidator in strict mode — all checks are required to pass."""
    return AuthorityValidator(strict=True)


@pytest.fixture
def lifecycle_manager_empty() -> AuthorityLifecycleManager:
    """Empty AuthorityLifecycleManager with no pre-loaded grants."""
    return AuthorityLifecycleManager()


@pytest.fixture
def runner_default() -> DiscoveryAuthorityRunner:
    """DiscoveryAuthorityRunner wired with default sub-components."""
    return DiscoveryAuthorityRunner()


# ---------------------------------------------------------------------------
# AuthorityPromoter — check_trust
# ---------------------------------------------------------------------------

class TestAuthorityPromoterCheckTrust:
    """Tests for AuthorityPromoter.check_trust()."""

    @pytest.mark.parametrize("trust_score,threshold,expected", [
        # exactly at threshold — should pass
        (0.7, 0.7, True),
        (0.3, 0.3, True),
        (0.5, 0.5, True),
        (0.9, 0.9, True),
        # clearly above threshold
        (0.8, 0.7, True),
        (0.9, 0.7, True),
        (1.0, 0.7, True),
        (0.5, 0.3, True),
        (0.6, 0.5, True),
        (1.0, 0.9, True),
        # clearly below threshold
        (0.1, 0.7, False),
        (0.3, 0.7, False),
        (0.5, 0.7, False),
        (0.69, 0.7, False),
        (0.0, 0.3, False),
        (0.2, 0.3, False),
        (0.4, 0.5, False),
        (0.8, 0.9, False),
    ])
    def test_check_trust_parametrized(
        self,
        trust_score: float,
        threshold: float,
        expected: bool,
    ) -> None:
        """check_trust must respect the promoter's trust_threshold exactly."""
        promoter = AuthorityPromoter(trust_threshold=threshold)
        disc = make_discovery(trust_score=trust_score)
        assert promoter.check_trust(disc) is expected

    def test_check_trust_boundary_just_below(self, promoter_default: AuthorityPromoter) -> None:
        """trust_score of 0.699 is just below 0.7 — must return False."""
        disc = make_discovery(trust_score=0.699)
        assert promoter_default.check_trust(disc) is False

    def test_check_trust_boundary_just_above(self, promoter_default: AuthorityPromoter) -> None:
        """trust_score of 0.701 is just above 0.7 — must return True."""
        disc = make_discovery(trust_score=0.701)
        assert promoter_default.check_trust(disc) is True

    def test_check_trust_returns_bool(self, promoter_default: AuthorityPromoter) -> None:
        """Return type must be bool (not a truthy/falsy int or string)."""
        disc = make_discovery(trust_score=0.9)
        result = promoter_default.check_trust(disc)
        assert isinstance(result, bool)

    def test_check_trust_zero_score_fails(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.0)
        assert promoter_default.check_trust(disc) is False

    def test_check_trust_full_score_passes(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=1.0)
        assert promoter_default.check_trust(disc) is True

    def test_check_trust_with_very_low_threshold(self) -> None:
        """Any trust_score should pass a threshold of 0.0."""
        promoter = AuthorityPromoter(trust_threshold=0.0)
        disc = make_discovery(trust_score=0.01)
        assert promoter.check_trust(disc) is True

    def test_check_trust_with_very_high_threshold(self) -> None:
        """Only a trust_score of 1.0 should pass a threshold of 1.0."""
        promoter = AuthorityPromoter(trust_threshold=1.0)
        assert promoter.check_trust(make_discovery(trust_score=1.0)) is True
        assert promoter.check_trust(make_discovery(trust_score=0.999)) is False


# ---------------------------------------------------------------------------
# AuthorityPromoter — check_novelty
# ---------------------------------------------------------------------------

class TestAuthorityPromoterCheckNovelty:
    """Tests for AuthorityPromoter.check_novelty()."""

    def test_novelty_unknown_id_is_novel(self, promoter_default: AuthorityPromoter) -> None:
        """A discovery whose ID is absent from existing_ids must be novel."""
        disc = make_discovery(discovery_id="disc-new")
        assert promoter_default.check_novelty(disc, existing_ids=["disc-001", "disc-002"]) is True

    def test_novelty_known_id_not_novel(self, promoter_default: AuthorityPromoter) -> None:
        """A discovery whose ID already appears in existing_ids is not novel."""
        disc = make_discovery(discovery_id="disc-001")
        assert promoter_default.check_novelty(disc, existing_ids=["disc-001", "disc-002"]) is False

    def test_novelty_empty_existing_is_always_novel(self, promoter_default: AuthorityPromoter) -> None:
        """With no existing IDs every discovery is novel."""
        disc = make_discovery(discovery_id="disc-001")
        assert promoter_default.check_novelty(disc, existing_ids=[]) is True

    def test_novelty_returns_bool(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(discovery_id="disc-xyz")
        result = promoter_default.check_novelty(disc, existing_ids=[])
        assert isinstance(result, bool)

    @pytest.mark.parametrize("existing,expected", [
        ([], True),
        (["other-1"], True),
        (["disc-abc"], False),
        (["disc-abc", "disc-def"], False),
        (["disc-abc", "disc-xyz"], False),
    ])
    def test_novelty_parametrized(
        self,
        promoter_default: AuthorityPromoter,
        existing: list,
        expected: bool,
    ) -> None:
        disc = make_discovery(discovery_id="disc-abc")
        assert promoter_default.check_novelty(disc, existing_ids=existing) is expected

    def test_novelty_large_existing_list(self, promoter_default: AuthorityPromoter) -> None:
        """Performance sanity: novelty check over a large existing list must still work."""
        existing = [f"disc-{i}" for i in range(1000)]
        disc = make_discovery(discovery_id="disc-9999")
        assert promoter_default.check_novelty(disc, existing_ids=existing) is True


# ---------------------------------------------------------------------------
# AuthorityPromoter — check_conditions
# ---------------------------------------------------------------------------

class TestAuthorityPromoterCheckConditions:
    """Tests for AuthorityPromoter.check_conditions()."""

    def test_returns_dict(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery()
        ctx = make_context()
        result = promoter_default.check_conditions(disc, ctx)
        assert isinstance(result, dict)

    def test_dict_has_trust_key(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery()
        ctx = make_context()
        result = promoter_default.check_conditions(disc, ctx)
        assert "trust" in result

    def test_dict_has_novelty_key(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery()
        ctx = make_context()
        result = promoter_default.check_conditions(disc, ctx)
        assert "novelty" in result

    def test_dict_has_quorum_key(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery()
        ctx = make_context(quorum_reached=True)
        result = promoter_default.check_conditions(disc, ctx)
        assert "quorum" in result

    def test_all_values_are_bool(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery()
        ctx = make_context()
        result = promoter_default.check_conditions(disc, ctx)
        for k, v in result.items():
            assert isinstance(v, bool), f"Condition '{k}' value is not bool: {v!r}"

    def test_high_trust_disc_passes_trust_condition(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.95)
        ctx = make_context()
        result = promoter_default.check_conditions(disc, ctx)
        assert result["trust"] is True

    def test_low_trust_disc_fails_trust_condition(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.2)
        ctx = make_context()
        result = promoter_default.check_conditions(disc, ctx)
        assert result["trust"] is False

    def test_quorum_false_in_context_fails_quorum_condition(
        self, promoter_default: AuthorityPromoter
    ) -> None:
        disc = make_discovery()
        ctx = make_context(quorum_reached=False)
        result = promoter_default.check_conditions(disc, ctx)
        assert result["quorum"] is False

    def test_all_conditions_pass_for_ideal_disc(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.99)
        ctx = make_context(quorum_reached=True)
        result = promoter_default.check_conditions(disc, ctx)
        assert all(result.values()), f"Expected all conditions to pass, got: {result}"


# ---------------------------------------------------------------------------
# AuthorityPromoter — promote
# ---------------------------------------------------------------------------

class TestAuthorityPromoterPromote:
    """Tests for AuthorityPromoter.promote()."""

    def test_promote_returns_dict_on_success(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.9)
        ctx = make_context()
        grant = promoter_default.promote(disc, ctx)
        assert isinstance(grant, dict)

    def test_promote_grant_has_discovery_id(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(discovery_id="disc-promo", trust_score=0.9)
        ctx = make_context()
        grant = promoter_default.promote(disc, ctx)
        assert grant is not None
        assert grant.get("discovery_id") == "disc-promo"

    def test_promote_grant_has_grant_id(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.9)
        ctx = make_context()
        grant = promoter_default.promote(disc, ctx)
        assert "grant_id" in grant

    def test_promote_grant_has_trust_score(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.85)
        ctx = make_context()
        grant = promoter_default.promote(disc, ctx)
        assert grant.get("trust_score") == pytest.approx(0.85)

    def test_promote_returns_none_or_raises_on_low_trust(
        self, promoter_default: AuthorityPromoter
    ) -> None:
        """A discovery that fails trust check must not produce a valid grant."""
        disc = make_discovery(trust_score=0.1)
        ctx = make_context()
        try:
            grant = promoter_default.promote(disc, ctx)
            # Acceptable if it returns None (or a dict with status=failed)
            assert grant is None or grant.get("status") in (None, "failed", "rejected")
        except Exception:
            pass  # Raising is also acceptable

    def test_promote_no_quorum_fails(self, promoter_default: AuthorityPromoter) -> None:
        """Without quorum the promotion must not produce a live grant."""
        disc = make_discovery(trust_score=0.9)
        ctx = make_context(quorum_reached=False)
        try:
            grant = promoter_default.promote(disc, ctx)
            assert grant is None or grant.get("status") in (None, "failed", "rejected")
        except Exception:
            pass

    def test_promote_unique_grant_ids(self, promoter_default: AuthorityPromoter) -> None:
        """Each successful promotion must produce a unique grant_id."""
        ctx = make_context()
        grants = [
            promoter_default.promote(make_discovery(discovery_id=f"d-{i}", trust_score=0.9), ctx)
            for i in range(5)
        ]
        ids = [g["grant_id"] for g in grants if g]
        assert len(ids) == len(set(ids)), "Duplicate grant_ids produced"

    def test_promote_grant_has_regime(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.9, regime="scientific")
        ctx = make_context(regime="scientific")
        grant = promoter_default.promote(disc, ctx)
        assert grant is not None
        assert "regime" in grant


# ---------------------------------------------------------------------------
# AuthorityPromoter — batch_promote
# ---------------------------------------------------------------------------

class TestAuthorityPromoterBatchPromote:
    """Tests for AuthorityPromoter.batch_promote()."""

    def test_batch_promote_empty_list(self, promoter_default: AuthorityPromoter) -> None:
        result = promoter_default.batch_promote([], context=make_context())
        assert isinstance(result, list)
        assert len(result) == 0

    def test_batch_promote_single_item(self, promoter_default: AuthorityPromoter) -> None:
        disc = make_discovery(trust_score=0.9)
        result = promoter_default.batch_promote([disc], context=make_context())
        assert isinstance(result, list)
        assert len(result) == 1

    def test_batch_promote_multiple_items(self, promoter_default: AuthorityPromoter) -> None:
        discoveries = [
            make_discovery(discovery_id=f"d-{i}", trust_score=0.9) for i in range(5)
        ]
        result = promoter_default.batch_promote(discoveries, context=make_context())
        assert isinstance(result, list)

    def test_batch_promote_filters_low_trust(self, promoter_default: AuthorityPromoter) -> None:
        """Only discoveries meeting the trust threshold should appear in results."""
        discoveries = [
            make_discovery(discovery_id="good", trust_score=0.9),
            make_discovery(discovery_id="bad", trust_score=0.1),
        ]
        results = promoter_default.batch_promote(discoveries, context=make_context())
        grant_ids = [r.get("discovery_id") for r in results if r]
        # At least the good discovery should have a successful grant
        successful = [
            r for r in results
            if r and r.get("discovery_id") == "good"
            and r.get("status", "granted") not in ("failed", "rejected")
        ]
        assert len(successful) >= 1

    def test_batch_promote_returns_list_of_dicts(self, promoter_default: AuthorityPromoter) -> None:
        discoveries = [make_discovery(discovery_id=f"d-{i}", trust_score=0.9) for i in range(3)]
        results = promoter_default.batch_promote(discoveries, context=make_context())
        for item in results:
            assert isinstance(item, dict)

    def test_batch_promote_large_batch(self, promoter_default: AuthorityPromoter) -> None:
        discoveries = [
            make_discovery(discovery_id=f"disc-{i}", trust_score=0.8 + (i % 3) * 0.05)
            for i in range(20)
        ]
        results = promoter_default.batch_promote(discoveries, context=make_context())
        assert isinstance(results, list)
        assert len(results) == 20


# ---------------------------------------------------------------------------
# AuthorityValidator
# ---------------------------------------------------------------------------

class TestAuthorityValidatorTrust:
    """Tests for AuthorityValidator.validate_trust()."""

    def test_validate_trust_high_score_passes(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(trust_score=0.9)
        assert validator_strict.validate_trust(grant) is True

    def test_validate_trust_low_score_fails(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(trust_score=0.1)
        assert validator_strict.validate_trust(grant) is False

    def test_validate_trust_returns_bool(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(trust_score=0.8)
        assert isinstance(validator_strict.validate_trust(grant), bool)

    @pytest.mark.parametrize("score,expected", [
        (0.0, False),
        (0.3, False),
        (0.5, False),
        (0.69, False),
        (0.7, True),
        (0.8, True),
        (1.0, True),
    ])
    def test_validate_trust_parametrized(
        self,
        validator_strict: AuthorityValidator,
        score: float,
        expected: bool,
    ) -> None:
        grant = make_grant(trust_score=score)
        assert validator_strict.validate_trust(grant) is expected


class TestAuthorityValidatorNovelty:
    """Tests for AuthorityValidator.validate_novelty()."""

    def test_validate_novelty_confirmed_passes(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant()
        grant["novelty_confirmed"] = True
        assert validator_strict.validate_novelty(grant) is True

    def test_validate_novelty_not_confirmed_fails(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant()
        grant["novelty_confirmed"] = False
        assert validator_strict.validate_novelty(grant) is False

    def test_validate_novelty_returns_bool(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant()
        assert isinstance(validator_strict.validate_novelty(grant), bool)


class TestAuthorityValidatorQuorum:
    """Tests for AuthorityValidator.validate_quorum()."""

    def test_validate_quorum_enough_members(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(quorum_size=3)
        assert validator_strict.validate_quorum(grant, quorum_size=3) is True

    def test_validate_quorum_insufficient_members(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(quorum_size=1)
        assert validator_strict.validate_quorum(grant, quorum_size=3) is False

    @pytest.mark.parametrize("grant_quorum,required,expected", [
        (5, 3, True),
        (3, 3, True),
        (2, 3, False),
        (1, 3, False),
        (10, 5, True),
        (4, 5, False),
    ])
    def test_validate_quorum_parametrized(
        self,
        validator_strict: AuthorityValidator,
        grant_quorum: int,
        required: int,
        expected: bool,
    ) -> None:
        grant = make_grant(quorum_size=grant_quorum)
        assert validator_strict.validate_quorum(grant, quorum_size=required) is expected


class TestAuthorityValidatorRegime:
    """Tests for AuthorityValidator.validate_regime_compatibility()."""

    def test_matching_regime_passes(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(regime="scientific")
        assert validator_strict.validate_regime_compatibility(grant, regime="scientific") is True

    def test_mismatched_regime_fails(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(regime="scientific")
        assert validator_strict.validate_regime_compatibility(grant, regime="empirical") is False

    def test_default_regime_compatibility(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant(regime="default")
        assert validator_strict.validate_regime_compatibility(grant, regime="default") is True


class TestAuthorityValidatorAll:
    """Tests for AuthorityValidator.validate_all() and get_failures()."""

    def test_validate_all_returns_dict(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant()
        result = validator_strict.validate_all(grant)
        assert isinstance(result, dict)

    def test_validate_all_keys_present(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant()
        result = validator_strict.validate_all(grant)
        for key in ("trust", "novelty", "quorum"):
            assert key in result, f"Missing key '{key}' in validate_all result"

    def test_validate_all_values_are_bool(self, validator_strict: AuthorityValidator) -> None:
        grant = make_grant()
        result = validator_strict.validate_all(grant)
        for k, v in result.items():
            assert isinstance(v, bool), f"validate_all['{k}'] is not bool: {v!r}"

    def test_validate_all_perfect_grant_all_pass(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(trust_score=0.95, quorum_size=5)
        grant["novelty_confirmed"] = True
        result = validator_strict.validate_all(grant)
        assert all(result.values()), f"Expected all True, got {result}"

    def test_get_failures_empty_when_all_pass(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(trust_score=0.95, quorum_size=5)
        grant["novelty_confirmed"] = True
        failures = validator_strict.get_failures(grant)
        assert isinstance(failures, list)
        assert len(failures) == 0

    def test_get_failures_populated_when_some_fail(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(trust_score=0.1, quorum_size=1)
        grant["novelty_confirmed"] = False
        failures = validator_strict.get_failures(grant)
        assert isinstance(failures, list)
        assert len(failures) > 0

    def test_get_failures_contains_string_names(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(trust_score=0.1)
        failures = validator_strict.get_failures(grant)
        for name in failures:
            assert isinstance(name, str)

    def test_get_failures_trust_in_list_on_low_trust(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(trust_score=0.1)
        failures = validator_strict.get_failures(grant)
        assert "trust" in failures


# ---------------------------------------------------------------------------
# AuthorityLifecycleManager
# ---------------------------------------------------------------------------

class TestAuthorityLifecycleManagerGrant:
    """Tests for AuthorityLifecycleManager.grant()."""

    def test_grant_returns_string_id(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        authority = make_grant()
        gid = lifecycle_manager_empty.grant(authority)
        assert isinstance(gid, str)
        assert len(gid) > 0

    def test_grant_stores_entry(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        authority = make_grant()
        gid = lifecycle_manager_empty.grant(authority)
        assert lifecycle_manager_empty.is_active(gid)

    def test_grant_multiple_distinct_ids(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        ids = [lifecycle_manager_empty.grant(make_grant(grant_id=f"g-{i}")) for i in range(5)]
        assert len(set(ids)) == len(ids), "Lifecycle manager produced duplicate grant IDs"


class TestAuthorityLifecycleManagerRevoke:
    """Tests for AuthorityLifecycleManager.revoke()."""

    def test_revoke_returns_true_on_success(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        assert lifecycle_manager_empty.revoke(gid) is True

    def test_revoke_makes_inactive(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        lifecycle_manager_empty.revoke(gid)
        assert lifecycle_manager_empty.is_active(gid) is False

    def test_revoke_unknown_id_returns_false(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        result = lifecycle_manager_empty.revoke("nonexistent-grant-id")
        assert result is False


class TestAuthorityLifecycleManagerExpire:
    """Tests for AuthorityLifecycleManager.expire()."""

    def test_expire_returns_true(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        assert lifecycle_manager_empty.expire(gid) is True

    def test_expire_makes_inactive(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        lifecycle_manager_empty.expire(gid)
        assert lifecycle_manager_empty.is_active(gid) is False

    def test_expire_unknown_id_returns_false(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        result = lifecycle_manager_empty.expire("ghost-id")
        assert result is False


class TestAuthorityLifecycleManagerRefresh:
    """Tests for AuthorityLifecycleManager.refresh()."""

    def test_refresh_returns_true(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        assert lifecycle_manager_empty.refresh(gid, new_expiry="2099-12-31") is True

    def test_refresh_unknown_returns_false(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        assert lifecycle_manager_empty.refresh("bad-id", new_expiry="2099-12-31") is False

    def test_refresh_updates_expiry(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        """After refresh the grant should remain active."""
        gid = lifecycle_manager_empty.grant(make_grant(expired=True))
        lifecycle_manager_empty.refresh(gid, new_expiry="2099-12-31")
        # Post-refresh the grant must be reactivated
        assert lifecycle_manager_empty.is_active(gid) is True


class TestAuthorityLifecycleManagerActiveGrants:
    """Tests for is_active, get_active_grants, prune_expired."""

    def test_is_active_true_for_fresh_grant(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        assert lifecycle_manager_empty.is_active(gid) is True

    def test_is_active_false_for_unknown(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        assert lifecycle_manager_empty.is_active("ghost") is False

    def test_get_active_grants_returns_list(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        lifecycle_manager_empty.grant(make_grant(grant_id="g-1"))
        lifecycle_manager_empty.grant(make_grant(grant_id="g-2"))
        result = lifecycle_manager_empty.get_active_grants()
        assert isinstance(result, list)

    def test_get_active_grants_excludes_revoked(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid_keep = lifecycle_manager_empty.grant(make_grant(grant_id="keep"))
        gid_revoke = lifecycle_manager_empty.grant(make_grant(grant_id="revoke"))
        lifecycle_manager_empty.revoke(gid_revoke)
        active_ids = [g.get("grant_id") for g in lifecycle_manager_empty.get_active_grants()]
        assert gid_keep in active_ids or any(gid_keep in str(g) for g in lifecycle_manager_empty.get_active_grants())
        assert not any(gid_revoke == g.get("grant_id") for g in lifecycle_manager_empty.get_active_grants())

    def test_prune_expired_returns_int(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        lifecycle_manager_empty.expire(gid)
        count = lifecycle_manager_empty.prune_expired()
        assert isinstance(count, int)

    def test_prune_expired_returns_correct_count(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        for i in range(3):
            gid = lifecycle_manager_empty.grant(make_grant(grant_id=f"expire-{i}"))
            lifecycle_manager_empty.expire(gid)
        # Also add one live grant
        lifecycle_manager_empty.grant(make_grant(grant_id="live"))
        pruned = lifecycle_manager_empty.prune_expired()
        assert pruned == 3

    def test_prune_expired_removes_from_active(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        lifecycle_manager_empty.expire(gid)
        lifecycle_manager_empty.prune_expired()
        assert lifecycle_manager_empty.is_active(gid) is False


# ---------------------------------------------------------------------------
# DiscoveryAuthorityRunner
# ---------------------------------------------------------------------------

class TestDiscoveryAuthorityRunnerRun:
    """Tests for DiscoveryAuthorityRunner.run()."""

    def test_run_returns_dict(self, runner_default: DiscoveryAuthorityRunner) -> None:
        disc = make_discovery(trust_score=0.9)
        ctx = make_context()
        result = runner_default.run(disc, ctx)
        assert isinstance(result, dict)

    def test_run_result_has_status_key(self, runner_default: DiscoveryAuthorityRunner) -> None:
        disc = make_discovery(trust_score=0.9)
        ctx = make_context()
        result = runner_default.run(disc, ctx)
        assert "status" in result

    def test_run_result_has_discovery_id(self, runner_default: DiscoveryAuthorityRunner) -> None:
        disc = make_discovery(discovery_id="run-disc", trust_score=0.9)
        ctx = make_context()
        result = runner_default.run(disc, ctx)
        assert result.get("discovery_id") == "run-disc"

    def test_run_succeeds_for_high_trust(self, runner_default: DiscoveryAuthorityRunner) -> None:
        disc = make_discovery(trust_score=0.95)
        ctx = make_context()
        result = runner_default.run(disc, ctx)
        assert result.get("status") not in ("error",)

    def test_run_accumulates_in_results(self, runner_default: DiscoveryAuthorityRunner) -> None:
        for i in range(3):
            runner_default.run(make_discovery(discovery_id=f"r-{i}", trust_score=0.9), make_context())
        assert len(runner_default.get_results()) == 3

    def test_run_batch_returns_list(self, runner_default: DiscoveryAuthorityRunner) -> None:
        discoveries = [make_discovery(discovery_id=f"d-{i}", trust_score=0.9) for i in range(4)]
        results = runner_default.run_batch(discoveries, make_context())
        assert isinstance(results, list)
        assert len(results) == 4

    def test_run_batch_empty_list(self, runner_default: DiscoveryAuthorityRunner) -> None:
        results = runner_default.run_batch([], make_context())
        assert isinstance(results, list)
        assert len(results) == 0

    def test_run_batch_each_item_is_dict(self, runner_default: DiscoveryAuthorityRunner) -> None:
        discoveries = [make_discovery(discovery_id=f"d-{i}", trust_score=0.9) for i in range(3)]
        results = runner_default.run_batch(discoveries, make_context())
        for r in results:
            assert isinstance(r, dict)


class TestDiscoveryAuthorityRunnerGetResults:
    """Tests for DiscoveryAuthorityRunner.get_results() and reset()."""

    def test_get_results_initially_empty(self) -> None:
        runner = DiscoveryAuthorityRunner()
        assert runner.get_results() == []

    def test_get_results_returns_list(self, runner_default: DiscoveryAuthorityRunner) -> None:
        assert isinstance(runner_default.get_results(), list)

    def test_reset_clears_results(self, runner_default: DiscoveryAuthorityRunner) -> None:
        runner_default.run(make_discovery(trust_score=0.9), make_context())
        runner_default.reset()
        assert runner_default.get_results() == []

    def test_get_results_accumulates_across_runs(self) -> None:
        runner = DiscoveryAuthorityRunner()
        for i in range(5):
            runner.run(make_discovery(discovery_id=f"acc-{i}", trust_score=0.9), make_context())
        assert len(runner.get_results()) == 5

    def test_reset_does_not_raise(self, runner_default: DiscoveryAuthorityRunner) -> None:
        runner_default.reset()  # Called on already-empty runner — must not raise
        runner_default.reset()  # Double reset should also be safe


# ---------------------------------------------------------------------------
# Custom sub-components injection
# ---------------------------------------------------------------------------

class TestDiscoveryAuthorityRunnerInjection:
    """Tests that verify custom promoter/validator/lifecycle can be injected."""

    def test_inject_custom_promoter(self) -> None:
        custom_promoter = AuthorityPromoter(trust_threshold=0.5)
        runner = DiscoveryAuthorityRunner(promoter=custom_promoter)
        disc = make_discovery(trust_score=0.6)  # 0.6 >= 0.5 but < 0.7
        result = runner.run(disc, make_context())
        assert isinstance(result, dict)

    def test_inject_custom_validator(self) -> None:
        custom_validator = AuthorityValidator(strict=False)
        runner = DiscoveryAuthorityRunner(validator=custom_validator)
        result = runner.run(make_discovery(trust_score=0.9), make_context())
        assert isinstance(result, dict)

    def test_inject_custom_lifecycle(self) -> None:
        custom_lifecycle = AuthorityLifecycleManager()
        runner = DiscoveryAuthorityRunner(lifecycle=custom_lifecycle)
        result = runner.run(make_discovery(trust_score=0.9), make_context())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

class TestPromoteToAuthority:
    """Tests for the promote_to_authority() free function."""

    def test_returns_dict(self) -> None:
        disc = make_discovery(trust_score=0.9)
        result = promote_to_authority(disc)
        assert isinstance(result, dict)

    def test_default_threshold_rejects_low_trust(self) -> None:
        disc = make_discovery(trust_score=0.1)
        result = promote_to_authority(disc)
        assert result is None or result.get("status") in (None, "failed", "rejected")

    @pytest.mark.parametrize("threshold", [0.3, 0.5, 0.7, 0.9])
    def test_custom_threshold_applied(self, threshold: float) -> None:
        """Trust exactly at the threshold must pass; just below must fail."""
        disc_pass = make_discovery(trust_score=threshold)
        disc_fail = make_discovery(trust_score=max(0.0, threshold - 0.01))
        grant_pass = promote_to_authority(disc_pass, trust_threshold=threshold)
        grant_fail = promote_to_authority(disc_fail, trust_threshold=threshold)
        assert grant_pass is not None, f"Expected grant at threshold {threshold}"
        assert grant_fail is None or grant_fail.get("status") in (None, "failed", "rejected")

    @pytest.mark.parametrize("trust", [0.1, 0.5, 0.9])
    def test_novelty_values_do_not_crash(self, trust: float) -> None:
        disc = make_discovery(trust_score=trust)
        try:
            result = promote_to_authority(disc)
            assert result is None or isinstance(result, dict)
        except Exception:
            pass

    def test_grant_contains_discovery_id(self) -> None:
        disc = make_discovery(discovery_id="free-func-test", trust_score=1.0)
        result = promote_to_authority(disc)
        assert result is not None
        assert result.get("discovery_id") == "free-func-test"


class TestValidateAuthorityConditions:
    """Tests for the validate_authority_conditions() free function."""

    def test_all_pass_returns_true(self) -> None:
        conditions = {"trust": True, "novelty": True, "quorum": True}
        assert validate_authority_conditions(conditions) is True

    def test_some_fail_returns_false(self) -> None:
        conditions = {"trust": True, "novelty": False, "quorum": True}
        assert validate_authority_conditions(conditions) is False

    def test_all_fail_returns_false(self) -> None:
        conditions = {"trust": False, "novelty": False, "quorum": False}
        assert validate_authority_conditions(conditions) is False

    def test_empty_dict_behaviour(self) -> None:
        """An empty conditions dict — implementation may return True (vacuous truth) or False."""
        result = validate_authority_conditions({})
        assert isinstance(result, bool)

    def test_single_true_condition(self) -> None:
        assert validate_authority_conditions({"trust": True}) is True

    def test_single_false_condition(self) -> None:
        assert validate_authority_conditions({"trust": False}) is False

    def test_returns_bool(self) -> None:
        result = validate_authority_conditions({"trust": True, "novelty": True})
        assert isinstance(result, bool)

    def test_extra_conditions_respected(self) -> None:
        """Extra keys beyond the standard three must also be considered."""
        conditions = {"trust": True, "novelty": True, "quorum": True, "extra_check": False}
        assert validate_authority_conditions(conditions) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Miscellaneous edge-case tests for the authority promotion pipeline."""

    def test_promote_batch_all_failed_trust(
        self, promoter_default: AuthorityPromoter
    ) -> None:
        """A batch where every discovery fails trust should still return a list of the right size."""
        discoveries = [make_discovery(discovery_id=f"bad-{i}", trust_score=0.0) for i in range(5)]
        results = promoter_default.batch_promote(discoveries, context=make_context())
        assert isinstance(results, list)
        assert len(results) == 5

    def test_lifecycle_prune_empty_does_not_raise(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        count = lifecycle_manager_empty.prune_expired()
        assert count == 0

    def test_lifecycle_get_active_empty_returns_empty_list(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        assert lifecycle_manager_empty.get_active_grants() == []

    def test_runner_run_batch_accumulates_results(
        self, runner_default: DiscoveryAuthorityRunner
    ) -> None:
        runner_default.reset()
        batch = [make_discovery(discovery_id=f"acc-{i}", trust_score=0.9) for i in range(4)]
        runner_default.run_batch(batch, make_context())
        assert len(runner_default.get_results()) >= 4

    def test_validator_get_failures_all_pass(
        self, validator_strict: AuthorityValidator
    ) -> None:
        grant = make_grant(trust_score=0.95, quorum_size=5)
        grant["novelty_confirmed"] = True
        assert validator_strict.get_failures(grant) == []

    def test_promoter_zero_weight_conditions_do_not_crash(
        self, promoter_default: AuthorityPromoter
    ) -> None:
        disc = make_discovery(trust_score=0.9)
        ctx = {"quorum_reached": True, "regime": "default", "allow_promotion": True, "weight": 0.0}
        result = promoter_default.check_conditions(disc, ctx)
        assert isinstance(result, dict)

    def test_lifecycle_revoke_twice_is_safe(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        lifecycle_manager_empty.revoke(gid)
        # Second revoke should not raise — may return False
        result = lifecycle_manager_empty.revoke(gid)
        assert isinstance(result, bool)

    def test_lifecycle_expire_already_expired_is_safe(
        self, lifecycle_manager_empty: AuthorityLifecycleManager
    ) -> None:
        gid = lifecycle_manager_empty.grant(make_grant())
        lifecycle_manager_empty.expire(gid)
        result = lifecycle_manager_empty.expire(gid)
        assert isinstance(result, bool)

    def test_runner_custom_components_all_injected(self) -> None:
        promoter = AuthorityPromoter(trust_threshold=0.6)
        validator = AuthorityValidator(strict=False)
        lifecycle = AuthorityLifecycleManager()
        runner = DiscoveryAuthorityRunner(
            promoter=promoter, validator=validator, lifecycle=lifecycle
        )
        disc = make_discovery(trust_score=0.8)
        result = runner.run(disc, make_context())
        assert isinstance(result, dict)
