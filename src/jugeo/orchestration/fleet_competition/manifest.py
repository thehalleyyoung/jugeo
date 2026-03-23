"""
Package manifest and descriptor module for fleet_competition.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 46:
Fleet semantics — competitive search over admissible futures.

The *manifest* layer sits above the raw data models (:mod:`models`) and provides
the configuration, schema registry, and descriptor objects that bind together a
concrete instantiation of the fleet_competition protocol.  Every deployed
fleet_competition instance is described by a :class:`FleetCompetitionManifest`
that records:

* **version and chapter reference** — traceability back to theory2.tex Ch46.
* **competition configuration** (:class:`CompetitionConfig`) — numeric knobs
  that control bid acceptance, budget management, calibration windows, and
  convergence detection.
* **bid schema registry** (:class:`BidSchemaRegistry`) — a pluggable catalogue
  of allowed bid formats so that new bid types can be registered without
  changing the evaluator.
* **member roster** (:class:`FleetCompetitionDescriptor`) — the set of fleet
  member identifiers admitted into this competition instance.

Design rationale (theory2.tex §46.1):
  The manifest layer decouples *policy* (what constitutes a valid bid, how many
  rounds to run) from *mechanism* (the auction logic, Pareto filtering).
  This allows the same evaluator engine to be parameterised differently for
  different deployment contexts — e.g. a high-throughput production fleet vs.
  a low-latency interactive assistant fleet.

Chapter reference: theory2.tex Ch46 — Fleet semantics.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = [
    # Constants
    "PACKAGE_VERSION",
    "CHAPTER_REF",
    "PACKAGE_NAME",
    "THEORY_SECTION",
    # Dataclasses
    "CompetitionConfig",
    "BidSchemaEntry",
    # Classes
    "BidSchemaRegistry",
    "FleetCompetitionDescriptor",
    "FleetCompetitionManifest",
    # Functions
    "build_manifest",
    "validate_manifest",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Semantic version of the fleet_competition package.
PACKAGE_VERSION: str = "0.1.0"

#: Theory chapter reference tag used in all manifest objects.
CHAPTER_REF: str = "Ch46"

#: Short package identifier.
PACKAGE_NAME: str = "fleet_competition"

#: Full theory section path for traceability.
THEORY_SECTION: str = "theory2.tex Ch46: Fleet semantics"

#: Default maximum number of fleet members per competition.
DEFAULT_MAX_MEMBERS: int = 64

#: Default tag list for new competitions.
DEFAULT_TAGS: list[str] = ["fleet", "competition", "ch46"]

#: Required fields for the built-in "standard" bid schema.
STANDARD_BID_REQUIRED_FIELDS: tuple[str, ...] = (
    "move_id",
    "bidder_id",
    "bid_value",
    "semantic_score",
    "uncertainty",
    "trust_ceiling",
)

#: Optional fields allowed in the built-in "standard" bid schema.
STANDARD_BID_OPTIONAL_FIELDS: tuple[str, ...] = (
    "capabilities",
    "metadata",
    "bid_id",
    "timestamp",
    "status",
)

# ---------------------------------------------------------------------------
# CompetitionConfig — frozen configuration value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompetitionConfig:
    """Immutable configuration for a fleet competition instance.

    This frozen dataclass captures all numeric policy parameters that govern
    a fleet_competition run.  It is embedded in :class:`FleetCompetitionDescriptor`
    and serialised in :class:`FleetCompetitionManifest`.

    Parameters and their theory2.tex §46.x references:

    max_rounds (§46.4):
        Maximum number of competitive rounds before the competition terminates.
        Setting to 1 produces a single-shot auction.
    budget_per_round (§46.4):
        Notional budget allocated to each round.  The winning bid's cost is
        deducted from this budget; rounds where no bid fits within budget are
        declared empty.
    min_bids_per_round (§46.2):
        Minimum number of bids required for a round to produce a winner.
        Rounds with fewer bids are marked as *void*.
    max_bids_per_round (§46.2):
        Hard cap on bids accepted per round.  Excess bids are rejected as
        ``EXPIRED`` before evaluation begins.
    challenge_timeout_seconds (§46.5):
        Maximum age (in seconds) a :class:`~models.ChallengeRecord` may
        remain open before it is auto-expired.
    calibration_window (§46.6):
        Number of trailing samples used when computing the rolling calibration
        metrics for each fleet member.
    pareto_epsilon (§46.3):
        Tolerance used in Pareto dominance comparisons.  Two bids with
        objective differences below this threshold are treated as tied.
    enable_cross_calibration (§46.6):
        When True, fleet members' calibration traces are updated using
        *peer observations* as well as self-reports.
    trust_floor (§46.6):
        Minimum trust score any fleet member may hold, regardless of
        calibration history.  Prevents permanent exclusion.
    convergence_threshold (§46.4):
        Minimum change in the top bid's semantic score between rounds
        required to continue the competition.  Competitions that converge
        below this threshold terminate early.

    Attributes
    ----------
    max_rounds:
        See above; default 10.
    budget_per_round:
        See above; default 100.0.
    min_bids_per_round:
        See above; default 1.
    max_bids_per_round:
        See above; default 20.
    challenge_timeout_seconds:
        See above; default 30.0.
    calibration_window:
        See above; default 50.
    pareto_epsilon:
        See above; default 1e-6.
    enable_cross_calibration:
        See above; default True.
    trust_floor:
        See above; default 0.1.
    convergence_threshold:
        See above; default 0.01.
    """

    max_rounds: int = 10
    budget_per_round: float = 100.0
    min_bids_per_round: int = 1
    max_bids_per_round: int = 20
    challenge_timeout_seconds: float = 30.0
    calibration_window: int = 50
    pareto_epsilon: float = 1e-6
    enable_cross_calibration: bool = True
    trust_floor: float = 0.1
    convergence_threshold: float = 0.01

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this configuration to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of every field.
        """
        return {
            "max_rounds": self.max_rounds,
            "budget_per_round": self.budget_per_round,
            "min_bids_per_round": self.min_bids_per_round,
            "max_bids_per_round": self.max_bids_per_round,
            "challenge_timeout_seconds": self.challenge_timeout_seconds,
            "calibration_window": self.calibration_window,
            "pareto_epsilon": self.pareto_epsilon,
            "enable_cross_calibration": self.enable_cross_calibration,
            "trust_floor": self.trust_floor,
            "convergence_threshold": self.convergence_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CompetitionConfig":
        """Deserialise a :class:`CompetitionConfig` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.  Unknown keys are
            ignored; missing keys fall back to the field defaults.

        Returns
        -------
        CompetitionConfig
            Newly constructed configuration object.
        """
        return cls(
            max_rounds=int(d.get("max_rounds", 10)),
            budget_per_round=float(d.get("budget_per_round", 100.0)),
            min_bids_per_round=int(d.get("min_bids_per_round", 1)),
            max_bids_per_round=int(d.get("max_bids_per_round", 20)),
            challenge_timeout_seconds=float(d.get("challenge_timeout_seconds", 30.0)),
            calibration_window=int(d.get("calibration_window", 50)),
            pareto_epsilon=float(d.get("pareto_epsilon", 1e-6)),
            enable_cross_calibration=bool(d.get("enable_cross_calibration", True)),
            trust_floor=float(d.get("trust_floor", 0.1)),
            convergence_threshold=float(d.get("convergence_threshold", 0.01)),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this configuration for constraint violations.

        Returns
        -------
        list[str]
            List of human-readable error strings; empty if valid.
        """
        errors: list[str] = []
        if self.max_rounds < 1:
            errors.append(f"max_rounds must be >= 1; got {self.max_rounds}")
        if self.budget_per_round <= 0:
            errors.append(f"budget_per_round must be > 0; got {self.budget_per_round}")
        if self.min_bids_per_round < 1:
            errors.append(
                f"min_bids_per_round must be >= 1; got {self.min_bids_per_round}"
            )
        if self.max_bids_per_round < self.min_bids_per_round:
            errors.append(
                f"max_bids_per_round ({self.max_bids_per_round}) must be >= "
                f"min_bids_per_round ({self.min_bids_per_round})"
            )
        if self.challenge_timeout_seconds <= 0:
            errors.append(
                f"challenge_timeout_seconds must be > 0; got {self.challenge_timeout_seconds}"
            )
        if self.calibration_window < 1:
            errors.append(
                f"calibration_window must be >= 1; got {self.calibration_window}"
            )
        if self.pareto_epsilon < 0:
            errors.append(
                f"pareto_epsilon must be >= 0; got {self.pareto_epsilon}"
            )
        if not (0.0 <= self.trust_floor <= 1.0):
            errors.append(
                f"trust_floor must be in [0, 1]; got {self.trust_floor}"
            )
        if not (0.0 < self.convergence_threshold <= 1.0):
            errors.append(
                f"convergence_threshold must be in (0, 1]; got {self.convergence_threshold}"
            )
        return errors


# ---------------------------------------------------------------------------
# BidSchemaEntry — frozen schema descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BidSchemaEntry:
    """Descriptor for a single bid type in the schema registry.

    A ``BidSchemaEntry`` names the required and optional fields of a particular
    bid type and provides an optional runtime validator callable.

    Because this is a *frozen* dataclass, the ``validator`` field is declared
    with ``field(default=None)`` to avoid issues with mutable defaults in
    frozen dataclasses.

    Attributes
    ----------
    bid_type:
        Short string identifier for this bid type (e.g. ``"standard"``).
    required_fields:
        Tuple of field names that *must* be present in a bid dictionary of
        this type.
    optional_fields:
        Tuple of field names that *may* be present.
    validator:
        Optional callable ``(dict) -> bool`` for deeper validation beyond
        field presence checks.  ``None`` means no extra validation is run.
    """

    bid_type: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = field(default=())
    validator: Optional[Callable[[dict[str, Any]], bool]] = field(default=None)

    def check_required(self, bid_dict: dict[str, Any]) -> list[str]:
        """Return a list of missing required fields in *bid_dict*.

        Parameters
        ----------
        bid_dict:
            The bid dictionary to check.

        Returns
        -------
        list[str]
            Names of required fields absent from *bid_dict*.
        """
        return [f for f in self.required_fields if f not in bid_dict]

    def run_validator(self, bid_dict: dict[str, Any]) -> bool:
        """Run the custom validator if present; return True if absent.

        Parameters
        ----------
        bid_dict:
            The bid dictionary to validate.

        Returns
        -------
        bool
            ``True`` if the bid passes validation (or no validator is set).
        """
        if self.validator is None:
            return True
        try:
            return bool(self.validator(bid_dict))
        except Exception:
            return False


# ---------------------------------------------------------------------------
# BidSchemaRegistry — mutable registry of bid schemas
# ---------------------------------------------------------------------------


class BidSchemaRegistry:
    """A pluggable registry of bid type schemas.

    Fleet operators can register custom bid formats alongside the built-in
    ``"standard"`` type.  The evaluator consults the registry to validate
    incoming bids before adding them to a round.

    The default registry (created by :meth:`default`) includes the
    ``"standard"`` bid type which maps to the fields of
    :class:`~models.CompetitiveBid`.

    Attributes
    ----------
    _schemas:
        Internal dictionary mapping ``bid_type`` → :class:`BidSchemaEntry`.
    """

    def __init__(self) -> None:
        """Initialise an empty schema registry."""
        self._schemas: dict[str, BidSchemaEntry] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, entry: BidSchemaEntry) -> None:
        """Register a bid schema entry.

        If an entry with the same ``bid_type`` already exists it is
        overwritten (allowing schema upgrades).

        Parameters
        ----------
        entry:
            The :class:`BidSchemaEntry` to register.
        """
        self._schemas[entry.bid_type] = entry

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, bid_type: str) -> Optional[BidSchemaEntry]:
        """Return the schema entry for *bid_type*, or ``None`` if not found.

        Parameters
        ----------
        bid_type:
            The bid type identifier to look up.

        Returns
        -------
        BidSchemaEntry or None
        """
        return self._schemas.get(bid_type)

    def validate_bid(self, bid_type: str, bid_dict: dict[str, Any]) -> bool:
        """Validate *bid_dict* against the schema for *bid_type*.

        Validation passes iff:

        1. *bid_type* is registered.
        2. All required fields are present in *bid_dict*.
        3. The custom validator (if any) returns True.

        Parameters
        ----------
        bid_type:
            Type identifier to look up.
        bid_dict:
            The bid payload to validate.

        Returns
        -------
        bool
            ``True`` if *bid_dict* is a valid bid of *bid_type*.
        """
        entry = self.lookup(bid_type)
        if entry is None:
            return False
        missing = entry.check_required(bid_dict)
        if missing:
            return False
        return entry.run_validator(bid_dict)

    def list_types(self) -> list[str]:
        """Return sorted list of registered bid type names.

        Returns
        -------
        list[str]
        """
        return sorted(self._schemas.keys())

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "BidSchemaRegistry":
        """Create a registry pre-loaded with the built-in ``"standard"`` schema.

        The standard schema corresponds to the fields of
        :class:`~models.CompetitiveBid` and uses a lightweight validator that
        checks numeric ranges.

        Returns
        -------
        BidSchemaRegistry
            A registry with the standard bid type registered.
        """

        def _standard_validator(d: dict[str, Any]) -> bool:
            """Validate numeric range constraints for the standard bid type."""
            try:
                if float(d.get("bid_value", -1)) < 0:
                    return False
                ss = float(d.get("semantic_score", -1))
                if not (0.0 <= ss <= 1.0):
                    return False
                unc = float(d.get("uncertainty", -1))
                if not (0.0 <= unc <= 1.0):
                    return False
                tc = float(d.get("trust_ceiling", -1))
                if not (0.0 <= tc <= 1.0):
                    return False
            except (TypeError, ValueError):
                return False
            return True

        registry = cls()
        registry.register(
            BidSchemaEntry(
                bid_type="standard",
                required_fields=STANDARD_BID_REQUIRED_FIELDS,
                optional_fields=STANDARD_BID_OPTIONAL_FIELDS,
                validator=_standard_validator,
            )
        )
        return registry

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        types = self.list_types()
        return f"BidSchemaRegistry(types={types!r})"


# ---------------------------------------------------------------------------
# FleetCompetitionDescriptor — competition instance descriptor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetCompetitionDescriptor:
    """Descriptor for a single fleet competition instance.

    A ``FleetCompetitionDescriptor`` binds together a unique identifier, a
    :class:`CompetitionConfig`, and the set of admitted fleet member IDs.  It
    is the persistent record that survives across rounds and is embedded in the
    :class:`FleetCompetitionManifest`.

    Attributes
    ----------
    competition_id:
        UUID assigned at construction.
    config:
        Immutable competition configuration.
    member_roster:
        Mutable list of admitted fleet member identifiers.
    created_at:
        Wall-clock creation time.
    tags:
        Free-form searchable tags.
    """

    competition_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: CompetitionConfig = field(default_factory=CompetitionConfig)
    member_roster: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Roster management
    # ------------------------------------------------------------------

    def add_member(self, member_id: str) -> None:
        """Add a fleet member to the roster.

        Parameters
        ----------
        member_id:
            The identifier to add.  Duplicate additions are silently ignored
            (idempotent).

        Raises
        ------
        ValueError
            If *member_id* is empty or not a string.
        ValueError
            If the roster is already at :data:`DEFAULT_MAX_MEMBERS` capacity.
        """
        if not isinstance(member_id, str) or not member_id.strip():
            raise ValueError("member_id must be a non-empty string")
        if member_id in self.member_roster:
            return
        if len(self.member_roster) >= DEFAULT_MAX_MEMBERS:
            raise ValueError(
                f"Roster is full ({DEFAULT_MAX_MEMBERS} members); "
                "remove a member before adding another"
            )
        self.member_roster.append(member_id)

    def remove_member(self, member_id: str) -> None:
        """Remove a fleet member from the roster.

        Parameters
        ----------
        member_id:
            The identifier to remove.

        Raises
        ------
        ValueError
            If *member_id* is not in the current roster.
        """
        if member_id not in self.member_roster:
            raise ValueError(
                f"Member {member_id!r} is not in the roster for competition "
                f"{self.competition_id!r}"
            )
        self.member_roster.remove(member_id)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this descriptor to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "competition_id": self.competition_id,
            "config": self.config.to_dict(),
            "member_roster": list(self.member_roster),
            "created_at": self.created_at,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FleetCompetitionDescriptor":
        """Deserialise a :class:`FleetCompetitionDescriptor` from a plain dict.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        FleetCompetitionDescriptor
            Newly constructed descriptor.
        """
        config_data = d.get("config", {})
        config = (
            CompetitionConfig.from_dict(config_data)
            if isinstance(config_data, dict)
            else CompetitionConfig()
        )
        desc = cls(
            competition_id=d.get("competition_id", str(uuid.uuid4())),
            config=config,
            member_roster=list(d.get("member_roster", [])),
            created_at=float(d.get("created_at", time.time())),
            tags=list(d.get("tags", [])),
        )
        return desc

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly string."""
        return (
            f"FleetCompetitionDescriptor("
            f"id={self.competition_id!r}, "
            f"members={len(self.member_roster)}, "
            f"tags={self.tags!r})"
        )


# ---------------------------------------------------------------------------
# FleetCompetitionManifest
# ---------------------------------------------------------------------------


class FleetCompetitionManifest:
    """Top-level manifest describing a deployed fleet competition instance.

    A :class:`FleetCompetitionManifest` is the authoritative record of a
    fleet_competition deployment.  It ties together the package version, the
    theory chapter reference, the list of exported public symbols, and the
    competition descriptor.

    It is created by :func:`build_manifest` and validated by
    :func:`validate_manifest`.

    Attributes
    ----------
    version:
        Package version string; defaults to :data:`PACKAGE_VERSION`.
    chapter_ref:
        Theory chapter tag; defaults to :data:`CHAPTER_REF`.
    exported_symbols:
        List of public symbol names declared in ``__all__`` of the
        fleet_competition package.  Populated in :meth:`__init__`.
    descriptor:
        The :class:`FleetCompetitionDescriptor` for the competition.
    schema_registry:
        The :class:`BidSchemaRegistry` associated with this manifest.
    created_at:
        Wall-clock time when this manifest was constructed.
    """

    #: All public symbol names expected to be exported by this package.
    _EXPECTED_SYMBOLS: list[str] = [
        # models
        "BidStatus",
        "RoundPhase",
        "CalibrationStatus",
        "BidDelta",
        "CompetitiveBid",
        "FleetRound",
        "ChallengeRecord",
        "CalibrationTrace",
        # manifest
        "CompetitionConfig",
        "BidSchemaEntry",
        "BidSchemaRegistry",
        "FleetCompetitionDescriptor",
        "FleetCompetitionManifest",
        "build_manifest",
        "validate_manifest",
        # bid evaluation
        "BidEvaluation",
        "BidEvaluationCriterion",
        "MultiCriterionEvaluator",
        "ParetoFilter",
        "BidRanker",
        "BidAuction",
        "EvaluationHistory",
    ]

    def __init__(
        self,
        descriptor: Optional[FleetCompetitionDescriptor] = None,
    ) -> None:
        """Construct a new manifest.

        Parameters
        ----------
        descriptor:
            The competition descriptor to embed.  If ``None``, a new default
            :class:`FleetCompetitionDescriptor` is created.
        """
        self.version: str = PACKAGE_VERSION
        self.chapter_ref: str = CHAPTER_REF
        self.theory_section: str = THEORY_SECTION
        self.descriptor: FleetCompetitionDescriptor = (
            descriptor if descriptor is not None else FleetCompetitionDescriptor()
        )
        self.schema_registry: BidSchemaRegistry = BidSchemaRegistry.default()
        self.created_at: float = time.time()
        self.exported_symbols: list[str] = list(self._EXPECTED_SYMBOLS)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate this manifest and its embedded descriptor.

        Checks performed:

        1. Version string is non-empty.
        2. Chapter ref matches :data:`CHAPTER_REF`.
        3. Descriptor's :class:`CompetitionConfig` is internally valid.
        4. Exported symbols list is non-empty.

        Returns
        -------
        list[str]
            List of error strings; empty if valid.
        """
        errors: list[str] = []
        if not self.version:
            errors.append("version must be a non-empty string")
        if self.chapter_ref != CHAPTER_REF:
            errors.append(
                f"chapter_ref mismatch: expected {CHAPTER_REF!r}, got {self.chapter_ref!r}"
            )
        config_errors = self.descriptor.config.validate()
        errors.extend(
            f"config: {e}" for e in config_errors
        )
        if not self.exported_symbols:
            errors.append("exported_symbols must not be empty")
        if not self.schema_registry.list_types():
            errors.append("schema_registry must have at least one bid type registered")
        return errors

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "theory_section": self.theory_section,
            "created_at": self.created_at,
            "exported_symbols": list(self.exported_symbols),
            "descriptor": self.descriptor.to_dict(),
            "registered_bid_types": self.schema_registry.list_types(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialise this manifest to a JSON string.

        Parameters
        ----------
        indent:
            JSON indentation level.

        Returns
        -------
        str
            Formatted JSON string.
        """
        return json.dumps(self.to_dict(), indent=indent)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a concise human-readable summary of this manifest.

        Returns
        -------
        str
            Multi-line summary string.
        """
        lines = [
            f"FleetCompetitionManifest",
            f"  package      : {PACKAGE_NAME} v{self.version}",
            f"  chapter ref  : {self.chapter_ref} ({self.theory_section})",
            f"  competition  : {self.descriptor.competition_id}",
            f"  members      : {len(self.descriptor.member_roster)}",
            f"  max_rounds   : {self.descriptor.config.max_rounds}",
            f"  bid_types    : {self.schema_registry.list_types()}",
            f"  symbols      : {len(self.exported_symbols)} exported",
            f"  created_at   : {self.created_at:.3f}",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"FleetCompetitionManifest("
            f"version={self.version!r}, "
            f"competition_id={self.descriptor.competition_id!r})"
        )


# ---------------------------------------------------------------------------
# Module-level factory functions
# ---------------------------------------------------------------------------


def build_manifest(
    descriptor: Optional[FleetCompetitionDescriptor] = None,
) -> FleetCompetitionManifest:
    """Construct a :class:`FleetCompetitionManifest` for the given descriptor.

    This is the primary factory function for creating manifests.  If no
    descriptor is provided a new default descriptor is instantiated with a
    default :class:`CompetitionConfig`.

    Parameters
    ----------
    descriptor:
        Optional pre-built descriptor.  If ``None``, a fresh
        :class:`FleetCompetitionDescriptor` is created.

    Returns
    -------
    FleetCompetitionManifest
        A newly constructed manifest ready for validation and deployment.

    Examples
    --------
    ::

        manifest = build_manifest()
        errors = manifest.validate()
        assert not errors, errors
    """
    return FleetCompetitionManifest(descriptor=descriptor)


def validate_manifest(manifest: FleetCompetitionManifest) -> bool:
    """Return True if *manifest* passes all validation checks.

    This is a convenience wrapper around :meth:`FleetCompetitionManifest.validate`
    that returns a simple boolean suitable for use in assertions and guards.

    Parameters
    ----------
    manifest:
        The manifest to validate.

    Returns
    -------
    bool
        ``True`` iff :meth:`FleetCompetitionManifest.validate` returns an
        empty list.

    Examples
    --------
    ::

        manifest = build_manifest()
        assert validate_manifest(manifest)
    """
    if not isinstance(manifest, FleetCompetitionManifest):
        return False
    return len(manifest.validate()) == 0


# ---------------------------------------------------------------------------
# Module self-check (runs when this module is executed directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _manifest = build_manifest()
    print(_manifest.summary())
    _errors = _manifest.validate()
    if _errors:
        print("Validation errors:")
        for _e in _errors:
            print(f"  - {_e}")
    else:
        print("Manifest is valid.")
