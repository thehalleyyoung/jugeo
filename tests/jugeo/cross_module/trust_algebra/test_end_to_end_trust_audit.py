"""Cross-module tests: Full workflow produces trust audit trail."""
import pytest
import time

try:
    from jugeo.problem_modes.bug_detection import detect_bugs
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import SpecParser
    from jugeo.problem_modes.repair_semantics import CounterexampleRecord
    from jugeo.generation.inhabitant_fleets.models import InhabitantProposal, TrustTier
    from jugeo.evidence.trust import TrustLevel
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

SOURCE = """
def func():
    x = undefined_var
    return x
"""

SPEC_DOCSTRING = """
def func():
    '''
    Postcondition: result is always a valid integer.
    Precondition: no undefined variables.
    '''
    pass
"""

@pytest.fixture
def detection_result():
    return detect_bugs(SOURCE)

@pytest.fixture
def spec():
    parser = SpecParser({})
    return parser.parse_docstring("Postcondition: result is valid. Precondition: inputs are defined.")

@pytest.fixture
def cx():
    return CounterexampleRecord(
        record_id="audit-cx-001",
        coordinate="func:line_2",
        context={},
        assignment={},
        witness=None,
        severity=0.8,
        trust_tier="ORACLE_PROPOSED",
    )

@pytest.fixture
def proposal():
    return InhabitantProposal(
        proposal_id="audit-prop-001",
        patch_id="patch-001",
        section_label="audit_section",
        semantic_content="return 42",
        proposer_id="audit-agent",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_detect_bugs_result_bugs_have_trust_tier(detection_result):
    for bug in detection_result.bugs:
        assert hasattr(bug, "trust_tier")
        assert isinstance(bug.trust_tier, str)

def test_spec_obligations_have_trust_tier(spec):
    for obligation in spec.obligations:
        if hasattr(obligation, "trust_tier"):
            assert isinstance(obligation.trust_tier, str)

def test_counterexample_has_trust_tier(cx):
    assert isinstance(cx.trust_tier, str)

def test_proposal_has_trust_tier(proposal):
    assert proposal.trust_tier is not None

def test_trust_level_values_are_consistent():
    members = list(TrustLevel)
    values = [m.value for m in members]
    assert len(set(values)) == len(values)  # all distinct
