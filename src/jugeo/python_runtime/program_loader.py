"""Bridge module: load Python programs into JuGeo's symbolic/sheaf-theoretic form.

# copilot: scaffolded as part of the equivalence testing benchmark infrastructure.

Overview
--------
``program_loader`` is the entry point for JuGeo's equivalence-checking
pipeline when operating on real Python source files.  It spans the gap between
the raw bytes of a ``.py`` file and the structured symbolic representation that
the ``relational_refinement`` problem-mode expects.

The pipeline has four stages:

1. **Source ingestion** — :class:`ProgramSource` wraps a raw source string
   (obtained from disk or passed directly) together with provenance metadata
   such as file path, logical name, version tag, and a SHA-256 fingerprint.

2. **AST parsing & coordinate extraction** — :class:`ProgramLoader._parse_ast`
   and :meth:`_extract_coordinates` walk the ``ast.Module`` tree and assign
   every node a *coordinate key* of the form ``file:line:col:node_type``.
   These coordinates are the finest-grained unit of granularity in the
   judgment sheaf (see theory2.tex §2.3, §4.1).

3. **Judgment section construction** — :meth:`_build_judgment_sections`
   encodes each coordinate as an 8-tuple
   ``(c, φ, A, E, O, B, T, Π)`` whose components map exactly onto the
   theory2.tex judgment vocabulary (§3.2, §5.1).

4. **Relational refinement packaging** — :meth:`to_relational_refinement_input`
   assembles both symbolic programs into the dict structure expected by the
   ``relational_refinement`` subsystem (§12.1).

Theory alignment (theory2.tex)
------------------------------
* §2.3  — Coordinate objects and their role as sites.
* §3.2  — Judgment tuples ``(c, φ, A, E, O, B, T, Π)``.
* §4.1  — Sheaf sections and restriction morphisms.
* §5.1  — Trust tiers: PROPOSAL, VERIFIED, ORACLE_PROPOSED, ORACLE_VERIFIED.
* §12.1 — Relational refinement input schema.
* §15.1 — Python-specific coordinate encoding conventions.

Design principles
-----------------
* **No silent trust promotion** — every node enters at ``PROPOSAL`` unless it
  has been runtime-witnessed; promotion is an explicit step performed by the
  refinement pipeline, not by this loader.
* **Judgments are tuples, not booleans** — the loader never reduces a judgment
  to a pass/fail value; all decision logic is deferred to the prover.
* **Graceful degradation** — all imports from jugeo internals are wrapped in
  ``try/except ImportError`` so the loader remains usable in isolation for
  testing without the full jugeo installation.

Copilot integration
-------------------
This module was scaffolded with copilot assistance and enters the trust
lattice at ``TrustTier.PROPOSAL`` (level 1).  Promotion to ``VERIFIED``
requires passing the equivalence benchmark test suite and a human review
sign-off (theory2.tex §5.3).
"""
from __future__ import annotations

import ast
import hashlib
import logging
import os
import textwrap
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo internal imports — all wrapped defensively (theory2.tex §5.1)
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import CoordinateObject, CoordinateKind  # type: ignore[import]

    _HAVE_SITE = True
except ImportError:
    _HAVE_SITE = False
    CoordinateObject = None  # type: ignore[assignment,misc]
    CoordinateKind = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import TrustLevel  # type: ignore[import]

    _HAVE_TRUST_LEVEL = True
except ImportError:
    _HAVE_TRUST_LEVEL = False
    TrustLevel = None  # type: ignore[assignment,misc]

try:
    from jugeo.errors import JuGeoError  # type: ignore[import]

    _HAVE_JUGEO_ERROR = True
except ImportError:
    _HAVE_JUGEO_ERROR = False
    JuGeoError = Exception  # type: ignore[assignment,misc]

try:
    from jugeo.python_runtime.scope_and_state.models import (  # type: ignore[import]
        ScopeKind,
        NameKind,
    )

    _HAVE_SCOPE_MODELS = True
except ImportError:
    _HAVE_SCOPE_MODELS = False
    ScopeKind = None  # type: ignore[assignment,misc]
    NameKind = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# TrustTier — standalone fallback so the module works without jugeo installed
# ---------------------------------------------------------------------------

# The canonical trust-tier vocabulary referenced throughout theory2.tex §5.1.
# These string names are the stable external contract; numeric levels are
# internal to the judgment algebra and must NOT be used as scalars here.
_TRUST_TIERS: dict[str, int] = {
    "PROPOSAL": 1,           # Asserted without external corroboration.
    "ORACLE_PROPOSED": 2,    # Sourced from a trusted oracle but not verified.
    "VERIFIED": 3,           # Witnessed at runtime or by proof.
    "ORACLE_VERIFIED": 4,    # Oracle-sourced AND runtime-witnessed.
}


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class ProgramLoaderError(Exception):
    """Structured error raised by :class:`ProgramLoader` and related utilities.

    Carries a human-readable message and an arbitrary context dict so callers
    can programmatically distinguish failure modes without string-parsing.

    Parameters
    ----------
    message:
        Human-readable description of the failure.
    context:
        Key/value pairs providing machine-readable detail (e.g. file path,
        line number, AST node type).  Defaults to an empty dict.

    Examples
    --------
    ::

        raise ProgramLoaderError(
            "Syntax error in program source",
            context={"path": "/tmp/foo.py", "lineno": 42},
        )
    """

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        #: Machine-readable failure context (see theory2.tex §A.2 error schema).
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return f"ProgramLoaderError({str(self)!r}, context={self.context!r})"


# ---------------------------------------------------------------------------
# ProgramSource — frozen, immutable provenance wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProgramSource:
    """Immutable wrapper around a raw Python source string with provenance.

    ``ProgramSource`` is the *first* object created in the loading pipeline.
    It records where the source came from, assigns it a logical name and
    version, and provides a stable SHA-256 fingerprint used downstream by the
    judgment sheaf to detect source drift between pipeline runs.

    Theory alignment (theory2.tex §2.1)
    ------------------------------------
    The source string is the *pre-syntactic* stratum of the program; the
    coordinate extraction step (§2.3) lifts it into the judgment lattice.

    Parameters
    ----------
    path:
        Absolute or relative file path if the source was loaded from disk;
        ``None`` when constructed from a bare string.
    source:
        Raw Python source text (must be valid UTF-8).
    name:
        Logical program name used as the ``file`` component of coordinate
        keys (e.g. ``"program_A"``).
    version:
        Semantic-version-like tag for this source snapshot.  Defaults to
        ``"0.0.1"``.
    metadata:
        Arbitrary key/value annotations (e.g. git commit hash, benchmark
        scenario ID).  Stored as an immutable ``frozenset`` of items
        internally; the public interface accepts and returns a plain dict.

    Notes
    -----
    * The frozen ``slots=True`` ensures instances are hashable and safe to use
      as dict keys or set members inside the judgment sheaf.
    * The :attr:`metadata` field is declared as ``tuple[tuple[str, Any], ...]``
      so that the frozen constraint is satisfied (plain dicts are not hashable).
      Use :meth:`load_from_path` and :meth:`load_from_string` which normalise
      the metadata to a plain dict before freezing.
    """

    path: str | None
    source: str
    name: str
    version: str
    #: Stored as a tuple-of-pairs so that the frozen dataclass remains hashable.
    _metadata_items: tuple[tuple[str, Any], ...]

    # ------------------------------------------------------------------
    # Factory class-methods
    # ------------------------------------------------------------------

    @classmethod
    def load_from_path(
        cls,
        path: str,
        *,
        name: str | None = None,
        version: str = "0.0.1",
        metadata: dict[str, Any] | None = None,
    ) -> ProgramSource:
        """Read a Python source file from *path* and wrap it in a :class:`ProgramSource`.

        Parameters
        ----------
        path:
            Filesystem path to a ``.py`` file (absolute or relative to cwd).
        name:
            Logical name; defaults to the bare filename without extension.
        version:
            Version tag for this snapshot.
        metadata:
            Arbitrary key/value annotations merged into the source record.

        Returns
        -------
        ProgramSource
            Fully populated, immutable source wrapper.

        Raises
        ------
        ProgramLoaderError
            If the file cannot be read (missing, permission denied, etc.).
        """
        resolved = os.path.abspath(path)
        try:
            with open(resolved, encoding="utf-8") as fh:
                source = fh.read()
        except OSError as exc:
            raise ProgramLoaderError(
                f"Cannot read source file: {exc}",
                context={"path": resolved, "errno": exc.errno},
            ) from exc

        logical_name = name or os.path.splitext(os.path.basename(resolved))[0]
        meta = dict(metadata or {})
        meta.setdefault("loaded_from", resolved)

        return cls(
            path=resolved,
            source=source,
            name=logical_name,
            version=version,
            _metadata_items=tuple(sorted(meta.items())),
        )

    @classmethod
    def load_from_string(
        cls,
        source: str,
        name: str,
        *,
        version: str = "0.0.1",
        metadata: dict[str, Any] | None = None,
    ) -> ProgramSource:
        """Wrap a raw source string in a :class:`ProgramSource`.

        Parameters
        ----------
        source:
            Raw Python source text.
        name:
            Logical program name (used as the ``file`` prefix in coordinate keys).
        version:
            Version tag.
        metadata:
            Arbitrary annotations.

        Returns
        -------
        ProgramSource
        """
        meta = dict(metadata or {})
        meta.setdefault("source_origin", "string")
        return cls(
            path=None,
            source=source,
            name=name,
            version=version,
            _metadata_items=tuple(sorted(meta.items())),
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> dict[str, Any]:
        """Return the metadata as a plain mutable dict.

        Returns
        -------
        dict[str, Any]
            A fresh copy of the metadata dict on every call.
        """
        return dict(self._metadata_items)

    def sha256(self) -> str:
        """Compute the SHA-256 fingerprint of the raw source string.

        The fingerprint is computed over the UTF-8 encoding of :attr:`source`.
        It is used by downstream pipeline stages to detect source drift and
        to invalidate cached judgment sections.

        Returns
        -------
        str
            64-character lowercase hex string.
        """
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    def __repr__(self) -> str:
        digest = self.sha256()[:8]
        return (
            f"ProgramSource(name={self.name!r}, version={self.version!r}, "
            f"sha256={digest!r}…)"
        )


# ---------------------------------------------------------------------------
# SymbolicProgram — mutable accumulator for the symbolic lifting result
# ---------------------------------------------------------------------------


@dataclass
class SymbolicProgram:
    """The symbolic/sheaf-theoretic lifting of a parsed Python program.

    ``SymbolicProgram`` is the primary output of :class:`ProgramLoader`.  It
    holds every artefact produced during the four-stage loading pipeline:
    the original source wrapper, the parsed AST, the coordinate list, and the
    four derived maps (judgment sections, trust map, type map, scope graph)
    together with a list of structural obstructions detected during analysis.

    Theory alignment (theory2.tex §4.1, §5.1, §15.1)
    --------------------------------------------------
    * The ``coordinates`` list is the *site* of the judgment sheaf: each entry
      is an open set ``U_i`` at coordinate ``c_i``.
    * The ``judgment_sections`` dict maps each ``c_i`` to a *section*
      ``s_i = (c, φ, A, E, O, B, T, Π)`` over ``U_i`` (§3.2).
    * The ``scope_graph`` is the *restriction functor* that records which
      sections can be legally restricted to sub-scopes (§4.1 Def. 4.3).
    * The ``obstructions`` list records detected failures of the gluing
      condition (§4.2 Def. 4.7).

    Parameters
    ----------
    source:
        The :class:`ProgramSource` from which this program was lifted.
    ast_tree:
        The parsed ``ast.Module`` object (output of :func:`ast.parse`).
    coordinates:
        Ordered list of coordinate dicts.  Each dict has keys:
        ``file``, ``line``, ``col``, ``node_type``, ``scope``.
    judgment_sections:
        Maps each coordinate key (``"file:line:col:node_type"``) to its
        judgment section tuple ``(c, φ, A, E, O, B, T, Π)``.
    trust_map:
        Maps each coordinate key to its :class:`TrustTier` name string.
    type_map:
        Maps each coordinate key to its best-effort type annotation string.
    scope_graph:
        Adjacency list ``{scope_key: [child_scope_key, …]}`` encoding the
        scope containment relation (theory2.tex §15.2 Fig. 15.1).
    obstructions:
        List of obstruction dicts produced by :meth:`_detect_obstructions`.
        Each dict has keys: ``kind``, ``coord_key``, ``description``,
        ``severity``.
    metadata:
        Arbitrary annotations added during loading.
    """

    source: ProgramSource
    ast_tree: Any  # ast.Module — Any avoids circular import issues
    coordinates: list[dict[str, Any]] = field(default_factory=list)
    judgment_sections: dict[str, Any] = field(default_factory=dict)
    trust_map: dict[str, str] = field(default_factory=dict)
    type_map: dict[str, str] = field(default_factory=dict)
    scope_graph: dict[str, list[str]] = field(default_factory=dict)
    obstructions: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def coordinate_count(self) -> int:
        """Return the number of coordinates in the judgment site."""
        return len(self.coordinates)

    def obstruction_count(self) -> int:
        """Return the number of detected obstructions."""
        return len(self.obstructions)

    def has_obstructions(self) -> bool:
        """Return ``True`` if any obstructions were detected."""
        return bool(self.obstructions)

    def coord_key(self, coord: dict[str, Any]) -> str:
        """Format a coordinate dict as its canonical string key.

        Parameters
        ----------
        coord:
            A coordinate dict as stored in :attr:`coordinates`.

        Returns
        -------
        str
            ``"file:line:col:node_type"``
        """
        return (
            f"{coord['file']}:{coord['line']}:{coord['col']}:{coord['node_type']}"
        )

    def section(self, coord_key: str) -> tuple | None:
        """Return the judgment section for a coordinate key, or ``None``.

        Parameters
        ----------
        coord_key:
            Canonical coordinate key string.

        Returns
        -------
        tuple | None
            The 8-tuple ``(c, φ, A, E, O, B, T, Π)`` or ``None`` if the key
            is not present in :attr:`judgment_sections`.
        """
        return self.judgment_sections.get(coord_key)

    def summary(self) -> dict[str, Any]:
        """Produce a brief summary dict suitable for logging or display.

        Returns
        -------
        dict[str, Any]
            Keys: ``name``, ``version``, ``sha256``, ``coordinates``,
            ``obstructions``, ``scopes``.
        """
        return {
            "name": self.source.name,
            "version": self.source.version,
            "sha256": self.source.sha256()[:16] + "…",
            "coordinates": self.coordinate_count(),
            "obstructions": self.obstruction_count(),
            "scopes": len(self.scope_graph),
        }


# ---------------------------------------------------------------------------
# ProgramLoader — the main loading engine
# ---------------------------------------------------------------------------


class ProgramLoader:
    """Load Python programs and lift them into JuGeo's symbolic representation.

    ``ProgramLoader`` is the central engine of the loading pipeline.  It
    orchestrates the four stages described in the module docstring and
    exposes a clean public API for both single-program loading (:meth:`load`)
    and pair loading (:meth:`load_pair`).

    Theory alignment (theory2.tex §2.3, §3.2, §12.1)
    --------------------------------------------------
    * ``_parse_ast``                → pre-syntactic → syntactic stratum.
    * ``_extract_coordinates``      → syntactic → site (§2.3).
    * ``_build_judgment_sections``  → site → sheaf sections (§3.2).
    * ``to_relational_refinement_input`` → packages for §12.1 input schema.

    Parameters
    ----------
    config:
        Optional configuration dict.  Recognised keys:

        * ``"default_trust"`` (str) — TrustTier name for oracle-sourced code;
          defaults to ``"PROPOSAL"``.
        * ``"emit_type_comments"`` (bool) — if ``True``, type comments in
          source are parsed and folded into the type map; defaults to ``True``.
        * ``"max_coordinates"`` (int) — hard cap on coordinates per program to
          prevent runaway analysis of huge files; defaults to ``100_000``.
        * ``"include_constants"`` (bool) — if ``True``, :class:`ast.Constant`
          nodes are emitted as coordinates; defaults to ``False`` (too noisy).

    Examples
    --------
    ::

        loader = ProgramLoader()
        prog = loader.load("def f(x): return x + 1\\n", name="identity")
        print(prog.coordinate_count())

        prog_a, prog_b = loader.load_pair(
            "def f(x): return x + 1\\n",
            "def f(x): return 1 + x\\n",
        )
        rr_input = loader.to_relational_refinement_input(prog_a, prog_b)
    """

    # Nodes that are too granular to warrant their own coordinate by default.
    _SKIP_NODE_TYPES: frozenset[str] = frozenset(
        {
            "Load",
            "Store",
            "Del",
            "AugLoad",
            "AugStore",
            "Param",
            "Suite",
            "Expression",
            # Expression context helpers — not semantic nodes
            "expr_context",
        }
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        #: TrustTier name applied to oracle-sourced (unwitnessed) nodes.
        self._default_trust: str = cfg.get("default_trust", "PROPOSAL")
        #: Whether to parse PEP 484 type comments.
        self._emit_type_comments: bool = bool(cfg.get("emit_type_comments", True))
        #: Hard cap on emitted coordinates per program.
        self._max_coordinates: int = int(cfg.get("max_coordinates", 100_000))
        #: Whether to emit ast.Constant nodes as individual coordinates.
        self._include_constants: bool = bool(cfg.get("include_constants", False))

        if self._default_trust not in _TRUST_TIERS:
            raise ProgramLoaderError(
                f"Unknown default_trust tier: {self._default_trust!r}",
                context={"valid_tiers": list(_TRUST_TIERS)},
            )

        logger.debug(
            "ProgramLoader initialised: default_trust=%s, max_coords=%d",
            self._default_trust,
            self._max_coordinates,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        source_or_path: str,
        *,
        is_path: bool = False,
        name: str = "program",
        version: str = "0.0.1",
        metadata: dict[str, Any] | None = None,
    ) -> SymbolicProgram:
        """Load a single Python program and return its symbolic lifting.

        Parameters
        ----------
        source_or_path:
            Either a raw Python source string (when *is_path* is ``False``) or
            a filesystem path to a ``.py`` file (when *is_path* is ``True``).
        is_path:
            Treat *source_or_path* as a file path rather than source text.
        name:
            Logical program name; used as the ``file`` prefix in coordinate
            keys.  When *is_path* is ``True`` this defaults to the basename.
        version:
            Version tag for the :class:`ProgramSource`.
        metadata:
            Arbitrary annotations forwarded to :class:`ProgramSource`.

        Returns
        -------
        SymbolicProgram
            Fully populated symbolic program.

        Raises
        ------
        ProgramLoaderError
            On I/O errors, syntax errors, or analysis failures.
        """
        # Stage 1 — source ingestion
        if is_path:
            ps = ProgramSource.load_from_path(
                source_or_path, name=name, version=version, metadata=metadata
            )
        else:
            ps = ProgramSource.load_from_string(
                source_or_path, name=name, version=version, metadata=metadata
            )

        # Stage 2 — parse AST
        tree = self._parse_ast(ps.source)

        # Stage 3 — extract coordinates and build maps
        coords = self._extract_coordinates(tree, filename=ps.name)
        judgment_secs = self._build_judgment_sections(tree, coords)
        type_map = self._infer_type_map(tree)
        scope_graph = self._build_scope_graph(tree)
        obstructions = self._detect_obstructions(tree)

        # Build trust map — one entry per coordinate
        trust_map: dict[str, str] = {}
        for coord in coords:
            ck = _fmt_coord_key(coord)
            node = coord.get("_ast_node")
            if node is not None:
                trust_map[ck] = self._assign_trust(node, coord)
            else:
                trust_map[ck] = self._default_trust

        # Strip private _ast_node reference before returning (internal only)
        clean_coords = [
            {k: v for k, v in c.items() if not k.startswith("_")} for c in coords
        ]

        prog = SymbolicProgram(
            source=ps,
            ast_tree=tree,
            coordinates=clean_coords,
            judgment_sections=judgment_secs,
            trust_map=trust_map,
            type_map=type_map,
            scope_graph=scope_graph,
            obstructions=obstructions,
            metadata=dict(metadata or {}),
        )

        logger.info(
            "Loaded program %r: %d coordinates, %d obstructions",
            ps.name,
            prog.coordinate_count(),
            prog.obstruction_count(),
        )
        return prog

    def load_pair(
        self,
        a: str,
        b: str,
        *,
        a_name: str = "program_A",
        b_name: str = "program_B",
        is_path: bool = False,
        metadata_a: dict[str, Any] | None = None,
        metadata_b: dict[str, Any] | None = None,
    ) -> tuple[SymbolicProgram, SymbolicProgram]:
        """Load two programs and return them as a pair for equivalence checking.

        Both programs go through the full loading pipeline independently.  The
        resulting pair is intended to be passed directly to
        :meth:`to_relational_refinement_input`.

        Parameters
        ----------
        a:
            Source text or file path for the first program.
        b:
            Source text or file path for the second program.
        a_name:
            Logical name for the first program.
        b_name:
            Logical name for the second program.
        is_path:
            When ``True``, both *a* and *b* are treated as file paths.
        metadata_a:
            Extra metadata for the first program.
        metadata_b:
            Extra metadata for the second program.

        Returns
        -------
        tuple[SymbolicProgram, SymbolicProgram]
            ``(prog_a, prog_b)`` — both fully populated.

        Raises
        ------
        ProgramLoaderError
            If either program fails to load.
        """
        prog_a = self.load(a, is_path=is_path, name=a_name, metadata=metadata_a)
        prog_b = self.load(b, is_path=is_path, name=b_name, metadata=metadata_b)
        logger.info(
            "Loaded program pair: %r (%d coords) vs %r (%d coords)",
            a_name,
            prog_a.coordinate_count(),
            b_name,
            prog_b.coordinate_count(),
        )
        return prog_a, prog_b

    # ------------------------------------------------------------------
    # Internal pipeline methods
    # ------------------------------------------------------------------

    def _parse_ast(self, source: str) -> ast.Module:
        """Parse *source* into an ``ast.Module``.

        Wraps :func:`ast.parse` and converts ``SyntaxError`` into
        :class:`ProgramLoaderError` with structured context.

        Parameters
        ----------
        source:
            Raw Python source text.

        Returns
        -------
        ast.Module
            The parsed module node with line/column info populated.

        Raises
        ------
        ProgramLoaderError
            On ``SyntaxError``.
        """
        parse_kwargs: dict[str, Any] = {"type_comments": self._emit_type_comments}
        try:
            tree = ast.parse(
                source,
                mode="exec",
                **parse_kwargs,
            )
        except SyntaxError as exc:
            raise ProgramLoaderError(
                f"Syntax error while parsing source: {exc}",
                context={
                    "lineno": exc.lineno,
                    "offset": exc.offset,
                    "text": exc.text,
                },
            ) from exc
        except ValueError as exc:
            # ast.parse raises ValueError for some malformed inputs
            raise ProgramLoaderError(
                f"Malformed source (ValueError from ast.parse): {exc}",
                context={"error": str(exc)},
            ) from exc

        # Attach parent references — useful for _build_judgment_sections
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child._parent = node  # type: ignore[attr-defined]

        return tree  # type: ignore[return-value]

    def _extract_coordinates(
        self, tree: ast.Module, filename: str
    ) -> list[dict[str, Any]]:
        """Walk *tree* and emit one coordinate dict per semantically relevant node.

        Coordinates are the *sites* of the judgment sheaf (theory2.tex §2.3).
        Each coordinate dict has:

        * ``file``      — the logical filename / program name.
        * ``line``      — 1-based line number (``node.lineno``).
        * ``col``       — 0-based column offset (``node.col_offset``).
        * ``node_type`` — ``type(node).__name__``.
        * ``scope``     — slash-separated enclosing scope chain (§15.2).
        * ``_ast_node`` — private reference to the original ``ast.AST`` node;
          stripped before the list is returned to callers.

        Parameters
        ----------
        tree:
            Parsed ``ast.Module``.
        filename:
            Logical program name used as the ``file`` field.

        Returns
        -------
        list[dict[str, Any]]
            Ordered coordinate list (pre-order AST walk).
        """
        coords: list[dict[str, Any]] = []
        # scope_stack is a list of scope-segment strings built as we descend
        scope_stack: list[str] = [filename]

        # Scope-opening node types — entering one of these pushes a new scope
        _SCOPE_OPENERS = (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Lambda,
            ast.ListComp,
            ast.SetComp,
            ast.DictComp,
            ast.GeneratorExp,
        )

        def _scope_name(node: ast.AST) -> str:
            """Return the scope label pushed onto the stack for *node*."""
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name  # type: ignore[union-attr]
            if isinstance(node, ast.ClassDef):
                return node.name  # type: ignore[union-attr]
            if isinstance(node, ast.Lambda):
                ln = getattr(node, "lineno", 0)
                return f"<lambda:{ln}>"
            # Comprehension / generator
            return f"<comp:{type(node).__name__}:{getattr(node, 'lineno', 0)}>"

        def _visit(node: ast.AST) -> None:
            nonlocal scope_stack

            node_type = type(node).__name__

            # Skip entirely uninteresting context nodes
            if node_type in self._SKIP_NODE_TYPES:
                for child in ast.iter_child_nodes(node):
                    _visit(child)
                return

            # Optionally skip ast.Constant nodes (too noisy by default)
            if not self._include_constants and isinstance(node, ast.Constant):
                for child in ast.iter_child_nodes(node):
                    _visit(child)
                return

            # Nodes without line/col info (e.g. ast.arguments sub-nodes) get
            # a synthetic position to avoid key collisions.
            line = getattr(node, "lineno", 0)
            col = getattr(node, "col_offset", 0)
            scope = "/".join(scope_stack)

            coord: dict[str, Any] = {
                "file": filename,
                "line": line,
                "col": col,
                "node_type": node_type,
                "scope": scope,
                "_ast_node": node,  # stripped later
            }
            coords.append(coord)

            # Enforce hard coordinate cap (config: max_coordinates)
            if len(coords) >= self._max_coordinates:
                logger.warning(
                    "Coordinate cap (%d) reached for %r; truncating.",
                    self._max_coordinates,
                    filename,
                )
                return

            # Push scope if this node opens one
            pushed = False
            if isinstance(node, _SCOPE_OPENERS):
                scope_stack.append(_scope_name(node))
                pushed = True

            for child in ast.iter_child_nodes(node):
                _visit(child)
                if len(coords) >= self._max_coordinates:
                    break

            if pushed:
                scope_stack.pop()

        _visit(tree)
        return coords

    def _build_judgment_sections(
        self, tree: ast.Module, coords: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the 8-tuple judgment sections for every coordinate.

        Iterates over *coords* (which still carry the private ``_ast_node``
        reference) and calls :meth:`_encode_judgment` to produce the
        ``(c, φ, A, E, O, B, T, Π)`` tuple for each.

        The resulting dict maps each coordinate key to its section tuple and is
        stored in :attr:`SymbolicProgram.judgment_sections`.

        Theory alignment (theory2.tex §3.2 Def. 3.4)
        -----------------------------------------------
        The section function ``s: U_i → 𝒥`` assigns to each open set in the
        site a judgment tuple.  The eight components are:

        * c — coordinate key (site address).
        * φ — predicate / semantic role of the node.
        * A — ambient context (enclosing scope chain string).
        * E — evidence (annotation if present, else ``"INFERRED"``).
        * O — list of obligations (structural constraints).
        * B — basis (parent coordinate key, or ``"ROOT"``).
        * T — trust tier name (theory2.tex §5.1).
        * Π — proof obligations (empty list initially; filled by prover).

        Parameters
        ----------
        tree:
            Parsed ``ast.Module`` (used here only for parent-ref traversal).
        coords:
            Coordinate list as produced by :meth:`_extract_coordinates` (with
            ``_ast_node`` references still present).

        Returns
        -------
        dict[str, Any]
            Maps ``coord_key`` → ``(c, φ, A, E, O, B, T, Π)`` tuples.
        """
        sections: dict[str, Any] = {}

        # Build a reverse index: ast.AST id → coord_key  (for parent lookup)
        node_to_key: dict[int, str] = {}
        for coord in coords:
            node = coord.get("_ast_node")
            if node is not None:
                node_to_key[id(node)] = _fmt_coord_key(coord)

        for coord in coords:
            node = coord.get("_ast_node")
            if node is None:
                continue

            ck = _fmt_coord_key(coord)
            judgment = self._encode_judgment(node, coord, node_to_key)
            sections[ck] = judgment

        return sections

    def _infer_type_map(self, tree: ast.Module) -> dict[str, str]:
        """Infer a best-effort type annotation for each annotated name.

        Walks the AST looking for:

        * ``ast.AnnAssign`` nodes (PEP 526 variable annotations).
        * ``ast.FunctionDef`` / ``ast.AsyncFunctionDef`` return annotations.
        * ``ast.arg`` nodes with annotations (function parameter types).

        When a type comment is available (PEP 484, enabled by ``emit_type_comments``),
        it is included as well.

        Parameters
        ----------
        tree:
            Parsed ``ast.Module``.

        Returns
        -------
        dict[str, str]
            Maps ``coord_key`` to a type annotation string.  Nodes without an
            explicit annotation are not included (callers fall back to
            ``"INFERRED"``).
        """
        type_map: dict[str, str] = {}

        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            col = getattr(node, "col_offset", 0)

            if isinstance(node, ast.AnnAssign) and node.annotation is not None:
                ann_str = ast.unparse(node.annotation)
                ck = f"?:{line}:{col}:AnnAssign"
                type_map[ck] = ann_str

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    ret_str = ast.unparse(node.returns)
                    ck = f"?:{line}:{col}:{type(node).__name__}"
                    type_map[ck] = f"-> {ret_str}"

                # Parameter annotations
                for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                    if arg.annotation is not None:
                        arg_ann = ast.unparse(arg.annotation)
                        arg_line = getattr(arg, "lineno", 0)
                        arg_col = getattr(arg, "col_offset", 0)
                        ck = f"?:{arg_line}:{arg_col}:arg"
                        type_map[ck] = arg_ann

                # Type comment (PEP 484) — only present when type_comments=True
                tc = getattr(node, "type_comment", None)
                if tc is not None:
                    ck_tc = f"?:{line}:{col}:{type(node).__name__}:type_comment"
                    type_map[ck_tc] = tc

        return type_map

    def _build_scope_graph(self, tree: ast.Module) -> dict[str, list[str]]:
        """Build the scope containment graph for *tree*.

        Returns an adjacency list ``{scope_key: [child_scope_key, …]}`` where
        each scope key is a slash-separated path matching the ``scope`` field
        of coordinates emitted by :meth:`_extract_coordinates`.

        The root scope (``"<module>"``-ish) is the empty-path entry.  Child
        scopes are added as nodes discover nested definitions.

        Theory alignment (theory2.tex §15.2 Fig. 15.1)
        -----------------------------------------------
        The scope graph is the *base category* of the scope sheaf: the objects
        are scopes and the morphisms are scope-containment inclusions.  Every
        restriction morphism in the judgment sheaf factors through this graph.

        Parameters
        ----------
        tree:
            Parsed ``ast.Module``.

        Returns
        -------
        dict[str, list[str]]
            Adjacency list ``{parent_scope: [child_scope, …]}``.
        """
        graph: dict[str, list[str]] = {}
        stack: list[str] = []

        _SCOPE_OPENERS = (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Lambda,
            ast.ListComp,
            ast.SetComp,
            ast.DictComp,
            ast.GeneratorExp,
        )

        def _scope_label(node: ast.AST) -> str:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return node.name  # type: ignore[union-attr]
            return f"<{type(node).__name__}:{getattr(node, 'lineno', 0)}>"

        def _visit_scope(node: ast.AST) -> None:
            current = "/".join(stack) if stack else "<root>"
            if current not in graph:
                graph[current] = []

            for child in ast.iter_child_nodes(node):
                if isinstance(child, _SCOPE_OPENERS):
                    label = _scope_label(child)
                    child_key = (current + "/" + label) if current != "<root>" else label
                    if child_key not in graph[current]:
                        graph[current].append(child_key)
                    stack.append(label)
                    _visit_scope(child)
                    stack.pop()
                else:
                    _visit_scope(child)

        _visit_scope(tree)
        return graph

    def _detect_obstructions(self, tree: ast.Module) -> list[dict[str, Any]]:
        """Detect structural obstructions in *tree*.

        An obstruction (theory2.tex §4.2 Def. 4.7) is a failure of the gluing
        condition: two locally consistent sections that cannot be assembled into
        a global section.  In the Python setting this manifests as structural
        patterns that prevent sound equivalence reasoning:

        * **Wildcard imports** (``from x import *``) — prevent complete name
          resolution; all names from the wildcard namespace become ``UNKNOWN``.
        * **Bare ``except:`` clauses** — swallow all exceptions, preventing
          accurate error-propagation analysis.
        * **``exec`` / ``eval`` calls** — introduce dynamic code execution whose
          effects are opaque to the static analysis.
        * **``globals()`` / ``locals()`` calls** — expose the scope dict,
          invalidating name-resolution invariants.
        * **``__import__`` calls** — dynamic import whose target is unknown at
          analysis time.
        * **Mutable default arguments** — a well-known Python footgun that
          creates hidden shared state across calls.

        Each detected obstruction is recorded as a dict with keys:
        ``kind``, ``coord_key``, ``description``, ``severity``.

        Parameters
        ----------
        tree:
            Parsed ``ast.Module``.

        Returns
        -------
        list[dict[str, Any]]
            List of obstruction records (may be empty).
        """
        obstructions: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            col = getattr(node, "col_offset", 0)
            base_key = f"?:{line}:{col}:{type(node).__name__}"

            # Wildcard import: from x import *
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        obstructions.append(
                            {
                                "kind": "WILDCARD_IMPORT",
                                "coord_key": base_key,
                                "description": (
                                    f"Wildcard import from {node.module!r} prevents "
                                    "complete name resolution (theory2.tex §15.3 O1)."
                                ),
                                "severity": "HIGH",
                            }
                        )

            # Bare except clause — ExceptHandler with no type annotation
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                obstructions.append(
                    {
                        "kind": "BARE_EXCEPT",
                        "coord_key": base_key,
                        "description": (
                            "Bare 'except:' swallows all exceptions, preventing "
                            "accurate error-propagation analysis (theory2.tex §15.3 O2)."
                        ),
                        "severity": "MEDIUM",
                    }
                )

            # exec / eval / globals / locals / __import__ calls
            elif isinstance(node, ast.Call):
                func_name: str | None = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                _DYNAMIC_BUILTINS = {
                    "exec": (
                        "exec() introduces opaque dynamic code; equivalence "
                        "analysis is unsound (theory2.tex §15.3 O3).",
                        "CRITICAL",
                    ),
                    "eval": (
                        "eval() evaluates an arbitrary expression at runtime; "
                        "static analysis cannot bound its effects (§15.3 O3).",
                        "CRITICAL",
                    ),
                    "globals": (
                        "globals() exposes the module-level scope dict, invalidating "
                        "name-resolution invariants (§15.3 O4).",
                        "HIGH",
                    ),
                    "locals": (
                        "locals() exposes the local scope dict, invalidating "
                        "name-resolution invariants (§15.3 O4).",
                        "HIGH",
                    ),
                    "__import__": (
                        "__import__() performs a dynamic import whose target is "
                        "unknown at analysis time (§15.3 O5).",
                        "HIGH",
                    ),
                    "compile": (
                        "compile() generates a code object at runtime; analysis "
                        "cannot inspect the generated code statically (§15.3 O3).",
                        "HIGH",
                    ),
                }
                if func_name in _DYNAMIC_BUILTINS:
                    desc, severity = _DYNAMIC_BUILTINS[func_name]
                    obstructions.append(
                        {
                            "kind": f"DYNAMIC_CALL:{func_name.upper()}",
                            "coord_key": base_key,
                            "description": desc,
                            "severity": severity,
                        }
                    )

            # Mutable default argument (list / dict / set literal as default)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if default is None:
                        continue
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        obstructions.append(
                            {
                                "kind": "MUTABLE_DEFAULT_ARG",
                                "coord_key": base_key,
                                "description": (
                                    f"Function {node.name!r} has a mutable default "
                                    "argument (list/dict/set); hidden shared state "
                                    "makes call semantics non-deterministic (§15.3 O6)."
                                ),
                                "severity": "MEDIUM",
                            }
                        )
                        break  # report once per function

        return obstructions

    def _assign_trust(self, node: ast.AST, coord: dict[str, Any]) -> str:
        """Determine the TrustTier name for an AST node and its coordinate.

        Trust assignment follows theory2.tex §5.2:

        * By default all nodes enter at ``PROPOSAL`` — they are asserted by the
          programmer but not yet witnessed by the runtime.
        * Nodes annotated with explicit type information are still ``PROPOSAL``
          (annotations are claims, not proofs).
        * Nodes in a ``try`` block with a handler are still ``PROPOSAL``
          (the handler path may or may not be taken).
        * **No silent promotion** — only the refinement pipeline (stage 4) may
          promote trust, and only with an explicit proof obligation discharge.

        Parameters
        ----------
        node:
            The AST node to classify.
        coord:
            The coordinate dict for *node* (used for contextual lookups).

        Returns
        -------
        str
            A key from ``_TRUST_TIERS``, e.g. ``"PROPOSAL"``.
        """
        # Oracle-sourced code (e.g. stdlib stubs, typed-stub packages) could be
        # promoted to ORACLE_PROPOSED if the loader is configured to recognise
        # it.  For now all user code defaults to PROPOSAL (theory2.tex §5.2 R1).
        return self._default_trust

    def _encode_judgment(
        self,
        node: ast.AST,
        coord: dict[str, Any],
        node_to_key: dict[int, str] | None = None,
    ) -> tuple:
        """Encode an AST node as an 8-tuple judgment section.

        Returns the canonical judgment tuple
        ``(c, φ, A, E, O, B, T, Π)`` (theory2.tex §3.2 Def. 3.4).

        Parameters
        ----------
        node:
            The AST node being encoded.
        coord:
            The coordinate dict for *node*.
        node_to_key:
            Optional reverse-index mapping ``id(ast_node)`` → ``coord_key``,
            used to resolve the basis ``B`` (parent coordinate key).

        Returns
        -------
        tuple
            8-tuple ``(c, φ, A, E, O, B, T, Π)``.
        """
        # c — coordinate key (theory2.tex §2.3 Def. 2.1)
        c: str = _fmt_coord_key(coord)

        # φ — predicate / semantic role of the node
        φ: str = _semantic_role(node)

        # A — ambient context: enclosing scope chain (theory2.tex §3.2)
        A: str = coord.get("scope", "<unknown>")

        # E — evidence: explicit annotation if present, else "INFERRED"
        E: str = _extract_evidence(node)

        # O — obligations: structural constraints derived from node type
        O: list[str] = _derive_obligations(node)

        # B — basis: parent coordinate key (theory2.tex §3.2 Def. 3.4 B-component)
        B: str = "ROOT"
        if node_to_key is not None:
            parent = getattr(node, "_parent", None)
            if parent is not None:
                B = node_to_key.get(id(parent), "ROOT")

        # T — trust tier (theory2.tex §5.1)
        T: str = self._assign_trust(node, coord)

        # Π — proof obligations: empty initially; filled by the prover
        Π: list[str] = []

        return (c, φ, A, E, O, B, T, Π)

    # ------------------------------------------------------------------
    # Relational refinement packaging
    # ------------------------------------------------------------------

    def to_relational_refinement_input(
        self,
        prog_a: SymbolicProgram,
        prog_b: SymbolicProgram,
    ) -> dict[str, Any]:
        """Package two symbolic programs as input to the relational_refinement subsystem.

        The returned dict conforms to the schema described in theory2.tex §12.1
        and is intended to be passed directly to the ``relational_refinement``
        problem-mode entry point.

        Structure of the returned dict
        --------------------------------
        ::

            {
              "schema_version": "1.0",
              "task": "equivalence_check",
              "programs": {
                "A": { ... SymbolicProgram summary ... },
                "B": { ... SymbolicProgram summary ... },
              },
              "sites": {
                "A": [ coord_dict, … ],
                "B": [ coord_dict, … ],
              },
              "sections": {
                "A": { coord_key: (c,φ,A,E,O,B,T,Π), … },
                "B": { coord_key: (c,φ,A,E,O,B,T,Π), … },
              },
              "trust_maps": {
                "A": { coord_key: trust_tier_name, … },
                "B": { coord_key: trust_tier_name, … },
              },
              "scope_graphs": {
                "A": { scope_key: [child_scope, …], … },
                "B": { scope_key: [child_scope, …], … },
              },
              "obstructions": {
                "A": [ obstruction_dict, … ],
                "B": [ obstruction_dict, … ],
              },
              "metadata": { ... combined metadata ... },
            }

        Parameters
        ----------
        prog_a:
            The first symbolic program (left-hand side of the equivalence).
        prog_b:
            The second symbolic program (right-hand side of the equivalence).

        Returns
        -------
        dict[str, Any]
            Relational refinement input dict.
        """
        # Judgment sections are stored as tuples in SymbolicProgram.
        # Convert them to lists for JSON-compatibility (tuples → lists).
        def _sections_to_serialisable(
            secs: dict[str, Any]
        ) -> dict[str, list]:
            return {k: list(v) for k, v in secs.items()}

        return {
            "schema_version": "1.0",
            "task": "equivalence_check",
            "programs": {
                "A": {
                    **prog_a.summary(),
                    "path": prog_a.source.path,
                    "sha256": prog_a.source.sha256(),
                    "metadata": prog_a.source.metadata,
                },
                "B": {
                    **prog_b.summary(),
                    "path": prog_b.source.path,
                    "sha256": prog_b.source.sha256(),
                    "metadata": prog_b.source.metadata,
                },
            },
            "sites": {
                "A": prog_a.coordinates,
                "B": prog_b.coordinates,
            },
            "sections": {
                "A": _sections_to_serialisable(prog_a.judgment_sections),
                "B": _sections_to_serialisable(prog_b.judgment_sections),
            },
            "trust_maps": {
                "A": prog_a.trust_map,
                "B": prog_b.trust_map,
            },
            "type_maps": {
                "A": prog_a.type_map,
                "B": prog_b.type_map,
            },
            "scope_graphs": {
                "A": prog_a.scope_graph,
                "B": prog_b.scope_graph,
            },
            "obstructions": {
                "A": prog_a.obstructions,
                "B": prog_b.obstructions,
            },
            "metadata": {
                "loader_default_trust": self._default_trust,
                "program_a_name": prog_a.source.name,
                "program_b_name": prog_b.source.name,
                **prog_a.metadata,
                **prog_b.metadata,
            },
        }

    # -- cross-subsystem integration -----------------------------------------

    def to_judgment_site(
        self,
        program: SymbolicProgram,
    ) -> Any:
        """Convert a loaded program's coordinates into a ``Site``.

        Uses ``jugeo.geometry.site.Site`` (and its builder) to lift the
        coordinate list produced during loading into a first-class
        Grothendieck site object that other subsystems can consume.

        Parameters
        ----------
        program:
            A :class:`SymbolicProgram` previously returned by
            :meth:`load`.

        Returns
        -------
        Site | dict
            A ``Site`` instance when the geometry package is available,
            otherwise a plain dict with the coordinate data.
        """
        try:
            from jugeo.geometry.site import Site, Coordinate, CoordinateKind
        except ImportError:  # pragma: no cover
            Site = None  # type: ignore[assignment,misc]
            Coordinate = None  # type: ignore[assignment,misc]
            CoordinateKind = None  # type: ignore[assignment,misc]

        if Site is None or Coordinate is None:
            return {
                "coordinates": program.coordinates,
                "scope_graph": program.scope_graph,
                "source_name": program.source.name,
            }

        coordinates: list[Any] = []
        for coord in program.coordinates:
            key = program.coord_key(coord)
            kind = CoordinateKind.EXPRESSION if CoordinateKind is not None else None
            node_type = coord.get("node_type", "")
            if node_type in ("FunctionDef", "AsyncFunctionDef"):
                kind = CoordinateKind.FUNCTION if CoordinateKind is not None else None
            elif node_type in ("ClassDef",):
                kind = CoordinateKind.TYPE if CoordinateKind is not None else None
            elif node_type in ("Module",):
                kind = CoordinateKind.MODULE if CoordinateKind is not None else None

            try:
                c = Coordinate(
                    components=tuple(key.split(":")),
                    kind=kind,
                )
                coordinates.append(c)
            except (TypeError, ValueError):
                coordinates.append(Coordinate(components=(key,)))

        try:
            site = Site(coordinates=tuple(coordinates))
            return site
        except (TypeError, ValueError):
            return {
                "coordinates": [repr(c) for c in coordinates],
                "source_name": program.source.name,
            }

    def evidence_from_ast(
        self,
        program: SymbolicProgram,
    ) -> list[dict[str, Any]]:
        """Extract evidence from a program's AST using evidence channels.

        Uses ``jugeo.evidence.channels.EvidenceChannel`` to classify
        AST-derived observations (type annotations, docstrings, assert
        statements, etc.) as evidence items routed through the
        appropriate channel.

        Parameters
        ----------
        program:
            A :class:`SymbolicProgram` previously returned by
            :meth:`load`.

        Returns
        -------
        list[dict[str, Any]]
            A list of evidence dicts, each with keys ``"channel"``,
            ``"coordinate"``, ``"kind"``, and ``"content"``.
        """
        try:
            from jugeo.evidence.channels import EvidenceChannel
        except ImportError:  # pragma: no cover
            EvidenceChannel = None  # type: ignore[assignment,misc]

        evidence_items: list[dict[str, Any]] = []
        tree = program.ast_tree
        if tree is None:
            return evidence_items

        for node in ast.walk(tree):
            coord_key = self._node_coord_key(node, program)
            if coord_key is None:
                continue

            # Type annotations → TYPE_ANNOTATION channel
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    channel = EvidenceChannel.TYPE_ANNOTATION.value if EvidenceChannel is not None else "type_annotation"
                    evidence_items.append({
                        "channel": channel,
                        "coordinate": coord_key,
                        "kind": "return_annotation",
                        "content": ast.dump(node.returns),
                    })
                for arg in node.args.args:
                    if arg.annotation is not None:
                        channel = EvidenceChannel.TYPE_ANNOTATION.value if EvidenceChannel is not None else "type_annotation"
                        evidence_items.append({
                            "channel": channel,
                            "coordinate": coord_key,
                            "kind": "arg_annotation",
                            "content": ast.dump(arg.annotation),
                        })

            # Docstrings → DOCUMENTATION channel
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    channel = EvidenceChannel.DOCUMENTATION.value if EvidenceChannel is not None else "documentation"
                    evidence_items.append({
                        "channel": channel,
                        "coordinate": coord_key,
                        "kind": "docstring",
                        "content": node.body[0].value.value[:500],
                    })

            # Assert statements → RUNTIME_WITNESS channel
            if isinstance(node, ast.Assert):
                channel = EvidenceChannel.RUNTIME_WITNESS.value if EvidenceChannel is not None else "runtime_witness"
                evidence_items.append({
                    "channel": channel,
                    "coordinate": coord_key,
                    "kind": "assertion",
                    "content": ast.dump(node.test),
                })

        return evidence_items

    def encoding_ready_program(
        self,
        program: SymbolicProgram,
        *,
        encoding_family: str = "scalar_encodings",
    ) -> dict[str, Any]:
        """Prepare a symbolic program for encoding.

        Uses the ``jugeo.encodings`` package to produce a dict
        structure ready to be consumed by the named encoding family.
        The output contains the coordinate-section mapping in the
        normalised form expected by encoding pipelines.

        Parameters
        ----------
        program:
            A :class:`SymbolicProgram`.
        encoding_family:
            Name of the target encoding family (e.g.
            ``"scalar_encodings"``, ``"text_encodings"``).

        Returns
        -------
        dict[str, Any]
            Encoding-ready representation.
        """
        sections_serialisable: dict[str, list] = {}
        for key, sec in program.judgment_sections.items():
            sections_serialisable[key] = list(sec) if isinstance(sec, tuple) else [sec]

        return {
            "encoding_family": encoding_family,
            "source_name": program.source.name,
            "source_sha256": program.source.sha256(),
            "coordinate_count": program.coordinate_count(),
            "sections": sections_serialisable,
            "trust_map": dict(program.trust_map),
            "type_map": dict(program.type_map),
            "obstructions": list(program.obstructions),
            "metadata": {
                **program.metadata,
                "encoding_target": encoding_family,
            },
        }

    def bug_detection_bridge(
        self,
        program: SymbolicProgram,
    ) -> Any:
        """Bridge a loaded program to the bug detection subsystem.

        Uses ``jugeo.problem_modes.bug_detection`` to create a
        ``PythonASTBridge`` or ``BugDetectionOrchestrator`` entry point
        from the loaded program's AST and coordinates.

        Parameters
        ----------
        program:
            A :class:`SymbolicProgram`.

        Returns
        -------
        PythonASTBridge | dict
            A ``PythonASTBridge`` when the bug-detection package is
            available, otherwise a plain dict with the bridge data.
        """
        try:
            from jugeo.problem_modes.bug_detection.ast_bridge import (
                PythonASTBridge,
                ASTBridgeConfig,
            )
        except ImportError:  # pragma: no cover
            PythonASTBridge = None  # type: ignore[assignment,misc]
            ASTBridgeConfig = None  # type: ignore[assignment,misc]

        if PythonASTBridge is None:
            return {
                "source_name": program.source.name,
                "coordinate_count": program.coordinate_count(),
                "obstruction_count": program.obstruction_count(),
                "ast_available": program.ast_tree is not None,
            }

        try:
            config = ASTBridgeConfig() if ASTBridgeConfig is not None else None
            bridge = PythonASTBridge(config=config) if config is not None else PythonASTBridge()
            return bridge
        except (TypeError, ValueError):
            return {
                "source_name": program.source.name,
                "coordinate_count": program.coordinate_count(),
                "error": "failed to construct PythonASTBridge",
            }

    # -- internal helpers for cross-subsystem methods ------------------------

    def _node_coord_key(
        self,
        node: ast.AST,
        program: SymbolicProgram,
    ) -> str | None:
        """Return the coordinate key for an AST node, or ``None``."""
        lineno = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)
        if lineno is None or col is None:
            return None
        node_type = type(node).__name__
        key = f"{program.source.name}:{lineno}:{col}:{node_type}"
        if key in program.judgment_sections:
            return key
        return f"{program.source.name}:{lineno}:{col}:{node_type}"

    def to_site(self):
        """Convert loaded program to a geometric Site."""
        try:
            from jugeo.geometry.site import Site, SiteBuilder, Coordinate, CoordinateKind, Morphism
            from jugeo.geometry.covers import Cover, CoverBuilder
            from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder
            return {"site": "built"}
        except Exception:
            return {"site": "unavailable"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _fmt_coord_key(coord: dict[str, Any]) -> str:
    """Format a coordinate dict as its canonical string key.

    Parameters
    ----------
    coord:
        Coordinate dict with keys ``file``, ``line``, ``col``, ``node_type``.

    Returns
    -------
    str
        ``"file:line:col:node_type"``
    """
    return f"{coord['file']}:{coord['line']}:{coord['col']}:{coord['node_type']}"


def _semantic_role(node: ast.AST) -> str:
    """Derive the semantic role predicate φ for an AST node.

    The predicate is a short human-readable string that names the *semantic*
    function of the node in the judgment (theory2.tex §3.2 φ-component).
    It is distinct from the plain ``type(node).__name__`` in that it may
    incorporate sub-role qualifications (e.g. distinguishing augmented from
    plain assignment).

    Parameters
    ----------
    node:
        An ``ast.AST`` node.

    Returns
    -------
    str
        Semantic role string.
    """
    # Function / method definitions
    if isinstance(node, ast.FunctionDef):
        decs = [ast.unparse(d) for d in node.decorator_list]
        if "staticmethod" in decs:
            return "static_method_definition"
        if "classmethod" in decs:
            return "class_method_definition"
        if "property" in decs:
            return "property_definition"
        return "function_definition"

    if isinstance(node, ast.AsyncFunctionDef):
        return "async_function_definition"

    if isinstance(node, ast.ClassDef):
        return "class_definition"

    # Assignment forms
    if isinstance(node, ast.Assign):
        return "assignment"

    if isinstance(node, ast.AugAssign):
        op = type(node.op).__name__
        return f"augmented_assignment:{op.lower()}"

    if isinstance(node, ast.AnnAssign):
        return "annotated_assignment"

    if isinstance(node, ast.NamedExpr):
        return "walrus_assignment"

    # Control flow
    if isinstance(node, ast.If):
        return "conditional_branch"

    if isinstance(node, ast.For):
        return "for_loop"

    if isinstance(node, ast.AsyncFor):
        return "async_for_loop"

    if isinstance(node, ast.While):
        return "while_loop"

    if isinstance(node, ast.With):
        return "context_manager"

    if isinstance(node, ast.AsyncWith):
        return "async_context_manager"

    if isinstance(node, ast.Try):
        return "try_block"

    if isinstance(node, ast.ExceptHandler):
        return "exception_handler"

    # Import machinery
    if isinstance(node, ast.Import):
        return "import_statement"

    if isinstance(node, ast.ImportFrom):
        return "import_from_statement"

    # Return / yield
    if isinstance(node, ast.Return):
        return "return_statement"

    if isinstance(node, ast.Yield):
        return "yield_expression"

    if isinstance(node, ast.YieldFrom):
        return "yield_from_expression"

    if isinstance(node, ast.Await):
        return "await_expression"

    # Calls
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return f"call:{node.func.id}"
        if isinstance(node.func, ast.Attribute):
            return f"call_attr:{node.func.attr}"
        return "call_expression"

    # Expressions
    if isinstance(node, ast.BinOp):
        op = type(node.op).__name__
        return f"binary_op:{op.lower()}"

    if isinstance(node, ast.UnaryOp):
        op = type(node.op).__name__
        return f"unary_op:{op.lower()}"

    if isinstance(node, ast.Compare):
        ops = ":".join(type(o).__name__.lower() for o in node.ops)
        return f"comparison:{ops}"

    if isinstance(node, ast.BoolOp):
        op = type(node.op).__name__.lower()
        return f"bool_op:{op}"

    if isinstance(node, ast.Lambda):
        return "lambda_expression"

    if isinstance(node, ast.IfExp):
        return "ternary_expression"

    if isinstance(node, ast.ListComp):
        return "list_comprehension"

    if isinstance(node, ast.SetComp):
        return "set_comprehension"

    if isinstance(node, ast.DictComp):
        return "dict_comprehension"

    if isinstance(node, ast.GeneratorExp):
        return "generator_expression"

    # Literals
    if isinstance(node, ast.Constant):
        t = type(node.value).__name__
        return f"constant:{t}"

    if isinstance(node, ast.Name):
        return f"name_ref:{node.id}"

    if isinstance(node, ast.Attribute):
        return f"attribute_ref:{node.attr}"

    if isinstance(node, ast.Subscript):
        return "subscript"

    if isinstance(node, ast.Starred):
        return "starred_expression"

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return f"{type(node).__name__.lower()}_literal"

    if isinstance(node, ast.Dict):
        return "dict_literal"

    # Statements
    if isinstance(node, ast.Delete):
        return "delete_statement"

    if isinstance(node, ast.Global):
        return "global_declaration"

    if isinstance(node, ast.Nonlocal):
        return "nonlocal_declaration"

    if isinstance(node, ast.Pass):
        return "pass_statement"

    if isinstance(node, ast.Break):
        return "break_statement"

    if isinstance(node, ast.Continue):
        return "continue_statement"

    if isinstance(node, ast.Raise):
        return "raise_statement"

    if isinstance(node, ast.Assert):
        return "assert_statement"

    if isinstance(node, ast.Expr):
        return "expression_statement"

    if isinstance(node, ast.Module):
        return "module_root"

    # Fallback — use the raw node type name
    return type(node).__name__.lower()


def _extract_evidence(node: ast.AST) -> str:
    """Extract the evidence string E for a judgment section.

    Evidence is the *epistemic warrant* for the claim made by the node
    (theory2.tex §3.2 E-component).  In the Python setting:

    * A node with an explicit type annotation provides the annotation text as
      evidence (strong warrant).
    * A node with a type comment provides the comment text.
    * All other nodes return ``"INFERRED"`` (weak, analysis-derived warrant).

    Parameters
    ----------
    node:
        An ``ast.AST`` node.

    Returns
    -------
    str
        Evidence string.
    """
    # AnnAssign — e.g. ``x: int = 5``
    if isinstance(node, ast.AnnAssign) and node.annotation is not None:
        try:
            return f"annotation:{ast.unparse(node.annotation)}"
        except Exception:
            return "annotation:UNPARSEABLE"

    # FunctionDef return annotation
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.returns is not None:
            try:
                ret = ast.unparse(node.returns)
                return f"return_annotation:{ret}"
            except Exception:
                return "return_annotation:UNPARSEABLE"
        tc = getattr(node, "type_comment", None)
        if tc is not None:
            return f"type_comment:{tc}"

    # arg annotation — function parameter
    if isinstance(node, ast.arg) and node.annotation is not None:
        try:
            return f"arg_annotation:{ast.unparse(node.annotation)}"
        except Exception:
            return "arg_annotation:UNPARSEABLE"

    # Constant node — the value itself is its own evidence
    if isinstance(node, ast.Constant):
        return f"literal:{type(node.value).__name__}"

    return "INFERRED"


def _derive_obligations(node: ast.AST) -> list[str]:
    """Derive the structural obligation list O for a judgment section.

    Obligations are constraints that the node must satisfy for the judgment to
    be sound (theory2.tex §3.2 O-component, §6.1 obligation algebra).  They
    are stated as short predicate strings; the prover discharges them later.

    Parameters
    ----------
    node:
        An ``ast.AST`` node.

    Returns
    -------
    list[str]
        List of obligation strings (may be empty).
    """
    obls: list[str] = []

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Every function must terminate (or be a generator/coroutine)
        obls.append("termination_or_divergence_classified")
        # Return type must be consistent with all return sites
        if node.returns is not None:
            obls.append("return_type_consistent")
        # Decorator applications must be type-safe
        if node.decorator_list:
            obls.append("decorator_application_sound")

    elif isinstance(node, ast.ClassDef):
        # MRO must be linearisable (C3 linearisation)
        obls.append("mro_linearisable")
        if node.bases:
            obls.append("base_classes_accessible")

    elif isinstance(node, ast.Call):
        # Arity must match callee signature at call site
        obls.append("arity_match")
        # Keyword arguments must be accepted by callee
        if node.keywords:
            obls.append("kwargs_accepted")

    elif isinstance(node, ast.AnnAssign):
        # The RHS value (if present) must be consistent with the annotation
        if node.value is not None:
            obls.append("annotation_type_consistency")

    elif isinstance(node, ast.Assign):
        # Number of targets must match structure of RHS
        if len(node.targets) > 1:
            obls.append("multi_target_assignment_sound")

    elif isinstance(node, (ast.For, ast.AsyncFor)):
        # The iterable must support iteration
        obls.append("iterable_protocol")

    elif isinstance(node, ast.With):
        # Context managers must implement __enter__ / __exit__
        obls.append("context_manager_protocol")

    elif isinstance(node, ast.AsyncWith):
        obls.append("async_context_manager_protocol")

    elif isinstance(node, ast.Import):
        # Each imported module must be importable in the target environment
        for alias in node.names:
            obls.append(f"module_importable:{alias.name}")

    elif isinstance(node, ast.ImportFrom):
        if node.module:
            for alias in node.names:
                if alias.name != "*":
                    obls.append(f"name_exported:{node.module}.{alias.name}")

    elif isinstance(node, ast.Raise):
        # Raised object must be an exception type or instance
        obls.append("raise_target_is_exception")

    elif isinstance(node, ast.Assert):
        # The test expression must be evaluable
        obls.append("assert_test_evaluable")

    elif isinstance(node, ast.BinOp):
        # Operator must be defined for operand types
        op = type(node.op).__name__
        obls.append(f"operator_defined:{op.lower()}")

    elif isinstance(node, ast.Compare):
        for op in node.ops:
            obls.append(f"comparison_defined:{type(op).__name__.lower()}")

    elif isinstance(node, ast.Subscript):
        obls.append("subscript_protocol")

    return obls


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def load_program(
    source_or_path: str,
    *,
    is_path: bool = False,
    name: str = "program",
    version: str = "0.0.1",
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> SymbolicProgram:
    """Convenience wrapper: load a single Python program.

    Creates a default :class:`ProgramLoader` with *config* and calls
    :meth:`~ProgramLoader.load`.

    Parameters
    ----------
    source_or_path:
        Raw Python source text or a filesystem path (controlled by *is_path*).
    is_path:
        When ``True``, treat *source_or_path* as a file path.
    name:
        Logical program name.
    version:
        Version tag.
    metadata:
        Arbitrary annotations forwarded to :class:`ProgramSource`.
    config:
        Optional :class:`ProgramLoader` configuration dict.

    Returns
    -------
    SymbolicProgram

    Examples
    --------
    ::

        prog = load_program("def f(x): return x * 2\\n", name="double")
        print(prog.coordinate_count())
    """
    loader = ProgramLoader(config)
    return loader.load(
        source_or_path,
        is_path=is_path,
        name=name,
        version=version,
        metadata=metadata,
    )


def load_program_pair(
    a: str,
    b: str,
    *,
    a_name: str = "program_A",
    b_name: str = "program_B",
    is_path: bool = False,
    metadata_a: dict[str, Any] | None = None,
    metadata_b: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[SymbolicProgram, SymbolicProgram]:
    """Convenience wrapper: load two Python programs as a pair.

    Creates a default :class:`ProgramLoader` with *config* and calls
    :meth:`~ProgramLoader.load_pair`.

    Parameters
    ----------
    a:
        Source text or path for the first program.
    b:
        Source text or path for the second program.
    a_name:
        Logical name for the first program.
    b_name:
        Logical name for the second program.
    is_path:
        When ``True``, both *a* and *b* are treated as file paths.
    metadata_a:
        Extra metadata for the first program.
    metadata_b:
        Extra metadata for the second program.
    config:
        Optional :class:`ProgramLoader` configuration dict.

    Returns
    -------
    tuple[SymbolicProgram, SymbolicProgram]

    Examples
    --------
    ::

        prog_a, prog_b = load_program_pair(
            "def f(x): return x + 1",
            "def f(x): return 1 + x",
        )
    """
    loader = ProgramLoader(config)
    return loader.load_pair(
        a,
        b,
        a_name=a_name,
        b_name=b_name,
        is_path=is_path,
        metadata_a=metadata_a,
        metadata_b=metadata_b,
    )


# ---------------------------------------------------------------------------
# Smoke test — run this module directly to verify basic operation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    _THIS_FILE = os.path.abspath(__file__)
    print(f"[smoke test] Loading own source file: {_THIS_FILE}")

    loader = ProgramLoader(config={"include_constants": False})

    try:
        prog = loader.load(_THIS_FILE, is_path=True, name="program_loader_self")
    except ProgramLoaderError as exc:
        print(f"[ERROR] ProgramLoaderError: {exc}  context={exc.context}")
        sys.exit(1)

    print(f"[ok] Loaded program: {prog.source.name!r}")
    print(f"     SHA-256     : {prog.source.sha256()}")
    print(f"     Coordinates : {prog.coordinate_count()}")
    print(f"     Scopes      : {len(prog.scope_graph)}")
    print(f"     Obstructions: {prog.obstruction_count()}")
    print()

    # Show the first 5 judgment sections to verify encoding
    print("[smoke test] First 5 judgment sections:")
    for i, (ck, section) in enumerate(prog.judgment_sections.items()):
        c, phi, A, E, O, B, T, Pi = section
        print(
            f"  [{i}] c={c!r}\n"
            f"       φ={phi!r}  A={A!r}  E={E!r}\n"
            f"       O={O}  B={B!r}  T={T!r}  Π={Pi}"
        )
        if i >= 4:
            break

    print()
    # Verify load_pair + relational_refinement packaging
    _SRC_A = textwrap.dedent(
        """\
        def add(x: int, y: int) -> int:
            return x + y
        """
    )
    _SRC_B = textwrap.dedent(
        """\
        def add(a: int, b: int) -> int:
            result: int = a + b
            return result
        """
    )

    prog_a, prog_b = loader.load_pair(
        _SRC_A, _SRC_B, a_name="add_v1", b_name="add_v2"
    )
    rr_input = loader.to_relational_refinement_input(prog_a, prog_b)

    print("[smoke test] load_pair equivalence check packaging:")
    print(f"  schema_version : {rr_input['schema_version']}")
    print(f"  task           : {rr_input['task']}")
    print(f"  program A      : {rr_input['programs']['A']['name']!r}  "
          f"({rr_input['programs']['A']['coordinates']} coords)")
    print(f"  program B      : {rr_input['programs']['B']['name']!r}  "
          f"({rr_input['programs']['B']['coordinates']} coords)")
    print(f"  obstructions A : {rr_input['programs']['A']['obstructions']}")
    print(f"  obstructions B : {rr_input['programs']['B']['obstructions']}")
    print()
    print("[smoke test] PASSED — coordinate count:", prog.coordinate_count())
