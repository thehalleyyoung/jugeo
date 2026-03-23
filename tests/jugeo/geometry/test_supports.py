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
from jugeo.geometry.supports import compute_support, star_of_support


def test_star_of_support_includes_touching_patches() -> None:
    target = make_coordinate('target', 'target')
    patches = (make_coordinate('a', 'target', 'a'), make_coordinate('b', 'target', 'b'))
    cover = Cover(target, patches, ((patches[0].key, patches[1].key),))
    support = compute_support(target, cover=cover)
    star = star_of_support(support, cover)
    assert set(star.adjacent_patches) == {patches[0].key, patches[1].key}
