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
from jugeo.geometry.hypercovers import build_hypercover, enumerate_higher_overlaps


def test_hypercover_builds_layers() -> None:
    target = make_coordinate('target', 'target')
    patches = (make_coordinate('a', 'target', 'a'), make_coordinate('b', 'target', 'b'))
    cover = Cover(target, patches, ((patches[0].key, patches[1].key),))
    hypercover = build_hypercover(cover, depth=1)
    assert hypercover.layers
    assert enumerate_higher_overlaps(cover)[0] == (patches[0].key, patches[1].key)
