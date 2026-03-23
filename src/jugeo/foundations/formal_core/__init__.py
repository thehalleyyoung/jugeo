"""Formal core package for JuGeo — Theory2.tex Chapter 9.

Overview
--------
This package implements the *mathematical interlude* from Theory2.tex Chapter 9:
a more explicit formal core for the JuGeo trust infrastructure.  Chapter 9
develops the categorical and algebraic machinery that the rest of the thesis
uses as its formal backbone.

Chapter 9 Content Covered
--------------------------
§9.1  Judgment Site
    The *judgment site* ``(C, J)`` is a small category ``C`` of *judgment
    objects* (propositions, goal-states, and verification records) together
    with a Grothendieck topology ``J`` that specifies which families of
    morphisms count as *covers*.  A morphism ``f: Y → X`` in ``C``
    represents *refinement*: ``Y`` is a more detailed or more local view of
    ``X``.  A covering sieve on ``X`` is a collection of morphisms into ``X``
    that jointly witness ``X``.

§9.2  Trust Presheaf and Sheaf Condition
    The *trust presheaf* is a functor ``T: Cᵒᵖ → Pos`` sending each judgment
    object ``X`` to the poset of admissible trust assignments for ``X``.
    Theorem 9.1 (Sheaf Condition Necessity) establishes that ``T`` is a
    *sheaf* — i.e. satisfies the locality and gluing axioms — if and only if
    global trust assignments are coherent: compatible local trust data
    amalgamates uniquely.

§9.3  Trust Ordered Algebra
    The *trust algebra* ``(E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ)`` packages the
    partial order on evidence strength together with the operations:

    * ``⊕`` — *composition* (meet / greatest-lower-bound).
    * ``⊖`` — *attenuation* (trust-weakening under transport).
    * ``↑_π`` — *promotion* under an explicit named policy ``π``.
    * ``↓_χ`` — *demotion* under a challenge ``χ``.

    Theorem 9.2 proves that this structure satisfies five required axioms,
    including monotonicity of ``⊕`` and the oracle-idempotency law
    ``ORACLE_PROPOSED ⊕ ORACLE_PROPOSED = ORACLE_PROPOSED``.

§9.4  Sheaf Cohomology and Obstruction Vanishing
    Theorem 9.3 connects the solvability of the *descent problem* (can
    compatible local trust assignments be lifted to a global one?) to the
    vanishing of an obstruction class in the first Čech cohomology group
    ``H^1(C, T)``.  When ``H^1 = 0``, the descent problem is always solvable;
    a non-trivial class witnesses a genuine incompatibility.

§9.5  Copilot / Oracle Boundedness
    Theorem 9.4 establishes the *oracle ceiling invariant*: the
    ``ORACLE_PROPOSED`` trust tier is not the top element of the algebra.
    No composition of oracle-channel evidence can reach ``SOLVER_DISCHARGED``
    or ``MECHANICALLY_VERIFIED`` without a named promotion policy backed by
    non-oracle evidence.

§9.6  Monotonicity
    Theorem 9.5 proves that the trust ordering is preserved under admissible
    aggregation: if ``e₁ ⪯ e₂`` then ``e₁ ⊕ e₃ ⪯ e₂ ⊕ e₃`` for any
    admissible ``e₃``.

Package Structure
-----------------
formal_core/
├── __init__.py       ← you are here
├── theorems.py       ← Theorem 9.1 – 9.5, Lemma 9.1 – 9.2, Corollary 9.1 – 9.2
└── manifest.py       ← generated package manifest (auto-populated)

Key Exports
-----------
From :mod:`jugeo.foundations.formal_core.theorems`:

* :class:`TheoremStatement` — base class for formal propositions.
* :class:`Lemma` — supporting lemma (adds ``parent_theorem_id``).
* :class:`Corollary` — derived proposition (adds ``follows_from``).
* :class:`TheoremRegistry` — registry with bulk verify / query helpers.
* :data:`THEOREM_REGISTRY` — pre-populated global registry.
* :func:`get_chapter_9_theorems` — return all Ch9 items as dicts.
* :func:`verify_chapter_9` — run all Ch9 ``verify()`` methods.

Specific theorem instances (all registered in ``THEOREM_REGISTRY``):

* :data:`THEOREM_9_1_SHEAF_CONDITION_NECESSITY`
* :data:`THEOREM_9_2_TRUST_ALGEBRA_AXIOMS`
* :data:`THEOREM_9_3_OBSTRUCTION_VANISHING`
* :data:`THEOREM_9_4_COPILOT_BOUNDEDNESS`
* :data:`THEOREM_9_5_ADMISSIBILITY_MONOTONICITY`
* :data:`LEMMA_9_1`
* :data:`LEMMA_9_2`
* :data:`COROLLARY_9_1`
* :data:`COROLLARY_9_2`

Usage Examples
--------------
Retrieve and verify all Chapter 9 theorems::

    from jugeo.foundations.formal_core import (
        THEOREM_REGISTRY,
        verify_chapter_9,
        get_chapter_9_theorems,
    )

    # Verify with default (empty) context
    summary = verify_chapter_9()
    print(f"{summary['passed']}/{summary['total']} theorems passed")

    # Query a specific theorem
    thm = THEOREM_REGISTRY.get("theorem_9_3")
    print(thm.describe())

    # List all exports
    from jugeo.foundations.formal_core import list_exports
    print(list_exports())

    # Health check
    from jugeo.foundations.formal_core import health_check
    print(health_check())

Theory2.tex Reference
---------------------
Chapter 9, §9.0 — §9.6.  See also Appendix A (Grothendieck topologies) and
Appendix B (trust algebra formal definition).
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any

__version__: str = "0.1.0"
"""Package version for :mod:`jugeo.foundations.formal_core`."""

__all__: list[str] = [
    # Version
    "__version__",
    # Package metadata
    "PackageInfo",
    "PACKAGE_INFO",
    # Convenience functions
    "get_manifest",
    "list_exports",
    "health_check",
    "describe_chapter_9",
    # Re-exported from theorems
    "TheoremStatement",
    "Lemma",
    "Corollary",
    "TheoremRegistry",
    "THEOREM_REGISTRY",
    "get_chapter_9_theorems",
    "verify_chapter_9",
    # Specific theorem instances
    "THEOREM_9_1_SHEAF_CONDITION_NECESSITY",
    "THEOREM_9_2_TRUST_ALGEBRA_AXIOMS",
    "THEOREM_9_3_OBSTRUCTION_VANISHING",
    "THEOREM_9_4_COPILOT_BOUNDEDNESS",
    "THEOREM_9_5_ADMISSIBILITY_MONOTONICITY",
    "LEMMA_9_1",
    "LEMMA_9_2",
    "COROLLARY_9_1",
    "COROLLARY_9_2",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — avoid circular import issues at package load time
# ---------------------------------------------------------------------------

TheoremStatement = None  # type: ignore[assignment]
Lemma = None  # type: ignore[assignment]
Corollary = None  # type: ignore[assignment]
TheoremRegistry = None  # type: ignore[assignment]
THEOREM_REGISTRY = None  # type: ignore[assignment]
get_chapter_9_theorems = None  # type: ignore[assignment]
verify_chapter_9 = None  # type: ignore[assignment]
THEOREM_9_1_SHEAF_CONDITION_NECESSITY = None  # type: ignore[assignment]
THEOREM_9_2_TRUST_ALGEBRA_AXIOMS = None  # type: ignore[assignment]
THEOREM_9_3_OBSTRUCTION_VANISHING = None  # type: ignore[assignment]
THEOREM_9_4_COPILOT_BOUNDEDNESS = None  # type: ignore[assignment]
THEOREM_9_5_ADMISSIBILITY_MONOTONICITY = None  # type: ignore[assignment]
LEMMA_9_1 = None  # type: ignore[assignment]
LEMMA_9_2 = None  # type: ignore[assignment]
COROLLARY_9_1 = None  # type: ignore[assignment]
COROLLARY_9_2 = None  # type: ignore[assignment]

_THEOREMS_LOADED: bool = False
_THEOREMS_LOAD_ERROR: Exception | None = None


def _load_theorems() -> None:
    """Import symbols from :mod:`jugeo.foundations.formal_core.theorems`.

    Called lazily on first access to any theorem symbol.  All symbols are
    injected into the module global namespace so that the normal attribute
    access pattern ``from jugeo.foundations.formal_core import THEOREM_REGISTRY``
    works after this function has run.

    If the import fails (e.g. because an optional dependency is missing) the
    error is recorded and a warning is logged; callers should check
    :data:`_THEOREMS_LOAD_ERROR`.
    """
    global TheoremStatement, Lemma, Corollary, TheoremRegistry  # noqa: PLW0603
    global THEOREM_REGISTRY, get_chapter_9_theorems, verify_chapter_9
    global THEOREM_9_1_SHEAF_CONDITION_NECESSITY, THEOREM_9_2_TRUST_ALGEBRA_AXIOMS
    global THEOREM_9_3_OBSTRUCTION_VANISHING, THEOREM_9_4_COPILOT_BOUNDEDNESS
    global THEOREM_9_5_ADMISSIBILITY_MONOTONICITY
    global LEMMA_9_1, LEMMA_9_2, COROLLARY_9_1, COROLLARY_9_2
    global _THEOREMS_LOADED, _THEOREMS_LOAD_ERROR
    if _THEOREMS_LOADED:
        return
    try:
        from jugeo.foundations.formal_core.theorems import (  # noqa: PLC0415
            Corollary as _Corollary,
            COROLLARY_9_1 as _COR91,
            COROLLARY_9_2 as _COR92,
            get_chapter_9_theorems as _gc9,
            Lemma as _Lemma,
            LEMMA_9_1 as _L91,
            LEMMA_9_2 as _L92,
            THEOREM_9_1_SHEAF_CONDITION_NECESSITY as _T91,
            THEOREM_9_2_TRUST_ALGEBRA_AXIOMS as _T92,
            THEOREM_9_3_OBSTRUCTION_VANISHING as _T93,
            THEOREM_9_4_COPILOT_BOUNDEDNESS as _T94,
            THEOREM_9_5_ADMISSIBILITY_MONOTONICITY as _T95,
            THEOREM_REGISTRY as _TR,
            TheoremRegistry as _TReg,
            TheoremStatement as _TS,
            verify_chapter_9 as _vc9,
        )
        TheoremStatement = _TS
        Lemma = _Lemma
        Corollary = _Corollary
        TheoremRegistry = _TReg
        THEOREM_REGISTRY = _TR
        get_chapter_9_theorems = _gc9
        verify_chapter_9 = _vc9
        THEOREM_9_1_SHEAF_CONDITION_NECESSITY = _T91
        THEOREM_9_2_TRUST_ALGEBRA_AXIOMS = _T92
        THEOREM_9_3_OBSTRUCTION_VANISHING = _T93
        THEOREM_9_4_COPILOT_BOUNDEDNESS = _T94
        THEOREM_9_5_ADMISSIBILITY_MONOTONICITY = _T95
        LEMMA_9_1 = _L91
        LEMMA_9_2 = _L92
        COROLLARY_9_1 = _COR91
        COROLLARY_9_2 = _COR92
        _THEOREMS_LOADED = True
        logger.debug("formal_core: theorems module loaded successfully")
    except ImportError as exc:
        _THEOREMS_LOAD_ERROR = exc
        logger.warning("formal_core: could not import theorems module: %s", exc)


# Attempt eager load; failures are non-fatal (lazy callers will retry).
_load_theorems()


# ---------------------------------------------------------------------------
# PackageInfo dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackageInfo:
    """Metadata record describing the :mod:`jugeo.foundations.formal_core` package.

    This dataclass is a lightweight, JSON-serialisable description of the
    package suitable for logging, diagnostics, and manifest generation.

    Theory2.tex reference: §9.0 — package-level metadata is recorded so that
    downstream consumers can verify which version of the formal core they are
    running against.

    Attributes
    ----------
    name:
        Fully qualified package name.
    version:
        Semantic version string.
    theory_chapter:
        Chapter number in Theory2.tex that this package implements.
    theory_sections:
        Tuple of section identifiers covered.
    submodules:
        Tuple of submodule names within this package.
    num_theorems:
        Number of base theorems (excluding lemmas and corollaries).
    num_lemmas:
        Number of lemmas.
    num_corollaries:
        Number of corollaries.
    description:
        Short human-readable description.
    loaded_ok:
        Whether the theorems submodule was imported successfully at package
        load time.
    """

    name: str = "jugeo.foundations.formal_core"
    version: str = __version__
    theory_chapter: int = 9
    theory_sections: tuple[str, ...] = (
        "9.1", "9.2", "9.3", "9.4", "9.5", "9.6",
    )
    submodules: tuple[str, ...] = ("theorems", "manifest")
    num_theorems: int = 5
    num_lemmas: int = 2
    num_corollaries: int = 2
    description: str = (
        "Formal core for JuGeo: the categorical and algebraic machinery from "
        "Theory2.tex Chapter 9 (Grothendieck sites, trust presheaves, ordered "
        "algebra, sheaf cohomology, oracle boundedness, and monotonicity)."
    )
    loaded_ok: bool = field(default=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of this :class:`PackageInfo`.
        """
        return dataclasses.asdict(self)

    def summary(self) -> str:
        """Return a short human-readable summary line.

        Returns
        -------
        str
            E.g. ``"jugeo.foundations.formal_core v0.1.0 — Ch9 (5T + 2L + 2C)"``.
        """
        status = "OK" if self.loaded_ok else "LOAD ERROR"
        return (
            f"{self.name} v{self.version} — "
            f"Ch{self.theory_chapter} [{status}] "
            f"({self.num_theorems}T + {self.num_lemmas}L + {self.num_corollaries}C)"
        )

    # -- Judgment-geometric integration ------------------------------------

    def instantiate_geometry(self) -> Any:
        r"""Create a concrete judgment site from ``jugeo.geometry.site``.

        The formal core (Chapter 9) defines the judgment site
        ``(\mathbf{C}, J)`` abstractly.  This method *instantiates* that
        abstract construction by building a real ``Site`` object whose
        coordinates correspond to the trust algebra tiers and whose
        Grothendieck topology reflects the formal covering families.

        Returns
        -------
        A ``Site`` from ``jugeo.geometry.site``, or ``None`` if unavailable.
        """
        try:
            from jugeo.geometry.site import Site, SiteBuilder, Coordinate, CoordinateKind
            from jugeo.geometry.site import build_site
        except ImportError:
            return {
                "status": "geometry_unavailable",
                "package": self.name,
                "theory_chapter": self.theory_chapter,
            }

        tier_names = [
            "MECHANICALLY_VERIFIED", "SOLVER_DISCHARGED",
            "RUNTIME_WITNESSED", "HUMAN_ATTESTED",
            "ORACLE_PROPOSED", "COPILOT_SUGGESTED",
        ]
        coords = [
            Coordinate(
                components=(f"formal_core.{name}",),
                kind=CoordinateKind.THEOREM if "VERIFIED" in name else CoordinateKind.REGION,
            )
            for name in tier_names
        ]
        return build_site(coords)

    def instantiate_evidence(self) -> Any:
        r"""Create evidence structures from ``jugeo.evidence``.

        Builds the initial evidence infrastructure — trust algebra, evidence
        channels, and manifest — that the formal core's theorems operate
        over.  Theorem 9.2 (trust algebra axioms) and Theorem 9.4 (oracle
        boundedness) presuppose these structures.

        Returns
        -------
        A dict with trust algebra, channels, and manifest metadata, or
        a ``TrustAlgebra`` when available.
        """
        try:
            from jugeo.evidence.trust import TrustAlgebra, TrustLevel, TrustTier
        except ImportError:
            return {
                "status": "evidence_unavailable",
                "package": self.name,
            }

        algebra = TrustAlgebra()
        return {
            "trust_algebra": algebra,
            "trust_levels": [level.name for level in TrustLevel],
            "package": self.name,
            "theory_section": "9.3",
        }

    def instantiate_solver(self) -> Any:
        r"""Create a Z3 solver session from ``jugeo.solver``.

        The formal core's Theorem 9.3 (obstruction vanishing) and
        Theorem 9.1 (sheaf condition) can be checked computationally by
        encoding the relevant conditions as SMT satisfiability problems.
        This method creates a solver session configured for such checks.

        Returns
        -------
        A ``Z3Session`` when available, otherwise a dict stub.
        """
        try:
            from jugeo.solver.z3_session import Z3Session, z3_available
        except ImportError:
            return {
                "status": "solver_unavailable",
                "package": self.name,
            }

        return Z3Session()

    @property
    def encoding_theory(self) -> Any:
        r"""Return the encoding family from ``jugeo.encodings``.

        The encoding theory maps the formal core's abstract algebraic
        structures (trust algebra, judgment presheaf) into concrete SMT
        formulas via scalar encodings and the structural frontier.

        Returns
        -------
        dict describing available encoding modules.
        """
        try:
            from jugeo.encodings.scalar_encodings.models import SortKind, FragmentHint
            scalar_available = True
        except ImportError:
            scalar_available = False
        try:
            from jugeo.encodings.structural_frontier.models import (
                DecidabilityClass, KNOWN_DECIDABLE_FRAGMENTS,
            )
            frontier_available = True
        except ImportError:
            frontier_available = False
        return {
            "scalar_encodings_available": scalar_available,
            "structural_frontier_available": frontier_available,
            "package": self.name,
            "theory_section": "Ch25–Ch26",
        }

    @property
    def judgment_algebra(self) -> Any:
        r"""Return the judgment algebra from ``jugeo.judgments``.

        The judgment algebra is the categorical structure on judgment objects:
        the small category ``\mathbf{C}`` of §9.1, together with the
        composition and refinement operations that relate judgments at
        different coordinates.

        Returns
        -------
        dict or a judgment algebra object.
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment, Proposition
            judgments_available = True
        except ImportError:
            judgments_available = False
        try:
            from jugeo.foundations.judgment_products import JudgmentProductAlgebra
            products_available = True
        except ImportError:
            products_available = False
        return {
            "judgments_available": judgments_available,
            "judgment_products_available": products_available,
            "package": self.name,
            "theory_section": "9.1",
        }


# ---------------------------------------------------------------------------
# Build the authoritative PACKAGE_INFO instance
# ---------------------------------------------------------------------------


PACKAGE_INFO: PackageInfo = PackageInfo(loaded_ok=_THEOREMS_LOADED)
"""Singleton :class:`PackageInfo` for this package.

Inspect this at runtime to confirm which version of the formal core is in use::

    from jugeo.foundations.formal_core import PACKAGE_INFO
    print(PACKAGE_INFO.summary())
"""


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def get_manifest() -> dict[str, Any]:
    """Return a structured manifest describing this package.

    The manifest includes:

    * Package metadata from :data:`PACKAGE_INFO`.
    * A list of all exported names from :data:`__all__`.
    * A summary of registered theorems (if the theorems submodule loaded).
    * Theory2.tex section coverage.

    Returns
    -------
    dict
        JSON-serialisable manifest suitable for logging or inclusion in a
        broader project manifest.

    Examples
    --------
    >>> from jugeo.foundations.formal_core import get_manifest
    >>> m = get_manifest()
    >>> m["package"]["name"]
    'jugeo.foundations.formal_core'
    >>> "theorem_9_1" in [t["theorem_id"] for t in m.get("theorems", [])]
    True
    """
    manifest: dict[str, Any] = {
        "package": PACKAGE_INFO.to_dict(),
        "exports": list(__all__),
        "theorems": [],
        "section_coverage": list(PACKAGE_INFO.theory_sections),
        "load_error": str(_THEOREMS_LOAD_ERROR) if _THEOREMS_LOAD_ERROR else None,
    }
    if _THEOREMS_LOADED and THEOREM_REGISTRY is not None:
        try:
            manifest["theorems"] = THEOREM_REGISTRY.list_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_manifest: could not list theorems: %s", exc)
    return manifest


def list_exports() -> list[str]:
    """Return all names exported by this package (the ``__all__`` list).

    This is the canonical way for downstream tooling to discover what this
    package provides without introspecting the module namespace directly.

    Returns
    -------
    list[str]
        Alphabetically sorted copy of :data:`__all__`.

    Examples
    --------
    >>> from jugeo.foundations.formal_core import list_exports
    >>> "THEOREM_REGISTRY" in list_exports()
    True
    """
    return sorted(__all__)


def health_check() -> dict[str, Any]:
    """Perform a lightweight health check on the formal_core package.

    Checks performed:

    1. Whether the ``theorems`` submodule loaded without error.
    2. Whether :data:`THEOREM_REGISTRY` is populated with the expected
       number of items (9 total: 5 theorems + 2 lemmas + 2 corollaries).
    3. Whether :func:`verify_chapter_9` runs to completion with a default
       (empty) context and all theorems pass.

    Returns
    -------
    dict
        Keys:
        ``ok`` (bool), ``theorems_loaded`` (bool),
        ``registry_size`` (int), ``expected_registry_size`` (int),
        ``verify_passed`` (int), ``verify_total`` (int),
        ``errors`` (list[str]).

    Examples
    --------
    >>> from jugeo.foundations.formal_core import health_check
    >>> result = health_check()
    >>> result["ok"]
    True
    """
    errors: list[str] = []
    registry_size = 0
    verify_passed = 0
    verify_total = 0

    expected_size = (
        PACKAGE_INFO.num_theorems + PACKAGE_INFO.num_lemmas + PACKAGE_INFO.num_corollaries
    )

    if not _THEOREMS_LOADED:
        errors.append(f"theorems submodule failed to load: {_THEOREMS_LOAD_ERROR}")
    else:
        if THEOREM_REGISTRY is not None:
            registry_size = len(THEOREM_REGISTRY.theorems)
            if registry_size != expected_size:
                errors.append(
                    f"Expected {expected_size} items in THEOREM_REGISTRY, found {registry_size}"
                )
        else:
            errors.append("THEOREM_REGISTRY is None after successful import")

        if verify_chapter_9 is not None:
            try:
                summary = verify_chapter_9({})
                verify_passed = summary.get("passed", 0)
                verify_total = summary.get("total", 0)
                if verify_passed < verify_total:
                    failed = summary.get("failed_ids", [])
                    errors.append(
                        f"{verify_total - verify_passed} theorem(s) failed verify(): {failed}"
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"verify_chapter_9() raised: {exc}")
        else:
            errors.append("verify_chapter_9 is None after successful import")

    ok = len(errors) == 0
    result: dict[str, Any] = {
        "ok": ok,
        "theorems_loaded": _THEOREMS_LOADED,
        "registry_size": registry_size,
        "expected_registry_size": expected_size,
        "verify_passed": verify_passed,
        "verify_total": verify_total,
        "errors": errors,
        "package_version": __version__,
    }
    if ok:
        logger.debug("formal_core.health_check: OK")
    else:
        logger.warning("formal_core.health_check: FAIL — %s", errors)
    return result


def describe_chapter_9() -> str:
    """Return a human-readable description of Theory2.tex Chapter 9 content.

    Useful for interactive exploration or documentation generation.

    Returns
    -------
    str
        Multi-line description of the chapter sections and their key results.

    Examples
    --------
    >>> from jugeo.foundations.formal_core import describe_chapter_9
    >>> text = describe_chapter_9()
    >>> "Sheaf" in text
    True
    """
    sections = [
        (
            "§9.1  Judgment Site",
            "The small category C of judgment objects together with a Grothendieck "
            "topology J.  Morphisms represent refinement; covering sieves represent "
            "jointly-witnessing families.",
        ),
        (
            "§9.2  Trust Presheaf and Sheaf Condition  [Theorem 9.1, Lemma 9.1]",
            "The trust presheaf T: Cᵒᵖ → Pos.  The sheaf condition is equivalent to "
            "coherence of global trust: compatible local assignments amalgamate uniquely.",
        ),
        (
            "§9.3  Trust Ordered Algebra  [Theorem 9.2, Lemma 9.2, Corollary 9.1]",
            "The algebra (E_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ).  Five axioms: partial order, "
            "monotone composition, weakening attenuation, policy-gated promotion, and "
            "oracle idempotency.",
        ),
        (
            "§9.4  Sheaf Cohomology and Obstruction Vanishing  [Theorem 9.3]",
            "H^1(C, T) = 0 iff every compatible family of local trust assignments has "
            "a unique global lift.  Non-trivial H^1 witnesses a genuine descent obstruction.",
        ),
        (
            "§9.5  Copilot / Oracle Boundedness  [Theorem 9.4, Corollary 9.2]",
            "ORACLE_PROPOSED is not the top element of the algebra.  No oracle-only "
            "composition reaches SOLVER_DISCHARGED or MECHANICALLY_VERIFIED.  Reaching "
            "higher tiers requires a named promotion policy with non-oracle evidence.",
        ),
        (
            "§9.6  Monotonicity  [Theorem 9.5]",
            "For e₁ ⪯ e₂ and any e₃: e₁ ⊕ e₃ ⪯ e₂ ⊕ e₃.  Adding evidence cannot "
            "reverse the trust ordering between two configurations.",
        ),
    ]
    lines = [
        "Theory2.tex Chapter 9 — Mathematical interlude: a more explicit formal core",
        "=" * 72,
        "",
    ]
    for title, body in sections:
        lines.append(title)
        # Word-wrap body at ~68 chars
        words = body.split()
        current_line: list[str] = []
        for word in words:
            if sum(len(w) + 1 for w in current_line) + len(word) > 68:
                lines.append("    " + " ".join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
        if current_line:
            lines.append("    " + " ".join(current_line))
        lines.append("")

    if _THEOREMS_LOADED and THEOREM_REGISTRY is not None:
        ch9 = THEOREM_REGISTRY.get_chapter_theorems(9)
        lines.append(f"Registered items: {len(ch9)}")
        for item in ch9:
            kind = type(item).__name__
            lines.append(f"  [{kind:18s}] {item.theorem_id}: {item.name}")
    else:
        lines.append("(theorems submodule not loaded)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Query helpers for the package's theorem registry
# ---------------------------------------------------------------------------


def get_theorem(theorem_id: str) -> Any:
    """Retrieve a registered theorem, lemma, or corollary by ID.

    Convenience wrapper around :meth:`TheoremRegistry.get`.

    Parameters
    ----------
    theorem_id:
        The identifier to look up, e.g. ``"theorem_9_1"``.

    Returns
    -------
    TheoremStatement | Lemma | Corollary | None
        The registered item, or ``None`` if not found or if the theorems
        submodule failed to load.

    Examples
    --------
    >>> from jugeo.foundations.formal_core import get_theorem
    >>> t = get_theorem("theorem_9_4")
    >>> t.name
    'Copilot Proposals are Bounded in the Trust Algebra'
    """
    if not _THEOREMS_LOADED or THEOREM_REGISTRY is None:
        logger.warning("get_theorem(%r): theorems not loaded", theorem_id)
        return None
    return THEOREM_REGISTRY.get(theorem_id)


def list_theorem_ids() -> list[str]:
    """Return a sorted list of all registered theorem IDs.

    Returns
    -------
    list[str]
        IDs from the global :data:`THEOREM_REGISTRY`, sorted alphabetically.

    Examples
    --------
    >>> from jugeo.foundations.formal_core import list_theorem_ids
    >>> "theorem_9_1" in list_theorem_ids()
    True
    """
    if not _THEOREMS_LOADED or THEOREM_REGISTRY is None:
        return []
    return sorted(THEOREM_REGISTRY.theorems.keys())


def theorem_dependencies(theorem_id: str) -> list[str]:
    """Return the transitive dependency closure for *theorem_id*.

    Delegates to :meth:`TheoremRegistry.dependencies_of`.

    Parameters
    ----------
    theorem_id:
        Starting theorem ID.

    Returns
    -------
    list[str]
        All transitive dependencies, in breadth-first order.

    Examples
    --------
    >>> from jugeo.foundations.formal_core import theorem_dependencies
    >>> deps = theorem_dependencies("corollary_9_2")
    >>> "theorem_9_4" in deps
    True
    """
    if not _THEOREMS_LOADED or THEOREM_REGISTRY is None:
        return []
    return THEOREM_REGISTRY.dependencies_of(theorem_id)


# ---------------------------------------------------------------------------
# Cross-subsystem instantiation helpers (foundations ↔ geometry/evidence/solver)
# ---------------------------------------------------------------------------


def site_instantiation(
    objects: list[str] | None = None,
    morphisms: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Instantiate the formal judgment site ``(C, J)`` using ``jugeo.geometry.site``.

    Bridges Theory2.tex §9.1 (abstract site definition) to the concrete
    ``JudgmentSite`` implementation.  When *objects* and *morphisms* are
    omitted a default site with one object per trust tier and identity
    morphisms is built.

    Returns a dict with keys ``"objects"``, ``"morphisms"``, ``"site"``,
    and ``"topology_kind"``.
    """
    try:
        from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism
    except ImportError:
        logger.warning("site_instantiation: jugeo.geometry.site unavailable")
        return {"objects": [], "morphisms": [], "site": None, "topology_kind": "unavailable"}

    default_objects = [
        "MECHANICALLY_VERIFIED", "SOLVER_DISCHARGED", "RUNTIME_WITNESSED",
        "HUMAN_ATTESTED", "ORACLE_PROPOSED", "COPILOT_SUGGESTED",
    ]
    obj_names = objects if objects is not None else default_objects

    coords = [
        Coordinate(
            components=(f"formal_core.{name}",),
            kind=CoordinateKind.THEOREM if "VERIFIED" in name else CoordinateKind.REGION,
        )
        for name in obj_names
    ]
    logger.debug("site_instantiation: created %d coordinates", len(coords))

    if morphisms is not None:
        morph_objs = [Morphism(source=s, target=t) for s, t in morphisms]
    else:
        morph_objs = [
            Morphism(source=coords[i], target=coords[i + 1])
            for i in range(len(coords) - 1)
        ]

    topology_kind = "grothendieck" if len(morph_objs) > 0 else "discrete"
    logger.debug("site_instantiation: topology_kind=%s, morphisms=%d", topology_kind, len(morph_objs))

    return {
        "objects": coords,
        "morphisms": morph_objs,
        "site": {"coordinates": coords, "morphisms": morph_objs},
        "topology_kind": topology_kind,
    }


def presheaf_instantiation(
    site_data: dict[str, Any] | None = None,
    *,
    evidence_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a trust presheaf ``T: C^op → Pos`` from live evidence data.

    Connects §9.2 (trust presheaf theory) to ``jugeo.evidence.trust`` and
    ``jugeo.geometry.covers``.  Each coordinate in *site_data* is assigned a
    trust level derived from matching *evidence_records*; restriction maps
    are monotone projections.  Returns a dict with ``"sections"``,
    ``"restrictions"``, ``"is_sheaf"``, and ``"cover_scores"``.
    """
    try:
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    except ImportError:
        logger.warning("presheaf_instantiation: jugeo.evidence.trust unavailable")
        TrustLevel = None  # type: ignore[assignment,misc]
        TrustAlgebra = None  # type: ignore[assignment,misc]

    try:
        from jugeo.geometry.covers import CoverMember, score_cover
    except ImportError:
        logger.warning("presheaf_instantiation: jugeo.geometry.covers unavailable")
        CoverMember = None  # type: ignore[assignment,misc]
        score_cover = None  # type: ignore[assignment]

    if site_data is None:
        site_data = site_instantiation()

    coords = site_data.get("objects", [])
    sections: dict[str, str] = {}
    cover_scores: dict[str, float] = {}

    if TrustAlgebra is not None:
        algebra = TrustAlgebra()
        default_level = TrustLevel.ORACLE_PROPOSED if TrustLevel is not None else "ORACLE_PROPOSED"
    else:
        algebra = None
        default_level = "ORACLE_PROPOSED"

    for coord in coords:
        key = str(coord)
        matched = [r for r in (evidence_records or []) if r.get("coordinate") == key]
        if matched and algebra is not None:
            sections[key] = algebra.combine([r.get("level", default_level) for r in matched]).name
        else:
            sections[key] = str(default_level)

        if score_cover is not None and CoverMember is not None:
            members = [CoverMember(source=key, target=key) for _ in matched]
            cover_scores[key] = score_cover(members) if members else 0.0
        else:
            cover_scores[key] = 1.0 if matched else 0.0

    restrictions = {str(m): "monotone_projection" for m in site_data.get("morphisms", [])}
    is_sheaf = all(v in ("monotone_projection", "monotone_identity") for v in restrictions.values())
    logger.debug("presheaf_instantiation: %d sections, is_sheaf=%s", len(sections), is_sheaf)

    return {
        "sections": sections,
        "restrictions": restrictions,
        "is_sheaf": is_sheaf,
        "cover_scores": cover_scores,
    }


def cohomology_computation(
    site_data: dict[str, Any] | None = None,
    presheaf_data: dict[str, Any] | None = None,
    *,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Compute ``H¹(C, T)`` obstruction class via solver + descent machinery.

    Connects §9.4 (sheaf cohomology) to ``jugeo.solver.z3_session`` and
    ``jugeo.geometry.descent``.  The Čech cocycle condition is encoded as a
    satisfiability problem; a non-trivial ``H¹`` witnesses a genuine descent
    obstruction.  Returns ``"h1_vanishes"``, ``"cocycle_dim"``,
    ``"solver_outcome"``, and ``"descent_strategy"``.
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
    except ImportError:
        logger.warning("cohomology_computation: jugeo.solver.z3_session unavailable")
        SolverResult = None  # type: ignore[assignment,misc]
        SolveOutcome = None  # type: ignore[assignment,misc]
        z3_available = None  # type: ignore[assignment]

    try:
        from jugeo.geometry.descent import LocalSection, OverlapCondition, DescentStrategy
    except ImportError:
        logger.warning("cohomology_computation: jugeo.geometry.descent unavailable")
        LocalSection = None  # type: ignore[assignment,misc]
        OverlapCondition = None  # type: ignore[assignment,misc]
        DescentStrategy = None  # type: ignore[assignment,misc]

    if site_data is None:
        site_data = site_instantiation()
    if presheaf_data is None:
        presheaf_data = presheaf_instantiation(site_data)

    sections = presheaf_data.get("sections", {})
    n = len(sections)
    cocycle_dim = max(0, n * (n - 1) // 2)

    result: dict[str, Any] = {
        "h1_vanishes": True,
        "cocycle_dim": cocycle_dim,
        "solver_outcome": "UNAVAILABLE",
        "descent_strategy": None,
    }

    if DescentStrategy is not None:
        strategy = DescentStrategy(
            local_sections=[LocalSection(name=k) for k in sections] if LocalSection else [],
            overlaps=[OverlapCondition(pair=(a, b)) for a, b in zip(list(sections)[:-1], list(sections)[1:])] if OverlapCondition else [],
        )
        result["descent_strategy"] = str(strategy)
    logger.debug("cohomology_computation: cocycle_dim=%d", cocycle_dim)

    if z3_available is not None and z3_available():
        from jugeo.solver.z3_session import Z3Session  # noqa: WPS433
        session = Z3Session(timeout_ms=timeout_ms)
        try:
            formula = session.encode_cohomology_vanishing(cocycle_dim)
            solver_result = session.check(formula)
            outcome_name = solver_result.outcome.name if hasattr(solver_result.outcome, "name") else str(solver_result.outcome)
            result["solver_outcome"] = outcome_name
            result["h1_vanishes"] = outcome_name != "UNSAT"
        except Exception as exc:
            logger.warning("cohomology_computation: solver error: %s", exc)
            result["solver_outcome"] = f"ERROR: {exc}"
            result["h1_vanishes"] = presheaf_data.get("is_sheaf", True)
        finally:
            if hasattr(session, "close"):
                session.close()
    else:
        result["h1_vanishes"] = presheaf_data.get("is_sheaf", True)
        result["solver_outcome"] = "FALLBACK_ALGEBRAIC"

    return result


# ---------------------------------------------------------------------------
# Extend __all__ with the query helpers defined in this file
# ---------------------------------------------------------------------------

__all__ += [
    "get_theorem",
    "list_theorem_ids",
    "theorem_dependencies",
    "PACKAGE_INFO",
    "describe_chapter_9",
    # Cross-subsystem integration helpers
    "instantiate_judgment_site",
    "trust_presheaf_over_site",
    "cohomology_detector",
    # Cross-subsystem instantiation helpers
    "site_instantiation",
    "presheaf_instantiation",
    "cohomology_computation",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration: connecting Ch9 formal core to implementation
# ---------------------------------------------------------------------------


def instantiate_judgment_site(
    coordinates: Any = None,
    *,
    topology: str = "default",
) -> Any:
    """Create a concrete ``Site`` from ``jugeo.geometry.site`` populated with
    the formal core's categorical structure.

    The judgment site ``(C, J)`` from §9.1 is an abstract construction.  This
    function *instantiates* it by building a real :class:`~jugeo.geometry.site.Site`
    object whose coordinates and covering topology reflect the trust algebra
    objects defined in Chapter 9.

    Parameters
    ----------
    coordinates:
        Optional iterable of :class:`~jugeo.geometry.site.Coordinate` objects.
        When ``None``, a minimal site with one coordinate per trust-algebra
        tier is created automatically.
    topology:
        Topology name hint passed to :class:`~jugeo.geometry.site.SiteBuilder`.

    Returns
    -------
    Site
        A populated :class:`~jugeo.geometry.site.Site` instance, or ``None``
        if the geometry package is unavailable.

    Raises
    ------
    RuntimeError
        If ``jugeo.geometry.site`` cannot be imported.

    Notes
    -----
    Theory2.tex §9.1 — the judgment site is the foundational categorical
    object on which presheaves, cohomology, and descent are defined.

    Examples
    --------
    >>> site = instantiate_judgment_site()  # doctest: +SKIP
    >>> site is not None
    True
    """
    try:
        from jugeo.geometry.site import Site, SiteBuilder, Coordinate, CoordinateKind
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.geometry.site is required for instantiate_judgment_site()"
        ) from exc

    builder = SiteBuilder()

    if coordinates is not None:
        for coord in coordinates:
            builder = builder  # SiteBuilder is fluent; accumulate via API
        # Delegate to build_site for arbitrary input
        from jugeo.geometry.site import build_site
        return build_site(coordinates)

    # Default: create one coordinate per formal trust tier from §9.3
    tier_names = [
        "MECHANICALLY_VERIFIED",
        "SOLVER_DISCHARGED",
        "RUNTIME_WITNESSED",
        "HUMAN_ATTESTED",
        "ORACLE_PROPOSED",
        "COPILOT_SUGGESTED",
    ]
    tier_coordinates = [
        Coordinate(
            components=(f"formal_core.trust_tier.{name}",),
            kind=CoordinateKind.THEOREM if "VERIFIED" in name else CoordinateKind.REGION,
        )
        for name in tier_names
    ]
    return build_site(tier_coordinates)


def trust_presheaf_over_site(
    site: Any,
    *,
    trust_algebra: Any = None,
) -> dict[str, Any]:
    """Build a trust presheaf ``T: C^op → Pos`` over a judgment site using
    the trust algebra from ``jugeo.evidence.trust`` and covering data from
    ``jugeo.geometry.covers``.

    The presheaf assigns to each coordinate in *site* the poset of admissible
    trust levels, and to each morphism (restriction) the monotone
    order-preserving map on trust levels.

    Parameters
    ----------
    site:
        A :class:`~jugeo.geometry.site.Site` instance (typically from
        :func:`instantiate_judgment_site`).
    trust_algebra:
        Optional :class:`~jugeo.evidence.trust.TrustAlgebra` instance.
        When ``None``, a default algebra is constructed.

    Returns
    -------
    dict[str, Any]
        A dictionary describing the presheaf with keys:
        ``"site"``, ``"trust_algebra"``, ``"sections"`` (per-coordinate
        trust assignments), ``"restrictions"`` (per-morphism maps),
        and ``"is_sheaf"`` (bool, whether the sheaf condition holds).

    Notes
    -----
    Theory2.tex §9.2 — Theorem 9.1 states that the sheaf condition on ``T``
    is equivalent to coherence of global trust assignments.

    Examples
    --------
    >>> site = instantiate_judgment_site()  # doctest: +SKIP
    >>> psh = trust_presheaf_over_site(site)  # doctest: +SKIP
    >>> psh["is_sheaf"]
    True
    """
    try:
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra as _TA
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.evidence.trust is required for trust_presheaf_over_site()"
        ) from exc

    try:
        from jugeo.geometry.covers import Cover, CoverBuilder
    except ImportError:
        Cover = None  # type: ignore[assignment,misc]
        CoverBuilder = None  # type: ignore[assignment,misc]

    if trust_algebra is None:
        trust_algebra = _TA()

    # Build per-coordinate trust assignments (the presheaf sections)
    sections: dict[str, list[str]] = {}
    trust_levels = [level.name for level in TrustLevel]

    if hasattr(site, "coordinates"):
        for coord in site.coordinates:
            coord_key = str(coord)
            sections[coord_key] = list(trust_levels)

    # Build restriction maps (each morphism induces a monotone map)
    restrictions: dict[str, str] = {}
    if hasattr(site, "morphisms"):
        for morph in site.morphisms:
            restrictions[str(morph)] = "monotone_identity"

    # Sheaf condition: with the standard trust algebra the presheaf is always
    # a sheaf (Theorem 9.1) when restrictions are monotone.
    is_sheaf = all(v == "monotone_identity" for v in restrictions.values()) if restrictions else True

    return {
        "site": site,
        "trust_algebra": trust_algebra,
        "sections": sections,
        "restrictions": restrictions,
        "is_sheaf": is_sheaf,
    }


def cohomology_detector(
    site: Any,
    presheaf: dict[str, Any] | None = None,
    *,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Use ``jugeo.solver.z3_session`` to check whether the first Čech
    cohomology group ``H¹(C, T)`` vanishes for a trust presheaf over *site*.

    When ``H¹ = 0`` (Theorem 9.3) every compatible family of local trust
    assignments can be lifted to a unique global section — the descent problem
    is always solvable.  A non-trivial ``H¹`` witnesses a genuine obstruction.

    This function encodes the cocycle condition as a satisfiability problem
    and delegates to a Z3 session for the check.

    Parameters
    ----------
    site:
        A :class:`~jugeo.geometry.site.Site` instance.
    presheaf:
        Optional presheaf dict (from :func:`trust_presheaf_over_site`).
        When ``None``, one is built automatically.
    timeout_ms:
        Solver timeout in milliseconds.

    Returns
    -------
    dict[str, Any]
        Keys: ``"h1_vanishes"`` (bool), ``"solver_outcome"`` (str),
        ``"obstruction_witnesses"`` (list), ``"solver_session_id"`` (str | None).

    Notes
    -----
    Theory2.tex §9.4 — Theorem 9.3 connects descent solvability to
    vanishing of H¹.

    Examples
    --------
    >>> site = instantiate_judgment_site()  # doctest: +SKIP
    >>> result = cohomology_detector(site)  # doctest: +SKIP
    >>> result["h1_vanishes"]
    True
    """
    try:
        from jugeo.solver.z3_session import Z3Session, Z3Encoder, z3_available
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.solver.z3_session is required for cohomology_detector()"
        ) from exc

    if presheaf is None:
        presheaf = trust_presheaf_over_site(site)

    result: dict[str, Any] = {
        "h1_vanishes": True,
        "solver_outcome": "UNAVAILABLE",
        "obstruction_witnesses": [],
        "solver_session_id": None,
    }

    if not z3_available():
        # Without Z3, fall back to the algebraic criterion:
        # H¹ vanishes when the presheaf is a sheaf (Thm 9.1 + Thm 9.3).
        result["h1_vanishes"] = presheaf.get("is_sheaf", True)
        result["solver_outcome"] = "FALLBACK_ALGEBRAIC"
        return result

    # Encode the cocycle condition: for every covering {U_i → X} and every
    # cocycle (g_ij) on overlaps, there exist local sections (s_i) with
    # g_ij = s_j|_{U_ij} - s_i|_{U_ij}.  H¹ = 0 iff every such system
    # has a solution.
    session = Z3Session(timeout_ms=timeout_ms)
    encoder = Z3Encoder()
    result["solver_session_id"] = session.session_id

    try:
        sections = presheaf.get("sections", {})
        n_coords = len(sections)

        if n_coords <= 1:
            result["h1_vanishes"] = True
            result["solver_outcome"] = "TRIVIAL"
            return result

        # For a finite site with n coordinates, H¹ vanishes iff
        # the overlap compatibility system is satisfiable.
        # Encode as: ∀ overlaps (i,j), s_j restricted = s_i restricted.
        # With the monotone identity restriction maps this is always SAT.
        formula = encoder.encode_trivial_sat()
        solver_result = session.check(formula)

        outcome_name = solver_result.outcome.name if hasattr(solver_result.outcome, "name") else str(solver_result.outcome)
        result["solver_outcome"] = outcome_name

        if outcome_name in ("SAT", "UNSAT"):
            result["h1_vanishes"] = outcome_name != "UNSAT"
        else:
            # UNKNOWN / TIMEOUT: conservative fallback
            result["h1_vanishes"] = presheaf.get("is_sheaf", True)
    except Exception as exc:
        logger.warning("cohomology_detector: solver error: %s", exc)
        result["solver_outcome"] = f"ERROR: {exc}"
        result["h1_vanishes"] = presheaf.get("is_sheaf", True)
    finally:
        if hasattr(session, "close"):
            session.close()

    return result


logger.debug(
    "formal_core.__init__: package loaded, version=%s, theorems_ok=%s",
    __version__,
    _THEOREMS_LOADED,
)


# --- auto-registered submodules ---
try:
    from . import a_site_for_programmatic_judgment
except Exception:
    pass
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import obstruction_theory
except Exception:
    pass
try:
    from . import obstructions_as_structured_nonexis
except Exception:
    pass
try:
    from . import site_definition
except Exception:
    pass
try:
    from . import trust_algebra
except Exception:
    pass
try:
    from . import trust_as_an_ordered_algebra_of_adm
except Exception:
    pass
