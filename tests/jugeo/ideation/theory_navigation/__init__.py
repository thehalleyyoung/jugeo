"""Tests for the jugeo.ideation.theory_navigation package.

This test suite covers:
- models: core data models (TheoryNode, TheorySpace, NavigationPath, etc.)
- manifest: package manifest and registry
- s01_space_construction: theory space construction algorithms
- s02_purpose_conditioning: purpose-conditioned navigation
- s03_path_finding: path-finding algorithms
- algorithms: high-level navigation algorithms
- integration: integration with other jugeo packages
- theorems: formal theorems about navigation
"""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TESTS_ROOT = ROOT / "tests" / "jugeo"
RELATIVE = Path(__file__).resolve().parent.relative_to(TESTS_ROOT)
SOURCE_PATH = ROOT / "src" / "jugeo" / RELATIVE
if SOURCE_PATH.exists():
    __path__.append(str(SOURCE_PATH))

TEST_DATA_DIR = Path(__file__).parent / "data"


# Shared test factories - minimal versions for use across test files
def make_raw_node(
    node_id: str = "n1",
    name: str = "Test Theory",
    description: str = "A test theory about algebraic structures",
    maturity: str = "mature",
    purpose_alignment: float = 0.8,
    connections: list | None = None,
) -> dict:
    """Create a raw node dict for testing."""
    return {
        "id": node_id,
        "name": name,
        "description": description,
        "maturity": maturity,
        "purpose_alignment": purpose_alignment,
        "connections": connections or [],
        "metadata": {},
    }
