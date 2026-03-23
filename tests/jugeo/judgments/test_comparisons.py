from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.judgments.contexts import ContextBinding, SemanticContext
from jugeo.judgments.judgment_terms import JudgmentStatus, LocalJudgment


def make_section(patch: str = 'patch-a', proposition: str = 'P'):
    from jugeo.judgments.sections import JudgmentSection
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    context = SemanticContext(coordinate, (ContextBinding('x', 1),))
    judgment = LocalJudgment(coordinate, proposition, {'artifact': 'x'}, status=JudgmentStatus.SETTLED)
    support = SupportRegion(coordinate, frozenset({patch}))
    return JudgmentSection(coordinate, context, judgment, support, patch)
from jugeo.judgments.comparisons import compare_sections


def test_compare_sections_reports_overlap_mismatch() -> None:
    left = make_section('a', 'P')
    right = make_section('b', 'Q')
    result = compare_sections(left, right)
    assert result.compatible is False
    assert result.obstructions
