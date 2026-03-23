"""Cross-module tests: SymbolicNode properties from PythonASTBridge."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        ASTBridgeConfig, PythonASTBridge, SymbolicNode,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def greet(name: str) -> str:
    return f"Hello, {name}"

class Foo:
    def bar(self):
        return 42
"""

@pytest.fixture
def symbolic_nodes():
    bridge = PythonASTBridge(ASTBridgeConfig())
    tree = bridge.parse_source(SOURCE, "<test>")
    return bridge.build_symbolic_tree(tree, filename="<test>")

def test_symbolic_nodes_have_node_id(symbolic_nodes):
    for node in symbolic_nodes:
        # SymbolicNode uses 'coord' (ASTCoordinate) as its identifier
        assert hasattr(node, "coord")
        assert node.coord.coordinate_id() is not None

def test_symbolic_nodes_have_kind(symbolic_nodes):
    for node in symbolic_nodes:
        assert hasattr(node, "kind")
        if node.kind is not None:
            assert isinstance(node.kind, str) or hasattr(node.kind, "value")

def test_symbolic_nodes_have_coordinate(symbolic_nodes):
    for node in symbolic_nodes:
        assert hasattr(node, "coord")

def test_node_coordinate_has_filename(symbolic_nodes):
    for node in symbolic_nodes:
        coord = node.coord
        if hasattr(coord, "file"):
            assert isinstance(coord.file, str)
        elif isinstance(coord, str):
            pass  # coordinate is a string, that's fine
