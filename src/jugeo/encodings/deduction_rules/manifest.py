r"""Package manifest for the ``deduction_rules`` encoding package.

This manifest enumerates every public symbol exported by the
``jugeo.encodings.deduction_rules`` package, together with version
information, dependency declarations, and Copilot-integration metadata.

Chapter 33 reference
--------------------
``theory2.tex`` Chapter 33 (§§ 33.1–33.6) specifies the deduction-rule
calculus used throughout JuGeo.  The rules encode the judgment-level
proof theory:

.. math::

   \\frac{\\Gamma \\vdash P_1 \\quad \\Gamma \\vdash P_2}
        {\\Gamma \\vdash P_1 \\wedge P_2}
        \\;[\\text{∧-intro}]

This manifest is the machine-readable counterpart to that specification.

Architecture
------------
The manifest is queried at import time by ``jugeo.package_manifest`` to
register the package in the global symbol catalogue.  It is also queried
by the Copilot bridge to discover available rule schemas.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SymbolKind(str, Enum):
    """Category of a public symbol.

    Used by the manifest to classify exports so that consumers can filter
    by kind (e.g. retrieve only dataclasses or only enums).
    """

    ENUM = "enum"
    DATACLASS = "dataclass"
    FUNCTION = "function"
    CLASS = "class"
    CONSTANT = "constant"
    PROTOCOL = "protocol"
    TYPE_ALIAS = "type-alias"


class StabilityLevel(str, Enum):
    """Stability guarantee for a symbol.

    - ``STABLE``: API is frozen; breaking changes require a major version bump.
    - ``BETA``: API is reasonably stable but may change in minor releases.
    - ``EXPERIMENTAL``: May change at any time; use at your own risk.
    - ``INTERNAL``: Not part of the public API; subject to change without notice.
    """

    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"
    INTERNAL = "internal"


class DependencyKind(str, Enum):
    """The nature of a dependency declared in the manifest."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    DEV_ONLY = "dev-only"


# ---------------------------------------------------------------------------
# Core manifest dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SymbolEntry:
    """A single entry in the manifest symbol table.

    Each entry describes one public symbol exported by the package.

    Attributes
    ----------
    name:
        Qualified name of the symbol (e.g. ``"DeductionRule"``).
    kind:
        :class:`SymbolKind` — what type of object this symbol is.
    module:
        Dotted module path within the package (e.g. ``"models"``).
    stability:
        :class:`StabilityLevel` of this symbol's API.
    since_version:
        Version string when this symbol was introduced.
    description:
        One-line description of the symbol.
    aliases:
        Alternative names for the same symbol.
    copilot_tags:
        Tags that Copilot uses to discover this symbol.
    """

    name: str
    kind: SymbolKind
    module: str
    stability: StabilityLevel = StabilityLevel.STABLE
    since_version: str = "0.1.0"
    description: str = ""
    aliases: tuple[str, ...] = ()
    copilot_tags: tuple[str, ...] = ()

    def qualified_name(self) -> str:
        """Return the fully qualified name within the package.

        Returns
        -------
        str
            ``"jugeo.encodings.deduction_rules.<module>.<name>"``.
        """
        return f"jugeo.encodings.deduction_rules.{self.module}.{self.name}"

    def is_stable(self) -> bool:
        """Return ``True`` if this symbol is marked ``STABLE``."""
        return self.stability == StabilityLevel.STABLE

    def is_copilot_visible(self) -> bool:
        """Return ``True`` if this symbol has any Copilot tags."""
        return len(self.copilot_tags) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise this entry to a plain dict."""
        return {
            "name": self.name,
            "qualified_name": self.qualified_name(),
            "kind": self.kind.value,
            "module": self.module,
            "stability": self.stability.value,
            "since_version": self.since_version,
            "description": self.description,
            "aliases": list(self.aliases),
            "copilot_tags": list(self.copilot_tags),
        }

    def matches_tag(self, tag: str) -> bool:
        """Return ``True`` if *tag* appears in this entry's Copilot tags."""
        return tag in self.copilot_tags


@dataclass(frozen=True, slots=True)
class DependencyEntry:
    """A declared dependency of the ``deduction_rules`` package.

    Attributes
    ----------
    package:
        Dotted package path (e.g. ``"jugeo.solver.z3_session"``).
    kind:
        :class:`DependencyKind`.
    symbols_used:
        Tuple of symbol names imported from *package*.
    rationale:
        One-line explanation of why this dependency is needed.
    """

    package: str
    kind: DependencyKind
    symbols_used: tuple[str, ...]
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise this dependency entry."""
        return {
            "package": self.package,
            "kind": self.kind.value,
            "symbols_used": list(self.symbols_used),
            "rationale": self.rationale,
        }

    def is_optional(self) -> bool:
        """Return ``True`` if this dependency can be absent at runtime."""
        return self.kind in (DependencyKind.OPTIONAL, DependencyKind.DEV_ONLY)


@dataclass(frozen=True, slots=True)
class TheoremEntry:
    """A reference to a theorem proved within this package.

    Attributes
    ----------
    theorem_id:
        Short identifier (e.g. ``"cut-elimination"``).
    name:
        Human-readable name.
    chapter_ref:
        Chapter/section reference in ``theory2.tex``.
    summary:
        One-paragraph summary.
    module:
        Module within this package that encodes the theorem.
    """

    theorem_id: str
    name: str
    chapter_ref: str
    summary: str
    module: str = "theorems"

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem entry."""
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "chapter_ref": self.chapter_ref,
            "summary": self.summary,
            "module": self.module,
        }


@dataclass(frozen=True, slots=True)
class CopilotCapability:
    """A Copilot capability exposed by this package.

    Capabilities allow the Copilot assistant to discover and invoke
    specific actions (e.g. ``"suggest_rule"``, ``"explain_transition"``).

    Attributes
    ----------
    capability_id:
        Unique identifier.
    name:
        Human-readable capability name.
    entry_point:
        Dotted path to the Python callable.
    description:
        Short description of what Copilot can do with this capability.
    input_schema:
        Dict describing expected input keys.
    output_schema:
        Dict describing returned keys.
    """

    capability_id: str
    name: str
    entry_point: str
    description: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Serialise this capability entry."""
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "entry_point": self.entry_point,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
        }

    def invoke_path(self) -> tuple[str, str]:
        """Return ``(module_path, function_name)`` for dynamic import."""
        parts = self.entry_point.rsplit(".", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return self.entry_point, "__call__"


# ---------------------------------------------------------------------------
# Package manifest
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DeductionRulesManifest:
    """The authoritative manifest for the ``deduction_rules`` package.

    Aggregates all symbol entries, dependency declarations, theorem
    references, and Copilot capabilities.

    Attributes
    ----------
    package_name:
        Full dotted package name.
    version:
        Semantic version string.
    theory_chapter:
        Chapter reference in ``theory2.tex``.
    symbols:
        All public symbols exported by this package.
    dependencies:
        All declared dependencies.
    theorems:
        Theorems proved or encoded in this package.
    copilot_capabilities:
        Capabilities exposed to the Copilot assistant.
    created_at:
        ISO-8601 timestamp when this manifest was instantiated.
    """

    package_name: str = "jugeo.encodings.deduction_rules"
    version: str = "0.1.0"
    theory_chapter: str = "Ch33"
    symbols: list[SymbolEntry] = field(default_factory=list)
    dependencies: list[DependencyEntry] = field(default_factory=list)
    theorems: list[TheoremEntry] = field(default_factory=list)
    copilot_capabilities: list[CopilotCapability] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def lookup(self, name: str) -> SymbolEntry | None:
        """Look up a symbol by its short name.

        Parameters
        ----------
        name:
            Short symbol name (e.g. ``"DeductionRule"``).

        Returns
        -------
        SymbolEntry | None
        """
        for entry in self.symbols:
            if entry.name == name:
                return entry
            if name in entry.aliases:
                return entry
        return None

    def symbols_by_kind(self, kind: SymbolKind) -> list[SymbolEntry]:
        """Return all symbols of a given *kind*.

        Parameters
        ----------
        kind:
            The :class:`SymbolKind` to filter by.
        """
        return [s for s in self.symbols if s.kind == kind]

    def symbols_by_module(self, module: str) -> list[SymbolEntry]:
        """Return all symbols defined in *module*.

        Parameters
        ----------
        module:
            Short module name within the package (e.g. ``"models"``).
        """
        return [s for s in self.symbols if s.module == module]

    def copilot_symbols(self) -> list[SymbolEntry]:
        """Return symbols that have at least one Copilot tag."""
        return [s for s in self.symbols if s.is_copilot_visible()]

    def stable_api(self) -> list[SymbolEntry]:
        """Return only stable-API symbols."""
        return [s for s in self.symbols if s.is_stable()]

    def optional_dependencies(self) -> list[DependencyEntry]:
        """Return optional and dev-only dependencies."""
        return [d for d in self.dependencies if d.is_optional()]

    def required_dependencies(self) -> list[DependencyEntry]:
        """Return required (non-optional) dependencies."""
        return [d for d in self.dependencies if not d.is_optional()]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire manifest to a plain dict.

        Returns
        -------
        dict
            JSON-compatible representation of the manifest.
        """
        return {
            "package_name": self.package_name,
            "version": self.version,
            "theory_chapter": self.theory_chapter,
            "created_at": self.created_at,
            "symbol_count": len(self.symbols),
            "symbols": [s.to_dict() for s in self.symbols],
            "dependencies": [d.to_dict() for d in self.dependencies],
            "theorems": [t.to_dict() for t in self.theorems],
            "copilot_capabilities": [c.to_dict() for c in self.copilot_capabilities],
        }

    def fingerprint(self) -> str:
        """Compute a stable fingerprint of the manifest contents.

        The fingerprint is a 16-character hex digest of all symbol names
        concatenated in alphabetical order.
        """
        names = sorted(s.name for s in self.symbols)
        payload = "|".join(names)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def summary(self) -> str:
        """Return a short human-readable summary of the manifest."""
        stable_count = len(self.stable_api())
        dep_count = len(self.dependencies)
        theorem_count = len(self.theorems)
        copilot_count = len(self.copilot_capabilities)
        return (
            f"DeductionRulesManifest v{self.version}  ({self.theory_chapter})\n"
            f"  {len(self.symbols)} symbols  ({stable_count} stable)\n"
            f"  {dep_count} dependencies  ({len(self.required_dependencies())} required)\n"
            f"  {theorem_count} theorems\n"
            f"  {copilot_count} Copilot capabilities\n"
            f"  fingerprint: {self.fingerprint()}"
        )

    def add_symbol(self, entry: SymbolEntry) -> None:
        """Register a new symbol entry.

        Raises :class:`ValueError` if a symbol with the same name already
        exists in the manifest.
        """
        if self.lookup(entry.name) is not None:
            raise ValueError(f"Symbol '{entry.name}' is already registered.")
        self.symbols.append(entry)

    def add_dependency(self, dep: DependencyEntry) -> None:
        """Register a dependency.

        Raises :class:`ValueError` on duplicate package declarations.
        """
        existing = {d.package for d in self.dependencies}
        if dep.package in existing:
            raise ValueError(f"Dependency '{dep.package}' already declared.")
        self.dependencies.append(dep)

    def add_theorem(self, theorem: TheoremEntry) -> None:
        """Register a theorem reference."""
        existing = {t.theorem_id for t in self.theorems}
        if theorem.theorem_id in existing:
            raise ValueError(f"Theorem '{theorem.theorem_id}' already registered.")
        self.theorems.append(theorem)

    def add_capability(self, cap: CopilotCapability) -> None:
        """Register a Copilot capability."""
        existing = {c.capability_id for c in self.copilot_capabilities}
        if cap.capability_id in existing:
            raise ValueError(f"Capability '{cap.capability_id}' already registered.")
        self.copilot_capabilities.append(cap)

    def validate(self) -> list[str]:
        """Validate the manifest for internal consistency.

        Checks:
        - No duplicate symbol names
        - No duplicate dependency packages
        - All modules referenced by symbols are known
        - All Copilot entry points follow dotted-path format

        Returns
        -------
        list[str]
            Validation error messages (empty = valid).
        """
        errors: list[str] = []
        # Duplicate symbol check
        names: list[str] = [s.name for s in self.symbols]
        seen: set[str] = set()
        for n in names:
            if n in seen:
                errors.append(f"Duplicate symbol name: '{n}'")
            seen.add(n)

        # Known modules
        known_modules = {
            "models", "manifest", "inference_rules",
            "judgment_transitions", "structural_rules",
            "semantic_rules", "algorithms", "integration", "theorems",
        }
        for s in self.symbols:
            if s.module not in known_modules:
                errors.append(f"Symbol '{s.name}' references unknown module '{s.module}'")

        # Capability entry-point format
        for cap in self.copilot_capabilities:
            if "." not in cap.entry_point:
                errors.append(
                    f"Capability '{cap.capability_id}' entry_point '{cap.entry_point}' "
                    "must be a dotted path"
                )
        return errors


# ---------------------------------------------------------------------------
# Module-level manifest singleton construction
# ---------------------------------------------------------------------------

def _build_manifest() -> DeductionRulesManifest:
    """Construct and return the canonical manifest for this package."""
    m = DeductionRulesManifest()

    # ---- Symbols: models.py ----
    model_symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("RuleKind",          SymbolKind.ENUM,      "Classifies a deduction rule by logical role.",          ("rule-kind", "deduction")),
        ("TransitionKind",    SymbolKind.ENUM,      "Variety of judgment-transition step.",                  ("transition", "judgment")),
        ("InferenceStatus",   SymbolKind.ENUM,      "Status of an ongoing inference chain.",                 ("inference", "status")),
        ("ApplicationResult", SymbolKind.ENUM,      "Outcome of a rule-application attempt.",                ("application", "result")),
        ("DeductionRule",     SymbolKind.DATACLASS, "A deduction rule that fires on discharged obligations.", ("rule", "deduction", "copilot")),
        ("JudgmentTransition",SymbolKind.DATACLASS, "A transition between judgment states.",                 ("transition", "judgment")),
        ("InferenceStep",     SymbolKind.DATACLASS, "A single step in a derivation tree.",                   ("inference", "step")),
        ("RuleApplication",   SymbolKind.DATACLASS, "Immutable record of a rule-application event.",         ("application", "audit")),
        ("TransitionSystem",  SymbolKind.DATACLASS, "Full system of judgment transitions.",                  ("system", "fixpoint", "copilot")),
        ("make_axiom_rule",   SymbolKind.FUNCTION,  "Factory for zero-premise axiom rules.",                 ("factory", "axiom")),
        ("make_rule",         SymbolKind.FUNCTION,  "Factory for general deduction rules.",                  ("factory", "rule")),
    ]
    for name, kind, desc, tags in model_symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="models",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: inference_rules.py ----
    symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("RuleSchema",            SymbolKind.CLASS, "Abstract schema for an inference rule.",              ("schema", "inference")),
        ("PremiseSet",            SymbolKind.CLASS, "Ordered set of premise schemas.",                     ("premises",)),
        ("ConclusionForm",        SymbolKind.CLASS, "Conclusion schema with meta-variable tracking.",      ("conclusion",)),
        ("SideConditionEvaluator",SymbolKind.CLASS, "Evaluates side conditions against bindings.",         ("side-condition",)),
        ("UnificationEngine",     SymbolKind.CLASS, "First-order unification algorithm.",                  ("unification", "copilot")),
        ("CopilotRuleSuggester",  SymbolKind.CLASS, "Copilot-assisted rule-suggestion bridge.",            ("copilot", "suggestion")),
    ]
    for name, kind, desc, tags in symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="inference_rules",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: judgment_transitions.py ----
    symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("TransitionSchema",    SymbolKind.CLASS, "Schema describing a valid transition.",             ("transition", "schema")),
        ("SubstitutionAlgebra", SymbolKind.CLASS, "Algebraic operations on substitutions.",           ("substitution", "algebra")),
        ("TransitionComposer",  SymbolKind.CLASS, "Composes sequences of transitions.",               ("composition",)),
        ("TrustDeltaComputer",  SymbolKind.CLASS, "Computes trust changes through transitions.",      ("trust", "delta")),
        ("TransitionValidator", SymbolKind.CLASS, "Validates individual transitions.",                 ("validation",)),
        ("ProofTrace",          SymbolKind.CLASS, "Full trace of a proof as a transition sequence.",  ("proof", "trace", "copilot")),
    ]
    for name, kind, desc, tags in symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="judgment_transitions",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: structural_rules.py ----
    symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("WeakeningRule",         SymbolKind.CLASS, "Weakening: Γ ⊢ J → Γ, A ⊢ J.",                 ("structural", "weakening")),
        ("ContractionRule",       SymbolKind.CLASS, "Contraction: Γ, A, A ⊢ J → Γ, A ⊢ J.",         ("structural", "contraction")),
        ("ExchangeRule",          SymbolKind.CLASS, "Exchange: Γ, A, B ⊢ J → Γ, B, A ⊢ J.",         ("structural", "exchange")),
        ("CutRule",               SymbolKind.CLASS, "Cut elimination: Γ ⊢ A; Γ, A ⊢ B → Γ ⊢ B.",    ("structural", "cut")),
        ("StructuralRuleSystem",  SymbolKind.CLASS, "System of all structural rules.",               ("structural", "system")),
        ("PermutationLemma",      SymbolKind.CLASS, "Permutation lemma for structural rules.",       ("permutation", "lemma")),
    ]
    for name, kind, desc, tags in symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="structural_rules",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: semantic_rules.py ----
    symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("IntroductionRule",        SymbolKind.CLASS, "Type/connective introduction rule.",          ("semantic", "introduction")),
        ("EliminationRule",         SymbolKind.CLASS, "Type/connective elimination rule.",           ("semantic", "elimination")),
        ("ComputationRule",         SymbolKind.CLASS, "Beta/eta computation rule.",                  ("semantic", "computation", "beta")),
        ("DefinitionalEqualityRule",SymbolKind.CLASS, "Definitional equality rule.",                 ("semantic", "equality")),
        ("SemanticRuleSystem",      SymbolKind.CLASS, "Full system of semantic rules.",              ("semantic", "system")),
        ("SoundnessChecker",        SymbolKind.CLASS, "Checks soundness of a semantic rule set.",   ("soundness", "copilot")),
    ]
    for name, kind, desc, tags in symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="semantic_rules",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: algorithms.py ----
    algo_symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("apply_deduction_rule",         SymbolKind.FUNCTION, "Apply a rule to a judgment.", ("algorithm",)),
        ("compute_transition_sequence",  SymbolKind.FUNCTION, "Build a full transition chain.", ("algorithm",)),
        ("check_rule_applicability",     SymbolKind.FUNCTION, "Test if a rule is applicable.", ("algorithm",)),
        ("unify_judgment_patterns",      SymbolKind.FUNCTION, "Unify two judgment patterns.", ("unification",)),
        ("run_transition_system",        SymbolKind.FUNCTION, "Run a transition system to fixpoint.", ("fixpoint",)),
        ("eliminate_cuts",               SymbolKind.FUNCTION, "Cut-elimination procedure.", ("cut-elimination",)),
        ("verify_proof_trace",           SymbolKind.FUNCTION, "Verify a full proof trace.", ("verification",)),
        ("synthesize_rules_for_obligations", SymbolKind.FUNCTION, "Synthesize rules for outstanding obligations.", ("synthesis", "copilot")),
        ("copilot_suggest_next_rule",    SymbolKind.FUNCTION, "Ask Copilot for the next rule.", ("copilot", "suggestion")),
    ]
    for name, kind, desc, tags in algo_symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="algorithms",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: integration.py ----
    intg_symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("DeductionSession",         SymbolKind.CLASS,    "Session managing a deduction proof.",      ("session", "copilot")),
        ("TransitionSystemRunner",   SymbolKind.CLASS,    "Runner for transition systems.",           ("runner",)),
        ("RuleApplicationTracker",   SymbolKind.CLASS,    "Tracks all rule applications.",            ("tracker", "audit")),
        ("JudgmentDischarger",       SymbolKind.CLASS,    "Discharges judgment obligations.",         ("discharge",)),
        ("CopilotDeductionAssist",   SymbolKind.CLASS,    "Copilot bridge for deduction.",            ("copilot", "assist")),
    ]
    for name, kind, desc, tags in intg_symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="integration",
            description=desc, copilot_tags=tags,
        ))

    # ---- Symbols: theorems.py ----
    thm_symbols: list[tuple[str, SymbolKind, str, tuple[str, ...]]] = [
        ("CutEliminationTheorem",         SymbolKind.DATACLASS, "Cut-elimination theorem (Ch33 §33.4).", ("theorem", "cut")),
        ("StructuralAdmissibilityTheorem",SymbolKind.DATACLASS, "Structural rule admissibility.", ("theorem", "structural")),
        ("SemanticSoundnessTheorem",      SymbolKind.DATACLASS, "Semantic rule soundness.", ("theorem", "soundness")),
        ("ConfluenceTheorem",             SymbolKind.DATACLASS, "Transition system confluence.", ("theorem", "confluence")),
        ("CompletenessTheorem",           SymbolKind.DATACLASS, "Rule completeness theorem.", ("theorem", "completeness")),
    ]
    for name, kind, desc, tags in thm_symbols:
        m.add_symbol(SymbolEntry(
            name=name, kind=kind, module="theorems",
            description=desc, copilot_tags=tags,
        ))

    # ---- Dependencies ----
    deps: list[tuple[str, DependencyKind, tuple[str, ...], str]] = [
        (
            "jugeo.solver.z3_session",
            DependencyKind.OPTIONAL,
            ("Z3Session", "Z3Formula", "Z3Encoder", "Z3Result"),
            "Z3 encoding for side-condition checking and soundness verification.",
        ),
        (
            "jugeo.solver.reconstruction",
            DependencyKind.OPTIONAL,
            ("ModelReconstruction",),
            "Reconstructs proof witnesses from Z3 models.",
        ),
        (
            "jugeo.judgments.judgment_terms",
            DependencyKind.OPTIONAL,
            ("JudgmentTerm",),
            "Judgment term representation used by rules and transitions.",
        ),
        (
            "jugeo.evidence.trust",
            DependencyKind.OPTIONAL,
            ("TrustAlgebra", "TrustLevel"),
            "Trust algebra for trust-level propagation through transitions.",
        ),
    ]
    for pkg, kind, syms, rationale in deps:
        m.add_dependency(DependencyEntry(
            package=pkg, kind=kind, symbols_used=syms, rationale=rationale,
        ))

    # ---- Theorems ----
    theorem_entries: list[tuple[str, str, str, str]] = [
        (
            "cut-elimination",
            "Cut Elimination",
            "theory2.tex §33.4",
            "The cut rule is admissible: any proof using cut can be transformed "
            "into a cut-free proof of the same sequent.",
        ),
        (
            "structural-admissibility",
            "Structural Rule Admissibility",
            "theory2.tex §33.2",
            "Weakening and contraction are admissible in the core system.",
        ),
        (
            "semantic-soundness",
            "Semantic Rule Soundness",
            "theory2.tex §33.3",
            "Every derivable judgment is valid in the intended semantics.",
        ),
        (
            "confluence",
            "Transition System Confluence",
            "theory2.tex §33.5",
            "The transition system is confluent: all reduction paths converge.",
        ),
        (
            "completeness",
            "Rule Completeness",
            "theory2.tex §33.6",
            "The rule set is complete: every valid judgment is derivable.",
        ),
    ]
    for tid, name, ref, summary in theorem_entries:
        m.add_theorem(TheoremEntry(
            theorem_id=tid, name=name, chapter_ref=ref, summary=summary,
        ))

    # ---- Copilot Capabilities ----
    cap_entries: list[tuple[str, str, str, str, dict[str, str], dict[str, str]]] = [
        (
            "suggest-rule",
            "Suggest Deduction Rule",
            "jugeo.encodings.deduction_rules.integration.CopilotDeductionAssist.suggest_rule",
            "Given a partial judgment, suggest the most applicable deduction rule.",
            {"judgment": "str", "context": "dict"},
            {"rules": "list[str]", "scores": "list[float]"},
        ),
        (
            "explain-transition",
            "Explain Judgment Transition",
            "jugeo.encodings.deduction_rules.integration.CopilotDeductionAssist.explain_transition",
            "Explain why a particular judgment transition was made.",
            {"transition_id": "str"},
            {"explanation": "str"},
        ),
        (
            "complete-proof",
            "Complete Proof",
            "jugeo.encodings.deduction_rules.integration.CopilotDeductionAssist.complete_proof",
            "Given a partial proof, suggest the next step.",
            {"partial_proof": "list[dict]", "goal": "str"},
            {"next_step": "dict", "confidence": "float"},
        ),
    ]
    for cid, name, ep, desc, inp, out in cap_entries:
        m.add_capability(CopilotCapability(
            capability_id=cid, name=name, entry_point=ep,
            description=desc, input_schema=inp, output_schema=out,
        ))

    return m


# Module-level singleton
MANIFEST: DeductionRulesManifest = _build_manifest()
"""The canonical manifest for the ``deduction_rules`` package."""


def get_manifest() -> DeductionRulesManifest:
    """Return the module-level manifest singleton."""
    return MANIFEST


__all__ = [
    "SymbolKind",
    "StabilityLevel",
    "DependencyKind",
    "SymbolEntry",
    "DependencyEntry",
    "TheoremEntry",
    "CopilotCapability",
    "DeductionRulesManifest",
    "MANIFEST",
    "get_manifest",
]
