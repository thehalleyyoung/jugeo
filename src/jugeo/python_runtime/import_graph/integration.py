from __future__ import annotations

r"""theory2.tex Ch19 — Integration layer: Import graph analysis ↔ JuGeo framework.

This module is the **integration bridge** between the low-level import-graph
analysis tools (ImportGraphBuilder, CircularImportDetector, etc.) defined in
the rest of ``jugeo.python_runtime.import_graph`` and the higher-level JuGeo
framework objects — Sites, Judgments, Obstructions, Propositions — that live in
``jugeo.geometry`` and ``jugeo.judgments``.

Chapter reference
-----------------
theory2.tex Ch19 §19.2–§19.5 treats Python packages as *sites*, import edges as
*restriction morphisms*, and circular import chains as *cohomological
obstructions*.  The bridge established here converts the concrete Python
analysis data into those categorical objects so that the rest of the JuGeo
pipeline (evidence channels, solver sessions, proof-obligation trackers) can
reason uniformly about the import structure.

Key abstractions
----------------
* :class:`ImportsPackageFixedPointsBridge` — stateless bridge object that
  converts raw analysis artefacts into site objects, obstruction records, and
  judgment instances.  Every public method is a *functor* in the sense of
  theory2.tex §19.3: it maps morphisms of the analysis category to morphisms of
  the judgment category.

* :class:`ImportsPackageFixedPointsExportBundle` — mutable container that
  accumulates judgments, obstructions, and the constructed Site for a single
  analysis run.  Supports merge-and-reduce workflows for incremental
  re-analysis.

* :class:`CopilotImportAdvisor` — copilot-facing advisor that translates
  low-level analysis artefacts into human-readable suggestions.  The advisor is
  *not* a prover; it generates copilot-tier evidence (TrustLevel 1) that can
  later be promoted by the runtime or solver channels.

SMT2 encoding
-------------
§19.4 introduces an SMT2 encoding of the acyclicity property.  The key idea is
to assign an integer *rank* variable ``r_m`` to each module ``m`` and assert::

    for every import edge (m → n):   r_m < r_n

A satisfying assignment witnesses a topological ordering (DAG); unsatisfiability
witnesses a cycle.  The :meth:`ImportsPackageFixedPointsBridge.encode_import_graph_smt2`
method generates this encoding and
:meth:`~ImportsPackageFixedPointsBridge.run_z3_acyclicity_check` calls the
optional Z3 backend.

Theory alignment
----------------
* §19.2 — Site construction from module graphs
* §19.3 — Obstructions from cycles; the obstruction class is the first
  cohomology group H¹(X, 𝒪_X) of the import sheaf
* §19.4 — SMT2 rank encoding for acyclicity
* §19.5 — Fixed-point covering families and package topology
* §19.6 — Re-export consistency as a judgment proposition

Copilot annotation convention
------------------------------
Lines marked ``# copilot:`` carry inline annotations that are surfaced in the
copilot evidence channel.  They do NOT affect runtime semantics.
"""

import ast
import importlib
import importlib.util
import logging
import pkgutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jugeo geometry imports — try real package first, fall back to stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, Morphism, MorphismKind,
        Site, SiteBuilder, CoveringFamily, GrothendieckTopology, CoordinateObject,
    )
except ImportError:
    from dataclasses import dataclass as _dc, field as _field
    from enum import Enum

    class CoordinateKind(Enum):  # type: ignore[no-redef]
        MODULE = "module"; FUNCTION = "function"; INTERFACE = "interface"
        TEST = "test"; THEOREM = "theorem"; REGION = "region"

    class MorphismKind(Enum):  # type: ignore[no-redef]
        RESTRICTION = "restriction"; INCLUSION = "inclusion"
        TRANSPORT = "transport"; REFINEMENT = "refinement"

    @_dc(frozen=True)
    class Coordinate:  # type: ignore[no-redef]
        components: tuple = ()
        kind: "CoordinateKind" = CoordinateKind.MODULE
        support_labels: frozenset = frozenset()

    CoordinateObject = Coordinate

    @_dc(frozen=True)
    class Morphism:  # type: ignore[no-redef]
        source: "Coordinate" = None; target: "Coordinate" = None
        kind: "MorphismKind" = MorphismKind.INCLUSION; label: str = ""

    @_dc
    class CoveringFamily:  # type: ignore[no-redef]
        base: "Coordinate" = None; members: list = _field(default_factory=list)

    @_dc
    class GrothendieckTopology:  # type: ignore[no-redef]
        name: str = "custom"

    @_dc
    class Site:  # type: ignore[no-redef]
        label: str = ""
        _coords: list = _field(default_factory=list)
        _morphisms: list = _field(default_factory=list)

        def add_coordinate(self, c):
            self._coords.append(c); return self

        def add_morphism(self, m):
            self._morphisms.append(m); return self

        def objects(self):
            return list(self._coords)

        def morphisms_from(self, c):
            return [m for m in self._morphisms if getattr(m, "source", None) == c]

    @_dc
    class SiteBuilder:  # type: ignore[no-redef]
        _coords: list = _field(default_factory=list)
        _morphisms: list = _field(default_factory=list)

        def add_coordinate(self, c):
            self._coords.append(c); return self

        def add_morphism(self, m):
            self._morphisms.append(m); return self

        def build(self):
            return Site()

# ---------------------------------------------------------------------------
# Jugeo judgment imports — try real package first, fall back to stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentStatus, TrustLevel, Proposition, PropositionKind,
        Carrier, EvidenceBundle, EvidenceItem, EvidenceItemKind,
        ResidualObligation, Obstruction, TrustAnnotation, Provenance,
    )
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc, field as _field

    class JudgmentStatus(str, Enum):  # type: ignore[no-redef]
        PROPOSED = "proposed"; SETTLED = "settled"
        OBSTRUCTED = "obstructed"; OPEN = "open"

    class TrustLevel(int, Enum):  # type: ignore[no-redef]
        COPILOT_SUGGESTED = 1; ORACLE_PROPOSED = 2
        RUNTIME_WITNESSED = 3; VERIFIED = 4

    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; TEMPORAL = "temporal"
        INVARIANT = "invariant"; LIVENESS = "liveness"; SAFETY = "safety"

    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        STATIC_ANALYSIS = "static_analysis"; RUNTIME_TRACE = "runtime_trace"
        THEOREM_PROOF = "theorem_proof"; COPILOT_ANNOTATION = "copilot_annotation"

    @_dc(frozen=True)
    class Proposition:  # type: ignore[no-redef]
        kind: "PropositionKind" = PropositionKind.STRUCTURAL
        statement: str = ""; label: str = ""

    @_dc(frozen=True)
    class Carrier:  # type: ignore[no-redef]
        coordinate: object = None; payload: object = None; label: str = ""

    @_dc
    class EvidenceItem:  # type: ignore[no-redef]
        kind: "EvidenceItemKind" = EvidenceItemKind.STATIC_ANALYSIS
        payload: object = None; label: str = ""

    @_dc
    class EvidenceBundle:  # type: ignore[no-redef]
        items: list = _field(default_factory=list)

        def add(self, item):
            self.items.append(item); return self

    @_dc
    class TrustAnnotation:  # type: ignore[no-redef]
        level: "TrustLevel" = TrustLevel.COPILOT_SUGGESTED; rationale: str = ""

    @_dc
    class Provenance:  # type: ignore[no-redef]
        source: str = ""; module: str = ""; timestamp: str = ""

    @_dc
    class ResidualObligation:  # type: ignore[no-redef]
        description: str = ""; discharged: bool = False

    @_dc
    class Obstruction:  # type: ignore[no-redef]
        description: str = ""; coordinate: object = None

    @_dc
    class Judgment:  # type: ignore[no-redef]
        status: "JudgmentStatus" = JudgmentStatus.PROPOSED
        proposition: "Proposition" = None
        carrier: "Carrier" = None
        evidence: "EvidenceBundle" = _field(default_factory=EvidenceBundle)
        trust: "TrustAnnotation" = _field(default_factory=TrustAnnotation)
        provenance: "Provenance" = _field(default_factory=Provenance)
        obligations: list = _field(default_factory=list)
        label: str = ""

        def settle(self):
            self.status = JudgmentStatus.SETTLED; return self

        def obstruct(self, obs):
            self.status = JudgmentStatus.OBSTRUCTED; return self

# ---------------------------------------------------------------------------
# Jugeo solver imports — try real package first, fall back to stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import SolveOutcome, Z3Formula, Z3Session, z3_available
except ImportError:
    from enum import Enum
    from dataclasses import dataclass as _dc

    class SolveOutcome(str, Enum):  # type: ignore[no-redef]
        SAT = "sat"; UNSAT = "unsat"; UNKNOWN = "unknown"

    @_dc
    class Z3Formula:  # type: ignore[no-redef]
        smt2: str = ""; label: str = ""

    @_dc
    class Z3Session:  # type: ignore[no-redef]
        def check(self, formula):
            return SolveOutcome.UNKNOWN

        def add_assertion(self, formula):
            return self

    def z3_available() -> bool:
        return False


# ===========================================================================
# Bridge
# ===========================================================================

class ImportsPackageFixedPointsBridge:
    """Bridge between import-graph analysis and the JuGeo framework.

    Theory reference: theory2.tex Ch19 §19.2–§19.4.

    This class provides a collection of pure conversion methods that map
    concrete Python import-analysis artefacts (module names, dependency dicts,
    cycle lists, fixed-point records) to abstract JuGeo framework objects
    (Site, Morphism, Obstruction, Judgment).

    Design principles
    -----------------
    * **Stateless** — every method is a pure function of its inputs.  Bridge
      instances carry no mutable state; they can be freely shared across threads.
    * **Fail-soft** — conversion errors are logged and replaced with
      ``TrustLevel.COPILOT_SUGGESTED`` stubs rather than raising exceptions.
    * **Auditable** — every emitted Judgment records its Provenance so the
      evidence-chain is traceable back to this bridge method.

    Copilot note
    ------------
    # copilot: The bridge is the primary seam for injecting copilot-tier
    # copilot: evidence into the judgment layer.  Every ``emit_*`` method
    # copilot: stamps its output with TrustLevel.COPILOT_SUGGESTED unless the
    # copilot: caller explicitly supplies a higher trust level.
    """

    # ------------------------------------------------------------------ site

    def bridge_import_graph_to_site(
        self,
        module_graph: dict[str, list[str]],
    ) -> "Site":
        """Convert a module dependency graph to a JuGeo Site.

        Each module name becomes a :class:`Coordinate` with
        ``CoordinateKind.MODULE``.  Each directed import edge ``(importer →
        imported)`` becomes a restriction :class:`Morphism`.

        Theory reference: theory2.tex §19.2.1 — "The import presheaf".

        Parameters
        ----------
        module_graph:
            Mapping ``{module_name: [imported_module_name, ...]}`` as produced
            by :class:`~jugeo.python_runtime.import_graph.import_graph.ImportGraphBuilder`.

        Returns
        -------
        Site
            A JuGeo Site whose objects are module coordinates and whose
            morphisms are import restriction arrows.

        # copilot: Site construction is deterministic given the graph; caching
        # copilot: is safe if the caller guarantees graph immutability.
        """
        log.debug("bridge_import_graph_to_site: %d modules", len(module_graph))
        site = Site(label="import_graph_site")  # type: ignore[call-arg]
        coord_cache: dict[str, object] = {}

        def _get_coord(name: str) -> object:
            if name not in coord_cache:
                parts = tuple(name.split("."))
                try:
                    c = Coordinate(components=parts, kind=CoordinateKind.MODULE)
                except Exception:
                    # copilot: stub coordinate when real Coordinate is unavailable
                    c = type("_C", (), {"name": name, "components": parts})()
                coord_cache[name] = c
                try:
                    site.add_coordinate(c)
                except Exception as exc:
                    log.warning("add_coordinate failed for %r: %s", name, exc)
            return coord_cache[name]

        for importer, imports in module_graph.items():
            src = _get_coord(importer)
            for imported in imports:
                tgt = _get_coord(imported)
                try:
                    m = Morphism(
                        source=src,
                        target=tgt,
                        kind=MorphismKind.RESTRICTION,
                        label=f"{importer}→{imported}",
                    )
                    site.add_morphism(m)
                except Exception as exc:
                    log.warning("add_morphism failed %r→%r: %s", importer, imported, exc)

        log.info(
            "bridge_import_graph_to_site: site has %d objects",
            len(site.objects()),
        )
        return site

    # --------------------------------------------------------------- cycles

    def bridge_cycles_to_obstructions(self, cycles: list) -> list:
        """Convert cycle records to :class:`Obstruction` objects.

        Theory reference: theory2.tex §19.3.1 — "Cycles as cohomological
        obstructions."  A circular import chain is a witness for a non-trivial
        element of H¹(X, 𝒪_X) where X is the import site and 𝒪_X the
        structure sheaf of module namespaces.

        Parameters
        ----------
        cycles:
            List of cycle records.  Each record may be a plain list of module
            name strings or an object with a ``members`` attribute.

        Returns
        -------
        list[Obstruction]
            One Obstruction per detected cycle.

        # copilot: The description field is formatted for display in the
        # copilot: copilot evidence panel; keep it ≤120 characters.
        """
        obstructions = []
        for idx, cycle in enumerate(cycles):
            if isinstance(cycle, (list, tuple)):
                members = list(cycle)
            elif hasattr(cycle, "members"):
                members = list(cycle.members)
            else:
                members = [str(cycle)]
            description = f"Circular import cycle #{idx + 1}: {' → '.join(members)} → {members[0]}"
            log.debug("obstruction from cycle: %s", description)
            try:
                coord = Coordinate(
                    components=tuple(members[0].split(".")),
                    kind=CoordinateKind.MODULE,
                )
            except Exception:
                coord = None
            try:
                obs = Obstruction(description=description, coordinate=coord)
            except Exception:
                obs = type("_Obs", (), {"description": description, "coordinate": coord})()
            obstructions.append(obs)
        return obstructions

    # ---------------------------------------------------------- fixed points

    def bridge_fixed_point_to_covering(self, fixed_point: object) -> "CoveringFamily":
        """Convert a package fixed-point record to a CoveringFamily.

        Theory reference: theory2.tex §19.5 — "Package fixed points and their
        covering sieves."  The fixed point of the import-closure operator
        ``Cl(P)`` for a package P is the smallest set of modules closed under
        transitive imports.  This set forms a covering sieve in the
        Grothendieck topology on the import site.

        Parameters
        ----------
        fixed_point:
            A record with a ``root`` attribute (package root module name) and a
            ``members`` attribute (iterable of module names in the fixed point).
            Plain dicts with ``"root"`` / ``"members"`` keys are also accepted.

        Returns
        -------
        CoveringFamily
            A CoveringFamily whose base is the package root coordinate and
            whose members are the coordinates of the fixed-point modules.

        # copilot: Fixed-point covering families are the sheaf-theoretic
        # copilot: analogue of package ``__init__.py`` namespaces.
        """
        if isinstance(fixed_point, dict):
            root_name = fixed_point.get("root", "")
            member_names: list[str] = list(fixed_point.get("members", []))
        else:
            root_name = getattr(fixed_point, "root", "")
            member_names = list(getattr(fixed_point, "members", []))

        log.debug("bridge_fixed_point_to_covering: root=%r, %d members", root_name, len(member_names))

        try:
            base_coord = Coordinate(
                components=tuple(root_name.split(".")),
                kind=CoordinateKind.MODULE,
            )
        except Exception:
            base_coord = None  # type: ignore[assignment]

        member_coords = []
        for name in member_names:
            try:
                member_coords.append(
                    Coordinate(components=tuple(name.split(".")), kind=CoordinateKind.MODULE)
                )
            except Exception:
                member_coords.append(name)  # type: ignore[arg-type]

        try:
            return CoveringFamily(base=base_coord, members=member_coords)
        except Exception:
            cf = type("_CF", (), {"base": base_coord, "members": member_coords})()
            return cf  # type: ignore[return-value]

    # ------------------------------------------------------------ judgments

    def emit_import_judgment(
        self,
        module_name: str,
        imported_names: list[str],
        trust: "TrustLevel" = None,
    ) -> "Judgment":
        """Emit a judgment asserting that a module imports a set of names.

        Theory reference: theory2.tex §19.6.1 — "Import edge propositions."

        The proposition states: "Module *module_name* has static imports to
        *imported_names*."  The judgment status starts as PROPOSED and is
        promoted to SETTLED when runtime evidence confirms the imports.

        Parameters
        ----------
        module_name:
            The importing module's fully qualified name.
        imported_names:
            The list of imported module/name strings.
        trust:
            Override trust level; defaults to ``TrustLevel.COPILOT_SUGGESTED``.

        # copilot: Import judgments are the atomic unit of trust in the import
        # copilot: analysis pipeline.  Promote them to RUNTIME_WITNESSED after
        # copilot: a live import attempt succeeds.
        """
        if trust is None:
            try:
                trust = TrustLevel.COPILOT_SUGGESTED
            except Exception:
                trust = 1

        statement = (
            f"Module '{module_name}' imports: {', '.join(imported_names[:10])}"
            + (" …" if len(imported_names) > 10 else "")
        )
        log.debug("emit_import_judgment: %s", statement)

        try:
            prop = Proposition(
                kind=PropositionKind.STRUCTURAL,
                statement=statement,
                label=f"import_edges:{module_name}",
            )
            coord = Coordinate(
                components=tuple(module_name.split(".")),
                kind=CoordinateKind.MODULE,
            )
            carrier = Carrier(coordinate=coord, payload=imported_names, label=module_name)
            evidence = EvidenceBundle()
            evidence.add(
                EvidenceItem(
                    kind=EvidenceItemKind.STATIC_ANALYSIS,
                    payload={"module": module_name, "imports": imported_names},
                    label="ast_import_scan",
                )
            )
            ta = TrustAnnotation(level=trust, rationale="static AST scan via ImportGraphBuilder")
            prov = Provenance(
                source="jugeo.python_runtime.import_graph.integration",
                module=module_name,
                timestamp=str(int(time.time())),
            )
            j = Judgment(
                status=JudgmentStatus.PROPOSED,
                proposition=prop,
                carrier=carrier,
                evidence=evidence,
                trust=ta,
                provenance=prov,
                label=f"import_judgment:{module_name}",
            )
        except Exception as exc:
            log.warning("emit_import_judgment stub fallback: %s", exc)
            j = type("_J", (), {
                "status": "proposed",
                "label": f"import_judgment:{module_name}",
                "proposition": statement,
            })()
        return j  # type: ignore[return-value]

    def emit_cycle_judgment(
        self,
        cycle_members: list[str],
        severity: float = 1.0,
    ) -> "Judgment":
        """Emit a judgment asserting the presence of a circular import cycle.

        Theory reference: theory2.tex §19.3.2 — "Cycle judgments and residual
        obligations."  A cycle judgment is always OBSTRUCTED from the moment of
        emission because the obstruction is the cycle itself.  Residual
        obligations specify the refactoring work needed to eliminate the cycle.

        Parameters
        ----------
        cycle_members:
            Ordered list of module names forming the cycle.
        severity:
            Float in [0, 1]; 1.0 = maximally severe (hard import error at
            startup), <1 = soft cycle discovered only at runtime.

        # copilot: Cycle judgments should be surfaced immediately in the IDE
        # copilot: as error-level annotations when severity ≥ 0.9.
        """
        cycle_str = " → ".join(cycle_members) + f" → {cycle_members[0]}"
        statement = f"Circular import detected (severity={severity:.2f}): {cycle_str}"
        log.warning("emit_cycle_judgment: %s", cycle_str)

        try:
            prop = Proposition(
                kind=PropositionKind.SAFETY,
                statement=statement,
                label=f"cycle:{':'.join(cycle_members)}",
            )
            coord = Coordinate(
                components=tuple(cycle_members[0].split(".")),
                kind=CoordinateKind.MODULE,
            )
            carrier = Carrier(coordinate=coord, payload=cycle_members, label="cycle_root")
            obs = Obstruction(description=cycle_str, coordinate=coord)
            obligation = ResidualObligation(
                description=f"Resolve circular import: {cycle_str}",
                discharged=False,
            )
            evidence = EvidenceBundle()
            evidence.add(
                EvidenceItem(
                    kind=EvidenceItemKind.STATIC_ANALYSIS,
                    payload={"cycle": cycle_members, "severity": severity},
                    label="tarjan_scc",
                )
            )
            ta = TrustAnnotation(
                level=TrustLevel.COPILOT_SUGGESTED,
                rationale="Tarjan SCC via CircularImportDetector",
            )
            prov = Provenance(
                source="jugeo.python_runtime.import_graph.integration",
                module=cycle_members[0],
                timestamp=str(int(time.time())),
            )
            j = Judgment(
                status=JudgmentStatus.OBSTRUCTED,
                proposition=prop,
                carrier=carrier,
                evidence=evidence,
                trust=ta,
                provenance=prov,
                obligations=[obligation],
                label=f"cycle_judgment:{cycle_members[0]}",
            )
            j.obstruct(obs)
        except Exception as exc:
            log.warning("emit_cycle_judgment stub fallback: %s", exc)
            j = type("_J", (), {
                "status": "obstructed",
                "label": f"cycle_judgment:{cycle_members[0]}",
                "proposition": statement,
            })()
        return j  # type: ignore[return-value]

    def emit_reexport_judgment(
        self,
        source: str,
        target: str,
        names: list[str],
    ) -> "Judgment":
        """Emit a judgment asserting a re-export relationship.

        Theory reference: theory2.tex §19.6.3 — "Re-export morphisms and
        namespace transport."  A re-export from *source* to *target* is a
        transport morphism in the site category: it moves names from the
        source coordinate's stalk to the target coordinate's stalk.

        Parameters
        ----------
        source:
            Fully-qualified module name that originally defines the names.
        target:
            Fully-qualified module name that re-exports the names.
        names:
            List of names being re-exported.

        # copilot: Re-export judgments form the basis for the __all__ consistency
        # copilot: check (T19.3 in theorems.py).
        """
        statement = (
            f"Re-export: '{target}' exports {len(names)} name(s) originally from '{source}':"
            f" {', '.join(names[:8])}" + (" …" if len(names) > 8 else "")
        )
        log.debug("emit_reexport_judgment: %s → %s (%d names)", source, target, len(names))

        try:
            prop = Proposition(
                kind=PropositionKind.STRUCTURAL,
                statement=statement,
                label=f"reexport:{source}→{target}",
            )
            src_coord = Coordinate(
                components=tuple(source.split(".")), kind=CoordinateKind.MODULE
            )
            tgt_coord = Coordinate(
                components=tuple(target.split(".")), kind=CoordinateKind.MODULE
            )
            carrier = Carrier(coordinate=tgt_coord, payload={"source": source, "names": names}, label=target)
            evidence = EvidenceBundle()
            evidence.add(
                EvidenceItem(
                    kind=EvidenceItemKind.STATIC_ANALYSIS,
                    payload={"source": source, "target": target, "names": names},
                    label="reexport_scan",
                )
            )
            ta = TrustAnnotation(
                level=TrustLevel.COPILOT_SUGGESTED,
                rationale="__all__ static scan via ReExportAnalyzer",
            )
            prov = Provenance(
                source="jugeo.python_runtime.import_graph.integration",
                module=target,
                timestamp=str(int(time.time())),
            )
            j = Judgment(
                status=JudgmentStatus.PROPOSED,
                proposition=prop,
                carrier=carrier,
                evidence=evidence,
                trust=ta,
                provenance=prov,
                label=f"reexport_judgment:{source}→{target}",
            )
        except Exception as exc:
            log.warning("emit_reexport_judgment stub fallback: %s", exc)
            j = type("_J", (), {
                "status": "proposed",
                "label": f"reexport_judgment:{source}→{target}",
                "proposition": statement,
            })()
        return j  # type: ignore[return-value]

    # -------------------------------------------------------- support region

    def build_support_region(
        self,
        package_root: str,
        included_modules: list[str],
    ) -> object:
        """Build a support region for a package.

        Theory reference: theory2.tex §19.5.2 — "Support regions as sub-sites."
        A support region is a sub-site of the import site consisting of a
        designated root coordinate plus all included module coordinates.  It is
        the categorical image of the restriction functor to a package subtree.

        Parameters
        ----------
        package_root:
            Root module name (e.g. ``"jugeo.geometry"``).
        included_modules:
            List of fully-qualified module names in the region.

        Returns
        -------
        object
            A plain dataclass-like record with ``root``, ``members``, and
            ``site`` fields.

        # copilot: Support regions are used by the fixed-point theorem (T19.2)
        # copilot: to restrict the acyclicity check to a single package tree.
        """
        log.debug(
            "build_support_region: root=%r, %d modules", package_root, len(included_modules)
        )
        subgraph: dict[str, list[str]] = {
            m: [] for m in included_modules
        }
        site = self.bridge_import_graph_to_site(subgraph)

        region = type("SupportRegion", (), {
            "root": package_root,
            "members": included_modules,
            "site": site,
        })()
        return region

    # ----------------------------------------------------------------- SMT2

    def encode_import_graph_smt2(self, module_graph: dict[str, list[str]]) -> str:
        """Encode the import acyclicity property in SMT2 format.

        Theory reference: theory2.tex §19.4 — "SMT2 rank encoding for DAG
        verification."  The encoding assigns an integer rank variable ``r_M``
        to each module M and adds the constraint ``(< r_M r_N)`` for every
        import edge M → N.  Satisfiability witnesses a topological order; UNSAT
        witnesses a cycle.

        Parameters
        ----------
        module_graph:
            Mapping ``{importer: [imported, ...]}`` — the raw import graph.

        Returns
        -------
        str
            A self-contained SMT2 string ready for ``(check-sat)``.

        # copilot: The rank variables use the prefix "r_" followed by a
        # copilot: sanitized module name.  Dots are replaced with underscores.
        """
        modules: set[str] = set(module_graph.keys())
        for deps in module_graph.values():
            modules.update(deps)

        def _var(name: str) -> str:
            return "r_" + name.replace(".", "_").replace("-", "_")

        lines: list[str] = [
            "; SMT2 acyclicity encoding — theory2.tex Ch19 §19.4",
            "(set-logic QF_LIA)",
            "",
        ]
        # Declare rank variables
        for m in sorted(modules):
            lines.append(f"(declare-const {_var(m)} Int)")
        lines.append("")
        # Non-negativity constraints
        for m in sorted(modules):
            lines.append(f"(assert (>= {_var(m)} 0))")
        lines.append("")
        # Order constraints for each edge
        for importer, imports in sorted(module_graph.items()):
            for imported in imports:
                if importer != imported:
                    lines.append(
                        f"(assert (< {_var(importer)} {_var(imported)}))"
                        f"  ; {importer} → {imported}"
                    )
        lines.append("")
        lines.append("(check-sat)")
        lines.append("(get-model)")
        return "\n".join(lines)

    def run_z3_acyclicity_check(
        self,
        module_graph: dict[str, list[str]],
    ) -> tuple[bool, str]:
        """Run an acyclicity check via the Z3 session backend.

        Theory reference: theory2.tex §19.4.2 — "Automated acyclicity
        verification."  Uses :class:`~jugeo.solver.z3_session.Z3Session` when
        available; falls back to a pure-Python DFS cycle check otherwise.

        Parameters
        ----------
        module_graph:
            Raw import graph mapping.

        Returns
        -------
        tuple[bool, str]
            ``(is_acyclic, explanation)``  where *is_acyclic* is True when the
            graph is a DAG and *explanation* is a human-readable summary.

        # copilot: When Z3 is unavailable the fallback DFS is complete for
        # copilot: finite graphs but does not produce SMT witnesses.
        """
        log.debug("run_z3_acyclicity_check: %d modules", len(module_graph))

        if z3_available():
            smt2 = self.encode_import_graph_smt2(module_graph)
            session = Z3Session()
            formula = Z3Formula(smt2=smt2, label="import_acyclicity")
            outcome = session.check(formula)
            if outcome == SolveOutcome.SAT:
                return True, "Z3: graph is acyclic (SAT — topological order exists)"
            elif outcome == SolveOutcome.UNSAT:
                return False, "Z3: graph contains a cycle (UNSAT — no topological order)"
            else:
                return False, "Z3: result unknown; treating as potentially cyclic"

        # Fallback: iterative DFS
        visited: set[str] = set()
        in_stack: set[str] = set()
        cycle_path: list[str] = []

        def _dfs(node: str) -> bool:
            visited.add(node)
            in_stack.add(node)
            for neighbor in module_graph.get(node, []):
                if neighbor not in visited:
                    if _dfs(neighbor):
                        cycle_path.insert(0, neighbor)
                        return True
                elif neighbor in in_stack:
                    cycle_path.append(neighbor)
                    return True
            in_stack.discard(node)
            return False

        for module in module_graph:
            if module not in visited:
                if _dfs(module):
                    return False, f"Cycle detected (DFS): {cycle_path}"

        return True, "DFS: graph is acyclic"


# ===========================================================================
# Export bundle
# ===========================================================================

@dataclass
class ImportsPackageFixedPointsExportBundle:
    """Accumulator for all artefacts produced during one import-graph analysis run.

    Theory reference: theory2.tex Ch19 §19.7 — "Export bundles and incremental
    re-analysis."  An export bundle is the *output object* of a full analysis
    pass over a package tree.  It aggregates the constructed Site, all emitted
    Judgments, and all detected Obstructions into a single serialisable record.

    Fields
    ------
    module_graph:
        The raw module dependency graph that was analysed.
    cycles:
        List of cycle records produced by the SCC detector.
    fixed_point:
        The package fixed-point record (result of import-closure iteration).
    judgments:
        All :class:`Judgment` objects emitted during the analysis.
    site:
        The :class:`Site` constructed from the module graph.
    obstructions:
        All :class:`Obstruction` objects derived from detected cycles.

    # copilot: Export bundles are designed to be serialised to JSON and stored
    # copilot: in the JuGeo evidence store for offline analysis.
    """

    module_graph: dict[str, list[str]] = field(default_factory=dict)
    cycles: list = field(default_factory=list)
    fixed_point: object = None
    judgments: list = field(default_factory=list)
    site: object = None
    obstructions: list = field(default_factory=list)
    label: str = ""

    def add_judgment(self, j: object) -> None:
        """Append a judgment to the bundle.

        # copilot: Duplicate judgments (same label) are silently accepted;
        # copilot: deduplication is the caller's responsibility.
        """
        self.judgments.append(j)
        log.debug("add_judgment: %s", getattr(j, "label", repr(j)))

    def add_obstruction(self, o: object) -> None:
        """Append an obstruction to the bundle.

        # copilot: Obstructions are ordered by discovery time; the first
        # copilot: element is typically the most structurally critical cycle.
        """
        self.obstructions.append(o)
        log.debug("add_obstruction: %s", getattr(o, "description", repr(o)))

    def to_report(self) -> dict[str, Any]:
        """Serialise the bundle to a plain Python dict suitable for JSON export.

        Returns
        -------
        dict
            A JSON-serialisable representation of the bundle.  Complex objects
            are reduced to their string representations.

        # copilot: The report dict is the canonical output format for the
        # copilot: copilot import-analysis panel.
        """
        def _j_to_dict(j: object) -> dict:
            return {
                "label": getattr(j, "label", ""),
                "status": str(getattr(j, "status", "")),
                "proposition": str(getattr(j, "proposition", "")),
            }

        def _o_to_dict(o: object) -> dict:
            return {
                "description": getattr(o, "description", str(o)),
                "coordinate": str(getattr(o, "coordinate", "")),
            }

        return {
            "label": self.label,
            "module_count": len(self.module_graph),
            "module_graph": {k: list(v) for k, v in self.module_graph.items()},
            "cycle_count": len(self.cycles),
            "cycles": [list(c) if isinstance(c, (list, tuple)) else str(c) for c in self.cycles],
            "judgment_count": len(self.judgments),
            "judgments": [_j_to_dict(j) for j in self.judgments],
            "obstruction_count": len(self.obstructions),
            "obstructions": [_o_to_dict(o) for o in self.obstructions],
        }

    def summary(self) -> str:
        """Return a human-readable one-paragraph summary of the bundle.

        # copilot: Summary strings are surfaced in the copilot chat panel as
        # copilot: a quick-look import health report.
        """
        n_mod = len(self.module_graph)
        n_edge = sum(len(v) for v in self.module_graph.values())
        n_cycle = len(self.cycles)
        n_j = len(self.judgments)
        n_obs = len(self.obstructions)
        health = "✓ healthy" if n_cycle == 0 else f"✗ {n_cycle} cycle(s) detected"
        return (
            f"ImportBundle '{self.label}': {n_mod} modules, {n_edge} import edges, "
            f"{n_j} judgments, {n_obs} obstructions. Status: {health}."
        )

    def merge(
        self,
        other: "ImportsPackageFixedPointsExportBundle",
    ) -> "ImportsPackageFixedPointsExportBundle":
        """Merge another bundle into a new combined bundle.

        Theory reference: theory2.tex §19.7.2 — "Incremental bundle merging."
        Merging two bundles combines their module graphs, judgment lists, and
        obstruction lists.  The resulting site is *not* automatically
        reconstructed; call the bridge to regenerate it from the merged graph.

        Parameters
        ----------
        other:
            Another export bundle to merge with this one.

        Returns
        -------
        ImportsPackageFixedPointsExportBundle
            A new bundle containing the union of both bundles' data.

        # copilot: Merge is associative but not commutative: the label of the
        # copilot: left-hand bundle is retained.
        """
        merged_graph: dict[str, list[str]] = {**self.module_graph}
        for mod, deps in other.module_graph.items():
            if mod in merged_graph:
                merged_graph[mod] = list(set(merged_graph[mod]) | set(deps))
            else:
                merged_graph[mod] = list(deps)

        return ImportsPackageFixedPointsExportBundle(
            module_graph=merged_graph,
            cycles=self.cycles + other.cycles,
            fixed_point=self.fixed_point or other.fixed_point,
            judgments=self.judgments + other.judgments,
            site=None,  # caller must regenerate
            obstructions=self.obstructions + other.obstructions,
            label=self.label,
        )


# ===========================================================================
# Copilot advisor
# ===========================================================================

class CopilotImportAdvisor:
    """Copilot-facing advisor for import analysis results.

    Theory reference: theory2.tex Ch19 §19.8 — "Copilot evidence channel for
    import analysis."  The advisor translates low-level analysis artefacts into
    human-readable copilot suggestions.  All suggestions carry
    ``TrustLevel.COPILOT_SUGGESTED`` (level 1) and require promotion by the
    runtime or theorem-proof channels before being treated as verified.

    Design note
    -----------
    The advisor is intentionally *not* a prover.  It generates plausible
    refactoring suggestions based on structural patterns in the import graph,
    but it does not guarantee correctness.  Each suggestion includes a
    ``# copilot:`` rationale comment explaining the theory2.tex reference.

    # copilot: The advisor is the primary surface for IDE integrations.  Its
    # copilot: output strings are designed to fit in a single 120-char line.
    """

    def advise_on_cycles(self, cycles: list) -> list[str]:
        """Return human-readable suggestions for resolving circular imports.

        Theory reference: theory2.tex §19.3.3 — "Cycle elimination strategies."
        The three canonical strategies are: (1) extract shared state to a
        third module, (2) use lazy imports inside function bodies, (3) apply
        TYPE_CHECKING guards for typing-only dependencies.

        Parameters
        ----------
        cycles:
            List of cycle records (lists of module names or objects with
            ``members`` attribute).

        Returns
        -------
        list[str]
            One suggestion string per detected cycle.

        # copilot: Strategy (1) is preferred for data-model cycles; strategy
        # copilot: (2) is preferred for runtime-only dependency cycles.
        """
        suggestions = []
        for cycle in cycles:
            if isinstance(cycle, (list, tuple)):
                members = list(cycle)
            else:
                members = list(getattr(cycle, "members", [str(cycle)]))

            chain = " → ".join(members) + f" → {members[0]}"
            tip: str
            if len(members) == 2:
                # copilot: Two-module cycles are usually solvable by merging the modules.
                tip = (
                    f"Cycle {chain}: consider merging '{members[0]}' and '{members[1]}' "
                    f"or extracting shared state into a third module."
                )
            elif len(members) <= 4:
                # copilot: Small cycles often dissolve when a central shared-state module is extracted.
                tip = (
                    f"Cycle {chain}: extract the shared data model used by "
                    f"'{members[0]}' and '{members[-1]}' into a new base module."
                )
            else:
                # copilot: Large cycles are a sign of tightly-coupled package structure.
                tip = (
                    f"Cycle of length {len(members)} ({chain}): this suggests tightly "
                    f"coupled modules.  Consider restructuring into layers with strict "
                    f"import direction (data → logic → presentation)."
                )
            suggestions.append(tip)
        return suggestions

    def advise_on_star_imports(self, modules: list[str]) -> list[str]:
        """Return suggestions for modules that use star imports.

        Theory reference: theory2.tex §19.6.4 — "Star import determinism and
        namespace pollution."  Star imports without ``__all__`` definitions are
        non-deterministic in the presence of monkey-patching and violate the
        namespace disjointness theorem T19.5.

        Parameters
        ----------
        modules:
            List of module names that contain ``from X import *`` statements.

        Returns
        -------
        list[str]
            One suggestion string per module.

        # copilot: Always recommend __all__ definition before a star import
        # copilot: because it makes the namespace contribution deterministic.
        """
        suggestions = []
        for module in modules:
            suggestions.append(
                f"Module '{module}' uses star imports: define '__all__' in every "
                f"imported module to make the namespace contribution explicit and "
                f"deterministic (theory2.tex T19.4)."
            )
        return suggestions

    def advise_on_dynamic_imports(self, records: list) -> list[str]:
        """Return suggestions for dynamic import usage patterns.

        Theory reference: theory2.tex §19.6.5 — "Dynamic import reachability
        and the importlib bridge."  Dynamic imports via ``importlib.import_module``
        are invisible to static analysis; each should be documented with a
        ``# copilot: dynamic-import`` annotation so the evidence channel can
        track reachability.

        Parameters
        ----------
        records:
            List of dynamic-import records.  Each may be a dict with
            ``"module"`` and ``"call_site"`` keys or an object with those attrs.

        Returns
        -------
        list[str]
            One suggestion string per dynamic import record.

        # copilot: Dynamic imports that cannot be statically resolved lower
        # copilot: the trust level of any downstream judgment that depends on them.
        """
        suggestions = []
        for rec in records:
            if isinstance(rec, dict):
                mod = rec.get("module", "<unknown>")
                call_site = rec.get("call_site", "<unknown>")
            else:
                mod = getattr(rec, "module", "<unknown>")
                call_site = getattr(rec, "call_site", "<unknown>")
            suggestions.append(
                f"Dynamic import of '{mod}' at '{call_site}': add a "
                f"'# copilot: dynamic-import reason=<reason>' comment so the "
                f"reachability judgment (T19.6) can be promoted to RUNTIME_WITNESSED."
            )
        return suggestions

    def generate_import_report(
        self,
        bundle: "ImportsPackageFixedPointsExportBundle",
    ) -> str:
        """Generate a multi-section human-readable import health report.

        Parameters
        ----------
        bundle:
            The export bundle to report on.

        Returns
        -------
        str
            A formatted multi-line report string.

        # copilot: This report is the primary artefact for the copilot
        # copilot: import-analysis PR comment workflow.
        """
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("  JuGeo Import Analysis Report")
        lines.append(f"  Bundle: {bundle.label or '(unnamed)'}")
        lines.append("=" * 72)
        lines.append("")
        lines.append(bundle.summary())
        lines.append("")

        # Cycle section
        if bundle.cycles:
            lines.append(f"## Cycles ({len(bundle.cycles)} detected)")
            for suggestion in self.advise_on_cycles(bundle.cycles):
                lines.append(f"  • {suggestion}")
            lines.append("")

        # Obstruction section
        if bundle.obstructions:
            lines.append(f"## Obstructions ({len(bundle.obstructions)})")
            for obs in bundle.obstructions:
                desc = getattr(obs, "description", str(obs))
                lines.append(f"  ✗ {desc}")
            lines.append("")

        # Judgment section
        if bundle.judgments:
            settled = [j for j in bundle.judgments if str(getattr(j, "status", "")) in ("settled", "JudgmentStatus.SETTLED")]
            obstructed = [j for j in bundle.judgments if str(getattr(j, "status", "")) in ("obstructed", "JudgmentStatus.OBSTRUCTED")]
            proposed = [j for j in bundle.judgments if str(getattr(j, "status", "")) in ("proposed", "JudgmentStatus.PROPOSED")]
            lines.append(f"## Judgments ({len(bundle.judgments)} total)")
            lines.append(f"  settled={len(settled)}  obstructed={len(obstructed)}  proposed={len(proposed)}")
            lines.append("")

        lines.append("=" * 72)
        return "\n".join(lines)


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    log.info("integration.py smoke test — theory2.tex Ch19")

    # Build a small sample module graph with one cycle
    sample_graph: dict[str, list[str]] = {
        "pkg.core": ["pkg.utils", "pkg.models"],
        "pkg.utils": ["pkg.models"],
        "pkg.models": ["pkg.core"],  # cycle: core → models → core
        "pkg.api": ["pkg.core", "pkg.utils"],
        "pkg.cli": ["pkg.api"],
    }

    bridge = ImportsPackageFixedPointsBridge()

    # Convert to site
    site = bridge.bridge_import_graph_to_site(sample_graph)
    print(f"Site objects: {len(site.objects())}")

    # Detect cycle (manual for smoke test)
    cycles = [["pkg.core", "pkg.models"]]
    obstructions = bridge.bridge_cycles_to_obstructions(cycles)
    print(f"Obstructions: {len(obstructions)} — {obstructions[0].description}")  # type: ignore[attr-defined]

    # Fixed-point covering
    fp = {"root": "pkg", "members": list(sample_graph.keys())}
    covering = bridge.bridge_fixed_point_to_covering(fp)
    print(f"Covering family base: {covering.base}")  # type: ignore[attr-defined]

    # Emit judgments
    j1 = bridge.emit_import_judgment("pkg.core", ["pkg.utils", "pkg.models"])
    j2 = bridge.emit_cycle_judgment(["pkg.core", "pkg.models"], severity=0.95)
    j3 = bridge.emit_reexport_judgment("pkg.utils", "pkg.api", ["helper", "fmt"])

    # Build bundle
    bundle = ImportsPackageFixedPointsExportBundle(
        module_graph=sample_graph,
        cycles=cycles,
        site=site,
        obstructions=obstructions,
        label="smoke_test_bundle",
    )
    bundle.add_judgment(j1)
    bundle.add_judgment(j2)
    bundle.add_judgment(j3)

    # SMT2 encoding
    smt2 = bridge.encode_import_graph_smt2(sample_graph)
    print(f"SMT2 lines: {smt2.count(chr(10))}")

    # Acyclicity check (should detect cycle)
    is_acyclic, explanation = bridge.run_z3_acyclicity_check(sample_graph)
    print(f"Acyclic: {is_acyclic} — {explanation}")

    # Advisor
    advisor = CopilotImportAdvisor()
    report = advisor.generate_import_report(bundle)
    print(report)

    # to_report
    rep = bundle.to_report()
    print(f"Report keys: {list(rep.keys())}")

    # Merge test
    bundle2 = ImportsPackageFixedPointsExportBundle(
        module_graph={"pkg.extra": ["pkg.core"]},
        label="extra_bundle",
    )
    merged = bundle.merge(bundle2)
    print(f"Merged modules: {len(merged.module_graph)}")

    log.info("Smoke test passed.")
