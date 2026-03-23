"""
Implementation evidence collection and validation for the doctrine_completion package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It provides utilities for collecting, validating, aggregating, and estimating
confidence in implementation evidence that grounds doctrine statements.  The
extended EvidenceKind enum here includes COPILOT_REVIEW as an additional
evidence kind beyond those defined in models.py.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models import (
    ImplementationEvidence,
    EvidenceKind as BaseEvidenceKind,
    DoctrineStatement,
    StatementStatus,
)

__all__ = [
    "EvidenceKind",
    "EvidenceChain",
    "EvidenceCollector",
    "EvidenceValidator",
    "EvidenceAggregator",
    "ArtifactResolver",
    "ConfidenceEstimator",
    "collect_evidence_for",
    "validate_evidence_chain",
]


# ---------------------------------------------------------------------------
# Extended EvidenceKind
# ---------------------------------------------------------------------------


class EvidenceKind(str, Enum):
    """Extended evidence kind taxonomy including COPILOT_REVIEW.

    This enum extends the base EvidenceKind from models.py by adding the
    COPILOT_REVIEW kind, which represents evidence collected or reviewed
    with the assistance of a copilot/AI system.

    CODE         — direct code artefact (source file, module, class).
    TEST         — automated test or test suite.
    RUNTIME      — runtime observation, trace or log.
    PROOF        — formal proof or mechanised verification certificate.
    ORACLE       — oracle-based or property-based test result.
    BENCHMARK    — performance measurement or benchmark result.
    HUMAN_REVIEW — signed-off human review artefact.
    COPILOT_REVIEW — evidence collected or reviewed by an AI copilot system.
    """

    CODE = "code"
    TEST = "test"
    RUNTIME = "runtime"
    PROOF = "proof"
    ORACLE = "oracle"
    BENCHMARK = "benchmark"
    HUMAN_REVIEW = "human_review"
    COPILOT_REVIEW = "copilot_review"


# ---------------------------------------------------------------------------
# EvidenceChain
# ---------------------------------------------------------------------------


@dataclass
class EvidenceChain:
    """An ordered chain of evidence items grounding a single statement.

    EvidenceChain represents the sequence of evidence items collected to
    ground a doctrine statement.  The chain concept encodes the idea that
    evidence may build on prior evidence (e.g., a test chain: unit test ->
    integration test -> runtime trace).

    Attributes:
        chain_id: Unique identifier (uuid4).
        statement_id: ID of the statement this chain grounds.
        items: Ordered list of ImplementationEvidence in the chain.
        created_at: Unix timestamp of chain creation.
        chain_type: Descriptor string (e.g., "test_chain", "proof_chain").
    """

    chain_id: str
    statement_id: str
    items: list[ImplementationEvidence]
    created_at: float
    chain_type: str

    @classmethod
    def create(
        cls,
        statement_id: str,
        chain_type: str = "generic",
        items: Optional[list[ImplementationEvidence]] = None,
    ) -> EvidenceChain:
        """Factory method with auto-generated ID and current timestamp.

        Args:
            statement_id: ID of the grounded statement.
            chain_type: Descriptive type string for the chain.
            items: Initial list of evidence items (default empty).

        Returns:
            A new EvidenceChain instance.
        """
        return cls(
            chain_id=str(uuid.uuid4()),
            statement_id=statement_id,
            items=list(items or []),
            created_at=time.time(),
            chain_type=chain_type,
        )

    def length(self) -> int:
        """Return the number of evidence items in this chain.

        Returns:
            Integer count of items.
        """
        return len(self.items)

    def total_confidence(self) -> float:
        """Compute the combined confidence for the whole chain.

        Uses the geometric mean of individual confidence values so that a
        single low-confidence item degrades the overall score.

        Returns:
            Geometric mean confidence in [0.0, 1.0], or 0.0 if chain is empty.
        """
        if not self.items:
            return 0.0
        log_sum = sum(math.log(max(item.confidence, 1e-9)) for item in self.items)
        return math.exp(log_sum / len(self.items))

    def is_complete(self, required_kinds: list[BaseEvidenceKind]) -> bool:
        """Return True if every required kind is covered by at least one item.

        Args:
            required_kinds: List of BaseEvidenceKind values that must be present.

        Returns:
            True when the chain covers all required kinds.
        """
        present_kinds: set[str] = {item.evidence_kind.value for item in self.items}
        return all(k.value in present_kinds for k in required_kinds)

    def add_item(self, item: ImplementationEvidence) -> None:
        """Append an evidence item to this chain.

        Only adds the item if it belongs to the same statement.

        Args:
            item: ImplementationEvidence to add.

        Raises:
            ValueError: If the item's statement_id does not match the chain's.
        """
        if item.statement_id != self.statement_id:
            raise ValueError(
                f"Evidence item statement_id '{item.statement_id}' does not match "
                f"chain statement_id '{self.statement_id}'"
            )
        self.items.append(item)

    def get_by_kind(self, kind: BaseEvidenceKind) -> list[ImplementationEvidence]:
        """Return all items in this chain with the given evidence kind.

        Args:
            kind: The BaseEvidenceKind to filter by.

        Returns:
            List of matching ImplementationEvidence items.
        """
        return [item for item in self.items if item.evidence_kind.value == kind.value]

    def to_json(self) -> str:
        """Serialise this chain to a JSON string.

        Returns:
            JSON-encoded string of chain fields and all items.
        """
        data = {
            "chain_id": self.chain_id,
            "statement_id": self.statement_id,
            "chain_type": self.chain_type,
            "created_at": self.created_at,
            "items": [json.loads(item.to_json()) for item in self.items],
        }
        return json.dumps(data, indent=2)

    def summarize(self) -> str:
        """Return a human-readable one-line summary of this chain.

        Returns:
            Concise summary string.
        """
        confidence = self.total_confidence()
        return (
            f"[CHAIN {self.chain_id[:8]}] stmt={self.statement_id[:8]} "
            f"type={self.chain_type} length={self.length()} "
            f"confidence={confidence:.3f}"
        )


# ---------------------------------------------------------------------------
# EvidenceCollector
# ---------------------------------------------------------------------------


class EvidenceCollector:
    """Collects implementation evidence from various sources.

    EvidenceCollector is responsible for gathering ImplementationEvidence
    items for given statement IDs.  It maintains a list of registered
    source identifiers (e.g., file paths, archive keys) and provides
    methods to produce evidence lists for individual or bulk queries.

    Example usage::

        collector = EvidenceCollector(sources=["src/", "tests/"])
        evidences = collector.collect_for_statement("stmt-123", ["src/foo.py"])
    """

    def __init__(self, sources: Optional[list[str]] = None) -> None:
        """Initialise the collector with an optional list of sources.

        Args:
            sources: List of source identifiers (paths, URLs, keys).
        """
        self._sources: list[str] = list(sources or [])
        self._collector_id: str = str(uuid.uuid4())
        self._created_at: float = time.time()

    def collect_for_statement(
        self, statement_id: str, artifact_refs: list[str]
    ) -> list[ImplementationEvidence]:
        """Collect evidence for a single statement from given artefact refs.

        For each artefact reference, heuristically determines the evidence
        kind based on the ref string (e.g., refs ending in .py -> CODE,
        test_ prefix -> TEST, .log -> RUNTIME).

        Args:
            statement_id: The statement to collect evidence for.
            artifact_refs: List of artefact reference strings.

        Returns:
            List of ImplementationEvidence items constructed from the refs.
        """
        evidences: list[ImplementationEvidence] = []
        for ref in artifact_refs:
            kind = self._infer_kind(ref)
            confidence = self._estimate_initial_confidence(ref, kind)
            depth = self._estimate_depth(ref, kind)
            ev = ImplementationEvidence.create(
                statement_id=statement_id,
                evidence_kind=kind,
                artifact_ref=ref,
                confidence=confidence,
                grounding_depth=depth,
                author=f"collector:{self._collector_id[:8]}",
                copilot_assisted=False,
            )
            evidences.append(ev)
        return evidences

    def collect_from_archive(
        self, statement_id: str, archive_data: dict[str, Any]
    ) -> list[ImplementationEvidence]:
        """Collect evidence from a structured archive dictionary.

        The archive_data is expected to be a dict mapping artefact refs
        to metadata dicts.  Each entry is converted to an evidence item.

        Args:
            statement_id: The statement to collect evidence for.
            archive_data: Dictionary of artefact ref -> metadata.

        Returns:
            List of ImplementationEvidence items.
        """
        evidences: list[ImplementationEvidence] = []
        for ref, meta in archive_data.items():
            kind_str = meta.get("kind", "code")
            try:
                kind = BaseEvidenceKind(kind_str)
            except ValueError:
                kind = BaseEvidenceKind.CODE
            confidence = float(meta.get("confidence", 0.7))
            depth = int(meta.get("grounding_depth", 1))
            author = meta.get("author", "archive")
            copilot_assisted = bool(meta.get("copilot_assisted", False))
            ev = ImplementationEvidence.create(
                statement_id=statement_id,
                evidence_kind=kind,
                artifact_ref=ref,
                confidence=confidence,
                grounding_depth=depth,
                author=author,
                copilot_assisted=copilot_assisted,
                metadata=dict(meta),
            )
            evidences.append(ev)
        return evidences

    def bulk_collect(
        self,
        statement_ids: list[str],
        archive_data: dict[str, Any],
    ) -> dict[str, list[ImplementationEvidence]]:
        """Collect evidence for multiple statements from a shared archive.

        The archive_data is expected to have a nested structure:
        ``{statement_id: {artifact_ref: metadata}}``.

        Args:
            statement_ids: List of statement IDs to collect for.
            archive_data: Nested archive dictionary.

        Returns:
            Dictionary mapping statement_id to list of evidence items.
        """
        result: dict[str, list[ImplementationEvidence]] = {}
        for sid in statement_ids:
            stmt_data = archive_data.get(sid, {})
            result[sid] = self.collect_from_archive(sid, stmt_data)
        return result

    def add_source(self, source: str) -> None:
        """Add a new source to this collector's source list.

        Args:
            source: Source identifier string to add.
        """
        if source not in self._sources:
            self._sources.append(source)

    def get_sources(self) -> list[str]:
        """Return a copy of the current source list.

        Returns:
            List of source identifier strings.
        """
        return list(self._sources)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _infer_kind(self, ref: str) -> BaseEvidenceKind:
        """Heuristically infer the evidence kind from an artefact reference.

        Applies simple suffix and prefix rules:
        - .py, .java, .ts, .go, .rs -> CODE
        - test_, _test, Test, .spec. -> TEST
        - .log, trace_, runtime_ -> RUNTIME
        - .proof, .lean, .coq -> PROOF
        - benchmark_, .bench -> BENCHMARK
        - review_ -> HUMAN_REVIEW
        - Default -> CODE

        Args:
            ref: Artefact reference string.

        Returns:
            Inferred BaseEvidenceKind.
        """
        lower = ref.lower()
        if any(lower.endswith(ext) for ext in [".py", ".java", ".ts", ".go", ".rs", ".c", ".cpp"]):
            if "test" in lower or "spec" in lower:
                return BaseEvidenceKind.TEST
            return BaseEvidenceKind.CODE
        if any(s in lower for s in ["test_", "_test", "test.", ".spec.", "_spec"]):
            return BaseEvidenceKind.TEST
        if any(s in lower for s in [".log", "trace_", "runtime_", "execution_"]):
            return BaseEvidenceKind.RUNTIME
        if any(lower.endswith(ext) for ext in [".proof", ".lean", ".coq", ".v", ".agda"]):
            return BaseEvidenceKind.PROOF
        if any(s in lower for s in ["benchmark", ".bench", "perf_"]):
            return BaseEvidenceKind.BENCHMARK
        if any(s in lower for s in ["review_", "_review", "audit_"]):
            return BaseEvidenceKind.HUMAN_REVIEW
        return BaseEvidenceKind.CODE

    def _estimate_initial_confidence(self, ref: str, kind: BaseEvidenceKind) -> float:
        """Estimate an initial confidence score based on kind and ref quality.

        Formal proofs get 0.95; tests 0.80; human review 0.85; others 0.70.

        Args:
            ref: Artefact reference string.
            kind: Inferred or provided evidence kind.

        Returns:
            Initial confidence float in [0.0, 1.0].
        """
        base_scores = {
            BaseEvidenceKind.PROOF: 0.95,
            BaseEvidenceKind.TEST: 0.80,
            BaseEvidenceKind.HUMAN_REVIEW: 0.85,
            BaseEvidenceKind.ORACLE: 0.88,
            BaseEvidenceKind.BENCHMARK: 0.75,
            BaseEvidenceKind.RUNTIME: 0.72,
            BaseEvidenceKind.CODE: 0.70,
        }
        return base_scores.get(kind, 0.70)

    def _estimate_depth(self, ref: str, kind: BaseEvidenceKind) -> int:
        """Estimate grounding depth from the reference and kind.

        Proof artefacts get depth 3; tests 2; others 1.

        Args:
            ref: Artefact reference string.
            kind: Evidence kind.

        Returns:
            Integer grounding depth >= 1.
        """
        if kind == BaseEvidenceKind.PROOF:
            return 3
        if kind in (BaseEvidenceKind.TEST, BaseEvidenceKind.ORACLE):
            return 2
        return 1


# ---------------------------------------------------------------------------
# EvidenceValidator
# ---------------------------------------------------------------------------


class EvidenceValidator:
    """Validates individual evidence items and chains.

    EvidenceValidator enforces configurable quality thresholds on evidence.
    It can validate single items, full chains, or batches.

    Attributes (public):
        min_confidence: Minimum acceptable confidence score.
        min_depth: Minimum acceptable grounding depth.
    """

    def __init__(
        self, min_confidence: float = 0.6, min_depth: int = 1
    ) -> None:
        """Initialise the validator with threshold parameters.

        Args:
            min_confidence: Minimum acceptable confidence (default 0.6).
            min_depth: Minimum acceptable grounding depth (default 1).
        """
        self.min_confidence = min_confidence
        self.min_depth = min_depth
        self._validator_id: str = str(uuid.uuid4())

    def validate(
        self, evidence: ImplementationEvidence
    ) -> tuple[bool, list[str]]:
        """Validate a single ImplementationEvidence item.

        Checks field validity and enforces threshold constraints.

        Args:
            evidence: The evidence item to validate.

        Returns:
            (is_valid, errors) tuple.
        """
        is_valid, errors = evidence.validate()
        if evidence.confidence < self.min_confidence:
            errors.append(
                f"confidence {evidence.confidence:.3f} below threshold {self.min_confidence}"
            )
            is_valid = False
        if evidence.grounding_depth < self.min_depth:
            errors.append(
                f"grounding_depth {evidence.grounding_depth} below threshold {self.min_depth}"
            )
            is_valid = False
        return (is_valid, errors)

    def validate_chain(self, chain: EvidenceChain) -> tuple[bool, list[str]]:
        """Validate an entire EvidenceChain.

        Validates each item individually; the chain is valid only when all
        items are valid and the chain has at least one item.

        Args:
            chain: The EvidenceChain to validate.

        Returns:
            (is_valid, errors) tuple for the whole chain.
        """
        all_errors: list[str] = []
        if not chain.items:
            all_errors.append(f"chain '{chain.chain_id}' has no evidence items")
        for item in chain.items:
            item_valid, item_errors = self.validate(item)
            if not item_valid:
                prefixed = [f"[{item.evidence_id[:8]}] {e}" for e in item_errors]
                all_errors.extend(prefixed)
        return (len(all_errors) == 0, all_errors)

    def batch_validate(
        self, evidences: list[ImplementationEvidence]
    ) -> dict[str, tuple[bool, list[str]]]:
        """Validate a batch of evidence items.

        Args:
            evidences: List of evidence items to validate.

        Returns:
            Dictionary mapping evidence_id to (is_valid, errors).
        """
        return {ev.evidence_id: self.validate(ev) for ev in evidences}

    def compute_validity_score(
        self, evidences: list[ImplementationEvidence]
    ) -> float:
        """Compute the fraction of items in a list that are valid.

        Args:
            evidences: List of evidence items.

        Returns:
            Float in [0.0, 1.0] representing the valid fraction.
        """
        if not evidences:
            return 0.0
        valid_count = sum(1 for ev in evidences if self.validate(ev)[0])
        return valid_count / len(evidences)


# ---------------------------------------------------------------------------
# EvidenceAggregator
# ---------------------------------------------------------------------------


class EvidenceAggregator:
    """Aggregates multiple evidence items into summary statistics.

    EvidenceAggregator provides methods to compute aggregate statistics
    over a collection of evidence items, such as best-by-kind selection,
    weighted confidence, and chain combination.
    """

    def __init__(self) -> None:
        """Initialise the aggregator with a unique ID.

        The aggregator is stateless beyond its identity.
        """
        self._aggregator_id: str = str(uuid.uuid4())

    def aggregate(
        self, evidences: list[ImplementationEvidence]
    ) -> dict[str, Any]:
        """Compute aggregate statistics over a list of evidence items.

        Returns counts, mean/max confidence, kind breakdown, and other
        summary fields.

        Args:
            evidences: List of evidence items to aggregate.

        Returns:
            Dictionary of aggregate statistics.
        """
        if not evidences:
            return {
                "count": 0,
                "mean_confidence": 0.0,
                "max_confidence": 0.0,
                "min_confidence": 0.0,
                "kinds": [],
                "copilot_assisted_count": 0,
                "mean_depth": 0.0,
            }
        confidences = [ev.confidence for ev in evidences]
        depths = [ev.grounding_depth for ev in evidences]
        kinds = list({ev.evidence_kind.value for ev in evidences})
        copilot_count = sum(1 for ev in evidences if ev.copilot_assisted)
        return {
            "count": len(evidences),
            "mean_confidence": sum(confidences) / len(confidences),
            "max_confidence": max(confidences),
            "min_confidence": min(confidences),
            "std_confidence": self._std(confidences),
            "kinds": sorted(kinds),
            "kind_count": len(kinds),
            "copilot_assisted_count": copilot_count,
            "mean_depth": sum(depths) / len(depths),
            "max_depth": max(depths),
        }

    def best_by_kind(
        self, evidences: list[ImplementationEvidence]
    ) -> dict[BaseEvidenceKind, ImplementationEvidence]:
        """Return the highest-confidence evidence item for each kind.

        Args:
            evidences: List of evidence items.

        Returns:
            Dictionary mapping BaseEvidenceKind to the best item.
        """
        best: dict[BaseEvidenceKind, ImplementationEvidence] = {}
        for ev in evidences:
            kind = ev.evidence_kind
            if kind not in best or ev.confidence > best[kind].confidence:
                best[kind] = ev
        return best

    def weighted_confidence(
        self, evidences: list[ImplementationEvidence]
    ) -> float:
        """Compute a depth-weighted average confidence score.

        Each item's contribution is weighted by its grounding_depth.

        Args:
            evidences: List of evidence items.

        Returns:
            Weighted average confidence in [0.0, 1.0].
        """
        if not evidences:
            return 0.0
        total_weight = sum(ev.grounding_depth for ev in evidences)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(ev.confidence * ev.grounding_depth for ev in evidences)
        return weighted_sum / total_weight

    def combine_chains(
        self, chains: list[EvidenceChain]
    ) -> EvidenceChain:
        """Combine multiple EvidenceChains into a single merged chain.

        The combined chain collects all items from all input chains.
        Uses the statement_id from the first chain.

        Args:
            chains: List of EvidenceChain objects to merge.

        Returns:
            A new EvidenceChain containing all items.

        Raises:
            ValueError: If the chains list is empty.
        """
        if not chains:
            raise ValueError("Cannot combine an empty list of chains")
        combined_items: list[ImplementationEvidence] = []
        statement_id = chains[0].statement_id
        for chain in chains:
            combined_items.extend(chain.items)
        return EvidenceChain(
            chain_id=str(uuid.uuid4()),
            statement_id=statement_id,
            items=combined_items,
            created_at=time.time(),
            chain_type="combined",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _std(self, values: list[float]) -> float:
        """Compute the sample standard deviation of a list.

        Args:
            values: List of numeric values.

        Returns:
            Sample standard deviation, or 0.0 if fewer than 2 values.
        """
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance)


# ---------------------------------------------------------------------------
# ArtifactResolver
# ---------------------------------------------------------------------------


class ArtifactResolver:
    """Resolves artefact references to structured metadata dictionaries.

    ArtifactResolver maintains a registry of known artefact refs and
    provides methods to resolve individual refs or batches.  Unknown
    refs are handled gracefully with a stub resolution.

    Attributes:
        base_path: Base filesystem path for relative artefact refs.
    """

    def __init__(self, base_path: str = ".") -> None:
        """Initialise the resolver with a base path.

        Args:
            base_path: Base directory for relative artefact references.
        """
        self.base_path = base_path
        self._registry: dict[str, dict[str, Any]] = {}
        self._resolver_id: str = str(uuid.uuid4())

    def resolve(self, artifact_ref: str) -> dict[str, Any]:
        """Resolve a single artefact reference to its metadata.

        If the ref is in the internal registry, returns the registered
        metadata.  Otherwise, constructs a stub resolution from the ref.

        Args:
            artifact_ref: The artefact reference to resolve.

        Returns:
            Dictionary with 'ref', 'resolved', 'path', and 'metadata' keys.
        """
        if artifact_ref in self._registry:
            return {
                "ref": artifact_ref,
                "resolved": True,
                "metadata": self._registry[artifact_ref],
                "path": self._registry[artifact_ref].get("path", artifact_ref),
            }
        # Stub resolution for unknown refs
        return {
            "ref": artifact_ref,
            "resolved": False,
            "metadata": {},
            "path": f"{self.base_path}/{artifact_ref}",
            "warning": f"Artefact '{artifact_ref}' not in resolver registry",
        }

    def resolve_batch(
        self, refs: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Resolve a list of artefact references in bulk.

        Args:
            refs: List of artefact reference strings.

        Returns:
            Dictionary mapping each ref to its resolution result.
        """
        return {ref: self.resolve(ref) for ref in refs}

    def register_artifact(self, ref: str, metadata: dict[str, Any]) -> None:
        """Register an artefact with its metadata for future resolution.

        Args:
            ref: Artefact reference string.
            metadata: Metadata dictionary to associate with the ref.
        """
        self._registry[ref] = dict(metadata)

    def is_resolvable(self, ref: str) -> bool:
        """Return True if the ref has been registered with this resolver.

        Args:
            ref: Artefact reference string.

        Returns:
            True if the ref is in the internal registry.
        """
        return ref in self._registry


# ---------------------------------------------------------------------------
# ConfidenceEstimator
# ---------------------------------------------------------------------------


class ConfidenceEstimator:
    """Estimates and adjusts confidence scores for implementation evidence.

    ConfidenceEstimator applies heuristics based on evidence kind, grounding
    depth, and recency to produce confidence estimates.  It can also compute
    confidence intervals over a collection of evidence items.
    """

    # Base confidence per evidence kind
    _KIND_BASE: dict[str, float] = {
        "proof": 0.95,
        "oracle": 0.88,
        "human_review": 0.85,
        "copilot_review": 0.83,
        "test": 0.80,
        "benchmark": 0.75,
        "runtime": 0.72,
        "code": 0.70,
    }

    def __init__(self) -> None:
        """Initialise the estimator with a unique ID.

        No configurable thresholds; uses class-level _KIND_BASE constants.
        """
        self._estimator_id: str = str(uuid.uuid4())

    def estimate(self, evidence: ImplementationEvidence) -> float:
        """Estimate confidence for a single evidence item.

        Combines the kind-based base score with a depth bonus and caps at 1.0.
        Depth bonus = 0.02 * (depth - 1), capped so total does not exceed 1.0.

        Args:
            evidence: The evidence item to estimate confidence for.

        Returns:
            Estimated confidence in [0.0, 1.0].
        """
        base = self._KIND_BASE.get(evidence.evidence_kind.value, 0.70)
        depth_bonus = 0.02 * max(0, evidence.grounding_depth - 1)
        return min(1.0, base + depth_bonus)

    def estimate_for_statement(
        self,
        statement_id: str,
        evidences: list[ImplementationEvidence],
    ) -> float:
        """Estimate the aggregate confidence for a statement's evidence set.

        Filters to evidence for the given statement_id and returns the
        weighted confidence (depth-weighted average).

        Args:
            statement_id: The statement to estimate confidence for.
            evidences: List of all evidence items (will be filtered).

        Returns:
            Estimated aggregate confidence in [0.0, 1.0].
        """
        relevant = [ev for ev in evidences if ev.statement_id == statement_id]
        if not relevant:
            return 0.0
        total_weight = sum(ev.grounding_depth for ev in relevant)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(
            self.estimate(ev) * ev.grounding_depth for ev in relevant
        )
        return weighted_sum / total_weight

    def confidence_interval(
        self, evidences: list[ImplementationEvidence]
    ) -> tuple[float, float]:
        """Compute a simple confidence interval over a list of evidence items.

        Uses mean ± standard_deviation as the interval bounds.  The bounds
        are clamped to [0.0, 1.0].

        Args:
            evidences: List of evidence items.

        Returns:
            (lower_bound, upper_bound) tuple.
        """
        if not evidences:
            return (0.0, 0.0)
        scores = [self.estimate(ev) for ev in evidences]
        mean = sum(scores) / len(scores)
        if len(scores) < 2:
            return (max(0.0, mean - 0.1), min(1.0, mean + 0.1))
        variance = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
        std = math.sqrt(variance)
        return (max(0.0, mean - std), min(1.0, mean + std))

    def adjust_for_recency(
        self,
        evidence: ImplementationEvidence,
        decay_factor: float = 0.95,
    ) -> float:
        """Adjust the evidence confidence for age-based decay.

        Older evidence decays by decay_factor per day since collection.
        Evidence less than a day old is not decayed.

        Args:
            evidence: The evidence item to adjust.
            decay_factor: Per-day decay factor (default 0.95).

        Returns:
            Decayed confidence in [0.0, 1.0].
        """
        age_seconds = time.time() - evidence.timestamp
        age_days = age_seconds / 86400.0
        if age_days < 1.0:
            return self.estimate(evidence)
        base_confidence = self.estimate(evidence)
        decay = decay_factor ** age_days
        return max(0.0, base_confidence * decay)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def collect_evidence_for(
    statement_id: str,
    sources: list[str],
) -> list[ImplementationEvidence]:
    """Convenience function to collect evidence for a statement from sources.

    Creates an EvidenceCollector with the given sources and collects
    evidence for the specified statement ID.

    Args:
        statement_id: The doctrine statement to collect evidence for.
        sources: List of artefact reference strings.

    Returns:
        List of ImplementationEvidence items.
    """
    collector = EvidenceCollector(sources=sources)
    return collector.collect_for_statement(statement_id, sources)


def validate_evidence_chain(
    chain: EvidenceChain,
) -> tuple[bool, list[str]]:
    """Convenience function to validate an EvidenceChain with default thresholds.

    Uses an EvidenceValidator with default min_confidence=0.6 and
    min_depth=1.

    Args:
        chain: The EvidenceChain to validate.

    Returns:
        (is_valid, errors) tuple.
    """
    validator = EvidenceValidator()
    return validator.validate_chain(chain)
