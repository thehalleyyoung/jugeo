from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from jugeo.evidence.trust import TrustTier
from jugeo.generation.goals import ConstructionGoal, GoalPriority
from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion

def make_goal(name: str = 'goal', patch: str = 'p', budget: int = 1, priority: GoalPriority = GoalPriority.MEDIUM):
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    support = SupportRegion(coordinate, frozenset({patch}))
    return ConstructionGoal(name, support, TrustTier.PROPOSAL, priority, budget)
    
from jugeo.geometry.supports import SupportRegion
from jugeo.interfaces.api import JuGeoAPI
from jugeo.judgments.contexts import SemanticContext
from jugeo.judgments.exports import export_section
from jugeo.judgments.judgment_terms import JudgmentStatus, LocalJudgment
from jugeo.judgments.sections import JudgmentSection
from jugeo.geometry.site import CoordinateKind, CoordinateObject


def test_api_exports_records_as_dicts() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    section = JudgmentSection(coordinate, SemanticContext(coordinate), LocalJudgment(coordinate, 'P', {'artifact': 'x'}, status=JudgmentStatus.SETTLED), SupportRegion(coordinate, frozenset({'p'})), 'p')
    api = JuGeoAPI()
    export = export_section(section)
    assert api.export_record(export)['coordinate'] == 'coord'
