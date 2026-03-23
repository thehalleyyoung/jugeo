"""Tests for semantic contexts in the shared JuGeo core.

The goal of this suite is to pin down the theory-facing behavior of
``jugeo.judgments.contexts`` rather than to merely smoke-test a dictionary-like
helper. ``preliminaries/theory2.tex`` treats contexts as a presheaf of local
dependent environments, and these tests exercise that doctrine directly:
restriction, ambient packs, assumptions, dependent scope, and merge discipline
all stay visible. A copilot-tagged provenance entry may appear, but it never
substitutes for semantic compatibility.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.judgments.contexts import (
    ContextBinding,
    ContextPresheaf,
    SemanticContext,
    merge_contexts,
    restrict_context,
)


def make_coordinate(*path: str, name: str | None = None, labels: tuple[str, ...] = ()) -> CoordinateObject:
    """Build a region coordinate with predictable keys for the tests."""

    path_tuple = path or ("coord",)
    return CoordinateObject(name or path_tuple[-1], CoordinateKind.REGION, path_tuple, frozenset(labels))


def make_binding(
    name: str,
    value: object,
    *,
    provenance: tuple[str, ...] = (),
    depends_on: tuple[str, ...] = (),
    scope_markers: tuple[str, ...] = (),
    transport_tags: tuple[str, ...] = (),
) -> ContextBinding:
    """Create a binding with concise defaults for readable tests."""

    return ContextBinding(
        name=name,
        value=value,
        provenance=provenance,
        depends_on=depends_on,
        scope_markers=scope_markers,
        transport_tags=transport_tags,
    )


def make_context(
    coordinate: CoordinateObject,
    *bindings: ContextBinding,
    assumptions: tuple[str, ...] = (),
    ambient_packs: tuple[str, ...] = (),
    trust_boundary: str = "context",
    dependent_scope: tuple[str, ...] = (),
    support_labels: tuple[str, ...] = (),
    provenance: tuple[str, ...] = (),
) -> SemanticContext:
    """Create a semantic context with explicit defaults."""

    return SemanticContext(
        coordinate=coordinate,
        bindings=bindings,
        assumptions=assumptions,
        ambient_packs=ambient_packs,
        trust_boundary=trust_boundary,
        dependent_scope=dependent_scope,
        support_labels=frozenset(support_labels),
        provenance=provenance,
    )


def assert_binding_names(context: SemanticContext, expected: tuple[str, ...]) -> None:
    """Small assertion helper that keeps failure output focused."""

    assert context.binding_names() == expected
    assert tuple(context.binding_map()) == expected


def test_context_binding_normalizes_metadata_and_rejects_self_dependency() -> None:
    binding = make_binding(
        "cell",
        5,
        provenance=("copilot", "copilot", "proposal"),
        depends_on=("env", "env"),
        scope_markers=("closure", "closure", "cell"),
        transport_tags=("capture", "capture", "epoch"),
    )
    assert binding.provenance == ("copilot", "proposal")
    assert binding.depends_on == ("env",)
    assert binding.scope_markers == ("closure", "cell")
    assert binding.transport_tags == ("capture", "epoch")
    with pytest.raises(ValueError, match="cannot depend on itself"):
        make_binding("cell", 5, depends_on=("cell",))


def test_binding_merge_metadata_preserves_value_and_accumulates_tags() -> None:
    left = make_binding(
        "x",
        1,
        provenance=("author",),
        depends_on=("root",),
        scope_markers=("module",),
        transport_tags=("capture",),
    )
    right = make_binding(
        "x",
        1,
        provenance=("copilot",),
        depends_on=("root", "seed"),
        scope_markers=("closure",),
        transport_tags=("transport",),
    )
    merged = left.merge_metadata(right)
    assert merged.value == 1
    assert merged.provenance == ("author", "copilot")
    assert merged.depends_on == ("root", "seed")
    assert merged.scope_markers == ("module", "closure")
    assert merged.transport_tags == ("capture", "transport")
    with pytest.raises(ValueError, match="context conflict"):
        left.merge_metadata(make_binding("x", 2))


def test_semantic_context_defaults_scope_and_support_to_coordinate() -> None:
    coordinate = make_coordinate("pkg", "module", labels=("site",))
    context = make_context(coordinate, make_binding("x", 1), provenance=("seed",))
    assert context.dependent_scope == ("pkg", "module")
    assert context.support_labels == frozenset({"site"})
    assert context.provenance == ("seed",)


def test_semantic_context_rejects_duplicate_binding_names() -> None:
    coordinate = make_coordinate("coord")
    with pytest.raises(ValueError, match="duplicate context bindings"):
        make_context(coordinate, make_binding("x", 1), make_binding("x", 1))


def test_binding_map_lookup_and_mapping_export_are_consistent() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(
        coordinate,
        make_binding("x", {"n": 1}, provenance=("author",)),
        make_binding("y", 2, depends_on=("x",), scope_markers=("local",)),
        assumptions=("caller-ready",),
        ambient_packs=("arith",),
        provenance=("elaborate",),
    )
    assert context.binding_map() == {"x": {"n": 1}, "y": 2}
    assert context.lookup_binding("x") == context.bindings[0]
    assert context.lookup_binding("missing") is None
    exported = context.to_mapping()
    assert exported["coordinate"] == "coord"
    assert exported["bindings"][1]["depends_on"] == ["x"]
    assert exported["assumptions"] == ["caller-ready"]
    assert exported["ambient_packs"] == ["arith"]


def test_dependency_closure_tracks_transitive_requirements_in_order() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(
        coordinate,
        make_binding("seed", 1),
        make_binding("carrier", 2, depends_on=("seed",)),
        make_binding("payload", 3, depends_on=("carrier",)),
        make_binding("display", 4, depends_on=("payload",)),
    )
    assert context.dependency_closure(("display",)) == ("seed", "carrier", "payload", "display")
    assert context.dependency_closure(("carrier", "missing")) == ("seed", "carrier")


def test_restrict_context_preserves_old_behavior_for_simple_name_filter() -> None:
    coordinate = make_coordinate("coord")
    left = make_context(coordinate, make_binding("x", 1))
    right = make_context(coordinate, make_binding("y", 2))
    merged = merge_contexts(left, right)
    assert merged.binding_map() == {"x": 1, "y": 2}
    assert restrict_context(merged, names=("x",)).binding_map() == {"x": 1}


def test_restrict_context_follows_dependency_closure_by_default() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(
        coordinate,
        make_binding("env", 0),
        make_binding("x", 1, depends_on=("env",)),
        make_binding("result", 2, depends_on=("x",)),
        assumptions=("caller-ready", "task-local"),
        ambient_packs=("arith", "effects"),
        provenance=("elaborate",),
    )
    restricted = restrict_context(context, names=("result",))
    assert_binding_names(restricted, ("env", "x", "result"))
    assert restricted.assumptions == ("caller-ready", "task-local")
    assert restricted.ambient_packs == ("arith", "effects")
    assert restricted.provenance == ("elaborate",)


def test_restrict_context_can_skip_dependency_closure_when_requested() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(
        coordinate,
        make_binding("env", 0),
        make_binding("payload", 3, depends_on=("env",)),
    )
    restricted = restrict_context(context, names=("payload",), include_dependencies=False)
    assert_binding_names(restricted, ("payload",))


def test_restrict_context_can_refine_to_descendant_coordinate() -> None:
    parent = make_coordinate("pkg")
    child = make_coordinate("pkg", "fn", labels=("child",))
    context = make_context(
        parent,
        make_binding("module_name", "pkg"),
        make_binding("arity", 1, depends_on=("module_name",)),
        assumptions=("module-open",),
        ambient_packs=("python",),
        support_labels=("module",),
        provenance=("seed",),
    )
    restricted = restrict_context(context, coordinate=child, names=("arity",), support_labels=("runtime",))
    assert restricted.coordinate == child
    assert_binding_names(restricted, ("module_name", "arity"))
    assert restricted.dependent_scope == ("pkg", "fn")
    assert restricted.support_labels == frozenset({"module", "child", "runtime"})
    assert restricted.provenance == (
        "seed",
        "restrict-coordinate",
        "restrict-scope",
        "restrict-support",
    )


def test_restrict_context_rejects_unrelated_coordinate_refinement() -> None:
    source = make_coordinate("pkg")
    sibling = make_coordinate("other", "fn")
    context = make_context(source, make_binding("x", 1))
    with pytest.raises(ValueError, match="unrelated coordinate"):
        restrict_context(context, coordinate=sibling)


def test_restrict_context_can_filter_assumptions_and_ambient_packs() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(
        coordinate,
        make_binding("x", 1),
        assumptions=("alpha", "beta", "gamma"),
        ambient_packs=("arith", "effects", "transport"),
        provenance=("seed",),
    )
    restricted = restrict_context(
        context,
        assumptions=("beta", "missing"),
        ambient_packs=("transport",),
        dependent_scope=("coord", "child"),
    )
    assert restricted.assumptions == ("beta",)
    assert restricted.ambient_packs == ("transport",)
    assert restricted.dependent_scope == ("coord", "child")
    assert restricted.provenance == (
        "seed",
        "restrict-assumptions",
        "restrict-ambient-packs",
        "restrict-scope",
    )


def test_restrict_context_identity_request_returns_same_instance() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(coordinate, make_binding("x", 1))
    assert restrict_context(context) is context


def test_merge_contexts_deduplicates_assumptions_packs_and_metadata() -> None:
    coordinate = make_coordinate("coord")
    left = make_context(
        coordinate,
        make_binding("x", 1, provenance=("author",), depends_on=("seed",)),
        assumptions=("alpha", "beta"),
        ambient_packs=("arith", "effects"),
        support_labels=("left",),
        provenance=("left",),
    )
    right = make_context(
        coordinate,
        make_binding("x", 1, provenance=("copilot",), scope_markers=("closure",)),
        make_binding("y", 2, depends_on=("x",), transport_tags=("capture",)),
        assumptions=("beta", "gamma"),
        ambient_packs=("effects", "transport"),
        support_labels=("right",),
        provenance=("right",),
    )
    merged = merge_contexts(left, right)
    assert_binding_names(merged, ("x", "y"))
    assert merged.lookup_binding("x").provenance == ("author", "copilot")
    assert merged.lookup_binding("x").scope_markers == ("closure",)
    assert merged.lookup_binding("y").transport_tags == ("capture",)
    assert merged.assumptions == ("alpha", "beta", "gamma")
    assert merged.ambient_packs == ("arith", "effects", "transport")
    assert merged.support_labels == frozenset({"left", "right"})
    assert merged.provenance == ("left", "right", "merge")


def test_merge_contexts_prefers_more_local_coordinate_and_restricts_ancestor() -> None:
    parent = make_coordinate("pkg")
    child = make_coordinate("pkg", "fn", labels=("function",))
    ancestor_context = make_context(
        parent,
        make_binding("module_name", "pkg"),
        assumptions=("module-open",),
        ambient_packs=("python",),
        provenance=("ancestor",),
    )
    local_context = make_context(
        child,
        make_binding("local_arg", "x", depends_on=("module_name",)),
        assumptions=("call-open",),
        ambient_packs=("effects",),
        provenance=("local",),
    )
    merged = merge_contexts(ancestor_context, local_context)
    assert merged.coordinate == child
    assert_binding_names(merged, ("module_name", "local_arg"))
    assert merged.assumptions == ("module-open", "call-open")
    assert merged.ambient_packs == ("python", "effects")
    assert merged.dependent_scope == ("pkg", "fn")
    assert merged.provenance == ("ancestor", "restrict-coordinate", "restrict-scope", "restrict-support", "local", "merge")


def test_merge_contexts_accepts_explicit_common_refinement_for_siblings() -> None:
    root = make_coordinate("pkg")
    left_coordinate = make_coordinate("pkg", "left")
    right_coordinate = make_coordinate("pkg", "right")
    target = make_coordinate("pkg", "left", "overlap")
    left = make_context(left_coordinate, make_binding("x", 1), provenance=("left",))
    right = make_context(root, make_binding("root", 0), provenance=("root",))
    merged = merge_contexts(right, left, coordinate=target)
    assert merged.coordinate == target
    assert_binding_names(merged, ("root", "x"))
    assert merged.provenance == ("root", "restrict-coordinate", "restrict-scope", "left", "restrict-coordinate", "restrict-scope", "merge")


def test_merge_contexts_rejects_unrelated_coordinates_without_refinement() -> None:
    left = make_context(make_coordinate("left"), make_binding("x", 1))
    right = make_context(make_coordinate("right"), make_binding("y", 2))
    with pytest.raises(ValueError, match="unrelated coordinates"):
        merge_contexts(left, right)


def test_merge_contexts_rejects_conflicting_binding_values() -> None:
    coordinate = make_coordinate("coord")
    left = make_context(coordinate, make_binding("x", 1))
    right = make_context(coordinate, make_binding("x", 2))
    with pytest.raises(ValueError, match="context conflict on x"):
        merge_contexts(left, right)


def test_merge_contexts_rejects_trust_boundary_promotion() -> None:
    coordinate = make_coordinate("coord")
    left = make_context(coordinate, make_binding("x", 1), trust_boundary="context")
    right = make_context(coordinate, make_binding("y", 2), trust_boundary="solver")
    with pytest.raises(ValueError, match="trust boundaries"):
        merge_contexts(left, right)


def test_context_method_helpers_delegate_to_module_functions() -> None:
    coordinate = make_coordinate("coord")
    left = make_context(coordinate, make_binding("x", 1))
    right = make_context(coordinate, make_binding("y", 2))
    merged = left.merge(right)
    assert merged.binding_map() == {"x": 1, "y": 2}
    assert left.restrict(names=("x",)) is left
    assert right.restrict(names=("y",)).binding_map() == {"y": 2}


def test_context_presheaf_exact_assignment_round_trips() -> None:
    coordinate = make_coordinate("coord")
    context = make_context(coordinate, make_binding("x", 1))
    presheaf = ContextPresheaf()
    presheaf.assign(context)
    assert coordinate in presheaf
    assert len(presheaf) == 1
    assert presheaf.exact(coordinate) is context
    assert presheaf.restrict(coordinate) is context


def test_context_presheaf_restricts_from_nearest_ancestor() -> None:
    root = make_coordinate("pkg")
    child = make_coordinate("pkg", "module")
    grandchild = make_coordinate("pkg", "module", "fn")
    presheaf = ContextPresheaf()
    presheaf.assign(make_context(root, make_binding("root", 0), provenance=("root",)))
    presheaf.assign(make_context(child, make_binding("module", 1), provenance=("module",)))
    restricted = presheaf.restrict(grandchild)
    assert restricted is not None
    assert restricted.coordinate == grandchild
    assert_binding_names(restricted, ("module",))
    assert restricted.provenance == ("module", "restrict-coordinate", "restrict-scope")


def test_context_presheaf_returns_none_for_uncovered_coordinate() -> None:
    presheaf = ContextPresheaf()
    presheaf.assign(make_context(make_coordinate("known"), make_binding("x", 1)))
    assert presheaf.restrict(make_coordinate("missing", "child")) is None


def test_context_presheaf_merge_assign_uses_merge_discipline() -> None:
    coordinate = make_coordinate("coord")
    presheaf = ContextPresheaf()
    first = make_context(coordinate, make_binding("x", 1), provenance=("first",))
    second = make_context(coordinate, make_binding("y", 2), provenance=("second",))
    merged = presheaf.merge_assign(first)
    assert merged is first
    merged = presheaf.merge_assign(second)
    assert merged.binding_map() == {"x": 1, "y": 2}
    assert presheaf.restrict(coordinate) == merged


def test_context_presheaf_extend_and_materialize_are_deterministic() -> None:
    a = make_coordinate("pkg")
    b = make_coordinate("pkg", "module")
    c = make_coordinate("pkg", "module", "fn")
    presheaf = ContextPresheaf()
    presheaf.extend(
        (
            make_context(a, make_binding("a", 1)),
            make_context(b, make_binding("b", 2)),
        )
    )
    materialized = presheaf.materialize((a, b, c))
    assert [item.coordinate.key if item else None for item in materialized] == ["pkg", "pkg/module", "pkg/module/fn"]
    assert [item.binding_names() if item else () for item in materialized] == [("a",), ("b",), ("b",)]
    assert [key for key, _ in presheaf.items()] == ["pkg", "pkg/module"]
    assert [context.coordinate.key for context in presheaf.values()] == ["pkg", "pkg/module"]


@pytest.mark.parametrize(
    ("names", "include_dependencies", "expected"),
    [
        (("leaf",), True, ("root", "mid", "leaf")),
        (("leaf",), False, ("leaf",)),
        (("mid",), True, ("root", "mid")),
        (("unknown",), True, ()),
    ],
)
def test_restrict_context_parameterized_name_visibility(
    names: tuple[str, ...],
    include_dependencies: bool,
    expected: tuple[str, ...],
) -> None:
    coordinate = make_coordinate("coord")
    context = make_context(
        coordinate,
        make_binding("root", 0),
        make_binding("mid", 1, depends_on=("root",)),
        make_binding("leaf", 2, depends_on=("mid",)),
    )
    restricted = restrict_context(context, names=names, include_dependencies=include_dependencies)
    assert restricted.binding_names() == expected


def test_contexts_remain_compatible_with_sections_style_usage() -> None:
    """Guard the simple constructor path used by nearby shared judgment tests."""

    coordinate = make_coordinate("coord")
    context = SemanticContext(coordinate, (ContextBinding("x", 1),))
    assert context.binding_map() == {"x": 1}
    assert context.assumptions == ()
    assert context.ambient_packs == ()
    assert context.trust_boundary == "context"
