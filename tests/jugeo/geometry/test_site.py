from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def make_coordinate(name: str, *parts: str):
    from jugeo.geometry.site import CoordinateKind, CoordinateObject
    return CoordinateObject(name, CoordinateKind.REGION, tuple(parts or (name,)))
from jugeo.geometry.site import build_site, restrict_coordinate


def test_restrict_coordinate_extends_path() -> None:
    coordinate = make_coordinate('root', 'root')
    restricted = restrict_coordinate(coordinate, suffix=('child',), support_labels=('alpha',))
    site = build_site([coordinate, restricted])
    assert restricted.key.endswith('child')
    assert site.descendants(coordinate) == (restricted,)
