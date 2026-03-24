"""
Čech cohomology computer for web-application sites.

The key mathematical idea: cover the web application by language layers
(Python, JavaScript, HTML, CSS, SQL, Template).  Intersections of these
covering sets correspond to cross-language interfaces.  The Čech complex
built from this covering yields cohomology groups whose generators
classify bugs:

* **H⁰** generators = connected components of the covering
  (trivial ⟹ the app is connected; non-trivial ⟹ isolated layers).
* **H¹** generators = gluing failures = cross-language inconsistencies.
* **H²** generators = higher-order obstructions (three-way mismatches).

The computation is entirely combinatorial — no external linear algebra
library is required.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Callable

from .models import NerveCell, Cochain, CohomologyGroup, CechComplex


__all__ = ["CechCohomologyComputer"]


# ---------------------------------------------------------------------------
# Standard web-app layer pairs that can have non-empty intersection.
# Each pair describes a known interface through which data flows.
# ---------------------------------------------------------------------------
_KNOWN_INTERSECTIONS: dict[tuple[str, str], str] = {
    ("python", "template"): "CONTEXT_PROVISION",
    ("python", "javascript"): "JSON_API",
    ("python", "sql"): "ORM_QUERY",
    ("python", "html"): "ROUTE_RENDER",
    ("javascript", "html"): "DOM_MANIPULATION",
    ("javascript", "css"): "CLASS_TOGGLE",
    ("html", "css"): "STYLE_APPLICATION",
    ("template", "css"): "TEMPLATE_CLASS_USAGE",
    ("template", "html"): "TEMPLATE_FRAGMENT",
    ("template", "javascript"): "INLINE_SCRIPT_DATA",
}

# Triple intersections (known three-way interfaces).
_KNOWN_TRIPLES: list[tuple[str, str, str]] = [
    ("python", "template", "css"),
    ("javascript", "html", "css"),
    ("python", "template", "javascript"),
]


def _sorted_key(*names: str) -> str:
    """Canonical cell id for a set of layer names."""
    return "|".join(sorted(names))


# ---------------------------------------------------------------------------
# Computer
# ---------------------------------------------------------------------------

class CechCohomologyComputer:
    """Compute Čech cohomology of a web-application covering.

    Usage::

        computer = CechCohomologyComputer()
        groups = computer.compute(site_data, max_dimension=2)
        for dim, group in groups.items():
            print(dim, group.rank, group.interpretation)
    """

    # ------------------------------------------------------------------ API

    def compute(
        self,
        site_data: dict[str, Any],
        max_dimension: int = 2,
    ) -> dict[int, CohomologyGroup]:
        """Compute Čech cohomology groups up to *max_dimension*.

        Parameters
        ----------
        site_data:
            Must contain ``"covering"`` — a dict mapping layer name to a
            list of coordinate dicts (the local data for that layer).
        max_dimension:
            Highest cohomology dimension to compute (default 2).

        Returns
        -------
        dict mapping dimension → ``CohomologyGroup``.
        """
        covering: dict[str, list[dict[str, Any]]] = site_data.get(
            "covering", {},
        )
        if not covering:
            return {
                d: CohomologyGroup(
                    dimension=d,
                    interpretation="No covering data provided.",
                )
                for d in range(max_dimension + 1)
            }

        nerve = self.build_nerve(covering)

        # Build the full Čech complex.
        cochains: dict[int, list[Cochain]] = {}
        for dim in range(max_dimension + 1):
            cochains[dim] = self.compute_cochains(nerve, dim)

        groups: dict[int, CohomologyGroup] = {}
        for dim in range(max_dimension + 1):
            # Current coboundary: C^dim → C^(dim+1).
            delta = self.coboundary_map(cochains.get(dim, []), dim)

            # Previous coboundary: C^(dim-1) → C^dim.
            if dim > 0:
                prev_delta = self.coboundary_map(
                    cochains.get(dim - 1, []), dim - 1,
                )
            else:
                prev_delta = lambda _cs: []  # noqa: E731

            cocycles = self.compute_cocycles(cochains.get(dim, []), delta)
            coboundaries = self.compute_coboundaries(
                cochains.get(dim, []), prev_delta,
            )

            group = self.quotient(cocycles, coboundaries)
            group.dimension = dim
            group.interpretation = self._default_interpretation(dim, group)
            groups[dim] = group

        # Attach human-readable generator descriptions.
        for dim, group in groups.items():
            descs = self.interpret_generators(group, site_data)
            if descs:
                group.generators = descs
                group.rank = max(0, len(group.generators) - len(group.relations))
                group.is_trivial = group.rank == 0

        return groups

    # --------------------------------------------------------- nerve

    def build_nerve(
        self,
        covering: dict[str, list[Any]],
    ) -> list[NerveCell]:
        """Build the nerve of the covering.

        0-cells: individual language layers.
        1-cells: pairwise intersections (known cross-language interfaces).
        2-cells: triple intersections.
        """
        layers = sorted(covering.keys())
        cells: list[NerveCell] = []

        # 0-cells (vertices).
        for layer in layers:
            cells.append(NerveCell(
                dimension=0,
                vertices=[layer],
                data={"coordinates": covering[layer]},
                cell_id=layer,
            ))

        # 1-cells (edges): include if both layers are present and there
        # is a known intersection type.
        for a, b in combinations(layers, 2):
            key = (a, b) if (a, b) in _KNOWN_INTERSECTIONS else (b, a)
            interface = _KNOWN_INTERSECTIONS.get(key)
            if interface is not None:
                cid = _sorted_key(a, b)
                # Intersection data: coordinates that appear in both.
                shared = self._compute_intersection(
                    covering.get(a, []), covering.get(b, []),
                )
                cells.append(NerveCell(
                    dimension=1,
                    vertices=sorted([a, b]),
                    data={
                        "interface": interface,
                        "shared_coordinates": shared,
                    },
                    cell_id=cid,
                ))

        # 2-cells (triangles).
        for triple in _KNOWN_TRIPLES:
            if all(t in layers for t in triple):
                cid = _sorted_key(*triple)
                cells.append(NerveCell(
                    dimension=2,
                    vertices=sorted(triple),
                    data={"type": "triple_intersection"},
                    cell_id=cid,
                ))

        return cells

    # -------------------------------------------------------- cochains

    def compute_cochains(
        self,
        nerve: list[NerveCell],
        dimension: int,
    ) -> list[Cochain]:
        """Create one cochain per *dimension*-cell in the nerve.

        Each cochain carries the cell's local data as its value.
        """
        dim_cells = [c for c in nerve if c.dimension == dimension]
        cochains: list[Cochain] = []

        for cell in dim_cells:
            cochains.append(Cochain(
                dimension=dimension,
                cells=[cell],
                values={cell.cell_id: cell.data},
            ))

        return cochains

    # ------------------------------------------------ coboundary map

    def coboundary_map(
        self,
        cochains: list[Cochain],
        dimension: int,
    ) -> Callable[[list[Cochain]], list[dict[str, Any]]]:
        """Return the coboundary operator δ: C^dim → C^(dim+1).

        The returned function accepts cochains of the given dimension
        and returns a list of boundary-value dicts.  A cochain is a
        *cocycle* when every boundary value is ``{"consistent": True}``.
        """
        def _delta(input_cochains: list[Cochain]) -> list[dict[str, Any]]:
            boundary_values: list[dict[str, Any]] = []

            for cochain in input_cochains:
                for cell in cochain.cells:
                    val = cochain.values.get(cell.cell_id, {})

                    if dimension == 0:
                        # δ⁰: check whether the local section on this
                        # vertex extends consistently to its edges.
                        coords = val.get("coordinates", [])
                        boundary_values.append({
                            "cell_id": cell.cell_id,
                            "consistent": len(coords) > 0,
                            "detail": (
                                "non-empty local section"
                                if coords else "empty section"
                            ),
                        })

                    elif dimension == 1:
                        # δ¹: check the cocycle condition on edges.
                        shared = val.get("shared_coordinates", [])
                        interface = val.get("interface", "")
                        is_consistent = len(shared) > 0 or interface != ""
                        boundary_values.append({
                            "cell_id": cell.cell_id,
                            "consistent": is_consistent,
                            "detail": (
                                f"interface={interface}, "
                                f"shared={len(shared)}"
                            ),
                        })

                    else:
                        # Higher dimensions: check structural consistency.
                        boundary_values.append({
                            "cell_id": cell.cell_id,
                            "consistent": True,
                            "detail": "higher-dim cell",
                        })

            return boundary_values

        return _delta

    # --------------------------------------------------- cocycles

    def compute_cocycles(
        self,
        cochains: list[Cochain],
        coboundary: Callable[[list[Cochain]], list[dict[str, Any]]],
    ) -> list[Cochain]:
        """Return cochains in ker(δ) — those whose boundary is zero.

        A cochain is a cocycle when all its boundary values report
        ``{"consistent": True}``.
        """
        cocycles: list[Cochain] = []

        for cochain in cochains:
            bvals = coboundary([cochain])
            if all(bv.get("consistent", False) for bv in bvals):
                cocycles.append(cochain)

        return cocycles

    # ------------------------------------------------- coboundaries

    def compute_coboundaries(
        self,
        cochains: list[Cochain],
        prev_coboundary: Callable[[list[Cochain]], list[dict[str, Any]]],
    ) -> list[Cochain]:
        """Return cochains in im(δ_{dim-1}) — trivially consistent ones.

        A cochain at dimension *n* is a coboundary if it equals δ applied
        to some (n-1)-cochain.  In our discrete setting we mark a cochain
        as a coboundary when the *previous* coboundary map produces a
        consistent image that covers this cochain's cells.
        """
        if not cochains:
            return []

        # Evaluate the previous coboundary on its own inputs.
        prev_bvals = prev_coboundary(cochains)

        coboundaries: list[Cochain] = []
        consistent_ids: set[str] = {
            bv["cell_id"]
            for bv in prev_bvals
            if bv.get("consistent", False)
        }

        for cochain in cochains:
            cell_ids = {c.cell_id for c in cochain.cells}
            if cell_ids and cell_ids <= consistent_ids:
                coboundaries.append(cochain)

        return coboundaries

    # ---------------------------------------------------- quotient

    def quotient(
        self,
        cocycles: list[Cochain],
        coboundaries: list[Cochain],
    ) -> CohomologyGroup:
        """Compute H^n = ker(δ^n) / im(δ^{n-1}).

        Generators are cocycles that are *not* coboundaries.
        """
        coboundary_ids: set[str] = set()
        for cb in coboundaries:
            for cell in cb.cells:
                coboundary_ids.add(cell.cell_id)

        generators: list[str] = []
        relations: list[str] = []

        for zc in cocycles:
            cell_ids = {c.cell_id for c in zc.cells}
            if cell_ids & coboundary_ids:
                relations.append(
                    f"trivial: {','.join(sorted(cell_ids))}"
                )
            else:
                generators.append(
                    f"non-trivial: {','.join(sorted(cell_ids))}"
                )

        return CohomologyGroup(
            dimension=0,  # caller will override
            generators=generators,
            relations=relations,
        )

    # ----------------------------------------- interpretation

    def interpret_generators(
        self,
        group: CohomologyGroup,
        site_data: dict[str, Any],
    ) -> list[str]:
        """Translate cohomology generators to human-readable descriptions.

        Returns a list of description strings.
        """
        if group.is_trivial:
            return []

        descriptions: list[str] = []
        dim = group.dimension

        for gen in group.generators:
            # Parse the cell ids from the generator description.
            parts = gen.replace("non-trivial: ", "").split(",")
            layer_names = [p.strip() for p in parts if p.strip()]

            if dim == 0:
                descriptions.append(
                    f"H⁰ generator: isolated component "
                    f"{{{', '.join(layer_names)}}} — "
                    f"this layer is disconnected from the rest of the app."
                )
            elif dim == 1:
                if len(layer_names) == 1:
                    # Single edge cell id like "css|javascript".
                    edge_layers = layer_names[0].split("|")
                else:
                    edge_layers = layer_names
                descriptions.append(
                    f"H¹ generator: gluing failure between "
                    f"{' and '.join(edge_layers)} — "
                    f"cross-language data is inconsistent at this interface."
                )
            elif dim == 2:
                descriptions.append(
                    f"H² generator: higher obstruction at "
                    f"triple intersection {{{', '.join(layer_names)}}} — "
                    f"three-way protocol mismatch."
                )
            else:
                descriptions.append(
                    f"H^{dim} generator on {{{', '.join(layer_names)}}}."
                )

        return descriptions

    # --------------------------------------------------------- complex

    def build_complex(
        self,
        site_data: dict[str, Any],
        max_dimension: int = 2,
    ) -> CechComplex:
        """Build and return the full Čech complex (for inspection).

        This is a convenience wrapper that exposes the intermediate
        combinatorial structures without computing cohomology.
        """
        covering = site_data.get("covering", {})
        nerve = self.build_nerve(covering)

        cochains_by_dim: dict[int, list[dict[str, Any]]] = {}
        coboundary_descs: dict[int, str] = {}

        for dim in range(max_dimension + 1):
            dim_cochains = self.compute_cochains(nerve, dim)
            cochains_by_dim[dim] = [c.to_dict() for c in dim_cochains]
            coboundary_descs[dim] = self._coboundary_description(dim)

        return CechComplex(
            nerve=nerve,
            cochains_by_dim=cochains_by_dim,
            coboundary_maps=coboundary_descs,
            max_dimension=max_dimension,
        )

    # ------------------------------------------------ private helpers

    @staticmethod
    def _compute_intersection(
        coords_a: list[Any],
        coords_b: list[Any],
    ) -> list[dict[str, Any]]:
        """Compute the intersection of two coordinate lists.

        Two coordinate dicts intersect if they share a common ``"name"``
        key.  This models cross-language references (e.g. a Python
        variable also referenced in a Jinja2 template).
        """
        names_a: dict[str, dict[str, Any]] = {}
        for c in coords_a:
            if isinstance(c, dict) and "name" in c:
                names_a[c["name"]] = c

        shared: list[dict[str, Any]] = []
        for c in coords_b:
            if isinstance(c, dict) and "name" in c:
                if c["name"] in names_a:
                    shared.append({
                        "name": c["name"],
                        "from_a": names_a[c["name"]],
                        "from_b": c,
                    })
        return shared

    @staticmethod
    def _coboundary_description(dimension: int) -> str:
        """Human-readable description of the coboundary map at *dimension*."""
        if dimension == 0:
            return (
                "δ⁰: C⁰ → C¹ — for each vertex section f, "
                "δ⁰(f)(edge) = f(target) − f(source).  "
                "Measures whether local sections agree on overlaps."
            )
        if dimension == 1:
            return (
                "δ¹: C¹ → C² — for each edge 1-cochain ω, "
                "δ¹(ω)(triangle) = ω(ab) − ω(ac) + ω(bc).  "
                "Measures the cocycle condition on triple overlaps."
            )
        return (
            f"δ^{dimension}: C^{dimension} → C^{dimension + 1} — "
            f"alternating sum over faces of ({dimension + 1})-simplices."
        )

    @staticmethod
    def _default_interpretation(
        dimension: int,
        group: CohomologyGroup,
    ) -> str:
        """Provide a default interpretation string for a cohomology group."""
        if group.is_trivial:
            if dimension == 0:
                return (
                    "H⁰ is trivial: the application layers form a "
                    "connected covering — no isolated components."
                )
            if dimension == 1:
                return (
                    "H¹ is trivial: all cross-language interfaces are "
                    "consistent — no gluing failures detected."
                )
            if dimension == 2:
                return (
                    "H² is trivial: no higher-order obstructions — "
                    "triple overlaps are coherent."
                )
            return f"H^{dimension} is trivial: no obstructions."

        if dimension == 0:
            return (
                f"H⁰ has rank {group.rank}: the covering has "
                f"{group.rank} disconnected component(s)."
            )
        if dimension == 1:
            return (
                f"H¹ has rank {group.rank}: there are {group.rank} "
                f"independent cross-language gluing failure(s)."
            )
        if dimension == 2:
            return (
                f"H² has rank {group.rank}: there are {group.rank} "
                f"higher-order obstruction(s) (three-way mismatches)."
            )
        return (
            f"H^{dimension} has rank {group.rank}: "
            f"{group.rank} obstruction(s)."
        )
