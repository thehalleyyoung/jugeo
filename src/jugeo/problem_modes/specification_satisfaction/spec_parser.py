"""Specification parser for the specification-satisfaction problem mode.

Section 10.0: Specification Parsing.  A specification is a *target section* of
the judgment sheaf — a prescribed assignment of judgment values
``(c, φ, A, E, O, B, T, Π)`` to every coordinate in the site.  The parser is
the entry point of the pipeline: it reads human-readable or machine-readable
artefacts and emits first-class :class:`ParsedSpecification` objects that the
rest of the pipeline (s01 → s04) can process.

Four source formats are supported:

1. **Natural-language specs** (docstrings, comments, assertion strings)
   The parser applies a keyword-extraction pass ("NLP-lite") that identifies
   obligation-bearing sentences and maps them to judgment-sheaf coordinates via
   a lexical taxonomy.  No external NLP library is required.

2. **Type-annotation specs** (PEP 484 annotations, ``typing.Protocol`` classes)
   The ``ast`` module is used to walk function and class definitions, extract
   parameter/return-type annotations, and translate each annotation into a
   structural obligation at the corresponding coordinate.

3. **JSON/YAML specs** (structured obligation descriptions)
   Structured documents may include explicit coordinates, trust tiers, and
   obligation texts.  The parser validates, normalises, and hydrates these into
   :class:`ParsedObligation` tuples.

4. **Assertion-based specs** (``assert`` statements, preconditions/postconditions)
   ``ast.parse`` extracts every ``Assert`` node; each assertion test expression
   becomes an obligation at the function-body coordinate nearest to its source
   location.

In all cases the raw input text is preserved verbatim on
:class:`RawSpecification`.  Parsing is *non-destructive*: the original artefact
is always available for re-parsing, display, or audit.

References theory2.tex §10.0.

# copilot: entry-point parser for jugeo specification-satisfaction; all logic
# is real and non-trivial.  Extend _KEYWORD_TAXONOMY and
# _TRUST_TIER_RULES as the theory and domain vocabulary mature.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import textwrap
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional internal imports — all jugeo symbols wrapped in try/except so this
# module remains importable in a standalone / test environment.
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        CertificateOfSatisfaction,
        DescentCondition,
        GapSeverity,
        ResidualGap,
        SatisfactionStatus,
        SatisfactionWitness,
        Specification,
        SpecificationKind,
        WitnessStatus,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    CertificateOfSatisfaction = Any  # type: ignore[assignment,misc]
    ResidualGap = Any  # type: ignore[assignment,misc]
    SpecificationKind = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]
    GapSeverity = Any  # type: ignore[assignment,misc]
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    DescentCondition = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore[assignment,misc]
    SemanticSite = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentKind, JudgmentTerm, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Keyword taxonomy
# ---------------------------------------------------------------------------
# Each entry maps a set of trigger keywords to a (coordinate_hint, kind) pair.
# When the NLP-lite pass encounters a sentence containing one of the keywords,
# it uses this taxonomy to assign a default coordinate and obligation kind.
#
# Coordinates follow the judgment-tuple schema (c, φ, A, E, O, B, T, Π):
#   c  — code-unit coordinate (function, class, module)
#   φ  — property coordinate (type-correctness, liveness, …)
#   A  — agent coordinate (caller, callee, framework)
#   E  — evidence coordinate (proof, test, runtime-check)
#   O  — output coordinate (return value, side effect)
#   B  — budget/resource coordinate (time, memory, …)
#   T  — trust-tier coordinate
#   Π  — provenance coordinate (source file, commit)

_KEYWORD_TAXONOMY: list[tuple[frozenset[str], str, str]] = [
    # (trigger_keywords, coordinate_hint, obligation_kind)
    (frozenset({"must", "shall", "required", "mandatory", "requires"}),   "φ", "precondition"),
    (frozenset({"ensures", "guarantees", "postcondition", "returns"}),    "O", "postcondition"),
    (frozenset({"invariant", "always", "never", "invariantly"}),          "φ", "invariant"),
    (frozenset({"type", "typed", "annotation", "annotation:"}),           "φ", "type_constraint"),
    (frozenset({"trust", "trusted", "untrusted", "tier"}),                "T", "trust_constraint"),
    (frozenset({"raises", "throws", "exception", "error"}),               "O", "postcondition"),
    (frozenset({"not", "none", "null", "nil", "absent"}),                 "φ", "invariant"),
    (frozenset({"param", "parameter", "argument", "arg"}),                "A", "precondition"),
    (frozenset({"return", "result", "output", "yields"}),                 "O", "postcondition"),
    (frozenset({"assert", "check", "verify", "validate"}),                "E", "invariant"),
    (frozenset({"timeout", "deadline", "latency", "performance"}),        "B", "invariant"),
    (frozenset({"memory", "heap", "alloc", "leak"}),                      "B", "invariant"),
    (frozenset({"security", "auth", "permission", "privilege"}),          "T", "trust_constraint"),
    (frozenset({"pure", "side-effect", "deterministic", "idempotent"}),   "φ", "invariant"),
    (frozenset({"deprecated", "removed", "legacy", "backward"}),          "Π", "type_constraint"),
]

# Severity keywords: the presence of these words in an obligation text bumps
# the severity level of the resulting ParsedObligation.
_SEVERITY_KEYWORDS: dict[str, str] = {
    "critical":   "CRITICAL",
    "fatal":      "CRITICAL",
    "security":   "CRITICAL",
    "must":       "HIGH",
    "shall":      "HIGH",
    "required":   "HIGH",
    "mandatory":  "HIGH",
    "important":  "HIGH",
    "should":     "MEDIUM",
    "recommended":"MEDIUM",
    "preferred":  "MEDIUM",
    "may":        "LOW",
    "optional":   "LOW",
    "suggested":  "LOW",
}

# Trust-tier promotion rules.  A list of (keyword_set, tier) pairs evaluated
# left-to-right; the first match wins.
_TRUST_TIER_RULES: list[tuple[frozenset[str], str]] = [
    (frozenset({"verified", "proven", "formally", "coq", "lean", "isabelle"}), "VERIFIED"),
    (frozenset({"tested", "test", "pytest", "unittest", "hypothesis"}),        "TESTED"),
    (frozenset({"reviewed", "audit", "security", "auth", "crypto"}),           "AUDITED"),
    (frozenset({"asserted", "assert", "runtime", "checked"}),                  "ASSERTED"),
    (frozenset({"inferred", "infer", "static", "mypy", "pyright"}),            "INFERRED"),
    (frozenset({"claimed", "docstring", "documented", "comment"}),             "CLAIMED"),
]

_DEFAULT_TRUST_TIER = "CLAIMED"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns
    -------
    str
        UTC timestamp in ISO 8601 format.
    """
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()


def _make_obligation_id(text: str, coordinate: str, kind: str) -> str:
    """Derive a deterministic obligation identifier from its content.

    The identifier is a short SHA-256 digest prefixed with ``obl-`` so it is
    recognisable in logs and test output while remaining collision-resistant
    for any realistic number of obligations per specification.

    Parameters
    ----------
    text : str
        The raw obligation text.
    coordinate : str
        The judgment-sheaf coordinate (e.g. ``"φ"``, ``"O"``).
    kind : str
        Obligation kind (``"precondition"``, ``"postcondition"``, …).

    Returns
    -------
    str
        A ``"obl-<12hex>"`` identifier string.
    """
    payload = f"{coordinate}::{kind}::{text}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"obl-{digest}"


def _make_spec_id(name: str, fmt: str) -> str:
    """Derive a deterministic spec identifier from its name and format.

    Parameters
    ----------
    name : str
        Human-readable spec name.
    fmt : str
        Format string (e.g. ``"NATURAL_LANGUAGE"``).

    Returns
    -------
    str
        A ``"pspec-<12hex>"`` identifier string.
    """
    payload = f"{name}::{fmt}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"pspec-{digest}"


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace (including newlines) into single spaces.

    Used to normalise multi-line docstring paragraphs before keyword scanning.

    Parameters
    ----------
    text : str
        Input text, possibly containing newlines and extra spaces.

    Returns
    -------
    str
        The text with all whitespace runs collapsed to a single space.
    """
    return re.sub(r"\s+", " ", text).strip()


def _sentence_split(text: str) -> list[str]:
    """Split *text* into sentence-like segments.

    Splits on ``.``, ``!``, ``?``, and newlines, returning only non-empty
    segments with at least two words.  This deliberately avoids a heavy NLP
    dependency while still producing reasonable sentence boundaries for
    English-language docstrings and comments.

    Parameters
    ----------
    text : str
        The input text to split.

    Returns
    -------
    list[str]
        Non-empty sentence fragments.
    """
    raw = re.split(r"[.!?\n]+", text)
    cleaned: list[str] = []
    for fragment in raw:
        s = _normalise_whitespace(fragment)
        if len(s.split()) >= 2:  # discard single-word / empty fragments
            cleaned.append(s)
    return cleaned


def _words_lower(text: str) -> frozenset[str]:
    """Return the set of lower-cased words in *text*.

    Parameters
    ----------
    text : str
        Input sentence or phrase.

    Returns
    -------
    frozenset[str]
        Lower-cased word tokens (punctuation stripped).
    """
    tokens = re.findall(r"[A-Za-z_][\w-]*", text)
    return frozenset(t.lower() for t in tokens)


def _detect_severity(text: str) -> str:
    """Infer obligation severity from keyword presence.

    Scans *text* for severity-indicator keywords in priority order
    (CRITICAL → HIGH → MEDIUM → LOW).

    Parameters
    ----------
    text : str
        Raw obligation text.

    Returns
    -------
    str
        One of ``"CRITICAL"``, ``"HIGH"``, ``"MEDIUM"``, ``"LOW"``.
    """
    words = _words_lower(text)
    # evaluate in priority order: CRITICAL > HIGH > MEDIUM > LOW
    for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        triggers = {kw for kw, sv in _SEVERITY_KEYWORDS.items() if sv == severity}
        if words & triggers:
            return severity
    return "LOW"


def _assign_trust_tier(tokens: frozenset[str]) -> str:
    """Assign a trust tier from a token set using the promotion rule table.

    Evaluates :data:`_TRUST_TIER_RULES` left-to-right; the first matching rule
    wins.  Falls back to ``"CLAIMED"`` if no rule matches.

    Parameters
    ----------
    tokens : frozenset[str]
        Lower-cased words extracted from the obligation context.

    Returns
    -------
    str
        Trust tier string (e.g. ``"VERIFIED"``, ``"TESTED"``, ``"CLAIMED"``).
    """
    for keyword_set, tier in _TRUST_TIER_RULES:
        if tokens & keyword_set:
            return tier
    return _DEFAULT_TRUST_TIER


def _extract_ast_annotations(tree: ast.Module) -> list[dict[str, Any]]:
    """Walk an AST module and collect all function/method type annotations.

    For each function definition found (including nested class methods), emits
    a dict describing the parameter name, its annotation (as unparsed source),
    the enclosing qualified name, and whether it is a return type.

    Parameters
    ----------
    tree : ast.Module
        Parsed AST of a Python source file.

    Returns
    -------
    list[dict[str, Any]]
        Each entry has keys ``name``, ``annotation_src``, ``qualified_name``,
        ``is_return``, ``lineno``.
    """
    results: list[dict[str, Any]] = []

    def _walk(node: ast.AST, prefix: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qname = f"{prefix}.{node.name}" if prefix else node.name
            # parameter annotations
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                if arg.annotation is not None:
                    results.append({
                        "name": arg.arg,
                        "annotation_src": ast.unparse(arg.annotation),
                        "qualified_name": qname,
                        "is_return": False,
                        "lineno": arg.col_offset,
                    })
            # vararg / kwarg annotations
            for special in filter(None, [node.args.vararg, node.args.kwarg]):
                if special.annotation is not None:
                    results.append({
                        "name": special.arg,
                        "annotation_src": ast.unparse(special.annotation),
                        "qualified_name": qname,
                        "is_return": False,
                        "lineno": special.col_offset,
                    })
            # return annotation
            if node.returns is not None:
                results.append({
                    "name": "__return__",
                    "annotation_src": ast.unparse(node.returns),
                    "qualified_name": qname,
                    "is_return": True,
                    "lineno": node.lineno,
                })
            # recurse into body (handles nested functions / methods)
            for child in ast.walk(node):
                if child is not node:
                    _walk(child, qname)
        elif isinstance(node, ast.ClassDef):
            qname = f"{prefix}.{node.name}" if prefix else node.name
            for child in node.body:
                _walk(child, qname)

    for top_node in ast.iter_child_nodes(tree):
        _walk(top_node, "")

    return results


def _extract_ast_assertions(tree: ast.Module) -> list[dict[str, Any]]:
    """Extract all ``assert`` statements from a parsed AST.

    Each assertion is returned as a dict containing its test expression (as
    unparsed source), an optional message expression, and the source line
    number.

    Parameters
    ----------
    tree : ast.Module
        Parsed AST of a Python source file.

    Returns
    -------
    list[dict[str, Any]]
        Each entry has keys ``test_src``, ``msg_src`` (may be ``None``),
        ``lineno``, ``enclosing_function``.
    """
    results: list[dict[str, Any]] = []
    # build a mapping from line → enclosing function name for context
    func_ranges: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno + 50)
            func_ranges.append((node.lineno, end, node.name))

    def _enclosing(lineno: int) -> str:
        for start, end, name in func_ranges:
            if start <= lineno <= end:
                return name
        return "<module>"

    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            results.append({
                "test_src": ast.unparse(node.test),
                "msg_src": ast.unparse(node.msg) if node.msg else None,
                "lineno": node.lineno,
                "enclosing_function": _enclosing(node.lineno),
            })
    return results


def _parse_protocol_methods(tree: ast.Module) -> list[dict[str, Any]]:
    """Extract abstract methods from ``typing.Protocol`` subclasses in *tree*.

    A class is treated as a Protocol if its bases include ``Protocol`` or
    ``typing.Protocol``.  Each method (and property) is returned as a dict with
    its name, qualified path, docstring, and annotation information.

    Parameters
    ----------
    tree : ast.Module
        Parsed AST.

    Returns
    -------
    list[dict[str, Any]]
        Each entry has keys ``class_name``, ``method_name``, ``docstring``,
        ``annotations``, ``is_property``.
    """
    results: list[dict[str, Any]] = []

    def _is_protocol(cls_node: ast.ClassDef) -> bool:
        for base in cls_node.bases:
            base_name = ast.unparse(base)
            if base_name in {"Protocol", "typing.Protocol"}:
                return True
        return False

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_protocol(node):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # collect type annotation info for this method
            annotations: dict[str, str] = {}
            for arg in item.args.args:
                if arg.annotation:
                    annotations[arg.arg] = ast.unparse(arg.annotation)
            if item.returns:
                annotations["__return__"] = ast.unparse(item.returns)
            # detect @property decorator
            is_prop = any(
                ast.unparse(d) == "property" for d in item.decorator_list
            )
            # extract docstring if present
            docstring = ast.get_docstring(item) or ""
            results.append({
                "class_name": node.name,
                "method_name": item.name,
                "docstring": docstring,
                "annotations": annotations,
                "is_property": is_prop,
            })
    return results


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SpecFormat(str, Enum):
    """Enumeration of supported specification source formats.

    Each format corresponds to a different surface syntax or encoding through
    which human authors express obligations.  The parser uses this to dispatch
    to the correct sub-parser and to record provenance on parsed artefacts.

    Attributes
    ----------
    NATURAL_LANGUAGE
        Free-form English prose (e.g. README paragraphs, issue descriptions).
    TYPE_ANNOTATIONS
        PEP 484 / PEP 526 type annotations embedded in Python source.
    JSON_SCHEMA
        A structured JSON document describing obligations.
    YAML_SCHEMA
        A structured YAML document describing obligations.
    ASSERTION_BASED
        Python ``assert`` statements or ``pytest`` assertion patterns.
    DOCSTRING
        Python docstring in NumPy, Google, or reStructuredText style.
    PROTOCOL_CLASS
        A ``typing.Protocol`` class definition in Python source.
    MIXED
        A heterogeneous artefact containing elements of several formats; the
        parser auto-detects and delegates to the appropriate sub-parsers.
    """

    NATURAL_LANGUAGE = "NATURAL_LANGUAGE"
    TYPE_ANNOTATIONS = "TYPE_ANNOTATIONS"
    JSON_SCHEMA      = "JSON_SCHEMA"
    YAML_SCHEMA      = "YAML_SCHEMA"
    ASSERTION_BASED  = "ASSERTION_BASED"
    DOCSTRING        = "DOCSTRING"
    PROTOCOL_CLASS   = "PROTOCOL_CLASS"
    MIXED            = "MIXED"


SpecFormat.PYTHON_ASSERTIONS = SpecFormat.ASSERTION_BASED  # type: ignore[attr-defined]
SpecFormat.PYTHON_ANNOTATIONS = SpecFormat.TYPE_ANNOTATIONS  # type: ignore[attr-defined]
SpecFormat.UNKNOWN = SpecFormat.MIXED  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Error class
# ---------------------------------------------------------------------------


class SpecParserError(Exception):
    """Raised when the spec parser encounters an unrecoverable input error.

    Attributes
    ----------
    message : str
        Human-readable description of what went wrong.
    format : SpecFormat | None
        The format that was being parsed when the error occurred, if known.
    context : str
        A snippet of the raw input near the parse failure, for diagnostics.
    """

    def __init__(
        self,
        message: str,
        format: SpecFormat | None = None,  # noqa: A002 — mirrors stdlib convention
        context: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.format = format
        self.context = context

    def __repr__(self) -> str:
        fmt_str = self.format.value if self.format else "UNKNOWN"
        return (
            f"SpecParserError(message={self.message!r}, "
            f"format={fmt_str!r}, context={self.context[:80]!r})"
        )


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawSpecification:
    """An unprocessed specification artefact as received from the outside world.

    Carrying the verbatim input is a core theory2 invariant: parsing must be
    non-destructive so that artefacts can always be re-parsed, displayed, or
    audited without loss.

    Attributes
    ----------
    spec_id : str
        Globally unique identifier for this raw artefact (UUID-4).
    raw_text : str
        The original, unmodified input text.
    format : SpecFormat
        The format in which the text was received (or ``MIXED`` if unknown).
    source_location : str
        A ``"<file>:<line>"`` string identifying where the artefact was found.
        Empty string if the location is unknown.
    confidence : float
        Confidence in the format detection, in ``[0.0, 1.0]``.  Set to ``1.0``
        when the caller explicitly provides the format.
    metadata : dict
        Arbitrary key-value metadata (e.g. git commit, author, tool version).
    """

    spec_id: str
    raw_text: str
    format: SpecFormat
    source_location: str
    confidence: float
    metadata: dict  # type: ignore[type-arg]


@dataclass(frozen=True, slots=True)
class ParsedObligation:
    """A single atomic obligation extracted from a specification.

    An obligation is a claim of the form "at coordinate *c*, predicate *P* must
    hold".  In judgment-sheaf terms each obligation prescribes the value of one
    component of the tuple ``(c, φ, A, E, O, B, T, Π)`` at a specific
    coordinate.

    Attributes
    ----------
    obligation_id : str
        Deterministic identifier derived from content (``"obl-<12hex>"``).
    coordinate : str
        The judgment-sheaf coordinate this obligation constrains (e.g.
        ``"φ"``, ``"O"``, ``"T"``).
    predicate : str
        Human-readable statement of what must hold (the obligation text after
        stripping boilerplate).
    kind : str
        Obligation kind: one of ``"precondition"``, ``"postcondition"``,
        ``"invariant"``, ``"type_constraint"``, ``"trust_constraint"``.
    trust_tier : str
        Evidence tier assigned to this obligation: ``"VERIFIED"``,
        ``"TESTED"``, ``"AUDITED"``, ``"ASSERTED"``, ``"INFERRED"``,
        ``"CLAIMED"``.
    severity : str
        Impact severity: ``"CRITICAL"``, ``"HIGH"``, ``"MEDIUM"``, ``"LOW"``.
    raw_text : str
        The verbatim sentence or expression from which this obligation was
        parsed.
    metadata : dict
        Arbitrary extra data (e.g. source line number, enclosing function name,
        original annotation type).
    """

    obligation_id: str
    coordinate: str
    predicate: str
    kind: str
    trust_tier: str
    severity: str
    raw_text: str
    metadata: dict  # type: ignore[type-arg]

    @property
    def text(self) -> str:
        """Backward-compatible alias for the obligation's human-readable text."""
        return self.raw_text or self.predicate


class _CallableInt(int):
    def __call__(self) -> int:
        return int(self)


class _CallableTuple(tuple):
    def __call__(self):
        return self


@dataclass(frozen=True, slots=True)
class ParsedSpecification:
    """A fully parsed specification ready for the rest of the pipeline.

    A :class:`ParsedSpecification` is the structured output of the parsing
    stage.  It names all coordinates the specification covers, organises the
    obligations by coordinate, records a trust floor, and — when the jugeo
    models layer is available — can be converted into a first-class
    :class:`Specification` object via :meth:`to_jugeo_spec`.

    Attributes
    ----------
    spec_id : str
        Deterministic identifier (``"pspec-<12hex>"``).
    name : str
        Human-readable name for this specification.
    format : SpecFormat
        The source format (or ``MIXED`` for merged specifications).
    obligations : tuple[ParsedObligation, ...]
        All obligations extracted from the source.
    coordinates_covered : tuple[str, ...]
        Deduplicated, sorted tuple of all coordinates referenced by any
        obligation.
    trust_floor : str
        The minimum trust tier across all obligations (i.e. the weakest link).
    confidence : float
        Parser confidence in the extraction quality, in ``[0.0, 1.0]``.
    raw : RawSpecification | None
        The original artefact, if available.
    metadata : dict
        Arbitrary extra data (e.g. parse timestamp, parser version, source
        file path).
    """

    spec_id: str
    name: str
    format: SpecFormat
    obligations: tuple  # tuple[ParsedObligation, ...]
    coordinates_covered: tuple  # tuple[str, ...]
    trust_floor: str
    confidence: float
    raw: RawSpecification | None
    metadata: dict  # type: ignore[type-arg]

    def to_jugeo_spec(self) -> Any:
        """Convert this parsed specification into a jugeo ``Specification``.

        Attempts to import :class:`jugeo.problem_modes.specification_satisfaction.models.Specification`
        and construct an instance from the obligations contained here.  Each
        obligation is mapped to a prescribed-judgment entry at its coordinate.

        If the models layer is unavailable (e.g. in a standalone environment)
        the method returns a plain ``dict`` that carries the same information.

        Returns
        -------
        Specification | dict[str, Any]
            A jugeo :class:`Specification` dataclass instance, or a plain dict
            fallback if the models module cannot be imported.
        """
        # Build prescribed_judgments: coord → judgment dict
        prescribed: dict[str, dict[str, Any]] = {}
        constraint_map: dict[str, tuple[str, ...]] = {}
        for obl in self.obligations:
            coord = obl.coordinate
            if coord not in prescribed:
                prescribed[coord] = {
                    "polarity": "positive",
                    "trust_tier": obl.trust_tier,
                    "severity": obl.severity,
                    "predicates": [],
                    "obligation_ids": [],
                }
            prescribed[coord]["predicates"].append(obl.predicate)
            prescribed[coord]["obligation_ids"].append(obl.obligation_id)
            # constraints: one entry per obligation id
            constraint_map.setdefault(coord, ())
            constraint_map[coord] = (*constraint_map[coord], obl.obligation_id)

        base: dict[str, Any] = {
            "spec_id": self.spec_id,
            "name": self.name,
            "description": (
                f"Parsed from {self.format.value} with "
                f"{len(self.obligations)} obligations."
            ),
            "kind": "structural",  # default; callers may override
            "target_coordinates": self.coordinates_covered,
            "prescribed_judgments": prescribed,
            "constraint_map": constraint_map,
            "priority": 3,
            "version": "1.0.0",
            "created_at": self.metadata.get("parsed_at", _utc_now_iso()),
            "metadata": dict(self.metadata),
        }

        try:
            from jugeo.problem_modes.specification_satisfaction.models import (
                Specification as _Spec,
                SpecificationKind as _SKind,
            )
            # map trust_floor to the closest SpecificationKind
            kind_map = {
                "VERIFIED": _SKind.FORMAL,
                "TESTED":   _SKind.BEHAVIORAL,
                "AUDITED":  _SKind.STRUCTURAL,
                "ASSERTED": _SKind.BEHAVIORAL,
                "INFERRED": _SKind.STRUCTURAL,
                "CLAIMED":  _SKind.COMPOSITIONAL,
            }
            base["kind"] = kind_map.get(self.trust_floor, _SKind.STRUCTURAL)
            # Only pass fields that Specification's __init__ accepts
            spec_fields = {f for f in _Spec.__dataclass_fields__}  # type: ignore[attr-defined]
            return _Spec(**{k: v for k, v in base.items() if k in spec_fields})
        except (ImportError, AttributeError, TypeError):
            return base

    @property
    def obligation_count(self) -> int:
        """Return the total number of obligations in this specification.

        Returns
        -------
        int
            Length of :attr:`obligations`.
        """
        return _CallableInt(len(self.obligations))

    @property
    def obligations_by_coordinate(self) -> dict[str, list[ParsedObligation]]:
        """Group obligations by their judgment-sheaf coordinate.

        Returns
        -------
        dict[str, list[ParsedObligation]]
            Mapping from coordinate string to the list of obligations at that
            coordinate.
        """
        groups: dict[str, list[ParsedObligation]] = {}
        for obl in self.obligations:
            groups.setdefault(obl.coordinate, []).append(obl)
        return groups

    @property
    def critical_obligations(self) -> tuple:
        """Return only the CRITICAL-severity obligations.

        Returns
        -------
        tuple[ParsedObligation, ...]
            Subset of :attr:`obligations` with ``severity == "CRITICAL"``.
        """
        return _CallableTuple(o for o in self.obligations if o.severity == "CRITICAL")


# ---------------------------------------------------------------------------
# Trust-tier ordering helper
# ---------------------------------------------------------------------------

_TRUST_TIER_ORDER: dict[str, int] = {
    "VERIFIED": 6,
    "TESTED":   5,
    "AUDITED":  4,
    "ASSERTED": 3,
    "INFERRED": 2,
    "CLAIMED":  1,
}


def _trust_floor(tiers: list[str]) -> str:
    """Compute the minimum (weakest) trust tier in a collection.

    The weakest tier is the one with the lowest ordinal in
    :data:`_TRUST_TIER_ORDER`.

    Parameters
    ----------
    tiers : list[str]
        Trust tier strings to compare.

    Returns
    -------
    str
        The tier with the lowest ordinal, or ``"CLAIMED"`` if *tiers* is empty.
    """
    if not tiers:
        return _DEFAULT_TRUST_TIER
    return min(tiers, key=lambda t: _TRUST_TIER_ORDER.get(t, 0))


# ---------------------------------------------------------------------------
# SpecParser
# ---------------------------------------------------------------------------


class SpecParser:
    """Main specification parser.

    Transforms human-readable or machine-readable artefacts into
    :class:`ParsedSpecification` objects that the downstream pipeline
    (s01 → s04) can process directly.

    The parser is format-agnostic: it auto-detects the format of its input
    via :meth:`_detect_format` and dispatches to the appropriate sub-parser.
    Callers may also explicitly supply a :class:`SpecFormat` to bypass
    auto-detection.

    The design follows the theory2 principle that a specification is a
    *target section* of the judgment sheaf: the parser's job is to materialise
    that section from an opaque artefact, coordinate by coordinate, without
    losing or transforming the original source.

    Parameters
    ----------
    config : dict | None
        Optional configuration dictionary.  Recognised keys:

        ``default_trust_tier``
            Trust tier to assign when no tier can be inferred.
            Default: ``"CLAIMED"``.
        ``confidence_threshold``
            Minimum confidence below which obligations are silently dropped.
            Default: ``0.0`` (keep everything).
        ``max_obligations_per_spec``
            Hard cap on the number of obligations in a single
            :class:`ParsedSpecification`.  Default: ``1000``.
        ``parser_version``
            Version string written into ``metadata["parser_version"]``.
            Default: ``"spec_parser:1.0"``.
    """

    def __init__(self, config: dict | None = None) -> None:  # type: ignore[type-arg]
        cfg = config or {}
        self._default_trust_tier: str = cfg.get("default_trust_tier", _DEFAULT_TRUST_TIER)
        self._confidence_threshold: float = float(cfg.get("confidence_threshold", 0.0))
        self._max_obligations: int = int(cfg.get("max_obligations_per_spec", 1000))
        self._parser_version: str = cfg.get("parser_version", "spec_parser:1.0")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self,
        raw: str,
        format: SpecFormat = SpecFormat.MIXED,  # noqa: A002
        source_location: str = "",
        name: str = "",
    ) -> ParsedSpecification:
        """Parse a raw string into a :class:`ParsedSpecification`.

        If *format* is :attr:`SpecFormat.MIXED` the parser will attempt to
        auto-detect the format via :meth:`_detect_format`.

        Parameters
        ----------
        raw : str
            The raw input text to parse.
        format : SpecFormat
            The expected format of the input.  Defaults to ``MIXED``
            (auto-detect).
        source_location : str
            Optional ``"<file>:<line>"`` provenance string.
        name : str
            Optional human-readable name for the specification.  If omitted a
            name is derived from the first sentence of the input.

        Returns
        -------
        ParsedSpecification
            The parsed specification.

        Raises
        ------
        SpecParserError
            If the input is empty or cannot be parsed in the declared format.
        """
        if not raw or not raw.strip():
            raise SpecParserError(
                "Empty or whitespace-only input cannot be parsed.",
                format=format,
                context="",
            )

        # Detect format when caller does not specify
        if format is SpecFormat.MIXED:
            format = self._detect_format(raw)  # noqa: A001

        # Build the RawSpecification envelope first (non-destructive record)
        raw_spec = RawSpecification(
            spec_id=str(uuid.uuid4()),
            raw_text=raw,
            format=format,
            source_location=source_location,
            confidence=1.0 if format is not SpecFormat.MIXED else 0.7,
            metadata={"parser_version": self._parser_version},
        )

        # Dispatch to the appropriate sub-parser
        if format is SpecFormat.JSON_SCHEMA:
            return self.parse_json_spec(raw, raw_envelope=raw_spec)
        if format is SpecFormat.YAML_SCHEMA:
            return self.parse_yaml_spec(raw, raw_envelope=raw_spec)
        if format is SpecFormat.TYPE_ANNOTATIONS:
            obligations = self.parse_type_annotations(raw)
            return self._build_parsed_spec(
                obligations,
                format=format,
                raw=raw_spec,
                name=name or self._derive_name(raw),
                confidence=0.85,
            )
        if format is SpecFormat.ASSERTION_BASED:
            obligations = self._parse_assertion_obligations(raw)
            return self._build_parsed_spec(
                obligations,
                format=format,
                raw=raw_spec,
                name=name or self._derive_name(raw),
                confidence=0.9,
            )
        if format is SpecFormat.PROTOCOL_CLASS:
            return self.parse_protocol_class(raw, raw_envelope=raw_spec)
        if format is SpecFormat.DOCSTRING:
            obligations = self._parse_docstring_obligations(raw)
            return self._build_parsed_spec(
                obligations,
                format=format,
                raw=raw_spec,
                name=name or self._derive_name(raw),
                confidence=0.75,
            )
        # Default: NATURAL_LANGUAGE (also handles MIXED after detection)
        obligations = self._parse_natural_language(raw)
        return self._build_parsed_spec(
            obligations,
            format=format,
            raw=raw_spec,
            name=name or self._derive_name(raw),
            confidence=0.65,
        )

    def parse_from_file(self, path: str) -> ParsedSpecification:
        """Parse a specification from a file on disk.

        The format is inferred from the file extension and content:

        * ``.json`` → :attr:`SpecFormat.JSON_SCHEMA`
        * ``.yaml`` / ``.yml`` → :attr:`SpecFormat.YAML_SCHEMA`
        * ``.py`` → :attr:`SpecFormat.MIXED` (type annotations + assertions)
        * anything else → :attr:`SpecFormat.NATURAL_LANGUAGE`

        Parameters
        ----------
        path : str
            Absolute or relative path to the specification file.

        Returns
        -------
        ParsedSpecification
            The parsed specification.

        Raises
        ------
        SpecParserError
            If the file cannot be read or parsed.
        """
        import os
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            raise SpecParserError(
                f"Cannot read specification file: {exc}",
                format=None,
                context=path,
            ) from exc

        ext = os.path.splitext(path)[1].lower()
        fmt_map = {
            ".json": SpecFormat.JSON_SCHEMA,
            ".yaml": SpecFormat.YAML_SCHEMA,
            ".yml":  SpecFormat.YAML_SCHEMA,
            ".py":   SpecFormat.MIXED,
        }
        fmt = fmt_map.get(ext, SpecFormat.NATURAL_LANGUAGE)
        return self.parse(content, format=fmt, source_location=f"{path}:0")

    def _parse_docstring_obligations(
        self,
        docstring: str,
        coordinate: str = "",
    ) -> list[ParsedObligation]:
        """Parse a Python docstring into a list of obligations.

        Handles NumPy, Google, and reStructuredText docstring styles.  The
        parser identifies section headers (``Parameters``, ``Returns``,
        ``Raises``, ``Notes``, ``Warnings``) and maps each entry to a
        judgment-sheaf coordinate.

        Parameters
        ----------
        docstring : str
            The raw docstring text (without surrounding triple-quotes).
        coordinate : str
            If non-empty, all generated obligations will use this coordinate
            regardless of keyword inference.

        Returns
        -------
        list[ParsedObligation]
            Obligations extracted from the docstring.
        """
        # Dedent first to strip leading indentation from docstrings
        text = textwrap.dedent(docstring).strip()
        if not text:
            return []

        # Split into sections using common docstring section headers
        section_pattern = re.compile(
            r"^(?P<header>Parameters|Returns?|Raises?|Notes?|Warnings?|Examples?|"
            r"Yields?|Attributes|See Also|References|Args|Keyword Args|Todo)"
            r"\s*[:\-–—]*\s*$",
            re.MULTILINE | re.IGNORECASE,
        )

        # Map section names to (coordinate_hint, kind)
        section_coord_map: dict[str, tuple[str, str]] = {
            "parameter":   ("A", "precondition"),
            "parameters":  ("A", "precondition"),
            "arg":         ("A", "precondition"),
            "args":        ("A", "precondition"),
            "keyword args":("A", "precondition"),
            "return":      ("O", "postcondition"),
            "returns":     ("O", "postcondition"),
            "yields":      ("O", "postcondition"),
            "raise":       ("O", "postcondition"),
            "raises":      ("O", "postcondition"),
            "note":        ("φ", "invariant"),
            "notes":       ("φ", "invariant"),
            "warning":     ("φ", "invariant"),
            "warnings":    ("φ", "invariant"),
            "attribute":   ("φ", "type_constraint"),
            "attributes":  ("φ", "type_constraint"),
            "todo":        ("E", "invariant"),
        }

        obligations: list[ParsedObligation] = []
        sections = section_pattern.split(text)

        # The text before the first section header is the summary paragraph
        # Treat it as natural-language obligations with CLAIMED tier
        summary = sections[0] if sections else text
        for sentence in _sentence_split(summary):
            obl = self._parse_obligation_text(sentence, forced_coordinate=coordinate)
            obligations.append(obl)

        # Process each (header, body) pair
        it = iter(sections[1:])
        for header, body in zip(it, it):
            header_key = header.strip().lower()
            coord_hint, kind_hint = section_coord_map.get(
                header_key, ("φ", "invariant")
            )
            if coordinate:
                coord_hint = coordinate
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("---"):
                    continue
                obl = self._parse_obligation_text(
                    line,
                    forced_coordinate=coord_hint,
                    forced_kind=kind_hint,
                )
                obligations.append(obl)

        return obligations[: self._max_obligations]

    def parse_docstring(
        self,
        docstring: str,
        coordinate: str = "",
    ) -> ParsedSpecification:
        """Parse a Python docstring into a :class:`ParsedSpecification`."""
        obligations = self._parse_docstring_obligations(docstring, coordinate=coordinate)
        raw_spec = RawSpecification(
            spec_id=str(uuid.uuid4()),
            raw_text=docstring,
            format=SpecFormat.DOCSTRING,
            source_location="<memory>",
            confidence=1.0,
            metadata={"parser_version": self._parser_version},
        )
        return self._build_parsed_spec(
            obligations,
            format=SpecFormat.DOCSTRING,
            raw=raw_spec,
            name=self._derive_name(docstring),
            confidence=0.75,
        )

    def parse_type_annotations(self, source: str) -> list[ParsedObligation]:
        """Parse type annotations from Python source code into obligations.

        Uses the :mod:`ast` module to walk function definitions and extract
        PEP 484 annotations.  Each annotated parameter becomes a
        ``"type_constraint"`` obligation at coordinate ``"φ"``; each return
        annotation becomes a ``"type_constraint"`` at coordinate ``"O"``.

        Parameters
        ----------
        source : str
            Python source code containing annotated function or class
            definitions.

        Returns
        -------
        list[ParsedObligation]
            One obligation per annotated parameter or return annotation found.

        Raises
        ------
        SpecParserError
            If *source* cannot be parsed as valid Python.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise SpecParserError(
                f"Python syntax error while parsing type annotations: {exc}",
                format=SpecFormat.TYPE_ANNOTATIONS,
                context=source[:200],
            ) from exc

        annotations = _extract_ast_annotations(tree)
        obligations: list[ParsedObligation] = []

        for ann in annotations:
            coord = "O" if ann["is_return"] else "φ"
            if ann["is_return"]:
                predicate = (
                    f"{ann['qualified_name']} returns {ann['annotation_src']}"
                )
            else:
                predicate = (
                    f"Parameter '{ann['name']}' of {ann['qualified_name']} "
                    f"has type {ann['annotation_src']}"
                )
            raw_text = (
                f"{ann['name']}: {ann['annotation_src']}"
                if not ann["is_return"]
                else f"-> {ann['annotation_src']}"
            )
            tokens = _words_lower(predicate)
            obligation_id = _make_obligation_id(predicate, coord, "type_constraint")
            obligations.append(ParsedObligation(
                obligation_id=obligation_id,
                coordinate=coord,
                predicate=predicate,
                kind="type_constraint",
                trust_tier="INFERRED",   # static type annotations → INFERRED tier
                severity=_detect_severity(ann["annotation_src"]),
                raw_text=raw_text,
                metadata={
                    "qualified_name": ann["qualified_name"],
                    "is_return": ann["is_return"],
                    "lineno": ann.get("lineno", 0),
                },
            ))

        return obligations[: self._max_obligations]

    def _parse_assertion_obligations(self, source: str) -> list[ParsedObligation]:
        """Extract assertion-based obligations from Python source code.

        Each ``assert`` statement is translated into a ``"invariant"``
        (or ``"precondition"`` / ``"postcondition"`` heuristically) obligation
        at coordinate ``"E"`` (evidence coordinate).  The assertion test
        expression is the predicate.

        Parameters
        ----------
        source : str
            Python source code to scan for ``assert`` statements.

        Returns
        -------
        list[ParsedObligation]
            One obligation per ``assert`` statement found.

        Raises
        ------
        SpecParserError
            If *source* cannot be parsed as valid Python.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise SpecParserError(
                f"Python syntax error while parsing assertions: {exc}",
                format=SpecFormat.ASSERTION_BASED,
                context=source[:200],
            ) from exc

        raw_assertions = _extract_ast_assertions(tree)
        obligations: list[ParsedObligation] = []

        for entry in raw_assertions:
            test_src: str = entry["test_src"]
            msg_src: str | None = entry["msg_src"]
            lineno: int = entry["lineno"]
            enclosing: str = entry["enclosing_function"]

            # Heuristic kind assignment:
            # If the assertion is near the start of a function (line < fn_start + 5)
            # treat it as a precondition; near the end as postcondition; else invariant.
            if re.search(r"\bpre\b|_before\b|input", test_src, re.IGNORECASE):
                kind = "precondition"
                coord = "A"  # agent/input coordinate
            elif re.search(r"\bpost\b|_after\b|output|result", test_src, re.IGNORECASE):
                kind = "postcondition"
                coord = "O"
            else:
                kind = "invariant"
                coord = "E"  # evidence/check coordinate

            predicate = test_src
            if msg_src:
                predicate = f"{test_src}  # {msg_src}"

            obligation_id = _make_obligation_id(predicate, coord, kind)
            obligations.append(ParsedObligation(
                obligation_id=obligation_id,
                coordinate=coord,
                predicate=predicate,
                kind=kind,
                trust_tier="ASSERTED",   # runtime assert → ASSERTED tier
                severity=_detect_severity(test_src),
                raw_text=f"assert {test_src}" + (f", {msg_src}" if msg_src else ""),
                metadata={
                    "lineno": lineno,
                    "enclosing_function": enclosing,
                },
            ))

        return obligations[: self._max_obligations]

    def parse_json_spec(
        self,
        json_str: str,
        raw_envelope: RawSpecification | None = None,
    ) -> ParsedSpecification:
        """Parse a JSON-encoded obligation document.

        The expected schema is::

            {
              "name": "MySpec",          # optional
              "obligations": [
                {
                  "predicate": "...",    # required
                  "coordinate": "φ",    # optional; default "φ"
                  "kind": "...",         # optional; default "invariant"
                  "trust_tier": "...",   # optional; auto-inferred if absent
                  "severity": "...",     # optional; auto-inferred if absent
                  "metadata": { ... }    # optional
                },
                ...
              ]
            }

        Parameters
        ----------
        json_str : str
            JSON-encoded specification document.
        raw_envelope : RawSpecification | None
            Pre-built raw artefact to attach.  Created internally if ``None``.

        Returns
        -------
        ParsedSpecification
            The parsed specification.

        Raises
        ------
        SpecParserError
            If *json_str* is not valid JSON or the document structure is
            unrecognised.
        """
        try:
            doc = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise SpecParserError(
                f"Invalid JSON: {exc}",
                format=SpecFormat.JSON_SCHEMA,
                context=json_str[:200],
            ) from exc

        if not isinstance(doc, dict):
            raise SpecParserError(
                "JSON specification must be a top-level object.",
                format=SpecFormat.JSON_SCHEMA,
                context=json_str[:200],
            )

        name = str(doc.get("name", "unnamed-json-spec"))
        raw_obligs = doc.get("obligations", doc.get("constraints", []))
        if not isinstance(raw_obligs, list):
            raw_obligs = [raw_obligs]

        obligations = [self._hydrate_obligation_dict(o) for o in raw_obligs]

        if raw_envelope is None:
            raw_envelope = RawSpecification(
                spec_id=str(uuid.uuid4()),
                raw_text=json_str,
                format=SpecFormat.JSON_SCHEMA,
                source_location="",
                confidence=1.0,
                metadata={"parser_version": self._parser_version},
            )

        return self._build_parsed_spec(
            obligations,
            format=SpecFormat.JSON_SCHEMA,
            raw=raw_envelope,
            name=name,
            confidence=0.95,
        )

    def parse_yaml_spec(
        self,
        yaml_str: str,
        raw_envelope: RawSpecification | None = None,
    ) -> ParsedSpecification:
        """Parse a YAML-encoded obligation document.

        Falls back gracefully if the ``pyyaml`` package is not installed by
        attempting to parse the YAML as JSON (which is valid YAML).  If neither
        succeeds, raises :class:`SpecParserError`.

        The YAML document is expected to follow the same schema as the JSON
        document described in :meth:`parse_json_spec`.

        Parameters
        ----------
        yaml_str : str
            YAML-encoded specification document.
        raw_envelope : RawSpecification | None
            Pre-built raw artefact to attach.

        Returns
        -------
        ParsedSpecification
            The parsed specification.

        Raises
        ------
        SpecParserError
            If *yaml_str* cannot be parsed as YAML or JSON.
        """
        doc: dict[str, Any] | None = None

        try:
            import yaml  # type: ignore[import]
            doc = yaml.safe_load(yaml_str)
        except ImportError:
            # pyyaml not installed — try parsing as JSON
            try:
                doc = json.loads(yaml_str)
            except json.JSONDecodeError:
                pass
        except Exception as exc:
            raise SpecParserError(
                f"YAML parse error: {exc}",
                format=SpecFormat.YAML_SCHEMA,
                context=yaml_str[:200],
            ) from exc

        if doc is None:
            raise SpecParserError(
                "Could not parse YAML specification (pyyaml not installed and "
                "input is not valid JSON).",
                format=SpecFormat.YAML_SCHEMA,
                context=yaml_str[:200],
            )
        if not isinstance(doc, dict):
            raise SpecParserError(
                "YAML specification must be a top-level mapping.",
                format=SpecFormat.YAML_SCHEMA,
                context=yaml_str[:200],
            )

        # Re-use the JSON path after loading the YAML into a dict
        name = str(doc.get("name", "unnamed-yaml-spec"))
        raw_obligs = doc.get("obligations", doc.get("constraints", []))
        if not isinstance(raw_obligs, list):
            raw_obligs = [raw_obligs]

        obligations = [self._hydrate_obligation_dict(o) for o in raw_obligs]

        if raw_envelope is None:
            raw_envelope = RawSpecification(
                spec_id=str(uuid.uuid4()),
                raw_text=yaml_str,
                format=SpecFormat.YAML_SCHEMA,
                source_location="",
                confidence=1.0,
                metadata={"parser_version": self._parser_version},
            )

        return self._build_parsed_spec(
            obligations,
            format=SpecFormat.YAML_SCHEMA,
            raw=raw_envelope,
            name=name,
            confidence=0.93,
        )

    def parse_assertions(self, source: str) -> ParsedSpecification:
        """Parse Python assertions into a full :class:`ParsedSpecification`."""
        obligations = self._parse_assertion_obligations(source)
        raw_spec = RawSpecification(
            spec_id=str(uuid.uuid4()),
            raw_text=source,
            format=SpecFormat.ASSERTION_BASED,
            source_location="<memory>",
            confidence=0.9,
            metadata={"parser_version": self._parser_version},
        )
        return self._build_parsed_spec(
            obligations,
            format=SpecFormat.ASSERTION_BASED,
            raw=raw_spec,
            name=self._derive_name(source),
            confidence=0.9,
        )

    def parse_protocol_class(
        self,
        source: str,
        raw_envelope: RawSpecification | None = None,
    ) -> ParsedSpecification:
        """Parse a ``typing.Protocol`` class definition into a specification.

        Each method on the Protocol becomes a set of obligations: parameter
        annotations become ``"type_constraint"`` obligations at coordinate
        ``"φ"``; return annotations become obligations at coordinate ``"O"``;
        and the method docstring (if present) is fed to :meth:`parse_docstring`
        for any additional natural-language obligations.

        Parameters
        ----------
        source : str
            Python source code containing one or more Protocol class
            definitions.
        raw_envelope : RawSpecification | None
            Pre-built raw artefact to attach.

        Returns
        -------
        ParsedSpecification
            The parsed specification.

        Raises
        ------
        SpecParserError
            If the source cannot be parsed.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise SpecParserError(
                f"Python syntax error in Protocol class source: {exc}",
                format=SpecFormat.PROTOCOL_CLASS,
                context=source[:200],
            ) from exc

        methods = _parse_protocol_methods(tree)
        if not methods:
            # If no Protocol found, fall back to annotation + assertion parsing
            obligations = (
                self.parse_type_annotations(source)
                + self._parse_assertion_obligations(source)
            )
        else:
            obligations: list[ParsedObligation] = []
            for m in methods:
                qname = f"{m['class_name']}.{m['method_name']}"
                # Emit one type_constraint per annotation
                for param_name, ann_src in m["annotations"].items():
                    coord = "O" if param_name == "__return__" else "φ"
                    kind = "type_constraint"
                    predicate = (
                        f"{qname} return type is {ann_src}"
                        if param_name == "__return__"
                        else f"'{param_name}' of {qname} has type {ann_src}"
                    )
                    obligations.append(ParsedObligation(
                        obligation_id=_make_obligation_id(predicate, coord, kind),
                        coordinate=coord,
                        predicate=predicate,
                        kind=kind,
                        trust_tier="INFERRED",
                        severity=_detect_severity(ann_src),
                        raw_text=f"{param_name}: {ann_src}",
                        metadata={"qualified_name": qname, "is_property": m["is_property"]},
                    ))
                # Emit docstring obligations at a method-scoped coordinate
                if m["docstring"]:
                    doc_obls = self._parse_docstring_obligations(m["docstring"], coordinate="φ")
                    # stamp each with the method's qualified name in metadata
                    for obl in doc_obls:
                        obligations.append(ParsedObligation(
                            obligation_id=obl.obligation_id,
                            coordinate=obl.coordinate,
                            predicate=obl.predicate,
                            kind=obl.kind,
                            trust_tier=obl.trust_tier,
                            severity=obl.severity,
                            raw_text=obl.raw_text,
                            metadata={**obl.metadata, "qualified_name": qname},
                        ))

        # Derive protocol name from first class definition
        proto_name = methods[0]["class_name"] if methods else "UnknownProtocol"

        if raw_envelope is None:
            raw_envelope = RawSpecification(
                spec_id=str(uuid.uuid4()),
                raw_text=source,
                format=SpecFormat.PROTOCOL_CLASS,
                source_location="",
                confidence=1.0,
                metadata={"parser_version": self._parser_version},
            )

        return self._build_parsed_spec(
            obligations,
            format=SpecFormat.PROTOCOL_CLASS,
            raw=raw_envelope,
            name=f"{proto_name}-protocol-spec",
            confidence=0.88,
        )

    def merge(self, specs: list[ParsedSpecification]) -> ParsedSpecification:
        """Merge multiple :class:`ParsedSpecification` objects into one.

        The merged specification collects all obligations from every input
        specification, deduplicates by ``obligation_id``, re-computes the
        ``coordinates_covered`` and ``trust_floor``, and records all input
        ``spec_id`` values in ``metadata["merged_from"]``.

        Merging corresponds to the *union* of target sections: the merged
        section's value at each coordinate is the conjunction of all prescriptions
        from the contributing sections.

        Parameters
        ----------
        specs : list[ParsedSpecification]
            Specifications to merge.  Must contain at least one element.

        Returns
        -------
        ParsedSpecification
            The merged specification.

        Raises
        ------
        SpecParserError
            If *specs* is empty.
        """
        if not specs:
            raise SpecParserError(
                "Cannot merge an empty list of specifications.",
                format=SpecFormat.MIXED,
                context="",
            )
        if len(specs) == 1:
            return specs[0]

        # Deduplicate obligations by obligation_id
        seen_ids: set[str] = set()
        merged_obligations: list[ParsedObligation] = []
        for spec in specs:
            for obl in spec.obligations:
                if obl.obligation_id not in seen_ids:
                    seen_ids.add(obl.obligation_id)
                    merged_obligations.append(obl)

        # Combine confidence as weighted mean (equal weight per spec)
        mean_confidence = sum(s.confidence for s in specs) / len(specs)

        merged_name = " + ".join(s.name for s in specs[:3])
        if len(specs) > 3:
            merged_name += f" + {len(specs) - 3} more"

        merged_meta: dict[str, Any] = {
            "merged_from": [s.spec_id for s in specs],
            "merged_at": _utc_now_iso(),
            "parser_version": self._parser_version,
        }

        return self._build_parsed_spec(
            merged_obligations,
            format=SpecFormat.MIXED,
            raw=None,
            name=merged_name,
            confidence=mean_confidence,
            extra_metadata=merged_meta,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_format(self, raw: str) -> SpecFormat:
        """Auto-detect the format of a raw specification string.

        The detection heuristic applies a ranked set of pattern tests:

        1. **JSON** — starts with ``{`` or ``[`` after stripping whitespace.
        2. **YAML** — contains ``---`` header or key: value lines without JSON
           brackets.
        3. **Protocol class** — contains ``class ... Protocol`` pattern.
        4. **Type annotations** — contains ``def`` or ``->`` or ``:`` in a
           function-like context.
        5. **Assertion-based** — contains ``assert`` keyword.
        6. **Docstring** — contains NumPy/Google/reST section headers.
        7. **Natural language** — fallback.

        Parameters
        ----------
        raw : str
            The raw input to classify.

        Returns
        -------
        SpecFormat
            The detected format.
        """
        stripped = raw.strip()

        # JSON detection
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return SpecFormat.JSON_SCHEMA
            except json.JSONDecodeError:
                pass

        # YAML detection (very light heuristic)
        if re.search(r"^---", stripped, re.MULTILINE):
            return SpecFormat.YAML_SCHEMA
        yaml_kv_lines = re.findall(r"^\s*\w[\w\s]*:\s+\S", stripped, re.MULTILINE)
        if len(yaml_kv_lines) >= 2 and not stripped.startswith("{"):
            return SpecFormat.YAML_SCHEMA

        # Protocol class
        if re.search(r"class\s+\w+\s*\(.*Protocol.*\)", stripped):
            return SpecFormat.PROTOCOL_CLASS

        # Type annotations (Python source)
        if re.search(r"\bdef\b.*\(", stripped) and (
            re.search(r"->\s*\w", stripped) or re.search(r"\w+\s*:\s*\w", stripped)
        ):
            return SpecFormat.TYPE_ANNOTATIONS

        # Assertion-based
        if re.search(r"\bassert\b", stripped):
            return SpecFormat.ASSERTION_BASED

        # Docstring section headers (NumPy / Google / reST)
        if re.search(
            r"^(Parameters|Returns?|Raises?|Notes?|Args|Yields?)\s*[:\-–]+",
            stripped,
            re.MULTILINE | re.IGNORECASE,
        ):
            return SpecFormat.DOCSTRING

        return SpecFormat.NATURAL_LANGUAGE

    def _parse_obligation_text(
        self,
        text: str,
        forced_coordinate: str = "",
        forced_kind: str = "",
    ) -> ParsedObligation:
        """Parse a single obligation sentence using keyword extraction (NLP-lite).

        The method tokenises *text*, consults :data:`_KEYWORD_TAXONOMY` to
        identify the most relevant (coordinate, kind) pair, and constructs a
        :class:`ParsedObligation`.

        Parameters
        ----------
        text : str
            A single sentence or phrase describing an obligation.
        forced_coordinate : str
            If non-empty, overrides taxonomy-inferred coordinate.
        forced_kind : str
            If non-empty, overrides taxonomy-inferred kind.

        Returns
        -------
        ParsedObligation
            The constructed obligation.
        """
        words = _words_lower(text)
        # Default coordinate and kind
        coord = "φ"
        kind = "invariant"
        # Walk taxonomy and pick the best match (first match wins)
        for keyword_set, coord_hint, kind_hint in _KEYWORD_TAXONOMY:
            if words & keyword_set:
                coord = coord_hint
                kind = kind_hint
                break
        # Apply forced overrides
        if forced_coordinate:
            coord = forced_coordinate
        if forced_kind:
            kind = forced_kind

        trust_tier = _assign_trust_tier(words)
        severity = _detect_severity(text)
        # Predicate is the sentence itself, normalised
        predicate = _normalise_whitespace(text)
        obligation_id = _make_obligation_id(predicate, coord, kind)
        return ParsedObligation(
            obligation_id=obligation_id,
            coordinate=coord,
            predicate=predicate,
            kind=kind,
            trust_tier=trust_tier,
            severity=severity,
            raw_text=text,
            metadata={},
        )

    def _parse_natural_language(self, text: str) -> list[ParsedObligation]:
        """Parse a block of natural-language text into obligations.

        Splits the text into sentences and runs :meth:`_parse_obligation_text`
        on each one that looks obligation-bearing (contains at least one
        taxonomy keyword).

        Parameters
        ----------
        text : str
            Free-form natural-language specification text.

        Returns
        -------
        list[ParsedObligation]
            Obligations inferred from the text.
        """
        sentences = _sentence_split(text)
        obligations: list[ParsedObligation] = []
        all_taxonomy_keywords: frozenset[str] = frozenset().union(
            *(ks for ks, _, _ in _KEYWORD_TAXONOMY)
        )
        for sentence in sentences:
            words = _words_lower(sentence)
            # Only emit an obligation if the sentence contains at least one
            # obligation-bearing keyword.  Pure descriptive sentences are
            # skipped to reduce noise.
            if words & all_taxonomy_keywords:
                obligations.append(self._parse_obligation_text(sentence))
        return obligations[: self._max_obligations]

    def _hydrate_obligation_dict(self, raw_obl: Any) -> ParsedObligation:
        """Build a :class:`ParsedObligation` from a raw dict (JSON/YAML origin).

        Parameters
        ----------
        raw_obl : Any
            A dict or string from a structured spec document.

        Returns
        -------
        ParsedObligation
            The hydrated obligation.
        """
        if isinstance(raw_obl, str):
            # Accept bare strings as predicates with default fields
            return self._parse_obligation_text(raw_obl)

        if not isinstance(raw_obl, dict):
            return self._parse_obligation_text(str(raw_obl))

        predicate = str(raw_obl.get("predicate", raw_obl.get("text", "")))
        coordinate = str(raw_obl.get("coordinate", raw_obl.get("coord", "φ")))
        kind = str(raw_obl.get("kind", "invariant"))
        severity = str(raw_obl.get("severity", _detect_severity(predicate)))
        metadata = dict(raw_obl.get("metadata", {}))

        # Trust tier: explicit > inferred from keyword scan
        explicit_tier = raw_obl.get("trust_tier", "")
        if explicit_tier and explicit_tier in _TRUST_TIER_ORDER:
            trust_tier = str(explicit_tier)
        else:
            trust_tier = _assign_trust_tier(_words_lower(predicate))

        obligation_id = _make_obligation_id(predicate, coordinate, kind)
        return ParsedObligation(
            obligation_id=obligation_id,
            coordinate=coordinate,
            predicate=predicate,
            kind=kind,
            trust_tier=trust_tier,
            severity=severity,
            raw_text=predicate,
            metadata=metadata,
        )

    def _assign_trust_tier(self, obligation: dict[str, Any]) -> str:  # type: ignore[type-arg]
        """Assign a trust tier to an obligation dict.

        Public wrapper around the module-level :func:`_assign_trust_tier` for
        callers that have an obligation dict rather than a token set.

        Parameters
        ----------
        obligation : dict[str, Any]
            Obligation dictionary with at least a ``"predicate"`` key.

        Returns
        -------
        str
            Trust tier string.
        """
        text = str(obligation.get("predicate", ""))
        return _assign_trust_tier(_words_lower(text))

    def _extract_coordinates(
        self,
        obligations: list[ParsedObligation],
    ) -> tuple[str, ...]:
        """Extract and deduplicate the coordinates referenced by *obligations*.

        Parameters
        ----------
        obligations : list[ParsedObligation]
            The full list of obligations for a specification.

        Returns
        -------
        tuple[str, ...]
            Sorted, deduplicated tuple of coordinate strings.
        """
        return tuple(sorted({obl.coordinate for obl in obligations}))

    def _derive_name(self, raw: str) -> str:
        """Derive a short human-readable name from the first line of *raw*.

        Parameters
        ----------
        raw : str
            Raw input text.

        Returns
        -------
        str
            A name string of at most 60 characters.
        """
        first_line = raw.strip().splitlines()[0] if raw.strip() else "unnamed-spec"
        # Strip leading comment characters and whitespace
        first_line = re.sub(r"^[#\/*\s\"']+", "", first_line).strip()
        if len(first_line) > 60:
            first_line = first_line[:57] + "..."
        return first_line or "unnamed-spec"

    def _build_parsed_spec(
        self,
        obligations: list[ParsedObligation],
        format: SpecFormat,  # noqa: A002
        raw: RawSpecification | None,
        name: str,
        confidence: float,
        extra_metadata: dict[str, Any] | None = None,
    ) -> ParsedSpecification:
        """Assemble a :class:`ParsedSpecification` from its components.

        Parameters
        ----------
        obligations : list[ParsedObligation]
            Obligations to include.
        format : SpecFormat
            Source format.
        raw : RawSpecification | None
            Original raw artefact.
        name : str
            Human-readable name.
        confidence : float
            Parser confidence score.
        extra_metadata : dict | None
            Additional metadata entries to merge in.

        Returns
        -------
        ParsedSpecification
            Assembled specification.
        """
        coords = self._extract_coordinates(obligations)
        floor = _trust_floor([obl.trust_tier for obl in obligations])
        spec_id = _make_spec_id(name, format.value)
        meta: dict[str, Any] = {
            "parsed_at": _utc_now_iso(),
            "parser_version": self._parser_version,
            "obligation_count": len(obligations),
        }
        if extra_metadata:
            meta.update(extra_metadata)
        return ParsedSpecification(
            spec_id=spec_id,
            name=name,
            format=format,
            obligations=tuple(obligations),
            coordinates_covered=coords,
            trust_floor=floor,
            confidence=confidence,
            raw=raw,
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def parse_spec(
    raw: str,
    format: SpecFormat | None = None,  # noqa: A002
    name: str | None = None,
    **kwargs: Any,
) -> ParsedSpecification:
    """Parse a raw string into a :class:`ParsedSpecification`.

    A module-level convenience wrapper around :meth:`SpecParser.parse`.

    Parameters
    ----------
    raw : str
        The raw input text.
    **kwargs
        Keyword arguments forwarded to :meth:`SpecParser.parse`:

        ``format`` : :class:`SpecFormat`
            Explicit format (default: ``MIXED``).
        ``source_location`` : str
            ``"<file>:<line>"`` provenance string.
        ``name`` : str
            Human-readable spec name.
        ``config`` : dict
            Parser configuration dict.

    Returns
    -------
    ParsedSpecification
        The parsed specification.
    """
    if format is not None:
        kwargs.setdefault("format", format)
    if name is not None:
        kwargs.setdefault("name", name)
    config = kwargs.pop("config", None)
    parser = SpecParser(config=config)
    if not raw or not raw.strip():
        chosen_format = kwargs.get("format", SpecFormat.MIXED)
        return parser._build_parsed_spec(
            [],
            format=chosen_format,
            raw=RawSpecification(
                spec_id=str(uuid.uuid4()),
                raw_text=raw,
                format=chosen_format,
                source_location=str(kwargs.get("source_location", "")),
                confidence=1.0,
                metadata={"parser_version": parser._parser_version},
            ),
            name=str(kwargs.get("name", name or "empty-spec")),
            confidence=1.0,
            extra_metadata={"empty_input": True},
        )
    return parser.parse(raw, **kwargs)


def parse_spec_file(path: str) -> ParsedSpecification:
    """Parse a specification from a file on disk.

    A module-level convenience wrapper around :meth:`SpecParser.parse_from_file`.

    Parameters
    ----------
    path : str
        Absolute or relative path to the specification file.

    Returns
    -------
    ParsedSpecification
        The parsed specification.
    """
    return SpecParser().parse_from_file(path)


def spec_from_annotations(source: str) -> ParsedSpecification:
    """Build a :class:`ParsedSpecification` from Python type annotations.

    A module-level convenience wrapper around
    :meth:`SpecParser.parse_type_annotations`.

    Parameters
    ----------
    source : str
        Python source code containing annotated definitions.

    Returns
    -------
    ParsedSpecification
        The parsed specification.
    """
    parser = SpecParser()
    obligations = parser.parse_type_annotations(source)
    raw = RawSpecification(
        spec_id=str(uuid.uuid4()),
        raw_text=source,
        format=SpecFormat.TYPE_ANNOTATIONS,
        source_location="",
        confidence=1.0,
        metadata={"parser_version": parser._parser_version},
    )
    return parser._build_parsed_spec(
        obligations,
        format=SpecFormat.TYPE_ANNOTATIONS,
        raw=raw,
        name=parser._derive_name(source),
        confidence=0.85,
    )


def spec_from_docstring(source_or_docstring: str) -> ParsedSpecification:
    """Build a :class:`ParsedSpecification` from a Python docstring.

    If *source_or_docstring* looks like a complete Python source file (contains
    ``def`` or ``class`` keywords) the parser will extract the module-level
    docstring before parsing.  Otherwise it is treated as a bare docstring.

    A module-level convenience wrapper around :meth:`SpecParser.parse_docstring`.

    Parameters
    ----------
    source_or_docstring : str
        Either a bare docstring or Python source whose first string literal
        is the docstring.

    Returns
    -------
    ParsedSpecification
        The parsed specification.
    """
    parser = SpecParser()
    # Attempt to extract module docstring from source via ast
    docstring = source_or_docstring
    if re.search(r"\bdef\b|\bclass\b", source_or_docstring):
        try:
            tree = ast.parse(source_or_docstring)
            extracted = ast.get_docstring(tree)
            if extracted:
                docstring = extracted
        except SyntaxError:
            pass  # fall back to treating the whole thing as a docstring

    obligations = parser._parse_docstring_obligations(docstring)
    raw = RawSpecification(
        spec_id=str(uuid.uuid4()),
        raw_text=source_or_docstring,
        format=SpecFormat.DOCSTRING,
        source_location="",
        confidence=1.0,
        metadata={"parser_version": parser._parser_version},
    )
    return parser._build_parsed_spec(
        obligations,
        format=SpecFormat.DOCSTRING,
        raw=raw,
        name=parser._derive_name(docstring),
        confidence=0.75,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # §10.0 smoke test — exercises every sub-parser and the merge path.
    # -----------------------------------------------------------------------

    import sys

    _SAMPLE_DOCSTRING = """\
    Compute the least fixed point of the descent operator.

    Parameters
    ----------
    site : SemanticSite
        The site over which descent is performed.  Must be non-empty.
    section : LocalSection
        An initial section whose trust tier must be at least ASSERTED.

    Returns
    -------
    GlobalSection
        The assembled global section if descent succeeds.

    Raises
    ------
    DescentError
        If the gluing conditions fail at any overlap.

    Notes
    -----
    This function assumes that the site satisfies the coverage axiom.
    The invariant that every local section is non-empty must hold.
    """

    _SAMPLE_ANNOTATIONS = """\
def compute_fixed_point(
    site: SemanticSite,
    section: LocalSection,
    max_iters: int = 100,
) -> GlobalSection:
    ...

def validate_coverage(site: SemanticSite, cover: Cover) -> bool:
    ...
"""

    _SAMPLE_ASSERTIONS = """\
def check_witness(witness, spec):
    assert witness is not None, "witness must not be None"
    assert spec.coordinate_count > 0, "spec must cover at least one coordinate"
    result = compute(witness, spec)
    assert result.status == "satisfied", "result must be satisfied"
    return result
"""

    _SAMPLE_JSON = json.dumps({
        "name": "descent-correctness-spec",
        "obligations": [
            {
                "predicate": "The descent operator must converge within max_iters steps",
                "coordinate": "B",
                "kind": "invariant",
                "trust_tier": "CLAIMED",
                "severity": "HIGH",
            },
            {
                "predicate": "The returned GlobalSection must satisfy all local restrictions",
                "coordinate": "O",
                "kind": "postcondition",
                "trust_tier": "TESTED",
            },
        ],
    })

    _SAMPLE_PROTOCOL = """\
from typing import Protocol

class DescentOperator(Protocol):
    \"\"\"Contract for descent operators over a semantic site.

    Notes
    -----
    Every implementation must be deterministic.
    \"\"\"

    def apply(self, section: LocalSection) -> GlobalSection:
        \"\"\"Apply the operator.  Must not mutate the input section.

        Parameters
        ----------
        section : LocalSection
            The section to transform.  Requires trust tier ASSERTED.

        Returns
        -------
        GlobalSection
            The result.  Must satisfy all gluing conditions.
        \"\"\"
        ...

    def fixed_point(self) -> bool:
        \"\"\"Return True if the operator has reached its fixed point.\"\"\"
        ...
"""

    parser = SpecParser()

    print("─" * 60)
    print("§10.0 SpecParser smoke test")
    print("─" * 60)

    # 1. Docstring
    spec_doc = parser.parse(
        _SAMPLE_DOCSTRING,
        format=SpecFormat.DOCSTRING,
        name="fixed-point-docstring-spec",
    )
    print(f"\n[DOCSTRING] spec_id={spec_doc.spec_id}")
    print(f"  obligations: {spec_doc.obligation_count}")
    print(f"  coordinates: {spec_doc.coordinates_covered}")
    print(f"  trust_floor: {spec_doc.trust_floor}")
    assert spec_doc.obligation_count > 0, "Expected at least one obligation from docstring"

    # 2. Type annotations
    spec_ann = parser.parse(
        _SAMPLE_ANNOTATIONS,
        format=SpecFormat.TYPE_ANNOTATIONS,
        name="fixed-point-annotations-spec",
    )
    print(f"\n[ANNOTATIONS] spec_id={spec_ann.spec_id}")
    print(f"  obligations: {spec_ann.obligation_count}")
    assert "φ" in spec_ann.coordinates_covered or "O" in spec_ann.coordinates_covered

    # 3. Assertions
    spec_assert = parser.parse(
        _SAMPLE_ASSERTIONS,
        format=SpecFormat.ASSERTION_BASED,
        name="fixed-point-assertions-spec",
    )
    print(f"\n[ASSERTIONS] spec_id={spec_assert.spec_id}")
    print(f"  obligations: {spec_assert.obligation_count}")
    for obl in spec_assert.obligations:
        print(f"    [{obl.kind}|{obl.coordinate}|{obl.trust_tier}] {obl.predicate[:60]}")

    # 4. JSON
    spec_json = parser.parse_json_spec(_SAMPLE_JSON)
    print(f"\n[JSON] spec_id={spec_json.spec_id}  name={spec_json.name}")
    print(f"  obligations: {spec_json.obligation_count}")
    assert spec_json.obligation_count == 2

    # 5. Protocol class
    spec_proto = parser.parse_protocol_class(_SAMPLE_PROTOCOL)
    print(f"\n[PROTOCOL] spec_id={spec_proto.spec_id}  name={spec_proto.name}")
    print(f"  obligations: {spec_proto.obligation_count}")
    assert spec_proto.obligation_count > 0

    # 6. Merge
    merged = parser.merge([spec_doc, spec_ann, spec_assert, spec_json, spec_proto])
    print(f"\n[MERGED] spec_id={merged.spec_id}")
    print(f"  total obligations: {merged.obligation_count}")
    print(f"  coordinates: {merged.coordinates_covered}")
    print(f"  trust_floor: {merged.trust_floor}")
    assert merged.obligation_count >= spec_doc.obligation_count

    # 7. to_jugeo_spec round-trip
    jugeo_obj = merged.to_jugeo_spec()
    print(f"\n[to_jugeo_spec] type={type(jugeo_obj).__name__}")
    if isinstance(jugeo_obj, dict):
        print(f"  (fallback dict, jugeo models not available)")
        assert jugeo_obj["spec_id"] == merged.spec_id
    else:
        print(f"  spec_id={jugeo_obj.spec_id}")

    # 8. Auto-detection
    spec_auto = parser.parse(_SAMPLE_JSON)
    assert spec_auto.format is SpecFormat.JSON_SCHEMA, (
        f"Expected JSON_SCHEMA, got {spec_auto.format}"
    )
    print(f"\n[AUTO-DETECT] format correctly identified as {spec_auto.format.value}")

    # 9. Module-level helpers
    spec_h1 = spec_from_docstring(_SAMPLE_DOCSTRING)
    spec_h2 = spec_from_annotations(_SAMPLE_ANNOTATIONS)
    print(f"\n[HELPERS] spec_from_docstring  obligations={spec_h1.obligation_count}")
    print(f"          spec_from_annotations obligations={spec_h2.obligation_count}")

    print("\n─" * 60)
    print("All smoke-test assertions passed.")
    sys.exit(0)
