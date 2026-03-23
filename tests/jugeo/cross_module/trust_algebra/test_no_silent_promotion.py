"""Cross-module tests: Trust policy disallows silent promotion."""
import pytest

try:
    from jugeo.evidence.trust import TrustPolicy
    from jugeo.runtime_defaults import default_trust_policy
    from jugeo.problem_modes.bug_detection import BugReport
    from jugeo.generation.inhabitant_fleets.models import InhabitantProposal, TrustTier
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

def test_trust_policy_importable():
    assert TrustPolicy is not None

def test_default_trust_policy_fn():
    policy = default_trust_policy()
    assert policy is not None

def test_bug_reports_stay_oracle_proposed():
    bug = BugReport()
    assert bug.trust_tier == "ORACLE_PROPOSED"

def test_proposal_trust_tier_is_proposal():
    proposal = InhabitantProposal(
        proposal_id="no-promote-001",
        patch_id="patch-001",
        section_label="sec_A",
        semantic_content="return x",
        proposer_id="agent-001",
        trust_tier=TrustTier.PROPOSAL,
    )
    assert proposal.trust_tier == TrustTier.PROPOSAL
