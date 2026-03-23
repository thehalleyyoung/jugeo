"""Core algorithms for the JuGeo unstable_protocols package (Ch22).

Provides analysis, stability checking, delegation tracking, and proxy
validation algorithms used across the package.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §1  :class:`ProtocolAnalyzer`   – analysis and comparison of protocol sections
* §4  :class:`StabilityChecker`   – systematic stability condition checking
* §2  :class:`DelegationTracker`  – delegation graph tracking and cycle detection
* §2  :class:`ProxyValidator`     – proxy compliance validation with audit log

Algorithmic notes
-----------------
* **Drift score** uses the Jaccard dissimilarity between declared and observed
  method sets: ``1 - |D ∩ O| / |D ∪ O|``.
* **Stability ranking** combines Jaccard drift with normalised verification lag
  using a weighted sum (60 % drift, 40 % lag).
* **Cycle detection** in the delegation graph uses iterative depth-first search
  with a colouring scheme (white/grey/black) to identify back-edges.
* **Rolling stability** is a simple windowed moving average over the last *N*
  check results stored in :attr:`StabilityChecker.check_log`.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Local model imports
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection,
        StabilityLevel,
        ProxyRecord,
        ProxyRestriction,
        DelegationChain,
        DelegationKind,
        UnstableInterface,
        StabilityMonitor,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection:  # type: ignore[no-redef]
        pass
    class StabilityLevel:  # type: ignore[no-redef]
        pass
    class ProxyRecord:  # type: ignore[no-redef]
        pass
    class ProxyRestriction:  # type: ignore[no-redef]
        pass
    class DelegationChain:  # type: ignore[no-redef]
        pass
    class DelegationKind:  # type: ignore[no-redef]
        pass
    class UnstableInterface:  # type: ignore[no-redef]
        pass
    class StabilityMonitor:  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# s01/s02/s03 imports
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.unstable_protocols.protocol_sections import (
        ProtocolSectionManager,
        StalenessDetector,
    )
except ImportError:  # pragma: no cover
    class ProtocolSectionManager:  # type: ignore[no-redef]
        pass
    class StalenessDetector:  # type: ignore[no-redef]
        pass

try:
    from jugeo.python_runtime.unstable_protocols.proxy_delegation import (
        DelegationMorphism,
        DelegationChainBuilder,
    )
except ImportError:  # pragma: no cover
    class DelegationMorphism:  # type: ignore[no-redef]
        pass
    class DelegationChainBuilder:  # type: ignore[no-redef]
        pass

try:
    from jugeo.python_runtime.unstable_protocols.unstable_surfaces import (
        SurfaceTracker,
    )
except ImportError:  # pragma: no cover
    class SurfaceTracker:  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# Cross-package stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:  # pragma: no cover
    class SupportRegion:  # type: ignore[no-redef]
        pass
    class SupportSet:  # type: ignore[no-redef]
        pass
    class SupportTracker:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustTier
except ImportError:  # pragma: no cover
    class LocalJudgment:  # type: ignore[no-redef]
        pass
    class JudgmentStatus:  # type: ignore[no-redef]
        pass
    class TrustTier:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:  # pragma: no cover
    class EvidenceChannel:  # type: ignore[no-redef]
        pass
    class EvidenceRecord:  # type: ignore[no-redef]
        pass
    class ChannelRouter:  # type: ignore[no-redef]
        pass

try:
    from jugeo.orchestration.fleet import Fleet, FleetBid, FleetMember
except ImportError:  # pragma: no cover
    class Fleet:  # type: ignore[no-redef]
        pass
    class FleetBid:  # type: ignore[no-redef]
        pass
    class FleetMember:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jaccard_drift(declared: set[str], observed: set[str]) -> float:
    """Compute Jaccard dissimilarity between declared and observed method sets."""
    union = declared | observed
    if not union:
        return 0.0
    return 1.0 - len(declared & observed) / len(union)


# ---------------------------------------------------------------------------
# ProtocolAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class ProtocolAnalyzer:
    """Analyses protocol sections for drift, anomalies, and cross-section comparisons.

    The analyser caches results keyed by ``(section_id, last_verified)`` so that
    repeated calls for an unchanged section are free.  Cache entries expire after
    :attr:`cache_ttl` seconds.

    Parameters
    ----------
    analysis_cache:
        Mapping from cache key to analysis result dict.
    cache_ttl:
        Seconds before a cached analysis is considered stale.
    """

    analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_ttl: float = 300.0

    def _cache_key(self, section: ProtocolSection) -> str:
        """Compute a stable cache key for a section."""
        payload = f"{section.section_id}:{section.last_verified}"
        return hashlib.md5(payload.encode()).hexdigest()

    def _is_cache_valid(self, key: str) -> bool:
        """Return True if a cached entry exists and has not expired."""
        entry = self.analysis_cache.get(key)
        if entry is None:
            return False
        return time.time() - entry.get("_cached_at", 0.0) < self.cache_ttl

    def analyze_section(self, section: ProtocolSection) -> dict[str, Any]:
        """Return a comprehensive analysis of a single protocol section.

        The analysis includes drift score, anomaly list, missing/excess methods,
        verification lag, and stability level metadata.  Results are cached.

        Parameters
        ----------
        section:
            The section to analyse.

        Returns
        -------
        dict[str, Any]
            Keys: ``section_id``, ``coordinate``, ``drift_score``,
            ``stability_level``, ``missing_methods``, ``excess_methods``,
            ``verification_lag``, ``age_seconds``, ``anomalies``, ``_cached_at``.
        """
        key = self._cache_key(section)
        if self._is_cache_valid(key):
            return self.analysis_cache[key]

        drift = self.compute_drift_score(section)
        anomalies = self.detect_anomalies(section)
        result: dict[str, Any] = {
            "section_id": section.section_id,
            "coordinate": section.coordinate,
            "drift_score": drift,
            "stability_level": section.stability_level.value,
            "severity_score": section.stability_level.severity_score(),
            "declared_count": len(section.declared_methods),
            "observed_count": len(section.observed_methods),
            "missing_methods": sorted(section.missing_methods()),
            "excess_methods": sorted(section.excess_methods()),
            "supported_methods": sorted(section.supported_methods()),
            "verification_lag": section.verification_lag(),
            "age_seconds": section.age_seconds(),
            "anomalies": anomalies,
            "_cached_at": time.time(),
        }
        self.analysis_cache[key] = result
        return result

    def compute_drift_score(self, section: ProtocolSection) -> float:
        """Compute the Jaccard-based drift score for a section.

        Parameters
        ----------
        section:
            The section to score.
        """
        return _jaccard_drift(
            set(section.declared_methods), set(section.observed_methods)
        )

    def detect_anomalies(self, section: ProtocolSection) -> list[str]:
        """Detect anomalies in a protocol section.

        Anomalies include: collapsed state, high drift, unverified for too long,
        empty declared set, and excessive excess methods.

        Parameters
        ----------
        section:
            The section to inspect.
        """
        anomalies: list[str] = []

        if section.stability_level == StabilityLevel.COLLAPSED:
            anomalies.append("Section is in COLLAPSED state")

        drift = self.compute_drift_score(section)
        if drift > 0.5:
            anomalies.append(f"High drift score {drift:.3f} (>0.5)")

        lag = section.verification_lag()
        if lag > 600:
            anomalies.append(f"Verification lag {lag:.0f}s exceeds 600s")

        if not section.declared_methods:
            anomalies.append("Section has no declared methods")

        excess = section.excess_methods()
        if len(excess) > len(section.declared_methods) * 0.5 and section.declared_methods:
            anomalies.append(
                f"Excess methods ({len(excess)}) exceed 50% of declared methods"
            )

        if section.stability_level.severity_score() >= 0.75:
            anomalies.append(
                f"Severity score {section.stability_level.severity_score():.2f} indicates "
                f"critical instability ({section.stability_level.value})"
            )

        return anomalies

    def compare_sections(
        self, s1: ProtocolSection, s2: ProtocolSection
    ) -> dict[str, Any]:
        """Compare two protocol sections and report their differences.

        Parameters
        ----------
        s1, s2:
            The sections to compare.

        Returns
        -------
        dict[str, Any]
            Keys: ``section_ids``, ``drift_delta``, ``stability_delta``,
            ``methods_only_in_s1``, ``methods_only_in_s2``,
            ``common_declared``, ``common_observed``.
        """
        d1 = set(s1.declared_methods)
        d2 = set(s2.declared_methods)
        o1 = set(s1.observed_methods)
        o2 = set(s2.observed_methods)
        drift1 = self.compute_drift_score(s1)
        drift2 = self.compute_drift_score(s2)
        return {
            "section_ids": [s1.section_id, s2.section_id],
            "drift_delta": drift2 - drift1,
            "stability_delta": (
                s2.stability_level.severity_score() - s1.stability_level.severity_score()
            ),
            "methods_only_in_s1": sorted(d1 - d2),
            "methods_only_in_s2": sorted(d2 - d1),
            "common_declared": sorted(d1 & d2),
            "common_observed": sorted(o1 & o2),
            "declared_jaccard_similarity": 1.0 - _jaccard_drift(d1, d2),
            "observed_jaccard_similarity": 1.0 - _jaccard_drift(o1, o2),
        }

    def rank_by_stability(
        self, sections: list[ProtocolSection]
    ) -> list[tuple[ProtocolSection, float]]:
        """Rank sections from most to least stable using a composite score.

        The composite score is ``0.6 * drift + 0.4 * severity``, where
        drift is the Jaccard drift and severity is ``stability_level.severity_score()``.

        Parameters
        ----------
        sections:
            List of sections to rank.
        """
        ranked: list[tuple[ProtocolSection, float]] = []
        for s in sections:
            drift = self.compute_drift_score(s)
            severity = s.stability_level.severity_score()
            score = 0.6 * drift + 0.4 * severity
            ranked.append((s, score))
        ranked.sort(key=lambda t: t[1])
        return ranked

    def stability_histogram(
        self, sections: list[ProtocolSection]
    ) -> dict[str, int]:
        """Count sections at each stability level.

        Parameters
        ----------
        sections:
            List of sections to tally.

        Returns
        -------
        dict[str, int]
            Mapping from stability level value string to count.
        """
        counts: dict[str, int] = {level.value: 0 for level in StabilityLevel}
        for s in sections:
            counts[s.stability_level.value] += 1
        return counts

    def drift_over_time(
        self, section_history: list[ProtocolSection]
    ) -> list[float]:
        """Compute drift scores for an ordered sequence of section snapshots.

        Parameters
        ----------
        section_history:
            Ordered list of section instances (oldest first).

        Returns
        -------
        list[float]
            Drift score for each snapshot in order.
        """
        return [self.compute_drift_score(s) for s in section_history]

    def export_analysis(self, section_id: str) -> dict[str, Any] | None:
        """Return the cached analysis for a section by ID, or None if uncached.

        Parameters
        ----------
        section_id:
            The section to look up.
        """
        for entry in self.analysis_cache.values():
            if entry.get("section_id") == section_id:
                return dict(entry)
        return None


# ---------------------------------------------------------------------------
# StabilityChecker
# ---------------------------------------------------------------------------


@dataclass
class StabilityChecker:
    """Checks stability conditions across multiple protocol sections.

    The checker maintains a running log of individual check results and exposes
    rolling average stability, worst/best offender queries, and a set of
    best-practice recommendations derived from the current section states.

    Parameters
    ----------
    sections:
        Sections under observation, keyed by section_id.
    threshold:
        Drift threshold above which a section fails stability (0.0–1.0).
    check_log:
        Ordered list of individual check result records.
    """

    sections: dict[str, ProtocolSection] = field(default_factory=dict)
    threshold: float = 0.5
    check_log: list[dict[str, Any]] = field(default_factory=list)

    def check_all(self) -> dict[str, bool]:
        """Check all registered sections and return a pass/fail mapping.

        Returns
        -------
        dict[str, bool]
            Mapping from section_id to stability check result.
        """
        return {sid: self.check_single(s) for sid, s in self.sections.items()}

    def check_single(self, section: ProtocolSection) -> bool:
        """Check a single section for stability.

        A section fails when its drift score exceeds :attr:`threshold` *or*
        its stability level is RETRACTING or COLLAPSED.

        Parameters
        ----------
        section:
            The section to check.
        """
        drift = _jaccard_drift(
            set(section.declared_methods), set(section.observed_methods)
        )
        failed_drift = drift > self.threshold
        failed_level = section.stability_level in (
            StabilityLevel.RETRACTING,
            StabilityLevel.COLLAPSED,
        )
        passed = not failed_drift and not failed_level
        self.check_log.append(
            {
                "section_id": section.section_id,
                "passed": passed,
                "drift": drift,
                "stability_level": section.stability_level.value,
                "timestamp": time.time(),
            }
        )
        return passed

    def threshold_violations(self) -> list[ProtocolSection]:
        """Return all registered sections that currently fail the drift threshold."""
        return [
            s
            for s in self.sections.values()
            if _jaccard_drift(set(s.declared_methods), set(s.observed_methods))
            > self.threshold
        ]

    def rolling_stability(self, section_id: str, window: int = 10) -> float:
        """Compute the average pass-rate over the last ``window`` checks for a section.

        Parameters
        ----------
        section_id:
            The section to analyse.
        window:
            Number of recent checks to average over.

        Returns
        -------
        float
            Value in [0.0, 1.0]; 1.0 means all recent checks passed.
        """
        relevant = [
            e for e in self.check_log if e["section_id"] == section_id
        ][-window:]
        if not relevant:
            return 1.0
        return sum(1 for e in relevant if e["passed"]) / len(relevant)

    def stability_report(self) -> dict[str, Any]:
        """Return a structured summary of the current stability state.

        Returns
        -------
        dict[str, Any]
            Keys: ``total``, ``passed``, ``failed``, ``threshold``,
            ``mean_drift``, ``check_log_size``.
        """
        results = self.check_all()
        drifts = [
            _jaccard_drift(set(s.declared_methods), set(s.observed_methods))
            for s in self.sections.values()
        ]
        mean_drift = sum(drifts) / len(drifts) if drifts else 0.0
        passed_count = sum(1 for v in results.values() if v)
        return {
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "threshold": self.threshold,
            "mean_drift": mean_drift,
            "check_log_size": len(self.check_log),
        }

    def worst_offenders(self, top_n: int = 5) -> list[ProtocolSection]:
        """Return the ``top_n`` sections with the highest drift scores.

        Parameters
        ----------
        top_n:
            Number of results to return.
        """
        scored = [
            (
                s,
                _jaccard_drift(set(s.declared_methods), set(s.observed_methods)),
            )
            for s in self.sections.values()
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [s for s, _ in scored[:top_n]]

    def best_practices(self) -> list[str]:
        """Return a list of best-practice recommendations based on current section states.

        Returns
        -------
        list[str]
            Ordered list of recommendation strings.
        """
        recs: list[str] = []
        report = self.stability_report()
        if report.get("failed", 0) > 0:
            recs.append(
                f"Address {report['failed']} failing section(s); "
                "reduce drift below threshold {:.2f}.".format(self.threshold)
            )
        if report.get("mean_drift", 0.0) > 0.3:
            recs.append(
                "Mean drift {:.3f} is elevated; schedule re-verification passes.".format(
                    report["mean_drift"]
                )
            )
        collapsed = [
            s
            for s in self.sections.values()
            if s.stability_level == StabilityLevel.COLLAPSED
        ]
        if collapsed:
            recs.append(
                f"Purge or restart {len(collapsed)} COLLAPSED section(s) immediately."
            )
        if not recs:
            recs.append("All sections meet stability thresholds. No action required.")
        return recs

    def validate_invariants(self, section: ProtocolSection) -> list[str]:
        """Return a list of violated invariants for a single section.

        Invariants checked:
        * ``last_verified >= created_at``
        * ``stability_level`` is a valid :class:`StabilityLevel`
        * Drift score is finite
        * Section ID and coordinate are non-empty

        Parameters
        ----------
        section:
            The section to validate.
        """
        violations: list[str] = []
        if section.last_verified < section.created_at:
            violations.append("last_verified < created_at")
        if not section.section_id:
            violations.append("section_id is empty")
        if not section.coordinate:
            violations.append("coordinate is empty")
        drift = _jaccard_drift(
            set(section.declared_methods), set(section.observed_methods)
        )
        if not math.isfinite(drift):
            violations.append(f"drift score is not finite: {drift}")
        return violations


# ---------------------------------------------------------------------------
# DelegationTracker
# ---------------------------------------------------------------------------


@dataclass
class DelegationTracker:
    """Tracks delegation chains and validates morphism properties.

    Internally maintains a directed adjacency graph where each node is a
    section ID and each edge represents a delegation morphism.  Cycle detection
    uses iterative DFS with a colouring scheme.

    Parameters
    ----------
    chains:
        Mapping from chain_id to :class:`DelegationChain`.
    morphisms:
        Mapping from morphism_id to morphism data (dict with source/target/trust).
    graph:
        Directed adjacency: source_id → set of target IDs.
    """

    chains: dict[str, DelegationChain] = field(default_factory=dict)
    morphisms: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def track_chain(self, chain: DelegationChain) -> None:
        """Register a delegation chain and update the graph.

        Parameters
        ----------
        chain:
            The :class:`DelegationChain` to track.
        """
        self.chains[chain.chain_id] = chain
        links = chain.links
        for i in range(len(links) - 1):
            self.graph[links[i]].add(links[i + 1])

    def validate_morphism(
        self, source_id: str, target_id: str, trust_factor: float
    ) -> bool:
        """Return True when a proposed morphism is structurally valid.

        Conditions:
        * ``source_id ≠ target_id``
        * ``trust_factor ∈ [0.0, 1.0]``
        * Adding the edge ``source_id → target_id`` does not create a cycle.

        Parameters
        ----------
        source_id:
            Source section ID.
        target_id:
            Target section ID.
        trust_factor:
            Proposed trust scalar.
        """
        if source_id == target_id:
            return False
        if not (0.0 <= trust_factor <= 1.0):
            return False
        # Check for cycle with temporary edge
        temp_graph: dict[str, set[str]] = {
            k: set(v) for k, v in self.graph.items()
        }
        temp_graph.setdefault(source_id, set()).add(target_id)
        return not self._has_cycle(temp_graph)

    def detect_cycles(self) -> list[list[str]]:
        """Return all cycles in the current delegation graph.

        Uses iterative DFS with a node-colouring scheme (0=white, 1=grey, 2=black).

        Returns
        -------
        list[list[str]]
            Each inner list is a cycle as a sequence of node IDs.
        """
        colour: dict[str, int] = {}
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(node: str) -> None:
            colour[node] = 1
            path.append(node)
            for neighbour in self.graph.get(node, set()):
                if colour.get(neighbour, 0) == 1:
                    # Found a back-edge → cycle
                    cycle_start = path.index(neighbour)
                    cycles.append(path[cycle_start:])
                elif colour.get(neighbour, 0) == 0:
                    dfs(neighbour)
            path.pop()
            colour[node] = 2

        all_nodes = set(self.graph.keys())
        for v in self.graph.values():
            all_nodes.update(v)
        for node in all_nodes:
            if colour.get(node, 0) == 0:
                dfs(node)
        return cycles

    def chain_statistics(self, chain_id: str) -> dict[str, Any]:
        """Return statistics about a tracked delegation chain.

        Parameters
        ----------
        chain_id:
            The chain to analyse.

        Returns
        -------
        dict[str, Any]
            Keys: ``chain_id``, ``length``, ``head``, ``tail``, ``is_cyclic``,
            ``trust_ceiling``.
        """
        chain = self.chains.get(chain_id)
        if chain is None:
            return {"error": f"chain {chain_id!r} not found"}
        return {
            "chain_id": chain_id,
            "length": chain.chain_length(),
            "head": chain.head(),
            "tail": chain.tail(),
            "is_cyclic": chain.is_cyclic(),
            "trust_ceiling": chain.trust_ceiling,
            "delegation_kind": chain.delegation_kind.value,
        }

    def longest_chain(self) -> DelegationChain | None:
        """Return the longest currently tracked delegation chain, or None."""
        if not self.chains:
            return None
        return max(self.chains.values(), key=lambda c: c.chain_length())

    def delegation_graph(self) -> dict[str, list[str]]:
        """Return the delegation graph as a plain dict of lists."""
        return {k: sorted(v) for k, v in self.graph.items()}

    def prune_expired(self, before: float) -> int:
        """Remove chains created before ``before`` (Unix timestamp).

        Parameters
        ----------
        before:
            Chains created before this timestamp are removed.

        Returns
        -------
        int
            Number of chains removed.
        """
        expired = [
            cid for cid, c in self.chains.items() if c.created_at < before
        ]
        for cid in expired:
            c = self.chains.pop(cid)
            # remove edges from graph
            for i in range(len(c.links) - 1):
                self.graph.get(c.links[i], set()).discard(c.links[i + 1])
        return len(expired)

    def export_graph(self) -> dict[str, Any]:
        """Serialise the delegation graph and chain registry to a plain dictionary."""
        return {
            "graph": self.delegation_graph(),
            "chains": {cid: c.to_dict() for cid, c in self.chains.items()},
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _has_cycle(self, graph: dict[str, set[str]]) -> bool:
        """Return True if the given graph contains a directed cycle."""
        colour: dict[str, int] = {}

        def dfs(node: str) -> bool:
            colour[node] = 1
            for nb in graph.get(node, set()):
                if colour.get(nb, 0) == 1:
                    return True
                if colour.get(nb, 0) == 0 and dfs(nb):
                    return True
            colour[node] = 2
            return False

        all_nodes: set[str] = set(graph.keys())
        for v in graph.values():
            all_nodes.update(v)
        for n in all_nodes:
            if colour.get(n, 0) == 0 and dfs(n):
                return True
        return False


# ---------------------------------------------------------------------------
# ProxyValidator
# ---------------------------------------------------------------------------


@dataclass
class ProxyValidator:
    """Validates proxy records against their protocol sections.

    Provides compliance reports, expired-proxy detection, and an audit log of
    all validation decisions.

    Parameters
    ----------
    validation_results:
        Most recent validation result per proxy_id.
    audit_log:
        Ordered list of validation event records.
    """

    validation_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    _revoked: set[str] = field(default_factory=set, repr=False)

    def validate_proxy(self, proxy: ProxyRecord, section: ProtocolSection) -> bool:
        """Validate a proxy against its target section.

        Parameters
        ----------
        proxy:
            The proxy to validate.
        section:
            The target protocol section.

        Returns
        -------
        bool
            ``True`` when the proxy passes all checks.
        """
        if proxy.proxy_id in self._revoked:
            self._log(proxy.proxy_id, section.section_id, False, "proxy revoked")
            return False
        if proxy.is_expired():
            self._log(proxy.proxy_id, section.section_id, False, "proxy expired")
            return False
        if proxy.target_section_id != section.section_id:
            self._log(
                proxy.proxy_id, section.section_id, False, "section ID mismatch"
            )
            return False

        result = True
        reason = "passed"

        if proxy.restriction == ProxyRestriction.BLOCKED:
            result = False
            reason = "proxy is BLOCKED"
        elif section.stability_level in (StabilityLevel.RETRACTING, StabilityLevel.COLLAPSED):
            if proxy.restriction not in (ProxyRestriction.OPAQUE, ProxyRestriction.BLOCKED):
                result = False
                reason = f"section {section.stability_level.value} requires opaque proxy"

        self._log(proxy.proxy_id, section.section_id, result, reason)
        self.validation_results[proxy.proxy_id] = {
            "result": result,
            "reason": reason,
            "timestamp": time.time(),
        }
        return result

    def check_restrictions(self, proxy: ProxyRecord, requested_attr: str) -> bool:
        """Return True when ``requested_attr`` is accessible through the proxy.

        Parameters
        ----------
        proxy:
            The proxy to check.
        requested_attr:
            Attribute being requested.
        """
        return proxy.can_access(requested_attr)

    def access_audit(self, proxy_id: str) -> list[dict[str, Any]]:
        """Return all audit log entries for a specific proxy.

        Parameters
        ----------
        proxy_id:
            The proxy to filter by.
        """
        return [e for e in self.audit_log if e.get("proxy_id") == proxy_id]

    def expired_proxies(self, proxies: list[ProxyRecord]) -> list[ProxyRecord]:
        """Return only those proxies that have expired.

        Parameters
        ----------
        proxies:
            List to filter.
        """
        return [p for p in proxies if p.is_expired()]

    def compliance_report(
        self,
        proxies: list[ProxyRecord],
        sections: dict[str, ProtocolSection],
    ) -> dict[str, Any]:
        """Generate a compliance report for a set of proxies.

        Parameters
        ----------
        proxies:
            List of proxy records.
        sections:
            Mapping from section_id to section (for lookup).

        Returns
        -------
        dict[str, Any]
            Keys: ``total``, ``valid``, ``invalid``, ``expired``,
            ``by_proxy`` (per-proxy result dict).
        """
        by_proxy: dict[str, Any] = {}
        valid_count = 0
        expired_count = 0
        for proxy in proxies:
            section = sections.get(proxy.target_section_id)
            if proxy.is_expired():
                expired_count += 1
                by_proxy[proxy.proxy_id] = {"valid": False, "reason": "expired"}
            elif section is None:
                by_proxy[proxy.proxy_id] = {"valid": False, "reason": "section not found"}
            else:
                ok = self.validate_proxy(proxy, section)
                by_proxy[proxy.proxy_id] = {
                    "valid": ok,
                    "reason": self.validation_results.get(proxy.proxy_id, {}).get(
                        "reason", "unknown"
                    ),
                }
                if ok:
                    valid_count += 1

        return {
            "total": len(proxies),
            "valid": valid_count,
            "invalid": len(proxies) - valid_count,
            "expired": expired_count,
            "by_proxy": by_proxy,
        }

    def patch_proxy(self, proxy: ProxyRecord, new_expiry: float) -> ProxyRecord:
        """Return a new :class:`ProxyRecord` with an updated expiry timestamp.

        Parameters
        ----------
        proxy:
            The proxy to patch.
        new_expiry:
            New Unix timestamp for ``expires_at``.
        """
        from dataclasses import replace as dc_replace

        return dc_replace(proxy, expires_at=new_expiry)

    def revoke_proxy(self, proxy_id: str) -> bool:
        """Permanently revoke a proxy.

        Parameters
        ----------
        proxy_id:
            The proxy ID to revoke.

        Returns
        -------
        bool
            Always ``True`` (idempotent).
        """
        self._revoked.add(proxy_id)
        return True

    def audit_log_export(self) -> list[dict[str, Any]]:
        """Return a copy of the full audit log."""
        return list(self.audit_log)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log(
        self, proxy_id: str, section_id: str, result: bool, reason: str
    ) -> None:
        """Append an entry to the audit log."""
        self.audit_log.append(
            {
                "proxy_id": proxy_id,
                "section_id": section_id,
                "result": result,
                "reason": reason,
                "timestamp": time.time(),
            }
        )


# ---------------------------------------------------------------------------

__all__ = [
    "ProtocolAnalyzer",
    "StabilityChecker",
    "DelegationTracker",
    "ProxyValidator",
]

# copilot: algorithms.py – ProtocolAnalyzer, StabilityChecker, DelegationTracker, ProxyValidator with Jaccard drift and DFS cycle detection (Ch22)
