from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def make_coordinate(name: str, *parts: str):
    from jugeo.geometry.site import CoordinateKind, CoordinateObject
    return CoordinateObject(name, CoordinateKind.REGION, tuple(parts or (name,)))
from jugeo.geometry.covers import Cover
from jugeo.geometry.descent import run_descent


def test_descent_reports_obstruction_on_mismatch() -> None:
    target = make_coordinate('target', 'target')
    patches = (make_coordinate('a', 'target', 'a'), make_coordinate('b', 'target', 'b'))
    cover = Cover(target, patches, ((patches[0].key, patches[1].key),))
    report = run_descent(cover, {patches[0].key: {'value': 1}, patches[1].key: {'value': 2}})
    assert report.success is False
    assert report.obstruction_rank == 1
