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
def test_sections_compare_context_and_proposition() -> None:
    left = make_section('a')
    right = make_section('b')
    assert left.compatible_with(right) is True
    assert left.restrict(('child',)).coordinate.key.endswith('child')
