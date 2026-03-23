"""
Python programs are saturated with partiality — None returns, exceptions, missing attributes.

This module models partiality as a structural property of Python programs. Every function
that can return None, raise an exception, or access a potentially missing attribute is
partial. We encode these as judgment tuples and generate obligations to handle them.

# copilot:
"""

from __future__ import annotations

import time
import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional jugeo imports — graceful degradation when running standalone
# ---------------------------------------------------------------------------

try:
    from jugeo.core.trust import TrustTier
    from jugeo.core.judgment import Judgment
    from jugeo.core.obstruction import CechObstruction
except ImportError:
    from enum import Enum
    class TrustTier(Enum):
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5
    Judgment = tuple  # (c, φ, A, E, O, B, T, Π)
    @dataclass(frozen=True)
    class CechObstruction:
        cochain_id: str
        cochain_data: tuple
        coboundary_map: tuple
        obstruction_class: str
        tier: TrustTier


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Maximum number of partiality sources to enumerate per function description
MAX_SOURCES_PER_FUNCTION: int = 64

# Sentinel string used to indicate an undischarged obligation
UNDISCHARGED_SENTINEL: str = "__UNDISCHARGED__"

# Prefix used when generating obligation identifiers from source ids
OBLIGATION_ID_PREFIX: str = "OBL"

# The set of known partiality kinds that always require explicit handling
CRITICAL_PARTIALITY_KINDS: frozenset = frozenset({
    "none_return",
    "exception_raise",
    "missing_attribute",
    "index_out_of_bounds",
    "key_not_found",
})

# Default tier assigned to newly discovered partiality sources
DEFAULT_SOURCE_TIER: TrustTier = TrustTier.PROPOSAL


# ---------------------------------------------------------------------------
# PartialitySource
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartialitySource:
    """A single identified source of partiality in a Python program.

    Partiality sources represent locations or patterns in code where a function
    may be undefined — i.e., where it can return None, raise an exception, or
    access an attribute that may not exist. Each source is recorded as a frozen
    dataclass so that it can be used safely in sets and as dict keys.

    The tier field encodes how well-established the identification of this
    source is, following the TrustTier ordered algebra.
    """

    source_id: str
    source_kind: str
    location: str
    description: str
    tier: TrustTier

    def is_critical(self) -> bool:
        """Return True if this source requires mandatory handling.

        A source is critical when its trust tier has reached VERIFIED or above,
        meaning the source has been confirmed by review or runtime evidence. Any
        source in CRITICAL_PARTIALITY_KINDS is also considered critical regardless
        of tier.

        Returns:
            bool: True when tier.value >= TrustTier.VERIFIED.value or when the
                source_kind appears in CRITICAL_PARTIALITY_KINDS.
        """
        return (
            self.tier.value >= TrustTier.VERIFIED.value
            or self.source_kind in CRITICAL_PARTIALITY_KINDS
        )

    def obligation_generated(self) -> str:
        """Describe the proof obligation this partiality source creates.

        Every partiality source obligates the programmer to either (a) add a
        precondition that prevents the undefined case, (b) handle the undefined
        case with a default, or (c) propagate the partiality upward. This method
        returns a human-readable description of the obligation.

        Returns:
            str: A string describing what must be proved or handled.
        """
        return (
            f"Obligation[{self.source_id}]: Prove that '{self.description}' is handled "
            f"at {self.location} (kind={self.source_kind}, tier={self.tier.name})"
        )

    def as_domain_gap(self) -> str:
        """Describe the gap in the domain introduced by this partiality source.

        The domain gap is the set of inputs for which the function is undefined.
        This method returns a symbolic description of that gap suitable for use
        in proof obligations and domain restriction predicates.

        Returns:
            str: A symbolic description of the domain gap.
        """
        kind_to_gap = {
            "none_return": "inputs where result may be None",
            "exception_raise": f"inputs that trigger {self.source_kind} at {self.location}",
            "missing_attribute": f"objects lacking attribute referenced at {self.location}",
            "index_out_of_bounds": f"indices outside sequence bounds at {self.location}",
            "key_not_found": f"keys absent from mapping at {self.location}",
        }
        return kind_to_gap.get(
            self.source_kind,
            f"undefined inputs for {self.description} at {self.location}",
        )

    def to_judgment_tuple(self) -> tuple:
        """Return this source as a judgment 8-tuple (c, φ, A, E, O, B, T, Π).

        The judgment tuple encodes the partiality source in the canonical theory
        format. All eight components are strings so the tuple can be compared,
        hashed, and stored without special serialisation logic.

        Returns:
            tuple: An 8-tuple of strings (c, φ, A, E, O, B, T, Π).
        """
        return (
            f"context:{self.location}",          # c  — context
            f"partial:{self.description}",        # φ  — formula
            f"agent:partiality_analyzer",         # A  — agent
            f"evidence:{self.tier.name}",         # E  — evidence
            f"obligation:{self.obligation_generated()}",  # O
            f"blame:{self.source_id}",            # B  — blame
            f"tier:{self.tier.name}",             # T  — trust tier
            f"proof:gap={self.as_domain_gap()}",  # Π  — proof sketch
        )

    def summarize(self) -> str:
        """Return a one-line human-readable summary of this partiality source.

        The summary is compact enough to display in a table or log line, including
        the source id, kind, tier, and a truncated description.

        Returns:
            str: A concise single-line description.
        """
        desc_short = self.description[:60] + "…" if len(self.description) > 60 else self.description
        return f"[{self.tier.name}] {self.source_id} ({self.source_kind}) @ {self.location}: {desc_short}"


# ---------------------------------------------------------------------------
# PartialDomain
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartialDomain:
    """The domain of a partial function encoded as explicit total/defined/undefined sets.

    A partial function f : X ⇀ Y has a domain of definition dom(f) ⊆ X. The total
    domain is X, the defined subdomain is dom(f), and the undefined region is X \\ dom(f).
    This class records all three symbolically using frozensets of string identifiers.

    The domain_tier encodes how reliable the domain description is in the TrustTier algebra.
    Frozen so that instances can be stored in sets and used as dict keys.
    """

    domain_id: str
    total_domain: frozenset
    defined_subdomain: frozenset
    undefined_region: frozenset
    domain_tier: TrustTier

    def is_total(self) -> bool:
        """Return True if the function is total — defined on every element of the domain.

        Totality holds when the defined subdomain covers all elements of the total
        domain. An empty total domain is trivially total.

        Returns:
            bool: True iff defined_subdomain >= total_domain as sets.
        """
        return self.defined_subdomain >= self.total_domain

    def partiality_fraction(self) -> str:
        """Return the fraction of the total domain on which the function is defined.

        The fraction is expressed as "defined_count/total_count" where both counts
        are the sizes of the respective frozensets. If the total domain is empty
        the fraction is reported as "0/0".

        Returns:
            str: A string of the form "X/Y".
        """
        total_n = len(self.total_domain)
        defined_n = len(self.defined_subdomain)
        if total_n == 0:
            return "0/0"
        return f"{defined_n}/{total_n}"

    def extend_domain(self, new_def: frozenset) -> PartialDomain:
        """Return a new PartialDomain with additional elements marked as defined.

        The extended domain incorporates new_def into the defined subdomain and
        removes those elements from the undefined region. The total domain is
        also extended if new_def contains elements not already in it.

        Args:
            new_def: A frozenset of elements to add to the defined subdomain.

        Returns:
            PartialDomain: A new frozen instance with the extended definition.
        """
        extended_defined = self.defined_subdomain | new_def
        extended_total = self.total_domain | new_def
        new_undefined = extended_total - extended_defined
        return PartialDomain(
            domain_id=f"{self.domain_id}+ext",
            total_domain=extended_total,
            defined_subdomain=extended_defined,
            undefined_region=new_undefined,
            domain_tier=self.domain_tier,
        )

    def complement(self) -> frozenset:
        """Return the complement — elements of the total domain not in the defined subdomain.

        This is the undefined region of the partial function, computed as the
        set-theoretic difference total_domain \\ defined_subdomain.

        Returns:
            frozenset: The undefined region.
        """
        return self.total_domain - self.defined_subdomain

    def to_judgment_tuple(self) -> tuple:
        """Encode this partial domain as a judgment 8-tuple (c, φ, A, E, O, B, T, Π).

        All eight components are strings. The tuple follows the canonical theory
        format with components for context, formula, agent, evidence, obligation,
        blame, trust tier, and proof sketch.

        Returns:
            tuple: An 8-tuple of strings.
        """
        frac = self.partiality_fraction()
        undefined_list = ",".join(sorted(str(x) for x in self.complement()))
        return (
            f"context:domain:{self.domain_id}",                  # c
            f"partial_domain:fraction={frac}",                   # φ
            "agent:domain_builder",                              # A
            f"evidence:total={len(self.total_domain)}",          # E
            f"obligation:cover_undefined=[{undefined_list}]",    # O
            f"blame:{self.domain_id}",                           # B
            f"tier:{self.domain_tier.name}",                     # T
            f"proof:is_total={self.is_total()}",                 # Π
        )


# ---------------------------------------------------------------------------
# PartialnessObligation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartialnessObligation:
    """A proof obligation that arises from a detected partiality source.

    Each partiality source identified in a program gives rise to an obligation
    to handle the undefined case. Obligations track which source created them,
    what strategy has been chosen for handling it (if any), and what evidence
    exists that the obligation has been discharged.

    Obligations are immutable; discharging returns a new instance with the
    discharge_evidence field populated.
    """

    obligation_id: str
    source: PartialitySource
    handling_strategy: str
    discharge_evidence: str
    obligation_tier: TrustTier

    def is_discharged(self) -> bool:
        """Return True if this obligation has been satisfied by evidence.

        An obligation is discharged when discharge_evidence is non-empty,
        meaning some proof, test, or annotation has addressed the partiality.

        Returns:
            bool: True iff discharge_evidence != "".
        """
        return self.discharge_evidence != ""

    def discharge(self, evidence: str) -> PartialnessObligation:
        """Return a new obligation instance with the given evidence attached.

        Since obligations are frozen, this method cannot mutate in place.
        Instead it creates a new PartialnessObligation with discharge_evidence
        set and the obligation_tier promoted by one step to reflect the new
        confidence.

        Args:
            evidence: A string describing the proof or handling that satisfies
                the obligation (e.g. a test name, a precondition, a default).

        Returns:
            PartialnessObligation: A new frozen instance with evidence recorded.
        """
        new_tier_value = min(self.obligation_tier.value + 1, TrustTier.PROOF_BACKED.value)
        new_tier = TrustTier(new_tier_value)
        return PartialnessObligation(
            obligation_id=self.obligation_id,
            source=self.source,
            handling_strategy=self.handling_strategy,
            discharge_evidence=evidence,
            obligation_tier=new_tier,
        )

    def as_proof_goal(self) -> str:
        """Return a string formulation of what must be proved to discharge this obligation.

        The proof goal is expressed as a proposition that, when verified, eliminates
        the partiality at the source location. The formulation references the source's
        domain gap and the chosen handling strategy.

        Returns:
            str: A string encoding the proof goal.
        """
        gap = self.source.as_domain_gap()
        if self.handling_strategy:
            return (
                f"PROVE: strategy '{self.handling_strategy}' correctly handles {gap} "
                f"for obligation {self.obligation_id}"
            )
        return (
            f"PROVE: {gap} is handled at {self.source.location} "
            f"(obligation {self.obligation_id} has no strategy yet)"
        )

    def to_judgment_tuple(self) -> tuple:
        """Encode this obligation as a judgment 8-tuple (c, φ, A, E, O, B, T, Π).

        Returns:
            tuple: An 8-tuple of strings following (c, φ, A, E, O, B, T, Π).
        """
        discharged_str = "discharged" if self.is_discharged() else UNDISCHARGED_SENTINEL
        return (
            f"context:{self.source.location}",             # c
            f"obligation:{self.source.description}",       # φ
            "agent:obligation_tracker",                    # A
            f"evidence:{self.discharge_evidence or 'none'}",  # E
            f"obligation:{self.as_proof_goal()}",          # O
            f"blame:{self.source.source_id}",              # B
            f"tier:{self.obligation_tier.name}",           # T
            f"proof:status={discharged_str}",              # Π
        )

    def obligation_key(self) -> str:
        """Return a unique string key identifying this obligation.

        The key combines the obligation_id with a short hash of the source
        description and location so that keys remain stable and unique even
        when obligation_ids are reused across modules.

        Returns:
            str: A unique key string.
        """
        raw = f"{self.obligation_id}|{self.source.source_id}|{self.source.location}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        return f"{OBLIGATION_ID_PREFIX}-{self.obligation_id}-{digest}"


# ---------------------------------------------------------------------------
# TotalExtension
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TotalExtension:
    """A totalization of a partial function by extending its domain with a default.

    A total extension of a partial function f : dom(f) ⇀ Y is a total function
    f̄ : X → Y such that f̄(x) = f(x) for x ∈ dom(f) and f̄(x) = d for x ∉ dom(f),
    where d is a chosen default value. This class records the extension symbolically.

    The extension_tier tracks how well-justified the choice of default value is.
    Frozen so instances can be stored as dict keys or in sets.
    """

    extension_id: str
    partial_domain_id: str
    extension_strategy: str
    default_value_description: str
    extension_tier: TrustTier

    def is_sound(self) -> bool:
        """Return True if the extension is well-formed and has a concrete strategy.

        An extension is sound when both the extension_strategy and the
        default_value_description are non-empty strings, indicating that the
        extension is fully specified rather than a placeholder.

        Returns:
            bool: True iff both strategy and default description are non-empty.
        """
        return self.extension_strategy != "" and self.default_value_description != ""

    def apply_to_undefined(self, x: str) -> str:
        """Describe what value the total extension returns for an undefined input x.

        For inputs outside the partial domain, the extension returns the default
        value. This method produces a human-readable description of that mapping
        suitable for documentation and proof obligations.

        Args:
            x: A string naming the input element.

        Returns:
            str: A description of f̄(x) for x outside dom(f).
        """
        if not self.is_sound():
            return f"f̄({x}) = ⊥  [extension {self.extension_id} is not yet sound]"
        return (
            f"f̄({x}) = {self.default_value_description}  "
            f"[via strategy '{self.extension_strategy}' in extension {self.extension_id}]"
        )

    def cost(self) -> int:
        """Return a numeric cost estimate for this extension.

        The cost is a proxy for how expensive or invasive the extension strategy
        is. It is computed as the product of the tier value and the length of the
        strategy string, giving higher cost to more complex strategies at higher
        trust tiers.

        Returns:
            int: A non-negative integer cost.
        """
        return self.extension_tier.value * len(self.extension_strategy)

    def to_judgment_tuple(self) -> tuple:
        """Encode this total extension as a judgment 8-tuple (c, φ, A, E, O, B, T, Π).

        Returns:
            tuple: An 8-tuple of strings following (c, φ, A, E, O, B, T, Π).
        """
        sound_str = "sound" if self.is_sound() else "unsound"
        return (
            f"context:domain:{self.partial_domain_id}",           # c
            f"total_extension:{self.extension_id}",               # φ
            "agent:extension_builder",                            # A
            f"evidence:strategy={self.extension_strategy}",       # E
            f"obligation:verify_default={self.default_value_description}",  # O
            f"blame:{self.partial_domain_id}",                    # B
            f"tier:{self.extension_tier.name}",                   # T
            f"proof:soundness={sound_str},cost={self.cost()}",    # Π
        )

    def describe(self) -> str:
        """Return a human-readable multi-line description of this total extension.

        The description includes the extension id, the strategy, the default value,
        the cost, and whether the extension is currently sound. It is intended for
        display in reports and documentation.

        Returns:
            str: A formatted multi-line description string.
        """
        lines = [
            f"TotalExtension '{self.extension_id}'",
            f"  Partial domain : {self.partial_domain_id}",
            f"  Strategy       : {self.extension_strategy or '(none)'}",
            f"  Default value  : {self.default_value_description or '(none)'}",
            f"  Tier           : {self.extension_tier.name}",
            f"  Cost estimate  : {self.cost()}",
            f"  Sound          : {self.is_sound()}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PartialityAnalyzer
# ---------------------------------------------------------------------------

class PartialityAnalyzer:
    """Analyzes a function description for sources of partiality and generates obligations.

    The analyzer maintains an internal list of PartialitySource instances discovered
    during analysis. It provides methods to scan function descriptions for common
    partiality patterns, find None-returning patterns, find unsafe attribute accesses,
    generate obligations, and produce a summary report.
    """

    def __init__(self) -> None:
        """Initialise the analyzer with an empty list of discovered sources."""
        self._sources: list[PartialitySource] = []

    def analyze_function(self, func_name: str, body_desc: str) -> list:
        """Analyze a function description for sources of partiality.

        Scans body_desc for keyword patterns associated with partiality (None
        returns, exception raises, attribute accesses, subscripts) and creates
        a PartialitySource for each pattern found. The results are also stored
        internally so that subsequent calls to report_obligations can use them.

        Args:
            func_name: The name of the function being analyzed.
            body_desc: A textual description or pseudocode of the function body.

        Returns:
            list: A list of PartialitySource instances discovered.
        """
        found: list[PartialitySource] = []
        patterns = [
            ("none_return",        ["return None", "-> None", "Optional", "may return None"]),
            ("exception_raise",    ["raise", "Exception", "Error", "throws"]),
            ("missing_attribute",  [".attr", "getattr", "hasattr", "AttributeError"]),
            ("index_out_of_bounds", ["[i]", "[n]", "[0]", "IndexError", "index"]),
            ("key_not_found",      ["[key]", "KeyError", ".get(", "mapping"]),
        ]
        for kind, keywords in patterns:
            for kw in keywords:
                if kw.lower() in body_desc.lower():
                    src_id = f"{func_name}_{kind}_{hashlib.md5(kw.encode()).hexdigest()[:4]}"
                    source = PartialitySource(
                        source_id=src_id,
                        source_kind=kind,
                        location=f"{func_name}:body",
                        description=f"Pattern '{kw}' suggests {kind} in {func_name}",
                        tier=DEFAULT_SOURCE_TIER,
                    )
                    found.append(source)
                    break  # one source per kind per function is sufficient
        self._sources.extend(found)
        return found

    def find_none_returns(self) -> list:
        """Find all currently known sources of kind 'none_return'.

        Filters the internal source list for sources whose source_kind is
        'none_return', representing functions that may return None.

        Returns:
            list: A list of PartialitySource instances with kind 'none_return'.
        """
        return [s for s in self._sources if s.source_kind == "none_return"]

    def find_attribute_accesses(self) -> list:
        """Find all currently known sources of kind 'missing_attribute'.

        Filters the internal source list for sources whose source_kind is
        'missing_attribute', representing potentially unsafe attribute accesses.

        Returns:
            list: A list of PartialitySource instances with kind 'missing_attribute'.
        """
        return [s for s in self._sources if s.source_kind == "missing_attribute"]

    def report_obligations(self) -> list:
        """Generate a PartialnessObligation for each known partiality source.

        Each source in the internal list produces exactly one obligation. The
        obligation_id is derived from the source_id using the OBLIGATION_ID_PREFIX
        constant. The handling_strategy and discharge_evidence are initially empty,
        indicating unhandled obligations.

        Returns:
            list: A list of PartialnessObligation instances.
        """
        obligations = []
        for source in self._sources:
            obl = PartialnessObligation(
                obligation_id=f"{OBLIGATION_ID_PREFIX}_{source.source_id}",
                source=source,
                handling_strategy="",
                discharge_evidence="",
                obligation_tier=source.tier,
            )
            obligations.append(obl)
        return obligations

    def summarize(self) -> str:
        """Return a text summary of all discovered sources and obligations.

        The summary lists the total number of sources, the number of critical
        sources, and a bulleted list of one-line summaries for each source.

        Returns:
            str: A multi-line summary string.
        """
        if not self._sources:
            return "PartialityAnalyzer: no sources discovered yet."
        critical = [s for s in self._sources if s.is_critical()]
        lines = [
            f"PartialityAnalyzer summary: {len(self._sources)} source(s), "
            f"{len(critical)} critical",
        ]
        for src in self._sources:
            lines.append(f"  • {src.summarize()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone functions
# ---------------------------------------------------------------------------

def enumerate_partiality_sources(function_description: str) -> list:
    """Enumerate all partiality sources found in a plain-text function description.

    This function creates a temporary PartialityAnalyzer and uses it to scan the
    given description string for common partiality patterns. It returns the list
    of discovered PartialitySource instances, each annotated with tier PROPOSAL
    since they are identified by heuristic keyword matching rather than formal
    analysis.

    Args:
        function_description: A plain-text description of a Python function,
            possibly including pseudocode, docstring text, or code snippets.

    Returns:
        list: A list of PartialitySource instances, possibly empty.
    """
    analyzer = PartialityAnalyzer()
    func_name = "anonymous"
    # Derive a stable name from the first 32 chars of the description
    if function_description:
        slug = function_description[:32].replace(" ", "_").replace("\n", "_")
        func_name = f"fn_{hashlib.md5(slug.encode()).hexdigest()[:6]}"
    return analyzer.analyze_function(func_name, function_description)


def build_partial_domain(total: set, defined: set, tier: TrustTier) -> PartialDomain:
    """Construct a PartialDomain from ordinary sets and a trust tier.

    Converts the mutable sets to frozensets and computes the undefined region as
    total \\ defined. The domain_id is generated from the current nanosecond
    timestamp to ensure uniqueness.

    Args:
        total: The full set of inputs the function is intended to handle.
        defined: The subset of inputs for which the function is actually defined.
        tier: The TrustTier indicating confidence in the domain description.

    Returns:
        PartialDomain: A frozen dataclass instance representing the partial domain.
    """
    total_fs = frozenset(total)
    defined_fs = frozenset(defined)
    undefined_fs = total_fs - defined_fs
    domain_id = f"domain_{time.time_ns()}"
    return PartialDomain(
        domain_id=domain_id,
        total_domain=total_fs,
        defined_subdomain=defined_fs,
        undefined_region=undefined_fs,
        domain_tier=tier,
    )


def total_extension(
    partial_id: str,
    strategy: str,
    default_desc: str,
    tier: TrustTier,
) -> TotalExtension:
    """Create a TotalExtension for a named partial domain.

    Convenience constructor that generates a stable extension_id from the
    partial_id and strategy strings using a short SHA-256 digest.

    Args:
        partial_id: The domain_id of the partial domain being extended.
        strategy: A string describing the extension strategy (e.g. 'return None',
            'raise ValueError', 'return default').
        default_desc: A description of the default value returned for undefined inputs.
        tier: TrustTier expressing confidence in the extension.

    Returns:
        TotalExtension: A frozen instance fully describing the total extension.
    """
    raw = f"{partial_id}|{strategy}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    extension_id = f"ext_{partial_id}_{digest}"
    return TotalExtension(
        extension_id=extension_id,
        partial_domain_id=partial_id,
        extension_strategy=strategy,
        default_value_description=default_desc,
        extension_tier=tier,
    )


def classify_partiality(source: PartialitySource) -> str:
    """Classify a partiality source into a broad category string.

    Returns one of four category strings:
    - 'value_partiality'     — partiality due to value-level conditions (None, missing key)
    - 'structural_partiality' — partiality due to structural mismatches (wrong type, attribute)
    - 'resource_partiality'  — partiality due to external resource availability (import, IO)
    - 'arithmetic_partiality' — partiality due to numeric undefined operations (division)

    Args:
        source: A PartialitySource instance to classify.

    Returns:
        str: One of the four category strings above.
    """
    value_kinds = {"none_return", "key_not_found", "index_out_of_bounds"}
    structural_kinds = {"missing_attribute", "exception_raise"}
    resource_kinds = {"import_error", "io_error"}
    arithmetic_kinds = {"zero_division", "overflow", "recursion_limit"}

    if source.source_kind in value_kinds:
        return "value_partiality"
    if source.source_kind in structural_kinds:
        return "structural_partiality"
    if source.source_kind in resource_kinds:
        return "resource_partiality"
    if source.source_kind in arithmetic_kinds:
        return "arithmetic_partiality"
    # Default fallback: classify by keyword presence in description
    desc_lower = source.description.lower()
    if any(w in desc_lower for w in ("none", "null", "missing", "absent")):
        return "value_partiality"
    if any(w in desc_lower for w in ("type", "attribute", "struct")):
        return "structural_partiality"
    return "value_partiality"


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    t_start = time.time_ns()
    print("=== s01 smoke test starting ===")

    # --- TrustTier sanity ---
    assert TrustTier.PROPOSAL.value == 1
    assert TrustTier.PROOF_BACKED.value == 5
    print(f"[1] TrustTier tiers: {[t.name for t in TrustTier]}")

    # --- PartialitySource ---
    src = PartialitySource(
        source_id="src_001",
        source_kind="none_return",
        location="module:get_user:line42",
        description="get_user() may return None when user_id is not found in the database",
        tier=TrustTier.PROPOSAL,
    )
    assert not src.is_critical()  # PROPOSAL < VERIFIED
    src_verified = PartialitySource(
        source_id="src_002",
        source_kind="none_return",
        location="module:fetch:line10",
        description="fetch() returns None on 404",
        tier=TrustTier.VERIFIED,
    )
    assert src_verified.is_critical()
    gap = src.as_domain_gap()
    assert "None" in gap
    obligation_desc = src.obligation_generated()
    assert "src_001" in obligation_desc
    jt = src.to_judgment_tuple()
    assert len(jt) == 8
    assert all(isinstance(s, str) for s in jt)
    print(f"[2] PartialitySource summary: {src.summarize()}")
    print(f"    judgment tuple[0]: {jt[0]}")

    # --- PartialDomain ---
    total_set = frozenset({"a", "b", "c", "d", "e"})
    defined_set = frozenset({"a", "b", "c"})
    undefined_set = frozenset({"d", "e"})
    pd = PartialDomain(
        domain_id="dom_001",
        total_domain=total_set,
        defined_subdomain=defined_set,
        undefined_region=undefined_set,
        domain_tier=TrustTier.REVIEWED,
    )
    assert not pd.is_total()
    assert pd.partiality_fraction() == "3/5"
    complement = pd.complement()
    assert complement == frozenset({"d", "e"})
    extended_pd = pd.extend_domain(frozenset({"d"}))
    assert "d" in extended_pd.defined_subdomain
    assert not extended_pd.is_total()  # "e" still undefined
    pd_jt = pd.to_judgment_tuple()
    assert len(pd_jt) == 8
    assert all(isinstance(s, str) for s in pd_jt)
    print(f"[3] PartialDomain fraction: {pd.partiality_fraction()}, complement: {complement}")

    # --- PartialnessObligation ---
    obl = PartialnessObligation(
        obligation_id="obl_001",
        source=src,
        handling_strategy="",
        discharge_evidence="",
        obligation_tier=TrustTier.PROPOSAL,
    )
    assert not obl.is_discharged()
    proof_goal = obl.as_proof_goal()
    assert "obl_001" in proof_goal
    obl_key = obl.obligation_key()
    assert obl_key.startswith(OBLIGATION_ID_PREFIX)
    discharged_obl = obl.discharge("test_get_user_none_handled_in_test_suite.py:line99")
    assert discharged_obl.is_discharged()
    assert discharged_obl.obligation_tier.value > obl.obligation_tier.value
    obl_jt = obl.to_judgment_tuple()
    assert len(obl_jt) == 8
    assert all(isinstance(s, str) for s in obl_jt)
    print(f"[4] Obligation key: {obl_key}")
    print(f"    Discharged tier: {discharged_obl.obligation_tier.name}")

    # --- TotalExtension ---
    ext = TotalExtension(
        extension_id="ext_001",
        partial_domain_id="dom_001",
        extension_strategy="return default_user",
        default_value_description="User(id=-1, name='anonymous')",
        extension_tier=TrustTier.REVIEWED,
    )
    assert ext.is_sound()
    apply_result = ext.apply_to_undefined("missing_user_42")
    assert "default_user" in apply_result
    assert ext.cost() == TrustTier.REVIEWED.value * len(ext.extension_strategy)
    ext_jt = ext.to_judgment_tuple()
    assert len(ext_jt) == 8
    assert all(isinstance(s, str) for s in ext_jt)
    ext_desc = ext.describe()
    assert "TotalExtension" in ext_desc
    print(f"[5] TotalExtension cost: {ext.cost()}")
    print(f"    apply_to_undefined('missing_user_42'): {apply_result}")

    # --- TotalExtension unsound ---
    ext_empty = TotalExtension(
        extension_id="ext_empty",
        partial_domain_id="dom_001",
        extension_strategy="",
        default_value_description="",
        extension_tier=TrustTier.PROPOSAL,
    )
    assert not ext_empty.is_sound()
    apply_empty = ext_empty.apply_to_undefined("x")
    assert "not yet sound" in apply_empty
    print(f"[6] Unsound extension apply: {apply_empty}")

    # --- PartialityAnalyzer ---
    analyzer = PartialityAnalyzer()
    found = analyzer.analyze_function(
        "fetch_record",
        "This function may return None if the record is not found. "
        "It also performs getattr access on the result object. "
        "It raises ValueError if the id is negative.",
    )
    assert len(found) >= 1
    none_returns = analyzer.find_none_returns()
    attr_accesses = analyzer.find_attribute_accesses()
    print(f"[7] Analyzer found {len(found)} source(s)")
    print(f"    None-return sources: {len(none_returns)}")
    print(f"    Attribute access sources: {len(attr_accesses)}")

    # Analyze a second function to accumulate more sources
    analyzer.analyze_function(
        "safe_divide",
        "Divides two numbers. Uses index [0] to get numerator from list. "
        "Returns None if denominator is zero.",
    )
    obligations = analyzer.report_obligations()
    assert len(obligations) >= 2
    summary = analyzer.summarize()
    assert "PartialityAnalyzer" in summary
    print(f"[8] Total obligations generated: {len(obligations)}")
    print(f"    Summary:\n{summary}")

    # --- CechObstruction ---
    obs = CechObstruction(
        cochain_id="cech_001",
        cochain_data=("U0:s0", "U1:s1", "U2:s2"),
        coboundary_map=(("U0", "U1", "s0-s1"), ("U1", "U2", "s1-s2")),
        obstruction_class="H1_partiality_gluing",
        tier=TrustTier.REVIEWED,
    )
    assert obs.cochain_id == "cech_001"
    assert obs.tier == TrustTier.REVIEWED
    print(f"[9] CechObstruction: id={obs.cochain_id}, class={obs.obstruction_class}")

    # --- Standalone functions ---
    # enumerate_partiality_sources
    sources_list = enumerate_partiality_sources(
        "This function returns None when the user is not authenticated. "
        "It may raise AttributeError if the session object lacks the 'user' attribute."
    )
    assert isinstance(sources_list, list)
    print(f"[10] enumerate_partiality_sources found: {len(sources_list)} source(s)")

    # build_partial_domain
    built_pd = build_partial_domain(
        total={"x1", "x2", "x3", "x4"},
        defined={"x1", "x2"},
        tier=TrustTier.VERIFIED,
    )
    assert isinstance(built_pd, PartialDomain)
    assert built_pd.partiality_fraction() == "2/4"
    assert not built_pd.is_total()
    print(f"[11] build_partial_domain fraction: {built_pd.partiality_fraction()}")

    # total_extension
    te = total_extension(
        partial_id=built_pd.domain_id,
        strategy="return sentinel_value",
        default_desc="SENTINEL (a typed null object)",
        tier=TrustTier.VERIFIED,
    )
    assert isinstance(te, TotalExtension)
    assert te.is_sound()
    assert te.partial_domain_id == built_pd.domain_id
    print(f"[12] total_extension id: {te.extension_id}")
    print(f"     cost: {te.cost()}")

    # classify_partiality
    cat_src = PartialitySource(
        source_id="cls_001",
        source_kind="none_return",
        location="module:f",
        description="returns None",
        tier=TrustTier.PROPOSAL,
    )
    cat = classify_partiality(cat_src)
    assert cat == "value_partiality", f"Expected value_partiality, got {cat}"

    cat_src2 = PartialitySource(
        source_id="cls_002",
        source_kind="missing_attribute",
        location="module:g",
        description="getattr access",
        tier=TrustTier.PROPOSAL,
    )
    cat2 = classify_partiality(cat_src2)
    assert cat2 == "structural_partiality", f"Expected structural_partiality, got {cat2}"

    cat_src3 = PartialitySource(
        source_id="cls_003",
        source_kind="zero_division",
        location="module:h",
        description="division",
        tier=TrustTier.PROPOSAL,
    )
    cat3 = classify_partiality(cat_src3)
    assert cat3 == "arithmetic_partiality", f"Expected arithmetic_partiality, got {cat3}"
    print(f"[13] classify_partiality: {cat}, {cat2}, {cat3}")

    # --- itertools usage (required import exercise) ---
    all_sources = [src, src_verified, cat_src, cat_src2, cat_src3]
    pairs = list(itertools.combinations(all_sources, 2))
    print(f"[14] Source pairs via itertools.combinations: {len(pairs)}")

    # --- Judgment tuple structure check across all classes ---
    all_tuples = [
        src.to_judgment_tuple(),
        pd.to_judgment_tuple(),
        obl.to_judgment_tuple(),
        ext.to_judgment_tuple(),
    ]
    for i, jt_item in enumerate(all_tuples):
        assert len(jt_item) == 8, f"Judgment tuple {i} has {len(jt_item)} elements, expected 8"
        assert all(isinstance(s, str) for s in jt_item), f"Judgment tuple {i} has non-str elements"
    print(f"[15] All {len(all_tuples)} judgment tuples have exactly 8 string elements ✓")

    t_end = time.time_ns()
    elapsed_ms = (t_end - t_start) / 1_000_000
    print(f"\n=== s01 smoke test PASSED in {elapsed_ms:.2f} ms ===")
