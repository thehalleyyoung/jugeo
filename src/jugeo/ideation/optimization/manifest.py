"""Optimization manifest registry for JuGeo ideation optimization (Ch50).

Provides versioned algorithm descriptors, package-level manifests, and
validator utilities for the optimization subsystem.
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# 1. Module-level setup
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 2. Helper functions
# ---------------------------------------------------------------------------


def _validate_algorithm_id(algorithm_id: str) -> bool:
    """Return True if *algorithm_id* is non-empty and contains only safe chars.

    A valid algorithm ID must be a non-empty string whose characters are
    limited to ASCII letters, digits, hyphens, and underscores.  This
    prevents accidental injection of whitespace or special characters that
    could corrupt serialised manifests.

    Args:
        algorithm_id: Candidate identifier string to check.

    Returns:
        ``True`` when the identifier passes all checks, ``False`` otherwise.
    """
    if not algorithm_id or not isinstance(algorithm_id, str):
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    return all(ch in allowed for ch in algorithm_id)


def _format_complexity(complexity: str) -> str:
    """Return a normalised, human-readable representation of *complexity*.

    Strips surrounding whitespace and ensures the string starts with the
    conventional big-O prefix ``O(``.  If the prefix is absent the raw
    value is returned unchanged so that informal notations (e.g.
    ``'linear'``) are preserved without raising.

    Args:
        complexity: Raw complexity string, e.g. ``'O(n*k)'`` or ``' O(n^2) '``.

    Returns:
        Cleaned complexity string.
    """
    cleaned = complexity.strip()
    if cleaned and not cleaned.startswith("O("):
        return cleaned
    return cleaned


# ---------------------------------------------------------------------------
# 3. Core data-model classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlgorithmDescriptor:
    """Immutable descriptor for a single optimisation algorithm.

    Stores the algorithm's identity, asymptotic complexity, tuneable
    parameters, and a human-readable description.  Because the dataclass is
    frozen it is safe to use as a dictionary key or to store in sets.

    Attributes:
        algorithm_id: Unique, URL-safe identifier for the algorithm.
        name: Human-readable display name.
        complexity: Big-O complexity string, e.g. ``'O(n*k)'``.
        parameters: Default parameter dictionary (values are serialisable).
        description: Longer prose description of what the algorithm does.
    """

    algorithm_id: str
    name: str
    complexity: str
    parameters: dict[str, Any]
    description: str

    def __hash__(self) -> int:
        return hash(
            (
                self.algorithm_id,
                self.name,
                self.complexity,
                tuple(sorted(self.parameters.items())),
                self.description,
            )
        )

    def summary(self) -> str:
        """Return a concise one-line summary of this algorithm.

        The summary is suitable for use in log messages, CLI output, and
        report tables.  It includes the algorithm ID, display name, and
        complexity in a fixed-width-friendly format.

        Returns:
            Formatted one-liner string.
        """
        param_count = self.parameter_count()
        poly = "poly" if self.is_polynomial() else "non-poly"
        return (
            f"[{self.algorithm_id}] {self.name} | {_format_complexity(self.complexity)}"
            f" | {param_count} param(s) | {poly}"
        )

    def parameter_count(self) -> int:
        """Return the number of tuneable parameters for this algorithm.

        Returns:
            Integer count of keys in :attr:`parameters`.
        """
        return len(self.parameters)

    def is_polynomial(self) -> bool:
        """Return ``True`` if the complexity is classified as polynomial.

        Polynomial complexities include anything whose big-O expression
        starts with ``O(n`` (e.g. ``O(n)``, ``O(n^2)``, ``O(n*k)``) or
        ``O(log`` (e.g. ``O(log n)``).  Exponential, factorial, and
        pseudo-polynomial notations return ``False``.

        Returns:
            Boolean classification result.
        """
        c = self.complexity.strip().lower().replace(" ", "")
        if c.startswith("o(log"):
            return True
        if "!" in c or "^n" in c:
            return False
        return c.startswith("o(n")


# ---------------------------------------------------------------------------
# 4. Manifest class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OptimizationManifest:
    """Versioned registry of algorithm descriptors for one optimisation package.

    An :class:`OptimizationManifest` ties together a semantic version string,
    a unique package identifier, and a dictionary of :class:`AlgorithmDescriptor`
    objects.  It acts as the authoritative source of truth for which algorithms
    are available within a given deployment of the JuGeo ideation optimisation
    subsystem.

    Attributes:
        version: Semantic version string (e.g. ``'1.0.0'``).
        package_id: Unique identifier for the owning package.
        algorithm_registry: Maps algorithm IDs to their descriptors.
        created_at: ISO-8601 UTC timestamp of manifest creation.
        description: Optional prose description of this manifest.
    """

    version: str
    package_id: str
    algorithm_registry: dict[str, AlgorithmDescriptor] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    description: str = ""

    def register(self, descriptor: AlgorithmDescriptor) -> None:
        """Add *descriptor* to this manifest's algorithm registry.

        If an algorithm with the same ID already exists it will be
        silently overwritten and a warning will be emitted to the module
        logger.

        Args:
            descriptor: The :class:`AlgorithmDescriptor` to register.
        """
        if descriptor.algorithm_id in self.algorithm_registry:
            _log.warning(
                "Overwriting existing algorithm descriptor '%s' in manifest '%s'.",
                descriptor.algorithm_id,
                self.package_id,
            )
        self.algorithm_registry[descriptor.algorithm_id] = descriptor
        _log.debug(
            "Registered algorithm '%s' (%s) in manifest '%s'.",
            descriptor.algorithm_id,
            descriptor.name,
            self.package_id,
        )

    def lookup(self, algorithm_id: str) -> AlgorithmDescriptor | None:
        """Return the descriptor for *algorithm_id*, or ``None`` if absent.

        Args:
            algorithm_id: The identifier to look up.

        Returns:
            Matching :class:`AlgorithmDescriptor` or ``None``.
        """
        return self.algorithm_registry.get(algorithm_id)

    def summary(self) -> str:
        """Return a multi-line human-readable report for this manifest.

        Includes version, package ID, creation timestamp, algorithm count,
        and a bulleted list of each registered algorithm's one-line summary.

        Returns:
            Multi-line string report.
        """
        lines = [
            f"OptimizationManifest v{self.version}",
            f"  package_id  : {self.package_id}",
            f"  created_at  : {self.created_at}",
            f"  description : {self.description or '(none)'}",
            f"  algorithms  : {self.algorithm_count()}",
        ]
        for desc in self.algorithm_registry.values():
            lines.append(f"    • {desc.summary()}")
        return "\n".join(lines)

    def copilot_report(self) -> str:
        """Return a rich-text report suitable for Copilot inline display.

        The report uses Markdown-style headers and tables to present
        manifest metadata and algorithm details in a structured format.

        Returns:
            Rich multi-line Markdown string.
        """
        header = (
            f"## OptimizationManifest Report\n"
            f"- **Version**: {self.version}\n"
            f"- **Package ID**: {self.package_id}\n"
            f"- **Created**: {self.created_at}\n"
            f"- **Description**: {self.description or '_none_'}\n"
            f"- **Algorithm count**: {self.algorithm_count()}\n\n"
            f"### Registered Algorithms\n"
        )
        rows = ["| ID | Name | Complexity | Params | Polynomial |",
                "|----|------|-----------|--------|------------|"]
        for desc in self.algorithm_registry.values():
            rows.append(
                f"| {desc.algorithm_id} | {desc.name} | "
                f"{_format_complexity(desc.complexity)} | "
                f"{desc.parameter_count()} | "
                f"{'✓' if desc.is_polynomial() else '✗'} |"
            )
        return header + "\n".join(rows)

    def algorithm_count(self) -> int:
        """Return the number of algorithms registered in this manifest.

        Returns:
            Integer count.
        """
        return len(self.algorithm_registry)

    def remove(self, algorithm_id: str) -> bool:
        """Remove the algorithm identified by *algorithm_id*.

        Args:
            algorithm_id: ID of the algorithm to remove.

        Returns:
            ``True`` if the algorithm was found and removed, ``False`` if it
            was not present.
        """
        if algorithm_id in self.algorithm_registry:
            del self.algorithm_registry[algorithm_id]
            _log.debug("Removed algorithm '%s' from manifest '%s'.", algorithm_id, self.package_id)
            return True
        return False


# ---------------------------------------------------------------------------
# 5. Validator
# ---------------------------------------------------------------------------


class ManifestValidator:
    """Validates :class:`OptimizationManifest` instances for structural correctness.

    Performs a suite of checks covering version format, package ID
    validity, algorithm registry integrity, and descriptor completeness.
    Returns human-readable issue descriptions rather than raising
    exceptions, enabling callers to collect and report all issues at once.
    """

    def validate(self, manifest: OptimizationManifest) -> list[str]:
        """Run all validation checks on *manifest* and return a list of issues.

        An empty list indicates that the manifest passed all checks.  Each
        non-empty string in the returned list describes one discrete issue.

        Args:
            manifest: The :class:`OptimizationManifest` to validate.

        Returns:
            List of issue description strings (may be empty).
        """
        issues: list[str] = []

        version_issue = self._check_version(manifest.version)
        if version_issue:
            issues.append(version_issue)

        if not manifest.package_id or not isinstance(manifest.package_id, str):
            issues.append("package_id must be a non-empty string.")

        issues.extend(self._check_algorithms(manifest.algorithm_registry))

        if not isinstance(manifest.algorithm_registry, dict):
            issues.append("algorithm_registry must be a dict.")

        return issues

    def is_valid(self, manifest: OptimizationManifest) -> bool:
        """Return ``True`` if *manifest* passes all validation checks.

        Convenience wrapper around :meth:`validate`.

        Args:
            manifest: The manifest to check.

        Returns:
            Boolean validity flag.
        """
        return len(self.validate(manifest)) == 0

    def _check_version(self, version: str) -> str | None:
        """Verify that *version* follows a basic ``MAJOR.MINOR.PATCH`` pattern.

        Args:
            version: Version string to check.

        Returns:
            An issue description string if invalid, ``None`` if valid.
        """
        if not version or not isinstance(version, str):
            return "version must be a non-empty string."
        parts = version.split(".")
        if len(parts) != 3:
            return f"version '{version}' does not follow MAJOR.MINOR.PATCH format."
        for part in parts:
            if not part.isdigit():
                return f"version '{version}' contains non-numeric component '{part}'."
        return None

    def _check_algorithms(self, registry: dict) -> list[str]:
        """Validate each algorithm descriptor in *registry*.

        Checks that every key maps to an :class:`AlgorithmDescriptor`,
        that the ID stored in the descriptor matches the registry key,
        that the name is non-empty, and that the complexity string is
        non-empty.

        Args:
            registry: Mapping of algorithm IDs to descriptors.

        Returns:
            List of issue description strings (may be empty).
        """
        issues: list[str] = []
        for key, desc in registry.items():
            if not isinstance(desc, AlgorithmDescriptor):
                issues.append(f"Registry entry '{key}' is not an AlgorithmDescriptor.")
                continue
            if desc.algorithm_id != key:
                issues.append(
                    f"Descriptor algorithm_id '{desc.algorithm_id}' does not match registry key '{key}'."
                )
            if not desc.name.strip():
                issues.append(f"Descriptor '{key}' has an empty name.")
            if not desc.complexity.strip():
                issues.append(f"Descriptor '{key}' has an empty complexity string.")
            if not _validate_algorithm_id(key):
                issues.append(f"Registry key '{key}' contains invalid characters.")
        return issues


# ---------------------------------------------------------------------------
# 6. Singleton registry
# ---------------------------------------------------------------------------


class ManifestRegistry:
    """Package-level singleton registry of :class:`OptimizationManifest` objects.

    Uses a classic ``_instance`` class-level pattern so that all parts of
    the codebase share the same registry without explicit dependency injection.
    Thread safety is not guaranteed; callers requiring concurrent access should
    implement their own locking.

    Usage::

        registry = ManifestRegistry.instance()
        registry.add(my_manifest)
        m = registry.get("my-package")
    """

    _instance: ManifestRegistry | None = None
    _manifests: dict[str, OptimizationManifest]

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._manifests = {}

    @classmethod
    def instance(cls) -> ManifestRegistry:
        """Return the shared singleton instance, creating it if necessary.

        Returns:
            The global :class:`ManifestRegistry` instance.
        """
        if cls._instance is None:
            cls._instance = cls()
            _log.debug("ManifestRegistry singleton created.")
        return cls._instance

    def add(self, manifest: OptimizationManifest) -> None:
        """Add *manifest* to the registry, keyed by its package ID.

        If a manifest with the same package ID already exists it will be
        overwritten and a warning logged.

        Args:
            manifest: The :class:`OptimizationManifest` to store.
        """
        if manifest.package_id in self._manifests:
            _log.warning("Overwriting manifest for package_id '%s'.", manifest.package_id)
        self._manifests[manifest.package_id] = manifest
        _log.debug("ManifestRegistry: added manifest '%s'.", manifest.package_id)

    def get(self, package_id: str) -> OptimizationManifest | None:
        """Return the manifest for *package_id*, or ``None`` if absent.

        Args:
            package_id: Identifier of the package whose manifest is requested.

        Returns:
            :class:`OptimizationManifest` or ``None``.
        """
        return self._manifests.get(package_id)

    def list_all(self) -> list[OptimizationManifest]:
        """Return all registered manifests as an ordered list.

        The order is insertion order, preserved by Python's built-in ``dict``.

        Returns:
            List of :class:`OptimizationManifest` objects.
        """
        return list(self._manifests.values())

    def remove(self, package_id: str) -> bool:
        """Remove the manifest for *package_id*.

        Args:
            package_id: Package identifier to remove.

        Returns:
            ``True`` if removed, ``False`` if not found.
        """
        if package_id in self._manifests:
            del self._manifests[package_id]
            _log.debug("ManifestRegistry: removed manifest '%s'.", package_id)
            return True
        return False

    def clear(self) -> None:
        """Remove all manifests from the registry.

        This is primarily useful in test teardowns.
        """
        self._manifests.clear()
        _log.debug("ManifestRegistry: cleared all manifests.")


# ---------------------------------------------------------------------------
# 7. Algorithm registry
# ---------------------------------------------------------------------------


class AlgorithmRegistry:
    """Standalone (non-singleton) registry of :class:`AlgorithmDescriptor` objects.

    Unlike :class:`ManifestRegistry`, instances of :class:`AlgorithmRegistry`
    are independent of each other, making them suitable for use in tests and
    in scoped contexts where a shared global state is undesirable.

    Attributes:
        _descriptors: Internal mapping of algorithm ID to descriptor.
    """

    def __init__(self) -> None:
        """Initialise an empty algorithm registry."""
        self._descriptors: dict[str, AlgorithmDescriptor] = {}

    def register(self, desc: AlgorithmDescriptor) -> None:
        """Add *desc* to this registry.

        Overwrites any existing descriptor with the same ID.

        Args:
            desc: :class:`AlgorithmDescriptor` to register.
        """
        if not _validate_algorithm_id(desc.algorithm_id):
            _log.warning("AlgorithmRegistry: invalid algorithm_id '%s'.", desc.algorithm_id)
        self._descriptors[desc.algorithm_id] = desc
        _log.debug("AlgorithmRegistry: registered '%s'.", desc.algorithm_id)

    def lookup(self, algorithm_id: str) -> AlgorithmDescriptor | None:
        """Return the descriptor for *algorithm_id*, or ``None``.

        Args:
            algorithm_id: Identifier to look up.

        Returns:
            Matching :class:`AlgorithmDescriptor` or ``None``.
        """
        return self._descriptors.get(algorithm_id)

    def all_ids(self) -> list[str]:
        """Return a sorted list of all registered algorithm IDs.

        Returns:
            Sorted list of identifier strings.
        """
        return sorted(self._descriptors.keys())

    def by_complexity(self, complexity: str) -> list[AlgorithmDescriptor]:
        """Return all descriptors whose complexity contains *complexity* as a substring.

        The match is case-sensitive and uses plain substring inclusion.

        Args:
            complexity: Substring to search for (e.g. ``'O(n'``).

        Returns:
            List of matching :class:`AlgorithmDescriptor` objects.
        """
        return [d for d in self._descriptors.values() if complexity in d.complexity]

    def count(self) -> int:
        """Return the total number of registered descriptors.

        Returns:
            Integer count.
        """
        return len(self._descriptors)

    def summary(self) -> str:
        """Return a formatted summary of all registered algorithms.

        Includes a header line and a bullet for each algorithm's one-line
        summary.

        Returns:
            Multi-line string.
        """
        lines = [f"AlgorithmRegistry ({self.count()} algorithms):"]
        for desc in self._descriptors.values():
            lines.append(f"  • {desc.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. Module-level factory functions
# ---------------------------------------------------------------------------


def create_default_manifest() -> OptimizationManifest:
    """Create and return a pre-populated :class:`OptimizationManifest`.

    The returned manifest is assigned version ``'1.0.0'`` and a
    deterministic package ID.  It includes five widely-used multi-objective
    and combinatorial optimisation algorithms out of the box:

    * **weighted-sum** – Weighted Sum Scalarisation.
    * **pareto-nsga2** – NSGA-II Pareto Optimiser.
    * **epsilon-constraint** – Epsilon Constraint Method.
    * **simulated-annealing** – Simulated Annealing.
    * **knapsack-dp** – Dynamic Programming Knapsack.

    Returns:
        Fully initialised :class:`OptimizationManifest`.
    """
    manifest = OptimizationManifest(
        version="1.0.0",
        package_id="jugeo-ideation-optimization",
        description="Default optimisation manifest for JuGeo ideation (Ch50).",
    )

    descriptors: list[AlgorithmDescriptor] = [
        AlgorithmDescriptor(
            algorithm_id="weighted-sum",
            name="Weighted Sum Scalarization",
            complexity="O(n*k)",
            parameters={"weights": []},
            description=(
                "Converts the multi-objective problem into a single-objective "
                "one by computing a weighted linear combination of all objective "
                "scores.  Requires the user to supply a weight vector whose "
                "components sum to 1.  Extremely fast but sensitive to weight "
                "choice and unable to represent non-convex Pareto fronts."
            ),
        ),
        AlgorithmDescriptor(
            algorithm_id="pareto-nsga2",
            name="NSGA-II Pareto Optimizer",
            complexity="O(n^2*k)",
            parameters={"population_size": 100, "generations": 50},
            description=(
                "Non-dominated Sorting Genetic Algorithm II (NSGA-II) is the "
                "canonical evolutionary algorithm for multi-objective "
                "optimisation.  Maintains a population of candidate solutions, "
                "evolves them via selection/crossover/mutation, and uses "
                "crowding-distance ranking to preserve diversity on the "
                "Pareto front."
            ),
        ),
        AlgorithmDescriptor(
            algorithm_id="epsilon-constraint",
            name="Epsilon Constraint Method",
            complexity="O(n*k^2)",
            parameters={"steps": 10},
            description=(
                "Optimises one objective at a time while treating the others "
                "as inequality constraints bounded by epsilon values.  By "
                "varying the epsilon parameters systematically the full Pareto "
                "front can be traced, including non-convex regions that the "
                "weighted-sum method cannot reach."
            ),
        ),
        AlgorithmDescriptor(
            algorithm_id="simulated-annealing",
            name="Simulated Annealing",
            complexity="O(n*T)",
            parameters={"temp": 1.0, "cooling": 0.95},
            description=(
                "A probabilistic metaheuristic that explores the solution space "
                "by accepting neighbour solutions with a probability that "
                "decreases over time (temperature schedule).  Effective for "
                "escaping local optima in large, rugged combinatorial search "
                "spaces where exact methods are intractable."
            ),
        ),
        AlgorithmDescriptor(
            algorithm_id="knapsack-dp",
            name="Dynamic Programming Knapsack",
            complexity="O(n*W)",
            parameters={"precision": 1000},
            description=(
                "Classic 0/1 knapsack solved via dynamic programming over a "
                "discretised capacity grid.  The *precision* parameter controls "
                "the granularity of the capacity axis, trading memory and "
                "runtime for solution accuracy.  Suitable when budget constraints "
                "can be modelled as integer or discretised capacities."
            ),
        ),
    ]

    for desc in descriptors:
        manifest.register(desc)

    _log.info(
        "create_default_manifest: created manifest with %d algorithms.",
        manifest.algorithm_count(),
    )
    return manifest


def register_algorithm(registry: AlgorithmRegistry, desc: AlgorithmDescriptor) -> None:
    """Convenience wrapper: register *desc* in *registry*.

    Validates the algorithm ID before delegating to
    :meth:`AlgorithmRegistry.register`.  Emits a warning if the ID fails
    validation but still delegates to allow the registry to handle the edge
    case according to its own policy.

    Args:
        registry: Target :class:`AlgorithmRegistry`.
        desc: :class:`AlgorithmDescriptor` to register.
    """
    if not _validate_algorithm_id(desc.algorithm_id):
        _log.warning("register_algorithm: invalid algorithm_id '%s'.", desc.algorithm_id)
    registry.register(desc)


def lookup_algorithm(registry: AlgorithmRegistry, algorithm_id: str) -> AlgorithmDescriptor | None:
    """Convenience wrapper: look up *algorithm_id* in *registry*.

    Args:
        registry: Source :class:`AlgorithmRegistry`.
        algorithm_id: Identifier to look up.

    Returns:
        :class:`AlgorithmDescriptor` or ``None``.
    """
    return registry.lookup(algorithm_id)


# ---------------------------------------------------------------------------
# 9. Numeric utilities (bonus helpers used by manifest reports)
# ---------------------------------------------------------------------------


def _compute_complexity_score(descriptor: AlgorithmDescriptor) -> float:
    """Return a rough numeric score representing algorithm efficiency.

    The score is heuristic and intended only for sorting / reporting
    purposes.  Polynomial algorithms receive higher scores (closer to 1.0)
    than non-polynomial ones.

    Args:
        descriptor: The descriptor to score.

    Returns:
        Float in the range [0.0, 1.0].
    """
    if descriptor.is_polynomial():
        # Reward polynomial algorithms; penalise quadratic and above
        c = descriptor.complexity
        if "^2" in c or "n^2" in c:
            return 0.6
        if "n*k" in c or "n*T" in c or "n*W" in c:
            return 0.75
        return 0.9
    return 0.3


def _entropy_of_registry(registry: dict[str, AlgorithmDescriptor]) -> float:
    """Compute the Shannon entropy of the complexity distribution in *registry*.

    Groups algorithms by complexity string and treats the relative
    frequencies as a probability distribution.  Higher entropy indicates
    a more diverse portfolio of algorithms.

    Args:
        registry: Algorithm registry mapping IDs to descriptors.

    Returns:
        Non-negative float (entropy in nats).
    """
    if not registry:
        return 0.0
    counts: dict[str, int] = {}
    for desc in registry.values():
        counts[desc.complexity] = counts.get(desc.complexity, 0) + 1
    total = sum(counts.values())
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log(p)
    return entropy


def _descriptors_sorted_by_param_count(
    registry: dict[str, AlgorithmDescriptor],
) -> list[AlgorithmDescriptor]:
    """Return descriptors sorted ascending by their parameter count.

    Useful when surfacing the simplest (fewest parameters) algorithms first
    in a report.

    Args:
        registry: Algorithm registry mapping IDs to descriptors.

    Returns:
        Sorted list of :class:`AlgorithmDescriptor`.
    """
    return sorted(registry.values(), key=lambda d: d.parameter_count())


# ---------------------------------------------------------------------------
# 10. Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    "AlgorithmDescriptor",
    "AlgorithmRegistry",
    "ManifestRegistry",
    "ManifestValidator",
    "OptimizationManifest",
    "create_default_manifest",
    "lookup_algorithm",
    "register_algorithm",
]
