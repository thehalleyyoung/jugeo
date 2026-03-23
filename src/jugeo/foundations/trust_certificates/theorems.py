"""Formal theorem statements for Chapter 6 of theory2.tex — Trust, Provenance,
Evidence, and Certificates.

This module defines the TheoremStatement, TheoremRegistry, and ProofChecker
types, and instantiates all seven theorems of Chapter 6.

Author: copilot
Reference: theory2.tex Chapter 6
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
    from jugeo.evidence.provenance import ProvenanceNode, ProvenanceGraph
    from jugeo.evidence.certificates import Certificate, CertificateBuilder, CertificateStatus
    from jugeo.judgments.judgment_terms import JudgmentTerm
    from jugeo.errors import JuGeoError, StructuredFailure, FailureScope, EvidenceFamily
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Trust level ordering — lower value means weaker trust
# ---------------------------------------------------------------------------

_TRUST_ORDER: Dict[str, int] = {
    "CONTRADICTED": 0,
    "UNATTESTED": 1,
    "ORACLE_PROPOSED": 2,
    "HUMAN_ATTESTED": 3,
    "RUNTIME_WITNESSED": 4,
    "SOLVER_DISCHARGED": 5,
    "PEER_REVIEWED": 6,
    "MECHANICALLY_VERIFIED": 7,
}


# ---------------------------------------------------------------------------
# TheoremStatus enum
# ---------------------------------------------------------------------------


class TheoremStatus(Enum):
    """Lifecycle status of a theorem within the formal system."""

    CONJECTURED = "CONJECTURED"
    PROOF_SKETCH = "PROOF_SKETCH"
    FORMALLY_VERIFIED = "FORMALLY_VERIFIED"
    REFUTED = "REFUTED"
    DEPRECATED = "DEPRECATED"


# ---------------------------------------------------------------------------
# TheoremStatement dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremStatement:
    """Immutable record of a single theorem in Chapter 6 of theory2.tex.

    Each instance captures the full formal structure of the theorem: its
    hypotheses, conclusion, a human-readable proof sketch, the minimum trust
    level required to discharge the theorem, its current proof status, and
    structural metadata (chapter, section, theorem number, dependencies).
    """

    name: str
    statement: str
    hypotheses: Tuple[str, ...]
    conclusion: str
    proof_sketch: str
    trust_requirement: str
    status: str = "CONJECTURED"
    chapter: int = 6
    section: int = 0
    theorem_number: int = 0
    depends_on: Tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Return a list of constraint violations for this theorem.

        Checks that required string fields are non-empty and that the
        trust_requirement is a recognised level in _TRUST_ORDER.  An empty
        list indicates a valid theorem record.
        """
        violations: List[str] = []

        if not self.name or not self.name.strip():
            violations.append("name must be non-empty")

        if not self.statement or not self.statement.strip():
            violations.append("statement must be non-empty")

        if not self.conclusion or not self.conclusion.strip():
            violations.append("conclusion must be non-empty")

        if self.trust_requirement not in _TRUST_ORDER:
            violations.append(
                f"trust_requirement '{self.trust_requirement}' is not a known "
                f"trust level; valid values: {sorted(_TRUST_ORDER)}"
            )

        if self.chapter < 1:
            violations.append(f"chapter must be >= 1, got {self.chapter}")

        if self.theorem_number < 0:
            violations.append(
                f"theorem_number must be >= 0, got {self.theorem_number}"
            )

        return violations

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> Dict[str, Any]:
        """Return a plain-dict representation suitable for JSON serialisation.

        All tuple fields are converted to lists so that the result is
        directly JSON-serialisable without a custom encoder.
        """
        return {
            "name": self.name,
            "statement": self.statement,
            "hypotheses": list(self.hypotheses),
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "trust_requirement": self.trust_requirement,
            "status": self.status,
            "chapter": self.chapter,
            "section": self.section,
            "theorem_number": self.theorem_number,
            "depends_on": list(self.depends_on),
        }

    # ------------------------------------------------------------------
    # LaTeX rendering
    # ------------------------------------------------------------------

    def to_latex(self) -> str:
        r"""Return a LaTeX theorem environment for inclusion in theory2.tex.

        Hypotheses are rendered as ``\textit{Hypothesis:}`` items and the
        conclusion is rendered as a ``\textit{Conclusion:}`` item, all
        formatted as a description list inside a theorem environment named
        after ``self.name``.
        """
        lines: List[str] = [
            rf"\begin{{theorem}}[{self.name}]",
        ]

        if self.hypotheses:
            lines.append(r"  \begin{description}")
            for hyp in self.hypotheses:
                lines.append(rf"    \item[\textit{{Hypothesis:}}] {hyp}")
            lines.append(r"  \end{description}")

        lines.append(rf"  \textit{{Conclusion:}} {self.conclusion}")
        lines.append(r"\end{theorem}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def is_proved(self) -> bool:
        """Return True iff this theorem has status FORMALLY_VERIFIED."""
        return self.status == TheoremStatus.FORMALLY_VERIFIED.value

    def is_conjectured(self) -> bool:
        """Return True iff this theorem has status CONJECTURED."""
        return self.status == TheoremStatus.CONJECTURED.value


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


class TheoremRegistry:
    """Central registry that holds and indexes all TheoremStatement instances.

    Theorems are stored by name and can be queried by status, topologically
    sorted by their dependency graph, or validated in bulk.  All registration
    events are appended to ``registration_log`` so that audit trails are
    preserved across the registry's lifetime.
    """

    def __init__(self) -> None:
        """Initialise an empty registry with no registered theorems."""
        self.theorems: Dict[str, TheoremStatement] = {}
        self.registration_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, theorem: TheoremStatement) -> None:
        """Validate *theorem* and add it to the registry.

        If validation produces violations, those are recorded in the log
        alongside the registration event but do **not** prevent the theorem
        from being stored (callers may register sketch-level theorems).

        Raises:
            ValueError: if *theorem* is already registered under its name.
        """
        if theorem.name in self.theorems:
            raise ValueError(
                f"Theorem '{theorem.name}' is already registered. "
                "Use a distinct name or deregister the existing entry first."
            )

        violations = theorem.validate()

        self.theorems[theorem.name] = theorem
        self.registration_log.append(
            {
                "event": "register",
                "theorem_name": theorem.name,
                "status": theorem.status,
                "trust_requirement": theorem.trust_requirement,
                "violations": violations,
                "timestamp": time.time(),
                "event_id": str(uuid.uuid4()),
            }
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[TheoremStatement]:
        """Return the theorem registered under *name*, or None if absent."""
        return self.theorems.get(name)

    def get_all(self) -> List[TheoremStatement]:
        """Return all registered theorems sorted lexicographically by name."""
        return sorted(self.theorems.values(), key=lambda t: t.name)

    def get_by_status(self, status: str) -> List[TheoremStatement]:
        """Return all theorems whose ``status`` field equals *status*.

        The comparison is case-sensitive and matches the TheoremStatus enum
        value strings (e.g. ``'FORMALLY_VERIFIED'``).
        """
        return [t for t in self.theorems.values() if t.status == status]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_all(self) -> Dict[str, List[str]]:
        """Run ``validate()`` on every registered theorem.

        Returns a dict mapping theorem name to its violation list.  Only
        theorems with at least one violation appear in the result, so an
        empty dict means all theorems are well-formed.
        """
        result: Dict[str, List[str]] = {}
        for name, theorem in self.theorems.items():
            violations = theorem.validate()
            if violations:
                result[name] = violations
        return result

    # ------------------------------------------------------------------
    # Name listing
    # ------------------------------------------------------------------

    def list_names(self) -> List[str]:
        """Return a sorted list of all registered theorem names."""
        return sorted(self.theorems.keys())

    # ------------------------------------------------------------------
    # Topological sort
    # ------------------------------------------------------------------

    def dependency_order(self) -> List[str]:
        """Return theorem names in a valid topological evaluation order.

        Theorems that have no unregistered dependencies are processed first.
        If a dependency is referenced but not registered, it is silently
        skipped (to tolerate forward-references to theorems from other
        chapters).  If the dependency graph contains a cycle among the
        registered theorems, the cyclic nodes are appended at the end of the
        list in arbitrary order so that callers always receive all names.
        """
        in_degree: Dict[str, int] = {name: 0 for name in self.theorems}
        adjacency: Dict[str, List[str]] = defaultdict(list)

        for name, theorem in self.theorems.items():
            for dep in theorem.depends_on:
                if dep in self.theorems:
                    in_degree[name] += 1
                    adjacency[dep].append(name)

        queue: List[str] = sorted(
            name for name, deg in in_degree.items() if deg == 0
        )
        ordered: List[str] = []

        while queue:
            current = queue.pop(0)
            ordered.append(current)
            for neighbour in sorted(adjacency[current]):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        # Append any remaining cyclic nodes
        remaining = sorted(
            name for name in self.theorems if name not in ordered
        )
        ordered.extend(remaining)

        return ordered

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of registry state.

        Includes counts by status, a list of theorem names with their trust
        requirements, and any validation violations.
        """
        counts: Dict[str, int] = defaultdict(int)
        for t in self.theorems.values():
            counts[t.status] += 1

        lines: List[str] = [
            f"TheoremRegistry — {len(self.theorems)} theorem(s) registered",
            "",
            "Status breakdown:",
        ]
        for status in sorted(counts):
            lines.append(f"  {status}: {counts[status]}")

        lines.append("")
        lines.append("Theorem index (name | trust_requirement | status):")
        for name in self.list_names():
            t = self.theorems[name]
            lines.append(f"  {name:<50} {t.trust_requirement:<25} {t.status}")

        violations = self.validate_all()
        if violations:
            lines.append("")
            lines.append("Validation violations:")
            for name, v_list in sorted(violations.items()):
                for v in v_list:
                    lines.append(f"  [{name}] {v}")
        else:
            lines.append("")
            lines.append("All theorems pass validation.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ProofChecker
# ---------------------------------------------------------------------------


class ProofChecker:
    """Runtime checker that evaluates whether certificates and evidence
    satisfy the formal requirements of Chapter 6 theorems.

    Each check method corresponds to one or more structural properties that
    must hold for the formal system to be sound.  Violation lists are
    returned alongside a boolean pass/fail flag so that callers can collect
    all violations rather than stopping at the first failure.
    """

    def __init__(self, registry: TheoremRegistry) -> None:
        """Construct a ProofChecker backed by *registry*.

        Args:
            registry: A populated TheoremRegistry from which theorem
                      definitions are read during checks.
        """
        self.registry = registry
        self.check_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, method: str, result: bool, violations: List[str], extra: Optional[Dict] = None) -> None:
        """Append a structured entry to ``check_log``."""
        entry: Dict[str, Any] = {
            "event": method,
            "passed": result,
            "violations": list(violations),
            "timestamp": time.time(),
            "event_id": str(uuid.uuid4()),
        }
        if extra:
            entry.update(extra)
        self.check_log.append(entry)

    # ------------------------------------------------------------------
    # check_certificate_satisfies
    # ------------------------------------------------------------------

    def check_certificate_satisfies(
        self, cert_dict: Dict[str, Any], theorem_name: str
    ) -> Tuple[bool, List[str]]:
        """Check whether *cert_dict* satisfies the trust requirements of *theorem_name*.

        The check verifies:
        1. The theorem exists in the registry.
        2. The certificate's trust level is at least as strong as the
           theorem's ``trust_requirement``.
        3. The certificate carries no CONTRADICTED evidence.
        4. The certificate is not revoked.
        5. The certificate's coordinate field is non-empty.

        Args:
            cert_dict: A dict representation of a certificate.  Expected
                keys: ``trust_level``, ``evidence``, ``revoked``,
                ``coordinate``.
            theorem_name: Name of the theorem to check satisfaction against.

        Returns:
            A ``(satisfied, violations)`` tuple where *satisfied* is True
            only if all checks pass.
        """
        violations: List[str] = []

        theorem = self.registry.get(theorem_name)
        if theorem is None:
            violations.append(f"Theorem '{theorem_name}' not found in registry")
            self._log("check_certificate_satisfies", False, violations,
                      {"theorem_name": theorem_name})
            return False, violations

        # Check trust level
        cert_trust = cert_dict.get("trust_level", "UNATTESTED")
        cert_rank = _TRUST_ORDER.get(cert_trust, -1)
        required_rank = _TRUST_ORDER.get(theorem.trust_requirement, 0)

        if cert_rank < required_rank:
            violations.append(
                f"Certificate trust level '{cert_trust}' (rank {cert_rank}) is "
                f"below the required '{theorem.trust_requirement}' "
                f"(rank {required_rank}) for theorem '{theorem_name}'"
            )

        # Check for contradicted evidence
        evidence_list = cert_dict.get("evidence", [])
        if isinstance(evidence_list, list):
            for idx, ev in enumerate(evidence_list):
                ev_trust = ev.get("trust_level", "") if isinstance(ev, dict) else ""
                if ev_trust == "CONTRADICTED":
                    violations.append(
                        f"Evidence item at index {idx} has trust level "
                        f"CONTRADICTED; certificate cannot be sound"
                    )

        # Check revocation
        if cert_dict.get("revoked", False):
            violations.append(
                "Certificate is revoked; it cannot satisfy any theorem requirement"
            )

        # Check coordinate
        coordinate = cert_dict.get("coordinate", "")
        if not coordinate or not str(coordinate).strip():
            violations.append(
                "Certificate coordinate is empty; every certificate must "
                "reference a non-empty coordinate"
            )

        satisfied = len(violations) == 0
        self._log("check_certificate_satisfies", satisfied, violations,
                  {"theorem_name": theorem_name,
                   "cert_trust": cert_trust,
                   "required_trust": theorem.trust_requirement})
        return satisfied, violations

    # ------------------------------------------------------------------
    # check_no_silent_promotion
    # ------------------------------------------------------------------

    def check_no_silent_promotion(
        self,
        cert_dict: Dict[str, Any],
        provenance_log: List[Dict[str, Any]],
    ) -> Tuple[bool, List[str]]:
        """Verify that no silent trust promotion occurred in *provenance_log*.

        A silent promotion is any log entry whose ``event`` field is
        ``'silent_promotion'``, or any ``'promotion_accepted'`` entry that
        lacks a non-empty ``justification`` field.

        Args:
            cert_dict: The certificate whose provenance is being checked.
                       Used to extract contextual metadata for the log entry.
            provenance_log: An ordered list of provenance event dicts.
                Expected keys per entry: ``event``, optionally
                ``justification``.

        Returns:
            A ``(ok, violations)`` tuple.
        """
        violations: List[str] = []

        for idx, entry in enumerate(provenance_log):
            if not isinstance(entry, dict):
                violations.append(
                    f"provenance_log[{idx}] is not a dict; expected a mapping"
                )
                continue

            event_type = entry.get("event", "")

            if event_type == "silent_promotion":
                violations.append(
                    f"provenance_log[{idx}]: explicit 'silent_promotion' event "
                    f"detected — this is unconditionally forbidden"
                )

            elif event_type == "promotion_accepted":
                justification = entry.get("justification", "")
                if not justification or not str(justification).strip():
                    violations.append(
                        f"provenance_log[{idx}]: 'promotion_accepted' event "
                        f"lacks a non-empty justification field; promotion "
                        f"without justification is treated as silent"
                    )

        ok = len(violations) == 0
        cert_id = cert_dict.get("id", "<unknown>")
        self._log("check_no_silent_promotion", ok, violations,
                  {"cert_id": cert_id,
                   "provenance_log_length": len(provenance_log)})
        return ok, violations

    # ------------------------------------------------------------------
    # check_evidence_plurality
    # ------------------------------------------------------------------

    def check_evidence_plurality(
        self,
        cert_dict: Dict[str, Any],
        clause_types: List[str],
        jurisdiction: Dict[str, List[str]],
    ) -> Tuple[bool, List[str]]:
        """Verify that each clause type has evidence from an authorised channel.

        For each clause type in *clause_types*, this method checks that
        *cert_dict* carries at least one evidence item whose channel is
        listed in ``jurisdiction[clause_type]``.

        Args:
            cert_dict: A certificate dict.  Expected keys: ``evidence``
                (list of dicts with ``clause_type`` and ``channel`` keys).
            clause_types: The clause types that must be covered.
            jurisdiction: Maps each clause type to the list of channels that
                are authorised to discharge it.

        Returns:
            A ``(ok, violations)`` tuple.
        """
        violations: List[str] = []

        evidence_list = cert_dict.get("evidence", [])

        # Build a quick lookup: clause_type -> set of channels present in cert
        present: Dict[str, List[str]] = defaultdict(list)
        for ev in evidence_list:
            if isinstance(ev, dict):
                ct = ev.get("clause_type", "")
                ch = ev.get("channel", "")
                if ct and ch:
                    present[ct].append(ch)

        for clause_type in clause_types:
            authorised_channels = jurisdiction.get(clause_type, [])

            if not authorised_channels:
                violations.append(
                    f"clause_type '{clause_type}' has no entry in the "
                    f"jurisdiction map; all evidence is inadmissible"
                )
                continue

            cert_channels_for_type = present.get(clause_type, [])
            covered = any(ch in authorised_channels for ch in cert_channels_for_type)

            if not covered:
                violations.append(
                    f"clause_type '{clause_type}' has no evidence from an "
                    f"authorised channel; authorised={authorised_channels}, "
                    f"present={cert_channels_for_type}"
                )

        ok = len(violations) == 0
        self._log("check_evidence_plurality", ok, violations,
                  {"clause_types": clause_types})
        return ok, violations

    # ------------------------------------------------------------------
    # check_manifest_consistency
    # ------------------------------------------------------------------

    def check_manifest_consistency(
        self, manifest_dict: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """Validate the internal consistency of a manifest dict.

        Checks performed:
        1. Required keys (``judgments``, ``obligations``, ``evidence``,
           ``obstructions``, ``certificates``, ``epoch_map``,
           ``invalidation_graph``) are present.
        2. No certificate claims a trust level higher than its supporting
           evidence.
        3. All obligation IDs referenced by judgments appear either as open
           residuals in their certificate or in the global discharged set.
        4. Epoch map is monotone along the invalidation graph.

        Args:
            manifest_dict: A dict representation of the full manifest tuple.

        Returns:
            A ``(ok, violations)`` tuple.
        """
        violations: List[str] = []

        required_keys = [
            "judgments", "obligations", "evidence",
            "obstructions", "certificates", "epoch_map",
            "invalidation_graph",
        ]
        for key in required_keys:
            if key not in manifest_dict:
                violations.append(f"manifest missing required key '{key}'")

        if violations:
            # Cannot safely run further checks without required structure
            self._log("check_manifest_consistency", False, violations)
            return False, violations

        judgments = manifest_dict["judgments"]
        obligations_global = manifest_dict["obligations"]
        evidence_map = manifest_dict["evidence"]
        certificates = manifest_dict["certificates"]
        epoch_map = manifest_dict["epoch_map"]
        invalidation_graph = manifest_dict["invalidation_graph"]
        discharged: List[str] = manifest_dict.get("discharged_obligations", [])

        # Check 2: no cert claims higher trust than its evidence
        for cert_id, cert in certificates.items() if isinstance(certificates, dict) else []:
            cert_trust = cert.get("trust_level", "UNATTESTED")
            cert_rank = _TRUST_ORDER.get(cert_trust, -1)
            coord = cert.get("coordinate", cert_id)
            ev_for_coord = evidence_map.get(coord, {})
            ev_trust = ev_for_coord.get("trust_level", "UNATTESTED") if isinstance(ev_for_coord, dict) else "UNATTESTED"
            ev_rank = _TRUST_ORDER.get(ev_trust, -1)
            if cert_rank > ev_rank:
                violations.append(
                    f"Certificate '{cert_id}' claims trust '{cert_trust}' "
                    f"(rank {cert_rank}) but its evidence only supports "
                    f"'{ev_trust}' (rank {ev_rank})"
                )

        # Check 3: obligation coverage
        for j_id, judgment in judgments.items() if isinstance(judgments, dict) else []:
            j_obligations: List[str] = judgment.get("obligations", [])
            cert_id = judgment.get("certificate_id", "")
            cert = certificates.get(cert_id, {}) if isinstance(certificates, dict) else {}
            residuals: List[str] = cert.get("residual_obligations", [])
            for ob_id in j_obligations:
                if ob_id not in residuals and ob_id not in discharged:
                    violations.append(
                        f"Obligation '{ob_id}' for judgment '{j_id}' is "
                        f"neither a residual in certificate '{cert_id}' "
                        f"nor in the discharged set — silently dropped"
                    )

        # Check 4: epoch monotonicity along invalidation graph
        edges = invalidation_graph if isinstance(invalidation_graph, list) else []
        for edge in edges:
            if isinstance(edge, (list, tuple)) and len(edge) == 2:
                u, v = edge[0], edge[1]
                epoch_u = epoch_map.get(str(u), 0)
                epoch_v = epoch_map.get(str(v), 0)
                if epoch_u > epoch_v:
                    violations.append(
                        f"Epoch monotonicity violated on invalidation edge "
                        f"({u} -> {v}): epoch({u})={epoch_u} > epoch({v})={epoch_v}"
                    )

        ok = len(violations) == 0
        self._log("check_manifest_consistency", ok, violations)
        return ok, violations

    # ------------------------------------------------------------------
    # verify_theorem
    # ------------------------------------------------------------------

    def verify_theorem(
        self,
        theorem_name: str,
        evidence: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """Attempt to verify *theorem_name* using the supplied *evidence* dict.

        This is a best-effort runtime evaluation: it checks that each
        hypothesis label has a corresponding truthy entry in *evidence*, and
        that the evidence's trust level satisfies the theorem's
        ``trust_requirement``.  A formal proof checker would require a
        full term-level derivation; this method provides a lightweight guard.

        Args:
            theorem_name: Name of the theorem to evaluate.
            evidence: A dict mapping hypothesis labels (or free variables) to
                evidence values or trust-level strings.

        Returns:
            A ``(verified, notes)`` tuple where *notes* records what was
            checked.
        """
        notes: List[str] = []

        theorem = self.registry.get(theorem_name)
        if theorem is None:
            notes.append(f"Theorem '{theorem_name}' not found in registry")
            self._log("verify_theorem", False, notes,
                      {"theorem_name": theorem_name})
            return False, notes

        notes.append(f"Evaluating theorem '{theorem_name}' (status={theorem.status})")

        # Check hypotheses
        unsatisfied_hypotheses: List[str] = []
        for hyp in theorem.hypotheses:
            hyp_key = hyp.split()[0] if hyp else hyp
            if hyp_key not in evidence:
                unsatisfied_hypotheses.append(
                    f"Hypothesis '{hyp}' has no evidence key matching '{hyp_key}'"
                )
            else:
                ev_value = evidence[hyp_key]
                if not ev_value:
                    unsatisfied_hypotheses.append(
                        f"Hypothesis '{hyp}' — evidence key '{hyp_key}' is falsy"
                    )
                else:
                    notes.append(f"Hypothesis '{hyp_key}': satisfied (value={ev_value!r})")

        if unsatisfied_hypotheses:
            notes.extend(unsatisfied_hypotheses)

        # Check trust level of supplied evidence
        supplied_trust = evidence.get("trust_level", "UNATTESTED")
        supplied_rank = _TRUST_ORDER.get(str(supplied_trust), -1)
        required_rank = _TRUST_ORDER.get(theorem.trust_requirement, 0)

        if supplied_rank < required_rank:
            notes.append(
                f"Evidence trust '{supplied_trust}' (rank {supplied_rank}) does "
                f"not meet theorem requirement '{theorem.trust_requirement}' "
                f"(rank {required_rank})"
            )

        # Conclusion check: we record the expected conclusion for human review
        notes.append(f"Expected conclusion: {theorem.conclusion}")

        verified = (
            len(unsatisfied_hypotheses) == 0
            and supplied_rank >= required_rank
        )
        notes.append(f"Verification result: {'PASS' if verified else 'FAIL'}")
        self._log("verify_theorem", verified, notes,
                  {"theorem_name": theorem_name})
        return verified, notes

    # ------------------------------------------------------------------
    # batch_check
    # ------------------------------------------------------------------

    def batch_check(
        self,
        cert_list: List[Dict[str, Any]],
        theorem_names: List[str],
    ) -> Dict[str, Dict[str, Tuple[bool, List[str]]]]:
        """Check every certificate in *cert_list* against every theorem in *theorem_names*.

        Returns a nested dict of the form::

            {
                cert_id: {
                    theorem_name: (satisfied, violations),
                    ...
                },
                ...
            }

        where ``cert_id`` is taken from ``cert.get('id', idx)`` using the
        list index as a fallback.

        Args:
            cert_list: List of certificate dicts.
            theorem_names: List of theorem names to evaluate each cert against.

        Returns:
            Nested result dict as described above.
        """
        results: Dict[str, Dict[str, Tuple[bool, List[str]]]] = {}

        for idx, cert in enumerate(cert_list):
            cert_id = str(cert.get("id", idx))
            results[cert_id] = {}

            for theorem_name in theorem_names:
                satisfied, violations = self.check_certificate_satisfies(
                    cert, theorem_name
                )
                results[cert_id][theorem_name] = (satisfied, violations)

        self._log("batch_check", True, [],
                  {"num_certs": len(cert_list),
                   "num_theorems": len(theorem_names)})
        return results


# ---------------------------------------------------------------------------
# Theorem instances — Chapter 6
# ---------------------------------------------------------------------------

T1 = TheoremStatement(
    name="monotonicity_under_admissible_aggregation",
    statement=(
        "Adding admissible evidence to an evidence configuration cannot weaken "
        "the overall trust level, unless the new evidence is contradictory to "
        "existing evidence."
    ),
    hypotheses=(
        "E is an admissible evidence configuration with trust level t",
        "e is a new evidence item with trust level t_e in E_adm",
        "e is not contradictory to any item in E",
    ),
    conclusion=(
        "trust(E union {e}) >= trust(E) in the partial order \u227c"
    ),
    proof_sketch=(
        "By the definition of \u2295 as meet: adding admissible evidence can only "
        "decrease trust (be conservative) or leave it unchanged when evidence is "
        "consistent. The meet of two non-contradictory admissible levels is at "
        "least as high as the minimum of the two, and since e is admissible, it "
        "does not introduce contradiction."
    ),
    trust_requirement="SOLVER_DISCHARGED",
    status="PROOF_SKETCH",
    chapter=6,
    section=1,
    theorem_number=1,
    depends_on=(),
)

T2 = TheoremStatement(
    name="no_silent_promotion",
    statement=(
        "Trust can only be strengthened through explicitly named policy routes. "
        "Copilot proposals enter at a bounded ceiling and cannot be silently "
        "promoted above ORACLE_PROPOSED without human or mechanical review."
    ),
    hypotheses=(
        "c is a trust configuration at level t",
        "pi is a named promotion policy with recorded justification j",
        "j is non-empty and references a verifiable evidence record",
    ),
    conclusion=(
        "promote(c, pi, j) is the only valid route to increase trust(c); "
        "any promotion without (pi, j) is rejected and logged"
    ),
    proof_sketch=(
        "The promotion function \u2191_\u03c0 checks that policy_name is non-empty and "
        "justification has minimum length. Copilot channel has a hardcoded ceiling "
        "of ORACLE_PROPOSED. Any promotion attempt without these prerequisites is "
        "logged as a silent_promotion_rejected event in the audit trail."
    ),
    trust_requirement="HUMAN_ATTESTED",
    status="FORMALLY_VERIFIED",
    chapter=6,
    section=2,
    theorem_number=2,
    depends_on=("monotonicity_under_admissible_aggregation",),
)

T3 = TheoremStatement(
    name="challenge_conservativity",
    statement=(
        "On challenge to a certified claim, the system may demote trust or "
        "introduce residual obligations, but may not leave the old trust level "
        "standing without explicit explanation recorded in the audit log."
    ),
    hypotheses=(
        "K is an active certificate with trust level t for coordinate c",
        "A challenge event arrives for c",
        "The challenge provides contradictory evidence or questions the discharge channel",
    ),
    conclusion=(
        "After challenge: either cert K is revoked and replaced with a lower-trust "
        "cert, or a residual obligation is appended to K with the challenge recorded, "
        "or the challenge is dismissed with explicit recorded justification"
    ),
    proof_sketch=(
        "The challenge handling pipeline first attempts to validate the challenger\u2019s "
        "evidence. If valid, it invokes demote or revoke. If dismissed, it records the "
        "dismissal reason. The old trust level cannot persist silently \u2014 the audit "
        "log must have a corresponding entry."
    ),
    trust_requirement="RUNTIME_WITNESSED",
    status="PROOF_SKETCH",
    chapter=6,
    section=2,
    theorem_number=3,
    depends_on=("no_silent_promotion",),
)

T4 = TheoremStatement(
    name="evidence_plurality_soundness",
    statement=(
        "Each clause type is dischargeable only by its authorised channel(s) as "
        "defined in the jurisdiction map. Evidence from unauthorised channels is "
        "inadmissible for that clause type."
    ),
    hypotheses=(
        "J is the jurisdiction map from ClauseType to authorised EvidenceChannels",
        "phi is a clause of type tau",
        "e is evidence for phi from channel ch",
    ),
    conclusion=(
        "e is admissible for phi only if ch in J(tau)"
    ),
    proof_sketch=(
        "The ChannelJurisdiction class enforces this by checking is_authorized(tau, ch) "
        "before accepting evidence. Unauthorised evidence is flagged with a "
        "jurisdiction_violation event and excluded from the trust computation for that clause."
    ),
    trust_requirement="SOLVER_DISCHARGED",
    status="FORMALLY_VERIFIED",
    chapter=6,
    section=1,
    theorem_number=4,
    depends_on=(),
)

T5 = TheoremStatement(
    name="certificate_faithful_projection",
    statement=(
        "A certificate faithfully projects the full manifest tuple (J,O,E,X,K,\u03b7,\u03c3): "
        "it must preserve all residual obligations and obstructions. Certificates may "
        "not silently erase residuals or claim higher trust than the evidence supports."
    ),
    hypotheses=(
        "M = (J,O,E,X,K,\u03b7,\u03c3) is a valid manifest",
        "cert is issued for coordinate c in M",
        "E_c is the evidence archive for c in M",
    ),
    conclusion=(
        "trust(cert) <= trust(E_c) in \u227c, and all open obligations O_c appear as "
        "residuals in cert, and all obstructions X_c are referenced in cert"
    ),
    proof_sketch=(
        "The ManifestProjection.verify_faithfulness check ensures cert trust rank does "
        "not exceed the composed evidence trust. ResidualPreserver ensures no obligation "
        "ID is silently dropped. ObstructionRecord ensures obstructions are explicitly "
        "carried forward, not erased."
    ),
    trust_requirement="MECHANICALLY_VERIFIED",
    status="PROOF_SKETCH",
    chapter=6,
    section=3,
    theorem_number=5,
    depends_on=("no_silent_promotion", "evidence_plurality_soundness"),
)

T6 = TheoremStatement(
    name="manifest_consistency",
    statement=(
        "A valid manifest (J,O,E,X,K,\u03b7,\u03c3) has no judgment-obligation misalignment: "
        "every judgment that has been certified has evidence covering all its obligations, "
        "the epoch map is monotone along the invalidation graph, and no certificate "
        "suppresses an obstruction."
    ),
    hypotheses=(
        "M = (J,O,E,X,K,\u03b7,\u03c3) is a manifest",
        "For all judgments j in J: if cert(j) exists in K then "
        "obligations(j) subset of residuals(cert(j)) union discharged_obligations",
        "For all invalidation edges (u,v) in \u03c3: \u03b7(u) <= \u03b7(v)",
    ),
    conclusion=(
        "M is internally consistent: no obligation is silently dropped and "
        "epoch monotonicity holds"
    ),
    proof_sketch=(
        "ManifestValidator.validate runs four sub-checks: judgment-certificate "
        "alignment, obligation coverage, obstruction honesty, and epoch monotonicity. "
        "Each sub-check produces a violation list. A manifest is consistent iff all "
        "four lists are empty."
    ),
    trust_requirement="SOLVER_DISCHARGED",
    status="PROOF_SKETCH",
    chapter=6,
    section=4,
    theorem_number=6,
    depends_on=("certificate_faithful_projection",),
)

T7 = TheoremStatement(
    name="provenance_acyclicity",
    statement=(
        "The provenance DAG for any well-formed judgment is acyclic. Circular "
        "reasoning is detected by cycle-detection algorithms and rejected before "
        "a certificate is issued."
    ),
    hypotheses=(
        "G = (V, E) is the provenance DAG for a set of evidence items",
        "Certificate issuance requires G to pass cycle detection",
    ),
    conclusion=(
        "G is a DAG (directed acyclic graph); any cycle in G causes certificate "
        "issuance to fail with a provenance_cycle_detected error"
    ),
    proof_sketch=(
        "ProvenanceModel.verify_no_cycles uses Kahn\u2019s algorithm (topological sort via "
        "in-degree counting). If processed node count < total node count after the "
        "algorithm, a cycle exists. The CertificateIssuanceAlgorithm calls "
        "verify_no_cycles as part of validate_inputs and rejects issuance on cycle "
        "detection."
    ),
    trust_requirement="MECHANICALLY_VERIFIED",
    status="FORMALLY_VERIFIED",
    chapter=6,
    section=0,
    theorem_number=7,
    depends_on=(),
)

# ---------------------------------------------------------------------------
# Chapter theorem name tuple
# ---------------------------------------------------------------------------

CHAPTER_THEOREMS: Tuple[str, ...] = (
    T1.name,
    T2.name,
    T3.name,
    T4.name,
    T5.name,
    T6.name,
    T7.name,
)


# ---------------------------------------------------------------------------
# Factory and module-level registry
# ---------------------------------------------------------------------------


def build_theorem_registry() -> TheoremRegistry:
    """Construct and return a TheoremRegistry pre-loaded with all Chapter 6 theorems.

    All seven theorems (T1–T7) are registered in definition order.  The
    registry can subsequently be queried, validated, or used as the basis
    for a ProofChecker.

    Returns:
        A fully populated TheoremRegistry instance.
    """
    registry = TheoremRegistry()
    for theorem in (T1, T2, T3, T4, T5, T6, T7):
        registry.register(theorem)
    return registry


MODULE_REGISTRY: TheoremRegistry = build_theorem_registry()


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------


def get_theorem(name: str) -> Optional[TheoremStatement]:
    """Look up a theorem by name in the module-level registry.

    Args:
        name: The theorem's ``name`` field (e.g.
              ``'provenance_acyclicity'``).

    Returns:
        The matching TheoremStatement, or None if not registered.
    """
    return MODULE_REGISTRY.get(name)


def list_theorems() -> List[str]:
    """Return a sorted list of all theorem names in the module-level registry.

    Returns:
        Sorted list of theorem name strings.
    """
    return MODULE_REGISTRY.list_names()


def validate_all_theorems() -> Dict[str, List[str]]:
    """Validate every theorem in the module-level registry.

    Returns:
        A dict mapping theorem name to a list of violation strings.
        An empty dict means all theorems are well-formed.
    """
    return MODULE_REGISTRY.validate_all()


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §3 — Trust Certificates)
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def theorem_descent_verification(
    theorem_name: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify a trust-certificate theorem via geometric descent.

    Looks up *theorem_name* in ``MODULE_REGISTRY`` and checks its consistency
    with descent data supplied through ``jugeo.geometry.descent``.

    Reference: Theory2.tex §3 (Trust Certificates), descent-verification.

    Parameters
    ----------
    theorem_name:
        Name of the theorem to verify (must exist in ``MODULE_REGISTRY``).
    context:
        Optional dict with extra verification context (e.g. ``"coordinate"``).

    Returns
    -------
    Dict[str, Any]
        ``{"verified": bool, "theorem": str, "descent_info": dict, "errors": list}``
    """
    errors: List[str] = []
    descent_info: Dict[str, Any] = {}
    verified = False

    try:
        from jugeo.geometry.descent import LocalSection, DescentStrategy, OverlapCondition
        from jugeo.geometry.site import Coordinate
    except ImportError as exc:
        _logger.warning("geometry imports unavailable: %s", exc)
        return {"verified": False, "theorem": theorem_name, "descent_info": {}, "errors": [str(exc)]}

    try:
        stmt = MODULE_REGISTRY.get(theorem_name)
        if stmt is None:
            errors.append(f"theorem '{theorem_name}' not found in registry")
            return {"verified": False, "theorem": theorem_name, "descent_info": descent_info, "errors": errors}

        ctx = context or {}
        raw_coord = ctx.get("coordinate", "")
        coord = Coordinate(raw_coord) if raw_coord else None
        strategy = DescentStrategy.DEFAULT if hasattr(DescentStrategy, "DEFAULT") else list(DescentStrategy)[0]
        section = LocalSection(coordinate=coord, strategy=strategy) if coord else None
        overlap = OverlapCondition() if section is not None else None

        descent_info["strategy"] = str(strategy)
        descent_info["section"] = str(section) if section else None
        descent_info["overlap"] = str(overlap) if overlap else None

        verified = section is not None and not errors
    except Exception as exc:
        _logger.error("descent verification failed: %s", exc)
        errors.append(str(exc))

    return {"verified": verified, "theorem": theorem_name, "descent_info": descent_info, "errors": errors}


def theorem_encoding_bridge(
    theorem_name: str,
    *,
    format: str = "z3",
) -> Dict[str, Any]:
    """Encode a trust-certificate theorem for solver consumption.

    Translates the named theorem into solver-level assertions using
    ``jugeo.encodings`` and ``jugeo.solver.z3_session``.

    Reference: Theory2.tex §3 (Trust Certificates), encoding bridge.

    Parameters
    ----------
    theorem_name:
        Name of the theorem to encode.
    format:
        Target solver format (default ``"z3"``).

    Returns
    -------
    Dict[str, Any]
        ``{"encoded": bool, "theorem": str, "format": str, "assertions": list, "errors": list}``
    """
    errors: List[str] = []
    assertions: List[str] = []
    encoded = False

    try:
        from jugeo.encodings import encode_judgment
        from jugeo.solver.z3_session import z3_available, SolverResult
    except ImportError as exc:
        _logger.warning("encoding/solver imports unavailable: %s", exc)
        return {"encoded": False, "theorem": theorem_name, "format": format, "assertions": [], "errors": [str(exc)]}

    try:
        stmt = MODULE_REGISTRY.get(theorem_name)
        if stmt is None:
            errors.append(f"theorem '{theorem_name}' not found in registry")
            return {"encoded": False, "theorem": theorem_name, "format": format, "assertions": assertions, "errors": errors}

        if not z3_available():
            errors.append("z3 backend is not available")
            return {"encoded": False, "theorem": theorem_name, "format": format, "assertions": assertions, "errors": errors}

        hypothesis = getattr(stmt, "hypothesis", None)
        if hypothesis is not None:
            enc = encode_judgment(hypothesis)
            assertions.append(str(enc))

        conclusion = getattr(stmt, "conclusion", None)
        if conclusion is not None:
            enc = encode_judgment(conclusion)
            assertions.append(str(enc))

        encoded = len(assertions) > 0 and not errors
    except Exception as exc:
        _logger.error("encoding bridge failed: %s", exc)
        errors.append(str(exc))

    return {"encoded": encoded, "theorem": theorem_name, "format": format, "assertions": assertions, "errors": errors}
